# EP-130 — Report engine A: Jinja2 → MD/HTML

**Size:** M · **Tier:** fixture · **Core/Stretch:** core · **Depends on:** EP-59 (Export primitives), EP-43 (Disclosure primitives (`disclose` module)) · **Blocks:** EP-131 (Report engine B: PDF via Typst + export finalization), EP-132 (Model card + methods summary + executive summary templates), EP-133 (Disclosure-review tool), EP-134 (Runs & Provenance browser + Reports page / export gallery), EP-135 (Capstone #6 + full-tier regression)

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-127 (Re-plan P7) before execution.

## Context

Category 33 (reproducible reporting). D-23 fixes Jinja2 → Markdown + self-contained HTML (PDF via
Typst follows in EP-131); DESIGN §17 fixes the `Report` object: sections, tables (post-suppression),
figures (Vega-Lite spec + PNG), methods summary, claim-type label, provenance footer. It builds on
`disclose` (EP-43: `suppress`, `check`, sidecar; D-33, D-40), the export primitives (EP-59: chart /
table save with suppression + provenance footer + sidecar), the run ledger (EP-35) for provenance
fields, `viz/` spec builders (EP-64+) and `theme.py` (EP-5) for CSS. GOVERNANCE §7: every report
labels its claim type and states that MIMIC-IV analyses are retrospective. Every capstone from
EP-135 on and every method brief's artefact renders through this engine, so it must be pure Python,
deterministic and fully testable on the fixture tier.

## Scope sketch (refine at re-plan)

1. **`src/mimicwarehouse/report/model.py`** — pydantic `Report`: `title`, `slug`, required
   `claim_type: ClaimType` (exploratory / confirmatory / predictive / associational / causal),
   `audience` (technical / clinical, D-1 two paths), `sections: list[Section]`, `provenance:
   Provenance` (run ids, snapshot ids, protocol hash, git sha, `uv.lock` hash, DuckDB version,
   tier, seeds, package version), `notices` (always contains the module constant
   `RETROSPECTIVE_STATEMENT` — "All MIMIC-IV results in this report are retrospective analyses of
   de-identified, date-shifted data; prospective-style protocols do not make them prospective." —
   plus EP-129's `HOLDOUT_NOTICE` when present), `not_claimed: list[str]` (the "What it
   deliberately does not claim" convention from EP-32; warning if empty). Blocks: `TextBlock`
   (Markdown), `TableBlock` (Polars/pandas frame + caption + `k`), `FigureBlock` (Vega-Lite spec +
   optional PNG bytes), `MethodsBlock`. `Report` round-trips through JSON so runs can write
   `runs/<run_id>/report.json` and render later. `ClaimType.causal` renders as the badge text
   'causal-with-assumptions' and requires a non-empty `assumptions` list on the `Report`
   (validator); any other label string is refused.
2. **`report/render.py`** — Jinja2 environment (`StrictUndefined`, HTML autoescape); templates
   `report/templates/base.md.j2`, `base.html.j2`, `_table.j2`, `_figure.j2`, `_footer.j2`. HTML is
   self-contained: inline CSS from `theme.py`, figures as PNG data URIs (EP-59's Vega → PNG path),
   the Vega-Lite spec inlined only when its data array is an aggregate that passed `disclose.check`,
   no external `src`/`href` assets. Claim-type badge and `RETROSPECTIVE_STATEMENT` render in the
   header of both formats; the provenance footer in both.
3. **`report/build.py`** — `build_report(report, out_dir, formats=("md", "html")) -> BuildResult`:
   every `TableBlock` passes `disclose.suppress(df, k=11)` (suppressed-cell counts land in the
   footer); any frame with an identifier or free-text column raises `ReportDisclosureError`;
   after rendering, `disclose.check` runs on each output and EP-59 writes the `.disclosure.json`
   sidecars; output is byte-deterministic given an injectable `rendered_at` clock. CLI
   `mwh report build <report.json> --out <dir> [--formats md,html]` and `mwh report demo --out <dir>`
   (a synthetic showcase report on the fixture tier).
4. **Wiring** — `report_from_run(run_id)` seeds provenance and tables from a run directory (EP-35
   manifest + `tables/`); `safe_query` / `mwh sql` results (EP-30) are the only other accepted
   `TableBlock` inputs — the engine itself never opens the catalog.
5. **Tests `tests/ep/test_ep130.py`** (`@pytest.mark.ep_130`, fixture): golden MD/HTML for a
   synthetic aggregate report; missing claim type refused; a frame carrying a `subject_id` column
   refused; a cell of 7 appears suppressed in the output; sidecars written and `mwh disclose check`
   passes; HTML has no external assets; two builds with the same clock are byte-identical.

## Out of scope

- PDF via Typst and promotion into `reports/` → EP-131.
- Model card / methods summary / executive summary templates → EP-132.
- Approve/deny review ledger → EP-133; Reports page / gallery → EP-134; docs site → EP-160.

## Verification / acceptance (sketch)

- `uv run poe test -m ep_130` and `uv run --group dev mwh verify EP-130` green on fixture;
  `uv run --group dev mwh report demo --out %MWH_DATA_ROOT%\runs\demo-report` yields `report.md`,
  `report.html` and sidecars, and `mwh disclose check` passes on both.
- Crafted violation refused: an identifier column raises `ReportDisclosureError`; a table below
  k renders suppressed, never raw.
- Every rendered report shows the claim-type badge, the retrospective statement and a provenance
  footer citing run and snapshot ids.

## Parked → final-roadmap.md

- Quarto narrative lane (v2 REP-1), DOCX export (v2 REP-2), Great Tables journal formatting for
  Table 1 (v2 EDA-1) — triggers as already listed.
