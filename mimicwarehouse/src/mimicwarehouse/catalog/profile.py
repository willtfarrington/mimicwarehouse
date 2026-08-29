"""Aggregate profile step — ``lake/meta/<tier>/profile_*.parquet`` (EP-29 item 2).

:func:`profile_lake` scans the staged lake once per qualifying table (the same
``status.json`` qualification the catalog build uses) and writes two Parquet files under
``<lake_root>/meta/<tier>/``:

``profile_tables.parquet``
    one row per table: ``schema, table, row_count, build_id, snapshot_id, profiled_at``.
``profile_columns.parquet``
    one row per column: ``schema, table, column, null_pct`` (fraction in [0, 1]; NULL for
    an empty table), ``approx_distinct`` (``approx_count_distinct``), ``min_value`` /
    ``max_value`` (VARCHAR casts — numeric/timestamp columns only, and **never** for a
    column flagged ``identifier`` — those stay NULL, GOVERNANCE §4), plus the same
    provenance triple.

Everything produced is aggregate metadata — row counts, null fractions, distinct counts
and extrema of non-identifier measurement columns; no per-value frequency table is ever
computed (D-33: value-level counts are EP-44's suppressed QC territory, not this step's).
The ``dev`` tier profiles the bucket-filtered view of the shared lake
(``settings.dev_buckets``), so its numbers describe exactly what the dev catalog exposes.

Registered as the DAG ``python`` step ``meta.profile``
(``callable: mimicwarehouse.catalog.profile:run_profile``); the ``catalog`` step depends
on it so a ``--select meta.profile,catalog`` run loads fresh profiles into ``meta.*``.
The catalog build only *reads* these files — a missing profile leaves the ``null_pct`` /
``approx_distinct`` columns of ``meta.columns`` NULL, it never fails the build.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mimicwarehouse.config import (
    Settings,
    Tier,
    assert_not_credentialed_lake,
    get_settings,
)
from mimicwarehouse.dag.snapshot import layer_snapshot
from mimicwarehouse.loader.manifest import read_status, utc_now_iso
from mimicwarehouse.loader.paths import read_parquet_sql, table_dir

if TYPE_CHECKING:  # pragma: no cover
    import duckdb

    from mimicwarehouse.dag.runner import StepContext, StepOutcome
    from mimicwarehouse.dag.spec import Step
    from mimicwarehouse.schema.contract import Column, Table

_LOG = logging.getLogger(__name__)

#: The meta layer's directory for one tier (DESIGN §5: ``meta`` = catalog/profiles).
META_DIRNAME = "meta"
TABLES_FILENAME = "profile_tables.parquet"
COLUMNS_FILENAME = "profile_columns.parquet"

#: DuckDB types whose min/max are worth profiling (the contract's closed type set minus
#: VARCHAR/BOOLEAN; DECIMAL(p,s) via the prefix check below).
_MINMAX_TYPES: frozenset[str] = frozenset(
    {"INTEGER", "SMALLINT", "BIGINT", "DOUBLE", "FLOAT", "TIMESTAMP", "DATE"}
)

#: ``PermissionError`` retry policy for the Parquet publish (transient AV holds).
_RETRIES = 20
_RETRY_BASE_SLEEP_S = 0.05


class ProfileError(RuntimeError):
    """The profile step cannot run (no staged tables, DuckDB failure)."""


def meta_dir(lake_root: Path | str, tier: Tier | str) -> Path:
    """``<lake_root>/meta/<tier>/`` — where this tier's profile Parquet lives."""
    return Path(lake_root) / META_DIRNAME / str(tier)


def profile_paths(lake_root: Path | str, tier: Tier | str) -> tuple[Path, Path]:
    """``(profile_tables.parquet, profile_columns.parquet)`` for one tier."""
    root = meta_dir(lake_root, tier)
    return root / TABLES_FILENAME, root / COLUMNS_FILENAME


@dataclass(slots=True)
class ProfileResult:
    """What one :func:`profile_lake` produced (counts, ids and paths only)."""

    tier: str
    build_id: str
    snapshot_id: str
    tables_path: Path
    columns_path: Path
    tables: int = 0
    columns: int = 0
    rows: int = 0
    bytes: int = 0
    wall_s: float = 0.0


def wants_minmax(column: Column) -> bool:
    """Whether a column's min/max belong in the profile: numeric/timestamp only, and
    never an ``identifier`` column (those get NULL — GOVERNANCE §4)."""
    if column.identifier:
        return False
    return column.duckdb_type in _MINMAX_TYPES or column.duckdb_type.startswith("DECIMAL(")


def _relation_sql(table: Table, lake_root: Path, buckets: list[int] | None) -> str:
    """The ``read_parquet`` relation for one staged table (partitioned glob with the dev
    bucket filter, or the single dim file — the EP-18/EP-21 reader fragments)."""
    if table.partitioned:
        return read_parquet_sql(lake_root, table.schema_name, table.name, buckets)
    part = table_dir(lake_root, table.schema_name, table.name).resolve() / "part-0.parquet"
    return f"read_parquet('{part.as_posix()}')"


def _aggregate_sql(table: Table, relation: str) -> str:
    """One aggregate SELECT per table: ``count(*)`` plus per-column non-null count,
    ``approx_count_distinct`` and (where :func:`wants_minmax`) VARCHAR-cast min/max."""
    parts = ["count(*)"]
    for c in table.columns:
        q = f'"{c.name}"'
        parts.append(f"count({q})")
        parts.append(f"approx_count_distinct({q})")
        if wants_minmax(c):
            parts.append(f"CAST(min({q}) AS VARCHAR)")
            parts.append(f"CAST(max({q}) AS VARCHAR)")
    return f"SELECT {', '.join(parts)} FROM {relation}"


def _publish(tmp: Path, dest: Path) -> None:
    """``os.replace`` with the transient-``PermissionError`` retry loop the loader uses."""
    for attempt in range(_RETRIES):
        try:
            os.replace(tmp, dest)
            return
        except PermissionError:
            if attempt == _RETRIES - 1:
                raise
            time.sleep(_RETRY_BASE_SLEEP_S * (attempt + 1))


def _write_parquet(
    con: duckdb.DuckDBPyConnection, dest: Path, ddl_columns: str, rows: list[list[Any]]
) -> int:
    """Write ``rows`` to ``dest`` through a temp table + ``.tmp`` publish; returns bytes."""
    con.execute(f"CREATE OR REPLACE TEMP TABLE _mwh_profile ({ddl_columns})")
    try:
        placeholders = ", ".join("?" for _ in ddl_columns.split(","))
        con.executemany(f"INSERT INTO _mwh_profile VALUES ({placeholders})", rows)
        tmp = dest.with_name(dest.name + ".tmp")
        escaped = tmp.resolve().as_posix().replace("'", "''")
        con.execute(f"COPY _mwh_profile TO '{escaped}' (FORMAT PARQUET, COMPRESSION ZSTD)")
        _publish(tmp, dest)
    finally:
        con.execute("DROP TABLE IF EXISTS _mwh_profile")
    return dest.stat().st_size


def profile_lake(
    tier: Tier | str,
    settings: Settings | None = None,
    *,
    lake_root: Path | None = None,
    build_id: str | None = None,
    con: duckdb.DuckDBPyConnection | None = None,
) -> ProfileResult:
    """Profile every table complete for ``tier`` and publish the two Parquet files
    (module docstring). ``con`` is the runner's build connection when called as the
    ``meta.profile`` step; standalone callers (tests, rebuilds) get their own."""
    import duckdb

    from mimicwarehouse.catalog.build import STAGED_SCHEMAS, qualifies
    from mimicwarehouse.schema.contract import load_contract

    settings = settings or get_settings()
    lake_root = Path(lake_root) if lake_root is not None else settings.lake_root(tier)
    assert_not_credentialed_lake(tier, lake_root, settings)
    if build_id is None:
        from mimicwarehouse.dag.runner import new_build_id

        build_id = new_build_id(str(tier))

    tables_path, columns_path = profile_paths(lake_root, tier)
    tables_path.parent.mkdir(parents=True, exist_ok=True)

    status = read_status(lake_root)["steps"]
    contract = load_contract()
    buckets = list(settings.dev_buckets) if tier == "dev" else None
    snapshot_id = layer_snapshot(lake_root, "core", str(tier), settings=settings)
    profiled_at = utc_now_iso()

    result = ProfileResult(
        tier=str(tier),
        build_id=build_id,
        snapshot_id=snapshot_id,
        tables_path=tables_path,
        columns_path=columns_path,
    )
    targets = [
        table
        for schema in STAGED_SCHEMAS
        for table in contract.by_schema(schema)
        if qualifies(status.get(table.qualified_name), str(tier))
    ]
    if not targets:
        raise ProfileError(
            f"no table is complete for tier {tier!r} under {lake_root} — stage first "
            "(EP-17..EP-27), then rerun `mwh build --select meta.profile,catalog`"
        )

    own_con = con is None
    if con is None:
        from mimicwarehouse.loader.engine import open_build_connection

        con = open_build_connection(settings, tier=tier)
    t0 = time.perf_counter()
    table_rows: list[list[Any]] = []
    column_rows: list[list[Any]] = []
    try:
        for table in targets:
            t_table = time.perf_counter()
            try:
                agg = con.execute(_aggregate_sql(table, _relation_sql(table, lake_root, buckets)))
                row = agg.fetchone()
            except duckdb.Error as exc:
                raise ProfileError(f"{table.qualified_name}: profile scan failed: {exc}") from exc
            assert row is not None
            row_count = int(row[0])
            table_rows.append(
                [table.schema_name, table.name, row_count, build_id, snapshot_id, profiled_at]
            )
            i = 1
            for c in table.columns:
                non_null, distinct = int(row[i]), int(row[i + 1])
                i += 2
                min_value = max_value = None
                if wants_minmax(c):
                    min_value, max_value = row[i], row[i + 1]
                    i += 2
                null_pct = None if row_count == 0 else round(1.0 - non_null / row_count, 6)
                column_rows.append(
                    [
                        table.schema_name,
                        table.name,
                        c.name,
                        null_pct,
                        distinct,
                        min_value,
                        max_value,
                        build_id,
                        snapshot_id,
                        profiled_at,
                    ]
                )
            result.tables += 1
            result.columns += len(table.columns)
            result.rows += row_count
            _LOG.info(
                "profile %s: %s rows, %d column(s), wall=%.1fs",
                table.qualified_name,
                f"{row_count:,}",
                len(table.columns),
                time.perf_counter() - t_table,
            )
        result.bytes += _write_parquet(
            con,
            tables_path,
            '"schema" VARCHAR, "table" VARCHAR, row_count BIGINT, '
            "build_id VARCHAR, snapshot_id VARCHAR, profiled_at VARCHAR",
            table_rows,
        )
        result.bytes += _write_parquet(
            con,
            columns_path,
            '"schema" VARCHAR, "table" VARCHAR, "column" VARCHAR, null_pct DOUBLE, '
            "approx_distinct BIGINT, min_value VARCHAR, max_value VARCHAR, "
            "build_id VARCHAR, snapshot_id VARCHAR, profiled_at VARCHAR",
            column_rows,
        )
    finally:
        if own_con:
            con.close()
    result.wall_s = round(time.perf_counter() - t0, 3)
    _LOG.info(
        "profile %s: %d table(s), %d column(s), wall=%.1fs — %s",
        tier,
        result.tables,
        result.columns,
        result.wall_s,
        tables_path.parent,
    )
    return result


def run_profile(step: Step, ctx: StepContext) -> StepOutcome:
    """The ``meta.profile`` step handler body (DAG ``python`` kind, EP-29). ``rows``
    reports the summed profiled row counts — the step itself never returns a value."""
    from mimicwarehouse.dag.runner import StepOutcome

    result = profile_lake(
        ctx.tier, ctx.settings, lake_root=ctx.lake_root, build_id=ctx.build_id, con=ctx.con
    )
    return StepOutcome(rows=result.rows, bytes_out=result.bytes, files=2)


__all__ = [
    "COLUMNS_FILENAME",
    "META_DIRNAME",
    "TABLES_FILENAME",
    "ProfileError",
    "ProfileResult",
    "meta_dir",
    "profile_lake",
    "profile_paths",
    "run_profile",
    "wants_minmax",
]
