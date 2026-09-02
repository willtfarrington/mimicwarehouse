"""EP-167 — Retro C: CLI, settings & inventory consolidation: acceptance tests.

Fixture tier only. Covers the shared console + UTF-8 entry point (CFG-6), ``--help`` under an
unsafe/broken configuration (CFG-5), the unknown-``MWH_*`` warning (CFG-1), the per-tier lake
roots and 18-key layout (ARCH-3/FXT-10/ARCH-9), the DuckDB temp-dir policy (CFG-3/CFG-2),
``--data-root`` propagation into the verify child (CFG-4), the ``deny_coverage`` doctor row
(GOV-3) and the inventory resume/header/lookup fixes (INV-1/INV-2/INV-4). Volume probes are
mocked as in ``test_ep03``; the synthetic source tree writes only the 6 ED tables (contract
headers, ids >= 90 000 000); the two subprocess tests run the real ``mwh`` script against this
host with ``PYTHONUTF8``/``PYTHONIOENCODING`` stripped. No real data path is ever read.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections import namedtuple
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import typer
from typer.testing import CliRunner

from mimicwarehouse import config, doctor, inventory, verify
from mimicwarehouse.cli import DIAGNOSTIC_COMMANDS, CliState, app
from mimicwarehouse.config import LAYOUT_KEYS, DriveInfo, Settings, UnsafeLocationError
from mimicwarehouse.console import console_safe
from mimicwarehouse.inventory import (
    build_inventory,
    compute_snapshot_id,
    load_raw_manifest,
    refresh_header_status,
    rel_path_for,
    render_docs,
    write_dataset_manifest,
)
from mimicwarehouse.schema import load_contract

pytestmark = pytest.mark.ep_167

runner = CliRunner()
WORKSPACE = Path(__file__).resolve().parents[2]  # mimicwarehouse/ (the uv project)
MWH_SCRIPT = Path(sys.executable).parent / ("mwh.exe" if os.name == "nt" else "mwh")

DiskUsage = namedtuple("DiskUsage", "total used free")
FIXED_NTFS = DriveInfo(letter="C", drive_type="DRIVE_FIXED", label="Windows", filesystem="NTFS")
ED = "mimic-iv-ed-2.2"


def _fake_disk_usage(free_gb: float, total_gb: float = 950.0):
    def fake(path):
        total = int(total_gb * config.GB)
        free = int(free_gb * config.GB)
        return DiskUsage(total=total, used=total - free, free=free)

    return fake


@pytest.fixture
def workspace(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    """Isolated settings environment: temp workspace, no MWH_* vars, healthy fake volume."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    for key in list(os.environ):
        if key.upper().startswith("MWH_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setattr(config, "workspace_root", lambda: ws)
    monkeypatch.setattr(config, "drive_info", lambda path: FIXED_NTFS)
    monkeypatch.setattr(config, "logical_drives", lambda: ["C"])
    monkeypatch.setattr(config, "volume_of", lambda path: "VOL")
    monkeypatch.setattr(config, "onedrive_roots", lambda: [])
    monkeypatch.setattr(config.shutil, "disk_usage", _fake_disk_usage(500.0))
    config.configure()
    yield ws
    config.configure()


def _write_ed_source(root: Path, *, rows: int = 3) -> None:
    """The 6 ED CSVs with contract headers and synthetic cells (ids >= 90 000 000)."""
    contract = load_contract()
    for t in contract.by_dataset(ED):
        path = root / inventory.DATASET_DIRS[ED] / Path(*t.csv_path.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = [",".join(t.column_names)]
        for i in range(rows):
            lines.append(
                ",".join(
                    str(90_000_001 + i) if c.name.endswith("_id") else f"v{i}" for c in t.columns
                )
            )
        path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


@pytest.fixture
def ed_settings(workspace: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Settings:
    """Validated settings over a temp data root + a synthetic ED-only source tree."""
    source_root = tmp_path / "src"
    _write_ed_source(source_root)
    data_root = tmp_path / "mimicdata"
    data_root.mkdir()
    monkeypatch.setenv("MWH_DATA_ROOT", str(data_root))
    monkeypatch.setenv("MWH_SOURCE_ROOT", str(source_root))
    config.get_settings.cache_clear()
    return config.get_settings()


# ---------------------------------------------------------------------------
# 1. Shared console + UTF-8 entry point (CFG-6)
# ---------------------------------------------------------------------------


def test_every_command_module_shares_the_console_instances() -> None:
    import mimicwarehouse.cli as cli_mod
    import mimicwarehouse.console as console_mod
    import mimicwarehouse.fixtures.cli as fixtures_cli
    import mimicwarehouse.schema.cli as schema_cli

    # EP-33 (B8): errors go through console.fail (stderr) and --json through console.emit_json,
    # so the command modules no longer bind console/err_console for those; cli.py keeps
    # err_console for the unknown-MWH_* warning line only
    assert cli_mod.err_console is console_mod.err_console
    assert inventory.console is console_mod.console
    assert schema_cli.console is console_mod.console
    assert fixtures_cli.console is console_mod.console
    for mod in (inventory, schema_cli, fixtures_cli):
        assert mod.fail is console_mod.fail and mod.emit_json is console_mod.emit_json
    # verify._console_safe stays as an alias of the moved helper
    assert verify._console_safe is console_safe


def test_console_safe_replaces_what_the_stream_cannot_encode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Cp1252Stdout:
        encoding = "cp1252"

    monkeypatch.setattr(sys, "stdout", Cp1252Stdout())
    assert console_safe("ok \u23f1 done") == "ok ? done"  # the EP-6 stopwatch glyph
    assert console_safe("plain ascii") == "plain ascii"

    class Utf8Stdout:
        encoding = "utf-8"

    monkeypatch.setattr(sys, "stdout", Utf8Stdout())
    assert console_safe("\u2611 kept") == "\u2611 kept"


def test_run_reconfigures_both_streams_then_calls_the_app(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mimicwarehouse import console as console_mod

    seen: list[tuple[str, dict[str, Any]]] = []

    class Stream:
        def __init__(self, name: str) -> None:
            self.name = name

        def reconfigure(self, **kwargs: Any) -> None:
            seen.append((self.name, kwargs))

    monkeypatch.setattr(sys, "stdout", Stream("out"))
    monkeypatch.setattr(sys, "stderr", Stream("err"))
    monkeypatch.setattr("mimicwarehouse.cli.app", lambda: seen.append(("app", {})))
    console_mod.run()
    assert seen == [
        ("out", {"encoding": "utf-8", "errors": "replace"}),
        ("err", {"encoding": "utf-8", "errors": "replace"}),
        ("app", {}),
    ]


def test_entry_point_is_the_console_wrapper() -> None:
    import tomllib

    py = tomllib.loads((WORKSPACE / "pyproject.toml").read_text(encoding="utf-8"))
    assert py["project"]["scripts"]["mwh"] == "mimicwarehouse.console:run"


def _script_env() -> dict[str, str]:
    """This process's env without the UTF-8 belts and MWH_* overrides — the wrapper alone
    must make stdio UTF-8."""
    return {
        k: v
        for k, v in os.environ.items()
        if k not in {"PYTHONUTF8", "PYTHONIOENCODING"} and not k.upper().startswith("MWH_")
    }


@pytest.mark.skipif(not MWH_SCRIPT.exists(), reason="mwh script not installed in this venv")
def test_mwh_help_pipes_valid_utf8_without_the_env_belt() -> None:
    proc = subprocess.run(
        [str(MWH_SCRIPT), "--help"],
        cwd=WORKSPACE,
        capture_output=True,
        env=_script_env(),
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    text = proc.stdout.decode("utf-8")  # strict: invalid UTF-8 would raise
    assert "Usage" in text and "\ufffd" not in text


@pytest.mark.skipif(not MWH_SCRIPT.exists(), reason="mwh script not installed in this venv")
def test_mwh_doctor_pipes_utf8_glyphs_and_lf_json() -> None:
    env = _script_env()
    proc = subprocess.run(
        [str(MWH_SCRIPT), "doctor"],
        cwd=WORKSPACE,
        capture_output=True,
        env=env,
        timeout=300,
        check=False,
    )
    assert proc.returncode in (0, 1), proc.stdout + proc.stderr
    text = proc.stdout.decode("utf-8")  # strict
    assert "\u2713" in text  # the pass glyph survives the pipe — no cp1252 downgrade to "?"
    assert "\ufffd" not in text
    proc_json = subprocess.run(
        [str(MWH_SCRIPT), "doctor", "--json"],
        cwd=WORKSPACE,
        capture_output=True,
        env=env,
        timeout=300,
        check=False,
    )
    assert proc_json.returncode in (0, 1)
    assert b"\r\r\n" not in proc_json.stdout  # \n line ends, never double-translated
    report = json.loads(proc_json.stdout.decode("utf-8"))
    assert [c["id"] for c in report["checks"]] == list(doctor.CHECK_IDS)


# ---------------------------------------------------------------------------
# 2. --help under an unsafe root / broken config (CFG-5)
# ---------------------------------------------------------------------------


@pytest.fixture
def unsafe_root(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        config, "drive_info", lambda path: DriveInfo("G", "DRIVE_FIXED", "", "NTFS")
    )


def test_help_works_over_an_unsafe_root_but_commands_refuse(unsafe_root: None) -> None:
    root = ["--data-root", r"G:\nowhere"]
    assert runner.invoke(app, [*root, "inventory", "--help"]).exit_code == 0
    assert runner.invoke(app, [*root, "inventory", "build", "--help"]).exit_code == 0
    assert runner.invoke(app, [*root, "--help"]).exit_code == 0
    build = runner.invoke(app, [*root, "inventory", "build"])
    assert build.exit_code == 2 and "D-29" in build.output


def test_help_works_over_a_broken_dotenv_but_commands_refuse(workspace: Path) -> None:
    (workspace / ".env").write_text("MWH_DATA_ROOTT=C:/typo\n", encoding="utf-8")
    assert runner.invoke(app, ["inventory", "--help"]).exit_code == 0
    assert runner.invoke(app, ["inventory", "build", "--help"]).exit_code == 0
    assert runner.invoke(app, ["--version"]).exit_code == 0
    build = runner.invoke(app, ["inventory", "build"])
    assert build.exit_code == 2 and "mwh:" in build.output
    # diagnostic commands need a Settings instance too — a broken file still exits 2 there,
    # but only when the command actually asks for settings
    docres = runner.invoke(app, ["doctor"])
    assert docres.exit_code == 2


def test_clistate_settings_is_lazy_and_validates_once(workspace: Path) -> None:
    s = config.load_settings(checked=False)
    state = CliState(settings=s, diagnostic=False)
    assert state.settings is s  # healthy root: first access validates and returns
    broken = CliState(settings=None, pending_error=RuntimeError("boom"), diagnostic=True)
    with pytest.raises(typer.Exit):
        _ = broken.settings


def test_diagnostic_commands_are_the_six_of_seven() -> None:
    assert {"doctor", "paths", "guard", "verify", "schema", "fixtures"} == DIAGNOSTIC_COMMANDS
    assert "inventory" not in DIAGNOSTIC_COMMANDS


# ---------------------------------------------------------------------------
# 3. Unknown MWH_* environment variables (CFG-1)
# ---------------------------------------------------------------------------


def test_unknown_env_keys_names_only_and_settings_still_construct(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert config.unknown_env_keys() == []
    monkeypatch.setenv("MWH_DATA_ROOTT", "SECRET-VALUE-zq7")
    monkeypatch.setenv("mwh_data_roott_2", "x")  # case-insensitive prefix match
    keys = config.unknown_env_keys()
    # Windows normalises env names to upper case; compare case-insensitively for any host
    assert sorted(k.upper() for k in keys) == ["MWH_DATA_ROOTT", "MWH_DATA_ROOTT_2"]
    s = Settings()  # the stray variable is ignored, not fatal (unlike a .env line)
    assert s.data_root == config.DEFAULT_DATA_ROOT
    res = doctor.check_settings(config.load_settings())
    assert res.status == "warn"
    assert "MWH_DATA_ROOTT" in res.detail and res.value["unknown_env"] == keys
    assert "SECRET-VALUE-zq7" not in res.detail and "SECRET-VALUE-zq7" not in json.dumps(res.value)


def test_cli_prints_one_stderr_line_for_unknown_env_vars() -> None:
    env = {**os.environ, "MWH_DATA_ROOTT": "SECRET-VALUE-zq7"}
    env.pop("MWH_DATA_ROOT", None)
    proc = subprocess.run(
        [sys.executable, "-m", "mimicwarehouse.cli", "verify", "--list"],
        cwd=WORKSPACE,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=300,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "MWH_DATA_ROOTT" in proc.stderr  # one warning line, on stderr
    assert "SECRET-VALUE-zq7" not in proc.stdout + proc.stderr  # names only, never values


# ---------------------------------------------------------------------------
# 4. Per-tier lake roots + 18 layout keys (ARCH-3, FXT-10, ARCH-9)
# ---------------------------------------------------------------------------


def test_layout_gains_the_three_lake_keys(workspace: Path, tmp_path: Path) -> None:
    s = config.load_settings(data_root=tmp_path / "root")
    assert len(LAYOUT_KEYS) == 18
    assert s.layout["lake_fixture"] == tmp_path / "root" / "lake" / "fixture"
    assert s.layout["lake_demo"] == tmp_path / "root" / "lake" / "demo"
    assert s.layout["lake_rejects"] == tmp_path / "root" / "lake" / "rejects"


def test_lake_and_rejects_roots_per_tier(workspace: Path, tmp_path: Path) -> None:
    s = config.load_settings(data_root=tmp_path / "root")
    lake = s.layout["lake"]
    assert s.lake_root("dev") == lake and s.lake_root("full") == lake
    assert s.lake_root("fixture") == s.layout["lake_fixture"]
    assert s.lake_root("demo") == s.layout["lake_demo"]
    # synthetic rejects stay inside the synthetic lake roots; credentialed ones in lake/rejects
    assert s.rejects_root("fixture") == s.layout["lake_fixture"] / "rejects"
    assert s.rejects_root("demo") == s.layout["lake_demo"] / "rejects"
    assert s.rejects_root("dev") == s.layout["lake_rejects"]
    assert s.rejects_root("full") == s.layout["lake_rejects"]
    # the free-space guard is per tier: 1 GB for fixture, min_free_gb elsewhere
    assert s.min_free_gb_for("fixture") == 1.0
    for tier in ("demo", "dev", "full"):
        assert s.min_free_gb_for(tier) == float(s.min_free_gb)
    with pytest.raises(ValueError):
        s.lake_root("nope")
    # catalog_path is unchanged: warehouse/<tier>.duckdb for all four tiers
    for tier in ("fixture", "demo", "dev", "full"):
        assert s.catalog_path(tier) == s.layout["warehouse"] / f"{tier}.duckdb"


def test_assert_not_credentialed_lake_refuses_synthetic_tiers(
    workspace: Path, tmp_path: Path
) -> None:
    s = config.load_settings(data_root=tmp_path / "root")
    for tier in ("fixture", "demo"):
        with pytest.raises(UnsafeLocationError, match="credentialed"):
            config.assert_not_credentialed_lake(tier, s.layout["lake"], s)
        config.assert_not_credentialed_lake(tier, s.lake_root(tier), s)  # fine
    config.assert_not_credentialed_lake("dev", s.layout["lake"], s)  # dev/full: no-op
    with pytest.raises(ValueError):
        config.assert_not_credentialed_lake("nope", s.layout["lake"], s)


def test_paths_json_lists_18_keys(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MWH_DATA_ROOT", str(workspace.parent / "mimicdata"))
    config.get_settings.cache_clear()
    result = runner.invoke(app, ["paths", "--json"])
    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert [row["key"] for row in report["layout"]] == list(LAYOUT_KEYS)
    assert len(report["layout"]) == 18
    assert result.stdout.endswith("\n") and "\r\r\n" not in result.stdout


# ---------------------------------------------------------------------------
# 5. DuckDB temp-dir policy (CFG-3, CFG-2)
# ---------------------------------------------------------------------------


def test_check_temp_dir_three_states(workspace: Path, tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    s = config.load_settings(data_root=root)
    # parent missing → warn
    res = doctor.check_temp_dir(s)
    assert res.status == "warn" and "parent" in res.detail
    assert res.value["exists"] is False and res.value["parent_exists"] is False
    # parent exists (DuckDB creates the leaf) → pass
    (root / "tmp").mkdir()
    res = doctor.check_temp_dir(s)
    assert res.status == "pass" and "parent exists" in res.detail
    assert res.value["parent_exists"] is True and res.value["exists"] is False
    # exists → pass
    (root / "tmp" / "duckdb").mkdir()
    res = doctor.check_temp_dir(s)
    assert res.status == "pass" and "exists on the data-root volume" in res.detail


def test_duckdb_settings_does_not_mkdir(workspace: Path, tmp_path: Path) -> None:
    s = config.load_settings(data_root=tmp_path / "root")
    s.duckdb_settings("build")
    assert not (tmp_path / "root").exists()  # pure config — no side effects (CFG-3)


def test_open_connection_ensures_the_temp_dir(ed_settings: Settings) -> None:
    temp_dir = ed_settings.layout["tmp_duckdb"]
    assert not temp_dir.exists()
    con = inventory.open_connection(ed_settings)
    try:
        assert temp_dir.is_dir()
    finally:
        con.close()


def test_build_fixture_catalog_ensures_the_temp_dir(ed_settings: Settings) -> None:
    from mimicwarehouse.fixtures.catalog import build_fixture_catalog

    temp_dir = ed_settings.layout["tmp_duckdb"]
    assert not temp_dir.exists()
    # root passed explicitly: the workspace fixture monkeypatches workspace_root to a temp dir
    con = build_fixture_catalog(WORKSPACE / "tests" / "fixtures", settings=ed_settings)
    try:
        assert temp_dir.is_dir()
    finally:
        con.close()


# ---------------------------------------------------------------------------
# 6. --data-root propagation into the verify child (CFG-4)
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Clean MWH_* environment without touching workspace_root (verify needs the real
    roadmap + test modules)."""
    for key in list(os.environ):
        if key.upper().startswith("MWH_"):
            monkeypatch.delenv(key, raising=False)
    config.configure()
    yield
    config.configure()


@pytest.fixture
def fake_pytest_run(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    seen: dict[str, Any] = {"calls": []}

    def _run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        seen["calls"].append((list(argv), kwargs))
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(verify.subprocess, "run", _run)
    return seen


def test_data_root_reaches_the_pytest_child_env(
    cli_env: None, fake_pytest_run: dict[str, Any], tmp_path: Path
) -> None:
    root = tmp_path / "verifyroot"
    result = runner.invoke(app, ["--data-root", str(root), "verify", "EP-2"])
    assert result.exit_code == 0, result.output
    ((_argv, kwargs),) = fake_pytest_run["calls"]
    env = kwargs["env"]
    assert env is not None and env["MWH_DATA_ROOT"] == str(config._abspath(root))
    assert os.environ.get("MWH_DATA_ROOT") != str(config._abspath(root))  # never mutated


def test_no_override_means_no_env_override(cli_env: None, fake_pytest_run: dict[str, Any]) -> None:
    result = runner.invoke(app, ["verify", "EP-2"])
    assert result.exit_code == 0, result.output
    ((_argv, kwargs),) = fake_pytest_run["calls"]
    assert kwargs["env"] is None  # inherit the parent environment untouched


# ---------------------------------------------------------------------------
# 7. Doctor: deny_coverage + git version (GOV-3)
# ---------------------------------------------------------------------------


def _write_settings_json(repo: Path, deny: list[str]) -> None:
    p = repo / ".claude" / "settings.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"permissions": {"deny": deny}}), encoding="utf-8")


def test_claude_deny_prefixes_extracts_drive_letter_rules(tmp_path: Path) -> None:
    _write_settings_json(
        tmp_path,
        [
            "Read(//C:/mimicdata/**)",
            "Grep(C:/mimicdata/**)",
            "Glob(D:/other root/**)",
            "Read(**/*.csv)",  # extension glob — not a prefix
            "Read(./source material/x/**)",  # relative — not a drive-letter prefix
            "Bash(cat *mimicdata*)",  # command rule — no prefix
        ],
    )
    prefixes = doctor.claude_deny_prefixes(tmp_path / ".claude" / "settings.json")
    assert prefixes == ["C:/mimicdata", "D:/other root"]
    assert doctor.claude_deny_prefixes(tmp_path / "absent.json") is None


def test_check_deny_coverage_states(tmp_path: Path) -> None:
    _write_settings_json(tmp_path, ["Read(//C:/mimicdata/**)"])
    covered = doctor.check_deny_coverage(Path(r"C:\mimicdata\lake"), tmp_path)
    assert covered.status == "pass" and covered.value["covered"] is True
    exact = doctor.check_deny_coverage(Path(r"C:\mimicdata"), tmp_path)
    assert exact.status == "pass"
    moved = doctor.check_deny_coverage(Path(r"C:\relocated"), tmp_path)
    assert moved.status == "warn" and moved.value["covered"] is False
    assert "GOV-3" in moved.detail
    # a sibling with the prefix as a name prefix is NOT covered (C:\mimicdata2)
    sibling = doctor.check_deny_coverage(Path(r"C:\mimicdata2"), tmp_path)
    assert sibling.status == "warn"
    missing = doctor.check_deny_coverage(Path(r"C:\mimicdata"), tmp_path / "norepo")
    assert missing.status == "info" and missing.value["covered"] is None


def test_doctor_has_15_checks_with_deny_coverage_after_antivirus() -> None:
    ids = list(doctor.CHECK_IDS)
    assert len(ids) == 15
    assert ids.index("deny_coverage") == ids.index("antivirus") + 1


def test_longpaths_row_carries_the_git_version(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor, "IS_WINDOWS", True)
    monkeypatch.setattr(doctor, "_longpaths_registry", lambda: 1)
    monkeypatch.setattr(doctor, "_git_longpaths", lambda repo: "true")
    monkeypatch.setattr(doctor, "_git_version", lambda: "git version 0.0.0-test")
    res = doctor.check_longpaths(None)
    assert res.status == "pass"
    assert "git version 0.0.0-test" in res.detail
    assert res.value["git_version"] == "git version 0.0.0-test"


def test_deny_coverage_passes_against_the_real_repo_defaults(workspace: Path) -> None:
    """The shipped .claude/settings.json covers the default C:\\mimicdata root."""
    repo = config.repo_root()
    assert repo is not None
    res = doctor.check_deny_coverage(config.DEFAULT_DATA_ROOT, repo)
    assert res.status == "pass", res.detail


# ---------------------------------------------------------------------------
# 8. Inventory: no-op resume, header refresh, lookups, --no-resume, docs (INV-1/2/4)
# ---------------------------------------------------------------------------


def test_noop_resume_keeps_the_job_and_version_block(
    ed_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(inventory, "_git_sha", lambda: "a" * 40)
    first = build_inventory(ed_settings, datasets=[ED], quiet=True)
    assert len(first.processed) == 6 and first.ok
    snap1 = load_raw_manifest(ed_settings).snapshot
    assert snap1["git_sha"] == "a" * 40

    monkeypatch.setattr(inventory, "_git_sha", lambda: "b" * 40)
    second = build_inventory(ed_settings, datasets=[ED], quiet=True)
    assert second.processed == [] and len(second.skipped) == 6 and second.refreshed == []
    snap2 = load_raw_manifest(ed_settings).snapshot
    # the job block is untouched (EP-16's recipe reads it) …
    for key in ("started", "finished", "last_file", "pid", "hostname", "options"):
        assert snap2[key] == snap1[key], key
    # … and so are the versions (git_sha would have changed otherwise)
    for key in ("duckdb_version", "python_version", "git_sha", "mimic_code_sha", "contract_hash"):
        assert snap2[key] == snap1[key], key
    # but the invocation is still recorded in the runs history
    assert len(snap2["runs"]) == len(snap1["runs"]) + 1
    assert snap2["runs"][-1]["processed"] == 0 and snap2["runs"][-1]["skipped"] == 6

    # a real change repopulates the job block from this invocation
    _write_ed_source(ed_settings.source_root, rows=4)
    third = build_inventory(ed_settings, datasets=[ED], quiet=True)
    assert len(third.processed) == 6
    snap3 = load_raw_manifest(ed_settings).snapshot
    assert snap3["git_sha"] == "b" * 40 and snap3["pid"] == os.getpid()


def test_header_refresh_on_contract_change_without_file_io(ed_settings: Settings) -> None:
    build_inventory(ed_settings, datasets=[ED], quiet=True)
    manifest = load_raw_manifest(ed_settings)
    contract = load_contract()
    t = contract.table("mimiciv_ed.vitalsign")
    rel = rel_path_for(t)
    rec = manifest.records[rel]
    # simulate a record written under an older contract: stale header verdict, same header list
    stale = rec.model_copy(
        update={"header_matches_contract": False, "missing_columns": ["ghost_column"]}
    )
    write_dataset_manifest(
        manifest.root, ED, [stale if r.rel_path == rel else r for r in manifest.by_dataset(ED)]
    )
    result = build_inventory(ed_settings, datasets=[ED], quiet=True)
    assert result.processed == [] and result.refreshed == [rel]
    fixed = load_raw_manifest(ed_settings).records[rel]
    assert fixed.header_matches_contract and fixed.missing_columns == []
    assert fixed.sha256 == rec.sha256 and fixed.recorded_at == rec.recorded_at
    # the snapshot id excludes the header, so the refresh never changes it
    assert compute_snapshot_id([stale], files_expected=1) == compute_snapshot_id(
        [fixed], files_expected=1
    )
    # a second run is a clean no-op again
    again = build_inventory(ed_settings, datasets=[ED], quiet=True)
    assert again.refreshed == [] and again.processed == []


def test_refresh_header_status_unit() -> None:
    contract = load_contract()
    t = contract.table("mimiciv_hosp.d_labitems")
    rec = inventory.FileRecord(
        dataset=t.dataset,
        dataset_dir=inventory.dataset_dir(t.dataset),
        module="hosp",
        schema_name=t.schema_name,
        table=t.name,
        rel_path=rel_path_for(t),
        bytes=10,
        mtime="2026-01-01T00:00:00+00:00",
        mtime_ns=1,
        sha256="a" * 64,
        header=list(t.column_names),
        header_matches_contract=False,  # stale verdict
        missing_columns=["ghost"],
        seconds_hash=0.1,
        seconds_rows=0.0,
        recorded_at="2026-01-01T00:00:00+00:00",
    )
    fixed = refresh_header_status(rec, t)
    assert fixed is not None and fixed.header_matches_contract
    assert refresh_header_status(fixed, t) is None  # already current → no rewrite


def test_rel_path_for_and_for_table(ed_settings: Settings) -> None:
    contract = load_contract()
    t = contract.table("mimiciv_ed.edstays")
    assert rel_path_for(t) == f"{inventory.DATASET_DIRS[ED]}/{t.csv_path}"
    build_inventory(ed_settings, datasets=[ED], quiet=True)
    manifest = load_raw_manifest(ed_settings)
    rec = manifest.for_table(t)
    assert rec is not None and rec.table == "edstays"
    assert manifest.for_table(contract.table("mimiciv_hosp.patients")) is None  # not built


def test_cli_accepts_no_resume_as_advertised(ed_settings: Settings) -> None:
    res = runner.invoke(app, ["inventory", "build", "--quiet", "--dataset", ED])
    assert res.exit_code == 0, res.output
    res = runner.invoke(app, ["inventory", "build", "--quiet", "--dataset", ED, "--no-resume"])
    assert res.exit_code == 0, res.output
    snap = load_raw_manifest(ed_settings).snapshot
    assert snap["runs"][-1]["options"]["force"] is True
    assert snap["runs"][-1]["processed"] == 6  # everything recomputed


def test_docs_generated_line_uses_the_snapshot_timestamp(
    ed_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    build_inventory(ed_settings, datasets=[ED], quiet=True)
    manifest = load_raw_manifest(ed_settings)
    contract = load_contract()
    recon = inventory.reconcile(manifest, contract)
    monkeypatch.setattr(inventory, "_now", lambda: "9999-01-01T00:00:00+00:00")
    text = render_docs(manifest, recon, contract)
    assert f"- **Generated:** {manifest.snapshot['finished']}" in text
    assert "9999-01-01" not in text  # wall clock never enters → a no-op reconcile is git-clean
    assert text == render_docs(manifest, recon, contract)


def test_show_json_ends_with_a_newline(ed_settings: Settings) -> None:
    res = runner.invoke(app, ["inventory", "show", "--json"])
    assert res.exit_code == 0, res.output
    assert res.stdout.endswith("\n")
    json.loads(res.stdout)


# ---------------------------------------------------------------------------
# 9. Docs stay in step
# ---------------------------------------------------------------------------


def test_workspace_readme_reflects_the_new_contracts() -> None:
    readme = (WORKSPACE / "README.md").read_text(encoding="utf-8")
    assert "deny_coverage" in readme
    assert "15 host checks" in readme
    assert "lake/fixture" in readme
