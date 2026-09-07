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
`01-…`), allocated when the case study is written, never renumbered.
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
| — | Tracer bullet: first-ICU-stay adults → in-hospital mortality (EP-31) | associational (exploratory) | **pending promotion at EP-53** (EP-43 shipped the gate; the report's cohort table is the chain-rule regression case `disclose.suppress(mode="chain")` resolves) — report stays under `runs/tracer/` (full-tier run `20260830T011037-full`, dev run `20260830T011025-dev`); no results copied here |
