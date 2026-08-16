# EP-144 — ED-enabled workflow (ED triage → admission; time-to-antibiotics)

**Size:** M · **Tier:** fixture+dev+full · **Core/Stretch:** core · **Depends on:** EP-142 (ED ingestion via wizard → mimiciv_ed + ED concepts), EP-86 (Exposure-response / treatment patterns) · **Blocks:** EP-146 (Capstone #7)

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-136 (Re-plan P8) before execution.

## Context

The linkage only counts if it enables an analysis that hosp + icu alone could not do (D-4). This is
the representative ED-enabled workflow for category 35, reusing the exposure-response module
(EP-86), the GLM suite (EP-79/80), the cohort compiler (EP-47), the sepsis-3 phenotype (EP-42) and
the protocol freeze (EP-51). Clinical theme (D-5): **ED triage → admission with suspected
infection; time from ED arrival to first antibiotic** — the antibiotics-timing theme named in the
brief-writing rules. Caveats that bite: ED 2.2 = 2011–2019 (era filter is part of the cohort);
per-patient shift → only within-patient intervals; `dod` ~1 y horizon (in-hospital mortality
avoids it); confounding by indication → **associational** claim, never causal; all analyses
retrospective.

## Scope sketch (refine at re-plan)

1. **Cohort spec** `cohorts/ed_admitted_suspected_infection.yaml` (grain `edstay`, EP-46 model):
   ED stays with `disposition = 'ADMITTED'` and non-null `hadm_id`, adults, era 2011–2019 by
   construction, suspicion of infection (mimic-code `suspicion_of_infection` or EP-42 sepsis-3
   phenotype) within 24 h of `edstays.intime`; first ED stay per admission; attrition table via
   EP-47/48.
2. **Exposure & outcome** — exposure = hours from `edstays.intime` to the first antibiotic:
   primary `min(mimiciv_derived.ed_meds.charttime where antibiotic, first hosp emar administration
   in the antibiotic set)`; sensitivity: mimic-code `antibiotic` prescriptions `starttime`. Bands
   ≤ 1 h / 1–3 h / 3–6 h / > 6 h plus continuous with a restricted cubic spline (EP-80). Outcome =
   in-hospital mortality (`admissions.hospital_expire_flag`, EP-75 endpoint); secondary = ICU
   admission within 48 h of ED arrival.
3. **Method** — EP-86 treatment-pattern summaries (timing distribution by triage acuity, arrival
   transport, era) and dose-response curve; logistic GLM (EP-79) adjusted for age, sex, triage
   acuity, triage vitals (harmonized, EP-142 `ed_triage`), era, with cluster-robust SEs by
   `subject_id`; E-value style sensitivity from EP-97 if available; protocol frozen with
   `mwh protocol freeze` (claim type associational) **before** the full run.
4. **Validation** — fixture run on the ED-like fixture with a planted timing–outcome association;
   dev run for smoke; full run as a logged background job (`uv run --group dev mwh protocol run
   <hash> --tier full`, EP-19 launcher, log `%MWH_DATA_ROOT%\runs\jobs\ep144-ed-abx.log`; job id
   in the completion note); attrition and n per band checked
   for small cells; missing-triage-vitals handling documented (complete-case + indicator, EP-87).
5. **Report artifact** — EP-130 report `reports/ed-time-to-antibiotics.{md,html}` (label
   *associational*, retrospective statement, "What it deliberately does not claim": causal effect of
   earlier antibiotics) and `docs/analyses/NN-ed-time-to-antibiotics.md` case study with a
   Reproduction block citing run ids, protocol hash and snapshot ids.
6. **Tests** `tests/ep/test_ep144.py` (`@pytest.mark.ep_144`; fixture + dev, full opt-in): cohort
   compiles with the `edstay` grain; planted association recovered on the fixture; every table in the
   report passes `disclose.check`; the protocol hash in the report matches `runs/protocols.jsonl`.

## Out of scope

- Causal/target-trial framing of antibiotic timing → EP-95 machinery, a v2 case study.
- New ED derived tables beyond EP-142 (e.g., chief-complaint categories) → parked in EP-142.
- App page for ED analyses → not mandated for category 35 (wizard is the UI).

## Verification / acceptance (sketch)

- `uv run poe test -m ep_144` green on fixture + dev; `uv run --group dev mwh verify EP-144` green.
- Full-tier run id (protocol hash cited) and wall time recorded in the completion note; the report
  passes `uv run --group dev mwh disclose check reports/ed-time-to-antibiotics.html` and carries the
  `.disclosure.json` sidecar; case study numbers reproduce from the recorded run ids.
