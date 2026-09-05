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


def complete_for_tier(entry: dict[str, Any], tier: str) -> bool:
    """Whether a ``status.json`` step entry counts as complete for ``tier``:
    ``tier_complete == "full"`` everywhere, with ``dev``/``dev_ready`` sufficing for
    the dev tier (DESIGN §11; the EP-19 skip logic uses the same predicate)."""
    tc = entry.get("tier_complete")
    if tier == "dev":
        return tc in ("dev", "full") or bool(entry.get("dev_ready"))
    return tc == "full"


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
    payload: list[list[Any]] = []
    for line in _latest_lines(lake_root).values():
        if not line.path.startswith(f"{layer}/"):
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
    lake_root = Path(lake_root)
    status = read_status(lake_root)["steps"]
    complete = {qn for qn, entry in status.items() if complete_for_tier(entry, tier)}
    dev_buckets = set(settings.dev_buckets)
    stats: dict[str, tuple[int, int, int]] = {}
    for line in _latest_lines(lake_root).values():
        if not line.path.startswith(f"{layer}/"):
            continue
        qn = f"{line.schema_name}.{line.table}"
        if qn not in complete:
            continue
        if tier == "dev":
            bucket = _bucket_of(line.path)
            if bucket is not None and bucket not in dev_buckets:
                continue
        rows, size, files = stats.get(qn, (0, 0, 0))
        stats[qn] = (rows + line.rows, size + line.bytes, files + 1)
    return stats


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
    "SNAPSHOTS_FILENAME",
    "complete_for_tier",
    "layer_snapshot",
    "read_snapshots",
    "record_snapshot",
    "snapshots_path",
    "table_file_stats",
]
