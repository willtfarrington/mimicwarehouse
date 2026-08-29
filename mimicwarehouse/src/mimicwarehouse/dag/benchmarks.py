"""Benchmark ledger — build telemetry, append-only JSONL (EP-19 item 4; D-24).

``runs/benchmarks.jsonl``: one canonical JSON object per line, written with
``O_APPEND`` + flush so concurrent readers never see a torn line. **Nothing else
writes to this file** — the EP-19 runner appends one :class:`BenchmarkLine` per
executed step (``phase: total``; a large partitioned stage adds a ``pass1`` line —
absent when a resume skipped pass 1 — and a ``pass2`` line before it, EP-23)
plus one ``kind: build`` summary line per run; EP-28/EP-32 read it. Distinct from the
analysis run ledger (``runs/ledger.jsonl``, EP-30/EP-35).

Everything recorded is counts, versions and timings — never a row.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field

from mimicwarehouse.config import Settings, get_settings

if TYPE_CHECKING:  # pragma: no cover
    import polars

BENCHMARKS_FILENAME = "benchmarks.jsonl"

Phase = Literal["pass1", "pass2", "total"]


class HostInfo(BaseModel):
    """The host facts a benchmark line carries (cpu count, RAM GB)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    cpu: int
    ram_gb: float


class BenchmarkLine(BaseModel):
    """One ledger line: a step's telemetry, or the per-run ``kind: build`` summary."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ts: str
    build_id: str
    tier: str
    step: str | None = Field(default=None, description="None on the kind: build summary line")
    kind: str
    phase: Phase = "total"
    wall_s: float
    peak_rss_mb: float | None = None
    rows: int | None = None
    bytes_in: int | None = None
    bytes_out: int | None = None
    files: int | None = None
    duckdb_version: str
    git_sha: str | None = None
    host: HostInfo
    ok: bool
    error: str | None = None


def host_info() -> HostInfo:
    """Current host facts via psutil (the EP-19 core dependency)."""
    import psutil

    return HostInfo(
        cpu=psutil.cpu_count(logical=True) or 0,
        ram_gb=round(psutil.virtual_memory().total / 2**30, 1),
    )


def benchmarks_path(settings: Settings | None = None) -> Path:
    settings = settings or get_settings()
    return settings.layout["runs"] / BENCHMARKS_FILENAME


def append(line: BenchmarkLine, settings: Settings | None = None) -> Path:
    """Append one line (``O_APPEND`` + flush; one canonical JSON object per line)."""
    path = benchmarks_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    blob = (json.dumps(line.model_dump(mode="json"), sort_keys=True) + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, blob)
        os.fsync(fd)
    finally:
        os.close(fd)
    return path


def read(settings: Settings | None = None) -> polars.DataFrame:
    """The whole ledger as a polars DataFrame (empty frame when the file is missing)."""
    import polars

    path = benchmarks_path(settings)
    if not path.is_file():
        return polars.DataFrame()
    return polars.read_ndjson(path)


__all__ = [
    "BENCHMARKS_FILENAME",
    "BenchmarkLine",
    "HostInfo",
    "append",
    "benchmarks_path",
    "host_info",
    "read",
]
