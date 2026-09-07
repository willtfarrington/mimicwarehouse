"""Disclosure primitives — ``suppress`` / ``check`` / sidecars / ``mwh disclose`` (EP-43;
GOVERNANCE §5 and §7, DESIGN §14, D-33 / D-40).

The one implementation of the small-cell rule and of the release gate every later brief
calls instead of re-implementing (GOVERNANCE §5: "implemented once in
``mimicwarehouse.disclose`` and used everywhere"). Four surfaces:

1. :func:`suppress` — complementary k-suppression of a pandas / Polars aggregate frame.
   *Table mode*: every count-like cell with ``0 < n < k`` becomes null with a marker
   column ``<col>_suppressed = True`` (primary suppression); with ``complementary=True``
   no published margin can back a hidden cell out — within each count column every
   marginal total of the group columns (the whole column when there is at most one group
   column; every proper subset of the group columns otherwise, the grand total included)
   is assumed publishable, so a margin with exactly one hidden cell hides its
   next-smallest published cell (preferring a row already damaged), and within a row two
   nested count columns (``n`` / ``n_fit``, ``n_units`` / ``n_positive``,
   ``denominator`` / ``numerator``: the smaller never exceeds the larger in any row)
   whose published difference lies in ``(0, k)`` hide the smaller one — the EP-31
   "n vs n_fit" lesson (EP-33 amendment b). The loop runs to a fixpoint; rate-like
   columns (``share`` / ``rate`` / ``pct`` …) are blanked on every row that lost a cell so
   a share cannot restore it; the call is idempotent. *Chain mode* (attrition sequences
   ``n_0 >= n_1 >= …``, EP-48): a step whose drop ``n_(i-1) - n_i`` lies in ``(0, k)``
   has the drop withheld and **both** adjacent totals coarsened to bands rounded to the
   nearest 10 (``~1,000``; :data:`BAND_WIDTH`), and a drop is published only when both
   of its totals stay exact — so no exact difference survives (the rounding rule,
   documented in ``docs/methods/disclosure.md``). :func:`render_cell` renders ``<11`` /
   ``~1,000`` / ``1,234``; :class:`SuppressionReport` records every cell touched.
2. :func:`check` — the release gate over one artefact (``.csv .parquet .json .yaml .md
   .mmd .html .svg .png .txt``): identifier columns (``ID_COL``), real-id-band literals
   in text, numeric columns and JSON (``ID_BAND``; the guard's scanner, band values are
   patterns never data, count-like / telemetry columns exempt — EP-170 amendment 2),
   free text (``FREE_TEXT``: named columns, any string over
   :data:`FREE_TEXT_MAX_CHARS`, more than :data:`FREE_TEXT_MAX_DISTINCT` distinct values;
   ``allow_text`` for dictionary labels), small cells (``SMALL_CELL``: an unmarked count
   cell in ``1..k-1`` in a frame, a Markdown / HTML table, a ``n = 4`` in prose or a
   Mermaid diagram, a derivable difference of two published nested totals, a derivable
   attrition drop), embedded data arrays in HTML / Vega / Plotly / JSON
   (``EMBEDDED_ROWS``: fail over :data:`EMBEDDED_ROWS_FAIL` rows or with identifier
   keys, warn over :data:`EMBEDDED_ROWS_WARN`), a figure without its source table
   (``NO_SOURCE``) and oversize images (``OVERSIZE``). Every finding's detail is
   value-free (ids masked as the guard masks them, lengths and row numbers only) and
   any engine / parser error text is sanitised before it enters a result (DKB-2 rule,
   EP-33 amendment e). :func:`check_frame` is the in-process form; :func:`assert_clean`
   raises :class:`DisclosureError` for EP-59 / EP-130; :func:`warn_badges` returns the
   per-cell warn flags the app shows at ``n < k`` (EP-58).
3. :func:`write_sidecar` / :func:`verify` — ``<artefact>.disclosure.json`` (path, sha256,
   size, checks, k, passed, reviewer, timestamp, tool_version, git_sha); a sidecar is
   only ever written for a passing result and ``verify`` re-hashes the artefact.
4. ``mwh disclose check <path…> [--k 11] [--write-sidecar] [--allow-text col] [--json]``
   and ``mwh disclose verify <path> [--json]`` — findings exit
   :data:`~mimicwarehouse.console.EXIT_FINDINGS`, usage errors
   :data:`~mimicwarehouse.console.EXIT_USAGE` (``console.fail`` on stderr).

The ``safe.SUPPRESSOR`` hook (EP-30 seam, EP-33 amendment c) is :func:`safe_suppressor`:
the same complementary suppression released **row-wise** — a row with any hidden cell
is withheld, so the extreme-value aggregates it carries (``min`` / ``max`` / ``median``
over a subject-keyed column, SGT-2) leave with it; the hook's signature stays
``(df, k, count_columns) -> (df, rows_suppressed)``.

Nothing here reads the data root on its own; ``check`` opens exactly the path it is
given. Import budget: this module sits on the ``mwh --help`` path (the ``disclose``
sub-app), so polars / pandas / pyarrow / yaml parsing and ``mimicwarehouse.safe`` load
inside function bodies; :data:`FREE_TEXT_MAX_CHARS` mirrors ``safe.FREE_TEXT_MAX_CHARS``
the way ``tracer.VALUE_MAX_CHARS`` does (``test_ep43`` asserts them equal).
"""

from __future__ import annotations

import hashlib
import html.parser
import itertools
import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

import typer

from mimicwarehouse import guard

if TYPE_CHECKING:  # pragma: no cover
    import polars

# ---------------------------------------------------------------------------
# Constants (rule tables)
# ---------------------------------------------------------------------------

#: The small-cell threshold (GOVERNANCE §5, D-33); ``Settings.k_suppression`` defaults to it.
K_DEFAULT = 11
#: Chain mode rounds the totals beside a withheld drop to the nearest multiple of this.
BAND_WIDTH = 10
#: Marker column suffix: ``<count column>_suppressed`` (bool, never null).
MARKER_SUFFIX = "_suppressed"
#: Chain mode's band marker column suffix: ``<count column>_banded``.
BAND_SUFFIX = "_banded"
#: Chain mode's drop column and its marker.
DROP_COLUMN = "drop"
DROP_MARKER = "drop_suppressed"

#: Free-text bounds: any string value over this many characters is free text in an
#: artefact (the committed-text canon rule 4; mirrors ``safe.FREE_TEXT_MAX_CHARS``) …
FREE_TEXT_MAX_CHARS = 64
#: … a string column whose median length exceeds this is free text (brief item 2c) …
FREE_TEXT_MEDIAN_CHARS = 80
#: … and so is a string column with more distinct values than this, unless allow-listed.
FREE_TEXT_MAX_DISTINCT = 500
#: Embedded data arrays (HTML ``<script>`` JSON, Vega ``data.values``, Plotly traces,
#: any JSON record array): fail above the first, warn above the second.
EMBEDDED_ROWS_FAIL = 1000
EMBEDDED_ROWS_WARN = 200
#: Images: warn above this many bytes (a rendered aggregate figure is small) …
IMAGE_WARN_BYTES = 5 * 1024 * 1024
#: … and fail at the guard's blob bound (it could not be committed anyway).
IMAGE_FAIL_BYTES = guard.MAX_FILE_BYTES
#: At most this many per-line ``ID_BAND`` findings per file, then one summary row.
MAX_BAND_ROWS = guard.MAX_G4_ROWS_PER_FILE
#: Record arrays larger than this are sized, never materialised as a frame.
FRAME_SCAN_MAX_ROWS = 100_000

#: Finding codes, in report order.
CODES: tuple[str, ...] = (
    "ID_COL",
    "ID_BAND",
    "FREE_TEXT",
    "SMALL_CELL",
    "EMBEDDED_ROWS",
    "NO_SOURCE",
    "OVERSIZE",
)
CODE_TITLES: dict[str, str] = {
    "ID_COL": "identifier column",
    "ID_BAND": "real-id band literal",
    "FREE_TEXT": "free text",
    "SMALL_CELL": "small cell",
    "EMBEDDED_ROWS": "embedded data array",
    "NO_SOURCE": "figure without source table",
    "OVERSIZE": "oversize image",
}
FAIL = "fail"
WARN = "warn"
PASS = "pass"

#: Identifier column names refused by name (brief item 2a; the contract's identifier
#: flags join them at check time through ``safe.identifier_column_names``).
IDENTIFIER_COLUMN_NAMES: frozenset[str] = frozenset(
    {
        "subject_id",
        "hadm_id",
        "stay_id",
        "note_id",
        "emar_id",
        "pharmacy_id",
        "poe_id",
        "transfer_id",
        "caregiver_id",
        "provider_id",
        "order_id",
        "microevent_id",
        "labevent_id",
        "chartevent_id",
        "admit_provider_id",
        "enter_provider_id",
        "order_provider_id",
        "storetime_id",
        "orderid",
        "linkorderid",
        "emar_seq",
        "poe_seq",
        "microspecimen_id",
        "micro_specimen_id",
        "test_seq",
        "isolate_num",
        "ab_itemid",
    }
)
#: ``*_id`` names that are project / vocabulary ids, never patient-level (brief item 2a).
ID_NAME_ALLOW: frozenset[str] = frozenset(
    {
        "itemid",
        "codeset_id",
        "phenotype_id",
        "cohort_id",
        "run_id",
        "protocol_id",
        "concept_id",
        "patch_id",
        "study_id",
        "build_id",
        "audit_id",
        "snapshot_id",
        "job_id",
        "spec_id",
        "step_id",
        "model_id",
        "report_id",
        "export_id",
        "source_id",
        "d_id",
    }
)
#: Identifier-shaped names: ``id`` or ``*_id`` (checked after the allow-list).
ID_SHAPE_RE = re.compile(r"^(id|.*_id)$")

#: Count-like column names (brief item 1; ``n``, ``n_*``, ``*_n``, ``count*``, ``*_count``,
#: ``denominator``, ``numerator``, ``events`` …) plus DuckDB's generated
#: ``count(...)`` / ``approx_count_distinct(...)`` names.
COUNT_COLUMN_RE = re.compile(
    r"^(n|n_.+|.+_n|count.*|.+_count|cnt|.+_cnt|num_.+|approx_count_distinct.*|events?|deaths?"
    r"|numerator|denominator|subjects|patients|admissions|stays|rows|distinct)$",
    re.IGNORECASE,
)
#: Rate-like columns blanked beside a hidden count (a share would restore the cell).
RATE_COLUMN_RE = re.compile(
    r"^(share|rate|pct|percent|proportion|fraction|ratio|prevalence|incidence"
    r"|.+_(share|rate|pct|percent|prop|proportion|fraction|ratio))$",
    re.IGNORECASE,
)
#: Integer columns / keys exempt from the band scan: counts and telemetry are not ids
#: (EP-170 amendment 2 — "a large row count that happens to fall inside a band is not an
#: id"; the guard's committed-text canon does the same job with ``fmt_int``).
NOT_ID_COLUMN_RE = re.compile(
    r"^(.*bytes|size|.+_size|total|.+_total|sum|.+_sum|.+_ms|wall.*|rss.*|len|length|seed"
    r"|.+_seed|.+_s|elapsed.*|duration.*|.+_bytes|peak.*|.*rows.*|.*count.*|.*distinct.*)$",
    re.IGNORECASE,
)
#: Free-text column names (brief item 2c).
FREE_TEXT_COLUMN_RE = re.compile(
    r"^(text|notes?|comments?|value_text|narrative|free_text|.+_text|note_.+|.+_note|.+_notes)$",
    re.IGNORECASE,
)
#: Table headers whose integer cells are levels, codes, versions, units of measure or
#: telemetry — never people (the Markdown / HTML small-cell rule skips them).
EXEMPT_HEADER_RE = re.compile(
    r"level|stage|grade|code|version|seq|step|rank|index|idx|year|era|bucket|file|thread"
    r"|byte|\bmb\b|\bgb\b|\bkb\b|\bs\b|\bsec|\bms\b|\bmin\b|wall|rss|pass|%|pct|share|rate"
    r"|ratio|mean|median|\bsd\b|std|avg|\bmax\b|\bq\d|\bp\d|\bci\b|\bor\b|\bhr\b|score|age"
    r"|hour|day|\bk\b|itemid|\bid\b|#|unit|type|name|label|column|term|scope|table|tier"
    r"|status|width|height|depth|order|position|degree|dose|value|threshold|window|offset",
    re.IGNORECASE,
)
#: Table headers that make a table an attrition chain (first column).
CHAIN_HEADER_RE = re.compile(
    r"^(step|criterion|criteria|stage|filter|inclusion|exclusion|cohort step|attrition)$",
    re.IGNORECASE,
)
#: A whole table cell that is an integer (``1,234`` or ``1234``).
INT_CELL_RE = re.compile(r"^-?(\d{1,3}(,\d{3})+|\d+)$")
#: A cell already rendered as suppressed / banded / absent.
SUPPRESSED_CELL_RE = re.compile(
    r"^(<\s*\d[\d,]*|~\s*\d[\d,]*|suppressed.*|-|n/?a|null|none|\.\.\.)?$", re.IGNORECASE
)
#: ``n = 4`` / ``events: 3`` in prose, Mermaid labels, SVG text and HTML text.
PROSE_COUNT_RE = re.compile(
    r"(?<![\w.])(n|n_[a-z0-9_]+|[a-z0-9_]+_n|count|events|deaths|numerator|denominator)"
    r"\s*[=:]\s*(\d{1,3}(?:,\d{3})+|\d+)(?!\w|,\d|\.\d)",
    re.IGNORECASE,
)
#: JSON / JS shapes that carry embedded data arrays (item 2e).
EMBEDDED_ARRAY_RE = re.compile(
    r'("values"\s*:\s*|"data"\s*:\s*|"datasets"\s*:\s*|Plotly\.(?:newPlot|react|plot)\(\s*[^,]+,\s*)'
    r"(?=[\[{])"
)
SCRIPT_RE = re.compile(r"<script\b[^>]*>(.*?)</script>", re.IGNORECASE | re.DOTALL)
MARKDOWN_SEPARATOR_RE = re.compile(r"^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")

SUPPORTED_SUFFIXES: tuple[str, ...] = (
    ".csv",
    ".parquet",
    ".json",
    ".yaml",
    ".yml",
    ".md",
    ".mmd",
    ".html",
    ".htm",
    ".svg",
    ".png",
    ".txt",
)
TABULAR_SUFFIXES: tuple[str, ...] = (".csv", ".parquet")
IMAGE_SUFFIXES: tuple[str, ...] = (".png", ".svg")
#: Sibling source tables a figure must carry (checked in this order; first match wins).
SOURCE_SIBLING_SUFFIXES: tuple[str, ...] = (".vl.json", ".json", ".csv", ".parquet")
SIDECAR_SUFFIX = ".disclosure.json"
SIDECAR_SCHEMA = "mimicwarehouse.disclosure/1"
#: Group-column subsets are enumerated exhaustively up to this many group columns.
MAX_EXHAUSTIVE_GROUP_COLS = 6


class DiscloseUsageError(ValueError):
    """A usage problem: unknown path / extension / mode, an unreadable artefact, a
    sidecar requested for a failing result (exit ``EXIT_USAGE``)."""


class DisclosureError(RuntimeError):
    """A frame or artefact violates the disclosure rules (:func:`assert_clean`)."""


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SuppressedCell:
    """One cell :func:`suppress` touched: ``kind`` is ``primary`` (in ``(0, k)``),
    ``complementary`` (a margin's next-smallest), ``derived`` (the smaller of two nested
    totals whose difference was small), ``band`` (a chain total coarsened), ``drop`` (a
    chain drop withheld) or ``rate`` (a rate blanked beside a hidden count)."""

    row: int
    column: str
    kind: str

    def as_dict(self) -> dict[str, Any]:
        return {"row": self.row, "column": self.column, "kind": self.kind}


@dataclass(frozen=True, slots=True)
class SuppressionReport:
    """What one :func:`suppress` call did (brief item 1: ``n_primary``,
    ``n_complementary``, ``cells``)."""

    k: int
    mode: str
    count_cols: tuple[str, ...]
    n_primary: int
    n_complementary: int
    n_banded: int
    cells: tuple[SuppressedCell, ...]

    @property
    def n_suppressed(self) -> int:
        return self.n_primary + self.n_complementary

    @property
    def rows_suppressed(self) -> int:
        """Rows with at least one hidden count cell (what the ``safe`` hook reports)."""
        return len({c.row for c in self.cells if c.kind in _HIDING_KINDS})

    def to_dict(self) -> dict[str, Any]:
        return {
            "k": self.k,
            "mode": self.mode,
            "count_cols": list(self.count_cols),
            "n_primary": self.n_primary,
            "n_complementary": self.n_complementary,
            "n_banded": self.n_banded,
            "rows_suppressed": self.rows_suppressed,
            "cells": [c.as_dict() for c in self.cells],
        }


_HIDING_KINDS: frozenset[str] = frozenset({"primary", "complementary", "derived", "drop"})


@dataclass(frozen=True, slots=True)
class Finding:
    """One refused / warned thing. ``detail`` never carries a data value."""

    code: str
    status: str  # fail | warn
    where: str
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "status": self.status,
            "where": self.where,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class CheckEntry:
    """One line of the per-code summary (what the sidecar records)."""

    code: str
    status: str  # pass | warn | fail
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "status": self.status, "detail": self.detail}


@dataclass(frozen=True, slots=True)
class CheckResult:
    """The verdict over one artefact (or one in-process frame)."""

    path: str
    k: int
    findings: tuple[Finding, ...]
    allow_text: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return not any(f.status == FAIL for f in self.findings)

    @property
    def n_fail(self) -> int:
        return sum(1 for f in self.findings if f.status == FAIL)

    @property
    def n_warn(self) -> int:
        return sum(1 for f in self.findings if f.status == WARN)

    @property
    def checks(self) -> tuple[CheckEntry, ...]:
        return summarise(self.findings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "k": self.k,
            "passed": self.passed,
            "n_fail": self.n_fail,
            "n_warn": self.n_warn,
            "allow_text": list(self.allow_text),
            "checks": [c.as_dict() for c in self.checks],
            "findings": [f.as_dict() for f in self.findings],
        }


@dataclass(frozen=True, slots=True)
class VerifyResult:
    """:func:`verify`'s answer: ``ok`` iff the sidecar exists, records a pass and its
    sha256 matches the artefact as it is now."""

    path: str
    sidecar: str
    ok: bool
    reason: str
    recorded_sha256: str | None = None
    current_sha256: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "sidecar": self.sidecar,
            "ok": self.ok,
            "reason": self.reason,
            "recorded_sha256": self.recorded_sha256,
            "current_sha256": self.current_sha256,
        }


def summarise(findings: Iterable[Finding]) -> tuple[CheckEntry, ...]:
    """One :class:`CheckEntry` per :data:`CODES` entry: ``fail`` / ``warn`` / ``pass`` with
    a count and the first finding's detail."""
    by_code: dict[str, list[Finding]] = {code: [] for code in CODES}
    for f in findings:
        by_code.setdefault(f.code, []).append(f)
    out: list[CheckEntry] = []
    for code in CODES:
        hits = by_code[code]
        fails = [f for f in hits if f.status == FAIL]
        warns = [f for f in hits if f.status == WARN]
        if fails:
            status, first = FAIL, fails[0]
        elif warns:
            status, first = WARN, warns[0]
        else:
            out.append(CheckEntry(code, PASS, "clean"))
            continue
        n = len(fails) if status == FAIL else len(warns)
        prefix = f"{n} finding(s): " if n > 1 else ""
        out.append(CheckEntry(code, status, f"{prefix}{first.detail}"))
    return tuple(out)


# ---------------------------------------------------------------------------
# Frame helpers (polars / pandas)
# ---------------------------------------------------------------------------


def _frame_kind(df: Any) -> str:
    module = type(df).__module__.split(".")[0]
    if module == "pandas":
        return "pandas"
    if module == "polars":
        return "polars"
    raise DiscloseUsageError(f"expected a polars or pandas DataFrame, got {type(df).__name__}")


def _to_polars(df: Any) -> tuple[polars.DataFrame, str]:
    kind = _frame_kind(df)
    if kind == "pandas":
        import polars as pl

        return pl.from_pandas(df), kind
    return df, kind


def _from_polars(frame: polars.DataFrame, kind: str) -> Any:
    return frame.to_pandas() if kind == "pandas" else frame


def is_count_column(name: str) -> bool:
    """Whether a column name is count-like (brief item 1's auto-detection rule)."""
    return COUNT_COLUMN_RE.match(name) is not None


def is_marker_column(name: str) -> bool:
    return name.endswith(MARKER_SUFFIX) or name.endswith(BAND_SUFFIX) or name == DROP_MARKER


def count_columns(frame: polars.DataFrame, count_cols: Sequence[str] | None = None) -> list[str]:
    """The count columns of ``frame``: ``count_cols`` (validated numeric) or the
    auto-detected integer columns with count-like names (marker columns never)."""
    if count_cols is not None:
        out: list[str] = []
        for c in count_cols:
            if c not in frame.columns:
                raise DiscloseUsageError(f"count column {c!r} is not in the frame")
            if not frame.schema[c].is_numeric():
                raise DiscloseUsageError(f"count column {c!r} is not numeric")
            out.append(c)
        return out
    return [
        c
        for c, dtype in frame.schema.items()
        if dtype.is_integer() and is_count_column(c) and not is_marker_column(c)
    ]


def _group_columns(
    frame: polars.DataFrame, group_cols: Sequence[str] | None, counts: Sequence[str]
) -> list[str]:
    if group_cols is not None:
        for c in group_cols:
            if c not in frame.columns:
                raise DiscloseUsageError(f"group column {c!r} is not in the frame")
        return [c for c in group_cols if c not in counts]
    out: list[str] = []
    for c, dtype in frame.schema.items():
        if c in counts or is_marker_column(c) or RATE_COLUMN_RE.match(c):
            continue
        if dtype.is_float():
            continue  # a measured value, not a key
        out.append(c)
    return out


def _margin_groups(frame: polars.DataFrame, group_cols: Sequence[str]) -> list[list[int]]:
    """Row-index groups sharing the values of one subset of the group columns — every
    proper subset (the grand total ``()`` included) up to
    :data:`MAX_EXHAUSTIVE_GROUP_COLS` columns, else the grand total, the singletons and
    the all-but-one subsets. Singleton groups are dropped (nothing to complement)."""
    n = frame.height
    subsets: list[tuple[str, ...]] = [()]
    cols = list(group_cols)
    if len(cols) <= MAX_EXHAUSTIVE_GROUP_COLS:
        for size in range(1, len(cols)):
            subsets.extend(itertools.combinations(cols, size))
    else:
        subsets.extend((c,) for c in cols)
        subsets.extend(tuple(x for x in cols if x != c) for c in cols)
    groups: list[list[int]] = []
    seen: set[tuple[tuple[int, ...], ...]] = set()
    for subset in subsets:
        if not subset:
            members = [list(range(n))]
        else:
            keyed: dict[tuple[Any, ...], list[int]] = {}
            for i, key in enumerate(frame.select(list(subset)).rows()):
                keyed.setdefault(tuple(_hashable(v) for v in key), []).append(i)
            members = list(keyed.values())
        partition = tuple(tuple(m) for m in members if len(m) > 1)
        if not partition or partition in seen:
            continue
        seen.add(partition)
        groups.extend(list(m) for m in partition)
    return groups


def _hashable(value: Any) -> Any:
    try:
        hash(value)
    except TypeError:
        return repr(value)
    return value


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _nested_pairs(values: dict[str, list[Any]], counts: Sequence[str]) -> list[tuple[str, str]]:
    """``(outer, inner)`` count-column pairs where the inner never exceeds the outer on any
    row and is strictly smaller on at least one — a total and a part of it."""
    pairs: list[tuple[str, str]] = []
    for outer, inner in itertools.permutations(counts, 2):
        rows = [
            (a, b)
            for a, b in zip(values[outer], values[inner], strict=True)
            if _is_number(a) and _is_number(b)
        ]
        if rows and all(a >= b for a, b in rows) and any(a > b for a, b in rows):
            pairs.append((outer, inner))
    return pairs


# ---------------------------------------------------------------------------
# suppress (item 1)
# ---------------------------------------------------------------------------


def suppress(
    df: Any,
    k: int = K_DEFAULT,
    count_cols: Sequence[str] | None = None,
    group_cols: Sequence[str] | None = None,
    mode: str = "table",
    complementary: bool = True,
) -> tuple[Any, SuppressionReport]:
    """Complementary k-suppression of an aggregate frame (module docstring, item 1).
    Returns ``(df_out, report)`` in the input's library (pandas in, pandas out).
    ``mode`` is ``"table"`` (cells + markers) or ``"chain"`` (attrition sequence)."""
    if k < 1:
        raise DiscloseUsageError(f"k = {k} is invalid (k >= 1)")
    frame, kind = _to_polars(df)
    if mode == "table":
        out, report = _suppress_table(frame, k, count_cols, group_cols, complementary)
    elif mode == "chain":
        out, report = _suppress_chain(frame, k, count_cols)
    else:
        raise DiscloseUsageError(f"mode {mode!r} is not 'table' or 'chain'")
    return _from_polars(out, kind), report


def _suppress_table(
    frame: polars.DataFrame,
    k: int,
    count_cols: Sequence[str] | None,
    group_cols: Sequence[str] | None,
    complementary: bool,
) -> tuple[polars.DataFrame, SuppressionReport]:
    import polars as pl

    counts = count_columns(frame, count_cols)
    if not counts:
        return frame, SuppressionReport(k, "table", (), 0, 0, 0, ())
    groups = _group_columns(frame, group_cols, counts)
    n = frame.height
    values = {c: frame.get_column(c).to_list() for c in counts}
    markers = {c: f"{c}{MARKER_SUFFIX}" for c in counts}
    # already hidden on entry: a null, or a cell an earlier pass marked (idempotence)
    hidden: dict[str, list[bool]] = {}
    marked_before: dict[str, list[bool]] = {}
    for c in counts:
        before = (
            [bool(x) for x in frame.get_column(markers[c]).fill_null(False).to_list()]
            if markers[c] in frame.columns
            else [False] * n
        )
        marked_before[c] = before
        hidden[c] = [values[c][i] is None or before[i] for i in range(n)]
    kinds: dict[str, list[str | None]] = {c: [None] * n for c in counts}
    cells: list[SuppressedCell] = []

    def hide(row: int, col: str, kind: str) -> None:
        hidden[col][row] = True
        kinds[col][row] = kind
        cells.append(SuppressedCell(row, col, kind))

    for c in counts:
        for i, v in enumerate(values[c]):
            if not hidden[c][i] and _is_number(v) and 0 < v < k:
                hide(i, c, "primary")
    if complementary and k > 1:
        margin_groups = _margin_groups(frame, groups)
        nested = _nested_pairs(values, counts)
        damaged = [any(hidden[c][i] for c in counts) for i in range(n)]
        changed = True
        while changed:
            changed = False
            for c in counts:
                for members in margin_groups:
                    hidden_rows = [i for i in members if hidden[c][i]]
                    if len(hidden_rows) != 1:
                        continue
                    candidates = [
                        i for i in members if not hidden[c][i] and _is_number(values[c][i])
                    ]
                    if not candidates:
                        continue
                    pick = min(
                        candidates,
                        key=lambda i, c=c: (
                            0 if damaged[i] else 1,
                            0 if values[c][i] > 0 else 1,
                            values[c][i],
                            i,
                        ),
                    )
                    hide(pick, c, "complementary")
                    damaged[pick] = True
                    changed = True
            for outer, inner in nested:
                for i in range(n):
                    if hidden[outer][i] or hidden[inner][i]:
                        continue
                    a, b = values[outer][i], values[inner][i]
                    if _is_number(a) and _is_number(b) and 0 < a - b < k:
                        hide(i, inner, "derived")
                        damaged[i] = True
                        changed = True
    # rates: blank beside a hidden count (a share would restore it)
    rate_cols = [c for c in frame.columns if RATE_COLUMN_RE.match(c) and c not in counts]
    lost = [any(kinds[c][i] is not None for c in counts) for i in range(n)]
    new_columns: list[Any] = []
    for c in counts:
        series = frame.get_column(c)
        out_values = [None if hidden[c][i] else values[c][i] for i in range(n)]
        new_columns.append(pl.Series(c, out_values, dtype=series.dtype))
        marker_values = [marked_before[c][i] or kinds[c][i] is not None for i in range(n)]
        new_columns.append(pl.Series(markers[c], marker_values, dtype=pl.Boolean))
    for c in rate_cols:
        series = frame.get_column(c)
        raw = series.to_list()
        new_columns.append(
            pl.Series(c, [None if lost[i] else raw[i] for i in range(n)], dtype=series.dtype)
        )
        cells.extend(
            SuppressedCell(i, c, "rate") for i in range(n) if lost[i] and raw[i] is not None
        )
    out = frame.with_columns(new_columns)
    # marker columns sit right after their count column (new ones only)
    order: list[str] = []
    for c in frame.columns:
        order.append(c)
        if c in markers and markers[c] not in frame.columns:
            order.append(markers[c])
    out = out.select(order)
    n_primary = sum(1 for x in cells if x.kind == "primary")
    n_complementary = sum(1 for x in cells if x.kind in ("complementary", "derived"))
    return out, SuppressionReport(
        k, "table", tuple(counts), n_primary, n_complementary, 0, tuple(cells)
    )


def band(value: int, width: int = BAND_WIDTH) -> int:
    """``value`` rounded to the nearest multiple of ``width`` (half up)."""
    return int((value + width // 2) // width * width)


def _suppress_chain(
    frame: polars.DataFrame, k: int, count_cols: Sequence[str] | None
) -> tuple[polars.DataFrame, SuppressionReport]:
    import polars as pl

    counts = count_columns(frame, count_cols)
    if not counts:
        raise DiscloseUsageError("chain mode needs one count column (none detected)")
    c = counts[0]
    series = frame.get_column(c)
    values = series.to_list()
    if any(v is None or not _is_number(v) for v in values):
        raise DiscloseUsageError(f"chain mode: column {c!r} must hold integer counts, no nulls")
    totals = [int(v) for v in values]
    if any(b > a for a, b in itertools.pairwise(totals)):
        raise DiscloseUsageError(
            f"chain mode: column {c!r} must be non-increasing (n_0 >= n_1 >= ...)"
        )
    n = len(totals)
    hidden = [0 < v < k for v in totals]
    banded = [False] * n
    drop_hidden = [False] * n
    drops: list[int | None] = [None] * n
    cells: list[SuppressedCell] = []
    for i in range(1, n):
        d = totals[i - 1] - totals[i]
        if 0 < d < k:
            drop_hidden[i] = True
            cells.append(SuppressedCell(i, DROP_COLUMN, "drop"))
            for j in (i - 1, i):
                if not hidden[j] and not banded[j]:
                    banded[j] = True
                    cells.append(SuppressedCell(j, c, "band"))

    def exact(j: int) -> bool:
        return not hidden[j] and not banded[j]

    for i in range(1, n):
        if drop_hidden[i]:
            continue
        if exact(i - 1) and exact(i):
            drops[i] = totals[i - 1] - totals[i]
        else:
            drop_hidden[i] = True
            cells.append(SuppressedCell(i, DROP_COLUMN, "complementary"))
    for i in range(n):
        if hidden[i]:
            cells.append(SuppressedCell(i, c, "primary"))
    out_values = [
        None if hidden[i] else (band(totals[i]) if banded[i] else totals[i]) for i in range(n)
    ]
    out = frame.with_columns(
        pl.Series(c, out_values, dtype=series.dtype),
        pl.Series(f"{c}{MARKER_SUFFIX}", hidden, dtype=pl.Boolean),
        pl.Series(f"{c}{BAND_SUFFIX}", banded, dtype=pl.Boolean),
        pl.Series(DROP_COLUMN, drops, dtype=pl.Int64),
        pl.Series(DROP_MARKER, drop_hidden, dtype=pl.Boolean),
    )
    n_primary = sum(1 for x in cells if x.kind in ("primary", "drop"))
    n_complementary = sum(1 for x in cells if x.kind == "complementary")
    n_banded = sum(1 for x in cells if x.kind == "band")
    return out, SuppressionReport(
        k, "chain", (c,), n_primary, n_complementary, n_banded, tuple(cells)
    )


def render_cell(n: int | float | None, k: int = K_DEFAULT, *, banded: bool = False) -> str:
    """``<11`` for a suppressed cell, ``~1,000`` for a banded chain total, else the
    thousands-separated integer (``inventory.fmt_int``, the committed-text canon)."""
    from mimicwarehouse.inventory import fmt_int

    if n is None:
        return f"<{k}"
    text = fmt_int(int(n))
    return f"~{text}" if banded else text


def safe_suppressor(
    df: polars.DataFrame, k: int, count_columns: list[str]
) -> tuple[polars.DataFrame, int]:
    """The ``safe.SUPPRESSOR`` adapter (EP-33 amendment c): :func:`suppress` over the
    audited count columns, released row-wise — every row with a hidden cell is withheld,
    so the aggregates it carries (min / max / median over the small group, SGT-2) leave
    with it; returns ``(kept, rows_withheld)`` exactly like ``safe.rowwise_suppress``."""
    import polars as pl

    cols = [c for c in count_columns if c in df.columns and df.schema[c].is_numeric()]
    if not cols or k <= 1 or df.is_empty():
        return df, 0
    out, report = suppress(df, k=k, count_cols=cols, complementary=True)
    markers = [f"{c}{MARKER_SUFFIX}" for c in cols]
    withheld = pl.any_horizontal(*(pl.col(m) for m in markers))
    kept = out.filter(~withheld).drop(markers)
    return kept.select(df.columns), report.rows_suppressed


def warn_badges(df: Any, k: int = K_DEFAULT, count_cols: Sequence[str] | None = None) -> Any:
    """Per-cell warn flags for the app (EP-58): a boolean frame with the input's columns,
    ``True`` where a count-like cell lies in ``0 < n < k`` (every other cell ``False``)."""
    import polars as pl

    frame, kind = _to_polars(df)
    counts = set(count_columns(frame, count_cols))
    columns = [
        (pl.col(c).is_between(1, k - 1, closed="both").fill_null(False)).alias(c)
        if c in counts
        else pl.lit(False).alias(c)
        for c in frame.columns
    ]
    return _from_polars(frame.select(columns), kind)


# ---------------------------------------------------------------------------
# check (item 2) — frames
# ---------------------------------------------------------------------------


def _identifier_names() -> frozenset[str]:
    from mimicwarehouse.safe import identifier_column_names

    return IDENTIFIER_COLUMN_NAMES | identifier_column_names()


def _free_text_names() -> frozenset[str]:
    from mimicwarehouse.safe import _contract_names

    return _contract_names()[1]


def _label_names() -> frozenset[str]:
    from mimicwarehouse.safe import LABEL_COLUMN_NAMES

    return LABEL_COLUMN_NAMES


def _sanitize(text: str) -> str:
    from mimicwarehouse.safe import sanitize_error_text

    return sanitize_error_text(text)


def is_identifier_name(name: str) -> bool:
    """Brief item 2a: a known identifier name, or ``id`` / ``*_id`` outside the allow-list."""
    folded = name.strip().casefold()
    if folded in ID_NAME_ALLOW:
        return False
    return folded in _identifier_names() or ID_SHAPE_RE.match(folded) is not None


def _band_hits_in_values(values: Iterable[Any]) -> tuple[int, str | None]:
    """``(count, masked_example)`` of integer values inside a real-id band."""
    count = 0
    example: str | None = None
    for v in values:
        if isinstance(v, bool) or not isinstance(v, int):
            continue
        if guard.band_of(v) is not None:
            count += 1
            if example is None:
                example = guard.mask(str(v))
    return count, example


def _band_hits_in_text(text: str) -> tuple[int, str | None]:
    count = 0
    example: str | None = None
    for match in guard.ID_TOKEN.finditer(text):
        token = match.group()
        if guard.band_of(guard.token_value(token)) is not None:
            count += 1
            if example is None:
                example = guard.mask(token)
    return count, example


def check_frame(
    df: Any, k: int = K_DEFAULT, *, allow_text: Sequence[str] = (), where: str = ""
) -> list[Finding]:
    """The frame rules (item 2a-d) over a pandas / Polars frame: identifier columns,
    band values in non-count integer columns and in string values, free text, unmarked
    small cells and derivable differences of nested totals."""
    import polars as pl

    frame, _kind = _to_polars(df)
    findings: list[Finding] = []
    prefix = f"{where}: " if where else ""
    allowed_text = {c.casefold() for c in allow_text} | _label_names()
    free_text_names = _free_text_names()
    identifier_hits: set[str] = set()
    for name in frame.columns:
        if is_identifier_name(name):
            identifier_hits.add(name)
            findings.append(
                Finding(
                    "ID_COL",
                    FAIL,
                    f"{prefix}column {name!r}",
                    "identifier column name (GOVERNANCE §4) - aggregate it away",
                )
            )
    counts = [c for c in count_columns(frame) if c not in identifier_hits]
    numeric_counts = counts
    for name, dtype in frame.schema.items():
        if name in identifier_hits or is_marker_column(name):
            continue
        if dtype.is_integer() and not NOT_ID_COLUMN_RE.match(name) and not is_count_column(name):
            count, example = _band_hits_in_values(frame.get_column(name).to_list())
            if count:
                findings.append(
                    Finding(
                        "ID_BAND",
                        FAIL,
                        f"{prefix}column {name!r}",
                        f"{count} value(s) in a real MIMIC id band ({example}); fixture ids are "
                        f">= {guard.FIXTURE_ID_FLOOR:_}",
                    )
                )
        elif dtype == pl.String:
            series = frame.get_column(name).drop_nulls()
            folded = name.casefold()
            if (
                FREE_TEXT_COLUMN_RE.match(name) or folded in free_text_names
            ) and folded not in allowed_text:
                findings.append(
                    Finding(
                        "FREE_TEXT",
                        FAIL,
                        f"{prefix}column {name!r}",
                        "column name marks free text (GOVERNANCE §4/§9)",
                    )
                )
                continue
            unique = series.unique()
            count, example = _band_hits_in_text("\n".join(str(v) for v in unique.to_list()[:20000]))
            if count:
                findings.append(
                    Finding(
                        "ID_BAND",
                        FAIL,
                        f"{prefix}column {name!r}",
                        f"{count} string value(s) carry a real MIMIC id band token ({example})",
                    )
                )
            if folded in allowed_text or series.is_empty():
                continue
            lengths = series.str.len_chars()
            max_len, median_len = lengths.max(), lengths.median()
            longest = int(max_len) if isinstance(max_len, int | float) else 0
            median = float(median_len) if isinstance(median_len, int | float) else 0.0
            n_unique = int(unique.len())
            if longest > FREE_TEXT_MAX_CHARS or median > FREE_TEXT_MEDIAN_CHARS:
                findings.append(
                    Finding(
                        "FREE_TEXT",
                        FAIL,
                        f"{prefix}column {name!r}",
                        f"longest value {longest} chars (> {FREE_TEXT_MAX_CHARS}), median "
                        f"{median:.0f} - free text, or a label to allow with --allow-text",
                    )
                )
            elif n_unique > FREE_TEXT_MAX_DISTINCT:
                findings.append(
                    Finding(
                        "FREE_TEXT",
                        FAIL,
                        f"{prefix}column {name!r}",
                        f"{n_unique} distinct values (> {FREE_TEXT_MAX_DISTINCT}) - free text, "
                        "or a dictionary label to allow with --allow-text",
                    )
                )
    values = {c: frame.get_column(c).to_list() for c in numeric_counts}
    for c in numeric_counts:
        marker = f"{c}{MARKER_SUFFIX}"
        marks = (
            [bool(x) for x in frame.get_column(marker).fill_null(False).to_list()]
            if marker in frame.columns
            else [False] * frame.height
        )
        small = [i for i, v in enumerate(values[c]) if _is_number(v) and 0 < v < k and not marks[i]]
        if small:
            rows = ", ".join(str(i + 1) for i in small[:5]) + (" ..." if len(small) > 5 else "")
            findings.append(
                Finding(
                    "SMALL_CELL",
                    FAIL,
                    f"{prefix}column {c!r}",
                    f"{len(small)} cell(s) in 1..{k - 1} without a suppression marker (row(s) "
                    f"{rows}); run disclose.suppress first",
                )
            )
        stale = [i for i, v in enumerate(values[c]) if marks[i] and v is not None]
        if stale:
            findings.append(
                Finding(
                    "SMALL_CELL",
                    FAIL,
                    f"{prefix}column {c!r}",
                    f"{len(stale)} cell(s) marked suppressed but still carrying a value",
                )
            )
    for outer, inner in _nested_pairs(values, numeric_counts):
        derived = [
            i
            for i, (a, b) in enumerate(zip(values[outer], values[inner], strict=True))
            if _is_number(a) and _is_number(b) and 0 < a - b < k
        ]
        if derived:
            rows = ", ".join(str(i + 1) for i in derived[:5]) + (" ..." if len(derived) > 5 else "")
            findings.append(
                Finding(
                    "SMALL_CELL",
                    FAIL,
                    f"{prefix}columns {outer!r} - {inner!r}",
                    f"{len(derived)} row(s) whose published difference lies in 1..{k - 1} "
                    f"(row(s) {rows}) - a derivable small cell (EP-33 amendment b)",
                )
            )
    return findings


def assert_clean(df: Any, k: int = K_DEFAULT, *, allow_text: Sequence[str] = ()) -> CheckResult:
    """In-process gate for EP-59 / EP-130: :func:`check_frame`, raising
    :class:`DisclosureError` naming every failing finding; returns the result otherwise."""
    findings = check_frame(df, k, allow_text=allow_text)
    result = CheckResult("<frame>", k, tuple(findings), tuple(allow_text))
    if not result.passed:
        lines = "; ".join(f"{f.code} {f.where}: {f.detail}" for f in findings if f.status == FAIL)
        raise DisclosureError(f"frame refused by the disclosure gate: {lines}")
    return result


# ---------------------------------------------------------------------------
# check (item 2) — artefacts
# ---------------------------------------------------------------------------


def artefact_suffix(path: Path) -> str:
    """The lower-cased extension the checker dispatches on (``.vl.json`` → ``.json``)."""
    return path.suffix.lower()


def check(path: Path | str, k: int = K_DEFAULT, *, allow_text: Sequence[str] = ()) -> CheckResult:
    """The release gate over one artefact (module docstring, item 2). Raises
    :class:`DiscloseUsageError` for a missing path, an unsupported extension or an
    unreadable file (parser / engine text sanitised)."""
    target = Path(path)
    if not target.is_file():
        raise DiscloseUsageError(f"no such file: {target}")
    if k < 1:
        raise DiscloseUsageError(f"k = {k} is invalid (k >= 1)")
    suffix = artefact_suffix(target)
    if suffix not in SUPPORTED_SUFFIXES:
        raise DiscloseUsageError(
            f"unsupported artefact type {suffix or '(none)'!r}; supported: "
            + " ".join(SUPPORTED_SUFFIXES)
        )
    allow = tuple(allow_text)
    findings: list[Finding] = []
    if suffix in TABULAR_SUFFIXES:
        findings += _check_tabular(target, suffix, k, allow)
    elif suffix == ".json":
        findings += _check_json(target, k, allow)
    elif suffix in (".yaml", ".yml"):
        findings += _check_yaml(target, k, allow)
    elif suffix == ".md":
        findings += _check_markdown(target, k, allow)
    elif suffix in (".html", ".htm"):
        findings += _check_html(target, k, allow)
    elif suffix == ".svg":
        findings += _check_svg(target, k, allow)
    elif suffix == ".png":
        findings += _check_image(target, k, allow)
    else:  # .mmd, .txt
        findings += _check_text(target, k)
    return CheckResult(str(target), k, tuple(findings), allow)


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise DiscloseUsageError(f"cannot read {path.name}: {_sanitize(str(exc))}") from None


def _check_tabular(path: Path, suffix: str, k: int, allow: tuple[str, ...]) -> list[Finding]:
    import polars as pl

    try:
        frame = (
            pl.read_csv(path, infer_schema_length=10_000)
            if suffix == ".csv"
            else pl.read_parquet(path)
        )
    except Exception as exc:  # polars / pyarrow errors quote cell values (DKB-2)
        raise DiscloseUsageError(
            f"cannot load {path.name}: {type(exc).__name__}: {_sanitize(str(exc))}"
        ) from None
    return check_frame(frame, k, allow_text=allow)


def _text_findings(text: str, k: int, *, scan_prose: bool = True) -> list[Finding]:
    """Line-wise id-band scan (the guard's scanner) + the ``n = 4`` prose rule."""
    findings: list[Finding] = []
    hits = guard.id_band_hits(text.encode("utf-8", errors="replace"))
    for no, band_name, count, example in hits[:MAX_BAND_ROWS]:
        plural = "s" if count != 1 else ""
        findings.append(
            Finding(
                "ID_BAND",
                FAIL,
                f"line {no}",
                f"{count} token{plural} in the {band_name} band ({example}); real MIMIC id? "
                f"fixture ids are >= {guard.FIXTURE_ID_FLOOR:_}",
            )
        )
    if len(hits) > MAX_BAND_ROWS:
        findings.append(
            Finding("ID_BAND", FAIL, "file", f"... and {len(hits) - MAX_BAND_ROWS} more line(s)")
        )
    if scan_prose:
        for no, line in enumerate(text.splitlines(), start=1):
            for match in PROSE_COUNT_RE.finditer(line):
                value = int(match.group(2).replace(",", ""))
                if 0 < value < k:
                    findings.append(
                        Finding(
                            "SMALL_CELL",
                            FAIL,
                            f"line {no}",
                            f"'{match.group(1)} = <count>' names a count in 1..{k - 1}; "
                            f"render it as <{k} (disclose.render_cell)",
                        )
                    )
    return findings


def _check_text(path: Path, k: int) -> list[Finding]:
    return _text_findings(_read_text(path), k)


# ---- Markdown / HTML tables -----------------------------------------------------


def _split_markdown_row(line: str) -> list[str]:
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [_clean_cell(cell) for cell in stripped.split("|")]


def _clean_cell(cell: str) -> str:
    text = cell.strip()
    if text.startswith("`") and text.endswith("`") and len(text) >= 2:
        text = text[1:-1].strip()
    if text.startswith("**") and text.endswith("**") and len(text) >= 4:
        text = text[2:-2].strip()
    return text


def markdown_tables(text: str) -> list[tuple[int, list[str], list[list[str]]]]:
    """``(header line number, header cells, body rows)`` per pipe table in ``text``."""
    lines = text.splitlines()
    tables: list[tuple[int, list[str], list[list[str]]]] = []
    i = 0
    while i < len(lines) - 1:
        line = lines[i]
        if line.lstrip().startswith("|") and MARKDOWN_SEPARATOR_RE.match(lines[i + 1].strip()):
            header = _split_markdown_row(line)
            rows: list[list[str]] = []
            j = i + 2
            while j < len(lines) and lines[j].lstrip().startswith("|"):
                cells = _split_markdown_row(lines[j])
                cells = (cells + [""] * len(header))[: len(header)]
                rows.append(cells)
                j += 1
            tables.append((i + 1, header, rows))
            i = j
        else:
            i += 1
    return tables


def _cell_int(cell: str) -> int | None:
    text = cell.strip()
    if INT_CELL_RE.match(text):
        return int(text.replace(",", ""))
    return None


def _cell_suppressed(cell: str) -> bool:
    return SUPPRESSED_CELL_RE.match(cell.strip()) is not None


def check_table(
    header: Sequence[str],
    rows: Sequence[Sequence[str]],
    k: int = K_DEFAULT,
    *,
    allow_text: Sequence[str] = (),
    where: str = "table",
) -> list[Finding]:
    """The text-table rules (Markdown / HTML): identifier headers, free-text cells,
    unmarked integer cells in ``1..k-1`` under non-exempt headers, derivable differences
    of nested count columns and derivable attrition drops."""
    findings: list[Finding] = []
    allowed_text = {c.casefold() for c in allow_text} | _label_names()
    columns = list(header)
    for j, name in enumerate(columns):
        if name and is_identifier_name(name):
            findings.append(
                Finding(
                    "ID_COL", FAIL, f"{where}, column {j + 1} {name!r}", "identifier column name"
                )
            )
    ints: dict[int, list[int | None]] = {}
    for j, name in enumerate(columns):
        cells = [row[j] if j < len(row) else "" for row in rows]
        folded = name.casefold()
        if FREE_TEXT_COLUMN_RE.match(name) and folded not in allowed_text:
            findings.append(
                Finding(
                    "FREE_TEXT",
                    FAIL,
                    f"{where}, column {j + 1} {name!r}",
                    "column name marks free text (GOVERNANCE §4/§9)",
                )
            )
            continue
        if folded not in allowed_text:
            longest = max((len(c) for c in cells), default=0)
            if longest > FREE_TEXT_MAX_CHARS:
                findings.append(
                    Finding(
                        "FREE_TEXT",
                        FAIL,
                        f"{where}, column {j + 1} {name!r}",
                        f"longest cell {longest} chars (> {FREE_TEXT_MAX_CHARS}) - free text, or a "
                        "label to allow with --allow-text",
                    )
                )
        if EXEMPT_HEADER_RE.search(name) and not is_count_column(name):
            continue
        parsed = [_cell_int(c) for c in cells]
        small = [i for i, v in enumerate(parsed) if v is not None and 0 < v < k]
        if small:
            rows_text = ", ".join(str(i + 1) for i in small[:5]) + (
                " ..." if len(small) > 5 else ""
            )
            findings.append(
                Finding(
                    "SMALL_CELL",
                    FAIL,
                    f"{where}, column {j + 1} {name or '(unnamed)'!r}",
                    f"{len(small)} integer cell(s) in 1..{k - 1} (row(s) {rows_text}); render "
                    f"suppressed cells as <{k}",
                )
            )
        if any(v is not None for v in parsed) and all(
            v is not None or _cell_suppressed(c) for v, c in zip(parsed, cells, strict=True)
        ):
            ints[j] = parsed
    # nested count columns: a derivable difference
    for a, b in itertools.permutations(sorted(ints), 2):
        pairs = [
            (x, y) for x, y in zip(ints[a], ints[b], strict=True) if x is not None and y is not None
        ]
        if not pairs or not all(x >= y for x, y in pairs) or not any(x > y for x, y in pairs):
            continue
        derived = [i for i, (x, y) in enumerate(pairs) if 0 < x - y < k]
        if derived:
            findings.append(
                Finding(
                    "SMALL_CELL",
                    FAIL,
                    f"{where}, columns {columns[a]!r} - {columns[b]!r}",
                    f"{len(derived)} row(s) whose published difference lies in 1..{k - 1} - a "
                    "derivable small cell (EP-33 amendment b)",
                )
            )
    # attrition chain: derivable drops
    if columns and CHAIN_HEADER_RE.match(columns[0].strip()):
        for j, parsed in ints.items():
            if j == 0 or not is_count_column(columns[j]):
                continue
            totals = [v for v in parsed if v is not None]
            if len(totals) < 2 or len(totals) != len(parsed):
                continue
            if any(y > x for x, y in itertools.pairwise(totals)):
                continue
            drops = [i for i, (x, y) in enumerate(itertools.pairwise(totals)) if 0 < x - y < k]
            if drops:
                findings.append(
                    Finding(
                        "SMALL_CELL",
                        FAIL,
                        f"{where}, column {j + 1} {columns[j]!r}",
                        f"{len(drops)} attrition drop(s) in 1..{k - 1} derivable from consecutive "
                        "totals; use disclose.suppress(mode='chain')",
                    )
                )
    return findings


def _check_markdown(path: Path, k: int, allow: tuple[str, ...]) -> list[Finding]:
    text = _read_text(path)
    findings = _text_findings(text, k)
    for n, (line_no, header, rows) in enumerate(markdown_tables(text), start=1):
        findings += check_table(
            header, rows, k, allow_text=allow, where=f"table {n} (line {line_no})"
        )
    return findings


class _TableCollector(html.parser.HTMLParser):
    """Collects ``<table>`` rows (cell text) — stdlib only."""

    def __init__(self) -> None:
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self._rows: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag == "table":
            self._rows = []
        elif tag == "tr" and self._rows is not None:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(_clean_cell("".join(self._cell)))
            self._cell = None
        elif tag == "tr" and self._row is not None and self._rows is not None:
            self._rows.append(self._row)
            self._row = None
        elif tag == "table" and self._rows is not None:
            self.tables.append(self._rows)
            self._rows = None

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)


def _html_tables(text: str) -> list[tuple[list[str], list[list[str]]]]:
    collector = _TableCollector()
    try:
        collector.feed(text)
        collector.close()
    except Exception:  # a malformed document is scanned as text only
        return []
    out: list[tuple[list[str], list[list[str]]]] = []
    for rows in collector.tables:
        if not rows:
            continue
        header, body = rows[0], rows[1:]
        body = [(r + [""] * len(header))[: len(header)] for r in body]
        out.append((header, body))
    return out


def _check_html(path: Path, k: int, allow: tuple[str, ...]) -> list[Finding]:
    text = _read_text(path)
    findings = _text_findings(SCRIPT_RE.sub("", text), k)
    for n, (header, rows) in enumerate(_html_tables(text), start=1):
        findings += check_table(header, rows, k, allow_text=allow, where=f"html table {n}")
    findings += _embedded_array_findings(text, k, allow)
    return findings


def _check_svg(path: Path, k: int, allow: tuple[str, ...]) -> list[Finding]:
    text = _read_text(path)
    findings = _image_size_findings(path)
    findings += _text_findings(SCRIPT_RE.sub("", text), k)
    findings += _embedded_array_findings(text, k, allow)
    findings += _source_sibling_findings(path, k, allow)
    return findings


def _check_image(path: Path, k: int, allow: tuple[str, ...]) -> list[Finding]:
    findings = _image_size_findings(path)
    findings += _source_sibling_findings(path, k, allow)
    return findings


def _image_size_findings(path: Path) -> list[Finding]:
    size = path.stat().st_size
    if size > IMAGE_FAIL_BYTES:
        return [
            Finding(
                "OVERSIZE",
                FAIL,
                path.name,
                f"{size / (1024 * 1024):.1f} MiB exceeds the {IMAGE_FAIL_BYTES // 1024} KiB "
                "commit bound (guard G5)",
            )
        ]
    if size > IMAGE_WARN_BYTES:
        return [
            Finding(
                "OVERSIZE",
                WARN,
                path.name,
                f"{size / (1024 * 1024):.1f} MiB - a rendered aggregate figure is small; does "
                "the image embed data?",
            )
        ]
    return []


def source_siblings(path: Path) -> list[Path]:
    """The source-table candidates of a figure: ``<stem>.vl.json`` / ``.json`` / ``.csv``
    / ``.parquet`` beside it (``fig.png`` → ``fig.vl.json`` …)."""
    stem = path.name[: -len(path.suffix)] if path.suffix else path.name
    return [path.with_name(stem + suffix) for suffix in SOURCE_SIBLING_SUFFIXES]


def _source_sibling_findings(path: Path, k: int, allow: tuple[str, ...]) -> list[Finding]:
    present = [p for p in source_siblings(path) if p.is_file()]
    if not present:
        return [
            Finding(
                "NO_SOURCE",
                FAIL,
                path.name,
                "no sibling source table (" + " / ".join(SOURCE_SIBLING_SUFFIXES) + ") - a figure "
                "is released with the aggregate it renders (EP-59 writes the pair)",
            )
        ]
    sibling = present[0]
    try:
        inner = check(sibling, k, allow_text=allow)
    except DiscloseUsageError as exc:
        return [Finding("NO_SOURCE", FAIL, sibling.name, f"source table unreadable: {exc}")]
    return [
        Finding(f.code, f.status, f"source {sibling.name}: {f.where}", f.detail)
        for f in inner.findings
    ]


# ---- JSON / YAML / embedded arrays ----------------------------------------------


def _check_json(path: Path, k: int, allow: tuple[str, ...]) -> list[Finding]:
    text = _read_text(path)
    try:
        doc = json.loads(text)
    except ValueError as exc:
        raise DiscloseUsageError(f"cannot parse {path.name}: {_sanitize(str(exc))}") from None
    return _walk(doc, "$", k, allow)


def _check_yaml(path: Path, k: int, allow: tuple[str, ...]) -> list[Finding]:
    import yaml

    text = _read_text(path)
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise DiscloseUsageError(f"cannot parse {path.name}: {_sanitize(str(exc))}") from None
    return _walk(doc, "$", k, allow)


def _embedded_array_findings(text: str, k: int, allow: tuple[str, ...]) -> list[Finding]:
    """Item 2e over ``<script>`` bodies: whole-JSON scripts and the JSON values after
    ``"values":`` / ``"data":`` / ``"datasets":`` / ``Plotly.newPlot(id,``."""
    findings: list[Finding] = []
    for n, match in enumerate(SCRIPT_RE.finditer(text), start=1):
        body = match.group(1)
        doc = _try_json(body.strip())
        if doc is not None:
            findings += _walk(doc, f"script {n}", k, allow)
            continue
        scanned: list[tuple[int, int]] = []
        for hit in EMBEDDED_ARRAY_RE.finditer(body):
            start = hit.end()
            end = _match_bracket(body, start)
            if end is None or any(lo <= start < hi for lo, hi in scanned):
                continue  # already covered by an enclosing value
            scanned.append((start, end))
            doc = _try_json(body[start:end])
            if doc is not None:
                label = hit.group(1).strip().rstrip(":").strip().strip('"') or "array"
                findings += _walk(doc, f"script {n} {label}", k, allow)
    return findings


def _try_json(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        return None


def _match_bracket(text: str, start: int) -> int | None:
    """Index just past the bracket closing the one at ``start`` (strings respected)."""
    if start >= len(text) or text[start] not in "[{":
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
            if depth == 0:
                return i + 1
    return None


def _walk(obj: Any, where: str, k: int, allow: tuple[str, ...]) -> list[Finding]:
    """Structural scan of a JSON / YAML document (item 2b/2e): record arrays are tables
    (sized, identifier keys refused, then :func:`check_frame`), scalar arrays are sized,
    identifier keys and band integers under non-count keys are refused."""
    findings: list[Finding] = []
    if isinstance(obj, list):
        n = len(obj)
        if n and all(isinstance(x, dict) for x in obj):
            findings += _size_findings(n, where)
            keys: list[str] = []
            for rec in obj[:FRAME_SCAN_MAX_ROWS]:
                for key in rec:
                    if key not in keys:
                        keys.append(str(key))
            id_keys = [key for key in keys if is_identifier_name(key)]
            if id_keys:
                findings.append(
                    Finding(
                        "ID_COL",
                        FAIL,
                        where,
                        f"record array carries identifier key(s) {', '.join(id_keys[:5])}",
                    )
                )
            if n <= FRAME_SCAN_MAX_ROWS:
                frame = _records_frame(obj)
                if frame is not None:
                    findings += [
                        f
                        for f in check_frame(frame, k, allow_text=allow, where=where)
                        if f.code != "ID_COL"
                    ]
                else:
                    for i, rec in enumerate(obj):
                        findings += _walk(rec, f"{where}[{i}]", k, allow)
            return findings
        if n and all(isinstance(x, list) for x in obj):
            findings += _size_findings(n, where)
        elif n and not any(isinstance(x, dict | list) for x in obj):
            findings += _size_findings(n, where)
            count, example = _band_hits_in_values(obj)
            if count and not _count_like_key(where):
                findings.append(
                    Finding(
                        "ID_BAND",
                        FAIL,
                        where,
                        f"{count} integer(s) in a real MIMIC id band ({example})",
                    )
                )
            text_hits, text_example = _band_hits_in_text(
                "\n".join(str(x) for x in obj if isinstance(x, str))
            )
            if text_hits:
                findings.append(
                    Finding(
                        "ID_BAND",
                        FAIL,
                        where,
                        f"{text_hits} string(s) carry a band token ({text_example})",
                    )
                )
            return findings
        for i, item in enumerate(obj):
            if isinstance(item, dict | list):
                findings += _walk(item, f"{where}[{i}]", k, allow)
        return findings
    if isinstance(obj, dict):
        for key, value in obj.items():
            name = str(key)
            child = f"{where}.{name}"
            if value is not None and is_identifier_name(name):
                findings.append(Finding("ID_COL", FAIL, child, "identifier key"))
                continue
            if isinstance(value, dict | list):
                findings += _walk(value, child, k, allow)
            elif isinstance(value, int) and not isinstance(value, bool):
                if guard.band_of(value) is not None and not _count_like_key(name):
                    findings.append(
                        Finding(
                            "ID_BAND",
                            FAIL,
                            child,
                            f"integer in a real MIMIC id band ({guard.mask(str(value))})",
                        )
                    )
            elif isinstance(value, str):
                count, example = _band_hits_in_text(value)
                if count:
                    findings.append(
                        Finding("ID_BAND", FAIL, child, f"string carries a band token ({example})")
                    )
        return findings
    return findings


def _count_like_key(name: str) -> bool:
    leaf = name.rsplit(".", 1)[-1].split("[", 1)[0]
    return is_count_column(leaf) or NOT_ID_COLUMN_RE.match(leaf) is not None


def _size_findings(n: int, where: str) -> list[Finding]:
    if n > EMBEDDED_ROWS_FAIL:
        return [
            Finding(
                "EMBEDDED_ROWS",
                FAIL,
                where,
                f"{n} embedded row(s) (> {EMBEDDED_ROWS_FAIL}) - not an aggregate",
            )
        ]
    if n > EMBEDDED_ROWS_WARN:
        return [
            Finding(
                "EMBEDDED_ROWS",
                WARN,
                where,
                f"{n} embedded row(s) (> {EMBEDDED_ROWS_WARN}) - aggregate further if units",
            )
        ]
    return []


def _records_frame(records: list[dict[str, Any]]) -> polars.DataFrame | None:
    import polars as pl

    flat = [
        {
            str(k): (v if not isinstance(v, dict | list) else json.dumps(v, default=str))
            for k, v in rec.items()
        }
        for rec in records
    ]
    try:
        return pl.DataFrame(flat, strict=False)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Sidecars (item 3)
# ---------------------------------------------------------------------------


def sidecar_path(path: Path | str) -> Path:
    """``<artefact>.disclosure.json`` beside the artefact."""
    target = Path(path)
    return target.with_name(target.name + SIDECAR_SUFFIX)


def sha256_of(path: Path | str) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_sidecar(
    path: Path | str,
    result: CheckResult,
    k: int | None = None,
    reviewer: str = "owner",
    *,
    git_sha: str | None = None,
) -> Path:
    """Write ``<artefact>.disclosure.json`` for a **passing** result (a sidecar is the
    record of a pass; a failing result raises :class:`DiscloseUsageError`)."""
    target = Path(path)
    if not result.passed:
        raise DiscloseUsageError(
            f"{target.name}: {result.n_fail} failing finding(s) - no sidecar is written for a "
            "failing check"
        )
    from mimicwarehouse import __version__
    from mimicwarehouse.fsio import atomic_write_text

    if git_sha is None:
        from mimicwarehouse.dag.runner import git_short_sha

        git_sha = git_short_sha()
    payload = {
        "schema": SIDECAR_SCHEMA,
        "path": target.name,
        "sha256": sha256_of(target),
        "size": target.stat().st_size,
        "k": k if k is not None else result.k,
        "passed": result.passed,
        "checks": [c.as_dict() for c in result.checks],
        "n_warn": result.n_warn,
        "allow_text": list(result.allow_text),
        "reviewer": reviewer,
        "timestamp": datetime.now(UTC).isoformat(timespec="seconds"),
        "tool_version": __version__,
        "git_sha": git_sha,
    }
    out = sidecar_path(target)
    atomic_write_text(out, json.dumps(payload, indent=2) + "\n")
    return out


def read_sidecar(path: Path | str) -> dict[str, Any]:
    """The parsed sidecar of ``path`` (:class:`DiscloseUsageError` when absent / invalid)."""
    side = sidecar_path(path)
    if not side.is_file():
        raise DiscloseUsageError(f"no sidecar {side.name} beside {Path(path).name}")
    try:
        payload = json.loads(side.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DiscloseUsageError(f"sidecar {side.name} unreadable: {_sanitize(str(exc))}") from None
    if not isinstance(payload, dict):
        raise DiscloseUsageError(f"sidecar {side.name} is not a JSON object")
    return payload


def verify(path: Path | str) -> VerifyResult:
    """Re-hash ``path`` against its sidecar: ok iff the sidecar records a pass and the
    sha256 matches the artefact as it is now."""
    target = Path(path)
    side = sidecar_path(target)
    if not target.is_file():
        raise DiscloseUsageError(f"no such file: {target}")
    payload = read_sidecar(target)
    recorded = str(payload.get("sha256") or "")
    current = sha256_of(target)
    if not payload.get("passed", False):
        return VerifyResult(
            str(target), str(side), False, "sidecar records a failing check", recorded, current
        )
    if recorded != current:
        return VerifyResult(
            str(target),
            str(side),
            False,
            "sha256 mismatch - the artefact changed since its disclosure check; re-run "
            "`mwh disclose check --write-sidecar`",
            recorded,
            current,
        )
    return VerifyResult(
        str(target), str(side), True, "sha256 matches the sidecar", recorded, current
    )


# ---------------------------------------------------------------------------
# CLI — mwh disclose check | verify (item 3)
# ---------------------------------------------------------------------------

disclose_app = typer.Typer(
    name="disclose",
    help="Disclosure gate (EP-43): check artefacts for identifiers, free text, small cells "
    "and embedded rows; write / verify .disclosure.json sidecars.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)


def _render_result(result: CheckResult) -> Any:
    from rich.table import Table

    name = Path(result.path).name
    verdict = "PASS" if result.passed else "FAIL"
    table = Table(title=f"mwh disclose check - {name} ({verdict}, k = {result.k})", expand=False)
    table.add_column("code", no_wrap=True)
    table.add_column("status", no_wrap=True)
    table.add_column("detail", overflow="fold")
    for entry in result.checks:
        style = {FAIL: "bold red", WARN: "yellow", PASS: "green"}[entry.status]
        table.add_row(entry.code, f"[{style}]{entry.status.upper()}[/]", entry.detail)
    return table


def _render_findings(result: CheckResult) -> Any:
    from rich.table import Table

    table = Table(title="findings", expand=False)
    table.add_column("code", no_wrap=True)
    table.add_column("status", no_wrap=True)
    table.add_column("where", overflow="fold")
    table.add_column("detail", overflow="fold")
    for f in result.findings:
        style = "bold red" if f.status == FAIL else "yellow"
        table.add_row(f.code, f"[{style}]{f.status.upper()}[/]", f.where, f.detail)
    return table


def _resolve_k(ctx: typer.Context, k: int | None) -> int:
    if k is not None:
        return k
    state = ctx.obj
    if state is not None and getattr(state, "settings", None) is not None:
        return int(state.settings.k_suppression)
    return K_DEFAULT


@disclose_app.command("check")
def check_command(
    ctx: typer.Context,
    paths: Annotated[list[Path], typer.Argument(help="Artefact(s) to check.", show_default=False)],
    k: Annotated[
        int | None,
        typer.Option("--k", help="Small-cell threshold (default: settings.k_suppression = 11)."),
    ] = None,
    write_sidecar_flag: Annotated[
        bool,
        typer.Option(
            "--write-sidecar", help="Write <artefact>.disclosure.json for every passing artefact."
        ),
    ] = False,
    allow_text: Annotated[
        list[str] | None,
        typer.Option(
            "--allow-text",
            help="Column / header name whose long or many-valued strings are dictionary labels "
            "(repeatable).",
            show_default=False,
        ),
    ] = None,
    reviewer: Annotated[str, typer.Option("--reviewer", help="Recorded in the sidecar.")] = "owner",
    json_output: Annotated[bool, typer.Option("--json", help="Print the results as JSON.")] = False,
) -> None:
    """Run the disclosure gate over artefacts (GOVERNANCE section 7). Exit 0 when every
    artefact passes, 1 on any failing finding, 2 on a usage error."""
    from mimicwarehouse.console import EXIT_FINDINGS, EXIT_OK, EXIT_USAGE, console, emit_json, fail

    resolved_k = _resolve_k(ctx, k)
    allow = tuple(allow_text or ())
    results: list[dict[str, Any]] = []
    ok = True
    for path in paths:
        try:
            result = check(path, resolved_k, allow_text=allow)
            sidecar: Path | None = None
            if write_sidecar_flag and result.passed:
                sidecar = write_sidecar(path, result, resolved_k, reviewer)
        except DiscloseUsageError as exc:
            fail("mwh disclose check", str(exc), code=EXIT_USAGE)
        ok = ok and result.passed
        if json_output:
            payload = result.to_dict()
            payload["sidecar"] = str(sidecar) if sidecar else None
            results.append(payload)
            continue
        console.print(_render_result(result))
        if result.findings:
            console.print(_render_findings(result))
        summary = (
            f"mwh disclose check: {Path(result.path).name} "
            f"{'PASS' if result.passed else 'FAIL'} - {result.n_fail} fail, {result.n_warn} warn"
        )
        if sidecar is not None:
            summary += f" - sidecar {sidecar.name}"
        elif write_sidecar_flag and not result.passed:
            summary += " - no sidecar written"
        console.print(summary, style="bold green" if result.passed else "bold red")
    if json_output:
        emit_json({"k": resolved_k, "ok": ok, "results": results})
    raise typer.Exit(code=EXIT_OK if ok else EXIT_FINDINGS)


@disclose_app.command("verify")
def verify_command(
    ctx: typer.Context,
    path: Annotated[
        Path, typer.Argument(help="Artefact whose sidecar to verify.", show_default=False)
    ],
    json_output: Annotated[bool, typer.Option("--json", help="Print the result as JSON.")] = False,
) -> None:
    """Re-hash an artefact against its .disclosure.json sidecar. Exit 0 when it matches,
    1 when it does not (or the sidecar records a failure), 2 without a sidecar."""
    from mimicwarehouse.console import EXIT_FINDINGS, EXIT_OK, EXIT_USAGE, console, emit_json, fail

    try:
        result = verify(path)
    except DiscloseUsageError as exc:
        fail("mwh disclose verify", str(exc), code=EXIT_USAGE)
    if json_output:
        emit_json(result.to_dict())
    else:
        console.print(
            f"mwh disclose verify: {Path(result.path).name} {'OK' if result.ok else 'MISMATCH'} - "
            f"{result.reason}",
            style="bold green" if result.ok else "bold red",
        )
    raise typer.Exit(code=EXIT_OK if result.ok else EXIT_FINDINGS)


__all__ = [
    "BAND_WIDTH",
    "CODES",
    "CODE_TITLES",
    "COUNT_COLUMN_RE",
    "DROP_COLUMN",
    "DROP_MARKER",
    "EMBEDDED_ROWS_FAIL",
    "EMBEDDED_ROWS_WARN",
    "EXEMPT_HEADER_RE",
    "FREE_TEXT_COLUMN_RE",
    "FREE_TEXT_MAX_CHARS",
    "FREE_TEXT_MAX_DISTINCT",
    "FREE_TEXT_MEDIAN_CHARS",
    "IDENTIFIER_COLUMN_NAMES",
    "ID_NAME_ALLOW",
    "IMAGE_FAIL_BYTES",
    "IMAGE_WARN_BYTES",
    "K_DEFAULT",
    "MARKER_SUFFIX",
    "RATE_COLUMN_RE",
    "SIDECAR_SCHEMA",
    "SIDECAR_SUFFIX",
    "SOURCE_SIBLING_SUFFIXES",
    "SUPPORTED_SUFFIXES",
    "CheckEntry",
    "CheckResult",
    "DiscloseUsageError",
    "DisclosureError",
    "Finding",
    "SuppressedCell",
    "SuppressionReport",
    "VerifyResult",
    "assert_clean",
    "band",
    "check",
    "check_frame",
    "check_table",
    "count_columns",
    "disclose_app",
    "is_count_column",
    "is_identifier_name",
    "markdown_tables",
    "read_sidecar",
    "render_cell",
    "safe_suppressor",
    "sha256_of",
    "sidecar_path",
    "source_siblings",
    "summarise",
    "suppress",
    "verify",
    "warn_badges",
    "write_sidecar",
]
