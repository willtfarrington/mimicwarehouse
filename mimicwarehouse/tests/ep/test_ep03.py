"""EP-3 — Config & data root + safety checks: acceptance tests.

Everything runs under ``tmp_path``: the workspace root (where ``.env`` / ``mwh.toml`` are
looked up) is monkeypatched to a temp dir, ``MWH_*`` variables are cleared, and every Win32
probe (``drive_info``, ``logical_drives``, ``disk_usage``, ``volume_of``) is monkeypatched so the
suite passes on any host and never touches G:/D: or ``C:\\mimicdata``. Doctor's subprocess
probes are mocked as in EP-2. Only synthetic values appear here — no data, no identifiers.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections import namedtuple
from collections.abc import Iterator
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from mimicwarehouse import config, doctor
from mimicwarehouse.cli import app
from mimicwarehouse.config import (
    DATA_ROOT_README,
    LAYOUT_KEYS,
    DiskGuardError,
    DriveInfo,
    Settings,
    UnsafeLocationError,
)

pytestmark = pytest.mark.ep_3

runner = CliRunner()
WORKSPACE = Path(__file__).resolve().parents[2]  # mimicwarehouse/ (the uv project)
REPO = WORKSPACE.parent

DiskUsage = namedtuple("DiskUsage", "total used free")
FIXED_NTFS = DriveInfo(letter="C", drive_type="DRIVE_FIXED", label="Windows", filesystem="NTFS")


def _fake_disk_usage(free_gb: float, total_gb: float = 950.0):
    def fake(path):
        total = int(total_gb * config.GB)
        free = int(free_gb * config.GB)
        return DiskUsage(total=total, used=total - free, free=free)

    return fake


def _fake_run(argv, **kwargs):
    """`subprocess.run` stand-in for the doctor probes; unknown tools are a test failure."""
    tool = Path(argv[0]).name.lower().removesuffix(".exe")
    assert kwargs.get("timeout") == doctor.SUBPROCESS_TIMEOUT_S
    if tool == "powershell":
        script = argv[-1]
        if "Get-MpPreference" in script:
            out = "N/A: Must be an administrator to view exclusions\n"
        elif "SecurityCenter2" in script:  # EP-164 antivirus probe: Defender-only host
            out = (
                '{"displayName":"Windows Defender","productState":397568,'
                '"pathToSignedProductExe":"windowsdefender://"}\n'
            )
        else:
            out = "1\n"
    else:
        out = {
            "uv": "uv 0.0.0-test\n",
            "nvidia-smi": "Synthetic GPU 0, 8192 MiB, 999.99\n",
            "git": "true\n",
            "powercfg": "Power Scheme GUID: 381b4222-f694-41f0-9685-ff5bb260df2e  (Balanced)\n",
        }[tool]
    return subprocess.CompletedProcess(argv, 0, stdout=out, stderr="")


@pytest.fixture
def workspace(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    """Isolated settings environment: temp workspace, no MWH_* vars, healthy fake volume."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    for key in list(os.environ):
        if key.upper().startswith("MWH_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(config, "workspace_root", lambda: ws)
    monkeypatch.setattr(config, "drive_info", lambda path: FIXED_NTFS)
    monkeypatch.setattr(config, "logical_drives", lambda: ["C"])
    monkeypatch.setattr(config, "volume_of", lambda path: "VOL")
    monkeypatch.setattr(config, "onedrive_roots", lambda: [])
    monkeypatch.setattr(config.shutil, "disk_usage", _fake_disk_usage(500.0))
    config.configure()  # clears overrides + get_settings cache
    yield ws
    config.configure()


@pytest.fixture
def data_root(workspace: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    root = tmp_path / "mimicdata"
    monkeypatch.setenv("MWH_DATA_ROOT", str(root))
    config.get_settings.cache_clear()
    return root


@pytest.fixture
def mocked_doctor(monkeypatch: pytest.MonkeyPatch, data_root: Path) -> Path:
    monkeypatch.setattr(doctor, "IS_WINDOWS", True)
    monkeypatch.setattr(doctor.subprocess, "run", _fake_run)
    monkeypatch.setattr(doctor, "_longpaths_registry", lambda: 1)
    monkeypatch.setattr(doctor, "_power_overlay_registry", lambda: doctor.BEST_PERFORMANCE_OVERLAY)
    monkeypatch.setattr(doctor, "_drive_of", lambda p: "C:")
    monkeypatch.setattr(doctor, "_mount_of", lambda p: str(p))
    monkeypatch.setattr(doctor, "repo_root", lambda: data_root.parent)
    data_root.mkdir()
    return data_root


def _tree(root: Path) -> dict[str, float]:
    """Relative path → mtime for every entry under root (to prove a re-run changes nothing)."""
    return {
        str(p.relative_to(root)): p.stat().st_mtime for p in sorted(root.rglob("*")) if p != root
    }


# ---------------------------------------------------------------------------
# Settings: precedence, layout, DuckDB config, provenance
# ---------------------------------------------------------------------------


def test_precedence_kwargs_env_dotenv_toml_default(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    toml_root, dotenv_root, env_root, kw_root = (
        (tmp_path / name).as_posix() for name in ("toml", "dotenv", "env", "kw")
    )
    # defaults
    s = Settings()
    assert s.duckdb_threads == 12 and s.source_of("data_root") == "default"
    # mwh.toml > default
    (workspace / "mwh.toml").write_text(
        f'[settings]\ndata_root = "{toml_root}"\nduckdb_threads = 4\nk_suppression = 11\n',
        encoding="utf-8",
    )
    s = Settings()
    assert s.data_root == Path(toml_root) and s.duckdb_threads == 4
    assert s.source_of("data_root") == "mwh.toml" and s.source_of("min_free_gb") == "default"
    # .env > mwh.toml
    (workspace / ".env").write_text(f"MWH_DATA_ROOT={dotenv_root}\n", encoding="utf-8")
    s = Settings()
    assert s.data_root == Path(dotenv_root) and s.duckdb_threads == 4  # toml still fills gaps
    assert s.source_of("data_root") == ".env" and s.source_of("duckdb_threads") == "mwh.toml"
    # env > .env
    monkeypatch.setenv("MWH_DATA_ROOT", env_root)
    s = Settings()
    assert s.data_root == Path(env_root) and s.source_of("data_root") == "env"
    # kwargs > env
    s = Settings(data_root=kw_root)
    assert s.data_root == Path(kw_root) and s.source_of("data_root") == "init"
    assert s.sources() == {
        "init": ["data_root"],
        "env": [],
        ".env": [],
        "mwh.toml": ["duckdb_threads", "k_suppression"],
    }


def test_unknown_keys_and_bad_toml_are_rejected(workspace: Path) -> None:
    (workspace / ".env").write_text("MWH_DATA_ROOTT=C:/typo\n", encoding="utf-8")
    with pytest.raises(config.ValidationError):
        Settings()
    (workspace / ".env").unlink()
    (workspace / "mwh.toml").write_text('data_root = "C:/x"\n', encoding="utf-8")  # no [settings]
    with pytest.raises(config.SettingsFileError):
        Settings()


def test_relative_paths_anchor_at_workspace_and_empty_env_means_default(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (workspace / "mwh.toml").write_text(
        '[settings]\ndata_root = "data"\nsource_root = "../source material"\n', encoding="utf-8"
    )
    monkeypatch.setenv("MWH_DUCKDB_TEMP_DIR", "")
    s = Settings()
    assert s.data_root == workspace / "data"
    assert s.source_root == workspace.parent / "source material"
    assert s.duckdb_temp_dir is None


def test_layout_has_exactly_the_15_keys_under_the_root(data_root: Path) -> None:
    s = config.get_settings()
    assert s.data_root == data_root
    layout = s.layout
    assert tuple(layout) == LAYOUT_KEYS and len(layout) == 15
    for key, path in layout.items():
        assert path.is_relative_to(data_root), (key, path)
    assert layout["lake_core"] == data_root / "lake" / "core"
    assert layout["tmp_duckdb"] == data_root / "tmp" / "duckdb"
    assert layout["ext_demo"] == data_root / "ext" / "demo"
    assert layout["runs_jobs"] == data_root / "runs" / "jobs"
    assert s.catalog_path("dev") == data_root / "warehouse" / "dev.duckdb"
    assert str(s.catalog_path("dev")).replace("/", "\\").endswith("warehouse\\dev.duckdb")


def test_duckdb_settings_profiles(data_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MWH_DUCKDB_MEMORY_LIMIT", "20GB")
    monkeypatch.setenv("MWH_DUCKDB_APP_MEMORY_LIMIT", "6GB")
    monkeypatch.setenv("MWH_DUCKDB_THREADS", "7")
    monkeypatch.setenv("MWH_DUCKDB_MAX_TEMP_SIZE", "99GB")
    s = config.load_settings()
    build = s.duckdb_settings("build")
    assert build == {
        "memory_limit": "20GB",
        "threads": "7",
        "temp_directory": str(data_root / "tmp" / "duckdb"),
        "max_temp_directory_size": "99GB",
        "preserve_insertion_order": "false",
    }
    app_profile = s.duckdb_settings("app")
    assert app_profile["memory_limit"] == "6GB" and "preserve_insertion_order" not in app_profile
    assert all(isinstance(v, str) for v in build.values())
    with pytest.raises(ValueError):
        s.duckdb_settings("nope")  # type: ignore[arg-type]


def test_get_settings_is_cached_and_configure_overrides(data_root: Path, tmp_path: Path) -> None:
    assert config.get_settings() is config.get_settings()
    other = tmp_path / "other"
    config.configure(data_root=other)
    s = config.get_settings()
    assert s.data_root == other and s.source_of("data_root") == "init"
    config.configure()
    assert config.get_settings().data_root == data_root


# ---------------------------------------------------------------------------
# Refusals (D-29) — each raises UnsafeLocationError naming D-29
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("info", "needle"),
    [
        (DriveInfo("C", "DRIVE_FIXED", "Google Drive", "NTFS"), "Google Drive"),
        (DriveInfo("C", "DRIVE_FIXED", "My OneDrive", "NTFS"), "OneDrive"),
        (DriveInfo("C", "DRIVE_FIXED", "Google Cryptomator", "cryptoFs"), "Cryptomator"),
        (DriveInfo("C", "DRIVE_REMOTE", "", "NTFS"), "DRIVE_REMOTE"),
        (DriveInfo("C", "DRIVE_REMOVABLE", "", "NTFS"), "DRIVE_REMOVABLE"),
        (DriveInfo("C", "DRIVE_FIXED", "", "FAT32"), "FAT32"),
        (DriveInfo("C", "DRIVE_FIXED", "", "exFAT"), "exFAT"),
    ],
)
def test_check_local_fixed_refuses_cloud_remote_and_fat32(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, info: DriveInfo, needle: str
) -> None:
    monkeypatch.setattr(config, "drive_info", lambda path: info)
    with pytest.raises(UnsafeLocationError) as excinfo:
        config.check_local_fixed(tmp_path / "root")
    assert "D-29" in str(excinfo.value) and needle in str(excinfo.value)
    with pytest.raises(UnsafeLocationError):
        Settings(data_root=tmp_path / "root")  # the model validator refuses too


def test_check_local_fixed_accepts_fixed_ntfs_and_refs(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for fs in ("NTFS", "ReFS"):
        monkeypatch.setattr(
            config, "drive_info", lambda path, fs=fs: DriveInfo("C", "DRIVE_FIXED", "Windows", fs)
        )
        assert config.check_local_fixed(tmp_path / "root").filesystem == fs
        assert config.location_problem(tmp_path / "root") is None
    # unknown (non-Windows) type/filesystem are skipped, not refused
    monkeypatch.setattr(config, "drive_info", lambda path: DriveInfo("", "unknown", "", "unknown"))
    assert config.location_problem(tmp_path / "root") is None


def test_forbidden_drive_letter_refuses_g_and_d(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`G:\\mimicdata` is refused by the letter alone, even on a healthy-looking volume."""
    monkeypatch.setattr(
        config,
        "drive_info",
        lambda path: DriveInfo(config.drive_letter(path) or "G", "DRIVE_FIXED", "", "NTFS"),
    )
    with pytest.raises(UnsafeLocationError) as excinfo:
        Settings(data_root=r"G:\mimicdata")
    assert "forbidden" in str(excinfo.value) and "D-29" in str(excinfo.value)
    with pytest.raises(UnsafeLocationError):
        config.check_local_fixed(r"D:\mimicdata", ["G", "D"])
    # letters are normalised: "g:", "G:\\" and "G" all mean G
    with pytest.raises(UnsafeLocationError):
        config.check_local_fixed(r"G:\mimicdata", ["g:"])
    # not forbidden → fine
    config.check_local_fixed(r"G:\mimicdata", forbidden_drives=[])


def test_onedrive_path_refused(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    onedrive = tmp_path / "OneDrive"
    onedrive.mkdir()
    monkeypatch.setattr(config, "onedrive_roots", lambda: [onedrive])
    with pytest.raises(UnsafeLocationError, match="OneDrive"):
        config.check_local_fixed(onedrive / "mimicdata")
    config.check_local_fixed(tmp_path / "elsewhere")


def test_temp_dir_on_another_volume_refused(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "root"
    monkeypatch.setattr(config, "volume_of", lambda p: "OTHER" if "elsewhere" in str(p) else "ROOT")
    with pytest.raises(UnsafeLocationError, match="duckdb_temp_dir"):
        Settings(data_root=root, duckdb_temp_dir=tmp_path / "elsewhere" / "duck")
    same = Settings(data_root=root, duckdb_temp_dir=root.parent / "spill")
    assert same.layout["tmp_duckdb"] == root.parent / "spill"
    assert same.duckdb_settings()["temp_directory"] == str(root.parent / "spill")


def test_unchecked_load_reports_instead_of_raising(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        config, "drive_info", lambda path: DriveInfo("C", "DRIVE_REMOTE", "", "NTFS")
    )
    s = config.load_settings(checked=False, data_root=tmp_path / "root")
    with pytest.raises(UnsafeLocationError):
        s.require_safe()
    with pytest.raises(UnsafeLocationError):
        config.get_settings()  # the process-wide accessor never hands out an unsafe root


# ---------------------------------------------------------------------------
# Free-space guard
# ---------------------------------------------------------------------------


def test_free_space_guard(workspace: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(config.shutil, "disk_usage", _fake_disk_usage(50.0))
    space = config.check_free_space(tmp_path / "new" / "root", 100)
    assert space == config.FreeSpace(free_gb=50.0, total_gb=950.0, ok=False)
    with pytest.raises(DiskGuardError, match="DESIGN"):
        config.require_free_space(tmp_path / "new" / "root", 100)
    monkeypatch.setattr(config.shutil, "disk_usage", _fake_disk_usage(100.0))
    assert config.require_free_space(tmp_path, 100).ok is True


# ---------------------------------------------------------------------------
# mwh paths
# ---------------------------------------------------------------------------


def test_paths_table_lists_15_rows_and_source(data_root: Path) -> None:
    result = runner.invoke(app, ["paths"])
    assert result.exit_code == 0, result.output
    for key in LAYOUT_KEYS:
        assert key in result.output
    assert "from env" in result.output
    assert not data_root.exists()  # display never creates


def test_paths_json_shape(data_root: Path) -> None:
    result = runner.invoke(app, ["paths", "--json"])
    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert set(report) == {
        "data_root",
        "data_root_source",
        "sources",
        "workspace",
        "free_space",
        "unsafe",
        "created",
        "layout",
    }
    assert report["data_root"] == str(data_root)
    assert report["data_root_source"] == "env"
    assert report["unsafe"] is None and report["created"] is None
    assert [row["key"] for row in report["layout"]] == list(LAYOUT_KEYS)
    assert all(row["exists"] is False and row["mb_used"] is None for row in report["layout"])
    assert report["free_space"] == {"free_gb": 500.0, "total_gb": 950.0, "ok": True, "min_gb": 100}


def test_paths_create_makes_exactly_15_dirs_and_readme_and_is_idempotent(data_root: Path) -> None:
    result = runner.invoke(app, ["paths", "--create"])
    assert result.exit_code == 0, result.output
    dirs = sorted(p for p in data_root.rglob("*") if p.is_dir())
    files = sorted(p for p in data_root.rglob("*") if p.is_file())
    assert len(dirs) == 15
    assert {p.relative_to(data_root).as_posix() for p in dirs} == {
        "lake",
        "lake/core",
        "lake/derived",
        "lake/marts",
        "lake/manifests",
        "warehouse",
        "runs",
        "runs/jobs",
        "models",
        "notes",
        "ext",
        "ext/demo",
        "studies",
        "tmp",
        "tmp/duckdb",
    }
    assert files == [data_root / "README.txt"]
    text = files[0].read_text(encoding="utf-8")
    assert text == DATA_ROOT_README and "never sync" in text and "GOVERNANCE.md" in text
    before = _tree(data_root)

    again = runner.invoke(app, ["paths", "--create", "--json"])
    assert again.exit_code == 0, again.output
    report = json.loads(again.stdout)
    assert report["created"] == []
    assert _tree(data_root) == before  # nothing touched, mtimes included
    assert all(row["exists"] for row in report["layout"])
    assert next(r for r in report["layout"] if r["key"] == "tmp_duckdb")["mb_used"] == 0.0


def test_paths_create_refuses_below_min_free_and_creates_nothing(
    data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config.shutil, "disk_usage", _fake_disk_usage(50.0))
    result = runner.invoke(app, ["paths", "--create"])
    assert result.exit_code == 2, result.output
    assert "50 GB free" in result.output and "DESIGN" in result.output
    assert not data_root.exists()


def test_paths_create_refuses_forbidden_drive_and_creates_nothing(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Acceptance case: `mwh --data-root G:\\mimicdata paths --create` → exit 2, nothing made."""
    seen: list[Path] = []
    real_mkdir = Path.mkdir

    def spy_mkdir(self: Path, *args, **kwargs):
        seen.append(self)
        return real_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", spy_mkdir)
    monkeypatch.setattr(
        config, "drive_info", lambda path: DriveInfo("G", "DRIVE_FIXED", "", "NTFS")
    )
    result = runner.invoke(app, ["--data-root", r"G:\mimicdata", "paths", "--create"])
    assert result.exit_code == 2, result.output
    assert "D-29" in result.output and "nothing was created" in result.output
    assert not any("mimicdata" in str(p) for p in seen)
    # without --create the layout still prints (for diagnosis) but the exit code is 2
    shown = runner.invoke(app, ["--data-root", r"G:\mimicdata", "paths"])
    assert shown.exit_code == 2 and "unsafe data root" in shown.output


def test_non_diagnostic_commands_refuse_unsafe_root_in_the_callback(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every future command gets validated settings: the callback exits 2 before running."""

    monkeypatch.setattr(
        config, "drive_info", lambda path: DriveInfo("G", "DRIVE_FIXED", "", "NTFS")
    )
    calls: list[str] = []

    @app.command("ep3-probe", hidden=True)
    def _probe(ctx: typer.Context) -> None:  # pragma: no cover - must not run
        calls.append("ran")

    try:
        result = runner.invoke(app, ["--data-root", r"G:\mimicdata", "ep3-probe"])
    finally:
        app.registered_commands[:] = [c for c in app.registered_commands if c.name != "ep3-probe"]
    assert result.exit_code == 2, result.output
    assert "D-29" in result.output and calls == []


# ---------------------------------------------------------------------------
# mwh doctor upgrades
# ---------------------------------------------------------------------------


def test_doctor_json_includes_new_check_ids(mocked_doctor: Path) -> None:
    result = runner.invoke(app, ["doctor", "--json"])
    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    ids = [c["id"] for c in report["checks"]]
    assert ids == list(doctor.CHECK_IDS)
    for new in ("settings", "temp_dir", "cloud_mounts", "defender", "power_scheme"):
        assert new in ids
    by_id = {c["id"]: c for c in report["checks"]}
    assert by_id["data_root"]["status"] == "pass"
    assert by_id["data_root"]["value"]["volume"] == FIXED_NTFS.to_dict()
    assert by_id["temp_dir"]["status"] == "pass" and "creatable" in by_id["temp_dir"]["detail"]
    assert by_id["cloud_mounts"]["status"] == "info"
    assert (
        by_id["defender"]["status"] == "info" and "Add-MpPreference" in by_id["defender"]["detail"]
    )
    assert by_id["settings"]["status"] == "info"
    assert by_id["settings"]["value"]["sources"]["env"] == ["data_root"]
    assert by_id["settings"]["value"]["dotenv_present"] is False
    assert by_id["power_scheme"]["status"] == "info"
    assert by_id["power_scheme"]["value"]["overlay_name"] == "Best performance"
    assert "source material" not in result.output


def test_doctor_data_root_fails_on_unsafe_location(
    mocked_doctor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        config, "drive_info", lambda path: DriveInfo("G", "DRIVE_FIXED", "Google Drive", "FAT32")
    )
    result = runner.invoke(app, ["--data-root", r"G:\probe", "doctor", "--json"])
    assert result.exit_code == 1, result.output
    report = json.loads(result.stdout)
    assert report["ok"] is False
    root = next(c for c in report["checks"] if c["id"] == "data_root")
    assert (
        root["status"] == "fail" and "D-29" in root["detail"] and "Google Drive" in root["detail"]
    )


def test_doctor_temp_dir_fails_on_other_volume(
    mocked_doctor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "volume_of", lambda p: "OTHER" if "elsewhere" in str(p) else "ROOT")
    monkeypatch.setenv("MWH_DUCKDB_TEMP_DIR", str(mocked_doctor.parent / "elsewhere"))
    result = runner.invoke(app, ["doctor", "--json"])
    report = json.loads(result.stdout)
    temp = next(c for c in report["checks"] if c["id"] == "temp_dir")
    assert temp["status"] == "fail" and result.exit_code == 1


def test_doctor_cloud_mounts_lists_suspicious_volumes_and_warns_for_repo(
    mocked_doctor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    volumes = {
        "C": FIXED_NTFS,
        "D": DriveInfo("D", "DRIVE_REMOTE", "Google Cryptomator", "cryptoFs"),
        "G": DriveInfo("G", "DRIVE_FIXED", "Google Drive", "FAT32"),
    }
    monkeypatch.setattr(config, "logical_drives", lambda: list(volumes))
    monkeypatch.setattr(
        config, "drive_info", lambda path: volumes[config.drive_letter(path) or "C"]
    )
    monkeypatch.setattr(
        config, "drive_letter", lambda path: str(path)[0].upper() if str(path)[1:2] == ":" else ""
    )
    res = doctor.check_cloud_mounts(mocked_doctor.parent)
    assert res.status == "info"
    assert [s["letter"] for s in res.value["suspicious"]] == ["D", "G"]
    assert "Google Drive" in res.detail and "cryptoFs" in res.detail
    # repository on a synced volume → warn (never fail)
    res = doctor.check_cloud_mounts(Path(r"G:\repo"))
    assert res.status == "warn" and res.value["repo_problem"]


def test_doctor_defender_states(mocked_doctor: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor, "_defender_exclusions", lambda: [str(mocked_doctor)])
    assert doctor.check_defender(mocked_doctor).status == "pass"
    monkeypatch.setattr(doctor, "_defender_exclusions", lambda: [str(mocked_doctor.parent)])
    assert doctor.check_defender(mocked_doctor).status == "pass"  # parent exclusion covers it
    monkeypatch.setattr(doctor, "_defender_exclusions", lambda: [r"C:\somewhere-else"])
    res = doctor.check_defender(mocked_doctor)
    assert res.status == "warn" and "Add-MpPreference" in res.detail
    monkeypatch.setattr(doctor, "_defender_exclusions", lambda: None)
    assert doctor.check_defender(mocked_doctor).status == "info"


def test_doctor_settings_warns_when_allow_remote(
    mocked_doctor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MWH_ALLOW_REMOTE", "true")
    res = doctor.check_settings(config.load_settings())
    assert res.status == "warn" and "GOVERNANCE" in res.detail


# ---------------------------------------------------------------------------
# Repo hygiene: .env.example lists every key; .env is ignored
# ---------------------------------------------------------------------------


def test_env_example_lists_every_setting_and_env_is_gitignored() -> None:
    example = (WORKSPACE / ".env.example").read_text(encoding="utf-8")
    for field in Settings.model_fields:
        assert f"MWH_{field.upper()}=" in example, field
    gitignore = (REPO / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in gitignore and "!.env.example" in gitignore
