"""``mwh fixtures`` - build the synthetic fixture tree (EP-11 hosp, EP-12 icu).

Attached in :mod:`mimicwarehouse.cli` with one ``app.add_typer(fixtures_app, name="fixtures")``
line and listed in ``DIAGNOSTIC_COMMANDS``: the generator never touches the data root (it reads
the packaged vocab + contract and writes under ``tests/fixtures/`` in the checkout), so a
mis-set ``MWH_DATA_ROOT`` must not block regenerating fixtures. Import cost is typer + rich;
numpy / polars / the contract are imported inside the command.

Console output is ASCII (roadmap Risk 13) and never shows a row - only table names, row counts
and byte sizes (thousands-separated, so no bare 8-digit token can appear).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich import box
from rich.console import Console
from rich.markup import escape
from rich.table import Table as RichTable

fixtures_app = typer.Typer(
    name="fixtures",
    help="Synthetic mini-MIMIC fixture generator (ids >= 90 000 000, D-27): "
    "build [--out] [--seed] [--subjects]. Writes tests/fixtures/ in the checkout - never data.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

console = Console()
err_console = Console(stderr=True)


def _fail(message: str, code: int = 2) -> None:
    err_console.print(f"[bold red]mwh fixtures:[/] {escape(message)}", highlight=False)
    raise typer.Exit(code=code)


@fixtures_app.command("build")
def build_command(
    out: Annotated[
        Path | None,
        typer.Option(
            "--out",
            help="Output directory (default: <workspace>/tests/fixtures, resolved from the "
            "package location - from a wheel install it falls back to the current directory).",
            show_default=False,
        ),
    ] = None,
    seed: Annotated[
        int | None,
        typer.Option("--seed", help="Generator seed (default 2026; the committed fixture's)."),
    ] = None,
    subjects: Annotated[
        int | None,
        typer.Option("--subjects", help="Number of subjects (default 120)."),
    ] = None,
    check: Annotated[
        bool,
        typer.Option(
            "--check/--no-check",
            help="Run the contract + integrity checks before writing (default on).",
        ),
    ] = True,
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable summary.")] = False,
) -> None:
    """Generate the 22 hosp + 9 icu CSVs + manifest.json + README.md (deterministic per seed)."""
    from pydantic import ValidationError

    from mimicwarehouse.fixtures.check import FixtureError
    from mimicwarehouse.fixtures.spec import FixtureSpec
    from mimicwarehouse.fixtures.write import WriteError, build_and_write

    overrides = {}
    if seed is not None:
        overrides["seed"] = seed
    if subjects is not None:
        overrides["n_subjects"] = subjects
    try:
        spec = FixtureSpec(**overrides)
    except ValidationError as exc:
        _fail(str(exc))
        return
    try:
        result = build_and_write(out, spec=spec, check=check)
    except (FixtureError, WriteError) as exc:
        _fail(str(exc), code=1)
        return
    if as_json:
        payload = {
            "out_dir": str(result.out_dir),
            "seed": spec.seed,
            "n_subjects": spec.n_subjects,
            "files": [
                {"path": e.rel_path, "rows": e.rows, "bytes": e.bytes, "sha256": e.sha256}
                for e in result.entries
            ],
            "total_bytes": result.total_bytes,
            "total_rows": result.total_rows,
            "manifest": str(result.manifest_path),
        }
        console.print_json(json.dumps(payload))
        return
    table = RichTable(box=box.SIMPLE, show_edge=False, pad_edge=False)
    table.add_column("file")
    table.add_column("rows", justify="right")
    table.add_column("bytes", justify="right")
    for e in result.entries:
        table.add_row(e.rel_path, f"{e.rows:,}", f"{e.bytes:,}")
    console.print(table)
    console.print(
        f"wrote {len(result.entries)} files, {result.total_rows:,} rows, "
        f"{result.total_bytes:,} bytes ({result.total_bytes / 1_048_576:.2f} MiB) "
        f"under {escape(str(result.out_dir))} (seed {spec.seed}, {spec.n_subjects} subjects); "
        f"manifest {escape(str(result.manifest_path))}",
        highlight=False,
    )


__all__ = ["fixtures_app"]
