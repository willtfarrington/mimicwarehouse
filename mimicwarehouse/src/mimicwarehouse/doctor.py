"""``mwh doctor`` — host health checks (EP-2; helper of :mod:`mimicwarehouse.cli`).

Pure check functions returning :class:`CheckResult`, assembled by :func:`run_checks` and
serialised by :func:`doctor_report` into the object EP-35 embeds in every run manifest
(GOVERNANCE §2: BitLocker re-checked and recorded per run). Checks follow D-14/D-15/D-38
and DESIGN §2-3: managed CPython 3.13 in the workspace ``.venv``, uv, the DuckDB pin, free
disk on the data-root and repository drives (the ≥ 100 GB rule), data-root presence,
BitLocker, GPU/driver (informational until EP-121, D-16) and ``LongPathsEnabled``.

Nothing here opens a data file. Every external probe (``uv``, ``git``, ``nvidia-smi``,
PowerShell, the registry) goes through a small module-level helper with a 10 s timeout so
a missing tool yields ``warn``/``info`` — never a traceback — and so tests can monkeypatch
the helper (or :func:`subprocess.run`) and never shell out. Windows-only probes report
``info`` on other hosts.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path, PureWindowsPath
from typing import TYPE_CHECKING, Annotated, Any, Literal

import typer
from rich.table import Table

if TYPE_CHECKING:  # avoid a runtime import cycle (cli.py imports this module)
    from mimicwarehouse.cli import CliState

Status = Literal["pass", "warn", "fail", "info"]

#: Order and identity of the eight checks; ``mwh doctor --json`` always emits exactly these.
CHECK_IDS: tuple[str, ...] = (
    "python",
    "uv",
    "duckdb",
    "disk_free",
    "data_root",
    "bitlocker",
    "gpu",
    "longpaths",
)

IS_WINDOWS: bool = sys.platform == "win32"
SUBPROCESS_TIMEOUT_S = 10
GB = 2**30  # GiB — the unit Explorer / Win32_LogicalDisk report as "GB" (DESIGN §2-3)
DISK_FAIL_GB = 100  # DESIGN §3: never below 100 GB free
DISK_WARN_GB = 150
REQUIRED_DATA_DRIVE = "C:"  # D-29: local NVMe only; EP-3 turns the warning into a refusal

#: Meaning of ``System.Volume.BitLockerProtection`` (Shell COM; no elevation needed).
BITLOCKER_STATES: dict[int, str] = {
    1: "on",
    2: "off",
    3: "encrypting",
    4: "decrypting",
    5: "suspended",
    6: "locked",
    8: "waiting for activation",
}


@dataclass(frozen=True, slots=True)
class CheckResult:
    """One doctor finding. ``value`` is JSON-serialisable detail for manifests (EP-35)."""

    id: str
    status: Status
    detail: str
    value: Any = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ProbeError(RuntimeError):
    """A probe tool is missing, hung or errored; checks turn this into warn/info."""


# ---------------------------------------------------------------------------
# Probe helpers — the only places that touch the outside world. Tests monkeypatch these
# (or ``subprocess.run`` itself) so the suite never shells out.
# ---------------------------------------------------------------------------


def _run(argv: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Run a tool with a hard timeout; callers catch :class:`ProbeError`."""
    try:
        return subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_S,
            check=False,
            cwd=cwd,
        )
    except FileNotFoundError as exc:
        raise ProbeError(f"{argv[0]} not found on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise ProbeError(f"{argv[0]} timed out after {SUBPROCESS_TIMEOUT_S} s") from exc
    except OSError as exc:  # permission, broken exe, …
        raise ProbeError(f"{argv[0]} could not run ({exc.__class__.__name__})") from exc


def _uv_version() -> str:
    proc = _run(["uv", "--version"])
    if proc.returncode != 0:
        raise ProbeError(f"uv --version exited {proc.returncode}")
    return proc.stdout.strip()


def _duckdb_installed_version() -> str:
    import duckdb  # deliberately not imported at CLI import time (brief item 1)

    return duckdb.__version__


def _duckdb_pinned_version() -> str | None:
    """The ``duckdb==X`` pin declared by the installed ``mimicwarehouse`` distribution."""
    try:
        requires = metadata.requires("mimicwarehouse") or []
    except metadata.PackageNotFoundError:
        return None
    for req in requires:
        m = re.match(r"^duckdb\s*==\s*([0-9][\w.]*)", req)
        if m:
            return m.group(1)
    return None


def _bitlocker_protection(drive: str) -> int | None:
    """``System.Volume.BitLockerProtection`` for ``drive`` (e.g. ``"C:"``); None if unknown."""
    script = (
        "(New-Object -ComObject Shell.Application)"
        f".NameSpace('{drive}\\').Self.ExtendedProperty('System.Volume.BitLockerProtection')"
    )
    proc = _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script])
    text = proc.stdout.strip()
    if proc.returncode != 0 or not text:
        return None
    try:
        return int(text.splitlines()[-1].strip())
    except ValueError:
        return None


def _nvidia_smi() -> str:
    proc = _run(
        [
            "nvidia-smi",
            "--query-gpu=name,memory.total,driver_version",
            "--format=csv,noheader",
        ]
    )
    if proc.returncode != 0:
        raise ProbeError(f"nvidia-smi exited {proc.returncode}")
    return proc.stdout.strip()


def _longpaths_registry() -> int | None:
    """``HKLM\\SYSTEM\\CurrentControlSet\\Control\\FileSystem\\LongPathsEnabled`` or None."""
    try:
        import winreg
    except ImportError:  # pragma: no cover - non-Windows
        return None
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\FileSystem"
        ) as key:
            value, _kind = winreg.QueryValueEx(key, "LongPathsEnabled")
    except OSError:
        return None
    return int(value) if isinstance(value, int) else None


def _git_longpaths(repo: Path | None) -> str | None:
    proc = _run(["git", "config", "--get", "core.longpaths"], cwd=repo)
    return proc.stdout.strip() or None


def repo_root() -> Path | None:
    """The git checkout containing this package (``…/mimicwarehouse/src/mimicwarehouse``)."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / ".git").exists():
            return parent
    return None


def _drive_of(path: Path) -> str:
    """``"C:"`` on Windows, the anchor (``"/"``) elsewhere — labels only, never opened."""
    if IS_WINDOWS:
        return PureWindowsPath(path).drive.upper() or Path(path).anchor
    return Path(path).anchor or "/"


def _mount_of(path: Path) -> str:
    """The filesystem root ``shutil.disk_usage`` should be asked about."""
    if IS_WINDOWS:
        drive = PureWindowsPath(path).drive
        return f"{drive}\\" if drive else str(path)
    return Path(path).anchor or "/"


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def check_python() -> CheckResult:
    version = ".".join(str(p) for p in sys.version_info[:3])
    exe = Path(sys.executable).resolve()
    in_venv = ".venv" in exe.parts or Path(sys.prefix).name == ".venv"
    value = {"version": version, "in_venv": in_venv}
    if sys.version_info[:2] == (3, 13) and in_venv:
        return CheckResult("python", "pass", f"CPython {version} in .venv", value)
    if sys.version_info[:2] != (3, 13):
        return CheckResult(
            "python", "fail", f"CPython {version}; expected uv-managed 3.13 (D-15)", value
        )
    return CheckResult(
        "python", "fail", f"CPython {version} outside a .venv — run via `uv run`", value
    )


def check_uv() -> CheckResult:
    try:
        text = _uv_version()
    except ProbeError as exc:
        return CheckResult("uv", "warn", f"{exc} — install with `winget install astral-sh.uv`")
    return CheckResult("uv", "pass", text, {"version": text})


def check_duckdb() -> CheckResult:
    pinned = _duckdb_pinned_version()
    try:
        installed = _duckdb_installed_version()
    except Exception as exc:  # ImportError or a broken extension load
        return CheckResult(
            "duckdb", "fail", f"duckdb import failed ({exc.__class__.__name__})", {"pin": pinned}
        )
    value = {"installed": installed, "pin": pinned}
    if pinned is None:
        return CheckResult("duckdb", "warn", f"duckdb {installed}; no exact pin found", value)
    if installed == pinned:
        return CheckResult("duckdb", "pass", f"duckdb {installed} == pin", value)
    return CheckResult(
        "duckdb", "fail", f"duckdb {installed} != pin {pinned} — run `uv sync`", value
    )


def check_disk_free(data_root: Path, repo: Path | None = None) -> CheckResult:
    """Free space on the data-root drive and on the repository drive (DESIGN §3 rule)."""
    roots: dict[str, str] = {_drive_of(data_root): _mount_of(data_root)}  # label → mount
    repo_path = repo or repo_root() or Path.cwd()
    roots.setdefault(_drive_of(repo_path), _mount_of(repo_path))

    value: dict[str, Any] = {}
    status: Status = "pass"
    parts: list[str] = []
    for label, mount in roots.items():
        try:
            usage = shutil.disk_usage(mount)
        except OSError:
            value[label] = None
            parts.append(f"{label} unavailable")
            if status == "pass":
                status = "warn"
            continue
        free_gb = round(usage.free / GB, 1)
        total_gb = round(usage.total / GB, 1)
        value[label] = {"free_gb": free_gb, "total_gb": total_gb}
        parts.append(f"{label} {free_gb:g} / {total_gb:g} GB free")
        if free_gb < DISK_FAIL_GB:
            status = "fail"
        elif free_gb < DISK_WARN_GB and status != "fail":
            status = "warn"
    detail = " · ".join(parts)
    if status == "fail":
        detail += f" — below {DISK_FAIL_GB} GB; builds refuse to start (DESIGN §3)"
    elif status == "warn" and any(v for v in value.values()):
        detail += f" — keep ≥ {DISK_WARN_GB} GB before a full build"
    return CheckResult("disk_free", status, detail, value)


def check_data_root(data_root: Path) -> CheckResult:
    """Presence, writability and drive of the data root (D-29). EP-3 upgrades to a refusal."""
    data_root = Path(data_root)
    drive = _drive_of(data_root)
    value: dict[str, Any] = {"path": str(data_root), "drive": drive, "exists": data_root.exists()}
    warnings: list[str] = []
    if IS_WINDOWS and drive != REQUIRED_DATA_DRIVE:
        warnings.append(
            f"on {drive or 'an unknown drive'}, not {REQUIRED_DATA_DRIVE} — "
            "local NVMe only, never a synced/virtual drive (D-29; EP-3 refuses)"
        )
    writable = False
    if not data_root.exists():
        warnings.append("missing — run `mwh paths --create` (EP-3)")
    elif not data_root.is_dir():
        warnings.append("exists but is not a directory")
    else:
        try:
            with tempfile.NamedTemporaryFile(dir=data_root, prefix=".mwh-doctor-", suffix=".tmp"):
                writable = True
        except OSError:
            warnings.append("exists but is not writable")
    value["writable"] = writable
    if warnings:
        return CheckResult("data_root", "warn", f"{data_root}: " + "; ".join(warnings), value)
    return CheckResult("data_root", "pass", f"{data_root} exists and is writable", value)


def check_bitlocker(drives: Iterable[str]) -> CheckResult:
    """BitLocker protection on each drive (GOVERNANCE §2 requires it on C:)."""
    if not IS_WINDOWS:
        return CheckResult("bitlocker", "info", "not a Windows host — BitLocker probe skipped")
    value: dict[str, int | None] = {}
    status: Status = "pass"
    parts: list[str] = []
    for drive in dict.fromkeys(d for d in drives if d):
        try:
            code = _bitlocker_protection(drive)
        except ProbeError:
            code = None
        value[drive] = code
        if code == 1:
            parts.append(f"{drive} on")
        elif code == 2:
            parts.append(f"{drive} off — encrypt before touching data (GOVERNANCE §2)")
            status = "fail"
        elif code in (3, 5):
            parts.append(f"{drive} {BITLOCKER_STATES[code]}")
            if status != "fail":
                status = "warn"
        else:
            label = BITLOCKER_STATES.get(code, "unknown") if code is not None else "unknown"
            parts.append(f"{drive} {label} — run `manage-bde -status {drive}` elevated")
            if status != "fail":
                status = "warn"
    if not parts:
        return CheckResult("bitlocker", "warn", "no drive to probe", value)
    return CheckResult("bitlocker", status, " · ".join(parts), value)


def check_gpu() -> CheckResult:
    """Informational only until EP-121 (`mwh doctor --gpu`); never fails (D-16)."""
    try:
        text = _nvidia_smi()
    except ProbeError:
        return CheckResult("gpu", "info", "no NVIDIA driver on PATH (CPU-first, D-16)", None)
    first = text.splitlines()[0] if text else ""
    return CheckResult("gpu", "info", first or "nvidia-smi returned nothing", {"nvidia_smi": text})


def check_longpaths(repo: Path | None = None) -> CheckResult:
    """``LongPathsEnabled`` in the registry and ``core.longpaths=true`` in git (D-38)."""
    if not IS_WINDOWS:
        return CheckResult("longpaths", "info", "not a Windows host — MAX_PATH does not apply")
    reg = _longpaths_registry()
    try:
        git = _git_longpaths(repo if repo is not None else repo_root())
    except ProbeError:
        git = None
    value = {"registry": reg, "git_core_longpaths": git}
    reg_ok = reg == 1
    git_ok = (git or "").lower() == "true"
    if reg_ok and git_ok:
        return CheckResult("longpaths", "pass", "LongPathsEnabled=1 · core.longpaths=true", value)
    problems: list[str] = []
    if not reg_ok:
        problems.append(
            f"registry LongPathsEnabled={reg!r} — set to 1 (elevated) and reboot (D-38)"
        )
    if not git_ok:
        problems.append(f"git core.longpaths={git!r} — `git config core.longpaths true`")
    return CheckResult("longpaths", "warn", "; ".join(problems), value)


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def run_checks(data_root: Path) -> list[CheckResult]:
    """Run the eight checks in :data:`CHECK_IDS` order for ``data_root``."""
    data_root = Path(data_root)
    repo = repo_root()
    drives = [_drive_of(data_root), _drive_of(repo or Path.cwd())]
    checks: list[Callable[[], CheckResult]] = [
        check_python,
        check_uv,
        check_duckdb,
        lambda: check_disk_free(data_root, repo),
        lambda: check_data_root(data_root),
        lambda: check_bitlocker(drives),
        check_gpu,
        lambda: check_longpaths(repo),
    ]
    results = [c() for c in checks]
    assert [r.id for r in results] == list(CHECK_IDS)
    return results


def doctor_report(results: list[CheckResult]) -> dict[str, Any]:
    """The JSON object ``mwh doctor --json`` prints and EP-35 embeds in run manifests."""
    return {
        "timestamp": datetime.now(UTC).isoformat(timespec="seconds"),
        "host": platform.node(),
        "checks": [r.to_dict() for r in results],
        "ok": not any(r.status == "fail" for r in results),
    }


_GLYPHS_UTF = {"pass": "✓", "warn": "!", "fail": "✗", "info": "i"}
_GLYPHS_ASCII = {"pass": "OK", "warn": "!", "fail": "X", "info": "i"}
_STYLES = {"pass": "green", "warn": "yellow", "fail": "bold red", "info": "cyan"}


def render_table(results: list[CheckResult], *, utf: bool = True) -> Table:
    glyphs = _GLYPHS_UTF if utf else _GLYPHS_ASCII
    table = Table(title="mwh doctor", show_lines=False, pad_edge=False)
    table.add_column("check", style="bold")
    table.add_column("status", justify="center")
    table.add_column("detail", overflow="fold")
    for r in results:
        style = _STYLES[r.status]
        table.add_row(r.id, f"[{style}]{glyphs[r.status]} {r.status}[/]", r.detail)
    return table


def summary_line(results: list[CheckResult]) -> str:
    counts = {s: sum(1 for r in results if r.status == s) for s in ("pass", "warn", "fail", "info")}
    verdict = "OK" if counts["fail"] == 0 else "FAILED"
    return (
        f"doctor: {counts['pass']} pass · {counts['warn']} warn · {counts['fail']} fail · "
        f"{counts['info']} info — {verdict}"
    )


# ---------------------------------------------------------------------------
# CLI command (attached in cli.py with one ``app.command()`` line)
# ---------------------------------------------------------------------------


def doctor_command(
    ctx: typer.Context,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print the report as JSON (the object run manifests embed)."),
    ] = False,
) -> None:
    """Host health: python · uv · duckdb · disk_free · data_root · bitlocker · gpu · longpaths."""
    state: CliState = ctx.obj
    results = run_checks(state.data_root)
    report = doctor_report(results)
    if json_output:
        sys.stdout.write(json.dumps(report, indent=2) + os.linesep)
    else:
        from mimicwarehouse.cli import console

        utf = (console.encoding or "").lower().replace("-", "").startswith("utf")
        console.print(render_table(results, utf=utf))
        console.print(summary_line(results), style="bold" if report["ok"] else "bold red")
    raise typer.Exit(code=0 if report["ok"] else 1)
