# mimicwarehouse — DESIGN

Architecture of the local MIMIC-IV data lab. This document is the "why and how"; the
roadmap (`../roadmap/README.md`) is the "when". Every module below is marked with the
EP brief that builds it — as of 2026-08-16 nothing here exists as code yet. Sessions
update this file when an EP changes a design fact (append a dated note; do not rewrite
history).

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

## 5. Lake physical layout

Hive-partitioned Parquet: `lake/core/<schema>/<table>/subject_bucket=NN/part-*.parquet`,
sorted `(subject_id, <time column>)`, ZSTD level 3, ~1 M-row row groups, statistics on.
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

## 15. Package / module map (all planned)

```
mimicwarehouse/                    uv project root (nested, hupsim-style)
├── pyproject.toml                 EP-1   groups: core dev ui gpu gpl text
├── src/mimicwarehouse/
│   ├── cli.py                     EP-2   `mwh` (typer): doctor paths build sql verify demo runs protocol disclose backup app init
│   ├── config.py                  EP-3   pydantic-settings; MWH_DATA_ROOT layout; safety checks
│   ├── guard.py                   EP-4   pre-commit data-leak guard
│   ├── theme.py                   EP-5   palette, Altair/Streamlit themes
│   ├── verify.py                  EP-6   `mwh verify EP-n`; roadmap_check
│   ├── schema/                    EP-9   YAML contract loader; keys; column maps
│   ├── inventory.py               EP-10  raw manifest
│   ├── fixtures/                  EP-11/12 synthetic generator
│   ├── loader/                    EP-17/18 CSV→Parquet, buckets, resume
│   ├── dag/                       EP-19  `mwh build` runner, manifests, snapshot ids
│   ├── catalog/                   EP-21/29 tier catalogs, meta.*, data dictionary
│   ├── demo.py                    EP-22
│   ├── safe.py                    EP-30  safe_query, audit
│   ├── timesem.py                 EP-34  eras, relative time, dod rule, grains
│   ├── run.py                     EP-35/36 run ledger, seeds, resource log
│   ├── concepts/                  EP-37/38 vendored mimic-code + patches
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

> **Note (2026-08-17, EP-8).** `src/mimicwarehouse/concepts/` is now a package: `__init__.py`
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

## 21. Open design questions (to be resolved by the named EP)

- Exact bucket count trade-off (100 buckets × ~30 tables ≈ 3 000 files) vs Defender/NTFS
  overhead — measure in EP-18/28.
- Whether `dev.duckdb` should materialise (not just view) small tables for app latency —
  EP-21/55.
- FTS engine for notes if DuckDB FTS build exceeds memory — SQLite FTS5 fallback (EP-148).
- Whether the events spine should include a chartevents subset (vitals only) — EP-50/re-plan.
- Streamlit vs marimo-app for the Freezer/Wizard pages if the rerun model bites — re-plan P4.
