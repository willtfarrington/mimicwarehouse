"""``mwh`` — the mimicwarehouse command line (typer + rich; EP-2, DESIGN §15).

Commands live in their own modules and are attached here with **one** ``app.command()`` /
``app.add_typer()`` line each, so later briefs extend without restructuring. The registered
set (the ``# --- commands`` block below is authoritative; retro CLI-4):
``build``/``jobs`` (EP-19, :mod:`mimicwarehouse.dag.cli`) · ``canary`` (EP-171,
:mod:`mimicwarehouse.canary`) · ``catalog``/``sql`` (EP-21, :mod:`mimicwarehouse.catalog.cli`;
since EP-30 ``sql`` routes everything through ``safe_query`` — aggregate-only, audited,
refusals exit 3) · ``codeset`` (EP-40, :mod:`mimicwarehouse.codesets.cli`) · ``demo``
(EP-22, :mod:`mimicwarehouse.demo`) · ``doctor`` (EP-2,
:mod:`mimicwarehouse.doctor`) · ``fixtures`` (EP-11, :mod:`mimicwarehouse.fixtures.cli`) ·
``guard`` (EP-4, :mod:`mimicwarehouse.guard`) · ``inventory`` (EP-10,
:mod:`mimicwarehouse.inventory`) · ``paths`` (EP-3, :mod:`mimicwarehouse.config`) · ``runs``
(``refresh`` EP-30, ``benchmarks`` EP-32, :mod:`mimicwarehouse.runs_cli`) · ``schema`` (EP-9,
:mod:`mimicwarehouse.schema.cli`) · ``tracer`` (EP-31, :mod:`mimicwarehouse.tracer`) ·
``units`` (EP-39, :mod:`mimicwarehouse.units`) · ``verify`` (EP-6,
:mod:`mimicwarehouse.verify`). Later briefs add theirs the same way; a
command is listed here only once its line exists below.

Settings (EP-3, reworked EP-167): the callback loads an **unchecked**
:class:`mimicwarehouse.config.Settings` once per invocation — ``--data-root`` > ``MWH_*`` env >
``.env`` > ``mwh.toml`` > defaults — installs the override process-wide
(:func:`mimicwarehouse.config.configure`, so ``get_settings()`` agrees with ``ctx.obj``) and
hands a :class:`CliState` to the command. A configuration error (broken ``.env``/``mwh.toml``,
an unparsable complex value such as ``MWH_DEV_BUCKETS`` — pydantic-settings'
``SettingsError``, retro CLI-2) is stored as :attr:`CliState.pending_error` instead of
raised, and the D-29 location refusals run on the **first access** of
:attr:`CliState.settings` by a non-diagnostic command — so ``--help``, ``--version`` and
``no_args_is_help`` always work, even over an unsafe or broken configuration (retro CFG-5),
while ``mwh inventory build`` still exits 2 before touching anything. The diagnostic
commands in :data:`DIAGNOSTIC_COMMANDS` (the rule is stated once, on that constant)
receive the unchecked instance so they can *report* the problem.

Errors follow the EP-33 canon (:mod:`mimicwarehouse.console`): ``mwh: <message>`` in bold
red on **stderr** via :func:`~mimicwarehouse.console.fail`, exit ``EXIT_USAGE`` (2).

Import-time budget: ``mwh --help`` must stay under ~0.5 s, so this module never imports
duckdb / pandas / polars / pyarrow — commands import what they need inside their bodies.
The ``mwh`` script entry point is :func:`mimicwarehouse.console.run` (UTF-8 stdio, EP-167),
which calls :data:`app`; ``python -m mimicwarehouse.cli`` still works.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from pydantic_settings import SettingsError
from rich.markup import escape

from mimicwarehouse import __version__, config
from mimicwarehouse.canary import canary_app
from mimicwarehouse.catalog.cli import catalog_app, sql_command
from mimicwarehouse.codesets.cli import codeset_app
from mimicwarehouse.config import Settings, paths_command
from mimicwarehouse.console import EXIT_USAGE, err_console, fail
from mimicwarehouse.dag.cli import build_command, jobs_command
from mimicwarehouse.demo import demo_app
from mimicwarehouse.doctor import doctor_command
from mimicwarehouse.fixtures.cli import fixtures_app
from mimicwarehouse.guard import guard_command
from mimicwarehouse.inventory import inventory_app
from mimicwarehouse.runs_cli import runs_app
from mimicwarehouse.schema.cli import schema_app
from mimicwarehouse.tracer import tracer_command
from mimicwarehouse.units import units_app
from mimicwarehouse.verify import VERIFY_CONTEXT_SETTINGS, verify_command

#: The diagnostic allow-list — the one canonical statement of the rule (EP-33 B8;
#: ``docs/gotchas.md`` § One way to do each thing): **a command is diagnostic iff it never
#: touches the data root**, and membership is pinned by ``tests/ep/test_ep167.py``. Members
#: receive the *unchecked* settings so they can report a bad root instead of being blocked by
#: it: ``doctor`` / ``paths`` report it (exit codes tell); ``guard`` is the pre-commit hook
#: (EP-4); ``verify`` runs pytest in a fresh interpreter / reads the roadmap (EP-6); ``schema``
#: reads the packaged contract and the vendored DDL (EP-9); ``fixtures`` writes synthetic files
#: under ``tests/fixtures/`` in the checkout (EP-11). Every other command writes or reads under
#: the data root, so its first ``CliState.settings`` access runs the D-29 refusals (EP-167).
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
            fail("mwh", str(self.pending_error), code=EXIT_USAGE)
        if not self.diagnostic and not self._validated:
            try:
                self._settings.require_safe()
            except config.UnsafeLocationError as exc:
                fail("mwh", str(exc), code=EXIT_USAGE)
            self._validated = True
        return self._settings


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
    except (config.ConfigError, config.ValidationError, SettingsError) as exc:
        # SettingsError (retro CLI-2): pydantic-settings raises it *before* validation when a
        # complex field's env / .env value fails its JSON parse (an unparsable
        # MWH_DEV_BUCKETS); it is a ValueError, not a ValidationError, so it must be named.
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
app.add_typer(codeset_app, name="codeset")
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
app.add_typer(units_app, name="units")
app.command("verify", context_settings=VERIFY_CONTEXT_SETTINGS)(verify_command)


if __name__ == "__main__":  # pragma: no cover
    app()
