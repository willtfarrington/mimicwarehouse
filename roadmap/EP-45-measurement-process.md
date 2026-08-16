# EP-45 — Measurement-process summaries

**Size:** M · **Tier:** fixture+dev+full · **Core/Stretch:** core · **Depends on:** EP-44 (Data-quality profiling) · **Blocks:** EP-54 (Re-plan P3), EP-72 (Missing-data views)

## Context

In ICU data, *whether* something was measured carries information (informative presence), and
absence has two very different causes: structural (an item is not charted in that care unit or
era at all) versus unmeasured (the item is in use but this stay did not get it). Capability 7
(missing-data & measurement-process analysis) starts here with the descriptive half:
measurement frequency by ICU hour/day for the curated itemids (EP-39), structural-vs-unmeasured
classification by care unit × era (EP-34's `anchor_year_group` is the only admissible era axis),
and informative-presence summaries labelled exploratory. Builds on `qc/profile.py` (EP-44) and
writes `src/mimicwarehouse/qc/measurement.py` (DESIGN §15). Full-tier scans touch `chartevents`
and `labevents` restricted to ≤ 60 itemids (fast with Parquet pushdown; still a background job,
D-18). All outputs are aggregates; k = 11 suppression via `disclose` (EP-43) before any rendering.
D-5 (own theme per category — here the theme is first-24 h vitals/labs measurement) and D-33 apply.

## In scope

1. **Hourly/daily measurement frequency** (`src/mimicwarehouse/qc/measurement.py`) — for a
   configurable itemid set (default: all `curated=True` items in `meta.item_units`), per ICU stay:
   measurements per hour bin (`hours_since_icu_intime`, `[0,1) … [0,168)`), per ICU day, and
   `n_stays_at_risk` per bin (stays still in the ICU); population tables `meta.mp_item_hourly`
   (itemid, hour_bin, n_stays_at_risk, n_stays_measured, n_measurements), `meta.mp_item_daily`,
   `meta.mp_item_summary` (itemid, share_measured_first_24h, median measurements per stay-day,
   median inter-measurement interval min, p10/p90) — grain and bins from `timesem`.
2. **Structural absence vs unmeasured** — `meta.mp_structural` (itemid, first_careunit, era,
   n_stays, n_stays_measured, share, `structural_flag`): flag `structural` when share = 0 for a
   (unit, era) cell with n_stays ≥ 50, `sparse` when share < 5 %, else `in_use`; per-stay
   attribution then classifies each missing item as structural (its unit×era cell is structural)
   or unmeasured; `meta.mp_absence_summary` (itemid, n_missing_first_24h, n_structural,
   n_unmeasured).
3. **Informative-presence summaries** (exploratory) — for each curated lab: in-hospital
   mortality (from `admissions.hospital_expire_flag`) rate among stays *with* vs *without* a
   measurement in the first 24 h, rate ratio with a Wald 95 % CI (statsmodels), n per arm; also
   count-tertile version (0 / 1–2 / ≥ 3 measurements); table `meta.mp_presence_outcome` with a
   `claim_type = 'exploratory'` column and a note that this is descriptive association only.
4. **DAG + report + CLI** — DAG spec `src/mimicwarehouse/dag/specs/measurement.yaml` (python
   steps `measurement.hourly`, `measurement.structural`, `measurement.presence`,
   `measurement.report`; tag `measurement`; itemid set via the step's params) inside
   `run.start(kind="qc")`; `runs/<run_id>/measurement_process.md` with the three summaries
   (suppressed, reproduction block); `mwh qc measurement --tier dev` prints summary + top structural
   cells. Full tier: `uv run --group dev mwh build --tier full --tag measurement --background --job
   measurement-full` (log `%MWH_DATA_ROOT%\runs\jobs\measurement-full.log`), poll with `mwh jobs
   --job measurement-full` (expected 5–20 min), record run id/timing; while there, verify EP-44's
   full QC run (`mwh jobs --job qc-full`) if EP-44 deferred it (append its completion note).
5. **Tests + docs** (`tests/ep/test_ep45.py`, `@pytest.mark.ep_45`; fixture, `dev`) — crafted
   synthetic stays: hourly counts sum to total measurements; `n_stays_at_risk` decreases at
   `outtime`; a unit×era cell with zero measurements and ≥ 50 stays is `structural`; per-stay
   attribution splits missing into structural/unmeasured with the expected counts; the rate ratio
   matches a hand computation; the report passes `disclose.check`; on dev, tables build for the
   default itemid set. `docs/methods/measurement-process.md` (new): definitions, thresholds,
   caveats (charting practice varies by unit and era; MetaVision-only ICU data; no calendar time).

## Out of scope

- Missingness-pattern heatmaps and page → EP-72; imputation strategies (MICE etc.) → EP-87.
- Formal informative-presence models / MNAR sensitivity → parked (`final-roadmap.md` § 7).
- Prevalence/rate estimators with denominators → EP-68.

## Verification / acceptance

- `uv run poe test -m ep_45` green on fixture and dev; `uv run --group dev mwh verify EP-45` green.
- `uv run --group dev mwh build --tier dev --tag measurement` writes `meta.mp_item_hourly`,
  `meta.mp_item_daily`, `meta.mp_item_summary`, `meta.mp_structural`, `meta.mp_absence_summary`,
  `meta.mp_presence_outcome`; `uv run --group dev mwh qc measurement --tier dev` prints them.
- `mwh disclose check` exits 0 on `runs/<run_id>/measurement_process.md`.
- Full-tier run id, wall time, peak RSS recorded in the completion note; EP-44's completion note
  appended if it was deferred here.
