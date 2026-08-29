"""The one CSV dialect for every MIMIC-IV read and write (EP-169, retro SCH-1; D-17 addendum).

Upstream ships plain RFC-4180 CSV (``build_mimic.sh``: comma, double-quote quoting with doubled
quotes, header row, empty field = NULL) and the owner policy (D-17) keeps DuckDB's
``allow_quoted_nulls=true`` so a quoted empty string also loads as NULL - that is what upstream's
own DuckDB build does and what the two recorded nullability relaxations
(``microbiologyevents.spec_type_desc``, ``prescriptions.drug``) rely on.

``DATEFORMAT`` / ``TIMESTAMPFORMAT`` are **None** on purpose: readers rely on DuckDB's ISO cast,
which accepts the optional fractional seconds of the nine upstream ``TIMESTAMP(3)`` columns
(pharmacy 5, prescriptions 2, outputevents 2 - recorded as ``upstream_type`` in the contract),
whereas ``try_strptime(..., '%Y-%m-%d %H:%M:%S')`` fails on ``.123`` (verified at EP-169).
The fixture *writer* keeps emitting second-resolution ``%Y-%m-%d %H:%M:%S`` timestamps
(:data:`WRITE_TIMESTAMP_FORMAT` / :data:`WRITE_DATE_FORMAT`, re-exported by
:mod:`mimicwarehouse.fixtures.write`) - a write format, not a read expectation.

Consumers: :meth:`mimicwarehouse.schema.contract.Table.read_csv_options`,
:data:`mimicwarehouse.fixtures.catalog.READ_CSV_SQL`,
:data:`mimicwarehouse.inventory.CSV_READ_OPTIONS`, and the EP-17 loader.
Import cost: stdlib only (this module is on the ``mwh`` start-up path via inventory).
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import PurePath
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from mimicwarehouse.schema.contract import Table

DELIM = ","
QUOTE = '"'
ESCAPE = '"'
HEADER = True
#: Unquoted empty field = NULL (DuckDB ``nullstr``).
NULLSTR = ""
#: A *quoted* empty string is NULL too (owner policy, D-17: matches upstream build_mimic.sh and
#: the two contract nullability relaxations).
ALLOW_QUOTED_NULLS = True
#: None = no strptime format; rely on DuckDB's ISO cast (accepts optional fractional seconds).
DATEFORMAT: str | None = None
TIMESTAMPFORMAT: str | None = None

#: What the fixture writer emits (write-side only; readers use the ISO cast above).
WRITE_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"
WRITE_DATE_FORMAT = "%Y-%m-%d"


def _sql_str(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def _sql_bool(value: bool) -> str:
    return "true" if value else "false"


#: The dialect as a ``read_csv`` options fragment (no columns, no format strings - see above).
READ_OPTIONS_SQL = (
    f"header={_sql_bool(HEADER)}, delim={_sql_str(DELIM)}, quote={_sql_str(QUOTE)}, "
    f"escape={_sql_str(ESCAPE)}, nullstr={_sql_str(NULLSTR)}, "
    f"allow_quoted_nulls={_sql_bool(ALLOW_QUOTED_NULLS)}"
)
#: Parameterised strict read: ``execute(READ_CSV_SQL, [path, columns])``.
READ_CSV_SQL = f"SELECT * FROM read_csv(?, columns=?, {READ_OPTIONS_SQL}, ignore_errors=false)"


def read_csv_options(columns: Mapping[str, str] | None = None) -> dict[str, Any]:
    """The dialect as DuckDB ``read_csv`` keyword options (plus ``columns`` when given)."""
    options: dict[str, Any] = {
        "header": HEADER,
        "delim": DELIM,
        "quote": QUOTE,
        "escape": ESCAPE,
        "nullstr": NULLSTR,
        "allow_quoted_nulls": ALLOW_QUOTED_NULLS,
    }
    if DATEFORMAT is not None:
        options["dateformat"] = DATEFORMAT
    if TIMESTAMPFORMAT is not None:
        options["timestampformat"] = TIMESTAMPFORMAT
    if columns is not None:
        options["columns"] = dict(columns)
    return options


def read_csv_sql(path: str | PurePath, table: Table) -> str:
    """Literal ``SELECT * FROM read_csv('<path>', columns={...}, <dialect>)`` for one contract
    table - the strict typed read every loader / catalog uses (``ignore_errors=false``)."""
    cols = ", ".join(
        f"{_sql_str(name)}: {_sql_str(duck)}" for name, duck in table.read_csv_columns().items()
    )
    return (
        f"SELECT * FROM read_csv({_sql_str(str(path))}, columns={{{cols}}}, "
        f"{READ_OPTIONS_SQL}, ignore_errors=false)"
    )


__all__ = [
    "ALLOW_QUOTED_NULLS",
    "DATEFORMAT",
    "DELIM",
    "ESCAPE",
    "HEADER",
    "NULLSTR",
    "QUOTE",
    "READ_CSV_SQL",
    "READ_OPTIONS_SQL",
    "TIMESTAMPFORMAT",
    "WRITE_DATE_FORMAT",
    "WRITE_TIMESTAMP_FORMAT",
    "read_csv_options",
    "read_csv_sql",
]
