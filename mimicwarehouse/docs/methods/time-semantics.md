# Time semantics and units of analysis (EP-34)

The one written answer to "what is time in MIMIC-IV and what is a row?" — the prose twin
of `src/mimicwarehouse/timesem.py` (DESIGN §7, §15). Every cohort, timeline, rate,
endpoint and temporal split from P3 on imports that module instead of re-deriving these
rules; this page explains them for readers and reviewers. Nothing here is derived from
data: the tables are rendered from the module's constants (`python -m
mimicwarehouse.timesem` re-renders the marked blocks; `test_ep34` asserts they are in
sync). All MIMIC-IV analyses in this repository are retrospective.

## 1. The date-shift rule

PhysioNet de-identifies MIMIC-IV by shifting every patient's timestamps by a
patient-specific offset into the future (years 2100–2200 in the shipped data). Within one
patient every timestamp carries the same offset, so **within-patient intervals are real**;
across patients the calendar is meaningless — two admissions dated the same week may be a
decade apart in reality. Consequences the module enforces:

- Timestamps are stored and passed around as naive `TIMESTAMP` exactly as shipped; the
  module never localizes, formats or re-bases them.
- No analysis uses a calendar date as a cross-patient axis (no "admissions per calendar
  year", no seasonality by shifted month). The only cross-patient temporal axis is the
  era (§2). A test greps the module for calendar-formatting functions.
- Analyses use **relative** time (§5): hours or days since a within-patient anchor.

## 2. The era axis: `anchor_year_group`

`patients.anchor_year_group` is the one de-identification-safe statement about *when* a
patient's `anchor_year` fell: five 3-year eras. `timesem.ERAS` carries MIMIC's literal
spelling, `timesem.era_of(label)` resolves a label to an `Era` (label, index 0–4, year
span) and `timesem.sql_era_index(expr)` is the SQL `CASE` that yields the index (NULL for
anything else). Temporal holdouts (EP-104/129) split on this axis and nothing else; the
tracer (EP-31) enters it as a covariate, never as a calendar.

<!-- eras:begin -->
| era_index | anchor_year_group | years |
|---|---|---|
| 0 | `2008 - 2010` | 2008-2010 |
| 1 | `2011 - 2013` | 2011-2013 |
| 2 | `2014 - 2016` | 2014-2016 |
| 3 | `2017 - 2019` | 2017-2019 |
| 4 | `2020 - 2022` | 2020-2022 |
<!-- eras:end -->

`mimiciv_derived.hadm_era` (§8) carries `era_index` per admission, so "one row per era"
checks read `SELECT era_index, count(*) AS n FROM mimiciv_derived.hadm_era GROUP BY 1`
through `mwh sql`.

## 3. Age and the 91 cap

`patients.anchor_age` is the patient's age in `anchor_year`. The age at any event is

    age_at(anchor_age, anchor_year, event) = anchor_age + (year(event) - anchor_year)

— `timesem.age_at` in Python, `timesem.sql_age_at(anchor_age, anchor_year, event_ts)` in
SQL (`cap=True` wraps it in `least(..., 91)`, the tracer's `age_at_admit`). Both years
carry the same shift, so the difference is real; this is the **only** place the module
reads a calendar year. PhysioNet ships every age of 89 and over as **91**
(`timesem.AGE_CAP`), so a computed age that reaches 91 is a censored bucket, not a value:
`timesem.is_age_capped(age)` (SQL: `sql_is_age_capped`) is true at and above 91 — a
genuine 91 cannot be told apart from the sentinel and is treated the same way.
`timesem.AGE_BANDS` / `sql_age_band` are the tracer's descriptive bands (18–39, 40–64,
65–79, 80+; upper bounds exclusive, cohort floor 18).

## 4. The ICD version rule

Diagnosis coding switched from ICD-9-CM to ICD-10-CM around 2015. Because of the date
shift the switch is **not** visible by calendar; it is visible per row as
`diagnoses_icd.icd_version` (and `procedures_icd.icd_version`). An admission is classified
from its rows — `icd9` (every row 9), `icd10` (every row 10) or `mixed` — by
`timesem.icd_versions_of_hadm(versions)` / `sql_icd_versions(expr)`; an admission without
diagnosis rows is `NULL`. Code sets (EP-40) are therefore always **dual** (ICD-9 and
ICD-10 members), and `hadm_era.icd_versions` is the per-admission label a phenotype or a
QC check stratifies on.

## 5. Relative time and the naming convention

Every within-patient time is a signed offset from a named anchor:

| name | anchor | SQL (`timesem`) |
|---|---|---|
| `hours_since_icu_intime` | `icustays.intime` | `date_diff('second', intime, event) / 3600.0` |
| `hours_since_hosp_admit` | `admissions.admittime` | `date_diff('second', admittime, event) / 3600.0` |
| `hours_before_discharge` | `admissions.dischtime` | `date_diff('second', event, dischtime) / 3600.0` |

`timesem.sql_hours_since(anchor, event)` / `sql_days_since` build the expressions (negative
before the anchor); `timesem.RELATIVE_TIMES` holds the three named axes (`RelativeTime`).
Bins are **half-open**: `hour_bin(hours, width_h)` is `floor(hours / width_h)`, so bin *i*
covers `[i * width, (i + 1) * width)` — hour 1.0 exactly belongs to bin 1, and hours before
the anchor fall into negative bins (`sql_hour_bin` is the SQL twin, `bin_bounds` the
inverse). The timeline API (EP-49) and the hourly marts (EP-56) use exactly these.

Day-resolution columns (`patients.dod`, `procedures_icd.chartdate`, `omr.chartdate`, ...)
are `DATE`s: an hour offset computed from them is meaningless below one day. The module
does not guard this yet (a contract-level `time_resolution` marker is parked; retro
ARCH-15) — consumers compare `dod` against a timestamp as a whole day.

## 6. `dod`, the visibility horizon and censoring rules

`patients.dod` (date of death, hospital and state records) is populated only up to about
**one year after the patient's last hospital discharge** (`timesem.DOD_VISIBILITY_DAYS`
= 365); a later death is invisible and a NULL `dod` beyond that horizon means *unknown*,
not *alive*. Every out-of-hospital mortality outcome therefore carries an explicit
`CensoringRule` (outcome, horizon in days, anchor, competing events): follow-up ends at

    censor_time = min(anchor + horizon, last_dischtime + 365 d)

(`timesem.follow_up_end` / `CensoringRule.censor_time` in Python, `sql_follow_up_end` /
`CensoringRule.sql_censor_time` in SQL). The defaults:

<!-- censoring:begin -->
| outcome | anchor | horizon / censoring | competing events |
|---|---|---|---|
| `in_hospital_mortality` | `dischtime` | none (resolved at discharge) | `discharge_alive` |
| `mortality_30d` | `index_time` | 30 d after `index_time`, censored at min(anchor + horizon, last_dischtime + 365 d) | - |
| `mortality_90d` | `index_time` | 90 d after `index_time`, censored at min(anchor + horizon, last_dischtime + 365 d) | - |
| `mortality_1y` | `index_time` | 365 d after `index_time`, censored at min(anchor + horizon, last_dischtime + 365 d) | - |
<!-- censoring:end -->

`index_time` is the cohort's index event (the compiler binds it — the ICU `intime` for a
first-ICU-stay cohort); a protocol that wants post-discharge follow-up replaces the anchor
(`dataclasses.replace(rule, anchor="dischtime")`). For horizons of one year or less the
visibility horizon never binds when the anchor precedes the last discharge, but the rule
stays explicit so a longer follow-up or a person-time analysis (EP-68) cannot silently
treat invisible deaths as survival.

**Competing events.** Discharge alive is the competing event of every in-hospital outcome
(`timesem.DISCHARGE_ALIVE` on `in_hospital_mortality`): a patient discharged alive is not
"censored" for in-hospital death, the event became impossible. For readmission and
utilization outcomes (EP-84) death within the horizon is the competing event; survival
briefs (EP-91/93) model these as competing risks, never as independent censoring.

## 7. The unit-of-analysis registry

Every cohort spec, mart and model dataset declares its **grain** — what one row is — by
name from `timesem.GRAINS`; compilers refuse a placeholder grain (`available = false`)
until the EP named in `available_from` ships. Each grain carries its key columns
(`Grain.keys_sql()`), its source table, its time anchor and its applicable index-event
rules; `Grain.index_event_sql(rule)` renders a deterministic SQL fragment (a SELECT that
yields the keys, `index_time` and `end_time`; `icu_day` / `hour_bin` expand each stay into
`[start, end)` bins numbered from 0 with `bin_start` / `bin_end`).

<!-- grains:begin -->
| grain | keys | source | time anchor | index rules (default in bold) | available | what a row is |
|---|---|---|---|---|---|---|
| `subject` | `subject_id` | `mimiciv_hosp.patients` | - | **first_hadm**, first_icu_stay | yes | one row per patient; index events come from the rule (first admission or first ICU stay) |
| `hadm` | `hadm_id` | `mimiciv_hosp.admissions` | `admittime` | **each_hadm**, first_hadm | yes | one row per hospital admission |
| `icustay` | `stay_id` | `mimiciv_icu.icustays` | `intime` | **each_icustay**, first_icu_stay, first_icu_stay_of_first_hadm | yes | one row per ICU stay |
| `icu_day` | `stay_id`, `day_index` | `mimiciv_icu.icustays` | `intime` | **each_icustay**, first_icu_stay, first_icu_stay_of_first_hadm | yes | ICU stay x day index from intime ([start, end) days, day 0 first) |
| `hour_bin` | `stay_id`, `hour_bin` | `mimiciv_icu.icustays` | `intime` | **each_icustay**, first_icu_stay, first_icu_stay_of_first_hadm | yes | ICU stay x hour index from intime ([start, end) hours, bin 0 first) |
| `person_time` | `subject_id`, `interval_start`, `interval_end` | `mimiciv_hosp.patients` | - | - | yes | subject x [start, end) follow-up interval; intervals are built by the rates module |
| `edstay` | `stay_id` | `mimiciv_ed.edstays` | `intime` | - | no (placeholder until EP-142) | one row per ED stay — placeholder until the ED linkage ships (P9) |
| `note` | `note_id` | `mimiciv_note` | `charttime` | - | no (placeholder until EP-148) | one row per clinical note (segregated lake, owner-only) — placeholder until notes staging ships (P10) |
<!-- grains:end -->

Index-event rules (`timesem.INDEX_RULES`; `timesem.index_event_sql(rule)`):

| rule | one row per | ordering |
|---|---|---|
| `first_icu_stay` | subject — the first ICU stay over all admissions (the tracer's cohort; equals `icustay_index.first_icu_stay_of_subject`) | `intime, stay_id` |
| `first_hadm` | subject — the first hospital admission | `admittime, hadm_id` |
| `each_hadm` | hospital admission | — |
| `each_icustay` | ICU stay | — |
| `first_icu_stay_of_first_hadm` | subject — the first ICU stay *inside the first admission* (no row when that admission had no ICU stay) | `admittime, hadm_id` then `intime, stay_id` |

Ties are broken by the id column, so every rule is a total order and reproducible across
rebuilds (DESIGN §7 sort-key rule). `person_time` has no index template: its
`[interval_start, interval_end)` rows are built by the rates module (EP-68).

## 8. Catalog surfaces

`timesem.create_views` is registered in `catalog.build.CATALOG_EXTENSIONS` and runs inside
every `mwh build --tier <t> --select catalog` on the build connection, so all four tier
catalogs carry (rebuild + swap; close readers first on Windows):

- `mimiciv_derived.hadm_era` — one row per admission: `subject_id`, `hadm_id`,
  `anchor_year_group`, `era_index`, `age_at_admit` (capped), `age_capped`, `icd_versions`;
- `mimiciv_derived.icustay_index` — one row per ICU stay: `stay_id`, `hadm_id`,
  `subject_id`, `intime`, `outtime`, `icu_seq_in_hadm`, `icu_seq_in_subject`,
  `first_icu_stay_in_hadm`, `first_icu_stay_of_subject`;
- `meta.grains` — the registry table (`name`, `keys`, `source`, `time_anchor`,
  `default_index_rule`, `index_rules`, `available`, `available_from`, `description`).

Views only (cheap joins over the tier's own `patients` / `admissions` / `diagnoses_icd` /
`icustays`, so the dev bucket filter is inherited); a view whose sources are not cataloged
is skipped with a warning, never created empty. `mwh catalog info --tier <t>` lists them
under "meta / derived / marts objects"; a session reads them only through `mwh sql` /
`safe_query` (aggregates — the two views carry identifier columns).

## 9. What this page deliberately does not decide

- Cohort spec fields that reference grains and eras (EP-46), the timeline API's ASOF /
  window joins (EP-49), person-time intervals and rate estimators (EP-68), endpoint
  construction on these rules (EP-75/76).
- The `edstay` (EP-142) and `note` (EP-148) grains are named so specs can cite them; they
  stay placeholders until their staging briefs flip `available`.
- A day-resolution guard for `DATE` time columns (retro ARCH-15) — parked, see §5.
