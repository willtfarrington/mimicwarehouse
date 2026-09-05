# mimic-code concepts — inventory, status on DuckDB 1.5.x and local patches (EP-37, EP-38)

The human-readable inventory of the vendored MIT-LCP/mimic-code `concepts_duckdb` tree
(MIT; attribution in the repo-root `NOTICE`, D-19) as the warehouse runs it into
`mimiciv_derived`, and the register of every local deviation from it (§ Deviations). The
table between the markers is **generated** from
`src/mimicwarehouse/concepts/concepts.yaml` by `mimicwarehouse.concepts.inventory`
(`uv run python -m mimicwarehouse.concepts.inventory` regenerates the inventory, the DAG
spec `dag/specs/concepts.yaml` and this table; `tests/ep/test_ep37.py` pins all three to a
fresh scan of the vendor tree; `tests/ep/test_ep38.py` pins the patch registry and this
page's deviations table). Nothing on this page is derived from credentialed data: it is
file metadata, graph structure, the executability verdict and the count effect of each
patch on the ODbL **demo** tier (the committed `tests/ep/pins/concepts_demo.json`).

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
- **Full tier** runs as the background job `concepts-full` (EP-37 completion note; verified
  by EP-38: 65/65, 12 min 18 s, 1,338.0 MB of derived Parquet — see the EP-37 brief's
  second completion note); the patched subset was rebuilt on every tier by EP-38 (job
  `concepts-full-patched` on full).
- **Patches** (EP-38): a concept named in `src/mimicwarehouse/concepts/patches/patches.yaml`
  is built from `patches/<concept>.sql` instead of the vendored file — see § Deviations.

## Status on DuckDB 1.5.x

All vendored files execute cleanly on the pinned DuckDB 1.5.5: the EP-33 D2 pre-flight
smoke (65/65 in driver order, throwaway demo-catalog copy), EP-37's demo- and dev-tier
builds and the full-tier job through the runner. **Concepts failing on 1.5.x: none** at
the pin `8bcbd190ca75…` — the "status" column below comes from
`mimicwarehouse.concepts.inventory.KNOWN_FAILURES`, which EP-38 left **empty** (no
DuckDB 1.5.x breakage to record; the brief's "function renames / integer division /
`regexp_matches` / epoch" class did not occur). Execution success is not numerical
correctness: the open upstream concept-logic PRs (SIRS wbc guard, lab `valueuom`,
Charlson, APS-III; roadmap Risk 2) were ported by EP-38 as the patches below, and a
patched concept's status cell names its patch. Some concepts are empty or partial on the
synthetic fixture by design (`tests/fixtures/COVERAGE.md`).

## Deviations (EP-38 patches over the vendored tree)

The vendored files are never edited (D-19, `NOTICE`). A local deviation is a **patch**:
`src/mimicwarehouse/concepts/patches/patches.yaml` registers it (`patch_id`, `concept`,
`reason`, `upstream_ref` — the PR / issue / commit URL it follows —
`applies_to_upstream_commit`, `sql_sha256` of the patch file, `date`, `status`,
`semantics`) and `patches/<concept>.sql` is the full replacement file in the vendored shape
(a header comment citing the patch, the upstream path + commit, the MIT attribution and the
upstream reference; upstream's own header line kept verbatim; then the SELECT body). The
runner (`concepts.runner`) validates the whole registry once per build before the first
concept runs and **refuses to start** when an entry's `applies_to_upstream_commit` is not
the vendored pin (a re-vendor forces a review of every patch), when a patch file's sha256
differs from the registered one, or when a patch is byte-identical to the vendored body;
otherwise it prefers the patch, records the patch file's sha256 as the table's
`source_sha256` / `sql_sha256` and the `patch_id` in `status.json` and in
`meta.concept_versions` (`mwh sql "SELECT count(*) AS n FROM meta.concept_versions WHERE
patch_id IS NULL" --tier dev` = 60 of 65 — the patched count itself, 5, is a small cell
the gate suppresses, so read the complement). `status: ported-unmerged` marks a port of an
upstream PR that was still **open** at porting time; the P4 re-plan (EP-54) re-checks each
and flips it to `ported-merged` (or re-vendors). `uv run python -m
mimicwarehouse.concepts.patches` refreshes the sha256s and validates.

| patch_id | concept | upstream reference | status | semantics | effect on demo counts (before -> after) |
|---|---|---|---|---|---|
| `sirs-wbc-guard` | `sirs` | [PR #2146](https://github.com/MIT-LCP/mimic-code/pull/2146) — include `wbc_max` in the SIRS WBC missing-data guard | ported-unmerged | unchanged: `first_day_lab` derives `wbc_min`/`wbc_max` from one column, so they are null together and no real-data component can change; the crafted regression case (null `wbc_min`, normal `wbc_max` -> `wbc_score` 0, was NULL) is the reachable-in-principle input | rows 140 -> 140 |
| `cbc-mchc-valueuom` | `complete_blood_count` | [PR #2141](https://github.com/MIT-LCP/mimic-code/pull/2141) (issue #1922) — keep MCHC (itemid 51249) only when `valueuom = 'g/dL'`; a 51249 row in another unit no longer qualifies its specimen | ported-unmerged | changed | rows 2,959 -> 2,959 (no demo specimen carries a mis-united MCHC) |
| `inflammation-crp-valueuom` | `inflammation` | [PR #2141](https://github.com/MIT-LCP/mimic-code/pull/2141) (issue #1922) — keep CRP (itemid 50889) only when `valueuom = 'mg/L'`; rows without a unit are excluded | ported-unmerged | changed | rows 42 -> 42 |
| `charlson-exclude-c4a` | `charlson` | [PR #2142](https://github.com/MIT-LCP/mimic-code/pull/2142) — split `BETWEEN 'C45' AND 'C58'` into C45–C49 and C50–C58 so ICD-10-CM C4A (Merkel cell carcinoma, a skin malignancy Quan et al. 2005 exclude) no longer counts as `malignant_cancer` | ported-unmerged | changed | rows 275 -> 275; mean index 4.66 -> 4.66 (no demo admission is coded C4A) |
| `apsiii-equidistant-arms` | `apsiii` | [PR #2137](https://github.com/MIT-LCP/mimic-code/pull/2137) — the six "values are equidistant" arms (resp_rate, hematocrit, wbc, sodium, albumin, glucose) compare the max arm to the **min** arm instead of to itself | ported-unmerged | unchanged: the arms are only reached when the two distances are equal, where both predicates hold, so every score is identical; ported for intent and upstream parity | rows 140 -> 140 |

The committed demo pin set (`tests/ep/pins/concepts_demo.json`) therefore did **not**
change at EP-38: `compare_pins` reported zero differences after the patched rebuild, and
the dev drift detector's differences are recorded in the EP-38 completion note.

**Considered, not ported** (re-check at the P4 re-plan, EP-54):

- [PR #2043](https://github.com/MIT-LCP/mimic-code/pull/2043) — the alternative Charlson
  change: excludes C4A *and* maps C7A (malignant neuroendocrine tumours) into
  `malignant_cancer` and C7B (secondary neuroendocrine tumours) into
  `metastatic_solid_tumor`. Both PRs were open at the pin; `charlson-exclude-c4a` follows
  PR #2142 because it is the Quan-faithful minimum (C7A/C7B are ICD-10-CM extensions
  Quan 2005 never defined) and the newer of the two. Switching to PR #2043 later is a
  one-file patch replacement plus a registry entry update.
- [PR #2046](https://github.com/MIT-LCP/mimic-code/pull/2046) — APS-III axillary
  temperature +1 °C: re-aggregates the first-day temperature from `vitalsign` with a site
  adjustment, i.e. it changes an *input* of `apsiii` rather than its scoring; deferred
  with EP-39's unit/site harmonization, where `vitalsign.temperature_site` is curated.
- The brief's wider lab-`valueuom` list (`chemistry`, `blood_differential`, `enzyme`, `bg`)
  has **no** upstream unit fix at the pin — PR #2141 touches MCHC and CRP only. Per-itemid
  expected units for every lab panel are EP-39's `meta.item_units`; EP-38 does not
  duplicate them as patches.

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
| 13 | `complete_blood_count` | measurement | `labevents` | - | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds); patched: `cbc-mchc-valueuom (PR #2141)` (EP-38) |
| 14 | `creatinine_baseline` | measurement | `diagnoses_icd`, `patients` | `age`, `chemistry` | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds) |
| 15 | `enzyme` | measurement | `labevents` | - | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds) |
| 16 | `gcs` | measurement | `chartevents` | - | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds) |
| 17 | `height` | measurement | `chartevents` | - | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds) |
| 18 | `icp` | measurement | `chartevents` | - | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds) |
| 19 | `inflammation` | measurement | `labevents` | - | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds); patched: `inflammation-crp-valueuom (PR #2141)` (EP-38) |
| 20 | `oxygen_delivery` | measurement | `chartevents` | - | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds) |
| 21 | `rhythm` | measurement | `chartevents` | - | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds) |
| 22 | `urine_output_rate` | measurement | `chartevents`, `icustays` | `urine_output`, `weight_durations` | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds) |
| 23 | `ventilator_setting` | measurement | `chartevents` | - | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds) |
| 24 | `vitalsign` | measurement | `chartevents` | - | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds) |
| 25 | `charlson` | comorbidity | `admissions`, `diagnoses_icd` | `age` | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds); patched: `charlson-exclude-c4a (PR #2142)` (EP-38) |
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
| 54 | `apsiii` | score | `admissions`, `diagnoses_icd`, `patients`, `icustays` | `bg`, `first_day_gcs`, `first_day_lab`, `first_day_urine_output`, `first_day_vitalsign`, `ventilation` | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds); patched: `apsiii-equidistant-arms (PR #2137)` (EP-38) |
| 55 | `lods` | score | `admissions`, `patients`, `chartevents`, `icustays` | `bg`, `first_day_gcs`, `first_day_lab`, `first_day_urine_output`, `first_day_vitalsign`, `ventilation` | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds) |
| 56 | `oasis` | score | `admissions`, `patients`, `services`, `icustays` | `age`, `first_day_gcs`, `first_day_urine_output`, `first_day_vitalsign`, `ventilation` | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds) |
| 57 | `sapsii` | score | `admissions`, `diagnoses_icd`, `services`, `chartevents`, `icustays` | `age`, `bg`, `chemistry`, `complete_blood_count`, `enzyme`, `gcs`, `urine_output`, `ventilation`, `vitalsign` | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds) |
| 58 | `sirs` | score | `icustays` | `first_day_bg_art`, `first_day_lab`, `first_day_vitalsign` | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds); patched: `sirs-wbc-guard (PR #2146)` (EP-38) |
| 59 | `sofa` | score | `icustays` | `bg`, `chemistry`, `complete_blood_count`, `dobutamine`, `dopamine`, `enzyme`, `epinephrine`, `gcs`, `icustay_hourly`, `norepinephrine`, `urine_output_rate`, `ventilation`, `vitalsign` | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds) |
| 60 | `suspicion_of_infection` | sepsis | `microbiologyevents` | `antibiotic` | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds) |
| 61 | `kdigo_stages` | organfailure | `icustays` | `crrt`, `kdigo_creatinine`, `kdigo_uo` | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds) |
| 62 | `first_day_sofa` | firstday | `icustays` | `bg`, `dobutamine`, `dopamine`, `epinephrine`, `first_day_gcs`, `first_day_lab`, `first_day_urine_output`, `first_day_vitalsign`, `norepinephrine`, `ventilation` | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds) |
| 63 | `sepsis3` | sepsis | - | `sofa`, `suspicion_of_infection` | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds) |
| 64 | `vasoactive_agent` | medication | - | `dobutamine`, `dopamine`, `epinephrine`, `milrinone`, `norepinephrine`, `phenylephrine`, `vasopressin` | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds) |
| 65 | `norepinephrine_equivalent_dose` | medication | - | `vasoactive_agent` | `8bcbd190ca75` | executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds) |
<!-- concepts:end -->
