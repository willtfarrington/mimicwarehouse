# Itemid curation and unit harmonization (EP-39)

The one written answer to "which itemids are the canonical vitals and labs, what unit is
each in, and what values are implausible?" — the prose twin of
`src/mimicwarehouse/units.py` and its package data `src/mimicwarehouse/data/item_units.yaml`
(DESIGN §15). QC (EP-44), the first-day marts (EP-55) and the linkage mapping guide
(EP-138) import that module instead of re-typing itemids and factors; this page explains
the curation policy for readers and reviewers. Nothing on this page is derived from
patient data: the two tables are rendered from the packaged catalogue
(`python -m mimicwarehouse.units` re-renders the marked blocks; `test_ep39` asserts they
are in sync), and the only data-derived surface — `meta.item_unit_variants` — is
described, not reproduced. All MIMIC-IV analyses in this repository are retrospective.

## 1. Curation policy

- **Authority for itemids.** Every itemid was copied from the vendored mimic-code
  `concepts_duckdb` files named in its `source_ref` (`vitalsign`, `chemistry`,
  `complete_blood_count`, `bg`, `weight_durations`, `height`, `gcs`, `urine_output`,
  `coagulation`, `cardiac_marker`, `enzyme`), never typed from memory, and verified
  against `meta.itemids` (`d_items` + `d_labitems`) through `mwh sql` on the dev tier —
  dictionary lookups, no patient data. Three items no vendored concept reads
  (`Admission Weight (lbs.)`, `Magnesium`, `Phosphate`, plus `% Hemoglobin A1c`) carry the
  dictionary lookup as their `source_ref`.
- **One canonical unit per concept group.** Items of one `concept_group` (`vitals.hr`,
  `labs.creatinine`, `urine.output`, ...) share the canonical unit, so a mart can pool the
  serum and blood-gas glucose items, or the two temperature items, without a second
  conversion. The catalogue refuses a group whose members disagree.
- **Accepted units and normalisation.** Each item lists the unit strings it accepts and
  how each converts to the canonical unit. Strings are compared after
  `units.normalize_unit()`: whitespace removed, the degree sign and a leading `deg` /
  `degree(s)` dropped, micro signs folded to `u`, casefolded, a trailing dot removed — so
  `degF`, `deg F`, `F` and the degree-sign spelling are one key, `mm Hg` reads `mmhg`,
  `µmol/L` reads `umol/l`. The DuckDB macro `mwh_unit_norm` and the Polars twin apply the
  same steps. The key `""` stands for a **NULL or blank unit** and is accepted only where
  the itemid itself implies the unit (GCS points, INR, pH, FiO2 percent, the pounds and
  inches items, the bedside glucose items whose `d_items.unitname` is `None`).
- **Unknown units are never guessed.** A unit string the item does not accept passes the
  value through unchanged with `converted = false`; consumers filter on `converted` before
  trusting `value_canonical`. The variants table (§4) shows how often that happens.
- **Bounds are wide sanity bounds, not reference ranges,** stated in the canonical unit and
  **inclusive at both ends**. Where mimic-code filters strictly (`valuenum > 0 AND valuenum
  < 300`), the bound value itself differs only at the exact boundary; where mimic-code uses
  a garbage guard (`<= 10000`), a narrower adult sanity window is given and the curation
  note says so. A value outside its bounds is `plausible = false`; an uncurated itemid has
  no bounds and reads `plausible` NULL. Implausible-value *counts* per table are EP-44's.
- **Conversions are affine** (`canonical = value * scale + offset`) and named in
  `units.FORMULAS`; Python, Polars and SQL evaluate the same arithmetic, and every formula
  inverts exactly (`test_ep39` round-trips them within 1e-9).
- **Value-based rescaling is out of scope.** `Inspired O2 Fraction` (223835) is charted as
  a percentage, but some entries are fractions (0.21–1.0); mimic-code's `bg.sql` rescales
  them by *value* (x 100), which a unit-based rule cannot express — they read as
  implausible here and EP-55 applies upstream's value rule where it needs them.

## 2. The API

| surface | what |
|---|---|
| `units.load_catalogue()` / `units.spec(itemid)` / `units.is_curated(itemid)` / `units.curated_itemids(source)` | the validated catalogue (`ItemCatalogue` of `ItemSpec`s) |
| `units.harmonize(itemid, value, valueuom)` | scalar: `Harmonized(value_canonical, unit_canonical, converted, plausible)` |
| `units.harmonize_frame(df)` / `units.plausible_mask(df)` | the Polars twins over `itemid` / `valuenum` / `valueuom` columns (names configurable) |
| `units.bounds(itemid)` | `(plausible_low, plausible_high)` in the canonical unit — EP-44's input |
| `mwh_harmonize(itemid, value, valueuom)` | the DuckDB macro in every tier catalog, returning `STRUCT(value_canonical, unit_canonical, converted, plausible)`; helpers `mwh_unit_norm`, `mwh_unit_canonical`, `mwh_unit_known`, `mwh_value_canonical`, `mwh_plausible` |
| `mwh build --tier <t> --tag units` | the three DAG steps + the catalog rebuild (§3) |
| `mwh units check` | validate the packaged catalogue and print its summary (no data access) |
| `mwh units report --tier <t> [--format json]` | the unit-inconsistency report (§4) |

The macro is generated from the same YAML as the Python functions
(`units.macro_statements()`), so a value harmonised in SQL equals one harmonised in
Python; a session can call it on a literal through `mwh sql` (`SELECT
mwh_harmonize(223761, 100.4, 'degF') AS h` yields 38 degC, converted, plausible).

## 3. Catalog surfaces

`mwh build --tier <t> --tag units` runs three `python` steps (`dag/specs/units.yaml`) and
the shared `catalog` step; each step writes `lake/meta/<tier>/<table>.parquet`, which
EP-37's discovery walker registers as `meta.<table>`:

- `meta.item_units` — the catalogue, one row per itemid x accepted unit: `itemid`,
  `source`, `label`, `concept_group`, `canonical_unit`, `accepted_unit`, `unit_norm`,
  `formula`, `scale`, `intercept` (`canonical = value * scale + intercept`; the column is
  not called `offset` because that is a reserved word in DuckDB SQL), `plausible_low`,
  `plausible_high`, `curation_note`, `source_ref`, `version`, `catalogue_version`.
- `meta.item_unit_variants` — per curated itemid, the unit strings actually seen on the
  tier (§4).
- `meta.item_dictionary` — EP-29's `meta.itemids` shape (`source`, `itemid`, `label`,
  `abbreviation`, `linksto`, `category`, `fluid`, `unitname`, `param_type`) joined with
  `curated`, `concept_group`, `canonical_unit`, `plausible_low`, `plausible_high`;
  `unit_hint` stays on `meta.columns` (EP-29) and is not redefined here.

`units.register_units` (a `catalog.build.CATALOG_EXTENSIONS` entry) installs the macro
family and comments the three tables on every catalog build; `mwh catalog info --tier <t>`
lists them under "meta / derived / marts objects".

## 4. The variants table and the small-cell rule

`units.variants` counts `(itemid, source, valueuom)` over `labevents`, `chartevents` and
`outputevents` for the curated itemids only — an aggregate, so GOVERNANCE §5 applies. The
**raw** counts stay in the data root (`lake/meta/<tier>/raw/`, a subdirectory the catalog
walker never enters, never exported). The **published** `meta.item_unit_variants` keeps
every variant row but blanks `n_rows` and `share` on the rows the
`mimicwarehouse.safe.SUPPRESSOR` hook marks small (`suppressed = true`; EP-43 swaps the
hook for `disclose.suppress` with complementary suppression), and `share` is the share
**among the released rows** of the itemid, so a blanked cell cannot be backed out from the
shares. `expected` says whether the unit string is one the catalogue accepts.

`mwh units report --tier <t>` (`units.report`, reading both tables through `safe_query`,
audited) folds this into one row per curated itemid — `n_variants`, `n_suppressed`,
`dominant_unit`, `dominant_share`, `unexpected_units`, `flagged` (more than one variant,
or any unexpected unit) — with no cell below k shown. Flagged itemids are EP-44's input.

## 5. Conversion formulas

<!-- formulas:begin -->
| formula | canonical = f(x) | note |
|---|---|---|
| `identity` | `x` | the unit is already canonical |
| `f_to_c` | `x * 0.5555555555555556 + (-17.77777777777778)` | degrees Fahrenheit to Celsius: (F - 32) / 1.8 |
| `lb_to_kg` | `x * 0.45359237` | pounds to kilograms (international avoirdupois pound) |
| `in_to_cm` | `x * 2.54` | inches to centimetres |
| `mmol_to_mgdl_glucose` | `x * 18.0156` | glucose mmol/L to mg/dL (x 18.0156; 180.156 g/mol) |
| `umol_to_mgdl_creatinine` | `x * 0.011309658448314861` | creatinine umol/L to mg/dL (/ 88.42; 113.12 g/mol) |
| `mmol_to_mgdl_bun` | `x * 2.8013` | urea mmol/L to urea nitrogen mg/dL (x 2.8013; two N of 14.007) |
| `mgdl_to_mmol_lactate` | `x * 0.11101243339253998` | lactate mg/dL to mmol/L (/ 9.008; 90.08 g/mol) |
| `umol_to_mgdl_bilirubin` | `x * 0.05846585594013096` | bilirubin umol/L to mg/dL (/ 17.104; 584.66 g/mol) |
| `mmol_to_mgdl_calcium` | `x * 4.008` | calcium mmol/L to mg/dL (x 4.008; 40.078 g/mol) |
| `mmol_to_mgdl_magnesium` | `x * 2.4305` | magnesium mmol/L to mg/dL (x 2.4305; 24.305 g/mol) |
| `mmol_to_mgdl_phosphate` | `x * 3.0974` | phosphate (as phosphorus) mmol/L to mg/dL (x 3.0974; 30.974 g/mol) |
| `kpa_to_mmhg` | `x * 7.50062` | kilopascal to mmHg (x 7.50062) |
| `ifcc_to_ngsp_hba1c` | `x * 0.09148 + (2.152)` | HbA1c IFCC mmol/mol to NGSP percent (x 0.09148 + 2.152) |
| `g_l_to_g_dl` | `x * 0.1` | g/L to g/dL (x 0.1) |
| `mmol_to_gdl_hemoglobin` | `x * 1.6114` | haemoglobin mmol/L (Fe) to g/dL (x 1.6114) |
| `ng_l_to_ng_ml` | `x * 0.001` | ng/L to ng/mL (x 0.001) |
| `fraction_to_percent` | `x * 100.0` | a fraction (L/L) to percent (x 100) |
<!-- formulas:end -->

## 6. The curated items

63 items in 37 concept groups (catalogue v1). Bounds are inclusive, in the canonical unit;
`(blank)` is the NULL-unit key.

<!-- items:begin -->
| itemid | source | label | concept group | canonical unit | accepted units (conversion) | plausible (inclusive) | source_ref |
|---|---|---|---|---|---|---|---|
| 220045 | chartevents | Heart Rate | `vitals.hr` | `bpm` | `bpm` | 0 to 300 | `mimic-iv/concepts_duckdb/measurement/vitalsign.sql` |
| 220179 | chartevents | Non Invasive Blood Pressure systolic | `vitals.sbp` | `mmHg` | `mmHg` | 0 to 400 | `mimic-iv/concepts_duckdb/measurement/vitalsign.sql` |
| 220050 | chartevents | Arterial Blood Pressure systolic | `vitals.sbp` | `mmHg` | `mmHg` | 0 to 400 | `mimic-iv/concepts_duckdb/measurement/vitalsign.sql` |
| 225309 | chartevents | ART BP Systolic | `vitals.sbp` | `mmHg` | `mmHg` | 0 to 400 | `mimic-iv/concepts_duckdb/measurement/vitalsign.sql` |
| 220180 | chartevents | Non Invasive Blood Pressure diastolic | `vitals.dbp` | `mmHg` | `mmHg` | 0 to 300 | `mimic-iv/concepts_duckdb/measurement/vitalsign.sql` |
| 220051 | chartevents | Arterial Blood Pressure diastolic | `vitals.dbp` | `mmHg` | `mmHg` | 0 to 300 | `mimic-iv/concepts_duckdb/measurement/vitalsign.sql` |
| 225310 | chartevents | ART BP Diastolic | `vitals.dbp` | `mmHg` | `mmHg` | 0 to 300 | `mimic-iv/concepts_duckdb/measurement/vitalsign.sql` |
| 220181 | chartevents | Non Invasive Blood Pressure mean | `vitals.mbp` | `mmHg` | `mmHg` | 0 to 300 | `mimic-iv/concepts_duckdb/measurement/vitalsign.sql` |
| 220052 | chartevents | Arterial Blood Pressure mean | `vitals.mbp` | `mmHg` | `mmHg` | 0 to 300 | `mimic-iv/concepts_duckdb/measurement/vitalsign.sql` |
| 225312 | chartevents | ART BP Mean | `vitals.mbp` | `mmHg` | `mmHg` | 0 to 300 | `mimic-iv/concepts_duckdb/measurement/vitalsign.sql` |
| 220210 | chartevents | Respiratory Rate | `vitals.rr` | `insp/min` | `insp/min`, `/min` | 0 to 70 | `mimic-iv/concepts_duckdb/measurement/vitalsign.sql` |
| 224690 | chartevents | Respiratory Rate (Total) | `vitals.rr` | `insp/min` | `insp/min`, `/min` | 0 to 70 | `mimic-iv/concepts_duckdb/measurement/vitalsign.sql` |
| 220277 | chartevents | O2 saturation pulseoxymetry | `vitals.spo2` | `%` | `%` | 0 to 100 | `mimic-iv/concepts_duckdb/measurement/vitalsign.sql` |
| 223761 | chartevents | Temperature Fahrenheit | `vitals.temperature` | `degC` | `degF` (f_to_c), `degC` | 21.1 to 48.9 | `mimic-iv/concepts_duckdb/measurement/vitalsign.sql` |
| 223762 | chartevents | Temperature Celsius | `vitals.temperature` | `degC` | `degC`, `degF` (f_to_c) | 10 to 50 | `mimic-iv/concepts_duckdb/measurement/vitalsign.sql` |
| 226512 | chartevents | Admission Weight (Kg) | `anthro.weight` | `kg` | `kg`, `lbs` (lb_to_kg), `lb` (lb_to_kg) | 20 to 500 | `mimic-iv/concepts_duckdb/demographics/weight_durations.sql` |
| 224639 | chartevents | Daily Weight | `anthro.weight` | `kg` | `kg`, `lbs` (lb_to_kg), `lb` (lb_to_kg) | 20 to 500 | `mimic-iv/concepts_duckdb/demographics/weight_durations.sql` |
| 226531 | chartevents | Admission Weight (lbs.) | `anthro.weight` | `kg` | `(blank)` (lb_to_kg), `lbs` (lb_to_kg), `lb` (lb_to_kg), `kg` | 20 to 500 | `mimiciv_icu.d_items (mwh sql lookup, dev tier, 2026-09-06)` |
| 226730 | chartevents | Height (cm) | `anthro.height` | `cm` | `cm`, `inch` (in_to_cm), `in` (in_to_cm) | 120 to 230 | `mimic-iv/concepts_duckdb/measurement/height.sql` |
| 226707 | chartevents | Height | `anthro.height` | `cm` | `inch` (in_to_cm), `in` (in_to_cm), `(blank)` (in_to_cm), `cm` | 120 to 230 | `mimic-iv/concepts_duckdb/measurement/height.sql` |
| 220739 | chartevents | GCS - Eye Opening | `neuro.gcs_eyes` | `points` | `(blank)`, `points` | 1 to 4 | `mimic-iv/concepts_duckdb/measurement/gcs.sql` |
| 223900 | chartevents | GCS - Verbal Response | `neuro.gcs_verbal` | `points` | `(blank)`, `points` | 0 to 5 | `mimic-iv/concepts_duckdb/measurement/gcs.sql` |
| 223901 | chartevents | GCS - Motor Response | `neuro.gcs_motor` | `points` | `(blank)`, `points` | 1 to 6 | `mimic-iv/concepts_duckdb/measurement/gcs.sql` |
| 223835 | chartevents | Inspired O2 Fraction | `resp.fio2` | `%` | `(blank)`, `%` | 21 to 100 | `mimic-iv/concepts_duckdb/measurement/bg.sql` |
| 225664 | chartevents | Glucose finger stick (range 70-100) | `labs.glucose` | `mg/dL` | `(blank)`, `mg/dL`, `mmol/L` (mmol_to_mgdl_glucose) | 1 to 3000 | `mimic-iv/concepts_duckdb/measurement/vitalsign.sql` |
| 220621 | chartevents | Glucose (serum) | `labs.glucose` | `mg/dL` | `mg/dL`, `(blank)`, `mmol/L` (mmol_to_mgdl_glucose) | 1 to 3000 | `mimic-iv/concepts_duckdb/measurement/vitalsign.sql` |
| 226537 | chartevents | Glucose (whole blood) | `labs.glucose` | `mg/dL` | `mg/dL`, `(blank)`, `mmol/L` (mmol_to_mgdl_glucose) | 1 to 3000 | `mimic-iv/concepts_duckdb/measurement/vitalsign.sql` |
| 50912 | labevents | Creatinine | `labs.creatinine` | `mg/dL` | `mg/dL`, `umol/L` (umol_to_mgdl_creatinine) | 0.1 to 150 | `mimic-iv/concepts_duckdb/measurement/chemistry.sql` |
| 51006 | labevents | Urea Nitrogen | `labs.bun` | `mg/dL` | `mg/dL`, `mmol/L` (mmol_to_mgdl_bun) | 1 to 300 | `mimic-iv/concepts_duckdb/measurement/chemistry.sql` |
| 50983 | labevents | Sodium | `labs.sodium` | `mEq/L` | `mEq/L`, `mmol/L` | 80 to 200 | `mimic-iv/concepts_duckdb/measurement/chemistry.sql` |
| 50971 | labevents | Potassium | `labs.potassium` | `mEq/L` | `mEq/L`, `mmol/L` | 1 to 30 | `mimic-iv/concepts_duckdb/measurement/chemistry.sql` |
| 50902 | labevents | Chloride | `labs.chloride` | `mEq/L` | `mEq/L`, `mmol/L` | 50 to 200 | `mimic-iv/concepts_duckdb/measurement/chemistry.sql` |
| 50882 | labevents | Bicarbonate | `labs.bicarbonate` | `mEq/L` | `mEq/L`, `mmol/L` | 1 to 80 | `mimic-iv/concepts_duckdb/measurement/chemistry.sql` |
| 50931 | labevents | Glucose | `labs.glucose` | `mg/dL` | `mg/dL`, `mmol/L` (mmol_to_mgdl_glucose) | 1 to 3000 | `mimic-iv/concepts_duckdb/measurement/chemistry.sql` |
| 50809 | labevents | Glucose | `labs.glucose` | `mg/dL` | `mg/dL`, `mmol/L` (mmol_to_mgdl_glucose) | 1 to 3000 | `mimic-iv/concepts_duckdb/measurement/bg.sql` |
| 50813 | labevents | Lactate | `labs.lactate` | `mmol/L` | `mmol/L`, `mg/dL` (mgdl_to_mmol_lactate) | 0.1 to 40 | `mimic-iv/concepts_duckdb/measurement/bg.sql` |
| 50960 | labevents | Magnesium | `labs.magnesium` | `mg/dL` | `mg/dL`, `mmol/L` (mmol_to_mgdl_magnesium) | 0.1 to 20 | `mimiciv_hosp.d_labitems (mwh sql lookup, dev tier, 2026-09-06)` |
| 50893 | labevents | Calcium, Total | `labs.calcium` | `mg/dL` | `mg/dL`, `mmol/L` (mmol_to_mgdl_calcium) | 1 to 30 | `mimic-iv/concepts_duckdb/measurement/chemistry.sql` |
| 50970 | labevents | Phosphate | `labs.phosphate` | `mg/dL` | `mg/dL`, `mmol/L` (mmol_to_mgdl_phosphate) | 0.1 to 30 | `mimiciv_hosp.d_labitems (mwh sql lookup, dev tier, 2026-09-06)` |
| 50862 | labevents | Albumin | `labs.albumin` | `g/dL` | `g/dL`, `g/L` (g_l_to_g_dl) | 0.5 to 10 | `mimic-iv/concepts_duckdb/measurement/chemistry.sql` |
| 50852 | labevents | % Hemoglobin A1c | `labs.hba1c` | `%` | `%`, `mmol/mol` (ifcc_to_ngsp_hba1c) | 2 to 25 | `mimiciv_hosp.d_labitems (mwh sql lookup, dev tier, 2026-09-06)` |
| 50885 | labevents | Bilirubin, Total | `labs.bilirubin_total` | `mg/dL` | `mg/dL`, `umol/L` (umol_to_mgdl_bilirubin) | 0.1 to 100 | `mimic-iv/concepts_duckdb/measurement/enzyme.sql` |
| 51003 | labevents | Troponin T | `labs.troponin_t` | `ng/mL` | `ng/mL`, `ug/L`, `ng/L` (ng_l_to_ng_ml) | 0.001 to 100 | `mimic-iv/concepts_duckdb/measurement/cardiac_marker.sql` |
| 50820 | labevents | pH | `labs.ph` | `units` | `units`, `(blank)` | 6.5 to 8 | `mimic-iv/concepts_duckdb/measurement/bg.sql` |
| 50821 | labevents | pO2 | `labs.po2` | `mmHg` | `mm Hg`, `kPa` (kpa_to_mmhg) | 1 to 800 | `mimic-iv/concepts_duckdb/measurement/bg.sql` |
| 50818 | labevents | pCO2 | `labs.pco2` | `mmHg` | `mm Hg`, `kPa` (kpa_to_mmhg) | 1 to 250 | `mimic-iv/concepts_duckdb/measurement/bg.sql` |
| 51301 | labevents | White Blood Cells | `labs.wbc` | `K/uL` | `K/uL`, `x10^9/L`, `10^9/L` | 0.1 to 1000 | `mimic-iv/concepts_duckdb/measurement/complete_blood_count.sql` |
| 51222 | labevents | Hemoglobin | `labs.hemoglobin` | `g/dL` | `g/dL`, `g/L` (g_l_to_g_dl), `mmol/L` (mmol_to_gdl_hemoglobin) | 1 to 30 | `mimic-iv/concepts_duckdb/measurement/complete_blood_count.sql` |
| 51221 | labevents | Hematocrit | `labs.hematocrit` | `%` | `%`, `L/L` (fraction_to_percent) | 5 to 80 | `mimic-iv/concepts_duckdb/measurement/complete_blood_count.sql` |
| 51265 | labevents | Platelet Count | `labs.platelets` | `K/uL` | `K/uL`, `x10^9/L`, `10^9/L` | 1 to 3000 | `mimic-iv/concepts_duckdb/measurement/complete_blood_count.sql` |
| 51237 | labevents | INR(PT) | `labs.inr` | `ratio` | `(blank)`, `ratio` | 0.5 to 20 | `mimic-iv/concepts_duckdb/measurement/coagulation.sql` |
| 226559 | outputevents | Foley | `urine.output` | `mL` | `mL` | 0 to 5000 | `mimic-iv/concepts_duckdb/measurement/urine_output.sql` |
| 226560 | outputevents | Void | `urine.output` | `mL` | `mL` | 0 to 5000 | `mimic-iv/concepts_duckdb/measurement/urine_output.sql` |
| 226561 | outputevents | Condom Cath | `urine.output` | `mL` | `mL` | 0 to 5000 | `mimic-iv/concepts_duckdb/measurement/urine_output.sql` |
| 226584 | outputevents | Ileoconduit | `urine.output` | `mL` | `mL` | 0 to 5000 | `mimic-iv/concepts_duckdb/measurement/urine_output.sql` |
| 226563 | outputevents | Suprapubic | `urine.output` | `mL` | `mL` | 0 to 5000 | `mimic-iv/concepts_duckdb/measurement/urine_output.sql` |
| 226564 | outputevents | R Nephrostomy | `urine.output` | `mL` | `mL` | 0 to 5000 | `mimic-iv/concepts_duckdb/measurement/urine_output.sql` |
| 226565 | outputevents | L Nephrostomy | `urine.output` | `mL` | `mL` | 0 to 5000 | `mimic-iv/concepts_duckdb/measurement/urine_output.sql` |
| 226567 | outputevents | Straight Cath | `urine.output` | `mL` | `mL` | 0 to 5000 | `mimic-iv/concepts_duckdb/measurement/urine_output.sql` |
| 226557 | outputevents | R Ureteral Stent | `urine.output` | `mL` | `mL` | 0 to 5000 | `mimic-iv/concepts_duckdb/measurement/urine_output.sql` |
| 226558 | outputevents | L Ureteral Stent | `urine.output` | `mL` | `mL` | 0 to 5000 | `mimic-iv/concepts_duckdb/measurement/urine_output.sql` |
| 227488 | outputevents | GU Irrigant Volume In | `urine.output` | `mL` | `mL` | 0 to 5000 | `mimic-iv/concepts_duckdb/measurement/urine_output.sql` |
| 227489 | outputevents | GU Irrigant/Urine Volume Out | `urine.output` | `mL` | `mL` | 0 to 5000 | `mimic-iv/concepts_duckdb/measurement/urine_output.sql` |
<!-- items:end -->

## 7. What this page deliberately does not decide

- Implausible-value counts and QC status flags per table → EP-44 (uses `units.bounds`).
- First-day / hourly rollups of these itemids → EP-55 / EP-56.
- LOINC / SNOMED mapping of itemids (mimic-code `concept_map/*.csv`) → EP-138 / EP-143.
- Patching concept SQL to use these bounds → EP-38's mechanism, only where an upstream fix
  exists.
- Value-based FiO2 rescaling (§1) and any other rule that reads the value rather than the
  unit string.
