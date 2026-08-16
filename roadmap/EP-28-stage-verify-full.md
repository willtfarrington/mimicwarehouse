# EP-28 — Verify full staging

**Size:** S · **Tier:** fixture+dev+full · **Core/Stretch:** core · **Depends on:** EP-20 (Stage dimensions + small hosp/icu tables), EP-21 (Catalog builder (per-tier .duckdb)), EP-22 (Demo tier (MIMIC-IV Demo 2.2 + ED Demo)), EP-23 (Stage labevents ⏱), EP-24 (Stage emar + emar_detail ⏱), EP-25 (Stage remaining hosp tables ⏱), EP-26 (Stage chartevents ⏱), EP-27 (Stage icu event tables ⏱) · **Blocks:** EP-32 (Capstone #0: staging benchmark note + docs/analyses convention), EP-33 (Re-plan P2), EP-158 (Bootstrap `mwh init` + cloner smoke test on demo tier)

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
