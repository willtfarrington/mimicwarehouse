# EP-86 — Exposure-response / treatment patterns

**Size:** M · **Tier:** fixture+dev+full · **Core/Stretch:** core · **Depends on:** EP-80 (GLM suite B: interactions, nonlinear terms, diagnostics), EP-49 (Event-aligned timeline API) · **Blocks:** EP-89 (Capstone #3), EP-144 (ED-enabled workflow (ED triage → admission; time-to-antibiotics))

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-74 (Re-plan P4) before execution.

## Context

Capability category 12 (*Exposure-response and treatment-pattern queries*). Builds treatment
episodes from administration events (emar / emar_detail, ICU `inputevents`), anchors them with the
timeline API (EP-49: culture, med start, ICU in), summarises dose / duration / lags, and models
exposure-response with the RCS terms of EP-80. Code sets for drug classes come from EP-40, unit
harmonisation from EP-39, `suspicion_of_infection` and norepinephrine-equivalent dose from the
mimic-code concepts (EP-37). Everything here is *associational*: reports say so and hand causal
questions to EP-95–97 (D-5 theme: antibiotic timing in sepsis-3; the same lag machinery is reused
by EP-144 once ED triage times exist).

## Scope sketch (refine at re-plan)

1. **`src/mimicwarehouse/stats/exposure.py`** — `episodes(events, codeset, gap_hours)` (merge
   administrations of one drug class with gaps ≤ threshold; start / stop, n administrations,
   cumulative harmonised dose, duration, days of therapy), `first_exposure_lag(anchor)` via the
   timeline API, agent sequences (first → second agent), dose summaries per stay / day (max rate,
   time-weighted mean, cumulative), exposure categories with declared cut-points.
2. **`exposure_response(df, exposure, outcome, adjust, rcs_knots=4)`** — RCS logistic / linear
   via `stats/glm.py` + EP-80 `predict_curve`, nonlinearity test, categorical contrasts, and a
   report snippet carrying the confounding caveat.
3. **Treatment-pattern queries** — exposure prevalence per cohort / era, first-agent distribution,
   duration and dose distributions, anchor-lag distributions (all `disclose`-suppressed).
4. **Representative workflow**: sepsis-3 first ICU stays → time from suspected infection to first
   antibiotic administration (emar); lag distribution; RCS association of lag (hours, capped) with
   in-hospital death adjusted for age, SOFA, era → curve with CI band (`viz/`); antibiotic
   episodes (first agent class, duration); secondary: first-24 h peak norepinephrine-equivalent
   dose vs mortality curve → Markdown report via EP-59 (claim type *associational*; "not causal —
   see EP-95/96"; retrospective statement).
5. **Tests** `tests/ep/test_ep86.py` (`@pytest.mark.ep_86`): gap-merge logic on crafted events;
   cumulative dose with unit conversion; lag sign relative to anchor; RCS curve on synthetic
   monotone data; small cells suppressed; dev-tier run.

## Out of scope

- Causal effect estimation, PS / IPTW, target trials, sensitivity → EP-95, EP-96, EP-97.
- ED-anchored time-to-antibiotics → EP-144; care pathways → EP-83; new drug code sets → EP-40.
- Marginal structural models for time-varying exposure → parked (EXP-1).

## Verification / acceptance (sketch)

- `uv run poe test -m ep_86` green on fixture + dev; `uv run --group dev mwh verify EP-86` green.
- Full-tier run as a logged background job (`uv run --group dev mwh build --tier full --select
  analysis.exposure_antibiotics --background --job ep86-exposure`); run id + wall time in the
  completion note; curve, tables and report pass `mwh disclose check`.
- Lag machinery documented for reuse by EP-144.

## Parked → final-roadmap.md

- Marginal structural models (EXP-1); polypharmacy / drug-interaction networks; titration-protocol
  adherence metrics.
