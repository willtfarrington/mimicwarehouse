# mimicwarehouse — DESIGN

Architecture of the local MIMIC-IV data lab. This document is the "why and how"; the
roadmap (`../roadmap/README.md`) is the "when". Every module is marked with the EP brief
that built or will build it; the living inventory of what exists is the workspace
`README.md` § "State of the workspace" (D-43 item 13).

> **Consolidated at EP-33 (2026-09-01).** Every section below is rewritten to as-built
> truth as of the close of P2 (EP-0 … EP-33, EP-164 … EP-171; D-44 item 4). The 54 dated
> `> **Note (…)**` blocks that P0–P2 sessions appended were folded into their sections'
> bodies wherever they corrected or completed the 2026-08-16 planning text, and the
> planning prose that shipped code superseded was dropped — **git history is the archive**
> (this file at any commit before the EP-33 docs commit holds the dated notes verbatim).
> Each section ends with a *History* line naming the EPs whose completion notes and tests
> hold the evidence. Sections about phases not yet built (§8–§10, §13, §14, §16–§19) remain
> *design*, lightly reconciled with the shipped surfaces they will call. §21 stays a live
> open-questions list. From here on, sessions append dated notes again as before: when an
> EP changes a design fact, add a `> **Note (date, EP-n).**` under the section — do not
> rewrite history between consolidations.

Owner decisions are cited as **D-n** (see [`DECISIONS.md`](DECISIONS.md)). Safety and
licensing rules live in [`GOVERNANCE.md`](GOVERNANCE.md) and override anything here.

---

## 1. Purpose & non-goals

**Purpose.** A single-user, single-machine warehouse over MIMIC-IV 3.1 (hosp + icu),
MIMIC-IV-ED 2.2 and MIMIC-IV-Note 2.2 that supports (a) exploratory analysis and
visualization and (b) prospective-style, protocol-frozen inquiry over retrospective data,
with one tested end-to-end representative workflow for each of the 38 capability
categories listed in the roadmap, end-to-end provenance, and disclosure discipline. The
tie-breaker when scope, depth and polish compete is **portfolio value** (D-1).

**Non-goals for v1.** Multi-user access, network services, cloud deployment, OMOP/FHIR
conversion, an R toolchain, a JS front-end, and any analysis that requires row-level data
to leave this machine. All of these are catalogued in
[`../roadmap/final-roadmap.md`](../roadmap/final-roadmap.md).

**What P2 proved.** The first end-to-end proof (D-8) is the tracer bullet (EP-31, §15):
first-ICU-stay adults → in-hospital mortality, attrition and descriptives through
`safe_query`, a logistic fit, a run folder that passes the committed-text canon — run on
every tier including full.

*History:* planning text (2026-08-16); EP-31 added the tracer paragraph; consolidated at EP-33.

## 2. Machine & constraints (verified 2026-08-16)

| Resource | Value | Design consequence |
|---|---|---|
| CPU | Intel Core Ultra 9 285H, 16 cores | DuckDB `threads=12` in both profiles; leave headroom for the UI |
| RAM | 64 GB | `memory_limit` **36 GB** (`build` profile) / **12 GB** (`app` profile), `Settings.duckdb_*`; never `pandas.read_csv` a large table |
| GPU | RTX PRO 2000 Blackwell laptop, 8 GB VRAM, sm_120 | CPU-first; GPU is an opt-in dependency group (D-16); batch sizes ≤ 6 GB working set |
| Disk | one 954 GB NVMe; **392.8 GB free after P2 staging** (EP-28; 414.9 GB at EP-7) | see §3 budget; keep ≥ 100 GB free during builds |
| OS | Windows 11 Pro, PowerShell 7 | native Windows (D-14); `spawn` multiprocessing; MAX_PATH; `.gitattributes` for CRLF |
| Python | uv-managed CPython 3.13 (D-15) | system 3.14 untouched (`python-preference = only-managed`) |
| Encryption | BitLocker on C: | required by the DUA; recorded by `mwh doctor` |
| Cloud | GoogleDriveFS (G:), Cryptomator (D:) mounted | nothing warehouse-related may live on G:/D: (file locks, sync = redistribution) |
| Endpoint security | Windows Defender **and** Malwarebytes 5.1 Premium, both real-time (D-42) | every long writer is resumable and logs progress; sessions write files through the Write/Edit tools, never shell heredocs or burst copy/delete loops; `mwh doctor` reports both products |

**Toolchain (EP-1, re-verified EP-7).** uv 0.12.5 (winget, user scope; cache and managed
interpreters on C:) and uv-managed CPython 3.13.15 (`.python-version` = `3.13`, `.venv` in
the workspace). Resolved core stack: pandas 3.0.5 · numpy 2.5.2 · scipy 1.18.0 ·
polars 1.43.2 · pyarrow 24.0.0 · statsmodels 0.14.6 · lifelines 0.30.0 · scikit-learn 1.9.0 ·
altair 6.2.2 · pydantic 2.13.4 · psutil (core since EP-19); `ui`: Streamlit 1.61.1
(`pyarrow<25,>=7.0`). uv unified both resolver forks on pyarrow 24.0.0, so one venv serves
core and `ui` today; the `[tool.uv] conflicts` fork machinery is in place for when they
diverge. The only sdist-only package in the lock is `autograd-gamma 0.5.0` (pure Python).

**Console.** `.claude/settings.json` sets `PYTHONUTF8=1` for both tool shells (EP-165,
D-43 item 2), so `mwh` run from a session has UTF-8 stdio; other hosts (a plain
PowerShell, Task Scheduler) may still be cp1252. The `mwh` entry point is
`mimicwarehouse.console:run`, which reconfigures stdout/stderr to UTF-8 with
`errors="replace"` before the typer app runs (EP-167); new CLI strings stay ASCII or pass
through `console.console_safe`; JSON outputs end in plain `\n`. Roadmap Risk 13 points here.

**Host diagnostics.** `mwh doctor` runs 15 checks (`python uv duckdb settings disk_free
data_root temp_dir cloud_mounts defender antivirus deny_coverage bitlocker power_scheme gpu
longpaths`); on this host it ends 9 pass · 1 warn · 0 fail · 5 info, the warn being the
`antivirus` row by design (a non-Defender product is *listed*; its exclusion list is not
readable non-elevated). The owner toggles the Windows power mode off between sessions —
`power_scheme` must read Best performance before compute-heavy work (D-38).

*History:* built by EP-1, EP-7, EP-164, EP-165, EP-166, EP-167 (completion notes); consolidated at EP-33.

## 3. Layers & disk budget

```
raw        source material/<dataset>/<module>/*.csv      immutable, gitignored, never edited (D-30)
  │  EP-17/18 loader (typed COPY → Parquet, subject buckets)
lake       <lake_root(tier)>\core\<schema>\<table>\…      canonical typed snapshot (lake\ for dev/full; lake\fixture, lake\demo for the synthetic/ODbL tiers)
  │  EP-21 catalog builder
catalog    warehouse\{fixture,demo,dev,full}.duckdb       views over the lake + materialized dims + meta.*; runs.duckdb = views over the JSONL ledgers
  │  EP-19 DAG runner (mwh build) · EP-37 concept runner
derived    lake\derived\<concept|phenotype|spine>\…       mimic-code concepts, phenotypes, events spine (P3)
  │  EP-47 cohort compiler · EP-55/56 marts
marts      lake\marts\…  + studies\<study_id>\…            cohorts, feature matrices, latency marts (P3/P4)
```

Everything below `raw` is **rebuildable from raw + code**, so the honest backup of the
warehouse is a tested rebuild recipe (`mwh init`, EP-158). Non-reproducible state (run
ledger, protocol registry, audit) is backed up separately (EP-52).

**The data-root tree** is fixed by `Settings.layout` (`config.py`, `LAYOUT_KEYS`; **18
keys**, created idempotently by `mwh paths --create`, which also writes a `README.txt`
warning never to sync the folder). Layout keys in brackets — briefs reference the keys,
never literal paths:

```
C:\mimicdata\                          MWH_DATA_ROOT (D-29; local fixed NTFS/ReFS only)
├── README.txt                         "managed by mimicwarehouse; never sync"
├── lake\                              lake            Parquet layers (§5); the dev/full lake root
│   ├── core\                          lake_core       typed snapshot of raw (EP-17/18; 31 hosp + icu tables staged full-tier)
│   ├── derived\                       lake_derived    concepts, phenotypes, spine (EP-37+/50)
│   ├── marts\                         lake_marts      cohorts, features, latency marts (EP-47/55)
│   ├── manifests\                     lake_manifests  raw\<dataset-dir>.jsonl + raw_snapshot.json (EP-10) · <build_id>.jsonl · status.json · snapshots.json (EP-17/19)
│   ├── fixture\                       lake_fixture    the fixture tier's own lake root (core\, manifests\, rejects\ beneath it)
│   ├── demo\                          lake_demo       the demo tier's own lake root
│   ├── rejects\                       lake_rejects    <schema>\<table>\<build_id>.parquet reject rows (EP-17; dev/full)
│   └── meta\                                          <tier>\profile_*.parquet (EP-29; module-created, not a key)
├── warehouse\                         warehouse       {fixture,demo,dev,full}.duckdb (EP-21) · runs.duckdb (EP-30) · .build.lock (EP-19)
├── runs\                              runs            audit.jsonl (EP-30) · benchmarks.jsonl (EP-19) · tracer\<run>\ (EP-31) · ledger.jsonl + <run_id>\ (EP-35)
│   └── jobs\                          runs_jobs       <name>.json state files + <name>.log of detached jobs (EP-19)
├── models\                            models          model registry artefacts (P7)
├── notes\                             notes           segregated notes lake + notes.duckdb (EP-148, owner-only)
├── ext\                               ext             external sources <source>\source.yaml (§19)
│   └── demo\                          ext_demo        MIMIC-IV Demo 2.2 + ED Demo 2.2 + source.yaml (EP-22)
├── studies\                           studies         study workspaces <study_id>\ (§3 marts)
└── tmp\                               tmp             scratch (canary\ = the EP-171 write canary tree)
    └── duckdb\                        tmp_duckdb      DuckDB temp_directory (§6; MWH_DUCKDB_TEMP_DIR overrides, same volume)
```

`Settings` refuses a data root that is not on a local `DRIVE_FIXED` NTFS/ReFS volume, whose
volume label matches a sync client (Google Drive, OneDrive, Dropbox, Box, Cryptomator,
iCloud), that lies under `%OneDrive%`, or whose drive letter is in `forbidden_drives`
(default G:, D:); the temp dir must share the data-root volume. On this machine the probes
see C: = fixed NTFS "Windows", D: = remote cryptoFs "Google Cryptomator", G: = fixed FAT32
"Google Drive" — the last two are refused by label, filesystem and letter. Fixture and demo
builds resolve to their own lake roots (`Settings.lake_root(tier)`) and hard-refuse the
credentialed `lake\core` (`assert_not_credentialed_lake`, EP-167), so a stray
`mwh build --tier fixture` can never pollute the real lake or its `status.json`.

**Disk budget.** Rule: **never below 100 GB free** (`min_free_gb`); `mwh paths --create`,
`mwh build`, `mwh inventory build` and `mwh canary write` refuse to start under it
(`require_free_space`; the guard is per tier — `min_free_gb_for("fixture")` is 1 GB).
Measured at the close of P2 (EP-28; the ledger's `kind: verify` lines carry the per-table
numbers): raw CSV 97,190,431,138 bytes (kept) · **core lake 7,046,156,578 bytes ≈ 7.0 GB**
for all 31 hosp + icu tables (13.8× overall compression, 2.1× on the tiny dims up to 22.9×
on chartevents — 2.6–3.6× under the 18–25 GB planning estimate) · build-temp peak **no
spill observed** across the five ⏱ staging jobs (`tmp_duckdb` = 0 at every heartbeat;
staging RSS high-water 24,563 MB on emar_detail against the 36 GB build `memory_limit`).
Re-estimated for P3 (EP-33 item D3; Risk 6's open half — re-estimates, not measurements;
EP-37/EP-50's ⏱ jobs record the real sizes and EP-54 replaces these numbers): the 65
vendored `concepts_duckdb` files executed on a throwaway copy of the ODbL demo catalog in
2.2 s and re-encoded to 131,517 rows / 1,824,855 bytes of ZSTD Parquet at demo scale
(140 ICU stays); the concepts are ICU-stay-keyed and scale ≈ 675× (94,458 / 140), so
**derived concepts ≈ 90 M rows, 0.8–1.5 GB**; the MEDS-shaped spine (EP-50; ≈ 262 M
non-chartevents rows at ≈ 10 bytes/row plus an optional vitals subset) **2.5–4 GB**;
phenotypes and cohorts negligible; first-day / hourly-bin marts (EP-55/56) **≤ 1–2 GB**.
Revised lines: derived + spine ≈ 4–6 GB (was 15–30) · marts ≈ 1–2 GB (was 5–15) · notes lake
+ FTS + embeddings 5–15 GB (P10 only) · models ≤ 10 GB · uv cache + venv ≈ 15 GB · OS
hiberfil/pagefile 25–40 GB. Concept builds are per-concept aggregations over the
chartevents/labevents views; a worst-case single pass over all 433 M chartevents rows needs
≈ 17 GB of hash/sort state, so spill is possible but bounded (≤ 20–40 GB) under the 150 GB
`max_temp_directory_size` cap; expected free space after P3 ≈ 380 GB.

*History:* built by EP-3, EP-10, EP-17, EP-19, EP-21, EP-22, EP-28, EP-29, EP-166, EP-167, EP-171, EP-33 item D3 (completion notes); consolidated at EP-33.

## 4. Tiers & sampler spec (D-18, D-27)

| Tier | What | Where | Used for |
|---|---|---|---|
| `fixture` | synthetic mini-MIMIC generated by `mimicwarehouse.fixtures` (EP-11/12); **all ids ≥ 90 000 000** so the guard can recognise synthetic rows; sources committed to `mimicwarehouse/tests/fixtures/` | repo (sources) · `lake\fixture` + `warehouse\fixture.duckdb` when built for keeps | pytest default; CI-like runs without credentials; `mwh sql --tier fixture` and row-view development (GOVERNANCE §6) |
| `demo` | MIMIC-IV Clinical Database Demo 2.2 + MIMIC-IV-ED Demo 2.2 (ODbL, 100 subjects), fetched by `mwh demo fetch` (EP-22), passed through the 2.2 → 3.1 column map | `ext\demo\` → `lake\demo` → `demo.duckdb` | screenshots, cloner path, concept count-pinning, showcase |
| `dev` | deterministic 5 % of full: `subject_bucket IN settings.dev_buckets` (default `[0, 1, 2, 3, 4]`) — a partition filter over the same lake, so it cannot drift from full | `dev.duckdb` | every EP's development + tests |
| `full` | all subjects | `full.duckdb` | recorded full-tier runs; scale-contract EPs |

`subject_bucket = subject_id % 100` (`loader.buckets.NUM_BUCKETS`; MIMIC subject_ids are
10 000 000–19 999 999) is the single partitioning key for subject-keyed tables; dims are
unpartitioned. Tables without `subject_id` (`d_*`, `provider`, `caregiver`) exist
identically in every tier. The dev buckets live in `settings.dev_buckets` — no separate
constant (D-43 item 14); a catalog records the buckets it was built with and warns on drift.
Dev and full share one lake root: a table is `dev_ready` as soon as the dev buckets are
sorted and `tier_complete: "full"` once all 100 are (§5), so the dev catalog attaches
early during a full ⏱ pass and a dev rebuild can never discard the other 95 partitions
(the coverage guard, §5).

**Demo tier vs demo mode.** *Demo tier* = the ODbL dataset loaded as a tier. *Demo mode*
= the app (EP-159) launched with `--tier demo` and export/row-view features enabled,
because that data is redistributable. Never confuse the two in briefs. Demo ids sit inside
the real MIMIC bands, so the guard treats demo rows as real: the data lives only under the
data root, never in git.

Every brief states its tier using the vocabulary in the roadmap README (`fixture` /
`fixture+dev` / `fixture+dev+full` / `fixture+dev (full ⏱ → verified by EP-n)` / `demo` /
`n/a`).

**Fixture tier as built (EP-11/12, EP-169).** `mwh fixtures build [--out DIR] [--seed N]
[--subjects N] [--no-check] [--json]` writes `tests/fixtures/mimic-iv-3.1/{hosp,icu}/<table>.csv`
(22 + 9 tables, contract column order, raw-layout paths so the loader can point
`--source tests/fixtures/mimic-iv-3.1` at it), `manifest.json` (per file sha256 / bytes /
rows / seed / generator version; the spec; `contract_schema_hash` = `Contract.structural_hash()`,
load-relevant facts only, so a comment-only contract edit never forces a regeneration;
numpy/polars/python versions as provenance) and `README.md`. Defaults = the committed
fixture: seed 2026, **120 subjects** with consecutive `subject_id`s from 90 000 000 (so
`subject_id % 100` spans every bucket and the dev filter keeps exactly 10), 186 admissions,
75 ICU stays (`icustays` = `plan.icu_segments` verbatim, so it agrees with `transfers` by
construction), **50,974 rows / 5.12 MiB** (budget ≤ 10 MB; chartevents ≤ 3 MB). Id floors
are **disjoint per id space** (`first_subject_id 90_000_000`, `first_hadm_id 91_000_000`,
`first_stay_id 92_000_000`, `first_event_id 93_000_000`, `first_caregiver_id 93_900_000`;
enforced by `fixtures.check`, none an 8-digit real-band token) so a wrong-key join can
never match by accident (D-27 addendum). `GENERATOR_VERSION` is **0.2.0**; regeneration is
byte-identical for the same spec/generator (`test_ep11::test_fixture_drift`) and is a
deliberate, versioned act — never a hand edit (EP-41 extends the vocabulary and
regenerates as 0.3.0). Planted signal (AKI creatinine rise, sepsis culture → antibiotics →
norepinephrine, T2DM code + insulin + glucose) and the MIMIC caveats (ages ≥ 89 → 91,
shifted years, ICD-9/10 by era, `dod` rules) are mirrored; real itemids in `d_items` /
`d_labitems` come from public docs, and fixture-only 2401xx / 2402xx items back the
`datetimeevents` / `ingredientevents` tables no vendored concept reads. Which vendored
concepts are empty or partial on the fixture is documented in `tests/fixtures/COVERAGE.md`
(hand-maintained). Tests read the tree two ways: the in-memory
`fixtures.catalog.build_fixture_catalog()` (app profile, the 31 contract tables via
`read_csv(columns=<contract types>, ignore_errors=false)` in ≈ 0.6 s; EP-12's
`fixture_catalog` session fixture) and, since EP-21, the runner-built
`fixture_lake_catalog` (a fixture lake + `fixture.duckdb` in a temp root, built without a
tag filter so it grows with the DAG spec).

**Demo tier as built (EP-22).** `mwh demo fetch [--force]` (`mimicwarehouse.demo`)
downloads both demos from physionet.org (open access, ODbL 1.0; the only network-touching
command in P2 — not a text module, so `MWH_ALLOW_REMOTE` does not apply) into
`ext\demo\mimic-iv-demo-2.2\{hosp,icu}\` and `ext\demo\mimic-iv-ed-demo-2.2\ed\`:
`SHA256SUMS.txt` + `LICENSE.txt` first, then every listed file sha256-verified (mismatch =
delete + refuse; verified files are skipped on rerun); the result is recorded in
`ext\demo\source.yaml`, the D-36 licensing-register precursor `mwh demo status` prints.
`mwh build --tier demo` resolves the raw root to `ext\demo\mimic-iv-demo-2.2`, strips the
leading dataset dir from each step's `source` (`Step.demo_relative_source`; an explicit
`demo_source` overrides where a name differs — currently nowhere) and applies the
`demo_2_2` column map — the identity for every staged table except `icu.procedureevents`,
whose 2.2 header ships uppercase `ORIGINALAMOUNT`/`ORIGINALRATE` (a lossless rename). The
lossy-map machinery (`map_notes` in manifest lines / `status.json` / `meta.catalog_tables`,
shown by `mwh catalog info`) ships dormant. The **ED demo is fetched and verified but not
staged** — `mimiciv_ed` enters through the Linkage Wizard (EP-142, D-4); there is no note
demo. Attribution: `docs/resources/datasets.md` § Demo tier.

*History:* built by EP-11, EP-12, EP-21, EP-22, EP-166, EP-167, EP-169 (completion notes); consolidated at EP-33.

## 5. Lake physical layout

**Partitioned (subject-keyed) tables** — `loader/buckets.py`, EP-18:
`<lake_root>/core/<schema>/<table>/subject_bucket=<n>/part-0.parquet`, un-padded DuckDB
partition names, the partition column **not** written into the files, rows sorted
`(subject_id, <sort_by>)` where `sort_by` = the contract `sort_keys` tail (the time
column, then a same-table id/sequence tie-break since EP-169, so the per-bucket sort and
the "sha256 stable across two runs" determinism tests are well-defined under ties),
ZSTD level 3, ~1 M-row row groups, statistics on. **Unpartitioned (dim) tables** —
`loader/stage.py`, EP-17: one file `<lake_root>/core/<schema>/<table>/part-0.parquet`, no
`subject_bucket=` level. Readers see only `subject_bucket=*/part-*.parquet`
(`loader.paths.partition_glob`, pinned), so in-progress `raw_*` files are never visible to
a catalog view. Schema names mirror mimic-code: `mimiciv_hosp`, `mimiciv_icu`, `mimiciv_ed`
(from EP-142), `mimiciv_derived`; plus `meta` (catalog / profiles / dictionaries), `marts`
and `runs` (views only — §11).

**Stage coverage is 31 tables.** The P2 stage steps (`dag/specs/stage.yaml`: 31 `stage`
steps + the `meta.profile` `python` step + the `catalog` step) cover exactly the
`mimiciv_hosp` (22) + `mimiciv_icu` (9) contract tables; `mimiciv_ed` (6) has no stage step
until EP-142 and `mimiciv_note` (4) none until EP-148 — note tables go to the segregated
notes lake (§18), **never** `lake/core`. Coverage tests assert the negative too.

**Two load paths, one contract word.** Two-pass = every contract table with
`load_class: large` (**13**; rule of thumb CSV > 1 GB, with `microbiologyevents` kept
large by the owner as a deliberate exception, D-17 addendum): pass 1 streams
`COPY … (PARTITION_BY subject_bucket)` with `preserve_insertion_order=false` and no
`ORDER BY` into `raw_*` files (`sweeps=N` splits it into N sequential bucket-range
statements under `OVERWRITE_OR_IGNORE` — DuckDB's `APPEND` demands a `{uuid}` filename
pattern; the memory fallback EP-26 used), publishes, then pass 2 sorts one bucket at a time
(dev buckets first) from the raws (`hive_partitioning=false`, or DuckDB writes the partition
column into the sorted file) into `_sorting.tmp` → `part-0.parquet`, appending a manifest
line per finished bucket. **Small** tables (CSV ≤ ~1 GB) load in one partitioned `COPY`
with a global `ORDER BY subject_bucket, subject_id, <sort_by>` under
`preserve_insertion_order=true`; dims in one plain `COPY`. The loader reads with the
contract's declared types (`columns=`, no sniffing), accepts `.csv` and `.csv.gz`, applies
column maps (demo 2.2 → 3.1) and accounts rejects per table
(`<lake_root>/rejects/<schema>/<table>/<build_id>.parquet`, `Settings.rejects_root(tier)`;
a reject threshold fails the stage). A `load_class: large` stage reports both pass walls to
the runner (`pass1_wall_s` is `None` when a resume skipped pass 1) — the `phase: pass1` /
`pass2` benchmark lines of §11.

**Publish protocol — once, in `mimicwarehouse.publish` (EP-33 B2).** `os.replace(new →
dest)` fails on Windows whenever `dest` is an existing directory, and renaming a directory
that holds an open file fails the same way, so every table publish is
`publish.swap_dir(new, dest)`: the **rename-aside two-step** — restore a stale `<dest>.old`
beside a *missing* `dest` (crash recovery) / remove a stale `.old` beside a live `dest` /
`os.rename(dest → dest.old)` / `os.rename(new → dest)` and verify `dest` exists (a `.new`
that vanished at the publish rename — quarantine — rolls `.old` back and raises
`SwapError`) / remove `dest.old`. Every rename/remove retries on the transient Windows
`PermissionError` (AV/indexer holds; `retry_permission`, linear back-off, ~10 s at the
defaults); `FileNotFoundError` is tolerated **only** on remove/restore operations; a `.old`
that still cannot be removed after the retry budget is **deferred** to the next swap's
sweep with a warning, never a failed stage. The swap is crash-safe, **not** atomic: a table
under (re)stage is unavailable in every tier and readers must be closed first, which
matters because `dev.duckdb`'s views point at the **same files** a full-tier restage
rewrites. The same module's `rmtree` / `unlink` / `replace` helpers cover every other
rename/remove of freshly written files (pass-2 publish, stale-`.new` sweeps); the EP-171
canary keeps its verbatim raw-OS write sequence as the canon's one sanctioned exception.

**Resume state — `<dest>/_progress.json`** (written into `<dest>.new` during pass 1, so
the swap publishes data and progress together): `{build_id, pass1_done, buckets_requested,
sorted_buckets, dev_ready, complete, started, updated, rejects, source_sha256,
source_fingerprint (<name>:<size>:<mtime_ns>), sort_by}` — the last three since EP-33
(LDR-4); `read_progress` tolerates unknown and missing keys so the full lake's pre-EP-33
files still load. A stage **resumes** only when the recorded `buckets_requested` equal the
new request, `complete` is false and — when recorded — the source identity and the resolved
`sort_by` match; anything else (a different source file, a **full** request over a
`tier_complete: "dev"` table) restages the whole table. The per-bucket order in pass 2 is
**publish before delete**: `os.replace(_sorting.tmp → part-0.parquet)` → append the manifest
line → record the bucket in `_progress.json` → delete the `raw_*` files, so a crash at any
point leaves either the raws (the bucket re-sorts; a re-appended manifest line is harmless —
newest `ts` wins per path) or a recorded bucket whose line is already in the manifest,
never a bucket without a complete source (WIN-1/LDR-3). When the dev buckets are all
sorted the stage logs `dev-ready <schema>.<table>` and writes `status.json`
`dev_ready: true` *before* the final `complete` / `tier_complete` update.

**Coverage guard (EP-33 LDR-1, owner decision).** Before pass 1 replaces `dest`, a request
whose bucket set is a **strict subset** of what `dest` already holds (its recorded
`buckets_requested`, or `tier_complete: "full"` in `status.json`) raises
`StageCoverageError` and leaves the lake untouched — `mwh build --tier dev --force` can
never discard the 95 non-dev partitions of a complete table. Equal or wider requests
proceed; `mwh build --tier full --force` is the only path that rewrites a complete table,
and the runner's `--force` only bypasses the skip, never this guard.

**Manifests and status are per lake root** (`<lake_root(tier)>/manifests/`, so a
fixture/demo build's manifests live beside its Parquet, never in the credentialed
`lake/manifests/`; for dev/full `lake_root` = `lake/`). Every published Parquet file has a
`ManifestLine` in `manifests/<build_id>.jsonl`, appended through `fsio.append_jsonl`:
`{schema, table, path (lake-relative posix), sha256, bytes, rows, schema_hash (sha256 of the
ordered (name, type) list), writer_version (package + DuckDB versions), source_sha256,
raw_snapshot_id, map_notes, build_id, ts}` — the §11 provenance pair, with `sha256`
integrity-only. `manifests/status.json` is `{"steps": {"<schema>.<table>": {tier_complete,
dev_ready, build_id, rows, bytes, files, rejects, finished_at}}}` (`tier_complete` =
`"full"` when all 100 buckets were requested, `"dev"` when the request covers the dev
buckets, otherwise unchanged; the runner completes dims as `"full"` / `dev_ready: true`
directly). `manifests/snapshots.json` is the snapshot-id history (§11). Restaged tables
contribute their latest line per path (newest `ts` across every build's jsonl).

*History:* built by EP-17, EP-18, EP-23 … EP-28, EP-166, EP-169, EP-33 B2/B7 (completion notes); consolidated at EP-33.

## 6. DuckDB configuration & the single-writer rule

**One connection factory.** Every `duckdb.connect` in `src/` goes through
`mimicwarehouse.engine.open_duckdb(profile, *, database=None, read_only=False,
settings=None, memory_limit=None, retry_missing_s=0.0)` (EP-33 B3; pinned by a grep guard
in `tests/ep/test_ep33.py`). The profile is a **required positional** so callers choose
consciously: `build` = `memory_limit` 36 GB, `threads` 12, `temp_directory`
`layout["tmp_duckdb"]`, `max_temp_directory_size` 150 GB, `preserve_insertion_order=false`;
`app` = the same with a 12 GB limit and insertion order kept (`Settings.duckdb_settings
(profile)`, string values for `duckdb.connect(config=…)`). The factory creates the
temp-directory parent on every connect (DuckDB creates a missing leaf but raises on first
spill when the parent is missing) and, with `retry_missing_s > 0`, polls while a file is
transiently absent — the swap window below. `engine.attach_read_only(con, path, alias)` is
the one attach (`ATTACH IF NOT EXISTS … (READ_ONLY)`). DuckDB is pinned to
**`duckdb==1.5.5`** (exact pin; `test_ep01` asserts the installed version, so a bump is a
deliberate one-line change + re-lock + note here); no DuckDB CLI is installed or permitted
(GOVERNANCE §4), so the "one version across every process" rule has a single moving part;
every catalog and benchmark line records the version.

**Single writer.** DuckDB allows one read-write process **or** many read-only processes
per file. `mwh build` is the only writer of the lake and the catalogs and runs under the
build lock `warehouse/.build.lock` (`dag/runner.py`): created with `O_CREAT | O_EXCL` (two
builds racing the same data root cannot both pass an existence check), payload
`{pid, create_time, build_id, started}`; liveness = the recorded pid **with** the recorded
process creation time (a pid Windows has recycled reads as dead; a pre-EP-33 lock without
`create_time` falls back to the pid-only test); a live lock always refuses, a stale one
yields only to `--break-lock`. Hence **one build-profile connection per machine at a
time** (ARCH-11); tests and ad-hoc readers use the app profile. Anything that must be
written while readers are open (audit, run ledger, benchmark ledger) goes to
**append-only JSONL** under `runs\` through `fsio` and is exposed through `runs.duckdb`
views rebuilt on demand (§11).

**Catalog publish.** The catalog builder writes `<tier>.duckdb.new`, `CHECKPOINT`s, closes
and publishes through `publish.swap_file` — the §5 two-step for a single file, differing in
two places: the aside rename `os.rename(<tier>.duckdb → .old)` *succeeds* under DuckDB
READ_ONLY readers (`FILE_SHARE_DELETE`; Windows keeps `.old` delete-pending until they
close) but is **not retried** — a plain, non-sharing handle is not transient — and fails
fast with `SwapBlockedError`, reported as "close the app/notebooks and rerun `mwh build
--tier <t> --select catalog`" with the old catalog intact; and the publish is a bare
`os.replace` with a sub-millisecond no-file window that `catalog.connect.open_catalog`
covers by opening with `retry_missing_s = 0.5`. Open readers keep serving the old snapshot
until they reconnect; because the instance cache is path-keyed (§6.1 b), readers close
before a rebuild and the app (EP-57) will cache **results, not connections**, dropping its
handle when `meta.catalog_info.build_id` changes. The same scheme publishes
`warehouse/runs.duckdb` (`safe.build_runs_db`, `mwh runs refresh`), which the app may hold
ATTACHed. The fallback, if rename-aside misbehaves in practice, is versioned catalog files
+ a `.current` pointer (D-43 alternatives).

**Readers.** `catalog.connect.open_catalog(tier, *, settings=None, role=None, path=None)`
is the one way any reader (tests, the app, `safe_query`, notebooks) attaches a tier
catalog: `read_only=True` on the app profile, then the hardening `SET`s
(`autoinstall_known_extensions = false`, `autoload_known_extensions = false`,
`disabled_filesystems = 'HTTPFileSystem'` — a catalog reader never installs extensions or
touches the network); it refuses a `.new` path (an unpublished build), asserts the
catalog's recorded DuckDB version equals the running one and warns when
`settings.dev_buckets` drifts from the recorded ones. `role` defaults to `Settings.role`
(`MWH_ROLE`) — `agent` in every session, `owner` only in the owner's own shell. **Catalogs
are derived and disposable**: they embed absolute lake paths and assert the version on
open, so after a data-root move or a pin bump the fix is `mwh build --tier <t> --select
catalog` per tier, never surgery (ARCH-16).

*History:* built by EP-1, EP-19, EP-21, EP-30, EP-166, EP-167, EP-33 B2/B3 (completion notes); consolidated at EP-33.

### 6.1 Engine gotchas (EP-33; the full list lives in `docs/gotchas.md` § 1)

The DuckDB 1.5.5 facts that shape code in this repository, each learned once in P2 and
now pinned by tests or by the canon modules: (a) **`10**9` binds as DOUBLE** — spell
large integer literals out; (b) **the in-process instance cache is path-keyed** —
reconnecting to a path any connection still holds returns the old instance, an `ATTACH`
is instance-wide, so every attach is `engine.attach_read_only` (`ATTACH IF NOT EXISTS`)
and readers close before a rebuild; (c) **`COPY … APPEND` forces `{uuid}` names** —
pass-1 sweeps use `OVERWRITE_OR_IGNORE` over disjoint bucket ranges; (d) **pass-2 reads
set `hive_partitioning=false`** or the partition column is written into the file;
(e) **`sum()` returns HUGEINT** — a cast directly around one closed-set aggregate verifies
since EP-33 B1a, `count(*) FILTER` stays the sanctioned event count, arithmetic over
aggregates stays refused; (f) **READ_ONLY handles share delete, plain handles do not**,
and `os.replace` onto an existing directory fails — the two `publish` variants exist for
exactly these two cases; (g) **an in-memory connection spills only into a pre-created
temp-directory parent** — `engine.open_duckdb` creates it on every connect; (h) the
`duckdb` executable is never installed or run — driver files are executed from Python.
Every `duckdb.connect` in `src/` goes through `mimicwarehouse.engine.open_duckdb`
(profile `build` or `app`, a required positional), pinned by a grep guard in
`tests/ep/test_ep33.py`.

*History:* written at EP-33 (item C4) from the EP-18, EP-21, EP-30, EP-31, EP-167 completion notes; consolidated at EP-33.

## 7. Schema, keys, time semantics, unit of analysis

- **Schema contract** (EP-9, package data under `src/mimicwarehouse/schema/tables/`):
  `mimiciv_hosp.yaml` (22 tables), `mimiciv_icu.yaml` (9), `mimiciv_ed.yaml` (6),
  `mimiciv_note.yaml` (4), `keys.yaml`, `units.yaml`, `column_maps/demo_2_2_to_3_1.yaml` —
  transcribed from the vendored mimic-code `create.sql` / `constraint.sql` (EP-8) and kept
  honest by `mwh schema check`, which re-parses the DDL at the pin and reports any table /
  column / order / type / nullability / PK / FK difference as a finding (exit 1). The
  pydantic models (`schema/contract.py`, frozen, `extra="forbid"`) are `Column(name, type,
  nullable, comment, identifier, free_text, unit_of, upstream_type, upstream_nullable)`,
  `Table(schema, name, dataset, csv_path, columns, primary_key, uniqueness_hint,
  subject_keyed, time_column, sort_keys, partitioned, load_class, expected_rows_source)`,
  `ForeignKey`, `ColumnMap`, `UnitsSpec` and `Contract` (`content_hash()` informational,
  `structural_hash()` = the load-relevant facts the fixture manifest pins);
  `load_contract()` is cached. Type map: `INTEGER/SMALLINT/BIGINT` unchanged,
  `VARCHAR(n)/TEXT/CHAR(n)` → `VARCHAR`, `TIMESTAMP(n)` → naive `TIMESTAMP`, `DATE`,
  `DOUBLE PRECISION`/`FLOAT` → `DOUBLE`, `REAL` → `FLOAT`, `NUMERIC(p,s)` → `DECIMAL(p,s)`,
  unbounded `NUMERIC` → `DOUBLE`. Deliberate deviations are recorded on the column
  (`upstream_type` / `upstream_nullable`) and the drift check compares those — three
  exist: `microbiologyevents.spec_type_desc` and `prescriptions.drug` nullable (the data
  holds zero-length strings DuckDB loads as NULL; upstream's own `build_mimic.sh` relaxes
  the same two) and `mimiciv_ed.vitalsign.resprate` as `DOUBLE`. The loader reads CSVs
  with the declared types (no sniffing); the catalog applies `COMMENT`s from the same YAML
  (descriptions live in the YAML, never in code). The demo 2.2 → 3.1 column map is the
  identity for all 37 hosp/icu/ed tables (D-27 addendum), so the demo build only validates
  headers (`ColumnMap.check`).
- **Keys are metadata**: `Table.duckdb_ddl()` never emits `PRIMARY KEY` / `FOREIGN KEY`
  (an ART index over 400 M rows; upstream duplicates in `chartevents`). PKs are exactly
  `constraint.sql`'s (none for `drgcodes`, `emar_detail`, `omr`, `provider`, `caregiver`,
  `chartevents`, `ingredientevents`, nor any ED / Note table — those carry a
  `uniqueness_hint`); FKs are the 51 upstream ones plus 13 documented ED/Note ties marked
  `source: docs`. Natural keys only (`subject_id`, `hadm_id`, `stay_id`, `emar_id`,
  `pharmacy_id`, `poe_id`, `itemid`, …); integrity tests per tier (EP-28 full-tier verify
  shipped; EP-44 extends).
- **Sort keys and flags** (EP-17, EP-169): every subject-keyed table's `sort_keys` is
  `(subject_id, <time column>, <same-table id or sequence tie-break>)`, so per-bucket sorts
  are total under ties. **NULLS LAST (DuckDB `ORDER BY`) is the canonical null placement;
  the Polars fixture writer aligns at EP-41's 0.3.0** (CTR-1, D-17 addendum). Columns carry
  the `identifier` and `free_text` flags stamped from `keys.yaml` (EP-17) — the GOVERNANCE
  §4 sets `safe_query` and the run-folder sweeps read, surfaced by `meta.columns` as
  `is_identifier` / `is_free_text` (EP-29) — and `unit_of` stamped from `units.yaml`.
- **Time semantics** (EP-34): PhysioNet's shifted timestamps are stored as naive
  `TIMESTAMP` exactly as shipped; `anchor_year_group` (2008–2010 … 2020–2022) is the only
  cross-patient temporal axis (temporal holdouts split on it); analyses use within-patient
  relative times (`hours_since_icu_intime`, etc.); `dod` is available ~1 year after the
  last discharge → explicit censoring rule per outcome; ICD-9 → ICD-10 switch (~2015) →
  dual code sets everywhere; ages ≥ 89 appear as 91. The tracer's inlined age/era logic
  (EP-31) moves here.
- **Unit-of-analysis registry** (EP-34): `subject`, `hadm`, `icustay`, `edstay` (P9),
  `icu_day`, `hour_bin`, `person_time`, `note` (P10) — each with its key, time anchor and
  default index-event rule; every cohort spec, mart and model dataset declares its grain.

> **Note (2026-09-05, EP-34).** Both bullets above shipped as `src/mimicwarehouse/timesem.py`
> (stdlib-only; on the `mwh` start-up path because `tracer.py` imports it) with the prose
> twin `docs/methods/time-semantics.md` (its era / censoring / grain tables are rendered
> from the module's constants by `python -m mimicwarehouse.timesem` and drift-tested). As
> built: `ERAS` / `Era` / `era_of` / `sql_era_index`; `age_at` / `sql_age_at` (the only
> calendar reads in the module, pinned by a grep test), `AGE_CAP = 91`, `is_age_capped`
> (true at and above 91 — a genuine 91 is indistinguishable from the sentinel),
> `AGE_BANDS` / `sql_age_band` (lifted from the tracer; `tracer.AGE_BANDS` re-exports);
> `icd_versions_of_hadm` / `sql_icd_versions` (`icd9` / `icd10` / `mixed`, NULL for an
> admission without diagnosis rows); `sql_hours_since` / `sql_days_since` / `hour_bin`
> (`[start, end)`, negative bins before the anchor) / `sql_hour_bin` and the three named
> `RelativeTime` axes; `CensoringRule` + `CENSORING_RULES` (`in_hospital_mortality` with
> `discharge_alive` competing, `mortality_30d/90d/1y` anchored on `index_time` by default,
> censored at `min(anchor + horizon, last_dischtime + 365 d)`; the Python twins count
> whole-second boundaries exactly like `date_diff('second')`); `Grain` / `GRAINS` (eight
> entries, `edstay` / `note` `available=False`), five `INDEX_RULES` templates
> (`first_icu_stay` = the tracer's subject-level first stay, ordered `intime, stay_id`;
> `first_icu_stay_of_first_hadm` = inside the first admission) and `Grain.index_event_sql`
> (the `icu_day` / `hour_bin` grains expand each stay into numbered `[start, end)` bins
> with `bin_start` / `bin_end`; `person_time` has no template — EP-68 builds intervals).
> **Catalog:** `catalog.build.CATALOG_EXTENSIONS` (`(con, tier) -> None`, run after
> `meta.*` and before `CHECKPOINT`; a failing extension fails the build and removes the
> `.new`) with `timesem.create_views` registered first — `mimiciv_derived.hadm_era`,
> `mimiciv_derived.icustay_index` (flags named `first_icu_stay_in_hadm` /
> `first_icu_stay_of_subject` so neither collides with the rule name) and the
> `meta.grains` table, views skipped with a warning when their sources are not cataloged
> (never created empty); `mwh catalog info` lists the non-contract `meta` /
> `mimiciv_derived` / `marts` objects (`objects` in `--json`). The tracer's SQL and
> descriptives cite the module and reproduce EP-31 count for count on fixture and dev
> (`test_ep34` compares against the frozen EP-31 chain through `safe_query`).

*History:* built by EP-8, EP-9, EP-17, EP-22, EP-28, EP-29, EP-169, EP-34 (completion notes); consolidated at EP-33.

## 8. Concepts, code sets & phenotypes

- **mimic-code concepts** (EP-8 vendored; EP-37/38 run): `src/mimicwarehouse/concepts/vendor/mimic-code/`
  holds an allow-listed slice of MIT-LCP/mimic-code (MIT, attributed in the repo-root
  `NOTICE` + the JAMIA 2018 citation) at the pinned commit
  `8bcbd190ca75670cd5281f9ead3611ae1cefb73e` (upstream `main` of 2026-08-10) — 144 files,
  upstream-relative paths, LF: the Postgres `buildmimic` DDL for hosp/icu/ed/note (the
  contract's source), `mimic-iv/concepts_duckdb/**` (66 files incl. the `duckdb.sql`
  driver — what EP-37 executes) and `mimic-iv/concepts/**` (65 BigQuery sources, the
  reference for EP-38). `vendor/VENDOR.json` records sha, commit date, per-file
  `sha256_lf`, `known_upstream_issues`, `excluded` trees and two `local_edits` (a
  `guard_pragma` on `validate.sql`'s row-count lines; an `id_redaction` of two upstream
  debugging comments in `treatment/ventilation.sql` that carried real-band tokens).
  `concepts.vendor_info()` / `vendored_path(rel)` / `vendor_manifest()` expose the pin
  through `importlib.resources`; `poe vendor-mimic-code --sha <sha>` re-vendors from the
  clone's git object store (never a working tree), applies both edit kinds through the
  guard's own regex and fails on any violation.
  **All 65 `concepts_duckdb` files execute cleanly on DuckDB 1.5.5** (EP-33 D2, on a
  throwaway copy of the demo catalog, driver order, 2.2 s) — the `.print` / `.read` lines
  are CLI dialect, so EP-37 executes the files from Python by parsing the `.read` lines and
  writes `mimiciv_derived` per tier under `lake\derived`; count-pinning tests on demo/dev;
  local fixes recorded as patches beside `vendor/` with the upstream issue/PR reference.
  ED and Note concepts do not exist upstream and are ours.
- **Code-set registry** (EP-40): `codesets/*.yaml` (ICD-9/10 dual sets, itemid sets,
  drug-name/RxNorm sets, ATC classes) with semver + definition hash, compiled to
  `meta.codeset_members`; ICD-9→10 GEM utility. The `meta.itemids` view (EP-29; `d_items`
  as `source='icu'` UNION ALL `d_labitems` as `source='hosp'` under one 9-column shape) is
  the base EP-39 curates.
- **Phenotype engine** (EP-41/42): `phenotypes/*.yaml` combining diagnoses, procedures,
  medications, lab thresholds, microbiology, device/ventilation events and temporal logic
  → SQL; versioned like code sets; first three: T2DM, sepsis-3 (via concept), KDIGO AKI
  stage. The fixture plants all three traits (§4) and EP-41 regenerates it as 0.3.0.

*History:* built by EP-8, EP-167, EP-29, EP-33 item D2 (completion notes); EP-37 … EP-42 planned; consolidated at EP-33.

## 9. Cohort spec → SQL

Pydantic model / YAML (EP-46): `grain`, `inclusion`, `exclusion`, `index_event`,
`observation_window`, `washout`, `follow_up`, `era_filter`, references to code sets and
phenotypes by version. Compiler (EP-47) emits a deterministic CTE chain, one step per
criterion (the tracer's `base → first_stay → adult → complete → cohort` chain in
`sql/tracer_first_icu_mortality.sql` is the shipped precedent: identifiers only inside the
chain, never in a final select list), materialises the cohort table under
`marts/cohorts/<cohort_id>@<version>/`, registers it in `marts.cohorts` (already a
`safe.REGISTRY_TABLES` member, so the registry reads need no count column), records
per-step attrition counts through `safe_query` (a suppressed step comes back `None`, as in
the tracer) and writes a run record. Level-degeneracy policy (rare factor levels with zero
outcomes — real on every tier, EP-31) belongs here and in the model engines (EP-79), not in
each analysis. Attrition diagram (EP-48) renders from the attrition table (Mermaid
primary, Altair fallback), disclosure-aware.

*History:* planning text (2026-08-16), reconciled with EP-31's tracer and EP-33 B1c's registry seed; EP-46 … EP-48 planned; consolidated at EP-33.

## 10. Events spine (MEDS-compatible)

EP-50 materialises a long table `(subject_id, hadm_id, stay_id, time, code, numeric_value,
text_value, source_table)` under `lake/derived/spine/` covering admissions, transfers,
diagnoses, procedures, labs, microbiology, prescriptions/emar, ICU inputs/outputs/
procedures — **excluding raw `chartevents`** in v1 (size; an optional vitals-only subset
is the §21 question, sized at ≈ 2.5–4 GB either way in §3). The column set matches MEDS
0.4 so ACES/MEDS tooling can be used as an optional validation lane; the spine is our own
build from the catalog, not the external ETL. It runs as DAG `python` steps under the
EP-29 handler contract (`callable: module:function`, called with `(step, ctx)`), so it gets
the build lock, the benchmark lines and the `derived` layer snapshot id for free.

*History:* planning text (2026-08-16), reconciled with EP-29's `python` step handler and EP-33 item D3's sizing; EP-50 planned; consolidated at EP-33.

## 11. Run ledger, benchmark ledger, audit, snapshot ids

**The JSONL canon (`mimicwarehouse.fsio`, EP-33 B8).** Every ledger is written through
`fsio.append_jsonl` / `append_jsonl_lines`: one canonical JSON object per line
(`sort_keys=True`, UTF-8, exactly `\n` — `O_BINARY`, so no CRLF), one `os.write` per line
on an `O_APPEND` descriptor with the byte count **checked** (`ShortWriteError` on a full
disk instead of a truncated line the next append would merge into), then `fsync`. Readers
(`fsio.iter_jsonl` / `read_jsonl`) tolerate exactly **one** malformed *trailing* line (a
crash mid-append; warn and skip) and raise `TornLedgerError` on any other malformed line.
`fsio.atomic_write_text` (temp sibling + `os.replace` with the `PermissionError` retry) is
the one rewrite primitive for `status.json`, `snapshots.json`, `_progress.json`, the raw
manifests and job state files.

**Ledgers as built.**

- **`runs/audit.jsonl`** (EP-30; §12): one `AuditLine` per `safe_query` call — allowed,
  refused or usage — `{audit_id, ts, actor, tier, statement_sha256, sql_text, allowed,
  refusal_reason, n_rows, rows_suppressed, k, wall_ms, duckdb_version, snapshot_ids,
  git_sha}`; never values. Later: every row-view toggle and export attempt (EP-58/59).
- **`runs/benchmarks.jsonl`** (EP-19; `dag/benchmarks.py`): `BenchmarkLine`
  `{ts, build_id, tier, step, kind, phase, wall_s, peak_rss_mb, rows, bytes_in, bytes_out,
  files, duckdb_version, git_sha, host {cpu, ram_gb}, ok, error}`. Two sanctioned writers:
  the runner appends one line per executed step (`phase: total`; a large partitioned stage
  adds `pass1` — with `bytes_in`, absent when a resume skipped pass 1 — and `pass2` — with
  `rows` — before it, EP-23) plus one `kind: build` summary per run, and EP-28's full-tier
  verify test appends `kind: verify` lines (the per-table sizes and ratios of §3). Appends
  are **not** atomic between processes on Windows (the CRT implements `O_APPEND` as
  seek-then-write, DAG-4): the ledger relies on **single-writer-by-sequencing** — builds are
  serialized by the build lock and a verify run follows the build it verifies — not on OS
  locking. `read()` goes through `fsio` (one torn line tolerated); `summarize(tier, kind)`
  pivots per `(tier, step)` on the **latest** `phase: total` line, ordered by `(ts,
  build_id)` with a stable sort so two lines sharing a one-second `ts` resolve
  deterministically, joined with the same build's pass walls; `render_markdown` /
  `replace_marked_block` (EP-32) drive `mwh runs benchmarks [--tier] [--kind]
  [--format table|md] [--out PATH]`, which splices between `<!-- benchmarks:begin/end -->`
  markers (first consumer: `docs/analyses/00-staging-benchmark.md`).
- **`runs/tracer/<yyyymmddThhmmss>-<tier>/`** (EP-31): `manifest.json` (git sha, versions,
  `core_snapshot_id`, params, cohort n, wall, audit ids) + `attrition.json`,
  `descriptives.json`, `model.json`, `report.md` — the run-folder shape EP-35 generalises.
- **`runs/ledger.jsonl` + `runs/<run_id>/`** (EP-35, planned): `mimicwarehouse.run`, a
  context manager that assigns `run_id`, captures git sha, params, generated SQL, code-set
  / phenotype versions, cohort attrition, snapshot ids, seeds (EP-36), environment lock
  hash, `doctor.run_checks(settings)`, warnings, wall time, peak RSS, disk delta, and writes
  `manifest.json` + `sql/`, `tables/`, `figures/`. The `usage: ` prefix on audit
  `refusal_reason`s exists so EP-35's ledger views can filter usage errors out.
- **`runs/jobs/<name>.json` + `.log`** (EP-19): a detached ⏱ job's `{job, pid, argv,
  started, log, state, exit_code, finished}`, owned by the supervisor (`dag/jobs.py`,
  `DETACHED_PROCESS`, workspace-venv python, never a `uv` shim), which finalises the state
  on exit so `state`/`exit_code` are reliable for any argv including crashes and lock
  refusals; `mwh jobs [--job NAME] [--tail N]` is the only window a session has on a log
  (INFO counts only — never rows). The supervisor's own `[job] …` lines are plain `print`s
  into the log — with the canary observer, the sanctioned exception to the logging seam.
- **`warehouse/runs.duckdb`** (EP-30; EP-35 adds the ledger views): views over the JSONL,
  rebuilt by `mwh runs refresh` (`safe.build_runs_db`, published via `publish.swap_file`);
  the `audit` view reads `read_json_auto(…, format = 'newline_delimited', ignore_errors =
  true)` — DuckDB 1.5.5 turns a torn line into an all-NULL record rather than skipping it,
  so the view filters `audit_id IS NOT NULL` whenever the ledger holds a parseable record.
  `safe_query` attaches it read-only as `runs` when present; `runs` is deliberately **not**
  a registry schema (§12).

> **Note (2026-09-05, EP-35).** The run ledger shipped as `src/mimicwarehouse/run.py`
> (prose: `docs/methods/provenance.md`). As built, against the planned bullet above: the
> manifest (`run.RunManifest`, flat, `extra="forbid"`) carries the brief's field list plus
> `command` (the line to repeat), `tables` / `figures` (what `save_table` / `save_figure`
> produced, `{name: relative path}` like `sql`), `audit_ids` (the audit lines of the
> `safe_query` calls made on the run's behalf) and `doctor` — `doctor.run_checks` reduced
> to `{id, status, value}` per check (the `value` payloads `CheckResult` was designed to
> carry, never the prose `detail`), captured **once per process per data root** because the
> PowerShell probes cost ~6 s; `seeds` / `resources` stay `None` until EP-36. The
> `runs/ledger.jsonl` line is the nine-field subset `run_id, name, kind, tier, status,
> started, wall_s, git_sha, protocol_hash` through `fsio.append_jsonl` (the canon's
> `ensure_ascii` default stands over the brief's `ensure_ascii=False`). Third-party text
> (exception messages, captured `warnings.warn`) enters a manifest only through
> `safe.sanitize_error_text`; `save_table` refuses identifier columns by name. The
> `runs.duckdb` views `ledger` / `benchmarks` / `manifests` / `attrition` bind **explicit
> column types** (dict fields as `JSON`) rather than `read_json_auto` — older lines and
> manifests without a newer field read as NULL, an empty file is a typed empty view, a
> torn line is the all-NULL row the `IS NOT NULL` filter drops; the audit view keeps its
> EP-30 form. A refresh must be visible to a process that already holds the catalog
> instance, so `engine.detach(con, alias)` (`DETACH DATABASE IF EXISTS`) is the one detach
> and `safe_query` calls it before the attach (§6.1 b). `dag.benchmarks.BenchmarkLine` gained optional
> `run_id` / `disk_delta_mb` and the `BENCHMARK_KINDS` vocabulary; `run.bench` builds a
> line and calls `dag.benchmarks.append`. The tracer runs inside `run.start` and cites the
> run under `ledger_run_id` in its own manifest; its `runs/tracer/<stamp>-<tier>/` folder
> and numbers are unchanged. `run.reproduction_block(run_id)` renders the EP-32
> Reproduction + Provenance block.

> **Note (2026-09-05, EP-36).** The `seeds` / `resources` slots shipped, in
> `src/mimicwarehouse/run.py` (prose: `docs/methods/determinism.md`). **Seeds:**
> `derive_seed(protocol_id, stage, salt=0)` = the big-endian first four bytes of
> `sha256("{protocol_id}|{stage}|{salt}")` — 32-bit, stable across processes; `rng(...)`
> → `numpy.random.default_rng(seed)`; `spawn_rngs(..., n)` → `SeedSequence(seed).spawn(n)`
> children for workers; `seed_everything(seed)` → the global `random` / numpy-legacy /
> already-imported `torch` state (never imports torch); `sql_sample_clause(seed, rows=|
> percent=, method=)` → `USING SAMPLE <method>(<size>) REPEATABLE (<seed>)` with the
> 32-bit seed folded to `seed % 2**31` (DuckDB 1.5.5 parses the seed as an int32 literal;
> `DUCKDB_SEED_MAX`). `Run.seed
> (stage)` / `Run.spawn_rngs(stage, n)` derive from the run's `protocol_id` (frozen, EP-51)
> or its `run_id` (unfrozen), record `{stage: seed}` under `manifest.seeds` and rewrite the
> manifest at once (a killed run keeps its seed record); an open run starts with
> `seeds: {}` (`null` = pre-EP-36). **Resources:** `ResourceLog` — a daemon thread
> (0.5 s, psutil) started after the environment block and stopped before the manifest is
> finalised; `ResourceLog.measure(fn)` standalone, `Run.measure(kind, name, fn)` →
> `run.bench` — writes a `ResourceUsage` block under `manifest.resources`: `wall_s`,
> `cpu_time_s`, `peak_rss_mb` + `peak_rss_method`, `rss_start_mb` / `rss_end_mb`,
> `peak_wset_mb`, `disk_delta_mb` (data-root drive free-bytes delta), `gpu_mem_start_mb` /
> `gpu_mem_peak_mb` / `gpu_mem_method` (pynvml only when it imports **and** a device
> answers; per-process when the driver attributes memory, else device-level; `None`
> otherwise, silently), `samples`, `sample_errors`, `interval_s` — and mirrors `wall_s` /
> `peak_rss_mb` / `disk_delta_mb` at the top level, replacing EP-35's start/end RSS
> reading. Measured fact behind `peak_rss_method`: Windows' `peak_wset` is a
> *process-lifetime* high-water mark (memory freed before the run keeps it high), so the
> run-scoped peak is the sampled maximum, promoted to `peak_wset` only when the mark grew
> during the run (`docs/gotchas.md` § 2). The EP-19 runner's per-step `_RssSampler`
> predates `ResourceLog` and is untouched — EP-54 decides whether the runner adopts it.
> In the `manifests` view both slots bind as `JSON` (`json_extract(resources,
> '$.peak_rss_mb')`).

**Identifier glossary** (D-43 item 11) — briefs and modules use these names and no others:

- **`raw_snapshot_id`** (EP-10) — sha256 over the sorted `(rel_path, bytes, sha256, rows)`
  tuples of **all 41** raw CSVs (`lake/manifests/raw/<dataset-dir>.jsonl` +
  `raw_snapshot.json`); `None` until all 41 are inventoried. `inventory.raw_snapshot_id()`
  / `RawManifest.for_table(table)` are the readers.
- **`source_sha256`** (EP-17) — the per-file sha256 the raw manifest recorded for one
  source CSV (`None` on the fixture tier). Every lake `ManifestLine` carries **both**
  `source_sha256` and `raw_snapshot_id` (the pair supersedes the EP-17 brief's single
  `source_manifest_id`; D-26 addendum).
- **`build_id`** (EP-19) — one DAG-runner invocation: `<UTC yyyymmddThhmmss>-<tier>-<git
  short sha>`.
- **layer `snapshot_id`** (EP-19, `dag/snapshot.py`), one per `{core, derived, marts,
  notes}` × tier — a **logical** id: sha256 over the sorted canonical JSON of `(schema,
  table, path, rows, schema_hash, source_sha256 or raw_snapshot_id, sort_keys,
  writer_version)` per published file, so it is *stable when raw + contract + code are
  unchanged* and two identical rebuilds agree. The per-file Parquet `sha256` is
  **integrity-only**, never part of it (file bytes need not be reproducible under non-total
  sort orders). Scope: only tables `complete_for_tier` (`tier_complete == "full"`;
  `"dev"`/`dev_ready` suffice for dev — the same predicate the runner's skip logic and the
  catalog's admission use); the **dev** id hashes only manifest lines whose path lies in
  `settings.dev_buckets` plus unpartitioned tables, so it does **not** move when buckets
  5–99 finish during a full ⏱ pass; a restaged table contributes its latest line per
  path. Every build appends `{layer, tier, snapshot_id, build_id, ts}` to
  `lake/manifests/snapshots.json`; `table_file_stats` sums the same lines into the
  no-scan row counts of `meta.tables` / `meta.row_counts`.
- **catalog `build_id` + `core_snapshot_id`** (EP-21, `meta.catalog_info`) — what the
  catalog was built from; the audit `snapshot_ids` is `{"core": core_snapshot_id}` of the
  queried catalog.
- **`run_id` / `audit_id`** (EP-35 / EP-30) and the **protocol hash** (EP-51). Every run
  and audit line cites `snapshot_ids` — a `{layer: id}` dict.

*History:* built by EP-10, EP-17, EP-19, EP-23, EP-28, EP-29, EP-30, EP-31, EP-32, EP-166, EP-33 B8 (completion notes); EP-35/36 planned; consolidated at EP-33.

## 12. Safe-query (D-31, D-32)

`mimicwarehouse.safe` (EP-30; hardened EP-33 B1) is the choke point for anything an agent
or an export can see: from this module on, every result a Claude session sees comes
through `safe_query(sql, *, tier=None, k=None, row_cap=200, timeout_s=120, actor=None,
settings=None) -> SafeResult` or the `mwh sql` CLI built on it. Owner-only row viewing in
the app goes through a separate, audited `owner_rows()` path (EP-58) that is never
reachable from the CLI; this module behaves the same for every role. Enforcement is
layered: this code + `CLAUDE.md` + the repo `.claude/settings.json` deny rules and PreToolUse
hook (D-39, EP-165).

**The rule set as built**, in pipeline order:

1. **Parse** via DuckDB `json_serialize_sql` (the statement is a bound parameter, never
   executed; DESCRIBE/SHOW serialize as `SELECT_NODE` + `SHOW_REF`, so no regex pre-pass):
   exactly one statement — a SELECT (CTEs allowed) or `DESCRIBE <schema.table>` /
   `SHOW TABLES` / `SHOW ALL TABLES`; set operations **UNION / UNION ALL / EXCEPT /
   INTERSECT** of SELECTs are allowed since EP-33 B1b, with the full select-list checks per
   leaf, matching widths and count-family **positions** across branches (the output takes
   branch 1's names and the combined frame is suppressed as one); `UNION BY NAME` and
   everything else (COPY, ATTACH, INSTALL, LOAD, PRAGMA, SET, DDL/DML, EXPORT/IMPORT, CALL,
   BEGIN, multi-statement) is refused. AST node shapes are pinned by tests, never assumed.
2. **Allow-list**: no file/env/SQL-indirection functions (`FORBIDDEN_FUNCTION_NAMES`:
   `glob`, `getenv`, `current_setting`, `duckdb_settings`, `query`, `query_table`,
   `checkpoint`, `force_checkpoint`; prefixes `read_`, `parquet_`, `scan_`, `sniff_`);
   every qualified table in `ALLOWED_SCHEMAS` = `mimiciv_hosp, mimiciv_icu, mimiciv_derived,
   meta, marts, runs, information_schema` (notes schemas never exist in these catalogs);
   `duckdb_tables()` / `duckdb_columns()` admitted; unqualified names must be CTEs.
3. **Aggregate-only** on every leaf's select list: each column is an aggregate from the
   closed `AGGREGATE_FUNCTIONS` set (no value-collecting members — `string_agg`, `list`,
   `histogram`, `first`, `arg_min`, …), optionally one `CAST` / `TRY_CAST` directly around it
   (B1a; a cast around arithmetic or a bare column does not verify — arithmetic over
   aggregates stays refused, DIS-3), a GROUP BY key (structural, positional, by alias, or
   `GROUP BY ALL`) or a constant; identifier columns (the contract `identifier` flag) appear
   **only** inside count-family calls (`count`, `count_star`, `approx_count_distinct`),
   never as output, inside arithmetic or in MIN/MAX/string aggregates. A **real**
   count-family node is mandatory — an alias alone never satisfies it, a count-shaped alias
   (`COUNT_ALIAS_RE`: `n`, `n_*`, `*_count`, `cnt`, …) on a non-count expression is refused,
   and a cast-wrapped count must be aliased — unless the read is **registry-only**
   (`is_registry_ref`: `REGISTRY_SCHEMAS` = `meta`, `information_schema`; `REGISTRY_TABLES`
   = `marts.cohorts`; contract dims) or metadata functions. `runs` deliberately does not
   join the registry (owner, 2026-09-01): `runs.audit` carries statement text.
4. **Execute** on `open_catalog(tier)` (READ_ONLY, app profile, hardened) with
   `warehouse/runs.duckdb` attached as `runs` when present (`engine.attach_read_only`),
   under a `threading.Timer → con.interrupt()` at `timeout_s`. Any DuckDB error before or
   during execution — the snapshot read and the attach included — is refused with a
   **sanitized** message (`sanitize_error_text`: first line, quoted literals → `'...'`,
   standalone digit runs masked, 120 chars) before it reaches the refusal and the audit line
   (DuckDB quotes the offending cell value in conversion errors, DKB-2).
5. **Result checks**: output columns named like identifiers or like contract `free_text`
   columns are refused by name (ARCH-10/FC-18); the free-text value heuristic (any VARCHAR
   value over `FREE_TEXT_MAX_CHARS` = 64 characters or containing a newline) applies to every
   statement that is not registry-exempt — `mimiciv_derived` and non-registry `marts` reads
   are scanned too (B1c) — with `LABEL_COLUMN_NAMES` (`description`, `short_description`)
   allow-listed; post-suppression rows ≤ `row_cap`.
6. **k-suppression** through the module-level `SUPPRESSOR` hook
   (`(df, k, count_columns) -> (df, rows_suppressed)`; default `rowwise_suppress` drops every
   row with any real count column in 1…k−1; EP-43 assigns `disclose.suppress`), over the
   real count columns only. `tier` / `k` default to `settings.default_tier` /
   `settings.k_suppression`; `k < K_FLOOR` (11) is refused on the credentialed tiers
   (`dev`, `full`) and lowerable on `fixture`/`demo`. Extreme-value aggregates (`min` /
   `max` / `mode` / `median` / quantiles) stay admitted and are released only inside
   k-gated rows (owner, EP-33).

**Error taxonomy (B1d).** `SafeQueryRefused` = a governance verdict, exit
`EXIT_REFUSED` (3), raised **after** auditing; `SafeQueryError` = a usage error (`k < 1`,
`row_cap < 1`, unknown tier), exit `EXIT_USAGE` (2), audited with a `usage: ` reason;
`CatalogOpenError` (no catalog, `.new` path, version mismatch) = an environment error, exit
2, unaudited. **Every call** — allowed, refused or usage — appends one `AuditLine` (§11)
through `fsio.append_jsonl`. `safe.build_runs_db()` publishes `warehouse/runs.duckdb` via
`publish.swap_file` (§11). **`mwh sql "<stmt>" [--tier t] [--k n] [--row-cap n]
[--format table|csv|json] [--tables] [--describe SCHEMA.TABLE] [--count SCHEMA.TABLE]`**
routes free-form statements and the three helper forms through `safe_query`, prints the
`k=… · audit … · tier … · snapshot …` footer, thousands-separates table/CSV integers via
`inventory.fmt_int` (JSON keeps raw ints), sends errors to stderr via `console.fail` and
exits 3 on refusal (`--describe` prints the contract comment column). The tracer's
`count(*) FILTER (WHERE flag = 1)` remains the sanctioned event-count pattern (§6.1 e).

*History:* built by EP-21, EP-30, EP-31, EP-170, EP-33 B1 (completion notes); consolidated at EP-33.

## 13. Protocol freeze (D-25)

`mimicwarehouse.protocol` (EP-51): pydantic `Protocol` (cohort ref, exposure, outcome,
covariates, feature windows, analysis plan, temporal holdout, claim type). `mwh protocol
freeze <yaml>` computes the content hash, appends `{hash, timestamp, git sha, path}` to
`runs/protocols.jsonl` (through `fsio.append_jsonl`, like every ledger), and tags the
file; `mwh protocol run <hash>` refuses to run an unfrozen or modified protocol; amendments
append a new hash linked to the previous one. The Freezer page (EP-128) and
temporal-holdout runner (EP-129) sit on top. Every report built from a frozen protocol
states that MIMIC-IV analyses remain retrospective.

*History:* planning text (2026-08-16), reconciled with the EP-33 B8 ledger canon; EP-51 planned; consolidated at EP-33.

## 14. Disclosure primitives (D-33, D-40)

`mimicwarehouse.disclose` (EP-43): `suppress(df, k=11)` with complementary suppression —
installed into `safe.SUPPRESSOR` (§12) so every `safe_query` result and the tracer's
"n vs n_fit" differences (EP-31) get it without caller changes — `check(path)` scanning
tables/figures/HTML for identifier columns, note text, small cells and embedded data
arrays, and a `.disclosure.json` sidecar writer. In-app: warn badge at n < 11 (EP-58). On
export/commit: suppress and require a passing sidecar (EP-59, EP-133). Until EP-43,
nothing derived from real data enters `docs/` or git except manifests of hashes / counts /
schema (GOVERNANCE §3); the two P2 exceptions that carry telemetry only —
`DATA-DICTIONARY.md` (EP-29; distinct counts below k render `<11`) and
`docs/analyses/00-staging-benchmark.md` (EP-32) — get retroactive sidecars at EP-43.

*History:* planning text (2026-08-16), reconciled with EP-29/31/32's pending-sidecar consumers and EP-30's hook; EP-43 planned; consolidated at EP-33.

## 15. Package / module map (planned 2026-08-16; "shipped" marks what exists — details in the workspace README § State of the workspace)

The shipped-vs-planned map as of EP-33. Shipped rows name the EPs that built and last
changed them; planned rows keep their EPs. The workspace README § State of the workspace
carries the per-module CLI/test columns.

```
mimicwarehouse/                    uv project root (nested, hupsim-style)
├── pyproject.toml                 EP-1 shipped   groups: core dev ui gpu gpl text; [tool.poe.tasks]; ../poe_tasks.toml (EP-33) runs the same tasks from the repo root
├── src/mimicwarehouse/
│   ├── cli.py                     EP-2, EP-167, EP-33 shipped   `mwh` (typer): doctor paths guard verify schema inventory fixtures canary build jobs catalog sql demo runs tracer; lazy settings validation; DIAGNOSTIC_COMMANDS; planned: protocol disclose backup app init
│   ├── console.py                 EP-167, EP-33 shipped  shared consoles, UTF-8 `mwh` entry point, EXIT_* codes, fail, emit_json, configure_progress_logging
│   ├── config.py                  EP-3, EP-167 shipped   Settings (pydantic-settings; MWH_ env · .env · mwh.toml); 18-key layout; per-tier lake roots; D-29 refusals; duckdb_settings(profile); role
│   ├── doctor.py                  EP-2, EP-164, EP-167 shipped   15 host checks; run_checks(settings) is what EP-35 embeds
│   ├── guard.py                   EP-4, EP-165, EP-33 shipped   pre-commit data-leak guard G1–G5 (index blobs, path tokens, notebook/script scans)
│   ├── theme.py                   EP-5 shipped   palette, Altair/Streamlit themes, brand SVGs
│   ├── verify.py                  EP-6, EP-167 shipped   `mwh verify EP-n | --list | --roadmap`; roadmap_check
│   ├── schema/                    EP-9, EP-169 shipped   contract.py, transcribe.py, csv_dialect.py, cli.py; tables/*.yaml package data
│   ├── inventory.py               EP-10, EP-167 shipped  raw manifest + raw_snapshot_id; fmt_int / fmt_bytes_mb
│   ├── fixtures/                  EP-11/12, EP-169 shipped   spec, vocab, hosp, icu, check, write, catalog, cli
│   ├── canary.py                  EP-171 shipped write canary (synthetic write-shape rehearsal; raw-OS sequence kept on purpose)
│   ├── fsio.py                    EP-33 shipped  the JSONL ledger canon + atomic_write_text
│   ├── publish.py                 EP-17/21 → EP-33 shipped   the one rename-aside publish primitive (swap_dir, swap_file, retry helpers); supersedes paths.py and catalog.build.swap_catalog
│   ├── engine.py                  EP-33 shipped  the one DuckDB connection factory (open_duckdb, attach_read_only)
│   ├── loader/                    EP-17, EP-18, EP-23 … EP-27, EP-33 shipped   engine (build connection guards), csv, stage, buckets, manifest, paths
│   ├── dag/                       EP-19, EP-28, EP-29, EP-32, EP-33 shipped   spec (+ specs/stage.yaml), runner, snapshot, benchmarks, jobs, cli
│   ├── catalog/                   EP-21, EP-29, EP-30, EP-33 shipped   build, connect, profile, dictionary, cli (`mwh catalog`, `mwh sql`)
│   ├── demo.py                    EP-22 shipped  ODbL demo fetch + source.yaml register
│   ├── safe.py                    EP-30, EP-33 shipped   safe_query, AuditLine, build_runs_db
│   ├── runs_cli.py                EP-30, EP-32, EP-35 shipped   `mwh runs refresh | list | show | benchmarks`
│   ├── tracer.py                  EP-31 shipped  tracer bullet (+ sql/tracer_first_icu_mortality.sql); `mwh tracer`
│   ├── concepts/                  EP-8, EP-167 shipped (vendor/ + pin, vendoring.py); EP-37/38 runner + patches/
│   ├── timesem.py                 EP-34 shipped  eras, ages, ICD rule, relative time, dod censoring rules, grain registry + index-rule SQL, catalog views (CATALOG_EXTENSIONS entry), docs/methods/time-semantics.md renderer
│   ├── run.py                     EP-35, EP-36 shipped  provenance run ledger: `start` context manager, `RunManifest`, `runs/ledger.jsonl`, `runs.duckdb` ledger views, `bench`, `reproduction_block` (docs/methods/provenance.md); seeds (`derive_seed` / `rng` / `spawn_rngs` / `seed_everything` / `sql_sample_clause`, `Run.seed`) + `ResourceLog` (docs/methods/determinism.md)
│   ├── units.py                   EP-39  item dictionary curation, unit harmonization
│   ├── codesets/                  EP-40  registry, GEM utility
│   ├── phenotypes/                EP-41/42
│   ├── disclose.py                EP-43
│   ├── qc/                        EP-44/45 profiles, measurement process
│   ├── cohort/                    EP-46/47/48 spec, compiler, attrition
│   ├── timeline.py                EP-49
│   ├── spine.py                   EP-50
│   ├── protocol/                  EP-51 (+ EP-128/129)
│   ├── backup.py                  EP-52
│   ├── marts/                     EP-55/56
│   ├── viz/                       EP-64+  Altair specs, Plotly timeline, export
│   ├── stats/                     P5     endpoints, boot, glm, mixed, trajectories, pathways, utilization, tsa, exposure, missing
│   ├── survival/  causal/         P6
│   ├── ml/                        P7     datasets, splits, assess, registry, baselines, trees, flexible, unsupervised, dimred, bayes, audits, interpret, gpu, fm, bench
│   ├── report/                    P8     Jinja templates → MD/HTML, Typst PDF, cards
│   ├── linkage/                   P9     profiler, mapping, validation, wizard backend
│   └── text/                      P10    notes lake, search, extraction, embeddings
├── scripts/                       roadmap_check.py (EP-6) · claude_pretool_guard.py (EP-165, the PreToolUse session guard)
├── app/                           P4+    Streamlit multipage "Lab" app (pages/…)
├── notebooks/                     marimo scratch (zero-output .py; import the package)
├── tests/                         pytest; conftest.py + helpers.py (EP-12/168/33); tests/ep/test_epNN.py per brief; fixtures/ (+ COVERAGE.md)
├── docs/                          resources/ (P1) · analyses/ (capstones, EP-32) · gotchas.md + committed-text.md (EP-33) · brand/ (EP-5) · site (P11)
├── DESIGN.md · GOVERNANCE.md · DECISIONS.md · DATA-DICTIONARY.md (generated, EP-29)
```

**CLI shape (EP-2, EP-167).** Commands live in their own modules and attach to `cli.py`
with one `app.command()` / `app.add_typer()` line each (the `# --- commands` block is
authoritative). The callback loads `load_settings(checked=False)` once per invocation
(`--data-root` > `MWH_*` env > `.env` > `mwh.toml` > defaults; both files and relative paths
anchored at the workspace root), installs it process-wide (`config.configure`, so
`get_settings()` agrees with `ctx.obj`) and stores any configuration error as
`CliState.pending_error`; the first `CliState.settings` access by a non-diagnostic command
runs the D-29 refusals and exits 2 — so `--help`, `--version` and `no_args_is_help` always
work over a broken or unsafe configuration. **`DIAGNOSTIC_COMMANDS`** = `{doctor, paths,
guard, verify, schema, fixtures}` — the rule is stated once, on that constant: *a command is
diagnostic iff it never touches the data root*; members receive the unchecked settings so
they can report a bad root; membership is pinned by `test_ep167`. `inventory`, `canary`,
`build`, `catalog`, `sql`, `demo`, `runs`, `tracer` all touch the data root and validate.
Background work (`--background --job NAME` on `build` and `tracer`) detaches through
`dag.jobs.launch`; `verify` passes `MWH_DATA_ROOT=<resolved --data-root>` to its pytest
child without mutating `os.environ`, and spawned jobs pass the same env.

**CLI conventions (EP-33 B8; `mimicwarehouse.console`).** Exit codes `EXIT_OK` 0 /
`EXIT_FINDINGS` 1 / `EXIT_USAGE` 2 / `EXIT_REFUSED` 3, defined once in `console` and
re-exported where tests import them (`safe`, `catalog.cli`, `verify`); errors as
`mwh <cmd>: <message>` in bold red on **stderr** through `console.fail` (stdout stays
machine output; typer's `CliRunner.output` still holds both streams, `result.stdout` /
`result.stderr` split them); `--json` through `console.emit_json` (`json.dumps(indent=2,
default=str)` + plain `\n`, **raw integers** — `fmt_int` is for humans only); progress =
stdlib logging on the `mimicwarehouse` logger through `console.configure_progress_logging
(to_file=…, quiet=…)`, which is how a background job's log captures the runner's and the
loader's INFO lines (steps, counts, bytes, wall, rss — never rows).

**Import-budget doctrine (EP-33 B6).** `mwh --help` must stay under ~0.5 s, so the
**start-up set** = `cli.py`'s eager imports — stdlib, typer, rich, pydantic(-settings),
yaml — and nothing heavier: duckdb, pandas, polars, pyarrow, numpy (`helpers.HEAVY_MODULES`)
and expensive project singletons (the schema contract, the vendor pin) load only inside
function bodies or under `TYPE_CHECKING`. Package `__init__`s take one of three forms:
docstring-only with no submodule imports (`catalog/`, `dag/`, `concepts/` re-export
nothing eagerly), lazy re-exports via module `__getattr__` + `__dir__` (`schema/`,
`fixtures/`, `loader/`), or a self-contained leaf. Every module on the start-up path
carries an `Import budget:` line in its docstring saying what it defers (`canary`,
`inventory`, `demo`, `theme`, `tracer`, `catalog/cli`, `runs_cli`, `dag/cli` today).
Enforcement is `tests/helpers.assert_import_budget(module, *, forbid=HEAVY_MODULES,
lazy=…)` — import in a fresh interpreter (a `python -c` argv element, never a heredoc or
stdin script, D-42) and fail naming the offenders; one budget line per future package.
`tracer.VALUE_MAX_CHARS` mirrors `safe.FREE_TEXT_MAX_CHARS` instead of importing it for the
same reason (`tracer.py` is on the start-up path, `safe.py` is not; the two are asserted
equal).

**One way per thing (EP-33 B8; the lore behind each rule lives in `docs/gotchas.md`).**
JSONL is appended only via `fsio.append_jsonl` and read via `fsio.iter_jsonl` (§11);
files are rewritten only via `fsio.atomic_write_text`; every rename-aside publish is
`publish.swap_dir` / `publish.swap_file` and every retrying rename/remove is a `publish`
helper (§5) — the EP-171 canary's verbatim raw-OS sequence is the one sanctioned exception;
every `duckdb.connect` is `engine.open_duckdb` and every attach `engine.attach_read_only`
(§6); every catalog read is `catalog.connect.open_catalog`; progress is stdlib logging
through the console seam (exceptions: the canary's `observer` prints and `dag/jobs`'
supervisor `[job]` lines); CLI errors, exit codes and JSON go through `console` (above);
the diagnostic rule is `DIAGNOSTIC_COMMANDS`; integers in human-facing text go through
`inventory.fmt_int`; the free-space guard is `config.require_free_space` per tier.

**Committed-text canon (EP-33 B4; `docs/committed-text.md` is the page).** Six rules, each
with its enforcer and asserting tests: integers in human-facing text through `fmt_int`
(so an 8-digit row count can never match guard rule G4); CLI strings ASCII or
`console_safe`; no compact dates or run ids in committed file names (`guard.PATH_ID_TOKEN`,
no pragma escape); run-folder contents free of identifier column names and of strings over
64 characters; the `mwh-guard: allow` pragma carries a rationale; DOIs or stable URLs,
never bare PMIDs. `mwh guard` (EP-4; G1/G4 hardened at EP-165 and EP-33 — index blobs,
float renderings, entry paths, notebook source and script types; `selfcheck` verifies the
PreToolUse hook registration) is the pre-commit enforcer.

*History:* built by EP-2 … EP-6, EP-8 … EP-12, EP-17 … EP-22, EP-28 … EP-32, EP-164 … EP-171, EP-33 B4/B5/B6/B8 (completion notes); consolidated at EP-33.

## 16. App structure (D-21)

One Streamlit process, `127.0.0.1` only, tier switcher (fixture/demo/dev/full; default
dev), theme from `theme.py`. The app opens catalogs through `catalog.connect.open_catalog`
(READ_ONLY, app profile, `agent` role unless the owner's shell sets `MWH_ROLE=owner`) and
**caches results, not connections** (§6: it drops its handle when
`meta.catalog_info.build_id` changes, so a rebuild while the Lab app is open never fails
the swap); every aggregate it shows comes through `safe_query` (§12), and the owner-only
row views through the audited `owner_rows()` path (EP-58). Pages (each its own EP):
Catalog & QC · Cohort Builder · Phenotype Studio · Explorer (linked-brush distributions,
heatmaps/correlations, cross-tabs) · Timelines (owner-gated) · Prevalence & Rates ·
Subgroups · Table 1 · Missingness · Analysis (P5) · Survival/Causal (P6) · Models (P7) ·
Protocol Freezer · Runs & Provenance · Reports · Linkage Wizard (P9) · Text (P10, search
only). Linked views: Altair selections → server-side DuckDB re-aggregation (VegaFusion for
large specs); Plotly only for lane/Gantt timelines. All charts read from `viz/` spec
builders so the same spec renders in reports. Small-cell warnings and the row-view gate
are shell-level components (EP-58). Latency target ≤ 5 s on full via marts; pages default
to dev (D-28). The `ui` dependency group is isolated because Streamlit pins `pyarrow<25`
(one venv serves both while the forks resolve to the same pyarrow, §2). Streamlit vs
marimo for the Freezer/Wizard pages is a §21 question for the P4 re-plan.

*History:* planning text (2026-08-16), reconciled with EP-21/30/166's reader protocol and EP-167's tiers; EP-57 … EP-73 planned; consolidated at EP-33.

## 17. Reporting pipeline (D-23)

`Report` object (EP-130): sections, tables (post-suppression), figures (Vega-Lite specs
+ PNG), methods summary, **claim-type label** (exploratory / confirmatory / predictive /
associational / causal), provenance footer (run ids, snapshot ids, protocol hash, env
hash) → Jinja2 → Markdown + self-contained HTML; PDF via Typst (EP-131). Model cards,
methods summaries and executive one-pagers are templates (EP-132). Anything leaving
`runs/` for `reports/` or git passes `disclose.check` and gets a sidecar (EP-133). The
shipped precedents: the tracer's `report.md` (EP-31 — claim type *associational
(exploratory)*, "MIMIC-IV analyses are retrospective", an explicit does-not-claim list,
integers via `fmt_int`) and the `docs/analyses/` case-study convention (EP-32: `NN-slug.md`
names, required sections, claim-type labels, Reproduction blocks, the `benchmarks:begin/end`
marked block a command regenerates).

*History:* planning text (2026-08-16), reconciled with EP-31/32's shipped report shapes; P8 planned; consolidated at EP-33.

## 18. Notes segregation (D-3)

`C:\mimicdata\notes\` lake + `notes.duckdb` (DuckDB FTS) built in EP-148, attached only
by `mwh … --with-notes` in owner role, never by `safe_query` (`mimiciv_note` is outside
`safe.ALLOWED_SCHEMAS` and no stage step writes note tables into `lake/core` — §5 coverage
tests assert the negative), never by the app except the Text page's aggregate search
results (counts, ids only when owner-gated). Note text never enters run records, reports,
tool output or git.

*History:* planning text (2026-08-16), reconciled with EP-30's allow-list and EP-17's coverage tests; EP-148 planned; consolidated at EP-33.

## 19. External-data landing & linkage (D-36)

`C:\mimicdata\ext\<source>\` with a `source.yaml` (license, provenance, DUA, keys) written
by the profiler (EP-137) — `ext\demo\source.yaml` from `mwh demo fetch` (EP-22) is the
shipped precursor of that register; mapping YAML for concepts/units (EP-138; mimic-code
`concept_map/*.csv`, excluded from the EP-8 vendor slice with its upstream URL recorded, is
the head start for itemid → LOINC/SNOMED); key validation and join-cardinality/coverage
report (EP-139); commit into `mimiciv_ed` / `ref.*` schemas via the DAG runner (`stage`
steps with the contract's six `mimiciv_ed` tables — the ED demo is already fetched and
verified, never staged, §4). The Linkage Wizard (EP-140/141) drives exactly this sequence;
ED (EP-142) and a reference table (EP-143) are the v1 test cases.

*History:* planning text (2026-08-16), reconciled with EP-8's exclusions and EP-22's register; P9 planned; consolidated at EP-33.

## 20. Testing strategy

**Shape.** pytest (+ hypothesis where a property is the point); one `tests/ep/test_ep<NN>.py`
per brief (zero-padded file, unpadded marker `ep_<n>`; `ep_0` … `ep_199` registered in
`conftest.py`, `--strict-markers`); DuckDB data checks (row-count pins, key uniqueness,
referential integrity, unit plausibility) as first-class tests; golden files only for
aggregates that pass `disclose.check`; never snapshot real rows into fixtures, cassettes
or goldens. `mwh verify EP-n [-- <pytest args>]` runs the marker set in a **fresh
interpreter** (spawn-safe; a docs-only brief returns 0 with "nothing to run", a code brief
without a test module returns 2, "nothing collected" becomes 2 with a marker hint).
EP-33's acceptance spans `tests/ep/test_ep33*.py` — `test_ep33.py` (foundations),
`_cli.py`, `_hygiene.py`, `_loader.py`, `_safe.py`, one file per workstream area, all
marker `ep_33`.

**Tier ladder (EP-12, EP-168; `tests/conftest.py`, documented in `tests/README.md`).**
`@pytest.mark.tier("fixture" | "dev" | "full", needs="catalog" | "raw" | "lake")` names
the data a test needs; an unmarked test is `fixture`. `pytest --tier {fixture,dev,full}`
(fallback the **`PYTEST_TIER`** environment variable, then `fixture`) selects the
**maximum** tier: the ladder is `fixture < dev < full`, tests above it are **deselected**.
**Readiness is per need, not one catalog file**: inside the selected ladder a dev/full
test requests the readiness fixture for exactly the artefact it needs — `dev_catalog`,
`full_catalog`, `raw_root`, `dev_ready(step)` (reads `lake/manifests/status.json`
`{"steps": {"<step>": {"dev_ready": true}}}`), `item_tier` — and is **skipped with a
reason naming the missing path** while it is absent, so a fresh checkout is never red for
lack of data and "dev green" is never vacuous. **Demo tests are orthogonal opt-in**:
`@pytest.mark.demo` + `--with-demo` (env `PYTEST_DEMO`), deselected unless opted in,
skipped while `catalog_path("demo")` is missing — `demo` never joins the ladder. The knobs
are deliberately not `MWH_`-prefixed (a test knob is not a setting; `Settings` is
`extra="forbid"` on `.env`/`mwh.toml` lines and `test_ep03` asserts `.env.example` parity),
and the pytest tier never reads `settings.default_tier`. `pytest_plugins = ["pytester"]`
backs the marker-selection tests.

**Fixtures and helpers.** Session fixtures `tier`, `contract`, `fixture_root`,
`fixture_catalog` (EP-12's in-memory catalog over the 31 fixture CSVs) and
`fixture_lake_catalog` (EP-21's runner-built fixture lake + `fixture.duckdb` in a temp
root). `tests/helpers.py` (importable, not a plugin; `tests/` is on `sys.path` via
`conftest.py`): `cli_runner()` (COLUMNS=200), `tmp_data_root(monkeypatch, tmp_path)`
(clears every `MWH_*` / `PYTEST_*` variable, rebuilds the settings cache),
`fresh_interpreter(argv)`, `assert_import_budget(...)` (§15). Tests build into temp roots
via `--data-root` / `MWH_DATA_ROOT` and never into the real one; the fixture tier's data is
the committed tree, byte-identical across sessions (`GENERATOR_VERSION` 0.2.0).

**Churn rule (EP-168; `tests/README.md`).** A new EP must not need to edit an earlier
`test_ep*.py`: rolling literals (file counts, row totals, the verify probe's "code brief
without a test module") are read from `tests/fixtures/manifest.json`, the contract,
`build_plan()` or a crafted roadmap, never pinned. Earlier tests change only for a specific
consolidation item or to follow a deliberate surface change, with a dated
`# EP-n: …` comment.

**Gates.** `poe check` = `lint` (ruff check) + `fmt-check` (ruff format --check, the gate
EP-33 B5 added) + `typecheck` (pyright) + `test` (pytest, fixture tier, serial on purpose —
D-42's AV heuristics); `test-fast` = `pytest -n auto` (xdist opt-in); `test-dev` /
`test-full` = `pytest --tier dev|full`; `roadmap-check` = `mwh verify --roadmap`. Tasks run
from `mimicwarehouse/`, or from the repository root through `poe_tasks.toml`
(`uv run --project mimicwarehouse --group dev poe check`; deliberately not a root
`pyproject.toml`, which would enter uv's project discovery). Pre-commit: `mwh guard` →
`ruff-check` → `ruff-format --check` → `pre-commit-hooks` v6.0.0 (large files ≤ 20 MB,
merge-conflict, yaml/toml/json, end-of-file, trailing-whitespace, private keys; the
whitespace fixers exclude the vendored mimic-code tree so `sha256_lf` stays true). Full-tier
verification (EP-28) runs as `tier("full")` tests that read the ledgers and manifests and
append `kind: verify` benchmark lines — never rows.

*History:* built by EP-6, EP-7, EP-12, EP-21, EP-28, EP-166, EP-168, EP-33 B5/B6 (completion notes); consolidated at EP-33.

## 21. Open design questions (to be resolved by the named EP)

*(Live list. Settled questions keep their line with a "resolved by" clause so the
reasoning stays findable; the evidence is in the named EP's completion note.)*

- Exact bucket count trade-off (100 buckets × ~30 tables ≈ 3,000 files) vs Defender +
  Malwarebytes/NTFS overhead (D-42; the ARW module judges write bursts) — **resolved by
  EP-28 (2026-08-29), confirmed at EP-33 (D-44 item 3): keep 100 buckets.** The complete
  core lake holds 2,407 `part-0.parquet` files + 24 `_progress.json` markers in 2,433
  directories (24 partitioned tables × 100 buckets + 7 single-file dims — under the
  planning estimate because dims and the ed/note schemas stay out of the scheme); one
  `os.scandir` sweep of the tree takes 0.091 s; no AV stall or quarantine across the five ⏱
  staging jobs. Per-bucket ~1 MB files at dev scale (EP-18: 100 files in 0.8–1.3 s on the
  fixture, ~5 ms per file-create) are below NTFS overhead territory.
- Whether to parallelise the per-bucket sort of pass 2 (its "pass 2 ≥ pass 1" trigger fired
  broadly in EP-26/EP-28) — **parked by EP-33 (2026-08-30, D-44 item 3)**: staging is
  complete and never re-runs in P3; the fired triggers are recorded in
  `roadmap/final-roadmap.md` and the question is re-examined before P9's ED staging.
- Whether `dev.duckdb` should materialise (not just view) small tables for app latency —
  **resolved by EP-21 (2026-08-29) for P2: dims are materialized as tables in every tier,
  subject-keyed tables are views** over the partitioned lake (Hive pruning already makes
  the dev views fast — the dev catalog builds in ~1 s with its `subject_bucket IN
  (0,1,2,3,4)` filter). EP-55 revisits materialization for marts; until then no per-tier
  special-casing.
- FTS engine for notes if DuckDB FTS build exceeds memory — SQLite FTS5 fallback (EP-148).
- Whether the events spine should include a chartevents subset (vitals only) — EP-50 /
  the P3 re-plan (EP-54); §3's re-estimate sizes the spine at 2.5–4 GB with or without it.
- Streamlit vs marimo-app for the Freezer/Wizard pages if the rerun model bites — re-plan P4
  (EP-74).

*History:* planning text (2026-08-16); answers recorded by EP-18, EP-21, EP-28, EP-33 (completion notes, D-44); consolidated at EP-33.
