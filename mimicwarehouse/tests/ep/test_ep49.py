"""EP-49 — Event-aligned timeline API.

Fixture tier (default): crafted synthetic events (ids >= 90 000 000, a temp DuckDB) give
correct signed ``hours_since_anchor``, ``[start, end)`` window edges, ``clip_to_stay``, the
as-of pick (last prior value within tolerance, the event at the anchor counts), the
``ASOF`` / window joins, bins that sum to the raw counts with empty bins filled with 0 /
NULL, the population summary through the suppressor seam, ``to_mart``; every anchor's SQL
(the registry and the factories) and every source preset compile on the session fixture
lake for all three grains; ``stay_events`` refuses an agent / raw connection and admits
the owner one on the fixture lake with one ``row_view:`` audit line; the benchmark on the
fixture lake writes the four gated exports with sidecars; the CLI; the docs page is in
sync; the import budget. ``tier("dev")``: the benchmark pipeline runs on the real dev
catalog and the released summary prints. ``tier("full")``: the background job's ledger
line and exports verify.

Everything asserted or printed is synthetic or suppressed aggregate text — never a row of
real data; ``stay_events`` is never called on dev / full (D-32).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

import duckdb
import polars as pl
import pytest

import helpers
from mimicwarehouse import config, disclose, fsio, safe, timesem
from mimicwarehouse import timeline as tl
from mimicwarehouse.catalog.connect import ROLE_VARIABLE, open_catalog
from mimicwarehouse.cli import app
from mimicwarehouse.dag import benchmarks

if TYPE_CHECKING:
    from mimicwarehouse.config import Settings

pytestmark = pytest.mark.ep_49

K = 11
S1, S2, S3 = 90_000_001, 90_000_002, 90_000_003
H1, H2, H3 = 90_100_001, 90_100_002, 90_100_003
ST1, ST2, ST3 = 90_200_001, 90_200_002, 90_200_003
CREATININE, LACTATE, HR = 50912, 50813, 220045
RUN_ID_RE = re.compile(r"\d{8}T\d{6}Z-[0-9a-f]{6}")
BAND_TOKEN = re.compile(r"(?<![\w.])[123]\d{7}(?![\w.])")
GRAINS = ("icustay", "hadm", "subject")
#: Stay 1 (intime = T0, outtime = T0 + 72 h): creatinine at these hours since intime.
STAY1_HOURS: tuple[tuple[float, float], ...] = (
    (-25, 1.0),
    (-24, 2.0),
    (-0.5, 3.0),
    (0, 4.0),
    (1, 5.0),
    (71.99, 6.0),
    (72, 7.0),
    (100, 8.0),
)
#: Stay 2 (outtime = T0 + 24 h): two results in hour 2, plus a lactate at 5 h.
STAY2_EVENTS: tuple[tuple[int, float, float], ...] = (
    (CREATININE, 2, 10.0),
    (CREATININE, 2.5, 12.0),
    (LACTATE, 5, 2.5),
)


def _ts(base: str, hours: float) -> str:
    return f"TIMESTAMP '{base}' + to_seconds(CAST({round(hours * 3600)} AS BIGINT))"


@pytest.fixture(scope="module")
def crafted() -> Iterator[duckdb.DuckDBPyConnection]:
    """Three synthetic subjects / admissions / stays with hand-placed events."""
    con = duckdb.connect()
    con.execute("CREATE SCHEMA mimiciv_hosp")
    con.execute("CREATE SCHEMA mimiciv_icu")
    con.execute(
        "CREATE TABLE mimiciv_hosp.patients (subject_id INTEGER, anchor_age INTEGER, "
        "anchor_year INTEGER, anchor_year_group VARCHAR)"
    )
    con.execute(
        "CREATE TABLE mimiciv_hosp.admissions (subject_id INTEGER, hadm_id INTEGER, "
        "admittime TIMESTAMP, dischtime TIMESTAMP)"
    )
    con.execute(
        "CREATE TABLE mimiciv_icu.icustays (subject_id INTEGER, hadm_id INTEGER, stay_id INTEGER, "
        "intime TIMESTAMP, outtime TIMESTAMP)"
    )
    con.execute(
        "CREATE TABLE mimiciv_hosp.labevents (subject_id INTEGER, hadm_id INTEGER, "
        "charttime TIMESTAMP, itemid INTEGER, valuenum DOUBLE, valueuom VARCHAR)"
    )
    con.execute(
        "CREATE TABLE mimiciv_icu.chartevents (subject_id INTEGER, hadm_id INTEGER, "
        "stay_id INTEGER, charttime TIMESTAMP, itemid INTEGER, valuenum DOUBLE, valueuom VARCHAR)"
    )
    for s, h, st, admit, disch, intime, outtime in (
        (
            S1,
            H1,
            ST1,
            "2150-01-01 00:00",
            "2150-01-10 00:00",
            "2150-01-02 00:00",
            "2150-01-05 00:00",
        ),
        (
            S2,
            H2,
            ST2,
            "2151-03-01 00:00",
            "2151-03-05 00:00",
            "2151-03-02 00:00",
            "2151-03-03 00:00",
        ),
        (
            S3,
            H3,
            ST3,
            "2152-06-01 00:00",
            "2152-06-03 00:00",
            "2152-06-01 06:00",
            "2152-06-02 06:00",
        ),
    ):
        con.execute(f"INSERT INTO mimiciv_hosp.patients VALUES ({s}, 50, 2150, '2014 - 2016')")
        con.execute(f"INSERT INTO mimiciv_hosp.admissions VALUES ({s}, {h}, '{admit}', '{disch}')")
        con.execute(
            f"INSERT INTO mimiciv_icu.icustays VALUES ({s}, {h}, {st}, '{intime}', '{outtime}')"
        )
    for hours, value in STAY1_HOURS:  # labevents.hadm_id NULL on purpose: subject-keyed
        con.execute(
            f"INSERT INTO mimiciv_hosp.labevents VALUES ({S1}, NULL, "
            f"{_ts('2150-01-02 00:00', hours)}, {CREATININE}, {value}, 'mg/dL')"
        )
    for itemid, hours, value in STAY2_EVENTS:
        con.execute(
            f"INSERT INTO mimiciv_hosp.labevents VALUES ({S2}, {H2}, "
            f"{_ts('2151-03-02 00:00', hours)}, {itemid}, {value}, 'x')"
        )
    # stay 3: a creatinine every hour for 6 hours, and a heart rate at 3 h
    for hour in range(6):
        con.execute(
            f"INSERT INTO mimiciv_hosp.labevents VALUES ({S3}, {H3}, "
            f"{_ts('2152-06-01 06:00', hour + 0.25)}, {CREATININE}, {1.0 + hour}, 'mg/dL')"
        )
    con.execute(
        f"INSERT INTO mimiciv_icu.chartevents VALUES ({S3}, {H3}, {ST3}, "
        f"{_ts('2152-06-01 06:00', 3)}, {HR}, 88, 'bpm')"
    )
    try:
        yield con
    finally:
        con.close()


def _hours(rel: duckdb.DuckDBPyRelation, stay_id: int) -> list[float]:
    df = rel.pl().filter(pl.col("stay_id") == stay_id).sort("event_time")
    return df.get_column("hours_since_anchor").to_list()


# ---------------------------------------------------------------------------
# 1. Alignment: signed hours, window edges, clip_to_stay, the join-key rule
# ---------------------------------------------------------------------------


def test_align_signed_hours_window_edges_and_clip(crafted: duckdb.DuckDBPyConnection) -> None:
    src = tl.labs([CREATININE])
    rel = tl.align(src, tl.ICU_IN, (-24, 72), "icustay", clip_to_stay=False, con=crafted)
    assert list(rel.columns) == ["stay_id", *tl.ALIGNED_COLUMNS]
    # [-24, 72): -25 out, -24 in, 72 out, 100 out; sub-hour offsets exact
    assert _hours(rel, ST1) == [-24.0, -0.5, 0.0, 1.0, 71.99]
    clipped = tl.align(src, tl.ICU_IN, (-24, 72), "icustay", clip_to_stay=True, con=crafted)
    assert _hours(clipped, ST1) == [0.0, 1.0, 71.99], "before intime and after outtime clipped"
    # the Python twin agrees with the SQL on every kept row
    df = clipped.pl()
    for row in df.iter_rows(named=True):
        assert row["hours_since_anchor"] == pytest.approx(
            timesem.hours_since(row["anchor_time"], row["event_time"])
        )
    assert df.get_column("source_table").unique().to_list() == ["mimiciv_hosp.labevents"]
    assert df.get_column("code").unique().to_list() == [str(CREATININE)]
    # sql_query() round-trips (what a run records)
    assert crafted.sql(clipped.sql_query()).pl().height == df.height


def test_align_on_hadm_and_subject_grains_and_refusals(crafted: duckdb.DuckDBPyConnection) -> None:
    src = tl.labs([CREATININE])
    hadm = tl.align(src, tl.HOSP_ADMIT, (0, 240), "hadm", clip_to_stay=True, con=crafted)
    df = hadm.pl()
    assert df.columns[:1] == ["hadm_id"]
    # subject 1 is admitted 24 h before the ICU: the -25 h lab is 1 h before admission
    # (out), the rest inside [admittime, dischtime) and under 240 h
    assert sorted(df.filter(pl.col("hadm_id") == H1).get_column("hours_since_anchor")) == [
        0.0,
        23.5,
        24.0,
        25.0,
        95.99,
        96.0,
        124.0,
    ]
    subject = tl.align(src, tl.HOSP_ADMIT, (-48, 48), "subject", clip_to_stay=False, con=crafted)
    assert subject.pl().columns[:1] == ["subject_id"]
    with pytest.raises(tl.TimelineError, match="clip_to_stay"):
        tl.align_sql(src, tl.HOSP_ADMIT, (-48, 48), "subject", True)
    with pytest.raises(tl.TimelineError, match="window"):
        tl.align_sql(src, tl.ICU_IN, (72, -24), "icustay", True)
    with pytest.raises(tl.TimelineError, match="timeline grain"):
        tl.align_sql(src, tl.ICU_IN, (-24, 72), "icu_day", True)
    with pytest.raises(timesem.GrainError):
        tl.align_sql(src, tl.ICU_IN, (-24, 72), "nope", True)
    with pytest.raises(timesem.GrainUnavailableError):
        tl.align_sql(src, tl.ICU_IN, (-24, 72), "edstay", True)


def test_anchor_registry_selectors_and_missing_anchors(crafted: duckdb.DuckDBPyConnection) -> None:
    assert list(tl.ANCHORS) == [
        "hosp_admit",
        "hosp_discharge",
        "icu_in",
        "icu_out",
        "first_culture",
        "first_antibiotic",
        "suspected_infection",
        "vent_start",
        "deterioration",
    ]
    with pytest.raises(tl.TimelineError, match="unknown anchor"):
        tl.anchor("nope")
    # icu_in on the hadm grain = the admission's first ICU intime; every unit is listed
    rel = crafted.sql(tl.anchor_sql(tl.ICU_IN, "hadm")).pl()
    assert list(rel.columns) == ["hadm_id", "anchor_time"]
    assert rel.height == 3 and rel.get_column("anchor_time").null_count() == 0
    # a custom anchor that only some units have -> NULL anchor_time for the rest
    custom = tl.custom_anchor(
        "first_lactate",
        "SELECT subject_id, hadm_id, CAST(NULL AS INTEGER) AS stay_id, charttime AS anchor_time "
        f"FROM mimiciv_hosp.labevents WHERE itemid = {LACTATE}",
        key="hadm_id",
    )
    rel = crafted.sql(tl.anchor_sql(custom, "icustay")).pl()
    assert rel.height == 3 and rel.get_column("anchor_time").null_count() == 2
    aligned = tl.align(tl.labs([CREATININE]), custom, (-6, 6), con=crafted).pl()
    assert set(aligned.get_column("stay_id").to_list()) == {ST2}, "no anchor -> no rows"
    assert sorted(aligned.get_column("hours_since_anchor").to_list()) == [-3.0, -2.5]
    # selector each / last on a crafted multi-event anchor
    each = tl.custom_anchor(
        "labs_each",
        "SELECT subject_id, hadm_id, CAST(NULL AS INTEGER) AS stay_id, charttime AS anchor_time "
        f"FROM mimiciv_hosp.labevents WHERE hadm_id = {H2}",
        key="hadm_id",
        selector="each",
    )
    assert crafted.sql(tl.anchor_sql(each, "hadm")).pl().filter(pl.col("hadm_id") == H2).height == 3
    last = tl.custom_anchor("labs_last", each.sql or "", key="hadm_id", selector="last")
    picked = crafted.sql(tl.anchor_sql(last, "hadm")).pl().filter(pl.col("hadm_id") == H2)
    assert picked.height == 1
    assert picked.get_column("anchor_time")[0].hour == 5
    with pytest.raises(tl.TimelineError, match="selector"):
        tl.Anchor("x", "hadm", "t", "c", selector="nope")  # type: ignore[arg-type]
    with pytest.raises(tl.TimelineError, match="kinds"):
        tl.vent_start(["Bicycle"])
    assert tl.vent_start(["InvasiveVent"]).label == "vent_start(kinds=InvasiveVent)"
    assert tl.procedure([225802, 224385]).label == "procedure(itemids=224385,225802)"


# ---------------------------------------------------------------------------
# 2. As-of: event_at, asof_join, window_join
# ---------------------------------------------------------------------------


def test_event_at_picks_last_prior_value_within_tolerance(
    crafted: duckdb.DuckDBPyConnection,
) -> None:
    src = tl.labs([CREATININE])
    at = tl.event_at(src, tl.ICU_IN, 6, con=crafted).pl()
    # stay 1: -0.5 h and 0 h are within 6 h; the event AT the anchor is the last one
    row = at.filter(pl.col("stay_id") == ST1)
    assert row.height == 1 and row.get_column("hours_since_anchor")[0] == 0.0
    assert row.get_column("value")[0] == 4.0
    assert ST2 not in at.get_column("stay_id").to_list(), "no prior value within tolerance"
    at48 = tl.event_at(src, tl.ICU_IN, 0.75, con=crafted).pl()
    assert at48.filter(pl.col("stay_id") == ST1).get_column("hours_since_anchor")[0] == 0.0
    # the ICU -24 h lab of stay 1 sits exactly at admission: "at" the hosp_admit anchor
    at_q = tl.event_at(src, tl.HOSP_ADMIT, 0.25, grain="hadm", con=crafted).pl()
    assert at_q.filter(pl.col("hadm_id") == H1).get_column("hours_since_anchor")[0] == 0.0
    # stay 2's results sit 22 h and 21.5 h before icu_out: the tolerance decides
    assert ST2 not in tl.event_at(src, tl.ICU_OUT, 21.4, con=crafted).pl()["stay_id"].to_list()
    out = tl.event_at(src, tl.ICU_OUT, 21.6, con=crafted).pl().filter(pl.col("stay_id") == ST2)
    assert out.get_column("hours_since_anchor")[0] == -21.5 and out.get_column("value")[0] == 12.0
    with pytest.raises(tl.TimelineError):
        tl.event_at_sql(src, tl.ICU_IN, 0)


def test_asof_and_window_joins(crafted: duckdb.DuckDBPyConnection) -> None:
    left = crafted.sql(tl.anchor_sql(tl.ICU_IN, "icustay"))
    right = crafted.sql(
        "SELECT i.stay_id, l.charttime, l.valuenum FROM mimiciv_hosp.labevents AS l "
        f"JOIN mimiciv_icu.icustays AS i USING (subject_id) WHERE l.itemid = {CREATININE}"
    )
    back = tl.asof_join(left, right, ["stay_id"], ("anchor_time", "charttime"), con=crafted).pl()
    assert back.filter(pl.col("stay_id") == ST1).get_column("valuenum")[0] == 4.0
    assert back.filter(pl.col("stay_id") == ST1).get_column("hours_since")[0] == 0.0
    fwd = tl.asof_join(
        left, right, ["stay_id"], ("anchor_time", "charttime"), "forward", con=crafted
    ).pl()
    assert fwd.filter(pl.col("stay_id") == ST2).get_column("hours_since")[0] == 2.0
    assert fwd.filter(pl.col("stay_id") == ST3).get_column("hours_since")[0] == 0.25
    tight = tl.asof_join(
        left, right, ["stay_id"], ("anchor_time", "charttime"), "forward", 1.0, con=crafted
    ).pl()
    assert set(tight.get_column("stay_id").to_list()) == {ST1, ST3}, "stay 2 is 2 h away"
    kept = tl.asof_join(
        left,
        right,
        ["stay_id"],
        ("anchor_time", "charttime"),
        "forward",
        1.0,
        con=crafted,
        how="left",
    ).pl()
    assert kept.height == 3
    assert kept.filter(pl.col("stay_id") == ST2).get_column("hours_since")[0] is None
    win = tl.window_join(
        left, right, ["stay_id"], ("anchor_time", "charttime"), 1, 2, con=crafted
    ).pl()
    assert sorted(win.filter(pl.col("stay_id") == ST1).get_column("hours_since")) == [
        -0.5,
        0.0,
        1.0,
    ]
    assert sorted(win.filter(pl.col("stay_id") == ST3).get_column("hours_since")) == [0.25, 1.25]
    with pytest.raises(tl.TimelineError):
        tl.asof_join_sql(left, right, [], "t")
    with pytest.raises(tl.TimelineError):
        tl.asof_join_sql(left, right, ["stay_id"], "t", "sideways")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 3. Bins: [start, end), sums, fill, daily, the population summary, to_mart
# ---------------------------------------------------------------------------


def test_hourly_bins_sum_to_raw_counts_and_fill_empty_bins(
    crafted: duckdb.DuckDBPyConnection,
) -> None:
    src = tl.labs([CREATININE])
    aligned = tl.align(src, tl.ICU_IN, (-24, 72), clip_to_stay=False, con=crafted)
    raw = aligned.pl()
    binned = tl.hourly_bins(aligned, 1.0, con=crafted).pl().sort(["stay_id", "bin_index"])
    assert list(binned.columns) == [
        "stay_id",
        "code",
        "bin_index",
        "bin_start_h",
        "bin_end_h",
        "n_value",
        "value_mean",
        "value_min",
        "value_max",
        "value_last",
    ]
    assert binned.get_column("n_value").sum() == raw.height, "bins sum to the raw count"
    one = binned.filter(pl.col("stay_id") == ST1)
    assert one.get_column("bin_index").to_list() == [-24, -1, 0, 1, 71], "[start, end) bins"
    assert one.filter(pl.col("bin_index") == 1).get_column("bin_start_h")[0] == 1.0
    two = binned.filter(pl.col("stay_id") == ST2)
    assert two.get_column("n_value").to_list() == [2] and two.get_column("value_last")[0] == 12.0
    assert two.get_column("value_mean")[0] == 11.0 and two.get_column("value_max")[0] == 12.0
    for row in binned.iter_rows(named=True):
        assert timesem.bin_bounds(row["bin_index"]) == (row["bin_start_h"], row["bin_end_h"])
    filled = tl.hourly_bins(aligned, 1.0, con=crafted, fill=True, window=(-24, 72)).pl()
    assert filled.height == 3 * 96, "every unit x code x bin of the window"
    empties = filled.filter(pl.col("n_value") == 0)
    assert empties.height == 3 * 96 - binned.height
    assert empties.get_column("value_mean").null_count() == empties.height
    assert filled.get_column("n_value").sum() == raw.height
    auto = tl.hourly_bins(aligned, 1.0, con=crafted, fill=True).pl()
    assert auto.get_column("bin_index").min() == -24 and auto.get_column("bin_index").max() == 71
    daily = tl.daily_bins(aligned, con=crafted).pl().sort(["stay_id", "bin_index"])
    assert daily.filter(pl.col("stay_id") == ST1).get_column("bin_index").to_list() == [-1, 0, 2]
    assert daily.get_column("n_value").sum() == raw.height
    custom = tl.hourly_bins(
        aligned, 6.0, {"value": ["count", "median", "first", "sum"]}, con=crafted
    ).pl()
    assert {"n_value", "value_median", "value_first", "value_sum"} <= set(custom.columns)
    with pytest.raises(tl.TimelineError, match="aggregation"):
        tl.hourly_bins_sql("SELECT 1", 1.0, {"value": ["mode"]})
    with pytest.raises(tl.TimelineError):
        tl.hourly_bins_sql("SELECT 1", 0)


def test_population_summary_releases_through_the_suppressor(
    crafted: duckdb.DuckDBPyConnection,
) -> None:
    aligned = tl.align(tl.labs([CREATININE]), tl.ICU_IN, (0, 6), con=crafted)
    binned = tl.hourly_bins(aligned, 1.0, con=crafted)
    raw = crafted.sql(tl.population_summary_sql(binned)).pl()
    assert list(raw.columns) == list(tl.SUMMARY_COLUMNS)
    assert "n_events" not in raw.columns, "one count column: no nested pair to derive"
    # stays 1 and 3 share bins 0 and 1 (2 units), stay 3 alone in 2..5, stay 2 in bin 2
    by_bin = {int(r["bin_index"]): int(r["n_units"]) for r in raw.iter_rows(named=True)}
    assert by_bin == {0: 2, 1: 2, 2: 2, 3: 1, 4: 1, 5: 1}
    released = tl.population_summary(binned, k=1)
    assert released.height == raw.height and released.columns == raw.columns
    two = tl.population_summary(binned, k=2)
    assert set(two.get_column("bin_index").to_list()) <= {0, 1, 2}
    assert min(two.get_column("n_units").to_list()) >= 2
    assert tl.population_summary(binned, k=3).height == 0
    via_sql = tl.population_summary(tl.hourly_bins_sql(aligned.sql_query(), 1.0), k=1, con=crafted)
    assert via_sql.equals(released)
    assert not disclose.check_frame(two, k=2), "the released frame passes the frame gate"
    with pytest.raises(tl.TimelineError):
        tl.population_summary(binned, k=0)
    with pytest.raises(tl.TimelineError, match="con="):
        tl.population_summary("SELECT 1", k=1)


def test_to_mart_writes_parquet_through_the_publisher(
    crafted: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    aligned = tl.align(tl.labs([CREATININE]), tl.ICU_IN, (-24, 72), con=crafted)
    binned = tl.hourly_bins(aligned, 1.0, con=crafted)
    dest = tmp_path / "marts" / "timeline_test.parquet"
    assert tl.to_mart(binned, dest) == dest and dest.is_file()
    assert not dest.with_name(dest.name + ".new").exists()
    back = pl.read_parquet(dest)
    assert back.height == binned.pl().height and back.columns == binned.columns
    tl.to_mart(binned, dest)  # a second publish swaps the file in place
    assert pl.read_parquet(dest).height == back.height
    with pytest.raises(tl.TimelineError, match="parquet"):
        tl.to_mart(binned, tmp_path / "x.csv")


# ---------------------------------------------------------------------------
# 4. The fixture lake: every anchor and preset compiles; the owner gate; the benchmark
# ---------------------------------------------------------------------------


def _all_anchors() -> list[tl.Anchor]:
    return [
        *tl.ANCHORS.values(),
        tl.med_start("vasopressors@1.0.0"),
        tl.med_start("vasopressors@1.0.0", "inputevents"),
        tl.procedure([225802]),
        tl.vent_start(),
        tl.phenotype_onset("sepsis3@1.0.0"),
        tl.phenotype_onset("sepsis_explicit@1.0.0"),
    ]


def test_every_anchor_compiles_on_the_fixture_catalog(
    fixture_lake_catalog: duckdb.DuckDBPyConnection,
) -> None:
    con = fixture_lake_catalog
    for a in _all_anchors():
        for grain in GRAINS:
            rel = con.sql(tl.anchor_sql(a, grain))
            assert list(rel.columns) == [*timesem.grain(grain).keys, "anchor_time"], (
                a.label,
                grain,
            )
            n_units, n_anchored = con.sql(
                f"SELECT count(*), count(anchor_time) FROM ({rel.sql_query()})"
            ).fetchone() or (0, 0)
            assert n_units > 0 and 0 <= n_anchored <= n_units, (a.label, grain)
    with pytest.raises(tl.TimelineError, match="drug set"):
        tl.med_start("labs_creatinine@1.0.0")
    with pytest.raises(tl.TimelineError, match="source must be"):
        tl.med_start("vasopressors@1.0.0", "emar")


def test_every_source_preset_aligns_on_the_fixture_catalog(
    fixture_lake_catalog: duckdb.DuckDBPyConnection,
) -> None:
    con = fixture_lake_catalog
    presets = [
        tl.labs([CREATININE, LACTATE]),
        tl.vitals([HR]),
        tl.inputs([221906]),
        tl.outputs([226559]),
        tl.meds("vasopressors@1.0.0"),
        tl.meds("vasopressors@1.0.0", "inputevents"),
        tl.procedures([225802]),
        tl.micro(),
        tl.transfers(),
        tl.custom_source(
            "hr",
            f"SELECT subject_id, hadm_id, stay_id, charttime AS event_time, itemid AS code, "
            f"valuenum AS value, valueuom FROM mimiciv_icu.chartevents WHERE itemid = {HR}",
        ),
    ]
    assert [name for name, *_ in tl.SOURCE_PRESETS] == [
        "labs",
        "vitals",
        "inputs",
        "outputs",
        "meds",
        "procedures",
        "micro",
        "transfers",
        "custom",
    ]
    for src in presets:
        for grain, anchor in (("icustay", tl.ICU_IN), ("hadm", tl.HOSP_ADMIT)):
            rel = tl.align(src, anchor, (-24, 72), grain, con=con)
            assert list(rel.columns) == [*timesem.grain(grain).keys, *tl.ALIGNED_COLUMNS], src.label
            count = con.sql(f"SELECT count(*) FROM ({rel.sql_query()})").fetchone()
            assert count is not None and count[0] >= 0
            widths = con.sql(
                f"SELECT max(length(code)), max(length(valueuom)) FROM ({rel.sql_query()})"
            ).fetchone() or (0, 0)
            assert (widths[0] or 0) <= tl.CODE_MAX_CHARS and (widths[1] or 0) <= tl.CODE_MAX_CHARS
    assert tl.CODE_MAX_CHARS == safe.FREE_TEXT_MAX_CHARS
    # the released frame of a preset pipeline passes the frame gate at k = 11
    binned = tl.hourly_bins(tl.align(presets[1], tl.ICU_IN, (0, 24), con=con), 1.0, con=con)
    released = tl.population_summary(binned, k=K)
    assert not disclose.check_frame(released, k=K)
    shown = released.get_column("n_units").drop_nulls()
    assert not ((shown > 0) & (shown < K)).any()


def test_stay_events_refuses_without_the_owner_connection(
    crafted: duckdb.DuckDBPyConnection,
    fixture_catalog: duckdb.DuckDBPyConnection,
    fixture_lake_catalog: duckdb.DuckDBPyConnection,
) -> None:
    assert tl.connection_role(crafted) is None
    assert tl.connection_role(fixture_catalog) is None, "the in-memory CSV catalog is unstamped"
    assert tl.connection_role(fixture_lake_catalog) == "agent", "open_catalog stamps the role"
    for con in (crafted, fixture_catalog, fixture_lake_catalog):
        with pytest.raises(PermissionError, match="owner-only"):
            tl.stay_events(ST1, [tl.labs([CREATININE])], conn=con)
    with pytest.raises(tl.TimelineError):
        tl.stay_events_sql([])


def test_stay_events_owner_path_on_the_fixture_lake_writes_one_audit_line(
    fixture_lake_settings: Settings,
) -> None:
    """Synthetic tier only (D-32): the frame is inspected for shape, never printed."""
    con = open_catalog("fixture", settings=fixture_lake_settings, role="owner")
    try:
        assert tl.connection_role(con) == "owner"
        assert con.execute(f"SELECT getvariable('{ROLE_VARIABLE}')").fetchone() == ("owner",)
        stay = con.execute("SELECT min(stay_id) FROM mimiciv_icu.icustays").fetchone()
        assert stay is not None and stay[0] >= 90_000_000
        audit = safe.audit_path(fixture_lake_settings)
        before = len(fsio.read_jsonl(audit)) if audit.is_file() else 0
        sources = [tl.labs([CREATININE, LACTATE]), tl.vitals([HR])]
        frame = tl.stay_events(int(stay[0]), sources, conn=con, settings=fixture_lake_settings)
    finally:
        con.close()
    assert frame.columns == ["lane", "stay_id", *tl.ALIGNED_COLUMNS]
    assert set(frame.get_column("lane").to_list()) <= {"labs", "vitals"}
    assert frame.get_column("stay_id").n_unique() <= 1
    assert frame.get_column("event_time").is_sorted()
    lines = fsio.read_jsonl(audit)
    assert len(lines) == before + 1
    line = safe.AuditLine.model_validate(lines[-1])
    assert line.actor == "owner" and line.allowed and line.tier == "fixture"
    assert (
        line.sql_text
        == "row_view:stay_events sources=labs(itemids=50813,50912),vitals(itemids=220045)"
    )
    assert str(stay[0]) not in line.sql_text, "the row selection is never recorded"
    assert line.n_rows == frame.height and line.rows_suppressed == 0 and line.k == K
    assert re.fullmatch(r"[0-9a-f]{64}", line.statement_sha256)


def test_benchmark_on_the_fixture_lake_writes_gated_exports(
    fixture_lake_settings: Settings,
) -> None:
    from mimicwarehouse import run as run_mod

    result = tl.run_benchmark("fixture", settings=fixture_lake_settings, doctor=False)
    assert RUN_ID_RE.fullmatch(result.run_id) and result.tier == "fixture"
    assert set(result.exports) == {
        f"{tl.BENCH_NAME}.parquet",
        f"{tl.BENCH_NAME}.md",
        f"{tl.BENCH_FIGURE}.csv",
        f"{tl.BENCH_FIGURE}.png",
    }
    manifest = run_mod.read_manifest(result.run_id, fixture_lake_settings)
    assert manifest.kind == "bench" and manifest.status == "ok"
    assert manifest.claim_type == tl.CLAIM_TYPE
    assert set(manifest.sql) == {"align", "hourly_bins", "population_summary"}
    assert manifest.tables[tl.BENCH_NAME] == f"tables/{tl.BENCH_NAME}.parquet"
    assert manifest.tables[f"{tl.BENCH_NAME}.md"] == f"exports/{tl.BENCH_NAME}.md"
    assert manifest.figures[f"{tl.BENCH_FIGURE}.png"] == f"exports/{tl.BENCH_FIGURE}.png"
    assert manifest.params["itemids"] == list(tl.BENCH_ITEMIDS)
    assert "core" in manifest.snapshot_ids
    for path in result.exports.values():
        assert disclose.check(path, K).passed, path.name
        assert disclose.verify(path).ok, path.name
        side = json.loads(disclose.sidecar_path(path).read_text(encoding="utf-8"))
        assert side["passed"] and side["k"] == K
    md = result.exports[f"{tl.BENCH_NAME}.md"].read_text(encoding="utf-8")
    assert "Claim type: exploratory" in md and tl.RETROSPECTIVE_SENTENCE in md
    assert "## Reproduction" in md and result.run_id in md
    assert md.isascii() and not BAND_TOKEN.search(md)
    table = pl.read_parquet(result.exports[f"{tl.BENCH_NAME}.parquet"])
    assert table.columns == list(tl.SUMMARY_COLUMNS)
    shown = table.get_column("n_units").drop_nulls()
    assert not ((shown > 0) & (shown < K)).any()
    assert result.exports[f"{tl.BENCH_FIGURE}.png"].read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    ledger = benchmarks.read(fixture_lake_settings)
    mine = ledger.filter((pl.col("run_id") == result.run_id) & (pl.col("kind") == tl.BENCH_KIND))
    assert mine.height == 1 and mine.get_column("step")[0] == tl.BENCH_NAME
    assert mine.get_column("wall_s")[0] == pytest.approx(result.wall_s)


def test_benchmark_refuses_a_failing_export(
    fixture_lake_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A gate refusal writes nothing under exports/ and fails the run."""
    from mimicwarehouse import run as run_mod

    def leaky(*args: Any, **kwargs: Any) -> str:
        return "# leak\n\n| step | n |\n|---|---|\n| a | 100 |\n| b | 96 |\n"

    monkeypatch.setattr(tl, "render_markdown", leaky)
    with pytest.raises(disclose.DisclosureError, match="nothing written"):
        tl.run_benchmark("fixture", settings=fixture_lake_settings, doctor=False)
    latest = run_mod.list_runs(fixture_lake_settings, kind="bench", last=1)[0]  # newest first
    assert latest["status"] == "failed"
    assert not (
        run_mod.run_dir(latest["run_id"], fixture_lake_settings) / tl.EXPORTS_DIRNAME
    ).exists()


# ---------------------------------------------------------------------------
# 5. CLI, docs, import budget
# ---------------------------------------------------------------------------


def test_cli_anchors_and_bench(fixture_lake_settings: Settings) -> None:
    runner = helpers.cli_runner()
    result = runner.invoke(app, ["timeline", "anchors", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert [a["name"] for a in payload] == list(tl.ANCHORS)
    result = runner.invoke(app, ["timeline", "anchors"])
    assert result.exit_code == 0 and "deterioration" in result.stdout
    root = str(fixture_lake_settings.data_root)
    result = runner.invoke(
        app, ["--data-root", root, "timeline", "bench", "--tier", "fixture", "--json"]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert RUN_ID_RE.fullmatch(payload["run_id"]) and len(payload["exports"]) == 4
    result = runner.invoke(app, ["--data-root", root, "timeline", "bench", "--tier", "nope"])
    assert result.exit_code == 2 and "unknown tier" in result.output
    result = runner.invoke(
        app, ["--data-root", root, "timeline", "bench", "--tier", "fixture", "--background"]
    )
    assert result.exit_code == 2 and "requires --job" in result.output
    result = runner.invoke(app, ["--data-root", root, "timeline", "bench", "--tier", "demo"])
    assert result.exit_code == 2 and "no demo catalog" in result.output


def test_methods_doc_is_in_sync(tmp_path: Path) -> None:
    page = tl.methods_doc_path()
    assert page.is_file()
    text = page.read_text(encoding="utf-8")
    copy = tmp_path / "timelines.md"
    copy.write_text(text, encoding="utf-8", newline="\n")
    tl.sync_methods_doc(copy)
    assert copy.read_text(encoding="utf-8") == text, "run: uv run python -m mimicwarehouse.timeline"
    for name in tl.ANCHORS:
        assert f"`{name}`" in text
    assert "MIMIC-IV analyses in this repository are retrospective" in text
    assert disclose.check(page, K).passed
    assert not BAND_TOKEN.search(text)


def test_import_budget() -> None:
    helpers.assert_import_budget(lazy=("mimicwarehouse.run", "mimicwarehouse.safe"))
    helpers.assert_import_budget(
        "mimicwarehouse.timeline",
        lazy=(
            "mimicwarehouse.run",
            "mimicwarehouse.safe",
            "mimicwarehouse.disclose",
            "mimicwarehouse.codesets.registry",
            "mimicwarehouse.phenotypes.registry",
        ),
    )


# ---------------------------------------------------------------------------
# 6. Dev / full tiers (aggregates only; stay_events is never called here)
# ---------------------------------------------------------------------------


@pytest.mark.tier("dev")
def test_dev_benchmark_pipeline_prints_the_released_summary(dev_catalog: Path) -> None:
    settings = config.load_settings()
    result = tl.run_benchmark("dev", settings=settings, doctor=False)
    assert result.n_rows > 0, "the dev tier releases bins above k"
    table = pl.read_parquet(result.exports[f"{tl.BENCH_NAME}.parquet"])
    shown = table.get_column("n_units").drop_nulls()
    assert not ((shown > 0) & (shown < K)).any()
    assert set(table.get_column("code").to_list()) <= {str(i) for i in tl.BENCH_ITEMIDS}
    for path in result.exports.values():
        assert disclose.verify(path).ok, path.name
    with pytest.raises(tl.TimelineError, match="refused"):
        tl.run_benchmark("dev", settings=settings, k=5, doctor=False)
    print(f"dev run {result.run_id}: wall {result.wall_s:.1f} s, {result.n_rows} released rows")
    print(table.head(8))


@pytest.mark.tier("full")
def test_full_benchmark_recorded(full_catalog: Path) -> None:
    settings = config.load_settings()
    ledger = benchmarks.read(settings)
    remedy = (
        "run `mwh timeline bench --tier full --background --job ep49-timeline-bench` (EP-49) first"
    )
    lines = ledger.filter(
        (pl.col("tier") == "full")
        & (pl.col("kind") == tl.BENCH_KIND)
        & (pl.col("step") == tl.BENCH_NAME)
    )
    assert lines.height >= 1, remedy
    run_id = str(lines.sort("ts").get_column("run_id")[-1])
    from mimicwarehouse import run as run_mod

    manifest = run_mod.read_manifest(run_id, settings)
    assert manifest.status == "ok" and manifest.tier == "full"
    exports = run_mod.run_dir(run_id, settings) / tl.EXPORTS_DIRNAME
    for name in (f"{tl.BENCH_NAME}.parquet", f"{tl.BENCH_NAME}.md", f"{tl.BENCH_FIGURE}.png"):
        assert disclose.verify(exports / name).ok, name
    print(f"full run {run_id}: wall {lines.get_column('wall_s')[-1]:.1f} s")
