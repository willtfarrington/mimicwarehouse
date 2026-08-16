# EP-97 — Sensitivity analyses

**Size:** M · **Tier:** fixture+dev+full · **Core/Stretch:** core · **Depends on:** EP-96 (PS / IPTW / matching / balance / standardization) · **Blocks:** EP-100 (Capstone #4)

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-90 before execution.

## Context

Capability 19 continues: every causal-with-assumptions estimate produced by EP-96 needs a
sensitivity section before the capstone can cite it. Packet seeds: E-value, negative controls,
trimming. This brief adds `causal/sensitivity.py` and re-runs the EP-96 transfusion workflow
under alternative specifications; it re-implements nothing from EP-96 and calls the EP-43
`disclose` module for every exported table (D-33, D-40). Claim labels are unchanged by this brief
— sensitivity analyses qualify a claim, they do not upgrade it.

## Scope sketch (refine at re-plan)

1. **E-values** — `evalue(estimate, ci, scale="RR|OR|HR|RD")` per VanderWeele & Ding, with the
   OR/HR-to-RR conversions for common and rare outcomes, for the point estimate and the CI limit
   closest to the null; a tipping-point table (bias strength needed to move the CI across the null).
2. **Negative controls** — `negative_control()` re-runs the EP-96 pipeline with a
   negative-control outcome that cannot be caused by the exposure; representative: number of
   hospital admissions in the year before the index admission (pre-index utilization, computed
   within-patient so the date shift is harmless); optional negative-control exposure; the harness
   refuses an outcome whose window ends after the index time (leakage guard).
3. **Specification grid** — `sensitivity_grid()` over PS trimming (none / 1–99 / 5–95 / 10–90
   percentiles / overlap weights), caliper (0.1 / 0.2 / 0.5 SD), weight truncation, and
   leave-one-confounder-out sets → a specification-curve table + Altair spec builder in `viz/`;
   each cell records its own run id (EP-35).
4. **Representative workflow** — apply items 1–3 to the EP-96 transfusion → in-hospital mortality
   estimate on fixture, dev and full (logged background job): E-value for the primary estimate and
   its CI, negative-control outcome estimate, specification curve; a "sensitivity" section is
   appended to the EP-96 report artifact via EP-59, with interpretation guidance and the unchanged
   claim label.
5. **Tests** — `tests/ep/test_ep97.py` (`@pytest.mark.ep_97`): E-value closed-form checks
   (RR = 1 → E = 1; textbook values); OR/HR conversions; grid runner is deterministic and records
   one run id per cell; the negative-control harness refuses a post-index outcome window.

## Out of scope

- Quantitative bias analysis beyond E-values (Rosenbaum bounds, probabilistic bias analysis) →
  parked (final-roadmap § 19, CAUS-3).
- Instrumental variables / regression discontinuity → parked (CAUS-2).
- Known-truth simulation of the estimators → EP-98; capstone narrative → EP-100.

## Verification / acceptance (sketch)

- `uv run poe test -m ep_97` green on fixture (+dev); `uv run --group dev mwh verify EP-97` green.
- Full-tier run ids (grid cells included) + wall time recorded in the completion note; the
  extended EP-96 report artifact passes `uv run --group dev mwh disclose check <path>`.
- The report shows E-value, negative-control result and specification curve with no cell < 11.

## Parked → final-roadmap.md

- Rosenbaum Γ bounds and probabilistic bias analysis — trigger: reviewer request (CAUS-3).
