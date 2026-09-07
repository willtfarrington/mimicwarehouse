-- phenotype sepsis_explicit@1.0.0 (grain hadm); compiled by mimicwarehouse.phenotypes.compiler (EP-41)
-- criteria: dx; onset: earliest
-- references: sepsis_explicit@1.0.0=5d172007c621
WITH
units AS (
  SELECT subject_id, hadm_id FROM mimiciv_hosp.admissions
),
leaf_01_dx AS (
  SELECT d.subject_id, d.hadm_id, CAST(NULL AS INTEGER) AS stay_id, a.dischtime AS event_time
  FROM mimiciv_hosp.diagnoses_icd AS d
  JOIN mimiciv_hosp.admissions AS a ON a.hadm_id = d.hadm_id
  WHERE ((d.icd_version = 9 AND (d.icd_code IN ('0202', '0223', '0362', '0545', '449', '78552', '99591', '99592') OR d.icd_code LIKE '038%')) OR (d.icd_version = 10 AND (d.icd_code IN ('A021', 'A227', 'A267', 'A327', 'A427', 'A5486', 'B377', 'O85') OR d.icd_code LIKE 'A40%' OR d.icd_code LIKE 'A41%' OR d.icd_code LIKE 'R652%' OR d.icd_code LIKE 'T8112%' OR d.icd_code LIKE 'T8144%')))
),
mapped_01_dx AS (
  SELECT subject_id, hadm_id, event_time
  FROM leaf_01_dx
),
unit_01_dx AS (
  SELECT hadm_id, min(event_time) AS first_time, max(event_time) AS last_time, count(*) AS n_events
  FROM mapped_01_dx
  WHERE hadm_id IS NOT NULL
  GROUP BY hadm_id
  HAVING count(*) >= 1
),
reduced AS (
  SELECT
    u.subject_id,
    u.hadm_id,
    (l01.hadm_id IS NOT NULL) AS has_dx,
    l01.first_time AS t_dx,
    coalesce(l01.n_events, 0) AS n_dx
  FROM units AS u
  LEFT JOIN unit_01_dx AS l01 ON l01.hadm_id = u.hadm_id
)
SELECT
  subject_id,
  hadm_id,
  has_dx AS flag,
  CASE WHEN has_dx THEN t_dx END AS onset_time,
  '{"dx":' || CAST(n_dx AS VARCHAR) || '}' AS evidence_json
FROM reduced
ORDER BY subject_id, hadm_id
