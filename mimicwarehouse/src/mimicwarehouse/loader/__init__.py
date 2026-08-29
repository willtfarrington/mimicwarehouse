"""Loader — typed CSV → Parquet staging (EP-17 core A; EP-18 buckets, sort, resume).

The **only** package allowed to open files under ``source material/`` (GOVERNANCE §4,
CLAUDE.md §2). It never sniffs types (every ``read_csv`` gets the contract's ``columns=``
dict), never prints rows, and reports by counts, schemas and hashes only.

Modules: :mod:`.engine` (build connection), :mod:`.csv` (typed reader + column maps),
:mod:`.stage` (unpartitioned stage + swap), :mod:`.buckets` (partitioned stage: subject
buckets, per-bucket sort, resume), :mod:`.paths` (reader glob / ``read_parquet`` fragment),
:mod:`.manifest` (manifest lines, status).
"""

from mimicwarehouse.loader.buckets import stage_partitioned
from mimicwarehouse.loader.csv import SchemaMismatchError, csv_relation_sql, plan_csv_read
from mimicwarehouse.loader.engine import DuckDBVersionError, open_build_connection
from mimicwarehouse.loader.manifest import ManifestLine, append_manifest, update_status
from mimicwarehouse.loader.paths import partition_glob, read_parquet_sql
from mimicwarehouse.loader.stage import RejectThresholdError, StageResult, stage_unpartitioned

__all__ = [
    "DuckDBVersionError",
    "ManifestLine",
    "RejectThresholdError",
    "SchemaMismatchError",
    "StageResult",
    "append_manifest",
    "csv_relation_sql",
    "open_build_connection",
    "partition_glob",
    "plan_csv_read",
    "read_parquet_sql",
    "stage_partitioned",
    "stage_unpartitioned",
    "update_status",
]
