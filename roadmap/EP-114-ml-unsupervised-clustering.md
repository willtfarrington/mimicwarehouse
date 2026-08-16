# EP-114 — Unsupervised A: clustering / mixtures / stability

**Size:** M · **Tier:** fixture+dev+full · **Core/Stretch:** core · **Depends on:** EP-103 (Model-ready dataset B: patient-safe partitions + feature dictionary) · **Blocks:** EP-115 (Unsupervised B: anomaly detection / association rules / similarity search), EP-118 (Bayesian B: EM / mixtures / one graphical model / likelihood + bootstrap), EP-126 (Capstone #5)

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-101 (Re-plan P6) before execution.

## Context

Category 23 (unsupervised learning and pattern discovery), first half. Representative workflow (D-5,
sepsis theme): first ICU stay adults meeting sepsis-3 (EP-42 phenotype), first-24 h physiology from the
EP-102/103 dataset (vitals, labs, SOFA components) → 3–5 data-driven phenotypes, their stability under
subject-level resampling, and a descriptive association with in-hospital mortality — explicitly
exploratory, no clinical claim. Standard sklearn only; UMAP/HDBSCAN remain parked (numba pins).
Cluster sizes and profile tables are aggregates and pass through `mimicwarehouse.disclose`.

## Scope sketch (refine at re-plan)

1. **`src/mimicwarehouse/ml/unsupervised.py`** — `cluster(dataset_id, method, k, seed)` for k-means,
   agglomerative (Ward), and `GaussianMixture` on EP-102-normalised features (fitted on the whole
   unsupervised frame; missing values imputed with indicators kept); model selection tables:
   silhouette, Calinski–Harabasz, BIC (GMM) over k = 2…8.
2. **Stability** — bootstrap resamples of *subjects* (EP-78) → re-cluster → adjusted Rand index against
   the reference partition; consensus/co-assignment summary; a stability curve over k.
3. **Profiles** — per-cluster feature means/medians (standardised and raw), cluster size (suppressed if
   < 11), outcome rate per cluster with Wilson CI (EP-68), era mix per cluster; Altair profile heatmap
   from `viz/`.
4. **Representative report** — sepsis phenotypes on dev in-session and full as a logged background
   job; claim label *exploratory* with the retrospective statement.
5. **Tests** (`tests/ep/test_ep114.py`, `@pytest.mark.ep_114`): planted 3-cluster fixture recovered
   with ARI > 0.9; stability resampling is by subject (no subject split across resample halves);
   profile export suppresses a crafted small cluster.

## Out of scope

- Anomaly detection, association rules, similarity search → EP-115.
- Hand-rolled EM and latent-class models → EP-118; trajectory-based clustering → EP-82.

## Verification / acceptance (sketch)

- `uv run poe test -m ep_114` green on fixture (+dev); `uv run --group dev mwh verify EP-114` green.
- Full-tier run id (background job, `%MWH_DATA_ROOT%\runs\jobs\ep114.log`) recorded; profile heatmap
  and tables pass `mwh disclose check`.
