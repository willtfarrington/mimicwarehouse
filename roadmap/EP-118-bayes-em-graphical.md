# EP-118 — Bayesian B: EM / mixtures / one graphical model / likelihood + bootstrap

**Size:** M · **Tier:** fixture+dev+full · **Core/Stretch:** core · **Depends on:** EP-117 (Bayesian A: PyMC + nutpie models + Bambi GLMM), EP-114 (Unsupervised A: clustering / mixtures / stability) · **Blocks:** EP-126 (Capstone #5)

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-101 (Re-plan P6) before execution.

## Context

Second half of category 25: the "show your work" brief for a DS/ML reader (D-1) — a hand-rolled EM
algorithm checked against sklearn, one graphical model, and likelihood-based inference set against the
cluster bootstrap. Everything is permissive-licence and small: no pgmpy (parked), no new dependencies.
Representative workflow (D-5, comorbidity theme): a latent-class model of Elixhauser comorbidity flags
per hospital admission fitted by EM (class count by BIC) with class-by-mortality and class-by-era
tables; a Gaussian graphical model of first-24 h labs/vitals via graphical lasso; and Weibull LOS
inference where profile-likelihood CIs are compared with EP-78 bootstrap CIs. Claim: exploratory.

## Scope sketch (refine at re-plan)

1. **EM** (`src/mimicwarehouse/ml/bayes.py`): `em_gmm(X, k)` (Gaussian mixture, full covariance) with a
   log-likelihood trace and agreement test against sklearn `GaussianMixture` on the EP-114 sepsis
   frame; `em_latent_class(B, k)` for binary indicators (conditional-independence latent-class model),
   BIC across k = 2…6, class-conditional response profiles.
2. **One graphical model** — `GraphicalLassoCV` on standardised first-24 h labs/vitals → precision
   matrix, partial-correlation network table and an Altair edge-list figure (aggregate parameters
   only); stability of the edge set across subject bootstraps.
3. **Likelihood + bootstrap** — MLE for a Weibull model of ICU LOS (with the discharge/death censoring
   rule from EP-76), profile-likelihood CIs vs percentile bootstrap CIs (EP-78, cluster by subject),
   and a Wald/LR/bootstrap comparison table.
4. **Representative report** — latent-class profiles and their mortality/era tables (suppressed by
   `mimicwarehouse.disclose`), network figure, CI comparison; dev in-session, full as a logged
   background job; claim label *exploratory*, retrospective statement.
5. **Tests** (`tests/ep/test_ep118.py`, `@pytest.mark.ep_118`): EM log-likelihood is non-decreasing;
   `em_gmm` matches sklearn within tolerance on the fixture; latent-class EM recovers planted classes;
   graphical lasso recovers a planted sparse precision structure; profile and bootstrap CIs overlap.

## Out of scope

- pgmpy / discrete Bayesian networks, UMAP/HDBSCAN → parked (final-roadmap 20–24).
- Bayesian model comparison at scale → parked (25); trajectory latent classes → EP-82.

## Verification / acceptance (sketch)

- `uv run poe test -m ep_118` green on fixture (+dev); `uv run --group dev mwh verify EP-118` green.
- Full-tier run id (background job, `%MWH_DATA_ROOT%\runs\jobs\ep118.log`) recorded; report artefact
  passes `mwh disclose check`.
