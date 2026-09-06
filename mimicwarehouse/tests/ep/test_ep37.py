"""EP-37 — concept runner (mimic-code concepts_duckdb -> mimiciv_derived).

Fixture tier (default): the inventory is acyclic, covers every vendored concept file,
carries VENDOR.json's hashes and pin, and its two generated files (``concepts.yaml``,
``dag/specs/concepts.yaml``) equal a fresh render; header stripping on crafted files
and on every vendored file; spec discovery (``load_dag()`` merges the packaged specs,
the shared ``catalog`` step's dependencies and tags are unioned, a duplicate
non-shared step is refused, ``--with-deps`` pulls ancestors); ``--keep-going`` records a
crafted failure and blocks its dependents; the session fixture lake carries all 65
concept views + ``meta.concept_versions`` (one row per attempted concept, upstream
commit set); a ``--select … --with-deps`` build under a provenance run writes the
manifest line, the per-tier status entry, the ``derived`` snapshot, the ``kind:
concept`` benchmark line with the run id, skips on rerun and rebuilds on ``--force``; a
failing concept is recorded with a **sanitized** error; the pin helpers and the
committed demo pin file. ``@pytest.mark.demo``: the demo concepts rebuild and the pins
equal the committed file. ``tier("dev")``: ``--select concept.sepsis.sepsis3
--with-deps --force`` rebuilds only it, ``meta.concept_versions`` lists every concept
through ``safe_query``, and the dev pins are written / compared under ``runs/pins``.

Everything asserted or printed is counts, hashes, schemas, statuses and SQL text — the
fixture data is synthetic (ids >= 90 000 000); the dev/demo tests see only
k-suppressed aggregates through ``safe_query``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
import yaml

import helpers
from mimicwarehouse import config
from mimicwarehouse.cli import app
from mimicwarehouse.concepts import inventory as inv_mod
from mimicwarehouse.concepts import patching, vendor_manifest
from mimicwarehouse.concepts import pins as pins_mod
from mimicwarehouse.concepts import runner as concepts_runner
from mimicwarehouse.concepts.inventory import (
    DRIVER_FILENAME,
    Concept,
    InventoryError,
    discover,
    drift,
    load_inventory,
)
from mimicwarehouse.concepts.runner import ConceptError, strip_header
from mimicwarehouse.dag import benchmarks as benchmarks_mod
from mimicwarehouse.dag import runner as runner_mod
from mimicwarehouse.dag import snapshot as snapshot_mod
from mimicwarehouse.dag import spec as spec_mod
from mimicwarehouse.dag.spec import DagError, DagSpec, load_dag
from mimicwarehouse.loader import manifest as manifest_mod

if TYPE_CHECKING:
    import duckdb as duckdb_mod

    from mimicwarehouse.config import Settings
    from mimicwarehouse.schema.contract import Contract

pytestmark = pytest.mark.ep_37

HOSP = "mimiciv_hosp"
ICU = "mimiciv_icu"
DERIVED = "mimiciv_derived"
#: The five concepts the brief names for the fixture run (item 6).
NAMED = ("icustay_detail", "age", "charlson", "sofa", "sepsis3")
CRAFTED_HEADER = (
    "-- THIS SCRIPT IS AUTOMATICALLY GENERATED. DO NOT EDIT IT DIRECTLY.\n"
    "DROP TABLE IF EXISTS mimiciv_derived.crafted; CREATE TABLE mimiciv_derived.crafted AS\n"
)


@pytest.fixture
def data_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    root = helpers.tmp_data_root(monkeypatch, tmp_path)
    yield root
    config.configure()


@pytest.fixture(scope="session")
def fixture_manifest(fixture_root: Path) -> dict[str, Any]:
    return json.loads((fixture_root / "manifest.json").read_text(encoding="utf-8"))


def _rows(manifest: dict[str, Any], contract: Contract, schema: str, table: str) -> int:
    csv = f"mimic-iv-3.1/{contract.table(schema, table).csv_path}"
    return int(manifest["files"][csv]["rows"])


def _scalar(con: duckdb_mod.DuckDBPyConnection, sql: str) -> Any:
    row = con.execute(sql).fetchone()
    assert row is not None
    return row[0]


def _objects(con: duckdb_mod.DuckDBPyConnection, *schemas: str) -> dict[str, str]:
    rows = con.execute(
        "SELECT table_schema || '.' || table_name, table_type FROM information_schema.tables "
        "WHERE table_schema IN (SELECT unnest(?))",
        [list(schemas)],
    ).fetchall()
    return {str(name): str(kind) for name, kind in rows}


# ---------------------------------------------------------------------------
# 1. Inventory: acyclic, complete, pinned, generated files in sync
# ---------------------------------------------------------------------------


def test_inventory_covers_every_vendored_file_and_is_acyclic() -> None:
    inv = load_inventory()
    fresh = discover()
    assert drift(inv, fresh) == [], "regenerate: python -m mimicwarehouse.concepts.inventory"
    root = inv_mod.concepts_root()
    files = sorted(p for p in root.rglob("*.sql") if p.name != DRIVER_FILENAME)
    assert len(inv.concepts) == len(files) == len(inv_mod.driver_order()) > 0
    assert {c.path for c in inv.concepts} == {
        f"{inv_mod.CONCEPTS_DIR}/{p.parent.name}/{p.name}" for p in files
    }
    # every dependency precedes its dependent; every reference resolves
    position = {c.name: i for i, c in enumerate(inv.concepts)}
    for c in inv.concepts:
        for dep in c.depends_on:
            assert position[dep] < position[c.name], f"{c.name} runs before {dep}"
        assert c.name not in c.depends_on
    # the pin and the hashes are VENDOR.json's
    manifest = vendor_manifest()
    sha_by_path = {f["path"]: f["sha256_lf"] for f in manifest["files"]}
    for c in inv.concepts:
        assert c.upstream_commit == manifest["upstream_commit"] == inv.upstream_commit
        assert c.sql_sha256 == sha_by_path[c.path], c.path
    # the brief's named concepts are there with the dependencies the SQL declares
    by_name = inv.by_name()
    assert set(NAMED) <= set(by_name)
    assert set(by_name["sepsis3"].depends_on) == {"sofa", "suspicion_of_infection"}
    assert by_name["age"].depends_on == () and set(by_name["age"].sources) == {
        f"{HOSP}.admissions",
        f"{HOSP}.patients",
    }
    assert by_name["sofa"].step_name == "concept.score.sofa"
    assert inv.for_step("concept.sepsis.sepsis3") is by_name["sepsis3"]
    with pytest.raises(InventoryError):
        inv.for_step("concept.score.sepsis3")


def test_generated_files_in_sync() -> None:
    fresh = discover()
    committed = yaml.safe_load(inv_mod.inventory_path().read_text(encoding="utf-8"))
    assert committed == inv_mod.render_inventory(fresh)
    spec = yaml.safe_load(inv_mod.spec_path().read_text(encoding="utf-8"))
    assert spec == inv_mod.render_spec(fresh)
    text = inv_mod.inventory_path().read_text(encoding="utf-8")
    assert text.isascii() and "\r" not in text
    assert not re.search(r"(?<![\w.])[123]\d{7}(?![\w.])", text), "no band-shaped integers"


def test_topological_order_refuses_cycle_and_unknown() -> None:
    def concept(name: str, *deps: str, order: int = 0) -> Concept:
        return Concept(
            name=name,
            group="g",
            path=f"{inv_mod.CONCEPTS_DIR}/g/{name}.sql",
            sql_sha256="0" * 64,
            upstream_commit="1" * 40,
            depends_on=tuple(deps),
            driver_order=order,
        )

    with pytest.raises(InventoryError, match="cycle"):
        inv_mod.topological_order([concept("a", "b"), concept("b", "a")])
    with pytest.raises(InventoryError, match="unknown"):
        inv_mod.topological_order([concept("a", "ghost")])
    # ties follow the driver order; dependencies still win over it
    ordered = inv_mod.topological_order(
        [concept("c", order=0), concept("a", "b", order=1), concept("b", order=2)]
    )
    assert [c.name for c in ordered] == ["c", "b", "a"]
    derived, core = inv_mod.scan_references(
        "-- mimiciv_derived.commented\nSELECT * FROM mimiciv_derived.x JOIN MIMICIV_HOSP.Y"
    )
    assert derived == {"x"} and core == {"mimiciv_hosp.y"}


# ---------------------------------------------------------------------------
# 2. Header stripping
# ---------------------------------------------------------------------------


def test_strip_header_on_crafted_and_vendored_files() -> None:
    target, body = strip_header(CRAFTED_HEADER + "SELECT 1 AS one\n;\n")
    assert target == "crafted" and body == "SELECT 1 AS one"
    with pytest.raises(ConceptError, match="drops"):
        strip_header(
            "DROP TABLE IF EXISTS mimiciv_derived.a; CREATE TABLE mimiciv_derived.b AS SELECT 1"
        )
    with pytest.raises(ConceptError, match="header"):
        strip_header("SELECT 1")
    with pytest.raises(ConceptError, match="empty"):
        strip_header(CRAFTED_HEADER)
    for c in load_inventory().concepts:
        name, body = strip_header(concepts_runner.load_concept_sql(c))
        assert name == c.name and body.upper().startswith(("SELECT", "WITH")), c.name


# ---------------------------------------------------------------------------
# 3. Spec discovery: the merged DAG, the shared catalog step, --with-deps
# ---------------------------------------------------------------------------


def test_merged_dag_shape() -> None:
    dag = load_dag()
    inv = load_inventory()
    stage_names = {s.name for s in dag.steps if s.kind == "stage"}
    concept_steps = {c.step_name for c in inv.concepts}
    names = {s.name for s in dag.steps}
    assert concept_steps <= names and "meta.concept_versions" in names
    catalog = dag.step("catalog")
    assert concept_steps | stage_names | {"meta.profile", "meta.concept_versions"} <= set(
        catalog.depends_on
    )
    assert {"catalog", "concepts"} <= set(catalog.tags)
    assert sum(1 for s in dag.steps if s.name == "catalog") == 1
    for c in inv.concepts:
        step = dag.step(c.step_name)
        assert step.kind == "python" and step.callable_name == inv_mod.CONCEPT_CALLABLE
        assert step.qualified_table == step.target == c.qualified_name
        assert {"concepts", c.group} <= set(step.tags)
        assert step.tiers == ("fixture", "demo", "dev", "full")
        for dep in c.depends_on:
            assert inv.concept(dep).step_name in step.depends_on
        for src in c.sources:
            assert f"stage.{src}" in step.depends_on
    versions = dag.step("meta.concept_versions")
    assert versions.qualified_table is None and set(versions.depends_on) == concept_steps
    # --tag concepts reaches the shared catalog step, last
    tagged = [s.name for s in dag.ordered(tags=["concepts"], tier="fixture")]
    assert tagged[-1] == "catalog" and tagged[-2] == "meta.concept_versions"
    assert set(tagged) == concept_steps | {"meta.concept_versions", "catalog"}
    # the single-spec form is unchanged
    stage_only = load_dag("stage")
    assert not {s.name for s in stage_only.steps} & concept_steps
    assert set(stage_only.step("catalog").depends_on) == stage_names | {"meta.profile"}


def test_with_deps_pulls_transitive_ancestors() -> None:
    dag = load_dag()
    alone = [s.name for s in dag.ordered(select=["concept.sepsis.sepsis3"])]
    assert alone == ["concept.sepsis.sepsis3"]
    pulled = [s.name for s in dag.ordered(select=["concept.sepsis.sepsis3"], with_deps=True)]
    assert pulled[-1] == "concept.sepsis.sepsis3"
    assert {"concept.score.sofa", "concept.sepsis.suspicion_of_infection"} <= set(pulled)
    assert f"stage.{ICU}.icustays" in pulled and "catalog" not in pulled
    assert dag.ancestors(["concept.demographics.age"]) == {
        f"stage.{HOSP}.admissions",
        f"stage.{HOSP}.patients",
    }


def _write_spec(path: Path, steps: list[dict[str, Any]]) -> None:
    path.write_text(yaml.safe_dump({"version": 1, "steps": steps}), encoding="utf-8")


def test_merge_rules_on_crafted_specs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    specs = tmp_path / "specs"
    specs.mkdir()
    monkeypatch.setattr(spec_mod, "specs_root", lambda: specs)
    stage = [
        {"name": "a", "kind": "catalog", "tags": ["one"]},
        {"name": "catalog", "kind": "catalog", "tags": ["catalog"], "depends_on": ["a"]},
    ]
    _write_spec(specs / "stage.yaml", stage)
    _write_spec(
        specs / "zz.yaml",
        [
            {"name": "b", "kind": "catalog", "depends_on": ["a"]},  # cross-file reference
            {"name": "catalog", "kind": "catalog", "tags": ["zz"], "depends_on": ["b"]},
        ],
    )
    dag = load_dag()
    assert [s.name for s in dag.ordered()] == ["a", "b", "catalog"]
    assert dag.step("catalog").depends_on == ("a", "b")
    assert dag.step("catalog").tags == ("catalog", "zz")
    assert [p.name for p in spec_mod.spec_paths()] == ["stage.yaml", "zz.yaml"]

    _write_spec(specs / "zz.yaml", [{"name": "a", "kind": "catalog"}])
    with pytest.raises(DagError, match=re.escape("both stage.yaml and zz.yaml")):
        load_dag()
    _write_spec(specs / "zz.yaml", [{"name": "catalog", "kind": "python", "callable": "m:f"}])
    with pytest.raises(DagError, match="differs"):
        load_dag()
    # the python `target` field is optional and becomes the status key
    _write_spec(
        specs / "zz.yaml",
        [{"name": "p", "kind": "python", "callable": "m:f", "target": "x.y"}],
    )
    assert load_dag().step("p").qualified_table == "x.y"
    with pytest.raises(ValueError, match="belong to another kind"):
        DagSpec.model_validate(
            {"version": 1, "steps": [{"name": "s", "kind": "catalog", "target": "x.y"}]}
        )


# ---------------------------------------------------------------------------
# 4. --keep-going: a failure is recorded, dependents are blocked, the rest runs
# ---------------------------------------------------------------------------


def test_keep_going_records_failure_and_blocks_dependents(
    settings_root: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def dispatcher(step, ctx):
        calls.append(step.name)
        if step.name == "a":
            raise RuntimeError("crafted failure")
        return runner_mod.StepOutcome(rows=1)

    monkeypatch.setitem(runner_mod.STEP_HANDLERS, "python", dispatcher)
    monkeypatch.setitem(runner_mod.STEP_HANDLERS, "catalog", dispatcher)
    dag = DagSpec.model_validate(
        {
            "version": 1,
            "steps": [
                {"name": "a", "kind": "python", "callable": "m:f"},
                {"name": "b", "kind": "python", "callable": "m:f", "depends_on": ["a"]},
                {"name": "c", "kind": "python", "callable": "m:f", "depends_on": ["b"]},
                {"name": "d", "kind": "python", "callable": "m:f"},
                {"name": "catalog", "kind": "catalog", "depends_on": ["c", "d"]},
            ],
        }
    )
    result = runner_mod.run(dag, "fixture", settings=settings_root, keep_going=True)
    by_name = {s.name: s for s in result.steps}
    assert by_name["a"].status == "failed" and "crafted failure" in (by_name["a"].error or "")
    assert by_name["b"].status == "blocked" and by_name["b"].error == "blocked by a"
    assert by_name["c"].status == "blocked" and by_name["c"].error == "blocked by b"
    assert by_name["d"].status == "done"
    assert by_name["catalog"].status == "done", "a catalog step registers what is complete"
    assert calls == ["a", "d", "catalog"] and not result.ok and result.snapshot_ids == {}
    ledger = benchmarks_mod.read(settings_root)
    assert set(ledger.filter(ledger["kind"] != "build")["step"].to_list()) == {"a", "d", "catalog"}

    calls.clear()
    stopped = runner_mod.run(dag, "fixture", settings=settings_root)
    assert [s.status for s in stopped.steps] == ["failed"] and calls == ["a"]


@pytest.fixture
def settings_root(tmp_path: Path) -> Settings:
    from mimicwarehouse.config import Settings

    return Settings(data_root=tmp_path / "root")


# ---------------------------------------------------------------------------
# 5. The fixture lake: every concept built, registered, versioned
# ---------------------------------------------------------------------------


def test_fixture_lake_carries_every_concept(
    fixture_lake_settings: Settings,
    fixture_lake_catalog: duckdb_mod.DuckDBPyConnection,
    fixture_manifest: dict[str, Any],
    contract: Contract,
) -> None:
    con = fixture_lake_catalog
    inv = load_inventory()
    objects = _objects(con, DERIVED, "meta")
    for c in inv.concepts:
        assert objects.get(c.qualified_name) == "VIEW", c.name
    assert objects.get("meta.concept_versions") == "BASE TABLE"
    assert "meta.profile_tables" not in objects and "meta.profile_columns" not in objects
    for name in NAMED:
        assert _scalar(con, f"SELECT count(*) FROM {DERIVED}.{name}") >= 0
    # age = one row per admission with a patient; icustay_detail = one per ICU stay
    assert _scalar(con, f"SELECT count(*) FROM {DERIVED}.age") == _rows(
        fixture_manifest, contract, HOSP, "admissions"
    )
    assert _scalar(con, f"SELECT count(*) FROM {DERIVED}.icustay_detail") == _rows(
        fixture_manifest, contract, ICU, "icustays"
    )
    rows = con.execute(
        'SELECT concept, "group", upstream_commit, sql_sha256, patch_id, rows, built_at, '
        "run_id, snapshot_id, status, error_class, build_id FROM meta.concept_versions "
        "ORDER BY concept"
    ).fetchall()
    assert [r[0] for r in rows] == sorted(c.name for c in inv.concepts)
    by_name = inv.by_name()
    # EP-38 (2026-09-06, churn rule EP-168): a concept with a registry patch executes the
    # patch's SQL, so its row carries the patch id and the patch file's sha256; the rest
    # keep the vendored hash and a NULL patch_id (EP-37's "NULL until EP-38" pin).
    patched = patching.load_registry().by_concept()
    lake = fixture_lake_settings.lake_root("fixture")
    derived_ids = {
        e["snapshot_id"]
        for e in snapshot_mod.read_snapshots(lake)
        if e["layer"] == "derived" and e["tier"] == "fixture"
    }
    for (
        concept,
        group,
        commit,
        sha,
        patch,
        n,
        built_at,
        _run_id,
        snapshot_id,
        status,
        err,
        b,
    ) in rows:
        c = by_name[concept]
        assert (group, commit) == (c.group, c.upstream_commit)
        expected_patch = patched.get(concept)
        if expected_patch is None:
            assert patch is None and sha == c.sql_sha256
        else:
            assert patch == expected_patch.patch_id and sha == expected_patch.sql_sha256
        assert status == "done" and err is None
        assert n is not None and n >= 0 and built_at and b
        assert snapshot_id in derived_ids
    comment = _scalar(
        con,
        f"SELECT comment FROM duckdb_views() WHERE schema_name = '{DERIVED}' "
        "AND view_name = 'sepsis3'",
    )
    assert comment and "EP-37" in comment and inv.upstream_commit[:12] in comment
    # status.json: per-tier entries, complete for the fixture tier
    status = manifest_mod.read_status(lake)["steps"]
    for c in inv.concepts:
        entry = status[c.qualified_name]
        assert entry["per_tier"] is True and entry["tier_complete"] == "full"
        assert entry["tiers"]["fixture"]["status"] == "done"
        assert snapshot_mod.complete_for_tier(entry, "fixture")
        assert concepts_runner.derived_part(lake, "fixture", DERIVED, c.name).is_file()


def test_catalog_info_and_sql_list_the_concepts(fixture_lake_settings: Settings) -> None:
    runner = helpers.cli_runner()
    root = ["--data-root", str(fixture_lake_settings.data_root)]
    try:
        info = runner.invoke(app, [*root, "catalog", "info", "--tier", "fixture", "--json"])
        assert info.exit_code == 0, info.output
        objects = {
            f"{o['schema']}.{o['table']}": o["kind"] for o in json.loads(info.output)["objects"]
        }
        assert objects[f"{DERIVED}.sofa"] == "view" and objects["meta.concept_versions"] == "table"
        listed = runner.invoke(
            app,
            [
                *root,
                "sql",
                "--tier",
                "fixture",
                "--format",
                "json",
                "SELECT concept, rows FROM meta.concept_versions ORDER BY 1",
            ],
        )
        assert listed.exit_code == 0, listed.output
        names = [row["concept"] for row in json.loads(listed.output)["rows"]]
        assert names == sorted(c.name for c in load_inventory().concepts)
        plan = runner.invoke(
            app, [*root, "build", "--tier", "fixture", "--dry-run", "--tag", "concepts"]
        )
        assert plan.exit_code == 0, plan.output
        assert plan.output.rstrip().endswith("catalog (catalog)")
        assert "concept.sepsis.sepsis3 (python)" in plan.output
        bad = runner.invoke(app, [*root, "build", "--tier", "fixture", "--with-deps", "--dry-run"])
        assert bad.exit_code == 2 and "--with-deps" in bad.output
    finally:
        config.configure()


# ---------------------------------------------------------------------------
# 6. --select --with-deps under a provenance run: manifests, status, ledger, resume
# ---------------------------------------------------------------------------


def test_select_with_deps_provenance_and_resume(settings_root: Settings) -> None:
    from mimicwarehouse import run as run_mod

    settings = settings_root
    dag = load_dag()
    step = "concept.demographics.age"
    result = runner_mod.run(
        dag, "fixture", select=[step], with_deps=True, settings=settings, provenance=True
    )
    assert result.ok and result.run_id is not None
    statuses = {s.name: s.status for s in result.steps}
    assert statuses == {
        f"stage.{HOSP}.patients": "done",
        f"stage.{HOSP}.admissions": "done",
        step: "done",
    }
    assert set(result.snapshot_ids) == {"core", "derived"}
    lake = settings.lake_root("fixture")
    part = concepts_runner.derived_part(lake, "fixture", DERIVED, "age")
    assert part.is_file()
    rel = manifest_mod.lake_relative_posix(part, lake)
    assert rel == f"derived/fixture/{DERIVED}/age/part-0.parquet"
    lines = list(manifest_mod.iter_manifest(manifest_mod.manifest_path(lake, result.build_id)))
    derived_lines = [ln for ln in lines if ln.schema_name == DERIVED]
    assert len(derived_lines) == 1
    line = derived_lines[0]
    assert line.table == "age" and line.path == rel and line.bytes == part.stat().st_size
    assert line.source_sha256 == load_inventory().concept("age").sql_sha256
    assert line.raw_snapshot_id == result.snapshot_ids["core"]
    entry = manifest_mod.read_status(lake)["steps"][f"{DERIVED}.age"]
    assert entry["per_tier"] is True and entry["tier_complete"] == "full"
    attempt = entry["tiers"]["fixture"]
    assert attempt["status"] == "done" and attempt["run_id"] == result.run_id
    assert attempt["rows"] == line.rows and attempt["build_id"] == result.build_id
    history = snapshot_mod.read_snapshots(lake)
    assert {(e["layer"], e["tier"]) for e in history} == {
        ("core", "fixture"),
        ("derived", "fixture"),
    }
    # the run manifest: kind build, refs the concept, both snapshot ids
    m = run_mod.read_manifest(result.run_id, settings)
    assert m.kind == "build" and m.status == "ok" and m.params["build_id"] == result.build_id
    assert [(r.kind, r.name) for r in m.refs] == [("concept", "age")]
    assert m.snapshot_ids == result.snapshot_ids
    # the benchmark ledger: a concept line citing the run, beside the runner's python line
    ledger = benchmarks_mod.read(settings)
    concept_lines = ledger.filter(ledger["kind"] == "concept")
    assert concept_lines.height == 1
    rec = concept_lines.to_dicts()[0]
    assert rec["step"] == step and rec["run_id"] == result.run_id and rec["ok"]
    assert rec["rows"] == line.rows and rec["files"] == 1 and rec["wall_s"] > 0
    summary = benchmarks_mod.summarize(settings, tier="fixture", kind="concept")
    assert summary["step"].to_list() == [step]

    # rerun: everything complete -> skipped, the derived id does not move
    again = runner_mod.run(dag, "fixture", select=[step], with_deps=True, settings=settings)
    assert {s.status for s in again.steps} == {"skipped"}
    assert again.snapshot_ids == result.snapshot_ids
    # --force with --with-deps rebuilds the selected step only
    forced = runner_mod.run(
        dag, "fixture", select=[step], with_deps=True, force=True, settings=settings
    )
    by_name = {s.name: s.status for s in forced.steps}
    assert by_name[step] == "done" and by_name[f"stage.{HOSP}.patients"] == "skipped"
    assert forced.snapshot_ids["derived"] == result.snapshot_ids["derived"], "logical id"
    # a dependent whose ancestor is not built is refused with the remedy
    refused = runner_mod.run(
        dag, "fixture", select=["concept.demographics.icustay_hourly"], settings=settings
    )
    assert not refused.ok
    assert "not built for tier fixture" in (refused.steps[0].error or "")


def test_failure_is_recorded_and_sanitized(
    settings_root: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = settings_root
    dag = load_dag()
    step = "concept.demographics.age"
    runner_mod.run(
        dag,
        "fixture",
        select=[f"stage.{HOSP}.patients", f"stage.{HOSP}.admissions"],
        settings=settings,
    )
    crafted = CRAFTED_HEADER.replace("crafted", "age") + "SELECT CAST('abc-value' AS INTEGER) AS x"
    monkeypatch.setattr(concepts_runner, "load_concept_sql", lambda concept: crafted)
    result = runner_mod.run(dag, "fixture", select=[step], settings=settings)
    report = result.steps[0]
    assert report.status == "failed" and report.error is not None
    assert "abc-value" not in report.error and "'...'" in report.error, report.error
    assert report.error.startswith("ConceptError: age: ConversionException")
    lake = settings.lake_root("fixture")
    entry = manifest_mod.read_status(lake)["steps"][f"{DERIVED}.age"]
    attempt = entry["tiers"]["fixture"]
    assert attempt["status"] == "failed" and attempt["error_class"] == "ConversionException"
    assert not snapshot_mod.complete_for_tier(entry, "fixture")
    assert not concepts_runner.derived_table_dir(lake, "fixture", DERIVED, "age").exists()
    ledger = benchmarks_mod.read(settings)
    failed = ledger.filter((ledger["kind"] == "concept") & ~ledger["ok"])
    assert failed.height == 1 and failed["error"].to_list() == ["ConversionException"]
    # meta.concept_versions lists the failure (never hidden)
    versions = runner_mod.run(dag, "fixture", select=["meta.concept_versions"], settings=settings)
    assert versions.ok
    rows = concepts_runner.concept_versions_rows(lake, "fixture", settings=settings)
    assert [(r[0], r[9], r[10]) for r in rows] == [("age", "failed", "ConversionException")]


# ---------------------------------------------------------------------------
# 7. Pins: the helper, a fixture pin round trip, the committed demo file
# ---------------------------------------------------------------------------


def test_render_cell_rule() -> None:
    assert pins_mod.render_cell(None) is None
    assert pins_mod.render_cell(0) == 0
    assert [pins_mod.render_cell(n) for n in (1, 5, 10)] == ["<11"] * 3
    assert pins_mod.render_cell(11) == 11 and pins_mod.render_cell(120) == 120
    assert pins_mod.render_cell(3, k=3) == 3


def test_pins_on_fixture_lake(fixture_lake_settings: Settings, tmp_path: Path) -> None:
    inv = load_inventory()
    pins, diffs, created = pins_mod.write_or_compare(
        "fixture", tmp_path / "pins.json", fixture_lake_settings
    )
    assert created and diffs == []
    assert set(pins["counts"]) == {c.name for c in inv.concepts}
    assert pins["concepts"] == len(inv.concepts) and pins["k"] == 11
    for value in pins["counts"].values():
        assert value == "<11" or (isinstance(value, int) and (value == 0 or value >= 11))
    assert pins["sepsis3_true"] is not None and pins["kdigo_max_stage"] is not None
    assert set(pins["charlson"]) == {"n", "mean_index"}
    _, diffs, created = pins_mod.write_or_compare(
        "fixture", tmp_path / "pins.json", fixture_lake_settings
    )
    assert not created and diffs == [], "a rebuild-free recomputation is byte-for-byte stable"
    expected = pins_mod.read_pins(tmp_path / "pins.json")
    expected["counts"]["age"] = "<11" if expected["counts"]["age"] != "<11" else 99
    assert any(d.startswith("counts.age") for d in pins_mod.compare_pins(pins, expected))


def test_demo_pins_file_is_committed_and_suppressed() -> None:
    path = pins_mod.demo_pins_path()
    assert path.is_file(), "tests/ep/pins/concepts_demo.json is generated by the demo build"
    pins = pins_mod.read_pins(path)
    inv = load_inventory()
    assert pins["tier"] == "demo" and pins["upstream_commit"] == inv.upstream_commit
    assert set(pins["counts"]) == {c.name for c in inv.concepts}
    cells = [
        *pins["counts"].values(),
        pins["sepsis3_true"],
        pins["charlson"]["n"],
        *(pins["kdigo_max_stage"] or {}).values(),
    ]
    for value in cells:
        assert value == "<11" or (isinstance(value, int) and (value == 0 or value >= 11)), value
    text = path.read_text(encoding="utf-8")
    assert not re.search(r"(?<![\w.])[123]\d{7}(?![\w.])", text)


@pytest.mark.demo
def test_demo_concepts_rebuild_and_match_the_pins() -> None:
    settings = config.load_settings()
    result = runner_mod.run(load_dag(), "demo", tags=["concepts"], force=True, settings=settings)
    assert result.ok, [f"{s.name}: {s.error}" for s in result.steps if s.error]
    pins = pins_mod.compute_pins("demo", settings)
    assert pins_mod.compare_pins(pins, pins_mod.read_pins(pins_mod.demo_pins_path())) == []


# ---------------------------------------------------------------------------
# 8. Hygiene: import budget, the docs table
# ---------------------------------------------------------------------------


def test_import_budget() -> None:
    helpers.assert_import_budget(
        lazy=("mimicwarehouse.concepts.inventory", "mimicwarehouse.concepts.runner")
    )
    helpers.assert_import_budget("mimicwarehouse.concepts.inventory")
    helpers.assert_import_budget("mimicwarehouse.concepts.runner")


def test_docs_inventory_table_in_sync() -> None:
    from mimicwarehouse.config import workspace_root

    path = workspace_root() / "docs" / "resources" / "concepts.md"
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    inv = load_inventory()
    assert inv_mod.render_inventory_table(inv) in text, "regenerate the concepts.md table block"
    assert inv.upstream_commit in text and "1.5.5" in text
    for c in inv.concepts:
        assert f"`{c.name}`" in text
    assert not re.search(r"(?<![\w.])[123]\d{7}(?![\w.])", text)


# ---------------------------------------------------------------------------
# 9. Dev tier: one concept rebuilt, versions listed, pins written / compared
# ---------------------------------------------------------------------------


@pytest.mark.tier("dev")
def test_dev_select_sepsis3_rebuilds_only_it(dev_catalog: Path) -> None:
    proc = helpers.fresh_interpreter(
        [
            "-m",
            "mimicwarehouse.cli",
            "build",
            "--tier",
            "dev",
            "--select",
            "concept.sepsis.sepsis3",
            "--with-deps",
            "--force",
        ]
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    statuses: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        cells = [c.strip() for c in line.split("│")]
        if len(cells) >= 4 and cells[3] in ("done", "skipped", "failed", "blocked"):
            statuses[cells[1]] = cells[3]
    assert statuses.get("concept.sepsis.sepsis3") == "done", proc.stdout
    assert [n for n, s in statuses.items() if s == "done"] == ["concept.sepsis.sepsis3"]
    assert "skipped" in statuses.values() and "snapshot derived/dev" in proc.stdout


@pytest.mark.tier("dev")
def test_dev_concept_versions_and_pins(dev_catalog: Path) -> None:
    from mimicwarehouse.safe import safe_query

    settings = config.load_settings()
    inv = load_inventory()
    listed = safe_query(
        "SELECT concept, rows, status, error_class FROM meta.concept_versions ORDER BY 1",
        tier="dev",
        actor="test_ep37",
        settings=settings,
    )
    assert listed.df["concept"].to_list() == sorted(c.name for c in inv.concepts), (
        "rebuild with `mwh build --tier dev --tag concepts`"
    )
    for status, err in zip(
        listed.df["status"].to_list(), listed.df["error_class"].to_list(), strict=True
    ):
        assert status in ("done", "failed") and (err is None) == (status == "done")
    pins, diffs, created = pins_mod.write_or_compare(
        "dev", pins_mod.dev_pins_path(settings), settings
    )
    assert set(pins["counts"]) == {
        c.name for c in inv.concepts if pins["counts"].get(c.name) is not None
    }
    assert diffs == [], diffs
    print(f"dev pins {'written' if created else 'compared'}: {pins_mod.dev_pins_path(settings)}")
