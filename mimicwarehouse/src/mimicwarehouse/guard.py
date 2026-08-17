"""``mwh guard`` — the pre-commit data-leak guard (EP-4; GOVERNANCE §3, DESIGN §15).

Git history is permanent and the remote goes public at v1.0.0 (D-41), so nothing
data-shaped and no real MIMIC identifier may ever be committed. This module is a
*shape-and-id filter*, not a disclosure reviewer (that is ``mwh disclose check``, EP-43).
Five rules, each with an id used in messages and tests:

``G1`` data-shaped extension
    :data:`DATA_EXTENSIONS` anywhere in the tree — except under
    ``mimicwarehouse/tests/fixtures/`` where only :data:`FIXTURE_EXTENSIONS` pass
    (synthetic fixtures, ids >= 90 000 000, D-27).
``G2`` source material
    any path under ``source material/`` other than ``*.md``. The guard never opens a
    file under that directory: G2 refuses it by name and skips the content rules.
``G3`` notebook outputs
    an ``.ipynb`` whose JSON has a cell with non-empty ``outputs`` or a non-null
    ``execution_count`` (or that is not valid JSON), and anything inside a
    ``__marimo__/`` directory (marimo's per-notebook cache).
``G4`` real-id band
    in text files (UTF-8-decodable, no NUL byte; :data:`TEXT_EXTENSIONS` or no extension)
    any token matching :data:`ID_TOKEN` — an isolated 8-digit run starting with 1, 2 or 3 —
    whose value lies in :data:`SUBJECT_BAND`, :data:`HADM_BAND` or :data:`STAY_BAND`,
    unless the same line carries the pragma ``mwh-guard: allow``. Compact ``YYYYMMDD``
    dates are *not* exempt (write ISO dates with hyphens); longer digit runs, hex hashes
    and decimals never match by construction, and the band constants are written with
    digit-group underscores so this module never trips itself.
``G5`` oversize
    any blob larger than :data:`MAX_FILE_BYTES` (20 000 KiB, the same bound as
    ``check-added-large-files --maxkb=20000``); fixtures included.

Violation messages **never quote file content**: an id token is masked to its first digit
plus ``*`` (``1*******``) because a real id in a hook message would land in a session
transcript (GOVERNANCE §4).

Public surface (DESIGN §15): :class:`Violation`, :func:`scan` (working-tree paths),
:func:`scan_staged` (the index — what ``git commit`` would record, read as blobs so an
unstaged edit cannot mask a staged one), :func:`scan_tracked` (every tracked path, or a
revision's tree — the per-commit primitive EP-163's history sweep will call),
:func:`selfcheck` (the EP-0 ``.gitignore`` / ``.gitattributes`` probe list, the
``.pre-commit-config.yaml`` wiring and the installed hook) and :func:`guard_command`
(``mwh guard [PATHS…] [--staged] [--all-tracked] [--selfcheck] [--json]``; exit 0 clean /
1 violations / 2 usage). Import cost is a few stdlib modules + typer; nothing data-related.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Annotated, Any

import typer

# ---------------------------------------------------------------------------
# Constants (rule tables)
# ---------------------------------------------------------------------------

#: Real MIMIC-IV id bands (CLAUDE.md §2, GOVERNANCE §3). Digit-group underscores on purpose.
SUBJECT_BAND: tuple[int, int] = (10_000_000, 19_999_999)
HADM_BAND: tuple[int, int] = (20_000_000, 29_999_999)
STAY_BAND: tuple[int, int] = (30_000_000, 39_999_999)
BANDS: dict[str, tuple[int, int]] = {
    "subject_id": SUBJECT_BAND,
    "hadm_id": HADM_BAND,
    "stay_id": STAY_BAND,
}
#: Synthetic fixture ids start here (D-27); never collides with the bands above.
FIXTURE_ID_FLOOR = 90_000_000

#: G1 — refused anywhere except (a subset) under :data:`FIXTURE_DIR`.
DATA_EXTENSIONS: tuple[str, ...] = (
    ".csv",
    ".csv.gz",
    ".parquet",
    ".duckdb",
    ".duckdb.wal",
    ".duckdb.new",
    ".duckdb.tmp",
    ".wal",
    ".jsonl",
    ".feather",
    ".arrow",
    ".pkl",
    ".joblib",
    ".skops",
    ".pt",
    ".safetensors",
    ".npy",
    ".npz",
    ".h5",
)
#: The only place data-shaped files may live (repo-relative, posix, trailing slash).
FIXTURE_DIR = "mimicwarehouse/tests/fixtures/"
#: What may live there.
FIXTURE_EXTENSIONS: tuple[str, ...] = (".csv", ".csv.gz", ".parquet", ".jsonl", ".json", ".yaml")
#: G2 — nothing but ``*.md`` under here (GOVERNANCE §2-3, D-30).
SOURCE_MATERIAL_DIR = "source material/"
#: G3 — marimo cache directory name.
MARIMO_DIR = "__marimo__"
#: G4 — files whose content is scanned for band ids (plus extensionless files).
TEXT_EXTENSIONS: tuple[str, ...] = (
    ".py",
    ".md",
    ".yaml",
    ".yml",
    ".json",
    ".toml",
    ".sql",
    ".txt",
    ".csv",
    ".jsonl",
    ".html",
    ".svg",
    ".cff",
    ".ps1",
    ".ini",
    ".cfg",
)
#: G4 — an isolated 8-digit run starting with 1, 2 or 3 (ASCII digits only).
ID_TOKEN = re.compile(r"(?<![\w.])[123]\d{7}(?![\w.])", re.ASCII)
#: G4 — a line carrying this pragma is exempt (use it for documented examples only).
ALLOW_PRAGMA = "mwh-guard: allow"
#: G4 — at most this many per-line violations are reported per file, then one summary row.
MAX_G4_ROWS_PER_FILE = 25
#: G5 — same bound as ``check-added-large-files --maxkb=20000``.
MAX_FILE_BYTES = 20_000 * 1024

RULE_TITLES: dict[str, str] = {
    "G1": "data-shaped extension",
    "G2": "source material",
    "G3": "notebook outputs",
    "G4": "real-id band",
    "G5": "oversize",
}

#: EP-0 probe list (strings only — no file is ever created): must be ignored …
IGNORED_PROBES: tuple[str, ...] = (
    "source material/mimic-iv-3.1/hosp/patients.csv",
    "mimicwarehouse/foo.parquet",
    "mimicwarehouse/warehouse/dev.duckdb",
    "mimicwarehouse/runs/audit.jsonl",
    "mimicwarehouse/x.duckdb.new",
    ".claude/settings.local.json",
    "mimicwarehouse/.env",
)
#: … and must NOT be ignored.
TRACKED_PROBES: tuple[str, ...] = (
    "mimicwarehouse/tests/fixtures/hosp/patients.csv",
    "mimicwarehouse/.env.example",
    ".claude/settings.json",
    "mimicwarehouse/.streamlit/config.toml",
)
#: ``git check-attr binary`` must be ``set`` for these.
BINARY_PROBES: tuple[str, ...] = ("x.csv", "x.parquet", "x.duckdb")

GIT_TIMEOUT_S = 120


class GuardError(RuntimeError):
    """git is unavailable, the directory is not a repository, or a git call failed."""


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Violation:
    """One refused thing. ``detail`` never contains file content (ids are masked)."""

    rule: str
    path: str
    line: int | None
    detail: str

    @property
    def title(self) -> str:
        return RULE_TITLES.get(self.rule, self.rule)

    def as_dict(self) -> dict[str, Any]:
        return {
            "rule": self.rule,
            "title": self.title,
            "path": self.path,
            "line": self.line,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class Entry:
    """A path to check: repo-relative posix ``rel``; ``size`` None when it cannot be read;
    ``data`` (pre-loaded bytes, index mode) or ``path`` (working tree, read lazily)."""

    rel: str
    size: int | None
    data: bytes | None = None
    path: Path | None = None

    def content(self) -> bytes | None:
        if self.data is not None:
            return self.data
        if self.path is not None and self.path.is_file():
            return self.path.read_bytes()
        return None


@dataclass(frozen=True, slots=True)
class SelfcheckResult:
    """One selfcheck line; ``level`` is ``fail`` (breaks the selfcheck) or ``warn``."""

    id: str
    ok: bool
    detail: str
    level: str = "fail"

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "ok": self.ok, "level": self.level, "detail": self.detail}


# ---------------------------------------------------------------------------
# Rule helpers (pure)
# ---------------------------------------------------------------------------


def data_extension(name: str) -> str | None:
    """The data-shaped extension of ``name`` (longest match, e.g. ``.duckdb.wal``), or None."""
    lower = PurePosixPath(name).name.lower()
    for ext in sorted(DATA_EXTENSIONS, key=len, reverse=True):
        if lower.endswith(ext) and lower != ext:
            return ext
    return None


def is_text_candidate(rel: str) -> bool:
    """G4 applies to these names (content must still decode as UTF-8 without NULs)."""
    suffix = PurePosixPath(rel).suffix.lower()
    return suffix == "" or suffix in TEXT_EXTENSIONS


def is_notebook(rel: str) -> bool:
    return PurePosixPath(rel).suffix.lower() == ".ipynb"


def under_source_material(rel: str) -> bool:
    return rel.lower().startswith(SOURCE_MATERIAL_DIR)


def band_of(value: int) -> str | None:
    """Which real-id band an integer falls in, or None."""
    for name, (lo, hi) in BANDS.items():
        if lo <= value <= hi:
            return name
    return None


def mask(token: str) -> str:
    """First digit + ``*`` for the rest (``1*******``) — the only form an id token ever takes
    in output. (No literal example here: the guard scans this file too.)"""
    return token[:1] + "*" * (len(token) - 1)


def needs_content(rel: str, size: int | None) -> bool:
    """Whether the content rules (G3/G4) would read this entry."""
    if size is None or size > MAX_FILE_BYTES or under_source_material(rel):
        return False
    return is_notebook(rel) or is_text_candidate(rel)


def notebook_problem(data: bytes) -> str | None:
    """G3 detail for an ``.ipynb`` blob, or None when every cell is clean."""
    try:
        nb = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return "not a valid JSON notebook"
    cells = nb.get("cells", []) if isinstance(nb, dict) else []
    dirty = [
        i
        for i, cell in enumerate(cells)
        if isinstance(cell, dict)
        and (bool(cell.get("outputs")) or cell.get("execution_count") is not None)
    ]
    if not dirty:
        return None
    return (
        f"{len(dirty)} cell(s) with outputs or execution_count (first: cell {dirty[0]}); "
        "clear all outputs before committing"
    )


def id_band_hits(data: bytes) -> list[tuple[int, str, int, str]]:
    """G4: ``(line_no, band, count, masked_example)`` per offending line of a text blob.

    Non-UTF-8 or NUL-bearing content is not text and yields nothing; a line carrying
    :data:`ALLOW_PRAGMA` is skipped.
    """
    if b"\0" in data:
        return []
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return []
    hits: list[tuple[int, str, int, str]] = []
    for no, line in enumerate(text.splitlines(), start=1):
        if ALLOW_PRAGMA in line:
            continue
        found: dict[str, tuple[int, str]] = {}
        for match in ID_TOKEN.finditer(line):
            token = match.group()
            band = band_of(int(token))
            if band is None:
                continue
            count, example = found.get(band, (0, mask(token)))
            found[band] = (count + 1, example)
        for band, (count, example) in found.items():
            hits.append((no, band, count, example))
    return hits


# ---------------------------------------------------------------------------
# Checking entries
# ---------------------------------------------------------------------------


def check_entry(entry: Entry) -> list[Violation]:
    """Apply G1-G5 to one entry (name rules first, then content rules if allowed)."""
    rel = entry.rel
    out: list[Violation] = []
    ext = data_extension(rel)
    if ext is not None:
        in_fixtures = rel.startswith(FIXTURE_DIR)
        if not (in_fixtures and ext in FIXTURE_EXTENSIONS):
            where = f"under {FIXTURE_DIR}" if in_fixtures else f"outside {FIXTURE_DIR}"
            out.append(Violation("G1", rel, None, f"data-shaped extension {ext} {where}"))
    if under_source_material(rel) and not rel.lower().endswith(".md"):
        out.append(
            Violation("G2", rel, None, f"only *.md may be tracked under {SOURCE_MATERIAL_DIR!r}")
        )
        return out  # never open anything under source material/
    if MARIMO_DIR in PurePosixPath(rel).parts:
        out.append(Violation("G3", rel, None, f"{MARIMO_DIR}/ cache directory (notebook outputs)"))
    if entry.size is not None and entry.size > MAX_FILE_BYTES:
        mib = entry.size / (1024 * 1024)
        out.append(
            Violation(
                "G5",
                rel,
                None,
                f"{mib:.1f} MiB exceeds {MAX_FILE_BYTES // 1024} KiB (content skipped)",
            )
        )
        return out
    if not needs_content(rel, entry.size):
        return out
    data = entry.content()
    if data is None:
        return out
    if is_notebook(rel):
        problem = notebook_problem(data)
        if problem is not None:
            out.append(Violation("G3", rel, None, problem))
    if is_text_candidate(rel):
        hits = id_band_hits(data)
        for no, band, count, example in hits[:MAX_G4_ROWS_PER_FILE]:
            plural = "s" if count != 1 else ""
            out.append(
                Violation(
                    "G4",
                    rel,
                    no,
                    f"{count} token{plural} in the {band} band ({example}); "
                    f"real MIMIC id? use fixture ids >= {FIXTURE_ID_FLOOR:_} "
                    f"or the pragma '{ALLOW_PRAGMA}'",
                )
            )
        if len(hits) > MAX_G4_ROWS_PER_FILE:
            more = len(hits) - MAX_G4_ROWS_PER_FILE
            out.append(Violation("G4", rel, None, f"... and {more} more line(s) with band ids"))
    return out


def check_entries(entries: Iterable[Entry]) -> list[Violation]:
    violations: list[Violation] = []
    for entry in entries:
        violations.extend(check_entry(entry))
    return violations


# ---------------------------------------------------------------------------
# git plumbing
# ---------------------------------------------------------------------------


def _git(
    repo_root: Path, *args: str, input: bytes | None = None, ok_codes: Sequence[int] = (0,)
) -> bytes:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=repo_root,
            input=input,
            capture_output=True,
            timeout=GIT_TIMEOUT_S,
            check=False,
        )
    except FileNotFoundError as exc:
        raise GuardError("git executable not found on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise GuardError(f"git {args[0]} timed out after {GIT_TIMEOUT_S}s") from exc
    if proc.returncode not in ok_codes:
        err = proc.stderr.decode("utf-8", "replace").strip()
        raise GuardError(f"git {' '.join(args)} failed ({proc.returncode}): {err}")
    return proc.stdout


def _decode_path(raw: bytes) -> str:
    return raw.decode("utf-8", "surrogateescape")


def _encode_path(rel: str) -> bytes:
    return rel.encode("utf-8", "surrogateescape")


def find_repo_root(start: Path | None = None) -> Path:
    """The git toplevel containing ``start`` (default: cwd); :class:`GuardError` if none."""
    start = (start or Path.cwd()).resolve()
    out = _git(start if start.is_dir() else start.parent, "rev-parse", "--show-toplevel")
    return Path(_decode_path(out).strip())


def staged_paths(repo_root: Path) -> list[str]:
    """Repo-relative posix paths of added / copied / modified / renamed staged files."""
    out = _git(repo_root, "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z")
    return [_decode_path(p) for p in out.split(b"\0") if p]


def tracked_paths(repo_root: Path, rev: str | None = None) -> list[str]:
    """Repo-relative posix paths in the index (``rev`` None) or in ``rev``'s tree."""
    if rev is None:
        out = _git(repo_root, "ls-files", "-z", "--cached")
    else:
        out = _git(repo_root, "ls-tree", "-r", "-z", "--name-only", rev)
    return [_decode_path(p) for p in out.split(b"\0") if p]


def _object_names(rels: Sequence[str], rev: str | None) -> list[str]:
    prefix = ":" if rev is None else f"{rev}:"
    return [prefix + rel for rel in rels]


def blob_sizes(repo_root: Path, rels: Sequence[str], rev: str | None = None) -> dict[str, int]:
    """Sizes of ``rels`` as blobs in the index (``rev`` None) or in ``rev`` (missing → absent)."""
    if not rels:
        return {}
    names = _object_names(rels, rev)
    payload = b"\0".join(_encode_path(n) for n in names) + b"\0"
    out = _git(repo_root, "cat-file", "--batch-check", "-z", input=payload)
    sizes: dict[str, int] = {}
    lines = out.split(b"\n")
    for rel, line in zip(rels, lines, strict=False):
        if not line or line.endswith(b" missing") or line.endswith(b" ambiguous"):
            continue
        parts = line.rsplit(b" ", 2)  # <oid> <type> <size>; the oid never has spaces
        if len(parts) == 3 and parts[1] == b"blob":
            sizes[rel] = int(parts[2])
    return sizes


def blob_contents(repo_root: Path, rels: Sequence[str], rev: str | None = None) -> dict[str, bytes]:
    """Contents of ``rels`` as blobs (one ``git cat-file --batch`` call)."""
    if not rels:
        return {}
    names = _object_names(rels, rev)
    payload = b"\0".join(_encode_path(n) for n in names) + b"\0"
    out = _git(repo_root, "cat-file", "--batch", "-z", input=payload)
    contents: dict[str, bytes] = {}
    pos = 0
    for rel in rels:
        nl = out.find(b"\n", pos)
        if nl < 0:
            break
        header = out[pos:nl]
        pos = nl + 1
        if header.endswith(b" missing") or header.endswith(b" ambiguous"):
            continue
        parts = header.rsplit(b" ", 2)
        if len(parts) != 3:
            break
        size = int(parts[2])
        contents[rel] = out[pos : pos + size]
        pos += size + 1  # trailing LF after each object
    return contents


def index_entries(repo_root: Path, rels: Sequence[str], rev: str | None = None) -> list[Entry]:
    """Entries backed by blobs (index or ``rev``); content pre-loaded only where a rule reads it."""
    sizes = blob_sizes(repo_root, rels, rev)
    wanted = [rel for rel in rels if needs_content(rel, sizes.get(rel))]
    contents = blob_contents(repo_root, wanted, rev)
    return [Entry(rel=rel, size=sizes.get(rel), data=contents.get(rel)) for rel in rels]


# ---------------------------------------------------------------------------
# Working-tree entries
# ---------------------------------------------------------------------------


def _relative(path: Path, repo_root: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def worktree_entries(paths: Iterable[Path | str], repo_root: Path) -> list[Entry]:
    """Entries for working-tree paths (directories are walked; ``.git`` is skipped).

    Relative inputs are anchored at the current directory; paths outside ``repo_root``
    keep their own posix form (so the fixture allow-list never applies to them).
    """
    entries: list[Entry] = []
    seen: set[str] = set()

    def add(p: Path) -> None:
        rel = _relative(p, repo_root)
        if rel in seen:
            return
        seen.add(rel)
        size = p.stat().st_size if p.is_file() else None
        entries.append(Entry(rel=rel, size=size, path=p))

    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            for child in sorted(p.rglob("*")):
                if ".git" in child.parts or not child.is_file():
                    continue
                add(child)
        else:
            add(p)
    return entries


# ---------------------------------------------------------------------------
# Public scans
# ---------------------------------------------------------------------------


def scan(paths: Iterable[Path | str], repo_root: Path) -> list[Violation]:
    """Check working-tree ``paths`` (files or directories) against G1-G5."""
    return check_entries(worktree_entries(paths, repo_root))


def scan_staged(repo_root: Path) -> list[Violation]:
    """Check what ``git commit`` would record: staged A/C/M/R paths, read from the index."""
    rels = staged_paths(repo_root)
    return check_entries(index_entries(repo_root, rels))


def scan_tracked(repo_root: Path, rev: str | None = None) -> list[Violation]:
    """Check every tracked path (index) or every path in ``rev``'s tree — the per-commit
    primitive for the EP-163 history sweep (the walk itself lives there)."""
    rels = tracked_paths(repo_root, rev)
    return check_entries(index_entries(repo_root, rels, rev))


# ---------------------------------------------------------------------------
# Selfcheck
# ---------------------------------------------------------------------------


def check_ignored(repo_root: Path, probes: Sequence[str]) -> set[str]:
    """Subset of ``probes`` (strings, never files) that ``.gitignore`` ignores."""
    payload = b"\0".join(_encode_path(p) for p in probes) + b"\0"
    out = _git(repo_root, "check-ignore", "-z", "--stdin", input=payload, ok_codes=(0, 1))
    return {_decode_path(p) for p in out.split(b"\0") if p}


def check_binary_attr(repo_root: Path, probes: Sequence[str]) -> dict[str, str]:
    """``git check-attr binary`` value per probe (``set`` / ``unset`` / ``unspecified``)."""
    out = _git(repo_root, "check-attr", "-z", "binary", "--", *probes)
    fields = [_decode_path(f) for f in out.split(b"\0") if f]
    return {fields[i]: fields[i + 2] for i in range(0, len(fields) - 2, 3)}


def selfcheck(repo_root: Path) -> list[SelfcheckResult]:
    """Re-run the EP-0 probe list and check the hook wiring; no file is created or opened
    except ``.pre-commit-config.yaml`` and the installed hook script."""
    results: list[SelfcheckResult] = []
    ignored = check_ignored(repo_root, IGNORED_PROBES + TRACKED_PROBES)
    for probe in IGNORED_PROBES:
        ok = probe in ignored
        results.append(
            SelfcheckResult(f"gitignore:{probe}", ok, "ignored" if ok else "NOT ignored")
        )
    for probe in TRACKED_PROBES:
        ok = probe not in ignored
        results.append(
            SelfcheckResult(f"not-ignored:{probe}", ok, "not ignored" if ok else "IGNORED")
        )
    attrs = check_binary_attr(repo_root, BINARY_PROBES)
    for probe in BINARY_PROBES:
        value = attrs.get(probe, "unspecified")
        results.append(
            SelfcheckResult(f"gitattributes:{probe}", value == "set", f"binary: {value}")
        )
    cfg = repo_root / ".pre-commit-config.yaml"
    wired = cfg.is_file() and "id: mwh-guard" in cfg.read_text("utf-8", errors="replace")
    results.append(
        SelfcheckResult(
            "pre-commit-config",
            wired,
            "mwh-guard hook present" if wired else "missing or lacks the mwh-guard hook",
        )
    )
    hook_path = Path(
        _decode_path(_git(repo_root, "rev-parse", "--git-path", "hooks/pre-commit")).strip()
    )
    if not hook_path.is_absolute():
        hook_path = repo_root / hook_path
    installed = hook_path.is_file() and "pre-commit" in hook_path.read_text(
        "utf-8", errors="replace"
    )
    results.append(
        SelfcheckResult(
            "hook-installed",
            installed,
            "installed"
            if installed
            else "not installed - run: uv run --group dev pre-commit install",
            level="warn",
        )
    )
    return results


def selfcheck_ok(results: Iterable[SelfcheckResult]) -> bool:
    return all(r.ok for r in results if r.level == "fail")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _render_violations(violations: Sequence[Violation]) -> Any:
    from rich.table import Table

    table = Table(title="mwh guard - refused", show_lines=False, expand=False)
    table.add_column("rule", style="bold red", no_wrap=True)
    table.add_column("path", overflow="fold")
    table.add_column("line", justify="right", no_wrap=True)
    table.add_column("detail", overflow="fold")
    for v in violations:
        table.add_row(
            f"{v.rule} {v.title}", v.path, "" if v.line is None else str(v.line), v.detail
        )
    return table


def _render_selfcheck(results: Sequence[SelfcheckResult]) -> Any:
    from rich.table import Table

    table = Table(title="mwh guard --selfcheck", expand=False)
    table.add_column("check", overflow="fold")
    table.add_column("status", no_wrap=True)
    table.add_column("detail", overflow="fold")
    for r in results:
        status = "ok" if r.ok else ("WARN" if r.level == "warn" else "FAIL")
        style = "green" if r.ok else ("yellow" if r.level == "warn" else "bold red")
        table.add_row(r.id, f"[{style}]{status}[/]", r.detail)
    return table


def _emit_json(obj: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(obj, indent=2) + os.linesep)


def guard_command(
    ctx: typer.Context,
    paths: Annotated[
        list[Path] | None,
        typer.Argument(
            help="Working-tree files or directories to check (default: the staged changes).",
            show_default=False,
        ),
    ] = None,
    staged: Annotated[
        bool,
        typer.Option(
            "--staged", help="Check the staged changes as git would commit them (default)."
        ),
    ] = False,
    all_tracked: Annotated[
        bool,
        typer.Option("--all-tracked", help="Check every tracked path (git ls-files)."),
    ] = False,
    selfcheck_flag: Annotated[
        bool,
        typer.Option(
            "--selfcheck",
            help="Re-run the EP-0 .gitignore/.gitattributes probes and check the hook wiring.",
        ),
    ] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Print the report as JSON.")] = False,
) -> None:
    """Refuse data-shaped files, source material, notebook outputs, real MIMIC id bands
    and oversize blobs before they reach git (GOVERNANCE §3). Exit 0 clean, 1 refused, 2 usage."""
    from rich.markup import escape

    from mimicwarehouse.cli import console

    modes = [
        name
        for name, on in (
            ("paths", bool(paths)),
            ("--staged", staged),
            ("--all-tracked", all_tracked),
            ("--selfcheck", selfcheck_flag),
        )
        if on
    ]
    if len(modes) > 1:
        console.print(f"[bold red]mwh guard:[/] choose one of {', '.join(modes)}", highlight=False)
        raise typer.Exit(code=2)
    mode = modes[0] if modes else "--staged"

    try:
        repo_root = find_repo_root()
        if mode == "--selfcheck":
            results = selfcheck(repo_root)
            ok = selfcheck_ok(results)
            if json_output:
                _emit_json(
                    {
                        "mode": "selfcheck",
                        "repo_root": str(repo_root),
                        "checks": [r.as_dict() for r in results],
                        "ok": ok,
                    }
                )
            else:
                console.print(_render_selfcheck(results))
                console.print(
                    "mwh guard: selfcheck passed" if ok else "mwh guard: selfcheck FAILED",
                    style="bold" if ok else "bold red",
                )
            raise typer.Exit(code=0 if ok else 1)

        if mode == "paths":
            assert paths is not None
            missing = [str(p) for p in paths if not p.exists()]
            if missing:
                console.print(
                    f"[bold red]mwh guard:[/] no such path: {escape(', '.join(missing))}",
                    highlight=False,
                )
                raise typer.Exit(code=2)
            entries = worktree_entries(paths, repo_root)
        elif mode == "--all-tracked":
            entries = index_entries(repo_root, tracked_paths(repo_root))
        else:
            entries = index_entries(repo_root, staged_paths(repo_root))
        violations = check_entries(entries)
    except GuardError as exc:
        console.print(f"[bold red]mwh guard:[/] {escape(str(exc))}", highlight=False)
        raise typer.Exit(code=2) from None

    ok = not violations
    label = mode.lstrip("-")
    if json_output:
        _emit_json(
            {
                "mode": label,
                "repo_root": str(repo_root),
                "files_scanned": len(entries),
                "violations": [v.as_dict() for v in violations],
                "ok": ok,
            }
        )
    elif ok:
        console.print(
            f"mwh guard: clean ({len(entries)} file(s) scanned, {label})", style="bold green"
        )
    else:
        console.print(_render_violations(violations))
        files = len({v.path for v in violations})
        console.print(
            f"mwh guard: {len(violations)} violation(s) in {files} file(s) - commit refused "
            f"({len(entries)} file(s) scanned, {label})",
            style="bold red",
        )
    raise typer.Exit(code=0 if ok else 1)


__all__ = [
    "ALLOW_PRAGMA",
    "BANDS",
    "DATA_EXTENSIONS",
    "FIXTURE_DIR",
    "FIXTURE_EXTENSIONS",
    "FIXTURE_ID_FLOOR",
    "HADM_BAND",
    "ID_TOKEN",
    "MAX_FILE_BYTES",
    "STAY_BAND",
    "SUBJECT_BAND",
    "TEXT_EXTENSIONS",
    "Entry",
    "GuardError",
    "SelfcheckResult",
    "Violation",
    "band_of",
    "check_entries",
    "check_entry",
    "data_extension",
    "find_repo_root",
    "guard_command",
    "id_band_hits",
    "index_entries",
    "mask",
    "notebook_problem",
    "scan",
    "scan_staged",
    "scan_tracked",
    "selfcheck",
    "selfcheck_ok",
    "staged_paths",
    "tracked_paths",
    "worktree_entries",
]
