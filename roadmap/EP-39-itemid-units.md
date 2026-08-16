# EP-39 — Itemid dictionary curation + unit harmonization

**Size:** M · **Tier:** fixture+dev · **Core/Stretch:** core · **Depends on:** EP-29 (Catalog & data dictionary (meta.*)) · **Blocks:** EP-44 (Data-quality profiling), EP-54 (Re-plan P3), EP-55 (Latency marts A: first-day features + itemid rollups ⏱), EP-138 (Concept/unit mapping guide + mapping YAML)

## Context

`d_items` (chartevents/inputevents/outputevents/procedureevents/datetimeevents itemids) and
`d_labitems` (labevents itemids) are dictionary tables — schema-level metadata that a session may
freely see (GOVERNANCE §4). EP-29 exposed them as `meta.*` dictionaries. What is missing is
curation: which itemids are the canonical vitals/labs, what unit each should be in, how variant
units convert (°F→°C, lb→kg, in→cm, mmol/L↔mg/dL for glucose, µmol/L→mg/dL for creatinine), and
what values are physiologically implausible. mimic-code's concept SQL (`vitalsign`, `chemistry`,
`complete_blood_count`, `bg`, `weight_durations`, `height`, `gcs`, `urine_output`) already encodes
itemid lists and some unit filters — reuse them as the authority; do not hand-type itemids from
memory. Deliverable: `src/mimicwarehouse/units.py` (DESIGN §15) + `meta.item_units` consumed by
QC (EP-44), first-day marts (EP-55) and the linkage mapping guide (EP-138). All aggregate
inspection goes through `safe_query` (`SELECT valueuom, count(*) … GROUP BY 1` per itemid is an
aggregate; k = 11 applies). D-17, D-19, D-35 apply.

## In scope

1. **Curated item catalogue** (`src/mimicwarehouse/units.py` + package data
   `src/mimicwarehouse/data/item_units.yaml`) — pydantic `ItemSpec` (itemid, source
   `chartevents|labevents|outputevents|inputevents`, label, concept_group e.g. `vitals.hr`,
   `labs.creatinine`, canonical_unit, accepted_units → conversion (factor or named formula
   `f_to_c`, `lb_to_kg`, `in_to_cm`, `mmol_to_mgdl_glucose`, `umol_to_mgdl_creatinine`, …),
   plausible_low/high in canonical unit, curation_note, source_ref (mimic-code file), version).
   Seed ≥ 40 items: core vitals (heart rate, NIBP/ABP systolic/diastolic/mean, respiratory rate,
   SpO2, temperature °F/°C, weight daily/admission kg/lb, height cm/in, GCS eye/verbal/motor,
   FiO2), core labs (creatinine, BUN, sodium, potassium, chloride, bicarbonate, glucose (serum +
   blood-gas), lactate, WBC, hemoglobin, hematocrit, platelets, INR, pH, pO2, pCO2, bilirubin,
   albumin, troponin T, magnesium, calcium, phosphate, HbA1c), urine output items. Itemids are
   copied from the vendored concept SQL and verified with `mwh sql` against `d_items`/`d_labitems`
   labels (dictionary lookups, no patient data).
2. **Conversion + plausibility API** — `harmonize(itemid, value, valueuom) -> (value_canonical,
   unit_canonical, converted: bool, plausible: bool)` in Python; a DuckDB macro
   `mwh_harmonize(itemid, value, valueuom)` registered by the catalog builder (returns a STRUCT),
   built from the same YAML so SQL and Python agree; `bounds(itemid)`; `plausible_mask` for
   Polars frames. Unknown units → `converted=false`, value passed through, flagged.
3. **`meta.item_units` + `meta.item_unit_variants`** — DAG spec `src/mimicwarehouse/dag/specs/
   units.yaml` (steps `units.item_units`, `units.variants`, `units.dictionary`; tag `units`):
   materialize the YAML
   as `meta.item_units` (one row per itemid × accepted unit) and compute
   `meta.item_unit_variants` per tier: (itemid, source, valueuom, n_rows, share) from
   `labevents`/`chartevents` for curated itemids only (aggregate; suppress `n_rows < 11` via the
   catalog's `k`-rule until EP-43 lands — store raw in the data root, never export). Also
   `meta.item_dictionary`: EP-29's `meta.itemids` view (`d_items ∪ d_labitems`) joined with the
   curation columns `curated`, `concept_group`, `canonical_unit`, `plausible_low/high` (all
   written as `lake/meta/<tier>/<table>.parquet` per EP-29's convention).
4. **Unit-inconsistency report** — `units.report(tier) -> polars.DataFrame` (itemid, label,
   n_variants, dominant_unit, dominant_share, unexpected_units list) rendered by `mwh units report
   --tier dev` as a rich table (aggregates only); flagged itemids feed EP-44's checks. Add `mwh
   units` to the CLI and a dated note to DESIGN.md §15.
5. **Tests + docs** — `tests/ep/test_ep39.py` (`@pytest.mark.ep_39`; fixture, `dev`): every
   formula round-trips within 1e-9 (F↔C, lb↔kg, in↔cm, glucose, creatinine); YAML validates
   (unique itemids, plausible_low < plausible_high, canonical unit ∈ accepted); crafted synthetic
   rows in mixed units harmonize to the canonical unit; out-of-bounds values flag; on dev,
   `meta.item_units` and `meta.item_unit_variants` exist and every curated itemid has ≥ 1 variant
   row. `docs/methods/units.md` (new): curation policy, conversion table, bounds table generated
   from the YAML.

## Out of scope

- Implausible-value counts per table and QC status flags → EP-44 (uses `bounds`).
- First-day/hourly rollups of these itemids → EP-55/56.
- LOINC/SNOMED mapping of itemids (mimic-code `concept_map/*.csv`) → EP-138 / EP-143.
- Patching concept SQL to use these bounds → EP-38 (only where an upstream fix exists).

## Verification / acceptance

- `uv run poe test -m ep_39` green on fixture and dev; `uv run --group dev mwh verify EP-39` green.
- `uv run --group dev mwh build --tier dev --tag units` builds `meta.item_units`,
  `meta.item_unit_variants`, `meta.item_dictionary`; `uv run --group dev mwh units report --tier
  dev` prints the variants table with no cell < 11 shown.
- `uv run --group dev mwh sql "SELECT count(*) FROM meta.item_units"` ≥ 40 distinct itemids;
  `mwh_harmonize` macro callable from `mwh sql` on a literal (e.g. a temperature in °F).
- `docs/methods/units.md` exists; conversion and bounds tables render from the YAML.
