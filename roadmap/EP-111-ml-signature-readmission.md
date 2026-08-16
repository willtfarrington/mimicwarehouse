# EP-111 — Signature #2: 30-day readmission

**Size:** M · **Tier:** fixture+dev+full · **Core/Stretch:** core · **Depends on:** EP-110 (Signature #1: first-24h → in-hospital mortality), EP-84 (Repeated encounters / utilization) · **Blocks:** EP-126 (Capstone #5)

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-101 (Re-plan P6) before execution.

## Context

Second of the polished trio (D-6), same protocol → holdout → assessment → card path as EP-110 but a
different grain (`hadm`) and clinical theme (D-5: readmission). The readmission endpoint comes from
the utilization module (EP-84) and the endpoint library (EP-75). MIMIC caveats that shape the
protocol: only BIDMC readmissions are observable; the per-patient date shift preserves within-patient
intervals, so 30-day gaps are valid; death after discharge is only visible through `dod` (≈ 1-year
horizon) and competes with readmission; MIMIC has no planned/unplanned flag, so "readmission" is any
subsequent hospital admission and the protocol says so. Categories 20 and 37.

## Scope sketch (refine at re-plan)

1. **Protocol YAML** (`protocols/sig2-readmission-30d.yaml`): cohort = hospital admissions of adults
   discharged alive (excluding discharges to hospice, per EP-75 disposition ordinal); index = discharge
   time; outcome = any hospital admission within 30 days (EP-84 definition); death within 30 days
   without readmission handled as a pre-declared sensitivity (primary: negative; sensitivity: excluded);
   features at discharge — demographics, LOS, ICU use, prior admissions in the preceding 365 days
   (within-patient relative time), Charlson/Elixhauser via code sets, last-value labs, discharge
   disposition, medication class counts (features must be knowable at discharge; the EP-102 leakage
   assertion is extended with an `index = discharge` window check); temporal split by
   `anchor_year_group`; models LR vs LightGBM; claim `predictive`.
2. **Runner reuse** — `run_signature` from EP-110 with a `hadm` grain; the only new code is the
   feature spec, the sensitivity switch and the readmission-specific limitations block in the card.
   The protocol is frozen with `uv run --group dev mwh protocol freeze
   protocols/sig2-readmission-30d.yaml` (EP-51) before any dev/full run and the runner refuses an
   unfrozen/modified hash; assessment (AUROC, calibration slope, DCA 5–30 %, cluster-bootstrap CIs)
   comes from EP-105 `assess_binary`; both models are registered and carded via EP-106
   `register_model`/`render_card`; the card's leakage/drift audit slot is filled by EP-119
   (`mwh ml audit <model_id>` on the readmission champion) and the audit run id is recorded in this
   brief's completion note once EP-119 lands.
3. **Runs** — fixture and dev in-session; full tier as a logged background job; report artefact with
   calibration, DCA (thresholds 5–30 %), per-era table.
4. **Tests** (`tests/ep/test_ep111.py`, `@pytest.mark.ep_111`): a fixture admission with a follow-up
   admission at day 31 is a negative and at day 29 a positive; the leakage assertion refuses a feature
   drawn from after discharge; both sensitivity arms run and are recorded.

## Out of scope

- Causal questions about discharge interventions → P6 (EP-95/96); utilization descriptives → EP-84.
- ED-visit-based revisits → EP-144 (after ED ingestion).

## Verification / acceptance (sketch)

- `uv run poe test -m ep_111` green on fixture (+dev); `uv run --group dev mwh verify EP-111` green.
- Protocol hash and full-tier run id (background job, `%MWH_DATA_ROOT%\runs\jobs\ep111.log`) recorded;
  models registered with cards; report passes `mwh disclose check` and is promoted with its sidecar.
