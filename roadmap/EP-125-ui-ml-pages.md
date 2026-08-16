# EP-125 — ML pages in app

**Size:** M · **Tier:** fixture+dev+full · **Core/Stretch:** core · **Depends on:** EP-57 (App shell A (Streamlit multipage)), EP-105 (Assessment module), EP-120 (Interpretability & error analysis) · **Blocks:** EP-126 (Capstone #5)

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-101 (Re-plan P6) before execution.

## Context

The "Models" page group of the Lab app (D-21; DESIGN §16), category 32 for P7. The pages are read-only
views over the registry (`runs.models`, EP-106), stored assessment tables (EP-105) and interpretation
tables (EP-120) — never over raw predictions — so they meet the ≤ 5 s target on full (D-28) without
touching the lake, and small-cell badges come from the EP-58 shell components. Row-level predictions or
per-stay explanations are not shown (an owner-gated variant is parked). `ui` dependency group only.

## Scope sketch (refine at re-plan)

1. **`app/pages/models.py`** (registered in the EP-57 pages registry; split into sub-tabs): Registry —
   list/filter models (algorithm, dataset, protocol hash, tier, headline metrics), select one;
   Assessment — ROC/PR/calibration/DCA Altair charts and the threshold table with CIs from the stored
   EP-105 tables; Interpretability — global importance, dependence curves, subgroup forest and error
   slices from EP-120 outputs (badges at n < 11); Card — rendered EP-106 model card; Compare — two
   models side by side (paired differences from `compare`).
2. **Data access** — `READ_ONLY` `runs.duckdb` + `models/<id>/*.json|parquet` via a cached loader in
   `src/mimicwarehouse/ml/registry.py`; no `safe_query` needed for lake tables because none are read;
   the run id links to the Runs page (EP-134, later — plain text until then).
3. **Latency + screenshots** — page load ≤ 5 s on full recorded via the EP-56 harness; demo/fixture-tier
   screenshots through EP-60 tooling (fixture models registered by the EP-106 tests are enough for the
   showcase if the demo tier is too small to train on).
4. **Tests** (`tests/ep/test_ep125.py`, `@pytest.mark.ep_125`): Streamlit `AppTest` renders every tab on
   the fixture registry; a model whose subgroup table contains a small cell shows the badge; the page
   never issues a query against `mimiciv_*` schemas (guard test on the loader).

## Out of scope

- Runs & Provenance browser and Reports gallery → EP-134; Protocol Freezer page → EP-128.
- Owner-gated per-stay explanation view → parked (below); export → EP-59/EP-133.

## Verification / acceptance (sketch)

- `uv run --group ui poe test -m ep_125` green on fixture; `uv run --group ui mwh verify EP-125` green.
- Observable behaviour on the dev tier (`uv run --group ui mwh app --tier dev`); one full-tier page
  latency recorded (≤ 5 s); a demo/fixture screenshot at the EP-60 docs path with its `.disclosure.json`.

## Parked → final-roadmap.md

- Owner-only per-stay SHAP/explanation view behind the EP-58 row-view gate (row-level, audited, never
  exported) — trigger: the owner wants case-level review of model errors in the app.
