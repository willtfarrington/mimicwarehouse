# EP-18 — Loader core B: subject buckets, sort, resume

**Size:** M · **Tier:** fixture+dev · **Core/Stretch:** core · **Depends on:** EP-17 (Loader core A: typed CSV → Parquet) · **Blocks:** EP-19 (DAG runner `mwh build`), EP-33 (Re-plan P2)

## Context

EP-17 stages a table into a single sorted Parquet file. This brief adds the physical
layout every subject-keyed table uses (DESIGN §5): Hive partitions
`lake/core/<schema>/<table>/subject_bucket=<n>/part-0.parquet` with
`subject_bucket = subject_id % 100`, rows sorted `(subject_id, <time column>)`, ZSTD-3,
~1 M-row row groups. It also implements the two decisions that make the tiers work
(**D-18**): the dev tier is buckets 0–4 of the *same* lake (a partition filter, so dev cannot
drift from full), and large tables (`chartevents` 40 GB, `labevents` ~18 GB,
`emar_detail` 8 GB) load in two passes — a streaming partitioned `COPY`, then a per-bucket
sort — resumable per bucket via a progress file, because foreground shell commands are
capped at ~10 min and laptop jobs die. Everything here is exercised on fixtures and small
real tables; the ⏱ briefs (EP-23…EP-27) run it at scale through `mwh build` (EP-19).
Machine facts: 64 GB RAM (DuckDB `memory_limit` 36–40 GB), one NVMe, ≥ 100 GB free rule,
Windows (`os.replace` for atomic renames; MAX_PATH kept short). DESIGN §21 leaves the
bucket-count trade-off (100 buckets × ~30 tables ≈ 3 000 files vs NTFS/Defender overhead)
to be *measured* by this brief and EP-28.

## In scope

1. **Partitioned stage — small path** (`src/mimicwarehouse/loader/buckets.py`) —
   `stage_partitioned(con, table_spec, source, dest_dir, *, build_id, source_manifest_id, sort_by: list[str] | None = None, buckets: Iterable[int] | None = None, size_class: Literal["small","large"] | None = None, column_map=None, sweeps: int = 1) -> StageResult`.
   `sort_by` defaults to the contract's `sort_keys` minus the leading `subject_id` and
   `size_class` to the contract's `load_class` (EP-9); explicit arguments override.
   Small path (CSV ≤ ~1 GB): one statement,
   `COPY (SELECT <cols>, subject_id % 100 AS subject_bucket FROM <relation> [WHERE subject_id % 100 IN (…)] ORDER BY subject_bucket, subject_id, <sort_by>) TO '<dest>.new' (FORMAT parquet, PARTITION_BY (subject_bucket), COMPRESSION zstd, COMPRESSION_LEVEL 3, ROW_GROUP_SIZE 1000000, FILENAME_PATTERN 'part-{i}')`
   run with `preserve_insertion_order = true` so each partition file is sorted; then swap
   `.new` → `dest`. Partition columns are not written into the files (DuckDB default);
   readers use `hive_partitioning = true`.
2. **Large path (two-pass)** — pass 1: same `COPY … PARTITION_BY` into `<dest>.new` with
   `preserve_insertion_order = false`, no `ORDER BY`, `FILENAME_PATTERN 'raw_{i}'`
   (several files per partition are fine); optional `sweeps=N` runs pass 1 as N sequential
   statements each restricted to a bucket range (memory fallback for EP-26); swap `.new` →
   `dest` when pass 1 completes. Pass 2, per bucket, **buckets 0–4 first, then 5–99**:
   `COPY (SELECT * FROM read_parquet('<dest>/subject_bucket=<n>/raw_*.parquet') ORDER BY subject_id, <sort_by>) TO '<dest>/subject_bucket=<n>/_sorting.tmp' (FORMAT parquet, COMPRESSION zstd, COMPRESSION_LEVEL 3, ROW_GROUP_SIZE 1000000)`
   with `preserve_insertion_order = true`, then delete the `raw_*` files and
   `os.replace(_sorting.tmp → part-0.parquet)`; a manifest line is appended per finished
   bucket. Log one line per bucket (`bucket 07/100 sorted rows=… wall=…s`) and, every 60 s
   during pass 1, `rss`, `tmp/duckdb` size and bytes written — counts only.
3. **Progress + resume** — `<dest>/_progress.json` (`build_id, pass1_done, buckets_requested,
   sorted_buckets, dev_ready, complete, started, updated`). Resume rules: `pass1_done`
   false → redo pass 1 into `.new` (partial output discarded); a stale `_sorting.tmp` is
   deleted; already-sorted buckets are skipped; when buckets 0–4 are sorted set
   `dev_ready = true`, log `dev-ready <schema>.<table>` and `update_status(dev_ready=True)`;
   when all requested buckets are sorted set `complete = true` and
   `update_status(tier_complete="full"|"dev", …)`. A later **full** request on a table whose
   status is `tier_complete = "dev"` restages the whole table (cheap for small tables; the
   large ones are never dev-only). `DEV_BUCKETS = (0, 1, 2, 3, 4)` lives once in
   `mimicwarehouse.config` and is imported by the loader and the catalog builder (EP-21).
4. **Reader helpers** (`src/mimicwarehouse/loader/paths.py`) —
   `partition_glob(lake_root, schema, table) -> str` (absolute, forward slashes) and
   `read_parquet_sql(lake_root, schema, table, buckets=None) -> str` returning
   `read_parquet('<glob>', hive_partitioning = true, hive_types = {'subject_bucket': INTEGER})`
   plus `WHERE subject_bucket IN (…)` when `buckets` is given — the fragment the catalog
   views (EP-21) and tests use, so the layout is encoded in exactly one place.
5. **Tests** (`tests/ep/test_ep18.py`, `@pytest.mark.ep_18`) — fixture: stage fixture
   `admissions` via the small path and again via the large path (`size_class="large"`,
   plus once with `sweeps=2`) into `tmp_path`; identical row counts and identical
   per-bucket sha256 sets; exactly one `part-0.parquet` per partition and no `raw_*` /
   `_sorting.tmp` leftovers; sortedness verified by a lag query over each partition
   (`bool_and(prev <= cur)`); `buckets=DEV_BUCKETS` writes only 5 partition dirs and
   `dev_ready` before `complete`; resume: run pass 1, sort two buckets, simulate a crash
   (drop the connection, leave a `_sorting.tmp`), rerun and assert via a recording
   monkeypatch that only the unsorted buckets are processed; determinism (sha256 stable
   across two runs). If EP-11's fixture ids do not cover buckets 0–4, build a tiny inline
   CSV with ids 90 000 000–90 000 199 (never real-band ids). `tier("dev")`: stage real
   `mimiciv_hosp.admissions` (90 MB) into `<data_root>\tmp\ep18\` with
   `buckets=DEV_BUCKETS`; assert five partitions, rows equal a `count(*)` over the same
   `read_csv` relation filtered by bucket (in-process, not printed); teardown deletes it.
6. **Measurement note** — record on the fixture/dev runs: files written, wall time for the
   two paths, and append a dated note to `DESIGN.md` §21 (bucket-count question) stating
   what was observed and that the full-scale measurement is EP-28's.

## Out of scope

- YAML DAG, `mwh build`, benchmark ledger, background jobs, build lock → EP-19.
- Writing real tables into `lake/core` for keeps → EP-20 (small), EP-23…EP-27 (⏱).
- Catalog views over the partitions → EP-21; chartevents-specific memory tuning → EP-26.
- Bucket-count re-decision → EP-28 measurement, EP-33 re-plan.

## Verification / acceptance

- `uv run poe test -m ep_18` green on fixture; `uv run poe test -m ep_18 --tier dev` green; `uv run --group dev mwh verify EP-18` green.
- On-disk layout under the temp root is exactly `lake/core/<schema>/<table>/subject_bucket=<n>/part-0.parquet` (DuckDB's un-padded partition names) with sorted row groups of ≤ 1 M rows.
- The resume test proves already-sorted buckets are skipped and a stale `_sorting.tmp` is discarded; `dev-ready` is logged before `complete`.
- No row-level output in tool output; the temp lakes are removed at teardown.

## Parked → final-roadmap.md

- Alternative bucket schemes (256 hash buckets, or fewer buckets for small tables) — trigger: EP-28's file-count/Defender measurement shows NTFS overhead dominating.
- Incremental partition appends (`COPY … APPEND`) for refreshed raw releases — trigger: a MIMIC-IV point release lands.
- Parallel per-bucket sorting on several cursors — trigger: pass 2 wall time exceeds pass 1 in EP-26.
