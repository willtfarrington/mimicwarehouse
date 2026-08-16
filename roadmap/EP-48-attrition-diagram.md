# EP-48 — Attrition diagram renderer

**Size:** S · **Tier:** fixture+dev · **Core/Stretch:** core · **Depends on:** EP-47 (Cohort compiler, materialization, attrition, snapshot), EP-43 (Disclosure primitives (`disclose` module)) · **Blocks:** EP-54 (Re-plan P3), EP-62 (Cohort Builder page)

## Context

Every cohort deserves a STROBE-style attrition (flow) diagram, and it must be safe to paste into a
report, a docs page or the Cohort Builder page. EP-47 materializes the attrition table (step,
label, n_units, n_subjects, dropped) and exposes it through `cohort.attrition(...)`, which already
applies `disclose.suppress(mode="chain")` (EP-43). This brief renders that table as Mermaid
(primary — GitHub and Streamlit both render it) with an Altair fallback (DESIGN §9), stores the
artifacts in the run directory, and proves with `mwh disclose check` that the outputs carry no
exact small cell (D-33, D-40). The renderer lives in `src/mimicwarehouse/cohort/attrition.py`;
Vega-Lite specs are plain JSON that EP-64+ `viz/` may later absorb. No data beyond the attrition
aggregates is touched.

## In scope

1. **Mermaid renderer** (`src/mimicwarehouse/cohort/attrition.py`) — `render_mermaid(df,
   title=None, direction="TD") -> str`: one node per step (`"Step k — label<br/>n = X units ·
   Y subjects"`), a side node per drop (`"Excluded: n = Z"`), suppressed cells rendered exactly as
   `disclose.render_cell` (`<11`, `~` bands) — the renderer never sees raw counts because it takes
   the suppressed frame from `cohort.attrition(...)`; a footer node with `id@version`, tier and
   run id; deterministic node ids; escaping of `"`/`<>` in labels.
2. **Altair fallback** — `render_altair(df) -> alt.Chart` (horizontal funnel bar of `n_units`
   per step with drop annotations; suppressed values omitted, not zeroed; theme from `theme.py`
   EP-5) and `to_vegalite(df) -> dict`; PNG export via `vl-convert-python` (BSD-3; add to the
   `core` group if not already present) so reports can embed a static image.
3. **Artifacts + CLI** — `save_attrition(run_id | id@version, tier, out_dir=None)` writes
   `runs/<run_id>/figures/attrition.mmd`, `attrition.vl.json`, `attrition.png` and the suppressed
   `attrition.csv`; `mwh cohort attrition <id@version> --tier <t> --format mermaid|altair|all
   [--out <dir>]` prints Mermaid to stdout or writes files; each written file passes
   `disclose.check` (called inside `save_attrition`; refuse to write otherwise). `save_attrition`
   also writes `attrition.md` (suppressed table + footer with `Claim type: exploratory (cohort
   description)`, the retrospective statement, id@version, tier, run id) through the same
   `disclose.check` path.
4. **Tests + docs** (`tests/ep/test_ep48.py`, `@pytest.mark.ep_48`; fixture, `dev`) — a crafted
   attrition frame with a drop of 4 renders `<11` and no literal `4` in the `.mmd`; a clean chain
   renders exact counts; Mermaid output parses (balanced arrows, unique ids; a minimal grammar
   check); the Vega-Lite JSON has only the aggregate columns; every artifact passes
   `disclose.check`; on dev, `mwh cohort attrition first_icu_adults@1.0.0 --tier dev --format all`
   writes the four files. `docs/methods/cohorts.md` gains an "attrition diagram" section with a
   rendered example from the fixture tier (synthetic data, rendered through the same suppression
   so it shows no cell < 11; `mwh disclose check` on the docs file passes).

## Out of scope

- Cohort Builder page embedding (`st.markdown` Mermaid / `st.altair_chart`) → EP-62.
- Report engine integration → EP-130; export gallery → EP-134.
- anywidget/D3 diagram components → parked (`final-roadmap.md` § 8–10).

## Verification / acceptance

- `uv run poe test -m ep_48` green on fixture and dev; `uv run --group dev mwh verify EP-48` green.
- `uv run --group dev mwh cohort attrition first_icu_adults@1.0.0 --tier dev --format all --out
  %MWH_DATA_ROOT%\runs\<run_id>\figures` writes `.mmd`, `.vl.json`, `.png`, `.csv` and
  `attrition.md` (claim-type label + retrospective statement in the footer), and
  `uv run --group dev mwh disclose check` on that directory exits 0.
- The fixture-tier example diagram in `docs/methods/cohorts.md` renders on GitHub (Mermaid block).
