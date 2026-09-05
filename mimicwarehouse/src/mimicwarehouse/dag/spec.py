"""DAG spec — pydantic models + the YAML specs (EP-19 item 1; D-20, DESIGN §15).

A spec is a YAML document ``{version, steps: [...]}`` under ``dag/specs/`` (package
data, like the EP-9 contract). Each step has a ``kind`` — ``stage`` (typed CSV →
Parquet through the EP-17/18 loader), ``sql`` (a file under ``dag/sql/``, EP-37),
``python`` (a ``module:function`` callable, EP-50) or ``catalog`` (EP-21) — plus
``depends_on`` / ``tags`` / ``tiers`` selection metadata. ``stage`` steps normally
carry only ``schema, table, source``: ``size_class`` / ``partitioned`` / ``sort_by``
default to the contract's ``load_class`` / ``partitioned`` / ``sort_keys`` and are
per-step **overrides**, present in the YAML only when a brief deliberately deviates.

Validation (:class:`DagSpec`): unique step names, dependencies name existing steps,
the graph is acyclic (``graphlib.TopologicalSorter``), per-kind required/allowed
fields. :meth:`DagSpec.ordered` returns the topological execution order, optionally
filtered by ``--select`` names, ``--tag`` tags and the build tier.

Step *handlers* live in :data:`mimicwarehouse.dag.runner.STEP_HANDLERS` — a registry
dict keyed by kind, so later EPs add kinds without touching the runner.
"""

from __future__ import annotations

import re
from graphlib import CycleError, TopologicalSorter
from importlib.resources import files
from pathlib import Path, PurePosixPath
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from mimicwarehouse.config import Tier

SPECS_DIRNAME = "specs"
#: The spec every P2 stage brief extends (EP-20 adds the remaining tables).
DEFAULT_SPEC = "stage"

Kind = Literal["stage", "sql", "python", "catalog"]

_STEP_NAME = re.compile(r"^[a-z][a-z0-9._-]*$")

#: Per-kind field contract: (required, optional) beyond the common selection fields.
KIND_FIELDS: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "stage": (
        frozenset({"schema_name", "table", "source"}),
        frozenset({"size_class", "partitioned", "sort_by", "demo_source"}),
    ),
    "sql": (frozenset({"file", "target"}), frozenset()),
    "python": (frozenset({"callable_name"}), frozenset()),
    "catalog": (frozenset(), frozenset()),
}
#: Every kind-specific field (to refuse, per step, the ones another kind owns).
_KIND_SPECIFIC: frozenset[str] = frozenset().union(*(r | o for r, o in KIND_FIELDS.values()))

_CALLABLE = re.compile(r"^[A-Za-z_][\w.]*:[A-Za-z_]\w*$")


class DagError(ValueError):
    """The DAG spec is malformed (duplicate names, unknown dependency, cycle, bad kind
    fields) or a selection names an unknown step/tag."""


class _Frozen(BaseModel):
    model_config = ConfigDict(
        frozen=True, extra="forbid", validate_by_name=True, validate_by_alias=True
    )


class Step(_Frozen):
    """One DAG step: identity, selection metadata, per-kind payload."""

    name: str
    kind: Kind
    depends_on: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    tiers: tuple[Tier, ...] = Field(
        default=(), description="tiers the step runs for; empty = every tier"
    )
    # stage
    schema_name: str | None = Field(default=None, alias="schema")
    table: str | None = None
    source: str | None = Field(
        default=None, description="source CSV relative to the raw root, posix slashes"
    )
    demo_source: str | None = Field(
        default=None,
        description="demo-tier override relative to the demo raw root (EP-22); present only "
        "when the demo file name differs from the derived default (`source` minus its leading "
        "dataset directory; the loader's .gz fallback covers the extension)",
    )
    size_class: Literal["small", "large"] | None = None
    partitioned: bool | None = None
    sort_by: tuple[str, ...] | None = None
    # sql
    file: str | None = Field(default=None, description="SQL file under dag/sql/ (EP-37)")
    target: str | None = None
    # python
    callable_name: str | None = Field(
        default=None,
        alias="callable",
        description="module:function called with (step, ctx) (EP-29's meta.profile; EP-50)",
    )

    @model_validator(mode="after")
    def _check(self) -> Step:
        if not _STEP_NAME.match(self.name):
            raise ValueError(f"step name {self.name!r} is not lower [a-z0-9._-]")
        required, optional = KIND_FIELDS[self.kind]
        allowed = required | optional
        present = {f for f in _KIND_SPECIFIC if getattr(self, f) is not None}
        missing = sorted(required - present)
        if missing:
            raise ValueError(f"step {self.name} (kind {self.kind}): missing field(s) {missing}")
        extra = sorted(present - allowed)
        if extra:
            raise ValueError(
                f"step {self.name} (kind {self.kind}): field(s) {extra} belong to another kind"
            )
        for field_name in ("source", "demo_source"):
            value = getattr(self, field_name)
            if value is not None and ("\\" in value or value.startswith("/")):
                raise ValueError(f"step {self.name}: {field_name} must be a relative posix path")
        if self.callable_name is not None and not _CALLABLE.match(self.callable_name):
            raise ValueError(f"step {self.name}: callable must be 'module:function'")
        return self

    @property
    def demo_relative_source(self) -> str | None:
        """The source path a **demo**-tier stage resolves under the demo raw root (EP-22):
        the explicit ``demo_source`` when set, else ``source`` minus its leading dataset
        directory (``mimic-iv-3.1/hosp/x.csv`` → ``hosp/x.csv``; the demo ships ``.csv.gz``,
        which the runner's existing ``.gz`` fallback picks up)."""
        if self.source is None:
            return None
        if self.demo_source is not None:
            return self.demo_source
        parts = PurePosixPath(self.source).parts
        return str(PurePosixPath(*parts[1:])) if len(parts) > 1 else self.source

    @property
    def qualified_table(self) -> str | None:
        """``<schema>.<table>`` for a stage step (its ``status.json`` key), else None."""
        if self.schema_name is None or self.table is None:
            return None
        return f"{self.schema_name}.{self.table}"


class DagSpec(_Frozen):
    """A validated DAG: unique names, known dependencies, acyclic."""

    version: int = Field(ge=1)
    steps: tuple[Step, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _check(self) -> DagSpec:
        names = [s.name for s in self.steps]
        dupes = sorted({n for n in names if names.count(n) > 1})
        if dupes:
            raise ValueError(f"duplicate step names {dupes}")
        known = set(names)
        for s in self.steps:
            unknown = sorted(set(s.depends_on) - known)
            if unknown:
                raise ValueError(f"step {s.name}: unknown dependenc(ies) {unknown}")
        try:
            self._topological_names()
        except CycleError as exc:
            raise ValueError(f"dependency cycle: {exc.args[1]}") from None
        return self

    # -- ordering / selection ----------------------------------------------------------------

    def _topological_names(self) -> tuple[str, ...]:
        """Deterministic topological order (steps added in spec order)."""
        ts: TopologicalSorter[str] = TopologicalSorter()
        for s in self.steps:
            ts.add(s.name, *s.depends_on)
        return tuple(ts.static_order())

    def step(self, name: str) -> Step:
        for s in self.steps:
            if s.name == name:
                return s
        raise DagError(f"no step {name!r}; known: {sorted(s.name for s in self.steps)}")

    def ordered(
        self,
        *,
        select: list[str] | None = None,
        tags: list[str] | None = None,
        tier: Tier | str | None = None,
    ) -> tuple[Step, ...]:
        """The steps to run, in topological order.

        ``select`` keeps exactly the named steps (their dependencies are **not** pulled
        in — a selected step over an incomplete dependency simply finds no staged input);
        ``tags`` keeps steps carrying at least one of the tags; ``tier`` drops steps
        whose ``tiers`` list excludes it. Unknown names/tags raise :class:`DagError`.
        """
        by_name = {s.name: s for s in self.steps}
        if select:
            unknown = sorted(set(select) - set(by_name))
            if unknown:
                raise DagError(
                    f"--select names unknown step(s) {unknown}; known: {sorted(by_name)}"
                )
        if tags:
            all_tags = {t for s in self.steps for t in s.tags}
            unknown = sorted(set(tags) - all_tags)
            if unknown:
                raise DagError(f"--tag names unknown tag(s) {unknown}; known: {sorted(all_tags)}")
        out: list[Step] = []
        for name in self._topological_names():
            s = by_name[name]
            if select and s.name not in select:
                continue
            if tags and not set(tags) & set(s.tags):
                continue
            if tier is not None and s.tiers and tier not in s.tiers:
                continue
            out.append(s)
        return tuple(out)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def specs_root() -> Path:
    """``dag/specs/`` inside the installed package (source tree or wheel)."""
    return Path(str(files(__package__).joinpath(SPECS_DIRNAME)))


def load_dag_from(path: Path) -> DagSpec:
    """Parse and validate one spec YAML file; tests point this at crafted specs."""
    path = Path(path)
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise DagError(f"{path.name}: cannot read ({exc})") from exc
    if not isinstance(doc, dict):
        raise DagError(f"{path.name}: top level must be a mapping")
    try:
        return DagSpec.model_validate(doc)
    except ValidationError as exc:
        lines = [f"{path.name}: {exc.error_count()} validation error(s)"]
        for e in exc.errors():
            loc = ".".join(str(p) for p in e["loc"])
            lines.append(f"  {loc}: {e['msg']}")
        raise DagError("\n".join(lines)) from None


def load_dag(name: str = DEFAULT_SPEC) -> DagSpec:
    """The packaged spec ``dag/specs/<name>.yaml``."""
    path = specs_root() / f"{name}.yaml"
    if not path.is_file():
        known = sorted(p.stem for p in specs_root().glob("*.yaml"))
        raise DagError(f"no spec {name!r}; known: {known}")
    return load_dag_from(path)


__all__ = [
    "DEFAULT_SPEC",
    "KIND_FIELDS",
    "SPECS_DIRNAME",
    "DagError",
    "DagSpec",
    "Kind",
    "Step",
    "load_dag",
    "load_dag_from",
    "specs_root",
]
