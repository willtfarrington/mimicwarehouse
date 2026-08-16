# EP-55 — Latency marts A: first-day features + itemid rollups ⏱

**Size:** M · **Tier:** fixture+dev (full ⏱ → verified by EP-56) · **Core/Stretch:** core · **Depends on:** EP-38 (Concept fixes/ports for DuckDB 1.5.x), EP-39 (Itemid dictionary curation + unit harmonization) · **Blocks:** EP-56 (Latency marts B: hourly bins + <=5 s benchmark), EP-102 (Model-ready dataset A: feature spec, windows, normalization, indicators)

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
3. **Itemid rollups** (`marts/itemid_rollups.py` + specs) — `marts.itemid_daily`: one row per
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
4. **DAG wiring** — steps `marts.icustay_first_day`, `marts.itemid_daily`,
   `marts.itemid_summary` in the DAG YAML location established by EP-19, depending on the
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
   has no `icu_day < 0` and `n + n_implausible` equals the raw count for a crafted fixture itemid;
   `itemid_summary` has one row per curated itemid and
   `safe_query("SELECT itemid, n_stays FROM marts.itemid_summary")` returns without refusal;
   `meta.tables` lists the three marts after `register_meta`; dev build wall time < 10 min read
   back from the benchmark ledger (dev-marked).

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
- `DATA-DICTIONARY.md` regenerated (EP-29 generator) lists the three marts with their columns.
- Dated DESIGN.md §15 note if module/step names deviate from this brief.

## Parked → final-roadmap.md

- Uncurated all-itemid chartevents rollups (v2 UI-2 lane) — trigger: Explorer needs bedside items
  outside the curated set.
- Feature-store tooling (Feast-style registry) — trigger: EP-102 finds the YAML mart registry
  insufficient for feature versioning.
