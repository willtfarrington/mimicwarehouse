# mimic-code concepts — inventory, order and status on DuckDB 1.5.5 (EP-37 / EP-38)

The 65 `concepts_duckdb` files of MIT-LCP/mimic-code (MIT; D-19), vendored by EP-8 at
upstream commit `8bcbd190ca75670cd5281f9ead3611ae1cefb73e` (upstream `main` of
2026-08-10; `src/mimicwarehouse/concepts/vendor/VENDOR.json` is the pin), as the EP-37
concept runner executes them per tier into `mimiciv_derived` — with EP-38's patch registry
(`src/mimicwarehouse/concepts/patches/`, § Deviations) replacing five of them by ports of
open upstream fixes. This page is the human-readable twin of the generated inventory
`src/mimicwarehouse/concepts/concepts.yaml` (`uv run python -m
mimicwarehouse.concepts.inventory` regenerates it and the DAG spec
`dag/specs/concepts.yaml`; `tests/ep/test_ep37.py` drift-tests both and the inventory
table below) and of the patch registry (`uv run python -m mimicwarehouse.concepts.patching
--check | --table`; `tests/ep/test_ep38.py` drift-tests the deviations table). Nothing on
this page is derived from credentialed data: names, hashes, dependencies, statuses, and
the ODbL demo tier's committed count-pins only.

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
  `meta.concept_versions` (one row per attempted concept: upstream commit, the sha256 of
  the SQL that ran, the patch id when a patch ran (EP-38), rows, built_at, run/build ids,
  the derived snapshot id, status, error class).
- **Patches (EP-38)** — a validated entry in `patches/patches.yaml` makes the runner execute
  `patches/<concept>.sql` instead of the vendored file (§ Deviations); the registry is
  checked once per build and any mismatch with the vendored pin refuses every concept
  step. A patched rebuild is `mwh build --tier <t> --select <list> --force` with the list
  from `python -m mimicwarehouse.concepts.patching --select-list` (the patched concepts,
  every concept that reads them, `meta.concept_versions`, `catalog`).
- **Count-pins** — `tests/ep/pins/concepts_demo.json` (committed; ODbL demo, cells below
  11 stored as `"<11"`) and `<data_root>/runs/pins/concepts_dev.json` (never committed;
  written on the first dev run, compared on later ones). `mimicwarehouse.concepts.pins`
  reads every number through `safe_query`; since EP-38 a pin carries the patch map beside
  the upstream commit, and `python -m mimicwarehouse.concepts.pins --tier <t> --refresh`
  re-pins after a patched rebuild, printing the before/after differences.

## Status on DuckDB 1.5.5

All **65** concepts execute cleanly on the pinned DuckDB 1.5.5 on every tier — first
measured by EP-33's D2 pre-flight smoke (driver order, throwaway demo-catalog copy),
reproduced by EP-37 on 2026-09-05/06 through the runner on the `fixture` (synthetic),
`demo` (ODbL) and `dev` tiers, and verified on `full` by EP-38 (the `concepts-full` job:
65 / 65 done, 0 failed, 0 blocked, 95,777,751 derived rows in 1,337,952,596 bytes of
Parquet; the per-group wall / peak-RSS / rows table is EP-37's completion note). The
`DuckDB 1.5.5` column of the inventory table below reads `ok` for every concept, so the
"failing concepts on 1.5.x" list this page was planned to carry is **empty**. Execution
success is not numerical correctness — the count-pins above and the upstream concept-logic
ports (§ Deviations) cover that half.

| Concept | Error class on DuckDB 1.5.5 | Note |
|---|---|---|
| *(none)* | – | 65 / 65 executed on fixture, demo and dev (EP-37, 2026-09-05) and on full (EP-38, 2026-09-06) |

Concepts that are **empty on the synthetic fixture** by construction (the vocab does not
carry their itemids) are listed in `tests/fixtures/COVERAGE.md`; they still execute.

## Deviations

Since EP-38 every local deviation from the vendored SQL is a **patch**: a full-replacement
`src/mimicwarehouse/concepts/patches/<concept>.sql` — the vendored DuckDB body with one
upstream fix applied by hand, citing the upstream PR / issue and keeping the MIT
attribution — plus an entry in `patches/patches.yaml` that pins it to the vendored commit
(`applies_to_upstream_commit`) and to the file's sha256. The vendored files themselves are
never edited (`sql_sha256` in the inventory still equals `VENDOR.json`'s `sha256_lf`). The
runner validates the registry once per build and refuses every concept step when it does
not match the pin — a re-vendor forces a review of each patch — then prefers the patch;
`meta.concept_versions.patch_id` names it and `sql_sha256` is the executed SQL's. The
patches are ports of **open** upstream PRs (`ported-unmerged`), re-checked at the P4
re-plan (EP-54 / EP-74); a patch is dropped once its PR lands in a re-vendored pin.

*Columns: patch id · concept · the upstream PR / issue ported · status · date · what the
port changes · semantics (`changes-values` on MIMIC-IV, or `intent-only`: the SQL's intent
is corrected but the rows are identical on MIMIC-IV data) · the effect on the committed
demo count-pins (`tests/ep/pins/concepts_demo.json`, ODbL; before -> after) · the patch
file's sha256 (12 hex). Rendered by `concepts.patching.render_patch_table`.*

<!-- patches:begin -->
| patch id | concept | ported from | status | date | change | semantics | effect on demo counts | patch sha256 |
|---|---|---|---|---|---|---|---|---|
| `sirs-wbc-guard-pr2146` | `sirs` | <https://github.com/MIT-LCP/mimic-code/pull/2146> | ported-unmerged | 2026-09-06 | The WBC missing-data arm tests COALESCE(wbc_min, wbc_max, bands_max) IS NULL instead of COALESCE(wbc_min, bands_max) IS NULL, so a stay whose only WBC input is a normal wbc_max scores 0 rather than NULL. first_day_lab yields wbc_min and wbc_max together, so the rows do not change on MIMIC-IV; the crafted regression test shows the guard. | intent-only | none -- 140 rows before and after (one per ICU stay) and the same wbc_score values, because first_day_lab yields wbc_min and wbc_max together | `42d8e6a3a9fd` |
| `complete_blood_count-mchc-unit-pr2141` | `complete_blood_count` | <https://github.com/MIT-LCP/mimic-code/pull/2141>, <https://github.com/MIT-LCP/mimic-code/issues/1922> | ported-unmerged | 2026-09-06 | MCHC (itemid 51249) is kept only with valueuom = 'g/dL', in the pivoted column and in the WHERE clause; upstream treats the rows recorded with '%' as mis-labelled units (issue 1922). MCHC values recorded with '%' become NULL, and a specimen whose only CBC row was such an MCHC disappears from the table; the other nine CBC items are unchanged. | changes-values | none on the row count -- 2,959 specimens before and after (no demo specimen consisted of a '%'-labelled MCHC alone); the MCHC values recorded with '%' are NULL from now on | `b653b0f41088` |
| `inflammation-crp-unit-pr2141` | `inflammation` | <https://github.com/MIT-LCP/mimic-code/pull/2141>, <https://github.com/MIT-LCP/mimic-code/issues/1922> | ported-unmerged | 2026-09-06 | CRP (itemid 50889) is kept only with valueuom = 'mg/L' -- rows with a missing or other unit are excluded, in the pivoted column and in the WHERE clause (the second half of the same upstream PR). | changes-values | none -- 42 specimens before and after (every demo CRP row carries mg/L); on the dev tier fewer than 11 specimens (CRP rows without a unit) drop out | `e6c42a753003` |
| `charlson-c4a-exclusion-pr2142` | `charlson` | <https://github.com/MIT-LCP/mimic-code/pull/2142>, <https://github.com/MIT-LCP/mimic-code/issues/2017>, <https://github.com/MIT-LCP/mimic-code/pull/2043> | ported-unmerged | 2026-09-06 | The ICD-10 malignant_cancer range C45-C58 is split into C45-C49 and C50-C58 so the ICD-10-CM extension C4A (Merkel cell carcinoma), which sorts inside the single range, is excluded as a skin malignancy per Quan et al. (2005); C7A / C7B stay unmapped (upstream's documented, intentional omission -- issue 2017; the wider PR 2043 that maps them is not ported). | changes-values | none -- 275 admissions before and after and the mean index 4.66 unchanged (no demo admission carries a C4A code) | `d0c026b9afdc` |
| `apsiii-equidistant-arms-pr2137` | `apsiii` | <https://github.com/MIT-LCP/mimic-code/pull/2137> | ported-unmerged | 2026-09-06 | The "equidistant from normal -- pick the larger score" arms for respiratory rate, hematocrit, WBC, sodium, albumin and glucose compared ABS(x_max - mid) with itself; they now compare it with ABS(x_min - mid) like the heart-rate, MBP and temperature arms (Knaus et al. 1991). The tautology was only reached once the two distances were equal, so the rows do not change on MIMIC-IV; the crafted boundary test pins the rule. The second open APS III PR (2046, axillary temperature +1 C) is a further semantic change and is not ported. | intent-only | none -- 140 rows before and after with identical scores (the corrected arms are only reached once the two distances are equal) | `e1b3ccf2d603` |
<!-- patches:end -->

**Evaluated, not ported** (re-check at EP-54 / EP-74): upstream PR #2046 (APS III: + 1 °C
for axillary temperatures, first-day temperature re-sourced from `vitalsign` with its
site) — a further semantic change without maintainer acceptance (`mergeable_state`
unknown as of 2026-08-17); PR #2043 (Charlson: map the ICD-10-CM extensions C7A / C7B) —
superseded by the narrower #2142 and upstream's documented intent to leave C7A / C7B
unmapped; the `valueuom` filters the EP-38 brief expected for `chemistry`,
`blood_differential`, `enzyme` and `bg` — no upstream fix exists, every itemid of those
panels carries a single unit on the dev and full tiers (rare variants below the small-cell
threshold aside), and EP-39's brief reserves unit rules for `meta.item_units`. Effects
beyond the demo pins: on the dev tier the SIRS, APS III and Charlson tables are unchanged
row for row (the audited fingerprint queries are cited in EP-38's completion note); the
MCHC filter nulls the `mchc` of every CBC specimen whose MCHC was recorded with `%` — a
large share of MCHC rows on every credentialed tier; the CRP filter drops fewer than 11
specimens on dev.

## Inventory (generated; execution order)

*Columns: execution order · concept · upstream group · the concepts it reads
(`mimiciv_derived.*`) · the core tables it reads · the first 12 hex of the vendored
file's sha256 · status on DuckDB 1.5.5 (`ok` = executes on every tier) · the EP-38 patch
id (`-` = the vendored file runs unmodified). Rendered by
`concepts.inventory.render_inventory_table`.*

<!-- concepts:begin -->
| # | concept | group | reads (concepts) | sources (core tables) | sql sha256 | DuckDB 1.5.5 | patch |
|---:|---|---|---|---|---|---|---|
| 1 | `icustay_times` | demographics | - | `mimiciv_icu.chartevents`, `mimiciv_icu.icustays` | `8d13e38fac6c` | ok | - |
| 2 | `icustay_hourly` | demographics | `icustay_times` | - | `81455a58f9a4` | ok | - |
| 3 | `weight_durations` | demographics | - | `mimiciv_icu.chartevents`, `mimiciv_icu.icustays` | `b9c4cf351b3b` | ok | - |
| 4 | `urine_output` | measurement | - | `mimiciv_icu.outputevents` | `9b9c5fd0fa1a` | ok | - |
| 5 | `kdigo_uo` | organfailure | `urine_output`, `weight_durations` | `mimiciv_icu.icustays` | `e3dcc870ff90` | ok | - |
| 6 | `age` | demographics | - | `mimiciv_hosp.admissions`, `mimiciv_hosp.patients` | `39666414728a` | ok | - |
| 7 | `icustay_detail` | demographics | - | `mimiciv_hosp.admissions`, `mimiciv_hosp.patients`, `mimiciv_icu.icustays` | `6788ef812fc0` | ok | - |
| 8 | `bg` | measurement | - | `mimiciv_hosp.labevents`, `mimiciv_icu.chartevents` | `4b577c08f5c7` | ok | - |
| 9 | `blood_differential` | measurement | - | `mimiciv_hosp.labevents` | `8ba701be5d2d` | ok | - |
| 10 | `cardiac_marker` | measurement | - | `mimiciv_hosp.labevents` | `a878d5200a40` | ok | - |
| 11 | `chemistry` | measurement | - | `mimiciv_hosp.labevents` | `b3702e5b85ec` | ok | - |
| 12 | `coagulation` | measurement | - | `mimiciv_hosp.labevents` | `cfba9feeb261` | ok | - |
| 13 | `complete_blood_count` | measurement | - | `mimiciv_hosp.labevents` | `d5de5c0f8df5` | ok | `complete_blood_count-mchc-unit-pr2141` |
| 14 | `creatinine_baseline` | measurement | `age`, `chemistry` | `mimiciv_hosp.diagnoses_icd`, `mimiciv_hosp.patients` | `0a9b8e3d8c87` | ok | - |
| 15 | `enzyme` | measurement | - | `mimiciv_hosp.labevents` | `19372275662b` | ok | - |
| 16 | `gcs` | measurement | - | `mimiciv_icu.chartevents` | `ebea0def4a33` | ok | - |
| 17 | `height` | measurement | - | `mimiciv_icu.chartevents` | `dd1ac1b2a77b` | ok | - |
| 18 | `icp` | measurement | - | `mimiciv_icu.chartevents` | `c81861c28ada` | ok | - |
| 19 | `inflammation` | measurement | - | `mimiciv_hosp.labevents` | `25ac5ba3e158` | ok | `inflammation-crp-unit-pr2141` |
| 20 | `oxygen_delivery` | measurement | - | `mimiciv_icu.chartevents` | `1711e59b7fee` | ok | - |
| 21 | `rhythm` | measurement | - | `mimiciv_icu.chartevents` | `0ae68e3beeed` | ok | - |
| 22 | `urine_output_rate` | measurement | `urine_output`, `weight_durations` | `mimiciv_icu.chartevents`, `mimiciv_icu.icustays` | `70a70f6014ef` | ok | - |
| 23 | `ventilator_setting` | measurement | - | `mimiciv_icu.chartevents` | `9528bc6180a9` | ok | - |
| 24 | `vitalsign` | measurement | - | `mimiciv_icu.chartevents` | `0d28672f3a10` | ok | - |
| 25 | `charlson` | comorbidity | `age` | `mimiciv_hosp.admissions`, `mimiciv_hosp.diagnoses_icd` | `72e141d2e550` | ok | `charlson-c4a-exclusion-pr2142` |
| 26 | `acei` | medication | - | `mimiciv_hosp.prescriptions` | `332df4deccfb` | ok | - |
| 27 | `antibiotic` | medication | - | `mimiciv_hosp.prescriptions`, `mimiciv_icu.icustays` | `1b2f97d6069c` | ok | - |
| 28 | `arb` | medication | - | `mimiciv_hosp.prescriptions` | `86839c0ec6e3` | ok | - |
| 29 | `dobutamine` | medication | - | `mimiciv_icu.inputevents` | `f514b7e2ade3` | ok | - |
| 30 | `dopamine` | medication | - | `mimiciv_icu.inputevents` | `75284fe0209c` | ok | - |
| 31 | `epinephrine` | medication | - | `mimiciv_icu.inputevents` | `b021cd639961` | ok | - |
| 32 | `milrinone` | medication | - | `mimiciv_icu.inputevents` | `6dd9724146fd` | ok | - |
| 33 | `neuroblock` | medication | - | `mimiciv_icu.inputevents` | `19a8236d1587` | ok | - |
| 34 | `norepinephrine` | medication | - | `mimiciv_icu.inputevents` | `4085001f87dc` | ok | - |
| 35 | `nsaid` | medication | - | `mimiciv_hosp.prescriptions` | `53f882d04023` | ok | - |
| 36 | `phenylephrine` | medication | - | `mimiciv_icu.inputevents` | `595d8b41195d` | ok | - |
| 37 | `vasopressin` | medication | - | `mimiciv_icu.inputevents` | `d722d44250b3` | ok | - |
| 38 | `code_status` | treatment | - | `mimiciv_hosp.poe`, `mimiciv_hosp.poe_detail`, `mimiciv_icu.chartevents`, `mimiciv_icu.icustays` | `1f01018aa2a7` | ok | - |
| 39 | `crrt` | treatment | - | `mimiciv_icu.chartevents` | `89554b57063e` | ok | - |
| 40 | `invasive_line` | treatment | - | `mimiciv_icu.d_items`, `mimiciv_icu.procedureevents` | `00d323a4fe0e` | ok | - |
| 41 | `rrt` | treatment | - | `mimiciv_icu.chartevents`, `mimiciv_icu.inputevents`, `mimiciv_icu.procedureevents` | `976b9228053f` | ok | - |
| 42 | `ventilation` | treatment | `oxygen_delivery`, `ventilator_setting` | - | `2100128a7e26` | ok | - |
| 43 | `first_day_bg` | firstday | `bg` | `mimiciv_icu.icustays` | `08d751ae20a8` | ok | - |
| 44 | `first_day_bg_art` | firstday | `bg` | `mimiciv_icu.icustays` | `f02c45c374a4` | ok | - |
| 45 | `first_day_gcs` | firstday | `gcs` | `mimiciv_icu.icustays` | `43810b889dee` | ok | - |
| 46 | `first_day_height` | firstday | `height` | `mimiciv_icu.icustays` | `7f6392ba9ed1` | ok | - |
| 47 | `first_day_lab` | firstday | `blood_differential`, `chemistry`, `coagulation`, `complete_blood_count`, `enzyme` | `mimiciv_icu.icustays` | `1166d567e377` | ok | - |
| 48 | `first_day_rrt` | firstday | `rrt` | `mimiciv_icu.icustays` | `acf320bba173` | ok | - |
| 49 | `first_day_urine_output` | firstday | `urine_output` | `mimiciv_icu.icustays` | `6d46386e04fa` | ok | - |
| 50 | `first_day_vitalsign` | firstday | `vitalsign` | `mimiciv_icu.icustays` | `432b55a76286` | ok | - |
| 51 | `first_day_weight` | firstday | `weight_durations` | `mimiciv_icu.icustays` | `08ce1798ca2d` | ok | - |
| 52 | `kdigo_creatinine` | organfailure | - | `mimiciv_hosp.labevents`, `mimiciv_icu.icustays` | `fc97bc3e7a94` | ok | - |
| 53 | `meld` | organfailure | `first_day_lab`, `first_day_rrt` | `mimiciv_icu.icustays` | `52897caf761a` | ok | - |
| 54 | `apsiii` | score | `bg`, `first_day_gcs`, `first_day_lab`, `first_day_urine_output`, `first_day_vitalsign`, `ventilation` | `mimiciv_hosp.admissions`, `mimiciv_hosp.diagnoses_icd`, `mimiciv_hosp.patients`, `mimiciv_icu.icustays` | `b6a31eed3635` | ok | `apsiii-equidistant-arms-pr2137` |
| 55 | `lods` | score | `bg`, `first_day_gcs`, `first_day_lab`, `first_day_urine_output`, `first_day_vitalsign`, `ventilation` | `mimiciv_hosp.admissions`, `mimiciv_hosp.patients`, `mimiciv_icu.chartevents`, `mimiciv_icu.icustays` | `98c3668cbbf8` | ok | - |
| 56 | `oasis` | score | `age`, `first_day_gcs`, `first_day_urine_output`, `first_day_vitalsign`, `ventilation` | `mimiciv_hosp.admissions`, `mimiciv_hosp.patients`, `mimiciv_hosp.services`, `mimiciv_icu.icustays` | `67dd3c13b6c7` | ok | - |
| 57 | `sapsii` | score | `age`, `bg`, `chemistry`, `complete_blood_count`, `enzyme`, `gcs`, `urine_output`, `ventilation`, `vitalsign` | `mimiciv_hosp.admissions`, `mimiciv_hosp.diagnoses_icd`, `mimiciv_hosp.services`, `mimiciv_icu.chartevents`, `mimiciv_icu.icustays` | `af07e7d0ea85` | ok | - |
| 58 | `sirs` | score | `first_day_bg_art`, `first_day_lab`, `first_day_vitalsign` | `mimiciv_icu.icustays` | `56254d4b480b` | ok | `sirs-wbc-guard-pr2146` |
| 59 | `sofa` | score | `bg`, `chemistry`, `complete_blood_count`, `dobutamine`, `dopamine`, `enzyme`, `epinephrine`, `gcs`, `icustay_hourly`, `norepinephrine`, `urine_output_rate`, `ventilation`, `vitalsign` | `mimiciv_icu.icustays` | `35543af4d557` | ok | - |
| 60 | `suspicion_of_infection` | sepsis | `antibiotic` | `mimiciv_hosp.microbiologyevents` | `4f0990791396` | ok | - |
| 61 | `kdigo_stages` | organfailure | `crrt`, `kdigo_creatinine`, `kdigo_uo` | `mimiciv_icu.icustays` | `c441af84a639` | ok | - |
| 62 | `first_day_sofa` | firstday | `bg`, `dobutamine`, `dopamine`, `epinephrine`, `first_day_gcs`, `first_day_lab`, `first_day_urine_output`, `first_day_vitalsign`, `norepinephrine`, `ventilation` | `mimiciv_icu.icustays` | `81b5afffa263` | ok | - |
| 63 | `sepsis3` | sepsis | `sofa`, `suspicion_of_infection` | - | `3ba6b0f7008f` | ok | - |
| 64 | `vasoactive_agent` | medication | `dobutamine`, `dopamine`, `epinephrine`, `milrinone`, `norepinephrine`, `phenylephrine`, `vasopressin` | - | `597eda757be5` | ok | - |
| 65 | `norepinephrine_equivalent_dose` | medication | `vasoactive_agent` | - | `c17bf81d2fae` | ok | - |
<!-- concepts:end -->

*Sources: MIT-LCP/mimic-code at the pinned commit (`NOTICE` carries the attribution and
the JAMIA 2018 citation); EP-33 `retro-p2.md` § D2 for the smoke method; EP-37's
completion note (`roadmap/EP-37-concept-runner.md`) for the per-tier run ids and the
full-run table; EP-38's completion note (`roadmap/EP-38-concept-fixes.md`) for the patched
rebuilds and the pin before/after records; the upstream PRs linked in the deviations
table.*
