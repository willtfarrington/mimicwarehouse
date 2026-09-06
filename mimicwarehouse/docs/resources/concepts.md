# mimic-code concepts — inventory, order and status on DuckDB 1.5.5 (EP-37)

The 65 `concepts_duckdb` files of MIT-LCP/mimic-code (MIT; D-19), vendored by EP-8 at
upstream commit `8bcbd190ca75670cd5281f9ead3611ae1cefb73e` (upstream `main` of
2026-08-10; `src/mimicwarehouse/concepts/vendor/VENDOR.json` is the pin), as the EP-37
concept runner executes them per tier into `mimiciv_derived`. This page is the
human-readable twin of the generated inventory `src/mimicwarehouse/concepts/concepts.yaml`
(`uv run python -m mimicwarehouse.concepts.inventory` regenerates it and the DAG spec
`dag/specs/concepts.yaml`; `tests/ep/test_ep37.py` drift-tests both and the table below).
Nothing on this page is derived from data: names, hashes, dependencies and statuses only.

## How the runner executes them

- **One DAG step per concept** — `concept.<group>.<name>` (`mwh build --tier <t> --tag
  concepts`, or `--select concept.<group>.<name> [--with-deps]`; `--keep-going` records a
  failure and continues with the steps that do not depend on it). Each step strips
  upstream's `DROP TABLE … ; CREATE TABLE mimiciv_derived.<x> AS` header and runs the
  SELECT body on the build connection against views over the tier's staged lake (the dev
  tier therefore sees its five subject buckets only), sinks **one** ZSTD Parquet file to
  `<lake_root(tier)>/derived/<tier>/mimiciv_derived/<name>/part-0.parquet`, appends a
  manifest line (`source_sha256` = the SQL's sha256, `raw_snapshot_id` = the tier's core
  snapshot id) and a per-tier `status.json` entry, and writes one `kind: concept` line to
  the benchmark ledger (`mwh runs benchmarks --kind concept --tier <t>`).
- **Order** — topological over the `mimiciv_derived.<x>` references each file makes
  (column *reads* below); ties follow upstream's `duckdb.sql` driver order. Upstream's
  driver places `kdigo_uo` fifth because `urine_output_rate` and `kdigo_stages` need it,
  and the tail (`kdigo_stages`, `first_day_sofa`, `sepsis3`, `vasoactive_agent`,
  `norepinephrine_equivalent_dose`) last — the scan reproduces that.
- **Catalog** — the shared `catalog` step ends every concept build and registers each
  complete derived table as a `mimiciv_derived.<name>` view (the discovery walker
  `concepts.runner.register_derived`, an EP-34 `CATALOG_EXTENSIONS` entry) beside
  `meta.concept_versions` (one row per attempted concept: upstream commit, sql sha256,
  patch id — NULL until EP-38 —, rows, built_at, run/build ids, the derived snapshot id,
  status, error class).
- **Count-pins** — `tests/ep/pins/concepts_demo.json` (committed; ODbL demo, cells below
  11 stored as `"<11"`) and `<data_root>/runs/pins/concepts_dev.json` (never committed;
  written on the first dev run, compared on later ones). `mimicwarehouse.concepts.pins`
  reads every number through `safe_query`.

## Status on DuckDB 1.5.5

All **65** concepts execute cleanly on the pinned DuckDB 1.5.5 — first measured by EP-33's
D2 pre-flight smoke (driver order, throwaway demo-catalog copy), reproduced by EP-37 on
2026-09-05/06 through the runner on the `fixture` (synthetic), `demo` (ODbL) and `dev`
tiers, and launched on `full` as the `concepts-full` background job that EP-38 verifies.
The "failing concepts on 1.5.x" list this page was planned to carry is therefore
**empty**; execution success is not numerical correctness — the count-pins above and the
open upstream concept-logic PRs (SIRS `wbc` guard, lab `valueuom` filters, Charlson,
APS-III; roadmap Risk 2's open half) are EP-38's charter.

| Concept | Error class on DuckDB 1.5.5 | Note |
|---|---|---|
| *(none)* | – | 65 / 65 executed on fixture, demo and dev (EP-37, 2026-09-05) |

Concepts that are **empty on the synthetic fixture** by construction (the vocab does not
carry their itemids) are listed in `tests/fixtures/COVERAGE.md`; they still execute.

## Deviations

None in EP-37: the vendored files run unmodified (`sql_sha256` in the inventory equals
`VENDOR.json`'s `sha256_lf`, and the runner refuses a file whose bytes drifted). EP-38's
patch mechanism (`patches/patches.yaml`, `patch_id` in `meta.concept_versions`) records
every local deviation with its upstream reference and its effect on the demo counts here.

## Inventory (generated; execution order)

*Columns: execution order · concept · upstream group · the concepts it reads
(`mimiciv_derived.*`) · the core tables it reads · the first 12 hex of the vendored
file's sha256. Rendered by `concepts.inventory.render_inventory_table`.*

<!-- concepts:begin -->
| # | concept | group | reads (concepts) | sources (core tables) | sql sha256 |
|---:|---|---|---|---|---|
| 1 | `icustay_times` | demographics | - | `mimiciv_icu.chartevents`, `mimiciv_icu.icustays` | `8d13e38fac6c` |
| 2 | `icustay_hourly` | demographics | `icustay_times` | - | `81455a58f9a4` |
| 3 | `weight_durations` | demographics | - | `mimiciv_icu.chartevents`, `mimiciv_icu.icustays` | `b9c4cf351b3b` |
| 4 | `urine_output` | measurement | - | `mimiciv_icu.outputevents` | `9b9c5fd0fa1a` |
| 5 | `kdigo_uo` | organfailure | `urine_output`, `weight_durations` | `mimiciv_icu.icustays` | `e3dcc870ff90` |
| 6 | `age` | demographics | - | `mimiciv_hosp.admissions`, `mimiciv_hosp.patients` | `39666414728a` |
| 7 | `icustay_detail` | demographics | - | `mimiciv_hosp.admissions`, `mimiciv_hosp.patients`, `mimiciv_icu.icustays` | `6788ef812fc0` |
| 8 | `bg` | measurement | - | `mimiciv_hosp.labevents`, `mimiciv_icu.chartevents` | `4b577c08f5c7` |
| 9 | `blood_differential` | measurement | - | `mimiciv_hosp.labevents` | `8ba701be5d2d` |
| 10 | `cardiac_marker` | measurement | - | `mimiciv_hosp.labevents` | `a878d5200a40` |
| 11 | `chemistry` | measurement | - | `mimiciv_hosp.labevents` | `b3702e5b85ec` |
| 12 | `coagulation` | measurement | - | `mimiciv_hosp.labevents` | `cfba9feeb261` |
| 13 | `complete_blood_count` | measurement | - | `mimiciv_hosp.labevents` | `d5de5c0f8df5` |
| 14 | `creatinine_baseline` | measurement | `age`, `chemistry` | `mimiciv_hosp.diagnoses_icd`, `mimiciv_hosp.patients` | `0a9b8e3d8c87` |
| 15 | `enzyme` | measurement | - | `mimiciv_hosp.labevents` | `19372275662b` |
| 16 | `gcs` | measurement | - | `mimiciv_icu.chartevents` | `ebea0def4a33` |
| 17 | `height` | measurement | - | `mimiciv_icu.chartevents` | `dd1ac1b2a77b` |
| 18 | `icp` | measurement | - | `mimiciv_icu.chartevents` | `c81861c28ada` |
| 19 | `inflammation` | measurement | - | `mimiciv_hosp.labevents` | `25ac5ba3e158` |
| 20 | `oxygen_delivery` | measurement | - | `mimiciv_icu.chartevents` | `1711e59b7fee` |
| 21 | `rhythm` | measurement | - | `mimiciv_icu.chartevents` | `0ae68e3beeed` |
| 22 | `urine_output_rate` | measurement | `urine_output`, `weight_durations` | `mimiciv_icu.chartevents`, `mimiciv_icu.icustays` | `70a70f6014ef` |
| 23 | `ventilator_setting` | measurement | - | `mimiciv_icu.chartevents` | `9528bc6180a9` |
| 24 | `vitalsign` | measurement | - | `mimiciv_icu.chartevents` | `0d28672f3a10` |
| 25 | `charlson` | comorbidity | `age` | `mimiciv_hosp.admissions`, `mimiciv_hosp.diagnoses_icd` | `72e141d2e550` |
| 26 | `acei` | medication | - | `mimiciv_hosp.prescriptions` | `332df4deccfb` |
| 27 | `antibiotic` | medication | - | `mimiciv_hosp.prescriptions`, `mimiciv_icu.icustays` | `1b2f97d6069c` |
| 28 | `arb` | medication | - | `mimiciv_hosp.prescriptions` | `86839c0ec6e3` |
| 29 | `dobutamine` | medication | - | `mimiciv_icu.inputevents` | `f514b7e2ade3` |
| 30 | `dopamine` | medication | - | `mimiciv_icu.inputevents` | `75284fe0209c` |
| 31 | `epinephrine` | medication | - | `mimiciv_icu.inputevents` | `b021cd639961` |
| 32 | `milrinone` | medication | - | `mimiciv_icu.inputevents` | `6dd9724146fd` |
| 33 | `neuroblock` | medication | - | `mimiciv_icu.inputevents` | `19a8236d1587` |
| 34 | `norepinephrine` | medication | - | `mimiciv_icu.inputevents` | `4085001f87dc` |
| 35 | `nsaid` | medication | - | `mimiciv_hosp.prescriptions` | `53f882d04023` |
| 36 | `phenylephrine` | medication | - | `mimiciv_icu.inputevents` | `595d8b41195d` |
| 37 | `vasopressin` | medication | - | `mimiciv_icu.inputevents` | `d722d44250b3` |
| 38 | `code_status` | treatment | - | `mimiciv_hosp.poe`, `mimiciv_hosp.poe_detail`, `mimiciv_icu.chartevents`, `mimiciv_icu.icustays` | `1f01018aa2a7` |
| 39 | `crrt` | treatment | - | `mimiciv_icu.chartevents` | `89554b57063e` |
| 40 | `invasive_line` | treatment | - | `mimiciv_icu.d_items`, `mimiciv_icu.procedureevents` | `00d323a4fe0e` |
| 41 | `rrt` | treatment | - | `mimiciv_icu.chartevents`, `mimiciv_icu.inputevents`, `mimiciv_icu.procedureevents` | `976b9228053f` |
| 42 | `ventilation` | treatment | `oxygen_delivery`, `ventilator_setting` | - | `2100128a7e26` |
| 43 | `first_day_bg` | firstday | `bg` | `mimiciv_icu.icustays` | `08d751ae20a8` |
| 44 | `first_day_bg_art` | firstday | `bg` | `mimiciv_icu.icustays` | `f02c45c374a4` |
| 45 | `first_day_gcs` | firstday | `gcs` | `mimiciv_icu.icustays` | `43810b889dee` |
| 46 | `first_day_height` | firstday | `height` | `mimiciv_icu.icustays` | `7f6392ba9ed1` |
| 47 | `first_day_lab` | firstday | `blood_differential`, `chemistry`, `coagulation`, `complete_blood_count`, `enzyme` | `mimiciv_icu.icustays` | `1166d567e377` |
| 48 | `first_day_rrt` | firstday | `rrt` | `mimiciv_icu.icustays` | `acf320bba173` |
| 49 | `first_day_urine_output` | firstday | `urine_output` | `mimiciv_icu.icustays` | `6d46386e04fa` |
| 50 | `first_day_vitalsign` | firstday | `vitalsign` | `mimiciv_icu.icustays` | `432b55a76286` |
| 51 | `first_day_weight` | firstday | `weight_durations` | `mimiciv_icu.icustays` | `08ce1798ca2d` |
| 52 | `kdigo_creatinine` | organfailure | - | `mimiciv_hosp.labevents`, `mimiciv_icu.icustays` | `fc97bc3e7a94` |
| 53 | `meld` | organfailure | `first_day_lab`, `first_day_rrt` | `mimiciv_icu.icustays` | `52897caf761a` |
| 54 | `apsiii` | score | `bg`, `first_day_gcs`, `first_day_lab`, `first_day_urine_output`, `first_day_vitalsign`, `ventilation` | `mimiciv_hosp.admissions`, `mimiciv_hosp.diagnoses_icd`, `mimiciv_hosp.patients`, `mimiciv_icu.icustays` | `b6a31eed3635` |
| 55 | `lods` | score | `bg`, `first_day_gcs`, `first_day_lab`, `first_day_urine_output`, `first_day_vitalsign`, `ventilation` | `mimiciv_hosp.admissions`, `mimiciv_hosp.patients`, `mimiciv_icu.chartevents`, `mimiciv_icu.icustays` | `98c3668cbbf8` |
| 56 | `oasis` | score | `age`, `first_day_gcs`, `first_day_urine_output`, `first_day_vitalsign`, `ventilation` | `mimiciv_hosp.admissions`, `mimiciv_hosp.patients`, `mimiciv_hosp.services`, `mimiciv_icu.icustays` | `67dd3c13b6c7` |
| 57 | `sapsii` | score | `age`, `bg`, `chemistry`, `complete_blood_count`, `enzyme`, `gcs`, `urine_output`, `ventilation`, `vitalsign` | `mimiciv_hosp.admissions`, `mimiciv_hosp.diagnoses_icd`, `mimiciv_hosp.services`, `mimiciv_icu.chartevents`, `mimiciv_icu.icustays` | `af07e7d0ea85` |
| 58 | `sirs` | score | `first_day_bg_art`, `first_day_lab`, `first_day_vitalsign` | `mimiciv_icu.icustays` | `56254d4b480b` |
| 59 | `sofa` | score | `bg`, `chemistry`, `complete_blood_count`, `dobutamine`, `dopamine`, `enzyme`, `epinephrine`, `gcs`, `icustay_hourly`, `norepinephrine`, `urine_output_rate`, `ventilation`, `vitalsign` | `mimiciv_icu.icustays` | `35543af4d557` |
| 60 | `suspicion_of_infection` | sepsis | `antibiotic` | `mimiciv_hosp.microbiologyevents` | `4f0990791396` |
| 61 | `kdigo_stages` | organfailure | `crrt`, `kdigo_creatinine`, `kdigo_uo` | `mimiciv_icu.icustays` | `c441af84a639` |
| 62 | `first_day_sofa` | firstday | `bg`, `dobutamine`, `dopamine`, `epinephrine`, `first_day_gcs`, `first_day_lab`, `first_day_urine_output`, `first_day_vitalsign`, `norepinephrine`, `ventilation` | `mimiciv_icu.icustays` | `81b5afffa263` |
| 63 | `sepsis3` | sepsis | `sofa`, `suspicion_of_infection` | - | `3ba6b0f7008f` |
| 64 | `vasoactive_agent` | medication | `dobutamine`, `dopamine`, `epinephrine`, `milrinone`, `norepinephrine`, `phenylephrine`, `vasopressin` | - | `597eda757be5` |
| 65 | `norepinephrine_equivalent_dose` | medication | `vasoactive_agent` | - | `c17bf81d2fae` |
<!-- concepts:end -->

*Sources: MIT-LCP/mimic-code at the pinned commit (`NOTICE` carries the attribution and
the JAMIA 2018 citation); EP-33 `retro-p2.md` § D2 for the smoke method; EP-37's
completion note (`roadmap/EP-37-concept-runner.md`) for the per-tier run ids.*
