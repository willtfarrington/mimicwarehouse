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
  through P4. *(Mirrored into `final-roadmap.md` CONC-1 on 2026-09-05.)*

> **Completion note (2026-09-05).** Shipped: `src/mimicwarehouse/concepts/inventory.py`
> (scan → the generated, committed `concepts/concepts.yaml` + `dag/specs/concepts.yaml` +
> the `docs/resources/concepts.md` table; `python -m mimicwarehouse.concepts.inventory`),
> `concepts/runner.py` (`concept.<group>.<name>` python steps, `meta.concept_versions`),
> `concepts/pins.py` (count-pins through `safe_query`), `catalog/discover.py` (the
> discovery walker, second `CATALOG_EXTENSIONS` entry), `dag/spec.py` (multi-spec merge
> with the shared `catalog` step, `target` on python steps, `Step.status_key`, `--with-deps`
> closure + `explicit_selection`), `dag/runner.py` (`--keep-going` with the `blocked`
> status, generic status-key skip, forced set), `dag/snapshot.py` (per-tier layers:
> `layer`-aware `complete_for_tier`, `layer_path_prefix`, `layer_lines`),
> `catalog/build.py` (`CatalogExtensionContext`, signature-aware dispatch),
> `catalog/profile.py` (`relation_sql` / `write_meta_parquet` made public),
> `loader/paths.py` (`layer_table_dir`, `single_file_sql`, `PART_FILENAME`), `dag/cli.py`
> (`--keep-going`, `--with-deps`), `tests/ep/test_ep37.py` (26 tests: 23 fixture + 2 dev +
> 1 demo), `tests/ep/pins/concepts_demo.json`, DESIGN §3/§8/§15 dated notes, D-19/D-20
> addendum, README § State rows + quick-start lines, `docs/resources/README.md` row,
> `final-roadmap.md` CONC-1 re-parked.
>
> **Acceptance.** `uv run poe test -m ep_37`: 23 passed on fixture; with `--tier dev
> --with-demo` 26 passed (the dev select-with-deps and dev pin drift tests ran against
> `dev.duckdb`, the demo pin test force-rebuilt the demo concepts and matched the committed
> pins). `uv run --group dev mwh verify EP-37`: exit 0. `uv run poe check`: ruff / format /
> pyright clean, **908 passed** (944 collected, 36 tier probes deselected) in 303 s. `mwh
> guard` clean over every changed tree; `poe roadmap-check --strict` 0 errors / 0 warnings.
> `mwh build --tier demo --tag concepts` and `--tier dev --tag concepts` (job `concepts-dev`,
> 23 s wall) completed with **65/65 concepts ok** on both tiers; `mwh sql "SELECT status,
> count(*) AS n FROM meta.concept_versions GROUP BY 1" --tier dev|demo|full` = `ok 65` on
> all three; the dev catalog holds 77 registry/derived objects (65 concept views +
> `meta.concept_versions` + the two EP-29 profile tables beside the EP-29/EP-34 nine).
> **No concept fails on DuckDB 1.5.5** (`KNOWN_FAILURES` empty; roadmap Risk 2's
> executability half confirmed at build time on fixture, demo, dev and full).
>
> **Full-tier launch (⏱, item 5).** `uv run --group dev mwh build --tier full --tag concepts
> --keep-going --background --job concepts-full` — job `concepts-full`, supervisor pid
> 11132, started 2026-09-05T19:38:10Z, log `runs\jobs\concepts-full.log`. It did **not**
> need EP-38's patience: `state=done exit=0` at 2026-09-05T19:50:28Z — **12 min 18 s wall**,
> 65/65 concepts ok, `meta.concept_versions` + full catalog rebuilt, nothing blocked. From
> `mwh runs benchmarks --kind concept --tier full`: 95,777,751 derived rows, concept wall
> total 725.0 s, peak RSS high-water 7,460 MB (`sofa`: 8,215,784 rows, 20.4 s, 6,467 MB;
> `vitalsign`: 13,519,533 rows, 11.1 s, 3,257 MB; `charlson`: 546,028 rows, 14.1 s;
> `kdigo_stages`: 5,099,899 rows, 3.1 s; `sepsis3`: 41,296 rows, 1.8 s). `lake\derived`
> (dev + full) measures 1,337.4 MB — inside the EP-33 D3 estimate (≈ 90 M rows, 0.8–1.5 GB).
> EP-38 verifies the full-tier counts against the demo/dev pins and records the timing in
> the benchmark case study; the numbers above are the launcher's record.
>
> **Earlier tests touched (roadmap README rule, CMP-6):** `tests/ep/test_ep20.py`
> `test_spec_steps_carry_contract_defaults` — it pinned the *stage spec's* catalog
> dependencies through `load_dag()`, which now merges every spec (the shared `catalog` step
> also depends on the 65 concept steps and `meta.concept_versions`); it loads
> `load_dag("stage")` by name. No other earlier test changed; the earlier `mwh verify`
> loop stays green (908 passed in `poe check`).
>
> **As-built choices (routine, recorded here).** (1) **Layout deviation for the synthetic
> tiers:** the amendment's `layout["lake_derived"] / <tier> / …` is honoured for dev and
> full (`lake\derived\dev`, `lake\derived\full`); fixture and demo write the same shape under
> their **own** lake roots (`lake\fixture\derived\fixture\…`, `lake\demo\derived\demo\…`),
> because a fixture/demo build must never write into the credentialed tree (EP-167/ARCH-3)
> and EP-28's structural test requires every manifest path to resolve under the lake root it
> is recorded in (`derived/<tier>/…` relative to `lake_root(tier)`). The first demo build of
> this session (before the correction) left an orphaned `C:\mimicdata\lake\derived\demo\`
> (~13 MB of ODbL-derived Parquet) — deleting it is the owner's call (CLAUDE.md §6).
> (2) Concept steps use the shipped `python` kind with a `target`; no `sql` handler was
> registered (nothing to de-duplicate). (3) Execution order at run time is graphlib's
> topological order over the merged graph, not the spec's listing order (dependencies are
> honoured; the inventory's `order` column is upstream's driver order, itself topological).
> (4) `dag.benchmarks.read` now infers the ledger schema over every line
> (`infer_schema_length=None`): the EP-35 `run_id` column, null on every runner line, bound
> as NULL-typed under polars' 100-line default and crashed `mwh runs benchmarks` on the first
> `kind: concept` line — a latent EP-35 bug this brief surfaced and fixed. (5) A failing
> concept's engine error passes `safe.sanitize_error_text` before it reaches the runner's
> ledger line (quoted literals masked). (6) `meta.concept_versions` and the catalog step
> have no status key, so they rerun on every `--tag concepts` build (the versions step costs
> the doctor probe once per process, ≈ 8 s; a rerun writes no duplicate `kind: concept`
> lines — only concepts attempted in the current build get one). (7) The fixture-lake
> session fixture (`conftest.fixture_lake_settings`) now builds the 65 concepts too
> (+≈ 15 s per pytest session); `tests/fixtures/COVERAGE.md`'s empty/partial concepts hold
> (e.g. `icp`, `rhythm`, `neuroblock` = 0 rows; `sepsis3` 8 rows → `"<11"` in the fixture pins).
> (8) On the demo tier no concept count fell below 11 and `neuroblock` is the one empty
> table, so the committed pin file carries no `"<11"` cell; the dev pins
> (`runs\pins\concepts_dev.json`) were written by the first dev run. (9) `test_ep22`'s
> gzipped-fixture demo build and `test_ep19`'s crafted-failure rerun exercise the merged DAG
> unchanged. (10) Power mode: `mwh doctor` read `Balanced · AC power mode: Best performance`
> at session start (the AC overlay is the D-38 setting); timings above were taken under it.
>
> **Owner decisions at close (session-end prompt, all as recommended):** two-step commit
> (`feat(mimicwarehouse): concept runner … (EP-37)` then `docs(roadmap): record EP-37 commit
> hash`, no AI trailers) — done; the orphaned `C:\mimicdata\lake\derived\demo\` **deleted**
> (65 Parquet files; the live `lake\derived\dev` and `lake\derived\full` layers untouched);
> the fixture/demo layout deviation (own lake roots) **accepted as built**; pushing stays
> with the owner (nothing pushed by the session).

> **Completion note (2026-09-05, EP-38 — verification of the ⏱ full run, item 1 of
> EP-38).** Job `concepts-full` (supervisor pid 11132, `runs\jobs\concepts-full.log`):
> `state=done exit=0`, started 2026-09-05T19:38:10Z, finished 19:50:28Z — **12 min 18 s
> wall** for the whole job, of which the 65 concept steps account for 725.0 s (12 min 5 s)
> and `meta.concept_versions` 9.4 s + `catalog` 2.1 s for the rest. Build id
> `20260905T193811-full-062b0d0`, provenance run `20260905T195016Z-61a5cc` (`kind: build`,
> 65 `concept` refs), derived snapshot recorded, core snapshot `b1fc53134348…`. Verified
> from the ledgers, never from the data: the full lake carries **65 of 65** derived manifest
> lines and 65 `status.json` entries complete for `full`; `meta.concept_versions` on
> `full.duckdb` reads `ok 65` (`patch_id` NULL throughout — the build predates EP-38's
> patches); `mwh runs benchmarks --kind concept --tier full` has 65 `kind: concept` lines,
> all `ok`. Per concept group (latest line per step; peak RSS = the group's high-water
> mark; Parquet MB from the ledger's `bytes_out`):
>
> | group | concepts | wall s | peak RSS MB | rows | Parquet MB |
> |---|---:|---:|---:|---:|---:|
> | demographics | 5 | 3.2 | 486 | 11,619,064 | 61.5 |
> | measurement | 18 | 203.8 | 7,459 | 53,822,452 | 877.1 |
> | organfailure | 4 | 6.0 | 1,675 | 10,133,576 | 152.3 |
> | comorbidity | 1 | 14.1 | 478 | 546,028 | 4.6 |
> | medication | 14 | 5.4 | 1,513 | 3,851,347 | 61.7 |
> | treatment | 5 | 8.9 | 820 | 5,181,433 | 35.0 |
> | firstday | 10 | 5.1 | 1,513 | 944,580 | 20.0 |
> | score | 6 | 26.3 | 6,466 | 8,688,074 | 111.8 |
> | sepsis | 2 | 452.2 | 1,128 | 991,197 | 13.9 |
> | **total** | 65 | 725.0 | 7,459 | 95,777,751 | 1,338.0 |
>
> Disk used by `lake\derived\full\mimiciv_derived\` = the 65 single files' 1,337,955,005
> bytes (**1,338.0 MB**, ≈ 1.25 GiB), matching the ledger's `total` line (1.34 GB) and
> inside the EP-33 D3 estimate. Two steps dominate: `sepsis.suspicion_of_infection`
> (450.4 s — 62 % of the concept wall; the antibiotic × microbiology join) and
> `measurement.rhythm` (168.6 s, the 7,460 MB peak); `score.sofa` peaks at 6,467 MB in
> 20.4 s. No failures, nothing blocked, no `KNOWN_FAILURES` entry needed: the "failed
> but fixable" re-launch of EP-38 item 1 has nothing to re-launch. EP-38's count-pin
> comparison and the patched rebuild (job `concepts-full-patched`) are recorded in
> `EP-38-concept-fixes.md`.
