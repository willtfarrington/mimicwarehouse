"""EP-9 — schema registry: the YAML contract, its loader, the DDL renderer, the transcriber /
drift oracle and ``mwh schema``.

Fixture tier: everything here runs on the packaged YAML and the vendored mimic-code DDL (EP-8);
no data root is read. The in-memory DuckDB in ``test_all_ddl_execute_in_duckdb`` is opened with
``get_settings().duckdb_settings("app")`` (house rule, DESIGN §6). Synthetic DDL used to exercise
the parser carries no real-band ids.
"""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from mimicwarehouse import guard
from mimicwarehouse.cli import DIAGNOSTIC_COMMANDS, app
from mimicwarehouse.concepts import vendored_path
from mimicwarehouse.schema import (
    DATASETS,
    EXPECTED_TABLE_COUNTS,
    SCHEMAS,
    Column,
    ColumnMap,
    Contract,
    SchemaError,
    Table,
    TableMap,
    load_contract,
    load_contract_from,
    tables_root,
)
from mimicwarehouse.schema import transcribe as tr

pytestmark = pytest.mark.ep_9

WORKSPACE = Path(__file__).resolve().parents[2]
SCHEMA_DIR = WORKSPACE / "src" / "mimicwarehouse" / "schema"
YAML_FILES = sorted((SCHEMA_DIR / "tables").rglob("*.yaml"))

HOSP = [
    "admissions",
    "d_hcpcs",
    "diagnoses_icd",
    "d_icd_diagnoses",
    "d_icd_procedures",
    "d_labitems",
    "drgcodes",
    "emar_detail",
    "emar",
    "hcpcsevents",
    "labevents",
    "microbiologyevents",
    "omr",
    "patients",
    "pharmacy",
    "poe_detail",
    "poe",
    "prescriptions",
    "procedures_icd",
    "provider",
    "services",
    "transfers",
]
ICU = [
    "caregiver",
    "chartevents",
    "datetimeevents",
    "d_items",
    "icustays",
    "ingredientevents",
    "inputevents",
    "outputevents",
    "procedureevents",
]
ED = ["diagnosis", "edstays", "medrecon", "pyxis", "triage", "vitalsign"]
NOTE = ["discharge", "radiology", "discharge_detail", "radiology_detail"]
LARGE = set(tr.LARGE_TABLES)
NO_UPSTREAM_PK = {
    "mimiciv_hosp.drgcodes",
    "mimiciv_hosp.emar_detail",
    "mimiciv_hosp.omr",
    "mimiciv_hosp.provider",
    "mimiciv_icu.caregiver",
    "mimiciv_icu.chartevents",
    "mimiciv_icu.ingredientevents",
}


@pytest.fixture(scope="module")
def contract() -> Contract:
    load_contract.cache_clear()
    return load_contract()


# ---------------------------------------------------------------------------
# 1. Contract shape
# ---------------------------------------------------------------------------


def test_contract_loads_with_41_tables(contract: Contract) -> None:
    assert len(contract.tables) == 41
    assert EXPECTED_TABLE_COUNTS == {
        "mimiciv_hosp": 22,
        "mimiciv_icu": 9,
        "mimiciv_ed": 6,
        "mimiciv_note": 4,
    }
    for schema, n in EXPECTED_TABLE_COUNTS.items():
        assert len(contract.by_schema(schema)) == n, schema
    assert [t.name for t in contract.by_schema("mimiciv_hosp")] == HOSP
    assert [t.name for t in contract.by_schema("mimiciv_icu")] == ICU
    assert [t.name for t in contract.by_schema("mimiciv_ed")] == ED
    assert [t.name for t in contract.by_schema("mimiciv_note")] == NOTE
    assert contract.schema_names() == SCHEMAS
    assert {t.dataset for t in contract.tables} == set(DATASETS)
    assert len(contract.by_dataset("mimic-iv-3.1")) == 31
    assert len(contract.by_dataset("mimic-iv-ed-2.2")) == 6
    assert len(contract.by_dataset("mimic-iv-note-2.2")) == 4


def test_spot_checks(contract: Contract) -> None:
    patients = contract.table("mimiciv_hosp", "patients")
    assert patients.column_names == (
        "subject_id",
        "gender",
        "anchor_age",
        "anchor_year",
        "anchor_year_group",
        "dod",
    )
    assert patients.primary_key == ("subject_id",)
    assert "91" in (patients.column("anchor_age").comment or "")
    lab = contract.table("mimiciv_hosp.labevents")
    assert lab.column("valuenum").duckdb_type == "DOUBLE"
    assert lab.column("valueuom").unit_of == "valuenum"
    assert lab.load_class == "large"
    assert contract.table("mimiciv_hosp.d_icd_diagnoses").primary_key == ("icd_code", "icd_version")
    ce = contract.table("mimiciv_icu.chartevents")
    assert ce.column_names == (
        "subject_id",
        "hadm_id",
        "stay_id",
        "caregiver_id",
        "charttime",
        "storetime",
        "itemid",
        "value",
        "valuenum",
        "valueuom",
        "warning",
    )
    assert ce.primary_key is None and ce.uniqueness_hint == ("stay_id", "charttime", "itemid")
    assert contract.table("mimiciv_hosp.emar").column("emar_id").duckdb_type == "VARCHAR"
    assert contract.table("mimiciv_hosp.provider").column("provider_id").duckdb_type == "VARCHAR"
    assert contract.table("mimiciv_note.discharge").column("note_id").duckdb_type == "VARCHAR"
    for qn in (
        "mimiciv_hosp.labevents",
        "mimiciv_hosp.microbiologyevents",
        "mimiciv_hosp.transfers",
    ):
        t = contract.table(qn)
        idcol = {
            "labevents": "labevent_id",
            "microbiologyevents": "microevent_id",
            "transfers": "transfer_id",
        }[t.name]
        assert t.column(idcol).duckdb_type == "INTEGER"
    for t in contract.tables:
        for c in t.columns:
            if c.name in ("subject_id", "hadm_id", "stay_id"):
                assert c.duckdb_type == "INTEGER", f"{t.qualified_name}.{c.name}"


def test_metadata_rules(contract: Contract) -> None:
    for t in contract.tables:
        assert t.subject_keyed == t.has_column("subject_id"), t.qualified_name
        assert t.partitioned == t.subject_keyed, t.qualified_name
        if t.subject_keyed:
            assert t.sort_keys[0] == "subject_id", t.qualified_name
        else:
            assert t.sort_keys, t.qualified_name  # dims sort by their natural key
        assert (t.load_class == "large") == (t.name in LARGE), t.qualified_name
        assert t.csv_path == f"{t.schema_name.removeprefix('mimiciv_')}/{t.name}.csv"
        if t.time_column:
            assert t.column(t.time_column).duckdb_type in ("TIMESTAMP", "DATE")
        if t.primary_key is None:
            assert t.uniqueness_hint, f"{t.qualified_name} needs a uniqueness_hint"
        else:
            assert t.uniqueness_hint is None
        assert t.comment, t.qualified_name
        for c in t.columns:
            assert c.comment, f"{t.qualified_name}.{c.name} needs a comment (EP-29 inherits it)"
    dims = {t.qualified_name for t in contract.dims()}
    assert dims == {
        "mimiciv_hosp.d_hcpcs",
        "mimiciv_hosp.d_icd_diagnoses",
        "mimiciv_hosp.d_icd_procedures",
        "mimiciv_hosp.d_labitems",
        "mimiciv_hosp.provider",
        "mimiciv_icu.caregiver",
        "mimiciv_icu.d_items",
    }
    assert len(contract.subject_keyed()) == 41 - 7
    assert {t.name for t in contract.large()} == LARGE
    no_pk = {t.qualified_name for t in contract.tables if t.primary_key is None}
    assert no_pk == NO_UPSTREAM_PK | {f"mimiciv_ed.{n}" for n in ED} | {
        f"mimiciv_note.{n}" for n in NOTE
    }
    # expected_rows_source: validate.sql for hosp/icu except the three tables it omits; ED yes;
    # note none.
    for t in contract.tables:
        src = t.expected_rows_source
        if t.schema_name in ("mimiciv_hosp", "mimiciv_icu"):
            if t.name in ("provider", "caregiver", "ingredientevents"):
                assert src is None, t.qualified_name
            else:
                assert src == "mimic-iv/buildmimic/postgres/validate.sql", t.qualified_name
        elif t.schema_name == "mimiciv_ed":
            assert src == "mimic-iv-ed/buildmimic/postgres/validate.sql"
        else:
            assert src is None


def test_keys_reference_existing_columns_and_match_constraint_sql(contract: Contract) -> None:
    by_name = {t.qualified_name: t for t in contract.tables}
    for fk in contract.foreign_keys:
        for c in fk.columns:
            assert by_name[fk.table].has_column(c), fk
        for c in fk.ref_columns:
            assert by_name[fk.ref_table].has_column(c), fk
        assert fk.source in ("constraint.sql", "docs")
    upstream = [fk for fk in contract.foreign_keys if fk.source == "constraint.sql"]
    keys = tr.parse_constraint_sql(
        vendored_path("mimic-iv/buildmimic/postgres/constraint.sql").read_text("utf-8")
    )
    assert len(upstream) == len(keys.foreign_keys) == 51
    assert {(fk.table, fk.columns, fk.ref_table, fk.ref_columns) for fk in upstream} == {
        (a, b, c, d) for a, b, c, d, _ in keys.foreign_keys
    }
    for t in contract.by_dataset("mimic-iv-3.1"):
        assert keys.primary_keys.get(t.qualified_name) == t.primary_key, t.qualified_name
    docs = [fk for fk in contract.foreign_keys if fk.source == "docs"]
    assert {fk.table.split(".")[0] for fk in docs} == {"mimiciv_ed", "mimiciv_note"}
    assert (
        contract.foreign_keys_of("mimiciv_icu.chartevents")[2].ref_table == "mimiciv_icu.icustays"
    )


def test_units_and_column_map_name_existing_columns(contract: Contract) -> None:
    by_name = {t.qualified_name: t for t in contract.tables}
    named = contract.units.columns_named()
    assert named, "units.yaml is empty"
    for qn, col in named:
        assert by_name[qn].has_column(col), (qn, col)
    pairs = {(p.table, p.value, p.unit) for p in contract.units.value_unit_pairs}
    for want in (
        ("mimiciv_hosp.labevents", "valuenum", "valueuom"),
        ("mimiciv_icu.chartevents", "valuenum", "valueuom"),
        ("mimiciv_icu.inputevents", "amount", "amountuom"),
        ("mimiciv_icu.inputevents", "rate", "rateuom"),
        ("mimiciv_icu.ingredientevents", "amount", "amountuom"),
        ("mimiciv_icu.outputevents", "value", "valueuom"),
        ("mimiciv_icu.procedureevents", "value", "valueuom"),
    ):
        assert want in pairs
    fixed = {(f.table, f.column): f for f in contract.units.fixed_units}
    for col, unit in (
        ("temperature", "degF"),
        ("heartrate", "/min"),
        ("resprate", "/min"),
        ("o2sat", "%"),
        ("sbp", "mmHg"),
        ("dbp", "mmHg"),
    ):
        f = fixed[("mimiciv_ed.vitalsign", col)]
        assert f.unit == unit and f.plausible_min is not None and f.plausible_max is not None
        assert f.plausible_min < f.plausible_max
    assert any(
        i.table == "mimiciv_hosp.omr" and i.implied_by == "result_name"
        for i in contract.units.implied_units
    )
    # unit_of is stamped from the pairs, and only there
    for p in contract.units.value_unit_pairs:
        assert by_name[p.table].column(p.unit).unit_of == p.value
    stamped = {(t.qualified_name, c.name) for t in contract.tables for c in t.columns if c.unit_of}
    assert stamped == {(p.table, p.unit) for p in contract.units.value_unit_pairs}
    cm = contract.column_map("demo_2_2")
    for qn in cm.identity_tables:
        assert qn in by_name
    assert cm.covers("mimiciv_hosp.provider") and cm.covers("mimiciv_icu.caregiver")
    assert not cm.covers("mimiciv_note.discharge")
    assert cm.schemas_absent_in_source == ("mimiciv_note",)
    assert cm.tables == {} and cm.tables_absent_in_2_2 == ()
    assert len(cm.identity_tables) == 37
    with pytest.raises(KeyError):
        contract.column_map("nope")


# ---------------------------------------------------------------------------
# 2. Renderers + DuckDB
# ---------------------------------------------------------------------------


def test_all_ddl_execute_in_duckdb(contract: Contract) -> None:
    import duckdb

    from mimicwarehouse.config import get_settings

    config: dict[str, Any] = dict(get_settings().duckdb_settings("app"))
    con = duckdb.connect(":memory:", config=config)
    try:
        con.execute(contract.duckdb_schema_ddl())
        for t in contract.tables:
            ddl = t.duckdb_ddl()
            assert ddl.startswith(f"CREATE TABLE {t.qualified_name} (\n")
            assert ddl.endswith("\n);")
            assert "PRIMARY KEY" not in ddl and "FOREIGN KEY" not in ddl
            con.execute(ddl)
            desc = con.execute(f"DESCRIBE {t.qualified_name}").fetchall()
            assert [row[0] for row in desc] == list(t.column_names), t.qualified_name
            for row, c in zip(desc, t.columns, strict=True):
                assert row[1] == c.duckdb_type, (
                    f"{t.qualified_name}.{c.name}: {row[1]} vs {c.duckdb_type}"
                )
                assert (row[2] == "NO") == (not c.nullable), (
                    f"{t.qualified_name}.{c.name} nullability"
                )
            con.execute(t.duckdb_ddl(if_not_exists=True))  # idempotent form
        n = con.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema LIKE 'mimiciv_%'"
        ).fetchone()
        assert n is not None and n[0] == 41
    finally:
        con.close()


def test_read_csv_columns_and_hash(contract: Contract) -> None:
    ce = contract.table("mimiciv_icu.chartevents")
    cols = ce.read_csv_columns()
    assert list(cols) == list(ce.column_names)
    assert cols["valuenum"] == "DOUBLE" and cols["charttime"] == "TIMESTAMP"
    h = contract.content_hash()
    assert re.fullmatch(r"[0-9a-f]{64}", h)
    load_contract.cache_clear()
    assert load_contract().content_hash() == h
    assert Column(name="x", type="INTEGER").ddl == "x INTEGER"
    assert Column(name="x", type="VARCHAR", nullable=False).ddl == "x VARCHAR NOT NULL"


def test_model_validators_reject_bad_tables() -> None:
    cols = [{"name": "subject_id", "type": "INTEGER"}, {"name": "charttime", "type": "TIMESTAMP"}]
    base: dict[str, Any] = {
        "schema": "mimiciv_hosp",
        "name": "t",
        "dataset": "mimic-iv-3.1",
        "csv_path": "hosp/t.csv",
        "columns": cols,
        "sort_keys": ["subject_id"],
    }

    def table(**over: Any) -> Table:
        return Table.model_validate({**base, **over})

    t = table(time_column="charttime")
    assert t.subject_keyed and t.partitioned and t.is_dim is False
    with pytest.raises(ValueError, match="sort_keys must start"):
        table(sort_keys=["charttime"])
    with pytest.raises(ValueError, match="partitioned must equal"):
        table(partitioned=False)
    with pytest.raises(ValueError, match="time_column"):
        table(time_column="nope")
    with pytest.raises(ValueError, match="TIMESTAMP/DATE"):
        table(time_column="subject_id")
    with pytest.raises(ValueError, match="unknown column"):
        table(primary_key=["zzz"])
    with pytest.raises(ValueError, match="uniqueness_hint is only"):
        table(primary_key=["subject_id"], uniqueness_hint=["subject_id"])
    with pytest.raises(ValueError, match="duplicate columns"):
        table(columns=[*cols, cols[0]])
    with pytest.raises(ValueError, match="not an allowed DuckDB type"):
        Column(name="x", type="TEXT")
    with pytest.raises(ValueError, match="drop the override"):
        Column(name="x", type="INTEGER", nullable=True, upstream_nullable=True)
    with pytest.raises(ValueError, match="csv_path"):
        table(csv_path="hosp\\t.csv")
    with pytest.raises(ValueError):
        table(extra_field=1)


def test_column_map_apply_missing_check(contract: Contract) -> None:
    cm = contract.column_map("demo_2_2")
    t = contract.table("mimiciv_hosp.admissions")
    header = list(t.column_names)
    assert cm.apply(t, header) == {c: c for c in header}
    assert cm.missing(t, header) == () and cm.check(t, header) == []
    short = [c for c in header if c != "admit_provider_id"]
    assert cm.missing(t, short) == ("admit_provider_id",)
    assert cm.check(t, short) == ["missing column 'admit_provider_id' (not declared added_in_3_1)"]
    assert cm.apply(t, [*header, "extra"])["extra"] is None
    assert cm.check(t, [*header, "extra"]) == ["unexpected column 'extra'"]
    with pytest.raises(SchemaError, match="not covered"):
        cm.table_map("mimiciv_note.discharge")
    # A synthetic non-identity map exercises renamed / added / dropped.
    synthetic = ColumnMap.model_validate(
        {
            "name": "synthetic",
            "description": "test",
            "derivation": "test",
            "schemas": ["mimiciv_hosp"],
            "identity_tables": [],
            "tables": {
                "mimiciv_hosp.admissions": {
                    "added_in_3_1": ["admit_provider_id"],
                    "renamed": {"ethnicity": "race"},
                    "dropped_in_3_1": ["old_col"],
                }
            },
            "tables_absent_in_2_2": [
                x.qualified_name
                for x in contract.by_schema("mimiciv_hosp")
                if x.name != "admissions"
            ],
        }
    )
    assert isinstance(synthetic.tables["mimiciv_hosp.admissions"], TableMap)
    assert not synthetic.tables["mimiciv_hosp.admissions"].is_identity
    old_header = [c for c in header if c not in ("admit_provider_id", "race")] + [
        "ethnicity",
        "old_col",
    ]
    mapping = synthetic.apply(t, old_header)
    assert mapping["ethnicity"] == "race" and mapping["old_col"] is None
    assert synthetic.missing(t, old_header) == ("admit_provider_id",)
    assert synthetic.check(t, old_header) == []
    with pytest.raises(SchemaError, match="absent"):
        synthetic.table_map("mimiciv_hosp.patients")


# ---------------------------------------------------------------------------
# 3. Transcriber + drift oracle
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("pg", "duck"),
    [
        ("INTEGER", "INTEGER"),
        ("int", "INTEGER"),
        ("SMALLINT", "SMALLINT"),
        ("BIGINT", "BIGINT"),
        ("VARCHAR(40)", "VARCHAR"),
        ("VARCHAR", "VARCHAR"),
        ("TEXT", "VARCHAR"),
        ("CHAR(7)", "VARCHAR"),
        ("CHARACTER VARYING(10)", "VARCHAR"),
        ("TIMESTAMP(0)", "TIMESTAMP"),
        ("TIMESTAMP(3)", "TIMESTAMP"),
        ("TIMESTAMP", "TIMESTAMP"),
        ("timestamp without time zone", "TIMESTAMP"),
        ("DATE", "DATE"),
        ("DOUBLE PRECISION", "DOUBLE"),
        ("FLOAT", "DOUBLE"),
        ("FLOAT8", "DOUBLE"),
        ("REAL", "FLOAT"),
        ("FLOAT4", "FLOAT"),
        ("NUMERIC(10, 4)", "DECIMAL(10,4)"),
        ("NUMERIC(10,4)", "DECIMAL(10,4)"),
        ("DECIMAL(5)", "DECIMAL(5,0)"),
        ("NUMERIC", "DOUBLE"),
        ("BOOLEAN", "BOOLEAN"),
    ],
)
def test_pg_to_duckdb(pg: str, duck: str) -> None:
    assert tr.pg_to_duckdb(pg) == duck


def test_pg_to_duckdb_refuses_unknown() -> None:
    with pytest.raises(tr.UnknownTypeError):
        tr.pg_to_duckdb("JSONB")
    with pytest.raises(tr.UnknownTypeError):
        tr.pg_to_duckdb("NUMERIC(a,b)")
    assert tr.normalise_pg_type("numeric( 10 ,4 )") == "NUMERIC(10, 4)"


SYNTHETIC_DDL = """
-- synthetic DDL for the parser test (no data)
DROP SCHEMA IF EXISTS mimiciv_hosp CASCADE;
CREATE SCHEMA mimiciv_hosp;
/* block
   comment */
DROP TABLE IF EXISTS mimiciv_hosp.alpha;
CREATE TABLE mimiciv_hosp.alpha
(
  subject_id INTEGER NOT NULL,   -- trailing comment
  charttime TIMESTAMP(0),
  resprate NUMERIC(10, 4),
  value DOUBLE PRECISION,
  label VARCHAR(20) NOT NULL
);
CREATE TABLE mimiciv_hosp.beta(itemid INTEGER NOT NULL, note TEXT) ;
CREATE TABLE mimiciv_icu.gamma
(
  stay_id INTEGER NOT NULL,
  los REAL,
  CONSTRAINT gamma_pk PRIMARY KEY (stay_id)
);
"""


def test_parse_create_sql_synthetic() -> None:
    tables = tr.parse_create_sql(SYNTHETIC_DDL)
    assert [t.qualified_name for t in tables] == [
        "mimiciv_hosp.alpha",
        "mimiciv_hosp.beta",
        "mimiciv_icu.gamma",
    ]
    alpha = tables[0]
    assert [(c.name, c.pg_type, c.not_null) for c in alpha.columns] == [
        ("subject_id", "INTEGER", True),
        ("charttime", "TIMESTAMP(0)", False),
        ("resprate", "NUMERIC(10, 4)", False),
        ("value", "DOUBLE PRECISION", False),
        ("label", "VARCHAR(20)", True),
    ]
    assert [c.duckdb_type for c in alpha.columns] == [
        "INTEGER",
        "TIMESTAMP",
        "DECIMAL(10,4)",
        "DOUBLE",
        "VARCHAR",
    ]
    assert [(c.name, c.duckdb_type) for c in tables[1].columns] == [
        ("itemid", "INTEGER"),
        ("note", "VARCHAR"),
    ]
    assert [(c.name, c.duckdb_type) for c in tables[2].columns] == [
        ("stay_id", "INTEGER"),
        ("los", "FLOAT"),
    ]
    with pytest.raises(SchemaError, match="unterminated"):
        tr.parse_create_sql("CREATE TABLE a.b (\n x INTEGER\n")
    keys = tr.parse_constraint_sql(
        "ALTER TABLE mimiciv_hosp.alpha ADD CONSTRAINT alpha_pk "
        "PRIMARY KEY (subject_id, charttime);\n"
        "ALTER TABLE mimiciv_hosp.beta ADD CONSTRAINT beta_alpha_fk FOREIGN KEY (itemid) "
        "REFERENCES mimiciv_hosp.alpha (subject_id);"
    )
    assert keys.primary_keys == {"mimiciv_hosp.alpha": ("subject_id", "charttime")}
    assert keys.foreign_keys == (
        ("mimiciv_hosp.beta", ("itemid",), "mimiciv_hosp.alpha", ("subject_id",), "beta_alpha_fk"),
    )


def test_parse_vendored_ddl_matches_counts() -> None:
    hosp_icu = tr.parse_create_sql(
        vendored_path("mimic-iv/buildmimic/postgres/create.sql").read_text("utf-8")
    )
    assert sum(t.schema == "mimiciv_hosp" for t in hosp_icu) == 22
    assert sum(t.schema == "mimiciv_icu" for t in hosp_icu) == 9
    ed = tr.parse_create_sql(
        vendored_path("mimic-iv-ed/buildmimic/postgres/create.sql").read_text("utf-8")
    )
    assert [t.name for t in ed] == ED
    note = tr.parse_create_sql(
        vendored_path("mimic-iv-note/buildmimic/postgres/create.sql").read_text("utf-8")
    )
    assert [t.name for t in note] == NOTE
    vs = next(t for t in ed if t.name == "vitalsign")
    assert vs.columns[5].name == "resprate" and vs.columns[5].pg_type == "NUMERIC(10, 4)"


def test_draft_schema_yaml_is_single_document_and_loads(tmp_path: Path) -> None:
    tables = tr.parse_create_sql(SYNTHETIC_DDL)
    text = tr.draft_schema_yaml(tables, "mimiciv_hosp", source_ddl="synthetic")
    docs = list(yaml.safe_load_all(text))
    assert len(docs) == 1 and docs[0]["schema"] == "mimiciv_hosp"
    assert [t["name"] for t in docs[0]["tables"]] == ["alpha", "beta"]
    alpha = docs[0]["tables"][0]
    assert alpha["time_column"] == "charttime" and alpha["sort_keys"] == ["subject_id", "charttime"]
    assert alpha["columns"][2] == {
        "name": "resprate",
        "type": "DECIMAL(10,4)",
        "nullable": True,
        "comment": "TODO",
    }
    assert "!!" not in text and text.isascii()
    with pytest.raises(SchemaError, match="no CREATE TABLE"):
        tr.draft_schema_yaml(tables, "mimiciv_note")
    # CLI form
    ddl = tmp_path / "create.sql"
    ddl.write_text(SYNTHETIC_DDL, encoding="utf-8")
    out = tmp_path / "draft" / "mimiciv_icu.yaml"
    res = CliRunner().invoke(
        app,
        [
            "schema",
            "transcribe",
            "--create-sql",
            str(ddl),
            "--schema",
            "mimiciv_icu",
            "--out",
            str(out),
        ],
    )
    assert res.exit_code == 0, res.output
    assert yaml.safe_load(out.read_text("utf-8"))["tables"][0]["name"] == "gamma"
    res = CliRunner().invoke(
        app,
        [
            "schema",
            "transcribe",
            "--create-sql",
            str(tmp_path / "nope.sql"),
            "--schema",
            "x",
            "--out",
            str(out),
        ],
    )
    assert res.exit_code == 2


def test_drift_check_is_clean_against_vendored_ddl(contract: Contract) -> None:
    assert tr.check_contract(contract) == []


def _edited_copy(tmp_path: Path, rel: str, pattern: str, replacement: str) -> Path:
    root = tmp_path / "tables"
    shutil.copytree(tables_root(), root)
    path = root / rel
    text = path.read_text(encoding="utf-8")
    assert pattern in text, pattern
    path.write_text(text.replace(pattern, replacement, 1), encoding="utf-8", newline="\n")
    return root


def test_drift_check_bites_on_edited_yaml(tmp_path: Path) -> None:
    # 1. one column type edited -> exactly one 'type' finding
    root = _edited_copy(
        tmp_path / "a",
        "mimiciv_hosp.yaml",
        "name: anchor_age, type: SMALLINT",
        "name: anchor_age, type: INTEGER",
    )
    drifts = tr.check_contract(load_contract_from(root))
    assert [(d.kind, d.table, d.column, d.expected, d.actual) for d in drifts] == [
        ("type", "patients", "anchor_age", "SMALLINT", "INTEGER")
    ]
    # 2. nullability edited
    root = _edited_copy(
        tmp_path / "b",
        "mimiciv_hosp.yaml",
        '{name: hadm_id, type: INTEGER, nullable: false, comment: "Hospital admission',
        '{name: hadm_id, type: INTEGER, comment: "Hospital admission',
    )
    drifts = tr.check_contract(load_contract_from(root))
    assert [(d.kind, d.table, d.column) for d in drifts] == [
        ("nullability", "admissions", "hadm_id")
    ]
    # 3. a recorded upstream_type that no longer matches the DDL
    root = _edited_copy(
        tmp_path / "c",
        "mimiciv_ed.yaml",
        'upstream_type: "NUMERIC(10, 4)"',
        'upstream_type: "NUMERIC(12, 4)"',
    )
    drifts = tr.check_contract(load_contract_from(root))
    assert [(d.kind, d.table, d.column, d.expected) for d in drifts] == [
        ("type", "vitalsign", "resprate", "NUMERIC(10, 4)")
    ]
    # 4. a primary key edited in keys.yaml
    root = _edited_copy(
        tmp_path / "d",
        "keys.yaml",
        "mimiciv_hosp.d_labitems: [itemid]",
        "mimiciv_hosp.d_labitems: [itemid, label]",
    )
    drifts = tr.check_contract(load_contract_from(root))
    assert [(d.kind, d.table) for d in drifts] == [("primary_key", "d_labitems")]
    # 5. a column removed -> missing_column (order of the rest unchanged, so no 'order' finding)
    root = _edited_copy(
        tmp_path / "e",
        "mimiciv_icu.yaml",
        '      - {name: warning, type: SMALLINT, comment: "1 when MetaVision raised a warning '
        'for the value."}\n',
        "",
    )
    drifts = tr.check_contract(load_contract_from(root))
    assert [(d.kind, d.table, d.column) for d in drifts] == [
        ("missing_column", "chartevents", "warning")
    ]
    # 6. loader refuses an unknown key on a column (extra="forbid")
    root = _edited_copy(
        tmp_path / "f",
        "mimiciv_hosp.yaml",
        "name: anchor_age, type: SMALLINT",
        "name: anchor_age, type: SMALLINT, typo: 1",
    )
    with pytest.raises(SchemaError, match="typo"):
        load_contract_from(root)


def test_check_contract_with_injected_ddl(contract: Contract, tmp_path: Path) -> None:
    """A trimmed create.sql handed in through ``vendored=`` yields missing_table findings."""
    text = vendored_path("mimic-iv-note/buildmimic/postgres/create.sql").read_text("utf-8")
    trimmed = text.replace(
        "CREATE TABLE mimiciv_note.radiology_detail",
        "CREATE TABLE mimiciv_note.radiology_detail_v3",
    )
    path = tmp_path / "create.sql"
    path.write_text(trimmed, encoding="utf-8")
    drifts = tr.check_contract(
        contract, vendored={"mimic-iv-note/buildmimic/postgres/create.sql": path}
    )
    assert {(d.kind, d.table) for d in drifts} == {
        ("missing_table", "radiology_detail_v3"),
        ("extra_table", "radiology_detail"),
    }


# ---------------------------------------------------------------------------
# 4. CLI + packaging + hygiene
# ---------------------------------------------------------------------------


def test_schema_is_a_diagnostic_command() -> None:
    assert "schema" in DIAGNOSTIC_COMMANDS
    # A nonsense data root must not stop `mwh schema check` (it never touches the root).
    res = CliRunner().invoke(app, ["--data-root", "Z:\\definitely\\not\\here", "schema", "check"])
    assert res.exit_code == 0, res.output
    assert "no drift" in res.output


def test_cli_list_show_ddl_check() -> None:
    runner = CliRunner()
    res = runner.invoke(app, ["schema", "list"])
    assert res.exit_code == 0, res.output
    assert "41 table(s)" in res.output and "mimiciv_hosp=22" in res.output
    res = runner.invoke(app, ["schema", "list", "--schema", "mimiciv_ed", "--json"])
    assert res.exit_code == 0
    assert '"mimiciv_ed.triage"' in res.output
    res = runner.invoke(app, ["schema", "list", "--schema", "nope"])
    assert res.exit_code == 2
    res = runner.invoke(app, ["schema", "show", "mimiciv_hosp.patients"])
    assert res.exit_code == 0, res.output
    assert "anchor_year_group" in res.output and "shown as 91" in res.output
    res = runner.invoke(app, ["schema", "show", "mimiciv_hosp.nope"])
    assert res.exit_code == 2
    res = runner.invoke(app, ["schema", "show", "mimiciv_ed.vitalsign", "--json"])
    assert res.exit_code == 0 and '"upstream_type": "NUMERIC(10, 4)"' in res.output
    res = runner.invoke(app, ["schema", "ddl", "mimiciv_icu.chartevents"])
    assert res.exit_code == 0, res.output
    lines = [ln.strip().rstrip(",") for ln in res.output.strip().splitlines()]
    assert lines[0] == "CREATE TABLE mimiciv_icu.chartevents ("
    assert lines[-1] == ");"
    assert [ln.split()[0] for ln in lines[1:-1]] == [
        "subject_id",
        "hadm_id",
        "stay_id",
        "caregiver_id",
        "charttime",
        "storetime",
        "itemid",
        "value",
        "valuenum",
        "valueuom",
        "warning",
    ]
    res = runner.invoke(app, ["schema", "ddl", "--all"])
    assert res.exit_code == 0 and res.output.count("CREATE TABLE ") == 41
    assert res.output.startswith("CREATE SCHEMA IF NOT EXISTS mimiciv_hosp;")
    res = runner.invoke(app, ["schema", "ddl"])
    assert res.exit_code == 2
    res = runner.invoke(app, ["schema", "check", "--json"])
    assert res.exit_code == 0, res.output
    assert '"drift": []' in res.output and '"tables": 41' in res.output
    res = runner.invoke(app, ["schema"])
    assert "list" in res.output and "check" in res.output


def test_cli_check_fails_on_drift_in_a_fresh_interpreter(tmp_path: Path) -> None:
    """The acceptance recipe end to end: point the loader at an edited copy and run the CLI."""
    import subprocess

    root = _edited_copy(
        tmp_path, "mimiciv_hosp.yaml", "name: valuenum, type: DOUBLE", "name: valuenum, type: FLOAT"
    )
    code = (
        "import sys\n"
        "from pathlib import Path\n"
        "from mimicwarehouse.schema import contract as c\n"
        f"c.tables_root = lambda: Path({str(root)!r})\n"
        "c.load_contract.cache_clear()\n"
        "from mimicwarehouse.cli import app\n"
        "sys.argv = ['mwh', 'schema', 'check']\n"
        "app()\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, cwd=WORKSPACE
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert (
        "DRIFT" in proc.stdout
        and "labevents.valuenum" in proc.stdout
        and "1 drift finding" in proc.stdout
    )


def test_yaml_files_are_single_document_tag_free_ascii_and_guard_clean() -> None:
    assert {p.name for p in YAML_FILES} == {
        "mimiciv_hosp.yaml",
        "mimiciv_icu.yaml",
        "mimiciv_ed.yaml",
        "mimiciv_note.yaml",
        "keys.yaml",
        "units.yaml",
        "demo_2_2_to_3_1.yaml",
    }
    for path in YAML_FILES:
        raw = path.read_bytes()
        assert b"\r" not in raw, f"{path.name}: CRLF"
        assert raw.endswith(b"\n") and not raw.endswith(b"\n\n"), f"{path.name}: end-of-file-fixer"
        text = raw.decode("utf-8")
        assert text.isascii(), f"{path.name}: non-ASCII (cp1252 console safety, Risk 13)"
        assert not any(ln != ln.rstrip() for ln in text.splitlines()), (
            f"{path.name}: trailing whitespace"
        )
        assert "!!" not in text and not re.search(r"^---\s*$", text, re.M), (
            f"{path.name}: tags / multi-doc"
        )
        docs = list(yaml.safe_load_all(text))
        assert len(docs) == 1 and isinstance(docs[0], dict), path.name
        assert not guard.id_band_hits(raw), f"{path.name}: real-band id token"
    violations = guard.scan([SCHEMA_DIR], WORKSPACE.parent)
    assert violations == [], [str(v) for v in violations]


def test_package_data_resolves_and_is_hatch_shipped() -> None:
    root = tables_root()
    assert root.is_dir() and (root / "keys.yaml").is_file() and (root / "column_maps").is_dir()
    assert root == SCHEMA_DIR / "tables"
    pyproject = (WORKSPACE / "pyproject.toml").read_text(encoding="utf-8")
    assert 'packages = ["src/mimicwarehouse"]' in pyproject  # hatchling ships non-.py files (EP-8)


def test_cli_import_budget() -> None:
    """``mwh --help`` must not pay for yaml / duckdb / the contract at import time."""
    import subprocess

    code = (
        "import sys, mimicwarehouse.cli as m\n"
        "heavy = [k for k in ('duckdb', 'pandas', 'polars', 'pyarrow') if k in sys.modules]\n"
        "print('heavy=' + ','.join(sorted(heavy)))\n"
        "lazy = 'mimicwarehouse.schema.contract' not in sys.modules\n"
        "print('contract=' + ('lazy' if lazy else 'loaded'))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, cwd=WORKSPACE
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.split() == ["heavy=", "contract=lazy"], proc.stdout
