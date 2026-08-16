# EP-126 — Capstone #5

**Size:** M · **Tier:** fixture+dev+full · **Core/Stretch:** core · **Depends on:** EP-102 (Model-ready dataset A: feature spec, windows, normalization, indicators), EP-103 (Model-ready dataset B: patient-safe partitions + feature dictionary), EP-104 (Splits (grouped/temporal by anchor_year_group), CV, nested CV), EP-105 (Assessment module), EP-106 (Model registry + model cards), EP-107 (Baselines (LR / regularized / kNN / SVM)), EP-108 (Trees / ensembles A (DT, RF, bagging, LightGBM)), EP-109 (Trees / ensembles B (stacking; overfitting diagnostics)), EP-110 (Signature #1: first-24h → in-hospital mortality), EP-111 (Signature #2: 30-day readmission), EP-112 (Signature #3: AKI within 7 d (time-to-event prediction)), EP-113 (Nonlinear / flexible modeling), EP-114 (Unsupervised A: clustering / mixtures / stability), EP-115 (Unsupervised B: anomaly detection / association rules / similarity search), EP-116 (Dimensionality reduction & high-dimensional analysis), EP-117 (Bayesian A: PyMC + nutpie models + Bambi GLMM), EP-118 (Bayesian B: EM / mixtures / one graphical model / likelihood + bootstrap), EP-119 (Leakage / drift / robustness audits), EP-120 (Interpretability & error analysis), EP-121 (GPU enablement (gpu group; doctor --gpu; XGBoost-CUDA vs LightGBM-CPU)), EP-122 (Tabular foundation model vs GBM), EP-124 (Simulation / ablation / benchmark harness), EP-125 (ML pages in app) · **Blocks:** EP-127 (Re-plan P7 (writes full P8, re-charters P9; notes-track go/no-go))

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-101 (Re-plan P6) before execution.

## Context

The per-phase capstone (D-8): a case study that a hiring manager and a clinical-informatics reader can
both follow (D-1, two reading paths), assembled from recorded P7 run ids — the three signature
workflows (D-6), the audits and interpretation, the FM-vs-GBM comparison, the benchmark/ablation, and
one figure each from the flexible/unsupervised/dimred/Bayesian briefs. Follows the `docs/analyses/`
convention set by EP-32 (hupsim precedent: "What it deliberately does not claim" + Reproduction blocks).
Every number is reproduced from a run id; every artefact carries a `.disclosure.json` (D-40).

## Scope sketch (refine at re-plan)

1. **`docs/analyses/05-prediction-signature.md`** — sections: question and cohorts (three signature
   protocols with hashes); data → dataset → splits (dictionary and split-plan blocks); results per
   signature (temporal-holdout tables, calibration/DCA figures, model-card links); leakage/drift audit
   summary; what the models lean on / where they fail; FM vs GBM; ablation/benchmark; the exploratory
   side-lane (phenotypes, rules, high-dim screen, hierarchical model, latent classes) in one figure each;
   "What it deliberately does not claim" (retrospective, single centre, date shift, `dod` horizon,
   billing codes, no external validation); Reproduction block listing every run id, protocol hash,
   dataset id and `mwh` command.
2. **Figures/screenshots** — exported via EP-59 primitives from the recorded runs; Models-page
   screenshots via EP-60 on demo/fixture; all under `docs/analyses/05/` with sidecars.
3. **Bookkeeping** — a phase benchmark table (wall time, peak RSS/VRAM per full-tier run) appended to
   the case study and to `roadmap/README.md` risks if any budget was exceeded; coverage-table re-audit
   for categories 20–26, 28–31, 34; confirm every P7 full-tier run id resolves in `runs.duckdb`.
4. **Tests** (`tests/ep/test_ep126.py`, `@pytest.mark.ep_126`): every run id cited in the case study
   exists in the ledger; every figure has a passing sidecar; links resolve (roadmap_check-style).

## Out of scope

- New analyses or model fits (only reproduction from recorded runs); the notes go/no-go → EP-127.
- Reports engine rendering (MD/HTML/PDF) → P8 (EP-130–132); this capstone is hand-authored Markdown.

## Verification / acceptance (sketch)

- `uv run poe test -m ep_126` green; `uv run --group dev mwh verify EP-126` green.
- `docs/analyses/05-prediction-signature.md` and its figures exist, numbers reproduce from the recorded
  run ids, every artefact passes `mwh disclose check`, links resolve.
