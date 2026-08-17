"""EP-4 — Governance enforcement: pre-commit + ``mwh guard`` acceptance tests.

Every refusal is proven in a throw-away repository ``git init``-ed under ``tmp_path``; the
selfcheck and the wiring tests run against the real repository (read-only: strings are
probed through ``git check-ignore``, no file is created there). Only synthetic values appear
here — no data, no identifiers. Every band id below is **built at runtime** from the module
constants (a literal 8-digit band number in this file would trip the guard on this very
file, which the last test proves is not the case).
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from mimicwarehouse import config, guard
from mimicwarehouse.cli import DIAGNOSTIC_COMMANDS, app
from mimicwarehouse.guard import (
    HADM_BAND,
    MAX_FILE_BYTES,
    STAY_BAND,
    SUBJECT_BAND,
    Violation,
    scan,
    scan_staged,
    scan_tracked,
    selfcheck,
    selfcheck_ok,
)

pytestmark = pytest.mark.ep_4

runner = CliRunner()
WORKSPACE = Path(__file__).resolve().parents[2]  # mimicwarehouse/ (the uv project)
REPO = WORKSPACE.parent

# Runtime-built band ids (never literals): one per band, plus a compact date in the hadm band.
SUBJECT_ID = SUBJECT_BAND[0] + 7
HADM_ID = HADM_BAND[0] + 12_345
STAY_ID = STAY_BAND[1] - 3
COMPACT_DATE = HADM_BAND[0] + 260_817  # "YYYYMMDD" of 2026-08-17 — not exempt
FIXTURE_ID_A = 90_000_001
FIXTURE_ID_B = 90_000_002
HEX_SHA = "1234567890abcdef1234567890abcdef12345678"  # 40 hex chars, digits bordered by \w
BYTE_COUNT = "123456789012"  # 12 digits


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True, encoding="utf-8"
    )
    return proc.stdout


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def _rules(violations: list[Violation]) -> set[str]:
    return {v.rule for v in violations}


def _notebook(executed: bool) -> str:
    cell = {
        "cell_type": "code",
        "metadata": {},
        "source": ["print(1)"],
        "execution_count": 1 if executed else None,
        "outputs": (
            [{"output_type": "stream", "name": "stdout", "text": ["1\n"]}] if executed else []
        ),
    }
    return json.dumps({"cells": [cell], "metadata": {}, "nbformat": 4, "nbformat_minor": 5})


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A throw-away git repository (no .gitignore: the guard alone must refuse)."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "guard-test@example.invalid")
    _git(root, "config", "user.name", "guard test")
    _git(root, "config", "commit.gpgsign", "false")
    return root


@pytest.fixture
def cli_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Clean settings environment for CLI invocations (guard never reads the data root)."""
    for key in list(os.environ):
        if key.upper().startswith("MWH_"):
            monkeypatch.delenv(key, raising=False)
    config.configure()
    yield
    config.configure()


# ---------------------------------------------------------------------------
# G1 — data-shaped extensions
# ---------------------------------------------------------------------------


def test_g1_data_shaped_outside_fixtures_refused_and_synthetic_fixture_passes(repo: Path) -> None:
    root_csv = _write(repo / "x.csv", "a,b\n1,2\n")
    fixture_csv = _write(
        repo / "mimicwarehouse/tests/fixtures/hosp/patients.csv",
        f"subject_id,gender\n{FIXTURE_ID_A},F\n{FIXTURE_ID_B},M\n",
    )
    model = _write(repo / "tests/fixtures/model.pt", "not really a tensor")
    fixture_model = _write(repo / "mimicwarehouse/tests/fixtures/model.pt", "x")

    assert _rules(scan([root_csv], repo)) == {"G1"}
    assert scan([fixture_csv], repo) == []
    assert _rules(scan([model], repo)) == {"G1"}  # not under mimicwarehouse/tests/fixtures/
    assert _rules(scan([fixture_model], repo)) == {"G1"}  # allow-list is .csv/.parquet/… only

    detail = scan([root_csv], repo)[0].detail
    assert ".csv" in detail and "1,2" not in detail


@pytest.mark.parametrize(
    "name",
    ["a.csv.gz", "a.parquet", "a.duckdb", "a.duckdb.wal", "a.wal", "a.duckdb.new", "a.jsonl",
     "a.feather", "a.arrow", "a.pkl", "a.joblib", "a.skops", "a.pt", "a.safetensors", "a.npy",
     "a.npz", "a.h5"],
)  # fmt: skip
def test_g1_every_listed_extension_is_refused(repo: Path, name: str) -> None:
    path = _write(repo / "sub" / name, "x")
    assert "G1" in _rules(scan([path], repo))


def test_g1_multi_suffix_reports_the_longest_extension(repo: Path) -> None:
    path = _write(repo / "cat.duckdb.wal", "x")
    (v,) = [v for v in scan([path], repo) if v.rule == "G1"]
    assert ".duckdb.wal" in v.detail


# ---------------------------------------------------------------------------
# G2 — source material
# ---------------------------------------------------------------------------


def test_g2_source_material_only_markdown_passes(repo: Path) -> None:
    data = _write(repo / "source material/x/y.txt", "anything")
    readme = _write(repo / "source material/README.md", "# datasets\n")
    assert _rules(scan([data], repo)) == {"G2"}
    assert scan([readme], repo) == []


def test_g2_never_opens_source_material_content(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A band id inside a source-material file is refused by name only — content is not read."""
    data = _write(repo / "source material/x/rows.csv", f"subject_id\n{SUBJECT_ID}\n")

    def boom(self: guard.Entry) -> bytes | None:  # pragma: no cover - must not be called
        raise AssertionError("guard opened a file under source material/")

    monkeypatch.setattr(guard.Entry, "content", boom)
    assert _rules(scan([data], repo)) == {"G1", "G2"}


# ---------------------------------------------------------------------------
# G3 — notebook outputs
# ---------------------------------------------------------------------------


def test_g3_notebook_with_outputs_refused_cleared_passes(repo: Path) -> None:
    nb = _write(repo / "notebooks/scratch.ipynb", _notebook(executed=True))
    assert _rules(scan([nb], repo)) == {"G3"}
    _write(nb, _notebook(executed=False))
    assert scan([nb], repo) == []
    _write(nb, "{not json")
    assert _rules(scan([nb], repo)) == {"G3"}


def test_g3_marimo_cache_refused(repo: Path) -> None:
    cached = _write(repo / "notebooks/study/__marimo__/session.json", "{}")
    assert _rules(scan([cached], repo)) == {"G3"}


# ---------------------------------------------------------------------------
# G4 — real-id band
# ---------------------------------------------------------------------------


def test_g4_band_id_in_markdown_is_refused_and_masked(repo: Path) -> None:
    md = _write(repo / "docs/note.md", f"# note\n\nsubject_id={SUBJECT_ID}\n")
    violations = scan([md], repo)
    assert _rules(violations) == {"G4"}
    (v,) = violations
    assert v.line == 3
    assert "subject_id" in v.detail
    assert str(SUBJECT_ID) not in v.detail
    assert str(SUBJECT_ID)[0] + "*" * 7 in v.detail
    rendered = json.dumps(v.as_dict())
    assert str(SUBJECT_ID) not in rendered


def test_g4_pragma_exempts_the_line_only(repo: Path) -> None:
    md = _write(
        repo / "docs/note.md", f"example subject_id={SUBJECT_ID}  <!-- mwh-guard: allow -->\n"
    )
    assert scan([md], repo) == []
    md = _write(
        repo / "docs/note.md",
        f"example subject_id={SUBJECT_ID}  <!-- mwh-guard: allow -->\nhadm_id={HADM_ID}\n",
    )
    (v,) = scan([md], repo)
    assert (v.rule, v.line) == ("G4", 2) and "hadm_id" in v.detail


def test_g4_every_band_is_recognised_and_compact_dates_are_not_exempt(repo: Path) -> None:
    md = _write(repo / "x.md", f"s {SUBJECT_ID}\nh {HADM_ID}\nst {STAY_ID}\ndate {COMPACT_DATE}\n")
    violations = scan([md], repo)
    assert [(v.rule, v.line) for v in violations] == [("G4", 1), ("G4", 2), ("G4", 3), ("G4", 4)]
    assert [v.detail.split(" in the ")[1].split(" band")[0] for v in violations] == [
        "subject_id",
        "hadm_id",
        "stay_id",
        "hadm_id",
    ]


def test_g4_non_matches_are_clean(repo: Path) -> None:
    text = "\n".join(
        [
            f"fixture {FIXTURE_ID_A} {FIXTURE_ID_B}",  # ≥ 90 000 000 (D-27)
            f"sha {HEX_SHA}",  # 40-char hex
            f"bytes {BYTE_COUNT}",  # 12 digits
            f"const ({SUBJECT_BAND[0]:_}, {SUBJECT_BAND[1]:_})",  # underscore groups
            f"decimal {SUBJECT_ID}.5 and 0.{HADM_ID}",  # bordered by '.'
            f"ident x{STAY_ID} {STAY_ID}y _{SUBJECT_ID}",  # bordered by \\w
            "iso 2026-08-17 and 1 000 000",
        ]
    )
    md = _write(repo / "x.md", text + "\n")
    assert scan([md], repo) == []


def test_g4_only_text_files_are_scanned(repo: Path) -> None:
    binary = repo / "blob.bin"
    binary.write_bytes(b"\0" + str(SUBJECT_ID).encode())
    lock = _write(repo / "uv.lock", f"x = {SUBJECT_ID}\n")  # .lock is not a text extension
    nul_txt = repo / "with_nul.txt"
    nul_txt.write_bytes(str(SUBJECT_ID).encode() + b"\0")
    latin1 = repo / "latin1.txt"
    latin1.write_bytes(str(SUBJECT_ID).encode() + b" caf\xe9\n")  # not UTF-8 → not text
    assert scan([binary, lock, nul_txt, latin1], repo) == []

    py = _write(repo / "mod.py", f"HADM = {HADM_ID}\n")
    bare = _write(repo / "LICENSE", f"{STAY_ID}\n")  # extensionless → scanned
    dotfile = _write(repo / ".probe", f"{STAY_ID}\n")
    csv = _write(repo / "mimicwarehouse/tests/fixtures/hosp/x.csv", f"subject_id\n{SUBJECT_ID}\n")
    assert _rules(scan([py, bare, dotfile], repo)) == {"G4"}
    assert len(scan([py, bare, dotfile], repo)) == 3
    assert _rules(scan([csv], repo)) == {"G4"}  # allowed extension, but a real-band id


def test_g4_rows_are_capped_per_file(repo: Path) -> None:
    lines = "\n".join(f"row {SUBJECT_ID + i}" for i in range(guard.MAX_G4_ROWS_PER_FILE + 5))
    md = _write(repo / "many.md", lines + "\n")
    violations = scan([md], repo)
    assert len(violations) == guard.MAX_G4_ROWS_PER_FILE + 1
    assert violations[-1].line is None and "5 more line(s)" in violations[-1].detail
    assert all(str(SUBJECT_ID + i) not in v.detail for i in range(30) for v in violations)


# ---------------------------------------------------------------------------
# G5 — oversize
# ---------------------------------------------------------------------------


def test_g5_oversize_refused_and_content_skipped(repo: Path) -> None:
    big = repo / "big.bin"
    big.write_bytes(b"\0" * (21 * 1024 * 1024))
    violations = scan([big], repo)
    assert _rules(violations) == {"G5"}
    assert "MiB" in violations[0].detail
    small = repo / "small.bin"
    small.write_bytes(b"\0" * MAX_FILE_BYTES)  # exactly at the bound → clean
    assert scan([small], repo) == []


# ---------------------------------------------------------------------------
# Staged / tracked scans read the index
# ---------------------------------------------------------------------------


def test_scan_staged_after_git_add_and_after_git_rm_cached(repo: Path) -> None:
    _write(repo / "probe.csv", "a,b\n1,2\n")
    _write(repo / "docs/note.md", f"subject_id={SUBJECT_ID}\n")
    _write(repo / "README.md", "# clean\n")
    _git(repo, "add", "-A")
    violations = scan_staged(repo)
    assert _rules(violations) == {"G1", "G4"}
    assert {v.path for v in violations} == {"probe.csv", "docs/note.md"}
    assert all(str(SUBJECT_ID) not in v.detail for v in violations)

    _git(repo, "rm", "--cached", "-q", "probe.csv", "docs/note.md")
    assert scan_staged(repo) == []


def test_scan_staged_judges_the_index_not_the_working_tree(repo: Path) -> None:
    md = _write(repo / "note.md", f"subject_id={SUBJECT_ID}\n")
    _git(repo, "add", "note.md")
    _write(md, "cleaned in the working tree only\n")  # unstaged edit must not hide the staged id
    assert _rules(scan_staged(repo)) == {"G4"}
    _git(repo, "add", "note.md")
    assert scan_staged(repo) == []


def test_scan_staged_covers_renames_and_modifications_only(repo: Path) -> None:
    _write(repo / "ok.md", "fine\n")
    _write(repo / "bad.txt", f"{HADM_ID}\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "seed")  # (bad.txt now in history; the guard was not run)
    assert scan_staged(repo) == []  # nothing staged
    _git(repo, "rm", "-q", "bad.txt")
    assert scan_staged(repo) == []  # deletions are never violations
    _git(repo, "mv", "ok.md", "renamed.md")
    _write(repo / "renamed.md", f"stay {STAY_ID}\n")
    _git(repo, "add", "-A")
    (v,) = scan_staged(repo)
    assert (v.rule, v.path, v.line) == ("G4", "renamed.md", 1)


def test_scan_tracked_index_and_revision(repo: Path) -> None:
    _write(repo / "a.md", "clean\n")
    _write(repo / "b.md", f"{SUBJECT_ID}\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "one")
    assert {v.path for v in scan_tracked(repo)} == {"b.md"}
    assert {v.path for v in scan_tracked(repo, "HEAD")} == {"b.md"}
    _write(repo / "b.md", "clean now\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "two")
    assert scan_tracked(repo) == []
    assert {v.path for v in scan_tracked(repo, "HEAD~1")} == {"b.md"}  # the per-commit primitive


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_paths_exit_codes_and_masked_output(
    repo: Path, cli_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(repo)
    bad = _write(repo / "docs/note.md", f"subject_id={SUBJECT_ID}\n")
    good = _write(repo / "README.md", "# clean\n")

    result = runner.invoke(app, ["guard", "docs/note.md", "README.md"])
    assert result.exit_code == 1, result.output
    assert "G4" in result.output and "docs/note.md" in result.output
    assert str(SUBJECT_ID) not in result.output
    assert str(SUBJECT_ID)[0] + "*******" in result.output

    result = runner.invoke(app, ["guard", str(good)])
    assert result.exit_code == 0, result.output
    assert "clean" in result.output

    result = runner.invoke(app, ["guard", str(repo / "docs")])  # a directory is walked
    assert result.exit_code == 1
    assert bad.name in result.output


def test_cli_json_shape(repo: Path, cli_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(repo)
    _write(repo / "x.csv", "a\n1\n")
    _write(repo / "n.md", f"{STAY_ID}\n")
    result = runner.invoke(app, ["guard", "--json", "x.csv", "n.md"])
    assert result.exit_code == 1, result.output
    report = json.loads(result.output)
    assert set(report) == {"mode", "repo_root", "files_scanned", "violations", "ok"}
    assert report["mode"] == "paths" and report["files_scanned"] == 2 and report["ok"] is False
    assert [v["rule"] for v in report["violations"]] == ["G1", "G4"]
    assert set(report["violations"][0]) == {"rule", "title", "path", "line", "detail"}
    assert report["violations"][1]["line"] == 1
    assert str(STAY_ID) not in result.output


def test_cli_staged_is_the_default_and_all_tracked(
    repo: Path, cli_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(repo)
    _write(repo / "probe.csv", "a\n1\n")
    _write(repo / "note.md", f"{HADM_ID}\n")
    result = runner.invoke(app, ["guard"])
    assert result.exit_code == 0 and "0 file(s) scanned, staged" in result.output

    _git(repo, "add", "-A")
    result = runner.invoke(app, ["guard"])
    assert result.exit_code == 1
    assert "G1" in result.output and "G4" in result.output
    assert str(HADM_ID) not in result.output
    result = runner.invoke(app, ["guard", "--staged", "--json"])
    assert json.loads(result.output)["mode"] == "staged"

    _git(repo, "rm", "--cached", "-q", "probe.csv", "note.md")
    assert runner.invoke(app, ["guard", "--staged"]).exit_code == 0

    _write(repo / "ok.md", "fine\n")
    _git(repo, "add", "ok.md")
    _git(repo, "commit", "-q", "-m", "seed")
    result = runner.invoke(app, ["guard", "--all-tracked", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.output)["files_scanned"] == 1


def test_cli_usage_errors_exit_2(
    repo: Path, cli_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(repo)
    assert runner.invoke(app, ["guard", "--staged", "--all-tracked"]).exit_code == 2
    assert runner.invoke(app, ["guard", "--selfcheck", "nope.md"]).exit_code == 2
    result = runner.invoke(app, ["guard", "does-not-exist.md"])
    assert result.exit_code == 2 and "no such path" in result.output
    outside = repo.parent / "not-a-repo"
    outside.mkdir()
    monkeypatch.chdir(outside)
    result = runner.invoke(app, ["guard", "--staged"])
    assert result.exit_code == 2, result.output


def test_guard_runs_even_when_the_data_root_is_unsafe(
    repo: Path, cli_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The hook must never be blocked by a mis-set MWH_DATA_ROOT (guard is diagnostic)."""
    assert "guard" in DIAGNOSTIC_COMMANDS
    monkeypatch.chdir(repo)
    _write(repo / "ok.md", "fine\n")
    result = runner.invoke(app, ["--data-root", r"G:\mimicdata", "guard", "ok.md"])
    assert result.exit_code == 0, result.output


# ---------------------------------------------------------------------------
# The real repository: selfcheck, wiring, docs, and this brief's own files
# ---------------------------------------------------------------------------


def test_selfcheck_passes_on_the_real_repo(cli_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    results = selfcheck(REPO)
    failed = [r for r in results if not r.ok and r.level == "fail"]
    assert not failed, [r.as_dict() for r in failed]
    assert selfcheck_ok(results)
    ids = {r.id for r in results}
    assert {"gitattributes:x.csv", "gitattributes:x.parquet", "gitattributes:x.duckdb"} <= ids
    assert "gitignore:source material/mimic-iv-3.1/hosp/patients.csv" in ids
    assert "pre-commit-config" in ids and "hook-installed" in ids

    monkeypatch.chdir(REPO)
    result = runner.invoke(app, ["guard", "--selfcheck", "--json"])
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["mode"] == "selfcheck" and report["ok"] is True
    assert runner.invoke(app, ["guard", "--selfcheck"]).exit_code == 0


def test_selfcheck_fails_in_a_bare_repo(repo: Path) -> None:
    results = selfcheck(repo)  # no .gitignore / .gitattributes / config → fails
    assert not selfcheck_ok(results)


def test_pre_commit_config_is_wired_as_the_brief_says() -> None:
    cfg = yaml.safe_load((REPO / ".pre-commit-config.yaml").read_text("utf-8"))
    assert "default_language_version" not in cfg
    local, hooks = cfg["repos"]
    assert local["repo"] == "local"
    ids = [h["id"] for h in local["hooks"]]
    assert ids == ["mwh-guard", "ruff-check", "ruff-format"]
    mwh = local["hooks"][0]
    assert mwh["entry"] == "uv run --project mimicwarehouse --group dev mwh guard --staged"
    assert mwh["language"] == "system"
    assert mwh["pass_filenames"] is False and mwh["always_run"] is True
    assert all(h["language"] == "system" for h in local["hooks"])
    assert local["hooks"][1]["types_or"] == ["python"]
    assert hooks["repo"] == "https://github.com/pre-commit/pre-commit-hooks"
    assert hooks["rev"].startswith("v")
    assert [h["id"] for h in hooks["hooks"]] == [
        "check-added-large-files",
        "check-merge-conflict",
        "check-yaml",
        "check-toml",
        "check-json",
        "end-of-file-fixer",
        "trailing-whitespace",
        "detect-private-key",
    ]
    assert hooks["hooks"][0]["args"] == ["--maxkb=20000"]


def test_poe_task_and_readme_mention_the_guard() -> None:
    import tomllib

    pyproject = tomllib.loads((WORKSPACE / "pyproject.toml").read_text("utf-8"))
    assert pyproject["tool"]["poe"]["tasks"]["guard"] == "mwh guard --staged"
    readme = (WORKSPACE / "README.md").read_text("utf-8")
    assert "pre-commit install" in readme and "mwh-guard: allow" in readme


def test_this_briefs_files_carry_no_band_ids() -> None:
    """The guard proves the brief's own promise: no plain band literal in what EP-4 wrote."""
    files = [
        Path(__file__),
        WORKSPACE / "src/mimicwarehouse/guard.py",
        WORKSPACE / "src/mimicwarehouse/cli.py",
        WORKSPACE / "pyproject.toml",
        WORKSPACE / "README.md",
        REPO / ".pre-commit-config.yaml",
        REPO / "roadmap/EP-4-guard-precommit.md",
    ]
    assert scan(files, REPO) == []
