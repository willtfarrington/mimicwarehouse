"""Raw inventory manifest — SHA-256, size, header and row count of every raw CSV (EP-10; D-26).

The 41 raw CSVs under ``source material/`` were decompressed in place and the ``.csv.gz``
archives deleted, so PhysioNet's ``SHA256SUMS.txt`` (which covers only the archives) cannot
verify them (roadmap Risk 1). This module computes the local manifest that **D-26** makes the
**raw snapshot id** every lake manifest (EP-17+, DESIGN §5) cites as its ``source manifest id``:

* :func:`inventory_file` — one :class:`FileRecord` per CSV: streaming SHA-256
  (``hashlib.file_digest``), byte size + mtime, the header line (schema, not data) compared
  with the EP-9 contract, and the row count from DuckDB ``read_csv`` (quoted embedded newlines
  make newline counting wrong; the parallel reader falls back to ``parallel=false`` per file);
* the manifest store under ``<data_root>/lake/manifests/raw/`` — one JSONL line per file in
  ``<dataset-dir>.jsonl`` plus ``raw_snapshot.json`` (``raw_snapshot_id`` = sha256 over the sorted
  canonical ``(rel_path, bytes, sha256, rows)`` tuples, ``None`` until all 41 files are present,
  job status, per-dataset totals, versions); :func:`load_raw_manifest` / :func:`raw_snapshot_id`
  are what the loader (EP-17/18/19) reads;
* reconciliation against the row counts of the vendored mimic-code ``validate.sql`` (EP-8):
  :func:`parse_validate_sql`, :func:`expected_counts`, :func:`reconcile`, and the docs writer for
  ``docs/resources/raw-inventory.md`` (hashes / counts / schema only — committable under
  GOVERNANCE §3);
* ``mwh inventory build | show | reconcile`` (:data:`inventory_app`, attached in
  :mod:`mimicwarehouse.cli`; ``build`` writes under the data root, so it receives *validated*
  settings).

Governance: this module **reads** the raw files (that is its job) but its outputs are hashes,
byte counts, column names and row counts only — never a row, never a value (GOVERNANCE §4).
The header line is schema, not data. Console output stays ASCII (roadmap Risk 13); every
integer in the docs table carries thousands separators because the guard's G4 rule (EP-4)
refuses bare 8-digit tokens starting with 1-3, and byte sizes / row counts routinely are.

Import budget: this module is imported by ``mwh`` at start-up (the typer sub-app lives here),
so duckdb, the schema contract and the vendor pin are imported inside the functions that need
them.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Annotated, Any, Literal

import typer
from pydantic import BaseModel, ConfigDict, Field
from rich import box
from rich.console import Console
from rich.markup import escape
from rich.table import Table as RichTable

from mimicwarehouse import config
from mimicwarehouse.config import Settings

if TYPE_CHECKING:  # pragma: no cover — typing only (import budget)
    import duckdb

    from mimicwarehouse.schema.contract import Contract, Table

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: The contract's 41 tables = 41 raw CSVs (22 hosp + 9 icu + 6 ed + 4 note).
FILES_EXPECTED = 41
#: Contract dataset label → directory name under the source root (PhysioNet archive names,
#: ``source material/README.md``).
DATASET_DIRS: dict[str, str] = {
    "mimic-iv-3.1": "mimic-iv-3.1",
    "mimic-iv-ed-2.2": "mimic-iv-ed-2.2",
    "mimic-iv-note-2.2": "mimic-iv-note-deidentified-free-text-clinical-notes-2.2",
}
#: ``<data_root>/lake/manifests/<RAW_DIRNAME>/`` — not a ``Settings.layout`` key; created here.
RAW_DIRNAME = "raw"
SNAPSHOT_FILENAME = "raw_snapshot.json"
SHA256SUMS_NAME = "SHA256SUMS.txt"
DOCS_RELPATH = PurePosixPath("docs/resources/raw-inventory.md")
#: DuckDB ``read_csv`` options for counting: header on, everything VARCHAR (no casts can fail),
#: explicit dialect (no sniffer surprises). The fallback adds ``parallel=false``.
CSV_READ_OPTIONS = "header=true, all_varchar=true, delim=',', quote='\"', escape='\"'"
#: Snapshot history kept in ``raw_snapshot.json`` (last N build invocations).
MAX_RUN_HISTORY = 20

RowcountMethod = Literal["duckdb", "duckdb_serial", "skipped", "failed"]
ReconStatus = Literal["match", "mismatch", "no-expectation", "pending"]

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class FileRecord(BaseModel):
    """One raw CSV: identity, hash, header status, row count, timings. Never a value."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    dataset: str = Field(description="contract dataset label, e.g. mimic-iv-3.1")
    dataset_dir: str = Field(description="directory name under the source root")
    module: str = Field(description="hosp / icu / ed / note (first segment of csv_path)")
    schema_name: str = Field(description="contract schema, e.g. mimiciv_hosp")
    table: str = Field(description="bare table name, e.g. admissions")
    rel_path: str = Field(description="posix path relative to the source root")
    bytes: int = Field(ge=0)
    mtime: str = Field(description="file mtime, ISO 8601 UTC")
    mtime_ns: int = Field(description="file mtime in ns (resume key together with bytes)")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    header: list[str]
    header_matches_contract: bool
    missing_columns: list[str] = Field(default_factory=list)
    extra_columns: list[str] = Field(default_factory=list)
    rows: int | None = None
    rowcount_method: RowcountMethod = "skipped"
    rowcount_error: str | None = None
    csv_parallel_fallback: bool = False
    seconds_hash: float = Field(ge=0)
    seconds_rows: float = Field(ge=0)
    physionet_gz_sha256: str | None = Field(
        default=None, description="SHA256SUMS.txt entry of the deleted .csv.gz (parked RAW-1)"
    )
    recorded_at: str = Field(description="ISO 8601 UTC")

    @property
    def qualified_name(self) -> str:
        return f"{self.schema_name}.{self.table}"

    @property
    def header_status(self) -> str:
        """``ok`` / ``order`` (same names, different order) / ``mismatch``."""
        if self.header_matches_contract:
            return "ok"
        if not self.missing_columns and not self.extra_columns:
            return "order"
        return "mismatch"

    @property
    def mb_per_s(self) -> float | None:
        if self.seconds_hash <= 0:
            return None
        return self.bytes / 1e6 / self.seconds_hash

    def matches_stat(self, size: int, mtime_ns: int) -> bool:
        """The resume key: same byte size and same mtime."""
        return self.bytes == size and self.mtime_ns == mtime_ns


@dataclass(frozen=True, slots=True)
class PlannedFile:
    """One contract table resolved against the source root (exists or not)."""

    table_qn: str
    dataset: str
    dataset_dir: str
    csv_path: str
    rel_path: str
    path: Path
    exists: bool
    bytes: int
    mtime_ns: int


@dataclass
class RawManifest:
    """The manifest store as read from disk: records by ``rel_path`` + the snapshot object."""

    root: Path
    records: dict[str, FileRecord] = field(default_factory=dict)
    snapshot: dict[str, Any] = field(default_factory=dict)

    @property
    def files_done(self) -> int:
        return len(self.records)

    @property
    def raw_snapshot_id(self) -> str | None:
        return self.snapshot.get("raw_snapshot_id")

    def by_dataset(self, dataset: str) -> list[FileRecord]:
        return sorted(
            (r for r in self.records.values() if r.dataset == dataset), key=lambda r: r.rel_path
        )


@dataclass(frozen=True, slots=True)
class ReconRow:
    """One line of ``mwh inventory reconcile``."""

    dataset: str
    schema_name: str
    table: str
    expected: int | None
    observed: int | None
    delta: int | None
    status: ReconStatus
    source: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset": self.dataset,
            "table": f"{self.schema_name}.{self.table}",
            "expected": self.expected,
            "observed": self.observed,
            "delta": self.delta,
            "status": self.status,
            "source": self.source,
        }


@dataclass
class BuildResult:
    """What one ``build`` invocation did."""

    processed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    filtered: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    errors: list[dict[str, str]] = field(default_factory=list)
    started: str = ""
    finished: str = ""
    seconds: float = 0.0
    raw_snapshot_id: str | None = None
    files_done: int = 0

    @property
    def ok(self) -> bool:
        return not self.errors


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _iso(ts_ns: int) -> str:
    return datetime.fromtimestamp(ts_ns / 1e9, UTC).replace(microsecond=0).isoformat()


def fmt_int(value: int | None) -> str:
    """Thousands-separated integer (``123,456,789``) or ``-`` — the only way integers appear
    in docs / console output (guard G4)."""
    return "-" if value is None else f"{value:,}"


def fmt_bytes_mb(value: int) -> str:
    return f"{value / 1e6:,.1f} MB"


def dataset_dir(dataset: str) -> str:
    """Directory name under the source root for a contract dataset label."""
    try:
        return DATASET_DIRS[dataset]
    except KeyError:
        raise KeyError(f"unknown dataset {dataset!r}; known: {sorted(DATASET_DIRS)}") from None


def resolve_dataset(name: str) -> str:
    """Accept a dataset label or its directory name; return the label."""
    if name in DATASET_DIRS:
        return name
    for label, dirname in DATASET_DIRS.items():
        if name == dirname:
            return label
    raise KeyError(
        f"unknown dataset {name!r}; expected one of {sorted(DATASET_DIRS)} or a directory name "
        f"{sorted(DATASET_DIRS.values())}"
    )


def _git_sha() -> str | None:
    root = config.repo_root()
    if root is None:
        return None
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    sha = proc.stdout.strip()
    return sha if proc.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}", sha) else None


def _mimic_code_sha() -> str | None:
    try:
        from mimicwarehouse.concepts import vendor_info

        return vendor_info().sha
    except Exception:  # pragma: no cover — not vendored / malformed pin
        return None


def _duckdb_version() -> str:
    import duckdb

    return duckdb.__version__


def _contract() -> Contract:
    from mimicwarehouse.schema.contract import load_contract

    return load_contract()


# ---------------------------------------------------------------------------
# Per-file inventory
# ---------------------------------------------------------------------------


def sha256_file(path: Path) -> tuple[str, float]:
    """Streaming SHA-256 of ``path`` → ``(hex digest, seconds)``."""
    t0 = time.perf_counter()
    with path.open("rb") as f:
        digest = hashlib.file_digest(f, "sha256")
    return digest.hexdigest(), time.perf_counter() - t0


def read_header(path: Path) -> list[str]:
    """Column names from the first line only (UTF-8, BOM-tolerant, CRLF-tolerant)."""
    with path.open("rb") as f:
        first = f.readline()
    text = first.decode("utf-8-sig", errors="replace").rstrip("\r\n")
    if not text:
        return []
    return next(csv.reader([text]))


def compare_header(header: Sequence[str], table: Table) -> tuple[bool, list[str], list[str]]:
    """``(matches, missing_columns, extra_columns)`` against the contract's column order."""
    expected = list(table.column_names)
    got = list(header)
    matches = got == expected
    missing = [c for c in expected if c not in got]
    extra = [c for c in got if c not in expected]
    return matches, missing, extra


def open_connection(settings: Settings | None = None) -> duckdb.DuckDBPyConnection:
    """In-memory DuckDB configured with ``duckdb_settings("build")`` (DESIGN §6: explicit
    memory_limit / threads / temp_directory / max_temp_directory_size / insertion order)."""
    import duckdb

    settings = settings or config.get_settings()
    cfg: dict[str, Any] = dict(settings.duckdb_settings("build"))
    return duckdb.connect(database=":memory:", config=cfg)


def count_rows(
    path: Path, connection: duckdb.DuckDBPyConnection
) -> tuple[int | None, RowcountMethod, bool, float, str | None]:
    """``SELECT count(*) FROM read_csv(...)`` → ``(rows, method, parallel_fallback, seconds,
    error)``. The parallel reader is tried first; on any DuckDB error the file is re-read with
    ``parallel=false`` (recorded as ``csv_parallel_fallback``); a second failure records
    ``rows=None`` with the error text."""
    import duckdb

    t0 = time.perf_counter()
    sql = f"SELECT count(*) FROM read_csv(?, {CSV_READ_OPTIONS})"
    try:
        (n,) = connection.execute(sql, [str(path)]).fetchone()  # type: ignore[misc]
        return int(n), "duckdb", False, time.perf_counter() - t0, None
    except duckdb.Error as exc:
        first_error = f"{type(exc).__name__}: {exc}"
    sql_serial = f"SELECT count(*) FROM read_csv(?, {CSV_READ_OPTIONS}, parallel=false)"
    try:
        (n,) = connection.execute(sql_serial, [str(path)]).fetchone()  # type: ignore[misc]
        return int(n), "duckdb_serial", True, time.perf_counter() - t0, None
    except duckdb.Error as exc:
        error = f"parallel: {first_error}; serial: {type(exc).__name__}: {exc}"
        return None, "failed", True, time.perf_counter() - t0, _one_line(error)


def _one_line(text: str, limit: int = 400) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 3] + "..."


def parse_sha256sums(path: Path) -> dict[str, str]:
    """``SHA256SUMS.txt`` → ``{listed name: sha256}`` (``<hex>  <name>`` lines; blanks and
    comments ignored). Only file names and hashes are read."""
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        digest, name = parts
        name = name.strip().lstrip("*").replace("\\", "/")
        if re.fullmatch(r"[0-9a-fA-F]{64}", digest):
            out[name] = digest.lower()
    return out


def gz_sha256_for(sums: dict[str, str], csv_path: str) -> str | None:
    """The archive hash for ``hosp/admissions.csv`` (looked up as ``….csv.gz``, with or without
    the module directory)."""
    gz = csv_path + ".gz"
    if gz in sums:
        return sums[gz]
    base = PurePosixPath(gz).name
    return sums.get(base)


def inventory_file(
    path: Path,
    table: Table,
    *,
    rel_path: str | None = None,
    rowcount: bool = True,
    connection: duckdb.DuckDBPyConnection | None = None,
    gz_sha256: str | None = None,
    known_sha256: tuple[str, float] | None = None,
) -> FileRecord:
    """Inventory one raw CSV against its contract table.

    ``rel_path`` defaults to ``<dataset-dir>/<csv_path>``; ``known_sha256=(hex, seconds)`` reuses
    a hash from a previous manifest line whose ``(bytes, mtime)`` still match (``--resume`` after
    a ``--no-rowcount`` pass); ``connection`` is the DuckDB connection to count with (opened from
    settings when omitted and ``rowcount`` is on).
    """
    st = path.stat()
    if known_sha256 is not None:
        digest, seconds_hash = known_sha256
    else:
        digest, seconds_hash = sha256_file(path)
    header = read_header(path)
    matches, missing, extra = compare_header(header, table)
    rows: int | None = None
    method: RowcountMethod = "skipped"
    fallback = False
    seconds_rows = 0.0
    error: str | None = None
    if rowcount:
        con = connection if connection is not None else open_connection()
        rows, method, fallback, seconds_rows, error = count_rows(path, con)
    ds_dir = dataset_dir(table.dataset)
    return FileRecord(
        dataset=table.dataset,
        dataset_dir=ds_dir,
        module=PurePosixPath(table.csv_path).parts[0],
        schema_name=table.schema_name,
        table=table.name,
        rel_path=rel_path or f"{ds_dir}/{table.csv_path}",
        bytes=st.st_size,
        mtime=_iso(st.st_mtime_ns),
        mtime_ns=st.st_mtime_ns,
        sha256=digest,
        header=list(header),
        header_matches_contract=matches,
        missing_columns=missing,
        extra_columns=extra,
        rows=rows,
        rowcount_method=method,
        rowcount_error=error,
        csv_parallel_fallback=fallback,
        seconds_hash=round(seconds_hash, 3),
        seconds_rows=round(seconds_rows, 3),
        physionet_gz_sha256=gz_sha256,
        recorded_at=_now(),
    )


# ---------------------------------------------------------------------------
# Manifest store
# ---------------------------------------------------------------------------


def manifest_dir(settings: Settings | None = None) -> Path:
    """``<data_root>/lake/manifests/raw`` (created by :func:`ensure_manifest_dir`)."""
    settings = settings or config.get_settings()
    return settings.layout["lake_manifests"] / RAW_DIRNAME


def ensure_manifest_dir(settings: Settings | None = None) -> Path:
    d = manifest_dir(settings)
    d.mkdir(parents=True, exist_ok=True)
    return d


def dataset_manifest_path(root: Path, dataset: str) -> Path:
    return root / f"{dataset_dir(dataset)}.jsonl"


def _atomic_write_text(path: Path, text: str, *, retries: int = 20) -> None:
    """Write ``path`` via a temp file + ``os.replace``. On Windows the replace fails with
    ``PermissionError`` while another process (``mwh inventory show``) has the target open for
    the few milliseconds it takes to read it, so retry briefly before giving up."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    for attempt in range(retries):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == retries - 1:
                raise
            time.sleep(0.05 * (attempt + 1))


def write_dataset_manifest(root: Path, dataset: str, records: Iterable[FileRecord]) -> Path:
    """Rewrite ``<dataset-dir>.jsonl`` atomically: one canonical JSON line per file, sorted by
    ``rel_path`` (41 short lines — rewriting is cheaper than de-duplicating appends)."""
    path = dataset_manifest_path(root, dataset)
    lines = [
        json.dumps(r.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        for r in sorted(records, key=lambda r: r.rel_path)
    ]
    _atomic_write_text(path, "\n".join(lines) + ("\n" if lines else ""))
    return path


def read_dataset_manifest(path: Path) -> list[FileRecord]:
    """Parse one JSONL manifest (last line wins per ``rel_path``; blank lines ignored)."""
    if not path.is_file():
        return []
    by_rel: dict[str, FileRecord] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = FileRecord.model_validate_json(line)
        by_rel[rec.rel_path] = rec
    return list(by_rel.values())


def load_raw_manifest(settings: Settings | None = None, root: Path | None = None) -> RawManifest:
    """The manifest store: every ``<dataset-dir>.jsonl`` under ``root`` (default
    :func:`manifest_dir`) plus ``raw_snapshot.json`` (``{}`` when absent)."""
    root = root if root is not None else manifest_dir(settings)
    manifest = RawManifest(root=root)
    if not root.is_dir():
        return manifest
    for dataset in DATASET_DIRS:
        for rec in read_dataset_manifest(dataset_manifest_path(root, dataset)):
            manifest.records[rec.rel_path] = rec
    snap = root / SNAPSHOT_FILENAME
    if snap.is_file():
        manifest.snapshot = json.loads(snap.read_text(encoding="utf-8"))
    return manifest


def canonical_tuples(records: Iterable[FileRecord]) -> list[tuple[str, int, str, int | None]]:
    return sorted((r.rel_path, r.bytes, r.sha256, r.rows) for r in records)


def compute_snapshot_id(
    records: Iterable[FileRecord], *, files_expected: int = FILES_EXPECTED
) -> str | None:
    """sha256 over the sorted canonical ``(rel_path, bytes, sha256, rows)`` tuples — independent
    of processing order — or ``None`` until ``files_expected`` files are present."""
    tuples = canonical_tuples(records)
    if len(tuples) < files_expected:
        return None
    payload = json.dumps(tuples, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def raw_snapshot_id(settings: Settings | None = None, root: Path | None = None) -> str | None:
    """The current raw snapshot id (recomputed from the manifest lines; ``None`` until complete)."""
    manifest = load_raw_manifest(settings, root)
    return compute_snapshot_id(manifest.records.values())


def _dataset_totals(records: Iterable[FileRecord]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for label, dirname in DATASET_DIRS.items():
        recs = [r for r in records if r.dataset == label]
        rows_known = all(r.rows is not None for r in recs)
        out[label] = {
            "dir": dirname,
            "files_done": len(recs),
            "bytes": sum(r.bytes for r in recs),
            "rows": sum(r.rows or 0 for r in recs) if recs and rows_known else None,
            "seconds_hash": round(sum(r.seconds_hash for r in recs), 3),
            "seconds_rows": round(sum(r.seconds_rows for r in recs), 3),
            "header_ok": sum(1 for r in recs if r.header_matches_contract),
        }
    return out


def write_snapshot(
    root: Path,
    records: Iterable[FileRecord],
    *,
    job: dict[str, Any] | None = None,
    previous: dict[str, Any] | None = None,
    files_expected: int = FILES_EXPECTED,
    versions: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Write ``raw_snapshot.json`` and return it. ``job`` carries the current build's status
    (``started``, ``finished``, ``last_file``, ``errors``, ``pid``, ``options``); ``previous`` is
    the snapshot read at start (its ``runs`` history and versions are kept when not re-supplied).
    """
    recs = list(records)
    prev = previous or {}
    job = job or {}
    versions = versions or {
        k: prev.get(k)
        for k in ("duckdb_version", "python_version", "git_sha", "mimic_code_sha", "contract_hash")
    }
    per_dataset = _dataset_totals(recs)
    expected_per_dataset = prev.get("files_expected_per_dataset") or {}
    for label, info in per_dataset.items():
        info["files_expected"] = expected_per_dataset.get(label)
    snapshot: dict[str, Any] = {
        "raw_snapshot_id": compute_snapshot_id(recs, files_expected=files_expected),
        "files_expected": files_expected,
        "files_done": len(recs),
        "files_expected_per_dataset": expected_per_dataset,
        "started": job.get("started", prev.get("started")),
        "finished": job.get("finished", prev.get("finished")),
        "last_file": job.get("last_file", prev.get("last_file")),
        "errors": job.get("errors", prev.get("errors", [])),
        "pid": job.get("pid", prev.get("pid")),
        "hostname": job.get("hostname", prev.get("hostname")),
        "options": job.get("options", prev.get("options")),
        **versions,
        "datasets": per_dataset,
        "canonical": "sha256(json(sorted (rel_path, bytes, sha256, rows)))",
        "runs": job.get("runs", prev.get("runs", [])),
        "updated_at": _now(),
    }
    _atomic_write_text(
        root / SNAPSHOT_FILENAME, json.dumps(snapshot, indent=2, sort_keys=False) + "\n"
    )
    return snapshot


# ---------------------------------------------------------------------------
# Planning & build
# ---------------------------------------------------------------------------


def plan_files(
    contract: Contract, source_root: Path, *, datasets: Iterable[str] | None = None
) -> list[PlannedFile]:
    """Resolve every contract table to ``<source_root>/<dataset-dir>/<csv_path>``."""
    wanted = {resolve_dataset(d) for d in datasets} if datasets else set(DATASET_DIRS)
    out: list[PlannedFile] = []
    for t in contract.tables:
        if t.dataset not in wanted:
            continue
        ds_dir = dataset_dir(t.dataset)
        path = source_root / ds_dir / Path(*t.csv_path.split("/"))
        rel = f"{ds_dir}/{t.csv_path}"
        try:
            st = path.stat()
            exists, size, mtime_ns = True, st.st_size, st.st_mtime_ns
        except OSError:
            exists, size, mtime_ns = False, 0, 0
        out.append(
            PlannedFile(
                table_qn=t.qualified_name,
                dataset=t.dataset,
                dataset_dir=ds_dir,
                csv_path=t.csv_path,
                rel_path=rel,
                path=path,
                exists=exists,
                bytes=size,
                mtime_ns=mtime_ns,
            )
        )
    return out


class _Log:
    """Progress lines to stdout and, optionally, an append-only log file (ASCII, timestamped)."""

    def __init__(self, path: Path | None, *, quiet: bool = False) -> None:
        self.path = path
        self.quiet = quiet
        self._fh = None
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = path.open("a", encoding="utf-8")

    def __call__(self, message: str) -> None:
        line = f"{_now()} {message}"
        if not self.quiet:
            print(line, flush=True)
        if self._fh is not None:
            self._fh.write(line + "\n")
            self._fh.flush()

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None


def build_inventory(
    settings: Settings | None = None,
    *,
    datasets: Iterable[str] | None = None,
    max_bytes: int | None = None,
    force: bool = False,
    rowcount: bool = True,
    log_path: Path | None = None,
    quiet: bool = False,
    contract: Contract | None = None,
    manifest_root: Path | None = None,
) -> BuildResult:
    """Walk the contract's CSVs under ``settings.source_root``, inventory the ones that need it,
    and keep the manifest store current after **every** file (resumable; D-42).

    Skips files whose ``(bytes, mtime)`` already match a manifest line unless ``force``; a line
    whose hash is current but whose rows were never counted is completed with a row count only.
    Files are processed sequentially, smallest first (one NVMe; DuckDB threads do the
    parallelism). Raises :class:`~mimicwarehouse.config.DiskGuardError` below the free-space
    guard and :class:`FileNotFoundError` when the source root is missing.
    """
    settings = settings or config.get_settings()
    config.require_free_space(settings.data_root, settings.min_free_gb)
    source_root = settings.source_root
    if not source_root.is_dir():
        raise FileNotFoundError(f"source root {source_root} does not exist")
    contract = contract or _contract()
    root = manifest_root if manifest_root is not None else ensure_manifest_dir(settings)
    root.mkdir(parents=True, exist_ok=True)
    manifest = load_raw_manifest(settings, root)
    log = _Log(log_path, quiet=quiet)
    result = BuildResult(started=_now())
    t_start = time.perf_counter()

    planned = plan_files(contract, source_root, datasets=datasets)
    expected_per_dataset = {
        label: sum(1 for t in contract.tables if t.dataset == label) for label in DATASET_DIRS
    }
    result.missing = [p.rel_path for p in planned if not p.exists]
    todo: list[tuple[PlannedFile, tuple[str, float] | None]] = []
    for p in sorted(planned, key=lambda p: (p.bytes, p.rel_path)):
        if not p.exists:
            continue
        if max_bytes is not None and p.bytes > max_bytes:
            result.filtered.append(p.rel_path)
            continue
        prev = manifest.records.get(p.rel_path)
        if prev is not None and not force and prev.matches_stat(p.bytes, p.mtime_ns):
            if prev.rows is not None or not rowcount:
                result.skipped.append(p.rel_path)
                continue
            todo.append((p, (prev.sha256, prev.seconds_hash)))  # hash current; count rows only
        else:
            todo.append((p, None))

    versions = {
        "duckdb_version": _duckdb_version(),
        "python_version": sys.version.split()[0],
        "git_sha": _git_sha(),
        "mimic_code_sha": _mimic_code_sha(),
        "contract_hash": contract.content_hash(),
    }
    options = {
        "datasets": sorted(resolve_dataset(d) for d in datasets) if datasets else None,
        "max_bytes": max_bytes,
        "force": force,
        "rowcount": rowcount,
        "log": str(log_path) if log_path else None,
    }
    job: dict[str, Any] = {
        "started": result.started,
        "finished": None,
        "last_file": None,
        "errors": [],
        "pid": os.getpid(),
        "hostname": socket.gethostname(),
        "options": options,
        "runs": list(manifest.snapshot.get("runs", [])),
    }
    previous = dict(manifest.snapshot)
    previous["files_expected_per_dataset"] = expected_per_dataset

    def flush() -> dict[str, Any]:
        return write_snapshot(
            root, manifest.records.values(), job=job, previous=previous, versions=versions
        )

    log(
        f"inventory build: {len(todo)} to process, {len(result.skipped)} up to date, "
        f"{len(result.filtered)} over --max-bytes, {len(result.missing)} missing; "
        f"source root {source_root}; manifest {root}"
    )
    for rel in result.missing:
        log(f"missing: {rel}")
    flush()

    sums_cache: dict[str, dict[str, str]] = {}
    con: duckdb.DuckDBPyConnection | None = None
    try:
        if rowcount and todo:
            con = open_connection(settings)
        for i, (p, known) in enumerate(todo, start=1):
            table = contract.table(p.table_qn)
            if p.dataset not in sums_cache:
                sums_cache[p.dataset] = parse_sha256sums(
                    source_root / p.dataset_dir / SHA256SUMS_NAME
                )
            gz = gz_sha256_for(sums_cache[p.dataset], p.csv_path)
            t0 = time.perf_counter()
            try:
                rec = inventory_file(
                    p.path,
                    table,
                    rel_path=p.rel_path,
                    rowcount=rowcount,
                    connection=con,
                    gz_sha256=gz,
                    known_sha256=known,
                )
            except OSError as exc:
                err = {"rel_path": p.rel_path, "stage": "hash", "error": _one_line(str(exc))}
                job["errors"].append(err)
                result.errors.append(err)
                log(f"[{i}/{len(todo)}] ERROR {p.rel_path}: {err['error']}")
                flush()
                continue
            manifest.records[p.rel_path] = rec
            write_dataset_manifest(root, p.dataset, manifest.by_dataset(p.dataset))
            job["last_file"] = p.rel_path
            result.processed.append(p.rel_path)
            if rec.rowcount_error:
                err = {"rel_path": p.rel_path, "stage": "rows", "error": rec.rowcount_error}
                job["errors"].append(err)
                result.errors.append(err)
            flush()
            wall = time.perf_counter() - t0
            rate = f"{rec.mb_per_s:,.0f} MB/s" if rec.mb_per_s else "reused"
            rows_txt = (
                f"rows {fmt_int(rec.rows)} in {rec.seconds_rows:.1f}s"
                + (" (serial fallback)" if rec.csv_parallel_fallback else "")
                if rec.rows is not None
                else f"rows {rec.rowcount_method}"
            )
            log(
                f"[{i}/{len(todo)}] {p.rel_path}  {fmt_bytes_mb(rec.bytes)}  "
                f"sha256 {rec.sha256[:12]} in {rec.seconds_hash:.1f}s ({rate})  {rows_txt}  "
                f"header {rec.header_status}  wall {wall:.1f}s"
            )
    finally:
        if con is not None:
            con.close()
        result.finished = _now()
        result.seconds = round(time.perf_counter() - t_start, 3)
        job["finished"] = result.finished
        runs = list(job["runs"])
        runs.append(
            {
                "started": result.started,
                "finished": result.finished,
                "seconds": result.seconds,
                "processed": len(result.processed),
                "skipped": len(result.skipped),
                "errors": len(result.errors),
                "pid": job["pid"],
                "options": options,
            }
        )
        job["runs"] = runs[-MAX_RUN_HISTORY:]
        snapshot = flush()
        result.raw_snapshot_id = snapshot["raw_snapshot_id"]
        result.files_done = snapshot["files_done"]
        log(
            f"inventory build finished: {len(result.processed)} processed, "
            f"{len(result.errors)} error(s), {result.files_done}/{FILES_EXPECTED} files in "
            f"manifest, raw_snapshot_id {result.raw_snapshot_id or 'None (incomplete)'}, "
            f"{result.seconds:.1f}s"
        )
        log.close()
    return result


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------

_VALIDATE_RE = re.compile(
    r"'(?P<tbl>[A-Za-z_][A-Za-z0-9_]*)'\s+AS\s+tbl\s*,\s*(?P<n>\d+)\s+AS\s+row_count",
    re.IGNORECASE,
)


def parse_validate_sql(path: Path) -> dict[str, int]:
    """``{table: expected rows}`` from a mimic-code ``validate.sql`` (the ``'tbl' … <int> AS
    row_count`` pairs of the ``expected`` CTE; the ``count(*)`` lines of ``observed`` and any
    trailing ``-- mwh-guard: allow`` comment are ignored)."""
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    out: dict[str, int] = {}
    for m in _VALIDATE_RE.finditer(text):
        out[m.group("tbl").lower()] = int(m.group("n"))
    return out


def expected_counts(
    dataset: str = "mimic-iv-3.1", contract: Contract | None = None
) -> dict[str, int]:
    """Expected row counts for a dataset from the vendored ``validate.sql`` files the contract's
    ``expected_rows_source`` names (empty for MIMIC-IV-Note, which has none)."""
    from mimicwarehouse.concepts import vendored_path

    contract = contract or _contract()
    label = resolve_dataset(dataset)
    sources = sorted(
        {t.expected_rows_source for t in contract.by_dataset(label) if t.expected_rows_source}
    )
    out: dict[str, int] = {}
    for src in sources:
        out.update(parse_validate_sql(vendored_path(src)))
    return out


def reconcile(manifest: RawManifest, contract: Contract | None = None) -> list[ReconRow]:
    """One :class:`ReconRow` per contract table: expected (validate.sql) vs observed (manifest)."""
    contract = contract or _contract()
    expected_by_dataset = {d: expected_counts(d, contract) for d in DATASET_DIRS}
    rows: list[ReconRow] = []
    for t in contract.tables:
        expected = expected_by_dataset[t.dataset].get(t.name) if t.expected_rows_source else None
        rec = manifest.records.get(f"{dataset_dir(t.dataset)}/{t.csv_path}")
        observed = rec.rows if rec is not None else None
        status: ReconStatus
        delta: int | None = None
        if expected is None:
            status = "no-expectation"
        elif observed is None:
            status = "pending"
        else:
            delta = observed - expected
            status = "match" if delta == 0 else "mismatch"
        rows.append(
            ReconRow(
                dataset=t.dataset,
                schema_name=t.schema_name,
                table=t.name,
                expected=expected,
                observed=observed,
                delta=delta,
                status=status,
                source=t.expected_rows_source,
            )
        )
    return rows


def _fmt_delta(delta: int | None) -> str:
    if delta is None:
        return "-"
    return f"{delta:+,}"


def docs_path() -> Path:
    """``mimicwarehouse/docs/resources/raw-inventory.md`` (the directory is created on write)."""
    return config.workspace_root() / Path(*DOCS_RELPATH.parts)


def render_docs(manifest: RawManifest, recon: Sequence[ReconRow], contract: Contract) -> str:
    """The markdown manifest page: hashes / bytes / rows / header status per file plus the
    reconciliation table. Every integer is thousands-separated (guard G4); no data."""
    snap = manifest.snapshot
    sid = compute_snapshot_id(manifest.records.values())
    done = manifest.files_done
    complete = done >= FILES_EXPECTED
    n_match = sum(1 for r in recon if r.status == "match")
    n_mismatch = sum(1 for r in recon if r.status == "mismatch")
    n_noexp = sum(1 for r in recon if r.status == "no-expectation")
    n_pending = sum(1 for r in recon if r.status == "pending")
    lines: list[str] = []
    a = lines.append
    a("# Raw inventory manifest (EP-10, D-26)")
    a("")
    a(
        "Locally computed provenance of the raw PhysioNet CSVs under `source material/`: "
        "SHA-256, byte size, header check against the schema contract (EP-9) and DuckDB row "
        "count per file, reconciled against the row counts in mimic-code's vendored "
        "`validate.sql` (EP-8). Generated by `mwh inventory reconcile` from the manifest store "
        "under `<data_root>/lake/manifests/raw/`. This page holds hashes, counts and column "
        "names only (GOVERNANCE section 3); it contains no data."
    )
    a("")
    status = "complete" if complete else f"partial ({done} of {FILES_EXPECTED} files)"
    a(f"- **Status:** {status}")
    a(f"- **raw_snapshot_id:** `{sid}`" if sid else "- **raw_snapshot_id:** none (incomplete)")
    a(f"- **Files in manifest:** {done} / {FILES_EXPECTED}")
    a(f"- **Generated:** {_now()}")
    duck, py = snap.get("duckdb_version") or "-", snap.get("python_version") or "-"
    git, mc = snap.get("git_sha") or "-", snap.get("mimic_code_sha") or "-"
    started, finished = snap.get("started") or "-", snap.get("finished") or "-"
    a(f"- **DuckDB:** {duck} · **Python:** {py}")
    a(f"- **git sha:** `{git}` · **mimic-code sha:** `{mc}`")
    a(f"- **contract hash:** `{snap.get('contract_hash') or contract.content_hash()}`")
    a(f"- **Last build:** started {started}, finished {finished}")
    a(
        f"- **Reconciliation:** {n_match} match · {n_mismatch} mismatch · {n_noexp} no-expectation"
        f" · {n_pending} pending"
    )
    a("")
    a("## Reconciliation against validate.sql")
    a("")
    a(
        "`expected` is the row count pinned by the vendored mimic-code `validate.sql` "
        "(MIMIC-IV 3.1 for hosp/icu, MIMIC-IV-ED 2.2 for ed); tables without an upstream "
        "expectation (provider, caregiver, ingredientevents, all of MIMIC-IV-Note) are "
        "`no-expectation`; `pending` means the file has not been inventoried (or rows not "
        "counted) yet."
    )
    a("")
    a("| dataset | table | expected | observed | delta | status |")
    a("|---|---|---:|---:|---:|---|")
    for r in recon:
        a(
            f"| {r.dataset} | {r.schema_name}.{r.table} | {fmt_int(r.expected)} | "
            f"{fmt_int(r.observed)} | {_fmt_delta(r.delta)} | {r.status} |"
        )
    a("")
    a("## Files")
    a("")
    a(
        "`header` compares the CSV header line with the contract's column list (`ok` = same "
        "names in the same order); `physionet .csv.gz sha256` is the archive hash from the "
        "dataset's `SHA256SUMS.txt` (the archives themselves were deleted after decompression; "
        "re-verification is parked as RAW-1)."
    )
    a("")
    a("| dataset | file | bytes | rows | header | sha256 | physionet .csv.gz sha256 |")
    a("|---|---|---:|---:|---|---|---|")
    for label in DATASET_DIRS:
        for rec in manifest.by_dataset(label):
            hdr = rec.header_status
            if hdr == "mismatch":
                hdr = (
                    "mismatch (missing: "
                    + (", ".join(rec.missing_columns) or "-")
                    + "; extra: "
                    + (", ".join(rec.extra_columns) or "-")
                    + ")"
                )
            rows_txt = fmt_int(rec.rows)
            if rec.rows is not None and rec.csv_parallel_fallback:
                rows_txt += " (serial)"
            gz = f"`{rec.physionet_gz_sha256}`" if rec.physionet_gz_sha256 else "-"
            a(
                f"| {rec.dataset} | `{rec.rel_path}` | {fmt_int(rec.bytes)} | {rows_txt} | {hdr} | "
                f"`{rec.sha256}` | {gz} |"
            )
    a("")
    a("## Per-dataset totals")
    a("")
    a("| dataset | files | bytes | rows | hash seconds | rowcount seconds | header ok |")
    a("|---|---:|---:|---:|---:|---:|---:|")
    for label, info in _dataset_totals(manifest.records.values()).items():
        a(
            f"| {label} | {fmt_int(info['files_done'])} | {fmt_int(info['bytes'])} | "
            f"{fmt_int(info['rows'])} | {info['seconds_hash']:,.1f} | "
            f"{info['seconds_rows']:,.1f} | {fmt_int(info['header_ok'])} |"
        )
    a("")
    return "\n".join(lines).rstrip("\n") + "\n"  # hook-clean: single trailing newline


def write_docs(
    manifest: RawManifest, recon: Sequence[ReconRow], contract: Contract, path: Path | None = None
) -> Path:
    path = path or docs_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_docs(manifest, recon, contract), encoding="utf-8", newline="\n")
    return path


# ---------------------------------------------------------------------------
# CLI — mwh inventory build | show | reconcile
# ---------------------------------------------------------------------------

inventory_app = typer.Typer(
    name="inventory",
    help="Raw inventory manifest of the source CSVs (sha256 / bytes / header / rows) and its "
    "reconciliation against mimic-code validate.sql (EP-10, D-26). Hashes and counts only - "
    "never data.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

console = Console()
err_console = Console(stderr=True)


def _settings(ctx: typer.Context) -> Settings:
    state = ctx.obj
    settings = getattr(state, "settings", None)
    return settings if isinstance(settings, Settings) else config.get_settings()


def _fail(message: str, code: int = 2) -> None:
    err_console.print(f"[bold red]mwh inventory:[/] {escape(message)}", highlight=False)
    raise typer.Exit(code=code)


@inventory_app.command("build")
def build_command(
    ctx: typer.Context,
    dataset: Annotated[
        list[str] | None,
        typer.Option(
            "--dataset",
            help="Only this dataset (label or directory name); repeatable.",
            show_default=False,
        ),
    ] = None,
    max_bytes: Annotated[
        int | None,
        typer.Option(
            "--max-bytes", help="Skip files larger than N bytes (bounded foreground pass)."
        ),
    ] = None,
    resume: Annotated[
        bool,
        typer.Option(
            "--resume/--force",
            help="--resume (default) skips files whose bytes+mtime already match a manifest "
            "line; --force recomputes everything.",
        ),
    ] = True,
    force: Annotated[
        bool,
        typer.Option("--force", help="Recompute every file (same as --no-resume).", hidden=True),
    ] = False,
    rowcount: Annotated[
        bool,
        typer.Option("--rowcount/--no-rowcount", help="Count rows with DuckDB (default on)."),
    ] = True,
    log: Annotated[
        Path | None,
        typer.Option("--log", help="Append progress lines to this file as well as stdout."),
    ] = None,
    quiet: Annotated[bool, typer.Option("--quiet", help="No stdout progress lines.")] = False,
) -> None:
    """Hash + header-check + row-count the raw CSVs into the manifest store (resumable)."""
    settings = _settings(ctx)
    for d in dataset or []:
        try:
            resolve_dataset(d)
        except KeyError as exc:
            _fail(str(exc))
    try:
        result = build_inventory(
            settings,
            datasets=dataset or None,
            max_bytes=max_bytes,
            force=force or not resume,
            rowcount=rowcount,
            log_path=log,
            quiet=quiet,
        )
    except config.DiskGuardError as exc:
        _fail(str(exc))
        return
    except FileNotFoundError as exc:
        _fail(str(exc))
        return
    raise typer.Exit(code=0 if result.ok else 1)


def _job_lines(snapshot: dict[str, Any]) -> list[str]:
    if not snapshot:
        return ["job: no raw_snapshot.json yet (run `mwh inventory build`)"]
    errors = snapshot.get("errors") or []
    finished = snapshot.get("finished") or "- (running or interrupted)"
    git = str(snapshot.get("git_sha") or "-")[:12]
    mc = str(snapshot.get("mimic_code_sha") or "-")[:12]
    lines = [
        f"job: started {snapshot.get('started') or '-'}  finished {finished}  "
        f"pid {snapshot.get('pid') or '-'}",
        f"     last_file {snapshot.get('last_file') or '-'}  files_done "
        f"{snapshot.get('files_done', 0)}/{snapshot.get('files_expected', FILES_EXPECTED)}  "
        f"errors {len(errors)}",
        f"     duckdb {snapshot.get('duckdb_version') or '-'}  git {git}  mimic-code {mc}",
        f"     raw_snapshot_id {snapshot.get('raw_snapshot_id') or 'None (incomplete)'}",
    ]
    for e in errors[:10]:
        lines.append(f"     error [{e.get('stage')}] {e.get('rel_path')}: {e.get('error')}")
    if len(errors) > 10:
        lines.append(f"     ... and {len(errors) - 10} more error(s)")
    return lines


@inventory_app.command("show")
def show_command(
    ctx: typer.Context,
    timing: Annotated[
        bool, typer.Option("--timing", help="Seconds and MB/s per file, per-dataset totals.")
    ] = False,
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    """Show the manifest: table, bytes, rows, header ok, sha256[:12] + the job status. No data."""
    settings = _settings(ctx)
    manifest = load_raw_manifest(settings)
    contract = _contract()
    planned = plan_files(contract, settings.source_root)
    pending = [p.rel_path for p in planned if p.rel_path not in manifest.records]
    if as_json:
        payload = {
            "manifest_dir": str(manifest.root),
            "raw_snapshot_id": compute_snapshot_id(manifest.records.values()),
            "files_done": manifest.files_done,
            "files_expected": FILES_EXPECTED,
            "pending": pending,
            "files": [
                r.model_dump(mode="json")
                for label in DATASET_DIRS
                for r in manifest.by_dataset(label)
            ],
            "snapshot": manifest.snapshot,
        }
        console.print_json(json.dumps(payload))
        return
    rt = RichTable(box=box.SIMPLE, header_style="bold")
    cols = ["dataset", "table", "bytes", "rows", "header ok", "sha256[:12]"]
    if timing:
        cols += ["hash s", "MB/s", "rows s"]
    for c in cols:
        rt.add_column(
            c,
            overflow="fold",
            justify="right" if c in {"bytes", "rows", "hash s", "MB/s", "rows s"} else "left",
        )
    for label in DATASET_DIRS:
        for rec in manifest.by_dataset(label):
            row = [
                rec.dataset,
                rec.qualified_name,
                fmt_int(rec.bytes),
                fmt_int(rec.rows)
                + (" (serial)" if rec.csv_parallel_fallback and rec.rows is not None else ""),
                str(rec.header_matches_contract),
                rec.sha256[:12],
            ]
            if timing:
                row += [
                    f"{rec.seconds_hash:,.1f}",
                    f"{rec.mb_per_s:,.0f}" if rec.mb_per_s else "-",
                    f"{rec.seconds_rows:,.1f}",
                ]
            rt.add_row(*row)
    console.print(rt)
    if timing:
        tt = RichTable(box=box.SIMPLE, header_style="bold", title="per-dataset totals")
        for c in ("dataset", "files", "bytes", "rows", "hash s", "rows s", "header ok"):
            tt.add_column(c, overflow="fold", justify="left" if c == "dataset" else "right")
        for label, info in _dataset_totals(manifest.records.values()).items():
            tt.add_row(
                label,
                fmt_int(info["files_done"]),
                fmt_int(info["bytes"]),
                fmt_int(info["rows"]),
                f"{info['seconds_hash']:,.1f}",
                f"{info['seconds_rows']:,.1f}",
                fmt_int(info["header_ok"]),
            )
        console.print(tt)
    bad = [r for r in manifest.records.values() if not r.header_matches_contract]
    console.print(
        f"{manifest.files_done}/{FILES_EXPECTED} files in manifest ({len(pending)} pending, "
        f"{len(bad)} header mismatch); manifest dir {escape(str(manifest.root))}",
        highlight=False,
    )
    for r in sorted(bad, key=lambda r: r.rel_path):
        console.print(
            f"  header {r.header_status}: {r.qualified_name}  missing={r.missing_columns}  "
            f"extra={r.extra_columns}",
            highlight=False,
        )
    for line in _job_lines(manifest.snapshot):
        console.print(escape(line), highlight=False)


@inventory_app.command("reconcile")
def reconcile_command(
    ctx: typer.Context,
    docs: Annotated[
        bool,
        typer.Option(
            "--docs/--no-docs",
            help="Write docs/resources/raw-inventory.md (default on).",
        ),
    ] = True,
    docs_out: Annotated[
        Path | None,
        typer.Option("--docs-path", help="Where to write the markdown page.", show_default=False),
    ] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    """Compare manifest row counts with mimic-code validate.sql; exit 1 on any mismatch."""
    settings = _settings(ctx)
    manifest = load_raw_manifest(settings)
    contract = _contract()
    rows = reconcile(manifest, contract)
    written: Path | None = None
    if docs:
        written = write_docs(manifest, rows, contract, docs_out)
    n_mismatch = sum(1 for r in rows if r.status == "mismatch")
    if as_json:
        payload = {
            "raw_snapshot_id": compute_snapshot_id(manifest.records.values()),
            "files_done": manifest.files_done,
            "files_expected": FILES_EXPECTED,
            "rows": [r.to_dict() for r in rows],
            "summary": {
                s: sum(1 for r in rows if r.status == s)
                for s in ("match", "mismatch", "no-expectation", "pending")
            },
            "docs": str(written) if written else None,
        }
        console.print_json(json.dumps(payload))
        raise typer.Exit(code=1 if n_mismatch else 0)
    rt = RichTable(box=box.SIMPLE, header_style="bold")
    for c in ("dataset", "table", "expected", "observed", "delta", "status"):
        rt.add_column(
            c,
            overflow="fold",
            justify="right" if c in {"expected", "observed", "delta"} else "left",
        )
    for r in rows:
        rt.add_row(
            r.dataset,
            f"{r.schema_name}.{r.table}",
            fmt_int(r.expected),
            fmt_int(r.observed),
            _fmt_delta(r.delta),
            r.status,
        )
    console.print(rt)
    summary = ", ".join(
        f"{s}={sum(1 for r in rows if r.status == s)}"
        for s in ("match", "mismatch", "no-expectation", "pending")
    )
    console.print(f"{len(rows)} table(s): {summary}", highlight=False)
    if written:
        console.print(f"wrote {escape(str(written))}", highlight=False)
    raise typer.Exit(code=1 if n_mismatch else 0)


__all__ = [
    "CSV_READ_OPTIONS",
    "DATASET_DIRS",
    "DOCS_RELPATH",
    "FILES_EXPECTED",
    "RAW_DIRNAME",
    "SNAPSHOT_FILENAME",
    "BuildResult",
    "FileRecord",
    "PlannedFile",
    "RawManifest",
    "ReconRow",
    "build_inventory",
    "compare_header",
    "compute_snapshot_id",
    "count_rows",
    "dataset_dir",
    "dataset_manifest_path",
    "docs_path",
    "ensure_manifest_dir",
    "expected_counts",
    "fmt_int",
    "gz_sha256_for",
    "inventory_app",
    "inventory_file",
    "load_raw_manifest",
    "manifest_dir",
    "open_connection",
    "parse_sha256sums",
    "parse_validate_sql",
    "plan_files",
    "raw_snapshot_id",
    "read_dataset_manifest",
    "read_header",
    "reconcile",
    "render_docs",
    "resolve_dataset",
    "sha256_file",
    "write_dataset_manifest",
    "write_docs",
    "write_snapshot",
]
