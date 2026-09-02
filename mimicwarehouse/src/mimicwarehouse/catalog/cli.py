"""``mwh catalog info``, ``mwh catalog dictionary`` and the final ``mwh sql``
(EP-21 item 3, EP-29 item 4, EP-30 item 4; attached in :mod:`mimicwarehouse.cli`).

``catalog info`` prints ``meta.catalog_info`` and ``meta.catalog_tables`` — metadata
only. ``sql`` routes **everything** — free-form statements and the ``--tables`` /
``--describe`` / ``--count`` conveniences — through
:func:`mimicwarehouse.safe.safe_query` (EP-30): read-only, allow-listed, aggregate-only,
row-capped, k-suppressed, audited. A refusal prints the reason and exits 3
(GOVERNANCE §4 — sessions see schemas, counts, dictionaries and statistics, never rows).

Output discipline (EP-170 amendment 2, FC-16): ``table`` / ``csv`` output
thousands-separates integers via :func:`mimicwarehouse.inventory.fmt_int` so pasted
numbers never trip guard G4; ``--format json`` keeps raw ints and is never pasted into
tracked files. Free-form output carries the audit footer
``k=<k>: <rows> rows suppressed · audit <id> · tier <t> · snapshot <id>`` (on stderr for
``csv`` so stdout stays parseable). Exports are EP-59's — there is no ``--out``.

Import budget: duckdb, polars and the safe module are imported inside the command bodies
(cli.py rule — ``mwh --help`` stays under ~0.5 s).
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Annotated, Any

import typer
from rich.markup import escape

from mimicwarehouse.console import (
    EXIT_REFUSED,
    EXIT_USAGE,
    console,
    console_safe,
    err_console,
    fail,
)

if TYPE_CHECKING:  # pragma: no cover
    import duckdb

    from mimicwarehouse.cli import CliState
    from mimicwarehouse.config import Settings
    from mimicwarehouse.safe import SafeResult

TIERS = ("fixture", "demo", "dev", "full")

#: ``schema.table`` the metadata subcommands accept (contract identifiers only).
QUALIFIED_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*$")

# ``EXIT_REFUSED`` (3, a safe_query refusal; brief EP-30 item 4) is re-exported from
# :mod:`mimicwarehouse.console` since EP-33 — test_ep21 / test_ep30 import it from here.

catalog_app = typer.Typer(
    name="catalog",
    help=(
        "Per-tier catalog metadata (EP-21/EP-29): mwh catalog info --tier <t>, "
        "mwh catalog dictionary --tier <t> [--out PATH]."
    ),
    no_args_is_help=True,
    rich_markup_mode="rich",
)


@contextmanager
def safe_cli_errors(prefix: str) -> Iterator[None]:
    """The one error mapping of every command built on ``safe_query`` (EP-33 B1d):
    :class:`~mimicwarehouse.safe.SafeQueryRefused` -> ``refused: <reason>``, exit
    :data:`EXIT_REFUSED` (3); :class:`~mimicwarehouse.safe.SafeQueryError` (usage) and
    :class:`~mimicwarehouse.catalog.connect.CatalogOpenError` (environment) -> exit
    :data:`EXIT_USAGE` (2). Messages go to stderr through :func:`console.fail`; nothing
    tracebacks to exit 1."""
    from mimicwarehouse.catalog.connect import CatalogOpenError
    from mimicwarehouse.safe import SafeQueryError, SafeQueryRefused

    try:
        yield
    except SafeQueryRefused as exc:
        fail(prefix, f"refused: {exc}", code=EXIT_REFUSED)
    except SafeQueryError as exc:
        fail(prefix, str(exc), code=EXIT_USAGE)
    except CatalogOpenError as exc:
        fail(prefix, str(exc), code=EXIT_USAGE)


def _require_tier(prefix: str, tier: str) -> None:
    if tier not in TIERS:
        fail(prefix, f"unknown tier {tier!r}; expected one of {', '.join(TIERS)}")


def _open(tier: str, settings: Settings, prefix: str) -> duckdb.DuckDBPyConnection:
    from mimicwarehouse.catalog.connect import CatalogOpenError, open_catalog

    try:
        return open_catalog(tier, settings=settings)
    except CatalogOpenError as exc:
        fail(prefix, str(exc), code=EXIT_USAGE)


@catalog_app.command("info")
def info_command(
    ctx: typer.Context,
    tier: Annotated[
        str, typer.Option("--tier", help="Tier catalog to describe: fixture | demo | dev | full.")
    ],
    json_output: Annotated[
        bool, typer.Option("--json", help="Print catalog_info + catalog_tables as JSON.")
    ] = False,
) -> None:
    """Print meta.catalog_info and meta.catalog_tables of one tier's catalog (metadata only)."""
    state: CliState = ctx.obj
    _require_tier("mwh catalog info", tier)
    con = _open(tier, state.settings, "mwh catalog info")
    try:
        info_cols = [
            "build_id",
            "tier",
            "duckdb_version",
            "package_version",
            "git_sha",
            "core_snapshot_id",
            "lake_root",
            "built_at",
            "k_default",
            "dev_buckets",
        ]
        info_row = con.execute(f"SELECT {', '.join(info_cols)} FROM meta.catalog_info").fetchone()
        assert info_row is not None  # open_catalog verified the row exists
        info = dict(zip(info_cols, info_row, strict=True))
        tables = con.execute(
            'SELECT "schema", "table", kind, status, rows_hint, map_notes '
            'FROM meta.catalog_tables ORDER BY "schema", "table"'
        ).fetchall()
    finally:
        con.close()

    if json_output:
        payload = {
            "catalog_info": info,
            "catalog_tables": [
                {
                    "schema": s,
                    "table": t,
                    "kind": k,
                    "status": st,
                    "rows_hint": r,
                    "map_notes": None if mn is None else json.loads(mn),
                }
                for s, t, k, st, r, mn in tables
            ],
        }
        sys.stdout.write(json.dumps(payload, indent=2, default=str) + "\n")
        return

    from rich.table import Table as RichTable

    kv = RichTable(title=f"meta.catalog_info ({tier})", show_header=False, pad_edge=False)
    kv.add_column("key", style="bold")
    kv.add_column("value", overflow="fold")
    for key in info_cols:
        kv.add_row(key, escape(str(info[key])))
    console.print(kv)

    # map_notes (EP-22 lossy column maps) is shown only when some table carries any —
    # expected never while the demo map stays the identity, so the table stays narrow.
    any_notes = any(mn is not None for *_rest, mn in tables)
    listing = RichTable(title="meta.catalog_tables", pad_edge=False)
    columns: list[tuple[str, str]] = [
        ("schema", "left"),
        ("table", "left"),
        ("kind", "left"),
        ("status", "left"),
        ("rows_hint", "right"),
    ]
    if any_notes:
        columns.append(("map_notes", "left"))
    for col, justify in columns:
        listing.add_column(col, justify=justify)  # type: ignore[arg-type]
    for s, t, k, st, r, mn in tables:
        row = [escape(s), escape(t), k, st or "", "" if r is None else f"{r:,}"]
        if any_notes:
            row.append("" if mn is None else escape(str(mn)))
        listing.add_row(*row)
    console.print(listing)
    present = sum(1 for row in tables if row[2] != "missing")
    console.print(
        f"{present} cataloged ({sum(1 for r in tables if r[2] == 'table')} table(s), "
        f"{sum(1 for r in tables if r[2] == 'view')} view(s)), "
        f"{len(tables) - present} missing",
        highlight=False,
    )


@catalog_app.command("dictionary")
def dictionary_command(
    ctx: typer.Context,
    tier: Annotated[
        str,
        typer.Option("--tier", help="Tier catalog to render: fixture | demo | dev | full."),
    ] = "full",
    out: Annotated[
        str | None,
        typer.Option(
            "--out",
            metavar="PATH",
            help="Output path (default: mimicwarehouse/DATA-DICTIONARY.md).",
        ),
    ] = None,
) -> None:
    """Generate DATA-DICTIONARY.md from one tier catalog's meta.* schema (EP-29)."""
    from pathlib import Path

    from mimicwarehouse.catalog.connect import CatalogOpenError
    from mimicwarehouse.catalog.dictionary import generate_dictionary

    state: CliState = ctx.obj
    prefix = "mwh catalog dictionary"
    _require_tier(prefix, tier)
    try:
        result = generate_dictionary(
            tier, state.settings, out=Path(out) if out is not None else None
        )
    except CatalogOpenError as exc:
        fail(prefix, str(exc), code=EXIT_USAGE)
    console.print(
        f"wrote {result.path} ({result.tables} table(s), {result.columns} column(s), "
        f"tier {result.tier}, build {result.build_id})",
        highlight=False,
    )


# ---------------------------------------------------------------------------
# mwh sql — the final body over safe_query (EP-30 item 4)
# ---------------------------------------------------------------------------

#: The statement behind ``--tables`` (information_schema only — metadata-exempt).
_TABLES_SQL = (
    "SELECT table_schema || '.' || table_name AS table_name "
    "FROM information_schema.tables "
    "WHERE table_schema LIKE 'mimiciv%' OR table_schema IN ('meta', 'marts') "
    "ORDER BY 1"
)


def _cell(value: Any) -> str:
    """One table/CSV cell: integers thousands-separated (FC-16 / guard G4)."""
    from mimicwarehouse.inventory import fmt_int

    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return fmt_int(value)
    return str(value)


def _footer(result: SafeResult) -> str:
    from mimicwarehouse.inventory import fmt_int

    return (
        f"k={result.k}: {fmt_int(result.rows_suppressed)} rows suppressed"
        f" · audit {result.audit_id}"
        f" · tier {result.tier}"
        f" · snapshot {result.snapshot_id or '-'}"
    )


def _print_free_form(result: SafeResult, output_format: str) -> None:
    df = result.df
    if output_format == "json":
        payload = {
            "tier": result.tier,
            "k": result.k,
            "n_rows": result.n_rows,
            "rows_suppressed": result.rows_suppressed,
            "audit_id": result.audit_id,
            "snapshot_id": result.snapshot_id,
            "columns": df.columns,
            "rows": df.to_dicts(),
        }
        sys.stdout.write(json.dumps(payload, default=str) + "\n")
        return
    if output_format == "csv":
        import csv

        writer = csv.writer(sys.stdout, lineterminator="\n")
        writer.writerow(df.columns)
        for row in df.rows():
            writer.writerow([_cell(v) for v in row])
        err_console.print(console_safe(escape(_footer(result))), highlight=False)
        return
    from rich.table import Table as RichTable

    listing = RichTable(pad_edge=False)
    for name, dtype in df.schema.items():
        listing.add_column(name, justify="right" if dtype.is_numeric() else "left")
    for row in df.rows():
        listing.add_row(*(escape(_cell(v)) for v in row))
    console.print(listing)
    console.print(console_safe(escape(_footer(result))), highlight=False)


def sql_command(
    ctx: typer.Context,
    statement: Annotated[
        str | None,
        typer.Argument(
            metavar="[STATEMENT]",
            help="One aggregate SELECT (or DESCRIBE schema.table / SHOW TABLES) — "
            "runs through safe_query (EP-30): allow-listed, k-suppressed, audited.",
        ),
    ] = None,
    tier: Annotated[
        str | None,
        typer.Option("--tier", help="Tier catalog to query (default: settings.default_tier)."),
    ] = None,
    k: Annotated[
        int | None,
        typer.Option(
            "--k",
            help="Small-cell threshold (default: settings.k_suppression = 11); "
            "k < 11 is refused on dev/full (D-31/D-33).",
        ),
    ] = None,
    row_cap: Annotated[
        int,
        typer.Option("--row-cap", help="Maximum result rows after suppression."),
    ] = 200,
    output_format: Annotated[
        str,
        typer.Option("--format", help="Output format: table | csv | json."),
    ] = "table",
    tables: Annotated[
        bool, typer.Option("--tables", help="List the catalog's schema.table names.")
    ] = False,
    describe: Annotated[
        str | None,
        typer.Option("--describe", metavar="SCHEMA.TABLE", help="Column names/types of a table."),
    ] = None,
    count: Annotated[
        str | None,
        typer.Option("--count", metavar="SCHEMA.TABLE", help="count(*) of a table."),
    ] = None,
) -> None:
    """Query a tier catalog through safe_query (EP-30): aggregates, schemas and counts
    only — refusals exit 3 with the reason; every call is audited."""
    prefix = "mwh sql"
    state: CliState = ctx.obj
    if output_format not in ("table", "csv", "json"):
        fail(prefix, f"unknown --format {output_format!r}; expected table | csv | json")
    selectors = sum([statement is not None, tables, describe is not None, count is not None])
    if selectors != 1:
        fail(
            prefix,
            "give exactly one of a STATEMENT, --tables, --describe SCHEMA.TABLE or "
            "--count SCHEMA.TABLE",
        )
    settings = state.settings
    for qualified in (describe, count):
        if qualified is not None and not QUALIFIED_RE.match(qualified):
            fail(prefix, f"{qualified!r} is not a schema.table identifier")

    from mimicwarehouse.safe import safe_query

    def run(sql: str) -> SafeResult:
        # tier / k pass through as given (None -> settings.default_tier / k_suppression
        # inside safe_query, which also audits an unknown tier as a usage error; EP-33 B1d)
        with safe_cli_errors(prefix):
            return safe_query(sql, tier=tier, k=k, row_cap=row_cap, settings=settings)
        raise AssertionError  # unreachable: safe_cli_errors exits on every error

    if tables:
        result = run(_TABLES_SQL)
        names = [str(r[0]) for r in result.df.rows()]
        if output_format == "json":
            sys.stdout.write(json.dumps({"tier": result.tier, "tables": names}) + "\n")
        else:
            for name in names:
                console.print(escape(name), highlight=False)
        return

    if describe is not None:
        schema, table = describe.split(".", 1)
        result = run(f'DESCRIBE {schema}."{table}"')
        described = result.df
        comments_result = run(
            "SELECT column_name, comment FROM duckdb_columns() "
            f"WHERE schema_name = '{schema}' AND table_name = '{table}'"
        )
        comments = {str(name): comment for name, comment in comments_result.df.rows()}
        if output_format == "json":
            payload = [
                {
                    "column": row["column_name"],
                    "type": row["column_type"],
                    "null": row["null"],
                    "comment": comments.get(str(row["column_name"])),
                }
                for row in described.to_dicts()
            ]
            sys.stdout.write(
                json.dumps({"tier": result.tier, "table": describe, "columns": payload}) + "\n"
            )
            return
        from rich.table import Table as RichTable

        listing = RichTable(title=f"{describe} ({result.tier})", pad_edge=False)
        for col in ("column", "type", "null", "comment"):
            listing.add_column(col)
        for row in described.to_dicts():
            name = str(row["column_name"])
            listing.add_row(
                escape(name),
                escape(str(row["column_type"])),
                str(row["null"]),
                escape(str(comments.get(name) or "")),
            )
        console.print(listing)
        return

    if count is not None:
        schema, table = count.split(".", 1)
        result = run(f'SELECT count(*) AS n FROM {schema}."{table}"')
        suppressed = result.rows_suppressed > 0
        n = None if suppressed or result.df.is_empty() else int(result.df["n"][0])
        if output_format == "json":
            sys.stdout.write(
                json.dumps(
                    {
                        "tier": result.tier,
                        "table": count,
                        "count": n,
                        "suppressed": suppressed,
                        "k": result.k,
                    }
                )
                + "\n"
            )
        elif suppressed:
            console.print(
                f"{escape(count)} count(*) < {result.k} (suppressed, GOVERNANCE §5)",
                highlight=False,
            )
        else:
            from mimicwarehouse.inventory import fmt_int

            console.print(f"{escape(count)} count(*) = {fmt_int(n)}", highlight=False)
        return

    assert statement is not None
    _print_free_form(run(statement), output_format)


__all__ = [
    "EXIT_REFUSED",
    "EXIT_USAGE",
    "catalog_app",
    "dictionary_command",
    "info_command",
    "safe_cli_errors",
    "sql_command",
]
