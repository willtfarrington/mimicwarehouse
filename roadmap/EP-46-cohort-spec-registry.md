# EP-46 — Cohort spec + registry

**Size:** M · **Tier:** fixture+dev · **Core/Stretch:** core · **Depends on:** EP-34 (Time semantics + unit-of-analysis registry), EP-40 (Code-set registry + ICD-9→10 GEM utility), EP-172 (GEM review adjudication: t2dm + sepsis_explicit (owner-supervised)) · **Blocks:** EP-47 (Cohort compiler, materialization, attrition, snapshot), EP-51 (Protocol schema + freeze registry + `mwh protocol`), EP-54 (Re-plan P3)

> **Owner gate at pickup (owner directive 2026-09-06, at EP-41; re-pointed 2026-09-16 at
> EP-45 completion).** Do not start this brief until
> [EP-172](EP-172-gem-review-adjudication.md) is ☑ in the roadmap table: the owner-supervised
> adjudication of the EP-40 GEM reviews, which either records "all rejected" for a set or locks
> the accepted codes as `t2dm@1.1.0` / `sepsis_explicit@1.1.0` and bumps the phenotypes that
> reference them. Cohort specs reference code sets by `id@version` from here on, so the
> versions this brief seeds must be the reviewed ones (the latest locked pair of each set;
> the 1.0.0 pairs stay valid). If EP-172's ☑ is missing, stop and ask (`AskUserQuestion`)
> rather than seeding specs over unreviewed versions.

> **EP-33 amendment (2026-09-01).** Header facts unchanged. (1) **Level-degeneracy policy**
> (EP-31 lesson; EP-31's amendment names EP-46 and EP-79 as the two homes): EP-31's
> `tracer.fit` probes every categorical covariate level for zero-event / all-event cells,
> excludes those rows and **names** the excluded levels in `model.json` and the report — never
> silently drops them, never fits through them. This brief owns the spec-level half: the
> `CohortSpec` gains an optional probe block (e.g. `degeneracy_probe`: categorical columns to
> probe against the follow-up outcome, defaulting to the tracer's four — `gender`,
> `admission_type`, `first_careunit`, `anchor_year_group`; exact field name at the
> implementer's discretion), `mwh cohort validate --tier <t>` reports as suppressed aggregates
> which levels would be zero-/all-event for the spec's outcome, and `docs/methods/cohorts.md`
> states the policy; EP-79 inherits the model-side half. (2) **`marts.cohorts` is
> pre-registered** in `safe.REGISTRY_TABLES` (EP-33 B1c: `REGISTRY_SCHEMAS = {meta,
> information_schema}`, `REGISTRY_TABLES = {marts.cohorts}`, plus contract dims via
> `is_registry_ref`), so the registry index this brief defines (`meta.cohort_specs`, a `meta.*`
> read) and EP-47's `marts.cohorts` are readable by sessions without a count-family column —
> EP-46/47 add **no second exemption mechanism**; every other `marts.*`/`mimiciv_derived.*`
> read stays subject-keyed (ledger P3C-5). (3) `meta.cohort_specs` lands as
> `lake/meta/<tier>/cohort_specs.parquet` (EP-29 convention); frozen-version and validation
> refusals exit via `console.fail(..., code=console.EXIT_REFUSED)` on stderr; the YAML writer
> uses `fsio.atomic_write_text`.

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

> **Completion note (2026-09-16).** Executed as briefed on fixture + dev (the gate held:
> EP-172 ☑ `e00aad7`, so the seeds reference the reviewed pairs — `hf_admissions@1.0.0`
> reads `heart_failure@1.0.0`, unchanged since EP-40; neither seed needs `t2dm` or
> `sepsis_explicit`). Shipped: `src/mimicwarehouse/cohort/` — `spec.py` (the pydantic
> `CohortSpec` with the nine criterion kinds, the three index-event forms, windows /
> washout / follow-up over `timesem`, the era filter, the `degeneracy_probe` block —
> field name as chosen here — the date / grain / cap-band refusals, `to_yaml` /
> `from_yaml` / `save_yaml`, `json_schema()`), `registry.py` (`specs/` + `cohorts.lock.json`,
> `CohortSpecFrozenError`, reference resolution against the EP-40 / EP-41 registries,
> static `validate`, `save_spec`, the `cohorts.specs` DAG step → `meta.cohort_specs`,
> `register_cohorts`, the docs renderer), `probe.py` (references on the tier + the
> degeneracy probe through `safe_query`), `cli.py` (`mwh cohort list | show [--yaml] |
> validate [--tier] [--k] | schema [--out] | lock [--check]`), `__main__.py`;
> `dag/specs/cohorts.yaml`; the seeds `first_icu_adults@1.0.0` (def_hash `2724fd711767`) and
> `hf_admissions@1.0.0` (`3b5f5efc1c6e`); `docs/methods/cohorts.md` (three generated blocks:
> the schema reference from the JSON schema, the seed cards, the tracer spec as the worked
> example; the criterion semantics table and the level-degeneracy policy are prose);
> `tests/ep/test_ep46.py` (11 fixture + 1 dev tests); the DESIGN §9 / §15 notes, the D-25
> addendum, the README state row + quick-start lines.
>
> **Interpretation choices (owner review at the end of the session).** (1) `mwh cohort
> lock [--check]` was added beside the four commands the brief lists — the `(id, version)`
> immutability rule is a lock file exactly as EP-40 / EP-41 built it, and the frozen-version
> test needs the command. (2) The "hospice discharge" exclusion is a `demographic`
> criterion on `discharge_location` — the field joins the brief's seven because no listed
> kind could express it; the docs state that it conditions on a post-index disposition.
> (3) `hf_admissions` follows up `mortality_30d` (the brief names no outcome; the seed
> exercises the `dod` horizon rule) with an empty competing-event list. (4) The tracer
> seed omits EP-31's `complete` step (non-null `dischtime` / `hospital_expire_flag`): it is
> implied by the in-hospital outcome and drops nothing on MIMIC-IV 3.1 — EP-47 compares
> the compiled counts with the tracer report and records any difference, as its brief
> says; the 4-hour ICU exclusion is the brief's addition and is expected to move the final
> count. (5) The degeneracy probe runs over the **index population** (the grain's index
> rule joined to admissions / patients under the era filter) — there is no compiled cohort
> before EP-47, which re-runs the probe over the materialised cohort; a `phenotype_onset`
> / `concept_time` index skips it with a warning. (6) Criterion labels enter the hash and
> defaults are explicit in the canonical form (D-25 addendum). (7) The `custom_sql`
> contract is "a SELECT yielding the grain keys, semi-joined by EP-47" — the least
> compiler-coupled shape.
>
> **Dev tier (2026-09-16).** `mwh build --tier dev --tag cohorts` (build
> `20260916T212735-dev-e4dd6fb`, run `20260916T212735Z-c0072b`): `cohorts.specs` 2 rows in
> 0.4 s, catalog 3.0 s, 7.1 s end to end; `meta.cohort_specs` carries both seeds with the
> registry hashes. `mwh cohort validate first_icu_adults@1.0.0 --tier dev` (k = 11): index
> population 3,208 first ICU stays; gender 2 levels released, admission_type 4 released /
> 4 withheld, first_careunit 7 / 7, anchor_year_group 5 / 0; **0 degenerate levels**
> (every released level has ≥ 14 in-hospital deaths), exit 0. `mwh cohort validate
> hf_admissions@1.0.0 --tier dev`: `heart_failure@1.0.0` compiled on dev with the registry
> hash (`f993dad36b9c`); index population 27,263 admissions; admission_type 5 released / 4
> withheld, first_careunit 9 / 6 (the `(null)` level = admissions without an ICU stay,
> 23,056), anchor_year_group 5 / 0; 0 degenerate, exit 0. On the **fixture** the probe
> already earns its keep: `hf_admissions`' `admission_type = DIRECT OBSERVATION` is
> zero-event for `mortality_30d` (6 admissions, 0 deaths at k = 1) and is named as a
> warning, the EP-31 policy in miniature. No full-tier run (the brief's tier is
> fixture+dev; `meta.cohort_specs` lands on full with EP-47's first full build).
>
> **Gates.** `uv run poe test -m ep_46`: 11 passed (fixture, ≈ 60 s); `poe test-dev -m
> ep_46`: 12 passed; `mwh verify EP-46`: 11 passed; `poe check` green (ruff, `ruff format
> --check`, pyright 0 errors, the full fixture suite); `mwh guard` clean over the changed
> files; `poe roadmap-check --strict` 0 errors, 0 warnings. Earlier tests untouched: the
> catalog extension slots between the measurement and phenotype extensions so `test_ep41`'s
> order pins (phenotypes second to last, units last) hold, and `test_ep20`'s catalog
> dependency check is a superset check. `jsonschema` (already in `uv.lock` as altair's
> transitive dependency, pure Python) re-validates the seeds in `test_ep46`; no new
> dependency was added.
>
> **Owner decisions at the interactive review (2026-09-16, every recommended option
> taken).** (1) Commit in the standard two steps, no push (the owner pushes). (2) Keep
> `discharge_location` as a `demographic` field and the hospice exclusion as
> `demographic: {discharge_location: [HOSPICE]}` (rejected: a `concept` criterion over
> `admissions.discharge_location`; dropping the exclusion). (3) Keep `mortality_30d` as
> `hf_admissions@1.0.0`'s follow-up (rejected: in-hospital mortality with `discharge_alive`;
> `mortality_1y`). (4) Keep EP-31's `complete` step omitted from `first_icu_adults@1.0.0`;
> EP-47 reconciles the counts (rejected: an explicit `outcome_recorded` concept criterion).
> The remaining choices (the `lock` command, the index-population probe, hashed labels /
> explicit defaults, the `custom_sql` contract) are routine and logged above.
>
> **Handed on.** EP-47: re-run the degeneracy probe over the compiled cohort (the
> `probe.probe_sql` shape over the materialised table), reconcile `first_icu_adults@1.0.0`
> with the EP-31 counts (the 4-hour exclusion), compile `custom_sql` as a semi-join on the
> grain keys, and register `marts.cohorts` beside `meta.cohort_specs`. EP-62: `save_spec`
> and `json_schema()` are the two seams the form uses. EP-79: the model-side half of the
> degeneracy policy.
