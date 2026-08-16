# EP-66 — Explorer C: heatmaps, correlations, cross-tabs, conditional summaries

**Size:** M · **Tier:** fixture+dev+full · **Core/Stretch:** core · **Depends on:** EP-64 (Explorer A: server-side aggregation service + VegaFusion) · **Blocks:** EP-73 (Capstone #2: EDA case study + screenshots)

## Context

Completes the Explorer (DESIGN §16: distributions, heatmaps/correlations, cross-tabs) with the
relationship views capability 4 (cross-sectional EDA) needs beside Table 1 (EP-71). It extends
EP-64's `AggRequest` with correlation and quantile measures and adds spec builders for
correlation/density heatmaps, cross-tabs and quantile-based conditional summaries — all computed
in DuckDB, all rendered from pre-aggregated frames (never raw points), all under the EP-58
small-cell rule (pairwise n and cross-tab cells < 11 badged). Descriptive only: no p-values or
tests here (EP-77 owns inference). Drill-down reuses EP-65's `BrushState` so a clicked cell or
category becomes a filter for the other views. Latency ≤ 5 s on full for a 30-variable
correlation matrix over `icustay_first_day` (D-28).

## In scope

1. **Measures** — extend `src/mimicwarehouse/viz/agg.py`: `corr(x, y, method="pearson" |
   "spearman")` (Spearman via per-column `rank()` in a CTE then `corr()`), `pairwise_n(x, y)`,
   `quantiles(col, [0.05, 0.25, 0.5, 0.75, 0.95])`; `CorrRequest(dataset, columns ≤ 40,
   filters, method)` compiling to one SQL with all pairs → long result (x, y, r, n) with n < 11
   pairs masked; `CrosstabRequest(dataset, row, col, facet=None, filters)` → counts, row %,
   col %, margins; `ConditionalRequest(dataset, y, x | Bin(x), filters)` → n, mean, p05, p25,
   p50, p75, p95 per level/bin.
2. **Spec builders** — `src/mimicwarehouse/viz/heatmap.py`: `corr_heatmap(df)` (diverging EP-5
   palette, values annotated, masked pairs blank), `density_heatmap(df_binned, log=False)`,
   `crosstab_heatmap(df)`; `src/mimicwarehouse/viz/summary.py`: `conditional_box(df_q)`
   (rule p05–p95, bar p25–p75, tick p50 from precomputed quantiles) and `crosstab_table(df)`
   (formatted counts + % with margins).
3. **Relationships tab** in `app/pages/30_explorer.py` with sub-tabs: *Correlation* (pick
   numeric variables → heatmap; clicking a cell drills to the pair's 2-D density heatmap via
   `on_select`); *2-D density* (x, y numeric; bin steps); *Cross-tabs* (row var, col var,
   optional facet; counts + row %/col %; small cells badged; no chi-square); *Conditional
   summaries* (y numeric by x categorical or binned → table + `conditional_box`; selecting a
   category pushes a filter into EP-65's `BrushState`). All via EP-58 wrappers with the SQL
   expander and latency caption from EP-64.
4. **Latency + bench + screenshots** — full: 30-variable Pearson + Spearman matrices, a
   `gender × era × admission_type` cross-tab and `los_icu_days` conditional quantiles by
   `first_service` each ≤ 5 s (`MWH_APP_RECORD_LATENCY=1`; completion note); add
   `explorer_corr30`, `explorer_crosstab`, `explorer_condq` to `marts/bench_queries.yaml`;
   manifest entries `explorer-correlation`, `explorer-crosstab` on demo.
5. **Tests** `tests/ep/test_ep66.py` (`@pytest.mark.ep_66`, ui group): Pearson and Spearman on
   crafted fixture columns match numpy/scipy within 1e-6; pairwise-n masking; cross-tab margins
   equal totals and % rows sum to 100; conditional quantiles monotone and equal Polars quantiles
   on fixture; AppTest renders each sub-tab; `ui_lint`; dev-marked; full latencies recorded.

## Out of scope

- Inference (chi-square, tests, CIs on correlations) → EP-77; regression → EP-79/80.
- Hierarchical clustering / reordering of the correlation matrix → parked below; PCA/embeddings
  → EP-116.
- Timelines → EP-67; Table 1 → EP-71; missingness heatmaps → EP-72.

## Verification / acceptance

- `uv run --group ui poe test -m ep_66` green (fixture; dev-marked); `uv run --group ui mwh
  verify EP-66` green (includes `ui_lint`).
- On dev: correlation heatmap → click → density; cross-tab with badges; conditional summaries
  drill into the brush state.
- Full-tier latencies for the four representative queries recorded (≤ 5 s) and the three bench
  queries pass in `mwh bench queries --tier full`.
- Demo screenshots `explorer-correlation-*.png`, `explorer-crosstab-*.png` + sidecars.

## Parked → final-roadmap.md

- Clustered ordering of correlation matrices (seriation) and partial correlations — trigger:
  EP-116 or a case study needs structure discovery beyond visual inspection.
