# EP-81 — Multilevel / repeated measures

**Size:** M · **Tier:** fixture+dev+full · **Core/Stretch:** core · **Depends on:** EP-79 (GLM suite A: families + tidy()) · **Blocks:** EP-82 (Longitudinal trajectories (+ trajectory groups)), EP-89 (Capstone #3), EP-117 (Bayesian A: PyMC + nutpie models + Bambi GLMM)

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-74 (Re-plan P4) before execution.

## Context

Capability category 16 (*Repeated-measures and multilevel modeling*). Repeated labs per ICU day per
stay per subject are the natural MIMIC hierarchy; this brief wraps statsmodels `MixedLM` (random
intercepts and slopes, nested variance components) and `GEE` (working correlations, sandwich SEs)
in the EP-79 tidy conventions, and runs one Bambi GLMM as a feasibility smoke that EP-117 deepens.
Grain `icu_day` from EP-34; long-format shaping via the timeline API (EP-49) and first-day/hourly
marts (EP-55/56); Polars primary, pandas at the boundary (D-17). Theme per D-5: daily creatinine
over ICU days 1–7 (AKI / creatinine-trajectory theme; KDIGO stage from EP-42). Random slopes from
this brief feed EP-82.

## Scope sketch (refine at re-plan)

1. **`src/mimicwarehouse/stats/mixed.py`** — `MixedSpec` (formula, `groups` = `subject_id` or
   `stay_id`, `re_formula` for random slopes, `vc_formula` for stay-within-subject nesting, REML
   flag) and `fit_mixed(df, spec)` → fixed-effect tidy (EP-79 shape), random-effect variance table,
   ICC, convergence diagnostics; profile-likelihood CIs optional.
2. **`fit_gee(df, spec)`** — families binomial / poisson / gaussian; working correlation
   exchangeable / AR(1) / independence; cluster = subject or stay; robust SEs; QIC; tidy output.
3. **Bambi GLMM smoke** — one random-intercept + slope logistic (`nutpie` sampler, short chains,
   dev tier only, ArviZ summary); if the pytensor/nutpie stack does not resolve that day, record it
   and hand the model to EP-117 via the toolchain-remediation slot.
4. **Shaping helpers** — long frame `(subject_id, stay_id, day, value)` at `icu_day` grain from the
   timeline API; time centering; irregular/missing days stay missing (imputation → EP-87).
5. **Representative workflow**: first ICU stays with ≥ 2 daily creatinine values → creatinine ~
   day × admission KDIGO stage + age, random intercept + slope per stay nested in subject; GEE
   (AR(1)) for daily "KDIGO stage ≥ 1" → variance-component and fixed-effect tables + aggregate
   mean-trajectory figure by group with CI bands (`viz/`; per-stay lines only behind the owner
   row-view in-app) → Markdown report via EP-59 (claim type *associational*; retrospective).
6. **Tests** `tests/ep/test_ep81.py` (`@pytest.mark.ep_81`): recovery of known variance components
   on simulated nested data (tolerance); GEE vs direct statsmodels; nested `vc_formula` syntax;
   convergence flags surface as warnings; dev-tier run.

## Out of scope

- Trajectory features and grouping → EP-82; full Bayesian hierarchical models → EP-117.
- Time-varying covariates in survival → EP-92; imputation of missing days → EP-87.

## Verification / acceptance (sketch)

- `uv run poe test -m ep_81` green on fixture + dev; `uv run --group dev mwh verify EP-81` green.
- Full-tier fits as a logged background job (`uv run --group dev mwh build --tier full --select
  analysis.mixed_creatinine --background --job ep81-mixed`); run id, wall time and peak RSS in
  the completion note (MixedLM on full may be slow — never in the foreground); report passes
  `mwh disclose check`.

## Parked → final-roadmap.md

- Crossed random effects / three-level frequentist GLMMs (glmmTMB, lme4 via R-1).
