# 00 — Staging benchmark: raw CSV → core lake (P2)

> Build telemetry only — no patient-level aggregates. Passed `mwh disclose check` at EP-43
> (the retroactive check of EP-33 amendment a; see [README.md](README.md)); the sidecar
> `00-staging-benchmark.md.disclosure.json` sits beside this file — re-run
> `mwh disclose check docs/analyses/00-staging-benchmark.md --write-sidecar` after
> regenerating the table.

**Claim type: exploratory** (build telemetry; single machine, single runs). All MIMIC-IV
analyses in this project are retrospective.

*Reader guide (DS/ML):* what it costs to stage 97 GB of hospital-scale CSV into a typed,
partitioned Parquet lake on one laptop — per-table timings, throughput, memory and the
file-count trade-off, reproducible from an append-only ledger.
*Reader guide (clinical informatics):* no clinical content here — this note measures the
data engineering that every later analysis stands on; row counts are the dataset's own
published table sizes.

## Question

How long, how big, and how fast is the raw → lake staging of MIMIC-IV 3.1 (hosp + icu
modules, 31 tables) on this machine?

## Data & tier

- **Tier:** full (every subject). Inputs are the raw MIMIC-IV 3.1 CSVs
  (97,190,431,138 bytes across the 31 hosp/icu tables, per the EP-10 raw manifest);
  outputs are the `lake/core` Parquet layer and the full-tier catalog.
- **Core snapshot id:**
  `b1fc53134348f3b4ede369ed6ae27424c4988fafb45065c87a09907f7a410eca` (unchanged from
  the last ⏱ staging job through the EP-28 verification; full catalog build
  `20260829T215141-full-691c974`).
- **Stage build ids** (latest per step in the benchmark ledger, all 2026-08-29):
  `20260829T174216-full-642f6ef` · `20260829T180116-full-2a513e4` ·
  `20260829T192353-full-b22528f` · `20260829T193854-full-182dcff` ·
  `20260829T200147-full-691fa6c` · `20260829T202417-full-e431ca2` ·
  `20260829T204546-full-5c4c49c`.
- MIMIC-IV analyses are retrospective; this note contains no patient-level data — only
  row counts (also published in the vendored `validate.sql`), bytes, timings, RSS and
  file counts (GOVERNANCE §3).

## Method

The loader stages each table with a typed DuckDB `COPY` from CSV to Parquet against the
EP-9 schema contract, hash-partitioning subject-keyed tables into 100
`subject_bucket=NN` directories (dimensions land as a single file). Small tables go in
one pass; the five large ⏱ tables use a two-pass path — pass 1 sweeps the CSV into raw
per-bucket partitions, pass 2 sorts and publishes each bucket — resumable per bucket
under a manifest, with one benchmark-ledger line per pass. The `mwh build` DAG runner
is the only lake writer; full-tier work runs as detached background jobs
(`runs/jobs/<name>.log`), and every step appends telemetry to `runs/benchmarks.jsonl`.

Machine (DESIGN §2): Intel Core Ultra 9 285H (16 cores) · 64 GB RAM · one 954 GB NVMe ·
Windows 11 Pro · power mode *Best performance* on AC · two real-time endpoint products
(Windows Defender, data root excluded; Malwarebytes 5.1 Premium with its own allow
list). Engine: DuckDB 1.5.5 with explicit `memory_limit` 36 GB, `threads` 12,
`temp_directory` on the data-root volume (max 150 GB),
`preserve_insertion_order=false`.

## Results

Per-table telemetry from the benchmark ledger — latest full-tier `stage` line per step;
`pass 1 s`/`pass 2 s` only on the two-pass tables; `MB/s` is CSV bytes in per total
wall second; the totals row's peak RSS is the high-water, not a sum. The table between
the markers is generated — edit it only via
`uv run --group dev mwh runs benchmarks --out <this file>`.

<!-- benchmarks:begin -->
| table | rows | CSV GB | Parquet GB | ratio | files | pass 1 s | pass 2 s | total s | MB/s | peak RSS MB |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| mimiciv_hosp.admissions | 546,028 | 0.09 | 0.02 | 5.2x | 100 | - | - | 1.1 | 85.9 | 77 |
| mimiciv_hosp.d_hcpcs | 89,208 | 0.00 | 0.00 | 10.8x | 1 | - | - | 0.1 | 58.4 | 131 |
| mimiciv_hosp.d_icd_diagnoses | 112,107 | 0.01 | 0.00 | 9.9x | 1 | - | - | 0.1 | 124.3 | 144 |
| mimiciv_hosp.d_icd_procedures | 86,423 | 0.01 | 0.00 | 11.0x | 1 | - | - | 0.1 | 118.4 | 145 |
| mimiciv_hosp.d_labitems | 1,650 | 0.00 | 0.00 | 4.6x | 1 | - | - | 0.0 | 4.6 | 114 |
| mimiciv_hosp.diagnoses_icd | 6,364,488 | 0.18 | 0.02 | 8.3x | 100 | - | - | 1.5 | 118.4 | 88 |
| mimiciv_hosp.drgcodes | 761,856 | 0.05 | 0.01 | 9.2x | 100 | - | - | 0.9 | 63.5 | 92 |
| mimiciv_hosp.emar | 42,808,593 | 6.25 | 0.64 | 9.7x | 100 | 29.1 | 35.4 | 64.7 | 96.6 | 6,853 |
| mimiciv_hosp.emar_detail | 87,371,064 | 8.68 | 0.67 | 13.0x | 100 | 438.4 | 191.6 | 630.1 | 13.8 | 24,563 |
| mimiciv_hosp.hcpcsevents | 186,074 | 0.01 | 0.00 | 6.8x | 100 | - | - | 0.7 | 18.3 | 145 |
| mimiciv_hosp.labevents | 158,374,764 | 18.40 | 1.79 | 10.3x | 100 | 62.1 | 76.4 | 138.7 | 132.7 | 9,827 |
| mimiciv_hosp.microbiologyevents | 3,988,224 | 0.91 | 0.08 | 11.0x | 100 | 8.0 | 7.0 | 15.0 | 60.5 | 1,582 |
| mimiciv_hosp.omr | 7,753,027 | 0.32 | 0.03 | 11.8x | 100 | - | - | 2.1 | 150.4 | 145 |
| mimiciv_hosp.patients | 364,627 | 0.01 | 0.00 | 4.8x | 100 | - | - | 0.8 | 15.8 | 74 |
| mimiciv_hosp.pharmacy | 17,847,567 | 3.98 | 0.40 | 9.9x | 100 | 25.9 | 25.6 | 51.6 | 77.0 | 7,347 |
| mimiciv_hosp.poe | 52,212,109 | 5.10 | 0.37 | 13.8x | 100 | 29.3 | 40.3 | 69.7 | 73.2 | 9,796 |
| mimiciv_hosp.poe_detail | 8,504,982 | 0.42 | 0.04 | 9.8x | 100 | - | - | 3.1 | 135.5 | 1,218 |
| mimiciv_hosp.prescriptions | 20,292,611 | 3.49 | 0.45 | 7.7x | 100 | 33.9 | 28.1 | 62.1 | 56.2 | 7,236 |
| mimiciv_hosp.procedures_icd | 859,655 | 0.03 | 0.01 | 5.4x | 100 | - | - | 0.8 | 40.5 | 90 |
| mimiciv_hosp.provider | 42,244 | 0.00 | 0.00 | 2.1x | 1 | - | - | 0.0 | 11.4 | 146 |
| mimiciv_hosp.services | 593,071 | 0.03 | 0.01 | 3.6x | 100 | - | - | 0.7 | 35.7 | 84 |
| mimiciv_hosp.transfers | 2,413,581 | 0.21 | 0.04 | 4.7x | 100 | - | - | 1.5 | 138.1 | 81 |
| mimiciv_icu.caregiver | 17,984 | 0.00 | 0.00 | 2.2x | 1 | - | - | 0.0 | 4.8 | 679 |
| mimiciv_icu.chartevents | 432,997,491 | 41.94 | 1.83 | 22.9x | 100 | 44.6 | 93.0 | 137.8 | 304.4 | 4,729 |
| mimiciv_icu.d_items | 4,095 | 0.00 | 0.00 | 4.7x | 1 | - | - | 0.0 | 15.0 | 679 |
| mimiciv_icu.datetimeevents | 9,979,761 | 1.09 | 0.05 | 22.4x | 100 | 3.1 | 4.9 | 8.1 | 134.7 | 626 |
| mimiciv_icu.icustays | 94,458 | 0.01 | 0.00 | 4.9x | 100 | - | - | 0.6 | 22.7 | 169 |
| mimiciv_icu.ingredientevents | 14,253,480 | 2.47 | 0.21 | 11.5x | 100 | 10.3 | 11.8 | 22.2 | 111.3 | 2,821 |
| mimiciv_icu.inputevents | 10,953,713 | 2.87 | 0.29 | 10.0x | 100 | 13.9 | 14.9 | 28.9 | 99.1 | 3,539 |
| mimiciv_icu.outputevents | 5,359,395 | 0.46 | 0.05 | 9.4x | 100 | - | - | 2.0 | 233.5 | 678 |
| mimiciv_icu.procedureevents | 808,706 | 0.15 | 0.02 | 6.1x | 100 | - | - | 1.3 | 114.5 | 169 |
| total | 886,043,036 | 97.19 | 7.05 | 13.8x | 2,407 | 698.9 | 529.1 | 1246.5 | 78.0 | 24,563 |
<!-- benchmarks:end -->

Against the DESIGN §3 estimate: the core lake measures **7.05 GB versus the planned
18–25 GB** (2.6–3.6× under), a **13.8× overall compression** of the 97.19 GB CSV
(per-table 2.1× on the tiny dimensions up to 22.9× on chartevents). Total engine wall
across all 31 tables ≈ 1,247 s (≈ 21 min, summed over jobs that ran sequentially);
staging peak RSS high-water 24,563 MB (emar_detail) against the 36 GB `memory_limit`,
and DuckDB never spilled to its temp directory (0 bytes at every 60 s heartbeat).

Bucket-count and endpoint-security observations (EP-28, DESIGN §21): the complete lake
holds **2,407 `part-0.parquet` files + 24 `_progress.json` markers = 2,431 files in
2,433 directories** — under the ≈ 3,000-file planning estimate — and one `os.scandir`
sweep of the whole tree takes **0.091 s**. No antivirus stall or quarantine was
observed across the five ⏱ jobs (Defender excludes the data root per D-38;
Malwarebytes runs its own allow list). The 100-bucket scheme costs nothing measurable
on this host and stays. Free disk after staging: 392.8 GB (floor: 100 GB).

## What it deliberately does not claim

- **Single-laptop, single-run numbers** — one machine, one run per table, no variance
  estimate; thermal effects (a long job warming the chassis) are unquantified.
- **Not a DuckDB benchmark** — timings mix DuckDB, this project's loader design,
  Windows, NTFS and two live antivirus products; they characterize this pipeline on
  this host, nothing more.
- **No patient-level content** — nothing here supports (or is) a clinical finding.
- MB/s figures are CSV-bytes-per-wall-second of whole steps, not device throughput.

## Reproduction

Stage jobs (EP-20, EP-23 … EP-27; each resumable — rerunning the same command resumes
an interrupted job), then render this note's table from the ledger:

```powershell
cd mimicwarehouse
uv run --group dev mwh build --tier full --tag small --background --job stage-small-full
uv run --group dev mwh build --tier full --select stage.mimiciv_hosp.labevents --background --job stage-labevents-full
uv run --group dev mwh build --tier full --tag emar --background --job stage-emar-full
uv run --group dev mwh build --tier full --tag hosp-rest --background --job stage-hosp-rest-full
uv run --group dev mwh build --tier full --select stage.mimiciv_icu.chartevents --background --job stage-chartevents-full
uv run --group dev mwh build --tier full --tag icu-events --background --job stage-icu-events-full
uv run --group dev mwh build --tier full --select catalog --background --job catalog-full-p2
uv run --group dev mwh runs benchmarks --format md
```

Every number in the Results table reproduces from `runs/benchmarks.jsonl` via the last
command; the narrative's file counts and scan timing come from the ledger's
`kind: verify` lines (EP-28, build prefix `-verify-`).

## Provenance

Generated at git sha `de50fb8` · core snapshot
`b1fc53134348f3b4ede369ed6ae27424c4988fafb45065c87a09907f7a410eca` · environment hash
(`uv.lock` git blob) `d70a8e8ec0dec6052cb465e3a96f571190c3b64f` · DuckDB 1.5.5 ·
uv-managed CPython 3.13.
