# EP-96 — PS / IPTW / matching / balance / standardization

**Size:** M · **Tier:** fixture+dev+full · **Core/Stretch:** core · **Depends on:** EP-79 (GLM suite A: families + tidy()) · **Blocks:** EP-97 (Sensitivity analyses), EP-98 (Causal simulation tests (known truth)), EP-99 (Survival / causal app pages), EP-100 (Capstone #4)

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-90 before execution.

## Context

The point-exposure toolkit of capability 19: a **hand-rolled propensity-score module with explicit
diagnostics** (P6 standing decision) built on the EP-79 GLM suite (logit PS, weighted outcome
models with cluster-robust SEs) and the EP-78 cluster bootstrap. It is the module EP-97
(sensitivity), EP-98 (known-truth simulation) and the Causal page (EP-99) drive; the packet seeds
are Love plots, SMD balance and standardization. Reports are labelled associational or
**causal-with-assumptions** and state that analyses are retrospective. All exported tables pass
`mimicwarehouse.disclose` (D-33); no dowhy/econml in v1 (parked, D-34 permissive-only holds anyway).

## Scope sketch (refine at re-plan)

1. **`causal/ps.py` + `causal/weights.py`** — PS via EP-79 logit (formula; EP-80 splines
   optional); overlap diagnostics (mirror-histogram spec builder in `viz/`, common-support bounds,
   positivity warnings into the run record); ATE / ATT / overlap (ATO) weights, stabilized,
   truncation presets; effective sample size; weighted outcome models → risk difference, risk
   ratio, odds ratio or mean difference with robust SEs.
2. **`causal/matching.py` + `causal/balance.py`** — 1:k nearest-neighbour matching on the logit
   PS with caliper (default 0.2 SD), with/without replacement, deterministic order (seeded, EP-36);
   matched-set ids; SMD table before/after (weighted SMDs), variance ratios, |SMD| < 0.1 flag,
   Love-plot spec builder.
3. **`causal/standardization.py`** — g-computation via outcome regression (marginal risks under
   treat-all / treat-none), AIPW doubly-robust estimator optional; cluster-bootstrap CIs by
   `subject_id` (EP-78) for every estimator; a single `estimate_all()` returning a tidy table
   (estimator, estimand, estimate, CI, n, ESS).
4. **Representative workflow** — cohort: first-ICU-stay adults with first-day haemoglobin
   7–10 g/dL (EP-55 first-day labs); exposure: RBC transfusion within the first 24 h of ICU
   (blood-product code set, EP-40, over inputevents/emar); outcome: in-hospital mortality (EP-75);
   confounders: age, sex, `anchor_year_group`, admission type, first-day SOFA, lactate, lowest
   haemoglobin, MAP, vasopressors, ventilation, Charlson index; estimates: crude, IPTW-ATE,
   IPTW-ATT, matched-ATT, standardization; balance table + Love plot; overlap figure. Registered
   analysis step (`analysis.causal_ps_transfusion`); full tier as a logged background job.
5. **Report artifact** — `runs/<run_id>/report/` (Markdown + figures) via EP-59: overlap and
   Love figures, balance table, effect table, assumption list (exchangeability, positivity,
   consistency), claim label, retrospective statement. Claim type is `causal` (rendered
   "causal-with-assumptions" with the assumptions list) when the balance/positivity diagnostics
   pass the thresholds in item 2, otherwise `associational`; the rule and the outcome are written
   into the report.
6. **Tests** — `tests/ep/test_ep96.py` (`@pytest.mark.ep_96`): a small inline known-truth DGP
   (the full battery is EP-98) — weighting achieves |SMD| < 0.1 on all confounders; matching
   respects the caliper and is deterministic under a fixed seed; ESS ≤ n; positivity warning fires
   on a crafted non-overlap covariate; fixture-tier smoke run of the workflow.

## Out of scope

- E-values, negative controls, trimming grids → EP-97; simulation battery with coverage → EP-98.
- Target-trial / sequential designs → EP-95; Causal page → EP-99.
- dowhy refuters, econml heterogeneous effects, TMLE → parked (final-roadmap § 19, CAUS-1).

## Verification / acceptance (sketch)

- `uv run poe test -m ep_96` green on fixture (+dev); `uv run --group dev mwh verify EP-96` green.
- Full-tier run id + wall time recorded in the completion note; report artifact passes
  `uv run --group dev mwh disclose check <path>` and carries its claim label.
- Love plot / balance table show post-weighting |SMD| < 0.1, or the report explains why not.

## Parked → final-roadmap.md

- Heterogeneous effects (econml DR-learner / causal forests), dowhy refuters — after EP-96–98
  (already CAUS-1); TMLE / super-learner nuisance models — trigger: reviewer request.
