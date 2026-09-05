# EP-34 — Time semantics + unit-of-analysis registry

**Size:** M · **Tier:** fixture+dev · **Core/Stretch:** core · **Depends on:** EP-21 (Catalog builder (per-tier .duckdb)) · **Blocks:** EP-37 (Concept runner (mimic-code concepts_duckdb → mimiciv_derived) ⏱), EP-46 (Cohort spec + registry), EP-49 (Event-aligned timeline API), EP-50 (Events spine (MEDS-compatible) ⏱), EP-54 (Re-plan P3), EP-68 (Prevalence/incidence/event-rate module), EP-75 (Endpoints A: binary/continuous/count/ordinal)

> **EP-33 amendment (2026-09-01).** Header facts unchanged; shorthand per the README notation
> table. (1) **Absorbs the tracer's age-band / era inlining** (EP-33 D1 queue). EP-31 inlined
> an age-band `CASE` (`tracer._age_band_case`), the era covariate (`anchor_year_group`) and the
> age rule (`anchor_age + year(event) - anchor_year`, cap 91) directly in `tracer.py` and
> `sql/tracer_first_icu_mortality.sql` as a stopgap. Item 1 lifts them into `timesem` as the
> registry's first two entries — the era axis (`ERAS`/`era_of`/`sql_era_index`) and the age
> rule (`age_at`/`AGE_CAP`/`sql_age_at`, plus a named age-band fragment with the tracer's cut
> points; exact name at the implementer's discretion) — and this brief regenerates the tracer
> SQL to cite them: **no restage, no change to the tracer's numbers** (a count-for-count
> comparison of the tracer descriptives on dev before/after is the acceptance). (2) **Shipped
> names to code against.** Catalogs open through `mimicwarehouse.catalog.connect.open_catalog(
> tier, *, settings=None, role=None, path=None)` (READ_ONLY, hardening SQL, `meta.catalog_info`
> checks); item 5's `CATALOG_EXTENSIONS` hook is added to `catalog.build.build_catalog`, which
> the `catalog` step (`dag.runner.STEP_HANDLERS["catalog"]`) already calls on the build
> connection from `mimicwarehouse.engine.open_duckdb("build", ...)` — extensions receive that
> connection and never open their own; the rebuild-and-swap of the catalog file is
> `publish.swap_file` (`catalog.build.swap_catalog` is removed by EP-33). (3) `meta.grains`
> joins the EP-29 `meta.*` family (`meta.tables`, `meta.columns`, `meta.row_counts`,
> `meta.itemids`, `meta.catalog_info`, `meta.catalog_tables`) and is listed by `mwh catalog
> info`. (4) Item 6's dev assertion (`SELECT era_index, count(*) ... GROUP BY 1`) runs through
> `safe_query`/`mwh sql` (`safe_query(sql, *, tier=None, k=None, ...)` — tier/k default from
> settings since EP-33 B1) and already carries the mandatory real count-family node; the
> `mimiciv_derived.*` views are non-registry reads (subject-keyed for the 64-char free-text
> check — ledger P3C-5), so `icd_versions` stays a short label. Fixture counts in tests come
> from `tests/fixtures/manifest.json` (EP-33 TST-2), not literals.

## Context

Every cohort, timeline, rate, endpoint and temporal split in P3–P8 needs one shared answer to
"what is time in MIMIC-IV and what is a row?". This brief writes that answer once, as code, in
`src/mimicwarehouse/timesem.py` (DESIGN §7, §15) so later briefs import it instead of
re-deriving it. The catalog (EP-21) exposes `mimiciv_hosp.patients / admissions / transfers` and
`mimiciv_icu.icustays` per tier; the fixture tier (EP-11/12) is synthetic with ids ≥ 90 000 000.
MIMIC caveats that this module encodes: PhysioNet's per-patient date shift means calendar time
is meaningless across patients — `anchor_year_group` (five 3-year eras, 2008–2010 … 2020–2022)
is the **only** cross-patient temporal axis; `dod` is populated only up to ~1 year after a
patient's last discharge, so every mortality outcome needs an explicit censoring horizon;
ICD-9 → ICD-10 coding switched around 2015 (visible per row as `icd_version`, never by
calendar); ages ≥ 89 are shipped as 91; discharge-alive is a competing event for in-hospital
outcomes. Owner decisions implemented: D-17 (DuckDB/Polars), D-18 (tiers), the unit-of-analysis
registry and `dod` censoring rule listed under "Defaults" in DECISIONS.md.

## In scope

1. **Eras and ages** (`src/mimicwarehouse/timesem.py`) — `ERAS: tuple[str, ...]` in
   MIMIC's literal spelling (`"2008 - 2010"` … `"2020 - 2022"`), `Era` (label, index 0–4,
   start/end years), `era_of(anchor_year_group) -> Era`; `age_at(anchor_age, anchor_year,
   event_ts) -> float` (= anchor_age + year(event) − anchor_year), `AGE_CAP = 91`,
   `is_age_capped(age)`; `icd_versions_of_hadm` helper that classifies an admission as
   `icd9` / `icd10` / `mixed` from `diagnoses_icd.icd_version` (never from dates). Also SQL
   snippet builders returning DuckDB expressions for the same quantities (`sql_age_at(...)`,
   `sql_era_index(...)`) so the cohort compiler (EP-47) and marts (EP-55) embed identical logic.
2. **Relative time** — `RelativeTime` helpers: `sql_hours_since(anchor_expr, event_expr)`
   (`date_diff('second', anchor, event) / 3600.0`), `sql_days_since`, `hour_bin(hours,
   width_h=1)` with `[start, end)` semantics, and the naming convention used everywhere:
   `hours_since_icu_intime`, `hours_since_hosp_admit`, `hours_before_discharge`. Timestamps stay
   naive `TIMESTAMP` exactly as shipped (DESIGN §7); the module never localizes.
3. **`dod` censoring rule + competing events** — `CensoringRule` (outcome name, horizon days,
   anchor: `dischtime` | `intime` | `index_time`, competing events list) with defaults:
   `in_hospital_mortality` (no censoring; discharge-alive competing), `mortality_30d` /
   `mortality_90d` / `mortality_1y` (censor at min(anchor + horizon, last_dischtime + 365 d) —
   the `dod` visibility horizon), and `follow_up_end(last_dischtime, horizon_days)`; the rule
   is documented in `docs/methods/time-semantics.md` (new) with the caveat list above.
4. **Unit-of-analysis (grain) registry** — `Grain` (name, key columns, source table/view,
   time anchor column, default index-event rule, available_from EP) and `GRAINS` for `subject`,
   `hadm`, `icustay`, `icu_day` (icustay × day index from `intime`), `hour_bin` (icustay ×
   hour index), `person_time` (subject × [start, end) interval), plus placeholders `edstay`
   (P9, EP-142) and `note` (P10, EP-148) flagged `available=False` so specs can name them but
   compilers refuse them. Index-event rules as named SQL templates: `first_icu_stay`,
   `first_hadm`, `each_hadm`, `each_icustay`, `first_icu_stay_of_first_hadm`; `grain.keys_sql()`
   and `grain.index_event_sql(rule)` return deterministic SQL fragments.
5. **Catalog views** — add a small extension hook to EP-21's `build_catalog`
   (`catalog/build.py`: `CATALOG_EXTENSIONS: list[Callable[[con, tier], None]]`, called after the
   contract tables and before `CHECKPOINT`) and register `timesem.create_views(con, tier)`, which
   creates two views in every tier catalog: `mimiciv_derived.hadm_era` (subject_id, hadm_id,
   anchor_year_group, era_index, age_at_admit, age_capped, icd_versions) and
   `mimiciv_derived.icustay_index` (stay_id, hadm_id, subject_id, intime, outtime,
   icu_seq_in_hadm, first_icu_stay flag, first_icu_stay_of_subject flag) plus a `meta.grains`
   table listing the registry (name, keys, anchor, rule, available). Views only (cheap joins over
   `patients/admissions/diagnoses_icd/icustays`), no materialization; they appear after
   `mwh build --tier <t> --select catalog` (rebuild + swap; close readers first on Windows).
6. **Tests + docs** — `tests/ep/test_ep34.py` (`@pytest.mark.ep_34`; fixture default, `dev`
   marker for the catalog views): era mapping for all five labels; age cap detection on a
   crafted frame; relative-time signs and bin boundaries (`[start, end)`); censoring horizon
   math; each grain's SQL fragments compile against the fixture catalog; on dev, both views
   exist and `SELECT era_index, count(*) … GROUP BY 1` returns exactly five eras (aggregate).

## Out of scope

- Prevalence/rate estimators over person-time → EP-68 (rates module).
- Endpoint construction (binary/TTE outcomes using these rules) → EP-75/76.
- Timeline anchors and ASOF/window joins → EP-49 (imports `sql_hours_since` from here).
- Cohort spec fields that reference grains/eras → EP-46; temporal holdout by era → EP-104/129.
- ED (`edstay`) and note grains become real in EP-142 / EP-148 — placeholders only here.

## Verification / acceptance

- `uv run poe test -m ep_34` green on fixture and dev; `uv run --group dev mwh verify EP-34` green.
- `uv run --group dev mwh build --tier dev --select catalog` registers `mimiciv_derived.hadm_era`,
  `mimiciv_derived.icustay_index` and `meta.grains`; `uv run --group dev mwh sql "SELECT era_index,
  count(*) AS n FROM mimiciv_derived.hadm_era GROUP BY 1 ORDER BY 1"` returns five rows on dev.
- `docs/methods/time-semantics.md` exists and lists: date-shift rule, era axis, `dod` horizon,
  ICD version rule, age cap, competing-event note, grain table (generated from `GRAINS`).
- No calendar-date function (`year(admittime)` used as a cross-patient axis, `strftime` on shifted
  dates) appears in the module except inside `age_at`; a test greps the module for `strftime`.

## Parked → final-roadmap.md

- **Day-resolution guard for `DATE`-grain time columns** (`patients.dod`, `procedures_icd` /
  `hcpcsevents` / `omr.chartdate`, the microbiology `chartdate` fallback; retro ledger ARCH-15): a
  contract `time_resolution: day|second` marker that `timesem`'s relative-time builders and the
  EP-50 spine would honour. v1 documents the rule in `docs/methods/time-semantics.md` §5 (an hour
  offset from a `DATE` column is meaningless below one day; consumers compare `dod` as a whole
  day) and does not enforce it — a contract change moves `structural_hash()` and forces a fixture
  regeneration, so the natural slot is EP-41's 0.3.0 or v2 (mirrored as v2 TIME-1).

> **Completion note (2026-09-05).** Shipped as `src/mimicwarehouse/timesem.py` (stdlib-only;
> `tracer.py` imports it on the `mwh` start-up path, budget-tested), `docs/methods/time-semantics.md`
> (its era / censoring / grain tables are rendered from the module by `python -m
> mimicwarehouse.timesem` and drift-tested), the `catalog.build.CATALOG_EXTENSIONS` hook with
> `timesem.create_views` registered (`mimiciv_derived.hadm_era`, `mimiciv_derived.icustay_index`,
> `meta.grains`; a view whose sources are not cataloged is skipped with a warning, never created
> empty; a failing extension fails the build and removes the `.new`), the `meta / derived / marts
> objects` block of `mwh catalog info` (`objects` in `--json`), `tests/ep/test_ep34.py` (17 fixture
> + 2 dev tests) and the DESIGN §7 note / §15 row. **Item 1 (tracer lift):** `sql/tracer_first_icu_
> mortality.sql` embeds `timesem.sql_age_at(...)` / `sql_age_at(..., cap=True)` verbatim (the
> WITH clause minus comments is byte-identical to EP-31's, so the audited statement hashes are
> unchanged), `tracer.descriptives` bands with `timesem.sql_age_band`, `tracer.AGE_BANDS`
> re-exports `timesem.AGE_BANDS`; acceptance = count-for-count identity on dev: run
> `20260905T161857-dev` (before) vs `20260905T164312-dev` (after) — cohort n = 3,208, model fit,
> AUC 0.744, 7 audited calls, as in EP-31; a scratch byte-diff of `attrition.json` /
> `descriptives.json` / `model.json` differed only in the seven audit ids; `test_ep34` repeats the
> comparison against the frozen EP-31 chain through `safe_query` on fixture and dev. **Gates:**
> `poe check` 853 passed (297 s); `mwh verify EP-34` 17 passed (47 s); `pytest -m ep_34 --tier dev`
> 19 passed (51 s); `mwh verify` EP-21 · EP-29 · EP-30 · EP-31 · EP-33 all exit 0. **Catalogs:**
> `mwh build --tier {dev,full,demo} --select catalog` 2.8 s / 2.5 s / 2.7 s, 31 cataloged each,
> core snapshot ids unchanged; the acceptance statement `SELECT era_index, count(*) AS n FROM
> mimiciv_derived.hadm_era GROUP BY 1 ORDER BY 1` on dev returned exactly five rows (era_index
> 0–4, 0 rows suppressed, audit `bac8e8d6dc254a9d8843457e4ae04170`). Timings were measured with
> the Windows power mode at *Better performance* (the owner's Best-performance toggle was off
> and the session was unattended; nothing here is a long job — the first cold dev tracer run took
> 10.7 s, the warm one 2.7 s). **Earlier test touched (README acceptance clause):**
> `tests/ep/test_ep29.py::test_comments_visible_via_duckdb_columns` counted commented objects in
> every `mimiciv_%` schema and pinned 31; `mimiciv_derived` now carries two commented views (EP-37's
> concepts will add more), so the filter names the two contract schemas — a dated `# EP-34:` comment
> marks it. **Judgment calls (owner review):** (1) `icustay_index`'s flags are named
> `first_icu_stay_in_hadm` / `first_icu_stay_of_subject` rather than the brief's `first_icu_stay` /
> `first_icu_stay_of_subject`, because the index rule `first_icu_stay` means the subject-level
> first stay (the tracer's cohort) and a same-named per-admission flag would invert its meaning;
> (2) `mortality_30d/90d/1y` default to `anchor = "index_time"` (the cohort's index event, the
> ICU `intime` for a first-stay cohort), `dischtime` variants via `dataclasses.replace`;
> (3) `is_age_capped` is true at 91 itself (a genuine 91 is indistinguishable from PhysioNet's
> ≥ 89 sentinel) and `hadm_era.age_at_admit` is the capped value like the tracer's;
> (4) `hadm_era.icd_versions` is `NULL` for an admission without diagnosis rows; (5) `person_time`
> is registered `available=True` but has no index-event template (EP-68 builds the intervals);
> (6) `dag.benchmarks.replace_marked_block` gained optional `begin` / `end` markers instead of a
> second splice helper; (7) the Python relative-time twins count whole-second boundaries exactly
> like `date_diff('second')`. Parked: the `DATE`-resolution guard (above; v2 TIME-1).
> **Owner decisions (2026-09-05, session-end review):** commit as the standard two-step pair
> (this note included); keep the `first_icu_stay_in_hadm` / `first_icu_stay_of_subject` flag
> names (judgment call 1); keep `index_time` as the default mortality anchor (2); keep the
> narrowed `test_ep29` schema filter rather than dropping the view comments. The Windows power
> mode was *Better performance* for the whole session (FYI, no action taken — never changed by
> a session).
