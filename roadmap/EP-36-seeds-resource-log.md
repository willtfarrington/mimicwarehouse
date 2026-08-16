# EP-36 — Seed/determinism policy + resource logger

**Size:** S · **Tier:** fixture · **Core/Stretch:** core · **Depends on:** EP-35 (Provenance run ledger) · **Blocks:** EP-54 (Re-plan P3), EP-78 (Cluster bootstrap `boot` module)

## Context

EP-35 gave every run a manifest with `seeds` and `resources` slots left optional. This brief
fills them: a single seed-derivation rule so that any stage of any protocol is reproducible
without global state, and a resource logger that records wall time, peak RSS, disk delta and
(when present) GPU memory into the same manifest (DESIGN §11; GOVERNANCE §12; D-18's "record a
full-tier run with timing"; D-16 GPU is opt-in so GPU sampling must degrade silently). Machine
facts: 64 GB RAM, one NVMe, 8 GB VRAM Blackwell GPU that is only reachable after EP-121 installs
the `gpu` group; Windows `spawn` multiprocessing means seeds must be passed explicitly to workers,
never inherited from module state. All work is fixture-tier: no data is read.

## In scope

1. **Seed derivation** (`src/mimicwarehouse/run.py`, section "seeds") —
   `derive_seed(protocol_id: str, stage: str, salt: int = 0) -> int` =
   `int.from_bytes(sha256(f"{protocol_id}|{stage}|{salt}".encode()).digest()[:4], "big")`
   (fits numpy's 32-bit seed range); `rng(protocol_id, stage, salt=0) -> numpy.random.Generator`
   (`default_rng(derive_seed(...))`); `spawn_rngs(protocol_id, stage, n)` via
   `SeedSequence(derive_seed(...)).spawn(n)` for joblib/CV workers; `seed_everything(seed)` sets
   `random`, numpy legacy global, and `torch` only if already imported (never imports it).
   `Run.seed(stage)` derives from the run's `protocol_id` (or `run_id` when unfrozen work),
   records `{stage: seed}` in `RunManifest.seeds`, and returns the Generator.
2. **Determinism policy** (`docs/methods/determinism.md`, new) — rules later briefs cite:
   library code takes a `Generator` argument, never seeds globally; every stochastic stage
   (bootstrap, CV split, model fit, imputation, subsampling) names its stage string; sklearn /
   LightGBM / XGBoost / statsmodels receive `random_state=int(rng.integers(2**31))` from the
   stage Generator; DuckDB `SAMPLE … REPEATABLE (seed)` for SQL sampling; multiprocessing under
   `if __name__ == "__main__":` with seeds passed as arguments; the same protocol + stage + git
   sha + snapshot ids ⇒ the same numbers, and the run manifest proves it.
3. **`ResourceLog`** (`src/mimicwarehouse/run.py`) — a daemon-thread sampler (0.5 s) using
   psutil: peak working set (`memory_info().peak_wset` on Windows, `rss` max elsewhere),
   `wall_s`, `cpu_time_s`, data-root drive free-bytes delta, and `gpu_mem_peak_mb` via
   `nvidia-ml-py` (`pynvml`) **only if** importable and a device is present (else `null`, no
   warning spam). Started/stopped by `Run`; snapshot also callable standalone
   (`ResourceLog.measure(callable)`) for benchmark ledger entries (`run.bench`).
4. **Tests** — `tests/ep/test_ep36.py` (`@pytest.mark.ep_36`, fixture): `derive_seed` is stable
   across processes and differs across stages/salts; `rng` reproduces a draw sequence; `spawn_rngs`
   yields distinct, reproducible streams; `seed_everything` does not import torch; a run manifest
   contains `seeds` and `resources` with `peak_rss_mb > 0` and `wall_s > 0`; a synthetic 200 MB
   numpy allocation inside a run raises `peak_rss_mb` measurably; GPU fields are `null` on a
   machine without pynvml.

## Out of scope

- Cluster bootstrap and CI machinery → EP-78 (consumes `rng`/`spawn_rngs`).
- GPU installation, `mwh doctor --gpu`, CUDA checks → EP-121.
- Benchmark harness / ablations → EP-124. Page-latency benchmarks → EP-56.

## Verification / acceptance

- `uv run poe test -m ep_36` green on fixture; `uv run --group dev mwh verify EP-36` green.
- `uv run --group dev mwh runs show <run_id>` for the test run displays `seeds` and `resources`.
- `docs/methods/determinism.md` exists and is linked from `docs/methods/provenance.md` (EP-35).
- `derive_seed("demo-protocol", "bootstrap")` printed twice in two separate `uv run --group dev python -c`
  invocations gives the same integer (record it in the completion note).
