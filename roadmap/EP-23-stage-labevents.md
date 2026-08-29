# EP-23 — Stage labevents ⏱

**Size:** M · **Tier:** fixture+dev (full ⏱ → verified by EP-28) · **Core/Stretch:** core · **Depends on:** EP-19 (DAG runner `mwh build`) · **Blocks:** EP-28 (Verify full staging), EP-33 (Re-plan P2)

> **Amended at EP-170 (2026-08-29).** Header facts unchanged; shorthand per the README notation
> table. (1) The contract already carries the tie-broken `sort_keys` item 1 expects (EP-169
> adopted them in one edit) — item 1 is a *verify*, not a contract edit [FC-3]. (2) The
> `identifier`/`free_text` flag machinery ships with EP-17; item 2 only *verifies* this table's
> flags (`comments` free text, `order_provider_id` identifier) — a missing flag is fixed with a
> dated note, and flag edits move `content_hash()` only (the fixture manifest pins
> `structural_hash()`, EP-169) [FC-5]. (3) The dev-marked test requests EP-168's
> `dev_ready("stage.mimiciv_hosp.labevents")` readiness fixture (skip-with-reason until the
> status entry exists), so "green once dev-ready" is real, not vacuous [VT-1].

## Context

`labevents.csv` (~18 GB, on the order of 1.5 × 10^8 rows — `validate.sql` has the exact
count) is the second-largest table and the **dress rehearsal for chartevents** (EP-26): the
first real run of the two-pass bucketed loader (EP-18) at scale through `mwh build`
(EP-19), as a logged background job because foreground commands are capped at ~10 min.
Layout per DESIGN §5 (**D-17**, **D-18**): `lake/core/mimiciv_hosp/labevents/subject_bucket=<n>/part-0.parquet`,
sorted `(subject_id, charttime)`, ZSTD-3, ~1 M-row groups; buckets 0–4 are sorted first so
the dev tier becomes usable early. Contract facts to check (EP-9): `labevent_id` PK,
`hadm_id` nullable (outpatient labs), `specimen_id`, `itemid` → `d_labitems`,
`order_provider_id` (identifier), `charttime`/`storetime` (per-patient shifted timestamps),
`value` VARCHAR, `valuenum` DOUBLE, `valueuom`, `ref_range_lower/upper`, `flag`,
`priority`, and **`comments` — free text**, which must be flagged so `safe_query` (EP-30)
refuses it. Timing, peak RSS and disk are recorded by EP-28, which appends this brief's
completion note. Machine facts: 64 GB RAM (`memory_limit` 36–40 GB), one NVMe, ≥ 100 GB
free rule, laptop thermals.

## In scope

1. **DAG step** — add `stage.mimiciv_hosp.labevents` to `stage.yaml`
   (`source: mimic-iv-3.1/hosp/labevents.csv`, tags `[large, hosp]`, tiers
   `[fixture, dev, full, demo]`); the contract (EP-9) must say `load_class: large`,
   `partitioned: true`, `sort_keys: [subject_id, charttime, itemid]` — fix it there with a
   dated note if not (the YAML step carries no overrides).
2. **Contract flags** — in EP-9's YAML mark `labevents.comments` `free_text: true` and
   confirm `order_provider_id` is in `keys.yaml`'s identifier list (dated note in the
   YAML header). Verify the declared types load with `loader_reject_max = 0`; a parse
   failure is fixed in the contract, never by widening to VARCHAR blindly.
3. **Launch the full job first thing** —
   `uv run --group dev mwh build --tier full --select stage.mimiciv_hosp.labevents --background --job stage-labevents-full`;
   append a `> **Launch note (date).**` to this brief with the job name, log path
   (`%MWH_DATA_ROOT%\runs\jobs\stage-labevents-full.log`), start time and build id. Poll
   with `uv run --group dev mwh jobs --job stage-labevents-full --tail 5` every 5–10 min
   while doing items 4–6. Expected: pass 1 in the 10–25 min range, `dev-ready` a few
   minutes later, completion inside the hour — do not wait past the session; EP-28
   verifies and records the numbers.
4. **Fixture tests** (`tests/ep/test_ep23.py`, `@pytest.mark.ep_23`) — fixture
   `labevents` through the large path via the runner
   (`mwh build --tier fixture --select stage.mimiciv_hosp.labevents --data-root <tmp>`):
   layout, one sorted `part-0.parquet` per partition, no `raw_*`/`_sorting.tmp`, manifest
   lines, `status.json` complete, ledger lines with `phase = pass1|pass2|total`; the
   contract shows `comments` as free text.
5. **Dev-marked test** (runs when `status.json` shows `dev_ready`; otherwise skips with a
   clear reason) — through `open_catalog("dev")` (EP-21): `SELECT count(*) FROM
   mimiciv_hosp.labevents` is positive and equals the sum of manifest rows for buckets 0–4;
   per-file `parquet_metadata()` row-group `subject_id` min/max are non-decreasing (sorted);
   `count(*) WHERE hadm_id IS NULL` is positive (outpatient labs exist — a count only).
6. **Rehearsal notes for EP-26** — while the job runs, capture from the log: throughput
   (MB of CSV per second in pass 1), peak RSS, `tmp\duckdb` high-water mark, per-bucket
   sort time; write them into the launch note as "chartevents planning inputs" (EP-26
   scales them ×2.2 and decides on `--sweeps`).

## Out of scope

- Timing/RSS/disk recording, completion note, count reconciliation → EP-28.
- `chartevents` → EP-26; `emar*` → EP-24; other hosp tables → EP-25.
- Lab unit harmonization / plausibility → EP-39; LOINC mapping → EP-143; QC profiles → EP-44.

## Verification / acceptance

- `uv run poe test -m ep_23` green on fixture; the `tier("dev")`-marked test green once `dev-ready` (or documented as pending for EP-28); `uv run --group dev mwh verify EP-23` green.
- Launched `mwh build --tier full …` **in the background**; log at `%MWH_DATA_ROOT%\runs\jobs\stage-labevents-full.log`; job id and build id recorded in the launch note; timing verified by EP-28.
- `mwh jobs --job stage-labevents-full` shows `running` or `done` — never a foreground scan; no rows in the log or tool output.

> **Launch note (2026-08-29).** Job `stage-labevents-full` (pid 35356) launched first thing via
> `uv run --group dev mwh build --tier full --select stage.mimiciv_hosp.labevents --background --job stage-labevents-full`;
> log `%MWH_DATA_ROOT%\runs\jobs\stage-labevents-full.log`; started 2026-08-29T19:23:52Z;
> build id `20260829T192353-full-b22528f`. `mwh doctor` confirmed AC power mode
> *Best performance* and 400 GB free on C: before launch. Timing/RSS/disk recording and
> count reconciliation land with EP-28 per the header.
>
> The job finished **inside the session**: exit 0 at 2026-08-29T19:26:12Z — total wall
> 138.9 s against the brief's inside-the-hour budget. Chartevents planning inputs for
> EP-26 (from the job log; EP-28 verifies against the benchmark ledger):
>
> - **Pass 1** (streaming partitioned COPY, `sweeps=1`): ≈ 62 s for the 17.5 GB CSV
>   ≈ 285 MB/s; the 60 s heartbeat showed 1,265,790,536 raw bytes written and
>   `tmp_duckdb=0` (no spill observed at the single sample).
> - **dev-ready** at 2026-08-29T19:24:59Z — 66 s after step start (buckets 0–4 first).
> - **Pass 2** (per-bucket sort): ≈ 76 s; 100 buckets at ~0.7–0.9 s each,
>   ≈ 1.46 M–1.76 M rows per bucket.
> - **Peak RSS** 9,827 MB (runner sampler) — far under the 36 GB `memory_limit`; scaled
>   ×2.2 for chartevents ≈ 22 GB, so `sweeps=1` looks viable (EP-26 decides). FYI: the
>   pass-1 heartbeat's own ctypes rss probe read 0 on this host; the runner's psutil
>   sampler is the trustworthy number.
> - **Output**: 158,374,764 rows, 1,786,549,301 bytes across 100 sorted
>   `part-0.parquet` files (≈ 10× compression vs the CSV).
>
> Because the job completed, this session also refreshed the dev catalog
> (`mwh build --tier dev --select catalog`, build `20260829T192732-dev-b22528f`, 21
> cataloged) and ran `poe test -m ep_23 --tier dev`: all 5 tests green — the dev-marked
> test executed for real (dev view count = manifest rows for buckets 0–4; outpatient
> `hadm_id IS NULL` count positive; per-file row-group `subject_id` ranges monotonic).
>
> Earlier-test edits (README §"acceptance phrasing", CMP-6): `tests/ep/test_ep20.py` —
> the exactly-20-steps and all-steps-tagged-`small` assertions were made growth-tolerant
> (the spec grows brief by brief per the stage.yaml header; EP-23 added the first
> `large` step), with dated comments. `mwh verify EP-20` still exits 0.

> **Completion note (2026-08-29, EP-28 verification).** Ledger-verified from
> `runs/benchmarks.jsonl` via the new `dag.benchmarks.summarize()` — job
> `stage-labevents-full`, build `20260829T192353-full-b22528f`: **pass 1 62.1 s ·
> pass 2 76.4 s · total 138.7 s · peak RSS 9,827 MB** (runner sampler); 18,402,851,720
> CSV bytes in (132.7 MB/s over the whole step) → 1,786,549,301 Parquet bytes across
> 100 sorted `part-0.parquet` files (10.3× compression). **158,374,764 rows == the
> vendored `validate.sql` expectation == the EP-10 raw count**; rejects 0; dev ⊂ full
> confirmed (dev-catalog `count(*)` = manifest rows for buckets 0–4). FYI for EP-33:
> pass 2 > pass 1 here too (76.4 vs 62.1 s) — the parallel-sort trigger EP-26 parked is
> not chartevents-specific.
