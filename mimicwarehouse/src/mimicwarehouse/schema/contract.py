"""Schema contract — pydantic models and the YAML loader (EP-9, DESIGN §7).

The contract is the one authoritative description of the 41 MIMIC-IV tables (22
``mimiciv_hosp``, 9 ``mimiciv_icu``, 6 ``mimiciv_ed``, 4 ``mimiciv_note``): column names,
DuckDB types, nullability, keys, per-table load metadata, unit expectations and the demo
2.2 → 3.1 column map. It is transcribed from the vendored mimic-code DDL (EP-8, pinned sha in
``VENDOR.json``) and kept honest by the drift oracle in :mod:`mimicwarehouse.schema.transcribe`
(``mwh schema check``).

Data files (package data under ``schema/tables/``): ``mimiciv_hosp.yaml``, ``mimiciv_icu.yaml``,
``mimiciv_ed.yaml``, ``mimiciv_note.yaml`` (one file per schema: tables + columns + comments),
``keys.yaml`` (primary keys, uniqueness hints, foreign keys), ``units.yaml`` (value/unit column
pairs, fixed units, implied units) and ``column_maps/demo_2_2_to_3_1.yaml``.

Public API (used by EP-10/11/12/17/18/21/22/29 …)::

    contract = load_contract()                      # cached
    t = contract.table("mimiciv_hosp", "labevents")  # or contract.table("mimiciv_hosp.labevents")
    t.duckdb_ddl()                                   # CREATE TABLE mimiciv_hosp.labevents (...)
    t.read_csv_columns()                             # {name: duckdb_type} for read_csv(columns=…)
    contract.subject_keyed(); contract.dims(); contract.by_dataset("mimic-iv-3.1")
    contract.column_map("demo_2_2").apply(t, header) # {csv_col: contract_col | None}

Nothing in this module reads data: it only parses the YAML shipped with the package (or, for
tests, a directory of the same shape handed to :func:`load_contract_from`).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from functools import cache
from importlib.resources import files
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: The four schemas of the contract, in load order (hosp before icu: FKs point at hosp).
SCHEMAS: tuple[str, ...] = ("mimiciv_hosp", "mimiciv_icu", "mimiciv_ed", "mimiciv_note")
#: Dataset labels (one per PhysioNet project); ``Contract.by_dataset`` filters on them.
DATASETS: tuple[str, ...] = ("mimic-iv-3.1", "mimic-iv-ed-2.2", "mimic-iv-note-2.2")
#: Expected table counts per schema (the brief's 22 / 9 / 6 / 4).
EXPECTED_TABLE_COUNTS: dict[str, int] = {
    "mimiciv_hosp": 22,
    "mimiciv_icu": 9,
    "mimiciv_ed": 6,
    "mimiciv_note": 4,
}
#: DuckDB types the contract may use (the closed target set of the Postgres → DuckDB type map).
DUCKDB_TYPE_RE = re.compile(
    r"^(INTEGER|SMALLINT|BIGINT|VARCHAR|TIMESTAMP|DATE|DOUBLE|FLOAT|BOOLEAN|DECIMAL\(\d+,\d+\))$"
)
#: Types acceptable for ``Table.time_column`` / sort keys that carry time.
TIME_TYPES: frozenset[str] = frozenset({"TIMESTAMP", "DATE"})
#: The partitioning key (DESIGN §4/§5): a table carrying this column is subject-keyed.
SUBJECT_KEY = "subject_id"

TABLES_DIRNAME = "tables"
COLUMN_MAPS_DIRNAME = "column_maps"
KEYS_FILENAME = "keys.yaml"
UNITS_FILENAME = "units.yaml"

LoadClass = Literal["small", "large"]

_IDENT = re.compile(r"^[a-z][a-z0-9_]*$")


class SchemaError(ValueError):
    """The contract YAML is malformed or internally inconsistent."""


def _split_qualified(name: str) -> tuple[str, str]:
    schema, sep, table = name.partition(".")
    if not sep or not schema or not table:
        raise SchemaError(f"expected '<schema>.<table>', got {name!r}")
    return schema, table


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class _Frozen(BaseModel):
    model_config = ConfigDict(
        frozen=True, extra="forbid", validate_by_name=True, validate_by_alias=True
    )


class Column(_Frozen):
    """One column: name, DuckDB type, nullability, documentation.

    ``upstream_type`` / ``upstream_nullable`` are set **only** when the contract deliberately
    deviates from the vendored Postgres DDL (the raw upstream type text / NOT NULL flag are
    recorded so ``mwh schema check`` can still detect upstream drift; the ``comment`` says why).
    """

    name: str
    duckdb_type: str = Field(alias="type")
    nullable: bool = True
    comment: str | None = None
    unit_of: str | None = Field(
        default=None,
        description="for a unit column: the value column it qualifies (stamped from units.yaml)",
    )
    upstream_type: str | None = Field(
        default=None,
        description="raw Postgres type from create.sql when duckdb_type deliberately differs",
    )
    upstream_nullable: bool | None = Field(
        default=None,
        description="upstream nullability from create.sql when `nullable` deliberately differs",
    )

    @model_validator(mode="after")
    def _check(self) -> Column:
        if not _IDENT.match(self.name):
            raise ValueError(f"column name {self.name!r} is not a lower_snake identifier")
        if not DUCKDB_TYPE_RE.match(self.duckdb_type):
            raise ValueError(
                f"column {self.name}: {self.duckdb_type!r} is not an allowed DuckDB type "
                f"(pattern {DUCKDB_TYPE_RE.pattern})"
            )
        if self.upstream_nullable is not None and self.upstream_nullable == self.nullable:
            raise ValueError(
                f"column {self.name}: upstream_nullable equals nullable — drop the override"
            )
        return self

    @property
    def ddl(self) -> str:
        """``name TYPE [NOT NULL]`` as it appears in :meth:`Table.duckdb_ddl`."""
        return f"{self.name} {self.duckdb_type}" + ("" if self.nullable else " NOT NULL")


class Table(_Frozen):
    """One table of the contract with its load metadata (DESIGN §4/§5/§7)."""

    schema_name: str = Field(alias="schema")
    name: str
    dataset: str
    csv_path: str = Field(description="raw layout relative path, e.g. hosp/labevents.csv")
    columns: tuple[Column, ...] = Field(min_length=1)
    primary_key: tuple[str, ...] | None = Field(
        default=None, description="upstream-declared PK (constraint.sql), else None"
    )
    uniqueness_hint: tuple[str, ...] | None = Field(
        default=None,
        description="candidate unique columns for tables without a declared PK — to be tested "
        "(EP-28/EP-44), never asserted",
    )
    subject_keyed: bool = Field(description="True iff the table has a subject_id column")
    time_column: str | None = Field(
        default=None, description="primary event timestamp; None for dims / detail tables"
    )
    sort_keys: tuple[str, ...] = Field(
        default=(), description="lake sort order: [subject_id, time] or [subject_id, parent, seq]"
    )
    partitioned: bool = Field(description="Hive-partitioned by subject_bucket (== subject_keyed)")
    load_class: LoadClass = "small"
    expected_rows_source: str | None = Field(
        default=None,
        description="upstream-relative path of the validate.sql that pins this table's row count",
    )
    comment: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _derive_defaults(cls, data: Any) -> Any:
        """``subject_keyed`` / ``partitioned`` follow from the columns when not given."""
        if isinstance(data, dict):
            cols = data.get("columns") or ()
            names = {c["name"] if isinstance(c, Mapping) else c.name for c in cols}
            data = dict(data)
            data.setdefault("subject_keyed", SUBJECT_KEY in names)
            data.setdefault("partitioned", data["subject_keyed"])
        return data

    @model_validator(mode="after")
    def _check(self) -> Table:
        if self.schema_name not in SCHEMAS:
            raise ValueError(f"unknown schema {self.schema_name!r}; expected one of {SCHEMAS}")
        if not _IDENT.match(self.name):
            raise ValueError(f"table name {self.name!r} is not a lower_snake identifier")
        if self.dataset not in DATASETS:
            raise ValueError(f"{self.qualified_name}: unknown dataset {self.dataset!r}")
        names = [c.name for c in self.columns]
        dupes = sorted({n for n in names if names.count(n) > 1})
        if dupes:
            raise ValueError(f"{self.qualified_name}: duplicate columns {dupes}")
        known = set(names)
        for label, cols in (
            ("primary_key", self.primary_key),
            ("uniqueness_hint", self.uniqueness_hint),
            ("sort_keys", self.sort_keys),
        ):
            for c in cols or ():
                if c not in known:
                    raise ValueError(f"{self.qualified_name}: {label} names unknown column {c!r}")
        if self.primary_key is not None and self.uniqueness_hint is not None:
            raise ValueError(
                f"{self.qualified_name}: uniqueness_hint is only for tables without a primary_key"
            )
        if self.primary_key is not None and len(self.primary_key) == 0:
            raise ValueError(f"{self.qualified_name}: primary_key must be null or non-empty")
        has_subject = SUBJECT_KEY in known
        if self.subject_keyed != has_subject:
            raise ValueError(
                f"{self.qualified_name}: subject_keyed={self.subject_keyed} but "
                f"'{SUBJECT_KEY}' in columns is {has_subject}"
            )
        if self.partitioned != self.subject_keyed:
            raise ValueError(
                f"{self.qualified_name}: partitioned must equal subject_keyed (DESIGN §4/§5)"
            )
        if self.subject_keyed and (not self.sort_keys or self.sort_keys[0] != SUBJECT_KEY):
            raise ValueError(f"{self.qualified_name}: sort_keys must start with '{SUBJECT_KEY}'")
        if self.time_column is not None:
            if self.time_column not in known:
                raise ValueError(
                    f"{self.qualified_name}: time_column {self.time_column!r} is not a column"
                )
            if self.column(self.time_column).duckdb_type not in TIME_TYPES:
                raise ValueError(
                    f"{self.qualified_name}: time_column {self.time_column!r} is not a "
                    f"TIMESTAMP/DATE column"
                )
        if not self.csv_path.endswith(".csv") or "\\" in self.csv_path:
            raise ValueError(f"{self.qualified_name}: csv_path must be a posix '….csv' path")
        return self

    # -- lookups ---------------------------------------------------------------------------

    @property
    def qualified_name(self) -> str:
        return f"{self.schema_name}.{self.name}"

    @property
    def column_names(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.columns)

    @property
    def is_dim(self) -> bool:
        """Dimension / lookup table: not subject-keyed, identical in every tier (DESIGN §4)."""
        return not self.subject_keyed

    def column(self, name: str) -> Column:
        for c in self.columns:
            if c.name == name:
                return c
        raise KeyError(f"{self.qualified_name} has no column {name!r}")

    def has_column(self, name: str) -> bool:
        return any(c.name == name for c in self.columns)

    # -- renderers -------------------------------------------------------------------------

    def duckdb_ddl(self, *, if_not_exists: bool = False) -> str:
        """``CREATE TABLE schema.table (col TYPE [NOT NULL], …);`` — no key constraints (keys are
        integrity-test metadata, DESIGN §7; a DuckDB PK would build an ART index over 400 M
        rows and reject upstream's known duplicates)."""
        head = "CREATE TABLE IF NOT EXISTS" if if_not_exists else "CREATE TABLE"
        body = ",\n".join(f"  {c.ddl}" for c in self.columns)
        return f"{head} {self.qualified_name} (\n{body}\n);"

    def read_csv_columns(self) -> dict[str, str]:
        """``{name: duckdb_type}`` in column order, for ``read_csv(..., columns=…)`` (EP-17)."""
        return {c.name: c.duckdb_type for c in self.columns}


class ForeignKey(_Frozen):
    """``table(columns) → ref_table(ref_columns)``; ``source`` says where it comes from."""

    table: str
    columns: tuple[str, ...] = Field(min_length=1)
    ref_table: str
    ref_columns: tuple[str, ...] = Field(min_length=1)
    name: str | None = None
    source: str = Field(
        default="constraint.sql",
        description="'constraint.sql' (vendored DDL) or 'docs' (MIMIC documentation; ED / Note "
        "have no upstream constraint file)",
    )

    @model_validator(mode="after")
    def _check(self) -> ForeignKey:
        _split_qualified(self.table)
        _split_qualified(self.ref_table)
        if len(self.columns) != len(self.ref_columns):
            raise ValueError(f"FK {self.name or self.table}: column count mismatch")
        return self


class TableMap(_Frozen):
    """Per-table differences between a source layout and the contract."""

    added_in_3_1: tuple[str, ...] = Field(
        default=(), description="contract columns absent from the source header → loaded as NULL"
    )
    renamed: dict[str, str] = Field(default_factory=dict, description="{source_col: contract_col}")
    dropped_in_3_1: tuple[str, ...] = Field(
        default=(), description="source columns with no contract counterpart → ignored on load"
    )

    @property
    def is_identity(self) -> bool:
        return not (self.added_in_3_1 or self.renamed or self.dropped_in_3_1)


class ColumnMap(_Frozen):
    """Header → contract mapping for one alternative source layout (e.g. the demo 2.2)."""

    name: str
    description: str
    source_versions: dict[str, str] = Field(default_factory=dict)
    target_versions: dict[str, str] = Field(default_factory=dict)
    derivation: str = Field(description="how the map was derived (evidence, commits, dates)")
    schemas: tuple[str, ...] = Field(min_length=1, description="schemas the source layout ships")
    schemas_absent_in_source: tuple[str, ...] = Field(
        default=(), description="contract schemas the source layout does not ship at all"
    )
    identity_tables: tuple[str, ...] = Field(default=(), description="verified header == contract")
    tables: dict[str, TableMap] = Field(default_factory=dict, description="tables with differences")
    tables_absent_in_2_2: tuple[str, ...] = Field(
        default=(), description="contract tables the source layout does not ship"
    )

    def covers(self, table: str) -> bool:
        return (
            table in self.identity_tables
            or table in self.tables
            or table in self.tables_absent_in_2_2
        )

    def table_map(self, table: Table | str) -> TableMap:
        qn = table if isinstance(table, str) else table.qualified_name
        if qn in self.tables:
            return self.tables[qn]
        if qn in self.identity_tables:
            return TableMap()
        if qn in self.tables_absent_in_2_2:
            raise SchemaError(f"{qn} is absent from the {self.name} source layout")
        raise SchemaError(f"{qn} is not covered by column map {self.name!r}")

    def apply(self, table: Table, header: Sequence[str]) -> dict[str, str | None]:
        """``{csv_col: contract_col | None}`` for one CSV header (None = ignored on load)."""
        tm = self.table_map(table)
        out: dict[str, str | None] = {}
        for h in header:
            if h in tm.renamed:
                out[h] = tm.renamed[h]
            elif h in tm.dropped_in_3_1:
                out[h] = None
            elif table.has_column(h):
                out[h] = h
            else:
                out[h] = None
        return out

    def missing(self, table: Table, header: Sequence[str]) -> tuple[str, ...]:
        """Contract columns the header does not supply (the loader fills them with NULL)."""
        mapped = {v for v in self.apply(table, header).values() if v is not None}
        return tuple(c for c in table.column_names if c not in mapped)

    def check(self, table: Table, header: Sequence[str]) -> list[str]:
        """Problems with ``header`` under this map (empty list = header is as expected)."""
        tm = self.table_map(table)
        problems: list[str] = []
        seen: dict[str, str] = {}
        for h, target in self.apply(table, header).items():
            if target is None and h not in tm.dropped_in_3_1:
                problems.append(f"unexpected column {h!r}")
            if target is not None:
                if target in seen:
                    problems.append(f"columns {seen[target]!r} and {h!r} both map to {target!r}")
                seen[target] = h
        for c in self.missing(table, header):
            if c not in tm.added_in_3_1:
                problems.append(f"missing column {c!r} (not declared added_in_3_1)")
        return problems


class ValueUnitPair(_Frozen):
    """A value column and the column that carries its unit of measure."""

    table: str
    value: str
    unit: str
    note: str | None = None


class FixedUnit(_Frozen):
    """A value column whose unit is fixed by the data dictionary (no unit column)."""

    table: str
    column: str
    unit: str
    plausible_min: float | None = None
    plausible_max: float | None = None
    note: str | None = None


class ImpliedUnit(_Frozen):
    """A value column whose unit is implied by another (categorical) column."""

    table: str
    value: str
    implied_by: str
    note: str | None = None


class UnitsSpec(_Frozen):
    """The units seed (``units.yaml``); itemid-level expectations arrive with EP-39."""

    version_note: str
    value_unit_pairs: tuple[ValueUnitPair, ...] = ()
    fixed_units: tuple[FixedUnit, ...] = ()
    implied_units: tuple[ImpliedUnit, ...] = ()

    def columns_named(self) -> list[tuple[str, str]]:
        """Every ``(table, column)`` the seed refers to (for existence checks)."""
        out: list[tuple[str, str]] = []
        for p in self.value_unit_pairs:
            out += [(p.table, p.value), (p.table, p.unit)]
        out += [(f.table, f.column) for f in self.fixed_units]
        for i in self.implied_units:
            out += [(i.table, i.value), (i.table, i.implied_by)]
        return out


class SchemaInfo(_Frozen):
    """File-level facts of one ``<schema>.yaml``."""

    name: str
    dataset: str
    csv_dir: str
    source_ddl: str = Field(description="upstream-relative path of the create.sql transcribed")
    source_keys: str | None = Field(
        default=None, description="upstream-relative constraint.sql (hosp/icu only)"
    )
    comment: str | None = None


class Contract(_Frozen):
    """The whole contract: tables + keys + units + column maps."""

    tables: tuple[Table, ...] = Field(min_length=1)
    foreign_keys: tuple[ForeignKey, ...] = ()
    version_note: str
    schemas: dict[str, SchemaInfo] = Field(default_factory=dict)
    units: UnitsSpec
    column_maps: dict[str, ColumnMap] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check(self) -> Contract:
        names = [t.qualified_name for t in self.tables]
        dupes = sorted({n for n in names if names.count(n) > 1})
        if dupes:
            raise ValueError(f"duplicate tables {dupes}")
        by_name = {t.qualified_name: t for t in self.tables}

        def col_exists(qn: str, col: str, where: str) -> None:
            t = by_name.get(qn)
            if t is None:
                raise ValueError(f"{where}: unknown table {qn!r}")
            if not t.has_column(col):
                raise ValueError(f"{where}: {qn} has no column {col!r}")

        for fk in self.foreign_keys:
            where = f"foreign key {fk.name or fk.table}"
            for c in fk.columns:
                col_exists(fk.table, c, where)
            for c in fk.ref_columns:
                col_exists(fk.ref_table, c, where)
        for qn, col in self.units.columns_named():
            col_exists(qn, col, "units.yaml")
        for cm in self.column_maps.values():
            for s in (*cm.schemas, *cm.schemas_absent_in_source):
                if s not in SCHEMAS:
                    raise ValueError(f"column map {cm.name}: unknown schema {s!r}")
            expected = {qn for qn, t in by_name.items() if t.schema_name in cm.schemas}
            covered = set(cm.identity_tables) | set(cm.tables) | set(cm.tables_absent_in_2_2)
            if covered != expected:
                missing = sorted(expected - covered)
                extra = sorted(covered - expected)
                raise ValueError(
                    f"column map {cm.name}: coverage mismatch (missing {missing}, extra {extra})"
                )
            for qn, tm in cm.tables.items():
                t = by_name[qn]
                for c in (*tm.added_in_3_1, *tm.renamed.values()):
                    col_exists(qn, c, f"column map {cm.name}")
                for c in (*tm.renamed, *tm.dropped_in_3_1):
                    if t.has_column(c):
                        raise ValueError(
                            f"column map {cm.name}: {qn}.{c} is a contract column, so it cannot "
                            "be a renamed/dropped source column"
                        )
        return self

    # -- lookups ---------------------------------------------------------------------------

    def table(self, schema: str, name: str | None = None) -> Table:
        """``table("mimiciv_hosp", "labevents")`` or ``table("mimiciv_hosp.labevents")``."""
        if name is None:
            schema, name = _split_qualified(schema)
        for t in self.tables:
            if t.schema_name == schema and t.name == name:
                return t
        raise KeyError(f"no table {schema}.{name} in the contract")

    def has_table(self, qualified: str) -> bool:
        schema, name = _split_qualified(qualified)
        return any(t.schema_name == schema and t.name == name for t in self.tables)

    def by_schema(self, schema: str) -> tuple[Table, ...]:
        return tuple(t for t in self.tables if t.schema_name == schema)

    def by_dataset(self, dataset: str) -> tuple[Table, ...]:
        return tuple(t for t in self.tables if t.dataset == dataset)

    def subject_keyed(self) -> tuple[Table, ...]:
        return tuple(t for t in self.tables if t.subject_keyed)

    def dims(self) -> tuple[Table, ...]:
        return tuple(t for t in self.tables if t.is_dim)

    def large(self) -> tuple[Table, ...]:
        return tuple(t for t in self.tables if t.load_class == "large")

    def foreign_keys_of(self, table: Table | str) -> tuple[ForeignKey, ...]:
        qn = table if isinstance(table, str) else table.qualified_name
        return tuple(fk for fk in self.foreign_keys if fk.table == qn)

    def column_map(self, name: str) -> ColumnMap:
        try:
            return self.column_maps[name]
        except KeyError:
            raise KeyError(
                f"no column map {name!r}; available: {sorted(self.column_maps)}"
            ) from None

    def schema_names(self) -> tuple[str, ...]:
        return tuple(s for s in SCHEMAS if any(t.schema_name == s for t in self.tables))

    # -- renderers -------------------------------------------------------------------------

    def duckdb_schema_ddl(self) -> str:
        return "\n".join(f"CREATE SCHEMA IF NOT EXISTS {s};" for s in self.schema_names())

    def content_hash(self) -> str:
        """sha256 of the canonical JSON dump — what run manifests cite (GOVERNANCE §12)."""
        payload = self.model_dump(mode="json", by_alias=True)
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(blob).hexdigest()


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def tables_root() -> Path:
    """``schema/tables/`` inside the installed package (source tree or wheel)."""
    return Path(str(files(__package__).joinpath(TABLES_DIRNAME)))


def _read_yaml(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    try:
        docs = list(yaml.safe_load_all(text))
    except yaml.YAMLError as exc:
        raise SchemaError(f"{path.name}: invalid YAML — {exc}") from exc
    if len(docs) != 1:
        raise SchemaError(f"{path.name}: expected exactly one YAML document, got {len(docs)}")
    if not isinstance(docs[0], dict):
        raise SchemaError(f"{path.name}: top level must be a mapping")
    return docs[0]


def _wrap(path: Path, exc: ValidationError) -> SchemaError:
    lines = [f"{path.name}: {exc.error_count()} validation error(s)"]
    for e in exc.errors():
        loc = ".".join(str(p) for p in e["loc"])
        lines.append(f"  {loc}: {e['msg']}")
    return SchemaError("\n".join(lines))


def load_contract_from(root: Path) -> Contract:
    """Build a :class:`Contract` from a directory shaped like ``schema/tables/``.

    Tests point this at a copy with one edited type to prove the drift check bites; production
    code uses :func:`load_contract`.
    """
    root = Path(root)
    keys_path = root / KEYS_FILENAME
    keys_doc = _read_yaml(keys_path) if keys_path.is_file() else {}
    pks: dict[str, Any] = keys_doc.get("primary_keys") or {}
    hints: dict[str, Any] = keys_doc.get("uniqueness_hints") or {}
    units_doc = _read_yaml(root / UNITS_FILENAME)

    schemas: dict[str, SchemaInfo] = {}
    tables: list[Table] = []
    for schema in SCHEMAS:
        path = root / f"{schema}.yaml"
        if not path.is_file():
            continue
        doc = _read_yaml(path)
        try:
            info = SchemaInfo(
                name=doc["schema"],
                dataset=doc["dataset"],
                csv_dir=doc["csv_dir"],
                source_ddl=doc["source_ddl"],
                source_keys=doc.get("source_keys"),
                comment=doc.get("comment"),
            )
        except (KeyError, ValidationError) as exc:
            raise SchemaError(f"{path.name}: bad file header ({exc})") from exc
        if info.name != schema:
            raise SchemaError(f"{path.name}: header says schema {info.name!r}")
        schemas[schema] = info
        for entry in doc.get("tables") or ():
            if not isinstance(entry, dict):
                raise SchemaError(f"{path.name}: every table entry must be a mapping")
            qn = f"{schema}.{entry.get('name')}"
            payload = {
                "schema": schema,
                "dataset": info.dataset,
                "csv_path": f"{info.csv_dir}/{entry.get('name')}.csv",
                "expected_rows_source": doc.get("expected_rows_source"),
                **entry,
                "primary_key": pks.get(qn),
                "uniqueness_hint": hints.get(qn),
            }
            if qn not in pks:
                raise SchemaError(f"{KEYS_FILENAME}: primary_keys has no entry for {qn}")
            try:
                tables.append(Table.model_validate(payload))
            except ValidationError as exc:
                raise _wrap(path, exc) from None

    for qn in (*pks, *hints):
        if not any(t.qualified_name == qn for t in tables):
            raise SchemaError(f"{KEYS_FILENAME}: unknown table {qn}")

    try:
        fks = tuple(ForeignKey.model_validate(fk) for fk in keys_doc.get("foreign_keys") or ())
    except ValidationError as exc:
        raise _wrap(keys_path, exc) from None
    try:
        units = UnitsSpec.model_validate(units_doc)
    except ValidationError as exc:
        raise _wrap(root / UNITS_FILENAME, exc) from None

    # Stamp unit_of on unit columns from the units seed (single source of truth).
    unit_of: dict[tuple[str, str], str] = {
        (p.table, p.unit): p.value for p in units.value_unit_pairs
    }
    if unit_of:
        stamped: list[Table] = []
        for t in tables:
            cols = tuple(
                c.model_copy(update={"unit_of": unit_of[(t.qualified_name, c.name)]})
                if (t.qualified_name, c.name) in unit_of and c.unit_of is None
                else c
                for c in t.columns
            )
            stamped.append(t.model_copy(update={"columns": cols}) if cols != t.columns else t)
        tables = stamped

    column_maps: dict[str, ColumnMap] = {}
    maps_dir = root / COLUMN_MAPS_DIRNAME
    if maps_dir.is_dir():
        for path in sorted(maps_dir.glob("*.yaml")):
            try:
                cm = ColumnMap.model_validate(_read_yaml(path))
            except ValidationError as exc:
                raise _wrap(path, exc) from None
            if cm.name in column_maps:
                raise SchemaError(f"{path.name}: duplicate column map name {cm.name!r}")
            column_maps[cm.name] = cm

    try:
        return Contract(
            tables=tuple(tables),
            foreign_keys=fks,
            version_note=str(keys_doc.get("version_note") or units.version_note),
            schemas=schemas,
            units=units,
            column_maps=column_maps,
        )
    except ValidationError as exc:
        raise _wrap(root, exc) from None


@cache
def load_contract() -> Contract:
    """The packaged contract (cached; ``load_contract.cache_clear()`` in tests)."""
    return load_contract_from(tables_root())


def iter_tables(contract: Contract, schema: str | None = None) -> Iterable[Table]:
    return contract.by_schema(schema) if schema else contract.tables


__all__ = [
    "COLUMN_MAPS_DIRNAME",
    "DATASETS",
    "DUCKDB_TYPE_RE",
    "EXPECTED_TABLE_COUNTS",
    "KEYS_FILENAME",
    "SCHEMAS",
    "SUBJECT_KEY",
    "TABLES_DIRNAME",
    "TIME_TYPES",
    "UNITS_FILENAME",
    "Column",
    "ColumnMap",
    "Contract",
    "FixedUnit",
    "ForeignKey",
    "ImpliedUnit",
    "SchemaError",
    "SchemaInfo",
    "Table",
    "TableMap",
    "UnitsSpec",
    "ValueUnitPair",
    "iter_tables",
    "load_contract",
    "load_contract_from",
    "tables_root",
]
