"""The DAG runner behind ``mwh build`` (EP-19 item 2; D-20, DESIGN §6).

``run(dag, tier, ...)`` executes the selected steps of a :class:`~mimicwarehouse.dag.spec.DagSpec`
in topological order, one tier at a time, as the **only writer** of the lake
(single-writer rule, DESIGN §6):

* ``build_id = <UTC yyyymmddThhmmss>-<tier>-<git short sha>``;
* the build lock ``warehouse/.build.lock`` (``{pid, build_id, started}``) refuses a
  second build while the recorded pid is alive; a stale lock (dead pid) yields only to
  ``break_lock`` — **one** build-profile (36 GB / 12-thread) connection per machine at
  a time (ledger ARCH-11), tests and ad-hoc readers use the app profile;
* the free-space guard is per tier (``settings.min_free_gb_for``, EP-170/ARCH-9);
* the raw root: ``fixture`` -> the committed ``tests/fixtures`` tree (EP-11/12),
  ``dev``/``full`` -> ``settings.source_root``, ``demo`` -> EP-22; the lake root is
  ``settings.lake_root(tier)`` and fixture/demo builds pass
  :func:`~mimicwarehouse.config.assert_not_credentialed_lake` first (EP-170/ARCH-3);
* the bucket filter: ``dev`` -> ``settings.dev_buckets``, else all 100;
* a step already complete for the tier in ``status.json`` is skipped unless ``force``;
  a failing step stops the run and completed work stays complete (rerun = resume);
* every step runs under a :class:`StepContext` with wall time and peak RSS sampled
  every 2 s by a daemon thread; one :class:`~mimicwarehouse.dag.benchmarks.BenchmarkLine`
  per step plus a ``kind: build`` summary line go to the benchmark ledger;
* the run ends by appending the layer snapshot id to ``lake/manifests/snapshots.json``
  (:mod:`~mimicwarehouse.dag.snapshot`).

Step handlers live in :data:`STEP_HANDLERS` (kind -> handler) so later EPs add kinds
without touching this module: ``stage`` is implemented here; ``catalog`` raises
``NotImplementedError("EP-21")`` until EP-21 registers the real one; ``sql``/``python``
arrive with EP-37/EP-50. Everything returned or logged is counts, schemas, hashes and
timings — never a row.
"""

from __future__ import annotations

import json
import logging
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, Literal

from mimicwarehouse import config
from mimicwarehouse.config import Settings, Tier, assert_not_credentialed_lake, require_free_space
from mimicwarehouse.dag import benchmarks
from mimicwarehouse.dag.snapshot import complete_for_tier, layer_snapshot, record_snapshot
from mimicwarehouse.dag.spec import DagError, DagSpec, Step
from mimicwarehouse.loader import paths as loader_paths
from mimicwarehouse.loader.engine import open_build_connection
from mimicwarehouse.loader.manifest import read_status, update_status, utc_now_iso

if TYPE_CHECKING:  # pragma: no cover
    import duckdb

    from mimicwarehouse.inventory import RawManifest
    from mimicwarehouse.schema.contract import Table

_LOG = logging.getLogger(__name__)

LOCK_FILENAME = ".build.lock"
#: RSS sample interval of the per-step daemon thread (seconds).
RSS_SAMPLE_S = 2.0
#: Layer the EP-19 stage steps write (EP-37/EP-50 add derived; EP-148 the notes lake).
LAYER = "core"

StepStatus = Literal["planned", "done", "skipped", "failed"]


class BuildLockError(RuntimeError):
    """Another build holds (or held) ``warehouse/.build.lock``."""


# ---------------------------------------------------------------------------
# Context, outcomes
# ---------------------------------------------------------------------------


@dataclass
class StepContext:
    """What a step handler gets (EP-19 item 2 + EP-170 amendment 3)."""

    settings: Settings
    tier: str
    build_id: str
    con: duckdb.DuckDBPyConnection
    log: logging.Logger
    raw_root: Path
    lake_root: Path
    buckets: list[int] | None
    raw_manifest: RawManifest | None = None

    def provenance_for(self, table: Table) -> tuple[str | None, str | None]:
        """``(source_sha256, raw_snapshot_id)`` from the EP-10 raw manifest (DESIGN §11);
        ``(None, None)`` on the fixture tier / while the manifest is incomplete."""
        if self.raw_manifest is None:
            return None, None
        record = self.raw_manifest.for_table(table)
        return (record.sha256 if record else None, self.raw_manifest.raw_snapshot_id)


@dataclass(frozen=True, slots=True)
class StepOutcome:
    """Counts a handler reports (never values). ``pass1_wall_s`` / ``pass2_wall_s`` come
    from a large partitioned stage (EP-23) and become ``phase: pass1`` / ``pass2``
    benchmark-ledger lines beside the step's ``phase: total`` line."""

    rows: int | None = None
    bytes_in: int | None = None
    bytes_out: int | None = None
    files: int | None = None
    pass1_wall_s: float | None = None
    pass2_wall_s: float | None = None


@dataclass(slots=True)
class StepReport:
    """One step of a :class:`BuildResult`."""

    name: str
    kind: str
    status: StepStatus
    rows: int | None = None
    bytes_out: int | None = None
    files: int | None = None
    wall_s: float = 0.0
    peak_rss_mb: float | None = None
    error: str | None = None


@dataclass(slots=True)
class BuildResult:
    """What one ``run`` did (the CLI renders it; counts and ids only)."""

    build_id: str
    tier: str
    steps: list[StepReport] = field(default_factory=list)
    snapshot_id: str | None = None
    dry_run: bool = False

    @property
    def ok(self) -> bool:
        return all(s.status != "failed" for s in self.steps)


# ---------------------------------------------------------------------------
# Step handlers — a registry dict, so later EPs add kinds without touching run()
# ---------------------------------------------------------------------------


def _run_stage(step: Step, ctx: StepContext) -> StepOutcome:
    """Stage one contract table through the EP-17/18 loader (defaults from the contract;
    the step's ``size_class`` / ``partitioned`` / ``sort_by`` are deliberate overrides)."""
    from mimicwarehouse.loader.buckets import stage_partitioned
    from mimicwarehouse.loader.stage import stage_unpartitioned
    from mimicwarehouse.schema.contract import load_contract

    assert step.schema_name and step.table and step.source  # spec-validated
    contract = load_contract()
    table = contract.table(step.schema_name, step.table)
    # demo tier (EP-22): the source lives under the demo raw root with the dataset dir
    # stripped, and the (identity) 2.2 -> 3.1 column map validates every header on load.
    column_map = contract.column_map("demo_2_2") if ctx.tier == "demo" else None
    rel_source = step.demo_relative_source if ctx.tier == "demo" else step.source
    assert rel_source is not None  # step.source is set, so the demo derivation never Nones
    source = ctx.raw_root / PurePosixPath(rel_source)
    if not source.is_file():
        gz = source.with_name(source.name + ".gz")
        if gz.is_file():
            source = gz
        else:
            raise DagError(f"step {step.name}: source not found under the raw root: {rel_source}")
    dest = loader_paths.table_dir(ctx.lake_root, table.schema_name, table.name)
    source_sha256, raw_snapshot_id = ctx.provenance_for(table)
    partitioned = step.partitioned if step.partitioned is not None else table.partitioned
    if partitioned:
        result = stage_partitioned(
            ctx.con,
            table,
            source,
            dest,
            lake_root=ctx.lake_root,
            build_id=ctx.build_id,
            settings=ctx.settings,
            source_sha256=source_sha256,
            raw_snapshot_id=raw_snapshot_id,
            sort_by=list(step.sort_by) if step.sort_by is not None else None,
            buckets=ctx.buckets,
            size_class=step.size_class,
            column_map=column_map,
        )
    else:
        result = stage_unpartitioned(
            ctx.con,
            table,
            source,
            dest,
            lake_root=ctx.lake_root,
            build_id=ctx.build_id,
            settings=ctx.settings,
            source_sha256=source_sha256,
            raw_snapshot_id=raw_snapshot_id,
            column_map=column_map,
        )
        # dims exist identically in every tier (DESIGN §4): one full stage completes them
        # everywhere (EP-17 left tier_complete/dev_ready to this orchestration).
        update_status(ctx.lake_root, table.qualified_name, tier_complete="full", dev_ready=True)
    return StepOutcome(
        rows=result.rows,
        bytes_in=source.stat().st_size,
        bytes_out=result.bytes,
        files=result.files,
        pass1_wall_s=result.pass1_wall_s,
        pass2_wall_s=result.pass2_wall_s,
    )


def _run_catalog(step: Step, ctx: StepContext) -> StepOutcome:
    """Build and publish the tier's catalog from the staged lake (EP-21;
    :func:`mimicwarehouse.catalog.build.build_catalog`). ``rows`` reports the number of
    cataloged tables/views — the step never touches a data row."""
    from mimicwarehouse.catalog.build import build_catalog

    result = build_catalog(ctx.tier, ctx.settings, lake_root=ctx.lake_root, build_id=ctx.build_id)
    return StepOutcome(rows=result.cataloged, bytes_out=result.bytes, files=1)


#: kind -> handler. EP-37 adds ``sql``; EP-50 ``python``.
STEP_HANDLERS: dict[str, Callable[[Step, StepContext], StepOutcome]] = {
    "stage": _run_stage,
    "catalog": _run_catalog,
}


# ---------------------------------------------------------------------------
# Build id, lock, RSS sampler
# ---------------------------------------------------------------------------


def git_short_sha() -> str:
    """Short git sha of the checkout (``nogit`` outside one / without git)."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(config.workspace_root()),
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except OSError:
        return "nogit"
    return out.stdout.strip() if out.returncode == 0 and out.stdout.strip() else "nogit"


def new_build_id(tier: str) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    return f"{stamp}-{tier}-{git_short_sha()}"


def lock_path(settings: Settings) -> Path:
    return settings.layout["warehouse"] / LOCK_FILENAME


def acquire_lock(settings: Settings, build_id: str, *, break_lock: bool = False) -> Path:
    """Take ``warehouse/.build.lock`` or raise :class:`BuildLockError` (module docstring).

    A live pid always refuses; a stale lock (dead pid, or unreadable) is taken over
    only with ``break_lock``.
    """
    from mimicwarehouse.dag.jobs import pid_alive

    path = lock_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        try:
            held = json.loads(path.read_text(encoding="utf-8"))
            held_pid = int(held.get("pid", 0))
        except (OSError, ValueError):
            held, held_pid = {}, 0
        if held_pid and pid_alive(held_pid):
            raise BuildLockError(
                f"{path}: build {held.get('build_id', '?')} (pid {held_pid}) is running — "
                "one build-profile connection per machine (DESIGN §6); wait for it"
            )
        if not break_lock:
            raise BuildLockError(
                f"{path}: stale lock from build {held.get('build_id', '?')} "
                f"(pid {held_pid or '?'} is not alive) — rerun with --break-lock to take over"
            )
        path.unlink(missing_ok=True)
    import os

    payload = {"pid": os.getpid(), "build_id": build_id, "started": utc_now_iso()}
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8", newline="\n")
    return path


def release_lock(settings: Settings, build_id: str) -> None:
    """Remove the lock iff it still records this build (never someone else's)."""
    path = lock_path(settings)
    try:
        held = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if held.get("build_id") == build_id:
        path.unlink(missing_ok=True)


class _RssSampler(threading.Thread):
    """Samples this process's RSS every ``interval_s`` (daemon); ``peak_mb`` after stop."""

    def __init__(self, interval_s: float = RSS_SAMPLE_S) -> None:
        super().__init__(name="ep19-rss-sampler", daemon=True)
        self._interval = interval_s
        self._stop = threading.Event()
        self._peak = 0

    def _sample(self) -> None:
        try:
            import psutil

            rss = psutil.Process().memory_info().rss
        except Exception:  # pragma: no cover - defensive: a failed probe is just 0
            rss = 0
        self._peak = max(self._peak, rss)

    def run(self) -> None:
        self._sample()
        while not self._stop.wait(self._interval):
            self._sample()

    def stop(self) -> float:
        self._stop.set()
        self._sample()
        return self.peak_mb

    @property
    def peak_mb(self) -> float:
        return round(self._peak / 2**20, 1)


# ---------------------------------------------------------------------------
# The runner
# ---------------------------------------------------------------------------


def resolve_raw_root(settings: Settings, tier: str) -> Path:
    """The tier's raw-CSV root: ``fixture`` -> the committed synthetic tree, ``demo`` ->
    ``ext/demo/mimic-iv-demo-2.2`` (EP-22), ``dev``/``full`` -> ``source material/``."""
    if tier == "fixture":
        from mimicwarehouse.fixtures.write import default_out_dir

        return default_out_dir()
    if tier == "demo":
        from mimicwarehouse.demo import demo_raw_root

        root = demo_raw_root(settings)
        if not root.is_dir():
            raise DagError(f"demo raw root not found: {root} — run `mwh demo fetch` first (EP-22)")
        return root
    if tier in ("dev", "full"):
        return settings.source_root
    raise DagError(f"unknown tier {tier!r}; expected fixture | demo | dev | full")


def run(
    dag: DagSpec,
    tier: Tier | str,
    *,
    select: list[str] | None = None,
    tags: list[str] | None = None,
    force: bool = False,
    dry_run: bool = False,
    data_root: Path | None = None,
    job: str | None = None,
    break_lock: bool = False,
    settings: Settings | None = None,
) -> BuildResult:
    """Execute the DAG for one tier (module docstring) and return a :class:`BuildResult`.

    ``dry_run`` returns the ordered plan (status ``planned``) without touching the lock,
    the lake or DuckDB. ``job`` only labels the log lines — the state file is owned by
    :mod:`~mimicwarehouse.dag.jobs`.
    """
    if tier not in ("fixture", "demo", "dev", "full"):
        raise DagError(f"unknown tier {tier!r}; expected fixture | demo | dev | full")
    if settings is None:
        settings = (
            config.load_settings(data_root=data_root)
            if data_root is not None
            else config.get_settings()
        )
    steps = dag.ordered(select=select, tags=tags, tier=tier)
    if not steps:
        raise DagError("the selection matches no steps for this tier")

    build_id = new_build_id(tier)
    result = BuildResult(build_id=build_id, tier=str(tier), dry_run=dry_run)
    if dry_run:
        result.steps = [StepReport(name=s.name, kind=s.kind, status="planned") for s in steps]
        return result

    prefix = f"[{job}] " if job else ""
    acquire_lock(settings, build_id, break_lock=break_lock)
    try:
        require_free_space(settings.data_root, settings.min_free_gb_for(tier))
        lake_root = settings.lake_root(tier)
        assert_not_credentialed_lake(tier, lake_root, settings)
        lake_root.mkdir(parents=True, exist_ok=True)
        raw_root = resolve_raw_root(settings, tier)
        buckets = list(settings.dev_buckets) if tier == "dev" else None

        raw_manifest: RawManifest | None = None
        if tier in ("dev", "full"):
            from mimicwarehouse.inventory import load_raw_manifest

            raw_manifest = load_raw_manifest(settings)
            if not raw_manifest.records:
                _LOG.warning(
                    "%sEP-10 raw manifest is empty — provenance fields will be null", prefix
                )

        git_sha = git_short_sha()
        host = benchmarks.host_info()
        import duckdb

        _LOG.info(
            "%sbuild %s tier=%s steps=%d raw_root=%s lake_root=%s",
            prefix,
            build_id,
            tier,
            len(steps),
            raw_root,
            lake_root,
        )
        t_run = time.perf_counter()
        con = open_build_connection(settings, tier=tier)
        try:
            ctx = StepContext(
                settings=settings,
                tier=str(tier),
                build_id=build_id,
                con=con,
                log=_LOG,
                raw_root=raw_root,
                lake_root=lake_root,
                buckets=buckets,
                raw_manifest=raw_manifest,
            )
            for step in steps:
                qn = step.qualified_table
                if qn is not None and not force:
                    entry = read_status(lake_root)["steps"].get(qn)
                    if entry is not None and complete_for_tier(entry, str(tier)):
                        _LOG.info(
                            "%sskip %s (%s already complete for %s)", prefix, step.name, qn, tier
                        )
                        result.steps.append(
                            StepReport(name=step.name, kind=step.kind, status="skipped")
                        )
                        continue
                handler = STEP_HANDLERS.get(step.kind)
                if handler is None:
                    raise DagError(
                        f"step {step.name}: no handler registered for kind {step.kind!r} "
                        f"(known: {sorted(STEP_HANDLERS)})"
                    )
                _LOG.info("%sstep %s (%s) start", prefix, step.name, step.kind)
                sampler = _RssSampler()
                sampler.start()
                t0 = time.perf_counter()
                report = StepReport(name=step.name, kind=step.kind, status="done")
                try:
                    outcome = handler(step, ctx)
                except Exception as exc:
                    report.status = "failed"
                    report.error = f"{type(exc).__name__}: {exc}"
                    outcome = StepOutcome()
                finally:
                    report.wall_s = round(time.perf_counter() - t0, 3)
                    report.peak_rss_mb = sampler.stop()
                report.rows = outcome.rows
                report.bytes_out = outcome.bytes_out
                report.files = outcome.files
                result.steps.append(report)
                # per-phase lines first (chronological: pass1 < pass2 < total) — only a
                # large partitioned stage reports them (EP-23; benchmarks module doc)
                for phase, wall in (
                    ("pass1", outcome.pass1_wall_s),
                    ("pass2", outcome.pass2_wall_s),
                ):
                    if wall is None:
                        continue
                    benchmarks.append(
                        benchmarks.BenchmarkLine(
                            ts=utc_now_iso(),
                            build_id=build_id,
                            tier=str(tier),
                            step=step.name,
                            kind=step.kind,
                            phase=phase,  # type: ignore[arg-type]
                            wall_s=round(wall, 3),
                            bytes_in=outcome.bytes_in if phase == "pass1" else None,
                            rows=outcome.rows if phase == "pass2" else None,
                            duckdb_version=duckdb.__version__,
                            git_sha=git_sha,
                            host=host,
                            ok=report.status == "done",
                            error=report.error,
                        ),
                        settings,
                    )
                benchmarks.append(
                    benchmarks.BenchmarkLine(
                        ts=utc_now_iso(),
                        build_id=build_id,
                        tier=str(tier),
                        step=step.name,
                        kind=step.kind,
                        phase="total",
                        wall_s=report.wall_s,
                        peak_rss_mb=report.peak_rss_mb,
                        rows=outcome.rows,
                        bytes_in=outcome.bytes_in,
                        bytes_out=outcome.bytes_out,
                        files=outcome.files,
                        duckdb_version=duckdb.__version__,
                        git_sha=git_sha,
                        host=host,
                        ok=report.status == "done",
                        error=report.error,
                    ),
                    settings,
                )
                if report.status == "failed":
                    _LOG.error(
                        "%sstep %s failed: %s — stopping; completed work stays "
                        "complete (rerun to resume)",
                        prefix,
                        step.name,
                        report.error,
                    )
                    break
                _LOG.info(
                    "%sstep %s done rows=%s bytes=%s files=%s wall=%.1fs rss=%.0fMB",
                    prefix,
                    step.name,
                    outcome.rows,
                    outcome.bytes_out,
                    outcome.files,
                    report.wall_s,
                    report.peak_rss_mb or 0.0,
                )
        finally:
            con.close()

        wall_run = round(time.perf_counter() - t_run, 3)
        if result.ok:
            result.snapshot_id = layer_snapshot(lake_root, LAYER, str(tier), settings=settings)
            record_snapshot(
                lake_root,
                layer=LAYER,
                tier=str(tier),
                snapshot_id=result.snapshot_id,
                build_id=build_id,
            )
            _LOG.info("%ssnapshot %s/%s = %s", prefix, LAYER, tier, result.snapshot_id)
        done = [s for s in result.steps if s.status == "done"]
        benchmarks.append(
            benchmarks.BenchmarkLine(
                ts=utc_now_iso(),
                build_id=build_id,
                tier=str(tier),
                step=None,
                kind="build",
                phase="total",
                wall_s=wall_run,
                rows=sum(s.rows or 0 for s in done) if done else None,
                bytes_out=sum(s.bytes_out or 0 for s in done) if done else None,
                files=sum(s.files or 0 for s in done) if done else None,
                duckdb_version=duckdb.__version__,
                git_sha=git_sha,
                host=host,
                ok=result.ok,
                error=next((s.error for s in result.steps if s.error), None),
            ),
            settings,
        )
        _LOG.info(
            "%sbuild %s %s wall=%.1fs", prefix, build_id, "ok" if result.ok else "FAILED", wall_run
        )
    finally:
        release_lock(settings, build_id)
    return result


def summary_rows(result: BuildResult) -> list[dict[str, Any]]:
    """Plain rows for the CLI summary table (step, kind, status, rows, bytes, wall)."""
    return [
        {
            "step": s.name,
            "kind": s.kind,
            "status": s.status,
            "rows": s.rows,
            "bytes": s.bytes_out,
            "wall_s": s.wall_s,
        }
        for s in result.steps
    ]


__all__ = [
    "LAYER",
    "LOCK_FILENAME",
    "STEP_HANDLERS",
    "BuildLockError",
    "BuildResult",
    "StepContext",
    "StepOutcome",
    "StepReport",
    "acquire_lock",
    "git_short_sha",
    "lock_path",
    "new_build_id",
    "release_lock",
    "resolve_raw_root",
    "run",
    "summary_rows",
]
