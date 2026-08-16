# EP-121 — GPU enablement (gpu group; doctor --gpu; XGBoost-CUDA vs LightGBM-CPU)

**Size:** M · **Tier:** fixture+dev · **Core/Stretch:** core · **Depends on:** EP-1 (Toolchain bootstrap (uv + CPython 3.13 + pyproject)), EP-108 (Trees / ensembles A (DT, RF, bagging, LightGBM)) · **Blocks:** EP-122 (Tabular foundation model vs GBM), EP-123 (Bounded sequence model (GRU/GRU-D on 48 h) (stretch)), EP-126 (Capstone #5)

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-101 (Re-plan P6) before execution.

## Context

Opens category 26 (resource-aware neural and deep-learning experiments) and implements D-16 (CPU-first,
GPU opt-in): the `gpu` dependency group, a doctor check, and a measured comparator. Machine facts:
RTX PRO 2000 Blackwell laptop, 8 GB VRAM, sm_120 → torch must come from the cu130 index with
`explicit = true` (PyPI torch is CPU-only on Windows; cu126 lacks sm_120); no Triton/torch.compile on
Windows; JAX has no Windows CUDA; XGBoost Windows wheels are CUDA-enabled while LightGBM CUDA is
Linux-only. Everything must still import and test on CPU when the GPU is absent (Windows spawn: no
module-level CUDA initialisation).

## Scope sketch (refine at re-plan)

1. **`pyproject.toml`** — `gpu` group: torch (`[[tool.uv.index]] name = "pytorch-cu130",
   url = "https://download.pytorch.org/whl/cu130", explicit = true` + `[tool.uv.sources]` mapping),
   `xgboost`, `nvidia-ml-py`; `uv lock` committed; a dated DESIGN.md note records the group and index.
2. **`mwh doctor --gpu`** (`src/mimicwarehouse/cli.py` + `src/mimicwarehouse/ml/gpu.py`): driver and
   CUDA runtime versions, `torch.cuda.get_device_capability() == (12, 0)` assertion, VRAM total/free,
   a small matmul with no "no kernel image" warning, XGBoost `device="cuda"` smoke fit; results recorded
   in the run manifest by EP-35's resource logger; `gpu_available()`, `vram_budget_bytes()` (working set
   ≤ 6 GB) and a `vram_peak` context (nvidia-ml-py) added to the EP-36 resource log.
3. **Comparator** — XGBoost `device="cuda"` vs LightGBM CPU (EP-108 spec) on the EP-110 dataset, dev
   tier, identical grouped CV: AUROC, wall time, peak RSS/VRAM → benchmark ledger and a short
   comparison table; the recorded conclusion (which is the workhorse) goes into a DECISIONS addendum
   under D-16.
4. **Tests** (`tests/ep/test_ep121.py`, `@pytest.mark.ep_121`): CPU-only path passes with the GPU
   masked (`CUDA_VISIBLE_DEVICES=""`); the doctor check *refuses* to report GPU-ready when capability
   ≠ (12, 0); XGBoost falls back to CPU with a logged warning when CUDA is unavailable.

## Out of scope

- Foundation model → EP-122; sequence model → EP-123; CatBoost GPU / LightGBM OpenCL → parked.
- Full-tier GPU training runs (this brief is fixture+dev; the benchmark harness EP-124 scales up).

## Verification / acceptance (sketch)

- `uv run --group gpu poe test -m ep_121` green on fixture (+dev), and `uv run poe test -m ep_121` green
  without the `gpu` group (CPU fallback); `uv run --group gpu mwh verify EP-121` green.
- `uv run --group gpu mwh doctor --gpu` output (capability, VRAM, versions) and the comparator ledger
  entry recorded in the completion note.
