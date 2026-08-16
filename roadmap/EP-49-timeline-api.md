# EP-49 — Event-aligned timeline API

**Size:** M · **Tier:** fixture+dev+full · **Core/Stretch:** core · **Depends on:** EP-34 (Time semantics + unit-of-analysis registry) · **Blocks:** EP-54 (Re-plan P3), EP-67 (Patient-safe timeline viewer), EP-82 (Longitudinal trajectories (+ trajectory groups)), EP-86 (Exposure-response / treatment patterns)

## Context

Capability 8 (event-aligned timeline queries) is the query layer under trajectories (EP-82),
exposure-response (EP-86), the hourly marts (EP-56) and the owner-only timeline viewer (EP-67):
"take events from table X, align them to anchor A per stay/admission, keep the window
[−a, +b) hours, bin hourly, join as-of". This brief builds `src/mimicwarehouse/timeline.py`
(DESIGN §15) on `timesem` (EP-34: `sql_hours_since`, `[start, end)` bins, grains) with DuckDB's
`ASOF JOIN` and window functions. Anchors come from the catalog and mimic-code concepts (EP-37/38:
`ventilation`, `vasoactive_agent`, `antibiotic`, `suspicion_of_infection`); relative time only
(per-patient date shift), naive timestamps as shipped. Governance: the API returns lazy DuckDB
relations / aggregated frames; the single-stay row-level path exists for EP-67 but is reachable
only through the owner role (`safe.owner_rows`, EP-30) and is never exercised on dev/full in tests
(D-32). Full tier: one alignment benchmark over `labevents` (Parquet, itemid pushdown) recorded
via `run.bench` (D-18, D-28). D-17 (Polars/DuckDB) applies.

## In scope

1. **Anchor registry** (`src/mimicwarehouse/timeline.py`) — `Anchor` (name, grain, source
   table/view, time column, selector `first|last|each`, filter) and `ANCHORS`: `hosp_admit`
   (`admissions.admittime`), `hosp_discharge`, `icu_in` (`icustays.intime`), `icu_out`,
   `first_culture` (`microbiologyevents.charttime`, first per hadm), `first_antibiotic`
   (`mimiciv_derived.antibiotic.starttime`, first per hadm), `suspected_infection`
   (`mimiciv_derived.suspicion_of_infection`), `med_start(codeset@version, source)`
   (`prescriptions.starttime` | `inputevents.starttime`), `procedure(itemids)`
   (`procedureevents.starttime`), `vent_start` (`mimiciv_derived.ventilation`, first invasive or
   non-invasive; parameter `kinds`), `deterioration` (first vasopressor start from
   `mimiciv_derived.vasoactive_agent`), `phenotype_onset(id@version)`, `custom(sql)`.
   `anchor_sql(anchor, grain) -> SQL` yields `(grain keys, anchor_time)`; missing anchors → null
   (documented; consumers decide).
2. **Alignment + windows** — `align(source: EventSource, anchor, window=(-24, 72), grain=
   "icustay", clip_to_stay=True) -> duckdb.DuckDBPyRelation` producing `(grain keys, event_time,
   hours_since_anchor, code/itemid, value, valueuom, source_table)`; `EventSource` presets:
   `labs(itemids)`, `vitals(itemids)` (chartevents), `inputs(itemids)`, `outputs(itemids)`,
   `meds(codeset)`, `procedures(itemids)`, `micro()`, `transfers()`, `custom(sql)`;
   `window_join(left, right, by, on, before_h, after_h)`; `asof_join(left, right, by, on,
   direction="backward", tolerance_h=None)` (DuckDB `ASOF JOIN`); `event_at(source, anchor,
   tolerance_h)` (last value before anchor within tolerance).
3. **Binning + aggregation** — `hourly_bins(aligned, width_h=1, aggs={"value": ["count",
   "mean", "min", "max", "last"]}) -> relation` with `[start, end)` bins over `hours_since_anchor`,
   including empty bins when `fill=True`; `daily_bins`; `population_summary(binned, k=11)` →
   per-bin counts/means across units, passed through `disclose.suppress` (EP-43) — the only
   frame shape a session prints. `to_mart(relation, path)` helper writing Parquet through the DAG
   sink (EP-55/56 will call it).
4. **Owner-only single-stay path** — `stay_events(stay_id, sources, conn=owner_conn)` returns
   the row-level lane data for EP-67; it requires the owner-role connection object from
   `safe.owner_rows` (raises `PermissionError` otherwise), writes the EP-30 audit line, and is
   tested only on the fixture tier.
5. **Benchmarks + tests + docs** — full-tier benchmark: `align(labs(5 curated lab itemids),
   icu_in, (-6, 48))` → `hourly_bins` → `population_summary` on `full.duckdb`; run inside
   `run.start(kind="bench")`, `run.bench(kind="query")`; always as a logged background job via
   EP-19's `dag.jobs.launch` (`uv run --group dev mwh timeline bench --tier full --background
   --job ep49-timeline-bench`, log `%MWH_DATA_ROOT%\runs\jobs\ep49-timeline-bench.log`; poll
   with `mwh jobs --job ep49-timeline-bench`); record wall/peak RSS from the ledger. The full-tier
   benchmark run also saves the suppressed `population_summary` frame as
   `runs/<run_id>/tables/timeline_labs_icu_in_48h.parquet` (with a `.md` twin whose header
   carries `Claim type: exploratory` and the sentence that MIMIC-IV analyses are retrospective)
   and a `population_band` figure (`.png` + sibling `.csv` source table) written through EP-43's
   `disclose.suppress` + `disclose.check(..., write_sidecar)` (EP-59's `export_table`/
   `export_chart` wrap the same path once built; EP-73 promotes these exports through them) so
   `mwh disclose check` passes on the run's exports. `tests/ep/test_ep49.py`
   (`@pytest.mark.ep_49`; fixture, `dev`, `full` opt-in): crafted synthetic events (ids
   ≥ 90 000 000, temp DuckDB) → correct signed `hours_since_anchor`, window edges (`[start, end)`),
   `clip_to_stay`, as-of picks the last prior value within tolerance, bins sum to raw counts, empty
   bins filled with 0/null, `stay_events` refuses without the owner connection; every anchor's SQL
   compiles on the fixture catalog; on dev the benchmark pipeline runs and `population_summary`
   prints. `docs/methods/timelines.md` (new): anchor table, window semantics, as-of rules,
   examples.

## Out of scope

- Hourly-binned latency marts and the ≤ 5 s benchmark → EP-55/56 (call `to_mart`).
- The Plotly timeline viewer page → EP-67; trajectories → EP-82; exposure-response → EP-86.
- Events spine (long MEDS table) → EP-50 (the API may later read from it; not required here).
- Chartevents-scale vitals in the spine → parked (`final-roadmap.md` § 8–10).

## Verification / acceptance

- `uv run poe test -m ep_49` green on fixture and dev; `uv run --group dev mwh verify EP-49` green.
- Full-tier benchmark launched only as a background job (`--background --job
  ep49-timeline-bench`); job id, log path, run id, wall time and peak RSS recorded in the
  completion note and in `runs.benchmarks` (`kind='query'`, name `timeline_labs_icu_in_48h`).
- `runs/<run_id>/exports/` for the full-tier benchmark passes `uv run --group dev mwh disclose
  check` and carries `.disclosure.json` sidecars (table, `.md` twin, `population_band` figure +
  source table).
- `stay_events` raises `PermissionError` without the owner connection (test), and no test on
  dev/full calls it.
- `docs/methods/timelines.md` exists with the anchor table generated from `ANCHORS`.
