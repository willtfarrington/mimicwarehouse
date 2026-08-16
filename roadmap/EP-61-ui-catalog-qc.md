# EP-61 — Catalog & QC browser page

**Size:** M · **Tier:** fixture+dev+full · **Core/Stretch:** core · **Depends on:** EP-57 (App shell A (Streamlit multipage)), EP-44 (Data-quality profiling) · **Blocks:** EP-73 (Capstone #2: EDA case study + screenshots)

## Context

The first real page of the Lab app (DESIGN §16 "Catalog & QC"). Everything it shows is
metadata or pre-computed aggregate: `meta.tables`/`meta.columns`/`meta.row_counts` and the item
dictionaries from EP-29, `meta.item_units` (harmonised units, plausibility bounds) from EP-39,
`marts.itemid_summary` (coverage per itemid) from EP-55, and the EP-44 profiles
(`meta.profile_*`: null %, cardinality, ranges, duplicate keys, timestamp-ordering violations,
referential-integrity failures, unit inconsistencies, implausible values, each with a
severity flag). The shell (EP-57) provides the tier switcher, cached READ_ONLY connection and
the single query path; EP-58 provides the small-cell wrappers and lint. This brief serves
capability 1 (inventory & QC) in the UI and sets the pattern every later page follows: thin
page script in `app/pages/`, spec builders in `viz/`, all data via `ui.conn.query`, latency
recorded on full (D-28). Dictionaries (`d_items`, `d_labitems`) are not identifiers and may be
displayed; the page must never show a `subject_id`-bearing frame.

## In scope

1. **Page** `app/pages/10_catalog_qc.py` (registered as `catalog_qc`, section Data) with three
   tabs. *Tables*: schema tree from `meta.tables` (schema, table, rows, bytes, partitioning,
   snapshot id, comment) with search; selecting a table lists its columns. *Columns &
   dictionaries*: `meta.columns` (name, type, nullable, comment, null %), item dictionaries
   (`d_items`, `d_labitems` searchable by label/category) joined to `meta.item_units` (unit,
   bounds, core flag) and `marts.itemid_summary` (`n_stays`, `pct_stays_measured`, p01/p50/p99).
   *QC*: EP-44 profile tables per table → severity tiles (`ok/warn/fail` counts) → per-check
   detail frames (null %, cardinality, ranges, duplicates, ordering, RI, units, implausibles);
   every frame/metric through `safe_dataframe`/`safe_metric` (EP-58) so implausible-value
   counts < 11 are badged on dev/full.
2. **Spec builders** `src/mimicwarehouse/viz/qc.py` — `null_pct_bars(df)`, `flag_matrix(df)`
   (table × check heatmap coloured by severity), `range_strip(df)` (min/p01/p50/p99/max per
   numeric column); Altair over pre-aggregated frames with the EP-5 theme; reusable by reports.
3. **Latency** — the page reads only `meta.*`/`marts.itemid_summary`; add `catalog_tables` and
   `qc_flags` to `marts/bench_queries.yaml` (EP-56); measure the page on full with
   `MWH_APP_RECORD_LATENCY=1` and record the wall time in the completion note (≤ 5 s).
4. **Screenshots** — add manifest entries `catalog-tables`, `catalog-qc` (EP-60) and capture on
   demo (light + dark), sidecars included.
5. **Tests** `tests/ep/test_ep61.py` (`@pytest.mark.ep_61`, ui group): AppTest on fixture —
   page renders three tabs; tables list contains `mimiciv_hosp.admissions` and
   `marts.icustay_first_day`; selecting a table lists its columns; QC tab shows ≥ 1 severity
   tile; no rendered dataframe has a column in the identifier set (walk `at.dataframe`);
   `ui_lint` passes; dev-marked — page runs against the dev catalog; full latency recorded.

## Out of scope

- Computing profiles (EP-44) or measurement-process summaries (EP-45) — this page displays.
- Missingness views → EP-72; itemid curation edits → EP-39/EP-138 (read-only here).
- Data dictionary generation → EP-29.

## Verification / acceptance

- `uv run --group ui poe test -m ep_61` green (fixture; dev-marked test with dev catalog);
  `uv run --group ui mwh verify EP-61` green (includes `ui_lint`).
- On dev: tables → columns → QC drill-down works; a small-cell badge appears on a crafted or
  real < 11 implausible-value count.
- Full-tier page latency recorded in the completion note (`page_latency` ledger entry ≤ 5 s).
- `docs/screenshots/catalog-tables-{light,dark}.png`, `catalog-qc-{light,dark}.png` with
  sidecars; `mwh disclose check docs/screenshots` passes.
