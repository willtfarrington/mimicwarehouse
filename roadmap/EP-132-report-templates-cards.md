# EP-132 — Model card + methods summary + executive summary templates

**Size:** M · **Tier:** fixture · **Core/Stretch:** core · **Depends on:** EP-130 (Report engine A: Jinja2 → MD/HTML), EP-106 (Model registry + model cards) · **Blocks:** EP-135 (Capstone #6 + full-tier regression)

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-127 (Re-plan P7) before execution.

## Context

Category 33 (serving 20 and 28): three templates on the EP-130 engine, fed by the model registry
and its `card.json` (EP-106), the assessment module (EP-105: discrimination, calibration, DCA,
thresholds, bootstrap CIs), the leakage/drift/robustness audits (EP-119), interpretability (EP-120,
SHAP tree/linear only), protocol (EP-51) and run (EP-35) records. D-6 makes the signature trio the
polished cards; D-1's two audiences want an executive summary (hiring managers, clinical readers)
next to a technical methods summary; D-23 fixes the formats. Model cards must be honest about the
data: single-centre BIDMC, de-identified and date-shifted, ages ≥ 89 shown as 91, `dod` ~1-year
horizon, retrospective, not for clinical use.

## Scope sketch (refine at re-plan)

1. **`src/mimicwarehouse/report/cards.py`** — `model_card(model_id) -> Report` (claim type
   `predictive`) with sections: model details (registry id, algorithm, version, git sha); intended
   use and out-of-scope uses; data (cohort spec@version, grain, eras used, n per era via
   `disclose.suppress`); features and preprocessing (feature dictionary, EP-103); training and
   evaluation protocol (frozen hash, temporal-holdout eras from EP-129 or the EP-104 split);
   metrics with 95 % CIs overall and by subgroup (age band, sex, admission type — small cells
   suppressed); calibration and DCA figures; leakage/drift/robustness audit link (EP-119 run id, or
   the explicit sentence "no audit recorded"); interpretability summary (EP-120 top-k); ethical
   considerations and caveats (list above); provenance footer.
2. **`methods_summary(run_id) -> Report`** — STROBE/RECORD-style paragraphs for observational claim
   types, TRIPOD+AI-style for predictive, chosen from the protocol's claim type; auto-filled from
   protocol + run record: data source and version (MIMIC-IV 3.1), cohort / index event / windows,
   outcome definition (EP-75/76), covariates and code-set versions (EP-40), missing-data handling
   (EP-87), analysis plan and software versions, sensitivity analyses; ends with the "What this
   analysis deliberately does not claim" list.
3. **`executive_summary(run_id_or_report) -> Report`** — one page: question, population (n
   suppressed), headline estimate with CI, one figure, claim-type badge, retrospective statement,
   "reproduce from run id …", link to the full report; `plain_language=True` variant.
4. **Templates and CLI** —
   `report/templates/{model_card,methods_summary,executive_summary}.{md,html,typ}.j2`;
   `mwh report card <model_id> --out <dir>`, `mwh report methods <run_id> --out <dir>`,
   `mwh report exec <run_id> --out <dir>` (all formats via EP-130/131).
5. **Representative** — render the signature #1 card (EP-110 registry entry) plus its methods and
   executive summaries from the EP-110 full-tier signature #1 model id and run id (the category-33
   full-tier representative artefact; model id, run id and output paths recorded in the completion
   note); fixture tests use a synthetic `card.json` and run directory.
6. **Tests `tests/ep/test_ep132.py`** (`@pytest.mark.ep_132`, fixture): the three templates render
   on synthetic inputs; a card missing intended-use or evaluation eras fails validation; a subgroup
   below k is suppressed; sidecars pass `mwh disclose check`; each output shows claim type and the
   retrospective statement; the card cites an audit run id or the "no audit recorded" sentence.

## Out of scope

- Registry / `card.json` schema → EP-106; metrics → EP-105; audits → EP-119; explanations → EP-120.
- Rendering and export mechanics → EP-130 / EP-131.
- Case-study compilation → EP-161; the project's own executive one-pager → EP-162.

## Verification / acceptance (sketch)

- `uv run poe test -m ep_132` and `uv run --group dev mwh verify EP-132` green on fixture;
  `uv run --group dev mwh report card <signature-1 model id> --out %MWH_DATA_ROOT%\runs\cards\sig1`
  produces MD/HTML/PDF passing `mwh disclose check`; the completion note records the model id and
  run ids used.
- Each template output carries the claim-type badge, the retrospective statement and a provenance
  footer; subgroup tables show suppression where n < 11.

## Parked → final-roadmap.md

- Auto-filled STROBE / TRIPOD+AI checklists from protocol + run records (v2 REP-3).
- Model-card export in the Hugging Face card format — trigger: publishing model weights (not in v1).
