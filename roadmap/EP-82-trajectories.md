# EP-82 — Longitudinal trajectories (+ trajectory groups)

**Size:** M · **Tier:** fixture+dev+full · **Core/Stretch:** core · **Depends on:** EP-49 (Event-aligned timeline API), EP-81 (Multilevel / repeated measures) · **Blocks:** EP-89 (Capstone #3)

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-74 (Re-plan P4) before execution.

## Context

Capability category 9 (*Longitudinal trajectory analysis*). Builds per-stay trajectory features on
event-aligned series from the timeline API (EP-49: anchors, windows, hourly bins) and the random
slopes of EP-81, then groups trajectories with a transparent method (k-means on standardised
features; GBTM / latent-class / DTW are parked, TRAJ-1). Seeds per EP-36; suppression of small
groups via `disclose` (D-33). Theme per D-5: lactate clearance and cumulative vasopressor exposure
in sepsis-3 first ICU stays (mimic-code `vasoactive` / norepinephrine-equivalent dose concept,
EP-37). Per-stay feature tables are row-level and stay in the data root; only group profiles and
aggregate associations leave it.

## Scope sketch (refine at re-plan)

1. **`src/mimicwarehouse/stats/trajectories.py`** — `features(series, spec)` per stay: slope
   (per-stay OLS and EP-81 random slope), variability (SD, CV, RMSSD), threshold crossings (first
   time below/above, count), time-to-normalisation, cumulative exposure (AUC; rate × duration
   dose-hours), peak / nadir and time-to-peak, measurement density, rule-based
   recovery / deterioration labels with declared thresholds.
2. **Grouping** — standardised feature matrix → sklearn k-means, k by silhouette over a declared
   range, stability = seeded resampling of stays with a Jaccard index; groups with n < 11 merged
   or dropped with a note; group profiles = mean ± CI by hour bin (aggregate).
3. **Association** — trajectory group → in-hospital death via `stats/glm.py` (EP-79) logistic
   adjusted for age and SOFA; tidy table with the associational caveat.
4. **Representative workflow**: sepsis-3 first ICU stays with ≥ 2 lactate values in the first
   24 h → lactate clearance (≥ 10 % fall within 6 h), time to < 2 mmol/L, variability; cumulative
   norepinephrine-equivalent dose over 24 h; k-means groups (k = 3–4) → profile figure (`viz/`
   Altair) + association table → Markdown report via EP-59 (claim type *exploratory* for the
   grouping, with the group→mortality table labelled *associational* in its own caption;
   retrospective statement).
5. **Tests** `tests/ep/test_ep82.py` (`@pytest.mark.ep_82`): features on synthetic series with
   known slope / AUC / crossings; grouping determinism by seed; small-group suppression; hypothesis
   over monotone series; dev-tier run.

## Out of scope

- Mixed models → EP-81; time-series smoothing / forecasting → EP-85; treatment episodes → EP-86.
- Timeline viewer (row-level) → EP-67; GBTM / latent class / DTW → parked.

## Verification / acceptance (sketch)

- `uv run poe test -m ep_82` green on fixture + dev; `uv run --group dev mwh verify EP-82` green.
- Full-tier feature build + grouping as a logged background job (`uv run --group dev mwh build
  --tier full --select analysis.trajectories_lactate --background --job ep82-traj`); run id +
  wall time in the completion note; profile figure and report pass `mwh disclose check`.
- Group sizes in every exported table are ≥ 11 (test).

## Parked → final-roadmap.md

- GBTM / latent-class growth / DTW clustering (TRAJ-1); functional PCA of trajectories.
