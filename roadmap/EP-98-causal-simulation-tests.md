# EP-98 — Causal simulation tests (known truth)

**Size:** M · **Tier:** fixture · **Core/Stretch:** core · **Depends on:** EP-96 (PS / IPTW / matching / balance / standardization) · **Blocks:** EP-100 (Capstone #4)

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-90 before execution.

## Context

The P6 ordering rationale requires that the causal module be validated against known truth
**before** any real-data causal claim is cited (README P6). This brief builds a synthetic
data-generating-process library and an estimator battery in `causal/sim.py`, runs entirely on the
`fixture` tier only (D-18; no MIMIC data touched; ids ≥ 90 000 000 where ids appear at all), and
produces the coverage table the capstone (EP-100) cites; DGPs return Polars frames (D-17) and the
results pass `mwh disclose check` for habit (D-40). Seeds follow the EP-36 policy; joblib
parallelism uses `if __name__ == "__main__":` guards (Windows spawn). It also serves capability 31
(simulation / benchmarking) as a supporting workflow.

## Scope sketch (refine at re-plan)

1. **`causal/sim.py` — DGP library** — `dgp_point_exposure(n, ate, confounding, unmeasured=0.0,
   positivity_violation=False, outcome="binary|continuous", seed)` returning Polars frames with
   potential outcomes and the true estimand; `dgp_sequential(n, effect, time_varying_confounding,
   seed)` for the EP-95 harness; every DGP documents its structural equations in the docstring.
2. **Estimator battery** — `run_battery(scenarios, estimators, R=200, n=2000)` calling the EP-96
   estimators (crude, IPTW-ATE/ATT, matched-ATT, standardization, AIPW if built) and the EP-95
   harness for the sequential DGP; metrics: bias, RMSE, CI coverage, mean CI width; results table
   + a coverage figure (Altair spec builder in `viz/`); an opt-in `--long` mode for larger R/n.
3. **Scenario matrix as tests** — `tests/ep/test_ep98.py` (`@pytest.mark.ep_98`, fixture only):
   (a) no confounding → all estimators unbiased; (b) measured confounding → crude biased, adjusted
   unbiased and coverage ≈ 95 %; (c) unmeasured confounder → all biased and the EP-97 E-value of
   the biased estimate is at least the induced bias strength; (d) positivity violation → warnings
   fire and trimming reduces bias; (e) sequential DGP → naive biased, IPCW harness unbiased;
   (f) misspecified PS with a correct outcome model → standardization/AIPW remain unbiased.
4. **Gate + docs** — `uv run --group dev mwh verify EP-98` runs the battery at test size and is
   named as a prerequisite in the EP-100 capstone; a short `docs/methods/causal-simulation.md`
   describes scenarios and results (synthetic-only, still passed through `mwh disclose check`
   for habit and a sidecar); results table saved under `runs/<run_id>/` on the fixture tier.

## Out of scope

- Real-data claims and their sensitivity → EP-96 / EP-97 / EP-100.
- The general simulation / ablation / benchmark harness for ML → EP-124.
- dowhy refutation tests → parked (final-roadmap § 19, CAUS-1).

## Verification / acceptance (sketch)

- `uv run poe test -m ep_98` green on fixture within the size budget (test-size R/n); `uv run
  --group dev mwh verify EP-98` green.
- Results table and coverage figure exist under the recorded fixture run id and are cited by
  EP-100; `docs/methods/causal-simulation.md` exists with a `.disclosure.json` sidecar.
- Determinism: two runs with the same seed produce identical tables.

## Parked → final-roadmap.md

- Larger simulation grids (heterogeneous effects, misspecified outcome models, non-collapsibility
  studies) — trigger: after EP-124 provides the general harness.
