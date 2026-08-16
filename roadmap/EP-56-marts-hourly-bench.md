# EP-56 — Latency marts B: hourly bins + <=5 s benchmark

**Size:** M · **Tier:** fixture+dev+full · **Core/Stretch:** core · **Depends on:** EP-55 (Latency marts A: first-day features + itemid rollups ⏱) · **Blocks:** EP-64 (Explorer A: server-side aggregation service + VegaFusion), EP-85 (Time-series & forecasting)

## Context

EP-55 built the stay-grain `marts.icustay_first_day`, the itemid rollups and the mart registry,
and launched the full-tier marts build as a background ⏱ job. This brief (a) verifies that job
and closes EP-55's completion note, (b) adds the hour-grain marts on EP-34's `hour_bin` grain
(`hour_bin = floor((charttime − icu_intime) / 1 h)`), and (c) builds the page-query benchmark
harness that every P4 UI brief uses to record its full-tier latency against the ≤ 5 s target
(D-28). Sources are already materialised in `mimiciv_derived` (EP-37/38): `vitalsign`
(chartevents vitals) and the hadm-keyed lab concepts `chemistry`, `complete_blood_count`,
`blood_gas`, `coagulation`, `enzyme` (attributed to the ICU stay whose window contains
`charttime`); units are harmonised by EP-39. The full hourly build reads ~10⁸ rows and is a
logged background job; benchmark queries are read-only aggregates through `safe_query` (EP-30).
Ledger writes go through the EP-35 benchmark-ledger writer (`runs/benchmarks.jsonl`, exposed by
`runs.duckdb`). EP-64 (Explorer) and EP-85 (time series) consume the hourly tables. Time is
relative to ICU admission only (per-patient date shift; `anchor_year_group` = `era` is the only
cross-patient axis).

## In scope

1. **Verify EP-55's full job** — check the job via `uv run --group dev mwh jobs --job
   ep55-marts-full --tail 20` (state + INFO lines only) and the ledger via `mwh runs bench --kind
   build`; via `safe_query` compare `count(*)` of
   `marts.icustay_first_day` with `mimiciv_icu.icustays` on full and check `itemid_summary` has
   one row per curated itemid; record wall time, peak RSS and disk delta in the benchmark ledger
   (if the runner did not) and append `> **Completion note (date).**` with a small table to
   `EP-55-marts-first-day.md`. If the job failed or is incomplete, resume it (`mwh build --tier
   full --target marts --resume`) as a background job and record that instead.
2. **`marts.icustay_hourly`** (`marts/hourly.py` + `marts/specs/icustay_hourly.yaml`) — one row
   per (`stay_id`, `hour_bin`) for `hour_bin` 0 … `ceil(los_icu_hours)`, capped at
   `MWH_MARTS_HOURLY_MAX_HOURS` (default 336; documented in the spec); a row exists only when at
   least one variable was measured in that hour; wide columns `<var>_{mean,min,max,n,last}` for
   variables heart_rate, sbp, dbp, mbp (arterial preferred, else NBP, as in `vitalsign`),
   resp_rate, temperature (°C), spo2, glucose, lactate, creatinine, bun, sodium, potassium,
   bicarbonate, hemoglobin, wbc, platelet, ph, pao2, pco2, inr; keys `subject_id, stay_id,
   subject_bucket`; partitioned by `subject_bucket`, sorted (`subject_id, stay_id, hour_bin`).
   `marts.hourly_variables`: dictionary (variable, unit, source concept, itemids) generated from
   the spec. `marts.hourly_population`: unpartitioned pre-aggregate for `hour_bin ≤ 168` —
   (`hour_bin`, `variable`, `era` including an `all` row, `n_stays`, `p05, p25, p50, p75, p95`,
   `mean`) — the table the Explorer's default trend views hit in milliseconds; `n_stays` is
   present so the app can apply the small-cell rule.
3. **DAG steps + full build** — steps `marts.icustay_hourly`, `marts.hourly_population` after
   the EP-55 steps; build dev; then, as the *first* action after fixture tests pass, launch
   `mwh build --tier full --target marts.icustay_hourly,marts.hourly_population` as a background
   job with log `%MWH_DATA_ROOT%\runs\jobs\ep56-hourly-full.log`; check at the end of the
   session; if unfinished, record job id/log and let EP-64 (which depends on this brief) record
   the timing in its completion note.
4. **Benchmark harness** (`src/mimicwarehouse/marts/bench.py`, `marts/bench_queries.yaml`,
   `mwh bench queries --tier <t> [--repeat 3] [--target-s 5]`) — ~12 named aggregate queries
   representative of P4 pages: histogram of `heart_rate_mean` bins × `hospital_expire_flag`;
   `hourly_population` `sbp` p50 by hour ≤ 72; cross-tab `gender × era`; `itemid_summary`
   lookup; first-day `sofa` distribution by `era`; stays by `first_service`; conditional
   quantiles of `los_icu_days` by `admission_type`; missing-% per first-day column; `corr()`
   over 10 first-day variables; `sepsis3` prevalence by `era`; `meta.row_counts` scan; the
   tracer cohort's attrition counts (EP-47 table if present, else skipped). Each runs through
   `safe_query` with the app-tier DuckDB settings from `config.py` (`MWH_APP_MEMORY_LIMIT`
   default `12GB`, `MWH_APP_THREADS` 8), cold (fresh connection) and warm; records
   `{kind:"page_query", name, tier, wall_s, rows, cold, git_sha, snapshot_ids}` via the EP-35
   ledger writer; prints a rich pass/fail table (never result rows); exit code 1 if any warm
   query misses the target. Also `bench.record_page_latency(page, tier, wall_s, note)`
   appending `kind:"page_latency"` — used by the app shell (EP-57) and every UI brief.
5. **Run the bench** — dev in the foreground; full as a logged background job
   (`%MWH_DATA_ROOT%\runs\jobs\ep56-bench-full.log`, minutes at most); paste the full-tier
   per-query table into the completion note. Any query > 5 s: add/adjust a pre-aggregate mart in
   this brief (dated DESIGN note) or record a risk in `roadmap/README.md` § Risks naming EP-64.
6. **Tests** `tests/ep/test_ep56.py` (`@pytest.mark.ep_56`): fixture — `hour_bin` for crafted
   charttimes (exact-hour boundary, pre-ICU measurement dropped, cap honoured); population
   quantiles monotone (`p05 ≤ p25 ≤ p50 ≤ p75 ≤ p95`) and `n_stays` ≤ stays on the tier; every
   hourly variable is declared in `hourly_variables`; `bench_queries.yaml` parses and every
   query executes on fixture through `safe_query` without refusal; `bench` output contains no
   data rows (capsys); dev-marked — all warm dev queries < 5 s; full-marked (opt-in) — the ledger
   holds a `page_query` record for tier `full` from this session.

## Out of scope

- Explorer aggregation service, VegaFusion, page code → EP-64/65/66.
- Time-series methods (smoothing, forecasting) → EP-85; feature windows → EP-102.
- Hourly bins for non-ICU (ward) labs and GCS/urine hourly series → parked below.

## Verification / acceptance

- `uv run poe test -m ep_56` green on fixture and dev; `uv run mwh verify EP-56` green.
- EP-55 carries a completion note with full-tier row counts, wall time, peak RSS, disk delta.
- Full hourly build launched as a background job (log at
  `%MWH_DATA_ROOT%\runs\jobs\ep56-hourly-full.log`); job id and, if finished, counts + timing
  recorded in this brief's completion note (else EP-64 records them).
- `uv run --group dev mwh bench queries --tier full` results table recorded; every warm query
  ≤ 5 s or a named risk added; `runs.duckdb` `benchmarks` view shows `page_query` rows for dev
  and full.

## Parked → final-roadmap.md

- Hourly bins over hospital-wide (non-ICU) labs and GCS/urine-output hourly series — trigger:
  EP-82/85 need them.
- Rasterised chartevents-scale views (Datashader lane, v2 UI-2) and DuckDB-WASM/Mosaic offload
  (v2 UI-3) — trigger: Explorer needs > 10⁷ points client-side.
