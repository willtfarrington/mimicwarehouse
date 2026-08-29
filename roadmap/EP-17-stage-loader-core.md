# EP-17 — Loader core A: typed CSV → Parquet

**Size:** M · **Tier:** fixture+dev · **Core/Stretch:** core · **Depends on:** EP-3 (Config & data root + safety checks), EP-9 (Schema registry (YAML contract)) · **Blocks:** EP-18 (Loader core B: subject buckets, sort, resume), EP-33 (Re-plan P2), EP-137 (Importer profiler + provenance/licensing register)

> **Amended at EP-170 (2026-08-29).** Reconciled with the shipped P0/P1 code and the retro
> decisions (D-43; ledger ids in brackets). Header facts unchanged; brief shorthand reads per the
> "Notation used in briefs" table in `README.md` (this folder) § How to use.
> (1) Item 1's "if EP-3 already ships a connection factory" — it does not, but
> `inventory.open_connection()` (EP-10/EP-167) is the de-facto opener: wrap or supersede it, one
> implementation only [FC-7]. The free-space guard is tier-aware since EP-167 — use
> `settings.min_free_gb_for(tier)` (0 for `fixture`), so fixture builds under `tmp_path` pass [FC-27].
> (2) `settings.loader_reject_max` is a **new** Settings field: `extra="forbid"` and the
> `.env.example` parity test apply — add both together [FC-14, ARCH-13].
> (3) Item 4's manifest line carries **two** provenance fields, not one: the per-file
> `source_sha256` **and** the 41-file `raw_snapshot_id` (DESIGN §11 glossary; D-43 item 11) —
> the brief's single `source_manifest_id` is superseded [FC-8, ARCH-6].
> (4) Item 3's `os.replace(dest.new → dest)` fails on Windows whenever `dest` exists — ship
> `paths.swap_dir(new, dest)` implementing the rename-aside two-step of the DESIGN §5 note
> (crash-safe, **not** atomic; readers must be closed first) [FC-6, ARCH-2].
> (5) Item 3's `ORDER BY <primary key from keys.yaml>` → `ORDER BY` the contract's
> `Table.sort_keys` (`provider`/`caregiver` have no primary key to sort by; sort keys carry
> same-table tie-breaks since EP-169, so the determinism tests are well-defined) [SCH-3].
> (6) Item 2's hard-coded dialect is superseded: read options come from
> `Table.read_csv_options()` / `mimicwarehouse/schema/csv_dialect.py` (EP-169) —
> `allow_quoted_nulls=true`, **no** `timestampformat` (DuckDB's ISO cast accepts optional
> fractional seconds; the nine upstream TIMESTAMP(3) columns are recorded as `upstream_type`).
> Add a one-line dev-tier probe (`SELECT max(length(<col>)) FROM read_csv(…, all_varchar=true)`
> on pharmacy/prescriptions/outputevents timestamp columns) so the fractional-seconds question
> is answered by counts before any full-tier run under `loader_reject_max = 0` [SCH-1].
> (7) Path facts: the contract's `csv_path` is dataset-relative, the EP-19 DAG `source` is
> raw-root-relative, and the note dataset directory is PhysioNet's long name — use
> `inventory.rel_path_for(table)` / `inventory.DATASET_DIRS` and the manifest's `for_table()`
> lookup instead of re-deriving the key [FC-29].
> (8) Create the DuckDB `temp_directory` **parent** before connecting — DuckDB 1.5.5 does not
> create a missing parent and errors on first spill [CFG-3].
> (9) Column flags: **this brief owns** adding `identifier: true` / `free_text: true` flags to
> `Column` and an `identifiers:` section to `keys.yaml`; EP-23 … EP-30 only *verify* their
> per-table flags. Flag edits move `content_hash()` only — the fixture manifest pins
> `structural_hash()` (EP-169), so no fixture regeneration [FC-5].
> (10) Dev tests request EP-168's `raw_root` conftest fixture (skip-with-reason while the raw
> dataset is unreachable), so the `--tier dev` acceptance is not vacuous [FC-1].
> (11) Timing inputs, not gates: EP-10 measured 2.0–2.4 GB/s sequential raw-CSV reads on this
> machine [FC-26].
> Shipped names to code against (EP-167/168/169): `Settings.lake_root(tier)` /
> `rejects_root(tier)` / `min_free_gb_for(tier)`, the shared `mimicwarehouse/console.py` for CLI
> output, `inventory.rel_path_for` / `open_connection`, `Table.read_csv_options()`,
> `Contract.structural_hash()`, conftest `raw_root`.

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

- Alternative CSV engines (Polars/pyarrow readers) behind the same `stage_*` API — trigger: DuckDB's CSV reader misparses a MIMIC table that the contract cannot express. *(Mirrored as v2 LOAD-1, 2026-08-29.)*

> **Completion note (2026-08-29).** Shipped as amended: `mimicwarehouse/paths.py`
> (`swap_dir` rename-aside two-step, crash recovery + PermissionError retries) and the
> `loader/` package — `engine.open_build_connection` (wraps `inventory.open_connection`
> [FC-7]; DuckDB-pin refusal via the package's own `duckdb==` requirement; tier-aware
> free-space guard, so it gained a `tier` kwarg [FC-27]; per-connection `memory_limit`
> override), `csv` (one-dialect `read_csv` fragment from `Table.read_csv_options()` — no
> format strings [SCH-1]; gz-aware header validation; column-map rename/drop/NULL-fill;
> `store_rejects` into `<t>_rejects`/`<t>_scans`), `stage.stage_unpartitioned` (ORDER BY the
> contract `sort_keys` [SCH-3], zstd-3 / 1 M-row `part-0.parquet`, swap via `paths.swap_dir`
> [FC-6]; rejects copied to `<lake_root>/rejects/<schema>/<table>/<build_id>.parquet`,
> refusal above `settings.loader_reject_max` — a new Settings field with its `.env.example`
> line [FC-14]), `manifest` (`ManifestLine` with the provenance **pair** `source_sha256` +
> `raw_snapshot_id` [FC-8]; `append_manifest`; `update_status` → `{"steps": {...}}`,
> atomic). Signature deltas vs the brief text: `stage_unpartitioned` takes `lake_root=` and
> the pair instead of `source_manifest_id`, and itself appends the manifest line + status
> entry. Column flags [FC-5]: `Column.identifier`/`free_text` stamped from keys.yaml's new
> `identifiers.names` (21 names → 112 columns) and `free_text` (7 columns) sections;
> `Table.identifier_columns()`/`free_text_columns()`; typos refuse at load;
> `structural_hash()` unchanged (pinned `10b39af3…` stands — no fixture regeneration),
> `content_hash()` moved as designed; D-17 addendum records the shape. Layout facts →
> DESIGN §4/5 dated note (per-tier `manifests/`+`rejects/` under each lake root;
> `status.json` shape). **Dev probe answer (SCH-1):** all nine upstream TIMESTAMP(3)
> columns have `max(length) = 19` in the raw 3.1 CSVs — **no fractional seconds exist**
> (pharmacy 1.6 s, prescriptions 1.4 s, outputevents 0.2 s scans; the ISO-cast dialect
> stays correct either way). Acceptance: `poe test -m ep_17` 17 passed; `--tier dev`
> 19 passed (real `d_labitems`/`patients` staged into `tmp\ep17`, rows == EP-10 manifest,
> rejects 0, temp lake deleted); `mwh verify EP-17` green; full fixture suite 576 passed;
> `poe check` (lint + pyright + tests) green. Determinism held: restage and `.csv.gz`
> stagings byte-identical (sha256-pinned in tests).
