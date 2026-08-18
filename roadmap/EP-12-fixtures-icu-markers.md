# EP-12 — Synthetic fixture generator B (icu) + pytest tier markers

**Size:** M · **Tier:** fixture · **Core/Stretch:** core · **Depends on:** EP-11 (Synthetic fixture generator A (hosp)) · **Blocks:** EP-16 (Re-plan P1)

> **Amended at EP-7 re-plan (2026-08-17).** Checked against the P0 code; header facts unchanged.
> (1) **`MWH_TEST_TIER` collides with `Settings`** (EP-3): `MWH_` is the pydantic-settings env prefix and
> `Settings` is `extra="forbid"` — a stray `MWH_TEST_TIER=` line in `mimicwarehouse/.env` would make every
> `Settings()` construction raise (`test_ep03` proves it with `MWH_DATA_ROOTT`), and making it a real
> setting would require a `.env.example` line (`test_ep03` asserts parity). Item 5 renames the fallback to
> **`PYTEST_TIER`** (not `MWH_`-prefixed). (2) **Tier vocabulary:** `config.Tier` is
> `fixture | demo | dev | full` and `Settings.default_tier` defaults to `"dev"`; the pytest ladder in item 5
> is deliberately the three-step subset `fixture < dev < full` (`demo` is a data tier for EP-22, never a test
> tier) and never reads `settings.default_tier` — say so in `tests/README.md`. (3) `pytester` ships with
> pytest 9.1.1 but is off by default — item 5 adds `pytest_plugins = ["pytester"]` to `tests/conftest.py`
> (the nested runs inherit `addopts = "-ra --strict-markers"`). (4) `test_ep01` asserts the marker
> registration line still starts with `tier(name):` — keep that prefix when giving the marker semantics;
> the "still green" claim reads **EP-0 … EP-11**, not EP-8 … EP-11. (5) `poe check` stays fixture-only
> (`lint` + `typecheck` + `test`); `test-dev`/`test-full` are new tasks outside it. (6) Same pre-commit
> byte-identity and G4-all-columns rules as EP-11's amendment (the icu CSVs and the extended
> `manifest.json`); `mwh fixtures` diagnostic-command choice inherited from EP-11. Command forms: `uv run
> mwh …` ≡ `uv run --group dev mwh …`.

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
   (`fixture` | `dev` | `full`; unmarked = `fixture`; keep the registration text starting `tier(name):` —
   `test_ep01` asserts it); option `--tier {fixture,dev,full}` (env **`PYTEST_TIER`** fallback — not
   `MWH_TEST_TIER`, which would collide with `Settings`' `MWH_` prefix / `extra="forbid"`; amended EP-7; default
   `fixture`; the ladder is a deliberate subset of `config.Tier` — `demo` is a data tier, not a test tier — and
   never reads `settings.default_tier`) selecting the **maximum** tier to run (`--tier dev` runs fixture+dev;
   `--tier full` runs all); `dev`/`full` tests are deselected below their tier and **skipped with a reason** when
   `get_settings().catalog_path(tier)` is absent; session fixtures `contract`, `fixture_root`, `fixture_catalog`
   (from item 4), `tier`; `pytest_plugins = ["pytester"]` in `tests/conftest.py` for item 6 (amended EP-7). poe
   tasks `test` (fixture, EP-1), `test-dev` (`pytest --tier dev`), `test-full` (`poe check` stays fixture-only);
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
  markers) still green — EP-0 … EP-11 tests unchanged (incl. `test_ep01`'s marker-registration and
  `test_ep03`'s `.env.example` parity assertions; amended EP-7).
- `uv run poe test-dev` runs and reports the dev-tier tests as **skipped** (no `dev.duckdb` yet), not failed.
- `uv run --group dev mwh fixtures build` twice ⇒ clean `git status`; 31 CSVs under `tests/fixtures/mimic-iv-3.1/`
  total ≤ 10 MB; pre-commit `mwh guard` passes.
- Commit `feat(mimicwarehouse): icu fixtures + pytest tiers + fixture catalog (EP-12)`, then
  `docs(roadmap): record EP-12 commit hash`.

## Parked → final-roadmap.md

- Hypothesis-driven property fixtures (random but schema-valid frames) for loader edge cases; trigger: a loader
  bug the fixed seed does not reproduce. *(Mirrored as v2 FIX-3, 2026-08-18.)*

> **Completion note (2026-08-18).** Executed as one autonomous session (≈ 1½ h against M ≈ 1 h), tier fixture,
> no MIMIC data touched: the inputs were the EP-9 contract, the vendored concept SQL (to confirm which itemids /
> value texts `vitalsign`, `gcs`, `ventilator_setting` / `oxygen_delivery` / `ventilation`, `urine_output`,
> `norepinephrine`, `first_day_weight`, `height`, `crrt` / `rrt`, `invasive_line` look up) and public
> documentation typed by hand into `fixtures/vocab/d_items.yaml`. Every command output was schema, counts,
> hashes, byte sizes or synthetic aggregates (GOVERNANCE §4); no CSV was ever opened by a tool.
>
> **Items 1–6 — as specified.** `src/mimicwarehouse/fixtures/{icu,catalog}.py` (new), `vocab/d_items.yaml`
> (new, ASCII — `°F` / `°C` as YAML escapes so EP-11's ASCII assertion holds), `check.py` / `write.py` /
> `vocab.py` / `spec.py` / `cli.py` / `__init__.py` extended; `tests/conftest.py` (tiers), `tests/README.md`
> (new), `tests/ep/test_ep12.py` (32 tests, marker `ep_12`), poe `test-dev` / `test-full`. Committed fixture:
> `mimicwarehouse/tests/fixtures/mimic-iv-3.1/icu/<9 tables>.csv` + the extended `manifest.json` (`modules:
> [hosp, icu]`, 31 files) + `README.md` — seed 2026, **75 ICU stays** (= `plan.icu_segments`; `stay_id` from
> 90 000 000), 15 caregivers, `chartevents` 20,125 rows / 1,962,815 bytes (≤ 3 MB), `outputevents` 1,857,
> `ingredientevents` 364, `inputevents` 285, `procedureevents` 136, `datetimeevents` 116, `d_items` 47,
> `icustays` 75; **whole fixture 31 CSVs, 50,974 rows, 5,370,673 bytes = 5.12 MiB** (≤ 10 MB). **The 22 hosp
> CSVs did not change by a byte** (per-table child generators; `git status` shows only the icu tree, manifest and
> README). `uv run mwh fixtures build` twice ⇒ identical manifest sha256; a build takes ≈ 1.6 s wall.
> `validate(hosp, contract, plan, icu=icu)` clean (icu columns / dtypes / NOT NULL, id floor, 22 icu contract
> FKs + 6 caregiver links, PKs / uniqueness hints, sort keys, `icustays` inside its admission with exactly one
> matching `transfers` row and equal to the plan, `los` = window, every event inside `[intime − 6 h, outtime +
> 6 h]` with its stay's ids, `storetime ≥ charttime`, `endtime ≥ starttime`, every `itemid` in `d_items` with
> the table's `linksto`); all 9 files load through DuckDB `read_csv(columns=<contract types>, header=true,
> ignore_errors=false)` with the manifest's row counts and zero rejects; **planted signal across modules**: all
> 6 planted sepsis ICU stays carry norepinephrine (`inputevents` 221906, starting exactly when the hosp
> prescription starts) + blood culture + IV antibiotic, all 6 planted AKI ICU stays carry oliguric hourly Foley
> output + `Dialysis - CRRT`; 30 of 75 stays ventilated (`Intubation` → `Invasive Ventilation` → `Extubation`,
> FiO2 / PEEP / tidal volume / ventilator mode / `Endotracheal tube` rows inside the window, propofol in 26), 4
> NIV, 43 arterial lines (ABP instead of NBP while in), 23 vasopressor stays in total, 5 insulin drips, 64
> Foleys, 9 ICU deaths (vent to `outtime`, no extubation). `build_fixture_catalog()` → 31 tables in **0.8 s cold
> / 0.2 s warm** (budget < 5 s; comments on). Tier markers: `pytest` (default) deselects the two dev/full
> probes → **394 passed, 2 deselected**; `poe test-dev` → **394 passed, 1 skipped** (`dev tier: catalog not
> found (C:\mimicdata\warehouse\dev.duckdb); EP-21 builds it`), `poe test-full` → 2 skipped; `PYTEST_TIER=demo`
> and `tier('nope')` are usage errors, `@pytest.mark.tierx` a strict-markers error (all under `pytester` against
> a throw-away data root, so the cases do not depend on this machine's `C:\mimicdata`). `poe check` green
> (ruff, pyright, 394 tests), `mwh verify EP-12` green, `mwh verify EP-12 -- --tier dev` passes through
> (32 passed, 1 skipped). `pre-commit run <end-of-file-fixer | trailing-whitespace | check-json |
> check-added-large-files> --files <the 11 new/changed fixture files>` all Passed **and rewrote nothing**
> (manifest sha256 unchanged); `mwh guard` clean over the fixture tree + every new / changed source, test and doc
> file (64 files); `mwh --help` still imports no numpy / polars / duckdb (asserted). DESIGN §4, §15, §20 notes
> written; parked item mirrored into `final-roadmap.md` (FIX-3).
>
> **Choices worth knowing (all inside the brief; none changes an interface a later EP cites):**
> - **`validate(frames, contract, plan, *, icu=None)`** — the EP-11 hosp-only form is unchanged; passing
>   `icu=` adds the icu checks (structural first, then cross-schema FKs and semantics), which is how the hand-off
>   asked for the schema argument to be generalised without a second entry point. `write_fixture` likewise
>   accepts the EP-11 flat `{table: frame}` **or** `{module: {table: frame}}`; `build_frames(spec)` returns
>   `(plan, {"hosp", "icu"})` for callers that want the whole fixture in memory.
> - **`spec.n_caregivers` / `vent_fraction` / `vasopressor_fraction`** are `FixtureSpec` fields (defaults 15 /
>   0.4 / 0.25 → ≈ 40 % ventilated incl. boosts for sepsis / ICU deaths, ≈ 30 % vasopressor stays incl. the six
>   planted). `build_plan` ignores them, so `spec.canonical()` grew three keys in the manifest but no hosp byte
>   moved. `GENERATOR_VERSION` stays `0.1.0` (hand-off rule: bump only when a hosp byte changes).
> - **Fixture-only itemids** for `datetimeevents` (2401xx: Foley / arterial-line insertion date, last dialysis)
>   and `ingredientevents` (2402xx: Water / Sodium / Dextrose): no vendored concept reads those tables and the
>   real ids were not certain from public docs, so the YAML says they are fixture-only rather than risk a
>   plausible-but-wrong "real" id (the EP-11 micro-item precedent). Everything a concept looks up by number is
>   the real MetaVision id. `d_items.param_type` texts (`Numeric` / `Text` / `Solution` / `Processes` / `Date
>   and time`) and `procedureevents.ordercategorydescription` (`ProcessDuration` / `Task`) are approximate
>   upstream vocab, not copies. `unitname` for the two temperature items is the real `°F` / `°C` (UTF-8 in the
>   CSV, ASCII escapes in the YAML).
> - **Simplifications the realism target allows** (say so once): one caregiver per 12-h block (day / night
>   nurse per stay); `chartevents.storetime` = `charttime` + 1–90 min, never NULL; drips carry `originalamount`
>   = the bag content and `originalrate` = the first segment's rate; `procedureevents.originalamount /
>   originalrate` NULL; `continueinnextdept` always 0; every event strictly inside `[intime, outtime]` (the
>   validate window keeps the ± 6 h slack the brief specified for future generators); `Intubation` /
>   `Extubation` rows are 1-minute `Task` rows with `value` 1 and `valueuom` `None`.
> - **`PYTEST_TIER` handled at `pytest_configure`** (`config.stash[TIER_KEY]`), so a bad value is one usage
>   error before collection; the skip reason names the missing catalog path; `catalog_status` swallows a
>   settings failure into the skip reason so an unsafe data root cannot turn a dev/full test into an error.
> - **Two marker-mechanics probes live in the real suite** (`test_dev_tier_catalog_opens_read_only`,
>   `test_full_…`): deselected by default, skipped under `--tier dev` / `--tier full` until EP-21, then they only
>   open the catalog file read-only and run `SELECT 1`. They exist so `poe test-dev` visibly reports a skip
>   (the acceptance line); they are not dev-tier *content* — that stays EP-17's.
>
> **Deviations / FYIs.** (1) `tests/ep/test_ep11.py` needed three small edits (the brief's "EP-0 … EP-11 tests
> unchanged" is read as *still green*, and EP-11's own drift test says "EP-12 extends both"): the `regenerated`
> fixture now runs `build_and_write` (whole tree) because the manifest / README byte comparison covers both
> modules — it still asserts the hosp shas equal a hosp-only `write_fixture` — and the CLI test's file counts
> read 31 (with the 22-hosp count asserted on the directory / JSON). No hosp assertion was weakened. (2)
> `tests/ep/test_ep06.py::test_mwh_verify_usage_errors` probed EP-12 as "a code brief without a test module";
> it now probes **EP-17** (EP-13..16 are `n/a` docs briefs that verify cleanly) — the same rolling edit EP-11
> made. (3) `test_ep06`'s pass-through assertion (`… -- -q -k help --tier dev`) already covered the `--tier`
> pass-through; EP-12 adds `verify.pytest_argv(12, ["--tier", "dev"])` to its own test only. (4)
> Non-heredoc discipline (D-42) held: files were written with Write/Edit; one accidental `python -` stdin
> invocation hung for two minutes and was the session's only tooling incident (nothing was quarantined).
>
> **Hand-off to EP-17/21:** point the loader at `tests/fixtures/mimic-iv-3.1` (`hosp/` + `icu/`, raw layout,
> contract column order); `fixtures.catalog.READ_CSV_SQL` is the strict `read_csv` clause every file passes;
> the first `@pytest.mark.tier("dev")` tests go in `test_ep17.py` and skip cleanly until EP-21 writes
> `dev.duckdb`; EP-21 can replace the in-memory `fixture_catalog` with `fixture.duckdb` built from the same
> CSVs without touching the conftest interface (`build_fixture_catalog(root, contract=…)`).
