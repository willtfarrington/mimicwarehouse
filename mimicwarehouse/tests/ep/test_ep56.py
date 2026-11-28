"""EP-56 — latency marts B: the hourly marts ``marts.icustay_hourly`` /
``marts.hourly_variables`` / ``marts.hourly_population`` (fixture; dev where marked; full
opt-in).

Covers: the three specs validate (grains, keys, partition, columns and types, units) and the
registry's EP-56 shapes (the family statistics ``n`` — an INTEGER count without a unit — and
``last``; the summary grains ``variable`` / ``hour_variable_era``) leave the earlier specs'
hashes alone; the builder's VARIABLES agree with the spec family; the dictionary frame (every
variable, its concept source and the curated itemids found in their concept files); the DAG
wiring and the hour-cap setting; crafted concept tables (synthetic ids >= 90 000 000) through
the hourly statement — the exact-hour boundary, a pre-ICU measurement dropped, post-outtime
vitals kept up to ``ceil(los)``, labs inside ``[intime, outtime)`` only, venous blood gases
dropped, a row only where something was measured, the latest value with ties broken by the
highest, the cap honoured; the population statement over a crafted hourly table (counts, the
``all`` row, quantiles, the hour limit, the ``unknown`` era) and its release at k (an era
hidden with its complement, the ``all`` row as the protected margin); the session fixture
lake (declared columns and types, the hour range per stay, counts reproduced from the
concept, quantiles monotone, nothing below k released, every variable in the dictionary,
registry reads through ``safe_query``, the release gate over the published population).
``tier("dev")``: the same invariants through ``safe_query`` and the dev build's wall time from
the benchmark ledger. ``tier("full")``: the full build's marts are built and current.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import duckdb
import polars as pl
import pytest

from mimicwarehouse import config, disclose, safe, units
from mimicwarehouse.dag.spec import load_dag
from mimicwarehouse.marts import hourly as hr
from mimicwarehouse.marts import registry
from mimicwarehouse.marts.registry import ColumnFamily, FamilyVar, MartBuildError
from mimicwarehouse.timesem import ERAS

if TYPE_CHECKING:  # pragma: no cover
    from mimicwarehouse.config import Settings

pytestmark = pytest.mark.ep_56

K = 11
KEY_COLUMNS: tuple[tuple[str, str], ...] = (
    ("subject_id", "INTEGER"),
    ("stay_id", "INTEGER"),
    ("subject_bucket", "INTEGER"),
    ("hour_bin", "INTEGER"),
)
STAT_TYPES = {"mean": "DOUBLE", "min": "DOUBLE", "max": "DOUBLE", "n": "INTEGER", "last": "DOUBLE"}
HOURLY_COLUMNS: tuple[tuple[str, str], ...] = KEY_COLUMNS + tuple(
    (f"{v}_{s}", STAT_TYPES[s]) for v in hr.VARIABLES for s in hr.STATS
)
VARIABLES_COLUMNS: tuple[tuple[str, str], ...] = (
    ("ordinal", "INTEGER"),
    ("variable", "VARCHAR"),
    ("label", "VARCHAR"),
    ("unit", "VARCHAR"),
    ("concept_group", "VARCHAR"),
    ("source_concept", "VARCHAR"),
    ("source_column", "VARCHAR"),
    ("row_filter", "VARCHAR"),
    ("attach", "VARCHAR"),
    ("itemids", "VARCHAR"),
    ("stats", "VARCHAR"),
)
POPULATION_COLUMNS: tuple[tuple[str, str], ...] = (
    ("hour_bin", "INTEGER"),
    ("variable", "VARCHAR"),
    ("era", "VARCHAR"),
    ("n_stays", "BIGINT"),
    ("p05", "DOUBLE"),
    ("p25", "DOUBLE"),
    ("p50", "DOUBLE"),
    ("p75", "DOUBLE"),
    ("p95", "DOUBLE"),
    ("mean", "DOUBLE"),
    ("suppressed", "BOOLEAN"),
    ("k", "INTEGER"),
)
HOURLY_DEPENDS_ON = (
    "stage.mimiciv_icu.icustays",
    "concept.measurement.vitalsign",
    "concept.measurement.chemistry",
    "concept.measurement.complete_blood_count",
    "concept.measurement.bg",
    "concept.measurement.coagulation",
)
ERA_A, ERA_B, ERA_C = ERAS[0], ERAS[1], ERAS[2]
#: The EP-55 / EP-175 marts, whose specs the registry change must not touch.
EARLIER_MARTS = ("icustay_first_day", "itemid_daily", "itemid_summary")


# ---------------------------------------------------------------------------
# 1. The specs, the registry's EP-56 shapes, the dictionary, the DAG, the setting
# ---------------------------------------------------------------------------


def test_packaged_specs_validate() -> None:
    reg = registry.load_registry()
    assert {hr.SPEC_HOURLY, hr.SPEC_VARIABLES, hr.SPEC_POPULATION} <= set(reg.names)
    hourly, variables, population = hr.hourly_spec(), hr.variables_spec(), hr.population_spec()
    assert hourly.grain == "hour_bin" and hourly.version == 1
    assert hourly.key == ("subject_id", "stay_id", "hour_bin")
    assert hourly.partition == registry.BUCKET_COLUMN
    assert hourly.sort == ("subject_id", "stay_id", "hour_bin")
    assert tuple((c.name, c.type) for c in hourly.expanded) == HOURLY_COLUMNS
    assert len(HOURLY_COLUMNS) == 4 + 21 * 5
    assert hourly.column("subject_bucket").expr == f"r.subject_id % {registry.NUM_BUCKETS}"
    assert hourly.depends_on == HOURLY_DEPENDS_ON
    assert hourly.base.cte and hourly.cte_aliases == ("r",)
    reads = {s.relation for s in hourly.sources if s.join == "read"}
    assert reads == {src.relation for src in hr.SOURCES.values()}
    for column in hourly.expanded:
        if column.name.endswith("_n"):
            assert column.unit is None and column.concept_group is None, column.name
        elif column.name.endswith(("_mean", "_min", "_max", "_last")):
            assert column.unit and column.concept_group, column.name
    assert hourly.column("temperature_mean").unit == "degC"
    assert hourly.column("pao2_last").concept_group == "labs.po2"
    assert variables.grain == "variable" and variables.grain in registry.SUMMARY_GRAINS
    assert variables.partition is None and variables.key == ("variable",)
    assert tuple((c.name, c.type) for c in variables.expanded) == VARIABLES_COLUMNS
    assert variables.depends_on == ()
    assert population.grain == "hour_variable_era"
    assert population.grain in registry.SUMMARY_GRAINS and population.partition is None
    assert population.key == ("hour_bin", "variable", "era") == population.sort
    assert tuple((c.name, c.type) for c in population.expanded) == POPULATION_COLUMNS
    assert population.depends_on == (hr.STEP_HOURLY, f"stage.{hr.PATIENTS_TABLE}")
    for spec, builder in (
        (hourly, "run_icustay_hourly"),
        (variables, "run_hourly_variables"),
        (population, "run_hourly_population"),
    ):
        assert spec.builder == f"mimicwarehouse.marts.hourly:{builder}"
        assert spec.step_name == spec.status_key == f"marts.{spec.name}"
        assert registry.check_units(spec) == []
        text = (registry.specs_dir() / f"{spec.name}.yaml").read_text(encoding="utf-8")
        assert text.isascii()
    assert hr.check_variables() == []
    # the dictionary and the population are registry tables; the hourly mart is not
    for name in (hr.SPEC_POPULATION, hr.SPEC_VARIABLES):
        assert f"marts.{name}" in safe.REGISTRY_TABLES and safe.is_registry_ref("marts", name)
    assert not safe.is_registry_ref("marts", hr.SPEC_HOURLY), "subject-keyed"


def test_family_statistics_n_and_last() -> None:
    family = ColumnFamily(
        family="probe",
        alias="x",
        stats=("min", "n", "last"),
        type="DOUBLE",
        description="{stat} {label} in the probe.",
        vars=(FamilyVar(name="hr", label="heart rate", unit="bpm", concept_group="vitals.hr"),),
    )
    by_name = {c.name: c for c in family.expand()}
    assert list(by_name) == ["hr_min", "hr_n", "hr_last"]
    assert (by_name["hr_n"].type, by_name["hr_n"].unit, by_name["hr_n"].concept_group) == (
        "INTEGER",
        None,
        None,
    ), "a count: INTEGER, no unit"
    assert (by_name["hr_last"].type, by_name["hr_last"].unit) == ("DOUBLE", "bpm")
    assert by_name["hr_n"].description == "Number of measurements of heart rate in the probe."
    assert by_name["hr_last"].description.startswith("Latest (by charttime) heart rate")
    assert dict(registry.STAT_TYPES) == {"n": "INTEGER"}
    assert set(registry.UNITLESS_STATS) == {"n"}


def test_earlier_specs_keep_their_hashes(
    fixture_lake_catalog: duckdb.DuckDBPyConnection,
) -> None:
    # the registry change (two statistics, two summary grains, the params field) leaves the
    # EP-55 / EP-175 specs' dumps alone: their built marts are not stale
    names = ", ".join(f"'{n}'" for n in EARLIER_MARTS)
    rows = fixture_lake_catalog.execute(
        f"SELECT mart, built, stale FROM meta.mart_tables WHERE mart IN ({names}) ORDER BY mart"
    ).fetchall()
    assert rows == [(name, True, False) for name in sorted(EARLIER_MARTS)]


def test_variables_frame() -> None:
    frame = hr.variables_frame()
    family = hr.hourly_family()
    assert frame.get_column("variable").to_list() == [v.name for v in family.vars]
    assert frame.get_column("ordinal").to_list() == list(range(1, 22))
    assert set(frame.get_column("stats").to_list()) == {"mean, min, max, n, last"}
    catalogue = {i.itemid: i for i in units.load_catalogue().items}
    for row in frame.iter_rows(named=True):
        concept, column = hr.VARIABLES[row["variable"]]
        assert row["source_concept"] == f"mimiciv_derived.{concept}"
        assert row["source_column"] == column
        ids = [int(x) for x in row["itemids"].split(", ")]
        assert ids, row["variable"]
        for itemid in ids:
            item = catalogue[itemid]
            assert item.concept_group == row["concept_group"]
            assert item.source_ref.endswith(f"/{concept}.sql")
        assert (row["row_filter"] is not None) == (concept == "bg")
        assert row["attach"] == hr.SOURCES[concept].attach_rule
    assert hr.SOURCES["vitalsign"].attach_rule == "stay_id"
    assert frame.filter(pl.col("variable") == "pao2").get_column("itemids").to_list() == ["50821"]
    assert "220045" in frame.filter(pl.col("variable") == "heart_rate")["itemids"].to_list()


def test_variables_frame_refuses_a_disagreeing_spec(monkeypatch: pytest.MonkeyPatch) -> None:
    variables = dict(hr.VARIABLES)
    variables["extra"] = ("vitalsign", "heart_rate")
    monkeypatch.setattr(hr, "VARIABLES", variables)
    assert hr.check_variables(), "VARIABLES and the spec family disagree"
    with pytest.raises(MartBuildError, match="family variables"):
        hr.variables_frame()


def test_dag_wiring_and_the_hour_cap_setting() -> None:
    dag = load_dag()
    for spec in (hr.hourly_spec(), hr.variables_spec(), hr.population_spec()):
        step = dag.step(spec.step_name)
        assert step.kind == "python" and step.callable_name == spec.builder
        assert step.qualified_table == spec.status_key, "skips when complete for the tier"
        assert tuple(step.depends_on) == spec.depends_on, "the DAG mirrors the spec"
        assert "marts" in step.tags and set(step.tiers) == {"fixture", "demo", "dev", "full"}
    names = {s.name for s in dag.steps}
    assert set(HOURLY_DEPENDS_ON) <= names
    catalog = dag.step("catalog")
    assert {hr.STEP_HOURLY, hr.STEP_VARIABLES, hr.STEP_POPULATION} <= set(catalog.depends_on)
    ordered = [s.name for s in dag.ordered(tags=["marts"], tier="fixture")]
    assert ordered.index(hr.STEP_HOURLY) < ordered.index(hr.STEP_POPULATION)
    assert ordered[-1] == "catalog"
    assert config.Settings.model_fields["marts_hourly_max_hours"].default == 336
    example = (Path(config.__file__).parents[2] / ".env.example").read_text(encoding="utf-8")
    assert "# MWH_MARTS_HOURLY_MAX_HOURS=336" in example
    with pytest.raises(ValueError):
        config.Settings(marts_hourly_max_hours=0)


# ---------------------------------------------------------------------------
# 2. Crafted concept tables through the hourly statement (synthetic ids)
# ---------------------------------------------------------------------------

SUBJ_A, SUBJ_B, SUBJ_C = 90000101, 90000102, 90000103
STAY_A, STAY_B = 93000101, 93000102
IN_A, OUT_A = "2150-01-01 08:00:00", "2150-01-01 18:30:00"  # 10.5 h: ceil = 11
IN_B, OUT_B = "2150-02-01 00:00:00", "2150-02-02 00:00:00"
VITAL_COLUMNS = ("heart_rate", "sbp", "dbp", "mbp", "resp_rate", "temperature", "spo2", "glucose")
VITALS: list[tuple[int, str, float | None, float | None]] = [
    # (stay_id, charttime, heart_rate, sbp); the other vitals NULL
    (STAY_A, "2150-01-01 07:59:59", 70.0, None),  # before intime: dropped
    (STAY_A, "2150-01-01 08:00:00", 80.0, 120.0),  # exactly intime: bin 0
    (STAY_A, "2150-01-01 08:59:59", 90.0, None),  # bin 0, the latest of the hour
    (STAY_A, "2150-01-01 09:00:00", 100.0, None),  # exactly 1 h: bin 1
    (STAY_A, "2150-01-01 12:00:00", None, None),  # nothing measured: no row for bin 4
    (STAY_A, "2150-01-01 18:45:00", 88.0, None),  # after outtime, bin 10 <= 11: kept
    (STAY_A, "2150-01-01 19:30:00", 87.0, None),  # bin 11 = ceil(los): kept
    (STAY_A, "2150-01-01 20:00:00", 86.0, None),  # bin 12 > ceil(los): dropped
    (STAY_B, "2150-02-01 03:15:00", 60.0, None),  # stay B, bin 3
]
CHEMISTRY: list[tuple[int, str, float | None, float | None]] = [
    # (subject_id, charttime, creatinine, sodium)
    (SUBJ_A, "2150-01-01 07:00:00", 1.1, None),  # before intime: dropped
    (SUBJ_A, "2150-01-01 10:30:00", 1.0, None),  # bin 2
    (SUBJ_A, "2150-01-01 10:30:00", 1.4, None),  # bin 2, a tie at the latest time
    (SUBJ_A, "2150-01-01 11:15:00", None, 140.0),  # bin 3: sodium only
    (SUBJ_A, "2150-01-01 18:30:00", 1.3, None),  # at outtime: dropped
    (SUBJ_C, "2150-01-01 10:00:00", 0.9, None),  # no stay at all: dropped
]
BG: list[tuple[int, str, str | None, float | None, float | None]] = [
    # (subject_id, charttime, specimen, lactate, ph)
    (SUBJ_A, "2150-01-01 10:30:00", "ART.", 2.0, 7.35),  # arterial: bin 2
    (SUBJ_A, "2150-01-01 10:45:00", "VEN.", 3.0, 7.30),  # venous: dropped
    (SUBJ_A, "2150-01-01 11:00:00", None, None, 7.25),  # no specimen type: dropped
]


def _crafted_concepts(con: duckdb.DuckDBPyConnection) -> None:
    for schema in ("mimiciv_icu", "mimiciv_hosp", "mimiciv_derived"):
        con.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
    con.execute(
        "CREATE TABLE mimiciv_icu.icustays (subject_id INTEGER, hadm_id INTEGER, "
        "stay_id INTEGER, intime TIMESTAMP, outtime TIMESTAMP)"
    )
    con.executemany(
        "INSERT INTO mimiciv_icu.icustays VALUES (?, ?, ?, ?, ?)",
        [[SUBJ_A, 92000101, STAY_A, IN_A, OUT_A], [SUBJ_B, 92000102, STAY_B, IN_B, OUT_B]],
    )
    vital_ddl = ", ".join(f"{c} DOUBLE" for c in VITAL_COLUMNS)
    con.execute(
        "CREATE TABLE mimiciv_derived.vitalsign (subject_id INTEGER, stay_id INTEGER, "
        f"charttime TIMESTAMP, {vital_ddl}, temperature_site VARCHAR)"
    )
    subject_of = {STAY_A: SUBJ_A, STAY_B: SUBJ_B}
    con.executemany(
        "INSERT INTO mimiciv_derived.vitalsign (subject_id, stay_id, charttime, heart_rate, "
        "sbp) VALUES (?, ?, ?, ?, ?)",
        [[subject_of[s], s, t, h, b] for s, t, h, b in VITALS],
    )
    con.execute(
        "CREATE TABLE mimiciv_derived.chemistry (subject_id INTEGER, charttime TIMESTAMP, "
        "creatinine DOUBLE, bun DOUBLE, sodium DOUBLE, potassium DOUBLE, bicarbonate DOUBLE)"
    )
    con.executemany(
        "INSERT INTO mimiciv_derived.chemistry (subject_id, charttime, creatinine, sodium) "
        "VALUES (?, ?, ?, ?)",
        CHEMISTRY,
    )
    con.execute(
        "CREATE TABLE mimiciv_derived.complete_blood_count (subject_id INTEGER, "
        "charttime TIMESTAMP, hemoglobin DOUBLE, wbc DOUBLE, platelet DOUBLE)"
    )
    con.execute(
        "INSERT INTO mimiciv_derived.complete_blood_count VALUES "
        f"({SUBJ_A}, TIMESTAMP '2150-01-01 10:30:00', 10.0, NULL, NULL)"
    )
    con.execute(
        "CREATE TABLE mimiciv_derived.coagulation (subject_id INTEGER, charttime TIMESTAMP, "
        "inr DOUBLE)"
    )
    con.execute(
        "INSERT INTO mimiciv_derived.coagulation VALUES "
        f"({SUBJ_B}, TIMESTAMP '2150-02-01 05:00:00', 1.2)"
    )
    con.execute(
        "CREATE TABLE mimiciv_derived.bg (subject_id INTEGER, charttime TIMESTAMP, "
        "specimen VARCHAR, lactate DOUBLE, ph DOUBLE, po2 DOUBLE, pco2 DOUBLE)"
    )
    con.executemany(
        "INSERT INTO mimiciv_derived.bg (subject_id, charttime, specimen, lactate, ph) "
        "VALUES (?, ?, ?, ?, ?)",
        BG,
    )


def _hourly_rows(
    con: duckdb.DuckDBPyConnection, max_hours: int
) -> dict[tuple[int, int], dict[str, Any]]:
    sql = hr.hourly_sql(max_hours=max_hours)
    frame = con.execute(f"SELECT * FROM ({sql}) ORDER BY stay_id, hour_bin").pl()
    return {(r["stay_id"], r["hour_bin"]): r for r in frame.iter_rows(named=True)}


def test_crafted_hour_bins_through_the_statement() -> None:
    con = duckdb.connect()
    try:
        _crafted_concepts(con)
        rows = _hourly_rows(con, 336)
        assert set(rows) == {
            (STAY_A, 0),
            (STAY_A, 1),
            (STAY_A, 2),
            (STAY_A, 3),
            (STAY_A, 10),
            (STAY_A, 11),
            (STAY_B, 3),
            (STAY_B, 5),
        }, "no pre-ICU bin, nothing past ceil(los), no row where nothing was measured"
        a0 = rows[(STAY_A, 0)]
        assert (a0["heart_rate_n"], a0["heart_rate_min"], a0["heart_rate_max"]) == (2, 80.0, 90.0)
        assert a0["heart_rate_mean"] == pytest.approx(85.0)
        assert a0["heart_rate_last"] == 90.0, "the latest of the hour"
        assert (a0["sbp_n"], a0["sbp_last"]) == (1, 120.0)
        assert a0["creatinine_n"] == 0 and a0["creatinine_mean"] is None, "0, never NULL"
        assert rows[(STAY_A, 1)]["heart_rate_last"] == 100.0, "exactly 1 h is bin 1"
        a2 = rows[(STAY_A, 2)]
        assert (a2["creatinine_n"], a2["creatinine_last"]) == (2, 1.4), "a tie: the highest"
        assert a2["creatinine_mean"] == pytest.approx(1.2)
        assert (a2["lactate_n"], a2["lactate_mean"], a2["ph_last"]) == (1, 2.0, 7.35)
        assert a2["hemoglobin_mean"] == 10.0 and a2["heart_rate_n"] == 0
        a3 = rows[(STAY_A, 3)]
        assert (a3["sodium_n"], a3["creatinine_n"], a3["ph_n"]) == (1, 0, 0), (
            "venous and untyped blood gases are not arterial"
        )
        assert rows[(STAY_A, 10)]["heart_rate_last"] == 88.0, "post-outtime charting kept"
        assert rows[(STAY_A, 11)]["heart_rate_last"] == 87.0
        assert rows[(STAY_B, 5)]["inr_mean"] == 1.2 and rows[(STAY_B, 3)]["heart_rate_n"] == 1
        for row in rows.values():
            assert row["subject_bucket"] == row["subject_id"] % 100
            assert sum(row[f"{v}_n"] for v in hr.VARIABLES) >= 1
        # the cap: only bins 0 .. max_hours - 1
        capped = _hourly_rows(con, 3)
        assert set(capped) == {(STAY_A, 0), (STAY_A, 1), (STAY_A, 2)}
        with pytest.raises(MartBuildError, match="max_hours"):
            hr.hourly_sql(max_hours=0)
    finally:
        con.close()


def test_the_statement_inlines_no_macro_and_names_every_source() -> None:
    sql = hr.hourly_sql(max_hours=336)
    assert "mwh_harmonize" not in sql
    for src in hr.SOURCES.values():
        assert f"FROM {src.relation} AS x" in sql
    assert "x.specimen = 'ART.'" in sql
    assert "hour_bin < 336" in sql and "hour_bin <= last_bin" in sql


# ---------------------------------------------------------------------------
# 3. The population: the statement over a crafted hourly table, the release at k
# ---------------------------------------------------------------------------


def _crafted_hourly_table(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("CREATE SCHEMA IF NOT EXISTS mimiciv_hosp")
    means = ", ".join(
        f"{'60.0 + i' if v == 'heart_rate' else 'NULL::DOUBLE'} AS {v}_mean" for v in hr.VARIABLES
    )
    # 30 stays at hour 0 (20 in era A, 9 in era B, one without a patients row) and at hour
    # 169 (outside the population's 0 .. 168)
    con.execute(
        "CREATE TABLE crafted_hourly AS SELECT 90000200 + i AS subject_id, "
        f"93000200 + i AS stay_id, h AS hour_bin, {means} "
        "FROM range(1, 31) AS r(i), (VALUES (0), (169)) AS hb(h)"
    )
    con.execute(
        "CREATE TABLE mimiciv_hosp.patients AS SELECT 90000200 + i AS subject_id, "
        f"CASE WHEN i <= 20 THEN '{ERA_A}' ELSE '{ERA_B}' END AS anchor_year_group "
        "FROM range(1, 30) AS r(i)"
    )


def test_population_statement_on_a_crafted_hourly_table() -> None:
    con = duckdb.connect()
    try:
        _crafted_hourly_table(con)
        raw = hr.compute_population(con, relation="crafted_hourly")
    finally:
        con.close()
    assert raw.columns == ["hour_bin", "variable", "era", "n_stays", *hr.QUANTILE_COLUMNS, "mean"]
    assert set(raw.get_column("hour_bin").to_list()) == {0}, "hour 169 is past the limit"
    assert set(raw.get_column("variable").to_list()) == {"heart_rate"}, "NULLs unpivot away"
    counts = dict(raw.select("era", "n_stays").rows())
    assert counts == {ERA_A: 20, ERA_B: 9, hr.UNKNOWN_ERA: 1, hr.ALL_ERA: 30}
    era_a = raw.filter(pl.col("era") == ERA_A).row(0, named=True)
    assert era_a["p50"] == pytest.approx(70.5) and era_a["mean"] == pytest.approx(70.5)
    for row in raw.iter_rows(named=True):
        q = [row[c] for c in hr.QUANTILE_COLUMNS]
        assert q == sorted(q), "p05 <= p25 <= p50 <= p75 <= p95"


def test_population_release_at_k() -> None:
    raw = pl.DataFrame(
        {
            "hour_bin": [0, 0, 0, 0, 0, 0, 0, 0, 1, 1],
            "variable": ["sbp", "sbp", "sbp", "hr", "hr", "hr", "hr", "hr", "hr", "hr"],
            "era": [ERA_A, ERA_B, "all", ERA_A, ERA_B, ERA_C, "unknown", "all", ERA_A, "all"],
            "n_stays": [20, 5, 25, 12, 13, 30, 14, 69, 6, 6],
            "p05": [1.0] * 10,
            "p25": [2.0] * 10,
            "p50": [3.0] * 10,
            "p75": [4.0] * 10,
            "p95": [5.0] * 10,
            "mean": [3.0] * 10,
        },
        schema_overrides={"hour_bin": pl.Int32},
    )
    out = hr.release_population(raw, K)
    assert out.columns == [c for c, _t in POPULATION_COLUMNS]
    flags = dict(
        zip(
            zip(out["hour_bin"], out["variable"], out["era"], strict=True),
            out["suppressed"],
            strict=True,
        )
    )
    # sbp at hour 0: era B is small; era A must go with it, or 'all' - A would give B back
    assert flags[(0, "sbp", ERA_B)] and flags[(0, "sbp", ERA_A)]
    assert not flags[(0, "sbp", "all")], "the margin itself is published"
    assert not any(flags[(0, "hr", e)] for e in (ERA_A, ERA_B, ERA_C, "unknown", "all"))
    assert flags[(1, "hr", ERA_A)] and flags[(1, "hr", "all")], "the 'all' row below k"
    for row in out.iter_rows(named=True):
        if row["suppressed"]:
            assert all(row[c] is None for c in hr.POPULATION_BLANKED)
        else:
            assert row["n_stays"] >= K
    assert set(out["k"].to_list()) == {K}
    findings = disclose.check_frame(out.drop("suppressed", "k"))
    assert all(f.status != disclose.FAIL for f in findings), [f.code for f in findings]


# ---------------------------------------------------------------------------
# 4. The session fixture lake (conftest: the whole DAG, the catalog)
# ---------------------------------------------------------------------------


def test_session_lake_hourly_mart(
    fixture_lake_catalog: duckdb.DuckDBPyConnection, fixture_lake_settings: Settings
) -> None:
    con = fixture_lake_catalog
    for name, columns in (
        (hr.SPEC_HOURLY, HOURLY_COLUMNS),
        (hr.SPEC_VARIABLES, VARIABLES_COLUMNS),
        (hr.SPEC_POPULATION, POPULATION_COLUMNS),
    ):
        described = [
            (str(r[0]), str(r[1])) for r in con.execute(f"DESCRIBE marts.{name}").fetchall()
        ]
        assert described == list(columns), name
    n_sum = " + ".join(f"{v}_n" for v in hr.VARIABLES)
    stat_order = " OR ".join(
        f"({v}_n > 0 AND NOT ({v}_min <= {v}_mean AND {v}_mean <= {v}_max AND {v}_min <= {v}_last "
        f"AND {v}_last <= {v}_max))"
        for v in hr.VARIABLES
    )
    stat_without_n = " OR ".join(f"({v}_n = 0 AND {v}_mean IS NOT NULL)" for v in hr.VARIABLES)
    row = con.execute(
        "SELECT count(*), count(*) FILTER (WHERE h.hour_bin < 0), "
        "count(*) FILTER (WHERE h.hour_bin >= 336), "
        "count(*) FILTER (WHERE h.hour_bin > CAST(ceil(date_diff('second', i.intime, i.outtime) "
        "/ 3600.0) AS INTEGER)), "
        f"count(*) FILTER (WHERE ({n_sum}) < 1), "
        "count(*) FILTER (WHERE h.subject_bucket <> h.subject_id % 100), "
        f"count(*) FILTER (WHERE {stat_order}), count(*) FILTER (WHERE {stat_without_n}), "
        "count(*) FILTER (WHERE i.stay_id IS NULL) "
        "FROM marts.icustay_hourly AS h LEFT JOIN mimiciv_icu.icustays AS i "
        "ON i.stay_id = h.stay_id"
    ).fetchone()
    assert row is not None
    n_rows, negative, capped, past_los, empty, bucket_bad, order_bad, stat_alone, orphan = row
    assert n_rows > 0
    assert (negative, capped, past_los, empty, bucket_bad, order_bad, stat_alone, orphan) == (
        0,
    ) * 8
    duplicates = con.execute(
        "SELECT count(*) - count(DISTINCT (stay_id, hour_bin)) FROM marts.icustay_hourly"
    ).fetchone()
    assert duplicates == (0,), "one row per (stay_id, hour_bin)"
    # heart_rate_n reproduces the concept's valued rows inside the hour window
    raw = con.execute(
        "SELECT count(*) FROM mimiciv_derived.vitalsign AS v JOIN mimiciv_icu.icustays AS i "
        "ON i.stay_id = v.stay_id WHERE v.heart_rate IS NOT NULL AND "
        "floor(date_diff('second', i.intime, v.charttime) / 3600.0) BETWEEN 0 AND "
        "least(ceil(date_diff('second', i.intime, i.outtime) / 3600.0), 335)"
    ).fetchone()
    rolled = con.execute("SELECT sum(heart_rate_n) FROM marts.icustay_hourly").fetchone()
    assert raw is not None and rolled is not None
    assert raw[0] > 0 and rolled[0] == raw[0]
    lake_root = fixture_lake_settings.lake_root("fixture")
    manifest = registry.read_mart_manifest(lake_root, "fixture", hr.SPEC_HOURLY)
    assert manifest is not None and manifest["params"] == {"max_hours": 336}
    assert set(manifest["snapshot_ids"]) == {"core", "derived"}


def test_session_lake_dictionary_and_population(
    fixture_lake_catalog: duckdb.DuckDBPyConnection, fixture_lake_settings: Settings
) -> None:
    con = fixture_lake_catalog
    k = fixture_lake_settings.k_suppression
    declared = [
        r[0]
        for r in con.execute(
            "SELECT variable FROM marts.hourly_variables ORDER BY ordinal"
        ).fetchall()
    ]
    assert declared == list(hr.VARIABLES), "every hourly variable is in the dictionary"
    stays = con.execute("SELECT count(*) FROM mimiciv_icu.icustays").fetchone()
    assert stays is not None
    rows = con.execute(
        'SELECT hour_bin, variable, era, n_stays, p05, p25, p50, p75, p95, "mean", suppressed, k '
        "FROM marts.hourly_population"
    ).fetchall()
    assert rows, "the population has rows on the fixture"
    eras = {*ERAS, hr.ALL_ERA, hr.UNKNOWN_ERA}
    for hour_bin, variable, era, n, *stats, suppressed, row_k in rows:
        assert 0 <= hour_bin <= hr.POPULATION_MAX_HOUR and variable in hr.VARIABLES
        assert era in eras and row_k == k
        if suppressed:
            assert n is None and all(s is None for s in stats)
            continue
        assert k <= n <= stays[0], "nothing below k released; never more stays than the tier"
        q = stats[:5]
        assert q == sorted(q), "p05 <= p25 <= p50 <= p75 <= p95"
    # the released 'all' counts equal the stays measured in the hourly mart
    released = {(h, v): n for h, v, era, n, *_rest in rows if era == hr.ALL_ERA and n is not None}
    for (hour_bin, variable), n in list(released.items())[:20]:
        got = con.execute(
            f"SELECT count(*) FROM marts.icustay_hourly WHERE hour_bin = ? AND {variable}_n > 0",
            [hour_bin],
        ).fetchone()
        assert got == (n,), (hour_bin, variable)
    # the registry lists the three hourly marts, built and current
    tables = {
        r[0]: r[1:]
        for r in con.execute("SELECT mart, built, stale, grain FROM meta.mart_tables").fetchall()
    }
    assert tables[hr.SPEC_HOURLY] == (True, False, "hour_bin")
    assert tables[hr.SPEC_VARIABLES] == (True, False, "variable")
    assert tables[hr.SPEC_POPULATION] == (True, False, "hour_variable_era")
    lake_root = fixture_lake_settings.lake_root("fixture")
    part = registry.mart_dir(lake_root, "fixture", hr.SPEC_POPULATION) / registry.PART
    assert disclose.check(part).passed, "the published population passes the release gate"


def test_session_lake_safe_query_and_dictionary_render(
    fixture_lake_catalog: duckdb.DuckDBPyConnection, fixture_lake_settings: Settings
) -> None:
    from mimicwarehouse.catalog.dictionary import render_dictionary
    from mimicwarehouse.safe import SafeQueryRefused, safe_query

    def run(sql: str) -> Any:
        return safe_query(sql, tier="fixture", settings=fixture_lake_settings, actor="test_ep56")

    trend = run(
        "SELECT hour_bin, p50 FROM marts.hourly_population "
        "WHERE variable = 'heart_rate' AND era = 'all' AND hour_bin <= 72"
    )
    assert trend.n_rows >= 1, "a registry read: no count column, no refusal"
    picker = run("SELECT variable, unit, itemids FROM marts.hourly_variables")
    assert picker.n_rows == len(hr.VARIABLES)
    count = run("SELECT count(*) AS n FROM marts.icustay_hourly")
    assert count.n_rows == 1 and int(count.df["n"][0]) > 0
    with pytest.raises(SafeQueryRefused):
        run("SELECT stay_id, heart_rate_mean FROM marts.icustay_hourly")  # identifiers out
    with pytest.raises(SafeQueryRefused):
        run("SELECT hour_bin, heart_rate_mean FROM marts.icustay_hourly")  # rows, no count
    text, info = render_dictionary(fixture_lake_catalog)
    for spec in (hr.hourly_spec(), hr.variables_spec(), hr.population_spec()):
        assert f"\n### marts.{spec.name}\n" in text
    assert info.marts == len(registry.load_registry())


# ---------------------------------------------------------------------------
# 5. The dev tier (aggregates through safe_query only)
# ---------------------------------------------------------------------------


@pytest.mark.tier("dev")
def test_dev_hourly_marts_through_safe_query(dev_catalog: Path) -> None:
    from mimicwarehouse.safe import safe_query

    settings = config.load_settings()
    remedy = "run `mwh build --tier dev --tag marts` (EP-56) first"

    def run(sql: str) -> Any:
        return safe_query(sql, tier="dev", settings=settings, actor="test_ep56")

    built = run("SELECT mart, built, stale FROM meta.mart_tables").df.rows()
    for name in (hr.SPEC_HOURLY, hr.SPEC_VARIABLES, hr.SPEC_POPULATION):
        assert (name, True, False) in built, remedy
    checks = run(
        "SELECT count(*) AS n, count(*) FILTER (WHERE hour_bin < 0) AS n_negative, "
        "count(*) FILTER (WHERE hour_bin >= 336) AS n_capped, "
        "count(DISTINCT stay_id) AS n_stays FROM marts.icustay_hourly"
    )
    assert checks.n_rows == 1, "a row was withheld (a small cell) or missing"
    row = checks.df.row(0, named=True)
    assert row["n"] > 0 and row["n_negative"] == 0 and row["n_capped"] == 0
    # the population through aggregates (the safe-query row cap is 200) and one trend read
    shape = run(
        "SELECT count(*) AS n, count(*) FILTER (WHERE NOT suppressed) AS n_released, "
        "count(*) FILTER (WHERE NOT suppressed AND n_stays < 11) AS n_small, "
        "count(*) FILTER (WHERE NOT suppressed AND NOT (p05 <= p25 AND p25 <= p50 AND "
        "p50 <= p75 AND p75 <= p95)) AS n_unordered, "
        "count(*) FILTER (WHERE suppressed AND n_stays IS NOT NULL) AS n_leaked "
        "FROM marts.hourly_population"
    ).df.row(0, named=True)
    assert shape["n"] > 0 and shape["n_released"] > 0, "some cells survive k on dev"
    assert (shape["n_small"], shape["n_unordered"], shape["n_leaked"]) == (0, 0, 0)
    trend = run(
        "SELECT hour_bin, n_stays, p05, p25, p50, p75, p95 FROM marts.hourly_population "
        "WHERE variable = 'heart_rate' AND era = 'all' ORDER BY hour_bin"
    ).df
    assert 0 < trend.height <= hr.POPULATION_MAX_HOUR + 1
    for q in trend.drop_nulls().select(*hr.QUANTILE_COLUMNS).rows():
        assert list(q) == sorted(q)
    print(
        f"dev: marts.icustay_hourly {row['n']:,} rows; hourly_population "
        f"{shape['n']:,} rows, {shape['n_released']:,} released"
    )


@pytest.mark.tier("dev")
def test_dev_build_wall_time_from_the_ledger(dev_catalog: Path) -> None:
    from mimicwarehouse.dag import benchmarks

    settings = config.load_settings()
    ledger = benchmarks.read(settings)
    dev_ok = ledger.filter((ledger["tier"] == "dev") & ledger["ok"])
    for kind, step in (
        ("mart", hr.STEP_HOURLY),
        ("mart", hr.STEP_VARIABLES),
        ("mart", hr.STEP_POPULATION),
        ("query", hr.POPULATION_BENCH_STEP),
    ):
        lines = dev_ok.filter((dev_ok["kind"] == kind) & (dev_ok["step"] == step))
        assert lines.height >= 1, f"run `mwh build --tier dev --tag marts` (EP-56) first: {step}"
        wall = float(lines["wall_s"][-1])
        assert wall < 600.0, f"{step} took {wall:.1f} s on dev (target < 10 min)"
        print(f"dev: {step} ({kind}) wall {wall:.1f} s")


# ---------------------------------------------------------------------------
# 6. The full tier (opt-in: `poe test-full`)
# ---------------------------------------------------------------------------


@pytest.mark.tier("full")
def test_full_hourly_marts_built(full_catalog: Path) -> None:
    from mimicwarehouse.safe import safe_query

    settings = config.load_settings()
    built = safe_query(
        "SELECT mart, built, stale FROM meta.mart_tables",
        tier="full",
        settings=settings,
        actor="test_ep56",
    ).df.rows()
    for name in (hr.SPEC_HOURLY, hr.SPEC_VARIABLES, hr.SPEC_POPULATION):
        assert (name, True, False) in built, "run the ep56-hourly-full job (EP-56) first"


def test_docstrings_name_the_hourly_marts() -> None:
    import mimicwarehouse.marts as marts_pkg

    doc = marts_pkg.__doc__ or ""
    assert "hourly" in doc and "EP-56" in doc and "EP-176" in doc
    module = hr.__doc__ or ""
    for phrase in ("safe.REGISTRY_TABLES", "disclose.suppress", "ceil(los_icu_hours)", "ART."):
        assert phrase in module, phrase
    assert "UNITLESS_STATS" in (registry.__doc__ or "")
