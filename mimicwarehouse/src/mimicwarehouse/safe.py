"""Safe-query wrapper + audit log — the governance choke point (EP-30; DESIGN §12,
GOVERNANCE §4/§5/§8, D-31/D-32/D-33/D-39; hardened in EP-33 B1).

From this module on, *every* result a Claude session or an export can see comes through
:func:`safe_query` (or the ``mwh sql`` CLI built on it). The pipeline, in order:

1. **Parse** (DuckDB ``json_serialize_sql``): exactly one statement, of type SELECT
   (CTEs allowed; since EP-33 also ``UNION`` / ``UNION ALL`` / ``EXCEPT`` / ``INTERSECT``
   of SELECTs — every leaf is checked on its own, see 3) or one of the metadata forms
   ``DESCRIBE <schema.table>`` / ``SHOW TABLES`` / ``SHOW ALL TABLES``. Everything else
   (COPY, ATTACH, INSTALL, LOAD, PRAGMA, SET, CREATE, INSERT, UPDATE, DELETE, EXPORT,
   IMPORT, CALL, BEGIN, multi-statement, ``UNION BY NAME``) is refused.
2. **Allow-list**: no table/scalar functions that touch files or the environment
   (:data:`FORBIDDEN_FUNCTION_NAMES` / :data:`FORBIDDEN_FUNCTION_PREFIXES`; ``query`` /
   ``query_table`` are included — they take SQL/table-name strings and would bypass this
   static walk); every qualified table must live in :data:`ALLOWED_SCHEMAS` (notes
   schemas never exist in these catalogs); unqualified names must be CTEs.
3. **Aggregate-only** (the select list of every leaf SELECT): every result column must be
   an aggregate call (:data:`AGGREGATE_FUNCTIONS` — deliberately no value-collecting
   aggregates such as ``string_agg`` / ``list`` / ``histogram`` / ``arg_min``), optionally
   wrapped in ``CAST`` / ``TRY_CAST`` (EP-33 B1a: ``CAST(sum(x) AS BIGINT)`` verifies; a
   cast around arithmetic or around a bare column does not — the set stays closed,
   DIS-3), a GROUP BY key, or a constant; identifier columns (contract flags, GOVERNANCE
   §4) may appear **only** inside count-family calls (:data:`COUNT_FAMILY_FUNCTIONS`),
   never as output, inside arithmetic, or in MIN/MAX/string aggregates. At least one
   column must be a **real** count-family call — ``count(*)`` / ``count(x)`` /
   ``count(DISTINCT x)`` / ``approx_count_distinct(x)``, possibly cast-wrapped; an alias
   alone never satisfies it, an alias matching :data:`COUNT_ALIAS_RE` on a non-count
   expression is refused (DKB-1/SGT-1/SGT-4: the suppressor must only ever test group
   sizes), and a cast-wrapped count must be aliased (its generated column name would
   evade the suppressor). The count requirement is waived when the statement reads only
   registry tables (:func:`is_registry_ref`: ``meta.*``, ``information_schema``, contract
   dims, :data:`REGISTRY_TABLES`) or metadata functions (``duckdb_tables()`` /
   ``duckdb_columns()``), which GOVERNANCE §4 explicitly allows (EP-170 amendment 1).
   Set operations (EP-33 B1b): every branch is checked against its own node; branch
   widths and count-family **positions** must agree across branches (the output column
   takes branch 1's name and the combined frame is suppressed as one).
4. **Execute** on :func:`~mimicwarehouse.catalog.connect.open_catalog` (READ_ONLY, app
   profile, hardened) with ``warehouse/runs.duckdb`` attached read-only as ``runs`` when
   it exists (:func:`mimicwarehouse.engine.attach_read_only`), under a ``threading.Timer``
   that calls ``con.interrupt()`` at ``timeout_s``. Any DuckDB error before or during
   execution is refused with a **sanitized** message (:func:`sanitize_error_text`: first
   line only, quoted literals and standalone numbers masked, ~120 characters — DuckDB
   quotes the offending cell value in conversion errors, DKB-2; DKB-3/SGT-6 put the
   snapshot read and the attach inside the audited path).
5. **Result checks**: refuse output columns named like identifiers or like contract
   ``free_text`` columns (refused by name, ARCH-10/FC-18); the free-text value heuristic
   (any VARCHAR value longer than :data:`FREE_TEXT_MAX_CHARS` characters or containing a
   newline) applies to every statement that is **not** registry-exempt (EP-33 B1c: reads
   of ``mimiciv_derived`` and non-registry ``marts`` are scanned too, P3C-5); dims and
   ``meta.*`` are exempt, and the label columns :data:`LABEL_COLUMN_NAMES`
   (``drgcodes.description``, ``hcpcsevents.short_description``; since EP-35 also the
   ledger columns ``refusal_reason`` and ``error``, whose values legitimately exceed 64
   characters) are allow-listed (EP-170 amendment 1); the post-suppression row count
   must not exceed ``row_cap``.
6. **k-suppression** via :data:`SUPPRESSOR` (see below) over the real count columns
   only; on ``dev``/``full`` a ``k`` below 11 is refused (D-31/D-33); on
   ``fixture``/``demo`` (synthetic / ODbL) the caller may lower it. Extreme-value
   aggregates (``min`` / ``max`` / ``mode`` / ``median`` / quantiles) stay admitted and are
   released only inside k-gated rows (SGT-2, owner decision; since EP-43 the gate is
   complementary — a row needed to keep a small cell from being backed out is withheld
   too, ``docs/methods/disclosure.md``).

Errors are three-way (EP-33 B1d): :class:`SafeQueryRefused` is a governance verdict
(exit :data:`EXIT_REFUSED` = 3), raised **after** auditing; :class:`SafeQueryError` is a
usage error (``k < 1``, ``row_cap < 1``, unknown tier; exit :data:`EXIT_USAGE` = 2),
also audited — its ``refusal_reason`` starts with ``usage: `` so the EP-35 ledger views
can filter it; :class:`~mimicwarehouse.catalog.connect.CatalogOpenError` is an
environment error (exit 2), unaudited. ``tier`` / ``k`` default to
``settings.default_tier`` / ``settings.k_suppression``.

Every call — allowed, refused or usage — appends one :class:`AuditLine` to the
append-only ``runs/audit.jsonl`` through :func:`mimicwarehouse.fsio.append_jsonl`
(``O_APPEND`` + checked write + fsync, one canonical JSON object per line; D-24,
GOVERNANCE §8): never result values, only the statement text/hash, counts and provenance
(``snapshot_ids`` is a ``{layer: id}`` dict per the DESIGN §11 glossary — here
``{"core": <core_snapshot_id>}`` of the queried catalog). :func:`build_runs_db` exposes
the file as the ``audit`` view of ``warehouse/runs.duckdb`` (published with
:func:`mimicwarehouse.publish.swap_file`; ``mwh runs refresh`` calls it) beside the EP-35
ledger views ``ledger`` / ``benchmarks`` / ``manifests`` / ``attrition``
(:func:`mimicwarehouse.run.runs_db_views`); the views read with ``ignore_errors = true``
so a torn trailing line (LGR-1) skips instead of breaking every query. ``safe_query``
detaches (:func:`mimicwarehouse.engine.detach`) before it attaches the store, so a rebuild
published while this process holds the catalog instance is visible to the next call
(DESIGN §6.1 b).
``runs`` is deliberately **not** a registry schema (EP-33 checkpoint): GROUP BYs over the
ledger views need a real count-family column like any subject-level read, and a
``refusal_reason LIKE 'usage: %'`` predicate separates argument errors from refusals.

**Suppression hook contract**: :data:`SUPPRESSOR` is a module-level
``Callable[[polars.DataFrame, int, list[str]], tuple[polars.DataFrame, int]]`` —
``(df, k, count_columns) -> (suppressed_df, rows_suppressed)``. Since EP-43 the
default is :func:`mimicwarehouse.disclose.safe_suppressor` — ``disclose.suppress``
(complementary suppression: a margin with exactly one small cell also loses its
next-smallest cell, two nested totals whose difference is small lose the smaller one)
released **row-wise**: every row with a hidden cell is withheld, so the extreme-value
aggregates it carries leave with it (SGT-2, D-31 addendum). :func:`rowwise_suppress`
(the EP-30 primary-only rule) stays importable for comparison and tests; callers never
bypass the hook, and a test may still swap it by assigning the module attribute.

Owner row viewing is **not** here — that is the app's audited ``owner_rows()`` path
(EP-58); this module behaves the same for every role.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict

from mimicwarehouse.config import Settings, Tier, get_settings
from mimicwarehouse.console import EXIT_REFUSED, EXIT_USAGE
from mimicwarehouse.disclose import safe_suppressor

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

#: Registry schemas (EP-33 B1c): metadata-only surfaces GOVERNANCE §4 lets a session read
#: without a count column and without the free-text value scan. ``runs`` deliberately
#: does **not** join (owner decision, 2026-09-01): ``runs.audit`` carries statement text.
REGISTRY_SCHEMAS: frozenset[str] = frozenset({"meta", "information_schema"})
#: Named registry tables outside the registry schemas (``schema.table``, casefolded) —
#: seeded for EP-47's cohort registry so it does not need a second mechanism.
REGISTRY_TABLES: frozenset[str] = frozenset({"marts.cohorts"})

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

#: Aliases reserved for count-family columns (brief EP-30 item 1c). Since EP-33 (DKB-1 /
#: SGT-1) an alias never *satisfies* the count requirement — it is refused on any
#: non-count expression so the suppressor only ever tests group sizes.
COUNT_ALIAS_RE = re.compile(r"^(n|n_.*|.*_n|count|.*_count|cnt|.*_cnt|num_.*)$")

#: Result-column *name* prefixes that mark unaliased count outputs (``count_star()``,
#: ``count(DISTINCT …)``, ``approx_count_distinct(…)``) for suppression.
COUNT_NAME_PREFIXES: tuple[str, ...] = ("count", "approx_count_distinct")

#: The free-text value heuristic (result check 5).
FREE_TEXT_MAX_CHARS = 64
#: Label columns exempt from the value heuristic (EP-170 amendment 1: legitimate dim-like
#: labels on subject-keyed tables — ``drgcodes.description``,
#: ``hcpcsevents.short_description``; EP-35 amendment c: the ledger label columns
#: ``runs.audit.refusal_reason`` and ``runs.benchmarks.error``, whose values legitimately
#: exceed 64 characters and are project-authored, never data).
LABEL_COLUMN_NAMES: frozenset[str] = frozenset(
    {"description", "short_description", "refusal_reason", "error"}
)

#: The k floor on credentialed tiers (D-31/D-33).
K_FLOOR = 11
CREDENTIALED_TIERS: frozenset[str] = frozenset({"dev", "full"})

#: Longest sanitized engine-error text that enters a refusal / audit line (DKB-2).
ERROR_TEXT_MAX_CHARS = 120

#: Set-operation kinds the walk supports (json_serialize_sql ``setop_type``; DuckDB 1.5.5
#: spells ``UNION BY NAME`` as ``UNION_BY_NAME`` and is refused by name).
_SET_OPERATION_TYPES: frozenset[str] = frozenset({"UNION", "EXCEPT", "INTERSECT"})

#: SHOW_REF table names DuckDB desugars ``SHOW TABLES`` / ``SHOW ALL TABLES`` into.
_SHOW_TABLE_NAMES: frozenset[str] = frozenset({'"tables"', "__show_tables_expanded"})

_JUNK_AST_KEYS = ("alias", "query_location")

_TIERS: tuple[str, ...] = ("fixture", "demo", "dev", "full")

# single- or double-quoted SQL literal (doubled quotes inside are the escape form)
_QUOTED_LITERAL_RE = re.compile(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"")
# a standalone number token (not glued to a word such as INT32 / UINT8)
_NUMBER_TOKEN_RE = re.compile(r"(?<![\w.])[-+]?\d[\d.,]*(?:[eE][-+]?\d+)?(?![\w.])")


class SafeQueryRefused(RuntimeError):
    """The statement (or its result) violates the safe-query rules; already audited."""


class SafeQueryError(RuntimeError):
    """A usage error (``k < 1``, ``row_cap < 1``, unknown tier) — exit
    :data:`EXIT_USAGE`; audited with a ``usage: `` reason (EP-33 B1d)."""


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
# Contract-derived name sets (identifiers, free text, dims) + registry exemptions
# ---------------------------------------------------------------------------


@cache
def _contract_names() -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
    """(identifier column names, free-text column names, dim ``schema.table``\\ s) from
    the EP-9 contract flags."""
    from mimicwarehouse.schema.contract import load_contract

    contract = load_contract()
    identifiers = frozenset(c.name for t in contract.tables for c in t.columns if c.identifier)
    free_text = frozenset(c.name for t in contract.tables for c in t.columns if c.free_text)
    dims = frozenset(t.qualified_name for t in contract.dims())
    return identifiers, free_text, dims


def identifier_column_names() -> frozenset[str]:
    """The contract's identifier column names (``subject_id``, ``hadm_id``, ``stay_id``,
    ``note_id``, …) — what the result check refuses by name and what ``run.save_table``
    refuses in a run folder (EP-35)."""
    return _contract_names()[0]


def is_registry_ref(schema: str, table: str) -> bool:
    """Whether ``schema.table`` is a registry / metadata surface (module note 3):
    :data:`REGISTRY_SCHEMAS`, :data:`REGISTRY_TABLES`, or a contract dim
    (``Table.is_dim``). Case-insensitive."""
    folded_schema = schema.casefold()
    if folded_schema in REGISTRY_SCHEMAS:
        return True
    qualified = f"{folded_schema}.{table.casefold()}"
    return qualified in REGISTRY_TABLES or qualified in _contract_names()[2]


def sanitize_error_text(text: str, *, max_chars: int = ERROR_TEXT_MAX_CHARS) -> str:
    """The first line of an engine error with every quoted literal replaced by ``'...'``
    and every standalone number by ``#``, whitespace collapsed, cut to ``max_chars``
    (DKB-2: DuckDB quotes the offending cell value in conversion errors, so raw error text
    must never reach a refusal message or an audit line)."""
    first = text.strip().split("\n", 1)[0]
    masked = _QUOTED_LITERAL_RE.sub("'...'", first)
    masked = _NUMBER_TOKEN_RE.sub("#", masked)
    masked = " ".join(masked.split())
    if len(masked) > max_chars:
        masked = masked[: max_chars - 3] + "..."
    return masked


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
    """One canonical JSON object per line through the ledger canon
    (:func:`mimicwarehouse.fsio.append_jsonl`: ``O_APPEND``, checked write, fsync)."""
    from mimicwarehouse.fsio import append_jsonl

    append_jsonl(audit_path(settings), line.model_dump(mode="json"))


def _git_sha() -> str | None:
    from mimicwarehouse.dag.runner import git_short_sha

    return git_short_sha()


# ---------------------------------------------------------------------------
# Suppression hook (item 2) — since EP-43 the default is disclose.safe_suppressor
# ---------------------------------------------------------------------------


def rowwise_suppress(
    df: polars.DataFrame, k: int, count_columns: list[str]
) -> tuple[polars.DataFrame, int]:
    """The EP-30 primary-only rule: drop every row in which any numeric count-family
    column has a value in ``1 .. k-1``; return ``(kept, dropped_count)``. Kept for
    comparison — the live hook is :func:`mimicwarehouse.disclose.safe_suppressor`."""
    import polars as pl

    cols = [c for c in count_columns if c in df.columns and df.schema[c].is_numeric()]
    if not cols or k <= 1 or df.is_empty():
        return df, 0
    mask = pl.any_horizontal(*(pl.col(c).is_between(1, k - 1).fill_null(False) for c in cols))
    kept = df.filter(~mask)
    return kept, df.height - kept.height


SUPPRESSOR: Callable[[polars.DataFrame, int, list[str]], tuple[polars.DataFrame, int]] = (
    safe_suppressor
)


# ---------------------------------------------------------------------------
# Static analysis over the json_serialize_sql AST
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _Analysis:
    """What the static walk learned about one parsed statement."""

    metadata_form: bool  # DESCRIBE / SHOW TABLES / SHOW ALL TABLES
    exempt: bool  # reads only registry refs (meta.* / dims / information_schema) or no tables
    count_aliases: list[str]  # aliases of the real count-family columns (all branches)
    unaliased_counts: int  # unaliased count-family items in branch 1 (name-prefix matched)


@dataclass(slots=True)
class _LeafReport:
    """What :func:`_check_select_list` learned about one leaf SELECT."""

    width: int
    count_positions: list[bool] = field(default_factory=list)
    count_aliases: list[str] = field(default_factory=list)
    unaliased_counts: int = 0


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


def _unwrap_cast(expr: Any) -> Any:
    """Descend through ``CAST`` / ``TRY_CAST`` nodes (json_serialize_sql ``class ==
    'CAST'``, ``type == 'OPERATOR_CAST'``, ``try_cast`` flag, the operand under ``child``;
    ``x::T`` serializes the same way) to the expression being cast (EP-33 B1a)."""
    while isinstance(expr, dict) and expr.get("class") == "CAST":
        expr = expr.get("child") or {}
    return expr


def _function_name(expr: Any) -> str:
    if isinstance(expr, dict) and expr.get("class") == "FUNCTION":
        return str(expr.get("function_name", "")).casefold()
    return ""


def _find_identifier_outside_count(expr: Any, identifiers: frozenset[str]) -> str | None:
    """First identifier column referenced outside a count-family call, or None."""
    if isinstance(expr, dict):
        if _function_name(expr) in COUNT_FAMILY_FUNCTIONS:
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
    """A real count-family FUNCTION node, possibly cast-wrapped — never an alias
    (DKB-1/SGT-1)."""
    return _function_name(_unwrap_cast(expr)) in COUNT_FAMILY_FUNCTIONS


def _is_allowed_aggregate(expr: dict[str, Any]) -> bool:
    """An :data:`AGGREGATE_FUNCTIONS` call, optionally cast-wrapped; operator arithmetic
    (``is_operator``) never qualifies, even under a cast (DIS-3 stays parked)."""
    inner = _unwrap_cast(expr)
    return (
        _function_name(inner) in AGGREGATE_FUNCTIONS
        and isinstance(inner, dict)
        and not inner.get("is_operator")
    )


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


def _describe_ast(sql: str, settings: Settings) -> tuple[list[dict[str, Any]] | None, str | None]:
    """``json_serialize_sql`` on a throw-away in-memory app-profile connection (the
    statement is passed as a **parameter**, never executed): ``(statements,
    refusal_reason)``."""
    from mimicwarehouse.engine import open_duckdb

    con = open_duckdb("app", settings=settings)
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


def _leaf_selects(node: dict[str, Any], leaves: list[dict[str, Any]]) -> str | None:
    """Collect the leaf SELECT nodes of ``node`` left-to-right into ``leaves`` (a plain
    SELECT is its own single leaf); the refusal reason for unsupported shapes."""
    kind = str(node.get("type") or "")
    if kind == "SELECT_NODE":
        leaves.append(node)
        return None
    if kind == "SET_OPERATION_NODE":
        setop = str(node.get("setop_type") or "")
        if setop == "UNION_BY_NAME":
            return (
                "UNION BY NAME is not supported — the safe-query walk matches set-operation "
                "columns by position (branch 1 names the output); use UNION / UNION ALL "
                "with the same column order in every branch"
            )
        if setop not in _SET_OPERATION_TYPES:
            return f"set operation {setop!r} is not supported"
        for side in ("left", "right"):
            reason = _leaf_selects(node.get(side) or {}, leaves)
            if reason is not None:
                return reason
        return None
    if leaves:
        return f"set-operation branch {len(leaves) + 1} is a {kind!r} node, not a SELECT"
    return f"statement node {kind!r} is not a SELECT"


def _check_select_list(
    node: dict[str, Any], identifiers: frozenset[str], exempt: bool, label: str
) -> tuple[_LeafReport | None, str | None]:
    """Step 3 over one leaf SELECT: ``(report, refusal_reason)``. ``label`` names the
    branch in a set operation (empty for a plain SELECT)."""
    where = f" ({label})" if label else ""
    select_list = node.get("select_list") or []
    report = _LeafReport(width=len(select_list))
    for position, item in enumerate(select_list, start=1):
        hit = _find_identifier_outside_count(item, identifiers)
        if hit is not None:
            return None, (
                f"identifier column {hit!r} may appear only inside count(...) / "
                "count(DISTINCT ...) / approx_count_distinct(...) — never as output, "
                f"in arithmetic, or in other aggregates (GOVERNANCE §4){where}"
            )
        alias = str(item.get("alias") or "")
        is_count = _is_count_family_item(item)
        report.count_positions.append(is_count)
        if is_count:
            if alias:
                report.count_aliases.append(alias)
            elif item.get("class") == "CAST":
                return None, (
                    f"select-list column #{position}{where} is a cast-wrapped count without "
                    "an alias — alias the cast count column (its generated name would not be "
                    "recognised by the k-suppressor)"
                )
            else:
                report.unaliased_counts += 1
        elif alias and COUNT_ALIAS_RE.match(alias.casefold()):
            return None, (
                f"count-named alias {alias!r} on a non-count expression{where} — aliases "
                f"matching {COUNT_ALIAS_RE.pattern!r} are reserved for count(*)/count(x)/"
                "count(DISTINCT x)/approx_count_distinct(x), whose values drive k-suppression"
            )
        if exempt:
            continue
        if _is_allowed_aggregate(item) or item.get("class") == "CONSTANT":
            continue
        if not _is_group_key(item, position, node):
            name = alias or f"#{position}"
            return None, (
                f"select-list column {name}{where} is neither an allowed aggregate "
                "(optionally CAST-wrapped) nor a GROUP BY key — results must be aggregates "
                "only (GOVERNANCE §4)"
            )
    if not exempt and not any(report.count_positions):
        return None, (
            f"no count-family column{where} — every SELECT needs count(*)/count(x)/"
            "count(DISTINCT x)/approx_count_distinct(x) as a real call (an alias alone "
            "does not count; unless it reads only meta.*, dims, information_schema or "
            "registry tables)"
        )
    return report, None


def _analyze(sql: str, settings: Settings) -> tuple[_Analysis | None, str | None]:
    """The full static walk (steps 1-3): ``(analysis, refusal_reason)``."""
    identifiers, _free_text, _dims = _contract_names()
    statements, reason = _describe_ast(sql, settings)
    if reason is not None:
        return None, reason
    assert statements is not None
    if len(statements) != 1:
        return None, f"multi-statement input ({len(statements)} statements; exactly 1 allowed)"
    node = statements[0].get("node") or {}
    leaves: list[dict[str, Any]] = []
    reason = _leaf_selects(node, leaves)
    if reason is not None:
        return None, reason

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

    # ---- one pass over the whole tree (all branches): functions, base tables, CTEs ----
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

    exempt = metadata_form or all(is_registry_ref(s, t) for s, t in base_tables if s)

    if metadata_form:
        return _Analysis(
            metadata_form=True, exempt=True, count_aliases=[], unaliased_counts=0
        ), None

    # ---- step 3 per leaf; set operations must agree positionally ----
    reports: list[_LeafReport] = []
    for index, leaf in enumerate(leaves, start=1):
        label = f"set-operation branch {index}" if len(leaves) > 1 else ""
        if label and (leaf.get("from_table") or {}).get("type") == "SHOW_REF":
            return None, f"{label}: DESCRIBE / SHOW forms cannot be combined in a set operation"
        report, reason = _check_select_list(leaf, identifiers, exempt, label)
        if reason is not None:
            return None, reason
        assert report is not None
        reports.append(report)
    first = reports[0]
    for index, report in enumerate(reports[1:], start=2):
        if report.width != first.width:
            return None, (
                f"set-operation branch {index} has {report.width} select-list column(s), "
                f"branch 1 has {first.width}"
            )
        for position, (leftmost, here) in enumerate(
            zip(first.count_positions, report.count_positions, strict=True), start=1
        ):
            if leftmost != here:
                return None, (
                    f"set-operation branch {index}, select-list column #{position} is "
                    f"{'' if here else 'not '}count-family while branch 1's is "
                    f"{'' if leftmost else 'not '}— count-family positions must agree "
                    "across branches (the output column takes branch 1's name and the "
                    "combined frame is k-suppressed as one)"
                )
    count_aliases: list[str] = []
    for report in reports:
        for alias in report.count_aliases:
            if alias not in count_aliases:
                count_aliases.append(alias)
    return (
        _Analysis(
            metadata_form=False,
            exempt=exempt,
            count_aliases=count_aliases,
            unaliased_counts=first.unaliased_counts,
        ),
        None,
    )


# ---------------------------------------------------------------------------
# Result checks (step 5)
# ---------------------------------------------------------------------------


def _count_columns(df: polars.DataFrame, analysis: _Analysis) -> list[str]:
    """The frame's real count columns: the audited aliases, plus — only when branch 1
    had unaliased count calls — DuckDB's generated ``count…`` names."""
    cols = [c for c in df.columns if c in analysis.count_aliases]
    if analysis.unaliased_counts:
        cols += [
            c for c in df.columns if c not in cols and c.casefold().startswith(COUNT_NAME_PREFIXES)
        ]
    return cols


def _result_problem(df: polars.DataFrame, analysis: _Analysis) -> str | None:
    """Identifier / free-text checks over the executed frame, or None when clean."""
    import polars as pl

    identifiers, free_text, _dims = _contract_names()
    for name in df.columns:
        folded = name.casefold()
        if folded in identifiers:
            return f"output column {name!r} is an identifier column (GOVERNANCE §4)"
        if folded in free_text:
            return f"output column {name!r} is a contract free-text column (GOVERNANCE §4/§9)"
    if not analysis.exempt:
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
    tier: Tier | str | None = None,
    k: int | None = None,
    row_cap: int = 200,
    timeout_s: float = 120,
    actor: str | None = None,
    settings: Settings | None = None,
) -> SafeResult:
    """Run one allow-listed aggregate statement against the tier catalog (module
    docstring has the full rule set). ``tier`` defaults to ``settings.default_tier``,
    ``k`` to ``settings.k_suppression``. Raises :class:`SafeQueryRefused` (governance)
    or :class:`SafeQueryError` (usage: ``k < 1``, ``row_cap < 1``, unknown tier) after
    auditing; :class:`~mimicwarehouse.catalog.connect.CatalogOpenError` (no catalog)
    propagates unaudited — it is an environment error, not a statement verdict."""
    import duckdb

    settings = settings or get_settings()
    resolved_tier: str = str(tier if tier is not None else settings.default_tier)
    resolved_k: int = k if k is not None else settings.k_suppression
    started = time.perf_counter()
    sha = hashlib.sha256(sql.encode("utf-8")).hexdigest()
    snapshot_ids: dict[str, str] = {}
    resolved_actor = actor if actor else settings.role

    def audit_verdict(reason: str) -> None:
        _append_audit(
            AuditLine(
                audit_id=uuid.uuid4().hex,
                ts=datetime.now(UTC).isoformat(timespec="milliseconds"),
                actor=resolved_actor,
                tier=resolved_tier,
                statement_sha256=sha,
                sql_text=sql,
                allowed=False,
                refusal_reason=reason,
                k=resolved_k,
                wall_ms=round((time.perf_counter() - started) * 1000, 1),
                duckdb_version=duckdb.__version__,
                snapshot_ids=snapshot_ids,
                git_sha=_git_sha(),
            ),
            settings,
        )

    def refuse(reason: str) -> SafeQueryRefused:
        audit_verdict(reason)
        return SafeQueryRefused(reason)

    def usage(reason: str) -> SafeQueryError:
        audit_verdict(f"usage: {reason}")
        return SafeQueryError(reason)

    if resolved_tier not in _TIERS:
        raise usage(f"unknown tier {resolved_tier!r}; expected {' | '.join(_TIERS)}")
    if resolved_k < 1:
        raise usage(f"k = {resolved_k} is invalid (k >= 1)")
    if row_cap < 1:
        raise usage(f"row_cap = {row_cap} is invalid (row_cap >= 1)")
    if resolved_tier in CREDENTIALED_TIERS and resolved_k < K_FLOOR:
        raise refuse(
            f"k = {resolved_k} < {K_FLOOR} is refused on the {resolved_tier} tier (D-31/D-33; "
            "only the synthetic fixture/demo tiers may lower k)"
        )

    analysis, reason = _analyze(sql, settings)
    if reason is not None:
        raise refuse(reason)
    assert analysis is not None

    from mimicwarehouse.catalog.connect import open_catalog
    from mimicwarehouse.engine import attach_read_only, detach

    con = open_catalog(resolved_tier, settings=settings)
    try:
        # DKB-3/SGT-6: the pre-execution catalog statements sit inside the audited path
        try:
            row = con.execute("SELECT core_snapshot_id FROM meta.catalog_info").fetchone()
            snapshot_id = str(row[0]) if row and row[0] is not None else None
            if snapshot_id is not None:
                snapshot_ids["core"] = snapshot_id
            runs_db = runs_db_path(settings)
            if runs_db.is_file():
                # EP-35: a `mwh runs refresh` published while this process still held the
                # catalog instance must not be served from the stale attach (DESIGN §6.1 b)
                detach(con, "runs")
                attach_read_only(con, runs_db, "runs")
        except duckdb.Error as exc:
            raise refuse(
                f"catalog error before execution: {exc.__class__.__name__}: "
                f"{sanitize_error_text(str(exc))}"
            ) from None

        def interrupt() -> None:
            # DKB-4: the timer may fire while the connection is already closing
            with contextlib.suppress(Exception):
                con.interrupt()

        timer = threading.Timer(timeout_s, interrupt)
        timer.start()
        try:
            df = con.execute(sql).pl()
        except duckdb.InterruptException:
            raise refuse(f"timeout: statement exceeded {timeout_s} s and was interrupted") from None
        except duckdb.Error as exc:
            raise refuse(
                f"execution error: {exc.__class__.__name__}: {sanitize_error_text(str(exc))}"
            ) from None
        finally:
            timer.cancel()  # before con.close() (outer finally)
    finally:
        con.close()

    problem = _result_problem(df, analysis)
    if problem is not None:
        raise refuse(problem)
    df, rows_suppressed = SUPPRESSOR(df, resolved_k, _count_columns(df, analysis))
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
            tier=resolved_tier,
            statement_sha256=sha,
            sql_text=sql,
            allowed=True,
            refusal_reason=None,
            n_rows=df.height,
            rows_suppressed=rows_suppressed,
            k=resolved_k,
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
        tier=resolved_tier,
        k=resolved_k,
        duckdb_version=duckdb.__version__,
        snapshot_id=snapshot_ids.get("core"),
    )


# ---------------------------------------------------------------------------
# runs.duckdb (item 3) — the audit view store + the EP-35 ledger views
# ---------------------------------------------------------------------------

#: The views ``build_runs_db`` creates, in creation order (``attrition`` reads
#: ``manifests``): EP-30's ``audit`` plus the EP-35 ledger views.
RUNS_DB_VIEWS: tuple[str, ...] = ("audit", "ledger", "benchmarks", "manifests", "attrition")


def _ledger_has_record(path: Path) -> bool:
    """Whether at least one line of the JSONL parses (a tolerant scan — a torn or merged
    line is skipped, never raised, unlike :func:`fsio.read_jsonl`)."""
    with Path(path).open("rb") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            return True
    return False


def build_runs_db(settings: Settings | None = None) -> Path:
    """Build ``warehouse/runs.duckdb.new`` with the :data:`RUNS_DB_VIEWS` — the ``audit``
    view over ``runs/audit.jsonl`` (EP-30) and the EP-35 ``ledger`` / ``benchmarks`` /
    ``manifests`` / ``attrition`` views (:func:`mimicwarehouse.run.runs_db_views`) — and
    publish it with :func:`mimicwarehouse.publish.swap_file` (rename-aside; a reader
    holding the live file raises :class:`~mimicwarehouse.publish.SwapBlockedError` naming
    ``mwh runs refresh`` as the remedy). Readers open it read-only; nothing else ever
    writes it (single-writer rule, DESIGN §6). Creates ``runs/`` and the empty ledgers
    when missing (the views then read 0 rows).

    The audit view is ``read_json_auto(..., ignore_errors = true)`` (LGR-1): DuckDB 1.5.5
    turns a torn trailing line — or the merged line a later append leaves behind it — into
    an all-NULL record instead of skipping it, so the view filters ``audit_id IS NOT NULL``
    (every real line carries one). An empty ledger binds as a single ``json`` column, so
    the filter is added only once the ledger holds a parseable record. The EP-35 views
    bind explicit column types instead, so they are typed even over an empty file."""
    from mimicwarehouse import publish
    from mimicwarehouse.engine import open_duckdb
    from mimicwarehouse.run import runs_db_views

    settings = settings or get_settings()
    audit = audit_path(settings)
    audit.parent.mkdir(parents=True, exist_ok=True)
    audit.touch(exist_ok=True)
    dest = runs_db_path(settings)
    dest.parent.mkdir(parents=True, exist_ok=True)
    new = publish.new_path_for(dest)
    publish.unlink(new)
    escaped = audit.resolve().as_posix().replace("'", "''")
    source = f"read_json_auto('{escaped}', format = 'newline_delimited', ignore_errors = true)"
    torn_filter = " WHERE audit_id IS NOT NULL" if _ledger_has_record(audit) else ""
    views = [("audit", f"SELECT * FROM {source}{torn_filter}"), *runs_db_views(settings)]
    assert tuple(name for name, _ in views) == RUNS_DB_VIEWS
    con = open_duckdb("app", database=new, settings=settings)
    try:
        for name, select in views:
            con.execute(f"CREATE VIEW {name} AS {select}")
        con.execute("CHECKPOINT")
    finally:
        con.close()
    publish.swap_file(new, dest, blocked_hint="close it and rerun `mwh runs refresh`")
    return dest


__all__ = [
    "AGGREGATE_FUNCTIONS",
    "ALLOWED_SCHEMAS",
    "AUDIT_FILENAME",
    "COUNT_ALIAS_RE",
    "COUNT_FAMILY_FUNCTIONS",
    "CREDENTIALED_TIERS",
    "ERROR_TEXT_MAX_CHARS",
    "EXIT_REFUSED",
    "EXIT_USAGE",
    "FORBIDDEN_FUNCTION_NAMES",
    "FORBIDDEN_FUNCTION_PREFIXES",
    "FREE_TEXT_MAX_CHARS",
    "K_FLOOR",
    "LABEL_COLUMN_NAMES",
    "REGISTRY_SCHEMAS",
    "REGISTRY_TABLES",
    "RUNS_DB_FILENAME",
    "RUNS_DB_VIEWS",
    "SUPPRESSOR",
    "AuditLine",
    "SafeQueryError",
    "SafeQueryRefused",
    "SafeResult",
    "audit_path",
    "build_runs_db",
    "identifier_column_names",
    "is_registry_ref",
    "rowwise_suppress",
    "runs_db_path",
    "safe_query",
    "sanitize_error_text",
]
