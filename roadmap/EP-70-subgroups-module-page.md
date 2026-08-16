# EP-70 — Descriptive stratified/subgroup module + page

**Size:** M · **Tier:** fixture+dev+full · **Core/Stretch:** core · **Depends on:** EP-68 (Prevalence/incidence/event-rate module) · **Blocks:** EP-73 (Capstone #2: EDA case study + screenshots)

## Context

Capability 6 (stratified and subgroup analysis), descriptive form: an outcome or measure by
demographic, clinical, severity, service and era strata with n, CIs, stratum overlap and
missingness of the stratifying variables — no p-values or interaction tests (those are EP-77 /
EP-80). It reuses EP-68's proportion CIs and run plumbing, the marts (`icustay_first_day`
supplies age, gender, race, admission type, first service, era, SOFA and phenotype flags;
EP-55), materialised cohorts (EP-47), `viz/forest.py` (EP-69), the EP-57 shell and EP-58
wrappers. Representative workflow (D-5): in-hospital mortality among `first_icu_stay_adults` by
gender × age band × era × sepsis-3 × first-day SOFA band. Race is shown only as a documented
collapse to ≤ 6 groups; small strata are badged in-app and suppressed on export (D-33); ages
≥ 89 (= 91) fall in the top age band; era is `anchor_year_group`.

## In scope

1. **Module** `src/mimicwarehouse/stats/subgroups.py` — pydantic `SubgroupSpec(target:
   cohort id@version | mart dataset, outcome: binary column/phenotype flag (e.g.
   `hospital_expire_flag`, `sepsis3`) | continuous column (e.g. `los_icu_days`), strata:
   list of dims, k=11)`; dims: demographic (`gender`, `age_band`, `race_group` — collapse map
   in `stats/specs/race_groups.yaml`, `insurance`), clinical (`sepsis3`,
   `kdigo_max_stage_first_day ≥ 1`, `t2dm`), severity (`sofa` bands 0–1 / 2–5 / 6–9 / ≥ 10,
   `admission_type`, `vent_invasive_first_day`), service (`first_service`), unit
   (`first_careunit`), era. `estimate(spec, tier, conn=None) -> SubgroupResult`: per level —
   n, N, proportion + Wilson CI (EP-68 helpers) or mean/SD/median/IQR (quantiles server-side);
   overall row; overlap matrix between the levels of the selected dims (intersection counts,
   small cells masked); missingness of each stratifying variable (n missing, %); band
   cut-points in `definitions`; all via `safe_query` inside a `run` context; CLI `mwh stats
   subgroups <spec.yaml> --tier <t> [--out DIR]`.
2. **Charts** — reuse `viz/forest.py` (EP-69) grouped by dim with a reference line at the
   overall estimate; overlap via `viz/heatmap.crosstab_heatmap` (EP-66); missingness bar via
   `viz/qc.null_pct_bars` (EP-61).
3. **Page** `app/pages/41_subgroups.py` (registry id `subgroups`, section Explore) — pick
   target (cohort registry / mart), outcome, dims (multi, ≤ 5); run in-process on demo/dev and
   on full (single-pass GROUP BYs on marts; if the dev run took > 3 s use `ui.jobs.launch`);
   render forest, table (n, N, estimate, CI), overlap matrix (badged), missingness table,
   definitions, run id via EP-58 wrappers; accept the EP-69 handoff (`?spec=`).
4. **Representative workflow on full** — `mwh stats subgroups
   stats/specs/subgroups/mortality_first_icu_by_strata.yaml --tier full` as a logged background
   job (`%MWH_DATA_ROOT%\runs\jobs\ep70-subgroups-full.log`); record run id; export forest +
   table via `viz.export` (EP-59; claim type exploratory) and check; page latency on full
   recorded (`MWH_APP_RECORD_LATENCY=1`, ≤ 5 s); manifest entry `subgroups-forest` on demo.
5. **Tests** `tests/ep/test_ep70.py` (`@pytest.mark.ep_70`): per-stratum proportions equal a
   Polars recomputation on fixture; the overall row equals `stats.rates` on the same target;
   overlap matrix symmetric with diagonal == level counts; missingness counts; band cut-points
   (`admission_age = 91` → 80+; SOFA 10 → ≥ 10); race collapse covers every fixture level;
   AppTest (ui group) renders forest + tables; `ui_lint`; dev-marked; full run id + latency
   recorded.

## Out of scope

- Tests of heterogeneity, interaction, multiplicity → EP-77/EP-80/EP-113.
- Adjusted/matched subgroup effects → EP-96; standardised rates → parked (EP-68 item).
- Table 1 → EP-71; missingness views → EP-72.

## Verification / acceptance

- `uv run poe test -m ep_70` and `uv run --group ui poe test -m ep_70` green (fixture; dev-
  marked); `uv run --group ui mwh verify EP-70` green (includes `ui_lint`).
- On dev: choose outcome + dims → forest with reference line, table, overlap, missingness.
- Full-tier run id (background job log path) and page latency recorded in the completion note;
  exported artifacts pass `mwh disclose check`.
- Demo screenshot `subgroups-forest-*.png` + sidecars.
