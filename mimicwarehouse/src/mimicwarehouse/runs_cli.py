"""``mwh runs`` — the run/audit stores CLI (EP-30 item 3; attached in
:mod:`mimicwarehouse.cli`).

EP-30 ships only ``mwh runs refresh``: rebuild ``warehouse/runs.duckdb`` (the read-only
view store) with the ``audit`` view over the append-only ``runs/audit.jsonl``
(:func:`mimicwarehouse.safe.build_runs_db`, published by the DESIGN §6 rename-aside
swap). EP-35 adds the ledger views plus ``mwh runs list`` / ``mwh runs show``.

Import budget: duckdb and the safe module are imported inside the command body
(cli.py rule — ``mwh --help`` stays under ~0.5 s).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import typer
from rich.markup import escape

from mimicwarehouse.console import console

if TYPE_CHECKING:  # pragma: no cover
    from mimicwarehouse.cli import CliState

runs_app = typer.Typer(
    name="runs",
    help=(
        "Run/audit stores (EP-30): mwh runs refresh rebuilds warehouse/runs.duckdb "
        "over runs/audit.jsonl; list/show arrive with EP-35."
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


__all__ = ["refresh_command", "runs_app"]
