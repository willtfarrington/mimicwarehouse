"""The latency marts (EP-55/56; DESIGN §3, D-28): per-tier Parquet tables under
``<lake_root>/marts/<tier>/<name>/`` exposed as ``marts.<name>`` in every tier catalog, so
the Lab app answers typical questions in seconds on the full tier.

- :mod:`~mimicwarehouse.marts.registry` -- the YAML mart specs (``marts/specs/*.yaml``,
  pydantic), the SQL generated from them, the generic writer (one sorted file per
  ``subject_bucket``, swap-published, manifest + status + benchmark + snapshot id) and the
  catalog extension (``marts.<name>`` views, ``meta.mart_tables`` / ``meta.mart_columns``).
- :mod:`~mimicwarehouse.marts.first_day` -- ``marts.icustay_first_day``, one row per ICU
  stay (EP-55).
- :mod:`~mimicwarehouse.marts.itemid_rollups` -- the itemid rollups (EP-175, split from
  EP-55 at its pickup): ``marts.itemid_daily`` (stay x curated itemid x ICU day) and
  ``marts.itemid_summary`` (one row per curated itemid, k-suppressed at build time, a
  ``safe.REGISTRY_TABLES`` member).
- :mod:`~mimicwarehouse.marts.hourly` -- the hourly marts (EP-56): ``marts.icustay_hourly``
  (stay x hour bin, 21 variables from five mimic-code concepts), ``marts.hourly_variables``
  (its dictionary) and ``marts.hourly_population`` (hour x variable x era quantiles,
  k-suppressed at build time); the last two are ``safe.REGISTRY_TABLES`` members.

The page-query benchmark (``marts/bench.py``, ``mwh bench queries``) is EP-176's, split from
EP-56 at its pickup. Import budget: nothing heavy at package import (the CLI and the catalog
builder import these modules lazily or for the extension only).
"""
