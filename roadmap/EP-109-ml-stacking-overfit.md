# EP-109 — Trees / ensembles B (stacking; overfitting diagnostics)

**Size:** M · **Tier:** fixture+dev+full · **Core/Stretch:** core · **Depends on:** EP-108 (Trees / ensembles A (DT, RF, bagging, LightGBM)) · **Blocks:** EP-126 (Capstone #5)

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-101 (Re-plan P6) before execution.

## Context

Completes category 22 and adds the overfitting story a hiring reader looks for (D-1): learning curves,
validation curves and train–test gaps computed on subject-grouped folds (EP-104), plus a stacked
ensemble built strictly from out-of-fold predictions. sklearn's `StackingClassifier` cannot be trusted
to respect subject groups without metadata routing, so the stacker is hand-rolled over the EP-104
iterators. Representative workflow: the tracer dataset with the EP-107/108 base learners.

## Scope sketch (refine at re-plan)

1. **`stack()` in `src/mimicwarehouse/ml/trees.py`** — out-of-fold predictions from a list of registered
   base models (re-fitted per outer fold with the same grouped plan), a logistic meta-learner on the
   OOF matrix, and the same assessment/registry path; the meta-learner's coefficients become a
   "which base learner carries the ensemble" table.
2. **Overfitting diagnostics** (`src/mimicwarehouse/ml/assess.py` extension) — learning curve (training
   subjects vs AUROC/Brier on train and validation), validation curves over `max_depth`,
   `n_estimators`, `num_leaves`, `min_child_samples`, regularisation strength; LightGBM early-stopping
   trace; train–validation gap table; per-`anchor_year_group` performance of the final models (an early
   look at drift, formalised in EP-119). Altair specs from `viz/`.
3. **Representative report** — stacked LR + RF + LightGBM vs the best single model on the tracer
   dataset; the diagnostics figures; dev in-session, full as a logged background job; claim type
   *predictive*.
4. **Tests** (`tests/ep/test_ep109.py`, `@pytest.mark.ep_109`): the OOF matrix has no subject overlap
   between the rows a base model predicted and the rows it was trained on; learning curve is
   monotone-ish on a crafted large-signal fixture; the stacker refuses base models fitted on a different
   dataset id or split plan.

## Out of scope

- Ablation grids and repeated-seed benchmarking → EP-124.
- Drift/robustness audits proper → EP-119; interpretation → EP-120.

## Verification / acceptance (sketch)

- `uv run poe test -m ep_109` green on fixture (+dev); `uv run --group dev mwh verify EP-109` green.
- Full-tier run id (background job, `%MWH_DATA_ROOT%\runs\jobs\ep109.log`) recorded; report + curves
  pass `mwh disclose check`.
