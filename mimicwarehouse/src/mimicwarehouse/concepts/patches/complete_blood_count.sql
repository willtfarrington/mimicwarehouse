-- mimicwarehouse concept patch `cbc-mchc-valueuom` (EP-38, 2026-09-05).
-- Full replacement of mimic-iv/concepts_duckdb/measurement/complete_blood_count.sql at MIT-LCP/mimic-code commit
-- 8bcbd190ca75670cd5281f9ead3611ae1cefb73e (MIT License, Copyright (c) 2019 MIT Laboratory for
-- Computational Physiology; attribution in the repository-root NOTICE, D-19).
-- Upstream reference: https://github.com/MIT-LCP/mimic-code/pull/2141
-- Change:
--   MCHC (itemid 51249) is sometimes recorded with valueuom '%' in error; only g/dL
--   rows feed the mchc column and the specimen filter.
-- Upstream header, kept verbatim: THIS SCRIPT IS AUTOMATICALLY GENERATED. DO NOT EDIT IT DIRECTLY.
DROP TABLE IF EXISTS mimiciv_derived.complete_blood_count; CREATE TABLE mimiciv_derived.complete_blood_count AS
SELECT
  MAX(subject_id) AS subject_id,
  MAX(hadm_id) AS hadm_id,
  MAX(charttime) AS charttime,
  le.specimen_id,
  MAX(CASE WHEN itemid = 51221 THEN valuenum ELSE NULL END) AS hematocrit,
  MAX(CASE WHEN itemid = 51222 THEN valuenum ELSE NULL END) AS hemoglobin,
  MAX(CASE WHEN itemid = 51248 THEN valuenum ELSE NULL END) AS mch,
  MAX(CASE WHEN itemid = 51249 AND valueuom = 'g/dL' THEN valuenum ELSE NULL END) AS mchc,
  MAX(CASE WHEN itemid = 51250 THEN valuenum ELSE NULL END) AS mcv,
  MAX(CASE WHEN itemid = 51265 THEN valuenum ELSE NULL END) AS platelet,
  MAX(CASE WHEN itemid = 51279 THEN valuenum ELSE NULL END) AS rbc,
  MAX(CASE WHEN itemid = 51277 THEN valuenum ELSE NULL END) AS rdw,
  MAX(CASE WHEN itemid = 52159 THEN valuenum ELSE NULL END) AS rdwsd,
  MAX(CASE WHEN itemid = 51301 THEN valuenum ELSE NULL END) AS wbc
FROM mimiciv_hosp.labevents AS le
WHERE
  le.itemid IN (51221, 51222, 51248, 51249, 51250, 51265, 51279, 51277, 52159, 51301)
  AND NOT valuenum IS NULL
  AND (
    itemid <> 51249 OR valueuom = 'g/dL'
  )
  AND valuenum > 0
GROUP BY
  le.specimen_id
