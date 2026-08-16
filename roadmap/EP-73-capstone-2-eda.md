# EP-73 — Capstone #2: EDA case study + screenshots

**Size:** M · **Tier:** fixture+dev+full · **Core/Stretch:** core · **Depends on:** EP-57 (App shell A (Streamlit multipage)), EP-58 (App shell B: row-view gate + app-side small-cell enforcement), EP-59 (Export primitives), EP-60 (Screenshot tooling), EP-61 (Catalog & QC browser page), EP-62 (Cohort Builder page), EP-63 (Phenotype Studio page), EP-64 (Explorer A: server-side aggregation service + VegaFusion), EP-65 (Explorer B: linked-brush distributions), EP-66 (Explorer C: heatmaps, correlations, cross-tabs, conditional summaries), EP-67 (Patient-safe timeline viewer), EP-68 (Prevalence/incidence/event-rate module), EP-69 (Prevalence/incidence page), EP-70 (Descriptive stratified/subgroup module + page), EP-71 (Cross-sectional EDA module + page (Table 1)), EP-72 (Missing-data views) · **Blocks:** EP-74 (Re-plan P4 (writes full P5, re-charters P6))

## Context

The P4 showcase (D-8: capstone per phase; D-1: portfolio value). Everything it needs exists:
the app shell and pages (EP-57–72), export primitives with suppression + footer + sidecar
(EP-59), demo-tier screenshot tooling (EP-60), the modules `stats.rates/subgroups/table1/
missing` with recorded full-tier run ids (EP-68/70/71/72), the tracer cohort
`first_icu_stay_adults` (EP-31/47), the case-study conventions in `docs/analyses/README.md`
(EP-32: "What it deliberately does not claim" + Reproduction blocks) and the benchmark ledger
with page latencies (EP-56/57). This brief writes `docs/analyses/02-eda-case-study.md` from
recorded full-tier run ids only, promotes every table/figure through `mwh export … --promote`
(D-40; sidecars), and regenerates all P4 screenshots on demo. Claim type is **exploratory**;
the document states that MIMIC-IV analyses are retrospective and lists the caveats (per-patient
date shift → era bins only; `dod` ~1 y horizon; ICD-9→10 switch; discharge-alive as a
competing event; ages ≥ 89 = 91; ED not yet linked; demo = 100 subjects, v2.2 schema).

## In scope

1. **Close the ledger** — confirm full-tier records exist for the marts (EP-55/56 completion
   notes), the module runs (EP-68/70/71/72 run ids) and every P4 page latency
   (`page_latency` entries EP-61–72); rerun any missing one as a logged background job and
   record it; refresh all screenshots: `uv run --group ui mwh app screenshot --tier demo`
   (light + dark) → `docs/screenshots/` with sidecars; `mwh disclose check docs/screenshots`.
2. **Case study** `docs/analyses/02-eda-case-study.md` (per `docs/analyses/README.md`): front
   matter (title, date, claim type exploratory, tier full, run ids); *Question*; *Cohort*
   (tracer attrition table + diagram export from EP-62/48); *Table 1 by in-hospital mortality*
   (EP-71 export); *Prevalence & incidence* (EP-68: sepsis-3 by era; AKI 7-day incidence and
   event rate; T2DM across the ICD switch); *Subgroups* (EP-70 forest); *Distributions &
   relationships* (EP-64–66: histogram, correlation heatmap, conditional summary — three or four
   figures); *Missingness & measurement process* (EP-72); *Data-quality caveats* (EP-61/44 flags
   touching the variables used); *Aggregate timeline density* (EP-67 aggregate mode); *What it
   deliberately does not claim* (no causal or associational claims, no p-values, no calendar
   trends, dod horizon, ICD switch, 91, competing discharge, ED unlinked); *Reproduction* (run
   ids; exact commands `uv run --group dev mwh stats rates|subgroups|table1|missing … --tier
   full`, `mwh export … --promote docs/analyses/02-eda/`, `uv run --group ui mwh app`);
   *Performance* (page latencies on full vs the 5 s target from the benchmark ledger).
3. **Artifacts** — every table/figure produced by `mwh export table|figure <run_id> … --promote
   docs/analyses/02-eda/` (EP-59) from the recorded full-tier run ids; every file carries a
   `.disclosure.json` sidecar; screenshots embedded from `docs/screenshots/` and labelled
   "demo tier (ODbL)"; no artifact hand-edited.
4. **Docs plumbing** — add the entry to the `docs/analyses/README.md` index; add a "Lab app
   wave 1" section to the repo `README.md` with two or three demo screenshots and the case-study
   link; `docs/screenshots/README.md` alt-text list updated.
5. **Tests** `tests/ep/test_ep73.py` (`@pytest.mark.ep_73`): fixture — every artifact
   referenced by the case study exists and has a sidecar; no file under
   `docs/analyses/02-eda/` or `docs/screenshots/` lacks one; Markdown links resolve; a
   guard-style regex finds no number in the real id bands anywhere in the document; full-marked
   — every cited run id exists in `runs/ledger.jsonl` (via the EP-35 ledger reader).
6. **Gate before commit** — `uv run --group dev mwh guard` and `uv run --group dev mwh disclose
   check docs/analyses/02-eda docs/screenshots` pass; commit pair per CLAUDE.md §4.

## Out of scope

- New analyses or pages (fix only what the case study reveals; log the rest in the completion
  note for EP-74).
- Report engine / PDF (EP-130/131); the docs site (EP-160); one-pager (EP-162).

## Verification / acceptance

- `docs/analyses/02-eda-case-study.md` and `docs/analyses/02-eda/*` exist; every artifact has a
  passing `.disclosure.json` sidecar; `mwh disclose check` passes on both directories.
- Numbers reproduce: re-running two of the cited CLI commands on full (background job) yields
  identical exported tables (diff clean).
- Links resolve (test); `uv run poe test -m ep_73` green on fixture; full-marked ledger test
  green; `uv run --group dev mwh verify EP-73` green.
- All P4 pages have demo screenshots (light + dark) with sidecars; the README section renders.
