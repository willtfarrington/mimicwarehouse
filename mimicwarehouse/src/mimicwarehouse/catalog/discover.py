"""Catalog discovery of the per-tier layers (EP-37 amendment 3; carried P3C-7).

The convention every P3 spec relies on, fixed here once: a lake layer after ``core`` is
materialised **per tier** under the tier's own lake root and the ``catalog`` step registers
what is complete —

* ``<lake_root(tier)>/derived/<tier>/<schema>/<table>/part-0.parquet`` -> a
  ``<schema>.<table>`` **view** (EP-37's ``mimiciv_derived.<concept>``; EP-42's phenotypes
  land the same way) — ``lake/derived/<tier>/…`` for dev and full (``layout["lake_derived"]``;
  they share ``lake/``), ``lake/fixture/derived/fixture/…`` / ``lake/demo/derived/demo/…``
  for the synthetic tiers (EP-167: a fixture/demo build never writes into the credentialed
  tree, and every manifest path resolves under its own lake root);
* ``<lake_root(tier)>/marts/<tier>/<schema>/<table>/part-0.parquet`` -> a ``marts.*`` view
  (EP-47);
* ``<lake_root(tier)>/meta/<tier>/<table>.parquet`` -> a ``meta.<table>`` **table** (EP-29's
  ``profile_tables`` / ``profile_columns`` and EP-37's ``concept_versions`` — small,
  materialised so the catalog stays self-contained for its registry surface).

A layer table qualifies when its ``status.json`` entry (keyed ``<schema>.<table>``, with
``layer`` set by the step that wrote it) is complete for the tier under
:func:`~mimicwarehouse.dag.snapshot.complete_for_tier` **and** its file exists; an entry
whose file is missing is skipped with a warning, never registered empty. The bucketed
events spine (EP-50) is the one exception to the rule and is registered by its own
``union`` step. Registered in :data:`mimicwarehouse.catalog.build.CATALOG_EXTENSIONS`
after ``timesem.create_views``: :func:`register_layers` receives the build connection
plus the :class:`~mimicwarehouse.catalog.build.CatalogExtensionContext` (settings, lake
root, build id) and opens nothing itself. Everything created or logged is DDL over paths
and counts of objects — never a row.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mimicwarehouse.config import Settings
from mimicwarehouse.dag.snapshot import CORE_LAYER, complete_for_tier, entry_layer
from mimicwarehouse.loader.manifest import read_status
from mimicwarehouse.loader.paths import PART_FILENAME, layer_table_dir, single_file_sql

if TYPE_CHECKING:  # pragma: no cover
    import duckdb

    from mimicwarehouse.catalog.build import CatalogExtensionContext

_LOG = logging.getLogger(__name__)

#: Per-tier layers and the ``Settings.layout`` key their dev/full root carries (DESIGN §3);
#: :func:`layer_root` resolves every tier through ``Settings.lake_root``.
LAYER_ROOTS: dict[str, str] = {"derived": "lake_derived", "marts": "lake_marts"}
#: Files under ``lake/meta/<tier>/`` land in this catalog schema.
META_SCHEMA = "meta"


@dataclass(frozen=True, slots=True)
class LayerTable:
    """One complete per-tier layer table: identity, layer and its single Parquet file."""

    layer: str
    schema_name: str
    table: str
    path: Path
    comment: str | None = None

    @property
    def qualified_name(self) -> str:
        return f"{self.schema_name}.{self.table}"


def layer_root(settings: Settings, layer: str, tier: str) -> Path:
    """``<lake_root(tier)>/<layer>`` — ``layout["lake_derived"]`` / ``layout["lake_marts"]``
    for dev and full, the synthetic tiers' own lake roots otherwise (module docstring)."""
    if layer not in LAYER_ROOTS:
        raise ValueError(f"unknown per-tier layer {layer!r}; expected one of {sorted(LAYER_ROOTS)}")
    return settings.lake_root(tier) / layer


def layer_table_path(settings: Settings, layer: str, tier: str, schema: str, table: str) -> Path:
    """``<layer root>/<tier>/<schema>/<table>/part-0.parquet`` — the one file of a layer
    table (EP-37 amendment 3: single-file ZSTD Parquet, no bucket partitions)."""
    return layer_table_dir(layer_root(settings, layer, tier), tier, schema, table) / PART_FILENAME


def discover_layer_tables(settings: Settings, lake_root: Path, tier: str) -> list[LayerTable]:
    """Every per-tier layer table complete for ``tier`` whose file exists, from the lake
    root's ``status.json`` (module docstring); sorted by layer, schema, table."""
    status = read_status(lake_root)["steps"]
    found: list[LayerTable] = []
    for key, entry in sorted(status.items()):
        layer = entry_layer(entry)
        if layer == CORE_LAYER or layer not in LAYER_ROOTS:
            continue
        if not complete_for_tier(entry, tier) or "." not in key:
            continue
        schema, table = key.split(".", 1)
        path = layer_table_path(settings, layer, tier, schema, table)
        if not path.is_file():
            _LOG.warning(
                "catalog discovery: %s is complete for %s in status.json but %s is missing — "
                "skipped (rebuild it with `mwh build --tier %s --select <step> --force`)",
                key,
                tier,
                path,
                tier,
            )
            continue
        comment = entry.get("comment")
        found.append(LayerTable(layer, schema, table, path, str(comment) if comment else None))
    return found


def discover_meta_files(lake_root: Path, tier: str) -> list[tuple[str, Path]]:
    """``[(table, path), ...]`` for every ``<lake_root>/meta/<tier>/<table>.parquet``
    (``.tmp`` files of an in-progress write excluded), sorted by name."""
    from mimicwarehouse.catalog.profile import meta_dir

    root = meta_dir(lake_root, tier)
    if not root.is_dir():
        return []
    return [(p.stem, p) for p in sorted(root.glob("*.parquet")) if not p.name.endswith(".tmp")]


def _present(con: duckdb.DuckDBPyConnection, schema: str) -> set[str]:
    rows = con.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = ?", [schema]
    ).fetchall()
    return {str(r[0]) for r in rows}


def _sql_str(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def register_layers(con: duckdb.DuckDBPyConnection, tier: str, context: Any) -> None:
    """The ``CATALOG_EXTENSIONS`` entry (module docstring): on the build connection,
    create one view per complete per-tier layer table and one ``meta.<table>`` table per
    ``lake/meta/<tier>/*.parquet``. A name already present in the target schema (a
    contract table, a timesem view, an earlier extension's object) is a hard error —
    a collision is a bug, never silently shadowed."""
    from mimicwarehouse.catalog.build import CatalogBuildError

    ctx: CatalogExtensionContext = context
    settings, lake_root = ctx.settings, Path(ctx.lake_root)
    tables = discover_layer_tables(settings, lake_root, tier)
    schemas = sorted({t.schema_name for t in tables} | {META_SCHEMA})
    for schema in schemas:
        con.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
    present: dict[str, set[str]] = {schema: _present(con, schema) for schema in schemas}

    for t in tables:
        if t.table in present[t.schema_name]:
            raise CatalogBuildError(
                f"{tier} catalog: {t.qualified_name} already exists — the {t.layer} layer "
                "table collides with a contract table or an extension view"
            )
        con.execute(
            f'CREATE VIEW {t.schema_name}."{t.table}" AS SELECT * FROM {single_file_sql(t.path)}'
        )
        present[t.schema_name].add(t.table)
        comment = t.comment or (
            f"{t.layer} layer table {t.qualified_name} for tier {tier} (single-file Parquet "
            "under lake/derived; discovered by the catalog step, EP-37)"
        )
        con.execute(f'COMMENT ON VIEW {t.schema_name}."{t.table}" IS {_sql_str(comment)}')

    meta_files = discover_meta_files(lake_root, tier)
    for name, path in meta_files:
        if name in present[META_SCHEMA]:
            raise CatalogBuildError(
                f"{tier} catalog: meta.{name} already exists — lake/meta/{tier}/{path.name} "
                "collides with a registry table"
            )
        con.execute(f'CREATE TABLE meta."{name}" AS SELECT * FROM {single_file_sql(path)}')
        present[META_SCHEMA].add(name)
        con.execute(
            f'COMMENT ON TABLE meta."{name}" IS '
            + _sql_str(
                f"Loaded from lake/meta/{tier}/{path.name} by the catalog discovery walker "
                "(EP-29 profiles, EP-37 concept_versions)."
            )
        )
    _LOG.info(
        "catalog discovery %s: %d layer view(s) (%s), %d meta table(s)",
        tier,
        len(tables),
        ", ".join(sorted({t.layer for t in tables})) or "none",
        len(meta_files),
    )


__all__ = [
    "LAYER_ROOTS",
    "META_SCHEMA",
    "LayerTable",
    "discover_layer_tables",
    "discover_meta_files",
    "layer_root",
    "layer_table_path",
    "register_layers",
]
