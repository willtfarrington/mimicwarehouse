WITH base AS (
    SELECT subject_id, hadm_id, stay_id
    FROM mimiciv_icu.icustays
),
idx AS (
    SELECT
        x.subject_id, x.hadm_id, x.stay_id,
        x.index_time,
        x.end_time,
        a.admittime,
        a.dischtime,
        a.hospital_expire_flag,
        p.gender AS gender,
        a.admission_type AS admission_type,
        a.admission_location AS admission_location,
        a.discharge_location AS discharge_location,
        a.insurance AS insurance,
        a.language AS language,
        a.marital_status AS marital_status,
        i.first_careunit AS first_careunit,
        i.intime AS stay_intime,
        i.outtime AS stay_outtime,
        p.anchor_age,
        p.anchor_year,
        p.anchor_year_group,
        p.dod,
        ld.last_dischtime
    FROM (
        SELECT subject_id, hadm_id, stay_id, intime AS index_time, outtime AS end_time
        FROM (
            SELECT subject_id, hadm_id, stay_id, intime, outtime,
                   row_number() OVER (PARTITION BY subject_id ORDER BY intime, stay_id) AS seq
            FROM mimiciv_icu.icustays
        )
        WHERE seq = 1
    ) AS x
    LEFT JOIN mimiciv_hosp.admissions AS a ON a.hadm_id = x.hadm_id
    JOIN mimiciv_hosp.patients AS p ON p.subject_id = x.subject_id
    JOIN mimiciv_icu.icustays AS i ON i.stay_id = x.stay_id
    LEFT JOIN (
        SELECT subject_id, max(dischtime) AS last_dischtime
        FROM mimiciv_hosp.admissions
        GROUP BY subject_id
    ) AS ld ON ld.subject_id = x.subject_id
),
crit_01_adult AS (
    SELECT c.*
    FROM idx AS c
    WHERE coalesce(c.anchor_age + (year(c.index_time) - c.anchor_year) >= 18.0, false)
),
crit_02_short_icu_stay AS (
    SELECT c.*
    FROM crit_01_adult AS c
    WHERE NOT coalesce(date_diff('second', c.stay_intime, c.stay_outtime) / 3600.0 < 4.0, false)
),
cohort AS (
    SELECT
        c.subject_id,
        c.hadm_id,
        c.stay_id,
        c.index_time,
        CASE c.anchor_year_group WHEN '2008 - 2010' THEN 0 WHEN '2011 - 2013' THEN 1 WHEN '2014 - 2016' THEN 2 WHEN '2017 - 2019' THEN 3 WHEN '2020 - 2022' THEN 4 END AS era_index,
        least(c.anchor_age + (year(c.index_time) - c.anchor_year), 91) AS age_at_index,
        (c.anchor_age + (year(c.index_time) - c.anchor_year)) >= 91 AS age_capped,
        c.index_time + to_seconds(CAST(-86400 AS BIGINT)) AS obs_start,
        c.index_time + to_seconds(CAST(0 AS BIGINT)) AS obs_end,
        c.dischtime AS follow_up_end,
        CASE WHEN c.hospital_expire_flag = 1 THEN 'death' WHEN c.dischtime IS NOT NULL THEN 'discharge_alive' ELSE 'unknown' END AS censor_reason,
        false AS custom_flag
    FROM crit_02_short_icu_stay AS c
    ORDER BY c.subject_id, c.hadm_id, c.stay_id
)
SELECT *
FROM cohort
