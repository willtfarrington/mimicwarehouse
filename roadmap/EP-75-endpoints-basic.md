# EP-75 — Endpoints A: binary/continuous/count/ordinal

**Size:** M · **Tier:** fixture+dev+full · **Core/Stretch:** core · **Depends on:** EP-47 (Cohort compiler, materialization, attrition, snapshot), EP-34 (Time semantics + unit-of-analysis registry) · **Blocks:** EP-76 (Endpoints B: time-to-event + recurrent), EP-79 (GLM suite A: families + tidy()), EP-84 (Repeated encounters / utilization), EP-89 (Capstone #3)

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-74 (Re-plan P4) before execution.

## Context

Capability category 13 (*Outcome and endpoint construction*). Every P5–P7 analysis consumes an
endpoint, so a versioned endpoint library is the first P5 deliverable. Builds on the cohort tables
of EP-47 (`marts/cohorts/<cohort_id>@<version>/`), the grain registry and `dod` rule of EP-34
(`timesem.py`), the run ledger (EP-35) and `disclose` (EP-43); Polars primary (D-17). Theme per D-5:
the tracer-bullet cohort (first-ICU-stay adults, EP-31). Caveats: within-patient intervals are valid,
cross-patient calendar time is not (date shift; `anchor_year_group` is the only temporal axis); a
subject's last observed admission is administratively censored at an unknown date;
`discharge_location` has nulls and free-form levels that need a declared ordinal order.

## Scope sketch (refine at re-plan)

1. **`src/mimicwarehouse/stats/endpoints.py`** — pydantic `Endpoint` spec (`name`, `version`,
   `grain` from the EP-34 registry, `type ∈ {binary, continuous, count, ordinal}`, SQL/expression
   definition over catalog tables, `window` relative to the grain's index event, `missing_policy`,
   ordered `levels` for ordinal); YAML registry `src/mimicwarehouse/endpoints/*.yaml` versioned
   like code sets (semver + definition hash, EP-40 convention); `compile_endpoint(cohort_ref,
   endpoint) → pl.DataFrame` (keys + value); `summarize_endpoint()` → aggregate summary via
   `disclose.suppress` (n, event %, quantiles, level counts).
2. **Four seed endpoints**: `in_hospital_death` (binary; `admissions.hospital_expire_flag`
   cross-checked against `deathtime`; hadm and icustay grains), `hospital_los_days` /
   `icu_los_days` (continuous; `dischtime − admittime`, `icustays.los`; non-positive guard),
   `readmission_count_30d` / `_365d` (count; later admissions of the same subject within the
   window after discharge; death in window flagged via `dod`; last-admission censoring documented),
   `discharge_disposition_ordinal` (from `discharge_location`; level order declared in YAML;
   unmapped → missing; in-hospital death handled per YAML flag).
3. **Materialisation + provenance**: register an `analysis` step kind in the EP-19 `STEP_HANDLERS`
   registry and the step-id convention `analysis.<workflow>` every P5–P7 workflow follows (here
   `analysis.endpoints_basic`), writing `marts/endpoints/<cohort_id>@<v>/<endpoint>@<v>.parquet`;
   run record (EP-35) cites cohort snapshot + endpoint version/hash; per-unit tables stay in the
   data root (row-level).
4. **Representative workflow**: tracer cohort → the four endpoints → summary table + distribution
   figures (`viz/` Altair specs; bins n ≥ 11) → Markdown report under `runs/<run_id>/report/` via
   the EP-59 export primitives (claim type *exploratory*; retrospective statement).
5. **Tests** `tests/ep/test_ep75.py` (`@pytest.mark.ep_75`, tier markers): fixture invariants
   (LOS ≥ 0; death ⇒ disposition rule; readmission counts monotone in window; ordinal levels
   complete), hypothesis over window arithmetic, dev counts pinned to `safe_query` aggregates.

## Out of scope

- Time-to-event / recurrent endpoints → EP-76; regression → EP-79; utilization windows and rates
  → EP-84; KM / Cox → EP-91; endpoint UI (none mandated; endpoints appear as pickers in EP-88).

## Verification / acceptance (sketch)

- `uv run poe test -m ep_75` green on fixture + dev; `uv run --group dev mwh verify EP-75` green.
- Full-tier compile as a logged background job: `uv run --group dev mwh build --tier full --select
  analysis.endpoints_basic --background --job ep75-endpoints` (log `%MWH_DATA_ROOT%\runs\jobs\`;
  `mwh jobs --job ep75-endpoints` shows `done`); run id + wall time in the completion note.
- Report passes `mwh disclose check`; endpoint YAMLs carry version + hash, cited in the run manifest.

## Parked → final-roadmap.md

- Days-alive-and-out-of-hospital (DAOH) endpoint; composite / win-ratio endpoints (already END-1).
