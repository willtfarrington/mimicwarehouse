# EP-99 — Survival / causal app pages

**Size:** M · **Tier:** fixture+dev+full · **Core/Stretch:** core · **Depends on:** EP-57 (App shell A (Streamlit multipage)), EP-93 (Competing risks (Aalen–Johansen; cause-specific; Fine–Gray via gpl optional)), EP-96 (PS / IPTW / matching / balance / standardization) · **Blocks:** EP-100 (Capstone #4)

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-90 before execution.

## Context

The interactive-visualization (capability 32) slice for P6: the "Survival/Causal" pages named in
DESIGN §16, built on the EP-57 shell (tier switcher, READ_ONLY cached connection, theme), the
EP-58 small-cell wrappers and row-view gate (D-32, D-33), the EP-59 export primitives, and the
`viz/` spec builders written by EP-91–96 so the same charts render in reports. Pages default to
the dev tier and must meet the ≤ 5 s target on full via cached run results (D-28). The `ui`
dependency group is isolated (Streamlit pins `pyarrow<25`), so all commands name it. Curves and
tables never show a stratum with n < 11 without the warning badge; export is disabled on dev/full.

## Scope sketch (refine at re-plan)

1. **`app/pages/survival.py`** ("Survival", registered through the EP-57 pages registry) —
   selectors: registered cohort (EP-46), EP-76 endpoint, grouping variable (phenotype or
   covariate), horizon; panels: KM curves + CI + at-risk strip (EP-91), Cox forest plot from a
   chosen covariate set + Schoenfeld panel, Aalen–Johansen CIF panel with a competing-event
   picker (EP-93); every fit runs under `mimicwarehouse.run` and the page links the run id.
2. **`app/pages/causal.py`** ("Causal") — exposure / outcome / confounder pickers over the EP-96
   workflow: PS overlap mirror histogram, Love plot (before/after), weight distribution + ESS,
   effect table (crude / IPTW / matched / standardized) with CIs, E-value line (EP-97 when
   present), a persistent "causal-with-assumptions" banner and positivity warnings.
3. **Latency + caching** — results cached per (tier, cohort, params) and served from the last
   recorded run with identical params; full-tier fits beyond a configurable cohort size are
   launched as background jobs with a "results pending" state instead of blocking the page; one
   full-tier page latency recorded in the benchmark ledger (≤ 5 s target, D-28).
4. **Screenshots + tests** — demo-tier screenshots via the EP-60 tooling for EP-100;
   `tests/ep/test_ep99.py` (`@pytest.mark.ep_99`) with Streamlit `AppTest` on the fixture tier:
   both pages render, a crafted small stratum shows the small-cell badge, export controls are
   disabled on dev/full, and the run-id link is present.

## Out of scope

- Analysis pages wave 1 (inference / GLM diagnostics) → EP-88; ML pages → EP-125.
- Protocol Freezer page → EP-128; Runs & Reports pages → EP-134.
- Owner row-level views on these pages (aggregate-only by design; single-stay views → EP-67).

## Verification / acceptance (sketch)

- `uv run --group ui poe test -m ep_99` green on fixture; `uv run --group dev mwh verify EP-99` green.
- Observable behaviour on the dev tier with `uv run --group ui mwh app`: KM/CIF/forest/Love panels
  render for the representative workflows of EP-91/93/96; small-cell badge appears; export disabled.
- One full-tier page latency recorded (≤ 5 s); demo-tier screenshots saved at the EP-60 paths with
  `.disclosure.json` sidecars.

## Parked → final-roadmap.md

- Interactive landmark/TDC explorer and target-trial designer page — trigger: after EP-128
  (Freezer) exists.
