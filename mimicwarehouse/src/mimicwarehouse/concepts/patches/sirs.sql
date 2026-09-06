-- mimicwarehouse concept patch: sirs-wbc-guard-pr2146 (EP-38; D-19, DESIGN section 8).
-- Full replacement for the vendored mimic-code file
--   mimic-iv/concepts_duckdb/score/sirs.sql
-- at upstream commit 8bcbd190ca75670cd5281f9ead3611ae1cefb73e (the vendored file is never edited).
-- Upstream: MIT-LCP/mimic-code -- MIT License, Copyright (c) 2019 MIT Laboratory for
--   Computational Physiology (vendor/mimic-code/LICENSE; repository NOTICE). The body below is
--   upstream's sqlglot-transpiled DuckDB SQL with the change described here applied by hand.
-- Ported from: https://github.com/MIT-LCP/mimic-code/pull/2146
--   "fix: include wbc_max in SIRS WBC missing-data guard" (open, unmerged as of 2026-09-06).
-- Change: the WBC missing-data arm tests COALESCE(wbc_min, wbc_max, bands_max) IS NULL instead
--   of COALESCE(wbc_min, bands_max) IS NULL, so a stay whose only WBC input is a normal wbc_max
--   scores 0 rather than falling through to NULL.
-- Registry (this file's sha256): src/mimicwarehouse/concepts/patches/patches.yaml.
DROP TABLE IF EXISTS mimiciv_derived.sirs; CREATE TABLE mimiciv_derived.sirs AS
WITH scorecomp AS (
  SELECT
    ie.stay_id,
    v.temperature_min,
    v.temperature_max,
    v.heart_rate_max,
    v.resp_rate_max,
    bg.pco2_min AS paco2_min,
    l.wbc_min,
    l.wbc_max,
    l.bands_max
  FROM mimiciv_icu.icustays AS ie
  LEFT JOIN mimiciv_derived.first_day_bg_art AS bg
    ON ie.stay_id = bg.stay_id
  LEFT JOIN mimiciv_derived.first_day_vitalsign AS v
    ON ie.stay_id = v.stay_id
  LEFT JOIN mimiciv_derived.first_day_lab AS l
    ON ie.stay_id = l.stay_id
), scorecalc AS (
  SELECT
    stay_id,
    CASE
      WHEN temperature_min < 36.0
      THEN 1
      WHEN temperature_max > 38.0
      THEN 1
      WHEN temperature_min IS NULL
      THEN NULL
      ELSE 0
    END AS temp_score,
    CASE
      WHEN heart_rate_max > 90.0
      THEN 1
      WHEN heart_rate_max IS NULL
      THEN NULL
      ELSE 0
    END AS heart_rate_score,
    CASE
      WHEN resp_rate_max > 20.0
      THEN 1
      WHEN paco2_min < 32.0
      THEN 1
      WHEN COALESCE(resp_rate_max, paco2_min) IS NULL
      THEN NULL
      ELSE 0
    END AS resp_score,
    CASE
      WHEN wbc_min < 4.0
      THEN 1
      WHEN wbc_max > 12.0
      THEN 1
      WHEN bands_max > 10
      THEN 1
      WHEN COALESCE(wbc_min, wbc_max, bands_max) IS NULL
      THEN NULL
      ELSE 0
    END AS wbc_score
  FROM scorecomp
)
SELECT
  ie.subject_id,
  ie.hadm_id,
  ie.stay_id,
  COALESCE(temp_score, 0) + COALESCE(heart_rate_score, 0) + COALESCE(resp_score, 0) + COALESCE(wbc_score, 0) AS sirs,
  temp_score,
  heart_rate_score,
  resp_score,
  wbc_score
FROM mimiciv_icu.icustays AS ie
LEFT JOIN scorecalc AS s
  ON ie.stay_id = s.stay_id
