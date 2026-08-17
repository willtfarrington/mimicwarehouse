"""``mwh doctor`` — host health checks (EP-2, upgraded EP-3; helper of :mod:`mimicwarehouse.cli`).

Pure check functions returning :class:`CheckResult`, assembled by :func:`run_checks` and
serialised by :func:`doctor_report` into the object EP-35 embeds in every run manifest
(GOVERNANCE §2: BitLocker re-checked and recorded per run). Checks follow D-14/D-15/D-29/
D-38 and DESIGN §2-3/§6: managed CPython 3.13 in the workspace ``.venv``, uv, the DuckDB pin,
the settings sources in use, free disk on the data-root and repository drives (the ≥ 100 GB
rule), the data root (**fails** on an unsafe location — synced/virtual/network volume,
FAT32/cryptoFs, OneDrive, forbidden drive letter), the DuckDB temp dir (same volume),
mounted cloud/virtual volumes (letters + labels only), the Defender exclusion, BitLocker,
the power scheme, GPU/driver (informational until EP-121, D-16) and ``LongPathsEnabled``.

Nothing here opens a data file. Every external probe (``uv``, ``git``, ``nvidia-smi``,
``powercfg``, PowerShell, the registry, Win32 volume APIs via :mod:`mimicwarehouse.config`)
goes through a small module-level helper with a 10 s timeout so a missing tool yields
``warn``/``info`` — never a traceback — and so tests can monkeypatch the helper (or
:func:`subprocess.run`) and never shell out. Windows-only probes report ``info`` on other
hosts. The report never prints environment variables wholesale, any path under
``source material/`` (only whether it exists), or anything below directory level.
"""

from __future__ import annotations

import json
import os
import platform
import re
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

from mimicwarehouse import config
from mimicwarehouse.config import DEFAULT_FORBIDDEN_DRIVES, Settings, repo_root

if TYPE_CHECKING:  # avoid a runtime import cycle (cli.py imports this module)
    from mimicwarehouse.cli import CliState

Status = Literal["pass", "warn", "fail", "info"]

#: Order and identity of the checks; ``mwh doctor --json`` always emits exactly these.
CHECK_IDS: tuple[str, ...] = (
    "python",
    "uv",
    "duckdb",
    "settings",
    "disk_free",
    "data_root",
    "temp_dir",
    "cloud_mounts",
    "defender",
    "bitlocker",
    "power_scheme",
    "gpu",
    "longpaths",
)

IS_WINDOWS: bool = sys.platform == "win32"
SUBPROCESS_TIMEOUT_S = 10
GB = config.GB  # GiB — the unit Explorer / Win32_LogicalDisk report as "GB" (DESIGN §2-3)
DISK_FAIL_GB = 100  # DESIGN §3: never below 100 GB free (Settings.min_free_gb overrides)
DISK_WARN_MARGIN_GB = 50  # warn within this margin above the fail line

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

#: Windows 11 power-mode overlays (``ActiveOverlayAcPowerScheme``), D-38.
POWER_OVERLAYS: dict[str, str] = {
    "00000000-0000-0000-0000-000000000000": "default (Balanced)",
    "961cc777-2547-4f9d-8174-7d86181b8a7a": "Best power efficiency",
    "3af9b8d9-7c97-431d-ad78-34a8bfea439f": "Better performance",
    "ded574b5-45a0-4f42-8737-46345c09c238": "Best performance",
}
BEST_PERFORMANCE_OVERLAY = "ded574b5-45a0-4f42-8737-46345c09c238"


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


def _powershell(script: str) -> subprocess.CompletedProcess[str]:
    """Windows PowerShell (always present; ``pwsh`` is not) with a 10 s timeout."""
    return _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script])


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
    proc = _powershell(script)
    text = proc.stdout.strip()
    if proc.returncode != 0 or not text:
        return None
    try:
        return int(text.splitlines()[-1].strip())
    except ValueError:
        return None


def _defender_exclusions() -> list[str] | None:
    """Defender path exclusions, or None when not elevated / unavailable (D-38).

    ``Get-MpPreference`` prints ``N/A: Must be an administrator to view exclusions`` for a
    non-elevated shell (exit code 0), so that text — not the exit code — is the signal.
    """
    proc = _powershell("(Get-MpPreference).ExclusionPath")
    if proc.returncode != 0:
        raise ProbeError(f"Get-MpPreference exited {proc.returncode}")
    lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    if not lines or any("must be an administrator" in line.lower() for line in lines):
        return None
    return lines


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


def _powercfg_active_scheme() -> str:
    proc = _run(["powercfg", "/getactivescheme"])
    if proc.returncode != 0:
        raise ProbeError(f"powercfg exited {proc.returncode}")
    return proc.stdout.strip()


def _registry_value(subkey: str, name: str) -> Any:
    """``HKLM\\<subkey>\\<name>`` or None (non-Windows / missing)."""
    try:
        import winreg
    except ImportError:  # pragma: no cover - non-Windows
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, subkey) as key:
            value, _kind = winreg.QueryValueEx(key, name)
    except OSError:
        return None
    return value


def _longpaths_registry() -> int | None:
    """``HKLM\\SYSTEM\\CurrentControlSet\\Control\\FileSystem\\LongPathsEnabled`` or None."""
    value = _registry_value(r"SYSTEM\CurrentControlSet\Control\FileSystem", "LongPathsEnabled")
    return int(value) if isinstance(value, int) else None


def _power_overlay_registry() -> str | None:
    """``ActiveOverlayAcPowerScheme`` (the Windows 11 power-mode overlay GUID) or None."""
    value = _registry_value(
        r"SYSTEM\CurrentControlSet\Control\Power\User\PowerSchemes", "ActiveOverlayAcPowerScheme"
    )
    return str(value).lower() if value else None


def _git_longpaths(repo: Path | None) -> str | None:
    proc = _run(["git", "config", "--get", "core.longpaths"], cwd=repo)
    return proc.stdout.strip() or None


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


def check_settings(settings: Settings) -> CheckResult:
    """Which sources are in use, whether ``.env`` / ``mwh.toml`` / ``source_root`` exist
    (existence only — never a listing or the raw path), and the ``allow_remote`` gate."""
    workspace = config.workspace_root()
    dotenv_present = (workspace / ".env").is_file()
    toml_present = (workspace / "mwh.toml").is_file()
    sources = settings.sources()
    used = [f"{name}: {', '.join(fields)}" for name, fields in sources.items() if fields]
    source_root_present = settings.source_root.is_dir()
    value: dict[str, Any] = {
        "sources": sources,
        "dotenv_present": dotenv_present,
        "toml_present": toml_present,
        "source_root_present": source_root_present,
        "default_tier": settings.default_tier,
        "allow_remote": settings.allow_remote,
        "k_suppression": settings.k_suppression,
    }
    parts = [
        "sources: " + ("; ".join(used) if used else "defaults only"),
        f".env {'present' if dotenv_present else 'absent'}",
        f"mwh.toml {'present' if toml_present else 'absent'}",
        f"source_root {'present' if source_root_present else 'absent'}",
        f"tier={settings.default_tier}",
        f"k={settings.k_suppression}",
        f"allow_remote={str(settings.allow_remote).lower()}",
    ]
    if settings.allow_remote:
        parts.append("— remote calls from text modules are enabled; GOVERNANCE §9 wants false")
        return CheckResult("settings", "warn", " · ".join(parts), value)
    return CheckResult("settings", "info", " · ".join(parts), value)


def check_disk_free(
    data_root: Path, repo: Path | None = None, min_free_gb: float = DISK_FAIL_GB
) -> CheckResult:
    """Free space on the data-root drive and on the repository drive (DESIGN §3 rule)."""
    roots: dict[str, str] = {_drive_of(data_root): _mount_of(data_root)}  # label → mount
    repo_path = repo or repo_root() or Path.cwd()
    roots.setdefault(_drive_of(repo_path), _mount_of(repo_path))
    warn_gb = min_free_gb + DISK_WARN_MARGIN_GB

    value: dict[str, Any] = {}
    status: Status = "pass"
    parts: list[str] = []
    for label, mount in roots.items():
        try:
            space = config.check_free_space(mount, min_free_gb)
        except OSError:
            value[label] = None
            parts.append(f"{label} unavailable")
            if status == "pass":
                status = "warn"
            continue
        value[label] = {"free_gb": space.free_gb, "total_gb": space.total_gb}
        parts.append(f"{label} {space.free_gb:g} / {space.total_gb:g} GB free")
        if not space.ok:
            status = "fail"
        elif space.free_gb < warn_gb and status != "fail":
            status = "warn"
    detail = " · ".join(parts)
    if status == "fail":
        detail += f" — below {min_free_gb:g} GB; builds refuse to start (DESIGN §3)"
    elif status == "warn" and any(v for v in value.values()):
        detail += f" — keep >= {warn_gb:g} GB before a full build"
    return CheckResult("disk_free", status, detail, value)


def check_data_root(
    data_root: Path, forbidden_drives: Iterable[str] = DEFAULT_FORBIDDEN_DRIVES
) -> CheckResult:
    """Location safety (D-29 — **fail**), presence and writability of the data root."""
    data_root = Path(data_root)
    drive = _drive_of(data_root)
    value: dict[str, Any] = {"path": str(data_root), "drive": drive, "exists": data_root.exists()}
    try:
        info = config.check_local_fixed(data_root, forbidden_drives)
    except config.UnsafeLocationError as exc:
        value["volume"] = config.drive_info(data_root).to_dict()
        value["writable"] = False
        return CheckResult("data_root", "fail", str(exc), value)
    value["volume"] = info.to_dict()
    warnings: list[str] = []
    writable = False
    if not data_root.exists():
        warnings.append("missing — run `mwh paths --create`")
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
    volume = f"{info.drive_type.removeprefix('DRIVE_').lower()} {info.filesystem}"
    return CheckResult(
        "data_root", "pass", f"{data_root} exists and is writable ({volume} volume)", value
    )


def check_temp_dir(settings: Settings) -> CheckResult:
    """DuckDB temp dir: same volume as the data root (else **fail**); exists or creatable."""
    temp_dir = settings.layout["tmp_duckdb"]
    value: dict[str, Any] = {
        "path": str(temp_dir),
        "explicit": settings.duckdb_temp_dir is not None,
        "exists": temp_dir.is_dir(),
        "max_temp_directory_size": settings.duckdb_max_temp_size,
    }
    try:
        config.check_same_volume(temp_dir, settings.data_root, what="duckdb_temp_dir")
    except config.UnsafeLocationError as exc:
        return CheckResult("temp_dir", "fail", str(exc), value)
    if temp_dir.is_dir():
        return CheckResult(
            "temp_dir",
            "pass",
            f"{temp_dir} exists on the data-root volume (max {settings.duckdb_max_temp_size})",
            value,
        )
    anchor = config.nearest_existing(temp_dir)
    creatable = anchor.is_dir() and os.access(anchor, os.W_OK)
    value["creatable"] = creatable
    if creatable:
        return CheckResult(
            "temp_dir",
            "pass",
            f"{temp_dir} missing but creatable — `mwh paths --create` makes it",
            value,
        )
    return CheckResult(
        "temp_dir", "warn", f"{temp_dir} missing and its nearest ancestor is not writable", value
    )


def check_cloud_mounts(
    repo: Path | None = None, forbidden_drives: Iterable[str] = DEFAULT_FORBIDDEN_DRIVES
) -> CheckResult:
    """Mounted volumes that look synced / virtual / remote — letters, labels, filesystem and
    type only, never contents (info) — and a warn-only check that the repository itself is
    not on one (D-29 applies to the repo tree too: `.venv`, caches, fixtures)."""
    if not IS_WINDOWS:
        return CheckResult("cloud_mounts", "info", "not a Windows host — volume scan skipped")
    suspicious: list[dict[str, str]] = []
    for letter in config.logical_drives():
        root = f"{letter}:\\"
        problem = config.location_problem(root)
        if problem is None:
            continue
        info = config.drive_info(root)
        suspicious.append({"letter": letter, **info.to_dict(), "reason": problem})
    value: dict[str, Any] = {"suspicious": suspicious, "repo_drive": None, "repo_problem": None}
    parts = [
        f"{s['letter']}: {s['label'] or 'no label'} ({s['filesystem']}, "
        f"{s['drive_type'].removeprefix('DRIVE_').lower()})"
        for s in suspicious
    ]
    detail = (" · ".join(parts) if parts else "no synced/virtual volume detected") + (
        " — nothing warehouse-related may live there (D-29)"
    )
    status: Status = "info"
    repo_path = repo if repo is not None else repo_root()
    if repo_path is not None:
        value["repo_drive"] = _drive_of(repo_path)
        problem = config.location_problem(repo_path, forbidden_drives)
        if problem is not None:
            value["repo_problem"] = problem
            status = "warn"
            detail += f"; repository {repo_path}: {problem}"
        else:
            detail += f"; repository on {value['repo_drive']} (fixed)"
    return CheckResult("cloud_mounts", status, detail, value)


def check_defender(data_root: Path) -> CheckResult:
    """Defender real-time exclusion for the data root (D-38, owner action)."""
    data_root = Path(data_root)
    command = f"Add-MpPreference -ExclusionPath '{data_root}'"
    if not IS_WINDOWS:
        return CheckResult("defender", "info", "not a Windows host — Defender probe skipped")
    try:
        exclusions = _defender_exclusions()
    except ProbeError as exc:
        exclusions = None
        why = str(exc)
    else:
        why = "not elevated — exclusions are not readable"
    value: dict[str, Any] = {"elevated": exclusions is not None, "excluded": None}
    if exclusions is None:
        return CheckResult(
            "defender",
            "info",
            f"{why}; owner runs elevated: {command} (D-38; recorded done at the EP-0 follow-up)",
            value,
        )
    norm = os.path.normcase(os.path.normpath(str(data_root)))
    excluded = any(
        norm == os.path.normcase(os.path.normpath(e))
        or norm.startswith(os.path.normcase(os.path.normpath(e)).rstrip("\\/") + os.sep)
        for e in exclusions
    )
    value["excluded"] = excluded
    if excluded:
        return CheckResult(
            "defender", "pass", f"{data_root} is excluded from real-time scanning", value
        )
    return CheckResult(
        "defender", "warn", f"{data_root} is not excluded — run elevated: {command} (D-38)", value
    )


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


def check_power_scheme() -> CheckResult:
    """Active power scheme + Windows 11 AC overlay (D-38 wants Best performance) — info."""
    if not IS_WINDOWS:
        return CheckResult("power_scheme", "info", "not a Windows host — powercfg skipped")
    try:
        text = _powercfg_active_scheme()
    except ProbeError as exc:
        return CheckResult("power_scheme", "info", f"powercfg unavailable ({exc})")
    m = re.search(r"([0-9a-fA-F-]{36})\s*\((.*?)\)", text)
    scheme_guid = m.group(1).lower() if m else None
    scheme_name = m.group(2).strip() if m else text
    overlay = _power_overlay_registry()
    overlay_name = POWER_OVERLAYS.get(overlay or "", "unknown overlay") if overlay else None
    value = {
        "scheme_guid": scheme_guid,
        "scheme_name": scheme_name,
        "overlay_guid": overlay,
        "overlay_name": overlay_name,
    }
    detail = f"{scheme_name}"
    if overlay:
        detail += f" · AC power mode: {overlay_name}"
        if overlay != BEST_PERFORMANCE_OVERLAY:
            detail += " (D-38 recommends Best performance while plugged in)"
    return CheckResult("power_scheme", "info", detail, value)


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


def run_checks(settings: Settings) -> list[CheckResult]:
    """Run the checks in :data:`CHECK_IDS` order for ``settings`` (EP-35 embeds the result).

    Pass an unchecked instance (``config.load_settings(checked=False)``) when the point is
    to *report* an unsafe root; the ``data_root`` / ``temp_dir`` checks fail loudly.
    """
    data_root = settings.data_root
    repo = repo_root()
    drives = [_drive_of(data_root), _drive_of(repo or Path.cwd())]
    checks: list[Callable[[], CheckResult]] = [
        check_python,
        check_uv,
        check_duckdb,
        lambda: check_settings(settings),
        lambda: check_disk_free(data_root, repo, settings.min_free_gb),
        lambda: check_data_root(data_root, settings.forbidden_drives),
        lambda: check_temp_dir(settings),
        lambda: check_cloud_mounts(repo, settings.forbidden_drives),
        lambda: check_defender(data_root),
        lambda: check_bitlocker(drives),
        check_power_scheme,
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
    """Host health: python · uv · duckdb · settings · disk_free · data_root · temp_dir ·
    cloud_mounts · defender · bitlocker · power_scheme · gpu · longpaths."""
    state: CliState = ctx.obj
    results = run_checks(state.settings)
    report = doctor_report(results)
    if json_output:
        sys.stdout.write(json.dumps(report, indent=2) + os.linesep)
    else:
        from mimicwarehouse.cli import console

        utf = (console.encoding or "").lower().replace("-", "").startswith("utf")
        console.print(render_table(results, utf=utf))
        console.print(summary_line(results), style="bold" if report["ok"] else "bold red")
    raise typer.Exit(code=0 if report["ok"] else 1)
