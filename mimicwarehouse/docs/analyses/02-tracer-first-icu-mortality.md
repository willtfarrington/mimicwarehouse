# 02 - Tracer bullet: first ICU stay of adult patients -> in-hospital mortality (P2)

> Promoted at EP-53 from the EP-31 run folder `runs/tracer/20260917T213352-full/` through the
> disclosure gate (`mwh disclose check --write-sidecar`, EP-43): the body is re-rendered
> from the run's JSON aggregates after chain-mode suppression of the attrition,
> table-mode suppression of the descriptives and the n-vs-n_fit rule (EP-43 amendment
> b), so a `~n` total or a `suppressed` cell here may be exact in the run folder. The
> sidecar sits beside this file and beside every JSON aggregate in the same-named folder.
> Regenerate with `mwh tracer --tier full --background --job tracer-full` and
> `python -m mimicwarehouse.analyses.c01_concepts_qc promote-tracer --run <stamp>`.

**Claim type: associational (exploratory).** MIMIC-IV analyses are retrospective.

*Reader guide (DS/ML):* the first end-to-end analysis through the safe-query gate - cohort attrition, k-suppressed descriptives and a logistic regression with robust errors, every number audited and reproducible from a run id; the zero-cell handling is the interesting engineering.
*Reader guide (clinical informatics):* admission characteristics associated with in-hospital death on a first ICU stay - an association in retrospective, de-identified data with shifted dates and a capped age; not a risk score and not a causal claim.

Run `20260917T213352-full` - tier `full` (EP-31); ledger run `20260917T213345Z-4376d1` (EP-35). The JSON aggregates the report
renders from are in [02-tracer-first-icu-mortality/](02-tracer-first-icu-mortality/): [attrition.json](02-tracer-first-icu-mortality/attrition.json), [descriptives.json](02-tracer-first-icu-mortality/descriptives.json), [model.json](02-tracer-first-icu-mortality/model.json).

## Question

Among adult patients, on their first ICU stay, what is the association between
admission characteristics (age at admission, gender, admission type, first care
unit, anchor-year era) and in-hospital mortality?

## Data

- Tier catalog: `full` - core snapshot `b1fc53134348f3b4ede369ed6ae27424c4988fafb45065c87a09907f7a410eca`.
- MIMIC-IV analyses are retrospective.
- Outcome: in-hospital death (discharge alive is the competing state; the
  post-discharge death date is not used).
- Age at admission is derived from the anchor fields; ages >= 89 appear as 91,
  so age is capped at 91.
- The anchor-year era (3-year windows) is the only temporal axis and enters as a
  covariate, never a calendar.

## Cohort

One row per criterion, counted through the audited safe-query wrapper:

| step | n |
|---|---|
| base | 94,458 |
| first_stay | 65,366 |
| adult | 65,366 |
| complete | 65,366 |
| cohort | 65,366 |

## Descriptives

k = 11 suppression applies to both count columns (a row with any count in
1..10, or a row that would let such a cell be backed out, is withheld -
the disclose hook, EP-43); suppressed rows are counted below each table.

### Mortality by age band x gender

| age band | gender | n | deaths |
|---|---|---|---|
| 18-39 | F | 2,886 | 132 |
| 18-39 | M | 3,551 | 193 |
| 40-64 | F | 9,270 | 773 |
| 40-64 | M | 14,160 | 1,149 |
| 65-79 | F | 9,219 | 1,105 |
| 65-79 | M | 12,514 | 1,328 |
| 80+ | F | 7,271 | 1,234 |
| 80+ | M | 6,495 | 1,172 |

Suppressed rows: 0.

### Mortality by first care unit

| first care unit | n | deaths |
|---|---|---|
| Cardiac Vascular Intensive Care Unit (CVICU) | 11,520 | 376 |
| Coronary Care Unit (CCU) | 7,371 | 933 |
| Medical Intensive Care Unit (MICU) | 12,517 | 2,001 |
| Medical/Surgical Intensive Care Unit (MICU/SICU) | 10,053 | 1,398 |
| Neuro Intermediate | 4,314 | 71 |
| Neuro Stepdown | 1,102 | 15 |
| Neuro Surgical Intensive Care Unit (Neuro SICU) | 1,313 | 366 |
| Surgery/Vascular/Intermediate | 95 | 26 |
| Surgical Intensive Care Unit (SICU) | 9,068 | 1,047 |
| Trauma SICU (TSICU) | 7,914 | 830 |

Suppressed rows: 7.

## Model

**Claim type: associational (exploratory).**

Logistic regression (treatment coding, HC1 robust errors);
n = 65,342, events = 7,086;
AUC = 0.731 (in-sample, optimistic - not a
prediction claim).

4 rare covariate level(s) had a
zero cell (no events, or no non-events) and are inestimable by maximum
likelihood; their rows are excluded, which fits the remaining
coefficients exactly (excluded rows: 24):

- admission_type=AMBULATORY OBSERVATION (zero-event)
- first_careunit=Med/Surg (zero-event)
- first_careunit=Medicine/Cardiology Intermediate (zero-event)
- first_careunit=Neurology (zero-event)

| term | OR | 95% CI |
|---|---|---|
| gender=M | 1.037 | 0.984 - 1.092 |
| admission_type=DIRECT OBSERVATION | 1.050 | 0.604 - 1.826 |
| admission_type=ELECTIVE | 0.299 | 0.222 - 0.403 |
| admission_type=EU OBSERVATION | 1.258 | 0.891 - 1.776 |
| admission_type=EW EMER. | 0.988 | 0.855 - 1.141 |
| admission_type=OBSERVATION ADMIT | 0.887 | 0.758 - 1.036 |
| admission_type=SURGICAL SAME DAY ADMISSION | 0.168 | 0.132 - 0.214 |
| admission_type=URGENT | 1.219 | 1.051 - 1.414 |
| first_careunit=Coronary Care Unit (CCU) | 2.989 | 2.629 - 3.399 |
| first_careunit=Intensive Care Unit (ICU) | 13.753 | 5.555 - 34.046 |
| first_careunit=Medical Intensive Care Unit (MICU) | 4.651 | 4.112 - 5.260 |
| first_careunit=Medical/Surgical Intensive Care Unit (MICU/SICU) | 4.095 | 3.610 - 4.644 |
| first_careunit=Medicine | 4.301 | 1.003 - 18.450 |
| first_careunit=Neuro Intermediate | 0.375 | 0.288 - 0.487 |
| first_careunit=Neuro Stepdown | 0.387 | 0.230 - 0.651 |
| first_careunit=Neuro Surgical Intensive Care Unit (Neuro SICU) | 7.688 | 6.486 - 9.111 |
| first_careunit=PACU | 3.286 | 1.559 - 6.924 |
| first_careunit=Surgery/Trauma | 9.072 | 1.930 - 42.650 |
| first_careunit=Surgery/Vascular/Intermediate | 6.259 | 3.913 - 10.013 |
| first_careunit=Surgical Intensive Care Unit (SICU) | 3.740 | 3.288 - 4.253 |
| first_careunit=Trauma SICU (TSICU) | 3.308 | 2.894 - 3.780 |
| anchor_year_group=2011 - 2013 | 1.100 | 1.020 - 1.187 |
| anchor_year_group=2014 - 2016 | 1.291 | 1.196 - 1.395 |
| anchor_year_group=2017 - 2019 | 1.454 | 1.339 - 1.578 |
| anchor_year_group=2020 - 2022 | 1.929 | 1.769 - 2.104 |
| age_at_admit | 1.028 | 1.026 - 1.029 |

## What this deliberately does not claim

- No causal effect: covariates are admission characteristics, not interventions.
- No prediction performance: the AUC is in-sample and optimistic (holdout
  modelling is P7).
- No calendar-time trends: the era covariate orders 3-year windows, not years.
- Ages >= 89 are indistinguishable (all appear as 91).
- ICU length of stay is post-index and is not a covariate.

## Reproduction

    mwh tracer --tier full

Run id `20260917T213352-full`; every displayed number came through the audited
safe-query wrapper (audit ids in `manifest.json`).

## Provenance

- git `bd64821` - package `0.1.0` -
  DuckDB `1.5.5`.
- Core snapshot `b1fc53134348f3b4ede369ed6ae27424c4988fafb45065c87a09907f7a410eca`; 7 audited safe-query calls.
- Wall time 3.46 s.

## Reproduction (run ledger, EP-35)

Run `20260917T213345Z-4376d1` - kind `analysis`, tier `full`, status `ok`, started 2026-09-17T21:33:45.039+00:00.

```powershell
cd mimicwarehouse
mwh tracer --tier full
```

- Recorded SQL: 8 statement(s) under `runs/<run_id>/sql/`; audited safe-query calls: 7; attrition steps: 5.
- Protocol: none (not run under a frozen protocol; EP-51).
- Seeds: none (no stochastic stage).
- Claim type: associational (exploratory). MIMIC-IV analyses are retrospective.

## Provenance (run ledger, EP-35)

- git `bd6482133c370679723086615e7ddf3c07b5b43b` (dirty) - package `0.1.0` - DuckDB `1.5.5` - Python `3.13.15`.
- Environment hash (`uv.lock` sha256): `aa614a0d27db4514d9a8825c6a6fdd4babebdbc0832251502d854984cbaa2282`.
- Snapshot ids: core `b1fc53134348f3b4ede369ed6ae27424c4988fafb45065c87a09907f7a410eca`.
- Wall time 3.465813 s; peak RSS 310 MB (peak_wset); CPU time 5.047 s; disk delta 1.1 MB; GPU memory peak -.
