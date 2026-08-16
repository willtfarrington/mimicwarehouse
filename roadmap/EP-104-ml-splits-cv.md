# EP-104 — Splits (grouped/temporal by anchor_year_group), CV, nested CV

**Size:** M · **Tier:** fixture · **Core/Stretch:** core · **Depends on:** EP-103 (Model-ready dataset B: patient-safe partitions + feature dictionary) · **Blocks:** EP-105 (Assessment module), EP-126 (Capstone #5), EP-129 (Temporal holdout runner)

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-101 (Re-plan P6) before execution.

## Context

First half of category 28 (model assessment and selection): the split machinery every P7 estimator
must use. Grouped splits by `subject_id` prevent the classic MIMIC leak (repeat stays of one patient on
both sides); temporal splits by `anchor_year_group` are the only honest "future" evaluation available
under the per-patient date shift (standing decision for P7; D-6). The temporal-holdout runner (EP-129)
in P8 wraps these iterators, so their `SplitPlan` must be serialisable into run records (EP-35) and
protocols (EP-51). Tier is fixture: this is pure library code over the EP-103 partition columns.

## Scope sketch (refine at re-plan)

1. **`src/mimicwarehouse/ml/splits.py`** — `SplitPlan` pydantic model (`kind` ∈ {`grouped_kfold`,
   `temporal`, `nested`}, `k`, `group_key`, `stratify_on`, `train_groups`/`test_groups` for temporal,
   `inner_k` for nested, `seed`) and iterators that yield `(train_idx, test_idx)` from the EP-103
   `partitions.parquet`: `GroupedKFold` (wraps sklearn `StratifiedGroupKFold`), `TemporalSplit`
   (train on named earlier `anchor_year_group` levels, test on later ones; rolling-origin variant),
   `NestedCV` (outer grouped folds, inner grouped folds for hyperparameter selection).
2. **Guards** — `assert_no_subject_overlap(train, test)`; the plan validator refuses a row-wise random
   split when the dataset grain is finer than `subject`; temporal plans must name disjoint, ordered
   groups; every plan hashes to a stable id written into the run manifest.
3. **Reporting helpers** — per-fold/era row and event counts as a tidy table (through
   `mimicwarehouse.disclose` so small folds are flagged), and a compact "split plan" Markdown block for
   model cards (EP-106).
4. **Tests** (`tests/ep/test_ep104.py`, `@pytest.mark.ep_104`): overlap guard fires on a crafted leaky
   plan; temporal split never places a later era in train; nested CV yields inner folds only from outer
   train; determinism under seed; plan round-trips YAML → model → YAML.

## Out of scope

- Metrics, calibration, DCA → EP-105; hyperparameter search at scale (Optuna) → parked.
- Protocol-driven temporal holdout execution and its retrospective disclaimer → EP-129.

## Verification / acceptance (sketch)

- `uv run poe test -m ep_104` green on fixture; `uv run --group dev mwh verify EP-104` green.
- A crafted plan with a subject on both sides is *refused* in a test; the split-plan block renders and
  passes `mwh disclose check` on the fixture dataset.
- The first full-tier use of `TemporalSplit`/`NestedCV` is EP-110's frozen-protocol run (and EP-129's
  holdout runner); their run ids and the split-plan Markdown block in the EP-110 model card are the
  category-28 full-tier evidence for this module.
