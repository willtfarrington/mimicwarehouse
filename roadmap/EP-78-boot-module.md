# EP-78 — Cluster bootstrap `boot` module

**Size:** M · **Tier:** fixture · **Core/Stretch:** core · **Depends on:** EP-36 (Seed/determinism policy + resource logger) · **Blocks:** EP-89 (Capstone #3), EP-91 (KM / Cox / Schoenfeld), EP-105 (Assessment module)

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-74 (Re-plan P4) before execution.

## Context

Capability category 14 support (and 28 later): the project-wide resampling primitive. MIMIC has
several stays per subject, so the standing decision is cluster resampling by `subject_id` for
every bootstrap CI (EP-77 comparisons, EP-79 GLMs, EP-91 survival curves, EP-105 model
assessment). Implements the EP-36 seed policy (numpy `Generator` derived from
`sha256(protocol_id, stage)`), uses joblib for parallel CV-style work (default from DECISIONS),
Polars primary (D-17), and Windows `spawn` rules (`if __name__ == "__main__"` guards, no
module-level connections). Pure numerics → tier `fixture`; first real-data use is EP-79/EP-91.

## Scope sketch (refine at re-plan)

1. **`src/mimicwarehouse/stats/boot.py`** — `cluster_bootstrap(df, statistic, cluster="subject_id",
   B=1000, ci="percentile"|"basic"|"bca", alpha=0.05, seed=None, n_jobs=1, stratify=None)` →
   `BootResult` (estimate, ci_low, ci_high, se, B, method, seed, n_clusters, distribution
   quantiles). Resampling = draw cluster ids with replacement, expand by join (Polars), preserving
   whole clusters; `stratify` resamples within strata; `statistic` may return a float, a dict or a
   Series (multi-output CIs).
2. **Determinism**: all B index draws generated on the parent from the seeded `Generator`, then
   chunked to joblib (`loky`) workers → identical results for any `n_jobs`; default `n_jobs =
   min(4, cores)`; memory note for large frames (64 GB machine; chunk, do not replicate).
3. **BCa**: delete-one-cluster jackknife; auto-fallback to percentile with a warning above a
   cluster-count threshold; warn if B < 999 for BCa.
4. **Adapters**: `boot_ci(estimator)` for EP-77 estimators; a refit-callable hook so `stats/glm.py`
   (EP-79) and later `ml/assess` (EP-105) can bootstrap fitted models; results carry `B`, `seed`,
   `n_clusters` into the run record (EP-35).
5. **Tests** `tests/ep/test_ep78.py` (`@pytest.mark.ep_78`): coverage simulation on synthetic
   clustered data with known truth (small B, few hundred replications, tolerance band); same seed
   with `n_jobs=1` vs `n_jobs=4` → identical; refuses a missing cluster column and B < 2;
   dict-valued statistics; hypothesis over shapes.

## Out of scope

- Permutation tests → EP-77; wild / Bayesian bootstrap → parked; bootstrapping ML pipelines
  → EP-105; survival-curve bands → EP-91.

## Verification / acceptance (sketch)

- `uv run poe test -m ep_78` green on fixture; `uv run --group dev mwh verify EP-78` green.
- Determinism and coverage tests present and green; docstring example runs under
  `uv run --group dev python -c …` with a `__main__` guard note.
- No full-tier run (module only); its first recorded real-data timing lands in EP-79.

## Parked → final-roadmap.md

- Wild cluster bootstrap, m-out-of-n and Bayesian bootstrap variants.
