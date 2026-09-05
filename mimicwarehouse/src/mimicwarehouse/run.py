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

**Seeds (EP-36 item 1; prose: ``docs/methods/determinism.md``).** One derivation rule,
no global state: :func:`derive_seed` (``protocol_id``, ``stage``, ``salt``) → a 32-bit
seed via sha256; :func:`rng` → the ``numpy.random.Generator`` a stochastic stage receives;
:func:`spawn_rngs` → ``n`` reproducible child streams for joblib / CV workers;
:func:`seed_everything` → the global ``random`` / numpy-legacy / (already-imported)
``torch`` seeds for entry points and tests; :func:`sql_sample_clause` → the DuckDB
``USING SAMPLE … REPEATABLE (seed)`` clause. ``Run.seed(stage)`` derives from the run's
``protocol_id`` (a frozen protocol, EP-51) or its ``run_id`` (unfrozen work), records
``{stage: seed}`` in ``RunManifest.seeds`` (rewriting the manifest at once, so a killed
run still shows what it seeded) and returns the Generator; ``Run.spawn_rngs`` is the
worker form.

**Resource log (EP-36 item 3).** :class:`ResourceLog` is a daemon-thread sampler
(:data:`SAMPLE_INTERVAL_S`) over psutil: process RSS every tick, the Windows
process-lifetime ``peak_wset``, CPU time, the data-root drive's free-bytes delta and —
only when ``pynvml`` (``nvidia-ml-py``) imports **and** a device is present — GPU memory,
else ``None`` without a warning (D-16). ``start()`` runs one per run and stores its
:class:`ResourceUsage` under ``manifest.resources`` (``wall_s`` / ``peak_rss_mb`` /
``disk_delta_mb`` mirror it at the top level); ``ResourceLog.measure(fn)`` is the
standalone form whose :meth:`ResourceUsage.bench_fields` feed :func:`bench` (``run.bench``
→ ``dag.benchmarks.BenchmarkLine``). The peak-RSS rule: ``peak_wset`` is a
*process-lifetime* high-water mark (an earlier allocation in the same process keeps it
high), so the run-scoped value is the sampled maximum, promoted to ``peak_wset`` only when
that mark **grew** during the run (``peak_rss_method``).

Import budget: not on the ``mwh`` start-up path (``tracer.py`` and ``runs_cli.py`` import
it inside function bodies); numpy, psutil, pynvml, duckdb-adjacent modules and ``safe``
load lazily (``test_ep35`` / ``test_ep36`` pin it).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import platform
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import warnings
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from importlib.metadata import version as dist_version
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field

from mimicwarehouse import __version__, config, fsio
from mimicwarehouse.config import Settings, get_settings

if TYPE_CHECKING:  # pragma: no cover
    from numpy.random import Generator

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

#: A derived seed is the big-endian value of the first :data:`SEED_BYTES` bytes of a
#: sha256 digest: 32 bits, so it fits numpy's legacy global seed and every
#: ``random_state`` (sklearn / LightGBM / XGBoost / statsmodels) as it is.
SEED_BYTES = 4
SEED_MAX = 2**32 - 1
#: DuckDB parses ``REPEATABLE (<seed>)`` as an int32 literal (2**31 and above is a syntax
#: error; probed on 1.5.5), so :func:`sql_sample_clause` folds a seed to ``seed % 2**31``.
DUCKDB_SEED_MAX = 2**31 - 1
#: Stage names are one token like recorded SQL / table names (``bootstrap``, ``cv_split``,
#: ``model_fit.lgbm``); ``|`` is the key separator and can never appear in a part.
STAGE_RE = NAME_RE
#: DuckDB sampling methods :func:`sql_sample_clause` accepts.
SAMPLE_METHODS: tuple[str, ...] = ("reservoir", "bernoulli", "system")
#: The resource sampler's tick (EP-36 item 3).
SAMPLE_INTERVAL_S = 0.5

PeakRssMethod = Literal["peak_wset", "sampled"]
GpuMemMethod = Literal["process", "device"]


class RunLedgerError(RuntimeError):
    """A run-ledger usage error (bad tier / name / run id, missing manifest)."""


class SeedError(RunLedgerError):
    """A seed-derivation usage error (empty scope, bad stage name, salt, size or seed)."""


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


class ResourceUsage(BaseModel):
    """What one :class:`ResourceLog` measured — the ``resources`` block of a manifest.
    MB = 2**20 bytes, one decimal; a ``None`` means "not measurable here" (no psutil,
    no pynvml / GPU, no data root), never an error. ``peak_rss_mb`` is the run-scoped
    peak (module note: the sampled maximum, promoted to the process-lifetime
    ``peak_wset`` only when that mark grew during the measurement —
    ``peak_rss_method`` says which); ``gpu_mem_*`` is this process's GPU memory when the
    driver reports it per process, else the device-level total (``gpu_mem_method``)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    wall_s: float
    cpu_time_s: float | None = None
    peak_rss_mb: float | None = None
    peak_rss_method: PeakRssMethod | None = None
    rss_start_mb: float | None = None
    rss_end_mb: float | None = None
    peak_wset_mb: float | None = None
    disk_delta_mb: float | None = None
    gpu_mem_start_mb: float | None = None
    gpu_mem_peak_mb: float | None = None
    gpu_mem_method: GpuMemMethod | None = None
    samples: int = 0
    sample_errors: int = 0
    interval_s: float = SAMPLE_INTERVAL_S

    def bench_fields(self) -> dict[str, Any]:
        """The fields a benchmark line takes from a measurement — what
        ``run.bench(kind, name, **usage.bench_fields())`` records."""
        return {
            "wall_s": self.wall_s,
            "peak_rss_mb": self.peak_rss_mb,
            "disk_delta_mb": self.disk_delta_mb,
        }


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
    #: ``{stage: seed}`` (EP-36): ``{}`` for a run that seeded nothing; ``None`` only in
    #: manifests written before EP-36.
    seeds: dict[str, int] | None = None
    #: The :class:`ResourceLog` measurement (EP-36); ``None`` while ``status: running``.
    resources: ResourceUsage | None = None
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


def _free_bytes(path: Path) -> int | None:
    try:
        return shutil.disk_usage(config.nearest_existing(path)).free
    except OSError:  # pragma: no cover
        return None


def _mb(value: int | float | None) -> float | None:
    return None if value is None else round(value / 2**20, 1) + 0.0  # + 0.0: never "-0.0"


# ---------------------------------------------------------------------------
# Seeds (EP-36 item 1; docs/methods/determinism.md)
# ---------------------------------------------------------------------------


def _require_seed_scope(protocol_id: str) -> str:
    if not isinstance(protocol_id, str) or not protocol_id.strip():
        raise SeedError("protocol_id (the seed scope) must be a non-empty string")
    if "|" in protocol_id or "\n" in protocol_id:
        raise SeedError(f"protocol_id {protocol_id!r} may not contain '|' or a newline")
    return protocol_id


def _require_stage(stage: str) -> str:
    if not isinstance(stage, str) or not STAGE_RE.match(stage):
        raise SeedError(
            f"stage name {stage!r} must be one token (letters, digits, '_', '.', '-'; "
            "e.g. 'bootstrap', 'cv_split', 'model_fit.lgbm')"
        )
    return stage


def _require_salt(salt: int) -> int:
    if isinstance(salt, bool) or not isinstance(salt, int) or salt < 0:
        raise SeedError(f"salt must be a non-negative int, got {salt!r}")
    return salt


def seed_key(protocol_id: str, stage: str, salt: int = 0) -> str:
    """The exact string :func:`derive_seed` hashes: ``"<protocol_id>|<stage>|<salt>"``."""
    return f"{_require_seed_scope(protocol_id)}|{_require_stage(stage)}|{_require_salt(salt)}"


def derive_seed(protocol_id: str, stage: str, salt: int = 0) -> int:
    """The seed of ``stage`` under ``protocol_id`` — a frozen protocol id (EP-51) or a run
    id for unfrozen work — as ``int.from_bytes(sha256(f"{protocol_id}|{stage}|{salt}")
    .digest()[:4], "big")``: 0 … 2**32 - 1, the same in every process and on every
    machine, different for every stage, salt and protocol. ``salt`` names a deliberate
    variant (a repeat with fresh randomness); everything else is a different stage."""
    digest = hashlib.sha256(seed_key(protocol_id, stage, salt).encode("utf-8")).digest()
    return int.from_bytes(digest[:SEED_BYTES], "big")


def rng(protocol_id: str, stage: str, salt: int = 0) -> Generator:
    """``numpy.random.default_rng(derive_seed(protocol_id, stage, salt))`` — the Generator a
    stochastic stage receives as an argument (the policy's first rule: library code
    never seeds globals)."""
    import numpy as np

    return np.random.default_rng(derive_seed(protocol_id, stage, salt))


def spawn_rngs(protocol_id: str, stage: str, n: int, salt: int = 0) -> list[Generator]:
    """``n`` independent, reproducible child Generators for joblib / CV workers:
    ``SeedSequence(derive_seed(...)).spawn(n)``, spawned on the parent and handed to the
    workers as arguments (Windows ``spawn`` never inherits module state). The children
    differ from each other and from :func:`rng`'s stream for the same stage."""
    import numpy as np

    if isinstance(n, bool) or not isinstance(n, int) or n < 1:
        raise SeedError(f"spawn_rngs needs n >= 1, got {n!r}")
    seed = derive_seed(protocol_id, stage, salt)
    return [np.random.default_rng(child) for child in np.random.SeedSequence(seed).spawn(n)]


def seed_everything(seed: int) -> dict[str, bool]:
    """Seed the **global** generators — ``random``, numpy's legacy ``np.random`` state and
    ``torch`` *only if it is already imported* (never imported here; ``manual_seed``
    also seeds every CUDA device). For entry points, notebooks and tests: library code
    takes a Generator instead (``docs/methods/determinism.md``). Returns
    ``{library: seeded}`` so a caller can see what it reached."""
    import random

    import numpy as np

    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= SEED_MAX:
        raise SeedError(f"seed must be an int in 0 ... {SEED_MAX}, got {seed!r}")
    random.seed(seed)
    np.random.seed(seed)
    seeded = {"random": True, "numpy": True, "torch": False}
    torch = sys.modules.get("torch")
    if torch is not None:
        torch.manual_seed(seed)
        seeded["torch"] = True
    return seeded


def sql_sample_clause(
    seed: int,
    *,
    rows: int | None = None,
    percent: float | None = None,
    method: str = "reservoir",
) -> str:
    """The DuckDB sampling clause of the policy — ``USING SAMPLE <method>(<size>)
    REPEATABLE (<seed>)`` — for SQL subsampling that must reproduce (a bare ``USING
    SAMPLE`` never does). ``seed`` is any 32-bit seed (:func:`derive_seed` output), folded
    to DuckDB's int32 literal range as ``seed % 2**31`` (:data:`DUCKDB_SEED_MAX`; the
    fold is deterministic and documented, so the clause reproduces from the recorded
    seed). Exactly one of ``rows`` (a count) or ``percent`` (0 < p ≤ 100); ``reservoir``
    (default, exact size) takes either, ``bernoulli`` and ``system`` take a percentage
    only (DuckDB refuses a count for them), and ``system`` samples whole 2,048-row
    vectors, so a small percentage of a small table returns nothing."""
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= SEED_MAX:
        raise SeedError(f"seed must be an int in 0 ... {SEED_MAX}, got {seed!r}")
    duckdb_seed = seed % (DUCKDB_SEED_MAX + 1)
    if method not in SAMPLE_METHODS:
        raise SeedError(f"unknown sample method {method!r}; expected {', '.join(SAMPLE_METHODS)}")
    if (rows is None) == (percent is None):
        raise SeedError("sql_sample_clause takes exactly one of rows= or percent=")
    if rows is not None:
        if isinstance(rows, bool) or not isinstance(rows, int) or rows < 1:
            raise SeedError(f"rows must be an int >= 1, got {rows!r}")
        if method != "reservoir":
            raise SeedError(f"{method} sampling takes percent=, not rows= (DuckDB rule)")
        size = str(rows)
    else:
        if isinstance(percent, bool) or not isinstance(percent, int | float):
            raise SeedError(f"percent must be a number in (0, 100], got {percent!r}")
        if not 0 < percent <= 100:
            raise SeedError(f"percent must be in (0, 100], got {percent!r}")
        size = f"{percent:.6f}".rstrip("0").rstrip(".") + "%"
    return f"USING SAMPLE {method}({size}) REPEATABLE ({duckdb_seed})"


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
    ``settings``, ``resource_log`` (the live :class:`ResourceLog` while the run is open).
    Everything recorded lands in ``manifest`` and is written on exit (seeds at once)."""

    def __init__(self, manifest: RunManifest, directory: Path, settings: Settings) -> None:
        self.manifest = manifest
        self.dir = directory
        self.settings = settings
        self.resource_log: ResourceLog | None = None

    @property
    def run_id(self) -> str:
        return self.manifest.run_id

    @property
    def tier(self) -> str:
        return self.manifest.tier

    # -- seeds (EP-36) ---------------------------------------------------------------------

    @property
    def seed_scope(self) -> str:
        """What this run's seeds derive from: the frozen ``protocol_id`` when the run is
        under a protocol, else the ``run_id`` (unfrozen work is reproducible from its
        manifest — ``derive_seed(run_id, stage)`` — not across runs)."""
        return self.manifest.protocol_id or self.run_id

    def _record_seed(self, stage: str) -> int:
        seed = derive_seed(self.seed_scope, stage)
        have = dict(self.manifest.seeds or {})
        if have.get(stage) != seed:
            have[stage] = seed
            self.manifest.seeds = have
            self.write_manifest()  # persisted before the stochastic work starts
        return seed

    def seed(self, stage: str) -> Generator:
        """The Generator of ``stage`` (``rng(seed_scope, stage)``), recorded as
        ``manifest.seeds[stage]``. Calling it again for the same stage returns the same
        stream and records nothing new — name distinct stochastic steps distinctly
        (``bootstrap.auc`` / ``bootstrap.brier``), never reuse one stage for two."""
        import numpy as np

        return np.random.default_rng(self._record_seed(stage))

    def spawn_rngs(self, stage: str, n: int) -> list[Generator]:
        """``n`` worker Generators for ``stage`` (:func:`spawn_rngs` on this run's seed
        scope), the stage's seed recorded like :meth:`seed`."""
        children = spawn_rngs(self.seed_scope, stage, n)
        self._record_seed(stage)
        return children

    # -- resources (EP-36) -----------------------------------------------------------------

    def measure[T](
        self, kind: str, name: str, fn: Callable[[], T], **fields: Any
    ) -> tuple[T, ResourceUsage]:
        """Run ``fn()`` under its own :class:`ResourceLog` and append a benchmark line for
        it (:meth:`bench` with the measurement's ``wall_s`` / ``peak_rss_mb`` /
        ``disk_delta_mb``, plus ``fields``); returns ``(result, usage)``."""
        result, usage = ResourceLog.measure(fn, data_root=self.settings.data_root)
        self.bench(kind, name, **{**usage.bench_fields(), **fields})
        return result, usage

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
# Resource log (EP-36 item 3)
# ---------------------------------------------------------------------------


class ResourceLog:
    """A daemon-thread resource sampler (module note): ``start()`` takes a first sample
    and starts the thread, every :data:`SAMPLE_INTERVAL_S` it samples this process's RSS
    (and, when NVML is available, GPU memory), ``stop()`` joins the thread, takes the
    final sample and returns the :class:`ResourceUsage`. Also a context manager, and
    :meth:`measure` wraps one callable. Every probe is fail-quiet: a psutil / NVML error
    counts in ``sample_errors`` and leaves the field ``None`` — telemetry never fails a
    run. ``data_root`` names the drive whose free-bytes delta is measured (``None`` = not
    measured); ``gpu=False`` skips the NVML probe altogether."""

    def __init__(
        self,
        *,
        data_root: Path | None = None,
        interval_s: float = SAMPLE_INTERVAL_S,
        gpu: bool = True,
    ) -> None:
        bad_type = isinstance(interval_s, bool) or not isinstance(interval_s, int | float)
        if bad_type or interval_s <= 0:
            raise RunLedgerError(f"ResourceLog interval_s must be > 0, got {interval_s!r}")
        self.interval_s = float(interval_s)
        self.data_root = Path(data_root) if data_root is not None else None
        self.gpu_enabled = bool(gpu)
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._proc: Any = None
        self._nvml: tuple[Any, list[Any]] | None = None
        self._t0: float | None = None
        self._cpu0: float | None = None
        self._free0: int | None = None
        self._rss_start: int | None = None
        self._rss_last: int | None = None
        self._rss_max: int | None = None
        self._peak_wset_start: int | None = None
        self._peak_wset_last: int | None = None
        self._gpu_start: int | None = None
        self._gpu_last: int | None = None
        self._gpu_max: int | None = None
        self._gpu_method: GpuMemMethod | None = None
        self._samples = 0
        self._errors = 0
        self._usage: ResourceUsage | None = None

    # -- lifecycle -------------------------------------------------------------------------

    @property
    def started(self) -> bool:
        return self._t0 is not None

    @property
    def running(self) -> bool:
        return self._t0 is not None and self._usage is None

    @property
    def usage(self) -> ResourceUsage | None:
        """The measurement once :meth:`stop` ran; ``None`` before."""
        return self._usage

    def start(self) -> ResourceLog:
        if self._t0 is not None:
            raise RunLedgerError("ResourceLog.start() called twice — one measurement per log")
        self._proc = self._open_process()
        self._nvml = self._open_nvml()
        self._free0 = _free_bytes(self.data_root) if self.data_root is not None else None
        self._cpu0 = self._cpu_seconds()
        self._t0 = time.perf_counter()
        self.sample()
        self._rss_start = self._rss_last
        self._peak_wset_start = self._peak_wset_last
        self._gpu_start = self._gpu_last
        self._thread = threading.Thread(target=self._loop, name="mwh-resource-log", daemon=True)
        self._thread.start()
        return self

    def _loop(self) -> None:
        while not self._stop_event.wait(self.interval_s):
            self.sample()

    def sample(self) -> None:
        """Take one sample now (the thread calls this every tick; ``start`` / ``stop``
        call it too, so a measurement always has at least two samples)."""
        with self._lock:
            self._sample_memory()
            self._sample_gpu()
            self._samples += 1

    def stop(self) -> ResourceUsage:
        """Stop sampling and return the measurement (idempotent after the first call)."""
        if self._t0 is None:
            raise RunLedgerError("ResourceLog.stop() before start()")
        if self._usage is not None:
            return self._usage
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=self.interval_s + 1.0)
        self.sample()
        wall = time.perf_counter() - self._t0
        cpu1 = self._cpu_seconds()
        free1 = _free_bytes(self.data_root) if self.data_root is not None else None
        self._close_nvml()

        peak_wset_end = self._peak_wset_last
        grew = (
            peak_wset_end is not None
            and self._peak_wset_start is not None
            and peak_wset_end > self._peak_wset_start
        )
        peak: int | None
        method: PeakRssMethod | None
        if self._rss_max is None:
            peak, method = None, None
        elif grew and peak_wset_end is not None:
            peak, method = max(self._rss_max, peak_wset_end), "peak_wset"
        else:
            peak, method = self._rss_max, "sampled"
        cpu = None if self._cpu0 is None or cpu1 is None else round(max(cpu1 - self._cpu0, 0.0), 3)
        disk = None if self._free0 is None or free1 is None else _mb(self._free0 - free1)
        self._usage = ResourceUsage(
            wall_s=round(wall, 6),
            cpu_time_s=cpu,
            peak_rss_mb=_mb(peak),
            peak_rss_method=method,
            rss_start_mb=_mb(self._rss_start),
            rss_end_mb=_mb(self._rss_last),
            peak_wset_mb=_mb(peak_wset_end),
            disk_delta_mb=disk,
            gpu_mem_start_mb=_mb(self._gpu_start),
            gpu_mem_peak_mb=_mb(self._gpu_max),
            gpu_mem_method=self._gpu_method if self._gpu_max is not None else None,
            samples=self._samples,
            sample_errors=self._errors,
            interval_s=self.interval_s,
        )
        return self._usage

    def __enter__(self) -> ResourceLog:
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()

    @classmethod
    def measure[T](
        cls,
        fn: Callable[[], T],
        *,
        data_root: Path | None = None,
        interval_s: float = SAMPLE_INTERVAL_S,
        gpu: bool = True,
    ) -> tuple[T, ResourceUsage]:
        """``fn()`` under a fresh log: ``(result, usage)`` — the standalone form whose
        ``usage.bench_fields()`` feed :func:`bench` (``run.bench``)."""
        log = cls(data_root=data_root, interval_s=interval_s, gpu=gpu)
        log.start()
        try:
            result = fn()
        finally:
            usage = log.stop()
        return result, usage

    # -- probes (all fail-quiet) -----------------------------------------------------------

    @staticmethod
    def _open_process() -> Any:
        try:
            import psutil

            return psutil.Process()
        except Exception:  # pragma: no cover - psutil unavailable / restricted
            return None

    def _cpu_seconds(self) -> float | None:
        if self._proc is None:
            return None
        try:
            times = self._proc.cpu_times()
            return float(times.user) + float(times.system)
        except Exception:  # pragma: no cover - defensive
            self._errors += 1
            return None

    def _sample_memory(self) -> None:
        if self._proc is None:
            return
        try:
            mem = self._proc.memory_info()
        except Exception:  # pragma: no cover - defensive
            self._errors += 1
            return
        rss = int(mem.rss)
        self._rss_last = rss
        self._rss_max = rss if self._rss_max is None else max(self._rss_max, rss)
        peak = getattr(mem, "peak_wset", None)  # Windows only; None elsewhere
        if peak is not None:
            self._peak_wset_last = int(peak)

    def _open_nvml(self) -> tuple[Any, list[Any]] | None:
        """``(pynvml, device handles)`` when ``pynvml`` imports, initialises and reports
        at least one device; else ``None`` — silently (D-16: GPU is opt-in)."""
        if not self.gpu_enabled:
            return None
        try:
            import pynvml  # type: ignore[import-not-found]  # nvidia-ml-py: gpu group (EP-121)
        except Exception:
            return None
        try:
            pynvml.nvmlInit()
        except Exception:
            return None
        try:
            count = int(pynvml.nvmlDeviceGetCount())
            handles = [pynvml.nvmlDeviceGetHandleByIndex(i) for i in range(count)]
        except Exception:
            handles = []
        if not handles:
            self._shutdown_nvml(pynvml)
            return None
        _LOG.debug("resource log: sampling GPU memory over %d NVML device(s)", len(handles))
        return pynvml, handles

    @staticmethod
    def _shutdown_nvml(nvml: Any) -> None:
        with contextlib.suppress(Exception):  # defensive: a shutdown error is not ours
            nvml.nvmlShutdown()

    def _close_nvml(self) -> None:
        if self._nvml is not None:
            self._shutdown_nvml(self._nvml[0])
            self._nvml = None

    @staticmethod
    def _gpu_process_bytes(nvml: Any, handles: list[Any], pid: int) -> int | None:
        """This process's GPU memory summed over devices, or ``None`` when the driver does
        not attribute memory per process (Windows WDDM reports N/A)."""
        total = 0
        seen = False
        for handle in handles:
            try:
                procs = nvml.nvmlDeviceGetComputeRunningProcesses(handle)
            except Exception:
                continue
            for proc in procs:
                used = getattr(proc, "usedGpuMemory", None)
                if getattr(proc, "pid", None) == pid and used is not None:
                    total += int(used)
                    seen = True
        return total if seen else None

    def _sample_gpu(self) -> None:
        if self._nvml is None:
            return
        nvml, handles = self._nvml
        used = self._gpu_process_bytes(nvml, handles, os.getpid())
        method: GpuMemMethod = "process"
        if used is None:
            try:
                used = sum(int(nvml.nvmlDeviceGetMemoryInfo(h).used) for h in handles)
            except Exception:
                self._errors += 1
                return
            method = "device"
        self._gpu_last = used
        if self._gpu_max is None or used >= self._gpu_max:
            self._gpu_max = used
            self._gpu_method = method


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
    many runs). The manifest is written at entry (``status: running``, ``seeds: {}``),
    whenever a stage is seeded, and again at exit with the :class:`ResourceLog`
    measurement (``resources``; ``wall_s`` / ``peak_rss_mb`` / ``disk_delta_mb`` mirror
    it); the ledger line is appended once, at exit; exceptions mark the run ``failed``
    and re-raise."""
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
        seeds={},
        protocol_id=protocol_id,
        protocol_hash=protocol_hash,
        claim_type=claim_type,
        doctor=environment_block(settings) if doctor else None,
    )
    run = Run(manifest, directory, settings)
    run.write_manifest()
    # the sampler starts after the environment block (its probes cost seconds and are
    # not the run's work) and stops before the manifest is finalised
    log = ResourceLog(data_root=settings.data_root)
    run.resource_log = log
    log.start()
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
            usage = log.stop()
            run.resource_log = None
            for w in caught:
                text = _sanitized(f"{w.category.__name__}: {w.message}")
                manifest.warnings = [*manifest.warnings, text]
                _LOG.warning("run %s: %s", run_id, text)
            manifest.resources = usage
            manifest.wall_s = usage.wall_s
            manifest.peak_rss_mb = usage.peak_rss_mb
            manifest.disk_delta_mb = usage.disk_delta_mb
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
    id + hash (or "none"), claim type, the counts of recorded SQL statements and audited
    calls, the seeds (EP-36; every integer through ``fmt_int``, so a seed can never look
    like an id) and the resource measurement. ASCII; the run id appears inline only."""
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
    if m.seeds:
        listed = "; ".join(f"{stage} {fmt_int(seed)}" for stage, seed in sorted(m.seeds.items()))
        seeds = (
            f"{listed} - each is derive_seed(`{m.protocol_id or m.run_id}`, stage) "
            "(docs/methods/determinism.md)"
        )
    elif m.seeds is None:
        seeds = "not recorded (run predates EP-36)"
    else:
        seeds = "none (no stochastic stage)"
    res = m.resources
    if res is None:
        resources = "not measured"
    else:
        gpu = "-" if res.gpu_mem_peak_mb is None else f"{fmt_int(round(res.gpu_mem_peak_mb))} MB"
        resources = (
            f"peak RSS {fmt_int(None if res.peak_rss_mb is None else round(res.peak_rss_mb))} MB "
            f"({res.peak_rss_method or '-'}); CPU time "
            f"{'-' if res.cpu_time_s is None else res.cpu_time_s} s; disk delta "
            f"{'-' if res.disk_delta_mb is None else res.disk_delta_mb} MB; GPU memory peak {gpu}"
        )
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
        f"- Seeds: {seeds}.",
        f"- Claim type: {m.claim_type or 'not stated'}. MIMIC-IV analyses are retrospective.",
        "",
        "## Provenance",
        "",
        f"- git `{m.git_sha or 'nogit'}` ({dirty}) - package `{m.package_version}` - "
        f"DuckDB `{m.duckdb_version}` - Python `{m.python_version}`.",
        f"- Environment hash (`uv.lock` sha256): `{m.uv_lock_sha256 or '-'}`.",
        f"- Snapshot ids: {snapshots}.",
        f"- Wall time {m.wall_s if m.wall_s is not None else '-'} s; {resources}.",
        "",
    ]
    return "\n".join(lines)


__all__ = [
    "BENCHMARK_COLUMNS",
    "DUCKDB_SEED_MAX",
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
    "SAMPLE_INTERVAL_S",
    "SAMPLE_METHODS",
    "SEED_BYTES",
    "SEED_MAX",
    "SQL_DIRNAME",
    "STAGE_RE",
    "TABLES_DIRNAME",
    "TEXT_MAX_CHARS",
    "TIERS",
    "AttritionRow",
    "GpuMemMethod",
    "PeakRssMethod",
    "RefEntry",
    "ResourceLog",
    "ResourceUsage",
    "Run",
    "RunErrorInfo",
    "RunKind",
    "RunLedgerError",
    "RunManifest",
    "RunStatus",
    "SeedError",
    "bench",
    "derive_seed",
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
    "rng",
    "run_dir",
    "runs_db_views",
    "seed_everything",
    "seed_key",
    "spawn_rngs",
    "sql_sample_clause",
    "start",
    "uv_lock_sha256",
]
