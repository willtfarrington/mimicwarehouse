# EP-94 — Recurrent events (Andersen–Gill)

**Size:** M · **Tier:** fixture+dev+full · **Core/Stretch:** core · **Depends on:** EP-92 (Parametric AFT, landmark, time-dependent covariates) · **Blocks:** EP-100 (Capstone #4)

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-90 before execution.

## Context

Closes the survival category (capability 18) with recurrent-event models on the counting-process
machinery of EP-92 (`survival/startstop.py`) and the recurrent endpoint tables of EP-76 (recurrent
KDIGO-AKI episodes, built on the EP-42 phenotype). Andersen–Gill is fitted with lifelines
`CoxTimeVaryingFitter` (D-34); robust sandwich SEs by `subject_id` are the default (P5/P6
standing decision). Death and discharge terminate follow-up, which is informative censoring for
recurrent events — the report says so explicitly. Results are labelled **associational**.

## Scope sketch (refine at re-plan)

1. **`survival/recurrent.py`** — `andersen_gill(cp_df, covariates, cluster="subject_id")` over
   the EP-76 counting-process table `(id, start, stop, event, episode_no)` (validated by the EP-92
   `startstop` checks); PWP gap-time and total-time variants via `strata="episode_no"`; `mcf()` —
   the mean cumulative function (Nelson–Aalen for recurrent events) with CI and an Altair spec
   builder in `viz/`; validator refuses non-nested intervals per id.
2. **Representative workflow** — tracer cohort (first ICU stay, adults, no AKI at ICU admission);
   events: the EP-76 `recurrent_aki_episodes` endpoint (KDIGO stage ≥ 1 episodes separated by
   ≥ 48 h at stage 0, per icustay); covariates: sepsis-3 (EP-42), vasopressor exposure
   as a time-dependent covariate (mimic-code concept), age, sex, baseline creatinine,
   `anchor_year_group`; MCF by sepsis status; Andersen–Gill rate ratios; PWP gap-time comparison;
   robust SEs by `subject_id`. Registered analysis step (`analysis.surv_recurrent`); full tier as
   a logged background job.
3. **Report artifact** — `runs/<run_id>/report/` (Markdown + figures) via EP-59: MCF figure,
   AG/PWP comparison table, terminal-event caveat, claim type **associational**, retrospective
   statement.
4. **Tests** — `tests/ep/test_ep94.py` (`@pytest.mark.ep_94`): synthetic Poisson-process
   recurrent DGP with a known covariate rate ratio is recovered by AG; MCF matches the closed
   form; robust SE exceeds naive SE under within-subject correlation; validator rejects
   overlapping episodes; fixture-tier smoke run of the workflow.

## Out of scope

- Recurrent hospital admissions per subject as a second AG application → EP-84 supplies the
  utilization counts; EP-90 may add it here if the EP-76 endpoint exists.
- Frailty / joint-frailty models → parked (final-roadmap § 11–13, UTIL-1).
- Page → EP-99; capstone → EP-100.

## Verification / acceptance (sketch)

- `uv run poe test -m ep_94` green on fixture (+dev); `uv run --group dev mwh verify EP-94` green.
- Full-tier run id + wall time recorded in the completion note; report artifact passes
  `uv run --group dev mwh disclose check <path>` and carries the associational claim label.
- The MCF/AG tables have no cell < 11 on export (sidecar).

## Parked → final-roadmap.md

- Shared-frailty (gamma) and joint frailty–terminal-event models — trigger: after this brief
  (already listed as UTIL-1).
- Wei–Lin–Weissfeld marginal model — trigger: reviewer request.
