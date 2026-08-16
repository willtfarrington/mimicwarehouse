# EP-79 — GLM suite A: families + tidy()

**Size:** M · **Tier:** fixture+dev+full · **Core/Stretch:** core · **Depends on:** EP-75 (Endpoints A: binary/continuous/count/ordinal), EP-77 (Inference & group comparison) · **Blocks:** EP-80 (GLM suite B: interactions, nonlinear terms, diagnostics), EP-81 (Multilevel / repeated measures), EP-87 (Missing-data strategies), EP-88 (Analysis pages wave 1), EP-89 (Capstone #3), EP-96 (PS / IPTW / matching / balance / standardization)

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-74 (Re-plan P4) before execution.

## Context

Capability category 15 (*Regression and generalized linear modeling*). The tracer bullet (EP-31)
fitted one ad-hoc logistic model; this brief replaces it with a suite over statsmodels with
cluster-robust SEs by `subject_id` by default (standing decision), Polars in / pandas only at the
statsmodels boundary (D-17), endpoints from EP-75, the tidy-frame shape from EP-77, run records
(EP-35) and level-count suppression via `disclose` (D-33). Theme per D-5: the tracer cohort
(first-ICU-stay adults) with three families on three endpoints. Caveat: `anchor_year_group` is the
only admissible era covariate; ages ≥ 89 appear as 91 (treat as a top bin).

## Scope sketch (refine at re-plan)

1. **`src/mimicwarehouse/stats/glm.py`** — pydantic `GlmSpec` (formula, `family ∈ {gaussian,
   binomial, multinomial, ordinal, poisson, negbin, regularized}`, link, offset, weights,
   `cluster="subject_id"`, `cov_type="cluster"` with automatic HC1 fallback when clusters are
   singletons) and `fit_glm(df, spec) → GlmFit` wrapping OLS / GLM-Binomial / MNLogit /
   OrderedModel / Poisson / NegativeBinomial (alpha estimated) / `fit_regularized` (elastic net).
2. **Formula handling**: formulaic → design matrices with declared reference levels and categorical
   coding, arrays passed to statsmodels, term names preserved; centering/scaling helpers;
   `anchor_year_group` available as a factor.
3. **`tidy(fit)`** → Polars frame (term, estimate, std_error, ci_low, ci_high, statistic, p_value,
   `exp_estimate` for log/logit links, family, cov_type, n_obs, n_clusters) and **`glance(fit)`**
   (n, df, log-lik, AIC/BIC, deviance / pseudo-R², dispersion, converged, warnings). No row-level
   `augment` export: fitted values stay in the data root (binned summaries only, EP-80).
4. **Provenance**: `GlmFit.record(run)` writes formula, family, spec hash, coefficient table (any
   factor-level n < 11 suppressed via `disclose.suppress`), reference levels and convergence to
   `runs/<run_id>/tables/`; export through EP-59 primitives.
5. **Representative workflow**: tracer cohort → (a) logistic `in_hospital_death ~ age + sex +
   sepsis3 + first-24 h lactate + era`; (b) log-OLS `hospital_los_days`; (c) OrderedModel
   `discharge_disposition_ordinal`; all cluster-robust by subject → three tidy tables + one
   Markdown report via EP-59 (claim type *associational*; states "not causal" and retrospective).
6. **Tests** `tests/ep/test_ep79.py` (`@pytest.mark.ep_79`): known-answer vs direct statsmodels
   calls; cluster → HC1 fallback; MNLogit / OrderedModel on synthetic ordinal data; regularized
   path; NB alpha > 0; tidy schema stable; dev-tier run of the three models.

## Out of scope

- Interactions, splines, marginal effects, diagnostics → EP-80; mixed / GEE → EP-81.
- Propensity models and weighting → EP-96; GAM / kernel → EP-113; Bayesian GLM → EP-117.
- Analysis pages → EP-88.

## Verification / acceptance (sketch)

- `uv run poe test -m ep_79` green on fixture + dev; `uv run --group dev mwh verify EP-79` green.
- Full-tier fits launched as a logged background job (`uv run --group dev mwh build --tier full
  --select analysis.glm_tracer --background --job ep79-glm`); run id, wall time and — if EP-78 is
  ☑ — the first real-data `boot` timing recorded in the completion note.
- Report + tables pass `mwh disclose check`; the tidy schema is imported unchanged by EP-80/81/88.

## Parked → final-roadmap.md

- Zero-inflated / hurdle count models; Firth penalised logistic; quantile regression; lme4/mgcv via
  the R bridge (R-1).
