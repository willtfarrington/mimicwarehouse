# mimic-code concepts — inventory and status on DuckDB 1.5.x (EP-37)

The human-readable inventory of the vendored MIT-LCP/mimic-code `concepts_duckdb` tree
(MIT; attribution in the repo-root `NOTICE`, D-19) as the warehouse runs it into
`mimiciv_derived`. The table between the markers is **generated** from
`src/mimicwarehouse/concepts/concepts.yaml` by `mimicwarehouse.concepts.inventory`
(`uv run python -m mimicwarehouse.concepts.inventory` regenerates the inventory, the DAG
spec `dag/specs/concepts.yaml` and this table; `tests/ep/test_ep37.py` pins all three to a
fresh scan of the vendor tree). Nothing on this page is derived from data: it is file
metadata, graph structure and the executability verdict.

## How the concepts run

- **One DAG step per concept**, `concept.<group>.<name>` (`mwh build --tier <t> --tag
  concepts`; a subset with `--select concept.<group>.<name> --with-deps`). Each step strips
  upstream's `DROP TABLE … ; CREATE TABLE mimiciv_derived.<name> AS` header and sinks the
  SELECT to **one** ZSTD Parquet file per tier,
  `lake/derived/<tier>/mimiciv_derived/<name>/part-0.parquet` for dev and full (no bucket
  partitions; fixture and demo keep the same shape under their own lake roots,
  `lake/fixture/derived/fixture/…` and `lake/demo/derived/demo/…`). The step reads the
  tier's staged core tables (the dev tier = the dev buckets) and the concepts already
  complete for the tier; a rerun resumes per concept through `status.json`, and a dev
  rebuild never touches the full tier's files.
- **Order** is upstream's `duckdb.sql` driver order, verified topological against the
  `mimiciv_derived.*` references found in each file (the "depends on" column); the graph
  is asserted acyclic at generation time.
- **`meta.concept_versions`** (the step after every concept) records one row per attempted
  concept — concept, group, upstream commit, `sql_sha256`, `patch_id` (NULL until EP-38),
  rows, bytes, wall time, status, error, built_at, build id, run id, derived snapshot id —
  as `lake/meta/<tier>/concept_versions.parquet`, opens one provenance run (`mwh runs list
  --kind build`) and appends `kind: concept` benchmark lines (`mwh runs benchmarks --kind
  concept --tier <t>`). The **catalog** step then registers every complete derived table as
  a `mimiciv_derived.<name>` view and the versions file as `meta.concept_versions`
  (`mwh sql "SELECT concept, rows FROM meta.concept_versions ORDER BY 1" --tier <t>`).
- **Count-pins.** `tests/ep/pins/concepts_demo.json` pins the demo tier (ODbL; every cell
  released through `safe_query`, counts below 11 stored as `"<11"`); the dev pins live
  under the data root (`runs/pins/concepts_dev.json`, a drift detector, never committed).
- **Full tier** runs as the background job `concepts-full` (EP-37 completion note); EP-38
  verifies its timing and count-pins.

## Status on DuckDB 1.5.x

All vendored files execute cleanly on the pinned DuckDB 1.5.5: the EP-33 D2 pre-flight
smoke (65/65 in driver order, throwaway demo-catalog copy) and EP-37's demo- and dev-tier
builds through the runner. **Concepts failing on 1.5.x: none** at the pin
`8bcbd190ca75…` — the "status" column below comes from
`mimicwarehouse.concepts.inventory.KNOWN_FAILURES`, the place EP-38 records a failing
concept with its DuckDB error class before porting the fix as a patch beside `vendor/`.
Execution success is not numerical correctness: the open upstream concept-logic PRs
(SIRS wbc guard, lab `valueuom`, Charlson, APS-III; roadmap Risk 2) and the count-pin
verification on the full tier are EP-38's charter. Some concepts are empty or partial on
the synthetic fixture by design (`tests/fixtures/COVERAGE.md`).

## Inventory

<!-- concepts:begin -->
| # | concept | group | reads (core) | depends on (derived) | upstream | status on DuckDB 1.5.x |
|---|---|---|---|---|---|---|
| 1 | `icustay_times` | demographics | `chartevents`, `icustays` | - | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds) |
| 2 | `icustay_hourly` | demographics | - | `icustay_times` | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds) |
| 3 | `weight_durations` | demographics | `chartevents`, `icustays` | - | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds) |
| 4 | `urine_output` | measurement | `outputevents` | - | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds) |
| 5 | `kdigo_uo` | organfailure | `icustays` | `urine_output`, `weight_durations` | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds) |
| 6 | `age` | demographics | `admissions`, `patients` | - | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds) |
| 7 | `icustay_detail` | demographics | `admissions`, `patients`, `icustays` | - | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds) |
| 8 | `bg` | measurement | `labevents`, `chartevents` | - | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds) |
| 9 | `blood_differential` | measurement | `labevents` | - | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds) |
| 10 | `cardiac_marker` | measurement | `labevents` | - | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds) |
| 11 | `chemistry` | measurement | `labevents` | - | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds) |
| 12 | `coagulation` | measurement | `labevents` | - | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds) |
| 13 | `complete_blood_count` | measurement | `labevents` | - | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds) |
| 14 | `creatinine_baseline` | measurement | `diagnoses_icd`, `patients` | `age`, `chemistry` | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds) |
| 15 | `enzyme` | measurement | `labevents` | - | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds) |
| 16 | `gcs` | measurement | `chartevents` | - | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds) |
| 17 | `height` | measurement | `chartevents` | - | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds) |
| 18 | `icp` | measurement | `chartevents` | - | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds) |
| 19 | `inflammation` | measurement | `labevents` | - | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds) |
| 20 | `oxygen_delivery` | measurement | `chartevents` | - | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds) |
| 21 | `rhythm` | measurement | `chartevents` | - | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds) |
| 22 | `urine_output_rate` | measurement | `chartevents`, `icustays` | `urine_output`, `weight_durations` | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds) |
| 23 | `ventilator_setting` | measurement | `chartevents` | - | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds) |
| 24 | `vitalsign` | measurement | `chartevents` | - | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds) |
| 25 | `charlson` | comorbidity | `admissions`, `diagnoses_icd` | `age` | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds) |
| 26 | `acei` | medication | `prescriptions` | - | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds) |
| 27 | `antibiotic` | medication | `prescriptions`, `icustays` | - | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds) |
| 28 | `arb` | medication | `prescriptions` | - | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds) |
| 29 | `dobutamine` | medication | `inputevents` | - | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds) |
| 30 | `dopamine` | medication | `inputevents` | - | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds) |
| 31 | `epinephrine` | medication | `inputevents` | - | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds) |
| 32 | `milrinone` | medication | `inputevents` | - | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds) |
| 33 | `neuroblock` | medication | `inputevents` | - | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds) |
| 34 | `norepinephrine` | medication | `inputevents` | - | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds) |
| 35 | `nsaid` | medication | `prescriptions` | - | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds) |
| 36 | `phenylephrine` | medication | `inputevents` | - | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds) |
| 37 | `vasopressin` | medication | `inputevents` | - | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds) |
| 38 | `code_status` | treatment | `poe`, `poe_detail`, `chartevents`, `icustays` | - | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds) |
| 39 | `crrt` | treatment | `chartevents` | - | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds) |
| 40 | `invasive_line` | treatment | `d_items`, `procedureevents` | - | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds) |
| 41 | `rrt` | treatment | `chartevents`, `inputevents`, `procedureevents` | - | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds) |
| 42 | `ventilation` | treatment | - | `oxygen_delivery`, `ventilator_setting` | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds) |
| 43 | `first_day_bg` | firstday | `icustays` | `bg` | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds) |
| 44 | `first_day_bg_art` | firstday | `icustays` | `bg` | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds) |
| 45 | `first_day_gcs` | firstday | `icustays` | `gcs` | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds) |
| 46 | `first_day_height` | firstday | `icustays` | `height` | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds) |
| 47 | `first_day_lab` | firstday | `icustays` | `blood_differential`, `chemistry`, `coagulation`, `complete_blood_count`, `enzyme` | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds) |
| 48 | `first_day_rrt` | firstday | `icustays` | `rrt` | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds) |
| 49 | `first_day_urine_output` | firstday | `icustays` | `urine_output` | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds) |
| 50 | `first_day_vitalsign` | firstday | `icustays` | `vitalsign` | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds) |
| 51 | `first_day_weight` | firstday | `icustays` | `weight_durations` | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds) |
| 52 | `kdigo_creatinine` | organfailure | `labevents`, `icustays` | - | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds) |
| 53 | `meld` | organfailure | `icustays` | `first_day_lab`, `first_day_rrt` | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds) |
| 54 | `apsiii` | score | `admissions`, `diagnoses_icd`, `patients`, `icustays` | `bg`, `first_day_gcs`, `first_day_lab`, `first_day_urine_output`, `first_day_vitalsign`, `ventilation` | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds) |
| 55 | `lods` | score | `admissions`, `patients`, `chartevents`, `icustays` | `bg`, `first_day_gcs`, `first_day_lab`, `first_day_urine_output`, `first_day_vitalsign`, `ventilation` | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds) |
| 56 | `oasis` | score | `admissions`, `patients`, `services`, `icustays` | `age`, `first_day_gcs`, `first_day_urine_output`, `first_day_vitalsign`, `ventilation` | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds) |
| 57 | `sapsii` | score | `admissions`, `diagnoses_icd`, `services`, `chartevents`, `icustays` | `age`, `bg`, `chemistry`, `complete_blood_count`, `enzyme`, `gcs`, `urine_output`, `ventilation`, `vitalsign` | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds) |
| 58 | `sirs` | score | `icustays` | `first_day_bg_art`, `first_day_lab`, `first_day_vitalsign` | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds) |
| 59 | `sofa` | score | `icustays` | `bg`, `chemistry`, `complete_blood_count`, `dobutamine`, `dopamine`, `enzyme`, `epinephrine`, `gcs`, `icustay_hourly`, `norepinephrine`, `urine_output_rate`, `ventilation`, `vitalsign` | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds) |
| 60 | `suspicion_of_infection` | sepsis | `microbiologyevents` | `antibiotic` | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds) |
| 61 | `kdigo_stages` | organfailure | `icustays` | `crrt`, `kdigo_creatinine`, `kdigo_uo` | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds) |
| 62 | `first_day_sofa` | firstday | `icustays` | `bg`, `dobutamine`, `dopamine`, `epinephrine`, `first_day_gcs`, `first_day_lab`, `first_day_urine_output`, `first_day_vitalsign`, `norepinephrine`, `ventilation` | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds) |
| 63 | `sepsis3` | sepsis | - | `sofa`, `suspicion_of_infection` | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds) |
| 64 | `vasoactive_agent` | medication | - | `dobutamine`, `dopamine`, `epinephrine`, `milrinone`, `norepinephrine`, `phenylephrine`, `vasopressin` | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds) |
| 65 | `norepinephrine_equivalent_dose` | medication | - | `vasoactive_agent` | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds) |
<!-- concepts:end -->
