# EP-134 — Runs & Provenance browser + Reports page / export gallery

**Size:** M · **Tier:** fixture+dev · **Core/Stretch:** core · **Depends on:** EP-57 (App shell A (Streamlit multipage)), EP-35 (Provenance run ledger), EP-130 (Report engine A: Jinja2 → MD/HTML) · **Blocks:** EP-135 (Capstone #6 + full-tier regression)

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-127 (Re-plan P7) before execution.

## Context

Category 38 (end-to-end provenance: EP-35/36 plus this page) and 33 (reports). D-24: the run store
is per-run JSON sidecars + append-only JSONL ledgers exposed as `runs.duckdb` views rebuilt by
`mwh runs refresh`; GOVERNANCE §8 makes the audit trail browsable read-only in the app here. Built
on the shell (EP-57: pages registry, tier switcher, READ_ONLY connections, theme; D-21), the EP-58
small-cell components, and report bundles from EP-130/131 with review status from EP-133 sidecars
when present. The ≤ 5 s latency target (D-28) is trivially met — the ledgers are small — but is
still recorded.

## Scope sketch (refine at re-plan)

1. **`app/pages/runs.py` — Runs & Provenance** — table over the `runs.duckdb` views (`runs`,
   `protocols`, `holdouts`, `benchmarks`, `audit`, `disclosure_reviews`, as each exists) with
   filters (tier, claim type, EP tag, protocol hash, date range, actor). Run detail: manifest facts
   (git sha, `uv.lock` hash, DuckDB version, snapshot ids per layer, params, code-set / phenotype /
   protocol versions, seeds, wall time, peak RSS, disk delta, warnings), generated SQL
   (spec-templated, viewable), attrition table through `disclose.suppress` with the EP-58 badge,
   listing of `tables/` and `figures/` (aggregates) with inline Altair render via `viz/`; a lineage
   panel protocol hash → cohort id@version → snapshot ids → run → report bundle (Mermaid / Altair
   as EP-48 renders attrition).
2. **Audit and benchmark tabs** — `runs/audit.jsonl`: timestamp, actor, tier, event kind,
   statement hash, row counts (no statement text, no results); `runs/benchmarks.jsonl`: build
   steps and page latencies as tables plus Altair timing charts; an owner-only *Refresh views*
   button calling `mwh runs refresh`.
3. **`app/pages/reports.py` — Reports / export gallery** — a card per bundle under
   `runs/<run_id>/report/` and `reports/<slug>/` (claim-type badge, formats, review status
   approved / pending / denied / unchecked, run ids); inline preview of the self-contained EP-130
   HTML via `st.components.v1.html`; *Build report* for runs holding `report.json` but no output
   (EP-130 build on dev); *Download* only for approved bundles (EP-59 export gate; all enabled in
   demo mode, DESIGN §4); *Send to review* → the EP-133 queue when present.
4. **CLI parity** — `mwh runs list [--tier …]` and `mwh runs show <run_id>` (rich tables over the
   same view SQL) so agents read provenance without the app.
5. **Tests `tests/ep/test_ep134.py`** (`@pytest.mark.ep_134`, fixture) — `streamlit.testing.v1.AppTest`
   over a temporary data root seeded by the EP-35 context manager: a run appears with its manifest
   fields; the audit tab shows entries without statement text; an unapproved bundle has no download
   control while an approved one (synthetic sidecar) does; the runs page renders in < 5 s on dev.

## Out of scope

- Ledger formats and manifests → EP-35 / EP-36; review verdicts → EP-133.
- Report rendering → EP-130 / EP-131; model-registry browsing → EP-125; screenshot tooling → EP-60.

## Verification / acceptance (sketch)

- `uv run poe test -m ep_134` and `uv run --group dev mwh verify EP-134` green on fixture; on dev
  (`uv run --group ui mwh app`) the EP-31 tracer run and the EP-110 signature run are visible with
  their lineage panels.
- One full-tier page latency recorded (≤ 5 s, D-28); demo-tier screenshots of both pages via EP-60
  tooling, passing `mwh disclose check`.
- Download of an unapproved bundle is refused in the UI (observable + test).
- The lineage panel for the EP-110 full-tier signature run (protocol hash → cohort id@version →
  snapshot ids → run → model card → report bundle) is the category-38 representative artefact; its
  demo/fixture screenshot with sidecar and the `mwh runs show <run_id>` output (hashes only) are
  recorded in the completion note.

## Parked → final-roadmap.md

- MLflow local mirror UI (v2 TRK-1); `mwh reproduce <run_id>` re-computation checks (v2 PROV-1);
  benchmark-ledger dashboards (v2 BENCH-1).
