"""``mwh qc`` — the data-quality command group (EP-44 item 3, EP-45 item 4; attached in
:mod:`mimicwarehouse.cli`).

``mwh qc status --tier <t>`` reads ``meta.qc_checks`` / ``meta.qc_tables`` through
:func:`mimicwarehouse.safe.safe_query` (``meta.*`` is a registry exemption; the counts
were k-suppressed when the tables were built, EP-44) and prints the pass / warn / fail
counts per check plus the failing (``--show fail``, default), warning (``--show warn``)
or every flagged (``--show all``) check row — aggregates only, every read audited. Exit
codes follow the EP-33 canon (``EXIT_FINDINGS`` 1 when any check failed, so a CI-style
caller can gate on it; refusals 3; usage / no catalog 2).

``mwh qc measurement --tier <t> [--top N]`` (EP-45) reads ``meta.mp_item_summary`` and
``meta.mp_structural`` the same way and prints the per-item measurement summary (stays
measured, share measured in the first 24 h, median occasions per stay-day, the interval
quantiles) and the top structural cells (unit x era cells of at least ``min_stays`` stays
where the item is never charted); a count suppressed at build time reads ``<k``.

Import budget: this module is on the ``mwh --help`` path — typer / rich only; the
profile / report / measurement modules, ``safe`` and polars load inside the command body.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any

import typer
from rich.markup import escape

from mimicwarehouse.console import EXIT_FINDINGS, console, console_safe, emit_json, fail

if TYPE_CHECKING:  # pragma: no cover
    from mimicwarehouse.cli import CliState
    from mimicwarehouse.config import Settings

TIERS = ("fixture", "demo", "dev", "full")
SHOW_CHOICES = ("fail", "warn", "all", "none")
#: The row cap of the flagged-rows read (every check row of a tier is a few hundred).
STATUS_ROW_CAP = 10_000

#: Plain column reads, counted in Python on purpose: a ``count(*)`` over the registry
#: table would put the *number of checks* per status through the k-suppressor (a check
#: id with three rows would vanish); the rows themselves carry no count column but
#: ``n_affected``, suppressed at build time.
_COUNTS_SQL = "SELECT check_id, status FROM meta.qc_checks"
_TABLES_SQL = 'SELECT "schema", "table", worst_status, run_id, build_id FROM meta.qc_tables'
_ROWS_SQL = (
    'SELECT check_id, "schema", "table", "column", itemid, label, metric, value, threshold, '
    "status, n_affected, n_affected_suppressed, k, detail FROM meta.qc_checks "
    "WHERE status IN ({statuses}) "
    'ORDER BY status, check_id, "schema", "table", "column", itemid'
)

qc_app = typer.Typer(
    name="qc",
    help=(
        "Data-quality profiling (EP-44/45): mwh qc status --tier <t> [--show fail|warn|all|none] "
        "prints the pass / warn / fail counts of meta.qc_checks and the flagged checks; "
        "mwh qc measurement --tier <t> [--top N] prints the measurement-process summary "
        "(meta.mp_item_summary) and the top structural cells (meta.mp_structural). "
        "Aggregates only; counts were k-suppressed when the tables were built."
    ),
    no_args_is_help=True,
    rich_markup_mode="rich",
)

#: The measurement reads (EP-45): plain column reads of registry tables — a `count(*)`
#: would meet the safe-query suppressor (``docs/gotchas.md`` §1); the flag counts per
#: item are tallied in Python.
_MP_SUMMARY_SQL = (
    "SELECT itemid, label, source, n_stays, n_stays_suppressed, n_stays_measured, "
    "n_stays_measured_suppressed, n_stays_measured_first_24h, "
    "n_stays_measured_first_24h_suppressed, measured_first_24h_share, median_per_stay_day, "
    "median_interval_min, p10_interval_min, p90_interval_min, k, run_id, build_id "
    "FROM meta.mp_item_summary ORDER BY source, itemid"
)
_MP_FLAGS_SQL = "SELECT itemid, structural_flag FROM meta.mp_structural"
_MP_STRUCTURAL_SQL = (
    "SELECT itemid, label, first_careunit, era, n_stays, n_stays_suppressed, structural_flag "
    "FROM meta.mp_structural WHERE structural_flag = 'structural' "
    "ORDER BY n_stays DESC NULLS LAST, itemid, first_careunit, era"
)


def measurement_summary(
    tier: str,
    *,
    settings: Settings | None = None,
    top: int = 20,
    actor: str | None = None,
) -> dict[str, Any]:
    """``{tier, run_id, build_id, k, n_population, items: [...], flag_counts: {itemid:
    {in_use, sparse, structural}}, structural_cells: [...], n_structural_cells}`` from the
    tier catalog through ``safe_query`` (module docstring). ``top`` caps the structural
    cells listed (largest cells first); every count below k is ``None`` with its
    ``<count>_suppressed`` marker true (suppressed when the tables were built)."""
    from mimicwarehouse.config import get_settings
    from mimicwarehouse.qc.measurement import FLAGS
    from mimicwarehouse.safe import safe_query

    resolved = settings or get_settings()
    items = safe_query(
        _MP_SUMMARY_SQL, tier=tier, settings=resolved, actor=actor, row_cap=STATUS_ROW_CAP
    ).df.to_dicts()
    flags = safe_query(
        _MP_FLAGS_SQL, tier=tier, settings=resolved, actor=actor, row_cap=STATUS_ROW_CAP * 10
    ).df.to_dicts()
    cells = safe_query(
        _MP_STRUCTURAL_SQL, tier=tier, settings=resolved, actor=actor, row_cap=STATUS_ROW_CAP
    ).df.to_dicts()
    flag_counts: dict[int, dict[str, int]] = {}
    for row in flags:
        entry = flag_counts.setdefault(int(row["itemid"]), dict.fromkeys(FLAGS, 0))
        flag = row.get("structural_flag")
        if flag in entry:
            entry[str(flag)] += 1
    first = items[0] if items else {}
    population = first.get("n_stays") if not first.get("n_stays_suppressed") else None
    return {
        "tier": tier,
        "run_id": first.get("run_id"),
        "build_id": first.get("build_id"),
        "k": first.get("k"),
        "n_population": population,
        "items": items,
        "flag_counts": {str(itemid): counts for itemid, counts in sorted(flag_counts.items())},
        "structural_cells": cells[: max(top, 0)],
        "n_structural_cells": len(cells),
    }


def status_summary(
    tier: str,
    *,
    settings: Settings | None = None,
    show: str = "fail",
    actor: str | None = None,
) -> dict[str, Any]:
    """``{tier, run_id, build_id, tables: {...}, counts: {check_id: {pass, warn, fail}},
    totals: {...}, rows: [...]}`` from the tier catalog through ``safe_query`` (module
    docstring). ``show`` selects the rows: ``fail`` / ``warn`` / ``all`` (both) / ``none``."""
    from mimicwarehouse.config import get_settings
    from mimicwarehouse.qc.profile import CHECK_IDS, STATUSES
    from mimicwarehouse.safe import safe_query

    resolved = settings or get_settings()
    counts_df = safe_query(
        _COUNTS_SQL, tier=tier, settings=resolved, actor=actor, row_cap=STATUS_ROW_CAP
    ).df
    tables_df = safe_query(
        _TABLES_SQL, tier=tier, settings=resolved, actor=actor, row_cap=STATUS_ROW_CAP
    ).df
    counts: dict[str, dict[str, int]] = {cid: dict.fromkeys(STATUSES, 0) for cid in CHECK_IDS}
    for row in counts_df.to_dicts():
        counts.setdefault(str(row["check_id"]), dict.fromkeys(STATUSES, 0))[str(row["status"])] += 1
    totals = {s: sum(c[s] for c in counts.values()) for s in STATUSES}
    wanted = {"fail": ["fail"], "warn": ["warn"], "all": ["warn", "fail"], "none": []}[show]
    rows: list[dict[str, Any]] = []
    if wanted:
        sql = _ROWS_SQL.format(statuses=", ".join(f"'{s}'" for s in wanted))
        rows = safe_query(
            sql, tier=tier, settings=resolved, actor=actor, row_cap=STATUS_ROW_CAP
        ).df.to_dicts()
    table_rows = tables_df.to_dicts()
    first = table_rows[0] if table_rows else {}
    return {
        "tier": tier,
        "run_id": first.get("run_id"),
        "build_id": first.get("build_id"),
        "tables": {
            "n": len(table_rows),
            "fail": sum(1 for t in table_rows if t.get("worst_status") == "fail"),
            "warn": sum(1 for t in table_rows if t.get("worst_status") == "warn"),
        },
        "counts": counts,
        "totals": totals,
        "rows": rows,
    }


def _fmt_value(metric: str, value: Any) -> str:
    from mimicwarehouse.qc.report import SHARE_METRICS

    if value is None:
        return "-"
    if metric in SHARE_METRICS:
        return f"{float(value) * 100:.2f}%"
    return f"{float(value):g}"


@qc_app.command("status")
def status_command(
    ctx: typer.Context,
    tier: Annotated[
        str, typer.Option("--tier", help="Tier catalog to read: fixture | demo | dev | full.")
    ],
    show: Annotated[
        str,
        typer.Option("--show", help="Rows to list: fail (default) | warn | all | none."),
    ] = "fail",
    json_output: Annotated[bool, typer.Option("--json", help="Print the summary as JSON.")] = False,
) -> None:
    """Pass / warn / fail counts of meta.qc_checks and the flagged checks (EP-44;
    aggregates only, every read audited). Exit 1 when any check failed."""
    prefix = "mwh qc status"
    state: CliState = ctx.obj
    if tier not in TIERS:
        fail(prefix, f"unknown tier {tier!r}; expected one of {', '.join(TIERS)}")
    if show not in SHOW_CHOICES:
        fail(prefix, f"unknown --show {show!r}; expected one of {', '.join(SHOW_CHOICES)}")
    settings = state.settings
    from mimicwarehouse.catalog.cli import safe_cli_errors

    with safe_cli_errors(prefix):
        summary = status_summary(tier, settings=settings, show=show)
    if json_output:
        emit_json(summary)
        if summary["totals"]["fail"]:
            raise typer.Exit(code=EXIT_FINDINGS)
        return

    from rich.table import Table as RichTable

    from mimicwarehouse.disclose import render_cell
    from mimicwarehouse.inventory import fmt_int

    listing = RichTable(title=f"meta.qc_checks ({tier})", pad_edge=False)
    for name, justify in (
        ("check", "left"),
        ("pass", "right"),
        ("warn", "right"),
        ("fail", "right"),
    ):
        listing.add_column(name, justify=justify)  # type: ignore[arg-type]
    for cid, n in summary["counts"].items():
        listing.add_row(escape(cid), fmt_int(n["pass"]), fmt_int(n["warn"]), fmt_int(n["fail"]))
    totals = summary["totals"]
    listing.add_row(
        "[bold]total[/]", fmt_int(totals["pass"]), fmt_int(totals["warn"]), fmt_int(totals["fail"])
    )
    console.print(listing)
    rows = summary["rows"]
    if rows:
        flagged = RichTable(title=f"flagged checks ({show})", pad_edge=False)
        for name in (
            "status",
            "check",
            "table",
            "column",
            "itemid",
            "metric",
            "value",
            "n_affected",
            "detail",
        ):
            flagged.add_column(
                name, justify="right" if name in ("itemid", "value", "n_affected") else "left"
            )  # type: ignore[arg-type]
        for r in rows:
            k = int(r.get("k") or settings.k_suppression)
            n_cell = (
                render_cell(None, k)
                if r.get("n_affected_suppressed")
                else ("-" if r.get("n_affected") is None else fmt_int(int(r["n_affected"])))
            )
            flagged.add_row(
                escape(str(r["status"])),
                escape(str(r["check_id"])),
                escape(f"{r['schema']}.{r['table']}"),
                escape("" if r.get("column") is None else str(r["column"])),
                "" if r.get("itemid") is None else fmt_int(int(r["itemid"])),
                escape(str(r["metric"])),
                _fmt_value(str(r["metric"]), r.get("value")),
                n_cell,
                escape("" if r.get("detail") is None else str(r["detail"])),
            )
        console.print(flagged)
    tables = summary["tables"]
    console.print(
        console_safe(
            f"{fmt_int(tables['n'])} table(s) profiled ({fmt_int(tables['fail'])} with a failing "
            f"check, {fmt_int(tables['warn'])} with warnings only); run "
            f"{summary['run_id'] or '-'} (mwh runs show {summary['run_id'] or '-'}); small "
            "counts were suppressed when the tables were built (GOVERNANCE section 5)"
        ),
        highlight=False,
    )
    if totals["fail"]:
        raise typer.Exit(code=EXIT_FINDINGS)


def _stat(value: Any, digits: int = 2) -> str:
    return "-" if value is None else f"{float(value):.{digits}f}"


def _pct(value: Any) -> str:
    return "-" if value is None else f"{float(value) * 100:.1f}%"


@qc_app.command("measurement")
def measurement_command(
    ctx: typer.Context,
    tier: Annotated[
        str, typer.Option("--tier", help="Tier catalog to read: fixture | demo | dev | full.")
    ],
    top: Annotated[
        int, typer.Option("--top", help="Structural cells to list (largest first).", min=0)
    ] = 20,
    json_output: Annotated[bool, typer.Option("--json", help="Print the summary as JSON.")] = False,
) -> None:
    """The measurement-process summary per curated itemid and the top structural cells
    (EP-45; meta.mp_item_summary / meta.mp_structural through safe_query, every read
    audited; a count suppressed at build time reads <k)."""
    prefix = "mwh qc measurement"
    state: CliState = ctx.obj
    if tier not in TIERS:
        fail(prefix, f"unknown tier {tier!r}; expected one of {', '.join(TIERS)}")
    settings = state.settings
    from mimicwarehouse.catalog.cli import safe_cli_errors

    with safe_cli_errors(prefix):
        summary = measurement_summary(tier, settings=settings, top=top)
    if json_output:
        emit_json(summary)
        return

    from rich.table import Table as RichTable

    from mimicwarehouse.disclose import render_cell
    from mimicwarehouse.inventory import fmt_int
    from mimicwarehouse.qc.measurement import FLAG_IN_USE, FLAG_SPARSE, FLAG_STRUCTURAL

    k = int(summary.get("k") or settings.k_suppression)

    def count_cell(row: dict[str, Any], column: str) -> str:
        if row.get(f"{column}_suppressed"):
            return render_cell(None, k)
        value = row.get(column)
        return "-" if value is None else fmt_int(int(value))

    listing = RichTable(title=f"meta.mp_item_summary ({tier})", pad_edge=False)
    for name, justify in (
        ("itemid", "right"),
        ("label", "left"),
        ("source", "left"),
        ("stays measured", "right"),
        ("first 24 h", "right"),
        ("share 24 h", "right"),
        ("per stay-day", "right"),
        ("interval p10/med/p90 min", "right"),
        ("cells in use/sparse/structural", "right"),
    ):
        listing.add_column(name, justify=justify)  # type: ignore[arg-type]
    for r in summary["items"]:
        flags = summary["flag_counts"].get(str(int(r["itemid"])), {})
        listing.add_row(
            fmt_int(int(r["itemid"])),
            escape(str(r.get("label") or "")),
            escape(str(r.get("source") or "")),
            count_cell(r, "n_stays_measured"),
            count_cell(r, "n_stays_measured_first_24h"),
            _pct(r.get("measured_first_24h_share")),
            _stat(r.get("median_per_stay_day")),
            f"{_stat(r.get('p10_interval_min'), 0)}/{_stat(r.get('median_interval_min'), 0)}/"
            f"{_stat(r.get('p90_interval_min'), 0)}",
            f"{fmt_int(flags.get(FLAG_IN_USE, 0))}/{fmt_int(flags.get(FLAG_SPARSE, 0))}/"
            f"{fmt_int(flags.get(FLAG_STRUCTURAL, 0))}",
        )
    console.print(listing)
    cells = summary["structural_cells"]
    if cells:
        table = RichTable(
            title=f"structural cells (top {fmt_int(len(cells))} of "
            f"{fmt_int(summary['n_structural_cells'])})",
            pad_edge=False,
        )
        for name in ("itemid", "label", "first_careunit", "era", "stays in cell"):
            table.add_column(
                name, justify="right" if name in ("itemid", "stays in cell") else "left"
            )  # type: ignore[arg-type]
        for r in cells:
            table.add_row(
                fmt_int(int(r["itemid"])),
                escape(str(r.get("label") or "")),
                escape(str(r.get("first_careunit") or "")),
                escape(str(r.get("era") or "")),
                count_cell(r, "n_stays"),
            )
        console.print(table)
    else:
        console.print("no structural cell on this tier", highlight=False)
    population = summary.get("n_population")
    console.print(
        console_safe(
            f"{fmt_int(len(summary['items']))} curated itemid(s); population "
            f"{render_cell(None, k) if population is None else fmt_int(int(population))} ICU "
            f"stay(s); run {summary['run_id'] or '-'} (mwh runs show {summary['run_id'] or '-'}); "
            "small counts were suppressed when the tables were built (GOVERNANCE section 5)"
        ),
        highlight=False,
    )


__all__ = [
    "SHOW_CHOICES",
    "STATUS_ROW_CAP",
    "TIERS",
    "measurement_command",
    "measurement_summary",
    "qc_app",
    "status_command",
    "status_summary",
]
