"""EP-25 — stage the remaining hosp tables (pharmacy, prescriptions, poe,
microbiologyevents) in one sequential background job.

Fixture tier (default): the contract facts the brief pins (item 1 — a *verify* since
EP-169/EP-170 [FC-3]: all four ``load_class: large``, ``microbiologyevents``
deliberately so by owner decision at EP-169 despite its ~0.9 GB CSV; tie-broken sort
keys; the watch columns ``prescriptions.dose_val_rx``/``form_val_disp`` VARCHAR,
``poe.discontinue_of_poe_id`` nullable, ``microbiologyevents.dilution_value`` DOUBLE),
the EP-17 governance flags (item 2 — verify only [FC-5]: ``pharmacy_id``/``poe_id``/
``order_provider_id``/``microevent_id``/``micro_specimen_id`` stamped identifiers from
keys.yaml; ``microbiologyevents.comments`` is ``free_text`` while the medication
tables' ``drug``/``medication``/``prod_strength`` are labels, not notes), the spec
shape of the four new steps (independent tables — the EP-19 runner is sequential on
one connection, so single-writer needs no ``depends_on`` chain; spec order stages
``pharmacy`` before ``prescriptions``), the item-4 coverage assertion (the tag groups
``small`` / ``emar`` / ``hosp-rest`` plus the EP-23 ``labevents`` step partition the 22
hosp contract tables exactly), and one real fixture build via the runner (item 5:
EP-18 layout, per-partition sortedness, manifest lines, ``status.json``, ledger phases,
and the two join closures — every ``prescriptions.pharmacy_id`` in ``pharmacy`` and
every ``poe_detail.poe_id`` in ``poe``, as unmatched **counts**, in-process).

``tier("dev", needs="lake")``-marked (item 6): runs once the real lake's
``status.json`` marks all four tables ``dev_ready`` (the ⏱ background job sorts
buckets 0-4 first), else skips with a reason [VT-1]; skips too while the dev catalog
predates the steps. The dev-tier unmatched-key counts for the two joins are recorded,
never failed on (EP-28/EP-44 own referential integrity). Everything printed or
asserted is counts, paths and booleans — never a row, never an identifier value (a
sortedness failure reports the file, not the ids).
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

pytestmark = pytest.mark.ep_25

HOSP = "mimiciv_hosp"
TABLES = ("pharmacy", "prescriptions", "poe", "microbiologyevents")
QNS = tuple(f"{HOSP}.{t}" for t in TABLES)
STEPS = tuple(f"stage.{qn}" for qn in QNS)
STEP_POE_DETAIL = f"stage.{HOSP}.poe_detail"

#: The tie-broken contract sort keys the brief pins (EP-169 adopted them; item 1 verify).
SORT_KEYS: dict[str, tuple[str, ...]] = {
    "pharmacy": ("subject_id", "starttime", "pharmacy_id"),
    "prescriptions": ("subject_id", "starttime", "pharmacy_id"),
    "poe": ("subject_id", "ordertime", "poe_seq"),
    "microbiologyevents": ("subject_id", "chartdate", "charttime", "microevent_id"),
}
#: Non-null prefixes used for sortedness checks (EP-24 precedent): the loader sorts
#: NULLS LAST and DuckDB tuple comparisons over a NULL member go NULL, so the check
#: stops before the first nullable sort key. pharmacy/prescriptions ``starttime`` and
#: microbiologyevents ``charttime`` are nullable; poe's full key is NOT NULL.
SORT_PREFIX: dict[str, tuple[str, ...]] = {
    "pharmacy": ("subject_id",),
    "prescriptions": ("subject_id",),
    "poe": ("subject_id", "ordertime", "poe_seq"),
    "microbiologyevents": ("subject_id", "chartdate"),
}


@pytest.fixture
def data_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    root = helpers.tmp_data_root(monkeypatch, tmp_path)
    yield root
    config.configure()


# ---------------------------------------------------------------------------
# 1. Contract facts (brief item 1/2 — verify; fix in the EP-9 YAML with a dated note)
# ---------------------------------------------------------------------------


def test_contract_matches_brief(contract: Contract) -> None:
    for table_name in TABLES:
        table = contract.table(f"{HOSP}.{table_name}")
        # all four are large — microbiologyevents deliberately so (owner decision at
        # EP-169, D-17 addendum; supersedes the brief's "small" [FC-3])
        assert table.load_class == "large", table_name
        assert table.partitioned and table.subject_keyed, table_name
        assert table.sort_keys == SORT_KEYS[table_name], table_name


def test_contract_columns_pharmacy_prescriptions(contract: Contract) -> None:
    pharmacy = contract.table(f"{HOSP}.pharmacy")
    assert pharmacy.time_column == "starttime"
    assert pharmacy.primary_key == ("pharmacy_id",)
    by_name = {c.name: c for c in pharmacy.columns}
    assert by_name["pharmacy_id"].duckdb_type == "INTEGER"
    assert not by_name["pharmacy_id"].nullable
    assert by_name["starttime"].duckdb_type == "TIMESTAMP"
    assert by_name["starttime"].nullable, "pharmacy orders may lack a start time"

    prescriptions = contract.table(f"{HOSP}.prescriptions")
    assert prescriptions.time_column == "starttime"
    # upstream declares (pharmacy_id, drug_type, drug) — EP-28/EP-44 test it, we record it
    assert prescriptions.primary_key == ("pharmacy_id", "drug_type", "drug")
    by_name = {c.name: c for c in prescriptions.columns}
    assert by_name["pharmacy_id"].duckdb_type == "INTEGER", "shared key with pharmacy"
    # the brief's watch columns: dose/dispense values are text (may be ranges), never numeric
    assert by_name["dose_val_rx"].duckdb_type == "VARCHAR"
    assert by_name["form_val_disp"].duckdb_type == "VARCHAR"
    assert by_name["drug"].nullable, "zero-length drug strings load as NULL (contract note)"


def test_contract_columns_poe_microbiology(contract: Contract) -> None:
    poe = contract.table(f"{HOSP}.poe")
    assert poe.time_column == "ordertime"
    assert poe.primary_key == ("poe_id",)
    by_name = {c.name: c for c in poe.columns}
    assert by_name["poe_id"].duckdb_type == "VARCHAR", "poe_id embeds the subject id"
    assert not by_name["poe_id"].nullable
    assert not by_name["ordertime"].nullable
    # the brief's watch column: the discontinuation chain is sparse
    assert by_name["discontinue_of_poe_id"].duckdb_type == "VARCHAR"
    assert by_name["discontinue_of_poe_id"].nullable
    assert by_name["hadm_id"].nullable, "orders outside an admission have no hadm_id"

    micro = contract.table(f"{HOSP}.microbiologyevents")
    assert micro.time_column == "charttime"
    assert micro.primary_key == ("microevent_id",)
    by_name = {c.name: c for c in micro.columns}
    assert not by_name["chartdate"].nullable, "chartdate is always present"
    assert by_name["charttime"].nullable, "charttime only when a time is known"
    # the brief's watch column: dilution_value is the one numeric in the dilution trio
    assert by_name["dilution_value"].duckdb_type == "DOUBLE"
    assert by_name["micro_specimen_id"].duckdb_type == "INTEGER"
    assert not by_name["micro_specimen_id"].nullable


# ---------------------------------------------------------------------------
# 2. Governance flags (brief item 2 — verify; stamped from keys.yaml, EP-17/FC-5)
# ---------------------------------------------------------------------------


def test_contract_flags(contract: Contract) -> None:
    pharmacy = contract.table(f"{HOSP}.pharmacy")
    prescriptions = contract.table(f"{HOSP}.prescriptions")
    poe = contract.table(f"{HOSP}.poe")
    micro = contract.table(f"{HOSP}.microbiologyevents")
    # the identifier names the brief lists are all in keys.yaml's identifiers list
    for table, name in (
        (pharmacy, "pharmacy_id"),
        (pharmacy, "poe_id"),
        (prescriptions, "pharmacy_id"),
        (prescriptions, "poe_id"),
        (prescriptions, "order_provider_id"),
        (poe, "poe_id"),
        (poe, "discontinue_of_poe_id"),
        (poe, "discontinued_by_poe_id"),
        (poe, "order_provider_id"),
        (micro, "microevent_id"),
        (micro, "micro_specimen_id"),
        (micro, "order_provider_id"),
    ):
        assert table.column(name).identifier, f"{table.name}.{name}"
    # microbiologyevents.comments is free text (flagged for safe_query, EP-30);
    # itemid & friends are dimension codes, not identifiers (keys.yaml note)
    assert micro.free_text_columns() == ("comments",)
    assert not micro.column("spec_itemid").identifier
    # the medication tables' drug/medication/prod_strength columns are labels, not notes
    assert pharmacy.free_text_columns() == ()
    assert prescriptions.free_text_columns() == ()
    assert poe.free_text_columns() == ()


# ---------------------------------------------------------------------------
# 3. Spec shape: four steps, one tag, sequential runner order; no overrides
# ---------------------------------------------------------------------------


def test_spec_step_shape() -> None:
    dag = load_dag()
    for step_name, qn, table_name in zip(STEPS, QNS, TABLES, strict=True):
        step = dag.step(step_name)
        assert step.kind == "stage" and step.qualified_table == qn
        assert step.source == f"mimic-iv-3.1/hosp/{table_name}.csv"
        assert {"stage", HOSP, "large", "hosp-rest"} <= set(step.tags)
        assert "dims" not in step.tags
        assert step.tiers == ("fixture", "demo", "dev", "full")
        # independent tables: the EP-19 runner is sequential on one connection (single
        # writer), so no depends_on chain — spec order alone sequences the job
        assert step.depends_on == ()
        # size_class / partitioned / sort_by come from the contract, never the spec
        assert step.size_class is None and step.partitioned is None and step.sort_by is None
    assert set(STEPS) <= set(dag.step("catalog").depends_on)
    # --tag hosp-rest selects exactly the four steps, pharmacy first (it shares
    # pharmacy_id with prescriptions, staged next)
    names = [s.name for s in dag.ordered(tags=["hosp-rest"], tier="full")]
    assert names == list(STEPS)


# ---------------------------------------------------------------------------
# 4. Coverage (brief item 4): the tag groups partition the 22 hosp tables exactly
# ---------------------------------------------------------------------------


def test_hosp_coverage_by_tag(contract: Contract) -> None:
    dag = load_dag()
    hosp_steps = [
        s
        for s in dag.steps
        if s.kind == "stage" and (s.qualified_table or "").startswith(f"{HOSP}.")
    ]
    small = {s.qualified_table for s in hosp_steps if "small" in s.tags}
    emar = {s.qualified_table for s in hosp_steps if "emar" in s.tags}
    hosp_rest = {s.qualified_table for s in hosp_steps if "hosp-rest" in s.tags}
    labevents = {dag.step(f"stage.{HOSP}.labevents").qualified_table}
    groups = [small, emar, hosp_rest, labevents]
    union = set().union(*groups)
    # exactly once: the groups are pairwise disjoint and their union is the contract
    assert sum(len(g) for g in groups) == len(union)
    hosp_tables = {t.qualified_name for t in contract.tables if t.schema_name == HOSP}
    assert len(hosp_tables) == 22
    assert union == hosp_tables, "every hosp table is staged by exactly one tag group"
    assert hosp_rest == set(QNS)


# ---------------------------------------------------------------------------
# 5. Fixture build through the runner: layout, sortedness, manifests, status,
#    ledger, and the two join closures (prescriptions->pharmacy, poe_detail->poe)
# ---------------------------------------------------------------------------


def test_fixture_build_large_path(data_root: Path, contract: Contract) -> None:
    runner = helpers.cli_runner()
    result = runner.invoke(app, ["build", "--tier", "fixture", "--tag", "hosp-rest"])
    assert result.exit_code == 0, result.output
    # poe_detail (an EP-20 small step) joins poe on poe_id — stage it too for the closure
    result = runner.invoke(app, ["build", "--tier", "fixture", "--select", STEP_POE_DETAIL])
    assert result.exit_code == 0, result.output

    settings = config.get_settings()
    lake = settings.lake_root("fixture")
    fixture_manifest = fixtures_write.load_manifest(fixtures_write.default_out_dir())

    bucket_dirs_by_table: dict[str, dict[int, Path]] = {}
    for table_name in TABLES:
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

    def glob_for(table_name: str) -> str:
        tdir = loader_paths.table_dir(lake, HOSP, table_name)
        return (tdir / "subject_bucket=*" / "part-0.parquet").as_posix().replace("'", "''")

    con = engine.open_build_connection(settings, tier="fixture", memory_limit="4GB")
    try:
        for table_name in TABLES:
            sort_keys = SORT_PREFIX[table_name]
            tup = f"row({', '.join(sort_keys)})"
            for bdir in bucket_dirs_by_table[table_name].values():
                part = (bdir / "part-0.parquet").as_posix().replace("'", "''")
                (ok,) = con.execute(
                    f"SELECT bool_and(cur >= prev) FROM ("
                    f"SELECT {tup} AS cur, lag({tup}, 1, {tup}) OVER (ORDER BY file_row_number) "
                    f"AS prev FROM read_parquet('{part}', file_row_number=true, "
                    f"hive_partitioning=false))"
                ).fetchone()  # type: ignore[misc]
                assert ok is True, f"{table_name}/{bdir.name} is not sorted by {sort_keys}"

        # brief item 5: the two join closures, as unmatched counts on fixture data only
        (unmatched,) = con.execute(
            f"SELECT count(*) FROM read_parquet('{glob_for('prescriptions')}', "
            f"hive_partitioning=false) p WHERE NOT EXISTS "
            f"(SELECT 1 FROM read_parquet('{glob_for('pharmacy')}', hive_partitioning=false) f "
            f"WHERE f.pharmacy_id = p.pharmacy_id)"
        ).fetchone()  # type: ignore[misc]
        assert unmatched == 0, f"{unmatched} prescriptions rows have no pharmacy row on fixture"
        (unmatched,) = con.execute(
            f"SELECT count(*) FROM read_parquet('{glob_for('poe_detail')}', "
            f"hive_partitioning=false) d WHERE NOT EXISTS "
            f"(SELECT 1 FROM read_parquet('{glob_for('poe')}', hive_partitioning=false) o "
            f"WHERE o.poe_id = d.poe_id)"
        ).fetchone()  # type: ignore[misc]
        assert unmatched == 0, f"{unmatched} poe_detail rows have no poe parent on fixture"
    finally:
        con.close()

    # status.json complete + rows equal the committed fixture manifest's, per table
    status_steps = manifest_mod.read_status(lake)["steps"]
    for table_name in TABLES:
        qn = f"{HOSP}.{table_name}"
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

    # benchmark ledger: pass1 + pass2 + total per large step; two build summary lines
    # (the hosp-rest build, then the poe_detail closure build)
    ledger = benchmarks_mod.read(settings)
    for step_name in STEPS:
        step_lines = ledger.filter(ledger["step"] == step_name)
        assert set(step_lines["phase"].to_list()) == {"pass1", "pass2", "total"}, step_name
        assert all(step_lines["ok"].to_list()), step_name
    assert ledger.filter(ledger["kind"] == "build").height == 2


# ---------------------------------------------------------------------------
# 6. Dev tier (brief item 6): green once the ⏱ job marks all four tables dev_ready
# ---------------------------------------------------------------------------


@pytest.mark.tier("dev", needs="lake")
def test_dev_counts_and_join_reports(dev_ready, contract: Contract, record_property) -> None:
    import duckdb

    from mimicwarehouse.catalog.connect import open_catalog

    # status.json keys steps by <schema>.<table> (loader/manifest.py), so the readiness
    # fixture takes the qualified table name, not the spec's step name
    for qn in QNS:
        entry = dev_ready(qn)
        assert entry["dev_ready"] is True

    settings = config.load_settings()
    lake = settings.lake_root("dev")

    # manifest rows per lake-relative path; last line wins per path (rebuilds append)
    manifest_rows: dict[str, int] = {}
    for qn in QNS:
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
        for qn in QNS:
            try:
                (total,) = con.execute(f"SELECT count(*) FROM {qn}").fetchone()  # type: ignore[misc]
            except duckdb.CatalogException:
                pytest.skip(
                    "dev catalog predates the hosp-rest steps — refresh it with "
                    "`mwh build --tier dev --select catalog`"
                )
            assert total > 0
            assert total == manifest_rows[qn], (
                f"dev view count {total} != manifest rows {manifest_rows[qn]} for {qn}, "
                f"buckets {list(settings.dev_buckets)}"
            )
        # unmatched-key counts for the two joins: counted and RECORDED, not failed —
        # the brief expects counts only (EP-28/EP-44 own referential integrity)
        (unmatched_rx,) = con.execute(
            f"SELECT count(*) FROM {HOSP}.prescriptions p WHERE NOT EXISTS "
            f"(SELECT 1 FROM {HOSP}.pharmacy f WHERE f.pharmacy_id = p.pharmacy_id)"
        ).fetchone()  # type: ignore[misc]
        record_property("prescriptions_unmatched_pharmacy_dev", unmatched_rx)
        print(f"dev-tier prescriptions rows without a pharmacy row (a count): {unmatched_rx}")
        (unmatched_poe,) = con.execute(
            f"SELECT count(*) FROM {HOSP}.poe_detail d WHERE NOT EXISTS "
            f"(SELECT 1 FROM {HOSP}.poe o WHERE o.poe_id = d.poe_id)"
        ).fetchone()  # type: ignore[misc]
        record_property("poe_detail_unmatched_poe_dev", unmatched_poe)
        print(f"dev-tier poe_detail rows without a poe parent (a count): {unmatched_poe}")
    finally:
        con.close()

    # per-file row-group subject_id min/max are non-decreasing (sorted; EP-18 pass 2).
    # Values are compared, never printed — a failure names the file only (GOVERNANCE §4).
    meta_con = duckdb.connect()
    try:
        for table_name in TABLES:
            for b in settings.dev_buckets:
                part = loader_paths.table_dir(lake, HOSP, table_name) / f"subject_bucket={b}"
                part = part / "part-0.parquet"
                assert part.is_file(), f"{table_name}: dev bucket {b} has no part-0.parquet"
                escaped = part.as_posix().replace("'", "''")
                groups = meta_con.execute(
                    f"SELECT stats_min_value, stats_max_value FROM parquet_metadata('{escaped}') "
                    "WHERE path_in_schema = 'subject_id' ORDER BY row_group_id"
                ).fetchall()
                assert groups, f"{table_name}/{part.name}: no row-group statistics"
                bounds = [(int(lo), int(hi)) for lo, hi in groups]
                ok = all(lo <= hi for lo, hi in bounds) and all(
                    bounds[i][1] <= bounds[i + 1][0] for i in range(len(bounds) - 1)
                )
                assert ok, (
                    f"{table_name}: row-group subject_id ranges are not non-decreasing "
                    f"in bucket {b}"
                )
    finally:
        meta_con.close()
