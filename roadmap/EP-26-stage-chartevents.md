# EP-26 — Stage chartevents ⏱

**Size:** L · **Tier:** fixture+dev (full ⏱ → verified by EP-28) · **Core/Stretch:** core · **Depends on:** EP-19 (DAG runner `mwh build`) · **Blocks:** EP-28 (Verify full staging), EP-33 (Re-plan P2)

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

## Parked → final-roadmap.md

- Parallel per-bucket sorting on multiple cursors — trigger: EP-28 shows pass 2 wall time ≥ pass 1 for chartevents.
