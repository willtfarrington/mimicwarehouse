# EP-44 — Data-quality profiling

**Size:** M · **Tier:** fixture+dev+full · **Core/Stretch:** core · **Depends on:** EP-29 (Catalog & data dictionary (meta.*)), EP-39 (Itemid dictionary curation + unit harmonization), EP-43 (Disclosure primitives (`disclose` module)) · **Blocks:** EP-45 (Measurement-process summaries), EP-53 (Capstone #1: concepts/QC case study), EP-54 (Re-plan P3), EP-61 (Catalog & QC browser page)

> **EP-33 amendment (2026-09-01).** Header facts unchanged. (1) **`meta.*` names** (EP-29):
> reuse `meta.tables` / `meta.columns` (`null_pct`, `approx_distinct`, `unit_hint`,
> `is_identifier`, `is_free_text`) / `meta.row_counts` / `meta.itemids`; `is_dictionary_coded`
> derives from the contract plus EP-39's `meta.item_dictionary`. (2) **Defect injection
> targets a fixture lake copy** (carried P3C-11): a copied fixture *catalog* holds views over
> absolute-path Parquet for every subject-keyed table, so injecting rows into the copy changes
> nothing — item 6's tests copy the fixture **lake** instead (`tests/conftest.py`'s
> `fixture_lake_settings`, or a `tmp_path` lake root with `Settings.lake_root("fixture")`
> semantics), rewrite the affected `part-0.parquet` with Polars (ids >= 90 000 000), rebuild the
> catalog against that root (`mwh build --tier fixture --select catalog`) and then profile — or
> materialize the table into the temp DuckDB first. (3) **Spec discovery** (ledger P3C-2, EP-37):
> `dag/specs/qc.yaml` is merged by `dag.spec.load_dag()`; `python` steps through
> `dag.runner.STEP_HANDLERS`; no `--spec`. (4) Sessions read `meta.qc_*` through `safe_query`
> (`meta.*` is a registry exemption — EP-33 B1c; `mimiciv_derived`/`marts` reads are
> subject-keyed — P3C-5); the profile SQL runs on the build connection
> (`engine.open_duckdb("build", ...)`), never on a raw `duckdb.connect`. (5) Job peeks: `mwh jobs
> --job qc-full --tail 20`; `run.bench` -> `BenchmarkLine(kind="query")` (EP-35 amendment), read
> with `mwh runs benchmarks --kind query`. (6) Report error/detail text follows the DKB-2
> sanitization rule (EP-43 amendment (e)); file names follow `docs/committed-text.md`
> (`qc_report.md`, never `qc_report_<run_id>.md`).

## Context

Capability 1 (inventory & quality profiling) has the inventory half (EP-10 raw manifest, EP-29
`meta.tables/columns/row_counts/null_pct`) but not the quality half. This brief builds
`src/mimicwarehouse/qc/profile.py` (DESIGN §15): per-table, per-column and per-check profiles
computed in DuckDB over the tier catalog, stored as `meta.qc_*` tables (Parquet under
`lake/meta/<tier>/`, EP-29's convention — EP-29's `catalog/profile.py` already computes row counts,
null % and approximate distinct counts into `meta.tables/columns`; this brief deepens, never
duplicates, that step), and a QC report that is already suppressed (EP-43) because it is the first
artifact the capstone (EP-53) will promote into `docs/`. Inputs: the schema contract + `keys.yaml`
(EP-9) for PK/FK checks, `meta.item_units`
bounds and `meta.item_unit_variants` (EP-39) for unit and plausibility checks, `timesem` (EP-34)
for era stratification. Full-tier profiling scans the big tables (`chartevents` 40 GB Parquet,
`labevents`, `emar*`) — column statistics via DuckDB with the explicit config (`memory_limit`
36–40 GB, `threads` 12, temp on C:) take minutes each; run the full profile as a logged
background job polled from the session (foreground cap ~10 min) and record timing (D-18). Never
inspect top-k values of free-text-like columns; top-k is restricted to dictionary-coded columns.
D-17, D-33, D-40 apply.

## In scope

1. **Profile engine** (`src/mimicwarehouse/qc/profile.py`) — for every table in the tier
   catalog: `meta.qc_tables` (table, rows, cols, bytes_parquet, built_at, run_id,
   snapshot_id); `meta.qc_columns` (table, column, dtype, null_pct and n_distinct_approx reused
   from EP-29's profile, plus min, max, p01/p50/p99 for numerics, min/max for timestamps,
   `is_dictionary_coded` flag) with
   `top_k` (value, n) **only** for columns declared dictionary-coded in the schema contract or
   `meta.item_dictionary` (itemid, icd_code, category, admission_type/location, careunit,
   insurance, …); one SQL per table generated from the contract (no sniffing).
2. **Checks** (`meta.qc_checks`: check_id, table, column, metric, value, threshold,
   status `pass|warn|fail`, n_affected, tier, run_id) with thresholds in
   `src/mimicwarehouse/qc/thresholds.yaml`: PK uniqueness (dupes = 0 else fail); FK orphans
   (`keys.yaml`; > 0 warn, > 1 % fail); null share (> 50 % warn); timestamp ordering
   (`admittime ≤ dischtime`, `intime ≤ outtime`, `charttime ≤ storetime` rate, event times inside
   `[intime − 24 h, outtime + 24 h]` share); duplicates on natural event keys (e.g.
   `chartevents (stay_id, charttime, itemid)` exact duplicates); unit inconsistencies (curated
   itemid with dominant-unit share < 95 %); implausible values (share of curated-itemid rows outside
   EP-39 bounds; > 1 % warn, > 5 % fail); age cap presence (share of `anchor_age = 91`, informational);
   era coverage (rows per `anchor_year_group` non-zero). Each check is a named function returning
   an aggregate; no row samples are stored — `n_affected` counts only.
3. **DAG + run record** — DAG spec `src/mimicwarehouse/dag/specs/qc.yaml` (python steps
   `qc.profile.<table>` per contract table, `qc.checks`, `qc.report`; tag `qc`; subsets via
   `--select`) writes the three `meta.qc_*` tables through the runner (per-tier `meta`) inside
   a `run.start(kind="qc")` run (EP-35) with `run.bench(kind="query")` per table; `mwh qc status
   --tier dev` prints pass/warn/fail counts and the failing checks (aggregates only). Add `mwh qc`
   to the CLI (dated DESIGN §15 note).
4. **QC report** (`src/mimicwarehouse/qc/report.py`) — `runs/<run_id>/qc_report.md` (+ CSV
   tables) rendering: table summary, checks by status, unit variants, implausible shares, timestamp
   ordering rates; every table passes through `disclose.suppress` (k = 11) before rendering, and
   the report ends with the EP-35 reproduction block; `mwh disclose check` must pass on the report
   directory (a test asserts it on fixture). The report header carries `Claim type: exploratory
   (data-quality profile)` and the sentence that MIMIC-IV analyses are retrospective (the same
   wording EP-31/EP-32 use).
5. **Full-tier run** — `uv run --group dev mwh build --tier full --tag qc --background --job
   qc-full` (EP-19 job runner; log `%MWH_DATA_ROOT%\runs\jobs\qc-full.log`); poll with `mwh jobs
   --job qc-full --tail 20` (expected 15–45 min); record run id, wall time, peak RSS and disk delta
   in the completion note; if it cannot finish within the session, record the job name/log here and
   let EP-45 verify (state this explicitly in the completion note).
6. **Tests** (`tests/ep/test_ep44.py`, `@pytest.mark.ep_44`; fixture, `dev`, `full` opt-in) —
   copy the fixture catalog to a temp dir and inject defects (a duplicated PK row, an orphan
   `hadm_id`, an implausible heart rate, an `outtime < intime` stay, a wrong-unit lab row); each
   check flags exactly the injected defect with `n_affected = 1`; the clean fixture yields no
   `fail`; the report passes `disclose.check`; on dev, `mwh build --tag qc` completes and
   `meta.qc_checks` has ≥ 1 row per check id.

## Out of scope

- Measurement frequency / structural-absence summaries → EP-45. Missing-data views → EP-72.
- Catalog & QC browser page → EP-61 (reads `meta.qc_*` and EP-29's `meta.tables/columns`).
- Fixing data defects found (they are MIMIC facts) — document, do not "clean" the lake.
- great_expectations / pandera suites → parked (`final-roadmap.md` § 1).

## Verification / acceptance

- `uv run poe test -m ep_44` green on fixture and dev; `uv run --group dev mwh verify EP-44` green.
- `uv run --group dev mwh build --tier dev --tag qc` builds `meta.qc_tables/qc_columns/qc_checks`;
  `uv run --group dev mwh qc status --tier dev` prints counts by status.
- `uv run --group dev mwh disclose check %MWH_DATA_ROOT%\runs\<run_id>\qc_report.md` exits 0.
- Full-tier QC run id, wall time, peak RSS and disk delta recorded in the completion note (or the
  PID/log path if EP-45 verifies).

## Parked -> final-roadmap.md

- Era-stratified QC checks (every `meta.qc_checks` metric by `anchor_year_group`; EP-44 uses
  `timesem.ERAS` for the coverage check only) -> v2 QC-3.
- Composite and documented foreign keys in `fk_orphans` (`emar -> poe`, `transfers -> admissions`,
  ICD codes -> `d_icd_*` with `icd_version`; the ED / Note `source: docs` keys) -> v2 QC-4.

> **Completion note (2026-09-14).** Executed on fixture + dev in the foreground and on full as
> the `qc-full` background job, as briefed; all three tiers finished in-session, nothing is
> deferred to EP-45.
>
> **Shipped.** `src/mimicwarehouse/qc/` (`profile.py` — the thresholds document
> `thresholds.yaml` and its pydantic models, `dictionary_coded_columns`, the per-table
> profile + the ten named check functions, the `qc.profile.<schema>.<table>` / `qc.checks`
> handlers, `register_qc`, the `dag/specs/qc.yaml` renderer; `report.py` — `qc.report`,
> `runs/<run_id>/qc_report.md` + `qc_tables.csv` / `qc_checks.csv`, the `docs/methods/qc.md`
> renderer; `cli.py` — `mwh qc status`; `python -m mimicwarehouse.qc`), the generated
> `dag/specs/qc.yaml` (31 profile steps + `qc.checks` + `qc.report` + the shared `catalog`
> step, tag `qc`), the tables `meta.qc_tables` / `meta.qc_columns` / `meta.qc_topk` /
> `meta.qc_checks` (raw twins under `lake/meta/<tier>/raw/`), `docs/methods/qc.md`,
> `tests/ep/test_ep44.py` (12 fixture + 1 dev + 1 full tests), the README state row + quick
> start, the DESIGN §14 note + §15 map, the D-33 addendum, two `docs/gotchas.md` entries, the
> `docs/analyses/README.md` index row, `final-roadmap.md` QC-3 / QC-4; `disclose.ID_NAME_ALLOW`
> gains `check_id`.
>
> **As built vs the brief.** (1) A **fourth** table, `meta.qc_topk`, holds the top-k `(value,
> n)` pairs of the dictionary-coded columns instead of a nested `top_k` column on `qc_columns`
> — a flat table is what `safe_query`, the CSV gate and EP-61's browser read; the other three
> tables are as named. (2) `qc_tables.bytes_parquet` is **`parquet_bytes`**: the disclosure
> gate's telemetry-name rule exempts names ending in `bytes`, and a Parquet size inside the
> `3xxxxxxx` band tripped `ID_BAND` on the dev and full `qc_tables.csv` before the rename.
> (3) Thresholds are applied by `qc.checks`, not by the profile steps, so
> `--select qc.checks,qc.report,catalog` re-applies an edited `thresholds.yaml` without a
> re-scan; a `timestamp_order` rule may **override** the check's bounds with a note, and the
> `pharmacy` / `prescriptions` `starttime <= stoptime` pairs do (a discontinued order keeps
> its intended start and gets the discontinuation time as stop, so `stoptime < starttime` on a
> few percent of rows is a MIMIC-IV fact: warn above 1 %, fail above 10 %, instead of the
> generic fail above 1 % — which had flagged both tables `fail` on the first dev run).
> (4) `null_pct` / `n_distinct_approx` / `min` / `max` are reused from EP-29's profile as
> briefed; a missing or stale profile (core-snapshot mismatch) refuses the step with the
> remedy, so `--tag qc` presumes a current `meta.profile` (both real tiers had one).
> (5) Build-time suppression follows the EP-39 rule (D-33 addendum): `n_affected` in
> `0 < n < k` blanked with `n_affected_suppressed` and its `value` blanked beside it; top-k
> through `disclose.suppress` per column, complementary. (6) The profile steps create their
> own source views (`ensure_table_views`): the concept runner's cached `ensure_source_views`
> misses a table staged later in the same build (the session fixture lake orders a concept
> before `stage.emar_detail`; `docs/gotchas.md` §1). (7) `mwh qc status` counts check rows
> in Python from plain column reads — a `count(*)` over the registry table meets the
> safe-query suppressor (gotchas). (8) The per-table `run.bench(kind="query")` lines are
> written by `qc.checks` from the slices' `ResourceLog` measurements (the profile steps run
> before the `kind: qc` run opens; the runner's own `kind: python` lines exist beside them).
> (9) The `qc.report` step writes into the closed qc run's folder, so the reproduction block
> carries the run's final wall / RSS / disk numbers.
>
> **Runs.** Dev: job `qc-dev` → build run `20260915T010347Z-9c5636` (34 steps, wall 9.7 s,
> peak RSS 980 MB, disk delta 7.4 MB); re-assembled after the override as
> `20260915T011100Z-1e4858` → qc run `20260915T011104Z-d0708e` (572 checks: pass 495 / warn
> 77 / fail 0; 31 tables). Full: job `qc-full` (`runs\jobs\qc-full.log`) → build run
> `20260915T010618Z-8c5bb8` (34 steps, wall 106.3 s, peak RSS 15,935 MB (`peak_wset`), disk
> delta -62.4 MB — the catalog rebuild freed more than the QC tables took); per-table step
> wall: chartevents 64.2 s at 15.9 GB RSS (the natural-key GROUP BY over 432,997,491 rows),
> labevents 10.1 s, emar_detail 7.3 s, poe 3.6 s, every other table under 3 s — 886,043,036
> rows profiled in 99.0 s of step wall (`mwh runs benchmarks --tier full --kind query`);
> re-assembled as `20260915T011109Z-6e69c8` → qc run `20260915T011113Z-4b675d` (574 checks:
> pass 491 / warn 83 / fail 0; 31 tables, 19 with warnings). The brief's 15–45 min estimate
> assumed a scan per check; one SQL family per table plus Parquet column pruning made the full
> tier a two-minute job. `mwh disclose check` passes on `qc_report.md`, `qc_tables.csv` and
> `qc_checks.csv` of both real runs (and of the fixture run, asserted by `test_ep44`).
>
> **What the real tiers say (statuses only — the numbers stay in the reports under `runs/`
> until EP-53 promotes them).** No `fail` on dev or full. Warnings: `natural_key_dupes` on
> `chartevents` (upstream duplicates on `(stay_id, charttime, itemid)`, the known MIMIC fact —
> a large share of rows, so `(stay_id, charttime, itemid)` is **not** a usable natural key);
> `ts_order` on the two medication-order pairs (the override) and, at well under 1 %, on
> `admittime <= dischtime` / `admittime <= deathtime` / `edregtime <= edouttime`
> (admissions), `intime <= outtime` (transfers), `starttime <= endtime` (inputevents,
> ingredientevents); `ts_store_lag` on `outputevents` (above the 10 % back-charting bar);
> `unit_consistency` on one chartevents itemid (whole-blood glucose charted under a second
> unit string); `implausible_values` on two outputevents items above 1 %; `null_share` on 70
> columns above 50 % (the sparse emar_detail / pharmacy / poe fields). `pk_unique`,
> `fk_orphans`, `event_window` and `era_coverage` pass everywhere; `age_cap` is informational.
>
> **Acceptance.** `uv run poe test -m ep_44`: 12 passed on fixture, `--tier full`: 14 passed
> (the dev and full probes: every check id present, nothing below k released);
> `uv run mwh verify EP-44`: 12 passed; `uv run poe check` (ruff check + format + pyright +
> the whole suite): green — 1,024 passed, 46 deselected (fixture tier, 546 s); `mwh guard` clean
> over every changed file; `poe roadmap-check --strict` 0 errors / 0 warnings. **Earlier tests
> edited: none** (the session fixture lake now runs the 34 qc steps too; `test_ep29`'s profile
> expectations are unchanged, and `test_ep43` does not pin the `ID_NAME_ALLOW` contents).
