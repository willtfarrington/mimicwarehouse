---
ep: 55
title: "Latency marts A: first-day features + mart registry ⏱"
size: M
model: claude-opus-5-5
effort: max
status: done
---

# EP-55 — Latency marts A: first-day features + mart registry ⏱

**Size:** M · **Tier:** fixture+dev (full ⏱ → verified by EP-56) · **Core/Stretch:** core · **Depends on:** EP-38 (Concept fixes/ports for DuckDB 1.5.x), EP-39 (Itemid dictionary curation + unit harmonization) · **Blocks:** EP-175 (Latency marts A2: itemid rollups ⏱), EP-56 (Latency marts B: hourly bins + <=5 s benchmark), EP-102 (Model-ready dataset A: feature spec, windows, normalization, indicators)

> **Split at pickup (2026-11-26; owner decision, D-2 addendum).** Judged against M ≈ 45 min
> (the D-2 addendum of 2026-11-25: EP-55 first), the six items came to ≈ 1.5–2 h, so the row
> was split before any code: this brief keeps the mart registry (item 1), `marts.icustay_first_day`
> (item 2), the wiring (item 4), the full launch (item 5) and the tests (item 6) for the first-day
> mart; **item 3, the itemid rollups (`marts.itemid_daily`, `marts.itemid_summary`), with its
> share of items 4–6, moved to EP-175** (M, core, right after this row and before EP-56, whose
> benchmark and EP-61's page read `itemid_summary`). The title changed from "first-day features
> + itemid rollups" to "first-day features + mart registry". Items below that name the rollups
> carry a "→ EP-175" mark; the rest stands as written.

> **EP-54 pickup note (2026-09-17).** Header facts unchanged (EP-38 ☑ `c5c0b0c`, EP-39 ☑
> `963ab67`); read the body against `roadmap/README.md` § "P3 → P4 name corrections" — the
> shipped names override the text. Brief-specific: (1) **layout** — the marts are per tier,
> `lake/marts/<tier>/icustay_first_day/…` etc. (item 2's `lake\marts\icustay_first_day\
> subject_bucket=NN\` gains the tier segment; the cohort marts already sit at
> `lake/marts/<tier>/cohorts/`), and the catalog registers them through a `register_marts`
> extension of your own appended to `catalog.build.CATALOG_EXTENSIONS` (the EP-47 one
> registers the cohorts; there is no `register_meta`) — `meta.tables` / `meta.columns` are
> EP-29's core-table registry, so add the marts to `DATA-DICTIONARY.md` through
> `catalog/dictionary.py` rather than `meta.tables`. (2) **DAG** — a new spec file
> `dag/specs/marts.yaml` (the EP-37 multi-spec discovery merges it; tag `marts`, which
> `cohorts.build` already carries, so `mwh build --tier t --tag marts` builds cohorts + your
> marts; `--select marts.<step>`; no `--target`, no `--resume`: steps skip when complete);
> depend on the concept steps by their spec ids (`concept.firstday.first_day_vitalsign`,
> …) — the blood-gas concept is **`bg` / `first_day_bg` / `first_day_bg_art`**, not
> `blood_gas`. (3) **Units** — `meta.item_units` has no `core` column: the curated flag is
> `meta.item_dictionary.curated` / `concept_group` (EP-39), bounds are `units.bounds`; do
> **not** call the `mwh_harmonize` macro per row over an event table (43 s per predicate on
> the fixture, `docs/gotchas.md` §1) — inline the conversions as the phenotype compiler
> does (`phenotypes.compiler.harmonised_value_sql` is the template). (4) **Inputs that exist
> now**: `timeline.to_mart(relation, path)` is the Parquet writer shape (`publish.swap_file`
> underneath), `timeline.hourly_bins` / `timesem.sql_hour_bin` the bin helpers,
> `mimiciv_derived.icustay_hourly` a mimic-code stay × hour grid you may join rather than
> rebuild, `mimiciv_derived.spine` a substitute event source (280 M rows, 13 sources, no
> chartevents). (5) **Provenance** — the CLI build already runs inside `run.start(kind=
> "build")`; add one `run.bench(kind="mart", …)` line per mart (the EP-47 shape) and
> `record_snapshot_once` for the `marts` layer id. (6) **Size budget** — DESIGN §3 now
> measures derived + spine at ≈ 2.2 GB; marts ≤ 1–2 GB is the remaining line. Full ⏱ job
> name `ep55-marts-full`; EP-56 verifies.
## Context

The marts layer (DESIGN §3: `lake\marts\…` + schema `marts` in every tier catalog) is what lets
the Lab app answer typical questions in ≤ 5 s on the full tier (D-28). Nothing in `marts` exists
yet. What does exist: the vendored mimic-code concepts materialised per tier into `mimiciv_derived`
by EP-37/38 (`icustay_detail`, `first_day_vitalsign`, `first_day_lab`, `first_day_bg_art`,
`first_day_gcs`, `first_day_urine_output`, `first_day_weight`, `first_day_sofa`, `first_day_rrt`,
`ventilation`, `vasoactive_agent`, `charlson`, `sepsis3`, `kdigo_stages`, count-pinned on demo/dev),
EP-39's curated `meta.item_units` (harmonised units, plausibility bounds, core vitals/labs flag)
and `units.py`, EP-34's grain registry (`icustay`, `icu_day`, …), and the DAG runner `mwh build`
(EP-19, D-20) with manifests, snapshot ids and the benchmark ledger. This brief builds the first
two marts — a wide stay-grain "first-day" table and per-itemid daily rollups — as DAG steps so
they carry manifests and snapshot ids (D-17, D-18), then launches the full-tier build as a
background ⏱ job that EP-56 verifies. EP-102 derives model-ready feature matrices from these
marts, so the column naming and unit conventions fixed here persist. MIMIC caveats: ages ≥ 89
appear as 91 (keep as shipped, document); the only cross-patient temporal axis is
`anchor_year_group` (carried as `era`); first-day windows are relative to `icu_intime`, never
calendar dates. Machine: full chartevents rollups read ~4×10⁸ rows — background job only,
DuckDB `memory_limit`/`threads`/`temp_directory` from `config.py`, ≥ 100 GB free.

## In scope

1. **Mart registry** (`src/mimicwarehouse/marts/__init__.py`, `marts/registry.py`,
   `marts/specs/<mart>.yaml`) — one YAML spec per mart: `name`, `grain` (EP-34 registry key),
   `key` columns, `partition` (`subject_bucket` for subject-keyed marts, none for summaries),
   `sources` (layer + tables/concepts read), `columns` (name, DuckDB type, unit, description,
   source expression), `depends_on` DAG steps. `registry.load()` validates specs with pydantic;
   `registry.register_meta(tier)` writes mart rows into `meta.tables`/`meta.columns` (EP-29) so
   `DATA-DICTIONARY.md` regenerates with the marts.
2. **`marts.icustay_first_day`** (`marts/first_day.py` + spec) — one row per `stay_id`, grain
   `icustay`. Keys `subject_id, hadm_id, stay_id, subject_bucket`; demographics/admission from
   `icustay_detail` + `patients`/`admissions`/`services` (`admission_age` (91 for ≥ 89), `gender`,
   `race`, `admission_type`, `insurance`, `first_service`, `first_careunit`, `era` =
   `anchor_year_group`, `first_hosp_stay`, `first_icu_stay`, `icustay_seq`); durations only
   (`los_icu_days`, `los_hospital_days`, `hours_admit_to_icu`) plus `icu_intime` as the naive
   anchor timestamp; outcomes (`hospital_expire_flag`, `discharge_location`); first-24 h vitals
   `<var>_{min,max,mean}` for heart_rate, sbp, dbp, mbp, resp_rate, temperature (°C), spo2,
   glucose; `first_day_lab` min/max columns as shipped; arterial BG `ph_*`, `lactate_*`,
   `pao2fio2ratio_*`; `gcs_min`; `urineoutput`; `weight_admit`; `sofa` + its six sub-scores;
   `charlson_comorbidity_index`; flags `vent_invasive_first_day`, `vasopressor_first_day`,
   `rrt_first_day`, `sepsis3` (onset within the stay), `kdigo_max_stage_first_day`. Names are
   snake_case `<concept>_<stat>`; units per `meta.item_units`. Parquet under
   `lake\marts\icustay_first_day\subject_bucket=NN\` (sorted `subject_id, stay_id`, ZSTD-3) +
   catalog view `marts.icustay_first_day`.
3. **→ EP-175 (split at pickup).** **Itemid rollups** (`marts/itemid_rollups.py` + specs) — `marts.itemid_daily`: one row per
   (`stay_id`, `itemid`, `icu_day`) for the curated core itemid set (EP-39 `meta.item_units.core`;
   the itemid lists come from the vendored concept SQL for `vitalsign`, `chemistry`,
   `complete_blood_count`, `blood_gas`, `coagulation`, `enzyme` — read them from `concepts/`,
   never retype), sources `mimiciv_icu.chartevents` and `mimiciv_hosp.labevents` (labs attributed
   to the ICU stay whose window contains `charttime`), unit-harmonised via `units.py`, columns
   `n, min, max, mean, first, last, n_implausible` (values outside EP-39 bounds are counted and
   excluded from stats), `icu_day = floor(hours since icu_intime / 24)`, days < 0 dropped;
   partitioned by `subject_bucket`. `marts.itemid_summary`: one unpartitioned row per curated
   itemid with `label, source_table, unit, n_stays, n_measurements, pct_stays_measured, p01,
   p50, p99` — the small aggregate table pages use for variable pickers (no identifiers).
4. **DAG wiring** — steps `marts.icustay_first_day`, `marts.itemid_daily` (→ EP-175),
   `marts.itemid_summary` (→ EP-175) in the DAG YAML location established by EP-19, depending on the
   EP-37 concept steps and EP-39's `meta.item_units`; tier-aware; write manifests + snapshot id;
   append build timings to `runs/benchmarks.jsonl`; idempotent rebuild (write the mart directory
   as `.new` and swap; catalog views re-pointed by the EP-21 builder). `uv run --group dev mwh
   build --tier dev --target marts` builds all three.
5. **Full ⏱ launch** — after fixture+dev are green, launch `mwh build --tier full --target
   marts` as a background job (EP-19/EP-23 convention) with log
   `%MWH_DATA_ROOT%\runs\jobs\ep55-marts-full.log`; do not wait for it; record job id, log path
   and launch time in this brief's completion note. EP-56 verifies counts, wall time, peak RSS
   and disk delta and appends them here.
6. **Tests** `tests/ep/test_ep55.py` (`@pytest.mark.ep_55`; fixture default, `dev` where marked):
   registry specs validate and every declared column exists with its declared type in the built
   mart; `stay_id` unique and row count == `count(*)` of `mimiciv_icu.icustays` (fixture; dev);
   `temperature_mean` inside EP-39 plausibility bounds and `admission_age ≤ 91`; `itemid_daily`
   has no `icu_day < 0` and `n + n_implausible` equals the raw count for a crafted fixture itemid
   (→ EP-175); `itemid_summary` has one row per curated itemid and
   `safe_query("SELECT itemid, n_stays FROM marts.itemid_summary")` returns without refusal
   (→ EP-175); `meta.tables` lists the three marts after `register_meta` (as built:
   `meta.mart_tables`, written by the catalog extension `register_marts`, lists the first-day
   mart; EP-175 adds the other two); dev build wall time < 10 min read back from the benchmark
   ledger (dev-marked).

## Out of scope

- Hourly bins, population quantiles and the page-query benchmark → EP-56 (Latency marts B).
- Feature windows, normalisation, indicators, feature dictionary → EP-102/103.
- ED-derived features → EP-142/144; cohort-specific study marts → EP-47/EP-102.
- Timeline/event-aligned queries → EP-49; anything reading raw CSVs (marts read the catalog only).

## Verification / acceptance

- `uv run poe test -m ep_55` green on fixture and dev (dev catalog present; `mwh build --tier dev
  --target marts` completed with its wall time in the completion note); `uv run --group dev mwh verify EP-55`
  green.
- `uv run --group dev mwh sql --tier dev "SELECT count(*) AS n FROM marts.icustay_first_day"`
  returns one count through safe_query (nothing else printed).
- Full ⏱: launched `mwh build --tier full --target marts` in the background; log at
  `%MWH_DATA_ROOT%\runs\jobs\ep55-marts-full.log`; job id recorded here; timing verified by EP-56.
- `DATA-DICTIONARY.md` regenerated (EP-29 generator) lists the three marts with their columns
  (as split: the first-day mart; EP-175 adds the rollups).
- Dated DESIGN.md §15 note if module/step names deviate from this brief.
- **Residual risk.** *(Added at completion.)* The first-day flags derived here (invasive
  ventilation, vasopressors, the KDIGO stage) are checked for shape on fixture, dev and full
  (never NULL, stages 0–3) but not against a clinical reference; their definitions are mimic-code's
  own conventions, recorded in the spec's `sources` notes, and EP-102 can tighten them through a
  spec version. A spec change rebuilds only with `--force` on each tier (the step's target skips a
  complete mart); the status entry carries the `spec_sha256` it was built from, but nothing yet
  warns when it differs from the packaged spec. `DATA-DICTIONARY.md` now renders the marts from
  `meta.mart_*`, so EP-175's build must regenerate it again from the full catalog.

## Parked → final-roadmap.md

- ~~Uncurated all-itemid chartevents rollups (v2 UI-2 lane)~~ — moved to EP-175's Parked section
  with the rollups at the split.
- Feature-store tooling (Feast-style registry) — trigger: EP-102 finds the YAML mart registry
  insufficient for feature versioning. (Mirrored into `final-roadmap.md` §34–35 as v2 MRD-2.)

> **Completion note (2026-11-26; claude-opus-5-5/max; ≈ 1 h 25 min, 22:31–23:58 local).**
> Built as split at pickup, against the M budget of 45 min: the overrun is verification the
> plan gave to EP-56 (the full job finished inside the session and was checked here), two
> gate runs superseded because the code changed under them, and the supervisor race the final
> gate surfaced (decision 2). **Pickup:** `mwh doctor` 10 pass ·
> 1 warn · 0 fail · 5 info, exit 0 (the `antivirus` warn: Malwarebytes reports real-time off
> through Security Center, Defender on — the FYI EP-174's evening already carried); AC power
> mode Best performance; `last_backup` pass, 0.0 days (OT-2's backup; the owner's tick of OT-2
> sat uncommitted in `owner-tasks.md`: decision 3). Inbox empty; OT-1 open (the
> CITI renewal by 2027-01-29). **Owner decisions** (two rounds, the recommended option first):
> (1) *the size* — EP-55 judged at ≈ 1.5–2 h against M ≈ 45 min → **split off the rollups**
> (recommended): EP-175 (M, core) between EP-55 and EP-56, this row retitled "first-day
> features + mart registry", EP-56 depending on both, the D-2 addendum, committed as
> `3360d84`; rejected: three rows, running the brief whole. (2) *the flaky test* — the final
> gate's one failure was EP-173's `test_dag9_failed_job_is_recorded_with_a_failed_provenance_run`:
> `dag.jobs._supervise` rewrote the state file to `failed` before printing its closing
> `[job] … exit code=…` line, so a reader that waits for the state and then reads the log
> could miss it under load (it passed in the first run and alone) → **fix the race now**
> (recommended): the two steps swapped (the line is printed and flushed first), its own
> `fix(mimicwarehouse)` commit after the feat commit, `mwh verify EP-173` 35 passed and `mwh
> verify EP-19` 8 passed after it (EP-19's launch test had the same read); rejected: a
> roadmap Risk for EP-74, a new S row. (3) *the owner's OT-2 tick* (uncommitted in
> `owner-tasks.md`) → **included in the record commit**, named there; rejected: leaving it
> for the owner to commit. **Built.** (1) `marts/registry.py`: `MartSpec` / `MartSource` /
> `MartColumn` / `ColumnFamily` (pydantic, `extra="forbid"`; grain from `timesem.GRAINS`; one
> base source first; key / sort / partition declared, the partition INTEGER), `check_units`
> (a unit tied to an EP-39 `concept_group` must be its canonical unit), `load_registry` /
> `packaged_registry`, `mart_sql` (the SELECT list, every `expr` cast to its declared type,
> over the base table + one LEFT JOIN per source, `cte: true` sources as `cte_<alias>`),
> `write_mart` (materialised once into a temp table, one sorted single-file `COPY` per
> `subject_bucket` or one file unpartitioned, `.new` + `publish.swap_dir`, manifest lines of
> schema `marts`, `manifest.json` with spec / SQL hashes and the core / derived snapshot ids,
> the per-tier status `marts.<name>` with its `spec_sha256`, failure recorded and the
> published mart kept, one `kind: mart` ledger line, the marts snapshot id), `register_marts`
> (after `register_spine` in `CATALOG_EXTENSIONS`: `marts.<name>` views with view and column
> comments, `meta.mart_tables` with a `stale` flag and `meta.mart_columns`). (2)
> `marts/specs/icustay_first_day.yaml`: 154 columns over `icustays` + 17 sources (14 concepts,
> patients, admissions, services), families for the 24 vitals, 86 labs and 6 blood-gas
> columns. (3) `marts/first_day.py`: the four CTEs (first service, invasive ventilation,
> vasopressors, the KDIGO stage) and the step handler (core views bound uncached, concept views
> refused with the remedy). (4) `dag/specs/marts.yaml`: `marts.icustay_first_day` (tag
> `marts`, target, the spec's 18 dependencies) and the shared catalog step. (5)
> `catalog/dictionary.py`: the `## marts` section from `meta.mart_*` and
> `DictionaryResult.marts`, so the CLI reports `31 table(s), 1 mart(s)`. (6) Records: the
> DESIGN §15 module-map row and dated note (the names as built), the workspace README's State
> row and Layout, the roadmap README's name-corrections row, `final-roadmap.md` v2 MRD-2,
> `DATA-DICTIONARY.md` regenerated from the full catalog with its sidecar. **Routine choices**
> (the session's call, each reversible through a spec version): the lab glucose keeps
> `glucose_min/max` as shipped and the charted one is `glucose_chart_{min,max,mean}` (the
> brief's two literal names collide); every column cast to its declared type (mimic-code's
> DECIMAL temperatures, absolute counts and weights become DOUBLE), `hospital_expire_flag`
> stays the shipped 0/1 INTEGER, the five new flags are BOOLEAN and never NULL;
> `admission_age` capped by the `timesem` rule with an added `age_capped`; the derived flags
> use mimic-code's own first-day window (−6 h … +24 h, inclusive), vasopressors are
> norepinephrine, epinephrine, dopamine, phenylephrine and vasopressin (no inotropes),
> invasive ventilation is mimic-code's `InvasiveVent` (its scores' convention; tracheostomy
> not counted), RRT is `dialysis_active`, the KDIGO stage is NULL when none was charted;
> `los_hospital_days` fractional from admit to discharge, `los_icu_days` = `icustays.los` as
> shipped; no unit on the 28 lab variables and the PaO2/FiO2 ratio the catalogue does not
> curate (mimic-code ships them in their native unit); `icustays` as the base table, so the row count is the stay count
> by construction; `subject_bucket` kept inside each file; the mart registry tables in `meta`
> rather than EP-29's `meta.tables`; a spec moved after a build gives a `stale` row and a view
> over the built columns, never a failed catalog build; the full job checked here, so EP-56's
> amendment moves its item 1 to EP-175's job. **Churn-rule edits:** `test_ep47` (its pin of
> `--tag marts` to exactly `[cohorts.build, catalog]` now names the cohort build, catalog last,
> every other step a `marts.` step — D-47 planned EP-55's marts to join that tag) and
> `test_ep29` (the dictionary-row allow-list gains the declared mart columns); `mwh verify
> EP-47` 7 passed, `mwh verify EP-29` 13 passed. **Gates:** `mwh verify EP-55` 20 passed
> (fixture, 118 s); the two dev-marked tests passed (counts, bounds, the ledger's wall time);
> `poe roadmap-check --strict` OK, 176 rows, 176 briefs, 66 done, 0 errors, 0 warnings; `mwh
> guard` clean over the 20 touched files; the final `poe check` (all EP-55 code, before the
> supervisor fix): ruff clean, 224 files formatted, pyright 0 errors, **1,295 passed, 1
> failed** (decision 2), 60 deselected, 1 warning (the crafted torn-ledger test), 1,105 s; it
> was not re-run after the two-line fix, whose own tests were (above). **Timings:**
>
> | tier | build | rows (= `icustays`) | files | bytes | step wall | ledger wall | peak RSS |
> |---|---|---:|---:|---:|---:|---:|---:|
> | dev | `20261127T035838-dev-dda8251` | 4,672 | 5 | 1,059,580 | 1.2 s | 0.6 s | 367 MB |
> | full | `20261127T035910-full-dda8251` | 94,458 | 100 | 21,376,025 | 3.8 s | 2.9 s | 914 MB |
>
> **Full ⏱ job:** `ep55-marts-full`, pid 51616, launched 2026-11-26 22:59:08 −05:00, finished
> 03:59:21 UTC with exit 0 (build wall 11.2 s, run `20261127T035910Z-851764`, run disk delta
> 22.0 MB, marts/full snapshot `ff633471…`); log `C:\mimicdata\runs\jobs\ep55-marts-full.log`.
> Verified through `safe_query`: 94,458 rows = 94,458 distinct stays = `mimiciv_icu.icustays`;
> the highest `admission_age` 91; no `temperature_mean` outside the EP-39 bounds (10–50 degC).
> After the `stale` column was added, `ep55-catalog-full` (catalog only, 10 s) rebuilt the full
> catalog (`20261127T041001-full-3360d84`, the dictionary's build) and the dev catalog was
> rebuilt the same way. **Commits:** `3360d84` (the split), the feat commit, the supervisor fix
> (both hashes on the roadmap row) and the record commit with the owner's OT-2 tick; none
> pushed.
