"""EP-23 — stage labevents (the first large event table; two-pass bucketed loader).

Fixture tier (default): the contract facts the brief pins (item 1: ``load_class: large``,
``partitioned``, tie-broken sort keys — a verify since EP-169), the EP-17 governance flags
(item 2: ``comments`` free text, ``order_provider_id`` identifier — stamped from keys.yaml,
verify only), the spec shape of the new ``stage.mimiciv_hosp.labevents`` step (item 1: no
overrides — the contract stays the authority), and one real
``mwh build --tier fixture --select stage.mimiciv_hosp.labevents`` into a temp data root
(item 4: EP-18 layout, per-partition sortedness, manifest lines, ``status.json`` complete,
benchmark-ledger lines with ``phase = pass1|pass2|total``).

``tier("dev", needs="lake")``-marked (item 5): runs once the real lake's ``status.json``
marks ``mimiciv_hosp.labevents`` ``dev_ready`` (the ⏱ background job sorts buckets 0-4
first), else skips with a reason; skips too while the dev catalog predates the step.
Everything printed or asserted is counts, paths and booleans — never a row, never an
identifier value (a sortedness failure reports the file, not the ids).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import helpers
from mimicwarehouse import config, publish
from mimicwarehouse.cli import app
from mimicwarehouse.dag import benchmarks as benchmarks_mod
from mimicwarehouse.dag.spec import load_dag
from mimicwarehouse.fixtures import write as fixtures_write
from mimicwarehouse.loader import buckets as buckets_mod
from mimicwarehouse.loader import engine
from mimicwarehouse.loader import manifest as manifest_mod
from mimicwarehouse.loader import paths as loader_paths
from mimicwarehouse.schema.contract import Contract

pytestmark = pytest.mark.ep_23

HOSP = "mimiciv_hosp"
QN = f"{HOSP}.labevents"
STEP = f"stage.{QN}"
#: The tie-broken contract sort keys the brief pins (EP-169 adopted them; item 1 verify).
SORT_KEYS = ("subject_id", "charttime", "itemid")


@pytest.fixture
def data_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    root = helpers.tmp_data_root(monkeypatch, tmp_path)
    yield root
    config.configure()


# ---------------------------------------------------------------------------
# 1. Contract facts (brief item 1 — verify; fix in the EP-9 YAML with a dated note)
# ---------------------------------------------------------------------------


def test_contract_matches_brief(contract: Contract) -> None:
    table = contract.table(QN)
    assert table.load_class == "large"
    assert table.partitioned and table.subject_keyed
    assert table.sort_keys == SORT_KEYS
    assert table.time_column == "charttime"
    assert table.primary_key == ("labevent_id",)
    by_name = {c.name: c for c in table.columns}
    assert by_name["hadm_id"].nullable, "outpatient labs have no admission"
    assert by_name["value"].duckdb_type == "VARCHAR"
    assert by_name["valuenum"].duckdb_type == "DOUBLE"
    for name in (
        "specimen_id",
        "itemid",
        "valueuom",
        "ref_range_lower",
        "ref_range_upper",
        "flag",
        "priority",
        "storetime",
        "comments",
        "order_provider_id",
    ):
        assert name in by_name, f"contract lost column {name!r}"


# ---------------------------------------------------------------------------
# 2. Governance flags (brief item 2 — verify; stamped from keys.yaml, EP-17/FC-5)
# ---------------------------------------------------------------------------


def test_contract_flags(contract: Contract) -> None:
    table = contract.table(QN)
    assert table.free_text_columns() == ("comments",), (
        "labevents.comments must be flagged free_text so safe_query (EP-30) refuses it"
    )
    assert table.column("order_provider_id").identifier
    # the GOVERNANCE real-band ids are identifiers too; itemid is a dimension code, not one
    for name in ("labevent_id", "subject_id", "hadm_id", "specimen_id"):
        assert table.column(name).identifier, name
    assert not table.column("itemid").identifier


# ---------------------------------------------------------------------------
# 3. Spec shape: the new step carries no overrides; catalog depends on it
# ---------------------------------------------------------------------------


def test_spec_step_shape() -> None:
    dag = load_dag()
    step = dag.step(STEP)
    assert step.kind == "stage" and step.qualified_table == QN
    assert step.source == "mimic-iv-3.1/hosp/labevents.csv"
    assert {"stage", HOSP, "large"} <= set(step.tags) and "dims" not in step.tags
    assert step.tiers == ("fixture", "demo", "dev", "full")
    # size_class / partitioned / sort_by come from the contract, never the spec
    assert step.size_class is None and step.partitioned is None and step.sort_by is None
    assert STEP in dag.step("catalog").depends_on


# ---------------------------------------------------------------------------
# 4. Fixture build through the runner: layout, sortedness, manifests, status, ledger
# ---------------------------------------------------------------------------


def test_fixture_build_large_path(data_root: Path, contract: Contract) -> None:
    runner = helpers.cli_runner()
    result = runner.invoke(app, ["build", "--tier", "fixture", "--select", STEP])
    assert result.exit_code == 0, result.output

    settings = config.get_settings()
    lake = settings.lake_root("fixture")
    table = contract.table(QN)
    tdir = loader_paths.table_dir(lake, HOSP, "labevents")

    # EP-18 layout: one sorted part-0.parquet per partition; no raw_* / _sorting.tmp / .new
    bucket_dirs = {
        int(p.name.split("=", 1)[1]): p
        for p in tdir.iterdir()
        if p.is_dir() and p.name.startswith("subject_bucket=")
    }
    assert bucket_dirs, "no partition directories staged"
    for bdir in bucket_dirs.values():
        assert [p.name for p in sorted(bdir.iterdir())] == ["part-0.parquet"], bdir.name
    assert not publish.new_path_for(tdir).exists() and not publish.old_path_for(tdir).exists()
    progress = buckets_mod.read_progress(tdir)
    assert progress is not None and progress.pass1_done and progress.complete

    con = engine.open_build_connection(settings, tier="fixture", memory_limit="4GB")
    try:
        for bdir in bucket_dirs.values():
            part = (bdir / "part-0.parquet").as_posix().replace("'", "''")
            tup = f"row({', '.join(SORT_KEYS)})"
            (ok,) = con.execute(
                f"SELECT bool_and(cur >= prev) FROM ("
                f"SELECT {tup} AS cur, lag({tup}, 1, {tup}) OVER (ORDER BY file_row_number) "
                f"AS prev FROM read_parquet('{part}', file_row_number=true, "
                f"hive_partitioning=false))"
            ).fetchone()  # type: ignore[misc]
            assert ok is True, f"{bdir.name} is not sorted by {SORT_KEYS}"
    finally:
        con.close()

    # status.json complete + rows equal the committed fixture manifest's
    entry = manifest_mod.read_status(lake)["steps"][QN]
    fixture_rows = fixtures_write.load_manifest(fixtures_write.default_out_dir())["files"][
        f"mimic-iv-3.1/{table.csv_path}"
    ]["rows"]
    assert entry["rows"] == fixture_rows and entry["rejects"] == 0
    assert entry["tier_complete"] == "full" and entry["dev_ready"] is True
    assert entry["files"] == len(bucket_dirs)

    # one manifest line per partition file (the large path appends per finished bucket)
    lines: dict[str, dict[str, Any]] = {}
    for mpath in manifest_mod.manifests_dir(lake).glob("*.jsonl"):
        for line in mpath.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                lines[rec["path"]] = rec
    expected_paths = {
        f"core/{HOSP}/labevents/subject_bucket={n}/part-0.parquet" for n in bucket_dirs
    }
    assert set(lines) == expected_paths
    assert sum(rec["rows"] for rec in lines.values()) == fixture_rows

    # benchmark ledger: pass1 + pass2 + total for the step, one build summary line
    ledger = benchmarks_mod.read(settings)
    step_lines = ledger.filter(ledger["step"] == STEP)
    assert set(step_lines["phase"].to_list()) == {"pass1", "pass2", "total"}
    assert all(step_lines["ok"].to_list())
    assert ledger.filter(ledger["kind"] == "build").height == 1


# ---------------------------------------------------------------------------
# 5. Dev tier (brief item 5): green once the ⏱ job marks the step dev_ready
# ---------------------------------------------------------------------------


@pytest.mark.tier("dev", needs="lake")
def test_dev_counts_and_sortedness(dev_ready, contract: Contract) -> None:
    import duckdb

    from mimicwarehouse.catalog.connect import open_catalog

    # status.json keys steps by <schema>.<table> (loader/manifest.py), so the readiness
    # fixture takes the qualified table name, not the spec's step name
    entry = dev_ready(QN)
    assert entry["dev_ready"] is True

    settings = config.load_settings()
    lake = settings.lake_root("dev")

    # manifest rows per lake-relative path; last line wins per path (rebuilds append)
    rows_by_path: dict[str, int] = {}
    for mpath in manifest_mod.manifests_dir(lake).glob("*.jsonl"):
        for line in mpath.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            if f"{rec['schema']}.{rec['table']}" == QN:
                rows_by_path[rec["path"]] = rec["rows"]
    manifest_rows = sum(
        rows
        for path, rows in rows_by_path.items()
        if any(f"subject_bucket={b}/" in path for b in settings.dev_buckets)
    )
    assert manifest_rows > 0, "no manifest rows recorded for the dev buckets"

    con = open_catalog("dev")
    try:
        try:
            (total,) = con.execute(f"SELECT count(*) FROM {QN}").fetchone()  # type: ignore[misc]
        except duckdb.CatalogException:
            pytest.skip(
                "dev catalog predates the labevents step — refresh it with "
                "`mwh build --tier dev --select catalog`"
            )
        assert total > 0
        assert total == manifest_rows, (
            f"dev view count {total} != manifest rows {manifest_rows} for buckets "
            f"{list(settings.dev_buckets)}"
        )
        (outpatient,) = con.execute(f"SELECT count(*) FROM {QN} WHERE hadm_id IS NULL").fetchone()  # type: ignore[misc]
        assert outpatient > 0, "outpatient labs (NULL hadm_id) must exist — a count only"
    finally:
        con.close()

    # per-file row-group subject_id min/max are non-decreasing (sorted; EP-18 pass 2).
    # Values are compared, never printed — a failure names the file only (GOVERNANCE §4).
    meta_con = duckdb.connect()
    try:
        for b in settings.dev_buckets:
            part = loader_paths.table_dir(lake, HOSP, "labevents") / f"subject_bucket={b}"
            part = part / "part-0.parquet"
            assert part.is_file(), f"dev bucket {b} has no part-0.parquet"
            escaped = part.as_posix().replace("'", "''")
            groups = meta_con.execute(
                f"SELECT stats_min_value, stats_max_value FROM parquet_metadata('{escaped}') "
                "WHERE path_in_schema = 'subject_id' ORDER BY row_group_id"
            ).fetchall()
            assert groups, f"{part.name}: no row-group statistics"
            bounds = [(int(lo), int(hi)) for lo, hi in groups]
            ok = all(lo <= hi for lo, hi in bounds) and all(
                bounds[i][1] <= bounds[i + 1][0] for i in range(len(bounds) - 1)
            )
            assert ok, f"row-group subject_id ranges are not non-decreasing in bucket {b}"
    finally:
        meta_con.close()
