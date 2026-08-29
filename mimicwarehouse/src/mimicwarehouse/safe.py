"""Safe-query wrapper + audit log — the governance choke point (EP-30; DESIGN §12,
GOVERNANCE §4/§5/§8, D-31/D-32/D-33/D-39).

From this module on, *every* result a Claude session or an export can see comes through
:func:`safe_query` (or the ``mwh sql`` CLI built on it). The pipeline, in order:

1. **Parse** (DuckDB ``json_serialize_sql``): exactly one statement, of type SELECT
   (CTEs allowed) or one of the metadata forms ``DESCRIBE <schema.table>`` /
   ``SHOW TABLES`` / ``SHOW ALL TABLES``. Everything else (COPY, ATTACH, INSTALL, LOAD,
   PRAGMA, SET, CREATE, INSERT, UPDATE, DELETE, EXPORT, IMPORT, CALL, BEGIN,
   multi-statement, set operations) is refused.
2. **Allow-list**: no table/scalar functions that touch files or the environment
   (:data:`FORBIDDEN_FUNCTION_NAMES` / :data:`FORBIDDEN_FUNCTION_PREFIXES`; ``query`` /
   ``query_table`` are included — they take SQL/table-name strings and would bypass this
   static walk); every qualified table must live in :data:`ALLOWED_SCHEMAS` (notes
   schemas never exist in these catalogs); unqualified names must be CTEs.
3. **Aggregate-only** (outermost select list): every result column must be an aggregate
   call (:data:`AGGREGATE_FUNCTIONS` — deliberately no value-collecting aggregates such
   as ``string_agg`` / ``list`` / ``histogram`` / ``arg_min``), a GROUP BY key, or a
   constant; identifier columns (contract flags, GOVERNANCE §4) may appear **only**
   inside count-family calls (:data:`COUNT_FAMILY_FUNCTIONS`), never as output, inside
   arithmetic, or in MIN/MAX/string aggregates; at least one count-family column
   (``count(*)`` / ``count(x)`` / ``count(DISTINCT x)`` or an alias matching
   :data:`COUNT_ALIAS_RE`) is required — unless the statement reads only ``meta.*``,
   dictionary/dim tables (``Table.is_dim``), ``information_schema`` or metadata
   functions (``duckdb_tables()`` / ``duckdb_columns()``), which GOVERNANCE §4
   explicitly allows (EP-170 amendment 1).
4. **Execute** on :func:`~mimicwarehouse.catalog.connect.open_catalog` (READ_ONLY, app
   profile, hardened) with ``warehouse/runs.duckdb`` ATTACHed read-only as ``runs`` when
   it exists, under a ``threading.Timer`` that calls ``con.interrupt()`` at ``timeout_s``.
5. **Result checks**: refuse output columns named like identifiers or like contract
   ``free_text`` columns (refused by name, ARCH-10/FC-18); the free-text value heuristic
   (any VARCHAR value longer than :data:`FREE_TEXT_MAX_CHARS` characters or containing a
   newline) is **scoped to statements that read a subject-keyed table** — dims and
   ``meta.*`` are exempt, and the label columns :data:`LABEL_COLUMN_NAMES`
   (``drgcodes.description``, ``hcpcsevents.short_description``) are allow-listed
   (EP-170 amendment 1); the post-suppression row count must not exceed ``row_cap``.
6. **k-suppression** via :data:`SUPPRESSOR` (see below); on ``dev``/``full`` a ``k``
   below 11 is refused (D-31/D-33); on ``fixture``/``demo`` (synthetic / ODbL) the
   caller may lower it.

Refusals raise :class:`SafeQueryRefused` **after** auditing. Every call — allowed or
refused — appends one :class:`AuditLine` to the append-only ``runs/audit.jsonl``
(``O_APPEND`` + flush + fsync, one canonical JSON object per line; D-24, GOVERNANCE §8):
never result values, only the statement text/hash, counts and provenance
(``snapshot_ids`` is a ``{layer: id}`` dict per the DESIGN §11 glossary — here
``{"core": <core_snapshot_id>}`` of the queried catalog). :func:`build_runs_db` exposes
the file as the ``audit`` view of ``warehouse/runs.duckdb`` (rename-aside swap, the
DESIGN §6 scheme; ``mwh runs refresh`` calls it, EP-35 adds the ledger views).

**Suppression hook contract**: :data:`SUPPRESSOR` is a module-level
``Callable[[polars.DataFrame, int, list[str]], tuple[polars.DataFrame, int]]`` —
``(df, k, count_columns) -> (suppressed_df, rows_suppressed)``. The default
(:func:`rowwise_suppress`) drops every row in which any count-family column has a value
in ``1 .. k-1``. EP-43 replaces it with ``disclose.suppress`` (complementary
suppression) by assigning the module attribute; callers never bypass it.

Owner row viewing is **not** here — that is the app's audited ``owner_rows()`` path
(EP-58); this module behaves the same for every role.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict

from mimicwarehouse.config import Settings, Tier, get_settings

if TYPE_CHECKING:  # pragma: no cover
    import polars

AUDIT_FILENAME = "audit.jsonl"
RUNS_DB_FILENAME = "runs.duckdb"

#: Schemas a statement may reference (GOVERNANCE §4; notes schemas never exist in these
#: catalogs, so ``mimiciv_note`` / ``mimiciv_ed`` are refused before they could bind).
ALLOWED_SCHEMAS: frozenset[str] = frozenset(
    {
        "mimiciv_hosp",
        "mimiciv_icu",
        "mimiciv_derived",
        "meta",
        "marts",
        "runs",
        "information_schema",
    }
)

#: Functions refused wherever they appear (file / environment / SQL-indirection access).
FORBIDDEN_FUNCTION_NAMES: frozenset[str] = frozenset(
    {
        "glob",
        "getenv",
        "current_setting",
        "duckdb_settings",
        "query",
        "query_table",
        "checkpoint",
        "force_checkpoint",
    }
)
#: Prefix families covering the file readers (``read_csv*``, ``read_parquet``,
#: ``parquet_scan`` & friends, ``read_json*``, ``read_text``, ``read_blob``,
#: ``sniff_csv``, …).
FORBIDDEN_FUNCTION_PREFIXES: tuple[str, ...] = ("read_", "parquet_", "scan_", "sniff_")

#: Count-family calls — the only place identifier columns may appear (GOVERNANCE §4).
COUNT_FAMILY_FUNCTIONS: frozenset[str] = frozenset({"count", "count_star", "approx_count_distinct"})

#: Aggregates allowed in the outermost select list. Deliberately excludes every
#: value-collecting aggregate (``string_agg``, ``list``, ``array_agg``, ``histogram``,
#: ``first``, ``last``, ``any_value``, ``arbitrary``, ``arg_min``, ``arg_max``): those
#: return raw values, not statistics.
AGGREGATE_FUNCTIONS: frozenset[str] = COUNT_FAMILY_FUNCTIONS | frozenset(
    {
        "sum",
        "avg",
        "mean",
        "min",
        "max",
        "median",
        "mode",
        "quantile",
        "quantile_cont",
        "quantile_disc",
        "approx_quantile",
        "stddev",
        "stddev_pop",
        "stddev_samp",
        "var_pop",
        "var_samp",
        "variance",
        "corr",
        "covar_pop",
        "covar_samp",
        "skewness",
        "kurtosis",
        "entropy",
        "bool_and",
        "bool_or",
    }
)

#: Aliases recognised as count-family columns (brief EP-30 item 1c).
COUNT_ALIAS_RE = re.compile(r"^(n|n_.*|.*_n|count|.*_count|cnt|.*_cnt|num_.*)$")

#: Result-column *name* prefixes that mark unaliased count outputs (``count_star()``,
#: ``count(DISTINCT …)``, ``approx_count_distinct(…)``) for suppression.
COUNT_NAME_PREFIXES: tuple[str, ...] = ("count", "approx_count_distinct")

#: The free-text value heuristic (result check 5).
FREE_TEXT_MAX_CHARS = 64
#: Label columns exempt from the value heuristic (EP-170 amendment 1: legitimate dim-like
#: labels on subject-keyed tables — ``drgcodes.description``,
#: ``hcpcsevents.short_description``).
LABEL_COLUMN_NAMES: frozenset[str] = frozenset({"description", "short_description"})

#: The k floor on credentialed tiers (D-31/D-33).
K_FLOOR = 11
CREDENTIALED_TIERS: frozenset[str] = frozenset({"dev", "full"})

#: SHOW_REF table names DuckDB desugars ``SHOW TABLES`` / ``SHOW ALL TABLES`` into.
_SHOW_TABLE_NAMES: frozenset[str] = frozenset({'"tables"', "__show_tables_expanded"})

_JUNK_AST_KEYS = ("alias", "query_location")


class SafeQueryRefused(RuntimeError):
    """The statement (or its result) violates the safe-query rules; already audited."""


class SafeQueryError(RuntimeError):
    """A non-governance failure inside safe_query (bad arguments)."""


@dataclass(slots=True)
class SafeResult:
    """What one allowed :func:`safe_query` call returned (suppressed aggregate only)."""

    df: polars.DataFrame
    n_rows: int
    rows_suppressed: int
    statement_sha256: str
    audit_id: str
    tier: str
    k: int
    duckdb_version: str
    snapshot_id: str | None


class AuditLine(BaseModel):
    """One ``runs/audit.jsonl`` line — statement, verdict and provenance; never values."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    audit_id: str
    ts: str
    actor: str
    tier: str
    statement_sha256: str
    sql_text: str
    allowed: bool
    refusal_reason: str | None = None
    n_rows: int | None = None
    rows_suppressed: int | None = None
    k: int
    wall_ms: float
    duckdb_version: str
    snapshot_ids: dict[str, str]
    git_sha: str | None = None


# ---------------------------------------------------------------------------
# Contract-derived name sets (identifiers, free text, dims, subject-keyed)
# ---------------------------------------------------------------------------


@cache
def _contract_names() -> tuple[frozenset[str], frozenset[str], frozenset[str], frozenset[str]]:
    """(identifier column names, free-text column names, dim ``schema.table``\\ s,
    subject-keyed ``schema.table``\\ s) from the EP-9 contract flags."""
    from mimicwarehouse.schema.contract import load_contract

    contract = load_contract()
    identifiers = frozenset(c.name for t in contract.tables for c in t.columns if c.identifier)
    free_text = frozenset(c.name for t in contract.tables for c in t.columns if c.free_text)
    dims = frozenset(t.qualified_name for t in contract.dims())
    subject_keyed = frozenset(t.qualified_name for t in contract.subject_keyed())
    return identifiers, free_text, dims, subject_keyed


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


def audit_path(settings: Settings | None = None) -> Path:
    """``<data_root>/runs/audit.jsonl`` (append-only; created on first use)."""
    return (settings or get_settings()).layout["runs"] / AUDIT_FILENAME


def runs_db_path(settings: Settings | None = None) -> Path:
    """``<data_root>/warehouse/runs.duckdb`` — the read-only view store over the JSONL."""
    return (settings or get_settings()).layout["warehouse"] / RUNS_DB_FILENAME


def _append_audit(line: AuditLine, settings: Settings) -> None:
    """One canonical JSON object per line, ``O_APPEND`` + fsync (the EP-19 ledger
    pattern) so concurrent readers never see a torn line."""
    path = audit_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    blob = (json.dumps(line.model_dump(mode="json"), sort_keys=True) + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, blob)
        os.fsync(fd)
    finally:
        os.close(fd)


def _git_sha() -> str | None:
    from mimicwarehouse.dag.runner import git_short_sha

    return git_short_sha()


# ---------------------------------------------------------------------------
# Suppression hook (item 2) — EP-43 replaces SUPPRESSOR with disclose.suppress
# ---------------------------------------------------------------------------


def rowwise_suppress(
    df: polars.DataFrame, k: int, count_columns: list[str]
) -> tuple[polars.DataFrame, int]:
    """The default row-wise rule: drop every row in which any numeric count-family
    column has a value in ``1 .. k-1``; return ``(kept, dropped_count)``."""
    import polars as pl

    cols = [c for c in count_columns if c in df.columns and df.schema[c].is_numeric()]
    if not cols or k <= 1 or df.is_empty():
        return df, 0
    mask = pl.any_horizontal(*(pl.col(c).is_between(1, k - 1).fill_null(False) for c in cols))
    kept = df.filter(~mask)
    return kept, df.height - kept.height


SUPPRESSOR: Callable[[polars.DataFrame, int, list[str]], tuple[polars.DataFrame, int]] = (
    rowwise_suppress
)


# ---------------------------------------------------------------------------
# Static analysis over the json_serialize_sql AST
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _Analysis:
    """What the static walk learned about one parsed statement."""

    metadata_form: bool  # DESCRIBE / SHOW TABLES / SHOW ALL TABLES
    exempt: bool  # reads only meta.* / dims / information_schema / no tables
    reads_subject_keyed: bool
    count_aliases: list[str]


def _iter_dicts(obj: Any):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _iter_dicts(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _iter_dicts(v)


def _strip_ast(obj: Any) -> Any:
    """The AST minus aliases/locations — for structural GROUP BY key matching."""
    if isinstance(obj, dict):
        return {k: _strip_ast(v) for k, v in obj.items() if k not in _JUNK_AST_KEYS}
    if isinstance(obj, list):
        return [_strip_ast(v) for v in obj]
    return obj


def _find_identifier_outside_count(expr: Any, identifiers: frozenset[str]) -> str | None:
    """First identifier column referenced outside a count-family call, or None."""
    if isinstance(expr, dict):
        if (
            expr.get("class") == "FUNCTION"
            and str(expr.get("function_name", "")).casefold() in COUNT_FAMILY_FUNCTIONS
        ):
            return None  # identifiers are allowed anywhere inside count-family calls
        if expr.get("class") == "COLUMN_REF":
            names = expr.get("column_names") or []
            if names and str(names[-1]).casefold() in identifiers:
                return str(names[-1])
        for value in expr.values():
            hit = _find_identifier_outside_count(value, identifiers)
            if hit is not None:
                return hit
    elif isinstance(expr, list):
        for value in expr:
            hit = _find_identifier_outside_count(value, identifiers)
            if hit is not None:
                return hit
    return None


def _is_count_family_item(expr: dict[str, Any]) -> bool:
    if (
        expr.get("class") == "FUNCTION"
        and str(expr.get("function_name", "")).casefold() in COUNT_FAMILY_FUNCTIONS
    ):
        return True
    alias = str(expr.get("alias") or "")
    return bool(alias and COUNT_ALIAS_RE.match(alias.casefold()))


def _is_group_key(expr: dict[str, Any], position: int, node: dict[str, Any]) -> bool:
    """Whether select item ``expr`` (1-based ``position``) is a GROUP BY key of
    ``node`` — structurally, positionally (``GROUP BY 1``), by alias, or via
    ``GROUP BY ALL``."""
    if node.get("aggregate_handling") == "FORCE_AGGREGATES":  # GROUP BY ALL
        return True
    stripped_item = json.dumps(_strip_ast(expr), sort_keys=True)
    alias = str(expr.get("alias") or "").casefold()
    for group in node.get("group_expressions") or ():
        if json.dumps(_strip_ast(group), sort_keys=True) == stripped_item:
            return True
        if group.get("class") == "CONSTANT":
            value = (group.get("value") or {}).get("value")
            if isinstance(value, int) and value == position:
                return True
        if group.get("class") == "COLUMN_REF":
            names = group.get("column_names") or []
            if alias and len(names) == 1 and str(names[0]).casefold() == alias:
                return True
    return False


def _describe_ast(sql: str) -> tuple[list[dict[str, Any]] | None, str | None]:
    """``json_serialize_sql`` on a throw-away in-memory connection (the statement is
    passed as a **parameter**, never executed): ``(statements, refusal_reason)``."""
    import duckdb

    con = duckdb.connect(":memory:")
    try:
        row = con.execute("SELECT json_serialize_sql(?)", [sql]).fetchone()
    finally:
        con.close()
    doc = json.loads(row[0]) if row else {"error": True, "error_message": "no parse result"}
    if doc.get("error"):
        message = str(doc.get("error_message") or "parse failed")
        if str(doc.get("error_type") or "") == "not implemented":
            return None, (
                "only a single SELECT statement (CTEs allowed) or DESCRIBE <schema.table> / "
                "SHOW TABLES / SHOW ALL TABLES is allowed — statement type refused "
                f"({message})"
            )
        return None, f"syntax error: {message}"
    return list(doc.get("statements") or ()), None


def _analyze(sql: str) -> tuple[_Analysis | None, str | None]:
    """The full static walk (steps 1-3): ``(analysis, refusal_reason)``."""
    identifiers, _free_text, dims, subject_keyed = _contract_names()
    statements, reason = _describe_ast(sql)
    if reason is not None:
        return None, reason
    assert statements is not None
    if len(statements) != 1:
        return None, f"multi-statement input ({len(statements)} statements; exactly 1 allowed)"
    node = statements[0].get("node") or {}
    if node.get("type") == "SET_OPERATION_NODE":
        return None, (
            "set operations (UNION/EXCEPT/INTERSECT) are not supported — run each "
            "aggregate separately (parked for a later EP)"
        )
    if node.get("type") != "SELECT_NODE":
        return None, f"statement node {node.get('type')!r} is not a SELECT"

    # ---- metadata forms: DESCRIBE <schema.table> / SHOW TABLES / SHOW ALL TABLES ----
    metadata_form = False
    from_table = node.get("from_table") or {}
    if from_table.get("type") == "SHOW_REF":
        show_type = str(from_table.get("show_type") or "")
        if show_type == "DESCRIBE":
            inner = ((from_table.get("query") or {}).get("from_table")) or {}
            if inner.get("type") != "BASE_TABLE" or not inner.get("schema_name"):
                return None, "DESCRIBE takes exactly one qualified <schema.table>"
        elif str(from_table.get("table_name") or "") not in _SHOW_TABLE_NAMES:
            return None, "only SHOW TABLES / SHOW ALL TABLES are allowed SHOW forms"
        metadata_form = True

    # ---- one pass over the whole tree: functions, base tables, CTE names ----
    cte_names: set[str] = set()
    base_tables: list[tuple[str, str]] = []
    for d in _iter_dicts(node):
        if "cte_map" in d:
            for entry in (d["cte_map"] or {}).get("map") or ():
                cte_names.add(str(entry.get("key", "")).casefold())
        if d.get("class") == "FUNCTION":
            name = str(d.get("function_name", "")).casefold()
            if name in FORBIDDEN_FUNCTION_NAMES or name.startswith(FORBIDDEN_FUNCTION_PREFIXES):
                return None, (
                    f"function {name!r} is refused — file/environment/SQL-indirection "
                    "access is not allowed"
                )
        if d.get("type") == "BASE_TABLE":
            base_tables.append((str(d.get("schema_name") or ""), str(d.get("table_name") or "")))

    for schema, table in base_tables:
        if not schema:
            if table.casefold() in cte_names:
                continue
            return None, (
                f"unqualified table {table!r} — every table must be schema-qualified "
                f"({', '.join(sorted(ALLOWED_SCHEMAS))}) or a CTE of this statement"
            )
        if schema.casefold() not in ALLOWED_SCHEMAS:
            return None, (
                f"schema {schema!r} is not allowed; statements may read "
                f"{', '.join(sorted(ALLOWED_SCHEMAS))}"
            )

    qualified = {f"{s.casefold()}.{t.casefold()}" for s, t in base_tables if s}

    def exempt_ref(schema: str, table: str) -> bool:
        return (
            schema.casefold() in ("meta", "information_schema")
            or f"{schema.casefold()}.{table.casefold()}" in dims
        )

    exempt = metadata_form or all(exempt_ref(s, t) for s, t in base_tables if s)
    reads_subject_keyed = any(q in subject_keyed for q in qualified)

    count_aliases: list[str] = []
    if not metadata_form:
        select_list = node.get("select_list") or []
        found_count_family = False
        for position, item in enumerate(select_list, start=1):
            hit = _find_identifier_outside_count(item, identifiers)
            if hit is not None:
                return None, (
                    f"identifier column {hit!r} may appear only inside count(...) / "
                    "count(DISTINCT ...) / approx_count_distinct(...) — never as output, "
                    "in arithmetic, or in other aggregates (GOVERNANCE §4)"
                )
            if _is_count_family_item(item):
                found_count_family = True
                alias = str(item.get("alias") or "")
                if alias:
                    count_aliases.append(alias)
            if exempt:
                continue
            is_aggregate = (
                item.get("class") == "FUNCTION"
                and str(item.get("function_name", "")).casefold() in AGGREGATE_FUNCTIONS
                and not item.get("is_operator")
            )
            if is_aggregate or item.get("class") == "CONSTANT":
                continue
            if not _is_group_key(item, position, node):
                label = str(item.get("alias") or "") or f"#{position}"
                return None, (
                    f"select-list column {label} is neither an allowed aggregate nor a "
                    "GROUP BY key — results must be aggregates only (GOVERNANCE §4)"
                )
        if not exempt and not found_count_family:
            return None, (
                "no count-family column — every SELECT needs count(*)/count(x)/"
                "count(DISTINCT x) or an alias matching "
                f"{COUNT_ALIAS_RE.pattern!r} (unless it reads only meta.*, dims or "
                "information_schema)"
            )

    return (
        _Analysis(
            metadata_form=metadata_form,
            exempt=exempt,
            reads_subject_keyed=reads_subject_keyed,
            count_aliases=count_aliases,
        ),
        None,
    )


# ---------------------------------------------------------------------------
# Result checks (step 5)
# ---------------------------------------------------------------------------


def _count_columns(df: polars.DataFrame, count_aliases: list[str]) -> list[str]:
    return [
        c
        for c in df.columns
        if c in count_aliases
        or COUNT_ALIAS_RE.match(c.casefold())
        or c.casefold().startswith(COUNT_NAME_PREFIXES)
    ]


def _result_problem(df: polars.DataFrame, analysis: _Analysis) -> str | None:
    """Identifier / free-text checks over the executed frame, or None when clean."""
    import polars as pl

    identifiers, free_text, _dims, _subject_keyed = _contract_names()
    for name in df.columns:
        folded = name.casefold()
        if folded in identifiers:
            return f"output column {name!r} is an identifier column (GOVERNANCE §4)"
        if folded in free_text:
            return f"output column {name!r} is a contract free-text column (GOVERNANCE §4/§9)"
    if analysis.reads_subject_keyed:
        for name, dtype in df.schema.items():
            if dtype != pl.String or name.casefold() in LABEL_COLUMN_NAMES:
                continue
            series = df.get_column(name)
            max_len = series.str.len_chars().max()
            if max_len is not None and int(max_len) > FREE_TEXT_MAX_CHARS:  # type: ignore[arg-type]
                return (
                    f"output column {name!r} has a value longer than "
                    f"{FREE_TEXT_MAX_CHARS} characters (free-text heuristic)"
                )
            if bool(series.str.contains("\n", literal=True).any()):
                return (
                    f"output column {name!r} has a value containing a newline (free-text heuristic)"
                )
    return None


# ---------------------------------------------------------------------------
# safe_query (item 1)
# ---------------------------------------------------------------------------


def safe_query(
    sql: str,
    *,
    tier: Tier | str = "dev",
    k: int = K_FLOOR,
    row_cap: int = 200,
    timeout_s: float = 120,
    actor: str | None = None,
    settings: Settings | None = None,
) -> SafeResult:
    """Run one allow-listed aggregate statement against the tier catalog (module
    docstring has the full rule set). Raises :class:`SafeQueryRefused` after auditing;
    :class:`~mimicwarehouse.catalog.connect.CatalogOpenError` (no catalog) propagates
    unaudited — it is an environment error, not a statement verdict."""
    import duckdb

    settings = settings or get_settings()
    if tier not in ("fixture", "demo", "dev", "full"):
        raise SafeQueryError(f"unknown tier {tier!r}; expected fixture | demo | dev | full")
    started = time.perf_counter()
    sha = hashlib.sha256(sql.encode("utf-8")).hexdigest()
    snapshot_ids: dict[str, str] = {}
    resolved_actor = actor if actor else settings.role

    def refuse(reason: str) -> SafeQueryRefused:
        _append_audit(
            AuditLine(
                audit_id=uuid.uuid4().hex,
                ts=datetime.now(UTC).isoformat(timespec="milliseconds"),
                actor=resolved_actor,
                tier=str(tier),
                statement_sha256=sha,
                sql_text=sql,
                allowed=False,
                refusal_reason=reason,
                k=k,
                wall_ms=round((time.perf_counter() - started) * 1000, 1),
                duckdb_version=duckdb.__version__,
                snapshot_ids=snapshot_ids,
                git_sha=_git_sha(),
            ),
            settings,
        )
        return SafeQueryRefused(reason)

    if k < 1:
        raise refuse(f"k = {k} is invalid (k >= 1)")
    if str(tier) in CREDENTIALED_TIERS and k < K_FLOOR:
        raise refuse(
            f"k = {k} < {K_FLOOR} is refused on the {tier} tier (D-31/D-33; only the "
            "synthetic fixture/demo tiers may lower k)"
        )
    if row_cap < 1:
        raise refuse(f"row_cap = {row_cap} is invalid (row_cap >= 1)")

    analysis, reason = _analyze(sql)
    if reason is not None:
        raise refuse(reason)
    assert analysis is not None

    from mimicwarehouse.catalog.connect import open_catalog

    con = open_catalog(tier, settings=settings)
    try:
        row = con.execute("SELECT core_snapshot_id FROM meta.catalog_info").fetchone()
        snapshot_id = str(row[0]) if row and row[0] is not None else None
        if snapshot_id is not None:
            snapshot_ids["core"] = snapshot_id
        runs_db = runs_db_path(settings)
        if runs_db.is_file():
            # IF NOT EXISTS: DuckDB's in-process instance cache is keyed on path
            # (DESIGN §6 note b) — while another connection keeps the catalog instance
            # alive, an earlier call's ATTACH is still present instance-wide.
            escaped = runs_db.resolve().as_posix().replace("'", "''")
            con.execute(f"ATTACH IF NOT EXISTS '{escaped}' AS runs (READ_ONLY)")

        timer = threading.Timer(timeout_s, con.interrupt)
        timer.start()
        try:
            df = con.execute(sql).pl()
        except duckdb.InterruptException:
            raise refuse(f"timeout: statement exceeded {timeout_s} s and was interrupted") from None
        except duckdb.Error as exc:
            raise refuse(f"execution error: {exc}") from None
        finally:
            timer.cancel()
    finally:
        con.close()

    problem = _result_problem(df, analysis)
    if problem is not None:
        raise refuse(problem)
    df, rows_suppressed = SUPPRESSOR(df, k, _count_columns(df, analysis.count_aliases))
    if df.height > row_cap:
        raise refuse(
            f"result has {df.height} rows after suppression, over the row cap of "
            f"{row_cap} — aggregate further or raise --row-cap deliberately"
        )

    audit_id = uuid.uuid4().hex
    _append_audit(
        AuditLine(
            audit_id=audit_id,
            ts=datetime.now(UTC).isoformat(timespec="milliseconds"),
            actor=resolved_actor,
            tier=str(tier),
            statement_sha256=sha,
            sql_text=sql,
            allowed=True,
            refusal_reason=None,
            n_rows=df.height,
            rows_suppressed=rows_suppressed,
            k=k,
            wall_ms=round((time.perf_counter() - started) * 1000, 1),
            duckdb_version=duckdb.__version__,
            snapshot_ids=snapshot_ids,
            git_sha=_git_sha(),
        ),
        settings,
    )
    return SafeResult(
        df=df,
        n_rows=df.height,
        rows_suppressed=rows_suppressed,
        statement_sha256=sha,
        audit_id=audit_id,
        tier=str(tier),
        k=k,
        duckdb_version=duckdb.__version__,
        snapshot_id=snapshot_ids.get("core"),
    )


# ---------------------------------------------------------------------------
# runs.duckdb (item 3) — the audit view store; EP-35 adds the ledger views
# ---------------------------------------------------------------------------


def build_runs_db(settings: Settings | None = None) -> Path:
    """Build ``warehouse/runs.duckdb.new`` with the ``audit`` view over
    ``runs/audit.jsonl`` and publish it by the DESIGN §6 rename-aside swap; readers
    open it read-only (``mwh runs refresh`` calls this). Creates ``runs/`` and an
    empty ``audit.jsonl`` when missing (the view then reads 0 rows)."""
    import duckdb

    from mimicwarehouse.catalog.build import swap_catalog

    settings = settings or get_settings()
    audit = audit_path(settings)
    audit.parent.mkdir(parents=True, exist_ok=True)
    audit.touch(exist_ok=True)
    dest = runs_db_path(settings)
    dest.parent.mkdir(parents=True, exist_ok=True)
    settings.layout["tmp_duckdb"].mkdir(parents=True, exist_ok=True)
    new = dest.with_name(dest.name + ".new")
    new.unlink(missing_ok=True)
    escaped = audit.resolve().as_posix().replace("'", "''")
    con = duckdb.connect(str(new), config=dict(settings.duckdb_settings("app")))
    try:
        con.execute(
            "CREATE VIEW audit AS SELECT * FROM "
            f"read_json_auto('{escaped}', format = 'newline_delimited')"
        )
        con.execute("CHECKPOINT")
    finally:
        con.close()
    swap_catalog(new, dest, "runs")
    return dest


__all__ = [
    "AGGREGATE_FUNCTIONS",
    "ALLOWED_SCHEMAS",
    "AUDIT_FILENAME",
    "COUNT_ALIAS_RE",
    "COUNT_FAMILY_FUNCTIONS",
    "CREDENTIALED_TIERS",
    "FORBIDDEN_FUNCTION_NAMES",
    "FORBIDDEN_FUNCTION_PREFIXES",
    "FREE_TEXT_MAX_CHARS",
    "K_FLOOR",
    "LABEL_COLUMN_NAMES",
    "RUNS_DB_FILENAME",
    "SUPPRESSOR",
    "AuditLine",
    "SafeQueryError",
    "SafeQueryRefused",
    "SafeResult",
    "audit_path",
    "build_runs_db",
    "rowwise_suppress",
    "runs_db_path",
    "safe_query",
]
