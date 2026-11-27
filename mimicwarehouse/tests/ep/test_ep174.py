"""EP-174 — Site card: publish the roadmap's figures at each commit (D-48; inbox IN-4).

Every card here is built from a **synthetic** roadmap committed in a throw-away git repository
under ``tmp_path`` whose working tree differs from ``HEAD`` (the card follows ``HEAD``), and is
written into temp folders — never Roadmap Wrangler's real ``cards`` folder. :func:`check_card`
mirrors Roadmap Wrangler's reader (``crates/core/src/export.rs``, its EP-58b; the contract is
its D-24 addendum of 2026-11-25): a change there needs a message here. The real repository is
read only through ``git show HEAD:…`` (the committed roadmap and workspace files).
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml

import helpers
from mimicwarehouse import card, config, verify
from mimicwarehouse.cli import DIAGNOSTIC_COMMANDS, app

pytestmark = pytest.mark.ep_174

TODAY = date(2026, 11, 25)
BOM = "\N{ZERO WIDTH NO-BREAK SPACE}"
PYPROJECT = '[project]\nname = "synthetic"\nrequires-python = ">=3.12"\n'
WORKSPACE_FILES = {
    "mimicwarehouse/pyproject.toml": PYPROJECT,
    "mimicwarehouse/.python-version": "3.13\n",
    "mimicwarehouse/uv.lock": "version = 1\n",
}
#: (ep, title, size cell, done) — two phases, so "table order" crosses a heading.
ROWS: list[tuple[int, str, str, bool]] = [
    (1, "First brief", "S", True),
    (2, "Second brief", "M", True),
    (3, "Third brief: a colon and a pipe \\| inside", "M", False),
    (4, "Fourth brief ⏱", "XL", False),
    (5, "Fifth brief", "S", False),
]


# ---------------------------------------------------------------------------
# Helpers: the reader's rules, a synthetic roadmap, a throw-away repository
# ---------------------------------------------------------------------------


def check_card(text: str, name: str, today: date) -> dict[str, Any]:
    """Roadmap Wrangler's ``check_card`` (export.rs, EP-58b), mirrored: UTF-8 with an optional
    byte-order mark, JSON with every field, the registered name, a day ``YYYY-MM-DD`` not after
    today, a total above nought, done at most the total, the percent rounded down, as many next
    rows as the undone rows allow up to two, each with an id, a title and a size of S, M, L or
    null. Fails the test, naming what broke."""
    payload = json.loads(text.removeprefix(BOM))
    assert isinstance(payload, dict), "not a JSON object"
    for key, kind in (("as_of", str), ("name", str), ("badge", str), ("next", list)):
        assert isinstance(payload.get(key), kind), f"{key} missing or not a {kind.__name__}"
    for key in ("done", "total", "percent"):
        value = payload.get(key)
        assert isinstance(value, int) and not isinstance(value, bool) and value >= 0, key
    assert payload["name"] == name, f"it names {payload['name']!r}, not {name!r}"
    assert date.fromisoformat(payload["as_of"]).isoformat() == payload["as_of"], "as_of form"
    assert date.fromisoformat(payload["as_of"]) <= today, "as_of is after today"
    assert payload["total"] > 0, "its total is nought"
    assert payload["done"] <= payload["total"], "done over the total"
    assert payload["percent"] == payload["done"] * 100 // payload["total"], "percent"
    assert len(payload["next"]) == min(2, payload["total"] - payload["done"]), "next rows"
    for row in payload["next"]:
        assert isinstance(row, dict) and str(row.get("id", "")).strip(), "a next row has no id"
        assert str(row.get("title", "")).strip(), f"next row {row['id']} has no title"
        assert row.get("size") in {"S", "M", "L", None}, f"next row {row['id']}'s size"
    return payload


def roadmap(rows: list[tuple[int, str, str, bool]]) -> str:
    """A synthetic master roadmap in this repository's table form, split over two phases."""
    head = "| # | Brief | Size | Depends on | Core | Done |\n|---|---|---|---|---|---|\n"

    def line(ep: int, title: str, size: str, done: bool) -> str:
        cell = "☑ `abc1234`" if done else "☐"
        return f"| EP-{ep} | [{title}](EP-{ep}-synthetic.md) | {size} | — | core | {cell} |\n"

    half = (len(rows) + 1) // 2
    parts = ["# Synthetic roadmap\n\nProse with no rows.\n"]
    for title, chunk in (("P0 — First (full briefs)", rows[:half]), ("P1 — Second", rows[half:])):
        parts.append(f"\n## Phase {title}\n\n{head}{''.join(line(*r) for r in chunk)}")
    return "".join(parts)


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True, encoding="utf-8"
    )
    return proc.stdout


def commit(repo: Path, files: dict[str, str], message: str = "synthetic") -> None:
    for rel, text in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
        _git(repo, "add", rel)
    _git(repo, "commit", "-q", "-m", message)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A throw-away git repository with no commit yet."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "card-test@example.invalid")
    _git(root, "config", "user.name", "card test")
    _git(root, "config", "commit.gpgsign", "false")
    return root


@pytest.fixture
def synthetic(repo: Path) -> Path:
    """``ROWS`` and the workspace files committed; the working tree then differs from HEAD
    (every row done, another pin, no lock), which the card must not see."""
    commit(repo, {"roadmap/README.md": roadmap(ROWS), **WORKSPACE_FILES})
    edited = [(ep, title, size, True) for ep, title, size, _ in ROWS]
    (repo / "roadmap" / "README.md").write_text(roadmap(edited), encoding="utf-8")
    (repo / "mimicwarehouse" / ".python-version").write_text("3.12\n", encoding="utf-8")
    (repo / "mimicwarehouse" / "uv.lock").unlink()
    return repo


@pytest.fixture
def tool(tmp_path: Path) -> Path:
    """A stand-in for Roadmap Wrangler's own folder, without its ``cards`` folder yet."""
    folder = tmp_path / "clauderoadmapwrangler"
    folder.mkdir()
    return folder


@pytest.fixture
def cli(monkeypatch: pytest.MonkeyPatch, synthetic: Path):
    """Run ``mwh`` against the synthetic repository (clean ``MWH_*`` environment)."""
    for key in list(os.environ):
        if key.upper().startswith("MWH_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(config, "repo_root", lambda: synthetic)
    config.configure()
    runner = helpers.cli_runner()
    yield lambda *args: runner.invoke(app, list(args))
    config.configure()


# ---------------------------------------------------------------------------
# 1. The card: fields, HEAD only, next rows, sizes
# ---------------------------------------------------------------------------


def test_card_follows_head_and_passes_the_reader(synthetic: Path) -> None:
    built = card.build_card(synthetic, today=TODAY)
    assert list(built) == list(card.FIELDS)  # exactly the reader's fields, none added
    payload = check_card(card.render(built), "mimicwarehouse", TODAY)
    assert payload == built
    assert (built["as_of"], built["done"], built["total"], built["percent"]) == (
        "2026-11-25",
        2,
        5,
        40,
    )
    assert built["badge"] == "Python · 3.13 · uv"  # HEAD's pin and lock, not the working tree's
    assert built["next"] == [
        {"id": "EP-3", "title": "Third brief: a colon and a pipe | inside", "size": "M"},
        {"id": "EP-4", "title": "Fourth brief ⏱", "size": None},  # XL is no size
    ]
    for row in built["next"]:
        assert list(row) == ["id", "title", "size"]


@pytest.mark.parametrize("undone", [0, 1, 2, 4])
def test_next_names_the_first_min_two_undone_rows(repo: Path, undone: int) -> None:
    rows = [(n, f"Brief {n}", "M", n <= 4 - undone) for n in range(1, 5)]
    commit(repo, {"roadmap/README.md": roadmap(rows)})
    built = card.build_card(repo, today=TODAY)
    check_card(card.render(built), "mimicwarehouse", TODAY)
    expected = [f"EP-{n}" for n in range(5 - undone, 5)][:2]
    assert [row["id"] for row in built["next"]] == expected
    assert built["percent"] == (4 - undone) * 100 // 4
    assert built["badge"] == ""  # no committed pyproject.toml: an empty badge is allowed


@pytest.mark.parametrize(
    ("cell", "size"),
    [("S", "S"), ("M", "M"), ("L", "L"), ("XL", None), ("M+", None), ("s", None), ("", None)],
)
def test_a_size_cell_other_than_s_m_l_gives_null(repo: Path, cell: str, size: str | None) -> None:
    commit(repo, {"roadmap/README.md": roadmap([(7, "Only brief", cell, False)])})
    built = card.build_card(repo, today=TODAY)
    assert built["next"] == [{"id": "EP-7", "title": "Only brief", "size": size}]
    check_card(card.render(built), "mimicwarehouse", TODAY)


def test_parse_roadmap_text_is_the_file_parser_split() -> None:
    readme = helpers.REPO_ROOT / "roadmap" / "README.md"
    rows = verify.parse_roadmap_table(readme)
    assert rows and rows == verify.parse_roadmap_text(readme.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 2. The badge: Roadmap Wrangler's stack rule, from HEAD
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("version", "files", "badge"),
    [
        ("3.13\n", {"uv.lock"}, "Python · 3.13 · uv"),
        (None, {"uv.lock"}, "Python · >=3.12 · uv"),  # requires-python when no .python-version
        ("\n3.11\n", {"uv.lock"}, "Python · >=3.12 · uv"),  # only the first line counts
        ("  3.13  \n", set(), "Python · 3.13"),  # uv only with uv.lock
        ("3.13\n", {"poetry.lock"}, "Python · 3.13 · Poetry"),
        ("3.13\n", {"uv.lock", "poetry.lock"}, "Python · 3.13 · uv"),
    ],
)
def test_stack_badge_mirrors_the_rule(version: str | None, files: set[str], badge: str) -> None:
    assert card.stack_badge(PYPROJECT, version, frozenset(files)) == badge


def test_stack_badge_edges() -> None:
    assert card.stack_badge(None, "3.13\n", frozenset({"uv.lock"})) == ""
    assert card.stack_badge('[project]\nname = "x"\n', None, frozenset()) == "Python"
    single = "[project]\nrequires-python = '>=3.13'\n"
    assert card.stack_badge(single, None, frozenset()) == "Python · >=3.13"


def test_badge_reads_the_committed_workspace(synthetic: Path) -> None:
    assert card.badge_at_head(synthetic) == "Python · 3.13 · uv"
    commit(synthetic, {"mimicwarehouse/.python-version": "3.14\n"}, "pin change")
    assert card.badge_at_head(synthetic) == "Python · 3.14 · uv"  # the lock's removal is unstaged
    _git(synthetic, "rm", "-q", "mimicwarehouse/uv.lock")
    _git(synthetic, "commit", "-q", "-m", "no lock")
    assert card.badge_at_head(synthetic) == "Python · 3.14"


# ---------------------------------------------------------------------------
# 3. The write: place, no-ops, unchanged figures, malformed files
# ---------------------------------------------------------------------------


def test_default_place_crw_home_then_localappdata() -> None:
    assert card.default_cards_dir({"CRW_HOME": "H", "LOCALAPPDATA": "L"}) == Path("H", "cards")
    expected = Path("L", "clauderoadmapwrangler", "cards")
    assert card.default_cards_dir({"CRW_HOME": "", "LOCALAPPDATA": "L"}) == expected
    assert card.default_cards_dir({"LOCALAPPDATA": "L"}) == expected
    assert card.default_cards_dir({}) is None
    assert card.card_path(Path("C")) == Path("C", "mimicwarehouse.json")


def test_missing_cards_folder_is_created_and_nothing_else_written(
    synthetic: Path, tool: Path
) -> None:
    outcome = card.run_card(synthetic, tool / "cards", write=True, today=TODAY)
    assert outcome.written and outcome.plan.reason == "no card file yet"
    assert sorted(p.name for p in tool.iterdir()) == ["cards"]
    assert [p.name for p in (tool / "cards").iterdir()] == ["mimicwarehouse.json"]
    raw = (tool / "cards" / "mimicwarehouse.json").read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf") and raw.endswith(b"}\n")
    assert len(raw) < card.CARD_BYTES
    check_card(raw.decode("utf-8"), "mimicwarehouse", TODAY)


def test_missing_tool_folder_is_a_quiet_noop(synthetic: Path, tmp_path: Path) -> None:
    absent = tmp_path / "not-installed"
    outcome = card.run_card(synthetic, absent / "cards", write=True, today=TODAY)
    assert outcome.plan.action == "skip" and not outcome.written
    assert not absent.exists()
    nowhere = card.run_card(synthetic, None, write=True, today=TODAY)
    assert nowhere.plan.action == "skip" and nowhere.plan.path is None


def test_unchanged_figures_keep_bytes_and_mtime(synthetic: Path, tool: Path) -> None:
    cards = tool / "cards"
    assert card.run_card(synthetic, cards, write=True, today=TODAY).written
    path = card.card_path(cards)
    os.utime(path, ns=(1_700_000_000_000_000_000, 1_700_000_000_000_000_000))
    before = (path.read_bytes(), path.stat().st_mtime_ns)
    later = TODAY + timedelta(days=3)
    again = card.run_card(synthetic, cards, write=True, today=later)  # as_of alone differs
    assert again.plan.action == "keep" and not again.written
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    commit(synthetic, {"roadmap/README.md": roadmap([*ROWS[:4], (5, "Fifth brief", "S", True)])})
    moved = card.run_card(synthetic, cards, write=True, today=later)
    assert moved.written and moved.plan.reason == "the figures changed"
    payload = check_card(path.read_text(encoding="utf-8"), "mimicwarehouse", later)
    assert (payload["as_of"], payload["done"]) == (later.isoformat(), 3)


def _same_figures(synthetic: Path, drop: str | None = None, **overrides: Any) -> bytes:
    """The card's own figures under another ``as_of`` / an extra field, as file bytes."""
    payload = {**card.build_card(synthetic, today=TODAY), **overrides}
    payload.pop(drop or "", None)
    return json.dumps(payload).encode("utf-8")


@pytest.mark.parametrize(
    ("label", "body"),
    [
        ("not json", lambda s: b"{"),
        ("not an object", lambda s: b"[]"),
        ("not utf-8", lambda s: b"\xff\xfe{"),
        ("as_of missing", lambda s: _same_figures(s, drop="as_of")),
        ("as_of null", lambda s: _same_figures(s, as_of=None)),
        ("as_of after today", lambda s: _same_figures(s, as_of="2026-11-26")),
        ("as_of no real day", lambda s: _same_figures(s, as_of="2026-02-30")),
        ("as_of another form", lambda s: _same_figures(s, as_of="2026-11-5")),
        ("an extra field", lambda s: _same_figures(s, public=True)),
        ("over 64 KiB", lambda s: _same_figures(s) + b" " * card.CARD_BYTES),
    ],
)
def test_malformed_or_foreign_file_is_rewritten(
    synthetic: Path, tool: Path, label: str, body: Any
) -> None:
    path = card.card_path(tool / "cards")
    path.parent.mkdir()
    path.write_bytes(body(synthetic))
    outcome = card.run_card(synthetic, tool / "cards", write=True, today=TODAY)
    assert outcome.written, label
    check_card(path.read_text(encoding="utf-8"), "mimicwarehouse", TODAY)


def test_same_figures_behind_a_byte_order_mark_are_kept(synthetic: Path, tool: Path) -> None:
    path = card.card_path(tool / "cards")
    path.parent.mkdir()
    path.write_bytes(BOM.encode("utf-8") + _same_figures(synthetic, as_of="2026-11-01"))
    outcome = card.run_card(synthetic, tool / "cards", write=True, today=TODAY)
    assert outcome.plan.action == "keep" and not outcome.written


def test_no_head_or_no_rows_writes_nothing(repo: Path, tool: Path) -> None:
    for files in (None, {"roadmap/README.md": "# Roadmap\n\nNo table yet.\n"}):
        if files:
            commit(repo, files)
        outcome = card.run_card(repo, tool / "cards", write=True, today=TODAY)
        assert outcome.card is None and not outcome.written
        (line,) = card.describe(outcome, write=True)
        assert line.isascii() and line.endswith("nothing written")
    assert not (tool / "cards").exists()


# ---------------------------------------------------------------------------
# 4. The command
# ---------------------------------------------------------------------------


def test_cli_preview_json_write_and_rerun(cli, tool: Path) -> None:
    dest = str(tool / "cards")
    preview = cli("card", "--dest", dest)
    assert preview.exit_code == 0, preview.output
    assert "would be written" in preview.output and "2 of 5 (40 %)" in preview.output
    assert preview.output.replace("·", "").replace("⏱", "").isascii()  # only the data is not
    assert not (tool / "cards").exists()  # a preview writes nothing

    as_json = cli("card", "--dest", dest, "--json")
    assert as_json.exit_code == 0 and as_json.output.isascii()  # emit_json escapes the badge
    outcome = json.loads(as_json.output)
    assert (outcome["action"], outcome["written"]) == ("write", False)
    check_card(json.dumps(outcome["card"]), "mimicwarehouse", date.today())

    first = cli("card", "--write", "--dest", dest)
    assert first.exit_code == 0 and "wrote" in first.output and "next EP-3, EP-4" in first.output
    second = cli("card", "--write", "--dest", dest)
    assert second.exit_code == 0 and "unchanged" in second.output
    assert len(second.output.strip().splitlines()) == 1


def test_cli_default_place_from_crw_home_and_noop(
    cli, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "trial-home"
    home.mkdir()
    monkeypatch.setenv("CRW_HOME", str(home))
    assert cli("card", "--write").exit_code == 0
    check_card(
        (home / "cards" / "mimicwarehouse.json").read_text(encoding="utf-8"),
        "mimicwarehouse",
        date.today(),
    )
    monkeypatch.setenv("CRW_HOME", str(tmp_path / "absent"))
    quiet = cli("card", "--write")
    assert quiet.exit_code == 0 and "nothing written" in quiet.output
    assert not (tmp_path / "absent").exists()


def test_card_is_diagnostic_and_ignores_an_unsafe_data_root(cli, tool: Path) -> None:
    assert "card" in DIAGNOSTIC_COMMANDS
    res = cli("--data-root", r"G:\mimicdata", "card", "--write", "--dest", str(tool / "cards"))
    assert res.exit_code == 0, res.output  # the post-commit hook is never blocked by settings


def test_real_repository_card_passes_the_reader() -> None:
    if config.repo_root() is None:
        pytest.skip("not a git checkout")
    built = card.build_card(helpers.REPO_ROOT)
    check_card(card.render(built), "mimicwarehouse", date.today())
    assert built["badge"].startswith("Python · ") and built["badge"].endswith(" · uv")


# ---------------------------------------------------------------------------
# 5. The hook, the wiring, the records, the import budget
# ---------------------------------------------------------------------------


def test_post_commit_hook_runs_the_card_alone() -> None:
    path = helpers.REPO_ROOT / ".pre-commit-config.yaml"
    text = path.read_text(encoding="utf-8")
    cfg = yaml.safe_load(text)
    assert cfg["default_install_hook_types"] == ["pre-commit", "post-commit"]
    assert cfg["default_stages"] == ["pre-commit"]
    hooks = [hook for repo in cfg["repos"] for hook in repo["hooks"]]
    (hook,) = [h for h in hooks if h["id"] == "mwh-card"]
    assert hook["entry"] == "uv run --project mimicwarehouse --group dev mwh card --write"
    assert hook["stages"] == ["post-commit"]
    assert hook["always_run"] is True and hook["pass_filenames"] is False
    assert hook["language"] == "system"
    assert all("post-commit" not in h.get("stages", []) for h in hooks if h is not hook)
    header = "".join(line for line in text.splitlines(keepends=True) if line.startswith("#"))
    assert "pre-commit install" in header and "post-commit" in header


def test_cli_wiring_records_and_import_budget() -> None:
    source = (helpers.WORKSPACE / "src" / "mimicwarehouse" / "cli.py").read_text(encoding="utf-8")
    block = [line for line in source.splitlines() if line.startswith("app.")]
    names = [line.split('"')[1] for line in block]
    assert names == sorted(names) and names.index("card") == names.index("canary") + 1
    assert names[names.index("card") + 1] == "catalog"
    assert "card" in helpers.cli_runner().invoke(app, ["--help"]).output
    decisions = (helpers.WORKSPACE / "DECISIONS.md").read_text(encoding="utf-8")
    addendum = decisions[decisions.index("**D-48 ") :]
    assert "Addendum (2026-11-25, EP-174)" in addendum and "mwh card --write" in addendum
    readme = (helpers.WORKSPACE / "README.md").read_text(encoding="utf-8")
    assert "`card.py`" in readme and "mwh card" in readme and "post-commit" in readme
    helpers.assert_import_budget()
    helpers.assert_import_budget("mimicwarehouse.card")
