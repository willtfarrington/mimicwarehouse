"""Write canary — rehearse P2's write-side I/O shapes against the data root (EP-171; D-42).

P1 proved the **read** side of this machine (EP-10: 98 GB hashed at 2.0-2.4 GB/s with both
endpoint-security products live). The write side is where roadmap Risk 12 puts the risk: the
Malwarebytes Ransomware Protection heuristic judges *processes* by I/O pattern, and EP-17+
will make the allow-listed ``.venv`` ``python.exe`` burst-create thousands of Parquet files
under ``C:\\mimicdata``, rewrite manifests in place and rename-aside catalog files
(DESIGN §5/§6). :func:`run_canary` performs exactly those shapes, in order, with **synthetic
bytes generated in-process** (any id column starts at 90 000 000; no MIMIC data is read or
written), foreground-sized:

1. **burst small-file pass** — N (default 200) ~1 MB Parquet files via DuckDB ``COPY`` into
   Hive-style ``subject_bucket=<n>/`` subdirectories (the EP-18 bucketed-staging shape);
2. **large sequential pass** — one Parquet of ``large_mb`` (default 2,048 MB; the EP-23/26
   event-table shape); wall time and MB/s are recorded separately for both passes;
3. **manifest churn** — line-at-a-time appends to a JSONL manifest beside the files, then
   atomic rewrites via the :func:`mimicwarehouse.fsio.atomic_write_text` retry pattern
   (the EP-19 ledger shape; ``inventory._atomic_write_text`` is the kept alias);
4. **rename-aside swap** — write ``canary.duckdb.new``, ``os.rename`` the live file aside,
   ``os.replace`` the new one in, remove the ``.old`` (the DESIGN §6 catalog protocol);
5. **cleanup** — delete the whole canary tree unless ``keep`` (the delete-loop shape that
   triggered the 2026-08-17 ``bash.exe`` kill, D-42).

The appends of pass 3 and the raw ``os.rename`` / ``os.replace`` / ``os.remove`` sequence of
pass 4 are the EP-33 canon's two **sanctioned exceptions** to :mod:`mimicwarehouse.fsio` and
:mod:`mimicwarehouse.publish` (owner decision at the EP-33 triage checkpoint): the canary
measures the raw OS write pattern against the endpoint-security products, so its writes stay
verbatim and its EP-171 baselines stay comparable — do not route them through the helpers.

After each phase the module **re-reads what it wrote** (sha256 of every file, compared with
the hash taken at write time), so a silent quarantine or in-flight alteration surfaces as a
hard :class:`CanaryError` — and the tree is then left in place as evidence for the
Malwarebytes-Quarantine / ``mbamservice.log`` triage (CLAUDE.md §3). Output is counts,
bytes, seconds and MB/s only — trivially disclosure-safe, everything is synthetic — with
thousands separators in human output (guard G4).

The canary tree lives at ``layout["tmp"] / "canary"`` (the ``canary`` leaf is not a layout
key — this module creates it, the EP-10 ``raw`` precedent). ``mwh canary write`` is **not**
in :data:`mimicwarehouse.cli.DIAGNOSTIC_COMMANDS` — it writes under the data root, so the
D-29 location refusals and :func:`mimicwarehouse.config.require_free_space` run first (the
``mwh inventory`` precedent). Deliberately **not** a doctor check: the doctor's only write
is ``check_data_root``'s writability probe (one temp file created and removed under the
data root; retro CLI-7) — it never rehearses I/O shapes or leaves files behind.

Import budget: imported by ``mwh`` at start-up (the typer sub-app lives here), so duckdb is
imported inside the function that needs it. Errors and ``--json`` follow the EP-33 canon
(:func:`~mimicwarehouse.console.fail` — ``mwh canary:`` on stderr — and
:func:`~mimicwarehouse.console.emit_json`).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

import typer
from rich import box
from rich.markup import escape
from rich.table import Table as RichTable

from mimicwarehouse import config
from mimicwarehouse.config import Settings
from mimicwarehouse.console import EXIT_FINDINGS, EXIT_USAGE, console, emit_json, fail
from mimicwarehouse.fsio import atomic_write_text
from mimicwarehouse.inventory import fmt_int, open_connection

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: ``<data_root>/tmp/<CANARY_DIRNAME>/`` — not a ``Settings.layout`` key; created here.
CANARY_DIRNAME = "canary"
#: Defaults of ``mwh canary write`` (the brief's shapes: 200 x ~1 MB + one 2,048 MB file).
DEFAULT_SMALL_FILES = 200
DEFAULT_LARGE_MB = 2048
#: Refuse ``--large-mb`` above this (a canary is a rehearsal, not a full-tier stage).
LARGE_MB_CAP = 8192
#: ~1 MB per small file at :data:`ROWS_PER_MB`.
SMALL_FILE_MB = 1
#: Rows of the synthetic relation per MB of Parquet: one delta-encoded BIGINT id plus three
#: ``random()`` DOUBLE columns is ~25 incompressible bytes/row, so 40,000 rows ~ 1 MB.
ROWS_PER_MB = 40_000
#: EP-18 staging buckets a small file lands in (``subject_bucket = file index % 100``).
BUCKETS = 100
#: Manifest-churn shape: one append per small-file record, then this many atomic rewrites.
MANIFEST_REWRITES = 20
MANIFEST_NAME = "canary_manifest.jsonl"
#: The rename-aside swap rehearses the catalog file name shape (synthetic bytes inside).
SWAP_NAME = "canary.duckdb"
SWAP_FILE_MB = 8
#: First synthetic id (D-27: fixture/synthetic ids are >= 90 000 000).
FIRST_SYNTHETIC_ID = 90_000_000
#: Phase names, in execution order.
PHASES: tuple[str, ...] = ("small", "large", "manifest", "swap", "cleanup")

#: Checkpoint seam: ``observer(event, path)`` fires at named points (``"<phase>:start"``,
#: ``"<phase>:written"``, the swap steps, ``"cleanup:done"`` / ``"cleanup:kept"``) so tests
#: can assert on-disk state between steps and the CLI can print progress lines.
Observer = Callable[[str, Path], None]


class CanaryError(RuntimeError):
    """Re-read verification failed: a file the canary wrote vanished or changed. The canary
    tree is left in place; triage starts at the Malwarebytes Quarantine (D-42)."""


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PassResult:
    """One phase: counts, bytes, write/verify timings — never a value."""

    name: str
    files: int
    bytes: int
    seconds_write: float
    seconds_verify: float
    ops: dict[str, int] = field(default_factory=dict)

    @property
    def mb_per_s(self) -> float | None:
        if self.seconds_write <= 0 or self.bytes <= 0:
            return None
        return self.bytes / 1e6 / self.seconds_write

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["mb_per_s"] = round(self.mb_per_s, 1) if self.mb_per_s is not None else None
        return d


@dataclass(slots=True)
class CanaryResult:
    """What one :func:`run_canary` invocation did (all five passes completed and verified)."""

    root: Path
    started: str
    finished: str = ""
    seconds: float = 0.0
    small_files: int = DEFAULT_SMALL_FILES
    large_mb: int = DEFAULT_LARGE_MB
    keep: bool = False
    removed_stale_tree: bool = False
    passes: list[PassResult] = field(default_factory=list)

    @property
    def files(self) -> int:
        return sum(p.files for p in self.passes)

    @property
    def bytes(self) -> int:
        return sum(p.bytes for p in self.passes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": str(self.root),
            "started": self.started,
            "finished": self.finished,
            "seconds": self.seconds,
            "small_files": self.small_files,
            "large_mb": self.large_mb,
            "keep": self.keep,
            "removed_stale_tree": self.removed_stale_tree,
            "files": self.files,
            "bytes": self.bytes,
            "survived": True,  # a returned result means no kill and every re-read matched
            "passes": [p.to_dict() for p in self.passes],
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def canary_root(settings: Settings | None = None) -> Path:
    """``<data_root>/tmp/canary`` (created by :func:`run_canary`)."""
    settings = settings or config.get_settings()
    return settings.layout["tmp"] / CANARY_DIRNAME


def _sha256(path: Path) -> tuple[str, int]:
    """Streaming ``(sha256, bytes)`` of ``path``."""
    with path.open("rb") as f:
        digest = hashlib.file_digest(f, "sha256")
    return digest.hexdigest(), path.stat().st_size


@dataclass(frozen=True, slots=True)
class _Written:
    """One file as recorded right after it was written (identity for the re-read check)."""

    path: Path
    bytes: int
    sha256: str


def _record(paths: list[Path]) -> list[_Written]:
    out: list[_Written] = []
    for p in paths:
        digest, size = _sha256(p)
        out.append(_Written(p, size, digest))
    return out


def _verify(phase: str, records: list[_Written]) -> float:
    """Re-read every recorded file and compare bytes + sha256; raise :class:`CanaryError` on
    the first miss (vanished, truncated or altered). Returns the verify wall seconds."""
    t0 = time.perf_counter()
    for rec in records:
        try:
            digest, size = _sha256(rec.path)
        except OSError as exc:
            raise CanaryError(_verify_message(phase, rec.path, f"unreadable ({exc})")) from exc
        if size != rec.bytes:
            raise CanaryError(
                _verify_message(
                    phase, rec.path, f"size changed ({fmt_int(rec.bytes)} -> {fmt_int(size)} bytes)"
                )
            )
        if digest != rec.sha256:
            raise CanaryError(_verify_message(phase, rec.path, "sha256 changed"))
    return time.perf_counter() - t0


def _verify_message(phase: str, path: Path, reason: str) -> str:
    return (
        f"re-read verification failed in pass {phase!r}: {path} {reason} — the canary tree is "
        "left in place; check Malwarebytes Detection History / Quarantine and mbamservice.log "
        "first (D-42, CLAUDE.md section 3)"
    )


def _rmtree_retry(path: Path, *, retries: int = 20) -> None:
    """``shutil.rmtree`` with a brief retry loop: on Windows a scanner or indexer holding a
    handle makes deletes fail transiently with ``PermissionError``."""
    for attempt in range(retries):
        try:
            shutil.rmtree(path)
            return
        except FileNotFoundError:
            return
        except OSError:
            if attempt == retries - 1:
                raise
            time.sleep(0.05 * (attempt + 1))


def _write_random_bytes(path: Path, mb: int) -> None:
    """``mb`` MB of ``os.urandom`` in 1 MB chunks (the swap files' synthetic content)."""
    with path.open("wb") as f:
        for _ in range(mb):
            f.write(os.urandom(1_000_000))


def _copy_parquet(con: Any, path: Path, *, rows: int, first_id: int) -> None:
    """One synthetic Parquet via DuckDB ``COPY``: a delta-friendly BIGINT id column starting
    at ``first_id`` (>= 90 000 000) plus three incompressible ``random()`` doubles."""
    target = path.as_posix().replace("'", "''")
    con.execute(
        f"COPY (SELECT {first_id} + i AS subject_id, random() AS v0, random() AS v1, "
        f"random() AS v2 FROM range({rows}) t(i)) TO '{target}' (FORMAT PARQUET)"
    )


# ---------------------------------------------------------------------------
# The canary
# ---------------------------------------------------------------------------


def run_canary(
    settings: Settings | None = None,
    *,
    small_files: int = DEFAULT_SMALL_FILES,
    large_mb: int = DEFAULT_LARGE_MB,
    keep: bool = False,
    observer: Observer | None = None,
) -> CanaryResult:
    """Run the five write-shape passes against ``<data_root>/tmp/canary`` and verify every
    re-read (module doc). Synthetic bytes only; foreground-sized.

    Raises :class:`ValueError` on bad parameters (``large_mb`` above :data:`LARGE_MB_CAP`),
    :class:`~mimicwarehouse.config.DiskGuardError` below the free-space guard, and
    :class:`CanaryError` when a re-read does not match — the tree is then **not** cleaned up.
    """
    if small_files < 1:
        raise ValueError(f"--small must be >= 1, got {small_files}")
    if large_mb < 1:
        raise ValueError(f"--large-mb must be >= 1, got {large_mb}")
    if large_mb > LARGE_MB_CAP:
        raise ValueError(
            f"--large-mb {fmt_int(large_mb)} is above the canary cap {fmt_int(LARGE_MB_CAP)} MB "
            "— a full-tier-sized write belongs to the loader briefs (EP-17+), not a rehearsal"
        )
    settings = settings or config.get_settings()
    projected_gb = (small_files * SMALL_FILE_MB + large_mb + 2 * SWAP_FILE_MB) / 1000
    config.require_free_space(settings.data_root, settings.min_free_gb + projected_gb)

    def note(event: str, path: Path) -> None:
        if observer is not None:
            observer(event, path)

    root = canary_root(settings)
    result = CanaryResult(
        root=root, started=_now(), small_files=small_files, large_mb=large_mb, keep=keep
    )
    if root.exists():  # leftovers of a crashed / --keep run: start clean
        _rmtree_retry(root)
        result.removed_stale_tree = True
    root.mkdir(parents=True, exist_ok=True)
    t_start = time.perf_counter()

    con = open_connection(settings)  # duckdb_settings("build"); mkdirs tmp_duckdb (CFG-3)
    try:
        # -- 1. burst small-file pass (EP-18 bucketed staging) -----------------------------
        small_dir = root / "small"
        note("small:start", small_dir)
        t0 = time.perf_counter()
        small_paths: list[Path] = []
        for i in range(small_files):
            bucket_dir = small_dir / f"subject_bucket={i % BUCKETS}"
            bucket_dir.mkdir(parents=True, exist_ok=True)
            p = bucket_dir / f"part-{i:05d}.parquet"
            _copy_parquet(
                con,
                p,
                rows=SMALL_FILE_MB * ROWS_PER_MB,
                first_id=FIRST_SYNTHETIC_ID + i * ROWS_PER_MB,
            )
            small_paths.append(p)
        seconds_write = time.perf_counter() - t0
        small_records = _record(small_paths)
        note("small:written", small_dir)
        seconds_verify = _verify("small", small_records)
        result.passes.append(
            PassResult(
                name="small",
                files=len(small_records),
                bytes=sum(r.bytes for r in small_records),
                seconds_write=round(seconds_write, 3),
                seconds_verify=round(seconds_verify, 3),
                ops={"buckets": len({p.parent for p in small_paths})},
            )
        )

        # -- 2. large sequential pass (EP-23/26 event-table shape) -------------------------
        large_dir = root / "large"
        large_dir.mkdir(parents=True, exist_ok=True)
        large_path = large_dir / "events.parquet"
        note("large:start", large_path)
        t0 = time.perf_counter()
        _copy_parquet(con, large_path, rows=large_mb * ROWS_PER_MB, first_id=FIRST_SYNTHETIC_ID)
        seconds_write = time.perf_counter() - t0
        large_records = _record([large_path])
        note("large:written", large_path)
        seconds_verify = _verify("large", large_records)
        result.passes.append(
            PassResult(
                name="large",
                files=1,
                bytes=large_records[0].bytes,
                seconds_write=round(seconds_write, 3),
                seconds_verify=round(seconds_verify, 3),
            )
        )

        # -- 3. manifest churn (EP-19 ledger shape) ----------------------------------------
        # Sanctioned exception to the fsio.append_jsonl canon (module docstring): the buffered
        # append + flush per line below is the raw pattern the canary measures; the atomic
        # rewrites use fsio.atomic_write_text because that *is* the raw pattern (temp + replace).
        manifest = root / MANIFEST_NAME
        note("manifest:start", manifest)
        t0 = time.perf_counter()
        lines = [
            json.dumps(
                {
                    "rel_path": r.path.relative_to(root).as_posix(),
                    "bytes": r.bytes,
                    "sha256": r.sha256,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            for r in [*small_records, *large_records]
        ]
        with manifest.open("a", encoding="utf-8", newline="\n") as f:
            for line in lines:  # one append + flush per line — the append-only ledger shape
                f.write(line + "\n")
                f.flush()
        for rev in range(MANIFEST_REWRITES):  # the snapshot-rewrite shape (EP-10 flush())
            payload = "\n".join([json.dumps({"rev": rev}, separators=(",", ":")), *lines]) + "\n"
            atomic_write_text(manifest, payload)
        seconds_write = time.perf_counter() - t0
        manifest_records = _record([manifest])
        note("manifest:written", manifest)
        seconds_verify = _verify("manifest", manifest_records)
        result.passes.append(
            PassResult(
                name="manifest",
                files=1,
                bytes=manifest_records[0].bytes,
                seconds_write=round(seconds_write, 3),
                seconds_verify=round(seconds_verify, 3),
                ops={"appends": len(lines), "rewrites": MANIFEST_REWRITES},
            )
        )

        # -- 4. rename-aside swap (DESIGN section 6 catalog protocol) ----------------------
        # Sanctioned exception to the publish.swap_file canon (module docstring): the inline
        # os.rename / os.replace / os.remove sequence is the raw pattern the canary measures;
        # production code swaps through mimicwarehouse.publish (retries, crash recovery).
        swap_dir = root / "swap"
        swap_dir.mkdir(parents=True, exist_ok=True)
        live = swap_dir / SWAP_NAME
        new = swap_dir / (SWAP_NAME + ".new")
        old = swap_dir / (SWAP_NAME + ".old")
        note("swap:start", swap_dir)
        t0 = time.perf_counter()
        _write_random_bytes(live, SWAP_FILE_MB)  # the first stage: a live file to swap over
        note("swap:live-written", live)
        _write_random_bytes(new, SWAP_FILE_MB)
        new_records = _record([new])
        note("swap:new-written", new)
        os.rename(live, old)  # succeeds with readers open (FILE_SHARE_DELETE)
        note("swap:aside", old)
        os.replace(new, live)
        note("swap:replaced", live)
        os.remove(old)
        note("swap:old-removed", live)
        seconds_write = time.perf_counter() - t0
        # the live file must now be byte-identical to what was written as .new
        swap_records = [_Written(live, new_records[0].bytes, new_records[0].sha256)]
        seconds_verify = _verify("swap", swap_records)
        for leftover in (new, old):
            if leftover.exists():
                raise CanaryError(_verify_message("swap", leftover, "should not exist after swap"))
        result.passes.append(
            PassResult(
                name="swap",
                files=1,
                bytes=2 * SWAP_FILE_MB * 1_000_000,  # live + new were both written
                seconds_write=round(seconds_write, 3),
                seconds_verify=round(seconds_verify, 3),
                ops={"swaps": 1},
            )
        )
    finally:
        con.close()

    # -- 5. cleanup (the delete-loop shape) ------------------------------------------------
    note("cleanup:start", root)
    t0 = time.perf_counter()
    if keep:
        note("cleanup:kept", root)
        cleanup_ops = {"kept": 1, "deleted": 0}
    else:
        _rmtree_retry(root)
        if root.exists():
            raise CanaryError(_verify_message("cleanup", root, "still exists after delete"))
        note("cleanup:done", root)
        cleanup_ops = {"kept": 0, "deleted": 1}
    result.passes.append(
        PassResult(
            name="cleanup",
            files=0,
            bytes=0,
            seconds_write=round(time.perf_counter() - t0, 3),
            seconds_verify=0.0,
            ops=cleanup_ops,
        )
    )

    result.finished = _now()
    result.seconds = round(time.perf_counter() - t_start, 3)
    return result


# ---------------------------------------------------------------------------
# CLI — mwh canary write
# ---------------------------------------------------------------------------

canary_app = typer.Typer(
    name="canary",
    help="Write-path canary: rehearse the loader's I/O shapes (Parquet burst, large "
    "sequential write, manifest churn, rename-aside swap, delete loop) with synthetic bytes "
    "under <data_root>\\tmp\\canary and verify every re-read (EP-171; D-42, Risk 12). "
    "Counts, bytes and timings only - no data.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)


def _settings(ctx: typer.Context) -> Settings:
    state = ctx.obj
    settings = getattr(state, "settings", None)
    return settings if isinstance(settings, Settings) else config.get_settings()


def _fmt_seconds(value: float) -> str:
    return f"{value:,.1f}"


def _progress(event: str, path: Path) -> None:
    if event.endswith(":start"):
        console.print(f"canary: {event.split(':')[0]} pass -> {escape(str(path))}", highlight=False)


@canary_app.command("write")
def write_command(
    ctx: typer.Context,
    small: Annotated[
        int,
        typer.Option("--small", help="Small Parquet files in the burst pass (~1 MB each)."),
    ] = DEFAULT_SMALL_FILES,
    large_mb: Annotated[
        int,
        typer.Option(
            "--large-mb",
            help=f"Size of the one large Parquet in MB (cap {LARGE_MB_CAP:,}).",
        ),
    ] = DEFAULT_LARGE_MB,
    keep: Annotated[
        bool, typer.Option("--keep", help="Keep the canary tree instead of deleting it.")
    ] = False,
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    """Run the five write-shape passes and verify every re-read; exit 0 only when the
    process survived and every file read back byte-identical."""
    settings = _settings(ctx)
    try:
        result = run_canary(
            settings,
            small_files=small,
            large_mb=large_mb,
            keep=keep,
            observer=None if as_json else _progress,
        )
    except ValueError as exc:
        fail("mwh canary", str(exc), code=EXIT_USAGE)
    except config.DiskGuardError as exc:
        fail("mwh canary", str(exc), code=EXIT_USAGE)
    except CanaryError as exc:
        fail("mwh canary", str(exc), code=EXIT_FINDINGS)

    if as_json:
        emit_json(result.to_dict())
        return
    rt = RichTable(box=box.SIMPLE, header_style="bold")
    for c in ("pass", "files", "bytes", "write s", "MB/s", "verify s", "ops"):
        rt.add_column(c, overflow="fold", justify="left" if c in {"pass", "ops"} else "right")
    for p in result.passes:
        rt.add_row(
            p.name,
            fmt_int(p.files),
            fmt_int(p.bytes),
            _fmt_seconds(p.seconds_write),
            f"{p.mb_per_s:,.0f}" if p.mb_per_s is not None else "-",
            _fmt_seconds(p.seconds_verify),
            " ".join(f"{k}={fmt_int(v)}" for k, v in p.ops.items()) or "-",
        )
    console.print(rt)
    tree = f"tree kept at {result.root}" if keep else f"tree removed ({result.root} absent)"
    console.print(
        f"canary write: OK - {len(result.passes)} passes, {fmt_int(result.files)} files, "
        f"{fmt_int(result.bytes)} bytes in {result.seconds:,.1f}s; process survived and every "
        f"re-read matched; {escape(tree)}",
        highlight=False,
    )


__all__ = [
    "BUCKETS",
    "CANARY_DIRNAME",
    "DEFAULT_LARGE_MB",
    "DEFAULT_SMALL_FILES",
    "LARGE_MB_CAP",
    "MANIFEST_NAME",
    "MANIFEST_REWRITES",
    "PHASES",
    "ROWS_PER_MB",
    "SWAP_NAME",
    "CanaryError",
    "CanaryResult",
    "PassResult",
    "canary_app",
    "canary_root",
    "run_canary",
]
