"""Reader helpers over the partitioned lake layout (EP-18 item 4; DESIGN §5).

The **one** place the ``lake/core/<schema>/<table>/subject_bucket=<n>/part-*.parquet``
layout is encoded for readers: the catalog views (EP-21) and the tests build their
``read_parquet`` fragments here, so a layout change is a one-module edit. The glob is
pinned to ``part-*.parquet`` on purpose — pass-1 ``raw_*`` files and ``_sorting.tmp`` of
an in-progress large stage are never visible through it (DESIGN §5 note, 2026-08-28).

Distinct from :mod:`mimicwarehouse.paths` (the directory-swap publisher): this module
only formats strings; it never touches the filesystem.
"""

from __future__ import annotations

from pathlib import Path

from mimicwarehouse.loader.buckets import BUCKET_COLUMN

#: The published-files glob under one table directory (raw_* / _sorting.tmp excluded).
PARTITION_PATTERN = f"{BUCKET_COLUMN}=*/part-*.parquet"
#: Layer directory the P2 stages write under (DESIGN §3/§5).
CORE_LAYER = "core"


def table_dir(lake_root: Path | str, schema: str, table: str) -> Path:
    """``<lake_root>/core/<schema>/<table>`` — where a stage's ``dest_dir`` lives."""
    return Path(lake_root) / CORE_LAYER / schema / table


def partition_glob(lake_root: Path | str, schema: str, table: str) -> str:
    """Absolute forward-slash glob over one table's published partition files."""
    return f"{table_dir(lake_root, schema, table).resolve().as_posix()}/{PARTITION_PATTERN}"


def read_parquet_sql(
    lake_root: Path | str, schema: str, table: str, buckets: list[int] | None = None
) -> str:
    """The ``read_parquet(...)`` relation fragment over one partitioned table.

    Hive partitioning is explicit (``hive_partitioning = true``) with ``subject_bucket``
    typed INTEGER; with ``buckets`` the fragment becomes a parenthesised subquery carrying
    the partition filter (the D-18 dev tier is exactly ``buckets=settings.dev_buckets``).
    """
    glob = partition_glob(lake_root, schema, table).replace("'", "''")
    base = (
        f"read_parquet('{glob}', hive_partitioning = true, "
        f"hive_types = {{'{BUCKET_COLUMN}': INTEGER}})"
    )
    if buckets is None:
        return base
    in_list = ", ".join(str(int(b)) for b in sorted(set(buckets)))
    return f"(SELECT * FROM {base} WHERE {BUCKET_COLUMN} IN ({in_list}))"


__all__ = [
    "CORE_LAYER",
    "PARTITION_PATTERN",
    "partition_glob",
    "read_parquet_sql",
    "table_dir",
]
