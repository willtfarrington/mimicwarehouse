-- phenotype t2dm@1.0.0 (grain subject); compiled by mimicwarehouse.phenotypes.compiler (EP-41)
-- criteria: any(dx, all(any(med, a1c), not(t1dm))); onset: earliest
-- references: noninsulin_antidiabetics@1.0.0=bba2843889fe, t1dm@1.0.0=024460ebe6e4, t2dm@1.0.0=780eafccc3a8
WITH
units AS (
  SELECT subject_id FROM mimiciv_hosp.patients
),
leaf_01_dx AS (
  SELECT d.subject_id, d.hadm_id, CAST(NULL AS INTEGER) AS stay_id, a.dischtime AS event_time
  FROM mimiciv_hosp.diagnoses_icd AS d
  JOIN mimiciv_hosp.admissions AS a ON a.hadm_id = d.hadm_id
  WHERE ((d.icd_version = 9 AND (d.icd_code IN ('25000', '25002', '25010', '25012', '25020', '25022', '25030', '25032', '25040', '25042', '25050', '25052', '25060', '25062', '25070', '25072', '25080', '25082', '25090', '25092'))) OR (d.icd_version = 10 AND (d.icd_code LIKE 'E11%')))
),
mapped_01_dx AS (
  SELECT subject_id, hadm_id, event_time
  FROM leaf_01_dx
),
leaf_02_med AS (
  SELECT p.subject_id, p.hadm_id, CAST(NULL AS INTEGER) AS stay_id, p.starttime AS event_time
  FROM mimiciv_hosp.prescriptions AS p
  WHERE (contains(upper(p.drug), 'ACARBOSE') OR contains(upper(p.drug), 'ACTOS') OR contains(upper(p.drug), 'ALOGLIPTIN') OR contains(upper(p.drug), 'AMARYL') OR contains(upper(p.drug), 'AVANDIA') OR contains(upper(p.drug), 'BYDUREON') OR contains(upper(p.drug), 'BYETTA') OR contains(upper(p.drug), 'CANAGLIFLOZIN') OR contains(upper(p.drug), 'CHLORPROPAMIDE') OR contains(upper(p.drug), 'DAPAGLIFLOZIN') OR contains(upper(p.drug), 'DIABETA') OR contains(upper(p.drug), 'DULAGLUTIDE') OR contains(upper(p.drug), 'EMPAGLIFLOZIN') OR contains(upper(p.drug), 'ERTUGLIFLOZIN') OR contains(upper(p.drug), 'EXENATIDE') OR contains(upper(p.drug), 'FARXIGA') OR contains(upper(p.drug), 'FORTAMET') OR contains(upper(p.drug), 'GLIBENCLAMIDE') OR contains(upper(p.drug), 'GLICLAZIDE') OR contains(upper(p.drug), 'GLIMEPIRIDE') OR contains(upper(p.drug), 'GLIPIZIDE') OR contains(upper(p.drug), 'GLUCOPHAGE') OR contains(upper(p.drug), 'GLUCOTROL') OR contains(upper(p.drug), 'GLUCOVANCE') OR contains(upper(p.drug), 'GLUMETZA') OR contains(upper(p.drug), 'GLYBURIDE') OR contains(upper(p.drug), 'GLYNASE') OR contains(upper(p.drug), 'INVOKANA') OR contains(upper(p.drug), 'JANUMET') OR contains(upper(p.drug), 'JANUVIA') OR contains(upper(p.drug), 'JARDIANCE') OR contains(upper(p.drug), 'LINAGLIPTIN') OR contains(upper(p.drug), 'LIRAGLUTIDE') OR contains(upper(p.drug), 'LIXISENATIDE') OR contains(upper(p.drug), 'METFORMIN') OR contains(upper(p.drug), 'MICRONASE') OR contains(upper(p.drug), 'MIGLITOL') OR contains(upper(p.drug), 'MOUNJARO') OR contains(upper(p.drug), 'NATEGLINIDE') OR contains(upper(p.drug), 'NESINA') OR contains(upper(p.drug), 'ONGLYZA') OR contains(upper(p.drug), 'OZEMPIC') OR contains(upper(p.drug), 'PIOGLITAZONE') OR contains(upper(p.drug), 'PRAMLINTIDE') OR contains(upper(p.drug), 'PRANDIN') OR contains(upper(p.drug), 'PRECOSE') OR contains(upper(p.drug), 'REPAGLINIDE') OR contains(upper(p.drug), 'RIOMET') OR contains(upper(p.drug), 'ROSIGLITAZONE') OR contains(upper(p.drug), 'RYBELSUS') OR contains(upper(p.drug), 'SAXAGLIPTIN') OR contains(upper(p.drug), 'SEMAGLUTIDE') OR contains(upper(p.drug), 'SITAGLIPTIN') OR contains(upper(p.drug), 'STARLIX') OR contains(upper(p.drug), 'STEGLATRO') OR contains(upper(p.drug), 'SYMLIN') OR contains(upper(p.drug), 'TIRZEPATIDE') OR contains(upper(p.drug), 'TOLAZAMIDE') OR contains(upper(p.drug), 'TOLBUTAMIDE') OR contains(upper(p.drug), 'TRADJENTA') OR contains(upper(p.drug), 'TRULICITY') OR contains(upper(p.drug), 'VICTOZA'))
),
mapped_02_med AS (
  SELECT subject_id, hadm_id, event_time
  FROM leaf_02_med
),
leaf_03_a1c AS (
  SELECT l.subject_id, l.hadm_id, CAST(NULL AS INTEGER) AS stay_id, l.charttime AS event_time
  FROM (SELECT subject_id, hadm_id, charttime, itemid, valuenum, regexp_replace(regexp_replace(lower(replace(replace(replace(replace(regexp_replace(coalesce(CAST(valueuom AS VARCHAR), ''), '\s+', '', 'g'), '°', ''), 'º', ''), 'µ', 'u'), 'μ', 'u')), '^deg(?:rees?)?\.?', ''), '\.$', '') AS unit_norm
        FROM mimiciv_hosp.labevents
        WHERE itemid IN (50852) AND valuenum IS NOT NULL) AS l
  WHERE (CASE WHEN l.itemid = 50852 THEN (CASE l.unit_norm WHEN 'mmol/mol' THEN (l.valuenum) * 0.09148 + (2.152) ELSE l.valuenum END) ELSE l.valuenum END) >= 6.5
),
mapped_03_a1c AS (
  SELECT subject_id, hadm_id, event_time
  FROM leaf_03_a1c
),
leaf_04_t1dm AS (
  SELECT d.subject_id, d.hadm_id, CAST(NULL AS INTEGER) AS stay_id, a.dischtime AS event_time
  FROM mimiciv_hosp.diagnoses_icd AS d
  JOIN mimiciv_hosp.admissions AS a ON a.hadm_id = d.hadm_id
  WHERE ((d.icd_version = 9 AND (d.icd_code IN ('25001', '25003', '25011', '25013', '25021', '25023', '25031', '25033', '25041', '25043', '25051', '25053', '25061', '25063', '25071', '25073', '25081', '25083', '25091', '25093'))) OR (d.icd_version = 10 AND (d.icd_code LIKE 'E10%')))
),
mapped_04_t1dm AS (
  SELECT subject_id, hadm_id, event_time
  FROM leaf_04_t1dm
),
unit_01_dx AS (
  SELECT subject_id, min(event_time) AS first_time, max(event_time) AS last_time, count(*) AS n_events
  FROM mapped_01_dx
  WHERE subject_id IS NOT NULL
  GROUP BY subject_id
  HAVING count(*) >= 1
),
unit_02_med AS (
  SELECT subject_id, min(event_time) AS first_time, max(event_time) AS last_time, count(*) AS n_events
  FROM mapped_02_med
  WHERE subject_id IS NOT NULL
  GROUP BY subject_id
  HAVING count(*) >= 1
),
unit_03_a1c AS (
  SELECT subject_id, min(event_time) AS first_time, max(event_time) AS last_time, count(*) AS n_events
  FROM mapped_03_a1c
  WHERE subject_id IS NOT NULL
  GROUP BY subject_id
  HAVING count(*) >= 1
),
unit_04_t1dm AS (
  SELECT subject_id, min(event_time) AS first_time, max(event_time) AS last_time, count(*) AS n_events
  FROM mapped_04_t1dm
  WHERE subject_id IS NOT NULL
  GROUP BY subject_id
  HAVING count(*) >= 1
),
reduced AS (
  SELECT
    u.subject_id,
    (l01.subject_id IS NOT NULL) AS has_dx,
    l01.first_time AS t_dx,
    coalesce(l01.n_events, 0) AS n_dx,
    (l02.subject_id IS NOT NULL) AS has_med,
    l02.first_time AS t_med,
    coalesce(l02.n_events, 0) AS n_med,
    (l03.subject_id IS NOT NULL) AS has_a1c,
    l03.first_time AS t_a1c,
    coalesce(l03.n_events, 0) AS n_a1c,
    (l04.subject_id IS NOT NULL) AS has_t1dm,
    l04.first_time AS t_t1dm,
    coalesce(l04.n_events, 0) AS n_t1dm
  FROM units AS u
  LEFT JOIN unit_01_dx AS l01 ON l01.subject_id = u.subject_id
  LEFT JOIN unit_02_med AS l02 ON l02.subject_id = u.subject_id
  LEFT JOIN unit_03_a1c AS l03 ON l03.subject_id = u.subject_id
  LEFT JOIN unit_04_t1dm AS l04 ON l04.subject_id = u.subject_id
)
SELECT
  subject_id,
  (has_dx OR ((has_med OR has_a1c) AND (NOT has_t1dm))) AS flag,
  CASE WHEN (has_dx OR ((has_med OR has_a1c) AND (NOT has_t1dm))) THEN least(t_dx, t_med, t_a1c) END AS onset_time,
  '{"a1c":' || CAST(n_a1c AS VARCHAR) || ',"dx":' || CAST(n_dx AS VARCHAR) || ',"med":' || CAST(n_med AS VARCHAR) || ',"t1dm":' || CAST(n_t1dm AS VARCHAR) || '}' AS evidence_json
FROM reduced
ORDER BY subject_id
