# EP-108 — Trees / ensembles A (DT, RF, bagging, LightGBM)

**Size:** M · **Tier:** fixture+dev+full · **Core/Stretch:** core · **Depends on:** EP-107 (Baselines (LR / regularized / kNN / SVM)) · **Blocks:** EP-109 (Trees / ensembles B (stacking; overfitting diagnostics)), EP-110 (Signature #1: first-24h → in-hospital mortality), EP-121 (GPU enablement (gpu group; doctor --gpu; XGBoost-CUDA vs LightGBM-CPU)), EP-126 (Capstone #5)

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-101 (Re-plan P6) before execution.

## Context

Category 22 (tree-based and ensemble learning). LightGBM on CPU is the P7 workhorse (D-16; XGBoost-CUDA
arrives as a comparator only in EP-121), and the champion model of Signature #1 (EP-110) comes from
this brief. Same runner, specs, assessment and registry as EP-107, so the deliverable is estimator
factories plus tree-specific handling of missingness (native NaN routing vs the EP-102 indicators),
monotone constraints and importance export. Representative workflow: the tracer dataset again, so
EP-107 vs EP-108 is a like-for-like comparison.

## Scope sketch (refine at re-plan)

1. **`src/mimicwarehouse/ml/trees.py`** — spec-driven factories for `DecisionTreeClassifier`,
   `RandomForestClassifier`, `BaggingClassifier`, and `lightgbm.LGBMClassifier` (CPU, `n_jobs = 12`,
   early stopping on an inner grouped validation fold, optional `monotone_constraints` from a
   feature → sign map, `class_weight`/`scale_pos_weight` option); a small default grid per algorithm
   evaluated with EP-104 nested CV; specs under `ml/specs/trees/*.yaml`.
2. **Missingness policy switch** — `missing: native | indicators | impute`; the run manifest records
   which; the report compares LightGBM native vs indicator handling on the tracer dataset.
3. **Importance export** — gain/split importances (LightGBM), impurity importances (RF/DT) as tidy
   tables in `runs/<run_id>/tables/`; permutation importance and SHAP are EP-120.
4. **Representative comparison** — DT vs RF vs bagging vs LightGBM on the tracer dataset with the
   EP-107 baselines in the same table; dev in-session, full as a logged background job (LightGBM full
   fit should be minutes: record wall time, peak RSS in the benchmark ledger); figures via EP-59.
5. **Tests** (`tests/ep/test_ep108.py`, `@pytest.mark.ep_108`): every factory fits on fixture; early
   stopping uses only inner-train/inner-validation subjects (overlap guard); monotone constraint is
   respected on a crafted monotone fixture; importances sum to 1 after normalisation.

## Out of scope

- Stacking, learning/validation curves, overfitting diagnostics → EP-109.
- XGBoost `device="cuda"` comparator and the `gpu` group → EP-121; SHAP → EP-120.
- The frozen-protocol signature run → EP-110.

## Verification / acceptance (sketch)

- `uv run poe test -m ep_108` green on fixture (+dev); `uv run --group dev mwh verify EP-108` green.
- Full-tier run id (background job, `%MWH_DATA_ROOT%\runs\jobs\ep108.log`) recorded; comparison
  report labelled *predictive* passes `mwh disclose check`; LightGBM model registered with a card.
