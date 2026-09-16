# EP-47 — Cohort compiler, materialization, attrition, snapshot

**Size:** M · **Tier:** fixture+dev+full · **Core/Stretch:** core · **Depends on:** EP-46 (Cohort spec + registry), EP-35 (Provenance run ledger) · **Blocks:** EP-48 (Attrition diagram renderer), EP-54 (Re-plan P3), EP-71 (Cross-sectional EDA module + page (Table 1)), EP-75 (Endpoints A: binary/continuous/count/ordinal), EP-102 (Model-ready dataset A: feature spec, windows, normalization, indicators)

> **EP-33 amendment (2026-09-01).** Header facts unchanged. (1) **Acceptance query** (ledger
> P3C-4): the acceptance sentence "`mwh sql "SELECT id, version, tier, rows FROM marts.cohorts
> ORDER BY 1,2,3"` lists the builds" now reads: *`uv run mwh sql "SELECT id, version, tier,
> rows FROM marts.cohorts ORDER BY 1,2,3" --tier dev` lists the builds — the statement has no
> count-family column and is admitted only because `marts.cohorts` is pre-registered in
> `safe.REGISTRY_TABLES` (EP-33 B1c, beside `REGISTRY_SCHEMAS = {meta, information_schema}` and
> the contract dims via `is_registry_ref`); the table must therefore keep a registry shape —
> one row per build, no subject-level columns, label values <= 64 chars, `path` relative to the
> data root.* No second registration mechanism is added (EP-46 amendment). (2) The
> `marts.cohort_<id>_v<major>` views and `cohort.parquet` are non-registry, subject-keyed reads
> under `safe_query` (P3C-5); `attrition_sql`'s `count(*)` / `count(DISTINCT subject_id)` pairs
> are real count-family nodes (EP-33 B1 — an alias alone never satisfies the rule). (3) Layout
> per EP-37's convention: `layout["lake_marts"] / <tier> / cohorts / <id>@<version>/` (the
> Context's "tier segment"), registered by the `catalog` step's discovery walker;
> `manifest.json` via `fsio.atomic_write_text`; directory publish via `publish.swap_dir(new,
> dest)` with `publish.new_path_for`/`old_path_for` (`mimicwarehouse.paths` is gone); the
> `--force` refusal mirrors the stage-level rule that `--tier dev --force` never replaces a
> full-complete artifact (EP-33 LDR-1). (4) `run.bench` -> `BenchmarkLine(kind="mart",
> run_id=...)` (EP-35 amendment); the background launch reuses `dag.jobs.launch` (as `mwh build
> --background` does) with `mwh jobs --job ep47-cohort-full` as the peek — or the build is a
> `python` step reached through spec discovery (`--select cohort.<id>`), whichever is lighter;
> state the choice in the completion note. Refusals print on stderr via `console.fail`.

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

> **Completion note (2026-09-16).** Executed as briefed on fixture + dev + full. Shipped:
> `src/mimicwarehouse/cohort/compiler.py` (`compile_spec` / `compile_entry` ->
> `CompiledCohort`: the chain `base` -> `idx` -> [`era`] -> `crit_NN_<label>` per criterion
> in spec order -> [`washout`] -> `cohort`, the brief's output columns under a total
> `ORDER BY`, the `attrition_sql` `UNION ALL`, `steps` with the `custom` flag, `sql_sha256`;
> every criterion kind mapped, `custom_sql` wrapped as a `custom_NN` CTE semi-joined on the
> grain keys; the criterion -> predicate table is `docs/methods/cohorts.md` §7),
> `cohort/build.py` (the `cohorts.build` python step — tag `marts`, `dag/specs/cohorts.yaml`
> — binding every relation the chain reads on the build connection with the resolved
> hashes; one `kind: cohort` run per spec -> `lake/marts/<tier>/cohorts/<id>@<version>/
> {cohort.parquet, attrition.parquet, spec.yaml, manifest.json}` via `publish.swap_dir`,
> the per-tier status entry, the marts snapshot id, a `kind: mart` benchmark line;
> `attrition()`; `register_marts` -> `marts.cohort_<id>_v<major>` + `marts.cohorts`, called
> from EP-46's `register_cohorts` extension), `probe.built_relation` + the `population`
> switch (the degeneracy probe over the compiled cohort), `mwh cohort build | attrition`,
> the golden `tests/ep/golden/first_icu_adults@1.0.0.sql`, `tests/ep/test_ep47.py` (7
> fixture + 1 dev + 1 full tests), `docs/methods/cohorts.md` §7, the DESIGN §9 / §15
> notes, the D-33 addendum, the README row + quick-start lines.
>
> **Interpretation choices.** (1) The background launch reuses `dag.jobs.launch` through
> `mwh cohort build --background --job NAME` (the `mwh phenotype compile` shape) — the
> lighter of the amendment's two options; no `--select cohort.<id>` discovery step. (2)
> Attrition counts are computed on the build connection (one statement, raw into the
> mart), and every session surface — the accessor, the CLI, the run manifest — shows the
> chain after `disclose.suppress(mode="chain")` on both count columns; in the run manifest
> a banded cell is `None` (D-33 addendum). (3) `base` is the grain's source population
> (all ICU stays for the tracer, EP-31's `base`); for the expanded grains it is every bin
> of every stay so the chain is non-increasing. (4) `cohorts.build` carries the `marts`
> tag, not `cohorts`, so EP-46's `--tag cohorts` and its test stay unchanged. (5) A
> directory built from a different definition under the same `id@version` refuses unless
> `--force`; a same-hash build is skipped unless `--force`; the LDR-1 dev/full rule is
> moot because the marts layer is per tier. (6) The `phenotype` criterion joins on the
> finest key both grains share, a coarser phenotype's onset must lie at or before the
> index; a `concept` relation is joined on the grain's unit key.
>
> **Reconciliation with EP-31 (the 4-hour exclusion).** The compiled chain reproduces the
> tracer count for count up to the step EP-46 added: the `idx` (= EP-31's `first_stay`)
> and `adult` steps are **3,208** stays / subjects on dev and **65,366** on full — exactly
> EP-31's cohort n on both tiers, confirming that EP-31's `complete` step drops nothing
> on MIMIC-IV 3.1 (EP-46 interpretation 4); `short_icu_stay` (ICU LOS < 4 h) then drops
> **12** on dev and **276** on full, so the materialised cohorts are 3,196 (dev) and 65,090
> (full). `base` (every ICU stay) is 4,672 on dev and 94,458 on full. The spec stays as
> seeded: the difference is the documented data-quality floor, not a drift.
>
> **Runs.** dev, foreground: `first_icu_adults@1.0.0` run `20260916T224707Z-ec19ba`
> (build `20260916T224707-dev`, 5 steps, 4,672 -> 3,208 -> 3,208 -> 3,196 -> 3,196 units /
> 3,208 subjects from `idx` on; the probe over the compiled cohort: 0 degenerate levels,
> admission_type 4 released / 4 withheld, first_careunit 7 / 7, k = 11);
> `hf_admissions@1.0.0` run `20260916T224732Z-2920bf` (materialisation 0.17 s; 27,263 ->
> 27,263 -> 4,309 -> 4,309 -> 4,234 -> 2,134 -> 2,134 units; subjects 11,267 -> 1,642 ->
> 1,642 -> ~1,620 -> ~1,620 -> 1,617 with two banded cells and three withheld drops at
> k = 11 — the chain mode at work). full, background jobs (EP-19 launcher):
> `ep47-cohort-full` (`runs\jobs\ep47-cohort-full.log`, 10 s end to end incl. the catalog;
> run `20260916T224746Z-ce6b7d`, materialisation wall 0.43 s, peak RSS 243 MB, core
> snapshot `b1fc53134348`; chain 94,458 -> 65,366 -> 65,366 -> 65,090 -> 65,090 units,
> subjects 65,366 from `idx` on; the probe over the compiled full cohort names
> `admission_type = AMBULATORY OBSERVATION` as **zero-event** for in-hospital mortality
> (n 20, 0 events) — the EP-31 policy's spec-level half, now over the materialised
> cohort; 7 first_careunit levels withheld) and `ep47-hf-full`
> (`runs\jobs\ep47-hf-full.log`, 10 s; run `20260916T224902Z-855aca`, wall 0.67 s, peak
> RSS 355 MB; chain 546,028 -> 546,028 -> 84,715 -> 84,715 -> 83,223 -> 42,497 -> 42,497
> units, subjects 223,452 -> 32,767 -> 32,767 -> 32,244 -> 32,200 -> 32,200, no cell
> withheld). `mwh sql "SELECT cohort_id, version, tier, rows FROM marts.cohorts ORDER BY
> 1,2,3" --tier full` lists both (the brief's `id` column is `cohort_id`, the EP-46
> registry's naming). Fixture: both seeds build in the conftest session lake (the full
> DAG) and in `test_ep47`'s module lake; a forced rebuild is byte-identical (sha256
> asserted), an unforced one is skipped.
>
> **Gates.** `uv run poe test -m ep_47`: 7 passed (fixture); `poe test-dev -m ep_47`: 8
> passed; `mwh verify EP-47` green; `poe check` green (ruff, `ruff format --check`,
> pyright 0 errors, the full fixture suite); `mwh guard` clean over the changed files;
> `poe roadmap-check --strict` 0 errors. Earlier tests untouched (`test_ep46`'s
> `--tag cohorts` ordering pin holds because the build step carries the `marts` tag).
>
> **Owner decisions at the interactive review (2026-09-16, every recommended option
> taken).** (1) Commit in the standard two steps, no push (the owner pushes). (2) Keep the
> run manifest's attrition chain **suppressed** (chain mode, banded cells `None`; raw
> counts in the mart only) — rejected: raw counts in the run manifest. (3) Record the
> EP-31 reconciliation in this note only — rejected: a pickup note under EP-31. (4) Keep
> `cohorts.build` under the `marts` tag — rejected: adding the `cohorts` tag (which would
> change EP-46's `--tag cohorts` contract and test). The remaining choices (the probe over
> the compiled cohort, the `jobs.launch` background path, the `base` definition for
> expanded grains, the join keys of `phenotype` / `concept` criteria) are routine and
> logged above.
>
> **Handed on.** EP-48: render `attrition()`'s frame (the `*_banded` / `*_suppressed`
> markers say what to draw as `~` / `<k`). EP-62: `mwh cohort build --dry-run --json`
> carries the SQL + steps for the page. EP-71 / EP-75 / EP-102: read
> `marts.cohort_<id>_v<major>` (subject-keyed; `obs_start` / `obs_end`, `follow_up_end` /
> `censor_reason` are the window and endpoint anchors). EP-54: decide whether
> `cohorts.build` should join `--tag cohorts` once the marts tag has more members
> (EP-55/56).
