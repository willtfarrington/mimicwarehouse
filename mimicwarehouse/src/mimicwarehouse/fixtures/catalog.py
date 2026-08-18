"""In-memory fixture catalog - typed DuckDB over the fixture CSVs (EP-12 item 4).

:func:`build_fixture_catalog` opens an **in-memory** DuckDB configured with
``get_settings().duckdb_settings("app")`` (explicit ``memory_limit`` / ``threads`` /
``temp_directory`` / ``max_temp_directory_size``, DESIGN section 6), creates the
``mimiciv_hosp`` and ``mimiciv_icu`` schemas and one table per contract table (31) with
``CREATE TABLE ... AS SELECT * FROM read_csv(<file>, header=true, columns=<contract types>,
ignore_errors=false)`` over ``tests/fixtures/mimic-iv-3.1/<module>/<table>.csv``, then stamps the
contract comments (``COMMENT ON TABLE`` / ``COMMENT ON COLUMN``). It is the ``fixture`` pytest
tier (``tests/conftest.py`` ``fixture_catalog`` session fixture) until EP-21 builds a real
``fixture.duckdb`` from the same CSVs with the loader; the read-only cursor / safe-query
discipline of the real catalogs does not apply here because every row is synthetic.

Budget: < 5 s for the committed fixture (a few MB of CSV). Nothing here touches the data root:
the settings are read only for the DuckDB configuration values.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from mimicwarehouse.fixtures.write import DATASET_DIR, DATE_FORMAT, MODULE_SCHEMAS, TIMESTAMP_FORMAT

if TYPE_CHECKING:  # pragma: no cover
    import duckdb

    from mimicwarehouse.config import Settings
    from mimicwarehouse.schema.contract import Contract

#: Schemas the fixture catalog holds, in creation order (module dir -> schema).
FIXTURE_SCHEMAS: tuple[str, ...] = tuple(MODULE_SCHEMAS.values())
#: The ``read_csv`` clause every table is loaded through (typed, strict).
READ_CSV_SQL = (
    "SELECT * FROM read_csv(?, header=true, columns=?, delim=',', quote='\"', escape='\"', "
    f"timestampformat='{TIMESTAMP_FORMAT}', dateformat='{DATE_FORMAT}', ignore_errors=false)"
)


class FixtureCatalogError(RuntimeError):
    """The fixture tree is missing or a CSV does not load with the contract types."""


def fixture_dataset_dir(root: Path | None = None) -> Path:
    """``<root>/mimic-iv-3.1`` (root defaults to ``tests/fixtures`` in the checkout)."""
    from mimicwarehouse.fixtures.write import default_out_dir

    base = Path(root) if root is not None else default_out_dir()
    return base / DATASET_DIR


def _quote(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def build_fixture_catalog(
    root: Path | None = None,
    *,
    contract: Contract | None = None,
    settings: Settings | None = None,
    comments: bool = True,
) -> duckdb.DuckDBPyConnection:
    """An in-memory DuckDB with the 31 contract tables loaded from the fixture CSVs under
    ``root`` (default: the committed fixture). Close it when done."""
    import duckdb

    from mimicwarehouse.config import get_settings
    from mimicwarehouse.schema.contract import load_contract

    contract = contract or load_contract()
    settings = settings or get_settings()
    dataset = fixture_dataset_dir(root)
    if not dataset.is_dir():
        raise FixtureCatalogError(
            f"fixture dataset directory {dataset} not found - run `uv run mwh fixtures build`"
        )
    config: dict[str, Any] = dict(settings.duckdb_settings("app"))
    con = duckdb.connect(":memory:", config=config)
    try:
        for schema in FIXTURE_SCHEMAS:
            con.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
        for schema in FIXTURE_SCHEMAS:
            for table in contract.by_schema(schema):
                path = dataset / Path(*table.csv_path.split("/"))
                if not path.is_file():
                    raise FixtureCatalogError(f"{table.qualified_name}: {path} is missing")
                try:
                    con.execute(
                        f"CREATE TABLE {table.qualified_name} AS {READ_CSV_SQL}",
                        [str(path), table.read_csv_columns()],
                    )
                except duckdb.Error as exc:  # pragma: no cover - a broken fixture
                    raise FixtureCatalogError(f"{table.qualified_name}: {exc}") from exc
                if comments:
                    if table.comment:
                        con.execute(
                            f"COMMENT ON TABLE {table.qualified_name} IS {_quote(table.comment)}"
                        )
                    for c in table.columns:
                        if c.comment:
                            con.execute(
                                f"COMMENT ON COLUMN {table.qualified_name}.{c.name} "
                                f"IS {_quote(c.comment)}"
                            )
    except Exception:
        con.close()
        raise
    return con


def catalog_tables(con: duckdb.DuckDBPyConnection) -> list[str]:
    """``schema.table`` names of the fixture catalog, sorted."""
    rows = con.execute(
        "SELECT table_schema || '.' || table_name FROM information_schema.tables "
        "WHERE table_schema IN (SELECT unnest(?)) ORDER BY 1",
        [list(FIXTURE_SCHEMAS)],
    ).fetchall()
    return [r[0] for r in rows]


__all__ = [
    "FIXTURE_SCHEMAS",
    "READ_CSV_SQL",
    "FixtureCatalogError",
    "build_fixture_catalog",
    "catalog_tables",
    "fixture_dataset_dir",
]
