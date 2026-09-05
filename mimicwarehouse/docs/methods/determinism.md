# Determinism: seeds, stages and the resource log (EP-36)

The one written rule for "where does randomness come from, and how do I get the same
numbers again?" — the prose twin of the `seeds` and `resources` sections of
`src/mimicwarehouse/run.py` (DESIGN §11, §15; GOVERNANCE §12). Every stochastic step from
P3 on — bootstrap intervals (EP-78), CV splits (EP-104), model fits (EP-107+), imputation
(EP-87), subsampling — follows it; later briefs cite this page instead of restating it.
Nothing here is derived from data. All MIMIC-IV analyses in this repository are
retrospective.

## 1. The rule in one sentence

**The same protocol, stage, git sha, `uv.lock` hash and snapshot ids give the same
numbers, and the run manifest proves it.** Randomness enters an analysis only through a
`numpy.random.Generator` derived from the run's seed scope and a named stage; the manifest
records the seed of every stage it derived (`seeds`), the code (`git_sha`, `git_dirty`),
the environment (`uv_lock_sha256`, the versions) and the data (`snapshot_ids`) —
everything needed to reproduce the numbers, nothing that is data.

## 2. Seed derivation

    derive_seed(protocol_id, stage, salt=0)
        = int.from_bytes(sha256(f"{protocol_id}|{stage}|{salt}".encode()).digest()[:4], "big")

- A 32-bit unsigned integer (0 … 4,294,967,295), so one value fits numpy's legacy global
  seed and every `random_state` argument as it is; DuckDB's `REPEATABLE (seed)` parses an
  int32 literal, so `sql_sample_clause` folds the seed to `seed % 2**31`
  (`run.DUCKDB_SEED_MAX`) — deterministically, so the clause still reproduces from the
  recorded seed.
- Stable across processes and machines: sha256 of a plain string, never a hash of a Python
  object, never `PYTHONHASHSEED`. `test_ep36` checks that two fresh interpreters agree.
- **Scope.** `protocol_id` is the seed scope: the frozen protocol's id (EP-51, D-25) when
  the run is under a protocol — so a frozen protocol reproduces its numbers across runs —
  or the `run_id` for unfrozen work, which then reproduces from its own manifest
  (`derive_seed("<run_id>", stage)`) but not across runs, on purpose: unfrozen runs are
  exploratory, and the record of what they drew lives in their manifest.
- **Stage** is one token naming the stochastic step (§4). **Salt** names a deliberate
  variant of the same stage (a repeat with fresh randomness for a sensitivity re-draw);
  anything else is a different stage.
- `|` never appears in a protocol id; stage names match `run.STAGE_RE` (letters, digits,
  `_`, `.`, `-`, one token).

The API, all in `mimicwarehouse.run`:

| Call | Returns | Use |
|---|---|---|
| `derive_seed(protocol_id, stage, salt=0)` | the 32-bit seed | a library that wants an integer |
| `rng(protocol_id, stage, salt=0)` | `numpy.random.default_rng(seed)` | the Generator a stage receives |
| `spawn_rngs(protocol_id, stage, n, salt=0)` | `n` child Generators via `SeedSequence(seed).spawn(n)` | joblib / CV / bootstrap workers — spawned on the parent, passed as arguments |
| `seed_everything(seed)` | `{random, numpy, torch: seeded?}` | entry points, notebooks, tests only: seeds the globals; touches `torch` only when it is already imported, never imports it |
| `sql_sample_clause(seed, rows= \| percent=, method="reservoir")` | `USING SAMPLE reservoir(n) REPEATABLE (seed)` | SQL subsampling (§3, rule 4) |
| `Run.seed(stage)` | the stage's Generator, recorded in `manifest.seeds` | inside `run.start` — the normal path |
| `Run.spawn_rngs(stage, n)` | worker Generators, the stage recorded once | inside `run.start`, parallel work |
| `Run.seed_scope` | the `protocol_id`, else the `run_id` | what the run's seeds derive from |

`Run.seed(stage)` rewrites `manifest.json` at once, so a run killed mid-fit still shows
what it seeded. Calling it twice for the same stage yields the same stream and records
nothing new: two different stochastic steps get two stage names (`bootstrap.auc`,
`bootstrap.brier`), never one name reused. The children of `spawn_rngs` differ from
`rng`'s stream for the same stage (SeedSequence spawning), so "one worker" and "no
workers" are not interchangeable — a module that may run either way always spawns.

## 3. Rules for library code (what later briefs cite)

1. **Library code takes a `Generator` argument and never seeds globally.**
   `def cluster_bootstrap(df, statistic, *, rng: numpy.random.Generator, ...)`; no
   `np.random.seed`, no `random.seed`, no module-level generator, no default seed baked
   into a signature. `seed_everything` exists for the boundaries — a CLI entry point, a
   notebook cell, a test — and is never called inside a module.
2. **Every stochastic stage names its stage** (§4) and takes its Generator from
   `Run.seed(stage)` (or `rng(...)` outside a run): bootstrap, CV split, model fit,
   imputation, subsampling, permutation, initialisation — each is a stage of its own.
3. **scikit-learn, LightGBM, XGBoost and statsmodels** receive
   `random_state=int(rng.integers(2**31))` drawn from the stage's Generator — a fresh
   draw per estimator, never the stage seed reused across estimators, never a hard-coded
   `random_state=0`. Libraries that accept a Generator directly (numpy, scipy's
   `random_state=rng`) get the Generator itself; Polars' `sample(seed=)` gets an integer
   drawn the same way.
4. **DuckDB sampling** uses `USING SAMPLE <method>(<size>) REPEATABLE (<seed>)`, rendered
   by `run.sql_sample_clause(seed, rows= | percent=, method=)` from the stage's seed
   (folded to DuckDB's int32 range, §2) — never a bare `USING SAMPLE`, which draws
   differently every time, and never a hand-typed literal. `reservoir` (the default) returns
   exactly the requested size and takes a row count or a percentage; `bernoulli` and
   `system` take a percentage only; `system` samples whole 2,048-row vectors and returns
   nothing for a small percentage of a small table. `ORDER BY … LIMIT` is not sampling and
   needs no seed.
5. **Parallel work** (joblib, `multiprocessing`, `concurrent.futures`): the parent derives
   everything — `Run.spawn_rngs(stage, n)` — and hands each worker its Generator (or its
   integer seed) as an argument. Windows starts workers by `spawn`, from a fresh
   interpreter, so module state, global seeds and open connections are never inherited;
   every script that starts workers runs them under `if __name__ == "__main__":`. Results
   are identical for any worker count (EP-78's acceptance: `n_jobs=1` equals `n_jobs=4`).
6. **torch** (EP-121+): `seed_everything` seeds it only when it is already imported; a GPU
   brief that needs bitwise-identical kernels sets `torch.use_deterministic_algorithms(True)`
   and the cuDNN flags itself and records that it did.
7. **What "the same numbers" means.** Given the same seeds, code, environment and snapshot
   ids, integer results (resample indices, fold assignments, counts) are identical, and
   floating-point results are identical to the precision a report states; thread counts
   and BLAS builds can change reduction order in the last digits (DuckDB threads are pinned
   per profile, DESIGN §6; `uv.lock` pins the libraries). A report never claims more
   digits than reproduce.

## 4. Stage names

| Stage | Where randomness enters | Typical consumer |
|---|---|---|
| `bootstrap` | resampling clusters / rows for a confidence interval | EP-77, EP-78, EP-91, EP-105 |
| `cv_split` | fold assignment, nested CV | EP-104 |
| `model_fit` | estimator initialisation (`random_state`) | EP-107 … EP-123 |
| `imputation` | multiple-imputation draws | EP-87 |
| `subsampling` | a random subset of rows or stays (`sql_sample_clause`) | EP-102, marts |
| `permutation` | permutation tests, permutation importance | EP-77, EP-120 |
| `init` | random starts (k-means, mixtures, MCMC chains) | EP-114, EP-117, EP-118 |
| `simulation` | synthetic data with known truth | EP-98, EP-124 |

Sub-stages take a dotted suffix (`bootstrap.auc`, `model_fit.lgbm`); a name outside this
table is fine when the brief documents it. The names are conventions for readers of
manifests, not an enum the code enforces.

## 5. What the manifest records

`runs/<run_id>/manifest.json` (`provenance.md` §2) carries:

- `seeds` — `{stage: seed}` for every stage the run derived (`{}` for a run with no
  stochastic step; `null` only in manifests written before EP-36); `protocol_id` says
  which scope they derive from (absent → the `run_id`).
- `resources` — the resource log (§6), with `wall_s`, `peak_rss_mb` and `disk_delta_mb`
  mirrored at the top level for the readers EP-35 already had.
- `git_sha` / `git_dirty`, `uv_lock_sha256`, `duckdb_version` / `python_version` /
  `package_version`, `snapshot_ids` — the rest of the reproducibility contract
  (GOVERNANCE §12): the git sha, the environment hash and the snapshot ids.

`run.reproduction_block(run_id)` renders the seeds (through `fmt_int`, so a seed can never
look like an identifier in a committed page — `docs/committed-text.md` rule 1) and the
resource summary into the Reproduction + Provenance block of a case study.

## 6. The resource log

`run.ResourceLog` runs inside every `run.start` — a daemon thread sampling every 0.5 s
(`run.SAMPLE_INTERVAL_S`), started after the environment block is captured and stopped
before the manifest is finalised — and standalone as `ResourceLog.measure(fn)` or
`Run.measure(kind, name, fn)`; the latter also appends a `runs/benchmarks.jsonl` line
through `run.bench` (`wall_s`, `peak_rss_mb`, `disk_delta_mb`). The `resources` block
(`run.ResourceUsage`; MB = 2**20 bytes, one decimal):

| Field | Meaning |
|---|---|
| `wall_s` | wall-clock seconds between start and stop |
| `cpu_time_s` | process CPU seconds (user + system, all threads) over the same span |
| `peak_rss_mb`, `peak_rss_method` | the **run-scoped** peak resident set: the maximum of the sampled RSS (`sampled`), promoted to Windows' `peak_wset` only when that process-lifetime mark **grew** during the run (`peak_wset` — then it is exact and catches a spike between two ticks) |
| `rss_start_mb`, `rss_end_mb` | RSS at start and at stop |
| `peak_wset_mb` | the process-lifetime peak working set at stop (Windows; `null` elsewhere) |
| `disk_delta_mb` | free bytes of the data-root drive at start minus at stop (positive = the run consumed space) |
| `gpu_mem_start_mb`, `gpu_mem_peak_mb`, `gpu_mem_method` | GPU memory sampled through `pynvml` (`nvidia-ml-py`) **only when it imports and a device answers**: this process's memory when the driver attributes it per process (`process`), else the device-level total across devices (`device`, what Windows WDDM offers); `null` without pynvml or a GPU, silently (D-16: the GPU is opt-in; EP-121 installs the `gpu` group) |
| `samples`, `sample_errors`, `interval_s` | how many ticks, how many probes failed (a failed probe leaves fields `null`, never fails the run), the tick |

Why not `peak_wset` alone: it is a lifetime high-water mark — memory freed before the run
leaves it high, so a run that allocates nothing would inherit an earlier peak. The rule
above keeps the number run-scoped, and exact whenever the kernel's mark moved
(`docs/gotchas.md` § 2). The EP-19 runner keeps its own per-step RSS sampler for now
(`dag/runner.py`); the P3 re-plan (EP-54) decides whether it adopts `ResourceLog`.

## 7. Reproducing a run

1. `uv run --group dev mwh runs show <run_id>` — read `command`, `git_sha`,
   `uv_lock_sha256`, `snapshot_ids`, `protocol_id` and `seeds`.
2. Check out `git_sha`, `uv sync --group dev` (the `uv.lock` hash must match), and confirm
   the catalog's snapshot ids match the manifest's (`mwh catalog info`).
3. Re-run the `command` — under the same protocol when the run was frozen
   (`mwh protocol run <hash>`, EP-51) — and compare the new manifest's `seeds` with the
   original: identical seeds and snapshot ids are the precondition for identical numbers;
   a seed that differs means the stage naming or the scope changed and the comparison is
   void.
4. For an unfrozen run, whose scope was its `run_id`, the seeds reproduce as
   `derive_seed("<original run_id>", stage)` — a re-run passes the original id as the
   scope (`rng("<original run_id>", stage)`) rather than seeding under a new run id.
