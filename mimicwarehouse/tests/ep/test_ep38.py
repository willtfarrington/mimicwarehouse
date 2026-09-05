"""EP-38 — concept fixes/ports for DuckDB 1.5.x: the patch mechanism + the ported fixes.

Fixture tier (default): the committed patch registry validates (every entry has an https
upstream reference, a matching file whose sha256 and header agree, and the vendored pin as
its ``applies_to_upstream_commit``); the registry render is deterministic, ASCII and guard
clean; the runner refuses a patch written against another upstream commit, a file whose
sha drifted, and a patch identical to the vendored body; ``effective_sql`` prefers the
patch; each ported fix is demonstrated on a **crafted synthetic case** in an in-memory
DuckDB (patched body vs vendored body): a ``sirs`` row with null ``wbc_min`` but a normal
``wbc_max`` scores 0 instead of NULL, a ``complete_blood_count`` MCHC row with the wrong
``valueuom`` is excluded, an ``inflammation`` CRP row without a unit is excluded, a
``charlson`` admission coded C4A no longer counts as malignant cancer while C45/C49/C50
still do, and an APS-III stay with equidistant respiratory-rate arms takes the larger
score per Knaus 1991; on the session fixture lake the patched concepts were built from
their patch files (``status.json`` ``patch_id``, effective ``sql_sha256``,
``meta.concept_versions.patch_id`` set for exactly the registry's concepts, the run
manifest's ``concept_patch`` refs); the docs carry the deviations table.

``tier("dev")``: ``count(patch_id)`` on ``meta.concept_versions`` equals the registry size
(brief acceptance). Everything asserted or printed is counts, hashes, names, statuses and
crafted synthetic values (ids >= 90 000 000) — never a real row or identifier.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

import helpers
from mimicwarehouse import config, guard
from mimicwarehouse import run as run_mod
from mimicwarehouse.concepts import inventory as inv_mod
from mimicwarehouse.concepts import patches as patches_mod
from mimicwarehouse.concepts import runner as concept_runner
from mimicwarehouse.concepts import vendor_info, vendored_path
from mimicwarehouse.dag import runner as dag_runner
from mimicwarehouse.dag import snapshot as snapshot_mod
from mimicwarehouse.loader.manifest import read_status

if TYPE_CHECKING:
    import duckdb as duckdb_mod

    from mimicwarehouse.config import Settings

pytestmark = pytest.mark.ep_38

DOCS = helpers.WORKSPACE / "docs"
CONCEPTS_DOC = DOCS / "resources" / "concepts.md"
DESIGN = helpers.WORKSPACE / "DESIGN.md"
NOTICE = helpers.REPO_ROOT / "NOTICE"
#: The four upstream fixes the brief names, by the concept they patch.
EXPECTED_PATCHED = {"sirs", "complete_blood_count", "inflammation", "charlson", "apsiii"}
#: Synthetic ids (CLAUDE.md: >= 90 000 000, outside every real MIMIC band).
SUBJECT, HADM, STAY = 90_000_001, 90_000_002, 90_000_003
T0 = "2150-01-01 08:00:00"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _body(text: str, concept: str) -> str:
    table, body = inv_mod.split_header(text)
    assert table == concept
    return body


def _vendored_body(concept: str) -> str:
    c = inv_mod.load_inventory().concept(concept)
    return _body(vendored_path(c.path).read_text(encoding="utf-8"), concept)


def _patched_body(concept: str) -> str:
    patch = patches_mod.load_registry().patch_for(concept)
    assert patch is not None, concept
    return _body(patches_mod.patch_text(patch), concept)


def _con() -> duckdb_mod.DuckDBPyConnection:
    import duckdb

    con = duckdb.connect()
    for schema in ("mimiciv_hosp", "mimiciv_icu", "mimiciv_derived"):
        con.execute(f"CREATE SCHEMA {schema}")
    return con


def _table(con: duckdb_mod.DuckDBPyConnection, name: str, ddl: str, rows: list[tuple]) -> None:
    con.execute(f"CREATE TABLE {name} ({ddl})")
    if rows:
        width = len(rows[0])
        con.executemany(
            f"INSERT INTO {name} VALUES ({', '.join('?' * width)})", [list(r) for r in rows]
        )


def _one(con: duckdb_mod.DuckDBPyConnection, sql: str) -> Any:
    row = con.execute(sql).fetchone()
    assert row is not None
    return row


def _lab_rows(*rows: tuple[int, int, float | None, str | None]) -> list[tuple]:
    """``(specimen_id, itemid, valuenum, valueuom)`` -> full labevents rows."""
    return [
        (
            90_100_000 + i,
            SUBJECT,
            HADM,
            spec,
            itemid,
            None,
            T0,
            T0,
            None,
            num,
            uom,
            None,
            None,
            None,
            None,
            None,
        )
        for i, (spec, itemid, num, uom) in enumerate(rows)
    ]


LABEVENTS_DDL = (
    "labevent_id INTEGER, subject_id INTEGER, hadm_id INTEGER, specimen_id INTEGER, "
    "itemid INTEGER, order_provider_id VARCHAR, charttime TIMESTAMP, storetime TIMESTAMP, "
    "value VARCHAR, valuenum DOUBLE, valueuom VARCHAR, ref_range_lower DOUBLE, "
    "ref_range_upper DOUBLE, flag VARCHAR, priority VARCHAR, comments VARCHAR"
)


# ---------------------------------------------------------------------------
# 1. The registry (brief items 2, 4)
# ---------------------------------------------------------------------------


def test_registry_validates_and_names_the_four_fixes(tmp_path: Path) -> None:
    reg = patches_mod.load_registry()
    assert set(reg.concepts) == EXPECTED_PATCHED
    assert len(reg.patches) == len(EXPECTED_PATCHED) == 5
    pin = vendor_info().sha
    inv = inv_mod.load_inventory()
    for p in reg.patches:
        assert p.upstream_ref.startswith("https://github.com/MIT-LCP/mimic-code/")
        assert p.short_ref.startswith("PR #"), p.short_ref
        assert p.applies_to_upstream_commit == pin
        assert patches_mod.patch_path(p).is_file() and p.filename == f"{p.concept}.sql"
        text = patches_mod.validate_patch(p, upstream_commit=pin, inventory=inv)
        assert inv_mod.sql_sha256(text) == p.sql_sha256
        assert text.isascii(), p.patch_id
        head = text.split("DROP TABLE", 1)[0]
        assert p.patch_id in head and p.upstream_ref in head and "MIT License" in head
        assert "THIS SCRIPT IS AUTOMATICALLY GENERATED" in head, "upstream header kept"
        assert p.status == "ported-unmerged", "every upstream PR was open at porting time"
        assert p.date == "2026-09-05" and p.reason
    assert patches_mod.validate_registry() == reg
    assert not patches_mod.stale_registry(), (
        "run `uv run python -m mimicwarehouse.concepts.patches`"
    )
    rendered = patches_mod.render_registry_yaml(reg)
    assert rendered.isascii()
    assert patches_mod.registry_path().read_text(encoding="utf-8") == rendered
    # semantics: the sirs and apsiii ports cannot move real-data values (module docs)
    by = reg.by_concept
    assert by["sirs"].semantics == "unchanged" and by["apsiii"].semantics == "unchanged"
    assert {by[c].semantics for c in ("charlson", "complete_blood_count", "inflammation")} == {
        "changed"
    }
    out = tmp_path / "patches.yaml"
    out.write_text(rendered, encoding="utf-8", newline="\n")
    files = [out, *(patches_mod.patch_path(p) for p in reg.patches)]
    assert guard.scan(files, helpers.REPO_ROOT) == []
    # the vendored tree is untouched: every vendored file still matches the inventory
    assert inv_mod.stale_generated() == []


def test_registry_model_rules() -> None:
    pin = vendor_info().sha
    base = {
        "patch_id": "x-y",
        "concept": "sirs",
        "reason": "r",
        "upstream_ref": "https://github.com/MIT-LCP/mimic-code/pull/1",
        "applies_to_upstream_commit": pin,
        "sql_sha256": "0" * 64,
        "date": "2026-09-05",
    }
    patch = patches_mod.Patch.model_validate(base)
    assert patch.status == "ported-unmerged" and patch.semantics == "changed"
    assert patch.short_ref == "PR #1"
    assert (
        patches_mod.Patch.model_validate(
            {**base, "upstream_ref": "https://github.com/MIT-LCP/mimic-code/issues/1922"}
        ).short_ref
        == "issue #1922"
    )
    assert patches_mod.Patch.model_validate(
        {**base, "upstream_ref": "https://github.com/MIT-LCP/mimic-code/commit/" + "a" * 40}
    ).short_ref.startswith("commit aaaa")
    with pytest.raises(ValueError, match="https"):
        patches_mod.Patch.model_validate({**base, "upstream_ref": "http://example.org/x"})
    with pytest.raises(ValueError, match="patch_id"):
        patches_mod.Patch.model_validate({**base, "patch_id": "Bad_Id"})
    with pytest.raises(ValueError, match="duplicate patch_id"):
        patches_mod.PatchRegistry(
            maintained_by="t", patches=(patch, patch.model_copy(update={"concept": "age"}))
        )
    with pytest.raises(ValueError, match="one patch"):
        patches_mod.PatchRegistry(
            maintained_by="t", patches=(patch, patch.model_copy(update={"patch_id": "x-z"}))
        )
    # an absent registry file is an empty registry
    empty = patches_mod.PatchRegistry(maintained_by="t")
    assert empty.patch_for("sirs") is None and empty.concepts == ()
    assert patches_mod.render_registry_yaml(empty).rstrip().endswith("patches: []")


def test_runner_refuses_mismatched_commit_stale_sha_and_noop_patch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    reg = patches_mod.load_registry()
    sirs = reg.patch_for("sirs")
    assert sirs is not None
    inv = inv_mod.load_inventory()
    # (a) applies_to_upstream_commit differs from the pin -> refused before anything runs
    foreign = sirs.model_copy(update={"applies_to_upstream_commit": "f" * 40})
    with pytest.raises(patches_mod.PatchError, match="re-vendored"):
        patches_mod.validate_patch(foreign, inventory=inv)
    with pytest.raises(patches_mod.PatchError, match="re-vendored"):
        patches_mod.validate_registry(
            patches_mod.PatchRegistry(maintained_by="t", patches=(foreign,)), inventory=inv
        )
    # (b) a drifted file sha
    stale = sirs.model_copy(update={"sql_sha256": "0" * 64})
    with pytest.raises(patches_mod.PatchError, match="differs from the registered"):
        patches_mod.validate_patch(stale, inventory=inv)
    # (c) an unknown concept
    ghost = sirs.model_copy(update={"concept": "ghost"})
    with pytest.raises(patches_mod.PatchError, match="unknown concept"):
        patches_mod.validate_patch(ghost, inventory=inv)
    # (d) a patch whose body equals the vendored one, and one creating another table
    root = tmp_path / "patches"
    root.mkdir()
    monkeypatch.setattr(patches_mod, "patches_root", lambda: root)
    vendored = vendored_path(inv.concept("sirs").path).read_text(encoding="utf-8")
    (root / "sirs.sql").write_text(vendored, encoding="utf-8", newline="\n")
    noop = sirs.model_copy(update={"sql_sha256": inv_mod.sql_sha256(vendored)})
    with pytest.raises(patches_mod.PatchError, match="identical to the vendored"):
        patches_mod.validate_patch(noop, inventory=inv)
    wrong = vendored.replace("mimiciv_derived.sirs", "mimiciv_derived.other")
    (root / "sirs.sql").write_text(wrong, encoding="utf-8", newline="\n")
    other = sirs.model_copy(update={"sql_sha256": inv_mod.sql_sha256(wrong)})
    with pytest.raises(patches_mod.PatchError, match="creates 'other'"):
        patches_mod.validate_patch(other, inventory=inv)
    (root / "sirs.sql").unlink()
    with pytest.raises(patches_mod.PatchError, match="patch file missing"):
        patches_mod.validate_patch(sirs, inventory=inv)
    # the runner wraps the refusal: build_concept on a build whose registry is foreign
    monkeypatch.setattr(
        patches_mod,
        "load_registry",
        lambda: patches_mod.PatchRegistry(maintained_by="t", patches=(foreign,)),
    )
    concept_runner._REGISTRY_CHECKED.discard("ep38-foreign")
    with pytest.raises(concept_runner.ConceptError, match="re-vendored"):
        concept_runner.ensure_registry("ep38-foreign")
    assert "ep38-foreign" not in concept_runner._REGISTRY_CHECKED


def test_effective_sql_prefers_the_patch_and_checks_the_vendored_sha() -> None:
    inv = inv_mod.load_inventory()
    reg = patches_mod.load_registry()
    text, patch = patches_mod.effective_sql(inv.concept("sirs"), reg)
    assert patch is not None and patch.patch_id == "sirs-wbc-guard"
    assert "COALESCE(wbc_min, wbc_max, bands_max)" in text
    age = inv.concept("age")
    text, patch = patches_mod.effective_sql(age, reg)
    assert patch is None and inv_mod.sql_sha256(text) == age.sql_sha256
    drifted = inv_mod.Concept(**{**age.model_dump(), "sql_sha256": "f" * 64})
    with pytest.raises(patches_mod.PatchError, match="differs from the committed inventory"):
        patches_mod.effective_sql(drifted, reg)
    # the docs status cell names the patch; an unpatched concept keeps EP-37's text
    assert inv_mod.concept_status("age") == inv_mod.STATUS_OK
    assert inv_mod.concept_status("sirs").endswith("; patched: `sirs-wbc-guard (PR #2146)` (EP-38)")
    assert inv_mod.concept_status("sirs", {"sirs": "BinderException"}, {}).startswith(
        "fails: Binder"
    )
    assert set(inv_mod.patched_concepts()) == EXPECTED_PATCHED


# ---------------------------------------------------------------------------
# 2. Regression cases per patch, crafted synthetic inputs (brief item 4)
# ---------------------------------------------------------------------------


def _icustays(con: duckdb_mod.DuckDBPyConnection) -> None:
    _table(
        con,
        "mimiciv_icu.icustays",
        "subject_id INTEGER, hadm_id INTEGER, stay_id INTEGER, first_careunit VARCHAR, "
        "last_careunit VARCHAR, intime TIMESTAMP, outtime TIMESTAMP, los DOUBLE",
        [(SUBJECT, HADM, STAY, "MICU", "MICU", T0, "2150-01-04 08:00:00", 3.0)],
    )


def test_sirs_null_wbc_min_with_normal_wbc_max_scores_zero() -> None:
    con = _con()
    try:
        _icustays(con)
        _table(con, "mimiciv_derived.first_day_bg_art", "stay_id INTEGER, pco2_min DOUBLE", [])
        _table(
            con,
            "mimiciv_derived.first_day_vitalsign",
            "stay_id INTEGER, temperature_min DOUBLE, temperature_max DOUBLE, "
            "heart_rate_max DOUBLE, resp_rate_max DOUBLE",
            [(STAY, 36.5, 37.2, 80.0, 16.0)],
        )
        _table(
            con,
            "mimiciv_derived.first_day_lab",
            "stay_id INTEGER, wbc_min DOUBLE, wbc_max DOUBLE, bands_max DOUBLE",
            [(STAY, None, 9.0, None)],
        )
        vendored = _one(con, f"SELECT wbc_score, sirs FROM ({_vendored_body('sirs')})")
        patched = _one(con, f"SELECT wbc_score, sirs FROM ({_patched_body('sirs')})")
        assert vendored == (None, 0), "upstream: the null wbc_min hides the normal wbc_max"
        assert patched == (0, 0), "ported: wbc_max is present and normal -> no criterion"
        # an abnormal wbc_max and an all-null panel behave the same in both versions
        con.execute("UPDATE mimiciv_derived.first_day_lab SET wbc_max = 13.0")
        assert _one(con, f"SELECT wbc_score FROM ({_patched_body('sirs')})") == (1,)
        assert _one(con, f"SELECT wbc_score FROM ({_vendored_body('sirs')})") == (1,)
        con.execute("UPDATE mimiciv_derived.first_day_lab SET wbc_max = NULL")
        assert _one(con, f"SELECT wbc_score FROM ({_patched_body('sirs')})") == (None,)
    finally:
        con.close()


def test_cbc_mchc_row_with_wrong_valueuom_is_excluded() -> None:
    con = _con()
    try:
        _table(
            con,
            "mimiciv_hosp.labevents",
            LABEVENTS_DDL,
            _lab_rows(
                (90_200_001, 51249, 33.0, "%"),  # MCHC mis-recorded in %
                (90_200_001, 51222, 12.0, "g/dL"),  # hemoglobin on the same specimen
                (90_200_002, 51249, 34.0, "g/dL"),  # MCHC in the expected unit
                (90_200_003, 51249, 35.0, "%"),  # a specimen carrying only a bad MCHC row
            ),
        )
        vendored = con.execute(
            f"SELECT specimen_id, mchc, hemoglobin FROM ({_vendored_body('complete_blood_count')}) "
            "ORDER BY 1"
        ).fetchall()
        patched = con.execute(
            f"SELECT specimen_id, mchc, hemoglobin FROM ({_patched_body('complete_blood_count')}) "
            "ORDER BY 1"
        ).fetchall()
        assert vendored == [
            (90_200_001, 33.0, 12.0),
            (90_200_002, 34.0, None),
            (90_200_003, 35.0, None),
        ]
        assert patched == [(90_200_001, None, 12.0), (90_200_002, 34.0, None)], (
            "the % row feeds no mchc and no longer qualifies its specimen"
        )
    finally:
        con.close()


def test_inflammation_crp_without_unit_is_excluded() -> None:
    con = _con()
    try:
        _table(
            con,
            "mimiciv_hosp.labevents",
            LABEVENTS_DDL,
            _lab_rows(
                (90_300_001, 50889, 12.5, "mg/L"),
                (90_300_002, 50889, 7.0, None),  # unit missing
                (90_300_003, 50889, 0.7, "mg/dL"),  # another unit
            ),
        )
        vendored = con.execute(
            f"SELECT specimen_id, crp FROM ({_vendored_body('inflammation')}) ORDER BY 1"
        ).fetchall()
        patched = con.execute(
            f"SELECT specimen_id, crp FROM ({_patched_body('inflammation')}) ORDER BY 1"
        ).fetchall()
        assert vendored == [(90_300_001, 12.5), (90_300_002, 7.0), (90_300_003, 0.7)]
        assert patched == [(90_300_001, 12.5)]
    finally:
        con.close()


@pytest.mark.parametrize(
    ("icd10", "vendored_flag", "patched_flag"),
    [
        ("C4A0", 1, 0),  # Merkel cell carcinoma: lexically inside C45-C58, excluded by Quan
        ("C4590", 1, 1),  # mesothelioma: first code of the split range
        ("C499", 1, 1),  # last code of the lower half
        ("C50919", 1, 1),  # breast: first code of the upper half
        ("C7A00", 0, 0),  # neuroendocrine: unmapped in both (PR #2043 would add it)
        ("I10", 0, 0),  # not a malignancy
    ],
)
def test_charlson_c4a_no_longer_counts_as_malignant_cancer(
    icd10: str, vendored_flag: int, patched_flag: int
) -> None:
    con = _con()
    try:
        _table(
            con,
            "mimiciv_hosp.admissions",
            "subject_id INTEGER, hadm_id INTEGER, admittime TIMESTAMP, dischtime TIMESTAMP",
            [(SUBJECT, HADM, T0, "2150-01-04 08:00:00")],
        )
        _table(
            con,
            "mimiciv_hosp.diagnoses_icd",
            "subject_id INTEGER, hadm_id INTEGER, seq_num INTEGER, icd_code VARCHAR, "
            "icd_version INTEGER",
            [(SUBJECT, HADM, 1, icd10, 10)],
        )
        _table(
            con,
            "mimiciv_derived.age",
            "subject_id INTEGER, hadm_id INTEGER, age DOUBLE",
            [(SUBJECT, HADM, 45.0)],
        )
        sql = "SELECT malignant_cancer, charlson_comorbidity_index FROM ({})"
        assert _one(con, sql.format(_vendored_body("charlson"))) == (
            vendored_flag,
            2 * vendored_flag,
        )
        assert _one(con, sql.format(_patched_body("charlson"))) == (patched_flag, 2 * patched_flag)
    finally:
        con.close()


def test_apsiii_equidistant_resp_rate_arms_take_the_larger_score() -> None:
    """resp_rate_min 13 (score 7) and resp_rate_max 25 (score 6) are both 6 from the
    reference 19: the equidistant rule picks the larger score, 7 (Knaus 1991 tables as
    coded upstream). The vendored predicate compared the max arm to itself, which was
    only ever reached in this equality case — so both versions agree (no count effect)."""
    con = _con()
    try:
        _icustays(con)
        _table(
            con,
            "mimiciv_hosp.admissions",
            "subject_id INTEGER, hadm_id INTEGER, admittime TIMESTAMP, dischtime TIMESTAMP",
            [(SUBJECT, HADM, T0, "2150-01-04 08:00:00")],
        )
        _table(
            con,
            "mimiciv_hosp.patients",
            "subject_id INTEGER, gender VARCHAR, anchor_age INTEGER",
            [(SUBJECT, "F", 60)],
        )
        _table(
            con,
            "mimiciv_hosp.diagnoses_icd",
            "subject_id INTEGER, hadm_id INTEGER, seq_num INTEGER, icd_code VARCHAR, "
            "icd_version INTEGER",
            [],
        )
        _table(
            con,
            "mimiciv_derived.bg",
            "subject_id INTEGER, hadm_id INTEGER, charttime TIMESTAMP, specimen VARCHAR, "
            "po2 DOUBLE, pco2 DOUBLE, ph DOUBLE, aado2 DOUBLE, fio2 DOUBLE, "
            "fio2_chartevents DOUBLE",
            [],
        )
        _table(
            con,
            "mimiciv_derived.ventilation",
            "stay_id INTEGER, starttime TIMESTAMP, endtime TIMESTAMP, ventilation_status VARCHAR",
            [],
        )
        _table(
            con,
            "mimiciv_derived.first_day_urine_output",
            "stay_id INTEGER, urineoutput DOUBLE",
            [(STAY, 1500.0)],
        )
        _table(
            con,
            "mimiciv_derived.first_day_lab",
            "stay_id INTEGER, hematocrit_min DOUBLE, hematocrit_max DOUBLE, wbc_min DOUBLE, "
            "wbc_max DOUBLE, creatinine_min DOUBLE, creatinine_max DOUBLE, bun_min DOUBLE, "
            "bun_max DOUBLE, sodium_min DOUBLE, sodium_max DOUBLE, albumin_min DOUBLE, "
            "albumin_max DOUBLE, bilirubin_total_min DOUBLE, bilirubin_total_max DOUBLE, "
            "glucose_min DOUBLE, glucose_max DOUBLE",
            [
                (
                    STAY,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                )
            ],
        )
        _table(
            con,
            "mimiciv_derived.first_day_gcs",
            "stay_id INTEGER, gcs_min DOUBLE, gcs_motor DOUBLE, gcs_verbal DOUBLE, "
            "gcs_eyes DOUBLE, gcs_unable INTEGER",
            [(STAY, 15.0, 6.0, 5.0, 4.0, 0)],
        )
        _table(
            con,
            "mimiciv_derived.first_day_vitalsign",
            "stay_id INTEGER, heart_rate_min DOUBLE, heart_rate_max DOUBLE, mbp_min DOUBLE, "
            "mbp_max DOUBLE, temperature_min DOUBLE, temperature_max DOUBLE, "
            "resp_rate_min DOUBLE, resp_rate_max DOUBLE, glucose_min DOUBLE, glucose_max DOUBLE",
            [(STAY, 70.0, 80.0, 85.0, 95.0, 36.5, 37.0, 13.0, 25.0, None, None)],
        )
        sql = "SELECT resp_rate_score, hr_score, mbp_score, temp_score, gcs_score, apsiii FROM ({})"
        patched = _one(con, sql.format(_patched_body("apsiii")))
        vendored = _one(con, sql.format(_vendored_body("apsiii")))
        assert patched == (7, 0, 0, 0, 0, 11), "equidistant arms -> the larger score (7); +4 uo"
        assert vendored == patched, "the typo was only reachable in the equality case"
        # the boundary the paper draws: 14 breaths/min is the first value scoring 0
        con.execute(
            "UPDATE mimiciv_derived.first_day_vitalsign SET resp_rate_min = 14, resp_rate_max = 24"
        )
        assert _one(con, sql.format(_patched_body("apsiii")))[0] == 0
    finally:
        con.close()


# ---------------------------------------------------------------------------
# 3. The session fixture lake: built from the patches (brief items 2, 5)
# ---------------------------------------------------------------------------


def test_fixture_lake_built_patched_concepts_from_their_patch_files(
    fixture_lake_settings: Settings, fixture_lake_catalog: duckdb_mod.DuckDBPyConnection
) -> None:
    settings = fixture_lake_settings
    lake = settings.lake_root("fixture")
    status = read_status(lake)["steps"]
    lines = dict(snapshot_mod.layer_lines(lake, "fixture", layer="derived", settings=settings))
    reg = patches_mod.load_registry()
    inv = inv_mod.load_inventory()
    for concept in inv.concepts:
        entry = status[concept.target]
        line = lines[concept.target]
        patch = reg.patch_for(concept.name)
        assert entry["vendored_sha256"] == concept.sql_sha256
        if patch is None:
            assert entry["patch_id"] is None and entry["sql_sha256"] == concept.sql_sha256
            assert line.source_sha256 == concept.sql_sha256
        else:
            assert entry["patch_id"] == patch.patch_id, concept.name
            assert entry["sql_sha256"] == patch.sql_sha256 == line.source_sha256
            assert patch.patch_id in entry["comment"]
    rows = fixture_lake_catalog.execute(
        "SELECT concept, patch_id, sql_sha256, run_id FROM meta.concept_versions ORDER BY 1"
    ).fetchall()
    patched = {r[0]: r[1] for r in rows if r[1] is not None}
    assert patched == {p.concept: p.patch_id for p in reg.patches}
    assert {r[2] for r in rows if r[1] is not None} == {p.sql_sha256 for p in reg.patches}
    n = fixture_lake_catalog.execute("SELECT count(patch_id) FROM meta.concept_versions").fetchone()
    assert n is not None and n[0] == len(reg.patches), "acceptance: count(patch_id) = registry"
    (run_id,) = {r[3] for r in rows}
    manifest = run_mod.read_manifest(run_id, settings)
    assert manifest.params["patched"] == len(reg.patches)
    assert manifest.params["patches"] == [p.patch_id for p in reg.patches]
    # the EP-37 ref shape is kept (one `concept` ref per concept, 65 refs); a patched
    # concept's ref carries the patch file's sha256 as its hash
    hashes = {ref.name: ref.hash for ref in manifest.refs if ref.kind == "concept"}
    assert len(hashes) == 65
    assert {p.concept: p.sql_sha256 for p in reg.patches} == {
        p.concept: hashes[p.concept] for p in reg.patches
    }
    # the fixture views of the patched concepts exist and hold rows (sirs: one per stay)
    stays = fixture_lake_catalog.execute("SELECT count(*) FROM mimiciv_icu.icustays").fetchone()
    sirs = fixture_lake_catalog.execute("SELECT count(*) FROM mimiciv_derived.sirs").fetchone()
    assert stays is not None and sirs is not None and sirs[0] == stays[0]


def test_crafted_concept_records_no_patch_and_forced_rebuild_applies_one(
    fixture_lake_settings: Settings,
) -> None:
    """``--select concept.score.sirs --force`` on the session lake rebuilds sirs from the
    patch (status carries the patch id; the logical derived snapshot is unchanged because
    the rebuilt file is identical)."""
    from mimicwarehouse.dag.spec import load_dag

    settings = fixture_lake_settings
    lake = settings.lake_root("fixture")
    before = snapshot_mod.layer_snapshot(lake, "derived", "fixture", settings=settings)
    result = dag_runner.run(
        load_dag(), "fixture", select=["concept.score.sirs"], force=True, settings=settings
    )
    assert result.ok and [s.status for s in result.steps] == ["done"]
    entry = read_status(lake)["steps"]["mimiciv_derived.sirs"]
    assert entry["patch_id"] == "sirs-wbc-guard"
    after = snapshot_mod.layer_snapshot(lake, "derived", "fixture", settings=settings)
    assert after == before


def test_crafted_sql_bypasses_the_registry(scratch_settings: Settings) -> None:
    from mimicwarehouse.engine import open_duckdb

    inv = inv_mod.load_inventory()
    concept = inv_mod.Concept(
        name="sirs",  # a patched name, but sql_text wins and no patch is recorded
        group="test",
        path="crafted.sql",
        sql_sha256="0" * 64,
        upstream_commit=inv.upstream_commit,
        order=0,
    )
    con = open_duckdb("build", settings=scratch_settings, memory_limit="1GB")
    try:
        ctx = dag_runner.StepContext(
            settings=scratch_settings,
            tier="fixture",
            build_id="ep38-crafted",
            con=con,
            log=logging.getLogger("test_ep38"),
            raw_root=scratch_settings.data_root,
            lake_root=scratch_settings.lake_root("fixture"),
            buckets=None,
        )
        sql = (
            "DROP TABLE IF EXISTS mimiciv_derived.sirs; CREATE TABLE mimiciv_derived.sirs AS "
            "SELECT 1 AS one"
        )
        rows, _ = concept_runner.build_concept(concept, ctx, sql_text=sql)
        assert rows == 1
        entry = read_status(ctx.lake_root)["steps"]["mimiciv_derived.sirs"]
        assert entry["patch_id"] is None and entry["sql_sha256"] == "0" * 64
        assert entry["vendored_sha256"] == "0" * 64
    finally:
        con.close()


@pytest.fixture
def scratch_settings(tmp_path: Path) -> Settings:
    from mimicwarehouse.config import Settings

    return Settings(data_root=tmp_path / "root", min_free_gb=1)


# ---------------------------------------------------------------------------
# 4. dev tier (brief acceptance: count(patch_id) on dev = registry size)
# ---------------------------------------------------------------------------


@pytest.mark.tier("dev")
def test_dev_concept_versions_patch_count_matches_registry(dev_catalog: Path) -> None:
    from mimicwarehouse.safe import safe_query

    settings = config.load_settings()
    entry = read_status(settings.lake_root("dev"))["steps"].get("mimiciv_derived.sirs")
    if entry is None or not snapshot_mod.complete_for_tier(entry, "dev"):
        pytest.skip("dev concepts not built yet (mwh build --tier dev --tag concepts)")
    if entry.get("patch_id") is None:
        pytest.skip("dev sirs not yet rebuilt from its patch (EP-38 item 5)")
    # `count(patch_id)` = 5 is a small cell (k = 11) and would be suppressed: read the
    # released complement (unpatched count) and the total, subtract in Python (EP-37
    # amendment 4: ratios/differences over released counts are computed here, never in SQL)
    reg = patches_mod.load_registry()
    total = safe_query(
        "SELECT count(*) AS n FROM meta.concept_versions", tier="dev", settings=settings
    )
    unpatched = safe_query(
        "SELECT count(*) AS n FROM meta.concept_versions WHERE patch_id IS NULL",
        tier="dev",
        settings=settings,
    )
    assert total.n_rows == 1 and unpatched.n_rows == 1
    assert int(total.df["n"][0]) == 65
    assert int(total.df["n"][0]) - int(unpatched.df["n"][0]) == len(reg.patches)
    # and the status ledger (metadata, not data) names exactly the registry's concepts
    status = read_status(settings.lake_root("dev"))["steps"]
    built_from_patch = {
        key.split(".", 1)[1]
        for key, e in status.items()
        if key.startswith("mimiciv_derived.") and e.get("patch_id") is not None
    }
    assert built_from_patch == set(reg.concepts)


# ---------------------------------------------------------------------------
# 5. Docs + import budget (brief item 6)
# ---------------------------------------------------------------------------


def test_docs_list_every_patch_and_keep_attribution() -> None:
    text = CONCEPTS_DOC.read_text(encoding="utf-8")
    reg = patches_mod.load_registry()
    assert "## Deviations" in text
    deviations = text.split("## Deviations", 1)[1]
    for p in reg.patches:
        assert f"`{p.patch_id}`" in deviations, p.patch_id
        assert p.upstream_ref in deviations, p.patch_id
    assert "PR #2046" in deviations and "PR #2043" in deviations, "the unported alternatives"
    assert inv_mod.render_markdown_table(inv_mod.load_inventory()) in text, (
        "regenerate: python -m mimicwarehouse.concepts.inventory"
    )
    for required in ("patches.yaml", "applies_to_upstream_commit", "ported-unmerged"):
        assert required in text, required
    design = DESIGN.read_text(encoding="utf-8")
    assert "> **Note (2026-09-05, EP-38" in design
    notice = NOTICE.read_text(encoding="utf-8")
    assert "EP-38" in notice and "MIT License" in notice, "NOTICE unchanged: patches cite it"
    violations = guard.scan([CONCEPTS_DOC, patches_mod.registry_path()], helpers.REPO_ROOT)
    assert violations == [], [f"{v.rule}: {v.path}" for v in violations]


def test_patches_module_keeps_duckdb_lazy() -> None:
    helpers.assert_import_budget("mimicwarehouse.concepts.patches")
    helpers.assert_import_budget("mimicwarehouse.concepts.inventory")
