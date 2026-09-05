"""EP-37 — concept runner (mimic-code ``concepts_duckdb`` -> ``mimiciv_derived``).

Fixture tier (default): the inventory scan covers every vendored file, is acyclic and
matches the committed ``concepts.yaml`` / ``dag/specs/concepts.yaml`` / docs table; the
header stripper works on crafted files and refuses malformed ones; ``load_dag()`` merges
the spec files with one shared ``catalog`` step; ``--with-deps`` closes the selection over
its ancestors and ``--force`` then applies to the explicit steps only; ``--keep-going``
records a failure and blocks its dependents on a crafted DAG; the session fixture lake
(built by the merged DAG) holds the five named concepts under
``lake/derived/fixture/…``, their views and ``meta.concept_versions`` (one row per
attempted concept, ``upstream_commit`` set, a ``kind: build`` run + ``kind: concept``
benchmark lines), and the catalog discovery walker refuses a name collision; a crafted
failing concept surfaces a sanitized error; the count-pin helpers and the committed demo
pin file pass the guard.

``@pytest.mark.demo``: rebuild the demo concepts and assert the committed pins.
``tier("dev")``: ``--select concept.sepsis.sepsis3 --with-deps --force`` rebuilds only
sepsis3; the dev pin drift detector agrees with its stored set. Everything asserted or
printed is counts, hashes, names and statuses — never a row, never an identifier value.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

import helpers
from mimicwarehouse import config, guard
from mimicwarehouse import run as run_mod
from mimicwarehouse.catalog import build as build_mod
from mimicwarehouse.catalog import discover
from mimicwarehouse.cli import app
from mimicwarehouse.concepts import inventory as inv_mod
from mimicwarehouse.concepts import pins as pins_mod
from mimicwarehouse.concepts import runner as concept_runner
from mimicwarehouse.concepts import vendor_info
from mimicwarehouse.dag import benchmarks as benchmarks_mod
from mimicwarehouse.dag import runner as dag_runner
from mimicwarehouse.dag import snapshot as snapshot_mod
from mimicwarehouse.dag.spec import (
    SHARED_STEPS,
    DagError,
    DagSpec,
    Step,
    load_dag,
    merge_spec_documents,
)
from mimicwarehouse.loader.manifest import read_status, update_status

if TYPE_CHECKING:
    import duckdb as duckdb_mod

    from mimicwarehouse.config import Settings

pytestmark = pytest.mark.ep_37

NAMED_FIVE = ("icustay_detail", "age", "charlson", "sofa", "sepsis3")
SEPSIS3_STEP = "concept.sepsis.sepsis3"
DOCS = helpers.WORKSPACE / "docs"
CONCEPTS_DOC = DOCS / "resources" / "concepts.md"
RESOURCES_INDEX = DOCS / "resources" / "README.md"
DESIGN = helpers.WORKSPACE / "DESIGN.md"

CALLS: list[str] = []


# --- crafted step bodies for the runner tests (callable: test_ep37:<name>) ----------------


def step_ok(step: Any, ctx: Any) -> None:
    CALLS.append(step.name)


def step_fail(step: Any, ctx: Any) -> None:
    raise RuntimeError("crafted step failure")


def step_mark(step: Any, ctx: Any) -> None:
    """A python step with a ``target``: marks its status entry complete (derived layer)."""
    CALLS.append(step.name)
    update_status(ctx.lake_root, step.target, layer="derived", tier_complete="full")


def _python_step(
    name: str, fn: str, *, depends_on: list[str] | None = None, target: str | None = None
):
    # this module under the name pytest imported it as (importlib resolves it from sys.modules)
    step: dict[str, Any] = {"name": name, "kind": "python", "callable": f"{__name__}:{fn}"}
    if depends_on:
        step["depends_on"] = depends_on
    if target:
        step["target"] = target
    return step


@pytest.fixture
def scratch_settings(tmp_path: Path) -> Settings:
    from mimicwarehouse.config import Settings

    return Settings(data_root=tmp_path / "root", min_free_gb=1)


def _objects(con: duckdb_mod.DuckDBPyConnection, schema: str) -> dict[str, str]:
    rows = con.execute(
        "SELECT table_name, table_type FROM information_schema.tables WHERE table_schema = ?",
        [schema],
    ).fetchall()
    return {str(n): str(k) for n, k in rows}


def _scalar(con: duckdb_mod.DuckDBPyConnection, sql: str) -> Any:
    row = con.execute(sql).fetchone()
    assert row is not None
    return row[0]


# ---------------------------------------------------------------------------
# 1. Inventory: coverage, order, generated files (brief item 1)
# ---------------------------------------------------------------------------


def test_inventory_covers_every_vendored_file_and_is_acyclic() -> None:
    inv = inv_mod.scan_vendor()
    root = inv_mod.concepts_root()
    files = sorted(p for p in root.glob("*/*.sql"))
    assert len(files) == 65 == len(inv.concepts)
    assert {p.stem for p in files} == set(inv.by_name)
    assert inv.upstream_commit == vendor_info().sha
    position = {c.name: c.order for c in inv.concepts}
    assert [c.order for c in inv.concepts] == list(range(65)), "dense execution order"
    for c in inv.concepts:
        assert c.name not in c.depends_on
        for dep in c.depends_on:
            assert position[dep] < position[c.name], f"{c.name} runs after {dep}"
        assert c.sql_sha256 == inv_mod.sql_sha256(
            (root / Path(*c.path.split("/")[2:])).read_text(encoding="utf-8")
        )
    # the upstream driver order is itself topological at this pin, so it is the order
    driver = [Path(rel).stem for rel in inv_mod.driver_order()]
    assert [c.name for c in inv.concepts] == driver
    # every core read is a stage step of the stage spec
    stage_steps = {s.name for s in load_dag("stage").steps if s.kind == "stage"}
    for c in inv.concepts:
        for read in c.reads:
            assert inv_mod.stage_step_for(read) in stage_steps, (c.name, read)
    # the five named concepts and their known edges
    by = inv.by_name
    assert (
        "sofa" in by["sepsis3"].depends_on and "suspicion_of_infection" in by["sepsis3"].depends_on
    )
    assert "age" in by["charlson"].depends_on and by["age"].depends_on == ()
    # committed files are in sync with a fresh scan (regenerate: python -m ...inventory)
    assert inv_mod.stale_generated(inv) == [], (
        "run `uv run python -m mimicwarehouse.concepts.inventory`"
    )
    assert inv_mod.load_inventory() == inv


def test_topological_order_tie_break_and_cycle() -> None:
    order = inv_mod._topological_order(["a", "b", "c"], {"a": ("b",), "b": (), "c": ()})
    assert order == ["b", "a", "c"], "driver position breaks ties; a waits for b"
    with pytest.raises(inv_mod.ConceptInventoryError, match="cycle"):
        inv_mod._topological_order(["a", "b"], {"a": ("b",), "b": ("a",)})
    with pytest.raises(inv_mod.ConceptInventoryError, match="unknown concept"):
        inv_mod._topological_order(["a"], {"a": ("ghost",)})


def test_split_header_on_crafted_files() -> None:
    crafted = (
        "-- THIS SCRIPT IS AUTOMATICALLY GENERATED. DO NOT EDIT IT DIRECTLY.\n"
        "DROP TABLE IF EXISTS mimiciv_derived.crafted; CREATE TABLE mimiciv_derived.crafted AS\n"
        "SELECT\n  ie.stay_id\nFROM mimiciv_icu.icustays AS ie\n"
        "INNER JOIN mimiciv_derived.age AS a ON a.hadm_id = ie.hadm_id -- mimiciv_derived.ghost\n"
    )
    table, body = inv_mod.split_header(crafted)
    assert (
        table == "crafted" and body.startswith("SELECT") and body.endswith("mimiciv_derived.ghost")
    )
    core, derived = inv_mod.references(body)
    assert core == ("mimiciv_icu.icustays",) and derived == ("age",), "comments are stripped"
    # whitespace / case variants of the same header still parse
    one_line = (
        "drop table if exists MIMICIV_DERIVED.X ;  create table mimiciv_derived.x as "
        "select 1 AS one;"
    )
    assert inv_mod.split_header(one_line) == ("x", "select 1 AS one")
    for bad, why in (
        ("SELECT 1", "not a concepts_duckdb file"),
        (
            "DROP TABLE IF EXISTS mimiciv_derived.a; CREATE TABLE mimiciv_derived.b AS SELECT 1",
            "drops 'a' but creates 'b'",
        ),
        (
            "DROP TABLE IF EXISTS mimiciv_derived.a; CREATE TABLE mimiciv_derived.a AS   ",
            "SELECT body",
        ),
    ):
        with pytest.raises(inv_mod.ConceptInventoryError, match=why):
            inv_mod.split_header(bad)


def test_rendered_inventory_and_spec_are_ascii_and_guard_clean(tmp_path: Path) -> None:
    inv = inv_mod.load_inventory()
    for name, text in (
        ("concepts.yaml", inv_mod.render_inventory_yaml(inv)),
        ("spec.yaml", inv_mod.render_spec_yaml(inv)),
        ("concepts.md", inv_mod.render_markdown_table(inv)),
    ):
        assert text.isascii(), name
        out = tmp_path / name
        out.write_text(text, encoding="utf-8", newline="\n")
        assert guard.scan([out], helpers.REPO_ROOT) == [], name
    assert inv_mod.concept_status("age") == inv_mod.STATUS_OK
    assert inv_mod.concept_status("age", {"age": "BinderException"}).startswith("fails: Binder")


# ---------------------------------------------------------------------------
# 2. Spec discovery, --with-deps, target (amendment items 1-2)
# ---------------------------------------------------------------------------


def test_load_dag_merges_specs_with_one_shared_catalog() -> None:
    dag = load_dag()
    names = [s.name for s in dag.steps]
    assert len(names) == len(set(names)), "step names are unique across spec files"
    stage_only = load_dag("stage")
    assert not any(s.name.startswith("concept.") for s in stage_only.steps)
    inv = inv_mod.load_inventory()
    assert set(inv.step_names) <= set(names) and inv_mod.VERSIONS_STEP in names
    catalog = dag.step("catalog")
    assert catalog.kind == "catalog" and {"catalog"} == SHARED_STEPS
    assert set(catalog.tags) == {"catalog", "concepts"}
    assert set(stage_only.step("catalog").depends_on) <= set(catalog.depends_on)
    assert set(inv.step_names) | {inv_mod.VERSIONS_STEP} <= set(catalog.depends_on)
    assert catalog.tiers == (), "empty on one side = every tier"
    for name in inv.step_names:
        step = dag.step(name)
        assert step.kind == "python" and step.callable_name == inv_mod.RUNNER_CALLABLE
        assert step.target == f"mimiciv_derived.{name.rsplit('.', 1)[1]}" == step.status_key
        assert "concepts" in step.tags and set(step.tiers) == set(inv_mod.TIERS)
    ordered = [s.name for s in dag.ordered(tags=["concepts"], tier="fixture")]
    assert len(ordered) == 65 + 2 and ordered[-2:] == [inv_mod.VERSIONS_STEP, "catalog"]
    assert set(ordered[:65]) == set(inv.step_names)
    # the runner's order is topological (graphlib), not the spec's: every concept runs
    # after the concepts it reads
    position = {name: i for i, name in enumerate(ordered)}
    for concept in inv.concepts:
        for dep in concept.depends_on:
            assert position[inv.concept(dep).step_name] < position[concept.step_name]
    assert [s.name for s in dag.ordered()][-1] == "catalog"


def test_merge_spec_documents_rules() -> None:
    a = {
        "version": 1,
        "steps": [
            _python_step("x", "step_ok"),
            {"name": "catalog", "kind": "catalog", "tags": ["catalog"], "depends_on": ["x"]},
        ],
    }
    b = {
        "version": 2,
        "steps": [
            _python_step("y", "step_ok"),
            {
                "name": "catalog",
                "kind": "catalog",
                "tags": ["more"],
                "depends_on": ["y"],
                "tiers": ["dev"],
            },
        ],
    }
    merged = merge_spec_documents([("a.yaml", a), ("b.yaml", b)])
    assert merged["version"] == 2 and [s["name"] for s in merged["steps"]] == ["x", "catalog", "y"]
    shared = merged["steps"][1]
    assert shared["depends_on"] == ["x", "y"] and shared["tags"] == ["catalog", "more"]
    assert shared["tiers"] == [], "one side unrestricted -> every tier"
    DagSpec.model_validate(merged)  # cross-file dependencies resolve after the merge
    with pytest.raises(DagError, match="defined in both"):
        merge_spec_documents(
            [("a.yaml", a), ("c.yaml", {"version": 1, "steps": [_python_step("x", "step_ok")]})]
        )
    with pytest.raises(DagError, match="kind"):
        merge_spec_documents(
            [
                ("a.yaml", a),
                ("d.yaml", {"version": 1, "steps": [_python_step("catalog", "step_ok")]}),
            ]
        )


def test_with_deps_closure_and_explicit_selection() -> None:
    dag = load_dag()
    alone = [s.name for s in dag.ordered(select=[SEPSIS3_STEP])]
    assert alone == [SEPSIS3_STEP], "--select never pulls dependencies in by itself"
    closed = [s.name for s in dag.ordered(select=[SEPSIS3_STEP], with_deps=True, tier="fixture")]
    assert closed[-1] == SEPSIS3_STEP
    for ancestor in (
        "concept.score.sofa",
        "concept.sepsis.suspicion_of_infection",
        "stage.mimiciv_icu.icustays",
        "stage.mimiciv_icu.chartevents",
    ):
        assert ancestor in closed
    assert "catalog" not in closed and inv_mod.VERSIONS_STEP not in closed
    assert dag.explicit_selection(select=[SEPSIS3_STEP]) == frozenset({SEPSIS3_STEP})
    assert dag.ancestors([SEPSIS3_STEP]) == frozenset(closed)
    with pytest.raises(DagError, match="unknown step"):
        dag.ordered(select=["concept.nope"], with_deps=True)


def test_python_step_target_validation() -> None:
    step = Step(name="p", kind="python", callable="m:f", target="mimiciv_derived.x")
    assert step.status_key == "mimiciv_derived.x" and step.qualified_table is None
    assert Step(name="q", kind="python", callable="m:f").status_key is None
    with pytest.raises(ValueError, match="target must be"):
        Step(name="r", kind="python", callable="m:f", target="NoSchema")
    with pytest.raises(ValueError, match="belong to another kind"):
        Step(name="s", kind="python", callable="m:f", file="x.sql")


# ---------------------------------------------------------------------------
# 3. Runner: --keep-going, --with-deps + --force on a crafted DAG (item 2)
# ---------------------------------------------------------------------------


def test_keep_going_records_failure_and_blocks_dependents(scratch_settings: Settings) -> None:
    spec = DagSpec.model_validate(
        {
            "version": 1,
            "steps": [
                _python_step("a", "step_ok"),
                _python_step("b", "step_ok", depends_on=["a"]),
                _python_step("c", "step_fail"),
                _python_step("d", "step_ok", depends_on=["c"]),
                _python_step("e", "step_ok", depends_on=["d"]),
                _python_step("f", "step_ok"),
            ],
        }
    )
    CALLS.clear()
    result = dag_runner.run(spec, "fixture", settings=scratch_settings, keep_going=True)
    by = {s.name: s.status for s in result.steps}
    assert by == {
        "a": "done",
        "b": "done",
        "c": "failed",
        "d": "blocked",
        "e": "blocked",
        "f": "done",
    }
    assert not result.ok and result.snapshot_id is None
    # graphlib's topological order runs the zero-indegree steps first (a, c, f), then b
    assert sorted(CALLS) == ["a", "b", "f"], "blocked steps never run"
    assert CALLS.index("a") < CALLS.index("b")
    blocked = {s.name: s.error for s in result.steps if s.status == "blocked"}
    assert blocked == {"d": "blocked by c", "e": "blocked by d"}
    ledger = benchmarks_mod.read(scratch_settings)
    steps = ledger.filter(ledger["kind"] != "build")
    assert set(steps["step"].to_list()) == {"a", "b", "c", "f"}, "no telemetry for blocked steps"
    build_line = ledger.filter(ledger["kind"] == "build")
    assert build_line["ok"].to_list() == [False]
    assert "crafted step failure" in (build_line["error"].to_list()[0] or "")
    # default: the first failure stops the run (EP-19 semantics unchanged)
    CALLS.clear()
    stopped = dag_runner.run(spec, "fixture", settings=scratch_settings)
    assert {s.name: s.status for s in stopped.steps} == {"a": "done", "c": "failed"}
    assert CALLS == ["a"], "f and b never ran: the run stopped at c"


def test_force_with_deps_applies_to_the_explicit_steps_only(scratch_settings: Settings) -> None:
    spec = DagSpec.model_validate(
        {
            "version": 1,
            "steps": [
                _python_step("parent", "step_mark", target="mimiciv_derived.parent"),
                _python_step(
                    "child", "step_mark", depends_on=["parent"], target="mimiciv_derived.child"
                ),
            ],
        }
    )
    CALLS.clear()
    first = dag_runner.run(spec, "fixture", settings=scratch_settings)
    assert [s.status for s in first.steps] == ["done", "done"] and CALLS == ["parent", "child"]
    status = read_status(scratch_settings.lake_root("fixture"))["steps"]
    assert status["mimiciv_derived.child"]["layer"] == "derived"
    # complete -> skipped (status_key drives the generic skip for python steps too)
    again = dag_runner.run(spec, "fixture", settings=scratch_settings)
    assert [s.status for s in again.steps] == ["skipped", "skipped"]
    # --select child --with-deps --force: the child reruns, the pulled-in parent skips
    CALLS.clear()
    forced = dag_runner.run(
        spec, "fixture", select=["child"], with_deps=True, force=True, settings=scratch_settings
    )
    assert {s.name: s.status for s in forced.steps} == {"parent": "skipped", "child": "done"}
    assert CALLS == ["child"]
    # plain --force reruns everything planned
    CALLS.clear()
    everything = dag_runner.run(spec, "fixture", force=True, settings=scratch_settings)
    assert [s.status for s in everything.steps] == ["done", "done"] and CALLS == ["parent", "child"]
    # dry run lists the closure without touching anything
    plan = dag_runner.run(
        spec, "fixture", select=["child"], with_deps=True, dry_run=True, settings=scratch_settings
    )
    assert [(s.name, s.status) for s in plan.steps] == [("parent", "planned"), ("child", "planned")]


def test_per_tier_layer_completeness_predicate() -> None:
    core_full = {"tier_complete": "full", "dev_ready": False}
    assert snapshot_mod.complete_for_tier(core_full, "dev") and snapshot_mod.complete_for_tier(
        core_full, "full"
    )
    derived_full = {"layer": "derived", "tier_complete": "full", "dev_ready": False}
    assert snapshot_mod.complete_for_tier(derived_full, "full")
    assert not snapshot_mod.complete_for_tier(derived_full, "dev"), (
        "a full derived file is not a dev one"
    )
    derived_dev = {"layer": "derived", "tier_complete": "dev", "dev_ready": True}
    assert snapshot_mod.complete_for_tier(
        derived_dev, "dev"
    ) and not snapshot_mod.complete_for_tier(derived_dev, "full")
    both = {"layer": "derived", "tier_complete": "full", "dev_ready": True}
    assert snapshot_mod.complete_for_tier(both, "dev") and snapshot_mod.complete_for_tier(
        both, "full"
    )
    assert snapshot_mod.layer_path_prefix("core", "dev") == "core/"
    assert snapshot_mod.layer_path_prefix("derived", "dev") == "derived/dev/"
    assert snapshot_mod.entry_layer({}) == "core" and snapshot_mod.entry_layer(both) == "derived"
    assert concept_runner._tier_status_fields("dev", {}) == {
        "dev_ready": True,
        "tier_complete": "dev",
    }
    assert concept_runner._tier_status_fields("dev", {"tier_complete": "full"}) == {
        "dev_ready": True,
        "tier_complete": "full",
    }
    assert concept_runner._tier_status_fields("full", {}) == {"tier_complete": "full"}
    assert concept_runner._tier_status_fields("demo", {}) == {"tier_complete": "full"}


# ---------------------------------------------------------------------------
# 4. The session fixture lake: the five concepts, views, versions, ledgers (items 2-3)
# ---------------------------------------------------------------------------


def test_fixture_run_built_the_named_concepts(
    fixture_lake_settings: Settings, fixture_lake_catalog: duckdb_mod.DuckDBPyConnection
) -> None:
    settings = fixture_lake_settings
    lake = settings.lake_root("fixture")
    status = read_status(lake)["steps"]
    lines = dict(snapshot_mod.layer_lines(lake, "fixture", layer="derived", settings=settings))
    views = _objects(fixture_lake_catalog, "mimiciv_derived")
    for name in NAMED_FIVE:
        path = concept_runner.derived_file(settings, "fixture", name)
        assert (
            path
            == settings.lake_root("fixture")
            / "derived"
            / "fixture"
            / "mimiciv_derived"
            / name
            / "part-0.parquet"
        )
        assert path.is_file(), name
        entry = status[f"mimiciv_derived.{name}"]
        assert entry["layer"] == "derived" and entry["tier_complete"] == "full"
        assert entry["upstream_commit"] == vendor_info().sha and len(entry["sql_sha256"]) == 64
        line = lines[f"mimiciv_derived.{name}"]
        assert line.path == f"derived/fixture/mimiciv_derived/{name}/part-0.parquet"
        assert line.source_sha256 == entry["sql_sha256"] and line.raw_snapshot_id
        assert views[name] == "VIEW"
        assert (
            _scalar(fixture_lake_catalog, f'SELECT count(*) FROM mimiciv_derived."{name}"')
            == line.rows
        )
    assert _scalar(
        fixture_lake_catalog, "SELECT count(*) FROM mimiciv_derived.icustay_detail"
    ) == _scalar(fixture_lake_catalog, "SELECT count(*) FROM mimiciv_icu.icustays")
    assert _scalar(fixture_lake_catalog, "SELECT count(*) FROM mimiciv_derived.age") == _scalar(
        fixture_lake_catalog, "SELECT count(*) FROM mimiciv_hosp.admissions"
    )
    # every vendored concept is a view beside the two timesem views; all 65 complete
    inv = inv_mod.load_inventory()
    assert set(views) == set(inv.by_name) | {"hadm_era", "icustay_index"}
    # the derived layer snapshot is recorded and logical (recomputation agrees)
    history = [h for h in snapshot_mod.read_snapshots(lake) if h["layer"] == "derived"]
    assert history and history[-1]["tier"] == "fixture"
    recomputed = snapshot_mod.layer_snapshot(lake, "derived", "fixture", settings=settings)
    assert history[-1]["snapshot_id"] == recomputed
    assert recomputed != snapshot_mod.layer_snapshot(lake, "core", "fixture", settings=settings)
    # the credentialed lake root was never touched by a fixture build
    assert not (settings.layout["lake"] / "core").exists()


def test_concept_versions_has_one_row_per_attempted_concept(
    fixture_lake_settings: Settings, fixture_lake_catalog: duckdb_mod.DuckDBPyConnection
) -> None:
    con = fixture_lake_catalog
    meta = _objects(con, "meta")
    assert meta["concept_versions"] == "BASE TABLE"
    assert "profile_tables" in meta and "profile_columns" in meta, "EP-29 profiles discovered too"
    described = [d[0] for d in con.execute("DESCRIBE meta.concept_versions").fetchall()]
    assert described == list(concept_runner.VERSIONS_COLUMNS)
    rows = con.execute(
        "SELECT concept, upstream_commit, status, rows, run_id, snapshot_id, patch_id, tier "
        "FROM meta.concept_versions ORDER BY concept"
    ).fetchall()
    inv = inv_mod.load_inventory()
    assert [r[0] for r in rows] == sorted(inv.by_name), "one row per attempted concept"
    assert {r[1] for r in rows} == {vendor_info().sha}
    assert {r[2] for r in rows} == {"ok"} and all(r[3] is not None and r[3] >= 0 for r in rows)
    # EP-38 (roadmap README CMP-6 rule): patch_id is set exactly for the concepts the patch
    # registry names; the EP-37 pin "always None" was that brief's placeholder
    from mimicwarehouse.concepts.patches import load_registry

    patched = set(load_registry().concepts)
    assert {r[0] for r in rows if r[6] is not None} == patched, "patch_id is EP-38's"
    assert {r[7] for r in rows} == {"fixture"}
    run_ids = {r[4] for r in rows}
    assert len(run_ids) == 1
    (run_id,) = run_ids
    manifest = run_mod.read_manifest(run_id, fixture_lake_settings)
    assert manifest.kind == "build" and manifest.status == "ok" and manifest.tier == "fixture"
    assert {ref.kind for ref in manifest.refs} == {"concept"} and len(manifest.refs) == 65
    assert set(manifest.snapshot_ids) == {"core", "derived"}
    assert manifest.snapshot_ids["derived"] == rows[0][5]
    assert manifest.params["attempted"] == 65 and manifest.params["failed"] == 0
    # kind: concept benchmark lines carry that run id and the concepts' step names
    ledger = benchmarks_mod.read(fixture_lake_settings)
    concept_lines = ledger.filter(ledger["kind"] == "concept")
    assert concept_lines.height == 65
    assert set(concept_lines["run_id"].to_list()) == {run_id}
    assert set(concept_lines["step"].to_list()) == set(inv.step_names)
    assert all(concept_lines["ok"].to_list())
    summary = benchmarks_mod.summarize(fixture_lake_settings, tier="fixture", kind="concept")
    assert summary.height == 65


def test_cli_reads_versions_benchmarks_and_plan(fixture_lake_settings: Settings) -> None:
    runner = helpers.cli_runner()
    root = ["--data-root", str(fixture_lake_settings.data_root)]
    try:
        versions = runner.invoke(
            app,
            [
                *root,
                "sql",
                "SELECT concept, rows FROM meta.concept_versions ORDER BY 1",
                "--tier",
                "fixture",
                "--format",
                "json",
            ],
        )
        assert versions.exit_code == 0, versions.output
        payload = json.loads(versions.stdout)
        assert len(payload["rows"]) == 65 and payload["rows"][0]["concept"] == "acei"
        bench = runner.invoke(
            app, [*root, "runs", "benchmarks", "--kind", "concept", "--tier", "fixture"]
        )
        assert bench.exit_code == 0, bench.output
        assert "sepsis.sepsis3" in bench.output and "total" in bench.output
        plan = runner.invoke(
            app,
            [
                *root,
                "build",
                "--tier",
                "fixture",
                "--select",
                SEPSIS3_STEP,
                "--with-deps",
                "--dry-run",
            ],
        )
        assert plan.exit_code == 0, plan.output
        assert plan.output.find("concept.score.sofa") < plan.output.find(SEPSIS3_STEP)
        assert "catalog" not in plan.output.split("plan", 1)[1]
    finally:
        config.configure()


def test_rerun_skips_and_select_with_deps_force_rebuilds_only_sepsis3(
    fixture_lake_settings: Settings,
) -> None:
    settings = fixture_lake_settings
    inv = inv_mod.load_inventory()
    lake = settings.lake_root("fixture")
    before = snapshot_mod.layer_snapshot(lake, "derived", "fixture", settings=settings)
    again = dag_runner.run(load_dag(), "fixture", select=list(inv.step_names), settings=settings)
    assert again.ok and {s.status for s in again.steps} == {"skipped"}
    forced = dag_runner.run(
        load_dag(), "fixture", select=[SEPSIS3_STEP], with_deps=True, force=True, settings=settings
    )
    by = {s.name: s.status for s in forced.steps}
    assert by.pop(SEPSIS3_STEP) == "done" and set(by.values()) == {"skipped"}
    assert "concept.score.sofa" in by and "stage.mimiciv_icu.icustays" in by
    after = snapshot_mod.layer_snapshot(lake, "derived", "fixture", settings=settings)
    assert after == before, "an identical rebuild keeps the logical derived snapshot id"


# ---------------------------------------------------------------------------
# 5. Catalog discovery + extension dispatch (amendment item 3)
# ---------------------------------------------------------------------------


def test_extension_dispatch_and_registration_order() -> None:
    def two(con: Any, tier: str) -> None: ...

    def three(con: Any, tier: str, context: Any) -> None: ...

    assert not build_mod._wants_context(two) and build_mod._wants_context(three)
    assert build_mod._wants_context(discover.register_layers)
    assert build_mod.CATALOG_EXTENSIONS[1] is discover.register_layers, "second, after timesem"


def test_discovery_refuses_collisions_and_skips_missing_files(
    fixture_lake_settings: Settings, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    import duckdb

    settings = fixture_lake_settings
    lake = settings.lake_root("fixture")
    found = discover.discover_layer_tables(settings, lake, "fixture")
    assert {t.qualified_name for t in found} >= {f"mimiciv_derived.{n}" for n in NAMED_FIVE}
    assert all(t.layer == "derived" and t.path.is_file() for t in found)
    assert dict(discover.discover_meta_files(lake, "fixture")).keys() >= {
        "concept_versions",
        "profile_tables",
    }
    context = build_mod.CatalogExtensionContext(
        settings=settings, lake_root=lake, build_id="ep37-probe", tier="fixture"
    )
    con = duckdb.connect()
    try:
        con.execute("CREATE SCHEMA mimiciv_derived")
        con.execute("CREATE TABLE mimiciv_derived.age AS SELECT 1 AS one")
        with pytest.raises(
            build_mod.CatalogBuildError, match=r"mimiciv_derived\.age already exists"
        ):
            discover.register_layers(con, "fixture", context)
    finally:
        con.close()
    # a status entry whose file is gone is skipped with a warning, never registered empty
    from mimicwarehouse.config import Settings

    other = Settings(data_root=tmp_path / "root", min_free_gb=1)
    other_lake = other.lake_root("fixture")
    update_status(other_lake, "mimiciv_derived.ghost", layer="derived", tier_complete="full")
    with caplog.at_level("WARNING"):
        assert discover.discover_layer_tables(other, other_lake, "fixture") == []
    assert "ghost" in caplog.text and "missing" in caplog.text


# ---------------------------------------------------------------------------
# 6. A crafted concept: sanitized failure, per-tier files (items 2, 6)
# ---------------------------------------------------------------------------


def test_crafted_concept_success_and_sanitized_failure(scratch_settings: Settings) -> None:
    import logging

    from mimicwarehouse.engine import open_duckdb

    inv = inv_mod.load_inventory()
    sha = "0" * 64
    concept = inv_mod.Concept(
        name="crafted",
        group="test",
        path="crafted.sql",
        sql_sha256=sha,
        upstream_commit=inv.upstream_commit,
        order=0,
    )
    settings = scratch_settings
    con = open_duckdb("build", settings=settings, memory_limit="1GB")
    try:
        ctx = dag_runner.StepContext(
            settings=settings,
            tier="fixture",
            build_id="ep37-crafted",
            con=con,
            log=logging.getLogger("test_ep37"),
            raw_root=scratch_settings.data_root,
            lake_root=settings.lake_root("fixture"),
            buckets=None,
        )
        header = (
            "DROP TABLE IF EXISTS mimiciv_derived.crafted; CREATE TABLE mimiciv_derived.crafted AS"
        )
        ok_sql = f"{header} SELECT 1 AS one, 'a' AS b"
        rows, size = concept_runner.build_concept(concept, ctx, sql_text=ok_sql)
        assert rows == 1 and size > 0
        dest = concept_runner.derived_file(settings, "fixture", "crafted")
        assert (
            dest.is_file()
            and dest.parent.parent.parent.parent == concept_runner.derived_root(settings, "fixture")
            and not settings.layout["lake_derived"].exists()
        )
        entry = read_status(ctx.lake_root)["steps"]["mimiciv_derived.crafted"]
        assert (
            entry["layer"] == "derived"
            and entry["tier_complete"] == "full"
            and entry["dev_ready"] is False
        )
        ((qn, line),) = snapshot_mod.layer_lines(
            ctx.lake_root, "fixture", layer="derived", settings=settings
        )
        assert qn == "mimiciv_derived.crafted" and line.rows == 1 and line.source_sha256 == sha
        assert line.path == "derived/fixture/mimiciv_derived/crafted/part-0.parquet"
        assert _scalar(con, "SELECT count(*) FROM mimiciv_derived.crafted") == 1, (
            "registered for the next concept"
        )
        # a failing SELECT: ConceptError with the engine's quoted literal masked; no file left
        bad_sql = f"{header} SELECT CAST('not-a-number-value' AS INTEGER) AS x"
        with pytest.raises(concept_runner.ConceptError) as excinfo:
            concept_runner.build_concept(concept, ctx, sql_text=bad_sql)
        message = str(excinfo.value)
        assert "not-a-number-value" not in message and "concept.test.crafted" in message
        assert not dest.with_name(dest.name + ".tmp").exists()
        assert dest.is_file(), "the previous publish is untouched"
        # a dev build of the same concept lands under derived/dev on the shared lake root
        dev_ctx = dag_runner.StepContext(
            settings=settings,
            tier="dev",
            build_id="ep37-crafted-dev",
            con=con,
            log=ctx.log,
            raw_root=scratch_settings.data_root,
            lake_root=settings.lake_root("dev"),
            buckets=list(settings.dev_buckets),
        )
        concept_runner.build_concept(concept, dev_ctx, sql_text=ok_sql)
        dev_entry = read_status(dev_ctx.lake_root)["steps"]["mimiciv_derived.crafted"]
        assert dev_entry["dev_ready"] is True and dev_entry["tier_complete"] == "dev"
        assert snapshot_mod.complete_for_tier(
            dev_entry, "dev"
        ) and not snapshot_mod.complete_for_tier(dev_entry, "full")
        assert concept_runner.derived_file(settings, "dev", "crafted").is_file()
        assert concept_runner.derived_file(settings, "full", "crafted").exists() is False
        # the inventory guard: a vendored file that drifted from the committed sha is refused
        age = inv.concept("age")
        stale = inv_mod.Concept(**{**age.model_dump(), "sql_sha256": "f" * 64})
        with pytest.raises(
            concept_runner.ConceptError, match="differs from the committed inventory"
        ):
            concept_runner.build_concept(stale, ctx)
    finally:
        con.close()


# ---------------------------------------------------------------------------
# 7. Count-pins (item 4)
# ---------------------------------------------------------------------------


def test_pin_helpers() -> None:
    assert pins_mod.render_pin(0, 11) == 0 and pins_mod.render_pin(11, 11) == 11
    assert pins_mod.render_pin(5, 11) == "<11" and pins_mod.render_pin(None, 11) == "<11"
    assert pins_mod.render_pin(2.5, 11) == 2.5, "means pass through"
    sql = pins_mod.row_count_sql(["age", "sofa"])
    assert sql.count("UNION ALL") == 1 and "count(*) AS n FROM mimiciv_derived.sofa" in sql
    with pytest.raises(ValueError):
        pins_mod.row_count_sql([])
    expected = {
        "tier": "demo",
        "k": 11,
        "row_counts": {"age": 275, "sofa": "<11"},
        "sepsis3_true": 12,
    }
    actual = {
        "tier": "demo",
        "k": 11,
        "row_counts": {"age": 276, "sofa": "<11"},
        "sepsis3_true": 12,
        "audit_ids": ["x"],
    }
    diffs = pins_mod.compare_pins(expected, actual)
    assert diffs == ["row_counts.age: pinned 275, now 276"]
    assert pins_mod.compare_pins(expected, {**actual, "row_counts": expected["row_counts"]}) == []


def test_compute_pins_on_the_fixture_catalog(
    fixture_lake_settings: Settings, tmp_path: Path
) -> None:
    pins = pins_mod.compute_pins("fixture", fixture_lake_settings)
    inv = inv_mod.load_inventory()
    assert (
        pins["tier"] == "fixture"
        and pins["k"] == 11
        and pins["upstream_commit"] == inv.upstream_commit
    )
    assert set(pins["row_counts"]) == set(inv.by_name)
    for name, value in pins["row_counts"].items():
        assert value == "<11" or (isinstance(value, int) and (value == 0 or value >= 11)), (
            name,
            value,
        )
    assert pins["sepsis3_true"] == "<11" or pins["sepsis3_true"] >= 11
    assert isinstance(pins["kdigo_max_stage"], dict) and pins["kdigo_stages_suppressed"] >= 0
    for value in pins["kdigo_max_stage"].values():
        assert value == "<11" or value >= 11
    assert pins["charlson_n"] == "<11" or pins["charlson_n"] >= 11
    assert len(pins["audit_ids"]) >= 5, "every number came through safe_query"
    path = pins_mod.write_pins(tmp_path / "pins.json", pins)
    assert pins_mod.read_pins(path) == pins
    assert (
        pins_mod.compare_pins(pins, pins_mod.compute_pins("fixture", fixture_lake_settings)) == []
    )
    assert guard.scan([path], helpers.REPO_ROOT) == []


def test_demo_pins_file_is_committed_and_clean() -> None:
    path = pins_mod.demo_pins_path()
    assert path.is_file(), "tests/ep/pins/concepts_demo.json is committed (demo is ODbL)"
    pins = pins_mod.read_pins(path)
    inv = inv_mod.load_inventory()
    assert (
        pins["tier"] == "demo"
        and pins["k"] == 11
        and pins["upstream_commit"] == inv.upstream_commit
    )
    assert set(pins["row_counts"]) == set(inv.by_name)
    for name, value in pins["row_counts"].items():
        assert value is not None, f"{name}: every concept was built on demo"
        assert value == "<11" or (isinstance(value, int) and (value == 0 or value >= 11)), (
            name,
            value,
        )
    assert (
        pins["sepsis3_true"] is not None
        and pins["kdigo_max_stage"]
        and pins["charlson_mean_index"] is not None
    )
    assert guard.scan([path], helpers.REPO_ROOT) == []


def _lock_held_by_live_build(settings: Settings) -> bool:
    from mimicwarehouse.dag import jobs

    path = dag_runner.lock_path(settings)
    if not path.is_file():
        return False
    held, pid = dag_runner._read_lock(path)
    create = held.get("create_time")
    return bool(pid) and jobs.pid_alive(
        pid, float(create) if isinstance(create, int | float) else None
    )


@pytest.mark.demo
def test_demo_rebuild_matches_committed_pins() -> None:
    settings = config.load_settings()
    if _lock_held_by_live_build(settings):
        pytest.skip("a live build holds the build lock (a background job is running)")
    result = dag_runner.run(load_dag(), "demo", tags=["concepts"], force=True, settings=settings)
    assert result.ok, [f"{s.name}: {s.error}" for s in result.steps if s.status == "failed"]
    assert sum(1 for s in result.steps if s.status == "done") == 65 + 2
    actual = pins_mod.compute_pins("demo", settings)
    diffs = pins_mod.compare_pins(pins_mod.read_pins(pins_mod.demo_pins_path()), actual)
    assert diffs == [], diffs


# ---------------------------------------------------------------------------
# 8. dev tier (needs dev.duckdb + `mwh build --tier dev --tag concepts`)
# ---------------------------------------------------------------------------


def _dev_concepts_built(settings: Settings) -> bool:
    entry = read_status(settings.lake_root("dev"))["steps"].get("mimiciv_derived.sepsis3")
    return entry is not None and snapshot_mod.complete_for_tier(entry, "dev")


@pytest.mark.tier("dev", needs="lake")
def test_dev_select_sepsis3_rebuilds_only_it(dev_catalog: Path) -> None:
    settings = config.load_settings()
    if not _dev_concepts_built(settings):
        pytest.skip("dev concepts not built yet (mwh build --tier dev --tag concepts)")
    if _lock_held_by_live_build(settings):
        pytest.skip("a live build holds the build lock (a background job is running)")
    result = dag_runner.run(
        load_dag(), "dev", select=[SEPSIS3_STEP], with_deps=True, force=True, settings=settings
    )
    assert result.ok, [f"{s.name}: {s.error}" for s in result.steps if s.status == "failed"]
    by = {s.name: s.status for s in result.steps}
    assert by.pop(SEPSIS3_STEP) == "done"
    assert set(by.values()) == {"skipped"}, "complete ancestors are never rebuilt"
    entry = read_status(settings.lake_root("dev"))["steps"]["mimiciv_derived.sepsis3"]
    assert entry["dev_ready"] is True
    assert concept_runner.derived_file(settings, "dev", "sepsis3").is_file()


@pytest.mark.tier("dev")
def test_dev_pins_drift_detector(dev_catalog: Path) -> None:
    settings = config.load_settings()
    if not _dev_concepts_built(settings):
        pytest.skip("dev concepts not built yet (mwh build --tier dev --tag concepts)")
    path, diffs, _created = pins_mod.check_or_write_dev_pins(settings)
    assert path == settings.layout["runs"] / "pins" / "concepts_dev.json" and path.is_file()
    assert diffs == [], diffs
    pins = pins_mod.read_pins(path)
    assert pins["tier"] == "dev" and pins["k"] == 11
    for value in pins["row_counts"].values():
        assert value is not None and (value == "<11" or value == 0 or value >= 11)


# ---------------------------------------------------------------------------
# 9. Docs + import budget
# ---------------------------------------------------------------------------


def test_docs_in_sync_and_guard_clean(tmp_path: Path) -> None:
    assert CONCEPTS_DOC.is_file()
    text = CONCEPTS_DOC.read_text(encoding="utf-8")
    inv = inv_mod.load_inventory()
    assert inv_mod.render_markdown_table(inv) in text, (
        "regenerate: python -m mimicwarehouse.concepts.inventory"
    )
    copy = tmp_path / "concepts.md"
    copy.write_text(text, encoding="utf-8", newline="\n")
    inv_mod.sync_doc(copy, inv)
    assert copy.read_text(encoding="utf-8") == text, "sync is idempotent"
    for required in (
        "concepts-full",
        "KNOWN_FAILURES",
        "lake/derived/<tier>/mimiciv_derived",
        "--with-deps",
        "meta.concept_versions",
        "COVERAGE.md",
    ):
        assert required in text, required
    index = RESOURCES_INDEX.read_text(encoding="utf-8")
    assert "concepts.md" in index and "EP-37" in index
    design = DESIGN.read_text(encoding="utf-8")
    assert "> **Note (2026-09-05, EP-37" in design, "DESIGN sections 3 and 15 need the dated notes"
    assert design.count("EP-37") >= 4
    violations = guard.scan(
        [CONCEPTS_DOC, RESOURCES_INDEX, inv_mod.inventory_path(), inv_mod.spec_path()],
        helpers.REPO_ROOT,
    )
    assert violations == [], [f"{v.rule}: {v.path}" for v in violations]


def test_inventory_module_keeps_duckdb_lazy() -> None:
    helpers.assert_import_budget("mimicwarehouse.concepts.inventory")
    helpers.assert_import_budget("mimicwarehouse.dag.spec")
