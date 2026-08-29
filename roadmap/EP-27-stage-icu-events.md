# EP-27 — Stage icu event tables ⏱

**Size:** M · **Tier:** fixture+dev (full ⏱ → verified by EP-28) · **Core/Stretch:** core · **Depends on:** EP-19 (DAG runner `mwh build`) · **Blocks:** EP-28 (Verify full staging), EP-33 (Re-plan P2)

> **Amended at EP-170 (2026-08-29).** Header facts unchanged; shorthand per the README notation
> table. (1) The contract already carries the tie-broken `sort_keys` item 1 expects (EP-169) —
> item 1 is a *verify*, not a contract edit [FC-3]. (2) The `identifier`/`free_text` flag
> machinery ships with EP-17; item 2 only *verifies* these tables' flags (`orderid`,
> `linkorderid`, `caregiver_id` identifiers) — a missing flag is fixed with a dated note; flag
> edits move `content_hash()` only (the fixture manifest pins `structural_hash()`, EP-169)
> [FC-5]. (3) The dev-marked test requests EP-168's `dev_ready("<step>")` readiness fixture per
> step (skip-with-reason until the status entry exists), so "green once dev-ready" is real, not
> vacuous [VT-1]. (Note `ingredientevents` has no `validate.sql` expectation — see the EP-20/
> EP-28 amendments [FC-12].)

## Context

Completes `mimiciv_icu`: `inputevents.csv` (~2.7 GB; infusions/boluses with `starttime`,
`endtime`, `amount`, `rate`, `orderid`, `linkorderid`), `ingredientevents.csv` (~2.4 GB;
the ingredient breakdown of the same orders) and `datetimeevents.csv` (~1.1 GB; charted
date/time observations whose `value` is itself a shifted TIMESTAMP). Together with EP-20
(`d_items, caregiver, icustays, procedureevents, outputevents`) and EP-26 (`chartevents`)
every one of the 9 icu tables in the contract is then staged. Layout per DESIGN §5
(**D-17**, **D-18**) through the loader (EP-17/18) and `mwh build` (EP-19); one background
job with sequential steps; dev buckets sorted first. Time semantics reminder for later
briefs (DESIGN §7): all timestamps are per-patient shifted, so these tables are only ever
analysed with within-patient relative times. `orderid`, `linkorderid` and `caregiver_id`
are identifiers for `safe_query` (EP-30). EP-28 records timings and appends the completion
note.

## In scope

1. **DAG steps** (tags `[icu-events, icu]`, tiers `[fixture, dev, full, demo]`) —
   `stage.mimiciv_icu.inputevents`, `stage.mimiciv_icu.ingredientevents`,
   `stage.mimiciv_icu.datetimeevents`. Expected contract values (EP-9; fix there with a
   dated note if different): all three `load_class: large`, partitioned; `sort_keys` after
   `subject_id`: inputevents `[starttime, orderid]`, ingredientevents `[starttime, orderid]`,
   datetimeevents `[charttime, itemid]`.
2. **Contract check + flags** — typed load with `loader_reject_max = 0` (`datetimeevents.value`
   TIMESTAMP, `inputevents.amount/rate/originalamount/originalrate` DOUBLE,
   `patientweight` DOUBLE, `statusdescription`, `ordercategoryname` VARCHAR labels);
   `orderid`, `linkorderid`, `caregiver_id` in `keys.yaml`'s identifier list (dated note).
3. **Launch** —
   `uv run --group dev mwh build --tier full --tag icu-events --background --job stage-icu-events-full`;
   `> **Launch note (date).**` with job name, log path
   (`%MWH_DATA_ROOT%\runs\jobs\stage-icu-events-full.log`), build id, start time; poll with
   `mwh jobs --job stage-icu-events-full --tail 5`; expected well under an hour; EP-28
   verifies. Do not launch while EP-26's chartevents job is running (`mwh jobs` shows it) —
   the build lock will refuse and the job will show `failed`; wait or sequence.
4. **Coverage assertion** — extend the coverage test so the union of `stage.yaml` icu steps
   (`small` from EP-20, `chartevents` from EP-26, `icu-events` here) equals the 9 icu tables
   of the contract exactly once, and the whole DAG covers all 31 hosp + icu tables.
5. **Fixture tests** (`tests/ep/test_ep27.py`, `@pytest.mark.ep_27`) — fixture build of the
   three steps via the runner: layout, sortedness, manifests, status, ledger lines; on the
   fixture, every `inputevents.stay_id` exists in `icustays` and every
   `ingredientevents.orderid` exists in `inputevents` (unmatched counts 0).
6. **Dev-marked test** (skips until `dev_ready`) — via `open_catalog("dev")`: counts positive
   and equal to the dev-bucket manifest sums; unmatched-key counts for the two joins above
   reported (counts only); `datetimeevents` `count(*) WHERE value IS NULL` reported.

## Out of scope

- Timing/RSS/disk recording, completion note → EP-28.
- Vasopressor / ventilation concepts over these tables → EP-37/38; unit harmonization → EP-39; first-day marts → EP-55.
- Referential-integrity suites → EP-44.

## Verification / acceptance

- `uv run poe test -m ep_27` green on fixture; `tier("dev")`-marked test green once `dev-ready` (or recorded as pending for EP-28); `uv run --group dev mwh verify EP-27` green.
- Launched `mwh build --tier full …` **in the background**; log at `%MWH_DATA_ROOT%\runs\jobs\stage-icu-events-full.log`; job id and build id recorded here; timing verified by EP-28.
- The coverage test proves all 31 hosp + icu contract tables are staged by exactly one brief; no rows in logs or tool output.

> **Launch note (2026-08-29).** Job `stage-icu-events-full` (pid 49376) launched first thing
> (right after the one spec edit adding the three `icu-events` steps) via
> `uv run --group dev mwh build --tier full --tag icu-events --background --job stage-icu-events-full`;
> log `%MWH_DATA_ROOT%\runs\jobs\stage-icu-events-full.log`; started 2026-08-29T20:45:45Z;
> build id `20260829T204546-full-5c4c49c`; 3 sequential steps in spec order
> (`inputevents` → `ingredientevents` → `datetimeevents`); **sweeps = 1** (loader default —
> EP-24's VARCHAR-heavy high-water was emar_detail's 24,563 MB peak RSS on an ~8 GB, 33-column
> CSV; these three are ~2.7 / ~2.4 / ~1.1 GB with far fewer VARCHAR columns, so no memory
> pressure expected under the 36 GB `memory_limit`). Pre-flight answers: `mwh doctor` OK
> (9 pass · 0 fail; the `antivirus` warn is the expected D-38\D-42 one) — **393.2 \ 951.5 GB
> free on C:** at start, BitLocker on, Defender exclusion on the owner's word (not readable
> non-elevated, D-42), **AC power mode Best performance**, DuckDB 1.5.5 == pin,
> `memory_limit` 36 GB (build profile) / `threads` 12 / `temp_directory`
> `C:\mimicdata\tmp\duckdb` with explicit `max_temp_directory_size` 150 GB; `mwh jobs`
> showed all prior jobs `done` (EP-26's chartevents job included) and no running build.
> EP-28 verifies timing/RSS/disk and appends the completion note.
>
> The job finished **inside the session**: exit 0 at 2026-08-29T20:46:46Z — total wall 61 s
> against the brief's well-under-an-hour budget. Step summaries from the job table (counts
> only; EP-28 reconciles against `validate.sql` and the benchmark ledger): `inputevents`
> 10,953,713 rows / 28.9 s · `ingredientevents` 14,253,480 rows / 22.2 s · `datetimeevents`
> 9,979,761 rows / 8.1 s — each into 100 sorted `part-0.parquet` buckets; snapshot
> `core/full` `b1fc5313…`. With the job complete, the dev catalog was refreshed
> (`mwh build --tier dev --select catalog`, build `20260829T204959-dev-5c4c49c`, 31 views)
> and the item-6 dev-marked test ran **green for real** (8/8 at `--tier dev`): dev counts
> equal the dev-bucket manifest sums, and all three reported counts are 0 — inputevents
> rows without an icustays row, ingredientevents rows without an inputevents order, and
> datetimeevents rows with NULL `value`.
>
> Verify-only outcome for items 1–2 (as the EP-170 amendment predicted): the contract
> already carried the tie-broken sort keys, types and `load_class: large` for all three
> tables, `ingredientevents.expected_rows_source: null` [FC-12], and `keys.yaml` already
> listed `orderid`/`linkorderid`/`caregiver_id` as identifiers — zero contract/keys edits,
> no dated notes needed. Touched earlier-EP module (README CMP-6 rule): the rolling probe
> in `tests/ep/test_ep21.py::test_catalog_info_cli` pinned `datetimeevents` as the catalog's
> `missing`-kind witness "until EP-27 stages it"; with all 31 hosp+icu tables now staged it
> asserts `datetimeevents` is a view and that no `missing` entry remains. `mwh verify EP-21`
> re-run green (12/12).

> **Completion note (2026-08-29, EP-28 verification).** Ledger-verified from
> `runs/benchmarks.jsonl` via `dag.benchmarks.summarize()` — job `stage-icu-events-full`,
> build `20260829T204546-full-5c4c49c`:
>
> | table | pass 1 s | pass 2 s | total s | peak RSS MB | CSV bytes in | Parquet bytes out | MB/s | rows |
> |---|---:|---:|---:|---:|---:|---:|---:|---:|
> | inputevents | 13.9 | 14.9 | 28.9 | 3,539 | 2,868,896,449 | 287,752,892 | 99.1 | 10,953,713 |
> | ingredientevents | 10.3 | 11.8 | 22.2 | 2,821 | 2,472,156,247 | 214,569,172 | 111.3 | 14,253,480 |
> | datetimeevents | 3.1 | 4.9 | 8.1 | 626 | 1,092,342,579 | 48,664,346 | 134.7 | 9,979,761 |
>
> 100 sorted `part-0.parquet` files each; inputevents and datetimeevents row counts ==
> the vendored `validate.sql` expectations == the EP-10 raw counts; ingredientevents has
> no `validate.sql` row [FC-12] and reconciles against the EP-10 raw count alone (match);
> rejects 0; dev ⊂ full confirmed. All three show pass 2 > pass 1 (the EP-26 parallel-sort
> trigger pattern; EP-33 decides).
