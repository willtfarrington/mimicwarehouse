# Data-quality profiling: `meta.qc_*`, `mwh qc status`, the QC report (EP-44)

The one written answer to "what does the warehouse know about the quality of each staged
table, how is that computed, and where do the thresholds live?" - the prose twin of
`src/mimicwarehouse/qc/` (`profile.py`, `report.py`, `cli.py`) and its package data
`src/mimicwarehouse/qc/thresholds.yaml` (DESIGN section 15; GOVERNANCE sections 4, 5 and 7;
D-17, D-33, D-40). Capability 1 (inventory & quality profiling) has its inventory half in
EP-10's raw manifest and EP-29's `meta.tables` / `meta.columns`; this page is the quality
half. Nothing on this page is derived from patient data: the two tables below are rendered
from the thresholds document (`python -m mimicwarehouse.qc` re-renders the marked blocks;
`test_ep44` asserts they are in sync), and every data-derived surface - the four
`meta.qc_*` tables and the report - is described, not reproduced. All MIMIC-IV analyses in
this repository are retrospective.

## 1. What is profiled, and what never is

- **Aggregates only.** Every number the module writes, logs or prints is a row count, a
  share, an approximate distinct count, an extremum of a non-identifier measurement
  column, a quantile, or a dictionary value with its count. No check stores a row sample:
  `n_affected` counts the affected rows, and that is all it does (GOVERNANCE section 4).
- **Identifiers and free text are never inspected.** The contract's `identifier` /
  `free_text` flags (`keys.yaml`, EP-17) exclude a column from extrema, quantiles and
  top-k values; the EP-29 profile already leaves their `min` / `max` NULL.
- **Top-k values are for dictionary-coded columns only.** A column is dictionary-coded
  when it is a foreign key into a dimension table (`itemid` -> `d_items` / `d_labitems`,
  `hcpcs_cd` -> `d_hcpcs`) or when `thresholds.yaml` declares it - the categorical
  vocabularies MIMIC-IV documents (`admission_type`, `careunit`, `insurance`, `icd_code`,
  ...). The declaration is validated: an identifier or free-text column cannot be declared.
  The list, as resolved against the contract:

<!-- dictionary:begin -->
| table | dictionary-coded columns |
|---|---|
| `mimiciv_hosp.admissions` | `admission_type`, `admission_location`, `discharge_location`, `insurance`, `language`, `marital_status`, `race` |
| `mimiciv_hosp.d_hcpcs` | `category` |
| `mimiciv_hosp.d_icd_diagnoses` | `icd_version` |
| `mimiciv_hosp.d_icd_procedures` | `icd_version` |
| `mimiciv_hosp.d_labitems` | `fluid`, `category` |
| `mimiciv_hosp.diagnoses_icd` | `icd_code`, `icd_version` |
| `mimiciv_hosp.drgcodes` | `drg_type`, `drg_severity`, `drg_mortality` |
| `mimiciv_hosp.emar` | `event_txt` |
| `mimiciv_hosp.emar_detail` | `administration_type` |
| `mimiciv_hosp.hcpcsevents` | `hcpcs_cd` |
| `mimiciv_hosp.labevents` | `itemid`, `flag`, `priority` |
| `mimiciv_hosp.microbiologyevents` | `interpretation` |
| `mimiciv_hosp.omr` | `result_name` |
| `mimiciv_hosp.patients` | `gender`, `anchor_year_group` |
| `mimiciv_hosp.pharmacy` | `proc_type`, `status` |
| `mimiciv_hosp.poe` | `order_type`, `transaction_type`, `order_status` |
| `mimiciv_hosp.poe_detail` | `field_name` |
| `mimiciv_hosp.prescriptions` | `drug_type`, `route` |
| `mimiciv_hosp.procedures_icd` | `icd_code`, `icd_version` |
| `mimiciv_hosp.services` | `prev_service`, `curr_service` |
| `mimiciv_hosp.transfers` | `eventtype`, `careunit` |
| `mimiciv_icu.chartevents` | `itemid`, `warning` |
| `mimiciv_icu.d_items` | `linksto`, `category`, `param_type` |
| `mimiciv_icu.datetimeevents` | `itemid`, `valueuom`, `warning` |
| `mimiciv_icu.icustays` | `first_careunit`, `last_careunit` |
| `mimiciv_icu.ingredientevents` | `statusdescription` |
| `mimiciv_icu.inputevents` | `itemid`, `ordercategoryname`, `ordercategorydescription`, `statusdescription` |
| `mimiciv_icu.outputevents` | `itemid` |
| `mimiciv_icu.procedureevents` | `itemid`, `locationcategory`, `ordercategoryname`, `statusdescription` |
<!-- dictionary:end -->

- **Small cells are suppressed when the tables are built**, not when they are read
  (`meta.*` is a safe-query registry exemption - the EP-39 rule, D-33 addendum): in
  `meta.qc_checks` an `n_affected` in `0 < n < k` is blanked with `n_affected_suppressed =
  true` and its metric `value` blanked beside it (a share would restore the count); in
  `meta.qc_topk` the `(value, n)` pairs of each column go through `disclose.suppress`
  (complementary: a lone small cell takes its next-smallest neighbour with it, `share` is
  blanked beside a hidden `n`). The raw twins stay under `lake/meta/<tier>/raw/`, a
  directory the catalog's discovery walker never enters and nothing exports.
- **Nothing is cleaned.** Duplicates, orphans, back-charted rows and out-of-window events
  are MIMIC-IV facts; the profile documents them for cohort builders (EP-46/47) and the
  lake is never edited.

## 2. The four tables

| table | one row per | columns |
|---|---|---|
| `meta.qc_tables` | profiled table | `rows`, `columns`, `parquet_bytes` (the brief's `bytes_parquet`, renamed so the disclosure gate's telemetry-name rule - a name ending in `bytes` is never an id - exempts a byte count that happens to fall in an id band), `files`, `worst_status`, the profile step's `wall_s` / `peak_rss_mb`, `built_at`, `tier`, `build_id`, `run_id`, `snapshot_id` (core) |
| `meta.qc_columns` | column | `dtype`, `is_identifier` / `is_free_text` / `is_dictionary_coded`, `null_pct` and `n_distinct_approx` **reused from the EP-29 profile** (`meta.profile`; a missing or stale profile refuses the step with the remedy), `min_value` / `max_value` (EP-29's VARCHAR casts; NULL for identifiers), `p01` / `p50` / `p99` (DuckDB `approx_quantile`, a t-digest) for numeric non-identifier columns that are not dictionary-coded |
| `meta.qc_topk` | dictionary-coded column x value (top `top_k` = 10) | `rank`, `value` (clipped to 64 characters), `n`, `share` (of the column's non-null rows), `n_suppressed`, `k` |
| `meta.qc_checks` | check x table (x column / itemid) | `check_id`, `column`, `itemid`, `label`, `metric`, `value`, `threshold`, `rule`, `status` (`pass` / `warn` / `fail`), `n_affected`, `n_affected_suppressed`, `k`, `detail` |

Every row carries `tier`, `build_id` and the `run_id` of the `kind: qc` run that wrote
it (section 4). The tables are Parquet under `lake/meta/<tier>/` (EP-29's layout) and
enter the tier catalog through EP-37's discovery walker; `qc.profile.register_qc` (a
`CATALOG_EXTENSIONS` entry after the walker, before the phenotype and unit extensions)
comments them.

## 3. The checks

One SQL family per contract table, generated from the contract (no sniffing) and run on
the build connection over the tier's staged Parquet - the dev tier inherits the bucket
filter. Each check is a named function in `qc/profile.py` returning an aggregate;
`qc.checks` applies the thresholds afterwards, so a threshold change is picked up by
`mwh build --tier <t> --select qc.checks,qc.report,catalog` without a re-scan.
`fail` beats `warn`; a check whose denominator is empty passes with a NULL value; a check
without bounds is informational.

<!-- checks:begin -->
| check | metric | rule | what it counts |
|---|---|---|---|
| `pk_unique` | `duplicate_rows` | fail > 0 | rows beyond the first per declared primary key (keys.yaml primary_keys); the loader never deduplicates |
| `natural_key_dupes` | `duplicate_rows` | warn > 0 | rows beyond the first per uniqueness_hint key (tables without an upstream PK; chartevents has known upstream duplicates - a MIMIC fact, documented, never 'cleaned') |
| `fk_orphans` | `orphan_share` | warn > 0; fail > 0.01 | share of non-null foreign-key values without a match in the referenced table (keys.yaml foreign_keys); NULL keys are not orphans |
| `null_share` | `null_share` | warn > 0.5 | null fraction per column (reused from the EP-29 profile) |
| `ts_order` | `violation_share` | warn > 0; fail > 0.01 | rows whose later timestamp precedes the earlier one, among rows with both set (timestamp_order rules below) |
| `ts_store_lag` | `violation_share` | warn > 0.1 | rows stored before they were charted (storetime < charttime); a rate, not a defect - back-charting is normal, a high share is a data-flow warning |
| `event_window` | `outside_share` | warn > 0.01; fail > 0.05 | ICU events outside [intime - slack, outtime + slack] of their stay, among events joined to a stay with both bounds |
| `unit_consistency` | `dominant_unit_share` | warn < 0.95 | share of a curated itemid's rows charted in its dominant (normalised) unit string; EP-39 catalogue items only |
| `implausible_values` | `implausible_share` | warn > 0.01; fail > 0.05 | share of a curated itemid's valued rows outside the EP-39 plausibility bounds after unit harmonisation (unknown units judged on the raw value, as mwh_harmonize does) |
| `age_cap` | `capped_share` | informational | share of patients whose anchor_age is the 91 cap (ages >= 89); informational |
| `era_coverage` | `empty_eras` | fail > 0 | anchor_year_group eras (timesem.ERAS) with no patient row; every real tier covers all five |
<!-- checks:end -->

Rule inputs: primary keys and `uniqueness_hint` keys from `keys.yaml` (EP-9); foreign keys
from the same file (single-column, both tables present on the tier); the `timestamp_order`
pairs (`admittime <= dischtime`, `admittime <= deathtime`, `edregtime <= edouttime`,
`intime <= outtime` on `transfers` and `icustays`, `starttime <= endtime` / `stoptime` on
the infusion, procedure, pharmacy and prescription tables - a pair may **override** the
check's bounds with a note saying why, and the two medication-order pairs do: a
discontinued `pharmacy` / `prescriptions` order keeps its intended `starttime` and gets
the discontinuation time as `stoptime`, so `stoptime < starttime` on a few percent of rows
is a MIMIC-IV fact; those two rules warn above 1 % and fail above 10 %, and their
`meta.qc_checks.rule` cell says `(rule override)`), the `store_lag` pairs
(`charttime <= storetime` on the six charted tables) and the `event_window` rule (six ICU
event tables against `icustays`, 24 h slack, the stay bounds `min(intime)` /
`max(outtime)` per `stay_id`) from `thresholds.yaml`; the curated itemids, accepted
units, conversions and inclusive plausibility bounds from the EP-39 catalogue
(`units.py`, `docs/methods/units.md`) - the conversion is inlined per itemid, never the
`mwh_harmonize` macro per row (`docs/gotchas.md` section 1); the five era labels from
`timesem.ERAS`; the age cap from `timesem.AGE_CAP`.

## 4. Steps, runs and the report

- `mwh build --tier <t> --tag qc` runs, in order, `qc.profile.<schema>.<table>` (one
  python step per contract table of the staged schemas; each measures itself with
  `run.ResourceLog` and writes a JSON slice under `lake/meta/<tier>/raw/qc/`),
  `qc.checks` (reads every slice of the tier's complete tables, refuses a missing or
  stale one, evaluates the thresholds, assembles and suppresses the four tables and
  writes them inside a **`run.start(kind="qc")`** run - one `run.bench(kind="query")`
  line per profiled table, read with `mwh runs benchmarks --kind query`), `qc.report`
  and the shared `catalog` step. `--select` subsets work (`qc.profile.mimiciv_icu.chartevents,qc.checks,qc.report,catalog`).
  The profile steps depend on `meta.profile` (EP-29) because they reuse its aggregates;
  `--tag qc` does not pull it in, so the tier's profile must exist and match the current
  core snapshot (the steps check and name the remedy). The full tier is a background job:
  `mwh build --tier full --tag qc --background --job qc-full`, polled with `mwh jobs
  --job qc-full --tail 20`.
- **The report** `runs/<run_id>/qc_report.md` (+ `qc_tables.csv`, `qc_checks.csv`) is
  written into the qc run's folder by `qc.report`: the header carries `Claim type:
  exploratory (data-quality profile)`, the retrospective sentence and the disclosure line;
  then the table summary, the checks by status, the warnings and failures, the unit
  variants of the curated itemids, the implausible shares, the timestamp-ordering rates,
  "what it deliberately does not claim", and the EP-35 reproduction + provenance block.
  Every frame passes through `disclose.suppress` (EP-43) before rendering, every integer
  through `fmt_int`, a suppressed count renders as `<k`; `mwh disclose check` passes on every
  file (`test_ep44` asserts it on the fixture), so EP-53 can promote the report into
  `docs/` with a sidecar. File names follow `docs/committed-text.md`.
- `mwh qc status --tier <t> [--show fail|warn|all|none] [--json]` reads
  `meta.qc_checks` / `meta.qc_tables` through `safe_query` (audited) and prints the
  pass / warn / fail counts per check and the flagged rows; exit 1 when any check failed,
  so a caller can gate on it.

## 5. Caveats

- The dev tier profiles the bucket-filtered lake, so its shares describe the 5 % sample;
  cross-bucket facts (a `chartevents` duplicate whose twin sits in another bucket) are
  full-tier facts.
- `natural_key_dupes` on `chartevents` is expected to warn on the real tiers: upstream
  ships duplicates on `(stay_id, charttime, itemid)`, which is why the contract declares
  no primary key there (keys.yaml).
- `ts_store_lag` measures back-charting, a normal clinical workflow; it warns only above
  10 %. `event_window` uses a 24 h slack on purpose (pre-admission charting and
  post-discharge stores are common at the edges of a stay).
- `implausible_values` judges an unknown unit on its raw value (as `mwh_harmonize`
  does); the `unit_consistency` share says how often that happens.
- Percentiles and distinct counts are approximations; `min` / `max` are exact.
- Structural-absence / measurement-frequency summaries are EP-45's; missing-data views
  EP-72's; the Catalog & QC browser page (EP-61) reads `meta.qc_*` beside EP-29's
  `meta.tables` / `meta.columns`; the capstone (EP-53) promotes the report;
  great_expectations / pandera suites, cross-build drift dashboards, era-stratified
  checks and the composite / documented foreign keys are parked
  (`roadmap/final-roadmap.md` QC-1 to QC-4).
