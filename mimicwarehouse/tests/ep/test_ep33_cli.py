"""EP-33 Workstream B8 — the paradigm sweep over the CLI modules (agent C2).

Every ``mwh`` command module owned by the sweep (``cli``, ``inventory``, ``canary``,
``verify``, ``schema.cli``, ``fixtures.cli``, ``config.paths_command``, ``demo``) now uses
the :mod:`mimicwarehouse.console` canon: ``fail`` (``mwh <cmd>: …`` on **stderr**, exit
``EXIT_USAGE``), ``emit_json`` (raw integers, plain ``\\n``) and, for ``inventory``,
``configure_progress_logging``. Carried findings: CLI-2 (pydantic-settings ``SettingsError``
takes the pending-error path), CLI-5 (``CliState.data_root`` deleted), INV-15
(``open_connection`` is a thin alias over ``engine.open_duckdb``; self-opened connections
are closed), plus the ``fmt_bytes_mb`` / ``fmt_int`` None convention.

Fixture tier only: every data root is a ``tmp_path`` (``helpers.tmp_data_root``) and the
source root is an *empty* temp directory, so nothing under ``source material/`` is ever
stat-ed, let alone read. Synthetic fixtures use ids >= 90 000 000.
"""

from __future__ import annotations

import json
import logging
from collections import namedtuple
from pathlib import Path
from typing import Any

import pytest

import helpers
from mimicwarehouse import config, console, engine, inventory, verify
from mimicwarehouse.cli import CliState, app
from mimicwarehouse.config import DriveInfo
from mimicwarehouse.inventory import build_inventory, fmt_bytes_mb, fmt_int

pytestmark = pytest.mark.ep_33

runner = helpers.cli_runner()

DiskUsage = namedtuple("DiskUsage", "total used free")


def _fake_disk_usage(free_gb: float, total_gb: float = 950.0):
    def fake(path):
        total = int(total_gb * config.GB)
        free = int(free_gb * config.GB)
        return DiskUsage(total=total, used=total - free, free=free)

    return fake


@pytest.fixture
def data_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Throw-away ``MWH_DATA_ROOT`` + an **empty** ``MWH_SOURCE_ROOT`` (nothing real is ever
    stat-ed) with plenty of fake free space."""
    root = helpers.tmp_data_root(monkeypatch, tmp_path)
    source = tmp_path / "src"
    source.mkdir()
    monkeypatch.setenv("MWH_SOURCE_ROOT", str(source))
    monkeypatch.setattr(config.shutil, "disk_usage", _fake_disk_usage(500.0))
    config.configure()
    yield root
    config.configure()


def _assert_ints(obj: Any, keys: set[str]) -> None:
    """Every occurrence of ``keys`` anywhere in ``obj`` is a raw int (never a str)."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in keys and v is not None:
                assert isinstance(v, int) and not isinstance(v, bool), (k, v)
            _assert_ints(v, keys)
    elif isinstance(obj, list):
        for v in obj:
            _assert_ints(v, keys)


# ---------------------------------------------------------------------------
# CLI-2: pydantic-settings SettingsError takes the pending-error path
# ---------------------------------------------------------------------------


def test_unparsable_complex_env_value_keeps_help_working_and_commands_refusing(
    data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MWH_DEV_BUCKETS", "[1,2")  # not JSON -> SettingsError, not ValidationError
    config.configure()
    assert runner.invoke(app, ["inventory", "--help"]).exit_code == 0
    assert runner.invoke(app, ["inventory", "build", "--help"]).exit_code == 0
    assert runner.invoke(app, ["--version"]).exit_code == 0
    build = runner.invoke(app, ["inventory", "build"])
    assert build.exit_code == console.EXIT_USAGE, build.output
    assert build.stdout == ""
    assert "mwh:" in build.stderr and "dev_buckets" in build.stderr
    # a diagnostic command still needs a Settings instance: same message, same stream
    paths = runner.invoke(app, ["paths"])
    assert paths.exit_code == console.EXIT_USAGE and "dev_buckets" in paths.stderr


def test_clistate_has_no_data_root_property() -> None:
    """Carried CLI-5: the unused ``CliState.data_root`` property is gone."""
    assert "data_root" not in vars(CliState)
    state = CliState(settings=None, pending_error=RuntimeError("boom"), diagnostic=True)
    assert state.data_root_override is None


# ---------------------------------------------------------------------------
# console.fail: one stderr path per owned command, `mwh <cmd>:` prefix, exit 2
# ---------------------------------------------------------------------------


def _make_roadmap(tmp_path: Path) -> Path:
    rm = tmp_path / "roadmap"
    rm.mkdir()
    (rm / "README.md").write_text(
        "# Roadmap\n\n## Phase 0 - full briefs\n\n"
        "| EP | Brief | Size | Depends on | Core/Stretch | Done |\n"
        "|---|---|---|---|---|---|\n"
        "| EP-1 | [Alpha](EP-1-alpha.md) | S | - | core | ☐ |\n"
        "| EP-2 | [Beta](EP-2-beta.md) | M | EP-1 | core | ☐ |\n",
        encoding="utf-8",
    )
    (rm / "EP-1-alpha.md").write_text(
        "# EP-1 — Alpha\n\n**Size:** S · **Tier:** fixture · **Core/Stretch:** core · "
        "**Depends on:** - · **Blocks:** EP-2 (Beta)\n",
        encoding="utf-8",
    )
    (rm / "EP-2-beta.md").write_text(
        "# EP-2 — Beta\n\n**Size:** M · **Tier:** n/a · **Core/Stretch:** core · "
        "**Depends on:** EP-1 (Alpha) · **Blocks:** -\n",
        encoding="utf-8",
    )
    return rm


@pytest.mark.parametrize(
    ("argv", "prefix"),
    [
        (["schema", "show", "mimiciv_hosp.nope"], "mwh schema:"),
        (["schema", "list", "--schema", "nope"], "mwh schema:"),
        (["schema", "ddl"], "mwh schema:"),
        (["verify", "EP-x"], "mwh verify:"),
        (["verify"], "mwh verify:"),
        (["inventory", "build", "--dataset", "mimic-v"], "mwh inventory:"),
        (["canary", "write", "--large-mb", str(inventory.FILES_EXPECTED * 10_000)], "mwh canary:"),
        (["demo", "status"], "mwh demo status:"),
    ],
)
def test_fail_paths_write_to_stderr_with_the_command_prefix(
    data_root: Path, argv: list[str], prefix: str
) -> None:
    res = runner.invoke(app, argv)
    assert res.exit_code == console.EXIT_USAGE, res.output
    assert res.stdout == ""
    assert res.stderr.startswith(prefix), res.stderr


def test_fixtures_and_paths_and_callback_refusals_go_to_stderr(
    data_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    res = runner.invoke(
        app, ["fixtures", "build", "--out", str(tmp_path / "fx"), "--seed", str(10_000_000 + 5)]
    )
    assert res.exit_code == console.EXIT_USAGE and res.stdout == ""
    assert res.stderr.startswith("mwh fixtures:")

    monkeypatch.setattr(config.shutil, "disk_usage", _fake_disk_usage(50.0))
    res = runner.invoke(app, ["paths", "--create"])
    assert res.exit_code == console.EXIT_USAGE and res.stdout == ""
    assert res.stderr.startswith("mwh paths: refused:") and "nothing was created" in res.stderr
    assert not (data_root / "lake").exists()

    monkeypatch.setattr(
        config, "drive_info", lambda path: DriveInfo("G", "DRIVE_FIXED", "", "NTFS")
    )
    res = runner.invoke(app, ["--data-root", r"G:\nowhere", "inventory", "build"])
    assert res.exit_code == console.EXIT_USAGE and res.stdout == ""
    assert res.stderr.startswith("mwh:") and "D-29" in res.stderr
    # `paths` (diagnostic) prints its table on stdout and the verdict on stderr
    shown = runner.invoke(app, ["--data-root", r"G:\nowhere", "paths"])
    assert shown.exit_code == console.EXIT_USAGE
    assert "lake_manifests" in shown.stdout and "mwh paths: unsafe data root:" in shown.stderr


def test_verify_exit_codes_are_the_console_constants() -> None:
    assert verify.EXIT_USAGE is console.EXIT_USAGE
    assert verify.EXIT_OK is console.EXIT_OK
    assert "EXIT_OK" in verify.__all__ and "EXIT_USAGE" in verify.__all__


# ---------------------------------------------------------------------------
# emit_json: every owned --json surface parses and keeps raw integers
# ---------------------------------------------------------------------------


def _json(argv: list[str], *, expect: int = 0) -> Any:
    res = runner.invoke(app, argv)
    assert res.exit_code == expect, res.output
    assert res.stderr == ""
    assert res.stdout.endswith("\n") and "\r" not in res.stdout
    return json.loads(res.stdout)


def test_json_surfaces_parse_and_keep_ints(
    data_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = _json(["paths", "--json"])
    assert report["unsafe"] is None
    _assert_ints(report, {"min_gb"})

    monkeypatch.setattr(verify, "roadmap_dir", lambda: _make_roadmap(tmp_path))
    monkeypatch.setattr(verify, "repo_root", lambda: tmp_path)
    payload = _json(["verify", "--roadmap", "--json"])
    assert payload["ok"] is True and payload["counts"]["rows"] == 2
    _assert_ints(payload["counts"], {"rows", "briefs", "done", "errors", "warnings"})
    _assert_ints(payload["rows"], {"ep", "line_no"})
    _assert_ints(payload, {"exit_code"})

    listed = _json(["schema", "list", "--schema", "mimiciv_ed", "--json"])
    assert listed["tables"] and all(isinstance(t["columns"], int) for t in listed["tables"])
    shown = _json(["schema", "show", "mimiciv_hosp.patients", "--json"])
    assert shown["name"] == "patients"
    checked = _json(["schema", "check", "--json"])
    assert checked["drift"] == []
    _assert_ints(checked, {"tables"})

    fx = _json(
        [
            "fixtures",
            "build",
            "--out",
            str(tmp_path / "fx"),
            "--subjects",
            "15",
            "--seed",
            "7",
            "--json",
        ]
    )
    _assert_ints(fx, {"seed", "n_subjects", "rows", "bytes", "total_bytes", "total_rows"})
    assert fx["total_rows"] > 0

    show = _json(["inventory", "show", "--json"])
    assert show["files_done"] == 0 and len(show["pending"]) == inventory.FILES_EXPECTED
    _assert_ints(show, {"files_done", "files_expected"})
    recon = _json(["inventory", "reconcile", "--no-docs", "--json"])
    assert recon["summary"]["pending"] > 0 and recon["docs"] is None
    _assert_ints(recon, {"expected", "observed", "delta", "files_done", "files_expected"})
    _assert_ints(recon["summary"], {"match", "mismatch", "no-expectation", "pending"})

    canary = _json(["canary", "write", "--small", "1", "--large-mb", "1", "--json"])
    assert canary["survived"] is True
    _assert_ints(canary, {"files", "bytes", "small_files", "large_mb"})


# ---------------------------------------------------------------------------
# inventory: the engine alias, the None conventions, progress logging
# ---------------------------------------------------------------------------


def test_open_connection_is_a_thin_alias_over_the_engine(
    data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[Any, dict[str, Any]]] = []
    sentinel = object()

    def fake_open_duckdb(profile, **kwargs):
        seen.append((profile, kwargs))
        return sentinel

    monkeypatch.setattr(engine, "open_duckdb", fake_open_duckdb)
    settings = config.get_settings()
    assert inventory.open_connection(settings) is sentinel
    assert seen == [("build", {"settings": settings})]
    assert inventory.open_connection() is sentinel
    assert seen[-1] == ("build", {"settings": None})


def test_inventory_file_closes_a_self_opened_connection(
    data_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Carried INV-15: a bare ``inventory_file`` call (no ``connection``) no longer leaks."""
    from mimicwarehouse.schema import load_contract

    closed: list[bool] = []
    real_open = inventory.open_connection

    class Spy:  # DuckDB connection attributes are read-only, so proxy the two calls used
        def __init__(self, con: Any) -> None:
            self._con = con

        def execute(self, *args: Any, **kwargs: Any) -> Any:
            return self._con.execute(*args, **kwargs)

        def close(self) -> None:
            closed.append(True)
            self._con.close()

    monkeypatch.setattr(
        inventory, "open_connection", lambda settings=None: Spy(real_open(settings))
    )
    t = load_contract().table("mimiciv_hosp.d_labitems")
    csv = tmp_path / "d_labitems.csv"
    csv.write_text(",".join(t.column_names) + "\n" + "1,x,y,z\n", encoding="utf-8")
    rec = inventory.inventory_file(csv, t, rel_path="mimic-iv-3.1/hosp/d_labitems.csv")
    assert rec.rows == 1 and closed == [True]


def test_fmt_helpers_none_convention() -> None:
    assert fmt_bytes_mb(None) == "-"
    assert fmt_bytes_mb(1_234_567) == "1.2 MB"
    assert fmt_bytes_mb(0) == "0.0 MB"
    assert fmt_int(None) == "-"
    assert fmt_int(12_345_678) == "12,345,678"
    from mimicwarehouse.catalog import dictionary

    assert not hasattr(dictionary, "_fmt_mb")  # the private twin is gone (B8)


def test_build_inventory_progress_logging_is_scoped(
    data_root: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    logger = logging.getLogger("mimicwarehouse")
    before = list(logger.handlers)
    log = tmp_path / "logs" / "inventory.log"  # parent created by the build
    result = build_inventory(config.get_settings(), quiet=True, log_path=log)
    assert result.processed == [] and result.missing  # empty source root: a no-op build
    text = log.read_text(encoding="utf-8")
    assert "INFO inventory build: 0 to process" in text
    assert "missing: mimic-iv-3.1/hosp/patients.csv" in text
    assert "inventory build finished" in text
    assert capsys.readouterr().out == ""  # --quiet: nothing on stdout
    assert logger.handlers == before  # the file handler was removed and closed again

    build_inventory(config.get_settings(), quiet=False)
    out = capsys.readouterr().out
    assert "inventory build: 0 to process" in out and "inventory build finished" in out
    assert logger.handlers == before
