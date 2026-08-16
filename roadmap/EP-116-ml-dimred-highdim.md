# EP-116 — Dimensionality reduction & high-dimensional analysis

**Size:** M · **Tier:** fixture+dev+full · **Core/Stretch:** core · **Depends on:** EP-103 (Model-ready dataset B: patient-safe partitions + feature dictionary), EP-77 (Inference & group comparison) · **Blocks:** EP-126 (Capstone #5)

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-101 (Re-plan P6) before execution.

## Context

Category 24 (dimensionality reduction and high-dimensional analysis). Representative workflow (D-5,
diagnosis-code theme): a sparse high-dimensional matrix of diagnosis categories (CCSR via the EP-40
dual ICD-9/10 code sets, so the ~2015 ICD switch does not manufacture era artefacts) per hospital
admission → (a) FDR-controlled univariate screen against in-hospital mortality using the EP-77
multiplicity tools, (b) stability-selected elastic-net logistic regression, and (c) PCA of the dense
first-24 h lab/vital block from EP-102/103. Claim type: exploratory (the FDR screen's hits are
labelled "associational, billing-code based" in their caption).

## Scope sketch (refine at re-plan)

1. **`src/mimicwarehouse/ml/dimred.py`** — `pca(X, n)` / `truncated_svd(X_sparse, n)` / `sparse_pca`
   with scree table, loadings table, explained variance and an Altair biplot spec (aggregate points =
   loadings, not rows); the sparse code matrix is built with DuckDB pivots on `meta.codeset_members`
   and never leaves the data root.
2. **Regularised selection** — L1 / elastic-net logistic (sklearn, grouped CV from EP-104 for the
   penalty) with *stability selection*: selection frequency across EP-78 subject bootstraps, threshold
   0.6, a selected-set table with coefficients and CIs.
3. **FDR screen** — one test per code category (EP-77 contingency method), Benjamini–Hochberg
   q-values, a volcano-style table/figure (effect vs −log10 q; small-cell categories suppressed), era
   interaction check for the top hits.
4. **Representative report** — dev in-session, full as a logged background job; figures via `viz/`;
   claim label *exploratory*, retrospective statement, and a "codes reflect billing practice" caveat.
5. **Tests** (`tests/ep/test_ep116.py`, `@pytest.mark.ep_116`): PCA on a planted low-rank fixture
   recovers rank; stability selection recovers planted signal codes and rejects null codes at the stated
   FDR; BH q-values are monotone; small-cell suppression applied to the volcano table.

## Out of scope

- UMAP/HDBSCAN → parked; clustering → EP-114; feature importance of fitted GBMs → EP-120.
- Text embeddings → EP-151.

## Verification / acceptance (sketch)

- `uv run poe test -m ep_116` green on fixture (+dev); `uv run --group dev mwh verify EP-116` green.
- Full-tier run id (background job, `%MWH_DATA_ROOT%\runs\jobs\ep116.log`) recorded; report artefact
  passes `mwh disclose check`.
