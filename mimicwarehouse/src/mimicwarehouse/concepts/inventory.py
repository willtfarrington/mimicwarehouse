"""Concept inventory + order (EP-37 item 1; D-19, DESIGN §8).

The vendored ``mimic-iv/concepts_duckdb/<group>/<concept>.sql`` files (EP-8; read, never
moved) are enumerated into :class:`Concept` records — name, group, upstream-relative
path, ``sql_sha256`` of the vendored bytes (LF; equals ``VENDOR.json``'s ``sha256_lf``),
the pinned ``upstream_commit`` — plus the two reference sets a regex scan of the SQL
yields: ``depends_on`` (the ``mimiciv_derived.<x>`` tables it reads, i.e. the concepts
that must run first) and ``sources`` (the ``mimiciv_hosp`` / ``mimiciv_icu`` core tables
it reads, which become ``depends_on`` edges to the EP-19 stage steps). The execution
order is topological over ``depends_on`` (:func:`topological_order`; Kahn's algorithm,
ties broken by the upstream ``duckdb.sql`` driver order so the result is deterministic
and matches upstream where the graph allows); a cycle or a reference to an unknown
concept / core table is an :class:`InventoryError`.

Two generated, committed files are rendered from the inventory and drift-tested
(``python -m mimicwarehouse.concepts.inventory`` rewrites both; ``test_ep37`` asserts
they equal a fresh render):

``concepts/concepts.yaml``
    the inventory itself (:func:`render_inventory`) — what the runner loads
    (:func:`load_inventory`) and what ``meta.concept_versions`` / the docs table cite.
``dag/specs/concepts.yaml``
    the DAG spec (:func:`render_spec`): one ``python`` step ``concept.<group>.<name>``
    per concept (``callable`` :data:`CONCEPT_CALLABLE`, ``target``
    ``mimiciv_derived.<name>`` so the runner can skip/resume it, tags ``concepts`` +
    the group, all four tiers, ``depends_on`` = its concept and stage ancestors), the
    ``meta.concept_versions`` step after every concept, and the shared ``catalog`` step
    (merged with the stage spec's by :func:`mimicwarehouse.dag.spec.load_dag`).

Import budget: stdlib + yaml + pydantic + :mod:`mimicwarehouse.concepts` (the pin); the
schema contract loads only inside :func:`discover`. Never on the ``mwh --help`` path.
"""

from __future__ import annotations

import hashlib
import re
import sys
from collections.abc import Sequence
from functools import cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from mimicwarehouse.concepts import TREE_DIRNAME, vendor_info, vendor_root

#: Upstream-relative directory of the transpiled concepts (EP-8 allow-list).
CONCEPTS_DIR = "mimic-iv/concepts_duckdb"
#: The upstream CLI driver whose ``.read`` lines give the fallback order.
DRIVER_FILENAME = "duckdb.sql"
DERIVED_SCHEMA = "mimiciv_derived"
STAGE_SCHEMAS: tuple[str, ...] = ("mimiciv_hosp", "mimiciv_icu")
INVENTORY_FILENAME = "concepts.yaml"
#: ``dag/specs/<SPEC_NAME>.yaml``.
SPEC_NAME = "concepts"
STEP_PREFIX = "concept"
TAG = "concepts"
VERSIONS_STEP = "meta.concept_versions"
CATALOG_STEP = "catalog"
CONCEPT_CALLABLE = "mimicwarehouse.concepts.runner:run_concept"
VERSIONS_CALLABLE = "mimicwarehouse.concepts.runner:run_concept_versions"
TIERS: tuple[str, ...] = ("fixture", "demo", "dev", "full")
INVENTORY_VERSION = 1

_DERIVED_REF = re.compile(r"\bmimiciv_derived\.([a-z][a-z0-9_]*)", re.IGNORECASE)
_CORE_REF = re.compile(r"\b(mimiciv_hosp|mimiciv_icu)\.([a-z][a-z0-9_]*)", re.IGNORECASE)
_READ_LINE = re.compile(r"^\.read\s+(\S+)\s*$", re.MULTILINE)
_LINE_COMMENT = re.compile(r"--[^\n]*")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


class InventoryError(RuntimeError):
    """The vendored tree cannot be inventoried (cycle, unknown reference, drift)."""


class Concept(BaseModel):
    """One vendored concept file and its place in the graph (never a value)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    group: str
    path: str = Field(description="upstream-relative posix path of the vendored .sql")
    sql_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    upstream_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    depends_on: tuple[str, ...] = Field(default=(), description="concepts read first")
    sources: tuple[str, ...] = Field(default=(), description="core tables read (schema.table)")
    driver_order: int = Field(ge=0, description="position in the upstream duckdb.sql driver")

    @property
    def step_name(self) -> str:
        return f"{STEP_PREFIX}.{self.group}.{self.name}"

    @property
    def qualified_name(self) -> str:
        return f"{DERIVED_SCHEMA}.{self.name}"


class Inventory(BaseModel):
    """The concept set at one upstream pin, in execution order."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int = INVENTORY_VERSION
    upstream_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    concepts_dir: str = CONCEPTS_DIR
    driver: str = f"{CONCEPTS_DIR}/{DRIVER_FILENAME}"
    concepts: tuple[Concept, ...]

    def by_name(self) -> dict[str, Concept]:
        return {c.name: c for c in self.concepts}

    def concept(self, name: str) -> Concept:
        try:
            return self.by_name()[name]
        except KeyError:
            raise InventoryError(f"unknown concept {name!r}") from None

    def for_step(self, step_name: str) -> Concept:
        """The concept behind a DAG step name ``concept.<group>.<name>``."""
        parts = step_name.split(".")
        if len(parts) != 3 or parts[0] != STEP_PREFIX:
            raise InventoryError(f"{step_name!r} is not a concept step name")
        concept = self.concept(parts[2])
        if concept.group != parts[1]:
            raise InventoryError(f"{step_name!r}: concept {parts[2]} belongs to {concept.group}")
        return concept


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def concepts_root() -> Path:
    """``vendor/mimic-code/mimic-iv/concepts_duckdb`` inside the installed package."""
    return vendor_root() / TREE_DIRNAME / Path(*CONCEPTS_DIR.split("/"))


def inventory_path() -> Path:
    """``concepts/concepts.yaml`` beside this module (generated, committed)."""
    return Path(__file__).resolve().parent / INVENTORY_FILENAME


def spec_path() -> Path:
    """``dag/specs/concepts.yaml`` (generated, committed)."""
    from mimicwarehouse.dag.spec import specs_root

    return specs_root() / f"{SPEC_NAME}.yaml"


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def strip_comments(sql: str) -> str:
    """``sql`` without ``--`` line comments and ``/* */`` blocks (so a reference in a
    comment never becomes a dependency)."""
    return _LINE_COMMENT.sub("", _BLOCK_COMMENT.sub("", sql))


def scan_references(sql: str) -> tuple[set[str], set[str]]:
    """``(derived names, core schema.table names)`` referenced by ``sql`` (comments
    stripped; lower-cased)."""
    text = strip_comments(sql)
    derived = {m.group(1).lower() for m in _DERIVED_REF.finditer(text)}
    core = {f"{m.group(1).lower()}.{m.group(2).lower()}" for m in _CORE_REF.finditer(text)}
    return derived, core


def driver_order(root: Path | None = None) -> list[str]:
    """``<group>/<name>`` in the order the upstream ``duckdb.sql`` driver ``.read``\\ s
    them (the fallback order of the brief)."""
    driver = (root or concepts_root()) / DRIVER_FILENAME
    text = driver.read_text(encoding="utf-8")
    return [m.group(1).removesuffix(".sql") for m in _READ_LINE.finditer(text)]


def topological_order(concepts: Sequence[Concept]) -> tuple[Concept, ...]:
    """Kahn's algorithm over ``depends_on``; among ready concepts the smallest
    ``driver_order`` runs first (deterministic; upstream's order where the graph
    allows). Raises :class:`InventoryError` on a cycle or an unknown dependency."""
    by_name = {c.name: c for c in concepts}
    for c in concepts:
        unknown = sorted(set(c.depends_on) - set(by_name))
        if unknown:
            raise InventoryError(f"{c.name}: depends on unknown concept(s) {unknown}")
    remaining = {c.name: set(c.depends_on) for c in concepts}
    out: list[Concept] = []
    while remaining:
        ready = [n for n, deps in remaining.items() if not deps]
        if not ready:
            cyclic = sorted(remaining)
            raise InventoryError(f"dependency cycle among concepts {cyclic}")
        ready.sort(key=lambda n: (by_name[n].driver_order, n))
        chosen = ready[0]
        out.append(by_name[chosen])
        del remaining[chosen]
        for deps in remaining.values():
            deps.discard(chosen)
    return tuple(out)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def discover(root: Path | None = None) -> Inventory:
    """Walk the vendored tree and build the :class:`Inventory` (module docstring).
    Every ``<group>/<name>.sql`` below ``root`` is a concept; the driver file is not.
    References are validated: a derived reference must name a discovered concept, a
    core reference a hosp/icu contract table."""
    from mimicwarehouse.schema.contract import load_contract

    root = root or concepts_root()
    if not root.is_dir():
        raise InventoryError(f"vendored concepts not found: {root} (EP-8 vendoring)")
    order = {rel: i for i, rel in enumerate(driver_order(root))}
    pin = vendor_info()
    contract = load_contract()
    found: list[tuple[str, str, Path]] = []
    for group_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        for sql_path in sorted(group_dir.glob("*.sql")):
            found.append((group_dir.name, sql_path.stem, sql_path))
    if not found:
        raise InventoryError(f"no <group>/<concept>.sql files under {root}")
    names = [name for _, name, _ in found]
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        raise InventoryError(f"concept names are not unique across groups: {dupes}")
    concepts: list[Concept] = []
    for group, name, sql_path in found:
        if not _NAME.match(name) or not _NAME.match(group):
            raise InventoryError(f"{group}/{name}: not a lower [a-z0-9_] name")
        data = sql_path.read_bytes()
        derived, core = scan_references(data.decode("utf-8"))
        derived.discard(name)  # the file's own DROP/CREATE header
        unknown_core = sorted(qn for qn in core if not contract.has_table(qn))
        if unknown_core:
            raise InventoryError(f"{group}/{name}: unknown core table(s) {unknown_core}")
        rel = f"{group}/{name}"
        if rel not in order:
            raise InventoryError(f"{rel}: not read by the upstream driver {DRIVER_FILENAME}")
        concepts.append(
            Concept(
                name=name,
                group=group,
                path=f"{CONCEPTS_DIR}/{rel}.sql",
                sql_sha256=_sha256(data),
                upstream_commit=pin.sha,
                depends_on=tuple(sorted(derived)),
                sources=tuple(sorted(core)),
                driver_order=order[rel],
            )
        )
    missing = sorted(set(order) - {f"{c.group}/{c.name}" for c in concepts})
    if missing:
        raise InventoryError(f"driver reads file(s) that are not vendored: {missing}")
    return Inventory(upstream_commit=pin.sha, concepts=topological_order(concepts))


# ---------------------------------------------------------------------------
# Rendering (the two generated files + the docs table)
# ---------------------------------------------------------------------------

_INVENTORY_HEADER = (
    "# concepts/concepts.yaml -- the mimic-code concept inventory (EP-37 item 1; D-19).\n"
    "# GENERATED by `uv run python -m mimicwarehouse.concepts.inventory` from the vendored\n"
    "# mimic-iv/concepts_duckdb tree at the EP-8 pin; do not edit by hand (test_ep37 drift-\n"
    "# tests it). Execution order = topological over depends_on, ties by the upstream\n"
    "# duckdb.sql driver order. sql_sha256 = sha256 of the vendored (LF) bytes.\n"
)
_SPEC_HEADER = (
    "# dag/specs/concepts.yaml -- the concept DAG (EP-37 item 2; ASCII only).\n"
    "# GENERATED by `uv run python -m mimicwarehouse.concepts.inventory` from\n"
    "# concepts/concepts.yaml; do not edit by hand (test_ep37 drift-tests it). One python\n"
    "# step per vendored concept (callable concepts.runner:run_concept; target = the\n"
    "# status.json key so the runner skips/resumes it per tier), the meta.concept_versions\n"
    "# step after every concept, and the shared catalog step that load_dag() merges with\n"
    "# the stage spec's (depends_on / tags unioned). Run: mwh build --tier <t> --tag concepts\n"
    "# or --select concept.<group>.<name> [--with-deps]; --keep-going records failures.\n"
)


def render_inventory(inventory: Inventory) -> dict[str, Any]:
    """The ``concepts.yaml`` document (plain types, stable key order)."""
    return {
        "version": inventory.version,
        "upstream_commit": inventory.upstream_commit,
        "concepts_dir": inventory.concepts_dir,
        "driver": inventory.driver,
        "concepts": [
            {
                "name": c.name,
                "group": c.group,
                "path": c.path,
                "sql_sha256": c.sql_sha256,
                "depends_on": list(c.depends_on),
                "sources": list(c.sources),
                "driver_order": c.driver_order,
            }
            for c in inventory.concepts
        ],
    }


def _stage_step(qualified: str) -> str:
    return f"stage.{qualified}"


def render_spec(inventory: Inventory) -> dict[str, Any]:
    """The ``dag/specs/concepts.yaml`` document (module docstring)."""
    by_name = inventory.by_name()
    steps: list[dict[str, Any]] = []
    for c in inventory.concepts:
        depends = [_stage_step(qn) for qn in c.sources]
        depends += [by_name[d].step_name for d in c.depends_on]
        steps.append(
            {
                "name": c.step_name,
                "kind": "python",
                "callable": CONCEPT_CALLABLE,
                "target": c.qualified_name,
                "tags": [TAG, c.group],
                "tiers": list(TIERS),
                "depends_on": depends,
            }
        )
    concept_steps = [c.step_name for c in inventory.concepts]
    steps.append(
        {
            "name": VERSIONS_STEP,
            "kind": "python",
            "callable": VERSIONS_CALLABLE,
            "tags": [TAG, "meta"],
            "tiers": list(TIERS),
            "depends_on": concept_steps,
        }
    )
    steps.append(
        {
            "name": CATALOG_STEP,
            "kind": "catalog",
            "tags": ["catalog", TAG],
            "depends_on": [*concept_steps, VERSIONS_STEP],
        }
    )
    return {"version": 1, "steps": steps}


def dump_yaml(doc: dict[str, Any], header: str) -> str:
    """ASCII YAML with the generator header (block style, key order kept, LF)."""
    body = yaml.safe_dump(doc, sort_keys=False, allow_unicode=False, default_flow_style=False)
    return header + body


def write_inventory(inventory: Inventory | None = None, path: Path | None = None) -> Path:
    inventory = inventory or discover()
    target = path or inventory_path()
    target.write_text(
        dump_yaml(render_inventory(inventory), _INVENTORY_HEADER), encoding="utf-8", newline="\n"
    )
    return target


def write_spec(inventory: Inventory | None = None, path: Path | None = None) -> Path:
    inventory = inventory or discover()
    target = path or spec_path()
    text = dump_yaml(render_spec(inventory), _SPEC_HEADER)
    target.write_text(text, encoding="utf-8", newline="\n")
    return target


def render_inventory_table(inventory: Inventory) -> str:
    """The Markdown inventory table of ``docs/resources/concepts.md`` (generated block):
    order · concept · group · reads (concepts) · sources (core tables) · sql sha256
    (12 hex). Text only — no counts."""
    lines = [
        "| # | concept | group | reads (concepts) | sources (core tables) | sql sha256 |",
        "|---:|---|---|---|---|---|",
    ]
    for i, c in enumerate(inventory.concepts, start=1):
        reads = ", ".join(f"`{d}`" for d in c.depends_on) or "-"
        sources = ", ".join(f"`{s}`" for s in c.sources) or "-"
        lines.append(
            f"| {i} | `{c.name}` | {c.group} | {reads} | {sources} | `{c.sql_sha256[:12]}` |"
        )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Loading + drift
# ---------------------------------------------------------------------------


def load_inventory_from(path: Path) -> Inventory:
    """Parse one inventory YAML; the file carries ``upstream_commit`` once at the top
    level and each concept record inherits it."""
    doc = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or not isinstance(doc.get("concepts"), list):
        raise InventoryError(f"{path}: expected a mapping with a 'concepts' list")
    commit = doc.get("upstream_commit")
    doc = {
        **doc,
        "concepts": [
            {"upstream_commit": commit, **c} if isinstance(c, dict) else c for c in doc["concepts"]
        ],
    }
    return Inventory.model_validate(doc)


@cache
def load_inventory() -> Inventory:
    """The committed ``concepts/concepts.yaml`` (cached per process; what the runner
    executes). Raises :class:`InventoryError` when it is missing — regenerate with
    ``python -m mimicwarehouse.concepts.inventory``."""
    path = inventory_path()
    if not path.is_file():
        raise InventoryError(f"{path} missing — run python -m mimicwarehouse.concepts.inventory")
    return load_inventory_from(path)


def drift(committed: Inventory, fresh: Inventory) -> list[str]:
    """Human-readable differences between the committed inventory and a fresh
    :func:`discover` (empty = in sync)."""
    out: list[str] = []
    if committed.upstream_commit != fresh.upstream_commit:
        out.append("upstream_commit differs (re-vendored? regenerate the inventory)")
    old, new = committed.by_name(), fresh.by_name()
    for name in sorted(set(new) - set(old)):
        out.append(f"{name}: vendored but not in the inventory")
    for name in sorted(set(old) - set(new)):
        out.append(f"{name}: in the inventory but not vendored")
    for name in sorted(set(old) & set(new)):
        if old[name] != new[name]:
            out.append(f"{name}: record differs (sha / dependencies / order)")
    if [c.name for c in committed.concepts] != [c.name for c in fresh.concepts]:
        out.append("execution order differs")
    return out


def main(argv: Sequence[str] | None = None) -> int:
    """Regenerate ``concepts/concepts.yaml`` and ``dag/specs/concepts.yaml``."""
    args = list(sys.argv[1:] if argv is None else argv)
    check = "--check" in args
    fresh = discover()
    if check:
        problems = drift(load_inventory(), fresh)
        for p in problems:
            print(p)
        print(f"{len(fresh.concepts)} concept(s); {'drift' if problems else 'in sync'}")
        return 1 if problems else 0
    inv = write_inventory(fresh)
    spec = write_spec(fresh)
    print(f"wrote {inv} and {spec} ({len(fresh.concepts)} concept(s))")
    return 0


__all__ = [
    "CATALOG_STEP",
    "CONCEPTS_DIR",
    "CONCEPT_CALLABLE",
    "DERIVED_SCHEMA",
    "DRIVER_FILENAME",
    "INVENTORY_FILENAME",
    "SPEC_NAME",
    "STAGE_SCHEMAS",
    "STEP_PREFIX",
    "TAG",
    "TIERS",
    "VERSIONS_CALLABLE",
    "VERSIONS_STEP",
    "Concept",
    "Inventory",
    "InventoryError",
    "concepts_root",
    "discover",
    "drift",
    "driver_order",
    "dump_yaml",
    "inventory_path",
    "load_inventory",
    "load_inventory_from",
    "render_inventory",
    "render_inventory_table",
    "render_spec",
    "scan_references",
    "spec_path",
    "strip_comments",
    "topological_order",
    "write_inventory",
    "write_spec",
]

if __name__ == "__main__":
    sys.exit(main())
