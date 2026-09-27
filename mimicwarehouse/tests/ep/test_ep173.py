"""EP-173 — Debt sweep (P4): carried-low fixes.

Fixture tier only; nothing here reads the data root. One test (or a small group) per
AUDIT-1 row the sweep landed, in the brief's order:

1. **SGT-5** (governance): every ``duckdb_*`` / ``pragma_*`` metadata function and
   ``version()`` / ``current_database()`` / ``current_schema()`` is refused by
   ``safe_query`` — in-process (``SafeQueryRefused``), through ``mwh sql`` (exit 3) and in
   the audit line (``allowed = false``, the sanitized reason) — while
   ``information_schema.tables``, ``DESCRIBE`` and ``mwh sql --describe`` still verify.
2. The XS correctness fixes: DKB-6 (profile placeholders from the column list), P01-2
   (credentialed-lake containment in both directions), LGR-6 (the audit ``sql_text`` cap +
   ``sql_truncated``; ``runs.audit`` carries the column with NULL on older lines), WIN-7
   (unique temp sibling, cleaned on a failed write), DAG-10 (the lock is per data root),
   P3C-7 (the discovery walker's single-file rule warns).
3. The S items: CTR-7 (orphan manifest records pruned before any write; they cannot
   satisfy the completeness gate), CLI-8 (``parse_sums_file`` / ``parse_sums_text``; the
   promoted ``safe.contract_names`` / ``doctor.bitlocker_protection``), DAG-9 (a failed
   background job: state, exit code, finished, the failed provenance run; ``update_job``
   beside the supervisor rewrite), DKB-7 (the ``json_serialize_sql`` spellings and the
   partitioned-``COPY`` ``OVERWRITE_OR_IGNORE`` semantics pinned so a DuckDB bump fails
   loudly), TST-5 (``helpers.STAGED_TABLE_COUNT`` derived from the contract), TST-7 (the
   hook budget measured in-process).

Everything asserted is counts, schemas, refusal reasons, paths under ``tmp_path`` and
synthetic values (the committed fixture, ids >= 90 000 000; crafted tables of small
integers) — never a row of real data.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import logging
import os
import re
import time
import uuid
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

import duckdb
import pytest

import helpers
from mimicwarehouse import backup, config, demo, disclose, doctor, fsio, inventory, safe
from mimicwarehouse import run as run_mod
from mimicwarehouse.catalog import profile as profile_mod
from mimicwarehouse.catalog.build import STAGED_SCHEMAS
from mimicwarehouse.cli import app
from mimicwarehouse.concepts import runner as concepts_runner
from mimicwarehouse.config import UnsafeLocationError, assert_not_credentialed_lake
from mimicwarehouse.console import EXIT_FINDINGS, EXIT_REFUSED
from mimicwarehouse.dag import jobs as jobs_mod
from mimicwarehouse.dag import runner as runner_mod
from mimicwarehouse.engine import open_duckdb
from mimicwarehouse.inventory import (
    FileRecord,
    RawManifest,
    compute_snapshot_id,
    dataset_dir,
    prune_orphan_records,
    rel_path_for,
)
from mimicwarehouse.loader import buckets as buckets_mod
from mimicwarehouse.loader.buckets import BUCKET_COLUMN
from mimicwarehouse.loader.manifest import update_status
from mimicwarehouse.safe import (
    SQL_TEXT_MAX_CHARS,
    SafeQueryRefused,
    audit_path,
    build_runs_db,
    safe_query,
)
from mimicwarehouse.schema import load_contract

if TYPE_CHECKING:
    from mimicwarehouse.config import Settings
    from mimicwarehouse.schema.contract import Table

pytestmark = pytest.mark.ep_173

HOSP = "mimiciv_hosp"
DERIVED = "mimiciv_derived"
STAMP = "2026-09-26T00:00:00+00:00"


def _audit_lines(settings: Settings) -> list[dict[str, Any]]:
    path = audit_path(settings)
    if not path.is_file():
        return []
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def _sql_str(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


@pytest.fixture
def data_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    root = helpers.tmp_data_root(monkeypatch, tmp_path)
    yield root
    config.configure()


# ---------------------------------------------------------------------------
# 1. SGT-5 — the metadata-function family is refused; information_schema still verifies
# ---------------------------------------------------------------------------

METADATA_STATEMENTS: tuple[str, ...] = (
    "SELECT count(*) AS n FROM duckdb_databases()",
    "SELECT count(*) AS n FROM duckdb_tables()",
    "SELECT count(*) AS n FROM duckdb_views()",
    "SELECT count(*) AS n FROM duckdb_columns()",
    "SELECT count(*) AS n FROM duckdb_extensions()",
    "SELECT count(*) AS n FROM duckdb_secrets()",
    "SELECT count(*) AS n FROM duckdb_temporary_files()",
    "SELECT count(*) AS n FROM duckdb_settings()",
    "SELECT count(*) AS n FROM pragma_database_list()",
    "SELECT count(*) AS n FROM pragma_table_info('meta.catalog_info')",
    "SELECT version() AS v, count(*) AS n FROM meta.catalog_info",
    "SELECT current_database() AS d, count(*) AS n FROM meta.catalog_info",
    "SELECT current_schema() AS s, count(*) AS n FROM meta.catalog_info",
    # the tree pass sees every branch: a CTE / subquery cannot hide the call
    "WITH d AS (SELECT database_name FROM duckdb_databases()) SELECT count(*) AS n FROM d",
    (
        "SELECT count(*) AS n FROM meta.catalog_info "
        "WHERE build_id IN (SELECT database_name FROM duckdb_databases())"
    ),
)


@pytest.mark.parametrize("sql", METADATA_STATEMENTS)
def test_metadata_functions_refused_and_audited(fixture_lake_settings: Settings, sql: str) -> None:
    with pytest.raises(SafeQueryRefused) as excinfo:
        safe_query(sql, tier="fixture", settings=fixture_lake_settings)
    reason = str(excinfo.value)
    assert "is refused" in reason and "information_schema" in reason
    last = _audit_lines(fixture_lake_settings)[-1]
    assert last["allowed"] is False and last["sql_truncated"] is False
    assert last["refusal_reason"] == reason
    assert last["statement_sha256"] == hashlib.sha256(sql.encode("utf-8")).hexdigest()
    # the sanitized reason names the function, never a path of the environment
    assert str(fixture_lake_settings.data_root) not in last["refusal_reason"]
    assert ".duckdb" not in last["refusal_reason"]


def test_forbidden_sets_carry_the_sgt5_additions() -> None:
    assert {"duckdb_", "pragma_"} <= set(safe.FORBIDDEN_FUNCTION_PREFIXES)
    assert {"version", "current_database", "current_schema"} <= safe.FORBIDDEN_FUNCTION_NAMES
    # information_schema stays the sanctioned metadata surface
    assert "information_schema" in safe.REGISTRY_SCHEMAS
    assert safe.is_registry_ref("information_schema", "columns")


def test_metadata_refusal_through_mwh_sql_exits_3(fixture_lake_settings: Settings) -> None:
    runner = helpers.cli_runner()
    try:
        before = len(_audit_lines(fixture_lake_settings))
        result = runner.invoke(
            app,
            [
                "--data-root",
                str(fixture_lake_settings.data_root),
                "sql",
                "--tier",
                "fixture",
                "SELECT count(*) AS n FROM duckdb_databases()",
            ],
        )
        assert result.exit_code == EXIT_REFUSED, result.output
        lines = _audit_lines(fixture_lake_settings)
        assert len(lines) == before + 1
        assert lines[-1]["allowed"] is False and "duckdb_databases" in lines[-1]["refusal_reason"]
    finally:
        config.configure()


def test_information_schema_and_describe_still_verify(fixture_lake_settings: Settings) -> None:
    contract = load_contract()
    tables = safe_query(
        f"SELECT count(*) AS n FROM information_schema.tables WHERE table_schema = '{HOSP}'",
        tier="fixture",
        k=1,
        settings=fixture_lake_settings,
    )
    assert tables.n_rows == 1 and int(tables.df["n"][0]) == len(contract.by_schema(HOSP))
    comments = safe_query(
        "SELECT count(*) AS n FROM information_schema.columns "
        f"WHERE table_schema = '{HOSP}' AND column_comment IS NOT NULL",
        tier="fixture",
        k=1,
        settings=fixture_lake_settings,
    )
    assert int(comments.df["n"][0]) == sum(len(t.columns) for t in contract.by_schema(HOSP))
    described = safe_query(
        f'DESCRIBE {HOSP}."admissions"', tier="fixture", settings=fixture_lake_settings
    )
    assert described.df.height == len(contract.table(f"{HOSP}.admissions").columns)
    assert "column_name" in described.df.columns and "column_type" in described.df.columns
    assert _audit_lines(fixture_lake_settings)[-1]["allowed"] is True


def test_mwh_sql_describe_reads_comments_from_information_schema(
    fixture_lake_settings: Settings,
) -> None:
    runner = helpers.cli_runner()
    try:
        result = runner.invoke(
            app,
            [
                "--data-root",
                str(fixture_lake_settings.data_root),
                "sql",
                "--tier",
                "fixture",
                "--format",
                "json",
                "--describe",
                f"{HOSP}.patients",
            ],
        )
        assert result.exit_code == 0, result.output
        columns = json.loads(result.output)["columns"]
        assert [c["column"] for c in columns] == [
            c.name for c in load_contract().table(f"{HOSP}.patients").columns
        ]
        assert all(c["comment"] for c in columns), "every contract comment surfaces"
        # both statements the helper ran were allowed — none touched duckdb_columns()
        tail = _audit_lines(fixture_lake_settings)[-2:]
        assert [line["allowed"] for line in tail] == [True, True]
        assert all("duckdb_" not in line["sql_text"] for line in tail)
    finally:
        config.configure()


# ---------------------------------------------------------------------------
# 2. XS correctness fixes, one test each
# ---------------------------------------------------------------------------


def test_dkb6_profile_placeholders_come_from_the_column_list(
    fixture_lake_settings: Settings, tmp_path: Path
) -> None:
    con = open_duckdb("app", settings=fixture_lake_settings)
    try:
        # a DECIMAL(18,3) column holds a comma: splitting the DDL on commas over-counted it
        columns = [("name", "VARCHAR"), ("amount", "DECIMAL(18,3)"), ("n", "BIGINT")]
        rows: list[list[Any]] = [
            ["a", Decimal("1.500"), 1],
            ["b", Decimal("2.250"), 2],
            ["c", None, 3],
        ]
        dest = tmp_path / "profile_probe.parquet"
        n_bytes = profile_mod._write_parquet(con, dest, columns, rows)
        assert n_bytes == dest.stat().st_size > 0
        back = con.execute(
            f"SELECT name, amount, n FROM read_parquet({_sql_str(dest.as_posix())}) ORDER BY n"
        ).fetchall()
        assert back == [("a", Decimal("1.500"), 1), ("b", Decimal("2.250"), 2), ("c", None, 3)]
        assert not list(tmp_path.glob("*.tmp"))
    finally:
        con.close()
    # the two shipped column lists are the profile files' DDL, one spelling each
    assert [name for name, _ in profile_mod.TABLES_COLUMNS] == [
        "schema",
        "table",
        "row_count",
        "build_id",
        "snapshot_id",
        "profiled_at",
    ]
    assert [name for name, _ in profile_mod.COLUMNS_COLUMNS] == [
        "schema",
        "table",
        "column",
        "null_pct",
        "approx_distinct",
        "min_value",
        "max_value",
        "build_id",
        "snapshot_id",
        "profiled_at",
    ]


def test_p01_2_credentialed_lake_containment_both_directions(data_root: Path) -> None:
    settings = config.get_settings()
    lake = settings.layout["lake"]
    # the default synthetic roots live *under* lake/ beside the credentialed layers: allowed
    for fine in (
        settings.lake_root("fixture"),
        settings.lake_root("demo"),
        settings.layout["lake_fixture"] / "nested",
        settings.data_root / "elsewhere",
    ):
        assert_not_credentialed_lake("fixture", fine, settings)
        assert_not_credentialed_lake("demo", fine, settings)
    # equal to, containing, or inside a credentialed layer: refused
    for bad in (
        lake,
        settings.data_root,
        Path(settings.data_root.anchor),
        settings.layout["lake_core"],
        settings.layout["lake_core"] / HOSP / "patients",
        settings.layout["lake_derived"] / "dev",
        settings.layout["lake_marts"],
        settings.layout["lake_manifests"] / "raw",
    ):
        with pytest.raises(UnsafeLocationError, match="credentialed"):
            assert_not_credentialed_lake("fixture", bad, settings)
    # the credentialed tiers are the lake's writers: a no-op for them
    assert_not_credentialed_lake("dev", lake, settings)
    assert_not_credentialed_lake("full", settings.data_root, settings)
    assert set(config.CREDENTIALED_LAKE_LAYERS) == {
        "lake_core",
        "lake_derived",
        "lake_marts",
        "lake_manifests",
    }


def test_lgr6_audit_sql_text_is_capped_and_flagged(fixture_lake_settings: Settings) -> None:
    settings = fixture_lake_settings
    assert safe.audit_sql_text("x" * 10, max_chars=4) == ("xxxx", True)
    assert safe.audit_sql_text("xy") == ("xy", False)
    filler = " AND ".join(["anchor_age >= 0"] * 700)
    long_sql = f"SELECT count(*) AS n FROM {HOSP}.patients WHERE {filler}"
    assert len(long_sql) > SQL_TEXT_MAX_CHARS
    result = safe_query(long_sql, tier="fixture", k=1, settings=settings)
    last = _audit_lines(settings)[-1]
    assert last["audit_id"] == result.audit_id and last["allowed"] is True
    assert last["sql_truncated"] is True and len(last["sql_text"]) == SQL_TEXT_MAX_CHARS
    assert last["sql_text"] == long_sql[:SQL_TEXT_MAX_CHARS]
    full_sha = hashlib.sha256(long_sql.encode("utf-8")).hexdigest()
    assert last["statement_sha256"] == full_sha == result.statement_sha256
    # a refused long statement is recorded the same way
    with pytest.raises(SafeQueryRefused):
        safe_query(
            long_sql.replace("count(*) AS n", "anchor_age"), tier="fixture", settings=settings
        )
    refused = _audit_lines(settings)[-1]
    assert refused["allowed"] is False and refused["sql_truncated"] is True
    assert len(refused["sql_text"]) == SQL_TEXT_MAX_CHARS
    # a short statement is not flagged
    safe_query(f"SELECT count(*) AS n FROM {HOSP}.patients", tier="fixture", k=1, settings=settings)
    short = _audit_lines(settings)[-1]
    assert short["sql_truncated"] is False and short["sql_text"].endswith("patients")


def test_lgr6_runs_audit_view_carries_sql_truncated_with_null_for_older_lines(
    fixture_lake_settings: Settings,
) -> None:
    settings = fixture_lake_settings
    # one flagged line of this session's own ...
    filler = " OR ".join(["anchor_age < 200"] * 600)
    safe_query(
        f"SELECT count(*) AS n FROM {HOSP}.patients WHERE {filler}",
        tier="fixture",
        k=1,
        settings=settings,
    )
    # ... and one crafted pre-EP-173 line: every field but the new one
    legacy = safe.AuditLine(
        audit_id=uuid.uuid4().hex,
        ts=STAMP,
        actor="agent",
        tier="fixture",
        statement_sha256="0" * 64,
        sql_text="SELECT 1",
        allowed=False,
        refusal_reason="crafted pre-EP-173 line (test_ep173)",
        k=11,
        wall_ms=0.0,
        duckdb_version=duckdb.__version__,
        snapshot_ids={},
        git_sha=None,
    ).model_dump(mode="json")
    del legacy["sql_truncated"]
    fsio.append_jsonl(audit_path(settings), legacy)
    build_runs_db(settings)
    described = safe_query("DESCRIBE runs.audit", tier="fixture", settings=settings)
    assert "sql_truncated" in described.df["column_name"].to_list()
    flagged = safe_query(
        "SELECT count(*) AS n FROM runs.audit WHERE sql_truncated",
        tier="fixture",
        k=1,
        settings=settings,
    )
    assert int(flagged.df["n"][0]) >= 1
    nulls = safe_query(
        "SELECT count(*) AS n FROM runs.audit WHERE sql_truncated IS NULL",
        tier="fixture",
        k=1,
        settings=settings,
    )
    assert int(nulls.df["n"][0]) >= 1, "a line without the key reads NULL, not an error"


def test_win7_atomic_write_temp_name_is_unique_and_cleaned_on_a_failed_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "state.json"
    seen: list[str] = []
    real_replace = os.replace

    def spy(src, dst):
        seen.append(Path(src).name)
        return real_replace(src, dst)

    monkeypatch.setattr(fsio.os, "replace", spy)
    fsio.atomic_write_text(target, "one\n")
    fsio.atomic_write_text(target, "two\n")
    assert target.read_text(encoding="utf-8") == "two\n"
    assert len(seen) == 2 and len(set(seen)) == 2, "one temp name per write, never shared"
    for name in seen:
        assert name.startswith("state.json.") and name.endswith(fsio.TEMP_SUFFIX)
        assert f".{os.getpid()}." in name
    assert not list(tmp_path.glob("*.tmp"))
    assert fsio.temp_sibling(target) != fsio.temp_sibling(target)
    # the write itself failing (a lone surrogate is not UTF-8-encodable) leaves no temp file
    with pytest.raises(UnicodeEncodeError):
        fsio.atomic_write_text(target, "\ud800")
    assert not list(tmp_path.glob("*.tmp"))
    assert target.read_text(encoding="utf-8") == "two\n"


def test_dag10_build_lock_is_described_per_data_root(data_root: Path) -> None:
    settings = config.get_settings()
    held = runner_mod.acquire_lock(settings, "ep173-first")
    try:
        with pytest.raises(runner_mod.BuildLockError, match="per data root"):
            runner_mod.acquire_lock(settings, "ep173-second")
    finally:
        held.unlink()
    source = inspect.getsource(runner_mod)
    assert "per machine" not in source
    assert "per **data\n  root**" in source or "per data root" in source
    design = (helpers.WORKSPACE / "DESIGN.md").read_text(encoding="utf-8")
    assert re.search(r"one build-profile connection per data root at a\s+time", design)
    assert "per machine at a time" not in design


def test_p3c7_register_derived_warns_on_the_single_file_rule(
    fixture_lake_settings: Settings, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    lake = tmp_path / "lake"
    ghost = lake / "derived" / "fixture" / DERIVED / "ghost"
    ghost.mkdir(parents=True)
    (ghost / "part-1.parquet").write_bytes(b"")  # a part file, but not *the* part file
    twins = lake / "derived" / "fixture" / DERIVED / "twins"
    twins.mkdir(parents=True)
    con = open_duckdb("app", settings=fixture_lake_settings)
    try:
        for name, value in (("part-0.parquet", 1), ("extra.parquet", 2)):
            con.execute(
                f"COPY (SELECT {value} AS x) TO {_sql_str((twins / name).as_posix())} "
                "(FORMAT PARQUET)"
            )
        update_status(lake, f"{DERIVED}.twins", tier_complete="full", per_tier=True)
        update_status(lake, f"{DERIVED}.ghost", tier_complete="full", per_tier=True)
        con.execute("CREATE SCHEMA meta")
        con.execute(f"CREATE TABLE meta.catalog_info AS SELECT {_sql_str(str(lake))} AS lake_root")
        with caplog.at_level(logging.WARNING, logger="mimicwarehouse.concepts.runner"):
            concepts_runner.register_derived(con, "fixture")
        messages = [record.getMessage() for record in caplog.records]
        assert any(
            "ghost" in m and "skipped" in m and "part-0.parquet" in m and "part-1.parquet" in m
            for m in messages
        ), messages
        assert any("twins" in m and "extra.parquet" in m and "ignored" in m for m in messages), (
            messages
        )
        views = {
            str(r[0])
            for r in con.execute(
                f"SELECT view_name FROM duckdb_views() WHERE schema_name = '{DERIVED}'"
            ).fetchall()
        }
        assert views == {"twins"}, "the multi-file directory registers part-0 only; ghost never"
        assert con.execute(f"SELECT x FROM {DERIVED}.twins").fetchall() == [(1,)]
    finally:
        con.close()
    assert "single-file rule" in (concepts_runner.__doc__ or "")


# ---------------------------------------------------------------------------
# 3. S-sized provenance and test items
# ---------------------------------------------------------------------------


def _record(table: Table, rel_path: str | None = None) -> FileRecord:
    return FileRecord(
        dataset=table.dataset,
        dataset_dir=dataset_dir(table.dataset),
        module=table.csv_path.split("/", 1)[0],
        schema_name=table.schema_name,
        table=table.name,
        rel_path=rel_path or rel_path_for(table),
        bytes=1,
        mtime=STAMP,
        mtime_ns=1,
        sha256="0" * 64,
        header=[c.name for c in table.columns],
        header_matches_contract=True,
        rows=1,
        rowcount_method="duckdb",
        seconds_hash=0.0,
        seconds_rows=0.0,
        recorded_at=STAMP,
    )


def test_ctr7_orphan_records_cannot_satisfy_the_completeness_gate() -> None:
    contract = load_contract()
    tables = list(contract.tables)
    assert len(tables) == inventory.FILES_EXPECTED
    current = [_record(t) for t in tables[:-1]]
    orphan = _record(
        tables[-1], rel_path=rel_path_for(tables[-1]).replace(tables[-1].name, "ghost")
    )
    manifest = RawManifest(root=Path("."), records={r.rel_path: r for r in [*current, orphan]})
    assert manifest.files_done == inventory.FILES_EXPECTED
    # the pre-EP-173 gate: 40 current lines + 1 orphan looked complete and produced an id
    assert compute_snapshot_id(manifest.records.values()) is not None
    dropped = prune_orphan_records(manifest, contract)
    assert [r.rel_path for r in dropped] == [orphan.rel_path]
    assert manifest.files_done == inventory.FILES_EXPECTED - 1
    assert compute_snapshot_id(manifest.records.values()) is None
    assert prune_orphan_records(manifest, contract) == []


class _Collect(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


def test_ctr7_inventory_build_prunes_orphans_before_writing(
    data_root: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    monkeypatch.setenv("MWH_SOURCE_ROOT", str(source_root))
    config.configure()
    settings = config.get_settings()
    assert settings.source_root == source_root.resolve()
    contract = load_contract()
    patients = contract.table(f"{HOSP}.patients")
    keep = _record(patients)
    orphan = _record(patients, rel_path=rel_path_for(patients).replace("patients", "ghost"))
    manifest_root = tmp_path / "manifests"
    manifest_root.mkdir()
    inventory.write_dataset_manifest(manifest_root, patients.dataset, [keep, orphan])
    assert (manifest_root / f"{patients.dataset}.jsonl").read_text("utf-8").count("\n") == 2
    collector = _Collect()
    logger = logging.getLogger("mimicwarehouse.inventory")
    previous_level = logger.level
    logger.addHandler(collector)
    logger.setLevel(logging.INFO)  # pytest leaves the root at WARNING: INFO would be dropped
    try:
        result = inventory.build_inventory(settings, quiet=True, manifest_root=manifest_root)
    finally:
        logger.removeHandler(collector)
        logger.setLevel(previous_level)
    assert result.pruned == [orphan.rel_path]
    assert result.files_done == 1 and result.raw_snapshot_id is None
    assert result.processed == [] and len(result.missing) == inventory.FILES_EXPECTED
    after = inventory.load_raw_manifest(settings, manifest_root)
    assert set(after.records) == {keep.rel_path}
    assert after.snapshot["files_done"] == 1 and after.snapshot["raw_snapshot_id"] is None
    text = (manifest_root / f"{patients.dataset}.jsonl").read_text(encoding="utf-8")
    assert "ghost" not in text and text.count("\n") == 1
    assert any(m.startswith("pruned: ") and "ghost" in m for m in collector.messages)


def test_cli8_renames_and_promotions_keep_one_phase_aliases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert inventory.parse_sha256sums is inventory.parse_sums_file
    assert demo.parse_sha256sums is demo.parse_sums_text
    assert safe._contract_names is safe.contract_names
    assert doctor._bitlocker_protection is doctor.bitlocker_protection
    sha = "ab" * 32
    sums = tmp_path / "SUMS.txt"
    sums.write_text(f"{sha}  hosp/admissions.csv.gz\n", encoding="utf-8")
    assert inventory.parse_sums_file(sums) == {"hosp/admissions.csv.gz": sha}
    assert demo.parse_sums_text(f"{sha}  hosp/admissions.csv.gz\n", where="t") == [
        (sha, "hosp/admissions.csv.gz")
    ]
    assert disclose._free_text_names() == safe.free_text_column_names() == safe.contract_names()[1]
    assert "comments" in safe.free_text_column_names()
    assert safe.identifier_column_names() == safe.contract_names()[0]
    if config.IS_WINDOWS:
        monkeypatch.setattr(doctor, "bitlocker_protection", lambda drive: 7)
        assert backup._bitlocker_state("C:") == 7
    # no cross-module private import remains; the old parser name is only its alias line
    src_root = Path(inventory.__file__).parent
    assert "_contract_names" not in (src_root / "disclose.py").read_text("utf-8")
    assert "_bitlocker_protection" not in (src_root / "backup.py").read_text("utf-8")
    for module in ("inventory.py", "demo.py"):
        assert "parse_sha256sums(" not in (src_root / module).read_text("utf-8"), module


def _wait_for_job(job: str, settings: Settings, timeout_s: float = 240.0) -> jobs_mod.JobInfo:
    deadline = time.monotonic() + timeout_s
    current = jobs_mod.read_job(job, settings)
    while time.monotonic() < deadline:
        current = jobs_mod.read_job(job, settings) or current
        if current is not None and current.state != "running":
            break
        time.sleep(0.5)
    assert current is not None, "the job state file never appeared"
    return current


def test_dag9_failed_job_is_recorded_with_a_failed_provenance_run(data_root: Path) -> None:
    settings = config.get_settings()
    job = "ep173-failing"
    # meta.profile over an empty lake fails inside the run (ProfileError): the child's
    # `mwh build` exits EXIT_FINDINGS, the supervisor rewrites the state file
    argv = ["build", "--tier", "fixture", "--select", "meta.profile", "--job", job]
    launched = jobs_mod.launch(argv, job, settings)
    assert launched.state == "running" and launched.pid > 0
    current = _wait_for_job(job, settings)
    assert current.state == "failed", current
    assert current.exit_code == EXIT_FINDINGS and current.finished is not None
    log_text = Path(current.log).read_text(encoding="utf-8", errors="replace")
    assert f"exit code={EXIT_FINDINGS} state=failed" in log_text
    assert "ProfileError" in log_text or "failed" in log_text
    runs = run_mod.list_runs(settings, kind="build", last=1)
    assert runs, "every CLI build is a provenance run (EP-37)"
    assert runs[0]["status"] == "failed" and runs[0]["tier"] == "fixture"
    # the CLI surfaces the terminal state
    result = helpers.cli_runner().invoke(app, ["jobs", "--job", job, "--tail", "3"])
    assert result.exit_code == 0 and "state=failed" in result.output


def test_dag9_update_job_merges_fields_beside_the_supervisor_rewrite(data_root: Path) -> None:
    settings = config.get_settings()
    assert jobs_mod.update_job("ep173-never-launched", settings, state="failed") is None
    job = "ep173-child"
    path = jobs_mod.job_json_path(job, settings)
    jobs_mod._write_info(
        path,
        jobs_mod.JobInfo(
            job=job,
            pid=0,
            argv=["--version"],
            started=STAMP,
            log=str(jobs_mod.job_log_path(job, settings)),
        ),
    )
    finished = "2026-09-26T00:00:01+00:00"
    merged = jobs_mod.update_job(job, settings, state="failed", exit_code=2, finished=finished)
    assert merged is not None
    assert (merged.state, merged.exit_code, merged.finished) == ("failed", 2, finished)
    assert jobs_mod.read_job(job, settings) == merged
    # the supervisor's own rewrite over the same file: a child that exits non-zero
    code = jobs_mod._supervise(path, ["--no-such-option"])
    assert code == 2
    final = jobs_mod.read_job(job, settings)
    assert final is not None and final.state == "failed" and final.exit_code == 2
    assert final.finished is not None and final.finished != finished


def _ast(con: duckdb.DuckDBPyConnection, sql: str) -> dict[str, Any]:
    row = con.execute("SELECT json_serialize_sql(?)", [sql]).fetchone()
    assert row is not None
    doc = json.loads(row[0])
    assert not doc.get("error"), doc
    return doc["statements"][0]["node"]


def test_dkb7_json_serialize_sql_spellings_are_pinned(fixture_lake_settings: Settings) -> None:
    con = open_duckdb("app", settings=fixture_lake_settings)
    try:
        by_name = _ast(
            con, "SELECT count(*) AS n FROM meta.a UNION BY NAME SELECT count(*) AS n FROM meta.b"
        )
        assert by_name["type"] == "SET_OPERATION_NODE" and by_name["setop_type"] == "UNION_BY_NAME"
        for keyword, spelled, setop_all in (
            ("UNION", "UNION", False),
            ("UNION ALL", "UNION", True),
            ("EXCEPT", "EXCEPT", False),
            ("INTERSECT", "INTERSECT", False),
        ):
            node = _ast(
                con, f"SELECT count(*) AS n FROM meta.a {keyword} SELECT count(*) AS n FROM meta.b"
            )
            assert node["type"] == "SET_OPERATION_NODE" and node["setop_type"] == spelled
            assert node["setop_all"] is setop_all and spelled in safe._SET_OPERATION_TYPES
            assert node["left"]["type"] == node["right"]["type"] == "SELECT_NODE"
        plain = _ast(con, "SELECT count(*) AS n FROM meta.a")
        assert plain["type"] == "SELECT_NODE"
        assert plain["from_table"]["type"] == "BASE_TABLE"
        assert plain["from_table"]["schema_name"] == "meta"
        tables = _ast(con, "SHOW TABLES")["from_table"]
        all_tables = _ast(con, "SHOW ALL TABLES")["from_table"]
        assert tables["type"] == all_tables["type"] == "SHOW_REF"
        assert tables["show_type"] == all_tables["show_type"] == "SHOW_UNQUALIFIED"
        assert {tables["table_name"], all_tables["table_name"]} == set(safe._SHOW_TABLE_NAMES)
        describe = _ast(con, "DESCRIBE meta.a")["from_table"]
        assert describe["type"] == "SHOW_REF" and describe["show_type"] == "DESCRIBE"
        assert describe["query"]["from_table"]["type"] == "BASE_TABLE"
        assert describe["query"]["from_table"]["schema_name"] == "meta"
        cast = _ast(con, "SELECT CAST(count(*) AS BIGINT) AS n FROM meta.a")["select_list"][0]
        assert cast["class"] == "CAST" and cast["type"] == "OPERATOR_CAST"
        assert (
            cast["child"]["class"] == "FUNCTION" and cast["child"]["function_name"] == "count_star"
        )
        group_all = _ast(con, "SELECT g, count(*) AS n FROM meta.a GROUP BY ALL")
        assert group_all["aggregate_handling"] == "FORCE_AGGREGATES"
        cte = _ast(con, "WITH c AS (SELECT 1 AS x) SELECT count(*) AS n FROM c")
        assert [entry["key"] for entry in cte["cte_map"]["map"]] == ["c"]
        # SGT-5 relies on table functions serializing as FUNCTION nodes the tree pass meets
        fn = _ast(con, "SELECT count(*) AS n FROM duckdb_databases()")["from_table"]
        assert fn["type"] == "TABLE_FUNCTION" and fn["function"]["class"] == "FUNCTION"
        assert fn["function"]["function_name"] == "duckdb_databases"
    finally:
        con.close()


def test_dkb7_partition_copy_overwrite_or_ignore_semantics(
    fixture_lake_settings: Settings, tmp_path: Path
) -> None:
    without = buckets_mod._partition_copy_options("raw_{i}", ignore_existing=False)
    with_flag = buckets_mod._partition_copy_options("raw_{i}", ignore_existing=True)
    assert "OVERWRITE_OR_IGNORE" in with_flag and "OVERWRITE_OR_IGNORE" not in without
    assert "APPEND" not in with_flag and f"PARTITION_BY ({BUCKET_COLUMN})" in with_flag
    root = tmp_path / "t"
    con = open_duckdb("app", settings=fixture_lake_settings)
    try:
        con.execute(
            f"CREATE TABLE t AS SELECT range AS k, range % 100 AS {BUCKET_COLUMN} FROM range(200)"
        )

        def copy(buckets: str, options: str) -> None:
            con.execute(
                f"COPY (SELECT * FROM t WHERE {BUCKET_COLUMN} IN ({buckets})) "
                f"TO {_sql_str(str(root))} ({options})"
            )

        copy("0, 1", without)
        first = sorted(root.rglob("*.parquet"))
        assert {p.parent.name for p in first} == {f"{BUCKET_COLUMN}=0", f"{BUCKET_COLUMN}=1"}
        assert {p.name for p in first} == {"raw_0.parquet"}
        before = {p: p.read_bytes() for p in first}
        # a non-empty root without the flag is refused: the sweeps rely on the flag lifting
        # exactly that check
        with pytest.raises(duckdb.Error, match=r"(?i)not empty"):
            copy("2", without)
        assert sorted(root.rglob("*.parquet")) == first
        # with it, disjoint buckets land beside the earlier ones and those stay untouched
        copy("2, 3", with_flag)
        after = sorted(root.rglob("*.parquet"))
        assert {p.parent.name for p in after} == {f"{BUCKET_COLUMN}={b}" for b in range(4)}
        assert all(p.read_bytes() == blob for p, blob in before.items())
        glob = _sql_str((root / "**" / "*.parquet").as_posix())
        total = con.execute(
            f"SELECT count(*) FROM read_parquet({glob}, hive_partitioning = true)"
        ).fetchone()
        assert total == (8,)  # 200 rows over 100 buckets: 2 per bucket, 4 buckets written
    finally:
        con.close()


def test_tst5_staged_table_count_is_derived_from_the_contract() -> None:
    contract = load_contract()
    count = helpers.STAGED_TABLE_COUNT
    assert helpers.staged_table_count() == count
    assert sum(len(contract.by_schema(s)) for s in STAGED_SCHEMAS) == count
    assert len(contract.by_dataset("mimic-iv-3.1")) == count
    assert len([t for t in contract.tables if t.schema_name in STAGED_SCHEMAS]) == count
    # churn guard: the literal stays out of the modules EP-173 moved onto the constant
    ep_dir = helpers.WORKSPACE / "tests" / "ep"
    for stem in (
        "test_ep08",
        "test_ep09",
        "test_ep10",
        "test_ep20",
        "test_ep21",
        "test_ep27",
        "test_ep28",
        "test_ep29",
        "test_ep34",
        "test_ep44",
    ):
        text = (ep_dir / f"{stem}.py").read_text(encoding="utf-8")
        assert "helpers.STAGED_TABLE_COUNT" in text, stem
        assert not re.search(r"==\s*31\b", text), f"{stem}: the staged-table literal is back"
        assert '"31 table' not in text, stem


def test_tst7_hook_budget_is_measured_in_process() -> None:
    text = (helpers.WORKSPACE / "tests" / "ep" / "test_ep165.py").read_text(encoding="utf-8")
    assert "HOOK_DECIDE_BUDGET_S = 0.2" in text and "HOOK_SUBPROCESS_CEILING_S = 3.0" in text
    assert "def test_hook_decide_within_budget_in_process" in text
    assert "elapsed <= 0.2" not in text, (
        "the cold-interpreter subprocess no longer carries the 200 ms budget"
    )
