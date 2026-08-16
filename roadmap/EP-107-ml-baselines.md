# EP-107 — Baselines (LR / regularized / kNN / SVM)

**Size:** M · **Tier:** fixture+dev+full · **Core/Stretch:** core · **Depends on:** EP-105 (Assessment module), EP-106 (Model registry + model cards) · **Blocks:** EP-108 (Trees / ensembles A (DT, RF, bagging, LightGBM)), EP-126 (Capstone #5)

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-101 (Re-plan P6) before execution.

## Context

First estimator brief of category 20 (supervised prediction) and the pattern every later model brief
copies: a declared model spec → the standard `fit_evaluate` runner → run record (EP-35) → assessment
(EP-105) → registry + card (EP-106). Baselines matter for D-1 (a portfolio reader wants to see that
LightGBM earns its keep against a well-tuned logistic regression). Representative workflow: the
tracer dataset (first ICU stay adults, first-24 h features → in-hospital mortality, EP-102/103) with
grouped CV on the train era (EP-104). Standing decision: scikit-learn CPU; SHAP linear only later
(EP-120).

## Scope sketch (refine at re-plan)

1. **`src/mimicwarehouse/ml/baselines.py`** — `ModelSpec` pydantic model (`algorithm`, `params`,
   `preprocess`: imputer strategy + EP-102 normaliser + optional one-hot, `calibration`: none |
   isotonic | sigmoid via inner grouped CV) and sklearn `Pipeline` factories for logistic regression
   (unpenalised, L1, L2, elastic-net; `class_weight` option), kNN, and linear SVM (`LinearSVC` +
   `CalibratedClassifierCV`, grouped). Specs live in `ml/specs/baselines/*.yaml`.
2. **Runner** — `fit_evaluate(dataset_id, split_plan, model_spec, tier) -> (model_id, assessment)`:
   inner grouped CV for regularisation strength, out-of-fold predictions on the outer folds, assessment
   with cluster-bootstrap CIs, registration, benchmark-ledger entry (wall time, peak RSS). CLI
   `uv run --group dev mwh ml fit <spec.yaml> --dataset <id> --tier dev`.
3. **Representative comparison** — LR (L2) vs elastic-net vs kNN vs linear SVM on the tracer dataset:
   dev tier in the session; full tier as a logged background job; comparison table (EP-105 `compare`)
   and calibration/DCA figures exported via EP-59; short report artefact labelled *predictive*
   (retrospective MIMIC-IV).
4. **Tests** (`tests/ep/test_ep107.py`, `@pytest.mark.ep_107`): each spec fits on the fixture dataset;
   the runner refuses a spec whose preprocessing fits on the whole dataset instead of the training fold
   (a crafted leaky preprocess); model ids resolve in `runs.models`.

## Out of scope

- Trees / boosting → EP-108; stacking and overfitting diagnostics → EP-109.
- Nonlinear/GAM modelling as inference → EP-113; interpretation → EP-120.

## Verification / acceptance (sketch)

- `uv run poe test -m ep_107` green on fixture (+dev); `uv run --group dev mwh verify EP-107` green.
- Full-tier run id (background job, log at `%MWH_DATA_ROOT%\runs\jobs\ep107.log`) recorded in the
  completion note; comparison table + figures pass `mwh disclose check`; four registered models with
  cards.
