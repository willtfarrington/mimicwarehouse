# EP-176 — Latency marts C: <=5 s page-query benchmark

**Size:** M · **Tier:** fixture+dev+full · **Core/Stretch:** core · **Depends on:** EP-56 (Latency marts B: hourly bins ⏱) · **Blocks:** EP-61 (Catalog & QC browser page), EP-64 (Explorer A: server-side aggregation service + VegaFusion), EP-65 (Explorer B: linked-brush distributions), EP-66 (Explorer C: heatmaps, correlations, cross-tabs, conditional summaries)

## Context

Split from EP-56 at its pickup (2026-11-27; the owner's choice, D-2 addendum of that day):
EP-56 came to ≈ 2–2.5 h against M ≈ 45 min, so its page-query benchmark (items 4 and 5, with
the benchmark's share of item 6) became this row. It sits right after EP-56, because one of its
queries reads `marts.hourly_population`, and before EP-57 and the pages whose briefs add their
own queries to its query file and record their full-tier latency against the ≤ 5 s target
(D-28): EP-61 (`catalog_tables_page` / `qc_flags`), EP-64 (the two Explorer default views),
EP-65 (`explorer_linked_6`), EP-66 (the heatmap queries). Wherever a P4 brief says "EP-56" for
the bench harness, its query file or the `page_query` kind, it means this row (the roadmap
README § "P3 → P4 name corrections"; the shipped names there override planning text).

**What exists.** `safe.safe_query(sql, *, tier, k, row_cap, timeout_s, actor, settings)`
(EP-30/33) opens its own read-only catalog for every call through
`catalog.connect.open_catalog`, which applies the **app** DuckDB profile
(`Settings.duckdb_app_memory_limit`, env `MWH_DUCKDB_APP_MEMORY_LIMIT`, default 12GB, and the
shared `duckdb_threads`; there is no `MWH_APP_MEMORY_LIMIT` / `MWH_APP_THREADS`, the EP-170
amendment). It audits every call, suppresses at k = 11, and refuses anything that is not
aggregate-shaped: a statement needs a real count-family column (`count(*)`, `count(DISTINCT
…)`) unless it reads only registry surfaces (`meta.*`, `information_schema`, and the named
`safe.REGISTRY_TABLES`, which hold `marts.cohorts`, `marts.itemid_summary` and whatever EP-56
added). Because each call opens a fresh connection, *cold* is the first execution of a query in
the process and *warm* its repeats: DuckDB's buffer pool does not outlive the connection, but
the OS file cache does. The benchmark ledger `runs/benchmarks.jsonl` is written only through
`run.bench(kind, name, *, wall_s, tier, run_id, build_id, settings, **fields)` →
`dag.benchmarks.append`. `dag.benchmarks.BenchmarkLine` is `extra="forbid"` (ts, build_id,
tier, step, kind, phase, wall_s, peak_rss_mb, rows, bytes_in, bytes_out, files,
duckdb_version, git_sha, host, ok, error, run_id, disk_delta_mb), so a cold flag or the
snapshot ids need new optional fields, carried into the `runs.duckdb` `benchmarks` view's
column map (`run.runs_db_views`), or an encoding the existing fields hold. `kind` is a plain
`str` from `BENCHMARK_KINDS`, to which this brief adds `page_query`. EP-57 adds `page_latency`
the same way and owns `MWH_APP_RECORD_LATENCY`; whether a `record_page_latency` helper exists is
EP-57's call (its EP-54 pickup note). The relations the queries read: `marts.icustay_first_day`
(EP-55, one row per ICU stay: `heart_rate_mean`, `hospital_expire_flag`, `gender`, `era`,
`sofa`, `first_service`, `los_icu_days`, `admission_type`, `sepsis3`, …),
`marts.itemid_summary` (EP-175, a registry table), EP-56's hourly marts, `meta.row_counts`, and
the tracer cohort's attrition (`cohort.build.attrition("first_icu_adults@1.0.0", tier)`,
chain-suppressed, or the `runs.attrition` view). No `mwh bench` group exists yet: this brief
creates it (`cli.py` registers each sub-app with one `app.add_typer()` line). CLI strings stay
ASCII or go through the console helper (Risk 13); the output is the pass/fail table, never a
result row.

## In scope

1. **The harness** (`src/mimicwarehouse/marts/bench.py`, `marts/bench_queries.yaml`, `mwh bench
   queries --tier <t> [--repeat 3] [--target-s 5]`): about 12 named aggregate queries
   representative of the P4 pages — a histogram of `heart_rate_mean` bins ×
   `hospital_expire_flag`; `hourly_population` `sbp` p50 by hour ≤ 72; the cross-tab `gender ×
   era`; an `itemid_summary` lookup; the first-day `sofa` distribution by `era`; stays by
   `first_service`; conditional quantiles of `los_icu_days` by `admission_type`; missing-% per
   first-day column; `corr()` over 10 first-day variables; `sepsis3` prevalence by `era`; a
   `meta.row_counts` scan; the tracer cohort's attrition counts (skipped when its cohort mart is
   absent). Each runs through `safe_query` (the app profile), once cold and then warm
   `--repeat` times. Every execution writes one ledger line through `run.bench`: `kind:
   page_query`, `step` = the query's name, `wall_s`, `rows`, the cold flag, `git_sha` and the
   snapshot ids `safe_query` reports. There is never a second writer or a free-form dict (the
   D-24 addendum of EP-54). The output is a rich pass/fail table (name, cold s, warm median s,
   target, verdict, rows), never a result row. The exit code is 1 when any warm median misses
   the target, and a refusal is a failure with its reason, never a crash.
2. **Run it**: dev in the foreground; full as a logged background job (log
   `runs\jobs\ep176-bench-full.log`, minutes at most), or in the foreground when the dev run
   shows that it fits well inside the 10-minute cap. Paste the full-tier per-query table into
   the completion note. For any warm query over 5 s, either add or adjust a pre-aggregate mart
   (with a dated DESIGN note), or record a risk in `roadmap/README.md` § Risks naming EP-64.
3. **Tests** `tests/ep/test_ep176.py` (`@pytest.mark.ep_176`):
   - `bench_queries.yaml` parses, and every query executes on fixture through `safe_query`
     without refusal.
   - The CLI's output contains no data rows (capsys), and its exit code follows the verdicts (a
     crafted target of 0 s fails).
   - Every execution leaves one `kind: page_query` ledger line.
   - Dev-marked: every warm dev query runs in under 5 s.
   - Full-marked (opt-in): the ledger holds a `page_query` line for tier `full` from this
     session.

## Out of scope

- The hourly marts (EP-56, shipped before this row).
- `page_latency` and the app shell's latency recording (EP-57).
- The pages' own queries: EP-61 and EP-64 … EP-66 add theirs to the query file.

## Verification / acceptance

- `uv run poe test -m ep_176` green on fixture and dev; `uv run mwh verify EP-176` green.
- `uv run --group dev mwh bench queries --tier full`: the results table is recorded in the
  completion note, and every warm query is ≤ 5 s or has a named risk added.
- The `runs.duckdb` `benchmarks` view shows `page_query` rows for dev and full.
