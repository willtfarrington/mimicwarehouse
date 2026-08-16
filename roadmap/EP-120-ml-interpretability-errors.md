# EP-120 — Interpretability & error analysis

**Size:** M · **Tier:** fixture+dev+full · **Core/Stretch:** core · **Depends on:** EP-110 (Signature #1: first-24h → in-hospital mortality) · **Blocks:** EP-125 (ML pages in app), EP-126 (Capstone #5)

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-101 (Re-plan P6) before execution.

## Context

Category 30 (interpretability and error analysis). Standing P7 decision: SHAP tree and linear
explainers only (no kernel/deep SHAP). All outputs are *aggregate* — global importances, binned
dependence curves, error-group profiles — because individual explanations are row-level data (D-31/
D-32; an owner-only per-stay explanation view is left to the app behind the EP-58 gate). Representative
target: the EP-110 Signature #1 LightGBM and LR models. Feeds the ML pages (EP-125).

## Scope sketch (refine at re-plan)

1. **`src/mimicwarehouse/ml/interpret.py`** — `explain(model_id, tier)`: SHAP `TreeExplainer`
   (LightGBM/RF) and `LinearExplainer` (LR) on the held-out era; global importance (mean |SHAP|) with
   subject-bootstrap CIs; binned dependence tables for the top-10 features (feature bin → mean SHAP,
   n; bins with n < 11 suppressed); permutation importance with subject-grouped permutation; sklearn
   partial dependence for the same features; LR coefficient table with CIs and odds ratios.
2. **Error analysis** — `error_analysis(model_id, threshold)`: confusion table at the protocol
   thresholds; aggregate feature profiles of FN / FP / TN / TP groups (means, missingness rates);
   calibration and AUROC by care unit, admission type, age band and era; a simple slice finder over
   categorical features returning worst slices with n ≥ 11 only.
3. **Figures** — importance bar, dependence curves, subgroup forest via `viz/`; tables through
   `mimicwarehouse.disclose`; everything written to `runs/<run_id>/`.
4. **Representative report** — for the EP-110 models on dev in-session and full as a logged background
   job; a short "what the model leans on / where it fails" section reused by the model card and the
   capstone; claim label *predictive* (interpretation of a predictive model, not causal).
5. **Tests** (`tests/ep/test_ep120.py`, `@pytest.mark.ep_120`): SHAP values sum to prediction minus
   base value on fixture; dependence table has no bin below 11; slice finder never returns a slice
   below 11; explainer choice refuses an unsupported estimator class.

## Out of scope

- Per-stay explanations in the app (owner-gated) → EP-125 if at all; captum/neural → parked.
- Counterfactual/causal interpretation → P6 (EP-96/97).

## Verification / acceptance (sketch)

- `uv run poe test -m ep_120` green on fixture (+dev); `uv run --group dev mwh verify EP-120` green.
- Full-tier run id (background job, `%MWH_DATA_ROOT%\runs\jobs\ep120.log`) recorded; report artefact
  and figures pass `mwh disclose check`.
