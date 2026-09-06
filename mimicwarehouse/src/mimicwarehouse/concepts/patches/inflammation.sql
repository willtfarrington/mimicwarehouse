-- mimicwarehouse concept patch: inflammation-crp-unit-pr2141 (EP-38; D-19, DESIGN section 8).
-- Full replacement for the vendored mimic-code file
--   mimic-iv/concepts_duckdb/measurement/inflammation.sql
-- at upstream commit 8bcbd190ca75670cd5281f9ead3611ae1cefb73e (the vendored file is never edited).
-- Upstream: MIT-LCP/mimic-code -- MIT License, Copyright (c) 2019 MIT Laboratory for
--   Computational Physiology (vendor/mimic-code/LICENSE; repository NOTICE). The body below is
--   upstream's sqlglot-transpiled DuckDB SQL with the change described here applied by hand.
-- Ported from: https://github.com/MIT-LCP/mimic-code/pull/2141
--   "fix(mimic-iv): filter lab concepts by expected valueuom" (open, unmerged as of 2026-09-06;
--   fixes https://github.com/MIT-LCP/mimic-code/issues/1922).
-- Change: CRP (itemid 50889) is kept only when valueuom = 'mg/L' -- rows with a missing or
--   different unit are excluded, both in the pivoted column and in the WHERE clause.
-- Registry (this file's sha256): src/mimicwarehouse/concepts/patches/patches.yaml.
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
  -- CRP (50889) rows with a missing valueuom are excluded; mg/L is the expected unit (PR 2141)
  AND (itemid <> 50889 OR valueuom = 'mg/L')
  AND valuenum > 0
GROUP BY
  le.specimen_id
