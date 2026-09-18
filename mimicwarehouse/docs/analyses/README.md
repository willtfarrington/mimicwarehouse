# docs/analyses — case studies (EP-32; the capstone convention)

Every phase of the roadmap ends with a capstone case study (**D-8**), and every case
study in this directory follows the convention below (hupsim precedent: an explicit
"What it deliberately does not claim" section and a Reproduction block in every note).
The portfolio is read by two audiences (**D-1**), so each case study carries a one-line
*reader guide* for each:

- **DS/ML hiring managers** — what was engineered, measured or modelled, and how it was
  verified;
- **clinical-informatics readers** — what the finding does and does not mean clinically,
  and which MIMIC-IV caveats bound it.

## File naming

`NN-slug.md` — two digits in allocation order (`00-staging-benchmark.md`,
`01-concepts-and-qc.md`, `02-tracer-first-icu-mortality.md`), allocated when the case
study is written, never renumbered. Tables and figures a case study embeds live in a
same-named folder (`NN-slug/`), each file beside its `.disclosure.json` sidecar (EP-53):
tables as Markdown (`<table>.md`, integers via `fmt_int`, hidden cells `<11`), figures as
`.vl.json` + `.png`. No CSV enters git - GOVERNANCE section 3's "no `.csv`" is enforced by
`.gitignore`, `.gitattributes` and the guard's G1 / G4, and the disclosure sidecar does not
override them (owner decision at EP-53); the CSV and Parquet twins stay under `runs/`.
The `NN-slug` form and the no-run-ids-or-compact-dates-in-names rule are instances of the
committed-text hygiene canon, `docs/committed-text.md` (rule 3; EP-33 B4).

## Required sections, in order

Every case study has these sections; a section that is trivially satisfied still
appears and says so.

1. **Question** — the one question the note answers.
2. **Data & tier** — tier(s), snapshot ids and build/run ids the numbers come from, and
   the sentence "MIMIC-IV analyses are retrospective".
3. **Method** — how, briefly; machine and engine facts where they matter.
4. **Results** — **aggregates only, post-suppression** (k = 11, GOVERNANCE §5). Figures
   are committed as a Vega-Lite spec plus a rendered PNG, neither embedding row arrays
   (aggregated data values only).
5. **What it deliberately does not claim** — the explicit non-claims list.
6. **Reproduction** — run ids, the exact `uv run …` commands that reproduce every
   number, and the protocol hash when the analysis ran under a frozen protocol (EP-51).
7. **Provenance footer** — git sha, snapshot ids, environment hash (`uv.lock` blob
   hash), DuckDB version.

Near the top, before **Question**, every case study carries a **claim-type label** line
— one of *exploratory / confirmatory / predictive / associational / causal*
(GOVERNANCE §7) — and the two reader-guide lines (D-1).

## Disclosure rule

Every case study, and every table or figure it embeds, passes
`uv run --group dev mwh disclose check <path>` and carries a `.disclosure.json`
sidecar (EP-43; the rules and codes are in [../methods/disclosure.md](../methods/disclosure.md)).
Files written before EP-43 existed stated "disclosure sidecar pending EP-43" in their
header; EP-43 checked them retroactively (2026-09-07) and added the sidecars, and
`test_ep43` re-verifies every committed pair. After regenerating a note,
re-run `mwh disclose check <path> --write-sidecar` so the sidecar's sha256 matches.
Before EP-43, nothing derived from patient-level data entered this directory — build
telemetry (counts also published in the vendored `validate.sql`, bytes, timings, RSS,
file counts) was the one admissible content class (GOVERNANCE §3 manifests rule).

## Index

| # | Case study | Claim type | Status |
|---|---|---|---|
| 00 | [Staging benchmark](00-staging-benchmark.md) | exploratory (build telemetry) | committed; passed `mwh disclose check` at EP-43, sidecar `00-staging-benchmark.md.disclosure.json` |
| 01 | [Concepts and QC (Capstone #1, P3)](01-concepts-and-qc.md) | exploratory (concepts and data quality) | committed at EP-53; full-tier report run `20260917T213458Z-3f1556` (`mwh build --tier full --select analyses.c01_concepts_qc --background --job ep53-capstone`); the eleven tables (promoted **as Markdown tables** - committed CSVs are refused by `.gitignore` / `.gitattributes` / the guard by design, owner decision 2026-09-17; the CSV + Parquet twins stay in the run folder) and two figures in [`01-concepts-and-qc/`](01-concepts-and-qc/) each carry a sidecar, written only through `python -m mimicwarehouse.analyses.c01_concepts_qc promote --run <run_id>` (the programmatic `mwh disclose check --write-sidecar`); the note's own sidecar via `... check-doc`; the QC highlights and the measurement teaser below are this note's tables (c) and (e) |
| 02 | [Tracer bullet: first ICU stay of adult patients -> in-hospital mortality (P2)](02-tracer-first-icu-mortality.md) | associational (exploratory) | promoted at EP-53 from the EP-31 run folder `runs/tracer/20260917T213352-full/` (a fresh full-tier re-run through the job runner, ledger run `20260917T213345Z-4376d1`; the EP-31 numbers reproduce: cohort n = 65,366, AUC 0.731) by `python -m mimicwarehouse.analyses.c01_concepts_qc promote-tracer --run <stamp>` — the body re-rendered after chain-mode suppression of the attrition, table-mode suppression of the descriptives and the n-vs-n_fit rule (EP-43 amendment b); the three JSON aggregates in [`02-tracer-first-icu-mortality/`](02-tracer-first-icu-mortality/) with sidecars. The 2026-08-30 folders stay under `runs/tracer/` for the audit trail |
| — | Data-quality profile: `runs/<run_id>/qc_report.md` + `qc_tables.csv` / `qc_checks.csv` (EP-44; the `kind: qc` run of `mwh build --tier <t> --tag qc`) | exploratory (data-quality profile) | **promoted at EP-53 as the QC highlights of 01** (checks by status per table, the top warn / fail checks, unit variants, implausible shares, timestamp ordering — read from `meta.qc_checks` / `meta.item_unit_variants`, full qc run `20260915T011113Z-4b675d`); the full report itself stays under `runs/` (written already suppressed and passing `mwh disclose check` on every file; `test_ep44` asserts it on the fixture) |
| — | Measurement process: `runs/<run_id>/measurement_process.md` + the six `mp_*.csv` tables (EP-45; the `kind: qc` run named `measurement` of `mwh build --tier <t> --tag measurement`) | exploratory (measurement process) | **promoted at EP-53 as the measurement teaser of 01** (share of ICU stays measured in the first 24 h for ten curated items by era, from the structural slice of full qc run `20260916T194157Z-25a7ea`); the full report and the six tables stay under `runs/` (EP-72 renders the views over the same tables; written already suppressed and passing `mwh disclose check` on every file; `test_ep45` asserts it on the fixture). The at-risk curves of `meta.mp_item_hourly` (roadmap Risk 17, MISS-4) are **not** promoted here |
