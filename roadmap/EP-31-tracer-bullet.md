# EP-31 — Tracer bullet: first-ICU-stay adults → in-hospital mortality

**Size:** M · **Tier:** fixture+dev+full · **Core/Stretch:** core · **Depends on:** EP-21 (Catalog builder (per-tier .duckdb)), EP-30 (Safe-query wrapper + audit log) · **Blocks:** EP-32 (Capstone #0: staging benchmark note + docs/analyses convention), EP-33 (Re-plan P2)

## Context

The first end-to-end proof (**D-8**: foundation → early tracer bullet → breadth) and the
project's canonical tracer theme (**D-5**): first ICU stay of adult patients → in-hospital
mortality. It runs hand-written cohort SQL against the tier catalogs (EP-21), pulls every
number a human or a session sees through `safe_query` (EP-30, **D-31**), fits a plain
logistic regression in-process, and writes a Markdown report with attrition counts under
`runs/`. It deliberately does **not** build the cohort engine (EP-46/47), the run ledger
(EP-35) or the report engine (EP-130) — it shows what they must make routine, and its
lessons go into the P2 re-plan. MIMIC caveats that shape it: age at admission derived from
`anchor_age`/`anchor_year` (ages ≥ 89 appear as 91), `anchor_year_group` as the only
temporal axis (used as an era covariate, never a calendar), `hospital_expire_flag` as the
outcome (in-hospital death; `dod` is not needed), discharge alive is the competing state.
Small tables only (`patients`, `admissions`, `icustays` from EP-20), so the full-tier run
takes seconds — it still runs as a logged background job. Claim type: **associational
(exploratory)**; retrospective; no prediction claim (that is P7, EP-110).

## In scope

1. **Cohort SQL** (`src/mimicwarehouse/sql/tracer_first_icu_mortality.sql`, loaded by
   `mimicwarehouse.tracer`) — a CTE chain, one step per criterion, each step selectable
   for counting: `base` (icustays ⋈ admissions ⋈ patients on `hadm_id`/`subject_id`) →
   `first_stay` (`row_number() OVER (PARTITION BY subject_id ORDER BY intime, stay_id) = 1`)
   → `adult` (`anchor_age + (year(admittime) - anchor_year) >= 18`) → `complete`
   (`dischtime IS NOT NULL AND hospital_expire_flag IS NOT NULL`) → `cohort` with columns
   `age_at_admit` (capped at 91), `gender`, `admission_type`, `first_careunit`,
   `anchor_year_group`, `hospital_expire_flag`. No ICU length of stay as a covariate (it is
   post-index) — say so in the report.
2. **Attrition + descriptives via `safe_query`** (`src/mimicwarehouse/tracer.py`) —
   `attrition(tier)`: one `safe_query("SELECT count(*) AS n FROM (<cte up to step>)", tier=…, actor="tracer")`
   per step → `[{step, n}]`; `descriptives(tier)`: mortality by age band
   (`18–39, 40–64, 65–79, 80+`) × gender and by `first_careunit`, each as
   `count(*) AS n, sum(hospital_expire_flag) AS n_deaths` — the `n_*` alias makes deaths a
   count column so k = 11 row-wise suppression applies (record `rows_suppressed`).
3. **Model** — `fit(tier)`: read the `cohort` CTE through `open_catalog(tier)` (READ_ONLY,
   in-process only; never printed) into polars → pandas → statsmodels `Logit`
   (`hospital_expire_flag ~ age_at_admit + gender + admission_type + first_careunit +
   anchor_year_group`, treatment coding, `cov_type = "HC1"`), report odds ratios with 95 %
   CIs, n, events, in-sample AUC (`sklearn.metrics.roc_auc_score`) labelled *in-sample,
   optimistic*. If the outcome is constant (possible on the fixture) record
   `model: not_fit (constant outcome)` and continue.
4. **Run folder + report** — `run_tracer(tier, *, out=None) -> TracerResult` writes
   `runs/tracer/<yyyymmddThhmmss>-<tier>/`: `manifest.json` (git sha, package + DuckDB
   versions, tier, `core_snapshot_id` from `meta.catalog_info`, params, cohort n, wall_s,
   audit ids of every `safe_query` call), `attrition.json`, `descriptives.json`,
   `model.json`, `report.md`. Report sections: Question · Data (tier, snapshot id, "MIMIC-IV
   analyses are retrospective") · Cohort (attrition table) · Descriptives (suppressed
   tables, suppression note) · Model (OR table; **Claim type: associational (exploratory)**)
   · What it deliberately does not claim (no causal effect, no prediction performance, no
   calendar-time trends, age cap) · Reproduction (`mwh tracer --tier <t>`, run id) ·
   Provenance. The report stays under `runs/` until `mwh disclose check` exists (EP-43);
   nothing from it is copied into `docs/` here.
5. **CLI** — `mwh tracer --tier {fixture,demo,dev,full} [--background --job NAME]` (reuses
   `dag.jobs.launch`); dated `DESIGN.md` §15 note for `tracer.py` and the command. Runs:
   `uv run --group dev mwh tracer --tier dev` (foreground, seconds) then
   `uv run --group dev mwh tracer --tier full --background --job tracer-full`; wait via
   `mwh jobs --job tracer-full`; record both run ids and wall times in the completion note.
6. **Tests** (`tests/ep/test_ep31.py`, `@pytest.mark.ep_31`) — fixture: attrition counts
   are non-increasing across steps and the first equals `count(*)` of icustays on the
   fixture; every descriptive table has `n`/`n_deaths` columns and no identifier columns;
   the run folder has all five files; `report.md` contains the claim-type label and the
   retrospective sentence; the number of `safe_query` audit lines with `actor = "tracer"`
   grew by exactly the number of calls made; either an OR table exists or the
   `not_fit` note does (if `not_fit`, add "enrich fixture outcomes" to the EP-33 retro).
   `tier("dev")`-marked: the dev run completes, `rows_suppressed` is reported, and no file under the
   run folder contains an identifier column name or a value longer than 64 characters.

## Out of scope

- Cohort spec/compiler/attrition diagram → EP-46/47/48; run ledger + seeds → EP-35/36; report engine → EP-130.
- Predictive modelling with holdouts, calibration, model cards → EP-110 (Signature #1).
- Promotion of the report into `docs/analyses/` → EP-43 (disclosure) / EP-53 (Capstone #1).
- Time semantics module (era helpers, censoring rule) → EP-34 — the tracer inlines its age/era logic and notes it for EP-34.

## Verification / acceptance

- `uv run poe test -m ep_31` green on fixture; `tier("dev")`-marked test green; `uv run --group dev mwh verify EP-31` green.
- Run folders exist for `dev` and `full` under `%MWH_DATA_ROOT%\runs\tracer\`; the full run was launched **in the background** (`runs\jobs\tracer-full.log`); both run ids and wall times are in the completion note.
- All attrition/descriptive numbers came through `safe_query` (audit lines with `actor = "tracer"` match the call count); the report labels the claim type and states the analysis is retrospective; nothing row-level appears in tool output or the run folder.
