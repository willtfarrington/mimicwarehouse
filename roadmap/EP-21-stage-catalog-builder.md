# EP-21 — Catalog builder (per-tier .duckdb)

**Size:** M · **Tier:** fixture+dev+full · **Core/Stretch:** core · **Depends on:** EP-20 (Stage dimensions + small hosp/icu tables) · **Blocks:** EP-22 (Demo tier (MIMIC-IV Demo 2.2 + ED Demo)), EP-28 (Verify full staging), EP-29 (Catalog & data dictionary (meta.*)), EP-30 (Safe-query wrapper + audit log), EP-31 (Tracer bullet: first-ICU-stay adults → in-hospital mortality), EP-33 (Re-plan P2), EP-34 (Time semantics + unit-of-analysis registry), EP-40 (Code-set registry + ICD-9→10 GEM utility)

> **Amended at EP-170 (2026-08-29).** Header facts unchanged; shorthand per the README notation
> table. (1) Item 1's swap is the **rename-aside two-step** of the DESIGN §6 note (D-43 item 6):
> `os.rename(<tier>.duckdb → .old)` *succeeds* with READ_ONLY readers open, then
> `os.replace(.new → <tier>.duckdb)`, then remove `.old`; `open_catalog` retries the
> sub-millisecond `FileNotFoundError` window; the "close the app and rerun" message applies only
> when even the rename fails [ARCH-1]. (2) `catalog_path("fixture")` is a real per-tier path
> under the data root (`warehouse/fixture.duckdb`) and the fixture lake root is `lake/fixture`
> (EP-167, D-43 item 7) — not a "temp root"; `Settings.catalog_path` already exists, do not
> re-specify it; tests wanting isolation pass a `tmp_path`-rooted Settings [ARCH-3, FC-7].
> (3) Record `dev_buckets` in `meta.catalog_info` and warn on open/build when the current
> `settings.dev_buckets` drifts from the recorded ones [ARCH-8]. (4) Item 4 does **not** re-point
> EP-12's in-memory `fixture_catalog` (31 tables; `test_ep12` and later suites depend on it): add
> a **separate** session fixture `fixture_lake_catalog` over the runner-built fixture-tier
> catalog, and build it without `--tag small` so it grows as EP-23 … EP-27 add fixture steps
> (see the EP-20 amendment, option B) [FC-11, FXT-11]. (5) Catalogs embed absolute lake paths and
> assert the DuckDB version on open — they are derived and disposable: after a data-root move or
> a pin bump the fix is `mwh build --tier <t> --select catalog`, never surgery (DESIGN §6 note)
> [ARCH-16]. (6) Item 2's `MWH_ROLE` is a **new** Settings field — `extra="forbid"` and the
> `.env.example` parity test apply [ARCH-13].

## Context

The catalog layer (DESIGN §3, §6): one `.duckdb` file per tier under
`C:\mimicdata\warehouse\` (`fixture` under a temp root, `demo`, `dev`, `full`) holding
`mimiciv_hosp` / `mimiciv_icu` views over the Parquet lake plus small materialized tables,
so that concept SQL, cohorts, the app and `safe_query` all address the same names in every
tier (**D-17**, **D-18**). Rules from DESIGN §6: `mwh build` is the only writer; catalogs
are built to `<tier>.duckdb.new` and atomically swapped; every reader opens
`access_mode = 'READ_ONLY'`; **one pinned DuckDB version** (storage format changes between
1.4 and 1.5) recorded in `meta.catalog_info` and asserted on open. What exists: the lake
with 20 staged tables and `status.json` (EP-20), `read_parquet_sql`/`DEV_BUCKETS`
(EP-18), the DAG runner with a registered-but-empty `catalog` step (EP-19), the schema
contract (EP-9), the fixture lake path (EP-11/12). DESIGN §21 asks whether `dev.duckdb`
should materialise small tables — decide here: **dims are materialized as tables in every
tier, subject-keyed tables are views** (Hive pruning already makes dev fast); EP-55 revisits
for marts. Governance: sessions never open catalogs directly except through package code
in tests; free-form SQL for sessions arrives only with `safe_query` (EP-30, **D-31**), so
this brief's `mwh sql` is a metadata/count-only interim.

## In scope

1. **Catalog build step** (`src/mimicwarehouse/catalog/build.py`) —
   `build_catalog(tier, settings, *, lake_root=None) -> CatalogBuildResult`, registered as
   the `catalog` step handler in `STEP_HANDLERS` (EP-19): delete a stale
   `warehouse/<tier>.duckdb.new`; connect to it with the build settings; assert
   `duckdb.__version__` equals the EP-1 pin; `CREATE SCHEMA` `mimiciv_hosp, mimiciv_icu,
   mimiciv_derived, meta, marts`; for every contract table whose `status.json` entry
   qualifies (`full` requires `tier_complete = "full"`; `dev` accepts `dev_ready`; `fixture`
   and `demo` require complete on their own lake roots): unpartitioned →
   `CREATE TABLE <schema>.<table> AS SELECT * FROM read_parquet('<abs>/part-0.parquet')`;
   partitioned → `CREATE VIEW <schema>.<table> AS SELECT <contract columns in order> FROM
   read_parquet('<abs glob>', hive_partitioning = true, hive_types = {'subject_bucket': INTEGER})`
   with `WHERE subject_bucket IN (0,1,2,3,4)` for `dev` (fragment from `read_parquet_sql`);
   tables not yet staged are **omitted** (never empty views) and listed in
   `meta.catalog_tables (schema, table, kind: table|view|missing, status, rows_hint)`;
   `meta.catalog_info` single row (`build_id, tier, duckdb_version, package_version, git_sha,
   core_snapshot_id, lake_root, built_at, k_default = 11`); `CHECKPOINT`; close; swap
   `os.replace(.new → <tier>.duckdb)`. On Windows the swap fails while a reader holds the
   file — raise a clear message ("close the app/notebooks and rerun `mwh build --tier <t>
   --select catalog`") and leave the old catalog intact.
2. **Read-only opener** (`src/mimicwarehouse/catalog/connect.py`) —
   `open_catalog(tier, *, settings=None, role=None) -> duckdb.DuckDBPyConnection` with
   `read_only=True` and config `memory_limit` (settings.duckdb_app_memory_limit, default
   `'12GB'`), `threads`, `temp_directory`, `max_temp_directory_size`; then
   `SET autoinstall_known_extensions = false; SET autoload_known_extensions = false;
   SET disabled_filesystems = 'HTTPFileSystem'`; asserts the catalog's recorded DuckDB
   version equals the running one; refuses to open a `.new` file; `role` defaults to
   `MWH_ROLE` (settings; `agent` unless the owner sets `owner` in their own shell — Claude
   sessions never set it; add that one line to `CLAUDE.md` §2). Also `catalog_path(tier)`.
3. **CLI** — `mwh catalog info --tier <t>` prints `meta.catalog_info` and
   `meta.catalog_tables` (metadata only). `mwh sql` is registered with its final option
   surface (`--tier`, `--k`, `--format`) but, until EP-30 replaces its body, supports only
   `--tables`, `--describe <schema.table>` and `--count <schema.table>` (prints `count(*)`);
   any free-form statement exits 2 with "free-form SQL arrives with safe_query (EP-30)".
   Add dated notes to `DESIGN.md` §15 (`mwh catalog`) and §21 (materialization decision).
4. **Fixture catalog for the test suite** — re-point EP-12's session-scoped
   `fixture_catalog` conftest fixture at a real fixture-tier catalog: build the fixture
   lake into a session temp root (`mwh build --tier fixture --tag small --data-root <tmp>`
   via the runner API), then `build_catalog("fixture")`, and yield
   `open_catalog("fixture", settings=<tmp settings>)`; keep EP-12's in-memory
   `build_fixture_catalog()` only as the fallback when the loader is unavailable, and keep
   EP-8…EP-12's tests green (same 31 table names).
5. **Runs** — `uv run --group dev mwh build --tier dev --select catalog` (seconds) and
   `uv run --group dev mwh build --tier full --select catalog --background --job catalog-full`;
   wait via `mwh jobs --job catalog-full`; then `mwh catalog info --tier full` lists the
   20 EP-20 tables as `table`/`view` and the rest as `missing`;
   `uv run --group dev mwh sql --tier full --count mimiciv_hosp.admissions` equals the
   `validate.sql` count.
6. **Tests** (`tests/ep/test_ep21.py`, `@pytest.mark.ep_21`) — fixture: catalog file
   exists and `.new` is gone; `meta.catalog_info` row and version match; views/tables list
   equals the staged set and dims are tables; an `INSERT` through `open_catalog` raises
   (read-only); opening a `.new` path is refused; the swap with an in-process reader open
   raises the documented message and leaves the old file valid; a table with
   `tier_complete = "dev"` only appears in `dev`, not `full`; the interim `mwh sql` refuses
   a crafted free-form statement (`SELECT * FROM mimiciv_hosp.patients`) with exit 2.
   `tier("dev")`-marked: `open_catalog("dev")` sees exactly the tables `mwh catalog info` reports.

## Out of scope

- `meta.tables/columns/row_counts/null_pct`, `COMMENT`s, `DATA-DICTIONARY.md` → EP-29.
- `safe_query`, audit log, `runs.duckdb`, free-form `mwh sql` → EP-30.
- Demo raw root, `.csv.gz` + column-map staging into `lake/demo`, `demo.duckdb` → EP-22.
- App-side cached READ_ONLY connections and tier switcher → EP-57; owner row view → EP-58.

## Verification / acceptance

- `uv run poe test -m ep_21` green on fixture; `tier("dev")`-marked tests green; `uv run --group dev mwh verify EP-21` green.
- `%MWH_DATA_ROOT%\warehouse\dev.duckdb` and `full.duckdb` exist; `meta.catalog_info.duckdb_version` equals the pinned version; no `.new` left behind; job `catalog-full` log at `runs\jobs\catalog-full.log`.
- The READ_ONLY opener **refuses** a write and the interim `mwh sql` **refuses** free-form SQL in tests (crafted violations).
- `mwh sql --tier full --count mimiciv_hosp.admissions` matches `validate.sql`; the number is recorded in the completion note.

> **Completion note (2026-08-29).** Executed in one session (≈ 55 min against M ≈ 1 h), all
> amendments applied.
>
> **Items 1/2 (package).** `src/mimicwarehouse/catalog/` landed — `build.py`
> (`build_catalog`, the real `catalog` handler in `STEP_HANDLERS`; single-file rename-aside
> swap per the amendment, with the "close the app/notebooks and rerun" message only when even
> the rename fails), `connect.py` (`open_catalog` READ_ONLY + hardening SETs + version
> assert + `.new` refusal + the sub-millisecond `FileNotFoundError` retry; `catalog_path`)
> and `cli.py`. `meta.catalog_info` records `dev_buckets` and both build and open warn on
> drift (amendment 3). `MWH_ROLE` shipped as the new `Settings.role` field (`agent` default,
> `.env.example` parity kept) and CLAUDE.md §2 gained the one line. Materialization decided
> per the Context: dims = tables, subject-keyed = views (DESIGN §21 note; §15 module note
> added).
>
> **Item 3 (CLI).** `mwh catalog info --tier <t> [--json]` prints both meta tables.
> `mwh sql` is registered with its final surface (`--tier`, `--k`, `--format`) and the
> interim body: `--tables` / `--describe` / `--count` only (identifier-validated against
> information_schema; counts `0 < n < k` print suppressed per GOVERNANCE §5); any free-form
> statement exits 2 with "free-form SQL arrives with safe_query (EP-30)" — verified against
> the real full catalog and in a test.
>
> **Item 4 (test fixture).** Per amendment 4 the in-memory `fixture_catalog` is untouched;
> the new session fixtures `fixture_lake_settings` / `fixture_lake_catalog` build the
> fixture tier through the runner **without** a tag filter (20 stages + catalog, ≈ 10 s once
> per session) into a temp root and open `fixture.duckdb` read-only.
>
> **Item 5 (runs).** `mwh build --tier dev --select catalog`: build
> `20260829T183223-dev-3e01f24`, **1.0 s**, `dev.duckdb` 10,235,904 bytes. Full: job
> **`catalog-full`** (build `20260829T183231-full-3e01f24`, log
> `C:\mimicdata\runs\jobs\catalog-full.log`), started 18:32:30Z, finished 18:32:32Z —
> catalog step **0.9 s**, `full.duckdb` 10,498,048 bytes; its `core_snapshot_id` equals
> EP-20's recorded `core/full` snapshot (`cb54d4ab…`). `mwh catalog info --tier full` lists
> the 20 EP-20 tables as 7 `table` + 13 `view` and the 11 later-brief tables as `missing`.
> **`mwh sql --tier full --count mimiciv_hosp.admissions` = 546,028** — equal to the
> `validate.sql` expectation reconciled at EP-20.
>
> **Item 6 (tests).** `tests/ep/test_ep21.py` (12 fixture + 1 dev-marked): published file
> with no `.new`; `meta.catalog_info` row + pinned-version match; tables/views/missing equal
> the staged set with dims as tables; INSERT through `open_catalog` raises; a `.new` path
> and a crafted version-mismatch catalog are refused; the swap under a plain (non-sharing)
> file handle raises the documented message and leaves the old catalog valid; a crafted
> `tier_complete = "dev"` table appears in the dev catalog (with the `subject_bucket IN
> (0, 1, 2, 3, 4)` view filter) but not full; `mwh sql` refuses the crafted free-form
> statement with exit 2; `open_catalog("dev")` sees exactly what `mwh catalog info` reports.
> `poe test -m ep_21` 12 passed · `--tier dev` 13 passed · full suite **609 passed** (`poe
> check` green) · `--tier full -m "ep_12 or ep_20 or ep_21"` 53 passed (EP-12's dev/full
> catalog probes now run against the real catalogs) · `mwh verify EP-21` green.
>
> **Earlier tests touched** (README § acceptance phrasing rule):
> `tests/ep/test_ep19.py::test_failure_stops_run_and_rerun_resumes` pinned the
> `NotImplementedError("EP-21")` stub as its failing step — a shipped fact this brief
> changes; it now crafts its own failing handler and its rerun leg exercises the real
> catalog build. `mwh verify EP-19` still exits 0 (8 passed).
