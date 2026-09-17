"""EP-50 — Events spine (MEDS-compatible).

Fixture tier (default): the registry and the DAG wiring; every source projection compiles
on the in-memory fixture catalog with the spine's column set and types; a crafted
synthetic admission (ids >= 90 000 000, a temp DuckDB) produces the expected event set in
order, including the cuts, the ``NONE`` segments, the lab text rule and the dropped
null-time rows; a projection that deliberately selects ``microbiologyevents.comments``
is refused by the static check and by ``validate``; the session fixture lake carries
every source (files, manifests, per-tier status, the ``mimiciv_derived.spine`` view, the
two ``meta`` tables, the MEDS schema check, per-source row counts equal to the source
filters, ``safe_query`` reads over code / text_value); a select / resume / force cycle on
a temp root with the ``kind: mart`` ledger lines and the run manifest; the CLI; the
docs page is in sync; the import budget. ``tier("dev")``: ``mwh build --tier dev --tag
spine`` completes, ``mimiciv_derived.spine`` is queryable and ``meta.spine_codes`` lists
every source, ``validate("dev")`` passes.

Everything asserted or printed is synthetic, SQL text, hashes, step names or suppressed
aggregate counts — never a row of real data.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

import duckdb
import pytest

import helpers
from mimicwarehouse import config, disclose, safe
from mimicwarehouse import spine as sp
from mimicwarehouse.catalog import build as build_mod
from mimicwarehouse.catalog.connect import open_catalog
from mimicwarehouse.cli import app
from mimicwarehouse.concepts.runner import register_derived
from mimicwarehouse.dag import benchmarks as benchmarks_mod
from mimicwarehouse.dag import runner as runner_mod
from mimicwarehouse.dag import snapshot as snapshot_mod
from mimicwarehouse.dag import spec as spec_mod
from mimicwarehouse.dag.spec import load_dag
from mimicwarehouse.loader import manifest as manifest_mod
from mimicwarehouse.phenotypes.runner import register_phenotypes
from mimicwarehouse.units import register_units

if TYPE_CHECKING:
    from mimicwarehouse.config import Settings
    from mimicwarehouse.schema.contract import Contract

pytestmark = pytest.mark.ep_50

K = 11
HOSP, ICU = "mimiciv_hosp", "mimiciv_icu"
S1, H1, ST1 = 90_000_001, 91_000_001, 92_000_001
BAND_TOKEN = re.compile(r"(?<![\w.])[123]\d{7}(?![\w.])")
SOURCE_STEPS = tuple(s.step_name for s in sp.SPINE_SOURCES)

#: ``(source, filter SQL over the catalog's core views)`` — the row count each source
#: must produce on any lake (item 5: "per-source row counts equal the source table filters").
EXPECTED_COUNT_SQL: dict[str, str] = {
    "patients": (
        f"SELECT (SELECT count(*) FROM {HOSP}.patients WHERE anchor_age IS NOT NULL) + "
        f"(SELECT count(*) FROM {HOSP}.patients WHERE dod IS NOT NULL)"
    ),
    "admissions": (
        f"SELECT (SELECT count(*) FROM {HOSP}.admissions WHERE admittime IS NOT NULL) + "
        f"(SELECT count(*) FROM {HOSP}.admissions WHERE dischtime IS NOT NULL)"
    ),
    "transfers": f"SELECT count(*) FROM {HOSP}.transfers WHERE intime IS NOT NULL",
    "icustays": (
        f"SELECT (SELECT count(*) FROM {ICU}.icustays WHERE intime IS NOT NULL) + "
        f"(SELECT count(*) FROM {ICU}.icustays WHERE outtime IS NOT NULL)"
    ),
    "diagnoses_icd": (
        f"SELECT count(*) FROM {HOSP}.diagnoses_icd d JOIN {HOSP}.admissions a "
        "ON a.hadm_id = d.hadm_id WHERE a.dischtime IS NOT NULL"
    ),
    "procedures_icd": f"SELECT count(*) FROM {HOSP}.procedures_icd WHERE chartdate IS NOT NULL",
    "labevents": f"SELECT count(*) FROM {HOSP}.labevents WHERE charttime IS NOT NULL",
    "microbiologyevents": (
        f"SELECT count(*) FROM {HOSP}.microbiologyevents "
        "WHERE coalesce(charttime, chartdate) IS NOT NULL"
    ),
    "prescriptions": (
        f"SELECT (SELECT count(*) FROM {HOSP}.prescriptions WHERE starttime IS NOT NULL) + "
        f"(SELECT count(*) FROM {HOSP}.prescriptions WHERE stoptime IS NOT NULL)"
    ),
    "emar": f"SELECT count(*) FROM {HOSP}.emar WHERE charttime IS NOT NULL",
    "inputevents": (
        f"SELECT (SELECT count(*) FROM {ICU}.inputevents WHERE starttime IS NOT NULL) + "
        f"(SELECT count(*) FROM {ICU}.inputevents WHERE starttime IS NOT NULL "
        "AND rate IS NOT NULL)"
    ),
    "outputevents": f"SELECT count(*) FROM {ICU}.outputevents WHERE charttime IS NOT NULL",
    "procedureevents": f"SELECT count(*) FROM {ICU}.procedureevents WHERE starttime IS NOT NULL",
}


def _scalar(con: duckdb.DuckDBPyConnection, sql: str) -> Any:
    row = con.execute(sql).fetchone()
    return row[0] if row else None


@pytest.fixture
def settings_root(tmp_path: Path) -> Settings:
    from mimicwarehouse.config import Settings

    return Settings(data_root=tmp_path / "root")


# ---------------------------------------------------------------------------
# 1. Registry, grammar bounds, DAG wiring
# ---------------------------------------------------------------------------


def test_registry_shape(contract: Contract) -> None:
    names = [s.name for s in sp.SPINE_SOURCES]
    assert len(names) == 13 and len(set(names)) == 13
    assert names == list(EXPECTED_COUNT_SQL)
    for s in sp.SPINE_SOURCES:
        assert s.name == s.table and contract.has_table(s.qualified_name), s.name
        for schema, table in s.joins:
            assert contract.has_table(f"{schema}.{table}")
        assert s.rules and all(r.code for r in s.rules)
        assert s.step_name == f"spine.{s.name}" and s.status_key == f"spine.{s.name}"
        assert s.stage_steps[0] == f"stage.{s.schema_name}.{s.table}"
        assert re.fullmatch(r"[0-9a-f]{64}", s.sql_sha256)
        assert sp.source(s.name) is s and sp.source_for_step(s.step_name) is s
    assert sp.source("diagnoses_icd").joins == ((HOSP, "admissions"),)
    assert set(sp.catalog_relations()) == {t for s in sp.SPINE_SOURCES for _, t in s.tables}
    with pytest.raises(sp.SpineError, match="no spine source"):
        sp.source("chartevents")
    with pytest.raises(sp.SpineError, match="not a spine"):
        sp.source_for_step(sp.UNION_STEP)
    # the 64-char bound is the safe-query free-text bound, and every cut respects it
    assert sp.CODE_MAX_CHARS == sp.TEXT_MAX_CHARS == safe.FREE_TEXT_MAX_CHARS
    assert len("MEDICATION_START//") + sp.DRUG_MAX_CHARS <= sp.CODE_MAX_CHARS
    assert (
        len("EMAR//") + sp.EMAR_MEDICATION_MAX_CHARS + len("//") + sp.EMAR_EVENT_MAX_CHARS
        <= sp.CODE_MAX_CHARS
    )
    assert sp.LAB_TEXT_MAX_CHARS < sp.TEXT_MAX_CHARS
    assert [n for n, _ in sp.SPINE_COLUMNS] == list(sp.COLUMN_NAMES)
    assert sp.COLUMN_NAMES[:5] == tuple(n for n, _, _ in sp.MEDS_COLUMNS)


def test_spec_wiring() -> None:
    assert "spine.yaml" in [p.name for p in spec_mod.spec_paths()]
    assert sp.spec_path().is_file()
    dag = load_dag()
    for s in sp.SPINE_SOURCES:
        step = dag.step(s.step_name)
        assert step.kind == "python" and step.callable_name == "mimicwarehouse.spine:build_source"
        assert step.target == s.status_key and step.qualified_table == s.status_key
        assert step.tags == (sp.TAG,)
        assert step.tiers == ("fixture", "demo", "dev", "full")
        assert set(step.depends_on) == set(s.stage_steps), s.name
    union = dag.step(sp.UNION_STEP)
    assert union.callable_name == "mimicwarehouse.spine:build_union" and union.target is None
    assert set(union.depends_on) == set(SOURCE_STEPS)
    catalog = dag.step("catalog")
    assert sp.TAG in catalog.tags and sp.UNION_STEP in catalog.depends_on
    ordered = [s.name for s in dag.ordered(tags=[sp.TAG], tier="fixture")]
    assert set(ordered) == {*SOURCE_STEPS, sp.UNION_STEP, "catalog"}
    assert ordered.index(sp.UNION_STEP) > max(ordered.index(n) for n in SOURCE_STEPS)
    assert ordered[-1] == "catalog"
    # the catalog extension sits after the walker, before the phenotype / units pins
    extensions = build_mod.CATALOG_EXTENSIONS
    assert sp.register_spine in extensions
    assert extensions.index(sp.register_spine) > extensions.index(register_derived)
    assert extensions.index(register_phenotypes) == len(extensions) - 2
    assert extensions[-1] is register_units


# ---------------------------------------------------------------------------
# 2. Projections compile on the fixture catalog
# ---------------------------------------------------------------------------


def test_every_projection_compiles_on_fixture_catalog(
    fixture_catalog: duckdb.DuckDBPyConnection,
) -> None:
    expected = [(n, t) for n, t in sp.SPINE_COLUMNS]
    for s in sp.SPINE_SOURCES:
        sql = s.select_sql()
        described = [
            (str(r[0]), str(r[1])) for r in fixture_catalog.execute(f"DESCRIBE {sql}").fetchall()
        ]
        assert described == expected, s.name
        assert sp.check_source(s, con=fixture_catalog) == [], s.name
        n, code_max, text_max = fixture_catalog.execute(
            f"SELECT count(*), max(length(code)), max(length(text_value)) FROM ({sql})"
        ).fetchone()  # type: ignore[misc]
        assert n > 0, s.name
        assert code_max <= sp.CODE_MAX_CHARS, s.name
        assert text_max is None or text_max <= sp.TEXT_MAX_CHARS, s.name
        assert n == _scalar(fixture_catalog, EXPECTED_COUNT_SQL[s.name]), s.name
    assert sp.check_sources(con=fixture_catalog) == {}


# ---------------------------------------------------------------------------
# 3. A crafted admission -> the expected event set, in order
# ---------------------------------------------------------------------------

LONG_DRUG = "X" * 50
LONG_MED = "M" * 40
LONG_EVENT = "Delayed Administered Extra Long Text!!"


@pytest.fixture(scope="module")
def crafted() -> duckdb.DuckDBPyConnection:
    """One synthetic subject / admission / stay with hand-placed rows in every source."""
    con = duckdb.connect()
    con.execute(f"CREATE SCHEMA {HOSP}")
    con.execute(f"CREATE SCHEMA {ICU}")
    ddl = {
        f"{HOSP}.patients": "subject_id INTEGER, gender VARCHAR, anchor_age SMALLINT, "
        "anchor_year SMALLINT, anchor_year_group VARCHAR, dod DATE",
        f"{HOSP}.admissions": "subject_id INTEGER, hadm_id INTEGER, admittime TIMESTAMP, "
        "dischtime TIMESTAMP, admission_type VARCHAR, admission_location VARCHAR, "
        "discharge_location VARCHAR",
        f"{HOSP}.transfers": "subject_id INTEGER, hadm_id INTEGER, transfer_id INTEGER, "
        "eventtype VARCHAR, careunit VARCHAR, intime TIMESTAMP",
        f"{ICU}.icustays": "subject_id INTEGER, hadm_id INTEGER, stay_id INTEGER, "
        "first_careunit VARCHAR, last_careunit VARCHAR, intime TIMESTAMP, outtime TIMESTAMP",
        f"{HOSP}.diagnoses_icd": "subject_id INTEGER, hadm_id INTEGER, seq_num INTEGER, "
        "icd_code VARCHAR, icd_version SMALLINT",
        f"{HOSP}.procedures_icd": "subject_id INTEGER, hadm_id INTEGER, seq_num INTEGER, "
        "chartdate DATE, icd_code VARCHAR, icd_version SMALLINT",
        f"{HOSP}.labevents": "subject_id INTEGER, hadm_id INTEGER, itemid INTEGER, "
        "charttime TIMESTAMP, value VARCHAR, valuenum DOUBLE, valueuom VARCHAR, comments VARCHAR",
        f"{HOSP}.microbiologyevents": "subject_id INTEGER, hadm_id INTEGER, chartdate TIMESTAMP, "
        "charttime TIMESTAMP, spec_itemid INTEGER, org_itemid INTEGER, interpretation VARCHAR, "
        "comments VARCHAR",
        f"{HOSP}.prescriptions": "subject_id INTEGER, hadm_id INTEGER, starttime TIMESTAMP, "
        "stoptime TIMESTAMP, drug VARCHAR, route VARCHAR",
        f"{HOSP}.emar": "subject_id INTEGER, hadm_id INTEGER, charttime TIMESTAMP, "
        "medication VARCHAR, event_txt VARCHAR",
        f"{ICU}.inputevents": "subject_id INTEGER, hadm_id INTEGER, stay_id INTEGER, "
        "starttime TIMESTAMP, itemid INTEGER, amount DOUBLE, amountuom VARCHAR, rate DOUBLE, "
        "rateuom VARCHAR",
        f"{ICU}.outputevents": "subject_id INTEGER, hadm_id INTEGER, stay_id INTEGER, "
        "charttime TIMESTAMP, itemid INTEGER, value DOUBLE, valueuom VARCHAR",
        f"{ICU}.procedureevents": "subject_id INTEGER, hadm_id INTEGER, stay_id INTEGER, "
        "starttime TIMESTAMP, itemid INTEGER, value DOUBLE, valueuom VARCHAR",
    }
    for table, columns in ddl.items():
        con.execute(f"CREATE TABLE {table} ({columns})")
    con.execute(
        f"INSERT INTO {HOSP}.patients VALUES ({S1}, 'F', 91, 2150, '2014 - 2016', "
        "DATE '2150-06-01')"
    )
    con.execute(
        f"INSERT INTO {HOSP}.admissions VALUES ({S1}, {H1}, '2150-01-01 10:00', "
        "'2150-01-05 12:00', 'EW EMER.', 'EMERGENCY ROOM', NULL)"
    )
    con.execute(
        f"INSERT INTO {HOSP}.transfers VALUES ({S1}, {H1}, 1, 'admit', 'Emergency Department', "
        f"'2150-01-01 10:00'), ({S1}, {H1}, 2, 'discharge', NULL, '2150-01-05 12:00')"
    )
    con.execute(
        f"INSERT INTO {ICU}.icustays VALUES ({S1}, {H1}, {ST1}, 'MICU', 'SICU', "
        "'2150-01-02 00:00', NULL)"
    )
    con.execute(f"INSERT INTO {HOSP}.diagnoses_icd VALUES ({S1}, {H1}, 1, 'E119', 10)")
    con.execute(
        f"INSERT INTO {HOSP}.procedures_icd VALUES ({S1}, {H1}, 2, DATE '2150-01-03', "
        "'0DJ08ZZ', 10)"
    )
    con.execute(
        f"INSERT INTO {HOSP}.labevents VALUES "
        f"({S1}, {H1}, 50912, '2150-01-02 06:00', '1.2', 1.2, 'mg/dL', 'FREE TEXT COMMENT'), "
        f"({S1}, NULL, 51464, '2150-01-02 07:00', 'NEG', NULL, NULL, NULL), "
        f"({S1}, {H1}, 51464, '2150-01-02 08:00', '{'T' * 40}', NULL, NULL, NULL), "
        f"({S1}, {H1}, 50912, NULL, '9.9', 9.9, 'mg/dL', NULL)"
    )
    con.execute(
        f"INSERT INTO {HOSP}.microbiologyevents VALUES "
        f"({S1}, {H1}, '2150-01-02 00:00', NULL, 70012, NULL, NULL, 'NO GROWTH.'), "
        f"({S1}, {H1}, '2150-01-03 00:00', '2150-01-03 08:00', 70012, 80002, 'S', NULL)"
    )
    con.execute(
        f"INSERT INTO {HOSP}.prescriptions VALUES "
        f"({S1}, {H1}, '2150-01-01 12:00', '2150-01-04 12:00', '{LONG_DRUG}', 'IV'), "
        f"({S1}, {H1}, '2150-01-02 12:00', NULL, NULL, 'PO')"
    )
    con.execute(
        f"INSERT INTO {HOSP}.emar VALUES ({S1}, {H1}, '2150-01-01 13:00', '{LONG_MED}', "
        f"'{LONG_EVENT}')"
    )
    con.execute(
        f"INSERT INTO {ICU}.inputevents VALUES "
        f"({S1}, {H1}, {ST1}, '2150-01-02 01:00', 220949, 100.0, 'mL', 50.0, 'mL/hour'), "
        f"({S1}, {H1}, {ST1}, '2150-01-02 02:00', 225158, 250.0, 'mL', NULL, NULL)"
    )
    con.execute(
        f"INSERT INTO {ICU}.outputevents VALUES ({S1}, {H1}, {ST1}, '2150-01-02 03:00', "
        "226559, 300.0, 'mL')"
    )
    con.execute(
        f"INSERT INTO {ICU}.procedureevents VALUES ({S1}, {H1}, {ST1}, '2150-01-02 04:00', "
        "225802, 120.0, 'min')"
    )
    return con


#: (time, code, numeric_value, text_value, hadm_id, stay_id, source_table) in spine order.
EXPECTED_EVENTS: list[tuple[str, str, float | None, str | None, int | None, int | None, str]] = [
    ("2059-01-01 00:00:00", "MEDS_BIRTH", None, "age_capped", None, None, "patients"),
    (
        "2150-01-01 10:00:00",
        "HOSPITAL_ADMISSION//EW EMER.",
        None,
        "EMERGENCY ROOM",
        H1,
        None,
        "admissions",
    ),
    (
        "2150-01-01 10:00:00",
        "TRANSFER_TO//Emergency Department",
        None,
        "admit",
        H1,
        None,
        "transfers",
    ),
    ("2150-01-01 12:00:00", f"MEDICATION_START//{'X' * 46}", None, "IV", H1, None, "prescriptions"),
    ("2150-01-01 13:00:00", f"EMAR//{'M' * 34}//{LONG_EVENT[:22]}", None, None, H1, None, "emar"),
    ("2150-01-02 00:00:00", "ICU_ADMISSION//MICU", None, None, H1, ST1, "icustays"),
    ("2150-01-02 00:00:00", "MICRO//70012//NONE", None, None, H1, None, "microbiologyevents"),
    ("2150-01-02 01:00:00", "INPUT//220949", 100.0, "mL", H1, ST1, "inputevents"),
    ("2150-01-02 01:00:00", "INPUT_RATE//220949", 50.0, "mL/hour", H1, ST1, "inputevents"),
    ("2150-01-02 02:00:00", "INPUT//225158", 250.0, "mL", H1, ST1, "inputevents"),
    ("2150-01-02 03:00:00", "OUTPUT//226559", 300.0, "mL", H1, ST1, "outputevents"),
    ("2150-01-02 04:00:00", "ICU_PROCEDURE//225802", 120.0, "min", H1, ST1, "procedureevents"),
    ("2150-01-02 06:00:00", "LAB//50912//mg/dL", 1.2, None, H1, None, "labevents"),
    ("2150-01-02 07:00:00", "LAB//51464//NONE", None, "NEG", None, None, "labevents"),
    ("2150-01-02 08:00:00", "LAB//51464//NONE", None, None, H1, None, "labevents"),
    ("2150-01-02 12:00:00", "MEDICATION_START//NONE", None, "PO", H1, None, "prescriptions"),
    ("2150-01-03 00:00:00", "PROCEDURE//ICD10//0DJ08ZZ", 2.0, None, H1, None, "procedures_icd"),
    ("2150-01-03 08:00:00", "MICRO//70012//80002", None, "S", H1, None, "microbiologyevents"),
    ("2150-01-04 12:00:00", f"MEDICATION_STOP//{'X' * 46}", None, "IV", H1, None, "prescriptions"),
    ("2150-01-05 12:00:00", "DIAGNOSIS//ICD10//E119", 1.0, None, H1, None, "diagnoses_icd"),
    ("2150-01-05 12:00:00", "HOSPITAL_DISCHARGE//NONE", None, None, H1, None, "admissions"),
    ("2150-01-05 12:00:00", "TRANSFER_TO//NONE", None, "discharge", H1, None, "transfers"),
    ("2150-06-01 00:00:00", "MEDS_DEATH", None, None, None, None, "patients"),
]


def test_crafted_admission_event_set(crafted: duckdb.DuckDBPyConnection) -> None:
    union = " UNION ALL ".join(s.select_sql() for s in sp.SPINE_SOURCES)
    order = ", ".join(sp.ORDER_BY)
    rows = crafted.execute(
        "SELECT CAST(time AS VARCHAR), code, round(CAST(numeric_value AS DOUBLE), 3), "
        f"text_value, hadm_id, stay_id, source_table FROM ({union}) ORDER BY {order}"
    ).fetchall()
    assert [tuple(r) for r in rows] == EXPECTED_EVENTS
    # the null-time lab row is dropped, subject ids are BIGINT, numeric values float32
    described = {
        str(r[0]): str(r[1])
        for r in crafted.execute(f"DESCRIBE {sp.source('labevents').select_sql()}").fetchall()
    }
    assert described["subject_id"] == "BIGINT" and described["numeric_value"] == "FLOAT"
    assert all(len(e[1]) <= sp.CODE_MAX_CHARS for e in EXPECTED_EVENTS)


def test_denied_column_is_refused(crafted: duckdb.DuckDBPyConnection) -> None:
    micro = sp.source("microbiologyevents")
    bad = sp.SpineSource(
        micro.name,
        micro.schema_name,
        micro.table,
        micro.projection.replace(
            "CAST(m.interpretation AS VARCHAR)", "CAST(m.comments AS VARCHAR)"
        ),
        micro.rules,
    )
    assert "m.comments" in bad.select_sql()
    assert sp.check_source(bad, con=crafted) == ["comments"]
    assert sp.check_sources([micro, bad], con=crafted) == {"microbiologyevents": ["comments"]}
    assert sp.check_sources([micro], con=crafted) == {}
    assert sp.check_source(micro, con=crafted) == []
    # the contract's free_text flags join the denied set (keys.yaml)
    assert {"comments"} <= sp.denied_columns_for(sp.source("labevents"))
    assert sp.referenced_columns(bad.select_sql(), crafted) >= {"comments", "subject_id"}
    with pytest.raises(sp.SpineError, match="does not parse"):
        sp.referenced_columns("SELECT FROM WHERE", crafted)


# ---------------------------------------------------------------------------
# 4. The session fixture lake: every source built, registered, validated
# ---------------------------------------------------------------------------


def test_fixture_lake_carries_the_spine(
    fixture_lake_settings: Settings,
    fixture_lake_catalog: duckdb.DuckDBPyConnection,
) -> None:
    settings = fixture_lake_settings
    lake = settings.lake_root("fixture")
    con = fixture_lake_catalog
    status = manifest_mod.read_status(lake)["steps"]
    for s in sp.SPINE_SOURCES:
        entry = status[s.status_key]
        assert entry["per_tier"] is True and entry["layer"] == "derived"
        assert entry["tier_complete"] == "full"
        attempt = entry["tiers"]["fixture"]
        assert attempt["status"] == "done" and attempt["files"] >= 1
        assert attempt["sql_sha256"] == s.sql_sha256
        assert sp.source_complete(lake, "fixture", s.name)
        directory = sp.source_dir(lake, "fixture", s.name)
        files = sorted(directory.glob(f"{sp.BUCKET_COLUMN}=*/part-0.parquet"))
        assert len(files) == attempt["files"]
        assert all(sp.check_file_schema(f) is None for f in files)
    union_entry = status[sp.STATUS_KEY]
    assert union_entry["tiers"]["fixture"]["validation_ok"] is True
    assert set(union_entry["tiers"]["fixture"]["sources"]) == {s.name for s in sp.SPINE_SOURCES}
    assert sp.union_complete(lake, "fixture")
    assert [s.name for s in sp.complete_sources(lake, "fixture")] == [
        s.name for s in sp.SPINE_SOURCES
    ]
    # manifests: one line per file, schema spine, provenance pair
    lines = [
        ln
        for jsonl in sorted(manifest_mod.manifests_dir(lake).glob("*.jsonl"))
        for ln in manifest_mod.iter_manifest(jsonl)
        if ln.schema_name == sp.SPINE_DIRNAME
    ]
    by_table: dict[str, int] = {}
    for ln in lines:
        by_table[ln.table] = by_table.get(ln.table, 0) + 1
        assert ln.path.startswith(f"derived/fixture/{sp.SPINE_DIRNAME}/source={ln.table}/")
        assert ln.source_sha256 == sp.source(ln.table).sql_sha256
        assert ln.raw_snapshot_id
    assert {t: n for t, n in by_table.items()} == {
        s.name: status[s.status_key]["tiers"]["fixture"]["files"] for s in sp.SPINE_SOURCES
    }
    history = snapshot_mod.read_snapshots(lake)
    assert ("derived", "fixture") in {(e["layer"], e["tier"]) for e in history}
    # the catalog: the view (MEDS order first), no `spine` schema, the two meta tables
    described = [
        (str(r[0]), str(r[1])) for r in con.execute("DESCRIBE mimiciv_derived.spine").fetchall()
    ]
    assert described == list(sp.SPINE_COLUMNS)
    schemata = {
        str(r[0])
        for r in con.execute("SELECT schema_name FROM information_schema.schemata").fetchall()
    }
    assert sp.SPINE_DIRNAME not in schemata
    comment = _scalar(con, "SELECT comment FROM duckdb_views() WHERE view_name = 'spine'")
    assert "EP-50" in str(comment) and "MEDS" in str(comment)
    per_source = dict(
        con.execute(
            "SELECT source_table, count(*) FROM mimiciv_derived.spine GROUP BY 1"
        ).fetchall()
    )
    assert set(per_source) == {s.name for s in sp.SPINE_SOURCES}
    for s in sp.SPINE_SOURCES:
        assert per_source[s.name] == _scalar(con, EXPECTED_COUNT_SQL[s.name]), s.name
        assert per_source[s.name] == status[s.status_key]["tiers"]["fixture"]["rows"]
    assert _scalar(con, "SELECT count(*) FROM mimiciv_derived.spine WHERE code = 'MEDS_BIRTH'") == (
        _scalar(con, f"SELECT count(*) FROM {HOSP}.patients")
    )
    assert _scalar(
        con,
        "SELECT count(*) FROM mimiciv_derived.spine WHERE code = 'MEDS_BIRTH' "
        f"AND text_value = '{sp.AGE_CAPPED_TEXT}'",
    ) == _scalar(con, f"SELECT count(*) FROM {HOSP}.patients WHERE anchor_age >= 91")
    assert _scalar(con, "SELECT max(length(code)) FROM mimiciv_derived.spine") <= sp.CODE_MAX_CHARS
    # meta.spine_codes: every source, k stamped, no unmarked small cell
    codes = con.execute(f"SELECT * FROM meta.{sp.CODES_TABLE}").pl()
    assert list(codes.columns) == [n for n, _ in sp.CODES_COLUMNS]
    assert set(codes["source_table"].to_list()) == {s.name for s in sp.SPINE_SOURCES}
    assert set(codes["k"].to_list()) == {K}
    for column in ("n_events", "n_subjects"):
        released = [
            v
            for v, hidden in zip(
                codes[column].to_list(), codes[f"{column}_suppressed"].to_list(), strict=True
            )
            if not hidden
        ]
        assert all(v is not None and (v == 0 or v >= K) for v in released), column
    assert [f.code for f in disclose.check_frame(codes, K) if f.status == disclose.FAIL] == []
    # meta.spine_validation: the union's row, ok
    validation = con.execute(
        f"SELECT tier, n_sources, ok, failed_checks FROM meta.{sp.VALIDATION_TABLE}"
    ).fetchall()
    assert validation and validation[-1][0] == "fixture" and validation[-1][1] == 13
    assert validation[-1][2] is True and validation[-1][3] is None
    # validate() itself (no write) and the safe-query reads a session would run
    report = sp.validate("fixture", settings=settings, write=False)
    assert report.ok and [c.name for c in report.checks] == list(sp.CHECK_NAMES)
    assert report.n_rows == sum(per_source.values()) and len(report.sources) == 13
    frame = safe.safe_query(
        "SELECT source_table, count(*) AS n FROM mimiciv_derived.spine GROUP BY 1 ORDER BY 1",
        tier="fixture",
        settings=settings,
        actor="test_ep50",
    ).df
    assert frame.height == 13 and set(frame["source_table"].to_list()) == set(per_source)
    codes_read = safe.safe_query(
        "SELECT code, count(*) AS n FROM mimiciv_derived.spine WHERE source_table = 'labevents' "
        "GROUP BY 1 ORDER BY 2 DESC",
        tier="fixture",
        settings=settings,
        actor="test_ep50",
    ).df
    assert codes_read.height > 0 and all(
        c.startswith("LAB//") for c in codes_read["code"].to_list()
    )
    text_read = safe.safe_query(
        "SELECT text_value, count(*) AS n FROM mimiciv_derived.spine "
        "WHERE source_table = 'transfers' GROUP BY 1 ORDER BY 2 DESC",
        tier="fixture",
        settings=settings,
        actor="test_ep50",
    ).df
    assert text_read.height > 0
    print(f"fixture spine: {report.n_rows:,} rows in {report.n_files:,} files over 13 sources")


def test_validate_refuses_a_crafted_projection(fixture_lake_settings: Settings) -> None:
    micro = sp.source("microbiologyevents")
    bad = sp.SpineSource(
        micro.name,
        micro.schema_name,
        micro.table,
        micro.projection.replace(
            "CAST(m.interpretation AS VARCHAR)", "CAST(m.comments AS VARCHAR)"
        ),
        micro.rules,
    )
    report = sp.validate("fixture", settings=fixture_lake_settings, write=False, sources=[bad])
    assert not report.ok and report.failed == [sp.CHECK_DENIED]
    denied = next(c for c in report.checks if c.name == sp.CHECK_DENIED)
    assert denied.n_bad == 1 and "comments" in denied.detail
    assert report.sources == ["microbiologyevents"]
    with pytest.raises(sp.SpineError, match="unknown tier"):
        sp.validate("nope", settings=fixture_lake_settings)


# ---------------------------------------------------------------------------
# 5. Select / resume / force on a temp root; the union without a source
# ---------------------------------------------------------------------------


def test_select_resume_force_and_registration(settings_root: Settings) -> None:
    from mimicwarehouse import run as run_mod

    settings = settings_root
    dag = load_dag()
    # --with-deps on the union would pull every source (its ancestors); select one source
    select = ["spine.patients"]
    result = runner_mod.run(
        dag, "fixture", select=select, with_deps=True, settings=settings, provenance=True
    )
    assert result.ok and result.run_id is not None
    statuses = {s.name: s.status for s in result.steps}
    assert statuses == {f"stage.{HOSP}.patients": "done", "spine.patients": "done"}
    assert set(result.snapshot_ids) == {"core", "derived"}
    union = runner_mod.run(
        dag, "fixture", select=[sp.UNION_STEP], settings=settings, provenance=True
    )
    assert union.ok and union.run_id is not None
    assert {s.name: s.status for s in union.steps} == {sp.UNION_STEP: "done"}
    lake = settings.lake_root("fixture")
    directory = sp.source_dir(lake, "fixture", "patients")
    files = sorted(directory.glob(f"{sp.BUCKET_COLUMN}=*/part-0.parquet"))
    assert (
        files
        and not sp.source_dir(lake, "fixture", "patients").with_name("source=patients.new").exists()
    )
    entry = manifest_mod.read_status(lake)["steps"]["spine.patients"]
    attempt = entry["tiers"]["fixture"]
    assert attempt["run_id"] == result.run_id and attempt["files"] == len(files)
    assert attempt["rows"] > 0
    lines = list(manifest_mod.iter_manifest(manifest_mod.manifest_path(lake, result.build_id)))
    spine_lines = [ln for ln in lines if ln.schema_name == sp.SPINE_DIRNAME]
    assert len(spine_lines) == len(files)
    assert all(ln.raw_snapshot_id == result.snapshot_ids["core"] for ln in spine_lines)
    assert sum(ln.rows for ln in spine_lines) == attempt["rows"]
    assert sp.meta_path(lake, "fixture", sp.CODES_TABLE).is_file()
    assert sp.meta_path(lake, "fixture", sp.CODES_TABLE, raw=True).is_file()
    assert sp.meta_path(lake, "fixture", sp.VALIDATION_TABLE).is_file()
    # the run manifest cites the source, the ledger carries the kind: mart lines
    m = run_mod.read_manifest(result.run_id, settings)
    assert ("spine_source", "patients") in [(r.kind, r.name) for r in m.refs]
    assert m.snapshot_ids == result.snapshot_ids
    ledger = benchmarks_mod.read(settings)
    marts = ledger.filter(ledger["kind"] == sp.BENCH_KIND)
    assert set(marts["step"].to_list()) == {"spine.patients", sp.UNION_STEP}
    assert all(marts["ok"].to_list())
    assert dict(zip(marts["step"].to_list(), marts["run_id"].to_list(), strict=True)) == {
        "spine.patients": result.run_id,
        sp.UNION_STEP: union.run_id,
    }
    # resume: the source is skipped; the union (no target) runs whenever selected
    again = runner_mod.run(dag, "fixture", select=select, with_deps=True, settings=settings)
    assert {s.name: s.status for s in again.steps} == {
        f"stage.{HOSP}.patients": "skipped",
        "spine.patients": "skipped",
    }
    assert again.snapshot_ids == result.snapshot_ids
    # --force rebuilds the selected source only; the derived id does not move
    forced = runner_mod.run(
        dag, "fixture", select=select, with_deps=True, force=True, settings=settings
    )
    assert {s.name: s.status for s in forced.steps} == {
        f"stage.{HOSP}.patients": "skipped",
        "spine.patients": "done",
    }
    assert forced.snapshot_ids["derived"] == result.snapshot_ids["derived"]
    # the union again, then the catalog step registers the view over the one source
    cat = runner_mod.run(dag, "fixture", select=[sp.UNION_STEP, "catalog"], settings=settings)
    assert cat.ok and [s.status for s in cat.steps] == ["done", "done"]
    con = open_catalog("fixture", settings=settings)
    try:
        per_source = dict(
            con.execute(
                "SELECT source_table, count(*) FROM mimiciv_derived.spine GROUP BY 1"
            ).fetchall()
        )
        assert set(per_source) == {"patients"} and per_source["patients"] == attempt["rows"]
        prefixes = {
            r[0] for r in con.execute(f"SELECT code_prefix FROM meta.{sp.CODES_TABLE}").fetchall()
        }
        assert prefixes == {sp.BIRTH_CODE, sp.DEATH_CODE}
        assert _scalar(con, f"SELECT count(*) FROM meta.{sp.VALIDATION_TABLE}") == 2
    finally:
        con.close()


def test_union_without_a_source_fails_with_the_remedy(settings_root: Settings) -> None:
    result = runner_mod.run(load_dag(), "fixture", select=[sp.UNION_STEP], settings=settings_root)
    assert not result.ok
    (step,) = result.steps
    assert step.status == "failed" and "no spine source is complete" in str(step.error)
    assert "--tag spine" in str(step.error)


# ---------------------------------------------------------------------------
# 6. CLI, docs page, import budget
# ---------------------------------------------------------------------------


def test_cli_sources_and_validate(fixture_lake_settings: Settings, tmp_path: Path) -> None:
    runner = helpers.cli_runner()
    root = ["--data-root", str(fixture_lake_settings.data_root)]
    result = runner.invoke(app, ["spine", "sources"])
    assert result.exit_code == 0, result.output
    assert "13 source(s)" in result.output and "labevents" in result.output
    result = runner.invoke(app, ["spine", "sources", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert [p["name"] for p in payload] == [s.name for s in sp.SPINE_SOURCES]
    assert payload[0]["codes"] == [sp.BIRTH_CODE, sp.DEATH_CODE]
    result = runner.invoke(app, [*root, "spine", "validate", "--tier", "fixture", "--no-write"])
    assert result.exit_code == 0, result.output
    assert "OK" in result.output and sp.CHECK_SORT in result.output
    result = runner.invoke(
        app, [*root, "spine", "validate", "--tier", "fixture", "--no-write", "--json"]
    )
    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert report["ok"] is True and len(report["checks"]) == len(sp.CHECK_NAMES)
    result = runner.invoke(app, [*root, "spine", "validate", "--tier", "nope"])
    assert result.exit_code == 2 and "unknown tier" in result.output
    empty = tmp_path / "empty"
    empty.mkdir()
    result = runner.invoke(
        app, ["--data-root", str(empty), "spine", "validate", "--tier", "fixture"]
    )
    assert result.exit_code == 2 and "no spine source is complete" in result.output
    # the build plan lists the spine steps under the tag
    result = runner.invoke(app, [*root, "build", "--tier", "fixture", "--dry-run", "--tag", sp.TAG])
    assert result.exit_code == 0, result.output
    assert sp.UNION_STEP in result.output and "spine.labevents" in result.output


def test_methods_doc_is_in_sync(tmp_path: Path) -> None:
    page = sp.methods_doc_path()
    assert page.is_file()
    text = page.read_text(encoding="utf-8")
    copy = tmp_path / "spine.md"
    copy.write_text(text, encoding="utf-8", newline="\n")
    sp.sync_methods_doc(copy)
    assert copy.read_text(encoding="utf-8") == text, "run: uv run python -m mimicwarehouse.spine"
    for s in sp.SPINE_SOURCES:
        assert f"`{s.qualified_name}`" in text
        for rule in s.rules:
            assert f"`{rule.code}`" in text
    for name, _, arrow in sp.MEDS_COLUMNS:
        assert f"`{name}`" in text and f"`{arrow}`" in text
    assert "MIMIC-IV analyses in this repository are retrospective" in text
    assert disclose.check(page, K).passed
    assert not BAND_TOKEN.search(text)


def test_import_budget() -> None:
    helpers.assert_import_budget(lazy=("mimicwarehouse.run", "mimicwarehouse.safe"))
    helpers.assert_import_budget(
        "mimicwarehouse.spine",
        lazy=(
            "mimicwarehouse.run",
            "mimicwarehouse.safe",
            "mimicwarehouse.disclose",
            "mimicwarehouse.schema.contract",
            "mimicwarehouse.loader.manifest",
            "mimicwarehouse.concepts.runner",
            "mimicwarehouse.catalog.build",
        ),
    )


# ---------------------------------------------------------------------------
# 7. Dev tier (aggregates only)
# ---------------------------------------------------------------------------


@pytest.mark.tier("dev")
def test_dev_build_query_and_validate(dev_catalog: Path) -> None:
    from mimicwarehouse.dag import jobs as jobs_mod

    settings = config.load_settings()
    lock = runner_mod.lock_path(settings)
    if lock.is_file():
        try:
            held = json.loads(lock.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            held = {}
        if held.get("pid") and jobs_mod.pid_alive(int(held["pid"]), held.get("create_time")):
            pytest.skip(f"a build holds {lock} (pid {held['pid']}); rerun when it finishes")
    result = helpers.cli_runner().invoke(app, ["build", "--tier", "dev", "--tag", sp.TAG])
    assert result.exit_code == 0, result.output
    assert "failed" not in result.output.lower()
    per_source = safe.safe_query(
        "SELECT source_table, count(*) AS n FROM mimiciv_derived.spine GROUP BY 1 ORDER BY 1",
        tier="dev",
        settings=settings,
        actor="test_ep50",
    ).df
    assert set(per_source["source_table"].to_list()) == {s.name for s in sp.SPINE_SOURCES}
    codes = safe.safe_query(
        f"SELECT source_table, code_prefix, n_events FROM meta.{sp.CODES_TABLE} ORDER BY 1, 2",
        tier="dev",
        settings=settings,
        actor="test_ep50",
    ).df
    assert set(codes["source_table"].to_list()) == {s.name for s in sp.SPINE_SOURCES}
    report = sp.validate("dev", settings=settings, write=False)
    assert report.ok, report.failed
    print(
        f"dev spine: {report.n_rows:,} rows in {report.n_files:,} files over "
        f"{len(report.sources)} sources; {codes.height} code-prefix rows"
    )
