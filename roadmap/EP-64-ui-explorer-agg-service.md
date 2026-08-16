# EP-64 — Explorer A: server-side aggregation service + VegaFusion

**Size:** M · **Tier:** fixture+dev+full · **Core/Stretch:** core · **Depends on:** EP-57 (App shell A (Streamlit multipage)), EP-56 (Latency marts B: hourly bins + <=5 s benchmark) · **Blocks:** EP-65 (Explorer B: linked-brush distributions), EP-66 (Explorer C: heatmaps, correlations, cross-tabs, conditional summaries), EP-73 (Capstone #2: EDA case study + screenshots)

## Context

The Explorer is the visualization centrepiece (D-21: Altair/Vega-Lite + VegaFusion primary,
linked brushing essential; DESIGN §16: Altair selections → server-side DuckDB re-aggregation).
Its rule is simple: the browser only ever receives aggregates (bins × counts, quantiles), never
rows. This brief builds the aggregation service behind it and the page skeleton; EP-65 adds
linked brushing and EP-66 the relationship views. Inputs exist: the marts (`icustay_first_day`,
`icustay_hourly`, `hourly_population`, `itemid_summary`, EP-55/56) with their YAML column
dictionaries, the bench harness (`mwh bench queries`, EP-56), the shell's single query path
(`ui.conn.query` → `safe_query`, EP-57), the EP-58 wrappers, and materialised cohorts (EP-47).
VegaFusion (BSD-3) evaluates spec-level transforms server-side so inline chart data stays small;
DuckDB does the heavy aggregation. Latency target ≤ 5 s on full for the default views (D-28).
Age bands must place 91 (= ≥ 89) in the top band; time is relative (`hour_bin`); `era` is
`anchor_year_group`. If EP-56 left the full hourly build unverified, this brief records it.

## In scope

1. **Dataset registry** (`src/mimicwarehouse/viz/datasets.py`) — `Dataset(id, title, grain,
   table, key, columns: dict[str, ColumnInfo(kind ∈ {numeric, categorical, binary, ordinal,
   hour}, unit, label, levels)])` for `icustay_first_day`, `icustay_hourly`,
   `hourly_population`, `itemid_summary`, plus "cohort × first_day" (a materialised cohort
   joined on `stay_id`); column info from the marts spec YAML (EP-55/56) + `meta.columns`;
   categorical levels loaded lazily (`SELECT DISTINCT` capped at 50) via `ui.conn.query`.
2. **Aggregation service** (`src/mimicwarehouse/viz/agg.py`) — pydantic `AggRequest(dataset,
   measures, group_by, filters, cohort=None, cap=5000)`; `Measure` ∈ count,
   count_distinct(key), mean(col), sum, min, max, quantiles(col, qs) (`quantile_cont`);
   `GroupBy` ∈ column | `Bin(column, step | maxbins)` (`floor(x/step)*step`) | `Hour(column)`;
   `Filter` ∈ In, Range, Eq, IsNull; `compile(req) -> (sql, params)` validates every column and
   level against the registry (no free strings reach SQL; literals parametrised); `run(req,
   tier) -> AggResult(df, n_total, small_cell_mask, sql, wall_s, truncated)` via `ui.conn.query`
   + `st.cache_data`; SQL text deterministic (golden-tested).
3. **Spec builders + VegaFusion** (`src/mimicwarehouse/viz/distributions.py`, `viz/__init__.py`)
   — `histogram(df_binned, x, count, color=None, normalize=False)`, `bar(df, cat, count,
   color=None)`, `population_band(df, hour, p05, p25, p50, p75, p95)`; Altair over
   pre-aggregated frames with the EP-5 theme; `viz/__init__.py` enables
   `alt.data_transformers.enable("vegafusion")` when importable (add `vegafusion` to the `ui`
   group; `vl-convert-python` already in core from EP-59). Policy comment at the top of
   `agg.py`: DuckDB aggregates (AggRequest) → VegaFusion for residual spec transforms → never
   raw rows.
4. **Explorer page skeleton** `app/pages/30_explorer.py` (registry id `explorer`, section
   Explore) — sidebar filters (era multiselect, gender, age band 18–39/40–64/65–79/80+, first
   ICU stay flag, cohort membership) → `AggRequest.filters`; dataset + searchable variable picker
   (label, unit); tab *Distributions*: histogram (numeric) or bar (categorical) for the chosen
   variable, optional colour by `hospital_expire_flag`/`era`, normalized toggle; n badge with
   small-cell warning; "SQL" expander (`st.code`) and latency caption; tabs *Linked* and
   *Relationships* as stubs for EP-65/66. All rendering through EP-58 wrappers.
5. **Latency + bench + screenshots** — default view (histogram of `heart_rate_mean` on
   `icustay_first_day`) and `hourly_population` band chart ≤ 5 s on full
   (`MWH_APP_RECORD_LATENCY=1`; completion note); add `explorer_default_hist`,
   `explorer_population_band` to `marts/bench_queries.yaml`; a miss is fixed by more
   pre-aggregation in marts (dated DESIGN note), never by client-side sampling; verify/record
   the EP-56 full hourly build if still open; manifest entry `explorer-distributions` on demo.
6. **Tests** `tests/ep/test_ep64.py` (`@pytest.mark.ep_64`, ui group): golden SQL for three
   requests; unknown column → `ValueError`; a crafted filter value `' OR 1=1 --` is passed as a
   parameter and the SQL text is unchanged; bin counts sum to `n_total` on fixture; `cap=10` →
   10 rows + `truncated=True`; small-cell mask on a crafted bin; VegaFusion transformer active
   under the ui group; AppTest renders the histogram and n badge; `ui_lint`; dev-marked run;
   full latencies recorded.

## Out of scope

- Linked brushing across panels → EP-65; correlations, heatmaps, cross-tabs, conditional
  summaries → EP-66; timelines → EP-67.
- Rates/subgroups/Table 1 statistics → EP-68/70/71 (the Explorer is descriptive counts only).
- Datashader/Mosaic big-data lanes → parked (v2 UI-2/UI-3, already listed).

## Verification / acceptance

- `uv run --group ui poe test -m ep_64` green (fixture; dev-marked); `uv run --group ui mwh
  verify EP-64` green (includes `ui_lint`).
- On dev: pick dataset/variable/filters → histogram/bar renders with n badge and SQL expander.
- Full-tier latencies for the two default views recorded (≤ 5 s); `mwh bench queries --tier
  full` shows the two new queries passing.
- If EP-56's full hourly build was open: its counts/timing recorded in EP-56's completion note.
- Demo screenshot `explorer-distributions-*.png` + sidecars.
