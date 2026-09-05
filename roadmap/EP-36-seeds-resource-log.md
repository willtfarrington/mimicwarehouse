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

> **Completion note (2026-09-05).** Redo of the reverted first attempt (`fc7aa1f`, undone by
> `798141c`, owner decision of 2026-09-05): written from the brief; the reverted commit was
> consulted only for its list of touched files, no code was reused. Shipped in
> `src/mimicwarehouse/run.py` — **seeds:** `derive_seed(protocol_id, stage, salt=0)` (the
> brief's sha256 formula; `seed_key` is the hashed string), `rng`, `spawn_rngs`
> (`SeedSequence.spawn`), `seed_everything` (`random` + numpy legacy + an already-imported
> `torch`, never imported), `sql_sample_clause(seed, rows=|percent=, method=)` → `USING SAMPLE
> <method>(<size>) REPEATABLE (<seed>)`, `SeedError`, `SEED_MAX` / `DUCKDB_SEED_MAX` /
> `STAGE_RE`; `Run.seed(stage)` / `Run.spawn_rngs(stage, n)` / `Run.seed_scope` (the frozen
> `protocol_id`, else the `run_id`), recording `{stage: seed}` under `manifest.seeds` and
> rewriting the manifest at once; **resources:** `ResourceLog` (daemon thread, 0.5 s, psutil;
> `start` / `sample` / `stop`, context manager, `ResourceLog.measure(fn)`), `ResourceUsage`
> (+ `bench_fields()`), `Run.measure(kind, name, fn)` → `run.bench`, `Run.resource_log`;
> `run.start` opens every run with `seeds: {}`, stores the measurement under `resources` at
> exit and mirrors `wall_s` / `peak_rss_mb` / `disk_delta_mb` (EP-35's start/end RSS reading
> is gone); `reproduction_block` lists the seeds (through `fmt_int`) and the resource summary.
> Docs: `docs/methods/determinism.md` (new: the rule, the derivation, seven library rules, the
> stage-name table, the manifest fields, the resource log, a reproduction recipe),
> `provenance.md` §2/§6/§8 (linked), DESIGN §11 dated note + §15 row, the D-24 addendum, the
> README `run.py` row + quick start, `docs/gotchas.md` (§2 `peak_wset` lore, two §6 canon rows).
> Tests: `tests/ep/test_ep36.py`, 32 fixture-tier tests (stubbed psutil / pynvml pin every
> branch of the peak-RSS and GPU rules; no data is read).
> **Acceptance.** `derive_seed("demo-protocol", "bootstrap")` printed by two separate
> `uv run --group dev python -c` invocations = **1,208,429,812** both times (also pinned by
> `test_ep36`). `mwh runs show 20260905T225055Z-998d33` — a fixture-tier `bench` run on the
> configured data root that seeded `bootstrap` + `cv_split`, allocated a 200 MB numpy probe and
> called `Run.measure` — displays `seeds` and `resources`: peak RSS 270.9 MB (`peak_wset`,
> the mark grew), RSS 56.9 → 270.7 MB, wall 0.197 s, CPU 0.172 s, disk delta 0.1 MB, GPU
> fields `null` (no `pynvml` on this host), 2 samples; `mwh runs refresh` rebuilt the views and
> the `manifests` view exposes both slots as JSON. **Gates:** `poe check` **903 passed**,
> 33 deselected, 251 s (ruff check, ruff format --check, pyright clean; 871 → 903 = the 32 new
> tests); `poe test -m ep_36` 32 passed (3.9 s); `mwh verify` EP-30 33 · EP-31 7 · EP-32 14 ·
> EP-33 115 · EP-34 17 · EP-35 18 · EP-36 32, all exit 0; `poe roadmap-check --strict` 0 errors,
> 0 warnings (172 rows, 44 done before this brief's tick); `mwh guard` clean over the ten new
> or edited files. Windows power mode read *Best performance* on AC for the whole session
> (`mwh doctor` `power_scheme`). No data-root read; no dependency change (`psutil` core,
> `pynvml` optional as amended).
> **Judgment calls (owner review):** (1) **peak-RSS rule** — the brief's literal reading
> ("`peak_wset` on Windows") is a *process-lifetime* high-water mark: a probe allocated and
> freed 200 MB (30 → 230 → 30 MB RSS) and `peak_wset` stayed at 230 MB, so a run that
> allocates nothing would inherit an earlier peak inside a pytest session or the app; shipped
> rule: the sampled RSS maximum, promoted to `peak_wset` only when the mark **grew** during the
> run (`peak_rss_method` says which; both branches pinned with a stubbed psutil; D-24 addendum,
> gotchas §2). (2) `Run.seed` rewrites the manifest immediately, so a hard-killed run still
> shows what it seeded (EP-35 wrote the manifest at entry and exit only). (3) `Run.seed(stage)`
> takes no `salt` and is idempotent for a repeated stage — distinct stochastic steps get
> distinct stage names (`bootstrap.auc`); `salt` stays on the module-level functions as the
> brief lists them. (4) `sql_sample_clause` is beyond the brief's API list: it makes the policy's
> DuckDB rule executable and tested — and the test caught that DuckDB 1.5.5 parses
> `REPEATABLE (<seed>)` as an **int32 literal** (2**31 and above is a syntax error), so the
> helper folds a 32-bit seed to `seed % 2**31` (`DUCKDB_SEED_MAX`), documented in
> `determinism.md` §2. (5) Conveniences beyond the brief: `Run.spawn_rngs`, `Run.measure`,
> `Run.resource_log`, `ResourceUsage.bench_fields`, `seed_key`. (6) `RunManifest.resources` is
> typed `ResourceUsage | None` and `seeds` stays `dict | None` so the three pre-EP-36 manifests
> on the data root (`null` in both slots) still validate; an open run starts at `seeds: {}`
> (`{}` = no stochastic stage, `null` = pre-EP-36). (7) GPU sampling prefers per-process
> attribution (`nvmlDeviceGetComputeRunningProcesses`) and falls back to the device-level
> total (what Windows WDDM reports); NVML is opened once per log and shut down at stop; every
> probe is fail-quiet (`sample_errors`, no warning spam). (8) `wall_s` keeps six decimals (an
> empty run finishes in well under a millisecond and must not read 0.0). (9) The EP-19 runner's
> per-step `_RssSampler` is **untouched** (out of scope; proven by five ⏱ jobs) — EP-54
> decides whether the runner adopts `ResourceLog`, which would also give stage lines their
> `disk_delta_mb` (recorded in DESIGN §11, gotchas §6, the D-24 addendum).
> **Touched earlier tests:** `tests/ep/test_ep35.py` — one assertion (`seeds is None and
> resources is None`, "EP-36 fills these") became `seeds == {}` and `resources is not None`
> with a dated `# EP-36` comment: the shipped fact changed, the churn rule's sanctioned case;
> `mwh verify EP-35` exits 0. **Observation (owner FYI):** `mwh runs list` on the data root
> still shows three `concepts` build runs (demo / full / dev, git sha `2be09908…`) written by
> the reverted EP-37 session — ledger lines and run folders from reverted work; the ledger is
> append-only and run folders are deleted only by the owner (`provenance.md` §7), so they are
> left for the pre-EP-37-redo cleanup. Nothing parked.
> **Owner decisions (2026-09-05, session-end review):** commit as the standard two-step pair
> (this note included; no push — the owner pushes); leave the EP-19 runner's `_RssSampler`
> alone — EP-54 decides whether the runner adopts `ResourceLog` (judgment call 9); keep all
> six additions beyond the brief's API list (judgment calls 4–5); keep the hybrid peak-RSS
> rule over the brief's literal `peak_wset` reading (judgment call 1).
