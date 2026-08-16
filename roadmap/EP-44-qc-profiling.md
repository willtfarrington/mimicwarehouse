# EP-44 — Data-quality profiling

**Size:** M · **Tier:** fixture+dev+full · **Core/Stretch:** core · **Depends on:** EP-29 (Catalog & data dictionary (meta.*)), EP-39 (Itemid dictionary curation + unit harmonization), EP-43 (Disclosure primitives (`disclose` module)) · **Blocks:** EP-45 (Measurement-process summaries), EP-53 (Capstone #1: concepts/QC case study), EP-54 (Re-plan P3), EP-61 (Catalog & QC browser page)

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
