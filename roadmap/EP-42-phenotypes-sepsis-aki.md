# EP-42 — Phenotypes: sepsis-3 + KDIGO AKI stage

**Size:** M · **Tier:** fixture+dev+full · **Core/Stretch:** core · **Depends on:** EP-41 (Phenotype engine + T2DM phenotype), EP-38 (Concept fixes/ports for DuckDB 1.5.x) · **Blocks:** EP-54 (Re-plan P3), EP-63 (Phenotype Studio page), EP-68 (Prevalence/incidence/event-rate module), EP-153 (Linkage to structured events)

> **Amended at EP-170 (2026-08-29).** Header facts unchanged; shorthand per the README notation
> table. Item 4 and the acceptance use EP-43's `mwh disclose check`, which does not exist yet in
> the linear order (EP-42 precedes EP-43) [FC-13]. Default resolution (D-43 item 14 — a wording
> fix, no table change): write `phenotype_prevalence.md` through EP-30's `safe.SUPPRESSOR` hook
> (row-wise k = 11), keep it under `runs/` with a "disclosure sidecar pending EP-43" header
> line, and EP-43 checks it retroactively (that acceptance line is added to EP-43 by this
> amendment); read the acceptance's "passes `mwh disclose check`" as that retroactive check.
> Alternatively the owner may move EP-43 before EP-42 — an owner call recorded at EP-33
> (Re-plan P2), which amends the P3 briefs anyway.

> **EP-33 amendment (2026-09-01).** Header facts unchanged. The EP-170 disclosure-ordering
> resolution above (write `phenotype_prevalence.md` through `safe.SUPPRESSOR`, keep it under
> `runs/` with a "sidecar pending EP-43" header, EP-43 checks it retroactively — a wording
> fix, no table move) **stands at EP-33** (D4a, confirmed at the checkpoint); EP-43's
> amendment carries the matching retroactive-check line. The `SUPPRESSOR` hook contract is
> `(df, k, count_columns) -> (df, rows_suppressed)` (fixed by safe.py; EP-43 swaps the
> implementation). Phenotype views are non-registry, subject-keyed reads under `safe_query`
> (EP-41 amendment, ledger P3C-5); the sepsis-3 vs explicit 2x2 and the stage distribution
> are `count(*) FILTER (WHERE ...)` aggregates (EP-33 B1). Full-tier compile jobs peek via
> `mwh jobs --job phenotypes-full --tail N`; `--tier dev --force` never replaces a
> full-complete derived table (EP-33 LDR-1).

## Context

The two phenotypes most of the later representative workflows lean on (D-5 themes: sepsis-3 for
rates/pathways/target-trial, AKI/KDIGO for signature #3 EP-112 and competing risks) are defined
upstream as mimic-code concepts — `mimiciv_derived.sepsis3` (suspicion of infection + SOFA rise
≥ 2 within the −48 h/+24 h window; columns include `stay_id`, `suspected_infection_time`,
`sofa_time`, `sofa_score`, `sepsis3`) and `mimiciv_derived.kdigo_stages` (per `stay_id` ×
`charttime`: creatinine- and urine-output-based `aki_stage`, `aki_stage_smoothed`), built and
patched on all tiers by EP-37/38. This brief wraps them as versioned phenotypes through the EP-41
engine's `concept` leaf (grain `icustay`), adds parameters (KDIGO window, minimum stage), proves
the versioning rules with tests, and records full-tier prevalence. Caveats: sepsis-3 here is the
mimic-code operationalization, not clinical adjudication; KDIGO baseline creatinine follows
mimic-code's `creatinine_baseline`; ages ≥ 89 = 91 and per-patient date shift do not affect these
per-stay definitions; small cells (n < 11) never leave the data root unsuppressed (D-33).

## In scope

1. **`sepsis3@1.0.0`** (`src/mimicwarehouse/phenotypes/defs/sepsis3.yaml`, grain `icustay`) —
   `concept(mimiciv_derived.sepsis3, sepsis3, =, true)`; onset = `suspected_infection_time`
   (record `sofa_time` and `sofa_score` as evidence); references the concept by name + the
   `meta.concept_versions` sql hash/patch id so the phenotype hash pins the concept build.
   Companion `sepsis_explicit@1.0.0` (grain `hadm`) = `diagnosis(sepsis_explicit@1.0.0)` from
   EP-40 for the classic explicit-codes vs sepsis-3 agreement cross-tab.
2. **`kdigo_aki@1.0.0`** (`defs/kdigo_aki.yaml`, grain `icustay`) — parameters `min_stage`
   (default 1) and `window_hours` (default 168 = 7 d from ICU `intime`, matching EP-112);
   `concept(mimiciv_derived.kdigo_stages, aki_stage_smoothed, >=, min_stage)` restricted to
   `charttime` within `[intime, intime + window_hours)`; onset = first qualifying `charttime`;
   evidence = `max_stage_in_window` (0–3) and `stage_at_onset`. Engine change if needed: allow
   `concept` leaves to declare a relative-time restriction using `timesem.sql_hours_since`.
3. **Versioning tests** (`tests/ep/test_ep42.py`, `@pytest.mark.ep_42`; fixture, `dev`,
   `full` opt-in) — editing `window_hours` without a version bump raises the frozen-version
   error; bumping to `kdigo_aki@1.1.0` yields a new hash and both versions coexist in
   `meta.phenotype_versions` and on disk; the phenotype hash changes when the referenced concept's
   sql hash/patch id changes (simulate by pointing at a modified `meta.concept_versions` row);
   crafted synthetic `sepsis3` / `kdigo_stages` frames (ids ≥ 90 000 000, in a temp DuckDB)
   produce the expected flags, onsets and max stages, including a stage-2-only-after-7-days case
   that is negative under the default window and positive under `window_hours=336`.
4. **Dev + full builds with prevalence** — `uv run --group dev mwh phenotype compile
   sepsis3@1.0.0 kdigo_aki@1.0.0 sepsis_explicit@1.0.0 --tier dev` in the foreground, then the
   full tier as a logged background job: `uv run --group dev mwh phenotype compile sepsis3@1.0.0
   kdigo_aki@1.0.0 sepsis_explicit@1.0.0 --tier full --background --job phenotypes-full` (EP-19
   launcher; log `%MWH_DATA_ROOT%\runs\jobs\phenotypes-full.log`; concept tables are already
   materialized, so minutes; poll with `mwh jobs --job phenotypes-full`); `mwh phenotype summary`
   for each on full: n stays, n positive, share, share by era (`hadm_era`), KDIGO stage
   distribution (0/1/2/3), sepsis-3 vs explicit-code 2×2 (per hadm with ≥ 1 ICU stay) — all
   aggregates ≫ 11 on full; record them, the run ids and wall time in the completion note.
   `mwh phenotype summary … --tier full --out %MWH_DATA_ROOT%\runs\<run_id>\` also writes
   `phenotype_prevalence.md` (prevalence tables, KDIGO stage distribution, sepsis-3 vs explicit
   2×2; `Claim type: exploratory`; retrospective statement) through `disclose.suppress`, and
   `uv run --group dev mwh disclose check` on it exits 0 — this is the artefact EP-53 promotes.
5. **Docs** — `docs/methods/phenotypes.md` gains definition cards for sepsis-3, KDIGO AKI and
   explicit sepsis (source concept, parameters, `what_it_does_not_claim`: not adjudicated; KDIGO
   urine-output criteria depend on charting completeness; explicit codes under-ascertain).

## Out of scope

- Prevalence with denominators/CIs and rate estimation → EP-68 (consumes these views).
- Phenotype Studio page (browse/apply/preview) → EP-63.
- Linking phenotypes to notes-derived labels → EP-153 (P10, gated).
- Concept patches themselves → EP-38 (if `sepsis3`/`kdigo_stages` need fixes, hand back there).

## Verification / acceptance

- `uv run poe test -m ep_42` green on fixture and dev; `uv run --group dev mwh verify EP-42` green.
- `meta.phenotype_versions` on dev and full contains `sepsis3@1.0.0`, `kdigo_aki@1.0.0`,
  `sepsis_explicit@1.0.0` with resolved reference hashes; `mwh runs list --kind phenotype --tier
  full` shows the three runs.
- Full-tier prevalence table (per phenotype: n, positive, share; KDIGO stage distribution; sepsis-3
  vs explicit 2×2) recorded in the completion note with run ids, wall time and the
  `phenotypes-full` job id/log path; no cell < 11; `runs/<run_id>/phenotype_prevalence.md` exists
  with its claim-type label and passes `uv run --group dev mwh disclose check`.
- The frozen-version refusal and coexistence of `kdigo_aki@1.0.0` / `@1.1.0` are demonstrated by tests.

## Parked → final-roadmap.md

- Phenotype parameter overrides from the command line (`--param window_hours=336`): a
  study copies the YAML as the next version; in-memory `parameters=` overrides serve tests
  — trigger: EP-63 "apply with parameters" / an EP-68 sensitivity sweep; hazard: a
  materialised variant must never masquerade as the locked pair. *(Mirrored into
  `final-roadmap.md` § 3 as v2 PHE-8 at execution, 2026-09-06.)*
- Complementary suppression of the artefact's 2×2 / distribution tables and the
  retroactive `mwh disclose check` on `runs/<run_id>/phenotype_prevalence.md` — EP-43's
  scope (its amendment already carries the check). *(Mirrored as v2 PHE-9, 2026-09-06.)*

> **Completion note (2026-09-06).** Executed on fixture + dev in the foreground and on
> full as the `phenotypes-full` background job, as briefed.
>
> **Shipped.** `phenotypes/spec.py` — top-level `parameters` (scalar mapping; `$name`
> placeholders resolved into the criteria before validation, `phenotype_from_text(...,
> parameters=)` overrides for in-memory variants), the concept leaf's `window:
> {from_hours, to_hours}` (relative to the key table's anchor through
> `timesem.sql_hours_since`) and `evidence: [{name, column, agg: first|last|min|max,
> default, levels}]` (typed per-unit output columns after `evidence_json`; reserved names
> and temporal operands refused), `def_hash` extended with `parameters` and `concepts`
> only when present (t2dm's hash and golden SQL unchanged); `registry.py` — `ConceptPin`
> / `concept_pin` / `resolve_concepts` (the executed-SQL sha256 of every vendored concept
> a leaf reads, from the committed inventory + patch registry, patch included; `Entry.
> concepts`), `validate` warns on an unpinned derived table; `compiler.py` — the window
> predicate, the evidence columns through leaf → mapped → unit → reduced → final select
> (`arg_min` / `arg_max` / `min` / `max`, `coalesce(default)`), `-- parameters` /
> `-- concepts` header lines, `icustay_hadm_companion_sql`; `runner.py` —
> `ConceptNotBuiltError` / `ConceptPinMismatchError` (the tier's concept status entry
> must carry the pinned sha), keep-going over the selected phenotypes with per-phenotype
> `failed` status before the step raises, concept refs on the `kind: phenotype` run,
> `meta.phenotype_versions.concept_refs`, the icustay `_hadm` companion in
> `register_phenotypes`, `summarize(run=)`, `distribution`, `agreement`,
> `prevalence_report`, `render_prevalence_report` / `write_prevalence_report`
> (`phenotype_prevalence.md`: claim type exploratory, the retrospective statement, the
> "disclosure sidecar pending EP-43" line, every integer through `fmt_int`), `concept_
> steps`; `cli.py` — `compile --background --job`, `summary <ref …> [--report | --out DIR]
> [--no-agreement] [--defs] [--codesets]`, `show` / `list --json` / `validate --json` carry
> parameters, pins and evidence; `defs/sepsis3.yaml`, `defs/kdigo_aki.yaml`,
> `defs/sepsis_explicit.yaml` (+ the lock); `dag/specs/phenotypes.yaml` depends on
> `concept.organfailure.kdigo_stages` / `concept.sepsis.sepsis3`; `tests/ep/golden/
> {sepsis3,kdigo_aki,sepsis_explicit}@1.0.0.sql`; `tests/ep/test_ep42.py` (12 fixture +
> 1 dev + 1 full tests); `docs/methods/phenotypes.md` (parameters, window / evidence,
> concept pins, the icustay companion, the summary / report; cards re-rendered);
> `tests/fixtures/COVERAGE.md`; DESIGN §8 / §15 notes; D-19 / D-33 addenda; README state
> row + quick start; `final-roadmap.md` PHE-8 / PHE-9.
>
> **Design calls (routine, logged here).** (1) Parameters are resolved into the leaves
> and hashed explicitly under `parameters`; CLI overrides are parked (PHE-8) because a
> materialised variant must not masquerade as the locked pair. (2) Evidence is typed
> columns rather than more JSON (the 64-character subject-keyed ceiling and the summary's
> `GROUP BY` both need real columns); the aggregates run over the *qualifying* events, so
> `max_stage_in_window` is the maximum qualifying stage and 0 means "no qualifying row"
> (documented in the definition and the methods page). (3) The concept pin is the
> executed-SQL sha (patch included) resolved from definition text, mirrored by a
> materialisation-time check against the concept's status entry — refusal, not a warning,
> so a phenotype never silently reads a concept built from other SQL; the pinned concept
> steps become `depends_on` of `phenotypes.compile` so the full DAG orders them first,
> while tag / compile runs still require the concepts built (the step records the
> phenotype as failed with the remedy and carries on with the others). (4) The
> icustay-grain `_hadm` companion covers admissions with >= 1 ICU stay (the phenotype is
> only assessable there), which is exactly the brief's 2×2 denominator; `agreement` joins
> each phenotype's admission-level relation, so any pair of grains works. (5) The report
> is written *inside* the analysis run (`Run.safe_query` records every statement and audit
> id); a failed compile stops the build before the `catalog` step, so the catalog's copy of
> `meta.phenotype_versions` refreshes at the next catalog build while the lake-level rows
> and `status.json` show the failure at once. (6) `summary`'s default report pairs every
> requested phenotype (three 2×2 tables for the trio; `--no-agreement` skips them).
> (7) **Snapshot history (found by `poe check`).** Ordering the phenotypes after the
> concepts made `meta.concept_versions` stamp a derived id that no longer matched the
> runner's single end-of-build entry (EP-37's `test_fixture_lake_carries_every_concept`
> asserts the stamp is a recorded snapshot; in EP-41 the phenotypes happened to run
> first). Fix in the shared machinery rather than the test: a step that stamps a layer id
> appends it through the new `dag.snapshot.record_snapshot_once` (`meta.concept_versions`
> and `phenotypes.compile` both do), and the runner's end-of-build entry is skipped only
> when the same build already recorded the identical id — every stamped id is a recorded
> state whatever the producer order, a core-only build still leaves exactly one entry
> (the EP-19 pin), `Run.read_layer` keeps the latest entry. Earlier **modules** touched
> for it (code, no earlier test edited): `dag/snapshot.py`, `dag/runner.py`,
> `concepts/runner.py` (DESIGN §11 note; `test_ep42` pins both stamps on the session
> lake).
>
> **Earlier tests edited (churn rule — a shipped fact changed: the registry contents).**
> `test_ep41.py` only: the exact registry pins (`refs() == ("t2dm@1.0.0",)`, the
> `list --json` order, `lock_dir(...).unchanged`, the study-registry set) became
> membership checks; the module's lake fixture selects `t2dm@1.0.0` through
> `compile_options` (the concept-backed phenotypes need their concepts, which
> `test_ep42`'s lake builds with `with_deps`); the DAG dependency pin became a superset
> (the concept steps joined `depends_on`); the session-lake pin reads `latest_versions(con)
> ["t2dm"]`. Every earlier `mwh verify EP-k` still exits 0 (`poe check` below).
>
> **Dev tier (2026-09-06).** `mwh phenotype compile sepsis3@1.0.0 kdigo_aki@1.0.0
> sepsis_explicit@1.0.0 --tier dev` (build `20260907T000930-dev-41fbf65`, run
> `20260907T000930Z-32bbd9`): `phenotypes.compile` 1.3 s (three materialisations of
> 0.02–0.04 s each), catalog 2.9 s, build 10.2 s. `mwh phenotype summary … --tier dev
> --report` (k = 11, 0 rows suppressed; analysis run `20260907T001135Z-132f3c`, 3.2 s,
> 13 audited reads; `runs\20260907T001135Z-132f3c\phenotype_prevalence.md`):
> **sepsis3 2,036 / 4,672 stays (43.6 %)**, **kdigo_aki 3,311 / 4,672 (70.9 %)**,
> **sepsis_explicit 1,158 / 27,263 admissions (4.2 %)**; KDIGO stage distribution
> 0 / 1 / 2 / 3 = 1,361 / 817 / 1,417 / 1,077 stays; sepsis-3 × explicit per admission
> with ≥ 1 ICU stay (n = 4,207): both 621, sepsis-3 only 1,293, explicit only 80, neither
> 2,213.
>
> **Full tier (2026-09-06).** `mwh phenotype compile sepsis3@1.0.0 kdigo_aki@1.0.0
> sepsis_explicit@1.0.0 --tier full --background --job phenotypes-full` (job
> `phenotypes-full`, log `runs\jobs\phenotypes-full.log`; build
> `20260907T001149-full-41fbf65`, run `20260907T001149Z-3df8ec`; **10.6 s** end to end,
> 00:11:49 → 00:12:00 UTC — the concept tables were already materialised): phenotype
> runs `20260907T001156Z-c09262` (kdigo_aki, 0.21 s), `20260907T001156Z-99ba05`
> (sepsis3, 0.13 s), `20260907T001156Z-d60fcb` (sepsis_explicit, 0.26 s);
> `phenotypes.compile` 1.6 s (734,944 rows), catalog 2.9 s; `mwh runs list --kind
> phenotype --tier full` shows the three beside EP-41's t2dm run. `mwh phenotype summary
> … --tier full --report` (k = 11, 0 rows suppressed; analysis run
> `20260907T001401Z-aec143`, 3.8 s, 13 audited reads;
> `runs\20260907T001401Z-aec143\phenotype_prevalence.md` — the artefact EP-43 checks
> retroactively and EP-53 promotes):
>
> | phenotype | unit | n | positive | share |
> |---|---|---|---|---|
> | `sepsis3@1.0.0` | ICU stay | 94,458 | 41,296 | 43.7 % |
> | `kdigo_aki@1.0.0` | ICU stay | 94,458 | 67,981 | 72.0 % |
> | `sepsis_explicit@1.0.0` | admission | 546,028 | 22,533 | 4.1 % |
>
> By era (`hadm_era`): sepsis-3 47.6 / 46.6 / 45.0 / 37.1 / 35.3 % of stays and explicit
> codes 3.2 / 3.9 / 4.8 / 5.3 / 6.5 % of admissions over 2008–2010 … 2020–2022 (the two
> operationalisations drift in opposite directions across the ICD switch); KDIGO AKI
> 75.0 / 71.0 / 70.6 / 70.1 / 70.3 %. KDIGO stage distribution (stays, 7-day window):
> stage 0 26,477 · stage 1 16,152 · stage 2 30,613 · stage 3 21,216. Sepsis-3 vs explicit
> codes per admission with ≥ 1 ICU stay (n = 85,242): both 12,066 · sepsis-3 only 26,873
> · explicit only 2,142 · neither 44,161 (sepsis-3 flags 39,0 k admissions, the codes
> 14,2 k; 85 % of coded admissions meet sepsis-3). Sepsis-3 × KDIGO: both 32,621 ·
> sepsis-3 only 6,318 · AKI only 28,180 · neither 18,123. No cell below 11 anywhere.
>
> **Gates.** `uv run poe test -m ep_42`: 12 passed (fixture, ≈ 125 s incl. the concept
> chains on the module lake); `--tier full`: the dev and full probes pass (aggregates
> printed above); `uv run poe test -m ep_41`: 14 passed after the edits; `uv run mwh
> verify EP-42`: 12 passed, `EP-41`: 14 passed, `EP-37`: 17 passed (fresh interpreters);
> `poe check` green — ruff check, `ruff format --check` (155 files), pyright (0 errors)
> and the full fixture suite **994 passed, 44 deselected** (the dev / full / demo
> probes), 593 s; `mwh guard` clean over every changed file (29 files, paths mode);
> `poe roadmap-check --strict` 0 errors, 0 warnings. The frozen-version
> refusal (parameter edit; moved concept hash), the `kdigo_aki@1.0.0` / `@1.1.0`
> coexistence and the concept-pin refusal at materialisation are demonstrated in
> `test_frozen_parameter_edit_refused_bump_coexists_and_concept_hash_pins` and
> `test_lake_concept_pin_mismatch_refused_and_second_version_coexists`; the
> stage-2-only-after-7-days case (negative by default, positive under `window_hours=336`)
> in `test_crafted_concept_frames`. `mwh disclose check` does not exist yet (EP-43): the
> artefact carries the "sidecar pending" line per the EP-170 / EP-33 amendments.
>
> **Owner decisions at the interactive review (2026-09-06, every recommended option
> taken).** (1) Commit in the standard two steps, no push (the owner pushes) — done,
> hashes in `README.md`. (2) Keep the verbatim restoration of the **D-34 heading** in
> `DECISIONS.md` (consumed by EP-41's D-33 addendum edit; recovered from `963ab67`) with
> its dated correction note (rejected: restore silently; leave HEAD's damage). (3) Accept
> the five membership-relaxed pins in `test_ep41.py` as the churn-rule exception
> (rejected: an isolated t2dm-only registry fixture for that module). (4) Keep
> `summary --report`'s all-pairs 2×2 default with `--no-agreement` as the opt-out
> (rejected: sepsis-3 × explicit only; opt-in only).
