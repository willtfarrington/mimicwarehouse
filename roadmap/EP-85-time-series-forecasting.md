# EP-85 — Time-series & forecasting

**Size:** M · **Tier:** fixture+dev+full · **Core/Stretch:** core · **Depends on:** EP-56 (Latency marts B: hourly bins + <=5 s benchmark) · **Blocks:** EP-89 (Capstone #3)

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-74 (Re-plan P4) before execution.

## Context

Capability category 17 (*Time-series analysis and forecasting*). Works on the hourly-bin mart
(EP-56; `hour_bin` grain from EP-34) with statsforecast + statsmodels (standing decision). MIMIC
caveat that defines the design: per-patient date shift forbids cross-patient calendar series, so
every series is indexed by hours since ICU admission and cohort-level series are aggregates over
that relative axis; hour-of-day analyses are only admissible if time-of-day preservation is
confirmed at re-plan (state the assumption). Seeds per EP-36; suppression via `disclose` (D-33);
per-stay series never leave the data root. Theme per D-5: heart rate and MAP under norepinephrine
in the first 48 h (hemodynamics / vasopressor theme; `vasoactive` concept, EP-37).

## Scope sketch (refine at re-plan)

1. **`src/mimicwarehouse/stats/tsa.py`** — series builder (stay × hour × variable; missing-hour
   policy none / forward-fill ≤ k / linear), smoothing (rolling mean / median, EWMA, LOWESS),
   decomposition (STL on per-stay series ≥ 48 h and on cohort-mean series), change-point detection
   (binary segmentation / PELT via `ruptures` (BSD-2) or hand-rolled), lagged relations (CCF up
   to ± 6 h within stay; Granger test via statsmodels VAR on stays ≥ 48 h, reported as association).
2. **Forecasting harness** — per-stay univariate forecasts (statsforecast AutoETS / AutoARIMA /
   Naive / SeasonalNaive), horizon 6 h, rolling-origin evaluation (origins every 6 h after a 12 h
   burn-in), MAE / RMSE / MASE per origin aggregated across stays (median, IQR; small-n
   suppression), naive baseline comparison; runtime bounded by a seeded stratified sample of stays
   on full (or all stays as a background job).
3. **Outputs** — aggregate metric tables; `viz/` figures: cohort mean series ± CI, error-by-horizon
   curve, aggregate CCF; no per-stay export.
4. **Representative workflow**: ICU stays receiving norepinephrine → hourly HR and MAP over the
   first 48 h: smoothing, change points around vasopressor start, CCF between norepinephrine rate
   and MAP, 6-h-ahead MAP forecasts with rolling-origin validation vs naive → Markdown report via
   EP-59 (claim type *exploratory* — the forecasting harness is descriptive of within-stay series;
   no prediction claim beyond the rolling-origin benchmark; retrospective statement).
5. **Tests** `tests/ep/test_ep85.py` (`@pytest.mark.ep_85`): synthetic series with a known change
   point and known lag; harness metrics vs hand computation; determinism by seed; suppression;
   dev-tier run.

## Out of scope

- Sequence deep learning → EP-123; time-series foundation models → parked (TS-1).
- Trajectory features / grouping → EP-82; Explorer hourly views → EP-64–66.

## Verification / acceptance (sketch)

- `uv run poe test -m ep_85` green on fixture + dev; `uv run --group dev mwh verify EP-85` green.
- Full-tier run as a logged background job (`uv run --group dev mwh build --tier full --select
  analysis.tsa_hemodynamics --background --job ep85-tsa`); run id, wall time, sample size / seed
  in the completion note; figures + report pass `mwh disclose check`.

## Parked → final-roadmap.md

- State-space / Kalman models with missing hourly data (statsmodels UnobservedComponents);
  darts / sktime (TS-2); Chronos / MOMENT / TimesFM (TS-1).
