-- phenotype kdigo_aki@1.0.0 (grain icustay); compiled by mimicwarehouse.phenotypes.compiler (EP-41)
-- criteria: aki; onset: earliest
-- references: (none)
-- parameters: min_stage=1, window_hours=168
-- concepts: mimiciv_derived.kdigo_stages=c441af84a639
WITH
units AS (
  SELECT subject_id, hadm_id, stay_id FROM mimiciv_icu.icustays
),
leaf_01_aki AS (
  SELECT s.subject_id, s.hadm_id, c.stay_id, c.charttime AS event_time, c.aki_stage_smoothed AS ev_max_stage_in_window, c.aki_stage_smoothed AS ev_stage_at_onset
  FROM mimiciv_derived.kdigo_stages AS c
  JOIN mimiciv_icu.icustays AS s ON s.stay_id = c.stay_id
  WHERE c.aki_stage_smoothed >= 1
    AND date_diff('second', s.intime, c.charttime) / 3600.0 >= 0.0
    AND date_diff('second', s.intime, c.charttime) / 3600.0 < 168.0
),
mapped_01_aki AS (
  SELECT subject_id, hadm_id, stay_id, event_time, ev_max_stage_in_window, ev_stage_at_onset
  FROM leaf_01_aki
),
unit_01_aki AS (
  SELECT stay_id, min(event_time) AS first_time, max(event_time) AS last_time, count(*) AS n_events, max(ev_max_stage_in_window) AS ev_max_stage_in_window, arg_min(ev_stage_at_onset, event_time) AS ev_stage_at_onset
  FROM mapped_01_aki
  WHERE stay_id IS NOT NULL
  GROUP BY stay_id
  HAVING count(*) >= 1
),
reduced AS (
  SELECT
    u.subject_id,
    u.hadm_id,
    u.stay_id,
    (l01.stay_id IS NOT NULL) AS has_aki,
    l01.first_time AS t_aki,
    coalesce(l01.n_events, 0) AS n_aki,
    l01.ev_max_stage_in_window AS ev_max_stage_in_window,
    l01.ev_stage_at_onset AS ev_stage_at_onset
  FROM units AS u
  LEFT JOIN unit_01_aki AS l01 ON l01.stay_id = u.stay_id
)
SELECT
  subject_id,
  hadm_id,
  stay_id,
  has_aki AS flag,
  CASE WHEN has_aki THEN t_aki END AS onset_time,
  '{"aki":' || CAST(n_aki AS VARCHAR) || '}' AS evidence_json,
  coalesce(ev_max_stage_in_window, 0) AS max_stage_in_window,
  ev_stage_at_onset AS stage_at_onset
FROM reduced
ORDER BY subject_id, hadm_id, stay_id
