"""EP-45 — measurement-process summaries (``mimicwarehouse.qc.measurement``).

Fixture tier (default): the DAG spec is wired (the four python steps, the shared
``catalog`` step, ``params`` on python steps); the params model; the SQL builders cite
``timesem``; **crafted synthetic stays** in an in-memory DuckDB (ids >= 90 000 000): hourly
counts sum to the total measurements, ``n_stays_at_risk`` decreases at ``outtime``, a
unit x era cell with zero measurements and >= 50 stays is ``structural`` while a small
empty cell is not, the first-24 h absence splits into structural / unmeasured with the
expected counts, the rate ratio matches a hand computation (Wald CI on the log scale),
the presence SQL is admitted by ``safe_query``; the assembled tables are k-suppressed
(complementary, the nested pairs, the cross-contrast rule, blanked statistics and
flags) and pass ``disclose.check_frame``; the report renders from crafted frames and
passes ``disclose.check``; the session fixture lake carries the six ``meta.mp_*``
tables, the ``kind: qc`` run, the ``kind: query`` benchmark lines and a report that
passes the gate; a stale slice refuses the report step; ``mwh qc measurement``; the docs
page; the import budget. ``tier("dev")``: the real catalog's tables for the default
itemid set through ``safe_query`` (every curated itemid present, nothing below k
released).

Everything asserted or printed is counts, shares, quantiles, dictionary text and crafted
synthetic values — never a row.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import polars as pl
import pytest
import yaml

import helpers
from mimicwarehouse import config, disclose, timesem, units
from mimicwarehouse import run as run_mod
from mimicwarehouse.catalog.build import CATALOG_EXTENSIONS
from mimicwarehouse.cli import app
from mimicwarehouse.config import Settings
from mimicwarehouse.dag import benchmarks
from mimicwarehouse.dag import runner as runner_mod
from mimicwarehouse.dag.spec import DagError, DagSpec, load_dag
from mimicwarehouse.qc import measurement as mp
from mimicwarehouse.qc import profile as qc
from mimicwarehouse.qc.cli import measurement_summary

pytestmark = pytest.mark.ep_45

K = 11
HR, CREATININE, FOLEY, WEIGHT = 220045, 50912, 226559, 226512
ITEMS = (CREATININE, HR, WEIGHT, FOLEY)
BAND_TOKEN = re.compile(r"(?<![\w.])[123]\d{7}(?![\w.])")
#: The crafted population (ids >= 90 000 000): unit A holds 60 + 12 stays, unit B 55 —
#: no unit x era cell below k, so the structural grid needs no suppression of its own
#: (the small-cell behaviour of the grid has its own crafted-frame test).
UNIT_A, UNIT_B = "Medical Intensive Care Unit (MICU)", "Neuro Stepdown"
ERA_1, ERA_2 = timesem.ERAS[0], timesem.ERAS[1]
T0 = datetime(2150, 1, 1, 8, 0)
SUBJECT0, HADM0, STAY0 = 90_500_000, 91_500_000, 92_500_000
N_A1, N_B1, N_A2 = 60, 55, 12
N_POP = N_A1 + N_B1 + N_A2
#: The admission weight is charted once, for the first N_WEIGHT stays (unit A / era 1):
#: an item measured in far fewer stays than it is missing from, which breaks the
#: coincidental orderings a three-item frame would show the nested-total heuristic.
N_WEIGHT = 12
#: Stays of unit A / era 1 whose HR starts only at hour 30 (missing in the first 24 h);
#: they stay 40 h so the late charting exists.
LATE_HR = 3
LATE_LOS = 40
#: Stays with a 10 h length of stay (the rest stay 30 h).
SHORT = 7
#: Creatinine in the first 24 h: 80 measured stays (20 deaths), the rest unmeasured
#: (5 deaths); 50 of the measured stays carry 1-2 draws, 30 carry 3.
N_MEAS, D_MEAS, D_NOT, N_LOW = 80, 20, 5, 50
N_NOT = N_POP - N_MEAS


@pytest.fixture(scope="module")
def catalogue() -> units.ItemCatalogue:
    return units.load_catalogue()


@pytest.fixture(scope="module")
def crafted() -> Iterator[duckdb.DuckDBPyConnection]:
    """An in-memory catalog of crafted stays (module docstring)."""
    con = duckdb.connect(":memory:")  # in-memory crafted rows only; no data root involved
    for schema in ("mimiciv_hosp", "mimiciv_icu"):
        con.execute(f"CREATE SCHEMA {schema}")
    con.execute(
        "CREATE TABLE mimiciv_hosp.patients (subject_id INTEGER, anchor_year_group VARCHAR)"
    )
    con.execute(
        "CREATE TABLE mimiciv_hosp.admissions (subject_id INTEGER, hadm_id INTEGER, "
        "hospital_expire_flag SMALLINT)"
    )
    con.execute(
        "CREATE TABLE mimiciv_icu.icustays (subject_id INTEGER, hadm_id INTEGER, stay_id INTEGER, "
        "first_careunit VARCHAR, intime TIMESTAMP, outtime TIMESTAMP)"
    )
    con.execute(
        "CREATE TABLE mimiciv_icu.chartevents (subject_id INTEGER, hadm_id INTEGER, "
        "stay_id INTEGER, itemid INTEGER, charttime TIMESTAMP)"
    )
    con.execute(
        "CREATE TABLE mimiciv_hosp.labevents (subject_id INTEGER, hadm_id INTEGER, "
        "itemid INTEGER, charttime TIMESTAMP)"
    )
    con.execute(
        "CREATE TABLE mimiciv_icu.outputevents (subject_id INTEGER, stay_id INTEGER, "
        "itemid INTEGER, charttime TIMESTAMP)"
    )
    con.execute(
        "CREATE TABLE mimiciv_icu.inputevents (subject_id INTEGER, stay_id INTEGER, "
        "itemid INTEGER, starttime TIMESTAMP)"
    )
    cells = [(UNIT_A, ERA_1)] * N_A1 + [(UNIT_B, ERA_1)] * N_B1 + [(UNIT_A, ERA_2)] * N_A2
    patients: list[tuple[int, str]] = []
    admissions: list[tuple[int, int, int]] = []
    stays: list[tuple[int, int, int, str, datetime, datetime]] = []
    charts: list[tuple[int, int, int, int, datetime]] = []
    labs: list[tuple[int, int, int, datetime]] = []
    outputs: list[tuple[int, int, int, datetime]] = []
    for i, (unit, era) in enumerate(cells):
        subject, hadm, stay = SUBJECT0 + i, HADM0 + i, STAY0 + i
        intime = T0 + timedelta(hours=3 * i)
        late = era == ERA_1 and N_A1 - LATE_HR <= i < N_A1
        los_h = 10 if i < SHORT else (LATE_LOS if late else 30)
        outtime = intime + timedelta(hours=los_h)
        # deaths: the first D_MEAS of the creatinine-measured stays and the first D_NOT of
        # the unmeasured ones (indices are assigned below)
        measured = i < N_MEAS
        died = (measured and i < D_MEAS) or (not measured and N_MEAS <= i < N_MEAS + D_NOT)
        patients.append((subject, era))
        admissions.append((subject, hadm, 1 if died else 0))
        stays.append((subject, hadm, stay, unit, intime, outtime))
        if unit == UNIT_A:
            # HR every hour on the half hour; the LATE_HR stays of era 1 start at hour 30
            # -> measured during the stay, missing in the first 24 h
            first_hour = 30 if late else 0
            for h in range(first_hour, los_h):
                t = intime + timedelta(hours=h, minutes=30)
                if t < outtime:
                    charts.append((subject, hadm, stay, HR, t))
            # a duplicate row of the first HR (chartevents' upstream duplicates count once)
            if not late:
                charts.append((subject, hadm, stay, HR, intime + timedelta(minutes=30)))
            # a Foley volume every 2 h from hour 1 (outputevents, the third source)
            for h in range(1, los_h, 2):
                outputs.append((subject, stay, FOLEY, intime + timedelta(hours=h)))
            # the admission weight once, shortly after arrival, for the first stays only
            if i < N_WEIGHT:
                charts.append((subject, hadm, stay, WEIGHT, intime + timedelta(minutes=6)))
        if measured:
            n_draws = 2 if i < N_LOW else 3
            for j in range(n_draws):
                labs.append((subject, hadm, CREATININE, intime + timedelta(hours=2 + 5 * j)))
        else:
            # a pre-ICU draw (never a measurement of the stay) and one after outtime
            labs.append((subject, hadm, CREATININE, intime - timedelta(hours=2)))
            labs.append((subject, hadm, CREATININE, outtime + timedelta(hours=1)))
    # an invalid stay (outtime before intime) that the population must exclude
    stays.append((SUBJECT0, HADM0, STAY0 + 10_000, UNIT_A, T0, T0 - timedelta(hours=1)))
    con.executemany("INSERT INTO mimiciv_hosp.patients VALUES (?, ?)", patients)
    con.executemany("INSERT INTO mimiciv_hosp.admissions VALUES (?, ?, ?)", admissions)
    con.executemany("INSERT INTO mimiciv_icu.icustays VALUES (?, ?, ?, ?, ?, ?)", stays)
    con.executemany("INSERT INTO mimiciv_icu.chartevents VALUES (?, ?, ?, ?, ?)", charts)
    con.executemany("INSERT INTO mimiciv_hosp.labevents VALUES (?, ?, ?, ?)", labs)
    con.executemany("INSERT INTO mimiciv_icu.outputevents VALUES (?, ?, ?, ?)", outputs)
    yield con
    con.close()


def _params(**overrides: Any) -> mp.MeasurementParams:
    base: dict[str, Any] = {"itemids": list(ITEMS), "hours": 48, "days": 3}
    base.update(overrides)
    return mp.MeasurementParams.model_validate(base)


@pytest.fixture(scope="module")
def crafted_slices(
    crafted: duckdb.DuckDBPyConnection, catalogue: units.ItemCatalogue
) -> dict[str, mp.Slice]:
    params = _params()
    return {
        mp.STEP_HOURLY: mp.compute_hourly(crafted, params, catalogue=catalogue),
        mp.STEP_STRUCTURAL: mp.compute_structural(crafted, params, catalogue=catalogue),
        mp.STEP_PRESENCE: mp.compute_presence(crafted, params, catalogue=catalogue),
    }


def _rows(frame: pl.DataFrame, **where: Any) -> list[dict[str, Any]]:
    out = frame
    for column, value in where.items():
        out = out.filter(pl.col(column) == value)
    return out.to_dicts()


def _scalar(con: duckdb.DuckDBPyConnection, sql: str) -> Any:
    row = con.execute(sql).fetchone()
    return None if row is None else row[0]


# ---------------------------------------------------------------------------
# 1. Wiring: the spec, params on python steps, the catalog extension
# ---------------------------------------------------------------------------


def test_spec_wired_and_params_on_python_steps() -> None:
    dag = load_dag()
    names = {s.name for s in dag.steps}
    assert set(mp.COMPUTE_STEPS) | {mp.STEP_REPORT} <= names
    for name in mp.COMPUTE_STEPS:
        step = dag.step(name)
        assert step.kind == "python" and step.target is None and step.params is None
        assert set(step.tags) == {mp.DAG_TAG, "meta"}
        assert set(step.tiers) == {"fixture", "demo", "dev", "full"}
        assert {f"stage.{mp.STAYS_TABLE}", f"stage.{mp.PATIENTS_TABLE}"} <= set(step.depends_on)
    assert dag.step(mp.STEP_HOURLY).callable_name == "mimicwarehouse.qc.measurement:run_hourly"
    assert (
        dag.step(mp.STEP_STRUCTURAL).callable_name == "mimicwarehouse.qc.measurement:run_structural"
    )
    assert dag.step(mp.STEP_PRESENCE).callable_name == "mimicwarehouse.qc.measurement:run_presence"
    assert f"stage.{mp.ADMISSIONS_TABLE}" in dag.step(mp.STEP_PRESENCE).depends_on
    report = dag.step(mp.STEP_REPORT)
    assert report.callable_name == "mimicwarehouse.qc.measurement:run_report"
    assert set(report.depends_on) == set(mp.COMPUTE_STEPS)
    catalog = dag.step("catalog")
    assert mp.STEP_REPORT in catalog.depends_on and mp.DAG_TAG in catalog.tags
    ordered = [s.name for s in dag.ordered(tags=[mp.DAG_TAG], tier="fixture")]
    assert ordered[-1] == "catalog" and ordered[-2] == mp.STEP_REPORT
    assert all(ordered.index(n) < ordered.index(mp.STEP_REPORT) for n in mp.COMPUTE_STEPS)
    # the committed file: ASCII, LF, loads, no band-shaped integer
    path = mp.spec_path()
    raw = path.read_bytes()
    assert b"\r" not in raw and raw.decode("utf-8").isascii()
    assert not BAND_TOKEN.search(raw.decode("utf-8"))
    doc = yaml.safe_load(raw.decode("utf-8"))
    assert [s["name"] for s in doc["steps"]] == [*mp.COMPUTE_STEPS, mp.STEP_REPORT, "catalog"]
    # params: an optional mapping on python steps only, refused on the other kinds
    spec = DagSpec.model_validate(
        {
            "version": 1,
            "steps": [
                {
                    "name": "m",
                    "kind": "python",
                    "callable": "mimicwarehouse.qc.measurement:run_hourly",
                    "params": {"itemids": [HR], "hours": 24},
                }
            ],
        }
    )
    assert mp.params_of(spec.step("m")) == _params(itemids=[HR], hours=24, days=14)
    with pytest.raises(ValueError, match="belong to another kind"):
        DagSpec.model_validate(
            {"version": 1, "steps": [{"name": "c", "kind": "catalog", "params": {"x": 1}}]}
        )
    with pytest.raises(DagError):
        load_dag("nope")
    # the catalog extension sits after the qc extension; the EP-39 / EP-41 order pins hold
    assert mp.register_measurement in CATALOG_EXTENSIONS
    assert CATALOG_EXTENSIONS.index(mp.register_measurement) == (
        CATALOG_EXTENSIONS.index(qc.register_qc) + 1
    )
    assert CATALOG_EXTENSIONS[-1] is units.register_units
    assert mp.RUN_KIND in run_mod.RUN_KINDS and mp.BENCH_KIND in benchmarks.BENCHMARK_KINDS


def test_params_model_and_sql_builders(catalogue: units.ItemCatalogue) -> None:
    defaults = mp.MeasurementParams()
    assert (defaults.hours, defaults.days, defaults.first_window_hours) == (168, 14, 24)
    assert (defaults.min_stays, defaults.sparse_share) == (50, 0.05)
    assert defaults.resolve_itemids(catalogue) == catalogue.itemids()
    assert len(defaults.sha256()) == 64 and defaults.sha256() != _params().sha256()
    assert _params().resolve_itemids(catalogue) == ITEMS
    with pytest.raises(qc.QcError, match="not curated"):
        _params(itemids=[HR, 1]).resolve_itemids(catalogue)
    with pytest.raises(qc.QcError, match="validation error"):
        mp.params_of(
            DagSpec.model_validate(
                {
                    "version": 1,
                    "steps": [
                        {
                            "name": "m",
                            "kind": "python",
                            "callable": "a:b",
                            "params": {"hours": 0, "bogus": 1},
                        }
                    ],
                }
            ).step("m")
        )
    assert mp.params_of(None) == defaults
    # the builders: timesem's relative-time arithmetic, the [start, end) bins, the sources
    pop = mp.population_sql()
    assert timesem.sql_hours_since("i.intime", "i.outtime") in pop
    assert "i.outtime > i.intime" in pop and mp.UNKNOWN_LABEL in pop
    hourly = mp.binned_sql(168, 1.0, "hour_bin")
    assert timesem.sql_hour_bin("hours_since", 1.0) in hourly and "< 168" in hourly
    daily = mp.binned_sql(14, 24.0, "day_index")
    assert timesem.sql_hour_bin("hours_since", 24.0) in daily
    assert "count(DISTINCT stay_id)" in hourly and "count(*) AS n_measurements" in hourly
    labs = mp.occasions_sql("labevents", [CREATININE])
    assert 'e."subject_id" = p."subject_id"' in labs and "SELECT DISTINCT" in labs
    charts = mp.occasions_sql("chartevents", [HR])
    assert 'e."stay_id" = p."stay_id"' in charts and f"e.itemid IN ({HR})" in charts
    assert "range(0, 168)" in mp.at_risk_sql(168, 1.0, "hour_bin")
    assert "lag(charttime) OVER (PARTITION BY stay_id, itemid" in mp.intervals_sql()
    assert "hospital_expire_flag = 1" in mp.presence_sql([CREATININE], 24)
    assert "unnest([" in mp.presence_sql([CREATININE], 24)
    with pytest.raises(qc.QcError, match="at least one itemid"):
        mp.presence_sql([], 24)
    for source, (table, time_col, key) in mp.SOURCE_EVENTS.items():
        assert source in units.SOURCES and key in ("stay_id", "subject_id")
        assert table.startswith(("mimiciv_icu.", "mimiciv_hosp.")) and time_col
    assert mp.rate_ratio(20, 100, 5, 50) is not None and mp.rate_ratio(1, 0, 1, 10) is None


# ---------------------------------------------------------------------------
# 2. Crafted synthetic stays: the raw computations
# ---------------------------------------------------------------------------


def test_hourly_counts_sum_and_at_risk_decreases_at_outtime(
    crafted: duckdb.DuckDBPyConnection, crafted_slices: dict[str, mp.Slice]
) -> None:
    s = crafted_slices[mp.STEP_HOURLY]
    assert s.step == mp.STEP_HOURLY and s.meta["n_population"] == N_POP
    assert s.meta["n_excluded"] == 1, "the outtime < intime stay is excluded"
    assert s.meta["sources"] == ["chartevents", "labevents", "outputevents"]
    assert s.meta["itemids"] == list(ITEMS)
    hourly, daily, stats = s.frames["hourly"], s.frames["daily"], s.frames["stats"]
    assert hourly.height == len(ITEMS) * 48 and daily.height == len(ITEMS) * 3, "complete grids"
    assert list(hourly.columns) == [
        "itemid",
        "hour_bin",
        "n_stays_at_risk",
        "n_stays_measured",
        "n_measurements",
    ]
    # hourly counts sum to the total number of distinct measurement occasions
    distinct_hr = _scalar(
        crafted,
        f"SELECT count(*) FROM (SELECT DISTINCT stay_id, charttime FROM mimiciv_icu.chartevents "
        f"WHERE itemid = {HR})",
    )
    raw_hr = _scalar(crafted, f"SELECT count(*) FROM mimiciv_icu.chartevents WHERE itemid = {HR}")
    assert raw_hr > distinct_hr, "the crafted duplicate rows exist"
    hr = hourly.filter(pl.col("itemid") == HR)
    assert int(hr.get_column("n_measurements").sum()) == distinct_hr
    # every measured stay in a bin was at risk in it
    assert (hr.get_column("n_stays_measured") <= hr.get_column("n_stays_at_risk")).all()
    # n_stays_at_risk: every stay at hour 0, the SHORT stays leave at hour 10, the 30 h
    # stays at hour 30, the LATE_HR (40 h) stays at hour 40
    at_risk = dict(
        zip(
            hr.get_column("hour_bin").to_list(),
            hr.get_column("n_stays_at_risk").to_list(),
            strict=True,
        )
    )
    assert at_risk[0] == N_POP and at_risk[9] == N_POP
    assert at_risk[10] == N_POP - SHORT and at_risk[29] == N_POP - SHORT
    assert at_risk[30] == LATE_HR and at_risk[39] == LATE_HR and at_risk[40] == 0
    # HR: unit A stays are measured every hour (bin 0 = the non-late A stays)
    measured_bin0 = _rows(hr, hour_bin=0)[0]
    assert measured_bin0["n_stays_measured"] == N_A1 + N_A2 - LATE_HR
    assert measured_bin0["n_measurements"] == N_A1 + N_A2 - LATE_HR, "the duplicate counts once"
    assert _rows(hr, hour_bin=30)[0]["n_stays_measured"] == LATE_HR, "the late stays only"
    assert _rows(hr, hour_bin=40)[0]["n_stays_measured"] == 0, "nothing after outtime"
    # the same for every itemid: the at-risk column is item-independent
    for itemid in (CREATININE, FOLEY):
        other = hourly.filter(pl.col("itemid") == itemid)
        assert (
            other.get_column("n_stays_at_risk").to_list()
            == hr.get_column("n_stays_at_risk").to_list()
        )
    # creatinine: draws at hours 2, 7, 12 only inside the window (pre-ICU / post-outtime never)
    cr = hourly.filter(pl.col("itemid") == CREATININE)
    assert int(cr.get_column("n_measurements").sum()) == N_LOW * 2 + (N_MEAS - N_LOW) * 3
    assert _rows(cr, hour_bin=2)[0]["n_stays_measured"] == N_MEAS
    # daily: the day-0 measured stays equal the first-24 h measured stays
    assert _rows(daily.filter(pl.col("itemid") == HR), day_index=0)[0]["n_stays_measured"] == (
        N_A1 + N_A2 - LATE_HR
    )
    assert _rows(daily, itemid=HR, day_index=2)[0]["n_stays_at_risk"] == 0
    # statistics: HR hourly -> a 60-minute median interval; per stay-day around 24
    hr_stats = _rows(stats, itemid=HR)[0]
    assert hr_stats["median_interval_min"] == pytest.approx(60.0)
    assert hr_stats["p10_interval_min"] == pytest.approx(60.0)
    assert hr_stats["median_per_stay_day"] == pytest.approx(24.0, rel=0.05)
    foley = _rows(stats, itemid=FOLEY)[0]
    assert foley["median_interval_min"] == pytest.approx(120.0)
    cr_stats = _rows(stats, itemid=CREATININE)[0]
    assert cr_stats["median_interval_min"] == pytest.approx(300.0)
    # one occasion per stay: no interval; the 7 short stays put the median at 1 / (10 / 24)
    weight = _rows(stats, itemid=WEIGHT)[0]
    assert weight["median_interval_min"] is None and weight["p90_interval_min"] is None
    assert weight["median_per_stay_day"] == pytest.approx(24.0 / 10.0)
    assert _rows(hourly, itemid=WEIGHT, hour_bin=0)[0]["n_stays_measured"] == N_WEIGHT


def test_structural_cells_and_absence_attribution(
    crafted_slices: dict[str, mp.Slice],
) -> None:
    s = crafted_slices[mp.STEP_STRUCTURAL]
    cells = s.frames["cells"]
    assert cells.height == len(ITEMS) * 3, "4 itemids x 3 populated cells"
    flagged = mp.flag_cells(cells, _params())
    hr = {(r["careunit"], r["era"]): r for r in _rows(flagged, itemid=HR)}
    assert hr[(UNIT_A, ERA_1)]["n_stays"] == N_A1 and hr[(UNIT_B, ERA_1)]["n_stays"] == N_B1
    assert hr[(UNIT_B, ERA_1)]["n_stays_measured"] == 0
    assert hr[(UNIT_B, ERA_1)]["structural_flag"] == mp.FLAG_STRUCTURAL, (
        ">= 50 stays, never charted"
    )
    assert hr[(UNIT_A, ERA_1)]["structural_flag"] == mp.FLAG_IN_USE
    assert hr[(UNIT_A, ERA_1)]["share"] == pytest.approx(1.0)
    assert hr[(UNIT_A, ERA_2)]["structural_flag"] == mp.FLAG_IN_USE, "12 stays, all measured"
    # a small empty cell is sparse, never structural: creatinine is drawn for none of the
    # 12 unit A / era 2 stays (below min_stays) and the weight for none of unit B's 55
    cr = {(r["careunit"], r["era"]): r for r in _rows(flagged, itemid=CREATININE)}
    assert cr[(UNIT_A, ERA_2)]["n_stays_measured"] == 0
    assert cr[(UNIT_A, ERA_2)]["structural_flag"] == mp.FLAG_SPARSE, "12 stays < min_stays"
    assert cr[(UNIT_B, ERA_1)]["structural_flag"] == mp.FLAG_IN_USE, "20 of 55 measured"
    weight = {(r["careunit"], r["era"]): r for r in _rows(flagged, itemid=WEIGHT)}
    assert weight[(UNIT_B, ERA_1)]["structural_flag"] == mp.FLAG_STRUCTURAL
    assert weight[(UNIT_A, ERA_1)]["n_stays_measured"] == N_WEIGHT
    assert weight[(UNIT_A, ERA_1)]["structural_flag"] == mp.FLAG_IN_USE, "12 / 60 = 20 %"
    # the same on the Foley item: unit B / era 1 (55 stays, none measured) is structural,
    # and a cell below min_stays with nothing measured reads sparse
    foley = {(r["careunit"], r["era"]): r for r in _rows(flagged, itemid=FOLEY)}
    assert foley[(UNIT_B, ERA_1)]["structural_flag"] == mp.FLAG_STRUCTURAL
    small_empty = mp.flag_cells(
        cells.filter((pl.col("itemid") == FOLEY) & (pl.col("careunit") == UNIT_A)).with_columns(
            pl.lit(0, dtype=pl.Int64).alias("n_stays_measured")
        ),
        _params(),
    )
    assert {
        r["structural_flag"] for r in small_empty.filter(pl.col("era") == ERA_2).to_dicts()
    } == {mp.FLAG_SPARSE}
    assert {
        r["structural_flag"] for r in small_empty.filter(pl.col("era") == ERA_1).to_dicts()
    } == {mp.FLAG_STRUCTURAL}
    # the per-stay attribution: HR missing in the first 24 h = the 55 unit-B stays
    # (structural) + the 3 late unit-A stays (unmeasured)
    per_item = {r["itemid"]: r for r in mp.item_counts(flagged).to_dicts()}
    hr_counts = per_item[HR]
    assert hr_counts["n_stays"] == N_POP
    assert hr_counts["n_stays_measured"] == N_A1 + N_A2
    assert hr_counts["n_stays_measured_first_24h"] == N_A1 + N_A2 - LATE_HR
    assert hr_counts["n_missing_first_24h"] == N_B1 + LATE_HR
    assert hr_counts["n_structural"] == N_B1 and hr_counts["n_unmeasured"] == LATE_HR
    cr_counts = per_item[CREATININE]
    assert cr_counts["n_stays_measured_first_24h"] == N_MEAS
    assert cr_counts["n_missing_first_24h"] == N_NOT and cr_counts["n_structural"] == 0
    assert cr_counts["n_unmeasured"] == N_NOT
    weight_counts = per_item[WEIGHT]
    assert weight_counts["n_stays_measured"] == weight_counts["n_stays_measured_first_24h"]
    assert weight_counts["n_stays_measured"] == N_WEIGHT
    assert weight_counts["n_missing_first_24h"] == N_POP - N_WEIGHT
    assert weight_counts["n_structural"] == N_B1
    assert weight_counts["n_unmeasured"] == N_POP - N_WEIGHT - N_B1


def test_presence_arms_and_rate_ratio_hand_computation(
    crafted: duckdb.DuckDBPyConnection, crafted_slices: dict[str, mp.Slice]
) -> None:
    s = crafted_slices[mp.STEP_PRESENCE]
    arms = {r["arm"]: r for r in _rows(s.frames["arms"], itemid=CREATININE)}
    assert set(arms) == set(mp.COUNT_ARMS) and s.meta["n_population"] == N_POP
    assert (arms["0"]["n_stays"], arms["0"]["n_deaths"]) == (N_NOT, D_NOT)
    assert arms["1-2"]["n_stays"] == N_LOW and arms[">=3"]["n_stays"] == N_MEAS - N_LOW
    assert arms["1-2"]["n_deaths"] + arms[">=3"]["n_deaths"] == D_MEAS
    rows = mp.presence_rows(s.frames["arms"], {CREATININE: "Creatinine"})
    binary = {r["arm"]: r for r in _rows(rows, contrast=mp.CONTRAST_BINARY)}
    assert (binary["measured"]["n_stays"], binary["measured"]["n_deaths"]) == (N_MEAS, D_MEAS)
    assert binary["measured"]["mortality_rate"] == pytest.approx(D_MEAS / N_MEAS)
    assert binary["not_measured"]["mortality_rate"] == pytest.approx(D_NOT / N_NOT)
    # the hand computation: RR = (20 / 80) / (5 / 47); Wald CI on the log scale with
    # se = sqrt(1/a - 1/n1 + 1/b - 1/n2) and z = 1.96 (scipy's exact 0.975 quantile)
    estimate = mp.rate_ratio(D_MEAS, N_MEAS, D_NOT, N_NOT)
    assert estimate is not None
    rr, lo, hi = estimate
    expected = (D_MEAS / N_MEAS) / (D_NOT / N_NOT)
    z = 1.959963984540054
    se = math.sqrt(1 / D_MEAS - 1 / N_MEAS + 1 / D_NOT - 1 / N_NOT)
    assert rr == pytest.approx(expected) and expected == pytest.approx(2.35)
    assert lo == pytest.approx(math.exp(math.log(expected) - z * se), rel=1e-6)
    assert hi == pytest.approx(math.exp(math.log(expected) + z * se), rel=1e-6)
    # a symmetric crafted table: RR = (20 / 100) / (5 / 50) = 2 exactly
    symmetric = mp.rate_ratio(20, 100, 5, 50)
    assert symmetric is not None and symmetric[0] == pytest.approx(2.0)
    assert symmetric[1] < 2.0 < symmetric[2]
    # the standalone statement equals the slice on the crafted connection
    again = crafted.execute(mp.presence_sql([CREATININE], 24)).pl().sort("arm")
    assert again.to_dicts() == s.frames["arms"].sort("arm").to_dicts()


def test_presence_sql_is_admitted_by_safe_query(fixture_lake_settings: Settings) -> None:
    """The shape the EP-33 amendment asks for: the arms are count(*) FILTER aggregates a
    session could run through safe_query itself (identifiers stay inside the CTE chain)."""
    from mimicwarehouse.safe import safe_query

    result = safe_query(
        mp.presence_sql([CREATININE], 24),
        tier="fixture",
        settings=fixture_lake_settings,
        actor="test_ep45",
        row_cap=100,
    )
    assert set(result.df.columns) == {"itemid", "arm", "n_stays", "n_deaths"}
    assert result.k == K and result.n_rows <= 3


# ---------------------------------------------------------------------------
# 3. Assembly: suppression, the published shapes, the gate
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def assembled(
    crafted_slices: dict[str, mp.Slice], catalogue: units.ItemCatalogue
) -> dict[str, pl.DataFrame]:
    return mp.assemble(
        crafted_slices, k=K, tier="fixture", build_id="b", run_id=None, catalogue=catalogue
    )


def _hidden(frame: pl.DataFrame, column: str) -> pl.DataFrame:
    return frame.filter(pl.col(f"{column}_suppressed"))


def test_assembled_tables_are_suppressed_and_pass_the_gate(
    assembled: dict[str, pl.DataFrame],
) -> None:
    assert set(assembled) == set(mp.META_TABLES)
    for table, frame in assembled.items():
        assert frame.height > 0, table
        assert (frame.get_column("k") == K).all() and (frame.get_column("tier") == "fixture").all()
        findings = disclose.check_frame(frame, K)
        assert not [f for f in findings if f.status == disclose.FAIL], (
            table,
            [f.as_dict() for f in findings],
        )
        for column in frame.columns:
            marker = f"{column}_suppressed"
            if marker in frame.columns:
                hidden = frame.filter(pl.col(marker))
                assert hidden.get_column(column).is_null().all(), (table, column)
                shown = frame.filter(~pl.col(marker)).get_column(column).drop_nulls()
                assert not ((shown > 0) & (shown < K)).any(), (table, column)
    hourly = assembled[mp.HOURLY_TABLE]
    assert list(hourly.columns) == list(mp.hourly_columns("hour_bin"))
    # the 3 late unit-A stays (hours 30-39) are a small cell in every column: hidden
    late_bins = hourly.filter((pl.col("itemid") == HR) & (pl.col("hour_bin").is_between(30, 39)))
    for column in ("n_stays_at_risk", "n_stays_measured", "n_measurements"):
        assert late_bins.get_column(f"{column}_suppressed").all(), column
    bin0 = _rows(hourly, itemid=HR, hour_bin=0)[0]
    assert bin0["n_stays_at_risk"] == N_POP and bin0["n_stays_measured"] == N_A1 + N_A2 - LATE_HR
    summary = assembled[mp.SUMMARY_TABLE]
    assert list(summary.columns) == list(mp.SUMMARY_COLUMNS)
    hr = _rows(summary, itemid=HR)[0]
    assert hr["label"] == units.spec(HR).label and hr["source"] == "chartevents"
    assert hr["n_stays"] == N_POP and hr["n_stays_measured"] == N_A1 + N_A2
    # measured (72) - measured in the first 24 h (69) = the 3 late stays: the nested-pair
    # rule hides the first-24 h count, the share goes with it, and the identity
    # n_stays = measured_24h + missing then hides n_missing (else 127 - 58 restores it)
    assert hr["n_stays_measured_first_24h"] is None
    assert hr["n_stays_measured_first_24h_suppressed"]
    assert hr["measured_first_24h_share"] is None
    assert hr["median_interval_min"] == pytest.approx(60.0), "stats stay: 65 stays published"
    absence = assembled[mp.ABSENCE_TABLE]
    assert list(absence.columns) == list(mp.ABSENCE_COLUMNS)
    hr_abs = _rows(absence, itemid=HR)[0]
    # n_unmeasured = 3 < k: hidden; n_missing (58) - n_structural (55) = 3 would restore
    # it, so the nested-pair rule hides n_structural too; n_missing hides by the identity
    assert hr_abs["n_unmeasured"] is None and hr_abs["n_unmeasured_suppressed"]
    assert hr_abs["n_structural"] is None and hr_abs["n_structural_suppressed"]
    assert hr_abs["n_missing_first_24h"] is None and hr_abs["n_missing_first_24h_suppressed"]
    # the Foley and weight items have nothing small: every count of their rows is published
    foley_abs = _rows(absence, itemid=FOLEY)[0]
    assert foley_abs["n_missing_first_24h"] == N_B1 and foley_abs["n_structural"] == N_B1
    assert foley_abs["n_unmeasured"] == 0
    weight_abs = _rows(absence, itemid=WEIGHT)[0]
    assert weight_abs["n_missing_first_24h"] == N_POP - N_WEIGHT
    assert weight_abs["n_structural"] == N_B1
    assert weight_abs["n_unmeasured"] == N_POP - N_WEIGHT - N_B1
    weight_row = _rows(summary, itemid=WEIGHT)[0]
    assert weight_row["n_stays_measured"] == N_WEIGHT
    assert weight_row["measured_first_24h_share"] == pytest.approx(N_WEIGHT / N_POP)
    structural = assembled[mp.STRUCTURAL_TABLE]
    assert list(structural.columns) == list(mp.STRUCTURAL_COLUMNS)
    assert not structural.get_column("n_stays_suppressed").any(), "no cell below k"
    assert not structural.get_column("n_stays_measured_suppressed").any()
    cells = {(r["first_careunit"], r["era"]): r for r in _rows(structural, itemid=HR)}
    assert cells[(UNIT_B, ERA_1)]["structural_flag"] == mp.FLAG_STRUCTURAL
    assert cells[(UNIT_B, ERA_1)]["n_stays_measured"] == 0, "zero is never small"
    assert cells[(UNIT_B, ERA_1)]["n_stays"] == N_B1 and cells[(UNIT_B, ERA_1)]["share"] == 0.0
    assert cells[(UNIT_A, ERA_2)]["structural_flag"] == mp.FLAG_IN_USE
    cr_cells = {(r["first_careunit"], r["era"]): r for r in _rows(structural, itemid=CREATININE)}
    assert cr_cells[(UNIT_A, ERA_2)]["structural_flag"] == mp.FLAG_SPARSE
    presence = assembled[mp.PRESENCE_TABLE]
    assert list(presence.columns) == list(mp.PRESENCE_COLUMNS)
    cr = {(r["contrast"], r["arm"]): r for r in _rows(presence, itemid=CREATININE)}
    assert set(cr) == {("binary", a) for a in mp.BINARY_ARMS} | {
        ("count", a) for a in mp.COUNT_ARMS
    }
    # not_measured: 40 stays / 5 deaths -> the deaths are hidden, the rate and the RR blank
    ref = cr[("binary", "not_measured")]
    assert ref["n_deaths"] is None and ref["n_deaths_suppressed"] and ref["mortality_rate"] is None
    assert ref["n_stays"] == N_NOT
    meas = cr[("binary", "measured")]
    assert meas["n_stays"] == N_MEAS
    assert meas["rate_ratio"] is None and meas["rr_ci_low"] is None
    assert cr[("count", "0")]["n_deaths"] is None, "the same stays as not_measured"
    assert (presence.get_column("claim_type") == mp.CLAIM_TYPE_SHORT).all()
    assert (presence.get_column("caveat") == mp.CAVEAT).all()
    longest = presence.get_column("caveat").str.len_chars().max()
    assert isinstance(longest, int) and longest <= qc.VALUE_MAX_CHARS


def test_structural_grid_suppression_on_a_crafted_small_cell() -> None:
    """A crafted grid of one item over two units x three eras with one 5-stay cell: the
    small cell is hidden, and the unit / era / population margins of the primitive
    cascade (a lone hidden cell takes its next-smallest neighbour in every margin it
    sits in — `docs/gotchas.md` section 5); what this module adds on top is asserted
    as rules: a hidden count leaves no share and no flag behind, a structural flag is
    only ever published beside a published zero, the raw flags still drive the absence
    split, and the published frame passes the gate."""
    eras = list(timesem.ERAS[:3])
    cells = pl.DataFrame(
        {
            "itemid": [HR] * 6,
            "careunit": [UNIT_A] * 3 + [UNIT_B] * 3,
            "era": eras * 2,
            "n_stays": [60, 60, 5, 55, 55, 55],
            "n_stays_measured": [60, 60, 5, 0, 0, 0],
            "n_stays_measured_first_24h": [60, 60, 5, 0, 0, 0],
        },
        schema=mp._schemas()["cells"],
    )
    flagged = mp.flag_cells(cells, mp.MeasurementParams())
    raw = {(r["careunit"], r["era"]): r for r in flagged.to_dicts()}
    assert all(raw[(UNIT_B, era)]["structural_flag"] == mp.FLAG_STRUCTURAL for era in eras)
    assert all(raw[(UNIT_A, era)]["structural_flag"] == mp.FLAG_IN_USE for era in eras)
    published = mp.suppress_cells(flagged, K)
    rows = {(r["careunit"], r["era"]): r for r in published.to_dicts()}
    small = rows[(UNIT_A, eras[2])]
    assert small["n_stays"] is None and small["n_stays_suppressed"], "5 stays: hidden"
    assert small["n_stays_measured"] is None and small["n_stays_measured_suppressed"]
    assert small["share"] is None and small["structural_flag"] is None
    for r in rows.values():
        if r["n_stays_measured_suppressed"]:
            assert r["n_stays_measured"] is None and r["structural_flag"] is None
            assert r["share"] is None, "no share beside a hidden count"
        if r["structural_flag"] == mp.FLAG_STRUCTURAL:
            assert r["n_stays_measured"] == 0 and not r["n_stays_measured_suppressed"]
        if r["n_stays_suppressed"]:
            assert r["n_stays"] is None and r["share"] is None
    assert not [f for f in disclose.check_frame(published, K) if f.status == disclose.FAIL]
    # the absence split reads the raw flags: every unit-B stay counts as structural
    counts = mp.item_counts(flagged).to_dicts()[0]
    assert counts["n_structural"] == 3 * 55 and counts["n_unmeasured"] == 0
    assert counts["n_missing_first_24h"] == 3 * 55 and counts["n_stays"] == 290


def test_rate_ratio_survives_when_every_arm_is_published(
    crafted_slices: dict[str, mp.Slice],
) -> None:
    """Crafted arms above k everywhere: the binary RR and the count RRs are published."""
    arms = pl.DataFrame(
        {
            "itemid": [CREATININE] * 3,
            "arm": list(mp.COUNT_ARMS),
            "n_stays": [200, 150, 100],
            "n_deaths": [20, 30, 40],
        },
        schema={"itemid": pl.Int64, "arm": pl.String, "n_stays": pl.Int64, "n_deaths": pl.Int64},
    )
    published = mp.suppress_presence(mp.presence_rows(arms, {CREATININE: "Creatinine"}), K)
    rows = {(r["contrast"], r["arm"]): r for r in published.to_dicts()}
    assert not any(r["n_stays_suppressed"] or r["n_deaths_suppressed"] for r in rows.values())
    meas = rows[("binary", "measured")]
    assert meas["n_stays"] == 250 and meas["n_deaths"] == 70
    assert meas["rate_ratio"] == pytest.approx((70 / 250) / (20 / 200))
    assert meas["rr_ci_low"] < meas["rate_ratio"] < meas["rr_ci_high"]
    assert rows[("binary", "not_measured")]["rate_ratio"] is None, "the reference arm"
    assert rows[("count", ">=3")]["rate_ratio"] == pytest.approx((40 / 100) / (20 / 200))
    assert rows[("count", "1-2")]["rate_ratio"] == pytest.approx((30 / 150) / (20 / 200))
    assert not [f for f in disclose.check_frame(published, K) if f.status == disclose.FAIL]
    # the cross-contrast rule: a hidden upper count arm hides the binary measured arm
    small = arms.with_columns(
        pl.Series("n_stays", [200, 150, 5], dtype=pl.Int64),
        pl.Series("n_deaths", [20, 30, 2], dtype=pl.Int64),
    )
    published = mp.suppress_presence(mp.presence_rows(small, {CREATININE: "Creatinine"}), K)
    rows = {(r["contrast"], r["arm"]): r for r in published.to_dicts()}
    assert rows[("count", ">=3")]["n_stays_suppressed"]
    assert rows[("binary", "measured")]["n_stays_suppressed"], "the sum of a hidden part"
    assert rows[("binary", "measured")]["rate_ratio"] is None
    assert not [f for f in disclose.check_frame(published, K) if f.status == disclose.FAIL]


def test_report_renders_from_crafted_frames_and_passes_the_gate(
    assembled: dict[str, pl.DataFrame], tmp_path: Path
) -> None:
    inputs = mp.ReportInputs(
        tier="fixture",
        k=K,
        run_id=None,
        build_id="b",
        snapshot_id="s",
        params=_params(),
        n_population=N_POP,
        n_excluded=1,
        n_population_outcome=N_POP,
        frames=assembled,
        generated="2026-09-16",
    )
    paths = mp.write_report(inputs, tmp_path)
    assert [p.name for p in paths] == list(mp.REPORT_FILES)
    text = paths[0].read_text(encoding="utf-8")
    assert text.isascii() and "\r" not in text
    for needle in (
        f"**Claim type: {mp.CLAIM_TYPE}.**",
        mp.RETROSPECTIVE_SENTENCE,
        "## Measurement frequency",
        "## Structural absence (care unit x era)",
        "### Structural cells",
        "### Absence in the first 24 h",
        "## Informative presence (exploratory)",
        "### By number of measurements in the first 24 h",
        "## What it deliberately does not claim",
        "## Reproduction",
        "<11",
        UNIT_B,
        "Heart Rate",
        "Creatinine",
        "MISS-2",
    ):
        assert needle in text, needle
    assert not BAND_TOKEN.search(text)
    for path in paths:
        result = disclose.check(path, k=K)
        assert result.passed, (path.name, [f.as_dict() for f in result.findings])


# ---------------------------------------------------------------------------
# 4. The session fixture lake: the six tables, the run, the report, the benchmarks
# ---------------------------------------------------------------------------


def test_session_lake_carries_the_measurement_tables(
    fixture_lake_catalog: duckdb.DuckDBPyConnection,
    fixture_lake_settings: Settings,
    catalogue: units.ItemCatalogue,
) -> None:
    con = fixture_lake_catalog
    meta_tables = {
        str(r[0])
        for r in con.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'meta'"
        ).fetchall()
    }
    assert set(mp.META_TABLES) <= meta_tables and not any(n.startswith("raw") for n in meta_tables)
    summary = con.execute("SELECT * FROM meta.mp_item_summary").pl()
    assert list(summary.columns) == list(mp.SUMMARY_COLUMNS)
    assert set(summary.get_column("itemid").to_list()) == set(catalogue.itemids()), (
        "the default itemid set: every curated item (the fixture stages every source table)"
    )
    run_ids = set(summary.get_column("run_id").to_list())
    assert len(run_ids) == 1 and run_mod.RUN_ID_RE.match(next(iter(run_ids)))
    assert (summary.get_column("k") == K).all() and (summary.get_column("tier") == "fixture").all()
    for table in mp.META_TABLES:
        frame = con.execute(f'SELECT * FROM meta."{table}"').pl()
        assert frame.height > 0, table
        assert set(frame.get_column("run_id").to_list()) == run_ids, "one run wrote every row"
        for column in frame.columns:
            marker = f"{column}_suppressed"
            if marker in frame.columns:
                shown = frame.filter(~pl.col(marker)).get_column(column).drop_nulls()
                assert not ((shown > 0) & (shown < K)).any(), (table, column)
                assert frame.filter(pl.col(marker)).get_column(column).is_null().all()
        comment = _scalar(
            con,
            f"SELECT comment FROM duckdb_tables() WHERE schema_name = 'meta' "
            f"AND table_name = '{table}'",
        )
        assert comment and "EP-45" in comment, table
    hourly = con.execute("SELECT * FROM meta.mp_item_hourly").pl()
    assert hourly.height == len(catalogue.itemids()) * 168
    assert hourly.get_column("hour_bin").max() == 167 and hourly.get_column("hour_bin").min() == 0
    daily = con.execute("SELECT * FROM meta.mp_item_daily").pl()
    assert daily.height == len(catalogue.itemids()) * 14
    # the at-risk curve is non-increasing where published
    hr = hourly.filter((pl.col("itemid") == HR) & ~pl.col("n_stays_at_risk_suppressed"))
    at_risk = hr.sort("hour_bin").get_column("n_stays_at_risk").to_list()
    assert at_risk == sorted(at_risk, reverse=True) and at_risk[0] > 0
    structural = con.execute("SELECT * FROM meta.mp_structural").pl()
    assert set(structural.get_column("era").to_list()) <= set(timesem.ERAS) | {mp.UNKNOWN_LABEL}
    assert set(structural.get_column("structural_flag").drop_nulls().to_list()) <= set(mp.FLAGS)
    presence = con.execute("SELECT * FROM meta.mp_presence_outcome").pl()
    labs = {i for i in catalogue.itemids() if catalogue.spec(i).source == "labevents"}
    assert set(presence.get_column("itemid").to_list()) == labs
    assert presence.height == len(labs) * (len(mp.BINARY_ARMS) + len(mp.COUNT_ARMS))
    assert (presence.get_column("claim_type") == mp.CLAIM_TYPE_SHORT).all()
    lake = fixture_lake_settings.lake_root("fixture")
    for step in mp.COMPUTE_STEPS:
        assert mp.slice_meta_path(lake, "fixture", step).is_file()
    raw_cells = mp.read_slice(lake, "fixture", mp.STEP_STRUCTURAL).frames["cells"]
    assert "n_stays_suppressed" not in raw_cells.columns and raw_cells.height == structural.height


def test_session_lake_report_run_and_benchmarks(
    fixture_lake_catalog: duckdb.DuckDBPyConnection, fixture_lake_settings: Settings
) -> None:
    run_id = _scalar(fixture_lake_catalog, "SELECT DISTINCT run_id FROM meta.mp_item_summary")
    assert run_id is not None
    out = run_mod.run_dir(run_id, fixture_lake_settings)
    paths = [out / name for name in mp.REPORT_FILES]
    assert all(p.is_file() for p in paths), paths
    text = paths[0].read_text(encoding="utf-8")
    assert text.isascii() and "\r" not in text
    for needle in (
        f"`{run_id}`",
        f"**Claim type: {mp.CLAIM_TYPE}.**",
        "## Measurement frequency",
        "## Structural absence (care unit x era)",
        "## Informative presence (exploratory)",
        "## Reproduction",
        "## Provenance",
        "Heart Rate",
    ):
        assert needle in text, needle
    assert not BAND_TOKEN.search(text)
    for path in paths:
        result = disclose.check(path, k=K)
        assert result.passed, (path.name, [f.as_dict() for f in result.findings])
    manifest = run_mod.read_manifest(run_id, fixture_lake_settings)
    assert manifest.kind == mp.RUN_KIND and manifest.status == "ok"
    assert manifest.name == mp.RUN_NAME and manifest.claim_type == mp.CLAIM_TYPE
    assert manifest.tier == "fixture" and "core" in manifest.snapshot_ids
    assert {r.kind for r in manifest.refs} >= {"item_catalogue", "measurement_params"}
    assert manifest.params["k"] == K and manifest.params["n_population"] > 0
    assert manifest.params["params"]["hours"] == 168 and manifest.params["params"]["days"] == 14
    assert set(manifest.params["tables"]) == set(mp.META_TABLES)
    assert {"population", "hourly", "presence", "cells"} <= set(manifest.sql)
    assert manifest.wall_s is not None and manifest.finished is not None
    assert any(
        r.get("run_id") == run_id and r.get("kind") == mp.RUN_KIND
        for r in run_mod.read_ledger(fixture_lake_settings)
    )
    bench = benchmarks.summarize(fixture_lake_settings, tier="fixture", kind=mp.BENCH_KIND)
    steps = set(bench.get_column("step").to_list())
    assert set(mp.COMPUTE_STEPS) <= steps
    assert bench.filter(pl.col("step").is_in(list(mp.COMPUTE_STEPS))).get_column("ok").all()


def test_stale_slice_and_missing_slice_refuse_the_report_step(
    fixture_lake_settings: Settings, tmp_path: Path
) -> None:
    import shutil

    source_lake = fixture_lake_settings.lake_root("fixture")
    root = tmp_path / "root"
    settings = Settings(data_root=root)
    lake = settings.lake_root("fixture")
    shutil.copytree(source_lake, lake)
    meta_path = mp.slice_meta_path(lake, "fixture", mp.STEP_PRESENCE)
    doc = json.loads(meta_path.read_text(encoding="utf-8"))
    doc["snapshot_id"] = "0" * 64
    meta_path.write_text(json.dumps(doc), encoding="utf-8")
    stale = runner_mod.run(load_dag(), "fixture", select=[mp.STEP_REPORT], settings=settings)
    assert not stale.ok and "stale" in (stale.steps[0].error or "")
    meta_path.unlink()
    missing = runner_mod.run(load_dag(), "fixture", select=[mp.STEP_REPORT], settings=settings)
    assert not missing.ok and "no measurement slice" in (missing.steps[0].error or "")
    # a fresh presence step + the report step repair it (the catalog is not needed)
    repaired = runner_mod.run(
        load_dag(), "fixture", select=[mp.STEP_PRESENCE, mp.STEP_REPORT], settings=settings
    )
    assert repaired.ok, [f"{s.name}: {s.error}" for s in repaired.steps if s.status == "failed"]


# ---------------------------------------------------------------------------
# 5. mwh qc measurement
# ---------------------------------------------------------------------------


def test_qc_measurement_cli(fixture_lake_settings: Settings) -> None:
    runner = helpers.cli_runner()
    root = str(fixture_lake_settings.data_root)
    try:
        listing = runner.invoke(
            app, ["--data-root", root, "qc", "measurement", "--tier", "fixture", "--top", "5"]
        )
        assert listing.exit_code == 0, listing.output
        assert "meta.mp_item_summary (fixture)" in listing.output
        assert "Heart Rate" in listing.output and "curated itemid(s)" in listing.output
        assert not BAND_TOKEN.search(listing.output)
        as_json = runner.invoke(
            app, ["--data-root", root, "qc", "measurement", "--tier", "fixture", "--json"]
        )
        assert as_json.exit_code == 0, as_json.output
        payload = json.loads(as_json.stdout)
        assert payload["tier"] == "fixture" and len(payload["items"]) == len(units.load_catalogue())
        assert run_mod.RUN_ID_RE.match(payload["run_id"]) and payload["k"] == K
        assert set(payload["flag_counts"]) == {str(i) for i in units.load_catalogue().itemids()}
        for item in payload["items"]:
            for column in ("n_stays", "n_stays_measured", "n_stays_measured_first_24h"):
                value = item[column]
                assert value is None or value == 0 or value >= K
        summary = measurement_summary("fixture", settings=fixture_lake_settings, actor="test_ep45")
        assert len(summary["structural_cells"]) == min(20, summary["n_structural_cells"])
        bad_tier = runner.invoke(app, ["--data-root", root, "qc", "measurement", "--tier", "nope"])
        assert bad_tier.exit_code == 2
    finally:
        config.configure()


# ---------------------------------------------------------------------------
# 6. Docs in sync, import budget
# ---------------------------------------------------------------------------


def test_methods_doc_and_status_surfaces() -> None:
    path = helpers.WORKSPACE / "docs" / "methods" / "measurement-process.md"
    text = path.read_text(encoding="utf-8")
    for needle in (
        "meta.mp_item_hourly",
        "meta.mp_item_daily",
        "meta.mp_item_summary",
        "meta.mp_structural",
        "meta.mp_absence_summary",
        "meta.mp_presence_outcome",
        "mwh qc measurement",
        "measurement_process.md",
        "disclose.suppress",
        "retrospective",
        "structural",
        "unmeasured",
        "informative presence",
        "anchor_year_group",
        "MetaVision",
        "Wald",
        "EP-39",
        "EP-43",
        "EP-44",
        "EP-45",
        "EP-72",
        "EP-87",
        "MISS-2",
    ):
        assert needle in text, needle
    assert not BAND_TOKEN.search(text) and text.isascii()
    for doc in ("README.md", "DESIGN.md", "DECISIONS.md"):
        assert "EP-45" in (helpers.WORKSPACE / doc).read_text(encoding="utf-8"), doc
    assert "mwh qc measurement" in (helpers.WORKSPACE / "README.md").read_text(encoding="utf-8")
    assert (helpers.WORKSPACE / "docs" / "analyses" / "README.md").read_text(
        encoding="utf-8"
    ).count("EP-45") >= 1


def test_import_budget() -> None:
    helpers.assert_import_budget(
        "mimicwarehouse.cli",
        lazy=(
            "mimicwarehouse.qc.report",
            "mimicwarehouse.safe",
            "mimicwarehouse.run",
            "mimicwarehouse.dag.runner",
            "statsmodels",
        ),
    )


# ---------------------------------------------------------------------------
# 7. Dev tier: the real catalog through safe_query (aggregates only)
# ---------------------------------------------------------------------------


@pytest.mark.tier("dev")
def test_dev_measurement_tables(dev_catalog: Path) -> None:
    from mimicwarehouse.safe import safe_query

    settings = config.load_settings()
    remedy = "run `mwh build --tier dev --tag measurement` (EP-45) first"
    catalogue = units.load_catalogue()
    summary = measurement_summary("dev", settings=settings, actor="test_ep45")
    itemids = {int(r["itemid"]) for r in summary["items"]}
    assert itemids == set(catalogue.itemids()), remedy
    assert summary["run_id"] and run_mod.RUN_ID_RE.match(summary["run_id"])
    for r in summary["items"]:
        for column in ("n_stays", "n_stays_measured", "n_stays_measured_first_24h"):
            if not r[f"{column}_suppressed"]:
                assert r[column] is None or r[column] == 0 or r[column] >= K
    presence = safe_query(
        "SELECT itemid, contrast, arm, n_stays, n_stays_suppressed, n_deaths, "
        "n_deaths_suppressed, rate_ratio FROM meta.mp_presence_outcome",
        tier="dev",
        settings=settings,
        actor="test_ep45",
        row_cap=10_000,
    ).df
    labs = {i for i in catalogue.itemids() if catalogue.spec(i).source == "labevents"}
    assert set(presence.get_column("itemid").to_list()) == labs, remedy
    for column in ("n_stays", "n_deaths"):
        shown = presence.filter(~pl.col(f"{column}_suppressed")).get_column(column).drop_nulls()
        assert not ((shown > 0) & (shown < K)).any(), "nothing below k leaves the real catalog"
    hourly = safe_query(
        "SELECT itemid, hour_bin, n_stays_at_risk, n_stays_at_risk_suppressed FROM "
        f"meta.mp_item_hourly WHERE itemid = {HR}",
        tier="dev",
        settings=settings,
        actor="test_ep45",
        row_cap=10_000,
    ).df
    assert hourly.height == 168
    print(
        f"dev: {len(itemids)} itemids, {summary['n_structural_cells']} structural cell(s), "
        f"run {summary['run_id']}"
    )
