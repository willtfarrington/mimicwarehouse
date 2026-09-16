# EP-45 — Measurement-process summaries

**Size:** M · **Tier:** fixture+dev+full · **Core/Stretch:** core · **Depends on:** EP-44 (Data-quality profiling) · **Blocks:** EP-54 (Re-plan P3), EP-72 (Missing-data views)

> **Owner gate to surface at completion (owner directive 2026-09-06, at EP-41).** The
> session that completes this brief must end its final message with an explicit
> "**before EP-46 starts, you must finish this**" item: the owner's review of the two
> EP-40 GEM review files (`<data_root>\studies\codesets\reviews\t2dm@1.0.0.gem-review.md`
> and `sepsis_explicit@1.0.0.gem-review.md` — accept codes into a new code-set version
> via `mwh codeset lock` / `compile`, or reject them all and say so). The owner asked for
> silence on this item from EP-42 through EP-45; EP-46's brief carries the matching
> pickup gate. Sessions never open the files (data root); the owner relays verdicts.

> **EP-33 amendment (2026-09-01).** Header facts unchanged. **Spec discovery** (ledger P3C-2,
> implemented by EP-37): `dag/specs/measurement.yaml` is merged into the one graph by
> `dag.spec.load_dag()`, so `mwh build --tier dev --tag measurement` needs no `--spec`; steps
> are the `python` kind via `dag.runner.STEP_HANDLERS` (`module:function`, `(step, ctx) ->
> StepOutcome`). **`meta.*` names** are EP-29's (`meta.itemids`, `meta.tables`, `meta.columns`
> with `unit_hint`) plus EP-39's `meta.item_units`/`meta.item_dictionary`; the `meta.mp_*`
> tables land as `lake/meta/<tier>/<table>.parquet` and are registered by the `catalog` step.
> The mortality-rate arms are `count(*) FILTER (WHERE ...)` aggregates under `safe_query`
> (EP-33 B1); the rate ratio and its CI are computed in Python from the released counts, not in
> SQL (arithmetic over aggregates stays refused — final-roadmap DIS-3). Job peeks via `mwh jobs
> --job measurement-full --tail N`; `run.bench` writes `BenchmarkLine` rows (EP-35 amendment),
> read with `mwh runs benchmarks --kind query`; the report obeys `docs/committed-text.md`.

## Context

In ICU data, *whether* something was measured carries information (informative presence), and
absence has two very different causes: structural (an item is not charted in that care unit or
era at all) versus unmeasured (the item is in use but this stay did not get it). Capability 7
(missing-data & measurement-process analysis) starts here with the descriptive half:
measurement frequency by ICU hour/day for the curated itemids (EP-39), structural-vs-unmeasured
classification by care unit × era (EP-34's `anchor_year_group` is the only admissible era axis),
and informative-presence summaries labelled exploratory. Builds on `qc/profile.py` (EP-44) and
writes `src/mimicwarehouse/qc/measurement.py` (DESIGN §15). Full-tier scans touch `chartevents`
and `labevents` restricted to ≤ 60 itemids (fast with Parquet pushdown; still a background job,
D-18). All outputs are aggregates; k = 11 suppression via `disclose` (EP-43) before any rendering.
D-5 (own theme per category — here the theme is first-24 h vitals/labs measurement) and D-33 apply.

## In scope

1. **Hourly/daily measurement frequency** (`src/mimicwarehouse/qc/measurement.py`) — for a
   configurable itemid set (default: all `curated=True` items in `meta.item_units`), per ICU stay:
   measurements per hour bin (`hours_since_icu_intime`, `[0,1) … [0,168)`), per ICU day, and
   `n_stays_at_risk` per bin (stays still in the ICU); population tables `meta.mp_item_hourly`
   (itemid, hour_bin, n_stays_at_risk, n_stays_measured, n_measurements), `meta.mp_item_daily`,
   `meta.mp_item_summary` (itemid, share_measured_first_24h, median measurements per stay-day,
   median inter-measurement interval min, p10/p90) — grain and bins from `timesem`.
2. **Structural absence vs unmeasured** — `meta.mp_structural` (itemid, first_careunit, era,
   n_stays, n_stays_measured, share, `structural_flag`): flag `structural` when share = 0 for a
   (unit, era) cell with n_stays ≥ 50, `sparse` when share < 5 %, else `in_use`; per-stay
   attribution then classifies each missing item as structural (its unit×era cell is structural)
   or unmeasured; `meta.mp_absence_summary` (itemid, n_missing_first_24h, n_structural,
   n_unmeasured).
3. **Informative-presence summaries** (exploratory) — for each curated lab: in-hospital
   mortality (from `admissions.hospital_expire_flag`) rate among stays *with* vs *without* a
   measurement in the first 24 h, rate ratio with a Wald 95 % CI (statsmodels), n per arm; also
   count-tertile version (0 / 1–2 / ≥ 3 measurements); table `meta.mp_presence_outcome` with a
   `claim_type = 'exploratory'` column and a note that this is descriptive association only.
4. **DAG + report + CLI** — DAG spec `src/mimicwarehouse/dag/specs/measurement.yaml` (python
   steps `measurement.hourly`, `measurement.structural`, `measurement.presence`,
   `measurement.report`; tag `measurement`; itemid set via the step's params) inside
   `run.start(kind="qc")`; `runs/<run_id>/measurement_process.md` with the three summaries
   (suppressed, reproduction block); `mwh qc measurement --tier dev` prints summary + top structural
   cells. Full tier: `uv run --group dev mwh build --tier full --tag measurement --background --job
   measurement-full` (log `%MWH_DATA_ROOT%\runs\jobs\measurement-full.log`), poll with `mwh jobs
   --job measurement-full` (expected 5–20 min), record run id/timing; while there, verify EP-44's
   full QC run (`mwh jobs --job qc-full`) if EP-44 deferred it (append its completion note).
5. **Tests + docs** (`tests/ep/test_ep45.py`, `@pytest.mark.ep_45`; fixture, `dev`) — crafted
   synthetic stays: hourly counts sum to total measurements; `n_stays_at_risk` decreases at
   `outtime`; a unit×era cell with zero measurements and ≥ 50 stays is `structural`; per-stay
   attribution splits missing into structural/unmeasured with the expected counts; the rate ratio
   matches a hand computation; the report passes `disclose.check`; on dev, tables build for the
   default itemid set. `docs/methods/measurement-process.md` (new): definitions, thresholds,
   caveats (charting practice varies by unit and era; MetaVision-only ICU data; no calendar time).

## Out of scope

- Missingness-pattern heatmaps and page → EP-72; imputation strategies (MICE etc.) → EP-87.
- Formal informative-presence models / MNAR sensitivity → parked (`final-roadmap.md` § 7).
- Prevalence/rate estimators with denominators → EP-68.

## Verification / acceptance

- `uv run poe test -m ep_45` green on fixture and dev; `uv run --group dev mwh verify EP-45` green.
- `uv run --group dev mwh build --tier dev --tag measurement` writes `meta.mp_item_hourly`,
  `meta.mp_item_daily`, `meta.mp_item_summary`, `meta.mp_structural`, `meta.mp_absence_summary`,
  `meta.mp_presence_outcome`; `uv run --group dev mwh qc measurement --tier dev` prints them.
- `mwh disclose check` exits 0 on `runs/<run_id>/measurement_process.md`.
- Full-tier run id, wall time, peak RSS recorded in the completion note; EP-44's completion note
  appended if it was deferred here.

## Parked -> final-roadmap.md

- Measurement frequency stratified by care unit x era (per-cell hourly / daily profiles and
  interval quantiles; this brief stratifies only the structural map) and summaries for
  uncurated itemids (any `meta.item_dictionary` row, not only the EP-39 catalogue) -> v2 MISS-3.
- At-risk curve banding: `meta.mp_item_hourly.n_stays_at_risk` is a non-increasing sequence whose
  consecutive differences (stays leaving per bin) can be small and derivable; the table-mode
  primitive does not band them, the chain mode bands 11-14 to 10 (below k), and the gate does not
  flag it -> v2 MISS-4 (owner decision recorded in the completion note; re-decide at EP-53 / EP-72).

> **Completion note (2026-09-16).** Executed on fixture + dev in the foreground and on full as
> the `measurement-full` background job, as briefed; all three tiers finished in-session. EP-44
> had deferred nothing (its full QC run was verified in its own session), so no EP-44 note is
> appended.
>
> **Shipped.** `src/mimicwarehouse/qc/measurement.py` (`MeasurementParams` - a python step's
> `params`: `itemids` (a subset of the EP-39 catalogue; default every curated item), `hours`
> 168, `days` 14, `first_window_hours` 24, `min_stays` 50, `sparse_share` 0.05; the SQL builders
> over `timesem` - `population_sql`, `occasions_sql` per source, `binned_sql`, `at_risk_sql`,
> `per_stay_day_sql`, `intervals_sql`, `cells_sql` / `measured_cells_sql`, the safe-query-shaped
> `presence_sql`; `compute_hourly` / `compute_structural` / `compute_presence` -> raw slices under
> `lake/meta/<tier>/raw/measurement/`; `flag_cells`, `item_counts`, `presence_rows`, `rate_ratio`
> (statsmodels `Table2x2`, Wald CI); `assemble` with `suppress_binned` / `suppress_items` (the
> additive-identity pass) / `suppress_cells` / `suppress_presence` (the cross-contrast rule); the
> handlers `run_hourly` / `run_structural` / `run_presence` / `run_report` (one `kind: qc` run
> named `measurement`, `kind: query` bench lines, the SQL recorded, the six tables written, the
> report rendered after the run closes); `render_report` / `write_report`;
> `register_measurement`), `dag/specs/measurement.yaml` (hand-written; tag `measurement`),
> `dag.spec.Step.params` (optional, python steps only), `catalog.build.CATALOG_EXTENSIONS +=
> register_measurement` (after `register_qc`), `mwh qc measurement --tier t [--top N] [--json]`
> (`qc/cli.py`), `docs/methods/measurement-process.md`, `tests/ep/test_ep45.py` (16 fixture + 1
> dev tests), the README state row + quick start, the DESIGN §14 note + §15 map, the D-33 and
> D-5 addenda, a gotchas entry, the `docs/analyses/README.md` index row, `final-roadmap.md`
> MISS-3 / MISS-4, roadmap Risk 17.
>
> **As built vs the brief.** (1) The three compute steps write **raw slices** and
> `measurement.report` assembles, suppresses and publishes the six `meta.mp_*` tables inside the
> one `kind: qc` run before rendering the report (EP-44's `qc.checks` + `qc.report` folded into
> one step), so `--select measurement.report,catalog` re-suppresses without a rescan; the run's
> reproduction block carries the closed run's numbers. (2) The item summary and the absence
> summary are suppressed as **one** frame with an additive-identity pass, statistics are blanked
> where `n_stays_measured` is hidden, flags where the cell's measured count is hidden, and the
> presence arms hide across the two contrasts (DESIGN §14 note; D-33 addendum). (3) Names:
> `share_measured_first_24h` is **`measured_first_24h_share`** (the `_share` suffix is what
> `disclose.suppress` blanks beside a hidden count); the "note" column of `mp_presence_outcome` is
> **`caveat`** (`note` is a free-text column name for the gate) beside `claim_type`; the
> per-stay-day and interval statistics are `median_per_stay_day`, `p10_interval_min`,
> `median_interval_min`, `p90_interval_min`. (4) A *measurement occasion* is a distinct `(stay,
> itemid, charttime)` (chartevents' upstream duplicates count once) inside `[intime, outtime)`;
> `labevents` attach by subject + window (no `stay_id` upstream); the daily grid covers 14 ICU
> days (the brief fixed only the 168 hours). (5) The population is every ICU stay with a valid
> window and a `patients` row (not adults only, not first stays only); `n_structural` is the
> number of stays in the item's structural cells (a structural cell has no measurement at all),
> so the per-stay attribution needs no second scan. (6) The mortality-rate arms are computed on
> the build connection with the `count(*) FILTER` shape `safe_query` admits (`test_ep45` runs
> `presence_sql` through `safe_query` on the fixture catalog); the rate ratio and its CI are
> Python over the released counts. (7) `--tag measurement` also selects the mimic-code
> **`measurement/` concept group** (the concept steps carry their group name as a tag): they are
> skipped when complete, which they are on every tier, but a fresh lake would build the sixteen
> concepts first (gotchas §3; EP-54 may rename one of the tags). (8) Full-tier scans took
> seconds, not the brief's 5-20 min: one temp table of occasions per source, then cheap
> aggregates.
>
> **Runs.** Dev: job `measurement-dev` -> build run `20260916T193950Z-6a537d` (wall 12 s
> incl. catalog; steps hourly 0.7 s / structural 0.4 s / presence 0.3 s / report 0.9 s, peak
> RSS 604 MB) -> qc run `20260916T193956Z-60492c` (63 itemids; hourly 10,584 / daily 882 /
> summary 63 / structural 3,339 / absence 63 / presence 120 rows; 181 structural cells; wall
> 0.8 s, peak RSS 263 MB, disk delta 0.2 MB). Full: job `measurement-full`
> (`runs\jobs\measurement-full.log`) -> build run `20260916T194120Z-a8ee17` (wall 42 s incl.
> catalog; hourly 15.6 s at 4,481 MB, structural 14.9 s at 8,068 MB (`peak_wset`), presence
> 1.0 s, report 1.0 s) -> qc run `20260916T194157Z-25a7ea` (population 94,444 ICU stays, 14
> `icustays` rows excluded for a missing or inverted window; hourly 10,584 / daily 882 / summary
> 63 / structural 4,473 / absence 63 / presence 120 rows; 119 structural cells; wall 0.9 s, peak
> RSS 384 MB, disk delta 1.4 MB). `mwh disclose check` passes on `measurement_process.md` and all
> six CSVs of both real runs (and of the fixture run, asserted by `test_ep45`).
>
> **What the real tiers say (statuses only - the numbers stay in the reports under `runs/`
> until EP-53 promotes them).** The core chemistry and CBC labs are measured in the first 24 h
> for almost every stay; blood gases and lactate for about half; the sparse labs (HbA1c,
> troponin, albumin, bilirubin) for a minority. The structural cells on the full tier are
> almost all urine-output items other than Foley / Void (ureteral stents, suprapubic,
> nephrostomies, ileoconduit) in particular unit x era cells; the vitals and core labs are in
> use in every cell. The admission-weight (kg) and height items are measured for a small
> minority of stays (the pounds / inches items carry the bulk), and the first-24-h counts of
> the rarest urine items are suppressed on both tiers. The informative-presence contrasts are
> descriptive only (the report says so per row).
>
> **Acceptance.** `uv run poe test -m ep_45`: 16 passed on fixture; `--tier dev`: 17 passed
> (the dev probe: every curated itemid present, nothing below k released); `uv run mwh verify
> EP-45`: 16 passed; `uv run --group dev mwh build --tier dev --tag measurement` and `mwh qc
> measurement --tier dev` as above; `uv run poe check` (ruff check + format + pyright + the
> whole fixture-tier suite): green - 1,040 passed, 47 deselected (541 s); `mwh guard` clean
> over every changed file; `poe roadmap-check --strict` 0 errors / 0 warnings. **Earlier
> tests edited: `test_ep44`** (one
> pin, per the roadmap's count-pin rule): its session-lake test pinned the `kind: query`
> benchmark ledger to exactly the 31 `qc.profile.*` lines, and EP-45's three compute steps
> write `kind: query` lines too (the EP-33 amendment above prescribes the kind), so the pin
> now filters to the `qc.profile.*` steps; `mwh verify EP-44` still exits 0. The session
> fixture lake now runs the four measurement steps too; `test_ep44`'s `CATALOG_EXTENSIONS`
> order pins hold (`register_measurement` sits right after `register_qc`).
>
> **Owner decisions (2026-09-16, interactive at completion).** (1) Commit in the two-step
> recipe, no push - done as recorded in the roadmap table. (2) The at-risk-curve residual
> (D-33 addendum, Risk 17, MISS-4) stays documented and is re-decided at EP-53 / EP-72; the
> two alternatives offered (a chain-style post-pass hiding an at-risk cell whose step to its
> published neighbour is below k; dropping `n_stays_at_risk` from the published grids) were
> declined for now. (3) The DAG tag stays the brief's `measurement` despite the overlap with
> the mimic-code `measurement/` concept group (gotchas §3); a rename to `measurement-process`
> was declined - EP-54 may rename the concept group tags instead.
