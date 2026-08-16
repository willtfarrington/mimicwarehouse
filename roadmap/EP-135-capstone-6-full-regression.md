# EP-135 — Capstone #6 + full-tier regression

**Size:** M · **Tier:** fixture+dev+full · **Core/Stretch:** core · **Depends on:** EP-128 (Protocol Freezer page + amendments UI), EP-129 (Temporal holdout runner), EP-130 (Report engine A: Jinja2 → MD/HTML), EP-131 (Report engine B: PDF via Typst + export finalization), EP-132 (Model card + methods summary + executive summary templates), EP-133 (Disclosure-review tool), EP-134 (Runs & Provenance browser + Reports page / export gallery) · **Blocks:** EP-136 (Re-plan P8 (writes full P9, re-charters P10/P11))

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-127 (Re-plan P7) before execution.

## Context

Capstone per phase (D-8): the P8 case study proves the prospective-style loop end to end — freeze →
amend → holdout → report → review → gallery — on full data, and the phase also runs the **full-tier
regression**: every recorded full-tier run so far is re-executed and its headline aggregates must
reproduce, before P9 changes the lake (ED ingestion, EP-142). It follows the `docs/analyses`
convention from EP-32 (numbered case study, "What it deliberately does not claim", Reproduction
block) and the D-6 signature depth. Claim type predictive; retrospective, as every report states.
Full-tier work is a logged background job (foreground cap ~10 min).

## Scope sketch (refine at re-plan)

1. **`docs/analyses/06-prospective-inquiry-and-reporting.md`** — narrative: take signature #1's
   frozen protocol (EP-110), amend it in the Freezer page (EP-128) to unseal 2020–2022 with a stated
   reason, run `uv run --group dev mwh protocol holdout <new hash> --tier full` as a logged
   background job (EP-129, log `%MWH_DATA_ROOT%\runs\jobs\ep135-holdout.log`), render the bundle in
   MD/HTML/PDF (EP-130/131) plus model card, methods summary and executive summary (EP-132), review
   and approve it (EP-133, owner), show it in the gallery (EP-134); demo-tier screenshots of the
   Freezer, Runs and Reports pages (EP-60); every table/figure promoted only after
   `mwh disclose check`; Reproduction block with run ids, protocol hashes, snapshot ids.
2. **Full-tier regression `mwh verify regression --tier full`** — extend `verify.py` (EP-6) with a
   probe runner reading `tests/regression/probes.yaml`, seeded here from the completion notes of
   the recorded full-tier runs: EP-28 counts vs `validate.sql`, EP-37/38 concept count pins, EP-31
   tracer aggregates, EP-47 attrition of the signature cohorts, EP-56 mart latency ≤ 5 s, EP-68
   rates, capstone headline numbers (EP-53/73/89/100/126), EP-110–112 development-era metrics
   (seeded fits: tolerance 1e-6; otherwise CI overlap), EP-124 bench. Each probe = {run_id,
   artefact, expected aggregate, tolerance}; results → `runs/regression/<build_id>/summary.json` and
   benchmark-ledger lines (timings, peak RSS, disk delta). Launch first thing in the session as a
   background job (`uv run --group dev mwh verify regression --tier full`, log
   `%MWH_DATA_ROOT%\runs\jobs\ep135-regression.log`); if it outlives the session, EP-136 records the
   completion note — say so in this brief's note.
3. **Coverage check** — confirm categories 33, 36, 37, 38 meet the six-part definition of done in
   the README coverage table; list gaps for EP-136.
4. **Tests `tests/ep/test_ep135.py`** (`@pytest.mark.ep_135`, fixture): `probes.yaml` validates; the
   probe runner passes and fails correctly on synthetic probes; the case study's numbers reproduce
   from the cited run ids (aggregate goldens with sidecars).

## Out of scope

- New features in P8 modules → file follow-ups for EP-136; regression of P9+ layers → EP-146.
- Docs site → EP-160; case-study compilation → EP-161.

## Verification / acceptance (sketch)

- Case study exists with disclosure-checked artefacts and sidecars; the amended protocol hash chain
  is visible in the Freezer; `runs/holdouts.jsonl` shows exactly one full-tier look on the amended
  hash; the report bundle is approved in the review ledger and listed in the gallery.
- Regression job id / log path recorded; `summary.json` shows every probe passing (or a documented
  deviation with a DECISIONS addendum); timings in the benchmark ledger.
- `uv run poe test -m ep_135` and `uv run --group dev mwh verify EP-135` green on fixture.

## Parked → final-roadmap.md

- Continuous regression dashboards over the benchmark ledger (v2 BENCH-1); `mwh reproduce <run_id>`
  (v2 PROV-1).
