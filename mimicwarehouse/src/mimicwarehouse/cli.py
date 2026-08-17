"""``mwh`` — the mimicwarehouse command line (typer + rich; EP-2, DESIGN §15).

Commands live in their own modules and are attached here with **one** ``app.command()`` /
``app.add_typer()`` line each, so later briefs extend without restructuring:
``doctor`` (EP-2, :mod:`mimicwarehouse.doctor`) · ``paths`` (EP-3,
:mod:`mimicwarehouse.config`) · ``guard`` (EP-4, :mod:`mimicwarehouse.guard`) · ``verify``
(EP-6) · ``build`` (EP-19) · ``demo`` (EP-22) · ``sql`` (EP-30) · ``runs`` (EP-35) ·
``protocol`` (EP-51) · ``backup`` (EP-52) · ``app`` (EP-57) · ``disclose`` (EP-43/133) ·
``init`` (EP-158).

Settings (EP-3): the callback loads :class:`mimicwarehouse.config.Settings` once per
invocation — ``--data-root`` > ``MWH_*`` env > ``.env`` > ``mwh.toml`` > defaults — installs
the override process-wide (:func:`mimicwarehouse.config.configure`, so ``get_settings()``
agrees with ``ctx.obj``) and hands the instance to the command as ``CliState.settings``.
Every command gets **validated** settings (an unsafe data root exits 2 before the command
runs) except the diagnostic commands in :data:`DIAGNOSTIC_COMMANDS`, which receive the
unchecked instance so they can *report* the problem.

Import-time budget: ``mwh --help`` must stay under ~0.5 s, so this module never imports
duckdb / pandas / polars / pyarrow — commands import what they need inside their bodies.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.markup import escape

from mimicwarehouse import __version__, config
from mimicwarehouse.config import Settings, paths_command
from mimicwarehouse.doctor import doctor_command
from mimicwarehouse.guard import guard_command

#: Commands that must run even when the data root is unsafe: ``doctor`` / ``paths`` report it
#: (exit codes tell); ``guard`` never touches the data root and, as the pre-commit hook, must
#: not be blocked by a mis-set ``MWH_DATA_ROOT`` (EP-4).
DIAGNOSTIC_COMMANDS: frozenset[str] = frozenset({"doctor", "paths", "guard"})

console = Console()

app = typer.Typer(
    name="mwh",
    help="mimicwarehouse — local MIMIC-IV data lab (DuckDB + Parquet). "
    "Aggregates only; read GOVERNANCE.md before touching data.",
    no_args_is_help=True,
    rich_markup_mode="rich",
    add_completion=False,
)


@dataclass(frozen=True, slots=True)
class CliState:
    """Per-invocation state handed to commands through ``ctx.obj``."""

    settings: Settings
    data_root_override: Path | None = None

    @property
    def data_root(self) -> Path:
        return self.settings.data_root


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"mwh {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    ctx: typer.Context,
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            help="Print the mwh version and exit.",
            callback=_version_callback,
            is_eager=True,
        ),
    ] = False,
    data_root: Annotated[
        Path | None,
        typer.Option(
            "--data-root",
            help="Data root for this invocation (overrides MWH_DATA_ROOT / .env / mwh.toml; "
            "default C:\\mimicdata).",
            show_default=False,
        ),
    ] = None,
) -> None:
    overrides = {"data_root": data_root} if data_root is not None else {}
    config.configure(**overrides)
    checked = ctx.invoked_subcommand not in DIAGNOSTIC_COMMANDS
    try:
        settings = (
            config.get_settings() if checked else config.load_settings(checked=False, **overrides)
        )
    except (config.ConfigError, config.ValidationError) as exc:
        console.print(f"[bold red]mwh:[/] {escape(str(exc))}", highlight=False)
        raise typer.Exit(code=2) from None
    ctx.obj = CliState(settings=settings, data_root_override=data_root)


# --- commands (one line each; keep alphabetical as briefs add them) -----------------------
app.command("doctor")(doctor_command)
app.command("guard")(guard_command)
app.command("paths")(paths_command)


if __name__ == "__main__":  # pragma: no cover
    app()
