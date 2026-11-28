"""The mart registry: YAML mart specs, the generic mart writer, the per-tier status and
the catalog extension that registers built marts (EP-55 items 1 and 4; DESIGN §3/§15;
D-17, D-18, D-20, D-28).

A **mart** is a per-tier Parquet table under ``<lake_root>/marts/<tier>/<name>/``
described by one packaged spec ``marts/specs/<name>.yaml`` (:class:`MartSpec`,
pydantic): ``name``, ``version``, ``grain`` (an EP-34 :data:`timesem.GRAINS` key, or a
:data:`SUMMARY_GRAINS` key for a dictionary-shaped summary such as ``itemid``, EP-175), the
``key`` columns, ``partition`` (``subject_bucket`` for a subject-keyed mart, ``null`` for
a small summary), ``sort``, ``builder`` (the DAG step callable, ``module:function``),
``depends_on`` (the DAG steps it reads, mirrored by ``dag/specs/marts.yaml``), ``sources``
(the base table, the joined relations with their aliases and, since EP-175, ``read``
sources: relations the builder's CTEs read that the FROM clause never joins) and
``columns``: explicit :class:`MartColumn` entries (name, DuckDB type, unit, the EP-39
``concept_group`` that fixes the unit, description, source expression) and
:class:`ColumnFamily` entries that expand ``vars x stats`` into ``<var>_<stat>`` columns.
EP-56 adds the family statistics ``n`` (always an ``INTEGER`` count without a unit or
``concept_group``, whatever the family's type: :data:`STAT_TYPES`, :data:`UNITLESS_STATS`)
and ``last``, and the summary grains ``variable`` and ``hour_variable_era``
(:data:`SUMMARY_GRAINS`) of the hourly marts' dictionary and population table; none of these
changes an existing spec's dump, so no built mart turns stale.

:func:`load_registry` validates every spec; a unit that disagrees with the canonical unit
of its ``concept_group`` in the EP-39 catalogue (``units.py``, ``meta.item_units``) is
refused, so the marts' units cannot drift from the curated catalogue.

:func:`mart_sql` generates a mart's statement from its spec: the SELECT list (every
``expr`` cast to its declared type, in spec order) over the join skeleton (the ``base``
table, then one ``LEFT JOIN`` per joined source on ``join_on``; a ``cte: true`` source
reads the builder's ``cte_<alias>``, one row per key -- or, for a ``cte: true`` base, the
mart's own rows, as the itemid rollups' aggregates are). The spec is therefore the single
source of the column contract, and the built file carries exactly the declared columns
and types.

:func:`write_mart` materialises the statement once on the build connection, then writes
one sorted single-file ``COPY`` per ``subject_bucket`` (``docs/gotchas.md`` §1: a sorted
``PARTITION_BY`` leaves seams) or one file for an unpartitioned mart, all under
``<dir>.new``, published by :func:`publish.swap_dir` (an interrupted build never looks
complete; a rebuild is idempotent). It appends one manifest line per file (schema
``marts``, table ``<name>``), writes ``manifest.json`` (spec and SQL hashes, the snapshot
ids of the layers read, rows, files, run and build ids, and -- since EP-56 -- the build
``params`` a builder passes, such as the hourly mart's hour cap), merges the per-tier status entry
``marts.<name>`` (the EP-37 ``per_tier`` shape), appends one ``kind: mart`` benchmark line
and stamps the marts layer snapshot id (:func:`dag.snapshot.record_snapshot_once`).

:func:`register_marts` (a :data:`catalog.build.CATALOG_EXTENSIONS` entry) creates the view
``marts.<name>`` over every mart complete for the tier, with comments on the view and on
each column (description and unit), and the two registry tables ``meta.mart_tables`` (one
row per spec: built or not, ``stale`` when the files were built from another spec than the
packaged one — the view then shows the columns the files hold and a warning names the
``--force`` rebuild, so a spec edit never breaks a catalog build — rows blanked below k,
files, bytes, hashes, ids) and
``meta.mart_columns`` (one row per declared column) that ``DATA-DICTIONARY.md`` renders
(:mod:`catalog.dictionary`): ``meta.tables`` / ``meta.columns`` stay EP-29's core-table
registry.

Everything written, logged or returned is DDL, paths, hashes, counts and timings, never a
row (GOVERNANCE §4). Import budget: pydantic and yaml at module level only; duckdb and the
runner modules load inside the functions.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from functools import cache
from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

if TYPE_CHECKING:  # pragma: no cover
    import duckdb

    from mimicwarehouse.dag.runner import StepContext, StepOutcome

_LOG = logging.getLogger(__name__)

#: The lake layer / catalog schema of every mart (DESIGN §3).
MARTS_LAYER = "marts"
MARTS_SCHEMA = "marts"
#: Packaged specs: ``marts/specs/<name>.yaml``.
SPECS_DIRNAME = "specs"
#: One file per partition directory (or at the mart root when unpartitioned).
PART = "part-0.parquet"
MANIFEST_FILE = "manifest.json"
#: The lake's Hive partition (D-18; ``loader.buckets.NUM_BUCKETS`` mirrored, tested).
BUCKET_COLUMN = "subject_bucket"
NUM_BUCKETS = 100
#: Benchmark-ledger kind of a mart build (``dag.benchmarks.BENCHMARK_KINDS``; EP-47 shape).
BENCH_KIND = "mart"
#: The registry tables :func:`register_marts` writes (``meta.<name>``).
TABLES_TABLE = "mart_tables"
COLUMNS_TABLE = "mart_columns"
#: The DuckDB types a mart column may declare.
DUCKDB_TYPES: tuple[str, ...] = (
    "BOOLEAN",
    "SMALLINT",
    "INTEGER",
    "BIGINT",
    "DOUBLE",
    "VARCHAR",
    "DATE",
    "TIMESTAMP",
)
#: The family statistics and the word each puts in a column description (EP-56: ``n``,
#: ``last``).
STAT_WORDS: dict[str, str] = {
    "min": "Lowest",
    "max": "Highest",
    "mean": "Mean",
    "n": "Number of measurements of",
    "last": "Latest (by charttime)",
}
#: Statistics whose column type is fixed whatever the family's ``type`` (a count).
STAT_TYPES: dict[str, DuckType] = {"n": "INTEGER"}
#: Statistics that carry no unit and no ``concept_group`` (a count of measurements).
UNITLESS_STATS: frozenset[str] = frozenset({"n"})
#: Join keys a source may declare (each is a column of every base table a mart uses).
JOIN_KEYS: tuple[str, ...] = ("stay_id", "hadm_id", "subject_id")
#: Grains of the dictionary-shaped summary marts (EP-175): one row per entry of a curated
#: list, no patient unit of analysis, so never an EP-34 grain and never partitioned.
SUMMARY_GRAINS: dict[str, str] = {
    "itemid": "one row per curated itemid (a summary over the tier; no patient unit)",
    "variable": "one row per hourly variable (a dictionary; no patient unit)",
    "hour_variable_era": (
        "one row per hour bin x hourly variable x era (a population summary; no patient unit)"
    ),
}

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_RELATION_RE = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")
_CALLABLE_RE = re.compile(r"^[a-z_][a-z0-9_.]*:[a-z_][a-z0-9_]*$")

DuckType = Literal[
    "BOOLEAN", "SMALLINT", "INTEGER", "BIGINT", "DOUBLE", "VARCHAR", "DATE", "TIMESTAMP"
]
Stat = Literal["min", "max", "mean", "n", "last"]


class MartSpecError(ValueError):
    """A mart spec is malformed, refers to something unknown, or disagrees with the EP-39
    catalogue."""


class MartBuildError(RuntimeError):
    """A mart cannot be built on the tier (a source is not built, a DuckDB failure)."""


# ---------------------------------------------------------------------------
# The spec models
# ---------------------------------------------------------------------------


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


def _check_name(kind: str, value: str) -> None:
    if not _NAME_RE.match(value):
        raise ValueError(f"{kind} {value!r} is not snake_case ([a-z][a-z0-9_]*)")


class MartSource(_Frozen):
    """One relation the mart reads: the ``base`` table (exactly one, first), a source
    left-joined on ``join_on``, or (EP-175) a ``read`` source -- a relation the builder's
    CTEs read that the FROM clause never joins. ``cte: true`` means the builder reduces the
    relation first (``cte_<alias>``; for a joined source one row per key, for the base the
    mart's own rows, e.g. an aggregate), ``note`` says how."""

    alias: str
    relation: str
    join: Literal["base", "left", "read"] = "left"
    join_on: Literal["stay_id", "hadm_id", "subject_id"] | None = None
    cte: bool = False
    note: str = ""

    @model_validator(mode="after")
    def _check(self) -> MartSource:
        _check_name("alias", self.alias)
        if not _RELATION_RE.match(self.relation):
            raise ValueError(f"source {self.alias}: relation {self.relation!r} is not schema.table")
        if self.join == "base" and self.join_on is not None:
            raise ValueError(f"source {self.alias}: the base table takes no join_on")
        if self.join == "read" and (self.join_on is not None or self.cte):
            raise ValueError(f"source {self.alias}: a read source takes no join_on and no cte")
        if self.join == "left" and self.join_on is None:
            raise ValueError(f"source {self.alias}: a joined source needs join_on")
        return self


class MartColumn(_Frozen):
    """One output column: name, DuckDB type, source expression, description; ``unit`` and
    the EP-39 ``concept_group`` that fixes it (checked by :func:`check_units`)."""

    name: str
    type: DuckType
    expr: str = Field(min_length=1)
    description: str = Field(min_length=1)
    unit: str | None = None
    concept_group: str | None = None

    @model_validator(mode="after")
    def _check(self) -> MartColumn:
        _check_name("column", self.name)
        return self


class FamilyVar(_Frozen):
    """One variable of a :class:`ColumnFamily` (``source`` = the source column stem when
    it differs from ``name``)."""

    name: str
    label: str = Field(min_length=1)
    source: str | None = None
    unit: str | None = None
    concept_group: str | None = None


class ColumnFamily(_Frozen):
    """``vars x stats`` columns ``<var>_<stat>`` reading ``<alias>.<source or var>_<stat>``;
    ``description`` is a template with ``{stat}`` and ``{label}``."""

    family: str
    alias: str
    stats: tuple[Stat, ...] = Field(min_length=1)
    type: DuckType = "DOUBLE"
    description: str = Field(min_length=1)
    vars: tuple[FamilyVar, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _check(self) -> ColumnFamily:
        _check_name("family", self.family)
        _check_name("alias", self.alias)
        for var in self.vars:
            _check_name("family variable", var.name)
        if "{stat}" not in self.description or "{label}" not in self.description:
            raise ValueError(f"family {self.family}: description needs {{stat}} and {{label}}")
        return self

    def expand(self) -> list[MartColumn]:
        """The concrete columns, variable by variable, statistics in declared order (a
        :data:`STAT_TYPES` statistic keeps its fixed type, a :data:`UNITLESS_STATS` one
        drops the variable's unit and ``concept_group``)."""
        return [
            MartColumn(
                name=f"{var.name}_{stat}",
                type=STAT_TYPES.get(stat, self.type),
                expr=f"{self.alias}.{var.source or var.name}_{stat}",
                description=self.description.format(stat=STAT_WORDS[stat], label=var.label),
                unit=None if stat in UNITLESS_STATS else var.unit,
                concept_group=None if stat in UNITLESS_STATS else var.concept_group,
            )
            for var in self.vars
            for stat in self.stats
        ]


class MartSpec(_Frozen):
    """One mart (module docstring)."""

    name: str
    version: int = Field(ge=1)
    grain: str
    description: str = Field(min_length=1)
    key: tuple[str, ...] = Field(min_length=1)
    partition: Literal["subject_bucket"] | None = None
    sort: tuple[str, ...] = ()
    builder: str
    depends_on: tuple[str, ...] = ()
    sources: tuple[MartSource, ...] = Field(min_length=1)
    columns: tuple[MartColumn | ColumnFamily, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _check(self) -> MartSpec:
        from mimicwarehouse.timesem import GRAINS

        _check_name("mart", self.name)
        if self.grain not in GRAINS and self.grain not in SUMMARY_GRAINS:
            raise ValueError(
                f"grain {self.grain!r} is not an EP-34 grain ({', '.join(GRAINS)}) or a "
                f"summary grain ({', '.join(SUMMARY_GRAINS)})"
            )
        if self.grain in SUMMARY_GRAINS and self.partition is not None:
            raise ValueError(f"summary grain {self.grain!r} takes no partition")
        if not _CALLABLE_RE.match(self.builder):
            raise ValueError(f"builder {self.builder!r} is not module:function")
        if self.sources[0].join != "base" or any(s.join == "base" for s in self.sources[1:]):
            raise ValueError("exactly one source is the base table, and it comes first")
        aliases = [s.alias for s in self.sources]
        dupes = sorted({a for a in aliases if aliases.count(a) > 1})
        if dupes:
            raise ValueError(f"duplicate source aliases {dupes}")
        joined = {s.alias for s in self.sources if s.join != "read"}
        for entry in self.columns:
            if isinstance(entry, ColumnFamily) and entry.alias not in aliases:
                raise ValueError(f"family {entry.family}: alias {entry.alias!r} is not a source")
            if isinstance(entry, ColumnFamily) and entry.alias not in joined:
                raise ValueError(
                    f"family {entry.family}: alias {entry.alias!r} is a read source (not joined)"
                )
        names = [c.name for c in self.expanded]
        dupes = sorted({n for n in names if names.count(n) > 1})
        if dupes:
            raise ValueError(f"duplicate column names {dupes}")
        known = set(names)
        for label, cols in (("key", self.key), ("sort", self.sort)):
            missing = [c for c in cols if c not in known]
            if missing:
                raise ValueError(f"{label} column(s) {missing} are not declared")
        if self.partition is not None:
            if self.partition not in known:
                raise ValueError(f"partition column {self.partition!r} is not declared")
            if self.column(self.partition).type != "INTEGER":
                raise ValueError(f"partition column {self.partition!r} must be INTEGER")
        deps = list(self.depends_on)
        if len(set(deps)) != len(deps):
            raise ValueError("depends_on lists a step twice")
        return self

    @property
    def expanded(self) -> tuple[MartColumn, ...]:
        """Every concrete column in output order (families expanded in place)."""
        out: list[MartColumn] = []
        for entry in self.columns:
            out.extend(entry.expand() if isinstance(entry, ColumnFamily) else [entry])
        return tuple(out)

    @property
    def column_names(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.expanded)

    def column(self, name: str) -> MartColumn:
        for c in self.expanded:
            if c.name == name:
                return c
        raise MartSpecError(f"mart {self.name}: no column {name!r}")

    @property
    def base(self) -> MartSource:
        return self.sources[0]

    @property
    def cte_aliases(self) -> tuple[str, ...]:
        return tuple(s.alias for s in self.sources if s.cte)

    @property
    def status_key(self) -> str:
        """The ``status.json`` key (and the DAG step's ``target``): ``marts.<name>``."""
        return f"{MARTS_SCHEMA}.{self.name}"

    @property
    def step_name(self) -> str:
        """The DAG step id (``dag/specs/marts.yaml``): ``marts.<name>``."""
        return f"{MARTS_LAYER}.{self.name}"

    @property
    def spec_sha256(self) -> str:
        """sha256 of the spec's canonical JSON (every field; a change to any is a change)."""
        blob = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    @property
    def schema_hash(self) -> str:
        """sha256 of the ordered ``(name, type)`` list (the manifest lines' schema hash)."""
        blob = json.dumps([[c.name, c.type] for c in self.expanded], separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def check_units(spec: MartSpec) -> list[str]:
    """Problems with the units: a ``concept_group`` the EP-39 catalogue does not know, or a
    ``unit`` that is not the group's canonical unit (empty when consistent)."""
    from mimicwarehouse.units import load_catalogue

    canonical: dict[str, str] = {}
    for item in load_catalogue().items:
        canonical.setdefault(item.concept_group, item.canonical_unit)
    problems: list[str] = []
    for c in spec.expanded:
        if c.concept_group is None:
            continue
        unit = canonical.get(c.concept_group)
        if unit is None:
            problems.append(f"{c.name}: concept_group {c.concept_group!r} is not in the catalogue")
        elif c.unit != unit:
            problems.append(
                f"{c.name}: unit {c.unit!r} differs from {c.concept_group}'s canonical {unit!r}"
            )
    return problems


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def specs_dir() -> Path:
    """``src/mimicwarehouse/marts/specs/`` inside the installed package."""
    return Path(str(files("mimicwarehouse.marts").joinpath(SPECS_DIRNAME)))


def load_spec_from(path: Path) -> MartSpec:
    """Parse and validate one spec file (units included); :class:`MartSpecError` names
    every problem."""
    path = Path(path)
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise MartSpecError(f"{path.name}: cannot read ({exc})") from exc
    if not isinstance(doc, dict):
        raise MartSpecError(f"{path.name}: top level must be a mapping")
    try:
        spec = MartSpec.model_validate(doc)
    except ValidationError as exc:
        lines = [f"{path.name}: {exc.error_count()} validation error(s)"]
        for e in exc.errors():
            loc = ".".join(str(p) for p in e["loc"])
            lines.append(f"  {loc}: {e['msg']}")
        raise MartSpecError("\n".join(lines)) from None
    if spec.name != path.stem:
        raise MartSpecError(f"{path.name}: name {spec.name!r} must match the file name")
    problems = check_units(spec)
    if problems:
        raise MartSpecError(f"{path.name}: " + "; ".join(problems))
    return spec


@dataclass(frozen=True, slots=True)
class MartRegistry:
    """The validated specs, in name order."""

    specs: tuple[MartSpec, ...]

    def __iter__(self) -> Iterator[MartSpec]:
        return iter(self.specs)

    def __len__(self) -> int:
        return len(self.specs)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(s.name for s in self.specs)

    def spec(self, name: str) -> MartSpec:
        for s in self.specs:
            if s.name == name:
                return s
        raise MartSpecError(f"no mart {name!r}; known: {', '.join(self.names) or '(none)'}")


def load_registry(directory: Path | None = None) -> MartRegistry:
    """Every ``*.yaml`` spec under ``directory`` (default: the packaged specs)."""
    root = Path(directory) if directory is not None else specs_dir()
    specs = [load_spec_from(p) for p in sorted(root.glob("*.yaml"))]
    return MartRegistry(tuple(sorted(specs, key=lambda s: s.name)))


@cache
def packaged_registry() -> MartRegistry:
    """The packaged registry (validated once per process)."""
    return load_registry()


# ---------------------------------------------------------------------------
# SQL generation
# ---------------------------------------------------------------------------


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _sql_str(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def select_list_sql(spec: MartSpec) -> str:
    """``CAST(<expr> AS <type>) AS "<name>"`` per column, spec order."""
    return ",\n  ".join(f"CAST({c.expr} AS {c.type}) AS {_q(c.name)}" for c in spec.expanded)


def from_clause_sql(spec: MartSpec) -> str:
    """The base table (its ``cte_<alias>`` when the builder reduces it), then one
    ``LEFT JOIN`` per joined source on ``join_on``; ``read`` sources are never joined."""
    base = spec.base
    lines = [f"FROM {f'cte_{base.alias}' if base.cte else base.relation} AS {base.alias}"]
    for s in spec.sources[1:]:
        if s.join == "read":
            continue
        relation = f"cte_{s.alias}" if s.cte else s.relation
        lines.append(
            f"LEFT JOIN {relation} AS {s.alias} ON {s.alias}.{s.join_on} = {base.alias}.{s.join_on}"
        )
    return "\n".join(lines)


def mart_sql(spec: MartSpec, ctes: Mapping[str, str] | None = None) -> str:
    """The mart's full statement: ``WITH cte_<alias> AS (...)`` for every ``cte: true``
    source (``ctes`` maps alias -> SELECT), the SELECT list, the join skeleton."""
    ctes = dict(ctes or {})
    missing = [a for a in spec.cte_aliases if a not in ctes]
    extra = sorted(set(ctes) - set(spec.cte_aliases))
    if missing or extra:
        raise MartSpecError(
            f"mart {spec.name}: CTEs missing for {missing} / not declared as sources {extra}"
        )
    head = ""
    if spec.cte_aliases:
        parts = [f"cte_{a} AS (\n{ctes[a].strip()}\n)" for a in spec.cte_aliases]
        head = "WITH " + ",\n".join(parts) + "\n"
    return f"{head}SELECT\n  {select_list_sql(spec)}\n{from_clause_sql(spec)}"


def sql_sha256(sql: str) -> str:
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Layout and status
# ---------------------------------------------------------------------------


def mart_dir(lake_root: Path | str, tier: str, name: str) -> Path:
    """``<lake_root>/marts/<tier>/<name>``."""
    return Path(lake_root) / MARTS_LAYER / tier / name


def mart_files(directory: Path, partitioned: bool) -> list[Path]:
    """The published Parquet files of a mart directory, sorted."""
    if not directory.is_dir():
        return []
    pattern = f"{BUCKET_COLUMN}=*/{PART}" if partitioned else PART
    return sorted(directory.glob(pattern))


def mart_glob(directory: Path, partitioned: bool) -> str:
    """The ``read_parquet`` glob of a mart directory (forward slashes)."""
    base = directory.resolve().as_posix()
    return f"{base}/{BUCKET_COLUMN}=*/{PART}" if partitioned else f"{base}/{PART}"


def status_entry(lake_root: Path | str, spec: MartSpec) -> dict[str, Any] | None:
    from mimicwarehouse.loader.manifest import read_status

    return read_status(Path(lake_root))["steps"].get(spec.status_key)


def mart_attempt(lake_root: Path | str, tier: str, spec: MartSpec) -> dict[str, Any] | None:
    entry = status_entry(lake_root, spec)
    attempt = ((entry or {}).get("tiers") or {}).get(tier)
    return dict(attempt) if attempt else None


def mart_complete(lake_root: Path | str, tier: str, spec: MartSpec) -> bool:
    """Complete for ``tier`` per :func:`dag.snapshot.complete_for_tier` **and** at least one
    published file exists."""
    from mimicwarehouse.dag.snapshot import complete_for_tier

    entry = status_entry(lake_root, spec)
    if entry is None or not complete_for_tier(entry, tier):
        return False
    return bool(mart_files(mart_dir(lake_root, tier, spec.name), spec.partition is not None))


def read_mart_manifest(lake_root: Path | str, tier: str, name: str) -> dict[str, Any] | None:
    """The mart's ``manifest.json`` (hashes, ids, counts), or None."""
    path = mart_dir(lake_root, tier, name) / MANIFEST_FILE
    if not path.is_file():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def _record_status(ctx: StepContext, spec: MartSpec, *, status: str, **attempt: Any) -> None:
    """Merge this attempt into the per-tier entry ``marts.<name>`` (the EP-37 ``per_tier``
    shape: completeness set on success and revoked for the tier on failure)."""
    from mimicwarehouse.loader.manifest import update_status, utc_now_iso

    entry = status_entry(ctx.lake_root, spec) or {}
    tiers = dict(entry.get("tiers") or {})
    tiers[ctx.tier] = {
        "status": status,
        "build_id": ctx.build_id,
        "run_id": ctx.run.run_id if ctx.run is not None else None,
        "finished_at": utc_now_iso(),
        "spec_sha256": spec.spec_sha256,
        **attempt,
    }
    fields: dict[str, Any] = {
        "per_tier": True,
        "layer": MARTS_LAYER,
        "tiers": tiers,
        "mart": spec.name,
        "version": spec.version,
    }
    if status == "done":
        if ctx.tier == "dev":
            fields["dev_ready"] = True
            if entry.get("tier_complete") != "full":
                fields["tier_complete"] = "dev"
        else:  # fixture / demo (own lake roots) and full
            fields["tier_complete"] = "full"
    elif ctx.tier == "dev":
        fields["dev_ready"] = False
        if entry.get("tier_complete") == "dev":
            fields["tier_complete"] = None
    else:
        fields["tier_complete"] = None
        fields["dev_ready"] = False
    update_status(ctx.lake_root, spec.status_key, **fields)


def record_failure(ctx: StepContext, spec: MartSpec, error_class: str) -> None:
    """Record a failed attempt for the context's tier (a builder's binding refusal; the
    writer records its own DuckDB failures)."""
    _record_status(ctx, spec, status="failed", error_class=error_class)


# ---------------------------------------------------------------------------
# The writer
# ---------------------------------------------------------------------------


def _bench(ctx: StepContext, spec: MartSpec, *, wall_s: float, ok: bool, **fields: Any) -> None:
    from mimicwarehouse import run as run_mod

    run_mod.bench(
        BENCH_KIND,
        spec.step_name,
        wall_s=wall_s,
        tier=ctx.tier,
        run_id=ctx.run.run_id if ctx.run is not None else None,
        build_id=ctx.build_id,
        settings=ctx.settings,
        ok=ok,
        **fields,
    )


def _layer_snapshot_ids(ctx: StepContext, layers: tuple[str, ...]) -> dict[str, str]:
    """The snapshot id of every layer read (cached per build connection)."""
    from mimicwarehouse.dag.snapshot import layer_snapshot

    out: dict[str, str] = {}
    for layer in layers:
        key = f"marts.{layer}_snapshot_id"
        if key not in ctx.state:
            ctx.state[key] = layer_snapshot(ctx.lake_root, layer, ctx.tier, settings=ctx.settings)
        out[layer] = str(ctx.state[key])
    return out


def write_mart(
    ctx: StepContext,
    spec: MartSpec,
    sql: str,
    *,
    layers_read: tuple[str, ...] = ("core",),
    params: Mapping[str, Any] | None = None,
) -> StepOutcome:
    """Materialise ``sql`` (the spec's statement) and publish the mart for the context's
    tier (module docstring); ``params`` (JSON values) are recorded in ``manifest.json``.
    Raises :class:`MartBuildError` on a DuckDB failure, after recording the failed
    attempt."""
    import duckdb

    from mimicwarehouse import fsio, publish
    from mimicwarehouse.dag.runner import StepOutcome
    from mimicwarehouse.dag.snapshot import layer_snapshot, record_snapshot_once
    from mimicwarehouse.loader.manifest import (
        ManifestLine,
        append_manifest,
        lake_relative_posix,
        sha256_streamed,
        utc_now_iso,
        writer_version,
    )
    from mimicwarehouse.loader.stage import COPY_OPTIONS
    from mimicwarehouse.run import ResourceLog

    partitioned = spec.partition is not None
    dest = mart_dir(ctx.lake_root, ctx.tier, spec.name)
    new = publish.new_path_for(dest)
    if new.exists():
        publish.rmtree(new)
    new.mkdir(parents=True, exist_ok=True)
    snapshot_ids = _layer_snapshot_ids(ctx, layers_read)
    statement_sha = sql_sha256(sql)
    temp = f"_mwh_mart_{spec.name}"
    columns = ", ".join(_q(c) for c in spec.column_names)
    order = ", ".join(_q(c) for c in spec.sort) or columns

    def work() -> int:
        ctx.con.execute(f"CREATE OR REPLACE TEMP TABLE {temp} AS {sql}")
        try:
            total = 0
            if partitioned:
                buckets = [
                    int(r[0])
                    for r in ctx.con.execute(
                        f"SELECT DISTINCT {BUCKET_COLUMN} FROM {temp} ORDER BY 1"
                    ).fetchall()
                ]
                for bucket in buckets:
                    part = new / f"{BUCKET_COLUMN}={bucket}" / PART
                    part.parent.mkdir(parents=True, exist_ok=True)
                    row = ctx.con.execute(
                        f"COPY (SELECT {columns} FROM {temp} WHERE {BUCKET_COLUMN} = {bucket} "
                        f"ORDER BY {order}) TO {_sql_str(part.resolve().as_posix())} "
                        f"({COPY_OPTIONS})"
                    ).fetchone()
                    total += int(row[0]) if row else 0
            else:
                part = new / PART
                row = ctx.con.execute(
                    f"COPY (SELECT {columns} FROM {temp} ORDER BY {order}) TO "
                    f"{_sql_str(part.resolve().as_posix())} ({COPY_OPTIONS})"
                ).fetchone()
                total = int(row[0]) if row else 0
            return total
        finally:
            ctx.con.execute(f"DROP TABLE IF EXISTS {temp}")

    try:
        rows, usage = ResourceLog.measure(work, data_root=ctx.settings.data_root)
    except duckdb.Error as exc:
        from mimicwarehouse.safe import sanitize_error_text

        publish.rmtree(new)
        error_class = type(exc).__name__
        _record_status(ctx, spec, status="failed", error_class=error_class)
        _bench(ctx, spec, wall_s=0.0, ok=False, error=error_class)
        raise MartBuildError(
            f"{spec.name}: {error_class}: {sanitize_error_text(str(exc))}"
        ) from None

    built_at = utc_now_iso()
    staged = mart_files(new, partitioned)
    manifest = {
        "mart": spec.name,
        "version": spec.version,
        "grain": spec.grain,
        "tier": ctx.tier,
        "spec_sha256": spec.spec_sha256,
        "sql_sha256": statement_sha,
        "schema_hash": spec.schema_hash,
        "snapshot_ids": snapshot_ids,
        "rows": rows,
        "files": len(staged),
        "partition": spec.partition,
        "sort": list(spec.sort),
        "columns": [[c.name, c.type] for c in spec.expanded],
        "run_id": ctx.run.run_id if ctx.run is not None else None,
        "build_id": ctx.build_id,
        "built_at": built_at,
        "writer_version": writer_version(),
    }
    if params is not None:
        manifest["params"] = dict(params)
    fsio.atomic_write_text(
        new / MANIFEST_FILE, json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    )
    publish.swap_dir(new, dest)

    published = mart_files(dest, partitioned)
    counts = {
        Path(str(f)).resolve(): int(n)
        for f, n in ctx.con.execute(
            "SELECT file_name, num_rows FROM parquet_file_metadata("
            f"{_sql_str(mart_glob(dest, partitioned))})"
        ).fetchall()
    }
    lines: list[ManifestLine] = []
    nbytes = 0
    version = writer_version()
    for path in published:
        size = path.stat().st_size
        nbytes += size
        lines.append(
            ManifestLine(
                schema=MARTS_SCHEMA,
                table=spec.name,
                path=lake_relative_posix(path, ctx.lake_root),
                sha256=sha256_streamed(path),
                bytes=size,
                rows=counts.get(path.resolve(), 0),
                schema_hash=spec.schema_hash,
                writer_version=version,
                source_sha256=statement_sha,
                raw_snapshot_id=snapshot_ids.get("core"),
                build_id=ctx.build_id,
                ts=built_at,
            )
        )
    if lines:
        append_manifest(ctx.lake_root, ctx.build_id, lines)
    _record_status(
        ctx,
        spec,
        status="done",
        rows=rows,
        bytes=nbytes,
        files=len(published),
        sql_sha256=statement_sha,
        error_class=None,
    )
    record_snapshot_once(
        ctx.lake_root,
        layer=MARTS_LAYER,
        tier=ctx.tier,
        snapshot_id=layer_snapshot(ctx.lake_root, MARTS_LAYER, ctx.tier, settings=ctx.settings),
        build_id=ctx.build_id,
    )
    _bench(
        ctx,
        spec,
        wall_s=usage.wall_s,
        ok=True,
        peak_rss_mb=usage.peak_rss_mb,
        disk_delta_mb=usage.disk_delta_mb,
        rows=rows,
        bytes_out=nbytes,
        files=len(published),
    )
    _LOG.info(
        "mart %s (%s): %s rows in %d file(s), %s bytes, wall=%.2fs",
        spec.name,
        ctx.tier,
        f"{rows:,}",
        len(published),
        f"{nbytes:,}",
        usage.wall_s,
    )
    return StepOutcome(rows=rows, bytes_out=nbytes, files=len(published), layer=MARTS_LAYER)


# ---------------------------------------------------------------------------
# The catalog extension: marts.<name> views + meta.mart_tables / meta.mart_columns
# ---------------------------------------------------------------------------

TABLES_COLUMNS: tuple[tuple[str, str], ...] = (
    ("mart", "VARCHAR"),
    ("version", "INTEGER"),
    ("grain", "VARCHAR"),
    ("keys", "VARCHAR"),
    ("partition", "VARCHAR"),
    ("description", "VARCHAR"),
    ("built", "BOOLEAN"),
    ("stale", "BOOLEAN"),
    ("rows", "BIGINT"),
    ("rows_suppressed", "BOOLEAN"),
    ("files", "INTEGER"),
    ("bytes", "BIGINT"),
    ("n_columns", "INTEGER"),
    ("spec_sha256", "VARCHAR"),
    ("sql_sha256", "VARCHAR"),
    ("run_id", "VARCHAR"),
    ("build_id", "VARCHAR"),
    ("built_at", "VARCHAR"),
    ("k", "INTEGER"),
    ("view", "VARCHAR"),
)
COLUMNS_COLUMNS: tuple[tuple[str, str], ...] = (
    ("mart", "VARCHAR"),
    ("ordinal", "INTEGER"),
    ("column", "VARCHAR"),
    ("duckdb_type", "VARCHAR"),
    ("unit", "VARCHAR"),
    ("concept_group", "VARCHAR"),
    ("is_key", "BOOLEAN"),
    ("source", "VARCHAR"),
    ("description", "VARCHAR"),
)

_TABLES_COMMENT = (
    "The mart registry (marts/registry.py, EP-55): one row per mart spec "
    "(marts/specs/<name>.yaml) - version, grain, keys, partition, description, built "
    "(whether this tier holds it under lake/marts/<tier>/<name>/), stale (built from another "
    "spec than the packaged one: the view shows the built columns until a --force rebuild), "
    "rows (NULL below k, rows_suppressed), files, bytes, n_columns, the packaged spec hash and "
    "the built SQL hash, the run and build ids, built_at, k and the session view marts.<name>. "
    "Registry read under safe_query."
)
_COLUMNS_COMMENT = (
    "The mart columns (marts/registry.py, EP-55): one row per declared column of every mart "
    "spec - ordinal, name, DuckDB type, unit (the EP-39 catalogue's canonical unit where "
    "concept_group names a curated group), is_key, the source expression and the "
    "description. Contract text only; DATA-DICTIONARY.md renders it."
)


def _catalog_info(con: duckdb.DuckDBPyConnection) -> tuple[Path, int]:
    row = con.execute("SELECT lake_root, k_default FROM meta.catalog_info").fetchone()
    if row is None or not row[0]:
        raise MartBuildError("meta.catalog_info carries no lake_root - extension order?")
    k = int(row[1]) if row[1] is not None else 11
    return Path(str(row[0])), k


def _cell(value: int | None, k: int) -> int | None:
    """``value`` unless it is a small cell (``1 .. k-1``), then None (D-33)."""
    if value is None:
        return None
    return None if 0 < value < k else value


def register_marts(con: duckdb.DuckDBPyConnection, tier: str) -> None:
    """The catalog extension body (module docstring). DDL and registry text only; never
    opens a connection, never creates a view over a mart that is not complete."""
    lake_root, k = _catalog_info(con)
    registry = packaged_registry()
    con.execute(f"CREATE SCHEMA IF NOT EXISTS {MARTS_SCHEMA}")
    table_rows: list[list[Any]] = []
    column_rows: list[list[Any]] = []
    views = 0
    for spec in registry:
        partitioned = spec.partition is not None
        directory = mart_dir(lake_root, tier, spec.name)
        built = mart_complete(lake_root, tier, spec)
        manifest = read_mart_manifest(lake_root, tier, spec.name) if built else None
        # a spec edited after the build: the view shows the columns the files hold (a view
        # over a missing column would fail the whole catalog build) and the row says stale
        stale = bool(manifest is not None and manifest.get("spec_sha256") != spec.spec_sha256)
        names = (
            [str(c[0]) for c in manifest.get("columns") or []]
            if stale and manifest is not None
            else list(spec.column_names)
        )
        if stale:
            _LOG.warning(
                "mart %s (%s) was built from another spec than the packaged one - its view "
                "shows the built columns; rebuild with `mwh build --tier %s --select %s --force`",
                spec.name,
                tier,
                tier,
                spec.step_name,
            )
        view: str | None = None
        if built and names:
            columns = ", ".join(_q(c) for c in names)
            relation = (
                f"read_parquet({_sql_str(mart_glob(directory, partitioned))}, "
                "hive_partitioning = false)"
            )
            con.execute(
                f"CREATE OR REPLACE VIEW {MARTS_SCHEMA}.{_q(spec.name)} AS "
                f"SELECT {columns} FROM {relation}"
            )
            comment = (
                f"{spec.description} Mart {spec.name} v{spec.version} (EP-55), grain "
                f"{spec.grain}, keys {', '.join(spec.key)}; built for tier {tier} by "
                f"{(manifest or {}).get('build_id') or '?'}. meta.mart_columns carries the "
                "column contract."
            )
            con.execute(f"COMMENT ON VIEW {MARTS_SCHEMA}.{_q(spec.name)} IS {_sql_str(comment)}")
            for c in spec.expanded:
                if c.name not in names:
                    continue
                text = c.description + (f" [{c.unit}]" if c.unit else "")
                con.execute(
                    f"COMMENT ON COLUMN {MARTS_SCHEMA}.{_q(spec.name)}.{_q(c.name)} IS "
                    f"{_sql_str(text)}"
                )
            view = f"{MARTS_SCHEMA}.{spec.name}"
            views += 1
        rows_n = (manifest or {}).get("rows")
        files_n = (manifest or {}).get("files")
        nbytes = (
            sum(p.stat().st_size for p in mart_files(directory, partitioned)) if built else None
        )
        table_rows.append(
            [
                spec.name,
                spec.version,
                spec.grain,
                ", ".join(spec.key),
                spec.partition,
                " ".join(spec.description.split()),
                built,
                stale,
                _cell(rows_n, k),
                rows_n is not None and _cell(rows_n, k) is None and rows_n != 0,
                files_n,
                nbytes,
                len(spec.expanded),
                spec.spec_sha256,
                (manifest or {}).get("sql_sha256"),
                (manifest or {}).get("run_id"),
                (manifest or {}).get("build_id"),
                (manifest or {}).get("built_at"),
                k,
                view,
            ]
        )
        keys = set(spec.key) | ({spec.partition} if spec.partition else set())
        for ordinal, c in enumerate(spec.expanded, start=1):
            column_rows.append(
                [
                    spec.name,
                    ordinal,
                    c.name,
                    c.type,
                    c.unit,
                    c.concept_group,
                    c.name in keys,
                    c.expr,
                    c.description,
                ]
            )
    con.execute("CREATE SCHEMA IF NOT EXISTS meta")
    for table, ddl_columns, rows, comment in (
        (TABLES_TABLE, TABLES_COLUMNS, table_rows, _TABLES_COMMENT),
        (COLUMNS_TABLE, COLUMNS_COLUMNS, column_rows, _COLUMNS_COMMENT),
    ):
        ddl = ", ".join(f"{_q(name)} {typ}" for name, typ in ddl_columns)
        con.execute(f"DROP TABLE IF EXISTS meta.{_q(table)}")
        con.execute(f"CREATE TABLE meta.{_q(table)} ({ddl})")
        if rows:
            placeholders = ", ".join("?" for _ in ddl_columns)
            con.executemany(f"INSERT INTO meta.{_q(table)} VALUES ({placeholders})", rows)
        con.execute(f"COMMENT ON TABLE meta.{_q(table)} IS {_sql_str(comment)}")
    _LOG.info(
        "catalog extension marts (%s): %d spec(s), %d view(s), %d column row(s)",
        tier,
        len(registry),
        views,
        len(column_rows),
    )


__all__ = [
    "BENCH_KIND",
    "BUCKET_COLUMN",
    "COLUMNS_COLUMNS",
    "COLUMNS_TABLE",
    "DUCKDB_TYPES",
    "JOIN_KEYS",
    "MANIFEST_FILE",
    "MARTS_LAYER",
    "MARTS_SCHEMA",
    "NUM_BUCKETS",
    "PART",
    "SPECS_DIRNAME",
    "STAT_TYPES",
    "STAT_WORDS",
    "SUMMARY_GRAINS",
    "TABLES_COLUMNS",
    "TABLES_TABLE",
    "UNITLESS_STATS",
    "ColumnFamily",
    "FamilyVar",
    "MartBuildError",
    "MartColumn",
    "MartRegistry",
    "MartSource",
    "MartSpec",
    "MartSpecError",
    "check_units",
    "from_clause_sql",
    "load_registry",
    "load_spec_from",
    "mart_attempt",
    "mart_complete",
    "mart_dir",
    "mart_files",
    "mart_glob",
    "mart_sql",
    "packaged_registry",
    "read_mart_manifest",
    "record_failure",
    "register_marts",
    "select_list_sql",
    "specs_dir",
    "sql_sha256",
    "status_entry",
    "write_mart",
]
