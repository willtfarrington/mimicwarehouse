"""EP-24 — stage emar + emar_detail (two large subject-keyed tables, one sequential job).

Fixture tier (default): the contract facts the brief pins (item 1: both ``load_class:
large``, partitioned, tie-broken sort keys — a verify since EP-169/EP-170 [FC-3]), the
EP-17 governance flags (item 2: ``emar_id``/``poe_id``/``pharmacy_id``/``enter_provider_id``
identifiers stamped from keys.yaml — verify only [FC-5]; no free-text flags: the
``emar_detail`` dose/product varchars are administration labels, not notes), the spec shape
of the two new steps (item 1: ``emar_detail`` depends on ``emar`` — one background job,
single writer; no overrides — the contract stays the authority), and one real
``mwh build --tier fixture --tag emar`` into a temp data root (item 4: EP-18 layout,
per-partition sortedness, manifest lines, ``status.json`` complete, benchmark-ledger lines
with ``phase = pass1|pass2|total`` per step, and every ``emar_detail`` row joining an
``emar`` row on ``(emar_id, emar_seq)`` — an unmatched **count**, in-process).

``tier("dev", needs="lake")``-marked (item 5): runs once the real lake's ``status.json``
marks both tables ``dev_ready`` (the ⏱ background job sorts buckets 0-4 first), else skips
with a reason [VT-1]; skips too while the dev catalog predates the steps. The dev-tier
unmatched-``emar_detail`` count is recorded, never failed on (the brief expects 0 or a
small documented number). Everything printed or asserted is counts, paths and booleans —
never a row, never an identifier value (a sortedness failure reports the file, not the ids).

Note (2026-08-29): the brief's item-2 expectation ``emar_detail.parent_field_ordinal
DOUBLE`` is planning-era and was **rejected** — the vendored mimic-code
``postgres/create.sql`` declares ``VARCHAR(10)`` (values like '1.1' vs '1.10' would
collide as DOUBLE), and the EP-9 contract already says VARCHAR. Tested as VARCHAR below.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import helpers
from mimicwarehouse import config, paths
from mimicwarehouse.cli import app
from mimicwarehouse.dag import benchmarks as benchmarks_mod
from mimicwarehouse.dag.spec import load_dag
from mimicwarehouse.fixtures import write as fixtures_write
from mimicwarehouse.loader import buckets as buckets_mod
from mimicwarehouse.loader import engine
from mimicwarehouse.loader import manifest as manifest_mod
from mimicwarehouse.loader import paths as loader_paths
from mimicwarehouse.schema.contract import Contract

pytestmark = pytest.mark.ep_24

HOSP = "mimiciv_hosp"
QN_EMAR = f"{HOSP}.emar"
QN_DET = f"{HOSP}.emar_detail"
STEP_EMAR = f"stage.{QN_EMAR}"
STEP_DET = f"stage.{QN_DET}"
#: The tie-broken contract sort keys the brief pins (EP-169 adopted them; item 1 verify).
SORT_EMAR = ("subject_id", "charttime", "emar_seq")
SORT_DET = ("subject_id", "emar_id", "emar_seq", "parent_field_ordinal")
#: The non-null prefix of SORT_DET used for sortedness checks: parent_field_ordinal is
#: nullable (NULL marks the summary row) and the loader sorts NULLS LAST, so tuple
#: comparisons over it would go NULL; the prefix must still be non-decreasing.
SORT_DET_PREFIX = ("subject_id", "emar_id", "emar_seq")


@pytest.fixture
def data_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    root = helpers.tmp_data_root(monkeypatch, tmp_path)
    yield root
    config.configure()


# ---------------------------------------------------------------------------
# 1. Contract facts (brief item 1/2 — verify; fix in the EP-9 YAML with a dated note)
# ---------------------------------------------------------------------------


def test_contract_matches_brief_emar(contract: Contract) -> None:
    table = contract.table(QN_EMAR)
    assert table.load_class == "large"
    assert table.partitioned and table.subject_keyed
    assert table.sort_keys == SORT_EMAR
    assert table.time_column == "charttime"
    assert table.primary_key == ("emar_id",)
    by_name = {c.name: c for c in table.columns}
    assert by_name["emar_id"].duckdb_type == "VARCHAR", "emar_id embeds the subject id"
    assert not by_name["emar_id"].nullable
    assert by_name["emar_seq"].duckdb_type == "INTEGER"
    assert by_name["pharmacy_id"].duckdb_type == "INTEGER"
    for name in ("charttime", "scheduletime", "storetime"):
        assert by_name[name].duckdb_type == "TIMESTAMP", name
    assert by_name["hadm_id"].nullable, "eMAR events outside an admission have no hadm_id"
    for name in ("poe_id", "enter_provider_id", "medication", "event_txt"):
        assert name in by_name, f"contract lost column {name!r}"


def test_contract_matches_brief_emar_detail(contract: Contract) -> None:
    table = contract.table(QN_DET)
    assert table.load_class == "large"
    assert table.partitioned and table.subject_keyed
    assert table.sort_keys == SORT_DET
    assert table.time_column is None, "emar_detail has no timestamp; its time is emar.charttime"
    assert table.primary_key is None, "no upstream primary key (keys.yaml)"
    by_name = {c.name: c for c in table.columns}
    assert by_name["emar_seq"].duckdb_type == "INTEGER"
    assert by_name["pharmacy_id"].duckdb_type == "INTEGER"
    # brief said DOUBLE; upstream create.sql says VARCHAR(10) — see the module docstring
    assert by_name["parent_field_ordinal"].duckdb_type == "VARCHAR"
    assert by_name["parent_field_ordinal"].nullable, "NULL marks the summary row"
    for name in (
        "administration_type",
        "reason_for_no_barcode",
        "dose_due",
        "dose_given",
        "dose_given_unit",
        "product_description",
        "product_description_other",
        "infusion_rate",
        "route",
    ):
        assert name in by_name, f"contract lost column {name!r}"


# ---------------------------------------------------------------------------
# 2. Governance flags (brief item 2 — verify; stamped from keys.yaml, EP-17/FC-5)
# ---------------------------------------------------------------------------


def test_contract_flags(contract: Contract) -> None:
    emar = contract.table(QN_EMAR)
    det = contract.table(QN_DET)
    # emar_id embeds '<subject_id>-<n>' so it is an identifier for safe_query (EP-30),
    # like the other linkage/staff ids the brief names (keys.yaml identifiers list)
    for name in ("emar_id", "poe_id", "pharmacy_id", "enter_provider_id", "subject_id", "hadm_id"):
        assert emar.column(name).identifier, name
    for name in ("emar_id", "pharmacy_id", "subject_id"):
        assert det.column(name).identifier, name
    # the dose/product/barcode varchars are drug/administration labels, not notes —
    # safe_query's long-string heuristic covers them (brief Context; keys.yaml free_text)
    assert emar.free_text_columns() == ()
    assert det.free_text_columns() == ()


# ---------------------------------------------------------------------------
# 3. Spec shape: two steps, sequential; no overrides; catalog depends on both
# ---------------------------------------------------------------------------


def test_spec_step_shape() -> None:
    dag = load_dag()
    emar = dag.step(STEP_EMAR)
    det = dag.step(STEP_DET)
    assert emar.kind == "stage" and emar.qualified_table == QN_EMAR
    assert det.kind == "stage" and det.qualified_table == QN_DET
    assert emar.source == "mimic-iv-3.1/hosp/emar.csv"
    assert det.source == "mimic-iv-3.1/hosp/emar_detail.csv"
    # one background job, sequential steps (single writer): detail waits for emar
    assert det.depends_on == (STEP_EMAR,)
    for step in (emar, det):
        assert {"stage", HOSP, "large", "emar"} <= set(step.tags) and "dims" not in step.tags
        assert step.tiers == ("fixture", "demo", "dev", "full")
        # size_class / partitioned / sort_by come from the contract, never the spec
        assert step.size_class is None and step.partitioned is None and step.sort_by is None
    assert {STEP_EMAR, STEP_DET} <= set(dag.step("catalog").depends_on)
    # --tag emar selects exactly the two steps, emar first
    names = [s.name for s in dag.ordered(tags=["emar"], tier="full")]
    assert names == [STEP_EMAR, STEP_DET]


# ---------------------------------------------------------------------------
# 4. Fixture build through the runner: layout, sortedness, manifests, status,
#    ledger, and the (emar_id, emar_seq) join closure
# ---------------------------------------------------------------------------


def test_fixture_build_large_path(data_root: Path, contract: Contract) -> None:
    runner = helpers.cli_runner()
    result = runner.invoke(app, ["build", "--tier", "fixture", "--tag", "emar"])
    assert result.exit_code == 0, result.output

    settings = config.get_settings()
    lake = settings.lake_root("fixture")
    fixture_manifest = fixtures_write.load_manifest(fixtures_write.default_out_dir())

    bucket_dirs_by_table: dict[str, dict[int, Path]] = {}
    for table_name in ("emar", "emar_detail"):
        tdir = loader_paths.table_dir(lake, HOSP, table_name)
        # EP-18 layout: one sorted part-0.parquet per partition; no raw_*/_sorting.tmp/.new
        bucket_dirs = {
            int(p.name.split("=", 1)[1]): p
            for p in tdir.iterdir()
            if p.is_dir() and p.name.startswith("subject_bucket=")
        }
        assert bucket_dirs, f"{table_name}: no partition directories staged"
        for bdir in bucket_dirs.values():
            assert [p.name for p in sorted(bdir.iterdir())] == ["part-0.parquet"], bdir
        assert not paths.new_dir_for(tdir).exists() and not paths.old_dir_for(tdir).exists()
        progress = buckets_mod.read_progress(tdir)
        assert progress is not None and progress.pass1_done and progress.complete
        bucket_dirs_by_table[table_name] = bucket_dirs

    con = engine.open_build_connection(settings, tier="fixture", memory_limit="4GB")
    try:
        for table_name, sort_keys in (("emar", SORT_EMAR), ("emar_detail", SORT_DET_PREFIX)):
            for bdir in bucket_dirs_by_table[table_name].values():
                part = (bdir / "part-0.parquet").as_posix().replace("'", "''")
                tup = f"row({', '.join(sort_keys)})"
                (ok,) = con.execute(
                    f"SELECT bool_and(cur >= prev) FROM ("
                    f"SELECT {tup} AS cur, lag({tup}, 1, {tup}) OVER (ORDER BY file_row_number) "
                    f"AS prev FROM read_parquet('{part}', file_row_number=true, "
                    f"hive_partitioning=false))"
                ).fetchone()  # type: ignore[misc]
                assert ok is True, f"{table_name}/{bdir.name} is not sorted by {sort_keys}"

        # brief item 4: every emar_detail row joins an emar row on (emar_id, emar_seq) —
        # a count of unmatched rows, computed in-process on fixture data only
        emar_glob = (
            (loader_paths.table_dir(lake, HOSP, "emar") / "subject_bucket=*" / "part-0.parquet")
            .as_posix()
            .replace("'", "''")
        )
        det_glob = (
            (
                loader_paths.table_dir(lake, HOSP, "emar_detail")
                / "subject_bucket=*"
                / "part-0.parquet"
            )
            .as_posix()
            .replace("'", "''")
        )
        (unmatched,) = con.execute(
            f"SELECT count(*) FROM read_parquet('{det_glob}', hive_partitioning=false) d "
            f"WHERE NOT EXISTS (SELECT 1 FROM read_parquet('{emar_glob}', "
            f"hive_partitioning=false) e "
            f"WHERE e.emar_id = d.emar_id AND e.emar_seq = d.emar_seq)"
        ).fetchone()  # type: ignore[misc]
        assert unmatched == 0, f"{unmatched} emar_detail rows have no emar parent on fixture"
    finally:
        con.close()

    # status.json complete + rows equal the committed fixture manifest's, per table
    status_steps = manifest_mod.read_status(lake)["steps"]
    for qn, table_name in ((QN_EMAR, "emar"), (QN_DET, "emar_detail")):
        table = contract.table(qn)
        entry = status_steps[qn]
        fixture_rows = fixture_manifest["files"][f"mimic-iv-3.1/{table.csv_path}"]["rows"]
        assert entry["rows"] == fixture_rows and entry["rejects"] == 0, qn
        assert entry["tier_complete"] == "full" and entry["dev_ready"] is True
        assert entry["files"] == len(bucket_dirs_by_table[table_name])

        # one manifest line per partition file (the large path appends per finished bucket)
        lines: dict[str, dict[str, Any]] = {}
        for mpath in manifest_mod.manifests_dir(lake).glob("*.jsonl"):
            for line in mpath.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    rec = json.loads(line)
                    if f"{rec['schema']}.{rec['table']}" == qn:
                        lines[rec["path"]] = rec
        expected_paths = {
            f"core/{HOSP}/{table_name}/subject_bucket={n}/part-0.parquet"
            for n in bucket_dirs_by_table[table_name]
        }
        assert set(lines) == expected_paths, qn
        assert sum(rec["rows"] for rec in lines.values()) == fixture_rows, qn

    # benchmark ledger: pass1 + pass2 + total per step, one build summary line
    ledger = benchmarks_mod.read(settings)
    for step_name in (STEP_EMAR, STEP_DET):
        step_lines = ledger.filter(ledger["step"] == step_name)
        assert set(step_lines["phase"].to_list()) == {"pass1", "pass2", "total"}, step_name
        assert all(step_lines["ok"].to_list()), step_name
    assert ledger.filter(ledger["kind"] == "build").height == 1


# ---------------------------------------------------------------------------
# 5. Dev tier (brief item 5): green once the ⏱ job marks both tables dev_ready
# ---------------------------------------------------------------------------


@pytest.mark.tier("dev", needs="lake")
def test_dev_counts_and_sortedness(dev_ready, contract: Contract, record_property) -> None:
    import duckdb

    from mimicwarehouse.catalog.connect import open_catalog

    # status.json keys steps by <schema>.<table> (loader/manifest.py), so the readiness
    # fixture takes the qualified table name, not the spec's step name
    for qn in (QN_EMAR, QN_DET):
        entry = dev_ready(qn)
        assert entry["dev_ready"] is True

    settings = config.load_settings()
    lake = settings.lake_root("dev")

    # manifest rows per lake-relative path; last line wins per path (rebuilds append)
    manifest_rows: dict[str, int] = {}
    for qn in (QN_EMAR, QN_DET):
        rows_by_path: dict[str, int] = {}
        for mpath in manifest_mod.manifests_dir(lake).glob("*.jsonl"):
            for line in mpath.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                rec = json.loads(line)
                if f"{rec['schema']}.{rec['table']}" == qn:
                    rows_by_path[rec["path"]] = rec["rows"]
        manifest_rows[qn] = sum(
            rows
            for path, rows in rows_by_path.items()
            if any(f"subject_bucket={b}/" in path for b in settings.dev_buckets)
        )
        assert manifest_rows[qn] > 0, f"no manifest rows recorded for {qn} dev buckets"

    con = open_catalog("dev")
    try:
        for qn in (QN_EMAR, QN_DET):
            try:
                (total,) = con.execute(f"SELECT count(*) FROM {qn}").fetchone()  # type: ignore[misc]
            except duckdb.CatalogException:
                pytest.skip(
                    "dev catalog predates the emar steps — refresh it with "
                    "`mwh build --tier dev --select catalog`"
                )
            assert total > 0
            assert total == manifest_rows[qn], (
                f"dev view count {total} != manifest rows {manifest_rows[qn]} for {qn}, "
                f"buckets {list(settings.dev_buckets)}"
            )
        # unmatched emar_detail rows on dev: counted and RECORDED, not failed — the brief
        # expects 0 or a small documented number (EP-28/EP-44 own referential integrity)
        (unmatched,) = con.execute(
            f"SELECT count(*) FROM {QN_DET} d WHERE NOT EXISTS "
            f"(SELECT 1 FROM {QN_EMAR} e "
            f"WHERE e.emar_id = d.emar_id AND e.emar_seq = d.emar_seq)"
        ).fetchone()  # type: ignore[misc]
        record_property("emar_detail_unmatched_dev", unmatched)
        print(f"dev-tier emar_detail rows without an emar parent (a count): {unmatched}")
    finally:
        con.close()

    # per-file row-group subject_id min/max are non-decreasing (sorted; EP-18 pass 2).
    # Values are compared, never printed — a failure names the file only (GOVERNANCE §4).
    meta_con = duckdb.connect()
    try:
        for b in settings.dev_buckets:
            part = loader_paths.table_dir(lake, HOSP, "emar") / f"subject_bucket={b}"
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
