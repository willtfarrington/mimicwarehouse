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

> **Completion note (2026-09-17).** Executed on fixture + dev in the foreground and on full
> through the job runner, one session (≈ M). Shipped `src/mimicwarehouse/analyses/`
> (`__init__.py` docstring-only; `c01_concepts_qc.py`), `dag/specs/analyses.yaml` (the
> `analyses.c01_concepts_qc` python step after the shared `catalog` step; tags `analyses`,
> `capstone`; no target — every run is a new run id), `tests/ep/test_ep53.py` (13 fixture
> tests + `tier("dev")` + `tier("full")`), the case study
> `docs/analyses/01-concepts-and-qc.md` + `01-concepts-and-qc/` (eleven **Markdown**
> tables, two `.vl.json` + `.png`, every file with its `.disclosure.json`), the promoted tracer
> `docs/analyses/02-tracer-first-icu-mortality.md` + `02-tracer-first-icu-mortality/`
> (the three JSON aggregates with sidecars), the `docs/analyses/README.md` index rows,
> the README § State row, the DESIGN §15 module-map line and the §17 "capstone pattern as
> shipped" note.
>
> **Runs.** Full: job `ep53-capstone` (`runs\jobs\ep53-capstone.log`, launched
> `mwh build --tier full --select analyses.c01_concepts_qc --background --job ep53-capstone`)
> → build `20260917T213454-full-bd64821` (the step 4.1 s, build 7.8 s) → **report run
> `20260917T213458Z-3f1556`** (wall 4.0 s, peak RSS 335 MB (`peak_wset`), disk delta 0.3 MB;
> 14 recorded statements = 14 audit ids; snapshot ids core `b1fc5313…`, derived `61c31299…`,
> `core.demo` `f38696a8…`, `core.dev` `f830b941…`; refs: the mimic-code pin, the four
> phenotypes with `def_hash`, the EP-45 structural slice) — the run the case study cites and
> whose tables `01-concepts-and-qc/` holds as Markdown, value for value. Dev (foreground,
> `python -m mimicwarehouse.analyses.c01_concepts_qc build --tier dev`): run
> `20260917T213444Z-0d14d3` (3.9 s, 322 MB); the `tier("dev")` test's own run
> `20260917T215613Z-c7c0ae` (3.3 s). Fixture: the session lake now runs the step at the end
> of every full DAG build (≈ 4 s; the run under `tmp`). Tracer re-run for promotion (the
> 2026-08-30 folders predate EP-35 and carry no ledger run id): job `tracer-full-ep53`
> (`runs\jobs\tracer-full-ep53.log`) → `runs\tracer\20260917T213352-full`, ledger run
> `20260917T213345Z-4376d1`, 3.46 s, 7 audited calls — cohort n = 65,366, model fit,
> in-sample AUC 0.731: EP-31's numbers reproduce count for count. Runs cited by the note
> (all full): concepts `20260906T003510Z-9564c0` + `20260906T154530Z-fca779`, units build
> `20260906T230902-full-46e84a6`, phenotypes `20260906T231017Z-69287e` (t2dm),
> `20260907T001156Z-99ba05` (sepsis3), `20260907T001156Z-c09262` (kdigo_aki),
> `20260916T203433Z-28a895` (sepsis_explicit@1.1.0), QC `20260915T011113Z-4b675d`,
> measurement `20260916T194157Z-25a7ea`.
>
> **Headline numbers (full, k = 11).** 65 / 65 concepts done on demo, dev and full (9 groups,
> 5 patched); derived layer 95,777,694 rows on full (4,633,170 dev, 131,517 demo); concept
> wall 679.4 s of which `suspicion_of_infection` 442.8 s and `rhythm` 159.7 s (peak RSS
> 7,480 MB); demo pins 65 / 65 match, full-to-demo ratio median 674.7. QC 574 checks: pass
> 491 / warn 83 / **fail 0** over 31 tables; 16 / 63 curated items with a unit variant
> (14 of them a sub-k null-unit string); implausible shares ≤ 1.4 %; `pharmacy` /
> `prescriptions` `stoptime < starttime` 4.0 %, `outputevents` back-charted 14.1 %.
> Phenotypes: sepsis-3 41,296 / 94,458 ICU stays (43.7 %; 47.6 % → 35.3 % across eras),
> KDIGO AKI 67,981 / 94,458 (72.0 %; flat 70 – 75 %), t2dm 49,599 / 364,627 subjects
> (13.6 %); sepsis-3 × explicit codes per ICU admission (n = 85,242): both 12,113,
> sepsis-3 only 26,826, codes only 2,167, neither 44,136. First-24 h measurement: vitals
> ≥ 99 %, basic labs ≈ 95 %, lactate 50 → 55 %, Celsius temperature rare after 2008–2010
> (Fahrenheit is charted). Every number is in the promoted tables; the two headline numbers
> are re-parsed by `test_ep53` against `01-concepts-and-qc/phenotype_prevalence.md` and
> (full tier) against the run folder's CSV.
>
> **Gates.** `poe test -m ep_53` 13 passed (fixture); `mwh verify EP-53` exit 0;
> `poe test-dev -m ep_53` + `poe test-full -m ep_53` 1 + 1 passed; `mwh disclose check
> docs/analyses/01-concepts-and-qc` / `…-qc.md` / `02-…md` exit 0 (sidecars written by the
> promote / `check-doc` calls, `mwh disclose verify` ok); `poe check` green (ruff, format,
> pyright, the whole suite — 1,183 selected, 745 s); `mwh guard` clean on the new paths.
> Two earlier tests touched under the EP-170 CMP-6 rule (a shipped fact legitimately
> changed): `test_ep19::test_shipped_spec_orders_and_selects` pinned `catalog` as the
> **last** step of the merged DAG — since EP-53 the capstone step follows it by design, so
> the pin became "catalog follows every stage step and only analyses steps follow catalog";
> `test_ep32::test_convention_index_tracer_row_is_pending_only` asserted the index's tracer
> row still read "pending promotion at EP-53" — it now asserts the promoted 02 row.
> `tracer.render_report` gained a no-op-for-ints rendering of withheld / banded counts
> (`test_ep31` 7 passed).
>
> **Deviations and judgment calls (routine).** (1) The DAG callable is `run_step`, not
> `build`: the brief names `build(tier) -> run_id` as the Python entry point and
> `(step, ctx) -> StepOutcome` as the step signature, which cannot be one function;
> `run_step` wraps `build(ctx.tier)`. (2) Table (e) reads EP-45's **raw structural slice**
> (data root only) rather than a `meta.mp_*` table: no published table carries the
> first-24 h share by era; the slice is the designed input of EP-45's `assemble`, summed
> per item × era and k-suppressed by the same primitive before anything is written
> (recorded on the run as the `measurement_slice` ref). (3) Cross-tier reads (demo, dev
> catalogs for the inventory) go through plain `safe_query` with the statement, audit id and
> that tier's snapshot recorded by hand under `core.<tier>`, because `Run.safe_query` would
> overwrite the run's `core` id. (4) The tracer promotion **re-renders** from the JSON
> payloads after `suppress_tracer_payloads` (chain mode over the attrition — on the fixture
> the chain rule genuinely fires, `~80 / ~70 / 66`; table mode over the descriptives; the
> n-vs-n_fit rule) rather than copying `report.md`: the gate refused the verbatim fixture
> report (derivable attrition drops), and the rule the brief asks for has to be applied
> somewhere. On full the payloads needed no suppression (n − n_fit = 24, no drop < 11), so
> the promoted body equals the run folder's. (5) `render_tables` / `summary` /
> `headline` subcommands exist because the session's `.csv` deny rule (correctly) refuses
> shell reads of the promoted CSVs; project commands over k-suppressed aggregates are the
> sanctioned inspection path (the same class as `mwh qc status`). (6) Count columns are
> named `n_*` so the checker scans exactly the counts; counts of checks / unit strings are
> named `checks_*` / `unit_strings` and headed `# …` in Markdown (the gate refused a
> `concepts` header over 1 … 6 before the rename — the EP-44 report's `# fail` convention).
> (7) `git_dirty = true` in the promoted provenance blocks is inherent: the artefacts are
> generated before the commit that carries them.
>
> **Owner decision (2026-09-17, interactive).** The brief's "the Markdown links the CSVs"
> cannot be committed as written: `.gitignore` ignores `*.csv` outside `tests/fixtures/`,
> `.gitattributes` marks `*.csv` binary and the guard's G1 (data-shaped extension) and G4
> (raw 8-digit row counts read as id-band tokens) refuse all eleven promoted CSVs, although
> every one passes `mwh disclose check` with a sidecar — GOVERNANCE §3's "no `.csv`" as
> built, stricter than D-40's sidecar rule. Options put to the owner: (a) promote the
> tables **as Markdown** (`<table>.md`, `fmt_int`, `<11`; no governance change — recommended),
> (b) keep CSVs and extend four "ask before" files (`.gitignore`, `.gitattributes`, the guard
> with a verified-sidecar exemption, GOVERNANCE §3) plus a D-40 addendum, (c) commit no
> tables. **Decision: (a).** `promote` now renders each run CSV as a Markdown table,
> `load_table` / `md_table_frame` read it back typed (markers from `<11` cells), the
> case study links the `.md` tables, and the CSV + Parquet twins stay in the run folder.
> Recorded in `docs/analyses/README.md` § File naming and the DESIGN §17 note; no
> `DECISIONS.md` entry because nothing in the governance layers changed.
>
> **Closing decisions (owner, interactive, 2026-09-17).** (1) Commit: both steps now —
> the `feat(mimicwarehouse): … (EP-53)` commit and the `docs(roadmap): record EP-53 commit
> hash` commit; no push (the owner pushes). (2) Roadmap Risk 17 (at-risk curves, MISS-4):
> left open and moved to EP-54 with EP-72 as the next consumer — EP-53 promoted nothing
> monotone (sentence appended to the risk).
>
> **Handed on.** EP-54: the derived-layer row sum (95,777,694) differs from EP-38's
> completion note (95,777,751) by 57 rows — reconcile against the derived snapshot id;
> roadmap Risk 17 (at-risk curves, MISS-4) stays open — nothing monotone is promoted here.
> The 2026-08-30 tracer folders stay under `runs\tracer\` for the audit trail.
