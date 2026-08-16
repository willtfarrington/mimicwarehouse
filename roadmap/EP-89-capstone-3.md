# EP-89 — Capstone #3

**Size:** M · **Tier:** fixture+dev+full · **Core/Stretch:** core · **Depends on:** EP-75 (Endpoints A: binary/continuous/count/ordinal), EP-76 (Endpoints B: time-to-event + recurrent), EP-77 (Inference & group comparison), EP-78 (Cluster bootstrap `boot` module), EP-79 (GLM suite A: families + tidy()), EP-80 (GLM suite B: interactions, nonlinear terms, diagnostics), EP-81 (Multilevel / repeated measures), EP-82 (Longitudinal trajectories (+ trajectory groups)), EP-83 (Event-sequence / care-pathway analysis), EP-84 (Repeated encounters / utilization), EP-85 (Time-series & forecasting), EP-86 (Exposure-response / treatment patterns), EP-87 (Missing-data strategies), EP-88 (Analysis pages wave 1) · **Blocks:** EP-90 (Re-plan P5 (writes full P6, re-charters P7))

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-74 (Re-plan P4) before execution.

## Context

Phase capstone (D-8): one case study that compiles the P5 representative workflows into a
narrative under the `docs/analyses/` convention set by EP-32 (`README.md` index, "What it
deliberately does not claim", Reproduction blocks; `00-staging-benchmark`, `01-concepts-and-qc`,
`02-eda-case-study` precede it). Two reading paths (D-1: DS/ML and clinical-informatics), a
claim-type label per section, the retrospective statement, and every promoted table / figure with a
`.disclosure.json` sidecar (D-40). Size M means **compile, do not analyse**: numbers come from
recorded full-tier run ids; a missing run is re-launched as a background job and cited, never
computed in the foreground.

## Scope sketch (refine at re-plan)

1. **`docs/analyses/03-outcomes-and-regression.md`** — sections mirroring the phase: endpoints
   (EP-75/76) → group comparison (EP-77) → GLM with splines / interactions (EP-79/80) → multilevel
   creatinine (EP-81) → lactate trajectories (EP-82) → care pathways (EP-83) → utilization (EP-84)
   → hemodynamic forecasting (EP-85) → antibiotic timing (EP-86) → missing-data sensitivity
   (EP-87); each with claim type, run id(s), snapshot ids, one or two suppressed tables / figures
   and its "What it deliberately does not claim".
2. **Reproduction blocks** per section (`uv run --group dev mwh …` from the frozen spec / run id)
   and the index entry in `docs/analyses/README.md`.
3. **Assets** promoted from `runs/<run_id>/` via EP-59 into `docs/analyses/assets/03/`, each with a
   sidecar; two Analysis-page screenshots (demo tier, EP-60).
4. **Coverage re-audit** of categories 7, 9–17 against the six-part definition of done → appendix
   checklist; gaps handed to EP-90.
5. **Tests** `tests/ep/test_ep89.py` (`@pytest.mark.ep_89`): links resolve; every asset has a
   sidecar; cited run ids exist in the ledger (`runs.duckdb` views / `mwh runs`); guard scan for
   id-band numbers.

## Out of scope

- New analyses or model fits; the report engine (EP-130) — this is hand-authored Markdown; PDF
  (EP-131); executive one-pager (EP-162); case-study compilation across phases (EP-161).

## Verification / acceptance (sketch)

- The case study and its assets exist at the paths above; `uv run --group dev mwh disclose check`
  passes on every asset; numbers reproduce from the recorded run ids; links resolve.
- `uv run poe test -m ep_89` and `uv run --group dev mwh verify EP-89` green.
- Any re-launched full-tier run id + timing recorded in the completion note.
