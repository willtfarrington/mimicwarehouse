"""EP-14 — ontologies & vocabularies inventory: ``docs/resources/vocabularies.md`` exists,
the register table carries the ten required columns with >= 15 rows (every row a non-empty
License and a literal ``yes``/``no`` Redistributable cell), the "Needed by EP" column names
EP-39 / EP-40 / EP-41/42 / EP-143, the ``source.yaml`` template block parses as YAML with
the DESIGN §19 keys, the which-EP-needs-what table exists, and the file passes the docs
hygiene rules (no real-band id token, hyphenated ISO dates). Docs-only brief (tier n/a) —
no data, no network, no CLI.
"""

from __future__ import annotations

import re

import pytest
import yaml

import helpers
from mimicwarehouse import guard

pytestmark = pytest.mark.ep_14

RESOURCES_DIR = helpers.WORKSPACE / "docs" / "resources"
VOCAB_MD = RESOURCES_DIR / "vocabularies.md"
INDEX_MD = RESOURCES_DIR / "README.md"

#: The ten required register column headers (brief item 1).
REGISTER_HEADERS = (
    "Vocabulary",
    "Steward / URL",
    "Version cadence",
    "License",
    "Registration / DUA",
    "Redistributable in this repo?",
    "Where it appears in MIMIC-IV",
    "Needed by EP",
    "v1 verdict",
    "How to obtain (steps)",
)
VERDICTS = {"use", "later", "flag"}
#: EPs the acceptance criteria say the "Needed by EP" column must name (brief item 3 /
#: verification). EP-41/42 is written as one token in the doc, so EP-41 matches it.
NEEDED_EPS = ("EP-39", "EP-40", "EP-41", "EP-143")
#: Top-level keys of the ``source.yaml`` template (brief item 2), in order.
SOURCE_YAML_KEYS = (
    "name",
    "version",
    "release_date",
    "url",
    "license",
    "license_url",
    "registration_required",
    "redistributable",
    "obtained_on",
    "obtained_by",
    "files",
    "columns_of_interest",
    "used_by_eps",
    "notes",
)


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
def vocab_text() -> str:
    assert VOCAB_MD.is_file(), f"missing {VOCAB_MD}"
    return VOCAB_MD.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def register(vocab_text: str) -> tuple[list[str], list[list[str]]]:
    for headers, rows in parse_tables(vocab_text):
        if headers and headers[0] == REGISTER_HEADERS[0] and "v1 verdict" in headers:
            return headers, rows
    pytest.fail("register table (Vocabulary … v1 verdict …) not found in vocabularies.md")


def test_register_headers(register: tuple[list[str], list[list[str]]]) -> None:
    headers, _ = register
    assert tuple(headers) == REGISTER_HEADERS


def test_register_rows(register: tuple[list[str], list[list[str]]]) -> None:
    headers, rows = register
    assert len(rows) >= 15, f"register has {len(rows)} rows, needs >= 15"
    url_col = headers.index("Steward / URL")
    license_col = headers.index("License")
    redis_col = headers.index("Redistributable in this repo?")
    verdict_col = headers.index("v1 verdict")
    obtain_col = headers.index("How to obtain (steps)")
    for row in rows:
        assert len(row) == len(headers), row[0]
        assert "https://" in row[url_col], (row[0], row[url_col])
        assert row[license_col], f"empty License cell in row {row[0]}"
        assert row[redis_col] in {"yes", "no"}, (row[0], row[redis_col])
        assert row[verdict_col] in VERDICTS, (row[0], row[verdict_col])
        # owner-executable numbered steps
        assert row[obtain_col].startswith("1."), (row[0], row[obtain_col])
    # all three verdicts are actually in use
    assert {row[verdict_col] for row in rows} == VERDICTS


def test_needed_by_ep_coverage(register: tuple[list[str], list[list[str]]]) -> None:
    headers, rows = register
    needed = " ".join(row[headers.index("Needed by EP")] for row in rows)
    for ep in NEEDED_EPS:
        assert re.search(rf"{ep}\b", needed), f'"Needed by EP" column never names {ep}'


def test_source_yaml_template(vocab_text: str) -> None:
    blocks = re.findall(r"```yaml\n(.*?)```", vocab_text, flags=re.DOTALL)
    assert len(blocks) == 1, "expected exactly one fenced yaml block (the source.yaml template)"
    doc = yaml.safe_load(blocks[0])
    assert isinstance(doc, dict)
    assert tuple(doc) == SOURCE_YAML_KEYS
    assert isinstance(doc["files"], list) and doc["files"]
    for entry in doc["files"]:
        assert tuple(entry) == ("name", "sha256", "bytes")
    assert isinstance(doc["registration_required"], bool)
    assert isinstance(doc["redistributable"], bool)


def test_which_ep_table(vocab_text: str) -> None:
    tables = [(h, r) for h, r in parse_tables(vocab_text) if h and h[0] == "EP"]
    assert len(tables) == 1, "which-EP-needs-what table (EP | Vocabulary | …) not found"
    headers, rows = tables[0]
    assert headers == ["EP", "Vocabulary", "Use", "Free path in v1?"]
    assert len(rows) >= 5
    ep_cells = " ".join(row[0] for row in rows)
    for ep in ("EP-37", "EP-39", "EP-40", "EP-41", "EP-143"):
        assert re.search(rf"{ep}\b", ep_cells), f"which-EP table does not name {ep}"


def test_checked_on_date_is_hyphenated_iso(vocab_text: str) -> None:
    assert re.search(r"\*\*Checked on:\*\* \d{4}-\d{2}-\d{2}\b", vocab_text)
    # compact YYYYMMDD dates are refused by the guard's G4 hygiene rule for docs tables
    assert not re.search(r"\b20\d{6}\b", vocab_text)


def test_docs_hygiene_no_ids_no_row_fragments(vocab_text: str) -> None:
    # no token in the real MIMIC id bands (guard G4, same predicate the pre-commit hook runs)
    assert guard.id_band_hits(vocab_text.encode("utf-8")) == []
    # no `subject_id =`-style row fragments (docs carry column *names* only, never values)
    assert not re.search(r"\b(subject|hadm|stay|note|emar)_id\s*[=:]\s*\d", vocab_text)
    # hook-clean bytes: LF only, single trailing newline, no trailing whitespace
    assert "\r" not in vocab_text
    assert vocab_text.endswith("\n") and not vocab_text.endswith("\n\n")
    assert all(line == line.rstrip() for line in vocab_text.splitlines())


def test_index_lists_vocabularies_md() -> None:
    assert INDEX_MD.is_file(), f"missing {INDEX_MD}"
    text = INDEX_MD.read_text(encoding="utf-8")
    tables = [(h, r) for h, r in parse_tables(text) if h == ["File", "What", "Owner EP"]]
    assert len(tables) == 1, "index table (File | What | Owner EP) not found"
    _, rows = tables[0]
    vocab_rows = [row for row in rows if "vocabularies.md" in row[0]]
    assert len(vocab_rows) == 1, "index does not list vocabularies.md exactly once"
    # shipped, so the row is a real link, not a "(planned)" placeholder
    assert "[vocabularies.md](vocabularies.md)" in vocab_rows[0][0]
    assert "planned" not in vocab_rows[0][0]
    assert guard.id_band_hits(text.encode("utf-8")) == []
