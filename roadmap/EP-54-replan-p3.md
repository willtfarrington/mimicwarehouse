# EP-54 — Re-plan P3

**Size:** S · **Tier:** n/a · **Core/Stretch:** core · **Depends on:** EP-34 (Time semantics + unit-of-analysis registry), EP-35 (Provenance run ledger), EP-36 (Seed/determinism policy + resource logger), EP-37 (Concept runner (mimic-code concepts_duckdb → mimiciv_derived) ⏱), EP-38 (Concept fixes/ports for DuckDB 1.5.x), EP-39 (Itemid dictionary curation + unit harmonization), EP-40 (Code-set registry + ICD-9→10 GEM utility), EP-41 (Phenotype engine + T2DM phenotype), EP-42 (Phenotypes: sepsis-3 + KDIGO AKI stage), EP-43 (Disclosure primitives (`disclose` module)), EP-44 (Data-quality profiling), EP-45 (Measurement-process summaries), EP-172 (GEM review adjudication: t2dm + sepsis_explicit (owner-supervised)), EP-46 (Cohort spec + registry), EP-47 (Cohort compiler, materialization, attrition, snapshot), EP-48 (Attrition diagram renderer), EP-49 (Event-aligned timeline API), EP-50 (Events spine (MEDS-compatible) ⏱), EP-51 (Protocol schema + freeze registry + `mwh protocol`), EP-52 (Backup of non-reproducible state (`mwh backup`)), EP-53 (Capstone #1: concepts/QC case study) · **Blocks:** —

> **EP-33 amendment (2026-09-01).** Header facts unchanged. (1) Item 5's command is
> `uv run poe roadmap-check --strict` (= `mwh verify --roadmap --strict`; retro FC-20, README
> notation table) — there is no `python roadmap_check.py` at the workspace root; `--strict`
> exits 0 since EP-164. (2) **Retro convention inherited from EP-33**: `roadmap/retro-p3.md`
> (planned-vs-actual, timings, surprises) plus — if P3 runs a discovery audit — a findings
> ledger `roadmap/retro-p3-findings.md` in the `retro-p2-findings.md` shape (verified index
> with a triage outcome per row, carried-low index, per-finding evidence limited to id / area /
> file:line / class / severity / evidence / fix direction, no reproduction recipes, id bands
> written `1xxxxxxx`-style). (3) **Re-examine the EP-33 carried-low findings** parked in
> `final-roadmap.md` § Cross-cutting (the `next-replan` rows of `retro-p2-findings.md`'s
> carried-low index — e.g. DAG-5/6/8/9, DKB-3..7, LDR-7..9, LGR-6, SGT-4..6, CTR-5..7,
> CLI-5/8 — and the `next-replan` verified rows: CTR-1 closed by EP-41's regeneration, DAG-3/
> DAG-4 landed at EP-33, SGT-3's arithmetic half = DIS-3, P3C-* as amended into the P3 briefs):
> each gets fix-now / allocate (EP-172+ S brief) / park / reject with reason. (4) **Replace the
> D3 estimates with measurements**: record the real full-tier sizes of the derived layer
> (`lake/derived/full/` concepts + phenotypes), `lake/derived/full/spine/` and `lake/marts/
> full/`, and the `layout["tmp_duckdb"]` high-water mark observed during the concept/spine
> jobs, against EP-33 D3's re-estimates (spine 2.5–4 GB; DESIGN §3 note) and Risk 6; decide the
> chartevents vitals-subset question for the spine (DESIGN §21; EP-50 amendment). (5) Ledger
> pulls use `mwh runs benchmarks --kind mart|concept|query --format md` (the EP-32 verb);
> `runs.*` reads through `mwh sql` need a count-family column (not a registry exemption).
> (6) Header/table reconciliation: EP-33 changed the Depends-on of EP-37 (+EP-34, EP-35), EP-49
> (+EP-30) and EP-50 (+EP-34) — confirm the README rows and the Blocks lists still match before
> the ☑ pass.

## Context

Every phase closes with a re-plan (D-8): retro, timings, decision addenda, ☑ reconciliation and
amendments to the next phase's briefs in the light of what was actually built. P3 leaves one
open ⏱ job — the full-tier events spine launched by EP-50 — which this brief verifies. P4 (EP-55 …
EP-74, full briefs written at planning time) depends heavily on P3's concrete APIs: the per-tier
derived/marts layout (EP-37/47), `timesem` and `timeline` signatures (EP-34/49), `disclose`
(EP-43), `run.py` (EP-35/36), the phenotype/cohort/protocol registries and CLI groups
(`mwh codeset|phenotype|cohort|units|qc`) added beyond DESIGN §15's original list. This brief
records those facts as DECISIONS addenda / DESIGN dated notes and amends the P4 briefs so a P4
session never has to guess. Docs-only (`n/a` tier): the only data touched is aggregate row counts
of the spine through `mwh sql`. Full briefs for P5 are written by EP-74, not here (D-9).

## In scope

1. **Verify the EP-50 full spine** — `uv run --group dev mwh jobs --job spine-full --tail 40`
   (state + INFO lines only; log `%MWH_DATA_ROOT%\runs\jobs\spine-full.log`), confirm per-source
   manifests and `meta.spine_codes` on `full.duckdb`
   (`uv run --group dev mwh sql "SELECT source_table, count(*) FROM mimiciv_derived.spine GROUP BY 1"`),
   run `spine.validate("full")`, pull wall/peak RSS/disk from `runs.benchmarks`, record disk used
   by `lake/derived/full/spine/`, and append `> **Completion note (date).**` to
   `EP-50-events-spine.md` (table: source · rows · wall s · bytes). If the job failed, relaunch
   the failed sources (`mwh build --tier full --select spine.<source> --background --job
   spine-full-2`) and note the follow-up in EP-55's pickup note.
2. **Retro + ledger** — `roadmap/retro-p3.md` (or the section convention EP-33 established):
   planned vs actual size per brief, full-tier timings table (concepts, patched concepts, QC,
   measurement, phenotypes, cohorts, timeline benchmark, spine) from `runs.benchmarks`, what
   surprised (DuckDB 1.5.x concept breakages, count-pin drift, disk), and the toolchain
   remediation slot decision (allocate an S brief for P4 only if a wheel/version fight is open —
   Streamlit/pyarrow is the known candidate for EP-57).
3. **Decisions + design notes** — DECISIONS.md addenda under D-19 (patch registry, semantics
   deviations), D-20 (per-tier derived/marts layout), D-25 (protocol registry format), D-33
   (chain-mode rounding rule for attrition), and new numbered decisions if P3 made any (e.g.
   spec packaging as package data + `studies/`); DESIGN.md dated notes for §3 (layout), §9
   (`marts/<tier>/cohorts`), §11/§13/§14 (final field names), §15 (new CLI groups, `analyses/`
   module); README § Risks strike-throughs (`~~risk~~ **Resolved by EP-n (date)**`) for items 2
   and 5 as appropriate; capability coverage re-audit for categories 1, 2, 3, 7, 8, 36, 37, 38.
4. **Amend P4 briefs** — walk EP-55 … EP-74 and edit only what P3 changed: exact function/CLI
   names (`timeline.to_mart`, `disclose.warn_badges`, `cohort.attrition`, `mwh cohort build`),
   table names (`meta.qc_*`, `meta.mp_*`, `meta.item_units`, `marts.cohorts`,
   `mimiciv_derived.phenotype_*`, `mimiciv_derived.spine`), the derived/marts layout, and any
   Depends-on that P3 re-ordered; add `> **EP-n pickup note.**` blocks rather than rewriting;
   mirror every P3 brief's `## Parked → final-roadmap.md` items into `roadmap/final-roadmap.md`
   tables (categories 1–3, 7–10, 34–38, cross-cutting).
5. **Reconciliation** — `uv run --group dev python roadmap_check.py` (EP-6) green: every P3 ☑
   has its two commit hashes in `roadmap/README.md`, table ↔ file parity holds, no orphan briefs;
   `uv run --group dev mwh verify EP-34 … EP-53` all still green on fixture (a loop; record any
   red as a P4 pickup note); commit `docs(roadmap): re-plan P3 (EP-54)`.

## Out of scope

- Writing full P5 briefs / re-chartering P6 → EP-74 (D-9). Any code change → new brief or P4.
- Fixing a failed spine build beyond a relaunch → note for EP-55/EP-83 pickup.

## Verification / acceptance

- EP-50 carries a completion note with per-source rows, wall time, peak RSS and disk; `runs.benchmarks`
  has `kind='mart'` rows for every spine source.
- `roadmap/retro-p3.md` exists; DECISIONS.md addenda and DESIGN.md dated notes committed;
  `final-roadmap.md` contains every P3 parked item; README § Risks updated.
- `roadmap_check.py` exits 0; every P4 brief that names a P3 API matches the code (spot-checked
  by grepping the named symbols in `src/mimicwarehouse/`); README ☑ hashes recorded for EP-34 … EP-54.

> **Completion note (2026-09-18).** Executed as one session (2026-09-17 evening →
> 2026-09-18 after midnight, local), tier n/a — docs-only. The only commands that touched
> real-data state were read-only and audited: seven `mwh sql` aggregates through the gate
> (the spine census per source, `DESCRIBE` of two registry tables, the
> `meta.concept_versions` row sum, the `meta.spine_validation` row, the `meta.spine_codes`
> count, a `meta.tables` sum that came back wholly suppressed — two schema groups, one
> below k — and one `chartevents` row count per curated concept group for the SPINE-1
> sizing; every released frame at k = 11 with 0 rows suppressed), `mwh jobs`, `mwh runs
> list | show | benchmarks`, `mwh spine validate --tier full --no-write`, `mwh doctor`.
> Power mode read **Best performance** before the verify loop and `poe check`. All
> Depends-on rows were ☑ at pickup. Per the EP-33 amendment block:
>
> **(1) EP-50's full spine verified** from the job state (exit 0, 2 min 43 s), the gate
> census (13 sources row for row), `spine.validate("full")` (seven checks, 1,300 files,
> 280,203,383 rows), the two registry tables and the `kind: mart` ledger lines (one per
> source + `spine.union`; 867,290,614 bytes) — completion note appended to
> `EP-50-events-spine.md`; nothing relaunched or re-measured. **(2) `roadmap/retro-p3.md`**
> in the `retro-p2.md` shape; no separate findings ledger because P3 ran no discovery audit
> — the carried-low re-triage is a section of the retro with file:line anchors (the
> `retro-p2-findings.md` format rule kept: no reproduction recipes, `1xxxxxxx`-style bands).
> Planned-vs-actual: **≈ 29 h against ≈ 19.5 h planned** (commit-gap basis; ≈ 1.5×, the
> first phase over budget; plus ≈ 2 h 45 min of reverted first attempts) — Risk 11
> re-budgeted, the owner review round named as the unbudgeted fixed cost. Full-tier
> estimates were over-estimates everywhere; DuckDB 1.5.x concept breakages: none; count-pin
> drift: none — EP-53's 57-row hand-off is the EP-38 patch effect (`inflammation` −56,
> `complete_blood_count` −1), reconciled in a D-19 addendum. Toolchain slot: **not needed**
> (lock current, one `pyarrow 24.0.0`). Connector roster: **new write-capable tools** found
> (Claude Docs, Excalidraw, alphaXiv writes, Gmail labels) → owner package in the session
> scratchpad (`ep54-connector-denies.md`), D-43 addendum. **(3) Decisions + design notes:**
> DECISIONS **D-46** (definitions = versioned package data + lock file; studies from the data
> root) and **D-47** (the checkpoint decisions), addenda under D-15 (FC-9: `vl-convert-python`
> core; no P4 slot), D-17 (the seams: document and leave, fix before P9), D-19 (PR re-check;
> row sum reconciled), D-20 (per-tier layout as the P4 contract; the sampler / tags /
> `marts`-tag rulings), D-24 (ledgers as the P4 contract; `runs` non-registry), D-25 (the
> registry format confirmed), D-33 (the count-column rule; MISS-4 → EP-72), D-43 (the
> roster re-check); the status index gained D-46/D-47 rows and split D-32 / D-33 / D-34 into
> their own rows (no header consumed — structure diff before and after). DESIGN dated
> notes: §3 (the D3 estimates replaced: derived + spine ≈ 2.2 GB vs 4–6 GB, no spill, peaks
> 15,935 / 8,068 / 7,480 MB RSS, 380.4 GB free), §5 (the small-path seams), §9 (the cohort
> names final for P4), §10 (the full spine), §11 (the ledger fields final), §13 (the
> protocol registry as the P4/P5/P8 contract), §14 (the disclosure API + count-column
> rule), §15 (the module map at the P3 close: seventeen sub-apps, nothing under `marts/` /
> `ui/` / `viz/` / `stats/` yet), §21 (SPINE-1, `ResourceLog`, the seams resolved). README
> Risks: 2 re-checked (all five PRs open; `main` at `303d26c` touches no concept), 5 struck
> (the engine canon; P3's peaks under the limit), 6 measured, 11 re-budgeted, 15 (FC-9
> settled), 16 (AUDIT-1 re-triaged), 17 (routed to EP-72), 18 (decided); coverage rows 1, 3,
> 7, 10, 36, 38 re-audited and extended (2, 8, 37 unchanged). **(4) P4 amendments:** a
> **"P3 → P4 name corrections"** table in the README's notation section (the override
> mechanism EP-166 established — seventeen mismatch classes the audit found: `mwh app |
> bench | export | stats`, `disclose.small_cells`, `safe.audit` / `read_audit`, the
> `safe_query` signature, `Run.add_artifact`, the cohort / registry / phenotype / timeline /
> measurement function names, `meta.profile_*`, the missing tier segment, `blood_gas`, the
> env names, the `page_latency` kind, EP-172's versions) plus twenty `> **EP-54 pickup
> note**` blocks on EP-55 … EP-74 with only the brief-specific facts; every P3 brief's
> Parked items re-audited present in `final-roadmap.md` (each session had mirrored its own —
> TIME-1, CONC-1/2, PHE-5 … PHE-10, DIS-4, QC-3/4, MISS-3/4, SPINE-2, LOAD-5, BKP-1, AUDIT-1)
> and the EP-54 outcomes written into SPINE-1, LOAD-5, MISS-4, AUDIT-1 and CONC-2, with
> PROV-2 new. **(5) Reconciliation:** `poe roadmap-check --strict` **0 errors / 0 warnings
> — 174 rows, 174 briefs, 63 done** before this tick; the 21-brief `mwh verify` loop **0
> failures** (347 tests, ≈ 26 min); `poe check` **1,183 passed, 58 deselected, 729 s**;
> `mwh guard` clean over the 29 changed / new files; the amendment's item 6 (EP-37 / EP-49 /
> EP-50 rows vs headers) held without an edit (`header: ok`).
>
> **Owner decisions at the interactive review (2026-09-18; two rounds, every recommended
> option taken — D-47).** (1) SPINE-1: v1 keeps raw `chartevents` out (measured ≈ 66 M rows /
> ≈ 0.2 GB / < 1 min recorded). (2) LOAD-5: document and leave; the small path switches to
> per-bucket `COPY`s before P9's ED staging. (3) MISS-4 / Risk 17: routed to EP-72 as a
> `disclose` refinement. (4) **EP-173 — Debt sweep (P4)** (S, core) allocated at the head of
> P4 before EP-55 → **174 briefs, 24 S · 148 M · 2 L** (`EP-173-debt-sweep-p4.md`; the
> fix-now rows of the re-triage led by SGT-5; the session-guard trio as an owner package).
> (5) The connector deny package is applied by the owner. (6) No P4 toolchain slot. (7)
> Commit in the two standard steps, no push.
>
> **Deviations / findings, for the record.** (a) This S brief ran ≈ 3 h — the EP-33
> amendment made it a six-item re-plan with a carried-low re-triage and twenty brief
> amendments (M/L work); recorded under Risk 11 beside P3's own over-runs. (b) **SGT-5 is a
> real governance finding** — the safe-query forbidden-function list does not block the
> `duckdb_*` / `pragma_*` metadata functions, so a session statement could enumerate the
> attached databases' file paths; it is a static review finding (no such statement appears
> in the audit ledger's history, nothing left the gate), fixed first by EP-173, until then
> a known hole. (c) The advisory `mwh disclose check` over the retro flags only prose cells
> over 64 characters (the `retro-p2.md` shape) — roadmap records of counts and telemetry
> carry no sidecar (GOVERNANCE §3 manifests class). (d) No code changed; no earlier test
> edited. **Handed on:** the owner applies `ep54-connector-denies.md` and pushes; EP-173 runs
> next, then EP-55 against the name-corrections table; EP-74 inherits the retro convention,
> the PR re-check, the re-sizing and the fixture-suite wall.
