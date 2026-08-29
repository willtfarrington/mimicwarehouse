# EP-20 — Stage dimensions + small hosp/icu tables

**Size:** M · **Tier:** fixture+dev+full · **Core/Stretch:** core · **Depends on:** EP-19 (DAG runner `mwh build`) · **Blocks:** EP-21 (Catalog builder (per-tier .duckdb)), EP-28 (Verify full staging), EP-33 (Re-plan P2)

> **Amended at EP-170 (2026-08-29).** Header facts unchanged; shorthand per the README notation
> table. (1) The item 1/items 4 coverage assertion is scoped to **hosp + icu**: the contract holds
> 41 tables, but P2 stages exactly the 31 `mimiciv_hosp` (22) + `mimiciv_icu` (9) tables —
> `mimiciv_ed` (6) has no stage step until EP-142, `mimiciv_note` (4) none until EP-148
> (segregated lake); assert the negative too (DESIGN §5 note) [ARCH-14]. (2) Item 4 reconciles
> against `validate.sql` only where the contract's `expected_rows_source` is set — `provider` and
> `caregiver` have no upstream count (nor does EP-27's `ingredientevents`); those reconcile
> against EP-10's raw-manifest rows only, via `inventory.expected_counts()`/`reconcile()`
> semantics. The "if validate.sql predates 3.1" clause is moot: EP-10 found every upstream count
> matches (34 match · 0 mismatch · 7 without expectation) [FC-12]. (3) Step declaration: **option
> B** — declare only these 20 steps here, and EP-21's fixture-lake build drops its `--tag small`
> filter so the fixture lake (and `fixture_lake_catalog`) grows automatically as EP-23 … EP-27
> add their fixture-tier steps; EP-12's in-memory 31-table `fixture_catalog` is untouched.
> (Option A — declaring all 31 steps now with event tables `tiers: [fixture]` — only if EP-17/18
> made it trivial.) [FC-11, FXT-11]. (4) DATE-grain time columns (`patients.dod`,
> `procedures_icd`/`hcpcsevents`/`omr.chartdate`) load as the contract's DATE type; the
> DATE-grain *semantics* rule belongs to EP-34/49/50 — do not add contract markers here
> [ARCH-15].

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

> **Completion note (2026-08-29).** Executed in one session (≈ 45 min against M ≈ 1 h). No contract
> fix was needed: EP-9's YAML already said exactly what item 1 pins (load_class `small`,
> partitioning, sort keys for all 20 tables — the amendment's EP-169 tie-breaks included), and the
> full-tier load hit **zero** type/format quirks (item 2): 0 rejects on every table,
> `loader_reject_max = 0` kept.
>
> **Item 1 (spec).** `dag/specs/stage.yaml` grew from 3 stage steps to the 20 (15 hosp + 5 icu),
> each `tiers: [fixture, dev, full]` and only `schema/table/source` (contract stays the authority —
> the shape test asserts no per-step overrides); `catalog` now depends on all 20. Tags: the brief's
> `hosp`/`icu` shorthand landed as EP-19's shipped schema tags `mimiciv_hosp`/`mimiciv_icu` plus
> `stage`, with `small` (the run selector) and `dims` (the 7 unpartitioned dimensions) added.
> Option B of the EP-170 amendment: only these 20 steps are declared; EP-23…EP-27 add theirs.
>
> **Items 3/4 (runs + reconciliation).** Fixture: `mwh build --tier fixture --tag small` in
> `tests/ep/test_ep20.py` (rows equal the committed fixture manifest's, rejects 0, EP-18 layout,
> 20 ledger lines). Full: job **`stage-small-full`** (build id `20260829T180116-full-2a513e4`,
> log `C:\mimicdata\runs\jobs\stage-small-full.log`), started 18:01:15Z, finished 18:01:33Z —
> **17.0 s wall** for 19 tables; `stage.mimiciv_hosp.patients` was **skipped** as already
> `tier_complete = "full"` from EP-19's smoke job (resume semantics working as designed; its row
> below is EP-19's ledger line, wall 0.8 s). Snapshot `core/full =
> cb54d4abf098f15ac8bf0d45907e244656ff1838bcadce25b9b166118b57ede5`. Dev needed no separate pass
> (buckets 0–4 of the same lake). `uv run poe test -m ep_20 --tier full`: 6 passed — all 18
> tables with a `validate.sql` expectation match it exactly and every table matches EP-10's
> raw-manifest rows (`provider`/`caregiver` raw-manifest only, per the amendment); rejects 0;
> `tier_complete = "full"` for all 20.
>
> **Item 5 (benchmark table, full tier — build telemetry only).**
>
> | table | rows | Parquet MB | files | wall s |
> |---|---:|---:|---:|---:|
> | mimiciv_hosp.patients | 364,627 | 2.5 | 100 | 0.8 |
> | mimiciv_hosp.admissions | 546,028 | 17.9 | 100 | 1.1 |
> | mimiciv_hosp.transfers | 2,413,581 | 43.3 | 100 | 1.5 |
> | mimiciv_hosp.services | 593,071 | 7.2 | 100 | 0.7 |
> | mimiciv_hosp.diagnoses_icd | 6,364,488 | 21.9 | 100 | 1.5 |
> | mimiciv_hosp.procedures_icd | 859,655 | 6.3 | 100 | 0.8 |
> | mimiciv_hosp.drgcodes | 761,856 | 6.0 | 100 | 0.9 |
> | mimiciv_hosp.hcpcsevents | 186,074 | 1.7 | 100 | 0.7 |
> | mimiciv_hosp.omr | 7,753,027 | 27.2 | 100 | 2.1 |
> | mimiciv_hosp.poe_detail | 8,504,982 | 43.1 | 100 | 3.1 |
> | mimiciv_hosp.d_labitems | 1,650 | 0.0 | 1 | 0.0 |
> | mimiciv_hosp.d_hcpcs | 89,208 | 0.3 | 1 | 0.1 |
> | mimiciv_hosp.d_icd_diagnoses | 112,107 | 0.9 | 1 | 0.1 |
> | mimiciv_hosp.d_icd_procedures | 86,423 | 0.7 | 1 | 0.1 |
> | mimiciv_hosp.provider | 42,244 | 0.1 | 1 | 0.0 |
> | mimiciv_icu.icustays | 94,458 | 3.0 | 100 | 0.6 |
> | mimiciv_icu.procedureevents | 808,706 | 24.8 | 100 | 1.3 |
> | mimiciv_icu.outputevents | 5,359,395 | 49.4 | 100 | 2.0 |
> | mimiciv_icu.d_items | 4,095 | 0.1 | 1 | 0.0 |
> | mimiciv_icu.caregiver | 17,984 | 0.0 | 1 | 0.0 |
> | **total** | 34,963,659 | 256.6 | 1,307 | 17.5 |
>
> CSV → Parquet: 2,013.5 MB → 256.6 MB = **7.85 : 1** (ZSTD-3, sorted, typed).
>
> **Item 6 (file-count observation, for EP-28 / DESIGN §21).** After the run `lake/core` holds
> **1,320 files in 1,322 directories** (13 partitioned tables × 100 `subject_bucket=` dirs + 20
> table dirs + 2 schema dirs; files = 1,307 Parquet + 13 `_progress.json`); one full `os.scandir`
> sweep over the tree takes **0.046 s**.
>
> **Earlier tests touched** (README § acceptance phrasing rule): `tests/ep/test_ep19.py::`
> `test_shipped_spec_orders_and_selects` pinned the shipped spec's exact step list (a shipped fact
> this brief legitimately changes); it now asserts the EP-19 mechanics (spec order preserved,
> `catalog` last, `dims` selection works) instead of the 3-step list. `mwh verify EP-19` still
> exits 0 (all 8 tests), full suite 597 passed.
