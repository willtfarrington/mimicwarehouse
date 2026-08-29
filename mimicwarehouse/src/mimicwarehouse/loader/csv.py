"""Typed CSV reads for the loader (EP-17 item 2).

Builds the ``read_csv(...)`` SQL fragment for one contract table under the one CSV dialect
(:mod:`mimicwarehouse.schema.csv_dialect`, EP-169 — ``allow_quoted_nulls=true``, **no**
``dateformat``/``timestampformat``: DuckDB's ISO cast accepts the optional fractional
seconds of the nine upstream ``TIMESTAMP(3)`` columns), with the contract's ``columns=``
dict (never the sniffer) and per-read reject capture (``store_rejects`` into
``<table>_rejects`` / ``<table>_scans`` temp tables — row-level, so they stay inside the
connection; :mod:`.stage` copies them to the data root and reports the *count* only).

Before any read the file header is validated — first line, names only — against the
contract after applying the column map; a mismatch raises :class:`SchemaMismatchError`
naming missing/extra column *names* (never values). Column maps are the contract's
(:meth:`ColumnMap.apply`): a source column mapped to a contract column is renamed in the
``columns=`` dict itself, one mapped to ``None`` is read as VARCHAR and dropped by the
projection, and a contract column absent from the source becomes a typed NULL. The
projection (:attr:`CsvPlan.select_exprs`) always yields the contract's columns, in order.

``.csv`` and ``.csv.gz`` are both accepted; compression is inferred from the extension.
This module opens raw files only to read their first line (header names).
"""

from __future__ import annotations

import csv as _stdlib_csv
import gzip
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mimicwarehouse.schema.contract import ColumnMap, Table
from mimicwarehouse.schema.csv_dialect import read_csv_options

#: Reject / scan temp-table name suffixes (``store_rejects``; EP-17 item 2).
REJECTS_SUFFIX = "_rejects"
SCANS_SUFFIX = "_scans"


class SchemaMismatchError(ValueError):
    """A CSV header does not match the contract (after the column map, if any)."""


def rejects_table_for(table: Table) -> str:
    return f"{table.name}{REJECTS_SUFFIX}"


def scans_table_for(table: Table) -> str:
    return f"{table.name}{SCANS_SUFFIX}"


def source_compression(source: Path) -> str | None:
    """``'gzip'`` for ``.csv.gz``, ``None`` for ``.csv``; anything else is refused."""
    name = source.name.lower()
    if name.endswith(".csv.gz"):
        return "gzip"
    if name.endswith(".csv"):
        return None
    raise SchemaMismatchError(f"{source.name}: expected a .csv or .csv.gz file")


def read_csv_header(source: Path) -> list[str]:
    """Column names from the first line only (gzip-aware, UTF-8, BOM/CRLF tolerant)."""
    opener = gzip.open if source_compression(source) == "gzip" else open
    with opener(source, "rb") as f:  # type: ignore[operator]
        first = f.readline()
    text = first.decode("utf-8-sig", errors="replace").rstrip("\r\n")
    if not text:
        return []
    return next(_stdlib_csv.reader([text]))


def _sql_str(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


@dataclass(frozen=True, slots=True)
class CsvPlan:
    """One validated typed read: the ``read_csv`` fragment plus the contract projection."""

    relation_sql: str  # read_csv('<path>', columns={...}, <dialect>, store_rejects, ...)
    select_exprs: tuple[str, ...]  # contract columns in order; absent ones as typed NULL
    supplied: tuple[str, ...]  # contract columns the source actually carries
    rejects_table: str
    rejects_scan: str


def apply_column_map(
    table: Table, header: list[str], column_map: ColumnMap | None
) -> dict[str, str | None]:
    """``{csv_col: contract_col | None}`` for the header, validated — raises
    :class:`SchemaMismatchError` (missing/extra column *names* only) on any problem."""
    if column_map is None:
        if header == list(table.column_names):
            return {c: c for c in header}
        expected, got = set(table.column_names), set(header)
        missing = [c for c in table.column_names if c not in got]
        extra = [c for c in header if c not in expected]
        detail = (
            f"missing columns {missing}, extra columns {extra}"
            if missing or extra
            else "same names, different order"
        )
        raise SchemaMismatchError(
            f"{table.qualified_name}: header does not match the contract — {detail}"
        )
    problems = column_map.check(table, header)
    if problems:
        raise SchemaMismatchError(
            f"{table.qualified_name}: header does not match the contract under column map "
            f"{column_map.name!r} — {'; '.join(problems)}"
        )
    return column_map.apply(table, header)


def _read_columns(
    table: Table, header: list[str], applied: dict[str, str | None]
) -> dict[str, str]:
    """The ``columns=`` dict, in CSV order: mapped source columns take the contract name and
    type (the rename happens inside ``read_csv``); dropped ones keep their source name as
    VARCHAR and are excluded by the projection."""
    out: dict[str, str] = {}
    for csv_col in header:
        target = applied[csv_col]
        name = target if target is not None else csv_col
        if name in out:
            raise SchemaMismatchError(
                f"{table.qualified_name}: duplicate column {name!r} after applying the map"
            )
        out[name] = table.column(target).duckdb_type if target is not None else "VARCHAR"
    return out


def plan_csv_read(source: Path, table: Table, column_map: ColumnMap | None = None) -> CsvPlan:
    """Validate ``source``'s header and build the typed read plan (module docstring)."""
    source = Path(source)
    compression = source_compression(source)
    header = read_csv_header(source)
    applied = apply_column_map(table, header, column_map)
    columns = _read_columns(table, header, applied)

    options: dict[str, Any] = read_csv_options()  # the one dialect (EP-169); no format strings
    options["parallel"] = True
    options["store_rejects"] = True
    options["rejects_table"] = rejects_table_for(table)
    options["rejects_scan"] = scans_table_for(table)
    if compression is not None:
        options["compression"] = compression

    def render(value: Any) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        return _sql_str(str(value))

    cols_sql = ", ".join(f"{_sql_str(n)}: {_sql_str(t)}" for n, t in columns.items())
    opts_sql = ", ".join(f"{key}={render(value)}" for key, value in options.items())
    relation_sql = f"read_csv({_sql_str(str(source))}, columns={{{cols_sql}}}, {opts_sql})"

    supplied = frozenset(v for v in applied.values() if v is not None)
    select_exprs = tuple(
        c.name if c.name in supplied else f"CAST(NULL AS {c.duckdb_type}) AS {c.name}"
        for c in table.columns
    )
    return CsvPlan(
        relation_sql=relation_sql,
        select_exprs=select_exprs,
        supplied=tuple(c for c in table.column_names if c in supplied),
        rejects_table=rejects_table_for(table),
        rejects_scan=scans_table_for(table),
    )


def csv_relation_sql(source: Path, table_spec: Table, column_map: ColumnMap | None = None) -> str:
    """The ``read_csv(...)`` fragment for one contract table (EP-17 item 2's named API)."""
    return plan_csv_read(source, table_spec, column_map).relation_sql


def max_length_probe(con: Any, source: Path, columns: list[str]) -> dict[str, int | None]:
    """``{column: max(length(column))}`` over an ``all_varchar=true`` read — the EP-17
    dev-tier probe for the nine upstream ``TIMESTAMP(3)`` columns: 19 chars = no fractional
    seconds in the raw CSV, 21-23 = fractional seconds present. Counts only, never values;
    lives here so no test reads a raw file outside the loader."""
    from mimicwarehouse.schema.csv_dialect import READ_OPTIONS_SQL

    exprs = ", ".join(f"max(length({c}))" for c in columns)
    sql = (
        f"SELECT {exprs} FROM read_csv({_sql_str(str(Path(source)))}, "
        f"{READ_OPTIONS_SQL}, all_varchar=true)"
    )
    row = con.execute(sql).fetchone()
    return {c: (None if v is None else int(v)) for c, v in zip(columns, row, strict=True)}


__all__ = [
    "REJECTS_SUFFIX",
    "SCANS_SUFFIX",
    "CsvPlan",
    "SchemaMismatchError",
    "apply_column_map",
    "csv_relation_sql",
    "max_length_probe",
    "plan_csv_read",
    "read_csv_header",
    "rejects_table_for",
    "scans_table_for",
    "source_compression",
]
