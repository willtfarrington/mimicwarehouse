"""EP-21 — catalog builder (per-tier .duckdb).

Fixture tier (default): the session ``fixture_lake_catalog`` (a real ``fixture.duckdb``
built by the runner's ``catalog`` step over the runner-built fixture lake, EP-170
amendment 4) is checked for the published file (no ``.new`` left), the
``meta.catalog_info`` provenance row (DuckDB version = the EP-1 pin, ``dev_buckets``
recorded), the materialization decision (dims are tables, subject-keyed tables are
views, unstaged tables are omitted and listed ``missing``), the read-only refusals
(INSERT, ``.new`` path, version mismatch) and the rename-aside swap's documented
failure with a non-sharing reader. A crafted mini lake proves a ``tier_complete =
"dev"`` table appears in the ``dev`` catalog but not ``full``. The interim ``mwh sql``
refuses a crafted free-form statement with exit 2 (the governance acceptance clause).

``tier("dev")``-marked: ``open_catalog("dev")`` sees exactly the tables
``mwh catalog info`` reports. Everything asserted or printed is counts, schemas, paths
and metadata — never a row, never an identifier value.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

import helpers
from mimicwarehouse import config
from mimicwarehouse.catalog import build as build_mod
from mimicwarehouse.catalog.build import (
    CatalogSwapError,
    build_catalog,
    catalog_new_path,
    qualifies,
)
from mimicwarehouse.catalog.cli import FREE_FORM_MESSAGE
from mimicwarehouse.catalog.connect import CatalogOpenError, open_catalog
from mimicwarehouse.cli import app
from mimicwarehouse.dag.snapshot import read_snapshots
from mimicwarehouse.dag.spec import load_dag
from mimicwarehouse.loader.engine import pinned_duckdb_version
from mimicwarehouse.loader.manifest import update_status

if TYPE_CHECKING:
    import duckdb as duckdb_mod

    from mimicwarehouse.config import Settings
    from mimicwarehouse.schema.contract import Contract

pytestmark = pytest.mark.ep_21

HOSP = "mimiciv_hosp"
ICU = "mimiciv_icu"


@pytest.fixture
def data_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    root = helpers.tmp_data_root(monkeypatch, tmp_path)
    yield root
    config.configure()


@pytest.fixture(scope="module")
def mini_lake(tmp_path_factory: pytest.TempPathFactory) -> Settings:
    """A two-table fixture-tier lake (one partitioned + one dim) for the crafted
    qualification and swap tests — separate from the session lake so mutating its
    ``status.json`` never disturbs ``fixture_lake_catalog``."""
    from mimicwarehouse.config import Settings
    from mimicwarehouse.dag import runner

    root = tmp_path_factory.mktemp("ep21-mini")
    settings = Settings(data_root=root)
    result = runner.run(
        load_dag(),
        "fixture",
        select=["stage.mimiciv_hosp.patients", "stage.mimiciv_hosp.d_labitems"],
        settings=settings,
    )
    assert result.ok, [s.error for s in result.steps]
    return settings


def _catalog_tables(con: duckdb_mod.DuckDBPyConnection) -> dict[str, tuple[str, str | None]]:
    rows = con.execute(
        'SELECT "schema" || \'.\' || "table", kind, status FROM meta.catalog_tables'
    ).fetchall()
    return {qn: (kind, status) for qn, kind, status in rows}


def _information_schema_tables(con: duckdb_mod.DuckDBPyConnection) -> set[str]:
    rows = con.execute(
        "SELECT table_schema || '.' || table_name FROM information_schema.tables "
        f"WHERE table_schema IN ('{HOSP}', '{ICU}')"
    ).fetchall()
    return {r[0] for r in rows}


# ---------------------------------------------------------------------------
# 1. The published catalog file: exists, no .new left behind
# ---------------------------------------------------------------------------


def test_catalog_file_published_and_new_gone(fixture_lake_settings: Settings) -> None:
    path = fixture_lake_settings.catalog_path("fixture")
    assert path.is_file(), "the runner's catalog step publishes warehouse/fixture.duckdb"
    assert not catalog_new_path(path).exists(), "no .duckdb.new left after the swap"
    assert path.parent == fixture_lake_settings.layout["warehouse"]


# ---------------------------------------------------------------------------
# 2. meta.catalog_info: provenance row, pinned DuckDB version, dev_buckets recorded
# ---------------------------------------------------------------------------


def test_catalog_info_row(
    fixture_lake_catalog: duckdb_mod.DuckDBPyConnection, fixture_lake_settings: Settings
) -> None:
    import duckdb

    rows = fixture_lake_catalog.execute(
        "SELECT tier, duckdb_version, package_version, core_snapshot_id, k_default, "
        "dev_buckets, build_id, lake_root FROM meta.catalog_info"
    ).fetchall()
    assert len(rows) == 1, "meta.catalog_info is a single row"
    tier, version, package_version, snapshot_id, k_default, dev_buckets, build_id, lake_root = rows[
        0
    ]
    assert tier == "fixture"
    assert version == duckdb.__version__ == pinned_duckdb_version()
    assert package_version
    assert k_default == 11
    assert json.loads(dev_buckets) == list(fixture_lake_settings.dev_buckets)
    assert "-fixture-" in build_id
    lake = fixture_lake_settings.lake_root("fixture")
    assert Path(lake_root) == lake.resolve()
    # the step's snapshot id agrees with the run-end entry over the same manifests
    history = [e for e in read_snapshots(lake) if e["layer"] == "core" and e["tier"] == "fixture"]
    assert history and history[-1]["snapshot_id"] == snapshot_id


# ---------------------------------------------------------------------------
# 3. Materialization: dims are tables, subject-keyed are views, unstaged are omitted
# ---------------------------------------------------------------------------


def test_tables_views_and_missing_match_the_staged_set(
    fixture_lake_catalog: duckdb_mod.DuckDBPyConnection, contract: Contract
) -> None:
    staged = {s.qualified_table for s in load_dag().steps if s.kind == "stage"}
    hosp_icu = {t.qualified_name for t in contract.tables if t.schema_name in (HOSP, ICU)}
    listed = _catalog_tables(fixture_lake_catalog)
    assert set(listed) == hosp_icu, "meta.catalog_tables lists all 31 hosp/icu tables"

    present = {qn for qn, (kind, _) in listed.items() if kind != "missing"}
    assert present == staged, "exactly the staged tables are cataloged"
    for qn in staged:
        assert qn is not None
        kind, status = listed[qn]
        expected_kind = "view" if contract.table(qn).partitioned else "table"
        assert kind == expected_kind, f"{qn}: {kind} != {expected_kind} (DESIGN §21 decision)"
        assert status == "full", qn
    for qn in hosp_icu - present:
        assert listed[qn] == ("missing", None), f"{qn}: unstaged tables are omitted"

    # the catalog itself holds exactly the non-missing names — never an empty view
    assert _information_schema_tables(fixture_lake_catalog) == present

    # a cataloged view projects the contract columns in order (no subject_bucket leak)
    described = fixture_lake_catalog.execute(f"DESCRIBE {HOSP}.admissions").fetchall()
    assert [d[0] for d in described] == list(contract.table(f"{HOSP}.admissions").column_names)


# ---------------------------------------------------------------------------
# 4. Read-only: a write through open_catalog raises
# ---------------------------------------------------------------------------


def test_open_catalog_refuses_writes(
    fixture_lake_catalog: duckdb_mod.DuckDBPyConnection,
) -> None:
    import duckdb

    with pytest.raises(duckdb.Error, match="read-only"):
        fixture_lake_catalog.execute(
            # 6 values since EP-22 added the map_notes column
            "INSERT INTO meta.catalog_tables VALUES ('meta', 'probe', 'table', NULL, NULL, NULL)"
        )


# ---------------------------------------------------------------------------
# 5. Refusals: a .new path, a foreign/mismatched catalog
# ---------------------------------------------------------------------------


def test_open_catalog_refuses_a_new_path(fixture_lake_settings: Settings, tmp_path: Path) -> None:
    unpublished = tmp_path / "fixture.duckdb.new"
    unpublished.write_bytes(b"")
    with pytest.raises(CatalogOpenError, match=r"unpublished \.new"):
        open_catalog("fixture", settings=fixture_lake_settings, path=unpublished)


def test_open_catalog_refuses_a_version_mismatch(
    fixture_lake_settings: Settings, tmp_path: Path
) -> None:
    import duckdb

    crafted = tmp_path / "crafted.duckdb"
    con = duckdb.connect(str(crafted))
    con.execute("CREATE SCHEMA meta")
    con.execute("CREATE TABLE meta.catalog_info (duckdb_version VARCHAR, dev_buckets VARCHAR)")
    con.execute("INSERT INTO meta.catalog_info VALUES ('0.0.0', '[0,1,2,3,4]')")
    con.close()
    with pytest.raises(CatalogOpenError, match=r"built with DuckDB 0\.0\.0"):
        open_catalog("fixture", settings=fixture_lake_settings, path=crafted)


# ---------------------------------------------------------------------------
# 6. The swap with a non-sharing reader: documented message, old catalog left valid
# ---------------------------------------------------------------------------


def test_swap_with_open_reader_raises_and_keeps_old_catalog(mini_lake: Settings) -> None:
    lake = mini_lake.lake_root("fixture")
    first = build_catalog("fixture", mini_lake, lake_root=lake)
    assert first.path.is_file()

    with (
        first.path.open("rb"),  # a plain handle shares no DELETE — the rename must fail
        pytest.raises(CatalogSwapError, match="close the app/notebooks and rerun"),
    ):
        build_catalog("fixture", mini_lake, lake_root=lake)

    # the old catalog is intact and still opens; the next build clears the stale .new
    con = open_catalog("fixture", settings=mini_lake)
    try:
        assert con.execute("SELECT count(*) FROM meta.catalog_info").fetchone() == (1,)
    finally:
        con.close()
    rebuilt = build_catalog("fixture", mini_lake, lake_root=lake)
    assert rebuilt.path.is_file() and not catalog_new_path(rebuilt.path).exists()


# ---------------------------------------------------------------------------
# 7. Tier qualification: tier_complete="dev" appears in dev, not full
# ---------------------------------------------------------------------------


def test_dev_complete_table_only_in_the_dev_catalog(mini_lake: Settings) -> None:
    lake = mini_lake.lake_root("fixture")
    update_status(lake, f"{HOSP}.patients", tier_complete="dev")

    assert qualifies({"tier_complete": "dev", "dev_ready": True}, "dev")
    assert not qualifies({"tier_complete": "dev", "dev_ready": True}, "full")

    build_catalog("dev", mini_lake, lake_root=lake)
    build_catalog("full", mini_lake, lake_root=lake)

    dev_con = open_catalog("dev", settings=mini_lake)
    try:
        listed = _catalog_tables(dev_con)
        assert listed[f"{HOSP}.patients"][0] == "view"
        assert listed[f"{HOSP}.d_labitems"][0] == "table"
        assert f"{HOSP}.patients" in _information_schema_tables(dev_con)
        # the dev view carries the partition filter of settings.dev_buckets
        view_row = dev_con.execute(
            "SELECT sql FROM duckdb_views() WHERE schema_name = ? AND view_name = 'patients'",
            [HOSP],
        ).fetchone()
        assert view_row is not None
        buckets = ", ".join(str(b) for b in mini_lake.dev_buckets)
        assert f"subject_bucket IN ({buckets})" in view_row[0]
    finally:
        dev_con.close()

    full_con = open_catalog("full", settings=mini_lake)
    try:
        listed = _catalog_tables(full_con)
        assert listed[f"{HOSP}.patients"] == ("missing", "dev")
        assert f"{HOSP}.patients" not in _information_schema_tables(full_con)
        assert listed[f"{HOSP}.d_labitems"][0] == "table"
    finally:
        full_con.close()


# ---------------------------------------------------------------------------
# 8. The interim mwh sql: metadata/counts only; free-form SQL exits 2 (governance clause)
# ---------------------------------------------------------------------------


def test_sql_refuses_free_form(data_root: Path) -> None:
    runner = helpers.cli_runner()
    result = runner.invoke(app, ["sql", "--tier", "fixture", f"SELECT * FROM {HOSP}.patients"])
    assert result.exit_code == 2, result.output
    assert FREE_FORM_MESSAGE in result.output


def test_sql_metadata_surface(fixture_lake_settings: Settings) -> None:
    runner = helpers.cli_runner()
    root = ["--data-root", str(fixture_lake_settings.data_root)]
    try:
        listed = runner.invoke(app, [*root, "sql", "--tier", "fixture", "--tables"])
        assert listed.exit_code == 0, listed.output
        assert f"{HOSP}.admissions" in listed.output and "meta.catalog_info" in listed.output

        counted = runner.invoke(
            app,
            [*root, "sql", "--tier", "fixture", "--format", "json", "--count", f"{HOSP}.patients"],
        )
        assert counted.exit_code == 0, counted.output
        payload = json.loads(counted.output)
        assert payload == {
            "tier": "fixture",
            "table": f"{HOSP}.patients",
            "count": 120,  # the committed fixture's 120 synthetic subjects (EP-11)
            "suppressed": False,
            "k": 11,
        }

        # a count under the threshold is suppressed (GOVERNANCE §5; k raised to prove it)
        small = runner.invoke(
            app,
            [*root, "sql", "--tier", "fixture", "--k", "1000", "--count", f"{HOSP}.patients"],
        )
        assert small.exit_code == 0, small.output
        assert "suppressed" in small.output and "120" not in small.output

        described = runner.invoke(
            app,
            [*root, "sql", "--tier", "fixture", "--format", "json", "--describe", f"{ICU}.d_items"],
        )
        assert described.exit_code == 0, described.output
        columns = [c["column"] for c in json.loads(described.output)["columns"]]
        assert columns[0] == "itemid"

        bad = runner.invoke(app, [*root, "sql", "--tier", "fixture", "--count", "no_such.table"])
        assert bad.exit_code == 2 and "no table" in bad.output
    finally:
        config.configure()


def test_catalog_info_cli(fixture_lake_settings: Settings) -> None:
    runner = helpers.cli_runner()
    try:
        result = runner.invoke(
            app,
            [
                "--data-root",
                str(fixture_lake_settings.data_root),
                "catalog",
                "info",
                "--tier",
                "fixture",
                "--json",
            ],
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["catalog_info"]["tier"] == "fixture"
        kinds = {f"{t['schema']}.{t['table']}": t["kind"] for t in payload["catalog_tables"]}
        assert len(kinds) == 31
        assert kinds[f"{HOSP}.admissions"] == "view"
        assert kinds[f"{HOSP}.d_labitems"] == "table"
        assert kinds[f"{ICU}.chartevents"] == "missing"  # EP-26 stages it
    finally:
        config.configure()


# ---------------------------------------------------------------------------
# 9. Module shape: the runner registers the real handler; catalog/__init__ stays lean
# ---------------------------------------------------------------------------


def test_catalog_step_handler_registered() -> None:
    from mimicwarehouse.dag.runner import STEP_HANDLERS, _run_catalog

    assert STEP_HANDLERS["catalog"] is _run_catalog
    assert build_mod.CATALOG_SCHEMAS == (HOSP, ICU, "mimiciv_derived", "meta", "marts")


# ---------------------------------------------------------------------------
# 10. Real dev catalog: open_catalog sees exactly what mwh catalog info reports
# ---------------------------------------------------------------------------


@pytest.mark.tier("dev")
def test_dev_catalog_matches_info(item_tier: str) -> None:
    settings = config.load_settings()
    con = open_catalog(item_tier, settings=settings)
    try:
        listed = _catalog_tables(con)
        present = {qn for qn, (kind, _) in listed.items() if kind != "missing"}
        assert present == _information_schema_tables(con)
        assert len(listed) == 31
    finally:
        con.close()
