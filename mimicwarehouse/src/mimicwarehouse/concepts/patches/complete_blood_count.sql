-- mimicwarehouse concept patch: complete_blood_count-mchc-unit-pr2141 (EP-38; D-19, DESIGN section 8).
-- Full replacement for the vendored mimic-code file
--   mimic-iv/concepts_duckdb/measurement/complete_blood_count.sql
-- at upstream commit 8bcbd190ca75670cd5281f9ead3611ae1cefb73e (the vendored file is never edited).
-- Upstream: MIT-LCP/mimic-code -- MIT License, Copyright (c) 2019 MIT Laboratory for
--   Computational Physiology (vendor/mimic-code/LICENSE; repository NOTICE). The body below is
--   upstream's sqlglot-transpiled DuckDB SQL with the change described here applied by hand.
-- Ported from: https://github.com/MIT-LCP/mimic-code/pull/2141
--   "fix(mimic-iv): filter lab concepts by expected valueuom" (open, unmerged as of 2026-09-06;
--   fixes https://github.com/MIT-LCP/mimic-code/issues/1922).
-- Change: MCHC (itemid 51249) is kept only when valueuom = 'g/dL' -- both in the pivoted
--   column and in the WHERE clause -- because upstream treats the rows recorded with '%' as
--   mis-labelled units. The other nine CBC items are unchanged.
-- Registry (this file's sha256): src/mimicwarehouse/concepts/patches/patches.yaml.
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
  -- MCHC (51249) is sometimes recorded with valueuom '%' in error; keep g/dL only (PR 2141)
  AND (itemid <> 51249 OR valueuom = 'g/dL')
  AND valuenum > 0
GROUP BY
  le.specimen_id
