| table | check_id | rule | violation_share | n_affected | status |
|---|---|---|---|---|---|
| mimiciv_hosp.admissions | ts_order | admittime <= deathtime | 0.002 | 26 | warn |
| mimiciv_hosp.admissions | ts_order | admittime <= dischtime | 0.000 | 175 | warn |
| mimiciv_hosp.admissions | ts_order | edregtime <= edouttime | - | <11 | warn |
| mimiciv_hosp.emar | ts_store_lag | charttime <= storetime | 0.034 | 1,457,991 | pass |
| mimiciv_hosp.labevents | ts_store_lag | charttime <= storetime | 0.000 | 1,372 | pass |
| mimiciv_hosp.microbiologyevents | ts_store_lag | charttime <= storetime | 0.000 | 0 | pass |
| mimiciv_hosp.pharmacy | ts_order | starttime <= stoptime | 0.040 | 717,515 | warn |
| mimiciv_hosp.prescriptions | ts_order | starttime <= stoptime | 0.040 | 816,994 | warn |
| mimiciv_hosp.transfers | ts_order | intime <= outtime | - | <11 | warn |
| mimiciv_icu.chartevents | event_window | charttime in [intime - 24 h, outtime + 24 h] | 0.001 | 466,421 | pass |
| mimiciv_icu.chartevents | ts_store_lag | charttime <= storetime | 0.084 | 36,530,969 | pass |
| mimiciv_icu.datetimeevents | event_window | charttime in [intime - 24 h, outtime + 24 h] | 0.000 | 4,860 | pass |
| mimiciv_icu.datetimeevents | ts_store_lag | charttime <= storetime | 0.046 | 459,636 | pass |
| mimiciv_icu.icustays | ts_order | intime <= outtime | 0.000 | 0 | pass |
| mimiciv_icu.ingredientevents | event_window | starttime in [intime - 24 h, outtime + 24 h] | 0.000 | 6,352 | pass |
| mimiciv_icu.ingredientevents | ts_order | starttime <= endtime | 0.000 | 15 | warn |
| mimiciv_icu.inputevents | event_window | starttime in [intime - 24 h, outtime + 24 h] | 0.000 | 4,723 | pass |
| mimiciv_icu.inputevents | ts_order | starttime <= endtime | - | <11 | warn |
| mimiciv_icu.outputevents | event_window | charttime in [intime - 24 h, outtime + 24 h] | 0.001 | 2,960 | pass |
| mimiciv_icu.outputevents | ts_store_lag | charttime <= storetime | 0.141 | 757,757 | warn |
| mimiciv_icu.procedureevents | event_window | starttime in [intime - 24 h, outtime + 24 h] | 0.002 | 1,472 | pass |
| mimiciv_icu.procedureevents | ts_order | starttime <= endtime | 0.000 | 0 | pass |
