# EP-145 — Second subject-keyed PhysioNet source via wizard (stretch)

**Size:** M · **Tier:** fixture+dev · **Core/Stretch:** stretch · **Depends on:** EP-141 (Linkage Wizard B (validate → coverage → commit)) · **Blocks:** —

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-136 (Re-plan P8) before execution.

## Context

Stretch proof that the wizard generalizes beyond ED (D-36: "other PhysioNet datasets"): a second
subject-keyed source that shares MIMIC-IV's `subject_id` space but arrives with *different* keys and
a temporal rather than explicit admission link. **Default candidate:** MIMIC-IV-ECG 1.0
`machine_measurements.csv` (numeric ECG intervals/axes; keyed by `subject_id` + `study_id` +
`ecg_time`, no `hadm_id`; the `report_*` columns are free text and are dropped at mapping time).
**Alternative:** MIMIC-CXR-JPG metadata + CheXpert labels (`subject_id`, `study_id`, study
date/time). Either requires its own PhysioNet DUA, which the owner signs *before* the session and
records in the register (GOVERNANCE §1, §10); the files land under `%MWH_DATA_ROOT%\ext\<source>\`
(never G:/D:). Timestamps in these datasets are shifted consistently with MIMIC-IV per subject, so
event-time → admission-window linkage is valid within patient. Tier is fixture + dev only; a full
commit is left to the owner's discretion (record it if run). Category 35.

## Scope sketch (refine at re-plan)

1. **Owner pre-step + register** — DUA signed, download to `ext/<source_id>/`, `source.yaml` with
   license 1.5.0, DOI, manifest; profile flags `report_*` (ECG) as free text; mapping role `drop`
   for them.
2. **Temporal key inference** — extend `linkage/mapping.py` with a `derived_key` rule
   (`hadm_id := admission whose [admittime, dischtime] contains ecg_time`, ties → earliest; ICU
   `stay_id` analogously) and `linkage/validation.py` with the matching cardinality/coverage
   checks (share of events inside any admission, per era; multiple-match rate); the wizard's Map
   step exposes the rule.
3. **Synthetic fixture** — `tests/fixtures/ext/ecglike/` (ids ≥ 90 000 000, numeric columns, a
   dummy `report_0`) with events planted inside/outside fixture admissions.
4. **Wizard run on dev** — profile → map → validate → coverage → commit into `mimiciv_ecg.*`
   (or `mimiciv_cxr.*`, mirroring the mimic-code schema naming), bucketed by `subject_bucket`;
   coverage-by-era table recorded
   (suppressed) in the completion note; register status `committed` (dev).
5. **Tests** `tests/ep/test_ep145.py` (`@pytest.mark.ep_145`; fixture + dev): derived-key rule links
   the planted events correctly and leaves out-of-window events unlinked; free-text columns are
   absent from the committed table; `safe_query` over the new schema returns aggregates only.

## Out of scope

- Any waveform/image loading (metadata and machine measurements only).
- Analyses using the new source → v2 case study; further sources (eICU, waveform indices) →
  final-roadmap LINK-*.

## Verification / acceptance (sketch)

- `uv run poe test -m ep_145` green on fixture + dev; `uv run --group dev mwh verify EP-145` green.
- `mwh link status <source_id>` = `committed` on dev; coverage report passes `mwh disclose check`;
  the register markdown lists the source with its DUA date; full-tier run id recorded only if the
  owner chose to run it.

## Parked → final-roadmap.md

- Remaining PhysioNet companions (MIMIC-IV-ECHO, MIMIC-IV waveform indices, eICU-CRD external
  validation) — trigger: a workflow that needs them (final-roadmap LINK-*, EXT-1).
