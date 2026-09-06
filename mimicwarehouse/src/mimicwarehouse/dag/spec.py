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
filtered by ``--select`` names (``with_deps`` pulls in their transitive ancestors,
EP-37), ``--tag`` tags and the build tier.

**Spec discovery (EP-37, ledger P3C-2).** :func:`load_dag` with no name merges **every**
packaged ``dag/specs/*.yaml`` into one graph: step names are unique across files except
the shared steps in :data:`SHARED_STEPS` (today only ``catalog``), which are deduplicated
by name with their ``depends_on`` and ``tags`` unioned — so ``catalog`` runs after the
stage steps *and* after every concept step, and ``--tag concepts`` reaches it. Cross-file
``depends_on`` references resolve after the merge; the packaged ``stage`` spec
(:data:`DEFAULT_SPEC`) is simply the first of the merged files. ``load_dag("stage")``
still loads that one file alone. Later specs (EP-39/44/45/50/53) add their own file and
rely on the same mechanism; there is no ``--spec`` option.

``python`` steps may carry an optional ``target`` — the ``status.json`` key of the
per-tier table they publish (EP-37's concept steps) — which makes them resumable
through the runner's skip logic exactly like a stage step (:attr:`Step.qualified_table`).

Step *handlers* live in :data:`mimicwarehouse.dag.runner.STEP_HANDLERS` — a registry
dict keyed by kind, so later EPs add kinds without touching the runner.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from graphlib import CycleError, TopologicalSorter
from importlib.resources import files
from pathlib import Path, PurePosixPath
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from mimicwarehouse.config import Tier

SPECS_DIRNAME = "specs"
#: The spec every P2 stage brief extends (EP-20 adds the remaining tables); the first of
#: the merged files (EP-37 discovery) and the origin of the shared ``catalog`` step.
DEFAULT_SPEC = "stage"
#: Steps more than one packaged spec may declare (EP-37): merged by name, ``depends_on``
#: and ``tags`` unioned, every other field required to agree.
SHARED_STEPS: frozenset[str] = frozenset({"catalog"})

Kind = Literal["stage", "sql", "python", "catalog"]

_STEP_NAME = re.compile(r"^[a-z][a-z0-9._-]*$")

#: Per-kind field contract: (required, optional) beyond the common selection fields.
KIND_FIELDS: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "stage": (
        frozenset({"schema_name", "table", "source"}),
        frozenset({"size_class", "partitioned", "sort_by", "demo_source"}),
    ),
    "sql": (frozenset({"file", "target"}), frozenset()),
    # EP-37: ``target`` = the status.json key a python step publishes (resumable steps)
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
        description="the status.json key of the table the step publishes "
        "(`<schema>.<table>`): required for sql steps, optional for python steps whose "
        "output is a resumable per-tier table (EP-37 concept steps)",
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
        """The ``status.json`` key of what the step publishes: ``<schema>.<table>`` for a
        stage step, else the step's ``target`` (``sql`` steps always; ``python`` steps that
        publish a resumable per-tier table, EP-37); None for a step without one
        (``meta.profile``, ``catalog``), which the runner therefore never skips."""
        if self.schema_name is not None and self.table is not None:
            return f"{self.schema_name}.{self.table}"
        return self.target


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

    def ancestors(self, names: Sequence[str]) -> set[str]:
        """The transitive ``depends_on`` closure of ``names`` (the names themselves
        excluded); unknown names raise :class:`DagError`."""
        by_name = {s.name: s for s in self.steps}
        out: set[str] = set()
        stack = list(names)
        while stack:
            name = stack.pop()
            if name not in by_name:
                raise DagError(f"unknown step {name!r}; known: {sorted(by_name)}")
            for dep in by_name[name].depends_on:
                if dep not in out:
                    out.add(dep)
                    stack.append(dep)
        return out - set(names)

    def ordered(
        self,
        *,
        select: list[str] | None = None,
        tags: list[str] | None = None,
        tier: Tier | str | None = None,
        with_deps: bool = False,
    ) -> tuple[Step, ...]:
        """The steps to run, in topological order.

        ``select`` keeps exactly the named steps — their dependencies are **not** pulled
        in (a selected step over an incomplete dependency simply finds no staged input)
        unless ``with_deps`` is set (EP-37: the transitive ancestors join the selection
        and the runner then skips the ones already complete); ``tags`` keeps steps
        carrying at least one of the tags; ``tier`` drops steps whose ``tiers`` list
        excludes it. The filters compose. Unknown names/tags raise :class:`DagError`.
        """
        by_name = {s.name: s for s in self.steps}
        if select:
            unknown = sorted(set(select) - set(by_name))
            if unknown:
                raise DagError(
                    f"--select names unknown step(s) {unknown}; known: {sorted(by_name)}"
                )
            if with_deps:
                select = sorted(set(select) | self.ancestors(select))
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


def _read_spec_doc(path: Path) -> dict[str, Any]:
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


def load_dag_from(path: Path) -> DagSpec:
    """Parse and validate one spec YAML file; tests point this at crafted specs."""
    path = Path(path)
    return _validate(_read_spec_doc(path), path.name)


def _unique(values: Sequence[Any]) -> list[Any]:
    return list(dict.fromkeys(values))


def merge_spec_documents(docs: Sequence[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
    """One spec document from several ``(label, document)`` pairs (EP-37 discovery):
    steps are appended in file order; a step declared in more than one file must be a
    :data:`SHARED_STEPS` name, its ``depends_on`` / ``tags`` are unioned (first file's
    order first) and every other field must agree — anything else is a
    :class:`DagError` naming both files. ``version`` is the maximum."""
    merged: dict[str, dict[str, Any]] = {}
    origin: dict[str, str] = {}
    version = 1
    for label, doc in docs:
        version = max(version, int(doc.get("version", 1) or 1))
        steps = doc.get("steps")
        if not isinstance(steps, list):
            raise DagError(f"{label}: 'steps' must be a list")
        for raw in steps:
            if not isinstance(raw, dict) or "name" not in raw:
                raise DagError(f"{label}: every step needs a 'name'")
            name = str(raw["name"])
            if name not in merged:
                merged[name] = dict(raw)
                origin[name] = label
                continue
            if name not in SHARED_STEPS:
                raise DagError(
                    f"step {name!r} is declared in both {origin[name]} and {label}; only "
                    f"{sorted(SHARED_STEPS)} may be shared across spec files"
                )
            have = merged[name]
            for key in set(have) | set(raw):
                if key in ("depends_on", "tags"):
                    continue
                if have.get(key) != raw.get(key):
                    raise DagError(
                        f"shared step {name!r}: field {key!r} differs between "
                        f"{origin[name]} and {label}"
                    )
            have["depends_on"] = _unique(
                list(have.get("depends_on") or []) + list(raw.get("depends_on") or [])
            )
            have["tags"] = _unique(list(have.get("tags") or []) + list(raw.get("tags") or []))
    return {"version": version, "steps": list(merged.values())}


def load_dag_from_files(paths: Sequence[Path]) -> DagSpec:
    """Merge several spec files (:func:`merge_spec_documents`) and validate the result
    as one graph — cross-file ``depends_on`` references resolve after the merge."""
    docs = [(Path(p).name, _read_spec_doc(Path(p))) for p in paths]
    labels = ", ".join(label for label, _ in docs)
    return _validate(merge_spec_documents(docs), labels)


def spec_paths() -> list[Path]:
    """Every packaged ``dag/specs/*.yaml``, :data:`DEFAULT_SPEC` first, the rest by name."""
    root = specs_root()
    default = root / f"{DEFAULT_SPEC}.yaml"
    others = sorted(p for p in root.glob("*.yaml") if p != default)
    return ([default] if default.is_file() else []) + others


def load_dag(name: str | None = None) -> DagSpec:
    """The packaged DAG. With ``name`` the single spec ``dag/specs/<name>.yaml`` (the
    EP-19 form, e.g. ``load_dag("stage")``); with no name **every** packaged spec merged
    into one graph (EP-37 discovery, module docstring) — what ``mwh build`` runs."""
    if name is None:
        paths = spec_paths()
        if not paths:
            raise DagError(f"no packaged specs under {specs_root()}")
        return load_dag_from_files(paths)
    path = specs_root() / f"{name}.yaml"
    if not path.is_file():
        known = sorted(p.stem for p in specs_root().glob("*.yaml"))
        raise DagError(f"no spec {name!r}; known: {known}")
    return load_dag_from(path)


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
    "load_dag_from_files",
    "merge_spec_documents",
    "spec_paths",
    "specs_root",
]
