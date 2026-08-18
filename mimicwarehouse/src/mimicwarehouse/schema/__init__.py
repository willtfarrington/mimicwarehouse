"""Schema registry — the YAML schema contract for MIMIC-IV 3.1 / ED 2.2 / Note 2.2 (EP-9).

- :mod:`mimicwarehouse.schema.contract` — pydantic models (:class:`Column`, :class:`Table`,
  :class:`ForeignKey`, :class:`ColumnMap`, :class:`UnitsSpec`, :class:`Contract`) and the loader
  :func:`load_contract` over the package data in ``schema/tables/``;
- :mod:`mimicwarehouse.schema.transcribe` — the Postgres → DuckDB type map, the ``create.sql`` /
  ``constraint.sql`` parsers, the YAML draft writer and the drift check;
- :mod:`mimicwarehouse.schema.cli` — ``mwh schema list | show | ddl | check | transcribe``.

The public names below are re-exported **lazily** (module ``__getattr__``): ``mimicwarehouse.cli``
imports :mod:`mimicwarehouse.schema.cli` at start-up, and this package's ``__init__`` must not
drag yaml + the pydantic models into ``mwh --help`` (import budget, DESIGN §15 EP-2 note).
Nothing here reads data.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

__all__ = [
    "DATASETS",
    "EXPECTED_TABLE_COUNTS",
    "SCHEMAS",
    "SUBJECT_KEY",
    "Column",
    "ColumnMap",
    "Contract",
    "FixedUnit",
    "ForeignKey",
    "ImpliedUnit",
    "SchemaError",
    "SchemaInfo",
    "Table",
    "TableMap",
    "UnitsSpec",
    "ValueUnitPair",
    "load_contract",
    "load_contract_from",
    "tables_root",
]

if TYPE_CHECKING:  # pragma: no cover — static names for type checkers / IDEs only
    from mimicwarehouse.schema.contract import (
        DATASETS,
        EXPECTED_TABLE_COUNTS,
        SCHEMAS,
        SUBJECT_KEY,
        Column,
        ColumnMap,
        Contract,
        FixedUnit,
        ForeignKey,
        ImpliedUnit,
        SchemaError,
        SchemaInfo,
        Table,
        TableMap,
        UnitsSpec,
        ValueUnitPair,
        load_contract,
        load_contract_from,
        tables_root,
    )


def __getattr__(name: str) -> Any:
    if name in __all__:
        return getattr(import_module("mimicwarehouse.schema.contract"), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
