# EP-42 — Phenotypes: sepsis-3 + KDIGO AKI stage

**Size:** M · **Tier:** fixture+dev+full · **Core/Stretch:** core · **Depends on:** EP-41 (Phenotype engine + T2DM phenotype), EP-38 (Concept fixes/ports for DuckDB 1.5.x) · **Blocks:** EP-54 (Re-plan P3), EP-63 (Phenotype Studio page), EP-68 (Prevalence/incidence/event-rate module), EP-153 (Linkage to structured events)

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
