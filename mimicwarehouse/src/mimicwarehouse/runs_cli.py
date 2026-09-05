"""``mwh runs`` — the run/audit stores CLI (EP-30 item 3; attached in
:mod:`mimicwarehouse.cli`).

EP-30 ships ``mwh runs refresh``: rebuild ``warehouse/runs.duckdb`` (the read-only
view store) over the append-only ledgers (:func:`mimicwarehouse.safe.build_runs_db`,
published by the DESIGN §6 rename-aside swap — :func:`mimicwarehouse.publish.swap_file`
since EP-33; a non-sharing reader on the live file surfaces as
:class:`mimicwarehouse.publish.SwapBlockedError`, exit 2). EP-32 adds ``mwh runs
benchmarks`` — the benchmark-ledger summary (:func:`mimicwarehouse.dag.benchmarks.summarize`
→ ``render_markdown``) as a rich table, Markdown, or spliced between the
``benchmarks:begin``/``benchmarks:end`` markers of an existing doc (``--out``; the
``docs/analyses/00-staging-benchmark.md`` Results table). EP-35 adds the ledger views to
``refresh`` (``audit`` · ``ledger`` · ``benchmarks`` · ``manifests`` · ``attrition``),
``mwh runs list [--tier] [--kind] [--last N] [--json]`` (the ``runs/ledger.jsonl`` lines,
newest first — read through :func:`mimicwarehouse.run.list_runs`, never through the view
store, so it works before the first refresh) and ``mwh runs show <run_id> [--json]``
(the run's ``manifest.json``, pretty-printed). Everything printed is provenance — ids,
hashes, counts, timings, paths — never a row. Errors go through
:func:`mimicwarehouse.console.fail` (stderr, ``EXIT_USAGE``; EP-33 B8); ``--json`` through
:func:`mimicwarehouse.console.emit_json` (raw ints, never pasted into tracked files).

Import budget: duckdb, polars, the safe module and :mod:`mimicwarehouse.run` are imported
inside the command bodies (cli.py rule — ``mwh --help`` stays under ~0.5 s).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer
from rich.markup import escape

from mimicwarehouse.console import EXIT_USAGE, console, emit_json, fail

if TYPE_CHECKING:  # pragma: no cover
    from mimicwarehouse.cli import CliState

runs_app = typer.Typer(
    name="runs",
    help=(
        "Run/audit stores (EP-30/32/35): mwh runs refresh rebuilds warehouse/runs.duckdb "
        "over the ledgers; mwh runs list / show read the provenance run ledger; "
        "mwh runs benchmarks renders the benchmark ledger."
    ),
    no_args_is_help=True,
    rich_markup_mode="rich",
)

#: Columns of the ``mwh runs list`` table (ledger fields, in display order).
LIST_COLUMNS: tuple[str, ...] = (
    "run_id",
    "name",
    "kind",
    "tier",
    "status",
    "started",
    "wall_s",
    "git_sha",
)


@runs_app.command("refresh")
def refresh_command(ctx: typer.Context) -> None:
    """Rebuild warehouse/runs.duckdb (views audit, ledger, benchmarks, manifests,
    attrition over the runs/ ledgers and manifests; EP-30/EP-35)."""
    from mimicwarehouse.publish import SwapBlockedError
    from mimicwarehouse.safe import RUNS_DB_VIEWS, build_runs_db

    state: CliState = ctx.obj
    try:
        path = build_runs_db(state.settings)
    except SwapBlockedError as exc:
        fail("mwh runs refresh", str(exc))
    console.print(
        f"refreshed {escape(str(path))} (views: {', '.join(RUNS_DB_VIEWS)} over "
        f"{escape(str(state.settings.layout['runs']))})",
        highlight=False,
    )


@runs_app.command("list")
def list_command(
    ctx: typer.Context,
    tier: Annotated[
        str | None,
        typer.Option("--tier", help="Keep only runs of this tier (fixture | demo | dev | full)."),
    ] = None,
    kind: Annotated[
        str | None,
        typer.Option("--kind", help="Keep only runs of this kind (analysis, cohort, ...)."),
    ] = None,
    last: Annotated[
        int,
        typer.Option("--last", help="Show at most the N most recent runs (default 20).", min=1),
    ] = 20,
    as_json: Annotated[
        bool, typer.Option("--json", help="Emit the ledger lines as a JSON list (raw ints).")
    ] = False,
) -> None:
    """List recorded runs from runs/ledger.jsonl, newest first (EP-35)."""
    from mimicwarehouse.run import RUN_KINDS, TIERS, ledger_path, list_runs

    state: CliState = ctx.obj
    prefix = "mwh runs list"
    if tier is not None and tier not in TIERS:
        fail(prefix, f"unknown tier {tier!r}; expected one of {', '.join(TIERS)}")
    if kind is not None and kind not in RUN_KINDS:
        fail(prefix, f"unknown kind {kind!r}; expected one of {', '.join(RUN_KINDS)}")
    settings = state.settings
    rows = list_runs(settings, tier=tier, kind=kind, last=last)
    if as_json:
        emit_json(rows)
        return
    if not rows:
        console.print(
            f"no runs recorded in {escape(str(ledger_path(settings)))}"
            + (" for the given filters" if tier or kind else ""),
            highlight=False,
        )
        return

    from rich.table import Table as RichTable

    table = RichTable(title=f"runs (last {len(rows)})", pad_edge=False)
    for col in LIST_COLUMNS:
        table.add_column(col, justify="right" if col == "wall_s" else "left")
    for row in rows:
        cells = []
        for col in LIST_COLUMNS:
            value = row.get(col)
            if col == "wall_s":
                cells.append("-" if value is None else f"{float(value):.1f}")
            elif col == "git_sha":
                cells.append("-" if not value else str(value)[:12])
            else:
                cells.append("-" if value is None else str(value))
        table.add_row(*(escape(c) for c in cells))
    console.print(table)


@runs_app.command("show")
def show_command(
    ctx: typer.Context,
    run_id: Annotated[str, typer.Argument(help="The run id (YYYYMMDDTHHMMSSZ-<6 hex>).")],
    as_json: Annotated[
        bool, typer.Option("--json", help="Emit the manifest as JSON (raw ints).")
    ] = False,
) -> None:
    """Print a run's manifest.json (provenance only: hashes, counts, params, paths; EP-35)."""
    from mimicwarehouse.run import RunLedgerError, read_manifest

    state: CliState = ctx.obj
    try:
        manifest = read_manifest(run_id, state.settings)
    except RunLedgerError as exc:
        fail("mwh runs show", str(exc), code=EXIT_USAGE)
    payload = manifest.model_dump(mode="json")
    if as_json:
        emit_json(payload)
        return
    console.print_json(data=payload, indent=2, sort_keys=True)


@runs_app.command("benchmarks")
def benchmarks_command(
    ctx: typer.Context,
    tier: Annotated[
        str,
        typer.Option(
            "--tier",
            help="Ledger tier to summarize (default full — the staging benchmark); "
            "'all' keeps every tier.",
        ),
    ] = "full",
    kind: Annotated[
        str,
        typer.Option(
            "--kind",
            help="Ledger line kind to summarize (default stage); 'all' keeps every kind.",
        ),
    ] = "stage",
    fmt: Annotated[
        str,
        typer.Option("--format", help="Output format: table (rich) or md (Markdown)."),
    ] = "table",
    out: Annotated[
        Path | None,
        typer.Option(
            "--out",
            help="Existing Markdown file whose <!-- benchmarks:begin/end --> block is "
            "replaced with the rendered table (narrative untouched; implies md).",
        ),
    ] = None,
) -> None:
    """Render the benchmark ledger's per-step summary (EP-32; telemetry only)."""
    from mimicwarehouse.dag import benchmarks as benchmarks_mod

    state: CliState = ctx.obj
    prefix = "mwh runs benchmarks"
    if fmt not in ("table", "md"):
        fail(prefix, f"unknown --format {fmt}; expected table or md")
    summary = benchmarks_mod.summarize(
        state.settings,
        tier=None if tier == "all" else tier,
        kind=None if kind == "all" else kind,
    )
    if summary.is_empty():
        fail(
            prefix,
            f"no ledger lines for tier={tier} kind={kind} in "
            f"{benchmarks_mod.benchmarks_path(state.settings)}",
        )

    if out is not None:
        if not out.is_file():
            fail(prefix, f"--out target does not exist: {out}")
        # newline='' preserves the file's own line endings; the spliced block is LF
        with out.open(encoding="utf-8", newline="") as f:
            text = f.read()
        try:
            updated = benchmarks_mod.replace_marked_block(
                text, benchmarks_mod.render_markdown(summary)
            )
        except ValueError as exc:
            fail(prefix, str(exc))
        if updated != text:
            with out.open("w", encoding="utf-8", newline="") as f:
                f.write(updated)
        console.print(
            f"benchmarks block {'updated' if updated != text else 'unchanged'} in "
            f"{escape(str(out))}",
            highlight=False,
        )
        return

    if fmt == "md":
        typer.echo(benchmarks_mod.render_markdown(summary), nl=False)
        return

    from rich.table import Table as RichTable

    table = RichTable(title=f"benchmarks ({tier}/{kind})", pad_edge=False)
    for i, col in enumerate(benchmarks_mod.RENDER_COLUMNS):
        table.add_column(col, justify="left" if i == 0 else "right")
    for row in benchmarks_mod.render_rows(summary):
        table.add_row(*(escape(cell) for cell in row))
    console.print(table)


__all__ = [
    "LIST_COLUMNS",
    "benchmarks_command",
    "list_command",
    "refresh_command",
    "runs_app",
    "show_command",
]
