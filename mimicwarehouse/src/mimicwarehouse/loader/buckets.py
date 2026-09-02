"""Partitioned stage: typed CSV → Hive subject buckets, sorted, resumable (EP-18; DESIGN §5).

The physical layout every subject-keyed table uses:
``<dest>/subject_bucket=<n>/part-0.parquet`` with ``subject_bucket = subject_id % 100``
(un-padded DuckDB partition names, partition column **not** written into the files), rows
sorted ``(subject_id, <sort_by>)``, ZSTD-3, ~1 M-row row groups. Two paths, one publish
protocol (every ``.new`` → ``dest`` goes through :func:`mimicwarehouse.publish.swap_dir`,
rename-aside two-step — ``os.replace`` cannot replace an existing directory on Windows;
every other pass-2 replace/unlink and the stale-``.new`` sweep use the same module's
retrying helpers, EP-33 WIN-2/WIN-4):

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
append the bucket's manifest line, record the bucket in ``_progress.json``, **then**
delete the ``raw_*`` files — a crash at any point leaves either the raws (bucket
re-sorts; a re-appended manifest line is harmless, newest ``ts`` wins per path) or a
recorded sorted bucket whose line is already in the manifest (raws are swept on
resume), never a bucket with no complete source and never a recorded bucket without a
manifest line (EP-33 WIN-1/LDR-3). Progress resumes only when the recorded
``buckets_requested`` equal the new request, ``complete`` is false, and — when the
progress file records them (EP-33 LDR-4) — the source identity (``source_sha256`` or the
source file's name/size/mtime fingerprint) and the resolved ``sort_by`` match; anything
else (a different source file, a **full** request over a ``tier_complete = "dev"``
table) restages the whole table. A pre-EP-33 progress file without those fields keeps
the old predicate, so the full lake's ``_progress.json`` files still load.

**Coverage guard** (EP-33 LDR-1, owner decision): before pass 1 replaces ``dest``, a
request whose bucket set is a **strict subset** of what ``dest`` already holds (its
recorded ``buckets_requested``, or ``tier_complete = "full"`` in ``status.json``) raises
:class:`StageCoverageError` and leaves the lake untouched — dev and full share one lake
root, so ``mwh build --tier dev --force`` must never discard the 95 non-dev partitions
of a complete table. Equal or wider requests proceed; ``mwh build --tier full --force``
is the only path that rewrites a complete table, and the runner's ``--force`` only
bypasses the skip, never this guard.

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
import shutil
import sys
import threading
import time
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from mimicwarehouse import fsio, publish
from mimicwarehouse.config import Settings, dir_size_bytes, get_settings
from mimicwarehouse.loader.csv import plan_csv_read, plan_map_notes
from mimicwarehouse.loader.manifest import (
    ManifestLine,
    append_manifest,
    lake_relative_posix,
    read_status,
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


class StageCoverageError(StageError):
    """A bucket request would replace a table that already holds a strict superset of
    those buckets (EP-33 LDR-1). Nothing was changed."""


@dataclass(slots=True)
class Progress:
    """``<dest>/_progress.json`` — the resume state of one partitioned stage (EP-18 item 3).

    EP-33 (LDR-4) adds the identity a resume must match: ``source_sha256`` (the EP-10
    raw-manifest hash the caller passed; None on the fixture tier), ``source_fingerprint``
    (``<name>:<size>:<mtime_ns>`` of the source file — always recorded, the check that
    works without a manifest) and the resolved ``sort_by``. They are ``None`` when the
    file predates EP-33; :func:`read_progress` tolerates unknown and missing keys.
    """

    build_id: str
    pass1_done: bool = False
    buckets_requested: list[int] = field(default_factory=list)
    sorted_buckets: list[int] = field(default_factory=list)
    dev_ready: bool = False
    complete: bool = False
    started: str = ""
    updated: str = ""
    rejects: int = 0  # counted when pass 1 ran; carried across resumes
    source_sha256: str | None = None
    source_fingerprint: str | None = None
    sort_by: list[str] | None = None

    def to_json(self) -> str:
        import json

        return json.dumps(asdict(self), indent=2, sort_keys=True) + "\n"

    def resumable_for(
        self, *, source_sha256: str | None, source_fingerprint: str, sort_by: Iterable[str]
    ) -> bool:
        """Whether the recorded identity fields (those present) match a new request."""
        if self.source_sha256 is not None and self.source_sha256 != source_sha256:
            return False
        if self.source_fingerprint is not None and self.source_fingerprint != source_fingerprint:
            return False
        return self.sort_by is None or self.sort_by == list(sort_by)


_PROGRESS_FIELDS = frozenset(f.name for f in fields(Progress))


def progress_path(dest_dir: Path) -> Path:
    return Path(dest_dir) / PROGRESS_FILENAME


def read_progress(dest_dir: Path) -> Progress | None:
    """The parsed progress file, or None (missing / unreadable / wrong shape). Unknown
    keys are ignored and missing ones take their defaults (EP-33: old and new formats
    both load)."""
    import json

    path = progress_path(dest_dir)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or "build_id" not in data:
            return None
        return Progress(**{k: v for k, v in data.items() if k in _PROGRESS_FIELDS})
    except (OSError, ValueError, TypeError):
        return None


def source_fingerprint(source: Path) -> str:
    """``<name>:<size>:<mtime_ns>`` of the source file — the resume identity that needs
    no raw manifest (a file name, never content)."""
    st = Path(source).stat()
    return f"{Path(source).name}:{st.st_size}:{st.st_mtime_ns}"


def _write_progress(dest_dir: Path, progress: Progress) -> None:
    progress.updated = utc_now_iso()
    fsio.atomic_write_text(progress_path(dest_dir), progress.to_json())


def _existing_coverage(dest_dir: Path, lake_root: Path, qn: str) -> set[int] | None:
    """The bucket set ``dest_dir`` already holds according to its records — the recorded
    ``buckets_requested`` of a pass-1-complete progress file, or all buckets when
    ``status.json`` says ``tier_complete = "full"``; None when nothing is recorded."""
    if not dest_dir.is_dir():
        return None
    covered: set[int] | None = None
    progress = read_progress(dest_dir)
    if progress is not None and progress.pass1_done and progress.buckets_requested:
        covered = set(progress.buckets_requested)
    entry = read_status(lake_root)["steps"].get(qn)
    if entry is not None and entry.get("tier_complete") == "full":
        covered = set(range(NUM_BUCKETS)) | (covered or set())
    return covered


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
    The replace and unlink go through :mod:`mimicwarehouse.publish`'s retry policy (a
    scanner holding a freshly written multi-GB file is transient; WIN-2).
    """
    tmp = bucket_dir / SORTING_TMP
    if tmp.exists():
        publish.unlink(tmp)
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
    publish.replace(tmp, part)
    (rows,) = con.execute(
        f"SELECT num_rows FROM parquet_file_metadata({_sql_str(str(part))})"
    ).fetchone()  # type: ignore[misc]
    return int(rows)


def _sweep_raws(bucket_dir: Path) -> None:
    """Delete leftover ``raw_*`` files of a bucket that is already recorded sorted
    (retrying unlink, WIN-2)."""
    for raw in list(bucket_dir.glob(RAW_GLOB)):
        publish.unlink(raw)


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
    map_notes: dict[str, list[str]] | None = None,
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
        map_notes=map_notes,
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
    directory is discarded and ``dest_dir`` is left untouched — and
    :class:`StageCoverageError` when ``buckets`` is a strict subset of what ``dest_dir``
    already holds (module docstring, coverage guard).
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
    # header-only read (EP-22): what the column map could not carry losslessly — computed
    # up front so a resumed large stage records the same notes as a fresh one
    map_notes = (
        plan_map_notes(table_spec, plan_csv_read(source, table_spec, column_map))
        if column_map is not None
        else None
    )

    # -- resume decision (large path only) -------------------------------------------------
    fingerprint = source_fingerprint(source)
    progress = read_progress(dest_dir)
    resume = (
        resolved_class == "large"
        and progress is not None
        and progress.pass1_done
        and not progress.complete
        and progress.buckets_requested == list(requested)
        and progress.resumable_for(
            source_sha256=source_sha256, source_fingerprint=fingerprint, sort_by=resolved_sort_by
        )
    )

    manifest_lines: list[ManifestLine] = []
    rejects = 0
    pass1_wall_s: float | None = None  # large path only; None when a resume skipped pass 1
    pass2_wall_s: float | None = None

    if resume:
        assert progress is not None
        progress.build_id = build_id
        rejects = progress.rejects
    else:
        # -- coverage guard (LDR-1): never let a subset request discard staged buckets ------
        covered = _existing_coverage(dest_dir, lake_root, qn)
        if covered is not None and set(requested) < covered:
            raise StageCoverageError(
                f"{qn}: refusing to restage {len(requested)} bucket(s) over a table that "
                f"already holds {len(covered)} — the request is a strict subset and the "
                "rename-aside swap would discard the other staged partitions; "
                "`mwh build --tier full --force` is the only path that rewrites a complete "
                "table (the runner's --force only bypasses the skip)"
            )
        # -- pass 1 (both paths): partitioned COPY into <dest>.new -------------------------
        t_pass1 = time.perf_counter()
        plan = plan_csv_read(source, table_spec, column_map)
        new_dir = publish.new_path_for(dest_dir)
        if new_dir.exists():  # a stale .new from a crashed stage: partial output discarded
            publish.rmtree(new_dir)  # retrying (WIN-4)
        new_dir.mkdir(parents=True, exist_ok=True)
        progress = Progress(
            build_id=build_id,
            buckets_requested=list(requested),
            started=utc_now_iso(),
            source_sha256=source_sha256,
            source_fingerprint=fingerprint,
            sort_by=list(resolved_sort_by),
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
        publish.swap_dir(new_dir, dest_dir)
        if resolved_class == "large":
            pass1_wall_s = time.perf_counter() - t_pass1

    # -- pass 2 (large path): per-bucket sort, dev buckets first ---------------------------
    if resolved_class == "large":
        t_pass2 = time.perf_counter()
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
            # order (WIN-1/LDR-3): manifest line -> progress record -> raw sweep. A crash
            # after the append but before the record re-sorts the bucket and re-appends
            # (newest ts wins per path); a recorded bucket always has its line.
            part = bucket_dir / PART_FILENAME if bucket_dir is not None else None
            if part is not None and part.is_file():
                line = _manifest_line_for(
                    part,
                    table_spec,
                    lake_root,
                    rows=rows,
                    build_id=build_id,
                    source_sha256=source_sha256,
                    raw_snapshot_id=raw_snapshot_id,
                    map_notes=map_notes,
                )
                append_manifest(lake_root, build_id, [line])
                manifest_lines.append(line)
            progress.sorted_buckets.append(n)
            _write_progress(dest_dir, progress)
            if bucket_dir is not None:
                _sweep_raws(bucket_dir)
            _LOG.info(
                "bucket %02d/%d sorted rows=%d wall=%.1fs",
                n,
                NUM_BUCKETS,
                rows,
                time.perf_counter() - tb0,
            )
            if not progress.dev_ready and set(dev_buckets) <= set(progress.sorted_buckets):
                # status first (LDR-8): a crash between the two writes then re-emits the
                # status signal on resume instead of losing it
                _LOG.info("dev-ready %s", qn)
                update_status(lake_root, qn, dev_ready=True)
                progress.dev_ready = True
                _write_progress(dest_dir, progress)
        pass2_wall_s = time.perf_counter() - t_pass2
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
                        map_notes=map_notes,
                    )
                )
        if manifest_lines:
            append_manifest(lake_root, build_id, manifest_lines)
        if not progress.dev_ready and set(dev_buckets) <= set(progress.sorted_buckets):
            _LOG.info("dev-ready %s", qn)
            update_status(lake_root, qn, dev_ready=True)  # status before progress (LDR-8)
            progress.dev_ready = True
            _write_progress(dest_dir, progress)

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
    if map_notes is not None:
        status_fields["map_notes"] = map_notes
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
        pass1_wall_s=pass1_wall_s,
        pass2_wall_s=pass2_wall_s,
    )


__all__ = [
    "BUCKET_COLUMN",
    "NUM_BUCKETS",
    "PROGRESS_FILENAME",
    "RAW_GLOB",
    "SORTING_TMP",
    "Progress",
    "StageCoverageError",
    "progress_path",
    "read_progress",
    "source_fingerprint",
    "stage_partitioned",
]
