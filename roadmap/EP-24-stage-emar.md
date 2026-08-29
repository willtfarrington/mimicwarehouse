# EP-24 — Stage emar + emar_detail ⏱

**Size:** M · **Tier:** fixture+dev (full ⏱ → verified by EP-28) · **Core/Stretch:** core · **Depends on:** EP-19 (DAG runner `mwh build`) · **Blocks:** EP-28 (Verify full staging), EP-33 (Re-plan P2)

> **Amended at EP-170 (2026-08-29).** Header facts unchanged; shorthand per the README notation
> table. (1) The contract already carries the tie-broken `sort_keys` item 1 expects (EP-169) —
> item 1 is a *verify*, not a contract edit [FC-3]. (2) The `identifier`/`free_text` flag
> machinery ships with EP-17; items 1–2 only *verify* these tables' flags (`emar_id`, `poe_id`,
> `pharmacy_id`, `enter_provider_id` identifiers) — a missing flag is fixed with a dated note;
> flag edits move `content_hash()` only (the fixture manifest pins `structural_hash()`, EP-169)
> [FC-5]. (3) The dev-marked test requests EP-168's `dev_ready("<step>")` readiness fixture per
> step (skip-with-reason until the status entry exists), so "green once dev-ready" is real, not
> vacuous [VT-1].

## Context

The electronic medication administration record: `emar.csv` (~6 GB) and `emar_detail.csv`
(~8 GB), ~14 GB combined, both subject-keyed and joined on the composite key
`(emar_id, emar_seq)`. `emar_id` is a VARCHAR that embeds the subject id
(`'<subject_id>-<n>'`), so it is an **identifier column** for `safe_query` (EP-30) and
must be listed as such in `keys.yaml`. `emar_detail` has **no timestamp of its own** — its
time is `emar.charttime` via the join — so its sort key is the natural key rather than
`(subject_id, time)`; DESIGN §7 allows that (natural keys). Both load through the two-pass
bucketed path (EP-18) via `mwh build` (EP-19) as one background job (single writer:
sequential steps). Layout per DESIGN §5 (**D-17**, **D-18**); dev buckets first. EP-28
records timings and appends this brief's completion note. Free-text-ish columns
(`emar_detail.reason_for_no_barcode`, `product_description*`) are drug/administration
labels, not notes; `safe_query`'s long-string heuristic covers them.

## In scope

1. **DAG steps** — `stage.mimiciv_hosp.emar` and `stage.mimiciv_hosp.emar_detail`
   (`depends_on: [stage.mimiciv_hosp.emar]`), tags `[large, hosp, emar]`, tiers
   `[fixture, dev, full, demo]`, sources `mimic-iv-3.1/hosp/emar.csv` / `emar_detail.csv`.
   Expected contract values (EP-9; fix there with a dated note if different): both
   `load_class: large`, partitioned; `sort_keys` `[subject_id, charttime, emar_seq]` for
   `emar` and `[subject_id, emar_id, emar_seq, parent_field_ordinal]` for `emar_detail`.
2. **Contract check** — types per EP-9 (`emar_seq` INTEGER, `pharmacy_id`, `poe_id`,
   `enter_provider_id` identifiers, `charttime`/`scheduletime`/`storetime` TIMESTAMP,
   `emar_detail.parent_field_ordinal` DOUBLE, dose/rate columns as declared); add
   `emar_id`, `poe_id`, `pharmacy_id`, `enter_provider_id` to the identifier list in
   `keys.yaml` if missing (dated note). `loader_reject_max = 0`.
3. **Launch** —
   `uv run --group dev mwh build --tier full --tag emar --background --job stage-emar-full`
   at the start of the session; `> **Launch note (date).**` with job name, log path
   (`%MWH_DATA_ROOT%\runs\jobs\stage-emar-full.log`), build id, start time; poll with
   `mwh jobs --job stage-emar-full --tail 5`. Expected: both tables complete within about an
   hour; do not wait past the session (EP-28 verifies).
4. **Fixture tests** (`tests/ep/test_ep24.py`, `@pytest.mark.ep_24`) — fixture build of both
   steps via the runner into `tmp_path`: layout/sortedness/manifests/status as in EP-18;
   ledger lines; `emar_detail` rows all join to an `emar` row on `(emar_id, emar_seq)`
   (a `count(*)` of unmatched rows equals 0 — on fixture data, in-process).
5. **Dev-marked test** (skips until `dev_ready`) — via `open_catalog("dev")`: both counts
   positive and equal to the dev-bucket manifest sums; unmatched `emar_detail` rows on the
   dev tier counted and reported (a count; expected 0 or a small documented number — record
   it, do not fail); sortedness of `emar` by `subject_id, charttime` from row-group stats.
6. **Notes for EP-28** — throughput, RSS and `tmp\duckdb` high-water for a VARCHAR-heavy
   table (emar_detail) captured from the log into the launch note.

## Out of scope

- Timing/RSS/disk recording and completion note → EP-28.
- Medication code sets, drug-name/RxNorm mapping → EP-40, EP-143; antibiotics-timing workflows → EP-86/EP-144.
- Referential-integrity suites across all tables → EP-44.

## Verification / acceptance

- `uv run poe test -m ep_24` green on fixture; `tier("dev")`-marked test green once `dev-ready` (or recorded as pending for EP-28); `uv run --group dev mwh verify EP-24` green.
- Launched `mwh build --tier full …` **in the background**; log at `%MWH_DATA_ROOT%\runs\jobs\stage-emar-full.log`; job id and build id recorded here; timing verified by EP-28.
- `keys.yaml` lists `emar_id` as an identifier (checked by a test); no rows in logs or tool output.

> **Launch note (2026-08-29).** Job `stage-emar-full` (pid 11592) launched first thing via
> `uv run --group dev mwh build --tier full --tag emar --background --job stage-emar-full`;
> log `%MWH_DATA_ROOT%\runs\jobs\stage-emar-full.log`; started 2026-08-29T19:38:53Z;
> build id `20260829T193854-full-182dcff` (2 steps: emar, then emar_detail — sequential,
> single writer). `mwh doctor` confirmed AC power mode *Best performance* before launch.
> Timing/RSS/disk recording and count reconciliation land with EP-28 per the header.
>
> The job finished **inside the session**: exit 0 at 2026-08-29T19:50:31Z — total wall
> 698 s against the brief's about-an-hour budget. VARCHAR-heavy planning inputs for EP-28
> (item 6; from the job log — EP-28 verifies against the benchmark ledger):
>
> - **emar** (~6 GB CSV, 12 columns): step wall 64.7 s; dev-ready 31 s after step start
>   (2026-08-29T19:39:25Z); 42,808,593 rows → 643,479,589 bytes across 100 sorted
>   `part-0.parquet` (pass-2 buckets ~0.3–0.4 s each); peak RSS 6,853 MB (runner sampler).
> - **emar_detail** (~8 GB CSV, 33 columns, VARCHAR-heavy): step wall **630.1 s** —
>   pass 1 ≈ 439 s (≈ 19 MB/s, roughly 15× slower than labevents' 285 MB/s pass 1: the
>   wide all-VARCHAR row dominates), dev-ready at 2026-08-29T19:47:28Z, pass 2 ≈ 191 s
>   (100 buckets at ~1.8–2.2 s, ≈ 0.8–0.97 M rows each); 87,371,064 rows →
>   670,346,169 bytes across 100 sorted buckets; **peak RSS 24,563 MB** (runner sampler —
>   under the 36 GB `memory_limit` but the high-water so far; EP-26/EP-27 should budget
>   for VARCHAR-heavy sorts accordingly). `tmp_duckdb=0` at every 60 s heartbeat sample
>   (no spill observed). FYI (as in EP-23): the pass-1 heartbeat's own rss/written probes
>   read 0 for the first minutes on this host; the runner's psutil sampler is the
>   trustworthy number.
>
> Because the job completed, this session also refreshed the dev catalog
> (`mwh build --tier dev --select catalog`, build `20260829T195232-dev-182dcff`,
> 23 cataloged, snapshot `73ba7d48…`) and ran `poe test -m ep_24 --tier dev`: all
> 6 tests green — the dev-marked test executed for real (dev view counts = manifest rows
> for buckets 0–4 for both tables; per-file row-group `subject_id` ranges monotonic;
> **dev-tier `emar_detail` rows without an `(emar_id, emar_seq)` parent: 0** — recorded
> per item 5, not failed on). Full-suite regression: 631 passed on fixture.
>
> Brief-vs-shipped deviation (dated note per item 2): the brief's expectation
> `emar_detail.parent_field_ordinal` **DOUBLE was rejected** — the vendored mimic-code
> `postgres/create.sql` declares `VARCHAR(10)` and the EP-9 contract already says VARCHAR
> (as DOUBLE, ordinals like '1.1' and '1.10' would collide); no contract edit was needed,
> `test_ep24` pins VARCHAR. `keys.yaml` already carried all four identifier names
> (`emar_id`, `poe_id`, `pharmacy_id`, `enter_provider_id`) — verify only, no dated
> keys.yaml note needed. No new free-text flags: the dose/product/barcode varchars are
> administration labels (brief Context; safe_query's long-string heuristic covers them).
