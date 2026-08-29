"""Per-tier catalogs — one ``warehouse/<tier>.duckdb`` over the Parquet lake (EP-21).

DESIGN §3/§6, D-17/D-18: ``mimiciv_hosp`` / ``mimiciv_icu`` views over the lake plus
small materialized tables, so concept SQL, cohorts, the app and ``safe_query`` address
the same names in every tier. Modules: :mod:`.build` (the ``catalog`` DAG step —
``mwh build`` is the only writer; built to ``<tier>.duckdb.new`` and published by the
rename-aside two-step), :mod:`.connect` (the READ_ONLY opener every reader uses),
:mod:`.cli` (``mwh catalog info`` and the interim metadata-only ``mwh sql``).

No eager submodule imports here: ``cli.py`` imports :mod:`.cli` at start-up and the
build/connect chain reaches duckdb and the schema contract, which the ``mwh --help``
import budget excludes (test_ep09) — import :mod:`mimicwarehouse.catalog.build` /
:mod:`.connect` directly where needed.

Everything logged, printed or returned is counts, schemas, hashes, paths and timings —
never a row (GOVERNANCE §4); free-form SQL arrives only with ``safe_query`` (EP-30).
"""
