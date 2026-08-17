"""``mwh verify`` — one brief's acceptance run + the roadmap consistency check (EP-6, DESIGN §15).

Two jobs, both driven by the roadmap conventions fixed at planning (D-37, ``roadmap/README.md``
§"How to use this roadmap"):

``verify(ep)`` — ``mwh verify EP-n [-- <pytest args>]``
    Runs the brief's pytest marker set (``ep_<n>``, registered by ``tests/conftest.py``) in a
    **fresh interpreter** (``python -m pytest -m ep_<n> -p no:cacheprovider …``, cwd = the
    workspace root; Windows spawn-safe) and returns pytest's exit code. Docs-only briefs
    (header Tier ``n/a`` and no ``tests/ep/test_ep<NN>.py``) verify cleanly without running
    anything; a code brief without a test module, or a marker that collects nothing (pytest
    exit 5), is failure 2. Tier selection (``--tier``) arrives with EP-12 and is passed through
    untouched as extra pytest args.

``roadmap_check(roadmap_dir, repo_root)`` — ``mwh verify --roadmap [--strict] [--json]``
    Parses the master roadmap tables and every brief's H1 / header line and reports, grouped
    by check: **parity** (rows ↔ files, numbers agree), **header** (H1 title, Size,
    Core/Stretch, Depends-on set equal the row), **hashes** (every ☑ hash resolves via
    ``git cat-file -e <hash>^{commit}``; warn when the commit subject lacks ``(EP-n)`` or a ☑
    brief depends on a ☐ brief) and **charters** (rows under a ``charter briefs`` phase heading
    carry a ``> **Charter.**`` line naming an existing re-plan EP; rows under ``full briefs``
    do not). Errors → exit 1; warnings → exit 0 unless ``strict``. The check only *reports* —
    ☑ boxes and hashes are ticked by hand (brief EP-6, Out of scope). The same function backs
    the thin script ``mimicwarehouse/scripts/roadmap_check.py`` (poe ``roadmap-check``) and its
    ``--json`` output is what the re-plan EPs read.

Formats recognised (planning session, verbatim)::

    | EP-n | [Title](EP-n-slug.md) | Size | Depends | core/stretch | ☐ or ☑ `hash` (+ `hash2`) |
    # EP-n — Title
    **Size:** … · **Tier:** … · **Core/Stretch:** … · **Depends on:** EP-a (name), … · **Blocks:** …
    > **Charter.** … upgraded to a full brief by EP-m (Re-plan Pk) before execution.

Import cost: stdlib + typer (rich only inside the command bodies); nothing data-related.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Annotated, Any, Literal

import typer

from mimicwarehouse import config

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Exit codes of :func:`verify` beyond pytest's own (0 pass, 1 failures, 3/4 internal/usage).
EXIT_OK = 0
EXIT_USAGE = 2
#: pytest's "no tests collected" — reported as :data:`EXIT_USAGE` with a marker hint.
PYTEST_NO_TESTS_COLLECTED = 5

DOCS_ONLY_TIER = "n/a"
DOCS_ONLY_MESSAGE = "docs-only brief — nothing to run"

Level = Literal["error", "warning"]
CheckName = Literal["parity", "header", "hashes", "charters"]
CHECKS: tuple[CheckName, ...] = ("parity", "header", "hashes", "charters")

_EP_TOKEN = re.compile(r"^\s*(?:ep-?)?(\d+)\s*$", re.IGNORECASE)
_BRIEF_FILE = re.compile(r"^EP-(\d+)-.*\.md$")
_EXCLUDED_BRIEF_SUFFIXES = ("-completion-handoff.md", "-completion-report.md")
_H1 = re.compile(r"^# EP-(\d+) — (.+?)\s*$")
_HEADER = re.compile(
    r"^\*\*Size:\*\*\s*(?P<size>\S+)\s*·\s*"
    r"\*\*Tier:\*\*\s*(?P<tier>.+?)\s*·\s*"
    r"\*\*Core/Stretch:\*\*\s*(?P<core>\S+)\s*·\s*"
    r"\*\*Depends on:\*\*\s*(?P<depends>.*?)\s*·\s*"
    r"\*\*Blocks:\*\*\s*(?P<blocks>.*?)\s*$"
)
#: Depends-on tokens in a brief header: ``EP-n`` immediately followed by `` (`` (the name).
_HEADER_DEP = re.compile(r"EP-(\d+)(?= \()")
_TABLE_DEP = re.compile(r"EP-(\d+)")
_ROW = re.compile(
    r"^\|\s*EP-(?P<ep>\d+)\s*\|\s*\[(?P<title>.+?)\]\((?P<link>[^)\s]+)\)\s*\|\s*"
    r"(?P<size>[^|]*?)\s*\|\s*(?P<depends>[^|]*?)\s*\|\s*(?P<core>[^|]*?)\s*\|\s*"
    r"(?P<done>[^|]*?)\s*\|\s*$"
)
_HASH = re.compile(r"`([0-9a-fA-F]{7,40})`")
_HEADING = re.compile(r"^##\s+(.*?)\s*$")
_CHARTER_LINE = re.compile(r"^>\s*\*\*Charter\.\*\*")
_CHARTER_EP = re.compile(r"\bby\s+EP-(\d+)")
_ANY_EP = re.compile(r"EP-(\d+)")


class VerifyError(RuntimeError):
    """A usage / lookup problem in :func:`verify` (unknown brief, bad EP token)."""


# ---------------------------------------------------------------------------
# EP tokens, roots
# ---------------------------------------------------------------------------


def resolve_ep(text: str | int) -> int:
    """``"EP-6"`` / ``"ep6"`` / ``"6"`` / ``6`` → ``6``; :class:`VerifyError` otherwise."""
    if isinstance(text, bool):  # bool is an int subclass — never an EP number
        raise VerifyError(f"not an EP number: {text!r}")
    if isinstance(text, int):
        if text < 0:
            raise VerifyError(f"not an EP number: {text!r}")
        return text
    m = _EP_TOKEN.match(text)
    if m is None:
        raise VerifyError(f"not an EP number: {text!r} (expected EP-<n>, ep<n> or <n>)")
    return int(m.group(1))


def workspace_root() -> Path:
    """The uv project (``mimicwarehouse/``) — where pytest runs (tests monkeypatch)."""
    return config.workspace_root()


def repo_root() -> Path:
    """The git checkout (roadmap lives at ``<repo>/roadmap``); fallback: the workspace parent."""
    root = config.repo_root()
    return root if root is not None else workspace_root().parent


def roadmap_dir() -> Path:
    return repo_root() / "roadmap"


# ---------------------------------------------------------------------------
# Brief and roadmap-table parsing
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Brief:
    """What one ``roadmap/EP-<n>-*.md`` brief declares in its H1, header line and charter note."""

    path: Path
    file_ep: int
    h1_ep: int | None
    title: str | None
    size: str | None
    tier: str | None
    core: str | None
    depends: frozenset[int]
    has_header: bool
    has_charter: bool
    charter_ep: int | None

    @property
    def docs_only(self) -> bool:
        return (self.tier or "").strip().lower() == DOCS_ONLY_TIER

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["path"] = self.path.name
        d["depends"] = sorted(self.depends)
        return d


@dataclass(frozen=True, slots=True)
class Row:
    """One ``| EP-n | [Title](file) | Size | Depends | core | Done |`` row of the master table."""

    ep: int
    title: str
    link: str
    size: str
    depends: frozenset[int]
    core: str
    done: bool
    hashes: tuple[str, ...]
    done_cell: str
    phase: str | None
    phase_kind: Literal["charter", "full"] | None
    line_no: int

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["depends"] = sorted(self.depends)
        d["hashes"] = list(self.hashes)
        return d


def brief_files(roadmap: Path) -> dict[int, list[Path]]:
    """``EP-<n>-*.md`` briefs by number (handoff / report companions excluded)."""
    found: dict[int, list[Path]] = {}
    for path in sorted(roadmap.glob("EP-*.md")):
        if path.name.endswith(_EXCLUDED_BRIEF_SUFFIXES):
            continue
        m = _BRIEF_FILE.match(path.name)
        if m is None:
            continue
        found.setdefault(int(m.group(1)), []).append(path)
    return found


def find_brief(ep: int, roadmap: Path | None = None) -> Path | None:
    """The brief file for EP-``ep`` (first match if several), or None."""
    files = brief_files(roadmap or roadmap_dir()).get(ep, [])
    return files[0] if files else None


def parse_brief(path: Path) -> Brief:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    m_file = _BRIEF_FILE.match(path.name)
    file_ep = int(m_file.group(1)) if m_file else -1

    h1_ep: int | None = None
    title: str | None = None
    for line in lines:
        m = _H1.match(line)
        if m:
            h1_ep, title = int(m.group(1)), m.group(2)
            break

    size = tier = core = None
    depends: frozenset[int] = frozenset()
    has_header = False
    for line in lines:
        if not line.startswith("**Size:**"):
            continue
        m = _HEADER.match(line.strip())
        if m:
            has_header = True
            size, tier, core = m.group("size"), m.group("tier"), m.group("core")
            depends = frozenset(int(n) for n in _HEADER_DEP.findall(m.group("depends")))
        break

    has_charter = False
    charter_ep: int | None = None
    for i, line in enumerate(lines):
        if not _CHARTER_LINE.match(line):
            continue
        has_charter = True
        # the whole blockquote paragraph (continuation lines start with ">")
        para: list[str] = []
        for cont in lines[i:]:
            if not cont.startswith(">"):
                break
            para.append(cont.lstrip("> ").rstrip())
        joined = " ".join(para)
        m = _CHARTER_EP.search(joined) or _ANY_EP.search(joined)
        charter_ep = int(m.group(1)) if m else None
        break

    return Brief(
        path=path,
        file_ep=file_ep,
        h1_ep=h1_ep,
        title=title,
        size=size,
        tier=tier,
        core=core,
        depends=depends,
        has_header=has_header,
        has_charter=has_charter,
        charter_ep=charter_ep,
    )


def parse_roadmap_table(readme: Path) -> list[Row]:
    """Every EP row of the master roadmap, tagged with its enclosing ``## …`` heading."""
    rows: list[Row] = []
    phase: str | None = None
    kind: Literal["charter", "full"] | None = None
    for line_no, line in enumerate(readme.read_text(encoding="utf-8").splitlines(), start=1):
        h = _HEADING.match(line)
        if h:
            phase = str(h.group(1))
            low = phase.lower()
            kind = (
                "charter" if "charter briefs" in low else "full" if "full briefs" in low else None
            )
            continue
        m = _ROW.match(line)
        if m is None:
            continue
        done_cell = m.group("done").strip()
        rows.append(
            Row(
                ep=int(m.group("ep")),
                title=m.group("title"),
                link=m.group("link"),
                size=m.group("size").strip(),
                depends=frozenset(int(n) for n in _TABLE_DEP.findall(m.group("depends"))),
                core=m.group("core").strip(),
                done=done_cell.startswith("☑"),
                hashes=tuple(h.lower() for h in _HASH.findall(done_cell)),
                done_cell=done_cell,
                phase=phase,
                phase_kind=kind,
                line_no=line_no,
            )
        )
    return rows


# ---------------------------------------------------------------------------
# roadmap_check
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Finding:
    level: Level
    check: CheckName
    ep: int | None
    message: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class Report:
    roadmap_dir: Path
    repo_root: Path
    findings: list[Finding] = field(default_factory=list)
    rows: list[Row] = field(default_factory=list)
    briefs: dict[int, Brief] = field(default_factory=dict)

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "error"]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "warning"]

    @property
    def done_count(self) -> int:
        return sum(1 for r in self.rows if r.done)

    def ok(self, strict: bool = False) -> bool:
        return not self.errors and not (strict and self.warnings)

    def exit_code(self, strict: bool = False) -> int:
        return 0 if self.ok(strict) else 1

    def by_check(self, check: CheckName) -> list[Finding]:
        return [f for f in self.findings if f.check == check]

    def summary(self, strict: bool = False) -> str:
        verdict = "OK" if self.ok(strict) else "FAIL"
        return (
            f"roadmap_check: {verdict} - {len(self.rows)} rows, {len(self.briefs)} briefs, "
            f"{self.done_count} done, {len(self.errors)} error(s), {len(self.warnings)} warning(s)"
            + (" [strict]" if strict else "")
        )

    def as_dict(self, strict: bool = False) -> dict[str, Any]:
        return {
            "roadmap_dir": str(self.roadmap_dir),
            "repo_root": str(self.repo_root),
            "ok": self.ok(strict),
            "strict": strict,
            "exit_code": self.exit_code(strict),
            "counts": {
                "rows": len(self.rows),
                "briefs": len(self.briefs),
                "done": self.done_count,
                "errors": len(self.errors),
                "warnings": len(self.warnings),
            },
            "findings": [f.as_dict() for f in self.findings],
            "rows": [
                {
                    **r.as_dict(),
                    "tier": (b.tier if (b := self.briefs.get(r.ep)) else None),
                    "charter_ep": (b.charter_ep if b else None),
                }
                for r in self.rows
            ],
        }


def _run_git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """``git -C <repo> <args>`` (tests monkeypatch this)."""
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def commit_exists(repo: Path, sha: str) -> bool:
    return _run_git(repo, "cat-file", "-e", f"{sha}^{{commit}}").returncode == 0


def commit_subject(repo: Path, sha: str) -> str | None:
    proc = _run_git(repo, "log", "-1", "--format=%s", sha)
    return proc.stdout.strip() if proc.returncode == 0 else None


def roadmap_check(
    roadmap: Path | None = None,
    repo: Path | None = None,
    strict: bool = False,
    readme_name: str = "README.md",
) -> Report:
    """Run the four checks over ``roadmap/README.md`` + ``roadmap/EP-*.md``; never edits."""
    roadmap = Path(roadmap) if roadmap is not None else roadmap_dir()
    repo = Path(repo) if repo is not None else repo_root()
    report = Report(roadmap_dir=roadmap, repo_root=repo)
    add = report.findings.append

    readme = roadmap / readme_name
    if not readme.is_file():
        add(Finding("error", "parity", None, f"master roadmap not found: {readme}"))
        return report
    rows = parse_roadmap_table(readme)
    report.rows = rows
    files = brief_files(roadmap)
    briefs: dict[int, Brief] = {}
    for ep, paths in files.items():
        if len(paths) > 1:
            names = ", ".join(p.name for p in paths)
            add(Finding("error", "parity", ep, f"more than one brief file for EP-{ep}: {names}"))
        briefs[ep] = parse_brief(paths[0])
    report.briefs = briefs
    rows_by_ep: dict[int, list[Row]] = {}
    for r in rows:
        rows_by_ep.setdefault(r.ep, []).append(r)

    # --- parity -------------------------------------------------------------------------
    linked: dict[str, list[Row]] = {}
    for r in rows:
        linked.setdefault(r.link, []).append(r)
        target = roadmap / r.link
        if not target.is_file():
            add(Finding("error", "parity", r.ep, f"row links a missing file: {r.link}"))
            continue
        m = _BRIEF_FILE.match(r.link)
        if m is None:
            add(Finding("error", "parity", r.ep, f"row links a non-brief file name: {r.link}"))
        elif int(m.group(1)) != r.ep:
            add(
                Finding(
                    "error",
                    "parity",
                    r.ep,
                    f"row EP-{r.ep} links a file numbered EP-{int(m.group(1))}: {r.link}",
                )
            )
    for ep, same in rows_by_ep.items():
        if len(same) > 1:
            lines = ", ".join(str(r.line_no) for r in same)
            add(Finding("error", "parity", ep, f"EP-{ep} has {len(same)} rows (lines {lines})"))
    for link, same in linked.items():
        if len(same) > 1:
            add(Finding("error", "parity", same[0].ep, f"{link} is linked by {len(same)} rows"))
    for ep, brief in sorted(briefs.items()):
        if brief.path.name not in linked:
            add(Finding("error", "parity", ep, f"brief has no table row: {brief.path.name}"))
        if brief.h1_ep is None:
            add(Finding("error", "parity", ep, f"{brief.path.name}: no '# EP-n — Title' H1"))
        elif brief.h1_ep != ep:
            add(
                Finding(
                    "error",
                    "parity",
                    ep,
                    f"{brief.path.name}: H1 says EP-{brief.h1_ep}, file name says EP-{ep}",
                )
            )
        if not brief.has_header:
            add(
                Finding(
                    "error",
                    "parity",
                    ep,
                    f"{brief.path.name}: no '**Size:** … · **Blocks:** …' header line",
                )
            )

    # --- header ↔ table -----------------------------------------------------------------
    for r in rows:
        b = briefs.get(r.ep)
        if b is None or b.path.name != r.link:
            continue  # reported under parity
        if b.title is not None and b.title != r.title:
            add(
                Finding(
                    "error",
                    "header",
                    r.ep,
                    f"H1 title {b.title!r} != table title {r.title!r}",
                )
            )
        if b.has_header:
            if (b.size or "") != r.size:
                add(Finding("error", "header", r.ep, f"Size {b.size!r} != table {r.size!r}"))
            if (b.core or "") != r.core:
                add(
                    Finding("error", "header", r.ep, f"Core/Stretch {b.core!r} != table {r.core!r}")
                )
            if b.depends != r.depends:
                only_b = ", ".join(f"EP-{n}" for n in sorted(b.depends - r.depends)) or "-"
                only_r = ", ".join(f"EP-{n}" for n in sorted(r.depends - b.depends)) or "-"
                add(
                    Finding(
                        "error",
                        "header",
                        r.ep,
                        f"Depends-on differs: header-only {only_b}; table-only {only_r}",
                    )
                )

    # --- ☑ hashes -----------------------------------------------------------------------
    done_eps = {r.ep for r in rows if r.done}
    for r in rows:
        if r.done and not r.hashes:
            add(Finding("error", "hashes", r.ep, f"☑ without a `hash`: {r.done_cell!r}"))
        if not r.done and r.hashes:
            add(Finding("error", "hashes", r.ep, f"☐ carries hashes: {r.done_cell!r}"))
        if not r.done and not r.done_cell.startswith("☐"):
            add(Finding("error", "hashes", r.ep, f"Done cell is neither ☐ nor ☑: {r.done_cell!r}"))
        for sha in r.hashes:
            if not commit_exists(repo, sha):
                add(Finding("error", "hashes", r.ep, f"hash {sha} does not resolve to a commit"))
                continue
            subject = commit_subject(repo, sha) or ""
            if not re.search(rf"\(EP-{r.ep}\b", subject):
                add(
                    Finding(
                        "warning",
                        "hashes",
                        r.ep,
                        f"commit {sha} subject lacks '(EP-{r.ep})': {subject[:80]!r}",
                    )
                )
        if r.done:
            for dep in sorted(r.depends):
                if dep in rows_by_ep and dep not in done_eps:
                    add(
                        Finding(
                            "warning",
                            "hashes",
                            r.ep,
                            f"☑ EP-{r.ep} depends on ☐ EP-{dep}",
                        )
                    )

    # --- charters -----------------------------------------------------------------------
    for r in rows:
        b = briefs.get(r.ep)
        if b is None or b.path.name != r.link:
            continue
        if r.phase_kind == "charter":
            if not b.has_charter:
                add(
                    Finding(
                        "error",
                        "charters",
                        r.ep,
                        "charter-phase row without a '> **Charter.**' line",
                    )
                )
            elif b.charter_ep is None:
                add(Finding("error", "charters", r.ep, "charter line names no re-plan EP"))
            elif b.charter_ep not in rows_by_ep:
                add(
                    Finding(
                        "error",
                        "charters",
                        r.ep,
                        f"charter names EP-{b.charter_ep}, which has no row",
                    )
                )
            else:
                target = briefs.get(b.charter_ep)
                if target is not None and target.title and "re-plan" not in target.title.lower():
                    add(
                        Finding(
                            "warning",
                            "charters",
                            r.ep,
                            f"charter names EP-{b.charter_ep} ({target.title!r}), "
                            "not a re-plan brief",
                        )
                    )
        elif r.phase_kind == "full" and b.has_charter:
            add(
                Finding(
                    "error",
                    "charters",
                    r.ep,
                    "full-brief-phase row carries a '> **Charter.**' line",
                )
            )

    return report


# ---------------------------------------------------------------------------
# verify (one brief)
# ---------------------------------------------------------------------------


def ep_test_module(ep: int, workspace: Path | None = None) -> Path:
    """``tests/ep/test_ep<NN>.py`` (two-digit minimum) for EP-``ep``."""
    return (workspace or workspace_root()) / "tests" / "ep" / f"test_ep{ep:02d}.py"


def pytest_argv(ep: int, pytest_args: Sequence[str] = ()) -> list[str]:
    """The exact command :func:`verify` runs (exposed for tests and ``--dry-run``-style use)."""
    return [
        sys.executable,
        "-m",
        "pytest",
        "-m",
        f"ep_{ep}",
        "-p",
        "no:cacheprovider",
        *pytest_args,
    ]


def verify(
    ep: int | str,
    pytest_args: Sequence[str] = (),
    *,
    workspace: Path | None = None,
    roadmap: Path | None = None,
    echo: Any = print,
) -> int:
    """Run EP-``ep``'s marker set in a fresh interpreter; return the exit code (see module doc)."""
    n = resolve_ep(ep)
    workspace = workspace or workspace_root()
    roadmap = roadmap or roadmap_dir()
    brief_path = find_brief(n, roadmap)
    if brief_path is None:
        echo(f"mwh verify: no brief EP-{n}-*.md under {roadmap}")
        return EXIT_USAGE
    brief = parse_brief(brief_path)
    module = ep_test_module(n, workspace)
    if not module.is_file():
        if brief.docs_only:
            echo(DOCS_ONLY_MESSAGE)
            return EXIT_OK
        echo(
            f"mwh verify: EP-{n} is a code brief (tier {brief.tier}) but "
            f"{module.relative_to(workspace).as_posix()} does not exist"
        )
        return EXIT_USAGE
    proc = subprocess.run(pytest_argv(n, pytest_args), cwd=workspace, check=False)
    if proc.returncode == PYTEST_NO_TESTS_COLLECTED:
        echo(
            f"mwh verify: pytest collected no tests for marker ep_{n} — is "
            f"'pytestmark = pytest.mark.ep_{n}' set in {module.name}?"
        )
        return EXIT_USAGE
    return proc.returncode


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

#: ``mwh verify EP-n -- -q -x``: everything after the EP token is handed to pytest untouched.
VERIFY_CONTEXT_SETTINGS: dict[str, Any] = {"allow_extra_args": True, "ignore_unknown_options": True}


def _console_safe(text: str) -> str:
    """Replace glyphs the current console cannot encode (⏱, ☑ on cp1252) instead of crashing."""
    enc = getattr(sys.stdout, "encoding", None) or "utf-8"
    return text.encode(enc, errors="replace").decode(enc, errors="replace")


def _print_list(console: Any, roadmap: Path, workspace: Path) -> None:
    from rich.markup import escape
    from rich.table import Table

    table = Table(title=escape(_console_safe(f"roadmap briefs — {roadmap}")), show_lines=False)
    table.add_column("EP", justify="right", no_wrap=True)
    table.add_column("Title")
    table.add_column("Tier")
    table.add_column("Test module")
    for ep, paths in sorted(brief_files(roadmap).items()):
        b = parse_brief(paths[0])
        module = ep_test_module(ep, workspace)
        table.add_row(
            f"EP-{ep}",
            escape(_console_safe(b.title or "?")),
            escape(_console_safe(b.tier or "?")),
            f"[green]{module.name}[/]" if module.is_file() else "[dim]-[/]",
        )
    console.print(table)


def _print_report(console: Any, report: Report, strict: bool) -> None:
    from rich.markup import escape
    from rich.table import Table

    for check in CHECKS:
        findings = report.by_check(check)
        if not findings:
            console.print(f"[green]{check}[/]: ok", highlight=False)
            continue
        table = Table(title=check, show_lines=False)
        table.add_column("Level")
        table.add_column("EP", justify="right", no_wrap=True)
        table.add_column("Finding")
        for f in findings:
            colour = "red" if f.level == "error" else "yellow"
            table.add_row(
                f"[{colour}]{f.level}[/]",
                f"EP-{f.ep}" if f.ep is not None else "-",
                escape(_console_safe(f.message)),
            )
        console.print(table)
    colour = "green" if report.ok(strict) else "red"
    console.print(f"[{colour}]{escape(_console_safe(report.summary(strict)))}[/]", highlight=False)


def verify_command(
    ctx: typer.Context,
    ep: Annotated[
        str | None,
        typer.Argument(
            help="Brief to verify: EP-6, ep6 or 6. Extra pytest args go after '--'.",
            show_default=False,
        ),
    ] = None,
    list_briefs: Annotated[
        bool, typer.Option("--list", help="List EP · title · tier · test module present.")
    ] = False,
    roadmap_flag: Annotated[
        bool, typer.Option("--roadmap", help="Check roadmap/README.md against the briefs.")
    ] = False,
    strict: Annotated[
        bool, typer.Option("--strict", help="With --roadmap: warnings also exit 1.")
    ] = False,
    json_output: Annotated[
        bool, typer.Option("--json", help="With --roadmap: print the report as JSON.")
    ] = False,
) -> None:
    """Run one brief's acceptance tests (``mwh verify EP-n [-- pytest args]``), list the
    briefs (``--list``) or check the roadmap tables against the briefs (``--roadmap``)."""
    from rich.markup import escape

    from mimicwarehouse.cli import console

    modes = [
        name
        for name, on in (
            ("EP", ep is not None),
            ("--list", list_briefs),
            ("--roadmap", roadmap_flag),
        )
        if on
    ]
    if len(modes) != 1:
        console.print(
            "[bold red]mwh verify:[/] give exactly one of EP-<n>, --list or --roadmap",
            highlight=False,
        )
        raise typer.Exit(code=EXIT_USAGE)
    roadmap = roadmap_dir()
    workspace = workspace_root()

    if list_briefs:
        _print_list(console, roadmap, workspace)
        raise typer.Exit(code=0)

    if roadmap_flag:
        report = roadmap_check(roadmap, repo_root(), strict=strict)
        if json_output:
            typer.echo(json.dumps(report.as_dict(strict), indent=2))
        else:
            _print_report(console, report, strict)
        raise typer.Exit(code=report.exit_code(strict))

    assert ep is not None
    try:
        n = resolve_ep(ep)
    except VerifyError as exc:
        console.print(f"[bold red]mwh verify:[/] {escape(str(exc))}", highlight=False)
        raise typer.Exit(code=EXIT_USAGE) from None
    brief_path = find_brief(n, roadmap)
    if brief_path is not None:
        b = parse_brief(brief_path)
        console.print(
            _console_safe(
                f"[bold]EP-{n}[/] — {escape(b.title or '?')} · tier {escape(b.tier or '?')} "
                f"· marker ep_{n}"
            ),
            highlight=False,
        )
    extra = [a for a in ctx.args if a != "--"]
    code = verify(n, extra, workspace=workspace, roadmap=roadmap, echo=typer.echo)
    raise typer.Exit(code=code)


def roadmap_check_main(argv: Sequence[str] | None = None) -> int:
    """Entry point of ``scripts/roadmap_check.py``: ``[--strict] [--json] [--roadmap DIR]``."""
    from rich.console import Console

    args = list(sys.argv[1:] if argv is None else argv)
    strict = "--strict" in args
    as_json = "--json" in args
    roadmap: Path | None = None
    if "--roadmap" in args:
        i = args.index("--roadmap")
        if i + 1 >= len(args):
            print("roadmap_check: --roadmap needs a directory", file=sys.stderr)
            return EXIT_USAGE
        roadmap = Path(args[i + 1])
    report = roadmap_check(roadmap, strict=strict)
    if as_json:
        print(json.dumps(report.as_dict(strict), indent=2))
    else:
        _print_report(Console(), report, strict)
    return report.exit_code(strict)


__all__ = [
    "CHECKS",
    "DOCS_ONLY_MESSAGE",
    "EXIT_OK",
    "EXIT_USAGE",
    "VERIFY_CONTEXT_SETTINGS",
    "Brief",
    "Finding",
    "Report",
    "Row",
    "VerifyError",
    "brief_files",
    "commit_exists",
    "commit_subject",
    "ep_test_module",
    "find_brief",
    "parse_brief",
    "parse_roadmap_table",
    "pytest_argv",
    "repo_root",
    "resolve_ep",
    "roadmap_check",
    "roadmap_check_main",
    "roadmap_dir",
    "verify",
    "verify_command",
    "workspace_root",
]
