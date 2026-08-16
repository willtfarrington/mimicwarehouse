# EP-105 — Assessment module

**Size:** M · **Tier:** fixture · **Core/Stretch:** core · **Depends on:** EP-104 (Splits (grouped/temporal by anchor_year_group), CV, nested CV), EP-78 (Cluster bootstrap `boot` module) · **Blocks:** EP-107 (Baselines (LR / regularized / kNN / SVM)), EP-125 (ML pages in app), EP-126 (Capstone #5)

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-101 (Re-plan P6) before execution.

## Context

Second half of category 28 (model assessment and selection) and one third of the signature depth
(D-6: prediction + assessment + leakage/drift). One module computes discrimination, calibration,
decision-curve analysis, threshold tables and bootstrap confidence intervals so every P7 model —
baseline, GBM, foundation model, sequence model — is judged identically and every model card (EP-106)
and ML page (EP-125) reads the same tables. Uncertainty comes from the cluster bootstrap by
`subject_id` (EP-78), never from row-wise resampling. Tier fixture: known-answer synthetic tests.
Category 28's representative full-tier workflow is the assessment of the EP-107 baselines and the
EP-110 signature #1 models on the temporal holdout (per-era table, calibration curve, DCA, threshold
table, paired-bootstrap `compare`) — its full-tier run id and disclosure-checked,
`predictive`-labelled report artefact are recorded in EP-107/EP-110's completion notes; this brief
ships the library on fixture only.

## Scope sketch (refine at re-plan)

1. **`src/mimicwarehouse/ml/assess.py`** — `assess_binary(y, p, subject_ids, plan) -> Assessment`:
   AUROC, AUPRC, Brier, log-loss; calibration slope/intercept, expected calibration error, calibration
   curve table (bin, mean predicted, observed, n); decision curve (net benefit vs threshold, with
   treat-all / treat-none references); threshold table (sensitivity, specificity, PPV, NPV, alerts per
   100 at named thresholds); optional subgroup breakdown by a categorical column. Every metric carries a
   percentile CI from `boot` (default B = 500, seed from EP-36).
2. **`assess_regression`** (RMSE, MAE, R², calibration-in-the-large) and a minimal
   `assess_tte` (Harrell's C via lifelines) — the horizon-specific time-to-event metrics are extended in
   EP-112.
3. **Comparison helpers** — `compare(assessments)` with paired-bootstrap differences in AUROC/Brier
   across models on the same folds; per-fold and per-era (`anchor_year_group`) tables.
4. **Outputs** — tidy Polars tables + Altair specs from `viz/` (ROC, PR, calibration, DCA); tables pass
   through `mimicwarehouse.disclose` (calibration bins with n < 11 flagged/suppressed) before any export.
5. **Tests** (`tests/ep/test_ep105.py`, `@pytest.mark.ep_105`): perfect / random classifiers give AUROC
   1.0 / ≈ 0.5; logistic-generated data yields calibration slope ≈ 1; DCA net benefit at threshold 0
   equals prevalence; CIs are reproducible under seed; a calibration table with a small bin is
   suppressed on export.

## Out of scope

- Model fitting (any estimator) → EP-107+; SHAP and error slices → EP-120; drift by era → EP-119.
- Charts on a page → EP-125; card templates → EP-106/EP-132.

## Verification / acceptance (sketch)

- `uv run poe test -m ep_105` green on fixture; `uv run --group dev mwh verify EP-105` green.
- Assessment tables and figures for the fixture dataset exported via EP-59 primitives pass
  `mwh disclose check`.
- EP-107's full-tier comparison table is produced by this module unchanged (asserted by a fixture
  golden of the tidy schema).
