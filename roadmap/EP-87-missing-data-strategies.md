# EP-87 — Missing-data strategies

**Size:** M · **Tier:** fixture+dev+full · **Core/Stretch:** core · **Depends on:** EP-72 (Missing-data views), EP-79 (GLM suite A: families + tidy()) · **Blocks:** EP-89 (Capstone #3)

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-74 (Re-plan P4) before execution.

## Context

Capability category 7 (*Missing-data and measurement-process analysis*), final piece after the
measurement-process summaries (EP-45) and missingness views (EP-72). Compares complete-case,
missing-indicator and multiple imputation (statsmodels MICE for inference; sklearn imputers stay
on the prediction side, P7) on one EP-79 GLM, pooling MICE with Rubin's rules, and adds an
informative-presence variant (measured / unmeasured indicators as covariates, EP-45 concept).
Seeds per EP-36; suppression via `disclose` (D-33). Theme per D-5: the tracer cohort's mortality
model with first-24 h labs that are frequently unmeasured (lactate, albumin, bilirubin, INR —
final pick from the EP-72 views).

## Scope sketch (refine at re-plan)

1. **`src/mimicwarehouse/stats/missing.py`** — strategies `complete_case`, `indicator` (missing
   indicator + median / mode fill), `single_impute` baseline, `mice` (statsmodels `MICEData` +
   `MICE`, m = 10 default, PMM for continuous / logistic for binary, seeded); `compare_strategies(df,
   glm_spec, strategies)` → tidy frame per strategy (EP-79 shape) + pooled MICE row (within /
   between variance, df, fraction of missing information) + coefficient-shift figure (`viz/`).
2. **Missingness diagnostics wiring** — pattern counts from EP-72 (`qc/`) summarised with
   suppression; a "missing ~ covariates" logistic to characterise MAR plausibility; the MAR
   assumption stated in the report template.
3. **Informative presence** — measured indicators added as covariates and compared with the
   strategies above (formal MNAR sensitivity parked).
4. **Representative workflow**: tracer cohort → in-hospital-death logistic (EP-79 spec) with the
   selected labs → complete-case vs indicator vs MICE vs informative-presence → table of coefficient
   shifts, FMI, n used per strategy; dot-and-whisker figure per term × strategy → Markdown report
   via EP-59 (claim type *exploratory* — a methods-comparison; the MAR assumption stated;
   retrospective statement).
5. **Tests** `tests/ep/test_ep87.py` (`@pytest.mark.ep_87`): simulated MAR data with a known
   coefficient — MICE closer to truth than complete case; Rubin pooling vs hand computation;
   determinism by seed; m configurable; dev-tier run.

## Out of scope

- Prediction-time imputation pipelines → EP-102 / EP-107; miceforest / tree imputers → parked
  (MISS-1); measurement-process summaries → EP-45; missingness UI → EP-72.

## Verification / acceptance (sketch)

- `uv run poe test -m ep_87` green on fixture + dev; `uv run --group dev mwh verify EP-87` green.
- Full-tier run as a logged background job (`uv run --group dev mwh build --tier full --select
  analysis.missing_strategies --background --job ep87-missing`); run id + wall time in the
  completion note; comparison table, figure and report pass `mwh disclose check`.

## Parked → final-roadmap.md

- Delta-adjustment / pattern-mixture MNAR sensitivity; miceforest (MISS-1); formal
  informative-presence models (MISS-2).
