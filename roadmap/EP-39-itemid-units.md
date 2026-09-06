# EP-39 — Itemid dictionary curation + unit harmonization

**Size:** M · **Tier:** fixture+dev · **Core/Stretch:** core · **Depends on:** EP-29 (Catalog & data dictionary (meta.*)) · **Blocks:** EP-44 (Data-quality profiling), EP-54 (Re-plan P3), EP-55 (Latency marts A: first-day features + itemid rollups ⏱), EP-138 (Concept/unit mapping guide + mapping YAML)

> **EP-33 amendment (2026-09-01).** Header facts unchanged; shorthand per the README notation
> table. (1) **Shipped `meta.*` names** (EP-29): the dictionary view is `meta.itemids`
> (`d_items` tagged `'icu'` UNION ALL `d_labitems` tagged `'hosp'`), the profiles are
> `meta.tables`/`meta.columns`/`meta.row_counts`, and `meta.columns` already carries
> `unit_hint` from the units seed (`mwh catalog dictionary` renders it) — item 3's
> `meta.item_dictionary` joins `meta.itemids` and must not redefine `unit_hint`;
> `meta.catalog_info`/`meta.catalog_tables` list the new tables. (2) **Spec discovery**
> (ledger P3C-2, implemented by EP-37): `dag/specs/units.yaml` is merged into the one graph by
> `dag.spec.load_dag()`, so `mwh build --tier dev --tag units` needs no `--spec`; the steps are
> the `python` kind (`module:function`, `(step, ctx) -> StepOutcome`) via
> `dag.runner.STEP_HANDLERS`, writing `lake/meta/<tier>/<table>.parquet` (EP-29's convention,
> registered by the `catalog` step). (3) The `mwh_harmonize` macro is installed by a
> `CATALOG_EXTENSIONS` entry (EP-34's hook) on the build connection (`engine.open_duckdb(
> "build", ...)`); sessions verify itemids with `mwh sql` against `meta.itemids` (`meta.*` is a
> registry exemption — EP-33 B1c). (4) The variants aggregate uses `count(*)` (with `FILTER
> (WHERE ...)` where needed) as its count-family node — an alias alone never satisfies the
> rule (EP-33 B1); rows < 11 are suppressed by the `safe.SUPPRESSOR` hook until EP-43 swaps
> in `disclose.suppress`. (5) `mwh units report` errors go to stderr via `console.fail`; new
> CLI strings stay ASCII (degree/micro signs spelled out or routed through the console helper).

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

> **Completion note (2026-09-06).** Executed as one session against EP-29 ☑ `e07d3ed`
> (EP-38 ☑ `c5c0b0c` was the head); Windows power mode read *Best performance* on AC
> (`mwh doctor` `power_scheme`) throughout; no full-tier run was launched (the brief's tier
> is fixture+dev — the full catalog gains the three tables and the macro at its next
> `mwh build --tier full --tag units`, a ~10 s foreground-class step the next brief that
> rebuilds full can fold in).
>
> **Item 1 — the catalogue.** `src/mimicwarehouse/data/item_units.yaml` (package data,
> ASCII, 63 items in 37 concept groups, 117 accepted-unit rows) validated by
> `units.ItemSpec` / `units.ItemCatalogue`. Itemids were copied from the vendored
> `concepts_duckdb` files (`vitalsign`, `chemistry`, `complete_blood_count`, `bg`,
> `weight_durations`, `height`, `gcs`, `urine_output`, `coagulation`, `cardiac_marker`,
> `enzyme`) and every one verified against `meta.itemids` on the dev catalog through
> `mwh sql` (audit `b3e57ee9…`, dictionary rows only); the four items no vendored concept
> reads (`Admission Weight (lbs.)` 226531, `Magnesium` 50960, `Phosphate` 50970, `%
> Hemoglobin A1c` 50852) carry that lookup as their `source_ref`. Accepted units were chosen
> from the `(itemid, valueuom, count)` aggregates over dev `labevents` / `chartevents` /
> `outputevents` (audits `1c994b8b…`, `46b39d88…`, `1b436379…`; k = 11): the labs are
> single-unit on dev (INR carries a NULL unit), the chart items are single-unit apart from
> a NULL-unit minority on the serum / whole-blood glucose items (accepted, mg/dL implied),
> the pounds and inches items are charted with NULL / `Inch` units. Bounds are inclusive
> sanity bounds in the canonical unit (upstream's numbers where upstream has a real filter;
> a narrower adult window where upstream only has a `<= 10000` garbage guard, said in the
> `curation_note`).
>
> **Item 2 — the API.** `normalize_unit` / `sql_normalize_unit` / the Polars twin (one
> comparison key per unit spelling; `""` = NULL / blank, accepted only where the itemid
> implies the unit), `Affine` + 18 named `FORMULAS` (all affine, all invertible),
> `harmonize` → `Harmonized(value_canonical, unit_canonical, converted, plausible)`,
> `harmonize_frame` / `plausible_mask` (Polars), `bounds`, and the macro family
> `mwh_harmonize` + `mwh_unit_norm` / `mwh_unit_canonical` / `mwh_unit_known` /
> `mwh_value_canonical` / `mwh_plausible` generated by `macro_statements` from the same
> YAML and installed by `register_units` (the third `CATALOG_EXTENSIONS` entry; it also
> comments the three meta tables). Unknown units pass through with `converted = false`;
> an uncurated itemid returns the value with `unit_canonical` / `plausible` NULL. The macro
> casts its value to DOUBLE (a DECIMAL literal times a DECIMAL scale overflowed —
> `docs/gotchas.md` §1), so SQL and Python are one float arithmetic (agreement within
> 1e-9 on every crafted case).
>
> **Item 3 — the meta tables.** `dag/specs/units.yaml`: `units.item_units`,
> `units.variants`, `units.dictionary` (`python` kind, tag `units`, no `target`) and the
> shared `catalog` step; each writes `lake/meta/<tier>/<table>.parquet` (EP-29's layout,
> EP-37's walker registers them). `meta.item_unit_variants` applies the k-rule at build
> time through `safe.SUPPRESSOR` — rows kept, `n_rows` / `share` blanked, `share` among
> the released rows, the raw counts under `lake/meta/<tier>/raw/` (D-33 addendum).
> `meta.item_dictionary` reuses EP-29's SELECT, now `catalog.build.ITEMIDS_SELECT_SQL`
> (the one edit to an earlier module: the constant extracted, `CATALOG_EXTENSIONS` grew
> the third entry). `mwh build --tier dev --tag units`: builds `20260906T184731-dev-9536a83`
> (run `20260906T184731Z-6c99d0`) and, after the `offset` → `intercept` column rename,
> `20260906T185024-dev-9536a83` (run `20260906T185024Z-c17a57`) — 9.6 s / 9.7 s wall
> for the three steps plus the dev catalog rebuild (variants 0.7 s over the dev event
> tables). On dev: 117 `meta.item_units` rows over 63 itemids; 64 variant rows over 62
> itemids, 1 suppressed at k = 11; `meta.item_dictionary` 5,745 itemids, 63 curated.
>
> **Item 4 — the report.** `units.report(tier)` (two audited `safe_query` reads of the meta
> tables, `row_cap` 10,000) + `summarize_variants` → one row per curated itemid
> (`n_variants`, `n_suppressed`, `dominant_unit`, `dominant_share`, `unexpected_units`,
> `flagged`); `mwh units report --tier dev` renders 63 rows, 2 flagged (the two chart
> glucose items with an accepted NULL-unit minority), no cell below k shown; `mwh units
> check` validates the catalogue. `mwh units` attached in `cli.py`; DESIGN §15 dated note
> + module-map line; the CLI's import budget holds (`safe`, `catalog.build`, the concept
> runner stay lazy).
>
> **Item 5 — tests + docs.** `tests/ep/test_ep39.py` (16 tests: 15 fixture + 1 dev):
> catalogue validation and eleven crafted refusals; every formula round-trips within
> 1e-9; Python / DuckDB / Polars normalisation agreement on 19 spellings; 23 crafted
> mixed-unit cases through the scalar, the frame and the macro (`plausible` inclusive,
> unknown units flagged, uncurated itemids NULL); the DAG wiring and hook order; crafted
> source tables → `compute_variants` / `suppress_variants` (the hook decides, shares over
> released rows); the fixture lake's three tables + macro (labels equal the fixture dims,
> no released cell below k, the raw file outside the catalog); the macro and the tables
> through `safe_query` and `mwh sql`; the report and its CLI; the docs page in sync; the
> import budget. Dev: labels equal the real dictionary for all 63, every core itemid has a
> variant row, coverage ≥ 95 % (62 / 63 — one urine-output item is absent from the 5 %
> sample), nothing below k released. `docs/methods/units.md` (curation policy, API,
> catalog surfaces, the small-cell handling, generated formula + item tables via `python
> -m mimicwarehouse.units`); `docs/gotchas.md` §1 gained the DECIMAL-overflow, `offset`
> and macro-persistence entries; workspace README module row + quick start.
>
> **Earlier tests touched.** None. `catalog/build.py` (constant extraction + the hook
> entry) and `cli.py` (one `add_typer` line) are the only earlier modules edited.
>
> **Judgment calls (owner review).** (1) Bounds are **inclusive** at both ends (mimic-code's
> strict filters differ only at the exact boundary; documented); (2) a NULL / blank unit
> is accepted only where the itemid implies the unit, never as a wildcard; (3) the
> variants table keeps suppressed rows with blanked counts and computes `share` over the
> released rows (D-33 addendum) rather than dropping them; (4) `flagged` = more than one
> variant spelling **or** any unexpected unit, so a legitimate NULL-unit minority flags
> too — EP-44 decides what to do with each flag; (5) the affine intercept column is
> `intercept`, not `offset` (reserved word); (6) value-based FiO2 rescaling (fractions
> 0.21–1.0) is out of scope for a unit-based rule — recorded on the docs page, EP-55
> applies upstream's value rule; (7) the two ureteral-stent urine items are curated for
> completeness although one is absent from the dev sample.
>
> **Deviations from the brief text.** "Every curated itemid has ≥ 1 variant row on dev" is
> asserted as every *core* itemid plus ≥ 95 % overall (an itemid with zero rows in the
> 5 % sample cannot have one); `harmonize` returns `plausible = None` (not `False`) for an
> uncurated itemid or a NULL value; `--tier dev` reads went through `mwh sql` exactly as
> the brief's Context asks, and the three dictionary-only lookups are cited by audit id.
>
> **Gates.** `poe check` **954 passed**, 40 deselected, 372 s (ruff check, ruff format
> --check, pyright clean; 939 → 954 = the 15 new fixture tests); `mwh verify EP-39` 15
> passed (50 s); `poe test-dev -m ep_39` 16 passed (58 s, the dev catalog rebuilt by
> `--tag units` first); `poe roadmap-check --strict` 0 errors, 0 warnings (172 rows, 47
> done before this brief's tick); `mwh guard` clean over every new and edited path;
> `mwh units check` 63 items ok; `mwh sql` on dev: `count(DISTINCT itemid)` over
> `meta.item_units` = 63 (audit `b21e4d8c…`), the macro on a Fahrenheit literal returns
> 38 degC converted + plausible (audit `c38d9140…`), zero released variant cells below
> k (audit `d7260d97…`); `mwh catalog info --tier dev` lists the three tables (78
> registry / derived objects). No dependency change.
>
> **Owner decisions (end of session, all the recommended option).** Keep the
> build-time suppression shape of `meta.item_unit_variants` (rows kept, counts blanked,
> shares over released rows — the D-33 addendum); keep the broad `flagged` rule (any second
> spelling or an unexpected unit; EP-44 triages); leave the full-tier `--tag units` build
> to the next brief that rebuilds full; commit as the two-step pair, no push.
