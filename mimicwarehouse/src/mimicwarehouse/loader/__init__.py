"""Loader — typed CSV → Parquet staging (EP-17 core A; EP-18 buckets, sort, resume).

The **only** package allowed to open files under ``source material/`` (GOVERNANCE §4,
CLAUDE.md §2). It never sniffs types (every ``read_csv`` gets the contract's ``columns=``
dict), never prints rows, and reports by counts, schemas and hashes only.

Modules: :mod:`.engine` (build connection), :mod:`.csv` (typed reader + column maps),
:mod:`.stage` (unpartitioned stage + swap), :mod:`.buckets` (partitioned stage: subject
buckets, per-bucket sort, resume), :mod:`.paths` (reader glob / ``read_parquet`` fragment),
:mod:`.manifest` (manifest lines, status).

Public names are re-exported **lazily** (module ``__getattr__``, the ``fixtures/__init__``
form — EP-33 B8): importing the package must not drag pydantic models, the schema contract
or DuckDB into ``mwh --help`` (import budget, DESIGN §15). Callers that need a submodule
import it directly (``from mimicwarehouse.loader import paths as loader_paths``).
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

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

if TYPE_CHECKING:  # pragma: no cover - static names for type checkers / IDEs only
    from mimicwarehouse.loader.buckets import stage_partitioned
    from mimicwarehouse.loader.csv import SchemaMismatchError, csv_relation_sql, plan_csv_read
    from mimicwarehouse.loader.engine import DuckDBVersionError, open_build_connection
    from mimicwarehouse.loader.manifest import ManifestLine, append_manifest, update_status
    from mimicwarehouse.loader.paths import partition_glob, read_parquet_sql
    from mimicwarehouse.loader.stage import RejectThresholdError, StageResult, stage_unpartitioned

_HOMES: dict[str, str] = {
    "stage_partitioned": "buckets",
    "SchemaMismatchError": "csv",
    "csv_relation_sql": "csv",
    "plan_csv_read": "csv",
    "DuckDBVersionError": "engine",
    "open_build_connection": "engine",
    "ManifestLine": "manifest",
    "append_manifest": "manifest",
    "update_status": "manifest",
    "partition_glob": "paths",
    "read_parquet_sql": "paths",
    "RejectThresholdError": "stage",
    "StageResult": "stage",
    "stage_unpartitioned": "stage",
}


def __getattr__(name: str) -> Any:
    home = _HOMES.get(name)
    if home is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(import_module(f"{__name__}.{home}"), name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
