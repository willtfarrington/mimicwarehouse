"""EP-38 — concept fixes / ports for DuckDB 1.5.x (the patch mechanism).

Fixture tier (default): the committed patch registry validates (every entry names a
vendored concept, cites an upstream mimic-code PR, applies to the EP-8 pin, hashes its
file, keeps the MIT attribution, creates the concept it claims and adds no dependency
the generated DAG spec lacks); crafted registries are refused per violation and the
runner refuses to start on an ``applies_to_upstream_commit`` mismatch; the runner
prefers a patch and records it (status entry, manifest line, run refs,
``meta.concept_versions``, the catalog view comment); one crafted synthetic case per
patch demonstrates the fix against in-memory DuckDB tables (ids >= 90 000 000): the
SIRS ``wbc_max`` guard, the MCHC / CRP ``valueuom`` filters, the Charlson C4A exclusion,
the APS III equidistant arms at the boundary; the rebuild selection (patched concepts +
their dependents); the pins carry the patch map and ``--refresh`` records before/after;
the docs tables are in sync; the CLI entry points; hygiene and the import budget.
``tier("dev")`` / ``tier("full")``: ``meta.concept_versions`` names exactly the
registry's concepts in ``patch_id`` (through ``safe_query``; the released complement
count stands in for the below-k patched count). ``@pytest.mark.demo``: the committed
demo pins equal a fresh computation, patch map included.

Everything asserted or printed is text, hashes, statuses and suppressed counts.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

import duckdb
import pytest
import yaml

import helpers
from mimicwarehouse import config
from mimicwarehouse.concepts import patching, vendor_info
from mimicwarehouse.concepts import pins as pins_mod
from mimicwarehouse.concepts import runner as concepts_runner
from mimicwarehouse.concepts.inventory import (
    CATALOG_STEP,
    DUCKDB_STATUS_OK,
    VERSIONS_STEP,
    InventoryError,
    load_inventory,
    render_inventory_table,
)
from mimicwarehouse.concepts.patching import (
    Patch,
    PatchError,
    PatchRegistry,
    load_patch_sql,
    load_registry,
    patch_path,
    rebuild_steps,
    registry_summary,
    render_patch_table,
    validate_registry,
)
from mimicwarehouse.concepts.runner import load_concept_sql, strip_header
from mimicwarehouse.dag import runner as runner_mod
from mimicwarehouse.dag.spec import load_dag
from mimicwarehouse.loader import manifest as manifest_mod

if TYPE_CHECKING:
    from mimicwarehouse.config import Settings

pytestmark = pytest.mark.ep_38

HOSP = "mimiciv_hosp"
ICU = "mimiciv_icu"
DERIVED = "mimiciv_derived"
#: Synthetic ids (D-27: >= 90 000 000, outside every real MIMIC band).
SUBJECT = 90_000_001
HADM = 92_000_001
STAY = 93_000_001
SPECIMEN = 95_000_001
#: The four fixes the brief names, by the concept each patch replaces.
BRIEF_CONCEPTS = ("sirs", "complete_blood_count", "charlson", "apsiii")
BAND_TOKEN = re.compile(r"(?<![\w.])[123]\d{7}(?![\w.])")


@pytest.fixture
def settings_root(tmp_path: Path) -> Settings:
    from mimicwarehouse.config import Settings

    return Settings(data_root=tmp_path / "root")


@pytest.fixture(scope="module")
def registry() -> PatchRegistry:
    return load_registry()


# ---------------------------------------------------------------------------
# 1. The committed registry validates; crafted violations are refused
# ---------------------------------------------------------------------------


def test_committed_registry_validates(registry: PatchRegistry) -> None:
    assert validate_registry(registry) == []
    inv = load_inventory()
    pin = vendor_info().sha
    assert registry.patches, "EP-38 ships at least the four brief ports"
    assert set(BRIEF_CONCEPTS) <= set(registry.concepts)
    for p in registry.patches:
        assert p.applies_to_upstream_commit == pin == inv.upstream_commit
        assert p.upstream_ref.startswith("https://github.com/MIT-LCP/mimic-code/pull/")
        assert p.status in patching.STATUSES and p.semantics in patching.SEMANTICS
        assert p.date == "2026-09-06" and p.demo_effect and p.reason
        path = patch_path(p.concept)
        assert path.is_file() and patching.sha256_of(path) == p.sql_sha256
        text = load_patch_sql(p)
        target, body = strip_header(text)
        assert target == p.concept and body.upper().startswith(("SELECT", "WITH"))
        assert p.upstream_ref in text and patching.LICENSE_MARK in text and "EP-38" in text
        # the patch differs from the vendored file (else it would be no patch at all)
        assert body != strip_header(load_concept_sql(inv.concept(p.concept)))[1]
    assert registry.by_id().keys() == {p.patch_id for p in registry.patches}
    assert registry.for_concept("age") is None
    assert registry_summary(registry) == {p.concept: p.patch_id for p in registry.patches}


def _write_registry(root: Path, patches: list[dict[str, Any]]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / patching.REGISTRY_FILENAME).write_text(
        yaml.safe_dump({"version": 1, "patches": patches}, sort_keys=False), encoding="utf-8"
    )


def _crafted_root(tmp_path: Path, registry: PatchRegistry, **update: Any) -> tuple[Path, Patch]:
    """A registry dir under ``tmp_path`` with the real first patch file and one entry
    (``update`` overrides its fields)."""
    root = tmp_path / "patches"
    root.mkdir()
    first = registry.patches[0]
    shutil.copy(patch_path(first.concept), root / first.file_name)
    entry = first.model_copy(update=update)
    _write_registry(root, [entry.model_dump(mode="json")])
    return root, entry


def test_crafted_violations_are_refused(tmp_path: Path, registry: PatchRegistry) -> None:
    root, entry = _crafted_root(tmp_path, registry)
    assert validate_registry(load_registry(root), root=root) == []
    # commit mismatch (a re-vendor) — the review trigger
    _write_registry(
        root,
        [entry.model_copy(update={"applies_to_upstream_commit": "0" * 40}).model_dump(mode="json")],
    )
    problems = validate_registry(load_registry(root), root=root)
    assert len(problems) == 1 and "applies_to_upstream_commit" in problems[0]
    assert "re-vendored" in problems[0]
    with pytest.raises(PatchError, match="refused"):
        patching.check_registry(root=root)
    # drifted file bytes
    _write_registry(root, [entry.model_dump(mode="json")])
    sql = root / entry.file_name
    sql.write_text(sql.read_text(encoding="utf-8") + "-- drift\n", encoding="utf-8")
    problems = validate_registry(load_registry(root), root=root)
    assert any("sha256" in p for p in problems)
    with pytest.raises(PatchError, match="sha256"):
        load_patch_sql(entry, root)
    shutil.copy(patch_path(entry.concept), sql)
    # unknown concept, a reference outside the upstream repo, a missing file, an orphan
    _write_registry(
        root,
        [
            entry.model_dump(mode="json"),
            entry.model_copy(
                update={
                    "patch_id": "ghost-fix",
                    "concept": "ghost",
                    "upstream_ref": "https://example.org/x",
                }
            ).model_dump(mode="json"),
        ],
    )
    (root / "age.sql").write_text("SELECT 1\n", encoding="utf-8")
    problems = validate_registry(load_registry(root), root=root)
    joined = "\n".join(problems)
    assert "unknown concept 'ghost'" in joined and "not under" in joined
    assert "age.sql: no registry entry" in joined and "ghost.sql missing" in joined
    (root / "age.sql").unlink()
    # a patch that creates another concept, or adds a dependency the spec lacks
    text = sql.read_text(encoding="utf-8")
    sql.write_text(
        text.replace(f"mimiciv_derived.{entry.concept}", "mimiciv_derived.other"), encoding="utf-8"
    )
    problems = validate_registry(load_registry_with_sha(root, entry, sql), root=root)
    assert any("creates other" in p for p in problems)
    sql.write_text(
        text.rstrip("\n") + "\n-- keep\nUNION ALL SELECT * FROM mimiciv_derived.age LIMIT 0\n",
        encoding="utf-8",
    )
    problems = validate_registry(load_registry_with_sha(root, entry, sql), root=root)
    assert any("references ['age']" in p for p in problems), problems
    # duplicates
    _write_registry(root, [entry.model_dump(mode="json"), entry.model_dump(mode="json")])
    shutil.copy(patch_path(entry.concept), sql)
    joined = "\n".join(validate_registry(load_registry(root), root=root))
    assert "duplicate patch_id" in joined and "more than one patch" in joined
    # malformed YAML shapes
    (root / patching.REGISTRY_FILENAME).write_text("- not: a mapping\n", encoding="utf-8")
    with pytest.raises(PatchError, match="mapping"):
        load_registry(root)
    (root / patching.REGISTRY_FILENAME).write_text(
        "version: 1\npatches:\n  - {}\n", encoding="utf-8"
    )
    with pytest.raises(PatchError):
        load_registry(root)
    (root / patching.REGISTRY_FILENAME).unlink()
    assert load_registry(root).patches == ()


def load_registry_with_sha(root: Path, entry: Patch, sql: Path) -> PatchRegistry:
    """The crafted registry with ``entry``'s sha refreshed to the edited file's."""
    refreshed = entry.model_copy(update={"sql_sha256": patching.sha256_of(sql)})
    _write_registry(root, [refreshed.model_dump(mode="json")])
    return load_registry(root)


def test_patch_model_rejects_bad_fields(registry: PatchRegistry) -> None:
    first = registry.patches[0].model_dump(mode="json")
    for field, bad in (
        ("patch_id", "Bad Id"),
        ("upstream_ref", "http://github.com/MIT-LCP/mimic-code/pull/1"),
        ("sql_sha256", "abc"),
        ("date", "06-09-2026"),  # ISO only (a compact date would also trip guard G4)
        ("status", "merged"),
        ("semantics", "cosmetic"),
    ):
        with pytest.raises(ValueError):
            Patch.model_validate({**first, field: bad})
    with pytest.raises(ValueError):
        Patch.model_validate({**first, "extra": 1})


# ---------------------------------------------------------------------------
# 2. The runner: refuses to start on a mismatch, prefers a patch, records it
# ---------------------------------------------------------------------------


def test_runner_refuses_to_start_on_commit_mismatch(
    settings_root: Settings,
    tmp_path: Path,
    registry: PatchRegistry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _entry = _crafted_root(tmp_path, registry, applies_to_upstream_commit="1" * 40)
    monkeypatch.setattr(patching, "patches_root", lambda: root)
    # even an unpatched concept is refused: the registry is checked before any SQL runs
    result = runner_mod.run(
        load_dag(),
        "fixture",
        select=["concept.demographics.age"],
        with_deps=True,
        settings=settings_root,
    )
    report = next(s for s in result.steps if s.name == "concept.demographics.age")
    assert report.status == "failed" and report.error is not None
    assert report.error.startswith("ConceptError: concept patch registry refused")
    assert "applies_to_upstream_commit" in report.error and "re-vendored" in report.error
    lake = settings_root.lake_root("fixture")
    assert f"{DERIVED}.age" not in manifest_mod.read_status(lake)["steps"]
    assert not concepts_runner.derived_table_dir(lake, "fixture", DERIVED, "age").exists()


def test_runner_prefers_patch_and_records_it(
    settings_root: Settings, registry: PatchRegistry
) -> None:
    from mimicwarehouse import run as run_mod

    inv = load_inventory()
    patch = registry.for_concept("complete_blood_count")
    assert patch is not None
    patched_step = "concept.measurement.complete_blood_count"
    plain_step = "concept.demographics.age"
    result = runner_mod.run(
        load_dag(),
        "fixture",
        select=[patched_step, plain_step],
        with_deps=True,
        settings=settings_root,
        provenance=True,
    )
    assert result.ok and result.run_id is not None
    lake = settings_root.lake_root("fixture")
    status = manifest_mod.read_status(lake)["steps"]
    attempt = status[f"{DERIVED}.complete_blood_count"]["tiers"]["fixture"]
    assert attempt["patch_id"] == patch.patch_id and attempt["sql_sha256"] == patch.sql_sha256
    assert attempt["vendored_sha256"] == inv.concept("complete_blood_count").sql_sha256
    plain = status[f"{DERIVED}.age"]["tiers"]["fixture"]
    assert plain["patch_id"] is None and plain["sql_sha256"] == inv.concept("age").sql_sha256
    # the manifest line carries the executed SQL's hash
    lines = {
        ln.table: ln
        for ln in manifest_mod.iter_manifest(manifest_mod.manifest_path(lake, result.build_id))
        if ln.schema_name == DERIVED
    }
    assert lines["complete_blood_count"].source_sha256 == patch.sql_sha256
    assert lines["age"].source_sha256 == inv.concept("age").sql_sha256
    # the run cites the concept (executed hash) and the patch (pin + file hash)
    m = run_mod.read_manifest(result.run_id, settings_root)
    refs = {(r.kind, r.name): r for r in m.refs}
    assert refs[("concept", "complete_blood_count")].hash == patch.sql_sha256
    ref = refs[("concept_patch", patch.patch_id)]
    assert ref.version == patch.applies_to_upstream_commit and ref.hash == patch.sql_sha256
    assert ("concept_patch", "age") not in refs and refs[("concept", "age")].hash == inv.concept(
        "age"
    ).sql_sha256
    # meta.concept_versions: patch_id set iff patched
    versions = runner_mod.run(load_dag(), "fixture", select=[VERSIONS_STEP], settings=settings_root)
    assert versions.ok
    rows = {
        r[0]: r
        for r in concepts_runner.concept_versions_rows(lake, "fixture", settings=settings_root)
    }
    assert (
        rows["complete_blood_count"][3] == patch.sql_sha256
        and rows["complete_blood_count"][4] == patch.patch_id
    )
    assert rows["age"][3] == inv.concept("age").sql_sha256 and rows["age"][4] is None


def test_fixture_lake_catalog_carries_patch_ids(
    fixture_lake_catalog: duckdb.DuckDBPyConnection, registry: PatchRegistry
) -> None:
    con = fixture_lake_catalog
    rows = con.execute(
        "SELECT concept, patch_id, sql_sha256 FROM meta.concept_versions "
        "WHERE patch_id IS NOT NULL ORDER BY 1"
    ).fetchall()
    assert {r[0]: r[1] for r in rows} == registry_summary(registry)
    assert {r[0]: r[2] for r in rows} == {p.concept: p.sql_sha256 for p in registry.patches}
    for p in registry.patches:
        comment = con.execute(
            f"SELECT comment FROM duckdb_views() WHERE schema_name = '{DERIVED}' "
            f"AND view_name = '{p.concept}'"
        ).fetchone()
        assert comment is not None and p.patch_id in comment[0] and "EP-38" in comment[0]
        assert p.sql_sha256[:12] in comment[0]
    plain = con.execute(
        f"SELECT comment FROM duckdb_views() WHERE schema_name = '{DERIVED}' AND view_name = 'age'"
    ).fetchone()
    assert plain is not None and "patch" not in plain[0]


# ---------------------------------------------------------------------------
# 3. One crafted synthetic case per patch (in-memory DuckDB, ids >= 90 000 000)
# ---------------------------------------------------------------------------


def _con() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()  # in-memory; crafted synthetic rows only
    for schema in (HOSP, ICU, DERIVED):
        con.execute(f"CREATE SCHEMA {schema}")
    return con


def _rows(con: duckdb.DuckDBPyConnection, concept: str, *, patched: bool) -> list[dict[str, Any]]:
    """Run the concept's SELECT body (the patch or the vendored file) on the crafted tables."""
    if patched:
        patch = load_registry().for_concept(concept)
        assert patch is not None, concept
        text = load_patch_sql(patch)
    else:
        text = load_concept_sql(load_inventory().concept(concept))
    _target, body = strip_header(text)
    cur = con.execute(body)
    columns = [d[0] for d in cur.description or ()]
    return [dict(zip(columns, row, strict=True)) for row in cur.fetchall()]


def test_sirs_patch_guards_a_lone_wbc_max() -> None:
    con = _con()
    con.execute(f"CREATE TABLE {ICU}.icustays (subject_id BIGINT, hadm_id BIGINT, stay_id BIGINT)")
    con.execute(f"CREATE TABLE {DERIVED}.first_day_bg_art (stay_id BIGINT, pco2_min DOUBLE)")
    con.execute(
        f"CREATE TABLE {DERIVED}.first_day_vitalsign (stay_id BIGINT, temperature_min DOUBLE, "
        "temperature_max DOUBLE, heart_rate_max DOUBLE, resp_rate_max DOUBLE)"
    )
    con.execute(
        f"CREATE TABLE {DERIVED}.first_day_lab (stay_id BIGINT, wbc_min DOUBLE, "
        "wbc_max DOUBLE, bands_max DOUBLE)"
    )
    cases = {
        STAY + 1: (None, 8.0, None),  # only a normal wbc_max: the guard -> 0 (vendored: NULL)
        STAY + 2: (None, None, None),  # nothing measured -> NULL either way
        STAY + 3: (None, 15.0, None),  # elevated wbc_max -> 1
        STAY + 4: (3.0, 9.0, None),  # low wbc_min -> 1
        STAY + 5: (6.0, 9.0, 12.0),  # bands > 10 % -> 1
        STAY + 6: (6.0, 9.0, 4.0),  # everything normal -> 0
    }
    for i, stay in enumerate(cases):
        con.execute(f"INSERT INTO {ICU}.icustays VALUES (?, ?, ?)", [SUBJECT + i, HADM + i, stay])
        con.execute(
            f"INSERT INTO {DERIVED}.first_day_lab VALUES (?, ?, ?, ?)", [stay, *cases[stay]]
        )
    patched = {r["stay_id"]: r for r in _rows(con, "sirs", patched=True)}
    vendored = {r["stay_id"]: r for r in _rows(con, "sirs", patched=False)}
    assert patched[STAY + 1]["wbc_score"] == 0 and vendored[STAY + 1]["wbc_score"] is None
    assert patched[STAY + 1]["sirs"] == 0
    assert patched[STAY + 2]["wbc_score"] is None and vendored[STAY + 2]["wbc_score"] is None
    for stay in (STAY + 3, STAY + 4, STAY + 5):
        assert patched[stay]["wbc_score"] == 1 == vendored[stay]["wbc_score"]
    assert patched[STAY + 6]["wbc_score"] == 0 == vendored[STAY + 6]["wbc_score"]
    assert set(patched) == set(cases) and set(patched[STAY + 1]) == set(vendored[STAY + 1])


def _labevents(
    con: duckdb.DuckDBPyConnection, rows: list[tuple[int, int, str | None, float]]
) -> None:
    """``(specimen_id, itemid, valueuom, valuenum)`` rows under one synthetic admission."""
    con.execute(
        f"CREATE TABLE {HOSP}.labevents (labevent_id BIGINT, subject_id BIGINT, hadm_id BIGINT, "
        "specimen_id BIGINT, itemid INTEGER, charttime TIMESTAMP, valuenum DOUBLE, "
        "valueuom VARCHAR)"
    )
    for i, (specimen, itemid, unit, value) in enumerate(rows):
        con.execute(
            f"INSERT INTO {HOSP}.labevents VALUES "
            "(?, ?, ?, ?, ?, TIMESTAMP '2150-01-01 08:00:00', ?, ?)",
            [96_000_000 + i, SUBJECT, HADM, specimen, itemid, value, unit],
        )


def test_complete_blood_count_patch_keeps_mchc_in_g_dl_only() -> None:
    con = _con()
    _labevents(
        con,
        [
            (SPECIMEN + 1, 51249, "%", 33.0),  # MCHC mis-labelled '%' -> dropped
            (SPECIMEN + 1, 51222, "g/dL", 12.0),  # the specimen's hemoglobin stays
            (SPECIMEN + 2, 51249, "g/dL", 34.0),  # expected unit -> kept
            (SPECIMEN + 2, 51221, "%", 40.0),
            (SPECIMEN + 3, 51249, "%", 32.0),  # a '%'-only specimen disappears altogether
        ],
    )
    patched = {r["specimen_id"]: r for r in _rows(con, "complete_blood_count", patched=True)}
    vendored = {r["specimen_id"]: r for r in _rows(con, "complete_blood_count", patched=False)}
    assert patched[SPECIMEN + 1]["mchc"] is None and patched[SPECIMEN + 1]["hemoglobin"] == 12.0
    assert vendored[SPECIMEN + 1]["mchc"] == 33.0
    assert patched[SPECIMEN + 2]["mchc"] == 34.0 == vendored[SPECIMEN + 2]["mchc"]
    assert patched[SPECIMEN + 2]["hematocrit"] == 40.0
    assert SPECIMEN + 3 not in patched and vendored[SPECIMEN + 3]["mchc"] == 32.0
    assert set(patched[SPECIMEN + 1]) == set(vendored[SPECIMEN + 1]), "same columns"


def test_inflammation_patch_keeps_crp_in_mg_l_only() -> None:
    con = _con()
    _labevents(
        con,
        [
            (SPECIMEN + 1, 50889, None, 12.0),  # missing unit -> excluded
            (SPECIMEN + 2, 50889, "mg/L", 30.0),  # expected unit -> kept
            (SPECIMEN + 3, 50889, "mg/dL", 3.0),  # another unit -> excluded
        ],
    )
    patched = {r["specimen_id"]: r for r in _rows(con, "inflammation", patched=True)}
    vendored = {r["specimen_id"]: r for r in _rows(con, "inflammation", patched=False)}
    assert set(patched) == {SPECIMEN + 2} and patched[SPECIMEN + 2]["crp"] == 30.0
    assert set(vendored) == {SPECIMEN + 1, SPECIMEN + 2, SPECIMEN + 3}
    assert vendored[SPECIMEN + 1]["crp"] == 12.0 and vendored[SPECIMEN + 3]["crp"] == 3.0


def test_charlson_patch_excludes_c4a_merkel_cell() -> None:
    con = _con()
    con.execute(f"CREATE TABLE {HOSP}.admissions (subject_id BIGINT, hadm_id BIGINT)")
    con.execute(
        f"CREATE TABLE {HOSP}.diagnoses_icd (subject_id BIGINT, hadm_id BIGINT, seq_num INTEGER, "
        "icd_code VARCHAR, icd_version INTEGER)"
    )
    con.execute(f"CREATE TABLE {DERIVED}.age (subject_id BIGINT, hadm_id BIGINT, age DOUBLE)")
    cases = {
        HADM + 1: ("C4A0", 10),  # Merkel cell carcinoma: excluded by the patch (vendored: 1)
        HADM + 2: ("C4500", 10),  # mesothelioma, C45: still 1
        HADM + 3: ("C50919", 10),  # breast, C50: still 1
        HADM + 4: ("C7A00", 10),  # neuroendocrine extension: unmapped either way
        HADM + 5: ("C4390", 10),  # melanoma, C43: 1 (explicit set)
        HADM + 6: ("1749", 9),  # ICD-9 breast: 1
        HADM + 7: ("C7800", 10),  # secondary lung: metastatic_solid_tumor 1, malignant 0
    }
    for i, (hadm, (code, version)) in enumerate(cases.items()):
        con.execute(f"INSERT INTO {HOSP}.admissions VALUES (?, ?)", [SUBJECT + i, hadm])
        con.execute(
            f"INSERT INTO {HOSP}.diagnoses_icd VALUES (?, ?, 1, ?, ?)",
            [SUBJECT + i, hadm, code, version],
        )
        con.execute(f"INSERT INTO {DERIVED}.age VALUES (?, ?, 62.0)", [SUBJECT + i, hadm])
    patched = {r["hadm_id"]: r for r in _rows(con, "charlson", patched=True)}
    vendored = {r["hadm_id"]: r for r in _rows(con, "charlson", patched=False)}
    assert (
        patched[HADM + 1]["malignant_cancer"] == 0 and vendored[HADM + 1]["malignant_cancer"] == 1
    )
    assert patched[HADM + 1]["charlson_comorbidity_index"] == 2  # age 62 -> 2, nothing else
    assert vendored[HADM + 1]["charlson_comorbidity_index"] == 4  # age 2 + 2 * malignant
    for hadm in (HADM + 2, HADM + 3, HADM + 5, HADM + 6):
        assert patched[hadm]["malignant_cancer"] == 1 == vendored[hadm]["malignant_cancer"], hadm
    assert patched[HADM + 4]["malignant_cancer"] == 0 == vendored[HADM + 4]["malignant_cancer"]
    assert (
        patched[HADM + 7]["malignant_cancer"] == 0
        and patched[HADM + 7]["metastatic_solid_tumor"] == 1
    )
    assert patched[HADM + 7]["charlson_comorbidity_index"] == 8  # 2 + max(2 * 0, 6 * 1)


def test_apsiii_patch_scores_equidistant_inputs_per_the_paper() -> None:
    con = _con()
    con.execute(
        f"CREATE TABLE {ICU}.icustays (subject_id BIGINT, hadm_id BIGINT, stay_id BIGINT, "
        "intime TIMESTAMP, outtime TIMESTAMP)"
    )
    con.execute(f"CREATE TABLE {HOSP}.admissions (subject_id BIGINT, hadm_id BIGINT)")
    con.execute(f"CREATE TABLE {HOSP}.patients (subject_id BIGINT)")
    con.execute(
        f"CREATE TABLE {HOSP}.diagnoses_icd (subject_id BIGINT, hadm_id BIGINT, seq_num INTEGER, "
        "icd_code VARCHAR, icd_version INTEGER)"
    )
    con.execute(
        f"CREATE TABLE {DERIVED}.bg (subject_id BIGINT, hadm_id BIGINT, charttime TIMESTAMP, "
        "specimen VARCHAR, po2 DOUBLE, pco2 DOUBLE, ph DOUBLE, aado2 DOUBLE, fio2 DOUBLE, "
        "fio2_chartevents DOUBLE)"
    )
    con.execute(
        f"CREATE TABLE {DERIVED}.ventilation (stay_id BIGINT, starttime TIMESTAMP, "
        "endtime TIMESTAMP, ventilation_status VARCHAR)"
    )
    con.execute(
        f"CREATE TABLE {DERIVED}.first_day_urine_output (stay_id BIGINT, urineoutput DOUBLE)"
    )
    con.execute(
        f"CREATE TABLE {DERIVED}.first_day_gcs (stay_id BIGINT, gcs_min DOUBLE, gcs_motor DOUBLE, "
        "gcs_verbal DOUBLE, gcs_eyes DOUBLE, gcs_unable INTEGER)"
    )
    con.execute(
        f"CREATE TABLE {DERIVED}.first_day_vitalsign (stay_id BIGINT, heart_rate_min DOUBLE, "
        "heart_rate_max DOUBLE, mbp_min DOUBLE, mbp_max DOUBLE, temperature_min DOUBLE, "
        "temperature_max DOUBLE, resp_rate_min DOUBLE, resp_rate_max DOUBLE, glucose_min DOUBLE, "
        "glucose_max DOUBLE)"
    )
    con.execute(
        f"CREATE TABLE {DERIVED}.first_day_lab (stay_id BIGINT, creatinine_min DOUBLE, "
        "creatinine_max DOUBLE, hematocrit_min DOUBLE, hematocrit_max DOUBLE, wbc_min DOUBLE, "
        "wbc_max DOUBLE, bun_min DOUBLE, bun_max DOUBLE, sodium_min DOUBLE, sodium_max DOUBLE, "
        "albumin_min DOUBLE, albumin_max DOUBLE, bilirubin_total_min DOUBLE, "
        "bilirubin_total_max DOUBLE, glucose_min DOUBLE, glucose_max DOUBLE)"
    )
    for i in (1, 2):
        con.execute(
            f"INSERT INTO {ICU}.icustays VALUES (?, ?, ?, TIMESTAMP '2150-01-01 08:00:00', "
            "TIMESTAMP '2150-01-03 08:00:00')",
            [SUBJECT + i, HADM + i, STAY + i],
        )
        con.execute(f"INSERT INTO {HOSP}.admissions VALUES (?, ?)", [SUBJECT + i, HADM + i])
        con.execute(f"INSERT INTO {HOSP}.patients VALUES (?)", [SUBJECT + i])
    # stay 1: every paired input equidistant from the APS III midpoint, so the tie arms decide
    con.execute(
        f"INSERT INTO {DERIVED}.first_day_vitalsign VALUES "
        "(?, 75, 75, 90, 90, 38, 38, 14, 24, NULL, NULL)",
        [STAY + 1],
    )
    con.execute(
        f"INSERT INTO {DERIVED}.first_day_lab VALUES (?, NULL, NULL, 41, 50, 2, 21, NULL, NULL, "
        "136, 155, 2.5, 4.5, NULL, NULL, 60, 200)",
        [STAY + 1],
    )
    patched = {r["stay_id"]: r for r in _rows(con, "apsiii", patched=True)}
    vendored = {r["stay_id"]: r for r in _rows(con, "apsiii", patched=False)}
    tie = patched[STAY + 1]
    # Knaus et al. (1991): the value furthest from normal scores; equidistant -> the larger score
    assert tie["hr_score"] == 0 and tie["mbp_score"] == 0 and tie["temp_score"] == 0
    assert tie["resp_rate_score"] == 0  # 14 and 24 both score 0
    assert tie["hematocrit_score"] == 3  # 41 -> 0, 50 -> 3
    assert tie["wbc_score"] == 5  # 2 -> 5, 21 -> 1
    assert tie["sodium_score"] == 4  # 136 -> 0, 155 -> 4
    assert tie["albumin_score"] == 4  # 2.5 -> 0, 4.5 -> 4
    assert tie["glucose_score"] == 3  # 60 -> 0, 200 -> 3 (the PR's own example)
    assert tie["apsiii"] == 19 and tie["creatinine_score"] is None and tie["gcs_score"] is None
    empty = patched[STAY + 2]
    assert empty["apsiii"] == 0 and empty["hr_score"] is None and empty["glucose_score"] is None
    # the port corrects the SQL's intent; on paired min/max inputs (both present or both
    # absent, as first_day_* always yields) the vendored arms produced the same rows
    assert vendored == patched


# ---------------------------------------------------------------------------
# 4. Rebuild selection, pins, docs, CLI, hygiene
# ---------------------------------------------------------------------------


def test_rebuild_steps_cover_the_patched_concepts_and_their_dependents(
    registry: PatchRegistry,
) -> None:
    inv = load_inventory()
    closure = inv.dependents_of(registry.concepts)
    # an independent recursive closure over the inventory graph agrees
    readers: dict[str, set[str]] = {c.name: set() for c in inv.concepts}
    for c in inv.concepts:
        for dep in c.depends_on:
            readers[dep].add(c.name)
    expected = set(registry.concepts)
    frontier = list(expected)
    while frontier:
        expected |= (new := readers[frontier.pop()] - expected)
        frontier.extend(new)
    assert set(closure) == expected and len(closure) == len(set(closure))
    position = {c.name: i for i, c in enumerate(inv.concepts)}
    assert [position[n] for n in closure] == sorted(position[n] for n in closure), "execution order"
    assert {"first_day_lab", "sofa", "sepsis3"} <= set(closure), (
        "cbc feeds first_day_lab -> sofa -> sepsis3"
    )
    steps = rebuild_steps(inv, registry)
    assert steps[-2:] == [VERSIONS_STEP, CATALOG_STEP]
    assert steps[:-2] == [inv.concept(n).step_name for n in closure]
    dag = load_dag()
    # a valid --select: every name resolves, the runner orders it topologically (its own
    # tie-break), the versions table and the catalog close the build
    ordered = [s.name for s in dag.ordered(select=steps, tier="fixture")]
    assert set(ordered) == set(steps) and ordered[-2:] == steps[-2:]
    depth = {name: i for i, name in enumerate(ordered)}
    for name in ordered[:-2]:
        for dep in dag.step(name).depends_on:
            assert dep not in depth or depth[dep] < depth[name], (dep, name)
    without = rebuild_steps(inv, registry, with_dependents=False)
    assert set(without[:-2]) == {inv.concept(n).step_name for n in registry.concepts}
    with pytest.raises(InventoryError, match="unknown"):
        inv.dependents_of(["ghost"])
    with pytest.raises(PatchError, match="unknown"):
        rebuild_steps(
            inv,
            PatchRegistry(patches=(registry.patches[0].model_copy(update={"concept": "ghost"}),)),
        )


def test_pins_carry_the_patch_map_and_refresh_records_before_after(
    fixture_lake_settings: Settings, tmp_path: Path, registry: PatchRegistry
) -> None:
    path = tmp_path / "pins.json"
    pins, diffs, created = pins_mod.write_or_compare("fixture", path, fixture_lake_settings)
    assert created and diffs == [] and pins["patches"] == registry_summary(registry)
    # an older pin file (pre-patch counts, no patch map): a plain comparison reports, a
    # refresh rewrites and reports the same before/after
    old = pins_mod.read_pins(path)
    del old["patches"]
    old["counts"]["age"] = 99 if old["counts"]["age"] != 99 else 98
    pins_mod.write_pins(old, path)
    _, diffs, created = pins_mod.write_or_compare("fixture", path, fixture_lake_settings)
    assert not created and pins_mod.read_pins(path) == old, "a plain comparison never writes"
    assert any(d.startswith("patches:") for d in diffs) and any(
        d.startswith("counts.age:") for d in diffs
    )
    _, refreshed, created = pins_mod.write_or_compare(
        "fixture", path, fixture_lake_settings, refresh=True
    )
    assert not created and refreshed == diffs
    assert pins_mod.read_pins(path)["patches"] == registry_summary(registry)
    _, after, _ = pins_mod.write_or_compare("fixture", path, fixture_lake_settings)
    assert after == []


def test_demo_pins_file_carries_the_patch_map(registry: PatchRegistry) -> None:
    pins = pins_mod.read_pins(pins_mod.demo_pins_path())
    assert pins["patches"] == registry_summary(registry)
    assert pins["upstream_commit"] == vendor_info().sha


@pytest.mark.demo
def test_demo_pins_match_a_fresh_computation_with_patches(registry: PatchRegistry) -> None:
    settings = config.load_settings()
    pins = pins_mod.compute_pins("demo", settings)
    assert pins["patches"] == registry_summary(registry)
    assert pins_mod.compare_pins(pins, pins_mod.read_pins(pins_mod.demo_pins_path())) == []


def test_docs_tables_in_sync(registry: PatchRegistry) -> None:
    from mimicwarehouse.config import workspace_root

    path = workspace_root() / "docs" / "resources" / "concepts.md"
    text = path.read_text(encoding="utf-8")
    inv = load_inventory()
    table = render_inventory_table(inv)
    assert table in text and "| DuckDB 1.5.5 | patch |" in table
    assert table.count(f"| {DUCKDB_STATUS_OK} |") == len(inv.concepts)
    assert render_patch_table(registry) in text, (
        "regenerate: python -m mimicwarehouse.concepts.patching --table"
    )
    for p in registry.patches:
        assert f"`{p.patch_id}`" in text and p.upstream_ref in text
        assert f"| {DUCKDB_STATUS_OK} | `{p.patch_id}` |" in table
    assert "ported-unmerged" in text and "## Deviations" in text
    assert not BAND_TOKEN.search(text)
    empty = render_patch_table(PatchRegistry())
    assert "*(none)*" in empty and empty.startswith("| patch id |")


def test_cli_entry_points(registry: PatchRegistry) -> None:
    check = helpers.fresh_interpreter(["-m", "mimicwarehouse.concepts.patching", "--check"])
    assert check.returncode == 0, check.stdout + check.stderr
    assert check.stdout.strip().endswith(f"{len(registry.patches)} patch(es); ok")
    listed = helpers.fresh_interpreter(["-m", "mimicwarehouse.concepts.patching", "--select-list"])
    assert listed.returncode == 0, listed.stdout + listed.stderr
    select = listed.stdout.splitlines()[0]
    assert select == ",".join(rebuild_steps(load_inventory(), registry))
    assert select.endswith(f",{VERSIONS_STEP},{CATALOG_STEP}")
    table = helpers.fresh_interpreter(["-m", "mimicwarehouse.concepts.patching", "--table"])
    assert table.returncode == 0 and render_patch_table(registry) in table.stdout
    assert pins_mod.main(["--tier", "nope"]) == 2 and pins_mod.main(["--tier", "fixture"]) == 2


def test_pins_cli_round_trip(
    fixture_lake_settings: Settings,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "cli-pins.json"
    # the CLI resolves settings through the name pins.py imported (never the real data root)
    monkeypatch.setattr(pins_mod, "get_settings", lambda: fixture_lake_settings)
    assert pins_mod.main(["--tier", "fixture", "--path", str(path)]) == 0
    out = capsys.readouterr().out
    assert "written" in out and "0 difference(s)" in out
    doc = pins_mod.read_pins(path)
    doc["counts"]["age"] = "<11" if doc["counts"]["age"] != "<11" else 0
    pins_mod.write_pins(doc, path)
    assert pins_mod.main(["--tier", "fixture", "--path", str(path)]) == 1
    assert "counts.age:" in capsys.readouterr().out
    assert pins_mod.main(["--tier", "fixture", "--path", str(path), "--refresh"]) == 0
    assert "refreshed" in capsys.readouterr().out
    assert pins_mod.main(["--tier", "fixture", "--path", str(path)]) == 0
    assert "compared" in capsys.readouterr().out


def test_patch_files_and_registry_hygiene(registry: PatchRegistry) -> None:
    root = patching.patches_root()
    files = sorted(root.glob("*.sql"))
    assert {f.stem for f in files} == set(registry.concepts)
    for f in (*files, patching.registry_path()):
        raw = f.read_bytes()
        assert b"\r" not in raw and raw.endswith(b"\n"), f.name
        text = raw.decode("utf-8")
        assert text.isascii(), f.name
        assert not BAND_TOKEN.search(text), f.name
    doc = yaml.safe_load(patching.registry_path().read_text(encoding="utf-8"))
    assert doc["version"] == patching.REGISTRY_VERSION
    assert [p["patch_id"] for p in doc["patches"]] == [p.patch_id for p in registry.patches]
    assert json.dumps(registry_summary(registry))  # plain types only


def test_import_budget() -> None:
    helpers.assert_import_budget(
        lazy=("mimicwarehouse.concepts.patching", "mimicwarehouse.concepts.pins")
    )
    helpers.assert_import_budget("mimicwarehouse.concepts.patching")


# ---------------------------------------------------------------------------
# 5. Dev / full tiers: meta.concept_versions names the patched concepts
# ---------------------------------------------------------------------------


def _assert_versions_carry_patches(tier: str, registry: PatchRegistry) -> None:
    from mimicwarehouse.safe import safe_query

    settings = config.load_settings()
    listed = safe_query(
        "SELECT concept, patch_id, sql_sha256 FROM meta.concept_versions ORDER BY 1",
        tier=tier,
        actor="test_ep38",
        settings=settings,
    )
    got = {
        c: (p, s)
        for c, p, s in zip(
            listed.df["concept"].to_list(),
            listed.df["patch_id"].to_list(),
            listed.df["sql_sha256"].to_list(),
            strict=True,
        )
    }
    patched = {c: v for c, v in got.items() if v[0] is not None}
    remedy = (
        f"rebuild the patched set: mwh build --tier {tier} --select "
        "$(python -m mimicwarehouse.concepts.patching --select-list) --force"
    )
    assert {c: p for c, (p, _s) in patched.items()} == registry_summary(registry), remedy
    assert {c: s for c, (_p, s) in patched.items()} == {
        p.concept: p.sql_sha256 for p in registry.patches
    }
    # the brief's `count(*) WHERE patch_id IS NOT NULL` is below k = 11, so the released
    # complement stands in for it (GOVERNANCE section 5)
    complement = safe_query(
        "SELECT count(*) AS n FROM meta.concept_versions WHERE patch_id IS NULL",
        tier=tier,
        actor="test_ep38",
        settings=settings,
    )
    assert int(complement.df["n"][0]) == len(load_inventory().concepts) - len(registry.patches)
    print(f"{tier}: {len(registry.patches)} patched concept(s) in meta.concept_versions")


@pytest.mark.tier("dev")
def test_dev_concept_versions_carry_patch_ids(dev_catalog: Path, registry: PatchRegistry) -> None:
    _assert_versions_carry_patches("dev", registry)


@pytest.mark.tier("full")
def test_full_concept_versions_carry_patch_ids(full_catalog: Path, registry: PatchRegistry) -> None:
    _assert_versions_carry_patches("full", registry)
