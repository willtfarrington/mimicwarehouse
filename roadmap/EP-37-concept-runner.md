# EP-37 — Concept runner (mimic-code concepts_duckdb → mimiciv_derived) ⏱

**Size:** M · **Tier:** fixture+dev (full ⏱ → verified by EP-38) · **Core/Stretch:** core · **Depends on:** EP-8 (mimic-code vendoring), EP-19 (DAG runner `mwh build`), EP-22 (Demo tier (MIMIC-IV Demo 2.2 + ED Demo)), EP-34 (Time semantics + unit-of-analysis registry), EP-35 (Provenance run ledger) · **Blocks:** EP-38 (Concept fixes/ports for DuckDB 1.5.x), EP-54 (Re-plan P3)

> **EP-33 amendment (2026-08-31) — D2 pre-flight smoke result.** The consolidation
> re-plan's concept smoke (EP-33 item D2) executed **all 65 vendored `concepts_duckdb`
> concept files cleanly, in `duckdb.sql` driver order, on DuckDB 1.5.5** — zero failures
> of any kind, 1.78 s wall — against a throwaway copy of the demo catalog (method: parse
> the driver's `.read` lines, execute each file via the Python client on a copy under
> `tmp\`; never a credentialed catalog's write path). The Context line "expect some
> concepts to fail — do not fix them here, record them" is therefore retired: plan for
> **zero executability breakage** on 1.5.x (roadmap Risk 2's executability half is struck
> with this measurement), item 4's "failing concepts on 1.5.x" list in
> `docs/resources/concepts.md` is expected to be empty, and the remaining real risks are
> count drift (execution success ≠ numerical correctness — the count-pins stay this
> brief's work) and the upstream concept-logic PR lag (EP-38's charter). The live demo
> catalog already holds all 31 staged tables. The fuller EP-33 D1 reconciliation of this
> brief against the shipped P2 APIs (spec-file loading for `mwh build --tag concepts` —
> ledger P3C-2; `CATALOG_EXTENSIONS`; Depends-on additions per retro FC-13) is **still
> owed** by the EP-33 re-run — see `EP-33-replan-p2.md` § Second attempt and
> `retro-p2-findings.md`.

> **EP-33 amendment (2026-09-01).** **Depends-on changed** (retro FC-13): now EP-8, EP-19,
> EP-22, **EP-34** (`timesem` fragments cited by the concept-versions/discovery step and the
> count-pins) and **EP-35** (`run.bench`, `BenchmarkLine.run_id`); the roadmap README row is
> updated in the same session. This block overrides item 2 where they differ; the D2 smoke
> result above (65/65 files clean on DuckDB 1.5.5) stands. (1) **Spec discovery — settled and
> owned here** (ledger P3C-2). Today `dag.spec.load_dag()` loads only the packaged `stage`
> spec (`DEFAULT_SPEC = "stage"`) and `mwh build` has no `--spec` option — none is added.
> This brief implements *discovery*: `load_dag()` merges **every** packaged `dag/specs/*.yaml`
> into one graph at load — step names are unique across files except the shared `catalog`
> step, which is deduplicated by name with its `depends_on` unioned (so `catalog` runs after
> the stage steps and after every concept step); cross-file `depends_on` references resolve
> after the merge; the packaged `stage` spec is simply one of the merged files. Result:
> `mwh build --tier <t> --tag concepts` and `--select concept.<group>.<name>` work with the
> shipped CLI (`mwh build --tier t [--select a,b] [--tag t] [--force] [--break-lock]
> [--background --job NAME] [--dry-run]`; `--dry-run` with `--background` is refused since
> EP-33), and item 2's "if the EP-19 runner does not merge specs" hedge is retired. EP-39/44/
> 45/50/53 add their own spec files and rely on this mechanism. (2) **Step kind.** Concept
> steps use the shipped `python` kind — `callable: module:function`, called with
> `(step, ctx)`, returning a `dag.runner.StepOutcome` (EP-29's `meta.profile` is the
> precedent) — registered through `dag.runner.STEP_HANDLERS` (`stage`, `catalog`, `python`
> today); adding a `sql` handler (the spec's `Kind` literal already lists it) is optional and
> only if it removes real duplication. Each derived table's manifest line + snapshot id come
> from the existing `dag.snapshot` conventions (`layer_snapshot`, `complete_for_tier`) — no
> new manifest format. (3) **Catalog discovery convention** (carried P3C-7, fixed here):
> derived tables land under `settings.layout["lake_derived"] / <tier> / <schema> / <table> /
> part-0.parquet` — one file, **no bucket partitions** (item 2's `PARTITION_BY subject_bucket`
> clause is dropped; the large concepts stay single-file ZSTD Parquet, which DuckDB scans
> with pushdown); demo uses the same key with its own tier segment. The `catalog` step
> registers `mimiciv_derived.<table>` views over them (`loader.paths.read_parquet_sql`, which
> `catalog.build` already imports, builds the relation); the discovery walker is registered
> via `CATALOG_EXTENSIONS` (EP-34's hook on `catalog.build.build_catalog`) and receives the
> build connection (`engine.open_duckdb("build", ...)`), attaching read-only sources with
> `engine.attach_read_only` — it opens nothing itself. Later layouts follow the same rule:
> `lake/meta/<tier>/<table>.parquet` -> `meta.<table>` (EP-29), `lake/marts/<tier>/...` ->
> `marts.*` (EP-47); the bucketed spine (EP-50) is the one exception, registered by its own
> `union` step. (4) **Count-pins.** The demo count-pin may be one `safe_query` statement now
> that set operations verify (EP-33 B1: `UNION ALL`/`EXCEPT`/`INTERSECT` with per-branch
> checks and the leftmost-leaf positional count rule) and `CAST` directly around a closed-set
> aggregate verifies; arithmetic over aggregates stays refused (final-roadmap DIS-3), so
> ratios are computed in Python from released counts. Pinned counts < 11 stay the string
> `"<11"` until EP-43's `disclose.render_cell`. (5) **Benchmarks.** Per-concept lines are
> `BenchmarkLine(kind="concept", run_id=...)` via `run.bench` (EP-35 extends the model; no
> `BenchmarkRecord`), read back with `mwh runs benchmarks --kind concept` (ledger P3C-6).
> (6) **Runtime facts.** `mwh build --tier dev --force` over a full-complete table is refused
> at the stage level (EP-33 LDR-1) and the derived layer inherits the rule — a dev rebuild
> never replaces a full derived table, `--tier full --force` is the only destructive path;
> `mwh jobs [--job NAME] [--tail N]` is the job peek; refusals print `mwh build: ...` on
> stderr with `console.EXIT_REFUSED`/`EXIT_USAGE`. (7) Fixture: generator 0.2.0 counts read
> from `tests/fixtures/manifest.json` (EP-33 TST-2); EP-41 regenerates to 0.3.0.

> **EP-37 pickup note (2026-09-05, owner-directed).** This brief runs for the **second
> time**. The first attempt (`3ba1224` + `2be0990`) was reverted together with EP-36 and
> EP-38 by `798141c` — the effort level had been set wrong for those sessions and the owner
> is redoing the three with closer attention (EP-36 shipped again as `8261089`). Execute
> this brief **fresh from its text**: the reverted implementation stays in history as a
> reference only (`git show 2be0990:roadmap/EP-37-concept-runner.md` holds its completion
> note; the hazards below are taken from it) and **nothing from it is cherry-picked or
> copied without asking the owner first**. Header facts unchanged; shorthand per the README
> notation table.
>
> **Step 0 — finish the data-root cleanup before item 1.** The first attempt's outputs
> would otherwise make the runner skip every concept as already complete, and the three
> catalogs still register views over files that no longer exist. The owner has already
> deleted, by hand on 2026-09-05: `lake\derived\{dev,full,demo}\`, `lake\demo\derived\`,
> `lake\demo\meta\demo\concept_versions.parquet`, every `lake\manifests\20260905T*.jsonl`
> and `lake\demo\manifests\20260905T*.jsonl`, `runs\pins\`, `runs\jobs\concepts-*.json` /
> `.log`, and the nine `runs\<run_id>\` folders of the first attempt's `concepts` build runs
> (ids in the EP-36 completion note). Paths here are layout keys (`get_settings().layout`,
> `lake_root(tier)`) and are never typed on a command line — the PreToolUse hook refuses
> them. The session then:
> **(0a) verifies, read-only:** `mwh doctor` (`power_scheme` must read *Best performance* on
> AC before anything heavy — ask, never change it), `mwh paths` (`lake_derived` ≈ 0 MB used),
> `mwh jobs` (no `concepts-*` rows), `mwh runs list --last 12` (no `concepts` runs; the three
> EP-35 dev runs and the EP-36 `ep36-acceptance` run remain), `mwh catalog info --tier
> dev|demo|full` (31 cataloged, 0 missing; the 65 `mimiciv_derived` concept views and
> `meta.concept_versions` are still listed — expected until the catalogs are rebuilt).
> **(0b) edits the four manifest files with an approved one-off script** (ask first,
> CLAUDE.md §6; write it to the session scratchpad and run it as `uv run python <script>`;
> resolve every path through `get_settings()`; print key counts only; keep a `.bak` copy
> beside each file until the completion note is written): in `lake\manifests\status.json`
> and `lake\demo\manifests\status.json` drop every `steps` key that starts with
> `mimiciv_derived.`, plus `meta.concept_versions` if present, keeping the 31 core entries
> and `meta.profile`; in `lake\manifests\snapshots.json` and
> `lake\demo\manifests\snapshots.json` drop every entry whose `layer` is `derived`, keeping
> `core`. Verify with `mwh build --tier dev --dry-run` and `--tier demo --dry-run` (the
> status files load; the plan shows only stage, `meta.profile` and `catalog` steps), then
> `mwh runs refresh`.
> **(0c) rebuilds nothing separately:** this brief's own `--tag concepts` builds end in the
> shared `catalog` step, which rebuilds each tier's catalog without the stale views
> (`meta.concept_versions` is then this brief's own table). Only if a check needs a clean
> catalog before the first concept build, run `mwh build --tier <t> --select catalog` (full
> as `--background --job catalog-full-cleanup`). The ledgers (`runs\ledger.jsonl`,
> `benchmarks.jsonl`, `audit.jsonl`) keep the first attempt's lines — append-only by design;
> every summary picks the latest line per `(tier, step)`, so this run's lines supersede
> them. Record step 0's outcome (keys and entries removed per file, the catalog checks) at
> the top of this brief's completion note.
>
> **Hazards the first attempt recorded** (facts from its completion note, not code): the
> per-tier derived layout is `layout["lake_derived"] / <tier> / …` for dev and full but
> `lake_root(tier) / derived / <tier> / …` for fixture and demo, which own their lake roots
> (EP-167) — the first attempt's first demo build wrote into the dev/full root and had to
> be corrected, because EP-28's structural test requires every manifest path to resolve
> under the lake root it is recorded in; merging every packaged spec in `load_dag()` broke
> `test_ep19::test_spec_steps_carry_contract_defaults`, which pins the stage spec's `catalog`
> dependencies through `load_dag()` — design the merge so the EP-19 assertion still holds,
> or change that test under the churn rule with a dated comment and say so in the completion
> note; the full-tier concept pass finished inside its session (65 of 65 concepts,
> 95,777,751 derived rows in ≈ 1,334 MB of Parquet), so "verified by EP-38" is
> record-keeping, not a wall-time necessity; `tests/ep/pins/concepts_demo.json` is
> regenerated by this run, never restored from history.

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
  through P4. *(Mirrored as `final-roadmap.md` CONC-1, parked by EP-8 and re-parked by
  EP-37 on 2026-09-05.)*

> **Completion note (2026-09-06).** Second execution of this brief, written from its text
> after the revert `798141c` (the first attempt's code was not consulted or reused; the
> Depends-on row — EP-8, EP-19, EP-22, EP-34, EP-35 — was ☑ before starting).
>
> **Step 0 (data-root cleanup, owner-approved).** *(0a, read-only)* `mwh doctor`: 9 pass ·
> 1 warn (`antivirus`, by design) · 0 fail · 5 info, `power_scheme` "Balanced · AC power
> mode: Best performance" for the whole session; `mwh paths`: `lake_derived` 0.0 MB;
> `mwh jobs`: no `concepts-*` row; `mwh runs list --last 12`: the nine first-attempt
> `concepts` build lines **still appear** — the ledger is append-only, as (0c) says — while
> their run folders are gone (`mwh runs show 20260905T205421Z-87de91` → "no manifest");
> `mwh catalog info --tier dev|demo|full`: 31 cataloged, 0 missing, the 65 stale
> `mimiciv_derived` views + `meta.concept_versions` + `meta.profile_*` still listed.
> *(0b, the approved one-off script — scratchpad `ep37_step0b_strip_derived.py`, run as
> `uv run python <script>`, every path through `get_settings()`, key counts only)*:
> `lake\manifests\status.json` 96 keys → dropped 65 `mimiciv_derived.*`, kept 31 core (no
> `meta.profile` / `meta.concept_versions` key existed: `meta.profile` has no status key by
> design); `lake\manifests\snapshots.json` 36 entries → dropped 4 `derived`, kept 32 `core`;
> `lake\demo\manifests\status.json` 96 → 65 dropped / 31 kept;
> `lake\demo\manifests\snapshots.json` 14 → 5 dropped / 9 kept; a `.bak` copy sits beside
> each of the four files (owner may delete them now that this note exists). Verified:
> `mwh build --tier dev --dry-run` and `--tier demo --dry-run` planned 33 steps (31 stage,
> `meta.profile`, `catalog`); `mwh runs refresh` rebuilt `runs.duckdb`. *(0c)* no separate
> catalog rebuild — this brief's own `--tag concepts` builds rebuilt each tier's catalog
> without the stale views.
>
> **Shipped.** `src/mimicwarehouse/concepts/inventory.py` (+ the generated, committed
> `concepts/concepts.yaml` and `dag/specs/concepts.yaml`; `python -m
> mimicwarehouse.concepts.inventory [--check]` regenerates / drift-checks), `concepts/runner.py`
> (`run_concept`, `run_concept_versions`, the `register_derived` catalog walker, `strip_header`,
> the per-tier derived layout helpers), `concepts/pins.py` (+ `tests/ep/pins/concepts_demo.json`),
> `dag/spec.py` (multi-spec discovery with the shared `catalog` step, `--with-deps`, `target` on
> python steps), `dag/runner.py` (`--keep-going` + `blocked`, `--with-deps`, a `run.start
> (kind="build")` provenance run per CLI build, per-layer snapshot ids, `StepContext.state` /
> `.run`, `StepOutcome.layer`), `dag/snapshot.py` (`per_tier` completeness, `layer_prefix`),
> `dag/cli.py` (the two flags, snapshot/run lines), `dag/benchmarks.py` (`read` infers the
> ledger schema from every line), `catalog/build.py` (the walker registered as the second
> `CATALOG_EXTENSIONS` entry), `docs/resources/concepts.md` (+ index row), DESIGN §3/§8/§15
> dated notes, D-19/D-20 addenda, the README rows + quick start, `tests/ep/test_ep37.py`
> (17 fixture · 2 dev · 1 demo opt-in tests). The EP-33 amendments were followed where they
> override the item text: `python` steps (not `sql`), `<schema>/<table>` single-file layout
> with no bucket partitions, `BenchmarkLine(kind="concept")` through `run.bench`, `mwh runs
> benchmarks --kind concept`, count-pins as one `UNION ALL` statement.
>
> **Runs.** *demo* — `mwh build --tier demo --tag concepts` (foreground): run
> `20260906T002639Z-ece96f`, 65 / 65 concepts done, concept wall 2.4 s (max 0.15 s), 131,517
> derived rows in 1,821,138 bytes of Parquet — EP-33's D3 measurement (131,517 rows at
> demo scale) reproduced exactly; `derived/demo` snapshot `10c7965497b1…`. *dev* — job
> `concepts-dev` (pid 14084, 2026-09-06T00:28:58 → 00:29:22 UTC, exit 0), run
> `20260906T002900Z-6f419d`: 65 / 65 done, concept wall 10.1 s (max 3.5 s), 4,633,174 rows in
> 62,739,641 bytes, run wall 15.6 s, peak RSS 577.8 MB, disk delta 48.8 MB; `derived/dev`
> snapshot `892d6dd46c30…`. On both tiers `SELECT status, count(*) AS n FROM
> meta.concept_versions GROUP BY 1` → `done 65` through `mwh sql`, and the brief's
> `SELECT concept, rows FROM meta.concept_versions ORDER BY 1` lists all 65 (read as counts
> only). Dev pins written on their first run to `runs\pins\concepts_dev.json` (layout key
> `runs`); demo pins committed (65 counts, every cell 0 or ≥ 11; `sepsis3` true 62; KDIGO
> max stage 0 / 1 / 2 / 3 = 38 / 29 / 42 / 31 stays; Charlson n 275, mean index 4.66).
> The `concept.sepsis.sepsis3` dev rebuild (`--select … --with-deps --force`) rebuilt only
> it with every ancestor skipped (`test_ep37::test_dev_select_sepsis3_rebuilds_only_it`).
>
> **Full-tier launch (⏱).** `uv run --group dev mwh build --tier full --tag concepts
> --keep-going --background --job concepts-full` — job **`concepts-full`**, PID **48132**,
> started **2026-09-06T00:35:09+00:00**, log `runs\jobs\concepts-full.log` (layout key
> `runs_jobs`); peek with `mwh jobs --job concepts-full --tail 20`. **It finished inside the
> session**: state `done`, exit 0, finished 2026-09-06T00:47:03+00:00 (11 min 54 s job
> wall incl. the catalog rebuild), run `20260906T003510Z-9564c0` (status ok, run wall
> 707.0 s, peak RSS 7,479.6 MB, disk delta 1,427.4 MB): **65 / 65 concepts done, 0 failed,
> 0 blocked**, summed concept wall 700.1 s (the longest single concept 442.8 s), **95,777,751
> derived rows in 1,337,952,596 bytes** of Parquet (`lake_derived` now 1,335.8 MB incl. the
> dev tier; 391 GB free) — the first attempt's 95,777,751 rows reproduced exactly;
> `derived/full` snapshot `a681ed30d829…`; `meta.concept_versions` on `full.duckdb` →
> `done 65`; `mwh catalog info --tier full` lists 75 registry/derived objects (the 65
> concept views + EP-34's two + 8 `meta.*`). "Verified by EP-38" is therefore
> record-keeping, as the pickup note anticipated: EP-38 pulls the per-concept table
> (`mwh runs benchmarks --tier full --kind concept`) and appends it here.
>
> **Owner decisions (end of session, all the recommended option):** commit as the two-step
> pair, no push; keep `meta.profile_*` out of the catalog; keep the provenance run per CLI
> build; the owner deletes the four step-0b `.bak` files by hand (`lake\manifests\` and
> `lake\demo\manifests\` — `status.json.bak`, `snapshots.json.bak`).
>
> **Gates.** `poe test -m ep_37` 17 passed (fixture; 58 s incl. the session lake);
> `pytest --tier dev -m ep_37` 2 passed (the sepsis3 rebuild, versions + dev pins);
> `mwh verify EP-37` 17 passed, exit 0; `poe check` **920 passed**, 36 deselected, 506 s
> (ruff check, ruff format --check, pyright clean; 903 → 920 = the 17 new fixture tests);
> the earlier-EP regression subset (EP-19/20/21/22/28/29/34/35/36) 120 passed after the
> `benchmarks.read` fix; `poe roadmap-check --strict` 0 errors, 0 warnings (172 rows, 45
> done before this brief's tick); `mwh guard` clean over the 20 touched files. Windows power
> mode read *Best performance* on AC throughout. No dependency change.
>
> **Earlier tests touched (churn rule, EP-168).** `tests/ep/test_ep20.py::
> test_spec_steps_carry_contract_defaults` — the exact catalog-dependency set became a
> superset check (dated comment); `load_dag("stage")` keeps the exact set. The hazard note
> named `test_ep19` for this pin; it is EP-20's. No other earlier test changed, but one
> earlier module did: `dag.benchmarks.read` now passes `infer_schema_length=None` — a ledger
> whose first hundred lines carry a null `error` (65 concept lines) made polars type the
> column NULL and `test_ep19::test_failure_stops_run_and_rerun_resumes` (a crafted catalog
> failure after the whole merged DAG) raised `ComputeError`. Every earlier `mwh verify`
> stays green (`poe check` runs the whole suite).
>
> **Judgment calls (owner review; the D-20 addendum lists them in full).** (1) per-tier
> derived completeness via `per_tier: true` + `layer` on the status entry; (2) concept
> sources are views over the lake, not the tier catalog; (3) `meta.profile_*` are **not**
> re-exposed as catalog tables (the first attempt had; per-column extrema on a
> registry-exempt surface); (4) every CLI `mwh build` is now a provenance run (≈ 6 s of
> doctor probes per process); (5) `--force` under `--with-deps` forces the selected steps
> only; (6) a `catalog` step is never `blocked`; (7) the session fixture lake
> (`conftest.fixture_lake_settings`, the full merged DAG by design since EP-170) now also
> runs the 65 concepts — ≈ 25 s more per pytest session, 10 concepts empty on the fixture
> (COVERAGE.md); (8) `meta.concept_versions` carries `status` / `error_class` / `build_id`
> beyond the brief's columns so failures are recorded, not hidden; (9) the run ledger keeps
> the first attempt's nine `concepts` lines (append-only; `mwh runs list` shows them,
> `mwh runs show` says "no manifest").
>
> **Deviations from the brief text.** None beyond the EP-33 amendments; the "failing
> concepts on 1.5.x" list in `docs/resources/concepts.md` is empty (65 / 65 on fixture,
> demo and dev). Nothing was cherry-picked from `3ba1224` / `2be0990`.

> **Completion note (2026-09-06, appended by EP-38 — the ⏱ verification of the full run).**
> Record-keeping, as the pickup note anticipated: the `concepts-full` job (pid 48132) ran
> 2026-09-06T00:35:09 → 00:47:03 UTC, state `done`, exit 0 (`mwh jobs --job concepts-full
> --tail 40`: INFO lines only, every concept step `done`), run `20260906T003510Z-9564c0`
> (`mwh runs show`: kind build, status ok, wall 707.0 s, peak RSS 7,479.6 MB, disk delta
> 1,427.4 MB, one `concept` ref per table, `derived/full` snapshot `a681ed30d829…`). Every
> concept has its manifest line and per-tier status entry (65 `kind: concept` ledger lines,
> all `ok`), and `meta.concept_versions` on `full.duckdb` read `done 65` with `patch_id`
> NULL for all 65 through `mwh sql` (audit `d244e46e…` / `d6983c82…`) — **no failures,
> nothing to re-launch**. Per-group table from the benchmark ledger (`mwh runs benchmarks
> --tier full --kind concept`; wall = the group's summed concept wall, peak RSS = the
> group's maximum):
>
> | group | concepts | wall s | peak RSS MB | rows | Parquet bytes |
> |---|---:|---:|---:|---:|---:|
> | comorbidity | 1 | 13.3 | 492 | 546,028 | 4,599,911 |
> | demographics | 5 | 2.5 | 534 | 11,619,064 | 61,530,604 |
> | firstday | 10 | 4.2 | 1,613 | 944,580 | 19,958,939 |
> | measurement | 18 | 192.7 | 7,479 | 53,822,452 | 877,265,027 |
> | medication | 14 | 4.5 | 635 | 3,851,347 | 61,774,489 |
> | organfailure | 4 | 5.4 | 1,826 | 10,133,576 | 152,303,216 |
> | score | 6 | 24.5 | 7,177 | 8,688,074 | 111,629,846 |
> | sepsis | 2 | 444.4 | 1,195 | 991,197 | 13,916,801 |
> | treatment | 5 | 8.6 | 1,250 | 5,181,433 | 34,973,763 |
> | **total** | 65 | 700.1 | 7,479 | 95,777,751 | 1,337,952,596 |
>
> The five slowest concepts: `suspicion_of_infection` 442.8 s (peak RSS 1,107 MB, 949,901
> rows), `rhythm` 159.7 s (7,479 MB, 7,887,354 rows), `sofa` 19.3 s (7,177 MB, 8,215,784
> rows), `charlson` 13.3 s (492 MB, 546,028 rows), `vitalsign` 10.2 s (3,702 MB,
> 13,519,533 rows). Disk: `lake/derived/full/mimiciv_derived/` (the as-built layout — the
> brief's `lake/derived/full/concepts/`) holds 65 files, 1,337,952,596 bytes (1,276.0 MB);
> with the dev tier's 62,739,879 bytes the `lake_derived` key reads 1,335.8 MB in `mwh
> paths`, `meta/full/` adds 16,439 bytes in 3 Parquet files, and C: keeps 389.6 GB free.
> EP-38's patched rebuild of 12 concepts on full followed the same day (its completion
> note carries that run).
