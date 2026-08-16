# EP-65 — Explorer B: linked-brush distributions

**Size:** M · **Tier:** fixture+dev+full · **Core/Stretch:** core · **Depends on:** EP-64 (Explorer A: server-side aggregation service + VegaFusion) · **Blocks:** EP-73 (Capstone #2: EDA case study + screenshots)

## Context

Linked brushing is the one Explorer feature the owner called essential (D-21; DESIGN §16:
Altair selections → server-side DuckDB re-aggregation). EP-64 delivered the pieces: the dataset
registry, `AggRequest`/`compile`/`run`, histogram/bar spec builders, VegaFusion, the Explorer
page with global filters and a *Linked* stub. Streamlit ≥ 1.35 returns Altair selections
through `st.altair_chart(chart, on_select="rerun", key=…)`; each rerun therefore maps the
selection payload to `AggRequest` filters and re-aggregates every other panel in DuckDB — no
rows ever reach the browser. This brief builds that loop, a small-multiples grid, a brushed-n
badge under the small-cell rule (EP-58 wrappers), and a "brush → cohort" handoff to the Cohort
Builder (EP-62). Round-trip latency ≤ 5 s on full for six panels (D-28). Streamlit's `AppTest`
cannot simulate chart selections, so the payload mapping is unit-tested and the interactive
loop is checked manually on dev and recorded.

## In scope

1. **Selection plumbing** (`src/mimicwarehouse/viz/linked.py`) — `brush_hist(df, var, name)`
   (Altair histogram over pre-binned data with `alt.selection_interval(encodings=["x"],
   name=name)`), `click_bar(df, cat, name)` (`selection_point(fields=[cat])`),
   `selection_to_filters(payload, dataset, panel) -> list[Filter]` mapping Streamlit's
   `event.selection` dict (interval → `Range` on the binned column; point → `In` on levels) to
   EP-64 filters; `BrushState` in `st.session_state["mwh.explorer.brush"]`: ordered mapping
   panel_id → Filter, `combined()` (AND), `clear()`, `drop(panel_id)`.
2. **Linked tab** in `app/pages/30_explorer.py` — user picks 2–6 variables (default
   `admission_age`, `heart_rate_mean`, `sbp_min`, `lactate_max`, `sofa`, `admission_type`) →
   grid of panels; each panel's `AggRequest` = global filters + brushes from *other* panels;
   the panel itself renders its full extent (muted) with the brushed subset overlaid (accent):
   one query per panel grouped by bin × `in_brush` boolean; brushed-n badge (small-cell warning
   if < 11) and total n; "Clear brushes"; optional colour by `hospital_expire_flag`/`era` with
   normalized toggle; each panel rendered via `safe_altair(..., on_select="rerun", key=…)`.
3. **Round-trip cost** — run all panel queries on the cached connection in one pass; show the
   round-trip wall time (brush → all panels rendered); target ≤ 5 s on full for six panels over
   `icustay_first_day` and over `hourly_population`-backed hour variables; if `icustay_hourly`
   panels miss the target, restrict hourly panels to `hourly_population` and note it (dated
   DESIGN note); add `explorer_linked_6` to `marts/bench_queries.yaml`.
4. **Brush → cohort handoff** — button converts `BrushState.combined()` into a `CohortSpec`
   YAML fragment (`inclusion` criteria on mart columns via a `MartCriterion`; if EP-46's spec
   lacks it, emit the nearest supported criteria and show a caveat), stores it in
   `st.session_state["mwh.handoff.cohort_yaml"]` and `st.switch_page` to the Cohort Builder,
   which loads it into the form (small addition to EP-62's page).
5. **Tests** `tests/ep/test_ep65.py` (`@pytest.mark.ep_65`, ui group): `selection_to_filters`
   for crafted interval and point payloads; AND combination, `drop`, `clear`; the `in_brush`
   overlay query's brushed + unbrushed counts sum to the panel's n on fixture; the handoff YAML
   validates as a `CohortSpec` (or produces the documented caveat); AppTest renders N panels and
   the clear button; `ui_lint`; dev-marked run; manual interactive check on dev + full
   round-trip latency recorded in the completion note; manifest entry `explorer-linked` (demo).

## Out of scope

- Correlations, 2-D heatmaps, cross-tabs, conditional summaries → EP-66.
- Cohort compilation itself → EP-47/62; statistical comparison of brushed vs unbrushed → EP-77.
- Crossfilter over > 10⁷ points client-side → parked (v2 UI-2/UI-3).

## Verification / acceptance

- `uv run --group ui poe test -m ep_65` green (fixture; dev-marked); `uv run --group ui mwh
  verify EP-65` green (includes `ui_lint`).
- On dev: brushing one histogram re-aggregates the others; the brushed-n badge updates and
  warns below 11; clear resets; "brush → cohort" opens the Cohort Builder with the fragment.
- Full-tier round trip for six panels recorded (≤ 5 s) as a `page_latency` entry and in the
  completion note; `explorer_linked_6` passes in `mwh bench queries --tier full`.
- Demo screenshot `explorer-linked-*.png` + sidecars.
