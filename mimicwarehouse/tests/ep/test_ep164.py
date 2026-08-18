"""EP-164 — Toolchain remediation (P1): the ``mwh doctor`` ``antivirus`` check.

Every probe is faked at the ``_run`` / ``_powershell`` / ``subprocess.run`` seams, so this
module passes on any host and never shells out; an unmocked ``subprocess.run`` is a test
failure. Only synthetic product rows appear here — product names and ``productState`` values
of the kind ``root/SecurityCenter2`` returns; no data, no identifiers.

Recorded on the owner's host at EP-164 (2026-08-17): Malwarebytes Premium is listed with
``productState`` ``0x060000`` — "real-time off" per Security Center — although its own
protection modules run (they quarantined ``bash.exe`` the day before, D-42), because a
third-party product reports "on" only when it is *the* registered Security Center antivirus.
The check therefore warns on the **presence** of a non-Defender product and reports the WSC
bit descriptively; case (c) below pins that decision.
"""

from __future__ import annotations

import json
import subprocess
from collections import namedtuple
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from mimicwarehouse import config, doctor
from mimicwarehouse.cli import app

pytestmark = pytest.mark.ep_164

runner = CliRunner()

DiskUsage = namedtuple("DiskUsage", "total used free")
FIXED_NTFS = config.DriveInfo(
    letter="C", drive_type="DRIVE_FIXED", label="Windows", filesystem="NTFS"
)

# ``productState`` values as Security Center reports them (0xAABBCC; BB = real-time, CC = sigs)
DEFENDER_ON = 0x061100  # 397568 — Windows Defender, real-time on, up to date
THIRD_PARTY_ON = 0x061000  # a third-party product registered as the active WSC antivirus
THIRD_PARTY_OFF = 0x060000  # 393216 — what Malwarebytes reports on the owner's host
THIRD_PARTY_ON_OUTDATED = 0x061010

DEFENDER_ROW = {
    "displayName": "Windows Defender",
    "productState": DEFENDER_ON,
    "pathToSignedProductExe": "windowsdefender://",
}


def _row(name: str, state: int, exe: str = r"C:\Program Files\Synthetic AV\wsc.exe") -> dict:
    return {"displayName": name, "productState": state, "pathToSignedProductExe": exe}


def _wsc_json(rows: list[dict]) -> str:
    """``ConvertTo-Json -Compress``: one object for a single row, an array otherwise."""
    return json.dumps(rows[0] if len(rows) == 1 else rows, separators=(",", ":"))


DEFENDER_ONLY_JSON = _wsc_json([DEFENDER_ROW])


def _fake_run_factory(
    *, securitycenter: str | Exception = DEFENDER_ONLY_JSON, returncode: int = 0
) -> Callable[..., subprocess.CompletedProcess[str]]:
    """``subprocess.run`` stand-in keyed on argv[0] / the PowerShell script; unknown tools
    fail the test. ``securitycenter`` is what the SecurityCenter2 query prints (or raises)."""

    def fake_run(argv, **kwargs):
        tool = Path(argv[0]).name.lower().removesuffix(".exe")
        assert kwargs.get("timeout") == doctor.SUBPROCESS_TIMEOUT_S, "probes need a 10 s timeout"
        if tool == "powershell":
            script = argv[-1]
            if "SecurityCenter2" in script:
                assert "AntiVirusProduct" in script and "ConvertTo-Json" in script
                if isinstance(securitycenter, Exception):
                    raise securitycenter
                return subprocess.CompletedProcess(
                    argv, returncode, stdout=securitycenter + "\n", stderr=""
                )
            if "Get-MpPreference" in script:
                out = "N/A: Must be an administrator to view exclusions\n"
            else:  # BitLocker Shell COM probe
                out = "1\n"
        else:
            out = {
                "uv": "uv 0.0.0-test\n",
                "nvidia-smi": "Synthetic GPU 0, 8192 MiB, 999.99\n",
                "powercfg": "Power Scheme GUID: 381b4222-f694-41f0-9685-ff5bb260df2e  (Balanced)\n",
                "git": "true\n",
            }.get(tool)
            assert out is not None, f"unexpected subprocess in EP-164 tests: {argv!r}"
        return subprocess.CompletedProcess(argv, 0, stdout=out, stderr="")

    return fake_run


def _fake_disk_usage(free_gb: float, total_gb: float = 950.0):
    def fake(path):
        total = int(total_gb * doctor.GB)
        free = int(free_gb * doctor.GB)
        return DiskUsage(total=total, used=total - free, free=free)

    return fake


@pytest.fixture
def mocked_host(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    """Healthy Windows host with every probe faked (Defender-only unless a test re-patches
    ``subprocess.run``); yields the writable fake data root."""
    monkeypatch.setattr(doctor, "IS_WINDOWS", True)
    monkeypatch.setattr(doctor.subprocess, "run", _fake_run_factory())
    monkeypatch.setattr(config.shutil, "disk_usage", _fake_disk_usage(500.0))
    monkeypatch.setattr(doctor, "_longpaths_registry", lambda: 1)
    monkeypatch.setattr(doctor, "_power_overlay_registry", lambda: None)
    monkeypatch.setattr(doctor, "_drive_of", lambda p: "C:")
    monkeypatch.setattr(doctor, "_mount_of", lambda p: str(p))
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


def _antivirus_with(monkeypatch: pytest.MonkeyPatch, rows: list[dict]) -> doctor.CheckResult:
    monkeypatch.setattr(doctor, "IS_WINDOWS", True)
    monkeypatch.setattr(doctor.subprocess, "run", _fake_run_factory(securitycenter=_wsc_json(rows)))
    return doctor.check_antivirus()


# ---------------------------------------------------------------------------
# (a) Defender only → info
# ---------------------------------------------------------------------------


def test_defender_only_is_info(monkeypatch: pytest.MonkeyPatch) -> None:
    res = _antivirus_with(monkeypatch, [DEFENDER_ROW])
    assert res.id == "antivirus" and res.status == "info"
    assert "Windows Defender" in res.detail and "only product" in res.detail
    assert res.value == {
        "products": [
            {
                "name": "Windows Defender",
                "state": "0x061100",
                "enabled": True,
                "up_to_date": True,
                "exe": "windowsdefender://",
            }
        ],
        "non_defender": [],
        "non_defender_realtime": [],
    }
    assert "source material" not in res.detail  # the allow list is only spelled out on warn


def test_microsoft_defender_antivirus_name_counts_as_defender(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    res = _antivirus_with(monkeypatch, [_row("Microsoft Defender Antivirus", DEFENDER_ON)])
    assert res.status == "info" and res.value["non_defender"] == []


# ---------------------------------------------------------------------------
# (b) Defender + a second real-time product → warn, names it, reminds about the allow list
# ---------------------------------------------------------------------------


def test_second_realtime_product_warns_and_names_the_allow_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    res = _antivirus_with(monkeypatch, [_row("Synthetic Shield", THIRD_PARTY_ON), DEFENDER_ROW])
    assert res.status == "warn"
    assert "Synthetic Shield" in res.detail and "Windows Defender" in res.detail
    assert "allow list" in res.detail and "not readable non-elevated" in res.detail
    assert "D-38" in res.detail
    for path in doctor.D38_ALLOW_LIST:  # the seven paths, spelled out
        assert path in res.detail
    assert len(doctor.D38_ALLOW_LIST) == 7
    assert res.value["non_defender"] == ["Synthetic Shield"]
    assert res.value["non_defender_realtime"] == ["Synthetic Shield"]
    by_name = {p["name"]: p for p in res.value["products"]}
    assert by_name["Synthetic Shield"]["enabled"] is True
    assert by_name["Synthetic Shield"]["state"] == "0x061000"
    assert set(by_name["Synthetic Shield"]) == {"name", "state", "enabled", "up_to_date", "exe"}
    assert "registration, not whether" not in res.detail  # bit is on → no WSC caveat needed


def test_outdated_signatures_are_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    res = _antivirus_with(
        monkeypatch, [DEFENDER_ROW, _row("Synthetic Shield", THIRD_PARTY_ON_OUTDATED)]
    )
    assert res.status == "warn"
    shield = next(p for p in res.value["products"] if p["name"] == "Synthetic Shield")
    assert shield["up_to_date"] is False and shield["enabled"] is True
    assert "out of date" in res.detail


# ---------------------------------------------------------------------------
# (c) second product present, WSC real-time bit off — still warn (presence is the trigger)
# ---------------------------------------------------------------------------


def test_second_product_with_wsc_realtime_off_still_warns(monkeypatch: pytest.MonkeyPatch) -> None:
    """The owner's host: Malwarebytes reports 0x060000 while its modules run (D-42)."""
    res = _antivirus_with(monkeypatch, [_row("Malwarebytes", THIRD_PARTY_OFF), DEFENDER_ROW])
    assert res.status == "warn"
    assert "Malwarebytes" in res.detail and "real-time off per Security Center" in res.detail
    assert "registration, not whether its own modules run" in res.detail
    assert res.value["non_defender"] == ["Malwarebytes"]
    assert res.value["non_defender_realtime"] == []  # the WSC bit, reported as decoded
    mb = next(p for p in res.value["products"] if p["name"] == "Malwarebytes")
    assert mb == {
        "name": "Malwarebytes",
        "state": "0x060000",
        "enabled": False,
        "up_to_date": True,
        "exe": r"C:\Program Files\Synthetic AV\wsc.exe",
    }


# ---------------------------------------------------------------------------
# (d) empty / non-JSON / missing tool / non-zero exit / odd shape → info with the reason
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("securitycenter", "returncode", "reason"),
    [
        ("", 0, "lists no antivirus product"),
        ("not json at all", 0, "no JSON"),
        ("1", 0, "unexpected JSON shape"),
        ('["a", "b"]', 0, "unexpected JSON shape"),
        ("", 1, "SecurityCenter2 not available"),
        (FileNotFoundError("powershell"), 0, "not found on PATH"),
        (subprocess.TimeoutExpired(["powershell"], 10), 0, "timed out"),
    ],
)
def test_probe_failures_are_info_with_a_reason(
    monkeypatch: pytest.MonkeyPatch,
    securitycenter: str | Exception,
    returncode: int,
    reason: str,
) -> None:
    monkeypatch.setattr(doctor, "IS_WINDOWS", True)
    monkeypatch.setattr(
        doctor.subprocess,
        "run",
        _fake_run_factory(securitycenter=securitycenter, returncode=returncode),
    )
    res = doctor.check_antivirus()  # never raises
    assert res.status == "info", res.detail
    assert reason in res.detail
    assert "D-38" in res.detail
    assert res.value["products"] == [] and res.value["non_defender_realtime"] == []


def test_missing_product_state_is_reported_as_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [DEFENDER_ROW, {"displayName": "Synthetic Shield", "productState": None}]
    res = _antivirus_with(monkeypatch, rows)
    assert res.status == "warn"  # present → warn, even when WSC gives no state
    shield = next(p for p in res.value["products"] if p["name"] == "Synthetic Shield")
    assert shield == {
        "name": "Synthetic Shield",
        "state": None,
        "enabled": None,
        "up_to_date": None,
        "exe": None,
    }
    assert "state unknown" in res.detail


# ---------------------------------------------------------------------------
# (e) non-Windows → info, and no probe is even attempted
# ---------------------------------------------------------------------------


def test_non_windows_is_info_without_probing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor, "IS_WINDOWS", False)

    def boom(argv, **kwargs):
        raise AssertionError(f"antivirus probe must not run off Windows: {argv!r}")

    monkeypatch.setattr(doctor.subprocess, "run", boom)
    res = doctor.check_antivirus()
    assert res.status == "info" and "not a Windows host" in res.detail
    assert res.value is None


# ---------------------------------------------------------------------------
# Probe seam and decoding
# ---------------------------------------------------------------------------


def test_probe_goes_through_the_powershell_seam_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor, "IS_WINDOWS", True)
    calls: list[str] = []

    def fake_powershell(script: str) -> subprocess.CompletedProcess[str]:
        calls.append(script)
        return subprocess.CompletedProcess(["powershell"], 0, stdout=DEFENDER_ONLY_JSON, stderr="")

    monkeypatch.setattr(doctor, "_powershell", fake_powershell)
    assert doctor.check_antivirus().status == "info"
    assert calls == [doctor.SECURITYCENTER_SCRIPT]
    assert "root/SecurityCenter2" in calls[0] and "-ErrorAction Stop" in calls[0]
    # only the three columns the brief allows are selected — nothing else is queried
    assert "displayName, productState, pathToSignedProductExe" in calls[0]


@pytest.mark.parametrize(
    ("state", "enabled", "up_to_date"),
    [
        (0x061100, True, True),  # Defender on
        (0x060100, False, True),  # Defender off (third party registered instead)
        (0x061000, True, True),  # third party on
        (0x060000, False, True),  # third party present, not the registered WSC antivirus
        (0x061010, True, False),  # on, signatures out of date
        (0x040000, False, True),  # older provider class, off
    ],
)
def test_product_state_decoding(
    monkeypatch: pytest.MonkeyPatch, state: int, enabled: bool, up_to_date: bool
) -> None:
    monkeypatch.setattr(
        doctor.subprocess, "run", _fake_run_factory(securitycenter=_wsc_json([_row("X", state)]))
    )
    (product,) = doctor._securitycenter_products()
    assert product["enabled"] is enabled and product["up_to_date"] is up_to_date
    assert product["state"] == f"0x{state:06x}"


def test_check_never_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """The check reports, the owner decides — every scenario is info or warn."""
    scenarios = [
        [DEFENDER_ROW],
        [DEFENDER_ROW, _row("A", THIRD_PARTY_ON)],
        [_row("A", THIRD_PARTY_OFF), _row("B", THIRD_PARTY_ON_OUTDATED)],
        [_row("A", THIRD_PARTY_ON), _row("B", THIRD_PARTY_ON), DEFENDER_ROW],
    ]
    for rows in scenarios:
        assert _antivirus_with(monkeypatch, rows).status in {"info", "warn"}


# ---------------------------------------------------------------------------
# (f) mwh doctor --json: 14 check ids, antivirus after defender, warn never fails the run
# ---------------------------------------------------------------------------


def _doctor_json(args: list[str]) -> tuple[int, dict]:
    result = runner.invoke(app, args)
    assert result.exception is None or isinstance(result.exception, SystemExit), result.output
    return result.exit_code, json.loads(result.stdout)


def test_doctor_json_lists_14_checks_with_antivirus_after_defender(mocked_host: Path) -> None:
    code, report = _doctor_json(["--data-root", str(mocked_host), "doctor", "--json"])
    assert code == 0 and report["ok"] is True
    ids = [c["id"] for c in report["checks"]]
    assert ids == list(doctor.CHECK_IDS) and len(ids) == 14
    assert ids.index("antivirus") == ids.index("defender") + 1
    for check in report["checks"]:
        assert set(check) == {"id", "status", "detail", "value"}
    av = next(c for c in report["checks"] if c["id"] == "antivirus")
    assert av["status"] == "info" and av["value"]["non_defender_realtime"] == []
    assert "source material" not in json.dumps(report)


def test_doctor_with_second_product_warns_but_exits_zero(
    mocked_host: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = [_row("Malwarebytes", THIRD_PARTY_OFF), DEFENDER_ROW]
    monkeypatch.setattr(doctor.subprocess, "run", _fake_run_factory(securitycenter=_wsc_json(rows)))
    code, report = _doctor_json(["--data-root", str(mocked_host), "doctor", "--json"])
    assert code == 0 and report["ok"] is True  # warn never fails
    av = next(c for c in report["checks"] if c["id"] == "antivirus")
    assert av["status"] == "warn" and av["value"]["non_defender"] == ["Malwarebytes"]
    result = runner.invoke(app, ["--data-root", str(mocked_host), "doctor"])
    assert result.exit_code == 0
    assert "antivirus" in result.output and "1 warn" in result.output


def test_doctor_help_and_table_mention_antivirus(mocked_host: Path) -> None:
    result = runner.invoke(app, ["doctor", "--help"])
    assert result.exit_code == 0 and "antivirus" in result.output
    result = runner.invoke(app, ["--data-root", str(mocked_host), "doctor"])
    assert result.exit_code == 0, result.output
    assert "antivirus" in result.output and "doctor:" in result.output
