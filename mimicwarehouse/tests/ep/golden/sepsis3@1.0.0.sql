-- phenotype sepsis3@1.0.0 (grain icustay); compiled by mimicwarehouse.phenotypes.compiler (EP-41)
-- criteria: s3; onset: earliest
-- references: (none)
-- concepts: mimiciv_derived.sepsis3=3ba6b0f7008f
WITH
units AS (
  SELECT subject_id, hadm_id, stay_id FROM mimiciv_icu.icustays
),
leaf_01_s3 AS (
  SELECT s.subject_id, s.hadm_id, c.stay_id, c.suspected_infection_time AS event_time, c.sofa_time AS ev_sofa_time, c.sofa_score AS ev_sofa_score
  FROM mimiciv_derived.sepsis3 AS c
  JOIN mimiciv_icu.icustays AS s ON s.stay_id = c.stay_id
  WHERE c.sepsis3 = TRUE
),
mapped_01_s3 AS (
  SELECT subject_id, hadm_id, stay_id, event_time, ev_sofa_time, ev_sofa_score
  FROM leaf_01_s3
),
unit_01_s3 AS (
  SELECT stay_id, min(event_time) AS first_time, max(event_time) AS last_time, count(*) AS n_events, arg_min(ev_sofa_time, event_time) AS ev_sofa_time, arg_min(ev_sofa_score, event_time) AS ev_sofa_score
  FROM mapped_01_s3
  WHERE stay_id IS NOT NULL
  GROUP BY stay_id
  HAVING count(*) >= 1
),
reduced AS (
  SELECT
    u.subject_id,
    u.hadm_id,
    u.stay_id,
    (l01.stay_id IS NOT NULL) AS has_s3,
    l01.first_time AS t_s3,
    coalesce(l01.n_events, 0) AS n_s3,
    l01.ev_sofa_time AS ev_sofa_time,
    l01.ev_sofa_score AS ev_sofa_score
  FROM units AS u
  LEFT JOIN unit_01_s3 AS l01 ON l01.stay_id = u.stay_id
)
SELECT
  subject_id,
  hadm_id,
  stay_id,
  has_s3 AS flag,
  CASE WHEN has_s3 THEN t_s3 END AS onset_time,
  '{"s3":' || CAST(n_s3 AS VARCHAR) || '}' AS evidence_json,
  ev_sofa_time AS sofa_time,
  ev_sofa_score AS sofa_score
FROM reduced
ORDER BY subject_id, hadm_id, stay_id
