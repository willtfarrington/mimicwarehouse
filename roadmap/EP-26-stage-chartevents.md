# EP-26 — Stage chartevents ⏱

**Size:** L · **Tier:** fixture+dev (full ⏱ → verified by EP-28) · **Core/Stretch:** core · **Depends on:** EP-19 (DAG runner `mwh build`) · **Blocks:** EP-28 (Verify full staging), EP-33 (Re-plan P2)

> **Amended at EP-170 (2026-08-29).** Header facts unchanged; shorthand per the README notation
> table. (1) The contract already carries the tie-broken `sort_keys` item 1 expects (EP-169:
> `[subject_id, charttime, itemid]` with the tie-break adopted) — item 1 is a *verify*, not a
> contract edit [FC-3]. (2) The `identifier`/`free_text` flag machinery ships with EP-17; item 1
> only *verifies* this table's flags (`caregiver_id` identifier; `value` unflagged) — a missing
> flag is fixed with a dated note; flag edits move `content_hash()` only (the fixture manifest
> pins `structural_hash()`, EP-169) [FC-5]. (3) The dev-marked test requests EP-168's
> `dev_ready("stage.mimiciv_icu.chartevents")` readiness fixture (skip-with-reason until the
> status entry exists), so "green once dev-ready" is real, not vacuous [VT-1].

## Context

`chartevents.csv` is the largest single file (40 GB, on the order of 4 × 10^8 rows —
`validate.sql` has the exact count): bedside charted observations keyed by
`(subject_id, hadm_id, stay_id, charttime, itemid)` with `value` VARCHAR, `valuenum`
DOUBLE, `valueuom`, `warning`, `storetime`, `caregiver_id` (identifier). It is the reason
the loader has a two-pass bucketed path (EP-18), the runner has background jobs (EP-19)
and the machine has a ≥ 100 GB free rule: expect roughly an hour for the streaming
partitioned pass plus tens of minutes of per-bucket sorting, a Parquet output around a
quarter of the CSV size, DuckDB `memory_limit` 36–40 GB, `temp_directory` under the data
root with an explicit `max_temp_directory_size`, and a build-temp peak that may reach
60–100 GB (DESIGN §3). EP-23 (labevents) was the dress rehearsal — its launch note holds
the throughput/RSS/temp inputs to scale by ~2.2. Layout per DESIGN §5 (**D-17**, **D-18**);
buckets 0–4 sorted first so the dev tier is usable early. Raw `chartevents` is deliberately
excluded from the events spine (DESIGN §10) — that is a later question, not this brief's.
Laptop thermals and Defender scanning slow long jobs; the owner decided the Defender
exclusion (**D-38**), `mwh doctor` records its state. EP-28 records timing, peak RSS and disk
and appends this brief's completion note. Size L covers pre-flight, launch, babysitting
and contingencies — not new machinery.

## In scope

1. **DAG step + contract** — `stage.mimiciv_icu.chartevents`
   (`source: mimic-iv-3.1/icu/chartevents.csv`, tags `[large, icu, chartevents]`, tiers
   `[fixture, dev, full, demo]`); the contract (EP-9) must say `load_class: large`,
   partitioned, `sort_keys: [subject_id, charttime, itemid]`. Confirm the contract types load with
   `loader_reject_max = 0`; `caregiver_id` in the identifier list; `value` stays VARCHAR
   (mixed numeric/categorical; short strings — no free-text flag).
2. **Pre-flight (record the answers in the launch note)** — `uv run --group dev mwh doctor`:
   free space ≥ 100 GB **plus** the expected temp peak (target ≥ 200 GB free before
   starting; if less, stop and ask the owner), BitLocker on, Defender exclusion state,
   power plan, DuckDB version; `mwh jobs` shows no running build; the app and notebooks
   are closed (single writer, and readers block the catalog swap later); settings show
   `memory_limit` 36–40 GB, `threads` 12, `temp_directory` `C:\mimicdata\tmp\duckdb`,
   `max_temp_directory_size` explicit (e.g. `'150GB'`); read EP-23's planning inputs and
   choose `--sweeps` (default 1; use 2–4 only if EP-23 showed memory pressure in pass 1 —
   each sweep re-scans the CSV).
3. **Launch first thing** —
   `uv run --group dev mwh build --tier full --select stage.mimiciv_icu.chartevents --background --job stage-chartevents-full`;
   `> **Launch note (date).**` with job name, log path
   (`%MWH_DATA_ROOT%\runs\jobs\stage-chartevents-full.log`), build id, start time, sweeps,
   free space at start. Poll `mwh jobs --job stage-chartevents-full --tail 5` every 10 min;
   the loader's 60 s progress lines give RSS, `tmp\duckdb` size and bytes written. Note the
   time of `pass1 done`, `dev-ready` and, if reached, `complete`; do not wait past the
   session (EP-28 verifies).
4. **Contingencies (documented in the brief and applied if they occur)** — free-space guard
   trips mid-run → the job stops cleanly; free space (never by deleting raw CSVs, D-30),
   then rerun the same command: pass 1 done → resumes at the next unsorted bucket; pass 1
   not done → restarts pass 1 (rerun with `--sweeps 4` if the failure was memory);
   a `_sorting.tmp` leftover is discarded automatically. Thermal throttling suspected
   (throughput halves) → note it, do not intervene. Machine sleep → the job is lost;
   rerun (owner sets the power plan, D-38).
5. **Fixture tests** (`tests/ep/test_ep26.py`, `@pytest.mark.ep_26`) — fixture
   `chartevents` through the large path with `sweeps=1` and `sweeps=3`: identical per-bucket
   sha256 sets, sortedness, manifests, status, ledger `phase` lines; the runner's progress
   lines contain no row values (assert the log matches only the expected line patterns).
6. **Dev-marked test** (skips until `dev_ready`) — via `open_catalog("dev")`:
   `count(*)` positive and equal to the dev-bucket manifest sums; row-group `subject_id`
   min/max non-decreasing per file; `count(*) WHERE stay_id IS NULL` reported (a count).
   Add the measured file count for the table (100 partition files) to the launch note for
   EP-28's DESIGN §21 measurement.

## Out of scope

- Timing/RSS/disk recording, completion note, count reconciliation → EP-28.
- Vitals subset in the events spine → EP-50 / re-plan (DESIGN §21); itemid curation → EP-39; latency marts over chartevents → EP-55/56.
- Any change to the bucket scheme → EP-33 after EP-28's measurement.

## Verification / acceptance

- `uv run poe test -m ep_26` green on fixture; `tier("dev")`-marked test green once `dev-ready` (or recorded as pending for EP-28); `uv run --group dev mwh verify EP-26` green.
- Launched `mwh build --tier full …` **in the background** after the pre-flight checklist; log at `%MWH_DATA_ROOT%\runs\jobs\stage-chartevents-full.log`; job id, build id, sweeps and free-space-at-start recorded in the launch note; timing verified by EP-28.
- Free space never dropped below 100 GB during the session (checked with `mwh doctor` at the end and noted); no foreground scan; no rows in logs or tool output.

> **Launch note (2026-08-29).** Job `stage-chartevents-full` (pid 47936) launched first thing
> (right after the one spec edit adding `stage.mimiciv_icu.chartevents`) via
> `uv run --group dev mwh build --tier full --select stage.mimiciv_icu.chartevents --background --job stage-chartevents-full`;
> log `%MWH_DATA_ROOT%\runs\jobs\stage-chartevents-full.log`; started 2026-08-29T20:24:16Z;
> build id `20260829T202417-full-e431ca2`; **sweeps = 1** (EP-23 planning inputs: peak RSS
> 9,827 MB × 2.2 ≈ 22 GB, under the 36 GB `memory_limit` — no memory pressure expected, none
> observed). Pre-flight answers (item 2): `mwh doctor` OK (9 pass · 0 fail; the `antivirus`
> warn is the expected D-38/D-42 one) — **395.4 / 951.5 GB free on C:** at start (≥ the
> 200 GB target), BitLocker on, Defender exclusion on the owner's word (not readable
> non-elevated, D-42), **AC power mode Best performance**, DuckDB 1.5.5 == pin,
> `memory_limit` 36 GB (build profile) / `threads` 12 / `temp_directory`
> `C:\mimicdata\tmp\duckdb` with explicit `max_temp_directory_size` 150 GB; `mwh jobs`
> showed no running build and no app/notebook/duckdb reader processes were open.
>
> The job finished **inside the session**: exit 0 at 2026-08-29T20:26:36Z — total wall
> 137.9 s against the brief's roughly-an-hour planning envelope. Observed (from the job log
> via `mwh jobs --tail`; EP-28 verifies against the benchmark ledger):
>
> - **Pass 1** (streaming partitioned COPY, `sweeps=1`): ≈ 46 s (step start
>   20:24:17.975Z → first sorted bucket 20:25:03.8Z); shorter than the 60 s heartbeat
>   interval, so no `pass1` rss/tmp/bytes line fired. That implies a far higher effective
>   source throughput than EP-23's 285 MB/s if the source was the plain 40 GB CSV — EP-28
>   reconciles `bytes_in` from the ledger before reusing either number for planning.
> - **dev-ready** at 20:25:07.7Z — 50 s after step start (buckets 0–4 first).
> - **Pass 2** (per-bucket sort): ≈ 92 s; 100 buckets at 0.7–1.1 s each,
>   3,863,802–4,881,976 rows per bucket. **Pass 2 wall > pass 1 wall** — the trigger of the
>   parked "parallel per-bucket sorting" item (final-roadmap.md, parked by EP-18) reads as
>   fired; EP-28 confirms from the ledger and EP-33 decides.
> - **Peak RSS** 4,729 MB (runner sampler) — well under the 36 GB `memory_limit` and under
>   the ×2.2-scaled 22 GB estimate; `sweeps=1` was the right call.
> - **Output**: 432,997,491 rows — exactly the validate.sql expected count —
>   1,832,286,645 bytes across **100 partition files** (`part-0.parquet` × 100; the
>   DESIGN §21 file-count measurement input for EP-28); free-space guard never tripped.
>
> Because the job completed, this session also refreshed the dev catalog
> (`mwh build --tier dev --select catalog`, build `20260829T202927-dev-e431ca2`, 28
> cataloged, 3 missing = EP-27's remaining icu event tables) and ran
> `poe test -m ep_26 --tier dev`: all 6 tests green — the dev-marked test executed for real
> (dev view count = manifest rows for buckets 0–4; `stay_id IS NULL` count = 0, matching the
> contract NOT NULL; per-file row-group `subject_id` ranges monotonic).
> End-of-session `mwh doctor`: 393.2 / 951.5 GB free — free space never approached the
> 100 GB floor (the whole staged output is 1,832,286,645 bytes and no pass-1 spill was
> observed).

> **Completion note (2026-08-29, EP-28 verification).** Ledger-verified from
> `runs/benchmarks.jsonl` via `dag.benchmarks.summarize()` — job `stage-chartevents-full`,
> build `20260829T202417-full-e431ca2`: **pass 1 44.6 s · pass 2 93.0 s · total 137.8 s ·
> peak RSS 4,729 MB**; ledger `bytes_in` = 41,935,806,083 (the plain CSV — the source
> really was the 41.9 GB file), so pass 1 streamed at ≈ 940 MB/s (vs labevents' ≈ 296
> MB/s pass 1: chartevents' narrow row parses far faster per byte); 304.4 MB/s over the
> whole step → 1,832,286,645 Parquet bytes across 100 sorted `part-0.parquet` files
> (22.9× compression, the best of the lake). **432,997,491 rows == the vendored
> `validate.sql` expectation == the EP-10 raw count**; rejects 0; dev ⊂ full confirmed.
> **Pass 2 > pass 1 confirmed from the ledger (93.0 vs 44.6 s)** — the parked
> parallel-per-bucket-sorting trigger below is fired (and not chartevents-specific:
> labevents, emar, poe and all three EP-27 tables show pass 2 ≥ pass 1 too); EP-33
> decides.

## Parked → final-roadmap.md

- Parallel per-bucket sorting on multiple cursors — trigger: EP-28 shows pass 2 wall time ≥ pass 1 for chartevents.
