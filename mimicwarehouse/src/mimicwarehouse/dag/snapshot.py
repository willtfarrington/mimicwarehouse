"""Layer snapshot ids (EP-19 item 3; DESIGN §11 glossary, D-43 item 11).

A layer snapshot id is **logical**: sha256 over the sorted canonical JSON of
``(schema, table, path, rows, schema_hash, source_sha256 or raw_snapshot_id,
sort_keys, writer_version)`` per published Parquet file — never the per-file Parquet
``sha256``, which is integrity-only (file bytes need not be reproducible under
non-total sort orders). It is therefore *stable when raw + contract + code are
unchanged*, and two identical rebuilds agree.

Scope rules:

* only tables whose ``status.json`` entry is **complete for the tier** count
  (:func:`complete_for_tier`; ``dev_ready`` suffices for ``dev``);
* the ``dev`` id hashes only manifest lines whose path lies in
  ``settings.dev_buckets`` plus unpartitioned tables — it must **not** move when
  buckets 5-99 finish during a full pass;
* a table restaged across builds contributes its **latest** line per path (newest
  ``ts`` wins across every ``<lake_root>/manifests/*.jsonl``).

**Per-tier layers (EP-37).** The ``core`` layer is one bucketed file set shared by
``dev`` and ``full``, so *full-complete implies dev-complete*. Every later layer
(``derived`` from EP-37, ``marts`` from EP-47) is materialised **per tier** under
``<lake_root(tier)>/<layer>/<tier>/<schema>/<table>/part-0.parquet`` and its manifest lines
carry that tier segment (``derived/dev/…`` and ``derived/full/…`` side by side under the
shared ``lake/``; ``derived/fixture/…`` inside the fixture tier's own lake root, so every
manifest path still resolves under the root it is recorded in). Two consequences,
both keyed on the entry's ``layer`` field (``"core"`` when absent): the ``dev`` predicate
requires ``dev_ready`` / ``tier_complete = "dev"`` (a full derived file is not a dev one),
and a layer snapshot only hashes lines under ``<layer>/<tier>/``. A derived line's
``source_sha256`` is the concept SQL's sha256 and its ``raw_snapshot_id`` the core snapshot
it read — the same provenance pair, one level up.

Every build appends ``{layer, tier, snapshot_id, build_id, ts}`` to
``lake/manifests/snapshots.json`` (a history list); EP-21 stores the id in
``meta.catalog_info`` and EP-35 cites it in run manifests.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from mimicwarehouse import fsio
from mimicwarehouse.config import Settings, get_settings
from mimicwarehouse.loader.buckets import BUCKET_COLUMN
from mimicwarehouse.loader.manifest import (
    ManifestLine,
    manifests_dir,
    read_status,
    utc_now_iso,
)

SNAPSHOTS_FILENAME = "snapshots.json"
#: The layer whose files are shared by ``dev`` and ``full`` (bucketed); every other layer
#: is materialised per tier (module docstring, EP-37).
CORE_LAYER = "core"


def entry_layer(entry: dict[str, Any]) -> str:
    """The lake layer a ``status.json`` entry belongs to (``"core"`` when unrecorded)."""
    return str(entry.get("layer") or CORE_LAYER)


def complete_for_tier(entry: dict[str, Any], tier: str) -> bool:
    """Whether a ``status.json`` step entry counts as complete for ``tier``.

    ``core`` entries: ``tier_complete == "full"`` everywhere, with ``dev``/``dev_ready``
    sufficing for the dev tier (DESIGN §11; the EP-19 skip logic uses the same predicate).
    Per-tier layers (``layer`` field set, EP-37): the dev tier needs its **own** file —
    ``dev_ready`` or ``tier_complete == "dev"`` — because a full derived table is a
    different file; every other tier still needs ``tier_complete == "full"`` (fixture and
    demo write it on their own lake roots, as their stages do).
    """
    tc = entry.get("tier_complete")
    if tier == "dev":
        if entry_layer(entry) == CORE_LAYER:
            return tc in ("dev", "full") or bool(entry.get("dev_ready"))
        return tc == "dev" or bool(entry.get("dev_ready"))
    return tc == "full"


def layer_path_prefix(layer: str, tier: str) -> str:
    """The manifest-path prefix of ``layer``'s files for ``tier``: ``core/`` (shared) or
    ``<layer>/<tier>/`` (per-tier layers, EP-37)."""
    return f"{layer}/" if layer == CORE_LAYER else f"{layer}/{tier}/"


def _bucket_of(path: str) -> int | None:
    """The ``subject_bucket=<n>`` value in a manifest path, or None (unpartitioned)."""
    marker = f"{BUCKET_COLUMN}="
    for part in path.split("/"):
        if part.startswith(marker):
            try:
                return int(part[len(marker) :])
            except ValueError:
                return None
    return None


def _latest_lines(lake_root: Path) -> dict[str, ManifestLine]:
    """Newest manifest line per lake-relative path, across every build's jsonl (read
    through :func:`fsio.iter_jsonl`, so one torn trailing line — a crash mid-append — is
    skipped with a warning instead of breaking every snapshot id; LGR-1/LDR-5)."""
    latest: dict[str, ManifestLine] = {}
    mdir = manifests_dir(lake_root)
    if not mdir.is_dir():
        return latest
    for jsonl in sorted(mdir.glob("*.jsonl")):
        for obj in fsio.iter_jsonl(jsonl):
            line = ManifestLine.model_validate(obj)
            have = latest.get(line.path)
            if have is None or line.ts >= have.ts:
                latest[line.path] = line
    return latest


def layer_snapshot(
    lake_root: Path,
    layer: str = "core",
    tier: str = "full",
    *,
    settings: Settings | None = None,
) -> str:
    """The logical snapshot id of one layer for one tier (module docstring)."""
    settings = settings or get_settings()
    lake_root = Path(lake_root)
    status = read_status(lake_root)["steps"]
    complete = {qn for qn, entry in status.items() if complete_for_tier(entry, tier)}
    dev_buckets = set(settings.dev_buckets)

    from mimicwarehouse.schema.contract import load_contract

    contract = load_contract()
    prefix = layer_path_prefix(layer, tier)
    payload: list[list[Any]] = []
    for line in _latest_lines(lake_root).values():
        if not line.path.startswith(prefix):
            continue
        qn = f"{line.schema_name}.{line.table}"
        if qn not in complete:
            continue
        if tier == "dev":
            bucket = _bucket_of(line.path)
            if bucket is not None and bucket not in dev_buckets:
                continue
        sort_keys = list(contract.table(qn).sort_keys) if contract.has_table(qn) else []
        payload.append(
            [
                line.schema_name,
                line.table,
                line.path,
                line.rows,
                line.schema_hash,
                line.source_sha256 or line.raw_snapshot_id,
                sort_keys,
                line.writer_version,
            ]
        )
    payload.sort()
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()


def table_file_stats(
    lake_root: Path,
    tier: str = "full",
    *,
    layer: str = "core",
    settings: Settings | None = None,
) -> dict[str, tuple[int, int, int]]:
    """``{schema.table: (rows, bytes, files)}`` summed from the latest manifest line per
    published path — the no-scan row-count source of ``meta.tables`` / ``meta.row_counts``
    (EP-29 item 3). Scope rules are exactly :func:`layer_snapshot`'s: only tables complete
    for ``tier`` count, and the ``dev`` numbers keep dev-bucket lines plus unpartitioned
    files, so they describe what the dev catalog exposes."""
    settings = settings or get_settings()
    stats: dict[str, tuple[int, int, int]] = {}
    for qn, line in layer_lines(lake_root, tier, layer=layer, settings=settings):
        rows, size, files = stats.get(qn, (0, 0, 0))
        stats[qn] = (rows + line.rows, size + line.bytes, files + 1)
    return stats


def layer_lines(
    lake_root: Path,
    tier: str = "full",
    *,
    layer: str = "core",
    settings: Settings | None = None,
) -> list[tuple[str, ManifestLine]]:
    """``[(schema.table, latest manifest line), ...]`` of the files ``layer`` exposes for
    ``tier`` — the scope rules of :func:`layer_snapshot` (complete for the tier, the
    layer's per-tier path prefix, dev-bucket lines only on ``dev``), sorted by path. What
    :func:`table_file_stats` sums and what EP-37's ``meta.concept_versions`` reads for a
    derived table's rows / bytes / ``ts`` / ``build_id``."""
    settings = settings or get_settings()
    lake_root = Path(lake_root)
    status = read_status(lake_root)["steps"]
    complete = {qn for qn, entry in status.items() if complete_for_tier(entry, tier)}
    dev_buckets = set(settings.dev_buckets)
    prefix = layer_path_prefix(layer, tier)
    out: list[tuple[str, ManifestLine]] = []
    for path, line in sorted(_latest_lines(lake_root).items()):
        if not path.startswith(prefix):
            continue
        qn = f"{line.schema_name}.{line.table}"
        if qn not in complete:
            continue
        if tier == "dev":
            bucket = _bucket_of(path)
            if bucket is not None and bucket not in dev_buckets:
                continue
        out.append((qn, line))
    return out


# ---------------------------------------------------------------------------
# History (lake/manifests/snapshots.json)
# ---------------------------------------------------------------------------


def snapshots_path(lake_root: Path) -> Path:
    return manifests_dir(lake_root) / SNAPSHOTS_FILENAME


def read_snapshots(lake_root: Path) -> list[dict[str, Any]]:
    path = snapshots_path(lake_root)
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else []


def record_snapshot(
    lake_root: Path, *, layer: str, tier: str, snapshot_id: str, build_id: str
) -> dict[str, Any]:
    """Append one history entry to ``snapshots.json`` (atomic write) and return it."""
    entry = {
        "layer": layer,
        "tier": tier,
        "snapshot_id": snapshot_id,
        "build_id": build_id,
        "ts": utc_now_iso(),
    }
    history = read_snapshots(lake_root)
    history.append(entry)
    path = snapshots_path(lake_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    fsio.atomic_write_text(path, json.dumps(history, indent=2, sort_keys=True) + "\n")
    return entry


__all__ = [
    "CORE_LAYER",
    "SNAPSHOTS_FILENAME",
    "complete_for_tier",
    "entry_layer",
    "layer_lines",
    "layer_path_prefix",
    "layer_snapshot",
    "read_snapshots",
    "record_snapshot",
    "snapshots_path",
    "table_file_stats",
]
