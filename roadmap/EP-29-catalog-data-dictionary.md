# EP-29 — Catalog & data dictionary (meta.*)

**Size:** M · **Tier:** fixture+dev+full · **Core/Stretch:** core · **Depends on:** EP-21 (Catalog builder (per-tier .duckdb)) · **Blocks:** EP-33 (Re-plan P2), EP-39 (Itemid dictionary curation + unit harmonization), EP-44 (Data-quality profiling)

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
