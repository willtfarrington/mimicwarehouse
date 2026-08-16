# EP-88 — Analysis pages wave 1

**Size:** M · **Tier:** fixture+dev+full · **Core/Stretch:** core · **Depends on:** EP-57 (App shell A (Streamlit multipage)), EP-79 (GLM suite A: families + tidy()), EP-80 (GLM suite B: interactions, nonlinear terms, diagnostics) · **Blocks:** EP-89 (Capstone #3)

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-74 (Re-plan P4) before execution.

## Context

Capability category 32 (*Interactive visualization*): the "Analysis" section of the Lab app
(DESIGN §16) — an Inference page over `stats/inference.py` (EP-77; precedes this brief in
execution order — the re-plan should add it to Depends-on) and a GLM page over `stats/glm.py`
(EP-79/80). Runs in the isolated `ui` group (Streamlit pins `pyarrow<25`), on the EP-57 shell with
the EP-58 small-cell components and row-view gate, pages default to the dev tier (D-28), the
READ_ONLY cached connection, `viz/` Altair specs, run records via EP-35 and exports via EP-59.
Model fits are not marts: page load and aggregates must meet ≤ 5 s on full; a full-tier fit runs
only after explicit confirmation and its time is recorded separately. Screenshots on the demo tier
via EP-60.

## Scope sketch (refine at re-plan)

1. **`app/pages/analysis_inference.py`** — cohort picker (EP-46/47 registry), group variable,
   outcomes (EP-75 endpoint registry + mart columns), method auto / choice, CI level, permutation
   toggle (bounded `n_perm`), FDR toggle → tidy table with small-cell badges + forest plot; "record
   run" (EP-35) and export (EP-59; disabled on dev / full unless the disclosure check passes).
2. **`app/pages/analysis_glm.py`** — formula builder (outcome from endpoints; covariates from
   cohort / mart columns; family; cluster column; interactions; `rcs()` terms with knots), fit on
   dev by default (full requires confirmation and shows the last recorded time), tidy + glance
   tables, diagnostics tab (binned residuals, partial-effect curves, calibration by decile,
   dispersion note from EP-80 spec builders), side-by-side model comparison (AIC / BIC / LR), run
   record link.
3. **Shell integration** — tier switcher honoured; no per-row tables anywhere; page-latency
   entries via the EP-56 harness (page load, one aggregate, one dev fit) into the benchmark ledger.
4. **Screenshots** — two demo-tier images via EP-60 for docs, passed through `mwh disclose check`.
5. **Tests** `tests/ep/test_ep88.py` (`@pytest.mark.ep_88`): Streamlit `AppTest` smoke on fixture
   (pages render; controls yield a tidy table; a crafted small group shows the badge); guard test
   that no rendered dataframe carries identifier columns.

## Out of scope

- Survival / causal pages → EP-99; ML pages → EP-125; Runs & Reports pages → EP-134.
- Explorer linked brushing → EP-64–66; report generation → EP-130; Protocol Freezer → EP-128.

## Verification / acceptance (sketch)

- `uv run --group ui mwh app` shows both pages on dev with the behaviours above; `uv run poe test
  -m ep_88` and `uv run --group dev mwh verify EP-88` green on fixture (+ dev).
- One full-tier page-load / aggregate latency ≤ 5 s recorded in the benchmark ledger; the full
  fit time recorded (any duration) in the completion note.
- Demo-tier screenshots exist at the documented paths with `.disclosure.json` sidecars.
