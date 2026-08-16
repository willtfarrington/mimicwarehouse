# EP-113 — Nonlinear / flexible modeling

**Size:** M · **Tier:** fixture+dev+full · **Core/Stretch:** core · **Depends on:** EP-80 (GLM suite B: interactions, nonlinear terms, diagnostics) · **Blocks:** EP-126 (Capstone #5)

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-101 (Re-plan P6) before execution.

## Context

Category 21 (nonlinear and flexible modeling) as *inference about shape*, not prediction: splines,
GAMs, kernel and local regression with partial-effect curves and CIs, sitting on the formulaic
splines and diagnostics of EP-80 and the GLM families of EP-79. Standing decision (roadmap risk 3):
statsmodels GAM, not pygam (scipy pin) — pygam/EBM stay parked. Representative workflow (D-5, glucose
theme): the U-shaped association between first-24 h mean glucose and in-hospital mortality among
first ICU stays, adjusted for age, sex and SOFA, with the ICD-era and diabetes-status interaction
checked. Claim type: associational (the report states "shape of association, not causal").

## Scope sketch (refine at re-plan)

1. **`src/mimicwarehouse/ml/flexible.py`** — thin, tidy-returning wrappers: restricted cubic splines
   (formulaic, from EP-80) inside EP-79 GLMs; statsmodels `GLMGam` with `BSplines`/`CyclicCubicSplines`
   and penalty selection by GCV; LOWESS (statsmodels) and Nadaraya–Watson kernel regression
   (`KernelReg`) for descriptive curves; `partial_effect(model, term, grid)` → curve + pointwise CI
   (delta method or EP-78 cluster bootstrap by `subject_id`).
2. **Model comparison** — linear vs spline vs GAM by AIC/deviance and grouped-CV log-loss (EP-104
   splitter), a knots-sensitivity table (3/4/5 knots), and a linearity test summary.
3. **Representative report** — glucose–mortality curve with CIs, stratified by diabetes phenotype
   (EP-41 T2DM) and by `anchor_year_group` era; dev in-session, full as a logged background job; Altair
   curve specs via `viz/`; claim label *associational* and the retrospective statement.
4. **Tests** (`tests/ep/test_ep113.py`, `@pytest.mark.ep_113`): a fixture with a planted quadratic
   effect is recovered by the spline/GAM but not the linear term (deviance drop); partial-effect CI
   widens with fewer subjects; curve tables pass `disclose.check`.

## Out of scope

- Prediction-oriented flexible models (trees/GBM) → EP-108; SHAP dependence → EP-120.
- pygam / interpret (EBM) → parked (final-roadmap 14–16); GAMs as multilevel → EP-81/117.

## Verification / acceptance (sketch)

- `uv run poe test -m ep_113` green on fixture (+dev); `uv run --group dev mwh verify EP-113` green.
- Full-tier run id (background job, `%MWH_DATA_ROOT%\runs\jobs\ep113.log`) recorded; report artefact
  labelled *associational* passes `mwh disclose check`.
