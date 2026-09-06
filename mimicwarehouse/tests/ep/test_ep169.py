"""EP-169 — Retro E: contract sort-key tie-breaks, ``Contract.structural_hash()``, the one CSV
dialect (``schema/csv_dialect.py``), disjoint fixture id floors, manifest provenance keys and
the concept-coverage note — regenerated fixture 0.2.0.

Fixture tier only: everything here runs on the packaged YAML, the committed synthetic fixture
(ids >= 90 000 000, D-27) and in-memory frames / DuckDB connections opened with
``get_settings().duckdb_settings("app")`` (house rule, DESIGN §6). No data root is read; no test
prints a row.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

import polars as pl
import pytest
from typer.testing import CliRunner

from mimicwarehouse import guard
from mimicwarehouse.cli import app
from mimicwarehouse.fixtures import build_frames, validate
from mimicwarehouse.fixtures import catalog as catalog_mod
from mimicwarehouse.fixtures import write as write_mod
from mimicwarehouse.fixtures.spec import FIXTURE_ID_FLOOR, FixtureSpec
from mimicwarehouse.inventory import CSV_READ_OPTIONS
from mimicwarehouse.schema import Contract, load_contract_from, tables_root
from mimicwarehouse.schema import csv_dialect as dialect

pytestmark = pytest.mark.ep_169

runner = CliRunner()

WORKSPACE = Path(__file__).resolve().parents[2]
FIXTURE_DIR = WORKSPACE / "tests" / "fixtures"

#: The tie-break sort keys adopted in one edit (ledger ARCH-4/FC-3; D-17 addendum).
EXPECTED_SORT_KEYS: dict[str, tuple[str, ...]] = {
    # hosp
    "mimiciv_hosp.transfers": ("subject_id", "intime", "transfer_id"),
    "mimiciv_hosp.hcpcsevents": ("subject_id", "chartdate", "hadm_id", "seq_num"),
    "mimiciv_hosp.procedures_icd": ("subject_id", "chartdate", "hadm_id", "seq_num"),
    "mimiciv_hosp.pharmacy": ("subject_id", "starttime", "pharmacy_id"),
    "mimiciv_hosp.prescriptions": ("subject_id", "starttime", "pharmacy_id"),
    "mimiciv_hosp.poe": ("subject_id", "ordertime", "poe_seq"),
    "mimiciv_hosp.emar": ("subject_id", "charttime", "emar_seq"),
    "mimiciv_hosp.emar_detail": ("subject_id", "emar_id", "emar_seq", "parent_field_ordinal"),
    "mimiciv_hosp.labevents": ("subject_id", "charttime", "itemid"),
    "mimiciv_hosp.microbiologyevents": ("subject_id", "chartdate", "charttime", "microevent_id"),
    # icu
    "mimiciv_icu.chartevents": ("subject_id", "charttime", "itemid"),
    "mimiciv_icu.datetimeevents": ("subject_id", "charttime", "itemid"),
    "mimiciv_icu.outputevents": ("subject_id", "charttime", "itemid"),
    "mimiciv_icu.inputevents": ("subject_id", "starttime", "orderid"),
    "mimiciv_icu.ingredientevents": ("subject_id", "starttime", "orderid"),
    "mimiciv_icu.procedureevents": ("subject_id", "starttime", "orderid"),
    # ed / note (uniform rule: +stay_id / +note_id)
    "mimiciv_ed.edstays": ("subject_id", "intime", "stay_id"),
    "mimiciv_note.discharge": ("subject_id", "charttime", "note_id"),
    "mimiciv_note.radiology": ("subject_id", "charttime", "note_id"),
}
#: The nine upstream TIMESTAMP(3) columns recorded as upstream_type (ledger SCH-1).
TIMESTAMP3_COLUMNS: dict[str, tuple[str, ...]] = {
    "mimiciv_hosp.pharmacy": (
        "starttime",
        "stoptime",
        "entertime",
        "verifiedtime",
        "expirationdate",
    ),
    "mimiciv_hosp.prescriptions": ("starttime", "stoptime"),
    "mimiciv_icu.outputevents": ("charttime", "storetime"),
}
DEFAULT_FLOORS = {
    "first_subject_id": 90_000_000,
    "first_hadm_id": 91_000_000,
    "first_stay_id": 92_000_000,
    "first_event_id": 93_000_000,
    "first_caregiver_id": 93_900_000,
}


@pytest.fixture(scope="module")
def spec() -> FixtureSpec:
    return FixtureSpec()


@pytest.fixture(scope="module")
def all_frames(spec: FixtureSpec, contract: Contract) -> dict[str, dict[str, pl.DataFrame]]:
    _plan, frames = build_frames(spec, contract=contract)
    return frames


@pytest.fixture(scope="module")
def manifest() -> dict[str, Any]:
    return json.loads((FIXTURE_DIR / "manifest.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def con():
    import duckdb

    from mimicwarehouse.config import get_settings

    connection = duckdb.connect(":memory:", config=dict(get_settings().duckdb_settings("app")))
    try:
        yield connection
    finally:
        connection.close()


def _edited_copy(tmp_path: Path, rel: str, pattern: str, replacement: str) -> Path:
    root = tmp_path / "tables"
    shutil.copytree(tables_root(), root)
    path = root / rel
    text = path.read_text(encoding="utf-8")
    assert pattern in text, pattern
    path.write_text(text.replace(pattern, replacement, 1), encoding="utf-8", newline="\n")
    return root


# ---------------------------------------------------------------------------
# 1. Sort-key tie-breaks (item 1)
# ---------------------------------------------------------------------------


def test_sort_key_tie_breaks(contract: Contract) -> None:
    for qn, keys in EXPECTED_SORT_KEYS.items():
        assert contract.table(qn).sort_keys == keys, qn
    # owner decision: microbiologyevents keeps its shipped load class
    assert contract.table("mimiciv_hosp.microbiologyevents").load_class == "large"
    # uniform rule across the four schemas: every subject-keyed table with a time_column and
    # a same-table id/sequence tie-break candidate ends in a non-time column (deterministic
    # enough for the EP-18 per-bucket sort); detail tables sort [subject_id, parent, ordinal]
    for qn in ("mimiciv_note.discharge_detail", "mimiciv_note.radiology_detail"):
        assert contract.table(qn).sort_keys == ("subject_id", "note_id", "field_ordinal")
    assert contract.table("mimiciv_ed.diagnosis").sort_keys == ("subject_id", "stay_id", "seq_num")


def test_timestamp3_columns_recorded(contract: Contract) -> None:
    recorded = {
        (t.qualified_name, c.name)
        for t in contract.tables
        for c in t.columns
        if c.upstream_type is not None and "TIMESTAMP" in c.upstream_type
    }
    expected = {(qn, col) for qn, cols in TIMESTAMP3_COLUMNS.items() for col in cols}
    assert recorded == expected
    for qn, cols in TIMESTAMP3_COLUMNS.items():
        for col in cols:
            c = contract.table(qn).column(col)
            assert c.upstream_type == "TIMESTAMP(3)" and c.duckdb_type == "TIMESTAMP", (qn, col)


# ---------------------------------------------------------------------------
# 2. structural_hash (item 2)
# ---------------------------------------------------------------------------


def test_structural_hash_shape_and_stability(contract: Contract) -> None:
    h = contract.structural_hash()
    assert re.fullmatch(r"[0-9a-f]{64}", h)
    assert h == contract.structural_hash()
    assert h != contract.content_hash()


def test_comment_edit_moves_content_hash_only(contract: Contract, tmp_path: Path) -> None:
    root = _edited_copy(
        tmp_path,
        "mimiciv_hosp.yaml",
        'comment: "Patient identifier (-> patients)."',
        'comment: "Patient identifier (-> patients; wording edited)."',
    )
    edited = load_contract_from(root)
    assert edited.content_hash() != contract.content_hash()
    assert edited.structural_hash() == contract.structural_hash()


def test_load_relevant_edit_moves_both_hashes(contract: Contract, tmp_path: Path) -> None:
    root = _edited_copy(
        tmp_path,
        "mimiciv_hosp.yaml",
        "sort_keys: [subject_id, admittime]",
        "sort_keys: [subject_id, admittime, hadm_id]",
    )
    edited = load_contract_from(root)
    assert edited.content_hash() != contract.content_hash()
    assert edited.structural_hash() != contract.structural_hash()


# ---------------------------------------------------------------------------
# 3. CSV dialect (item 3)
# ---------------------------------------------------------------------------


def test_dialect_constants_and_consumers(contract: Contract) -> None:
    assert (dialect.DELIM, dialect.QUOTE, dialect.ESCAPE) == (",", '"', '"')
    assert dialect.HEADER is True and dialect.NULLSTR == ""
    assert dialect.ALLOW_QUOTED_NULLS is True  # owner policy, D-17
    assert dialect.DATEFORMAT is None and dialect.TIMESTAMPFORMAT is None  # ISO cast
    assert "allow_quoted_nulls=true" in dialect.READ_OPTIONS_SQL
    assert "timestampformat" not in dialect.READ_CSV_SQL
    assert "dateformat" not in dialect.READ_CSV_SQL
    # consumers share the one dialect
    assert catalog_mod.READ_CSV_SQL is dialect.READ_CSV_SQL
    assert CSV_READ_OPTIONS.startswith(dialect.READ_OPTIONS_SQL)
    assert "all_varchar=true" in CSV_READ_OPTIONS
    assert write_mod.TIMESTAMP_FORMAT == dialect.WRITE_TIMESTAMP_FORMAT == "%Y-%m-%d %H:%M:%S"
    assert write_mod.DATE_FORMAT == dialect.WRITE_DATE_FORMAT == "%Y-%m-%d"
    # Table.read_csv_options: dialect keywords + the contract columns
    t = contract.table("mimiciv_hosp.patients")
    options = t.read_csv_options()
    assert options["columns"] == t.read_csv_columns()
    assert options["header"] is True and options["delim"] == ","
    assert options["allow_quoted_nulls"] is True
    assert "timestampformat" not in options and "dateformat" not in options


def test_read_csv_sql_reads_committed_fixture(
    con, contract: Contract, manifest: dict[str, Any]
) -> None:
    for name in ("patients", "microbiologyevents"):
        t = contract.table("mimiciv_hosp", name)
        path = FIXTURE_DIR / "mimic-iv-3.1" / "hosp" / f"{name}.csv"
        sql = dialect.read_csv_sql(path, t)
        rel = con.execute(f"SELECT * FROM ({sql})")
        assert [d[0] for d in rel.description] == list(t.column_names)
        n = con.execute(f"SELECT count(*) FROM ({sql})").fetchone()[0]
        assert n == manifest["files"][f"mimic-iv-3.1/hosp/{name}.csv"]["rows"], name


def test_iso_cast_accepts_fractional_seconds_where_strptime_fails(con, tmp_path: Path) -> None:
    """The D-17 rationale for TIMESTAMPFORMAT=None, verified: DuckDB's ISO cast parses the
    optional fractional seconds of the upstream TIMESTAMP(3) columns; a '%Y-%m-%d %H:%M:%S'
    strptime does not."""
    row = con.execute(
        "SELECT try_strptime('2150-01-02 03:04:05.123', '%Y-%m-%d %H:%M:%S') IS NULL, "
        "try_cast('2150-01-02 03:04:05.123' AS TIMESTAMP) IS NOT NULL"
    ).fetchone()
    assert row == (True, True)
    csv_path = tmp_path / "t3.csv"
    csv_path.write_text("t\n2150-01-02 03:04:05.123\n2150-01-02 03:04:05\n", encoding="utf-8")
    n = con.execute(
        f"SELECT count(t) FROM ({dialect.READ_CSV_SQL})", [str(csv_path), {"t": "TIMESTAMP"}]
    ).fetchone()[0]
    assert n == 2


# ---------------------------------------------------------------------------
# 4. Disjoint id floors (item 4)
# ---------------------------------------------------------------------------


def test_spec_floor_defaults_are_disjoint_and_guard_safe(spec: FixtureSpec) -> None:
    floors = {name: getattr(spec, name) for name in DEFAULT_FLOORS}
    assert floors == DEFAULT_FLOORS
    assert len(set(floors.values())) == len(floors)
    for value in floors.values():
        assert value >= FIXTURE_ID_FLOOR
        assert not guard.id_band_hits(str(value).encode())  # never an 8-digit 1/2/3 token


def test_fixture_id_spaces_are_pairwise_disjoint(
    all_frames: dict[str, dict[str, pl.DataFrame]], spec: FixtureSpec
) -> None:
    hosp, icu = all_frames["hosp"], all_frames["icu"]
    subjects = set(hosp["patients"].get_column("subject_id").to_list())
    hadms = set(hosp["admissions"].get_column("hadm_id").to_list())
    stays = set(icu["icustays"].get_column("stay_id").to_list())
    caregivers = set(icu["caregiver"].get_column("caregiver_id").to_list())
    spaces = [subjects, hadms, stays, caregivers]
    for i, a in enumerate(spaces):
        for b in spaces[i + 1 :]:
            assert not (a & b)
    assert min(subjects) == spec.first_subject_id and max(subjects) < spec.first_hadm_id
    assert min(hadms) == spec.first_hadm_id and max(hadms) < spec.first_stay_id
    assert min(stays) == spec.first_stay_id and max(stays) < spec.first_event_id
    assert min(caregivers) == spec.first_caregiver_id
    # event ids start at their own floor and never collide with caregiver ids
    transfer_ids = set(hosp["transfers"].get_column("transfer_id").to_list())
    assert min(transfer_ids) >= spec.first_event_id
    assert not (transfer_ids & caregivers)


def test_validate_flags_overlapping_id_spaces(
    all_frames: dict[str, dict[str, pl.DataFrame]], contract: Contract
) -> None:
    hosp, icu = all_frames["hosp"], all_frames["icu"]
    subjects = hosp["patients"].get_column("subject_id")
    broken = dict(icu)
    n = icu["caregiver"].height
    broken["caregiver"] = icu["caregiver"].with_columns(
        subjects.head(n).cast(pl.Int32).alias("caregiver_id")
    )
    problems = validate(hosp, contract, None, icu=broken)
    assert any("id spaces" in p and "caregiver_id" in p for p in problems)
    assert validate(hosp, contract, None, icu=icu) == []  # untouched frames stay clean


# ---------------------------------------------------------------------------
# 5. Manifest provenance + protocol (items 5/6)
# ---------------------------------------------------------------------------


def test_generator_version_bumped() -> None:
    # EP-169 shipped 0.2.0; EP-41 regenerated as 0.3.0 (the planned next regeneration).
    # EP-41: pin "at least the EP-169 bump", not the exact string, so later regenerations
    # under the tests/README.md protocol no longer touch this module.
    major, minor, _patch = (int(p) for p in write_mod.GENERATOR_VERSION.split("."))
    assert (major, minor) >= (0, 2)


def test_manifest_provenance_and_structural_pin(
    manifest: dict[str, Any], contract: Contract
) -> None:
    import numpy
    import polars

    assert write_mod.MANIFEST_PROVENANCE_KEYS == (
        "contract_hash",
        "numpy_version",
        "polars_version",
        "python_version",
    )
    assert set(write_mod.MANIFEST_PROVENANCE_KEYS) <= set(manifest)
    # EP-41: the committed manifest carries the shipped generator version (0.3.0 since
    # EP-41's regeneration), never a literal
    assert manifest["generator_version"] == write_mod.GENERATOR_VERSION
    assert manifest["contract_schema_hash"] == contract.structural_hash()
    # provenance keys are real version strings (the committed ones are the locked versions)
    assert manifest["numpy_version"] == numpy.__version__
    assert manifest["polars_version"] == polars.__version__
    assert re.fullmatch(r"3\.\d+\.\d+", manifest["python_version"])
    assert {e["generator_version"] for e in manifest["files"].values()} == {
        write_mod.GENERATOR_VERSION
    }
    for name, floor in DEFAULT_FLOORS.items():
        assert manifest["spec"][name] == floor, name


def test_rendered_readme_names_floors_hashes_and_protocol() -> None:
    readme = (FIXTURE_DIR / "README.md").read_text(encoding="utf-8")
    for token in (
        "90_000_000",
        "91_000_000",
        "92_000_000",
        "93_000_000",
        "93_900_000",
        "contract_schema_hash",
        "Changing the",  # the tests/README.md protocol section
        "COVERAGE.md",
    ):
        assert token in readme, token


# ---------------------------------------------------------------------------
# 6. Concept coverage note (item 7)
# ---------------------------------------------------------------------------


def test_coverage_note_exists_and_is_linked() -> None:
    path = FIXTURE_DIR / "COVERAGE.md"
    data = path.read_bytes()
    assert data.isascii() and data.endswith(b"\n") and not data.endswith(b"\n\n")
    assert b"\r" not in data and not guard.id_band_hits(data)
    text = data.decode("utf-8")
    for needle in (
        "52033",
        "first_day_bg_art",
        "kdigo_stages",
        "invasive_line",
        "urine_output",
        "EP-41",
        "0.3.0",
        "EP-142",
        "EP-148",
    ):
        assert needle in text, needle
    design = (WORKSPACE / "DESIGN.md").read_text(encoding="utf-8")
    assert "COVERAGE.md" in design


# ---------------------------------------------------------------------------
# 7. CLI: both hashes printed
# ---------------------------------------------------------------------------


def test_schema_cli_prints_both_hashes(contract: Contract) -> None:
    res = runner.invoke(app, ["schema", "list", "--json"])
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    assert payload["content_hash"] == contract.content_hash()
    assert payload["structural_hash"] == contract.structural_hash()
    res = runner.invoke(app, ["schema", "show", "mimiciv_hosp.patients", "--json"])
    assert res.exit_code == 0, res.output
    shown = json.loads(res.output)
    assert shown["contract_hash"] == contract.content_hash()
    assert shown["contract_schema_hash"] == contract.structural_hash()
    res = runner.invoke(app, ["schema", "list"], env={"COLUMNS": "200"})
    assert res.exit_code == 0
    assert "structural" in res.output
