# EP-112 — Signature #3: AKI within 7 d (time-to-event prediction)

**Size:** M · **Tier:** fixture+dev+full · **Core/Stretch:** core · **Depends on:** EP-110 (Signature #1: first-24h → in-hospital mortality), EP-93 (Competing risks (Aalen–Johansen; cause-specific; Fine–Gray via gpl optional)) · **Blocks:** EP-126 (Capstone #5)

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-101 (Re-plan P6) before execution.

## Context

Third of the polished trio (D-6): time-to-event prediction with competing risks, using the KDIGO AKI
phenotype (EP-42) as outcome and the competing-risks machinery of EP-93. Death and discharge alive
before AKI are competing events (roadmap risk 9), so predicted 7-day risk must be an absolute
(cumulative-incidence) risk, not a naive Kaplan–Meier complement. Only permissive libraries are used
(lifelines, scikit-learn, LightGBM); scikit-survival stays in the optional `gpl` group and is parked
(D-34). Categories 20, 18 and 37.

## Scope sketch (refine at re-plan)

1. **Protocol YAML** (`protocols/sig3-aki-7d.yaml`): cohort = first ICU stay adults with a baseline
   creatinine and no AKI (KDIGO stage 0) and no ESRD/dialysis code-set history at the landmark; landmark
   = ICU intime + 24 h; features = EP-102 first-24 h groups + creatinine trajectory features (EP-82);
   outcome = time from landmark to first KDIGO stage ≥ 1 within 7 days; competing events = death, ICU
   discharge alive; censoring at 7 days; temporal split by `anchor_year_group`; claim `predictive`.
2. **Models** (`src/mimicwarehouse/ml/signatures.py` extension): (a) cause-specific Cox (lifelines,
   L2) with Aalen–Johansen absolute risk at 7 d (EP-93); (b) discrete-time person-day LightGBM
   (day dummies + features → daily cause-specific hazards → 7-day cumulative incidence).
3. **`assess_tte` extension** (`src/mimicwarehouse/ml/assess.py`): time-dependent AUC and IPCW Brier at
   7 d, calibration of predicted 7-d risk (deciles, small bins suppressed), DCA at 7 d; cluster
   bootstrap CIs.
4. **Runs** — fixture and dev in-session; full tier as a logged background job; cards for both models;
   report artefact. The protocol is frozen with `uv run --group dev mwh protocol freeze
   protocols/sig3-aki-7d.yaml` (EP-51) before any dev/full run; both models are registered and carded
   via EP-106; the leakage/drift audit link on each card is filled by EP-119 (`mwh ml audit
   <model_id>`), whose drift table for this model uses the `assess_tte` per-era metrics
   (time-dependent AUC and IPCW Brier at 7 d by `anchor_year_group`) added in item 3; the audit run
   id is recorded in the completion note once EP-119 lands.
5. **Tests** (`tests/ep/test_ep112.py`, `@pytest.mark.ep_112`): person-day expansion has one row per
   stay-day until event/competing event/censor; cumulative incidences of the two competing causes and
   event-free probability sum to 1; a stay with AKI before the landmark is *excluded*.

## Out of scope

- Random survival forests / gradient-boosted survival / DeepSurv → parked (final-roadmap 18).
- Fine–Gray → EP-93 (`gpl` optional) / parked; AKI epidemiology and recurrent AKI episodes → EP-76/84.

## Verification / acceptance (sketch)

- `uv run poe test -m ep_112` green on fixture (+dev); `uv run --group dev mwh verify EP-112` green.
- Protocol hash and full-tier run id (background job, `%MWH_DATA_ROOT%\runs\jobs\ep112.log`) recorded;
  report with 7-d calibration/DCA passes `mwh disclose check` and is promoted with its sidecar.
