"""``mwh`` — the mimicwarehouse command line (typer + rich; EP-2, DESIGN §15).

Commands live in their own modules and are attached here with **one** ``app.command()`` /
``app.add_typer()`` line each, so later briefs extend without restructuring:
``doctor`` (EP-2, :mod:`mimicwarehouse.doctor`) · ``paths`` (EP-3,
:mod:`mimicwarehouse.config`) · ``guard`` (EP-4, :mod:`mimicwarehouse.guard`) · ``verify``
(EP-6, :mod:`mimicwarehouse.verify`) · ``schema`` (EP-9, :mod:`mimicwarehouse.schema.cli`) ·
``inventory`` (EP-10, :mod:`mimicwarehouse.inventory`) · ``fixtures`` (EP-11,
:mod:`mimicwarehouse.fixtures.cli`) · ``canary`` (EP-171, :mod:`mimicwarehouse.canary`) ·
``build``/``jobs`` (EP-19, :mod:`mimicwarehouse.dag.cli`) · ``catalog``/``sql`` (EP-21,
:mod:`mimicwarehouse.catalog.cli`; since EP-30 ``sql`` routes everything through
``safe_query`` — aggregate-only, audited, refusals exit 3) · ``demo`` (EP-22) ·
``runs`` (EP-30 ``refresh``, :mod:`mimicwarehouse.runs_cli`; EP-35 adds list/show) ·
``tracer`` (EP-31, :mod:`mimicwarehouse.tracer`) · ``protocol`` (EP-51) · ``backup``
(EP-52) · ``app`` (EP-57) · ``disclose`` (EP-43/133) · ``init`` (EP-158).

Settings (EP-3, reworked EP-167): the callback loads an **unchecked**
:class:`mimicwarehouse.config.Settings` once per invocation — ``--data-root`` > ``MWH_*`` env >
``.env`` > ``mwh.toml`` > defaults — installs the override process-wide
(:func:`mimicwarehouse.config.configure`, so ``get_settings()`` agrees with ``ctx.obj``) and
hands a :class:`CliState` to the command. A configuration error (broken ``.env``/``mwh.toml``)
is stored as :attr:`CliState.pending_error` instead of raised, and the D-29 location refusals
run on the **first access** of :attr:`CliState.settings` by a non-diagnostic command — so
``--help``, ``--version`` and ``no_args_is_help`` always work, even over an unsafe or broken
configuration (retro CFG-5), while ``mwh inventory build`` still exits 2 before touching
anything. The diagnostic commands in :data:`DIAGNOSTIC_COMMANDS` receive the unchecked
instance so they can *report* the problem. Whether the allow-list should give way to fully
lazy validation everywhere is the EP-16 (re-plan P1) decision; until then the set below is
authoritative.

Import-time budget: ``mwh --help`` must stay under ~0.5 s, so this module never imports
duckdb / pandas / polars / pyarrow — commands import what they need inside their bodies.
The ``mwh`` script entry point is :func:`mimicwarehouse.console.run` (UTF-8 stdio, EP-167),
which calls :data:`app`; ``python -m mimicwarehouse.cli`` still works.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.markup import escape

from mimicwarehouse import __version__, config
from mimicwarehouse.canary import canary_app
from mimicwarehouse.catalog.cli import catalog_app, sql_command
from mimicwarehouse.config import Settings, paths_command
from mimicwarehouse.console import console, err_console
from mimicwarehouse.dag.cli import build_command, jobs_command
from mimicwarehouse.demo import demo_app
from mimicwarehouse.doctor import doctor_command
from mimicwarehouse.fixtures.cli import fixtures_app
from mimicwarehouse.guard import guard_command
from mimicwarehouse.inventory import inventory_app
from mimicwarehouse.runs_cli import runs_app
from mimicwarehouse.schema.cli import schema_app
from mimicwarehouse.tracer import tracer_command
from mimicwarehouse.verify import VERIFY_CONTEXT_SETTINGS, verify_command

#: Commands that must run even when the data root is unsafe: ``doctor`` / ``paths`` report it
#: (exit codes tell); ``guard`` never touches the data root and, as the pre-commit hook, must
#: not be blocked by a mis-set ``MWH_DATA_ROOT`` (EP-4); ``verify`` only runs pytest in a fresh
#: interpreter / reads the roadmap markdown, so a bad root must not hide a roadmap check (EP-6);
#: ``schema`` only reads the packaged YAML contract and the vendored DDL, so a bad root must not
#: hide a schema-drift check (EP-9); ``fixtures`` reads the packaged vocab + contract and writes
#: synthetic files under ``tests/fixtures/`` in the checkout - never the data root (EP-11).
#: Since EP-167 validation is lazy (first ``CliState.settings`` access), so this allow-list only
#: decides *whether* that access validates; replacing it with lazy validation everywhere is the
#: EP-16 decision.
DIAGNOSTIC_COMMANDS: frozenset[str] = frozenset(
    {"doctor", "paths", "guard", "verify", "schema", "fixtures"}
)

app = typer.Typer(
    name="mwh",
    help="mimicwarehouse — local MIMIC-IV data lab (DuckDB + Parquet). "
    "Aggregates only; read GOVERNANCE.md before touching data.",
    no_args_is_help=True,
    rich_markup_mode="rich",
    add_completion=False,
)


class CliState:
    """Per-invocation state handed to commands through ``ctx.obj`` (EP-167 lazy validation).

    :attr:`settings` is a property: the callback stores the unchecked instance (or the
    configuration error it failed with), and the first access by a non-diagnostic command
    runs the D-29 location refusals — raising ``typer.Exit(2)`` with the message — so
    ``--help`` on any subcommand never trips over a broken or unsafe configuration.
    """

    def __init__(
        self,
        *,
        settings: Settings | None,
        pending_error: Exception | None = None,
        diagnostic: bool = False,
        data_root_override: Path | None = None,
    ) -> None:
        self._settings = settings
        self._validated = False
        self.pending_error = pending_error
        self.diagnostic = diagnostic
        self.data_root_override = data_root_override

    @property
    def settings(self) -> Settings:
        if self._settings is None:
            console.print(f"[bold red]mwh:[/] {escape(str(self.pending_error))}", highlight=False)
            raise typer.Exit(code=2)
        if not self.diagnostic and not self._validated:
            try:
                self._settings.require_safe()
            except config.UnsafeLocationError as exc:
                console.print(f"[bold red]mwh:[/] {escape(str(exc))}", highlight=False)
                raise typer.Exit(code=2) from None
            self._validated = True
        return self._settings

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
    settings: Settings | None = None
    pending: Exception | None = None
    try:
        settings = config.load_settings(checked=False, **overrides)
    except (config.ConfigError, config.ValidationError) as exc:
        pending = exc
    unknown = config.unknown_env_keys()
    if unknown:
        err_console.print(
            f"[yellow]mwh:[/] ignoring unknown MWH_* environment variable(s): "
            f"{escape(', '.join(unknown))} — not a Settings field (mwh doctor lists them)",
            highlight=False,
        )
    ctx.obj = CliState(
        settings=settings,
        pending_error=pending,
        diagnostic=ctx.invoked_subcommand in DIAGNOSTIC_COMMANDS,
        data_root_override=data_root,
    )


# --- commands (one line each; keep alphabetical as briefs add them) -----------------------
app.command("build")(build_command)
app.add_typer(canary_app, name="canary")
app.add_typer(catalog_app, name="catalog")
app.add_typer(demo_app, name="demo")
app.command("doctor")(doctor_command)
app.add_typer(fixtures_app, name="fixtures")
app.command("guard")(guard_command)
app.add_typer(inventory_app, name="inventory")
app.command("jobs")(jobs_command)
app.command("paths")(paths_command)
app.add_typer(runs_app, name="runs")
app.add_typer(schema_app, name="schema")
app.command("sql")(sql_command)
app.command("tracer")(tracer_command)
app.command("verify", context_settings=VERIFY_CONTEXT_SETTINGS)(verify_command)


if __name__ == "__main__":  # pragma: no cover
    app()
