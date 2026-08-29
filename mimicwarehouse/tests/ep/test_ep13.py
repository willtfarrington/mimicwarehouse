"""EP-13 — repos & awesome-lists inventory: ``docs/resources/repos.md`` exists, its main
table carries the ten required columns with >= 15 verdict-bearing rows, every URL is
``https://``, the borrow map has >= 8 rows naming the borrowing EPs, and the file passes the
docs-hygiene rules (no real-band id token, no row-fragment shapes). Docs-only brief (tier
n/a) — no data, no network, no CLI.
"""

from __future__ import annotations

import re

import pytest

import helpers
from mimicwarehouse import guard

pytestmark = pytest.mark.ep_13

RESOURCES_DIR = helpers.WORKSPACE / "docs" / "resources"
REPOS_MD = RESOURCES_DIR / "repos.md"
INDEX_MD = RESOURCES_DIR / "README.md"

#: The ten required column headers (brief item 1); "Last activity" carries an
#: ``(as of <date>)`` suffix, so it is matched as a prefix.
MAIN_HEADERS = (
    "Resource",
    "URL",
    "License",
    "Last activity",
    "MIMIC target",
    "Python",
    "What it offers",
    "Verdict",
    "Used by / borrowed by",
    "Notes",
)
VERDICTS = {"adopt", "port", "ignore"}
#: The eight EPs the acceptance criteria say the borrow map must name (brief item 3).
BORROW_EPS = ("EP-17", "EP-10", "EP-37", "EP-46", "EP-50", "EP-110", "EP-58", "EP-138")


def parse_tables(text: str) -> list[tuple[list[str], list[list[str]]]]:
    """Every markdown pipe table in ``text`` as ``(headers, rows)``; the ``|---|`` separator
    line is consumed, cells are stripped."""
    tables: list[tuple[list[str], list[list[str]]]] = []
    lines = text.splitlines()
    i = 0

    def cells(line: str) -> list[str]:
        return [c.strip() for c in line.strip().strip("|").split("|")]

    while i < len(lines):
        line = lines[i]
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        if line.lstrip().startswith("|") and re.fullmatch(r"\s*\|[-:| ]+\|\s*", nxt):
            headers = cells(line)
            rows = []
            i += 2
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                rows.append(cells(lines[i]))
                i += 1
            tables.append((headers, rows))
        else:
            i += 1
    return tables


@pytest.fixture(scope="module")
def repos_text() -> str:
    assert REPOS_MD.is_file(), f"missing {REPOS_MD}"
    return REPOS_MD.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def main_table(repos_text: str) -> tuple[list[str], list[list[str]]]:
    for headers, rows in parse_tables(repos_text):
        if headers and headers[0] == MAIN_HEADERS[0] and "Verdict" in headers:
            return headers, rows
    pytest.fail("main table (Resource … Verdict … Notes) not found in repos.md")


def test_main_table_headers(main_table: tuple[list[str], list[list[str]]]) -> None:
    headers, _ = main_table
    assert len(headers) == len(MAIN_HEADERS)
    for got, want in zip(headers, MAIN_HEADERS, strict=True):
        assert got == want or got.startswith(want), (got, want)
    # the "Last activity" header carries a hyphenated ISO as-of date (G4 hygiene)
    assert re.search(r"Last activity \(as of \d{4}-\d{2}-\d{2}\)", headers[3])


def test_main_table_rows_verdicts_urls(main_table: tuple[list[str], list[list[str]]]) -> None:
    headers, rows = main_table
    assert len(rows) >= 15, f"main table has {len(rows)} rows, needs >= 15"
    url_col = headers.index("URL")
    verdict_col = headers.index("Verdict")
    for row in rows:
        assert len(row) == len(headers), row[0]
        assert row[url_col].startswith("https://"), (row[0], row[url_col])
        assert row[verdict_col] in VERDICTS, (row[0], row[verdict_col])
    # all three verdicts are actually in use
    assert {row[verdict_col] for row in rows} == VERDICTS


def test_checked_on_date_is_hyphenated_iso(repos_text: str) -> None:
    assert re.search(r"\*\*Checked on:\*\* \d{4}-\d{2}-\d{2}\b", repos_text)
    # compact YYYYMMDD dates are refused by the guard's G4 hygiene rule for docs tables
    assert not re.search(r"\b20\d{6}\b", repos_text)


def test_borrow_map(repos_text: str) -> None:
    tables = [
        (headers, rows) for headers, rows in parse_tables(repos_text) if headers[0] == "Our EP"
    ]
    assert len(tables) == 1, "borrow map table (Our EP | Resource | …) not found"
    headers, rows = tables[0]
    assert headers == ["Our EP", "Resource", "Exactly what we borrow"]
    assert len(rows) >= 8, f"borrow map has {len(rows)} rows, needs >= 8"
    ep_cells = " ".join(row[0] for row in rows)
    for ep in BORROW_EPS:
        assert re.search(rf"{ep}\b", ep_cells), f"borrow map does not name {ep}"


def test_docs_hygiene_no_ids_no_row_fragments(repos_text: str) -> None:
    # no token in the real MIMIC id bands (guard G4, same predicate the pre-commit hook runs)
    assert guard.id_band_hits(repos_text.encode("utf-8")) == []
    # no `subject_id =`-style row fragments (docs carry column *names* only, never values)
    assert not re.search(r"\b(subject|hadm|stay|note|emar)_id\s*[=:]\s*\d", repos_text)
    # hook-clean bytes: LF only, single trailing newline, no trailing whitespace
    assert "\r" not in repos_text
    assert repos_text.endswith("\n") and not repos_text.endswith("\n\n")
    assert all(line == line.rstrip() for line in repos_text.splitlines())


def test_awesome_lists_section(repos_text: str) -> None:
    # >= 3 curated lists, each a table row with a https:// URL and a MIMIC-tooling verdict cell
    tables = [(h, r) for h, r in parse_tables(repos_text) if h[0] == "List"]
    assert len(tables) == 1, "awesome-lists table not found"
    headers, rows = tables[0]
    assert "MIMIC-tooling section?" in headers
    assert len(rows) >= 3
    url_col = headers.index("URL")
    for row in rows:
        assert row[url_col].startswith("https://")


def test_index_lists_repos_md() -> None:
    assert INDEX_MD.is_file(), f"missing {INDEX_MD}"
    text = INDEX_MD.read_text(encoding="utf-8")
    tables = [(h, r) for h, r in parse_tables(text) if h == ["File", "What", "Owner EP"]]
    assert len(tables) == 1, "index table (File | What | Owner EP) not found"
    _, rows = tables[0]
    files = " ".join(row[0] for row in rows)
    assert "repos.md" in files and "raw-inventory.md" in files
    # EP-14/15 placeholders so their sessions have rows to land in
    for name in ("vocabularies.md", "reading.md", "datasets.md", "methods-notes.md"):
        assert name in files, f"index is missing the {name} placeholder"
    assert guard.id_band_hits(text.encode("utf-8")) == []
