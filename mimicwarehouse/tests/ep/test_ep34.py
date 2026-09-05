"""EP-34 — time semantics + unit-of-analysis registry.

Fixture tier (default): the era mapping for all five labels (Python + SQL); age cap
detection on a crafted frame (Python and the SQL twin agree); relative-time signs and the
``[start, end)`` bin boundaries; the censoring-horizon math; the ICD version rule; every
available grain's SQL fragments compile against the in-memory fixture catalog and count
what they claim; the ``CATALOG_EXTENSIONS`` hook runs inside ``build_catalog`` (a crafted
extension is called with the build connection; a mini lake without ``icustays`` gets the
skip path, never an empty view); the runner-built ``fixture.duckdb`` carries both
``mimiciv_derived`` views and ``meta.grains``; ``mwh catalog info`` lists them and
``safe_query`` reads the registry; the tracer cites ``timesem`` and its numbers are
count-for-count identical to the pre-EP-34 inlined SQL; the module reads no calendar
function outside ``age_at``; the methods page is in sync with the registry.

``tier("dev")``-marked: both views exist in the real dev catalog, ``meta.grains`` too,
``SELECT era_index, count(*) … GROUP BY 1`` through ``safe_query`` returns exactly five
eras, and the tracer's attrition + descriptives on dev equal the legacy chain's
count-for-count. Everything asserted or printed is counts, schemas, SQL text and
metadata — the fixture data is synthetic (ids >= 90 000 000); the dev tests see only
k-suppressed aggregates through ``safe_query``.
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

import helpers
from mimicwarehouse import config, timesem, tracer
from mimicwarehouse.catalog import build as build_mod
from mimicwarehouse.catalog.build import build_catalog
from mimicwarehouse.catalog.connect import open_catalog
from mimicwarehouse.cli import app
from mimicwarehouse.safe import safe_query
from mimicwarehouse.timesem import (
    AGE_CAP,
    CENSORING_RULES,
    DOD_VISIBILITY_DAYS,
    ERAS,
    GRAINS,
    INDEX_RULES,
    CensoringRule,
    GrainError,
    GrainUnavailableError,
    UnknownEraError,
    UnknownIndexRuleError,
    age_at,
    age_band,
    bin_bounds,
    era_of,
    follow_up_end,
    hour_bin,
    hours_since,
    icd_versions_of_hadm,
    is_age_capped,
    sql_age_at,
    sql_age_band,
    sql_era_index,
    sql_hour_bin,
    sql_hours_since,
    sql_icd_versions,
    sql_is_age_capped,
)

if TYPE_CHECKING:
    import duckdb as duckdb_mod

    from mimicwarehouse.config import Settings
    from mimicwarehouse.schema.contract import Contract

pytestmark = pytest.mark.ep_34

HOSP = "mimiciv_hosp"
ICU = "mimiciv_icu"

#: The tracer's WITH clause exactly as EP-31 shipped it (comments dropped) — the "before"
#: side of the EP-33 amendment's count-for-count acceptance. Frozen on purpose: it must
#: never be regenerated from timesem, or the comparison would be circular.
LEGACY_TRACER_CTE = """WITH base AS (
    SELECT
        i.subject_id,
        i.stay_id,
        i.intime,
        i.first_careunit,
        a.admittime,
        a.dischtime,
        a.admission_type,
        a.hospital_expire_flag,
        p.gender,
        p.anchor_age,
        p.anchor_year,
        p.anchor_year_group
    FROM mimiciv_icu.icustays AS i
    JOIN mimiciv_hosp.admissions AS a ON i.hadm_id = a.hadm_id
    JOIN mimiciv_hosp.patients AS p ON i.subject_id = p.subject_id
),
first_stay AS (
    SELECT * EXCLUDE (stay_rank)
    FROM (
        SELECT
            base.*,
            row_number() OVER (PARTITION BY subject_id ORDER BY intime, stay_id)
                AS stay_rank
        FROM base
    )
    WHERE stay_rank = 1
),
adult AS (
    SELECT *
    FROM first_stay
    WHERE anchor_age + (year(admittime) - anchor_year) >= 18
),
complete AS (
    SELECT *
    FROM adult
    WHERE dischtime IS NOT NULL AND hospital_expire_flag IS NOT NULL
),
cohort AS (
    SELECT
        least(anchor_age + (year(admittime) - anchor_year), 91) AS age_at_admit,
        gender,
        admission_type,
        first_careunit,
        anchor_year_group,
        hospital_expire_flag
    FROM complete
)"""

LEGACY_AGE_BAND_CASE = (
    "CASE WHEN age_at_admit < 40 THEN '18-39' WHEN age_at_admit < 65 THEN '40-64' "
    "WHEN age_at_admit < 80 THEN '65-79' ELSE '80+' END"
)


@pytest.fixture(scope="session")
def fixture_manifest(fixture_root: Path) -> dict[str, Any]:
    """``tests/fixtures/manifest.json`` — the churn-rule source of expected counts."""
    return json.loads((fixture_root / "manifest.json").read_text(encoding="utf-8"))


def _rows(manifest: dict[str, Any], contract: Contract, schema: str, table: str) -> int:
    csv = f"mimic-iv-3.1/{contract.table(schema, table).csv_path}"
    return int(manifest["files"][csv]["rows"])


def _scalar(con: duckdb_mod.DuckDBPyConnection, sql: str) -> Any:
    row = con.execute(sql).fetchone()
    assert row is not None
    return row[0]


# ---------------------------------------------------------------------------
# 1. Eras: all five labels, Python and SQL
# ---------------------------------------------------------------------------


def test_era_mapping_for_all_five_labels() -> None:
    assert len(ERAS) == 5 and list(ERAS) == sorted(ERAS)
    for index, label in enumerate(ERAS):
        era = era_of(label)
        assert era.index == index and era.label == label
        assert era.end_year - era.start_year == 2, "every era spans three years"
        assert list(era.years) == [era.start_year, era.start_year + 1, era.end_year]
        assert era_of(label.replace(" - ", "-")) == era, (
            "whitespace around the hyphen is normalised"
        )
    assert era_of("2008 - 2010").start_year == 2008 and era_of("2020 - 2022").end_year == 2022
    assert [e.start_year for e in timesem.ERA_TABLE] == sorted(
        e.start_year for e in timesem.ERA_TABLE
    )
    for bad in ("2023 - 2025", "2008", "", "2008 - 2011"):
        with pytest.raises(UnknownEraError):
            era_of(bad)


def test_sql_era_index_matches_python_on_fixture(
    fixture_catalog: duckdb_mod.DuckDBPyConnection,
) -> None:
    rows = fixture_catalog.execute(
        f"SELECT anchor_year_group, {sql_era_index('anchor_year_group')} AS era_index, count(*) "
        f"FROM {HOSP}.patients GROUP BY 1, 2 ORDER BY 1"
    ).fetchall()
    assert rows, "the fixture carries anchor_year_group labels"
    for label, index, _n in rows:
        assert index == era_of(label).index
    assert {r[1] for r in rows} == set(range(5)), "the fixture spans all five eras"
    assert (
        _scalar(fixture_catalog, f"SELECT {sql_era_index(chr(39) + '1999 - 2001' + chr(39))}")
        is None
    )


# ---------------------------------------------------------------------------
# 2. Ages: the anchor rule, the 91 cap, the bands (Python == SQL on a crafted frame)
# ---------------------------------------------------------------------------


def test_age_cap_detection_on_crafted_frame() -> None:
    from mimicwarehouse.engine import open_duckdb

    crafted = [
        # anchor_age, anchor_year, event year -> expected age, capped?
        (45, 2150, 2153, 48.0, False),
        (88, 2150, 2152, 90.0, False),
        (88, 2150, 2153, 91.0, True),  # reaches the sentinel value: indistinguishable
        (91, 2150, 2150, 91.0, True),  # shipped as 91: the >= 89 bucket
        (91, 2150, 2160, 101.0, True),
        (17, 2150, 2149, 16.0, False),  # an event before the anchor year
    ]
    for anchor_age, anchor_year, year, expected, capped in crafted:
        age = age_at(anchor_age, anchor_year, datetime(year, 6, 1, 12, 0))
        assert age == expected and isinstance(age, float)
        assert is_age_capped(age) is capped
    assert AGE_CAP == 91
    assert age_band(18) == "18-39" and age_band(39.9) == "18-39" and age_band(40) == "40-64"
    assert age_band(80) == "80+" and age_band(91) == "80+"
    assert sql_age_band("age_at_admit") == LEGACY_AGE_BAND_CASE

    con = open_duckdb("app")
    try:
        con.execute(
            "CREATE TABLE crafted (anchor_age INTEGER, anchor_year INTEGER, event TIMESTAMP)"
        )
        con.executemany(
            "INSERT INTO crafted VALUES (?, ?, ?)",
            [[a, y, datetime(e, 6, 1, 12, 0)] for a, y, e, _, _ in crafted],
        )
        uncapped = sql_age_at("anchor_age", "anchor_year", "event")
        capped = sql_age_at("anchor_age", "anchor_year", "event", cap=True)
        assert uncapped == "anchor_age + (year(event) - anchor_year)"
        assert capped == f"least({uncapped}, 91)"
        rows = con.execute(
            f"SELECT {uncapped}, {capped}, {sql_is_age_capped(uncapped)}, "
            f"{sql_age_band(capped)} FROM crafted ORDER BY rowid"
        ).fetchall()
    finally:
        con.close()
    for (age, age_capped, flag, band), (_, _, _, expected, capped_expected) in zip(
        rows, crafted, strict=True
    ):
        assert float(age) == expected
        assert float(age_capped) == min(expected, AGE_CAP)
        assert bool(flag) is capped_expected
        assert band == age_band(min(expected, AGE_CAP))


# ---------------------------------------------------------------------------
# 3. Relative time: signs and [start, end) bins (Python == SQL)
# ---------------------------------------------------------------------------


def test_relative_time_signs_and_bin_boundaries() -> None:
    from mimicwarehouse.engine import open_duckdb

    anchor = datetime(2150, 1, 1, 12, 0)
    assert hours_since(anchor, anchor + timedelta(hours=3)) == 3.0
    assert hours_since(anchor, anchor - timedelta(minutes=30)) == -0.5
    assert timesem.days_since(anchor, anchor + timedelta(days=1, hours=12)) == 1.5

    # [start, end): the right edge belongs to the next bin; negatives to negative bins
    assert [hour_bin(h) for h in (0.0, 0.999, 1.0, 1.5, 23.99, 24.0)] == [0, 0, 1, 1, 23, 24]
    assert [hour_bin(h) for h in (-0.001, -0.5, -1.0, -1.5)] == [-1, -1, -1, -2]
    assert [hour_bin(h, 6) for h in (0, 5.99, 6, 11.99, 12)] == [0, 0, 1, 1, 2]
    assert bin_bounds(0) == (0.0, 1.0) and bin_bounds(-1, 6) == (-6.0, 0.0)
    for width in (1.0, 6.0, 24.0):
        for h in (-7.5, -1.0, -0.25, 0.0, 0.5, 5.999, 6.0, 47.99, 48.0):
            start, end = bin_bounds(hour_bin(h, width), width)
            assert start <= h < end
    with pytest.raises(ValueError):
        hour_bin(1.0, 0)

    # date_diff('second') counts whole-second boundaries; the Python twin does the same
    assert timesem.seconds_between(anchor, anchor + timedelta(seconds=1, microseconds=999)) == 1
    # one microsecond before the anchor already crosses the anchor's second boundary
    assert timesem.seconds_between(anchor, anchor - timedelta(microseconds=1)) == -1
    assert hours_since(anchor, anchor + timedelta(seconds=1799)) == 1799 / 3600

    con = open_duckdb("app")
    try:
        con.execute("CREATE TABLE t (anchor TIMESTAMP, event TIMESTAMP)")
        # whole-second offsets, in minutes: before / at / after the anchor, on and off
        # the bin edges of the 1 h and 6 h widths
        offsets = [-90.0, -30.0, -0.05, 0.0, 0.5, 59.95, 60.0, 90.0, 360.0, 1440.0]
        con.executemany(
            "INSERT INTO t VALUES (?, ?)",
            [[anchor, anchor + timedelta(minutes=m)] for m in offsets],
        )
        rows = con.execute(
            f"SELECT {sql_hours_since('anchor', 'event')}, "
            f"{sql_hour_bin(sql_hours_since('anchor', 'event'))}, "
            f"{sql_hour_bin(sql_hours_since('anchor', 'event'), 6)}, "
            f"{timesem.sql_days_since('anchor', 'event')}, "
            f"{timesem.HOURS_BEFORE_DISCHARGE.sql('event', 'anchor')} FROM t ORDER BY rowid"
        ).fetchall()
    finally:
        con.close()
    for (hours, b1, b6, days, before), minutes in zip(rows, offsets, strict=True):
        expected = hours_since(anchor, anchor + timedelta(minutes=minutes))
        assert abs(float(hours) - expected) < 1e-6
        assert b1 == hour_bin(expected) and b6 == hour_bin(expected, 6)
        assert abs(float(days) - expected / 24.0) < 1e-9
        assert abs(float(before) + expected) < 1e-6, "'before' is the negated 'since'"
    assert set(timesem.RELATIVE_TIMES) == {
        "hours_since_icu_intime",
        "hours_since_hosp_admit",
        "hours_before_discharge",
    }
    assert timesem.HOURS_SINCE_ICU_INTIME.sql("charttime") == sql_hours_since("intime", "charttime")


# ---------------------------------------------------------------------------
# 4. Censoring: horizon math, the dod visibility end, competing events
# ---------------------------------------------------------------------------


def test_censoring_horizon_math() -> None:
    from mimicwarehouse.engine import open_duckdb

    index = datetime(2150, 3, 1, 8, 0)
    last_discharge = datetime(2150, 3, 20, 14, 0)
    visibility_end = last_discharge + timedelta(days=DOD_VISIBILITY_DAYS)
    assert DOD_VISIBILITY_DAYS == 365
    assert timesem.dod_visibility_end(last_discharge) == visibility_end

    rules = CENSORING_RULES
    assert set(rules) == {"in_hospital_mortality", "mortality_30d", "mortality_90d", "mortality_1y"}
    in_hospital = rules["in_hospital_mortality"]
    assert not in_hospital.censored and in_hospital.censor_time(index, last_discharge) is None
    assert in_hospital.sql_censor_time("index_time", "last_dischtime") is None
    assert in_hospital.competing_events == ("discharge_alive",)
    for name, days in (("mortality_30d", 30), ("mortality_90d", 90), ("mortality_1y", 365)):
        rule = rules[name]
        assert rule.horizon_days == days and rule.censored and rule.competing_events == ()
        # the horizon never reaches past the visibility end from an anchor before the
        # last discharge, so the anchor + horizon side wins here
        assert rule.censor_time(index, last_discharge) == index + timedelta(days=days)
    # a longer horizon (or an anchor after the last discharge) hits the dod horizon
    two_years = replace(rules["mortality_1y"], outcome="mortality_2y", horizon_days=730)
    assert two_years.censor_time(index, last_discharge) == visibility_end
    late_anchor = last_discharge + timedelta(days=350)
    assert rules["mortality_30d"].censor_time(late_anchor, last_discharge) == visibility_end
    assert follow_up_end(last_discharge, 30) == last_discharge + timedelta(days=30)
    assert follow_up_end(last_discharge, 400) == visibility_end
    assert follow_up_end(last_discharge, 730, anchor=index) == visibility_end
    with pytest.raises(ValueError):
        follow_up_end(last_discharge, -1)
    with pytest.raises(ValueError):
        CensoringRule("bad", 30, "admittime")  # type: ignore[arg-type]

    con = open_duckdb("app")
    try:
        con.execute("CREATE TABLE t (index_time TIMESTAMP, last_dischtime TIMESTAMP)")
        con.execute(
            "INSERT INTO t VALUES (?, ?), (?, ?)",
            [index, last_discharge, late_anchor, last_discharge],
        )
        sql = rules["mortality_30d"].sql_censor_time("index_time", "last_dischtime")
        assert sql is not None
        rows = con.execute(f"SELECT {sql} FROM t ORDER BY index_time").fetchall()
        rows_2y = con.execute(
            f"SELECT {two_years.sql_censor_time('index_time', 'last_dischtime')} FROM t "
            "ORDER BY index_time"
        ).fetchall()
    finally:
        con.close()
    assert [r[0] for r in rows] == [index + timedelta(days=30), visibility_end]
    assert [r[0] for r in rows_2y] == [visibility_end, visibility_end]


# ---------------------------------------------------------------------------
# 5. ICD versions: per row, never by date (Python == SQL on the fixture)
# ---------------------------------------------------------------------------


def test_icd_version_rule(fixture_catalog: duckdb_mod.DuckDBPyConnection) -> None:
    assert icd_versions_of_hadm([]) is None
    assert icd_versions_of_hadm([9, 9]) == "icd9"
    assert icd_versions_of_hadm([10]) == "icd10"
    assert icd_versions_of_hadm([9, 10]) == "mixed"
    assert icd_versions_of_hadm([9, None]) == "mixed"
    grouped = fixture_catalog.execute(
        f"SELECT hadm_id, {sql_icd_versions('icd_version')} AS label, "
        f"list(icd_version) AS versions FROM {HOSP}.diagnoses_icd GROUP BY hadm_id"
    ).fetchall()
    assert grouped, "the fixture carries diagnoses"
    labels = {label for _, label, _ in grouped}
    assert labels <= set(timesem.ICD_VERSION_LABELS)
    assert {"icd9", "icd10"} <= labels, "the fixture spans both coding systems (by era)"
    for _hadm, label, versions in grouped:
        assert label == icd_versions_of_hadm(versions)


# ---------------------------------------------------------------------------
# 6. Grain registry: shape, refusals, every fragment compiles and counts what it claims
# ---------------------------------------------------------------------------


def test_grain_registry_shape() -> None:
    assert list(GRAINS) == [
        "subject",
        "hadm",
        "icustay",
        "icu_day",
        "hour_bin",
        "person_time",
        "edstay",
        "note",
    ]
    assert INDEX_RULES == (
        "first_icu_stay",
        "first_hadm",
        "each_hadm",
        "each_icustay",
        "first_icu_stay_of_first_hadm",
    )
    placeholders = {g.name: g.available_from for g in GRAINS.values() if not g.available}
    assert placeholders == {"edstay": "EP-142", "note": "EP-148"}
    assert {g.name for g in timesem.available_grains()} == set(GRAINS) - set(placeholders)
    assert GRAINS["subject"].keys_sql() == "subject_id"
    assert GRAINS["icu_day"].keys_sql() == "stay_id, day_index"
    assert GRAINS["hour_bin"].keys_sql() == "stay_id, hour_bin"
    assert GRAINS["person_time"].keys_sql() == "subject_id, interval_start, interval_end"
    for g in GRAINS.values():
        assert g.default_index_rule is None or g.default_index_rule == g.index_rules[0]
        assert re.fullmatch(r"EP-\d+", g.available_from)
    with pytest.raises(GrainUnavailableError, match="EP-142"):
        GRAINS["edstay"].index_event_sql()
    with pytest.raises(GrainUnavailableError, match="EP-148"):
        GRAINS["note"].index_event_sql()
    with pytest.raises(GrainError, match="no index-event template"):
        GRAINS["person_time"].index_event_sql()
    with pytest.raises(UnknownIndexRuleError):
        GRAINS["hadm"].index_event_sql("first_icu_stay")
    with pytest.raises(UnknownIndexRuleError):
        timesem.index_event_sql("last_icu_stay")
    with pytest.raises(GrainError):
        timesem.grain("visit")
    assert timesem.grain("icustay").default_index_rule == "each_icustay"


def test_grain_fragments_compile_and_count_on_the_fixture(
    fixture_catalog: duckdb_mod.DuckDBPyConnection,
    fixture_manifest: dict[str, Any],
    contract: Contract,
) -> None:
    con = fixture_catalog
    admissions = _rows(fixture_manifest, contract, HOSP, "admissions")
    icustays = _rows(fixture_manifest, contract, ICU, "icustays")
    subjects_with_hadm = _scalar(con, f"SELECT count(DISTINCT subject_id) FROM {HOSP}.admissions")
    subjects_with_stay = _scalar(con, f"SELECT count(DISTINCT subject_id) FROM {ICU}.icustays")

    def count(sql: str) -> int:
        return int(_scalar(con, f"SELECT count(*) FROM ({sql}) AS q"))

    # every available grain x every applicable rule compiles; the fragments are
    # deterministic text and reference schema-qualified tables only
    for g in timesem.available_grains():
        for rule in g.index_rules:
            sql = g.index_event_sql(rule)
            assert sql == g.index_event_sql(rule)
            assert "FROM mimiciv_" in sql, "schema-qualified base tables only"
            assert count(sql) >= 0
        if g.index_rules:
            assert g.index_event_sql() == g.index_event_sql(g.default_index_rule)

    hadm, stay = GRAINS["hadm"], GRAINS["icustay"]
    assert count(hadm.index_event_sql("each_hadm")) == admissions
    assert count(hadm.index_event_sql("first_hadm")) == subjects_with_hadm
    assert count(stay.index_event_sql("each_icustay")) == icustays
    assert count(stay.index_event_sql("first_icu_stay")) == subjects_with_stay
    first_of_first = count(stay.index_event_sql("first_icu_stay_of_first_hadm"))
    assert 0 < first_of_first <= subjects_with_stay
    # the subject grain yields one row per subject under both of its rules
    assert count(GRAINS["subject"].index_event_sql("first_hadm")) == subjects_with_hadm
    assert count(GRAINS["subject"].index_event_sql("first_icu_stay")) == subjects_with_stay
    # first_icu_stay is the tracer's window: the same rows as its first_stay CTE
    assert count(stay.index_event_sql("first_icu_stay")) == count(
        f"{LEGACY_TRACER_CTE} SELECT 1 FROM first_stay"
    )
    # the expansions: one row per [start, end) bin, numbered from 0, bins start inside
    # the stay, sizes equal the ceiling of the stay length in the bin unit
    expected_days = int(
        _scalar(
            con,
            f"SELECT sum(CAST(ceil(date_diff('second', intime, outtime) / 86400.0) AS INTEGER)) "
            f"FROM {ICU}.icustays WHERE outtime IS NOT NULL",
        )
    )
    expected_hours = int(
        _scalar(
            con,
            f"SELECT sum(CAST(ceil(date_diff('second', intime, outtime) / 3600.0) AS INTEGER)) "
            f"FROM {ICU}.icustays WHERE outtime IS NOT NULL",
        )
    )
    day_sql = GRAINS["icu_day"].index_event_sql()
    hour_sql = GRAINS["hour_bin"].index_event_sql()
    assert count(day_sql) == expected_days and expected_days > 0
    assert count(hour_sql) == expected_hours and expected_hours > expected_days
    probe = con.execute(
        f"SELECT min(day_index), count(*) FILTER (WHERE bin_start < end_time), "
        f"count(*) FILTER (WHERE bin_end = bin_start + INTERVAL 1 DAY), "
        f"count(*) FILTER (WHERE bin_start = index_time + to_days(day_index)), count(*) "
        f"FROM ({day_sql}) AS q"
    ).fetchone()
    assert probe is not None
    assert probe[0] == 0 and probe[1] == probe[2] == probe[3] == probe[4]
    per_stay = _scalar(con, f"SELECT count(DISTINCT stay_id) FROM ({day_sql}) AS q")
    assert per_stay == _scalar(
        con, f"SELECT count(*) FROM {ICU}.icustays WHERE outtime IS NOT NULL AND outtime > intime"
    )
    # a first-stay expansion is a subset of the each-stay expansion
    assert count(GRAINS["icu_day"].index_event_sql("first_icu_stay")) <= expected_days


# ---------------------------------------------------------------------------
# 7. The catalog extension hook + the views on the runner-built fixture catalog
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def mini_lake(tmp_path_factory: pytest.TempPathFactory) -> Settings:
    """A fixture-tier lake without icustays / diagnoses_icd — the skip path of
    ``create_views`` (separate from the session lake so rebuilding its catalog never
    disturbs ``fixture_lake_catalog``)."""
    from mimicwarehouse.config import Settings
    from mimicwarehouse.dag import runner
    from mimicwarehouse.dag.spec import load_dag

    root = tmp_path_factory.mktemp("ep34-mini")
    settings = Settings(data_root=root)
    result = runner.run(
        load_dag(),
        "fixture",
        select=["stage.mimiciv_hosp.patients", "stage.mimiciv_hosp.admissions"],
        settings=settings,
    )
    assert result.ok, [s.error for s in result.steps]
    return settings


def _objects(con: duckdb_mod.DuckDBPyConnection, *schemas: str) -> dict[str, str]:
    rows = con.execute(
        "SELECT table_schema || '.' || table_name, table_type FROM information_schema.tables "
        "WHERE table_schema IN (SELECT unnest(?))",
        [list(schemas)],
    ).fetchall()
    return {str(name): str(kind) for name, kind in rows}


def test_catalog_extension_hook_runs_inside_build(
    mini_lake: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, bool, bool]] = []

    def recorder(con: Any, tier: str) -> None:
        # the build connection is open and already holds the contract + meta tables
        info = con.execute("SELECT count(*) FROM meta.catalog_info").fetchone()[0]
        has_patients = f"{HOSP}.patients" in _objects(con, HOSP)
        calls.append((tier, info == 1, has_patients))
        con.execute("CREATE TABLE meta.ep34_probe AS SELECT 1 AS one")

    assert build_mod.CATALOG_EXTENSIONS[0] is timesem.create_views, "registered first"
    monkeypatch.setattr(build_mod, "CATALOG_EXTENSIONS", [*build_mod.CATALOG_EXTENSIONS, recorder])
    lake = mini_lake.lake_root("fixture")
    result = build_catalog("fixture", mini_lake, lake_root=lake)
    assert calls == [("fixture", True, True)]
    con = open_catalog("fixture", settings=mini_lake)
    try:
        objects = _objects(con, "meta", "mimiciv_derived")
        assert "meta.ep34_probe" in objects and "meta.grains" in objects
        # icustays is not staged: icustay_index is skipped, never created empty;
        # diagnoses_icd is missing too, so hadm_era is skipped as well
        assert "mimiciv_derived.icustay_index" not in objects
        assert "mimiciv_derived.hadm_era" not in objects
        assert _scalar(con, "SELECT count(*) FROM meta.grains") == len(GRAINS)
    finally:
        con.close()
    assert result.path.is_file()

    # a failing extension fails the build by name and leaves no .new behind
    def broken(con: Any, tier: str) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(build_mod, "CATALOG_EXTENSIONS", [broken])
    with pytest.raises(build_mod.CatalogBuildError, match="broken"):
        build_catalog("fixture", mini_lake, lake_root=lake)
    assert not (result.path.with_name(result.path.name + ".new")).exists()


def test_fixture_lake_catalog_has_views_and_registry(
    fixture_lake_catalog: duckdb_mod.DuckDBPyConnection,
    fixture_manifest: dict[str, Any],
    contract: Contract,
) -> None:
    con = fixture_lake_catalog
    objects = _objects(con, "meta", "mimiciv_derived")
    assert objects.get("mimiciv_derived.hadm_era") == "VIEW"
    assert objects.get("mimiciv_derived.icustay_index") == "VIEW"
    assert objects.get("meta.grains") == "BASE TABLE"

    admissions = _rows(fixture_manifest, contract, HOSP, "admissions")
    icustays = _rows(fixture_manifest, contract, ICU, "icustays")
    assert _scalar(con, "SELECT count(*) FROM mimiciv_derived.hadm_era") == admissions
    described = [d[0] for d in con.execute("DESCRIBE mimiciv_derived.hadm_era").fetchall()]
    assert described == [
        "subject_id",
        "hadm_id",
        "anchor_year_group",
        "era_index",
        "age_at_admit",
        "age_capped",
        "icd_versions",
    ]
    probe = con.execute(
        "SELECT count(*) FILTER (WHERE era_index BETWEEN 0 AND 4), "
        "count(*) FILTER (WHERE age_at_admit <= 91), "
        "count(*) FILTER (WHERE age_capped = (age_at_admit >= 91)), "
        "count(*) FILTER (WHERE icd_versions IN ('icd9', 'icd10', 'mixed') "
        "OR icd_versions IS NULL), "
        "count(DISTINCT era_index), count(*) FROM mimiciv_derived.hadm_era"
    ).fetchone()
    assert probe is not None
    assert probe[0] == probe[1] == probe[2] == probe[3] == probe[5] == admissions
    assert probe[4] == 5, "the fixture spans all five eras"
    # every hadm_era row agrees with the Python twins
    rows = con.execute(
        "SELECT h.anchor_year_group, h.era_index, h.age_at_admit, h.age_capped, "
        "p.anchor_age, p.anchor_year, a.admittime "
        f"FROM mimiciv_derived.hadm_era h JOIN {HOSP}.patients p USING (subject_id) "
        f"JOIN {HOSP}.admissions a USING (hadm_id)"
    ).fetchall()
    for label, index, age_capped, flag, anchor_age, anchor_year, admittime in rows:
        raw = age_at(anchor_age, anchor_year, admittime)
        assert index == era_of(label).index
        assert float(age_capped) == min(raw, AGE_CAP) and bool(flag) is is_age_capped(raw)

    assert _scalar(con, "SELECT count(*) FROM mimiciv_derived.icustay_index") == icustays
    described = [d[0] for d in con.execute("DESCRIBE mimiciv_derived.icustay_index").fetchall()]
    assert described == [
        "stay_id",
        "hadm_id",
        "subject_id",
        "intime",
        "outtime",
        "icu_seq_in_hadm",
        "icu_seq_in_subject",
        "first_icu_stay_in_hadm",
        "first_icu_stay_of_subject",
    ]
    flags = con.execute(
        "SELECT count(*) FILTER (WHERE first_icu_stay_of_subject), "
        "count(*) FILTER (WHERE first_icu_stay_in_hadm), "
        "count(DISTINCT subject_id), count(DISTINCT hadm_id), "
        "count(*) FILTER (WHERE first_icu_stay_of_subject AND NOT first_icu_stay_in_hadm) "
        "FROM mimiciv_derived.icustay_index"
    ).fetchone()
    assert flags is not None
    assert flags[0] == flags[2], "exactly one first stay per subject"
    assert flags[1] == flags[3], "exactly one first stay per admission"
    assert flags[4] == 0, "a subject's first stay is the first of its admission"
    # the flag equals the tracer's rule / the registry's first_icu_stay template
    assert flags[0] == _scalar(
        con, f"SELECT count(*) FROM ({GRAINS['icustay'].index_event_sql('first_icu_stay')}) q"
    )

    registry = con.execute(
        "SELECT name, keys, available, available_from, default_index_rule FROM meta.grains "
        "ORDER BY rowid"
    ).fetchall()
    assert [r[0] for r in registry] == list(GRAINS)
    for name, keys, available, available_from, default_rule in registry:
        g = GRAINS[name]
        assert keys == g.keys_sql() and available is g.available
        assert available_from == g.available_from and default_rule == g.default_index_rule
    comment = _scalar(
        con,
        "SELECT comment FROM duckdb_views() WHERE schema_name = 'mimiciv_derived' "
        "AND view_name = 'hadm_era'",
    )
    assert comment and "EP-34" in comment


# ---------------------------------------------------------------------------
# 8. CLI + safe_query surfaces on the fixture lake
# ---------------------------------------------------------------------------


def test_catalog_info_lists_the_registry_and_views(fixture_lake_settings: Settings) -> None:
    runner = helpers.cli_runner()
    root = ["--data-root", str(fixture_lake_settings.data_root)]
    try:
        as_json = runner.invoke(app, [*root, "catalog", "info", "--tier", "fixture", "--json"])
        assert as_json.exit_code == 0, as_json.output
        payload = json.loads(as_json.output)
        objects = {f"{o['schema']}.{o['table']}": o["kind"] for o in payload["objects"]}
        assert objects["meta.grains"] == "table"
        assert objects["mimiciv_derived.hadm_era"] == "view"
        assert objects["mimiciv_derived.icustay_index"] == "view"
        assert objects["meta.catalog_info"] == "table" and objects["meta.itemids"] == "view"
        assert len(payload["catalog_tables"]) == 31, "the contract listing is unchanged"

        text = runner.invoke(app, [*root, "catalog", "info", "--tier", "fixture"])
        assert text.exit_code == 0, text.output
        assert "grains" in text.output and "hadm_era" in text.output
    finally:
        config.configure()


def test_registry_and_views_through_safe_query(fixture_lake_settings: Settings) -> None:
    registry = safe_query(
        "SELECT name, available FROM meta.grains ORDER BY 1",
        tier="fixture",
        settings=fixture_lake_settings,
    )
    assert registry.n_rows == len(GRAINS), "meta.* is registry-exempt: no count column needed"
    assert set(registry.df["name"].to_list()) == set(GRAINS)

    eras = safe_query(
        "SELECT era_index, count(*) AS n FROM mimiciv_derived.hadm_era GROUP BY 1 ORDER BY 1",
        tier="fixture",
        settings=fixture_lake_settings,
    )
    assert eras.df["era_index"].to_list() == [0, 1, 2, 3, 4]
    assert eras.rows_suppressed == 0

    runner = helpers.cli_runner()
    root = ["--data-root", str(fixture_lake_settings.data_root)]
    try:
        result = runner.invoke(
            app,
            [
                *root,
                "sql",
                "--tier",
                "fixture",
                "--format",
                "json",
                "SELECT icd_versions, count(*) AS n FROM mimiciv_derived.hadm_era "
                "GROUP BY 1 ORDER BY 1",
            ],
        )
        assert result.exit_code == 0, result.output
        labels = {row["icd_versions"] for row in json.loads(result.output)["rows"]}
        assert labels <= {"icd9", "icd10", "mixed", None}
    finally:
        config.configure()


# ---------------------------------------------------------------------------
# 9. The tracer cites timesem; its numbers are count-for-count unchanged
# ---------------------------------------------------------------------------


def test_tracer_cites_timesem_fragments() -> None:
    sql = tracer.cohort_cte_sql()
    assert sql_age_at("anchor_age", "anchor_year", "admittime") in sql
    assert sql_age_at("anchor_age", "anchor_year", "admittime", cap=True) in sql
    assert "timesem" in sql, "the SQL header cites the module"
    assert tracer.AGE_BANDS is timesem.AGE_BANDS
    assert not hasattr(tracer, "_age_band_case"), "the inlined CASE builder is gone"
    # the statements the tracer audits are byte-identical to the EP-31 forms (same
    # statement hashes): the WITH clause minus comments, and the band CASE
    stripped = "\n".join(line for line in sql.splitlines() if not line.startswith("--")).strip()
    assert stripped == LEGACY_TRACER_CTE
    assert sql_age_band("age_at_admit") == LEGACY_AGE_BAND_CASE


def _tracer_tables(tier: str, cte: str, settings: Settings) -> dict[str, list[tuple[Any, ...]]]:
    """Attrition counts + both descriptive tables through safe_query over ``cte``."""
    counts = "count(*) AS n, count(*) FILTER (WHERE hospital_expire_flag = 1) AS n_deaths"
    selects = {
        **{step: f"SELECT count(*) AS n FROM {step}" for step in tracer.STEPS},
        "by_age_gender": (
            f"SELECT {LEGACY_AGE_BAND_CASE} AS age_band, gender, {counts} "
            "FROM cohort GROUP BY 1, 2 ORDER BY 1, 2"
        ),
        "by_first_careunit": f"SELECT first_careunit, {counts} FROM cohort GROUP BY 1 ORDER BY 1",
    }
    out: dict[str, list[tuple[Any, ...]]] = {}
    for name, select in selects.items():
        result = safe_query(f"{cte}\n{select}", tier=tier, actor="test_ep34", settings=settings)
        out[name] = sorted(result.df.rows(), key=str)
    return out


def _assert_tracer_unchanged(tier: str, settings: Settings) -> None:
    before = _tracer_tables(tier, LEGACY_TRACER_CTE, settings)
    after = _tracer_tables(tier, tracer.cohort_cte_sql().rstrip(), settings)
    assert after == before, "the timesem-cited chain must reproduce EP-31 count for count"
    assert before["cohort"] and before["cohort"][0][0] is not None


def test_tracer_numbers_unchanged_on_fixture(fixture_lake_settings: Settings) -> None:
    _assert_tracer_unchanged("fixture", fixture_lake_settings)


# ---------------------------------------------------------------------------
# 10. Module hygiene: no calendar function outside age_at; import budget; docs in sync
# ---------------------------------------------------------------------------


def test_module_reads_no_calendar_outside_age_at() -> None:
    source = Path(timesem.__file__).read_text(encoding="utf-8")
    assert "strftime" not in source
    for token in ("strptime", "date_trunc", "date_part", "epoch(", "month(", "dayofyear"):
        assert token not in source, token
    tree = ast.parse(source)
    allowed: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in ("age_at", "sql_age_at"):
            assert node.end_lineno is not None
            allowed.append((node.lineno, node.end_lineno))
    assert len(allowed) == 2
    for lineno, line in enumerate(source.splitlines(), start=1):
        if "year(" in line:
            assert any(a <= lineno <= b for a, b in allowed), f"line {lineno}: calendar read"


def test_import_budget() -> None:
    helpers.assert_import_budget("mimicwarehouse.timesem")
    # tracer.py imports timesem on the mwh start-up path: the budget line must still hold
    helpers.assert_import_budget()


def test_methods_doc_in_sync(tmp_path: Path) -> None:
    path = timesem.methods_doc_path()
    assert path.is_file(), "docs/methods/time-semantics.md exists"
    text = path.read_text(encoding="utf-8")
    for needle in (
        "date-shift",
        "anchor_year_group",
        "dod",
        "icd_version",
        "91",
        "competing",
        "retrospective",
        "[start, end)",
        "hours_since_icu_intime",
    ):
        assert needle in text, needle
    # the three generated blocks equal what the registry renders now
    copy = tmp_path / "time-semantics.md"
    copy.write_text(text, encoding="utf-8", newline="\n")
    timesem.sync_methods_doc(copy)
    assert copy.read_text(encoding="utf-8") == text, "re-run `python -m mimicwarehouse.timesem`"
    grain_block = timesem.render_grain_table()
    assert grain_block in text and timesem.render_era_table() in text
    assert timesem.render_censoring_table() in text
    for g in GRAINS.values():
        assert f"`{g.name}`" in grain_block
    assert "placeholder until EP-142" in grain_block and "placeholder until EP-148" in grain_block
    assert not re.search(r"(?<![\w.])[123]\d{7}(?![\w.])", text), "no band-shaped integers"


# ---------------------------------------------------------------------------
# 11. Dev tier: both views exist, five eras through safe_query, tracer unchanged
# ---------------------------------------------------------------------------


@pytest.mark.tier("dev")
def test_dev_catalog_views_and_five_eras(dev_catalog: Path) -> None:
    settings = config.load_settings()
    con = open_catalog("dev", settings=settings)
    try:
        objects = _objects(con, "meta", "mimiciv_derived")
    finally:
        con.close()
    assert objects.get("mimiciv_derived.hadm_era") == "VIEW", (
        "rebuild with `mwh build --tier dev --select catalog`"
    )
    assert objects.get("mimiciv_derived.icustay_index") == "VIEW"
    assert objects.get("meta.grains") == "BASE TABLE"
    eras = safe_query(
        "SELECT era_index, count(*) AS n FROM mimiciv_derived.hadm_era GROUP BY 1 ORDER BY 1",
        tier="dev",
        actor="test_ep34",
        settings=settings,
    )
    assert eras.n_rows == 5 and eras.df["era_index"].to_list() == [0, 1, 2, 3, 4]
    assert eras.rows_suppressed == 0
    registry = safe_query(
        "SELECT name FROM meta.grains ORDER BY 1", tier="dev", actor="test_ep34", settings=settings
    )
    assert set(registry.df["name"].to_list()) == set(GRAINS)


@pytest.mark.tier("dev")
def test_dev_tracer_numbers_unchanged(dev_catalog: Path) -> None:
    _assert_tracer_unchanged("dev", config.load_settings())
