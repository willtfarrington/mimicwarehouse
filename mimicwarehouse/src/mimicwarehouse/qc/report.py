"""The QC report — ``runs/<run_id>/qc_report.md`` + CSV tables (EP-44 item 4; DESIGN
§14/§15, GOVERNANCE §5/§7, D-33, D-40).

The ``qc.report`` DAG step (:func:`run_report`) reads the tier's published
``meta.qc_tables`` / ``meta.qc_columns`` / ``meta.qc_checks`` Parquet (already suppressed
at build time by :mod:`mimicwarehouse.qc.profile`), passes every frame it renders through
``disclose.suppress`` again (idempotent: markers already present are honoured, ``rows``
of the table summary gains its own) and writes, into the ``kind: qc`` run's folder:

* ``qc_report.md`` — the header (``Claim type: exploratory (data-quality profile)``, the
  retrospective sentence, the disclosure line), the table summary, the checks by status,
  the warnings and failures, the unit variants of the curated itemids, the implausible
  shares, the timestamp-ordering rates, "what it deliberately does not claim", and the
  EP-35 reproduction + provenance block (``run.reproduction_block``);
* ``qc_tables.csv`` / ``qc_checks.csv`` — the two frames the sections are rendered from.

Every integer goes through ``inventory.fmt_int`` and every suppressed count renders as
``<k`` (``disclose.render_cell``), so ``mwh disclose check`` passes on every file
(``test_ep44`` asserts it on the fixture) and EP-53 can promote the report with a sidecar.
File names follow ``docs/committed-text.md`` (``qc_report.md``, never
``qc_report_<run_id>.md``); the run id appears inline only.

The module also renders the generated blocks of ``docs/methods/qc.md`` (the check table
and the dictionary-coded list from ``thresholds.yaml``; ``python -m mimicwarehouse.qc``).

Import budget: not on the ``mwh`` start-up path (``qc/cli.py`` imports it inside the
command body); polars, ``disclose`` and ``run`` load inside function bodies.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mimicwarehouse.qc.profile import (
    CHECK_IDS,
    CHECKS_TABLE,
    CLAIM_TYPE,
    COLUMNS_TABLE,
    DAG_TAG,
    STATUSES,
    TABLES_TABLE,
    QcError,
    Thresholds,
    dictionary_coded_columns,
    load_thresholds,
    read_meta_frame,
)

if TYPE_CHECKING:  # pragma: no cover
    from mimicwarehouse.config import Settings
    from mimicwarehouse.dag.runner import StepContext, StepOutcome
    from mimicwarehouse.dag.spec import Step

_LOG = logging.getLogger(__name__)

REPORT_FILENAME = "qc_report.md"
TABLES_CSV = "qc_tables.csv"
CHECKS_CSV = "qc_checks.csv"
REPORT_FILES: tuple[str, ...] = (REPORT_FILENAME, TABLES_CSV, CHECKS_CSV)
#: The retrospective sentence (the EP-31/EP-32/EP-42 wording); ``CLAIM_TYPE`` is the
#: profile module's (the qc run records it too).
RETROSPECTIVE_SENTENCE = "MIMIC-IV analyses are retrospective."
DISCLOSURE_LINE = (
    "Disclosure: every count below passed `disclose.suppress` (k-suppressed at build time, "
    "complementary for the top-k values; a suppressed count renders as `<k`) before "
    "rendering; run `mwh disclose check --write-sidecar` before promoting this file (EP-43)."
)
#: Metrics rendered as percentages (the rest are counts).
SHARE_METRICS: frozenset[str] = frozenset(
    {
        "orphan_share",
        "null_share",
        "violation_share",
        "outside_share",
        "dominant_unit_share",
        "implausible_share",
        "capped_share",
    }
)
NON_CLAIMS: tuple[str, ...] = (
    "It does not judge clinical validity: a value outside the EP-39 plausibility bounds "
    "is a charting artefact or a unit slip until a clinician says otherwise, and the "
    "bounds are wide sanity windows, not reference ranges.",
    "It does not clean anything: duplicates, orphans and out-of-window events are MIMIC-IV "
    "facts recorded here for cohort builders to handle explicitly (EP-46/47); the lake "
    "is never edited.",
    "It says nothing about missingness mechanisms: null shares are structural facts of the "
    "source systems; informative presence and structural absence are EP-45's (measurement "
    "process) and EP-72's (missing-data views).",
    "Percentiles are DuckDB t-digest approximations; distinct counts are HyperLogLog "
    "approximations (EP-29); neither is exact.",
    "It is not a calendar-time analysis: the only cross-patient temporal axis is "
    "anchor_year_group (era coverage); timestamps are per-patient shifted.",
)


@dataclass(slots=True)
class QcReport:
    """The report's inputs: the tier's published QC frames and the run identity."""

    tier: str
    k: int
    run_id: str | None
    build_id: str | None
    snapshot_id: str | None
    tables: Any
    checks: Any
    n_columns: int
    generated: str

    @property
    def status_counts(self) -> dict[str, int]:
        return {
            s: int((self.checks.get_column("status") == s).sum()) if self.checks.height else 0
            for s in STATUSES
        }


def _first(frame: Any, column: str) -> Any:
    if frame.height == 0 or column not in frame.columns:
        return None
    value = frame.get_column(column)[0]
    return None if value is None else value


def load_report(lake_root: Path | str, tier: str) -> QcReport:
    """The report inputs from ``lake/meta/<tier>/qc_*.parquet`` (:class:`QcError` when
    the tables are missing — run ``mwh build --tier <t> --tag qc``)."""
    tables = read_meta_frame(lake_root, tier, TABLES_TABLE)
    checks = read_meta_frame(lake_root, tier, CHECKS_TABLE)
    columns = read_meta_frame(lake_root, tier, COLUMNS_TABLE)
    k = _first(checks, "k")
    return QcReport(
        tier=tier,
        k=int(k) if k is not None else 11,
        run_id=_first(tables, "run_id"),
        build_id=_first(tables, "build_id"),
        snapshot_id=_first(tables, "snapshot_id"),
        tables=tables,
        checks=checks,
        n_columns=columns.height,
        generated=datetime.now(UTC).date().isoformat(),
    )


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------


def _fmt_int(value: Any) -> str:
    from mimicwarehouse.inventory import fmt_int

    return "-" if value is None else fmt_int(int(value))


def _count_cell(value: Any, suppressed: Any, k: int) -> str:
    """A count cell: ``<k`` when its marker says suppressed, ``-`` when absent (no
    denominator), else the thousands-separated integer."""
    from mimicwarehouse.disclose import render_cell

    if suppressed:
        return render_cell(None, k)
    return _fmt_int(value)


def _value_cell(metric: str, value: Any, suppressed: Any = False) -> str:
    if suppressed or value is None:
        return "-"
    if metric in SHARE_METRICS:
        return f"{float(value) * 100:.2f} %"
    return _fmt_int(round(float(value)))


def _threshold_cell(metric: str, value: Any) -> str:
    if value is None:
        return "-"
    if metric in SHARE_METRICS:
        return f"{float(value) * 100:g} %"
    return _fmt_int(round(float(value)))


def _mb(value: Any) -> str:
    return "-" if value is None else f"{float(value) / 1e6:.1f}"


def _text(value: Any) -> str:
    if value is None:
        return "-"
    return str(value).replace("|", "/").replace("\n", " ")


def _md_table(header: list[str], rows: list[list[str]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return lines


def _suppressed_tables(tables: Any, k: int) -> Any:
    """The table summary with ``rows`` k-suppressed (``rows_suppressed`` marker) —
    complementary over the ``(schema, table)`` margins."""
    from mimicwarehouse.disclose import suppress

    if tables.height == 0:
        return tables
    out, _report = suppress(
        tables, k=k, count_cols=["rows"], group_cols=["schema", "table"], complementary=True
    )
    return out


def report_frames(report: QcReport) -> dict[str, Any]:
    """The two CSV frames: the suppressed table summary and the (already suppressed)
    checks frame."""
    return {TABLES_CSV: _suppressed_tables(report.tables, report.k), CHECKS_CSV: report.checks}


# ---------------------------------------------------------------------------
# The Markdown report
# ---------------------------------------------------------------------------


def _qualified(row: dict[str, Any]) -> str:
    return f"{row['schema']}.{row['table']}"


def render_report(
    report: QcReport,
    *,
    settings: Settings | None = None,
    thresholds: Thresholds | None = None,
    reproduction: str | None = None,
) -> str:
    """The ``qc_report.md`` text (module docstring). ``reproduction`` overrides the
    EP-35 block (tests); by default it is rendered from the run id's manifest."""
    from mimicwarehouse.inventory import fmt_int

    thresholds = thresholds or load_thresholds()
    k = report.k
    counts = report.status_counts
    checks = report.checks.to_dicts() if report.checks.height else []
    tables = _suppressed_tables(report.tables, k)
    per_table: dict[str, dict[str, int]] = {}
    for c in checks:
        entry = per_table.setdefault(_qualified(c), dict.fromkeys(STATUSES, 0))
        entry[str(c["status"])] += 1

    lines = [
        "# Data-quality profile (EP-44)",
        "",
        f"**Claim type: {CLAIM_TYPE}.** {RETROSPECTIVE_SENTENCE}",
        "",
        DISCLOSURE_LINE,
        "",
        f"Run `{report.run_id or '-'}` - tier `{report.tier}` - build "
        f"`{report.build_id or '-'}` - core snapshot `{report.snapshot_id or '-'}` - "
        f"generated {report.generated} - k = {fmt_int(k)}.",
        "",
        f"{fmt_int(report.tables.height)} tables and {fmt_int(report.n_columns)} columns "
        f"profiled; {fmt_int(len(checks))} checks evaluated - pass {fmt_int(counts['pass'])} "
        f"/ warn {fmt_int(counts['warn'])} / fail {fmt_int(counts['fail'])} "
        f"(thresholds v{fmt_int(thresholds.version)}, `qc/thresholds.yaml`).",
        "",
        "## Table summary",
        "",
    ]
    table_rows: list[list[str]] = []
    for t in tables.to_dicts():
        qn = f"{t['schema']}.{t['table']}"
        statuses = per_table.get(qn, dict.fromkeys(STATUSES, 0))
        table_rows.append(
            [
                f"`{qn}`",
                _count_cell(t.get("rows"), t.get("rows_suppressed"), k),
                _fmt_int(t.get("columns")),
                _mb(t.get("parquet_bytes")),
                _text(t.get("worst_status")),
                _fmt_int(statuses["fail"]),
                _fmt_int(statuses["warn"]),
            ]
        )
    lines += _md_table(
        ["table", "rows", "columns", "Parquet MB", "worst status", "# fail", "# warn"],
        table_rows,
    )
    lines += ["", "## Checks by status", ""]
    by_check: dict[str, dict[str, int]] = {cid: dict.fromkeys(STATUSES, 0) for cid in CHECK_IDS}
    for c in checks:
        by_check.setdefault(str(c["check_id"]), dict.fromkeys(STATUSES, 0))[str(c["status"])] += 1
    lines += _md_table(
        ["check", "metric", "rule", "# pass", "# warn", "# fail"],
        [
            [
                f"`{cid}`",
                thresholds.check(cid).metric if cid in thresholds.checks else "-",
                thresholds.check(cid).rule() if cid in thresholds.checks else "-",
                _fmt_int(n["pass"]),
                _fmt_int(n["warn"]),
                _fmt_int(n["fail"]),
            ]
            for cid, n in by_check.items()
        ],
    )
    lines += [
        "",
        "Every check is a named function in `qc/profile.py` returning aggregates; "
        "`n_affected` counts rows, never samples them.",
        "",
        "### Warnings and failures",
        "",
    ]
    flagged = [c for c in checks if c["status"] != "pass"]
    flagged.sort(
        key=lambda c: (
            -STATUSES.index(str(c["status"])),
            str(c["check_id"]),
            _qualified(c),
            str(c.get("column") or ""),
            int(c["itemid"] or 0),
        )
    )
    if flagged:
        lines += _md_table(
            [
                "status",
                "check",
                "table",
                "column",
                "itemid",
                "metric",
                "value",
                "threshold",
                "n_affected",
                "detail",
            ],
            [
                [
                    _text(c["status"]),
                    f"`{c['check_id']}`",
                    f"`{_qualified(c)}`",
                    _text(c.get("column")),
                    _fmt_int(c.get("itemid")),
                    _text(c["metric"]),
                    _value_cell(str(c["metric"]), c.get("value"), c.get("n_affected_suppressed")),
                    _threshold_cell(str(c["metric"]), c.get("threshold")),
                    _count_cell(c.get("n_affected"), c.get("n_affected_suppressed"), k),
                    _text(c.get("detail")),
                ]
                for c in flagged
            ],
        )
    else:
        lines.append("No check warned or failed.")
    lines += ["", "## Unit variants (curated itemids)", ""]
    units = [c for c in checks if c["check_id"] == "unit_consistency"]
    if units:
        lines += _md_table(
            [
                "itemid",
                "label",
                "table",
                "dominant unit",
                "dominant share",
                "# unit strings",
                "n_affected",
                "status",
            ],
            [
                [
                    _fmt_int(c.get("itemid")),
                    _text(c.get("label")),
                    f"`{_qualified(c)}`",
                    _dominant_unit(str(c.get("detail") or "")),
                    _value_cell(
                        "dominant_unit_share", c.get("value"), c.get("n_affected_suppressed")
                    ),
                    _unit_count(str(c.get("detail") or "")),
                    _count_cell(c.get("n_affected"), c.get("n_affected_suppressed"), k),
                    _text(c["status"]),
                ]
                for c in units
            ],
        )
        lines += [
            "",
            "`n_affected` = rows charted outside the dominant (normalised) unit string; the "
            "share is over all rows of the itemid on this tier (EP-39 catalogue items only).",
        ]
    else:
        lines.append("No curated itemid was found in the event tables of this tier.")
    lines += ["", "## Implausible values", ""]
    implausible = [c for c in checks if c["check_id"] == "implausible_values"]
    if implausible:
        lines += _md_table(
            ["itemid", "label", "table", "bounds", "share implausible", "n_affected", "status"],
            [
                [
                    _fmt_int(c.get("itemid")),
                    _text(c.get("label")),
                    f"`{_qualified(c)}`",
                    _text(c.get("detail")).removeprefix("bounds "),
                    _value_cell(
                        "implausible_share", c.get("value"), c.get("n_affected_suppressed")
                    ),
                    _count_cell(c.get("n_affected"), c.get("n_affected_suppressed"), k),
                    _text(c["status"]),
                ]
                for c in implausible
            ],
        )
        lines += [
            "",
            "Shares are over rows with a numeric value, after the EP-39 unit conversion "
            "(an unknown unit is judged on its raw value, as `mwh_harmonize` does); bounds "
            "are inclusive, in the canonical unit.",
        ]
    else:
        lines.append("No curated itemid was found in the event tables of this tier.")
    lines += ["", "## Timestamp ordering", ""]
    ordering = [c for c in checks if c["check_id"] in ("ts_order", "ts_store_lag", "event_window")]
    if ordering:
        lines += _md_table(
            ["table", "rule", "check", "share violating", "n_affected", "status"],
            [
                [
                    f"`{_qualified(c)}`",
                    _text(c.get("detail")),
                    f"`{c['check_id']}`",
                    _value_cell(str(c["metric"]), c.get("value"), c.get("n_affected_suppressed")),
                    _count_cell(c.get("n_affected"), c.get("n_affected_suppressed"), k),
                    _text(c["status"]),
                ]
                for c in ordering
            ],
        )
        lines += [
            "",
            "`ts_order` rules are hard orderings (a violation warns; above 1 % fails); "
            "`ts_store_lag` is the back-charting rate (`storetime < charttime`), a warning "
            "only above 10 %; `event_window` is the share of ICU events outside "
            "[intime - 24 h, outtime + 24 h] of their stay.",
        ]
    else:
        lines.append("No timestamp rule applies to the tables of this tier.")
    lines += ["", "## What it deliberately does not claim", ""]
    lines += [f"- {item}" for item in NON_CLAIMS]
    lines.append("")
    if reproduction is None:
        reproduction = _reproduction(report.run_id, settings)
    lines.append(reproduction.rstrip("\n"))
    lines.append("")
    return "\n".join(lines)


def _dominant_unit(detail: str) -> str:
    """``dominant: mmhg; 3 unit string(s)`` -> ``mmhg``."""
    head = detail.split(";", 1)[0]
    return _text(head.removeprefix("dominant: ").strip() or None)


def _unit_count(detail: str) -> str:
    tail = detail.split(";", 1)[1].strip() if ";" in detail else ""
    number = tail.split(" ", 1)[0]
    return number if number.isdigit() else "-"


def _reproduction(run_id: str | None, settings: Settings | None) -> str:
    from mimicwarehouse import run as run_mod

    if run_id is None:
        return "## Reproduction\n\nNo run id recorded (the tables predate the qc run).\n"
    try:
        return run_mod.reproduction_block(run_id, settings)
    except run_mod.RunLedgerError as exc:
        return f"## Reproduction\n\nRun `{run_id}`: manifest unavailable ({exc}).\n"


def write_report(
    report: QcReport,
    out_dir: Path,
    *,
    settings: Settings | None = None,
    thresholds: Thresholds | None = None,
) -> list[Path]:
    """Write ``qc_report.md`` + the two CSVs into ``out_dir``; returns the paths."""
    from mimicwarehouse.fsio import atomic_write_text

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    text = render_report(report, settings=settings, thresholds=thresholds)
    target = out_dir / REPORT_FILENAME
    atomic_write_text(target, text)
    paths.append(target)
    for name, frame in report_frames(report).items():
        csv_path = out_dir / name
        frame.write_csv(csv_path)
        paths.append(csv_path)
    return paths


def run_report(step: Step, ctx: StepContext) -> StepOutcome:
    """The ``qc.report`` handler (module docstring): the tier's published QC tables ->
    ``runs/<run_id>/qc_report.md`` + CSVs, where ``run_id`` is the ``kind: qc`` run
    ``qc.checks`` recorded in ``meta.qc_tables``."""
    from mimicwarehouse.dag.runner import StepOutcome
    from mimicwarehouse.run import run_dir

    report = load_report(ctx.lake_root, ctx.tier)
    if report.run_id is None:
        raise QcError(
            f"meta.qc_tables of tier {ctx.tier} carries no run id — rerun "
            f"`mwh build --tier {ctx.tier} --tag {DAG_TAG}`"
        )
    out_dir = run_dir(report.run_id, ctx.settings)
    paths = write_report(report, out_dir, settings=ctx.settings)
    counts = report.status_counts
    _LOG.info(
        "qc report (%s): %s — %d table(s), %d check(s): pass %d / warn %d / fail %d",
        ctx.tier,
        paths[0],
        report.tables.height,
        report.checks.height,
        counts["pass"],
        counts["warn"],
        counts["fail"],
    )
    return StepOutcome(
        rows=report.checks.height,
        bytes_out=sum(p.stat().st_size for p in paths),
        files=len(paths),
    )


# ---------------------------------------------------------------------------
# docs/methods/qc.md — the generated blocks
# ---------------------------------------------------------------------------

METHODS_DOC_RELPATH = Path("docs") / "methods" / "qc.md"
CHECKS_MARK = ("<!-- checks:begin -->", "<!-- checks:end -->")
DICTIONARY_MARK = ("<!-- dictionary:begin -->", "<!-- dictionary:end -->")


def render_check_table(thresholds: Thresholds | None = None) -> str:
    """The checks as a Markdown table (id, metric, rule, note) from ``thresholds.yaml``."""
    thresholds = thresholds or load_thresholds()
    rows = [
        [f"`{cid}`", f"`{th.metric}`", th.rule(), th.note] for cid, th in thresholds.checks.items()
    ]
    return "\n".join(_md_table(["check", "metric", "rule", "what it counts"], rows)) + "\n"


def render_dictionary_list(thresholds: Thresholds | None = None) -> str:
    """The dictionary-coded columns per table (the automatic FK-to-dimension rule plus
    the declared list) as a Markdown table."""
    rows = [
        [f"`{qn}`", ", ".join(f"`{c}`" for c in columns)]
        for qn, columns in dictionary_coded_columns(thresholds=thresholds).items()
    ]
    return "\n".join(_md_table(["table", "dictionary-coded columns"], rows)) + "\n"


def methods_doc_path() -> Path:
    """``mimicwarehouse/docs/methods/qc.md``."""
    from mimicwarehouse.config import workspace_root

    return workspace_root() / METHODS_DOC_RELPATH


def sync_methods_doc(path: Path | None = None) -> Path:
    """Re-render the generated blocks of the methods page in place (idempotent; the
    narrative around the markers is never touched)."""
    from mimicwarehouse.dag.benchmarks import replace_marked_block

    target = Path(path) if path is not None else methods_doc_path()
    text = target.read_text(encoding="utf-8")
    for (begin, end), block in (
        (CHECKS_MARK, render_check_table()),
        (DICTIONARY_MARK, render_dictionary_list()),
    ):
        text = replace_marked_block(text, block, begin=begin, end=end)
    target.write_text(text.rstrip("\n") + "\n", encoding="utf-8", newline="\n")
    return target


__all__ = [
    "CHECKS_CSV",
    "CHECKS_MARK",
    "CLAIM_TYPE",
    "DICTIONARY_MARK",
    "DISCLOSURE_LINE",
    "METHODS_DOC_RELPATH",
    "NON_CLAIMS",
    "REPORT_FILENAME",
    "REPORT_FILES",
    "RETROSPECTIVE_SENTENCE",
    "SHARE_METRICS",
    "TABLES_CSV",
    "QcReport",
    "load_report",
    "methods_doc_path",
    "render_check_table",
    "render_dictionary_list",
    "render_report",
    "report_frames",
    "run_report",
    "sync_methods_doc",
    "write_report",
]
