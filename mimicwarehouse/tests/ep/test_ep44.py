"""EP-44 — data-quality profiling (``mimicwarehouse.qc``).

Fixture tier (default): the DAG spec is wired and drift-free (31 ``qc.profile.*`` steps,
``qc.checks``, ``qc.report``, the shared ``catalog`` step); the packaged thresholds
validate against the contract and crafted documents are refused per violation;
``Threshold.evaluate`` arithmetic; the session fixture lake carries the four ``meta.qc_*``
tables (identifiers without extrema, quantiles on measurement columns, top-k values only
for dictionary-coded columns, no released ``n_affected`` below k, no ``fail`` on the clean
fixture, the ``kind: qc`` run with its ``kind: query`` benchmark lines) and its report
passes ``disclose.check`` on every file; the standalone ``profile_table`` on the in-memory
fixture catalog (the EP-29-less path, the inlined unit SQL); crafted suppression of the
checks / top-k frames; **injected defects on a copied fixture lake** — a duplicated
primary-key row, an orphan ``hadm_id``, an implausible heart rate, an ``outtime <
intime`` stay, a wrong-unit lab row — each flagged by exactly one more ``n_affected``
(published as ``<11``), a stale slice and a missing EP-29 profile refused with the remedy;
``mwh qc status``; the docs page in sync; the import budget.
``tier("dev")`` / ``tier("full")``: the real catalogs' ``meta.qc_checks`` through
``safe_query`` (every check id present, nothing below k released).

Everything asserted or printed is counts, shares, dictionary text, schema names and
crafted synthetic values (ids >= 90 000 000) — never a row.
"""

from __future__ import annotations

import copy
import json
import re
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

import duckdb
import polars as pl
import pytest
import yaml

import helpers
from mimicwarehouse import config, disclose, units
from mimicwarehouse import run as run_mod
from mimicwarehouse.catalog.build import CATALOG_EXTENSIONS, STAGED_SCHEMAS
from mimicwarehouse.catalog.connect import open_catalog
from mimicwarehouse.cli import app
from mimicwarehouse.config import Settings
from mimicwarehouse.dag import benchmarks
from mimicwarehouse.dag import runner as runner_mod
from mimicwarehouse.dag.spec import load_dag
from mimicwarehouse.qc import profile as qc
from mimicwarehouse.qc import report as qc_report
from mimicwarehouse.qc.cli import status_summary

if TYPE_CHECKING:
    from mimicwarehouse.schema.contract import Contract

pytestmark = pytest.mark.ep_44

HOSP = "mimiciv_hosp"
ICU = "mimiciv_icu"
K = 11
BAND_TOKEN = re.compile(r"(?<![\w.])[123]\d{7}(?![\w.])")
HR, CREATININE = 220045, 50912
#: A hadm_id no fixture table carries and no real band contains (fixture ids >= 90 000 000).
ORPHAN_HADM = 99_999_999


@pytest.fixture(scope="module")
def thresholds() -> qc.Thresholds:
    return qc.load_thresholds()


def _tables(contract: Contract) -> list[str]:
    return [t.qualified_name for s in STAGED_SCHEMAS for t in contract.by_schema(s)]


def _scalar(con: duckdb.DuckDBPyConnection, sql: str) -> Any:
    row = con.execute(sql).fetchone()
    return None if row is None else row[0]


# ---------------------------------------------------------------------------
# 1. Wiring: the spec, the handlers, the catalog extension
# ---------------------------------------------------------------------------


def test_spec_wired_and_drift_free(contract: Contract, thresholds: qc.Thresholds) -> None:
    dag = load_dag()
    names = {s.name for s in dag.steps}
    expected = {f"{qc.STEP_PREFIX}{qn}" for qn in _tables(contract)}
    assert len(expected) == 31 and expected <= names
    checks = dag.step(qc.STEP_CHECKS)
    assert checks.kind == "python"
    assert checks.callable_name == "mimicwarehouse.qc.profile:run_checks"
    assert set(checks.depends_on) == expected
    report = dag.step(qc.STEP_REPORT)
    assert report.callable_name == "mimicwarehouse.qc.report:run_report"
    assert report.depends_on == (qc.STEP_CHECKS,)
    catalog = dag.step("catalog")
    assert qc.STEP_CHECKS in catalog.depends_on and qc.DAG_TAG in catalog.tags
    for name in expected:
        step = dag.step(name)
        assert step.callable_name == "mimicwarehouse.qc.profile:run_profile_table"
        assert "meta.profile" in step.depends_on and step.target is None
        assert set(step.tags) == {qc.DAG_TAG, "meta"}
        assert set(step.tiers) == {"fixture", "demo", "dev", "full"}
    chartevents = dag.step(f"{qc.STEP_PREFIX}{ICU}.chartevents")
    assert {
        f"stage.{ICU}.chartevents",
        f"stage.{HOSP}.patients",
        f"stage.{HOSP}.admissions",
        f"stage.{ICU}.icustays",
        f"stage.{ICU}.d_items",
    } <= set(chartevents.depends_on)
    ordered = [s.name for s in dag.ordered(tags=[qc.DAG_TAG], tier="fixture")]
    assert ordered[-1] == "catalog"
    assert ordered.index(qc.STEP_CHECKS) < ordered.index(qc.STEP_REPORT)
    assert all(ordered.index(n) < ordered.index(qc.STEP_CHECKS) for n in expected)
    # the committed file is the generator's output (ASCII, LF, no band-shaped integer)
    path = qc.spec_path()
    assert yaml.safe_load(path.read_text(encoding="utf-8")) == qc.spec_document(
        contract, thresholds
    )
    raw = path.read_bytes()
    assert b"\r" not in raw and raw.decode("utf-8").isascii()
    assert not BAND_TOKEN.search(raw.decode("utf-8"))
    assert path.read_text(encoding="utf-8") == qc.render_spec(contract, thresholds)
    # after the discovery walker that registers the tables it comments; the EP-39 / EP-41
    # pins (units last, phenotypes second to last) stay true
    from mimicwarehouse.concepts.runner import register_derived

    assert qc.register_qc in CATALOG_EXTENSIONS
    assert CATALOG_EXTENSIONS.index(qc.register_qc) > CATALOG_EXTENSIONS.index(register_derived)
    assert CATALOG_EXTENSIONS[-1] is units.register_units
    assert qc.RUN_KIND in run_mod.RUN_KINDS and qc.BENCH_KIND in benchmarks.BENCHMARK_KINDS


# ---------------------------------------------------------------------------
# 2. Thresholds: the packaged document, crafted refusals, the arithmetic
# ---------------------------------------------------------------------------


def test_thresholds_validate_and_dictionary_coded_rule(
    contract: Contract, thresholds: qc.Thresholds
) -> None:
    assert set(thresholds.checks) == set(qc.CHECK_IDS)
    qc.validate_thresholds(thresholds, contract)
    assert thresholds.top_k == 10
    coded = qc.dictionary_coded_columns(contract, thresholds)
    assert "itemid" in coded[f"{ICU}.chartevents"], "an FK into a dimension is automatic"
    assert "hcpcs_cd" in coded[f"{HOSP}.hcpcsevents"]
    assert "admission_type" in coded[f"{HOSP}.admissions"], "the declared list"
    for qn, columns in coded.items():
        table = contract.table(qn)
        assert columns == tuple(c for c in table.column_names if c in columns), "contract order"
        for name in columns:
            column = table.column(name)
            assert not column.identifier and not column.free_text, (qn, name)
    assert "caregiver_id" not in coded[f"{ICU}.chartevents"], "an FK to an identifier dim is not"
    assert "comments" not in coded.get(f"{HOSP}.labevents", ())
    # package-data hygiene
    raw = qc.thresholds_path().read_bytes()
    assert b"\r" not in raw and raw.decode("utf-8").isascii()
    assert not BAND_TOKEN.search(raw.decode("utf-8"))
    assert len(qc.thresholds_sha256()) == 64


def _refused(tmp_path: Path, doc: Any, match: str, contract: Contract) -> None:
    path = tmp_path / "crafted.yaml"
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    with pytest.raises(qc.QcError, match=match):
        qc.validate_thresholds(qc.load_thresholds_from(path), contract)


def test_crafted_thresholds_are_refused(tmp_path: Path, contract: Contract) -> None:
    base = yaml.safe_load(qc.thresholds_path().read_text(encoding="utf-8"))
    good = tmp_path / "good.yaml"
    good.write_text(yaml.safe_dump(base, sort_keys=False), encoding="utf-8")
    qc.validate_thresholds(qc.load_thresholds_from(good), contract)

    def mutated(mutate: Any) -> Any:
        doc = copy.deepcopy(base)
        mutate(doc)
        return doc

    _refused(tmp_path, mutated(lambda d: d["checks"].pop("pk_unique")), "missing", contract)
    _refused(
        tmp_path,
        mutated(lambda d: d["checks"].__setitem__("bogus", {"metric": "x"})),
        "unknown check",
        contract,
    )
    _refused(
        tmp_path,
        mutated(lambda d: d["dictionary_coded"].__setitem__(f"{HOSP}.admissions", ["subject_id"])),
        "identifier",
        contract,
    )
    _refused(
        tmp_path,
        mutated(lambda d: d["dictionary_coded"].__setitem__(f"{HOSP}.labevents", ["comments"])),
        "free-text",
        contract,
    )
    _refused(
        tmp_path,
        mutated(lambda d: d["dictionary_coded"].__setitem__(f"{HOSP}.nope", ["x"])),
        "unknown table",
        contract,
    )
    _refused(
        tmp_path,
        mutated(
            lambda d: d["timestamp_order"].append(
                {"table": f"{HOSP}.admissions", "earlier": "admittime", "later": "insurance"}
            )
        ),
        "TIMESTAMP",
        contract,
    )
    _refused(
        tmp_path,
        mutated(lambda d: d["checks"]["fk_orphans"].update({"warn_above": 0.5, "fail_above": 0.1})),
        "fail_above",
        contract,
    )
    _refused(tmp_path, mutated(lambda d: d.__setitem__("extra", 1)), "extra", contract)
    _refused(tmp_path, ["not", "a", "mapping"], "top level", contract)
    with pytest.raises(qc.QcError, match="cannot read"):
        qc.load_thresholds_from(tmp_path / "missing.yaml")


def test_threshold_evaluation_and_rules() -> None:
    above = qc.Threshold(metric="share", warn_above=0.0, fail_above=0.01)
    assert above.evaluate(None) == ("pass", None)
    assert above.evaluate(0.0) == ("pass", 0.0)
    assert above.evaluate(0.005) == ("warn", 0.0)
    assert above.evaluate(0.01) == ("warn", 0.0), "strict comparisons"
    assert above.evaluate(0.02) == ("fail", 0.01)
    assert above.rule() == "warn > 0; fail > 0.01"
    below = qc.Threshold(metric="share", warn_below=0.95)
    assert below.evaluate(0.9) == ("warn", 0.95) and below.evaluate(0.99) == ("pass", 0.95)
    assert below.rule() == "warn < 0.95"
    info = qc.Threshold(metric="x")
    assert info.informational and info.evaluate(5.0) == ("pass", None)
    assert info.rule() == "informational"
    count = qc.Threshold(metric="duplicate_rows", fail_above=0)
    assert count.evaluate(1.0) == ("fail", 0.0) and count.evaluate(0.0) == ("pass", 0.0)
    assert qc.worst_status(["pass", "warn", "pass"]) == "warn"
    assert qc.worst_status(["warn", "fail"]) == "fail" and qc.worst_status([]) == "pass"
    # a timestamp_order rule may override the check's bounds (with a note): the two
    # medication-order pairs are rates (stoptime < starttime on discontinued orders)
    thresholds = qc.load_thresholds()
    override = thresholds.order_rule(f"{HOSP}.pharmacy", "stoptime")
    assert override is not None and override.overrides and override.note
    assert thresholds.order_rule(f"{HOSP}.prescriptions", "stoptime") is not None
    assert not (thresholds.order_rule(f"{HOSP}.admissions", "dischtime") or override).overrides
    row = qc.CheckRow(
        "ts_order",
        f"{HOSP}.pharmacy",
        "stoptime",
        metric="violation_share",
        value=0.04,
        n_affected=50,
    )
    hard = qc.CheckRow(
        "ts_order",
        f"{HOSP}.admissions",
        "dischtime",
        metric="violation_share",
        value=0.04,
        n_affected=50,
    )
    evaluated = {e["column"]: e for e in qc.evaluate([row, hard], thresholds)}
    assert evaluated["stoptime"]["status"] == "warn" and evaluated["dischtime"]["status"] == "fail"
    assert evaluated["stoptime"]["rule"] == "warn > 0.01; fail > 0.1 (rule override)"
    assert evaluated["dischtime"]["rule"] == "warn > 0; fail > 0.01"
    with pytest.raises(ValueError, match="needs a note"):
        qc.OrderRule(table="t", earlier="a", later="b", fail_above=0.5)


# ---------------------------------------------------------------------------
# 3. The session fixture lake: the four tables, the run, the report
# ---------------------------------------------------------------------------


def test_session_lake_carries_the_qc_tables(
    fixture_lake_catalog: duckdb.DuckDBPyConnection,
    fixture_lake_settings: Settings,
    contract: Contract,
) -> None:
    con = fixture_lake_catalog
    tables = con.execute(
        'SELECT "schema", "table", rows, columns, worst_status, run_id, snapshot_id, tier '
        "FROM meta.qc_tables"
    ).fetchall()
    assert {f"{s}.{t}" for s, t, *_ in tables} == set(_tables(contract))
    by_name = {f"{s}.{t}": t_ for s, t, *t_ in tables}
    for qn, (rows, columns, worst, run_id, snapshot_id, tier) in by_name.items():
        assert rows >= 0 and columns == len(contract.table(qn).columns)
        assert worst in ("pass", "warn"), f"{qn}: the clean fixture never fails"
        assert run_mod.RUN_ID_RE.match(run_id) and len(snapshot_id) == 64 and tier == "fixture"
    assert len({v[3] for v in by_name.values()}) == 1, "one qc run wrote every row"
    assert _scalar(con, "SELECT count(*) FROM meta.qc_columns") == sum(
        len(contract.table(qn).columns) for qn in _tables(contract)
    )
    columns = con.execute(
        'SELECT "schema", "table", "column", is_identifier, is_free_text, is_dictionary_coded, '
        "null_pct, n_distinct_approx, min_value, max_value, p01, p50, p99, dtype "
        "FROM meta.qc_columns"
    ).fetchall()
    coded = qc.dictionary_coded_columns(contract)
    for s, t, c, is_id, is_text, is_coded, null_pct, nd, lo, hi, p01, p50, p99, dtype in columns:
        column = contract.table(f"{s}.{t}").column(c)
        assert is_id == column.identifier and is_text == column.free_text
        assert is_coded == (c in coded.get(f"{s}.{t}", ()))
        assert dtype == column.duckdb_type
        assert null_pct is not None and 0.0 <= null_pct <= 1.0, "reused from the EP-29 profile"
        assert nd is not None and nd >= 0
        if is_id:
            assert lo is None and hi is None and p01 is None and p50 is None and p99 is None
        if is_text:
            assert not is_coded
        if p50 is not None:
            assert p01 is not None and p99 is not None and p01 <= p50 <= p99
            assert qc.wants_quantiles(column) and not is_coded
    quantiles = con.execute(
        "SELECT p01, p50, p99 FROM meta.qc_columns WHERE \"table\" = 'labevents' "
        "AND \"column\" = 'valuenum'"
    ).fetchone()
    assert quantiles is not None and all(q is not None for q in quantiles)
    checks = con.execute("SELECT * FROM meta.qc_checks").pl()
    assert set(checks.get_column("check_id").to_list()) == set(qc.CHECK_IDS)
    assert set(checks.get_column("status").to_list()) <= {"pass", "warn"}
    assert (checks.get_column("k") == K).all()
    released = checks.filter(~pl.col("n_affected_suppressed")).get_column("n_affected").drop_nulls()
    assert not ((released > 0) & (released < K)).any(), "no small count leaves the build"
    hidden = checks.filter(pl.col("n_affected_suppressed"))
    assert hidden.get_column("n_affected").is_null().all()
    assert hidden.get_column("value").is_null().all(), "the share is blanked beside the count"
    assert hidden.height > 0, "the fixture has a few small cells to hide"
    assert checks.filter(pl.col("check_id") == "natural_key_dupes").height == 7
    assert checks.filter(pl.col("check_id") == "era_coverage").get_column("status").to_list() == [
        "pass"
    ]
    lengths = checks.get_column("detail").drop_nulls().str.len_chars()
    assert lengths.max() is None or int(lengths.max()) <= qc.VALUE_MAX_CHARS  # type: ignore[arg-type]
    topk = con.execute(
        'SELECT "schema", "table", "column", n, n_suppressed, share, value, rank, k '
        "FROM meta.qc_topk"
    ).fetchall()
    assert topk, "dictionary-coded columns carry top-k rows"
    for s, t, c, n, suppressed, share, value, rank, k in topk:
        assert c in coded[f"{s}.{t}"], "top-k only for dictionary-coded columns"
        assert 1 <= rank <= 10 and k == K
        assert value is None or len(value) <= qc.VALUE_MAX_CHARS
        if suppressed:
            assert n is None and share is None
        else:
            assert n is not None and n >= K and share is not None
    assert {c for _s, t, c, *_ in topk if t == "chartevents"} >= {"itemid"}
    assert not any(c == "comments" for _s, _t, c, *_ in topk)
    comment = _scalar(
        con,
        "SELECT comment FROM duckdb_tables() WHERE schema_name = 'meta' "
        "AND table_name = 'qc_checks'",
    )
    assert comment and "EP-44" in comment
    meta_tables = {
        str(r[0])
        for r in con.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'meta'"
        ).fetchall()
    }
    assert set(qc.META_TABLES) <= meta_tables and not any(n.startswith("raw") for n in meta_tables)
    lake = fixture_lake_settings.lake_root("fixture")
    for table in qc.RAW_TABLES:
        assert qc.raw_table_path(lake, "fixture", table).is_file()
    assert qc.slice_path(lake, "fixture", f"{ICU}.chartevents").is_file()
    raw_checks = pl.read_parquet(qc.raw_table_path(lake, "fixture", qc.CHECKS_TABLE))
    assert raw_checks.height == checks.height
    assert "n_affected_suppressed" not in raw_checks.columns


def test_session_lake_report_run_and_benchmarks(
    fixture_lake_catalog: duckdb.DuckDBPyConnection, fixture_lake_settings: Settings
) -> None:
    run_id = _scalar(fixture_lake_catalog, "SELECT DISTINCT run_id FROM meta.qc_tables")
    assert run_id is not None
    out = run_mod.run_dir(run_id, fixture_lake_settings)
    paths = [out / name for name in qc_report.REPORT_FILES]
    assert all(p.is_file() for p in paths), paths
    text = paths[0].read_text(encoding="utf-8")
    assert text.isascii() and "\r" not in text
    assert f"**Claim type: {qc.CLAIM_TYPE}.**" in text
    assert qc_report.RETROSPECTIVE_SENTENCE in text
    for needle in (
        f"`{run_id}`",
        "## Table summary",
        "## Checks by status",
        "### Warnings and failures",
        "## Unit variants (curated itemids)",
        "## Implausible values",
        "## Timestamp ordering",
        "## What it deliberately does not claim",
        "## Reproduction",
        "## Provenance",
        "`mimiciv_icu.chartevents`",
        "Heart Rate",
    ):
        assert needle in text, needle
    assert not BAND_TOKEN.search(text)
    for path in paths:
        result = disclose.check(path, k=K)
        assert result.passed, (path.name, [f.as_dict() for f in result.findings])
    manifest = run_mod.read_manifest(run_id, fixture_lake_settings)
    assert manifest.kind == qc.RUN_KIND and manifest.status == "ok"
    assert manifest.tier == "fixture" and manifest.claim_type == qc.CLAIM_TYPE
    assert {r.kind for r in manifest.refs} >= {"qc_thresholds", "item_catalogue"}
    thresholds_ref = next(r for r in manifest.refs if r.kind == "qc_thresholds")
    assert thresholds_ref.hash == qc.thresholds_sha256()
    assert "core" in manifest.snapshot_ids
    assert manifest.params["status_counts"]["fail"] == 0
    assert manifest.params["tables"] == 31 and manifest.params["k"] == K
    assert any(
        r.get("run_id") == run_id and r.get("kind") == qc.RUN_KIND
        for r in run_mod.read_ledger(fixture_lake_settings)
    )
    bench = benchmarks.summarize(fixture_lake_settings, tier="fixture", kind=qc.BENCH_KIND)
    assert bench.height == 31
    assert all(str(s).startswith(qc.STEP_PREFIX) for s in bench.get_column("step").to_list())
    assert (bench.get_column("ok")).all()


# ---------------------------------------------------------------------------
# 4. Standalone profile_table (no EP-29 profile) + crafted suppression
# ---------------------------------------------------------------------------


def test_profile_table_standalone_on_the_in_memory_fixture(
    fixture_catalog: duckdb.DuckDBPyConnection, contract: Contract, thresholds: qc.Thresholds
) -> None:
    table = contract.table(f"{ICU}.chartevents")
    profile = qc.profile_table(fixture_catalog, table, contract=contract, thresholds=thresholds)
    assert profile.rows == _scalar(fixture_catalog, f"SELECT count(*) FROM {ICU}.chartevents")
    columns = {c["column"]: c for c in profile.columns}
    assert set(columns) == set(table.column_names)
    assert columns["subject_id"]["is_identifier"] and columns["subject_id"]["min_value"] is None
    assert columns["itemid"]["is_dictionary_coded"] and columns["itemid"]["p50"] is None
    assert 0.0 <= columns["valuenum"]["null_pct"] <= 1.0 and columns["valuenum"]["p50"] is not None
    assert columns["valuenum"]["n_distinct_approx"] > 0
    assert columns["charttime"]["min_value"] is not None, "timestamps carry min / max"
    ranks = [t["rank"] for t in profile.topk if t["column"] == "itemid"]
    counts = [t["n"] for t in profile.topk if t["column"] == "itemid"]
    assert ranks == list(range(1, len(ranks) + 1)) and counts == sorted(counts, reverse=True)
    assert all(0 < (t["share"] or 0) <= 1 for t in profile.topk)
    ids = {c.check_id for c in profile.checks}
    assert ids == {
        "natural_key_dupes",
        "fk_orphans",
        "null_share",
        "ts_store_lag",
        "event_window",
        "unit_consistency",
        "implausible_values",
    }
    assert {c.column for c in profile.checks if c.check_id == "fk_orphans"} == {
        "subject_id",
        "hadm_id",
        "stay_id",
        "itemid",
    }
    hr = [c for c in profile.checks if c.itemid == HR]
    assert {c.check_id for c in hr} == {"unit_consistency", "implausible_values"}
    assert all(c.label == units.spec(HR).label for c in hr)
    assert all(c.value == 0.0 or c.value == 1.0 for c in hr)
    # every check row round-trips through the slice document and the threshold evaluation
    doc = json.loads(json.dumps(profile.to_dict()))
    again = qc.TableProfile.from_dict(doc)
    assert [c.as_dict() for c in again.checks] == [c.as_dict() for c in profile.checks]
    evaluated = qc.evaluate(profile.checks, thresholds)
    assert {e["status"] for e in evaluated} <= {"pass", "warn"}
    # a table with neither keys nor rules: only the null shares
    provider = qc.profile_table(fixture_catalog, contract.table(f"{HOSP}.provider"))
    assert {c.check_id for c in provider.checks} == {"natural_key_dupes", "null_share"}
    patients = qc.profile_table(fixture_catalog, contract.table(f"{HOSP}.patients"))
    assert {c.check_id for c in patients.checks} >= {"age_cap", "era_coverage", "pk_unique"}
    era = next(c for c in patients.checks if c.check_id == "era_coverage")
    assert era.n_affected == 0 and era.value == 0.0
    # the inlined unit SQL never calls the macro family (docs/gotchas.md section 1)
    sql = qc.units_sql(table, "chartevents", units.load_catalogue())
    assert "mwh_harmonize" not in sql and str(HR) in sql
    with pytest.raises(qc.QcError, match="not present"):
        qc.profile_table(fixture_catalog, contract.table("mimiciv_ed.edstays"))


def _crafted_profile() -> qc.TableProfile:
    table = f"{HOSP}.patients"
    specs: list[tuple[str, str, str, float, int]] = [
        ("pk_unique", "subject_id", "duplicate_rows", 0.0, 0),
        ("fk_orphans", "hadm_id", "orphan_share", 0.002, 1),
        ("null_share", "dod", "null_share", 0.6, 5),
        ("null_share", "gender", "null_share", 0.0, 0),
        ("age_cap", "anchor_age", "capped_share", 0.1, 11),
        ("era_coverage", "anchor_year_group", "empty_eras", 0.0, 0),
    ]
    rows = [
        qc.CheckRow(check_id, table, column, metric=metric, value=value, n_affected=n)
        for check_id, column, metric, value, n in specs
    ]
    topk = [
        {"column": "gender", "rank": 1, "value": "F", "n": 60, "share": 0.6},
        {"column": "gender", "rank": 2, "value": "M", "n": 40, "share": 0.4},
        {"column": "anchor_year_group", "rank": 1, "value": "2008 - 2010", "n": 50, "share": 0.5},
        {"column": "anchor_year_group", "rank": 2, "value": "2011 - 2013", "n": 45, "share": 0.45},
        {"column": "anchor_year_group", "rank": 3, "value": "2014 - 2016", "n": 5, "share": 0.05},
    ]
    return qc.TableProfile(
        table=f"{HOSP}.patients",
        rows=100,
        columns=[
            {
                "column": "subject_id",
                "ordinal": 1,
                "dtype": "INTEGER",
                "is_identifier": True,
                "is_free_text": False,
                "is_dictionary_coded": False,
                "null_pct": 0.0,
                "n_distinct_approx": 100,
                "min_value": None,
                "max_value": None,
                "p01": None,
                "p50": None,
                "p99": None,
            }
        ],
        topk=topk,
        checks=rows,
        parquet_bytes=1234,
        files=1,
        wall_s=0.5,
        peak_rss_mb=100.0,
    )


def test_assemble_suppresses_small_counts_and_evaluates_thresholds() -> None:
    frames = qc.assemble(
        [_crafted_profile()],
        k=K,
        tier="fixture",
        build_id="b",
        run_id=None,
        snapshot_id="s",
        built_at="t",
    )
    checks = frames[qc.CHECKS_TABLE]
    assert list(checks.columns) == list(qc.CHECKS_COLUMNS)
    by_check = {(r["check_id"], r["column"]): r for r in checks.to_dicts()}
    orphan = by_check[("fk_orphans", "hadm_id")]
    assert orphan["n_affected"] is None and orphan["n_affected_suppressed"] is True
    assert orphan["value"] is None, "the share is blanked beside the hidden count"
    assert orphan["status"] == "warn" and orphan["threshold"] == 0.0, "status survives"
    dod = by_check[("null_share", "dod")]
    assert dod["n_affected"] is None and dod["status"] == "warn"
    assert by_check[("age_cap", "anchor_age")]["n_affected"] == 11, "k itself is released"
    assert by_check[("pk_unique", "subject_id")]["n_affected"] == 0, "zero is never small"
    assert by_check[("era_coverage", "anchor_year_group")]["status"] == "pass"
    raw = frames[f"raw_{qc.CHECKS_TABLE}"]
    assert raw.filter(pl.col("check_id") == "fk_orphans").get_column("n_affected").to_list() == [1]
    tables = frames[qc.TABLES_TABLE]
    assert tables.get_column("worst_status").to_list() == ["warn"]
    assert tables.get_column("rows").to_list() == [100]
    topk = frames[qc.TOPK_TABLE]
    assert list(topk.columns) == list(qc.TOPK_COLUMNS)
    eras = topk.filter(pl.col("column") == "anchor_year_group").sort("rank")
    assert eras.get_column("n").to_list() == [50, None, None], "complementary: the neighbour goes"
    assert eras.get_column("n_suppressed").to_list() == [False, True, True]
    assert eras.get_column("share").to_list()[1:] == [None, None]
    gender = topk.filter(pl.col("column") == "gender").sort("rank")
    assert gender.get_column("n").to_list() == [60, 40]
    assert frames[f"raw_{qc.TOPK_TABLE}"].height == 5
    # the report renders the crafted frames and passes the gate (no run manifest needed)
    report = qc_report.QcReport(
        tier="fixture",
        k=K,
        run_id=None,
        build_id="b",
        snapshot_id="s",
        tables=tables,
        checks=checks,
        n_columns=1,
        generated="2026-09-14",
    )
    text = qc_report.render_report(report, reproduction="## Reproduction\n\ncrafted\n")
    assert "<11" in text and "| `fk_orphans` |" in text and text.isascii()


# ---------------------------------------------------------------------------
# 5. Injected defects on a copied fixture lake
# ---------------------------------------------------------------------------


def _bucket_files(lake: Path, schema: str, table: str) -> list[Path]:
    return sorted((lake / "core" / schema / table).glob("subject_bucket=*/part-0.parquet"))


def _rewrite(path: Path, mutate: Any) -> None:
    frame = pl.read_parquet(path)
    mutate(frame).write_parquet(path)


def _n_affected(
    frame: pl.DataFrame,
    check_id: str,
    table: str,
    column: str | None = None,
    itemid: int | None = None,
) -> int:
    sub = frame.filter((pl.col("check_id") == check_id) & (pl.col("table") == table))
    if column is not None:
        sub = sub.filter(pl.col("column") == column)
    if itemid is not None:
        sub = sub.filter(pl.col("itemid") == itemid)
    assert sub.height == 1, (check_id, table, column, itemid, sub.height)
    value = sub.get_column("n_affected")[0]
    return -1 if value is None else int(value)


def test_injected_defects_are_flagged_exactly_once(
    fixture_lake_settings: Settings, tmp_path: Path, contract: Contract, thresholds: qc.Thresholds
) -> None:
    source_root = fixture_lake_settings.data_root
    source_lake = fixture_lake_settings.lake_root("fixture")
    root = tmp_path / "root"
    settings = Settings(data_root=root)
    lake = settings.lake_root("fixture")
    shutil.copytree(source_lake, lake)
    assert lake.relative_to(root) == source_lake.relative_to(source_root)
    before = pl.read_parquet(qc.raw_table_path(lake, "fixture", qc.CHECKS_TABLE))

    # 1. a duplicated primary-key row (icustays, PK stay_id) and 4. an outtime < intime stay
    # (two distinct stays of one bucket file: the first is doubled, the second reversed)
    stays = [p for p in _bucket_files(lake, ICU, "icustays") if pl.read_parquet(p).height >= 2]
    assert stays, "a fixture bucket with two ICU stays"

    def dup_and_reverse(frame: pl.DataFrame) -> pl.DataFrame:
        first = frame.head(1)
        second = frame.slice(1, 1) if frame.height > 1 else frame.head(1)
        reversed_stay = second.with_columns(
            (pl.col("intime") - pl.duration(hours=1)).alias("outtime")
        )
        rest = frame.slice(2) if frame.height > 2 else frame.clear()
        return pl.concat([first, first, reversed_stay, rest], how="vertical")

    _rewrite(stays[0], dup_and_reverse)
    # 2. an orphan hadm_id (diagnoses_icd -> admissions)
    diagnoses = _bucket_files(lake, HOSP, "diagnoses_icd")

    def orphan(frame: pl.DataFrame) -> pl.DataFrame:
        return frame.with_columns(
            pl.when(pl.int_range(pl.len()) == 0)
            .then(pl.lit(ORPHAN_HADM, dtype=frame.schema["hadm_id"]))
            .otherwise(pl.col("hadm_id"))
            .alias("hadm_id")
        )

    _rewrite(diagnoses[0], orphan)
    # 3. an implausible heart rate (chartevents, itemid 220045: 900 bpm)
    charts = _bucket_files(lake, ICU, "chartevents")
    injected_hr = False
    for path in charts:
        frame = pl.read_parquet(path)
        hits = frame.with_row_index("_i").filter(pl.col("itemid") == HR)
        if hits.height == 0:
            continue
        target = int(hits.get_column("_i")[0])
        frame = frame.with_columns(
            pl.when(pl.int_range(pl.len()) == target)
            .then(pl.lit(900.0))
            .otherwise(pl.col("valuenum"))
            .alias("valuenum")
        )
        frame.write_parquet(path)
        injected_hr = True
        break
    assert injected_hr, "the fixture charts a heart rate"
    # 5. a wrong-unit lab row (creatinine in 'furlongs')
    labs = _bucket_files(lake, HOSP, "labevents")
    injected_unit = False
    for path in labs:
        frame = pl.read_parquet(path)
        hits = frame.with_row_index("_i").filter(pl.col("itemid") == CREATININE)
        if hits.height == 0:
            continue
        target = int(hits.get_column("_i")[0])
        frame = frame.with_columns(
            pl.when(pl.int_range(pl.len()) == target)
            .then(pl.lit("furlongs"))
            .otherwise(pl.col("valueuom"))
            .alias("valueuom")
        )
        frame.write_parquet(path)
        injected_unit = True
        break
    assert injected_unit, "the fixture measures creatinine"

    result = runner_mod.run(
        load_dag(),
        "fixture",
        select=[
            "meta.profile",
            f"{qc.STEP_PREFIX}{ICU}.icustays",
            f"{qc.STEP_PREFIX}{HOSP}.diagnoses_icd",
            f"{qc.STEP_PREFIX}{ICU}.chartevents",
            f"{qc.STEP_PREFIX}{HOSP}.labevents",
            qc.STEP_CHECKS,
            qc.STEP_REPORT,
            "catalog",
        ],
        settings=settings,
    )
    assert result.ok, [f"{s.name}: {s.error}" for s in result.steps if s.status == "failed"]
    after = pl.read_parquet(qc.raw_table_path(lake, "fixture", qc.CHECKS_TABLE))
    assert after.height == before.height, "the same check matrix, new counts"
    for check_id, table, column, itemid in (
        ("pk_unique", "icustays", "stay_id", None),
        ("fk_orphans", "diagnoses_icd", "hadm_id", None),
        ("implausible_values", "chartevents", "valuenum", HR),
        ("ts_order", "icustays", "outtime", None),
        ("unit_consistency", "labevents", "valueuom", CREATININE),
    ):
        was = _n_affected(before, check_id, table, column, itemid)
        now = _n_affected(after, check_id, table, column, itemid)
        assert now == was + 1, (check_id, table, column, itemid, was, now)
        assert was == 0, f"{check_id}: the clean fixture had none"
    statuses = {
        (r["check_id"], r["table"]): r["status"]
        for r in after.filter(pl.col("table").is_in(["icustays", "diagnoses_icd"])).to_dicts()
    }
    assert statuses[("pk_unique", "icustays")] == "fail"
    assert statuses[("fk_orphans", "diagnoses_icd")] in ("warn", "fail")
    ts = after.filter((pl.col("check_id") == "ts_order") & (pl.col("table") == "icustays"))
    expected_status, _bound = thresholds.check("ts_order").evaluate(ts.get_column("value")[0])
    assert ts.get_column("status")[0] == expected_status != "pass"
    # the published table hides the single row behind <k and blanks the share
    con = open_catalog("fixture", settings=settings)
    try:
        row = con.execute(
            "SELECT n_affected, n_affected_suppressed, value, status FROM meta.qc_checks "
            "WHERE check_id = 'pk_unique' AND \"table\" = 'icustays'"
        ).fetchone()
        assert row == (None, True, None, "fail")
        assert _scalar(con, "SELECT count(*) FROM meta.qc_tables") == 31
        assert (
            _scalar(con, "SELECT worst_status FROM meta.qc_tables WHERE \"table\" = 'icustays'")
            == "fail"
        )
        run_id = _scalar(con, "SELECT DISTINCT run_id FROM meta.qc_tables")
    finally:
        con.close()
    summary = status_summary("fixture", settings=settings, show="fail", actor="test_ep44")
    assert summary["totals"]["fail"] >= 1 and summary["tables"]["fail"] >= 1
    assert any(r["check_id"] == "pk_unique" and r["table"] == "icustays" for r in summary["rows"])
    assert all(r["n_affected"] is None for r in summary["rows"] if r["n_affected_suppressed"])
    report = run_mod.run_dir(run_id, settings) / qc_report.REPORT_FILENAME
    assert report.is_file()
    assert "| fail | `pk_unique` | `mimiciv_icu.icustays` |" in report.read_text(encoding="utf-8")
    for name in qc_report.REPORT_FILES:
        result_check = disclose.check(run_mod.run_dir(run_id, settings) / name, k=K)
        assert result_check.passed, (name, [f.as_dict() for f in result_check.findings])
    # a stale slice refuses qc.checks with the remedy; a missing EP-29 profile refuses a step
    slice_path = qc.slice_path(lake, "fixture", f"{HOSP}.provider")
    doc = json.loads(slice_path.read_text(encoding="utf-8"))
    doc["snapshot_id"] = "0" * 64
    slice_path.write_text(json.dumps(doc), encoding="utf-8")
    stale = runner_mod.run(load_dag(), "fixture", select=[qc.STEP_CHECKS], settings=settings)
    assert not stale.ok and "stale" in (stale.steps[0].error or "")
    from mimicwarehouse.catalog.profile import profile_paths

    _tables_path, columns_path = profile_paths(lake, "fixture")
    columns_path.unlink()
    missing = runner_mod.run(
        load_dag(), "fixture", select=[f"{qc.STEP_PREFIX}{HOSP}.provider"], settings=settings
    )
    assert not missing.ok and "EP-29 profile" in (missing.steps[0].error or "")


# ---------------------------------------------------------------------------
# 6. mwh qc status
# ---------------------------------------------------------------------------


def test_qc_status_cli(fixture_lake_settings: Settings) -> None:
    runner = helpers.cli_runner()
    root = str(fixture_lake_settings.data_root)
    try:
        listing = runner.invoke(
            app, ["--data-root", root, "qc", "status", "--tier", "fixture", "--show", "all"]
        )
        assert listing.exit_code == 0, listing.output
        assert "meta.qc_checks (fixture)" in listing.output
        assert "null_share" in listing.output and "31 table(s) profiled" in listing.output
        assert "flagged checks (all)" in listing.output and "<11" in listing.output
        assert not BAND_TOKEN.search(listing.output)
        as_json = runner.invoke(
            app, ["--data-root", root, "qc", "status", "--tier", "fixture", "--json"]
        )
        assert as_json.exit_code == 0, as_json.output
        payload = json.loads(as_json.stdout)
        assert payload["tier"] == "fixture" and payload["tables"]["n"] == 31
        assert set(payload["counts"]) == set(qc.CHECK_IDS)
        assert payload["totals"]["fail"] == 0 and payload["totals"]["warn"] > 0
        assert payload["rows"] == [], "--show fail lists nothing on the clean fixture"
        assert run_mod.RUN_ID_RE.match(payload["run_id"])
        bad_tier = runner.invoke(app, ["--data-root", root, "qc", "status", "--tier", "nope"])
        assert bad_tier.exit_code == 2
        bad_show = runner.invoke(
            app, ["--data-root", root, "qc", "status", "--tier", "fixture", "--show", "some"]
        )
        assert bad_show.exit_code == 2
    finally:
        config.configure()


# ---------------------------------------------------------------------------
# 7. Docs in sync, import budget
# ---------------------------------------------------------------------------


def test_methods_doc_in_sync(tmp_path: Path) -> None:
    path = qc_report.methods_doc_path()
    text = path.read_text(encoding="utf-8")
    for needle in (
        "meta.qc_tables",
        "meta.qc_columns",
        "meta.qc_topk",
        "meta.qc_checks",
        "mwh qc status",
        "thresholds.yaml",
        "qc_report.md",
        "disclose.suppress",
        "retrospective",
        "EP-29",
        "EP-39",
        "EP-43",
        "EP-44",
        "EP-45",
        "EP-53",
        "EP-61",
        "QC-1",
    ):
        assert needle in text, needle
    for check_id in qc.CHECK_IDS:
        assert f"`{check_id}`" in text
    assert qc_report.render_check_table().rstrip("\n") in text
    assert qc_report.render_dictionary_list().rstrip("\n") in text
    copy_path = tmp_path / "qc.md"
    copy_path.write_text(text, encoding="utf-8", newline="\n")
    qc_report.sync_methods_doc(copy_path)
    assert copy_path.read_text(encoding="utf-8") == text, "re-run `python -m mimicwarehouse.qc`"
    assert not BAND_TOKEN.search(text) and text.isascii()
    assert (helpers.WORKSPACE / "docs" / "analyses" / "README.md").read_text(
        encoding="utf-8"
    ).count("EP-44") >= 1


def test_import_budget() -> None:
    helpers.assert_import_budget(
        "mimicwarehouse.cli",
        lazy=(
            "mimicwarehouse.qc.report",
            "mimicwarehouse.safe",
            "mimicwarehouse.run",
            "mimicwarehouse.dag.runner",
        ),
    )


# ---------------------------------------------------------------------------
# 8. Dev / full tiers: the real catalogs through safe_query (aggregates only)
# ---------------------------------------------------------------------------


def _real_tier_probe(tier: str) -> None:
    from mimicwarehouse.safe import safe_query

    settings = config.load_settings()
    remedy = f"run `mwh build --tier {tier} --tag qc` (EP-44) first"
    summary = status_summary(tier, settings=settings, show="all", actor="test_ep44")
    assert summary["tables"]["n"] == 31, remedy
    for check_id in qc.CHECK_IDS:
        assert sum(summary["counts"][check_id].values()) >= 1, (tier, check_id, remedy)
    released = safe_query(
        "SELECT n_affected, n_affected_suppressed, k FROM meta.qc_checks",
        tier=tier,
        settings=settings,
        actor="test_ep44",
        row_cap=10_000,
    ).df
    assert (released.get_column("k") >= K).all()
    shown = released.filter(~pl.col("n_affected_suppressed")).get_column("n_affected").drop_nulls()
    assert not ((shown > 0) & (shown < K)).any(), "nothing below k leaves the real catalogs"
    topk = safe_query(
        "SELECT n, n_suppressed FROM meta.qc_topk",
        tier=tier,
        settings=settings,
        actor="test_ep44",
        row_cap=10_000,
    ).df
    shown_topk = topk.filter(~pl.col("n_suppressed")).get_column("n").drop_nulls()
    assert not ((shown_topk > 0) & (shown_topk < K)).any()
    print(
        f"{tier}: {summary['tables']['n']} tables, pass {summary['totals']['pass']:,} / "
        f"warn {summary['totals']['warn']:,} / fail {summary['totals']['fail']:,}; run "
        f"{summary['run_id']}"
    )
    for row in summary["rows"]:
        if row["status"] == "fail":
            print(f"{tier}: fail {row['check_id']} {row['schema']}.{row['table']} {row['column']}")


@pytest.mark.tier("dev")
def test_dev_qc_tables(dev_catalog: Path) -> None:
    _real_tier_probe("dev")


@pytest.mark.tier("full")
def test_full_qc_tables(full_catalog: Path) -> None:
    _real_tier_probe("full")
