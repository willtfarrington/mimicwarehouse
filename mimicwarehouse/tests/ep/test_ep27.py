"""EP-27 — stage the icu event tables (inputevents, ingredientevents, datetimeevents)
in one sequential background job, completing `mimiciv_icu`.

Fixture tier (default): the contract facts the brief pins (item 1 — a *verify* since
EP-169/EP-170 [FC-3]: all three ``load_class: large``, partitioned, tie-broken sort keys
``[subject_id, starttime, orderid]`` for inputevents/ingredientevents and
``[subject_id, charttime, itemid]`` for datetimeevents; the watch columns
``datetimeevents.value`` TIMESTAMP, ``inputevents.amount/rate/originalamount/
originalrate``/``patientweight`` DOUBLE, ``statusdescription``/``ordercategoryname``
VARCHAR labels; ``ingredientevents`` has no ``validate.sql`` expectation [FC-12]), the
EP-17 governance flags (item 2 — verify only [FC-5]: ``orderid``/``linkorderid``/
``caregiver_id`` stamped identifiers from keys.yaml; no free-text flags — the status/
category varchars are labels, not notes), the spec shape of the three new steps
(independent tables — the EP-19 runner is sequential on one connection, so single-writer
needs no ``depends_on`` chain; spec order stages ``inputevents`` before
``ingredientevents``, whose ``orderid`` links back to the inputevents order), the item-4
coverage assertion (the icu tag groups ``small`` / ``chartevents`` / ``icu-events``
partition the 9 icu contract tables exactly, and the whole DAG stages all 31 hosp + icu
tables exactly once), and one real fixture build via the runner (item 5: EP-18 layout,
per-partition sortedness, manifest lines, ``status.json``, ledger phases, and the two
join closures — every non-NULL ``inputevents.stay_id`` in ``icustays`` and every
``ingredientevents.orderid`` in ``inputevents``, as unmatched **counts**, in-process).

``tier("dev", needs="lake")``-marked (item 6): runs once the real lake's ``status.json``
marks all three tables ``dev_ready`` (the ⏱ background job sorts buckets 0-4 first), else
skips with a reason [VT-1]; skips too while the dev catalog predates the steps. The
dev-tier unmatched-key counts for the two joins are recorded, never failed on (EP-28/
EP-44 own referential integrity); the ``datetimeevents.value`` NULL count is reported.
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

pytestmark = pytest.mark.ep_27

HOSP = "mimiciv_hosp"
ICU = "mimiciv_icu"
TABLES = ("inputevents", "ingredientevents", "datetimeevents")
QNS = tuple(f"{ICU}.{t}" for t in TABLES)
STEPS = tuple(f"stage.{qn}" for qn in QNS)
STEP_ICUSTAYS = f"stage.{ICU}.icustays"

#: The tie-broken contract sort keys the brief pins (EP-169 adopted them; item 1 verify).
#: Every member is NOT NULL in the contract, so the sortedness checks use the full keys
#: (no nullable-prefix trimming needed, unlike EP-25's pharmacy/microbiologyevents).
SORT_KEYS: dict[str, tuple[str, ...]] = {
    "inputevents": ("subject_id", "starttime", "orderid"),
    "ingredientevents": ("subject_id", "starttime", "orderid"),
    "datetimeevents": ("subject_id", "charttime", "itemid"),
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
        table = contract.table(f"{ICU}.{table_name}")
        assert table.load_class == "large", table_name
        assert table.partitioned and table.subject_keyed, table_name
        assert table.sort_keys == SORT_KEYS[table_name], table_name
    # the contract types must load with zero tolerated rejects (item 2)
    assert config.get_settings().loader_reject_max == 0


def test_contract_columns_inputevents_ingredientevents(contract: Contract) -> None:
    inputevents = contract.table(f"{ICU}.inputevents")
    assert inputevents.time_column == "starttime"
    # upstream declares (orderid, itemid) — EP-28/EP-44 test it, we record it
    assert inputevents.primary_key == ("orderid", "itemid")
    by_name = {c.name: c for c in inputevents.columns}
    # the brief's watch columns: the amount/rate quartet + patientweight are DOUBLE,
    # the status/category columns are short VARCHAR labels
    for name in ("amount", "rate", "originalamount", "originalrate", "patientweight"):
        assert by_name[name].duckdb_type == "DOUBLE", name
    assert by_name["statusdescription"].duckdb_type == "VARCHAR"
    assert by_name["ordercategoryname"].duckdb_type == "VARCHAR"
    assert not by_name["orderid"].nullable
    assert by_name["linkorderid"].nullable, "linkorderid links rate changes — sparse"
    assert by_name["stay_id"].nullable, "inputs outside a matched ICU stay have no stay_id"

    ingredient = contract.table(f"{ICU}.ingredientevents")
    assert ingredient.time_column == "starttime"
    # no upstream primary key; the uniqueness hint is the documented candidate
    assert ingredient.primary_key is None
    assert ingredient.uniqueness_hint == ("orderid", "itemid")
    # upstream validate.sql has no ingredientevents row (EP-20/EP-28 amendments [FC-12])
    assert ingredient.expected_rows_source is None
    by_name = {c.name: c for c in ingredient.columns}
    for name in ("amount", "rate", "originalamount", "originalrate"):
        assert by_name[name].duckdb_type == "DOUBLE", name
    assert by_name["statusdescription"].duckdb_type == "VARCHAR"
    assert not by_name["orderid"].nullable, "orderid links back to the inputevents order"
    assert by_name["stay_id"].nullable


def test_contract_columns_datetimeevents(contract: Contract) -> None:
    table = contract.table(f"{ICU}.datetimeevents")
    assert table.time_column == "charttime"
    assert table.primary_key == ("stay_id", "itemid", "charttime")
    by_name = {c.name: c for c in table.columns}
    # the brief's watch column: the charted observation is itself a shifted TIMESTAMP
    assert by_name["value"].duckdb_type == "TIMESTAMP"
    assert not by_name["value"].nullable
    assert by_name["valueuom"].duckdb_type == "VARCHAR"
    assert by_name["warning"].duckdb_type == "SMALLINT"
    assert not by_name["hadm_id"].nullable and not by_name["stay_id"].nullable
    assert by_name["caregiver_id"].nullable, "caregiver_id arrived in v2.2 — nullable"


# ---------------------------------------------------------------------------
# 2. Governance flags (brief item 2 — verify; stamped from keys.yaml, EP-17/FC-5)
# ---------------------------------------------------------------------------


def test_contract_flags(contract: Contract) -> None:
    inputevents = contract.table(f"{ICU}.inputevents")
    ingredient = contract.table(f"{ICU}.ingredientevents")
    datetimeevents = contract.table(f"{ICU}.datetimeevents")
    # the identifier names the brief lists are all in keys.yaml's identifiers list
    for table in (inputevents, ingredient):
        for name in ("orderid", "linkorderid", "caregiver_id"):
            assert table.column(name).identifier, f"{table.name}.{name}"
    assert datetimeevents.column("caregiver_id").identifier
    # the GOVERNANCE real-band ids are identifiers too; itemid is a dimension code, not one
    for table in (inputevents, ingredient, datetimeevents):
        for name in ("subject_id", "hadm_id", "stay_id"):
            assert table.column(name).identifier, f"{table.name}.{name}"
        assert not table.column("itemid").identifier, table.name
        # status/category/unit varchars are labels, not notes — no free-text flags
        assert table.free_text_columns() == (), table.name


# ---------------------------------------------------------------------------
# 3. Spec shape: three steps, one tag, sequential runner order; no overrides
# ---------------------------------------------------------------------------


def test_spec_step_shape() -> None:
    dag = load_dag()
    for step_name, qn, table_name in zip(STEPS, QNS, TABLES, strict=True):
        step = dag.step(step_name)
        assert step.kind == "stage" and step.qualified_table == qn
        assert step.source == f"mimic-iv-3.1/icu/{table_name}.csv"
        assert {"stage", ICU, "large", "icu-events"} <= set(step.tags)
        assert "dims" not in step.tags
        assert step.tiers == ("fixture", "demo", "dev", "full")
        # independent tables: the EP-19 runner is sequential on one connection (single
        # writer), so no depends_on chain — spec order alone sequences the job
        assert step.depends_on == ()
        # size_class / partitioned / sort_by come from the contract, never the spec
        assert step.size_class is None and step.partitioned is None and step.sort_by is None
    assert set(STEPS) <= set(dag.step("catalog").depends_on)
    # --tag icu-events selects exactly the three steps, inputevents first (its orders
    # are the parents of the ingredientevents rows, staged next)
    names = [s.name for s in dag.ordered(tags=["icu-events"], tier="full")]
    assert names == list(STEPS)


# ---------------------------------------------------------------------------
# 4. Coverage (brief item 4): the icu tag groups partition the 9 icu tables exactly,
#    and the whole DAG stages all 31 hosp + icu contract tables exactly once
# ---------------------------------------------------------------------------


def test_icu_and_whole_dag_coverage(contract: Contract) -> None:
    dag = load_dag()
    icu_steps = [
        s
        for s in dag.steps
        if s.kind == "stage" and (s.qualified_table or "").startswith(f"{ICU}.")
    ]
    small = {s.qualified_table for s in icu_steps if "small" in s.tags}
    chartevents = {s.qualified_table for s in icu_steps if "chartevents" in s.tags}
    icu_events = {s.qualified_table for s in icu_steps if "icu-events" in s.tags}
    groups = [small, chartevents, icu_events]
    union = set().union(*groups)
    # exactly once: the groups are pairwise disjoint and their union is the contract
    assert sum(len(g) for g in groups) == len(union)
    icu_tables = {t.qualified_name for t in contract.tables if t.schema_name == ICU}
    assert len(icu_tables) == 9
    assert union == icu_tables, "every icu table is staged by exactly one tag group"
    assert icu_events == set(QNS)

    # the whole DAG: one stage step per hosp/icu contract table, no table twice,
    # nothing beyond the contract (mimiciv_ed waits for EP-142, mimiciv_note for EP-148)
    staged = [s.qualified_table for s in dag.steps if s.kind == "stage"]
    assert len(staged) == len(set(staged)), "a table is staged by more than one step"
    hosp_icu = {t.qualified_name for t in contract.tables if t.schema_name in (HOSP, ICU)}
    assert len(hosp_icu) == 31
    assert set(staged) == hosp_icu, "the DAG stages all 31 hosp + icu tables exactly"


# ---------------------------------------------------------------------------
# 5. Fixture build through the runner: layout, sortedness, manifests, status,
#    ledger, and the two join closures (inputevents->icustays,
#    ingredientevents->inputevents)
# ---------------------------------------------------------------------------


def test_fixture_build_large_path(data_root: Path, contract: Contract) -> None:
    runner = helpers.cli_runner()
    result = runner.invoke(app, ["build", "--tier", "fixture", "--tag", "icu-events"])
    assert result.exit_code == 0, result.output
    # icustays (an EP-20 small step) anchors the stay_id closure — stage it too
    result = runner.invoke(app, ["build", "--tier", "fixture", "--select", STEP_ICUSTAYS])
    assert result.exit_code == 0, result.output

    settings = config.get_settings()
    lake = settings.lake_root("fixture")
    fixture_manifest = fixtures_write.load_manifest(fixtures_write.default_out_dir())

    bucket_dirs_by_table: dict[str, dict[int, Path]] = {}
    for table_name in TABLES:
        tdir = loader_paths.table_dir(lake, ICU, table_name)
        # EP-18 layout: one sorted part-0.parquet per partition; no raw_*/_sorting.tmp/.new
        bucket_dirs = {
            int(p.name.split("=", 1)[1]): p
            for p in tdir.iterdir()
            if p.is_dir() and p.name.startswith("subject_bucket=")
        }
        assert bucket_dirs, f"{table_name}: no partition directories staged"
        for bdir in bucket_dirs.values():
            assert [p.name for p in sorted(bdir.iterdir())] == ["part-0.parquet"], bdir
        assert not publish.new_path_for(tdir).exists() and not publish.old_path_for(tdir).exists()
        progress = buckets_mod.read_progress(tdir)
        assert progress is not None and progress.pass1_done and progress.complete
        bucket_dirs_by_table[table_name] = bucket_dirs

    def glob_for(schema: str, table_name: str) -> str:
        tdir = loader_paths.table_dir(lake, schema, table_name)
        return (tdir / "subject_bucket=*" / "part-0.parquet").as_posix().replace("'", "''")

    con = engine.open_build_connection(settings, tier="fixture", memory_limit="4GB")
    try:
        for table_name in TABLES:
            sort_keys = SORT_KEYS[table_name]
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

        # brief item 5: the two join closures, as unmatched counts on fixture data only.
        # inputevents.stay_id is nullable in the contract, so the closure quantifies over
        # non-NULL stay_id values (the fixture generator always fills it in practice)
        (unmatched,) = con.execute(
            f"SELECT count(*) FROM read_parquet('{glob_for(ICU, 'inputevents')}', "
            f"hive_partitioning=false) i WHERE i.stay_id IS NOT NULL AND NOT EXISTS "
            f"(SELECT 1 FROM read_parquet('{glob_for(ICU, 'icustays')}', "
            f"hive_partitioning=false) s WHERE s.stay_id = i.stay_id)"
        ).fetchone()  # type: ignore[misc]
        assert unmatched == 0, f"{unmatched} inputevents rows have no icustays row on fixture"
        (unmatched,) = con.execute(
            f"SELECT count(*) FROM read_parquet('{glob_for(ICU, 'ingredientevents')}', "
            f"hive_partitioning=false) g WHERE NOT EXISTS "
            f"(SELECT 1 FROM read_parquet('{glob_for(ICU, 'inputevents')}', "
            f"hive_partitioning=false) i WHERE i.orderid = g.orderid)"
        ).fetchone()  # type: ignore[misc]
        assert unmatched == 0, (
            f"{unmatched} ingredientevents rows have no inputevents order on fixture"
        )
    finally:
        con.close()

    # status.json complete + rows equal the committed fixture manifest's, per table
    status_steps = manifest_mod.read_status(lake)["steps"]
    for table_name in TABLES:
        qn = f"{ICU}.{table_name}"
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
            f"core/{ICU}/{table_name}/subject_bucket={n}/part-0.parquet"
            for n in bucket_dirs_by_table[table_name]
        }
        assert set(lines) == expected_paths, qn
        assert sum(rec["rows"] for rec in lines.values()) == fixture_rows, qn

    # benchmark ledger: pass1 + pass2 + total per large step; two build summary lines
    # (the icu-events build, then the icustays closure build)
    ledger = benchmarks_mod.read(settings)
    for step_name in STEPS:
        step_lines = ledger.filter(ledger["step"] == step_name)
        assert set(step_lines["phase"].to_list()) == {"pass1", "pass2", "total"}, step_name
        assert all(step_lines["ok"].to_list()), step_name
    assert ledger.filter(ledger["kind"] == "build").height == 2


# ---------------------------------------------------------------------------
# 6. Dev tier (brief item 6): green once the ⏱ job marks all three tables dev_ready
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
                    "dev catalog predates the icu-events steps — refresh it with "
                    "`mwh build --tier dev --select catalog`"
                )
            assert total > 0
            assert total == manifest_rows[qn], (
                f"dev view count {total} != manifest rows {manifest_rows[qn]} for {qn}, "
                f"buckets {list(settings.dev_buckets)}"
            )
        # unmatched-key counts for the two joins: counted and RECORDED, not failed —
        # the brief expects counts only (EP-28/EP-44 own referential integrity)
        (unmatched_stays,) = con.execute(
            f"SELECT count(*) FROM {ICU}.inputevents i WHERE i.stay_id IS NOT NULL "
            f"AND NOT EXISTS (SELECT 1 FROM {ICU}.icustays s WHERE s.stay_id = i.stay_id)"
        ).fetchone()  # type: ignore[misc]
        record_property("inputevents_unmatched_icustays_dev", unmatched_stays)
        print(f"dev-tier inputevents rows without an icustays row (a count): {unmatched_stays}")
        (unmatched_orders,) = con.execute(
            f"SELECT count(*) FROM {ICU}.ingredientevents g WHERE NOT EXISTS "
            f"(SELECT 1 FROM {ICU}.inputevents i WHERE i.orderid = g.orderid)"
        ).fetchone()  # type: ignore[misc]
        record_property("ingredientevents_unmatched_inputevents_dev", unmatched_orders)
        print(
            f"dev-tier ingredientevents rows without an inputevents order (a count): "
            f"{unmatched_orders}"
        )
        # the brief asks for the NULL-value count (a count only); the contract says
        # NOT NULL and the loader tolerates zero rejects, so it must be exactly 0
        (null_values,) = con.execute(
            f"SELECT count(*) FROM {ICU}.datetimeevents WHERE value IS NULL"
        ).fetchone()  # type: ignore[misc]
        record_property("datetimeevents_null_value_dev", null_values)
        print(f"dev-tier datetimeevents rows with NULL value (a count): {null_values}")
        assert null_values == 0, f"{null_values} datetimeevents rows with NULL value"
    finally:
        con.close()

    # per-file row-group subject_id min/max are non-decreasing (sorted; EP-18 pass 2).
    # Values are compared, never printed — a failure names the file only (GOVERNANCE §4).
    meta_con = duckdb.connect()
    try:
        for table_name in TABLES:
            for b in settings.dev_buckets:
                part = loader_paths.table_dir(lake, ICU, table_name) / f"subject_bucket={b}"
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
