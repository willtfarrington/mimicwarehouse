# EP-84 — Repeated encounters / utilization

**Size:** M · **Tier:** fixture+dev+full · **Core/Stretch:** core · **Depends on:** EP-75 (Endpoints A: binary/continuous/count/ordinal) · **Blocks:** EP-89 (Capstone #3), EP-111 (Signature #2: 30-day readmission)

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-74 (Re-plan P4) before execution.

## Context

Capability category 11 (*Repeated-encounter and utilization analysis*). Turns the EP-75 readmission
counts into a utilization module: readmissions, ICU returns, transfers, cumulative LOS, escalation,
and negative-binomial rate models with person-time offsets (`person_time` grain, EP-34). Count
models go through `stats/glm.py` (EP-79 precedes this brief in execution order; the re-plan should
add it to Depends-on). Caveats: encounter order is valid within a subject (per-patient date shift
preserves intervals) but the data window has no calendar end, so readmissions after a subject's
last observed contact are unobservable — every report states this; death after discharge (`dod`,
EP-34 horizon) is a competing outcome; the definitions here are reused verbatim by Signature #2
(EP-111). Theme per D-5: 30-day readmission after ICU-containing index hospitalisations.

## Scope sketch (refine at re-plan)

1. **`src/mimicwarehouse/stats/utilization.py`** — encounter sequences per subject (admissions
   ordered by `admittime`), index-admission rules (first / all with washout / per grain registry),
   readmission indicators + counts at 30 / 90 / 365 d (from EP-75), time-to-readmission, ICU
   returns within the same hadm (bounce-back ≤ 48 h / ≤ 72 h / any), transfer counts per hadm,
   cumulative LOS per subject within follow-up, escalation (ward → ICU after ≥ 24 h on the ward,
   from `transfers`), death-after-discharge flag.
2. **Rates and models** — events per 100 person-years / per 1 000 patient-days; Poisson / NB with
   `offset = log(person_time)` via `stats/glm.py` → rate ratios by age band, sex, sepsis-3, KDIGO
   AKI, discharge disposition, era; overdispersion check; cluster-robust by subject.
3. **Follow-up policy** — cap at 365 d after index; `dod` horizon rule; documented limitation text
   reused by reports and by EP-111.
4. **Representative workflow**: adults, first hospitalisation per subject with an ICU stay,
   discharged alive → 30-day all-cause readmission proportion with Wilson CI (EP-68 rates module),
   readmission counts over 365 d → NB rate ratios; ICU bounce-back ≤ 72 h among ICU stays;
   escalation rate → tables + rate-ratio forest (`viz/`) → Markdown report via EP-59 (claim type
   *associational*; retrospective; unobservable-out-of-network caveat).
5. **Tests** `tests/ep/test_ep84.py` (`@pytest.mark.ep_84`): crafted admission sequences give the
   expected windows / counts; competing death handling; offset correctness vs hand computation;
   small cells suppressed; dev-tier run.

## Out of scope

- Readmission prediction → EP-111; time-to-readmission survival → EP-91; recurrent-event models
  → EP-94; care-unit pathways → EP-83; ED revisits → EP-144.

## Verification / acceptance (sketch)

- `uv run poe test -m ep_84` green on fixture + dev; `uv run --group dev mwh verify EP-84` green.
- Full-tier run as a logged background job (`uv run --group dev mwh build --tier full --select
  analysis.utilization_readmission --background --job ep84-util`); run id + wall time in the
  completion note; tables, forest and report pass `mwh disclose check`.
- Definitions module-documented so EP-111 imports them unchanged.

## Parked → final-roadmap.md

- Risk-standardised readmission ratios (hierarchical, HRRP-style); recurrent-event frailty models
  (UTIL-1).
