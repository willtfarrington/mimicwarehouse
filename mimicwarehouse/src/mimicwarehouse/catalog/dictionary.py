"""``DATA-DICTIONARY.md`` generator (EP-29 item 4; DESIGN §15).

:func:`generate_dictionary` renders one tier's ``meta.*`` dictionary tables (EP-29
item 3) as Markdown — header (build id, tier, snapshot id, DuckDB version, catalog build
date, the MIMIC-IV caveats paragraph, the disclosure-sidecar note), then one section
per table: description, manifest row count, Parquet size, materialization, and a column
table (name, type, nullable, id/free-text flags, null %, approx distinct, description).

Disclosure shape (GOVERNANCE §3/§5): everything in the output is contract text or an
aggregate count. Integers go through :func:`mimicwarehouse.inventory.fmt_int`
(thousands-separated — guard G4 refuses bare 8-digit tokens, ledger FC-16), distinct
counts below the k = 11 threshold render as ``<11``, and no per-value frequency ever
appears (the profile does not compute any). Ordering is deterministic (schema, table,
ordinal) and every timestamp comes from ``meta.catalog_info``, so regenerating from an
unchanged catalog is byte-identical — a clean diff.

The committed file carries its ``.disclosure.json`` sidecar (EP-43 retroactive check,
EP-33 amendment a): after regenerating, run ``mwh disclose check DATA-DICTIONARY.md
--write-sidecar`` so ``mwh disclose verify`` (and ``test_ep43``) still match the bytes.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mimicwarehouse.config import Settings, Tier, get_settings, workspace_root
from mimicwarehouse.inventory import fmt_bytes_mb, fmt_int

if TYPE_CHECKING:  # pragma: no cover
    import duckdb

DEFAULT_FILENAME = "DATA-DICTIONARY.md"

#: Distinct counts below this render as ``<11`` (D-33 presentation rule; the k default).
SMALL_CELL_K = 11

#: The standing MIMIC-IV caveats every reader of a count must know (brief Context; D-33).
CAVEATS = (
    "**Caveats (MIMIC-IV de-identification; GOVERNANCE section 5, D-33).** All timestamps "
    "are date-shifted per subject: absolute dates are meaningless and `anchor_year_group` "
    "(patients) is the only calendar anchor. Patients aged 89 and over appear with "
    "`anchor_age` 91. The `dod` (date of death) horizon is roughly one year after the last "
    "hospital discharge; later deaths are not captured. All row counts and distinct counts "
    "here are aggregate metadata over a retrospective EHR extract; distinct counts under "
    "11 are shown as `<11`. Identifier and free-text columns are flagged: `safe_query` "
    "(EP-30) and `mwh disclose check` (EP-43) refuse to return them."
)


@dataclass(slots=True)
class DictionaryResult:
    """What one :func:`generate_dictionary` wrote (counts, ids and the path only)."""

    path: Path
    tier: str
    build_id: str
    tables: int = 0
    columns: int = 0


def default_out_path() -> Path:
    """``mimicwarehouse/DATA-DICTIONARY.md`` (the workspace root, DESIGN §15)."""
    return workspace_root() / DEFAULT_FILENAME


def _cell(text: str | None) -> str:
    """One Markdown table cell: pipe-escaped, single-line, ``-`` when empty."""
    if not text:
        return "-"
    return " ".join(str(text).split()).replace("|", "\\|")


def _fmt_null_pct(value: float | None) -> str:
    return "-" if value is None else f"{value * 100:.1f}%"


def _fmt_distinct(value: int | None, k: int = SMALL_CELL_K) -> str:
    if value is None:
        return "-"
    return f"<{k}" if value < k else fmt_int(value)


def _flags(is_identifier: bool, is_free_text: bool) -> str:
    parts = (["id"] if is_identifier else []) + (["text"] if is_free_text else [])
    return ", ".join(parts) if parts else "-"


def render_dictionary(con: duckdb.DuckDBPyConnection) -> tuple[str, DictionaryResult]:
    """The Markdown text for one open tier catalog (deterministic; module docstring)."""
    info_row = con.execute(
        "SELECT build_id, tier, duckdb_version, core_snapshot_id, built_at, dev_buckets, "
        "k_default FROM meta.catalog_info"
    ).fetchone()
    assert info_row is not None  # open_catalog verified the row exists
    build_id, tier, duckdb_version, snapshot_id, built_at, dev_buckets, k_raw = info_row
    k = max(int(k_raw or SMALL_CELL_K), SMALL_CELL_K)  # never lower than the D-33 default
    tables = con.execute(
        'SELECT "schema", "table", description, kind, partitioned, row_count, bytes, files '
        'FROM meta.tables ORDER BY "schema", "table"'
    ).fetchall()
    columns = con.execute(
        'SELECT "schema", "table", "column", duckdb_type, nullable, description, '
        "is_identifier, is_free_text, unit_hint, null_pct, approx_distinct "
        'FROM meta.columns ORDER BY "schema", "table", ordinal'
    ).fetchall()
    by_table: dict[tuple[str, str], list[tuple[Any, ...]]] = {}
    for row in columns:
        by_table.setdefault((row[0], row[1]), []).append(row)

    lines: list[str] = [
        "# MIMIC-IV data dictionary (mimicwarehouse)",
        "",
        "> Generated by `mwh catalog dictionary` (EP-29) from the `meta.*` schema of the "
        f"`{tier}` tier catalog - do not edit by hand; regenerate after a catalog rebuild.",
        "> Disclosure: this file passes `mwh disclose check` and is committed with the "
        "sidecar `DATA-DICTIONARY.md.disclosure.json` (EP-43; re-run "
        "`mwh disclose check DATA-DICTIONARY.md --write-sidecar` after regenerating); "
        "content is contract text and aggregate counts only (GOVERNANCE section 3).",
        "",
        "| | |",
        "|---|---|",
        f"| Build id | `{build_id}` |",
        f"| Tier | `{tier}` |",
        f"| Core snapshot id | `{snapshot_id}` |",
        f"| DuckDB | {duckdb_version} |",
        f"| Catalog built at | {built_at} |",
    ]
    if tier == "dev":
        lines.append(f"| Dev buckets | `{dev_buckets}` |")
    lines += ["", CAVEATS, ""]

    result = DictionaryResult(path=Path(), tier=str(tier), build_id=str(build_id))
    current_schema = None
    for schema, table, description, kind, partitioned, row_count, size, files in tables:
        if schema != current_schema:
            current_schema = schema
            lines += [f"## {schema}", ""]
        lines += [f"### {schema}.{table}", ""]
        if description:
            lines += [description, ""]
        if kind == "missing":
            lines += ["*Not staged in this tier's lake - no rows cataloged.*", ""]
        else:
            shape = "partitioned (view)" if partitioned else "dimension (table)"
            lines += [
                f"Rows: {fmt_int(row_count)} - Parquet: {fmt_bytes_mb(size)} "
                f"({fmt_int(files)} file(s)) - {shape}",
                "",
            ]
        lines += [
            "| column | type | nullable | flags | unit | null % | distinct | description |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for row in by_table.get((schema, table), []):
            (_s, _t, column, duckdb_type, nullable, col_desc, is_id, is_text, hint, np, ad) = row
            lines.append(
                f"| {column} | {duckdb_type} | {'yes' if nullable else 'no'} "
                f"| {_flags(is_id, is_text)} | {_cell(hint)} | {_fmt_null_pct(np)} "
                f"| {_fmt_distinct(ad, k)} | {_cell(col_desc)} |"
            )
            result.columns += 1
        lines.append("")
        result.tables += 1
    return "\n".join(lines), result


def generate_dictionary(
    tier: Tier | str,
    settings: Settings | None = None,
    *,
    out: Path | None = None,
) -> DictionaryResult:
    """Render the tier catalog's dictionary and write it (default
    ``mimicwarehouse/DATA-DICTIONARY.md``; UTF-8, LF). Raises
    :class:`~mimicwarehouse.catalog.connect.CatalogOpenError` when the catalog is
    missing — build it first (``mwh build --tier <t> --select meta.profile,catalog``)."""
    from mimicwarehouse.catalog.connect import open_catalog

    settings = settings or get_settings()
    con = open_catalog(tier, settings=settings)
    try:
        text, result = render_dictionary(con)
    finally:
        con.close()
    path = Path(out) if out is not None else default_out_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # exactly one trailing newline — the pre-commit end-of-file-fixer must be a no-op
    # on the committed file, or regeneration would not be a clean diff
    path.write_text(text.rstrip("\n") + "\n", encoding="utf-8", newline="\n")
    result.path = path
    return result


__all__ = [
    "CAVEATS",
    "DEFAULT_FILENAME",
    "SMALL_CELL_K",
    "DictionaryResult",
    "default_out_path",
    "generate_dictionary",
    "render_dictionary",
]
