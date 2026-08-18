"""EP-6 — ``mwh verify EP-n`` + ``roadmap_check`` acceptance tests.

No data, no tier: ``verify`` is exercised with ``subprocess.run`` mocked (argument
construction) and once end-to-end (EP-2's marker set in a fresh interpreter); ``roadmap_check``
runs against the real ``../roadmap/`` (read-only, must report zero errors) and against crafted
roadmaps under ``tmp_path`` where every fault class is planted on purpose. Git lookups for the
crafted roadmaps go through a fake ``_run_git`` so no real hash is needed; the fake hashes are
hex strings (never 8-digit runs — the EP-4 guard scans this file too).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from mimicwarehouse import config, verify
from mimicwarehouse.cli import DIAGNOSTIC_COMMANDS, app
from mimicwarehouse.verify import (
    CHECKS,
    DOCS_ONLY_MESSAGE,
    EXIT_USAGE,
    PYTEST_NO_TESTS_COLLECTED,
    Report,
    VerifyError,
    brief_files,
    ep_test_module,
    parse_brief,
    parse_roadmap_table,
    pytest_argv,
    resolve_ep,
    roadmap_check,
)

pytestmark = pytest.mark.ep_6

runner = CliRunner()
WORKSPACE = Path(__file__).resolve().parents[2]  # mimicwarehouse/ (the uv project)
REPO = WORKSPACE.parent
ROADMAP = REPO / "roadmap"

# Fake commit hashes for crafted roadmaps: hex, 7 chars, letters included (guard-safe).
GOOD_A = "abc1234"
GOOD_B = "def5678"
GOOD_C = "0c0ffee"
BAD_HASH = "badbad0"
FAKE_SUBJECTS = {
    GOOD_A: "feat(mimicwarehouse): thing (EP-1)",
    GOOD_B: "docs: no ep tag in this subject",
    GOOD_C: "feat(mimicwarehouse): other (EP-2)",
}


# ---------------------------------------------------------------------------
# helpers / fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Clean settings environment for CliRunner invocations."""
    for key in list(os.environ):
        if key.upper().startswith("MWH_"):
            monkeypatch.delenv(key, raising=False)
    config.configure()
    yield
    config.configure()


@pytest.fixture
def fake_git(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[list[str]]]:
    """Route ``verify._run_git`` to an in-memory commit table; records the calls."""
    calls: dict[str, list[list[str]]] = {"calls": []}

    def _fake(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
        calls["calls"].append(list(args))
        if args[:2] == ("cat-file", "-e"):
            sha = args[2].removesuffix("^{commit}")
            return subprocess.CompletedProcess(args, 0 if sha in FAKE_SUBJECTS else 128, "", "")
        if args[:1] == ("log",):
            sha = args[-1]
            if sha in FAKE_SUBJECTS:
                return subprocess.CompletedProcess(args, 0, FAKE_SUBJECTS[sha] + "\n", "")
            return subprocess.CompletedProcess(args, 128, "", "fatal: bad object")
        raise AssertionError(f"unexpected git call {args}")

    monkeypatch.setattr(verify, "_run_git", _fake)
    return calls


def _header(
    size: str = "S",
    tier: str = "fixture",
    core: str = "core",
    depends: str = "—",
    blocks: str = "—",
) -> str:
    return (
        f"**Size:** {size} · **Tier:** {tier} · **Core/Stretch:** {core} · "
        f"**Depends on:** {depends} · **Blocks:** {blocks}"
    )


def _brief(
    ep: int,
    title: str,
    header: str,
    *,
    charter_by: int | None = None,
    h1_ep: int | None = None,
) -> str:
    lines = [f"# EP-{h1_ep if h1_ep is not None else ep} — {title}", "", header, ""]
    if charter_by is not None:
        lines += [
            "> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch "
            "to be upgraded to a",
            f"> full brief by EP-{charter_by} (Re-plan Px) before execution.",
            "",
        ]
    lines += ["## Context", "", "Synthetic brief for the EP-6 tests.", ""]
    return "\n".join(lines)


def _row(ep: int, title: str, link: str, size: str, depends: str, core: str, done: str) -> str:
    return f"| EP-{ep} | [{title}]({link}) | {size} | {depends} | {core} | {done} |"


TABLE_HEAD = [
    "| # | Brief | Size | Depends on | Core | Done |",
    "|---|-------|------|-----------|------|------|",
]


def _readme(sections: dict[str, list[str]]) -> str:
    out = ["# crafted roadmap", ""]
    for heading, rows in sections.items():
        out += [f"## {heading}", "", *TABLE_HEAD, *rows, ""]
    return "\n".join(out)


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _make_roadmap(tmp_path: Path, name: str = "roadmap") -> Path:
    """A consistent two-row roadmap (EP-1 ☑, EP-2 ☐) that passes every check."""
    rm = tmp_path / name
    rm.mkdir()
    _write(rm / "EP-1-alpha.md", _brief(1, "Alpha", _header(size="S", tier="fixture")))
    _write(
        rm / "EP-2-beta.md",
        _brief(2, "Beta `code`", _header(size="M", tier="n/a", depends="EP-1 (Alpha)")),
    )
    _write(
        rm / "README.md",
        _readme(
            {
                "Phase P0 — Test (full briefs; planned 2026-08-16)": [
                    _row(1, "Alpha", "EP-1-alpha.md", "S", "—", "core", f"☑ `{GOOD_A}`"),
                    _row(2, "Beta `code`", "EP-2-beta.md", "M", "EP-1", "core", "☐"),
                ]
            }
        ),
    )
    return rm


def _messages(report: Report, check: str | None = None, level: str | None = None) -> list[str]:
    return [
        f.message
        for f in report.findings
        if (check is None or f.check == check) and (level is None or f.level == level)
    ]


# ---------------------------------------------------------------------------
# resolve_ep
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("token", "expected"),
    [("EP-6", 6), ("ep6", 6), ("6", 6), ("EP6", 6), ("Ep-12", 12), (" 7 ", 7), (3, 3), ("0", 0)],
)
def test_resolve_ep_variants(token: str | int, expected: int) -> None:
    assert resolve_ep(token) == expected


@pytest.mark.parametrize("token", ["EP-x", "", "EP-", "x6", "-1", "6a", "EP-1-2", True])
def test_resolve_ep_rejects(token: Any) -> None:
    with pytest.raises(VerifyError):
        resolve_ep(token)


# ---------------------------------------------------------------------------
# verify — argument construction (subprocess mocked)
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_run(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replace ``subprocess.run`` inside verify.py; ``seen["rc"]`` sets the return code."""
    seen: dict[str, Any] = {"rc": 0, "calls": []}

    def _run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        seen["calls"].append((list(argv), kwargs))
        return subprocess.CompletedProcess(argv, seen["rc"])

    monkeypatch.setattr(verify.subprocess, "run", _run)
    return seen


def test_pytest_argv_shape() -> None:
    assert pytest_argv(6) == [
        sys.executable,
        "-m",
        "pytest",
        "-m",
        "ep_6",
        "-p",
        "no:cacheprovider",
    ]
    assert pytest_argv(2, ["-q", "-x"])[-2:] == ["-q", "-x"]


def test_verify_builds_the_pytest_command_and_returns_its_exit_code(
    fake_run: dict[str, Any],
) -> None:
    out: list[str] = []
    assert verify.verify("EP-2", ["-q", "--tier", "dev"], echo=out.append) == 0
    ((argv, kwargs),) = fake_run["calls"]
    assert argv == [
        sys.executable,
        "-m",
        "pytest",
        "-m",
        "ep_2",
        "-p",
        "no:cacheprovider",
        "-q",
        "--tier",
        "dev",
    ]
    assert Path(kwargs["cwd"]) == WORKSPACE
    assert kwargs.get("check") is False
    assert out == []

    fake_run["rc"] = 1
    assert verify.verify(2, echo=out.append) == 1
    fake_run["rc"] = 3
    assert verify.verify("ep2", echo=out.append) == 3


def test_verify_no_tests_collected_becomes_2_with_marker_hint(fake_run: dict[str, Any]) -> None:
    fake_run["rc"] = PYTEST_NO_TESTS_COLLECTED
    out: list[str] = []
    assert verify.verify("EP-2", echo=out.append) == EXIT_USAGE
    assert "ep_2" in out[-1] and "pytestmark" in out[-1]


def test_verify_docs_only_brief_runs_nothing(fake_run: dict[str, Any]) -> None:
    out: list[str] = []
    assert verify.verify("EP-0", echo=out.append) == 0  # EP-0: tier n/a, no test_ep00.py
    assert out == [DOCS_ONLY_MESSAGE]
    assert fake_run["calls"] == []


def test_verify_code_brief_without_test_module_is_2(
    fake_run: dict[str, Any], tmp_path: Path
) -> None:
    rm = _make_roadmap(
        tmp_path
    )  # EP-1 fixture-tier, EP-2 n/a — no test modules under tmp workspace
    ws = tmp_path / "ws"
    (ws / "tests" / "ep").mkdir(parents=True)
    out: list[str] = []
    assert verify.verify(1, workspace=ws, roadmap=rm, echo=out.append) == EXIT_USAGE
    assert "test_ep01.py" in out[-1] and "code brief" in out[-1]
    assert verify.verify(2, workspace=ws, roadmap=rm, echo=out.append) == 0
    assert out[-1] == DOCS_ONLY_MESSAGE
    assert fake_run["calls"] == []
    # n/a tier *with* a test module (EP-5 style) → pytest runs
    _write(ws / "tests" / "ep" / "test_ep02.py", "")
    assert verify.verify(2, workspace=ws, roadmap=rm, echo=out.append) == 0
    assert len(fake_run["calls"]) == 1
    # unknown brief
    assert verify.verify(9, workspace=ws, roadmap=rm, echo=out.append) == EXIT_USAGE
    assert "no brief EP-9" in out[-1]


def test_ep_test_module_uses_two_digit_names() -> None:
    assert ep_test_module(6, WORKSPACE).name == "test_ep06.py"
    assert ep_test_module(123, WORKSPACE).name == "test_ep123.py"
    assert ep_test_module(6, WORKSPACE).is_file()


# ---------------------------------------------------------------------------
# verify — end to end (fresh interpreters)
# ---------------------------------------------------------------------------


def _mwh(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "mimicwarehouse.cli", *args],
        cwd=WORKSPACE,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=600,
    )


def test_mwh_verify_ep2_end_to_end() -> None:
    proc = _mwh("verify", "EP-2", "--", "-q")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "marker ep_2" in proc.stdout
    assert "passed" in proc.stdout and "deselected" in proc.stdout


def test_mwh_verify_ep0_docs_only(cli_env: None) -> None:
    result = runner.invoke(app, ["verify", "EP-0"])
    assert result.exit_code == 0, result.output
    assert DOCS_ONLY_MESSAGE in result.output
    assert "EP-0" in result.output and "tier n/a" in result.output


def test_mwh_verify_usage_errors(cli_env: None) -> None:
    assert runner.invoke(app, ["verify", "EP-x"]).exit_code == 2
    assert runner.invoke(app, ["verify"]).exit_code == 2
    assert runner.invoke(app, ["verify", "EP-2", "--list"]).exit_code == 2
    assert runner.invoke(app, ["verify", "--list", "--roadmap"]).exit_code == 2
    # a code brief with no test module yet (EP-11's landed with EP-11; EP-12 is next in line)
    result = runner.invoke(app, ["verify", "EP-12"])
    assert result.exit_code == 2 and "test_ep12.py" in result.output


def test_mwh_verify_passes_extra_pytest_args_through(
    cli_env: None, fake_run: dict[str, Any]
) -> None:
    result = runner.invoke(app, ["verify", "EP-2", "--", "-q", "-k", "help", "--tier", "dev"])
    assert result.exit_code == 0, result.output
    ((argv, _),) = fake_run["calls"]
    assert argv[-5:] == ["-q", "-k", "help", "--tier", "dev"]


def test_mwh_verify_list(cli_env: None) -> None:
    result = runner.invoke(app, ["verify", "--list"])
    assert result.exit_code == 0, result.output
    for ep in range(0, 7):
        assert f"EP-{ep} " in result.output
    assert "test_ep06.py" in result.output and "test_ep02.py" in result.output


def test_verify_is_a_diagnostic_command() -> None:
    assert "verify" in DIAGNOSTIC_COMMANDS


# ---------------------------------------------------------------------------
# roadmap_check — the real roadmap
# ---------------------------------------------------------------------------


def test_real_roadmap_has_zero_errors() -> None:
    report = roadmap_check(ROADMAP, REPO)
    assert report.errors == [], [f.message for f in report.errors]
    assert len(report.rows) == len(report.briefs) >= 164
    assert report.done_count >= 6
    assert report.exit_code() == 0
    # every check ran over real content
    assert all(b.has_header and b.h1_ep == ep for ep, b in report.briefs.items())
    assert {r.phase_kind for r in report.rows} == {"charter", "full"}
    assert report.briefs[6].tier == "fixture" and report.briefs[0].docs_only


def test_real_roadmap_row_and_brief_parsing() -> None:
    rows = {r.ep: r for r in parse_roadmap_table(ROADMAP / "README.md")}
    assert rows[6].title == "`mwh verify EP-n` + roadmap_check.py"
    assert rows[6].link == "EP-6-verify-roadmap-check.md"
    assert rows[6].depends == frozenset({2}) and rows[6].size == "S" and rows[6].core == "core"
    # EP-164 item 6 relaxed this from == 3: the planning commit `cd67743` left the EP-0 cell
    # (no `(EP-0)` in its subject → the one `--strict` warning) and is cited in prose instead
    assert rows[0].done and len(rows[0].hashes) >= 2
    assert rows[7].depends == frozenset(range(0, 7))
    b = parse_brief(ROADMAP / "EP-7-replan-p0.md")
    assert b.depends == frozenset(range(0, 7)) and b.docs_only and not b.has_charter
    files = brief_files(ROADMAP)
    assert set(files) == set(rows)


# ---------------------------------------------------------------------------
# roadmap_check — crafted roadmaps (each fault class)
# ---------------------------------------------------------------------------


def test_crafted_roadmap_is_clean(tmp_path: Path, fake_git: dict[str, list[list[str]]]) -> None:
    rm = _make_roadmap(tmp_path)
    report = roadmap_check(rm, tmp_path)
    assert report.findings == [], report.findings
    assert report.exit_code() == 0 and report.exit_code(strict=True) == 0
    assert [c[:2] for c in fake_git["calls"]] == [["cat-file", "-e"], ["log", "-1"]]
    assert report.summary().startswith("roadmap_check: OK - 2 rows, 2 briefs, 1 done")


def test_missing_readme_is_a_parity_error(tmp_path: Path) -> None:
    report = roadmap_check(tmp_path / "nowhere", tmp_path)
    assert report.exit_code() == 1 and report.errors[0].check == "parity"


def test_parity_faults(tmp_path: Path, fake_git: dict[str, list[list[str]]]) -> None:
    rm = _make_roadmap(tmp_path)
    (rm / "EP-2-beta.md").unlink()  # row links a missing file
    _write(rm / "EP-3-gamma.md", _brief(3, "Gamma", _header()))  # brief without a row
    _write(rm / "EP-4-delta.md", _brief(4, "Delta", _header(), h1_ep=5))  # H1 number ≠ file
    _write(rm / "EP-4-delta-completion-handoff.md", "# handoff — ignored\n")
    report = roadmap_check(rm, tmp_path)
    parity = _messages(report, "parity", "error")
    assert any("missing file: EP-2-beta.md" in m for m in parity)
    assert any("no table row: EP-3-gamma.md" in m for m in parity)
    assert any("no table row: EP-4-delta.md" in m for m in parity)
    assert any("H1 says EP-5, file name says EP-4" in m for m in parity)
    assert not any("handoff" in m for m in parity)
    assert report.exit_code() == 1


def test_parity_row_number_and_duplicates(
    tmp_path: Path, fake_git: dict[str, list[list[str]]]
) -> None:
    rm = _make_roadmap(tmp_path)
    readme = rm / "README.md"
    text = readme.read_text(encoding="utf-8")
    # a second row for EP-2 that links EP-1's file, and a row whose number ≠ its file number
    extra = "\n".join(
        [
            _row(2, "Alpha", "EP-1-alpha.md", "S", "—", "core", "☐"),
            _row(9, "Beta `code`", "EP-2-beta.md", "M", "EP-1", "core", "☐"),
        ]
    )
    _write(readme, text.rstrip("\n") + "\n" + extra + "\n")
    parity = _messages(roadmap_check(rm, tmp_path), "parity", "error")
    assert any("EP-2 has 2 rows" in m for m in parity)
    assert any("EP-1-alpha.md is linked by 2 rows" in m for m in parity)
    assert any("row EP-9 links a file numbered EP-2" in m for m in parity)


def test_header_faults(tmp_path: Path, fake_git: dict[str, list[list[str]]]) -> None:
    rm = _make_roadmap(tmp_path)
    # H1 title mismatch (backticks matter), Size, Core and Depends-on drift
    _write(
        rm / "EP-2-beta.md",
        _brief(2, "Beta code", _header(size="L", tier="n/a", core="stretch", depends="—")),
    )
    report = roadmap_check(rm, tmp_path)
    header = _messages(report, "header", "error")
    assert any("H1 title 'Beta code' != table title 'Beta `code`'" in m for m in header)
    assert any("Size 'L' != table 'M'" in m for m in header)
    assert any("Core/Stretch 'stretch' != table 'core'" in m for m in header)
    assert any("Depends-on differs: header-only -; table-only EP-1" in m for m in header)
    assert _messages(report, "parity") == []
    assert report.exit_code() == 1


def test_header_depends_regex_takes_only_named_tokens(
    tmp_path: Path, fake_git: dict[str, list[list[str]]]
) -> None:
    rm = _make_roadmap(tmp_path)
    # "EP-1 (Alpha)" counts; a bare "EP-7" in a name or in Blocks does not
    _write(
        rm / "EP-2-beta.md",
        _brief(
            2,
            "Beta `code`",
            _header(size="M", tier="n/a", depends="EP-1 (Alpha (see EP-7))", blocks="EP-3 (x)"),
        ),
    )
    assert parse_brief(rm / "EP-2-beta.md").depends == frozenset({1})
    assert _messages(roadmap_check(rm, tmp_path), "header") == []


def test_hash_faults(tmp_path: Path, fake_git: dict[str, list[list[str]]]) -> None:
    rm = _make_roadmap(tmp_path)
    readme = rm / "README.md"
    _write(
        readme,
        _readme(
            {
                "Phase P0 — Test (full briefs)": [
                    # ☑ with an unresolvable hash + a resolvable one whose subject lacks (EP-1)
                    _row(
                        1,
                        "Alpha",
                        "EP-1-alpha.md",
                        "S",
                        "—",
                        "core",
                        f"☑ `{BAD_HASH}` + `{GOOD_B}`",
                    ),
                    # ☑ depending on ☐ EP-1? no — EP-1 is ☑; make EP-2 ☑ and depend on ☐ EP-3
                    _row(
                        2, "Beta `code`", "EP-2-beta.md", "M", "EP-1, EP-3", "core", f"☑ `{GOOD_C}`"
                    ),
                    _row(3, "Gamma", "EP-3-gamma.md", "S", "—", "core", "☐"),
                ]
            }
        ),
    )
    _write(
        rm / "EP-2-beta.md",
        _brief(
            2, "Beta `code`", _header(size="M", tier="n/a", depends="EP-1 (Alpha), EP-3 (Gamma)")
        ),
    )
    _write(rm / "EP-3-gamma.md", _brief(3, "Gamma", _header()))
    report = roadmap_check(rm, tmp_path)
    errors = _messages(report, "hashes", "error")
    warnings = _messages(report, "hashes", "warning")
    assert errors == [f"hash {BAD_HASH} does not resolve to a commit"]
    assert any(f"commit {GOOD_B} subject lacks '(EP-1)'" in m for m in warnings)
    assert any("☑ EP-2 depends on ☐ EP-3" in m for m in warnings)
    assert not any("EP-1" in m and "depends on" in m for m in warnings)
    assert report.exit_code() == 1
    # the fake git saw the ^{commit} probe for every hash
    probes = [c[2] for c in fake_git["calls"] if c[:2] == ["cat-file", "-e"]]
    assert set(probes) == {f"{BAD_HASH}^{{commit}}", f"{GOOD_B}^{{commit}}", f"{GOOD_C}^{{commit}}"}


def test_hash_cell_shape_faults(tmp_path: Path, fake_git: dict[str, list[list[str]]]) -> None:
    rm = _make_roadmap(tmp_path)
    _write(
        rm / "README.md",
        _readme(
            {
                "Phase P0 — Test (full briefs)": [
                    _row(1, "Alpha", "EP-1-alpha.md", "S", "—", "core", "☑"),
                    _row(2, "Beta `code`", "EP-2-beta.md", "M", "EP-1", "core", f"☐ `{GOOD_A}`"),
                ]
            }
        ),
    )
    errors = _messages(roadmap_check(rm, tmp_path), "hashes", "error")
    assert any("☑ without a `hash`" in m for m in errors)
    assert any("☐ carries hashes" in m for m in errors)


def test_strict_flips_warnings_to_exit_1(
    tmp_path: Path, fake_git: dict[str, list[list[str]]]
) -> None:
    rm = _make_roadmap(tmp_path)
    readme = rm / "README.md"
    _write(readme, readme.read_text(encoding="utf-8").replace(f"☑ `{GOOD_A}`", f"☑ `{GOOD_B}`"))
    report = roadmap_check(rm, tmp_path)
    assert report.errors == [] and len(report.warnings) == 1
    assert report.exit_code() == 0 and report.ok()
    assert report.exit_code(strict=True) == 1 and not report.ok(strict=True)
    assert "[strict]" in report.summary(strict=True)


def test_charter_faults(tmp_path: Path, fake_git: dict[str, list[list[str]]]) -> None:
    rm = _make_roadmap(tmp_path)
    _write(rm / "EP-3-gamma.md", _brief(3, "Gamma", _header(), charter_by=5))  # names EP-5: no row
    _write(rm / "EP-4-delta.md", _brief(4, "Delta", _header()))  # charter phase, no charter line
    _write(
        rm / "EP-6-zeta.md", _brief(6, "Zeta", _header(), charter_by=1)
    )  # ok but EP-1 not a re-plan
    _write(
        rm / "EP-1-alpha.md", _brief(1, "Alpha", _header(size="S", tier="fixture"), charter_by=2)
    )  # full phase
    _write(
        rm / "README.md",
        _readme(
            {
                "Phase P0 — Test (full briefs)": [
                    _row(1, "Alpha", "EP-1-alpha.md", "S", "—", "core", f"☑ `{GOOD_A}`"),
                    _row(2, "Beta `code`", "EP-2-beta.md", "M", "EP-1", "core", "☐"),
                ],
                "Phase P1 — Later (charter briefs)": [
                    _row(3, "Gamma", "EP-3-gamma.md", "S", "—", "core", "☐"),
                    _row(4, "Delta", "EP-4-delta.md", "S", "—", "core", "☐"),
                    _row(6, "Zeta", "EP-6-zeta.md", "S", "—", "core", "☐"),
                ],
            }
        ),
    )
    report = roadmap_check(rm, tmp_path)
    by_ep = {(f.ep, f.level): f.message for f in report.by_check("charters")}
    assert "names EP-5, which has no row" in by_ep[(3, "error")]
    assert "without a '> **Charter.**' line" in by_ep[(4, "error")]
    assert "not a re-plan brief" in by_ep[(6, "warning")]
    assert "full-brief-phase row carries" in by_ep[(1, "error")]
    assert len(report.by_check("charters")) == 4
    assert _messages(report, "parity") == [] and _messages(report, "header") == []


def test_charter_ok_when_named_replan_exists(
    tmp_path: Path, fake_git: dict[str, list[list[str]]]
) -> None:
    rm = _make_roadmap(tmp_path)
    _write(rm / "EP-3-gamma.md", _brief(3, "Gamma", _header(), charter_by=4))
    _write(rm / "EP-4-replan.md", _brief(4, "Re-plan P1", _header(tier="n/a"), charter_by=4))
    _write(
        rm / "README.md",
        _readme(
            {
                "Phase P0 — Test (full briefs)": [
                    _row(1, "Alpha", "EP-1-alpha.md", "S", "—", "core", f"☑ `{GOOD_A}`"),
                    _row(2, "Beta `code`", "EP-2-beta.md", "M", "EP-1", "core", "☐"),
                ],
                "Phase P1 — Later (charter briefs)": [
                    _row(3, "Gamma", "EP-3-gamma.md", "S", "—", "core", "☐"),
                    _row(4, "Re-plan P1", "EP-4-replan.md", "S", "—", "core", "☐"),
                ],
            }
        ),
    )
    report = roadmap_check(rm, tmp_path)
    assert report.findings == [], report.findings
    assert report.briefs[3].charter_ep == 4 and report.briefs[4].has_charter


def test_report_json_shape(tmp_path: Path, fake_git: dict[str, list[list[str]]]) -> None:
    rm = _make_roadmap(tmp_path)
    d = roadmap_check(rm, tmp_path).as_dict()
    assert set(d) == {
        "roadmap_dir",
        "repo_root",
        "ok",
        "strict",
        "exit_code",
        "counts",
        "findings",
        "rows",
    }
    assert d["counts"] == {"rows": 2, "briefs": 2, "done": 1, "errors": 0, "warnings": 0}
    row = d["rows"][0]
    assert row["ep"] == 1 and row["done"] is True and row["hashes"] == [GOOD_A]
    assert row["tier"] == "fixture" and row["phase_kind"] == "full" and row["charter_ep"] is None
    assert d["rows"][1]["depends"] == [1] and d["rows"][1]["tier"] == "n/a"
    json.dumps(d)  # serialisable


# ---------------------------------------------------------------------------
# CLI + script surface for --roadmap
# ---------------------------------------------------------------------------


def test_mwh_verify_roadmap_cli(cli_env: None) -> None:
    result = runner.invoke(app, ["verify", "--roadmap"])
    assert result.exit_code == 0, result.output
    for check in CHECKS:
        assert check in result.output
    assert "roadmap_check: OK" in result.output

    result = runner.invoke(app, ["verify", "--roadmap", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True and payload["counts"]["errors"] == 0
    assert payload["counts"]["rows"] >= 164


def test_mwh_verify_roadmap_strict_cli(
    tmp_path: Path,
    cli_env: None,
    fake_git: dict[str, list[list[str]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rm = _make_roadmap(tmp_path)
    readme = rm / "README.md"
    _write(readme, readme.read_text(encoding="utf-8").replace(f"☑ `{GOOD_A}`", f"☑ `{GOOD_B}`"))
    monkeypatch.setattr(verify, "roadmap_dir", lambda: rm)
    monkeypatch.setattr(verify, "repo_root", lambda: tmp_path)
    assert runner.invoke(app, ["verify", "--roadmap"]).exit_code == 0
    result = runner.invoke(app, ["verify", "--roadmap", "--strict"])
    assert result.exit_code == 1, result.output
    assert "warning" in result.output and "[strict]" in result.output
    payload = json.loads(runner.invoke(app, ["verify", "--roadmap", "--strict", "--json"]).output)
    assert payload["strict"] is True and payload["exit_code"] == 1


def test_roadmap_check_main_and_script(
    tmp_path: Path, fake_git: dict[str, list[list[str]]], capsys: pytest.CaptureFixture[str]
) -> None:
    rm = _make_roadmap(tmp_path)
    assert verify.roadmap_check_main(["--roadmap", str(rm), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["counts"]["rows"] == 2
    assert verify.roadmap_check_main(["--roadmap"]) == EXIT_USAGE
    assert verify.roadmap_check_main(["--roadmap", str(rm)]) == 0
    assert "roadmap_check: OK" in capsys.readouterr().out
    # the thin script exists and runs against the real roadmap in a fresh interpreter
    script = WORKSPACE / "scripts" / "roadmap_check.py"
    assert script.is_file()
    proc = subprocess.run(
        [sys.executable, str(script), "--json"],
        cwd=WORKSPACE,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=300,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert json.loads(proc.stdout)["counts"]["errors"] == 0


def test_poe_task_and_docs_registered() -> None:
    import tomllib

    tasks = tomllib.loads((WORKSPACE / "pyproject.toml").read_text(encoding="utf-8"))["tool"][
        "poe"
    ]["tasks"]
    assert tasks["roadmap-check"] == "python scripts/roadmap_check.py"
    readme = (WORKSPACE / "README.md").read_text(encoding="utf-8")
    assert "uv run --group dev mwh verify EP-<n>" in readme
    assert "uv run poe roadmap-check" in readme
    assert (WORKSPACE / "src" / "mimicwarehouse" / "verify.py").is_file()


def test_verify_import_stays_light() -> None:
    """verify.py is imported by cli.py: no duckdb/pandas/polars/pyarrow at import time."""
    code = (
        "import sys, mimicwarehouse.cli; "
        "print(sorted(m for m in ('duckdb','pandas','polars','pyarrow') if m in sys.modules))"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code], cwd=WORKSPACE, capture_output=True, text=True, check=False
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "[]"
