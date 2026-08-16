# EP-37 — Concept runner (mimic-code concepts_duckdb → mimiciv_derived) ⏱

**Size:** M · **Tier:** fixture+dev (full ⏱ → verified by EP-38) · **Core/Stretch:** core · **Depends on:** EP-8 (mimic-code vendoring), EP-19 (DAG runner `mwh build`), EP-22 (Demo tier (MIMIC-IV Demo 2.2 + ED Demo)) · **Blocks:** EP-38 (Concept fixes/ports for DuckDB 1.5.x), EP-54 (Re-plan P3)

## Context

D-19: adopt mimic-code's `concepts_duckdb/` (MIT, ~65 sqlglot-transpiled concepts: demographics
`icustay_detail/icustay_times/icustay_hourly/age/weight_durations`, measurement `vitalsign/bg/
chemistry/complete_blood_count/gcs/urine_output/…`, comorbidity `charlson`, medication
`vasoactive_agent/norepinephrine_equivalent_dose/antibiotic/…`, organfailure `kdigo_creatinine/
kdigo_uo/kdigo_stages/meld`, treatment `ventilation/crrt/rrt/invasive_line`, firstday `first_day_*`,
score `sofa/sapsii/apsiii/oasis/lods/sirs`, sepsis `suspicion_of_infection/sepsis3`), vendored at a
pinned commit by EP-8 with attribution in `NOTICE`. This brief runs them per tier into
`mimiciv_derived` through the DAG runner (EP-19, D-20) so every derived table gets a manifest line
and snapshot id, count-pins them on the demo tier (ODbL, committable) and the dev tier, and
launches the full-tier build as a logged background job that EP-38 verifies. Known hazards
(README Risks 2): `concepts_duckdb` lags upstream `concepts/` and its README targets DuckDB 1.4
LTS while we pin 1.5.x — expect some concepts to fail; **do not fix them here** (EP-38), record
them. Full staging was verified by EP-28, so `chartevents`-based concepts (`vitalsign`, `gcs`,
`ventilator_setting`, `weight_durations`, `height`) can run on full. Machine: 64 GB RAM →
`memory_limit` 36–40 GB, `threads` 12, temp on C:, ≥ 100 GB free (DESIGN §6); foreground
shell cap ~10 min → the full run is background-only.

## In scope

1. **Concept inventory + order** (`src/mimicwarehouse/concepts/inventory.py`) — enumerate the
   vendored `.sql` files (EP-8 vendored them under `src/mimicwarehouse/concepts/vendor/mimic-code/
   mimic-iv/concepts_duckdb/<group>/<concept>.sql`, preserving upstream paths — read, never move)
   into a `Concept` record (name, group, path, sql_sha256, upstream_commit from
   `concepts/vendor/VENDOR.json`) and a topological order derived by regex-scanning each file
   for `mimiciv_derived.<x>` references (fallback: upstream make-script order); assert the graph
   is acyclic; write the inventory to `src/mimicwarehouse/concepts/concepts.yaml` (generated,
   committed).
2. **Runner as a DAG** (`src/mimicwarehouse/concepts/runner.py` + spec
   `src/mimicwarehouse/dag/specs/concepts.yaml`, generated from the inventory: one step
   `concept.<group>.<name>` per concept, kind `sql`, tag `concepts`, `depends_on` from item 1;
   run with `mwh build --tier <t> --tag concepts`) — each step strips upstream's `DROP TABLE … ;
   CREATE TABLE mimiciv_derived.<x> AS` header, runs the SELECT body against the tier catalog with
   the explicit DuckDB config, and sinks to Parquet under `lake/derived/<tier>/concepts/<concept>/`
   (single file; `PARTITION_BY subject_bucket` when the concept has `subject_id` and > 5 M rows —
   `vitalsign`, `bg`, `chemistry`, `complete_blood_count`, `blood_differential`, `enzyme`,
   `coagulation`, `urine_output`, `icustay_hourly`, `kdigo_creatinine`); the demo tier follows
   the same rule (`lake/derived/demo/…`; EP-22 gave demo its own lake root). Catalog registration =
   a generic discovery extension for EP-21's `build_catalog` (via EP-34's `CATALOG_EXTENSIONS`):
   every complete manifest under `lake/derived/<tier>/<layer>/<name>/` becomes a
   `mimiciv_derived.<name>` view, every `lake/meta/<tier>/<table>.parquet` (EP-29's convention) a
   `meta.<table>` table and every `lake/marts/<tier>/…` a `marts.*` view — the convention all later
   P3 specs (phenotypes, meta.* producers, cohorts, spine) rely on;
   the concepts spec therefore ends with the shared `catalog` step (`depends_on` all concept
   steps; if the EP-19 runner does not merge specs into one graph, run `--select catalog` after).
   Subsets via EP-19's `--select concept.<group>.<name>,…` (confirm that `--select` pulls in
   incomplete `depends_on` ancestors; if not, add `--with-deps` to the runner here); add a
   `--keep-going` runner flag (record a step failure, continue with steps that do not
   depend on it) because the EP-19 runner stops at the first failure; resumable per concept via
   `status.json`. Add dated notes to DESIGN.md §3 (per-tier derived layout used from here on) and
   §15 (`--keep-going`/`--with-deps`).
3. **Concept versions table** — DAG step writing `meta.concept_versions` (concept, group,
   upstream_commit, sql_sha256, patch_id (null here; EP-38), rows, built_at, run_id, snapshot_id)
   and appending a `BenchmarkRecord(kind="concept")` per concept via `run.bench` (EP-35).
4. **Count-pinning** — `tests/ep/pins/concepts_demo.json` (committed; demo is ODbL): row count
   per concept table on the demo tier + `sepsis3` true count, `kdigo_stages` max-stage
   distribution, `charlson` mean index (rounded 2 dp); any pinned count < 11 is stored as the
   string `"<11"` (GOVERNANCE §3 forbids unsuppressed small cells in git even for ODbL data) — a
   one-line helper that EP-43 replaces with `disclose.render_cell()`; EP-38/EP-43 must switch the
   pin writer to it; the test rebuilds demo concepts and asserts equality. Dev pins go to `%MWH_DATA_ROOT%\runs\pins\concepts_dev.json` (written on first run,
   compared on later runs — a drift detector, **not committed** because it precedes EP-43's
   disclosure gate). Failing concepts on 1.5.x are listed with their DuckDB error class in
   `docs/resources/concepts.md` (new; also the human-readable inventory table).
5. **Full-tier launch (⏱)** — from `mimicwarehouse/`: `uv run --group dev mwh build --tier full
   --tag concepts --keep-going --background --job concepts-full` (EP-19's detached job runner;
   state in `runs/jobs/concepts-full.json`, log `runs/jobs/concepts-full.log`; `mwh jobs --job
   concepts-full --tail 20` to peek — INFO lines only). Record the job name, PID, start time and
   log path in the completion note; expected 30–120 min (the `chartevents`/`labevents` scans
   dominate). Do not wait for it — EP-38 verifies.
6. **Tests** — `tests/ep/test_ep37.py` (`@pytest.mark.ep_37`; fixture, `dev`): inventory is
   acyclic and covers every vendored file; header stripping on a crafted concept file; fixture
   run of `icustay_detail`, `age`, `charlson`, `sofa`, `sepsis3` succeeds; `meta.concept_versions`
   has one row per attempted concept with `upstream_commit` set; the demo pin test; on dev,
   `--select concept.sepsis.sepsis3` rebuilds only it and its incomplete ancestors.

## Out of scope

- Patching failing or lagging concepts (SIRS wbc guard, lab `valueuom`, Charlson, APS-III) and
  the full-run verification → EP-38.
- First-day feature marts and itemid rollups built on these concepts → EP-55.
- ED and Note concepts (none upstream) → EP-142 / P10. Phenotypes over concepts → EP-42.

## Verification / acceptance

- `uv run poe test -m ep_37` green on fixture and dev; `uv run --group dev mwh verify EP-37` green.
- `uv run --group dev mwh build --tier demo --tag concepts` and `--tier dev` complete;
  `uv run --group dev mwh sql "SELECT concept, rows FROM meta.concept_versions ORDER BY 1"` lists
  every attempted concept on dev; failures (if any) are recorded, not hidden.
- `tests/ep/pins/concepts_demo.json` committed; `docs/resources/concepts.md` lists concept ·
  group · upstream commit · status on DuckDB 1.5.x.
- Launched `mwh build --tier full --tag concepts --keep-going --background --job concepts-full`;
  log at `%MWH_DATA_ROOT%\runs\jobs\concepts-full.log`; job name/PID/start recorded here; timing
  verified by EP-38.

## Parked → final-roadmap.md

- Regenerating `concepts_duckdb` ourselves via sqlglot from upstream `concepts/` (BigQuery
  dialect) when the vendored transpilation lags — trigger: upstream regeneration PR stays open
  through P4.
