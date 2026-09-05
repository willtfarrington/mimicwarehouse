# EP-36 — Seed/determinism policy + resource logger

**Size:** S · **Tier:** fixture · **Core/Stretch:** core · **Depends on:** EP-35 (Provenance run ledger) · **Blocks:** EP-54 (Re-plan P3), EP-78 (Cluster bootstrap `boot` module)

> **Amended at EP-170 (2026-08-29).** `psutil` is a **core** dependency since EP-19 (D-15
> addendum) — item 3 imports it, no dependency change [FC-9]; `pynvml` stays optional as
> written. Shorthand per the README notation table; header facts unchanged.

> **EP-33 amendment (2026-09-01).** Header facts unchanged. Ledger writes use the one JSONL
> canon: `fsio.append_jsonl(path, payload)` is the only ledger writer (any resource-log sample
> line and the ledger line EP-35's `Run` appends) and `fsio.read_jsonl`/`iter_jsonl` the reader
> (one torn trailing line tolerated with a warning, `TornLedgerError` otherwise) — no
> module-local `os.open(..., O_APPEND)` writer (EP-33 LGR-2/LGR-4); manifest rewrites use
> `fsio.atomic_write_text` (the former `inventory._atomic_write_text`). `ResourceLog.measure`
> feeds `run.bench`, which emits `dag.benchmarks.BenchmarkLine` rows (EP-35 amendment — no
> `BenchmarkRecord`). The `usage:` audit convention (EP-33 B1d: `usage: `-prefixed
> `refusal_reason` for argument errors, exit 2, vs gate refusals, exit 3) applies to any
> `safe_query` a test here wraps; `mwh runs show` errors go to stderr via `console.fail`.
> Nothing else changes: `psutil` core (EP-170 note), `pynvml` optional.

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

> **Completion note (2026-09-05).** Fixture tier only; no data read. Shipped in
> `src/mimicwarehouse/run.py` (the "seeds" and "resource sampler" sections),
> `docs/methods/determinism.md` (new; linked from `provenance.md` §2/§8), `tests/ep/test_ep36.py`
> (14 tests), DESIGN §11 dated note + §15 row, D-24 addendum, `docs/gotchas.md` §2 (the
> `peak_wset` lifetime lore), README § State row.
>
> **Acceptance.** `derive_seed("demo-protocol", "bootstrap")` printed **1,208,429,812** in two
> separate `uv run --group dev python -c` invocations (also pinned by `test_ep36`).
> `uv run poe test -m ep_36`: 14 passed (3.4 s); `uv run --group dev mwh verify EP-36`: exit 0.
> `uv run poe check`: ruff / format / pyright clean, **885 passed** (918 collected, 33 tier probes
> deselected) in 248 s. The verify loop EP-30 … EP-36 all exit 0; `poe roadmap-check --strict`
> 0 errors / 0 warnings; `mwh guard` clean over every changed file. `mwh runs show <run_id>`
> for the test run prints `seeds` and `resources` (asserted in
> `test_runs_show_and_manifests_view_display_seeds_and_resources`, which also reads them back
> through the `manifests` view with `json_extract`). Smoke in a fresh interpreter on a scratch
> data root: an empty run recorded `peak_rss_mb` 49.7 (`peak_source: peak_wset`), the run
> holding a 200 MB `numpy.ones` recorded 252.0; `gpu_mem_peak_mb` / `gpu_device` are `null`
> on this host (`pynvml` and `torch` absent, as the brief expects until EP-121).
>
> **Earlier test touched (roadmap README rule, CMP-6):** `tests/ep/test_ep35.py` line 164 —
> its own assertion `resources is None, "EP-36 fills these"` now asserts `resources.wall_s >= 0`
> (`seeds` still `None` for an unseeded run). No other earlier test changed.
>
> **As-built choices (routine, recorded here).** (1) `RunManifest.resources` is a typed
> `ResourceUsage` (frozen, `extra="forbid"`), not a bare dict; the `manifests` view column stays
> `JSON`. (2) `peak_rss_mb` carries `peak_source`: `peak_wset` is a process-lifetime mark, so it is
> reported as the run's peak only when it rose during the run, else the sampled RSS maximum —
> otherwise a second run in one pytest session or the app would inherit the first run's peak.
> (3) `wall_s` keeps µs precision so the brief's `wall_s > 0` holds for a trivial run (the
> EP-35 ledger column is DOUBLE; nothing else changes). (4) `bench(..., usage=ResourceUsage)`
> is how `ResourceLog.measure` feeds the benchmark ledger; `wall_s` became optional when a
> usage is given, and an explicit field wins. (5) `Run.seed(stage, salt=)` records a non-zero
> salt as `"<stage>@<salt>"`; `derive_seed` refuses `|` in a name (the separator).
> (6) `Run.resource_log` exposes the sampler so a caller can `sample()` at a moment worth
> catching. (7) GPU memory is per-pid through NVML's running-process list (device-wide `used`
> would count the desktop); a stub `pynvml` in `test_ep36` proves sampling, shutdown and silent
> degradation without a driver. (8) The EP-19 runner keeps its own `_RssSampler`; merging it
> into `ResourceLog` is handed to EP-54 (re-plan) as a routine consolidation, not done here.
> (9) No dependency change: `psutil` core (EP-170 note), `pynvml` optional and absent.
>
> **Owner decisions at close (session-end prompt, all as recommended):** two-step commit
> (feat, then the README ☑ record commit) — yes; push — no, the owner pushes; keep
> `RunManifest.resources` typed (`ResourceUsage`) with `peak_source` — yes; the EP-19 runner's
> `_RssSampler` merge — deferred to EP-54. Feat commit `fc7aa1f`; not pushed.
