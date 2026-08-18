"""EP-2 — `mwh` CLI skeleton + `mwh doctor` acceptance tests.

Every subprocess-backed probe (uv, git, nvidia-smi, PowerShell/BitLocker) and the
registry read are mocked, so this module passes on any host and never shells out; the
``mocked_probes`` fixture makes an unmocked ``subprocess.run`` an immediate failure.
Only synthetic values appear here — no data, no identifiers.

EP-3 upgraded the doctor (settings-driven, 13 checks, ``data_root`` *fails* on an unsafe
location, ``powercfg`` / ``Get-MpPreference`` probes) and replaced ``resolve_data_root`` with
``mimicwarehouse.config.Settings``; EP-164 added the 14th check (``antivirus``,
``root/SecurityCenter2`` via PowerShell — the fake runner answers it with a Defender-only
host); the assertions below were updated accordingly.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from collections import namedtuple
from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

import mimicwarehouse
from mimicwarehouse import config, doctor
from mimicwarehouse.cli import app

pytestmark = pytest.mark.ep_2

runner = CliRunner()

DiskUsage = namedtuple("DiskUsage", "total used free")
FAKE_GPU_LINE = "Synthetic GPU 0, 8192 MiB, 999.99"
# EP-164: what `root/SecurityCenter2` returns on a Defender-only host (state 0x061100 = on)
DEFENDER_ONLY_JSON = (
    '{"displayName":"Windows Defender","productState":397568,'
    '"pathToSignedProductExe":"windowsdefender://"}'
)
FIXED_NTFS = config.DriveInfo(
    letter="C", drive_type="DRIVE_FIXED", label="Windows", filesystem="NTFS"
)


def _fake_disk_usage(free_gb: float, total_gb: float = 950.0):
    def fake(path):
        total = int(total_gb * doctor.GB)
        free = int(free_gb * doctor.GB)
        return DiskUsage(total=total, used=total - free, free=free)

    return fake


def _fake_run_factory(*, missing: frozenset[str] = frozenset(), bitlocker: str = "1"):
    """A `subprocess.run` stand-in keyed on argv[0]; unknown tools are a test failure."""

    def fake_run(argv, **kwargs):
        tool = Path(argv[0]).name.lower().removesuffix(".exe")
        if tool in missing:
            raise FileNotFoundError(argv[0])
        assert kwargs.get("timeout") == doctor.SUBPROCESS_TIMEOUT_S, "probes need a 10 s timeout"
        if tool == "powershell" and "Get-MpPreference" in argv[-1]:  # EP-3 defender probe
            tool = "get-mppreference"
        elif tool == "powershell" and "SecurityCenter2" in argv[-1]:  # EP-164 antivirus probe
            tool = "securitycenter2"
        out = {
            "uv": "uv 0.0.0-test\n",
            "nvidia-smi": FAKE_GPU_LINE + "\n",
            "powershell": bitlocker + "\n",
            "get-mppreference": "N/A: Must be an administrator to view exclusions\n",
            "securitycenter2": DEFENDER_ONLY_JSON + "\n",
            "powercfg": "Power Scheme GUID: 381b4222-f694-41f0-9685-ff5bb260df2e  (Balanced)\n",
            "git": "true\n",
        }
        assert tool in out, f"unexpected subprocess in doctor tests: {argv!r}"
        return subprocess.CompletedProcess(argv, 0, stdout=out[tool], stderr="")

    return fake_run


@pytest.fixture
def mocked_probes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    """Healthy host: every probe mocked, data root = a writable tmp dir on the repo drive."""
    monkeypatch.setattr(doctor, "IS_WINDOWS", True)
    monkeypatch.setattr(doctor.subprocess, "run", _fake_run_factory())
    monkeypatch.setattr(config.shutil, "disk_usage", _fake_disk_usage(500.0))
    monkeypatch.setattr(doctor, "_longpaths_registry", lambda: 1)
    monkeypatch.setattr(doctor, "_power_overlay_registry", lambda: None)
    # keep the drive comparison meaningful on any host: the fake root lives on "C:"
    monkeypatch.setattr(doctor, "_drive_of", lambda p: "C:")
    monkeypatch.setattr(doctor, "_mount_of", lambda p: str(p))
    # EP-3: location-safety probes mocked to a healthy fixed NTFS volume; settings files are
    # looked up in an empty temp workspace; MWH_* overrides cleared
    monkeypatch.setattr(config, "drive_info", lambda p: FIXED_NTFS)
    monkeypatch.setattr(config, "logical_drives", lambda: ["C"])
    monkeypatch.setattr(config, "volume_of", lambda p: "VOL")
    monkeypatch.setattr(config, "onedrive_roots", lambda: [])
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(config, "workspace_root", lambda: workspace)
    monkeypatch.delenv("MWH_DATA_ROOT", raising=False)
    config.configure()
    data_root = tmp_path / "mimicdata"
    data_root.mkdir()
    yield data_root
    config.configure()


def _doctor_json(args: list[str]) -> tuple[int, dict]:
    result = runner.invoke(app, args)
    assert result.exception is None or isinstance(result.exception, SystemExit), result.output
    return result.exit_code, json.loads(result.stdout)


# ---------------------------------------------------------------------------
# CLI skeleton
# ---------------------------------------------------------------------------


def test_version_is_eager_and_matches_package() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0, result.output
    assert result.output.strip() == f"mwh {mimicwarehouse.__version__}"
    assert mimicwarehouse.__version__ == "0.1.0"


def test_help_lists_doctor() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0, result.output
    assert "doctor" in result.output
    assert "--data-root" in result.output


def test_no_args_shows_help() -> None:
    result = runner.invoke(app, [])
    assert "Usage" in result.output


def test_cli_import_does_not_pull_heavy_libraries() -> None:
    """`mwh --help` must stay fast: no duckdb/pandas/polars/pyarrow at CLI import time."""
    code = (
        "import sys, mimicwarehouse.cli; "
        "print(sorted(m for m in ('duckdb','pandas','polars','pyarrow') if m in sys.modules))"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, timeout=60
    )
    assert proc.stdout.strip() == "[]", proc.stdout


def test_data_root_flag_beats_env_beats_default(
    mocked_probes: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """EP-2's `resolve_data_root` became `config.Settings` in EP-3; the precedence survives."""
    assert config.load_settings().data_root == config.DEFAULT_DATA_ROOT
    monkeypatch.setenv("MWH_DATA_ROOT", str(tmp_path / "env"))
    assert config.load_settings().data_root == tmp_path / "env"
    assert config.load_settings(data_root=tmp_path / "flag").data_root == tmp_path / "flag"
    _code, report = _doctor_json(["--data-root", str(tmp_path / "flag"), "doctor", "--json"])
    root = next(c for c in report["checks"] if c["id"] == "data_root")
    assert root["value"]["path"] == str(tmp_path / "flag")


# ---------------------------------------------------------------------------
# mwh doctor — healthy mocked host
# ---------------------------------------------------------------------------


def test_doctor_json_shape(mocked_probes: Path) -> None:
    code, report = _doctor_json(["--data-root", str(mocked_probes), "doctor", "--json"])
    assert code == 0
    assert set(report) == {"timestamp", "host", "checks", "ok"}
    assert isinstance(report["ok"], bool) and report["ok"] is True
    ids = [c["id"] for c in report["checks"]]
    assert ids == list(doctor.CHECK_IDS)
    # EP-2's 8 + settings · temp_dir · cloud_mounts · defender · power_scheme (EP-3) + antivirus
    # (EP-164)
    assert len(ids) == 14
    assert {
        "python",
        "uv",
        "duckdb",
        "disk_free",
        "data_root",
        "bitlocker",
        "gpu",
        "longpaths",
    } <= set(ids)
    for check in report["checks"]:
        assert set(check) == {"id", "status", "detail", "value"}
        assert check["status"] in {"pass", "warn", "fail", "info"}
    by_id = {c["id"]: c for c in report["checks"]}
    assert by_id["gpu"]["status"] == "info"
    assert by_id["gpu"]["detail"] == FAKE_GPU_LINE
    assert by_id["uv"]["status"] == "pass"
    assert by_id["bitlocker"]["status"] == "pass"
    assert by_id["data_root"]["status"] == "pass"
    assert by_id["longpaths"]["status"] == "pass"
    assert by_id["disk_free"]["value"]["C:"]["free_gb"] == 500.0


def test_doctor_table_renders_and_exits_zero(mocked_probes: Path) -> None:
    result = runner.invoke(app, ["--data-root", str(mocked_probes), "doctor"])
    assert result.exit_code == 0, result.output
    for check_id in doctor.CHECK_IDS:
        assert check_id in result.output
    assert "doctor:" in result.output and "OK" in result.output


def test_doctor_report_never_dumps_environment(
    mocked_probes: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MWH_SECRET_CANARY", "canary-value-90000001")
    result = runner.invoke(app, ["--data-root", str(mocked_probes), "doctor", "--json"])
    assert "canary-value-90000001" not in result.output
    assert "source material" not in result.output


# ---------------------------------------------------------------------------
# mwh doctor — monkeypatched failures
# ---------------------------------------------------------------------------


def test_disk_free_50gb_fails_and_exit_code_1(
    mocked_probes: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config.shutil, "disk_usage", _fake_disk_usage(50.0))
    code, report = _doctor_json(["--data-root", str(mocked_probes), "doctor", "--json"])
    assert code == 1
    assert report["ok"] is False
    disk = next(c for c in report["checks"] if c["id"] == "disk_free")
    assert disk["status"] == "fail"
    assert disk["value"]["C:"]["free_gb"] == 50.0


def test_disk_free_thresholds(mocked_probes: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config.shutil, "disk_usage", _fake_disk_usage(120.0))
    assert doctor.check_disk_free(mocked_probes, mocked_probes).status == "warn"
    monkeypatch.setattr(config.shutil, "disk_usage", _fake_disk_usage(99.9))
    assert doctor.check_disk_free(mocked_probes, mocked_probes).status == "fail"
    monkeypatch.setattr(config.shutil, "disk_usage", _fake_disk_usage(150.0))
    assert doctor.check_disk_free(mocked_probes, mocked_probes).status == "pass"


def test_duckdb_pin_mismatch_fails(mocked_probes: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor, "_duckdb_pinned_version", lambda: "0.0.1")
    monkeypatch.setattr(doctor, "_duckdb_installed_version", lambda: "1.5.5")
    result = doctor.check_duckdb()
    assert result.status == "fail"
    assert result.value == {"installed": "1.5.5", "pin": "0.0.1"}
    code, report = _doctor_json(["--data-root", str(mocked_probes), "doctor", "--json"])
    assert code == 1 and report["ok"] is False


def test_duckdb_pin_matches_real_install() -> None:
    """The real pin from importlib.metadata equals the installed duckdb (EP-1 invariant)."""
    result = doctor.check_duckdb()
    assert result.status == "pass", result.detail


def test_missing_uv_and_nvidia_smi_do_not_raise(
    mocked_probes: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        doctor.subprocess, "run", _fake_run_factory(missing=frozenset({"uv", "nvidia-smi"}))
    )
    uv = doctor.check_uv()
    assert uv.status == "warn" and "not found" in uv.detail
    gpu = doctor.check_gpu()
    assert gpu.status == "info" and "no NVIDIA driver" in gpu.detail
    code, report = _doctor_json(["--data-root", str(mocked_probes), "doctor", "--json"])
    assert code == 0 and report["ok"] is True  # warn/info never fail the run


def test_probe_timeout_becomes_warn(monkeypatch: pytest.MonkeyPatch) -> None:
    def hang(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs.get("timeout", 0))

    monkeypatch.setattr(doctor.subprocess, "run", hang)
    assert doctor.check_uv().status == "warn"
    assert doctor.check_gpu().status == "info"


def test_bitlocker_off_fails(mocked_probes: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor, "_bitlocker_protection", lambda drive: 2)
    result = doctor.check_bitlocker(["C:"])
    assert result.status == "fail"
    assert result.value == {"C:": 2}
    code, report = _doctor_json(["--data-root", str(mocked_probes), "doctor", "--json"])
    assert code == 1 and report["ok"] is False


@pytest.mark.parametrize(
    ("code", "expected"),
    [(1, "pass"), (2, "fail"), (3, "warn"), (5, "warn"), (4, "warn"), (None, "warn")],
)
def test_bitlocker_state_mapping(
    mocked_probes: Path, monkeypatch: pytest.MonkeyPatch, code: int | None, expected: str
) -> None:
    monkeypatch.setattr(doctor, "_bitlocker_protection", lambda drive: code)
    assert doctor.check_bitlocker(["C:"]).status == expected


def test_bitlocker_probe_via_powershell_stdout(
    mocked_probes: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(doctor.subprocess, "run", _fake_run_factory(bitlocker="2"))
    assert doctor.check_bitlocker(["C:"]).status == "fail"
    monkeypatch.setattr(doctor.subprocess, "run", _fake_run_factory(bitlocker="garbage"))
    assert doctor.check_bitlocker(["C:"]).status == "warn"


def test_data_root_missing_warns_and_forbidden_drive_fails(
    mocked_probes: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    missing = doctor.check_data_root(tmp_path / "does-not-exist")
    assert missing.status == "warn" and "mwh paths --create" in missing.detail

    # crafted violation from the EP-2 brief: `mwh --data-root G:\probe doctor` — EP-2 warned
    # (non-C: drive); EP-3 refuses via the D-29 location check → fail, exit 1
    monkeypatch.setattr(doctor, "_drive_of", lambda p: "G:")
    monkeypatch.setattr(
        config, "drive_info", lambda p: config.DriveInfo("G", "DRIVE_FIXED", "", "NTFS")
    )
    code, report = _doctor_json(["--data-root", r"G:\probe", "doctor", "--json"])
    root = next(c for c in report["checks"] if c["id"] == "data_root")
    assert code == 1 and report["ok"] is False
    assert root["status"] == "fail"
    assert "D-29" in root["detail"] and "forbidden" in root["detail"]
    assert root["value"]["drive"] == "G:"


def test_longpaths_warns_when_registry_or_git_unset(
    mocked_probes: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(doctor, "_longpaths_registry", lambda: 0)
    result = doctor.check_longpaths(mocked_probes)
    assert result.status == "warn" and "LongPathsEnabled" in result.detail
    monkeypatch.setattr(doctor, "_longpaths_registry", lambda: 1)
    monkeypatch.setattr(doctor, "_git_longpaths", lambda repo: None)
    result = doctor.check_longpaths(mocked_probes)
    assert result.status == "warn" and "core.longpaths" in result.detail


def test_windows_only_probes_are_info_elsewhere(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor, "IS_WINDOWS", False)
    monkeypatch.setattr(doctor.subprocess, "run", _fake_run_factory(missing=frozenset({"x"})))
    assert doctor.check_bitlocker(["C:"]).status == "info"
    assert doctor.check_longpaths(None).status == "info"


def test_check_result_roundtrips_to_json() -> None:
    result = doctor.CheckResult("python", "pass", "ok", {"version": "3.13.0"})
    assert json.loads(json.dumps(result.to_dict())) == {
        "id": "python",
        "status": "pass",
        "detail": "ok",
        "value": {"version": "3.13.0"},
    }
    assert doctor.summary_line([result]).startswith("doctor: 1 pass · 0 warn · 0 fail · 0 info")


def test_disk_usage_is_called_through_shutil(mocked_probes: Path) -> None:
    """Sanity: the mocked disk probe is what run_checks consults (guards the monkeypatch seam;
    since EP-3 the doctor asks `config.check_free_space`, which calls `shutil.disk_usage`)."""
    assert shutil.disk_usage is config.shutil.disk_usage
