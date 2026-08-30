"""``mwh runs`` — the run/audit stores CLI (EP-30 item 3; attached in
:mod:`mimicwarehouse.cli`).

EP-30 ships ``mwh runs refresh``: rebuild ``warehouse/runs.duckdb`` (the read-only
view store) with the ``audit`` view over the append-only ``runs/audit.jsonl``
(:func:`mimicwarehouse.safe.build_runs_db`, published by the DESIGN §6 rename-aside
swap). EP-32 adds ``mwh runs benchmarks`` — the benchmark-ledger summary
(:func:`mimicwarehouse.dag.benchmarks.summarize` → ``render_markdown``) as a rich
table, Markdown, or spliced between the ``benchmarks:begin``/``benchmarks:end``
markers of an existing doc (``--out``; the ``docs/analyses/00-staging-benchmark.md``
Results table). EP-35 adds the ledger views plus ``mwh runs list`` / ``mwh runs show``.
Everything printed is build telemetry — counts, bytes, timings — never a row.

Import budget: duckdb, polars and the safe module are imported inside the command
bodies (cli.py rule — ``mwh --help`` stays under ~0.5 s).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer
from rich.markup import escape

from mimicwarehouse.console import console

if TYPE_CHECKING:  # pragma: no cover
    from mimicwarehouse.cli import CliState

runs_app = typer.Typer(
    name="runs",
    help=(
        "Run/audit stores (EP-30/32): mwh runs refresh rebuilds warehouse/runs.duckdb "
        "over runs/audit.jsonl; mwh runs benchmarks renders the benchmark ledger; "
        "list/show arrive with EP-35."
    ),
    no_args_is_help=True,
    rich_markup_mode="rich",
)


@runs_app.command("refresh")
def refresh_command(ctx: typer.Context) -> None:
    """Rebuild warehouse/runs.duckdb (audit view over runs/audit.jsonl; EP-30)."""
    from mimicwarehouse.catalog.build import CatalogSwapError
    from mimicwarehouse.safe import audit_path, build_runs_db

    state: CliState = ctx.obj
    try:
        path = build_runs_db(state.settings)
    except CatalogSwapError as exc:
        console.print(f"[bold red]mwh runs refresh:[/] {escape(str(exc))}", highlight=False)
        raise typer.Exit(code=2) from None
    console.print(
        f"refreshed {escape(str(path))} (view: audit over "
        f"{escape(str(audit_path(state.settings)))})",
        highlight=False,
    )


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
    if fmt not in ("table", "md"):
        console.print(
            f"[bold red]mwh runs benchmarks:[/] unknown --format {escape(fmt)!s}; "
            "expected table or md",
            highlight=False,
        )
        raise typer.Exit(code=2)
    summary = benchmarks_mod.summarize(
        state.settings,
        tier=None if tier == "all" else tier,
        kind=None if kind == "all" else kind,
    )
    if summary.is_empty():
        console.print(
            f"[bold red]mwh runs benchmarks:[/] no ledger lines for tier={escape(tier)} "
            f"kind={escape(kind)} in {escape(str(benchmarks_mod.benchmarks_path(state.settings)))}",
            highlight=False,
        )
        raise typer.Exit(code=2)

    if out is not None:
        if not out.is_file():
            console.print(
                f"[bold red]mwh runs benchmarks:[/] --out target does not exist: "
                f"{escape(str(out))}",
                highlight=False,
            )
            raise typer.Exit(code=2)
        # newline='' preserves the file's own line endings; the spliced block is LF
        with out.open(encoding="utf-8", newline="") as f:
            text = f.read()
        try:
            updated = benchmarks_mod.replace_marked_block(
                text, benchmarks_mod.render_markdown(summary)
            )
        except ValueError as exc:
            console.print(f"[bold red]mwh runs benchmarks:[/] {escape(str(exc))}", highlight=False)
            raise typer.Exit(code=2) from None
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


__all__ = ["benchmarks_command", "refresh_command", "runs_app"]
