# EP-106 — Model registry + model cards

**Size:** M · **Tier:** fixture · **Core/Stretch:** core · **Depends on:** EP-35 (Provenance run ledger) · **Blocks:** EP-107 (Baselines (LR / regularized / kNN / SVM)), EP-110 (Signature #1: first-24h → in-hospital mortality), EP-126 (Capstone #5), EP-132 (Model card + methods summary + executive summary templates)

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-101 (Re-plan P6) before execution.

## Context

Serves categories 38 (end-to-end provenance) and 20 (supervised prediction): every model trained in P7
is registered with the run that produced it (EP-35, D-24) and described by a model card that is itself
a disclosure-checked aggregate artefact (D-33, D-40). The standing P7 decision is "model cards for every
registered model"; the polished Jinja/HTML/PDF card template arrives in EP-132, so this brief ships the
data model, the ledger and a plain-Markdown renderer that EP-132 replaces without changing callers.
Registry metadata is JSONL + `runs.duckdb` views like every other ledger (D-24); model artefacts live
under the data root, never in git.

## Scope sketch (refine at re-plan)

1. **`src/mimicwarehouse/ml/registry.py`** — `register_model(estimator, *, dataset_id, split_plan,
   assessment, run_id, algorithm, params, weights_license=None) -> model_id`; writes
   `%MWH_DATA_ROOT%\models\<model_id>\` (joblib artefact, `model.json` with algorithm, hyperparameters,
   feature-list hash, dataset id + snapshot ids, split plan hash, seeds, headline metrics with CIs, git
   sha, env hash, protocol hash if any, licence of any pretrained weights) and appends one line to
   `runs\models.jsonl`; `mwh runs refresh` adds a `runs.models` view; `load_model(model_id)`.
2. **`ModelCard` pydantic model + `render_card(model_id) -> Markdown`** — sections: intended use;
   data (cohort ref, tier, era coverage, attrition link); features (link to the EP-103 dictionary);
   splits; performance (EP-105 tables with CIs, calibration, DCA); subgroup table (suppressed via
   `mimicwarehouse.disclose`); limitations (retrospective MIMIC-IV; single centre; date shift; `dod`
   horizon; ages ≥ 89 = 91); claim type `predictive` by default, `exploratory` allowed when the
   registering brief passes `claim_type="exploratory"` (EP-152 label-recovery models), no other value
   accepted; leakage/drift audit link (filled by EP-119);
   provenance footer (run id, snapshot ids, protocol hash, env hash).
3. **CLI** — `uv run --group dev mwh models list|show <model_id>|card <model_id>` (aggregate metadata
   only; never predictions or rows).
4. **Tests** (`tests/ep/test_ep106.py`, `@pytest.mark.ep_106`): register a dummy estimator on the fixture
   dataset → ledger line, `model.json` schema valid, `runs.models` view row; card renders and passes
   `disclose.check`; a card containing an unsuppressed small subgroup cell is *refused* by the renderer.

## Out of scope

- Jinja/HTML/Typst card and methods-summary templates → EP-132; Reports page → EP-134.
- Model comparison UI → EP-125; MLflow mirror → parked (final-roadmap 28–31).

## Verification / acceptance (sketch)

- `uv run poe test -m ep_106` green on fixture; `uv run --group dev mwh verify EP-106` green.
- `mwh models card <fixture_model_id>` writes Markdown that passes `mwh disclose check` and carries a
  `.disclosure.json` sidecar.
