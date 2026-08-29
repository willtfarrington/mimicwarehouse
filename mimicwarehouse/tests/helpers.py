"""Shared test helpers (EP-168, retro VT-7). Importable module, **not** a pytest plugin:
test modules ``import helpers`` (``tests/`` is on ``sys.path`` because it has a ``conftest.py``
and no ``__init__.py``) and wrap what they need in their own fixtures.

Kept deliberately small - the setups these replace were duplicated across the ``tests/ep``
modules (CliRunner + ``COLUMNS=200``, a throw-away ``MWH_DATA_ROOT`` with a cleared ``MWH_*``
environment, fresh-interpreter subprocess runs). Migration is opportunistic: an older module is
only moved onto these when it is being edited anyway (a wholesale rewrite would churn every
``test_ep*.py`` at once, the exact coupling EP-168 removes).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from mimicwarehouse import config

#: ``mimicwarehouse/`` (the uv project) and the git checkout above it.
WORKSPACE = Path(__file__).resolve().parents[1]
REPO_ROOT = WORKSPACE.parent


def cli_runner() -> CliRunner:
    """A CliRunner whose invocations see ``COLUMNS=200`` so rich tables never wrap mid-cell."""
    return CliRunner(env={"COLUMNS": "200"})


def tmp_data_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, name: str = "root") -> Path:
    """Point ``MWH_DATA_ROOT`` at a throw-away directory (safe: local fixed volume), clearing
    every other ``MWH_*`` variable and the ``PYTEST_*`` test knobs first, and rebuild the
    settings cache. Call from a yield-fixture and call ``config.configure()`` again after the
    test so the cache is rebuilt from the restored environment::

        @pytest.fixture
        def data_root(monkeypatch, tmp_path):
            yield helpers.tmp_data_root(monkeypatch, tmp_path)
            config.configure()
    """
    for key in list(os.environ):
        if key.upper().startswith("MWH_"):
            monkeypatch.delenv(key, raising=False)
    for key in ("PYTEST_TIER", "PYTEST_DEMO"):
        monkeypatch.delenv(key, raising=False)
    root = tmp_path / name
    root.mkdir(exist_ok=True)
    monkeypatch.setenv("MWH_DATA_ROOT", str(root))
    config.configure()
    return root


def fresh_interpreter(
    argv: list[str], *, cwd: Path = WORKSPACE, timeout: int = 600
) -> subprocess.CompletedProcess[str]:
    """Run ``sys.executable`` with ``argv`` in a fresh interpreter (captured, UTF-8, no
    check) - the shape every end-to-end CLI / import-budget test uses."""
    return subprocess.run(
        [sys.executable, *argv],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=timeout,
    )
