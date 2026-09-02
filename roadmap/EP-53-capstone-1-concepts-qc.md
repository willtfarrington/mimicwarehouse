# EP-53 — Capstone #1: concepts/QC case study

**Size:** M · **Tier:** fixture+dev+full · **Core/Stretch:** core · **Depends on:** EP-38 (Concept fixes/ports for DuckDB 1.5.x), EP-44 (Data-quality profiling) · **Blocks:** EP-54 (Re-plan P3)

> **EP-33 amendment (2026-09-01).** Header facts unchanged. (1) **Tracer-report promotion**
> (EP-33 D1 queue): the EP-31 tracer report (`report.md` + `model.json`, `attrition.json`,
> `descriptives.json` under `layout["runs"]/tracer/` from `mwh tracer --tier full`) is promoted
> into `docs/analyses/` beside this capstone — through `mwh disclose check --write-sidecar`
> (EP-43), with the n-vs-n_fit complementary-suppression rule applied (EP-43 amendment (b)), a
> file name without run id / compact date (`docs/committed-text.md` rule 3; e.g. `docs/
> analyses/02-tracer-first-icu-mortality.md` + a same-named folder), the claim-type label
> (exploratory) and the retrospective sentence; its reproduction block cites the EP-31 run and,
> after EP-35's retrofit, the ledger run id. (2) **Spec discovery** (ledger P3C-2, EP-37):
> `--select analyses.c01_concepts_qc` resolves because the `analyses` spec file is merged by
> `dag.spec.load_dag()` — the module is a `python` step (`mimicwarehouse.analyses.
> c01_concepts_qc:build`, `(step, ctx) -> StepOutcome`) in `dag/specs/analyses.yaml`; no
> `--spec`. (3) Reads: `meta.*` is a registry exemption, `runs.*` is **not** (checkpoint
> decision — count-family GROUP BYs), phenotype/concept views are subject-keyed (P3C-5); ratios
> (demo-vs-full pins, Wilson CIs) are computed in Python from released counts (DIS-3).
> Benchmarks via `mwh runs benchmarks --kind concept|query --format md --out ...` (the EP-32
> verb). (4) Errors on stderr via `console.fail`; `--json` via `console.emit_json` (raw ints
> never pasted into the case study — `fmt_int` for tracked Markdown).

## Context

Each phase closes with a capstone that turns the phase's machinery into a reproducible, disclosed
artifact (D-8; docs convention set by EP-32 in `docs/analyses/README.md`: purpose, methods, "What
it deliberately does not claim", Reproduction block). P3 built the concept layer (EP-37/38 with
patches and full-tier timings), unit curation (EP-39), phenotypes with full-tier prevalence
(EP-41/42), disclosure primitives (EP-43), QC profiles (EP-44) and measurement-process summaries
(EP-45). This brief writes `docs/analyses/01-concepts-and-qc.md`: an **exploratory**,
retrospective case study whose numbers reproduce from recorded run ids and whose every table and
figure passed `mwh disclose check` with a `.disclosure.json` sidecar (D-33, D-40; GOVERNANCE §7).
Reuse existing full-tier runs (concepts, QC, phenotypes); new full-tier work is limited to fast
`safe_query` aggregates over `full.duckdb`, launched — like every full-tier run — through the
EP-19 job runner rather than in the foreground. Audience: both
reading paths (D-1) — a DS/ML reader sees timings, coverage and pins; a clinical-informatics
reader sees phenotype definitions, prevalence by era and data-quality caveats.

## In scope

1. **Analysis module** (`src/mimicwarehouse/analyses/c01_concepts_qc.py`, or the location EP-32's
   convention fixed — follow it) — a single `build(tier="full") -> run_id` entry point inside
   `run.start(kind="report")`, launched on full as a logged background job (`uv run --group dev
   mwh build --tier full --select analyses.c01_concepts_qc --background --job ep53-capstone`,
   EP-19 launcher, log `%MWH_DATA_ROOT%\runs\jobs\ep53-capstone.log`; job id + run id recorded in
   the completion note; only pre-aggregated `meta.*`/ledger reads happen in-session), that
   produces, via `safe_query`/`disclose.suppress`, the tables:
   (a) concept inventory: concept · group · upstream commit · patch id · rows on demo/dev/full ·
   wall s on full (from `meta.concept_versions` + `runs.benchmarks`); (b) demo count-pins vs
   full ratios; (c) QC highlights: checks by status per table, top-10 warn/fail checks, unit
   variants for curated itemids, implausible-value shares, timestamp-ordering rates
   (`meta.qc_checks`, `meta.item_unit_variants`); (d) phenotype prevalence: T2DM (subject),
   sepsis-3 and KDIGO AKI (icustay) overall and by `anchor_year_group` era, KDIGO stage
   distribution, sepsis-3 vs explicit-code 2×2 (`meta.phenotype_versions` + phenotype views);
   (e) measurement-process teaser: share measured in first 24 h for 10 curated items by era
   (`meta.mp_*`). Every table is written to `runs/<run_id>/tables/*.csv` after suppression.
2. **Figures** — two Altair charts with the EP-5 theme: concept build wall time by group
   (bar) and sepsis-3 / AKI prevalence by era (grouped bar with Wilson CIs from statsmodels);
   saved as `.vl.json` + `.png` in `runs/<run_id>/figures/` (aggregates only in the spec).
3. **Case study document** (`docs/analyses/01-concepts-and-qc.md`) — sections: Question ·
   Data & tiers (fixture/demo/dev/full; snapshot ids) · Concept layer (what was adopted, patched,
   deviations table link to `docs/resources/concepts.md`) · Data quality (highlights + how to read
   `meta.qc_*`) · Phenotypes (definition cards link, prevalence tables/figure) · Measurement
   process teaser · **Claim type: exploratory; all MIMIC-IV analyses are retrospective** · What it
   deliberately does not claim (no clinical validation of phenotypes; counts reflect charting, not
   incidence; era differences confound with coding practice) · Reproduction block
   (`run.reproduction_block(run_id)`: run ids for concepts full, QC full, phenotypes full, this
   report; git sha; commands) · Limitations · Next (P4 marts/app).
4. **Promotion with disclosure review** — copy tables/figures from `runs/<run_id>/` into
   `docs/analyses/01-concepts-and-qc/` only through `uv run --group dev mwh disclose check <path>
   --write-sidecar` (each artifact gets its `.disclosure.json`); the Markdown embeds the PNGs and
   links the CSVs; `mwh disclose check docs/analyses/01-concepts-and-qc.md` also passes; nothing
   under 11 appears anywhere.
5. **Tests** (`tests/ep/test_ep53.py`, `@pytest.mark.ep_53`; fixture, `dev`, `full` opt-in) —
   `build(tier="fixture")` runs end to end on the fixture catalog and every produced artifact
   passes `disclose.check`; the case-study file exists, contains the claim-type line and the
   retrospective sentence, its relative links resolve, and each linked artifact has a sidecar; on
   dev, `build("dev")` completes; the numbers quoted in the Markdown match the CSVs (a test parses
   the Reproduction block's run id and compares two headline numbers).

## Out of scope

- New concepts, patches or QC checks — hand back to EP-38/44/45 with a note; report only.
- EDA case study with app screenshots → EP-73 (P4). Report engine (Jinja/HTML/PDF) → P8; this
  capstone is hand-authored Markdown per the EP-32 convention.
- Case-study compilation for the docs site → EP-161.

## Verification / acceptance

- `uv run poe test -m ep_53` green on fixture and dev; `uv run --group dev mwh verify EP-53` green.
- `docs/analyses/01-concepts-and-qc.md` and `docs/analyses/01-concepts-and-qc/` exist; every
  table/figure there has a `.disclosure.json` sidecar; `uv run --group dev mwh disclose check
  docs/analyses/01-concepts-and-qc` exits 0.
- The report's run id (full tier) and the `ep53-capstone` job id/log path are recorded in the
  completion note together with the run ids it cites; two headline numbers reproduce from
  `mwh runs show <run_id>` tables.
- Both reading paths are visible: a "For ML/DS readers" and a "For clinical-informatics readers"
  pointer paragraph near the top; the guard (`mwh guard`) passes on the commit.
