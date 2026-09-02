# P2 retrospective — staging, catalog, safe-query, tracer (EP-33)

Written at EP-33, the consolidation re-plan of P0–P2 (D-44; the brief's amendment block is
the charter). P2 ran 2026-08-29 → 2026-08-30 as nineteen briefs — EP-170/EP-171 at the head,
then EP-17 … EP-32 — and staged the complete core lake, the per-tier catalogs, `meta.*`, the
safe-query gate and the first end-to-end analysis (tracer bullet). This file is the phase
record the convention EP-54 inherits: baselines, planned-vs-actual, what dragged, the
full-tier wall-time table, the lake/temp measurements vs DESIGN §3, the `dev-first` verdict,
and — appended as the EP-33 session proceeds — the § Renames ledger, § Worklist outcomes,
§ Checkpoint minutes and the commit series. Integers are thousands-separated
(`inventory.fmt_int` style, guard G4).

> **Session note (2026-08-31).** The EP-33 second attempt was stopped by the owner at
> weekly-usage-limit proximity after Workstream E completed (no safeguard refusal fired —
> the first addendum's tunings held throughout); its finished outputs were salvaged into
> the repository on the owner's follow-up instruction. Done: A (+B9), the baselines below,
> D2, E1/E2 (ledger `retro-p2-findings.md`), and the B1–B6/B8 scout plans
> (`retro-p2-scout-plans.md`). The re-run resumes at the owner triage checkpoint — see the
> brief's § Second attempt for the full map. § Checkpoint minutes stays pending.

## Pre-flight baselines (recorded before any EP-33 edit; the F6 comparison base)

Session start 2026-08-30 (UTC timestamps below roll past midnight); `mwh doctor`
9 pass · 1 warn · 0 fail · 5 info (the warn is `antivirus`, by design — D-38/D-42);
AC power mode **Best performance**; disk 393.3 / 951.5 GB free; DuckDB 1.5.5 == pin;
working tree clean at `876e781`.

| Probe | Result |
|---|---|
| `uv run poe check` (ruff + pyright + pytest, fixture tier) | exit 0 · 720 passed · **219 s** |
| `mwh verify EP-k`, k ∈ 0…32 ∪ 164…171 (41 briefs, fresh interpreter each) | **0 failures** · 414 s |
| `uv run poe roadmap-check --strict` | exit 0 (0 errors, 0 warnings) |
| `mwh build --tier fixture` (first build into the data-root fixture lake / resume over it) | exit 0 · 16 s first · **3.3 s resume** (the F6 comparable) |
| `mwh --help` (import-budget probe, one cold run) | 558 ms |
| `mwh jobs` | 11 jobs, all `done`, exit 0 |
| A7 guard sweep — `guard.scan_tracked` per commit, `45f1c84` → `876e781` | **37 commits · 0 violations** · 13.2 s |
| D2 concept smoke — all vendored `concepts_duckdb` files, driver order, DuckDB 1.5.5, throwaway demo-catalog copy | **65/65 executed cleanly** · 1.78 s |

## Planned vs actual (P2 + EP-170/EP-171)

Every P2 brief finished at or under its planned size; the five ⏱ staging jobs all finished
*inside* their launching sessions (the verified-by-EP-28 convention was record-keeping, not a
wall-time necessity). Sizes: S ≈ 30 min, M ≈ 1 h, L ≈ 2 h (D-2).

| EP | Planned | Actual (completion-note basis) |
|---|---|---|
| EP-170 | M | one session (reconciliation-only; several items were verify-only — overtaken by EP-16/EP-171 allocations) |
| EP-171 | S | ≈ 45 min (canary total 13.3 s; the session cost was harness + note) |
| EP-17 | M | one session; dev probe answered SCH-1 in-line (three raw scans, ≤ 1.6 s each) |
| EP-18 | M | one session |
| EP-19 | M | one session; full-tier smoke job 0.8 s stage wall |
| EP-20 | M | ≈ 45 min; job 17.0 s for 19 tables |
| EP-21 | M | ≈ 55 min; dev catalog 1.0 s, full catalog job 0.9 s |
| EP-22 | M | one session; demo fetch + full demo build 6.4 s |
| EP-23 | M | job 138.9 s (in-session) |
| EP-24 | M | job 698 s (in-session; emar_detail is the wall-time head) |
| EP-25 | M | job 198.8 s (in-session) |
| EP-26 | L | job 137.9 s (in-session — the L sizing anticipated chartevents pain that never came) |
| EP-27 | M | job 61 s (in-session) |
| EP-28 | S | well under budget (all five jobs already done, reconciliation 31/31) |
| EP-29 | M | one session; full `meta.profile` 886,043,036 rows in 14.1 s |
| EP-30 | M | one session |
| EP-31 | M | one session; dev run 2.11 s, full run 3.3 s |
| EP-32 | S | under budget |

## What dragged / environment lessons

- **emar_detail is the staging wall-time head, not chartevents**: pass 1 438.4 s at
  13.8 MB/s (wide all-VARCHAR rows are CSV-parse-bound) vs chartevents' whole-step 137.8 s
  at 304.4 MB/s (narrow rows stream at ≈ 940 MB/s in pass 1). Peak RSS high-water is also
  emar_detail's (24,563 MB against the 36 GB build `memory_limit`).
- **Pass 2 ≥ pass 1 fired broadly** (the parked parallel-sort trigger): chartevents
  93.0 vs 44.6 s, labevents 76.4 vs 62.1 s, emar 35.4 vs 29.1 s, poe 40.3 vs 29.3 s, and
  all three EP-27 icu event tables; emar_detail/prescriptions/pharmacy/microbiologyevents
  are the exceptions (parse-bound or ≈ equal). Decision at this re-plan: keep parked (D4c).
- **DuckDB 1.5.5 semantics cost real time to learn** and are now canon (→ the C4 gotchas
  home): `COPY … APPEND` demands `{uuid}` filename patterns (so pass-1 sweeps use
  `OVERWRITE_OR_IGNORE` over disjoint bucket ranges); pass-2 reads need
  `hive_partitioning=false` or the synthesised partition column is written into the sorted
  file; an integer literal as large as `10**9` binds DOUBLE (spell it out); the in-process
  instance cache is path-keyed, so `ATTACH` of `runs.duckdb` must be `IF NOT EXISTS`;
  `sum()` returns HUGEINT and a cast around it trips the safe-query closed-set walk (the
  sanctioned pattern is `count(*) FILTER`); `.print`/`.read` driver files are CLI-dialect,
  executed file-by-file from Python.
- **Windows process reality**: a child-side state rewrite cannot record a hard crash — the
  ⏱ standard became a *detached supervisor* (`dag.jobs`, DETACHED_PROCESS) that owns the
  log and the authoritative state file. The pass-1 heartbeat's in-process rss/ctypes probes
  read 0 on this host for minutes; the runner's psutil sampler is the trustworthy number.
- **Endpoint security never interfered** with staging: no stall, nothing quarantined,
  across all five ⏱ jobs and the EP-171 canary (write baselines 191 / 239 MB/s).
- **The EP-166 doc consolidation ate three DESIGN headings** (`## 5`, `## 6`, `## 12`,
  `## 21` lead-in), restored piecemeal by EP-18/EP-19 — the C1 as-built rewrite carries the
  lesson: heading-preserving edits, verified by a structure diff.
- **One real contract falsification in all of P2**: the demo `procedureevents` 2.2 CSV
  header ships uppercase `ORIGINALAMOUNT`/`ORIGINALRATE` (EP-22) — fixed as a lossless
  rename in the column map; zero rejects on every staged table (`loader_reject_max = 0`
  held); EP-24's `parent_field_ordinal` DOUBLE expectation was rejected in favour of the
  contract's VARCHAR (ordinals like `1.1`/`1.10` would collide).

## Full-tier wall-time table (`mwh runs benchmarks --format md`, ledger `kind: verify` lines)

| table | rows | CSV GB | Parquet GB | ratio | files | pass 1 s | pass 2 s | total s | MB/s | peak RSS MB |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| mimiciv_hosp.admissions | 546,028 | 0.09 | 0.02 | 5.2x | 100 | - | - | 1.1 | 85.9 | 77 |
| mimiciv_hosp.d_hcpcs | 89,208 | 0.00 | 0.00 | 10.8x | 1 | - | - | 0.1 | 58.4 | 131 |
| mimiciv_hosp.d_icd_diagnoses | 112,107 | 0.01 | 0.00 | 9.9x | 1 | - | - | 0.1 | 124.3 | 144 |
| mimiciv_hosp.d_icd_procedures | 86,423 | 0.01 | 0.00 | 11.0x | 1 | - | - | 0.1 | 118.4 | 145 |
| mimiciv_hosp.d_labitems | 1,650 | 0.00 | 0.00 | 4.6x | 1 | - | - | 0.0 | 4.6 | 114 |
| mimiciv_hosp.diagnoses_icd | 6,364,488 | 0.18 | 0.02 | 8.3x | 100 | - | - | 1.5 | 118.4 | 88 |
| mimiciv_hosp.drgcodes | 761,856 | 0.05 | 0.01 | 9.2x | 100 | - | - | 0.9 | 63.5 | 92 |
| mimiciv_hosp.emar | 42,808,593 | 6.25 | 0.64 | 9.7x | 100 | 29.1 | 35.4 | 64.7 | 96.6 | 6,853 |
| mimiciv_hosp.emar_detail | 87,371,064 | 8.68 | 0.67 | 13.0x | 100 | 438.4 | 191.6 | 630.1 | 13.8 | 24,563 |
| mimiciv_hosp.hcpcsevents | 186,074 | 0.01 | 0.00 | 6.8x | 100 | - | - | 0.7 | 18.3 | 145 |
| mimiciv_hosp.labevents | 158,374,764 | 18.40 | 1.79 | 10.3x | 100 | 62.1 | 76.4 | 138.7 | 132.7 | 9,827 |
| mimiciv_hosp.microbiologyevents | 3,988,224 | 0.91 | 0.08 | 11.0x | 100 | 8.0 | 7.0 | 15.0 | 60.5 | 1,582 |
| mimiciv_hosp.omr | 7,753,027 | 0.32 | 0.03 | 11.8x | 100 | - | - | 2.1 | 150.4 | 145 |
| mimiciv_hosp.patients | 364,627 | 0.01 | 0.00 | 4.8x | 100 | - | - | 0.8 | 15.8 | 74 |
| mimiciv_hosp.pharmacy | 17,847,567 | 3.98 | 0.40 | 9.9x | 100 | 25.9 | 25.6 | 51.6 | 77.0 | 7,347 |
| mimiciv_hosp.poe | 52,212,109 | 5.10 | 0.37 | 13.8x | 100 | 29.3 | 40.3 | 69.7 | 73.2 | 9,796 |
| mimiciv_hosp.poe_detail | 8,504,982 | 0.42 | 0.04 | 9.8x | 100 | - | - | 3.1 | 135.5 | 1,218 |
| mimiciv_hosp.prescriptions | 20,292,611 | 3.49 | 0.45 | 7.7x | 100 | 33.9 | 28.1 | 62.1 | 56.2 | 7,236 |
| mimiciv_hosp.procedures_icd | 859,655 | 0.03 | 0.01 | 5.4x | 100 | - | - | 0.8 | 40.5 | 90 |
| mimiciv_hosp.provider | 42,244 | 0.00 | 0.00 | 2.1x | 1 | - | - | 0.0 | 11.4 | 146 |
| mimiciv_hosp.services | 593,071 | 0.03 | 0.01 | 3.6x | 100 | - | - | 0.7 | 35.7 | 84 |
| mimiciv_hosp.transfers | 2,413,581 | 0.21 | 0.04 | 4.7x | 100 | - | - | 1.5 | 138.1 | 81 |
| mimiciv_icu.caregiver | 17,984 | 0.00 | 0.00 | 2.2x | 1 | - | - | 0.0 | 4.8 | 679 |
| mimiciv_icu.chartevents | 432,997,491 | 41.94 | 1.83 | 22.9x | 100 | 44.6 | 93.0 | 137.8 | 304.4 | 4,729 |
| mimiciv_icu.d_items | 4,095 | 0.00 | 0.00 | 4.7x | 1 | - | - | 0.0 | 15.0 | 679 |
| mimiciv_icu.datetimeevents | 9,979,761 | 1.09 | 0.05 | 22.4x | 100 | 3.1 | 4.9 | 8.1 | 134.7 | 626 |
| mimiciv_icu.icustays | 94,458 | 0.01 | 0.00 | 4.9x | 100 | - | - | 0.6 | 22.7 | 169 |
| mimiciv_icu.ingredientevents | 14,253,480 | 2.47 | 0.21 | 11.5x | 100 | 10.3 | 11.8 | 22.2 | 111.3 | 2,821 |
| mimiciv_icu.inputevents | 10,953,713 | 2.87 | 0.29 | 10.0x | 100 | 13.9 | 14.9 | 28.9 | 99.1 | 3,539 |
| mimiciv_icu.outputevents | 5,359,395 | 0.46 | 0.05 | 9.4x | 100 | - | - | 2.0 | 233.5 | 678 |
| mimiciv_icu.procedureevents | 808,706 | 0.15 | 0.02 | 6.1x | 100 | - | - | 1.3 | 114.5 | 169 |
| total | 886,043,036 | 97.19 | 7.05 | 13.8x | 2,407 | 698.9 | 529.1 | 1246.5 | 78.0 | 24,563 |

The committed narrative around these numbers is
[`../mimicwarehouse/docs/analyses/00-staging-benchmark.md`](../mimicwarehouse/docs/analyses/00-staging-benchmark.md)
(EP-32; regenerate with `mwh runs benchmarks --out …`).

## Lake & temp vs the DESIGN §3 budget

Measured at EP-28, carried here as the phase record: `lake\core` = 7,046,156,578 bytes
≈ **7.0 GB** against the planning estimate of 18–25 GB (13.8× overall compression; 2.1× on
the tiny dims up to 22.9× on chartevents). **No build-temp spill was observed** in any ⏱ job
(`tmp_duckdb` = 0 at every 60 s heartbeat); the 60–100 GB build-temp planning line never
materialised for staging. Staging peak RSS high-water 24,563 MB. The lake holds 2,407
`part-0.parquet` files + 24 `_progress.json` = 2,431 files in 2,433 directories; one
`os.scandir` sweep 0.091 s. Free space after P2: 392.8 GB (baseline session start:
393.3 GB). The derived/spine/marts estimates stay open — Workstream D3 re-estimates them
from the measured 13.8× and the D2 shapes.

**D3 (2026-09-01).** All 65 vendored concepts re-run on a throwaway demo-catalog copy
(2.2 s, 0 failures; 65 derived tables, 131,517 rows, 1,824,855 bytes as ZSTD Parquet at
140 ICU stays) and scaled by the stay ratio (≈ 675×): derived concepts ≈ 90 M rows /
0.8–1.5 GB; MEDS spine 2.5–4 GB; marts ≤ 1–2 GB → **derived + spine ≈ 4–6 GB, marts
≈ 1–2 GB** against the 15–30 / 5–15 GB planning lines; concept build-temp spill bounded at
≤ 20–40 GB worst case. Recorded in DESIGN §3 (dated note) and roadmap Risk 6; EP-37/EP-50
measure, EP-54 records. Free space at this session's pre-flight: 390.7 GB.

## dev-first ordering verdict

**Keep.** Pass 2 sorts `settings.dev_buckets` first and flips `dev_ready` the moment they
are sorted, so the dev tier became queryable 31–66 s into each large table's build
(labevents 66 s, emar 31 s, chartevents 50 s after step start) while full passes continued —
at zero measured cost. `dev_ready(step)` is the readiness signal the EP-168 test fixtures
consume; recorded as a D-18 addendum at this EP.

## Renames (Workstream B ledger — invariant 6)

Every public-surface rename/move/merge of 2026-09-01, with where the old name was cited
and what now cites the new one (historical briefs, completion notes and retro ledgers are
never edited; P3 briefs carry the correction in their `EP-33 amendment` block; P4+ briefs
a one-line `EP-33 rename` block — D5).

| Old | New | Propagated to |
|---|---|---|
| `mimicwarehouse.paths` (module) | `mimicwarehouse.publish` (paths.py deleted) | loader/stage.py, loader/buckets.py, loader/paths.py docstring, catalog/build.py, DESIGN §5/§6/§15, tests ep17/18/21/23–28, workspace README |
| `paths.swap_dir` · `paths.new_dir_for` · `paths.old_dir_for` · `paths.SwapError` | `publish.swap_dir` · `publish.new_path_for` · `publish.old_path_for` · `publish.SwapError` | same files |
| `catalog.build.swap_catalog(new, dest, tier)` · `catalog_new_path` · `catalog_old_path` · duplicated `NEW_SUFFIX/OLD_SUFFIX/RETRIES/RETRY_BASE_SLEEP_S` | `publish.swap_file(new, dest, *, blocked_hint, observer=None)` · `publish.new_path_for` / `old_path_for` · single definitions in `publish`/`fsio` | catalog/build.py (`_publish_catalog` translates `SwapBlockedError` → `CatalogSwapError`, which now also subclasses `publish.SwapBlockedError`), safe.build_runs_db, runs_cli.py, test_ep21, DESIGN §6, EP-34/35 amendments |
| `catalog.cli.EXIT_REFUSED` (definition) · `verify.EXIT_OK` / `EXIT_USAGE` (definitions) · tracer's literal `3` | `console.EXIT_OK` / `EXIT_FINDINGS` / `EXIT_USAGE` / `EXIT_REFUSED` (re-exported from `safe`, `catalog.cli`, `verify`) | catalog/cli.py, safe.py, tracer.py, verify.py, guard.py, dag/cli.py, runs_cli.py; tests unchanged (re-exports) |
| private `_fail` helpers (catalog/cli, dag/cli, fixtures/cli, canary, inventory, schema/cli, verify, guard) · `dag.cli._configure_build_logging` · `guard._emit_json` · `inventory._Log` | `console.fail` (stderr) · `console.configure_progress_logging` · `console.emit_json` · stdlib logging (`mimicwarehouse.inventory`) | every command module; test_ep167 identity asserts |
| `inventory.open_connection` (canonical opener) · raw `duckdb.connect` sites (safe._describe_ast, safe.build_runs_db, catalog/build ×2, catalog/connect, fixtures/catalog, loader/engine via inventory) | `engine.open_duckdb(profile, …)` (inventory keeps `open_connection` as a thin alias); inline `ATTACH` → `engine.attach_read_only` | those modules; grep guard in test_ep33 |
| `inventory._atomic_write_text` · the O_APPEND twins `safe._append_audit` / `benchmarks.append` · buffered `manifest.append_manifest` · bare `json.loads` ledger readers | `fsio.atomic_write_text` (alias kept) · `fsio.append_jsonl` / `append_jsonl_lines` · `fsio.iter_jsonl` / `read_jsonl` (+ `manifest.iter_manifest`) | safe.py, dag/benchmarks.py, dag/snapshot.py, dag/jobs.py, loader/manifest.py, loader/buckets.py, tracer.py, canary.py |
| `catalog.dictionary._fmt_mb` | `inventory.fmt_bytes_mb` (widened to `int \| None`) | catalog/dictionary.py |
| `safe.exempt_ref` (closure) · `_Analysis.reads_subject_keyed` | `safe.is_registry_ref` + `REGISTRY_SCHEMAS` / `REGISTRY_TABLES` · (dropped; heuristic scoped to `not exempt`) | safe.py, DESIGN §12, EP-35/41/46/47 amendments |
| `safe_query(sql, *, tier="dev", k=11, …)` · `SafeQueryError` = "bad arguments, unaudited" | `safe_query(sql, *, tier=None, k=None, …)` (settings defaults) · `SafeQueryError` = the audited usage class (`usage: ` reason) | safe.py, catalog/cli.py (`safe_cli_errors`), tracer.py, test_ep30 |
| `catalog.connect._connect_with_retry(path, config, tier)` · `OPEN_RETRY_SLEEP_S` | `_connect_with_retry(path, settings, tier)` over `engine.open_duckdb(retry_missing_s=…)` · removed | catalog/connect.py (private) |
| `jobs.pid_alive(pid)` | `jobs.pid_alive(pid, create_time=None)` + `jobs.process_create_time`; `JobInfo.create_time`; lock payload `create_time` | dag/jobs.py, dag/runner.py, test_ep19 stub |
| `loader/__init__` eager re-exports | lazy `__getattr__` / `__dir__` (`__all__` unchanged) | loader/__init__.py |
| new names (no old): `buckets.StageCoverageError`, `buckets.source_fingerprint`, `Progress.resumable_for`, `Progress.source_sha256/source_fingerprint/sort_by`, `manifest.iter_manifest`, `safe.sanitize_error_text`, `safe.ERROR_TEXT_MAX_CHARS`, `catalog.cli.safe_cli_errors`, `helpers.assert_import_budget` / `HEAVY_MODULES`, `guard._command_words` / `_resolve_hook_word`, `cli.CliState.data_root` (deleted) | — | — |
| `mwh runs bench` (brief shorthand) | `mwh runs benchmarks` | EP-35/38/56 amendments |
| `safe.owner_rows` (brief citation of a non-existent API) | EP-49's own owner gate (`open_catalog(role="owner")` + an `AuditLine` via `fsio.append_jsonl`); EP-58 defines `owner_rows()` | EP-49/67/149 amendments |

## Worklist outcomes (Workstream B — one line per item)

- **B1** — done: cast-around-aggregate verifies (`_unwrap_cast`; unaliased cast counts
  refused); set operations implemented with per-leaf checks and a symmetric positional
  count rule (`UNION BY NAME` refused; DIS-2 closed); `REGISTRY_SCHEMAS`/`REGISTRY_TABLES`/
  `is_registry_ref` (runs excluded), free-text heuristic scoped to non-registry reads,
  settings defaults for tier/k; three-way taxonomy with `usage()` audit lines and
  `catalog.cli.safe_cli_errors` shared by `mwh sql` and `mwh tracer`; plus DKB-1/SGT-1
  (real count node mandatory), DKB-2 (`sanitize_error_text`), DKB-3/DKB-4/SGT-6 (pre-
  execution catalog statements audited; interrupt timer guarded). 43 tests.
- **B2** — done: `mimicwarehouse.publish` (swap_dir/swap_file over one retry core,
  observer seam, FileNotFoundError tolerated only on remove/restore, deferred `.old`),
  every caller migrated, `paths.py` deleted, `catalog.build._publish_catalog` translation;
  loader fixes LDR-1 (coverage guard), LDR-4 (progress identity), WIN-1/LDR-3 (append
  before record), LDR-8, WIN-2/WIN-4 (retry on pass-2 ops and stale-`.new` sweeps). 16
  tests + the foundation's 18.
- **B3** — done: `mimicwarehouse.engine.open_duckdb(profile)` + `attach_read_only`; all
  eight `src/` connect sites migrated (two were profile-less); grep guard; DESIGN §6.1.
- **B4** — done: `docs/committed-text.md` (six rules with enforcers + tests), `fmt_bytes_mb`
  fold, guard docstring pointer, SGD-3 (notebook source + six text types scanned), SGD-4
  (selfcheck resolves hook paths), CTR-2/CTR-4 carried items, RES-1/3/5 docs. 21 tests.
- **B5** — done: `fmt-check` in `poe check` (tree was already formatted), repo-root
  `poe_tasks.toml`, tests/README text, test_ep12 pin relaxed.
- **B6** — done: `helpers.assert_import_budget` + `HEAVY_MODULES` (numpy added — a
  tightening), test_ep02 canonical, test_ep09 lazy clause, three duplicates deleted; red
  run verified the offender message; doctrine recorded in DESIGN §15.
- **B7** — owner-applied diff package (D-45 item 4): authored last, see § Commit series.
- **B8** — done: `fsio` (JSONL canon), console `fail`/`emit_json`/`configure_progress_
  logging` adopted across every command module (errors → stderr), `inventory._Log`
  deleted, `loader/__init__` lazy, `DIAGNOSTIC_COMMANDS` doctrine stated once, CLI-2/4/5/7,
  DAG-1/2/3/4/8, LGR-1/2/4, WIN-6 fixed; canary kept verbatim (sanctioned exception).
  17 CLI tests.
- **Test layout note:** EP-33's acceptance spans `tests/ep/test_ep33.py` (foundation) +
  `test_ep33_loader.py`, `test_ep33_safe.py`, `test_ep33_hygiene.py`, `test_ep33_cli.py`
  — one file per workstream area written by parallel agents, all marker `ep_33`; kept
  split rather than merged (recorded in tests/README.md as this brief's one exception).

- **B9** — done (landed with the A4 DECISIONS edit pass): D-43's *Why/Alternatives* tail
  restored under item 14 with its lost first line recovered verbatim from `f3eb115`; the
  orphaned fragment below the addenda removed; dated correction note in place.
- **A4 repair (ledger DRF-1)** — the A4 edit pass itself consumed four decision headers
  (D-18/D-19/D-21/D-25) as edit anchors without re-emitting them; caught by the E-audit,
  restored verbatim in the salvage commit. C1 inherits the lesson: heading-preserving
  edits, verified by a structure diff, before committing DECISIONS/DESIGN changes.

## Checkpoint minutes

> **Checkpoint minutes (2026-09-01).** Third session on this brief; it resumed at the
> owner triage checkpoint with the salvaged ledger and scout plans in hand (pre-flight:
> doctor 9 pass / 1 warn, AC power mode Best performance, 390.7 GB free, tree clean at
> `84d9d8d`; multi-agent orchestration was **not** enabled, so Workstream B ran as four
> parallel file-ownership subagents instead of a workflow). The owner answered two rounds
> of four questions; every recommended option was taken.
>
> 1. **Triage of the 45 pending verified findings — accepted as proposed.** Outcomes are
>    recorded per row in the ledger's Triage column
>    ([`retro-p2-findings.md`](retro-p2-findings.md)): 36 fix-now (bug, robustness,
>    docs/tests, guard code, or via a D1 brief amendment), 2 to the B7 owner-applied diff
>    package (SGD-1, SGD-2), 4 reject-with-reason (LGR-3, P3C-1, TST-3, SGT-2 — the
>    last with an EP-43 amendment), plus CTR-1's decision-now/fixture-later split and the
>    already-fixed DRF-1. The 60 carried-low findings: the XS doc/dead-code/convention
>    items are swept where B/C already touch the file (CLI-4/5/6/7, CTR-2/4, DRF-7/8/9,
>    LDR-5/6/8, LGR-7, P01-3/4, RES-4/5, TST-4, WIN-6/7, DKB-5, DAG-6/8, SGT-4 — the last
>    closed by DKB-1's fix); the remainder are parked in `final-roadmap.md` for EP-54.
> 2. **LDR-1 — stage-level refusal.** `stage_partitioned` refuses when the existing
>    table's recorded coverage is a strict superset of the request; `mwh build --tier full
>    --force` is the only path that rewrites a complete table; the runner's `--force`
>    bypasses the skip, never this guard.
> 3. **SGT-2 — extreme-value aggregates stay admitted.** After DKB-1's fix every row that
>    carries a min/max/mode/median/quantile is gated by a real count column, so such
>    values are released only inside k-suppressed rows (dates are patient-shifted);
>    recorded as a D-31 addendum; EP-43 decides any per-column tightening in the disclose
>    module (amendment).
> 4. **B7 — owner-applied diff package, authored last** (first addendum's tunings 1 and 5):
>    settings.json + PreToolUse hook + selfcheck/test diffs for SGD-1, SGD-2, the
>    `tests/fixtures/**` Read allowance and the path-aware repo-internal `.csv`/`.duckdb`
>    check are written to the session scratchpad for the owner to apply interactively.
> 5. **Renames — approved:** `mimicwarehouse.paths` → `mimicwarehouse.publish` (paths.py
>    deleted; `swap_dir`/`swap_file`/`new_path_for`/`old_path_for`; `catalog.build.
>    swap_catalog` absorbed); `mimicwarehouse.console` is the single home of
>    `EXIT_OK/EXIT_FINDINGS/EXIT_USAGE/EXIT_REFUSED` (safe, catalog.cli, verify re-export).
> 6. **B1c — `runs` does not join `REGISTRY_SCHEMAS`;** EP-35 allow-lists its ledger label
>    columns when it builds the ledger views (D1 amendment).
> 7. **Earlier-EP test edits — approved:** test_ep12's exact `check`-chain pin relaxed
>    (B5); the duplicate import-budget tests in test_ep06/11/12 deleted, test_ep02
>    canonical + test_ep09's lazy-contract clause (B6); fixture counts read from
>    `manifest.json` in test_ep21/22/30 (TST-2).
> 8. **B8 — errors to stderr via `console.fail`; the AV canary keeps its verbatim raw-os
>    write sequence** as the canon's sanctioned exception (EP-171 baselines stay
>    comparable).
>
> Routine calls stated at the checkpoint and not objected to: the D4 defaults a–g all
> kept (b now measurement-backed by D2); the gotchas home is `mimicwarehouse/docs/
> gotchas.md` with a short DESIGN §6 "Engine gotchas" subsection pointing at it and the
> hygiene canon at `docs/committed-text.md`; the engine canon module is
> `mimicwarehouse.engine` (`loader/engine.py` keeps its name; prose uses dotted paths);
> `fmt_int` stays in `inventory.py`; the DIAGNOSTIC_COMMANDS allow-list is kept and
> recorded; no agent round over the completeness critique's ten gaps (the catalog package
> and the tracer get a manual look during B3 and F3).

## Workstream F — battery results (2026-09-01/02, after B–D landed)

| Probe | Result | vs pre-flight baseline |
|---|---|---|
| F1 `uv run poe check` (ruff check + **ruff format --check** + pyright + pytest) | exit 0 · **832 passed**, 29 deselected · 248 s (poe wall 257 s) | 720 passed / 219 s — +112 tests (the EP-33 modules), +29 s |
| F1 `mwh verify EP-k`, k ∈ 0…33 ∪ 164…171 (42 briefs) | **0 failures** after one fix · 486 s | 0 failures / 414 s (41 briefs); the single red was test_ep165's 9,000-byte cap on CLAUDE.md after a pointer bullet — CLAUDE.md trimmed to 8,715 bytes (C3 dedupe), re-verified green |
| F1 `poe roadmap-check --strict` | 0 errors / 0 warnings (172 rows, 41 done before the EP-33 tick) | 0/0 |
| F2 catalogs rebuilt (`--select catalog`) — fixture, dev foreground; full as job `catalog-full-ep33` | fixture + dev ≈ 1 s each; full job exit 0 in 4 s; `mwh catalog info` = 31 cataloged (7 tables + 24 views), 0 missing on all three tiers; snapshot `core/full` unchanged (`b1fc5313…410eca`) | lake untouched (invariant 1) |
| F2 `mwh catalog dictionary --tier full` vs committed `DATA-DICTIONARY.md` | identical except the two provenance lines (build id, built-at timestamp) — file regenerated with the post-EP-33 catalog's provenance | 31 tables / 342 columns unchanged |
| F3 `mwh tracer --tier dev` | run `…-dev`: cohort n = 3,208, fit, AUC 0.744, 7 audited calls, 2.17 s | EP-31: n = 3,208, AUC 0.744, 2.11 s — identical |
| F3 `mwh tracer --tier full` (job `tracer-full-ep33`) | n = 65,366, fit, AUC 0.731, 7 audited calls, 3.59 s | EP-31: n = 65,366, AUC 0.731, 3.3 s — identical |
| F4 `mwh canary write` | OK — 5 passes, 203 files, 2,534,564,809 bytes, 13.7 s; small 179 MB/s, large 229 MB/s, swap 1,231 MB/s; every re-read matched, tree removed | EP-171: 191 / 239 MB/s, 13.3 s (the full tracer job ran concurrently) |
| F5 `mwh guard --all-tracked` · `--selfcheck` | clean (500 files) · passed, `pretool-hook` registered (the selfcheck now resolves the interpreter/script paths — SGD-4) | clean / passed |
| F5 per-commit sweep `guard.scan_tracked`, `45f1c84` → `3e2f872` (the P2 era + the EP-33 series) | **43 commits · 0 violations** · 13.6 s | A7: 37 commits · 0 · 13.2 s |
| F6 fixture build (resume over the data-root fixture lake) | 3.4 s | 3.3 s |
| F6 `mwh --help` (two cold runs) | 582 / 554 ms | 558 ms |
| F6 fixture-suite wall | 248 s for 832 tests (0.30 s/test) | 219 s for 720 (0.30 s/test) |

`mwh doctor` at session start: 9 pass · 1 warn (`antivirus`, by design) · 0 fail · 5 info,
390.7 GB free, AC power mode Best performance.

## Commit series

- `876e781` — `docs(roadmap): record EP-33 aborted first attempt + re-run tuning (EP-33)`
- `d88e2b5` — `docs(roadmap): EP-33 workstream A - EP-32 hash, P2 retro, DECISIONS addenda, parked-trigger records (EP-33)`
- `2aa9e65` — `docs(roadmap): EP-33 second-attempt salvage - audit ledger, scout plans, D2 amendments, brief map, DRF-1 repair (EP-33)` *(per-commit guard sweep re-run over the extended series: 39 commits `45f1c84` → `2aa9e65`, 0 violations)*

- `8848534` — `feat(mimicwarehouse): P0-P2 consolidation - publish/engine/fsio/console canons, safe-query and loader hardening (EP-33)` *(Workstream B + the B4/B5/B6 docs and tests; 2026-09-02 UTC)*
- `00a7e50` — `docs(mimicwarehouse): DESIGN as-built consolidation, DECISIONS D-45 + status index, README/CLAUDE refresh (EP-33)` *(Workstream C)*
- `3e2f872` — `docs(roadmap): EP-33 checkpoint minutes, triage outcomes, P3 amendments, D3 re-estimates, retro and risks (EP-33)` *(Workstreams A-close, D; the F record)*
- the `docs(roadmap): record EP-33 commit hashes` commit that follows — ticks the row with the seven-hash series and carries the per-commit guard sweep over `45f1c84` → HEAD (F5, below).
- **B7 owner-applied package** (D-45 item 4): reviewed diff files written to the session scratchpad after the series (`b7-settings.diff`, `b7-hook.diff`, `b7-tests.diff` + `b7-README.md`), not committed by the session.
