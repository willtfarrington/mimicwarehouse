"""``mwh qc`` — the data-quality command group (EP-44 item 3; attached in
:mod:`mimicwarehouse.cli`).

``mwh qc status --tier <t>`` reads ``meta.qc_checks`` / ``meta.qc_tables`` through
:func:`mimicwarehouse.safe.safe_query` (``meta.*`` is a registry exemption; the counts
were k-suppressed when the tables were built, EP-44) and prints the pass / warn / fail
counts per check plus the failing (``--show fail``, default), warning (``--show warn``)
or every flagged (``--show all``) check row — aggregates only, every read audited. Exit
codes follow the EP-33 canon (``EXIT_FINDINGS`` 1 when any check failed, so a CI-style
caller can gate on it; refusals 3; usage / no catalog 2). EP-45 adds ``mwh qc
measurement``.

Import budget: this module is on the ``mwh --help`` path — typer / rich only; the
profile / report modules, ``safe`` and polars load inside the command body.
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
        "Data-quality profiling (EP-44): mwh qc status --tier <t> [--show fail|warn|all|none] "
        "prints the pass / warn / fail counts of meta.qc_checks and the flagged checks "
        "(aggregates only; counts were k-suppressed when the tables were built)."
    ),
    no_args_is_help=True,
    rich_markup_mode="rich",
)


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


__all__ = ["SHOW_CHOICES", "STATUS_ROW_CAP", "TIERS", "qc_app", "status_command", "status_summary"]
