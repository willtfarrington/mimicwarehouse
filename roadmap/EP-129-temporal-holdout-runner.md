# EP-129 — Temporal holdout runner

**Size:** M · **Tier:** fixture+dev+full · **Core/Stretch:** core · **Depends on:** EP-51 (Protocol schema + freeze registry + `mwh protocol`), EP-104 (Splits (grouped/temporal by anchor_year_group), CV, nested CV) · **Blocks:** EP-135 (Capstone #6 + full-tier regression)

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-127 (Re-plan P7) before execution.

## Context

The method half of category 37: emulate a *future* evaluation on retrospective data. A frozen
protocol (EP-51, D-25) declares a `temporal_holdout` block; EP-104 provides grouped/temporal
splitters by `anchor_year_group` in `mimicwarehouse.ml.splits`. This brief builds
`mimicwarehouse.protocol.holdout` (DESIGN §13): fit the protocol's analysis plan on development
eras, evaluate exactly once on the holdout eras, keep later eras sealed, and write run records whose
notice states that this is prospective-*style* evaluation over retrospective, date-shifted data
(D-18 tiers; the D-6 signature trio reuses it). MIMIC caveats that bite: `anchor_year_group`
(2008–2010 … 2020–2022) is a per-patient attribute, so an era split is automatically
patient-disjoint but is not calendar time; the ICD-9→10 switch (~2015) falls inside 2014–2016
(dual code sets, EP-40); the 2020–2022 era carries pandemic-era shift and is sealed by default;
`dod` reaches only ~1 year past the last discharge, so any out-of-hospital outcome needs the EP-34
censoring rule.

## Scope sketch (refine at re-plan)

1. **`src/mimicwarehouse/protocol/holdout.py`** — `TemporalHoldout` pydantic block
   (`development_eras`, `holdout_eras`, `sealed_eras`, `one_look: bool = True`) validated against
   the five era labels; `run_holdout(protocol_hash, tier, actor)` resolves the frozen protocol
   (refusing unfrozen/modified, as `mwh protocol run` does), builds the era partition with
   `ml.splits.temporal_split` over the protocol's materialised cohort (EP-47), asserts
   patient-disjointness, fits on development eras, evaluates on holdout eras, and — inside the
   EP-35 run context — writes `runs/<run_id>/manifest.json` with a `holdout` block and a `notices`
   list containing the module constant `HOLDOUT_NOTICE` ("Prospective-style evaluation over
   retrospective, date-shifted MIMIC-IV data: eras are `anchor_year_group` bins, not calendar
   time; this is not a prospective validation."), then appends
   `runs/holdouts.jsonl` {protocol_hash, tier, holdout_eras, run_id, timestamp, actor}.
   `TemporalHoldout` replaces EP-51's `temporal_holdout {train_eras, test_eras}` block (a dated
   addendum on EP-51 renames the fields; `train_eras` → `development_eras`, `test_eras` →
   `holdout_eras`, new `sealed_eras`).
2. **Plan adapters** — a `PlanRunner` protocol (`fit(dev_frame) -> artefact`,
   `evaluate(artefact, holdout_frame) -> metrics table`) with two adapters shipped here:
   `PredictionPlan` (wraps the signature #1 pipeline: features EP-102/103, LR/LightGBM EP-107/108,
   metrics with bootstrap CIs from `ml.assess` EP-105) and `GlmPlan` (wraps `stats.glm` EP-79:
   coefficient table on development eras, discrimination/calibration of the fitted GLM on holdout).
   Other plan kinds register in a dict from their owning briefs.
3. **One-look and sealing rules** — on `full`, a (protocol_hash, holdout_eras) pair may be evaluated
   once; a second call is refused with a message naming the run id that already looked, unless the
   protocol was amended (new hash, EP-51/EP-128). `sealed_eras` never enter any frame the plan
   sees; unsealing requires an amendment. `dev`/`fixture` runs are unlimited but tagged
   `rehearsal: true`. `--dry-run` prints era partition counts through `disclose.suppress` (EP-43)
   and fits nothing. On first use `run_holdout` back-fills `runs/holdouts.jsonl` from any earlier
   full-tier run whose manifest carries the same `protocol_hash` and holdout eras (EP-110), so
   that run counts as the look; the EP-129 full-tier representative run then re-executes only if
   no such line exists, otherwise it runs on dev as `rehearsal: true` and cites EP-110's run id.
4. **CLI** — `mwh protocol holdout <hash> --tier {fixture,dev,full} [--dry-run]` and
   `mwh protocol holdout status <hash>` (looks taken, run ids). Outputs are aggregates only:
   metric tables with CIs, era-wise counts suppressed, one Vega-Lite figure per metric via `viz/`.
5. **Representative workflow** — signature #1 (first-ICU-stay adults, first-24 h features →
   in-hospital mortality, EP-110): development 2008–2016, holdout 2017–2019, 2020–2022 sealed;
   dev in-session, then full as a logged background job (`uv run --group dev mwh protocol holdout
   <hash> --tier full`, EP-19's launcher pattern, log
   `%MWH_DATA_ROOT%\runs\jobs\ep129-holdout-full.log`); plus one `GlmPlan` rehearsal on dev
   (first-24 h lactate → mortality, associational). The report artefact is rendered by EP-130 once
   it exists; until then the run's `tables/` stand in.
6. **Tests `tests/ep/test_ep129.py`** (`@pytest.mark.ep_129`, fixture + dev): unfrozen or modified
   protocol refused; partition patient-disjoint and free of sealed eras; second full-tier look
   refused (temporary ledger); manifest carries `HOLDOUT_NOTICE`; dry-run writes no artefacts.

## Out of scope

- Split/CV machinery → EP-104; model fitting and assessment → EP-105/107/108/110.
- Drift audit of holdout vs development eras → EP-119 (cite its run id in the report).
- Report rendering → EP-130/132; the Freezer UI (amend/unseal) → EP-128.

## Verification / acceptance (sketch)

- `uv run poe test -m ep_129` green on fixture + dev; `uv run --group dev mwh verify EP-129` green.
- Full-tier run id, wall time and peak RSS recorded in the completion note; `runs/holdouts.jsonl`
  holds exactly one full-tier line for the signature #1 hash; a repeat invocation is refused (quote
  the refusal message, never data).
- The metrics table passes `mwh disclose check`; the report built at the capstone (EP-135) is
  labelled predictive and shows `HOLDOUT_NOTICE` and the retrospective statement.
- The full-tier metrics table is exported via EP-59 `export_table(claim_type="predictive")` with
  `HOLDOUT_NOTICE` in the footer so a disclosure-checked artefact exists at this brief, independent
  of EP-135.

## Parked → final-roadmap.md

- Rolling-origin / era-by-era backtesting across all five eras — trigger: after EP-85 forecasting
  patterns settle; category 37.
