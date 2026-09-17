# Provenance: the run ledger (EP-35)

Every analysis from P3 on is reproducible from a **run id** (GOVERNANCE §12, D-24). This
page is the prose twin of `src/mimicwarehouse/run.py` (DESIGN §11, §15): what a run
records, where the files live, how they are read, what may never enter them, and how
long they are kept. Nothing on this page is derived from data. All MIMIC-IV analyses in
this repository are retrospective.

## 1. What a run is

A run is one execution of one analysis-shaped unit of work — a cohort build, a QC pass,
a phenotype evaluation, a protocol run, a report, a benchmark — wrapped in

```python
from mimicwarehouse import run

with run.start("tracer", tier="dev", kind="analysis", params={"k": 11}) as r:
    result = r.safe_query("SELECT ... count(*) AS n ...", name="patients_by_era")
    r.record_attrition([{"step": "base", "label": "all stays", "n_units": 120, "n_subjects": 100}])
    r.save_table("by_era", result.df)
```

`run.start` assigns the **run id** `YYYYMMDDTHHMMSSZ-<6 hex>` (UTC second + a random
suffix; the `T` glues the date so the id is legal in ledger content but, like every id,
never appears in a committed file name — `docs/committed-text.md` rule 3), creates
`runs/<run_id>/`, captures provenance and writes a `status: running` manifest **before**
the block runs, so a crash still leaves a record. On exit — normal or by exception — the
manifest is finalised and rewritten atomically and one line is appended to
`runs/ledger.jsonl`. An exception marks the run `failed`, records the exception type and
a sanitised one-line message (never a traceback), and is re-raised.

`kind` is one of `analysis | cohort | build | qc | phenotype | protocol | report | bench`;
`tier` one of `fixture | demo | dev | full`; `status` `running | ok | failed`.

## 2. What a run records

`runs/<run_id>/manifest.json` (`run.RunManifest`, flat, `extra = "forbid"`):

| Field | Meaning |
|---|---|
| `run_id`, `name`, `kind`, `tier`, `status`, `started`, `finished` | identity and lifecycle (UTC ISO timestamps) |
| `command` | the command line to repeat (`mwh tracer --tier dev`; defaults to the process argv) |
| `git_sha`, `git_dirty` | full sha of `HEAD` and whether tracked files differed from it |
| `uv_lock_sha256` | sha256 of `uv.lock` — the environment hash |
| `duckdb_version`, `python_version`, `package_version` | the engine, the interpreter, `mimicwarehouse.__version__` |
| `params` | the caller's JSON-able parameters |
| `snapshot_ids` | `{layer: id}` — the logical layer snapshot ids read (DESIGN §11 glossary); `r.read_layer("core")` resolves one from the lake manifests, `r.safe_query` records the queried catalog's `core` id, `r.record_snapshot` cites one explicitly |
| `refs` | `[{kind, name, version, hash}]` — code sets, phenotypes, cohorts, protocols the run depended on (filled by EP-40/41/46/51) |
| `sql` | `{name: "sql/<name>.sql"}` — every statement the run issued, as files; the text never sits in the manifest |
| `tables`, `figures` | `{name: "tables/<name>.parquet"}`, `{name: "figures/<name>.<ext>"}` — what the run produced |
| `attrition` | `[{step, label, n_units, n_subjects}]` — one row per criterion |
| `audit_ids` | the `runs/audit.jsonl` ids of every `safe_query` call made on the run's behalf |
| `warnings` | `r.warn(...)` lines plus every `warnings.warn` raised inside the block |
| `wall_s`, `peak_rss_mb`, `disk_delta_mb` | mirrors of the `resources` block (EP-36): wall time; the run-scoped peak RSS; free-space delta of the data-root drive in MB |
| `seeds` | `{stage: seed}` from `r.seed(stage)` / `r.spawn_rngs(stage, n)` (EP-36) — `{}` for a run without a stochastic stage, `null` only in manifests written before EP-36; the seeds derive from `protocol_id` (frozen) or the `run_id`: [determinism.md](determinism.md) §2 |
| `resources` | the `ResourceLog` measurement (EP-36): wall, CPU time, run-scoped peak RSS with its method, RSS start/end, the Windows `peak_wset`, disk delta, GPU memory (`null` without `pynvml` and a device), sample counts — [determinism.md](determinism.md) §6; `null` while `status: running` |
| `protocol_id`, `protocol_hash`, `claim_type` | `None` until a protocol run (EP-51) or the caller states them |
| `error` | `{type, message}` on failure |
| `doctor` | the environment block: `mwh doctor`'s sixteen checks (fifteen before EP-52's `last_backup`) reduced to `{id, status, value}` (the machine-readable payloads — versions, paths, product names — never the prose detail), captured once per process because the probes cost seconds |

`runs/ledger.jsonl` receives the subset `run_id, name, kind, tier, status, started,
wall_s, git_sha, protocol_hash` — one canonical JSON line per run through
`fsio.append_jsonl` (`O_APPEND`, checked write, fsync; DESIGN §11). The manifest is the
record; the ledger is the index.

## 3. Where the files live

```
<data_root>\runs\
  ledger.jsonl                 one line per run (EP-35)
  audit.jsonl                  one line per safe_query call (EP-30)
  benchmarks.jsonl             build / verify / query telemetry (EP-19; run.bench since EP-35)
  <run_id>\
    manifest.json              the record (section 2)
    sql\<name>.sql             every statement issued
    tables\<name>.parquet      aggregate outputs (never rows: identifier columns are refused)
    figures\<name>.<ext>       Vega-Lite / JSON specs, PNG, SVG
  tracer\<stamp>-<tier>\       the EP-31 tracer's report artefacts (its run record is under <run_id>\)
  jobs\<name>.json, .log       detached ⏱ jobs (EP-19)
<data_root>\warehouse\runs.duckdb   read-only views over all of the above (section 5)
```

Everything under `runs/` stays inside the data root. Promotion of any file into
`docs/`, `reports/` or git goes through `mwh disclose check` (EP-43; GOVERNANCE §7).

## 4. Manifests never contain rows

The rule the module enforces and later briefs inherit:

- A manifest holds **hashes, counts, parameters, paths and version strings**. Its schema
  is closed (`extra = "forbid"`), so a stray field cannot smuggle a value in.
- SQL text lives in `sql/*.sql`, not in the manifest; `runs/audit.jsonl` already holds
  the same statements.
- `r.save_table` refuses any frame whose columns include a contract identifier
  (`subject_id`, `hadm_id`, `stay_id`, `note_id`, …) — aggregate first.
- Text that originates **outside** the project — exception messages, captured
  `warnings.warn` calls — passes through `safe.sanitize_error_text` (first line, quoted
  literals and numbers masked, capped) before it is recorded: DuckDB quotes the offending
  cell value in conversion errors, and that value must never land in a run folder.
  `r.warn` messages are project-authored and only trimmed to one line; never pass a value.
- Attrition counts are **raw** inside the data root (they are what the analysis needs) and
  go through `disclose` on any export; a manifest shown in a Claude session is counts and
  metadata only, which GOVERNANCE §4 allows.
- The 64-character bound on run-folder strings (`docs/committed-text.md` rule 4) applies to
  the analysis artefacts (`attrition.json`, `descriptives.json`, `model.json`, `report.md`,
  the tracer folder); the manifest's `doctor.checks[].value` and `command` may legitimately
  carry longer environment strings (a path, a product name) — environment facts, never data.

## 5. Reading runs

- `uv run --group dev mwh runs list [--tier t] [--kind k] [--last N] [--json]` — the ledger
  lines, newest first, read straight from `runs/ledger.jsonl` (works before the first
  refresh; a torn trailing line is tolerated).
- `uv run --group dev mwh runs show <run_id> [--json]` — the manifest, pretty-printed.
- `uv run --group dev mwh runs refresh` — rebuilds `warehouse/runs.duckdb` with the views
  `audit` (EP-30), `ledger`, `benchmarks`, `manifests` (`read_json` over
  `runs/*/manifest.json` with explicit column types; the dict-valued fields bind as
  `JSON`) and `attrition` (the manifests' attrition rows unnested). Built to `.new` and
  published with `publish.swap_file`; nothing else ever writes the file (DESIGN §6). A
  view over a file that does not exist yet is typed and empty.
- Through `mwh sql`, the views are the `runs` schema:
  `uv run --group dev mwh sql "SELECT kind, count(*) AS n FROM runs.ledger GROUP BY 1" --tier dev`.
  `runs` is deliberately **not** a registry schema (EP-33 checkpoint): a GROUP BY over a
  ledger view needs a real count-family column like any subject-level read, and the k = 11
  suppression applies to the counts. Usage errors are separated from governance refusals
  with `WHERE refusal_reason LIKE 'usage: %'` (EP-33 B1d); `refusal_reason` and `error`
  are label columns exempt from the 64-character free-text heuristic.
- `safe_query` re-attaches `runs.duckdb` (`DETACH` + `ATTACH`) on every call, so a refresh
  published while a process still holds the catalog instance is visible to the next call.

## 6. Reproduction blocks

`run.reproduction_block(run_id)` renders the **Reproduction** + **Provenance** block that
every `docs/analyses/*` case study carries (EP-32 convention): run id, kind, tier, status,
the command line, git sha and dirty flag, package / DuckDB / Python versions, the
`uv.lock` hash, snapshot ids, the protocol id + hash (or "none"), the claim type and the
counts of recorded statements and audited calls, the seeds and the resource summary
(EP-36; every integer through `fmt_int`). The capstones (EP-53+) call it instead of
hand-writing the block; the run id is quoted inline only.

## 7. Retention and backup

Run folders and the four ledgers are **never auto-deleted**: they are the
non-reproducible state of the warehouse (the lake and catalogs rebuild from raw + code;
the record of what was run does not). `mwh backup` (EP-52, section 9) copies the ledgers,
the frozen protocol copies, every run's `manifest.json` + `sql/` and the study / registry
metadata to the owner's encrypted local target; the ledgers are append-only and the
manifests are rewritten only by the run that owns them. Deleting a run folder is an
owner action, never a session's.

## 8. What the later briefs add

EP-36 (shipped 2026-09-05) filled `seeds` and `resources` — the seed-derivation rule and
the resource sampler are documented in [determinism.md](determinism.md), the policy later
briefs cite; EP-43 suppresses attrition counts on export and writes the
`.disclosure.json` sidecars; EP-47 records cohort attrition through `record_attrition`;
EP-51 fills `protocol_id` / `protocol_hash` for frozen protocols; EP-52 (shipped
2026-09-17, section 9) backs the non-reproducible state up; EP-134 is the Runs &
Provenance browser over the same views.

## 9. Backup & restore (EP-52)

`mwh backup` copies the non-reproducible state — and only that — to an encrypted local
target the owner chooses (GOVERNANCE §11). This section is the prose twin of
`src/mimicwarehouse/backup.py`; the rebuildable layers (lake, catalogs, derived data,
marts) are never backed up — `mwh init` (EP-158) + `mwh build` is their recovery path.

**The set** (`backup.BACKUP_SET`, globs relative to the data root, enumerated in this
order; a file is counted once):

| Glob | What | Why it is in the set |
|---|---|---|
| `runs/*.jsonl` | `ledger.jsonl`, `audit.jsonl`, `benchmarks.jsonl`, `protocols.jsonl` | the append-only ledgers (sections 2 and 7; GOVERNANCE §8) |
| `runs/protocols/**` | the frozen protocol copies (EP-51) | byte-for-byte and **read-only**; `shutil.copy2` carries the attribute into the backup and back out of it (`docs/gotchas.md` §2) |
| `runs/*/manifest.json` · `runs/*/sql/**` | every run's record and the statements it issued | the record is the manifest, the index is the ledger (section 2) |
| `models/registry/**` (`.json` / `.yaml` only) | model-registry metadata (EP-106) | weights are re-trainable from frozen protocols |
| `studies/**` minus the guard's data-shaped suffixes | study specs, notes, JSONL review records | never a `.parquet` / `.duckdb` / `.csv` / archive — specs and notes only |
| `runs/*/tables/**` · `runs/*/figures/**` | run artifacts — **only with `--include-run-artifacts`** | off by default: they may hold derived row-level tables inside the data root |

Never copied: `runs/jobs/` (transient ⏱ job state and logs), `warehouse/runs.duckdb`
(section 5 rebuilds it from the ledgers), the tracer's own report folder (its run
record is under `runs/<run_id>/`), the lake, catalogs and marts.

**Commands** (the target defaults to `MWH_BACKUP_TARGET`; `--target <dir>` overrides):

- `uv run --group dev mwh backup run [--target <dir>] [--include-run-artifacts] [--json]`
  — hashes every file of the set, copies it to `<target>\mwh-backup-<UTC>\…` mirroring
  the data-root paths, re-hashes every copy (a copy that does not hash to its source is a
  hard error — the EP-171 canary rule for a silent quarantine) and writes
  `backup_manifest.json`: `backup_id`, tool + version, UTC timestamp, the data root, the
  git sha, the rules in force, files per rule, `n_files`, `total_bytes` and one
  `{path, sha256, bytes}` entry per file. The directory is staged as
  `mwh-backup-<UTC>.new` and published with `publish.swap_dir`, so an interrupted backup
  never looks complete and its leftover is swept by the next run. The plain output is
  the path, the file count and the byte total (never a file name); `--json` prints the
  manifest summary without the file list.
- `uv run --group dev mwh backup verify <backup-dir> [--json]` — re-hashes every listed
  file and walks the directory: a mismatched, missing or unexpected file is named and the
  command exits 1.
- `uv run --group dev mwh backup restore --from <backup-dir> --to <dir> [--dry-run] [--json]`
  — verifies first (a damaged backup is never restored), refuses a `--to` whose `runs/`
  is non-empty (a restore never overwrites a live record) or that the D-29 detector
  flags, copies every file back with the same re-hash, and prints the drill's next step:
  `uv run --group dev mwh --data-root <dir> runs refresh` rebuilds `runs.duckdb` over the
  restored ledgers (section 5). A torn trailing ledger line (a backup taken mid-append)
  survives the round trip: `fsio.read_jsonl` and the views tolerate it.
- `uv run --group dev mwh backup list [--target <dir>] [--json]` — the backups under the
  target, newest first, with age in days, file count and size; a directory without a
  readable manifest is shown as such, `.new` staging leftovers are ignored.
- `mwh doctor` gains the `last_backup` row: **pass** when the newest backup under
  `MWH_BACKUP_TARGET` is at most 7 days old, **warn** when older or when the target holds
  none, **info** when no target is configured.

**Target safety** (`backup.target_problem`; refused with exit 3, and there is no
override flag — the owner changes the target instead): the EP-3 detector
(`config.location_problem` — a sync-client volume label, not a fixed disk, not
NTFS/ReFS, under OneDrive, a forbidden drive letter such as G:/D:), a path inside the
data root, a path inside the repository, and a volume whose BitLocker protection reads
**off** through the doctor's probe (an unknown state is a warning, printed on stderr,
and the backup proceeds). The same detector judges a restore destination.

**What a session may see.** Everything `mwh backup` prints or writes is paths, counts,
hashes and timestamps; the copied bytes are the ledgers (audit statement text, run
parameters, counts), frozen protocol YAML and metadata — project state, never a row.
Backups live outside git and outside the data root; nothing from a backup is promoted
anywhere without the disclosure gate (GOVERNANCE §7).
