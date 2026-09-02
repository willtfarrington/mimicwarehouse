"""Append-only JSONL ledgers and atomic text writes — the one file-I/O canon (EP-33 B8;
D-24, D-42; ledger findings LGR-1/LGR-2/LGR-4).

Every ledger this project keeps (``runs/audit.jsonl``, ``runs/benchmarks.jsonl``, the lake's
``manifests/<build_id>.jsonl``) is written through :func:`append_jsonl` /
:func:`append_jsonl_lines`: one canonical JSON object per line (``sort_keys=True``, UTF-8,
``\\n``), a single ``os.write`` per line on an ``O_APPEND`` descriptor, the returned byte
count **checked** (a short write on a full disk raises :class:`ShortWriteError` instead of
leaving a truncated line the next append would merge into), then ``os.fsync``.

Readers go through :func:`read_jsonl` / :func:`iter_jsonl`, which tolerate exactly **one**
malformed *trailing* line (a crash or ``ENOSPC`` mid-write tears the last line; the file is
append-only so the tear is permanent) — they warn and skip it. A malformed line anywhere
else is corruption and raises.

:func:`atomic_write_text` is the temp-file + ``os.replace`` write (promoted from
``inventory._atomic_write_text``, which stays as an alias) with the Windows
``PermissionError`` retry: a reader (``mwh inventory show``) holds the target for a few
milliseconds. The temp sibling is removed when the replace finally gives up.

Stdlib only — this module sits under ``safe.py`` and ``dag/benchmarks.py`` and must not add
to the ``mwh --help`` import budget (DESIGN §15).
"""

from __future__ import annotations

import json
import os
import time
import warnings
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path
from typing import Any

#: ``PermissionError`` retry policy shared with :mod:`mimicwarehouse.publish`.
RETRIES = 20
RETRY_BASE_SLEEP_S = 0.05


class ShortWriteError(OSError):
    """``os.write`` returned fewer bytes than the line holds (disk full, interrupted)."""


class TornLedgerError(ValueError):
    """A JSONL ledger has a malformed line that is not the trailing one (corruption)."""


def encode_line(payload: Mapping[str, Any]) -> bytes:
    """The canonical on-disk form of one ledger line."""
    return (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8")


def _write_all(fd: int, blob: bytes, path: Path) -> None:
    written = os.write(fd, blob)
    if written != len(blob):
        raise ShortWriteError(
            f"{path}: short write ({written} of {len(blob)} bytes) — the ledger line may be "
            "torn; check free space"
        )


def append_jsonl(path: Path, payload: Mapping[str, Any]) -> Path:
    """Append one canonical JSON line to ``path`` (parents created; ``O_APPEND`` + fsync)."""
    return append_jsonl_lines(path, (payload,))


def append_jsonl_lines(path: Path, payloads: Iterable[Mapping[str, Any]]) -> Path:
    """Append several lines: one ``os.write`` per line, one ``fsync`` at the end."""
    path = Path(path)
    blobs = [encode_line(p) for p in payloads]
    path.parent.mkdir(parents=True, exist_ok=True)
    # O_BINARY: without it Windows' CRT translates the "\n" to "\r\n" (the pre-EP-33 writers
    # did; JSON readers tolerate the stray "\r", but the canon writes exactly what it says).
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_BINARY", 0)
    fd = os.open(path, flags, 0o644)
    try:
        for blob in blobs:
            _write_all(fd, blob, path)
        os.fsync(fd)
    finally:
        os.close(fd)
    return path


def iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    """Yield the parsed objects of a JSONL ledger; a malformed **trailing** line is skipped
    with a :class:`UserWarning` (LGR-1), any other malformed line raises
    :class:`TornLedgerError`. A missing file yields nothing."""
    path = Path(path)
    if not path.is_file():
        return
    with path.open("rb") as f:
        raw = f.read()
    lines = raw.split(b"\n")
    if lines and lines[-1] == b"":
        lines.pop()  # the terminating newline
    last = len(lines) - 1
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            obj = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            if i == last:
                warnings.warn(
                    f"{path}: ignoring a torn trailing line ({exc.__class__.__name__}); the "
                    "last append was interrupted",
                    stacklevel=2,
                )
                return
            raise TornLedgerError(f"{path}: malformed line {i + 1} of {last + 1}") from exc
        if not isinstance(obj, dict):
            raise TornLedgerError(f"{path}: line {i + 1} is not a JSON object")
        yield obj


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """:func:`iter_jsonl` as a list."""
    return list(iter_jsonl(path))


def atomic_write_text(path: Path, text: str, *, retries: int = RETRIES) -> None:
    """Write ``path`` via ``<name>.tmp`` + ``os.replace``, retrying the replace on the
    transient Windows ``PermissionError``; the temp file is removed if the replace
    ultimately fails (carried finding WIN-7)."""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    for attempt in range(retries):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == retries - 1:
                tmp.unlink(missing_ok=True)
                raise
            time.sleep(RETRY_BASE_SLEEP_S * (attempt + 1))


__all__ = [
    "RETRIES",
    "RETRY_BASE_SLEEP_S",
    "ShortWriteError",
    "TornLedgerError",
    "append_jsonl",
    "append_jsonl_lines",
    "atomic_write_text",
    "encode_line",
    "iter_jsonl",
    "read_jsonl",
]
