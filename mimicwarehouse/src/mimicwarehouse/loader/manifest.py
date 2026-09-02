"""Lake manifest lines and the staging status file (EP-17 item 4; DESIGN §5/§11, D-26).

One :class:`ManifestLine` per Parquet file, appended to
``<lake_root>/manifests/<build_id>.jsonl``; the layer **snapshot id** (EP-19) is derived
from these lines. Provenance is the **pair** settled at the retro (D-43 item 11, DESIGN
§11 glossary): per-file ``source_sha256`` (what the EP-10 raw manifest recorded for the
source CSV; ``None`` on the fixture tier) **and** the 41-file ``raw_snapshot_id`` — the
planning-era single ``source_manifest_id`` is superseded. The per-file Parquet ``sha256``
is integrity-only, never part of a snapshot id.

``status.json`` (``<lake_root>/manifests/status.json``) tracks one entry per
``<schema>.<table>`` under ``{"steps": {...}}`` — the shape the EP-168 ``dev_ready``
test fixture reads — written atomically (:func:`mimicwarehouse.fsio.atomic_write_text`:
``.tmp`` + ``os.replace`` with the Windows retry loop).

The manifest jsonl is written through :func:`mimicwarehouse.fsio.append_jsonl_lines`
(EP-33 B8: ``O_APPEND`` + short-write check + fsync — the resume-critical ledger no
longer has a buffered-write torn-line window) and read through
:func:`mimicwarehouse.fsio.iter_jsonl` (:func:`iter_manifest`), which skips one torn
trailing line with a warning (LGR-1/LDR-5).

Nothing here opens a source file: hashes are streamed from the Parquet the loader itself
wrote, row counts come from Parquet metadata.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from mimicwarehouse import __version__, fsio
from mimicwarehouse.schema.contract import Table

MANIFESTS_DIRNAME = "manifests"
STATUS_FILENAME = "status.json"
#: sha256 streaming chunk size (8 MB).
HASH_CHUNK_BYTES = 8 * 2**20
#: Fields every ``status.json`` step entry carries (EP-17; EP-18/19 fill the tier fields).
STATUS_DEFAULTS: dict[str, Any] = {"tier_complete": None, "dev_ready": False}


class ManifestLine(BaseModel):
    """One staged Parquet file: identity, integrity, provenance. Never a value."""

    model_config = ConfigDict(frozen=True, extra="forbid", validate_by_name=True)

    schema_name: str = Field(alias="schema", description="contract schema, e.g. mimiciv_hosp")
    table: str
    path: str = Field(description="forward-slash path relative to the lake root")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$", description="integrity-only (DESIGN §11)")
    bytes: int = Field(ge=0)
    rows: int = Field(ge=0)
    schema_hash: str = Field(
        pattern=r"^[0-9a-f]{64}$", description="sha256 of the contract's ordered (name, type) list"
    )
    writer_version: str = Field(description="package version + DuckDB version")
    source_sha256: str | None = Field(
        default=None, description="EP-10 raw-manifest sha256 of the source CSV; None on fixture"
    )
    raw_snapshot_id: str | None = Field(
        default=None, description="41-file raw snapshot id (EP-10); None until complete / fixture"
    )
    map_notes: dict[str, list[str]] | None = Field(
        default=None,
        description="columns a column map could not carry losslessly (EP-22: filled_null / "
        "dropped lists, names only); None without a map or when the map is the identity — "
        "expected None on the demo tier while demo_2_2 stays the identity",
    )
    build_id: str
    ts: str = Field(description="ISO 8601 UTC")


def table_schema_hash(table: Table) -> str:
    """sha256 of the contract's ordered ``(name, type)`` list for one table."""
    payload = [[c.name, c.duckdb_type] for c in table.columns]
    blob = json.dumps(payload, separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()


def writer_version() -> str:
    """``mimicwarehouse <version> / duckdb <version>`` — recorded in every manifest line."""
    import duckdb

    return f"mimicwarehouse {__version__} / duckdb {duckdb.__version__}"


def sha256_streamed(path: Path) -> str:
    """Streaming sha256 in 8 MB chunks (the staged Parquet may be tens of GB)."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as f:
        while chunk := f.read(HASH_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def lake_relative_posix(path: Path, lake_root: Path) -> str:
    """``path`` relative to the lake root, forward slashes (the manifest ``path`` field)."""
    rel = Path(path).resolve().relative_to(Path(lake_root).resolve())
    return str(PurePosixPath(*rel.parts))


def manifests_dir(lake_root: Path) -> Path:
    return Path(lake_root) / MANIFESTS_DIRNAME


def manifest_path(lake_root: Path, build_id: str) -> Path:
    return manifests_dir(lake_root) / f"{build_id}.jsonl"


def append_manifest(lake_root: Path, build_id: str, lines: list[ManifestLine]) -> Path:
    """Append ``lines`` to ``<lake_root>/manifests/<build_id>.jsonl`` (one canonical JSON
    object per line, append-only — a build appends as tables finish; ``O_APPEND`` +
    fsync via :func:`fsio.append_jsonl_lines`)."""
    return fsio.append_jsonl_lines(
        manifest_path(lake_root, build_id),
        (line.model_dump(mode="json", by_alias=True) for line in lines),
    )


def iter_manifest(path: Path) -> Iterator[ManifestLine]:
    """The :class:`ManifestLine` objects of one manifest jsonl, tolerant of a torn
    trailing line (:func:`fsio.iter_jsonl`); a missing file yields nothing."""
    for obj in fsio.iter_jsonl(path):
        yield ManifestLine.model_validate(obj)


def status_path(lake_root: Path) -> Path:
    return manifests_dir(lake_root) / STATUS_FILENAME


def read_status(lake_root: Path) -> dict[str, Any]:
    """The parsed ``status.json`` (``{"steps": {}}`` when missing)."""
    path = status_path(lake_root)
    if not path.is_file():
        return {"steps": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    data.setdefault("steps", {})
    return data


def update_status(lake_root: Path, step: str, **fields: Any) -> Path:
    """Merge ``fields`` into ``status.json``'s entry for ``step`` (``<schema>.<table>``),
    atomically (``.tmp`` + ``os.replace`` retry loop). New entries start from
    :data:`STATUS_DEFAULTS` so ``tier_complete`` / ``dev_ready`` are always present."""
    data = read_status(lake_root)
    entry = data["steps"].setdefault(step, dict(STATUS_DEFAULTS))
    for key in STATUS_DEFAULTS:
        entry.setdefault(key, STATUS_DEFAULTS[key])
    entry.update(fields)
    path = status_path(lake_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    fsio.atomic_write_text(path, json.dumps(data, indent=2, sort_keys=True) + "\n")
    return path


__all__ = [
    "HASH_CHUNK_BYTES",
    "MANIFESTS_DIRNAME",
    "STATUS_FILENAME",
    "ManifestLine",
    "append_manifest",
    "iter_manifest",
    "lake_relative_posix",
    "manifest_path",
    "manifests_dir",
    "read_status",
    "sha256_streamed",
    "status_path",
    "table_schema_hash",
    "update_status",
    "utc_now_iso",
    "writer_version",
]
