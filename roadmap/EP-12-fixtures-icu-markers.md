# EP-12 — Synthetic fixture generator B (icu) + pytest tier markers

**Size:** M · **Tier:** fixture · **Core/Stretch:** core · **Depends on:** EP-11 (Synthetic fixture generator A (hosp)) · **Blocks:** EP-16 (Re-plan P1)

## Context

EP-11 delivered the deterministic hosp fixture (`mimicwarehouse.fixtures`: `FixtureSpec`, `FixturePlan` with
`plan.icu_segments`, seed vocabularies, writer, `mwh fixtures build`, `tests/fixtures/mimic-iv-3.1/hosp/`).
This brief completes the fixture tier (**D-18**, **D-27**) with the 9 `mimiciv_icu` tables — `icustays`
derived from the same ICU segments so `transfers` and `icustays` agree, chartevents-shaped vitals/GCS/FiO2
for the concepts and first-day marts, vasopressor/fluid `inputevents`, urine `outputevents`, ventilation and
line `procedureevents` — and the test infrastructure every later brief relies on: pytest **tier markers**
(`fixture` default, `dev` and `full` opt-in, DESIGN §20), the `--tier` option that `uv run poe test` and
`mwh verify EP-n` (EP-6) pass through, and an **in-memory fixture catalog** (typed DuckDB over the fixture
CSVs) that tests use until the real loader/catalog (EP-17/21) can build a fixture-tier lake. Ids stay
≥ 90 000 000 (`stay_id`, `caregiver_id`, `orderid`, …); real `itemid`s are used because concepts (EP-37)
and marts (EP-55) look them up by number — itemids are dictionary values, not patient data, typed from public
docs (mimic.mit.edu), never from `source material/`. The `dev`/`full` markers must *skip*, not fail, until
`dev.duckdb`/`full.duckdb` exist (EP-21).

## In scope

1. **`d_items` + `caregiver` seed** (`src/mimicwarehouse/fixtures/vocab/d_items.yaml`): ~45 real itemids with
   `label, abbreviation, linksto, category, unitname, param_type, lownormalvalue, highnormalvalue`: vitals
   (HR 220045, NBP 220179/220180/220181, ABP 220050/220051/220052, RR 220210, SpO2 220277, Temp °F 223761 /
   °C 223762), GCS (220739 eye, 223900 verbal, 223901 motor), FiO2 223835, admission weight 226512, height 226730,
   vasoactives (norepinephrine 221906, epinephrine 221289, phenylephrine 221749, vasopressin 222315, dopamine 221662),
   fluids/drips (NaCl 0.9 % 225158, D5W 220949, propofol 222168, insulin regular 223258), urine (Foley 226559,
   Void 226560), procedures (invasive ventilation 225792, non-invasive 225794, arterial line 225752, CRRT 225802,
   intubation 224385, extubation 227194), a few `datetimeevents` and `ingredientevents` items with the matching
   `linksto`; ~15 `caregiver_id`s ≥ 90 000 000.
2. **icu generators** (`src/mimicwarehouse/fixtures/icu.py`, contract-typed Polars frames): `icustays` (one row
   per `plan.icu_segments` entry: `stay_id`, `first_careunit`/`last_careunit` from the segment, `intime`/`outtime`,
   `los` days); `chartevents` (per stay: hourly HR/RR/SpO2/NBP for the first 48 h then every 4 h, temperature
   6-hourly, GCS 4-hourly, FiO2 where ventilated, weight/height once; `valuenum` in plausible ranges with rare
   outliers, `value` = formatted number, `valueuom` from `d_items`, `storetime` ≥ `charttime`, `warning` 0/1,
   `caregiver_id`); `datetimeevents` (a few per stay); `inputevents` (norepinephrine mcg/kg/min drips in ~30 % of
   stays with `starttime/endtime/amount/amountuom/rate/rateuom/orderid/linkorderid/ordercategoryname/…/
   patientweight/statusdescription`, maintenance fluids in most stays); `ingredientevents` (mirror of the fluid
   orders); `outputevents` (urine hourly-ish, mL); `procedureevents` (invasive ventilation in ~40 % of stays with
   `starttime/endtime/value/valueuom/location/…/statusdescription`, arterial lines, CRRT in the planted AKI stays);
   `caregiver`; `d_items`. Keep the phenotype signal EP-11 planted consistent (norepinephrine + culture + antibiotic
   in the sepsis stays; ventilation and CRRT where the story needs them). Extend `mwh fixtures build` and the
   EP-11 `validate()` (every `stay_id` inside its admission and matching a `transfers` ICU row; every icu event
   inside `[intime − 6 h, outtime + 6 h]`; every `itemid` in `d_items` with the right `linksto`).
3. **Fixture files**: `tests/fixtures/mimic-iv-3.1/icu/<table>.csv` (9 files), `manifest.json` extended, README
   updated; whole fixture ≤ 10 MB (chartevents ≤ 3 MB — trim hourly cadence before trimming stays).
4. **In-memory fixture catalog** (`src/mimicwarehouse/fixtures/catalog.py`):
   `build_fixture_catalog(root: Path | None = None) -> duckdb.DuckDBPyConnection` — in-memory DuckDB configured with
   `get_settings().duckdb_settings("app")` (EP-3; explicit `temp_directory`), schemas `mimiciv_hosp`/`mimiciv_icu`,
   `CREATE TABLE … AS SELECT * FROM read_csv(<file>, header=true, columns=<contract types>)` for all 31 tables in
   < 5 s; contract `COMMENT`s optional. This is the `fixture` tier for unit tests until EP-21 builds a real
   fixture catalog from the same CSVs.
5. **pytest tier markers + conftest** (extend EP-1's `mimicwarehouse/tests/conftest.py`, which already registers
   `ep_0`…`ep_199` and a placeholder `tier(name)` under `--strict-markers`): give `tier(name)` its semantics
   (`fixture` | `dev` | `full`; unmarked = `fixture`); option `--tier {fixture,dev,full}` (env `MWH_TEST_TIER`
   fallback; default `fixture`) selecting the **maximum** tier to run (`--tier dev` runs fixture+dev; `--tier full`
   runs all); `dev`/`full` tests are deselected below their tier and **skipped with a reason** when
   `get_settings().catalog_path(tier)` is absent; session fixtures `contract`, `fixture_root`, `fixture_catalog`
   (from item 4), `tier`. poe tasks `test` (fixture, EP-1), `test-dev` (`pytest --tier dev`), `test-full`;
   `mwh verify EP-n -- --tier dev` works through EP-6's pass-through unchanged. Document the vocabulary in
   `tests/README.md` (or extend the fixtures README) and add a dated note to `DESIGN.md` §20.
6. **Tests** (`tests/ep/test_ep12.py`, `@pytest.mark.ep_12`): icu regeneration byte-identical vs `manifest.json`;
   all 9 files load through DuckDB with contract types, zero rejects; `icustays` ↔ `transfers` agreement; every
   event within its stay window; ≥ 1 stay with norepinephrine + culture + antibiotic and ≥ 1 with ventilation;
   `build_fixture_catalog()` returns 31 tables and `SELECT count(*) FROM mimiciv_icu.icustays` equals the plan; via
   `pytester`, a `tier("dev")` test is deselected by default and selected under `--tier dev` (skipped when the
   catalog is missing); `--strict-markers` rejects an unknown marker; `mwh guard` accepts the fixture tree.

## Out of scope

- ED / note fixtures → EP-142 / EP-148; the ODbL demo tier → EP-22.
- Parquet/bucketed fixture lake and `fixture.duckdb` built by the real loader/catalog → EP-17/18/21.
- Any dev/full-tier test content — this brief only makes the markers work; the first `dev` tests are EP-17's.

## Verification / acceptance

- `uv run poe test -m ep_12` and `uv run --group dev mwh verify EP-12` green on fixture; `uv run poe test` (all
  markers) still green — EP-8..EP-11 tests unchanged.
- `uv run poe test-dev` runs and reports the dev-tier tests as **skipped** (no `dev.duckdb` yet), not failed.
- `uv run --group dev mwh fixtures build` twice ⇒ clean `git status`; 31 CSVs under `tests/fixtures/mimic-iv-3.1/`
  total ≤ 10 MB; pre-commit `mwh guard` passes.
- Commit `feat(mimicwarehouse): icu fixtures + pytest tiers + fixture catalog (EP-12)`, then
  `docs(roadmap): record EP-12 commit hash`.

## Parked → final-roadmap.md

- Hypothesis-driven property fixtures (random but schema-valid frames) for loader edge cases; trigger: a loader
  bug the fixed seed does not reproduce.
