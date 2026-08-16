# EP-103 — Model-ready dataset B: patient-safe partitions + feature dictionary

**Size:** M · **Tier:** fixture+dev+full · **Core/Stretch:** core · **Depends on:** EP-102 (Model-ready dataset A: feature spec, windows, normalization, indicators) · **Blocks:** EP-104 (Splits (grouped/temporal by anchor_year_group), CV, nested CV), EP-114 (Unsupervised A: clustering / mixtures / stability), EP-116 (Dimensionality reduction & high-dimensional analysis), EP-126 (Capstone #5)

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-101 (Re-plan P6) before execution.

## Context

Completes category 34 (model-ready dataset generation). A dataset is only "model-ready" when its rows
can be partitioned without a subject leaking across train/validation/test and when every column is
documented well enough for a model card. This brief adds both to the EP-102 artefacts: patient-safe
partition assignments (deterministic under the EP-36 seed policy) and a generated feature dictionary
that is itself a disclosure-checked aggregate (D-33, D-40). `anchor_year_group` remains the only
cross-patient temporal axis (per-patient date shift), so the era partition is defined on it and nothing
else.

## Scope sketch (refine at re-plan)

1. **Partitioner** (`src/mimicwarehouse/ml/datasets.py`, `assign_partitions(dataset_id, plan, seed)`):
   `PartitionPlan` pydantic model with two schemes written side by side into
   `marts/ml/<dataset_id>@<version>/partitions.parquet` (grain key → columns): (a) `fold` = k grouped,
   outcome-stratified folds keyed by `subject_id` (hash of subject and seed → all rows of a subject share
   a fold); (b) `era_split` = `train` / `validate` / `test` / `sealed` by `anchor_year_group` (default
   train = 2008–2010, 2011–2013, 2014–2016; validate = last training group held back within CV;
   test = 2017–2019; `sealed` = 2020–2022, never read unless a plan names it explicitly; the plan names
   the groups explicitly). Partition sizes are recorded in `manifest.json`.
2. **Feature dictionary generator** — `describe_dataset(dataset_id)` → `feature_dictionary.parquet` and
   a Markdown rendering: name, source (table/concept/mart), code set or itemid set version (EP-39/40),
   aggregation, unit, window, dtype, plausibility range, missing rate (%), summary quantiles, whether an
   indicator column exists. All numbers pass through `mimicwarehouse.disclose` before rendering.
   The Markdown rendering opens with `Claim type: exploratory (dataset description)` and the
   retrospective statement, and is written through EP-59 `export_table` so the sidecar records the
   check.
3. **CLI** — `uv run --group dev mwh ml dataset describe <id> --tier dev` prints dictionary and partition
   counts (aggregates only; the CLI never prints rows).
4. **Representative dataset** — partition and describe the EP-102 tracer dataset on fixture, dev and
   full; commit the full-tier dictionary Markdown under `docs/analyses/datasets/` with its
   `.disclosure.json` sidecar.
5. **Tests** (`tests/ep/test_ep103.py`, `@pytest.mark.ep_103`): hypothesis property "no subject appears
   in two folds or two era partitions"; same seed → identical assignments; a plan that asks for a
   row-wise random split on a sub-subject grain is *refused*; dictionary row count = feature count.

## Out of scope

- Split iterators consumed by estimators (grouped/temporal/nested CV) → EP-104.
- Feature selection or dimensionality reduction → EP-116.
- Interactive dataset browsing → EP-125 (Models pages) / EP-61 (Catalog & QC).

## Verification / acceptance (sketch)

- `uv run poe test -m ep_103` green on fixture (+dev); `uv run --group dev mwh verify EP-103` green.
- Full-tier partition + dictionary generation run as a logged background job; run id recorded in the
  completion note; `docs/analyses/datasets/<dataset_id>.md` exists with a passing `.disclosure.json`.
- `mwh disclose check` refuses the dictionary if any missing-rate denominator cell is < 11 unsuppressed
  (crafted fixture test).
