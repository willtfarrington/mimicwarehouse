"""EP-165 — Retro A: session & repo governance hardening acceptance tests.

Four surfaces, all fixture-tier and read-only against the real repository: (1) the
PreToolUse command-string hook (``scripts/claude_pretool_guard.py``) driven with crafted
hook payloads — decision matrix through the imported module, end-to-end (stdin → JSON
verdict, ≤ 200 ms) through a subprocess; (2) the guard's new G1/G4 rules (float-rendered
ids, path tokens, the 19 new data-shaped extensions); (3) ``mwh guard --selfcheck``'s new
probes (root-anchored ``.gitignore`` pairs, ``pretool-hook`` registration); (4) string
pins on ``.claude/settings.json`` (env block, deny floor, allow list, hook registration —
retro GOV-10) and on CLAUDE.md (PATH fallback, heredoc ban, connector rule). Only
synthetic values appear here; band ids are built at runtime from the module constants.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from mimicwarehouse import guard
from mimicwarehouse.guard import HADM_BAND, STAY_BAND, SUBJECT_BAND, scan, selfcheck

pytestmark = pytest.mark.ep_165

WORKSPACE = Path(__file__).resolve().parents[2]  # mimicwarehouse/ (the uv project)
REPO = WORKSPACE.parent
HOOK_SCRIPT = WORKSPACE / "scripts" / "claude_pretool_guard.py"
SETTINGS_JSON = REPO / ".claude" / "settings.json"
CLAUDE_MD = REPO / "CLAUDE.md"

# Runtime-built band ids (never literals — this file is G4-scanned too).
SUBJECT_ID = SUBJECT_BAND[0] + 7
HADM_ID = HADM_BAND[0] + 12_345
STAY_ID = STAY_BAND[0] + 424_242

#: The deny rules EP-165 added (brief item 2b; `uv run *python*` per the ledger's GOV-1
#: corrected fix — the narrower `uv run python*` misses the `--project …` form).
REQUIRED_DENY = (
    "Bash(cp *source material*)",
    "Bash(cp *mimicdata*)",
    "Bash(mv *mimicdata*)",
    "Bash(python* *mimicdata*)",
    "Bash(python* *source material*)",
    "Bash(uv run *python* *mimicdata*)",
    "Bash(uv run *python* *source material*)",
    "Bash(sh -c *)",
    "Bash(bash -c *)",
    "Bash(perl *)",
    "Bash(node -e *)",
    "Bash(diff *.csv*)",
    "Bash(od *)",
    "Bash(nl *.csv*)",
    "PowerShell(Copy-Item *mimicdata*)",
    "PowerShell(Copy-Item *source material*)",
    "PowerShell(*ReadAllText*)",
    "PowerShell(*ReadAllLines*)",
    "PowerShell(*ReadAllBytes*)",
    "PowerShell(gc *.csv*)",
    "PowerShell(cat *.csv*)",
    "PowerShell(type *.csv*)",
    "PowerShell(Get-ChildItem *mimicdata*)",
    "Read(//C:/**/*.csv)",
    "Read(//C:/**/*.csv.gz)",
    "Read(//C:/**/*.parquet)",
    "Read(//C:/**/*.duckdb)",
    "mcp__claude_ai_Gmail__send_message",
    "mcp__claude_ai_Gmail__reply",
    "mcp__claude_ai_Gmail__forward",
    "mcp__claude_ai_Gmail__create_draft",
    "mcp__claude_ai_Gmail__update_draft",
    "mcp__claude_ai_Gmail__trash_message",
    "mcp__claude_ai_Gmail__trash_thread",
    "mcp__claude_ai_Google_Calendar__create_event",
    "mcp__claude_ai_Google_Calendar__update_event",
    "mcp__claude_ai_Google_Calendar__delete_event",
    "mcp__claude_ai_Google_Calendar__respond_to_event",
    "mcp__claude_ai_Hugging_Face__hf_fs",
    "mcp__claude_ai_Hugging_Face__dynamic_space",
)


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


# ---------------------------------------------------------------------------
# 1a. Hook decision matrix (imported module — fast, exhaustive)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def hook() -> ModuleType:
    spec = importlib.util.spec_from_file_location("claude_pretool_guard", HOOK_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DECISION_CASES: tuple[tuple[str, dict[str, Any], bool], ...] = (
    # (tool_name, tool_input, expect_deny)
    ("Bash", {"command": "cat C:/mimicdata/x.csv"}, True),
    ("Bash", {"command": "python -c \"open('C:/nonexistent/probe.csv')\""}, True),
    ("Bash", {"command": "cp probe.csv %TEMP%"}, True),
    ("Bash", {"command": "sh -c 'cat C:/nonexistent_dir/mimicdata_probe/x.csv'"}, True),
    ("Bash", {"command": "uv run pytest x.py && sh -c 'cat foo.parquet'"}, True),  # no rescue
    ("Bash", {"command": "head 'source material/mimic-iv-3.1/hosp/patients.csv'"}, True),
    ("PowerShell", {"command": "[IO.File]::ReadAllText('C:/mimicdata/x.csv')"}, True),
    ("Read", {"file_path": "C:/anywhere/at/all/frame.parquet"}, True),
    ("Glob", {"pattern": "C:/mimicdata/**", "path": None}, True),
    ("Grep", {"pattern": "select", "path": "C:/mimicdata/lake"}, True),
    ("Bash", {"command": "uv run mwh inventory show"}, False),
    ("Bash", {"command": "uv run mwh build --tier dev --data-root C:\\mimicdata"}, False),
    ("Bash", {"command": "uv run --project mimicwarehouse --group dev poe test"}, False),
    ("Bash", {"command": "git status"}, False),
    ("Bash", {"command": "git add 'source material/README.md'"}, False),
    ("Bash", {"command": "ls mimicwarehouse/src"}, False),
    ("Read", {"file_path": "source material/README.md"}, False),
    ("Read", {"file_path": "roadmap/EP-165-retro-governance-session-hardening.md"}, False),
    ("Grep", {"pattern": "mimicdata", "path": "mimicwarehouse/DESIGN.md"}, False),  # doc work
    ("Edit", {"file_path": "C:/mimicdata/x.csv"}, False),  # unmatched tool → not this hook's job
)


@pytest.mark.parametrize(("tool", "tool_input", "want_deny"), DECISION_CASES)
def test_hook_decision_matrix(
    hook: ModuleType, tool: str, tool_input: dict[str, Any], want_deny: bool
) -> None:
    denied, _ = hook.decide(tool, tool_input)
    assert denied is want_deny


# ---------------------------------------------------------------------------
# 1b. Hook end-to-end: stdin payload → JSON verdict, ≤ 200 ms, fail-open
# ---------------------------------------------------------------------------

E2E_CASES: tuple[tuple[str, dict[str, str], bool], ...] = (
    ("Bash", {"command": "cat C:/mimicdata/x.csv"}, True),
    ("Bash", {"command": "python -c \"open('C:/nonexistent/probe.csv')\""}, True),
    ("Bash", {"command": "cp probe.csv %TEMP%"}, True),
    ("Bash", {"command": "uv run mwh inventory show"}, False),
    ("Bash", {"command": "git status"}, False),
    ("Read", {"file_path": str(CLAUDE_MD)}, False),
)


@pytest.mark.parametrize(("tool", "tool_input", "want_deny"), E2E_CASES)
def test_hook_subprocess_verdict_within_budget(
    tool: str, tool_input: dict[str, str], want_deny: bool
) -> None:
    payload = json.dumps({"tool_name": tool, "tool_input": tool_input}).encode()
    start = time.perf_counter()
    proc = subprocess.run(
        [sys.executable, str(HOOK_SCRIPT)], input=payload, capture_output=True, timeout=10
    )
    elapsed = time.perf_counter() - start
    assert proc.returncode == 0
    out = proc.stdout.decode("utf-8")
    if want_deny:
        verdict = json.loads(out)["hookSpecificOutput"]
        assert verdict["hookEventName"] == "PreToolUse"
        assert verdict["permissionDecision"] == "deny"
        assert "mimicdata" in verdict["permissionDecisionReason"]  # names the rule, not the data
    else:
        assert out == ""  # silent allow → normal permission flow
    assert elapsed <= 0.2, f"hook took {elapsed * 1000:.0f} ms (budget 200 ms)"


def test_hook_fails_open_on_malformed_input() -> None:
    proc = subprocess.run(
        [sys.executable, str(HOOK_SCRIPT)], input=b"not json {", capture_output=True, timeout=10
    )
    assert proc.returncode == 0
    assert proc.stdout == b""


# ---------------------------------------------------------------------------
# 2. Guard G1/G4: new extensions, float-rendered ids, path tokens
# ---------------------------------------------------------------------------


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, capture_output=True)
    return tmp_path


@pytest.mark.parametrize(
    ("name", "ext"),
    [
        ("dump.tsv", ".tsv"),
        ("book.xlsx", ".xlsx"),
        ("old.xls", ".xls"),
        ("bundle.zip", ".zip"),
        ("bundle.7z", ".7z"),
        ("t.tar", ".tar"),
        ("t.tgz", ".tgz"),
        ("t.tar.gz", ".tar.gz"),
        ("raw.gz", ".gz"),
        ("raw.bz2", ".bz2"),
        ("raw.zst", ".zst"),
        ("raw.xz", ".xz"),
        ("db.sqlite", ".sqlite"),
        ("db.sqlite3", ".sqlite3"),
        ("db.db", ".db"),
        ("t.orc", ".orc"),
        ("t.avro", ".avro"),
        ("t.ndjson", ".ndjson"),
        ("t.hdf5", ".hdf5"),
        ("frame.parquet.gz", ".gz"),  # longest-suffix: bare .gz covers compressed parquet
        ("frame.csv.zst", ".zst"),
        ("frame.csv.gz", ".csv.gz"),
    ],
)
def test_g1_new_extensions_are_recognised(name: str, ext: str) -> None:
    assert guard.data_extension(name) == ext


def test_g1_refuses_new_shapes_even_under_fixtures(repo: Path) -> None:
    outside = repo / "dump.tsv"
    outside.write_bytes(b"a\tb\n")
    under = repo / "mimicwarehouse/tests/fixtures/hosp/dump.tsv"
    under.parent.mkdir(parents=True)
    under.write_bytes(b"a\tb\n")
    violations = scan([outside, under], repo)
    assert [v.rule for v in violations].count("G1") == 2  # .tsv is not a FIXTURE_EXTENSION


def test_g4_token_value_and_mask_handle_float_renderings() -> None:
    token = f"{HADM_ID}.0"
    assert guard.token_value(token) == HADM_ID
    assert guard.token_value(str(SUBJECT_ID)) == SUBJECT_ID
    masked = guard.mask(token)
    assert masked == f"{str(HADM_ID)[0]}{'*' * 7}.0"
    assert str(HADM_ID) not in masked


def test_g4_float_rendered_id_in_content_is_a_hit(repo: Path) -> None:
    md = _write(repo / "agg.md", f"| hadm | {HADM_ID}.0 |\n| ok | {HADM_ID}.5 |\n")
    violations = scan([md], repo)
    assert [(v.rule, v.line) for v in violations] == [("G4", 1)]  # .5 stays clean


def test_g4_tsv_content_is_scanned(repo: Path) -> None:
    tsv = _write(repo / "x.tsv", f"subject_id\n{SUBJECT_ID}\n")
    assert {v.rule for v in scan([tsv], repo)} == {"G1", "G4"}


def test_g4_path_tokens_are_hits_with_masked_detail(repo: Path) -> None:
    named = _write(repo / f"stay_{STAY_ID}.md", "no ids in the content\n")
    png = repo / "figures" / f"stay-{STAY_ID}.png"
    png.parent.mkdir(parents=True)
    png.write_bytes(b"\x89PNG")
    violations = scan([named, png], repo)
    assert [(v.rule, v.line) for v in violations] == [("G4", None), ("G4", None)]
    for v in violations:
        assert "stay_id" in v.detail
        assert str(STAY_ID) not in v.detail  # masked
    clean = _write(repo / "stay_90000001.md", "fixture-floor ids in names are fine\n")
    assert scan([clean], repo) == []


# ---------------------------------------------------------------------------
# 3. Selfcheck: new .gitignore probes + pretool-hook registration
# ---------------------------------------------------------------------------


def test_selfcheck_carries_the_ep165_probes_and_hook_row() -> None:
    results = {r.id: r for r in selfcheck(REPO)}
    for probe_id in (
        "gitignore:mimicwarehouse/data/x.parquet",
        "gitignore:mimicwarehouse/x.tsv",
        "gitignore:mimicwarehouse/x.zip",
        "not-ignored:mimicwarehouse/src/mimicwarehouse/data/item_units.yaml",
        "not-ignored:mimicwarehouse/app/models/x.py",
        "gitattributes:x.tsv",
        "gitattributes:x.zip",
    ):
        assert results[probe_id].ok, results[probe_id].as_dict()
    hook_row = results["pretool-hook"]
    assert hook_row.ok and hook_row.detail == "registered" and hook_row.level == "fail"


# ---------------------------------------------------------------------------
# 4. Pins: .claude/settings.json and CLAUDE.md (retro GOV-10 / DOC-1)
# ---------------------------------------------------------------------------


def test_settings_json_env_deny_allow_and_hook() -> None:
    settings = json.loads(SETTINGS_JSON.read_text("utf-8"))
    assert settings["env"]["PYTHONUTF8"] == "1"
    deny = settings["permissions"]["deny"]
    missing = [rule for rule in REQUIRED_DENY if rule not in deny]
    assert not missing, f"deny rules missing: {missing}"
    assert len(deny) >= 100  # EP-0's 67 + EP-165's additions; shrinking this needs D-39 review
    allow = settings["permissions"]["allow"]
    assert "Bash(uv run mwh *)" in allow and "Bash(git status*)" in allow
    lowered = [rule.lower() for rule in allow]
    assert not any("mimicdata" in rule or "source material" in rule for rule in lowered)
    groups = settings["hooks"]["PreToolUse"]
    assert groups[0]["matcher"] == "Bash|PowerShell|Read|Grep|Glob"
    command = groups[0]["hooks"][0]["command"]
    assert "claude_pretool_guard.py" in command and "python" in command.lower()


def test_claude_md_carries_the_session_tooling_rules() -> None:
    text = CLAUDE_MD.read_text("utf-8")
    assert 'export PATH="$LOCALAPPDATA/Microsoft/WinGet/Links:$PATH"' in text  # (a) uv PATH
    assert "never** bash heredocs" in text and "`python -`" in text  # (c) D-42 verbatim
    assert "git commit -F" in text
    assert "PYTHONUTF8=1" in text  # (e) console
    assert 'default-groups=["dev"]' in text  # (f)
    assert "second egress path" in text and "public-reference" in text  # §2 connector bullet
    assert "sending anything through a connector" in text  # §6 ask-before
    assert CLAUDE_MD.stat().st_size < 9_000  # keep it a session brief, not a handbook
