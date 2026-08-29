"""Unpartitioned stage: one typed CSV → one sorted Parquet file (EP-17 item 3/5).

The path for dimension tables (``d_*``, ``provider``, ``caregiver`` — every
``load_class: small`` table without a bucket layout); EP-18 adds the Hive-partitioned
path and reuses everything here.

Write protocol (DESIGN §5): stage into ``<dest>.new/part-0.parquet`` with
``COPY (SELECT <contract cols> FROM read_csv(...) ORDER BY <contract sort_keys>)``
(zstd level 3, 1 M-row row groups, ``preserve_insertion_order = true`` for this one
statement — small tables only), then publish with :func:`mimicwarehouse.paths.swap_dir`
(rename-aside two-step; crash-safe, not atomic — readers must be closed). The sort keys
carry same-table tie-breaks since EP-169, so two stagings of the same file are
byte-identical (the determinism test pins their sha256).

Rejects (item 5): the ``store_rejects`` temp tables are row-level and stay inside the
connection; their rows are copied to ``<lake_root>/rejects/<schema>/<table>/<build_id>.parquet``
(data root only — never printed, never committed) and everything the caller sees carries
only the reject **count**. The stage raises :class:`RejectThresholdError` when the count
exceeds ``settings.loader_reject_max`` (default 0: any reject on the credentialed datasets
is a contract bug to fix in the EP-9 YAML with a dated note, not to tolerate) — the
``.new`` directory is discarded and ``dest`` is left untouched.
"""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from mimicwarehouse import paths
from mimicwarehouse.config import Settings, get_settings
from mimicwarehouse.loader.csv import plan_csv_read, plan_map_notes
from mimicwarehouse.loader.manifest import (
    ManifestLine,
    append_manifest,
    lake_relative_posix,
    sha256_streamed,
    table_schema_hash,
    update_status,
    utc_now_iso,
    writer_version,
)
from mimicwarehouse.schema.contract import ColumnMap, Table

if TYPE_CHECKING:  # pragma: no cover
    import duckdb

PART_FILENAME = "part-0.parquet"
REJECTS_DIRNAME = "rejects"
#: Parquet write options (DESIGN §5): zstd 3, ~1 M-row row groups, statistics on (default).
COPY_OPTIONS = "FORMAT parquet, COMPRESSION zstd, COMPRESSION_LEVEL 3, ROW_GROUP_SIZE 1000000"


class StageError(RuntimeError):
    """The stage cannot run (bad destination, missing sort keys, …)."""


class RejectThresholdError(RuntimeError):
    """More rows were rejected than ``settings.loader_reject_max`` allows. Carries counts
    only — the rejected rows themselves are on the data root, never in the message."""

    def __init__(self, table: str, rejects: int, allowed: int, rejects_path: Path) -> None:
        self.table, self.rejects, self.allowed = table, rejects, allowed
        self.rejects_path = rejects_path
        super().__init__(
            f"{table}: {rejects} rejected row(s) > loader_reject_max={allowed} — staging "
            f"refused; row-level rejects at {rejects_path} (data root only). A reject on the "
            "credentialed datasets is a contract bug: fix the EP-9 YAML with a dated note."
        )


@dataclass(frozen=True, slots=True)
class StageResult:
    """What one unpartitioned stage produced (counts and manifest lines only)."""

    rows: int
    bytes: int
    files: int
    rejects: int
    wall_s: float
    manifest_lines: tuple[ManifestLine, ...]


def rejects_parquet_path(lake_root: Path, table: Table, build_id: str) -> Path:
    return (
        Path(lake_root) / REJECTS_DIRNAME / table.schema_name / table.name / f"{build_id}.parquet"
    )


def _sql_str(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def _reject_count(con: duckdb.DuckDBPyConnection, rejects_table: str) -> int:
    import duckdb

    try:
        row = con.execute(f"SELECT count(*) FROM {rejects_table}").fetchone()
    except duckdb.CatalogException:  # no reject table = the scan stored nothing
        return 0
    return int(row[0]) if row else 0


def stage_unpartitioned(
    con: duckdb.DuckDBPyConnection,
    table_spec: Table,
    source: Path,
    dest_dir: Path,
    *,
    lake_root: Path,
    build_id: str,
    settings: Settings | None = None,
    source_sha256: str | None = None,
    raw_snapshot_id: str | None = None,
    column_map: ColumnMap | None = None,
) -> StageResult:
    """Stage ``source`` as ``dest_dir/part-0.parquet`` (module docstring) and record it:
    appends the manifest line to ``<lake_root>/manifests/<build_id>.jsonl`` and merges the
    step entry into ``status.json``. ``dest_dir`` must lie under ``lake_root``."""
    t0 = time.perf_counter()
    settings = settings or get_settings()
    source, dest_dir, lake_root = Path(source), Path(dest_dir), Path(lake_root)
    try:
        dest_dir.resolve().relative_to(lake_root.resolve())
    except ValueError:
        raise StageError(f"{dest_dir} is not under the lake root {lake_root}") from None
    if not table_spec.sort_keys:
        raise StageError(
            f"{table_spec.qualified_name}: contract sort_keys are empty — the deterministic "
            "ORDER BY is undefined (EP-169 gave every staged table tie-broken sort keys)"
        )

    plan = plan_csv_read(source, table_spec, column_map)
    map_notes = plan_map_notes(table_spec, plan) if column_map is not None else None
    new_dir = paths.new_dir_for(dest_dir)
    if new_dir.exists():  # a stale .new from a crashed stage
        shutil.rmtree(new_dir)
    new_dir.mkdir(parents=True, exist_ok=True)
    part = new_dir / PART_FILENAME

    con.execute(f"DROP TABLE IF EXISTS {plan.rejects_table}")
    con.execute(f"DROP TABLE IF EXISTS {plan.rejects_scan}")
    copy_sql = (
        f"COPY (SELECT {', '.join(plan.select_exprs)} FROM {plan.relation_sql} "
        f"ORDER BY {', '.join(table_spec.sort_keys)}) "
        f"TO {_sql_str(str(part))} ({COPY_OPTIONS})"
    )
    con.execute("SET preserve_insertion_order = true")  # the ORDER BY must survive the COPY
    try:
        con.execute(copy_sql)
        rejects = _reject_count(con, plan.rejects_table)
        rejects_path = rejects_parquet_path(lake_root, table_spec, build_id)
        if rejects:
            rejects_path.parent.mkdir(parents=True, exist_ok=True)
            con.execute(
                f"COPY (SELECT * FROM {plan.rejects_table}) "
                f"TO {_sql_str(str(rejects_path))} ({COPY_OPTIONS})"
            )
        if rejects > settings.loader_reject_max:
            shutil.rmtree(new_dir, ignore_errors=True)
            raise RejectThresholdError(
                table_spec.qualified_name, rejects, settings.loader_reject_max, rejects_path
            )
    finally:
        con.execute("SET preserve_insertion_order = false")

    (rows,) = con.execute(
        f"SELECT num_rows FROM parquet_file_metadata({_sql_str(str(part))})"
    ).fetchone()  # type: ignore[misc]
    paths.swap_dir(new_dir, dest_dir)

    published = dest_dir / PART_FILENAME
    line = ManifestLine(
        schema=table_spec.schema_name,
        table=table_spec.name,
        path=lake_relative_posix(published, lake_root),
        sha256=sha256_streamed(published),
        bytes=published.stat().st_size,
        rows=int(rows),
        schema_hash=table_schema_hash(table_spec),
        writer_version=writer_version(),
        source_sha256=source_sha256,
        raw_snapshot_id=raw_snapshot_id,
        map_notes=map_notes,
        build_id=build_id,
        ts=utc_now_iso(),
    )
    append_manifest(lake_root, build_id, [line])
    wall_s = time.perf_counter() - t0
    status_fields: dict[str, object] = {
        "build_id": build_id,
        "rows": line.rows,
        "bytes": line.bytes,
        "files": 1,
        "rejects": rejects,
        "finished_at": line.ts,
    }
    if map_notes is not None:
        status_fields["map_notes"] = map_notes
    update_status(lake_root, table_spec.qualified_name, **status_fields)
    return StageResult(
        rows=line.rows,
        bytes=line.bytes,
        files=1,
        rejects=rejects,
        wall_s=wall_s,
        manifest_lines=(line,),
    )


__all__ = [
    "COPY_OPTIONS",
    "PART_FILENAME",
    "REJECTS_DIRNAME",
    "RejectThresholdError",
    "StageError",
    "StageResult",
    "rejects_parquet_path",
    "stage_unpartitioned",
]
