# EP-20 — Stage dimensions + small hosp/icu tables

**Size:** M · **Tier:** fixture+dev+full · **Core/Stretch:** core · **Depends on:** EP-19 (DAG runner `mwh build`) · **Blocks:** EP-21 (Catalog builder (per-tier .duckdb)), EP-28 (Verify full staging), EP-33 (Re-plan P2)

## Context

The first tables that live in `lake/core` for keeps: the four hosp dimensions and `d_items`,
the small subject-keyed hosp/icu tables, and the two unkeyed helper tables — everything
the catalog (EP-21), the demo tier (EP-22) and the tracer bullet (EP-31) need, so the
end-to-end proof can run while the ⏱ event tables (EP-23…EP-27) are still loading. Uses
the loader (EP-17/18) through the DAG runner (EP-19). Layout per DESIGN §5 (**D-17**,
**D-18**): dims unpartitioned, subject-keyed tables Hive-partitioned by
`subject_id % 100` and sorted `(subject_id, <time>)`. Row counts are reconciled against the
counts published in mimic-code's `validate.sql` (vendored by EP-8; parsed by EP-10 —
**D-26**) and against EP-10's raw manifest. Total input ≈ 2 GB of CSV, so the full run
finishes in minutes, but it still runs as a logged background job (`mwh build --tier
full … --background`). Plain CSVs stay untouched (**D-30**). Nothing here prints rows;
verification is by counts.

## In scope

1. **DAG steps** — extend `src/mimicwarehouse/dag/specs/stage.yaml` with one `stage` step
   per table (`tiers: [fixture, dev, full]`, tags `small` + `hosp`/`icu`/`dims`); size
   class, partitioning and sort keys come from the contract (EP-9 `load_class = "small"`,
   `partitioned`, `sort_keys`) — verify the contract says: unpartitioned for `d_hcpcs,
   d_icd_diagnoses, d_icd_procedures, d_labitems, d_items, provider, caregiver`; partitioned
   with these sort keys after `subject_id` (fix the contract with a dated note if it
   disagrees and the difference is not deliberate): `patients` — none · `admissions` `[admittime]` ·
   `transfers` `[intime, transfer_id]` · `services` `[transfertime]` · `procedures_icd`
   `[chartdate, hadm_id, seq_num]` · `diagnoses_icd` `[hadm_id, seq_num]` · `drgcodes`
   `[hadm_id]` · `hcpcsevents` `[chartdate, hadm_id, seq_num]` · `omr` `[chartdate, seq_num]` ·
   `poe_detail` `[poe_id, field_name]` · `icustays` `[intime]` · `procedureevents`
   `[starttime, orderid]` · `outputevents` `[charttime, itemid]`. Sources are
   `mimic-iv-3.1/hosp/<table>.csv` / `mimic-iv-3.1/icu/<table>.csv`. That is 15 hosp + 5 icu
   = 20 tables; with EP-23/24/25 (hosp) and EP-26/27 (icu) every table in the contract is
   covered exactly once — assert this coverage in a test.
2. **Type/format quirks** — the loader reads with the contract's declared types, so any
   parse failure is a contract error: fix it in EP-9's YAML with a dated note (examples to
   watch: `patients.dod` DATE, `procedures_icd.chartdate`/`hcpcsevents.chartdate` DATE,
   `omr.result_value` VARCHAR, `icustays.los` DOUBLE, nullable `transfers.hadm_id`,
   `services.prev_service`, `admissions.deathtime`). Never widen a column to VARCHAR just to
   make a load pass; keep `loader_reject_max = 0`.
3. **Runs** — fixture: `uv run --group dev mwh build --tier fixture --tag small --data-root <tmp>`
   (in tests). Real data:
   `uv run --group dev mwh build --tier full --tag small --background --job stage-small-full`,
   then poll `uv run --group dev mwh jobs --job stage-small-full` (about every minute;
   expected well under 10 min). The dev tier needs no separate pass — its partitions are
   buckets 0–4 of the same lake. Record job name, log path, wall time here.
4. **Count reconciliation** (`tests/ep/test_ep20.py`, `@pytest.mark.ep_20`) — fixture:
   the fixture build stages all 20 tables (rows equal fixture rows, rejects 0, layout per
   EP-18). `tier("full")`-marked: for each of the 20 tables `status.json.rows` equals the
   `validate.sql` expected count (`mimicwarehouse.inventory.expected_counts()`, EP-10)
   **and** equals EP-10's raw manifest rows (`lake/manifests/raw/mimic-iv-3.1.jsonl`);
   rejects are 0; print only a `(table, expected, actual, ok)` table.
   `tier("dev")`-marked: for each partitioned table the dev partitions exist and their manifest rows
   sum to a positive count. If `validate.sql` predates 3.1 for a table (EP-10's completion
   note says which), record the delta instead of failing.
5. **Benchmark + completion note** — ledger lines exist for the 20 steps; append
   `> **Completion note (date).**` to this brief with a table (table, rows, Parquet MB,
   files, wall s) and the observed CSV → Parquet size ratio — build telemetry only.
6. **File-count observation** — count files and directories under `lake/core` after the run
   (`os.walk`, ~15 partitioned tables × ≤ 100 + dims) and time one full `os.scandir`
   sweep; put the numbers in the completion note for EP-28/DESIGN §21.

## Out of scope

- Catalog `.duckdb` and views → EP-21; `meta.*` row counts / dictionary → EP-29.
- `labevents` → EP-23; `emar`/`emar_detail` → EP-24; `pharmacy, prescriptions, poe, microbiologyevents` → EP-25; `chartevents` → EP-26; `inputevents, ingredientevents, datetimeevents` → EP-27.
- Full reconciliation across all 31 tables, disk usage and timings summary → EP-28.
- `mimiciv_ed` tables → EP-142 (D-4); note tables → EP-148 (D-3).

## Verification / acceptance

- `uv run poe test -m ep_20` green on fixture; `tier("dev")`/`tier("full")`-marked reconciliation tests green; `uv run --group dev mwh verify EP-20` green.
- `%MWH_DATA_ROOT%\lake\manifests\status.json` shows `tier_complete = "full"` for all 20 tables; partitioned tables have `subject_bucket=<n>` directories with one `part-0.parquet` each; dims have one file.
- Job `stage-small-full` ran in the background; `runs\jobs\stage-small-full.log` exists; wall time and per-table numbers are in the completion note; the coverage test proves every contract table is assigned to exactly one staging brief.
