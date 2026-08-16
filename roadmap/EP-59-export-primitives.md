# EP-59 — Export primitives

**Size:** S · **Tier:** fixture · **Core/Stretch:** core · **Depends on:** EP-43 (Disclosure primitives (`disclose` module)), EP-35 (Provenance run ledger) · **Blocks:** EP-73 (Capstone #2: EDA case study + screenshots), EP-130 (Report engine A: Jinja2 → MD/HTML)

## Context

Anything that leaves `%MWH_DATA_ROOT%\runs\` for `docs/`, `reports/` or git must be
k-suppressed, carry a provenance footer, pass `mwh disclose check` and get a `.disclosure.json`
sidecar (GOVERNANCE §7, D-33, D-40). EP-43 built `disclose.suppress(df, k=11)` (complementary
suppression), `disclose.check(path)` and the sidecar writer; EP-35 built the run ledger
(`runs/<run_id>/manifest.json`, `tables/`, `figures/`, artifact registration). This brief adds
the one export path both the P4 capstone and the P8 report engine call — table and chart save
with suppression, footer and sidecar — plus a thin `mwh export` CLI. It creates
`src/mimicwarehouse/viz/export.py` (DESIGN §15 lists `viz/` from EP-64+; export starts here —
dated note). Reports and exports state the claim type and that MIMIC-IV analyses are
retrospective. `mwh guard` (EP-4) refuses `.csv`/`.parquet` outside fixtures, so promotable
formats are Markdown, JSON, PNG, SVG and Vega-Lite JSON.

## In scope

1. **`export_table(df, out_dir, *, name, run, title, claim_type, fmt=("md","json","csv"),
   k=11)`** (`src/mimicwarehouse/viz/export.py`) — `disclose.suppress(df, k)` → write
   `<name>.md` (Markdown table + footer paragraph), `<name>.json` (`{"rows": [...],
   "_provenance": {...}}`), `<name>.csv` (runs-only; never promotable) → provenance footer:
   `run_id`, tier, snapshot ids, git sha, env-lock hash, protocol hash if any, k, timestamp,
   claim-type label ∈ {exploratory, confirmatory, predictive, associational, causal} and the
   sentence "Retrospective analysis of MIMIC-IV (PhysioNet); not a prospective study." →
   `disclose.check(path)` → sidecar `<file>.disclosure.json` (EP-43 writer). On any failure:
   delete partial outputs, raise `DisclosureError`. Register each artifact on the run
   (`run.add_artifact(path, kind, sha256)`, EP-35).
2. **`export_chart(chart, out_dir, *, name, run, title, claim_type, k=11)`** — accepts an
   Altair chart or Vega-Lite dict whose inline data is aggregate: ≤ 5 000 rows, no identifier
   columns, small cells suppressed via `disclose.suppress` on the embedded data before writing;
   writes `<name>.vl.json`, `<name>.png`, `<name>.svg` with `vl-convert-python` (BSD-3; add to
   the `core` group), footer as `title.subtitle`; same check + sidecar + registration.
3. **CLI** — `mwh export table <run_id> <table> [--fmt md,json] [--out DIR]` and `mwh export
   figure <run_id> <figure> [--out DIR]` read the run's `tables/`/`figures/` artifacts (EP-35
   layout), apply the pipeline, default `--out %MWH_DATA_ROOT%\runs\<run_id>\exports\`;
   `--promote <repo-relative dir>` copies artifact + sidecar into `docs/…` or `reports/…` only
   after the check passed and only for md/json/png/svg/vl.json (`.csv` promotion refused). The
   CLI prints paths and the check summary, never table contents.
4. **Tests** `tests/ep/test_ep59.py` (`@pytest.mark.ep_59`, fixture): a frame with a 7-count
   cell exports with the cell and one complementary cell suppressed (no total backs it out);
   sidecar has `checks, k, sha256, reviewer, timestamp`; md/json footers contain claim type,
   retrospective sentence and run id; a frame with `hadm_id` → `DisclosureError` and no files
   left; a chart with 6 000 inline rows or an id column → refused; run manifest lists the
   artifacts; `mwh export table` on a fixture run writes md+json+sidecar; `--promote` of `.csv`
   refused; `mwh disclose check <exports dir>` passes.

## Out of scope

- Report engine (Jinja2 → MD/HTML) → EP-130; PDF → EP-131; templates → EP-132.
- Disclosure-review UI → EP-133; promotion of P4 artifacts into docs → EP-73.
- Plotly figure export (kaleido) → parked below.

## Verification / acceptance

- `uv run poe test -m ep_59` green on fixture; `uv run mwh verify EP-59` green.
- The exporter **refuses** crafted violations (identifier column, oversized inline chart data,
  `.csv` promotion) in tests.
- `uv run --group dev mwh disclose check %MWH_DATA_ROOT%\runs\<fixture run>\exports` passes and
  every artifact has a `.disclosure.json` sidecar.
- Dated DESIGN.md note: `viz/export.py` + `mwh export` created in EP-59.

## Parked → final-roadmap.md

- Plotly static export via kaleido — trigger: an aggregate Plotly figure needs to enter a report
  (timeline lanes are row-level and never exported).
