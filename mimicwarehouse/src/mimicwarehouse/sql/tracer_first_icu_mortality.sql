-- Tracer-bullet cohort (EP-31, D-5): first ICU stay of adult patients -> in-hospital
-- mortality. A CTE chain, one step per criterion; mimicwarehouse.tracer appends the
-- final SELECT (attrition counts, descriptives, the model's cohort read), so this file
-- holds the WITH clause only and is not executable on its own.
--
-- MIMIC caveats baked in (brief EP-31), cited from mimicwarehouse.timesem since EP-34:
-- the age rule is timesem.sql_age_at("anchor_age", "anchor_year", "admittime") in the
-- `adult` step and the same with cap=True (least(..., timesem.AGE_CAP = 91), because ages
-- >= 89 are shipped as 91) in `cohort` -- both fragments are embedded verbatim and pinned
-- by test_ep34; the first-stay window is timesem's `first_icu_stay` index rule (first
-- ICU stay of the subject, ordered intime, stay_id); anchor_year_group (timesem.ERAS) is
-- an era covariate, never a calendar axis; hospital_expire_flag is the outcome (discharge
-- alive is the competing state, timesem.IN_HOSPITAL_MORTALITY; dod is not needed); ICU
-- length of stay is post-index and deliberately not carried. Identifier columns appear
-- only inside the chain (joins, the first-stay window) and never in any final select
-- list (GOVERNANCE section 4).
WITH base AS (
    SELECT
        i.subject_id,
        i.stay_id,
        i.intime,
        i.first_careunit,
        a.admittime,
        a.dischtime,
        a.admission_type,
        a.hospital_expire_flag,
        p.gender,
        p.anchor_age,
        p.anchor_year,
        p.anchor_year_group
    FROM mimiciv_icu.icustays AS i
    JOIN mimiciv_hosp.admissions AS a ON i.hadm_id = a.hadm_id
    JOIN mimiciv_hosp.patients AS p ON i.subject_id = p.subject_id
),
first_stay AS (
    SELECT * EXCLUDE (stay_rank)
    FROM (
        SELECT
            base.*,
            row_number() OVER (PARTITION BY subject_id ORDER BY intime, stay_id)
                AS stay_rank
        FROM base
    )
    WHERE stay_rank = 1
),
adult AS (
    SELECT *
    FROM first_stay
    WHERE anchor_age + (year(admittime) - anchor_year) >= 18
),
complete AS (
    SELECT *
    FROM adult
    WHERE dischtime IS NOT NULL AND hospital_expire_flag IS NOT NULL
),
cohort AS (
    SELECT
        least(anchor_age + (year(admittime) - anchor_year), 91) AS age_at_admit,
        gender,
        admission_type,
        first_careunit,
        anchor_year_group,
        hospital_expire_flag
    FROM complete
)
