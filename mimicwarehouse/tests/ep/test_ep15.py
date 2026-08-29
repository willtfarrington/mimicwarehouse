"""EP-15 — reading list + companion datasets + methods notes: the three files exist under
``docs/resources/``; ``reading.md`` carries all 38 numbered category headings exactly as the
roadmap README coverage table titles them (parsed live from ``roadmap/README.md``) and
>= 60 entries each carrying a DOI or ``https://`` link; ``datasets.md``'s register has the
nine required columns and >= 10 rows with a non-empty License and a literal ``yes``/``no``
"May enter git?"; ``methods-notes.md`` mentions each of the twelve caveats (keyword check;
"twelve" corrected at the EP-7 re-plan) and cites >= 5 distinct D-numbers; and no file
contains an isolated 8-digit token starting 1/2/3 (the exact G4 rule — ``guard.ID_TOKEN``;
amended EP-7). Docs-only brief (tier n/a) — no data, no network, no CLI.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import helpers
from mimicwarehouse import guard

pytestmark = pytest.mark.ep_15

RESOURCES_DIR = helpers.WORKSPACE / "docs" / "resources"
READING_MD = RESOURCES_DIR / "reading.md"
DATASETS_MD = RESOURCES_DIR / "datasets.md"
METHODS_MD = RESOURCES_DIR / "methods-notes.md"
INDEX_MD = RESOURCES_DIR / "README.md"
ROADMAP_README = helpers.REPO_ROOT / "roadmap" / "README.md"

#: The nine required register column headers (brief item 2).
DATASET_HEADERS = (
    "Dataset",
    "Version",
    "Steward / URL",
    "License",
    "Access (open / credentialed DUA)",
    "Size",
    "Schema note",
    "Planned use",
    "May enter git?",
)

#: The twelve caveats of brief item 3(a) ("eleven" corrected to twelve at EP-7), as
#: regex groups over ``methods-notes.md`` — one entry per caveat ("Demo = v2.2 schema and
#: no note demo" counts as one), every pattern in a group must match.
CAVEAT_PATTERNS: dict[str, tuple[str, ...]] = {
    "per-patient date shift": (r"date shift",),
    "dod ~1 y after last discharge": (r"`dod`",),
    "ICD-9 -> ICD-10 dual code sets": (r"ICD-9(?:-CM)? → ICD-10",),
    "discharge alive as competing event": (r"[Dd]ischarge alive", r"competing"),
    "ages >= 89 shown as 91": (r"\b91\b",),
    "ED 2.2 = 2011-2019 partial linkage": (r"2011–2019",),  # noqa: RUF001  (en dash, as the doc prints it)
    "Demo = v2.2 schema and no note demo": (r"v2\.2 schema", r"no note demo"),
    "labevents rows without hadm_id": (r"`labevents`", r"`hadm_id`"),
    "itemid/unit heterogeneity": (r"itemid", r"`valueuom`"),
    "duplicated storetimes": (r"storetime",),
    "emar vs prescriptions": (r"`emar`", r"`prescriptions`"),
    "time-of-day preservation": (r"[Tt]ime-of-day",),
}


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
def reading_text() -> str:
    assert READING_MD.is_file(), f"missing {READING_MD}"
    return READING_MD.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def datasets_text() -> str:
    assert DATASETS_MD.is_file(), f"missing {DATASETS_MD}"
    return DATASETS_MD.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def methods_text() -> str:
    assert METHODS_MD.is_file(), f"missing {METHODS_MD}"
    return METHODS_MD.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def coverage_rows() -> list[tuple[int, str]]:
    """The roadmap README coverage table as ``(number, category title)`` pairs."""
    text = ROADMAP_README.read_text(encoding="utf-8")
    for headers, rows in parse_tables(text):
        if headers[:2] == ["#", "Capability category"]:
            parsed = [(int(row[0]), row[1]) for row in rows]
            assert len(parsed) == 38, f"coverage table has {len(parsed)} rows, expected 38"
            return parsed
    pytest.fail("capability coverage table (# | Capability category | …) not found")


def test_reading_has_all_38_category_headings(
    reading_text: str, coverage_rows: list[tuple[int, str]]
) -> None:
    lines = reading_text.splitlines()
    for number, title in coverage_rows:
        assert f"## {number}. {title}" in lines, (
            f"reading.md lacks a '## {number}. {title}' heading matching the roadmap "
            "coverage table (a re-plan that re-titles a category updates both in one commit)"
        )
    # numbered headings match the table exactly - no extras, no renumbering
    numbered = [ln for ln in lines if re.match(r"## \d+\. ", ln)]
    assert len(numbered) == 38, f"{len(numbered)} numbered headings, expected exactly 38"


def test_reading_has_60_linked_entries(reading_text: str) -> None:
    entries = [
        ln
        for ln in reading_text.splitlines()
        if ln.startswith("- ") and ("https://doi.org/" in ln or "https://" in ln)
    ]
    assert len(entries) >= 60, f"reading.md has {len(entries)} linked entries, needs >= 60"
    # every entry under a category heading states whether it is free to read
    body = reading_text[reading_text.index("## 1. ") :]
    for ln in body.splitlines():
        if ln.startswith("- "):
            assert "free:" in ln, f"entry without a free-to-read marker: {ln[:80]}"


def test_datasets_register(datasets_text: str) -> None:
    tables = [(h, r) for h, r in parse_tables(datasets_text) if h and h[0] == "Dataset"]
    assert len(tables) == 1, "register table (Dataset | Version | …) not found in datasets.md"
    headers, rows = tables[0]
    assert tuple(headers) == DATASET_HEADERS
    assert len(rows) >= 10, f"register has {len(rows)} rows, needs >= 10"
    license_col = headers.index("License")
    git_col = headers.index("May enter git?")
    url_col = headers.index("Steward / URL")
    for row in rows:
        assert len(row) == len(headers), row[0]
        assert row[license_col], f"empty License cell in row {row[0]}"
        assert row[git_col] in {"yes", "no"}, (row[0], row[git_col])
        assert "https://" in row[url_col], (row[0], row[url_col])
    # the synthetic fixture is the only row that may enter git
    yes_rows = [row[0] for row in rows if row[git_col] == "yes"]
    assert yes_rows == ["mimicwarehouse synthetic fixture"], yes_rows


def test_datasets_states_the_two_plain_facts(datasets_text: str) -> None:
    # brief item 2: say plainly that no note demo exists and that ED 2.2 covers 2011-2019
    assert "no note demo" in datasets_text
    assert re.search(r"MIMIC-IV-ED 2\.2[\s\S]{0,40}2011–2019", datasets_text)  # noqa: RUF001


def test_methods_notes_mentions_all_twelve_caveats(methods_text: str) -> None:
    assert len(CAVEAT_PATTERNS) == 12
    missing = [
        name
        for name, patterns in CAVEAT_PATTERNS.items()
        if not all(re.search(p, methods_text) for p in patterns)
    ]
    assert not missing, f"methods-notes.md never mentions: {missing}"


def test_methods_notes_cites_five_distinct_decisions(methods_text: str) -> None:
    decisions = set(re.findall(r"\bD-\d+\b", methods_text))
    assert len(decisions) >= 5, f"only {sorted(decisions)} cited, need >= 5 distinct D-numbers"
    # the mandatory sentence and the claim-type ladder are present verbatim
    assert "MIMIC-IV analyses are retrospective" in methods_text
    for label in ("exploratory", "confirmatory", "predictive", "associational", "causal"):
        assert label in methods_text, f"claim-type ladder is missing '{label}'"


@pytest.mark.parametrize("path", [READING_MD, DATASETS_MD, METHODS_MD], ids=lambda p: p.name)
def test_docs_hygiene(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    # the exact G4 rule (amended EP-7): no isolated 8-digit token starting 1, 2 or 3 -
    # DOIs and 7-digit PMIDs are safe because a token adjacent to `.`/`/` never matches
    match = guard.ID_TOKEN.search(text)
    assert match is None, f"{path.name} contains an isolated 8-digit band token: {match.group()}"
    assert guard.id_band_hits(text.encode("utf-8")) == []
    # checked-on date present, hyphenated ISO; compact YYYYMMDD dates are refused (G4 hygiene)
    assert re.search(r"\*\*Checked on:\*\* \d{4}-\d{2}-\d{2}\b", text), path.name
    assert not re.search(r"\b20\d{6}\b", text), path.name
    # no `subject_id =`-style row fragments (docs carry column *names* only, never values)
    assert not re.search(r"\b(subject|hadm|stay|note|emar)_id\s*[=:]\s*\d", text), path.name
    # hook-clean bytes: LF only, single trailing newline, no trailing whitespace
    assert "\r" not in text, path.name
    assert text.endswith("\n") and not text.endswith("\n\n"), path.name
    assert all(line == line.rstrip() for line in text.splitlines()), path.name


def test_index_lists_the_three_files() -> None:
    assert INDEX_MD.is_file(), f"missing {INDEX_MD}"
    text = INDEX_MD.read_text(encoding="utf-8")
    tables = [(h, r) for h, r in parse_tables(text) if h == ["File", "What", "Owner EP"]]
    assert len(tables) == 1, "index table (File | What | Owner EP) not found"
    _, rows = tables[0]
    for name in ("reading.md", "datasets.md", "methods-notes.md"):
        matching = [row for row in rows if name in row[0]]
        assert len(matching) == 1, f"index does not list {name} exactly once"
        # shipped, so the row is a real link, not a "(planned)" placeholder
        assert f"[{name}]({name})" in matching[0][0]
        assert "planned" not in matching[0][0]
    assert guard.id_band_hits(text.encode("utf-8")) == []
