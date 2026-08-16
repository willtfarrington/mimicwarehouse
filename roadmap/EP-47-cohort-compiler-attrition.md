# EP-47 — Cohort compiler, materialization, attrition, snapshot

**Size:** M · **Tier:** fixture+dev+full · **Core/Stretch:** core · **Depends on:** EP-46 (Cohort spec + registry), EP-35 (Provenance run ledger) · **Blocks:** EP-48 (Attrition diagram renderer), EP-54 (Re-plan P3), EP-71 (Cross-sectional EDA module + page (Table 1)), EP-75 (Endpoints A: binary/continuous/count/ordinal), EP-102 (Model-ready dataset A: feature spec, windows, normalization, indicators)

## Context

EP-46 defined `CohortSpec`; this brief makes it executable (DESIGN §9): a compiler that emits a
deterministic CTE chain (one step per criterion, in spec order), materializes the cohort table
under the marts layer, records per-step attrition, and wraps everything in a run record (EP-35)
that cites the snapshot ids read — so a cohort is reproducible from `id@version` + snapshot ids.
The compiler embeds `timesem` SQL fragments (EP-34: index-event rules, `hours_since`, age at
index with the ≥ 89 = 91 cap, era index, `dod` follow-up end) and resolves `codeset`/`phenotype`
criteria against `meta.codeset_members` / `mimiciv_derived.phenotype_*`. Marts follow the
per-tier layout fixed in EP-37 (`lake/marts/<tier>/cohorts/<id>@<version>/`); DESIGN §9's path
gains the tier segment (dated note). Full tier: `first_icu_adults@1.0.0` touches only
`icustays/admissions/patients`, so it completes in seconds — but, like every full-tier run, it is
launched as a logged background job (EP-19) — and its counts are compared with the tracer bullet
(EP-31). Attrition counts stay raw inside the data root and pass
through `disclose` (EP-43) whenever displayed or exported (D-33). D-17, D-20, D-24 apply.

## In scope

1. **Compiler** (`src/mimicwarehouse/cohort/compiler.py`) — `compile(spec, tier) ->
   CompiledCohort` with `sql` (single statement: `WITH base AS (grain population), idx AS (index
   event), crit_01_<slug> AS (…), … SELECT …`), `attrition_sql` (one `UNION ALL` of
   `count(*)`, `count(DISTINCT subject_id)` per CTE — computed in one query), `steps` (ordered
   labels), `sql_sha256`. Output columns: grain keys (`subject_id`, `hadm_id`, `stay_id` as
   applicable), `index_time`, `era_index`, `age_at_index`, `age_capped`, `obs_start`, `obs_end`,
   `follow_up_end`, `censor_reason`, `custom_flag`; deterministic ordering (`ORDER BY` keys) and
   no non-deterministic functions; `--dry-run` prints the SQL. Criterion → SQL mapping documented
   inline; `custom_sql` criteria are wrapped as a CTE and marked in `steps`.
2. **Materialization + registry** (`src/mimicwarehouse/cohort/build.py`) — `mwh cohort build
   <id@version> --tier <t> [--force]`: run inside `run.start(kind="cohort")` (records spec hash,
   refs, `sql/cohort.sql`, `sql/attrition.sql`, snapshot ids of every layer read, attrition rows);
   write `lake/marts/<tier>/cohorts/<id>@<version>/cohort.parquet` (sorted by keys, ZSTD-3),
   `attrition.parquet`, `spec.yaml` (copy), `manifest.json` (spec def_hash, sql_sha256, run_id,
   snapshot ids, rows, built_at) via the DAG runner sink; register `marts.cohort_<id>_v<major>`
   view (latest patch) and a `marts.cohorts` registry table (id, version, def_hash, tier, rows,
   n_subjects, run_id, path). Rebuild with the same spec + snapshot ids reproduces a byte-identical
   `cohort.parquet` (assert sha256 in tests); a spec/hash mismatch with an existing directory
   refuses unless `--force`.
3. **Attrition access** — `cohort.attrition(id@version | run_id, tier, k=11) -> polars.DataFrame`
   (step, label, n_units, n_subjects, dropped_units, dropped_subjects) passed through
   `disclose.suppress(mode="chain")` before it is returned/printed; `mwh cohort attrition
   <id@version> --tier <t>` prints it. Raw counts remain in `attrition.parquet` and the manifest.
4. **Dev + full builds** — build both seed specs on dev; build `first_icu_adults@1.0.0` on full
   as a logged background job (`uv run --group dev mwh cohort build first_icu_adults@1.0.0 --tier
   full --background --job ep47-cohort-full`, EP-19 launcher, log
   `%MWH_DATA_ROOT%\runs\jobs\ep47-cohort-full.log`; poll with `mwh jobs --job ep47-cohort-full`;
   run id + wall time recorded), compare its final `n_units`/`n_subjects` with EP-31's tracer
   report and record agreement (or explain the difference and align the spec/EP-31 note);
   `hf_admissions@1.0.0` on full as a second logged background job (`--job ep47-hf-full`).
5. **Tests + docs** (`tests/ep/test_ep47.py`, `@pytest.mark.ep_47`; fixture, `dev`, `full`
   opt-in) — golden SQL for `first_icu_adults@1.0.0` (`tests/ep/golden/first_icu_adults@1.0.0.sql`);
   fixture build twice → identical parquet hash; per-step attrition on a crafted synthetic
   population (in a temp DuckDB, ids ≥ 90 000 000) matches hand counts including a `washout` and
   a `phenotype` criterion; `age_capped` set for a synthetic 91-year-old; `attrition()` output on a
   chain with a small drop contains no exact count < 11; the manifest cites the layer snapshot
   ids; on dev, `mwh cohort build` for both specs succeeds and `marts.cohorts` has two rows.
   `docs/methods/cohorts.md` gains a "compilation" section (CTE naming, determinism, marts layout).

## Out of scope

- Mermaid/Altair attrition diagram → EP-48. Cohort Builder page → EP-62.
- Table 1 / EDA over a cohort → EP-71; endpoints → EP-75; model-ready datasets → EP-102.
- Cohort diff/versions viewer → parked (`final-roadmap.md` § 2).

## Verification / acceptance

- `uv run poe test -m ep_47` green on fixture and dev; `uv run --group dev mwh verify EP-47` green.
- `uv run --group dev mwh cohort build first_icu_adults@1.0.0 --tier dev` and `--tier full
  --background --job ep47-cohort-full` succeed; both full builds launched in the background (job
  names/log paths in the completion note); `uv run --group dev mwh cohort attrition
  first_icu_adults@1.0.0 --tier full` prints the suppressed chain; run ids, wall times and the
  EP-31 comparison are in the completion note.
- `uv run --group dev mwh sql "SELECT id, version, tier, rows FROM marts.cohorts ORDER BY 1,2,3"`
  lists the builds; `mwh runs show <run_id>` shows `attrition` and `snapshot_ids`.
- Golden SQL committed; byte-identical rebuild demonstrated on fixture.
