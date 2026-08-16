# EP-10 — Raw inventory manifest ⏱

**Size:** M · **Tier:** fixture+dev (full ⏱ → verified by EP-16) · **Core/Stretch:** core · **Depends on:** EP-3 (Config & data root + safety checks), EP-9 (Schema registry (YAML contract)) · **Blocks:** EP-16 (Re-plan P1)

## Context

The 41 raw CSVs under `source material/` (~98 GB; `chartevents.csv` alone 40 GB) were decompressed
in place and the `.csv.gz` archives deleted, so PhysioNet's `SHA256SUMS.txt` (which covers only the
archives) cannot verify them (README Risk 1). **D-26** therefore makes a locally computed manifest —
SHA-256, byte size, header, row count per file, reconciled against the row counts in mimic-code's
vendored `validate.sql` (EP-8) — the **raw snapshot id** that every lake manifest (EP-17+, DESIGN §5)
cites as its `source manifest id`. EP-3 gives us `mimicwarehouse.config` (`get_settings()`: `source_root`, `layout["lake_manifests"]`,
`layout["runs_jobs"]`, `duckdb_settings("build")`, `require_free_space()`); EP-9 gives the contract
(`load_contract()`, `Table.csv_path`, `read_csv_columns()`). Machine facts: single NVMe; Defender's
real-time exclusion covers the data root, **not** `source material/`, so hashing runs at Defender speed;
the `source material` path contains a space (always `pathlib`, never string-built shell commands);
foreground shell commands are capped at ~10 min, so the full pass is a logged **background job**
(**D-18**). Governance: the module reads the files (that is its job) but its outputs are hashes, byte
counts, column names and row counts only — never a row, never a value (GOVERNANCE §4; the header line
is schema, not data). `mwh sql`/`safe_query` do not exist yet (EP-30); nothing here needs them.

## In scope

1. **Inventory module** (`src/mimicwarehouse/inventory.py`): `inventory_file(path, table) -> FileRecord` with
   `dataset, module, table, rel_path, bytes, mtime, sha256, header: list[str], header_matches_contract: bool,
   missing_columns, extra_columns, rows: int | None, rowcount_method, csv_parallel_fallback: bool, seconds_hash,
   seconds_rows`. SHA-256 via `hashlib.file_digest(f, "sha256")` (streaming); header via reading the first line
   only; rows via DuckDB `SELECT count(*) FROM read_csv(?, header=true, all_varchar=true, …)` on a connection
   configured with `get_settings().duckdb_settings("build")` (explicit `memory_limit`, `threads`, `temp_directory`,
   `max_temp_directory_size`, `preserve_insertion_order=false`) — quoted embedded newlines (`labevents.comments`, note text) make newline
   counting wrong, so DuckDB is the counter; if the parallel reader fails on a file, retry with `parallel=false`
   and record `csv_parallel_fallback=true`. Also parse each dataset's `SHA256SUMS.txt` (hashes + `.csv.gz` names)
   into `physionet_gz_sha256` per file for the parked `.csv.gz` re-verification (v2 RAW-1).
2. **Manifest store + snapshot id**: one JSONL line per file at
   `%MWH_DATA_ROOT%\lake\manifests\raw\<dataset-dir>.jsonl` (`mimic-iv-3.1`, `mimic-iv-ed-2.2`, `mimic-iv-note-…`)
   and `%MWH_DATA_ROOT%\lake\manifests\raw\raw_snapshot.json` with `raw_snapshot_id = sha256` over the sorted
   canonical `(rel_path, bytes, sha256, rows)` tuples, `files_expected=41`, `files_done`, `started/finished`,
   `duckdb_version`, `git_sha`, `mimic_code_sha` (EP-8 `vendor_info()`), per-dataset totals. `raw_snapshot_id` is
   `None` until all 41 files are present. Expose `load_raw_manifest()` and `raw_snapshot_id()` for the loader.
3. **CLI** (`mwh inventory build [--dataset <dir>] [--max-bytes N] [--resume] [--force] [--no-rowcount] [--log <path>]`,
   `mwh inventory show`, `mwh inventory reconcile`): `build` walks the contract's `csv_path`s under the source root
   from config, skips files whose `(bytes, mtime)` already match a manifest line unless `--force` (`--resume` is
   the default), processes files sequentially (one NVMe; DuckDB threads do the parallelism), logs one line per
   file with wall time and MB/s, refuses to start when free space is < 100 GB (config guard) or the source root
   is missing. `show [--timing]` prints a table of `table, bytes, rows, header ok, sha256[:12]` (with `--timing`:
   seconds and MB/s per file, per-dataset totals) plus the job status kept in `raw_snapshot.json`
   (`started, finished, last_file, files_done, errors`) — no data. This matters because sessions cannot read
   anything under the data root (deny rules), including the job log: `show` is how EP-16 verifies the job.
   Record the `mwh inventory` addition as a dated note in `DESIGN.md` §15.
4. **Reconciliation** (`mimicwarehouse.inventory.parse_validate_sql(path) -> dict[table, int]`, tolerant regex over
   the upstream `'table' … <int>` pairs, ignoring the EP-8 `-- mwh-guard: allow` suffixes; unit-tested on a synthetic
   snippet; plus `expected_counts(dataset="mimic-iv-3.1") -> dict[table, int]` reading the vendored file — the
   name EP-20/EP-28 call) and `mwh inventory reconcile`:
   `table, expected (validate.sql), observed, delta, status ∈ {match, mismatch, no-expectation}` for hosp/icu (and
   ED if the vendored ED `validate.sql` exists). MIMIC-IV 3.1 removed two orphan `subject_id`s vs 3.0 — if the pinned
   `validate.sql` targets 3.0, small deltas in `patients`/`admissions`-linked tables are expected; record them.
   Write the reconciliation table + per-file hashes/bytes/rows/header status into
   `mimicwarehouse/docs/resources/raw-inventory.md` (a manifest of hashes/counts/schema — allowed in git by
   GOVERNANCE §3 without a disclosure sidecar; every count here is in the thousands or more). Format every integer
   in that file and in completion notes with thousands separators (`123,456,789`): the guard's G4 rule (EP-4)
   refuses any bare 8-digit token starting with 1–3, and byte sizes / row counts routinely are.
5. **Runs**: (a) **dev-like foreground pass** — `uv run --group dev mwh inventory build --max-bytes 1000000000`
   (every file < 1 GB, ~28 files, a few minutes) in the session — this foreground pass is deliberate: it is
   bounded by `--max-bytes`, finishes well inside the ~10 min foreground cap, and emits hashes, counts and column
   names only (never rows), so D-18's background rule for long full jobs does not bite; (b) **full ⏱
   background job** for the rest:
   ```powershell
   # from the workspace dir (cd mimicwarehouse); MWH_DATA_ROOT set in .env / env
   $jobs = "$env:MWH_DATA_ROOT\runs\jobs"; New-Item -ItemType Directory -Force $jobs | Out-Null
   $mwhArgs = @('run','--group','dev','mwh','inventory','build','--resume','--log',"$jobs\ep10-raw-inventory.log")
   (Start-Process -FilePath uv -ArgumentList $mwhArgs -WorkingDirectory (Get-Location).Path -NoNewWindow -PassThru `
      -RedirectStandardOutput "$jobs\ep10-raw-inventory.out" -RedirectStandardError "$jobs\ep10-raw-inventory.err").Id
   ```
   Expect 10–30 min (hash + parse of 98 GB, Defender-limited). Record the PID, start time and log path in the
   completion note; **do not wait for it** — EP-16 verifies.
6. **Tests** (`tests/ep/test_ep10.py`, `@pytest.mark.ep_10`, fixture tier = synthetic CSVs written to `tmp_path`
   with contract headers and ids ≥ 90 000 000): sha256 equals `hashlib` over the bytes; row count correct with quoted
   embedded newlines and CRLF; header mismatch detected (extra/missing column names); `parse_validate_sql` on a
   snippet; `raw_snapshot_id` deterministic and independent of processing order; `--resume` skips unchanged files
   and `--force` recomputes; the CLI output of `show`/`reconcile` contains no fixture id and no cell value (assert
   on captured stdout).

## Out of scope

- Loading anything into Parquet/DuckDB → EP-17/18; `lake/manifests/<build_id>.jsonl` → EP-19.
- Re-downloading `.csv.gz` to restore checksum-verifiable raw → parked (v2 RAW-1).
- Timing/RSS/disk to the benchmark ledger (`runs/benchmarks.jsonl` is EP-19's) → EP-16 records timing in this
  brief's completion note instead.
- Data-quality profiling (null %, ranges) → EP-44.

## Verification / acceptance

- `uv run poe test -m ep_10` and `uv run --group dev mwh verify EP-10` green on fixture.
- Foreground pass done: `uv run --group dev mwh inventory show` lists every file < 1 GB with `header ok = True`
  (any `False` is a finding: record the column-name diff in the completion note and in README Risks — it means the
  contract or the local files differ from the pinned DDL).
- Full pass launched in the background; log at `%MWH_DATA_ROOT%\runs\jobs\ep10-raw-inventory.log`; PID and start
  time recorded in the `> **Completion note (date).**` block of this brief; timing, `raw_snapshot_id`,
  reconciliation status and `docs/resources/raw-inventory.md` are completed by **EP-16**.
- Commit `feat(mimicwarehouse): raw inventory manifest + reconcile (EP-10)` (code, tests, DESIGN note; the docs
  table lands with EP-16), then `docs(roadmap): record EP-10 commit hash`.
