"""PreToolUse command-string guard for Claude Code sessions (EP-165; GOV-1/GOV-2, D-39, D-43).

Registered in ``.claude/settings.json`` under ``hooks.PreToolUse`` (matcher
``Bash|PowerShell|Read|Grep|Glob``) and run as ``<workspace>\\.venv\\Scripts\\python.exe
<this file>`` — a *file* script reading the hook JSON from stdin (data, not code: this is
neither a heredoc nor ``python -``, so D-42(1) is not violated, and the interpreter is the
Malwarebytes-allow-listed venv python, D-38/D-42(4)).

Rule (owner decision 2026-08-18, D-43 item 2): **deny** a tool call whose command string /
target path mentions a protected data token — ``mimicdata``, ``source material/<anything
but README.md>``, ``.csv``/``.csv.gz``, ``.parquet``, ``.duckdb`` — unless the command
starts with an allow-listed read-only project launcher (``uv run [--project mimicwarehouse]
[--group <g>] mwh|pytest|poe|pre-commit``, ``git ``, ``ls ``, ``mwh ``, ``pre-commit``).
Nested interpreters (``sh -c`` / ``bash -c`` / ``python -c``) mentioning those tokens are
denied even behind an allow-listed prefix, and so are git's ``--no-index`` modes, which
read arbitrary file content (EP-33, D-45 item 4). For the Read tool the ``file_path`` is
checked; for Glob the pattern+path (a filesystem glob); for Grep the ``path`` **and** the
``glob`` (both filesystem selectors) — never the search *pattern*, which is content, so
``Grep("mimicdata", path="DESIGN.md")`` stays legal doc work (GOV-12: use the Read/Grep
tools, not shell readers, for docs mentioning the tokens).

This is a mitigation, not a guarantee (a string filter is trivially evaded): GOVERNANCE §4
prose and ``safe_query`` (EP-30) remain the primary controls. Decisions — never tool
output, never file content — are appended to ``%LOCALAPPDATA%\\Temp\\claude\\mwh-pretool.log``
(one append per call, D-42(2)-safe). Any internal error fails **open** (exit 0, no output)
so a harness change can never brick a session; the deny path emits the documented
``hookSpecificOutput.permissionDecision`` JSON. Budget: ≤ 200 ms per call (stdlib only).
"""

from __future__ import annotations

import json
import os
import re
import sys
import time

#: Protected data tokens (the brief's regex, verbatim).
DATA_RE = re.compile(
    r"(?i)mimicdata|source material[/\\](?!README\.md)|\.csv(\.gz)?\b|\.parquet\b|\.duckdb"
)
#: Read-only project launchers a data-token mention is legitimate for (GOV-2 corrected fix:
#: every launcher form the briefs actually use, options before the program name).
ALLOW_RE = re.compile(
    r"^\s*(?:git\s|ls\s|mwh\s|pre-commit\b"
    r"|uv run\s+(?:--project mimicwarehouse\s+)?(?:--group [A-Za-z0-9_-]+\s+)?"
    r"(?:mwh|pytest|poe|pre-commit)\b)"
)
#: Nested interpreters an allow-listed prefix must NOT rescue.
NESTED_RE = re.compile(r"(?i)\b(?:sh -c|bash -c|python3? -c)\b")
#: git modes that print arbitrary file content (``--no-index`` diff / grep): the bare
#: ``git`` launcher rescue does not extend to them (EP-33, D-45 item 4).
GIT_CONTENT_RE = re.compile(r"(?i)^\s*git\b.*\s--no-index\b")

#: Tool name → the ``tool_input`` fields that are path-like and therefore checked.
CHECKED_FIELDS: dict[str, tuple[str, ...]] = {
    "Bash": ("command",),
    "PowerShell": ("command",),
    "Read": ("file_path",),
    "Grep": ("path", "glob"),  # both filesystem selectors; never the pattern (content, doc work)
    "Glob": ("pattern", "path"),  # a Glob pattern IS a filesystem path
}

DENY_REASON = (
    "mwh pretool guard: the command/path mentions protected data tokens "
    "(mimicdata / source material / .csv / .parquet / .duckdb) and is not an "
    "allow-listed read-only project command. All data access goes through "
    "`uv run mwh sql` / safe_query (GOVERNANCE §4, CLAUDE.md §2); for docs that merely "
    "mention these tokens use the Read/Grep tools, not shell readers."
)

LOG_SNIPPET_CHARS = 200


def decide(tool_name: str, tool_input: dict[str, object]) -> tuple[bool, str]:
    """(deny?, checked-text) for one hook payload. Unknown tools are allowed."""
    fields = CHECKED_FIELDS.get(tool_name)
    if fields is None:
        return False, ""
    text = " ".join(str(tool_input.get(f, "") or "") for f in fields).strip()
    if not text or not DATA_RE.search(text):
        return False, text
    if (
        tool_name in ("Bash", "PowerShell")
        and ALLOW_RE.match(text)
        and not NESTED_RE.search(text)
        and not GIT_CONTENT_RE.search(text)
    ):
        return False, text
    return True, text


def log_decision(tool_name: str, denied: bool, text: str) -> None:
    """One appended line: timestamp, tool, verdict, first 200 chars of the command/path.
    Never tool output, never file content. Logging failures are swallowed (the hook must
    never break a tool call because a log file was locked)."""
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        return
    log_dir = os.path.join(local, "Temp", "claude")
    try:
        os.makedirs(log_dir, exist_ok=True)
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
        snippet = text[:LOG_SNIPPET_CHARS].replace("\n", " ")
        with open(
            os.path.join(log_dir, "mwh-pretool.log"), "a", encoding="utf-8", errors="replace"
        ) as fh:
            fh.write(f"{stamp} {tool_name} {'DENY' if denied else 'allow'} {snippet}\n")
    except OSError:
        pass


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        tool_name = str(payload.get("tool_name", ""))
        tool_input = payload.get("tool_input")
        if not isinstance(tool_input, dict):
            tool_input = {}
        denied, text = decide(tool_name, tool_input)
        log_decision(tool_name, denied, text)
        if denied:
            sys.stdout.write(
                json.dumps(
                    {
                        "hookSpecificOutput": {
                            "hookEventName": "PreToolUse",
                            "permissionDecision": "deny",
                            "permissionDecisionReason": DENY_REASON,
                        }
                    }
                )
            )
        return 0
    except Exception:  # fail OPEN: a guard bug must never brick the session
        return 0


if __name__ == "__main__":
    sys.exit(main())
