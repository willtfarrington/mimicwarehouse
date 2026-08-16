# EP-72 — Missing-data views

**Size:** M · **Tier:** fixture+dev+full · **Core/Stretch:** core · **Depends on:** EP-45 (Measurement-process summaries) · **Blocks:** EP-73 (Capstone #2: EDA case study + screenshots), EP-87 (Missing-data strategies)

## Context

Capability 7 (missing-data and measurement-process analysis), the descriptive/visual half:
missingness patterns over a cohort's variables, measurement-frequency views, and the
structural-vs-unmeasured distinction that EP-45 formalised (measurement frequency by ICU
hour/day for top itemids, structural absence vs unmeasured, informative-presence summaries in
`qc/`). It reads the wide mart `marts.icustay_first_day` (EP-55) and materialised cohorts
(EP-47), reuses the EP-64 dataset registry, `viz/heatmap` (EP-66), the shell/wrappers
(EP-57/58), export (EP-59) and the run ledger (EP-35). EP-87 later compares handling strategies
(complete-case vs indicator vs MICE) on top of these views. All statistics are aggregate SQL
through `safe_query`; patterns are counted, never listed per unit; counts < 11 badged in-app and
suppressed on export (D-33). Absence in MIMIC is often informative (a lactate is drawn when
someone worries) — the views make that visible without claiming causation. Representative
workflow (D-5): first-day features of `first_icu_stay_adults`; lactate, troponin and BNP
measurement rates by era and mortality.

## In scope

1. **Module** `src/mimicwarehouse/stats/missing.py` — pydantic `MissingSpec(target: cohort
   id@version | mart dataset, variables (default: first-day feature columns), group_by ∈ {None,
   era, first_careunit, hospital_expire_flag}, top_k_patterns=15, k=11)`; `profile(spec, tier,
   conn=None) -> MissingResult` with: (a) per-variable missing n/% (per group); (b) top-K
   missingness patterns — pattern string of present/absent bits over ≤ 30 variables built in
   SQL (`CASE WHEN x IS NULL THEN '0' ELSE '1' END` concatenated) and `GROUP BY`, counts < 11
   masked; (c) pairwise co-missingness (n both missing, Jaccard) in one aggregate query;
   (d) informative presence: for each variable, the outcome proportion (`hospital_expire_flag`
   or a chosen binary) among measured vs unmeasured (n, %, no p-values); (e) measurement-
   frequency views via EP-45's API (`qc.measurement.frequency(itemids, by=…)`: measurements
   per stay-day, % of stays with ≥ 1 in the first 24 h, time-to-first-measurement bins, by care
   unit/era); (f) structural vs unmeasured classification via EP-45's classifier
   (`qc.measurement.classify_absence(...)`) → variable → {structural, unmeasured,
   by-design (demo/ED)} with the rule text; all inside a `run` context; CLI `mwh stats missing
   <spec.yaml> --tier <t> [--out DIR]`.
2. **Spec builders** `src/mimicwarehouse/viz/missing.py` — `missing_bars(df)`,
   `pattern_matrix(df)` (rows = patterns by count, columns = variables, present/absent cells +
   count bar), `comissing_heatmap(df)` (via `viz/heatmap`), `measurement_frequency_lines(df)`;
   Altair, EP-5 theme, pre-aggregated frames only.
3. **Page** `app/pages/43_missingness.py` (registry id `missingness`, section Explore) — tabs
   Patterns / Co-missingness / Informative presence / Measurement process / Classification;
   target, variables and group-by controls; wrappers; run id; manifest entry
   `missingness-patterns` on demo.
4. **Representative workflow on full** — `mwh stats missing
   stats/specs/missing/tracer_first_day.yaml --tier full` (logged background job
   `%MWH_DATA_ROOT%\runs\jobs\ep72-missing-full.log`); record run id; export pattern table +
   heatmap via `viz.export` (EP-59; claim type exploratory) and check; page latency on full
   (`MWH_APP_RECORD_LATENCY=1`, ≤ 5 s).
5. **Tests** `tests/ep/test_ep72.py` (`@pytest.mark.ep_72`): pattern counts sum to N and every
   pattern string has length = number of variables; Jaccard symmetric and in [0, 1];
   informative-presence n's sum to N per variable; a crafted structural case (an ICU-only
   variable evaluated on a `hadm`-grain target) classifies as structural; frequency views come
   back for a fixture itemid; AppTest (ui group) renders each tab; `ui_lint`; dev-marked; full
   run id + latency recorded.

## Out of scope

- Computing measurement-process summaries themselves → EP-45 (this brief visualises them).
- Imputation and strategy comparison → EP-87; MNAR sensitivity / informative-presence models →
  parked (v2 MISS-2, already listed).
- QC profiles page → EP-61.

## Verification / acceptance

- `uv run poe test -m ep_72` and `uv run --group ui poe test -m ep_72` green (fixture; dev-
  marked); `uv run --group ui mwh verify EP-72` green (includes `ui_lint`).
- On dev: patterns heatmap, co-missingness, informative presence, measurement process and
  classification tabs render for the tracer cohort's first-day features.
- Full-tier run id and page latency recorded in the completion note; exported artifacts pass
  `mwh disclose check`; demo screenshot `missingness-patterns-*.png` + sidecars.
