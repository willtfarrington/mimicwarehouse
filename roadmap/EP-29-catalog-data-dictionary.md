# EP-29 — Catalog & data dictionary (meta.*)

**Size:** M · **Tier:** fixture+dev+full · **Core/Stretch:** core · **Depends on:** EP-21 (Catalog builder (per-tier .duckdb)) · **Blocks:** EP-33 (Re-plan P2), EP-39 (Itemid dictionary curation + unit harmonization), EP-44 (Data-quality profiling)

> **Amended at EP-170 (2026-08-29).** Header facts unchanged; shorthand per the README notation
> table. (1) Item 1 is smaller than written: all 41 tables and 421 columns **already carry a
> `comment`** in the shipped contract (EP-9), and the field is `comment`, not `description` —
> item 1 becomes *expose* (into `meta.*` and `COMMENT ON`) plus improving only genuinely weak
> comments; the "count of undescribed columns" clause is moot. The `identifier`/`free_text`
> flags ship with EP-17 (EP-23 … EP-27 verified them per table); item 1 verifies the full set,
> never re-adds the machinery [FC-17, FC-5]. (2) Integers in the generated Markdown
> (`DATA-DICTIONARY.md`) are thousands-separated via `inventory.fmt_int` — guard G4 refuses bare
> 8-digit tokens starting 1–3; raw-int `--json` output is never pasted into tracked files
> [FC-16].

## Context

Capability category 1 (data inventory & quality profiling) starts here: the `meta` schema
of every tier catalog (DESIGN §5, §7) — table and column dictionaries transcribed from the
schema contract (EP-9), row counts, null fractions, an itemid dictionary base for EP-39,
and DuckDB `COMMENT`s so `DESCRIBE`/the app show descriptions — plus the generated
`mimicwarehouse/DATA-DICTIONARY.md` (DESIGN §15). Everything is aggregate metadata: row
counts, null %, distinct counts (no per-value frequency tables), identifier and free-text
flags — the flags are what `safe_query` (EP-30) and `disclose` (EP-43) key on. Descriptions
come from the contract YAML (`description` fields, transcribed from the public MIMIC-IV
documentation at mimic.mit.edu; add them there, not in code). Profiles that need a scan
(null %, distinct counts) run as a DAG `python` step into a `meta` layer of the lake
(`lake/meta/`), so the catalog build (which must finish inside `.new` before the swap) only
loads them. Full-tier profiling is a background job (chartevents/labevents scans).
Caveats worth stating in the dictionary: shifted timestamps, `anchor_year_group` as the
only temporal axis, ages ≥ 89 shown as 91, `dod` horizon ~1 year (**D-33**/GOVERNANCE §5
apply to any count that ever appears). `mwh disclose check` does not exist yet (EP-43), so
the generated file is committed with a header line saying its sidecar is pending EP-43.

## In scope

1. **Contract descriptions + flags** — extend EP-9's YAML with `description` for all 31
   tables and for the key, time, identifier and free-text columns at minimum (others may
   stay empty; report the count of undescribed columns); ensure `identifier: true` /
   `free_text: true` flags exist per column (identifier set = `keys.yaml` list: `subject_id,
   hadm_id, stay_id, transfer_id, emar_id, pharmacy_id, poe_id, orderid, linkorderid,
   caregiver_id, *_provider_id, labevent_id, specimen_id, microevent_id, micro_specimen_id,
   note_id`; free text = `labevents.comments`, `microbiologyevents.comments`, plus any
   VARCHAR the session judges note-like — record why).
2. **Profile step** (`src/mimicwarehouse/catalog/profile.py`, DAG `python` step
   `meta.profile`) — per table (from the lake, tier-aware buckets):
   `row_count`, per column `null_pct`, `approx_distinct` (`approx_count_distinct`), for
   numeric/timestamp columns `min`/`max` **only when the column is not an identifier**
   (identifiers get null), written to `lake/meta/<tier>/profile_columns.parquet` and
   `profile_tables.parquet` with `build_id`, `snapshot_id`, `profiled_at`. Small cells:
   the profile never emits value-level counts; distinct counts < 11 are reported as
   `"<11"` in Markdown output.
3. **Catalog build extension** (`catalog/build.py`) — `meta.tables (schema, table,
   description, kind, partitioned, row_count, bytes, files, build_id, snapshot_id)`,
   `meta.columns (schema, table, column, ordinal, duckdb_type, nullable, description,
   is_identifier, is_free_text, unit_hint, null_pct, approx_distinct)`,
   `meta.row_counts (schema, table, tier, rows, source: manifest|profile)`,
   `meta.itemids` view = `d_items` (`source = 'icu'`, itemid, label, abbreviation,
   linksto, category, unitname, param_type) UNION `d_labitems` (`source = 'hosp'`, itemid,
   label, fluid, category, null unit) — the base EP-39 curates; `COMMENT ON TABLE/COLUMN`
   from descriptions. Row counts for partitioned tables come from manifests (no scan);
   dev counts from bucket-filtered manifest lines.
4. **Dictionary generator** (`src/mimicwarehouse/catalog/dictionary.py`,
   `mwh catalog dictionary --tier full [--out <path>]`, default `mimicwarehouse/DATA-DICTIONARY.md`) —
   header (generated, do not edit; build id, tier, snapshot id, DuckDB version, date,
   MIMIC caveats paragraph, "disclosure sidecar pending EP-43"); per schema/table:
   description, rows (full), Parquet MB, partitioned, then a column table (name, type,
   nullable, id/free-text flags, null %, approx distinct or `<11`, description).
   Deterministic ordering so re-generation is a clean diff.
5. **Runs** — fixture in tests; real:
   `uv run --group dev mwh build --tier full --select meta.profile,catalog --background --job meta-full`
   (expect 10–30 min: chartevents/labevents null-% scans), `mwh build --tier dev --select
   meta.profile,catalog` (foreground OK if < 10 min, else `--background`), then
   `uv run --group dev mwh catalog dictionary --tier full` and commit the Markdown after
   the guard hook passes. Add dated notes to `DESIGN.md` §15 (`mwh catalog dictionary`).
6. **Tests** (`tests/ep/test_ep29.py`, `@pytest.mark.ep_29`) — fixture: `meta.tables`
   /`meta.columns` rows equal the contract; `null_pct ∈ [0, 1]`; identifiers have null
   min/max; `meta.itemids` unions both dims; `COMMENT`s visible via `duckdb_columns()`;
   the generated Markdown contains no line matching an identifier column *value* pattern
   and no 8-digit number in the real id bands (reuse EP-4's guard scanner) and no
   per-value counts; regeneration is byte-stable. `tier("dev")`/`tier("full")`-marked: `meta.row_counts` for
   `full` equal `status.json`; `DATA-DICTIONARY.md` exists and its header build id equals
   `meta.catalog_info.build_id`.

## Out of scope

- Itemid curation, unit harmonization, plausibility bounds (`meta.item_units`) → EP-39.
- Full QC profiles (duplicates, timestamp ordering, referential integrity, implausible values, suppressed QC report) → EP-44; measurement-process summaries → EP-45.
- `mwh disclose check` + `.disclosure.json` sidecar for `DATA-DICTIONARY.md` → EP-43 (list this file in that brief's acceptance at the P2 re-plan).
- Catalog & QC browser page → EP-61.

## Verification / acceptance

- `uv run poe test -m ep_29` green on fixture; `tier("dev")`/`tier("full")`-marked tests green; `uv run --group dev mwh verify EP-29` green.
- `mimicwarehouse/DATA-DICTIONARY.md` exists, is generated from the full tier, passes the guard hook, and its header names the build id and the pending-sidecar note; job `meta-full` log at `%MWH_DATA_ROOT%\runs\jobs\meta-full.log` with wall time in the completion note.
- `DESCRIBE mimiciv_hosp.admissions` via `mwh sql --describe` shows comments; `meta.itemids` row count equals `d_items` + `d_labitems`.
- The number of undescribed columns is recorded in the completion note as a follow-up for EP-33.

> **Completion note (2026-08-29).** Shipped as amended at EP-170: `catalog/profile.py`
> (`profile_lake` + `run_profile`; DAG `python` step `meta.profile` — the runner gained the
> generic `python` handler, `callable` called with `(step, ctx)`, which EP-50 reuses),
> `dag.snapshot.table_file_stats` (scan-free manifest row counts, dev bucket-filtered),
> the `meta.tables` / `meta.columns` / `meta.row_counts` / `meta.itemids` + `COMMENT ON`
> extension of `catalog/build.py`, `catalog/dictionary.py` + `mwh catalog dictionary`
> (`mwh sql --describe` now also prints the comment column), and `tests/ep/test_ep29.py`
> (13 fixture + 3 dev/full tests).
>
> - **Undescribed columns: 0** (all 31 staged tables / 342 columns — indeed all 41 / 421 —
>   already carried contract comments from EP-9; the EP-33 follow-up is moot). Judgment
>   calls: two genuinely weak comments rewritten (`microbiologyevents.test_itemid` "Test
>   item.", `d_items.label` "Item label."); the remaining terse ones ("9 or 10.",
>   "Y/N flag.") are adequate in context and kept. No new free-text flags: the EP-17
>   `keys.yaml` set (labevents/microbiologyevents `comments` + the ED/Note columns) was
>   re-reviewed and stands — the EP-23..27 per-table verifications already examined the
>   candidate VARCHARs (dose/`field_value` columns are structured/categorical).
> - **Runs.** dev (foreground): build `20260829T215120-dev-691c974` — `meta.profile`
>   43,860,757 rows wall 2.0 s, `catalog` 1.7 s, build 3.7 s. full (background job
>   `meta-full`, log `runs\jobs\meta-full.log` under the data root): build
>   `20260829T215141-full-691c974` — `meta.profile` **886,043,036 rows, wall 14.1 s**
>   (peak RSS ~0.2 GB in-process; the scan runs inside DuckDB), `catalog` 1.7 s, job wall
>   15.9 s. The brief's 10-30 min estimate assumed sort-shaped cost; a pure aggregate scan
>   over ZSTD Parquet on NVMe with 12 threads sustains ~60 M rows/s, so full-tier
>   re-profiling is cheap enough to fold into every full build (it is part of the standard
>   DAG: `catalog` now depends on `meta.profile`).
> - **Dictionary.** `mwh catalog dictionary --tier full` wrote
>   `mimicwarehouse/DATA-DICTIONARY.md` (31 tables / 342 columns, header build id
>   `20260829T215141-full-691c974`, pending-EP-43 sidecar line); `mwh guard` clean;
>   regeneration byte-stable (timestamps come from `meta.catalog_info`).
> - **CMP-6 (earlier tests touched).** `test_ep20.py` (the catalog step's `depends_on`
>   pin now includes `meta.profile`) and `test_ep22.py` (the demo-tier DAG kind set gained
>   `python`) — both are spec pins over the DAG that the new step legitimately extends.
>   Full ladder green after the change: `poe test --tier full` 691 passed; `poe check`
>   (ruff + pyright + pytest) green; `mwh verify EP-29` green.
