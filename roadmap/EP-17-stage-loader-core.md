# EP-17 — Loader core A: typed CSV → Parquet

**Size:** M · **Tier:** fixture+dev · **Core/Stretch:** core · **Depends on:** EP-3 (Config & data root + safety checks), EP-9 (Schema registry (YAML contract)) · **Blocks:** EP-18 (Loader core B: subject buckets, sort, resume), EP-33 (Re-plan P2), EP-137 (Importer profiler + provenance/licensing register)

## Context

This is the first code that reads raw CSVs from `source material/`. It builds the typed
CSV → Parquet primitive that every staging brief (EP-18…EP-27), the demo tier (EP-22) and
the P9 importer profiler (EP-137) reuse. It implements the lake layer of **D-17** (DuckDB +
Parquet canonical), leaves the plain CSVs untouched (**D-30**) and produces the per-file
manifest lines that become snapshot ids (**D-26**, DESIGN §5). What already exists: the
settings object from `mimicwarehouse.config` (EP-3: data-root layout, DuckDB
`memory_limit`/`threads`/`temp_directory`/`max_temp_directory_size`, the ≥ 100 GB free-space
guard, the raw-root path), the schema contract in `mimicwarehouse.schema` (EP-9: ordered
columns, DuckDB types, nullability, `keys.yaml`, demo 2.2 → 3.1 column maps), the raw
manifest of EP-10 (sha256/bytes/rows per CSV — the raw snapshot id) and the synthetic
fixtures of EP-11/12 (ids ≥ 90 000 000). Machine facts: 64 GB RAM, one NVMe, Windows
(`spawn`, MAX_PATH → short paths under `C:\mimicdata`). The loader never sniffs types
(`read_csv` gets the contract's `columns=` dict), always sets DuckDB memory/threads/temp
explicitly (an in-memory DuckDB without `temp_directory` hard-OOMs), and is the only
module allowed to open files under `source material/`. Sessions never print rows;
verification is by counts, schemas and hashes.

## In scope

1. **Build connection** (`src/mimicwarehouse/loader/engine.py`) —
   `open_build_connection(settings, *, memory_limit: str | None = None) -> duckdb.DuckDBPyConnection`:
   in-memory DuckDB with `SET memory_limit` (default from settings, 36–40 GB class),
   `SET threads` (12), `SET temp_directory` (`<data_root>\tmp\duckdb`), `SET
   max_temp_directory_size` (explicit, settings default e.g. `'150GB'`),
   `SET preserve_insertion_order = false` — all values from EP-3's
   `get_settings().duckdb_settings("build")`; asserts `duckdb.__version__` equals the
   version pinned by EP-1 (`pyproject.toml`/`uv.lock`); calls EP-3's `require_free_space()`
   first (refuse under `min_free_gb`, default 100). If EP-3 already ships a connection
   factory in `config.py`, wrap it — one implementation only.
2. **Typed CSV reader + column maps** (`src/mimicwarehouse/loader/csv.py`) —
   `csv_relation_sql(source: Path, table_spec, column_map=None) -> str` builds a
   `read_csv('<path>', header=true, columns={<name: type from contract>}, dateformat='%Y-%m-%d',
   timestampformat='%Y-%m-%d %H:%M:%S', quote='"', escape='"', nullstr='', parallel=true,
   store_rejects=true, rejects_table='<t>_rejects', rejects_scan='<t>_scans')` fragment;
   compression is inferred from the extension (`.csv` and `.csv.gz` both accepted). Before
   reading, validate the file header (read only the first line, names only) against the
   contract after applying the column map — a mismatch raises `SchemaMismatchError`
   listing missing/extra column *names*. Column maps are EP-9's
   `Contract.column_map("demo_2_2").apply(table, header) -> {csv_col: contract_col | None}`:
   a source column mapped to a contract column is renamed, one mapped to `None` is dropped,
   and a contract column absent from the source (`added_in_3_1`) becomes a typed NULL.
   Output columns are always the contract's, in order.
3. **Unpartitioned stage** (`src/mimicwarehouse/loader/stage.py`) —
   `stage_unpartitioned(con, table_spec, source, dest_dir, *, build_id, source_manifest_id, column_map=None) -> StageResult`
   writes `dest_dir.new/part-0.parquet` via
   `COPY (SELECT <cols> FROM <relation> ORDER BY <primary key from keys.yaml>) TO … (FORMAT parquet, COMPRESSION zstd, COMPRESSION_LEVEL 3, ROW_GROUP_SIZE 1000000)`
   with `preserve_insertion_order = true` for this statement (small tables only), then
   swaps `dest_dir.new` → `dest_dir` atomically (`os.replace`; a stale `.new` is deleted
   first). `StageResult` = rows, bytes, files, rejects, wall_s, manifest lines. This is the
   path for dimension tables (`d_*`, `provider`, `caregiver`); EP-18 adds the partitioned
   path and reuses everything here.
4. **Manifests** (`src/mimicwarehouse/loader/manifest.py`) — pydantic `ManifestLine`
   (`schema, table, path` (relative to the lake root, forward slashes), `sha256, bytes,
   rows, schema_hash` (sha256 of the contract's ordered `(name, type)` list),
   `writer_version` (package version + DuckDB version), `source_manifest_id` (the sha256
   EP-10 recorded for the source file in `lake/manifests/raw/<dataset>.jsonl`, or
   `"fixture"`), `build_id, ts`);
   `append_manifest(lake_root, build_id, lines)` → `lake/manifests/<build_id>.jsonl`;
   `update_status(lake_root, "<schema>.<table>", **fields)` → `lake/manifests/status.json`
   (atomic write via `.tmp` + `os.replace`; fields: `tier_complete: null|dev|full`,
   `dev_ready`, `build_id`, `rows`, `bytes`, `files`, `rejects`, `finished_at`). Rows come
   from `parquet_metadata()`, sha256 is streamed in 8 MB chunks.
5. **Rejects** — the `store_rejects` tables are copied to
   `lake/rejects/<schema>/<table>/<build_id>.parquet` (data root only; row-level; never
   printed, never committed — `.gitignore` already covers `*.parquet`); the manifest/status
   carry only the reject *count*. `stage_unpartitioned` raises `RejectThresholdError` when
   rejects exceed `settings.loader_reject_max` (default 0: any reject on the credentialed
   datasets is a contract bug to fix in EP-9's YAML with a dated note, not to tolerate).
6. **Tests** (`tests/ep/test_ep17.py`, `@pytest.mark.ep_17`) — fixture (default tier;
   data root = `tmp_path`, never the real root): stage fixture `d_labitems` and `patients`
   through the unpartitioned path; Parquet schema types equal the contract; row count
   equals the fixture CSV rows; manifest line validates; sha256 identical across two runs
   (determinism); a `.csv.gz` copy of the same fixture stages identically; column-map test
   with a renamed + missing column; header mismatch → `SchemaMismatchError`; a crafted
   bad row with `loader_reject_max=10` → `rejects == 1`, with the default → refused.
   `@pytest.mark.tier("dev")` (EP-12's `--tier dev` switch): stage real
   `mimiciv_hosp.d_labitems` and `mimiciv_hosp.patients` (64 KB, 12 MB) into a temporary
   lake root under `<data_root>\tmp\ep17\` — rows equal EP-10's raw-manifest rows for those
   files, rejects 0; the temp lake is deleted at teardown. Nothing is printed except counts.

## Out of scope

- Hive `subject_bucket` partitioning, sort, resume, two-pass large tables, dev bucket filter → EP-18 (Loader core B).
- YAML DAG, `mwh build`, benchmark ledger, background jobs → EP-19 (DAG runner).
- Staging any real table into `lake/core` for keeps → EP-20 … EP-27.
- Catalog `.duckdb` views → EP-21; demo download + 2.2 → 3.1 shim wiring → EP-22.
- Profiling of arbitrary external CSVs → EP-137 (Importer profiler).

## Verification / acceptance

- `uv run poe test -m ep_17` green on fixture; `uv run poe test -m ep_17 --tier dev` green; `uv run --group dev mwh verify EP-17` green.
- The loader **refuses** a header mismatch and a reject overflow in tests (crafted violations).
- Two stagings of the same fixture file produce byte-identical Parquet (sha256 equal); `lake/manifests/<build_id>.jsonl` and `status.json` exist under the temp root with the fields above.
- No test or command reads a raw file with anything but the loader; no row-level output appears in tool output.
- Append a dated note to `DESIGN.md` §5 if any layout fact (file naming, manifest fields) differs from what is written there.

## Parked → final-roadmap.md

- Alternative CSV engines (Polars/pyarrow readers) behind the same `stage_*` API — trigger: DuckDB's CSV reader misparses a MIMIC table that the contract cannot express.
