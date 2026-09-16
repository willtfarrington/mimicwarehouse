# Measurement process: `meta.mp_*`, `mwh qc measurement`, the measurement report (EP-45)

The one written answer to "how often is each curated item measured over an ICU stay, where
is it never charted at all, and does its presence in the first 24 h go with the outcome?" -
the prose twin of `src/mimicwarehouse/qc/measurement.py` and `dag/specs/measurement.yaml`
(DESIGN section 14/15; GOVERNANCE sections 4, 5 and 7; D-5, D-33, D-40). Capability 7
(missing-data and measurement-process analysis) starts here with its descriptive half; the
missing-data views are EP-72's and the imputation strategies EP-87's. Nothing on this page
is derived from patient data: every data-derived surface - the six `meta.mp_*` tables and
the report - is described, not reproduced. All MIMIC-IV analyses in this repository are
retrospective.

## 1. Why: informative presence, structural vs unmeasured absence

In ICU data *whether* something was measured carries information (informative presence:
a lactate is drawn when someone is worried), and an absence has two very different
causes. **Structural** absence: the item is not charted in that care unit or era at all -
a documentation-system fact (the ICU tables are MetaVision-era; charting practice varies
by unit and changed across eras), not a clinical decision. **Unmeasured** absence: the
item is in use where the stay was, but this stay did not get it. Any analysis that
imputes, models missingness or uses measurement indicators as features (EP-87, and the
parked informative-presence models, `final-roadmap.md` MISS-2) needs the two told apart.
The clinical theme here (D-5: one theme per capability) is first-24-h vitals and labs.

## 2. Definitions

- **Population.** Every ICU stay (`icustays`) with a valid `[intime, outtime)` (both set,
  `outtime > intime`) and a `patients` row; not adults only, not first stays only. The
  report states how many `icustays` rows were excluded.
- **Care unit and era.** `icustays.first_careunit` and `patients.anchor_year_group` - the
  only admissible cross-patient time axis (`timesem.ERAS`; timestamps are per-patient
  shifted, so nothing here is calendar time). A NULL unit or era is its own cell,
  `(unknown)`.
- **Itemid set.** Every curated item of the EP-39 catalogue (`units.py`,
  `meta.item_units`: 63 itemids over `chartevents`, `labevents`, `outputevents`) by
  default; a step's `params.itemids` narrows it (a subset of the catalogue - an
  uncurated itemid is refused).
- **Measurement occasion.** A distinct `(stay, itemid, charttime)`: chartevents' known
  upstream duplicates count once. `chartevents` / `outputevents` / `inputevents` events
  attach to their `stay_id`; `labevents` (no `stay_id`) attach to the stay of the same
  subject whose window contains `charttime`. Events outside `[intime, outtime)` are not
  measurements of the stay (pre-ICU labs included); EP-44's `event_window` check counts
  them.
- **Bins.** Hours since `intime` (`timesem.sql_hours_since`) in `[start, end)` bins
  (`timesem.sql_hour_bin`): 168 one-hour bins (the `hour_bin` grain) and 14 one-day bins
  (the `icu_day` grain). **At risk** in a bin = still in the ICU when the bin starts
  (`los_hours > start`), so a stay measured in a bin is always at risk in it.
- **First window.** The first 24 h since `intime` (`first_window_hours`); a stay shorter
  than 24 h is judged on its whole stay.
- **Structural cell.** A unit x era cell of at least `min_stays` (50) stays in which no
  stay was ever measured for the item (share = 0). `sparse`: the share of measured stays
  is below `sparse_share` (5 %). `in_use`: the rest. A cell below 50 stays with no
  measurement is `sparse`, never `structural` (too few stays to call the item absent).
- **Attribution.** A stay without a measurement of the item in its first window is
  *structural* when its unit x era cell is structural and *unmeasured* otherwise. Because
  a structural cell has no measurement at all, `n_structural` is the number of stays in
  the item's structural cells and `n_unmeasured = n_missing_first_24h - n_structural`.
- **Presence-outcome contrast.** For each curated lab, stays with vs without a measurement
  in the first window (`binary`) and by number of occasions `0` / `1-2` / `>=3` (`count`);
  the outcome is in-hospital mortality, `admissions.hospital_expire_flag` (stays without
  a flag are outside this population). The rate ratio (arm / reference arm; reference =
  `not_measured` / `0`) carries a Wald 95 % CI on the log scale - statsmodels
  `Table2x2.riskratio_confint` (a zero cell shifts every cell by 0.5, the library
  default) - computed in Python from the **released** counts, never in SQL (arithmetic
  over aggregates stays refused, `final-roadmap.md` DIS-3). Every row says
  `claim_type = exploratory` and carries the caveat: descriptive association only, not
  adjusted, not causal.

## 3. The six tables

| table | one row per | columns |
|---|---|---|
| `meta.mp_item_hourly` | itemid x ICU hour bin (0 ... 167) | `n_stays_at_risk`, `n_stays_measured`, `n_measurements` (each with a `<count>_suppressed` marker) |
| `meta.mp_item_daily` | itemid x ICU day (0 ... 13) | the same |
| `meta.mp_item_summary` | itemid | `label`, `source`, `n_stays` (the population), `n_stays_measured` (any time), `n_stays_measured_first_24h`, `measured_first_24h_share`, `median_per_stay_day` (occasions per stay-day, median over measured stays), `p10_interval_min` / `median_interval_min` / `p90_interval_min` (minutes between consecutive occasions of one stay) |
| `meta.mp_structural` | itemid x `first_careunit` x era | `n_stays`, `n_stays_measured`, `share`, `structural_flag` (`in_use` / `sparse` / `structural`; NULL where the measured count is suppressed) |
| `meta.mp_absence_summary` | itemid | `n_missing_first_24h`, `n_structural`, `n_unmeasured` |
| `meta.mp_presence_outcome` | lab itemid x contrast x arm | `n_stays`, `n_deaths`, `mortality_rate`, `rate_ratio`, `rr_ci_low`, `rr_ci_high`, `claim_type`, `caveat` |

Every row carries `k`, `tier`, `build_id` and the `run_id` of the `kind: qc` run that
wrote it (section 5). The tables are Parquet under `lake/meta/<tier>/` (EP-29's layout)
and enter the tier catalog through EP-37's discovery walker;
`qc.measurement.register_measurement` (a `CATALOG_EXTENSIONS` entry right after EP-44's)
comments them. The raw, unsuppressed slices the compute steps wrote stay under
`lake/meta/<tier>/raw/measurement/` (a directory the walker never enters and nothing
exports).

## 4. Suppression (the EP-39 / EP-44 rule, D-33 addenda)

`meta.*` is a safe-query registry exemption, so the small-cell rule is applied when the
tables are **built**: `disclose.suppress` (EP-43, k = 11, complementary) runs over each
published frame with its count columns and group margins -

- the hourly / daily grids over `(itemid, bin)`: the nested pairs at-risk >= measured and
  measurements >= measured hide a derivable small difference as well as the small cells;
- the item summary and the absence summary as **one** frame per itemid (six count
  columns): `n_missing_first_24h = n_stays - n_stays_measured_first_24h` and
  `n_missing = n_structural + n_unmeasured` are nested pairs the primitive sees, so a
  hidden count in one table cannot be backed out from the other; the statistics (medians,
  quantiles, the share) are blanked on a row that lost a count;
- the structural grid over `(itemid, careunit, era)`: `share` is blanked beside a hidden
  count and `structural_flag` is blanked wherever `n_stays_measured` is hidden (a flag
  would bound the hidden count); a structural cell's zero is never small;
- the presence arms over `(itemid, contrast, arm)` plus the cross-contrast rule: the
  binary `measured` arm is the sum of the `1-2` and `>=3` arms and `not_measured` is the
  `0` arm, so a hidden count on either side hides its counterpart (to a fixpoint);
  `mortality_rate`, `rate_ratio` and the CI are blank for every `(itemid, contrast)` group
  that lost a cell, and the rate ratio is computed only from released counts.

The report renders every suppressed count as `<11` and passes `mwh disclose check` on
every file (`test_ep45` asserts it on the fixture), so EP-53 can promote it with a
sidecar.

## 5. Steps, runs, the report, the CLI

- `mwh build --tier <t> --tag measurement` runs `measurement.hourly`,
  `measurement.structural` and `measurement.presence` (each scans the event tables once
  on the build connection - a temp table of occasions per source - measures itself with
  `run.ResourceLog` and writes its raw slice), then `measurement.report` (reads the three
  slices - a missing one, a stale one computed over another core snapshot, or slices with
  differing params refuse with the remedy - opens the one **`run.start(kind="qc")`** run
  named `measurement`, records the SQL, the catalogue and params refs and one
  `kind: query` benchmark line per compute step, assembles and suppresses the six tables,
  writes them, and once the run is closed renders `runs/<run_id>/measurement_process.md`
  plus one CSV per table into the run's folder) and the shared `catalog` step.
  `--select measurement.report,catalog` re-suppresses and re-renders without a rescan.
  The full tier is a background job: `mwh build --tier full --tag measurement
  --background --job measurement-full`, polled with `mwh jobs --job measurement-full
  --tail 20`; `mwh runs benchmarks --tier full --kind query` lists the step timings.
- **Params.** Each python step takes an optional `params` mapping (the EP-45 extension of
  the DAG spec, `dag.spec.Step.params`): `itemids`, `hours` (168), `days` (14),
  `first_window_hours` (24), `min_stays` (50), `sparse_share` (0.05). The slices record
  the params they were computed with; the run manifest records them and their hash.
- **The report** carries `Claim type: exploratory (measurement process)`, the
  retrospective sentence and the disclosure line; then the measurement-frequency table,
  the at-risk profile by ICU hour and day, the structural map (cells per flag per item,
  the structural cells, the first-24-h absence split), the informative-presence tables
  (binary and count contrasts), "what it deliberately does not claim" and the EP-35
  reproduction + provenance block.
- `mwh qc measurement --tier <t> [--top N] [--json]` reads `meta.mp_item_summary` and
  `meta.mp_structural` through `safe_query` (audited; plain column reads - a `count(*)`
  over a registry table would meet the suppressor, `docs/gotchas.md` section 1) and
  prints the per-item summary with the cell counts per flag and the top structural cells.

## 6. Caveats

- Charting practice varies by care unit and by era: a structural cell says the item was
  not documented under that itemid there - MetaVision itemids only (the ICU tables are
  MetaVision-era; CareVue-era itemids are not in MIMIC-IV), which is why the same
  physiology can be structural under one itemid and in use under another (the three
  systolic pressure items, the two temperature items).
- Nothing is calendar time: `anchor_year_group` is the only era axis and it mixes
  charting practice with case mix.
- The unit of analysis is the ICU stay; repeated stays of one admission share the
  in-hospital outcome; labs attach to the stay whose window contains the draw, so a lab
  drawn in the ED before ICU admission is not a measurement of the stay.
- The dev tier describes the 5 % subject sample: its structural map has fewer cells above
  `min_stays`, and its suppression hides more.
- Medians, quantiles and intervals describe measurement occasions, not clinical values;
  the presence-outcome rate ratios are unadjusted descriptive associations.
- Known residual (recorded in the D-33 addendum of EP-45): the `n_stays_at_risk` column
  of the hourly grid is a non-increasing sequence, so consecutive differences - stays
  leaving the ICU in one bin - can be small and are derivable from two published
  neighbours; the table-mode primitive suppresses cells, not steps, and the gate does not
  treat an at-risk curve as an attrition chain (parked as MISS-4 for EP-53 / EP-72).
- Missingness-pattern heatmaps and the page are EP-72's; imputation strategies (MICE and
  the like) EP-87's; formal informative-presence models and MNAR sensitivity are parked
  (`final-roadmap.md` MISS-2); per-unit x era hourly profiles and era-stratified interval
  statistics are parked (MISS-3); prevalence / rate estimators with denominators are
  EP-68's.
