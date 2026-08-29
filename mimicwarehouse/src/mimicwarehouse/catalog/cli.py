"""``mwh catalog info``, ``mwh catalog dictionary`` and the interim ``mwh sql``
(EP-21 item 3, EP-29 item 4; attached in :mod:`mimicwarehouse.cli`).

``catalog info`` prints ``meta.catalog_info`` and ``meta.catalog_tables`` — metadata
only. ``sql`` is registered with its **final** option surface (``--tier``, ``--k``,
``--format``) but, until EP-30 replaces its body with ``safe_query``, supports only
``--tables``, ``--describe <schema.table>`` and ``--count <schema.table>``: any
free-form statement exits 2 with "free-form SQL arrives with safe_query (EP-30)"
(GOVERNANCE §4 — sessions see schemas, counts and metadata, never rows). A count below
the small-cell threshold (0 < n < k) prints suppressed (GOVERNANCE §5; the shared
``disclose`` module arrives with EP-43).

Import budget: duckdb and the connect module are imported inside the command bodies
(cli.py rule — ``mwh --help`` stays under ~0.5 s).
"""

from __future__ import annotations

import json
import re
import sys
from typing import TYPE_CHECKING, Annotated

import typer
from rich.markup import escape

from mimicwarehouse.console import console

if TYPE_CHECKING:  # pragma: no cover
    import duckdb

    from mimicwarehouse.cli import CliState
    from mimicwarehouse.config import Settings

TIERS = ("fixture", "demo", "dev", "full")

#: ``schema.table`` the metadata subcommands accept (contract identifiers only).
QUALIFIED_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*$")

FREE_FORM_MESSAGE = "free-form SQL arrives with safe_query (EP-30)"

catalog_app = typer.Typer(
    name="catalog",
    help=(
        "Per-tier catalog metadata (EP-21/EP-29): mwh catalog info --tier <t>, "
        "mwh catalog dictionary --tier <t> [--out PATH]."
    ),
    no_args_is_help=True,
    rich_markup_mode="rich",
)


def _fail(prefix: str, message: str, code: int = 2) -> None:
    console.print(f"[bold red]{prefix}:[/] {escape(message)}", highlight=False)
    raise typer.Exit(code=code)


def _require_tier(prefix: str, tier: str) -> None:
    if tier not in TIERS:
        _fail(prefix, f"unknown tier {tier!r}; expected one of {', '.join(TIERS)}")


def _open(tier: str, settings: Settings, prefix: str) -> duckdb.DuckDBPyConnection:
    from mimicwarehouse.catalog.connect import CatalogOpenError, open_catalog

    try:
        return open_catalog(tier, settings=settings)
    except CatalogOpenError as exc:
        _fail(prefix, str(exc))
        raise AssertionError from exc  # unreachable; _fail always raises


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
        _fail(prefix, str(exc))
        raise AssertionError from exc  # unreachable; _fail always raises
    console.print(
        f"wrote {result.path} ({result.tables} table(s), {result.columns} column(s), "
        f"tier {result.tier}, build {result.build_id})",
        highlight=False,
    )


def _validated_table(con: duckdb.DuckDBPyConnection, qualified: str, prefix: str) -> str:
    """``schema.table`` checked against the identifier grammar **and** the catalog's own
    information_schema (parameterized) before it is ever interpolated into SQL."""
    if not QUALIFIED_RE.match(qualified):
        _fail(prefix, f"{qualified!r} is not a schema.table identifier")
    schema, table = qualified.split(".", 1)
    hit = con.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_schema = ? AND table_name = ?",
        [schema, table],
    ).fetchone()
    if hit is None:
        _fail(prefix, f"no table {qualified} in this catalog (mwh catalog info lists them)")
    return f'{schema}."{table}"'


def sql_command(
    ctx: typer.Context,
    statement: Annotated[
        str | None,
        typer.Argument(
            metavar="[STATEMENT]",
            help="Free-form SQL — refused until EP-30 ships safe_query.",
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
            help="Small-cell threshold (default: settings.k_suppression = 11). Do not lower.",
        ),
    ] = None,
    output_format: Annotated[
        str,
        typer.Option("--format", help="Output format: table | json."),
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
    """Query a tier catalog (EP-30 wires safe_query; until then: metadata and counts only)."""
    prefix = "mwh sql"
    state: CliState = ctx.obj
    if output_format not in ("table", "json"):
        _fail(prefix, f"unknown --format {output_format!r}; expected table | json")
    if statement is not None:
        _fail(prefix, FREE_FORM_MESSAGE)
    if sum([tables, describe is not None, count is not None]) != 1:
        _fail(
            prefix,
            "give exactly one of --tables, --describe SCHEMA.TABLE or --count SCHEMA.TABLE "
            f"({FREE_FORM_MESSAGE})",
        )
    settings = state.settings
    resolved_tier = tier if tier is not None else settings.default_tier
    _require_tier(prefix, resolved_tier)
    threshold = k if k is not None else settings.k_suppression

    con = _open(resolved_tier, settings, prefix)
    try:
        if tables:
            rows = con.execute(
                "SELECT table_schema || '.' || table_name FROM information_schema.tables "
                "WHERE table_schema LIKE 'mimiciv%' OR table_schema IN ('meta', 'marts') "
                "ORDER BY 1"
            ).fetchall()
            names = [r[0] for r in rows]
            if output_format == "json":
                sys.stdout.write(json.dumps({"tier": resolved_tier, "tables": names}) + "\n")
            else:
                for name in names:
                    console.print(escape(name), highlight=False)
            return
        if describe is not None:
            target = _validated_table(con, describe, prefix)
            described = con.execute(f"DESCRIBE {target}").fetchall()
            # contract descriptions from the catalog's COMMENT ONs (EP-29; DESCRIBE
            # itself does not surface them)
            schema, table = describe.split(".", 1)
            comments = dict(
                con.execute(
                    "SELECT column_name, comment FROM duckdb_columns() "
                    "WHERE schema_name = ? AND table_name = ?",
                    [schema, table],
                ).fetchall()
            )
            if output_format == "json":
                payload = [
                    {"column": d[0], "type": d[1], "null": d[2], "comment": comments.get(d[0])}
                    for d in described
                ]
                sys.stdout.write(
                    json.dumps({"tier": resolved_tier, "table": describe, "columns": payload})
                    + "\n"
                )
                return
            from rich.table import Table as RichTable

            listing = RichTable(title=f"{describe} ({resolved_tier})", pad_edge=False)
            for col in ("column", "type", "null", "comment"):
                listing.add_column(col)
            for d in described:
                listing.add_row(
                    escape(str(d[0])),
                    escape(str(d[1])),
                    str(d[2]),
                    escape(str(comments.get(d[0]) or "")),
                )
            console.print(listing)
            return
        assert count is not None
        target = _validated_table(con, count, prefix)
        row = con.execute(f"SELECT count(*) FROM {target}").fetchone()
        assert row is not None
        n = int(row[0])
        # GOVERNANCE §5: a count below the threshold is a small cell — suppressed on
        # anything returned to a session (EP-43 centralises this in `disclose`).
        suppressed = 0 < n < threshold
        if output_format == "json":
            sys.stdout.write(
                json.dumps(
                    {
                        "tier": resolved_tier,
                        "table": count,
                        "count": None if suppressed else n,
                        "suppressed": suppressed,
                        "k": threshold,
                    }
                )
                + "\n"
            )
        elif suppressed:
            console.print(
                f"{escape(count)} count(*) < {threshold} (suppressed, GOVERNANCE §5)",
                highlight=False,
            )
        else:
            console.print(f"{escape(count)} count(*) = {n:,}", highlight=False)
    finally:
        con.close()


__all__ = [
    "FREE_FORM_MESSAGE",
    "catalog_app",
    "dictionary_command",
    "info_command",
    "sql_command",
]
