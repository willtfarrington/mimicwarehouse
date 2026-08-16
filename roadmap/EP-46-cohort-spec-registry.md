# EP-46 — Cohort spec + registry

**Size:** M · **Tier:** fixture+dev · **Core/Stretch:** core · **Depends on:** EP-34 (Time semantics + unit-of-analysis registry), EP-40 (Code-set registry + ICD-9→10 GEM utility) · **Blocks:** EP-47 (Cohort compiler, materialization, attrition, snapshot), EP-51 (Protocol schema + freeze registry + `mwh protocol`), EP-54 (Re-plan P3)

## Context

Capability 2 (reproducible cohort construction) needs a declarative, versioned cohort
specification that the compiler (EP-47), the Cohort Builder page (EP-62, via a JSON-schema form)
and the protocol schema (EP-51, by reference) all share. DESIGN §9 fixes its shape: `grain`,
`inclusion`, `exclusion`, `index_event`, `observation_window`, `washout`, `follow_up`,
`era_filter`, references to code sets and phenotypes by version. This brief builds
`src/mimicwarehouse/cohort/spec.py` + `registry.py` (DESIGN §15) using EP-34's grain registry,
relative-time and censoring rules and EP-40's code-set registry (phenotype references resolve
through EP-41's registry, which exists by execution order). Caveats baked into the schema: no
calendar dates anywhere (per-patient date shift) — only relative windows and `anchor_year_group`
era filters; ages ≥ 89 = 91 (an `age ≥ 89` criterion is expressed as `≥ 89` and documented as
capped); `dod` horizon rule for follow-up; discharge-alive competing event optional. The tracer
cohort (EP-31, first-ICU-stay adults) becomes the first registered spec so EP-47 can reproduce
its attrition. D-5, D-18, D-25 (specs are hashed like protocols) apply.

## In scope

1. **`CohortSpec` schema** (`src/mimicwarehouse/cohort/spec.py`) — pydantic model with:
   `id`, `version` (semver), `title`, `description`, `grain` (must be `available` in
   `timesem.GRAINS`), `index_event` {rule from the grain registry (`first_icu_stay`,
   `first_hadm`, `each_hadm`, `each_icustay`, `first_icu_stay_of_first_hadm`) or
   `phenotype_onset(id@version)` or `concept_time(table, column, first|last)`}, `inclusion` and
   `exclusion` lists of `Criterion` (ordered; each with a `label`), `observation_window` {start_h,
   end_h relative to index; default [−24, 0)}, `washout` {no_prior_hadm_days | no_prior_icu_days |
   none}, `follow_up` {horizon_days, censoring rule name from `timesem`, competing_events list},
   `era_filter` (subset of `timesem.ERAS`), `references` (code sets / phenotypes `id@version` →
   resolved `def_hash` at registration), `notes`, `what_it_does_not_claim`. Criterion types:
   `age` {min, max, at: index}, `demographic` {gender, admission_type, admission_location,
   insurance, first_careunit, language, marital_status — equality/set membership only},
   `codeset` {id@version, position any|primary, lookback: same_admission|prior_days N|any_prior},
   `phenotype` {id@version, when: before|at|within_hours N}, `concept` {table, column, op, value,
   window_h}, `los` {min_hours, max_hours, of: icu|hosp}, `data_availability` {table, itemids?,
   min_rows, window_h}, `prior_admissions` {min, max, lookback_days}, `custom_sql` {sql, hash —
   allowed, flagged `custom=True` in attrition and reports}. Field-level validators forbid absolute
   dates and unavailable grains.
2. **Hash + registry** (`registry.py`) — `def_hash` = sha256 of canonical JSON of everything
   except `title/description/notes/what_it_does_not_claim`; `(id, version)` immutable (same
   frozen-version error as EP-40/41); registry index `meta.cohort_specs` (id, version, def_hash,
   grain, refs, path); built-in specs under `src/mimicwarehouse/cohort/specs/`, study specs under
   `%MWH_DATA_ROOT%\studies\<study_id>\cohorts\`; `mwh cohort list|show|validate|schema` (`schema`
   dumps the JSON schema used by EP-62's form). Dated DESIGN §15 note for the `mwh cohort` group.
3. **Seed specs** — `first_icu_adults@1.0.0` (grain `icustay`; index `first_icu_stay`;
   inclusion: age ≥ 18 at index; exclusion: ICU LOS < 4 h; follow-up: in-hospital mortality,
   discharge-alive competing; observation window [−24, 0) h → the tracer bullet's cohort, wording
   aligned with EP-31 so attrition matches) and `hf_admissions@1.0.0` (grain `hadm`; index
   `each_hadm`; inclusion: `codeset heart_failure@1.0.0` any position same admission, age ≥ 18;
   exclusion: hospice discharge; washout: no prior hadm with the same code set within 365 days;
   era filter none).
4. **YAML I/O + docs** — `CohortSpec.from_yaml/to_yaml` (round-trip stable, key order fixed),
   `docs/methods/cohorts.md` (new): schema reference (generated from the JSON schema), criterion
   semantics table (windows are `[start, end)` in hours relative to index; lookbacks are
   within-patient relative time), versioning rule, worked example (the tracer spec).
5. **Tests** (`tests/ep/test_ep46.py`, `@pytest.mark.ep_46`; fixture, `dev`) — every seed spec
   validates; hash invariance and frozen-version refusal; a spec with an absolute date, an
   unavailable grain (`note`) or an unknown code-set version fails validation with a clear message;
   JSON schema exports and re-validates the seed specs; `references` resolve to EP-40 hashes on
   fixture; on dev, `mwh cohort validate first_icu_adults@1.0.0 --tier dev` resolves all
   references against `meta.codesets`/`meta.phenotype_versions`.

## Out of scope

- Compiling to SQL, materialization, attrition counts, run records → EP-47.
- Attrition diagram → EP-48; Cohort Builder page → EP-62.
- Protocol-level fields (exposure, outcome, analysis plan, holdout) → EP-51 (references a spec).
- ACES/MEDS cohort DSL as a validation lane → parked (`final-roadmap.md` § 2).

## Verification / acceptance

- `uv run poe test -m ep_46` green on fixture and dev; `uv run --group dev mwh verify EP-46` green.
- `uv run --group dev mwh cohort list` shows both seed specs with versions and hashes; `mwh cohort
  schema` prints a JSON schema; `mwh cohort validate hf_admissions@1.0.0 --tier dev` exits 0.
- A test demonstrates refusal of an absolute-date criterion and of an edited-without-bump spec.
- `docs/methods/cohorts.md` exists with the criterion semantics table.
