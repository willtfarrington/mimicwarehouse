# Determinism: seeds, stochastic stages and the resource log (EP-36)

The one seed rule for every stochastic stage from P3 on, and what a run records about the
machine it ran on — the prose twin of the "seeds" and "resource sampler" sections of
`src/mimicwarehouse/run.py` (DESIGN §11; GOVERNANCE §12 lists seeds, wall time, peak RSS
and disk delta among what every run records). Later briefs cite these rules instead of
restating them; the run manifest ([provenance.md](provenance.md)) proves which seed each
stage used. Nothing on this page is derived from data. All MIMIC-IV analyses in this
repository are retrospective.

## 1. The seed rule

`derive_seed(protocol_id, stage, salt=0)` is the first four bytes of
`sha256(f"{protocol_id}|{stage}|{salt}")` read big-endian: a 32-bit integer
(0 … 4,294,967,295) that fits numpy's legacy seed range and every `random_state=`
argument. It is a pure function of three names — no global state, no process identity, no
clock — so it is the same integer in every interpreter, in every `spawn` worker and on
every machine. `|` is the field separator and may not appear in a name. The brief's
acceptance pair, `derive_seed("demo-protocol", "bootstrap")`, is 1,208,429,812
(`test_ep36` pins it).

| Call | Returns | Use it for |
|---|---|---|
| `rng(protocol_id, stage, salt=0)` | `numpy.random.default_rng(derive_seed(...))` | the stage Generator every stochastic function takes as an argument |
| `spawn_rngs(protocol_id, stage, n, salt=0)` | `n` child Generators from `SeedSequence(derive_seed(...)).spawn(n)` | CV folds, bootstrap replicate sets, joblib / `multiprocessing` workers — independent, reproducible, picklable |
| `random_state(generator)` | `int(generator.integers(2**31))` | the integer sklearn / LightGBM / XGBoost / statsmodels take (§2 rule 3) |
| `seed_everything(seed)` | `{random, numpy, torch}` → which globals were seeded | the last resort for opaque library paths: `random`, numpy's legacy global and — only if torch is *already* imported — `torch.manual_seed` (+ every CUDA device); it never imports torch (D-16) |
| `r.seed(stage, salt=0)` inside `run.start(...)` | the stage Generator, **recorded** | the form analysis code uses: derives from the run's `protocol_id`, writes `{stage: seed}` into `manifest.seeds` (`"<stage>@<salt>"` for a non-zero salt) |

**Frozen vs unfrozen work.** Under a frozen protocol (EP-51) the seed depends only on the
protocol id and the stage name, so two runs of the same protocol draw the same numbers.
Before a protocol exists, `r.seed` derives from the `run_id` instead: fresh per run, still
recorded in the manifest, and reproducible afterwards by handing the recorded integer to
`numpy.random.default_rng` directly. `salt` distinguishes deliberate repeats of one stage
(a second bootstrap replicate set, a sensitivity re-run) and is part of the recorded key.

## 2. Rules for library and analysis code

1. **Take a `Generator` argument; never seed globally.** A stochastic function is
   `def bootstrap(frame, *, rng: numpy.random.Generator, n_boot: int)` — the caller passes
   `r.seed("bootstrap")` (or `rng(protocol_id, "bootstrap")` outside a run). No module
   calls `numpy.random.seed`, `random.seed` or `np.random.<draw>` on the legacy global;
   `seed_everything` exists for third-party code that ignores the Generator you pass, and a
   call to it is itself recorded through `r.seed`'s manifest entry for the stage that needed
   it.
2. **Every stochastic stage names its stage string.** The canonical names — later briefs
   add to this table rather than inventing spellings:

   | Stage | What draws from it |
   |---|---|
   | `bootstrap` | bootstrap confidence intervals, replicate weights |
   | `cv_split` | cross-validation / temporal-split fold assignment (EP-102/103) |
   | `model_fit` | any estimator with internal randomness (tree ensembles, SGD, MCMC init) |
   | `imputation` | multiple imputation draws, noise injection |
   | `subsampling` | row subsampling, negative sampling, SQL `SAMPLE` (rule 4) |
   | `permutation` | permutation tests, feature-importance shuffles |
   | `init` | cluster / embedding initialisation (EP-78 `boot` consumes `rng` / `spawn_rngs` here) |

3. **Third-party estimators receive an integer from the stage Generator:**
   `random_state=random_state(rng)` for scikit-learn, LightGBM (`seed=`), XGBoost
   (`random_state=`) and statsmodels; never a literal `42`, never `None`.
4. **SQL sampling is `SAMPLE … REPEATABLE (seed)`.** In DuckDB:
   `SELECT … FROM t USING SAMPLE 5 PERCENT (bernoulli) REPEATABLE (<seed>)` with
   `seed = derive_seed(protocol_id, "subsampling")`. Prefer `bernoulli` when exact
   reproducibility across thread counts matters; reservoir sampling's row *set* is fixed by
   the seed but its order depends on parallelism, so sort before comparing.
5. **Multiprocessing stays under `if __name__ == "__main__":`** (Windows `spawn`, CLAUDE.md
   §3) **with seeds passed as arguments** — a child Generator from `spawn_rngs` or its
   integer — never read from module state, which a spawned worker does not inherit.
6. **The claim and its proof.** The same `protocol_id` + `stage` + git sha + snapshot ids
   ⇒ the same numbers, and the manifest carries all four (`protocol_id` / `protocol_hash`,
   `seeds`, `git_sha`, `snapshot_ids`) plus the environment hash (`uv_lock_sha256`) and
   the doctor block. What is **not** promised: bit-identical floating point across BLAS
   builds, thread counts or library versions — the lock hash and the host facts let a
   reader tell those apart from a real change.

## 3. The resource log (`resources`)

`ResourceLog` is a daemon thread that samples this process every 0.5 s (plus once at
start and once at stop; `r.resource_log.sample()` marks a moment by hand). `run.start`
runs one per run and stores its `ResourceUsage` under `manifest.resources`; the top-level
`wall_s` / `peak_rss_mb` / `disk_delta_mb` mirror it for the ledger views.

| Field | Meaning |
|---|---|
| `wall_s` | wall time of the interval (`perf_counter`, µs precision) |
| `cpu_time_s` | user + system CPU time consumed by this process |
| `peak_rss_mb`, `peak_source` | the interval's peak working set. `peak_wset`: on Windows the process set a new **lifetime** working-set high-water mark during the interval, so that mark *is* the interval's peak (exact). `sampled`: the maximum of the 0.5 s samples — elsewhere, or when an earlier interval in the same process (a previous run, a previous test) peaked higher; a shorter spike than the interval can be missed, which is what `sample()` is for |
| `rss_start_mb`, `rss_end_mb` | working set at start / stop |
| `samples`, `interval_s` | how many samples, how often |
| `disk_delta_mb` | free space consumed on the data-root drive over the interval (negative = freed) |
| `gpu_mem_peak_mb`, `gpu_device` | this process's peak GPU memory (per-pid through `pynvml`'s running-process list, summed over devices) and the first device's name — **only** when `pynvml` imports and a device answers; otherwise both are `null`, with no warning (D-16: the `gpu` group is opt-in until EP-121) |

Standalone: `result, usage = ResourceLog.measure(fn, *args, **kwargs)` (or
`with ResourceLog() as log:` … `log.usage`) measures one callable, and
`run.bench(kind, name, usage=usage, tier=...)` / `r.bench(kind, name, usage=usage)` turn
that into a benchmark-ledger line (`wall_s`, `peak_rss_mb`, `disk_delta_mb`; an explicit
field wins over the usage's). The EP-19 runner keeps its own per-step sampler; EP-124's
benchmark harness is the first consumer of `measure`.

## 4. What later briefs add

EP-51 supplies the `protocol_id` that makes seeds protocol-stable; EP-78's `boot` module
consumes `rng` / `spawn_rngs` for cluster bootstrap; EP-102/103 name their split stages
here; EP-121 installs the `gpu` group, after which the GPU fields populate on this host;
EP-124 builds the benchmark harness on `ResourceLog.measure` + `bench(usage=)`.
