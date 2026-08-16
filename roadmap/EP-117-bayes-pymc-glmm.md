# EP-117 — Bayesian A: PyMC + nutpie models + Bambi GLMM

**Size:** M · **Tier:** fixture+dev+full · **Core/Stretch:** core · **Depends on:** EP-81 (Multilevel / repeated measures) · **Blocks:** EP-118 (Bayesian B: EM / mixtures / one graphical model / likelihood + bootstrap), EP-126 (Capstone #5)

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-101 (Re-plan P6) before execution.

## Context

Category 25 (probabilistic and Bayesian analysis), first half, and the Bayesian arm of category 16.
Stack per D-16 and the planning defaults: PyMC with the nutpie sampler (numba backend, no MSVC on
Windows; JAX has no Windows CUDA so nothing here touches the GPU), Bambi for formula GLMMs, ArviZ for
diagnostics. EP-81 already fits the frequentist MixedLM/GEE and a first Bambi model; this brief adds
the full Bayesian workflow — priors, prior/posterior predictive checks, convergence diagnostics,
LOO comparison — on a bounded problem. Representative workflow (D-5): in-hospital mortality among first
ICU stays with varying intercepts by first ICU care unit and by `anchor_year_group` era, adjusting for
age, sex and SOFA. Data are aggregated to binomial strata where possible so full-tier sampling stays
minutes-scale; > 50k-row hierarchical models remain parked. Claim type: associational.

## Scope sketch (refine at re-plan)

1. **`src/mimicwarehouse/ml/bayes.py`** — `hier_logit(df, formula, groups, priors, seed)` building the
   PyMC model explicitly (non-centred varying intercepts) and the equivalent Bambi model; sampling with
   nutpie (chains 4, draws/tune configurable), seeds via EP-36; ArviZ summary (R-hat, ESS, divergences),
   prior predictive and posterior predictive check tables/figures, LOO/WAIC comparison against a pooled
   model; posterior tables (medians, HDI) as tidy Polars frames.
2. **Runtime guardrails** — aggregate to `(care unit, era, age band, sex, SOFA band)` binomial cells
   before sampling; wall time and peak RSS logged; a `max_rows` refusal that points to the parked
   minibatch lane.
3. **Representative report** — care-unit and era intercept forest plot (Altair via `viz/`), PPC figure,
   LOO table; dev in-session, full as a logged background job; claim label *associational* with the
   retrospective statement.
4. **Tests** (`tests/ep/test_ep117.py`, `@pytest.mark.ep_117`): a fixture with planted group effects is
   recovered within HDI; R-hat < 1.01 on the fixture run; Bambi and PyMC posteriors agree in sign and
   magnitude; the row-count guard *refuses* an oversized frame.

## Out of scope

- EM, latent-class, graphical models, likelihood-vs-bootstrap → EP-118.
- Bayesian survival, minibatch ADVI, model comparison at scale → parked (final-roadmap 25).

## Verification / acceptance (sketch)

- `uv run poe test -m ep_117` green on fixture (+dev); `uv run --group dev mwh verify EP-117` green.
- Full-tier run id (background job, `%MWH_DATA_ROOT%\runs\jobs\ep117.log`) with wall time recorded;
  report artefact passes `mwh disclose check`.
