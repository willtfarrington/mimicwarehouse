"""EP-33 Workstream B - C1: committed-text hygiene canon (B4), dev-loop gates (B5),
import-budget doctrine (B6) and the guard/doctor findings SGD-3, SGD-4, CLI-3/P01-1.

Fixture tier only; the sweeps read tracked *names* and repository source/doc text - never a
fixture CSV, never the data root. Every band-shaped token is built at runtime from the
guard's band constants (this file is G4-scanned too), and no failure message ever echoes a
matched line.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

import helpers
from mimicwarehouse import config, guard
from mimicwarehouse.guard import HADM_BAND, STAY_BAND, SUBJECT_BAND

pytestmark = pytest.mark.ep_33

WORKSPACE = helpers.WORKSPACE
REPO = helpers.REPO_ROOT
CANON = WORKSPACE / "docs" / "committed-text.md"

# Runtime-built tokens (never literals).
HADM_ID = HADM_BAND[0] + 4_321
STAY_ID = STAY_BAND[1] - 9
SUBJECT_ID = SUBJECT_BAND[0] + 77
COMPACT_DATE = HADM_BAND[0] + 260_901  # "YYYYMMDD" of 2026-09-01 - inside the hadm band
RUN_ID = f"{COMPACT_DATE}T120000-full-abc123"


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def _tracked_paths() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=REPO, capture_output=True, check=True, timeout=120
    ).stdout
    return [p.decode("utf-8", "surrogateescape") for p in out.split(b"\0") if p]


@pytest.fixture
def data_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    yield helpers.tmp_data_root(monkeypatch, tmp_path)
    config.configure()


# ---------------------------------------------------------------------------
# B4 - the hygiene canon and the rules it indexes
# ---------------------------------------------------------------------------


def test_tracer_value_bound_mirrors_the_safe_query_free_text_bound() -> None:
    from mimicwarehouse import safe, tracer

    assert tracer.VALUE_MAX_CHARS == safe.FREE_TEXT_MAX_CHARS == 64


def test_filename_asymmetry_run_id_refused_in_names_legal_inline() -> None:
    name = f"run-{RUN_ID}.md"
    assert [band for band, _ in guard.path_id_hits(name)] == ["hadm_id"]
    masked = guard.path_id_hits(name)[0][1]
    assert masked == str(COMPACT_DATE)[0] + "*" * 7
    # the same token inline in text is glued to the "T" - not an isolated 8-digit run
    assert guard.id_band_hits(f"reproduced from run {RUN_ID} on the dev tier\n".encode()) == []
    # ... whereas a bare compact date in text is still refused (write ISO dates)
    assert [band for _, band, _, _ in guard.id_band_hits(f"{COMPACT_DATE}\n".encode())] == [
        "hadm_id"
    ]


def test_tracked_names_carry_no_band_token_or_compact_date() -> None:
    offenders = [rel for rel in _tracked_paths() if guard.path_id_hits(rel)]
    assert not offenders, f"tracked names with band-shaped tokens: {offenders}"


def test_pragma_lines_that_exempt_a_band_token_carry_a_rationale() -> None:
    from mimicwarehouse.concepts.vendoring import GUARD_PRAGMA

    vendored = GUARD_PRAGMA.strip()
    rationale = re.compile(re.escape(guard.ALLOW_PRAGMA) + r"\s*\([^)]+\)")
    offenders: list[tuple[str, int]] = []
    for rel in _tracked_paths():
        if not rel.startswith(("mimicwarehouse/src/", "mimicwarehouse/docs/")):
            continue
        if not guard.is_text_candidate(rel) or not (REPO / rel).is_file():
            continue  # (a tracked path deleted in the working tree has nothing to sweep)
        text = (REPO / rel).read_text(encoding="utf-8", errors="replace")
        for no, line in enumerate(text.splitlines(), start=1):
            if guard.ALLOW_PRAGMA not in line:
                continue
            exempts = guard.id_band_hits(line.replace(guard.ALLOW_PRAGMA, "").encode())
            if exempts and not (vendored in line or rationale.search(line)):
                offenders.append((rel, no))
    assert not offenders, f"pragma without a rationale at (path, line): {offenders}"


def test_docs_resources_cite_dois_not_pmids() -> None:
    pmid = re.compile(r"PMID\s*[:#]?\s*\d{5,}")
    offenders = [
        (path.name, no)
        for path in sorted((WORKSPACE / "docs" / "resources").glob("*.md"))
        for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if pmid.search(line)
    ]
    assert not offenders, f"bare PMIDs at (file, line): {offenders}"
    reading = (WORKSPACE / "docs" / "resources" / "reading.md").read_text(encoding="utf-8")
    assert "never bare PMIDs" in reading


def test_canon_page_is_ascii_policy_only_and_names_every_enforcer() -> None:
    raw = CANON.read_bytes()
    text = raw.decode("utf-8")
    assert text.isascii(), "docs/committed-text.md must be ASCII"
    for symbol in (
        "fmt_int",
        "console_safe",
        "ID_TOKEN",
        "PATH_ID_TOKEN",
        "TEXT_EXTENSIONS",
        "VALUE_MAX_CHARS",
        "FREE_TEXT_MAX_CHARS",
        "ALLOW_PRAGMA",
        "GUARD_PRAGMA",
        "DOI",
    ):
        assert symbol in text, symbol
    for rule in range(1, 7):
        assert f"### {rule}. " in text
    assert guard.id_band_hits(raw) == []  # shapes only, never a band integer
    assert not re.search(r"(?<![\w.])20\d{6}(?![\w.])", text)  # no compact dates either


def test_render_markdown_over_a_synthetic_ledger_is_ascii(data_root: Path) -> None:
    from mimicwarehouse.dag import benchmarks

    host = benchmarks.HostInfo(cpu=8, ram_gb=32.0)
    for step, rows in (("stage.mimiciv_hosp.d_items", 4_000), ("stage.mimiciv_icu.x", 12_345_678)):
        benchmarks.append(
            benchmarks.BenchmarkLine(
                ts="2026-09-01T00:00:01+00:00",
                build_id="synthetic-build",
                tier="fixture",
                step=step,
                kind="stage",
                wall_s=1.5,
                rows=rows,
                bytes_in=2_000_000,
                bytes_out=500_000,
                files=1,
                peak_rss_mb=120.0,
                duckdb_version="1.5.5",
                host=host,
                ok=True,
            )
        )
    md = benchmarks.render_markdown(benchmarks.summarize(tier="fixture"))
    assert md.isascii() and "12,345,678" in md and md.endswith("\n")


# ---------------------------------------------------------------------------
# SGD-3 - G4 scans notebook source and the script/markup types
# ---------------------------------------------------------------------------


def _notebook(source: str) -> str:
    cell = {
        "cell_type": "code",
        "execution_count": None,
        "metadata": {},
        "outputs": [],
        "source": [source],
    }
    return json.dumps({"cells": [cell], "metadata": {}, "nbformat": 4, "nbformat_minor": 5})


def test_g4_flags_a_band_id_in_cleared_notebook_source(tmp_path: Path) -> None:
    nb = _write(tmp_path / "nb" / "explore.ipynb", _notebook(f"hadm = {HADM_ID}\n"))
    violations = guard.scan([nb], tmp_path)
    assert {v.rule for v in violations} == {"G4"}  # outputs are clear, so no G3
    assert "hadm_id" in violations[0].detail and str(HADM_ID) not in violations[0].detail
    clean = _write(tmp_path / "nb" / "clean.ipynb", _notebook("print(1)\n"))
    assert guard.scan([clean], tmp_path) == []


@pytest.mark.parametrize("ext", [".sh", ".psm1", ".bat", ".cmd", ".rst", ".xml"])
def test_g4_scans_the_ep33_script_and_markup_types(tmp_path: Path, ext: str) -> None:
    assert ext in guard.TEXT_EXTENSIONS
    path = _write(tmp_path / f"probe{ext}", f"x {STAY_ID}\n")
    assert [v.rule for v in guard.scan([path], tmp_path)] == ["G4"]


# ---------------------------------------------------------------------------
# SGD-4 - selfcheck resolves the registered hook interpreter and script paths
# ---------------------------------------------------------------------------


def _hook_repo(tmp_path: Path, command: str) -> Path:
    root = tmp_path / "clone"
    _write(root / guard.PRETOOL_HOOK_SCRIPT, "# hook stub\n")
    settings = {"hooks": {"PreToolUse": [{"hooks": [{"type": "command", "command": command}]}]}}
    _write(root / ".claude" / "settings.json", json.dumps(settings))
    return root


def test_pretool_hook_selfcheck_fails_on_a_dead_interpreter_path(tmp_path: Path) -> None:
    dead = (tmp_path / "gone" / ".venv" / "Scripts" / "python.exe").as_posix()
    root = _hook_repo(tmp_path, f'"{dead}" "{tmp_path / "clone" / guard.PRETOOL_HOOK_SCRIPT}"')
    row = guard._pretool_hook_check(root)
    assert not row.ok and row.level == "fail"
    assert "interpreter path does not exist" in row.detail and "fails open" in row.detail


def test_pretool_hook_selfcheck_passes_with_live_paths_and_project_dir_form(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = (tmp_path / "clone" / guard.PRETOOL_HOOK_SCRIPT).as_posix()
    root = _hook_repo(tmp_path, f'"{sys.executable}" "{script}"')
    assert guard._pretool_hook_check(root).detail == "registered"
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    root = _hook_repo(
        tmp_path, f'"{sys.executable}" "$CLAUDE_PROJECT_DIR/{guard.PRETOOL_HOOK_SCRIPT}"'
    )
    assert guard._pretool_hook_check(root).ok


def test_pretool_hook_selfcheck_fails_when_script_is_missing_or_outside_the_repo(
    tmp_path: Path,
) -> None:
    root = _hook_repo(
        tmp_path, f'"{sys.executable}" "{tmp_path / "nowhere" / "claude_pretool_guard.py"}"'
    )
    row = guard._pretool_hook_check(root)
    assert not row.ok and "script path does not exist" in row.detail
    elsewhere = _write(tmp_path / "other" / "claude_pretool_guard.py", "# stray copy\n")
    root = _hook_repo(tmp_path, f'"{sys.executable}" "{elsewhere.as_posix()}"')
    row = guard._pretool_hook_check(root)
    assert not row.ok and "outside this repository" in row.detail
    bare = tmp_path / "bare"
    bare.mkdir()
    assert "NOT registered" in guard._pretool_hook_check(bare).detail


def test_pretool_hook_selfcheck_passes_on_this_clone() -> None:
    assert guard._pretool_hook_check(REPO).detail == "registered"


# ---------------------------------------------------------------------------
# console conventions in guard / doctor (emit_json newlines, fail on stderr)
# ---------------------------------------------------------------------------


def test_guard_json_uses_plain_newlines_and_usage_errors_go_to_stderr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mimicwarehouse.cli import app

    for key in ("MWH_DATA_ROOT", "MWH_ROLE"):
        monkeypatch.delenv(key, raising=False)
    config.configure()
    runner = helpers.cli_runner()
    monkeypatch.chdir(REPO)
    result = runner.invoke(app, ["guard", "--selfcheck", "--json"])
    assert result.exit_code == 0, result.output
    # emit_json writes plain "\n" (the platform text layer may make it "\r\n"); the old
    # os.linesep writer produced "\r\r\n" on Windows.
    assert b"\r\r\n" not in result.stdout_bytes and result.stdout.endswith("}\n")
    assert json.loads(result.stdout)["ok"] is True
    result = runner.invoke(app, ["guard", "--staged", "--all-tracked"])
    assert result.exit_code == 2
    assert "choose one of" in result.stderr and "choose one of" not in result.stdout
    config.configure()


# ---------------------------------------------------------------------------
# B5 - dev-loop gates
# ---------------------------------------------------------------------------


def test_poe_check_has_the_format_gate_and_the_root_include_exists() -> None:
    tasks = tomllib.loads((WORKSPACE / "pyproject.toml").read_text(encoding="utf-8"))["tool"][
        "poe"
    ]["tasks"]
    assert tasks["fmt-check"] == "ruff format --check ."
    assert "fmt-check" in tasks["check"]
    assert tasks["check"].index("fmt-check") < tasks["check"].index("typecheck")
    root = tomllib.loads((REPO / "poe_tasks.toml").read_text(encoding="utf-8"))
    assert root["include"] == [{"path": "mimicwarehouse/pyproject.toml", "cwd": "mimicwarehouse"}]
    readme = (WORKSPACE / "tests" / "README.md").read_text(encoding="utf-8")
    assert "fmt-check" in readme and "poe_tasks.toml" in readme


# ---------------------------------------------------------------------------
# B6 - the import-budget helper names its offenders
# ---------------------------------------------------------------------------


def test_import_budget_helper_names_the_offender() -> None:
    with pytest.raises(AssertionError, match=r"loaded json at start-up"):
        helpers.assert_import_budget(module="json", forbid=("json",))
    with pytest.raises(AssertionError, match=r"eagerly loaded json"):
        helpers.assert_import_budget(module="json", forbid=(), lazy=("json",))
    helpers.assert_import_budget(module="json", forbid=("polars",))
