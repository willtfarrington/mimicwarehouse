"""Data-quality profiling — the profile + check engine and its DAG steps (EP-44; DESIGN
§15, GOVERNANCE §4/§5, D-17, D-33, D-40).

Capability 1 (inventory & quality profiling) already has the inventory half (EP-10's raw
manifest, EP-29's ``meta.tables`` / ``meta.columns`` / ``meta.row_counts``). This module is
the quality half: for every table in the tier catalog it computes **aggregates only** —
per-table, per-column and per-check profiles — in DuckDB over the tier's staged lake and
publishes them as ``lake/meta/<tier>/qc_*.parquet`` (EP-29's meta layout, registered as
``meta.qc_*`` by EP-37's discovery walker at catalog time):

``meta.qc_tables``
    one row per table: rows, columns, Parquet bytes, the worst check status, the profile
    step's wall / peak RSS, ``built_at``, run / build / snapshot ids.
``meta.qc_columns``
    one row per column: dtype, the identifier / free-text / dictionary-coded flags,
    ``null_pct`` and ``n_distinct_approx`` **reused from EP-29's profile** (never
    recomputed while a fresh profile exists), ``min_value`` / ``max_value`` (EP-29's
    VARCHAR casts; NULL for identifiers), and ``p01`` / ``p50`` / ``p99`` (DuckDB
    ``approx_quantile``, t-digest) for numeric non-identifier columns.
``meta.qc_topk``
    the top-k ``(value, n)`` pairs of the **dictionary-coded** columns only
    (:func:`dictionary_coded_columns`: a foreign key into a dimension — ``itemid``,
    ``hcpcs_cd`` — or a column declared in ``thresholds.yaml``; never an identifier or a
    free-text column), k-suppressed at build time with ``disclose.suppress`` per column
    (complementary; ``n`` / ``share`` blanked, ``n_suppressed`` marks it).
``meta.qc_checks``
    one row per named check (``CHECK_IDS``): check_id, table, column, itemid, label,
    metric, value, threshold, rule, status ``pass | warn | fail``, ``n_affected`` (blanked
    with ``n_affected_suppressed`` when ``0 < n < k``, its ``value`` blanked beside it),
    detail, tier, build / run ids. Each check is a named function returning an aggregate
    — :func:`check_pk_unique`, :func:`check_natural_key_dupes`, :func:`check_fk_orphans`,
    :func:`check_null_share`, :func:`check_ts_order`, :func:`check_ts_store_lag`,
    :func:`check_event_window`, :func:`check_units` (``unit_consistency`` +
    ``implausible_values`` over the EP-39 catalogue, conversions inlined per itemid —
    never the ``mwh_harmonize`` macro per row, ``docs/gotchas.md`` §1),
    :func:`check_age_cap`, :func:`check_era_coverage` (``timesem.ERAS``) — and no row
    sample is ever stored: ``n_affected`` counts only.

The raw (unsuppressed) ``qc_checks`` / ``qc_topk`` stay under ``lake/meta/<tier>/raw/`` —
a subdirectory the discovery walker never enters and nothing exports (the EP-39 rule,
D-33 addendum).

**Steps** (``dag/specs/qc.yaml``, rendered by :func:`spec_document` — ``python -m
mimicwarehouse.qc``; tag ``qc``): ``qc.profile.<schema>.<table>`` (one per contract table
of the staged schemas: the table's SQL family on the build connection, measured with
:class:`~mimicwarehouse.run.ResourceLog`, written as a JSON slice under
``raw/qc/``), ``qc.checks`` (reads every slice, evaluates the thresholds, assembles and
suppresses the four tables and writes them inside a ``run.start(kind="qc")`` run with one
``run.bench(kind="query")`` line per profiled table — ``mwh runs benchmarks --kind
query``), ``qc.report`` (:mod:`mimicwarehouse.qc.report`) and the shared ``catalog`` step.
Thresholds live in ``thresholds.yaml`` and are applied by ``qc.checks``, so tuning them
needs ``--select qc.checks,qc.report,catalog``, not a re-scan.

Everything computed, written, logged or returned is counts, shares, dictionary values,
schema text and timings — never a row (GOVERNANCE §4). Import budget: stdlib, yaml and
pydantic at import time; duckdb / polars / the contract / ``run`` / ``safe`` load inside
function bodies (the catalog builder imports this module for its extension).
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import cache
from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

if TYPE_CHECKING:  # pragma: no cover
    import duckdb
    import polars

    from mimicwarehouse.dag.runner import StepContext, StepOutcome
    from mimicwarehouse.dag.spec import Step
    from mimicwarehouse.schema.contract import Contract, Table
    from mimicwarehouse.units import ItemCatalogue

_LOG = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------

THRESHOLDS_FILENAME = "thresholds.yaml"
#: The four ``lake/meta/<tier>/<table>.parquet`` files -> ``meta.<table>`` (EP-37 walker).
TABLES_TABLE = "qc_tables"
COLUMNS_TABLE = "qc_columns"
TOPK_TABLE = "qc_topk"
CHECKS_TABLE = "qc_checks"
META_TABLES: tuple[str, ...] = (TABLES_TABLE, COLUMNS_TABLE, TOPK_TABLE, CHECKS_TABLE)
#: The tables whose raw (unsuppressed) twin lives under ``meta/<tier>/raw/``.
RAW_TABLES: tuple[str, ...] = (TOPK_TABLE, CHECKS_TABLE)
#: Per-table slices: ``meta/<tier>/raw/qc/<schema>.<table>.json``.
SLICES_DIRNAME = "qc"
#: The DAG step names (``dag/specs/qc.yaml``) and the tag.
STEP_PREFIX = "qc.profile."
STEP_CHECKS = "qc.checks"
STEP_REPORT = "qc.report"
DAG_TAG = "qc"
#: The provenance run every ``qc.checks`` opens (EP-35 ``RunKind``) and its benchmark kind.
RUN_NAME = "qc"
RUN_KIND = "qc"
BENCH_KIND = "query"
#: The claim-type label the qc run and its report carry (GOVERNANCE §7).
CLAIM_TYPE = "exploratory (data-quality profile)"
#: Check statuses, worst last.
STATUSES: tuple[str, ...] = ("pass", "warn", "fail")
Status = Literal["pass", "warn", "fail"]
#: Every check the thresholds document must name (one function each, module docstring).
CHECK_IDS: tuple[str, ...] = (
    "pk_unique",
    "natural_key_dupes",
    "fk_orphans",
    "null_share",
    "ts_order",
    "ts_store_lag",
    "event_window",
    "unit_consistency",
    "implausible_values",
    "age_cap",
    "era_coverage",
)
#: The quantiles ``meta.qc_columns`` carries for numeric non-identifier columns.
QUANTILES: tuple[float, ...] = (0.01, 0.5, 0.99)
QUANTILE_COLUMNS: tuple[str, ...] = ("p01", "p50", "p99")
#: Longest string any QC table / report cell may carry (the run-folder rule,
#: ``docs/committed-text.md`` rule 4; mirrors ``safe.FREE_TEXT_MAX_CHARS``).
VALUE_MAX_CHARS = 64
#: DuckDB numeric types that get quantiles (DECIMAL(p,s) by prefix).
_NUMERIC_TYPES: frozenset[str] = frozenset({"INTEGER", "SMALLINT", "BIGINT", "DOUBLE", "FLOAT"})
#: How a NULL / blank unit string reads in ``detail`` cells (the EP-39 spelling).
NULL_UNIT_LABEL = "(null)"


class QcError(RuntimeError):
    """The thresholds document is malformed, or a QC step cannot run (unstaged table,
    missing / stale EP-29 profile, missing slice)."""


# ---------------------------------------------------------------------------
# Thresholds (package data)
# ---------------------------------------------------------------------------


class Threshold(BaseModel):
    """One check's rule: a metric name and strict ``above`` / ``below`` bounds for
    ``warn`` and ``fail``; no bound at all = informational (always ``pass``)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    metric: str = Field(min_length=1)
    warn_above: float | None = None
    fail_above: float | None = None
    warn_below: float | None = None
    fail_below: float | None = None
    note: str = ""

    @model_validator(mode="after")
    def _check(self) -> Threshold:
        above = (self.warn_above, self.fail_above)
        if None not in above and self.fail_above < self.warn_above:  # type: ignore[operator]
            raise ValueError(f"metric {self.metric}: fail_above must be >= warn_above")
        below = (self.warn_below, self.fail_below)
        if None not in below and self.fail_below > self.warn_below:  # type: ignore[operator]
            raise ValueError(f"metric {self.metric}: fail_below must be <= warn_below")
        return self

    @property
    def informational(self) -> bool:
        return all(
            b is None for b in (self.warn_above, self.fail_above, self.warn_below, self.fail_below)
        )

    def evaluate(self, value: float | None) -> tuple[Status, float | None]:
        """``(status, threshold)`` of a metric value: ``fail`` beats ``warn``; a NULL value
        (no denominator) passes; the threshold returned is the bound that decided, or
        the first bound the value stayed within."""
        if value is None or (isinstance(value, float) and math.isnan(value)):
            return "pass", None
        if self.fail_above is not None and value > self.fail_above:
            return "fail", self.fail_above
        if self.fail_below is not None and value < self.fail_below:
            return "fail", self.fail_below
        if self.warn_above is not None and value > self.warn_above:
            return "warn", self.warn_above
        if self.warn_below is not None and value < self.warn_below:
            return "warn", self.warn_below
        for bound in (self.warn_above, self.warn_below, self.fail_above, self.fail_below):
            if bound is not None:
                return "pass", bound
        return "pass", None

    def rule(self) -> str:
        """``warn > 0; fail > 0.01`` / ``warn < 0.95`` / ``informational`` (the
        ``meta.qc_checks.rule`` cell)."""
        parts: list[str] = []
        if self.warn_above is not None:
            parts.append(f"warn > {_fmt_bound(self.warn_above)}")
        if self.warn_below is not None:
            parts.append(f"warn < {_fmt_bound(self.warn_below)}")
        if self.fail_above is not None:
            parts.append(f"fail > {_fmt_bound(self.fail_above)}")
        if self.fail_below is not None:
            parts.append(f"fail < {_fmt_bound(self.fail_below)}")
        return "; ".join(parts) if parts else "informational"


def _fmt_bound(value: float) -> str:
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return text if text not in ("", "-0") else "0"


class OrderRule(BaseModel):
    """``earlier <= later`` on one table (a violation is ``later < earlier``). A rule may
    override the check's ``warn_above`` / ``fail_above`` with a ``note`` saying why — the
    medication-order pairs, where a discontinued order's ``stoptime`` legitimately
    precedes its ``starttime`` (a MIMIC-IV fact, not a warehouse defect)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    table: str
    earlier: str
    later: str
    warn_above: float | None = None
    fail_above: float | None = None
    note: str = ""

    @property
    def overrides(self) -> bool:
        return self.warn_above is not None or self.fail_above is not None

    @model_validator(mode="after")
    def _check(self) -> OrderRule:
        if self.overrides and not self.note.strip():
            raise ValueError(
                f"{self.table}: {self.earlier} <= {self.later}: an override needs a note"
            )
        return self


class WindowTable(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    table: str
    time: str
    key: str = "stay_id"


class EventWindow(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    slack_hours: int = Field(ge=0)
    stay_table: str
    intime: str = "intime"
    outtime: str = "outtime"
    key: str = "stay_id"
    tables: tuple[WindowTable, ...] = ()


class Thresholds(BaseModel):
    """The validated ``thresholds.yaml`` (module docstring)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int = Field(ge=1)
    version_note: str = ""
    top_k: int = Field(default=10, ge=1)
    checks: dict[str, Threshold]
    dictionary_coded: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    timestamp_order: tuple[OrderRule, ...] = ()
    store_lag: tuple[OrderRule, ...] = ()
    event_window: EventWindow

    @model_validator(mode="after")
    def _check(self) -> Thresholds:
        missing = sorted(set(CHECK_IDS) - set(self.checks))
        if missing:
            raise ValueError(f"checks: missing {missing}")
        unknown = sorted(set(self.checks) - set(CHECK_IDS))
        if unknown:
            raise ValueError(f"checks: unknown check id(s) {unknown}; known: {list(CHECK_IDS)}")
        return self

    def check(self, check_id: str) -> Threshold:
        return self.checks[check_id]

    def order_rules(self, qualified: str) -> tuple[OrderRule, ...]:
        return tuple(r for r in self.timestamp_order if r.table == qualified)

    def order_rule(self, qualified: str, later: str) -> OrderRule | None:
        """The ``timestamp_order`` rule of ``(table, later column)``, if any."""
        for r in self.timestamp_order:
            if r.table == qualified and r.later == later:
                return r
        return None

    def effective(self, check_id: str, table: str, column: str | None) -> Threshold:
        """The threshold a check row is evaluated against: the check's, with a
        ``timestamp_order`` rule's overrides applied for ``ts_order`` rows."""
        base = self.check(check_id)
        if check_id != "ts_order" or column is None:
            return base
        rule = self.order_rule(table, column)
        if rule is None or not rule.overrides:
            return base
        update: dict[str, Any] = {"note": rule.note or base.note}
        if rule.warn_above is not None:
            update["warn_above"] = rule.warn_above
        if rule.fail_above is not None:
            update["fail_above"] = rule.fail_above
        return base.model_copy(update=update)

    def store_lag_rules(self, qualified: str) -> tuple[OrderRule, ...]:
        return tuple(r for r in self.store_lag if r.table == qualified)

    def window_rule(self, qualified: str) -> WindowTable | None:
        for w in self.event_window.tables:
            if w.table == qualified:
                return w
        return None


def thresholds_path() -> Path:
    """``src/mimicwarehouse/qc/thresholds.yaml`` inside the installed package."""
    return Path(str(files("mimicwarehouse.qc").joinpath(THRESHOLDS_FILENAME)))


def load_thresholds_from(path: Path) -> Thresholds:
    """Parse and validate one thresholds YAML (tests point this at crafted files);
    :class:`QcError` names every validation problem."""
    path = Path(path)
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise QcError(f"{path.name}: cannot read ({exc})") from exc
    if not isinstance(doc, dict):
        raise QcError(f"{path.name}: top level must be a mapping")
    try:
        return Thresholds.model_validate(doc)
    except ValidationError as exc:
        lines = [f"{path.name}: {exc.error_count()} validation error(s)"]
        for e in exc.errors():
            loc = ".".join(str(p) for p in e["loc"])
            lines.append(f"  {loc}: {e['msg']}")
        raise QcError("\n".join(lines)) from None


@cache
def load_thresholds() -> Thresholds:
    """The packaged thresholds (validated once per process)."""
    return load_thresholds_from(thresholds_path())


def thresholds_sha256() -> str:
    """sha256 of the packaged file's bytes — the ``qc_thresholds`` ref every QC run cites."""
    return hashlib.sha256(thresholds_path().read_bytes()).hexdigest()


def validate_thresholds(thresholds: Thresholds, contract: Contract | None = None) -> None:
    """Every table / column the document names exists in the contract; declared
    dictionary-coded columns are neither identifiers nor free text; ordering rules name
    TIMESTAMP / DATE columns. :class:`QcError` listing every problem."""
    from mimicwarehouse.schema.contract import TIME_TYPES, load_contract

    contract = contract or load_contract()
    problems: list[str] = []

    def column_of(qualified: str, name: str, where: str) -> Any:
        if not contract.has_table(qualified):
            problems.append(f"{where}: unknown table {qualified}")
            return None
        table = contract.table(qualified)
        if not table.has_column(name):
            problems.append(f"{where}: {qualified} has no column {name!r}")
            return None
        return table.column(name)

    for qualified, columns in thresholds.dictionary_coded.items():
        for name in columns:
            column = column_of(qualified, name, "dictionary_coded")
            if column is None:
                continue
            if column.identifier:
                problems.append(f"dictionary_coded: {qualified}.{name} is an identifier column")
            if column.free_text:
                problems.append(f"dictionary_coded: {qualified}.{name} is a free-text column")
    for label, rules in (
        ("timestamp_order", thresholds.timestamp_order),
        ("store_lag", thresholds.store_lag),
    ):
        for rule in rules:
            for name in (rule.earlier, rule.later):
                column = column_of(rule.table, name, label)
                if column is not None and column.duckdb_type not in TIME_TYPES:
                    problems.append(f"{label}: {rule.table}.{name} is not a TIMESTAMP/DATE column")
    window = thresholds.event_window
    for name in (window.key, window.intime, window.outtime):
        column_of(window.stay_table, name, "event_window.stay_table")
    for w in window.tables:
        column_of(w.table, w.key, "event_window")
        column = column_of(w.table, w.time, "event_window")
        if column is not None and column.duckdb_type not in TIME_TYPES:
            problems.append(f"event_window: {w.table}.{w.time} is not a TIMESTAMP/DATE column")
    if problems:
        raise QcError(f"{THRESHOLDS_FILENAME}: " + "; ".join(problems))


def dictionary_coded_columns(
    contract: Contract | None = None, thresholds: Thresholds | None = None
) -> dict[str, tuple[str, ...]]:
    """``{schema.table: (columns, ...)}`` whose top-k values ``meta.qc_topk`` may hold
    (module docstring): every foreign-key column into a dimension table (``itemid``,
    ``hcpcs_cd``; ``keys.yaml``) plus the declared list — never an identifier or a
    free-text column, in contract column order."""
    from mimicwarehouse.schema.contract import load_contract

    contract = contract or load_contract()
    thresholds = thresholds or load_thresholds()
    picked: dict[str, set[str]] = {}
    for fk in contract.foreign_keys:
        if not contract.has_table(fk.table) or not contract.has_table(fk.ref_table):
            continue
        if not contract.table(fk.ref_table).is_dim:
            continue
        table = contract.table(fk.table)
        for name in fk.columns:
            column = table.column(name)
            if column.identifier or column.free_text:
                continue
            picked.setdefault(fk.table, set()).add(name)
    for qualified, columns in thresholds.dictionary_coded.items():
        if not contract.has_table(qualified):
            continue
        table = contract.table(qualified)
        for name in columns:
            if table.has_column(name):
                column = table.column(name)
                if not (column.identifier or column.free_text):
                    picked.setdefault(qualified, set()).add(name)
    out: dict[str, tuple[str, ...]] = {}
    for qualified, names in picked.items():
        order = contract.table(qualified).column_names
        out[qualified] = tuple(c for c in order if c in names)
    return dict(sorted(out.items()))


# ---------------------------------------------------------------------------
# SQL helpers
# ---------------------------------------------------------------------------


def _sql_str(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def relation(qualified: str) -> str:
    """``schema."table"`` — the view / table name on a build or catalog connection."""
    schema, _, table = qualified.partition(".")
    return f"{schema}.{_q(table)}"


def present_tables(con: duckdb.DuckDBPyConnection) -> set[str]:
    """Every ``schema.table`` (tables and views) visible on ``con``."""
    rows = con.execute(
        "SELECT table_schema || '.' || table_name FROM information_schema.tables"
    ).fetchall()
    return {str(r[0]) for r in rows}


def wants_quantiles(column: Any) -> bool:
    """Numeric, non-identifier: the columns ``p01`` / ``p50`` / ``p99`` are computed for."""
    if column.identifier:
        return False
    return column.duckdb_type in _NUMERIC_TYPES or column.duckdb_type.startswith("DECIMAL(")


def _clip(text: str | None, limit: int = VALUE_MAX_CHARS) -> str | None:
    if text is None:
        return None
    return text if len(text) <= limit else text[: limit - 3] + "..."


# ---------------------------------------------------------------------------
# Rows
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CheckRow:
    """One check result before threshold evaluation (module docstring): a metric value
    and the count of affected rows — never a sample."""

    check_id: str
    table: str
    column: str | None = None
    itemid: int | None = None
    label: str | None = None
    metric: str = ""
    value: float | None = None
    n_affected: int | None = None
    detail: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "table": self.table,
            "column": self.column,
            "itemid": self.itemid,
            "label": _clip(self.label),
            "metric": self.metric,
            "value": self.value,
            "n_affected": self.n_affected,
            "detail": _clip(self.detail),
        }


@dataclass(slots=True)
class TableProfile:
    """What :func:`profile_table` produced for one table (counts and aggregates only)."""

    table: str
    rows: int
    columns: list[dict[str, Any]] = field(default_factory=list)
    topk: list[dict[str, Any]] = field(default_factory=list)
    checks: list[CheckRow] = field(default_factory=list)
    parquet_bytes: int | None = None
    files: int | None = None
    wall_s: float | None = None
    peak_rss_mb: float | None = None
    disk_delta_mb: float | None = None
    build_id: str | None = None
    snapshot_id: str | None = None
    built_at: str | None = None

    @property
    def schema_name(self) -> str:
        return self.table.partition(".")[0]

    @property
    def name(self) -> str:
        return self.table.partition(".")[2]

    def to_dict(self) -> dict[str, Any]:
        return {
            "table": self.table,
            "rows": self.rows,
            "parquet_bytes": self.parquet_bytes,
            "files": self.files,
            "wall_s": self.wall_s,
            "peak_rss_mb": self.peak_rss_mb,
            "disk_delta_mb": self.disk_delta_mb,
            "build_id": self.build_id,
            "snapshot_id": self.snapshot_id,
            "built_at": self.built_at,
            "columns": self.columns,
            "topk": self.topk,
            "checks": [c.as_dict() for c in self.checks],
        }

    @classmethod
    def from_dict(cls, doc: Mapping[str, Any]) -> TableProfile:
        return cls(
            table=str(doc["table"]),
            rows=int(doc["rows"]),
            columns=list(doc.get("columns") or []),
            topk=list(doc.get("topk") or []),
            checks=[CheckRow(**c) for c in doc.get("checks") or []],
            parquet_bytes=doc.get("parquet_bytes", doc.get("bytes_parquet")),
            files=doc.get("files"),
            wall_s=doc.get("wall_s"),
            peak_rss_mb=doc.get("peak_rss_mb"),
            disk_delta_mb=doc.get("disk_delta_mb"),
            build_id=doc.get("build_id"),
            snapshot_id=doc.get("snapshot_id"),
            built_at=doc.get("built_at"),
        )


#: What the EP-29 profile supplies per column: ``(null_pct, approx_distinct, min, max)``.
Ep29Column = tuple[float | None, int | None, str | None, str | None]


# ---------------------------------------------------------------------------
# The per-table profile (columns + top-k)
# ---------------------------------------------------------------------------


def profile_sql(
    table: Table, *, dict_columns: Iterable[str] = (), basics: bool = False
) -> tuple[str, list[tuple[str, str]]]:
    """The one aggregate SELECT of a table and the decode plan ``[(kind, column)]``:
    ``count(*)`` first; with ``basics`` (no EP-29 profile to reuse) per-column ``count`` /
    ``approx_count_distinct`` / VARCHAR ``min`` / ``max`` (:func:`wants_minmax`); a
    non-null count per dictionary-coded column (the top-k shares); ``approx_quantile``
    over the numeric non-identifier columns that are not dictionary-coded."""
    from mimicwarehouse.catalog.profile import wants_minmax

    dict_set = set(dict_columns)
    parts = ["count(*)"]
    plan: list[tuple[str, str]] = [("rows", "")]
    quantiles = ", ".join(str(q) for q in QUANTILES)
    for c in table.columns:
        q = _q(c.name)
        if basics:
            parts += [f"count({q})", f"approx_count_distinct({q})"]
            plan += [("nn", c.name), ("nd", c.name)]
            if wants_minmax(c):
                parts += [f"CAST(min({q}) AS VARCHAR)", f"CAST(max({q}) AS VARCHAR)"]
                plan += [("min", c.name), ("max", c.name)]
        elif c.name in dict_set:
            parts.append(f"count({q})")
            plan.append(("nn", c.name))
        if wants_quantiles(c) and c.name not in dict_set:
            parts.append(f"approx_quantile(CAST({q} AS DOUBLE), [{quantiles}])")
            plan.append(("q", c.name))
    return f"SELECT {', '.join(parts)} FROM {relation(table.qualified_name)}", plan


def topk_sql(table: Table, column: str, top_k: int) -> str:
    """The top-k ``(value, n)`` of one dictionary-coded column (ties broken by value;
    values clipped to :data:`VALUE_MAX_CHARS`)."""
    q = _q(column)
    return (
        f"SELECT substr(v, 1, {VALUE_MAX_CHARS}) AS value, n FROM ("
        f"SELECT CAST({q} AS VARCHAR) AS v, count(*) AS n FROM {relation(table.qualified_name)} "
        f"WHERE {q} IS NOT NULL GROUP BY 1 ORDER BY n DESC, v LIMIT {int(top_k)})"
    )


def _profile_columns(
    con: duckdb.DuckDBPyConnection,
    table: Table,
    *,
    dict_columns: tuple[str, ...],
    ep29: Mapping[str, Ep29Column] | None,
) -> tuple[int, list[dict[str, Any]], dict[str, int]]:
    """``(rows, column rows, non-null counts of the dictionary-coded columns)``."""
    sql, plan = profile_sql(table, dict_columns=dict_columns, basics=ep29 is None)
    row = con.execute(sql).fetchone()
    assert row is not None
    decoded: dict[tuple[str, str], Any] = dict(zip(plan, row, strict=True))
    rows = int(decoded[("rows", "")])
    non_null: dict[str, int] = {}
    out: list[dict[str, Any]] = []
    for ordinal, c in enumerate(table.columns, start=1):
        if ep29 is not None:
            null_pct, distinct, min_value, max_value = ep29.get(c.name, (None, None, None, None))
        else:
            nn = int(decoded[("nn", c.name)])
            null_pct = None if rows == 0 else round(1.0 - nn / rows, 6)
            distinct = int(decoded[("nd", c.name)])
            min_value = decoded.get(("min", c.name))
            max_value = decoded.get(("max", c.name))
            non_null[c.name] = nn
        if ("nn", c.name) in decoded:
            non_null[c.name] = int(decoded[("nn", c.name)])
        quantiles: list[float | None] = [None] * len(QUANTILES)
        raw_q = decoded.get(("q", c.name))
        if raw_q is not None:
            quantiles = [None if v is None else float(v) for v in raw_q]
        out.append(
            {
                "column": c.name,
                "ordinal": ordinal,
                "dtype": c.duckdb_type,
                "is_identifier": bool(c.identifier),
                "is_free_text": bool(c.free_text),
                "is_dictionary_coded": c.name in dict_columns,
                "null_pct": None if null_pct is None else float(null_pct),
                "n_distinct_approx": None if distinct is None else int(distinct),
                "min_value": None if c.identifier else _clip(min_value),
                "max_value": None if c.identifier else _clip(max_value),
                **dict(zip(QUANTILE_COLUMNS, quantiles, strict=True)),
            }
        )
    return rows, out, non_null


def _profile_topk(
    con: duckdb.DuckDBPyConnection,
    table: Table,
    dict_columns: tuple[str, ...],
    non_null: Mapping[str, int],
    top_k: int,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for name in dict_columns:
        denominator = non_null.get(name)
        for rank, (value, n) in enumerate(con.execute(topk_sql(table, name, top_k)).fetchall(), 1):
            count = int(n)
            out.append(
                {
                    "column": name,
                    "rank": rank,
                    "value": None if value is None else str(value),
                    "n": count,
                    "share": None if not denominator else count / denominator,
                }
            )
    return out


# ---------------------------------------------------------------------------
# The checks — named functions, each returning aggregates
# ---------------------------------------------------------------------------


def _surplus_rows(con: duckdb.DuckDBPyConnection, table: Table, keys: Sequence[str]) -> int:
    key_list = ", ".join(_q(k) for k in keys)
    sql = (
        "SELECT CAST(coalesce(sum(c - 1), 0) AS BIGINT) FROM "
        f"(SELECT count(*) AS c FROM {relation(table.qualified_name)} GROUP BY {key_list})"
    )
    row = con.execute(sql).fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def check_pk_unique(con: duckdb.DuckDBPyConnection, table: Table) -> list[CheckRow]:
    """``pk_unique``: rows beyond the first per declared primary key (none = no row)."""
    if not table.primary_key:
        return []
    surplus = _surplus_rows(con, table, table.primary_key)
    return [
        CheckRow(
            "pk_unique",
            table.qualified_name,
            column=table.primary_key[0],
            metric="duplicate_rows",
            value=float(surplus),
            n_affected=surplus,
            detail="key: " + ", ".join(table.primary_key),
        )
    ]


def check_natural_key_dupes(con: duckdb.DuckDBPyConnection, table: Table) -> list[CheckRow]:
    """``natural_key_dupes``: rows beyond the first per ``uniqueness_hint`` key (tables
    without an upstream primary key; chartevents' upstream duplicates are a MIMIC fact)."""
    if not table.uniqueness_hint:
        return []
    surplus = _surplus_rows(con, table, table.uniqueness_hint)
    return [
        CheckRow(
            "natural_key_dupes",
            table.qualified_name,
            column=table.uniqueness_hint[0],
            metric="duplicate_rows",
            value=float(surplus),
            n_affected=surplus,
            detail="hint key: " + ", ".join(table.uniqueness_hint),
        )
    ]


def fk_orphans_sql(table: Table, fks: Sequence[Any]) -> str:
    """One scan of ``table`` LEFT JOINed to the DISTINCT keys of every referenced table:
    per foreign key the non-null count and the orphan count."""
    t = relation(table.qualified_name)
    parts: list[str] = []
    joins: list[str] = []
    for i, fk in enumerate(fks):
        col = _q(fk.columns[0])
        ref_col = _q(fk.ref_columns[0])
        parts.append(f"count(t.{col})")
        parts.append(f"count(*) FILTER (WHERE t.{col} IS NOT NULL AND r{i}.k IS NULL)")
        joins.append(
            f"LEFT JOIN (SELECT DISTINCT {ref_col} AS k FROM {relation(fk.ref_table)} "
            f"WHERE {ref_col} IS NOT NULL) AS r{i} ON t.{col} = r{i}.k"
        )
    return f"SELECT {', '.join(parts)} FROM {t} AS t " + " ".join(joins)


def check_fk_orphans(
    con: duckdb.DuckDBPyConnection,
    table: Table,
    contract: Contract,
    present: set[str] | None = None,
) -> list[CheckRow]:
    """``fk_orphans``: per ``keys.yaml`` foreign key of ``table`` whose referenced table
    is present on ``con`` — the share of non-null key values without a match."""
    have = present if present is not None else present_tables(con)
    fks = [
        fk
        for fk in contract.foreign_keys_of(table)
        if fk.ref_table in have and len(fk.columns) == 1
    ]
    if not fks:
        return []
    row = con.execute(fk_orphans_sql(table, fks)).fetchone()
    assert row is not None
    out: list[CheckRow] = []
    for i, fk in enumerate(fks):
        non_null, orphans = int(row[2 * i]), int(row[2 * i + 1])
        out.append(
            CheckRow(
                "fk_orphans",
                table.qualified_name,
                column=fk.columns[0],
                metric="orphan_share",
                value=None if non_null == 0 else orphans / non_null,
                n_affected=orphans,
                detail=fk.name or f"-> {fk.ref_table}",
            )
        )
    return out


def check_null_share(
    table: Table, rows: int, columns: Sequence[Mapping[str, Any]]
) -> list[CheckRow]:
    """``null_share``: one row per column from the profiled ``null_pct`` (EP-29's
    aggregate); ``n_affected`` is the null count it implies."""
    out: list[CheckRow] = []
    for c in columns:
        null_pct = c.get("null_pct")
        n_null = None if null_pct is None else round(float(null_pct) * rows)
        out.append(
            CheckRow(
                "null_share",
                table.qualified_name,
                column=str(c["column"]),
                metric="null_share",
                value=None if null_pct is None else float(null_pct),
                n_affected=n_null,
            )
        )
    return out


def order_sql(table: Table, rules: Sequence[OrderRule]) -> str:
    parts: list[str] = []
    for r in rules:
        a, b = _q(r.earlier), _q(r.later)
        parts.append(f"count(*) FILTER (WHERE {a} IS NOT NULL AND {b} IS NOT NULL)")
        parts.append(f"count(*) FILTER (WHERE {b} < {a})")
    return f"SELECT {', '.join(parts)} FROM {relation(table.qualified_name)}"


def _order_rows(
    con: duckdb.DuckDBPyConnection, table: Table, rules: Sequence[OrderRule], check_id: str
) -> list[CheckRow]:
    if not rules:
        return []
    row = con.execute(order_sql(table, rules)).fetchone()
    assert row is not None
    out: list[CheckRow] = []
    for i, r in enumerate(rules):
        n_both, n_bad = int(row[2 * i]), int(row[2 * i + 1])
        out.append(
            CheckRow(
                check_id,
                table.qualified_name,
                column=r.later,
                metric="violation_share",
                value=None if n_both == 0 else n_bad / n_both,
                n_affected=n_bad,
                detail=f"{r.earlier} <= {r.later}",
            )
        )
    return out


def check_ts_order(
    con: duckdb.DuckDBPyConnection, table: Table, thresholds: Thresholds
) -> list[CheckRow]:
    """``ts_order``: per ``timestamp_order`` rule of the table, the share of rows with
    ``later < earlier`` among rows carrying both."""
    return _order_rows(con, table, thresholds.order_rules(table.qualified_name), "ts_order")


def check_ts_store_lag(
    con: duckdb.DuckDBPyConnection, table: Table, thresholds: Thresholds
) -> list[CheckRow]:
    """``ts_store_lag``: the ``storetime < charttime`` rate (``store_lag`` rules)."""
    return _order_rows(con, table, thresholds.store_lag_rules(table.qualified_name), "ts_store_lag")


def window_sql(table: Table, rule: WindowTable, window: EventWindow) -> str:
    t = relation(table.qualified_name)
    s = relation(window.stay_table)
    key, time_col = _q(rule.key), _q(rule.time)
    skey, intime, outtime = _q(window.key), _q(window.intime), _q(window.outtime)
    slack = f"INTERVAL {int(window.slack_hours)} HOUR"
    outside = f"t.{time_col} < s.lo OR t.{time_col} > s.hi"
    return (
        "SELECT count(*) FILTER (WHERE s.lo IS NOT NULL), "
        f"count(*) FILTER (WHERE s.lo IS NOT NULL AND ({outside})) "
        f"FROM {t} AS t LEFT JOIN (SELECT {skey} AS k, min({intime}) - {slack} AS lo, "
        f"max({outtime}) + {slack} AS hi FROM {s} WHERE {intime} IS NOT NULL AND "
        f"{outtime} IS NOT NULL GROUP BY 1) AS s ON t.{key} = s.k WHERE t.{time_col} IS NOT NULL"
    )


def check_event_window(
    con: duckdb.DuckDBPyConnection,
    table: Table,
    thresholds: Thresholds,
    present: set[str] | None = None,
) -> list[CheckRow]:
    """``event_window``: the share of the table's events outside ``[intime - slack,
    outtime + slack]`` of the stay they carry, among events joined to a stay with both
    bounds."""
    rule = thresholds.window_rule(table.qualified_name)
    window = thresholds.event_window
    have = present if present is not None else present_tables(con)
    if rule is None or window.stay_table not in have:
        return []
    row = con.execute(window_sql(table, rule, window)).fetchone()
    assert row is not None
    n_scope, n_out = int(row[0]), int(row[1])
    slack_h = window.slack_hours
    return [
        CheckRow(
            "event_window",
            table.qualified_name,
            column=rule.time,
            metric="outside_share",
            value=None if n_scope == 0 else n_out / n_scope,
            n_affected=n_out,
            detail=f"{rule.time} in [intime - {slack_h} h, outtime + {slack_h} h]",
        )
    ]


def units_sql(table: Table, source: str, catalogue: ItemCatalogue) -> str:
    """The per-``(itemid, unit_norm)`` aggregate over the curated itemids of one EP-39
    source table: rows, valued rows and rows outside the plausibility bounds after the
    catalogue's conversion, **inlined** as one ``CASE`` per itemid (``docs/gotchas.md``
    §1: the ``mwh_harmonize`` macro is for point lookups, never a scan)."""
    from mimicwarehouse.units import SOURCE_COLUMNS, sql_normalize_unit

    _schema, _name, value_col, unit_col = SOURCE_COLUMNS[source]
    ids = catalogue.itemids(source)
    canon_arms: list[str] = []
    lo_arms: list[str] = []
    hi_arms: list[str] = []
    for itemid in ids:
        item = catalogue.spec(itemid)
        branches = [
            f"WHEN {_sql_str(norm)} THEN {affine.sql('v')}"
            for norm, affine in sorted(item.conversions().items())
            if not affine.is_identity
        ]
        canon = f"(CASE unit_norm {' '.join(branches)} ELSE v END)" if branches else "v"
        canon_arms.append(f"WHEN {itemid} THEN {canon}")
        lo_arms.append(f"WHEN {itemid} THEN {item.plausible_low!r}")
        hi_arms.append(f"WHEN {itemid} THEN {item.plausible_high!r}")
    id_list = ", ".join(str(i) for i in ids)
    inner = (
        f"SELECT itemid, {sql_normalize_unit(_q(unit_col))} AS unit_norm, "
        f"CAST({_q(value_col)} AS DOUBLE) AS v FROM {relation(table.qualified_name)} "
        f"WHERE itemid IN ({id_list})"
    )
    middle = (
        f"SELECT itemid, unit_norm, v, CASE itemid {' '.join(canon_arms)} END AS canon, "
        f"CASE itemid {' '.join(lo_arms)} END AS lo, CASE itemid {' '.join(hi_arms)} END AS hi "
        f"FROM ({inner})"
    )
    return (
        "SELECT itemid, unit_norm, count(*) AS n_rows, count(v) AS n_valued, "
        "count(*) FILTER (WHERE canon IS NOT NULL AND NOT (canon BETWEEN lo AND hi)) "
        f"AS n_implausible FROM ({middle}) GROUP BY 1, 2 ORDER BY 1, 2"
    )


def check_units(
    con: duckdb.DuckDBPyConnection, table: Table, catalogue: ItemCatalogue | None = None
) -> list[CheckRow]:
    """``unit_consistency`` (share of a curated itemid's rows in its dominant normalised
    unit string; ``n_affected`` = rows outside it) and ``implausible_values`` (share of
    its valued rows outside the EP-39 bounds after harmonisation) — one row each per
    curated itemid present in an EP-39 source table (``chartevents`` / ``labevents`` /
    ``outputevents`` / ``inputevents``)."""
    from mimicwarehouse.units import SOURCE_COLUMNS, load_catalogue

    source = next(
        (
            s
            for s, (schema, name, _v, _u) in SOURCE_COLUMNS.items()
            if f"{schema}.{name}" == table.qualified_name
        ),
        None,
    )
    if source is None:
        return []
    cat = catalogue or load_catalogue()
    if not cat.itemids(source):
        return []
    _schema, _name, value_col, unit_col = SOURCE_COLUMNS[source]
    per_item: dict[int, list[tuple[str, int, int, int]]] = {}
    for itemid, unit_norm, n_rows, n_valued, n_bad in con.execute(
        units_sql(table, source, cat)
    ).fetchall():
        per_item.setdefault(int(itemid), []).append(
            ("" if unit_norm is None else str(unit_norm), int(n_rows), int(n_valued), int(n_bad))
        )
    out: list[CheckRow] = []
    for itemid in sorted(per_item):
        item = cat.spec(itemid)
        variants = per_item[itemid]
        total = sum(v[1] for v in variants)
        dominant = max(variants, key=lambda v: (v[1], v[0] != "", v[0]))
        n_units = len(variants)
        out.append(
            CheckRow(
                "unit_consistency",
                table.qualified_name,
                column=unit_col,
                itemid=itemid,
                label=item.label,
                metric="dominant_unit_share",
                value=None if total == 0 else dominant[1] / total,
                n_affected=total - dominant[1],
                detail=f"dominant: {dominant[0] or NULL_UNIT_LABEL}; {n_units} unit string(s)",
            )
        )
        valued = sum(v[2] for v in variants)
        implausible = sum(v[3] for v in variants)
        out.append(
            CheckRow(
                "implausible_values",
                table.qualified_name,
                column=value_col,
                itemid=itemid,
                label=item.label,
                metric="implausible_share",
                value=None if valued == 0 else implausible / valued,
                n_affected=implausible,
                detail=(
                    f"bounds {_fmt_bound(item.plausible_low)}..{_fmt_bound(item.plausible_high)} "
                    f"{item.canonical_unit}"
                ),
            )
        )
    return out


def check_age_cap(con: duckdb.DuckDBPyConnection, table: Table) -> list[CheckRow]:
    """``age_cap``: the share of patients at the 91 cap (informational; ``patients``)."""
    if table.qualified_name != "mimiciv_hosp.patients":
        return []
    from mimicwarehouse.timesem import AGE_CAP

    row = con.execute(
        f"SELECT count(*) FILTER (WHERE anchor_age >= {int(AGE_CAP)}), count(anchor_age) "
        f"FROM {relation(table.qualified_name)}"
    ).fetchone()
    assert row is not None
    capped, n_aged = int(row[0]), int(row[1])
    return [
        CheckRow(
            "age_cap",
            table.qualified_name,
            column="anchor_age",
            metric="capped_share",
            value=None if n_aged == 0 else capped / n_aged,
            n_affected=capped,
            detail=f"anchor_age >= {int(AGE_CAP)} (ages >= 89 are shipped as {int(AGE_CAP)})",
        )
    ]


def check_era_coverage(con: duckdb.DuckDBPyConnection, table: Table) -> list[CheckRow]:
    """``era_coverage``: the ``timesem.ERAS`` labels with no patient row (``patients``)."""
    if table.qualified_name != "mimiciv_hosp.patients":
        return []
    from mimicwarehouse.timesem import ERAS

    counts = {
        str(label): int(n)
        for label, n in con.execute(
            f"SELECT anchor_year_group, count(*) FROM {relation(table.qualified_name)} "
            "WHERE anchor_year_group IS NOT NULL GROUP BY 1"
        ).fetchall()
    }
    empty = [era for era in ERAS if counts.get(era, 0) == 0]
    unknown = sorted(set(counts) - set(ERAS))
    detail = f"{len(ERAS)} eras expected"
    if empty:
        detail += "; empty: " + ", ".join(empty)
    if unknown:
        detail += f"; {len(unknown)} unknown label(s)"
    return [
        CheckRow(
            "era_coverage",
            table.qualified_name,
            column="anchor_year_group",
            metric="empty_eras",
            value=float(len(empty)),
            n_affected=len(empty),
            detail=detail,
        )
    ]


# ---------------------------------------------------------------------------
# profile_table — the whole family for one table
# ---------------------------------------------------------------------------


def profile_table(
    con: duckdb.DuckDBPyConnection,
    table: Table,
    *,
    contract: Contract | None = None,
    thresholds: Thresholds | None = None,
    catalogue: ItemCatalogue | None = None,
    ep29: Mapping[str, Ep29Column] | None = None,
    present: set[str] | None = None,
) -> TableProfile:
    """Every profile and check of one table on ``con`` (module docstring). ``ep29`` is
    the table's EP-29 profile (``null_pct`` / ``approx_distinct`` / ``min`` / ``max`` per
    column) when it exists — the DAG step passes it; without it the basics are computed
    in the same scan (standalone callers, tests)."""
    from mimicwarehouse.schema.contract import load_contract
    from mimicwarehouse.units import load_catalogue

    contract = contract or load_contract()
    thresholds = thresholds or load_thresholds()
    catalogue = catalogue or load_catalogue()
    have = present if present is not None else present_tables(con)
    qn = table.qualified_name
    if qn not in have:
        raise QcError(f"{qn} is not present on the connection (stage it first)")
    dict_columns = dictionary_coded_columns(contract, thresholds).get(qn, ())
    rows, columns, non_null = _profile_columns(con, table, dict_columns=dict_columns, ep29=ep29)
    topk = _profile_topk(con, table, dict_columns, non_null, thresholds.top_k)
    checks: list[CheckRow] = []
    checks += check_pk_unique(con, table)
    checks += check_natural_key_dupes(con, table)
    checks += check_fk_orphans(con, table, contract, have)
    checks += check_null_share(table, rows, columns)
    checks += check_ts_order(con, table, thresholds)
    checks += check_ts_store_lag(con, table, thresholds)
    checks += check_event_window(con, table, thresholds, have)
    checks += check_units(con, table, catalogue)
    checks += check_age_cap(con, table)
    checks += check_era_coverage(con, table)
    return TableProfile(table=qn, rows=rows, columns=columns, topk=topk, checks=checks)


# ---------------------------------------------------------------------------
# Threshold evaluation + assembly
# ---------------------------------------------------------------------------


def evaluate(
    rows: Iterable[CheckRow], thresholds: Thresholds | None = None
) -> list[dict[str, Any]]:
    """The check rows with ``status`` / ``threshold`` / ``rule`` from the thresholds."""
    thresholds = thresholds or load_thresholds()
    out: list[dict[str, Any]] = []
    for r in rows:
        th = thresholds.effective(r.check_id, r.table, r.column)
        status, bound = th.evaluate(r.value)
        d = r.as_dict()
        d["status"] = status
        d["threshold"] = bound
        d["rule"] = th.rule() + (" (rule override)" if th != thresholds.check(r.check_id) else "")
        out.append(d)
    return out


def worst_status(statuses: Iterable[str]) -> str:
    worst = "pass"
    for s in statuses:
        if STATUSES.index(s) > STATUSES.index(worst):
            worst = s
    return worst


def suppress_checks(frame: polars.DataFrame, k: int) -> polars.DataFrame:
    """The published shape of the raw checks frame: ``n_affected`` blanked and
    ``n_affected_suppressed = true`` where ``0 < n < k``, the metric ``value`` blanked
    beside it (a share would restore the count), ``k`` recorded. Primary suppression
    only — check rows are not cells of one published total (each has its own
    denominator), so there is no margin to protect; the rate blanking is the
    complementary half."""
    import polars as pl

    small = (pl.col("n_affected") > 0) & (pl.col("n_affected") < k)
    return frame.with_columns(
        pl.when(small).then(None).otherwise(pl.col("n_affected")).alias("n_affected"),
        small.fill_null(False).alias("n_affected_suppressed"),
        pl.when(small).then(None).otherwise(pl.col("value")).alias("value"),
        pl.lit(k, dtype=pl.Int32).alias("k"),
    )


def suppress_topk(frame: polars.DataFrame, k: int) -> polars.DataFrame:
    """The published shape of the raw top-k frame: ``disclose.suppress`` per
    ``(schema, table, column)`` group over ``n`` (complementary: a lone small cell takes
    its next-smallest neighbour, so the group's published total cannot back it out;
    ``share`` blanked beside a hidden ``n``), ``n_suppressed`` marking the cells, ``k``."""
    import polars as pl

    from mimicwarehouse.disclose import suppress

    if frame.is_empty():
        return frame.with_columns(
            pl.lit(False).alias("n_suppressed"), pl.lit(k, dtype=pl.Int32).alias("k")
        ).select(*TOPK_COLUMNS)
    parts: list[pl.DataFrame] = []
    keys = ["schema", "table", "column"]
    for _key, group in frame.group_by(keys, maintain_order=True):
        out, _report = suppress(
            group, k=k, count_cols=["n"], group_cols=["value"], complementary=True
        )
        parts.append(out)
    published = pl.concat(parts)
    return published.with_columns(pl.lit(k, dtype=pl.Int32).alias("k")).select(*TOPK_COLUMNS)


#: Column orders of the four published tables (the CSV / Parquet shapes).
TABLES_COLUMNS: tuple[str, ...] = (
    "schema",
    "table",
    "rows",
    "columns",
    "parquet_bytes",
    "files",
    "worst_status",
    "wall_s",
    "peak_rss_mb",
    "built_at",
    "tier",
    "build_id",
    "run_id",
    "snapshot_id",
)
COLUMNS_COLUMNS: tuple[str, ...] = (
    "schema",
    "table",
    "column",
    "ordinal",
    "dtype",
    "is_identifier",
    "is_free_text",
    "is_dictionary_coded",
    "null_pct",
    "n_distinct_approx",
    "min_value",
    "max_value",
    *QUANTILE_COLUMNS,
    "tier",
    "build_id",
    "run_id",
)
TOPK_COLUMNS: tuple[str, ...] = (
    "schema",
    "table",
    "column",
    "rank",
    "value",
    "n",
    "share",
    "n_suppressed",
    "k",
    "tier",
    "build_id",
    "run_id",
)
CHECKS_COLUMNS: tuple[str, ...] = (
    "check_id",
    "schema",
    "table",
    "column",
    "itemid",
    "label",
    "metric",
    "value",
    "threshold",
    "rule",
    "status",
    "n_affected",
    "n_affected_suppressed",
    "k",
    "detail",
    "tier",
    "build_id",
    "run_id",
)


def _schemas() -> dict[str, dict[str, Any]]:
    import polars as pl

    tables = {
        "schema": pl.String,
        "table": pl.String,
        "rows": pl.Int64,
        "columns": pl.Int32,
        "parquet_bytes": pl.Int64,
        "files": pl.Int32,
        "worst_status": pl.String,
        "wall_s": pl.Float64,
        "peak_rss_mb": pl.Float64,
        "built_at": pl.String,
        "tier": pl.String,
        "build_id": pl.String,
        "run_id": pl.String,
        "snapshot_id": pl.String,
    }
    columns = {
        "schema": pl.String,
        "table": pl.String,
        "column": pl.String,
        "ordinal": pl.Int32,
        "dtype": pl.String,
        "is_identifier": pl.Boolean,
        "is_free_text": pl.Boolean,
        "is_dictionary_coded": pl.Boolean,
        "null_pct": pl.Float64,
        "n_distinct_approx": pl.Int64,
        "min_value": pl.String,
        "max_value": pl.String,
        "p01": pl.Float64,
        "p50": pl.Float64,
        "p99": pl.Float64,
        "tier": pl.String,
        "build_id": pl.String,
        "run_id": pl.String,
    }
    topk = {
        "schema": pl.String,
        "table": pl.String,
        "column": pl.String,
        "rank": pl.Int32,
        "value": pl.String,
        "n": pl.Int64,
        "share": pl.Float64,
        "tier": pl.String,
        "build_id": pl.String,
        "run_id": pl.String,
    }
    checks = {
        "check_id": pl.String,
        "schema": pl.String,
        "table": pl.String,
        "column": pl.String,
        "itemid": pl.Int64,
        "label": pl.String,
        "metric": pl.String,
        "value": pl.Float64,
        "threshold": pl.Float64,
        "rule": pl.String,
        "status": pl.String,
        "n_affected": pl.Int64,
        "detail": pl.String,
        "tier": pl.String,
        "build_id": pl.String,
        "run_id": pl.String,
    }
    return {"tables": tables, "columns": columns, "topk": topk, "checks": checks}


def assemble(
    profiles: Sequence[TableProfile],
    *,
    thresholds: Thresholds | None = None,
    k: int,
    tier: str,
    build_id: str,
    run_id: str | None,
    snapshot_id: str | None,
    built_at: str,
) -> dict[str, polars.DataFrame]:
    """The four published frames plus the two raw twins (``raw_topk`` / ``raw_checks``)
    from the per-table profiles: thresholds evaluated, statuses rolled up per table,
    counts suppressed (:func:`suppress_checks`, :func:`suppress_topk`)."""
    import polars as pl

    thresholds = thresholds or load_thresholds()
    schemas = _schemas()
    ids = {"tier": tier, "build_id": build_id, "run_id": run_id}
    table_rows: list[dict[str, Any]] = []
    column_rows: list[dict[str, Any]] = []
    topk_rows: list[dict[str, Any]] = []
    check_rows: list[dict[str, Any]] = []
    for p in profiles:
        evaluated = evaluate(p.checks, thresholds)
        for d in evaluated:
            d["schema"] = p.schema_name
            d["table"] = p.name
            check_rows.append({**d, **ids})
        table_rows.append(
            {
                "schema": p.schema_name,
                "table": p.name,
                "rows": p.rows,
                "columns": len(p.columns),
                "parquet_bytes": p.parquet_bytes,
                "files": p.files,
                "worst_status": worst_status(d["status"] for d in evaluated),
                "wall_s": p.wall_s,
                "peak_rss_mb": p.peak_rss_mb,
                "built_at": built_at,
                "snapshot_id": snapshot_id,
                **ids,
            }
        )
        for c in p.columns:
            column_rows.append({"schema": p.schema_name, "table": p.name, **c, **ids})
        for t in p.topk:
            topk_rows.append({"schema": p.schema_name, "table": p.name, **t, **ids})
    tables = pl.DataFrame(table_rows, schema=schemas["tables"]).select(*TABLES_COLUMNS)
    columns = pl.DataFrame(column_rows, schema=schemas["columns"]).select(*COLUMNS_COLUMNS)
    raw_topk = pl.DataFrame(topk_rows, schema=schemas["topk"])
    raw_checks = pl.DataFrame(check_rows, schema=schemas["checks"])
    checks = suppress_checks(raw_checks, k).select(*CHECKS_COLUMNS)
    topk = suppress_topk(raw_topk, k)
    return {
        TABLES_TABLE: tables,
        COLUMNS_TABLE: columns,
        TOPK_TABLE: topk,
        CHECKS_TABLE: checks,
        f"raw_{TOPK_TABLE}": raw_topk,
        f"raw_{CHECKS_TABLE}": raw_checks,
    }


# ---------------------------------------------------------------------------
# Files: slices, meta Parquet
# ---------------------------------------------------------------------------


def meta_table_path(lake_root: Path | str, tier: str, table: str) -> Path:
    """``<lake_root>/meta/<tier>/<table>.parquet`` (EP-29's meta layout)."""
    from mimicwarehouse.catalog.profile import meta_dir

    return meta_dir(lake_root, tier) / f"{table}.parquet"


def raw_table_path(lake_root: Path | str, tier: str, table: str) -> Path:
    """``<lake_root>/meta/<tier>/raw/<table>.parquet`` — the unsuppressed twin."""
    from mimicwarehouse.catalog.profile import meta_dir
    from mimicwarehouse.units import RAW_DIRNAME

    return meta_dir(lake_root, tier) / RAW_DIRNAME / f"{table}.parquet"


def slices_dir(lake_root: Path | str, tier: str) -> Path:
    """``<lake_root>/meta/<tier>/raw/qc/`` — one JSON slice per profiled table."""
    from mimicwarehouse.catalog.profile import meta_dir
    from mimicwarehouse.units import RAW_DIRNAME

    return meta_dir(lake_root, tier) / RAW_DIRNAME / SLICES_DIRNAME


def slice_path(lake_root: Path | str, tier: str, qualified: str) -> Path:
    return slices_dir(lake_root, tier) / f"{qualified}.json"


def write_slice(lake_root: Path | str, tier: str, profile: TableProfile) -> Path:
    """Write one table's profile as a JSON slice (``fsio.atomic_write_text``)."""
    from mimicwarehouse.fsio import atomic_write_text

    path = slice_path(lake_root, tier, profile.table)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(profile.to_dict(), indent=1, sort_keys=True) + "\n")
    return path


def read_slice(lake_root: Path | str, tier: str, qualified: str) -> TableProfile:
    path = slice_path(lake_root, tier, qualified)
    if not path.is_file():
        raise QcError(
            f"no QC slice for {qualified} on tier {tier} — run "
            f"`mwh build --tier {tier} --select {STEP_PREFIX}{qualified},{STEP_CHECKS}` "
            f"(or --tag {DAG_TAG}) first"
        )
    return TableProfile.from_dict(json.loads(path.read_text(encoding="utf-8")))


def write_frame_parquet(con: duckdb.DuckDBPyConnection, dest: Path, frame: Any) -> int:
    """A Polars frame -> ``dest`` through a registered view + ``COPY`` +
    :func:`publish.replace` (the EP-29 / EP-39 meta-writer shape over a frame instead of
    ``executemany``, ``docs/gotchas.md`` §1); returns the file size."""
    from mimicwarehouse import publish

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".tmp")
    con.register("_mwh_qc_frame", frame)
    try:
        con.execute(
            f"COPY _mwh_qc_frame TO {_sql_str(tmp.resolve().as_posix())} "
            "(FORMAT PARQUET, COMPRESSION ZSTD)"
        )
        publish.replace(tmp, dest)
    finally:
        con.unregister("_mwh_qc_frame")
    return dest.stat().st_size


def read_meta_frame(lake_root: Path | str, tier: str, table: str, *, raw: bool = False) -> Any:
    """The published (or ``raw``) meta table of one tier as a Polars frame."""
    import polars as pl

    path = (raw_table_path if raw else meta_table_path)(lake_root, tier, table)
    if not path.is_file():
        raise QcError(
            f"no meta.{table} for tier {tier} ({path}) — run `mwh build --tier {tier} "
            f"--tag {DAG_TAG}` first"
        )
    return pl.read_parquet(path)


# ---------------------------------------------------------------------------
# The DAG step handlers
# ---------------------------------------------------------------------------

_STATE_SNAPSHOT = "qc.core_snapshot_id"


def _core_snapshot_id(ctx: StepContext) -> str:
    from mimicwarehouse.dag.snapshot import layer_snapshot

    if _STATE_SNAPSHOT not in ctx.state:
        ctx.state[_STATE_SNAPSHOT] = layer_snapshot(
            ctx.lake_root, "core", ctx.tier, settings=ctx.settings
        )
    return str(ctx.state[_STATE_SNAPSHOT])


def ep29_profile(
    con: duckdb.DuckDBPyConnection,
    lake_root: Path,
    tier: str,
    qualified: str,
    *,
    snapshot_id: str | None = None,
) -> dict[str, Ep29Column]:
    """The EP-29 profile of one table (``profile_columns.parquet``) as ``{column:
    (null_pct, approx_distinct, min, max)}``. :class:`QcError` when the profile is
    missing, lacks the table, or — with ``snapshot_id`` — was computed over another core
    snapshot (stale: rerun ``meta.profile``)."""
    from mimicwarehouse.catalog.profile import profile_paths

    _tables_path, columns_path = profile_paths(lake_root, tier)
    remedy = f"run `mwh build --tier {tier} --select meta.profile,catalog` first (EP-29)"
    if not columns_path.is_file():
        raise QcError(f"no EP-29 profile for tier {tier} under {columns_path.parent} — {remedy}")
    schema, _, name = qualified.partition(".")
    rows = con.execute(
        'SELECT "column", null_pct, approx_distinct, min_value, max_value, snapshot_id '
        f"FROM read_parquet({_sql_str(columns_path.resolve().as_posix())}) "
        f'WHERE "schema" = ? AND "table" = ?',
        [schema, name],
    ).fetchall()
    if not rows:
        raise QcError(f"the EP-29 profile of tier {tier} has no rows for {qualified} — {remedy}")
    recorded = {str(r[5]) for r in rows if r[5] is not None}
    if snapshot_id is not None and recorded and recorded != {snapshot_id}:
        raise QcError(
            f"the EP-29 profile of tier {tier} is stale for {qualified} (core snapshot "
            f"changed since it was computed) — {remedy}"
        )
    return {
        str(c): (
            None if p is None else float(p),
            None if d is None else int(d),
            None if lo is None else str(lo),
            None if hi is None else str(hi),
        )
        for c, p, d, lo, hi, _sid in rows
    }


def ensure_table_views(
    ctx: StepContext, tables: Iterable[str], contract: Contract | None = None
) -> int:
    """``CREATE OR REPLACE VIEW`` for every named core table complete for the tier on
    the build connection — the same relations the concept runner's
    ``ensure_source_views`` creates, but **uncached**: that walker runs once per build
    connection and misses a table whose stage step completed later in the same build
    (a concept step ordered before ``stage.emar_detail`` freezes the view set without
    it). Returns the number of views (re)created."""
    from mimicwarehouse.catalog.build import qualifies
    from mimicwarehouse.concepts.runner import source_relation_sql
    from mimicwarehouse.loader.manifest import read_status
    from mimicwarehouse.schema.contract import load_contract

    contract = contract or load_contract()
    status = read_status(ctx.lake_root)["steps"]
    created = 0
    for qualified in dict.fromkeys(tables):
        if not contract.has_table(qualified) or not qualifies(status.get(qualified), ctx.tier):
            continue
        table = contract.table(qualified)
        ctx.con.execute(f"CREATE SCHEMA IF NOT EXISTS {table.schema_name}")
        columns = ", ".join(_q(c.name) for c in table.columns)
        ctx.con.execute(
            f"CREATE OR REPLACE VIEW {relation(qualified)} AS SELECT {columns} "
            f"FROM {source_relation_sql(table, ctx.lake_root, ctx.buckets)}"
        )
        created += 1
    return created


def run_profile_table(step: Step, ctx: StepContext) -> StepOutcome:
    """The ``qc.profile.<schema>.<table>`` handler (module docstring): the table's SQL
    family on the build connection, measured, written as a JSON slice."""
    from mimicwarehouse.concepts.runner import ensure_source_views
    from mimicwarehouse.dag.runner import StepOutcome
    from mimicwarehouse.dag.snapshot import table_file_stats
    from mimicwarehouse.loader.manifest import utc_now_iso
    from mimicwarehouse.run import ResourceLog
    from mimicwarehouse.schema.contract import load_contract
    from mimicwarehouse.units import load_catalogue

    if not step.name.startswith(STEP_PREFIX):
        raise QcError(f"step {step.name}: expected a {STEP_PREFIX}<schema>.<table> name")
    qualified = step.name[len(STEP_PREFIX) :]
    contract = load_contract()
    if not contract.has_table(qualified):
        raise QcError(f"step {step.name}: unknown contract table {qualified}")
    table = contract.table(qualified)
    thresholds = load_thresholds()
    validate_thresholds(thresholds, contract)
    ensure_source_views(ctx)
    needed = [qualified, *(fk.ref_table for fk in contract.foreign_keys_of(table))]
    if thresholds.window_rule(qualified) is not None:
        needed.append(thresholds.event_window.stay_table)
    ensure_table_views(ctx, needed, contract)
    have = present_tables(ctx.con)
    if qualified not in have:
        raise QcError(
            f"{qualified} is not staged for tier {ctx.tier} — `mwh build --tier {ctx.tier} "
            f"--select stage.{qualified}` first"
        )
    snapshot_id = _core_snapshot_id(ctx)
    ep29 = ep29_profile(ctx.con, ctx.lake_root, ctx.tier, qualified, snapshot_id=snapshot_id)
    catalogue = load_catalogue()

    def work() -> TableProfile:
        return profile_table(
            ctx.con,
            table,
            contract=contract,
            thresholds=thresholds,
            catalogue=catalogue,
            ep29=ep29,
            present=have,
        )

    profile, usage = ResourceLog.measure(work, data_root=ctx.settings.data_root)
    stats = table_file_stats(ctx.lake_root, ctx.tier, settings=ctx.settings).get(qualified)
    profile.parquet_bytes = stats[1] if stats else None
    profile.files = stats[2] if stats else None
    profile.wall_s = usage.wall_s
    profile.peak_rss_mb = usage.peak_rss_mb
    profile.disk_delta_mb = usage.disk_delta_mb
    profile.build_id = ctx.build_id
    profile.snapshot_id = snapshot_id
    profile.built_at = utc_now_iso()
    path = write_slice(ctx.lake_root, ctx.tier, profile)
    _LOG.info(
        "qc profile %s (%s): %s rows, %d column(s), %d top-k row(s), %d check(s), wall=%.1fs",
        qualified,
        ctx.tier,
        f"{profile.rows:,}",
        len(profile.columns),
        len(profile.topk),
        len(profile.checks),
        usage.wall_s,
    )
    return StepOutcome(rows=profile.rows, bytes_out=path.stat().st_size, files=1)


def profiled_tables(lake_root: Path, tier: str, contract: Contract | None = None) -> list[str]:
    """The contract tables (staged schemas, contract order) complete for ``tier`` on
    this lake — the tables ``qc.checks`` assembles."""
    from mimicwarehouse.catalog.build import STAGED_SCHEMAS, qualifies
    from mimicwarehouse.loader.manifest import read_status
    from mimicwarehouse.schema.contract import load_contract

    contract = contract or load_contract()
    status = read_status(Path(lake_root))["steps"]
    return [
        t.qualified_name
        for schema in STAGED_SCHEMAS
        for t in contract.by_schema(schema)
        if qualifies(status.get(t.qualified_name), tier)
    ]


def run_checks(step: Step, ctx: StepContext) -> StepOutcome:
    """The ``qc.checks`` handler (module docstring): every slice of the tier's complete
    tables (a missing or stale slice refuses with the remedy), the thresholds, the four
    meta tables (+ raw twins) written inside a ``kind: qc`` run with one
    ``kind: query`` benchmark line per profiled table."""
    from mimicwarehouse import run as run_mod
    from mimicwarehouse.dag.runner import StepOutcome
    from mimicwarehouse.loader.manifest import utc_now_iso
    from mimicwarehouse.schema.contract import load_contract
    from mimicwarehouse.units import CATALOGUE_FILENAME, load_catalogue

    contract = load_contract()
    thresholds = load_thresholds()
    validate_thresholds(thresholds, contract)
    snapshot_id = _core_snapshot_id(ctx)
    targets = profiled_tables(ctx.lake_root, ctx.tier, contract)
    if not targets:
        raise QcError(
            f"no table is complete for tier {ctx.tier} under {ctx.lake_root} — stage first"
        )
    profiles = [read_slice(ctx.lake_root, ctx.tier, qn) for qn in targets]
    stale = [p.table for p in profiles if p.snapshot_id and p.snapshot_id != snapshot_id]
    if stale:
        raise QcError(
            f"stale QC slice(s) for {', '.join(stale)} (core snapshot changed) — rerun "
            f"`mwh build --tier {ctx.tier} --tag {DAG_TAG}`"
        )
    k = ctx.settings.k_suppression
    catalogue = load_catalogue()
    params: dict[str, Any] = {
        "build_id": ctx.build_id,
        "tables": len(profiles),
        "k": k,
        "thresholds_version": thresholds.version,
        "top_k": thresholds.top_k,
    }
    with run_mod.start(
        RUN_NAME,
        tier=ctx.tier,
        kind=RUN_KIND,
        params=params,
        settings=ctx.settings,
        claim_type=CLAIM_TYPE,
        doctor=ctx.run is not None,
    ) as r:
        r.record_snapshot("core", snapshot_id)
        r.record_ref(
            "qc_thresholds",
            THRESHOLDS_FILENAME,
            version=str(thresholds.version),
            hash=thresholds_sha256(),
        )
        r.record_ref("item_catalogue", CATALOGUE_FILENAME, version=str(catalogue.version))
        for p in profiles:
            r.bench(
                BENCH_KIND,
                f"{STEP_PREFIX}{p.table}",
                wall_s=float(p.wall_s or 0.0),
                build_id=ctx.build_id,
                rows=p.rows,
                peak_rss_mb=p.peak_rss_mb,
                disk_delta_mb=p.disk_delta_mb,
                ok=True,
            )
        frames = assemble(
            profiles,
            thresholds=thresholds,
            k=k,
            tier=ctx.tier,
            build_id=ctx.build_id,
            run_id=r.run_id,
            snapshot_id=snapshot_id,
            built_at=utc_now_iso(),
        )
        nbytes = 0
        for table in META_TABLES:
            nbytes += write_frame_parquet(
                ctx.con, meta_table_path(ctx.lake_root, ctx.tier, table), frames[table]
            )
        for table in RAW_TABLES:
            nbytes += write_frame_parquet(
                ctx.con, raw_table_path(ctx.lake_root, ctx.tier, table), frames[f"raw_{table}"]
            )
        checks = frames[CHECKS_TABLE]
        counts = {
            s: int((checks.get_column("status") == s).sum()) if checks.height else 0
            for s in STATUSES
        }
        r.manifest.params = {**r.manifest.params, "status_counts": counts}
        _LOG.info(
            "meta.qc_* (%s): %d table(s), %d column(s), %d top-k row(s), %d check(s) — "
            "pass %d / warn %d / fail %d, k=%d — run %s",
            ctx.tier,
            frames[TABLES_TABLE].height,
            frames[COLUMNS_TABLE].height,
            frames[TOPK_TABLE].height,
            checks.height,
            counts["pass"],
            counts["warn"],
            counts["fail"],
            k,
            r.run_id,
        )
    return StepOutcome(
        rows=checks.height, bytes_out=nbytes, files=len(META_TABLES) + len(RAW_TABLES)
    )


# ---------------------------------------------------------------------------
# Catalog extension: comments on the meta.qc_* tables
# ---------------------------------------------------------------------------

_META_COMMENTS: dict[str, str] = {
    TABLES_TABLE: (
        "QC table profile (qc/profile.py, EP-44): one row per profiled table - rows, columns, "
        "Parquet bytes, worst check status, profile wall / peak RSS, built_at, run / build / "
        "core snapshot ids."
    ),
    COLUMNS_TABLE: (
        "QC column profile (EP-44): dtype, identifier / free-text / dictionary-coded flags, "
        "null_pct and n_distinct_approx reused from the EP-29 profile, min / max (VARCHAR "
        "casts; NULL for identifiers), p01 / p50 / p99 (approx_quantile) for numeric "
        "non-identifier columns."
    ),
    TOPK_TABLE: (
        "Top-k values of the dictionary-coded columns only (EP-44): (value, n, share) per "
        "column, k-suppressed at build time with disclose.suppress (n / share NULL where "
        "n_suppressed = true). Raw counts stay in the data root, never in a catalog."
    ),
    CHECKS_TABLE: (
        "QC checks (EP-44; thresholds in qc/thresholds.yaml): check_id, table, column, itemid, "
        "metric, value, threshold, rule, status pass|warn|fail, n_affected (NULL with "
        "n_affected_suppressed = true where 0 < n < k; value blanked beside it), detail. "
        "Aggregates only - no row sample is ever stored."
    ),
}


def register_qc(con: duckdb.DuckDBPyConnection, tier: str) -> None:
    """The catalog extension (:data:`mimicwarehouse.catalog.build.CATALOG_EXTENSIONS`
    entry, after EP-37's discovery walker): comment the ``meta.qc_*`` tables the walker
    registered from ``lake/meta/<tier>/``. DDL only; never opens a connection."""
    present = {
        str(r[0])
        for r in con.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'meta'"
        ).fetchall()
    }
    commented = 0
    for table, comment in _META_COMMENTS.items():
        if table in present:
            con.execute(f'COMMENT ON TABLE meta."{table}" IS {_sql_str(comment)}')
            commented += 1
    _LOG.info("catalog extension qc: %d meta.qc_* table(s) on tier %s", commented, tier)


# ---------------------------------------------------------------------------
# dag/specs/qc.yaml — rendered from the contract + thresholds
# ---------------------------------------------------------------------------

SPEC_NAME = "qc"
_TIERS: list[str] = ["fixture", "demo", "dev", "full"]
_SPEC_HEADER = (
    "# dag/specs/qc.yaml -- the data-quality profiling steps (EP-44; ASCII only). GENERATED\n"
    "# by `python -m mimicwarehouse.qc` from the EP-9 contract + qc/thresholds.yaml\n"
    "# (mimicwarehouse.qc.profile.spec_document; test_ep44 asserts no drift) - edit the\n"
    "# generator, not this file. Merged into the one graph by dag.spec.load_dag() (EP-37\n"
    "# discovery): one python step per contract table of the staged schemas (its profile +\n"
    "# checks on the build connection, a JSON slice under lake/meta/<tier>/raw/qc/), the\n"
    "# qc.checks step (thresholds, the four meta.qc_* tables inside a kind: qc run), the\n"
    "# qc.report step (runs/<run_id>/qc_report.md + CSVs) and the shared catalog step, whose\n"
    "# depends_on / tags are unioned with the other specs' so `mwh build --tier <t> --tag qc`\n"
    "# ends in a catalog rebuild. No target: the steps re-run whenever selected. A profile\n"
    "# step depends on meta.profile (it reuses EP-29's null / distinct / min / max), on its\n"
    "# table's stage step, on the stage steps of the tables its foreign keys reference and on\n"
    "# icustays where the event-window rule applies.\n"
)


def spec_document(
    contract: Contract | None = None, thresholds: Thresholds | None = None
) -> dict[str, Any]:
    """The ``dag/specs/qc.yaml`` document (module docstring)."""
    from mimicwarehouse.catalog.build import STAGED_SCHEMAS
    from mimicwarehouse.schema.contract import load_contract

    contract = contract or load_contract()
    thresholds = thresholds or load_thresholds()
    steps: list[dict[str, Any]] = []
    profile_names: list[str] = []
    for schema in STAGED_SCHEMAS:
        for table in contract.by_schema(schema):
            qn = table.qualified_name
            deps = [f"stage.{qn}", "meta.profile"]
            for fk in contract.foreign_keys_of(table):
                if fk.ref_table.partition(".")[0] in STAGED_SCHEMAS:
                    deps.append(f"stage.{fk.ref_table}")
            if thresholds.window_rule(qn) is not None:
                deps.append(f"stage.{thresholds.event_window.stay_table}")
            name = f"{STEP_PREFIX}{qn}"
            profile_names.append(name)
            steps.append(
                {
                    "name": name,
                    "kind": "python",
                    "callable": "mimicwarehouse.qc.profile:run_profile_table",
                    "tags": [DAG_TAG, "meta"],
                    "tiers": list(_TIERS),
                    "depends_on": list(dict.fromkeys(deps)),
                }
            )
    steps.append(
        {
            "name": STEP_CHECKS,
            "kind": "python",
            "callable": "mimicwarehouse.qc.profile:run_checks",
            "tags": [DAG_TAG, "meta"],
            "tiers": list(_TIERS),
            "depends_on": profile_names,
        }
    )
    steps.append(
        {
            "name": STEP_REPORT,
            "kind": "python",
            "callable": "mimicwarehouse.qc.report:run_report",
            "tags": [DAG_TAG],
            "tiers": list(_TIERS),
            "depends_on": [STEP_CHECKS],
        }
    )
    steps.append(
        {
            "name": "catalog",
            "kind": "catalog",
            "tags": ["catalog", DAG_TAG],
            "depends_on": [STEP_CHECKS],
        }
    )
    return {"version": 1, "steps": steps}


def render_spec(contract: Contract | None = None, thresholds: Thresholds | None = None) -> str:
    """The YAML text of :func:`spec_document` with the generated-file header."""
    body = yaml.safe_dump(
        spec_document(contract, thresholds), sort_keys=False, default_flow_style=None, width=100
    )
    return _SPEC_HEADER + body


def spec_path() -> Path:
    """``src/mimicwarehouse/dag/specs/qc.yaml`` inside the installed package."""
    from mimicwarehouse.dag.spec import specs_root

    return specs_root() / f"{SPEC_NAME}.yaml"


def sync_spec(path: Path | None = None) -> Path:
    """Write :func:`render_spec` to ``dag/specs/qc.yaml`` (LF; idempotent)."""
    target = Path(path) if path is not None else spec_path()
    target.write_text(render_spec(), encoding="utf-8", newline="\n")
    return target


__all__ = [
    "BENCH_KIND",
    "CHECKS_COLUMNS",
    "CHECKS_TABLE",
    "CHECK_IDS",
    "CLAIM_TYPE",
    "COLUMNS_COLUMNS",
    "COLUMNS_TABLE",
    "DAG_TAG",
    "META_TABLES",
    "QUANTILES",
    "QUANTILE_COLUMNS",
    "RAW_TABLES",
    "RUN_KIND",
    "RUN_NAME",
    "SLICES_DIRNAME",
    "SPEC_NAME",
    "STATUSES",
    "STEP_CHECKS",
    "STEP_PREFIX",
    "STEP_REPORT",
    "TABLES_COLUMNS",
    "TABLES_TABLE",
    "THRESHOLDS_FILENAME",
    "TOPK_COLUMNS",
    "TOPK_TABLE",
    "VALUE_MAX_CHARS",
    "CheckRow",
    "EventWindow",
    "OrderRule",
    "QcError",
    "Status",
    "TableProfile",
    "Threshold",
    "Thresholds",
    "WindowTable",
    "assemble",
    "check_age_cap",
    "check_era_coverage",
    "check_event_window",
    "check_fk_orphans",
    "check_natural_key_dupes",
    "check_null_share",
    "check_pk_unique",
    "check_ts_order",
    "check_ts_store_lag",
    "check_units",
    "dictionary_coded_columns",
    "ensure_table_views",
    "ep29_profile",
    "evaluate",
    "fk_orphans_sql",
    "load_thresholds",
    "load_thresholds_from",
    "meta_table_path",
    "order_sql",
    "present_tables",
    "profile_sql",
    "profile_table",
    "profiled_tables",
    "raw_table_path",
    "read_meta_frame",
    "read_slice",
    "register_qc",
    "relation",
    "render_spec",
    "run_checks",
    "run_profile_table",
    "slice_path",
    "slices_dir",
    "spec_document",
    "spec_path",
    "suppress_checks",
    "suppress_topk",
    "sync_spec",
    "thresholds_path",
    "thresholds_sha256",
    "topk_sql",
    "units_sql",
    "validate_thresholds",
    "wants_quantiles",
    "window_sql",
    "worst_status",
    "write_frame_parquet",
    "write_slice",
]
