"""EP-33 Workstream B1 — safe-query hardening (the orchestrator merges this module into
``test_ep33.py``).

Cast-around-aggregate (B1a), the real-count requirement (DKB-1 / SGT-1 / SGT-4), set
operations with per-branch checks (B1b), registry exemptions + settings defaults (B1c),
the three-way error taxonomy (B1d), sanitized engine errors (DKB-2/DKB-3), the
fsio / engine / publish migrations of ``safe`` and ``tracer`` (B3, LGR-1, WIN-6) and the
tracer mirror constant (B4). Fixture tier only — the session's runner-built fixture lake
(synthetic ids >= 90 000 000) or ``tmp_path``; everything asserted is counts, dtypes,
refusal reasons and audit metadata.
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Any

import pytest

import helpers
from mimicwarehouse import config, fsio, publish, safe, tracer
from mimicwarehouse.catalog import cli as catalog_cli
from mimicwarehouse.cli import app
from mimicwarehouse.safe import (
    SafeQueryError,
    SafeQueryRefused,
    audit_path,
    build_runs_db,
    is_registry_ref,
    runs_db_path,
    safe_query,
    sanitize_error_text,
)

if TYPE_CHECKING:
    from pathlib import Path

    from mimicwarehouse.config import Settings

pytestmark = pytest.mark.ep_33

HOSP = "mimiciv_hosp"
ICU = "mimiciv_icu"
PATIENTS = f"SELECT count(*) AS n FROM {HOSP}.patients"

#: The committed fixture's subject count, from its manifest (churn rule, tests/README.md).
N_SUBJECTS = int(
    json.loads(
        (helpers.WORKSPACE / "tests" / "fixtures" / "manifest.json").read_text(encoding="utf-8")
    )["files"]["mimic-iv-3.1/hosp/patients.csv"]["rows"]
)

#: test_ep30's crafted small group (one synthetic patient holds the minimum anchor_age)
#: with the count cast-wrapped — k = 11 must still drop exactly that row.
SMALL_GROUP_CAST_SQL = (
    "SELECT CASE WHEN anchor_age = (SELECT min(anchor_age) FROM mimiciv_hosp.patients) "
    "THEN 'index' ELSE 'rest' END AS age_bucket, CAST(count(*) AS INTEGER) AS n "
    "FROM mimiciv_hosp.patients GROUP BY 1"
)


def _audit_lines(settings: Settings) -> list[dict[str, Any]]:
    return fsio.read_jsonl(audit_path(settings))


def _assert_refused(settings: Settings, sql: str, fragment: str, **kwargs: Any) -> SafeQueryRefused:
    with pytest.raises(SafeQueryRefused) as excinfo:
        safe_query(sql, tier="fixture", settings=settings, **kwargs)
    assert fragment in str(excinfo.value)
    last = _audit_lines(settings)[-1]
    assert last["allowed"] is False and fragment in last["refusal_reason"]
    return excinfo.value


# ---------------------------------------------------------------------------
# B1a — cast around a closed-set aggregate
# ---------------------------------------------------------------------------


def test_cast_around_sum_allowed_int64_and_audited(fixture_lake_settings: Settings) -> None:
    """The mandated sum() regression: CAST(sum(...) AS BIGINT) beside count(*)."""
    import polars as pl

    result = safe_query(
        f"SELECT anchor_year_group, CAST(sum(anchor_age) AS BIGINT) AS s, count(*) AS n "
        f"FROM {HOSP}.patients GROUP BY anchor_year_group",
        tier="fixture",
        settings=fixture_lake_settings,
    )
    assert result.df.schema["s"] == pl.Int64
    assert result.n_rows + result.rows_suppressed >= 1
    last = _audit_lines(fixture_lake_settings)[-1]
    assert last["allowed"] is True and last["audit_id"] == result.audit_id


@pytest.mark.parametrize(
    "expr",
    [
        "CAST(count(DISTINCT subject_id) AS INTEGER)",
        "count(DISTINCT subject_id)::INTEGER",
        "TRY_CAST(count(DISTINCT subject_id) AS INTEGER)",
    ],
)
def test_cast_wrapped_count_distinct_identifier_allowed(
    fixture_lake_settings: Settings, expr: str
) -> None:
    import polars as pl

    result = safe_query(
        f"SELECT {expr} AS n FROM {HOSP}.admissions", tier="fixture", settings=fixture_lake_settings
    )
    assert result.n_rows == 1 and result.df.schema["n"] == pl.Int32


@pytest.mark.parametrize(
    ("sql", "fragment"),
    [
        (
            f"SELECT CAST(subject_id AS BIGINT) AS x, count(*) AS n FROM {HOSP}.patients "
            "GROUP BY 1",
            "identifier column 'subject_id'",
        ),
        (
            f"SELECT CAST(anchor_age AS BIGINT) AS a, count(*) AS n FROM {HOSP}.patients",
            "neither an allowed aggregate",
        ),
        (
            f"SELECT CAST(count(*) / 2 AS DOUBLE) AS half, count(*) AS n FROM {HOSP}.patients",
            "neither an allowed aggregate",  # arithmetic under a cast: DIS-3 stays parked
        ),
        (f"SELECT CAST(count(*) AS INTEGER) FROM {HOSP}.patients", "alias the cast count column"),
    ],
)
def test_cast_closed_set_refused(fixture_lake_settings: Settings, sql: str, fragment: str) -> None:
    _assert_refused(fixture_lake_settings, sql, fragment)


def test_aliased_cast_count_still_k_suppressed(fixture_lake_settings: Settings) -> None:
    result = safe_query(SMALL_GROUP_CAST_SQL, tier="fixture", settings=fixture_lake_settings)
    assert result.rows_suppressed == 1
    assert result.df["age_bucket"].to_list() == ["rest"]


# ---------------------------------------------------------------------------
# DKB-1 / SGT-1 / SGT-4 — only a real count call satisfies the count requirement
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("sql", "fragment"),
    [
        (f"SELECT avg(anchor_age) AS n FROM {HOSP}.patients", "count-named alias 'n'"),
        (
            f"SELECT anchor_age AS n, count(*) AS cnt FROM {HOSP}.patients GROUP BY 1",
            "count-named alias 'n'",  # SGT-4: a numeric group key is never a count
        ),
        (f"SELECT dod AS n FROM {HOSP}.patients GROUP BY 1", "count-named alias 'n'"),
        (
            f"SELECT gender AS n_gender, avg(anchor_age) AS num_age FROM {HOSP}.patients "
            "GROUP BY 1",
            "count-named alias",  # only alias-named items: no real count anywhere
        ),
        (
            f"SELECT gender, avg(anchor_age) AS mean_age FROM {HOSP}.patients GROUP BY 1",
            "no count-family column",
        ),
    ],
)
def test_alias_never_satisfies_count_requirement(
    fixture_lake_settings: Settings, sql: str, fragment: str
) -> None:
    _assert_refused(fixture_lake_settings, sql, fragment)


def test_suppressor_sees_only_real_count_columns(
    fixture_lake_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[list[str]] = []

    def stub(df: Any, k: int, count_columns: list[str]) -> tuple[Any, int]:
        seen.append(count_columns)
        return df, 0

    monkeypatch.setattr(safe, "SUPPRESSOR", stub)
    safe_query(
        f"SELECT gender, count(*) AS n, avg(anchor_age) AS mean_age, count(DISTINCT anchor_age) "
        f"FROM {HOSP}.patients GROUP BY 1",
        tier="fixture",
        settings=fixture_lake_settings,
    )
    assert seen and set(seen[0]) == {"n", "count(DISTINCT anchor_age)"}


# ---------------------------------------------------------------------------
# B1b — set operations: per-branch checks, positional count rule, row-wise suppression
# ---------------------------------------------------------------------------


def test_union_all_allowed_audited_and_suppressed_rowwise(fixture_lake_settings: Settings) -> None:
    sql = (
        f"{SMALL_GROUP_CAST_SQL} UNION ALL SELECT 'all' AS age_bucket, count(*) AS n "
        f"FROM {HOSP}.patients"
    )
    result = safe_query(sql, tier="fixture", settings=fixture_lake_settings)
    assert result.rows_suppressed == 1  # the 'index' row of branch 1
    rows = dict(zip(result.df["age_bucket"].to_list(), result.df["n"].to_list(), strict=True))
    assert rows == {"rest": N_SUBJECTS - 1, "all": N_SUBJECTS}
    last = _audit_lines(fixture_lake_settings)[-1]
    assert last["allowed"] is True and last["rows_suppressed"] == 1 and last["n_rows"] == 2


@pytest.mark.parametrize(
    "sql",
    [
        f"{PATIENTS} EXCEPT SELECT count(*) AS n FROM {HOSP}.admissions",
        f"{PATIENTS} INTERSECT SELECT count(DISTINCT subject_id) AS n FROM {HOSP}.admissions",
        f"{PATIENTS} UNION SELECT count(*) AS n FROM {HOSP}.admissions UNION ALL {PATIENTS}",
        f"WITH p AS (SELECT * FROM {HOSP}.patients) SELECT count(*) AS n FROM p "
        f"UNION ALL SELECT count(*) FROM p",
    ],
)
def test_set_operation_variants_allowed(fixture_lake_settings: Settings, sql: str) -> None:
    result = safe_query(sql, tier="fixture", settings=fixture_lake_settings)
    assert result.n_rows >= 0 and "n" in result.df.columns
    assert _audit_lines(fixture_lake_settings)[-1]["allowed"] is True


@pytest.mark.parametrize(
    ("sql", "fragments"),
    [
        (
            f"{PATIENTS} UNION ALL SELECT anchor_age FROM {HOSP}.patients",
            ("set-operation branch 2", "neither an allowed aggregate"),
        ),
        (
            f"SELECT gender, count(*) AS n FROM {HOSP}.patients GROUP BY 1 UNION ALL "
            f"SELECT count(*) AS n, gender FROM {HOSP}.patients GROUP BY 2",
            ("set-operation branch 2, select-list column #1", "count-family positions must agree"),
        ),
        (
            f"{PATIENTS} UNION ALL SELECT count(*) AS n, count(*) AS m FROM {HOSP}.patients",
            ("set-operation branch 2 has 2 select-list column(s)",),
        ),
        (f"{PATIENTS} UNION ALL SELECT subject_id FROM {HOSP}.patients", ("identifier column",)),
        (f"{PATIENTS} UNION ALL VALUES (1)", ("set-operation branch 2",)),
        (f"{PATIENTS} UNION BY NAME {PATIENTS}", ("UNION BY NAME",)),
        (
            f"{PATIENTS} UNION ALL SELECT count(*) AS n FROM mimiciv_note.discharge",
            ("not allowed",),
        ),
    ],
)
def test_set_operation_refusals_name_the_branch(
    fixture_lake_settings: Settings, sql: str, fragments: tuple[str, ...]
) -> None:
    exc = _assert_refused(fixture_lake_settings, sql, fragments[0])
    for fragment in fragments[1:]:
        assert fragment in str(exc)


# ---------------------------------------------------------------------------
# B1c — registry exemptions, free-text scan rescoped, settings defaults
# ---------------------------------------------------------------------------


def test_is_registry_ref() -> None:
    assert sorted(safe.REGISTRY_SCHEMAS) == ["information_schema", "meta"]  # runs stays out
    assert is_registry_ref("meta", "catalog_info") and is_registry_ref("META", "anything")
    assert is_registry_ref("information_schema", "tables")
    assert is_registry_ref(ICU, "d_items"), "a contract dim"
    assert is_registry_ref("marts", "cohorts"), "REGISTRY_TABLES (EP-47 registry)"
    assert not is_registry_ref(ICU, "icustays")
    assert not is_registry_ref("runs", "audit")
    assert not is_registry_ref("marts", "anything_else")


def test_registry_only_select_needs_no_count(fixture_lake_settings: Settings) -> None:
    result = safe_query(
        "SELECT tier, build_id FROM meta.catalog_info",
        tier="fixture",
        settings=fixture_lake_settings,
    )
    assert result.n_rows == 1 and result.rows_suppressed == 0
    dims = safe_query(
        f"SELECT category FROM {ICU}.d_items GROUP BY 1",
        tier="fixture",
        settings=fixture_lake_settings,
    )
    assert dims.n_rows >= 1


def test_free_text_scan_covers_every_non_registry_read(fixture_lake_settings: Settings) -> None:
    """P3C-5: the 64-char value heuristic follows `not exempt`, not the contract's
    subject-keyed flag — dims stay exempt, everything else is scanned."""
    long_label = "repeat('x', 70) AS long_label"
    _assert_refused(
        fixture_lake_settings,
        f"SELECT {long_label}, count(*) AS n FROM {HOSP}.patients GROUP BY 1",
        "longer than 64",
    )
    exempt = safe_query(
        f"SELECT {long_label}, count(*) AS n FROM {ICU}.d_items GROUP BY 1",
        tier="fixture",
        settings=fixture_lake_settings,
    )
    assert exempt.n_rows == 1


def test_defaults_resolve_settings_tier_and_k(fixture_lake_settings: Settings) -> None:
    settings = config.Settings(
        data_root=fixture_lake_settings.data_root, default_tier="fixture", k_suppression=12
    )
    result = safe_query(PATIENTS, settings=settings)
    assert result.tier == "fixture" and result.k == 12
    last = _audit_lines(settings)[-1]
    assert last["tier"] == "fixture" and last["k"] == 12 and last["allowed"] is True


# ---------------------------------------------------------------------------
# B1d — the three-way error taxonomy
# ---------------------------------------------------------------------------


def test_exit_codes_are_reexported() -> None:
    assert safe.EXIT_REFUSED == catalog_cli.EXIT_REFUSED == 3
    assert safe.EXIT_USAGE == catalog_cli.EXIT_USAGE == 2


@pytest.mark.parametrize(
    ("kwargs", "fragment"),
    [
        ({"k": 0}, "k = 0"),
        ({"row_cap": 0}, "row_cap = 0"),
        ({"tier": "bogus"}, "unknown tier 'bogus'"),
    ],
)
def test_usage_errors_raise_and_audit(
    fixture_lake_settings: Settings, kwargs: dict[str, Any], fragment: str
) -> None:
    with pytest.raises(SafeQueryError) as excinfo:
        safe_query(PATIENTS, settings=fixture_lake_settings, **{"tier": "fixture", **kwargs})
    assert fragment in str(excinfo.value)
    last = _audit_lines(fixture_lake_settings)[-1]
    assert last["allowed"] is False
    assert last["refusal_reason"].startswith("usage: ") and fragment in last["refusal_reason"]


def test_k_below_floor_on_dev_stays_a_refusal(fixture_lake_settings: Settings) -> None:
    with pytest.raises(SafeQueryRefused, match="k = 5 < 11"):
        safe_query(PATIENTS, tier="dev", k=5, settings=fixture_lake_settings)
    assert not _audit_lines(fixture_lake_settings)[-1]["refusal_reason"].startswith("usage")


def test_sql_cli_exit_codes_and_stderr(fixture_lake_settings: Settings) -> None:
    runner = helpers.cli_runner()
    root = ["--data-root", str(fixture_lake_settings.data_root)]
    try:
        usage = runner.invoke(app, [*root, "sql", "--tier", "fixture", "--k", "0", PATIENTS])
        assert usage.exit_code == catalog_cli.EXIT_USAGE, usage.output
        assert "k = 0" in usage.stderr and usage.stdout == ""

        bogus = runner.invoke(app, [*root, "sql", "--tier", "bogus", PATIENTS])
        assert bogus.exit_code == catalog_cli.EXIT_USAGE and "unknown tier" in bogus.stderr

        refused = runner.invoke(
            app, [*root, "sql", "--tier", "fixture", f"SELECT gender FROM {HOSP}.patients"]
        )
        assert refused.exit_code == catalog_cli.EXIT_REFUSED, refused.output
        assert "mwh sql: refused:" in refused.stderr and refused.stdout == ""

        no_catalog = runner.invoke(app, [*root, "sql", "--tier", "demo", PATIENTS])  # never built
        assert no_catalog.exit_code == catalog_cli.EXIT_USAGE, no_catalog.output
        assert "no demo catalog" in no_catalog.stderr

        # tier/k pass through: the resolved values come back in the JSON payload
        ok = runner.invoke(
            app,
            [*root, "sql", "--tier", "fixture", "--format", "json", "--count", f"{HOSP}.patients"],
        )
        assert ok.exit_code == 0, ok.output
        assert '"tier": "fixture"' in ok.stdout and '"k": 11' in ok.stdout
    finally:
        config.configure()


def test_tracer_cli_guard_paths(
    fixture_lake_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mimicwarehouse.catalog.connect import CatalogOpenError

    runner = helpers.cli_runner()
    root = ["--data-root", str(fixture_lake_settings.data_root)]
    cases: list[tuple[Exception, int]] = [
        (SafeQueryError("k = 0 is invalid"), catalog_cli.EXIT_USAGE),
        (tracer.TracerError("cannot allocate a run folder"), catalog_cli.EXIT_USAGE),
        (CatalogOpenError("no fixture catalog at nowhere"), catalog_cli.EXIT_USAGE),
        (SafeQueryRefused("no count-family column"), catalog_cli.EXIT_REFUSED),
    ]
    try:
        for exc, code in cases:

            def boom(*args: Any, _exc: Exception = exc, **kwargs: Any) -> Any:
                raise _exc

            monkeypatch.setattr(tracer, "run_tracer", boom)
            result = runner.invoke(app, [*root, "tracer", "--tier", "fixture"])
            assert result.exit_code == code, (exc, result.output)
            assert str(exc) in result.stderr and result.stdout == ""
        bad_tier = runner.invoke(app, [*root, "tracer", "--tier", "bogus"])
        assert bad_tier.exit_code == catalog_cli.EXIT_USAGE and "unknown tier" in bad_tier.stderr
    finally:
        config.configure()


# ---------------------------------------------------------------------------
# DKB-2 / DKB-3 — engine errors never surface raw text; pre-execution errors are audited
# ---------------------------------------------------------------------------


def test_sanitize_error_text() -> None:
    assert (
        sanitize_error_text("Conversion Error: Could not convert string 'F' to INT32\nLINE 1: ...")
        == "Conversion Error: Could not convert string '...' to INT32"
    )
    assert sanitize_error_text('Binder Error: column "its" not found') == (
        "Binder Error: column '...' not found"
    )
    assert (
        sanitize_error_text("value 12345 out of range for INT8") == "value # out of range for INT8"
    )
    long = sanitize_error_text("x" * 300)
    assert len(long) == safe.ERROR_TEXT_MAX_CHARS and long.endswith("...")


def test_execution_error_is_sanitized_in_message_and_audit(fixture_lake_settings: Settings) -> None:
    sql = f"{PATIENTS} WHERE CAST(gender AS INTEGER) = 1"  # a failing cast over a VARCHAR
    exc = _assert_refused(fixture_lake_settings, sql, "execution error")
    reason = _audit_lines(fixture_lake_settings)[-1]["refusal_reason"]
    for text in (str(exc), reason):
        assert "'...'" in text, text
        assert "'F'" not in text and "'M'" not in text and "\n" not in text
        assert len(text) < 200


def test_pre_execution_catalog_error_is_refused_and_audited(
    fixture_lake_settings: Settings, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from mimicwarehouse import engine

    garbage = tmp_path / "runs.duckdb"
    garbage.write_bytes(b"not a database")
    monkeypatch.setattr(safe, "runs_db_path", lambda settings=None: garbage)

    def attach_garbage(con: Any, path: Any, alias: str) -> None:
        # a real ATTACH of the garbage file; not IF NOT EXISTS, because an earlier test's
        # `runs` attach survives on the path-keyed cached instance (DESIGN section 6)
        con.execute(f"ATTACH '{garbage.as_posix()}' AS runs_probe (READ_ONLY)")

    monkeypatch.setattr(engine, "attach_read_only", attach_garbage)
    exc = _assert_refused(fixture_lake_settings, PATIENTS, "catalog error before execution")
    assert "'...'" in str(exc) and str(garbage) not in str(exc)  # the quoted path is masked


# ---------------------------------------------------------------------------
# B3 / LGR-1 / WIN-6 — build_runs_db over the canon (torn line tolerated, swap_file hint)
# ---------------------------------------------------------------------------


def test_build_runs_db_tolerates_torn_line_and_names_the_remedy(tmp_path: Path) -> None:
    from mimicwarehouse.engine import open_duckdb

    settings = config.Settings(data_root=tmp_path / "root")
    ledger = audit_path(settings)
    fsio.append_jsonl_lines(
        ledger, [{"audit_id": "a", "allowed": True}, {"audit_id": "b", "allowed": False}]
    )
    with ledger.open("ab") as f:
        f.write(b'{"audit_id": "c", "allo')  # torn trailing line
    dest = build_runs_db(settings)
    assert dest == runs_db_path(settings) and not publish.new_path_for(dest).exists()
    con = open_duckdb("app", database=dest, read_only=True, settings=settings)
    try:
        assert con.execute("SELECT count(*) FROM audit").fetchone() == (2,)
    finally:
        con.close()
    if os.name != "nt":
        pytest.skip("the non-sharing handle semantics are Windows-only")
    with dest.open("rb"), pytest.raises(publish.SwapBlockedError, match="mwh runs refresh"):
        build_runs_db(settings)


# ---------------------------------------------------------------------------
# B4 — the tracer's mirror constant
# ---------------------------------------------------------------------------


def test_tracer_value_max_chars_mirrors_safe() -> None:
    assert tracer.VALUE_MAX_CHARS == safe.FREE_TEXT_MAX_CHARS
