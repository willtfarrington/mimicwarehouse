# EP-123 — Bounded sequence model (GRU/GRU-D on 48 h) (stretch)

**Size:** M · **Tier:** fixture+dev · **Core/Stretch:** stretch · **Depends on:** EP-121 (GPU enablement (gpu group; doctor --gpu; XGBoost-CUDA vs LightGBM-CPU)), EP-102 (Model-ready dataset A: feature spec, windows, normalization, indicators) · **Blocks:** —

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-101 (Re-plan P6) before execution.

## Context

The stretch half of D-7: a small sequence model over the first 48 h of hourly-binned vitals and labs
(EP-56 hourly mart via the EP-102 window API) — GRU baseline and GRU-D (learned decay for missingness)
— against LightGBM on the same cohort, bounded to ≤ 6 GB VRAM and ≤ 30 min training on the dev tier
(planning implication for the GPU EP). Fixture+dev only; if the P7 re-plan is short of time this brief
is dropped first (stretch cutline). Category 26. Windows: `spawn` → `DataLoader(num_workers=0)`,
`if __name__ == "__main__"` guards, no module-level CUDA init; deterministic seeds via EP-36.

## Scope sketch (refine at re-plan)

1. **Sequence tensors** (`src/mimicwarehouse/ml/datasets.py` extension, `build_sequence(spec)`): cohort =
   first ICU stay adults with ICU LOS ≥ 48 h; hourly bins 0–47 for a fixed vitals/labs list from the
   hourly mart; per-variable value, mask and time-since-last-measured channels (GRU-D inputs);
   normalisation fitted on the training era; written as chunked `.npz`/Parquet under
   `marts/ml/<dataset_id>@<version>/seq/`; label = in-hospital death after hour 48.
2. **Models** (`src/mimicwarehouse/ml/gpu.py` or `ml/seq.py`): torch GRU and GRU-D modules, class-weighted
   BCE, early stopping on a grouped validation split (EP-104), mixed precision optional; training
   budget guard (stops at 30 min or the VRAM budget); CPU path with tiny epochs for tests.
3. **Comparison** — GRU/GRU-D vs LightGBM on 48-h aggregate features (EP-108) with EP-105 assessment on
   the temporal holdout; wall time, VRAM peak, epochs → benchmark ledger; registry + card (EP-106);
   short report artefact labelled *predictive*.
4. **Tests** (`tests/ep/test_ep123.py`, `@pytest.mark.ep_123`): tensor shapes and mask semantics on
   fixture; a planted temporal signal is learned above chance on CPU in < 2 min; the budget guard
   *stops* a crafted over-long run; no subject crosses train/validation.

## Out of scope

- Full-tier training (dev only here); TCN/transformer/RETAIN, event-sequence FMs → parked
  (final-roadmap 26); captum → parked (28–31).

## Verification / acceptance (sketch)

- `uv run --group gpu poe test -m ep_123` green on fixture (+dev), CPU path green without `gpu`;
  `uv run --group gpu mwh verify EP-123` green.
- Dev-tier training run id, wall time and VRAM peak recorded in the completion note; comparison table
  passes `mwh disclose check`.
