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
filtered by ``--select`` names, ``--tag`` tags and the build tier; ``with_deps`` closes
the selection over its ``depends_on`` ancestors (EP-37 ``--with-deps``).

**Spec discovery (EP-37, ledger P3C-2).** :func:`load_dag` with no name merges **every**
packaged ``dag/specs/*.yaml`` into one graph: ``stage.yaml`` first, the others in file-name
order; step names must be unique across files except the shared steps in
:data:`SHARED_STEPS` (``catalog``), which are deduplicated by name with their
``depends_on`` / ``tags`` unioned — so ``catalog`` runs after the stage steps *and* after
every concept step; cross-file ``depends_on`` references resolve after the merge, which is
why validation runs once on the merged document. ``load_dag("stage")`` still loads one
file. A ``python`` step may carry a ``target`` (``<schema>.<table>``): the ``status.json``
key the runner uses for skip / completeness (:attr:`Step.status_key`), the way
``qualified_table`` serves a ``stage`` step.

Step *handlers* live in :data:`mimicwarehouse.dag.runner.STEP_HANDLERS` — a registry
dict keyed by kind, so later EPs add kinds without touching the runner.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from graphlib import CycleError, TopologicalSorter
from importlib.resources import files
from pathlib import Path, PurePosixPath
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from mimicwarehouse.config import Tier

SPECS_DIRNAME = "specs"
#: The spec every P2 stage brief extends (EP-20 adds the remaining tables); merged first.
DEFAULT_SPEC = "stage"
#: Step names that may appear in several spec files and are merged by name (EP-37):
#: ``depends_on`` and ``tags`` are unioned, ``tiers`` stay "every tier" when either side
#: says so. Every other duplicate across files is a :class:`DagError`.
SHARED_STEPS: frozenset[str] = frozenset({"catalog"})

Kind = Literal["stage", "sql", "python", "catalog"]

_STEP_NAME = re.compile(r"^[a-z][a-z0-9._-]*$")
_TARGET = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")

#: Per-kind field contract: (required, optional) beyond the common selection fields.
KIND_FIELDS: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "stage": (
        frozenset({"schema_name", "table", "source"}),
        frozenset({"size_class", "partitioned", "sort_by", "demo_source"}),
    ),
    "sql": (frozenset({"file", "target"}), frozenset()),
    # EP-37: a python step may name the status.json key it completes (``target``)
    "python": (frozenset({"callable_name"}), frozenset({"target"})),
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
    # sql (+ python since EP-37)
    file: str | None = Field(default=None, description="SQL file under dag/sql/ (EP-37)")
    target: str | None = Field(
        default=None,
        description="<schema>.<table> the step completes — its status.json key (sql; "
        "optional on python steps since EP-37, e.g. mimiciv_derived.<concept>)",
    )
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
        if self.target is not None and not _TARGET.match(self.target):
            raise ValueError(f"step {self.name}: target must be '<schema>.<table>' (lower)")
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

    @property
    def status_key(self) -> str | None:
        """The ``status.json`` entry this step completes — :attr:`qualified_table` for a
        stage step, ``target`` for a sql / python step that declares one (EP-37), None for
        steps without a completion record (``catalog``, ``meta.profile``). The runner's
        skip logic and :func:`~mimicwarehouse.dag.snapshot.complete_for_tier` read it."""
        return self.qualified_table or self.target


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

    def explicit_selection(
        self, *, select: list[str] | None = None, tags: list[str] | None = None
    ) -> frozenset[str]:
        """The step names ``--select`` / ``--tag`` name directly (both filters conjunctive;
        every step when neither is given) — before any ``with_deps`` closure. Unknown
        names/tags raise :class:`DagError`. The runner applies ``--force`` to exactly this
        set when ``--with-deps`` pulled ancestors in (EP-37)."""
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
        chosen: set[str] = set()
        for s in self.steps:
            if select and s.name not in select:
                continue
            if tags and not set(tags) & set(s.tags):
                continue
            chosen.add(s.name)
        return frozenset(chosen)

    def ancestors(self, names: Iterable[str]) -> frozenset[str]:
        """``names`` plus every transitive ``depends_on`` ancestor (EP-37 ``--with-deps``)."""
        by_name = {s.name: s for s in self.steps}
        seen: set[str] = set()
        stack = list(names)
        while stack:
            name = stack.pop()
            if name in seen:
                continue
            seen.add(name)
            stack.extend(by_name[name].depends_on)
        return frozenset(seen)

    def ordered(
        self,
        *,
        select: list[str] | None = None,
        tags: list[str] | None = None,
        tier: Tier | str | None = None,
        with_deps: bool = False,
    ) -> tuple[Step, ...]:
        """The steps to run, in topological order.

        ``select`` keeps exactly the named steps (their dependencies are **not** pulled
        in — a selected step over an incomplete dependency simply finds no staged input —
        unless ``with_deps`` closes the selection over its ancestors, EP-37: complete
        ancestors then skip in the runner, incomplete ones run); ``tags`` keeps steps
        carrying at least one of the tags; ``tier`` drops steps whose ``tiers`` list
        excludes it. Unknown names/tags raise :class:`DagError`.
        """
        by_name = {s.name: s for s in self.steps}
        chosen = self.explicit_selection(select=select, tags=tags)
        if with_deps:
            chosen = self.ancestors(chosen)
        out: list[Step] = []
        for name in self._topological_names():
            s = by_name[name]
            if s.name not in chosen:
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


def load_spec_document(path: Path) -> dict[str, Any]:
    """Parse one spec YAML file to its top-level mapping (no validation — cross-file
    ``depends_on`` references only resolve after :func:`merge_spec_documents`)."""
    path = Path(path)
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise DagError(f"{path.name}: cannot read ({exc})") from exc
    if not isinstance(doc, dict):
        raise DagError(f"{path.name}: top level must be a mapping")
    return doc


def _validate(doc: dict[str, Any], label: str) -> DagSpec:
    try:
        return DagSpec.model_validate(doc)
    except ValidationError as exc:
        lines = [f"{label}: {exc.error_count()} validation error(s)"]
        for e in exc.errors():
            loc = ".".join(str(p) for p in e["loc"])
            lines.append(f"  {loc}: {e['msg']}")
        raise DagError("\n".join(lines)) from None


def _merge_shared(first: dict[str, Any], other: dict[str, Any], name: str, files: str) -> None:
    """Fold ``other`` (a later file's copy of shared step ``name``) into ``first`` in place:
    ``kind`` must agree, ``depends_on`` / ``tags`` union in first-seen order, ``tiers``
    stay empty (= every tier) when either side is empty, else union."""
    if other.get("kind") != first.get("kind"):
        raise DagError(
            f"shared step {name!r} has kind {first.get('kind')!r} and {other.get('kind')!r} "
            f"across {files}"
        )
    for key in ("depends_on", "tags"):
        merged = list(first.get(key) or [])
        merged.extend(v for v in (other.get(key) or []) if v not in merged)
        first[key] = merged
    tiers_a, tiers_b = list(first.get("tiers") or []), list(other.get("tiers") or [])
    if not tiers_a or not tiers_b:
        first["tiers"] = []
    else:
        first["tiers"] = tiers_a + [t for t in tiers_b if t not in tiers_a]


def merge_spec_documents(docs: Sequence[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
    """One spec document from several ``(file label, document)`` pairs (module docstring):
    steps concatenate in the given order; a name in :data:`SHARED_STEPS` that recurs is
    merged into its first occurrence; any other recurring name is a :class:`DagError`.
    The merged ``version`` is the maximum."""
    steps: list[dict[str, Any]] = []
    where: dict[str, str] = {}
    version = 1
    for label, doc in docs:
        version = max(version, int(doc.get("version") or 1))
        for raw in doc.get("steps") or []:
            if not isinstance(raw, dict) or "name" not in raw:
                raise DagError(f"{label}: every step needs a name (got {raw!r})")
            name = str(raw["name"])
            if name in where:
                if name not in SHARED_STEPS:
                    raise DagError(
                        f"step {name!r} is defined in both {where[name]} and {label}; only "
                        f"{sorted(SHARED_STEPS)} may recur across spec files"
                    )
                first = next(s for s in steps if s["name"] == name)
                _merge_shared(first, dict(raw), name, f"{where[name]} and {label}")
                continue
            where[name] = label
            steps.append(dict(raw))
    return {"version": version, "steps": steps}


def load_dag_from(path: Path) -> DagSpec:
    """Parse and validate one spec YAML file; tests point this at crafted specs."""
    path = Path(path)
    return _validate(load_spec_document(path), path.name)


def spec_files() -> list[Path]:
    """The packaged spec files in merge order: ``stage.yaml`` first, then the rest by name."""
    paths = sorted(specs_root().glob("*.yaml"))
    return sorted(paths, key=lambda p: (p.stem != DEFAULT_SPEC, p.name))


def load_dag(name: str | None = None) -> DagSpec:
    """The packaged DAG: every ``dag/specs/*.yaml`` merged into one graph (module
    docstring; EP-37 spec discovery), or one named spec ``dag/specs/<name>.yaml``."""
    if name is not None:
        path = specs_root() / f"{name}.yaml"
        if not path.is_file():
            known = sorted(p.stem for p in specs_root().glob("*.yaml"))
            raise DagError(f"no spec {name!r}; known: {known}")
        return load_dag_from(path)
    files = spec_files()
    if not files:
        raise DagError(f"no spec files under {specs_root()}")
    merged = merge_spec_documents([(p.name, load_spec_document(p)) for p in files])
    return _validate(merged, " + ".join(p.name for p in files))


__all__ = [
    "DEFAULT_SPEC",
    "KIND_FIELDS",
    "SHARED_STEPS",
    "SPECS_DIRNAME",
    "DagError",
    "DagSpec",
    "Kind",
    "Step",
    "load_dag",
    "load_dag_from",
    "load_spec_document",
    "merge_spec_documents",
    "spec_files",
    "specs_root",
]
