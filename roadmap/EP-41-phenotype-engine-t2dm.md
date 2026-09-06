# EP-41 — Phenotype engine + T2DM phenotype

**Size:** M · **Tier:** fixture+dev · **Core/Stretch:** core · **Depends on:** EP-40 (Code-set registry + ICD-9→10 GEM utility) · **Blocks:** EP-42 (Phenotypes: sepsis-3 + KDIGO AKI stage), EP-54 (Re-plan P3)

> **EP-33 amendment (2026-09-01).** Header facts unchanged. (1) **The fixture 0.3.0
> regeneration folds in three things**: the vocab extension + T2DM inputs this brief already
> plans; the **outcome enrichment** (EP-33 D4e — EP-31 found 5 degenerate zero-event levels
> among the 66 fixture first ICU stays; seed enough deaths per `gender` / `admission_type` /
> `first_careunit` / `anchor_year_group` level that the tracer's `fit` excludes no level on
> fixture); and the **NULLS LAST alignment** of the Polars fixture writer/check with DuckDB's
> `ORDER BY` (ledger CTR-1; DuckDB NULLS LAST is the canonical placement — D-17 addendum at
> EP-33), so the committed fixture equals what the loader would sort. Bump `generator_version`
> to `0.3.0` in `tests/fixtures/manifest.json`; since EP-33 (TST-2) `test_ep21/22/30` read
> their counts from that manifest, so the count change does not ripple into earlier tests.
> (2) **Phenotype views are non-registry reads** (ledger P3C-5; EP-33 B1c rule): every
> `safe_query` read outside the EP-9 contract that is not `meta.*` / `information_schema` /
> `marts.cohorts` / a contract dim is treated **subject-keyed**, so the 64-char free-text
> result check applies to `mimiciv_derived.phenotype_<id>` — keep `evidence_json` values
> <= 64 chars or aggregate/omit them in anything a session selects; item 4's `summary` selects
> counts and shares only (`count(*) FILTER (WHERE flag)` — a real count-family node). (3)
> Materialization follows EP-37's convention: `layout["lake_derived"]/<tier>/phenotypes/
> <id>@<version>/part-0.parquet` (single file), registered by the `catalog` step's discovery
> walker; runs record via EP-35 (`BenchmarkLine.run_id`). (4) Frozen-version refusals use
> `console.fail(..., code=console.EXIT_REFUSED)` on stderr.

## Context

Capability 3 (computable clinical phenotypes, versioned) needs an engine that turns a declarative
YAML into deterministic SQL over the tier catalog, materializes the result in the derived layer,
and versions it like a code set. This brief builds `src/mimicwarehouse/phenotypes/` (DESIGN §8,
§15) and its first phenotype, type 2 diabetes mellitus — chosen because it exercises diagnoses
(dual ICD-9/10 sets from EP-40), medications (`prescriptions`/`emar` drug-name sets), labs
(HbA1c via `labevents`, unit-aware through EP-39 when available) and temporal logic (onset =
earliest qualifying event) without needing mimic-code concepts (EP-42 adds concept-backed
phenotypes). Grain and relative time come from `timesem` (EP-34); code sets by `id@version` come
from EP-40 and are resolved to hashes so a phenotype version pins its inputs. MIMIC caveats:
diagnoses carry no timestamps (assign `dischtime` of the admission), the ICD switch is per row
(`icd_version`), and a per-subject phenotype cannot use calendar time across patients. D-19/D-20
(SQL through the DAG runner), D-33 (small cells) apply; all counts a session sees are aggregates.

## In scope

1. **Phenotype schema** (`src/mimicwarehouse/phenotypes/spec.py`) — pydantic `Phenotype`:
   `id`, `version` (semver), `name`, `grain` (`subject|hadm|icustay`, from EP-34's registry),
   `criteria` (a boolean tree `all/any/not` over leaves), `onset` (`earliest|latest|first_of:
   [leaf ids]`), `outputs` (flag, onset_time, per-leaf evidence counts), `references` (code sets
   `id@version` → resolved `def_hash`), `provenance`, `notes`, `what_it_does_not_claim`. Leaves:
   `diagnosis(codeset, position: any|primary, min_admissions)`, `procedure(codeset)`,
   `medication(codeset, source: prescriptions|emar|inputevents, min_orders)`,
   `lab(codeset|itemids, op, threshold, unit, min_count)`, `microbiology(spec_itemids|org_itemids,
   positive_only)`, `concept(table, column, op, value)` (used by EP-42), `temporal(a, relation:
   before|after|within_hours, b)`. `def_hash` = sha256 of canonical JSON of grain + criteria +
   resolved reference hashes; `(id, version)` immutable (same rule and error type as EP-40).
2. **Compiler + materialization** (`compiler.py`, `runner.py`) — deterministic CTE chain, one
   CTE per leaf (`leaf_01_dx …`) each yielding `(grain keys, event_time, evidence)`, a boolean
   reduction CTE, and a final `SELECT grain keys, flag, onset_time, evidence_json`; materialize
   under `lake/derived/<tier>/phenotypes/<id>@<version>/` via the DAG runner sink (per-tier layout
   from EP-37) with a manifest, register `mimiciv_derived.phenotype_<id>` view (latest version) and
   `meta.phenotype_versions` (id, version, def_hash, grain, refs, rows, n_positive, built_at,
   run_id); every build is a `run.start(kind="phenotype")` run (EP-35) recording the SQL and
   reference hashes. `mwh phenotype list|show|validate|compile <id@version> [--tier] [--dry-run]`;
   dated DESIGN §15 note for the new CLI group.
3. **T2DM phenotype** (`src/mimicwarehouse/phenotypes/defs/t2dm.yaml`, `t2dm@1.0.0`, grain
   `subject`) — `any( diagnosis(t2dm@1.0.0, any position, min_admissions=1),
   all( any( medication(noninsulin_antidiabetics@1.0.0, prescriptions), lab(HbA1c ≥ 6.5 %,
   min_count=1) ), not(diagnosis(t1dm@1.0.0)) ) )`; onset = earliest qualifying event; also emits
   a per-`hadm` companion view (`phenotype_t2dm_hadm`: hadm flagged if onset ≤ dischtime). Record
   in `what_it_does_not_claim`: not a validated eMERGE algorithm; HbA1c alone does not distinguish
   type; gestational/secondary diabetes not excluded.
4. **Prevalence summary helper** — `phenotypes.summary(id@version, tier) -> polars.DataFrame`
   (n_units, n_positive, share, by era via `hadm_era` for hadm-grain views), returned through the
   catalog's aggregate path (k = 11 suppression as EP-30 already enforces for sessions); printed by
   `mwh phenotype summary`.
5. **Tests + docs** — `tests/ep/test_ep41.py` (`@pytest.mark.ep_41`; fixture, `dev`): schema
   round-trips YAML; hash invariance and frozen-version refusal; compiler emits identical SQL for
   identical specs (golden SQL file under `tests/ep/golden/t2dm@1.0.0.sql`); crafted synthetic
   subjects (ids ≥ 90 000 000, built in-test into a temp DuckDB using EP-9's schema) cover each
   branch (dx only; med + lab without dx; T1DM-only excluded; none); onset equals the earliest
   event; on dev, `mwh phenotype compile t2dm@1.0.0 --tier dev` builds and `summary` prints
   n_units/n_positive/share (aggregate). `docs/methods/phenotypes.md` (new): schema, leaf
   semantics, versioning, T2DM definition card.

## Out of scope

- Concept-backed phenotypes (sepsis-3, KDIGO AKI) and full-tier prevalence → EP-42.
- Phenotype Studio page → EP-63. Prevalence/incidence with CIs by denominator → EP-68.
- Additional phenotypes (HF, COPD, CKD stages, delirium, ARDS, VAP) → parked (`final-roadmap.md` § 3).
- Cohort criteria that reference phenotypes → EP-46/47.

## Verification / acceptance

- `uv run poe test -m ep_41` green on fixture and dev; `uv run --group dev mwh verify EP-41` green.
- `uv run --group dev mwh phenotype compile t2dm@1.0.0 --tier dev` writes the derived table and a
  `meta.phenotype_versions` row with `def_hash` and resolved code-set hashes; `mwh runs list --kind
  phenotype` shows the run.
- `uv run --group dev mwh phenotype summary t2dm@1.0.0 --tier dev` prints n_units, n_positive and
  share (no cell < 11); the number is recorded in the completion note.
- Golden SQL file committed; changing a threshold in the YAML without a version bump is refused.

## Parked → final-roadmap.md

- Session reads of a **specific** phenotype version: the walker's `phenotypes."<id>@<version>"`
  views sit outside `safe.ALLOWED_SCHEMAS`, so `mwh sql` / `mwh phenotype summary` see only the
  latest built version through `mimiciv_derived.phenotype_<id>` — trigger: a version-comparison
  workflow (EP-63, a cohort pinned to an older version at EP-46/47); hazard: allow-listing the
  schema is a governance loosening (EP-43 decides) vs per-version `mimiciv_derived` views.
  *(Mirrored into `final-roadmap.md` § 3 as v2 PHE-6 at execution, 2026-09-06.)*
- Branch-aware onset semantics (today: `earliest` / `latest` / `first_of` over the positive
  leaves a unit satisfies, whichever branch made the flag true) — trigger: EP-49 / EP-76
  anchoring on a phenotype onset. *(Mirrored as v2 PHE-7, 2026-09-06.)*

> **Completion note (2026-09-06).** Executed on fixture + dev as briefed; nothing ran on
> full (`--tag phenotypes` joins the owner's next full rebuild, as EP-39's `--tag units`
> and EP-40's `--tag codesets`; EP-42's `phenotypes-full` job covers the three phenotypes).
>
> **Shipped.** `src/mimicwarehouse/phenotypes/` — `spec.py` (pydantic `Phenotype`: grain,
> the `all` / `any` / `not` criteria tree over the seven leaf kinds, onset, outputs,
> provenance, `what_it_does_not_claim`; `def_hash` over grain + criteria + onset + the
> referenced code-set hashes), `registry.py` (`defs/` + `phenotypes.lock.json`,
> `PhenotypeFrozenError`, reference resolution against the EP-40 registry incl.
> `--codesets DIR` study directories, `validate`), `compiler.py` (the deterministic CTE
> chain — code sets and lab conversions inlined; grain mapping for subject / hadm /
> icustay; the `hadm` companion SQL), `runner.py` (the `phenotypes.compile` step →
> `lake/derived/<tier>/phenotypes/<id>@<version>/part-0.parquet` + `meta.phenotype_versions`,
> one `kind: phenotype` run per phenotype, skip-when-built / `--force`,
> `register_phenotypes` — `mimiciv_derived.phenotype_<id>` = the latest built version +
> `phenotype_<id>_hadm` — `summarize` / `summary` through `safe_query`, the docs renderer),
> `cli.py` (`mwh phenotype list | show [--sql] | validate | lock | compile [--dry-run]
> [--force] | summary`), `defs/t2dm.yaml` (`t2dm@1.0.0`, locked), `dag/specs/phenotypes.yaml`,
> `tests/ep/golden/t2dm@1.0.0.sql`, `tests/ep/test_ep41.py` (14 fixture + 1 dev tests),
> `docs/methods/phenotypes.md`; `BENCHMARK_KINDS` + `phenotype`; the fourth
> `CATALOG_EXTENSIONS` entry; DESIGN §4/§8/§15 notes, D-17/D-27/D-33 addenda, README state
> row + quick start, `docs/gotchas.md` (the macro-scan finding), `tests/README.md`,
> `tests/fixtures/COVERAGE.md`, `final-roadmap.md` PHE-6/PHE-7.
>
> **Fixture 0.3.0** (the EP-33 amendment's three items, one regeneration, minor bump for the
> new spec key `min_deaths_per_level`): HbA1c 50852 + metformin / glipizide in the vocab
> (the planted t2dm admissions force HbA1c 6.6–11.0 % and a metformin order; the T1DM
> codes stay absent on purpose, `test_ep40` pins it); `_plant_deaths` — singleton
> `admission_type` / `first_careunit` levels among the first ICU stays fold into the most
> common level, then every level of the tracer's four covariates gets a death and a
> survivor (0.2.0 had 5 degenerate levels, 0.3.0 has 0; 22 deaths / 186 admissions, 4
> planted; no careunit relabel was needed); `nulls_last=True` in the writer and the check
> (CTR-1). 50,746 rows / 5.09 MiB; 13 of 31 CSVs moved (admissions, patients, the order
> chain, labevents, microbiologyevents — the NULLS LAST tables — inputevents /
> ingredientevents; diagnoses, transfers, icustays and chartevents are byte-identical).
> Earlier test edited: `test_ep169` only (its two literal `0.2.0` pins now read
> `write.GENERATOR_VERSION` and floor at 0.2.0 — the churn rule's "shipped fact changed"
> case); every earlier `mwh verify EP-k` still exits 0.
>
> **Design calls (routine, logged here).** (1) Code sets and lab conversions are
> **inlined** into the SQL: the statement is the definition, runs on any connection that
> sees the tier's relations and needs no macro — the `mwh_harmonize` family re-expands per
> call and scanned the fixture's 9,619 lab rows in 43 s (50 s / 5.6 GB RSS in the step; 0.13
> s inlined), recorded in `docs/gotchas.md` §1 for EP-55. (2) The materialised versions
> land as `phenotypes."<id>@<version>"` views through EP-37's walker (versions coexist on
> disk and in the catalog) and the session surface is `mimiciv_derived.phenotype_<id>` =
> the latest semver built on the tier, plus the `_hadm` companion for subject grain; the
> `phenotypes` schema stays off `safe.ALLOWED_SCHEMAS` (parked, PHE-6). (3) One nested
> `kind: phenotype` run per phenotype inside the build's `kind: build` run, so
> `mwh runs list --kind phenotype` shows each build with its SQL and reference hashes.
> (4) `n_positive` is blanked below k in `meta.phenotype_versions`, the run params and
> the log (D-33 addendum); the phenotype views are subject-keyed reads under `safe_query`.
> (5) Onset = `least` / `greatest` over the positive leaves a unit satisfies (branch-aware
> onsets parked, PHE-7); temporal operands are inline leaves that count only through
> the temporal leaf; timeless events (diagnoses at `dischtime`, procedures at
> `chartdate`) map to every stay of their admission under the icustay grain. (6) The
> compiled SQL is UTF-8, not ASCII (EP-39's degree / micro signs in the unit
> normalisation); the CLI strings stay ASCII.
>
> **Dev tier (2026-09-06).** `mwh phenotype compile t2dm@1.0.0 --tier dev` (build
> `20260906T223651-dev-765f50f`, run `20260906T223651Z-639b6a`; the phenotype run
> `20260906T223658Z-94e38f`): materialisation 0.15 s (18,322 units, 102,196 bytes),
> `phenotypes.compile` 1.0 s, catalog 2.9 s, build 10.0 s. `mwh phenotype summary
> t2dm@1.0.0 --tier dev` (k = 11, 0 rows suppressed): **18,322 subjects, 2,491 positive,
> 13.6 %**; by era through the `_hadm` companion — 2008–2010 11,291 admissions / 3,666
> (32.5 %), 2011–2013 5,604 / 1,388 (24.8 %), 2014–2016 4,635 / 1,032 (22.3 %),
> 2017–2019 3,514 / 731 (20.8 %), 2020–2022 2,219 / 567 (25.6 %). `mwh runs list --kind
> phenotype` shows the run; `meta.phenotype_versions` carries `def_hash f0511c05bf8b…`
> and the three code-set hashes. Fixture: 120 subjects, 68 positive (the fixture's random
> code sampling draws T2DM codes freely); `summary --k 1` prints the era rows.
>
> **Gates.** `uv run poe test -m ep_41`: 14 passed (fixture); with `--tier dev`: 15
> passed (the dev summary above); `uv run mwh verify EP-41`: 14 passed; `poe check`
> green — ruff check, `ruff format --check`, pyright (0 errors) and the full fixture
> suite: **982 passed, 42 deselected** (the dev / full / demo probes), 434 s;
> `mwh guard` clean over the working tree (690 files); `poe roadmap-check --strict`
> 0 errors, 0 warnings. The frozen-version refusal (phenotype and code set, library and
> every command → exit 3) and the golden-SQL pin are demonstrated in
> `test_frozen_version_refused_and_bump_allowed` / `test_compiler_golden_and_determinism`;
> the fixture-dependent modules of EP-11 … EP-40 were re-run after the regeneration
> (163 passed) before the full suite.
>
> **Owner decisions at the interactive review (2026-09-06).** (1) Commit in the standard
> two steps, no push (the owner pushes) — done, hashes in `README.md`. (2) Keep
> `test_ep169`'s relaxed generator-version pin (reads the shipped version, floors at
> 0.2.0) so later regenerations touch no earlier module (rejected: a literal `0.3.0`).
> (3) Accept that the `phenotypes` catalog schema stays outside `safe.ALLOWED_SCHEMAS` —
> sessions read the latest version through `mimiciv_derived.phenotype_<id>`; a
> version-addressed read is parked as PHE-6 for EP-43 (rejected: allow-listing the schema
> in this brief). (4) Defer the full-tier `--tag phenotypes` build to the next full rebuild
> / EP-42's `phenotypes-full` job (rejected: a background job now).

> **Full-tier record (2026-09-06, after the review; owner-launched).** The owner ran the
> deferred trio the same evening: `mwh build --tier full --tag units --tag codesets --tag
> phenotypes --background --job meta-full` (build `20260906T230902-full-46e84a6`, run
> `20260906T230902Z-cb954b`, log `runs\jobs\meta-full.log`; 7 steps, **87 s** end to end,
> 23:09:02 → 23:10:29 UTC). The phenotype run `20260906T231017Z-69287e` materialised
> `t2dm@1.0.0` on full in **1.3 s**; `codesets.compile` 5,433 member rows,
> `units.variants` 79 variant rows, `units.dictionary` 5,745 rows, catalog 31 tables /
> views. `mwh phenotype summary t2dm@1.0.0 --tier full` (k = 11, 0 rows suppressed):
> **364,627 subjects, 49,599 positive, 13.6 %** — the same share as dev; by era through
> the `_hadm` companion — 2008–2010 227,719 admissions / 72,568 (31.9 %), 2011–2013
> 114,880 / 29,312 (25.5 %), 2014–2016 91,088 / 21,583 (23.7 %), 2017–2019 69,850 /
> 15,922 (22.8 %), 2020–2022 42,491 / 9,337 (22.0 %). `mwh units report --tier full`
> reads the EP-39 table as on dev (63 curated itemids, 16 flagged — the null-unit
> variants and the two chart glucose items). The full catalog therefore carries
> `meta.item_*`, `meta.codeset*`, `meta.gem_*`, `meta.phenotype_versions` and
> `mimiciv_derived.phenotype_t2dm` (+ `_hadm`) from here on; EP-42's `phenotypes-full`
> job adds sepsis-3 / KDIGO.
