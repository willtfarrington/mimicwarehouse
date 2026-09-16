# Cohort specs (EP-46)

The one written answer to "how is a study population defined, versioned and checked
here?" - the prose twin of `src/mimicwarehouse/cohort/` (DESIGN §9, §15). A cohort spec
is a declarative YAML: a **grain** (what one row is, from the EP-34 unit-of-analysis
registry), an **index event**, ordered **inclusion / exclusion criteria**, an
**observation window**, a **washout**, a **follow-up** outcome, an **era filter** and the
code sets (EP-40) and phenotypes (EP-41) it references by `id@version`. It is versioned
and hashed like a code set, registered in `meta.cohort_specs`, and shared by the compiler
(EP-47: one deterministic CTE per criterion, materialisation under the marts layer, the
attrition table), the attrition diagram (EP-48), the Cohort Builder page (EP-62, through
the JSON schema) and the protocol schema (EP-51, by reference). Nothing on this page is
derived from patient data: the schema reference, the seed cards and the worked example
are rendered from the package (`python -m mimicwarehouse.cohort` re-renders the marked
blocks; `test_ep46` asserts they are in sync). All MIMIC-IV analyses in this repository
are retrospective.

## 1. The shape

A spec is one YAML document - a packaged spec under `src/mimicwarehouse/cohort/specs/` or
a study file under the data root (`studies/<study_id>/cohorts/`; `mwh cohort … --specs
<dir>`, with `--codesets <dir>` / `--phenotypes <dir>` for the registries its references
resolve against). The worked example is the tracer bullet's cohort (EP-31), the first
registered spec:

<!-- example:begin -->
```yaml
# mimicwarehouse cohort spec (EP-46). ASCII only; no calendar dates (per-patient date shift);
# the (id, version) pair is frozen in cohorts.lock.json - bump version to change anything
# below `grain`. The tracer bullet's cohort (EP-31, D-5): first ICU stay of adult patients
# -> in-hospital mortality, so EP-47's compiler can reproduce its attrition (base ->
# first_stay -> adult -> cohort; EP-31's `complete` step - non-null dischtime /
# hospital_expire_flag - drops nothing on MIMIC-IV 3.1 and is implied by the outcome).
id: first_icu_adults
version: "1.0.0"
title: First ICU stay of adult patients (the tracer bullet's cohort)
description: >
  One row per patient: the first ICU stay of the subject (ordered intime, stay_id), adults
  only (age at the index >= 18; ages >= 89 are shipped as 91 and the bound is documented
  as capped), ICU stays shorter than 4 hours excluded; followed for in-hospital mortality
  with discharge alive as the competing event. The covariate window is the 24 hours
  before the ICU admission. The definition mirrors the EP-31 tracer bullet so the
  compiled attrition (EP-47) can be compared count for count.
grain: icustay
index_event: {rule: first_icu_stay}
inclusion:
  - label: adult
    description: age at the ICU admission of at least 18 (timesem.sql_age_at; capped at 91)
    age: {min: 18, at: index}
exclusion:
  - label: short_icu_stay
    description: ICU stays under 4 hours (hours_since(intime, outtime) < 4)
    los: {max_hours: 4, of: icu}
observation_window: {start_h: -24, end_h: 0}
washout: {rule: none}
follow_up: {outcome: in_hospital_mortality, competing_events: [discharge_alive]}
era_filter: []
degeneracy_probe: {columns: [gender, admission_type, first_careunit, anchor_year_group]}
references: {codesets: [], phenotypes: []}
notes: >
  Seeded by EP-46 as the first registered spec. EP-31's chain is base -> first_stay ->
  adult -> complete -> cohort; `complete` (dischtime and hospital_expire_flag not null)
  is implied by the in-hospital outcome and drops nothing on MIMIC-IV 3.1 - EP-47 verifies
  the counts against the tracer report and records any difference; the 4-hour exclusion
  is this brief's addition (EP-46 In scope 3). The degeneracy probe names the tracer's
  four covariates (EP-31 lesson: rare admission types / care units with zero deaths).
what_it_does_not_claim:
  - No causal claim - the criteria are admission characteristics, not interventions.
  - Ages >= 89 are indistinguishable (all appear as 91); `adult` keeps them.
  - The era filter is empty - anchor_year_group is a covariate axis, never a calendar.
  - The 4-hour ICU exclusion is a data-quality floor, not a clinical definition of an
    ICU stay.
```
<!-- example:end -->

Five MIMIC caveats are baked into the schema and refused at load time:

- **No calendar dates anywhere in the definition.** PhysioNet shifts every patient's
  timestamps by a patient-specific offset (DESIGN §7), so a date-like string
  (`2150-01-01`, `01/02/2150`, a SQL `DATE '…'` literal) or a YAML date object in any
  definition field is refused. Time is always **relative to the index event**; the only
  cross-patient axis is `era_filter` over `anchor_year_group` (`timesem.ERAS`).
- **Ages >= 89 are shipped as 91**, so an `age` bound inside `(89, 91]` is refused: an
  "age >= 89" criterion is written `min: 89` and documented as capped
  (`timesem.AGE_CAP`).
- **`dod` is visible for about a year after the last discharge**, so every
  out-of-hospital outcome (`mortality_30d` / `_90d` / `_1y`) is censored at
  `min(index + horizon, last discharge + 365 d)` (`timesem.CENSORING_RULES`); an
  in-hospital outcome takes no horizon, and `discharge_alive` is its optional competing
  event.
- **Grains are the registry's available ones** (`subject`, `hadm`, `icustay`, `icu_day`,
  `hour_bin`, `person_time`); a placeholder grain (`edstay` until EP-142, `note` until
  EP-148) is refused with the EP that ships it, and a named index rule must be one the
  grain lists (`first_icu_stay` is the tracer's; `first_hadm`, `each_hadm`,
  `each_icustay`, `first_icu_stay_of_first_hadm`).
- **ICD-9 / ICD-10 are dual by construction**: a `codeset` criterion references an EP-40
  set, which carries both systems (the switch is per row, never a date).

## 2. Criterion semantics

Every criterion is a mapping with a `label` (a slug, unique in the spec - the attrition
step name EP-47 uses as the CTE name) and exactly one kind key. `inclusion` keeps the
units the predicate selects, `exclusion` removes them; the compiler evaluates them in
document order, one CTE each, so the attrition table reads top to bottom. The
conventions: **windows are `[start, end)` in hours relative to the index** (negative =
before it; `timesem.sql_hours_since`); **lookbacks are within-patient relative time**
(days before the index, never a calendar); **durations and ages are half-open**; **counts
are inclusive**.

| kind | predicate (kept when true) | fields | notes |
|---|---|---|---|
| `age` | `min <= age at index < max` | `min`, `max` (years), `at: index` | `timesem.sql_age_at` from `anchor_age` / `anchor_year`; a bound in `(89, 91]` is refused - write `89` (capped) |
| `demographic` | every listed field's value is in its list (AND across fields) | `gender`, `admission_type`, `admission_location`, `discharge_location`, `insurance`, `first_careunit`, `language`, `marital_status` | equality / set membership only; `discharge_location` is known only at discharge (post-index) - allowed for a retrospective definition, stated in the docs |
| `codeset` | a code of the dual ICD set is billed | `ref` (id@version), `position: any \| primary`, `lookback: same_admission \| prior_days \| any_prior`, `prior_days` | `icd_dx` -> `diagnoses_icd`, `icd_px` -> `procedures_icd`; `prior_days` = admissions that started within N days before the index; `any_prior` = any earlier admission |
| `phenotype` | the EP-41 phenotype relates to the index as `when` says | `ref`, `when: before \| at \| within_hours`, `hours` | `before`: onset < index; `at`: the phenotype flags the index unit (a subject-grain phenotype: onset at or before the index); `within_hours`: `\|hours since index\| <= hours` - the one closed interval (as EP-41's temporal leaf) |
| `concept` | `column op value` holds on the relation for the unit | `table`, `column`, `op` (`=`, `!=`, `<`, `<=`, `>`, `>=`, `in`, `not_in`, `is_null`, `is_not_null`), `value`, `time_column`, `window` | a relation keyed by a grain key - usually a materialised `mimiciv_derived.<concept>` (EP-37/38; `validate` warns when it is not vendored), an admissions / stays attribute works too; `window` keeps rows whose `time_column` lies in `[start_h, end_h)` |
| `los` | `min_hours <= length of stay < max_hours` | `min_hours`, `max_hours`, `of: icu \| hosp` | `icu` = `hours_since(intime, outtime)` of the index ICU stay; `hosp` = `hours_since(admittime, dischtime)` of the index admission |
| `data_availability` | at least `min_rows` rows exist in the window | `table`, `itemids`, `min_rows`, `window` | a contract table with a time column; `itemids` only where the table has an `itemid` column; the window defaults to the spec's observation window |
| `prior_admissions` | `min <= n prior admissions <= max` | `min`, `max`, `lookback_days` | admissions that started before the index, within the lookback when given; inclusive counts |
| `custom_sql` | the unit's grain keys appear in the SELECT's result | `sql`, `hash` | a hand-written SELECT yielding the grain keys, semi-joined by the compiler; `hash` = sha256 of the whitespace-normalised SQL (an edit is visible); **flagged `custom`** in attrition and reports |

The other definition blocks:

| block | meaning |
|---|---|
| `index_event` | exactly one of `rule` (a `timesem.INDEX_RULES` name the grain lists), `phenotype_onset` (id@version of a phenotype of the spec's grain; the index is its onset) or `concept_time` (`table`, `column`, `pick: first \| last`) |
| `observation_window` | `[start_h, end_h)` hours relative to the index - the covariate window (default `[-24, 0)`); `data_availability` inherits it |
| `washout` | `none`; `no_prior_hadm` (no admission that started within `days` before the index - any earlier admission when `days` is absent - optionally only admissions carrying `codeset`); `no_prior_icu` (no ICU stay in the same window) |
| `follow_up` | `outcome` (a `timesem.CENSORING_RULES` name), `horizon_days` (censored outcomes only; defaults to the rule's), `competing_events` (`discharge_alive`, in-hospital outcomes only) |
| `era_filter` | the `anchor_year_group` labels kept (a subset of `timesem.ERAS`, sorted; empty = every era) |
| `degeneracy_probe` | the categorical columns `validate --tier` probes (§4); the tracer's four by default, an empty list disables it |
| `references` | optional `{codesets: [...], phenotypes: [...]}`; when given it must equal what the criteria, washout and index event name |

## 3. Versioning: `def_hash` and the immutable `(id, version)` pair

The registry resolves every reference to the referenced definition's `def_hash` (EP-40's
`meta.codesets` hash for a code set, EP-41's for a phenotype) and the spec's **`def_hash`**
is the sha256 of the canonical JSON of everything except the documentation fields
(`title`, `description`, `notes`, `what_it_does_not_claim`, a criterion's `description`)
with those hashes inlined - key order, whitespace and explicit defaults never move it;
the definition, or a referenced definition, does (D-25: specs are hashed like protocols).
Every spec directory carries `cohorts.lock.json` mapping each `id@version` to its hash
(`mwh cohort lock` records new pairs through `fsio.atomic_write_text`; the packaged lock
is committed). Loading a YAML whose hash differs from the recorded one raises
`CohortSpecFrozenError`, and every `mwh cohort` command refuses with exit 3
(`EXIT_REFUSED`) before anything runs; a frozen code set or phenotype refuses the specs
that read it the same way. The fix is never to edit the released version: copy the file
to the next version (the Cohort Builder's "save as new version", EP-62, writes
`<id>_<major>_<minor>_<patch>.yaml` through `cohort.registry.save_spec`), change it, lock
it, and let the protocols reference the new pair. Both versions then coexist in the
registry and in `meta.cohort_specs`.

`meta.cohort_specs` (`lake/meta/<tier>/cohort_specs.parquet`, written by the
`cohorts.specs` DAG step - `mwh build --tier <t> --tag cohorts` - and registered by the
EP-37 discovery walker) is the registry index: one row per registered `id@version` with
`def_hash`, `grain`, `index_event`, `n_inclusion` / `n_exclusion`, `custom`, `refs` (JSON
of `codeset:<id@version>` / `phenotype:<id@version>` -> hash), `outcome`, `locked`,
`path`. It is a `meta.*` registry read under `safe_query` (no count column needed, EP-33
B1c); EP-47's `marts.cohorts` - already in `safe.REGISTRY_TABLES` - records the builds
beside it, and no second exemption mechanism exists.

## 4. Validation and the level-degeneracy policy

`mwh cohort validate <ref>` checks what the schema cannot without data: a `codeset`
reference is a billed ICD set (the washout's a diagnosis set), a `phenotype_onset` names
a phenotype of the spec's grain, a `data_availability` table is a contract table with a
time column, a `concept` table outside the vendored inventory warns, a `custom_sql`
criterion is named as flagged. With `--tier <t>` two more checks run through
`safe_query` (audited, aggregate-only):

- **References on the tier** - every code set the spec names must be compiled on the
  tier (`meta.codesets`) and every phenotype built (`meta.phenotype_versions`) **with
  the hash the registry resolved**; a missing or stale one is a problem with its remedy
  (`mwh codeset compile --tier <t> <ref>` / `mwh phenotype compile <ref> --tier <t>`).
- **The degeneracy probe** - the EP-31 lesson written into the spec (EP-33 amendment):
  `tracer.fit` found that on every tier a handful of rare `first_careunit` /
  `admission_type` levels have no deaths, so a model that conditions on them has no
  finite maximum-likelihood coefficient for those levels. **The policy: such levels are
  excluded from the fit and named - never silently dropped, never fitted through.** The
  spec-level half lives here: for every column of `degeneracy_probe` the probe cross-tabs
  level x outcome over the spec's index population (the grain's index-event rule joined
  to the admission and the patient, under the era filter) and reports each released
  level as `zero-event`, `all-event` or `ok`; `safe_query` withholds every level whose
  counts fall in `1 .. k-1` (k = 11 on dev / full), so a small level is reported as
  *withheld*, not as a count. Degenerate levels are warnings (the cohort stays valid;
  the model must exclude and name them). The population is the index population
  *before* the criteria - EP-47 re-runs the probe over the compiled cohort - and a
  `phenotype_onset` / `concept_time` index has no population until compiled, so the probe
  is skipped with a warning. The model-side half (the exclusion inside the fit, the
  naming in `model.json` and the report) is the tracer's today and EP-79's for the GLM
  suite.

`mwh cohort schema` prints the JSON schema (`--out PATH` writes it) - pydantic's schema of
`CohortSpec` plus the exactly-one-kind-key rule on criteria and index events - which the
Cohort Builder form (EP-62) renders and against which `test_ep46` re-validates the seeds.

## 5. Schema reference

Generated from the JSON schema (`cohort.spec.json_schema()`): the top-level fields, then
every nested object.

<!-- schema:begin -->
**`CohortSpec`** — A mimicwarehouse cohort specification (EP-46): grain, index event, ordered inclusion / exclusion criteria, observation window, washout, follow-up, era filter and references by id@version. No absolute dates; windows are [start, end) hours relative to the index.

| field | type | required | meaning | default |
|---|---|---|---|---|
| `id` | string | yes | a slug; with version the reference id@version |  |
| `version` | string (`^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$`) | yes | semver |  |
| `title` | string | yes | documentation (not hashed) |  |
| `description` | string |  | documentation (not hashed) |  |
| `grain` | one of `subject`, `hadm`, `icustay`, `icu_day`, `hour_bin`, `person_time` | yes | the unit of analysis (timesem's registry, available grains only) |  |
| `index_event` | `IndexEvent` | yes |  |  |
| `inclusion` | list of `Criterion` |  | ordered; kept when true |  |
| `exclusion` | list of `Criterion` |  | ordered; removed when true |  |
| `observation_window` | `Window` |  | the covariate window relative to the index, default [-24, 0) h | `{"start_h": -24.0, "end_h": 0.0}` |
| `washout` | `Washout` |  | the washout rule | `{"rule": "none", "days": null, "codeset": null}` |
| `follow_up` | `FollowUp` | yes |  |  |
| `era_filter` | list of `2008 - 2010`, `2011 - 2013`, `2014 - 2016`, `2017 - 2019`, `2020 - 2022` |  | anchor_year_group labels kept (empty = every era) |  |
| `degeneracy_probe` | `DegeneracyProbe` |  | the level-degeneracy probe columns | `{"columns": ["gender", "admission_type", "first_careunit", "anchor_year_group"]}` |
| `references` | `References` (optional) |  | optional; must match |  |
| `notes` | string |  | documentation (not hashed) |  |
| `what_it_does_not_claim` | list of string |  | documentation |  |

**`AgeCriterion`** — Age at the index event in ``[min, max)`` years (``timesem.sql_age_at``, capped at 91: an ``age >= 89`` criterion is written ``min: 89`` and documented as capped).

| field | type | required | meaning | default |
|---|---|---|---|---|
| `min` | number (optional) |  | lower bound (inclusive), years |  |
| `max` | number (optional) |  | upper bound (exclusive), years |  |
| `at` | string |  | the age is taken at the index | `"index"` |

**`CodesetCriterion`** — Billed ICD codes of a dual EP-40 set (``icd_dx`` -> ``diagnoses_icd``, ``icd_px`` -> ``procedures_icd``): on the index admission (``same_admission``), on an admission that started within ``prior_days`` before the index (``prior_days``), or on any prior admission (``any_prior``); ``position: primary`` keeps ``seq_num = 1``.

| field | type | required | meaning | default |
|---|---|---|---|---|
| `ref` | string | yes | the code set as id@version |  |
| `position` | one of `any`, `primary` |  | diagnosis position | `"any"` |
| `lookback` | one of `same_admission`, `prior_days`, `any_prior` |  | which admissions are searched | `"same_admission"` |
| `prior_days` | integer (optional) |  | the lookback in days (lookback: prior_days only) |  |

**`ConceptCriterion`** — ``column op value`` on a cataloged relation keyed by one of the grain's keys (typically a materialised ``mimiciv_derived.<concept>``, EP-37/38; an admissions / stays attribute works the same way); ``window`` keeps only rows whose ``time_column`` lies in ``[start_h, end_h)`` hours from the index.

| field | type | required | meaning | default |
|---|---|---|---|---|
| `table` | string | yes | <schema>.<table> of the relation |  |
| `column` | string | yes | the tested column |  |
| `op` | one of `=`, `!=`, `<`, `<=`, `>`, `>=`, `in`, `not_in`, `is_null`, `is_not_null` | yes | the comparison |  |
| `value` | boolean \| integer \| number \| string \| list of boolean \| integer \| number \| string (optional) |  | a scalar, a list for in / not_in, absent for the null tests |  |
| `time_column` | string (optional) |  | the event-time column |  |
| `window` | `Window` (optional) |  | relative window on time_column |  |

**`ConceptTime`** — ``concept_time``: the index is the ``first`` / ``last`` value of ``column`` in ``table`` per grain unit (a relation keyed by the grain's keys).

| field | type | required | meaning | default |
|---|---|---|---|---|
| `table` | string | yes | <schema>.<table> |  |
| `column` | string | yes | the timestamp column |  |
| `pick` | one of `first`, `last` |  | which value | `"first"` |

**`Criterion`** — One inclusion / exclusion criterion: ``label`` + exactly one kind key (module docstring). ``description`` is documentation (not hashed).

| field | type | required | meaning | default |
|---|---|---|---|---|
| `label` | string | yes | a slug, unique in the spec: the attrition step name |  |
| `description` | string |  | documentation (not hashed) |  |
| `age` | `AgeCriterion` (optional) |  |  |  |
| `demographic` | `DemographicCriterion` (optional) |  |  |  |
| `codeset` | `CodesetCriterion` (optional) |  |  |  |
| `phenotype` | `PhenotypeCriterion` (optional) |  |  |  |
| `concept` | `ConceptCriterion` (optional) |  |  |  |
| `los` | `LosCriterion` (optional) |  |  |  |
| `data_availability` | `DataAvailabilityCriterion` (optional) |  |  |  |
| `prior_admissions` | `PriorAdmissionsCriterion` (optional) |  |  |  |
| `custom_sql` | `CustomSqlCriterion` (optional) |  |  |  |

**`CustomSqlCriterion`** — A hand-written SELECT yielding the grain's key column(s) of the units the criterion keeps (the compiler wraps it as a CTE and semi-joins, EP-47); ``hash`` must equal the sha256 of the whitespace-normalised SQL so an edit is visible. Flagged ``custom`` in attrition and reports.

| field | type | required | meaning | default |
|---|---|---|---|---|
| `sql` | string | yes | a SELECT yielding the grain keys |  |
| `hash` | string | yes | sha256 of the whitespace-normalised sql |  |

**`DataAvailabilityCriterion`** — At least ``min_rows`` rows of ``table`` for the unit (of the given ``itemids`` when the table has an ``itemid`` column) inside ``window`` — the spec's observation window when absent.

| field | type | required | meaning | default |
|---|---|---|---|---|
| `table` | string | yes | <schema>.<table> of a contract table with a time column |  |
| `itemids` | list of integer |  | restrict to these itemids |  |
| `min_rows` | integer |  | minimum row count (inclusive) | `1` |
| `window` | `Window` (optional) |  | relative window; default: the observation window |  |

**`DegeneracyProbe`** — The categorical columns ``mwh cohort validate --tier`` probes for zero-event / all-event levels against the follow-up outcome (EP-31 policy); an empty list disables the probe.

| field | type | required | meaning | default |
|---|---|---|---|---|
| `columns` | list of `gender`, `admission_type`, `admission_location`, `discharge_location`, `insurance`, `first_careunit`, `language`, `marital_status`, `anchor_year_group` |  | categorical columns to probe | `["gender", "admission_type", "first_careunit", "anchor_year_group"]` |

**`DemographicCriterion`** — Set membership on admission / patient / first-ICU-unit fields (public vocabulary values, equality only; several fields = AND). ``discharge_location`` is known only at discharge — a post-index attribute a retrospective definition may still exclude on.

| field | type | required | meaning | default |
|---|---|---|---|---|
| `gender` | list of string |  | patients.gender values |  |
| `admission_type` | list of string |  | admissions.admission_type |  |
| `admission_location` | list of string |  | admissions.admission_location |  |
| `discharge_location` | list of string |  | admissions.discharge_location (post-index) |  |
| `insurance` | list of string |  | admissions.insurance |  |
| `first_careunit` | list of string |  | icustays.first_careunit of the index / first ICU stay |  |
| `language` | list of string |  | admissions.language |  |
| `marital_status` | list of string |  | admissions.marital_status |  |

**`FollowUp`** — The outcome (a ``timesem.CENSORING_RULES`` name), its horizon in days after the index (an in-hospital outcome has none; a censored outcome defaults to the rule's and is censored at ``min(index + horizon, last discharge + 365 d)`` — the ``dod`` visibility rule) and the competing events (``discharge_alive`` for in-hospital outcomes, optional).

| field | type | required | meaning | default |
|---|---|---|---|---|
| `outcome` | one of `in_hospital_mortality`, `mortality_30d`, `mortality_90d`, `mortality_1y` | yes | a timesem censoring-rule name |  |
| `horizon_days` | integer (optional) |  | days after the index (censored outcomes only) |  |
| `competing_events` | list of string |  | competing events |  |

**`IndexEvent`** — Exactly one of: a named ``rule`` of the grain's registry entry (``timesem.INDEX_RULES``), the onset of a ``phenotype_onset`` (``id@version``, a phenotype of the spec's grain) or a ``concept_time``.

| field | type | required | meaning | default |
|---|---|---|---|---|
| `rule` | string (optional); one of `first_icu_stay`, `first_hadm`, `each_hadm`, `each_icustay`, `first_icu_stay_of_first_hadm` |  | an index-event rule of timesem's registry |  |
| `phenotype_onset` | string (optional) |  | a phenotype as id@version |  |
| `concept_time` | `ConceptTime` (optional) |  |  |  |

**`LosCriterion`** — Length of stay in hours, ``[min_hours, max_hours)``: ``icu`` = the index ICU stay (``hours_since(intime, outtime)``), ``hosp`` = the index admission (``hours_since(admittime, dischtime)``).

| field | type | required | meaning | default |
|---|---|---|---|---|
| `min_hours` | number (optional) |  | lower bound (inclusive) |  |
| `max_hours` | number (optional) |  | upper bound (exclusive) |  |
| `of` | one of `icu`, `hosp` |  | which stay | `"icu"` |

**`PhenotypeCriterion`** — An EP-41 phenotype: its onset lies ``before`` the index, the phenotype flags the index unit (``at``: the unit itself, or the subject's onset at or before the index for a subject-grain phenotype), or the onset lies ``within_hours`` of the index (``\|hours since index\| <= hours``, the one closed interval).

| field | type | required | meaning | default |
|---|---|---|---|---|
| `ref` | string | yes | the phenotype as id@version |  |
| `when` | one of `before`, `at`, `within_hours` |  | how the phenotype relates to the index | `"at"` |
| `hours` | number (optional) |  | the half-width in hours (when: within_hours only) |  |

**`PriorAdmissionsCriterion`** — The number of hospital admissions that started before the index (within ``lookback_days`` when given) lies in the inclusive range ``[min, max]``.

| field | type | required | meaning | default |
|---|---|---|---|---|
| `min` | integer (optional) |  | minimum count (inclusive) |  |
| `max` | integer (optional) |  | maximum count (inclusive) |  |
| `lookback_days` | integer (optional) |  | lookback in days |  |

**`References`** — The declared references (optional in YAML; when given they must equal the ones the criteria, washout and index event name).

| field | type | required | meaning | default |
|---|---|---|---|---|
| `codesets` | list of string |  | code sets as id@version |  |
| `phenotypes` | list of string |  | phenotypes as id@version |  |

**`Washout`** — ``none``, ``no_prior_hadm`` (no hospital admission that started within ``days`` before the index — any prior admission when ``days`` is absent — optionally only admissions carrying ``codeset``) or ``no_prior_icu`` (no ICU stay in the same window).

| field | type | required | meaning | default |
|---|---|---|---|---|
| `rule` | one of `none`, `no_prior_hadm`, `no_prior_icu` |  | the washout rule | `"none"` |
| `days` | integer (optional) |  | the lookback in days (absent = any) |  |
| `codeset` | string (optional) |  | only prior admissions carrying this set (no_prior_hadm) |  |

**`Window`** — A half-open window ``[start_h, end_h)`` in hours relative to the index event (negative = before it; ``timesem.sql_hours_since`` in the compiled SQL).

| field | type | required | meaning | default |
|---|---|---|---|---|
| `start_h` | number | yes | window start in hours relative to the index (inclusive) |  |
| `end_h` | number | yes | window end in hours relative to the index (exclusive) |  |
<!-- schema:end -->

## 6. Seed specs

The packaged specs (rendered from `specs/*.yaml`; definition text and hashes only):

<!-- cards:begin -->
### `first_icu_adults@1.0.0` — First ICU stay of adult patients (the tracer bullet's cohort)

- **grain** `icustay` · **index** `first_icu_stay` · **def_hash** `2724fd711767` · **locked** yes · `specs/first_icu_adults.yaml`
- **observation window** `[-24, 0)` h · **washout** none · **follow-up** in_hospital_mortality, competing discharge_alive · **era filter** none
- **references** (none)
- **degeneracy probe** `gender`, `admission_type`, `first_careunit`, `anchor_year_group`

| step | polarity | kind | definition |
|---|---|---|---|
| `adult` | inclusion | age | age(18 <= age at index) |
| `short_icu_stay` | exclusion | los | los(icu hours < 4) |

One row per patient: the first ICU stay of the subject (ordered intime, stay_id), adults only (age at the index >= 18; ages >= 89 are shipped as 91 and the bound is documented as capped), ICU stays shorter than 4 hours excluded; followed for in-hospital mortality with discharge alive as the competing event. The covariate window is the 24 hours before the ICU admission. The definition mirrors the EP-31 tracer bullet so the compiled attrition (EP-47) can be compared count for count.

What it does not claim:

- No causal claim - the criteria are admission characteristics, not interventions.
- Ages >= 89 are indistinguishable (all appear as 91); `adult` keeps them.
- The era filter is empty - anchor_year_group is a covariate axis, never a calendar.
- The 4-hour ICU exclusion is a data-quality floor, not a clinical definition of an ICU stay.

### `hf_admissions@1.0.0` — Heart-failure admissions of adults (first in 365 days)

- **grain** `hadm` · **index** `each_hadm` · **def_hash** `3b5f5efc1c6e` · **locked** yes · `specs/hf_admissions.yaml`
- **observation window** `[-24, 0)` h · **washout** no_prior_hadm within 365 d carrying heart_failure@1.0.0 · **follow-up** mortality_30d within 30 d · **era filter** none
- **references** `codeset:heart_failure@1.0.0` (`f993dad36b9c`)
- **degeneracy probe** `gender`, `admission_type`, `first_careunit`, `anchor_year_group`

| step | polarity | kind | definition |
|---|---|---|---|
| `hf_coded` | inclusion | codeset | codeset(heart_failure@1.0.0, position any, same_admission) |
| `adult` | inclusion | age | age(18 <= age at index) |
| `hospice_discharge` | exclusion | demographic | demographic(discharge_location in ['HOSPICE']) |

One row per hospital admission that carries a heart-failure diagnosis code (heart_failure@1.0.0, the Quan 2005 Charlson definition, any position, same admission) for a patient aged 18 or more at admission, excluding admissions discharged to hospice, with a 365-day washout: no earlier admission carrying the same code set within 365 days of the index. Followed for 30-day mortality from the admission time under the dod visibility rule (min(index + 30 d, last discharge + 365 d)).

What it does not claim:

- A billed code is not an adjudicated diagnosis; coding practice varies by era.
- 30-day mortality counts deaths dod records - deaths after the dod visibility horizon (about one year after the last discharge) are unobservable.
- The hospice exclusion removes admissions by their outcome-adjacent disposition; a survival analysis should treat it as a design choice, not a covariate.
- No calendar-time claim; anchor_year_group is the only era axis.
<!-- cards:end -->

## 7. What this does not claim

A cohort spec is a **computable definition** of a study population, not a validated
clinical cohort: it selects on billed codes, orders, charted values and mimic-code
concepts as recorded, the index event is a recorded time, and the follow-up is bounded by
what `dod` can show. It carries no exposure, outcome model, analysis plan or holdout -
those are the protocol's (EP-51), which references a spec by `id@version`. Every seed
states its own limits in `what_it_does_not_claim` (§6); the compiled attrition (EP-47),
the diagram (EP-48) and the Table 1 over a cohort (EP-71) inherit them.
