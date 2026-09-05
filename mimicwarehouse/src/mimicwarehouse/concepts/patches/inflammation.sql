-- mimicwarehouse concept patch `inflammation-crp-valueuom` (EP-38, 2026-09-05).
-- Full replacement of mimic-iv/concepts_duckdb/measurement/inflammation.sql at MIT-LCP/mimic-code commit
-- 8bcbd190ca75670cd5281f9ead3611ae1cefb73e (MIT License, Copyright (c) 2019 MIT Laboratory for
-- Computational Physiology; attribution in the repository-root NOTICE, D-19).
-- Upstream reference: https://github.com/MIT-LCP/mimic-code/pull/2141
-- Change:
--   CRP (itemid 50889) rows whose valueuom is not mg/L (including a missing unit) are
--   excluded; mg/L is the expected unit.
-- Upstream header, kept verbatim: THIS SCRIPT IS AUTOMATICALLY GENERATED. DO NOT EDIT IT DIRECTLY.
DROP TABLE IF EXISTS mimiciv_derived.inflammation; CREATE TABLE mimiciv_derived.inflammation AS
SELECT
  MAX(subject_id) AS subject_id,
  MAX(hadm_id) AS hadm_id,
  MAX(charttime) AS charttime,
  le.specimen_id,
  MAX(CASE WHEN itemid = 50889 AND valueuom = 'mg/L' THEN valuenum ELSE NULL END) AS crp
FROM mimiciv_hosp.labevents AS le
WHERE
  le.itemid IN (50889)
  AND NOT valuenum IS NULL
  AND (
    itemid <> 50889 OR valueuom = 'mg/L'
  )
  AND valuenum > 0
GROUP BY
  le.specimen_id
