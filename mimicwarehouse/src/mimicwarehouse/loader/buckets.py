"""Partitioned stage: typed CSV → Hive subject buckets, sorted, resumable (EP-18; DESIGN §5).

The physical layout every subject-keyed table uses:
``<dest>/subject_bucket=<n>/part-0.parquet`` with ``subject_bucket = subject_id % 100``
(un-padded DuckDB partition names, partition column **not** written into the files), rows
sorted ``(subject_id, <sort_by>)``, ZSTD-3, ~1 M-row row groups. Two paths, one publish
protocol (every ``.new`` → ``dest`` goes through :func:`mimicwarehouse.paths.swap_dir`,
rename-aside two-step — ``os.replace`` cannot replace an existing directory on Windows):

* **small** (CSV ≤ ~1 GB): one ``COPY … PARTITION_BY`` with a global
  ``ORDER BY subject_bucket, subject_id, <sort_by>`` under
  ``preserve_insertion_order = true``, so each partition file lands sorted; then swap.
* **large** (contract ``load_class: large``): pass 1 streams the same partitioned ``COPY``
  with ``preserve_insertion_order = false`` and **no** ``ORDER BY`` into ``raw_*`` files
  (``sweeps=N`` splits pass 1 into N sequential bucket-range statements — the memory
  fallback for EP-26); swap; pass 2 sorts one bucket at a time (dev buckets first) into
  ``_sorting.tmp`` → ``part-0.parquet``, appending a manifest line per finished bucket.

**Resume** (large path): ``<dest>/_progress.json`` (written into ``<dest>.new`` during
pass 1, so the swap publishes data and progress together). ``pass1_done`` false → pass 1
is redone into a fresh ``.new`` (partial output discarded); already-sorted buckets are
skipped; a stale ``_sorting.tmp`` is deleted. The per-bucket order of operations is
deliberately *publish before delete*: ``os.replace(_sorting.tmp → part-0.parquet)``,
record the bucket in ``_progress.json``, **then** delete the ``raw_*`` files — a crash at
any point leaves either the raws (bucket re-sorts) or a recorded sorted bucket (raws are
swept on resume), never a bucket with no complete source. Progress resumes only when the
recorded ``buckets_requested`` equal the new request and ``complete`` is false; anything
else (e.g. a **full** request over a ``tier_complete = "dev"`` table) restages the whole
table.

When the dev buckets (``settings.dev_buckets``, D-43 item 14 — no separate constant) are
all sorted the stage logs ``dev-ready <schema>.<table>`` and sets
``update_status(dev_ready=True)`` *before* the final ``complete`` log /
``update_status(tier_complete=…)``, so EP-21's dev catalog can attach early. Everything
logged or returned is counts, hashes, paths and timings — never a value; the module logger
also emits one line per sorted bucket and, every ``heartbeat_s`` during pass 1, rss /
tmp-dir / bytes-written counts.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
import threading
import time
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from mimicwarehouse import paths
from mimicwarehouse.config import Settings, dir_size_bytes, get_settings
from mimicwarehouse.loader.csv import plan_csv_read
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
from mimicwarehouse.loader.stage import (
    COPY_OPTIONS,
    PART_FILENAME,
    RejectThresholdError,
    StageError,
    StageResult,
    _reject_count,
    _sql_str,
    rejects_parquet_path,
)
from mimicwarehouse.schema.contract import SUBJECT_KEY, ColumnMap, Table

if TYPE_CHECKING:  # pragma: no cover
    import duckdb

_LOG = logging.getLogger(__name__)

#: ``subject_bucket = subject_id % NUM_BUCKETS`` (DESIGN §4/§5; re-measured at EP-28).
NUM_BUCKETS = 100
BUCKET_COLUMN = "subject_bucket"
#: Pass-1 output files (several per partition are fine; never visible to readers — the
#: catalog glob is pinned to ``part-*.parquet``, :mod:`mimicwarehouse.loader.paths`).
RAW_GLOB = "raw_*.parquet"
#: Pass-2 in-progress file inside one partition directory (stale ones are deleted on resume).
SORTING_TMP = "_sorting.tmp"
#: Progress/resume state beside the partition directories (written into ``.new`` in pass 1).
PROGRESS_FILENAME = "_progress.json"

SizeClass = Literal["small", "large"]


@dataclass(slots=True)
class Progress:
    """``<dest>/_progress.json`` — the resume state of one partitioned stage (EP-18 item 3)."""

    build_id: str
    pass1_done: bool = False
    buckets_requested: list[int] = field(default_factory=list)
    sorted_buckets: list[int] = field(default_factory=list)
    dev_ready: bool = False
    complete: bool = False
    started: str = ""
    updated: str = ""
    rejects: int = 0  # counted when pass 1 ran; carried across resumes

    def to_json(self) -> str:
        import json

        return json.dumps(asdict(self), indent=2, sort_keys=True) + "\n"


def progress_path(dest_dir: Path) -> Path:
    return Path(dest_dir) / PROGRESS_FILENAME


def read_progress(dest_dir: Path) -> Progress | None:
    """The parsed progress file, or None (missing / unreadable / wrong shape)."""
    import json

    path = progress_path(dest_dir)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return Progress(**data)
    except (OSError, ValueError, TypeError):
        return None


def _write_progress(dest_dir: Path, progress: Progress) -> None:
    from mimicwarehouse.inventory import _atomic_write_text

    progress.updated = utc_now_iso()
    _atomic_write_text(progress_path(dest_dir), progress.to_json())


# ---------------------------------------------------------------------------
# Pass-1 heartbeat — counts only, never a value
# ---------------------------------------------------------------------------


def _rss_bytes() -> int:
    """Resident set size of this process (0 when the probe is unavailable)."""
    try:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            class _PMC(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            pmc = _PMC()
            pmc.cb = ctypes.sizeof(_PMC)
            handle = ctypes.windll.kernel32.GetCurrentProcess()  # type: ignore[attr-defined]
            if ctypes.windll.psapi.GetProcessMemoryInfo(  # type: ignore[attr-defined]
                handle, ctypes.byref(pmc), pmc.cb
            ):
                return int(pmc.WorkingSetSize)
        return 0
    except Exception:  # pragma: no cover - defensive; a failed probe is just 0
        return 0


class _Heartbeat(threading.Thread):
    """Logs ``rss`` / tmp-duckdb size / bytes written every ``interval_s`` while pass 1 runs."""

    def __init__(self, what: str, new_dir: Path, tmp_duckdb: Path, interval_s: float) -> None:
        super().__init__(name="ep18-pass1-heartbeat", daemon=True)
        self._what, self._new_dir, self._tmp = what, new_dir, tmp_duckdb
        self._interval = interval_s
        self._stop = threading.Event()

    def run(self) -> None:
        while not self._stop.wait(self._interval):
            _LOG.info(
                "pass1 %s: rss=%d tmp_duckdb=%d written=%d bytes",
                self._what,
                _rss_bytes(),
                dir_size_bytes(self._tmp) if self._tmp.is_dir() else 0,
                dir_size_bytes(self._new_dir) if self._new_dir.is_dir() else 0,
            )

    def stop(self) -> None:
        self._stop.set()


# ---------------------------------------------------------------------------
# SQL builders
# ---------------------------------------------------------------------------


def _bucket_expr() -> str:
    return f"{SUBJECT_KEY} % {NUM_BUCKETS}"


def _in_list(buckets: Iterable[int]) -> str:
    return ", ".join(str(b) for b in buckets)


def _partition_copy_options(filename_pattern: str, *, ignore_existing: bool) -> str:
    opts = (
        f"{COPY_OPTIONS}, PARTITION_BY ({BUCKET_COLUMN}), "
        f"FILENAME_PATTERN {_sql_str(filename_pattern)}"
    )
    if ignore_existing:
        # later sweeps write DISJOINT buckets into the same root; the flag only lifts the
        # "directory is not empty" refusal (APPEND would force a {uuid} filename pattern)
        opts += ", OVERWRITE_OR_IGNORE"
    return opts


def _chunks(seq: tuple[int, ...], n: int) -> list[tuple[int, ...]]:
    """``seq`` split into ``n`` contiguous, near-equal, non-empty chunks."""
    n = min(n, len(seq))
    size, rem = divmod(len(seq), n)
    out: list[tuple[int, ...]] = []
    start = 0
    for i in range(n):
        end = start + size + (1 if i < rem else 0)
        out.append(seq[start:end])
        start = end
    return out


# ---------------------------------------------------------------------------
# Pass 2 — one bucket (module-level so the resume test can record/interrupt calls)
# ---------------------------------------------------------------------------


def _sort_bucket(
    con: duckdb.DuckDBPyConnection, bucket_dir: Path, order_by: tuple[str, ...]
) -> int:
    """Sort one bucket's ``raw_*`` files into ``part-0.parquet``; returns the row count.

    Publish-before-delete (module docstring): write ``_sorting.tmp``, ``os.replace`` it to
    ``part-0.parquet`` — the raws are deleted by the caller only after the bucket is
    recorded sorted. A pre-existing ``_sorting.tmp`` here is stale (crashed run): deleted.
    """
    tmp = bucket_dir / SORTING_TMP
    if tmp.exists():
        tmp.unlink()
    part = bucket_dir / PART_FILENAME
    raw_glob = (bucket_dir / RAW_GLOB).as_posix()
    if not any(bucket_dir.glob(RAW_GLOB)):
        if part.is_file():  # crash between publish and progress write: already sorted
            (rows,) = con.execute(
                f"SELECT num_rows FROM parquet_file_metadata({_sql_str(str(part))})"
            ).fetchone()  # type: ignore[misc]
            return int(rows)
        return 0
    con.execute("SET preserve_insertion_order = true")
    try:
        # hive_partitioning=false: the raws live under subject_bucket=<n>/, and DuckDB's
        # auto-detection would otherwise synthesise a subject_bucket column INTO the file
        con.execute(
            f"COPY (SELECT * FROM read_parquet({_sql_str(raw_glob)}, hive_partitioning=false) "
            f"ORDER BY {', '.join(order_by)}) "
            f"TO {_sql_str(str(tmp))} ({COPY_OPTIONS})"
        )
    finally:
        con.execute("SET preserve_insertion_order = false")
    os.replace(tmp, part)
    (rows,) = con.execute(
        f"SELECT num_rows FROM parquet_file_metadata({_sql_str(str(part))})"
    ).fetchone()  # type: ignore[misc]
    return int(rows)


def _sweep_raws(bucket_dir: Path) -> None:
    """Delete leftover ``raw_*`` files of a bucket that is already recorded sorted."""
    for raw in bucket_dir.glob(RAW_GLOB):
        raw.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# The partitioned stage
# ---------------------------------------------------------------------------


def _manifest_line_for(
    part: Path,
    table_spec: Table,
    lake_root: Path,
    *,
    rows: int,
    build_id: str,
    source_sha256: str | None,
    raw_snapshot_id: str | None,
) -> ManifestLine:
    return ManifestLine(
        schema=table_spec.schema_name,
        table=table_spec.name,
        path=lake_relative_posix(part, lake_root),
        sha256=sha256_streamed(part),
        bytes=part.stat().st_size,
        rows=rows,
        schema_hash=table_schema_hash(table_spec),
        writer_version=writer_version(),
        source_sha256=source_sha256,
        raw_snapshot_id=raw_snapshot_id,
        build_id=build_id,
        ts=utc_now_iso(),
    )


def _partition_dirs(dest_dir: Path) -> dict[int, Path]:
    """``{bucket: dir}`` of the existing ``subject_bucket=<n>`` partition directories."""
    out: dict[int, Path] = {}
    for child in dest_dir.iterdir() if dest_dir.is_dir() else ():
        if child.is_dir() and child.name.startswith(f"{BUCKET_COLUMN}="):
            try:
                out[int(child.name.split("=", 1)[1])] = child
            except ValueError:
                continue
    return out


def _table_totals(con: duckdb.DuckDBPyConnection, dest_dir: Path) -> tuple[int, int, int]:
    """``(rows, bytes, files)`` over every published ``part-*.parquet`` under ``dest_dir``."""
    rows = bytes_ = files = 0
    for bucket_dir in _partition_dirs(dest_dir).values():
        for part in sorted(bucket_dir.glob("part-*.parquet")):
            (n,) = con.execute(
                f"SELECT num_rows FROM parquet_file_metadata({_sql_str(str(part))})"
            ).fetchone()  # type: ignore[misc]
            rows += int(n)
            bytes_ += part.stat().st_size
            files += 1
    return rows, bytes_, files


def _tier_complete_for(requested: tuple[int, ...], dev_buckets: list[int]) -> str | None:
    if set(requested) == set(range(NUM_BUCKETS)):
        return "full"
    if set(dev_buckets) <= set(requested):
        return "dev"
    return None


def stage_partitioned(
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
    sort_by: list[str] | None = None,
    buckets: Iterable[int] | None = None,
    size_class: SizeClass | None = None,
    column_map: ColumnMap | None = None,
    sweeps: int = 1,
    heartbeat_s: float = 60.0,
) -> StageResult:
    """Stage ``source`` as Hive subject-bucket partitions under ``dest_dir`` (module doc).

    ``sort_by`` defaults to the contract ``sort_keys`` minus the leading ``subject_id``,
    ``size_class`` to the contract ``load_class``, ``buckets`` to all 100 (``None``); pass
    ``settings.dev_buckets`` for a dev-tier stage. Appends manifest lines to
    ``<lake_root>/manifests/<build_id>.jsonl`` (small path: per partition file after the
    swap; large path: per bucket as it finishes) and merges the ``status.json`` step entry
    (``dev_ready`` as soon as the dev buckets are sorted, ``tier_complete`` at the end).
    Raises :class:`RejectThresholdError` over ``settings.loader_reject_max`` — the ``.new``
    directory is discarded and ``dest_dir`` is left untouched.
    """
    t0 = time.perf_counter()
    settings = settings or get_settings()
    source, dest_dir, lake_root = Path(source), Path(dest_dir), Path(lake_root)
    try:
        dest_dir.resolve().relative_to(lake_root.resolve())
    except ValueError:
        raise StageError(f"{dest_dir} is not under the lake root {lake_root}") from None
    if not table_spec.partitioned:
        raise StageError(
            f"{table_spec.qualified_name} is not partitioned — use stage_unpartitioned (EP-17)"
        )
    if not table_spec.sort_keys or table_spec.sort_keys[0] != SUBJECT_KEY:
        raise StageError(
            f"{table_spec.qualified_name}: contract sort_keys must start with '{SUBJECT_KEY}'"
        )
    if sweeps < 1:
        raise StageError(f"sweeps must be >= 1, got {sweeps}")

    resolved_sort_by = tuple(sort_by) if sort_by is not None else tuple(table_spec.sort_keys[1:])
    for key in resolved_sort_by:
        if not table_spec.has_column(key):
            raise StageError(f"{table_spec.qualified_name}: sort_by names unknown column {key!r}")
    resolved_class: SizeClass = size_class if size_class is not None else table_spec.load_class
    if resolved_class not in ("small", "large"):
        raise StageError(f"unknown size_class {resolved_class!r}; expected 'small' or 'large'")
    if buckets is None:
        requested: tuple[int, ...] = tuple(range(NUM_BUCKETS))
    else:
        requested = tuple(sorted(set(int(b) for b in buckets)))
        if not requested or any(b < 0 or b >= NUM_BUCKETS for b in requested):
            raise StageError(f"buckets must be a non-empty subset of 0..{NUM_BUCKETS - 1}")
    dev_buckets = list(settings.dev_buckets)
    order_by_pass2 = (SUBJECT_KEY, *resolved_sort_by)
    qn = table_spec.qualified_name

    # -- resume decision (large path only) -------------------------------------------------
    progress = read_progress(dest_dir)
    resume = (
        resolved_class == "large"
        and progress is not None
        and progress.pass1_done
        and not progress.complete
        and progress.buckets_requested == list(requested)
    )

    manifest_lines: list[ManifestLine] = []
    rejects = 0

    if resume:
        assert progress is not None
        progress.build_id = build_id
        rejects = progress.rejects
    else:
        # -- pass 1 (both paths): partitioned COPY into <dest>.new -------------------------
        plan = plan_csv_read(source, table_spec, column_map)
        new_dir = paths.new_dir_for(dest_dir)
        if new_dir.exists():  # a stale .new from a crashed stage: partial output discarded
            shutil.rmtree(new_dir)
        new_dir.mkdir(parents=True, exist_ok=True)
        progress = Progress(
            build_id=build_id, buckets_requested=list(requested), started=utc_now_iso()
        )

        select = f"SELECT {', '.join(plan.select_exprs)}, {_bucket_expr()} AS {BUCKET_COLUMN} "
        heartbeat = _Heartbeat(qn, new_dir, settings.layout["tmp_duckdb"], heartbeat_s)
        heartbeat.start()
        try:
            if resolved_class == "small":
                where = (
                    f"WHERE {_bucket_expr()} IN ({_in_list(requested)}) "
                    if buckets is not None
                    else ""
                )
                order_cols = ", ".join((BUCKET_COLUMN, SUBJECT_KEY, *resolved_sort_by))
                copy_sql = (
                    f"COPY ({select}FROM {plan.relation_sql} {where}"
                    f"ORDER BY {order_cols}) TO {_sql_str(str(new_dir))} "
                    f"({_partition_copy_options('part-{i}', ignore_existing=False)})"
                )
                con.execute(f"DROP TABLE IF EXISTS {plan.rejects_table}")
                con.execute(f"DROP TABLE IF EXISTS {plan.rejects_scan}")
                con.execute("SET preserve_insertion_order = true")
                try:
                    con.execute(copy_sql)
                finally:
                    con.execute("SET preserve_insertion_order = false")
            else:
                sweep_chunks = _chunks(requested, sweeps)
                con.execute("SET preserve_insertion_order = false")
                for i, chunk in enumerate(sweep_chunks):
                    # every sweep re-scans the CSV, re-recording the same rejects — drop
                    # first so the post-pass count is the one full-file set, not N of them
                    con.execute(f"DROP TABLE IF EXISTS {plan.rejects_table}")
                    con.execute(f"DROP TABLE IF EXISTS {plan.rejects_scan}")
                    where = (
                        ""
                        if buckets is None and len(sweep_chunks) == 1
                        else f"WHERE {_bucket_expr()} IN ({_in_list(chunk)}) "
                    )
                    con.execute(
                        f"COPY ({select}FROM {plan.relation_sql} {where}) "
                        f"TO {_sql_str(str(new_dir))} "
                        f"({_partition_copy_options('raw_{i}', ignore_existing=i > 0)})"
                    )
            rejects = _reject_count(con, plan.rejects_table)
            if rejects:
                rej_path = rejects_parquet_path(lake_root, table_spec, build_id)
                rej_path.parent.mkdir(parents=True, exist_ok=True)
                con.execute(
                    f"COPY (SELECT * FROM {plan.rejects_table}) "
                    f"TO {_sql_str(str(rej_path))} ({COPY_OPTIONS})"
                )
            if rejects > settings.loader_reject_max:
                shutil.rmtree(new_dir, ignore_errors=True)
                raise RejectThresholdError(
                    qn,
                    rejects,
                    settings.loader_reject_max,
                    rejects_parquet_path(lake_root, table_spec, build_id),
                )
        finally:
            heartbeat.stop()

        progress.pass1_done = True
        progress.rejects = rejects
        if resolved_class == "small":  # the ordered COPY already sorted every bucket
            progress.sorted_buckets = list(requested)
        # progress travels inside .new so the swap publishes data + progress together
        _write_progress(new_dir, progress)
        paths.swap_dir(new_dir, dest_dir)

    # -- pass 2 (large path): per-bucket sort, dev buckets first ---------------------------
    if resolved_class == "large":
        already_sorted = set(progress.sorted_buckets)
        dirs = _partition_dirs(dest_dir)
        order = [b for b in requested if b in dev_buckets] + [
            b for b in requested if b not in dev_buckets
        ]
        for n in order:
            bucket_dir = dirs.get(n)
            if n in already_sorted:
                if bucket_dir is not None:
                    _sweep_raws(bucket_dir)  # leftovers of a crash after the progress write
                continue
            tb0 = time.perf_counter()
            rows = _sort_bucket(con, bucket_dir, order_by_pass2) if bucket_dir else 0
            progress.sorted_buckets.append(n)
            _write_progress(dest_dir, progress)
            if bucket_dir is not None:
                _sweep_raws(bucket_dir)
                part = bucket_dir / PART_FILENAME
                if part.is_file():
                    line = _manifest_line_for(
                        part,
                        table_spec,
                        lake_root,
                        rows=rows,
                        build_id=build_id,
                        source_sha256=source_sha256,
                        raw_snapshot_id=raw_snapshot_id,
                    )
                    append_manifest(lake_root, build_id, [line])
                    manifest_lines.append(line)
            _LOG.info(
                "bucket %02d/%d sorted rows=%d wall=%.1fs",
                n,
                NUM_BUCKETS,
                rows,
                time.perf_counter() - tb0,
            )
            if not progress.dev_ready and set(dev_buckets) <= set(progress.sorted_buckets):
                progress.dev_ready = True
                _write_progress(dest_dir, progress)
                _LOG.info("dev-ready %s", qn)
                update_status(lake_root, qn, dev_ready=True)
    else:
        # small path: everything was sorted in the one COPY — manifest per partition file
        for _n, bucket_dir in sorted(_partition_dirs(dest_dir).items()):
            for part in sorted(bucket_dir.glob("part-*.parquet")):
                (rows_n,) = con.execute(
                    f"SELECT num_rows FROM parquet_file_metadata({_sql_str(str(part))})"
                ).fetchone()  # type: ignore[misc]
                manifest_lines.append(
                    _manifest_line_for(
                        part,
                        table_spec,
                        lake_root,
                        rows=int(rows_n),
                        build_id=build_id,
                        source_sha256=source_sha256,
                        raw_snapshot_id=raw_snapshot_id,
                    )
                )
        if manifest_lines:
            append_manifest(lake_root, build_id, manifest_lines)
        if not progress.dev_ready and set(dev_buckets) <= set(progress.sorted_buckets):
            progress.dev_ready = True
            _write_progress(dest_dir, progress)
            _LOG.info("dev-ready %s", qn)
            update_status(lake_root, qn, dev_ready=True)

    # -- completion ------------------------------------------------------------------------
    progress.complete = True
    _write_progress(dest_dir, progress)
    rows, bytes_, files = _table_totals(con, dest_dir)
    tier = _tier_complete_for(requested, dev_buckets)
    _LOG.info("complete %s tier=%s", qn, tier or "partial")
    status_fields: dict[str, Any] = {
        "build_id": build_id,
        "rows": rows,
        "bytes": bytes_,
        "files": files,
        "rejects": rejects,
        "finished_at": utc_now_iso(),
    }
    if tier is not None:
        status_fields["tier_complete"] = tier
    update_status(lake_root, qn, **status_fields)
    return StageResult(
        rows=rows,
        bytes=bytes_,
        files=files,
        rejects=rejects,
        wall_s=time.perf_counter() - t0,
        manifest_lines=tuple(manifest_lines),
    )


__all__ = [
    "BUCKET_COLUMN",
    "NUM_BUCKETS",
    "PROGRESS_FILENAME",
    "RAW_GLOB",
    "SORTING_TMP",
    "Progress",
    "progress_path",
    "read_progress",
    "stage_partitioned",
]
