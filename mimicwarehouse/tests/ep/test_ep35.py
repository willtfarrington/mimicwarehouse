"""EP-35 — provenance run ledger.

Fixture tier (default): a run against a temp data root writes ``runs/<run_id>/manifest.json``
with every required field plus one ``runs/ledger.jsonl`` line (the exact ``LEDGER_FIELDS``
subset); the failure path marks the run ``failed`` (sanitized error, no traceback) and
re-raises; ``git_sha`` matches ``git rev-parse HEAD``; ``record_attrition`` / ``record_sql``
/ ``record_ref`` / ``save_table`` / ``save_figure`` / ``warn`` round-trip; ``save_table``
refuses identifier columns; ``mwh runs refresh`` builds the five views and ``mwh runs list``
/ ``show`` display the run (a torn trailing ledger line is tolerated); ``BenchmarkLine``
rejects a malformed line, accepts the EP-35 fields and old lines; ``run.bench`` appends
through ``dag.benchmarks``; ``safe_query`` reads the ledger views on the fixture lake (a
GROUP BY with a real count, the ``usage: `` predicate, the widened label columns) and sees
a rebuilt ``runs.duckdb``; ``reproduction_block`` renders the EP-32 block; the retrofitted
tracer records its statements, attrition and snapshot id without changing its numbers;
the docs exist and pass the guard; the module stays off the ``mwh`` start-up path.

``tier("dev")``-marked: a run wrapping one ``safe_query`` aggregate on the real dev
catalog records the ``core`` snapshot id and a ``sql/`` file, and ``read_layer`` resolves
the dev lake's core snapshot. Everything asserted or printed is ids, hashes, counts,
paths and metadata — the fixture data is synthetic (ids >= 90 000 000); the dev tests see
only k-suppressed aggregates through ``safe_query``.
"""

from __future__ import annotations

import json
import subprocess
import warnings
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from pydantic import ValidationError

import helpers
from mimicwarehouse import config, guard, safe
from mimicwarehouse import run as run_mod
from mimicwarehouse.cli import app
from mimicwarehouse.dag import benchmarks as benchmarks_mod
from mimicwarehouse.dag.benchmarks import BENCHMARK_KINDS, BenchmarkLine, HostInfo
from mimicwarehouse.run import (
    LEDGER_FIELDS,
    MANIFEST_COLUMNS,
    RUN_ID_RE,
    RUN_KINDS,
    AttritionRow,
    RunLedgerError,
    RunManifest,
    ledger_path,
    list_runs,
    read_manifest,
    reproduction_block,
    runs_db_views,
)
from mimicwarehouse.safe import RUNS_DB_VIEWS, build_runs_db, runs_db_path, safe_query
from mimicwarehouse.tracer import STEPS, run_tracer

if TYPE_CHECKING:
    from mimicwarehouse.config import Settings

pytestmark = pytest.mark.ep_35

HOSP = "mimiciv_hosp"
DOCS = helpers.WORKSPACE / "docs"
PROVENANCE_DOC = DOCS / "methods" / "provenance.md"
DESIGN = helpers.WORKSPACE / "DESIGN.md"

#: The manifest fields the brief (item 1) names — every one must be present.
REQUIRED_FIELDS = (
    "run_id",
    "name",
    "kind",
    "tier",
    "status",
    "started",
    "finished",
    "git_sha",
    "git_dirty",
    "uv_lock_sha256",
    "duckdb_version",
    "python_version",
    "package_version",
    "params",
    "snapshot_ids",
    "refs",
    "sql",
    "attrition",
    "seeds",
    "resources",
    "warnings",
    "wall_s",
    "peak_rss_mb",
    "disk_delta_mb",
    "protocol_id",
    "protocol_hash",
    "claim_type",
    "error",
)

_HOST = HostInfo(cpu=16, ram_gb=64.0)


@pytest.fixture
def data_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    yield helpers.tmp_data_root(monkeypatch, tmp_path)
    config.configure()


def _settings() -> Settings:
    return config.get_settings()


def _manifest_file(run: run_mod.Run) -> dict[str, Any]:
    return json.loads((run.dir / "manifest.json").read_text(encoding="utf-8"))


def _ledger_lines(settings: Settings) -> list[dict[str, Any]]:
    path = ledger_path(settings)
    if not path.is_file():
        return []
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def _view_count(settings: Settings, view: str, where: str = "") -> int:
    import duckdb

    con = duckdb.connect(str(runs_db_path(settings)), read_only=True)
    try:
        row = con.execute(f"SELECT count(*) FROM {view} {where}").fetchone()
    finally:
        con.close()
    assert row is not None
    return int(row[0])


# ---------------------------------------------------------------------------
# 1. A run writes the manifest + the ledger line (brief item 6, first clause)
# ---------------------------------------------------------------------------


def test_run_writes_manifest_and_ledger_line(data_root: Path) -> None:
    settings = _settings()
    with run_mod.start(
        "probe", tier="fixture", kind="analysis", params={"k": 11}, settings=settings, doctor=False
    ) as r:
        assert RUN_ID_RE.match(r.run_id), r.run_id
        assert r.dir == data_root / "runs" / r.run_id and r.dir.is_dir()
        running = _manifest_file(r)
        assert running["status"] == "running", "a crash must still leave a record"
        r.record_snapshot("core", "a" * 64)
    manifest = _manifest_file(r)
    for field in REQUIRED_FIELDS:
        assert field in manifest, f"manifest lacks {field!r}"
    assert manifest["status"] == "ok" and manifest["error"] is None
    assert manifest["name"] == "probe" and manifest["kind"] == "analysis"
    assert manifest["tier"] == "fixture" and manifest["params"] == {"k": 11}
    assert manifest["finished"] >= manifest["started"]
    assert manifest["wall_s"] >= 0 and manifest["peak_rss_mb"] is not None
    assert manifest["duckdb_version"] == "1.5.5" and manifest["python_version"].startswith("3.13")
    assert manifest["package_version"] and manifest["uv_lock_sha256"]
    assert manifest["snapshot_ids"] == {"core": "a" * 64}
    assert manifest["seeds"] is None, "nothing seeded (EP-36 records {stage: seed} on r.seed)"
    assert manifest["resources"]["wall_s"] >= 0, "EP-36 fills resources on every run"
    assert manifest["doctor"] is None, "doctor=False skips the environment block"
    # exactly one ledger line, the LEDGER_FIELDS subset, in the canonical form
    lines = _ledger_lines(settings)
    assert len(lines) == 1
    assert tuple(sorted(lines[0])) == tuple(sorted(LEDGER_FIELDS))
    assert lines[0]["run_id"] == r.run_id and lines[0]["status"] == "ok"
    assert lines[0] == {k: manifest[k] for k in LEDGER_FIELDS}
    assert read_manifest(r.run_id, settings).model_dump(mode="json") == manifest
    assert list_runs(settings)[0]["run_id"] == r.run_id


def test_environment_block_is_reduced_doctor_output(data_root: Path) -> None:
    settings = _settings()
    with run_mod.start("env", tier="fixture", settings=settings) as r:
        pass
    doctor = _manifest_file(r)["doctor"]
    assert doctor is not None and set(doctor) == {"timestamp", "host", "ok", "checks"}
    from mimicwarehouse.doctor import CHECK_IDS

    assert [c["id"] for c in doctor["checks"]] == list(CHECK_IDS)
    for check in doctor["checks"]:
        assert set(check) == {"id", "status", "value"}, "value payloads, never the prose detail"
    # cached per data root within the process: a second run costs no probe time
    with run_mod.start("env2", tier="fixture", settings=settings) as r2:
        pass
    assert _manifest_file(r2)["doctor"] == doctor


def test_failure_marks_failed_writes_record_and_reraises(data_root: Path) -> None:
    settings = _settings()
    with (
        pytest.raises(ValueError, match="boom"),
        run_mod.start("bad", tier="fixture", settings=settings, doctor=False) as r,
    ):
        raise ValueError("boom 'quoted value' 12345")
    manifest = _manifest_file(r)
    assert manifest["status"] == "failed"
    assert manifest["error"]["type"] == "ValueError"
    assert "boom" in manifest["error"]["message"]
    assert "quoted value" not in manifest["error"]["message"], "literals are masked (DKB-2)"
    assert "12345" not in manifest["error"]["message"], "numbers are masked (DKB-2)"
    assert "\n" not in manifest["error"]["message"] and "Traceback" not in json.dumps(manifest)
    lines = _ledger_lines(settings)
    assert len(lines) == 1 and lines[0]["status"] == "failed"


def test_git_sha_matches_rev_parse_head(data_root: Path) -> None:
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=helpers.WORKSPACE,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        pytest.skip("not a git checkout")
    with run_mod.start("sha", tier="fixture", settings=_settings(), doctor=False) as r:
        pass
    manifest = _manifest_file(r)
    assert manifest["git_sha"] == proc.stdout.strip()
    assert isinstance(manifest["git_dirty"], bool)


def test_start_refuses_bad_tier_kind_and_name(data_root: Path) -> None:
    settings = _settings()
    with (
        pytest.raises(RunLedgerError, match="tier"),
        run_mod.start("x", tier="bogus", settings=settings, doctor=False),
    ):
        pass
    with (
        pytest.raises(RunLedgerError, match="kind"),
        run_mod.start("x", tier="fixture", kind="mystery", settings=settings, doctor=False),
    ):
        pass
    with (
        pytest.raises(RunLedgerError, match="name"),
        run_mod.start("  ", tier="fixture", settings=settings, doctor=False),
    ):
        pass
    assert not _ledger_lines(settings), "a refused start records nothing"


# ---------------------------------------------------------------------------
# 2. Recording API round-trips
# ---------------------------------------------------------------------------


def test_record_attrition_sql_refs_tables_figures_warnings_round_trip(data_root: Path) -> None:
    import polars as pl

    settings = _settings()
    rows = [
        {"step": "base", "label": "all stays", "n_units": 120, "n_subjects": 100},
        {"step": "adult", "label": "age >= 18", "n_units": 90, "n_subjects": 80},
        AttritionRow(step="cohort", label="analysis cohort", n_units=70, n_subjects=70),
    ]
    with run_mod.start("rt", tier="fixture", settings=settings, doctor=False) as r:
        recorded = r.record_attrition(rows)
        assert [a.step for a in recorded] == ["base", "adult", "cohort"]
        sql_path = r.record_sql("counts", "SELECT count(*) AS n\nFROM meta.tables")
        assert sql_path == r.dir / "sql" / "counts.sql"
        r.record_ref("codeset", "sepsis-icd10", version="1.0", hash="deadbeef")
        table_path = r.save_table("summary", pl.DataFrame({"band": ["a", "b"], "n": [30, 40]}))
        figure_path = r.save_figure("bars", {"mark": "bar", "data": {"values": []}})
        r.warn("2 cells\nsecond line dropped")
        with warnings.catch_warnings():
            warnings.simplefilter("always")
            warnings.warn("library says 'value' 99", UserWarning, stacklevel=1)
        with pytest.raises(RunLedgerError, match="path segment"):
            r.record_sql("../escape", "SELECT 1")
    manifest = _manifest_file(r)
    assert manifest["attrition"] == [
        {"step": "base", "label": "all stays", "n_units": 120, "n_subjects": 100},
        {"step": "adult", "label": "age >= 18", "n_units": 90, "n_subjects": 80},
        {"step": "cohort", "label": "analysis cohort", "n_units": 70, "n_subjects": 70},
    ]
    assert manifest["sql"] == {"counts": "sql/counts.sql"}
    assert sql_path.read_text(encoding="utf-8") == "SELECT count(*) AS n\nFROM meta.tables\n"
    assert "SELECT" not in json.dumps(manifest), "SQL text lives in sql/, never in the manifest"
    assert manifest["refs"] == [
        {"kind": "codeset", "name": "sepsis-icd10", "version": "1.0", "hash": "deadbeef"}
    ]
    assert manifest["tables"] == {"summary": "tables/summary.parquet"} and table_path.is_file()
    assert pl.read_parquet(table_path)["n"].to_list() == [30, 40]
    assert manifest["figures"] == {"bars": "figures/bars.json"} and figure_path.is_file()
    assert manifest["warnings"][0] == "2 cells"
    assert manifest["warnings"][1].startswith("UserWarning:")
    assert "'value'" not in manifest["warnings"][1] and "99" not in manifest["warnings"][1]
    # attrition round-trips through the validated model too
    reread = read_manifest(r.run_id, settings)
    assert [a.model_dump() for a in reread.attrition] == manifest["attrition"]


def test_save_table_refuses_identifier_columns(data_root: Path) -> None:
    import polars as pl

    with run_mod.start("ids", tier="fixture", settings=_settings(), doctor=False) as r:
        with pytest.raises(RunLedgerError, match="subject_id"):
            r.save_table("rows", pl.DataFrame({"subject_id": [90_000_001], "n": [1]}))
        assert not (r.dir / "tables").exists()
    assert _manifest_file(r)["tables"] == {}


def test_manifest_forbids_extra_fields_and_columns_match_models() -> None:
    with pytest.raises(ValidationError):
        RunManifest.model_validate(
            {
                "run_id": "20260905T120000Z-abc123",
                "name": "x",
                "kind": "analysis",
                "tier": "fixture",
                "status": "ok",
                "started": "t",
                "duckdb_version": "1.5.5",
                "python_version": "3.13",
                "package_version": "0.1.0",
                "rows": [{"subject_id": 1}],
            }
        )
    assert set(MANIFEST_COLUMNS) == set(RunManifest.model_fields)
    assert tuple(run_mod.LEDGER_COLUMNS) == LEDGER_FIELDS
    assert set(run_mod.BENCHMARK_COLUMNS) == set(BenchmarkLine.model_fields)
    assert set(LEDGER_FIELDS) <= set(RunManifest.model_fields)
    assert RUN_KINDS == (
        "analysis",
        "cohort",
        "build",
        "qc",
        "phenotype",
        "protocol",
        "report",
        "bench",
    )


# ---------------------------------------------------------------------------
# 3. Benchmark ledger schema (brief item 2)
# ---------------------------------------------------------------------------


def _bench_line(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "ts": "2026-09-05T00:00:00+00:00",
        "build_id": "20260905T000000-fixture-abc1234",
        "tier": "fixture",
        "step": "stage.mimiciv_hosp.patients",
        "kind": "stage",
        "phase": "total",
        "wall_s": 1.0,
        "duckdb_version": "1.5.5",
        "host": {"cpu": 16, "ram_gb": 64.0},
        "ok": True,
    }
    return {**base, **kw}


def test_benchmark_line_validation() -> None:
    old = BenchmarkLine.model_validate(_bench_line())  # an EP-19-era line still validates
    assert old.run_id is None and old.disk_delta_mb is None
    new = BenchmarkLine.model_validate(
        _bench_line(kind="query", run_id="20260905T120000Z-abc123", disk_delta_mb=0.5)
    )
    assert new.run_id == "20260905T120000Z-abc123" and new.disk_delta_mb == 0.5
    with pytest.raises(ValidationError):
        BenchmarkLine.model_validate(_bench_line(wall_s="fast"))
    with pytest.raises(ValidationError):
        BenchmarkLine.model_validate(_bench_line(bogus=1))
    with pytest.raises(ValidationError):
        line = _bench_line()
        del line["host"]
        BenchmarkLine.model_validate(line)
    from typing import get_args

    from mimicwarehouse.dag.spec import Kind

    assert set(get_args(Kind)) <= set(BENCHMARK_KINDS), "every runner step kind is a ledger kind"
    assert {"build", "verify", "concept", "mart", "query", "page", "bench"} <= set(BENCHMARK_KINDS)


def test_run_bench_appends_through_dag_benchmarks(data_root: Path) -> None:
    settings = _settings()
    with run_mod.start("bench", tier="fixture", kind="bench", settings=settings, doctor=False) as r:
        r.bench("query", "patients_by_era", wall_s=0.25, rows=5)
    run_mod.bench("bench", "standalone", wall_s=0.5, tier="fixture", settings=settings)
    lines = [
        json.loads(line)
        for line in benchmarks_mod.benchmarks_path(settings)
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    assert len(lines) == 2
    first, second = (BenchmarkLine.model_validate(line) for line in lines)
    assert first.run_id == r.run_id and first.build_id == r.run_id and first.kind == "query"
    assert first.step == "patients_by_era" and first.rows == 5 and first.ok
    assert second.run_id is None and second.kind == "bench"
    assert "-fixture-" in second.build_id, "a runner-shaped build id when no run is given"
    assert benchmarks_mod.read(settings).height == 2
    assert benchmarks_mod.summarize(settings, kind="query").height == 1


# ---------------------------------------------------------------------------
# 4. runs.duckdb views + mwh runs refresh / list / show (brief item 3)
# ---------------------------------------------------------------------------


def test_views_are_typed_and_empty_on_a_fresh_root(data_root: Path) -> None:
    settings = _settings()
    assert [name for name, _ in runs_db_views(settings)] == list(RUNS_DB_VIEWS[1:])
    build_runs_db(settings)
    for view in RUNS_DB_VIEWS:
        assert _view_count(settings, view) == 0, view
    import duckdb

    con = duckdb.connect(str(runs_db_path(settings)), read_only=True)
    try:
        columns = [row[0] for row in con.execute("DESCRIBE manifests").fetchall()]
        assert columns == list(MANIFEST_COLUMNS)
        assert con.execute("SELECT kind, count(*) FROM ledger GROUP BY 1").fetchall() == []
    finally:
        con.close()


def test_refresh_builds_views_and_list_show_display_the_run(data_root: Path) -> None:
    settings = _settings()
    with run_mod.start(
        "viewed", tier="fixture", kind="cohort", settings=settings, doctor=False
    ) as r:
        r.record_attrition(
            [
                {"step": "base", "label": "all", "n_units": 50, "n_subjects": 40},
                {"step": "cohort", "label": "kept", "n_units": 30, "n_subjects": 30},
            ]
        )
        r.record_snapshot("core", "b" * 64)
        r.bench("query", "q", wall_s=0.1)
    with (
        pytest.raises(RuntimeError),
        run_mod.start("broken", tier="dev", kind="analysis", settings=settings, doctor=False),
    ):
        raise RuntimeError("nope")
    # a torn trailing ledger line (crash mid-append) is tolerated everywhere
    with ledger_path(settings).open("ab") as f:
        f.write(b'{"run_id": "2026')

    runner = helpers.cli_runner()
    root = ["--data-root", str(data_root)]
    refreshed = runner.invoke(app, [*root, "runs", "refresh"])
    assert refreshed.exit_code == 0, refreshed.output
    assert "refreshed" in refreshed.output and "audit" in refreshed.output
    for view in RUNS_DB_VIEWS[1:]:
        assert view in refreshed.output
    assert _view_count(settings, "ledger") == 2
    assert _view_count(settings, "ledger", "WHERE status = 'failed'") == 1
    assert _view_count(settings, "manifests") == 2
    assert _view_count(settings, "benchmarks") == 1
    assert _view_count(settings, "benchmarks", f"WHERE run_id = '{r.run_id}'") == 1
    assert _view_count(settings, "attrition") == 2
    assert _view_count(settings, "attrition", "WHERE step = 'cohort' AND n_units = 30") == 1
    failed = "WHERE json_extract_string(error, 'type') = 'RuntimeError'"
    assert _view_count(settings, "manifests", failed) == 1

    listed = runner.invoke(app, [*root, "runs", "list", "--last", "5"])
    assert listed.exit_code == 0, listed.output
    assert r.run_id in listed.output and "viewed" in listed.output and "broken" in listed.output
    assert listed.output.index("broken") < listed.output.index("viewed"), "newest first"
    only_cohort = runner.invoke(app, [*root, "runs", "list", "--kind", "cohort", "--json"])
    assert only_cohort.exit_code == 0, only_cohort.output
    payload = json.loads(only_cohort.stdout)
    assert [row["run_id"] for row in payload] == [r.run_id]
    assert set(payload[0]) == set(LEDGER_FIELDS)
    only_dev = runner.invoke(app, [*root, "runs", "list", "--tier", "dev", "--json"])
    assert [row["name"] for row in json.loads(only_dev.stdout)] == ["broken"]
    bad_kind = runner.invoke(app, [*root, "runs", "list", "--kind", "mystery"])
    assert bad_kind.exit_code == 2 and "unknown kind" in bad_kind.output

    shown = runner.invoke(app, [*root, "runs", "show", r.run_id])
    assert shown.exit_code == 0, shown.output
    for token in ("snapshot_ids", "attrition", "git_sha", "duckdb_version", "sql", "b" * 64):
        assert token in shown.output, token
    shown_json = runner.invoke(app, [*root, "runs", "show", r.run_id, "--json"])
    assert shown_json.exit_code == 0, shown_json.output
    assert json.loads(shown_json.stdout)["run_id"] == r.run_id
    missing = runner.invoke(app, [*root, "runs", "show", "20260101T000000Z-000000"])
    assert missing.exit_code == 2 and "no manifest" in missing.output
    traversal = runner.invoke(app, [*root, "runs", "show", "../warehouse"])
    assert traversal.exit_code == 2 and "not a run id" in traversal.output
    config.configure()


def test_list_on_a_fresh_root_says_so(data_root: Path) -> None:
    runner = helpers.cli_runner()
    result = runner.invoke(app, ["--data-root", str(data_root), "runs", "list"])
    assert result.exit_code == 0, result.output
    assert "no runs recorded" in result.output
    config.configure()


# ---------------------------------------------------------------------------
# 5. safe_query over the ledger views (fixture lake; amendment c)
# ---------------------------------------------------------------------------


def test_safe_query_reads_ledger_views_and_sees_a_rebuild(fixture_lake_settings: Settings) -> None:
    settings = fixture_lake_settings
    with run_mod.start("sq1", tier="fixture", kind="qc", settings=settings, doctor=False):
        pass
    with pytest.raises(safe.SafeQueryError):  # a `usage: ` audit line, > 64 characters
        safe_query("SELECT 1", tier="bogus", settings=settings)
    build_runs_db(settings)
    by_kind = safe_query(
        "SELECT kind, count(*) AS n FROM runs.ledger GROUP BY 1 ORDER BY 1",
        tier="fixture",
        k=1,
        settings=settings,
    )
    assert "qc" in by_kind.df["kind"].to_list()
    qc_before = int(by_kind.df.filter(by_kind.df["kind"] == "qc")["n"][0])
    usage = safe_query(
        "SELECT count(*) AS n FROM runs.audit WHERE refusal_reason LIKE 'usage: %'",
        tier="fixture",
        k=1,
        settings=settings,
    )
    assert int(usage.df["n"][0]) >= 1
    # the label columns whose values exceed 64 characters are admitted by name
    assert {"refusal_reason", "error"} <= safe.LABEL_COLUMN_NAMES
    reasons = safe_query(
        "SELECT refusal_reason, count(*) AS n FROM runs.audit "
        "WHERE refusal_reason LIKE 'usage: %' GROUP BY 1",
        tier="fixture",
        k=1,
        settings=settings,
    )
    assert reasons.n_rows >= 1
    with pytest.raises(safe.SafeQueryRefused, match="count-family"):
        safe_query("SELECT kind FROM runs.ledger GROUP BY 1", tier="fixture", settings=settings)
    benchmarks = safe_query(
        "SELECT kind, count(*) FROM runs.benchmarks GROUP BY 1",
        tier="fixture",
        k=1,
        settings=settings,
    )
    assert benchmarks.n_rows >= 0  # the acceptance statement verifies and executes
    # a second run + refresh is visible to the next call (refresh=True re-attaches)
    with run_mod.start("sq2", tier="fixture", kind="qc", settings=settings, doctor=False):
        pass
    build_runs_db(settings)
    again = safe_query(
        "SELECT kind, count(*) AS n FROM runs.ledger WHERE kind = 'qc' GROUP BY 1",
        tier="fixture",
        k=1,
        settings=settings,
    )
    assert int(again.df["n"][0]) == qc_before + 1


# ---------------------------------------------------------------------------
# 6. Reproduction block (brief item 4)
# ---------------------------------------------------------------------------


def test_reproduction_block_renders_the_ep32_shape(data_root: Path) -> None:
    settings = _settings()
    with run_mod.start(
        "capstone",
        tier="fixture",
        kind="report",
        settings=settings,
        command="mwh tracer --tier fixture",
        protocol_id="proto-1",
        protocol_hash="c" * 64,
        claim_type="associational (exploratory)",
        doctor=False,
    ) as r:
        r.record_snapshot("core", "d" * 64)
        r.record_sql("q", "SELECT count(*) AS n FROM meta.tables")
    block = reproduction_block(r.run_id, settings)
    assert block.startswith("## Reproduction") and "## Provenance" in block
    manifest = _manifest_file(r)
    for token in (
        r.run_id,
        manifest["git_sha"],
        "tier `fixture`",
        "mwh tracer --tier fixture",
        "core `" + "d" * 64,
        "hash `" + "c" * 64,
        "proto-1",
        "associational (exploratory)",
        "MIMIC-IV analyses are retrospective",
        "DuckDB `1.5.5`",
        manifest["uv_lock_sha256"],
    ):
        assert token in block, token
    assert block.isascii()
    assert not guard.id_band_hits(block.encode("utf-8")), "no guard G4 token in the block"
    with pytest.raises(RunLedgerError):
        reproduction_block("20260101T000000Z-000000", settings)


# ---------------------------------------------------------------------------
# 7. Tracer retrofit (brief item 5): a run record, numbers untouched
# ---------------------------------------------------------------------------


def test_tracer_records_a_run_without_changing_its_files(fixture_lake_settings: Settings) -> None:
    settings = fixture_lake_settings
    before = {line["run_id"] for line in _ledger_lines(settings)}
    result = run_tracer("fixture", settings=settings)
    new = [line for line in _ledger_lines(settings) if line["run_id"] not in before]
    assert len(new) == 1 and new[0]["name"] == "tracer" and new[0]["status"] == "ok"
    ledger_run_id = result.manifest["ledger_run_id"]
    assert new[0]["run_id"] == ledger_run_id
    manifest = read_manifest(ledger_run_id, settings)
    assert manifest.kind == "analysis" and manifest.tier == "fixture"
    assert manifest.claim_type == "associational (exploratory)"
    assert manifest.command == "mwh tracer --tier fixture"
    assert set(manifest.sql) == {f"attrition_{s}" for s in STEPS} | {
        "by_age_gender",
        "by_first_careunit",
        "model_frame",
    }
    for rel in manifest.sql.values():
        assert (run_mod.run_dir(ledger_run_id, settings) / rel).is_file()
    assert [a.step for a in manifest.attrition] == list(STEPS)
    assert [a.n_units for a in manifest.attrition] == [row["n"] for row in result.attrition]
    assert manifest.snapshot_ids.get("core") == result.manifest["core_snapshot_id"]
    assert manifest.audit_ids == result.manifest["audit_ids"]
    # the tracer's own folder is untouched: the same five files, run_id == folder name
    assert sorted(p.name for p in result.out_dir.iterdir()) == [
        "attrition.json",
        "descriptives.json",
        "manifest.json",
        "model.json",
        "report.md",
    ]
    assert result.manifest["run_id"] == result.out_dir.name


# ---------------------------------------------------------------------------
# 8. Docs + import budget
# ---------------------------------------------------------------------------


def test_provenance_doc_and_design_note() -> None:
    text = PROVENANCE_DOC.read_text(encoding="utf-8")
    for required in (
        "run_id",
        "runs/ledger.jsonl",
        "manifest.json",
        "mwh runs refresh",
        "mwh runs list",
        "mwh runs show",
        "never auto-deleted",
        "EP-52",
        "never contain rows",
        "reproduction_block",
        "EP-43",
        "retrospective",
    ):
        assert required in text, f"docs/methods/provenance.md lacks {required!r}"
    design = DESIGN.read_text(encoding="utf-8")
    assert "EP-35" in design and "runs/ledger.jsonl" in design
    assert "> **Note (2026-09-05, EP-35" in design, "DESIGN section 11 needs the dated EP-35 note"
    violations = guard.scan([PROVENANCE_DOC], helpers.REPO_ROOT)
    assert not violations, [f"{v.rule}: {v.path}" for v in violations]


def test_run_module_stays_off_the_startup_path() -> None:
    helpers.assert_import_budget(lazy=("mimicwarehouse.run", "mimicwarehouse.safe"))
    helpers.assert_import_budget("mimicwarehouse.run", lazy=("psutil", "mimicwarehouse.doctor"))


# ---------------------------------------------------------------------------
# 9. Dev tier: one safe_query aggregate inside a run records the core snapshot id
# ---------------------------------------------------------------------------


@pytest.mark.tier("dev")
def test_dev_run_wrapping_one_safe_query_records_snapshot_and_sql(dev_catalog: Path) -> None:
    settings = config.load_settings()
    with run_mod.start(
        "ep35-dev-probe",
        tier="dev",
        kind="qc",
        params={"statement": "patients_by_era"},
        settings=settings,
        command="uv run poe test -m ep_35 --tier dev",
    ) as r:
        result = r.safe_query(
            f"SELECT anchor_year_group, count(*) AS n FROM {HOSP}.patients GROUP BY 1",
            name="patients_by_era",
        )
        assert result.n_rows >= 1
        assert all(int(n) >= 11 or int(n) == 0 for n in result.df["n"].to_list())
    manifest = read_manifest(r.run_id, settings)
    assert manifest.status == "ok" and manifest.tier == "dev"
    core = manifest.snapshot_ids.get("core")
    assert core and len(core) == 64 and core == result.snapshot_id
    assert manifest.sql == {"patients_by_era": "sql/patients_by_era.sql"}
    assert (r.dir / "sql" / "patients_by_era.sql").is_file()
    assert manifest.audit_ids == [result.audit_id]
    assert manifest.doctor is not None and manifest.doctor["checks"]


@pytest.mark.tier("dev", needs="lake")
def test_dev_read_layer_resolves_the_core_snapshot(dev_catalog: Path) -> None:
    settings = config.load_settings()
    with run_mod.start("ep35-dev-layer", tier="dev", kind="qc", settings=settings) as r:
        snapshot = r.read_layer("core")
    assert len(snapshot) == 64 and all(c in "0123456789abcdef" for c in snapshot)
    assert read_manifest(r.run_id, settings).snapshot_ids == {"core": snapshot}
