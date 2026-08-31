# P2 retrospective — staging, catalog, safe-query, tracer (EP-33)

Written at EP-33, the consolidation re-plan of P0–P2 (D-44; the brief's amendment block is
the charter). P2 ran 2026-08-29 → 2026-08-30 as nineteen briefs — EP-170/EP-171 at the head,
then EP-17 … EP-32 — and staged the complete core lake, the per-tier catalogs, `meta.*`, the
safe-query gate and the first end-to-end analysis (tracer bullet). This file is the phase
record the convention EP-54 inherits: baselines, planned-vs-actual, what dragged, the
full-tier wall-time table, the lake/temp measurements vs DESIGN §3, the `dev-first` verdict,
and — appended as the EP-33 session proceeds — the § Renames ledger, § Worklist outcomes,
§ Checkpoint minutes and the commit series. Integers are thousands-separated
(`inventory.fmt_int` style, guard G4).

> **Session note (2026-08-31).** The EP-33 second attempt was stopped by the owner at
> weekly-usage-limit proximity after Workstream E completed (no safeguard refusal fired —
> the first addendum's tunings held throughout); its finished outputs were salvaged into
> the repository on the owner's follow-up instruction. Done: A (+B9), the baselines below,
> D2, E1/E2 (ledger `retro-p2-findings.md`), and the B1–B6/B8 scout plans
> (`retro-p2-scout-plans.md`). The re-run resumes at the owner triage checkpoint — see the
> brief's § Second attempt for the full map. § Checkpoint minutes stays pending.

## Pre-flight baselines (recorded before any EP-33 edit; the F6 comparison base)

Session start 2026-08-30 (UTC timestamps below roll past midnight); `mwh doctor`
9 pass · 1 warn · 0 fail · 5 info (the warn is `antivirus`, by design — D-38/D-42);
AC power mode **Best performance**; disk 393.3 / 951.5 GB free; DuckDB 1.5.5 == pin;
working tree clean at `876e781`.

| Probe | Result |
|---|---|
| `uv run poe check` (ruff + pyright + pytest, fixture tier) | exit 0 · 720 passed · **219 s** |
| `mwh verify EP-k`, k ∈ 0…32 ∪ 164…171 (41 briefs, fresh interpreter each) | **0 failures** · 414 s |
| `uv run poe roadmap-check --strict` | exit 0 (0 errors, 0 warnings) |
| `mwh build --tier fixture` (first build into the data-root fixture lake / resume over it) | exit 0 · 16 s first · **3.3 s resume** (the F6 comparable) |
| `mwh --help` (import-budget probe, one cold run) | 558 ms |
| `mwh jobs` | 11 jobs, all `done`, exit 0 |
| A7 guard sweep — `guard.scan_tracked` per commit, `45f1c84` → `876e781` | **37 commits · 0 violations** · 13.2 s |
| D2 concept smoke — all vendored `concepts_duckdb` files, driver order, DuckDB 1.5.5, throwaway demo-catalog copy | **65/65 executed cleanly** · 1.78 s |

## Planned vs actual (P2 + EP-170/EP-171)

Every P2 brief finished at or under its planned size; the five ⏱ staging jobs all finished
*inside* their launching sessions (the verified-by-EP-28 convention was record-keeping, not a
wall-time necessity). Sizes: S ≈ 30 min, M ≈ 1 h, L ≈ 2 h (D-2).

| EP | Planned | Actual (completion-note basis) |
|---|---|---|
| EP-170 | M | one session (reconciliation-only; several items were verify-only — overtaken by EP-16/EP-171 allocations) |
| EP-171 | S | ≈ 45 min (canary total 13.3 s; the session cost was harness + note) |
| EP-17 | M | one session; dev probe answered SCH-1 in-line (three raw scans, ≤ 1.6 s each) |
| EP-18 | M | one session |
| EP-19 | M | one session; full-tier smoke job 0.8 s stage wall |
| EP-20 | M | ≈ 45 min; job 17.0 s for 19 tables |
| EP-21 | M | ≈ 55 min; dev catalog 1.0 s, full catalog job 0.9 s |
| EP-22 | M | one session; demo fetch + full demo build 6.4 s |
| EP-23 | M | job 138.9 s (in-session) |
| EP-24 | M | job 698 s (in-session; emar_detail is the wall-time head) |
| EP-25 | M | job 198.8 s (in-session) |
| EP-26 | L | job 137.9 s (in-session — the L sizing anticipated chartevents pain that never came) |
| EP-27 | M | job 61 s (in-session) |
| EP-28 | S | well under budget (all five jobs already done, reconciliation 31/31) |
| EP-29 | M | one session; full `meta.profile` 886,043,036 rows in 14.1 s |
| EP-30 | M | one session |
| EP-31 | M | one session; dev run 2.11 s, full run 3.3 s |
| EP-32 | S | under budget |

## What dragged / environment lessons

- **emar_detail is the staging wall-time head, not chartevents**: pass 1 438.4 s at
  13.8 MB/s (wide all-VARCHAR rows are CSV-parse-bound) vs chartevents' whole-step 137.8 s
  at 304.4 MB/s (narrow rows stream at ≈ 940 MB/s in pass 1). Peak RSS high-water is also
  emar_detail's (24,563 MB against the 36 GB build `memory_limit`).
- **Pass 2 ≥ pass 1 fired broadly** (the parked parallel-sort trigger): chartevents
  93.0 vs 44.6 s, labevents 76.4 vs 62.1 s, emar 35.4 vs 29.1 s, poe 40.3 vs 29.3 s, and
  all three EP-27 icu event tables; emar_detail/prescriptions/pharmacy/microbiologyevents
  are the exceptions (parse-bound or ≈ equal). Decision at this re-plan: keep parked (D4c).
- **DuckDB 1.5.5 semantics cost real time to learn** and are now canon (→ the C4 gotchas
  home): `COPY … APPEND` demands `{uuid}` filename patterns (so pass-1 sweeps use
  `OVERWRITE_OR_IGNORE` over disjoint bucket ranges); pass-2 reads need
  `hive_partitioning=false` or the synthesised partition column is written into the sorted
  file; an integer literal as large as `10**9` binds DOUBLE (spell it out); the in-process
  instance cache is path-keyed, so `ATTACH` of `runs.duckdb` must be `IF NOT EXISTS`;
  `sum()` returns HUGEINT and a cast around it trips the safe-query closed-set walk (the
  sanctioned pattern is `count(*) FILTER`); `.print`/`.read` driver files are CLI-dialect,
  executed file-by-file from Python.
- **Windows process reality**: a child-side state rewrite cannot record a hard crash — the
  ⏱ standard became a *detached supervisor* (`dag.jobs`, DETACHED_PROCESS) that owns the
  log and the authoritative state file. The pass-1 heartbeat's in-process rss/ctypes probes
  read 0 on this host for minutes; the runner's psutil sampler is the trustworthy number.
- **Endpoint security never interfered** with staging: no stall, nothing quarantined,
  across all five ⏱ jobs and the EP-171 canary (write baselines 191 / 239 MB/s).
- **The EP-166 doc consolidation ate three DESIGN headings** (`## 5`, `## 6`, `## 12`,
  `## 21` lead-in), restored piecemeal by EP-18/EP-19 — the C1 as-built rewrite carries the
  lesson: heading-preserving edits, verified by a structure diff.
- **One real contract falsification in all of P2**: the demo `procedureevents` 2.2 CSV
  header ships uppercase `ORIGINALAMOUNT`/`ORIGINALRATE` (EP-22) — fixed as a lossless
  rename in the column map; zero rejects on every staged table (`loader_reject_max = 0`
  held); EP-24's `parent_field_ordinal` DOUBLE expectation was rejected in favour of the
  contract's VARCHAR (ordinals like `1.1`/`1.10` would collide).

## Full-tier wall-time table (`mwh runs benchmarks --format md`, ledger `kind: verify` lines)

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

The committed narrative around these numbers is
[`../mimicwarehouse/docs/analyses/00-staging-benchmark.md`](../mimicwarehouse/docs/analyses/00-staging-benchmark.md)
(EP-32; regenerate with `mwh runs benchmarks --out …`).

## Lake & temp vs the DESIGN §3 budget

Measured at EP-28, carried here as the phase record: `lake\core` = 7,046,156,578 bytes
≈ **7.0 GB** against the planning estimate of 18–25 GB (13.8× overall compression; 2.1× on
the tiny dims up to 22.9× on chartevents). **No build-temp spill was observed** in any ⏱ job
(`tmp_duckdb` = 0 at every 60 s heartbeat); the 60–100 GB build-temp planning line never
materialised for staging. Staging peak RSS high-water 24,563 MB. The lake holds 2,407
`part-0.parquet` files + 24 `_progress.json` = 2,431 files in 2,433 directories; one
`os.scandir` sweep 0.091 s. Free space after P2: 392.8 GB (baseline session start:
393.3 GB). The derived/spine/marts estimates stay open — Workstream D3 re-estimates them
from the measured 13.8× and the D2 shapes.

## dev-first ordering verdict

**Keep.** Pass 2 sorts `settings.dev_buckets` first and flips `dev_ready` the moment they
are sorted, so the dev tier became queryable 31–66 s into each large table's build
(labevents 66 s, emar 31 s, chartevents 50 s after step start) while full passes continued —
at zero measured cost. `dev_ready(step)` is the readiness signal the EP-168 test fixtures
consume; recorded as a D-18 addendum at this EP.

## Renames (Workstream B ledger — invariant 6)

*(populated as B lands; every public-surface rename/move/merge, with the docs/tests/briefs
propagated in the same session)*

## Worklist outcomes (Workstream B — one line per item)

- **B9** — done (landed with the A4 DECISIONS edit pass): D-43's *Why/Alternatives* tail
  restored under item 14 with its lost first line recovered verbatim from `f3eb115`; the
  orphaned fragment below the addenda removed; dated correction note in place.
- **A4 repair (ledger DRF-1)** — the A4 edit pass itself consumed four decision headers
  (D-18/D-19/D-21/D-25) as edit anchors without re-emitting them; caught by the E-audit,
  restored verbatim in the salvage commit. C1 inherits the lesson: heading-preserving
  edits, verified by a structure diff, before committing DECISIONS/DESIGN changes.

## Checkpoint minutes

*(recorded after the owner triage checkpoint)*

## Commit series

- `876e781` — `docs(roadmap): record EP-33 aborted first attempt + re-run tuning (EP-33)`
- `d88e2b5` — `docs(roadmap): EP-33 workstream A - EP-32 hash, P2 retro, DECISIONS addenda, parked-trigger records (EP-33)`
- `2aa9e65` — `docs(roadmap): EP-33 second-attempt salvage - audit ledger, scout plans, D2 amendments, brief map, DRF-1 repair (EP-33)` *(per-commit guard sweep re-run over the extended series: 39 commits `45f1c84` → `2aa9e65`, 0 violations)*

*(appended as the series lands)*
