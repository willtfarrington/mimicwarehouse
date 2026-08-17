"""Settings, data-root layout and location-safety checks (EP-3; DESIGN §3/§6, D-29, D-38).

One answer to "where is the data root, which tier, how much memory may DuckDB use, is this
machine safe to build on":

* :class:`Settings` — pydantic-settings model. Precedence **init kwargs > environment
  (``MWH_*``) > ``.env`` > ``mwh.toml`` ``[settings]`` > defaults**. Both files are looked up
  in the workspace root (``mimicwarehouse/``, resolved from the package location, so it does
  not matter whether ``mwh`` runs from the workspace or via ``uv run --project``); relative
  paths in either file are resolved against the same root. Unknown keys are rejected
  (``extra="forbid"``) so a typo in ``.env`` fails loudly instead of silently using a default.
* :attr:`Settings.layout` — the 15 data-root directories (:data:`LAYOUT_KEYS`);
  :meth:`Settings.catalog_path`; :meth:`Settings.duckdb_settings` (the explicit DuckDB
  ``memory_limit`` / ``threads`` / ``temp_directory`` / ``max_temp_directory_size`` DESIGN §6
  demands in every process; EP-17/EP-21 apply it — nothing here opens a connection).
* Safety validators, run as model validators **and** callable on their own:
  :func:`check_local_fixed` refuses a data root that is not a local fixed NTFS/ReFS volume,
  carries a sync-client volume label, lies under OneDrive, or sits on a forbidden drive
  letter (G:/D:, D-29); the DuckDB temp dir must share the data-root volume;
  :func:`check_free_space` / :func:`require_free_space` implement the ≥ 100 GB rule (DESIGN
  §3) for ``mwh paths --create`` now and ``mwh build`` from EP-19.
* :func:`get_settings` — process-wide cached accessor (``get_settings.cache_clear()`` in
  tests; :func:`configure` installs the CLI's ``--data-root`` override).
* ``mwh paths [--create] [--json]`` (:func:`paths_command`, attached in :mod:`~mimicwarehouse.cli`).

Every Win32 call (``GetDriveTypeW``, ``GetVolumeInformationW``, ``GetLogicalDrives``,
``shutil.disk_usage``) sits behind a small module-level function so tests monkeypatch it and
the suite passes on any host; off Windows those probes report ``unknown`` and the checks that
depend on them are skipped. Nothing in this module reads a data file: ``source material/`` is
only tested for existence, and directory sizes are summed from ``os.scandir`` metadata
without ever surfacing a file name.
"""

from __future__ import annotations

import ctypes
import functools
import json
import os
import re
import shutil
import sys
import tomllib
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from pathlib import Path, PureWindowsPath
from typing import TYPE_CHECKING, Annotated, Any, Literal

import typer
from pydantic import Field, PrivateAttr, ValidationError, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    DotEnvSettingsSource,
    EnvSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)
from rich.markup import escape
from rich.table import Table

if TYPE_CHECKING:  # avoid a runtime import cycle (cli.py imports this module)
    from mimicwarehouse.cli import CliState

Tier = Literal["fixture", "demo", "dev", "full"]
DuckDBProfile = Literal["build", "app"]

IS_WINDOWS: bool = sys.platform == "win32"
GB = 2**30  # GiB, labelled "GB" to match DESIGN §2-3 / Explorer (EP-2 judgment call)

#: D-29 default. ``--data-root`` / ``MWH_DATA_ROOT`` / ``.env`` / ``mwh.toml`` override it.
DEFAULT_DATA_ROOT = Path(r"C:\mimicdata")
DEFAULT_FORBIDDEN_DRIVES: tuple[str, ...] = ("G", "D")

#: The 15 data-root directories, in creation / display order (DESIGN §3 tree).
LAYOUT_KEYS: tuple[str, ...] = (
    "lake",
    "lake_core",
    "lake_derived",
    "lake_marts",
    "lake_manifests",
    "warehouse",
    "runs",
    "runs_jobs",
    "models",
    "notes",
    "ext",
    "ext_demo",
    "studies",
    "tmp",
    "tmp_duckdb",
)

#: Written to ``<data_root>/README.txt`` by ``mwh paths --create``.
DATA_ROOT_README = (
    "This folder is managed by mimicwarehouse (`mwh paths --create`).\n"
    "It holds derived, patient-level MIMIC-IV data under the PhysioNet Credentialed Health\n"
    "Data License 1.5.0: never sync, share, copy to a cloud/virtual drive, or commit it.\n"
    "Everything here is rebuildable from `source material/` + code; see GOVERNANCE.md in the\n"
    "repository (sections 2, 3 and 11) and DESIGN.md section 3 for the layout.\n"
)

#: Win32 ``GetDriveTypeW`` return codes.
DRIVE_TYPES: dict[int, str] = {
    0: "DRIVE_UNKNOWN",
    1: "DRIVE_NO_ROOT_DIR",
    2: "DRIVE_REMOVABLE",
    3: "DRIVE_FIXED",
    4: "DRIVE_REMOTE",
    5: "DRIVE_CDROM",
    6: "DRIVE_RAMDISK",
}
DRIVE_FIXED = "DRIVE_FIXED"
UNKNOWN = "unknown"  # off Windows, or when a probe fails
SAFE_FILESYSTEMS = frozenset({"NTFS", "REFS"})
#: Volume labels of sync clients / virtual drives (case-insensitive). ``box`` is
#: word-bounded so "Toolbox" does not trip it; "Dropbox" is matched by its own alternative.
CLOUD_LABEL_RE = re.compile(r"google drive|onedrive|dropbox|\bbox\b|cryptomator|icloud", re.I)
ONEDRIVE_ENV_VARS: tuple[str, ...] = ("OneDrive", "OneDriveConsumer", "OneDriveCommercial")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ConfigError(RuntimeError):
    """Base of the configuration / safety errors (never a ``ValueError``, so pydantic lets
    them propagate unchanged out of ``Settings(...)`` instead of wrapping them)."""


class UnsafeLocationError(ConfigError):
    """The path is not on a local, fixed NTFS/ReFS volume — or is on a forbidden drive (D-29)."""


class DiskGuardError(ConfigError):
    """Free space on the volume is below ``min_free_gb`` (DESIGN §3: never below 100 GB)."""


class SettingsFileError(ConfigError):
    """``mwh.toml`` is unreadable or lacks the ``[settings]`` table."""


# ---------------------------------------------------------------------------
# Roots
# ---------------------------------------------------------------------------


def workspace_root() -> Path:
    """The uv workspace (``mimicwarehouse/``): where ``.env`` and ``mwh.toml`` live.

    Resolved from the package location (editable ``src`` layout:
    ``<workspace>/src/mimicwarehouse/config.py``); if that layout does not hold (a wheel
    install) fall back to the current directory. Tests monkeypatch this function.
    """
    candidate = Path(__file__).resolve().parents[2]
    if (candidate / "pyproject.toml").is_file():
        return candidate
    return Path.cwd()


def repo_root() -> Path | None:
    """The git checkout containing this package, or None (a wheel install)."""
    for parent in Path(__file__).resolve().parents:
        if (parent / ".git").exists():
            return parent
    return None


def _default_source_root() -> Path:
    root = repo_root()
    base = root if root is not None else workspace_root().parent
    return base / "source material"


def _abspath(value: Path | str) -> Path:
    """Absolute, normalised path; relative values are anchored at the workspace root."""
    p = Path(value).expanduser()
    if not p.is_absolute():
        p = workspace_root() / p
    return Path(os.path.normpath(p))


# ---------------------------------------------------------------------------
# Win32 probes — the only places that touch the outside world (tests monkeypatch these)
# ---------------------------------------------------------------------------


def volume_root(path: Path | str) -> str:
    """Volume root: ``C:\\`` or ``\\\\server\\share\\`` on Windows, the anchor (``/``) elsewhere."""
    if IS_WINDOWS:
        drive = PureWindowsPath(path).drive
        return f"{drive}\\" if drive else str(Path(path).anchor or Path(path))
    return Path(path).anchor or "/"


def drive_letter(path: Path | str) -> str:
    """``'C'`` for a drive-letter path on Windows; ``''`` for UNC paths and off Windows."""
    drive = PureWindowsPath(path).drive if IS_WINDOWS else ""
    return drive[0].upper() if len(drive) == 2 and drive[1] == ":" else ""


def _win_kernel32() -> Any:  # pragma: no cover - exercised only on Windows hosts
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    from ctypes import wintypes

    k32.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
    k32.GetDriveTypeW.restype = wintypes.UINT
    k32.GetLogicalDrives.restype = wintypes.DWORD
    k32.GetVolumeInformationW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPWSTR,
        wintypes.DWORD,
    ]
    k32.GetVolumeInformationW.restype = wintypes.BOOL
    k32.SetThreadErrorMode.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    k32.SetThreadErrorMode.restype = wintypes.BOOL
    return k32


def _win_drive_type(root: str) -> int:  # pragma: no cover - Windows only
    return int(_win_kernel32().GetDriveTypeW(root))


def _win_volume_information(root: str) -> tuple[str, str] | None:  # pragma: no cover
    """``(label, filesystem)`` of a volume root, or None; never pops a "no disk" dialog."""
    from ctypes import wintypes

    k32 = _win_kernel32()
    label = ctypes.create_unicode_buffer(261)
    fs = ctypes.create_unicode_buffer(261)
    serial = wintypes.DWORD()
    max_len = wintypes.DWORD()
    flags = wintypes.DWORD()
    old_mode = wintypes.DWORD()
    k32.SetThreadErrorMode(1, ctypes.byref(old_mode))  # SEM_FAILCRITICALERRORS
    try:
        ok = k32.GetVolumeInformationW(
            root,
            label,
            261,
            ctypes.byref(serial),
            ctypes.byref(max_len),
            ctypes.byref(flags),
            fs,
            261,
        )
    finally:
        k32.SetThreadErrorMode(old_mode.value, None)
    if not ok:
        return None
    return label.value, fs.value


def _win_logical_drives() -> list[str]:  # pragma: no cover - Windows only
    mask = int(_win_kernel32().GetLogicalDrives())
    return [chr(65 + i) for i in range(26) if mask & (1 << i)]


@dataclass(frozen=True, slots=True)
class DriveInfo:
    """Metadata of the volume holding a path — labels and types only, never contents."""

    letter: str  # "C" (no colon) or "" for UNC / non-Windows
    drive_type: str  # DRIVE_FIXED, DRIVE_REMOTE, … or "unknown"
    label: str
    filesystem: str  # NTFS, ReFS, FAT32, cryptoFs, … or "unknown"

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def drive_info(path: Path | str) -> DriveInfo:
    """Drive type / volume label / filesystem of the volume holding ``path`` (ctypes).

    Non-Windows hosts (and failed probes) return ``unknown`` for type and filesystem, and
    :func:`location_problem` then skips the criteria it cannot evaluate. Volume information
    is only requested for fixed / remote / RAM-disk volumes so an empty card reader or DVD
    drive never triggers a "no disk" prompt.
    """
    letter = drive_letter(path)
    if not IS_WINDOWS:
        return DriveInfo(letter, UNKNOWN, "", UNKNOWN)
    root = volume_root(path)
    try:
        code = _win_drive_type(root)
    except OSError:  # pragma: no cover - defensive
        return DriveInfo(letter, UNKNOWN, "", UNKNOWN)
    dtype = DRIVE_TYPES.get(code, f"DRIVE_{code}")
    label, filesystem = "", UNKNOWN
    if code in (3, 4, 6):
        try:
            info = _win_volume_information(root)
        except OSError:  # pragma: no cover - defensive
            info = None
        if info is not None:
            label, filesystem = info
    return DriveInfo(letter, dtype, label, filesystem or UNKNOWN)


def logical_drives() -> list[str]:
    """Mounted drive letters on Windows (``["C", "D", "G"]``); ``[]`` elsewhere."""
    if not IS_WINDOWS:
        return []
    try:
        return _win_logical_drives()
    except OSError:  # pragma: no cover - defensive
        return []


def onedrive_roots() -> list[Path]:
    """The OneDrive folders announced by ``%OneDrive%`` & co (deduplicated)."""
    roots: list[Path] = []
    for var in ONEDRIVE_ENV_VARS:
        value = os.environ.get(var, "").strip()
        if value:
            p = Path(value)
            if p not in roots:
                roots.append(p)
    return roots


def nearest_existing(path: Path | str) -> Path:
    """``path`` or its closest existing ancestor (for disk_usage / writability of new roots)."""
    p = Path(path)
    for candidate in (p, *p.parents):
        if candidate.exists():
            return candidate
    return p


def disk_usage(path: Path | str) -> shutil._ntuple_diskusage:
    """``shutil.disk_usage`` of the volume holding ``path`` (its nearest existing ancestor)."""
    return shutil.disk_usage(str(nearest_existing(path)))


def volume_of(path: Path | str) -> str:
    """A key that is equal for two paths iff they share a volume (drive root / ``st_dev``)."""
    if IS_WINDOWS:
        return volume_root(path).upper()
    return str(os.stat(nearest_existing(path)).st_dev)


# ---------------------------------------------------------------------------
# Safety checks — callable on their own; Settings runs them as model validators
# ---------------------------------------------------------------------------


def _normalise_drives(drives: Iterable[str]) -> frozenset[str]:
    return frozenset(d.strip().rstrip(":\\/").upper() for d in drives if d and d.strip())


def location_problem(path: Path | str, forbidden_drives: Iterable[str] = ()) -> str | None:
    """Why ``path`` is unfit for warehouse data (D-29), or None when it looks fine.

    Reasons, most informative first: sync-client volume label · not ``DRIVE_FIXED`` ·
    filesystem outside NTFS/ReFS · under OneDrive · drive letter in ``forbidden_drives``.
    Criteria that cannot be evaluated off Windows (``unknown`` type/filesystem) are skipped.
    """
    p = Path(path)
    info = drive_info(p)
    root = volume_root(p)
    if CLOUD_LABEL_RE.search(info.label):
        return f"volume {root} is labelled {info.label!r} — a sync client / virtual drive"
    if info.drive_type not in (DRIVE_FIXED, UNKNOWN):
        return f"volume {root} is {info.drive_type}, not a local fixed disk"
    if info.filesystem != UNKNOWN and info.filesystem.upper() not in SAFE_FILESYSTEMS:
        return (
            f"volume {root} is {info.filesystem}, not NTFS/ReFS "
            "(virtual and sync-client volumes show up as FAT32 / cryptoFs / …)"
        )
    resolved = Path(os.path.normpath(_abspath(p)))
    for od in onedrive_roots():
        try:
            if resolved.resolve().is_relative_to(od.resolve()):
                return f"lies under OneDrive ({od})"
        except OSError:  # pragma: no cover - unreadable OneDrive path
            continue
    forbidden = _normalise_drives(forbidden_drives)
    if info.letter and info.letter in forbidden:
        return f"drive {info.letter}: is on the forbidden list {sorted(forbidden)}"
    return None


def check_local_fixed(
    path: Path | str, forbidden_drives: Iterable[str] = DEFAULT_FORBIDDEN_DRIVES
) -> DriveInfo:
    """Raise :class:`UnsafeLocationError` unless ``path`` may hold warehouse data (D-29)."""
    problem = location_problem(path, forbidden_drives)
    if problem is not None:
        raise UnsafeLocationError(
            f"{path}: {problem} — derived data must live on a local, fixed NTFS/ReFS volume, "
            "never a synced, virtual or network drive (D-29, GOVERNANCE §2)"
        )
    return drive_info(path)


def check_same_volume(path: Path | str, data_root: Path | str, *, what: str = "path") -> None:
    """Raise :class:`UnsafeLocationError` unless ``path`` shares the data root's volume."""
    if volume_of(path) != volume_of(data_root):
        raise UnsafeLocationError(
            f"{what} {path} is not on the data-root volume ({volume_root(data_root)}) — "
            "DuckDB spill / temp files must stay beside the lake (DESIGN §6, D-29)"
        )


@dataclass(frozen=True, slots=True)
class FreeSpace:
    """Free / total GB of a volume and whether it clears the guard."""

    free_gb: float
    total_gb: float
    ok: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def check_free_space(path: Path | str, min_gb: float) -> FreeSpace:
    usage = disk_usage(path)
    free_gb = round(usage.free / GB, 1)
    total_gb = round(usage.total / GB, 1)
    return FreeSpace(free_gb=free_gb, total_gb=total_gb, ok=free_gb >= min_gb)


def require_free_space(path: Path | str, min_gb: float) -> FreeSpace:
    """Raise :class:`DiskGuardError` when the volume holding ``path`` has < ``min_gb`` free."""
    space = check_free_space(path, min_gb)
    if not space.ok:
        raise DiskGuardError(
            f"{volume_root(path)} has {space.free_gb:g} GB free of {space.total_gb:g} GB; "
            f"the warehouse refuses to write below {min_gb:g} GB (DESIGN §3) — free space "
            "or lower MWH_MIN_FREE_GB deliberately"
        )
    return space


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

_SAFETY_ENFORCED: ContextVar[bool] = ContextVar("mwh_safety_enforced", default=True)


@contextmanager
def safety_checks_disabled() -> Iterator[None]:
    """Construct :class:`Settings` without the location refusals (doctor / paths *report*
    the problem instead of crashing on it). Never used by code that writes data."""
    token = _SAFETY_ENFORCED.set(False)
    try:
        yield
    finally:
        _SAFETY_ENFORCED.reset(token)


def _toml_source(settings_cls: type[BaseSettings], path: Path) -> PydanticBaseSettingsSource:
    if not path.is_file():
        return TomlConfigSettingsSource(settings_cls, toml_file=None)
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise SettingsFileError(f"{path}: cannot read ({exc})") from exc
    if "settings" not in data:
        raise SettingsFileError(
            f"{path}: expected a [settings] table (keys are the MWH_* names without the prefix)"
        )
    return TomlConfigSettingsSource(settings_cls, toml_file=path, toml_table_header=("settings",))


def named_sources(
    settings_cls: type[BaseSettings], env_settings: PydanticBaseSettingsSource | None = None
) -> dict[str, PydanticBaseSettingsSource]:
    """The file/environment sources in precedence order (after init kwargs), by name."""
    root = workspace_root()
    return {
        "env": env_settings if env_settings is not None else EnvSettingsSource(settings_cls),
        ".env": DotEnvSettingsSource(settings_cls, env_file=root / ".env"),
        "mwh.toml": _toml_source(settings_cls, root / "mwh.toml"),
    }


class Settings(BaseSettings):
    """mimicwarehouse settings (``MWH_`` env · ``.env`` · ``mwh.toml`` ``[settings]``)."""

    model_config = SettingsConfigDict(
        env_prefix="MWH_",
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,  # `MWH_DUCKDB_TEMP_DIR=` means "default", not Path("")
        toml_file="mwh.toml",
        extra="forbid",
        validate_default=True,
    )

    data_root: Path = Field(
        default=DEFAULT_DATA_ROOT,
        description="Data root outside the repository; local fixed NTFS only (D-29).",
    )
    source_root: Path = Field(
        default_factory=_default_source_root,
        description="Raw PhysioNet CSVs (`<repo>/source material`); existence-tested only.",
    )
    default_tier: Tier = "dev"
    duckdb_memory_limit: str = "36GB"
    duckdb_app_memory_limit: str = "12GB"
    duckdb_threads: int = Field(default=12, ge=1)
    duckdb_temp_dir: Path | None = Field(
        default=None, description="Defaults to <data_root>/tmp/duckdb; same volume as data_root."
    )
    duckdb_max_temp_size: str = "150GB"
    min_free_gb: int = Field(default=100, ge=0)
    k_suppression: int = Field(default=11, ge=1)
    allow_remote: bool = False
    forbidden_drives: list[str] = Field(default_factory=lambda: list(DEFAULT_FORBIDDEN_DRIVES))
    dev_buckets: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4])

    _init_fields: frozenset[str] = PrivateAttr(default=frozenset())

    def __init__(self, **values: Any) -> None:
        super().__init__(**values)
        self._init_fields = frozenset(k for k in values if not k.startswith("_"))

    # -- sources ---------------------------------------------------------------------------

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # init kwargs > env > .env > mwh.toml > defaults (secrets dir unused, parked)
        return (init_settings, *named_sources(settings_cls, env_settings).values())

    def source_of(self, field: str) -> str:
        """Which source supplied ``field``: init · env · .env · mwh.toml · default."""
        if field not in type(self).model_fields:
            raise KeyError(field)
        if field in self._init_fields:
            return "init"
        for name, source in named_sources(type(self)).items():
            if field in source():
                return name
        return "default"

    def sources(self) -> dict[str, list[str]]:
        """Fields supplied by each non-default source (empty lists for unused sources)."""
        found: dict[str, list[str]] = {"init": sorted(self._init_fields)}
        for name in named_sources(type(self)):
            found[name] = []
        for field in type(self).model_fields:
            src = self.source_of(field)
            if src not in ("init", "default"):
                found[src].append(field)
        return found

    # -- validators ------------------------------------------------------------------------

    @field_validator("data_root", "source_root", "duckdb_temp_dir", mode="after")
    @classmethod
    def _absolute(cls, value: Path | None) -> Path | None:
        return None if value is None else _abspath(value)

    @field_validator("forbidden_drives", mode="after")
    @classmethod
    def _drive_letters(cls, value: list[str]) -> list[str]:
        return sorted(_normalise_drives(value))

    @field_validator("dev_buckets", mode="after")
    @classmethod
    def _buckets(cls, value: list[int]) -> list[int]:
        if any(b < 0 or b > 99 for b in value):
            raise ValueError("dev_buckets must be subject buckets in 0..99 (subject_id % 100)")
        return sorted(set(value))

    @model_validator(mode="after")
    def _enforce_safety(self) -> Settings:
        if _SAFETY_ENFORCED.get():
            self.require_safe()
        return self

    def require_safe(self) -> None:
        """The location refusals (raise :class:`UnsafeLocationError`); D-29 / DESIGN §6."""
        check_local_fixed(self.data_root, self.forbidden_drives)
        check_same_volume(self.layout["tmp_duckdb"], self.data_root, what="duckdb_temp_dir")

    # -- helpers ---------------------------------------------------------------------------

    @property
    def layout(self) -> dict[str, Path]:
        """The 15 data-root directories keyed by :data:`LAYOUT_KEYS` (DESIGN §3)."""
        r = self.data_root
        lake, runs, ext, tmp = r / "lake", r / "runs", r / "ext", r / "tmp"
        return {
            "lake": lake,
            "lake_core": lake / "core",
            "lake_derived": lake / "derived",
            "lake_marts": lake / "marts",
            "lake_manifests": lake / "manifests",
            "warehouse": r / "warehouse",
            "runs": runs,
            "runs_jobs": runs / "jobs",
            "models": r / "models",
            "notes": r / "notes",
            "ext": ext,
            "ext_demo": ext / "demo",
            "studies": r / "studies",
            "tmp": tmp,
            "tmp_duckdb": self.duckdb_temp_dir or tmp / "duckdb",
        }

    def catalog_path(self, tier: Tier | str) -> Path:
        """``<data_root>/warehouse/<tier>.duckdb`` (EP-21 builds it; opened READ_ONLY elsewhere)."""
        return self.layout["warehouse"] / f"{tier}.duckdb"

    def duckdb_settings(self, profile: DuckDBProfile = "build") -> dict[str, str]:
        """Explicit DuckDB configuration for a process (DESIGN §6). String values, ready for
        ``duckdb.connect(config=...)`` or ``SET`` statements; ``build`` adds
        ``preserve_insertion_order = false`` for bulk loads."""
        if profile not in ("build", "app"):
            raise ValueError(f"unknown DuckDB profile {profile!r}; expected 'build' or 'app'")
        settings = {
            "memory_limit": (
                self.duckdb_memory_limit if profile == "build" else self.duckdb_app_memory_limit
            ),
            "threads": str(self.duckdb_threads),
            "temp_directory": str(self.layout["tmp_duckdb"]),
            "max_temp_directory_size": self.duckdb_max_temp_size,
        }
        if profile == "build":
            settings["preserve_insertion_order"] = "false"
        return settings


# ---------------------------------------------------------------------------
# Process-wide accessor
# ---------------------------------------------------------------------------

_overrides: dict[str, Any] = {}


@functools.cache
def get_settings() -> Settings:
    """The process-wide, validated :class:`Settings` (built once; refuses unsafe roots).

    Tests call ``get_settings.cache_clear()`` (or :func:`configure` with no arguments) after
    monkeypatching the environment.
    """
    return Settings(**_overrides)


def configure(**overrides: Any) -> None:
    """Install init-kwarg overrides for :func:`get_settings` (the CLI's ``--data-root``) and
    drop the cached instance; ``configure()`` with no arguments resets to the environment."""
    _overrides.clear()
    _overrides.update(overrides)
    get_settings.cache_clear()


def load_settings(*, checked: bool = True, **overrides: Any) -> Settings:
    """A fresh (uncached) :class:`Settings`. ``checked=False`` skips the location refusals so a
    diagnostic command can *report* them; anything that writes data uses the default."""
    if checked:
        return Settings(**overrides)
    with safety_checks_disabled():
        return Settings(**overrides)


# ---------------------------------------------------------------------------
# Layout creation & report (mwh paths)
# ---------------------------------------------------------------------------


def dir_size_bytes(path: Path) -> int:
    """Total bytes of regular files under ``path`` from ``os.scandir`` metadata — no names
    are collected or returned; symlinks / junctions are not followed."""
    total = 0
    stack = [Path(path)]
    while stack:
        current = stack.pop()
        try:
            it = os.scandir(current)
        except OSError:
            continue
        with it:
            for entry in it:
                try:
                    if entry.is_symlink() or entry.is_junction():
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(Path(entry.path))
                    elif entry.is_file(follow_symlinks=False):
                        total += entry.stat(follow_symlinks=False).st_size
                except OSError:
                    continue
    return total


def layout_rows(settings: Settings) -> list[dict[str, Any]]:
    """One row per layout key: ``{key, path, exists, mb_used}`` (directory totals only)."""
    rows: list[dict[str, Any]] = []
    for key, path in settings.layout.items():
        exists = path.is_dir()
        mb = round(dir_size_bytes(path) / 2**20, 1) if exists else None
        rows.append({"key": key, "path": str(path), "exists": exists, "mb_used": mb})
    return rows


def create_layout(settings: Settings) -> list[Path]:
    """Create the 15 directories (idempotent) and ``README.txt``; returns what was new.

    Callers run :meth:`Settings.require_safe` and :func:`require_free_space` **first** —
    this function only creates.
    """
    created: list[Path] = []
    for path in settings.layout.values():
        existed = path.is_dir()
        path.mkdir(parents=True, exist_ok=True)
        if not existed:
            created.append(path)
    readme = settings.data_root / "README.txt"
    if not readme.is_file() or readme.read_text(encoding="utf-8") != DATA_ROOT_README:
        readme.write_text(DATA_ROOT_README, encoding="utf-8", newline="\n")
        created.append(readme)
    return created


def paths_report(
    settings: Settings, *, unsafe: str | None, created: list[Path] | None
) -> dict[str, Any]:
    """The JSON object ``mwh paths --json`` prints."""
    space = check_free_space(settings.data_root, settings.min_free_gb)
    return {
        "data_root": str(settings.data_root),
        "data_root_source": settings.source_of("data_root"),
        "sources": settings.sources(),
        "workspace": str(workspace_root()),
        "free_space": {**space.to_dict(), "min_gb": settings.min_free_gb},
        "unsafe": unsafe,
        "created": None if created is None else [str(p) for p in created],
        "layout": layout_rows(settings),
    }


def render_paths_table(rows: list[dict[str, Any]], *, title: str) -> Table:
    table = Table(title=title, show_lines=False, pad_edge=False)
    table.add_column("key", style="bold")
    table.add_column("path", overflow="fold")
    table.add_column("exists", justify="center")
    table.add_column("MB used", justify="right")
    for row in rows:
        exists = "[green]yes[/]" if row["exists"] else "[yellow]no[/]"
        mb = "" if row["mb_used"] is None else f"{row['mb_used']:,.1f}"
        table.add_row(row["key"], escape(row["path"]), exists, mb)
    return table


def paths_command(
    ctx: typer.Context,
    create: Annotated[
        bool,
        typer.Option(
            "--create",
            help="Run the safety validators and the free-space guard, then create the layout.",
        ),
    ] = False,
    json_output: Annotated[
        bool, typer.Option("--json", help="Print the layout report as JSON.")
    ] = False,
) -> None:
    """Data-root layout (15 keys): path · exists · MB used; --create makes it after the checks."""
    state: CliState = ctx.obj
    settings = state.settings
    from mimicwarehouse.cli import console

    unsafe: str | None = None
    try:
        settings.require_safe()
    except UnsafeLocationError as exc:
        unsafe = str(exc)

    created: list[Path] | None = None
    if create:
        if unsafe is not None:
            console.print(f"[bold red]refused:[/] {escape(unsafe)}", highlight=False)
            console.print("nothing was created.")
            raise typer.Exit(code=2)
        try:
            require_free_space(settings.data_root, settings.min_free_gb)
        except DiskGuardError as exc:
            console.print(f"[bold red]refused:[/] {escape(str(exc))}", highlight=False)
            console.print("nothing was created.")
            raise typer.Exit(code=2) from None
        created = create_layout(settings)

    report = paths_report(settings, unsafe=unsafe, created=created)
    if json_output:
        sys.stdout.write(json.dumps(report, indent=2) + os.linesep)
    else:
        source = report["data_root_source"]
        console.print(
            f"data_root {escape(report['data_root'])}  [dim](from {source})[/]", highlight=False
        )
        console.print(render_paths_table(report["layout"], title="mwh paths"))
        fs = report["free_space"]
        console.print(
            f"free space {volume_root(settings.data_root)} "
            f"{fs['free_gb']:g} / {fs['total_gb']:g} GB"
            f" (guard >= {fs['min_gb']} GB: {'ok' if fs['ok'] else 'BELOW'})",
            highlight=False,
        )
        if created is not None:
            console.print(
                f"created {len(created)} new path(s)" if created else "already complete — no change"
            )
        if unsafe is not None:
            console.print(f"[bold red]unsafe data root:[/] {escape(unsafe)}", highlight=False)
    raise typer.Exit(code=2 if unsafe is not None else 0)


__all__ = [
    "DATA_ROOT_README",
    "DEFAULT_DATA_ROOT",
    "DEFAULT_FORBIDDEN_DRIVES",
    "LAYOUT_KEYS",
    "ConfigError",
    "DiskGuardError",
    "DriveInfo",
    "FreeSpace",
    "Settings",
    "SettingsFileError",
    "UnsafeLocationError",
    "ValidationError",
    "check_free_space",
    "check_local_fixed",
    "check_same_volume",
    "configure",
    "create_layout",
    "drive_info",
    "get_settings",
    "load_settings",
    "location_problem",
    "logical_drives",
    "paths_command",
    "repo_root",
    "require_free_space",
    "safety_checks_disabled",
    "volume_of",
    "workspace_root",
]
