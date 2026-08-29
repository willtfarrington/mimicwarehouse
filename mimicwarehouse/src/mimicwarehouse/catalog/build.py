"""Catalog build step — ``warehouse/<tier>.duckdb`` from the staged lake (EP-21 item 1).

DESIGN §3/§6 and D-17/D-18: one DuckDB file per tier holding the ``mimiciv_hosp`` /
``mimiciv_icu`` names every consumer addresses — **dimensions materialized as tables in
every tier, subject-keyed tables as views** over the Hive-partitioned Parquet (the DESIGN
§21 decision made here; Hive pruning already makes dev fast, EP-55 revisits for marts).
``mwh build`` is the only writer: the catalog is built to ``<tier>.duckdb.new`` and
published by the **rename-aside two-step** (DESIGN §6 note, D-43 item 6):
``os.rename(<tier>.duckdb → .old)`` succeeds with DuckDB READ_ONLY readers open
(``FILE_SHARE_DELETE``), then ``os.replace(.new → <tier>.duckdb)``, then remove ``.old``
(Windows holds it delete-pending while a reader lives). Only when even the rename fails
(a non-sharing handle) does the build raise the documented "close the app/notebooks and
rerun" message and leave the old catalog intact.

Which tables enter the catalog is decided by the lake's ``status.json`` (EP-17/19):
``full`` requires ``tier_complete = "full"``; ``dev`` accepts ``dev_ready`` (the EP-19
:func:`~mimicwarehouse.dag.snapshot.complete_for_tier` predicate); ``fixture`` / ``demo``
require completeness on their **own** lake roots. Tables not yet staged are **omitted**
(never empty views) and listed as ``missing`` in ``meta.catalog_tables``.
``meta.catalog_info`` records build provenance (build id, DuckDB/package versions, git
sha, core snapshot id, lake root, ``k_default``) plus the ``dev_buckets`` the catalog was
built with (EP-170 amendment 3) — the build warns when they drift from the previous
catalog's. Catalogs embed absolute lake paths and assert the DuckDB version on open:
they are **derived and disposable** — after a data-root move or a pin bump the fix is
``mwh build --tier <t> --select catalog``, never surgery (ledger ARCH-16).

Everything created, logged or returned is schema DDL, counts and paths — never a row.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mimicwarehouse import __version__
from mimicwarehouse.config import (
    Settings,
    Tier,
    assert_not_credentialed_lake,
    get_settings,
    require_free_space,
)
from mimicwarehouse.dag.snapshot import complete_for_tier, layer_snapshot
from mimicwarehouse.loader.manifest import read_status, utc_now_iso
from mimicwarehouse.loader.paths import read_parquet_sql, table_dir

if TYPE_CHECKING:  # pragma: no cover
    import duckdb

    from mimicwarehouse.schema.contract import Table

_LOG = logging.getLogger(__name__)

#: Schemas created in every catalog (DESIGN §3; ``mimiciv_derived`` filled by EP-37+,
#: ``meta`` by this module + EP-29, ``marts`` by EP-55).
CATALOG_SCHEMAS: tuple[str, ...] = (
    "mimiciv_hosp",
    "mimiciv_icu",
    "mimiciv_derived",
    "meta",
    "marts",
)
#: The schemas P2 stages into the lake — the catalog's candidate tables (the 31 hosp/icu
#: contract tables; mimiciv_ed has no stage step until EP-142, mimiciv_note none until
#: EP-148 — DESIGN §5 note).
STAGED_SCHEMAS: tuple[str, ...] = ("mimiciv_hosp", "mimiciv_icu")

NEW_SUFFIX = ".new"
OLD_SUFFIX = ".old"

#: ``PermissionError`` retry policy for the file swap (anti-virus / indexer holds are
#: transient on Windows; matches :mod:`mimicwarehouse.paths`).
RETRIES = 20
RETRY_BASE_SLEEP_S = 0.05


class CatalogBuildError(RuntimeError):
    """The catalog cannot be built (bad tier, missing lake, DuckDB failure)."""


class CatalogSwapError(CatalogBuildError):
    """The freshly built catalog cannot replace the live one (a non-sharing reader holds
    the file). The old catalog is left intact."""


def catalog_new_path(catalog: Path) -> Path:
    """``<tier>.duckdb.new`` — where a build writes before the swap."""
    return catalog.with_name(catalog.name + NEW_SUFFIX)


def catalog_old_path(catalog: Path) -> Path:
    """``<tier>.duckdb.old`` — the aside name the live catalog briefly holds."""
    return catalog.with_name(catalog.name + OLD_SUFFIX)


@dataclass(frozen=True, slots=True)
class CatalogTableEntry:
    """One ``meta.catalog_tables`` row: identity, kind and staging status — no values."""

    schema_name: str
    table: str
    kind: str  # table | view | missing
    status: str | None  # status.json tier_complete (None = not staged at all)
    rows_hint: int | None  # status.json rows (staged rows, not the dev-filtered count)
    map_notes: dict[str, list[str]] | None = None  # lossy column-map notes (EP-22; names only)

    @property
    def qualified_name(self) -> str:
        return f"{self.schema_name}.{self.table}"


@dataclass(slots=True)
class CatalogBuildResult:
    """What one :func:`build_catalog` produced (counts, ids and paths only)."""

    tier: str
    build_id: str
    path: Path
    core_snapshot_id: str
    tables: list[CatalogTableEntry] = field(default_factory=list)
    bytes: int = 0
    wall_s: float = 0.0

    @property
    def cataloged(self) -> int:
        """Tables actually present in the catalog (kind ``table`` or ``view``)."""
        return sum(1 for t in self.tables if t.kind != "missing")


# ---------------------------------------------------------------------------
# Qualification & DDL
# ---------------------------------------------------------------------------


def qualifies(entry: dict[str, Any] | None, tier: str) -> bool:
    """Whether a ``status.json`` entry admits its table into the ``tier`` catalog:
    ``dev`` accepts ``dev_ready`` / ``tier_complete in ("dev", "full")`` (the EP-19
    predicate); every other tier requires ``tier_complete = "full"`` — for ``fixture`` /
    ``demo`` that entry lives on their own lake roots (EP-167)."""
    if entry is None:
        return False
    if tier == "dev":
        return complete_for_tier(entry, "dev")
    return entry.get("tier_complete") == "full"


def _sql_str(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def _create_table_sql(table: Table, lake_root: Path) -> str:
    """``CREATE TABLE`` over the single published Parquet file of a dimension."""
    part = table_dir(lake_root, table.schema_name, table.name).resolve() / "part-0.parquet"
    return (
        f'CREATE TABLE {table.schema_name}."{table.name}" AS '
        f"SELECT * FROM read_parquet({_sql_str(part.as_posix())})"
    )


def _create_view_sql(table: Table, lake_root: Path, buckets: list[int] | None) -> str:
    """``CREATE VIEW`` with the contract columns in order over the partition glob
    (``read_parquet_sql``, EP-18); ``buckets`` adds the dev partition filter."""
    columns = ", ".join(f'"{c.name}"' for c in table.columns)
    relation = read_parquet_sql(lake_root, table.schema_name, table.name, buckets)
    return f'CREATE VIEW {table.schema_name}."{table.name}" AS SELECT {columns} FROM {relation}'


# ---------------------------------------------------------------------------
# The swap (rename-aside two-step for a single file; DESIGN §6 note)
# ---------------------------------------------------------------------------


def _retry_os(op: Callable[[], None], what: str) -> None:
    for attempt in range(RETRIES):
        try:
            op()
            return
        except PermissionError:
            if attempt == RETRIES - 1:
                raise
            time.sleep(RETRY_BASE_SLEEP_S * (attempt + 1))
        except FileNotFoundError:
            return  # already gone


def swap_catalog(new: Path, dest: Path, tier: str) -> None:
    """Publish ``new`` as ``dest``. Crash-safe, not atomic: there is a sub-millisecond
    window with no ``dest`` (``open_catalog`` retries it, EP-170 amendment 1). Raises
    :class:`CatalogSwapError` — with the old catalog intact — only when even the
    rename-aside fails (a reader without ``FILE_SHARE_DELETE`` holds the file)."""
    new, dest = Path(new), Path(dest)
    if not new.is_file():
        raise CatalogBuildError(f"swap_catalog: {new} is not a file (nothing built?)")
    old = catalog_old_path(dest)
    # crash recovery: an interrupted swap left the live catalog under `.old`
    if not dest.exists() and old.is_file():
        _retry_os(lambda: os.rename(old, dest), "restore .old")
    # a stale `.old` beside a live dest is dead weight from a crash after the replace
    if old.exists():
        _retry_os(lambda: os.remove(old), "remove stale .old")
    if dest.exists():
        try:
            os.rename(dest, old)
        except OSError as exc:
            raise CatalogSwapError(
                f"{dest}: cannot replace the live catalog while a reader holds it open — "
                f"close the app/notebooks and rerun `mwh build --tier {tier} --select catalog` "
                "(the old catalog is intact; DuckDB READ_ONLY readers share the file, a plain "
                "file handle does not)"
            ) from exc
    try:
        os.replace(new, dest)
    except OSError:
        # roll back so the tier is not left without a live catalog
        if not dest.exists() and old.is_file():
            os.rename(old, dest)
        raise
    if old.exists():
        try:
            _retry_os(lambda: os.remove(old), "remove .old")
        except OSError:  # delete-pending edge: the next build's stale-.old sweep gets it
            _LOG.warning("%s: could not remove yet (open reader?); next build removes it", old)


# ---------------------------------------------------------------------------
# The build
# ---------------------------------------------------------------------------


def _recorded_dev_buckets(path: Path) -> list[int] | None:
    """``meta.catalog_info.dev_buckets`` of an existing catalog, or None (absent, not a
    mimicwarehouse catalog, wrong DuckDB version, or locked)."""
    if not path.is_file():
        return None
    import duckdb

    try:
        con = duckdb.connect(str(path), read_only=True)
    except duckdb.Error:
        return None
    try:
        row = con.execute("SELECT dev_buckets FROM meta.catalog_info").fetchone()
    except duckdb.Error:
        return None
    finally:
        con.close()
    try:
        return [int(b) for b in json.loads(row[0])] if row else None
    except (TypeError, ValueError):
        return None


def build_catalog(
    tier: Tier | str,
    settings: Settings | None = None,
    *,
    lake_root: Path | None = None,
    build_id: str | None = None,
) -> CatalogBuildResult:
    """Build ``warehouse/<tier>.duckdb`` from the staged lake and publish it (module
    docstring). Registered as the ``catalog`` step handler (EP-19 ``STEP_HANDLERS``);
    callable standalone for tests and for rebuilds after a data-root move / pin bump."""
    import duckdb

    from mimicwarehouse.loader.engine import require_pinned_duckdb
    from mimicwarehouse.schema.contract import load_contract

    settings = settings or get_settings()
    lake_root = Path(lake_root) if lake_root is not None else settings.lake_root(tier)
    assert_not_credentialed_lake(tier, lake_root, settings)
    if build_id is None:
        from mimicwarehouse.dag.runner import new_build_id

        build_id = new_build_id(str(tier))
    require_pinned_duckdb()
    require_free_space(settings.data_root, settings.min_free_gb_for(tier))

    dest = settings.catalog_path(tier)
    new = catalog_new_path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    settings.layout["tmp_duckdb"].mkdir(parents=True, exist_ok=True)

    previous_buckets = _recorded_dev_buckets(dest)
    if previous_buckets is not None and previous_buckets != list(settings.dev_buckets):
        _LOG.warning(
            "dev_buckets drift: the previous %s catalog was built with %s, settings now say "
            "%s — dev views and snapshot ids follow the new setting (EP-170/ARCH-8)",
            tier,
            previous_buckets,
            list(settings.dev_buckets),
        )

    new.unlink(missing_ok=True)  # a stale .new from a crashed build
    t0 = time.perf_counter()
    result = CatalogBuildResult(
        tier=str(tier),
        build_id=build_id,
        path=dest,
        core_snapshot_id=layer_snapshot(lake_root, "core", str(tier), settings=settings),
    )
    status = read_status(lake_root)["steps"]
    contract = load_contract()
    buckets = list(settings.dev_buckets) if tier == "dev" else None

    con = duckdb.connect(str(new), config=dict(settings.duckdb_settings("build")))
    try:
        _populate(con, result, status, contract, lake_root, buckets, settings)
    except duckdb.Error as exc:
        new.unlink(missing_ok=True)
        raise CatalogBuildError(f"{tier} catalog build failed: {exc}") from exc

    swap_catalog(new, dest, str(tier))
    result.bytes = dest.stat().st_size
    result.wall_s = round(time.perf_counter() - t0, 3)
    _LOG.info(
        "catalog %s: %d table(s)/view(s), %d missing, %s bytes, wall=%.1fs — %s",
        tier,
        result.cataloged,
        len(result.tables) - result.cataloged,
        f"{result.bytes:,}",
        result.wall_s,
        dest,
    )
    return result


def _populate(
    con: duckdb.DuckDBPyConnection,
    result: CatalogBuildResult,
    status: dict[str, Any],
    contract: Any,
    lake_root: Path,
    buckets: list[int] | None,
    settings: Settings,
) -> None:
    """Schemas, tables/views, the ``meta`` dictionary tables (EP-21 + EP-29), then
    ``CHECKPOINT`` + close."""
    import duckdb

    tier = result.tier
    build_id = result.build_id
    try:
        for schema in CATALOG_SCHEMAS:
            con.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
        for schema in STAGED_SCHEMAS:
            for table in contract.by_schema(schema):
                entry = status.get(table.qualified_name)
                tier_complete = entry.get("tier_complete") if entry else None
                rows_hint = entry.get("rows") if entry else None
                if not qualifies(entry, tier):
                    kind = "missing"
                elif table.partitioned:
                    kind = "view"
                    con.execute(_create_view_sql(table, lake_root, buckets))
                else:
                    kind = "table"
                    con.execute(_create_table_sql(table, lake_root))
                result.tables.append(
                    CatalogTableEntry(
                        schema_name=schema,
                        table=table.name,
                        kind=kind,
                        status=tier_complete,
                        rows_hint=rows_hint,
                        map_notes=entry.get("map_notes") if entry else None,
                    )
                )
        _populate_meta(con, result, contract, lake_root, settings)
        con.execute(
            'CREATE TABLE meta.catalog_tables ("schema" VARCHAR, "table" VARCHAR, '
            "kind VARCHAR, status VARCHAR, rows_hint BIGINT, map_notes VARCHAR)"
        )
        con.executemany(
            "INSERT INTO meta.catalog_tables VALUES (?, ?, ?, ?, ?, ?)",
            [
                [
                    t.schema_name,
                    t.table,
                    t.kind,
                    t.status,
                    t.rows_hint,
                    None if t.map_notes is None else json.dumps(t.map_notes, sort_keys=True),
                ]
                for t in result.tables
            ],
        )
        con.execute(
            "CREATE TABLE meta.catalog_info (build_id VARCHAR, tier VARCHAR, "
            "duckdb_version VARCHAR, package_version VARCHAR, git_sha VARCHAR, "
            "core_snapshot_id VARCHAR, lake_root VARCHAR, built_at VARCHAR, "
            "k_default INTEGER, dev_buckets VARCHAR)"
        )
        from mimicwarehouse.dag.runner import git_short_sha

        con.execute(
            "INSERT INTO meta.catalog_info VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                build_id,
                str(tier),
                duckdb.__version__,
                __version__,
                git_short_sha(),
                result.core_snapshot_id,
                str(lake_root.resolve()),
                utc_now_iso(),
                settings.k_suppression,
                json.dumps(list(settings.dev_buckets)),
            ],
        )
        con.execute("CHECKPOINT")
    finally:
        con.close()


# ---------------------------------------------------------------------------
# The meta.* dictionary tables (EP-29 item 3)
# ---------------------------------------------------------------------------


def unit_hint(contract: Any, table: Table, column: Any) -> str | None:
    """The short unit note ``meta.columns.unit_hint`` carries, from the EP-9 units seed:
    a fixed unit is the unit string itself (``kg``); a unit column says which value
    column it qualifies (``unit of valuenum``); a value column names its unit/implied-by
    column (``unit in valueuom`` / ``implied by result_name``). None otherwise —
    itemid-level expectations are EP-39's."""
    if column.unit_of:
        return f"unit of {column.unit_of}"
    qn = table.qualified_name
    for pair in contract.units.value_unit_pairs:
        if pair.table == qn and pair.value == column.name:
            return f"unit in {pair.unit}"
    for fixed in contract.units.fixed_units:
        if fixed.table == qn and fixed.column == column.name:
            return fixed.unit
    for implied in contract.units.implied_units:
        if implied.table == qn and implied.value == column.name:
            return f"implied by {implied.implied_by}"
    return None


def _read_profiles(
    con: duckdb.DuckDBPyConnection, lake_root: Path, tier: str
) -> tuple[dict[str, int], dict[tuple[str, str], tuple[float | None, int | None]]]:
    """The tier's ``lake/meta`` profile Parquet, if present: ``{schema.table: row_count}``
    and ``{(schema.table, column): (null_pct, approx_distinct)}`` — empty dicts (never an
    error) while ``meta.profile`` has not run for this tier."""
    from mimicwarehouse.catalog.profile import profile_paths

    tables_path, columns_path = profile_paths(lake_root, tier)
    if not tables_path.is_file() or not columns_path.is_file():
        return {}, {}
    tables_sql = _sql_str(tables_path.resolve().as_posix())
    columns_sql = _sql_str(columns_path.resolve().as_posix())
    table_counts = {
        f"{s}.{t}": int(rows)
        for s, t, rows in con.execute(
            f'SELECT "schema", "table", row_count FROM read_parquet({tables_sql})'
        ).fetchall()
    }
    column_profiles = {
        (f"{s}.{t}", c): (p, None if d is None else int(d))
        for s, t, c, p, d in con.execute(
            f'SELECT "schema", "table", "column", null_pct, approx_distinct '
            f"FROM read_parquet({columns_sql})"
        ).fetchall()
    }
    return table_counts, column_profiles


def _comment_on(
    con: duckdb.DuckDBPyConnection, kind: str, qualified: str, comment: str | None
) -> None:
    if comment:
        keyword = "VIEW" if kind == "view" else "TABLE"
        con.execute(f"COMMENT ON {keyword} {qualified} IS {_sql_str(comment)}")


def _populate_meta(
    con: duckdb.DuckDBPyConnection,
    result: CatalogBuildResult,
    contract: Any,
    lake_root: Path,
    settings: Settings,
) -> None:
    """``meta.tables`` / ``meta.columns`` / ``meta.row_counts`` / the ``meta.itemids``
    view, plus ``COMMENT ON`` for every cataloged table and column (EP-29 item 3).
    Row counts and bytes come from the manifests (no scan; dev = bucket-filtered lines);
    ``null_pct`` / ``approx_distinct`` come from the ``meta.profile`` Parquet when it
    exists. Everything written is contract text and aggregate counts — never a value."""
    from mimicwarehouse.dag.snapshot import table_file_stats

    tier = result.tier
    kinds = {t.qualified_name: t.kind for t in result.tables}
    stats = table_file_stats(lake_root, tier, settings=settings)
    table_counts, column_profiles = _read_profiles(con, lake_root, tier)

    con.execute(
        'CREATE TABLE meta.tables ("schema" VARCHAR, "table" VARCHAR, description VARCHAR, '
        "kind VARCHAR, partitioned BOOLEAN, row_count BIGINT, bytes BIGINT, files INTEGER, "
        "build_id VARCHAR, snapshot_id VARCHAR)"
    )
    con.execute(
        'CREATE TABLE meta.columns ("schema" VARCHAR, "table" VARCHAR, "column" VARCHAR, '
        "ordinal INTEGER, duckdb_type VARCHAR, nullable BOOLEAN, description VARCHAR, "
        "is_identifier BOOLEAN, is_free_text BOOLEAN, unit_hint VARCHAR, null_pct DOUBLE, "
        "approx_distinct BIGINT)"
    )
    con.execute(
        'CREATE TABLE meta.row_counts ("schema" VARCHAR, "table" VARCHAR, tier VARCHAR, '
        "rows BIGINT, source VARCHAR)"
    )

    table_rows: list[list[Any]] = []
    column_rows: list[list[Any]] = []
    count_rows: list[list[Any]] = []
    for schema in STAGED_SCHEMAS:
        for table in contract.by_schema(schema):
            qn = table.qualified_name
            rows, size, files = stats.get(qn, (None, None, None))
            table_rows.append(
                [
                    schema,
                    table.name,
                    table.comment,
                    kinds.get(qn, "missing"),
                    table.partitioned,
                    rows,
                    size,
                    files,
                    result.build_id,
                    result.core_snapshot_id,
                ]
            )
            if rows is not None:
                count_rows.append([schema, table.name, tier, rows, "manifest"])
            if qn in table_counts:
                count_rows.append([schema, table.name, tier, table_counts[qn], "profile"])
            for ordinal, column in enumerate(table.columns, start=1):
                null_pct, approx_distinct = column_profiles.get((qn, column.name), (None, None))
                column_rows.append(
                    [
                        schema,
                        table.name,
                        column.name,
                        ordinal,
                        column.duckdb_type,
                        column.nullable,
                        column.comment,
                        column.identifier,
                        column.free_text,
                        unit_hint(contract, table, column),
                        null_pct,
                        approx_distinct,
                    ]
                )
    con.executemany("INSERT INTO meta.tables VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", table_rows)
    con.executemany(
        "INSERT INTO meta.columns VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", column_rows
    )
    if count_rows:
        con.executemany("INSERT INTO meta.row_counts VALUES (?, ?, ?, ?, ?)", count_rows)

    # COMMENT ON so DESCRIBE / duckdb_columns() / the app show the contract descriptions.
    for table in (t for s in STAGED_SCHEMAS for t in contract.by_schema(s)):
        kind = kinds.get(table.qualified_name, "missing")
        if kind == "missing":
            continue
        qualified = f'{table.schema_name}."{table.name}"'
        _comment_on(con, kind, qualified, table.comment)
        for column in table.columns:
            if column.comment:
                con.execute(
                    f'COMMENT ON COLUMN {qualified}."{column.name}" IS {_sql_str(column.comment)}'
                )

    # meta.itemids — the EP-39 curation base: both item dimensions under one shape.
    if kinds.get("mimiciv_icu.d_items") != "missing" and (
        kinds.get("mimiciv_hosp.d_labitems") != "missing"
    ):
        con.execute(
            "CREATE VIEW meta.itemids AS "
            "SELECT 'icu' AS source, itemid, label, abbreviation, linksto, category, "
            "CAST(NULL AS VARCHAR) AS fluid, unitname, param_type "
            "FROM mimiciv_icu.d_items "
            "UNION ALL "
            "SELECT 'hosp', itemid, label, CAST(NULL AS VARCHAR), CAST(NULL AS VARCHAR), "
            "category, fluid, CAST(NULL AS VARCHAR), CAST(NULL AS VARCHAR) "
            "FROM mimiciv_hosp.d_labitems"
        )
        _comment_on(
            con,
            "view",
            "meta.itemids",
            "Union of d_items (source = 'icu') and d_labitems (source = 'hosp') — the "
            "itemid dictionary base EP-39 curates.",
        )
    _comment_on(con, "table", "meta.tables", "Table dictionary from the EP-9 contract (EP-29).")
    _comment_on(
        con,
        "table",
        "meta.columns",
        "Column dictionary from the EP-9 contract + meta.profile aggregates (EP-29).",
    )
    _comment_on(
        con,
        "table",
        "meta.row_counts",
        "Per-table row counts: manifest-derived (no scan) and profile-derived (EP-29).",
    )


__all__ = [
    "CATALOG_SCHEMAS",
    "NEW_SUFFIX",
    "OLD_SUFFIX",
    "STAGED_SCHEMAS",
    "CatalogBuildError",
    "CatalogBuildResult",
    "CatalogSwapError",
    "CatalogTableEntry",
    "build_catalog",
    "catalog_new_path",
    "catalog_old_path",
    "qualifies",
    "swap_catalog",
    "unit_hint",
]
