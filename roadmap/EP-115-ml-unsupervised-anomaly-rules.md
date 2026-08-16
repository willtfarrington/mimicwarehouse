# EP-115 — Unsupervised B: anomaly detection / association rules / similarity search

**Size:** M · **Tier:** fixture+dev+full · **Core/Stretch:** core · **Depends on:** EP-114 (Unsupervised A: clustering / mixtures / stability) · **Blocks:** EP-126 (Capstone #5)

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-101 (Re-plan P6) before execution.

## Context

Second half of category 23. Three small, well-bounded methods over the EP-114 sepsis frame and the
comorbidity code sets: anomaly detection as a data-quality and outlier-stay screen, association rules
over binary comorbidity flags, and patient-similarity search that only ever returns *aggregate*
neighbourhood profiles (D-31/D-32: identifiers of neighbours are never listed outside the owner-gated
app path). Rule supports are counts, so `mimicwarehouse.disclose` governs the minimum support.

## Scope sketch (refine at re-plan)

1. **Anomaly detection** (`src/mimicwarehouse/ml/unsupervised.py`): `IsolationForest`, `LocalOutlierFactor`
   and robust Mahalanobis distance on the standardised first-24 h features; outputs = anomaly-score
   distributions, counts of flagged stays per era/care unit, and the feature-wise contribution summary
   for the flagged group vs the rest; a QC hook that writes flagged-rate per feature into the EP-44
   profile tables (implausible-value pockets, not patients).
2. **Association rules** — apriori/FP-growth (mlxtend, BSD-3) over Elixhauser/Charlson flag itemsets
   (EP-40 code sets, dual ICD-9/10) per hospital admission: support, confidence, lift; the minimum
   support is enforced as ≥ 11 admissions and the rules table is disclosure-checked; the same over
   first-day ICU medication classes as a second itemset.
3. **Similarity search** — `similar_stays(query_vector, k)` via `NearestNeighbors` in the normalised
   feature space (optionally the EP-116 PCA space); returns the neighbourhood's aggregate profile
   (feature means, outcome rate with CI, era mix), never ids; row-level neighbour listing is left to the
   owner-gated app path and is out of scope here.
4. **Representative report** — anomaly summary, top-20 comorbidity rules, and one similarity-search
   example built from a *synthetic* query vector (fixture-style values, not a real stay); dev
   in-session, full as a logged background job; claim label *exploratory*.
5. **Tests** (`tests/ep/test_ep115.py`, `@pytest.mark.ep_115`): planted outliers rank in the top scores;
   a rule with support 10 is dropped; `similar_stays` output contains no identifier columns (guard test).

## Out of scope

- Note similarity / embeddings → EP-151 (notes track); frequent-sequence mining → parked (final-roadmap 8–10).
- Owner row-level neighbour view → EP-125 (behind the EP-58 gate) if ever; not here.

## Verification / acceptance (sketch)

- `uv run poe test -m ep_115` green on fixture (+dev); `uv run --group dev mwh verify EP-115` green.
- Full-tier run id (background job, `%MWH_DATA_ROOT%\runs\jobs\ep115.log`) recorded; report artefact
  passes `mwh disclose check`; the identifier-free guard on `similar_stays` is tested.
