"""EP-168 — retro D: tier readiness fixtures + ``needs=``, demo opt-in marker, de-coupled
probes, ``tests/helpers``.

Fixture tier only: the marker/fixture semantics are exercised with ``pytester`` (nested pytest
sessions over a copy of ``tests/conftest.py``, a throw-away data root and an **empty** source
root so the real ``source material`` never leaks into a nested run). The one dev-marked probe
(``needs="raw"``) asserts only that the raw dataset *directory exists* and reads nothing
(GOVERNANCE §4). No test prints a row.
"""

from __future__ import annotations

import json
import tomllib
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest

import helpers
from mimicwarehouse import config as config_mod
from mimicwarehouse import verify

pytestmark = pytest.mark.ep_168

WORKSPACE = Path(__file__).resolve().parents[2]
CONFTEST = WORKSPACE / "tests" / "conftest.py"


# ---------------------------------------------------------------------------
# Nested-session scaffolding (EP-12 pattern + an isolated source root)
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[SimpleNamespace]:
    """Throw-away ``MWH_DATA_ROOT`` (via :func:`helpers.tmp_data_root`, which also clears
    ``MWH_*`` and the ``PYTEST_*`` knobs) plus an empty ``MWH_SOURCE_ROOT`` so readiness
    probes are fully under the test's control."""
    data_root = helpers.tmp_data_root(monkeypatch, tmp_path)
    source_root = tmp_path / "srcroot"
    source_root.mkdir()
    monkeypatch.setenv("MWH_SOURCE_ROOT", str(source_root))
    config_mod.configure()
    yield SimpleNamespace(data_root=data_root, source_root=source_root)
    config_mod.configure()


@pytest.fixture
def nested(pytester: pytest.Pytester, isolated: SimpleNamespace) -> pytest.Pytester:
    pytester.makeini("[pytest]\naddopts = -ra --strict-markers\n")
    pytester.makeconftest(CONFTEST.read_text(encoding="utf-8"))
    return pytester


NEEDS_RAW = """
import pytest

@pytest.mark.tier("dev", needs="raw")
def test_needs_raw():
    assert True
"""

NEEDS_LAKE = """
import pytest

@pytest.mark.tier("dev", needs="lake")
def test_needs_lake():
    assert True
"""

DEMO_TESTS = """
import pytest

def test_plain():
    assert True

@pytest.mark.demo
def test_demo():
    assert True
"""


# ---------------------------------------------------------------------------
# 1. needs= in the marker form (retro VT-1/FC-1)
# ---------------------------------------------------------------------------


def test_needs_raw_skips_then_passes(nested: pytest.Pytester, isolated: SimpleNamespace) -> None:
    nested.makepyfile(test_raw=NEEDS_RAW)
    result = nested.runpytest("-p", "no:cacheprovider", "--tier", "dev")
    result.assert_outcomes(skipped=1)
    result.stdout.fnmatch_lines(["*SKIP*dev tier: needs raw*mimic-iv-3.1*"])
    # deselected (not skipped) below its tier, exactly as before
    result = nested.runpytest("-p", "no:cacheprovider")
    result.assert_outcomes(deselected=1)
    # once the dataset directory exists the same test runs — no catalog required
    (isolated.source_root / "mimic-iv-3.1").mkdir()
    result = nested.runpytest("-p", "no:cacheprovider", "--tier", "dev")
    result.assert_outcomes(passed=1)


def test_needs_lake_skips_then_passes(nested: pytest.Pytester, isolated: SimpleNamespace) -> None:
    nested.makepyfile(test_lake=NEEDS_LAKE)
    result = nested.runpytest("-p", "no:cacheprovider", "--tier", "dev")
    result.assert_outcomes(skipped=1)
    result.stdout.fnmatch_lines(["*SKIP*dev tier: needs lake*status.json*"])
    manifests = isolated.data_root / "lake" / "manifests"
    manifests.mkdir(parents=True)
    (manifests / "status.json").write_text('{"steps": {}}\n', encoding="utf-8")
    result = nested.runpytest("-p", "no:cacheprovider", "--tier", "dev")
    result.assert_outcomes(passed=1)


def test_explicit_needs_catalog_keeps_ep12_semantics(
    nested: pytest.Pytester, isolated: SimpleNamespace
) -> None:
    nested.makepyfile(
        test_cat=(
            "import pytest\n"
            "@pytest.mark.tier('dev')\n"
            "def test_default():\n    assert True\n"
            "@pytest.mark.tier('dev', needs='catalog')\n"
            "def test_explicit():\n    assert True\n"
        )
    )
    result = nested.runpytest("-p", "no:cacheprovider", "--tier", "dev")
    result.assert_outcomes(skipped=2)
    result.stdout.fnmatch_lines(["*SKIP*dev tier: needs catalog*dev.duckdb*"])
    (isolated.data_root / "warehouse").mkdir()
    (isolated.data_root / "warehouse" / "dev.duckdb").write_bytes(b"")
    result = nested.runpytest("-p", "no:cacheprovider", "--tier", "dev")
    result.assert_outcomes(passed=2)


def test_bad_needs_and_bad_kwargs_are_usage_errors(nested: pytest.Pytester) -> None:
    nested.makepyfile(
        test_bad=(
            "import pytest\n@pytest.mark.tier('dev', needs='gpu')\ndef test_x():\n    assert True\n"
        )
    )
    result = nested.runpytest("-p", "no:cacheprovider", "--tier", "dev", "test_bad.py")
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*unknown needs 'gpu'*"])
    nested.makepyfile(
        test_kw=(
            "import pytest\n@pytest.mark.tier('dev', wants='raw')\ndef test_x():\n    assert True\n"
        )
    )
    result = nested.runpytest("-p", "no:cacheprovider", "test_kw.py")
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*only needs= is accepted*"])


# ---------------------------------------------------------------------------
# 2. Readiness fixtures (dev_catalog / full_catalog / raw_root / dev_ready / item_tier)
# ---------------------------------------------------------------------------


def test_catalog_fixtures_skip_then_pass(
    nested: pytest.Pytester, isolated: SimpleNamespace
) -> None:
    nested.makepyfile(
        test_fx=(
            "def test_dev(dev_catalog):\n    assert dev_catalog.name == 'dev.duckdb'\n"
            "def test_full(full_catalog):\n    assert full_catalog.name == 'full.duckdb'\n"
        )
    )
    result = nested.runpytest("-p", "no:cacheprovider")
    result.assert_outcomes(skipped=2)
    result.stdout.fnmatch_lines(["*needs catalog; not found*dev.duckdb*"])
    warehouse = isolated.data_root / "warehouse"
    warehouse.mkdir()
    (warehouse / "dev.duckdb").write_bytes(b"")
    (warehouse / "full.duckdb").write_bytes(b"")
    result = nested.runpytest("-p", "no:cacheprovider")
    result.assert_outcomes(passed=2)


def test_dev_ready_factory_gates_on_status_json(
    nested: pytest.Pytester, isolated: SimpleNamespace
) -> None:
    nested.makepyfile(
        test_ready=(
            "import pytest\n"
            "@pytest.mark.tier('dev', needs='lake')\n"
            "def test_step(dev_ready):\n"
            "    entry = dev_ready('stage_hosp')\n"
            "    assert entry['dev_ready'] is True\n"
        )
    )
    manifests = isolated.data_root / "lake" / "manifests"
    manifests.mkdir(parents=True)
    status = manifests / "status.json"
    status.write_text(json.dumps({"steps": {"other": {"dev_ready": True}}}), encoding="utf-8")
    result = nested.runpytest("-p", "no:cacheprovider", "--tier", "dev")
    result.assert_outcomes(skipped=1)
    result.stdout.fnmatch_lines(["*'stage_hosp' is not dev_ready*"])
    status.write_text(json.dumps({"steps": {"stage_hosp": {"dev_ready": True}}}), encoding="utf-8")
    result = nested.runpytest("-p", "no:cacheprovider", "--tier", "dev")
    result.assert_outcomes(passed=1)


def test_item_tier_defaults_to_fixture(item_tier: str) -> None:
    assert item_tier == "fixture"


@pytest.mark.tier("dev", needs="raw")
def test_raw_root_is_a_directory(raw_root: Path, item_tier: str) -> None:
    """The EP-17-shaped dev probe: existence only, no file is opened (GOVERNANCE §4). Skipped
    (never failed) on machines without the raw dataset; on this machine it must pass under
    ``--tier dev`` / ``--tier full``."""
    assert item_tier == "dev"
    assert raw_root.is_dir()


# ---------------------------------------------------------------------------
# 3. Demo opt-in marker (retro ARCH-7/FC-2)
# ---------------------------------------------------------------------------


def test_demo_deselected_by_default(nested: pytest.Pytester) -> None:
    nested.makepyfile(test_demo=DEMO_TESTS)
    result = nested.runpytest("-p", "no:cacheprovider")
    result.assert_outcomes(passed=1, deselected=1)
    result.stdout.fnmatch_lines(["*demo tests off*"])


def test_with_demo_skips_until_catalog_exists(
    nested: pytest.Pytester, isolated: SimpleNamespace
) -> None:
    nested.makepyfile(test_demo=DEMO_TESTS)
    result = nested.runpytest("-p", "no:cacheprovider", "--with-demo")
    result.assert_outcomes(passed=1, skipped=1)
    result.stdout.fnmatch_lines(["*demo tests on*", "*SKIP*demo: needs catalog*demo.duckdb*"])
    (isolated.data_root / "warehouse").mkdir()
    (isolated.data_root / "warehouse" / "demo.duckdb").write_bytes(b"")
    result = nested.runpytest("-p", "no:cacheprovider", "--with-demo")
    result.assert_outcomes(passed=2)


def test_demo_env_fallback_and_bad_values(
    nested: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    nested.makepyfile(test_demo=DEMO_TESTS)
    monkeypatch.setenv("PYTEST_DEMO", "1")
    result = nested.runpytest("-p", "no:cacheprovider")
    result.assert_outcomes(passed=1, skipped=1)
    monkeypatch.setenv("PYTEST_DEMO", "0")
    result = nested.runpytest("-p", "no:cacheprovider")
    result.assert_outcomes(passed=1, deselected=1)
    monkeypatch.setenv("PYTEST_DEMO", "maybe")
    result = nested.runpytest("-p", "no:cacheprovider")
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*PYTEST_DEMO='maybe' is not a demo opt-in*"])


def test_demo_is_orthogonal_to_the_tier_ladder(
    nested: pytest.Pytester, isolated: SimpleNamespace
) -> None:
    """``--tier full`` never opts demo in; ``--with-demo`` never raises the maximum tier.
    ``TIERS`` stays the three-step ladder (demo is a data tier, not a test tier)."""
    nested.makepyfile(
        test_mix=(
            "import pytest\n"
            "@pytest.mark.demo\n"
            "def test_demo():\n    assert True\n"
            "@pytest.mark.tier('dev')\n"
            "def test_dev():\n    assert True\n"
        )
    )
    result = nested.runpytest("-p", "no:cacheprovider", "--tier", "full")
    result.assert_outcomes(skipped=1, deselected=1)  # dev skipped (no catalog), demo deselected
    result = nested.runpytest("-p", "no:cacheprovider", "--with-demo")
    result.assert_outcomes(skipped=1, deselected=1)  # demo skipped (no catalog), dev deselected
    result = nested.runpytest("-p", "no:cacheprovider", "--tier", "demo")
    assert result.ret == pytest.ExitCode.USAGE_ERROR  # unchanged from EP-12


def test_demo_marker_and_option_registered(request: pytest.FixtureRequest) -> None:
    markers = request.config.getini("markers")
    demo_line = next(m for m in markers if m.startswith("demo:"))
    assert "--with-demo" in demo_line and "PYTEST_DEMO" in demo_line
    tier_line = next(m for m in markers if m.startswith("tier(name"))
    assert "needs" in tier_line and "deselected" in tier_line
    assert request.config.getoption("--with-demo") in (False, True)


def test_verify_passes_with_demo_through() -> None:
    """``mwh verify EP-22 -- --with-demo`` reaches pytest untouched (EP-6's ``--``)."""
    assert verify.pytest_argv(22, ["--with-demo"])[-1] == "--with-demo"


# ---------------------------------------------------------------------------
# 4. tests/helpers.py (retro VT-7/VT-8)
# ---------------------------------------------------------------------------


def test_helpers_cli_runner_and_constants() -> None:
    runner = helpers.cli_runner()
    assert runner.env.get("COLUMNS") == "200"
    assert helpers.WORKSPACE == WORKSPACE and WORKSPACE.parent == helpers.REPO_ROOT


def test_helpers_fresh_interpreter() -> None:
    proc = helpers.fresh_interpreter(["-c", "print('ok')"])
    assert proc.returncode == 0 and proc.stdout.strip() == "ok"


def test_helpers_tmp_data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    monkeypatch.setenv("MWH_BOGUS", "x")
    monkeypatch.setenv("PYTEST_DEMO", "1")
    root = tmp_path / "root"
    try:
        got = helpers.tmp_data_root(monkeypatch, tmp_path)
        assert got == root and root.is_dir()
        assert "MWH_BOGUS" not in os.environ and "PYTEST_DEMO" not in os.environ
        assert os.environ["MWH_DATA_ROOT"] == str(root)
        from mimicwarehouse.config import get_settings

        assert get_settings().data_root == root
    finally:
        config_mod.configure()


# ---------------------------------------------------------------------------
# 5. poe tasks + docs
# ---------------------------------------------------------------------------


def test_poe_tasks_and_docs() -> None:
    tasks = tomllib.loads((WORKSPACE / "pyproject.toml").read_text(encoding="utf-8"))["tool"][
        "poe"
    ]["tasks"]
    assert tasks["test"] == "pytest"  # serial by default (D-42: AV heuristics vs process bursts)
    assert tasks["test-fast"] == "pytest -n auto"
    readme = (WORKSPACE / "tests" / "README.md").read_text(encoding="utf-8")
    needles = (
        "dev_catalog", "full_catalog", "raw_root", "dev_ready", "item_tier", "needs=",
        "--with-demo", "PYTEST_DEMO", "helpers.py", "test-fast", "coupling is the bug",
    )  # fmt: skip
    for needle in needles:
        assert needle in readme, needle
    design = (WORKSPACE / "DESIGN.md").read_text(encoding="utf-8")
    assert "EP-168" in design
