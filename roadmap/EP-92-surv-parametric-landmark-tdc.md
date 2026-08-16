# EP-92 — Parametric AFT, landmark, time-dependent covariates

**Size:** M · **Tier:** fixture+dev+full · **Core/Stretch:** core · **Depends on:** EP-91 (KM / Cox / Schoenfeld) · **Blocks:** EP-94 (Recurrent events (Andersen–Gill)), EP-95 (Target-trial emulation harness), EP-100 (Capstone #4)

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-90 before execution.

## Context

Second survival brief (capability 18). It adds parametric accelerated-failure-time models, the
immortal-time-bias toolkit (landmark analysis, counting-process start–stop format,
time-dependent Cox) and inverse-probability-of-censoring weights (IPCW) that EP-94 and the
target-trial harness (EP-95) build on. It sits on `survival/km.py`/`cox.py` (EP-91), the EP-49
timeline windows and the EP-76 endpoints; lifelines remains the engine (D-34). Time is always
relative to ICU intime (per-patient date shift, EP-34); `dod` censoring follows the EP-34 horizon
rule. Results are labelled **associational**.

## Scope sketch (refine at re-plan)

1. **`survival/aft.py`** — Weibull, log-normal and log-logistic AFT via lifelines
   (`WeibullAFTFitter` etc.); `tidy()` (time ratios, CI, p) in the EP-79/EP-91 contract; AIC/BIC
   comparison against Cox; predicted survival at named covariate profiles; Cox–Snell residual /
   QQ spec builder in `viz/`.
2. **`survival/startstop.py`** — `to_counting_process(events, covariate_intervals)` building
   `(id, start, stop, event, covariates)` rows from EP-49 windows / EP-76 endpoints at the
   `icustay` grain (EP-34); validator refuses overlapping or zero-length intervals and more than
   one terminal event per id.
3. **`survival/landmark.py` + `survival/tdc.py`** — landmark analysis at time L (exposure fixed
   at L, follow-up restarts at L; multi-landmark supermodel optional) and time-dependent Cox via
   lifelines `CoxTimeVaryingFitter` with cluster-robust SEs by `subject_id`.
4. **`survival/ipcw.py`** — IPCW with a KM-based or Cox-based censoring model over start–stop
   rows, stabilized, truncated at configurable percentiles; weight-diagnostics table (mean, max,
   effective sample size) — consumed by EP-93 (optional Fine–Gray route) and EP-95.
5. **Representative workflow (immortal-time demonstration)** — tracer cohort (first ICU stay,
   adults); exposure: invasive mechanical ventilation (mimic-code `ventilation` concept, EP-37/38);
   outcome: death within 28 d of ICU intime (EP-76 `TimeToEvent` instance `time_to_death_28d_icu`,
   registered here alongside EP-91's 90-d endpoint); three estimates side by side — naive
   "ever-ventilated" Cox (biased), landmark at 48 h, time-dependent Cox — plus Weibull /
   log-normal / log-logistic AFT for the EP-91 90-day outcome compared with Cox by AIC. Registered
   analysis step (`analysis.surv_aft_landmark_tdc`); full tier as a logged background job.
6. **Report + tests** — `runs/<run_id>/report/` (Markdown + figures) via EP-59 with the
   three-estimate comparison table, AFT table and claim label; `tests/ep/test_ep92.py`
   (`@pytest.mark.ep_92`): synthetic DGP with immortal time → naive HR shifted, TDC recovers the
   truth; AFT recovers Weibull parameters; validator refuses overlapping intervals; IPCW weights
   average ≈ 1 under independent censoring.

## Out of scope

- Competing risks → EP-93; recurrent events (Andersen–Gill on the start–stop table) → EP-94.
- Target-trial emulation and artificial censoring → EP-95.
- Survival page → EP-99; capstone narrative → EP-100.

## Verification / acceptance (sketch)

- `uv run poe test -m ep_92` green on fixture (+dev); `uv run --group dev mwh verify EP-92` green.
- Full-tier run id + wall time recorded in the completion note; report artifact passes
  `uv run --group dev mwh disclose check <path>` and carries the associational claim label.
- The comparison table shows naive, landmark and time-dependent estimates with CIs; no cell < 11.

## Parked → final-roadmap.md

- Joint longitudinal–survival models — trigger: after EP-82 + this brief (already SURV-2).
- Royston–Parmar spline hazards; landmark supermodels — trigger: reviewer request.
