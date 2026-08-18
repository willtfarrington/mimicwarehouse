"""DDL transcriber and drift oracle (EP-9).

Two jobs, both over the **vendored** mimic-code DDL (EP-8, never over data):

1. ``transcribe`` — parse ``CREATE TABLE schema.table (col TYPE [NOT NULL], …)`` blocks out of a
   Postgres ``create.sql`` and write a YAML *draft* of one schema file that the session then
   curates (keys, metadata, comments). ``mwh schema transcribe --create-sql … --schema … --out …``.
2. ``check`` — re-parse the vendored ``create.sql`` / ``constraint.sql`` files named in the
   contract's schema headers and report every (table, column, type-class, nullability, key)
   that differs from the YAML. ``mwh schema check`` exits non-zero on any finding, so the
   contract can never silently diverge from the pinned DDL.

Postgres → DuckDB type map (:func:`pg_to_duckdb`; the brief's table plus what the vendored
files actually use)::

    INTEGER / INT / INT4          -> INTEGER          SMALLINT / INT2 -> SMALLINT
    BIGINT / INT8                 -> BIGINT           BOOLEAN / BOOL  -> BOOLEAN
    VARCHAR(n) / TEXT / CHAR(n)   -> VARCHAR          DATE            -> DATE
    TIMESTAMP(n) / TIMESTAMP      -> TIMESTAMP (naive, as shipped)
    DOUBLE PRECISION / FLOAT / FLOAT8 -> DOUBLE       REAL / FLOAT4   -> FLOAT (4-byte)
    NUMERIC(p,s) / DECIMAL(p,s)   -> DECIMAL(p,s)     NUMERIC / DECIMAL (unbounded) -> DOUBLE

Postgres ``FLOAT`` with no precision is double precision, hence DOUBLE; DuckDB's ``FLOAT`` is
the 4-byte type, hence Postgres ``REAL`` maps to it. An unbounded ``NUMERIC`` (ED vitals) has no
DuckDB counterpart — DuckDB's bare ``DECIMAL`` is ``DECIMAL(18,3)`` and would constrain the
scale — so it becomes DOUBLE, the analysis type for measurements. Deliberate per-column
deviations from the mapped type or nullability are recorded in the YAML as ``upstream_type`` /
``upstream_nullable`` and the checker compares *those* against the DDL instead.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from mimicwarehouse.schema.contract import (
    DATASETS,
    SUBJECT_KEY,
    Contract,
    SchemaError,
    Table,
)

# ---------------------------------------------------------------------------
# Type map
# ---------------------------------------------------------------------------

_TYPE_RE = re.compile(r"^([A-Z][A-Z0-9 ]*?)\s*(?:\(\s*([^)]*?)\s*\))?$")

_INTEGER_ALIASES = {"INTEGER": "INTEGER", "INT": "INTEGER", "INT4": "INTEGER"}
_SMALLINT_ALIASES = {"SMALLINT": "SMALLINT", "INT2": "SMALLINT"}
_BIGINT_ALIASES = {"BIGINT": "BIGINT", "INT8": "BIGINT"}
_TEXT_ALIASES = {"VARCHAR", "CHARACTER VARYING", "CHAR", "CHARACTER", "TEXT"}
_TIMESTAMP_ALIASES = {"TIMESTAMP", "TIMESTAMP WITHOUT TIME ZONE"}
_DOUBLE_ALIASES = {"DOUBLE PRECISION", "FLOAT", "FLOAT8"}
_FLOAT_ALIASES = {"REAL", "FLOAT4"}
_DECIMAL_ALIASES = {"NUMERIC", "DECIMAL"}
_BOOLEAN_ALIASES = {"BOOLEAN", "BOOL"}


class UnknownTypeError(SchemaError):
    """A Postgres type the map does not cover — extend :func:`pg_to_duckdb` deliberately."""


def normalise_pg_type(pg_type: str) -> str:
    """Canonical spelling of a Postgres type text: upper case, single spaces, ``(p, s)`` args."""
    text = " ".join(pg_type.strip().upper().split())
    m = _TYPE_RE.match(text)
    if not m:
        raise UnknownTypeError(f"cannot parse Postgres type {pg_type!r}")
    base, args = m.group(1).strip(), m.group(2)
    if args is None:
        return base
    parts = [a.strip() for a in args.split(",")]
    return f"{base}({', '.join(parts)})"


def pg_to_duckdb(pg_type: str) -> str:
    """Map one Postgres type text (as written in create.sql) to the contract's DuckDB type."""
    text = normalise_pg_type(pg_type)
    m = _TYPE_RE.match(text)
    assert m is not None  # normalise_pg_type already matched
    base, args = m.group(1).strip(), m.group(2)
    if base in _INTEGER_ALIASES:
        return _INTEGER_ALIASES[base]
    if base in _SMALLINT_ALIASES:
        return _SMALLINT_ALIASES[base]
    if base in _BIGINT_ALIASES:
        return _BIGINT_ALIASES[base]
    if base in _TEXT_ALIASES:
        return "VARCHAR"
    if base in _TIMESTAMP_ALIASES:
        return "TIMESTAMP"
    if base == "DATE":
        return "DATE"
    if base in _DOUBLE_ALIASES:
        return "DOUBLE"
    if base in _FLOAT_ALIASES:
        return "FLOAT"
    if base in _BOOLEAN_ALIASES:
        return "BOOLEAN"
    if base in _DECIMAL_ALIASES:
        if not args:
            return "DOUBLE"
        parts = [p.strip() for p in args.split(",")]
        if not all(p.isdigit() for p in parts) or len(parts) not in (1, 2):
            raise UnknownTypeError(f"bad NUMERIC arguments in {pg_type!r}")
        precision = int(parts[0])
        scale = int(parts[1]) if len(parts) == 2 else 0
        return f"DECIMAL({precision},{scale})"
    raise UnknownTypeError(f"no DuckDB mapping for Postgres type {pg_type!r}")


# ---------------------------------------------------------------------------
# DDL parsing
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DdlColumn:
    name: str
    pg_type: str  # normalised Postgres type text
    not_null: bool

    @property
    def duckdb_type(self) -> str:
        return pg_to_duckdb(self.pg_type)


@dataclass(frozen=True, slots=True)
class DdlTable:
    schema: str
    name: str
    columns: tuple[DdlColumn, ...]

    @property
    def qualified_name(self) -> str:
        return f"{self.schema}.{self.name}"


@dataclass(frozen=True, slots=True)
class DdlKeys:
    """What ``constraint.sql`` declares: PK per table and the FK list."""

    primary_keys: dict[str, tuple[str, ...]] = field(default_factory=dict)
    foreign_keys: tuple[tuple[str, tuple[str, ...], str, tuple[str, ...], str], ...] = ()


_CREATE_RE = re.compile(
    r"^\s*CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z_][\w]*)\.([A-Za-z_][\w]*)\s*(\(?)(.*)$",
    re.IGNORECASE,
)
_CONSTRAINT_WORDS = ("PRIMARY", "CONSTRAINT", "FOREIGN", "UNIQUE", "CHECK")
_NOT_NULL_RE = re.compile(r"\s+NOT\s+NULL\s*$", re.IGNORECASE)


def _strip_comment(line: str) -> str:
    """Drop a ``-- …`` trailer (the vendored DDL has no quoted strings to worry about)."""
    idx = line.find("--")
    return line if idx < 0 else line[:idx]


def _split_top_level(text: str) -> list[str]:
    """Split on commas that are not inside parentheses (``NUMERIC(10, 4)`` stays whole)."""
    parts: list[str] = []
    depth = 0
    buf: list[str] = []
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    parts.append("".join(buf))
    return [p.strip() for p in parts if p.strip()]


def parse_create_sql(text: str) -> list[DdlTable]:
    """Parse every ``CREATE TABLE schema.table (…)`` block of a Postgres ``create.sql``.

    Line-oriented (one column per line, as the vendored files are written); block comments
    ``/* … */`` and ``--`` trailers are ignored; table-level constraint lines are skipped.
    """
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    tables: list[DdlTable] = []
    current: tuple[str, str] | None = None
    body_open = False
    columns: list[DdlColumn] = []

    def close() -> None:
        nonlocal current, body_open, columns
        assert current is not None
        tables.append(DdlTable(current[0], current[1], tuple(columns)))
        current, body_open, columns = None, False, []

    def consume(line: str) -> None:
        """One body line: columns separated by top-level commas; a `)` closes the table."""
        nonlocal body_open
        if not body_open:
            if not line.startswith("("):
                raise SchemaError(f"expected '(' after CREATE TABLE {'.'.join(current or ())}")
            body_open = True
            line = line[1:].strip()
            if not line:
                return
        if line.startswith(")"):
            close()
            return
        closes = False
        for piece in _split_top_level(line):
            if piece.count(")") > piece.count("("):
                # `last_col TYPE)` or `last_col TYPE) ;` — the body closes on this line.
                piece = piece[: piece.rfind(")")].strip()
                closes = True
                if not piece:
                    break
            col = _parse_column_line(piece)
            if col is not None:
                columns.append(col)
        if closes:
            close()

    for raw in text.splitlines():
        line = _strip_comment(raw).strip()
        if not line:
            continue
        if current is None:
            m = _CREATE_RE.match(line)
            if not m:
                continue
            current = (m.group(1).lower(), m.group(2).lower())
            columns = []
            body_open = False
            rest = (m.group(3) + m.group(4)).strip()
            if rest:
                consume(rest)
            continue
        consume(line)
    if current is not None:
        raise SchemaError(f"unterminated CREATE TABLE {'.'.join(current)}")
    return tables


def _parse_column_line(piece: str) -> DdlColumn | None:
    piece = piece.rstrip(",").strip()
    upper = piece.upper()
    if any(upper.startswith(w) for w in _CONSTRAINT_WORDS):
        return None
    name, _, rest = piece.partition(" ")
    if not rest:
        raise SchemaError(f"cannot parse column definition {piece!r}")
    not_null = bool(_NOT_NULL_RE.search(rest))
    type_text = _NOT_NULL_RE.sub("", rest).strip()
    if type_text.count("(") != type_text.count(")"):
        raise SchemaError(f"unbalanced parentheses in column definition {piece!r}")
    return DdlColumn(name.lower(), normalise_pg_type(type_text), not_null)


_PK_RE = re.compile(
    r"ALTER\s+TABLE\s+(\w+)\.(\w+)\s+ADD\s+CONSTRAINT\s+(\w+)\s+PRIMARY\s+KEY\s*\(([^)]*)\)",
    re.IGNORECASE | re.DOTALL,
)
_FK_RE = re.compile(
    r"ALTER\s+TABLE\s+(\w+)\.(\w+)\s+ADD\s+CONSTRAINT\s+(\w+)\s+FOREIGN\s+KEY\s*\(([^)]*)\)\s*"
    r"REFERENCES\s+(\w+)\.(\w+)\s*\(([^)]*)\)",
    re.IGNORECASE | re.DOTALL,
)


def _cols(text: str) -> tuple[str, ...]:
    return tuple(c.strip().lower() for c in text.split(",") if c.strip())


def parse_constraint_sql(text: str) -> DdlKeys:
    """Primary keys and foreign keys declared by ``ALTER TABLE … ADD CONSTRAINT`` statements."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    text = "\n".join(_strip_comment(line) for line in text.splitlines())
    pks: dict[str, tuple[str, ...]] = {}
    for m in _PK_RE.finditer(text):
        qn = f"{m.group(1).lower()}.{m.group(2).lower()}"
        if qn in pks:
            raise SchemaError(f"constraint.sql declares two primary keys for {qn}")
        pks[qn] = _cols(m.group(4))
    fks = tuple(
        (
            f"{m.group(1).lower()}.{m.group(2).lower()}",
            _cols(m.group(4)),
            f"{m.group(5).lower()}.{m.group(6).lower()}",
            _cols(m.group(7)),
            m.group(3).lower(),
        )
        for m in _FK_RE.finditer(text)
    )
    return DdlKeys(primary_keys=pks, foreign_keys=fks)


# ---------------------------------------------------------------------------
# YAML draft
# ---------------------------------------------------------------------------

#: Brief item 2: tables loaded with the two-pass bucketed loader (EP-18).
LARGE_TABLES: frozenset[str] = frozenset(
    {
        "chartevents",
        "labevents",
        "emar",
        "emar_detail",
        "pharmacy",
        "poe",
        "prescriptions",
        "inputevents",
        "ingredientevents",
        "datetimeevents",
        "microbiologyevents",
        "discharge",
        "radiology",
    }
)
#: Candidate primary event timestamps, in preference order (brief item 2).
TIME_CANDIDATES: tuple[str, ...] = (
    "admittime",
    "intime",
    "charttime",
    "starttime",
    "ordertime",
    "transfertime",
    "chartdate",
)
_CSV_DIR = {
    "mimiciv_hosp": "hosp",
    "mimiciv_icu": "icu",
    "mimiciv_ed": "ed",
    "mimiciv_note": "note",
}
_DATASET = {
    "mimiciv_hosp": "mimic-iv-3.1",
    "mimiciv_icu": "mimic-iv-3.1",
    "mimiciv_ed": "mimic-iv-ed-2.2",
    "mimiciv_note": "mimic-iv-note-2.2",
}


def guess_time_column(columns: Sequence[str]) -> str | None:
    for cand in TIME_CANDIDATES:
        if cand in columns:
            return cand
    return None


def draft_table(t: DdlTable) -> dict:
    """The curated-later YAML entry for one parsed table (metadata guessed by the brief's rules)."""
    names = [c.name for c in t.columns]
    subject_keyed = SUBJECT_KEY in names
    time_column = guess_time_column(names) if subject_keyed else None
    sort_keys: list[str] = []
    if subject_keyed:
        sort_keys = [SUBJECT_KEY, time_column] if time_column else [SUBJECT_KEY]
    entry: dict = {
        "name": t.name,
        "comment": "TODO",
        "time_column": time_column,
        "sort_keys": sort_keys,
        "load_class": "large" if t.name in LARGE_TABLES else "small",
        "columns": [
            {"name": c.name, "type": c.duckdb_type, "nullable": not c.not_null, "comment": "TODO"}
            for c in t.columns
        ],
    }
    return entry


def draft_schema_yaml(
    tables: Iterable[DdlTable],
    schema: str,
    *,
    dataset: str | None = None,
    csv_dir: str | None = None,
    source_ddl: str = "TODO",
) -> str:
    """A single-document, tag-free YAML draft of ``<schema>.yaml`` for the given schema."""
    picked = [t for t in tables if t.schema == schema]
    if not picked:
        raise SchemaError(f"no CREATE TABLE {schema}.* blocks found")
    dataset = dataset or _DATASET.get(schema, DATASETS[0])
    doc = {
        "schema": schema,
        "dataset": dataset,
        "csv_dir": csv_dir or _CSV_DIR.get(schema, schema),
        "source_ddl": source_ddl,
        "comment": "TODO - transcribed draft; curate keys/metadata/comments, then mwh schema check",
        "tables": [draft_table(t) for t in picked],
    }
    return yaml.safe_dump(doc, sort_keys=False, allow_unicode=False, width=100)


# ---------------------------------------------------------------------------
# Drift check
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Drift:
    """One difference between the contract YAML and the vendored DDL."""

    schema: str
    table: str
    column: str | None
    kind: str  # missing_table / extra_table / missing_column / extra_column / order / type /
    #            nullability / primary_key / foreign_key
    expected: str  # what the DDL says
    actual: str  # what the YAML says

    def __str__(self) -> str:
        where = f"{self.schema}.{self.table}" + (f".{self.column}" if self.column else "")
        return f"{self.kind:<14} {where}: DDL {self.expected!r} vs YAML {self.actual!r}"


def check_tables(contract: Contract, ddl_tables: Sequence[DdlTable], schema: str) -> list[Drift]:
    """Compare one schema's YAML tables with the parsed DDL: table set, column set + order,
    mapped type (or recorded ``upstream_type``), nullability (or ``upstream_nullable``)."""
    out: list[Drift] = []
    ddl_by_name = {t.name: t for t in ddl_tables if t.schema == schema}
    yaml_by_name = {t.name: t for t in contract.by_schema(schema)}
    for name in sorted(ddl_by_name.keys() - yaml_by_name.keys()):
        out.append(Drift(schema, name, None, "missing_table", "present", "absent"))
    for name in sorted(yaml_by_name.keys() - ddl_by_name.keys()):
        out.append(Drift(schema, name, None, "extra_table", "absent", "present"))
    for name in sorted(ddl_by_name.keys() & yaml_by_name.keys()):
        out.extend(_check_columns(ddl_by_name[name], yaml_by_name[name]))
    return out


def _check_columns(ddl: DdlTable, t: Table) -> list[Drift]:
    out: list[Drift] = []
    ddl_names = [c.name for c in ddl.columns]
    yaml_names = list(t.column_names)
    ddl_set, yaml_set = set(ddl_names), set(yaml_names)
    for c in ddl_names:
        if c not in yaml_set:
            out.append(Drift(t.schema_name, t.name, c, "missing_column", "present", "absent"))
    for c in yaml_names:
        if c not in ddl_set:
            out.append(Drift(t.schema_name, t.name, c, "extra_column", "absent", "present"))
    common_ddl = [c for c in ddl_names if c in yaml_set]
    common_yaml = [c for c in yaml_names if c in ddl_set]
    if common_ddl != common_yaml:
        out.append(
            Drift(t.schema_name, t.name, None, "order", ",".join(common_ddl), ",".join(common_yaml))
        )
    for dc in ddl.columns:
        if dc.name not in yaml_set:
            continue
        yc = t.column(dc.name)
        if yc.upstream_type is not None:
            expected, actual = dc.pg_type, normalise_pg_type(yc.upstream_type)
            if expected != actual:
                out.append(Drift(t.schema_name, t.name, dc.name, "type", expected, actual))
        else:
            expected, actual = dc.duckdb_type, yc.duckdb_type
            if expected != actual:
                out.append(Drift(t.schema_name, t.name, dc.name, "type", expected, actual))
        ddl_nullable = not dc.not_null
        yaml_nullable = yc.upstream_nullable if yc.upstream_nullable is not None else yc.nullable
        if ddl_nullable != yaml_nullable:
            out.append(
                Drift(
                    t.schema_name,
                    t.name,
                    dc.name,
                    "nullability",
                    "NULL" if ddl_nullable else "NOT NULL",
                    "NULL" if yaml_nullable else "NOT NULL",
                )
            )
    return out


def check_keys(contract: Contract, keys: DdlKeys, schemas: Sequence[str]) -> list[Drift]:
    """Compare declared PKs and FKs (``source == 'constraint.sql'``) for the given schemas."""
    out: list[Drift] = []
    for t in contract.tables:
        if t.schema_name not in schemas:
            continue
        expected = keys.primary_keys.get(t.qualified_name)
        if expected != t.primary_key:
            out.append(
                Drift(
                    t.schema_name,
                    t.name,
                    None,
                    "primary_key",
                    ",".join(expected) if expected else "none",
                    ",".join(t.primary_key) if t.primary_key else "none",
                )
            )
    ddl_fks = {(a, b, c, d) for a, b, c, d, _ in keys.foreign_keys if a.split(".")[0] in schemas}
    yaml_fks = {
        (fk.table, fk.columns, fk.ref_table, fk.ref_columns)
        for fk in contract.foreign_keys
        if fk.source == "constraint.sql" and fk.table.split(".")[0] in schemas
    }
    for a, b, c, d in sorted(ddl_fks - yaml_fks):
        s, tname = a.split(".")
        out.append(
            Drift(s, tname, None, "foreign_key", f"{','.join(b)} -> {c}({','.join(d)})", "absent")
        )
    for a, b, c, d in sorted(yaml_fks - ddl_fks):
        s, tname = a.split(".")
        out.append(
            Drift(s, tname, None, "foreign_key", "absent", f"{','.join(b)} -> {c}({','.join(d)})")
        )
    return out


def check_contract(contract: Contract, *, vendored: dict[str, Path] | None = None) -> list[Drift]:
    """Run the full drift check against the vendored DDL named in the contract's schema headers.

    ``vendored`` maps upstream-relative paths to files (tests inject; production resolves through
    :func:`mimicwarehouse.concepts.vendored_path`). Returns the list of findings (empty = clean).
    """
    from mimicwarehouse.concepts import vendored_path

    def resolve(rel: str) -> Path:
        if vendored is not None and rel in vendored:
            return vendored[rel]
        return vendored_path(rel)

    out: list[Drift] = []
    parsed: dict[str, list[DdlTable]] = {}
    keys_by_file: dict[str, DdlKeys] = {}
    for schema, info in contract.schemas.items():
        if info.source_ddl not in parsed:
            parsed[info.source_ddl] = parse_create_sql(resolve(info.source_ddl).read_text("utf-8"))
        out.extend(check_tables(contract, parsed[info.source_ddl], schema))
    keyed: dict[str, list[str]] = {}
    for schema, info in contract.schemas.items():
        if info.source_keys:
            keyed.setdefault(info.source_keys, []).append(schema)
    for rel, schemas in keyed.items():
        if rel not in keys_by_file:
            keys_by_file[rel] = parse_constraint_sql(resolve(rel).read_text("utf-8"))
        out.extend(check_keys(contract, keys_by_file[rel], schemas))
    return out


__all__ = [
    "LARGE_TABLES",
    "TIME_CANDIDATES",
    "DdlColumn",
    "DdlKeys",
    "DdlTable",
    "Drift",
    "UnknownTypeError",
    "check_contract",
    "check_keys",
    "check_tables",
    "draft_schema_yaml",
    "draft_table",
    "guess_time_column",
    "normalise_pg_type",
    "parse_constraint_sql",
    "parse_create_sql",
    "pg_to_duckdb",
]
