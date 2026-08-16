# EP-122 — Tabular foundation model vs GBM

**Size:** M · **Tier:** fixture+dev+full · **Core/Stretch:** core · **Depends on:** EP-121 (GPU enablement (gpu group; doctor --gpu; XGBoost-CUDA vs LightGBM-CPU)), EP-110 (Signature #1: first-24h → in-hospital mortality) · **Blocks:** EP-126 (Capstone #5)

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-101 (Re-plan P6) before execution.

## Context

The deep-learning representative workflow chosen in D-7: a pretrained tabular foundation model
(TabPFN-class, in-context learning on structured features) against the EP-110 LightGBM champion on
identical splits — VRAM-bounded (8 GB), licensed weights only (GOVERNANCE §10), CPU fallback for tests.
Weights are downloaded once by the owner into `%MWH_DATA_ROOT%\models\weights\` (no runtime fetch;
`MWH_ALLOW_REMOTE` stays false) and the licence is recorded in this brief, the registry entry and the
model card. Category 26 (with EP-121) and 20.

## Scope sketch (refine at re-plan)

1. **Licence check + adapter** (`src/mimicwarehouse/ml/fm.py`): candidate order TabPFN 2.x (Prior
   Labs; verify the current weights licence permits research use at execution) then TabICL
   (BSD-3-Clause); the adapter refuses to load weights whose licence is not on the permitted list or
   whose file hash is not recorded in `models\weights\manifest.json`; `FMClassifier` exposing the
   sklearn API so the EP-107 runner, EP-105 assessment and EP-106 registry work unchanged.
2. **Bounding** — context limits respected by subject-stratified subsampling of the training era
   (rows and features capped per the model's documented limits; top features from EP-108 importance);
   an ensemble over several subsamples; VRAM peak logged via EP-121; CPU path for fixture tests;
   inference batched to keep the working set ≤ 6 GB.
3. **Comparison** — FM vs LightGBM vs LR on the EP-110 temporal holdout: AUROC/AUPRC/Brier/calibration/
   DCA with paired-bootstrap differences (EP-105 `compare`), wall time and VRAM in the benchmark
   ledger; dev in-session, full as a logged background job (`--group gpu`).
4. **Report + card** — model card with weights provenance and licence; report artefact labelled
   *predictive*, retrospective statement, and a "no fine-tuning; in-context only" limitation.
5. **Tests** (`tests/ep/test_ep122.py`, `@pytest.mark.ep_122`): the adapter *refuses* an unlisted
   licence and a hash mismatch (crafted manifest); CPU fixture fit produces valid probabilities;
   subsampling never splits a subject; comparison table passes `disclose.check`.

## Out of scope

- Fine-tuning, event-sequence FMs (MOTOR/CLMBR), text encoders → parked (final-roadmap 26).
- Sequence model → EP-123; drift audit of the FM → EP-119 pattern, optional at re-plan.

## Verification / acceptance (sketch)

- `uv run --group gpu poe test -m ep_122` green on fixture (+dev), CPU path green without `gpu`;
  `uv run --group gpu mwh verify EP-122` green.
- Full-tier run id (background job, `%MWH_DATA_ROOT%\runs\jobs\ep122.log`), VRAM peak and weights
  licence recorded in the completion note; report passes `mwh disclose check`.
