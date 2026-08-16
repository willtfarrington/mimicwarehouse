# EP-80 — GLM suite B: interactions, nonlinear terms, diagnostics

**Size:** M · **Tier:** fixture+dev+full · **Core/Stretch:** core · **Depends on:** EP-79 (GLM suite A: families + tidy()) · **Blocks:** EP-86 (Exposure-response / treatment patterns), EP-88 (Analysis pages wave 1), EP-89 (Capstone #3), EP-113 (Nonlinear / flexible modeling)

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-74 (Re-plan P4) before execution.

## Context

Capability category 15 continued: interaction terms, nonlinear terms (restricted cubic splines via
formulaic transforms), marginal effects and model diagnostics on top of EP-79's `GlmFit`. The
disclosure constraint shapes the design (D-33, GOVERNANCE §7): diagnostic figures must be
aggregate — binned residuals with n ≥ 11 per bin, influence reported as counts above thresholds,
never per-observation points or ids embedded in a Vega spec. Figures are Altair specs from `viz/`
so the same spec renders in EP-88 and in reports. Theme per D-5: the tracer cohort's mortality
model extended with splines and an age × sepsis-3 interaction, plus an overdispersion check on ICU
LOS.

## Scope sketch (refine at re-plan)

1. **Term builders** in `src/mimicwarehouse/stats/glm.py`: interactions (`a:b`, `a*b`), `rcs(x,
   knots=4)` restricted cubic splines (knots at Harrell quantiles when formulaic has no RCS
   built-in; `bs()`/`cr()` also exposed), polynomials, logs; `wald_test_terms(fit)` joint tests per
   term block with the cluster-robust covariance.
2. **Marginal effects**: `predict_curve(fit, term, grid, at=…)` → predicted response with
   delta-method CIs at grid points; average marginal effects; interaction contrasts (e.g. effect of
   sepsis-3 by age decade). Outputs are aggregate grids, never per-row predictions.
3. **Diagnostics**: deviance / Pearson residuals; binned residual plot (Gelman–Hill, bins n ≥ 11);
   leverage / Cook's distance as counts above cut-offs; VIF and condition number; dispersion ratio
   for Poisson (recommend NB when > 1.5); calibration-by-decile table (n ≥ 11 per decile);
   linearity check = linear vs `rcs()` Wald/LR; separation and convergence warnings.
4. **Figures** via `src/mimicwarehouse/viz/` spec builders (binned residual, partial-effect curve
   with CI band, calibration-by-decile) saved as PNG + Vega JSON through EP-59.
5. **Representative workflow**: tracer cohort in-hospital-death logistic (EP-79) extended with
   `rcs(age,4)`, `rcs(lactate,4)` and `age × sepsis3`; partial-effect curves; binned residuals;
   Poisson vs NB for `icu_los_days` (dispersion) → Markdown report via EP-59 (claim type
   *associational*; retrospective statement).
6. **Tests** `tests/ep/test_ep80.py` (`@pytest.mark.ep_80`): knot placement; joint Wald p vs
   statsmodels; prediction-CI shapes; overdispersion detected on synthetic NB data; every exported
   table/figure has bins ≥ 11 and no per-row records (guard test); dev-tier run.

## Out of scope

- GAM / kernel / loess → EP-113; substantive exposure-response workflow → EP-86.
- Discrimination / calibration / DCA for prediction models → EP-105; PS balance → EP-96.
- Pages → EP-88.

## Verification / acceptance (sketch)

- `uv run poe test -m ep_80` green on fixture + dev; `uv run --group dev mwh verify EP-80` green.
- Full-tier run as a logged background job (`uv run --group dev mwh build --tier full --select
  analysis.glm_diagnostics --background --job ep80-glm-diag`); run id + wall time in the
  completion note; report + figures pass `mwh disclose check` (no embedded row arrays).
- A crafted per-row diagnostic export is refused by the export path (test).

## Parked → final-roadmap.md

- Fractional polynomials / Box–Tidwell; pygam / EBM (GAM-1).
