"""EP-175 — latency marts A2: the itemid rollups ``marts.itemid_daily`` /
``marts.itemid_summary`` (fixture; dev where marked).

Covers: both specs validate (columns, types, keys, grains, partition, units) and the
registry's EP-175 shapes (a ``cte: true`` base, ``read`` sources, the summary grain
``itemid``) generate the statements and refuse their malformed forms; every curated
chartevents / labevents itemid is found in its ``source_ref`` concept file and a crafted
mismatch refuses the build; the values SQL inlines the conversions (no macro); the DAG
wiring; a crafted lake (synthetic ids >= 90 000 000) through both step handlers --
``n + n_implausible`` equals the raw count of valued rows (values on both sides of a bound,
one at the bound, one in an accepted non-canonical unit, one in an unaccepted unit), no
``icu_day < 0``, the day boundary, labs outside every stay window dropped, first / last by
charttime with ties broken by value, the summary's counts and percentiles, its release at
k (counts, share and percentiles blanked, ``suppressed`` / ``k``) and the release gate over
the published file; the session fixture lake (declared columns and types, raw counts
reproduced, daily vs summary agreement, one summary row per rolled-up itemid,
``safe_query`` registry read without a count column, row-level reads refused, the three
marts in ``meta.mart_tables``, the dictionary sections). ``tier("dev")``: the same
invariants through ``safe_query`` and the dev build's wall time from the benchmark ledger.
Section 5 pins the safe-query fix this session made on the owner's word: a result column
named after a count column's small-cell marker, and any failure inside the suppressor, are
audited refusals rather than unaudited crashes after execution.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import duckdb
import polars as pl
import pytest
import yaml

from mimicwarehouse import config, disclose, safe, units
from mimicwarehouse.dag.spec import load_dag
from mimicwarehouse.marts import itemid_rollups as ir
from mimicwarehouse.marts import registry
from mimicwarehouse.marts.registry import MartBuildError, MartSpecError

if TYPE_CHECKING:  # pragma: no cover
    from mimicwarehouse.config import Settings

pytestmark = pytest.mark.ep_175

K = 11
DAILY_COLUMNS: tuple[tuple[str, str], ...] = (
    ("subject_id", "INTEGER"),
    ("stay_id", "INTEGER"),
    ("subject_bucket", "INTEGER"),
    ("itemid", "INTEGER"),
    ("icu_day", "INTEGER"),
    ("n", "INTEGER"),
    ("min", "DOUBLE"),
    ("max", "DOUBLE"),
    ("mean", "DOUBLE"),
    ("first", "DOUBLE"),
    ("last", "DOUBLE"),
    ("n_implausible", "INTEGER"),
)
SUMMARY_COLUMNS: tuple[tuple[str, str], ...] = (
    ("itemid", "INTEGER"),
    ("label", "VARCHAR"),
    ("source_table", "VARCHAR"),
    ("unit", "VARCHAR"),
    ("n_stays", "BIGINT"),
    ("n_measurements", "BIGINT"),
    ("pct_stays_measured", "DOUBLE"),
    ("p01", "DOUBLE"),
    ("p50", "DOUBLE"),
    ("p99", "DOUBLE"),
    ("suppressed", "BOOLEAN"),
    ("k", "INTEGER"),
)
DEPENDS_ON = (
    "stage.mimiciv_icu.icustays",
    "stage.mimiciv_icu.chartevents",
    "stage.mimiciv_hosp.labevents",
    "units.item_units",
    "units.variants",
    "units.dictionary",
)
#: Curated itemids the crafted lake uses (EP-39 catalogue).
HR, TEMP_F, CREATININE = 220045, 223761, 50912


def _spec_doc(name: str) -> dict[str, Any]:
    doc = yaml.safe_load((registry.specs_dir() / f"{name}.yaml").read_text(encoding="utf-8"))
    assert isinstance(doc, dict)
    return doc


def _write_spec(tmp_path: Path, doc: dict[str, Any], name: str) -> Path:
    path = tmp_path / f"{name}.yaml"
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return path


def _ids(source: str) -> list[int]:
    return [i.itemid for i in ir.rollup_items() if i.source == source]


def _id_list(ids: list[int]) -> str:
    return ", ".join(str(i) for i in ids)


# ---------------------------------------------------------------------------
# 1. The specs, the registry's EP-175 shapes, the source references, the SQL, the DAG
# ---------------------------------------------------------------------------


def test_packaged_specs_validate() -> None:
    reg = registry.load_registry()
    # EP-56 adds its hourly marts beside these (the churn rule: a shipped count moved)
    assert {"icustay_first_day", ir.SPEC_DAILY, ir.SPEC_SUMMARY} <= set(reg.names)
    daily, summary = ir.daily_spec(), ir.summary_spec()
    assert daily.grain == "icu_day" and daily.version == 1
    assert daily.key == ("subject_id", "stay_id", "itemid", "icu_day")
    assert daily.partition == registry.BUCKET_COLUMN
    assert daily.sort == ("subject_id", "stay_id", "itemid", "icu_day")
    assert tuple((c.name, c.type) for c in daily.expanded) == DAILY_COLUMNS
    assert daily.column("subject_bucket").expr == f"r.subject_id % {registry.NUM_BUCKETS}"
    assert summary.grain == "itemid" and summary.grain in registry.SUMMARY_GRAINS
    assert summary.partition is None and summary.key == ("itemid",)
    assert summary.sort == ("itemid",)
    assert tuple((c.name, c.type) for c in summary.expanded) == SUMMARY_COLUMNS
    assert summary.column("pct_stays_measured").unit == "%"
    for spec, builder, step in (
        (daily, "run_itemid_daily", ir.STEP_DAILY),
        (summary, "run_itemid_summary", ir.STEP_SUMMARY),
    ):
        assert spec.builder == f"mimicwarehouse.marts.itemid_rollups:{builder}"
        assert spec.step_name == spec.status_key == step
        assert spec.depends_on == DEPENDS_ON
        assert spec.base.cte and spec.cte_aliases == (spec.base.alias,)
        reads = {s.relation for s in spec.sources if s.join == "read"}
        assert {"mimiciv_icu.chartevents", "mimiciv_hosp.labevents"} <= reads
        assert registry.check_units(spec) == []
        assert all(c.type in registry.DUCKDB_TYPES for c in spec.expanded)
        text = (registry.specs_dir() / f"{spec.name}.yaml").read_text(encoding="utf-8")
        assert text.isascii()
    assert daily.base.relation == ir.STAYS_TABLE
    # the summary is a registry table: no count column needed, nothing below k inside
    assert "marts.itemid_summary" in safe.REGISTRY_TABLES
    assert safe.is_registry_ref("marts", "itemid_summary")
    assert not safe.is_registry_ref("marts", "itemid_daily"), "the daily mart is subject-keyed"


def _shape_mutations() -> list[tuple[str, str, Any, str]]:
    def read_with_join_on(d: dict[str, Any]) -> None:
        d["sources"][1]["join_on"] = "stay_id"

    def read_with_cte(d: dict[str, Any]) -> None:
        d["sources"][1]["cte"] = True

    def base_with_join_on(d: dict[str, Any]) -> None:
        d["sources"][0]["join_on"] = "stay_id"

    def summary_partitioned(d: dict[str, Any]) -> None:
        d["partition"] = "subject_bucket"  # the one legal value, refused for a summary grain

    def unknown_grain(d: dict[str, Any]) -> None:
        d["grain"] = "fortnight"

    def family_on_read(d: dict[str, Any]) -> None:
        d["columns"].append(
            {
                "family": "bad",
                "alias": "ce",
                "stats": ["min"],
                "description": "{stat} {label}",
                "vars": [{"name": "valuenum", "label": "value"}],
            }
        )

    return [
        ("read source with join_on", ir.SPEC_DAILY, read_with_join_on, "takes no join_on"),
        ("read source with cte", ir.SPEC_DAILY, read_with_cte, "takes no join_on and no cte"),
        ("base with join_on", ir.SPEC_DAILY, base_with_join_on, "base table takes no join_on"),
        ("summary partitioned", ir.SPEC_SUMMARY, summary_partitioned, "takes no partition"),
        ("unknown grain", ir.SPEC_SUMMARY, unknown_grain, "not an EP-34 grain"),
        ("family on a read source", ir.SPEC_DAILY, family_on_read, "is a read source"),
    ]


@pytest.mark.parametrize(
    ("label", "name", "mutate", "message"), _shape_mutations(), ids=lambda v: str(v)
)
def test_malformed_shapes_are_refused(
    tmp_path: Path, label: str, name: str, mutate: Any, message: str
) -> None:
    doc = _spec_doc(name)
    mutate(doc)
    with pytest.raises(MartSpecError, match=message):
        registry.load_spec_from(_write_spec(tmp_path, doc, name))


def test_statements_are_generated_from_the_specs() -> None:
    daily = ir.daily_sql()
    assert daily.startswith("WITH cte_r AS (")
    assert registry.select_list_sql(ir.daily_spec()) in daily, "the spec's select list"
    assert "FROM cte_r AS r" in daily and "LEFT JOIN" not in daily, "read sources never join"
    assert "mwh_harmonize" not in daily, "the conversions are inlined (docs/gotchas.md §1)"
    assert "arg_min(v, (charttime, v))" in daily and "arg_max(v, (charttime, v))" in daily
    assert "WHERE icu_day >= 0" in daily and "/ 24.0" in daily
    assert "l.charttime >= s.intime AND l.charttime < s.outtime" in daily, "labs by window"
    assert "s.stay_id = l.stay_id" in daily, "chartevents by stay"
    assert "outputevents" not in daily and "inputevents" not in daily
    # the unit string is normalised only on the rows of items with a non-identity conversion
    needs = sorted(
        i.itemid
        for i in ir.rollup_items()
        if any(not a.is_identity for a in i.conversions().values())
    )
    assert TEMP_F in needs and HR not in needs and CREATININE in needs
    summary = ir.summary_sql()
    assert summary.startswith(f"WITH cte_s AS (\nSELECT * FROM {ir.RELEASED_TABLE}\n)")
    assert summary.count("CAST(") == len(SUMMARY_COLUMNS) and "FROM cte_s AS s" in summary
    aggregate = ir.summary_aggregate_sql()
    assert "quantile_cont(v, [0.01, 0.5, 0.99])" in aggregate
    assert "count(DISTINCT stay_id) FILTER (WHERE plausible)" in aggregate
    with pytest.raises(MartSpecError, match="CTEs missing"):
        registry.mart_sql(ir.daily_spec(), {})
    with pytest.raises(MartBuildError, match="not rolled up"):
        ir.source_values_sql("outputevents", [])


def test_every_curated_itemid_is_in_its_source_ref() -> None:
    items = ir.rollup_items()
    catalogue = units.load_catalogue()
    assert len(items) == len(catalogue.itemids("chartevents")) + len(catalogue.itemids("labevents"))
    assert {i.source for i in items} == set(ir.ROLLUP_SOURCES)
    check = ir.check_source_refs()
    assert check.ok, check.problems
    assert len(check.concept) + len(check.dictionary) == len(items)
    assert len(check.concept) >= 40, "most itemids were copied from a concept file"
    by_id = {i.itemid: i for i in items}
    for itemid in check.dictionary:
        assert not by_id[itemid].source_ref.startswith(ir.CONCEPT_REF_PREFIX)
    for itemid, ref in check.concept:
        assert (ir.vendor_tree() / ref).is_file()
        assert by_id[itemid].source_ref == ref


def test_a_source_ref_mismatch_is_refused(tmp_path: Path) -> None:
    hr = units.spec(HR)
    tree = tmp_path / "tree"
    ref_file = tree / hr.source_ref
    ref_file.parent.mkdir(parents=True)
    ref_file.write_text("SELECT 1 -- no curated itemid here (a crafted concept file)\n")
    check = ir.check_source_refs([hr], tree=tree)
    assert not check.ok and "not found in its source_ref" in check.problems[0]
    missing = hr.model_copy(update={"source_ref": "mimic-iv/concepts_duckdb/nowhere.sql"})
    assert "is not a vendored file" in ir.check_source_refs([missing], tree=tree).problems[0]
    lookup = hr.model_copy(update={"source_ref": "mimiciv_icu.d_items (a lookup)"})
    assert ir.check_source_refs([lookup], tree=tree).dictionary == (HR,)


def test_dag_wiring() -> None:
    dag = load_dag()
    for spec in (ir.daily_spec(), ir.summary_spec()):
        step = dag.step(spec.step_name)
        assert step.kind == "python" and step.callable_name == spec.builder
        assert step.qualified_table == spec.status_key, "skips when complete for the tier"
        assert tuple(step.depends_on) == spec.depends_on, "the DAG mirrors the spec"
        assert "marts" in step.tags and set(step.tiers) == {"fixture", "demo", "dev", "full"}
    names = {s.name for s in dag.steps}
    assert set(DEPENDS_ON) <= names
    catalog = dag.step("catalog")
    assert {ir.STEP_DAILY, ir.STEP_SUMMARY} <= set(catalog.depends_on)
    ordered = [s.name for s in dag.ordered(tags=["marts"], tier="fixture")]
    assert {ir.STEP_DAILY, ir.STEP_SUMMARY, "marts.icustay_first_day"} <= set(ordered)
    assert ordered[-1] == "catalog"


# ---------------------------------------------------------------------------
# 2. A crafted lake (synthetic rows, temp data root) through both step handlers
# ---------------------------------------------------------------------------

#: Two synthetic ICU stays: A (60 h, days 0-2) and B (24 h); subject C has no stay.
SUBJ_A, SUBJ_B, SUBJ_C = 90000001, 90000002, 90000003
STAY_A, STAY_B = 93000001, 93000002
IN_A, OUT_A = "2150-01-01 08:00:00", "2150-01-03 20:00:00"
IN_B, OUT_B = "2150-02-01 00:00:00", "2150-02-02 00:00:00"
CHART: list[tuple[int, int, str, float | None, str | None]] = [
    # (stay_id, itemid, charttime, valuenum, valueuom)
    (STAY_A, HR, "2150-01-01 07:00:00", 85.0, "bpm"),  # before intime: day -1, dropped
    (STAY_A, HR, "2150-01-01 09:00:00", 80.0, "bpm"),  # day 0, first
    (STAY_A, HR, "2150-01-01 10:00:00", 90.0, "bpm"),
    (STAY_A, HR, "2150-01-01 10:30:00", None, "bpm"),  # no value: not a measurement
    (STAY_A, HR, "2150-01-01 11:00:00", 350.0, "bpm"),  # above the bound: implausible
    (STAY_A, HR, "2150-01-01 12:00:00", 300.0, "bpm"),  # at the bound: plausible
    (STAY_A, HR, "2150-01-01 13:00:00", -5.0, "bpm"),  # below the bound: implausible
    (STAY_A, HR, "2150-01-02 07:59:00", 95.0, "bpm"),  # 23.98 h: still day 0, last
    (STAY_A, HR, "2150-01-02 08:00:00", 100.0, "bpm"),  # exactly 24 h: day 1, first
    (STAY_A, HR, "2150-01-02 09:00:00", 75.0, "bpm"),  # day 1 tie at the latest time
    (STAY_A, HR, "2150-01-02 09:00:00", 70.0, "bpm"),
    (STAY_A, HR, "2150-01-03 10:00:00", 65.0, "bpm"),  # day 2 tie at the earliest time
    (STAY_A, HR, "2150-01-03 10:00:00", 60.0, "bpm"),
    (STAY_A, HR, "2150-01-03 21:00:00", 88.0, "bpm"),  # after outtime: charted, kept
    (STAY_A, TEMP_F, "2150-01-01 09:00:00", 98.6, "deg F"),  # accepted non-canonical unit
    (STAY_A, TEMP_F, "2150-01-01 12:00:00", 37.5, "degC"),  # the canonical unit
    (STAY_A, TEMP_F, "2150-01-01 15:00:00", 310.15, "K"),  # unaccepted: passes, implausible
    (STAY_A, 220046, "2150-01-01 09:00:00", 120.0, "bpm"),  # not curated: ignored
]
LABS: list[tuple[int, int, str, float | None, str | None]] = [
    # (subject_id, itemid, charttime, valuenum, valueuom)
    (SUBJ_A, CREATININE, "2150-01-01 10:00:00", 88.42, "umol/L"),  # -> 1.0 mg/dL, day 0
    (SUBJ_A, CREATININE, "2150-01-02 12:00:00", 1.2, "mg/dL"),  # day 1
    (SUBJ_A, CREATININE, "2150-01-01 06:00:00", 1.1, "mg/dL"),  # before intime: dropped
    (SUBJ_A, CREATININE, "2150-01-03 20:00:00", 1.3, "mg/dL"),  # at outtime: dropped
    (SUBJ_A, CREATININE, "2150-01-05 00:00:00", 1.4, "mg/dL"),  # after outtime: dropped
    (SUBJ_B, CREATININE, "2150-02-01 06:00:00", 0.8, "mg/dL"),  # stay B, day 0
    (SUBJ_C, CREATININE, "2150-03-01 00:00:00", 0.9, "mg/dL"),  # no stay at all: dropped
]


def _crafted_ctx(settings: Settings, con: duckdb.DuckDBPyConnection) -> Any:
    from mimicwarehouse.dag.runner import StepContext, new_build_id

    lake_root = settings.lake_root("fixture")
    lake_root.mkdir(parents=True, exist_ok=True)
    return StepContext(
        settings=settings,
        tier="fixture",
        build_id=new_build_id("fixture"),
        con=con,
        log=logging.getLogger("test_ep175"),
        raw_root=settings.data_root,
        lake_root=lake_root,
        buckets=None,
    )


def _crafted_tables(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("CREATE SCHEMA mimiciv_icu")
    con.execute("CREATE SCHEMA mimiciv_hosp")
    con.execute(
        "CREATE TABLE mimiciv_icu.icustays (subject_id INTEGER, hadm_id INTEGER, "
        "stay_id INTEGER, intime TIMESTAMP, outtime TIMESTAMP)"
    )
    con.executemany(
        "INSERT INTO mimiciv_icu.icustays VALUES (?, ?, ?, ?, ?)",
        [
            [SUBJ_A, 92000001, STAY_A, IN_A, OUT_A],
            [SUBJ_B, 92000002, STAY_B, IN_B, OUT_B],
        ],
    )
    con.execute(
        "CREATE TABLE mimiciv_icu.chartevents (subject_id INTEGER, stay_id INTEGER, "
        "itemid INTEGER, charttime TIMESTAMP, valuenum DOUBLE, valueuom VARCHAR)"
    )
    con.executemany(
        "INSERT INTO mimiciv_icu.chartevents VALUES (?, ?, ?, ?, ?, ?)",
        [[SUBJ_A, *row] for row in CHART],
    )
    con.execute(
        "CREATE TABLE mimiciv_hosp.labevents (subject_id INTEGER, itemid INTEGER, "
        "charttime TIMESTAMP, valuenum DOUBLE, valueuom VARCHAR)"
    )
    con.executemany("INSERT INTO mimiciv_hosp.labevents VALUES (?, ?, ?, ?, ?)", LABS)


def _rows(con: duckdb.DuckDBPyConnection, path: str, columns: str) -> list[tuple[Any, ...]]:
    # hive_partitioning off, as the catalog view reads: subject_bucket lives inside the file
    return con.execute(
        f"SELECT {columns} FROM read_parquet('{path}', hive_partitioning = false)"
    ).fetchall()


def test_crafted_lake_through_both_steps(tmp_path: Path) -> None:
    from mimicwarehouse.dag import benchmarks
    from mimicwarehouse.loader.manifest import read_status

    settings = config.Settings(data_root=tmp_path / "root")
    con = duckdb.connect()
    try:
        _crafted_tables(con)
        ctx = _crafted_ctx(settings, con)
        dag = load_dag()
        daily_out = ir.run_itemid_daily(dag.step(ir.STEP_DAILY), ctx)
        assert daily_out.rows == 7 and daily_out.layer == "marts"
        lake_root = ctx.lake_root
        daily_dir = registry.mart_dir(lake_root, "fixture", ir.SPEC_DAILY)
        files = registry.mart_files(daily_dir, partitioned=True)
        assert [f.parent.name for f in files] == ["subject_bucket=1", "subject_bucket=2"]
        glob = registry.mart_glob(daily_dir, partitioned=True)
        described = [
            (str(r[0]), str(r[1]))
            for r in con.execute(
                f"DESCRIBE SELECT * FROM read_parquet('{glob}', hive_partitioning = false)"
            ).fetchall()
        ]
        assert described == list(DAILY_COLUMNS), "the declared contract"
        got = {
            (r[0], r[1], r[2]): r[3:]
            for r in _rows(
                con,
                glob,
                'stay_id, itemid, icu_day, n, "min", "max", "mean", "first", "last", n_implausible',
            )
        }
        assert set(got) == {
            (STAY_A, HR, 0),
            (STAY_A, HR, 1),
            (STAY_A, HR, 2),
            (STAY_A, TEMP_F, 0),
            (STAY_A, CREATININE, 0),
            (STAY_A, CREATININE, 1),
            (STAY_B, CREATININE, 0),
        }, "no day < 0, no uncurated item, labs outside every stay window dropped"
        lo, hi = units.bounds(HR)
        assert (lo, hi) == (0.0, 300.0), "the crafted values straddle these bounds"
        n, vmin, vmax, mean, first, last, n_bad = got[(STAY_A, HR, 0)]
        assert (n, n_bad) == (4, 2) and n + n_bad == 6, "the six valued rows of day 0"
        assert (vmin, vmax, first, last) == (80.0, 300.0, 80.0, 95.0)
        assert mean == pytest.approx((80 + 90 + 300 + 95) / 4)
        assert got[(STAY_A, HR, 1)][:1] + got[(STAY_A, HR, 1)][4:6] == (3, 100.0, 75.0), (
            "24 h is day 1; the tie at the latest time takes the highest value"
        )
        assert got[(STAY_A, HR, 2)][:1] + got[(STAY_A, HR, 2)][4:6] == (3, 60.0, 88.0), (
            "the tie at the earliest time takes the lowest; post-outtime charting is kept"
        )
        t_lo, t_hi = units.bounds(TEMP_F)
        assert t_lo <= 37.0 <= t_hi and not t_lo <= 310.15 <= t_hi
        n, vmin, vmax, mean, _first, _last, n_bad = got[(STAY_A, TEMP_F, 0)]
        assert (n, n_bad) == (2, 1), "degF converted and kept; an unaccepted unit implausible"
        assert vmin == pytest.approx(37.0) and vmax == pytest.approx(37.5)
        assert mean == pytest.approx(37.25)
        assert got[(STAY_A, CREATININE, 0)][1] == pytest.approx(1.0), "umol/L -> mg/dL"
        assert got[(STAY_A, CREATININE, 1)][1] == pytest.approx(1.2)
        assert got[(STAY_B, CREATININE, 0)][:2] == (1, pytest.approx(0.8))
        assert all(r[0] + r[-1] >= 1 for r in got.values())
        # n + n_implausible = the raw count of valued rows inside the windows, per stay-day
        expected: dict[tuple[int, int, int], int] = {}
        anchors = {STAY_A: (IN_A, OUT_A, SUBJ_A), STAY_B: (IN_B, OUT_B, SUBJ_B)}
        curated = set(_ids("chartevents")) | set(_ids("labevents"))

        def day_of(intime: str, t: str) -> int:
            delta = datetime.fromisoformat(t) - datetime.fromisoformat(intime)
            return int(delta.total_seconds() // 86400)

        for stay, itemid, t, value, _u in CHART:
            if value is not None and itemid in curated and day_of(anchors[stay][0], t) >= 0:
                key = (stay, itemid, day_of(anchors[stay][0], t))
                expected[key] = expected.get(key, 0) + 1
        for subject, itemid, t, value, _u in LABS:
            for stay, (intime, outtime, subj) in anchors.items():
                if subj == subject and value is not None and intime <= t < outtime:
                    key = (stay, itemid, day_of(intime, t))
                    expected[key] = expected.get(key, 0) + 1
        assert {key: r[0] + r[-1] for key, r in got.items()} == expected

        # the summary through its handler: raw aggregate -> release at k -> writer
        summary_out = ir.run_itemid_summary(dag.step(ir.STEP_SUMMARY), ctx)
        n_items = len(ir.rollup_items())
        assert summary_out.rows == n_items
        summary_dir = registry.mart_dir(lake_root, "fixture", ir.SPEC_SUMMARY)
        part = summary_dir / registry.PART
        described = [
            (str(r[0]), str(r[1]))
            for r in con.execute(
                f"DESCRIBE SELECT * FROM read_parquet('{part.as_posix()}')"
            ).fetchall()
        ]
        assert described == list(SUMMARY_COLUMNS)
        published = {
            r[0]: r[1:]
            for r in _rows(
                con,
                part.as_posix(),
                "itemid, n_stays, n_measurements, pct_stays_measured, p50, suppressed, k",
            )
        }
        assert sorted(published) == [i.itemid for i in ir.rollup_items()]
        for itemid in (HR, TEMP_F, CREATININE):
            assert published[itemid] == (None, None, None, None, True, K), "small: blanked"
        assert all(r[-1] == K for r in published.values())
        assert all(
            r[0] in (0, None) for i, r in published.items() if i not in (HR, TEMP_F, CREATININE)
        )
        assert disclose.check(part).passed, "the published file passes the release gate"
        entry = read_status(lake_root)["steps"][ir.summary_spec().status_key]
        assert entry["tiers"]["fixture"]["status"] == "done"
        ledger = benchmarks.read(settings)
        steps = set(ledger.filter(ledger["kind"] == "mart")["step"].to_list())
        assert {ir.STEP_DAILY, ir.STEP_SUMMARY} <= steps
        query = ledger.filter(ledger["kind"] == "query")
        assert query["step"].to_list() == [ir.SUMMARY_BENCH_STEP] and query["ok"][0]

        # the raw aggregate itself (unsuppressed, in-process only) and the release at k = 1
        raw = ir.compute_summary(con)
        row = raw.filter(pl.col("itemid") == HR).row(0, named=True)
        assert (row["n_stays"], row["n_measurements"]) == (1, 10)
        assert row["pct_stays_measured"] == pytest.approx(50.0), "1 of the 2 ICU stays"
        assert row["p50"] == pytest.approx(84.0), "median of the ten plausible HR values"
        creat = raw.filter(pl.col("itemid") == CREATININE).row(0, named=True)
        assert (creat["n_stays"], creat["n_measurements"]) == (2, 3)
        assert creat["pct_stays_measured"] == pytest.approx(100.0)
        unmeasured = raw.filter(~pl.col("itemid").is_in([HR, TEMP_F, CREATININE]))
        assert unmeasured["n_stays"].to_list() == [0] * (n_items - 3)
        assert unmeasured["p50"].null_count() == n_items - 3
        open_release = ir.release_summary(raw, 1)
        assert not open_release["suppressed"].any()
        assert open_release.drop("suppressed", "k").equals(raw)
    finally:
        con.close()


def test_a_disagreeing_catalogue_refuses_the_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mimicwarehouse.loader.manifest import read_status

    settings = config.Settings(data_root=tmp_path / "root")
    con = duckdb.connect()
    try:
        ctx = _crafted_ctx(settings, con)
        bad = ir.SourceRefCheck((), (), ("itemid 1: not found in its source_ref x.sql",))
        monkeypatch.setattr(ir, "check_source_refs", lambda *a, **k: bad)
        with pytest.raises(MartBuildError, match="disagrees with the vendored concepts"):
            ir.run_itemid_daily(load_dag().step(ir.STEP_DAILY), ctx)
        monkeypatch.undo()
        with pytest.raises(MartBuildError, match="not staged for tier fixture"):
            ir.run_itemid_summary(load_dag().step(ir.STEP_SUMMARY), ctx)
        steps = read_status(ctx.lake_root)["steps"]
        for spec in (ir.daily_spec(), ir.summary_spec()):
            attempt = steps[spec.status_key]["tiers"]["fixture"]
            assert attempt["status"] == "failed" and attempt["error_class"] == "MartBuildError"
    finally:
        con.close()


def test_release_blanks_beside_hidden_counts() -> None:
    # synthetic aggregates: a small item (1), an unmeasured one (2), three large ones, and
    # one (4) whose two counts differ by less than k (a nested total backing a cell out)
    raw = pl.DataFrame(
        {
            "itemid": [1, 2, 3, 4, 5, 6],
            "label": ["a", "b", "c", "d", "e", "f"],
            "source_table": ["chartevents"] * 6,
            "unit": ["u"] * 6,
            "n_stays": [5, 0, 400, 50, 900, 30],
            "n_measurements": [9, 0, 4000, 55, 9000, 300],
            "pct_stays_measured": [0.5, 0.0, 40.0, 5.0, 90.0, 3.0],
            "p01": [1.0, None, 1.0, 1.0, 1.0, 1.0],
            "p50": [2.0, None, 2.0, 2.0, 2.0, 2.0],
            "p99": [3.0, None, 3.0, 3.0, 3.0, 3.0],
        }
    )
    out = ir.release_summary(raw, K)
    assert out.columns == [*raw.columns, "suppressed", "k"]
    flags = dict(zip(out["itemid"].to_list(), out["suppressed"].to_list(), strict=True))
    assert flags[1], "a small n_stays is withheld"
    assert flags[4], "55 - 50 = 5 < k: the nested counts would back a small cell out"
    assert not flags[2], "a zero is not a small cell"
    assert not flags[3] and not flags[5], "large rows survive"
    for row in out.iter_rows(named=True):
        if row["suppressed"]:
            assert all(row[c] is None for c in ir.SUMMARY_BLANKED), row["itemid"]
        else:
            original = raw.filter(pl.col("itemid") == row["itemid"]).row(0, named=True)
            assert all(row[c] == original[c] for c in raw.columns)
    assert set(out["k"].to_list()) == {K}
    findings = disclose.check_frame(out.drop("suppressed", "k"))
    assert all(f.status != disclose.FAIL for f in findings), [f.code for f in findings]


# ---------------------------------------------------------------------------
# 3. The session fixture lake (conftest: the whole DAG, the catalog)
# ---------------------------------------------------------------------------


def test_session_lake_marts(
    fixture_lake_catalog: duckdb.DuckDBPyConnection, fixture_lake_settings: Settings
) -> None:
    con = fixture_lake_catalog
    for name, columns in ((ir.SPEC_DAILY, DAILY_COLUMNS), (ir.SPEC_SUMMARY, SUMMARY_COLUMNS)):
        described = [
            (str(r[0]), str(r[1])) for r in con.execute(f"DESCRIBE marts.{name}").fetchall()
        ]
        assert described == list(columns), name
    chart, labs = _id_list(_ids("chartevents")), _id_list(_ids("labevents"))
    row = con.execute(
        "SELECT count(*), count(*) FILTER (WHERE icu_day < 0), "
        "count(*) FILTER (WHERE n + n_implausible < 1), "
        "count(*) FILTER (WHERE subject_bucket <> subject_id % 100), "
        'count(*) FILTER (WHERE n > 0 AND NOT ("min" <= "first" AND "first" <= "max" '
        'AND "min" <= "last" AND "last" <= "max")), '
        'count(*) FILTER (WHERE n = 0 AND "mean" IS NOT NULL), '
        f"count(*) FILTER (WHERE itemid NOT IN ({chart}, {labs})), "
        "count(*) FILTER (WHERE stay_id NOT IN (SELECT stay_id FROM mimiciv_icu.icustays)) "
        "FROM marts.itemid_daily"
    ).fetchone()
    assert row is not None
    n_rows, neg, empty, bucket_bad, order_bad, stat_without_n, uncurated, orphan = row
    assert n_rows > 0
    assert (neg, empty, bucket_bad, order_bad, stat_without_n, uncurated, orphan) == (0,) * 7
    # n + n_implausible reproduces the raw count of valued rows, per source
    raw_chart = con.execute(
        "SELECT count(*) FROM mimiciv_icu.chartevents AS c JOIN mimiciv_icu.icustays AS i "
        f"ON i.stay_id = c.stay_id WHERE c.itemid IN ({chart}) AND c.valuenum IS NOT NULL "
        "AND c.charttime >= i.intime"
    ).fetchone()
    raw_labs = con.execute(
        "SELECT count(*) FROM mimiciv_hosp.labevents AS l JOIN mimiciv_icu.icustays AS i "
        "ON i.subject_id = l.subject_id AND l.charttime >= i.intime AND l.charttime < i.outtime "
        f"WHERE l.itemid IN ({labs}) AND l.valuenum IS NOT NULL"
    ).fetchone()
    rolled = dict(
        con.execute(
            f"SELECT itemid IN ({chart}) AS chart, sum(n + n_implausible) FROM marts.itemid_daily "
            "GROUP BY 1"
        ).fetchall()
    )
    assert raw_chart is not None and raw_labs is not None
    assert raw_chart[0] > 0 and raw_labs[0] > 0, "the fixture charts curated vitals and labs"
    assert rolled == {True: raw_chart[0], False: raw_labs[0]}
    # one summary row per rolled-up itemid; the unsuppressed ones agree with the daily mart
    summary = {
        r[0]: r[1:]
        for r in con.execute(
            "SELECT itemid, n_stays, n_measurements, suppressed, k, label, source_table, unit "
            "FROM marts.itemid_summary"
        ).fetchall()
    }
    items = {i.itemid: i for i in ir.rollup_items()}
    assert sorted(summary) == sorted(items)
    daily = {
        r[0]: r[1:]
        for r in con.execute(
            "SELECT itemid, sum(n), count(DISTINCT stay_id) FILTER (WHERE n > 0) "
            "FROM marts.itemid_daily GROUP BY itemid"
        ).fetchall()
    }
    k = fixture_lake_settings.k_suppression
    for itemid, (
        n_stays,
        n_measurements,
        suppressed,
        row_k,
        label,
        source,
        unit,
    ) in summary.items():
        assert row_k == k and (label, source, unit) == (
            items[itemid].label,
            items[itemid].source,
            items[itemid].canonical_unit,
        )
        if suppressed:
            assert n_stays is None and n_measurements is None
            continue
        assert n_stays == 0 or n_stays >= k, "nothing below k survives the build"
        measured, stays = daily.get(itemid, (0, 0))
        assert (n_measurements, n_stays) == (measured, stays), itemid
    # the registry tables list the three marts, built and current (EP-56 adds three more)
    tables = con.execute(
        "SELECT mart, built, stale, n_columns FROM meta.mart_tables "
        "WHERE mart IN ('icustay_first_day', 'itemid_daily', 'itemid_summary') ORDER BY mart"
    ).fetchall()
    assert tables == [
        (
            "icustay_first_day",
            True,
            False,
            len(registry.load_registry().spec("icustay_first_day").expanded),
        ),
        (ir.SPEC_DAILY, True, False, len(DAILY_COLUMNS)),
        (ir.SPEC_SUMMARY, True, False, len(SUMMARY_COLUMNS)),
    ]
    grains = dict(con.execute("SELECT mart, grain FROM meta.mart_tables").fetchall())
    assert grains[ir.SPEC_DAILY] == "icu_day" and grains[ir.SPEC_SUMMARY] == "itemid"
    keys = {
        r[0]
        for r in con.execute(
            'SELECT "column" FROM meta.mart_columns WHERE mart = ? AND is_key', [ir.SPEC_DAILY]
        ).fetchall()
    }
    assert keys == {"subject_id", "stay_id", "itemid", "icu_day", "subject_bucket"}
    # the published summary passes the release gate as built
    lake_root = fixture_lake_settings.lake_root("fixture")
    part = registry.mart_dir(lake_root, "fixture", ir.SPEC_SUMMARY) / registry.PART
    assert disclose.check(part).passed
    manifest = registry.read_mart_manifest(lake_root, "fixture", ir.SPEC_DAILY)
    assert manifest is not None and set(manifest["snapshot_ids"]) == {"core"}


def test_session_lake_safe_query_and_dictionary(
    fixture_lake_catalog: duckdb.DuckDBPyConnection, fixture_lake_settings: Settings
) -> None:
    from mimicwarehouse.catalog.dictionary import render_dictionary
    from mimicwarehouse.safe import SafeQueryRefused, safe_query

    def run(sql: str) -> Any:
        return safe_query(sql, tier="fixture", settings=fixture_lake_settings, actor="test_ep175")

    picker = run("SELECT itemid, n_stays FROM marts.itemid_summary")
    assert picker.n_rows == len(ir.rollup_items()), "a registry read: no count, no refusal"
    count = run("SELECT count(*) AS n FROM marts.itemid_daily")
    assert count.n_rows == 1 and int(count.df["n"][0]) > 0
    with pytest.raises(SafeQueryRefused):
        run("SELECT stay_id, n FROM marts.itemid_daily")  # an identifier as output
    with pytest.raises(SafeQueryRefused):
        run("SELECT itemid, n FROM marts.itemid_daily")  # subject-keyed: needs a count
    built = run("SELECT mart, built FROM meta.mart_tables").df.rows()
    assert {(ir.SPEC_DAILY, True), (ir.SPEC_SUMMARY, True)} <= set(built)
    text, info = render_dictionary(fixture_lake_catalog)
    for spec in (ir.daily_spec(), ir.summary_spec()):
        assert f"\n### marts.{spec.name}\n" in text
        section = text.split(f"### marts.{spec.name}", 1)[1].split("\n### ", 1)[0]
        for column in spec.column_names:
            assert f"\n| {column} |" in section, (spec.name, column)
    assert info.marts == len(registry.load_registry())


# ---------------------------------------------------------------------------
# 4. The dev tier (aggregates through safe_query only)
# ---------------------------------------------------------------------------


@pytest.mark.tier("dev")
def test_dev_marts_through_safe_query(dev_catalog: Path) -> None:
    from mimicwarehouse.safe import safe_query

    settings = config.load_settings()
    remedy = "run `mwh build --tier dev --tag marts` (EP-175) first"

    def run(sql: str) -> Any:
        return safe_query(sql, tier="dev", settings=settings, actor="test_ep175")

    built = run("SELECT mart, built, stale FROM meta.mart_tables").df.rows()
    for name in (ir.SPEC_DAILY, ir.SPEC_SUMMARY):
        assert (name, True, False) in built, remedy
    checks = run(
        "SELECT count(*) AS n, count(*) FILTER (WHERE icu_day < 0) AS n_negative_day, "
        "count(*) FILTER (WHERE n + n_implausible < 1) AS n_empty, "
        "count(DISTINCT itemid) AS n_items FROM marts.itemid_daily"
    )
    assert checks.n_rows == 1, "a row was withheld (a small cell) or missing"
    row = checks.df.row(0, named=True)
    assert row["n"] > 0 and row["n_negative_day"] == 0 and row["n_empty"] == 0
    assert row["n_items"] <= len(ir.rollup_items())
    picker = run("SELECT itemid, n_stays FROM marts.itemid_summary")
    assert picker.n_rows == len(ir.rollup_items()), "one row per rolled-up itemid, no refusal"
    print(f"dev: marts.itemid_daily {row['n']:,} rows over {row['n_items']} itemid(s)")


@pytest.mark.tier("dev")
def test_dev_build_wall_time_from_the_ledger(dev_catalog: Path) -> None:
    from mimicwarehouse.dag import benchmarks

    settings = config.load_settings()
    ledger = benchmarks.read(settings)
    dev_ok = ledger.filter((ledger["tier"] == "dev") & ledger["ok"])
    for kind, step in (
        ("mart", ir.STEP_DAILY),
        ("mart", ir.STEP_SUMMARY),
        ("query", ir.SUMMARY_BENCH_STEP),
    ):
        lines = dev_ok.filter((dev_ok["kind"] == kind) & (dev_ok["step"] == step))
        assert lines.height >= 1, f"run `mwh build --tier dev --tag marts` (EP-175) first: {step}"
        wall = float(lines["wall_s"][-1])
        assert wall < 600.0, f"{step} took {wall:.1f} s on dev (target < 10 min)"
        print(f"dev: {step} ({kind}) wall {wall:.1f} s")


# ---------------------------------------------------------------------------
# 5. The safe-query audit gap found at EP-175 (owner decision: fix now, own commit)
# ---------------------------------------------------------------------------


def test_a_marker_named_column_is_an_audited_refusal(
    fixture_lake_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mimicwarehouse import fsio
    from mimicwarehouse.safe import SafeQueryRefused, audit_path, safe_query

    def run(sql: str) -> Any:
        return safe_query(sql, tier="fixture", settings=fixture_lake_settings, actor="test_ep175")

    def audit_lines() -> list[dict[str, Any]]:
        return fsio.read_jsonl(audit_path(fixture_lake_settings))

    # before EP-175 this raised inside the suppressor after execution, with no audit line
    before = len(audit_lines())
    with pytest.raises(SafeQueryRefused, match="collides with the small-cell marker"):
        run(
            "SELECT count(*) AS n, count(*) FILTER (WHERE n_implausible > 0) AS n_suppressed "
            "FROM marts.itemid_daily"
        )
    lines = audit_lines()
    assert len(lines) == before + 1
    assert lines[-1]["allowed"] is False and "collides" in lines[-1]["refusal_reason"]
    with pytest.raises(SafeQueryRefused, match="collides"):
        run("SELECT count(*) AS N, count(*) AS N_SUPPRESSED FROM marts.itemid_daily")
    # a registry read may still carry a *_suppressed column: no count column, no marker
    assert run("SELECT mart, rows_suppressed FROM meta.mart_tables").n_rows >= 3
    assert run("SELECT count(*) AS n, count(*) AS rows_withheld FROM marts.itemid_daily").n_rows

    # any other failure inside the hook: an audited refusal, its error text sanitised
    def broken(df: Any, k: int, count_columns: list[str]) -> Any:
        raise RuntimeError("synthetic hook failure 4242")

    monkeypatch.setattr(safe, "SUPPRESSOR", broken)
    before = len(audit_lines())
    with pytest.raises(SafeQueryRefused, match="suppression error: RuntimeError"):
        run("SELECT count(*) AS n FROM marts.itemid_daily")
    lines = audit_lines()
    assert len(lines) == before + 1 and lines[-1]["allowed"] is False
    assert "4242" not in lines[-1]["refusal_reason"], "numbers are masked (DKB-2)"


def test_docstrings_name_the_rollups() -> None:
    import mimicwarehouse.marts as marts_pkg

    doc = marts_pkg.__doc__ or ""
    assert "itemid_rollups" in doc and "EP-175" in doc
    rollups = ir.__doc__ or ""
    for phrase in ("safe.REGISTRY_TABLES", "harmonised_value_sql", "n_implausible", "source_ref"):
        assert phrase in rollups, phrase
    assert "SUMMARY_GRAINS" in (registry.__doc__ or "")
