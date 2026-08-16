# EP-71 — Cross-sectional EDA module + page (Table 1)

**Size:** M · **Tier:** fixture+dev+full · **Core/Stretch:** core · **Depends on:** EP-47 (Cohort compiler, materialization, attrition, snapshot) · **Blocks:** EP-73 (Capstone #2: EDA case study + screenshots), EP-77 (Inference & group comparison)

## Context

Capability 4 (cross-sectional exploratory analysis) as a package module and page: counts,
frequencies, quantiles, robust summaries and standardized mean differences (SMDs) for a cohort,
optionally grouped — the "Table 1" every case study opens with. Inputs: materialised cohorts
with attrition and run records (EP-47), the wide stay-grain mart `marts.icustay_first_day`
(EP-55: age, gender, race, admission type, first-day vitals/labs, SOFA, Charlson, flags, LOS,
mortality) joined on the grain key, the dataset registry with column kinds (EP-64
`viz/datasets.py`), the shell/wrappers (EP-57/58), export (EP-59) and the run ledger (EP-35).
Design choices: no p-values by default (descriptive; EP-77 adds tests as an explicit option),
SMDs as the balance/difference summary, every statistic computed as aggregate SQL through
`safe_query` (means, SDs, `quantile_cont`, level counts) — rows never leave DuckDB. Missing
counts appear as a "Missing" row per variable; ages ≥ 89 show as 91 (footnote); small cells
badged in-app and suppressed on export (D-33). Representative workflow (D-5): the tracer cohort
`first_icu_stay_adults` grouped by in-hospital mortality.

## In scope

1. **Spec** `src/mimicwarehouse/stats/table1.py` — pydantic `Table1Spec(target: cohort
   id@version (joined to `marts.icustay_first_day`) | mart dataset, group_by: binary/
   categorical column | None, variables: list[Var(name, kind ∈ {continuous, categorical,
   binary}, summary ∈ {mean_sd, median_iqr, both}, label, unit)], smd=True, p_values=False,
   k=11)`; defaults in `stats/specs/table1_defaults.yaml` (age, gender, race_group,
   admission_type, insurance, era, first_service, first-day heart_rate/sbp/temperature/
   resp_rate/spo2 mean, lactate max, creatinine max, hemoglobin min, wbc max, sofa, charlson,
   vent/vasopressor/RRT flags, sepsis3, AKI stage ≥ 1, t2dm, los_icu_days, los_hospital_days,
   hospital_expire_flag).
2. **Builder** — `build(spec, tier, conn=None) -> Table1`: one aggregate SQL per group:
   continuous → `count`, `avg`, `stddev_samp`, `quantile_cont(x, [0.25, 0.5, 0.75])`, missing n;
   categorical/binary → level counts + denominators + missing; SMD between two groups (pooled-SD
   Cohen-style for continuous; `(p1−p2)/sqrt((p1(1−p1)+p2(1−p2))/2)` for binary; Yang–Dalton
   multi-level SMD from level proportions for categorical); > 2 groups → pairwise-max SMD;
   `p_values=True` raises `NotImplementedError("EP-77")`; small cells masked via
   `disclose.small_cells`; tidy long frame (`variable, level, group, n, stat, value`) +
   `render(fmt="md" | "html")` in the conventional layout ("Age, median [IQR]", "Sepsis-3,
   n (%)", "Missing" rows, SMD column, footnotes: units, 91 = ≥ 89, no p-values by design);
   inside a `run` context (spec hash, SQL, snapshot ids).
3. **CLI** — `mwh stats table1 <spec.yaml> --tier <t> [--out DIR]` → Markdown via
   `viz.export.export_table` (EP-59; claim type exploratory); prints the suppressed table only.
4. **Page** `app/pages/42_table1.py` (registry id `table1`, section Explore) — pick target
   (cohort registry / mart; accepts EP-62's `?cohort=` handoff), group variable, variables
   (defaults preloaded; add/remove; kind from the dataset registry), summary style; run
   in-process (single pass over the mart; on full use `ui.jobs.launch` only if the dev run
   took > 3 s); render via `safe_table` with |SMD| > 0.1 highlighted, "Missing" rows,
   footnotes, run id; export controls per EP-58 (disabled on dev/full).
5. **Representative workflow on full** — `mwh stats table1
   stats/specs/table1/tracer_by_mortality.yaml --tier full` (logged background job
   `%MWH_DATA_ROOT%\runs\jobs\ep71-table1-full.log`); record run id; exported md + sidecar pass
   `mwh disclose check`; page latency on full (`MWH_APP_RECORD_LATENCY=1`, ≤ 5 s); manifest
   entry `table1-tracer` on demo.
6. **Tests** `tests/ep/test_ep71.py` (`@pytest.mark.ep_71`): SMD formulas vs hand-computed
   values (continuous, binary, 3-level categorical); means/quantiles equal Polars on fixture;
   missing rows correct; `render("md")` contains every variable label and the SMD column;
   small-cell mask on a crafted level; `p_values=True` raises; AppTest (ui group) renders the
   table with the highlight; `ui_lint`; dev-marked; full run id + latency recorded.

## Out of scope

- Hypothesis tests, CIs on differences, multiplicity → EP-77; regression adjustment → EP-79.
- Journal-formatted Table 1 (Great Tables) → parked (v2 EDA-1, already listed).
- Distribution charts → EP-64–66; imputation → EP-87.

## Verification / acceptance

- `uv run poe test -m ep_71` and `uv run --group ui poe test -m ep_71` green (fixture; dev-
  marked); `uv run --group ui mwh verify EP-71` green (includes `ui_lint`).
- `uv run --group dev mwh stats table1 stats/specs/table1/tracer_by_mortality.yaml --tier dev`
  prints the suppressed Table 1 with SMDs and Missing rows.
- Full-tier run id and page latency recorded in the completion note; exported Markdown + sidecar
  pass `mwh disclose check`.
- Demo screenshot `table1-tracer-*.png` + sidecars.
