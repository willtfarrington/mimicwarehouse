# EP-9 — Schema registry (YAML contract)

**Size:** M · **Tier:** fixture · **Core/Stretch:** core · **Depends on:** EP-8 (mimic-code vendoring) · **Blocks:** EP-10 (Raw inventory manifest ⏱), EP-11 (Synthetic fixture generator A (hosp)), EP-16 (Re-plan P1), EP-17 (Loader core A: typed CSV → Parquet)

> **Amended at EP-7 re-plan (2026-08-17).** Checked against the P0 code; header facts unchanged.
> (1) **CLI contract (EP-3):** every `mwh` command except `cli.DIAGNOSTIC_COMMANDS` (`doctor`, `paths`,
> `guard`, `verify`) receives *validated* settings and exits 2 on an unsafe data root before the command
> runs. `mwh schema …` never touches the data root, so item 6 now adds `"schema"` to `DIAGNOSTIC_COMMANDS`
> (and to the planned-command roster in `cli.py`'s docstring) — a mis-set `MWH_DATA_ROOT` must not hide a
> schema-drift check. (2) **Pre-commit (EP-4):** `check-yaml` and `check-json` now parse every staged
> `.yaml`/`.json` — the contract files must be single-document, tag-free YAML; `end-of-file-fixer` /
> `trailing-whitespace` will normalise them (fine — they are ours, not upstream). (3) The in-memory
> DuckDB in test 6 is opened with `get_settings().duckdb_settings("app")` like EP-12 does (house rule,
> DESIGN §6) — the DDL test needs no data root beyond the settings default. (4) The EP-8 clone under
> `%TEMP%` that item 4 diffs is not on the Malwarebytes allow list (Risk 12) and `%TEMP%` may be cleaned
> between sessions — re-clone at the pinned sha if it is gone. (5) EP-164 runs before EP-8; nothing here
> depends on it. Command forms: `uv run mwh …` ≡ `uv run --group dev mwh …`.

## Context

The loader (EP-17/18) must read 98 GB of CSV with **declared** types — sniffing 40 GB of `chartevents`
is not an option — and the catalog (EP-21/29), fixtures (EP-11/12), inventory (EP-10) and demo tier
(EP-22) all need one authoritative description of the 41 tables (22 `mimiciv_hosp`, 9 `mimiciv_icu`,
6 `mimiciv_ed`, 4 `mimiciv_note`). DESIGN §7 fixes the shape: a repo YAML **schema contract** transcribed
from the vendored mimic-code `create.sql` (EP-8, pinned sha in `VENDOR.json`) — column names, DuckDB types,
nullability — plus `keys.yaml` (PK/FK from `constraint.sql`/`index.sql`), unit expectations, and the
demo-2.2 → 3.1 column map (**D-17**, **D-19**, **D-27**). MIMIC facts to encode as comments so the
data dictionary (EP-29) inherits them: `patients.anchor_age` shows ages ≥ 89 as 91; `anchor_year_group`
(2008–2010 … 2020–2022) is the only cross-patient temporal axis; timestamps are per-patient shifted and
stored naive; `dod` is available ~1 year past the last discharge; ICD-9 → ICD-10 switch (~2015) makes
`icd_version` mandatory in every diagnosis/procedure key; the MIMIC-IV Demo is **v2.2** schema (no
`provider`/`caregiver` tables, no provider/caregiver id columns). Nothing here reads data: the session
works from the vendored SQL and public documentation only (`source material/**` is denied to sessions).

## In scope

1. **Contract models + loader** (`src/mimicwarehouse/schema/__init__.py`, `contract.py`): pydantic
   `Column(name, duckdb_type, nullable=True, comment=None, unit_of=None)`, `Table(schema, name, dataset,
   csv_path, columns, primary_key: list[str] | None, subject_keyed: bool, time_column: str | None,
   sort_keys: list[str], partitioned: bool, load_class: "small" | "large", expected_rows_source)`,
   `ForeignKey(table, columns, ref_table, ref_columns)`, `Contract(tables, foreign_keys, version_note)`.
   API used by every later EP: `load_contract() -> Contract` (cached), `contract.table("mimiciv_hosp",
   "labevents")`, `Table.duckdb_ddl()` → `CREATE TABLE schema.table (...)`, `Table.read_csv_columns()` →
   `{name: duckdb_type}` for `read_csv(columns=…)`, `Contract.subject_keyed()`, `Contract.dims()`,
   `Contract.by_dataset("mimic-iv-3.1")`. Type map Postgres → DuckDB: `INTEGER/SMALLINT/BIGINT` unchanged,
   `VARCHAR(n)/TEXT` → `VARCHAR`, `TIMESTAMP(0)`/`TIMESTAMP` → `TIMESTAMP` (naive), `DATE`,
   `DOUBLE PRECISION` → `DOUBLE`, `NUMERIC(p,s)` → `DECIMAL(p,s)`, `CHAR(n)` → `VARCHAR`. Ids: `subject_id`
   `INTEGER`, `hadm_id` `INTEGER`, `stay_id` `INTEGER`, `labevent_id`/`microevent_id`/`transfer_id` `INTEGER`,
   `emar_id`/`poe_id`/`provider_id`/`note_id` `VARCHAR`.
2. **YAML data files** (`src/mimicwarehouse/schema/tables/mimiciv_hosp.yaml`, `mimiciv_icu.yaml`,
   `mimiciv_ed.yaml`, `mimiciv_note.yaml`, `keys.yaml`, `units.yaml`, `column_maps/demo_2_2_to_3_1.yaml`), shipped
   as package data. Fill `keys.yaml` from the vendored `constraint.sql`/`index.sql`: where upstream declares no
   PK (likely `chartevents`, `emar_detail`, `prescriptions`, `poe_detail` — take the truth from `constraint.sql`),
   set `primary_key: null` and add a `uniqueness_hint` (candidate columns) for EP-28/EP-44 to test rather than
   assert. Per-table metadata rules:
   `subject_keyed = "subject_id" in columns`; `partitioned = subject_keyed` (dims and `provider`/`caregiver`
   unpartitioned, DESIGN §4/§5); `time_column` = the primary event timestamp (`admittime`, `intime`, `charttime`,
   `starttime`, `ordertime`, `chartdate`, `transfertime`, …) or `null` for dims/detail tables;
   `sort_keys = [subject_id, time_column]` or `[subject_id, <parent key>, <seq>]` for detail tables;
   `load_class = "large"` for `chartevents`, `labevents`, `emar`, `emar_detail`, `pharmacy`, `poe`, `prescriptions`,
   `inputevents`, `ingredientevents`, `datetimeevents`, `microbiologyevents`, `discharge`, `radiology`;
   `csv_path` mirrors the raw layout (`hosp/labevents.csv`, `icu/chartevents.csv`, `ed/triage.csv`, `note/discharge.csv`).
3. **Transcriber** (`src/mimicwarehouse/schema/transcribe.py`, `mwh schema transcribe --create-sql <vendored path>
   --schema mimiciv_hosp --out <yaml>`): regex/sqlglot parse of `CREATE TABLE schema.table (col TYPE [NOT NULL], …)`
   from the vendored DDL into a YAML draft the session then curates (keys, metadata, comments). Keep it as the
   **drift oracle**: `mwh schema check` re-parses the vendored `create.sql` files and fails if any (table, column,
   type-class, nullability) differs from the YAML — the contract can never silently diverge from the pinned DDL.
4. **Column map demo 2.2 → 3.1** (`column_maps/demo_2_2_to_3_1.yaml`): per table `added_in_3_1: [cols]` (loader
   fills NULL when reading 2.2 data), `renamed: {old: new}`, `dropped_in_3_1: [cols]` (ignored on load),
   `tables_absent_in_2_2: [...]`. Derive it by diffing DDLs in the EP-8 clone (`git -C "$env:TEMP\mimic-code" log
   -- mimic-iv/buildmimic/postgres/create.sql`; take the last commit before the v3.0 additions of `provider`,
   `caregiver`, `*_provider_id`, `caregiver_id`) against the pinned 3.1 DDL. Expected differences to verify, not
   trust: hosp `provider` and icu `caregiver` tables absent in 2.2; `admissions.admit_provider_id`,
   `labevents.order_provider_id`, `microbiologyevents.order_provider_id`, `poe.order_provider_id`,
   `prescriptions.order_provider_id`, `emar.enter_provider_id`, and `caregiver_id` on the icu event tables added
   after 2.2. Expose `Contract.column_map("demo_2_2") -> ColumnMap` with `apply(table, header: list[str]) ->
   {csv_col: contract_col | None}`; ED demo 2.2 = our ED 2.2 source (identity map). EP-22 validates the map against
   the real demo headers (in code, never by printing them).
5. **Units seed** (`units.yaml`): value ↔ unit column pairs (`labevents.valuenum`↔`valueuom`,
   `chartevents.valuenum`↔`valueuom`, `inputevents.amount`↔`amountuom`, `inputevents.rate`↔`rateuom`,
   `ingredientevents.amount`↔`amountuom`, `outputevents.value`↔`valueuom`, `procedureevents.value`↔`valueuom`,
   `ed.vitalsign` fixed units: `temperature` °F, `heartrate` /min, `resprate` /min, `o2sat` %, `sbp`/`dbp` mmHg;
   `omr.result_value` text with `result_name`-implied units) and coarse plausibility bounds for the fixed-unit ED
   columns only. Itemid-level unit expectations and harmonisation → EP-39.
6. **Tests + CLI + docs** (`tests/ep/test_ep09.py`, `@pytest.mark.ep_9`): contract loads; exactly 41 tables (22/9/6/4);
   spot checks (`patients` has exactly `subject_id, gender, anchor_age, anchor_year, anchor_year_group, dod`;
   `labevents.valuenum` is `DOUBLE`; `d_icd_diagnoses` PK = `(icd_code, icd_version)`); every subject-keyed table
   has `sort_keys[0] == "subject_id"`; every FK references an existing table/column; every column named in the
   column map or `units.yaml` exists; all 41 `duckdb_ddl()` strings execute in an in-memory DuckDB opened with
   `get_settings().duckdb_settings("app")` (amended EP-7); the drift check passes against the vendored DDL. CLI:
   `mwh schema list | show <schema.table> | ddl <schema.table> | check` — attached in `cli.py` with one
   `app.add_typer()` line and **added to `DIAGNOSTIC_COMMANDS`** (it never touches the data root; amended EP-7);
   the contract YAML/JSON files pass the `check-yaml`/`check-json` pre-commit hooks (single-document, no tags).
   Dated note in `DESIGN.md` §7/§15 (`schema/` layout, `mwh schema` added).

## Out of scope

- Reading any CSV header or file → EP-10 (inventory) / EP-17 (loader).
- Generating synthetic rows → EP-11 / EP-12.
- Applying `COMMENT`s to a catalog, `meta.*` tables, `DATA-DICTIONARY.md` → EP-21 / EP-29.
- Itemid dictionaries, unit harmonisation, plausibility bounds per itemid → EP-39.
- Downloading or applying the demo → EP-22.

## Verification / acceptance

- `uv run poe test -m ep_9` and `uv run --group dev mwh verify EP-9` green on fixture.
- `uv run --group dev mwh schema check` exits 0 against the EP-8 vendored DDL; deliberately editing one column
  type in the YAML makes it exit non-zero (try it, then revert).
- `uv run --group dev mwh schema ddl mimiciv_icu.chartevents` prints a `CREATE TABLE` with the 11 chartevents
  columns (`subject_id, hadm_id, stay_id, caregiver_id, charttime, storetime, itemid, value, valuenum, valueuom,
  warning`) — schema text only, no data.
- Commit `feat(mimicwarehouse): schema contract YAML + loader (EP-9)`, then `docs(roadmap): record EP-9 commit hash`.

## Parked → final-roadmap.md

- Full OMOP/FHIR-style semantic annotations on the contract (concept ids per column); trigger: OMOP conversion
  (v2 OMOP-1) or FHIR demo (v2 FHIR-1).
