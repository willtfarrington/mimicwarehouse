# EP-110 — Signature #1: first-24h → in-hospital mortality

**Size:** M · **Tier:** fixture+dev+full · **Core/Stretch:** core · **Depends on:** EP-51 (Protocol schema + freeze registry + `mwh protocol`), EP-108 (Trees / ensembles A (DT, RF, bagging, LightGBM)), EP-106 (Model registry + model cards) · **Blocks:** EP-111 (Signature #2: 30-day readmission), EP-112 (Signature #3: AKI within 7 d (time-to-event prediction)), EP-119 (Leakage / drift / robustness audits), EP-120 (Interpretability & error analysis), EP-122 (Tabular foundation model vs GBM), EP-124 (Simulation / ablation / benchmark harness), EP-126 (Capstone #5)

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-101 (Re-plan P6) before execution.

## Context

The first of the polished trio (D-6): the tracer bullet (D-5: first ICU stay adults → in-hospital
mortality, EP-31) re-done as a frozen-protocol, temporally-held-out, calibrated and carded prediction
workflow. It exercises EP-51 (protocol freeze, D-25), EP-102/103 (dataset), EP-104 (temporal split by
`anchor_year_group`), EP-105 (assessment), EP-108 (LightGBM champion vs EP-107 LR baseline) and EP-106
(card). Everything downstream in P7 (audits, interpretability, FM, benchmark) reuses this run, so its
protocol hash and run ids are the phase's anchors. Categories 20 and 37.

## Scope sketch (refine at re-plan)

1. **Protocol YAML** (`protocols/sig1-mortality-24h.yaml` in the EP-51 protocol directory): cohort =
   first ICU stay per subject, age ≥ 18, ICU LOS ≥ 24 h (observation window must be complete; the
   protocol states this exclusion and its consequence); index = ICU intime; features = EP-102 first-24 h
   groups; outcome = in-hospital death (EP-75 endpoint, no `dod` dependence); split = temporal by
   `anchor_year_group` (development eras 2008–2010, 2011–2013, 2014–2016 with grouped inner CV;
   holdout era 2017–2019 evaluated once; 2020–2022 declared `sealed` and never read — it is unsealed
   only by an EP-128 amendment at EP-135); the protocol's `temporal_holdout` block names these three
   lists explicitly so EP-129's `TemporalHoldout` can consume it unchanged; models = LR (L2) and
   LightGBM; primary metric AUROC, co-primary calibration slope; DCA thresholds 5–50 %; claim type
   `predictive`. Frozen with `uv run --group dev mwh protocol freeze`.
2. **Runner** (`src/mimicwarehouse/ml/signatures.py`, `run_signature(protocol_hash, tier)`) — invoked
   by `mwh protocol run <hash> --tier <tier>`; refuses unfrozen/modified protocols; builds the dataset,
   fits on the train era, evaluates on the held-out eras, registers both models, renders cards, writes
   a report artefact (Markdown + figures) with the *predictive* label and the retrospective statement.
3. **Runs** — fixture and dev in-session; full tier as a logged background job; the model card links
   the run id, protocol hash, dataset dictionary and (once EP-119 runs) the audit.
4. **Tests** (`tests/ep/test_ep110.py`, `@pytest.mark.ep_110`): the runner refuses an unfrozen protocol;
   test-era subjects never appear in training; card and report pass `disclose.check`; a fixture run is
   reproducible under seed (identical metrics table hash).

## Out of scope

- SHAP/error analysis → EP-120; leakage/drift/robustness → EP-119; FM comparator → EP-122;
  ablations/seeds grid → EP-124; the interactive Freezer page → EP-128; general holdout runner → EP-129.

## Verification / acceptance (sketch)

- `uv run poe test -m ep_110` green on fixture (+dev); `uv run --group dev mwh verify EP-110` green.
- Protocol hash and full-tier run id (background job, `%MWH_DATA_ROOT%\runs\jobs\ep110.log`) recorded
  in the completion note; two registered models with cards; report artefact passes `mwh disclose check`
  and is promoted to `docs/analyses/` with its sidecar.
