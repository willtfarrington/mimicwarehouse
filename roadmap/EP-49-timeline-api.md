# EP-49 — Event-aligned timeline API

**Size:** M · **Tier:** fixture+dev+full · **Core/Stretch:** core · **Depends on:** EP-30 (Safe-query wrapper + audit log), EP-34 (Time semantics + unit-of-analysis registry) · **Blocks:** EP-54 (Re-plan P3), EP-67 (Patient-safe timeline viewer), EP-82 (Longitudinal trajectories (+ trajectory groups)), EP-86 (Exposure-response / treatment patterns)

> **EP-33 amendment (2026-09-01).** **Depends-on changed** (carried P3C-8): now EP-30 (the
> audit seam) and EP-34; the roadmap README row is updated in the same session. (1)
> **`safe.owner_rows` does not exist** (ledger P3C-3, confirmed): safe.py defers owner row
> viewing to EP-58's app path, and EP-58 follows this brief in the linear order. Item 4
> therefore **defines its own owner gate** — the recommended option, chosen over deferring
> item 4 behind an EP-58 dependency because EP-67 needs `stay_events` and EP-58 can wrap this
> gate rather than re-derive it: `stay_events(stay_id, sources, *, conn)` requires a connection
> opened as `catalog.connect.open_catalog(tier, role="owner")` (raises `PermissionError` for
> any other role, including the default `agent`; `MWH_ROLE` stays unset in Claude sessions, so
> the gate is closed there by construction) and writes one audit line through the public seam
> EP-33 exposes — `fsio.append_jsonl(<audit path>, safe.AuditLine(actor="owner",
> allowed=True, statement_sha256=sha256("row_view:" + canonical request), ...).model_dump())`,
> the `row_view:` statement_sha256 convention marking row-view events in `runs.audit` without
> recording the row selection itself (remaining fields filled as for a query; the audit path is
> the one `safe_query` uses). EP-58 reuses this gate for the app (its `owner_rows()` wraps
> `stay_events`-style calls with the gate token + TTL); read the Context's "(`safe.owner_rows`,
> EP-30)" as "(the owner gate of item 4)". Tests stay fixture-only; the `PermissionError` test
> passes a `role="agent"` connection. (2) Every aggregate the API returns to a session goes
> through `safe_query` (or the `SUPPRESSOR` seam for in-process frames): `population_summary`
> reads `mimiciv_derived.*` views, which are non-registry, subject-keyed reads (P3C-5) — keep
> `code`/`valueuom` values <= 64 chars. (3) Benchmarks: `run.bench` -> `BenchmarkLine(kind=
> "query", run_id=...)` (EP-35 amendment; no `BenchmarkRecord`), read with `mwh runs benchmarks
> --kind query`; `mwh timeline bench --background --job ...` delegates to `dag.jobs.launch`
> as `mwh build --background` does, peek via `mwh jobs --job ... --tail N`. (4) Exports follow
> `docs/committed-text.md` (no run ids in file names — `timeline_labs_icu_in_48h.parquet` is
> fine) and EP-43's sidecar path; refusals print on stderr via `console.fail`.

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

> **Completion note (2026-09-17).** Executed as briefed on fixture + dev + full (M, one
> session). Shipped: `src/mimicwarehouse/timeline.py` (the anchor registry `Anchor` /
> `ANCHORS` — `hosp_admit`, `hosp_discharge`, `icu_in`, `icu_out`, `first_culture`,
> `first_antibiotic`, `suspected_infection`, `vent_start`, `deterioration` — plus the
> factories `med_start(codeset, source)`, `procedure(itemids)`, `vent_start(kinds)`,
> `phenotype_onset(id@version)`, `custom_anchor(name, sql)`; `anchor_sql(anchor, grain)`
> → `(grain keys, anchor_time)` for every unit with NULL where the event is absent and
> `selector` first / last / each; the `EventSource` presets `labs` / `vitals` / `inputs` /
> `outputs` / `meds` / `procedures` / `micro` / `transfers` / `custom_source`;
> `align` / `align_sql`, `window_join`, `asof_join` (DuckDB `ASOF JOIN`), `event_at`;
> `hourly_bins` / `daily_bins` (`fill=True` adds the empty bins), `population_summary`
> through the `safe.SUPPRESSOR` seam, `to_mart` through `publish.swap_file`; the
> owner-only `stay_events` with its `row_view:` audit line; `run_benchmark` and the
> `mwh timeline anchors | bench [--background --job]` commands; the
> `docs/methods/timelines.md` renderer), `catalog/connect.py` (the role stamped on every
> catalog connection as the session variable `mwh_role`), `cli.py` (one `add_typer`
> line), `tests/ep/test_ep49.py` (17 fixture + 1 dev + 1 full tests),
> `docs/methods/timelines.md` (new; anchor + source tables generated), DESIGN §7 / §15
> notes, D-32 + D-33 addenda, README (State row, doc table, quick start, layout),
> `docs/gotchas.md` §1 / §5, `docs/methods/disclosure.md` (EP-49 note).
>
> **Interpretation choices.** (1) **SQL text first.** Every builder is a pure `*_sql`
> function and the relation twins are `con.sql(text)`, so a run records the statement it
> executed (`rel.sql_query()` round-trips; builders compose subqueries, never nested
> `WITH`); every relation-producing function takes `con=` (the brief's signatures omit it,
> but a lazy DuckDB relation needs a connection). (2) **The join-key rule.** Anchor events
> and event rows are normalised to `(subject_id, hadm_id, stay_id, …)` and join a grain's
> units on the finest key both sides carry — `icu_in` on the `hadm` grain is the
> admission's first ICU intime, `first_culture` on `icustay` gives every stay its
> admission's first culture, subject-keyed `labevents` reach every stay of the subject
> and the window / `clip_to_stay` decide; `phenotype_onset` reads the latest-built
> `mimiciv_derived.phenotype_<id>` view and records the requested version in its params.
> (3) **The aligned relation carries `anchor_time`** beside the brief's seven columns
> (`event_at` and the viewer need it). (4) **One count column in the released summary.**
> `population_summary` releases `n_units` + pooled mean + per-unit quartiles + extremes;
> the event count per bin is withheld because beside `n_units` it is a nested pair whose
> small difference the gate refuses (EP-33 amendment b) — on real hourly bins that
> difference is small on most rows (D-33 addendum). (5) **`event_at` counts an event at
> the anchor** (`[-tolerance, 0]`, closed at 0). (6) **The owner gate is a session
> variable** stamped by `open_catalog` (`SET VARIABLE mwh_role`; `timeline.require_owner`)
> — a guard against accidental use, not a security boundary; the audit line's `sql_text`
> names the lanes, never the stay id (D-32 addendum). (7) **`exports/` beside `tables/`.**
> The brief names `runs/<run_id>/tables/timeline_labs_icu_in_48h.parquet` and its
> acceptance `runs/<run_id>/exports/`; the run saves the released frame under `tables/`
> (`Run.save_table`) **and** publishes the four gated artefacts + sidecars under
> `exports/` (staged under `tmp/`, `disclose.check` each, published only when all pass —
> the EP-48 shape; recorded in the manifest's `tables` / `figures` maps), so EP-59's
> `export_table` / `export_chart` have a run-folder convention to inherit. (8) **Grains.**
> `icustay` / `hadm` / `subject` align; `icu_day` / `hour_bin` are refused (the API bins
> itself); `clip_to_stay` is refused on `subject`. (9) The `.md` twin's table headers carry
> gate-exempt words (`bin_index`, `start (hours)`) and its descriptions stay under 64
> characters (`docs/gotchas.md` §5). (10) Engine lesson: `bin_index * 1.0` binds as
> DECIMAL and reaches Altair as an unserialisable `Decimal` — bin bounds are cast to
> DOUBLE (`docs/gotchas.md` §1; the first dev run failed on it).
>
> **Runs.** fixture (data root, probe): run `20260917T151654Z-d08dfb` — 138 raw bin rows,
> all withheld below k on 75 synthetic stays, the four exports still pass the gate (an
> empty released table is a valid export); the test suite runs the same pipeline on its
> session fixture lake. dev: `mwh timeline bench --tier dev` run `20260917T151745Z-9938e6`
> — query wall **0.3 s**, peak RSS 140 MB, 240 raw bin rows → 240 released, 0 withheld,
> run wall 1.3 s; the dev test's run `20260917T152725Z-09ae8c` (query 0.1 s). full:
> background job `ep49-timeline-bench` (pid 31348, log
> `runs/jobs/ep49-timeline-bench.log`, started 2026-09-17T15:19:22Z, finished
> 15:19:29Z, exit 0) → run `20260917T151923Z-c25231` — query wall **0.8 s**, peak RSS
> 326 MB, disk delta 0.3 MB, run wall 1.7 s, 240 raw bin rows (5 items × 48 hourly bins
> from `[0, 48)`; the `[-6, 0)` bins are empty under `clip_to_stay`) → 240 released, 0
> withheld (every bin has 1,268–19,501 stays); `runs.benchmarks` carries the `kind =
> query` line `timeline_labs_icu_in_48h` for full (and two for dev); the four exports
> pass `disclose.check` (0 fail, 0 warn each) and `disclose.verify`. The full-tier
> alignment is fast because DuckDB reads only the six `labevents` columns the statement
> touches and pushes the five-itemid filter into the Parquet scan; a warm page cache
> helped (a cold run is the ⏱ standard's business, not this brief's).
>
> **Gates.** `uv run pytest -m ep_49` (fixture): 17 passed; `--tier dev -k dev_benchmark`:
> 1 passed; `--tier full -k full_benchmark`: 1 passed; `uv run mwh verify EP-49` and
> `uv run poe check` (ruff, `ruff format --check` — which formats the Markdown code
> blocks too — pyright 0 errors, the fixture suite **1,089 passed**, 54 dev/full/demo
> probes deselected, 617 s; one first-pass failure was `test_ep43`'s ASCII check on the
> `disclosure.md` note, fixed and re-run green) green; `mwh verify EP-49`: 17 passed;
> `mwh guard` clean over the nine changed / new files; `poe roadmap-check --strict` 0
> errors, 0 warnings; `mwh disclose check docs/methods/timelines.md` exit 0 (no
> `--allow-text`). No earlier `test_ep*.py` edited; `open_catalog`'s extra `SET
> VARIABLE` leaves `HARDENING_SQL` unchanged, so EP-21 / EP-30 / EP-33 pins hold.
>
> **Owner decisions at the interactive review (2026-09-17, every recommended option
> taken).** (1) Commit in the standard two steps, no push (the owner pushes). (2) Keep
> the owner gate as the session-variable stamp `open_catalog` sets (D-32 addendum) —
> rejected: a wrapper object around the connection, and deferring the row-level path
> behind EP-58. (3) `population_summary` withholds the event count and releases
> `n_units` alone (D-33 addendum) — rejected: releasing `n_events` under the suppressor
> (most full-tier bins would vanish), and relaxing the gate's nested-totals rule for
> timelines. (4) Keep both `tables/` (the run-internal frame) and `exports/` (the gated
> release set with sidecars) — rejected: `exports/` only, and `tables/` + `figures/`
> without an `exports/` folder. Routine choices logged above: `anchor_time` in the
> aligned relation, `con=` on every relation builder, the closed-at-zero `event_at`
> window, the DOUBLE cast of bin bounds, the gate-exempt Markdown headers.
>
> **Handed on.** EP-67: `stay_events` (lanes = source names; `require_owner` is the gate
> to wrap) and `align` for the aggregated / binned views; EP-58: `owner_rows()` wraps
> `require_owner` + the audit line with its token + TTL; EP-55/56: `to_mart` and
> `hourly_bins(fill=True, window=…)` are the mart builders; EP-82 / EP-86: `align` +
> `hourly_bins` relations and `population_summary` are the inputs; EP-59: the
> `exports/` stage → check → publish → sidecar shape; EP-50: the presets' `events_sql()`
> is the normalised event shape a spine reader can substitute; EP-54: whether the
> `n_events` decision (D-33 addendum) should become a general "one count column per
> released bin" rule, and whether `open_catalog`'s role stamp should move into
> `engine.open_duckdb` once the app (EP-57/58) opens connections elsewhere.
