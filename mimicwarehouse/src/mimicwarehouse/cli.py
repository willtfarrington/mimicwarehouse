"""``mwh`` — the mimicwarehouse command line (typer + rich; EP-2, DESIGN §15).

Commands live in their own modules and are attached here with **one** ``app.command()`` /
``app.add_typer()`` line each, so later briefs extend without restructuring:
``doctor`` (EP-2, :mod:`mimicwarehouse.doctor`) · ``paths`` (EP-3) · ``guard`` (EP-4) ·
``verify`` (EP-6) · ``build`` (EP-19) · ``demo`` (EP-22) · ``sql`` (EP-30) · ``runs``
(EP-35) · ``protocol`` (EP-51) · ``backup`` (EP-52) · ``app`` (EP-57) · ``disclose``
(EP-43/133) · ``init`` (EP-158).

Import-time budget: ``mwh --help`` must stay under ~0.5 s, so this module never imports
duckdb / pandas / polars / pyarrow — commands import what they need inside their bodies.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from mimicwarehouse import __version__
from mimicwarehouse.doctor import doctor_command

#: Until EP-3's ``Settings`` lands, the data root is ``--data-root`` > ``MWH_DATA_ROOT`` > this.
DEFAULT_DATA_ROOT = Path(r"C:\mimicdata")

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

    data_root: Path


def resolve_data_root(override: Path | None = None) -> Path:
    """``--data-root`` for one invocation, else ``MWH_DATA_ROOT``, else the default root."""
    if override is not None:
        return Path(override)
    env = os.environ.get("MWH_DATA_ROOT", "").strip()
    return Path(env) if env else DEFAULT_DATA_ROOT


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
            help="Data root for this invocation (overrides MWH_DATA_ROOT; default C:\\mimicdata).",
            show_default=False,
        ),
    ] = None,
) -> None:
    ctx.obj = CliState(data_root=resolve_data_root(data_root))


# --- commands (one line each; keep alphabetical as briefs add them) -----------------------
app.command("doctor")(doctor_command)


if __name__ == "__main__":  # pragma: no cover
    app()
