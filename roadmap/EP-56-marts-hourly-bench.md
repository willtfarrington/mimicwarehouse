---
ep: 56
title: "Latency marts B: hourly bins ⏱"
size: M
model: claude-opus-5-5
effort: max
status: in progress
---

# EP-56 — Latency marts B: hourly bins ⏱

**Size:** M · **Tier:** fixture+dev+full · **Core/Stretch:** core · **Depends on:** EP-55 (Latency marts A: first-day features + mart registry ⏱), EP-175 (Latency marts A2: itemid rollups ⏱) · **Blocks:** EP-176 (Latency marts C: <=5 s page-query benchmark), EP-64 (Explorer A: server-side aggregation service + VegaFusion), EP-85 (Time-series & forecasting)

> **Split at pickup (2026-11-27; owner decision, D-2 addendum).** Judged against M ≈ 45 min,
> the brief came to ≈ 2–2.5 h, so the row was split before any code. Item 1 was already met:
> EP-55 and EP-175 each verified their own full job in-session (their completion notes). This
> brief keeps the hourly marts (items 2 and 3, with their tests in item 6). **Items 4 and 5,
> the page-query benchmark (`marts/bench.py`, `marts/bench_queries.yaml`, `mwh bench queries`,
> the `page_query` ledger kind, the dev and full runs), with the benchmark's tests in item 6,
> moved to EP-176** (M, core, right after this row and before EP-57; its brief carries them).
> The title changed from "hourly bins + <=5 s benchmark" to "hourly bins ⏱". Items below that
> name the benchmark carry a "→ EP-176" mark; the rest stands as written.

> **Amended at EP-55 (2026-11-26).** EP-55 was split at its pickup (D-2 addendum): the itemid
> rollups (`marts.itemid_daily`, `marts.itemid_summary`) are **EP-175**, which runs between
> EP-55 and this brief, so this brief now depends on both. **Item 1 changes:** EP-55's full job
> (`ep55-marts-full`) finished inside EP-55's session (2026-11-26, 13 s) and EP-55 verified and
> recorded it itself (its completion note: counts against `mimiciv_icu.icustays`, wall, peak RSS,
> disk delta, the regenerated `DATA-DICTIONARY.md`), so item 1 verifies **EP-175's** job
> (`ep175-rollups-full`, the `itemid_summary` row-per-curated-itemid check) unless EP-175 already
> did, and appends the completion note to EP-175's brief instead. Build on what EP-55 shipped:
> a new mart is a spec under `marts/specs/`, a builder module and one `marts.<name>` step in
> `dag/specs/marts.yaml`, written through `marts.registry.write_mart` (sorted file per bucket,
> manifest, status, `kind: mart` ledger line, snapshot id) and registered by `register_marts`;
> `--target marts` reads `--tag marts` (the name-corrections table).

> **EP-54 pickup note (2026-09-17).** Header facts unchanged; the EP-170 / EP-33 blocks
> below stand; read the body against `roadmap/README.md` § "P3 → P4 name corrections".
> Brief-specific: (1) **`marts.icustay_hourly` vs `mimiciv_derived.icustay_hourly`** — the
> latter is a vendored mimic-code concept (one row per stay × hour, EP-37); name the mart
> so the two cannot be confused in prose (`marts.icustay_hourly` is fine as a *schema-
> qualified* name; never cite either unqualified), and consider building on it. (2) The
> bin helpers exist: `timesem.hour_bin` / `sql_hour_bin` / `bin_bounds`, `timeline.
> hourly_bins(fill=True, window=…)`; the released population shape is `timeline.
> population_summary` (one count column per bin — D-33 addendum; the EP-49 benchmark
> `timeline_labs_icu_in_48h` is already a `kind: query` ledger line you can compare
> against). (3) **The bench harness is new code**: there is no `mwh bench` group yet — this
> brief creates `mwh bench queries`, and the latency records are `run.bench(...)` lines with
> a kind you **add to `dag.benchmarks.BENCHMARK_KINDS`** (`page_query`; EP-57 adds
> `page_latency` the same way) — never a second ledger writer, never a free-form dict
> (D-24 addendum, EP-54). (4) The app-tier DuckDB settings are `Settings.duckdb_app_memory_
> limit` (`MWH_DUCKDB_APP_MEMORY_LIMIT`) and the shared `duckdb_threads` (the EP-170 block);
> any `MWH_MARTS_HOURLY_MAX_HOURS` knob is a Settings field with `.env.example` parity.
> (5) The tracer cohort's attrition for query (h) is `cohort.build.attrition("first_icu_
> adults@1.0.0", tier)` (chain-suppressed) or the `runs.attrition` view. (6) `blood_gas` →
> `bg`. (7) This brief also **verifies EP-55's ⏱ job** (`mwh jobs --job ep55-marts-full
> --tail 20`, `mwh runs benchmarks --tier full --kind mart`) and appends EP-55's completion
> note — the D-18 record-keeping pattern (every P3 ⏱ job finished inside its session).
> **Amended at EP-170 (2026-08-29).** Header facts unchanged; shorthand per the README notation
> table. Item 4's env names do not exist: the shipped Settings field is
> `duckdb_app_memory_limit` (env `MWH_DUCKDB_APP_MEMORY_LIMIT`, default 12GB), and threads are
> the shared `duckdb_threads` — there is no `MWH_APP_MEMORY_LIMIT`/`MWH_APP_THREADS`. Use the
> shipped names, or add new fields with `.env.example` parity if the app truly needs its own
> (the retro recommended a smaller `duckdb_app_threads`, ledger ARCH-11) [FC-14]. Any new
> `MWH_MARTS_*` knob (item 2) is likewise a Settings field with parity, not a bare env read
> [ARCH-13].

> **EP-33 rename (2026-09-01).** Item 1's `mwh runs bench --kind build` reads `mwh runs
> benchmarks --kind build` — the shipped EP-32 verb (ledger P3C-6); `dag.benchmarks.
> BenchmarkLine` (extended by EP-35 with optional `run_id`/`disk_delta_mb`) is the ledger
> model, not `BenchmarkRecord`. Mechanical only (D5); substantive re-amendment stays with EP-54.

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

1. **(Met before this session: EP-55 and EP-175 verified their own full jobs.)** **Verify EP-55's full job** — check the job via `uv run --group dev mwh jobs --job
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
4. **→ EP-176 (split at pickup).** **Benchmark harness** (`src/mimicwarehouse/marts/bench.py`, `marts/bench_queries.yaml`,
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
5. **→ EP-176 (split at pickup).** **Run the bench** — dev in the foreground; full as a logged background job
   (`%MWH_DATA_ROOT%\runs\jobs\ep56-bench-full.log`, minutes at most); paste the full-tier
   per-query table into the completion note. Any query > 5 s: add/adjust a pre-aggregate mart in
   this brief (dated DESIGN note) or record a risk in `roadmap/README.md` § Risks naming EP-64.
6. **Tests** `tests/ep/test_ep56.py` (`@pytest.mark.ep_56`): fixture — `hour_bin` for crafted
   charttimes (exact-hour boundary, pre-ICU measurement dropped, cap honoured); population
   quantiles monotone (`p05 ≤ p25 ≤ p50 ≤ p75 ≤ p95`) and `n_stays` ≤ stays on the tier; every
   hourly variable is declared in `hourly_variables`; (→ EP-176) `bench_queries.yaml` parses and every
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
- (→ EP-176) `uv run --group dev mwh bench queries --tier full` results table recorded; every warm query
  ≤ 5 s or a named risk added; `runs.duckdb` `benchmarks` view shows `page_query` rows for dev
  and full.

## Parked → final-roadmap.md

- Hourly bins over hospital-wide (non-ICU) labs and GCS/urine-output hourly series — trigger:
  EP-82/85 need them.
- Rasterised chartevents-scale views (Datashader lane, v2 UI-2) and DuckDB-WASM/Mosaic offload
  (v2 UI-3) — trigger: Explorer needs > 10⁷ points client-side.
