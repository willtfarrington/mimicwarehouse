"""EP-30 — safe-query wrapper + audit log.

Governance acceptance: :func:`mimicwarehouse.safe.safe_query` **refuses** every crafted
violation (non-SELECT statements, multi-statement input, file/env table functions,
identifier columns as output / aliased / inside MIN-MAX, row-level selects, contract
free-text columns, notes schemas, row-cap overruns, timeouts, ``k < 11`` on dev) and
each refusal is audited to the append-only ``runs/audit.jsonl`` with ``allowed = false``
and the reason. Allowed cases return k-suppressed aggregates only; the suppression hook
is replaceable (EP-43); ``warehouse/runs.duckdb`` exposes the ``audit`` view whose row
count equals the JSONL line count; the final ``mwh sql`` prints the footer and exits 3
on refusal. ``tier("dev")``-marked: one allowed aggregate on the real dev catalog via
``mwh sql`` appends exactly one audit line.

Everything asserted or printed is counts, schemas, refusal reasons and metadata — the
data queried is the committed synthetic fixture (ids >= 90 000 000) or, in the one
dev-tier test, k-suppressed aggregates only.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

import helpers
from mimicwarehouse import config, safe
from mimicwarehouse.catalog.cli import EXIT_REFUSED
from mimicwarehouse.cli import app
from mimicwarehouse.safe import (
    SafeQueryError,
    SafeQueryRefused,
    audit_path,
    build_runs_db,
    runs_db_path,
    safe_query,
)

if TYPE_CHECKING:
    from mimicwarehouse.config import Settings

pytestmark = pytest.mark.ep_30

HOSP = "mimiciv_hosp"

#: A crafted small group on the committed fixture: exactly one synthetic patient holds
#: the minimum anchor_age, so k = 11 suppresses exactly that row (deterministic — the
#: fixture CSVs are committed).
SMALL_GROUP_SQL = (
    "SELECT CASE WHEN anchor_age = (SELECT min(anchor_age) FROM mimiciv_hosp.patients) "
    "THEN 'index' ELSE 'rest' END AS age_bucket, count(*) AS n "
    "FROM mimiciv_hosp.patients GROUP BY 1"
)


def _audit_lines(settings: Settings) -> list[dict[str, Any]]:
    path = audit_path(settings)
    if not path.is_file():
        return []
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def _assert_refused(settings: Settings, sql: str, reason_fragment: str, **kwargs: Any) -> None:
    with pytest.raises(SafeQueryRefused) as excinfo:
        safe_query(sql, tier="fixture", settings=settings, **kwargs)
    assert reason_fragment in str(excinfo.value)
    last = _audit_lines(settings)[-1]
    assert last["allowed"] is False
    assert reason_fragment in last["refusal_reason"]
    assert last["statement_sha256"] == hashlib.sha256(sql.encode("utf-8")).hexdigest()
    assert last["actor"] == "agent"  # MWH_ROLE unset in sessions (CLAUDE.md section 2)


# ---------------------------------------------------------------------------
# 1. Crafted violations are refused AND audited (the governance acceptance)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("sql", "reason_fragment"),
    [
        ("COPY (SELECT 1) TO 'x.csv'", "statement type refused"),
        ("ATTACH 'x.duckdb'", "statement type refused"),
        ("INSTALL httpfs", "statement type refused"),
        ("PRAGMA database_list", "statement type refused"),
        ("SET threads = 1", "statement type refused"),
        ("SELECT * FROM read_csv('x.csv')", "read_csv"),
        ("SELECT glob('*')", "glob"),
        ("SELECT 1; SELECT 2", "multi-statement"),
        (
            f"SELECT subject_id, count(*) AS n FROM {HOSP}.admissions GROUP BY 1",
            "identifier column 'subject_id'",
        ),
        (
            f"SELECT subject_id AS s, count(*) AS n FROM {HOSP}.admissions GROUP BY 1",
            "identifier column 'subject_id'",
        ),
        (
            f"SELECT max(subject_id) AS m, count(*) AS n FROM {HOSP}.admissions",
            "identifier column 'subject_id'",
        ),
        (f"SELECT gender FROM {HOSP}.patients LIMIT 5", "GROUP BY key"),
        (f"SELECT avg(anchor_age) AS mean_age FROM {HOSP}.patients", "count-family"),
        (
            f"SELECT comments, count(*) AS n FROM {HOSP}.labevents GROUP BY 1",
            "free-text",
        ),
        ("SELECT count(*) AS n FROM mimiciv_note.discharge", "not allowed"),
        ("SELECT count(*) AS n FROM patients", "unqualified"),
        (
            f"SELECT count(*) n FROM {HOSP}.patients UNION ALL "
            f"SELECT count(*) FROM {HOSP}.admissions",
            "set operations",
        ),
    ],
)
def test_crafted_violation_refused_and_audited(
    fixture_lake_settings: Settings, sql: str, reason_fragment: str
) -> None:
    _assert_refused(fixture_lake_settings, sql, reason_fragment)


def test_row_cap_refused(fixture_lake_settings: Settings) -> None:
    # 342+ metadata rows > the default cap of 200 (information_schema is metadata-exempt
    # from the count-family rule, but never from the row cap)
    _assert_refused(
        fixture_lake_settings,
        "SELECT table_name, column_name FROM information_schema.columns",
        "row cap",
    )


def test_timeout_interrupts_and_audits(fixture_lake_settings: Settings) -> None:
    # the brief's range(10**9) spelled as a literal: `10**9` binds as DOUBLE in
    # DuckDB 1.5.x and would fail for the wrong reason
    _assert_refused(
        fixture_lake_settings,
        "SELECT count(*) AS n FROM range(1000000000)",
        "timeout",
        timeout_s=0.001,
    )


def test_k_below_11_refused_on_dev(fixture_lake_settings: Settings) -> None:
    sql = f"SELECT count(*) AS n FROM {HOSP}.patients"
    with pytest.raises(SafeQueryRefused) as excinfo:
        safe_query(sql, tier="dev", k=5, settings=fixture_lake_settings)
    assert "k = 5 < 11" in str(excinfo.value)
    last = _audit_lines(fixture_lake_settings)[-1]
    assert last["allowed"] is False and last["tier"] == "dev" and last["k"] == 5


def test_unknown_tier_is_an_error_not_a_refusal(fixture_lake_settings: Settings) -> None:
    with pytest.raises(SafeQueryError):
        safe_query("SELECT 1", tier="bogus", settings=fixture_lake_settings)


# ---------------------------------------------------------------------------
# 2. Allowed cases: suppressed aggregates, metadata forms, audit lines
# ---------------------------------------------------------------------------


def test_small_group_suppressed_rowwise(fixture_lake_settings: Settings) -> None:
    result = safe_query(SMALL_GROUP_SQL, tier="fixture", settings=fixture_lake_settings)
    assert result.rows_suppressed == 1
    assert result.n_rows == 1
    assert result.df["age_bucket"].to_list() == ["rest"]
    assert int(result.df["n"][0]) == 119  # 120 fixture subjects minus the crafted cell
    assert result.k == 11
    last = _audit_lines(fixture_lake_settings)[-1]
    assert last["allowed"] is True
    assert last["rows_suppressed"] == 1 and last["n_rows"] == 1
    assert last["snapshot_ids"].get("core"), "audit carries the {layer: id} dict (ARCH-6)"
    assert result.snapshot_id == last["snapshot_ids"]["core"]


def test_count_distinct_identifier_allowed(fixture_lake_settings: Settings) -> None:
    result = safe_query(
        f"SELECT count(DISTINCT subject_id) AS n_subjects FROM {HOSP}.admissions",
        tier="fixture",
        settings=fixture_lake_settings,
    )
    assert result.n_rows == 1 and result.rows_suppressed == 0
    assert int(result.df["n_subjects"][0]) == 120


def test_identifier_in_join_predicate_allowed(fixture_lake_settings: Settings) -> None:
    result = safe_query(
        f"SELECT count(*) AS n FROM {HOSP}.admissions a "
        f"JOIN {HOSP}.patients p ON a.subject_id = p.subject_id",
        tier="fixture",
        settings=fixture_lake_settings,
    )
    assert result.n_rows == 1  # identifiers may key joins/filters, never output


def test_information_schema_count_allowed(fixture_lake_settings: Settings) -> None:
    result = safe_query(
        "SELECT count(*) AS n FROM information_schema.tables",
        tier="fixture",
        settings=fixture_lake_settings,
    )
    assert result.n_rows == 1 and int(result.df["n"][0]) > 0


def test_describe_allowed(fixture_lake_settings: Settings) -> None:
    result = safe_query(f"DESCRIBE {HOSP}.patients", tier="fixture", settings=fixture_lake_settings)
    assert "column_name" in result.df.columns
    assert "subject_id" in result.df["column_name"].to_list()  # schema metadata, not data
    assert result.rows_suppressed == 0


def test_suppressor_hook_is_replaceable(
    fixture_lake_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[int, list[str]]] = []

    def stub(df: Any, k: int, count_columns: list[str]) -> tuple[Any, int]:
        calls.append((k, count_columns))
        return df, 42

    monkeypatch.setattr(safe, "SUPPRESSOR", stub)
    result = safe_query(
        f"SELECT count(*) AS n FROM {HOSP}.patients",
        tier="fixture",
        settings=fixture_lake_settings,
    )
    assert result.rows_suppressed == 42, "EP-43 replaces SUPPRESSOR; the hook must be live"
    assert calls and calls[0][0] == 11 and "n" in calls[0][1]


# ---------------------------------------------------------------------------
# 3. runs.duckdb: audit view row count equals the JSONL line count
# ---------------------------------------------------------------------------


def test_runs_db_audit_view_matches_lines(fixture_lake_settings: Settings) -> None:
    import duckdb

    safe_query(
        "SELECT count(*) AS n FROM information_schema.tables",
        tier="fixture",
        settings=fixture_lake_settings,
    )
    dest = build_runs_db(fixture_lake_settings)
    assert dest == runs_db_path(fixture_lake_settings) and dest.is_file()
    assert not dest.with_name(dest.name + ".new").exists()
    expected = len(_audit_lines(fixture_lake_settings))
    con = duckdb.connect(str(dest), read_only=True)
    try:
        row = con.execute("SELECT count(*) FROM audit").fetchone()
    finally:
        con.close()
    assert row is not None and int(row[0]) == expected


def test_runs_refresh_cli(fixture_lake_settings: Settings) -> None:
    runner = helpers.cli_runner()
    try:
        result = runner.invoke(
            app, ["--data-root", str(fixture_lake_settings.data_root), "runs", "refresh"]
        )
        assert result.exit_code == 0, result.output
        assert "refreshed" in result.output and "audit" in result.output
    finally:
        config.configure()


# ---------------------------------------------------------------------------
# 4. mwh sql final body: footer, formats, exit 3 on refusal
# ---------------------------------------------------------------------------


def test_sql_cli_free_form_footer_and_fmt_int(fixture_lake_settings: Settings) -> None:
    runner = helpers.cli_runner()
    root = ["--data-root", str(fixture_lake_settings.data_root)]
    try:
        before = len(_audit_lines(fixture_lake_settings))
        ok = runner.invoke(
            app,
            [
                *root,
                "sql",
                "--tier",
                "fixture",
                f"SELECT count(*) AS n FROM {HOSP}.labevents",
            ],
        )
        assert ok.exit_code == 0, ok.output
        assert "9,619" in ok.output, "table output thousands-separates counts (FC-16)"
        assert "rows suppressed" in ok.output and "audit" in ok.output
        assert "snapshot" in ok.output
        assert len(_audit_lines(fixture_lake_settings)) == before + 1

        refused = runner.invoke(
            app, [*root, "sql", "--tier", "fixture", f"SELECT gender FROM {HOSP}.patients"]
        )
        assert refused.exit_code == EXIT_REFUSED, refused.output
        assert "refused" in refused.output
        last = _audit_lines(fixture_lake_settings)[-1]
        assert last["allowed"] is False
    finally:
        config.configure()


def test_sql_cli_json_keeps_raw_ints(fixture_lake_settings: Settings) -> None:
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
                f"SELECT count(*) AS n FROM {HOSP}.labevents",
            ],
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["rows"] == [{"n": 9619}]  # raw int (FC-16); never pasted into git
        assert payload["n_rows"] == 1 and payload["rows_suppressed"] == 0
        assert payload["tier"] == "fixture" and payload["audit_id"]
    finally:
        config.configure()


def test_sql_cli_csv_format(fixture_lake_settings: Settings) -> None:
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
                "csv",
                SMALL_GROUP_SQL,
            ],
        )
        assert result.exit_code == 0, result.output
        assert "age_bucket,n" in result.output
        assert "rest,119" in result.output
        assert "index" not in result.output, "the small cell stays suppressed in CSV too"
    finally:
        config.configure()


def test_sql_cli_row_cap_option(fixture_lake_settings: Settings) -> None:
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
                "--row-cap",
                "1",
                f"SELECT anchor_year_group, count(*) AS n FROM {HOSP}.patients GROUP BY 1",
            ],
        )
        assert result.exit_code == EXIT_REFUSED, result.output
        assert "row cap" in result.output
    finally:
        config.configure()


# ---------------------------------------------------------------------------
# 5. Real dev catalog: one allowed aggregate through mwh sql, one audit line
# ---------------------------------------------------------------------------


@pytest.mark.tier("dev")
def test_dev_aggregate_audited(dev_catalog: Path) -> None:
    settings = config.load_settings()
    before = len(_audit_lines(settings))
    runner = helpers.cli_runner()
    result = runner.invoke(
        app,
        [
            "sql",
            "--tier",
            "dev",
            "--format",
            "json",
            f"SELECT anchor_year_group, count(*) AS n FROM {HOSP}.patients GROUP BY 1",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["n_rows"] >= 1
    assert all(row["n"] >= 11 or row["n"] == 0 for row in payload["rows"]), (
        "no small cell may survive suppression on a credentialed tier"
    )
    lines = _audit_lines(settings)
    assert len(lines) == before + 1
    assert audit_path(settings).is_file()
    last = lines[-1]
    assert last["allowed"] is True and last["tier"] == "dev" and last["k"] == 11
