---
ep: 175
title: "Latency marts A2: itemid rollups ⏱"
size: M
model: claude-opus-5-5
effort: max
status: done
---

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
  source before launching full. *(Added at completion.)* The full build peaked at 2.8 GB
  (the daily step at 2.3 GB), so the split was not needed. The rollups are checked for shape
  and internal consistency (the crafted values, the raw counts reproduced on the fixture
  lake, the daily and summary marts agreeing), not against a clinical reference: their
  values are the EP-39 catalogue's conversions and bounds applied to the raw rows, so a
  curation error there carries into them (EP-61's QC page and EP-64's Explorer are their
  first readers). On full, the only unit string among the 51 items that the catalogue does
  not accept is a NULL unit on 14 labs, each below k rows in EP-39's report; those values
  pass through unconverted and are judged against the bounds. Chartevents charted after
  `outtime` stay on their stay (a routine choice below), which EP-56's hourly bins should
  restate or change. A spec change still rebuilds only with `--force` (EP-55's residual
  risk).

## Parked → final-roadmap.md

- Uncurated all-itemid chartevents rollups (v2 UI-2 lane) — trigger: Explorer needs bedside items
  outside the curated set. (Moved here from EP-55's Parked section at the split; mirrored into
  `final-roadmap.md` §32 at completion.)

> **Completion note (2026-11-27; claude-opus-5-5/max; ≈ 1 h 5 min, 02:53–03:58 local).**
> Built as briefed and judged at pickup a true M (the registry and the writer existed; the
> work was one values query, two specs, the wiring and the tests), so the row was not split;
> the time over M ≈ 45 min is the full job verified in-session, the dictionary regeneration
> and the safe-query gap the dev checks surfaced (decision 1). **Pickup:** `mwh doctor`
> 10 pass · 1 warn · 0 fail · 5 info, exit 0 (the expected `antivirus` warn); AC power mode
> Best performance; `last_backup` pass, 0.2 days; tree clean and `main` level with
> `origin/main`; inbox empty; OT-1 open (the CITI renewal by 2027-01-29). **Owner decision**
> (one question, the recommended option first): (1) *the audit gap* — the dev checks
> surfaced a pre-existing hole in the safe-query gate: a result column named
> `<count>_suppressed` after one of the result's count columns collided with the marker the
> suppressor adds, so polars raised after the statement had executed and before its audit
> line (GOVERNANCE §8); nothing was released, but that one dev call (an aggregate-only count
> over `marts.itemid_summary`) has no audit line, and the append-only ledger is not
> back-filled → **fix now, its own commit** (recommended): the result checks refuse the
> colliding name (audited, case-insensitive; a registry read without count columns may
> still carry `*_suppressed` columns), any exception inside `safe.SUPPRESSOR` is an audited
> refusal with sanitised text, a regression test in `test_ep175.py` § 5, the D-31 addendum,
> its own `fix(mimicwarehouse)` commit after the feat commit; rejected: a roadmap Risk
> fixed at EP-58, a new S row. **Built.** (1) `marts/registry.py`: three shapes for
> aggregate-shaped marts — a base source with `cte: true` (the builder supplies the mart's
> rows as `cte_<alias>`, `mart_sql` still generates the cast SELECT list), `join: read`
> sources (bound and documented, never joined; a column family may not read one) and the
> summary grain `itemid` (`SUMMARY_GRAINS`, never partitioned); EP-55's shapes unchanged.
> (2) `marts/itemid_rollups.py`: `rollup_items` (the catalogue's 27 chartevents and 24
> labevents itemids), `check_source_refs` (47 found in their vendored concept file under
> `concepts/vendor/mimic-code/`, 4 dictionary-verified: 50852, 50960, 50970, 226531; a
> mismatch refuses the build), `values_sql` (conversions inline through
> `phenotypes.compiler.harmonised_value_sql`, the unit string normalised only on the rows
> of items with a non-identity conversion; chartevents by `stay_id`, labevents by subject
> inside `[intime, outtime)`; `icu_day` through `timesem.sql_hours_since` /
> `sql_hour_bin`; days < 0 dropped; bounds inclusive), `daily_cte_sql`,
> `summary_aggregate_sql` / `compute_summary` / `release_summary` (`safe.SUPPRESSOR` over
> both counts; counts, share and percentiles blanked on withheld rows, `suppressed`, `k`),
> and the two step handlers (the source-ref check, uncached core views, `write_mart`; the
> summary's scan measured as its own `kind: query` line, `marts.itemid_summary.aggregate`).
> (3) `marts/specs/itemid_daily.yaml` and `itemid_summary.yaml`, 12 columns each. (4)
> `dag/specs/marts.yaml`: the two steps (tag `marts`, targets, the six dependencies the
> specs list) and the catalog step after them. (5) `safe.REGISTRY_TABLES` gains
> `marts.itemid_summary` (the brief's acceptance read; the D-33 addendum). (6) Records: the
> DESIGN §12 and §15 notes, the workspace README's `marts/` and `safe.py` rows and Layout,
> the roadmap README's name-corrections row, the D-31 and D-33 addenda with their
> status-index rows, `docs/gotchas.md` §1 (a direct read of a bucketed mart needs
> `hive_partitioning = false`), `final-roadmap.md` §32 (the uncurated rollups, v2 UI-2,
> mirrored from the Parked section), `DATA-DICTIONARY.md` regenerated from the full catalog
> (31 tables, 3 marts, 520 columns) with its sidecar. **Routine choices** (the session's
> call, each reversible through a spec version): `key` is the row's unique key
> `subject_id, stay_id, itemid, icu_day` (the brief listed the identifier keys; `is_key`
> adds the partition); a value is a row with a non-NULL `valuenum`, so `n + n_implausible`
> counts those; a unit the catalogue does not accept passes through unconverted and is
> judged against the bounds (the macro's and EP-44's semantics); `first` / `last` break
> charttime ties by value (lowest first, highest last: `arg_min` / `arg_max` over
> `(charttime, value)`, deterministic and streaming); chartevents are not trimmed at
> `outtime` (the brief windows the labs only); a lab inside two overlapping stays counts
> for both (EP-45's rule); the summary's counts and percentiles are over the plausible
> values, so they agree with the daily `n`; `pct_stays_measured` is a percentage of every
> ICU stay on the tier; `quantile_cont` is exact; an unmeasured itemid keeps its row with
> zero counts; `suppressed` / `k` follow the EP-39 variants table; the summary's base
> `relation` names `meta.item_units` (documentation: the catalogue that table publishes);
> the raw per-itemid counts are not persisted. **Churn-rule edits:** `test_ep55` — four
> pins that read "one mart" (the registry's names, the `no mart` probe that used
> `itemid_daily`, `meta.mart_tables` read whole in two tests, the dictionary's mart count)
> now name the first-day mart or the registry's length; `mwh verify EP-55` 20 passed.
> **Gates:** `mwh verify EP-175` 18 passed (fixture, ≈ 100 s); the four dev-marked tests
> (EP-175's two, EP-55's two) passed; `poe roadmap-check --strict` OK, 176 rows, 176
> briefs, 67 done, 0 errors, 0 warnings; `mwh guard` clean over the 18 touched files; the
> final `poe check` over everything committed here, the fix included: ruff clean, 226 files
> formatted, pyright 0 errors, 1,314 passed, 62 deselected, 1 warning (the crafted
> torn-ledger test), 979 s. **Timings:**
>
> | tier | build | rows (`itemid_daily`) | files | bytes | step wall daily / summary | peak RSS (step) |
> |---|---|---:|---:|---:|---:|---:|
> | dev | `20261127T080853-dev-99a0008` | 537,715 | 5 | 4,793,911 | 1.3 s / 2.0 s | 112 MB / 188 MB |
> | full | `20261127T081928-full-99a0008` | 11,066,671 | 100 | 98,439,624 | 15.4 s / 7.0 s | 2,274 MB / 889 MB |
>
> **Full ⏱ job:** `ep175-rollups-full`, pid 64964, launched 2026-11-27 03:19:27 −05:00,
> finished 08:19:57 UTC with exit 0 (build wall 28.7 s, run `20261127T081928Z-90f904`,
> peak RSS 2,763 MB, run disk delta 97.7 MB, marts/full snapshot `a0fcb4ed…`); log
> `C:\mimicdata\runs\jobs\ep175-rollups-full.log`. The build carries `99a0008` with a dirty
> tree (the uncommitted EP-175 code the feat commit holds). Verified through `safe_query`:
> 11,066,671 rows over the 51 itemids, no `icu_day < 0`, no empty row; the summary's 51
> rows, none withheld, none unmeasured; `SELECT itemid, n_stays FROM marts.itemid_summary`
> returns without refusal. EP-56's item 1 (verify EP-175's job) is met here. **Commits:**
> the feat commit, the safe-query fix (both hashes on the roadmap row) and the record
> commit; none pushed.
