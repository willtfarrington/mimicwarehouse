# EP-34 — Time semantics + unit-of-analysis registry

**Size:** M · **Tier:** fixture+dev · **Core/Stretch:** core · **Depends on:** EP-21 (Catalog builder (per-tier .duckdb)) · **Blocks:** EP-46 (Cohort spec + registry), EP-49 (Event-aligned timeline API), EP-54 (Re-plan P3), EP-68 (Prevalence/incidence/event-rate module), EP-75 (Endpoints A: binary/continuous/count/ordinal)

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
