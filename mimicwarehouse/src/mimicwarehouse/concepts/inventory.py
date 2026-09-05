"""Concept inventory — the vendored ``concepts_duckdb`` files as a typed, ordered graph
(EP-37 item 1; D-19, DESIGN §8).

:func:`scan_vendor` walks ``vendor/mimic-code/mimic-iv/concepts_duckdb/<group>/<concept>.sql``
(read, never moved — upstream-relative paths are what EP-38's patches diff against) into
one :class:`Concept` per file: name (the ``mimiciv_derived.<x>`` the file's header creates,
asserted equal to the file stem), group (the directory), upstream-relative path, the
file's ``sql_sha256``, the pinned ``upstream_commit`` from ``VENDOR.json``, the core tables
it reads (``mimiciv_hosp.*`` / ``mimiciv_icu.*`` references, comments stripped) and the
concepts it depends on (``mimiciv_derived.<y>`` references other than itself). Execution
order is upstream's ``duckdb.sql`` driver order (the ``.read`` lines) whenever it is a
valid topological order of that graph — it is at the pinned commit — and a
driver-order-tie-broken topological sort otherwise; a cycle is a hard error.

Two generated, committed files derive from the inventory and a test pins them to a fresh
scan (regenerate with ``uv run python -m mimicwarehouse.concepts.inventory``):

* ``concepts/concepts.yaml`` — the inventory itself (:func:`load_inventory` reads it, so a
  build never rescans the vendor tree);
* ``dag/specs/concepts.yaml`` — one ``python`` step ``concept.<group>.<name>`` per concept
  (``callable`` :data:`RUNNER_CALLABLE`, ``target`` ``mimiciv_derived.<name>``, tags
  ``concepts`` + ``concepts.<group>``, ``depends_on`` = the stage steps of the tables it
  reads plus its concept dependencies), the ``meta.concept_versions`` step after every
  concept, and the shared ``catalog`` step (tags ``catalog`` + ``concepts``) — merged into
  the stage spec by :func:`mimicwarehouse.dag.spec.load_dag` (EP-37 spec discovery).

:func:`render_markdown_table` / :func:`sync_doc` keep the human-readable inventory in
``docs/resources/concepts.md`` (concept · group · reads · depends on · upstream commit ·
status on DuckDB 1.5.x) in sync; :data:`KNOWN_FAILURES` is the place EP-38 records a
concept that fails on 1.5.x with its DuckDB error class (empty at EP-37: the EP-33 D2
smoke and this brief's demo/dev builds executed all 65). Import budget: stdlib + yaml +
pydantic + the vendor pin; no DuckDB (the runner imports it).
"""

from __future__ import annotations

import hashlib
import heapq
import re
from collections.abc import Mapping
from functools import cache
from importlib.resources import files
from pathlib import Path, PurePosixPath
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from mimicwarehouse.concepts import VendorInfo, vendor_info, vendored_path

#: Upstream-relative directory of the transpiled concepts (D-19).
CONCEPTS_TREE = "mimic-iv/concepts_duckdb"
#: Upstream's CLI driver; its ``.read`` lines are the make order.
DRIVER_FILENAME = "duckdb.sql"
#: The generated inventory (package data beside ``vendor/``).
INVENTORY_FILENAME = "concepts.yaml"
#: The generated DAG spec (``dag/specs/concepts.yaml``).
SPEC_FILENAME = "concepts.yaml"
#: The schema every concept creates into and the derived-layer status keys use.
DERIVED_SCHEMA = "mimiciv_derived"
#: Step naming: ``concept.<group>.<name>``; the versions and shared catalog steps.
STEP_PREFIX = "concept"
VERSIONS_STEP = "meta.concept_versions"
CATALOG_STEP = "catalog"
#: Tags: every concept step + the versions step + the shared catalog step carry
#: ``concepts``; a concept step also carries ``concepts.<group>``.
CONCEPTS_TAG = "concepts"
RUNNER_CALLABLE = "mimicwarehouse.concepts.runner:run_concept"
VERSIONS_CALLABLE = "mimicwarehouse.concepts.runner:run_concept_versions"
TIERS: tuple[str, ...] = ("fixture", "demo", "dev", "full")
#: Schemas a concept may read from the core lake (the stage spec's schemas).
CORE_SCHEMAS: tuple[str, ...] = ("mimiciv_hosp", "mimiciv_icu")
#: Concept name -> DuckDB error class, for concepts that fail on the pinned DuckDB
#: (docs/resources/concepts.md "status" column). Empty at EP-37; EP-38 records and fixes.
KNOWN_FAILURES: dict[str, str] = {}
#: The status text of a concept absent from :data:`KNOWN_FAILURES`.
STATUS_OK = "executes on DuckDB 1.5.5 (EP-33 D2 smoke; EP-37 demo + dev builds)"

DOC_RELPATH = Path("docs") / "resources" / "concepts.md"
DOC_MARK = ("<!-- concepts:begin -->", "<!-- concepts:end -->")

#: ``-- comment`` / ``DROP TABLE IF EXISTS mimiciv_derived.x; CREATE TABLE mimiciv_derived.x AS``
#: then the SELECT body (upstream generates exactly this; the regex tolerates whitespace and
#: case so a crafted or re-transpiled file with the same shape still parses).
HEADER_RE = re.compile(
    r"^\s*(?:--[^\n]*\n\s*)*"
    r"DROP\s+TABLE\s+IF\s+EXISTS\s+mimiciv_derived\.(?P<drop>[A-Za-z_][A-Za-z0-9_]*)\s*;\s*"
    r"CREATE\s+TABLE\s+mimiciv_derived\.(?P<create>[A-Za-z_][A-Za-z0-9_]*)\s+AS\s*"
    r"(?P<body>.*)$",
    re.IGNORECASE | re.DOTALL,
)
_REF_RE = re.compile(r"\bmimiciv_(hosp|icu|derived)\.([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE)
_COMMENT_RE = re.compile(r"--[^\n]*")
_READ_RE = re.compile(r"^\.read\s+(\S+)\s*$", re.MULTILINE)


class ConceptInventoryError(RuntimeError):
    """The vendored tree is not what the inventory expects (bad header, cycle, a file
    outside the driver, a stale generated file)."""


class Concept(BaseModel):
    """One vendored concept file and its place in the graph (module docstring)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(description="the mimiciv_derived table the file creates (= file stem)")
    group: str = Field(description="upstream directory: demographics, measurement, ...")
    path: str = Field(description="upstream-relative posix path of the vendored .sql")
    sql_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    upstream_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    reads: tuple[str, ...] = Field(default=(), description="core schema.table references")
    depends_on: tuple[str, ...] = Field(default=(), description="concept names read")
    order: int = Field(ge=0, description="execution order (driver order when topological)")

    @property
    def step_name(self) -> str:
        return f"{STEP_PREFIX}.{self.group}.{self.name}"

    @property
    def target(self) -> str:
        return f"{DERIVED_SCHEMA}.{self.name}"


class Inventory(BaseModel):
    """The ordered concept inventory (``concepts/concepts.yaml``)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    upstream_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    generated_by: str
    concepts: tuple[Concept, ...] = Field(min_length=1)

    @property
    def by_name(self) -> dict[str, Concept]:
        return {c.name: c for c in self.concepts}

    def concept(self, name: str) -> Concept:
        try:
            return self.by_name[name]
        except KeyError:
            raise ConceptInventoryError(
                f"unknown concept {name!r}; known: {sorted(self.by_name)}"
            ) from None

    @property
    def step_names(self) -> tuple[str, ...]:
        return tuple(c.step_name for c in self.concepts)

    @property
    def groups(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(c.group for c in self.concepts))


# ---------------------------------------------------------------------------
# Header / reference parsing (pure text)
# ---------------------------------------------------------------------------


def split_header(sql: str) -> tuple[str, str]:
    """``(table, select_body)`` of one concept file: strip upstream's ``DROP TABLE IF
    EXISTS mimiciv_derived.<x>; CREATE TABLE mimiciv_derived.<x> AS`` header (leading
    ``--`` comments included) and return the bare SELECT the runner sinks to Parquet.
    :class:`ConceptInventoryError` when the header is absent, names two different tables
    or leaves no body."""
    m = HEADER_RE.match(sql)
    if m is None:
        raise ConceptInventoryError(
            "not a concepts_duckdb file: expected 'DROP TABLE IF EXISTS mimiciv_derived.<x>; "
            "CREATE TABLE mimiciv_derived.<x> AS <select>'"
        )
    drop, create = m.group("drop").lower(), m.group("create").lower()
    if drop != create:
        raise ConceptInventoryError(f"header drops {drop!r} but creates {create!r}")
    body = m.group("body").strip().rstrip(";").strip()
    if not body:
        raise ConceptInventoryError(f"{create}: the header is not followed by a SELECT body")
    return create, body


def references(body: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """``(core reads, derived references)`` named in ``body`` (comments stripped, sorted,
    lower-cased): ``schema.table`` for the core schemas, bare table names for
    ``mimiciv_derived``."""
    text = _COMMENT_RE.sub("", body)
    core: set[str] = set()
    derived: set[str] = set()
    for schema, table in _REF_RE.findall(text):
        schema, table = schema.lower(), table.lower()
        if schema == "derived":
            derived.add(table)
        else:
            core.add(f"mimiciv_{schema}.{table}")
    return tuple(sorted(core)), tuple(sorted(derived))


def sql_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Scan
# ---------------------------------------------------------------------------


def concepts_root(vendor: VendorInfo | None = None) -> Path:
    """``vendor/mimic-code/mimic-iv/concepts_duckdb`` inside the package."""
    return (vendor or vendor_info()).tree / Path(*CONCEPTS_TREE.split("/"))


def driver_order(vendor: VendorInfo | None = None) -> list[str]:
    """The ``.read <group>/<file>.sql`` lines of upstream's ``duckdb.sql``, in order."""
    text = vendored_path(f"{CONCEPTS_TREE}/{DRIVER_FILENAME}").read_text(encoding="utf-8")
    return [m.group(1) for m in _READ_RE.finditer(text)]


def _topological_order(names: list[str], deps: Mapping[str, tuple[str, ...]]) -> list[str]:
    """Kahn's algorithm with the driver position as the tie-break: the driver order itself
    when it is topological, else the nearest valid order; a cycle raises."""
    position = {n: i for i, n in enumerate(names)}
    indegree = {n: 0 for n in names}
    dependents: dict[str, list[str]] = {n: [] for n in names}
    for n in names:
        for d in deps[n]:
            if d not in position:
                raise ConceptInventoryError(f"{n}: depends on unknown concept {d!r}")
            indegree[n] += 1
            dependents[d].append(n)
    ready = [position[n] for n in names if indegree[n] == 0]
    heapq.heapify(ready)
    out: list[str] = []
    while ready:
        n = names[heapq.heappop(ready)]
        out.append(n)
        for m in dependents[n]:
            indegree[m] -= 1
            if indegree[m] == 0:
                heapq.heappush(ready, position[m])
    if len(out) != len(names):
        stuck = sorted(n for n in names if indegree[n] > 0)
        raise ConceptInventoryError(f"dependency cycle among concepts {stuck}")
    return out


def scan_vendor(vendor: VendorInfo | None = None) -> Inventory:
    """Read every vendored concept file into an :class:`Inventory` (module docstring)."""
    vendor = vendor or vendor_info()
    root = concepts_root(vendor)
    if not root.is_dir():
        raise ConceptInventoryError(f"vendored concepts_duckdb tree missing: {root}")
    driver = driver_order(vendor)
    files_found = sorted(
        str(PurePosixPath(p.relative_to(root).as_posix()))
        for p in root.glob("*/*.sql")
        if p.is_file()
    )
    missing_from_driver = sorted(set(files_found) - set(driver))
    missing_files = sorted(set(driver) - set(files_found))
    if missing_from_driver or missing_files:
        raise ConceptInventoryError(
            f"driver/tree mismatch: not in {DRIVER_FILENAME}: {missing_from_driver}; "
            f"in the driver but not vendored: {missing_files}"
        )

    parsed: dict[str, dict[str, Any]] = {}
    names_in_driver: list[str] = []
    for rel in driver:
        path = root / Path(*rel.split("/"))
        text = path.read_text(encoding="utf-8")
        table, body = split_header(text)
        stem = PurePosixPath(rel).stem
        if table != stem:
            raise ConceptInventoryError(f"{rel}: creates mimiciv_derived.{table}, file stem {stem}")
        if table in parsed:
            raise ConceptInventoryError(f"{rel}: concept {table!r} appears twice")
        core, derived = references(body)
        for ref in core:
            schema = ref.split(".", 1)[0]
            if schema not in CORE_SCHEMAS:
                raise ConceptInventoryError(f"{rel}: reads unsupported schema {schema}")
        parsed[table] = {
            "group": PurePosixPath(rel).parent.name,
            "path": f"{CONCEPTS_TREE}/{rel}",
            "sha": sql_sha256(text),
            "reads": core,
            "deps": tuple(d for d in derived if d != table),
        }
        names_in_driver.append(table)

    order = _topological_order(names_in_driver, {n: parsed[n]["deps"] for n in names_in_driver})
    concepts = tuple(
        Concept(
            name=n,
            group=parsed[n]["group"],
            path=parsed[n]["path"],
            sql_sha256=parsed[n]["sha"],
            upstream_commit=vendor.sha,
            reads=parsed[n]["reads"],
            depends_on=parsed[n]["deps"],
            order=i,
        )
        for i, n in enumerate(order)
    )
    return Inventory(
        upstream_commit=vendor.sha,
        generated_by="mimicwarehouse.concepts.inventory (EP-37)",
        concepts=concepts,
    )


# ---------------------------------------------------------------------------
# Generated files
# ---------------------------------------------------------------------------


def inventory_path() -> Path:
    """``concepts/concepts.yaml`` inside the installed package."""
    return Path(str(files("mimicwarehouse.concepts").joinpath(INVENTORY_FILENAME)))


def spec_path() -> Path:
    """``dag/specs/concepts.yaml`` inside the installed package."""
    from mimicwarehouse.dag.spec import specs_root

    return specs_root() / SPEC_FILENAME


def _yaml_list(items: tuple[str, ...] | list[str], indent: int) -> list[str]:
    pad = " " * indent
    return [f"{pad}- {item}" for item in items]


def render_inventory_yaml(inv: Inventory) -> str:
    """The committed inventory (ASCII, hand-rendered so the layout is stable)."""
    lines = [
        "# concepts/concepts.yaml -- GENERATED by mimicwarehouse.concepts.inventory (EP-37).",
        "# Regenerate: uv run python -m mimicwarehouse.concepts.inventory   (never edit).",
        "# One entry per vendored mimic-iv/concepts_duckdb/<group>/<name>.sql, in execution",
        "# order (upstream duckdb.sql driver order, verified topological). depends_on names",
        "# the mimiciv_derived tables a concept reads; reads names its core tables.",
        f"upstream_commit: {inv.upstream_commit}",
        f"generated_by: {inv.generated_by}",
        "concepts:",
    ]
    for c in inv.concepts:
        lines.append(f"  - name: {c.name}")
        lines.append(f"    group: {c.group}")
        lines.append(f"    path: {c.path}")
        lines.append(f"    sql_sha256: {c.sql_sha256}")
        lines.append(f"    upstream_commit: {c.upstream_commit}")
        lines.append(f"    order: {c.order}")
        lines.append("    reads:" + (" []" if not c.reads else ""))
        lines.extend(_yaml_list(c.reads, 6))
        lines.append("    depends_on:" + (" []" if not c.depends_on else ""))
        lines.extend(_yaml_list(c.depends_on, 6))
    return "\n".join(lines) + "\n"


def stage_step_for(read: str) -> str:
    """``mimiciv_hosp.patients`` -> ``stage.mimiciv_hosp.patients``."""
    return f"stage.{read}"


def render_spec_yaml(inv: Inventory) -> str:
    """The committed ``dag/specs/concepts.yaml`` (module docstring)."""
    by_name = inv.by_name
    tiers = "[" + ", ".join(TIERS) + "]"
    lines = [
        "# dag/specs/concepts.yaml -- GENERATED by mimicwarehouse.concepts.inventory (EP-37).",
        "# Regenerate: uv run python -m mimicwarehouse.concepts.inventory   (never edit).",
        "# One python step per vendored concept (callable concepts.runner:run_concept, target =",
        "# the mimiciv_derived table it completes in status.json), depends_on = the stage steps",
        "# of the core tables it reads + its concept dependencies; meta.concept_versions after",
        "# every concept; the shared catalog step (merged with stage.yaml's by load_dag) last.",
        "# Tags: concepts (+ concepts.<group>) so `mwh build --tier <t> --tag concepts` runs the",
        "# concepts, the versions table and the catalog.",
        "version: 1",
        "steps:",
    ]
    for c in inv.concepts:
        deps = [stage_step_for(r) for r in c.reads] + [by_name[d].step_name for d in c.depends_on]
        lines.append(f"  - name: {c.step_name}")
        lines.append("    kind: python")
        lines.append(f"    callable: {RUNNER_CALLABLE}")
        lines.append(f"    target: {c.target}")
        lines.append(f"    tags: [{CONCEPTS_TAG}, {CONCEPTS_TAG}.{c.group}]")
        lines.append(f"    tiers: {tiers}")
        lines.append("    depends_on:" + (" []" if not deps else ""))
        lines.extend(_yaml_list(deps, 6))
    lines.append(f"  - name: {VERSIONS_STEP}")
    lines.append("    kind: python")
    lines.append(f"    callable: {VERSIONS_CALLABLE}")
    lines.append(f"    tags: [{CONCEPTS_TAG}]")
    lines.append(f"    tiers: {tiers}")
    lines.append("    depends_on:")
    lines.extend(_yaml_list(inv.step_names, 6))
    lines.append(f"  - name: {CATALOG_STEP}")
    lines.append("    kind: catalog")
    lines.append(f"    tags: [catalog, {CONCEPTS_TAG}]")
    lines.append("    depends_on:")
    lines.extend(_yaml_list((VERSIONS_STEP, *inv.step_names), 6))
    return "\n".join(lines) + "\n"


def generated_files(inv: Inventory | None = None) -> dict[Path, str]:
    """``{path: content}`` of both generated files for ``inv`` (a fresh scan by default)."""
    inv = inv or scan_vendor()
    return {inventory_path(): render_inventory_yaml(inv), spec_path(): render_spec_yaml(inv)}


def write_generated(inv: Inventory | None = None) -> list[Path]:
    """Write both generated files (LF, UTF-8/ASCII) and return their paths."""
    written: list[Path] = []
    for path, content in generated_files(inv).items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
        written.append(path)
    return written


def stale_generated(inv: Inventory | None = None) -> list[Path]:
    """The generated files whose committed content differs from a fresh render (the
    ``test_ep37`` pin; empty when everything is in sync)."""
    stale: list[Path] = []
    for path, content in generated_files(inv).items():
        current = path.read_text(encoding="utf-8") if path.is_file() else None
        if current != content:
            stale.append(path)
    return stale


@cache
def load_inventory() -> Inventory:
    """The committed inventory (validated; cached per process). The runner reads this,
    never the vendor tree — a stale file fails the ``test_ep37`` pin, not a build."""
    path = inventory_path()
    if not path.is_file():
        raise ConceptInventoryError(
            f"{path} missing — run `uv run python -m mimicwarehouse.concepts.inventory`"
        )
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        raise ConceptInventoryError(f"{path.name}: top level must be a mapping")
    return Inventory.model_validate(doc)


# ---------------------------------------------------------------------------
# docs/resources/concepts.md
# ---------------------------------------------------------------------------


def concept_status(name: str, failures: Mapping[str, str] | None = None) -> str:
    """The "status on DuckDB 1.5.x" cell: :data:`STATUS_OK`, or the recorded error class."""
    failures = KNOWN_FAILURES if failures is None else failures
    if name in failures:
        return f"fails: {failures[name]} (EP-38)"
    return STATUS_OK


def render_markdown_table(inv: Inventory, failures: Mapping[str, str] | None = None) -> str:
    """The inventory as one Markdown table (order · concept · group · reads · depends on ·
    upstream commit · status). ASCII; no integers besides the order column."""
    header = [
        "#",
        "concept",
        "group",
        "reads (core)",
        "depends on (derived)",
        "upstream",
        "status on DuckDB 1.5.x",
    ]
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    for c in inv.concepts:
        reads = ", ".join(f"`{r.split('.', 1)[1]}`" for r in c.reads) or "-"
        deps = ", ".join(f"`{d}`" for d in c.depends_on) or "-"
        lines.append(
            "| "
            + " | ".join(
                [
                    str(c.order + 1),
                    f"`{c.name}`",
                    c.group,
                    reads,
                    deps,
                    f"`{c.upstream_commit[:12]}`",
                    concept_status(c.name, failures),
                ]
            )
            + " |"
        )
    return "\n".join(lines) + "\n"


def doc_path() -> Path:
    from mimicwarehouse.config import workspace_root

    return workspace_root() / DOC_RELPATH


def sync_doc(path: Path | None = None, inv: Inventory | None = None) -> Path:
    """Re-render the generated block of ``docs/resources/concepts.md`` in place
    (idempotent); the narrative around the markers is never touched."""
    from mimicwarehouse.dag.benchmarks import replace_marked_block

    target = Path(path) if path is not None else doc_path()
    inv = inv or load_inventory()
    text = target.read_text(encoding="utf-8")
    text = replace_marked_block(
        text, render_markdown_table(inv), begin=DOC_MARK[0], end=DOC_MARK[1]
    )
    target.write_text(text.rstrip("\n") + "\n", encoding="utf-8", newline="\n")
    return target


__all__ = [
    "CATALOG_STEP",
    "CONCEPTS_TAG",
    "CONCEPTS_TREE",
    "CORE_SCHEMAS",
    "DERIVED_SCHEMA",
    "DOC_MARK",
    "DOC_RELPATH",
    "DRIVER_FILENAME",
    "HEADER_RE",
    "INVENTORY_FILENAME",
    "KNOWN_FAILURES",
    "RUNNER_CALLABLE",
    "SPEC_FILENAME",
    "STATUS_OK",
    "STEP_PREFIX",
    "TIERS",
    "VERSIONS_CALLABLE",
    "VERSIONS_STEP",
    "Concept",
    "ConceptInventoryError",
    "Inventory",
    "concept_status",
    "concepts_root",
    "doc_path",
    "driver_order",
    "generated_files",
    "inventory_path",
    "load_inventory",
    "references",
    "render_inventory_yaml",
    "render_markdown_table",
    "render_spec_yaml",
    "scan_vendor",
    "spec_path",
    "split_header",
    "sql_sha256",
    "stage_step_for",
    "stale_generated",
    "sync_doc",
    "write_generated",
]


if __name__ == "__main__":
    inventory = scan_vendor()
    for written in write_generated(inventory):
        print(written)
    if doc_path().is_file():
        print(sync_doc(inv=inventory))
    print(f"{len(inventory.concepts)} concepts at {inventory.upstream_commit[:12]}")
