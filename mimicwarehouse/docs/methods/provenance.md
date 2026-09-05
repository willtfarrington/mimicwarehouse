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
| `wall_s`, `peak_rss_mb`, `disk_delta_mb` | wall time; the coarse process-RSS high-water (start/end; EP-36 adds the sampler); free-space delta of the data-root drive in MB |
| `seeds`, `resources` | `None` until EP-36 |
| `protocol_id`, `protocol_hash`, `claim_type` | `None` until a protocol run (EP-51) or the caller states them |
| `error` | `{type, message}` on failure |
| `doctor` | the environment block: `mwh doctor`'s fifteen checks reduced to `{id, status, value}` (the machine-readable payloads — versions, paths, product names — never the prose detail), captured once per process because the probes cost seconds |

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
counts of recorded statements and audited calls. The capstones (EP-53+) call it instead
of hand-writing the block; the run id is quoted inline only.

## 7. Retention and backup

Run folders and the three ledgers are **never auto-deleted**: they are the
non-reproducible state of the warehouse (the lake and catalogs rebuild from raw + code;
the record of what was run does not). `mwh backup` (EP-52) copies `runs/` — ledgers,
manifests, run folders — to the owner's encrypted local target; the ledgers are
append-only and the manifests are rewritten only by the run that owns them. Deleting a
run folder is an owner action, never a session's.

## 8. What the later briefs add

EP-36 fills `seeds` and `resources` (seed derivation, the RSS sampler thread) and writes
`docs/methods/determinism.md`; EP-43 suppresses attrition counts on export and writes the
`.disclosure.json` sidecars; EP-47 records cohort attrition through `record_attrition`;
EP-51 fills `protocol_id` / `protocol_hash` for frozen protocols; EP-52 backs `runs/` up;
EP-134 is the Runs & Provenance browser over the same views.
