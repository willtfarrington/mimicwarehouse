"""``mwh card`` — the site card's figures, published by this repository (EP-174; D-48).

The owner's portfolio site draws each public project's card — done, total, the next undone
rows, a stack badge — from Roadmap Wrangler's site export, which never reads inside a data
repository (its D-24 addenda of its EP-27 and EP-58b). This repository therefore writes its
own card, after every commit, to the one file that export reads for it, outside every project
folder (inbox IN-4; the D-48 addendum):

* ``%CRW_HOME%\\cards\\mimicwarehouse.json`` when ``CRW_HOME`` is set (the tool's trial home),
* else ``%LOCALAPPDATA%\\clauderoadmapwrangler\\cards\\mimicwarehouse.json``;
* ``--dest <folder>`` replaces the ``cards`` folder (tests).

**The figures** come from the committed roadmap only — ``git show HEAD:roadmap/README.md``
through :func:`mimicwarehouse.verify._run_git`, parsed by
:func:`mimicwarehouse.verify.parse_roadmap_text`, the parser ``poe roadmap-check --strict``
gates — so an uncommitted edit never reaches the card. **The badge** is Roadmap Wrangler's
stack rule (its ``badges/stack.rs``, D-25) applied to the workspace's committed
``pyproject.toml``, ``.python-version`` and lock file (``Python · 3.13 · uv`` at EP-174), so a
pin change follows. The file holds exactly the fields Roadmap Wrangler's ``check_card``
(``export.rs``) validates, in this order: ``as_of`` (the local day, ``YYYY-MM-DD``), ``name``,
``badge``, ``done``, ``total``, ``percent`` (done * 100 // total) and ``next`` (the first
min(2, total - done) undone rows in table order, ``{"id", "title", "size"}``, the size ``S`` /
``M`` / ``L`` or null). The reader ignores other fields, so none are added.

**The write** happens only when the figures — every field but ``as_of`` — differ from the
file's, or the file is missing, unreadable, over 64 KiB or malformed; so ``as_of`` reads the
day the figures last changed. It is whole, through :func:`mimicwarehouse.fsio.atomic_write_text`
(a temp sibling, then ``os.replace``), UTF-8 without a byte-order mark, and creates the
``cards`` folder when it is missing. Nothing else is written there and no project's files are
touched. **No-ops** (exit 0, one ASCII line): no ``HEAD``, no committed roadmap or no rows;
Roadmap Wrangler's own folder missing (not installed, another machine — D-48's quiet no-op).

The figures are roadmap counts and titles already public in the tracked README, never derived
from data, so no disclosure check applies (D-48; GOVERNANCE §3). The command never touches the
data root, so it is a diagnostic command (:data:`mimicwarehouse.cli.DIAGNOSTIC_COMMANDS`).
``mwh card`` previews; ``mwh card --write`` is the post-commit hook's call
(``.pre-commit-config.yaml``, ``stages: [post-commit]``). Import cost: stdlib + typer +
:mod:`mimicwarehouse.verify` (already paid by ``cli.py``).
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Annotated, Any, Literal

import typer

from mimicwarehouse import verify
from mimicwarehouse.console import EXIT_FINDINGS, console_safe, emit_json, fail
from mimicwarehouse.fsio import atomic_write_text

# ---------------------------------------------------------------------------
# Constants — Roadmap Wrangler's, as of its EP-58b (paths.rs, export.rs, badges/stack.rs)
# ---------------------------------------------------------------------------

#: The registered name: the card's ``name`` and its file's stem.
NAME = "mimicwarehouse"
#: Repository-relative: the roadmap the figures come from, the uv workspace the badge reads.
ROADMAP_PATH = "roadmap/README.md"
WORKSPACE_DIR = "mimicwarehouse"
#: ``%CRW_HOME%`` (a trial home), else ``%LOCALAPPDATA%\clauderoadmapwrangler``, holds ``cards``.
HOME_VAR = "CRW_HOME"
APP_DIR = "clauderoadmapwrangler"
CARDS_DIR = "cards"
#: The undone rows a card names, the sizes it carries, the most a card file may weigh.
NEXT_ROWS = 2
SIZES: frozenset[str] = frozenset({"S", "M", "L"})
CARD_BYTES = 64 * 1024
#: The card's fields, in the order they are written (the reader's ``CardFile``).
FIELDS: tuple[str, ...] = ("as_of", "name", "badge", "done", "total", "percent", "next")

_DAY = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")

Action = Literal["write", "keep", "skip"]


class NoCardError(RuntimeError):
    """No card can be made: no ``HEAD``, no committed roadmap, or a roadmap without rows."""


# ---------------------------------------------------------------------------
# The committed tree
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> str | None:
    """stdout of ``git -C <repo> <args>`` (:func:`verify._run_git`), or None when git fails
    or is missing."""
    try:
        proc = verify._run_git(repo, *args)
    except OSError:
        return None
    return proc.stdout if proc.returncode == 0 else None


def head_text(repo: Path, relpath: str) -> str | None:
    """The committed text of ``relpath`` at ``HEAD``, or None (no ``HEAD``, no such file)."""
    return _git(repo, "show", f"HEAD:{relpath}")


def head_files(repo: Path, folder: str) -> frozenset[str]:
    """The names of the files directly in ``folder`` at ``HEAD`` (``git ls-tree``)."""
    names: set[str] = set()
    for line in (_git(repo, "ls-tree", "HEAD", f"{folder}/") or "").splitlines():
        meta, _, path = line.partition("\t")
        parts = meta.split()
        if len(parts) == 3 and parts[1] == "blob":
            names.add(path.rsplit("/", 1)[-1])
    return frozenset(names)


# ---------------------------------------------------------------------------
# The badge — Roadmap Wrangler's stack rule for one Python folder
# ---------------------------------------------------------------------------


def _requires_python(pyproject: str) -> str | None:
    """``requires-python = ">=3.13"`` → ``>=3.13`` (the first such line, as stack.rs reads it)."""
    for line in pyproject.splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() == "requires-python":
            return value.strip().strip("\"'")
    return None


def stack_badge(pyproject: str | None, python_version: str | None, files: frozenset[str]) -> str:
    """The badge the stack rule gives one Python folder: empty without a ``pyproject.toml``,
    else ``Python`` and its detail joined by `` · `` — the pin (the first line of
    ``.python-version``, else ``requires-python``) and the manager (``uv`` with ``uv.lock``,
    ``Poetry`` with ``poetry.lock``). A badge joins its chips with ``", "``; the workspace
    gives one chip."""
    if pyproject is None:
        return ""
    first = (python_version or "").splitlines()[:1]
    pin = first[0].strip() if first and first[0].strip() else _requires_python(pyproject)
    manager = "uv" if "uv.lock" in files else "Poetry" if "poetry.lock" in files else None
    return " · ".join(["Python", *(part for part in (pin, manager) if part is not None)])


def badge_at_head(repo: Path, workspace: str = WORKSPACE_DIR) -> str:
    """:func:`stack_badge` over the workspace's committed files."""
    files = head_files(repo, workspace)
    if "pyproject.toml" not in files:
        return ""
    version = (
        head_text(repo, f"{workspace}/.python-version") if ".python-version" in files else None
    )
    return stack_badge(head_text(repo, f"{workspace}/pyproject.toml"), version, files)


# ---------------------------------------------------------------------------
# The card
# ---------------------------------------------------------------------------


def build_card(
    repo: Path, *, today: date | None = None, workspace: str = WORKSPACE_DIR
) -> dict[str, Any]:
    """The card the committed roadmap gives (:data:`FIELDS`, in order); :class:`NoCardError`
    when there is no ``HEAD``, no committed roadmap or no row."""
    text = head_text(repo, ROADMAP_PATH)
    if text is None:
        raise NoCardError(f"no committed {ROADMAP_PATH} at HEAD")
    rows = verify.parse_roadmap_text(text)
    if not rows:
        raise NoCardError(f"the committed {ROADMAP_PATH} has no rows")
    done = sum(1 for row in rows if row.done)
    undone = [row for row in rows if not row.done][:NEXT_ROWS]
    return {
        "as_of": (today or date.today()).isoformat(),
        "name": NAME,
        "badge": badge_at_head(repo, workspace),
        "done": done,
        "total": len(rows),
        "percent": done * 100 // len(rows),
        "next": [
            {
                "id": f"EP-{row.ep}",
                "title": row.title.replace("\\|", "|").strip() or f"EP-{row.ep}",
                "size": row.size if row.size in SIZES else None,
            }
            for row in undone
        ],
    }


def render(card: Mapping[str, Any]) -> str:
    """The file's text: indented JSON in UTF-8 (``·`` unescaped) with one trailing newline."""
    return json.dumps(card, indent=2, ensure_ascii=False) + "\n"


def _figures(card: Mapping[str, Any]) -> str:
    """Every field but ``as_of``, canonical: key order is ignored, ``1`` and ``true`` differ."""
    return json.dumps({k: v for k, v in card.items() if k != "as_of"}, sort_keys=True)


def _valid_day(value: object, today: date) -> bool:
    """A day written ``YYYY-MM-DD`` and not after ``today`` (the reader's rule)."""
    if not isinstance(value, str) or not _DAY.fullmatch(value):
        return False
    try:
        return date.fromisoformat(value) <= today
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# The place and the write
# ---------------------------------------------------------------------------


def default_cards_dir(env: Mapping[str, str] | None = None) -> Path | None:
    """``%CRW_HOME%\\cards`` when ``CRW_HOME`` is set, else
    ``%LOCALAPPDATA%\\clauderoadmapwrangler\\cards``; None when neither is (Roadmap
    Wrangler's ``paths::local_dir``)."""
    env = os.environ if env is None else env
    if home := env.get(HOME_VAR):
        return Path(home) / CARDS_DIR
    if local := env.get("LOCALAPPDATA"):
        return Path(local) / APP_DIR / CARDS_DIR
    return None


def card_path(cards: Path) -> Path:
    """The card file in ``cards``: ``<registered name>.json``."""
    return Path(cards) / f"{NAME}.json"


@dataclass(frozen=True, slots=True)
class Plan:
    """What a write does: ``write`` the file, ``keep`` it, or ``skip`` (nothing written)."""

    action: Action
    path: Path | None
    reason: str


def plan_write(card: Mapping[str, Any], cards: Path | None, *, today: date) -> Plan:
    """``skip`` without Roadmap Wrangler's own folder (the parent of ``cards``); ``keep``
    when the file holds these figures under a valid day; ``write`` otherwise."""
    if cards is None:
        return Plan("skip", None, f"no Roadmap Wrangler folder ({HOME_VAR} and LOCALAPPDATA unset)")
    path = card_path(cards)
    if not path.parent.parent.is_dir():
        return Plan("skip", path, f"no Roadmap Wrangler folder at {path.parent.parent}")
    try:
        if path.stat().st_size > CARD_BYTES:
            return Plan("write", path, "the file is over 64 KiB")
        existing = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return Plan("write", path, "no card file yet")
    except (OSError, UnicodeDecodeError, ValueError):
        return Plan("write", path, "the file is unreadable or malformed")
    if not isinstance(existing, dict) or not _valid_day(existing.get("as_of"), today):
        return Plan("write", path, "the file is malformed")
    if _figures(existing) != _figures(card):
        return Plan("write", path, "the figures changed")
    return Plan("keep", path, "the figures are unchanged")


def write_card(card: Mapping[str, Any], plan: Plan) -> bool:
    """Carry out a ``write`` plan — create ``cards`` when missing (never Roadmap Wrangler's
    folder itself), write the file whole; True when written."""
    if plan.action != "write" or plan.path is None:
        return False
    plan.path.parent.mkdir(exist_ok=True)
    atomic_write_text(plan.path, render(card))
    return True


@dataclass(frozen=True, slots=True)
class Outcome:
    """One ``mwh card`` run: the card (None when none can be made), the plan, whether written."""

    card: dict[str, Any] | None
    plan: Plan
    written: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "action": self.plan.action,
            "written": self.written,
            "path": None if self.plan.path is None else str(self.plan.path),
            "reason": self.plan.reason,
            "card": self.card,
        }


def run_card(
    repo: Path,
    cards: Path | None,
    *,
    write: bool,
    today: date | None = None,
    workspace: str = WORKSPACE_DIR,
) -> Outcome:
    """Build the card from ``repo``'s ``HEAD`` and plan its write into ``cards``; write it
    when ``write`` and the plan says so."""
    today = today or date.today()
    try:
        card = build_card(repo, today=today, workspace=workspace)
    except NoCardError as exc:
        return Outcome(None, Plan("skip", None, str(exc)), False)
    plan = plan_write(card, cards, today=today)
    return Outcome(card, plan, write_card(card, plan) if write else False)


def describe(outcome: Outcome, *, write: bool) -> list[str]:
    """The human lines (ASCII labels; the badge and titles go through ``console_safe``)."""
    card, plan = outcome.card, outcome.plan
    if card is None or (write and plan.action == "skip"):
        return [f"mwh card: {plan.reason} - nothing written"]
    figures = f"{card['done']} of {card['total']} ({card['percent']} %)"
    if write:
        if outcome.written:
            ids = ", ".join(row["id"] for row in card["next"]) or "none"
            return [f"mwh card: wrote {plan.path} ({figures}; next {ids})"]
        return [f"mwh card: {plan.path} unchanged ({figures})"]
    lines = [
        f"card: {card['name']} as of {card['as_of']} (the committed roadmap at HEAD)",
        f"  badge: {card['badge'] or '(none)'}",
        f"  done:  {figures}",
    ]
    for i, row in enumerate(card["next"]):
        lines.append(
            f"{'  next:  ' if i == 0 else ' ' * 9}{row['id']} ({row['size'] or '-'}) {row['title']}"
        )
    if not card["next"]:
        lines.append("  next:  none (every row is done)")
    verdict = {"write": "would be written", "keep": "stays as it is", "skip": "not written"}
    lines.append(f"  file:  {plan.path or '-'} - {verdict[plan.action]} ({plan.reason})")
    return lines


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def card_command(
    write: Annotated[
        bool,
        typer.Option(
            "--write",
            help="Write the card file when its figures changed (the post-commit hook's call); "
            "without it, preview only.",
        ),
    ] = False,
    dest: Annotated[
        Path | None,
        typer.Option(
            "--dest",
            help="The cards folder to use instead of Roadmap Wrangler's (CRW_HOME\\cards or "
            "%LOCALAPPDATA%\\clauderoadmapwrangler\\cards); for tests.",
            show_default=False,
        ),
    ] = None,
    as_json: Annotated[
        bool, typer.Option("--json", help="Print the outcome and the card as JSON.")
    ] = False,
) -> None:
    """Preview the site card the committed roadmap gives, or write it (EP-174, D-48)."""
    cards = dest if dest is not None else default_cards_dir()
    try:
        outcome = run_card(verify.repo_root(), cards, write=write)
    except OSError as exc:
        fail("mwh card", f"could not write the card: {exc}", code=EXIT_FINDINGS)
    if as_json:
        emit_json(outcome.as_dict())
        return
    for line in describe(outcome, write=write):
        typer.echo(console_safe(line))


__all__ = [
    "CARD_BYTES",
    "FIELDS",
    "NAME",
    "NEXT_ROWS",
    "SIZES",
    "NoCardError",
    "Outcome",
    "Plan",
    "badge_at_head",
    "build_card",
    "card_command",
    "card_path",
    "default_cards_dir",
    "describe",
    "plan_write",
    "render",
    "run_card",
    "stack_badge",
    "write_card",
]
