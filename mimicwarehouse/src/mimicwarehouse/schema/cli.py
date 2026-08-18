"""``mwh schema`` — list / show / ddl / check / transcribe over the schema contract (EP-9).

Attached in :mod:`mimicwarehouse.cli` with one ``app.add_typer(schema_app, name="schema")`` line
and listed in ``DIAGNOSTIC_COMMANDS``: nothing here touches the data root, so a mis-set
``MWH_DATA_ROOT`` must not hide a schema-drift check. Import cost is typer + rich only; the
contract / yaml / pydantic work is imported inside the commands.

Console output stays cp1252-safe (roadmap Risk 13): plain ASCII, no arrows or box glyphs beyond
what rich's ``Table`` draws with ``box.SIMPLE``.
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

schema_app = typer.Typer(
    name="schema",
    help="Schema contract (YAML transcribed from the vendored mimic-code DDL): "
    "list / show / ddl / check / transcribe. Schema text only - never data.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

console = Console()
err_console = Console(stderr=True)


def _load():
    from mimicwarehouse.schema.contract import SchemaError, load_contract

    try:
        return load_contract()
    except SchemaError as exc:
        err_console.print(f"[bold red]mwh schema:[/] {escape(str(exc))}", highlight=False)
        raise typer.Exit(code=2) from None


def _resolve_table(contract, name: str):
    from mimicwarehouse.schema.contract import SchemaError

    try:
        return contract.table(name)
    except (KeyError, SchemaError) as exc:
        err_console.print(f"[bold red]mwh schema:[/] {escape(str(exc))}", highlight=False)
        raise typer.Exit(code=2) from None


@schema_app.command("list")
def list_command(
    schema: Annotated[
        str | None,
        typer.Option("--schema", "-s", help="Only this schema (e.g. mimiciv_hosp)."),
    ] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    """List the contract's tables with their load metadata."""
    contract = _load()
    tables = contract.by_schema(schema) if schema else contract.tables
    if schema and not tables:
        err_console.print(f"[bold red]mwh schema:[/] no tables in schema {escape(schema)!s}")
        raise typer.Exit(code=2)
    if as_json:
        payload = [
            {
                "table": t.qualified_name,
                "dataset": t.dataset,
                "csv_path": t.csv_path,
                "columns": len(t.columns),
                "primary_key": list(t.primary_key) if t.primary_key else None,
                "uniqueness_hint": list(t.uniqueness_hint) if t.uniqueness_hint else None,
                "subject_keyed": t.subject_keyed,
                "partitioned": t.partitioned,
                "time_column": t.time_column,
                "sort_keys": list(t.sort_keys),
                "load_class": t.load_class,
            }
            for t in tables
        ]
        console.print_json(json.dumps({"tables": payload, "content_hash": contract.content_hash()}))
        return
    rt = RichTable(box=box.SIMPLE, header_style="bold")
    # overflow="fold" everywhere: rich would otherwise truncate with an ellipsis glyph that a
    # cp1252 console cannot show (roadmap Risk 13).
    for col in ("table", "cols", "pk", "time", "sort_keys", "class", "part"):
        rt.add_column(col, overflow="fold")
    for t in tables:
        rt.add_row(
            t.qualified_name,
            str(len(t.columns)),
            ",".join(t.primary_key) if t.primary_key else "-",
            t.time_column or "-",
            ",".join(t.sort_keys) or "-",
            t.load_class,
            "yes" if t.partitioned else "no",
        )
    console.print(rt)
    counts = ", ".join(f"{s}={len(contract.by_schema(s))}" for s in contract.schema_names())
    console.print(f"{len(tables)} table(s); {counts}; contract {contract.content_hash()[:12]}")


@schema_app.command("show")
def show_command(
    table: Annotated[str, typer.Argument(help="<schema>.<table>, e.g. mimiciv_hosp.patients")],
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    """Show one table: metadata, columns, keys, comments (schema text only)."""
    contract = _load()
    t = _resolve_table(contract, table)
    fks = contract.foreign_keys_of(t)
    if as_json:
        payload = t.model_dump(mode="json", by_alias=True)
        payload["foreign_keys"] = [fk.model_dump(mode="json") for fk in fks]
        console.print_json(json.dumps(payload))
        return
    console.print(f"[bold]{t.qualified_name}[/]  ({t.dataset}; {t.csv_path})", highlight=False)
    if t.comment:
        console.print(escape(t.comment), highlight=False)
    meta = RichTable(box=box.SIMPLE, show_header=False)
    meta.add_column("k", style="dim", overflow="fold")
    meta.add_column("v", overflow="fold")
    meta.add_row("primary_key", ",".join(t.primary_key) if t.primary_key else "none (upstream)")
    if t.uniqueness_hint:
        meta.add_row(
            "uniqueness_hint", ",".join(t.uniqueness_hint) + "  (candidate; test, never assert)"
        )
    meta.add_row("subject_keyed / partitioned", f"{t.subject_keyed} / {t.partitioned}")
    meta.add_row("time_column", t.time_column or "-")
    meta.add_row("sort_keys", ",".join(t.sort_keys) or "-")
    meta.add_row("load_class", t.load_class)
    meta.add_row("expected_rows_source", t.expected_rows_source or "-")
    console.print(meta)
    cols = RichTable(box=box.SIMPLE, header_style="bold")
    for c in ("column", "type", "null", "unit_of", "comment"):
        cols.add_column(c, overflow="fold")
    for c in t.columns:
        note = c.comment or ""
        if c.upstream_type:
            note = f"[upstream {c.upstream_type}] {note}"
        if c.upstream_nullable is not None:
            note = f"[upstream {'NULL' if c.upstream_nullable else 'NOT NULL'}] {note}"
        cols.add_row(
            c.name, c.duckdb_type, "" if c.nullable else "NOT NULL", c.unit_of or "", escape(note)
        )
    console.print(cols)
    if fks:
        console.print("foreign keys:")
        for fk in fks:
            console.print(
                f"  {','.join(fk.columns)} -> {fk.ref_table}({','.join(fk.ref_columns)})"
                f"  [{fk.source}]",
                highlight=False,
            )


@schema_app.command("ddl")
def ddl_command(
    table: Annotated[
        str | None,
        typer.Argument(help="<schema>.<table>; omit with --all for every table."),
    ] = None,
    all_tables: Annotated[bool, typer.Option("--all", help="Every table, schemas first.")] = False,
    if_not_exists: Annotated[bool, typer.Option("--if-not-exists")] = False,
) -> None:
    """Print CREATE TABLE DDL (DuckDB dialect) for one table or the whole contract."""
    contract = _load()
    if all_tables:
        typer.echo(contract.duckdb_schema_ddl())
        for t in contract.tables:
            typer.echo(t.duckdb_ddl(if_not_exists=if_not_exists))
        return
    if not table:
        err_console.print("[bold red]mwh schema:[/] give <schema>.<table> or --all")
        raise typer.Exit(code=2)
    t = _resolve_table(contract, table)
    typer.echo(t.duckdb_ddl(if_not_exists=if_not_exists))


@schema_app.command("check")
def check_command(
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    """Drift oracle: re-parse the vendored create.sql / constraint.sql; fail on any difference."""
    from mimicwarehouse.concepts import vendor_info
    from mimicwarehouse.schema.contract import SchemaError
    from mimicwarehouse.schema.transcribe import check_contract

    contract = _load()
    try:
        drifts = check_contract(contract)
    except (SchemaError, FileNotFoundError) as exc:
        err_console.print(f"[bold red]mwh schema check:[/] {escape(str(exc))}", highlight=False)
        raise typer.Exit(code=2) from None
    info = vendor_info()
    if as_json:
        console.print_json(
            json.dumps(
                {
                    "vendored_sha": info.sha,
                    "contract_hash": contract.content_hash(),
                    "tables": len(contract.tables),
                    "drift": [
                        {
                            "schema": d.schema,
                            "table": d.table,
                            "column": d.column,
                            "kind": d.kind,
                            "ddl": d.expected,
                            "yaml": d.actual,
                        }
                        for d in drifts
                    ],
                }
            )
        )
    else:
        for d in drifts:
            console.print(f"  DRIFT {escape(str(d))}", highlight=False)
        verdict = "no drift" if not drifts else f"{len(drifts)} drift finding(s)"
        n = len(contract.tables)
        console.print(
            f"schema check: {n} tables vs mimic-code {info.short_sha}: {verdict}",
            highlight=False,
        )
    raise typer.Exit(code=1 if drifts else 0)


@schema_app.command("transcribe")
def transcribe_command(
    create_sql: Annotated[
        Path,
        typer.Option("--create-sql", help="Postgres create.sql to parse (a vendored file)."),
    ],
    schema: Annotated[str, typer.Option("--schema", help="Schema to extract, e.g. mimiciv_hosp.")],
    out: Annotated[Path, typer.Option("--out", help="Where to write the YAML draft.")],
    dataset: Annotated[str | None, typer.Option("--dataset")] = None,
    csv_dir: Annotated[str | None, typer.Option("--csv-dir")] = None,
    source_ddl: Annotated[
        str | None,
        typer.Option("--source-ddl", help="Upstream-relative path recorded in the draft header."),
    ] = None,
) -> None:
    """Parse a vendored create.sql into a YAML draft of <schema>.yaml (then curate + check)."""
    from mimicwarehouse.schema.contract import SchemaError
    from mimicwarehouse.schema.transcribe import draft_schema_yaml, parse_create_sql

    if not create_sql.is_file():
        err_console.print(
            f"[bold red]mwh schema transcribe:[/] no such file {escape(str(create_sql))}"
        )
        raise typer.Exit(code=2)
    try:
        tables = parse_create_sql(create_sql.read_text(encoding="utf-8"))
        text = draft_schema_yaml(
            tables,
            schema,
            dataset=dataset,
            csv_dir=csv_dir,
            source_ddl=source_ddl or create_sql.as_posix(),
        )
    except SchemaError as exc:
        err_console.print(
            f"[bold red]mwh schema transcribe:[/] {escape(str(exc))}", highlight=False
        )
        raise typer.Exit(code=2) from None
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8", newline="\n")
    n = sum(1 for t in tables if t.schema == schema)
    console.print(f"wrote {n} table draft(s) for {schema} to {out}", highlight=False)


__all__ = ["schema_app"]
