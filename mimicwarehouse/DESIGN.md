# mimicwarehouse — DESIGN

Architecture of the local MIMIC-IV data lab. This document is the "why and how"; the
roadmap (`../roadmap/README.md`) is the "when". Every module below is marked with the
EP brief that builds it — as of 2026-08-16 nothing here existed as code; the P0/P1a
modules (EP-1 … EP-12, EP-164, EP-165) have since shipped — the living inventory is the
workspace `README.md` § "State of the workspace" (D-43 item 13), the history is the dated
notes below. Sessions update this file when an EP changes a design fact (append a dated
note; do not rewrite history).

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

## 2. Machine & constraints (verified 2026-08-16)

| Resource | Value | Design consequence |
|---|---|---|
| CPU | Intel Core Ultra 9 285H, 16 cores | DuckDB `threads=12`, Polars threads 12; leave headroom for the UI |
| RAM | 64 GB | DuckDB `memory_limit` 36–40 GB; never `pandas.read_csv` a large table |
| GPU | RTX PRO 2000 Blackwell laptop, 8 GB VRAM, sm_120 | CPU-first; GPU is an opt-in dependency group (D-16); batch sizes ≤ 6 GB working set |
| Disk | one 954 GB NVMe, ~415 GB free | see §3 budget; keep ≥ 100 GB free during builds |
| OS | Windows 11 Pro, PowerShell 7 | native Windows (D-14); `spawn` multiprocessing; MAX_PATH; `.gitattributes` for CRLF |
| Python | uv-managed CPython 3.13 (D-15) | system 3.14 untouched (`python-preference = only-managed`) |
| Encryption | BitLocker on C: | required by the DUA; recorded by `mwh doctor` |
| Cloud | GoogleDriveFS (G:), Cryptomator (D:) mounted | nothing warehouse-related may live on G:/D: (file locks, sync = redistribution) |

> **Note (2026-08-17, EP-1).** Toolchain installed: **uv 0.12.5** (winget, user scope;
> `%LOCALAPPDATA%\Microsoft\WinGet\Links\uv.exe`; cache `%LOCALAPPDATA%\uv\cache`; managed
> interpreters under `%APPDATA%\uv\python` — all on C:) and **uv-managed CPython 3.13.15**
> (`mimicwarehouse/.python-version` = `3.13`; `.venv` in the workspace). System CPython
> 3.14.7 untouched. Resolved core stack: pandas 3.0.5 · numpy 2.5.2 · scipy 1.18.0 ·
> polars 1.43.2 · pyarrow 24.0.0 · statsmodels 0.14.6 · lifelines 0.30.0 ·
> scikit-learn 1.9.0 · altair 6.2.2 · pydantic 2.13.4; `ui`: Streamlit 1.61.1
> (`pyarrow<25,>=7.0`). pyarrow 25.0.1 exists on PyPI, but uv unified both resolver
> forks on 24.0.0 (one version satisfies core and `ui`), so a single venv serves both
> today; the `[tool.uv] conflicts` fork machinery is in place for when they diverge. Only
> sdist-only package in the lock: `autograd-gamma 0.5.0` (pure Python, lifelines
> transitive) — see the EP-1 completion note.

> **Note (2026-08-17, EP-7).** Two machine facts the table above did not carry. (1) **Endpoint
> security is two real-time products**, Windows Defender *and* Malwarebytes 5.1 Premium (D-38
> addenda, D-42, roadmap Risk 12): Malwarebytes' Ransomware Protection judges processes by I/O
> pattern and quarantined the unsigned Git `bash.exe` during a burst file operation; the owner
> allow-lists the toolchain (Git, uv, uv's CPython, the workspace `.venv`, pre-commit's hook
> venvs) and both data locations in Malwarebytes and excludes `C:\mimicdata` in Defender, and keeps
> both products on. Design consequences: every long writer (loader EP-17/18, fixture generators
> EP-11/12, inventory EP-10) is resumable and logs progress; sessions write files through the
> Write/Edit tools, not shell heredocs; the §21 bucket-count question ("Defender/NTFS overhead")
> now reads "Defender + Malwarebytes/NTFS overhead"; `mwh doctor` gains an `antivirus` check at
> EP-164 that names the products it can see (neither exclusion list is readable non-elevated).
> (2) **Console code page**: the shells that run `mwh` may be cp1252 — CLI output must stay ASCII
> or pass through `verify._console_safe` (roadmap Risk 13); JSON outputs are unaffected. Versions
> re-verified at EP-7: unchanged from the EP-1 note; `mwh doctor` 8 pass · 5 info; 414.9 GB free.

> **Note (2026-08-28, EP-166 — console, updating the EP-7 note's item (2)).** Since EP-165,
> `.claude/settings.json` sets `PYTHONUTF8=1` for both tool shells (D-43 item 2), so `mwh` run
> from a Claude session gets UTF-8 stdio and the cp1252 crash class is gone *there*; other hosts
> (a plain PowerShell, Task Scheduler) may still be cp1252. The standing rule is unchanged: new
> CLI strings stay ASCII or pass through the console helper — EP-167 replaces the per-module
> `_console_safe` with one shared `mimicwarehouse/console.py` and a UTF-8 entry point (D-43
> item 12). Roadmap Risk 13 is now a two-line pointer to this note.
>
> *Shipped (2026-08-28, EP-167):* `mimicwarehouse/console.py` (`console`, `err_console`,
> `console_safe`) and the `mwh = "mimicwarehouse.console:run"` entry point, which reconfigures
> stdout/stderr to UTF-8 with `errors="replace"` before running the app; every command module
> imports the shared consoles; JSON outputs end in plain `\n` (never `\r\r\n`).

## 3. Layers & disk budget

```
raw        source material/<dataset>/<module>/*.csv      immutable, gitignored, never edited (D-30)
  │  EP-17/18 loader (typed COPY → Parquet, subject buckets)
lake       C:\mimicdata\lake\core\<schema>\<table>\subject_bucket=NN\*.parquet   canonical typed snapshot
  │  EP-21 catalog builder
catalog    C:\mimicdata\warehouse\{demo,dev,full}.duckdb  views over the lake + small materialized tables
  │  EP-19 DAG runner (mwh build) · EP-37 concept runner
derived    lake\derived\<concept|phenotype|spine>\…       mimic-code concepts, phenotypes, events spine
  │  EP-47 cohort compiler · EP-55/56 marts
marts      lake\marts\…  + studies\<study_id>\…            cohorts, feature matrices, latency marts
```

Everything below `raw` is **rebuildable from raw + code**, so the honest backup of the
warehouse is a tested rebuild recipe (`mwh init`, EP-158). Non-reproducible state (run
ledger, protocol registry, audit) is backed up separately (EP-52).

Disk budget (estimates to be measured in P2 and recorded in the benchmark ledger):
raw CSV 98 GB (kept) · core lake ~18–25 GB · derived + spine 15–30 GB · marts 5–15 GB ·
notes lake + FTS + embeddings 5–15 GB (P10 only) · models ≤ 10 GB · build temp peak
60–100 GB · uv cache + venv ~15 GB · OS hiberfil/pagefile 25–40 GB. Rule: **never below
100 GB free**; `mwh doctor` and `mwh build` refuse to start under that.

> **Note (2026-08-17, EP-3).** The data-root tree is fixed by `Settings.layout`
> (`config.py`; 15 keys, created idempotently by `mwh paths --create`, which also writes a
> `README.txt` warning never to sync the folder). Owners of each directory in brackets:
>
> ```
> C:\mimicdata\                          MWH_DATA_ROOT (D-29; local fixed NTFS/ReFS only)
> ├── README.txt                         "managed by mimicwarehouse; never sync"
> ├── lake\                              lake            Parquet layers (§5)
> │   ├── core\                          lake_core       typed snapshot of raw (EP-17/18)
> │   ├── derived\                       lake_derived    concepts, phenotypes, spine (EP-37+/50)
> │   ├── marts\                         lake_marts      cohorts, features, latency marts (EP-47/55)
> │   └── manifests\                     lake_manifests  <build_id>.jsonl → snapshot ids (EP-19)
> ├── warehouse\                         warehouse       {demo,dev,full}.duckdb catalogs (EP-21) — catalog_path(tier)
> ├── runs\                              runs            ledger.jsonl · audit.jsonl · benchmarks.jsonl · <run_id>\ (EP-30/35)
> │   └── jobs\                          runs_jobs       background ⏱ job logs + progress files (EP-19+)
> ├── models\                            models          model registry artefacts (P7)
> ├── notes\                             notes           segregated notes lake + notes.duckdb (EP-148, owner-only)
> ├── ext\                               ext             external sources <source>\source.yaml (§19)
> │   └── demo\                          ext_demo        MIMIC-IV Demo 2.2 + ED Demo (EP-22)
> ├── studies\                           studies         study workspaces <study_id>\ (§3 marts)
> └── tmp\                               tmp             scratch
>     └── duckdb\                        tmp_duckdb      DuckDB temp_directory (§6; MWH_DUCKDB_TEMP_DIR overrides, same volume)
> ```
>
> `Settings` refuses a data root that is not on a local `DRIVE_FIXED` NTFS/ReFS volume, whose
> volume label matches a sync client (Google Drive, OneDrive, Dropbox, Box, Cryptomator,
> iCloud), that lies under `%OneDrive%`, or whose drive letter is in `forbidden_drives`
> (default G:, D:); the temp dir must share the data-root volume; `mwh paths --create` and
> (from EP-19) `mwh build` additionally require `min_free_gb` (100) free. On this machine
> the probes see C: = fixed NTFS "Windows", D: = remote cryptoFs "Google Cryptomator",
> G: = fixed FAT32 "Google Drive" — the last two are refused by label, filesystem and letter.

> **Note (2026-08-28, EP-166 — tree entries decided by the 2026-08-18 retro; code EP-167+).**
> The EP-3 tree above grows as follows, so P2 briefs stop inventing paths ad hoc (D-43 items
> 6–7; ledger ARCH-3, ARCH-12): under `lake\` the **per-tier lake roots** `fixture\`
> (`lake_fixture`) and `demo\` (`lake_demo`) — the fixture/demo tiers never write into the
> credentialed `lake\core` — plus `rejects\` (`lake_rejects`, EP-17's per-table reject
> Parquet); under `lake\manifests\` the EP-10 `raw\<dataset-dir>.jsonl` + `raw_snapshot.json`
> (already real) and EP-17/19's `status.json`, `snapshots.json` and `<build_id>.jsonl`; under
> `warehouse\` the catalogs become `{fixture,demo,dev,full}.duckdb` plus `runs.duckdb`
> (EP-30/35 views over the JSONL ledgers) and the `.build.lock` (EP-19). `Settings.layout`
> goes 15 → **18** keys (`lake_fixture`, `lake_demo`, `lake_rejects`) at EP-167, which bumps
> the `test_ep03` pin once and updates `mwh paths`; briefs reference the layout keys, not
> literal paths.

## 4. Tiers & sampler spec (D-18, D-27)

| Tier | What | Where | Used for |
|---|---|---|---|
| `fixture` | synthetic mini-MIMIC generated by `mimicwarehouse.fixtures` (EP-11/12); **all ids ≥ 90 000 000** so the guard can recognise synthetic rows; committed to `mimicwarehouse/tests/fixtures/` | repo | pytest default; CI-like runs without credentials |
| `demo` | MIMIC-IV Clinical Database Demo 2.2 + MIMIC-IV-ED Demo 2.2 (ODbL, 100 subjects), downloaded on demand by `mwh demo fetch` (EP-22), passed through the 2.2 → 3.1 column map | `C:\mimicdata\ext\demo\` → `demo.duckdb` | screenshots, cloner path, concept count-pinning, showcase |
| `dev` | deterministic 5 % of full: `subject_id % 100 IN (0,1,2,3,4)` — a partition filter over the same lake, so it cannot drift from full | `dev.duckdb` | every EP's development + tests |
| `full` | all subjects | `full.duckdb` | recorded full-tier runs; scale-contract EPs |

`subject_bucket = subject_id % 100` (MIMIC subject_ids are 10 000 000–19 999 999) is the
single partitioning key for subject-keyed tables; dims are unpartitioned. Tables without
`subject_id` (e.g. `d_*`, `provider`, `caregiver`) exist identically in every tier.

**Demo tier vs demo mode.** *Demo tier* = the ODbL dataset loaded as a tier. *Demo mode*
= the app (EP-159) launched with `--tier demo` and export/row-view features enabled,
because that data is redistributable. Never confuse the two in briefs.

Every brief states its tier using the vocabulary in the roadmap README (`fixture` /
`fixture+dev` / `fixture+dev+full` / `fixture+dev (full ⏱ → verified by EP-n)` / `demo` /
`n/a`).

> **Note (2026-08-18, EP-11).** The `fixture` tier is real for `hosp`: `mimicwarehouse.fixtures`
> (`mwh fixtures build [--out DIR] [--seed N] [--subjects N] [--no-check] [--json]`) writes
> `mimicwarehouse/tests/fixtures/mimic-iv-3.1/hosp/<table>.csv` (22 tables, contract column order,
> raw-layout paths so EP-17 can point `--source tests/fixtures/mimic-iv-3.1` at it) plus
> `tests/fixtures/manifest.json` (per file: sha256, bytes, rows, seed, generator version; plus the
> spec, `contract_hash`, totals) and `tests/fixtures/README.md`. Defaults = the committed fixture:
> seed 2026, **120 subjects** with consecutive ids from 90 000 000 (so `subject_id % 100` spans
> buckets 0–99 and the dev filter `< 5` keeps exactly 10), 186 admissions, 75 ICU segments
> (`plan.icu_segments` → EP-12 `icustays`), 27,954 rows / 2.91 MiB (budget ≤ 6 MB hosp, ≤ 10 MB
> total after EP-12). Regeneration is byte-identical for the same spec/generator version
> (`tests/ep/test_ep11.py::test_fixture_drift`); the fixture is regenerated by code changes, never
> edited by hand. EP-12 adds `mimic-iv-3.1/icu/` from the same plan and extends the manifest.

> **Note (2026-08-18, EP-12).** The `fixture` tier is complete for `hosp` + `icu`: the same
> `mwh fixtures build` now also writes `mimicwarehouse/tests/fixtures/mimic-iv-3.1/icu/<table>.csv`
> (9 tables — `icustays` = the 75 `plan.icu_segments` verbatim, so it agrees with `transfers` by
> construction; `chartevents` 20,125 rows / 1.96 MB, `outputevents` 1,857, `ingredientevents` 364,
> `inputevents` 285, `procedureevents` 136, `datetimeevents` 116, `d_items` 47, `caregiver` 15) and
> one `manifest.json` (`modules: [hosp, icu]`, 31 files) + `README.md` for both modules — **50,974
> rows / 5,370,674 bytes = 5.12 MiB** in total (budget ≤ 10 MB; chartevents ≤ 3 MB). The hosp
> bytes did not move (per-table child generators). Tests read the tree through
> `mimicwarehouse.fixtures.catalog.build_fixture_catalog()` — an in-memory DuckDB (app profile of
> `Settings.duckdb_settings`) with the 31 contract tables loaded by `read_csv(columns=<contract
> types>, ignore_errors=false)` in ≈ 0.6 s — until EP-21 builds a real `fixture.duckdb` from the
> same CSVs with the loader. Real itemids in `d_items` are typed from public docs; the
> `datetimeevents` / `ingredientevents` items, which no vendored concept reads, carry fixture-only
> 2401xx / 2402xx ids on purpose.

> **Note (2026-08-28, EP-166 — fixture/demo lake roots; decided 2026-08-18, D-43 item 7;
> code EP-167).** The `fixture` tier's *sources* stay committed in the repo (the table
> above), but a fixture tier **built for keeps** through the loader/DAG lives under the data
> root like every other tier: lake root `lake\fixture` (`Settings.lake_root("fixture")`),
> catalog `warehouse\fixture.duckdb` (`catalog_path` is already uniform across all four
> tiers), and likewise `lake\demo` for the demo tier. Fixture/demo builds **hard-refuse**
> resolving to the credentialed `lake\core`, so a stray `mwh build --tier fixture` can never
> pollute the real lake or its `status.json`. Consequence: the app and `mwh sql` may target
> `--tier fixture` (GOVERNANCE §6 row-view development no longer depends on the demo tier
> alone). Tests keep building into temp roots via `--data-root`. Code lands in EP-167
> (`lake_root(tier)`, layout keys, runner refusal); EP-12's in-memory
> `build_fixture_catalog()` remains the test-time path until EP-21.

> **Note (2026-08-28, EP-169 — disjoint fixture id floors + regeneration 0.2.0; decided at
> the 2026-08-18 retro, D-27 addendum, ledger FXT-1/FXT-2/FXT-4).** `FixtureSpec` now
> defaults to **one floor per id space**: `first_subject_id 90_000_000`, `first_hadm_id
> 91_000_000`, `first_stay_id 92_000_000`, `first_event_id 93_000_000` (labevent/specimen/
> microevent/micro_specimen/transfer/pharmacy/order ids) and `first_caregiver_id
> 93_900_000` — pairwise disjoint (enforced by `fixtures.check`), all ≥ the guard floor,
> none an 8-digit 1/2/3 token, so a wrong-key join can never match by accident. The
> regeneration is `GENERATOR_VERSION` **0.2.0** (same totals: 50,974 rows / 5.12 MiB); the
> manifest now records numpy/polars/python versions (provenance, deliberately unpinned)
> and pins the contract by `contract_schema_hash` = `Contract.structural_hash()`
> (load-relevant facts only; the full `content_hash()` stays informational, so a
> comment-only contract edit no longer forces a regeneration). Which vendored concepts are
> empty/partial on the fixture is documented in `tests/fixtures/COVERAGE.md`
> (hand-maintained; EP-41 extends the vocab and regenerates as 0.3.0).

## 5. Lake physical layout

Hive-partitioned Parquet: `lake/core/<schema>/<table>/subject_bucket=NN/part-*.parquet`,
sorted by the contract `sort_keys` (`subject_id`, the time column, then a same-table
id/sequence tie-break since EP-169), ZSTD level 3, ~1 M-row row groups, statistics on.
Schema names mirror mimic-code: `mimiciv_hosp`, `mimiciv_icu`, `mimiciv_ed` (from EP-142),
`mimiciv_derived`; plus `meta` (catalog/profiles/dictionaries), `marts`, `runs`
(views only — see §11). Every Parquet file has a manifest line
(`sha256, bytes, rows, schema_hash, writer version, source manifest id`) in
`lake/manifests/<build_id>.jsonl`; the **snapshot id** of a layer is the hash of its
manifest.

Large tables (`chartevents` 40 GB, `labevents` 17.5 GB, `emar_detail` 8.3 GB) use a
two-pass load (EP-18): stream `COPY … (PARTITION_BY subject_bucket)` with
`preserve_insertion_order=false`, then sort each bucket file; resumable per bucket via a
progress file. Small tables load in one pass (EP-17). Loader accepts `.csv` and `.csv.gz`
and applies column maps (demo 2.2 → 3.1).

> **Note (2026-08-28, EP-166 — directory swap on Windows + scope; decided 2026-08-18,
> ledger ARCH-2/ARCH-12/ARCH-14; code EP-17/18).** Three corrections to the section above,
> settled by the retro's probes (evidence in the ledger; no data touched):
>
> 1. **Restage over an existing table directory.** `os.replace(dest.new → dest)` fails on
>    Windows with `PermissionError [WinError 5]` whenever `dest` exists (empty or not), and
>    renaming a directory that holds an open file fails the same way — so the one-line
>    "swaps `.new` → `dest` atomically" recipe only covers a *first* stage. EP-17 ships
>    `paths.swap_dir(new, dest)` implementing the **rename-aside two-step**: restore a stale
>    `<table>.old` if `dest` is missing (crash recovery) / `rmtree` a stale `.old` /
>    `os.rename(dest → dest.old)` / `os.rename(new → dest)` / `rmtree(dest.old)` with a
>    PermissionError retry loop — crash-safe, **not** atomic. A table under (re)stage is
>    unavailable in every tier; readers must be closed first (even the rename fails while
>    any handle is open inside the directory), which matters because `dev.duckdb`'s views
>    point at the **same files** a full-tier restage rewrites — a dev rebuild while the app
>    is open needs the same courtesy as the catalog swap (§6 note). Pass-1 progress travels
>    in `<dest>.new/_progress.json` so the crash window between swap and progress-write is
>    closed, and `partition_glob` is pinned to `subject_bucket=*/part-*.parquet` so
>    in-progress `raw_*` files are never visible to a catalog view.
> 2. **"Large" is the contract's word.** The three-table list above predates EP-9: two-pass
>    = every contract table with `load_class: large` (13 today; 12 after EP-169 moves
>    `microbiologyevents` to `small` — rule of thumb CSV > 1 GB).
> 3. **Stage coverage is 31 tables.** The P2 stage steps cover exactly the `mimiciv_hosp`
>    (22) + `mimiciv_icu` (9) contract tables; `mimiciv_ed` (6) has no stage step until
>    EP-142 and `mimiciv_note` (4) none until EP-148 — and note tables go to the segregated
>    notes lake (§18), **never** `lake/core`. Coverage tests assert the negative too.
>
> The manifest-line field "source manifest id" above is two fields since the retro:
> per-file `source_sha256` **plus** the 41-file `raw_snapshot_id` — see the §11 glossary
> note (D-43 item 11).

> **Note (2026-08-28, EP-169).** Correction to item 2 of the note above: the owner kept
> `microbiologyevents` at `load_class: large` (D-17 addendum — a deliberate exception to
> the "> 1 GB" rule of thumb; its raw CSV is 909 MB), so two-pass = the contract's **13**
> `large` tables, unchanged. The contract `sort_keys` now end in a same-table id/sequence
> tie-break (EP-169, ledger ARCH-4), so EP-18's per-bucket sort and its "sha256 stable
> across two runs" determinism tests are well-defined under ties.

> **Note (2026-08-29, EP-17 — shipped layout facts of the unpartitioned stage).** Three
> specifics the section above left open, fixed by the EP-17 code: (1) an **unpartitioned**
> (dim) table is one file, `<lake_root>/…/<schema>/<table>/part-0.parquet` — no
> `subject_bucket=` level (the Hive pattern above applies to partitioned tables only), and
> restages publish through `paths.swap_dir` exactly as the 2026-08-28 note prescribes;
> (2) manifests and status are **per lake root**: `<lake_root(tier)>/manifests/<build_id>.jsonl`
> and `…/manifests/status.json`, so a fixture/demo build's manifests live under
> `lake/fixture/` / `lake/demo/` beside its Parquet, never in the credentialed
> `lake/manifests/` (for dev/full, `lake_root` = `lake/`, i.e. exactly the path above);
> rejects follow the same rule (`<lake_root>/rejects/<schema>/<table>/<build_id>.parquet` ≡
> `Settings.rejects_root(tier)` for the standard roots). (3) `status.json` is
> `{"steps": {"<schema>.<table>": {tier_complete, dev_ready, build_id, rows, bytes, files,
> rejects, finished_at}}}` — the shape the EP-168 `dev_ready(step)` test fixture reads;
> EP-17 writes the count fields and leaves `tier_complete: null` / `dev_ready: false` to
> the EP-18/19 orchestration. Manifest lines carry the §11 provenance pair
> (`source_sha256` + `raw_snapshot_id`) plus a per-table `schema_hash` (sha256 of the
> ordered `(name, type)` list) and `writer_version` (package + DuckDB versions).

> **Note (2026-08-29, EP-18 — shipped facts of the partitioned stage).** Four specifics
> fixed by the code (`loader/buckets.py`, `loader/paths.py`): (1) the per-bucket sort's
> order of operations is **publish-before-delete** — `os.replace(_sorting.tmp →
> part-0.parquet)`, record the bucket in `_progress.json`, *then* delete the `raw_*` files
> (the brief's "delete the raws and replace" read literally would let a crash between the
> two lose a bucket's only complete copy; a stale `_sorting.tmp` is deletable precisely
> because the raws still exist). (2) Pass 2 reads the raws with `hive_partitioning=false`:
> DuckDB's auto-detection on a `subject_bucket=<n>/` path would otherwise synthesise the
> partition column **into** the sorted file (caught by the small≡large sha256 test).
> (3) Resume applies only when the recorded `buckets_requested` equal the new request and
> `complete` is false; any other progress state — including a **full** request over a
> `tier_complete: "dev"` table — restages the whole table. Pass-1 sweeps write disjoint
> bucket ranges into the same `.new` root under `OVERWRITE_OR_IGNORE` (DuckDB's `APPEND`
> demands a `{uuid}` filename pattern, which would break the deterministic `raw_{i}`
> names). (4) `status.json` semantics: `dev_ready: true` as soon as `settings.dev_buckets`
> are all sorted (logged `dev-ready <schema>.<table>` before `complete`);
> `tier_complete` = `"full"` when all 100 buckets were requested, `"dev"` when the request
> covers the dev buckets, otherwise left unchanged.

## 6. DuckDB configuration & the single-writer rule

Explicit in every build/analysis process (never rely on defaults): `memory_limit`
(36–40 GB builds; 8–16 GB app), `threads` (12), `temp_directory`
(`C:\mimicdata\tmp\duckdb`), `max_temp_directory_size` (explicit, e.g. 150 GB),
`preserve_insertion_order=false` for bulk loads. Pin **one** DuckDB version across the
Python client and any CLI (storage format changes between 1.4 and 1.5); record it in
every run manifest.

DuckDB allows one read-write process **or** many read-only processes per file. Therefore:
`mwh build` is the only writer; it writes `<tier>.duckdb.new` and atomically swaps; the app,
notebooks and analyses open `access_mode='READ_ONLY'`. Anything that must be written while
readers are open (audit, run ledger, benchmark ledger) goes to **append-only JSONL** under
`C:\mimicdata\runs\` and is exposed through `runs.duckdb` views rebuilt on demand (§11).

> **Note (2026-08-17, EP-1).** DuckDB is pinned to **`duckdb==1.5.5`** in
> `pyproject.toml` (exact pin; `tests/ep/test_ep01.py` asserts the installed version equals
> the pin, so bumping it is a deliberate one-line change + re-lock + version note here). No
> DuckDB CLI is installed or permitted (GOVERNANCE §4); the Python client is the only
> engine, so the "one version across every process" rule has a single moving part.

> **Note (2026-08-28, EP-166 — catalog reader/writer protocol; owner decision 2026-08-18,
> D-43 item 6; ledger ARCH-1; code EP-21/30/35/57).** The paragraph above says the writer
> "atomically swaps" — on Windows it cannot: `os.replace(<tier>.duckdb.new → <tier>.duckdb)`
> raises `PermissionError [WinError 5]` while **any** process holds `<tier>.duckdb` open
> (verified with duckdb 1.5.5 READ_ONLY handles; probe in the ledger). The adopted protocol
> is the **rename-aside two-step**: `os.rename(<tier>.duckdb → <tier>.duckdb.old)` — this
> *succeeds* with readers open, because DuckDB opens files with `FILE_SHARE_DELETE` — then
> `os.replace(.new → <tier>.duckdb)`, then `os.remove(.old)` (Windows holds it
> delete-pending while a reader lives). Open readers keep serving the old snapshot until
> they reconnect. Caveats the implementing EPs own: (a) the swap is *not* atomic — there is
> a sub-millisecond window with no `<tier>.duckdb`, so `open_catalog` (EP-21) retries on
> `FileNotFoundError`; (b) DuckDB's in-process instance cache is keyed on **path** — a
> process that still holds an old connection and re-connects to the same path gets the OLD
> instance, so it must close first (the EP-57 app clears its cached connection when
> `meta.catalog_info.build_id`/mtime changes; cross-process readers are unaffected);
> (c) the same scheme covers `warehouse/runs.duckdb` (EP-30/35 `mwh runs refresh`), which
> the app may hold ATTACHed. The app **caches results, not connections** (EP-57), so a
> rebuild while the Lab app is open no longer fails. Two companion rules from the same
> review: **one build-profile (36 GB / 12-thread) connection per machine at a time** — the
> EP-19 build lock covers `mwh build`; tests and ad-hoc readers use the app profile
> (ledger ARCH-11) — and **catalogs are derived and disposable**: they embed absolute lake
> paths and assert the DuckDB version on open, so after a data-root move or a DuckDB pin
> bump the fix is `mwh build --select catalog` per tier, never surgery (ledger ARCH-16).
> The fallback, if rename-aside misbehaves in practice, is versioned catalog files + a
> `.current` pointer (D-43 alternatives).

## 7. Schema, keys, time semantics, unit of analysis

- **Schema contract** (EP-9): repo YAML transcribed from mimic-code `create.sql` (hosp,
  icu, ed, note) — column names, DuckDB types, nullability, PK/FK in `keys.yaml`, unit
  expectations. The loader reads CSVs with declared types (no sniffing 40 GB); the
  catalog applies `COMMENT`s from the same YAML.
- **Keys**: natural keys only (`subject_id`, `hadm_id`, `stay_id`, `emar_id`,
  `pharmacy_id`, `poe_id`, `itemid`, …); integrity tests per tier (EP-28/44).
- **Time semantics** (EP-34): PhysioNet's shifted timestamps are stored as naive
  `TIMESTAMP` exactly as shipped; `anchor_year_group` (2008–2010 … 2020–2022) is the only
  cross-patient temporal axis (temporal holdouts split on it); analyses use within-patient
  relative times (`hours_since_icu_intime`, etc.); `dod` is available ~1 year after the
  last discharge → explicit censoring rule per outcome; ICD-9 → ICD-10 switch (~2015) →
  dual code sets everywhere; ages ≥ 89 appear as 91.
- **Unit-of-analysis registry** (EP-34): `subject`, `hadm`, `icustay`, `edstay` (P9),
  `icu_day`, `hour_bin`, `person_time`, `note` (P10) — each with its key, time anchor and
  default index-event rule; every cohort spec, mart and model dataset declares its grain.

> **Note (2026-08-17, EP-9).** The schema contract shipped as package data under
> `src/mimicwarehouse/schema/tables/` — `mimiciv_hosp.yaml` (22 tables), `mimiciv_icu.yaml` (9),
> `mimiciv_ed.yaml` (6), `mimiciv_note.yaml` (4), `keys.yaml`, `units.yaml`,
> `column_maps/demo_2_2_to_3_1.yaml` — transcribed from the EP-8 vendored DDL and kept honest by
> `mwh schema check` (re-parses `create.sql` / `constraint.sql` at the pin; any table / column /
> order / type / nullability / PK / FK difference is a finding and exit 1). Type map as shipped:
> `INTEGER/SMALLINT/BIGINT` unchanged, `VARCHAR(n)/TEXT/CHAR(n)` → `VARCHAR`, `TIMESTAMP(n)` →
> `TIMESTAMP` (naive), `DATE`, `DOUBLE PRECISION`/`FLOAT` → `DOUBLE`, `REAL` → `FLOAT` (4-byte),
> `NUMERIC(p,s)` → `DECIMAL(p,s)`, unbounded `NUMERIC` → `DOUBLE`. **Deliberate deviations are
> recorded on the column** (`upstream_type` / `upstream_nullable`) and the drift check compares
> those against the DDL instead — three exist: `microbiologyevents.spec_type_desc` and
> `prescriptions.drug` are nullable (upstream `NOT NULL`, but the data holds zero-length strings
> that DuckDB's CSV reader loads as NULL; upstream's own `build_mimic.sh` relaxes the same two),
> and `mimiciv_ed.vitalsign.resprate` is `DOUBLE` (upstream `NUMERIC(10, 4)`, kept alongside its
> DOUBLE siblings). **Keys are metadata**: `Table.duckdb_ddl()` never emits `PRIMARY KEY` /
> `FOREIGN KEY` (an ART index over 400 M rows; upstream duplicates in `chartevents`); PKs are
> exactly `constraint.sql`'s (none for `drgcodes`, `emar_detail`, `omr`, `provider`, `caregiver`,
> `chartevents`, `ingredientevents`, nor for any ED / Note table — those carry a
> `uniqueness_hint` for EP-28/EP-44 to test), FKs are the 51 upstream ones plus 13 documented
> ED/Note ties marked `source: docs`. `Column.unit_of` is stamped from `units.yaml`. The **demo
> 2.2 → 3.1 column map is the identity** for all 37 hosp/icu/ed tables (D-27 addendum): the
> provider/caregiver tables and `*_provider_id`/`caregiver_id` columns are part of v2.2, so EP-22
> only has to validate headers (`ColumnMap.check`) — no NULL-filling or renames.

## 8. Concepts, code sets & phenotypes

- **mimic-code concepts** (EP-8, 37, 38): `concepts_duckdb/` vendored at a pinned commit
  (MIT, attributed in `NOTICE`), executed per tier into `mimiciv_derived`; count-pinning
  tests on demo/dev; local fixes recorded as patches with the upstream issue/PR reference.
  ED and Note concepts do not exist upstream and are ours.
- **Code-set registry** (EP-40): `codesets/*.yaml` (ICD-9/10 dual sets, itemid sets,
  drug-name/RxNorm sets, ATC classes) with semver + definition hash, compiled to
  `meta.codeset_members`; ICD-9→10 GEM utility.
- **Phenotype engine** (EP-41/42): `phenotypes/*.yaml` combining diagnoses, procedures,
  medications, lab thresholds, microbiology, device/ventilation events and temporal logic
  → SQL; versioned like code sets; first three: T2DM, sepsis-3 (via concept), KDIGO AKI
  stage.

> **Note (2026-08-17, EP-8).** mimic-code vendored at **`8bcbd190ca75670cd5281f9ead3611ae1cefb73e`** (upstream `main` of
> 2026-08-10, "Docker mimic iv postgres (#1757)") on 2026-08-17 under
> `src/mimicwarehouse/concepts/vendor/mimic-code/` — 144 files, upstream-relative paths, LF:
> `LICENSE`; `mimic-iv/buildmimic/postgres/{create,load,constraint,index,validate}.sql`;
> `mimic-iv/buildmimic/duckdb/build_mimic.sh` (EP-17 loader precedent: resumable progress table);
> `mimic-iv-ed/buildmimic/postgres/{create,load,index,validate}.sql`;
> `mimic-iv-note/buildmimic/postgres/{create,load}.sql`; `mimic-iv/concepts_duckdb/**/*.sql` (66,
> incl. the `duckdb.sql` driver — what EP-37 executes); `mimic-iv/concepts/**/*.sql` (65 BigQuery
> sources, reference for EP-38). The pin (`vendor/VENDOR.json`) records the sha, commit date,
> `mimic_iv_version_targeted = 3.1` (from the `validate.sql` header — upstream has no
> `mimic-iv/CHANGELOG`), ED v2.2, the DuckDB README's "1.4.x LTS (currently 1.4.5)" against our
> 1.5.5 pin, per-file `sha256_lf`, `local_edits`, `known_upstream_issues` and `excluded` (READMEs,
> notebooks, `concepts_postgres/`, `mimic-iii/`, `concept_map/*.csv` → EP-138, docker trees, with
> upstream URLs). `poe vendor-mimic-code --sha <sha>` re-vendors (no-op at the same sha; EP-9
> reuses the blobless clone left at `%TEMP%\mimic-code`). Two `local_edits`, both recorded with
> upstream and vendored hashes and line numbers: `mimic-iv/buildmimic/postgres/validate.sql`
> (kind `guard_pragma`: three expected-row-count lines carry ` -- mwh-guard: allow (row count,
> not an id)`) and — a delta from the brief — `mimic-iv/concepts/treatment/ventilation.sql` (kind
> `id_redaction`: two upstream *debugging comments* of the form `stay_id = <8 digits>` had their
> real-band tokens replaced in place by `<mwh: id redacted>`, because they *are* identifiers and
> GOVERNANCE §3 forbids committing them; the row-count pragma is reserved for `validate.sql`
> files). No ED / Note concepts exist upstream. Attribution: repo-root `NOTICE` (+ the JAMIA 2018
> citation); `docs/resources/repos.md` does not exist yet → EP-13 picks the entry up.

## 9. Cohort spec → SQL

Pydantic model / YAML (EP-46): `grain`, `inclusion`, `exclusion`, `index_event`,
`observation_window`, `washout`, `follow_up`, `era_filter`, references to code sets and
phenotypes by version. Compiler (EP-47) emits a deterministic CTE chain, one step per
criterion, materialises the cohort table under `marts/cohorts/<cohort_id>@<version>/`,
records per-step attrition counts, and writes a run record. Attrition diagram (EP-48)
renders from the attrition table (Mermaid primary, Altair fallback), disclosure-aware.

## 10. Events spine (MEDS-compatible)

EP-50 materialises a long table `(subject_id, hadm_id, stay_id, time, code, numeric_value,
text_value, source_table)` under `lake/derived/spine/` covering admissions, transfers,
diagnoses, procedures, labs, microbiology, prescriptions/emar, ICU inputs/outputs/
procedures — **excluding raw `chartevents`** in v1 (size). The column set matches MEDS
0.4 so ACES/MEDS tooling can be used as an optional validation lane; the spine is our own
build from the catalog, not the external ETL.

## 11. Run ledger, benchmark ledger, audit, snapshot ids

- `mimicwarehouse.run` (EP-35): a context manager that assigns `run_id`, captures git
  sha, params, generated SQL, code-set/phenotype versions, cohort attrition, snapshot ids,
  seeds (EP-36), environment lock hash, warnings, wall time, peak RSS, disk delta, and
  writes `runs/<run_id>/manifest.json` + `sql/`, `tables/`, `figures/`; appends one line
  to `runs/ledger.jsonl`.
- Benchmark ledger `runs/benchmarks.jsonl` (EP-19/28): staging, concept and mart builds,
  page latencies.
- Audit `runs/audit.jsonl` (EP-30): every `safe_query`, every row-view toggle, every
  export attempt.
- `runs.duckdb` (views over the JSONL, rebuilt by `mwh runs refresh`) is what the app and
  `mwh runs` read.
- Snapshot id = hash of the layer manifest; every run cites the snapshot ids it read.

> **Note (2026-08-28, EP-166 — identifier glossary + snapshot-id definition; owner decision
> 2026-08-18, D-43 item 11; ledger ARCH-5/ARCH-6/INV-3/FC-8; code EP-17/19).** The
> provenance identifiers, defined once — briefs and modules use these names and no others:
>
> - **`raw_snapshot_id`** (EP-10, shipped) — sha256 over the sorted `(rel_path, bytes,
>   sha256, rows)` tuples of **all 41** raw CSVs; `None` until all 41 are inventoried.
> - **`source_sha256`** (EP-17) — the per-file sha256 the EP-10 raw manifest recorded for
>   one source CSV (`None` on the fixture tier). Every lake `ManifestLine` carries **both**
>   `source_sha256` and `raw_snapshot_id` — the EP-17 brief's single `source_manifest_id`
>   and the D-26 addendum's "this id is the source manifest id" each meant a different one
>   of the two; the pair supersedes both wordings (D-26 addendum records the same).
> - **`build_id`** (EP-19) — one DAG-runner invocation.
> - **layer `snapshot_id`** (EP-19), one per `{core, derived, marts, notes}` × tier — a
>   **logical** id: sha256 over the sorted JSON of `(schema, table, path, rows,
>   schema_hash, source_sha256 or raw_snapshot_id, sort_keys, writer_version)` per file
>   (the EP-10 pattern above), so it is *stable when raw + contract + code are unchanged*
>   and two identical rebuilds agree. The per-file Parquet `sha256` in the manifest line is
>   **integrity-only**, never part of the snapshot id (file bytes need not be reproducible
>   under non-total sort orders). The **dev** id hashes only manifest lines whose path lies
>   in `settings.dev_buckets` plus unpartitioned tables — it must **not** move when buckets
>   5–99 finish during a full ⏱ pass.
> - **catalog `build_id` + `core_snapshot_id`** (EP-21, `meta.catalog_info`) — what the
>   catalog was built from; EP-30's audit `snapshot_id` is the queried catalog's
>   `core_snapshot_id`.
> - **`run_id` / `audit_id`** (EP-35/EP-30) and the **protocol hash** (EP-51). Every run
>   and audit line cites `snapshot_ids` — a `{layer: id}` dict from the EP-19 helpers.

## 12. Safe-query (D-31, D-32)

`mimicwarehouse.safe` (EP-30) is the choke point for anything an agent or an export can
see: `safe_query(sql, k=11)` opens the tier catalog read-only, refuses statements outside
an allow-list (no `COPY TO`, no `ATTACH`, no note tables), applies a row cap, and refuses
to return result sets that contain identifier columns or free text or any count below k
without suppression. Owner-only row viewing in the app goes through a separate,
audited `owner_rows()` path that is never reachable from the CLI used by Claude sessions.
Enforcement is layered: code (this module) + `CLAUDE.md` + repo `.claude/settings.json`
deny rules (D-39).

## 13. Protocol freeze (D-25)

`mimicwarehouse.protocol` (EP-51): pydantic `Protocol` (cohort ref, exposure, outcome,
covariates, feature windows, analysis plan, temporal holdout, claim type). `mwh protocol
freeze <yaml>` computes the content hash, appends `{hash, timestamp, git sha, path}` to
`runs/protocols.jsonl`, and tags the file; `mwh protocol run <hash>` refuses to run an
unfrozen or modified protocol; amendments append a new hash linked to the previous one.
The Freezer page (EP-128) and temporal-holdout runner (EP-129) sit on top. Every report
built from a frozen protocol states that MIMIC-IV analyses remain retrospective.

## 14. Disclosure primitives (D-33, D-40)

`mimicwarehouse.disclose` (EP-43): `suppress(df, k=11)` with complementary suppression,
`check(path)` scanning tables/figures/HTML for identifier columns, note text, small cells
and embedded data arrays, and a `.disclosure.json` sidecar writer. In-app: warn badge at
n < 11 (EP-58). On export/commit: suppress and require a passing sidecar (EP-59, EP-133).

## 15. Package / module map (planned 2026-08-16; "shipped" marks what exists — details in the workspace README § State of the workspace)

```
mimicwarehouse/                    uv project root (nested, hupsim-style)
├── pyproject.toml                 EP-1 shipped   groups: core dev ui gpu gpl text
├── src/mimicwarehouse/
│   ├── cli.py                     EP-2 shipped   `mwh` (typer) — shipped: doctor paths guard verify schema inventory fixtures canary build jobs; planned: sql demo runs protocol disclose backup app init
│   ├── console.py                 EP-167 shipped shared rich consoles + UTF-8 `mwh` entry point
│   ├── config.py                  EP-3 shipped   pydantic-settings; MWH_DATA_ROOT layout; safety checks
│   ├── guard.py                   EP-4 shipped   pre-commit data-leak guard (G1/G4 hardened EP-165)
│   ├── theme.py                   EP-5 shipped   palette, Altair/Streamlit themes
│   ├── verify.py                  EP-6 shipped   `mwh verify EP-n`; roadmap_check
│   ├── schema/                    EP-9 shipped   YAML contract loader; keys; column maps
│   ├── inventory.py               EP-10 shipped  raw manifest
│   ├── fixtures/                  EP-11/12 shipped synthetic generator
│   ├── canary.py                  EP-171 shipped write canary (synthetic write-shape rehearsal)
│   ├── paths.py                   EP-17 shipped  swap_dir (rename-aside directory publish)
│   ├── loader/                    EP-17/18 shipped (engine, csv, stage, manifest — typed CSV→Parquet, unpartitioned; buckets — partitioned stage, per-bucket sort, resume; paths — reader glob/fragment)
│   ├── dag/                       EP-19 shipped  (spec, runner, snapshot, benchmarks, jobs, cli — `mwh build` / `mwh jobs`)
│   ├── catalog/                   EP-21/29 tier catalogs, meta.*, data dictionary
│   ├── demo.py                    EP-22
│   ├── safe.py                    EP-30  safe_query, audit
│   ├── timesem.py                 EP-34  eras, relative time, dod rule, grains
│   ├── run.py                     EP-35/36 run ledger, seeds, resource log
│   ├── concepts/                  EP-8 shipped (vendor/ + pin); EP-37/38 runner + patches
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
├── app/                           P4+    Streamlit multipage "Lab" app (pages/…)
├── notebooks/                     marimo scratch (zero-output .py; import the package)
├── tests/                         pytest; tests/ep/test_epNN.py per brief; fixtures/
├── docs/                          resources/ (P1), analyses/ (capstones), site (P11)
├── DESIGN.md · GOVERNANCE.md · DECISIONS.md · DATA-DICTIONARY.md (generated, EP-29)
```

> **Note (2026-08-17, EP-2).** `cli.py` landed as the typer + rich entry point (`app`,
> shared `console`, eager `--version`, global `--data-root` → `CliState` on `ctx.obj`;
> commands attach with one `app.command()` / `app.add_typer()` line each; no duckdb /
> pandas / polars at import time — `mwh --help` ≈ 0.3 s). Its first helper module is
> **`doctor.py`** (helper of `cli.py`, EP-2): `CheckResult`, eight pure checks (`python`,
> `uv`, `duckdb`, `disk_free`, `data_root`, `bitlocker`, `gpu`, `longpaths`),
> `run_checks(data_root)`, `doctor_report()` → the JSON object EP-35 embeds in run
> manifests, and the `mwh doctor [--json]` command. Later command modules follow the same
> pattern (`paths` → `config.py` EP-3, `guard.py` EP-4, `verify.py` EP-6).

> **Note (2026-08-17, EP-3).** **`config.py`** landed: `Settings(BaseSettings)` with
> `MWH_` env · `.env` · `mwh.toml [settings]` · defaults (init kwargs first; `extra="forbid"`;
> `env_ignore_empty`; both files and any relative path are anchored at the **workspace root**
> `mimicwarehouse/`, resolved from the package location, so `uv run --project mimicwarehouse mwh …`
> from the repo root reads the same files) — `layout` (15 keys, §3), `catalog_path(tier)`,
> `duckdb_settings("build"|"app")` (string values for `duckdb.connect(config=…)`;
> `preserve_insertion_order=false` only in `build`), `source_of(field)` / `sources()`
> provenance, `get_settings()` (cached; `configure(**overrides)` installs the CLI
> `--data-root`; `get_settings.cache_clear()` in tests), `load_settings(checked=False)` for
> diagnostics. The D-29 refusals run as an `after` model validator
> (`UnsafeLocationError`, a `RuntimeError` so pydantic propagates it unwrapped) and are
> callable alone: `drive_info` (ctypes `GetDriveTypeW`/`GetVolumeInformationW`, thread error
> mode set so an empty card reader never prompts), `location_problem`, `check_local_fixed`,
> `check_same_volume`, `check_free_space` / `require_free_space` (`DiskGuardError`). **CLI
> contract:** the `mwh` callback loads settings once per invocation; every command receives
> *validated* settings (unsafe root → message + exit 2 before the command runs) except the
> diagnostic commands `doctor` and `paths`, which get the unchecked instance and *report*
> (`data_root` / `temp_dir` **fail**; `paths` prints the table and exits 2). `paths --create`
> re-runs the validators + the free-space guard before creating anything. `doctor.py` now has
> 13 checks (`settings`, `temp_dir`, `cloud_mounts`, `defender`, `power_scheme` added) and
> takes a `Settings`; `run_checks(settings)` is what EP-35 embeds. `mwh --help` import cost:
> 0.25 s of module imports (config = 0.12 s, half of it pydantic-settings → asyncio),
> 0.45 s wall direct / 0.52 s via `uv run` — still inside the ~0.5 s budget; the doctor's
> `defender` and `bitlocker` PowerShell probes cost ~2 s each at run time.

> **Note (2026-08-17, EP-4).** **`guard.py`** landed as the pre-commit data-leak guard
> (GOVERNANCE §3): pure rule helpers + `Violation(rule, path, line, detail)`; `scan(paths,
> repo_root)` (working tree, directories walked), `scan_staged(repo_root)` (paths from
> `git diff --cached --name-only --diff-filter=ACMR -z`) and `scan_tracked(repo_root, rev=None)`
> (`git ls-files` / `git ls-tree -r rev` — the per-commit primitive for the EP-163 sweep). The
> staged/tracked scans read **blobs from the index** (`git cat-file --batch-check` for sizes, one
> `--batch` for the content the rules need), so an unstaged edit can never hide a staged
> violation and the hook judges exactly what `git commit` records. Rules as in the EP-4 brief with
> four small deltas: G1 also lists `.duckdb.tmp` (the EP-21 swap suffix, already gitignored);
> G3 refuses any path with a `__marimo__` segment (not only under `notebooks/`); G4 reports at
> most 25 lines per file plus one "… and N more" row (a real CSV would otherwise flood the
> table); files under `source material/` are refused by name (G2) and **never opened**. G4 detail
> masks the token (`1*******`), names the band, and never quotes the line. `selfcheck(repo_root)`
> re-runs the EP-0 `git check-ignore` probe strings, asserts `git check-attr binary` = `set` for
> `x.csv x.parquet x.duckdb`, and checks that `.pre-commit-config.yaml` carries `mwh-guard` and
> that the hook is installed (warn-level). CLI: `mwh guard [PATHS…] [--staged] [--all-tracked]
> [--selfcheck] [--json]`, one mode per call, exit 0 / 1 / 2; `guard` joined
> `DIAGNOSTIC_COMMANDS` because it never touches the data root and a mis-set `MWH_DATA_ROOT`
> must not block commits. `.pre-commit-config.yaml` (repo root): `repo: local`/`language: system`
> `mwh-guard` (`always_run`, `pass_filenames: false`) → `ruff-check` → `ruff-format --check` →
> `pre-commit/pre-commit-hooks` **v6.0.0** (`check-added-large-files --maxkb=20000`,
> merge-conflict, yaml, toml, json, end-of-file-fixer, trailing-whitespace
> `--markdown-linebreak-ext=md`, detect-private-key). Import cost of `guard.py` is stdlib +
> typer; rich is imported inside the command.

> **Note (2026-08-28, EP-165).** Guard hardening from the 2026-08-18 retro (GOV-4/GOV-5,
> D-43): G4's `ID_TOKEN` now also matches the float rendering `NNNNNNNN.0` (pandas nullable
> BIGINT — the realistic aggregate-table leak) and `_`-bordered tokens (`\w` boundaries →
> `[A-Za-z0-9.]`, with `token_value()` stripping the `.0` tail and `mask()` masking only the
> digit part); entry **paths** are scanned with the digit-boundary `PATH_ID_TOKEN`
> (`stay_3xxxxxxx.parquet` / `stay-3xxxxxxx.png`; no pragma escape for names). G1 gained 19
> extensions (`.tsv .xlsx .xls .zip .7z .tar .tgz .tar.gz .gz .bz2 .zst .xz .sqlite .sqlite3
> .db .orc .avro .ndjson .hdf5`, mirrored in `.gitignore`/`.gitattributes`), `.tsv` joined
> `TEXT_EXTENSIONS`, and `selfcheck` verifies the `.claude/settings.json` PreToolUse hook
> registration (`pretool-hook` row) next to three new ignore probes (root-anchored
> data-directory patterns, GOV-6). `vendoring.redact_band_ids` consumes the new pattern
> unchanged via `guard.token_value`. All 430 tracked blobs stay clean under the new rules
> (one pragma added: the retro ledger's own float-form example).

> **Note (2026-08-17, EP-6).** **`verify.py`** landed with the two roadmap-driven services
> DESIGN §15 promised. `verify(ep, pytest_args)` resolves `EP-6` / `ep6` / `6`, finds
> `../roadmap/EP-<n>-*.md`, and runs `[sys.executable, -m pytest -m ep_<n> -p no:cacheprovider
> …]` in a **fresh interpreter** with cwd = the workspace root (spawn-safe on Windows); it
> returns pytest's exit code, except that a docs-only brief (header Tier `n/a`, no
> `tests/ep/test_ep<NN>.py`) prints "docs-only brief — nothing to run" and returns 0, a code
> brief without a test module returns 2, and pytest's 5 (nothing collected) becomes 2 with a
> marker hint. `roadmap_check(roadmap_dir, repo_root, strict)` parses the master tables
> (`Row`: number, title, link, size, depends, core, ☐/☑ + hashes, enclosing `## Phase …`
> heading → `charter`/`full`) and every brief (`Brief`: H1, `**Size:** … · **Blocks:** …`
> header, `> **Charter.**` paragraph → named re-plan EP) into a `Report` of `Finding(level,
> check, ep, message)` grouped by **parity** / **header** / **hashes** / **charters**; git is
> touched only through `_run_git` (`cat-file -e <hash>^{commit}`, `log -1 --format=%s`), which
> tests replace. Deltas from the brief: ☑ cells may carry one **or more** hashes (EP-0 has
> three); a charter that names an existing EP whose title is not a re-plan is a *warning*;
> the JSON report also embeds every row (+ brief tier / charter EP) so the re-plan EPs can
> reconcile without re-parsing. CLI: `mwh verify EP-n [-- <pytest args>]` (extra args reach
> pytest untouched — `--tier` arrives with EP-12), `mwh verify --list`, `mwh verify --roadmap
> [--strict] [--json]`; `verify` joined `DIAGNOSTIC_COMMANDS` (it never touches the data root,
> and a mis-set `MWH_DATA_ROOT` must not hide a roadmap check). `scripts/roadmap_check.py`
> (poe `roadmap-check`) is a thin wrapper over `roadmap_check_main`. Console output is passed
> through `_console_safe` (glyphs the active code page cannot encode become `?` — ⏱ in
> titles, ☑ in messages — instead of crashing rich on cp1252). Import cost: stdlib + typer.

> **Note (2026-08-17, EP-7 — P0 re-plan; no code).** The P0 module map is now real:
> `cli.py` (EP-2), `doctor.py` (EP-2/EP-3, 13 checks: `python uv duckdb settings disk_free
> data_root temp_dir cloud_mounts defender bitlocker power_scheme gpu longpaths`), `config.py`
> (EP-3), `guard.py` (EP-4), `theme.py` (EP-5), `verify.py` (EP-6) + `scripts/roadmap_check.py`;
> `DIAGNOSTIC_COMMANDS = {doctor, paths, guard, verify}` receive unchecked settings, every other
> command validated ones. Planned change recorded here for the P1 slot: **EP-164** adds
> `doctor.check_antivirus` (14th check, after `defender`; `root/SecurityCenter2` products via the
> same `_powershell` seam; warn when a non-Defender real-time product is present) — the JSON
> shape EP-35 embeds is otherwise unchanged. Convention notes for P1+ authors, all as shipped:
> tests are `tests/ep/test_ep<NN>.py` with the marker `ep_<n>` (zero-padded file, unpadded
> marker; `ep_0`…`ep_199` registered in `conftest.py`); `tier(name)` is a placeholder marker
> until EP-12; `mwh verify EP-n [-- <pytest args>]` runs the marker set in a fresh interpreter and
> passes extra args through; `poe check` = `lint` + `typecheck` + `test`; `poe roadmap-check
> [--strict] [--json]`; `Settings.layout[<key>]` (not `paths`) with the 15 keys of §3,
> `Settings.catalog_path(tier)`, `Settings.duckdb_settings("build"|"app")`, `get_settings()` /
> `configure()` / `load_settings(checked=False)`; the guard's `mwh-guard: allow` pragma for
> documented id examples.

> **Note (2026-08-17, EP-164 — P1 toolchain remediation).** `doctor.py` now has **14 checks**:
> `check_antivirus()` sits after `defender` in `CHECK_IDS` / `run_checks` (D-38 addenda, D-42,
> roadmap Risk 12). One non-elevated CIM query through the existing `_powershell` seam
> (`Get-CimInstance -Namespace root/SecurityCenter2 -ClassName AntiVirusProduct | Select-Object
> displayName, productState, pathToSignedProductExe | ConvertTo-Json -Compress`, 10 s timeout,
> ≈ 0.8 s on the owner's host), decoded by `_securitycenter_products()`: `productState`
> `0xAABBCC` → `enabled` = bit `0x1000` (real-time), `up_to_date` = not bit `0x10`; `value` =
> `{"products": [{name, state, enabled, up_to_date, exe}], "non_defender": [names],
> "non_defender_realtime": [names]}` — product names, states and the product's own
> `pathToSignedProductExe`, nothing else. **Status rule, as shipped:** `warn` when any product
> other than Defender is *listed* (`_is_defender` = name contains "defender"), detail spelling out
> the seven-path `D38_ALLOW_LIST` and that the product's exclusion list is unreadable
> non-elevated; `info` when Defender is the only product, nothing is listed, the query fails
> (reason in the detail: "SecurityCenter2 not available (…)", "returned no JSON", "lists no
> antivirus product") or the host is not Windows; never `fail`. The brief's draft rule keyed the
> warn on the WSC real-time bit; the first real run showed why presence is the right trigger: a
> third-party product reports "real-time on" only when it is the *registered* Security Center
> antivirus (which switches Defender off), and Malwarebytes Premium on this host is deliberately
> not registered that way — it reports `0x060000` ("off") next to Defender's `0x061100` ("on")
> while its own modules run (they quarantined `bash.exe` the day before). The bit is still
> decoded and reported per product ("real-time off per Security Center", plus a one-clause
> caveat in the warn detail). JSON shape of `mwh doctor --json` unchanged apart from the added
> check object; summary/exit-code rules unchanged (warn never fails); `mwh doctor` on the owner's
> host now ends **8 pass · 1 warn · 0 fail · 5 info**, exit 0. Elevated exclusion-list reading is
> parked (`final-roadmap.md` DOC-1). Also under this brief (optional item 6, taken as EP-7
> recommended): `tests/ep/test_ep06.py` relaxed its EP-0 hash pin from `== 3` to `>= 2` so the
> pre-convention planning commit could leave the EP-0 ☑ cell → `poe roadmap-check --strict` is
> green (Risk 14 resolved). Convention reminder for later doctor rows: probes go through `_run` /
> `_powershell`, tests fake `subprocess.run` keyed on argv[0] / the script text (an unmocked
> tool is a test failure), Windows-only checks return `info` elsewhere.

> **Note (2026-08-28, EP-167 — Retro C: CLI, settings & inventory consolidation).** The new
> API surface P2 briefs (EP-17/19/21) build on, all fixture-tier code, ledger ids in EP-167:
> **console** — `mimicwarehouse/console.py`: shared `console` / `err_console`,
> `console_safe` (moved from `verify._console_safe`; alias kept), `run()` = the UTF-8 `mwh`
> entry point (§2 note). **config** — `Settings.layout` 15 → **18** keys (`lake_fixture`,
> `lake_demo`, `lake_rejects`, §3 note); `Settings.lake_root(tier)` / `rejects_root(tier)`
> (synthetic rejects live under the synthetic lake roots) / `min_free_gb_for(tier)` (1 GB for
> `fixture`, `min_free_gb` otherwise); module-level `assert_not_credentialed_lake(tier,
> lake_root, settings)` — EP-19 calls it before any fixture/demo write; `unknown_env_keys()`
> (names of stray `MWH_*` env vars, never values — pydantic-settings ignores them silently);
> `catalog_path(tier)` unchanged for all four tiers. **cli** — validation is now lazy:
> the callback loads `load_settings(checked=False)`, stores any config error as
> `CliState.pending_error`, and the first `CliState.settings` access by a non-diagnostic
> command runs `require_safe()` / surfaces the error with exit 2 — so `--help`, `--version`
> and `no_args_is_help` always work (retro CFG-5); `DIAGNOSTIC_COMMANDS` stays the authority
> on who validates (fully lazy validation everywhere is the EP-16 decision); the callback
> prints one stderr line when `unknown_env_keys()` is non-empty. **doctor** — 15 checks:
> `deny_coverage` (after `antivirus`; warns when the data root is under no
> `.claude/settings.json` drive-letter deny prefix, retro GOV-3), `settings` warns on unknown
> `MWH_*` vars, `temp_dir` distinguishes exists / parent-exists (DuckDB creates the leaf) /
> parent-missing → warn (DuckDB 1.5.5 IOException on first spill), `longpaths` carries the
> git version. **DuckDB temp dir** — `Settings.duckdb_settings()` stays side-effect-free; the
> connection sites (`inventory.open_connection`, `fixtures.catalog.build_fixture_catalog`,
> and EP-17's `open_build_connection` when it lands) mkdir `layout["tmp_duckdb"]` first
> (retro CFG-3). **verify** — `verify(ep, …, env=…)` merges extra variables over
> `os.environ` for the pytest child; the CLI passes `MWH_DATA_ROOT=<resolved --data-root>`
> (never mutating `os.environ`); spawn/background jobs (EP-19) must pass the same env to
> their children (retro CFG-4, addendum to the EP-6 note). **inventory** —
> `rel_path_for(table)` / `RawManifest.for_table(table)` (the one manifest-key builder,
> retro INV-4); a no-op resume (`todo == []`) keeps the snapshot's job + version block
> (`versions=None` re-uses `duckdb_version`/`python_version`/`git_sha`/`mimic_code_sha`/
> `contract_hash`) while still appending a `runs` entry (retro INV-1);
> `refresh_header_status` re-evaluates stored headers against a changed contract with no
> file I/O, counted in `BuildResult.refreshed` (snapshot id unchanged — it excludes the
> header; retro INV-2); `--no-resume` is accepted (alias of the hidden `--force`);
> `render_docs`' `Generated:` line uses the snapshot's `finished` timestamp so a no-op
> `reconcile` leaves git clean. `src/mimicwarehouse/concepts/` is now a package: `__init__.py`
> exposes `vendor_info() -> VendorInfo` (pydantic, frozen: `sha`, `upstream_url`, `commit_date`,
> `vendored_on`, `mimic_iv_version`, `file_count`, `local_edits`, `root`; `.tree`, `.short_sha`),
> `vendor_manifest()` (parsed `VENDOR.json`, cached) and `vendored_path(rel)` (upstream-relative
> posix path → absolute file; `ValueError` on absolute / `..` paths, `FileNotFoundError` when not
> vendored) — all through `importlib.resources.files("mimicwarehouse.concepts") / "vendor"`, so an
> installed wheel behaves like the checkout (verified: `uv build` wheel lists all 145 vendor
> entries; hatchling ships them without an include rule and honours `.gitignore`).
> `vendoring.py` (`python -m mimicwarehouse.concepts.vendoring --sha <sha> [--src] [--dest]
> [--vendored-on] [--dry-run]`; poe `vendor-mimic-code`, outside `poe check`) reads blobs from the
> clone's **git object store at the pinned sha** (`git ls-tree` + `git cat-file --batch`), never
> the working tree, so `core.autocrlf` cannot leak in; the allow-list is `ALLOW_LIST`
> (`AllowRule(path, why, tree, suffixes, required)`), refusals are `refusal_reason()` (suffix
> list incl. bare `.gz`, NUL / non-UTF-8, only `.sql`/`.sh`/`LICENSE`), the two local-edit kinds
> are `apply_guard_pragma()` (row-count files only) and `redact_band_ids()` (everything else;
> both driven by `guard.id_band_hits`, so the guard's own regex decides), and after writing it
> runs `guard.scan()` over the vendor tree and fails on any violation. Repo-root
> `.pre-commit-config.yaml`: `end-of-file-fixer` and `trailing-whitespace` carry
> `exclude: ^mimicwarehouse/src/mimicwarehouse/concepts/vendor/` (upstream SQL has trailing
> whitespace; the fixers would otherwise rewrite it and break `sha256_lf`); `mwh guard`,
> `check-added-large-files`, `detect-private-key` still cover the tree. Tests: `tests/ep/test_ep08.py`
> (26, marker `ep_8`, fixture tier; the re-vendor no-op test skips without the clone).
> `test_ep06::test_mwh_verify_usage_errors` now uses EP-9 as its "code brief without a test module".
> EP-37 adds the concept runner and EP-38 `patches/` beside `vendor/`.

> **Note (2026-08-17, EP-9).** `src/mimicwarehouse/schema/` is now a package: `contract.py`
> (pydantic, frozen, `extra="forbid"`: `Column(name, type→duckdb_type, nullable, comment, unit_of,
> upstream_type, upstream_nullable)`, `Table(schema→schema_name, name, dataset, csv_path, columns,
> primary_key, uniqueness_hint, subject_keyed, time_column, sort_keys, partitioned, load_class,
> expected_rows_source, comment)` with validators for every brief rule (subject_keyed ⇔ has
> `subject_id`, partitioned ⇔ subject_keyed, `sort_keys[0] == subject_id`, time column is
> TIMESTAMP/DATE, key columns exist), `ForeignKey(table, columns, ref_table, ref_columns, name,
> source)`, `TableMap` / `ColumnMap.apply | missing | check | table_map`, `ValueUnitPair` /
> `FixedUnit` / `ImpliedUnit` / `UnitsSpec`, `SchemaInfo`, `Contract.table("s.t" | s, t) |
> by_schema | by_dataset | subject_keyed | dims | large | foreign_keys_of | column_map |
> duckdb_schema_ddl | content_hash`; `load_contract()` (cached, `importlib.resources`) and
> `load_contract_from(root)` for tests). `transcribe.py`: `pg_to_duckdb`, `normalise_pg_type`,
> `parse_create_sql` (line-oriented, paren-aware, comment-stripping), `parse_constraint_sql`,
> `draft_schema_yaml`, `check_tables | check_keys | check_contract → list[Drift]`. `cli.py`:
> `schema_app` (`list [--schema] [--json]`, `show <s.t> [--json]`, `ddl <s.t> | --all
> [--if-not-exists]`, `check [--json]` → exit 0/1/2, `transcribe --create-sql --schema --out`)
> attached with one `app.add_typer(schema_app, name="schema")` and **added to
> `DIAGNOSTIC_COMMANDS`** (`{doctor, paths, guard, verify, schema}`). The package `__init__`
> re-exports lazily (module `__getattr__`) so `mwh --help` does not import yaml / the models —
> measured +3 ms; wall unchanged (0.6–0.7 s here, noise-bound). Console output is
> cp1252-safe (`overflow="fold"`, ASCII-only YAML — a test enforces ASCII, single-document,
> tag-free, LF, hook-clean, guard-clean). Tests: `tests/ep/test_ep09.py` (46, marker `ep_9`,
> fixture tier; DDL executed in an in-memory DuckDB opened with `duckdb_settings("app")`; the
> "edit one type → exit 1" recipe runs in a fresh interpreter over a temp copy).
> `test_ep06::test_mwh_verify_usage_errors` now uses **EP-10** as its "code brief without a test
> module". Downstream: EP-10 reads `expected_rows_source`, EP-11/12 generate against
> `read_csv_columns()`, EP-17/18 use `csv_path` / `sort_keys` / `load_class` / `partitioned`,
> EP-21/29 apply `comment`s, EP-22 uses `column_map("demo_2_2").check`, EP-28/44 test
> `primary_key` / `uniqueness_hint` / `foreign_keys`, EP-35 cites `content_hash()`.

> **Note (2026-08-17, EP-10).** **`inventory.py`** landed as the raw-provenance module (D-26)
> and the `mwh inventory build | show | reconcile` sub-app (`inventory_app`, one
> `app.add_typer` line; **not** diagnostic — `build` writes under the data root, so it receives
> validated settings and runs `require_free_space(data_root, min_free_gb)` first). Per file:
> `inventory_file(path, table, *, rel_path, rowcount, connection, gz_sha256, known_sha256) ->
> FileRecord` (pydantic, frozen: `dataset, dataset_dir, module, schema_name, table, rel_path,
> bytes, mtime, mtime_ns, sha256, header, header_matches_contract, missing_columns,
> extra_columns, rows, rowcount_method ∈ {duckdb, duckdb_serial, skipped, failed},
> rowcount_error, csv_parallel_fallback, seconds_hash, seconds_rows, physionet_gz_sha256,
> recorded_at`; `header_status` = ok / order / mismatch) — streaming `hashlib.file_digest`,
> header from the first line only (`csv.reader`, BOM/CRLF-tolerant), rows from an in-memory
> DuckDB opened with `duckdb_settings("build")` and `SELECT count(*) FROM read_csv(?,
> header=true, all_varchar=true, delim=',', quote='"', escape='"')`, retried with
> `parallel=false` on any `duckdb.Error` (a second failure records `rows=None`, `failed`, the
> error text, and the build continues with exit 1). Manifest store: `<data_root>/lake/manifests/
> raw/<dataset-dir>.jsonl` (canonical JSON, one line per file, rewritten atomically after **every**
> file, sorted by `rel_path`; the `raw/` level is created by the module, `mimic-iv-note-…` uses
> PhysioNet's long directory name — `DATASET_DIRS` maps contract label → directory) and
> `raw_snapshot.json` (`raw_snapshot_id` = sha256 of the JSON of the sorted `(rel_path, bytes,
> sha256, rows)` tuples, `None` below 41 files; `files_expected/_done`, per-dataset totals,
> `started/finished/last_file/errors/pid/options`, a `runs` history of the last 20 builds, and
> `duckdb_version / python_version / git_sha / mimic_code_sha / contract_hash`). Public readers:
> `load_raw_manifest() -> RawManifest(records, snapshot)`, `raw_snapshot_id()`,
> `compute_snapshot_id(records)`. `build_inventory(...)` walks the contract's `csv_path`s under
> `settings.source_root`, smallest file first, sequentially; `--resume` (default) skips a file
> whose `(bytes, mtime_ns)` match its manifest line, and re-uses the hash when only the row
> count is missing (a `--no-rowcount` pass followed by a full one hashes once); `--force`
> recomputes; `--max-bytes`, `--dataset` (label or dir, repeatable), `--log <file>` (append-only,
> ASCII, timestamped, one line per file with MB/s), `--quiet`. `SHA256SUMS.txt` is parsed per
> dataset (`parse_sha256sums`, names + hashes only) into `physionet_gz_sha256` for the parked
> `.csv.gz` re-verification. Reconciliation: `parse_validate_sql` (regex over `'tbl' … <int> AS
> row_count`, case-insensitive, pragma-tolerant), `expected_counts(dataset)` (the vendored files
> named by the contract's `expected_rows_source`; MIMIC-IV 3.1 → 28 tables, ED → 6, Note → none),
> `reconcile(manifest) -> [ReconRow]` with `status ∈ {match, mismatch, no-expectation, pending}`
> (`pending` = file not inventoried / rows not counted yet — a fourth state the brief's three did
> not need to name); `mwh inventory reconcile` prints the table, writes
> `docs/resources/raw-inventory.md` (`--no-docs` / `--docs-path`; every integer thousands-
> separated, hook-clean bytes, no data) and exits 1 on any mismatch. `show [--timing] [--json]`
> is the only window a Claude session has on the job (deny rules cover the log): table +
> per-dataset totals + the `raw_snapshot.json` job lines. JSON outputs keep raw integers for
> machine consumers; every human-readable line is ASCII with thousands separators. Import cost
> ≈ 9 ms (pydantic model + csv/hashlib; duckdb / contract / vendor pin imported inside
> functions); `mwh --help` 0.50–0.55 s here (noise-bound). Tests: `tests/ep/test_ep10.py` (28,
> marker `ep_10`, fixture tier: 41 synthetic CSVs with contract headers under `tmp_path`,
> ids ≥ 90 000 000 and a sentinel cell value that must never appear in any output).
> `test_ep06::test_mwh_verify_usage_errors` now uses **EP-11** as its "code brief without a test
> module". Downstream: EP-16 verifies the full-tier job through `show`, EP-17/18/19 cite
> `raw_snapshot_id()` as the `source manifest id`, EP-20/28 call `expected_counts`.

> **Note (2026-08-18, EP-11).** `src/mimicwarehouse/fixtures/` is now a package (the `fixture`
> tier, D-18/D-27; §4 note has the layout). **`spec.py`**: `FixtureSpec` (pydantic, frozen,
> `extra="forbid"`: `seed=2026, n_subjects=120, first_subject_id/first_hadm_id/first_stay_id/
> first_event_id=90_000_000` (all `ge` the floor), `admissions_per_subject_mean=1.5`,
> `icu_fraction=0.4`, `mortality_rate=0.08` (in-hospital deaths / admissions),
> `dod_fraction=0.15`, `ed_fraction=0.5`, `labs_per_admission=40`, `outpatient_lab_fraction=0.2`,
> `n_providers=40`, `anchor_year_range=(2110, 2200)`, `anchor_age_range=(18, 88)` + `age_cap_fraction`
> (91 = the ≥ 89 label), `los_days=(1, 20)` with `los_lognormal=(ln 3.5, 0.7)`,
> `planted_per_trait=6`; a validator refuses a seed inside the real id band); `build_plan(spec)`
> → `FixturePlan(subjects, providers)` of frozen dataclasses (`SubjectPlan` → `AdmissionPlan`
> (`admittime/dischtime/died/icd_version/ed/edregtime/edouttime/admission_type/location/
> discharge_location/insurance`, the ADT `segments` chain, `icu: IcuSegment | None`, planted
> `traits ⊆ {aki, sepsis, t2dm}`), `plan.admissions`, `plan.icu_segments` (stay ids consecutive
> from `first_stay_id`), `plan.admissions_with(trait)`) from **one** `default_rng(seed)`; every
> table then draws from `table_rng(spec, name)` = `default_rng([seed, crc32(name)])`, so EP-12's
> icu generators cannot perturb hosp bytes. MIMIC caveats mirrored: ages ≥ 89 → 91, shifted
> years, `anchor_year_group` from the five real labels, deaths only on a subject's last admission
> with `dod = deathtime.date()`, otherwise `dod` within 0–365 d of the last discharge for
> `dod_fraction`, ICD-9 for the 2008–2010 / 2011–2013 groups, ICD-10 from 2017, coin flip inside
> 2014–2016. **`vocab.py`** + `vocab/*.yaml` (package data, ASCII, hook-clean; hand-typed from
> public docs): `d_labitems.yaml` (45 real itemids with unit/decimals/sampling range/ref range,
> `panels` = specimen draws, text-valued and below-detection items), `icd.yaml` (53 ICD-9 + 55
> ICD-10 diagnoses, 12 + 12 procedures, `tags` for the planted traits), `d_hcpcs.yaml` (12), `drugs.yaml` (36 drugs
> with formulary/gsn/ndc/strength/form/dose/route/frequency/proc_type/`kind` ∈ once · bolus ·
> sliding · flush · prn · infusion · fluid, `base` products, tags antibiotic/vasopressor/insulin/
> sedative), `categories.yaml` (admission types/locations by ED vs not, discharge locations,
> insurance/language/marital/race, ward + MetaVision ICU careunits, services, HCFA + APR DRGs, OMR
> result names, micro specimens/organisms/antibiotics/dilutions, poe non-med order types with
> `poe_detail` fields, emar events, lab comments with commas / quotes / embedded newlines);
> `Vocab` (frozen) + `load_vocab()` (cached) / `load_vocab_from(root)`. **`hosp.py`**: `HospContext`
> (plan + vocab + contract + cached cross-table stages `trait_times`, `orders` (poe rows incl. `D/C`
> chains, `MedOrder`s with `pharmacy_id`, administrations with per-subject `emar_seq`), `labs`,
> `micro`), `POLARS_TYPES` / `polars_schema(table)` / `to_frame(table, rows)` (typed from the
> contract, sorted by `sort_keys`, stable), one generator per table in `GENERATORS`
> (`patients admissions transfers services diagnoses_icd procedures_icd drgcodes hcpcsevents omr
> labevents microbiologyevents prescriptions pharmacy poe poe_detail emar emar_detail provider
> d_labitems d_icd_diagnoses d_icd_procedures d_hcpcs`), `build_hosp_frames(plan)`. Shapes:
> `transfers` = optional `ED` row + `admit`/`transfer` rows = the plan segments verbatim +
> `discharge` row (NULL careunit/outtime); `labevents` ≈ 40/admission in panel draws (one
> `specimen_id` per draw, `storetime ≥ charttime`, `flag='abnormal'` outside the item's ref range,
> `value` text with fixed decimals, `<0.01` below detection, `NEG` text items, 6 % comments) +
> ~16 % `hadm_id`-NULL draws outside every admission; planted signal: creatinine 0.9 → 2.1 (→ 2.6)
> within 44 h, blood culture then IV vancomycin + piperacillin-tazobactam started < 13.5 h later
> (+ norepinephrine in ICU stays), T2DM primary code + insulin + glucose 180–340; `poe_id` =
> `<subject_id>-<poe_seq>` (meds, `Lab`, `ADT orders` with `poe_detail`, consults, …, ~20 % of med
> orders discontinued by a `D/C` row), `pharmacy` one row per med order, `prescriptions` `MAIN` +
> `BASE` rows, `emar_id` = `<subject_id>-<emar_seq>` (`Administered` / `Not Given` / `Flushed` /
> `Started` / `Rate Change` / `Stopped`), `emar_detail` = summary row (`parent_field_ordinal` NULL)
> + `1.1` detail row. **`check.py`**: `validate(frames, contract, plan) -> list[str]` /
> `assert_valid` (`FixtureError`): exact contract columns + dtypes + NOT NULL, `ID_COLUMNS` ≥ the
> floor and **no integer column anywhere inside 10 000 000–39 999 999** (G4 scans every column),
> every contract FK inside hosp + `EXTRA_FKS` (emar/pharmacy/prescriptions → poe/pharmacy,
> provider ids, ICD pairs), declared PKs / uniqueness hints unique, sorted by `sort_keys`,
> `dischtime > admittime`, `deathtime` ⇔ flag, `dod` ≥ last discharge / ≥ deathtime, `anchor_age`
> never 89/90, `(subject_id, hadm_id)` pairs = admissions', every ICU segment inside its admission
> with exactly one matching `transfers` row. **`write.py`**: `frame_to_csv_bytes` (Polars
> `write_csv`: LF, header, `%Y-%m-%d %H:%M:%S` / `%Y-%m-%d`, `float_scientific=False`, quote only
> when needed), `check_bytes` (final `\n`, no blank last line, no `\r`, no trailing blanks on any
> physical line — quoted multi-line values included — and the guard's own `id_band_hits`),
> `write_fixture` (renders + checks every file **before** the first write), `render_manifest` /
> `render_readme` / `load_manifest`, `default_out_dir()` (= `workspace_root()/tests/fixtures`),
> `build_and_write`. **`cli.py`**: `fixtures_app` (`build`), attached with one `app.add_typer` and
> **added to `DIAGNOSTIC_COMMANDS`** (`{doctor, paths, guard, verify, schema, fixtures}`: it never
> touches the data root; a mis-set `MWH_DATA_ROOT` must not block regenerating fixtures). Package
> `__init__` re-exports lazily (`__getattr__`), so `mwh --help` still imports no numpy / polars /
> duckdb (a test asserts it; 0.45 s wall here). Tests: `tests/ep/test_ep11.py` (43, marker
> `ep_11`, fixture tier: drift test byte-for-byte vs `manifest.json` and the committed files, all
> 22 CSVs through DuckDB `read_csv(columns=contract types, header=true, ignore_errors=false)` with
> the manifest's row counts, era split, multi-line comments, planted signal, guard clean on the
> directory, hook-clean bytes, ≤ 6 MB, CLI). `test_ep06::test_mwh_verify_usage_errors` now probes
> **EP-12**. Downstream: EP-12 (`icu` generators from `plan.icu_segments`, `d_items`, extended
> manifest, `--tier`), EP-17 (`--source tests/fixtures/mimic-iv-3.1`), EP-37/41/42 (real itemids
> / codes / drug names), EP-22 (same writer discipline for the demo column-map check).

> **Note (2026-08-18, EP-12).** `src/mimicwarehouse/fixtures/` gained **`icu.py`**, **`catalog.py`**
> and `vocab/d_items.yaml`; `check.py`, `write.py`, `vocab.py`, `spec.py` were extended. **`vocab/
> d_items.yaml`** (ASCII; `°F` / `°C` as YAML escapes): 47 `d_items` rows typed from public docs —
> vitals (HR 220045, NBP 220179/80/81, ABP 220050/51/52, RR 220210, SpO2 220277, Temp °F 223761 /
> °C 223762), GCS (220739 / 223900 / 223901 with the MetaVision text values and scores), FiO2
> 223835, PEEP set 220339, tidal volume set 224684, ventilator mode 223849, O2 device 226732,
> admission / daily weight 226512 / 224639, height 226730, finger-stick glucose 225664, vasoactives
> (norepinephrine 221906, epinephrine 221289, phenylephrine 221749, vasopressin 222315, dopamine
> 221662), fluids (NaCl 0.9 % 225158, D5W 220949, LR 225828), propofol 222168, insulin 223258,
> urine (Foley 226559, Void 226560), procedures (invasive ventilation 225792, NIV 225794, arterial
> line 225752, CRRT 225802, intubation 224385, extubation 227194) and fixture-only `datetimeevents`
> (2401xx) / `ingredientevents` (2402xx) items — each with `linksto`, `category`, `unitname`,
> `param_type`, normal bounds and the generator knobs (`role`, `low`/`high`/`decimals`,
> `text_values`, drip `rateuom` / rate bounds); `Vocab.icu_items` (`IcuItem`), `icu_role(s)`,
> `icu_linksto`, `icu_weighted`. **`spec.py`**: `n_caregivers=15`, `vent_fraction=0.4`,
> `vasopressor_fraction=0.25` (icu-only knobs; `build_plan` ignores them, so hosp bytes are
> unchanged). **`icu.py`**: `IcuContext` (plan + vocab + contract; cached `profiles` — one
> `StayProfile` per ICU segment: weight / height, severity, ventilation / NIV / arterial-line /
> CRRT / vasopressor / propofol / insulin **windows**, foley, day + night caregiver — and `inputs`,
> the inputevents rows plus the fluid rows `ingredientevents` mirrors), one generator per table in
> `GENERATORS` (`icustays chartevents datetimeevents inputevents ingredientevents outputevents
> procedureevents caregiver d_items`), `build_icu_frames(plan)`. Shapes: `chartevents` = vitals
> hourly for 48 h then 4-hourly (ABP where an arterial line is in, NBP otherwise; a 1/400 zero
> artefact), temperature 6-hourly in one unit per stay, GCS 4-hourly (`No Response-ETT` = 0 while
> intubated), O2 device 4-hourly (`Endotracheal tube` / `Bipap mask ` / `Non-rebreather` / …), FiO2
> + PEEP + tidal volume + ventilator mode 4-hourly inside the vent window (`PSV/SBT` before
> extubation), glucose 6-hourly in half the stays, admission weight / height once + a daily weight,
> `storetime ≥ charttime`, `warning` on ≈ 4 % of out-of-range values; `inputevents` = titrated drips
> (`01-Drips`, 1–3 rate segments with new `orderid` and `linkorderid` = the first, `Main order
> parameter` + `Mixed solution` carrier rows sharing the `orderid`, `patientweight`, bag
> `totalamount`, `Changed` → `FinishedRunning`/`Stopped`/`Paused`) for norepinephrine (every planted
> sepsis stay, starting exactly when the hosp prescription starts) / other pressors / propofol under
> ventilation / insulin, maintenance crystalloids (`02-Fluids (Crystalloids)`, 75–125 mL/h) and
> boluses (`03-IV Fluid Bolus`; two in each sepsis stay); `ingredientevents` = water + sodium /
> dextrose per fluid row; `outputevents` = Foley hourly-ish (hourly and oliguric in the planted AKI
> stays) or Void; `procedureevents` = `Intubation` → `Invasive Ventilation` (minutes) → `Extubation`
> (not after an ICU death), NIV, `Arterial Line` with a location, `Dialysis - CRRT` in every planted
> AKI stay; `datetimeevents` = Foley / arterial-line insertion dates and last dialysis. **`check.py`**
> generalised (`_structural_problems(schema, …)`, contract FKs across schemas) and
> `validate(hosp, contract, plan, icu=…)` / `assert_valid(…, icu=…)` add: icu columns / dtypes /
> NOT NULL / id floor / PKs / uniqueness hints / sort keys, icu → hosp + icu → icu contract FKs,
> `caregiver_id` → caregiver, every `icustays` row inside its admission with exactly one matching
> `transfers` row and equal to the plan, `los` = window in days, every event inside `[intime − 6 h,
> outtime + 6 h]` with its stay's `(subject_id, hadm_id)`, `storetime ≥ charttime`, `endtime ≥
> starttime`, every `itemid` in `d_items` with the table's `linksto`. **`write.py`**:
> `write_fixture` accepts `{module: {table: frame}}` (or the EP-11 flat form), one manifest (`modules`
> key) + README for both modules, `build_frames(spec) -> (plan, {"hosp": …, "icu": …})`,
> `build_and_write` validates hosp + icu together. **`catalog.py`**: `build_fixture_catalog(root=None,
> *, contract, settings, comments=True) -> duckdb.DuckDBPyConnection` (in-memory, `duckdb_settings
> ("app")`, schemas `mimiciv_hosp` / `mimiciv_icu`, `CREATE TABLE … AS SELECT * FROM read_csv(…,
> columns=<contract types>, ignore_errors=false)`, `COMMENT ON` table/columns from the contract),
> `catalog_tables(con)`, `FixtureCatalogError`. **`tests/conftest.py`** (see §20 note): `--tier` /
> `PYTEST_TIER`, `tier(name)` semantics, session fixtures `tier` / `contract` / `fixture_root` /
> `fixture_catalog`, `pytest_plugins = ["pytester"]`. Tests: `tests/ep/test_ep12.py` (marker
> `ep_12`; icu drift byte-for-byte, all 9 files typed through DuckDB with the manifest row counts,
> icustays ↔ transfers, event windows, planted signal across hosp + icu, catalog of 31 tables,
> pytester tier-selection cases, guard / hooks / budgets, CLI) — the two EP-11 assertions that count
> written files now read 31 (22 hosp + 9 icu) and the drift fixture regenerates the whole tree.

> **Note (2026-08-29, EP-171).** **`canary.py`** landed: the P2 write canary
> (`run_canary(settings, *, small_files, large_mb, keep, observer)` + `mwh canary write
> [--small N] [--large-mb N] [--keep] [--json]`). It rehearses, in order and with synthetic
> bytes only (ids from 90 000 000; DuckDB `COPY` of a delta-encoded id + three `random()`
> doubles, ~40,000 rows/MB), the five write shapes P2's briefs plan against `C:\mimicdata`:
> **burst small-file pass** (N ≈ 1 MB Parquet files into Hive `subject_bucket=<n>/` dirs, the
> EP-18 staging shape), **large sequential pass** (one Parquet of `--large-mb`, default
> 2,048 MB, cap 8,192 — the EP-23/26 event-table shape; wall time and MB/s recorded per
> pass), **manifest churn** (append-per-line + `inventory._atomic_write_text` rewrites, the
> EP-19 ledger shape), **rename-aside swap** (`x.duckdb.new` → rename live aside →
> `os.replace` → remove `.old`, the §6 catalog protocol) and **cleanup** (the delete-loop
> shape of the 2026-08-17 ARW kill, D-42). After every phase it re-reads what it wrote
> (sha256 + size per file) so a silent quarantine is a hard `CanaryError`, and the tree is
> then **left in place** for the Malwarebytes triage. The canary tree is
> `layout["tmp"]/canary` (not a layout key — module-created, the EP-10 `raw` precedent);
> `require_free_space(min_free_gb + projected)` runs first; `canary` is **not** in
> `DIAGNOSTIC_COMMANDS` (it writes under the data root — the `mwh inventory` precedent) and
> is deliberately **not a doctor check**: the doctor only reports, the canary writes. Output
> is counts/bytes/seconds/MB-per-s only (G4 thousands separators; `--json` raw for machines).
> An `observer(event, path)` seam fires at every step (the tests assert the swap's on-disk
> state between renames through it). Live baseline on this machine in the EP-171 completion
> note: burst 191 MB/s, large sequential 239 MB/s (floors — DuckDB defaults, generation cost
> included), 13.3 s total, both endpoint products live, nothing killed or quarantined.

> **Note (2026-08-29, EP-19).** **`dag/`** landed: `spec.py` (pydantic `DagSpec`/`Step`,
> kinds `stage|sql|python|catalog`, per-kind field contract, acyclic-graph validation,
> packaged specs under `dag/specs/` — `stage.yaml` ships three hosp steps + a `catalog`
> stub whose handler raises `NotImplementedError("EP-21")`), `runner.py` (`run(dag, tier,
> …)`: build lock `warehouse/.build.lock` — live pid always refuses, a stale lock yields
> only to `--break-lock`; per-tier free-space guard; raw-root/bucket resolution;
> skip-when-complete via `status.json` + `--force`; per-step wall/peak-RSS sampling — and
> the `STEP_HANDLERS[kind]` registry; the stage handler completes **dims** as
> `tier_complete: "full"` / `dev_ready: true` after `stage_unpartitioned`, closing the gap
> EP-17 left), `snapshot.py` (the §11 **logical** layer snapshot id + the
> `lake/manifests/snapshots.json` history; the dev id hashes dev-bucket lines plus
> unpartitioned tables only), `benchmarks.py` (`runs/benchmarks.jsonl`, `O_APPEND`+fsync,
> one line per step + a `kind: build` summary; polars `read()`), `jobs.py` and two new
> `mwh` commands: **`mwh build --tier {fixture,demo,dev,full} [--select a,b] [--tag t]
> [--force] [--dry-run] [--background --job NAME] [--break-lock]`** (foreground runs print
> a rich summary table — step, rows, bytes, wall — and the snapshot id) and **`mwh jobs
> [--job NAME] [--tail N]`**, which lists background jobs or prints one job's state file
> (`runs/jobs/<name>.json`: job, pid, argv, started, log, state, exit_code, finished) and
> the last N lines of its log (INFO counts only — never rows). `jobs.launch` spawns a
> detached supervisor `[sys.executable, -m, mimicwarehouse.dag.jobs, --job-file, …]`
> (workspace-venv python per the EP-170 amendment, never a `uv` shim) which runs
> `[sys.executable, -m, mimicwarehouse.cli, …]` into the log and finalises the state file
> on exit — so `state`/`exit_code` are reliable for any argv, including crashes and lock
> refusals; `mwh build --job NAME` additionally merges its own outcome into the same file
> (the brief's child-side rewrite). `psutil` joined the **core** dependency group here
> (D-15 addendum, D-43 item 14). `mimicwarehouse.dag.__init__` re-exports nothing: the
> runner's import chain reaches the schema contract, which the `mwh --help` import budget
> (test_ep09) excludes.

## 16. App structure (D-21)

One Streamlit process, `127.0.0.1` only, `READ_ONLY` catalog connection cached per tier,
tier switcher (demo/dev/full; default dev), theme from `theme.py`. Pages (each its own EP):
Catalog & QC · Cohort Builder · Phenotype Studio · Explorer (linked-brush distributions,
heatmaps/correlations, cross-tabs) · Timelines (owner-gated) · Prevalence & Rates ·
Subgroups · Table 1 · Missingness · Analysis (P5) · Survival/Causal (P6) · Models (P7) ·
Protocol Freezer · Runs & Provenance · Reports · Linkage Wizard (P9) · Text (P10, search
only). Linked views: Altair selections → server-side DuckDB re-aggregation (VegaFusion for
large specs); Plotly only for lane/Gantt timelines. All charts read from
`viz/` spec builders so the same spec renders in reports. Small-cell warnings and the
row-view gate are shell-level components (EP-58). Latency target ≤ 5 s on full via marts;
pages default to dev (D-28). The `ui` dependency group is isolated because Streamlit pins
`pyarrow<25`.

## 17. Reporting pipeline (D-23)

`Report` object (EP-130): sections, tables (post-suppression), figures (Vega-Lite specs
+ PNG), methods summary, **claim-type label** (exploratory / confirmatory / predictive /
associational / causal), provenance footer (run ids, snapshot ids, protocol hash, env
hash) → Jinja2 → Markdown + self-contained HTML; PDF via Typst (EP-131). Model cards,
methods summaries and executive one-pagers are templates (EP-132). Anything leaving
`runs/` for `reports/` or git passes `disclose.check` and gets a sidecar (EP-133).

## 18. Notes segregation (D-3)

`C:\mimicdata\notes\` lake + `notes.duckdb` (DuckDB FTS) built in EP-148, attached only
by `mwh … --with-notes` in owner role, never by `safe_query`, never by the app except the
Text page's aggregate search results (counts, ids only when owner-gated). Note text never
enters run records, reports, tool output or git.

## 19. External-data landing & linkage (D-36)

`C:\mimicdata\ext\<source>\` with a `source.yaml` (license, provenance, DUA, keys) written
by the profiler (EP-137); mapping YAML for concepts/units (EP-138); key validation and
join-cardinality/coverage report (EP-139); commit into `mimiciv_ed` / `ref.*` schemas via
the DAG runner. The Linkage Wizard (EP-140/141) drives exactly this sequence; ED (EP-142)
and a reference table (EP-143) are the v1 test cases. mimic-code `concept_map/*.csv` is a
head start for itemid → LOINC/SNOMED mapping.

## 20. Testing strategy

pytest + hypothesis; `tests/ep/test_epNN.py` per brief with tier markers
(`@pytest.mark.tier("fixture")` default; `dev`; `full` opt-in); DuckDB data checks
(row-count pins, key uniqueness, referential integrity, unit plausibility) as first-class
tests; golden files only for aggregates that pass `disclose.check`; `mwh verify EP-n`
runs the EP's marker set. Never snapshot real rows into fixtures, cassettes or goldens.

> **Note (2026-08-18, EP-12).** The tier markers are real (`mimicwarehouse/tests/conftest.py`,
> documented in `tests/README.md`). **Vocabulary:** `@pytest.mark.tier("fixture" | "dev" | "full")`
> names the data tier a test needs; an unmarked test is `fixture`. `pytest --tier {fixture,dev,full}`
> (fallback: the **`PYTEST_TIER`** environment variable, then `fixture`) selects the **maximum**
> tier to run — the ladder is `fixture < dev < full`, `--tier dev` runs fixture + dev, `--tier full`
> everything. Tests above the selected tier are **deselected**; `dev` / `full` tests inside it are
> **skipped with a reason** while `get_settings().catalog_path(tier)` (`<data_root>/warehouse/
> <tier>.duckdb`, EP-21) does not exist, so a fresh checkout is never red for lack of data. The
> option is deliberately not `MWH_`-prefixed (`Settings` is `extra="forbid"` on that prefix and
> `test_ep03` asserts `.env.example` parity — a test knob is not a setting), the ladder is the
> three-step subset of `config.Tier` (`demo` is a data tier for EP-22 / screenshots, never a test
> tier) and the pytest tier never reads `settings.default_tier`. `--strict-markers` stays on: an
> unknown marker is a collection error, `tier("<other>")` a usage error. poe tasks: `test`
> (fixture; unchanged), `test-dev` = `pytest --tier dev`, `test-full` = `pytest --tier full`;
> `check` = `lint` + `typecheck` + `test` stays fixture-only; `mwh verify EP-n -- --tier dev` passes
> through EP-6's `--` unchanged. Session fixtures: `tier`, `contract`, `fixture_root`,
> `fixture_catalog` (in-memory DuckDB over the 31 fixture CSVs — the `fixture` tier's data until
> EP-21). `pytest_plugins = ["pytester"]` in `tests/conftest.py` backs the marker-selection tests
> (nested sessions over a copy of the conftest and a throw-away data root). EP-12 adds no dev/full
> test content beyond two marker-mechanics probes that open the catalog file read-only; the first
> real dev tests are EP-17's.

> **Note (2026-08-28, EP-166 — tier readiness + demo marker; decided 2026-08-18, D-43
> item 8; code EP-168).** Two refinements to the EP-12 note, words now so P2 briefs cite
> them, mechanics in EP-168: (1) **readiness is per-need, not one catalog file** — keying
> every dev/full skip on `catalog_path(tier)` would leave EP-17…EP-20's dev tests (which
> need raw data or a lake, not a catalog) skipped and their "dev green" acceptance vacuous
> (ledger FC-1/VT-1). EP-168 keeps the ladder's deselect rule but replaces the blanket skip
> with requestable readiness fixtures (`raw_root`, `dev_catalog`, `full_catalog`,
> `dev_ready(step)`, `item_tier`) and a `tier(name, needs=…)` kwarg, so each test skips on
> exactly the artefact it needs. (2) **demo tests are orthogonal opt-in** — `demo` never
> joins the ladder (it is not "between" fixture and dev): EP-168 adds
> `@pytest.mark.demo` + `--with-demo` (env `PYTEST_DEMO`), deselected unless opted in,
> skipped while the demo catalog is absent; EP-22's "extend the tier vocabulary with demo"
> is superseded by this (EP-170 amends the brief). Also a correction to the EP-12 note's
> `PYTEST_TIER` rationale (ledger CFG-1): a stray `MWH_TEST_TIER` *environment variable*
> would **not** break `Settings()` — pydantic-settings ignores env vars that match no
> field; only unknown lines in `.env`/`mwh.toml` are rejected (`extra="forbid"`). The
> non-`MWH_` prefix is still right — it keeps the test knob out of the `Settings` namespace
> and the `.env.example` parity test — and `mwh doctor` gains an unknown-`MWH_*`-vars warn
> at EP-167 (D-43 item 12).

> **Note (2026-08-28, EP-168 — mechanics shipped).** The EP-166 note above is now code
> (`tests/conftest.py`, documented in `tests/README.md`): readiness fixtures `dev_catalog` /
> `full_catalog` / `raw_root` / `dev_ready(step)` (each skips the requesting test with a
> reason naming the missing path) and `item_tier`; the marker form
> `tier(name, needs="catalog"|"raw"|"lake")` resolved in the collection hook (default
> `catalog` keeps EP-12 semantics); `@pytest.mark.demo` + `--with-demo` (env `PYTEST_DEMO`),
> deselected unless opted in, skipped while `catalog_path("demo")` is missing — `TIERS` and
> the maximum-tier deselection are unchanged. `dev_ready` reads
> `lake/manifests/status.json` with shape `{"steps": {"<step>": {"dev_ready": true}}}` —
> EP-23/24/25 write exactly that. Also shipped: `tests/helpers.py` (importable, not a
> plugin: `cli_runner()`, `tmp_data_root()`, `fresh_interpreter()`), `poe test-fast =
> pytest -n auto` (`poe test` stays serial, D-42), the tests/README churn rule ("a new EP
> must not need to edit an earlier `test_ep*.py`"), and the de-coupling of the rolling
> literals (test_ep06's verify probe now runs on a crafted roadmap; fixture-tree counts are
> read from the contract/manifest, retro VT-2/VT-3).

## 21. Open design questions (to be resolved by the named EP)

*(Heading restored 2026-08-29, EP-18: the EP-166 doc consolidation accidentally deleted
this section's heading and the first bullet's lead-in, fusing the list into the EP-168
note above; text below is the original bullet, with the endpoint-security wording as
amended since.)*

- Exact bucket count trade-off (100 buckets × ~30 tables ≈ 3 000 files) vs Defender +
  Malwarebytes/NTFS overhead (D-42; the ARW module judges write bursts) — measure in
  EP-18/28.

  > **Note (2026-08-29, EP-18 — first fixture/dev observations; full-scale measurement is
  > EP-28's).** The partitioned stage exists (loader/buckets.py) and was measured on the
  > fixture and one small real table; nothing here settles the bucket-count question yet.
  > Fixture `admissions` (186 rows, all 100 buckets): 100 partition dirs / 100 files in
  > 0.8 s via the small path, 1.3 s via the two-pass large path (sweeps=2 identical), i.e.
  > ~5 ms per file-create at burst — consistent with the EP-171 canary's 200-file burst,
  > no Defender/Malwarebytes stall observed. Real `mimiciv_hosp.admissions` (94 MB CSV,
  > `buckets=dev` → 5 partitions, 27,263 rows): 0.21 s small path, 0.29 s large path.
  > Per-bucket ~1 MB files at the dev scale are comfortably below NTFS overhead territory;
  > the 3 000-file / 40 GB-table regime (chartevents, EP-26) and the file-count vs
  > Defender measurement stay with EP-28.
- Whether `dev.duckdb` should materialise (not just view) small tables for app latency —
  EP-21/55.
- FTS engine for notes if DuckDB FTS build exceeds memory — SQLite FTS5 fallback (EP-148).
- Whether the events spine should include a chartevents subset (vitals only) — EP-50/re-plan.
- Streamlit vs marimo-app for the Freezer/Wizard pages if the rerun model bites — re-plan P4.
