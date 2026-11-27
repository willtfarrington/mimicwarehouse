# EP-175 — Latency marts A2: itemid rollups ⏱

**Size:** M · **Tier:** fixture+dev (full ⏱ → verified by EP-56) · **Core/Stretch:** core · **Depends on:** EP-55 (Latency marts A: first-day features + mart registry ⏱), EP-39 (Itemid dictionary curation + unit harmonization) · **Blocks:** EP-56 (Latency marts B: hourly bins + <=5 s benchmark), EP-61 (Catalog & QC browser page), EP-64 (Explorer A: server-side aggregation service + VegaFusion)

## Context

Split from EP-55 at its pickup (2026-11-26; the owner's choice, D-2 addendum of that day):
EP-55 proved larger than M ≈ 45 min, so its item 3, the itemid rollups, became this row,
placed right after EP-55 and before EP-56 because EP-56's page-query benchmark
(`itemid_summary` lookup) and EP-61's item view read `marts.itemid_summary`. This brief
carries EP-55's item 3 and the parts of its items 4–6 that concern the rollups; read it against
`roadmap/README.md` § "P3 → P4 name corrections" (the shipped names override planning text).

**What exists (EP-55, `marts/`).** The mart registry: one YAML spec per mart under
`src/mimicwarehouse/marts/specs/` (`MartSpec`, pydantic: name, grain, key, partition, sort,
builder, depends_on, sources, columns with type / unit / EP-39 `concept_group` / description /
source expression, and `vars x stats` column families); `registry.mart_sql(spec, ctes)` (the
SELECT list cast to the declared types over a base table and left joins, `cte: true` sources
read the builder's `cte_<alias>`); `registry.write_mart(ctx, spec, sql, layers_read=…)` (the
statement materialised once, one sorted single-file `COPY` per `subject_bucket` or one file for
an unpartitioned mart, `.new` + `publish.swap_dir`, manifest lines, `manifest.json`, the
per-tier status `marts.<name>`, one `kind: mart` benchmark line, the marts snapshot id); the
catalog extension `registry.register_marts` (`marts.<name>` views over complete marts with
column comments, `meta.mart_tables` / `meta.mart_columns`, rendered by `DATA-DICTIONARY.md`);
`dag/specs/marts.yaml` (tag `marts`; each step's `target` is its status key, so a complete mart
is skipped and `--force` rebuilds). A new mart is a spec, a builder module and one step.

**Inputs.** `mimiciv_icu.chartevents` (433 M rows full) and `mimiciv_hosp.labevents`; the
EP-39 catalogue (`units.py`, `data/item_units.yaml`: 63 curated itemids in 37 concept groups,
each with a canonical unit, accepted unit strings → affine conversions, inclusive plausibility
bounds and a `source_ref` naming the mimic-code concept file it comes from). `meta.item_units`
has no `core` column: the curated set is the catalogue (`meta.item_dictionary.curated`). The
`mwh_harmonize` macro is for point lookups only (43 s per predicate on the fixture,
`docs/gotchas.md` §1): inline the conversions the way `phenotypes.compiler.
harmonised_value_sql` does — one `CASE` over a unit string normalised once per row.

## In scope

1. **`marts.itemid_daily`** (`marts/itemid_rollups.py` + `marts/specs/itemid_daily.yaml`) — one
   row per (`stay_id`, `itemid`, `icu_day`) for the curated itemids of `chartevents` and
   `labevents` (the catalogue's `source` field; outputevents / inputevents items stay out), each
   curated itemid checked against its `source_ref` concept file under `concepts/` (read, never
   retyped); labs attributed to the ICU stay whose `[intime, outtime)` contains `charttime`
   (subject + window join; a lab outside every stay is dropped); values unit-harmonised inline;
   columns `n, min, max, mean, first, last, n_implausible` (values outside the EP-39 bounds are
   counted in `n_implausible` and excluded from the statistics); `icu_day = floor(hours since
   icu_intime / 24)` through `timesem.sql_hours_since`, days < 0 dropped; keys `subject_id,
   stay_id, subject_bucket`, partitioned by `subject_bucket`, sorted (`subject_id, stay_id,
   itemid, icu_day`).
2. **`marts.itemid_summary`** (`marts/specs/itemid_summary.yaml`, unpartitioned) — one row per
   curated itemid with `label, source_table, unit, n_stays, n_measurements, pct_stays_measured,
   p01, p50, p99`: the small aggregate table pages use for variable pickers (no identifiers);
   counts below k = 11 blanked at build time as the other registry-shaped tables are (D-33).
3. **DAG wiring** — steps `marts.itemid_daily` and `marts.itemid_summary` in
   `dag/specs/marts.yaml` (tag `marts`, targets, depends_on mirroring the specs: the two event
   tables, `stage.mimiciv_icu.icustays`, the `units.*` steps), the catalog step after them.
   `uv run --group dev mwh build --tier dev --tag marts` builds them (the first-day mart and the
   cohorts skip when complete).
4. **Full ⏱ launch** — after fixture + dev are green: `mwh build --tier full --tag marts
   --background --job ep175-rollups-full` (log `runs\jobs\ep175-rollups-full.log`); record the
   job id, log path and launch time in the completion note; EP-56 verifies counts, wall time,
   peak RSS and disk delta unless the job finishes inside this session.
5. **Tests** `tests/ep/test_ep175.py` (`@pytest.mark.ep_175`; fixture default, `dev` where
   marked): both specs validate and every declared column exists with its type in the built
   marts; `itemid_daily` has no `icu_day < 0`; `n + n_implausible` equals the raw count for a
   crafted fixture itemid (a crafted lake with values on both sides of a bound and one in an
   accepted non-canonical unit); a lab outside every stay window is dropped; `itemid_summary`
   has one row per curated chartevents / labevents itemid and
   `safe_query("SELECT itemid, n_stays FROM marts.itemid_summary")` returns without refusal;
   `meta.mart_tables` lists all three marts; the dev build's wall time < 10 min from the
   benchmark ledger (dev-marked).

## Out of scope

- Hourly bins, population quantiles and the page-query benchmark → EP-56.
- Feature windows, normalisation, indicators → EP-102/103; anything reading raw CSVs.
- The first-day mart and the registry (EP-55, shipped).

## Verification / acceptance

- `uv run poe test -m ep_175` green on fixture and dev; `uv run mwh verify EP-175` green;
  `mwh verify EP-55` still green.
- `uv run --group dev mwh sql --tier dev "SELECT count(*) AS n FROM marts.itemid_daily"` returns
  one count through safe_query.
- `DATA-DICTIONARY.md` regenerated from the full catalog once the full job has finished (or by
  EP-56), with its disclosure sidecar.
- Full ⏱: launched in the background; job id and log recorded here.
- **Residual risk.** chartevents is the largest scan the marts make (433 M rows full): the
  per-row unit normalisation must stay inline (no macro), and the full job's peak RSS stays
  under the 36 GB build `memory_limit`; if the dev build shows otherwise, split the step per
  source before launching full.

## Parked → final-roadmap.md

- Uncurated all-itemid chartevents rollups (v2 UI-2 lane) — trigger: Explorer needs bedside items
  outside the curated set. (Moved here from EP-55's Parked section at the split.)
