# EP-119 — Leakage / drift / robustness audits

**Size:** M · **Tier:** fixture+dev+full · **Core/Stretch:** core · **Depends on:** EP-110 (Signature #1: first-24h → in-hospital mortality) · **Blocks:** EP-126 (Capstone #5)

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-101 (Re-plan P6) before execution.

## Context

Category 29 (leakage, drift and robustness testing) and the third pillar of the signature depth (D-6:
prediction + assessment + leakage/drift). The audits run against a *registered model + its dataset and
split plan* and produce an audit artefact whose run id the model card links (EP-106 left the slot).
Representative targets: the champion model of each signature (EP-110, EP-111, EP-112; item 4), so
all three cards carry an audit link. Drift is measured only across
`anchor_year_group` (the sole cross-patient temporal axis under the date shift); subgroup robustness
tables obey the small-cell rule through `mimicwarehouse.disclose`. Like the governance briefs, the
key acceptance is that a crafted violation is *caught*.

## Scope sketch (refine at re-plan)

1. **Leakage audit** (`src/mimicwarehouse/ml/audits.py`, `audit_leakage(model_id)`): re-checks the
   EP-102 window invariant from the dataset manifest and generated SQL (max contributing event time
   vs index + window); single-feature AUROC screen (flag > 0.95); "outcome-defining feature" screen by
   name/source pattern (e.g. discharge disposition or `dod`-derived columns in a mortality dataset);
   subject overlap across the split plan (EP-104 guard); duplicate rows; near-constant features.
2. **Drift audit** — `audit_drift(model_id)`: covariate shift per feature across eras (PSI, KS),
   adversarial validation (era classifier AUROC on features), label prevalence per era, model
   performance and calibration per era (from EP-105 tables), a drift summary flag with thresholds
   recorded in the manifest.
3. **Robustness audit** — `audit_robustness(model_id)`: performance under injected missingness
   (10/30 %), unit-scale perturbation of a feature (a wrong-unit simulation, EP-39 ranges), subgroup
   performance by age band / sex / admission type / care unit (small cells suppressed), and rank
   stability of top-10 features across subject bootstraps (EP-78).
4. **Artefact + card link** — `runs/<run_id>/audit.md` (+ tables/figures) and
   `register_audit(model_id, run_id)` writing the link into `model.json`; CLI
   `uv run --group dev mwh ml audit <model_id> --tier dev`. Targets: the champion model of each
   signature — EP-110 (LightGBM), EP-111 (LightGBM/LR at `hadm` grain, with the `index = discharge`
   window re-check) and EP-112 (discrete-time LightGBM, using `assess_tte` per-era metrics for the
   drift table); `register_audit` fills all three cards; the three full-tier audit run ids are
   recorded in the completion note. `audit.md` opens with `Claim type: predictive (audit of a
   predictive model)` and the retrospective statement, and is written through EP-59 `export_table`
   so the sidecar records the check.
5. **Tests** (`tests/ep/test_ep119.py`, `@pytest.mark.ep_119`): a fixture dataset with a planted leaky
   feature (post-index lab) is *flagged*; a planted era shift raises the drift flag; subgroup table
   suppresses a crafted small cell; the audit refuses a model whose dataset manifest is missing.

## Out of scope

- SHAP/error slices → EP-120; ablation grids → EP-124; external validation (eICU) → parked.

## Verification / acceptance (sketch)

- `uv run poe test -m ep_119` green on fixture (+dev); `uv run --group dev mwh verify EP-119` green.
- Full-tier audits of the EP-110, EP-111 and EP-112 champion models run as one logged background
  job (`%MWH_DATA_ROOT%\runs\jobs\ep119.log`); each run id is recorded in the completion note and
  linked in its model card; every `audit.md` passes `mwh disclose check`.
