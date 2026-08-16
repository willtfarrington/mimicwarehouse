# EP-124 — Simulation / ablation / benchmark harness

**Size:** M · **Tier:** fixture+dev+full · **Core/Stretch:** core · **Depends on:** EP-110 (Signature #1: first-24h → in-hospital mortality), EP-35 (Provenance run ledger) · **Blocks:** EP-126 (Capstone #5)

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-101 (Re-plan P6) before execution.

## Context

Category 31 (simulation, ablation and benchmarking experiments), building on the benchmark ledger
(EP-19/28) and the run ledger (EP-35, D-24). A declarative grid runner turns "one model, one run" into
"a matrix of runs with one summary table": feature-group ablations and window ablations of Signature
#1, seed repeats, and a known-truth simulation lane that checks which model classes recover a planted
nonlinearity/interaction. Every cell is a normal run record, so provenance is free.

## Scope sketch (refine at re-plan)

1. **`src/mimicwarehouse/ml/bench.py`** — `BenchSpec` pydantic model / YAML (`bench/*.yaml`): dataset(s),
   feature-group ablation list, observation windows (6/12/24 h), model specs (EP-107/108 ids), split
   plan(s), seeds; `run_bench(spec, tier)` expands the grid, runs each cell through the standard
   `fit_evaluate` path, and writes `runs/bench/<bench_id>/summary.parquet` (metrics ± CI, wall time,
   peak RSS, cell parameters) plus one benchmark-ledger line per cell; resumable per cell.
2. **Simulation lane** — `simulate(dgp, n, seed)` synthetic generators (logistic with planted
   nonlinearity / interaction / era shift; ids ≥ 90 000 000 like fixtures) and a known-truth report:
   which of LR / spline-LR / LightGBM recovers the planted effect, plus a sample-size curve.
3. **Representative benchmark** — Signature #1 ablations: drop labs / vitals / demographics /
   comorbidity; 6 vs 12 vs 24 h windows; era-restricted training; 5 seeds; dev in-session, full as a
   logged background job; figures (ablation bars with CIs) via `viz/`; report labelled *predictive*
   (benchmark), retrospective statement.
4. **Tests** (`tests/ep/test_ep124.py`, `@pytest.mark.ep_124`): grid expansion count and cell ids are
   deterministic; a resumed bench skips completed cells; the simulation recovers the planted effect
   direction; the summary passes `disclose.check`.

## Out of scope

- Optuna-scale hyperparameter search → parked (final-roadmap 20–24); external validation → parked.
- Continuous benchmark dashboards → parked (28–31); page latency benchmarks → EP-56.

## Verification / acceptance (sketch)

- `uv run poe test -m ep_124` green on fixture (+dev); `uv run --group dev mwh verify EP-124` green.
- Full-tier bench id and run ids (background job, `%MWH_DATA_ROOT%\runs\jobs\ep124.log`) recorded;
  summary table and ablation figure pass `mwh disclose check`.
