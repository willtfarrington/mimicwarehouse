"""EP-42 — phenotypes: sepsis-3 + KDIGO AKI stage (+ the explicit-sepsis companion).

Fixture tier (default): the three packaged definitions load, are locked and hygienic,
pin their code set (sepsis_explicit) and their vendored concepts (sepsis3, kdigo_stages —
the executed-SQL sha256 from the inventory / patch registry); the schema resolves
``$parameter`` placeholders, validates the concept ``window`` / ``evidence`` knobs and
refuses the crafted violations; the versioning rules — editing ``window_hours`` without a
bump is refused (library + every command, exit 3), a bump to ``kdigo_aki@1.1.0`` yields a
new hash and coexists, a moved concept SQL hash (simulated through the ``concept_pin``
seam) refuses the locked pairs; the compiler emits identical SQL matching the committed
golden files; crafted synthetic ``sepsis3`` / ``kdigo_stages`` frames (ids >= 90 000 000,
in an in-memory DuckDB) produce the expected flags, onsets, evidence columns and the
stage-2-only-after-7-days case (negative by default, positive under ``window_hours=336``),
plus the icustay ``_hadm`` companion and the 2x2 over the crafted admissions; the DAG spec
orders the concept steps first and the CLI launcher wiring; a runner-built fixture lake
(the phenotypes' concept ancestors pulled in with ``with_deps``) carries the three tables,
views, companions and ``meta.phenotype_versions`` with ``concept_refs``, agrees with an
independent SQL evaluation over the concept tables, feeds ``summarize`` /
``distribution`` / ``agreement`` / ``prevalence_report`` through ``safe_query``, writes
``phenotype_prevalence.md`` into an analysis run, refuses a concept-pin mismatch, and
keeps ``kdigo_aki@1.0.0`` / ``@1.1.0`` side by side; the docs page is in sync.
``tier("dev")`` / ``tier("full")``: the three built on the real catalogs, aggregates
only.

Everything asserted or printed is definition text, SQL, synthetic rows and aggregate
counts — never a patient-level row.
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import duckdb
import pytest

import helpers
from mimicwarehouse import config
from mimicwarehouse.cli import app
from mimicwarehouse.codesets import registry as codesets_registry
from mimicwarehouse.dag import runner as runner_mod
from mimicwarehouse.dag.spec import load_dag
from mimicwarehouse.phenotypes import compiler as compiler_mod
from mimicwarehouse.phenotypes import registry as registry_mod
from mimicwarehouse.phenotypes import runner as phen_runner
from mimicwarehouse.phenotypes.spec import (
    PhenotypeError,
    PhenotypeFrozenError,
    phenotype_from_text,
)

if TYPE_CHECKING:
    from mimicwarehouse.config import Settings
    from mimicwarehouse.phenotypes.registry import Registry
    from mimicwarehouse.schema.contract import Contract

pytestmark = pytest.mark.ep_42

HOSP = "mimiciv_hosp"
ICU = "mimiciv_icu"
DERIVED = "mimiciv_derived"
BAND_TOKEN = re.compile(r"(?<![\w.])[123]\d{7}(?![\w.])")
GOLDEN = helpers.WORKSPACE / "tests" / "ep" / "golden"
REFS: tuple[str, ...] = ("sepsis3@1.0.0", "kdigo_aki@1.0.0", "sepsis_explicit@1.0.0")
CONCEPT_STEPS: tuple[str, ...] = ("concept.organfailure.kdigo_stages", "concept.sepsis.sepsis3")
T0 = datetime(2150, 1, 1, 8, 0)
HOUR = timedelta(hours=1)
S = 90_000_000
H = 91_000_000
ST = 92_000_000


@pytest.fixture(scope="module")
def registry() -> Registry:
    return registry_mod.load_registry()


def _drop_progress_handlers() -> None:
    import logging

    logger = logging.getLogger("mimicwarehouse")
    for handler in list(logger.handlers):
        logger.removeHandler(handler)


def _scalar(con: duckdb.DuckDBPyConnection, sql: str) -> Any:
    row = con.execute(sql).fetchone()
    assert row is not None
    return row[0]


def _text(entry: registry_mod.Entry) -> str:
    return entry.path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. The packaged definitions: locked, hygienic, code set + concepts pinned
# ---------------------------------------------------------------------------


def test_packaged_definitions_locked_and_pinned(registry: Registry) -> None:
    assert set(REFS) <= set(registry.refs())
    lock = registry_mod.load_lock(registry_mod.packaged_defs_dir())
    for ref in REFS:
        entry = registry.get(ref)
        p = entry.phenotype
        assert entry.locked and entry.packaged and entry.display_path == f"defs/{p.id}.yaml"
        assert lock.phenotypes[ref].def_hash == entry.def_hash
        assert lock.phenotypes[ref].grain == p.grain
        assert re.fullmatch(r"[0-9a-f]{64}", entry.def_hash)
        assert len(p.what_it_does_not_claim) >= 3 and p.citations
        assert all(c.startswith("https://doi.org/") for c in p.citations)
        raw = entry.path.read_bytes()
        assert b"\r" not in raw and raw.endswith(b"\n") and raw.decode("utf-8").isascii()
        assert not BAND_TOKEN.search(raw.decode("utf-8"))
    sepsis3 = registry.get("sepsis3@1.0.0")
    kdigo = registry.get("kdigo_aki@1.0.0")
    explicit = registry.get("sepsis_explicit@1.0.0")
    # grains, criteria, parameters, evidence
    assert sepsis3.phenotype.grain == "icustay" and kdigo.phenotype.grain == "icustay"
    assert explicit.phenotype.grain == "hadm"
    assert sepsis3.phenotype.criteria.render() == "s3"
    assert kdigo.phenotype.criteria.render() == "aki"
    assert explicit.phenotype.criteria.render() == "dx"
    assert kdigo.phenotype.parameters == {"min_stage": 1, "window_hours": 168}
    aki = kdigo.phenotype.criteria_leaves[0].payload
    assert aki.value == 1 and aki.window is not None and aki.window.to_hours == 168
    assert aki.window.from_hours == 0 and aki.time_column == "charttime"
    assert [e.name for e in kdigo.phenotype.evidence_columns] == [
        "max_stage_in_window",
        "stage_at_onset",
    ]
    assert kdigo.phenotype.evidence_columns[0].levels == (0, 1, 2, 3)
    assert kdigo.phenotype.evidence_columns[0].default == 0
    assert [e.name for e in sepsis3.phenotype.evidence_columns] == ["sofa_time", "sofa_score"]
    s3 = sepsis3.phenotype.criteria_leaves[0].payload
    assert s3.value is True and s3.time_column == "suspected_infection_time"
    # code-set pin (explicit) and concept pins (sepsis3, kdigo)
    assert explicit.resolved == {
        "sepsis_explicit@1.0.0": registry.codesets.get("sepsis_explicit@1.0.0").codeset.def_hash
    }
    assert not explicit.concepts and not sepsis3.resolved and not kdigo.resolved
    assert set(sepsis3.concepts) == {f"{DERIVED}.sepsis3"}
    assert set(kdigo.concepts) == {f"{DERIVED}.kdigo_stages"}
    from mimicwarehouse.concepts import patching
    from mimicwarehouse.concepts.inventory import load_inventory

    inventory = load_inventory()
    patches = patching.load_registry()
    for entry, name in ((sepsis3, "sepsis3"), (kdigo, "kdigo_stages")):
        pin = entry.concepts[f"{DERIVED}.{name}"]
        concept = inventory.concept(name)
        assert pin.step == concept.step_name and pin.step in CONCEPT_STEPS
        assert pin.vendored_sha256 == concept.sql_sha256
        assert pin.upstream_commit == concept.upstream_commit
        patch = patches.for_concept(name)
        if patch is None:
            assert pin.patch_id is None and pin.executed_sha256 == concept.sql_sha256
        else:  # pragma: no cover - neither concept is patched at the EP-38 pin
            assert pin.patch_id == patch.patch_id and pin.executed_sha256 == patch.sql_sha256
        assert entry.concept_hashes == {f"{DERIVED}.{name}": pin.executed_sha256}
    # the hash carries the pins (the same YAML against other concept hashes differs)
    assert kdigo.phenotype.def_hash({}, kdigo.concept_hashes) == kdigo.def_hash
    assert kdigo.phenotype.def_hash({}, {f"{DERIVED}.kdigo_stages": "0" * 64}) != kdigo.def_hash
    assert phen_runner.concept_steps(registry) == CONCEPT_STEPS
    runner = helpers.cli_runner()
    check = runner.invoke(app, ["phenotype", "lock", "--check"])
    assert check.exit_code == 0 and "0 unlocked" in check.stdout, check.output
    listing = runner.invoke(app, ["phenotype", "list", "--json"])
    assert listing.exit_code == 0, listing.output
    payload = {x["ref"]: x for x in json.loads(listing.stdout)["phenotypes"]}
    assert set(REFS) <= set(payload) and all(payload[r]["locked"] for r in REFS)
    assert payload["kdigo_aki@1.0.0"]["parameters"] == {"min_stage": 1, "window_hours": 168}
    assert payload["kdigo_aki@1.0.0"]["evidence"] == ["max_stage_in_window", "stage_at_onset"]
    concepts = payload["sepsis3@1.0.0"]["concepts"]
    assert concepts[f"{DERIVED}.sepsis3"]["step"] == "concept.sepsis.sepsis3"
    assert (
        concepts[f"{DERIVED}.sepsis3"]["executed_sha256"]
        == sepsis3.concept_hashes[f"{DERIVED}.sepsis3"]
    )


# ---------------------------------------------------------------------------
# 2. Schema: parameters, window, evidence — resolution, hashing, refusals
# ---------------------------------------------------------------------------

_CRAFTED = """\
id: crafted_aki
version: "1.0.0"
name: crafted
grain: icustay
parameters: {min_stage: 1, window_hours: 168}
criteria:
  id: aki
  concept:
    table: mimiciv_derived.kdigo_stages
    column: aki_stage_smoothed
    op: ">="
    value: $min_stage
    key: stay_id
    time_column: charttime
    window: {from_hours: 0, to_hours: $window_hours}
    evidence:
      - {name: max_stage, column: aki_stage_smoothed, agg: max, default: 0, levels: [0, 1, 2, 3]}
      - {name: stage_at_onset, column: aki_stage_smoothed, agg: first}
onset: earliest
provenance: {source: hand, accessed: "2026-09-06"}
"""


def _refused(text: str, match: str) -> None:
    with pytest.raises(PhenotypeError, match=match):
        phenotype_from_text(text)


def test_schema_parameters_window_evidence_and_refusals() -> None:
    p = phenotype_from_text(_CRAFTED)
    leaf = p.criteria_leaves[0].payload
    assert p.parameters == {"min_stage": 1, "window_hours": 168}
    assert leaf.value == 1 and leaf.window is not None and leaf.window.to_hours == 168.0
    assert [e.name for e in p.evidence_columns] == ["max_stage", "stage_at_onset"]
    assert p.evidence_columns[0].canonical() == {
        "name": "max_stage",
        "column": "aki_stage_smoothed",
        "agg": "max",
        "default": 0,
        "levels": [0, 1, 2, 3],
    }
    canonical = p.canonical({})
    assert canonical["parameters"] == {"min_stage": 1, "window_hours": 168}
    assert "concepts" not in canonical, "no pins given -> no concepts key"
    pinned = p.canonical({}, {"mimiciv_derived.kdigo_stages": "a" * 64})
    assert pinned["concepts"] == {"mimiciv_derived.kdigo_stages": "a" * 64}
    assert p.canonical({}, {"mimiciv_derived.other": "b" * 64}) == canonical, (
        "a pin for a table the leaves do not read is ignored"
    )
    # overrides: a variant hashes differently; unknown names refused
    variant = phenotype_from_text(_CRAFTED, parameters={"window_hours": 336})
    assert variant.parameters["window_hours"] == 336
    assert variant.criteria_leaves[0].payload.window is not None
    assert variant.criteria_leaves[0].payload.window.to_hours == 336
    assert variant.def_hash({}) != p.def_hash({})
    with pytest.raises(PhenotypeError, match="not declared"):
        phenotype_from_text(_CRAFTED, parameters={"nope": 1})
    # editing a parameter in the text moves the hash exactly like editing the leaf
    edited = phenotype_from_text(_CRAFTED.replace("window_hours: 168", "window_hours: 336"))
    assert edited.def_hash({}) == variant.def_hash({})
    assert edited.def_hash({}) != p.def_hash({})
    assert phenotype_from_text(_CRAFTED.replace("min_stage: 1", "min_stage: 2")).def_hash(
        {}
    ) != p.def_hash({})
    # refusals
    _refused(_CRAFTED.replace("value: $min_stage", "value: $nope"), "unknown parameter")
    _refused(
        _CRAFTED.replace("parameters: {min_stage: 1, window_hours: 168}", "parameters: [1]"),
        "mapping",
    )
    _refused(
        _CRAFTED.replace("{min_stage: 1, window_hours: 168}", "{Min: 1, window_hours: 168}"),
        "slug",
    )
    _refused(
        _CRAFTED.replace(
            "{min_stage: 1, window_hours: 168}", "{min_stage: [1], window_hours: 168}"
        ),
        "scalar",
    )
    _refused(_CRAFTED.replace("    time_column: charttime\n", ""), "needs a time_column")
    _refused(_CRAFTED.replace("key: stay_id", "key: subject_id"), "anchored key")
    _refused(
        _CRAFTED.replace(
            "window: {from_hours: 0, to_hours: $window_hours}",
            "window: {from_hours: 200, to_hours: 100}",
        ),
        "must exceed",
    )
    _refused(_CRAFTED.replace("name: stage_at_onset", "name: max_stage"), "repeat")
    _refused(_CRAFTED.replace("name: stage_at_onset", "name: stay_id"), "reserved")
    _refused(_CRAFTED.replace("name: stage_at_onset", "name: flag"), "reserved")
    _refused(_CRAFTED.replace("name: stage_at_onset", "name: foo_id"), "reserved")
    _refused(_CRAFTED.replace("agg: max", "agg: median"), "agg")
    _refused(_CRAFTED.replace("levels: [0, 1, 2, 3]", "levels: [0, 0]"), "repeat a value")
    _refused(_CRAFTED.replace("levels: [0, 1, 2, 3]", "levels: 3"), "list")
    _refused(_CRAFTED.replace("default: 0", "default: [0]"), "scalar")
    # evidence on a temporal operand is refused; two leaves cannot share an evidence name
    temporal = (
        'id: t\nversion: "1.0.0"\nname: t\ngrain: icustay\ncriteria:\n  all:\n'
        "  - id: rel\n    temporal:\n      relation: before\n"
        "      a: {id: a, concept: {table: mimiciv_derived.sepsis3, column: sepsis3, op: '=', "
        "value: true, time_column: suspected_infection_time, evidence: [{name: score, "
        "column: sofa_score}]}}\n"
        "      b: {id: b, microbiology: {spec_itemids: [70012]}}\n"
        'provenance: {source: hand, accessed: "2026-09-06"}\n'
    )
    _refused(temporal, "temporal operand")
    two = (
        'id: two\nversion: "1.0.0"\nname: two\ngrain: icustay\ncriteria:\n  any:\n'
        "  - {id: a, concept: {table: mimiciv_derived.sepsis3, column: sepsis3, op: '=', "
        "value: true, evidence: [{name: score, column: sofa_score}]}}\n"
        "  - {id: b, concept: {table: mimiciv_derived.sepsis3, column: sepsis3, op: '=', "
        "value: false, evidence: [{name: score, column: sofa_score}]}}\n"
        'provenance: {source: hand, accessed: "2026-09-06"}\n'
    )
    _refused(two, "repeat across leaves")
    # a definition without parameters / concepts keeps the EP-41 canonical shape
    plain = phenotype_from_text(
        'id: plain\nversion: "1.0.0"\nname: plain\ngrain: hadm\n'
        "criteria: {id: dx, diagnosis: {codeset: sepsis_explicit@1.0.0}}\n"
        'provenance: {source: hand, accessed: "2026-09-06"}\n'
    )
    assert set(plain.canonical({"sepsis_explicit@1.0.0": "c" * 64})) == {
        "grain",
        "criteria",
        "onset",
        "references",
    }


# ---------------------------------------------------------------------------
# 3. Versioning: frozen refusals, the bump, the moved concept hash
# ---------------------------------------------------------------------------


def _copy_defs(tmp_path: Path) -> Path:
    target = tmp_path / "defs"
    shutil.copytree(registry_mod.packaged_defs_dir(), target)
    return target


def test_frozen_parameter_edit_refused_bump_coexists_and_concept_hash_pins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    defs = _copy_defs(tmp_path)
    path = defs / "kdigo_aki.yaml"
    original = path.read_text(encoding="utf-8")
    edited = original.replace("window_hours: 168", "window_hours: 336")
    assert edited != original
    path.write_text(edited, encoding="utf-8", newline="\n")
    codesets = codesets_registry.load_registry()
    with pytest.raises(PhenotypeFrozenError, match=re.escape("kdigo_aki@1.0.0 is frozen")):
        registry_mod.load_dir(defs, codesets)
    with pytest.raises(PhenotypeFrozenError, match="version bump"):
        registry_mod.lock_dir(defs, codesets=codesets)
    monkeypatch.setattr(registry_mod, "packaged_defs_dir", lambda: defs)
    runner = helpers.cli_runner()
    root = str(tmp_path / "root")
    try:
        for args in (
            ["phenotype", "list"],
            ["--data-root", root, "phenotype", "compile", "kdigo_aki@1.0.0", "--tier", "fixture"],
            ["phenotype", "validate", "kdigo_aki@1.0.0"],
            ["phenotype", "show", "kdigo_aki@1.0.0"],
            ["--data-root", root, "phenotype", "summary", "kdigo_aki@1.0.0", "--tier", "fixture"],
            ["phenotype", "lock"],
        ):
            result = runner.invoke(app, args)
            assert result.exit_code == 3, (args, result.output)
            flat = " ".join(result.stderr.split())  # rich wraps long messages
            assert "kdigo_aki@1.0.0 is frozen" in flat and "version bump" in flat
        assert not (tmp_path / "root" / "lake").exists(), "nothing was built"
    finally:
        config.configure()
        _drop_progress_handlers()
    # the bump: kdigo_aki@1.1.0 (336 h) loads unlocked beside the locked 1.0.0, then locks
    bumped = edited.replace('version: "1.0.0"', 'version: "1.1.0"')
    (defs / "kdigo_aki_1_1.yaml").write_text(bumped, encoding="utf-8", newline="\n")
    path.write_text(original, encoding="utf-8", newline="\n")
    entries = {e.ref: e for e in registry_mod.load_dir(defs, codesets)}
    assert entries["kdigo_aki@1.0.0"].locked and not entries["kdigo_aki@1.1.0"].locked
    assert entries["kdigo_aki@1.1.0"].def_hash != entries["kdigo_aki@1.0.0"].def_hash
    assert entries["kdigo_aki@1.1.0"].phenotype.parameters["window_hours"] == 336
    assert entries["kdigo_aki@1.1.0"].concepts == entries["kdigo_aki@1.0.0"].concepts
    result = registry_mod.lock_dir(defs, codesets=codesets)
    assert result.added == ("kdigo_aki@1.1.0",) and "kdigo_aki@1.0.0" in result.unchanged
    assert all(e.locked for e in registry_mod.load_dir(defs, codesets))
    lock_text = registry_mod.lock_path(defs).read_text(encoding="utf-8")
    assert "kdigo_aki@1.1.0" in lock_text and lock_text.isascii()
    # the concept pin: a moved executed-SQL hash (a re-vendor or a new patch) refuses the
    # locked pairs that read the concept, exactly like an edited definition
    monkeypatch.undo()
    real_pin = registry_mod.concept_pin

    def moved(name: str) -> registry_mod.ConceptPin | None:
        pin = real_pin(name)
        if pin is None or name != "kdigo_stages":
            return pin
        return registry_mod.ConceptPin(
            table=pin.table,
            name=pin.name,
            step=pin.step,
            executed_sha256="f" * 64,
            vendored_sha256=pin.vendored_sha256,
            patch_id="kdigo_stages-crafted-patch",
            upstream_commit=pin.upstream_commit,
        )

    monkeypatch.setattr(registry_mod, "concept_pin", moved)
    with pytest.raises(PhenotypeFrozenError, match=re.escape("kdigo_aki@1.0.0 is frozen")):
        registry_mod.load_registry()
    frozen = runner.invoke(app, ["phenotype", "list"])
    assert frozen.exit_code == 3 and "refused" in frozen.stderr
    # sepsis3 (another concept) is untouched by that move
    monkeypatch.undo()
    reg = registry_mod.load_registry()
    assert reg.get("sepsis3@1.0.0").locked and reg.get("kdigo_aki@1.0.0").locked
    # an unknown concept table is read unpinned, with a validation warning
    unpinned = phenotype_from_text(
        _CRAFTED.replace("mimiciv_derived.kdigo_stages", "mimiciv_derived.crafted_stages")
    )
    assert registry_mod.resolve_concepts(unpinned) == {}
    entry = registry_mod.Entry(unpinned, {}, "0" * 64, Path("x.yaml"), False, False)
    validation = registry_mod.validate(entry, codesets)
    assert validation.ok and any("not pinned" in w for w in validation.warnings)
    assert validation.to_dict()["parameters"] == {"min_stage": 1, "window_hours": 168}


# ---------------------------------------------------------------------------
# 4. The compiler: golden files, determinism, the window / evidence SQL
# ---------------------------------------------------------------------------


def test_compiler_golden_files_and_shapes(registry: Registry) -> None:
    for ref in REFS:
        entry = registry.get(ref)
        first = compiler_mod.compile_phenotype(
            entry.phenotype,
            registry.codesets,
            resolved=entry.resolved,
            concepts=entry.concept_hashes,
        )
        second = compiler_mod.compile_phenotype(
            entry.phenotype,
            registry.codesets,
            resolved=entry.resolved,
            concepts=entry.concept_hashes,
        )
        assert first.sql == second.sql
        golden = GOLDEN / f"{ref}.sql"
        assert golden.is_file(), f"the golden SQL file for {ref} is committed"
        assert golden.read_text(encoding="utf-8") == first.sql.rstrip("\n") + "\n", (
            f"the compiled SQL drifted from tests/ep/golden/{ref}.sql (a deliberate compiler "
            "change regenerates the file; a definition change needs a version bump)"
        )
        assert not BAND_TOKEN.search(first.sql) and first.sql.isascii()
    sepsis3 = registry.get("sepsis3@1.0.0")
    s3 = compiler_mod.compile_phenotype(
        sepsis3.phenotype, registry.codesets, concepts=sepsis3.concept_hashes
    )
    assert s3.columns == (
        "subject_id",
        "hadm_id",
        "stay_id",
        "flag",
        "onset_time",
        "evidence_json",
        "sofa_time",
        "sofa_score",
    )
    assert s3.evidence_columns == ("sofa_time", "sofa_score")
    assert s3.sources == (f"{ICU}.icustays", f"{DERIVED}.sepsis3")
    assert "c.sepsis3 = TRUE" in s3.sql and "c.suspected_infection_time AS event_time" in s3.sql
    assert "arg_min(ev_sofa_score, event_time)" in s3.sql
    assert f"-- concepts: {DERIVED}.sepsis3=" in s3.sql and "date_diff" not in s3.sql
    assert s3.hadm_companion_sql is not None and "bool_or(p.flag)" in s3.hadm_companion_sql
    kdigo = registry.get("kdigo_aki@1.0.0")
    aki = compiler_mod.compile_phenotype(
        kdigo.phenotype, registry.codesets, concepts=kdigo.concept_hashes
    )
    from mimicwarehouse.timesem import sql_hours_since

    hours = sql_hours_since("s.intime", "c.charttime")
    assert f"{hours} >= 0.0" in aki.sql and f"{hours} < 168.0" in aki.sql
    assert "c.aki_stage_smoothed >= 1" in aki.sql
    assert "max(ev_max_stage_in_window) AS ev_max_stage_in_window" in aki.sql
    assert "coalesce(ev_max_stage_in_window, 0) AS max_stage_in_window" in aki.sql
    assert "ev_stage_at_onset AS stage_at_onset" in aki.sql
    assert "-- parameters: min_stage=1, window_hours=168" in aki.sql
    assert aki.evidence_columns == ("max_stage_in_window", "stage_at_onset")
    described = compiler_mod.describe(aki)
    assert described["evidence_columns"] == ["max_stage_in_window", "stage_at_onset"]
    # a variant compiles a different window, nothing else
    variant = phenotype_from_text(_text(kdigo), parameters={"window_hours": 336})
    v = compiler_mod.compile_phenotype(variant, registry.codesets)
    assert f"{hours} < 336.0" in v.sql and "min_stage=1, window_hours=336" in v.sql
    explicit = registry.get("sepsis_explicit@1.0.0")
    ex = compiler_mod.compile_phenotype(
        explicit.phenotype, registry.codesets, resolved=explicit.resolved
    )
    assert ex.columns == ("subject_id", "hadm_id", "flag", "onset_time", "evidence_json")
    assert ex.hadm_companion_sql is None and ex.evidence_columns == ()
    assert "d.icd_code LIKE '038%'" in ex.sql and "d.icd_code LIKE 'A41%'" in ex.sql
    # the t2dm golden of EP-41 is untouched by the compiler change
    t2dm = registry.get("t2dm@1.0.0")
    assert (GOLDEN / "t2dm@1.0.0.sql").read_text(
        encoding="utf-8"
    ) == compiler_mod.compile_phenotype(
        t2dm.phenotype, registry.codesets, resolved=t2dm.resolved, concepts=t2dm.concept_hashes
    ).sql.rstrip("\n") + "\n"


# ---------------------------------------------------------------------------
# 5. Crafted synthetic concept frames: flags, onsets, evidence, the window
# ---------------------------------------------------------------------------


def _crafted_db(contract: Contract) -> duckdb.DuckDBPyConnection:
    from mimicwarehouse.engine import open_duckdb

    con = open_duckdb("app")
    for schema in (HOSP, ICU, DERIVED):
        con.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
    for qn in (
        f"{HOSP}.patients",
        f"{HOSP}.admissions",
        f"{HOSP}.diagnoses_icd",
        f"{ICU}.icustays",
    ):
        con.execute(contract.table(qn).duckdb_ddl())
    con.execute(
        f"CREATE TABLE {DERIVED}.sepsis3 (subject_id INTEGER, stay_id INTEGER, "
        "antibiotic_time TIMESTAMP, culture_time TIMESTAMP, suspected_infection_time TIMESTAMP, "
        "sofa_time TIMESTAMP, sofa_score INTEGER, sepsis3 BOOLEAN)"
    )
    con.execute(
        f"CREATE TABLE {DERIVED}.kdigo_stages (subject_id INTEGER, hadm_id INTEGER, "
        "stay_id INTEGER, charttime TIMESTAMP, creat DOUBLE, aki_stage_creat INTEGER, "
        "aki_stage_uo INTEGER, aki_stage INTEGER, aki_stage_smoothed INTEGER)"
    )
    return con


def _insert(
    con: duckdb.DuckDBPyConnection, contract: Contract, qn: str, rows: list[dict[str, Any]]
) -> None:
    table = contract.table(qn)
    names = [c.name for c in table.columns]
    placeholders = ", ".join("?" for _ in names)
    con.executemany(
        f"INSERT INTO {qn} VALUES ({placeholders})",
        [[row.get(n) for n in names] for row in rows],
    )


def _patient(subject_id: int) -> dict[str, Any]:
    return {
        "subject_id": subject_id,
        "gender": "M",
        "anchor_age": 65,
        "anchor_year": 2150,
        "anchor_year_group": "2017 - 2019",
        "dod": None,
    }


def _admission(subject_id: int, hadm_id: int, start: datetime, days: int) -> dict[str, Any]:
    return {
        "subject_id": subject_id,
        "hadm_id": hadm_id,
        "admittime": start,
        "dischtime": start + timedelta(days=days),
        "admission_type": "URGENT",
        "admission_location": "EMERGENCY ROOM",
        "insurance": "Other",
        "hospital_expire_flag": 0,
    }


def _stay(
    subject_id: int, hadm_id: int, stay_id: int, intime: datetime, days: int
) -> dict[str, Any]:
    return {
        "subject_id": subject_id,
        "hadm_id": hadm_id,
        "stay_id": stay_id,
        "first_careunit": "MICU",
        "last_careunit": "MICU",
        "intime": intime,
        "outtime": intime + timedelta(days=days),
        "los": float(days),
    }


def _dx(subject_id: int, hadm_id: int, seq: int, code: str, version: int) -> dict[str, Any]:
    return {
        "subject_id": subject_id,
        "hadm_id": hadm_id,
        "seq_num": seq,
        "icd_code": code,
        "icd_version": version,
    }


def _kdigo(
    stay: int, subject: int, hadm: int, when: datetime | None, stage: int | None
) -> list[Any]:
    return [subject, hadm, stay, when, None, stage, None, stage, stage]


def _crafted_rows(contract: Contract, con: duckdb.DuckDBPyConnection) -> dict[str, datetime]:
    """Five subjects, five admissions, five ICU stays (two on one admission); returns the
    ICU intimes by stay label."""
    day = timedelta(days=1)
    intimes = {
        "s1": T0 + 1 * day,
        "s2": T0 + 40 * day,
        "s3": T0 + 80 * day,
        "s4": T0 + 120 * day,
        "s5": T0 + 130 * day,
    }
    _insert(con, contract, f"{HOSP}.patients", [_patient(S + i) for i in range(1, 6)])
    _insert(
        con,
        contract,
        f"{HOSP}.admissions",
        [
            _admission(S + 1, H + 1, T0, 30),
            _admission(S + 2, H + 2, T0 + 39 * day, 30),
            _admission(S + 3, H + 3, T0 + 79 * day, 30),
            _admission(S + 4, H + 4, T0 + 119 * day, 30),
            _admission(S + 5, H + 5, T0 + 200 * day, 5),  # no ICU stay
        ],
    )
    _insert(
        con,
        contract,
        f"{ICU}.icustays",
        [
            _stay(S + 1, H + 1, ST + 1, intimes["s1"], 20),
            _stay(S + 2, H + 2, ST + 2, intimes["s2"], 20),
            _stay(S + 3, H + 3, ST + 3, intimes["s3"], 20),
            _stay(S + 4, H + 4, ST + 4, intimes["s4"], 5),
            _stay(S + 4, H + 4, ST + 5, intimes["s5"], 5),
        ],
    )
    _insert(
        con,
        contract,
        f"{HOSP}.diagnoses_icd",
        [
            _dx(S + 1, H + 1, 1, "0389", 9),  # explicit (ICD-9 038.x)
            _dx(S + 2, H + 2, 1, "4019", 9),  # not sepsis
            _dx(S + 3, H + 3, 2, "99591", 9),  # explicit (995.91), secondary position
            _dx(S + 5, H + 5, 1, "A419", 10),  # explicit (A41.9), admission without ICU stay
        ],
    )
    s1, s2, s4, s5 = intimes["s1"], intimes["s2"], intimes["s4"], intimes["s5"]
    con.executemany(
        f"INSERT INTO {DERIVED}.sepsis3 VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            [
                S + 1,
                ST + 1,
                s1 + 10 * HOUR,
                s1 + 11 * HOUR,
                s1 + 12 * HOUR,
                s1 + 20 * HOUR,
                5,
                True,
            ],
            [S + 2, ST + 2, s2 + 2 * HOUR, s2 + 3 * HOUR, s2 + 3 * HOUR, s2 + 6 * HOUR, 1, False],
            [S + 4, ST + 5, s5 + 4 * HOUR, s5 + 5 * HOUR, s5 + 5 * HOUR, s5 + 9 * HOUR, 3, True],
        ],
    )
    s3 = intimes["s3"]
    con.executemany(
        f"INSERT INTO {DERIVED}.kdigo_stages VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            # stay 1: 0 @ 10 h, 1 @ 30 h, 2 @ 50 h, a stage-3 row 1 h before intime and one
            # exactly at intime + 168 h (both outside the [0, 168) window)
            _kdigo(ST + 1, S + 1, H + 1, s1 + 10 * HOUR, 0),
            _kdigo(ST + 1, S + 1, H + 1, s1 + 30 * HOUR, 1),
            _kdigo(ST + 1, S + 1, H + 1, s1 + 50 * HOUR, 2),
            _kdigo(ST + 1, S + 1, H + 1, s1 - 1 * HOUR, 3),
            _kdigo(ST + 1, S + 1, H + 1, s1 + 168 * HOUR, 3),
            # stay 2: stage 2 only after 7 days (200 h)
            _kdigo(ST + 2, S + 2, H + 2, s2 + 5 * HOUR, 0),
            _kdigo(ST + 2, S + 2, H + 2, s2 + 200 * HOUR, 2),
            # stay 3: 1 @ 20 h, 3 @ 100 h
            _kdigo(ST + 3, S + 3, H + 3, s3 + 20 * HOUR, 1),
            _kdigo(ST + 3, S + 3, H + 3, s3 + 100 * HOUR, 3),
            # stay 4: only zeros; stay 5: a row without charttime / stage (no data)
            _kdigo(ST + 4, S + 4, H + 4, s4 + 3 * HOUR, 0),
            _kdigo(ST + 4, S + 4, H + 4, s4 + 9 * HOUR, 0),
            _kdigo(ST + 5, S + 4, H + 4, None, None),
        ],
    )
    return intimes


def _rows_by_stay(con: duckdb.DuckDBPyConnection, sql: str) -> dict[int, tuple[Any, ...]]:
    return {r[2]: tuple(r[3:]) for r in con.execute(sql).fetchall()}


def test_crafted_concept_frames(contract: Contract, registry: Registry) -> None:
    con = _crafted_db(contract)
    try:
        intimes = _crafted_rows(contract, con)
        s1, s2, s3, s5 = intimes["s1"], intimes["s2"], intimes["s3"], intimes["s5"]
        # sepsis-3: flag, onset = suspected_infection_time, sofa evidence at the first event
        sepsis3 = registry.get("sepsis3@1.0.0")
        s3_sql = compiler_mod.compile_phenotype(sepsis3.phenotype, registry.codesets).sql
        got = _rows_by_stay(con, s3_sql)
        assert set(got) == {ST + 1, ST + 2, ST + 3, ST + 4, ST + 5}
        flag, onset, evidence, sofa_time, sofa_score = got[ST + 1]
        assert flag and onset == s1 + 12 * HOUR and json.loads(evidence) == {"s3": 1}
        assert sofa_time == s1 + 20 * HOUR and sofa_score == 5
        assert got[ST + 2] == (False, None, '{"s3":0}', None, None), "sepsis3 = false"
        assert got[ST + 3] == (False, None, '{"s3":0}', None, None), "no concept row"
        assert got[ST + 4][0] is False and got[ST + 5][0] is True
        assert got[ST + 5][1] == s5 + 5 * HOUR and got[ST + 5][4] == 3
        # KDIGO: the default window (168 h) and min_stage 1
        kdigo = registry.get("kdigo_aki@1.0.0")
        aki_sql = compiler_mod.compile_phenotype(kdigo.phenotype, registry.codesets).sql
        got = _rows_by_stay(con, aki_sql)
        assert got[ST + 1] == (True, s1 + 30 * HOUR, '{"aki":2}', 2, 1), (
            "onset = the first qualifying row; the stage-3 rows outside [0, 168) do not count"
        )
        assert got[ST + 2] == (False, None, '{"aki":0}', 0, None), "stage 2 only after 7 days"
        assert got[ST + 3] == (True, s3 + 20 * HOUR, '{"aki":2}', 3, 1)
        assert got[ST + 4] == (False, None, '{"aki":0}', 0, None), "only stage 0"
        assert got[ST + 5] == (False, None, '{"aki":0}', 0, None), "no data"
        assert max(len(r[2]) for r in got.values()) <= phen_runner.EVIDENCE_MAX_CHARS
        # window_hours = 336: the late stage-2 stay turns positive, nothing else moves
        wide = phenotype_from_text(_text(kdigo), parameters={"window_hours": 336})
        got_wide = _rows_by_stay(con, compiler_mod.compile_phenotype(wide, registry.codesets).sql)
        assert got_wide[ST + 2] == (True, s2 + 200 * HOUR, '{"aki":1}', 2, 2)
        assert got_wide[ST + 1] == (True, s1 + 30 * HOUR, '{"aki":3}', 3, 1), (
            "the stage-3 row at exactly intime + 168 h now lies inside [0, 336)"
        )
        assert got_wide[ST + 3] == got[ST + 3]
        assert got_wide[ST + 4] == got[ST + 4] and got_wide[ST + 5] == got[ST + 5]
        # min_stage = 2: stage-1 rows stop qualifying (onset / stage_at_onset move)
        severe = phenotype_from_text(_text(kdigo), parameters={"min_stage": 2})
        got_severe = _rows_by_stay(
            con, compiler_mod.compile_phenotype(severe, registry.codesets).sql
        )
        assert got_severe[ST + 1] == (True, s1 + 50 * HOUR, '{"aki":1}', 2, 2)
        assert got_severe[ST + 3] == (True, s3 + 100 * HOUR, '{"aki":1}', 3, 3)
        assert got_severe[ST + 2][0] is False
        # explicit sepsis per admission (any position; the ICU-less admission counts)
        explicit = registry.get("sepsis_explicit@1.0.0")
        ex_sql = compiler_mod.compile_phenotype(explicit.phenotype, registry.codesets).sql
        ex = {r[1]: (bool(r[2]), r[3]) for r in con.execute(ex_sql).fetchall()}
        assert {h for h, (f, _t) in ex.items() if f} == {H + 1, H + 3, H + 5}
        assert ex[H + 1][1] == T0 + timedelta(days=30), "onset = dischtime"
        # the icustay companion: admissions with >= 1 stay, any stay flagged, n_stays
        con.execute(f"CREATE TABLE s3 AS {s3_sql}")
        companion_sql = compiler_mod.compile_phenotype(sepsis3.phenotype, registry.codesets)
        assert companion_sql.hadm_companion_sql is not None
        companion = {
            r[1]: (bool(r[2]), r[3], r[4])
            for r in con.execute(companion_sql.hadm_companion_sql.format(relation="s3")).fetchall()
        }
        assert set(companion) == {H + 1, H + 2, H + 3, H + 4}, "the ICU-less admission is absent"
        assert companion[H + 1] == (True, s1 + 12 * HOUR, 1)
        assert companion[H + 2] == (False, None, 1) and companion[H + 3] == (False, None, 1)
        assert companion[H + 4] == (True, s5 + 5 * HOUR, 2), "one of two stays flagged"
        # the 2x2 the summary runs: per admission with >= 1 ICU stay
        con.execute(f"CREATE TABLE ex AS {ex_sql}")
        con.execute("CREATE TABLE s3h AS " + companion_sql.hadm_companion_sql.format(relation="s3"))
        cells = con.execute(
            "SELECT count(*), count(*) FILTER (WHERE a.flag AND b.flag), "
            "count(*) FILTER (WHERE a.flag AND NOT b.flag), "
            "count(*) FILTER (WHERE NOT a.flag AND b.flag), "
            "count(*) FILTER (WHERE NOT a.flag AND NOT b.flag) "
            "FROM s3h AS a JOIN ex AS b ON b.hadm_id = a.hadm_id"
        ).fetchone()
        assert cells == (4, 1, 1, 1, 1)
        # the same crafted frames through the hadm grain: every admission with any stay
        # flagged (the concept's stay -> the admission's window)
        hadm_grain = phenotype_from_text(_text(sepsis3).replace("grain: icustay", "grain: hadm"))
        by_hadm = {
            r[1]: bool(r[2])
            for r in con.execute(
                compiler_mod.compile_phenotype(hadm_grain, registry.codesets).sql
            ).fetchall()
        }
        assert by_hadm == {H + 1: True, H + 2: False, H + 3: False, H + 4: True, H + 5: False}
    finally:
        con.close()


# ---------------------------------------------------------------------------
# 6. Wiring: the DAG orders the concepts first, the CLI launcher, the import budget
# ---------------------------------------------------------------------------


def test_dag_spec_orders_concepts_first_and_cli_launcher(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, registry: Registry
) -> None:
    dag = load_dag()
    step = dag.step(phen_runner.STEP_COMPILE)
    assert set(CONCEPT_STEPS) <= set(step.depends_on)
    assert set(phen_runner.concept_steps(registry)) <= set(step.depends_on), (
        "every concept the packaged definitions pin is a dependency of phenotypes.compile"
    )
    order = [s.name for s in dag.ordered(tier="fixture")]
    compile_at = order.index(phen_runner.STEP_COMPILE)
    for concept_step in CONCEPT_STEPS:
        assert order.index(concept_step) < compile_at, f"{concept_step} runs first"
    assert order.index("catalog") > compile_at
    tagged = [s.name for s in dag.ordered(tags=[phen_runner.DAG_TAG], tier="fixture")]
    assert tagged == [phen_runner.STEP_COMPILE, "catalog"], "the tag run pulls no concept"
    with_deps = {s.name for s in dag.ordered(select=[phen_runner.STEP_COMPILE], with_deps=True)}
    assert set(CONCEPT_STEPS) <= with_deps and "concept.score.sofa" in with_deps
    # the launcher: argv mirrors the foreground command; --job is mandatory
    launched: list[tuple[list[str], str]] = []

    def fake_launch(argv: list[str], job: str, settings: Any) -> Any:
        launched.append((list(argv), job))
        return SimpleNamespace(job=job, pid=4242, log=str(tmp_path / f"{job}.log"))

    from mimicwarehouse.dag import jobs as jobs_mod

    monkeypatch.setattr(jobs_mod, "launch", fake_launch)
    runner = helpers.cli_runner()
    root = str(tmp_path / "root")
    try:
        ok = runner.invoke(
            app,
            [
                "--data-root",
                root,
                "phenotype",
                "compile",
                "sepsis3@1.0.0",
                "kdigo_aki@1.0.0",
                "--tier",
                "fixture",
                "--force",
                "--background",
                "--job",
                "phenotypes-test",
            ],
        )
        assert ok.exit_code == 0 and "launched job" in ok.stdout, ok.output
        assert launched == [
            (
                [
                    "--data-root",
                    root,
                    "phenotype",
                    "compile",
                    "sepsis3@1.0.0",
                    "kdigo_aki@1.0.0",
                    "--tier",
                    "fixture",
                    "--force",
                ],
                "phenotypes-test",
            )
        ]
        assert not (tmp_path / "root" / "lake").exists(), "the parent built nothing"
        no_job = runner.invoke(
            app, ["--data-root", root, "phenotype", "compile", "--tier", "fixture", "--background"]
        )
        assert no_job.exit_code == 2 and "--job" in no_job.stderr
        dry = runner.invoke(
            app,
            ["phenotype", "compile", "sepsis3@1.0.0", "--dry-run", "--background", "--job", "x"],
        )
        assert dry.exit_code == 2 and "--dry-run" in dry.stderr
        dry_ok = runner.invoke(app, ["phenotype", "compile", *REFS, "--dry-run"])
        assert dry_ok.exit_code == 0 and dry_ok.stdout.count("-- phenotype ") == 3
        assert "ORDER BY subject_id, hadm_id, stay_id" in dry_ok.stdout
        assert len(launched) == 1
        shown = runner.invoke(app, ["phenotype", "show", "sepsis3@1.0.0"])
        assert shown.exit_code == 0 and "concept: mimiciv_derived.sepsis3 ->" in shown.stdout
        assert "evidence: sofa_score = first(sofa_score)" in shown.stdout
        shown_json = json.loads(
            runner.invoke(app, ["phenotype", "show", "kdigo_aki@1.0.0", "--json", "--sql"]).stdout
        )
        assert (
            "window_hours=168" in shown_json["sql"]
            and shown_json["parameters"]["window_hours"] == 168
        )
        validated = json.loads(
            runner.invoke(app, ["phenotype", "validate", "kdigo_aki@1.0.0", "--json"]).stdout
        )
        assert validated["ok"] and validated["evidence"] == [
            "max_stage_in_window",
            "stage_at_onset",
        ]
        assert set(validated["concepts"]) == {f"{DERIVED}.kdigo_stages"}
    finally:
        config.configure()
        _drop_progress_handlers()
    helpers.assert_import_budget(
        "mimicwarehouse.phenotypes.cli",
        lazy=(
            "mimicwarehouse.safe",
            "mimicwarehouse.catalog.build",
            "mimicwarehouse.dag.runner",
            "mimicwarehouse.dag.jobs",
            "mimicwarehouse.concepts.runner",
            "mimicwarehouse.concepts.inventory",
            "mimicwarehouse.concepts.patching",
            "mimicwarehouse.units",
            "mimicwarehouse.timesem",
            "mimicwarehouse.run",
            "mimicwarehouse.phenotypes.runner",
            "mimicwarehouse.phenotypes.compiler",
        ),
    )


# ---------------------------------------------------------------------------
# 7. A runner-built fixture lake with the concept ancestors: materialisation,
#    companions, meta, the independent evaluation, summary / distribution /
#    agreement / report, the pin mismatch, the second version
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def lake(tmp_path_factory: pytest.TempPathFactory) -> Settings:
    """A fixture lake: phenotypes.compile with its ancestors (the ten core tables, the
    sepsis3 / kdigo_stages concept chains) and the catalog; only the three EP-42
    phenotypes selected."""
    root = tmp_path_factory.mktemp("ep42-lake")
    settings = config.Settings(data_root=root)
    with phen_runner.compile_options(select=REFS):
        result = runner_mod.run(
            load_dag(),
            "fixture",
            select=[phen_runner.STEP_COMPILE, "catalog"],
            with_deps=True,
            settings=settings,
        )
    failed = [f"{s.name}: {s.error}" for s in result.steps if s.status == "failed"]
    assert not failed, failed
    return settings


def test_lake_materialisation_views_meta_and_independent_evaluation(
    lake: Settings, registry: Registry
) -> None:
    from mimicwarehouse.catalog.connect import open_catalog
    from mimicwarehouse.run import list_runs, read_manifest

    settings = lake
    lake_root = settings.lake_root("fixture")
    attempts: dict[str, dict[str, Any]] = {}
    for ref in REFS:
        entry = registry.get(ref)
        part = phen_runner.phenotype_part(lake_root, "fixture", ref)
        assert part.is_file() and phen_runner.phenotype_complete(lake_root, "fixture", ref)
        attempt = phen_runner.phenotype_attempt(lake_root, "fixture", ref)
        assert attempt is not None and attempt["status"] == "done"
        assert attempt["def_hash"] == entry.def_hash and attempt["refs"] == entry.resolved
        assert attempt["concepts"] == entry.concept_hashes and attempt["run_id"]
        attempts[ref] = attempt
    assert phen_runner.phenotype_attempt(lake_root, "fixture", "t2dm@1.0.0") is None, (
        "only the selected phenotypes were attempted"
    )
    con = open_catalog("fixture", settings=settings)
    try:
        n_stays = _scalar(con, f"SELECT count(*) FROM {ICU}.icustays")
        n_hadm = _scalar(con, f"SELECT count(*) FROM {HOSP}.admissions")
        views = {
            str(r[0]): str(r[1])
            for r in con.execute(
                "SELECT schema_name || '.' || view_name, comment FROM duckdb_views() "
                "WHERE schema_name IN ('phenotypes', 'mimiciv_derived')"
            ).fetchall()
        }
        for ref in REFS:
            assert f"phenotypes.{ref}" in views
        assert (
            "typed evidence columns sofa_time, sofa_score" in views[f"{DERIVED}.phenotype_sepsis3"]
        )
        assert "max_stage_in_window, stage_at_onset" in views[f"{DERIVED}.phenotype_kdigo_aki"]
        assert "EP-42" in views[f"{DERIVED}.phenotype_sepsis3_hadm"]
        assert "at least one ICU stay" in views[f"{DERIVED}.phenotype_kdigo_aki_hadm"]
        assert f"{DERIVED}.phenotype_sepsis_explicit_hadm" not in views, "hadm grain: no companion"
        assert phen_runner.latest_versions(con) == {
            "kdigo_aki": "1.0.0",
            "sepsis3": "1.0.0",
            "sepsis_explicit": "1.0.0",
        }
        assert attempts["sepsis3@1.0.0"]["rows"] == n_stays == attempts["kdigo_aki@1.0.0"]["rows"]
        assert attempts["sepsis_explicit@1.0.0"]["rows"] == n_hadm
        # independent SQL evaluations over the concept tables the walker registered
        expected_s3 = {
            r[0]
            for r in con.execute(f"SELECT stay_id FROM {DERIVED}.sepsis3 WHERE sepsis3").fetchall()
        }
        got_s3 = {
            r[0]
            for r in con.execute(
                f"SELECT stay_id FROM {DERIVED}.phenotype_sepsis3 WHERE flag"
            ).fetchall()
        }
        assert got_s3 == expected_s3
        assert len(got_s3) == attempts["sepsis3@1.0.0"]["n_positive"]
        onsets = con.execute(
            f"SELECT count(*) FROM {DERIVED}.phenotype_sepsis3 AS p JOIN {DERIVED}.sepsis3 AS c "
            "ON c.stay_id = p.stay_id WHERE p.flag AND (p.onset_time IS DISTINCT FROM "
            "c.suspected_infection_time OR p.sofa_score IS DISTINCT FROM c.sofa_score "
            "OR p.sofa_time IS DISTINCT FROM c.sofa_time)"
        ).fetchone()
        assert onsets == (0,), "onset and evidence equal the concept's columns"
        expected_aki = {
            r[0]: (r[1], r[2])
            for r in con.execute(
                "SELECT k.stay_id, max(k.aki_stage_smoothed), min(k.charttime) FILTER "
                "(WHERE k.aki_stage_smoothed >= 1) "
                f"FROM {DERIVED}.kdigo_stages AS k JOIN {ICU}.icustays AS s "
                "ON s.stay_id = k.stay_id "
                "WHERE k.aki_stage_smoothed >= 1 AND k.charttime >= s.intime "
                "AND k.charttime < s.intime + INTERVAL 168 HOUR GROUP BY 1"
            ).fetchall()
        }
        got_aki = {
            r[0]: (r[1], r[2])
            for r in con.execute(
                "SELECT stay_id, max_stage_in_window, onset_time "
                f"FROM {DERIVED}.phenotype_kdigo_aki WHERE flag"
            ).fetchall()
        }
        assert got_aki == expected_aki
        assert len(got_aki) == attempts["kdigo_aki@1.0.0"]["n_positive"]
        assert (
            _scalar(
                con,
                f"SELECT count(*) FROM {DERIVED}.phenotype_kdigo_aki WHERE NOT flag "
                "AND (max_stage_in_window <> 0 OR stage_at_onset IS NOT NULL "
                "OR onset_time IS NOT NULL)",
            )
            == 0
        ), "negative stays carry the default 0 and no onset"
        stage_levels = {
            r[0]
            for r in con.execute(
                f"SELECT DISTINCT max_stage_in_window FROM {DERIVED}.phenotype_kdigo_aki"
            ).fetchall()
        }
        assert stage_levels <= {0, 1, 2, 3}
        # the icustay companion: one row per admission with a stay, any-stay semantics
        companion = con.execute(
            "SELECT count(*), count(*) FILTER (WHERE flag), sum(n_stays) "
            f"FROM {DERIVED}.phenotype_sepsis3_hadm"
        ).fetchone()
        assert companion is not None
        assert companion[0] == _scalar(con, f"SELECT count(DISTINCT hadm_id) FROM {ICU}.icustays")
        assert companion[2] == n_stays
        assert companion[1] == _scalar(
            con, f"SELECT count(DISTINCT hadm_id) FROM {DERIVED}.phenotype_sepsis3 WHERE flag"
        )
        # meta.phenotype_versions carries the concept pins
        cols = [d[0] for d in con.execute("DESCRIBE meta.phenotype_versions").fetchall()]
        assert cols == [c for c, _t in phen_runner.VERSIONS_COLUMNS] and cols[-1] == "concept_refs"
        meta = {
            f"{r[0]}@{r[1]}": r
            for r in con.execute(
                "SELECT phenotype_id, version, def_hash, grain, refs, concept_refs, rows, status, "
                "run_id FROM meta.phenotype_versions"
            ).fetchall()
        }
        assert set(meta) == set(REFS)
        for ref in REFS:
            entry = registry.get(ref)
            row = meta[ref]
            assert row[2] == entry.def_hash and row[3] == entry.phenotype.grain
            assert json.loads(row[4]) == entry.resolved
            assert json.loads(row[5]) == entry.concept_hashes
            assert row[7] == "done" and row[8] == attempts[ref]["run_id"]
        comment = _scalar(
            con,
            "SELECT comment FROM duckdb_tables() WHERE schema_name = 'meta' "
            "AND table_name = 'phenotype_versions'",
        )
        assert "concept_refs" in comment
    finally:
        con.close()
    # the kind: phenotype runs cite the concept (and code-set) refs with hashes
    runs = {r["run_id"] for r in list_runs(settings, kind="phenotype")}
    assert runs == {a["run_id"] for a in attempts.values()}
    manifest = read_manifest(attempts["kdigo_aki@1.0.0"]["run_id"], settings)
    refs = {(r.kind, r.name): (r.version, r.hash) for r in manifest.refs}
    pin = registry.get("kdigo_aki@1.0.0").concepts[f"{DERIVED}.kdigo_stages"]
    assert refs[("concept", "kdigo_stages")] == (pin.upstream_commit, pin.executed_sha256)
    assert refs[("phenotype", "kdigo_aki")] == ("1.0.0", registry.get("kdigo_aki@1.0.0").def_hash)
    explicit_manifest = read_manifest(attempts["sepsis_explicit@1.0.0"]["run_id"], settings)
    explicit_refs = {(r.kind, r.name) for r in explicit_manifest.refs}
    assert ("codeset", "sepsis_explicit") in explicit_refs
    sql_text = (
        settings.layout["runs"] / attempts["sepsis3@1.0.0"]["run_id"] / "sql" / "phenotype.sql"
    ).read_text(encoding="utf-8")
    assert f"-- concepts: {DERIVED}.sepsis3=" in sql_text


def test_lake_summary_distribution_agreement_and_report(
    lake: Settings, registry: Registry, tmp_path: Path
) -> None:
    from mimicwarehouse.catalog.connect import open_catalog
    from mimicwarehouse.run import list_runs, read_manifest

    settings = lake
    root = str(settings.data_root)
    con = open_catalog("fixture", settings=settings)
    try:
        n_stays = _scalar(con, f"SELECT count(*) FROM {ICU}.icustays")
        expected_stage = {
            int(r[0]): (int(r[1]), int(r[2]))
            for r in con.execute(
                "SELECT max_stage_in_window, count(*), count(*) FILTER (WHERE flag) "
                f"FROM {DERIVED}.phenotype_kdigo_aki GROUP BY 1"
            ).fetchall()
        }
        expected_2x2 = con.execute(
            "SELECT count(*), count(*) FILTER (WHERE a.flag AND b.flag), "
            "count(*) FILTER (WHERE a.flag AND NOT b.flag), "
            "count(*) FILTER (WHERE NOT a.flag AND b.flag), "
            "count(*) FILTER (WHERE NOT a.flag AND NOT b.flag) "
            f"FROM {DERIVED}.phenotype_sepsis3_hadm AS a "
            f"JOIN {DERIVED}.phenotype_sepsis_explicit AS b ON b.hadm_id = a.hadm_id"
        ).fetchone()
    finally:
        con.close()
    assert expected_2x2 is not None
    # summarize per grain (k = 1 on the synthetic tier)
    s3 = phen_runner.summarize(
        "sepsis3@1.0.0", tier="fixture", settings=settings, k=1, actor="test_ep42"
    )
    assert s3.grain == "icustay" and s3.rows_suppressed == 0 and s3.snapshot_id
    total = s3.df.filter(s3.df["scope"] == "all").row(0, named=True)
    assert total["unit"] == "icustay" and total["n_units"] == n_stays
    eras = s3.df.filter(s3.df["scope"] != "all")
    assert set(eras["unit"].to_list()) == {"icustay"} and int(eras["n_units"].sum()) == n_stays
    ex = phen_runner.summarize("sepsis_explicit@1.0.0", tier="fixture", settings=settings, k=1)
    assert ex.grain == "hadm" and ex.df.filter(ex.df["scope"] == "all")["unit"][0] == "hadm"
    # the stage distribution equals the direct GROUP BY
    dist = phen_runner.distribution(
        "kdigo_aki@1.0.0",
        "max_stage_in_window",
        tier="fixture",
        settings=settings,
        k=1,
        levels=(0, 1, 2, 3),
    )
    assert dist.levels == (0, 1, 2, 3) and dist.rows_suppressed == 0
    got_stage = {
        int(r["level"]): (int(r["n_units"]), int(r["n_positive"])) for r in dist.df.to_dicts()
    }
    assert got_stage == expected_stage
    assert got_stage[0][1] == 0 and all(n == p for level, (n, p) in got_stage.items() if level > 0)
    with pytest.raises(PhenotypeError, match="not a typed evidence column"):
        phen_runner.distribution(
            "kdigo_aki@1.0.0", "stay_id", tier="fixture", settings=settings, k=1
        )
    # the 2x2 equals the direct join
    pair = phen_runner.agreement(
        "sepsis3@1.0.0", "sepsis_explicit@1.0.0", tier="fixture", settings=settings, k=1
    )
    assert pair.denominator == "admissions with at least one ICU stay" and not pair.suppressed
    assert (pair.n_hadm, pair.n_both, pair.n_a_only, pair.n_b_only, pair.n_neither) == expected_2x2
    cells = [pair.n_both, pair.n_a_only, pair.n_b_only, pair.n_neither]
    assert all(c is not None for c in cells) and sum(c or 0 for c in cells) == pair.n_hadm
    with pytest.raises(PhenotypeError, match="two different"):
        phen_runner.agreement(
            "sepsis3@1.0.0", "sepsis3@1.0.0", tier="fixture", settings=settings, k=1
        )
    # the bundle: three summaries, one distribution, three pairs; a run records the reads
    from mimicwarehouse import run as run_mod

    with run_mod.start(
        "ep42 prevalence",
        tier="fixture",
        kind="analysis",
        settings=settings,
        claim_type="exploratory",
        doctor=False,
    ) as r:
        bundle = phen_runner.prevalence_report(
            REFS,
            tier="fixture",
            settings=settings,
            k=1,
            actor="test_ep42",
            run=r,
            registry=registry,
        )
        report_path = phen_runner.write_prevalence_report(bundle, r.dir)
    assert [s.ref for s in bundle.summaries] == list(REFS)
    assert [(d.ref, d.column) for d in bundle.distributions] == [
        ("kdigo_aki@1.0.0", "max_stage_in_window")
    ]
    assert [(a.ref_a, a.ref_b) for a in bundle.agreements] == [
        ("sepsis3@1.0.0", "kdigo_aki@1.0.0"),
        ("sepsis3@1.0.0", "sepsis_explicit@1.0.0"),
        ("kdigo_aki@1.0.0", "sepsis_explicit@1.0.0"),
    ]
    assert bundle.run_id == r.run_id and len(bundle.audit_ids) == 3 * 2 + 1 + 3
    manifest = read_manifest(r.run_id, settings)
    assert (
        manifest.kind == "analysis"
        and manifest.claim_type == "exploratory"
        and manifest.status == "ok"
    )
    assert set(manifest.audit_ids) == set(bundle.audit_ids) and len(manifest.sql) == len(
        bundle.audit_ids
    )
    assert {(x.kind, x.name) for x in manifest.refs} >= {
        ("phenotype", "sepsis3"),
        ("phenotype", "kdigo_aki"),
    }
    text = report_path.read_text(encoding="utf-8")
    assert report_path.name == phen_runner.PREVALENCE_REPORT and report_path.parent == r.dir
    assert text.isascii() and not BAND_TOKEN.search(text)
    assert "**Claim type: exploratory.**" in text and "retrospective" in text
    assert phen_runner.SIDECAR_PENDING in text and f"Run `{r.run_id}`" in text
    assert "### `max_stage_in_window` of `kdigo_aki@1.0.0`" in text
    assert "### `sepsis3@1.0.0` x `sepsis_explicit@1.0.0`" in text
    assert "admissions with at least one ICU stay" in text
    assert "## What these definitions do not claim" in text and "## Reproduction" in text
    for word in ("subject_id", "hadm_id", "stay_id"):
        assert word not in text, "no identifier column name enters the artefact"
    assert text.count("\n| ") >= 12
    assert phen_runner.render_prevalence_report(bundle) == text
    payload = bundle.to_dict()
    assert payload["agreement"][1]["n_hadm"] == expected_2x2[0] and payload["k"] == 1
    # the CLI: several refs, --json, --report into the run folder, --out elsewhere
    runner = helpers.cli_runner()
    try:
        cli = runner.invoke(
            app,
            ["--data-root", root, "phenotype", "summary", *REFS, "--tier", "fixture", "--k", "1"],
        )
        assert cli.exit_code == 0, cli.output
        assert cli.stdout.count("mwh phenotype summary ") == 3
        assert "max_stage_in_window of kdigo_aki@1.0.0" in cli.stdout
        assert "sepsis3@1.0.0 x sepsis_explicit@1.0.0 per admission" in cli.stdout
        assert "report " not in cli.stdout
        before = {x["run_id"] for x in list_runs(settings, kind="analysis")}
        as_json = runner.invoke(
            app,
            [
                "--data-root",
                root,
                "phenotype",
                "summary",
                *REFS,
                "--tier",
                "fixture",
                "--k",
                "1",
                "--json",
                "--report",
            ],
        )
        assert as_json.exit_code == 0, as_json.output
        doc = json.loads(as_json.stdout)
        assert [p["ref"] for p in doc["phenotypes"]] == list(REFS) and doc["run_id"]
        assert doc["agreement"][1]["n_both"] == expected_2x2[1]
        assert Path(doc["report"]).is_file() and Path(doc["report"]).parent.name == doc["run_id"]
        after = {x["run_id"] for x in list_runs(settings, kind="analysis")}
        assert after - before == {doc["run_id"]}
        cli_manifest = read_manifest(doc["run_id"], settings)
        assert cli_manifest.params["report"] == doc["report"] and cli_manifest.params[
            "refs"
        ] == list(REFS)
        single = json.loads(
            runner.invoke(
                app,
                [
                    "--data-root",
                    root,
                    "phenotype",
                    "summary",
                    "sepsis3@1.0.0",
                    "--tier",
                    "fixture",
                    "--k",
                    "1",
                    "--json",
                ],
            ).stdout
        )
        assert single["ref"] == "sepsis3@1.0.0" and single["rows"][0]["n_units"] == n_stays
        assert single["phenotypes"][0]["ref"] == "sepsis3@1.0.0" and single["agreement"] == []
        out_dir = tmp_path / "report-out"
        with_out = runner.invoke(
            app,
            [
                "--data-root",
                root,
                "phenotype",
                "summary",
                "kdigo_aki@1.0.0",
                "--tier",
                "fixture",
                "--k",
                "1",
                "--out",
                str(out_dir),
                "--no-agreement",
            ],
        )
        assert with_out.exit_code == 0, with_out.output
        assert (out_dir / phen_runner.PREVALENCE_REPORT).is_file() and "report " in with_out.stdout
        assert "## Agreement" not in (out_dir / phen_runner.PREVALENCE_REPORT).read_text(
            encoding="utf-8"
        )
        unknown = runner.invoke(
            app,
            [
                "--data-root",
                root,
                "phenotype",
                "summary",
                "nope@1.0.0",
                "--tier",
                "fixture",
                "--k",
                "1",
            ],
        )
        assert unknown.exit_code == 2 and "no built version" in unknown.stderr
    finally:
        config.configure()
        _drop_progress_handlers()


def test_lake_concept_pin_mismatch_refused_and_second_version_coexists(
    lake: Settings, registry: Registry, tmp_path: Path
) -> None:
    from mimicwarehouse.catalog.connect import open_catalog
    from mimicwarehouse.loader.manifest import status_path

    settings = lake
    lake_root = settings.lake_root("fixture")
    root = str(settings.data_root)
    runner = helpers.cli_runner()
    status_file = status_path(lake_root)
    original = status_file.read_text(encoding="utf-8")
    before = phen_runner.phenotype_attempt(lake_root, "fixture", "kdigo_aki@1.0.0")
    assert before is not None
    try:
        # a re-compile skips; the tier's concept rebuilt from other SQL refuses the pin
        skipped = runner.invoke(
            app,
            ["--data-root", root, "phenotype", "compile", "kdigo_aki@1.0.0", "--tier", "fixture"],
        )
        assert skipped.exit_code == 0, skipped.output
        after = phen_runner.phenotype_attempt(lake_root, "fixture", "kdigo_aki@1.0.0")
        assert after is not None and after["run_id"] == before["run_id"]
        doc = json.loads(original)
        doc["steps"][f"{DERIVED}.kdigo_stages"]["tiers"]["fixture"]["sql_sha256"] = "0" * 64
        doc["steps"][f"{DERIVED}.kdigo_stages"]["tiers"]["fixture"]["patch_id"] = "crafted"
        status_file.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8", newline="\n")
        mismatch = runner.invoke(
            app,
            [
                "--data-root",
                root,
                "phenotype",
                "compile",
                "kdigo_aki@1.0.0",
                "sepsis3@1.0.0",
                "--tier",
                "fixture",
                "--force",
            ],
        )
        assert mismatch.exit_code == 1, mismatch.output
        assert "ConceptPinMismatchError" in mismatch.stderr or "pins" in mismatch.stderr
        failed = phen_runner.phenotype_attempt(lake_root, "fixture", "kdigo_aki@1.0.0")
        assert failed is not None and failed["status"] == "failed"
        assert failed["error_class"] == "ConceptPinMismatchError"
        rebuilt = phen_runner.phenotype_attempt(lake_root, "fixture", "sepsis3@1.0.0")
        assert rebuilt is not None and rebuilt["status"] == "done", "the other phenotype still ran"
        assert phen_runner.phenotype_part(lake_root, "fixture", "kdigo_aki@1.0.0").is_file(), (
            "the previous table stays intact"
        )
        # the lake-level meta rows carry the failure at once; the catalog's copy of
        # meta.phenotype_versions refreshes at the next catalog step (a failed compile
        # stops the build before it)
        by_ref = {
            f"{r[0]}@{r[1]}": (r[14], r[15])
            for r in phen_runner.versions_rows(lake_root, "fixture", k=1, settings=settings)
        }
        assert by_ref["kdigo_aki@1.0.0"] == ("failed", "ConceptPinMismatchError")
        assert by_ref["sepsis3@1.0.0"] == ("done", None)
        # restore the concept's status and rebuild: done again with the same counts
        status_file.write_text(original, encoding="utf-8", newline="\n")
        restored = runner.invoke(
            app,
            [
                "--data-root",
                root,
                "phenotype",
                "compile",
                "kdigo_aki@1.0.0",
                "--tier",
                "fixture",
                "--force",
            ],
        )
        assert restored.exit_code == 0, restored.output
        done = phen_runner.phenotype_attempt(lake_root, "fixture", "kdigo_aki@1.0.0")
        assert done is not None and done["status"] == "done"
        assert done["n_positive"] == before["n_positive"] and done["run_id"] != before["run_id"]
        # a study's kdigo_aki@1.1.0 (window 336 h) coexists on disk and in meta; the session
        # view follows it and the older version is refused by the summary
        study = tmp_path / "study"
        study.mkdir()
        text = _text(registry.get("kdigo_aki@1.0.0"))
        (study / "kdigo_aki_1_1.yaml").write_text(
            text.replace('version: "1.0.0"', 'version: "1.1.0"').replace(
                "window_hours: 168", "window_hours: 336"
            ),
            encoding="utf-8",
            newline="\n",
        )
        second = runner.invoke(
            app,
            [
                "--data-root",
                root,
                "phenotype",
                "compile",
                "kdigo_aki@1.1.0",
                "--tier",
                "fixture",
                "--defs",
                str(study),
            ],
        )
        assert second.exit_code == 0, second.output
        assert phen_runner.phenotype_part(lake_root, "fixture", "kdigo_aki@1.1.0").is_file()
        assert phen_runner.phenotype_part(lake_root, "fixture", "kdigo_aki@1.0.0").is_file()
        con = open_catalog("fixture", settings=settings)
        try:
            assert phen_runner.latest_versions(con)["kdigo_aki"] == "1.1.0"
            versions = con.execute(
                "SELECT version, status, def_hash FROM meta.phenotype_versions "
                "WHERE phenotype_id = 'kdigo_aki' ORDER BY version"
            ).fetchall()
            assert [(v[0], v[1]) for v in versions] == [("1.0.0", "done"), ("1.1.0", "done")]
            assert versions[0][2] != versions[1][2]
            n_old = _scalar(
                con, 'SELECT count(*) FILTER (WHERE flag) FROM phenotypes."kdigo_aki@1.0.0"'
            )
            n_new = _scalar(
                con, 'SELECT count(*) FILTER (WHERE flag) FROM phenotypes."kdigo_aki@1.1.0"'
            )
            n_view = _scalar(
                con, f"SELECT count(*) FILTER (WHERE flag) FROM {DERIVED}.phenotype_kdigo_aki"
            )
            assert n_view == n_new >= n_old, "a wider window never loses a positive"
        finally:
            con.close()
        latest = phen_runner.summarize("kdigo_aki@1.1.0", tier="fixture", settings=settings, k=1)
        assert latest.ref == "kdigo_aki@1.1.0"
        with pytest.raises(PhenotypeError, match="not the latest built version"):
            phen_runner.summarize("kdigo_aki@1.0.0", tier="fixture", settings=settings, k=1)
    finally:
        status_file.write_text(original, encoding="utf-8", newline="\n")
        config.configure()
        _drop_progress_handlers()
    assert phen_runner.current_options() == phen_runner.CompileOptions()


def test_compile_step_records_each_failure(tmp_path: Path) -> None:
    """No source tables, no concepts: every selected phenotype is attempted and recorded."""
    settings = config.Settings(data_root=tmp_path / "root")
    with phen_runner.compile_options(select=REFS):
        result = runner_mod.run(
            load_dag(), "fixture", select=[phen_runner.STEP_COMPILE], settings=settings
        )
    assert not result.ok and result.steps[0].status == "failed"
    assert "3 of 3 phenotype(s) failed" in (result.steps[0].error or "")
    lake_root = settings.lake_root("fixture")
    for ref, error_class in (
        ("sepsis3@1.0.0", "ConceptNotBuiltError"),
        ("kdigo_aki@1.0.0", "ConceptNotBuiltError"),
    ):
        attempt = phen_runner.phenotype_attempt(lake_root, "fixture", ref)
        assert attempt is not None and attempt["status"] == "failed"
        assert attempt["error_class"] == error_class, ref
    explicit = phen_runner.phenotype_attempt(lake_root, "fixture", "sepsis_explicit@1.0.0")
    assert explicit is not None and explicit["status"] == "failed" and explicit["error_class"]
    rows = phen_runner.versions_rows(lake_root, "fixture", k=11, settings=settings)
    assert {(r[0], r[14], r[15]) for r in rows} >= {
        ("sepsis3", "failed", "ConceptNotBuiltError"),
        ("kdigo_aki", "failed", "ConceptNotBuiltError"),
    }


def test_session_fixture_lake_carries_the_three(
    fixture_lake_settings: Settings,
    fixture_lake_catalog: duckdb.DuckDBPyConnection,
    registry: Registry,
) -> None:
    """The full DAG (conftest's session lake) orders the concepts before the phenotypes."""
    con = fixture_lake_catalog
    latest = phen_runner.latest_versions(con)
    assert {
        "sepsis3": "1.0.0",
        "kdigo_aki": "1.0.0",
        "sepsis_explicit": "1.0.0",
    }.items() <= latest.items()
    views = {
        str(r[0])
        for r in con.execute(
            "SELECT view_name FROM duckdb_views() WHERE schema_name = 'mimiciv_derived'"
        ).fetchall()
    }
    assert {
        "phenotype_sepsis3",
        "phenotype_sepsis3_hadm",
        "phenotype_kdigo_aki",
        "phenotype_kdigo_aki_hadm",
        "phenotype_sepsis_explicit",
        "sepsis3",
        "kdigo_stages",
    } <= views
    rows = {
        r[0]: (r[1], r[2])
        for r in con.execute(
            "SELECT phenotype_id, def_hash, status FROM meta.phenotype_versions"
        ).fetchall()
    }
    for ref in REFS:
        entry = registry.get(ref)
        assert rows[entry.phenotype.id] == (entry.def_hash, "done")
    assert _scalar(con, f"SELECT count(*) FROM {DERIVED}.phenotype_kdigo_aki") == _scalar(
        con, f"SELECT count(*) FROM {ICU}.icustays"
    )
    # both versions tables stamp a recorded derived snapshot although the phenotypes run
    # after meta.concept_versions and move the derived id (EP-42: record_snapshot_once)
    from mimicwarehouse.dag import snapshot as snapshot_mod

    lake = fixture_lake_settings.lake_root("fixture")
    history = snapshot_mod.read_snapshots(lake)
    derived_ids = {
        e["snapshot_id"] for e in history if e["layer"] == "derived" and e["tier"] == "fixture"
    }
    concept_id = _scalar(con, "SELECT DISTINCT snapshot_id FROM meta.concept_versions")
    phenotype_id = _scalar(con, "SELECT DISTINCT snapshot_id FROM meta.phenotype_versions")
    assert concept_id in derived_ids and phenotype_id in derived_ids
    assert concept_id != phenotype_id, "the phenotypes moved the derived layer"
    assert phenotype_id == snapshot_mod.layer_snapshot(lake, "derived", "fixture"), (
        "the phenotype stamp is the end state of the build"
    )
    build_ids = {e["build_id"] for e in history if e["layer"] == "derived"}
    assert len(build_ids) == 1, "one build; its entries are deduplicated by id"
    assert len([e for e in history if e["layer"] == "derived"]) == 2


# ---------------------------------------------------------------------------
# 8. Docs in sync
# ---------------------------------------------------------------------------


def test_methods_doc_in_sync(tmp_path: Path, registry: Registry) -> None:
    path = phen_runner.methods_doc_path()
    text = path.read_text(encoding="utf-8")
    for needle in (
        "parameters",
        "$window_hours",
        "window",
        "evidence",
        "levels",
        "concepts pinned",
        "sepsis3@1.0.0",
        "kdigo_aki@1.0.0",
        "sepsis_explicit@1.0.0",
        "max_stage_in_window",
        "phenotype_prevalence.md",
        "--report",
        "agreement",
        "EP-42",
        "EP-43",
        "EP-68",
        "not adjudicated",
        "under-ascertain",
    ):
        assert needle in text, needle
    copy = tmp_path / "phenotypes.md"
    copy.write_text(text, encoding="utf-8", newline="\n")
    phen_runner.sync_methods_doc(copy)
    assert copy.read_text(encoding="utf-8") == text, "re-run `python -m mimicwarehouse.phenotypes`"
    cards = phen_runner.render_cards(registry)
    assert cards.rstrip("\n") in text
    for ref in REFS:
        assert f"### `{ref}`" in cards
    assert (
        "**concepts pinned**" in cards
        and "**parameters**" in cards
        and "**evidence columns**" in cards
    )
    assert not BAND_TOKEN.search(text)
    coverage = (helpers.WORKSPACE / "tests" / "fixtures" / "COVERAGE.md").read_text(
        encoding="utf-8"
    )
    assert "EP-42" in coverage


# ---------------------------------------------------------------------------
# 9. Dev / full tiers: the three on the real catalogs (aggregates only)
# ---------------------------------------------------------------------------


def _real_tier_probe(tier: str, registry: Registry) -> None:
    settings = config.load_settings()
    remedy = f"run `mwh phenotype compile {' '.join(REFS)} --tier {tier}` (EP-42) first"
    versions = phen_runner.built_versions(tier, settings=settings, actor="test_ep42")
    for ref in REFS:
        phenotype_id = ref.partition("@")[0]
        mine = [v for v in versions if v["phenotype_id"] == phenotype_id and v["status"] == "done"]
        assert mine, remedy
        latest = max(mine, key=lambda v: phen_runner.semver_key(str(v["version"])))
        entry = registry.get(f"{phenotype_id}@{latest['version']}")
        assert latest["def_hash"] == entry.def_hash, f"{tier}: {ref} carries the current def_hash"
    bundle = phen_runner.prevalence_report(
        REFS, tier=tier, settings=settings, actor="test_ep42", registry=registry
    )
    assert bundle.k >= 11
    for s in bundle.summaries:
        total = s.df.filter(s.df["scope"] == "all")
        assert total.height == 1
        n_units, n_positive = int(total["n_units"][0]), int(total["n_positive"][0])
        assert n_units >= 11 and 0 < n_positive < n_units
        print(
            f"{tier}: {s.ref} n_units {n_units:,}, n_positive {n_positive:,}, "
            f"share {n_positive / n_units:.1%}"
        )
        for row in s.df.filter(s.df["scope"] != "all").to_dicts():
            assert int(row["n_positive"]) >= 11 and int(row["n_units"]) >= 11
    assert len(bundle.distributions) == 1
    for row in bundle.distributions[0].df.to_dicts():
        assert int(row["n_units"]) >= 11
        print(f"{tier}: stage {row['level']} n_units {int(row['n_units']):,}")
    pair = next(
        a
        for a in bundle.agreements
        if a.ref_b.startswith("sepsis_explicit") and a.ref_a.startswith("sepsis3")
    )
    assert not pair.suppressed
    assert pair.n_hadm is not None and pair.n_both is not None
    assert min(pair.n_both, pair.n_a_only or 0, pair.n_b_only or 0, pair.n_neither or 0) >= 11
    print(f"{tier}: sepsis3 x explicit n_hadm {pair.n_hadm:,}, both {pair.n_both:,}")


@pytest.mark.tier("dev")
def test_dev_compiled_phenotypes_and_report(dev_catalog: Path, registry: Registry) -> None:
    _real_tier_probe("dev", registry)


@pytest.mark.tier("full")
def test_full_compiled_phenotypes_and_report(full_catalog: Path, registry: Registry) -> None:
    _real_tier_probe("full", registry)
