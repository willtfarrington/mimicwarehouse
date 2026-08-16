# EP-54 — Re-plan P3

**Size:** S · **Tier:** n/a · **Core/Stretch:** core · **Depends on:** EP-34 (Time semantics + unit-of-analysis registry), EP-35 (Provenance run ledger), EP-36 (Seed/determinism policy + resource logger), EP-37 (Concept runner (mimic-code concepts_duckdb → mimiciv_derived) ⏱), EP-38 (Concept fixes/ports for DuckDB 1.5.x), EP-39 (Itemid dictionary curation + unit harmonization), EP-40 (Code-set registry + ICD-9→10 GEM utility), EP-41 (Phenotype engine + T2DM phenotype), EP-42 (Phenotypes: sepsis-3 + KDIGO AKI stage), EP-43 (Disclosure primitives (`disclose` module)), EP-44 (Data-quality profiling), EP-45 (Measurement-process summaries), EP-46 (Cohort spec + registry), EP-47 (Cohort compiler, materialization, attrition, snapshot), EP-48 (Attrition diagram renderer), EP-49 (Event-aligned timeline API), EP-50 (Events spine (MEDS-compatible) ⏱), EP-51 (Protocol schema + freeze registry + `mwh protocol`), EP-52 (Backup of non-reproducible state (`mwh backup`)), EP-53 (Capstone #1: concepts/QC case study) · **Blocks:** —

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
