# EP-28 — Verify full staging

**Size:** S · **Tier:** fixture+dev+full · **Core/Stretch:** core · **Depends on:** EP-20 (Stage dimensions + small hosp/icu tables), EP-21 (Catalog builder (per-tier .duckdb)), EP-22 (Demo tier (MIMIC-IV Demo 2.2 + ED Demo)), EP-23 (Stage labevents ⏱), EP-24 (Stage emar + emar_detail ⏱), EP-25 (Stage remaining hosp tables ⏱), EP-26 (Stage chartevents ⏱), EP-27 (Stage icu event tables ⏱) · **Blocks:** EP-32 (Capstone #0: staging benchmark note + docs/analyses convention), EP-33 (Re-plan P2), EP-158 (Bootstrap `mwh init` + cloner smoke test on demo tier)

> **Amended at EP-170 (2026-08-29).** Header facts unchanged; shorthand per the README notation
> table. (1) Item 3 reconciles via `inventory.reconcile()` / `expected_counts()` semantics: an
> upstream expectation exists only where the contract's `expected_rows_source` is set —
> `provider`, `caregiver` and `ingredientevents` have none and reconcile against EP-10's
> raw-manifest rows only. Strike the "recorded 3.1 delta" clause: EP-10 found every upstream
> count matches (34 match · 0 mismatch · 7 without expectation) [FC-12]. (2) No re-run of the
> EP-17 … EP-20 suites at `--tier full` is needed here: since EP-168 the tier ladder is gone —
> those tests key on requestable readiness fixtures (`raw_root`, `dev_ready(step)`,
> `full_catalog`) and were exercised when their EPs ran; this brief's own `tier("full")` checks
> subsume the staging-state assertions.

## Context

The verifying brief for every ⏱ staging job in P2 (**D-18**: long full jobs are resumable
background jobs verified by the next EP). Five background jobs (`stage-labevents-full`,
`stage-emar-full`, `stage-hosp-rest-full`, `stage-chartevents-full`,
`stage-icu-events-full`) plus EP-20's small tables should now have staged all 31 hosp + icu
tables into `lake/core`; this brief proves it, reconciles counts against `validate.sql`
(EP-8/EP-10, **D-26**) and EP-10's raw manifest, proves that dev is a strict subset of full,
measures disk and file counts (DESIGN §3 budget, §21 bucket-count question), moves the
timings into the benchmark ledger and writes the completion notes on the ⏱ briefs. Sources
of truth: `lake/manifests/status.json`, `lake/manifests/*.jsonl`, `runs/benchmarks.jsonl`,
`runs/jobs/*.json|.log`. Everything here is metadata and counts; the only data scans are
partition-pruned `count(*)` calls on the dev catalog.

## In scope

1. **Job triage** — `mwh jobs` for the five jobs: any `failed`/incomplete → rerun the same
   `mwh build … --background` command (resume) and wait; a table still not `complete`
   after that is a blocker for EP-33, not something to work around here.
2. **Structural checks** (`tests/ep/test_ep28.py`, `@pytest.mark.ep_28`, `tier("full")`-marked) —
   all 31 contract tables have `tier_complete = "full"`; every partitioned table directory
   holds ≤ 100 `subject_bucket=<n>` dirs each with exactly one `part-0.parquet` and no
   `raw_*`/`_sorting.tmp`/`.new` leftovers; dims have one file; every manifest line's path
   exists and its `bytes` equals the file size; `snapshots.json` has a `full` entry newer
   than the last job.
3. **Count reconciliation** — for each table: `status.json.rows` == `validate.sql` expected
   (or the recorded 3.1 delta from EP-10) == EP-10 raw manifest rows; rejects 0; output a
   `(table, expected, lake, ok)` table. **dev ⊂ full**: for each partitioned table the
   `count(*)` through `open_catalog("dev")` equals the sum of manifest rows for buckets 0–4
   (no full-tier scan needed); dims identical in both catalogs. Rebuild both catalogs first
   so all 31 tables are present:
   `uv run --group dev mwh build --tier dev --select catalog` and
   `uv run --group dev mwh build --tier full --select catalog --background --job catalog-full-p2`.
4. **Disk + files** — bytes per table/schema/lake total from manifests; CSV bytes from
   EP-10; compression ratio; free space now (`mwh doctor`); file and directory counts under
   `lake/core` and one timed `os.scandir` sweep; append `kind: verify` lines to
   `runs/benchmarks.jsonl` (`mimicwarehouse.dag.benchmarks.append`).
5. **Timings → completion notes** — from the ledger, per ⏱ table: pass 1 wall, pass 2 wall,
   total, peak RSS, bytes in/out, MB/s, files; append `> **Completion note (date).**` blocks
   to EP-23, EP-24, EP-25, EP-26 and EP-27 (and EP-20 if missing) with the numbers, job
   ids and build ids; add `mimicwarehouse.dag.benchmarks.summarize() -> polars.DataFrame`
   used by those notes and by EP-32.
6. **Design notes** — dated notes: `DESIGN.md` §3 (measured core lake size vs the 18–25 GB
   estimate; temp peak observed), §21 (bucket-count measurement: files, scandir time,
   Defender exclusion state; keep or revisit at EP-33), and `roadmap/README.md` § Risks
   (strike-through the disk-budget risk if resolved, hupsim style).

## Out of scope

- Data-quality profiles (null %, ranges, referential integrity across all tables) → EP-29 (meta) and EP-44 (QC).
- The narrative benchmark case study → EP-32; bucket-scheme decisions → EP-33.
- Cloner smoke test on demo → EP-158.

## Verification / acceptance

- `uv run poe test -m ep_28` green with the full tier enabled (`tier("dev")`-marked pieces green too); `uv run --group dev mwh verify EP-28` green.
- Every table shows `ok` in the reconciliation table; `mwh catalog info --tier full` lists 31 tables with none `missing`; job `catalog-full-p2` log at `%MWH_DATA_ROOT%\runs\jobs\catalog-full-p2.log`.
- Completion notes with timings exist on EP-23…EP-27; `runs\benchmarks.jsonl` has `verify` lines; DESIGN §3/§21 notes appended; free space ≥ 100 GB recorded.

> **Completion note (2026-08-29).** Executed in one session (well under the S budget).
> Item 1 was a no-op: `mwh jobs` showed all five ⏱ jobs (`stage-labevents-full`,
> `stage-emar-full`, `stage-hosp-rest-full`, `stage-chartevents-full`,
> `stage-icu-events-full`) already `done` with exit 0 — no reruns. Start-of-session
> `mwh doctor`: AC power mode *Best performance*, 392.7 GB free.
>
> Catalog rebuilds (item 3 lead-in): dev foreground (`20260829T210723-dev-d9a5a5c`, 31
> cataloged, snapshot `f830b941…`) and full as background job **`catalog-full-p2`**
> (pid 28748, exit 0 in ≈ 2 s; log `%MWH_DATA_ROOT%\runs\jobs\catalog-full-p2.log`; 31
> cataloged, snapshot `b1fc5313…` — the same core/full id EP-27 recorded, i.e. the lake
> did not move). `mwh catalog info --tier full`: **31 cataloged (7 tables, 24 views),
> 0 missing**.
>
> Shipped: `dag.benchmarks.summarize()` (per-step ledger pivot: pass1/pass2/total walls,
> RSS, bytes, MB/s, latest build per step) and `tests/ep/test_ep28.py` — 3 fixture tests
> (summarize on the session fixture lake + empty ledger; the structural checker proven on
> synthetic data) and 7 `tier("dev"/"full")` tests covering items 1–5. `poe test -m ep_28
> --tier full`: **10 passed** — all structural checks clean (no `raw_*`/`_sorting.tmp`/
> `.new` leftovers anywhere; every latest manifest line's path exists with matching
> bytes; `snapshots.json` core/full entry newer than the last ⏱ job), reconciliation
> **31/31 ok** (28 tables == the vendored `validate.sql` expectation; provider/caregiver/
> ingredientevents reconcile against the EP-10 raw counts alone [FC-12]; rejects 0
> everywhere; manifest-line sums == `status.json` rows), **dev ⊂ full** (24 partitioned
> tables: dev-catalog `count(*)` == manifest rows for buckets 0–4; 7 dims identical in
> both catalogs). `mwh verify EP-28` green; full `poe check` green (653 fixture tests).
>
> Measured (item 4; `kind: verify` ledger lines appended under a `-verify-` build id, one
> per table + one `verify.lake_core` sweep line, re-appended per full-tier run): core lake
> **7,046,156,578 bytes** vs 97,190,431,138 CSV bytes = **13.8×** compression; **2,407
> part files + 24 `_progress.json` = 2,431 files / 2,433 dirs**, one `os.scandir` sweep
> **0.091 s**; free space **392.8 GB** (≥ 100 GB floor). Item 5: EP-28-verification
> completion notes appended to EP-23 … EP-27 (EP-20 already had one); **EP-26's pass 2 >
> pass 1 trigger confirmed from the ledger (93.0 vs 44.6 s)** — and the pattern holds for
> labevents, emar, poe and all three EP-27 tables; EP-33 decides the parked parallel-sort
> item. Item 6: DESIGN §3 (lake size vs estimate; no temp spill; RSS high-water 24,563 MB)
> and §21 (bucket count settled for P2: keep 100) notes appended; roadmap Risk 6's staging
> portion struck with the measured numbers.
