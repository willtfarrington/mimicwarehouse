# mimicwarehouse — Roadmap

Master roadmap for building the local MIMIC-IV data lab described in [`../mimicwarehouse/DESIGN.md`](../mimicwarehouse/DESIGN.md), under the rules of [`../mimicwarehouse/GOVERNANCE.md`](../mimicwarehouse/GOVERNANCE.md), implementing the decisions in [`../mimicwarehouse/DECISIONS.md`](../mimicwarehouse/DECISIONS.md) (D-1 … D-41, settled with the project owner on 2026-08-16).

**Planned 2026-08-16.** v1 ("pre-employment v1") is **164 self-contained session briefs across 12 phases** (EP-0 … EP-163). The completion bar for v1 is **one tested, end-to-end representative workflow for each of the 38 capability categories** (coverage table below); every named algorithm or tool that is not built in v1 is parked in [`final-roadmap.md`](final-roadmap.md), the extension roadmap. Phases P0–P4 have full briefs now; P5–P11 have charter briefs that each phase's re-plan EP upgrades (D-9).

## How to use this roadmap

Each `EP-N-*.md` brief is **self-contained**: a session that has read only that brief plus the code (and the three design docs) can execute it. Hand one brief to one session. Execute in order (below), verify the acceptance criteria, commit, then check the box here. Depends on / Blocks list immediate technical prerequisites only; the linear order EP-0 → EP-163 supplies the rest (shared services EP-4/6/30/35/43/58/59/60 are assumed present for every later brief).

Workspace: `mimicwarehouse/` (the uv project, directly under the repository root). Commands in briefs run there (`uv run --group <group> mwh …`).

Git root: the `mimicwarehouse` repository root (`README.md`, `CLAUDE.md`, `mimicwarehouse/`, `roadmap/`, `source material/`, `.claude/`).

Data root: `MWH_DATA_ROOT` (default `C:\mimicdata`) — outside the repository, local NVMe only, never on G:/D: (D-29). Raw CSVs stay in `source material/` (D-30).

**Sizes** (D-2): S ≈ 30 min, M ≈ 1 h, L ≈ 2 h of one Claude session with the owner supervising; anything larger is split at pickup. Current mix: 21 S · 142 M · 1 L ≈ 155 h total, ≈ 145 h core (the 11 stretch briefs are the P10 text track, EP-123 and EP-145) — roughly three months at 12 h/week, four at 9 h/week. *(EP-7, 2026-08-17: EP-164 (S, core) allocated into P1 → 165 briefs, 22 S; P0's seven briefs actually took ≈ 3 h 20 min against ≈ 4 h 30 min planned — see the EP-7 retro table.)*

**Tiers** (D-18, DESIGN §4). Every brief states its tier in its header using this vocabulary: `fixture` (synthetic, committed) · `fixture+dev` · `fixture+dev+full` · `fixture+dev (full ⏱ → verified by EP-n)` (the brief launches a resumable background full-tier job and the named later brief records its timing) · `demo` (ODbL demo data) · `n/a` (docs-only). ⏱ in a title marks a brief that launches a long full-tier job; foreground shell commands are capped at ~10 min, so full-tier work is always a logged background job.

**Core / Stretch.** The `Core` column is the cutline: if time runs short, stretch briefs are dropped first (numbering gaps are fine, hupsim precedent). Re-plan EPs may move briefs across the line.

**Acceptance phrasing by brief class** (mechanically checkable, per hupsim):
- code briefs → `uv run poe test -m ep_<n>` green on fixture (+dev where stated) and `uv run mwh verify EP-<n>` green; full-tier run id + timing recorded in a completion note where the tier says so;
- ⏱ briefs → the launcher records the job id/log path; the named verifying brief records timing, peak RSS and disk in the benchmark ledger and appends a `> **Completion note (date).**` to the ⏱ brief;
- UI briefs → observable behaviour on the dev tier + one full-tier page latency recorded (≤ 5 s target, D-28) + a demo-tier screenshot where the page is showcase-worthy;
- governance briefs (EP-4, EP-30, EP-43, EP-133) → the hook/wrapper/tool **refuses** a crafted violation in a test;
- method briefs (P5–P7) → the representative workflow's full-tier run id is recorded in the completion note and its report artifact passes `mwh disclose check`;
- docs / resource / capstone / re-plan briefs → the named artifacts exist, numbers reproduce from recorded run ids, links resolve.

**Definition of done for a capability category's representative workflow** (six parts): (1) a spec/YAML or documented parameters; (2) code in the package (never only in a notebook); (3) tests on fixture + dev; (4) a recorded full-tier run id; (5) a report artifact with a claim-type label (exploratory / confirmatory / predictive / associational / causal) that passed disclosure review; (6) an app page only where the category mandates a UI (visualization, cohort/phenotype/timeline, protocol freezer, runs, linkage, reports).

**Governance rule (strict).** All data access from a Claude session goes through `mimicwarehouse.safe.safe_query` / `mwh sql`; no row-level data, identifiers or note text ever appear in tool output, tests, fixtures, docs, screenshots (except demo/fixture tiers) or git; small cells (n < 11) are warned in-app and suppressed on export; anything promoted into `docs/` or git carries a `.disclosure.json` sidecar. See `GOVERNANCE.md` and `CLAUDE.md`.

**Conventions carried over from hupsim** (use them verbatim): `> **Completion note (date).**` blocks appended to executed briefs (with benchmark tables where relevant); `> **Addendum (date, EP-n).**` under decisions; `> **EP-n pickup note.**` when a later session picks up a stale brief; `~~risk~~ **Resolved by EP-n (date)**` strike-throughs in Risks; two-hash ☑ boxes when an EP's work spans two commits; `EP-n-completion-handoff.md` / `EP-n-completion-report.md` pairs for context-limit rescues; commit pairs `feat(mimicwarehouse): … (EP-n)` then `docs(roadmap): record EP-n commit hash`; `docs(roadmap): add EP-n — …` when a brief is added mid-phase. Charter briefs (P5–P11) use `## Scope sketch (refine at re-plan)` and `## Verification / acceptance (sketch)` and carry a `> **Charter.**` note naming the re-plan EP that upgrades them. Every brief has an optional `## Parked → final-roadmap.md` section whose items are mirrored into `final-roadmap.md` at the phase re-plan.

**Re-plan EPs** close every phase (D-8): retro, timings, DECISIONS addenda, ☑ reconciliation via `roadmap_check.py`, and — from EP-74 on — writing full briefs for phase N+1 and re-chartering N+2. A per-phase optional 'toolchain remediation' S brief may be allocated at re-plan for wheel/version fights (spaCy cp314, Streamlit/pyarrow, pygam/scipy, sksurv/econml sklearn pins).

## Phase P0 — Charter, governance & toolchain (template step 1; full briefs; planned 2026-08-16)

| # | Brief | Size | Depends on | Core | Done |
|---|-------|------|-----------|------|------|
| EP-0 | [Baseline & hygiene](EP-0-baseline.md) | S | — | core | ☑ `707e9b4` + `795a044` |
| EP-1 | [Toolchain bootstrap (uv + CPython 3.13 + pyproject)](EP-1-toolchain-bootstrap.md) | M | EP-0 | core | ☑ `c232142` |
| EP-2 | [`mwh` CLI skeleton + `mwh doctor`](EP-2-mwh-cli-doctor.md) | S | EP-1 | core | ☑ `8e7a37e` |
| EP-3 | [Config & data root + safety checks](EP-3-config-data-root.md) | M | EP-2 | core | ☑ `e10416b` |
| EP-4 | [Governance enforcement: pre-commit + `mwh guard`](EP-4-guard-precommit.md) | S | EP-2 | core | ☑ `5d47ab4` |
| EP-5 | [Visual identity](EP-5-visual-identity.md) | S | EP-1 | core | ☑ `e2a664a` |
| EP-6 | [`mwh verify EP-n` + roadmap_check.py](EP-6-verify-roadmap-check.md) | S | EP-2 | core | ☑ `0d1807d` |
| EP-7 | [Re-plan P0](EP-7-replan-p0.md) | S | EP-0, EP-1, EP-2, EP-3, EP-4, EP-5, EP-6 | core | ☑ `45aa3f6` |

Ordering rationale: EP-0 first so every later commit is guarded by `.gitignore`/`.gitattributes`/the guard hook before any data code exists; EP-1 (toolchain) before EP-2..EP-6 which all need `uv run`; EP-5 (identity) sits early so every later screenshot is consistent (D-11). The owner's planning commit `cd67743` (2026-08-16: initial roadmap, design docs, governance) precedes EP-0 and is cited in EP-0's completion note; it left the EP-0 ☑ cell at EP-164 (item 6, 2026-08-17) because its subject predates the `(EP-n)` convention and was `roadmap-check --strict`'s one warning (Risk 14).

Standing decisions for phase P0: uv-managed CPython 3.13, one venv, groups core/dev/ui/gpu/gpl/text (D-15); native Windows (D-14); `mwh` CLI, pydantic-settings, poethepoet, ruff + pyright, pytest + hypothesis (defaults); data root `C:\mimicdata` outside the repo (D-29); owner performs Defender exclusion / LongPathsEnabled / power plan (D-38); guard refuses real-ID-band rows; fixture ids >= 90 000 000.

## Phase P1 — Resource inventory & external knowledge pack (template steps 2–3; full briefs; planned 2026-08-16)

| # | Brief | Size | Depends on | Core | Done |
|---|-------|------|-----------|------|------|
| EP-164 | [Toolchain remediation (P1)](EP-164-toolchain-remediation-p1.md) | S | EP-3 | core | ☑ `582bbd7` |
| EP-8 | [mimic-code vendoring](EP-8-mimic-code-vendoring.md) | S | EP-1 | core | ☐ |
| EP-9 | [Schema registry (YAML contract)](EP-9-schema-registry.md) | M | EP-8 | core | ☐ |
| EP-10 | [Raw inventory manifest ⏱](EP-10-raw-inventory.md) | M | EP-3, EP-9 | core | ☐ |
| EP-11 | [Synthetic fixture generator A (hosp)](EP-11-fixtures-hosp.md) | M | EP-9 | core | ☐ |
| EP-12 | [Synthetic fixture generator B (icu) + pytest tier markers](EP-12-fixtures-icu-markers.md) | M | EP-11 | core | ☐ |
| EP-13 | [Repos & awesome-lists inventory](EP-13-resources-repos.md) | M | — | core | ☐ |
| EP-14 | [Ontologies & vocabularies inventory](EP-14-resources-vocabularies.md) | M | — | core | ☐ |
| EP-15 | [Reading list + companion datasets + methods notes](EP-15-resources-reading-datasets.md) | M | — | core | ☐ |
| EP-16 | [Re-plan P1](EP-16-replan-p1.md) | S | EP-8, EP-9, EP-10, EP-11, EP-12, EP-13, EP-14, EP-15 | core | ☐ |

Ordering rationale: EP-8 (vendor mimic-code) precedes EP-9 (schema contract is transcribed from `create.sql`) precedes EP-10 (raw inventory reconciles against `validate.sql`); fixtures (EP-11/12) come after the schema contract they must satisfy. EP-13..15 are independent and may run in any order. **EP-164** (added 2026-08-17 at the P0 re-plan, EP-7; owner decision, Risk 12 / D-38 addendum) is the P1 toolchain-remediation slot — a `mwh doctor` `antivirus` check — and runs first so the doctor names both endpoint-security products before EP-10 hashes 98 GB of CSVs and EP-11/12 write fixture trees.

Standing decisions for phase P1: mimic-code vendored at a pinned commit with attribution (D-19); local manifest + row-count reconciliation is the raw snapshot id because plain CSVs cannot be checked against PhysioNet's `.csv.gz` checksums (D-26); free vocabularies first (D-35); resource inventories are cited markdown with per-resource license and an adopt/port/ignore verdict (D-10).

## Phase P2 — Staging: lake, DAG runner, catalog, tiers, safe-query, tracer (template step 4a; full briefs; planned 2026-08-16)

| # | Brief | Size | Depends on | Core | Done |
|---|-------|------|-----------|------|------|
| EP-17 | [Loader core A: typed CSV → Parquet](EP-17-stage-loader-core.md) | M | EP-3, EP-9 | core | ☐ |
| EP-18 | [Loader core B: subject buckets, sort, resume](EP-18-stage-loader-buckets.md) | M | EP-17 | core | ☐ |
| EP-19 | [DAG runner `mwh build`](EP-19-stage-dag-runner.md) | M | EP-18 | core | ☐ |
| EP-20 | [Stage dimensions + small hosp/icu tables](EP-20-stage-small-tables.md) | M | EP-19 | core | ☐ |
| EP-21 | [Catalog builder (per-tier .duckdb)](EP-21-stage-catalog-builder.md) | M | EP-20 | core | ☐ |
| EP-22 | [Demo tier (MIMIC-IV Demo 2.2 + ED Demo)](EP-22-stage-demo-tier.md) | M | EP-21 | core | ☐ |
| EP-23 | [Stage labevents ⏱](EP-23-stage-labevents.md) | M | EP-19 | core | ☐ |
| EP-24 | [Stage emar + emar_detail ⏱](EP-24-stage-emar.md) | M | EP-19 | core | ☐ |
| EP-25 | [Stage remaining hosp tables ⏱](EP-25-stage-hosp-rest.md) | M | EP-19 | core | ☐ |
| EP-26 | [Stage chartevents ⏱](EP-26-stage-chartevents.md) | L | EP-19 | core | ☐ |
| EP-27 | [Stage icu event tables ⏱](EP-27-stage-icu-events.md) | M | EP-19 | core | ☐ |
| EP-28 | [Verify full staging](EP-28-stage-verify-full.md) | S | EP-20, EP-21, EP-22, EP-23, EP-24, EP-25, EP-26, EP-27 | core | ☐ |
| EP-29 | [Catalog & data dictionary (meta.*)](EP-29-catalog-data-dictionary.md) | M | EP-21 | core | ☐ |
| EP-30 | [Safe-query wrapper + audit log](EP-30-safe-query-audit.md) | M | EP-21 | core | ☐ |
| EP-31 | [Tracer bullet: first-ICU-stay adults → in-hospital mortality](EP-31-tracer-bullet.md) | M | EP-21, EP-30 | core | ☐ |
| EP-32 | [Capstone #0: staging benchmark note + docs/analyses convention](EP-32-capstone-0-staging.md) | S | EP-28, EP-31 | core | ☐ |
| EP-33 | [Re-plan P2](EP-33-replan-p2.md) | S | EP-17, EP-18, EP-19, EP-20, EP-21, EP-22, EP-23, EP-24, EP-25, EP-26, EP-27, EP-28, EP-29, EP-30, EP-31, EP-32 | core | ☐ |

Ordering rationale: Loader (EP-17/18) → DAG runner (EP-19) → small tables (EP-20) → catalog (EP-21) → demo tier (EP-22) so the tracer bullet (EP-31) can run before the ⏱ chartevents pass finishes; EP-23 (labevents) is the dress rehearsal for EP-26 (chartevents); ⏱ briefs launch background jobs verified by EP-28; safe-query (EP-30) precedes the tracer because every result the tracer shows must pass through it.

Standing decisions for phase P2: DuckDB + Parquet lake canonical (D-17); Hive `subject_bucket = subject_id % 100`, dev = buckets 0–4 (D-18); explicit DuckDB memory/threads/temp config; single-writer rule with build-to-`.new`-and-swap catalogs opened READ_ONLY; audit/ledgers as append-only JSONL under `runs/`; keep >= 100 GB free; plain CSVs untouched (D-30); safe_query k = 11 (D-31, D-33).

## Phase P3 — Concepts, QC, cohort engine, provenance, protocol freeze (template step 4b; full briefs; planned 2026-08-16)

| # | Brief | Size | Depends on | Core | Done |
|---|-------|------|-----------|------|------|
| EP-34 | [Time semantics + unit-of-analysis registry](EP-34-time-semantics-grains.md) | M | EP-21 | core | ☐ |
| EP-35 | [Provenance run ledger](EP-35-run-ledger.md) | M | EP-30 | core | ☐ |
| EP-36 | [Seed/determinism policy + resource logger](EP-36-seeds-resource-log.md) | S | EP-35 | core | ☐ |
| EP-37 | [Concept runner (mimic-code concepts_duckdb → mimiciv_derived) ⏱](EP-37-concept-runner.md) | M | EP-8, EP-19, EP-22 | core | ☐ |
| EP-38 | [Concept fixes/ports for DuckDB 1.5.x](EP-38-concept-fixes.md) | M | EP-37 | core | ☐ |
| EP-39 | [Itemid dictionary curation + unit harmonization](EP-39-itemid-units.md) | M | EP-29 | core | ☐ |
| EP-40 | [Code-set registry + ICD-9→10 GEM utility](EP-40-codeset-registry-gem.md) | M | EP-21 | core | ☐ |
| EP-41 | [Phenotype engine + T2DM phenotype](EP-41-phenotype-engine-t2dm.md) | M | EP-40 | core | ☐ |
| EP-42 | [Phenotypes: sepsis-3 + KDIGO AKI stage](EP-42-phenotypes-sepsis-aki.md) | M | EP-41, EP-38 | core | ☐ |
| EP-43 | [Disclosure primitives (`disclose` module)](EP-43-disclose-primitives.md) | M | EP-30 | core | ☐ |
| EP-44 | [Data-quality profiling](EP-44-qc-profiling.md) | M | EP-29, EP-39, EP-43 | core | ☐ |
| EP-45 | [Measurement-process summaries](EP-45-measurement-process.md) | M | EP-44 | core | ☐ |
| EP-46 | [Cohort spec + registry](EP-46-cohort-spec-registry.md) | M | EP-34, EP-40 | core | ☐ |
| EP-47 | [Cohort compiler, materialization, attrition, snapshot](EP-47-cohort-compiler-attrition.md) | M | EP-46, EP-35 | core | ☐ |
| EP-48 | [Attrition diagram renderer](EP-48-attrition-diagram.md) | S | EP-47, EP-43 | core | ☐ |
| EP-49 | [Event-aligned timeline API](EP-49-timeline-api.md) | M | EP-34 | core | ☐ |
| EP-50 | [Events spine (MEDS-compatible) ⏱](EP-50-events-spine.md) | M | EP-19 | core | ☐ |
| EP-51 | [Protocol schema + freeze registry + `mwh protocol`](EP-51-protocol-freeze.md) | M | EP-35, EP-46 | core | ☐ |
| EP-52 | [Backup of non-reproducible state (`mwh backup`)](EP-52-backup-state.md) | S | EP-35, EP-51 | core | ☐ |
| EP-53 | [Capstone #1: concepts/QC case study](EP-53-capstone-1-concepts-qc.md) | M | EP-38, EP-44 | core | ☐ |
| EP-54 | [Re-plan P3](EP-54-replan-p3.md) | S | EP-34, EP-35, EP-36, EP-37, EP-38, EP-39, EP-40, EP-41, EP-42, EP-43, EP-44, EP-45, EP-46, EP-47, EP-48, EP-49, EP-50, EP-51, EP-52, EP-53 | core | ☐ |

Ordering rationale: Time semantics (EP-34) and the run ledger (EP-35/36) go first because cohorts, timelines, rates and temporal splits all depend on them; concepts (EP-37/38) need the demo tier (EP-22) for count-pinning; disclosure primitives (EP-43) precede QC (EP-44) so the first committed aggregates are already suppressed; the protocol freeze (EP-51) lands here so every P5+ workflow can be frozen; the events spine (EP-50) is ⏱ and verified at the re-plan.

Standing decisions for phase P3: adopt mimic-code concepts, port fixes, count-pin (D-19); custom DAG runner (D-20); YAML cohort/phenotype/protocol specs via pydantic (defaults); protocol freeze = content hash + registry + amendments (D-25); small cells n < 11 (D-33); MEDS-shaped spine excluding raw chartevents; unit-of-analysis registry; `dod` censoring rule and ICD-9→10 dual code sets everywhere.

## Phase P4 — Lab app wave 1: EDA & visualization (template step 5; full briefs; planned 2026-08-16)

| # | Brief | Size | Depends on | Core | Done |
|---|-------|------|-----------|------|------|
| EP-55 | [Latency marts A: first-day features + itemid rollups ⏱](EP-55-marts-first-day.md) | M | EP-38, EP-39 | core | ☐ |
| EP-56 | [Latency marts B: hourly bins + <=5 s benchmark](EP-56-marts-hourly-bench.md) | M | EP-55 | core | ☐ |
| EP-57 | [App shell A (Streamlit multipage)](EP-57-ui-app-shell.md) | M | EP-5, EP-30 | core | ☐ |
| EP-58 | [App shell B: row-view gate + app-side small-cell enforcement](EP-58-ui-row-gate-small-cells.md) | M | EP-57, EP-43 | core | ☐ |
| EP-59 | [Export primitives](EP-59-export-primitives.md) | S | EP-43, EP-35 | core | ☐ |
| EP-60 | [Screenshot tooling](EP-60-screenshot-tooling.md) | S | EP-57 | core | ☐ |
| EP-61 | [Catalog & QC browser page](EP-61-ui-catalog-qc.md) | M | EP-57, EP-44 | core | ☐ |
| EP-62 | [Cohort Builder page](EP-62-ui-cohort-builder.md) | M | EP-57, EP-48 | core | ☐ |
| EP-63 | [Phenotype Studio page](EP-63-ui-phenotype-studio.md) | M | EP-57, EP-42 | core | ☐ |
| EP-64 | [Explorer A: server-side aggregation service + VegaFusion](EP-64-ui-explorer-agg-service.md) | M | EP-57, EP-56 | core | ☐ |
| EP-65 | [Explorer B: linked-brush distributions](EP-65-ui-explorer-linked.md) | M | EP-64 | core | ☐ |
| EP-66 | [Explorer C: heatmaps, correlations, cross-tabs, conditional summaries](EP-66-ui-explorer-heatmaps.md) | M | EP-64 | core | ☐ |
| EP-67 | [Patient-safe timeline viewer](EP-67-ui-timeline-viewer.md) | M | EP-49, EP-58 | core | ☐ |
| EP-68 | [Prevalence/incidence/event-rate module](EP-68-rates-module.md) | M | EP-42, EP-34 | core | ☐ |
| EP-69 | [Prevalence/incidence page](EP-69-ui-rates-page.md) | S | EP-68, EP-57 | core | ☐ |
| EP-70 | [Descriptive stratified/subgroup module + page](EP-70-subgroups-module-page.md) | M | EP-68 | core | ☐ |
| EP-71 | [Cross-sectional EDA module + page (Table 1)](EP-71-table1-eda-module-page.md) | M | EP-47 | core | ☐ |
| EP-72 | [Missing-data views](EP-72-missingness-views.md) | M | EP-45 | core | ☐ |
| EP-73 | [Capstone #2: EDA case study + screenshots](EP-73-capstone-2-eda.md) | M | EP-57, EP-58, EP-59, EP-60, EP-61, EP-62, EP-63, EP-64, EP-65, EP-66, EP-67, EP-68, EP-69, EP-70, EP-71, EP-72 | core | ☐ |
| EP-74 | [Re-plan P4 (writes full P5, re-charters P6)](EP-74-replan-p4.md) | M | EP-73 | core | ☐ |

Ordering rationale: Latency marts (EP-55/56) precede the Explorer (EP-64+) so the <= 5 s target (D-28) is met from the first page; the shell (EP-57/58) precedes every page; export primitives (EP-59) and screenshot tooling (EP-60) precede the capstone; the timeline viewer (EP-67) is developed on fixture/demo only. Backend modules (EP-68, EP-70, EP-71, EP-72) and their pages are separate steps where the module is non-trivial.

Standing decisions for phase P4: Streamlit multipage app, one process, 127.0.0.1 (D-21); Altair/Vega-Lite + VegaFusion primary, Plotly for timelines; linked brushing essential on Explorer; owner row-view gate with audit (D-32); app-side small-cell warnings; pages default to dev tier (D-28); `ui` dependency group isolated (Streamlit pins pyarrow<25).

## Phase P5 — Outcomes, inference, regression, longitudinal (template step 7a; charter briefs; planned 2026-08-16)

| # | Brief | Size | Depends on | Core | Done |
|---|-------|------|-----------|------|------|
| EP-75 | [Endpoints A: binary/continuous/count/ordinal](EP-75-endpoints-basic.md) | M | EP-47, EP-34 | core | ☐ |
| EP-76 | [Endpoints B: time-to-event + recurrent](EP-76-endpoints-tte-recurrent.md) | M | EP-75 | core | ☐ |
| EP-77 | [Inference & group comparison](EP-77-inference-group-comparison.md) | M | EP-71 | core | ☐ |
| EP-78 | [Cluster bootstrap `boot` module](EP-78-boot-module.md) | M | EP-36 | core | ☐ |
| EP-79 | [GLM suite A: families + tidy()](EP-79-glm-families-tidy.md) | M | EP-75, EP-77 | core | ☐ |
| EP-80 | [GLM suite B: interactions, nonlinear terms, diagnostics](EP-80-glm-diagnostics.md) | M | EP-79 | core | ☐ |
| EP-81 | [Multilevel / repeated measures](EP-81-multilevel-repeated.md) | M | EP-79 | core | ☐ |
| EP-82 | [Longitudinal trajectories (+ trajectory groups)](EP-82-trajectories.md) | M | EP-49, EP-81 | core | ☐ |
| EP-83 | [Event-sequence / care-pathway analysis](EP-83-care-pathways.md) | M | EP-50 | core | ☐ |
| EP-84 | [Repeated encounters / utilization](EP-84-utilization.md) | M | EP-75 | core | ☐ |
| EP-85 | [Time-series & forecasting](EP-85-time-series-forecasting.md) | M | EP-56 | core | ☐ |
| EP-86 | [Exposure-response / treatment patterns](EP-86-exposure-response.md) | M | EP-80, EP-49 | core | ☐ |
| EP-87 | [Missing-data strategies](EP-87-missing-data-strategies.md) | M | EP-72, EP-79 | core | ☐ |
| EP-88 | [Analysis pages wave 1](EP-88-ui-analysis-pages-1.md) | M | EP-57, EP-79, EP-80 | core | ☐ |
| EP-89 | [Capstone #3](EP-89-capstone-3.md) | M | EP-75, EP-76, EP-77, EP-78, EP-79, EP-80, EP-81, EP-82, EP-83, EP-84, EP-85, EP-86, EP-87, EP-88 | core | ☐ |
| EP-90 | [Re-plan P5 (writes full P6, re-charters P7)](EP-90-replan-p5.md) | M | EP-89 | core | ☐ |

Ordering rationale: Endpoints (EP-75/76) first because every later analysis consumes them; the bootstrap module (EP-78) before GLMs; GLM families (EP-79) before diagnostics (EP-80) and multilevel (EP-81); trajectories (EP-82) after multilevel (random slopes); care pathways (EP-83) need the events spine.

Standing decisions for phase P5: statsmodels + scipy with cluster-robust SEs by subject_id by default; lifelines later; Polars primary with pandas only at library boundaries (D-17); every representative workflow picks its own clinical theme (D-5); the six-part definition of done applies (below).

## Phase P6 — Survival & causal (template step 7b; charter briefs; planned 2026-08-16)

| # | Brief | Size | Depends on | Core | Done |
|---|-------|------|-----------|------|------|
| EP-91 | [KM / Cox / Schoenfeld](EP-91-surv-km-cox.md) | M | EP-76, EP-78 | core | ☐ |
| EP-92 | [Parametric AFT, landmark, time-dependent covariates](EP-92-surv-parametric-landmark-tdc.md) | M | EP-91 | core | ☐ |
| EP-93 | [Competing risks (Aalen–Johansen; cause-specific; Fine–Gray via gpl optional)](EP-93-surv-competing-risks.md) | M | EP-91 | core | ☐ |
| EP-94 | [Recurrent events (Andersen–Gill)](EP-94-surv-recurrent-events.md) | M | EP-92 | core | ☐ |
| EP-95 | [Target-trial emulation harness](EP-95-causal-target-trial.md) | M | EP-51, EP-92 | core | ☐ |
| EP-96 | [PS / IPTW / matching / balance / standardization](EP-96-causal-ps-weighting-matching.md) | M | EP-79 | core | ☐ |
| EP-97 | [Sensitivity analyses](EP-97-causal-sensitivity.md) | M | EP-96 | core | ☐ |
| EP-98 | [Causal simulation tests (known truth)](EP-98-causal-simulation-tests.md) | M | EP-96 | core | ☐ |
| EP-99 | [Survival / causal app pages](EP-99-ui-survival-causal-pages.md) | M | EP-57, EP-93, EP-96 | core | ☐ |
| EP-100 | [Capstone #4](EP-100-capstone-4.md) | M | EP-91, EP-92, EP-93, EP-94, EP-95, EP-96, EP-97, EP-98, EP-99 | core | ☐ |
| EP-101 | [Re-plan P6 (writes full P7, re-charters P8)](EP-101-replan-p6.md) | M | EP-100 | core | ☐ |

Ordering rationale: KM/Cox (EP-91) precedes parametric/landmark/time-dependent (EP-92) and competing risks (EP-93); recurrent events (EP-94) after start–stop machinery; target-trial (EP-95) sits on the freeze registry (EP-51) and needs IPCW from survival; PS/weighting (EP-96) needs GLMs; simulation tests (EP-98) validate the causal module against known truth before any real-data claim.

Standing decisions for phase P6: lifelines + hand-rolled Aalen–Johansen and cause-specific Cox; Fine–Gray has no lifelines/scikit-survival implementation, so it is optional in EP-93 (hand-rolled IPCW/Geskus weights) and otherwise parked (R `cmprsk`); scikit-survival (GPL-3) only via the optional `gpl` group for survival-ML/IPCW metrics (D-34); hand-rolled PS toolkit with explicit diagnostics; results labelled observational/associational or causal-with-assumptions in every report.

## Phase P7 — Prediction, ML, DL — signature depth (template step 7c; charter briefs; planned 2026-08-16)

| # | Brief | Size | Depends on | Core | Done |
|---|-------|------|-----------|------|------|
| EP-102 | [Model-ready dataset A: feature spec, windows, normalization, indicators](EP-102-ml-dataset-features.md) | M | EP-55, EP-47 | core | ☐ |
| EP-103 | [Model-ready dataset B: patient-safe partitions + feature dictionary](EP-103-ml-dataset-partitions.md) | M | EP-102 | core | ☐ |
| EP-104 | [Splits (grouped/temporal by anchor_year_group), CV, nested CV](EP-104-ml-splits-cv.md) | M | EP-103 | core | ☐ |
| EP-105 | [Assessment module](EP-105-ml-assess.md) | M | EP-104, EP-78 | core | ☐ |
| EP-106 | [Model registry + model cards](EP-106-ml-registry-cards.md) | M | EP-35 | core | ☐ |
| EP-107 | [Baselines (LR / regularized / kNN / SVM)](EP-107-ml-baselines.md) | M | EP-105, EP-106 | core | ☐ |
| EP-108 | [Trees / ensembles A (DT, RF, bagging, LightGBM)](EP-108-ml-trees-ensembles.md) | M | EP-107 | core | ☐ |
| EP-109 | [Trees / ensembles B (stacking; overfitting diagnostics)](EP-109-ml-stacking-overfit.md) | M | EP-108 | core | ☐ |
| EP-110 | [Signature #1: first-24h → in-hospital mortality](EP-110-ml-signature-mortality.md) | M | EP-51, EP-108, EP-106 | core | ☐ |
| EP-111 | [Signature #2: 30-day readmission](EP-111-ml-signature-readmission.md) | M | EP-110, EP-84 | core | ☐ |
| EP-112 | [Signature #3: AKI within 7 d (time-to-event prediction)](EP-112-ml-signature-aki-tte.md) | M | EP-110, EP-93 | core | ☐ |
| EP-113 | [Nonlinear / flexible modeling](EP-113-ml-nonlinear-flexible.md) | M | EP-80 | core | ☐ |
| EP-114 | [Unsupervised A: clustering / mixtures / stability](EP-114-ml-unsupervised-clustering.md) | M | EP-103 | core | ☐ |
| EP-115 | [Unsupervised B: anomaly detection / association rules / similarity search](EP-115-ml-unsupervised-anomaly-rules.md) | M | EP-114 | core | ☐ |
| EP-116 | [Dimensionality reduction & high-dimensional analysis](EP-116-ml-dimred-highdim.md) | M | EP-103, EP-77 | core | ☐ |
| EP-117 | [Bayesian A: PyMC + nutpie models + Bambi GLMM](EP-117-bayes-pymc-glmm.md) | M | EP-81 | core | ☐ |
| EP-118 | [Bayesian B: EM / mixtures / one graphical model / likelihood + bootstrap](EP-118-bayes-em-graphical.md) | M | EP-117, EP-114 | core | ☐ |
| EP-119 | [Leakage / drift / robustness audits](EP-119-ml-leakage-drift-audits.md) | M | EP-110 | core | ☐ |
| EP-120 | [Interpretability & error analysis](EP-120-ml-interpretability-errors.md) | M | EP-110 | core | ☐ |
| EP-121 | [GPU enablement (gpu group; doctor --gpu; XGBoost-CUDA vs LightGBM-CPU)](EP-121-gpu-enablement.md) | M | EP-1, EP-108 | core | ☐ |
| EP-122 | [Tabular foundation model vs GBM](EP-122-ml-tabular-fm.md) | M | EP-121, EP-110 | core | ☐ |
| EP-123 | [Bounded sequence model (GRU/GRU-D on 48 h) (stretch)](EP-123-ml-sequence-model.md) | M | EP-121, EP-102 | stretch | ☐ |
| EP-124 | [Simulation / ablation / benchmark harness](EP-124-bench-harness.md) | M | EP-110, EP-35 | core | ☐ |
| EP-125 | [ML pages in app](EP-125-ui-ml-pages.md) | M | EP-57, EP-105, EP-120 | core | ☐ |
| EP-126 | [Capstone #5](EP-126-capstone-5.md) | M | EP-102, EP-103, EP-104, EP-105, EP-106, EP-107, EP-108, EP-109, EP-110, EP-111, EP-112, EP-113, EP-114, EP-115, EP-116, EP-117, EP-118, EP-119, EP-120, EP-121, EP-122, EP-124, EP-125 | core | ☐ |
| EP-127 | [Re-plan P7 (writes full P8, re-charters P9; notes-track go/no-go)](EP-127-replan-p7-notes-go-nogo.md) | M | EP-126 | core | ☐ |

Ordering rationale: Model-ready datasets (EP-102/103) → splits (EP-104) → assessment (EP-105) → registry/cards (EP-106) → baselines (EP-107) → trees/ensembles (EP-108/109) → the three signature workflows (EP-110–112) → audits/interpretability (EP-119/120); GPU enablement (EP-121) precedes the foundation-model (EP-122) and stretch sequence model (EP-123); the benchmark harness (EP-124) reuses signature #1. EP-127 decides the notes track go/no-go.

Standing decisions for phase P7: signature depth = prediction + assessment + leakage/drift (D-6); grouped/temporal splits by anchor_year_group; LightGBM CPU + XGBoost-CUDA comparator; SHAP tree/linear only; TabPFN-class FM, VRAM-bounded, licensed weights (D-7); CPU-first, GPU opt-in via cu130 index (D-16); model cards for every registered model.

## Phase P8 — Prospective inquiry, reporting, disclosure (template step 8; charter briefs; planned 2026-08-16)

| # | Brief | Size | Depends on | Core | Done |
|---|-------|------|-----------|------|------|
| EP-128 | [Protocol Freezer page + amendments UI](EP-128-ui-protocol-freezer.md) | M | EP-51, EP-57 | core | ☐ |
| EP-129 | [Temporal holdout runner](EP-129-temporal-holdout-runner.md) | M | EP-51, EP-104 | core | ☐ |
| EP-130 | [Report engine A: Jinja2 → MD/HTML](EP-130-report-engine-md-html.md) | M | EP-59, EP-43 | core | ☐ |
| EP-131 | [Report engine B: PDF via Typst + export finalization](EP-131-report-pdf-typst.md) | M | EP-130 | core | ☐ |
| EP-132 | [Model card + methods summary + executive summary templates](EP-132-report-templates-cards.md) | M | EP-130, EP-106 | core | ☐ |
| EP-133 | [Disclosure-review tool](EP-133-disclosure-review-tool.md) | M | EP-43, EP-130 | core | ☐ |
| EP-134 | [Runs & Provenance browser + Reports page / export gallery](EP-134-ui-runs-reports-pages.md) | M | EP-57, EP-35, EP-130 | core | ☐ |
| EP-135 | [Capstone #6 + full-tier regression](EP-135-capstone-6-full-regression.md) | M | EP-128, EP-129, EP-130, EP-131, EP-132, EP-133, EP-134 | core | ☐ |
| EP-136 | [Re-plan P8 (writes full P9, re-charters P10/P11)](EP-136-replan-p8.md) | M | EP-135 | core | ☐ |

Ordering rationale: The Freezer page (EP-128) and temporal-holdout runner (EP-129) sit on EP-51; the report engine (EP-130/131) precedes templates (EP-132) and the disclosure-review tool (EP-133); the Runs & Reports pages (EP-134) close the loop; the capstone (EP-135) also runs the full-tier regression.

Standing decisions for phase P8: Jinja2 → Markdown + HTML, PDF via Typst (D-23); claim-type labels and 'retrospective' statement in every report; exports pass `mwh disclose check` and carry a `.disclosure.json` sidecar (D-40).

## Phase P9 — Additional-data ingestion & linkage (ED as the test case) (template step 4c; charter briefs; planned 2026-08-16)

| # | Brief | Size | Depends on | Core | Done |
|---|-------|------|-----------|------|------|
| EP-137 | [Importer profiler + provenance/licensing register](EP-137-link-profiler-register.md) | M | EP-17 | core | ☐ |
| EP-138 | [Concept/unit mapping guide + mapping YAML](EP-138-link-mapping-guide.md) | M | EP-137, EP-39 | core | ☐ |
| EP-139 | [Key validation, join cardinality, linkage coverage](EP-139-link-key-validation-coverage.md) | M | EP-137 | core | ☐ |
| EP-140 | [Linkage Wizard A (profile → map)](EP-140-ui-linkage-wizard-a.md) | M | EP-57, EP-137, EP-138 | core | ☐ |
| EP-141 | [Linkage Wizard B (validate → coverage → commit)](EP-141-ui-linkage-wizard-b.md) | M | EP-140, EP-139, EP-19 | core | ☐ |
| EP-142 | [ED ingestion via wizard → mimiciv_ed + ED concepts](EP-142-link-ed-ingestion.md) | M | EP-141 | core | ☐ |
| EP-143 | [Reference-table ingestion via wizard (ATC / Elixhauser / LOINC map)](EP-143-link-reference-table.md) | M | EP-141, EP-14 | core | ☐ |
| EP-144 | [ED-enabled workflow (ED triage → admission; time-to-antibiotics)](EP-144-link-ed-workflow.md) | M | EP-142, EP-86 | core | ☐ |
| EP-145 | [Second subject-keyed PhysioNet source via wizard (stretch)](EP-145-link-second-source.md) | M | EP-141 | stretch | ☐ |
| EP-146 | [Capstone #7](EP-146-capstone-7.md) | M | EP-137, EP-138, EP-139, EP-140, EP-141, EP-142, EP-143, EP-144 | core | ☐ |
| EP-147 | [Re-plan P9 (writes full P10/P11)](EP-147-replan-p9.md) | M | EP-146 | core | ☐ |

Ordering rationale: Profiler + register (EP-137) → mapping guide (EP-138) and key validation (EP-139) → wizard pages (EP-140/141) → real ingestions: ED (EP-142) then a reference table (EP-143) → an ED-enabled workflow (EP-144) proves the linkage; a second subject-keyed source (EP-145) is stretch.

Standing decisions for phase P9: ED enters only here (D-4); wizard = profile → map → validate → coverage → commit with a license register (D-36); mimic-code `concept_map/*.csv` seeds itemid→LOINC/SNOMED mapping; ED 2.2 covers 2011–2019 (partial linkage by design).

## Phase P10 — Clinical text (optional; gated at EP-127; stretch track) (template step 7d; charter briefs; planned 2026-08-16)

| # | Brief | Size | Depends on | Core | Done |
|---|-------|------|-----------|------|------|
| EP-148 | [Notes staging ⏱ (segregated lake + notes.duckdb FTS)](EP-148-text-notes-staging.md) | M | EP-19, EP-127 | stretch | ☐ |
| EP-149 | [Note search + sectioning](EP-149-text-search-sectioning.md) | M | EP-148 | stretch | ☐ |
| EP-150 | [Concept extraction + negation/temporal context (medspaCy)](EP-150-text-concept-extraction.md) | M | EP-149 | stretch | ☐ |
| EP-151 | [Embeddings (CPU-capable, GPU-accelerated) + similarity search](EP-151-text-embeddings.md) | M | EP-149 | stretch | ☐ |
| EP-152 | [Topic discovery + classification](EP-152-text-topics-classification.md) | M | EP-151 | stretch | ☐ |
| EP-153 | [Linkage to structured events](EP-153-text-linkage-structured.md) | M | EP-150, EP-42 | stretch | ☐ |
| EP-154 | [Text pages in app (search only)](EP-154-ui-text-pages.md) | S | EP-57, EP-149 | stretch | ☐ |
| EP-155 | [Capstone #8](EP-155-capstone-8.md) | M | EP-148, EP-149, EP-150, EP-151, EP-152, EP-153, EP-154 | stretch | ☐ |
| EP-156 | [Re-plan P10](EP-156-replan-p10.md) | S | EP-155 | stretch | ☐ |

Ordering rationale: Gated by the EP-127 go/no-go; staging (EP-148, ⏱) → search/sectioning (EP-149) → extraction (EP-150) and embeddings (EP-151, CPU-capable) → topics/classification (EP-152) → linkage to structured events (EP-153) → a search-only page (EP-154). Runs after P9 so core deliverables are never blocked by the optional track.

Standing decisions for phase P10: notes segregated in their own lake + `notes.duckdb`, attached only by the owner role (D-3, GOVERNANCE §9); note text never in tool output, run records, reports or git; local models only (`MWH_ALLOW_REMOTE=false`); medspaCy + regex baseline; sentence-transformers embeddings on samples first.

## Phase P11 — Democratization, showcase, release (template step 9; charter briefs; planned 2026-08-16)

| # | Brief | Size | Depends on | Core | Done |
|---|-------|------|-----------|------|------|
| EP-157 | [Docs refresh (README/DESIGN/GOVERNANCE/DECISIONS; two reading paths)](EP-157-docs-refresh.md) | M | EP-136 | core | ☐ |
| EP-158 | [Bootstrap `mwh init` + cloner smoke test on demo tier](EP-158-bootstrap-init-cloner.md) | M | EP-22, EP-28 | core | ☐ |
| EP-159 | [Demo mode for the app](EP-159-demo-mode-app.md) | S | EP-158, EP-57 | core | ☐ |
| EP-160 | [Docs site (MkDocs Material)](EP-160-docs-site.md) | M | EP-157 | core | ☐ |
| EP-161 | [Case studies compilation (3–5)](EP-161-case-studies-compilation.md) | M | EP-157 | core | ☐ |
| EP-162 | [Executive one-pager + demo script + screenshots](EP-162-one-pager-demo-script.md) | M | EP-161, EP-60 | core | ☐ |
| EP-163 | [final-roadmap.md compilation + release v1.0.0 + final retro](EP-163-final-roadmap-release.md) | M | EP-157, EP-158, EP-159, EP-160, EP-161, EP-162 | core | ☐ |

Ordering rationale: Docs refresh (EP-157) precedes the docs site (EP-160) and case-study compilation (EP-161); bootstrap/cloner (EP-158) precedes demo mode (EP-159); the one-pager (EP-162) reuses screenshots; the release EP (EP-163) compiles `final-roadmap.md`, tags v1.0.0 and performs the full-history guard sweep before the repo goes public (D-41).

Standing decisions for phase P11: bootstrap script + docs site + demo mode (D-12); MIT license (D-34); two reading paths in docs (D-1); public at v1.0.0 after the history sweep (D-41).

## Capability coverage (38 categories → briefs)

Every category must have at least one tested end-to-end representative workflow by v1.0.0. The re-plan EPs re-audit this table.

| # | Capability category | Covering briefs |
|---|---------------------|-----------------|
| 1 | Data inventory & quality profiling | EP-10, EP-29, EP-39, EP-44 |
| 2 | Reproducible cohort construction (+ attrition diagram) | EP-46, EP-47, EP-48, EP-62 |
| 3 | Computable clinical phenotypes (versioned) | EP-40, EP-41, EP-42, EP-63 |
| 4 | Cross-sectional exploratory analysis | EP-71 (+ EP-64–66) |
| 5 | Prevalence, incidence, event-rate estimation | EP-68, EP-69 |
| 6 | Stratified and subgroup analysis | EP-70 |
| 7 | Missing-data and measurement-process analysis | EP-45, EP-72, EP-87 |
| 8 | Event-aligned timeline queries | EP-49, EP-67 |
| 9 | Longitudinal trajectory analysis | EP-82 |
| 10 | Event-sequence and care-pathway analysis | EP-83 |
| 11 | Repeated-encounter and utilization analysis | EP-84 |
| 12 | Exposure-response and treatment-pattern queries | EP-86 |
| 13 | Outcome and endpoint construction | EP-75, EP-76 |
| 14 | Statistical inference and group comparison | EP-77, EP-78 |
| 15 | Regression and generalized linear modeling | EP-79, EP-80 |
| 16 | Repeated-measures and multilevel modeling | EP-81, EP-117 |
| 17 | Time-series analysis and forecasting | EP-85 |
| 18 | Survival and event-history analysis | EP-91, EP-92, EP-93, EP-94 |
| 19 | Observational comparative-effectiveness / causal inference | EP-95, EP-96, EP-97, EP-98 |
| 20 | Supervised prediction | EP-107, EP-110, EP-111, EP-112 (signature depth, D-6) |
| 21 | Nonlinear and flexible modeling | EP-113 |
| 22 | Tree-based and ensemble learning | EP-108, EP-109 |
| 23 | Unsupervised learning and pattern discovery | EP-114, EP-115 |
| 24 | Dimensionality reduction and high-dimensional analysis | EP-116 |
| 25 | Probabilistic and Bayesian analysis | EP-117, EP-118 |
| 26 | Resource-aware neural and deep-learning experiments | EP-121, EP-122, EP-123 (stretch) |
| 27 | Clinical text analysis (separately authorized notes) | EP-148–EP-154 (optional track, gated at EP-127) |
| 28 | Model assessment and selection | EP-104, EP-105 |
| 29 | Leakage, drift, and robustness testing | EP-119 |
| 30 | Interpretability and error analysis | EP-120 |
| 31 | Simulation, ablation, and benchmarking experiments | EP-124 (+ benchmark ledger EP-19/EP-28) |
| 32 | Interactive visualization | EP-57–EP-67 (P4), EP-88, EP-99, EP-125 |
| 33 | Reproducible reporting | EP-130, EP-131, EP-132 |
| 34 | Model-ready dataset generation | EP-102, EP-103 |
| 35 | Additional-data ingestion and linkage | EP-137–EP-145 (P9) |
| 36 | Ethical and disclosure-aware analysis | EP-4, EP-30, EP-43, EP-58, EP-133 (+ GOVERNANCE.md) |
| 37 | Prospective-style inquiry over retrospective data | EP-51, EP-128, EP-129 |
| 38 | End-to-end provenance | EP-35, EP-36, EP-134 |

## Decision record

The full record is [`../mimicwarehouse/DECISIONS.md`](../mimicwarehouse/DECISIONS.md) (D-1 … D-41 + assumed defaults + judgment calls). The twelve that shape sequencing most:

1. **D-1** portfolio/employability breaks ties; both DS/ML and clinical-informatics audiences.
2. **D-2** S/M/L ≈ 30 min / 1 h / 2 h; ~160 briefs; core/stretch cutline.
3. **D-8/D-9** foundation → tracer bullet → breadth; re-plan + capstone per phase; full briefs P0–P4, charters after.
4. **D-13–D-15** Python throughout, native Windows, uv-managed CPython 3.13, one venv.
5. **D-17/D-18** DuckDB + Parquet lake canonical; tiers fixture/demo/dev/full; every brief states its tier.
6. **D-19/D-20** mimic-code concepts vendored and tested; custom `mwh build` DAG runner.
7. **D-21–D-23** Streamlit app; Altair + VegaFusion (+ Plotly timelines); marimo scratch only; Jinja2 → MD/HTML + Typst PDF.
8. **D-24/D-25** JSONL ledgers + `runs.duckdb` views; protocol freeze by content hash before run.
9. **D-29–D-33** data root outside the repo; CSVs untouched; Claude sessions aggregate-only via safe_query; owner row view in-app only; small cells n < 11.
10. **D-34/D-35** MIT + permissive deps (`gpl` extra); free vocabularies first.
11. **D-3/D-4** notes = optional late track (gated EP-127); ED via the Linkage Wizard.
12. **D-38–D-41** owner Windows tuning; `.claude/settings.json` deny rules; gated aggregates with disclosure sidecars; MIT now, public at v1.0.0 after a history sweep.

## Judgment calls made during planning (owner saw these at plan approval)

- Streamlit over marimo-as-app (the research panel's first pick) — owner choice for recognition and conventional multipage/wizard shape; a marimo app lane is parked.
- Notes staged late (P10) rather than early — owner choice; the P7 re-plan may pull staging forward.
- Plain CSVs kept (not re-gzipped/deleted) — owner choice; `.csv.gz` re-download for checksum-verifiable raw is parked.
- `subject_id % 100` buckets, dev = 0–4, fixture ids ≥ 90 000 000; numbering = planned execution order; `.gitattributes` and `.claude/settings.json` written in the planning session.

## Risks / open items

1. Plain CSVs cannot be verified against PhysioNet's `SHA256SUMS.txt` (covers `.csv.gz` only) → EP-10 local manifest is the raw snapshot id; `.csv.gz` re-download parked in `final-roadmap.md`.
2. `concepts_duckdb` lags `concepts/` upstream (open regeneration PR; open concept-logic PRs: SIRS wbc guard, lab valueuom, Charlson, APS-III) and its README targets DuckDB 1.4 LTS → EP-37/38 pin, count-pin and port; no ED/Note concepts exist upstream (ours).
3. Resolver traps: ~~Streamlit `pyarrow<25` (→ `ui` group, EP-1/EP-57)~~ **Resolved by EP-1 (2026-08-17)** — one pyarrow 24.0.0 serves core and `ui`, `[tool.uv] conflicts` (ui↔gpu, ui↔text) is in the lock for the day they diverge; pygam `scipy<1.17` (→ statsmodels GAM); scikit-survival pins `sklearn==1.9` and is GPL-3 (→ `gpl` group); econml `<1.10`; pytensor `numba<=0.66`; pandas 3.0 str dtype/CoW/µs datetimes → EP-1 smoke test. EP-1 (2026-08-17): lock resolved with **one** pyarrow (24.0.0) for core and `ui` (25.0.1 exists; uv unified the forks), pandas 3.0.5 `str`/`datetime64[us]` round-trip DuckDB↔Polars↔pandas and statsmodels `C(g)` on `str` dtype pass; the only sdist-only dependency is `autograd-gamma` (pure Python, lifelines transitive) — `--no-build` checks pass with `--no-install-package autograd-gamma`; allow-listed in `test_ep01`, decision for EP-7. **EP-7 (2026-08-17): allow-list kept** (D-15 addendum) — the sdist builds in < 1 s with no compiler and lifelines stays core; any P1+ brief that adds a dependency must keep `test_ep01::test_uv_lock_every_package_has_a_wheel_for_this_interpreter` green (extend the allow-list only for pure-Python sdists, and say so in its completion note).
4. Windows: ~~MAX_PATH (owner enables LongPathsEnabled; short data root)~~ **Resolved by EP-0/EP-2 (2026-08-17)** — `LongPathsEnabled=1`, repo-local `core.longpaths=true`, `mwh doctor` `longpaths` re-checks both, data root is `C:\mimicdata`; `spawn` multiprocessing (`__main__` guards; no module-level connections/GPU init); uv cache and `.venv` on the same volume (EP-1: both on C:); ~~CRLF (`.gitattributes`)~~ **Resolved by EP-0 (2026-08-17)** — `git add --renormalize` was a no-op (all tracked files LF), `.gitattributes` marks data extensions binary, `mwh guard --selfcheck` re-probes it; Defender exclusion for the data root only (owner) — **and Malwarebytes, see Risk 12**; foreground shell cap ~10 min → background jobs with logs; laptop thermal throttling → log clocks.
5. DuckDB defaults (memory 80 % ≈ 51 GB, threads 16, temp beside the DB, in-memory connections have no temp dir → hard OOM, `max_temp_directory_size` 90 % of free disk) must be overridden in every build; single writer; never on G:/D:; `start_ui` is not read-only (never use); VSS persistence experimental.
6. Disk: 98 GB CSV + ~20 GB lake + 15–30 GB derived/spine + 5–15 GB notes/embeddings + models + 60–100 GB build-temp peak + ~15 GB uv/venv + hiberfil/pagefile → never below 100 GB free (`mwh doctor`/`mwh build` refuse).
7. GPU: PyPI torch is CPU-only on Windows → cu130 index `explicit=true`; cu126 lacks sm_120; no Triton/torch.compile on Windows; JAX has no Windows CUDA; assert `torch.cuda.get_device_capability() == (12, 0)` at EP-121; 8 GB VRAM caps batches and local LLMs (≤ 8B Q4).
8. Governance: Claude Code ships tool results to Anthropic → k-suppressed aggregates only, never note text (GOVERNANCE §4); git history is permanent → guard/gitignore/gitattributes before any data code (EP-0/EP-4); MIMIC-IV-Note is a separate DUA and the highest-risk asset; owner supplies CITI + DUA acceptance dates for GOVERNANCE §1 and checks the claude.ai training toggle; suspected PHI → PhysioNet only. EP-0 (2026-08-17) verified the `.claude/settings.json` deny rules refuse a synthetic `*.csv` inside the repo (Read/Bash/PowerShell) but `Read(**/*.csv)` is project-relative and did not fire on the same probe under `%TEMP%`; `C:\mimicdata` relies on the explicit `//C:/mimicdata/**` rules — the EP-4 guard and EP-30 safe-query remain necessary layers.
9. Data caveats baked into every relevant brief: per-patient date shift (no cross-patient calendar analyses; `anchor_year_group` is the only temporal axis), `dod` ~1 year post-discharge, ICD-9→10 switch (~2015), discharge-alive as a competing event, ages ≥ 89 shown as 91, ED 2.2 covers 2011–2019, the Demo is v2.2 schema with no note demo (synthetic notes for text tests).
10. Notes track gated at EP-127; ED-dependent analyses only after EP-142; MEDS/ACES is an optional validation lane over the EP-50 spine; OMOP/FHIR are extension items.
11. Time budget: ≈ 155 h for 164 briefs; if the owner's cadence is below ~10 h/week, the P7 re-plan should move P10 and further stretch items across the cutline rather than compress briefs.
12. **Endpoint security is two products, not one** (found 2026-08-17 at the end of EP-6; D-38 addendum). Besides Defender, **Malwarebytes 5.1 Premium** runs real-time protection on the host with its own allow list — Defender exclusions do not apply to it. Its Ransomware Protection (ARW) module judges *processes* by I/O pattern and killed + quarantined the unsigned `C:\Program Files\Git\usr\bin\bash.exe` (Claude Code's Bash tool) during a burst `cp -r` / `sed -i` / `rm -rf` in the session scratchpad; the same heuristic would hit `python.exe` (uv's managed CPython, also unsigned) writing thousands of Parquet files at EP-17+ full tier, and `uv.exe` (unsigned) on `uv sync`. Separately, Defender flagged three Claude heredoc command lines (`bash -c … cat > roadmap/EP-1… <<'EOF'`) as `Trojan:Win32/ClickFix.FFQ!MTB` on 2026-08-16 (process killed, file untouched) — large file writes go through the Write/Edit tools, not shell heredocs. Owner mitigations (all 2026-08-17): bash.exe restored + allow-listed; Malwarebytes full exclusions for seven paths — `C:\Program Files\Git`, `%APPDATA%\uv\python`, the workspace `.venv`, `C:\mimicdata`, `source material/`, `%LOCALAPPDATA%\Microsoft\WinGet\Packages\astral-sh.uv_*` (uv.exe), `%USERPROFILE%\.cache\pre-commit` (hook venvs); Ransomware Protection stays on; usage/threat statistics + sample submission confirmed off. Neither product's exclusions are readable non-elevated (`mwh doctor` reports both on the owner's word); Malwarebytes uploads *detected executables* to its cloud, so the data-root / source-material exclusions are also a GOVERNANCE §2 control, not only a performance one. Any future "process killed / binary vanished / access denied mid-command" symptom → check `C:\ProgramData\Malwarebytes\MBAMService\logs\mbamservice.log` and Malwarebytes › Detection History › Quarantined items before suspecting Defender. No EP-0…EP-6 deliverable was affected. Owner decision: EP-7 **allocates** the P0 toolchain-remediation slot as `EP-164-toolchain-remediation-p1.md` (S) — a `mwh doctor` `antivirus` check listing `root/SecurityCenter2` products and warning when a non-Defender real-time product is present. → **EP-164 done (2026-08-17)**: `mwh doctor` `antivirus` (14th check) names every product Security Center lists and **warns on the presence** of any non-Defender product, spelling out the seven D-38 paths — presence rather than the WSC real-time bit, because Malwarebytes reports `productState 0x060000` ("real-time off": it is not the *registered* Security Center antivirus, so Defender stays on) while its own modules run; on this host the row reads "Malwarebytes (real-time off per Security Center) · Windows Defender (real-time on)" → warn, exit 0, probe 0.8 s. The exclusion lists themselves stay unreadable non-elevated (owner's word; elevated mode parked in `final-roadmap.md`).
13. **Console code page (cp1252) vs rich / Unicode output** (found at EP-3 and EP-6). The Bash tool and some PowerShell hosts run `mwh` under code page 1252, where `≥`, `☑`, `⏱`, `·`, `—` raise `UnicodeEncodeError` inside rich (`mwh paths` crashed on `≥` at EP-3; `verify.py` now routes console strings through `_console_safe`, which turns unencodable glyphs into `?`). Every CLI table or log line added by P1+ (inventory tables, fixture-generator progress, build logs) must either stay ASCII or pass through the same helper; the JSON outputs are unaffected. Owner option: `[console]::OutputEncoding = [Text.Encoding]::UTF8` / `PYTHONIOENCODING=utf-8` in the profile — not assumed by any brief.
14. ~~**`uv run poe roadmap-check --strict` is red by one accepted warning** (EP-7, 2026-08-17): the owner's planning commit `cd67743` sits in the EP-0 ☑ cell (as EP-0's completion note records) but has no `(EP-0)` in its subject; `tests/ep/test_ep06.py` pins the EP-0 cell to three hashes, and a re-plan writes no code, so EP-7 kept the cell and the warning (non-strict exits 0; the acceptance line "strict exits 0" is unmet by exactly this). Fix path handed to EP-164 (optional item 6): relax the pin to `>= 2`, drop `cd67743` from the cell, keep it in prose. Until then, treat "0 errors, 1 warning (EP-0)" as the green state of `roadmap-check --strict`.~~ **Resolved by EP-164 (2026-08-17, optional item 6 taken as EP-7 recommended)** — `test_ep06` pin relaxed to `>= 2`, EP-0 cell now `☑ 707e9b4 + 795a044`, `cd67743` cited in prose under the P0 table and in EP-0's completion note; `roadmap-check --strict` exits 0 (0 errors, 0 warnings).

## Provenance & disclosure discipline (applies to every brief)

Every run records git sha, `uv.lock` hash, DuckDB version, snapshot ids of every layer read, generated SQL, parameters, code-set/phenotype/protocol versions and hashes, cohort attrition, seeds, warnings, wall time, peak RSS, disk delta and tier (EP-35). Derived layers carry manifests whose hash is their snapshot id. Reports and case studies cite run ids and reproduce from them. Any aggregate that leaves the data root passes `mwh disclose check` (n < 11 suppressed, no identifiers, no free text) and gets a `.disclosure.json` sidecar. Briefs never re-implement suppression, audit or provenance — they call the modules built in EP-30, EP-35 and EP-43.
