"""Vendor MIT-LCP/mimic-code into the package at a pinned commit (EP-8; D-19, GOVERNANCE §10, §12).

mimic-code (MIT) is the backbone the warehouse reuses instead of re-deriving: its Postgres
``create.sql`` is the typed DDL EP-9 transcribes into the YAML schema contract, its
``validate.sql`` row counts are what EP-10 reconciles the raw CSVs against (D-26), and its
``concepts_duckdb/`` scripts are what EP-37 executes into ``mimiciv_derived``. This module
copies an **explicit allow-list** (:data:`ALLOW_LIST`) out of a local clone into
``src/mimicwarehouse/concepts/vendor/mimic-code/`` — preserving upstream relative paths (so
EP-38 patches are upstream-relative diffs), normalising line endings to LF, refusing every
``.csv`` / ``.gz`` / binary blob — and writes ``vendor/VENDOR.json`` (the pin every later run
manifest cites). Blobs are read from the clone's **git object store at the pinned sha**
(``git cat-file --batch``), never from the working tree, so a CRLF checkout (``core.autocrlf``)
or a stray local edit cannot leak into the vendored copy.

Guard interplay (EP-4 rule G4). Some ``validate.sql`` row counts are isolated 8-digit
integers starting 1, 2 or 3 — exactly what ``mwh guard`` flags as a real MIMIC id. The guard
is never weakened: instead every ``validate.sql`` line the guard would flag gets the trailing
pragma :data:`GUARD_PRAGMA` (``-- mwh-guard: allow …`` exempts the whole line; kind
``guard_pragma``). In any *other* ``.sql`` file such a token **is** an identifier — upstream
``mimic-iv/concepts/treatment/ventilation.sql`` carries two debugging comments of the form
``stay_id = <8 digits>`` — so it is replaced in place by :data:`REDACTED` (kind
``id_redaction``; GOVERNANCE §3 forbids committing real-band ids and the "row count" pragma
would be a lie). Both kinds are recorded under ``local_edits`` with the upstream and the
vendored ``sha256_lf`` and the line numbers; every other file stays byte-identical to upstream
(LF aside). A non-``.sql`` file the guard would flag is an error (no comment syntax we can
promise) — none exists at the pinned commit.

Run it (workspace root)::

    uv run --group dev python -m mimicwarehouse.concepts.vendoring \\
        --sha <sha> --src "$env:TEMP\\mimic-code"
    uv run poe vendor-mimic-code --sha <sha>     # same; --src defaults to %TEMP%\\mimic-code

Re-running with the same sha is a no-op (``git diff --stat`` empty): ``vendored_on`` is kept
from the existing manifest when the sha is unchanged. The clone is made once with
``git clone --filter=blob:none https://github.com/MIT-LCP/mimic-code.git "$env:TEMP\\mimic-code"``
and left in place (EP-9 reuses it); ``%TEMP%`` is not on the Malwarebytes allow list, so a
clone/copy that dies mid-way is checked against Malwarebytes Quarantine first (Risk 12, D-42).

Import cost: stdlib + :mod:`mimicwarehouse.guard` (stdlib + typer). No data is touched.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from mimicwarehouse import guard

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

UPSTREAM_URL = "https://github.com/MIT-LCP/mimic-code"
#: ``vendor/`` under this package (``src/mimicwarehouse/concepts/vendor``).
VENDOR_DIR = Path(__file__).resolve().parent / "vendor"
#: The vendored tree lives under ``vendor/mimic-code/`` with upstream-relative paths.
TREE_DIRNAME = "mimic-code"
MANIFEST_NAME = "VENDOR.json"
#: Appended to every ``validate.sql`` line ``mwh guard`` (G4) would otherwise flag.
GUARD_PRAGMA = " -- mwh-guard: allow (row count, not an id)"
#: Replaces a real-band id token in any other ``.sql`` file (upstream debugging comments).
REDACTED = "<mwh: id redacted>"
#: ``local_edits[].kind`` values.
EDIT_PRAGMA = "guard_pragma"
EDIT_REDACTION = "id_redaction"
_ID_TOKEN_BYTES = re.compile(guard.ID_TOKEN.pattern.encode(), re.ASCII)
#: The MIMIC-IV release the pinned ``validate.sql`` must target (GOVERNANCE §1).
EXPECTED_MIMIC_IV_VERSION = "3.1"
#: Refused by name, whatever the content (``.gitignore`` / ``mwh guard`` G1 refuse ``.csv`` and
#: ``.csv.gz``; bare ``.gz`` and the rest are refused *only* here and by ``test_ep08``).
REFUSED_SUFFIXES: tuple[str, ...] = (
    ".csv",
    ".csv.gz",
    ".gz",
    ".zip",
    ".7z",
    ".tar",
    ".parquet",
    ".ipynb",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".pdf",
    ".pkl",
    ".db",
    ".duckdb",
)
#: Only these may be vendored (LICENSE has no suffix and is allowed by name).
ALLOWED_SUFFIXES: tuple[str, ...] = (".sql", ".sh")
GIT_TIMEOUT_S = 300
DEFAULT_SRC = Path(os.environ.get("TEMP") or tempfile.gettempdir()) / "mimic-code"

_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_MIMIC_IV_VERSION = re.compile(r"MIMIC-IV(?:-ED|-Note)?\s+v(\d+\.\d+)", re.IGNORECASE)
_DUCKDB_LTS = re.compile(r"(\d+\.\d+\.(?:x|\d+))\s+LTS\s+line\s*\(currently\s+(\d+\.\d+\.\d+)\)")
_DUCKDB_ANY = re.compile(r"duckdb\D{0,40}?(\d+\.\d+\.\d+)", re.IGNORECASE)


class VendoringError(RuntimeError):
    """A refusal or a broken precondition; the message is printed and the exit code is 1."""


# ---------------------------------------------------------------------------
# Allow-list
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AllowRule:
    """One entry of the EP-8 allow-list: a single upstream file or a tree by suffix."""

    path: str
    why: str
    tree: bool = False
    suffixes: tuple[str, ...] = (".sql",)
    required: bool = True

    def matches(self, rel: str) -> bool:
        if not self.tree:
            return rel == self.path
        return rel.startswith(self.path.rstrip("/") + "/") and rel.endswith(self.suffixes)


ALLOW_LIST: tuple[AllowRule, ...] = (
    AllowRule("LICENSE", "MIT license text — attribution (GOVERNANCE §10, NOTICE)"),
    AllowRule(
        "mimic-iv/buildmimic/postgres/create.sql",
        "typed DDL for hosp + icu — EP-9 transcribes it into the YAML schema contract",
    ),
    AllowRule("mimic-iv/buildmimic/postgres/load.sql", "COPY column order per table (EP-17)"),
    AllowRule("mimic-iv/buildmimic/postgres/constraint.sql", "primary / unique keys (EP-9 keys)"),
    AllowRule("mimic-iv/buildmimic/postgres/index.sql", "upstream index set (EP-21 reference)"),
    AllowRule(
        "mimic-iv/buildmimic/postgres/validate.sql",
        "MIMIC-IV row counts — EP-10 reconciles the raw CSVs against them (D-26)",
    ),
    AllowRule(
        "mimic-iv/buildmimic/duckdb/build_mimic.sh",
        "DuckDB build script — loader precedent for EP-17 (COPY options, resumable progress table)",
        suffixes=(".sh",),
    ),
    AllowRule("mimic-iv-ed/buildmimic/postgres/create.sql", "MIMIC-IV-ED DDL", required=False),
    AllowRule("mimic-iv-ed/buildmimic/postgres/load.sql", "MIMIC-IV-ED COPY", required=False),
    AllowRule("mimic-iv-ed/buildmimic/postgres/index.sql", "MIMIC-IV-ED indexes", required=False),
    AllowRule(
        "mimic-iv-ed/buildmimic/postgres/validate.sql", "MIMIC-IV-ED row counts", required=False
    ),
    AllowRule("mimic-iv-note/buildmimic/postgres/create.sql", "MIMIC-IV-Note DDL"),
    AllowRule("mimic-iv-note/buildmimic/postgres/load.sql", "MIMIC-IV-Note COPY", required=False),
    AllowRule(
        "mimic-iv/concepts_duckdb",
        "sqlglot-transpiled DuckDB concepts — executed by EP-37 into mimiciv_derived",
        tree=True,
    ),
    AllowRule(
        "mimic-iv/concepts",
        "BigQuery concept sources — reference only; EP-38 ports fixes from them",
        tree=True,
    ),
)

#: Trees whose non-selected files are enumerated under ``excluded`` (so the manifest says what
#: was seen and left behind), plus whole trees excluded by policy.
CONSIDERED_TREES: tuple[str, ...] = (
    "mimic-iv/buildmimic/postgres",
    "mimic-iv/buildmimic/duckdb",
    "mimic-iv-ed/buildmimic/postgres",
    "mimic-iv-note/buildmimic/postgres",
    "mimic-iv/concepts_duckdb",
    "mimic-iv/concepts",
)
EXCLUDED_TREES: tuple[tuple[str, str], ...] = (
    (
        "mimic-iv/concepts_postgres",
        "Postgres transpilation of the concepts; concepts_duckdb/ is what EP-37 executes",
    ),
    ("mimic-iii", "MIMIC-III — out of scope for this warehouse"),
    ("mimic-iv/notebooks", "notebooks may contain demo ids / outputs (GOVERNANCE §3, guard G3)"),
    ("mimic-iv/buildmimic/postgres/docker", "container build files — not needed"),
    ("mimic-iv/buildmimic/duckdb/docker", "container build files — not needed"),
)

#: Recorded, not fixed (brief EP-8 § Context; EP-37/EP-38 act on them).
KNOWN_UPSTREAM_ISSUES: tuple[str, ...] = (
    "concepts_duckdb/ is auto-generated (sqlglot) from concepts/ and may lag it — upstream "
    "regeneration PR #2157 open as of 2026-08; EP-38 diffs the two trees before patching.",
    "Open upstream concept-logic PRs (2026-08): SIRS wbc guard, lab valueuom handling, "
    "Charlson, APS-III — EP-38 ports what EP-37's count-pinning tests need.",
    "mimic-iv/buildmimic/duckdb/README.md targets the DuckDB 1.4.x LTS line; this project pins "
    "duckdb==1.5.5 (pyproject) — DuckDB 1.5 compatibility fixes are EP-38's job.",
    "No ED (mimic-iv-ed) or Note (mimic-iv-note) concepts exist upstream; ours are new work.",
    "No mimic-iv/CHANGELOG file exists at the pinned commit — the MIMIC-IV version targeted is "
    "read from the header comment of mimic-iv/buildmimic/postgres/validate.sql.",
    "mimic-iv-note/buildmimic/postgres/validate.sql carries no version header; the ED "
    "validate.sql states MIMIC-IV-ED v2.2.",
)


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested without a clone)
# ---------------------------------------------------------------------------


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def normalise_lf(data: bytes) -> bytes:
    """CRLF → LF (lone CR is left alone — none occurs upstream)."""
    return data.replace(b"\r\n", b"\n")


def refusal_reason(rel: str, data: bytes) -> str | None:
    """Why ``rel`` may not be vendored, or None. Name first, then content (binary)."""
    name = PurePosixPath(rel).name.lower()
    for suffix in REFUSED_SUFFIXES:
        if name.endswith(suffix):
            return f"refused suffix {suffix!r}"
    if name != "license" and not name.endswith(ALLOWED_SUFFIXES):
        return "not an allowed suffix (.sql / .sh / LICENSE)"
    if b"\0" in data:
        return "binary content (NUL byte)"
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return "binary content (not UTF-8)"
    return None


def guard_flagged_lines(data: bytes) -> set[int]:
    """1-based line numbers ``mwh guard`` (G4) would flag in this LF text."""
    return {line_no for line_no, _band, _count, _mask in guard.id_band_hits(data)}


def apply_guard_pragma(data: bytes) -> tuple[bytes, list[int]]:
    """Append :data:`GUARD_PRAGMA` to every line G4 would flag; return the new text + lines."""
    flagged = guard_flagged_lines(data)
    if not flagged:
        return data, []
    out: list[bytes] = []
    trailing_newline = data.endswith(b"\n")
    for no, line in enumerate(data.split(b"\n"), start=1):
        if no in flagged:
            line = line.rstrip() + GUARD_PRAGMA.encode()
        out.append(line)
    text = b"\n".join(out)
    if trailing_newline and not text.endswith(b"\n"):
        text += b"\n"
    return text, sorted(flagged)


def strip_guard_pragma(data: bytes) -> bytes:
    """Inverse of :func:`apply_guard_pragma` for tests: drop the trailing pragma comments."""
    pragma = GUARD_PRAGMA.encode()
    return b"\n".join(
        line[: -len(pragma)] if line.endswith(pragma) else line for line in data.split(b"\n")
    )


def redact_band_ids(data: bytes) -> tuple[bytes, list[int]]:
    """Replace every real-band id token G4 would flag with :data:`REDACTED`; return text + lines.

    Used for the non-``validate.sql`` files, where an isolated 8-digit token in the real bands
    *is* an identifier (upstream ``concepts/treatment/ventilation.sql`` carries two debugging
    comments of the form ``stay_id = <8 digits>``); GOVERNANCE §3 forbids committing it, and
    the "row count" pragma would be a lie. Line count is preserved (EP-38 diffs stay aligned).
    """
    flagged = guard_flagged_lines(data)
    if not flagged:
        return data, []
    marker = REDACTED.encode()

    def redact(match: re.Match[bytes]) -> bytes:
        return marker if guard.band_of(int(match.group())) is not None else match.group()

    out: list[bytes] = []
    for no, line in enumerate(data.split(b"\n"), start=1):
        if no in flagged:
            line = _ID_TOKEN_BYTES.sub(redact, line)
        out.append(line)
    return b"\n".join(out), sorted(flagged)


def is_row_count_file(rel: str) -> bool:
    """``validate.sql`` files hold expected row counts — the only place the pragma is used."""
    return PurePosixPath(rel).name == "validate.sql"


def local_edit_for(rel: str, upstream_lf: bytes) -> tuple[bytes, str | None, list[int]]:
    """``(content, edit_kind, lines)`` for one LF-normalised upstream file.

    ``.sql`` row-count files → :data:`EDIT_PRAGMA`; other ``.sql`` → :data:`EDIT_REDACTION`;
    anything else the guard would flag is a :class:`VendoringError` (no comment syntax we can
    promise, and nothing at the pinned commit needs it).
    """
    if rel.endswith(".sql"):
        if is_row_count_file(rel):
            content, lines = apply_guard_pragma(upstream_lf)
            return content, (EDIT_PRAGMA if lines else None), lines
        content, lines = redact_band_ids(upstream_lf)
        return content, (EDIT_REDACTION if lines else None), lines
    if guard.is_text_candidate(rel) and guard_flagged_lines(upstream_lf):
        raise VendoringError(f"{rel}: mwh guard G4 would flag it and it is not SQL — refusing")
    return upstream_lf, None, []


def blob_url(sha: str, rel: str) -> str:
    return f"{UPSTREAM_URL}/blob/{sha}/{rel}"


def tree_url(sha: str, rel: str) -> str:
    return f"{UPSTREAM_URL}/tree/{sha}/{rel}"


def exclusion_reason(rel: str) -> str:
    name = PurePosixPath(rel).name.lower()
    if name.startswith("readme") or name.endswith((".md", ".ipynb")):
        return "documentation / notebook — may contain demo ids; not needed by the code"
    if name.endswith((".csv", ".csv.gz")):
        return (
            "concept_map CSV — .gitignore and mwh guard G1 refuse *.csv outside tests/fixtures/; "
            "EP-138 fetches it into ext/ at this same sha"
        )
    if "docker" in PurePosixPath(rel).parts:
        return "container build files — not needed"
    if name.endswith(".sh"):
        return "build helper for another engine — not in the EP-8 allow-list"
    return "not in the EP-8 allow-list"


def mimic_iv_version_from(validate_sql: bytes) -> str | None:
    """``-- of MIMIC-IV v3.1`` in the header comment of ``validate.sql`` → ``"3.1"``."""
    head = validate_sql[:600].decode("utf-8", errors="replace")
    m = _MIMIC_IV_VERSION.search(head)
    return m.group(1) if m else None


def duckdb_version_from(readme: bytes) -> str:
    text = readme.decode("utf-8", errors="replace")
    m = _DUCKDB_LTS.search(text)
    if m:
        return f"{m.group(1)} LTS (currently {m.group(2)})"
    m = _DUCKDB_ANY.search(text)
    return m.group(1) if m else "unknown"


# ---------------------------------------------------------------------------
# git plumbing over the clone
# ---------------------------------------------------------------------------


def _git(src: Path, *args: str, input_bytes: bytes | None = None) -> bytes:
    try:
        proc = subprocess.run(
            ["git", "-C", str(src), *args],
            input=input_bytes,
            capture_output=True,
            timeout=GIT_TIMEOUT_S,
            check=False,
        )
    except FileNotFoundError as exc:  # pragma: no cover - git is a hard requirement
        raise VendoringError("git is not on PATH") from exc
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", errors="replace").strip()
        raise VendoringError(f"git {' '.join(args[:2])} failed in {src}: {err}")
    return proc.stdout


def resolve_commit(src: Path, sha: str) -> tuple[str, str]:
    """``(full_sha, commit_date_iso)`` for ``sha`` in the clone at ``src``."""
    if not (src / ".git").exists():
        raise VendoringError(
            f"{src} is not a git clone — run: git clone --filter=blob:none "
            f'{UPSTREAM_URL}.git "{src}"'
        )
    full = _git(src, "rev-parse", "--verify", f"{sha}^{{commit}}").decode().strip()
    if not _SHA40.match(full):
        raise VendoringError(f"could not resolve {sha!r} to a commit in {src}")
    date = _git(src, "show", "-s", "--format=%cI", full).decode().strip()
    return full, date


def list_tree(src: Path, sha: str, rel: str) -> list[str]:
    """Every blob path under ``rel`` (recursive) at ``sha``; ``[]`` if the tree is absent."""
    try:
        out = _git(src, "ls-tree", "-r", "--name-only", "-z", sha, "--", rel)
    except VendoringError:
        return []
    return [p.decode("utf-8") for p in out.split(b"\0") if p]


def read_blobs(src: Path, sha: str, rels: Sequence[str]) -> dict[str, bytes]:
    """Exact upstream bytes of ``rels`` at ``sha`` in one ``git cat-file --batch`` call."""
    if not rels:
        return {}
    request = b"".join(f"{sha}:{rel}\n".encode() for rel in rels)
    out = _git(src, "cat-file", "--batch", input_bytes=request)
    blobs: dict[str, bytes] = {}
    pos = 0
    for rel in rels:
        nl = out.index(b"\n", pos)
        header = out[pos:nl].decode()
        pos = nl + 1
        parts = header.split()
        if len(parts) != 3 or parts[1] != "blob":
            raise VendoringError(f"{rel}: not a blob at {sha[:12]} ({header})")
        size = int(parts[2])
        blobs[rel] = out[pos : pos + size]
        pos += size + 1  # trailing newline after each object
    return blobs


# ---------------------------------------------------------------------------
# Manifest construction
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class VendoredFile:
    rel: str
    content: bytes  # LF; pragma / redaction applied
    upstream_sha256_lf: str
    edit_kind: str | None = None  # EDIT_PRAGMA | EDIT_REDACTION | None
    edit_lines: list[int] = field(default_factory=list)

    @property
    def sha256_lf(self) -> str:
        return sha256_hex(self.content)

    @property
    def local_edit(self) -> bool:
        return self.edit_kind is not None


EDIT_REASONS: dict[str, str] = {
    EDIT_PRAGMA: "mwh guard G4 pragma appended to expected-row-count lines that look like real "
    "MIMIC ids (isolated 8-digit integers starting 1/2/3); content otherwise byte-identical "
    "to upstream (LF aside)",
    EDIT_REDACTION: "real-band id tokens in upstream debugging comments replaced in place by "
    f"'{REDACTED}' (GOVERNANCE §3: no real MIMIC ids in git); line count and everything else "
    "byte-identical to upstream (LF aside)",
}


@dataclass(slots=True)
class Plan:
    sha: str
    commit_date: str
    vendored_on: str
    files: list[VendoredFile]
    excluded: list[dict[str, str]]
    mimic_iv_version: str
    mimic_iv_ed_version: str | None
    duckdb_version_readme: str

    def manifest(self) -> dict[str, Any]:
        return {
            "upstream_url": UPSTREAM_URL,
            "upstream_commit": self.sha,
            "commit_date": self.commit_date,
            "vendored_on": self.vendored_on,
            "vendored_by": "mimicwarehouse.concepts.vendoring (EP-8)",
            "mimic_iv_version_targeted": self.mimic_iv_version,
            "mimic_iv_version_source": "mimic-iv/buildmimic/postgres/validate.sql header "
            "(no mimic-iv/CHANGELOG upstream at this commit)",
            "mimic_iv_ed_version_targeted": self.mimic_iv_ed_version,
            "duckdb_version_upstream_readme": self.duckdb_version_readme,
            "duckdb_version_pinned_here": _pinned_duckdb_version(),
            "tree": TREE_DIRNAME,
            "line_endings": "LF (sha256_lf = sha256 of the LF-normalised bytes)",
            "guard_pragma": GUARD_PRAGMA.strip(),
            "redaction_marker": REDACTED,
            "file_count": len(self.files),
            "files": [
                {"path": f.rel, "sha256_lf": f.sha256_lf, "bytes": len(f.content)}
                for f in self.files
            ],
            "local_edits": [
                {
                    "path": f.rel,
                    "kind": f.edit_kind,
                    "upstream_sha256_lf": f.upstream_sha256_lf,
                    "sha256_lf": f.sha256_lf,
                    "lines": f.edit_lines,
                    "reason": EDIT_REASONS[f.edit_kind or ""],
                }
                for f in self.files
                if f.local_edit
            ],
            "known_upstream_issues": list(KNOWN_UPSTREAM_ISSUES),
            "excluded": self.excluded,
        }


def _pinned_duckdb_version() -> str:
    """The ``duckdb==x.y.z`` pin in the workspace pyproject (informational)."""
    pyproject = Path(__file__).resolve().parents[3] / "pyproject.toml"
    try:
        m = re.search(r'"duckdb==([0-9.]+)"', pyproject.read_text(encoding="utf-8"))
    except OSError:  # pragma: no cover - wheel install without the pyproject
        return "unknown"
    return m.group(1) if m else "unknown"


def select_files(src: Path, sha: str) -> tuple[list[str], list[str]]:
    """``(selected, seen_but_excluded)`` upstream paths under the allow-list at ``sha``."""
    seen: list[str] = []
    for tree in CONSIDERED_TREES:
        seen.extend(list_tree(src, sha, tree))
    seen_set = set(seen)
    selected: list[str] = []
    for rule in ALLOW_LIST:
        if rule.tree:
            hits = [rel for rel in seen if rule.matches(rel)]
            if not hits and rule.required:
                raise VendoringError(f"allow-list tree {rule.path!r} is empty at {sha[:12]}")
            selected.extend(hits)
        elif rule.path in seen_set or _blob_exists(src, sha, rule.path):
            selected.append(rule.path)
        elif rule.required:
            raise VendoringError(f"required upstream file {rule.path!r} missing at {sha[:12]}")
    selected = sorted(dict.fromkeys(selected))
    excluded = sorted(rel for rel in seen_set if rel not in set(selected))
    return selected, excluded


def _blob_exists(src: Path, sha: str, rel: str) -> bool:
    try:
        _git(src, "cat-file", "-e", f"{sha}:{rel}")
    except VendoringError:
        return False
    return True


def build_plan(
    src: Path,
    sha: str,
    *,
    vendored_on: str | None = None,
    previous: dict[str, Any] | None = None,
) -> Plan:
    """Read the allow-list at ``sha`` from ``src`` and compute what would be written."""
    full_sha, commit_date = resolve_commit(src, sha)
    selected, excluded_rels = select_files(src, full_sha)
    blobs = read_blobs(src, full_sha, selected)

    files: list[VendoredFile] = []
    for rel in selected:
        raw = blobs[rel]
        reason = refusal_reason(rel, raw)
        if reason is not None:
            raise VendoringError(f"refusing to vendor {rel}: {reason}")
        upstream_lf = normalise_lf(raw)
        content, kind, lines = local_edit_for(rel, upstream_lf)
        files.append(VendoredFile(rel, content, sha256_hex(upstream_lf), kind, lines))

    validate = blobs.get("mimic-iv/buildmimic/postgres/validate.sql", b"")
    version = mimic_iv_version_from(validate)
    if version != EXPECTED_MIMIC_IV_VERSION:
        raise VendoringError(
            f"validate.sql at {full_sha[:12]} targets MIMIC-IV v{version or '?'}, not "
            f"v{EXPECTED_MIMIC_IV_VERSION}: walk back/forward to the nearest commit whose "
            "validate.sql does and pin that one instead (say so in VENDOR.json)"
        )
    ed_validate = blobs.get("mimic-iv-ed/buildmimic/postgres/validate.sql")
    ed_version = mimic_iv_version_from(ed_validate) if ed_validate else None
    readme = read_blobs(src, full_sha, ["mimic-iv/buildmimic/duckdb/README.md"]).get(
        "mimic-iv/buildmimic/duckdb/README.md", b""
    )

    covered = tuple(tree + "/" for tree, _why in EXCLUDED_TREES)
    excluded: list[dict[str, str]] = [
        {
            "path": rel,
            "kind": "file",
            "reason": exclusion_reason(rel),
            "url": blob_url(full_sha, rel),
        }
        for rel in excluded_rels
        if not rel.startswith(covered)
    ]
    excluded.extend(
        {"path": tree + "/", "kind": "tree", "reason": why, "url": tree_url(full_sha, tree)}
        for tree, why in EXCLUDED_TREES
    )
    excluded.sort(key=lambda e: e["path"])

    if vendored_on is None:
        if previous and previous.get("upstream_commit") == full_sha and previous.get("vendored_on"):
            vendored_on = str(previous["vendored_on"])
        else:
            vendored_on = dt.date.today().isoformat()

    return Plan(
        sha=full_sha,
        commit_date=commit_date,
        vendored_on=vendored_on,
        files=files,
        excluded=excluded,
        mimic_iv_version=version,
        mimic_iv_ed_version=ed_version,
        duckdb_version_readme=duckdb_version_from(readme),
    )


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def load_manifest(vendor_dir: Path = VENDOR_DIR) -> dict[str, Any] | None:
    path = vendor_dir / MANIFEST_NAME
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def write_plan(plan: Plan, vendor_dir: Path = VENDOR_DIR) -> list[Path]:
    """Replace ``vendor_dir/mimic-code`` with the plan's files and write ``VENDOR.json``."""
    tree = vendor_dir / TREE_DIRNAME
    if tree.exists():
        shutil.rmtree(tree)
    written: list[Path] = []
    for f in plan.files:
        target = tree / Path(*f.rel.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(f.content)
        written.append(target)
    manifest_path = vendor_dir / MANIFEST_NAME
    manifest_path.write_bytes((json.dumps(plan.manifest(), indent=2) + "\n").encode("utf-8"))
    written.append(manifest_path)
    return written


def guard_violations(vendor_dir: Path = VENDOR_DIR) -> list[guard.Violation]:
    """``mwh guard`` over the vendored tree (G1-G5) — must be empty after writing."""
    repo_root = guard.find_repo_root(vendor_dir)
    return guard.scan([vendor_dir], repo_root)


def summary_lines(plan: Plan, written: Iterable[Path] | None) -> list[str]:
    n_edit = sum(1 for f in plan.files if f.local_edit)
    lines = [
        f"mimic-code {plan.sha} ({plan.commit_date}); MIMIC-IV v{plan.mimic_iv_version}"
        + (f", ED v{plan.mimic_iv_ed_version}" if plan.mimic_iv_ed_version else "")
        + f"; DuckDB upstream README {plan.duckdb_version_readme}",
        f"{len(plan.files)} files selected, {len(plan.excluded)} excluded entries, "
        f"{n_edit} local edit(s)",
    ]
    for f in plan.files:
        if f.local_edit:
            lines.append(f"  {f.edit_kind}: {f.rel} lines {f.edit_lines}")
    if written is None:
        lines.append("dry run - nothing written")
    else:
        lines.append(f"wrote {sum(1 for _ in written)} files under {VENDOR_DIR}")
    return lines


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m mimicwarehouse.concepts.vendoring",
        description="Vendor MIT-LCP/mimic-code (allow-listed files) into the package "
        "at a pinned sha.",
    )
    p.add_argument("--sha", required=True, help="upstream commit to pin (any git revision)")
    p.add_argument(
        "--src",
        type=Path,
        default=DEFAULT_SRC,
        help=f"local clone of {UPSTREAM_URL} (default: %%TEMP%%\\mimic-code)",
    )
    p.add_argument(
        "--dest", type=Path, default=VENDOR_DIR, help="vendor directory (default: the package's)"
    )
    p.add_argument(
        "--vendored-on",
        default=None,
        help="ISO date to record (default: keep the existing one when the sha is unchanged, "
        "else today)",
    )
    p.add_argument("--dry-run", action="store_true", help="compute and print, write nothing")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.vendored_on is not None:
            dt.date.fromisoformat(args.vendored_on)
        plan = build_plan(
            args.src,
            args.sha,
            vendored_on=args.vendored_on,
            previous=load_manifest(args.dest),
        )
        written: list[Path] | None = None
        if not args.dry_run:
            written = write_plan(plan, args.dest)
            violations = guard_violations(args.dest)
            if violations:
                for v in violations:
                    print(f"guard {v.rule}: {v.path}: {v.detail}", file=sys.stderr)
                raise VendoringError(
                    f"mwh guard refuses {len(violations)} vendored path(s) — nothing committed"
                )
    except (VendoringError, ValueError) as exc:
        print(f"vendoring: error: {exc}", file=sys.stderr)
        return 1
    for line in summary_lines(plan, written):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
