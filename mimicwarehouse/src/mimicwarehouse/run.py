"""Provenance run ledger — ``run.start(...)``, :class:`RunManifest`, ``runs/ledger.jsonl``
and the ``runs.duckdb`` ledger views (EP-35; DESIGN §11/§15, GOVERNANCE §12, D-24).

Every analysis from P3 on is reproducible from a **run id**. ``with run.start(name,
tier=..., kind=..., params=...) as r:`` assigns ``r.run_id`` (``YYYYMMDDTHHMMSSZ-<6 hex>``,
DESIGN §11 glossary), creates ``runs/<run_id>/``, captures provenance up front (git sha +
dirty flag, ``uv.lock`` sha256, DuckDB / Python / package versions, the environment block —
``doctor.run_checks`` reduced to ``{id, status, value}`` per check, captured **once per
process** because the probes cost seconds, EP-170 amendment 3) and writes a ``status:
running`` manifest so a crash still leaves a record. Inside the block the caller records
what the run read and produced — ``r.record_sql(name, sql)`` (``runs/<run_id>/sql/<name>.sql``),
``r.record_ref(kind, name, version=, hash=)``, ``r.record_attrition(rows)``,
``r.read_layer(layer)`` / ``r.record_snapshot(layer, id)`` (the ``{layer: id}`` dict every
run cites), ``r.safe_query(sql, name=...)`` (the audited wrapper plus the SQL, the ``core``
snapshot id and the audit id in one call), ``r.save_table(name, df)`` (Parquet under
``tables/``), ``r.save_figure(name, obj)`` (``figures/``), ``r.warn(msg)``, ``r.bench(...)``.
On exit the manifest is finalised (``status`` ``ok`` | ``failed``, wall time, coarse peak
RSS, free-space delta of the data-root drive, captured ``warnings.warn`` calls, the error
type + sanitized one-line message on failure) and rewritten atomically, then **one subset
line** (:data:`LEDGER_FIELDS`) is appended to ``runs/ledger.jsonl`` through
:func:`mimicwarehouse.fsio.append_jsonl` — the one JSONL writer (EP-33 B8). Exceptions mark
the run ``failed`` and re-raise.

**Manifests never contain rows.** They hold hashes, counts, parameters, paths and version
strings; SQL text lives in the ``sql/`` files (never in the manifest itself); free-text that
originates outside the project — captured warnings, exception messages — passes through
:func:`mimicwarehouse.safe.sanitize_error_text` (quoted literals and numbers masked, DKB-2)
so a value quoted by an engine error never lands in a run folder. ``save_table`` refuses
identifier columns by name (GOVERNANCE §4). Attrition counts are raw inside the data root
and go through ``disclose`` (EP-43) on any export.

**Views.** :func:`runs_db_views` returns the ``ledger`` / ``benchmarks`` / ``manifests`` /
``attrition`` view bodies that :func:`mimicwarehouse.safe.build_runs_db` creates beside the
EP-30 ``audit`` view (``mwh runs refresh``; published with ``publish.swap_file``). They read
the JSONL / JSON files with explicit column types (the dict-valued manifest fields as
``JSON``), so older lines without a newer field bind as NULL, a torn trailing line becomes
an all-NULL row that the ``IS NOT NULL`` filter drops (LGR-1), and an empty ledger or a
run root without manifests still yields a typed, empty view.

:func:`reproduction_block` renders the Markdown **Reproduction + Provenance** block of
``docs/analyses/*`` (EP-32 convention) from a run id — the capstones call it instead of
hand-writing one; it quotes the run id inline only (never in a file name, committed-text
rule 3).

Seeds and the resource sampler (``seeds`` / ``resources``) are EP-36's; the fields are
optional here. Import budget: not on the ``mwh`` start-up path (``tracer.py`` and
``runs_cli.py`` import it inside function bodies); psutil, duckdb-adjacent modules and
``safe`` load lazily.
"""

from __future__ import annotations

import hashlib
import json
import logging
import platform
import re
import secrets
import shutil
import subprocess
import sys
import time
import warnings
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from importlib.metadata import version as dist_version
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field

from mimicwarehouse import __version__, config, fsio
from mimicwarehouse.config import Settings, get_settings

if TYPE_CHECKING:  # pragma: no cover
    from mimicwarehouse.safe import SafeResult

_LOG = logging.getLogger(__name__)

LEDGER_FILENAME = "ledger.jsonl"
MANIFEST_FILENAME = "manifest.json"
SQL_DIRNAME = "sql"
TABLES_DIRNAME = "tables"
FIGURES_DIRNAME = "figures"

#: ``YYYYMMDDTHHMMSSZ-<6 hex>`` — safe in ledger *content* (guard ``ID_TOKEN`` cannot match
#: a digit run abutting ``T``), never in a committed file name (committed-text rule 3).
RUN_ID_RE = re.compile(r"^\d{8}T\d{6}Z-[0-9a-f]{6}$")
#: Names of recorded SQL / tables / figures: one path segment, no separators.
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")

RunKind = Literal["analysis", "cohort", "build", "qc", "phenotype", "protocol", "report", "bench"]
RUN_KINDS: tuple[str, ...] = get_args(RunKind)
RunStatus = Literal["running", "ok", "failed"]
RUN_STATUSES: tuple[str, ...] = get_args(RunStatus)
TIERS: tuple[str, ...] = ("fixture", "demo", "dev", "full")

#: The ``runs/ledger.jsonl`` line — a subset of the manifest (brief item 2).
LEDGER_FIELDS: tuple[str, ...] = (
    "run_id",
    "name",
    "kind",
    "tier",
    "status",
    "started",
    "wall_s",
    "git_sha",
    "protocol_hash",
)

#: Longest warning / error text kept in a manifest (after sanitising).
TEXT_MAX_CHARS = 200


class RunLedgerError(RuntimeError):
    """A run-ledger usage error (bad tier / name / run id, missing manifest)."""


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class AttritionRow(BaseModel):
    """One attrition step: a label and the unit / subject counts left after it (raw inside
    the data root; suppressed by ``disclose`` on export)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    step: str
    label: str
    n_units: int | None = None
    n_subjects: int | None = None


class RefEntry(BaseModel):
    """A versioned thing the run depended on (code set, phenotype, cohort, protocol,
    concept, model ...): ``kind`` + ``name`` + optional ``version`` / ``hash``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: str
    name: str
    version: str | None = None
    hash: str | None = None


class RunErrorInfo(BaseModel):
    """The failure record: exception type + one sanitized line, never a traceback body."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: str
    message: str


class RunManifest(BaseModel):
    """``runs/<run_id>/manifest.json`` — flat; hashes, counts, parameters, paths and
    versions only (module note). ``extra="forbid"`` so a stray field cannot smuggle a
    value in; mutable because the run fills it in as it goes."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    run_id: str
    name: str
    kind: RunKind
    tier: str
    status: RunStatus
    started: str
    finished: str | None = None
    command: str | None = None
    git_sha: str | None = None
    git_dirty: bool | None = None
    uv_lock_sha256: str | None = None
    duckdb_version: str
    python_version: str
    package_version: str
    params: dict[str, Any] = Field(default_factory=dict)
    snapshot_ids: dict[str, str] = Field(default_factory=dict)
    refs: list[RefEntry] = Field(default_factory=list)
    sql: dict[str, str] = Field(default_factory=dict)
    tables: dict[str, str] = Field(default_factory=dict)
    figures: dict[str, str] = Field(default_factory=dict)
    attrition: list[AttritionRow] = Field(default_factory=list)
    audit_ids: list[str] = Field(default_factory=list)
    seeds: dict[str, int] | None = None
    resources: dict[str, Any] | None = None
    warnings: list[str] = Field(default_factory=list)
    wall_s: float | None = None
    peak_rss_mb: float | None = None
    disk_delta_mb: float | None = None
    protocol_id: str | None = None
    protocol_hash: str | None = None
    claim_type: str | None = None
    error: RunErrorInfo | None = None
    doctor: dict[str, Any] | None = None

    def ledger_line(self) -> dict[str, Any]:
        """The :data:`LEDGER_FIELDS` subset appended to ``runs/ledger.jsonl``."""
        dumped = self.model_dump(mode="json")
        return {key: dumped[key] for key in LEDGER_FIELDS}


#: DuckDB column types of the ``manifests`` view (one entry per :class:`RunManifest`
#: field — ``test_ep35`` pins the parity); dict / list fields bind as ``JSON``.
MANIFEST_COLUMNS: dict[str, str] = {
    "run_id": "VARCHAR",
    "name": "VARCHAR",
    "kind": "VARCHAR",
    "tier": "VARCHAR",
    "status": "VARCHAR",
    "started": "VARCHAR",
    "finished": "VARCHAR",
    "command": "VARCHAR",
    "git_sha": "VARCHAR",
    "git_dirty": "BOOLEAN",
    "uv_lock_sha256": "VARCHAR",
    "duckdb_version": "VARCHAR",
    "python_version": "VARCHAR",
    "package_version": "VARCHAR",
    "params": "JSON",
    "snapshot_ids": "JSON",
    "refs": "JSON",
    "sql": "JSON",
    "tables": "JSON",
    "figures": "JSON",
    "attrition": "JSON",
    "audit_ids": "JSON",
    "seeds": "JSON",
    "resources": "JSON",
    "warnings": "JSON",
    "wall_s": "DOUBLE",
    "peak_rss_mb": "DOUBLE",
    "disk_delta_mb": "DOUBLE",
    "protocol_id": "VARCHAR",
    "protocol_hash": "VARCHAR",
    "claim_type": "VARCHAR",
    "error": "JSON",
    "doctor": "JSON",
}

#: Column types of the ``ledger`` view (:data:`LEDGER_FIELDS`).
LEDGER_COLUMNS: dict[str, str] = {
    "run_id": "VARCHAR",
    "name": "VARCHAR",
    "kind": "VARCHAR",
    "tier": "VARCHAR",
    "status": "VARCHAR",
    "started": "VARCHAR",
    "wall_s": "DOUBLE",
    "git_sha": "VARCHAR",
    "protocol_hash": "VARCHAR",
}

#: Column types of the ``benchmarks`` view (``dag.benchmarks.BenchmarkLine`` since EP-35).
BENCHMARK_COLUMNS: dict[str, str] = {
    "ts": "VARCHAR",
    "build_id": "VARCHAR",
    "tier": "VARCHAR",
    "step": "VARCHAR",
    "kind": "VARCHAR",
    "phase": "VARCHAR",
    "wall_s": "DOUBLE",
    "peak_rss_mb": "DOUBLE",
    "rows": "BIGINT",
    "bytes_in": "BIGINT",
    "bytes_out": "BIGINT",
    "files": "BIGINT",
    "duckdb_version": "VARCHAR",
    "git_sha": "VARCHAR",
    "host": "STRUCT(cpu BIGINT, ram_gb DOUBLE)",
    "ok": "BOOLEAN",
    "error": "VARCHAR",
    "run_id": "VARCHAR",
    "disk_delta_mb": "DOUBLE",
}

_ATTRITION_JSON_SHAPE = (
    '[{"step":"VARCHAR","label":"VARCHAR","n_units":"BIGINT","n_subjects":"BIGINT"}]'
)


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def ledger_path(settings: Settings | None = None) -> Path:
    """``<data_root>/runs/ledger.jsonl`` (append-only; created on first run)."""
    return (settings or get_settings()).layout["runs"] / LEDGER_FILENAME


def require_run_id(run_id: str) -> str:
    """``run_id`` when it has the :data:`RUN_ID_RE` shape, else :class:`RunLedgerError`
    (also what keeps ``mwh runs show`` from resolving an arbitrary path)."""
    if not RUN_ID_RE.match(run_id):
        raise RunLedgerError(f"{run_id!r} is not a run id (expected YYYYMMDDTHHMMSSZ-<6 hex>)")
    return run_id


def run_dir(run_id: str, settings: Settings | None = None) -> Path:
    """``<data_root>/runs/<run_id>``."""
    return (settings or get_settings()).layout["runs"] / require_run_id(run_id)


def manifest_path(run_id: str, settings: Settings | None = None) -> Path:
    return run_dir(run_id, settings) / MANIFEST_FILENAME


def read_manifest(run_id: str, settings: Settings | None = None) -> RunManifest:
    """The validated manifest of ``run_id``; :class:`RunLedgerError` when absent."""
    path = manifest_path(run_id, settings)
    if not path.is_file():
        raise RunLedgerError(f"no manifest for run {run_id} ({path})")
    return RunManifest.model_validate(json.loads(path.read_text(encoding="utf-8")))


def read_ledger(settings: Settings | None = None) -> list[dict[str, Any]]:
    """Every ``runs/ledger.jsonl`` line in append order (one torn trailing line tolerated,
    LGR-1); ``[]`` when the ledger does not exist yet."""
    return fsio.read_jsonl(ledger_path(settings))


def list_runs(
    settings: Settings | None = None,
    *,
    tier: str | None = None,
    kind: str | None = None,
    last: int | None = None,
) -> list[dict[str, Any]]:
    """Ledger lines filtered by ``tier`` / ``kind``, **newest first**, at most ``last``."""
    rows = read_ledger(settings)
    if tier is not None:
        rows = [r for r in rows if r.get("tier") == tier]
    if kind is not None:
        rows = [r for r in rows if r.get("kind") == kind]
    rows.reverse()
    if last is not None:
        rows = rows[: max(last, 0)]
    return rows


# ---------------------------------------------------------------------------
# Provenance probes
# ---------------------------------------------------------------------------


def _git(*args: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", *args],
            cwd=str(config.workspace_root()),
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except OSError:
        return None
    return out.stdout if out.returncode == 0 else None


def git_sha() -> str | None:
    """The full sha of ``HEAD`` (what ``git rev-parse HEAD`` prints); None outside git."""
    out = _git("rev-parse", "HEAD")
    return out.strip() or None if out is not None else None


def git_dirty() -> bool | None:
    """Whether tracked files differ from ``HEAD`` (untracked files ignored); None outside
    git."""
    out = _git("status", "--porcelain", "--untracked-files=no")
    return None if out is None else bool(out.strip())


def uv_lock_sha256() -> str | None:
    """sha256 of the workspace ``uv.lock`` bytes (the environment hash); None when absent."""
    path = config.workspace_root() / "uv.lock"
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _duckdb_version() -> str:
    """The installed DuckDB version from package metadata (no ``import duckdb``)."""
    try:
        return dist_version("duckdb")
    except Exception:  # pragma: no cover - metadata missing in a broken env
        return "unknown"


def _rss_mb() -> float | None:
    try:
        import psutil

        return round(psutil.Process().memory_info().rss / 2**20, 1)
    except Exception:  # pragma: no cover - psutil unavailable / restricted
        return None


def _free_bytes(path: Path) -> int | None:
    try:
        return shutil.disk_usage(config.nearest_existing(path)).free
    except OSError:  # pragma: no cover
        return None


def _jsonable(value: Any) -> Any:
    """``value`` round-tripped through JSON (``default=str``) so a manifest never carries
    an unserialisable object."""
    return json.loads(json.dumps(value, default=str))


_DOCTOR_CACHE: dict[str, dict[str, Any]] = {}


def environment_block(settings: Settings, *, refresh: bool = False) -> dict[str, Any]:
    """``mwh doctor``'s checks reduced to ``{timestamp, host, ok, checks: [{id, status,
    value}]}`` — the machine-readable ``value`` payloads (versions, paths, product names),
    never the prose ``detail``. Cached per data root for the life of the process (the
    probes cost seconds; EP-170 amendment 3) unless ``refresh``."""
    key = str(settings.data_root)
    if not refresh and key in _DOCTOR_CACHE:
        return _DOCTOR_CACHE[key]
    from mimicwarehouse.doctor import doctor_report, run_checks

    report = doctor_report(run_checks(settings))
    block = {
        "timestamp": report["timestamp"],
        "host": report["host"],
        "ok": report["ok"],
        "checks": [
            {"id": c["id"], "status": c["status"], "value": _jsonable(c["value"])}
            for c in report["checks"]
        ],
    }
    _DOCTOR_CACHE[key] = block
    return block


def _default_command() -> str:
    """``<argv0 basename> <args...>`` — what started this process."""
    if not sys.argv:
        return ""
    head = Path(sys.argv[0]).name or sys.argv[0]
    return " ".join([head, *sys.argv[1:]]).strip()


def _sanitized(text: str) -> str:
    """Third-party text (exception / warning messages): first line, literals and numbers
    masked, capped (DKB-2)."""
    from mimicwarehouse.safe import sanitize_error_text

    return sanitize_error_text(text, max_chars=TEXT_MAX_CHARS)


def _one_line(text: str) -> str:
    """Project-authored text (``r.warn``): first line, whitespace collapsed, capped —
    the caller is responsible for never passing a value."""
    first = " ".join(text.strip().split("\n", 1)[0].split())
    return first if len(first) <= TEXT_MAX_CHARS else first[: TEXT_MAX_CHARS - 3] + "..."


def _new_run_dir(settings: Settings) -> tuple[str, Path]:
    runs = settings.layout["runs"]
    runs.mkdir(parents=True, exist_ok=True)
    for _ in range(50):
        run_id = f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{secrets.token_hex(3)}"
        out = runs / run_id
        try:
            out.mkdir()
        except FileExistsError:  # pragma: no cover - 6-hex collision inside one second
            continue
        return run_id, out
    raise RunLedgerError(f"cannot allocate a run folder under {runs}")  # pragma: no cover


def _require_name(name: str, what: str) -> str:
    if not NAME_RE.match(name):
        raise RunLedgerError(
            f"{what} name {name!r} must be one path segment "
            "(letters, digits, '_', '.', '-'; no separators)"
        )
    return name


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------


class Run:
    """One open run (module note). Attributes: ``run_id``, ``dir``, ``manifest``,
    ``settings``. Everything recorded lands in ``manifest`` and is written on exit."""

    def __init__(self, manifest: RunManifest, directory: Path, settings: Settings) -> None:
        self.manifest = manifest
        self.dir = directory
        self.settings = settings

    @property
    def run_id(self) -> str:
        return self.manifest.run_id

    @property
    def tier(self) -> str:
        return self.manifest.tier

    # -- recording -------------------------------------------------------------------------

    def write_manifest(self) -> Path:
        """Rewrite ``manifest.json`` atomically (:func:`fsio.atomic_write_text`)."""
        path = self.dir / MANIFEST_FILENAME
        payload = self.manifest.model_dump(mode="json")
        fsio.atomic_write_text(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")
        return path

    def record_sql(self, name: str, sql: str) -> Path:
        """Write ``sql/<name>.sql`` (UTF-8, LF) and map ``name`` to its run-relative path in
        ``manifest.sql``; the text itself never enters the manifest."""
        _require_name(name, "sql")
        rel = f"{SQL_DIRNAME}/{name}.sql"
        path = self.dir / SQL_DIRNAME / f"{name}.sql"
        path.parent.mkdir(parents=True, exist_ok=True)
        fsio.atomic_write_text(path, sql.rstrip() + "\n")
        self.manifest.sql = {**self.manifest.sql, name: rel}
        return path

    def record_ref(
        self, kind: str, name: str, *, version: str | None = None, hash: str | None = None
    ) -> RefEntry:
        """Append a :class:`RefEntry` (code-set / phenotype / cohort / protocol ids with
        version + hash; filled by the later EPs that define them)."""
        entry = RefEntry(kind=kind, name=name, version=version, hash=hash)
        self.manifest.refs = [*self.manifest.refs, entry]
        return entry

    def record_attrition(
        self, rows: Sequence[Mapping[str, Any] | AttritionRow]
    ) -> list[AttritionRow]:
        """Replace the attrition table with ``rows`` (``{step, label, n_units, n_subjects}``
        or :class:`AttritionRow`), validated."""
        validated = [
            r if isinstance(r, AttritionRow) else AttritionRow.model_validate(dict(r)) for r in rows
        ]
        self.manifest.attrition = validated
        return validated

    def record_snapshot(self, layer: str, snapshot_id: str) -> None:
        """Cite a layer snapshot id (``{layer: id}``, DESIGN §11 glossary)."""
        if not layer or not snapshot_id:
            raise RunLedgerError("record_snapshot needs a layer name and a snapshot id")
        self.manifest.snapshot_ids = {**self.manifest.snapshot_ids, layer: str(snapshot_id)}

    def read_layer(self, layer: str = "core", *, tier: str | None = None) -> str:
        """The current snapshot id of ``layer`` for this run's tier (or ``tier``), from the
        lake manifests through the EP-19 helpers — the latest ``snapshots.json`` entry, or
        recomputed with :func:`dag.snapshot.layer_snapshot` when none is recorded — and
        record it. :class:`RunLedgerError` when the tier's lake has no status file."""
        from mimicwarehouse.dag.snapshot import layer_snapshot, read_snapshots
        from mimicwarehouse.loader.manifest import status_path

        resolved_tier = tier or self.tier
        lake_root = self.settings.lake_root(resolved_tier)
        recorded = [
            e
            for e in read_snapshots(lake_root)
            if e.get("layer") == layer and e.get("tier") == resolved_tier and e.get("snapshot_id")
        ]
        if recorded:
            snapshot_id = str(max(recorded, key=lambda e: str(e.get("ts", "")))["snapshot_id"])
        elif status_path(lake_root).is_file():
            snapshot_id = layer_snapshot(lake_root, layer, resolved_tier, settings=self.settings)
        else:
            raise RunLedgerError(
                f"no lake manifests for tier {resolved_tier!r} under {lake_root} — "
                "nothing to snapshot (mwh build stages the tier first)"
            )
        self.record_snapshot(layer, snapshot_id)
        return snapshot_id

    def record_audit(self, *audit_ids: str) -> None:
        """Cite the audit ids of ``safe_query`` calls made on this run's behalf."""
        have = list(self.manifest.audit_ids)
        have.extend(a for a in audit_ids if a and a not in have)
        self.manifest.audit_ids = have

    def safe_query(self, sql: str, *, name: str, **kwargs: Any) -> SafeResult:
        """:func:`mimicwarehouse.safe.safe_query` on this run's tier (override with
        ``tier=``), recording the statement under ``name``, the queried catalog's ``core``
        snapshot id and the audit id. Refusals propagate (and the run records nothing for
        the refused call beyond its audit line, which the wrapper already wrote)."""
        from mimicwarehouse.safe import safe_query

        kwargs.setdefault("tier", self.tier)
        kwargs.setdefault("settings", self.settings)
        self.record_sql(name, sql)
        result = safe_query(sql, **kwargs)
        if result.snapshot_id:
            self.record_snapshot("core", result.snapshot_id)
        self.record_audit(result.audit_id)
        return result

    def save_table(self, name: str, df: Any) -> Path:
        """Write a polars / pandas frame as ``tables/<name>.parquet``. Refuses identifier
        columns by name (GOVERNANCE §4) — run folders hold aggregates, never rows."""
        _require_name(name, "table")
        columns = [str(c) for c in getattr(df, "columns", [])]
        from mimicwarehouse.safe import identifier_column_names

        hits = sorted(c for c in columns if c.casefold() in identifier_column_names())
        if hits:
            raise RunLedgerError(
                f"save_table({name!r}): identifier column(s) {', '.join(hits)} may not enter a "
                "run folder (GOVERNANCE §4) — aggregate first"
            )
        rel = f"{TABLES_DIRNAME}/{name}.parquet"
        path = self.dir / TABLES_DIRNAME / f"{name}.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        if hasattr(df, "write_parquet"):  # polars
            df.write_parquet(path)
        elif hasattr(df, "to_parquet"):  # pandas
            df.to_parquet(path, index=False)
        else:
            raise RunLedgerError(f"save_table({name!r}): expected a polars or pandas DataFrame")
        self.manifest.tables = {**self.manifest.tables, name: rel}
        return path

    def save_figure(self, name: str, obj: Any) -> Path:
        """Write a figure under ``figures/``: an Altair chart (``to_json`` →
        ``<name>.vl.json``), a matplotlib figure (``savefig`` → ``<name>.png``), a dict
        (Vega/JSON spec → ``<name>.json``), ``bytes`` (``<name>.png``) or SVG text
        (``<name>.svg``). Specs must carry aggregates only (the disclosure gate checks
        embedded data arrays on export, EP-43)."""
        _require_name(name, "figure")
        directory = self.dir / FIGURES_DIRNAME
        directory.mkdir(parents=True, exist_ok=True)
        if hasattr(obj, "to_json"):
            filename = f"{name}.vl.json"
            fsio.atomic_write_text(directory / filename, str(obj.to_json()).rstrip() + "\n")
        elif hasattr(obj, "savefig"):
            filename = f"{name}.png"
            obj.savefig(directory / filename)
        elif isinstance(obj, dict):
            filename = f"{name}.json"
            fsio.atomic_write_text(directory / filename, json.dumps(obj, indent=2) + "\n")
        elif isinstance(obj, bytes | bytearray):
            filename = f"{name}.png"
            (directory / filename).write_bytes(bytes(obj))
        elif isinstance(obj, str):
            filename = f"{name}.svg"
            fsio.atomic_write_text(directory / filename, obj.rstrip() + "\n")
        else:
            raise RunLedgerError(
                f"save_figure({name!r}): unsupported object {type(obj).__name__} "
                "(Altair chart, matplotlib figure, dict spec, bytes or SVG text)"
            )
        self.manifest.figures = {**self.manifest.figures, name: f"{FIGURES_DIRNAME}/{filename}"}
        return directory / filename

    def warn(self, message: str) -> None:
        """Record a project-authored warning (one line; never a value) and log it."""
        line = _one_line(message)
        self.manifest.warnings = [*self.manifest.warnings, line]
        _LOG.warning("run %s: %s", self.run_id, line)

    def bench(self, kind: str, name: str, *, wall_s: float, **fields: Any) -> Path:
        """Append a benchmark-ledger line for this run (:func:`bench` with ``run_id`` and
        ``tier`` filled in)."""
        fields.setdefault("run_id", self.run_id)
        fields.setdefault("tier", self.tier)
        fields.setdefault("settings", self.settings)
        return bench(kind, name, wall_s=wall_s, **fields)


# ---------------------------------------------------------------------------
# start()
# ---------------------------------------------------------------------------


def _append_ledger(manifest: RunManifest, settings: Settings) -> Path:
    return fsio.append_jsonl(ledger_path(settings), manifest.ledger_line())


@contextmanager
def start(
    name: str,
    *,
    tier: str,
    kind: RunKind | str = "analysis",
    params: Mapping[str, Any] | None = None,
    settings: Settings | None = None,
    command: str | None = None,
    protocol_id: str | None = None,
    protocol_hash: str | None = None,
    claim_type: str | None = None,
    doctor: bool = True,
) -> Iterator[Run]:
    """Open a run (module note): ``with run.start("name", tier="dev", kind="analysis",
    params={...}) as r:``. ``doctor=False`` skips the environment block (tests that open
    many runs). The manifest is written at entry (``status: running``) and again at exit;
    the ledger line is appended once, at exit; exceptions mark the run ``failed`` and
    re-raise."""
    settings = settings or get_settings()
    if tier not in TIERS:
        raise RunLedgerError(f"unknown tier {tier!r}; expected one of {', '.join(TIERS)}")
    if kind not in RUN_KINDS:
        raise RunLedgerError(f"unknown kind {kind!r}; expected one of {', '.join(RUN_KINDS)}")
    if not name or not name.strip():
        raise RunLedgerError("a run needs a name")
    run_id, directory = _new_run_dir(settings)
    manifest = RunManifest(
        run_id=run_id,
        name=name.strip(),
        kind=kind,  # type: ignore[arg-type]
        tier=tier,
        status="running",
        started=datetime.now(UTC).isoformat(timespec="milliseconds"),
        command=command if command is not None else _default_command(),
        git_sha=git_sha(),
        git_dirty=git_dirty(),
        uv_lock_sha256=uv_lock_sha256(),
        duckdb_version=_duckdb_version(),
        python_version=platform.python_version(),
        package_version=__version__,
        params=_jsonable(dict(params or {})),
        protocol_id=protocol_id,
        protocol_hash=protocol_hash,
        claim_type=claim_type,
        doctor=environment_block(settings) if doctor else None,
    )
    run = Run(manifest, directory, settings)
    run.write_manifest()
    free_before = _free_bytes(settings.data_root)
    rss_start = _rss_mb()
    clock = time.perf_counter()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            yield run
        except BaseException as exc:
            manifest.status = "failed"
            manifest.error = RunErrorInfo(type=type(exc).__name__, message=_sanitized(str(exc)))
            raise
        else:
            manifest.status = "ok"
        finally:
            for w in caught:
                text = _sanitized(f"{w.category.__name__}: {w.message}")
                manifest.warnings = [*manifest.warnings, text]
                _LOG.warning("run %s: %s", run_id, text)
            manifest.wall_s = round(time.perf_counter() - clock, 3)
            rss_end = _rss_mb()
            samples = [s for s in (rss_start, rss_end) if s is not None]
            manifest.peak_rss_mb = max(samples) if samples else None
            free_after = _free_bytes(settings.data_root)
            if free_before is not None and free_after is not None:
                manifest.disk_delta_mb = round((free_before - free_after) / 2**20, 1)
            manifest.finished = datetime.now(UTC).isoformat(timespec="milliseconds")
            run.write_manifest()
            _append_ledger(manifest, settings)


# ---------------------------------------------------------------------------
# Benchmark ledger bridge (brief item 2)
# ---------------------------------------------------------------------------


def bench(
    kind: str,
    name: str,
    *,
    wall_s: float,
    tier: str,
    run_id: str | None = None,
    build_id: str | None = None,
    settings: Settings | None = None,
    **fields: Any,
) -> Path:
    """Build one :class:`dag.benchmarks.BenchmarkLine` (``kind`` per
    :data:`dag.benchmarks.BENCHMARK_KINDS`, ``step`` = ``name``) and append it through
    :func:`dag.benchmarks.append` — the EP-19 module stays the only writer. ``build_id``
    defaults to ``run_id``, else a fresh runner-shaped id; ``host``, ``ts``,
    ``duckdb_version``, ``git_sha`` and ``ok`` are filled in unless given."""
    from mimicwarehouse.dag import benchmarks
    from mimicwarehouse.dag.runner import git_short_sha, new_build_id

    settings = settings or get_settings()
    fields.setdefault("ts", datetime.now(UTC).isoformat(timespec="seconds"))
    fields.setdefault("host", benchmarks.host_info())
    fields.setdefault("duckdb_version", _duckdb_version())
    fields.setdefault("git_sha", git_short_sha())
    fields.setdefault("ok", True)
    line = benchmarks.BenchmarkLine(
        build_id=build_id or run_id or new_build_id(tier),
        tier=tier,
        step=name,
        kind=kind,
        wall_s=wall_s,
        run_id=run_id,
        **fields,
    )
    return benchmarks.append(line, settings)


# ---------------------------------------------------------------------------
# runs.duckdb views (brief item 3)
# ---------------------------------------------------------------------------


def _sql_path(path: Path) -> str:
    return path.resolve().as_posix().replace("'", "''")


def _columns_sql(columns: Mapping[str, str]) -> str:
    return "{" + ", ".join(f"'{name}': '{typ}'" for name, typ in columns.items()) + "}"


def _empty_select(columns: Mapping[str, str]) -> str:
    """A typed, zero-row SELECT with ``columns`` (what a missing file glob binds to)."""
    cols = ", ".join(f"NULL::{typ} AS {name}" for name, typ in columns.items())
    return f"SELECT {cols} WHERE false"


def _jsonl_select(path: Path, columns: Mapping[str, str], first: str) -> str:
    """``read_json`` over a JSONL ledger with explicit ``columns``; a torn trailing line
    binds as an all-NULL record (DuckDB 1.5.5, LGR-1), dropped by ``first IS NOT NULL``."""
    return (
        f"SELECT * FROM read_json('{_sql_path(path)}', format = 'newline_delimited', "
        f"columns = {_columns_sql(columns)}, ignore_errors = true) WHERE {first} IS NOT NULL"
    )


def runs_db_views(settings: Settings | None = None) -> list[tuple[str, str]]:
    """``[(view_name, select_sql), ...]`` for ``ledger``, ``benchmarks``, ``manifests`` and
    ``attrition`` (module note). Touches the ledgers so an empty file exists (a view over a
    missing file would fail at query time); ``manifests`` falls back to a typed empty
    SELECT when no ``runs/*/manifest.json`` exists yet."""
    from mimicwarehouse.dag.benchmarks import benchmarks_path

    settings = settings or get_settings()
    runs = settings.layout["runs"]
    runs.mkdir(parents=True, exist_ok=True)
    ledger = ledger_path(settings)
    ledger.touch(exist_ok=True)
    benchmarks = benchmarks_path(settings)
    benchmarks.touch(exist_ok=True)

    views: list[tuple[str, str]] = [
        ("ledger", _jsonl_select(ledger, LEDGER_COLUMNS, "run_id")),
        ("benchmarks", _jsonl_select(benchmarks, BENCHMARK_COLUMNS, "ts")),
    ]
    if any(p.is_file() for p in runs.glob(f"*/{MANIFEST_FILENAME}")):
        glob = _sql_path(runs) + f"/*/{MANIFEST_FILENAME}"
        manifests = (
            f"SELECT * FROM read_json('{glob}', format = 'auto', "
            f"columns = {_columns_sql(MANIFEST_COLUMNS)}, ignore_errors = true) "
            "WHERE run_id IS NOT NULL"
        )
    else:
        manifests = _empty_select(MANIFEST_COLUMNS)
    views.append(("manifests", manifests))
    views.append(
        (
            "attrition",
            "SELECT m.run_id, m.name, m.kind, m.tier, m.status, a.step, a.label, a.n_units, "
            "a.n_subjects FROM manifests AS m, "
            f"UNNEST(json_transform(m.attrition, '{_ATTRITION_JSON_SHAPE}')) AS t(a)",
        )
    )
    return views


# ---------------------------------------------------------------------------
# Reproduction block (brief item 4)
# ---------------------------------------------------------------------------


def reproduction_block(run_id: str, settings: Settings | None = None) -> str:
    """The Markdown **Reproduction** + **Provenance** block of a ``docs/analyses`` case
    study (EP-32 convention) for ``run_id``: run id, kind, tier, status, the command
    line, git sha (+ dirty flag), versions, the ``uv.lock`` hash, snapshot ids, protocol
    id + hash (or "none"), claim type, and the counts of recorded SQL statements and
    audited calls (through ``fmt_int``). ASCII; the run id appears inline only."""
    from mimicwarehouse.inventory import fmt_int

    m = read_manifest(run_id, settings)
    dirty = {True: "dirty", False: "clean", None: "no git"}[m.git_dirty]
    snapshots = (
        "; ".join(f"{layer} `{sid}`" for layer, sid in sorted(m.snapshot_ids.items()))
        or "none recorded"
    )
    if m.protocol_hash:
        protocol = f"`{m.protocol_id or '-'}` hash `{m.protocol_hash}`"
    else:
        protocol = "none (not run under a frozen protocol; EP-51)"
    lines = [
        "## Reproduction",
        "",
        f"Run `{m.run_id}` - kind `{m.kind}`, tier `{m.tier}`, status `{m.status}`, "
        f"started {m.started}.",
        "",
        "```powershell",
        "cd mimicwarehouse",
        m.command or "# (command line not recorded)",
        "```",
        "",
        f"- Recorded SQL: {fmt_int(len(m.sql))} statement(s) under `runs/<run_id>/sql/`; "
        f"audited safe-query calls: {fmt_int(len(m.audit_ids))}; "
        f"attrition steps: {fmt_int(len(m.attrition))}.",
        f"- Protocol: {protocol}.",
        f"- Claim type: {m.claim_type or 'not stated'}. MIMIC-IV analyses are retrospective.",
        "",
        "## Provenance",
        "",
        f"- git `{m.git_sha or 'nogit'}` ({dirty}) - package `{m.package_version}` - "
        f"DuckDB `{m.duckdb_version}` - Python `{m.python_version}`.",
        f"- Environment hash (`uv.lock` sha256): `{m.uv_lock_sha256 or '-'}`.",
        f"- Snapshot ids: {snapshots}.",
        f"- Wall time {m.wall_s if m.wall_s is not None else '-'} s.",
        "",
    ]
    return "\n".join(lines)


__all__ = [
    "BENCHMARK_COLUMNS",
    "FIGURES_DIRNAME",
    "LEDGER_COLUMNS",
    "LEDGER_FIELDS",
    "LEDGER_FILENAME",
    "MANIFEST_COLUMNS",
    "MANIFEST_FILENAME",
    "NAME_RE",
    "RUN_ID_RE",
    "RUN_KINDS",
    "RUN_STATUSES",
    "SQL_DIRNAME",
    "TABLES_DIRNAME",
    "TEXT_MAX_CHARS",
    "TIERS",
    "AttritionRow",
    "RefEntry",
    "Run",
    "RunErrorInfo",
    "RunKind",
    "RunLedgerError",
    "RunManifest",
    "RunStatus",
    "bench",
    "environment_block",
    "git_dirty",
    "git_sha",
    "ledger_path",
    "list_runs",
    "manifest_path",
    "read_ledger",
    "read_manifest",
    "reproduction_block",
    "require_run_id",
    "run_dir",
    "runs_db_views",
    "start",
    "uv_lock_sha256",
]
