"""EP-29 — catalog & data dictionary (meta.*).

Fixture tier (default): the session ``fixture_lake_catalog`` (its runner build now
includes the ``meta.profile`` python step) is checked for the ``meta.tables`` /
``meta.columns`` dictionaries transcribed exactly from the EP-9 contract, the profile
aggregates (``null_pct`` in [0, 1]; identifier columns get NULL min/max in the profile
Parquet), the ``meta.row_counts`` manifest/profile agreement, the ``meta.itemids``
union view, the ``COMMENT ON``\\ s visible through ``duckdb_columns()`` /
``mwh sql --describe``, and the generated Markdown dictionary — deterministic
(byte-stable regeneration), guard-clean (the EP-4 scanner finds no real-band id), and
free of per-value counts (every table row's first cell is a contract column name or a
fixed metadata key).

``tier("dev")`` / ``tier("full")``-marked: the real catalogs carry profile-loaded
``meta.*`` numbers; ``meta.row_counts`` (source ``manifest``) for ``full`` equals
``status.json``; ``DATA-DICTIONARY.md`` exists and its header build id equals
``meta.catalog_info.build_id``. Everything asserted or printed is counts, schemas,
paths and contract text — never a row, never an identifier value.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

import helpers
from mimicwarehouse import config, guard
from mimicwarehouse.catalog.build import STAGED_SCHEMAS, unit_hint
from mimicwarehouse.catalog.dictionary import generate_dictionary
from mimicwarehouse.catalog.profile import profile_paths, wants_minmax
from mimicwarehouse.cli import app
from mimicwarehouse.dag.spec import load_dag

if TYPE_CHECKING:
    import duckdb as duckdb_mod

    from mimicwarehouse.config import Settings
    from mimicwarehouse.schema.contract import Contract

pytestmark = pytest.mark.ep_29

HOSP = "mimiciv_hosp"
ICU = "mimiciv_icu"

#: ``| Build id | `...` |`` in the generated header (the full-tier acceptance parses it).
BUILD_ID_LINE = re.compile(r"^\| Build id \| `(?P<build_id>[^`]+)` \|$", re.MULTILINE)


def _hosp_icu_tables(contract: Contract):
    return [t for s in STAGED_SCHEMAS for t in contract.by_schema(s)]


def _dictionary_build_id(text: str) -> str:
    match = BUILD_ID_LINE.search(text)
    assert match, "the dictionary header carries a parseable Build id row"
    return match.group("build_id")


# ---------------------------------------------------------------------------
# 1. Wiring: the python handler, the meta.profile step, the catalog dependency
# ---------------------------------------------------------------------------


def test_python_handler_and_profile_step_registered() -> None:
    from mimicwarehouse.dag.runner import STEP_HANDLERS, _run_python

    assert STEP_HANDLERS["python"] is _run_python
    dag = load_dag()
    step = dag.step("meta.profile")
    assert step.kind == "python"
    assert step.callable_name == "mimicwarehouse.catalog.profile:run_profile"
    assert set(step.depends_on) == {s.name for s in dag.steps if s.kind == "stage"}
    assert "meta.profile" in dag.step("catalog").depends_on
    ordered = [s.name for s in dag.ordered(select=["meta.profile", "catalog"], tier="fixture")]
    assert ordered == ["meta.profile", "catalog"], "profile runs before the catalog build"


# ---------------------------------------------------------------------------
# 2. The profile Parquet: aggregates only, identifiers get NULL min/max
# ---------------------------------------------------------------------------


def test_profile_parquet_written_and_identifier_minmax_null(
    fixture_lake_settings: Settings, contract: Contract
) -> None:
    import duckdb

    tables_path, columns_path = profile_paths(fixture_lake_settings.lake_root("fixture"), "fixture")
    assert tables_path.is_file() and columns_path.is_file()
    con = duckdb.connect()
    try:
        t_sql = tables_path.resolve().as_posix()
        c_sql = columns_path.resolve().as_posix()
        n_tables = con.execute(f"SELECT count(*) FROM read_parquet('{t_sql}')").fetchone()
        assert n_tables == (len(_hosp_icu_tables(contract)),)
        rows = con.execute(
            'SELECT "schema", "table", "column", null_pct, approx_distinct, '
            f"min_value IS NULL, max_value IS NULL FROM read_parquet('{c_sql}')"
        ).fetchall()
    finally:
        con.close()
    assert len(rows) == sum(len(t.columns) for t in _hosp_icu_tables(contract))
    by_column = {
        (f"{s}.{t}", c): (np, ad, min_null, max_null)
        for s, t, c, np, ad, min_null, max_null in rows
    }
    for table in _hosp_icu_tables(contract):
        for column in table.columns:
            np, ad, min_null, max_null = by_column[(table.qualified_name, column.name)]
            assert np is not None and 0.0 <= np <= 1.0, (table.qualified_name, column.name)
            assert ad is not None and ad >= 0
            if not wants_minmax(column):
                # identifiers and VARCHAR/BOOLEAN columns never carry extrema
                assert min_null and max_null, (table.qualified_name, column.name)
            if column.identifier:
                assert min_null and max_null, f"{column.name}: identifier min/max must be NULL"
    # a non-identifier numeric column does carry extrema (synthetic fixture values)
    itemid = by_column[(f"{HOSP}.d_labitems", "itemid")]
    assert not itemid[2] and not itemid[3]


# ---------------------------------------------------------------------------
# 3. meta.tables / meta.columns equal the contract
# ---------------------------------------------------------------------------


def test_meta_tables_match_contract(
    fixture_lake_catalog: duckdb_mod.DuckDBPyConnection, contract: Contract
) -> None:
    rows = fixture_lake_catalog.execute(
        'SELECT "schema", "table", description, kind, partitioned, row_count FROM meta.tables'
    ).fetchall()
    expected = {t.qualified_name: t for t in _hosp_icu_tables(contract)}
    assert {f"{s}.{t}" for s, t, *_ in rows} == set(expected)
    for s, t, description, kind, partitioned, row_count in rows:
        table = expected[f"{s}.{t}"]
        assert description == table.comment
        assert partitioned == table.partitioned
        assert kind == ("view" if table.partitioned else "table")
        assert row_count is not None and row_count >= 0, "every fixture table is staged"


def test_meta_columns_match_contract(
    fixture_lake_catalog: duckdb_mod.DuckDBPyConnection, contract: Contract
) -> None:
    rows = fixture_lake_catalog.execute(
        'SELECT "schema", "table", "column", ordinal, duckdb_type, nullable, description, '
        "is_identifier, is_free_text, unit_hint, null_pct, approx_distinct "
        "FROM meta.columns"
    ).fetchall()
    assert len(rows) == sum(len(t.columns) for t in _hosp_icu_tables(contract))
    by_name = {t.qualified_name: t for t in _hosp_icu_tables(contract)}
    for s, t, c, ordinal, duckdb_type, nullable, description, is_id, is_text, hint, np, ad in rows:
        table = by_name[f"{s}.{t}"]
        column = table.columns[ordinal - 1]
        assert column.name == c, "ordinal follows the contract column order"
        assert duckdb_type == column.duckdb_type
        assert nullable == column.nullable
        assert description == column.comment
        assert is_id == column.identifier
        assert is_text == column.free_text
        assert hint == unit_hint(contract, table, column)
        assert np is not None and 0.0 <= np <= 1.0, "the profile loaded into meta.columns"
        assert ad is not None


def test_flags_cover_the_governance_lists(contract: Contract) -> None:
    """The brief's identifier set and free-text pair are flagged in the shipped contract
    (EP-17 stamped them; EP-29 verifies the full set — never re-adds the machinery)."""
    flagged = {c.name for t in contract.tables for c in t.columns if c.identifier}
    for name in (
        "subject_id",
        "hadm_id",
        "stay_id",
        "transfer_id",
        "emar_id",
        "pharmacy_id",
        "poe_id",
        "orderid",
        "linkorderid",
        "caregiver_id",
        "labevent_id",
        "specimen_id",
        "microevent_id",
        "micro_specimen_id",
        "note_id",
    ):
        assert name in flagged, name
    assert contract.table(f"{HOSP}.labevents").column("comments").free_text
    assert contract.table(f"{HOSP}.microbiologyevents").column("comments").free_text


# ---------------------------------------------------------------------------
# 4. meta.row_counts: manifest and profile agree on the fixture lake
# ---------------------------------------------------------------------------


def test_row_counts_sources_agree(
    fixture_lake_catalog: duckdb_mod.DuckDBPyConnection, contract: Contract
) -> None:
    rows = fixture_lake_catalog.execute(
        'SELECT "schema" || \'.\' || "table", tier, rows, source FROM meta.row_counts'
    ).fetchall()
    n_tables = len(_hosp_icu_tables(contract))
    by_source: dict[str, dict[str, int]] = {"manifest": {}, "profile": {}}
    for qn, tier, count, source in rows:
        assert tier == "fixture"
        by_source[source][qn] = count
    assert len(by_source["manifest"]) == n_tables
    assert len(by_source["profile"]) == n_tables
    assert by_source["manifest"] == by_source["profile"], (
        "manifest-derived counts equal the profile scan on the fixture lake"
    )


# ---------------------------------------------------------------------------
# 5. meta.itemids unions both item dimensions
# ---------------------------------------------------------------------------


def test_itemids_unions_both_dims(fixture_lake_catalog: duckdb_mod.DuckDBPyConnection) -> None:
    con = fixture_lake_catalog
    d_items = con.execute(f"SELECT count(*) FROM {ICU}.d_items").fetchone()
    d_labitems = con.execute(f"SELECT count(*) FROM {HOSP}.d_labitems").fetchone()
    assert d_items is not None and d_labitems is not None
    total = con.execute("SELECT count(*) FROM meta.itemids").fetchone()
    assert total == (d_items[0] + d_labitems[0],)
    per_source = dict(
        con.execute("SELECT source, count(*) FROM meta.itemids GROUP BY source").fetchall()
    )
    assert per_source == {"icu": d_items[0], "hosp": d_labitems[0]}
    described = [d[0] for d in con.execute("DESCRIBE meta.itemids").fetchall()]
    assert described == [
        "source",
        "itemid",
        "label",
        "abbreviation",
        "linksto",
        "category",
        "fluid",
        "unitname",
        "param_type",
    ]


# ---------------------------------------------------------------------------
# 6. COMMENT ONs: visible via duckdb_columns() and mwh sql --describe
# ---------------------------------------------------------------------------


def test_comments_visible_via_duckdb_columns(
    fixture_lake_catalog: duckdb_mod.DuckDBPyConnection, contract: Contract
) -> None:
    row = fixture_lake_catalog.execute(
        "SELECT count(*) FROM duckdb_columns() "
        "WHERE schema_name LIKE 'mimiciv_%' AND comment IS NOT NULL"
    ).fetchone()
    assert row == (sum(len(t.columns) for t in _hosp_icu_tables(contract)),), (
        "every cataloged column carries its contract comment"
    )
    table_comments = fixture_lake_catalog.execute(
        "SELECT count(*) FROM duckdb_tables() WHERE schema_name LIKE 'mimiciv_%' "
        "AND comment IS NOT NULL"
    ).fetchone()
    view_comments = fixture_lake_catalog.execute(
        "SELECT count(*) FROM duckdb_views() WHERE schema_name LIKE 'mimiciv_%' "
        "AND comment IS NOT NULL"
    ).fetchone()
    assert table_comments is not None and view_comments is not None
    assert table_comments[0] + view_comments[0] == len(_hosp_icu_tables(contract))


def test_sql_describe_shows_comments(fixture_lake_settings: Settings) -> None:
    runner = helpers.cli_runner()
    try:
        result = runner.invoke(
            app,
            [
                "--data-root",
                str(fixture_lake_settings.data_root),
                "sql",
                "--tier",
                "fixture",
                "--format",
                "json",
                "--describe",
                f"{HOSP}.admissions",
            ],
        )
        assert result.exit_code == 0, result.output
        columns = json.loads(result.output)["columns"]
        by_name = {c["column"]: c for c in columns}
        assert by_name["admittime"]["comment"], "DESCRIBE surfaces the contract comment"
        assert all(c.get("comment") for c in columns)
    finally:
        config.configure()


# ---------------------------------------------------------------------------
# 7. The generated dictionary: deterministic, guard-clean, no per-value counts
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def dictionary_text(
    fixture_lake_settings: Settings, tmp_path_factory: pytest.TempPathFactory
) -> str:
    out = tmp_path_factory.mktemp("ep29-dict") / "DATA-DICTIONARY.md"
    first = generate_dictionary("fixture", fixture_lake_settings, out=out)
    assert first.path == out and out.is_file()
    text = out.read_text(encoding="utf-8")
    again = generate_dictionary("fixture", fixture_lake_settings, out=out)
    assert again.tables == first.tables
    assert out.read_text(encoding="utf-8") == text, "regeneration is byte-stable"
    return text


def test_dictionary_header_and_sections(
    dictionary_text: str,
    fixture_lake_catalog: duckdb_mod.DuckDBPyConnection,
    contract: Contract,
) -> None:
    info = fixture_lake_catalog.execute(
        "SELECT build_id, core_snapshot_id, duckdb_version FROM meta.catalog_info"
    ).fetchone()
    assert info is not None
    assert _dictionary_build_id(dictionary_text) == info[0]
    assert info[1] in dictionary_text and info[2] in dictionary_text
    assert "Disclosure sidecar pending EP-43" in dictionary_text
    assert "anchor_year_group" in dictionary_text and "91" in dictionary_text
    for table in _hosp_icu_tables(contract):
        assert f"### {table.qualified_name}\n" in dictionary_text


def test_dictionary_is_guard_clean_and_value_free(
    dictionary_text: str, contract: Contract, tmp_path: Path
) -> None:
    md = tmp_path / "DATA-DICTIONARY.md"
    md.write_text(dictionary_text, encoding="utf-8", newline="\n")
    violations = guard.scan([md], helpers.REPO_ROOT)
    assert violations == [], [f"{v.rule}: {v.detail}" for v in violations]
    # no bare 8-digit token anywhere (fmt_int separates thousands; guard G4's pattern)
    assert not guard.ID_TOKEN.search(dictionary_text)

    # every pipe-table row is either header metadata or a contract column — a per-value
    # frequency table would need value cells, which have nowhere to appear
    allowed = (
        {"", "column"}
        | {c.name for t in contract.tables for c in t.columns}
        | {
            "Build id",
            "Tier",
            "Core snapshot id",
            "DuckDB",
            "Catalog built at",
            "Dev buckets",
        }
    )
    for line in dictionary_text.splitlines():
        if not line.startswith("|"):
            continue
        first_cell = line.split("|")[1].strip()
        if set(first_cell) <= {"-"}:
            continue  # the |---| separator rows
        assert first_cell in allowed, f"unexpected table row: {first_cell!r}"


def test_dictionary_suppresses_small_distinct_counts(dictionary_text: str) -> None:
    # gender has two levels; the fixture lake is small, so `<11` must appear, and no
    # unsuppressed distinct count below the threshold may
    assert "<11" in dictionary_text


def test_dictionary_cli(fixture_lake_settings: Settings, tmp_path: Path) -> None:
    runner = helpers.cli_runner()
    out = tmp_path / "dict.md"
    try:
        result = runner.invoke(
            app,
            [
                "--data-root",
                str(fixture_lake_settings.data_root),
                "catalog",
                "dictionary",
                "--tier",
                "fixture",
                "--out",
                str(out),
            ],
        )
        assert result.exit_code == 0, result.output
        assert out.is_file()
        assert "31 table(s)" in result.output

        missing = runner.invoke(
            app,
            [
                "--data-root",
                str(fixture_lake_settings.data_root),
                "catalog",
                "dictionary",
                "--tier",
                "demo",
                "--out",
                str(tmp_path / "never.md"),
            ],
        )
        assert missing.exit_code == 2, missing.output
        assert not (tmp_path / "never.md").exists()
    finally:
        config.configure()


# ---------------------------------------------------------------------------
# 8. Real tiers: profile-loaded meta.*, manifest counts equal status.json, the
#    committed DATA-DICTIONARY.md names the full catalog's build id
# ---------------------------------------------------------------------------


@pytest.mark.tier("dev")
def test_dev_catalog_meta_profile_loaded(dev_catalog: Path) -> None:
    from mimicwarehouse.catalog.connect import open_catalog

    con = open_catalog("dev", settings=config.load_settings())
    try:
        row = con.execute(
            "SELECT count(*), count(null_pct), count(approx_distinct) FROM meta.columns"
        ).fetchone()
        assert row is not None
        total, with_null_pct, with_distinct = row
        assert total > 0 and with_null_pct == total and with_distinct == total
    finally:
        con.close()


@pytest.mark.tier("full", needs="lake")
def test_full_row_counts_equal_status_json(full_catalog: Path, contract: Contract) -> None:
    from mimicwarehouse.catalog.connect import open_catalog
    from mimicwarehouse.loader.manifest import read_status

    settings = config.load_settings()
    status = read_status(settings.lake_root("full"))["steps"]
    con = open_catalog("full", settings=settings)
    try:
        rows = con.execute(
            'SELECT "schema" || \'.\' || "table", rows FROM meta.row_counts '
            "WHERE tier = 'full' AND source = 'manifest'"
        ).fetchall()
    finally:
        con.close()
    counted = dict(rows)
    expected = {t.qualified_name for t in _hosp_icu_tables(contract)}
    assert set(counted) == expected
    for qn in expected:
        assert counted[qn] == status[qn]["rows"], qn


@pytest.mark.tier("full")
def test_committed_dictionary_matches_full_catalog(full_catalog: Path) -> None:
    from mimicwarehouse.catalog.connect import open_catalog
    from mimicwarehouse.catalog.dictionary import default_out_path

    path = default_out_path()
    assert path.is_file(), "mimicwarehouse/DATA-DICTIONARY.md is generated and committed"
    text = path.read_text(encoding="utf-8")
    con = open_catalog("full", settings=config.load_settings())
    try:
        info = con.execute("SELECT build_id, tier FROM meta.catalog_info").fetchone()
    finally:
        con.close()
    assert info is not None and info[1] == "full"
    assert _dictionary_build_id(text) == info[0]
    assert "Disclosure sidecar pending EP-43" in text
