"""Concept patches — local ports of upstream mimic-code fixes over the vendored concept
files (EP-38 item 2; D-19, DESIGN §8).

The vendored ``concepts_duckdb`` files are never edited (EP-8; the runner refuses a file
whose bytes drifted from the inventory). A *patch* is a **full replacement**
``concepts/patches/<concept>.sql`` — the vendored DuckDB body with one upstream fix
applied, under the same ``DROP TABLE … ; CREATE TABLE mimiciv_derived.<x> AS`` header
(so :func:`mimicwarehouse.concepts.runner.strip_header` applies) and a header comment
that cites the upstream PR / issue and keeps mimic-code's MIT attribution — plus one
entry in the registry ``concepts/patches/patches.yaml`` (:class:`Patch`: ``patch_id``,
``concept``, ``reason``, ``upstream_ref`` URL, ``related_refs``,
``applies_to_upstream_commit``, ``sql_sha256`` of the patch file, ``date``, ``status``
``ported-unmerged`` / ``ported-merged``, ``semantics``, ``demo_effect``).

The runner (:func:`mimicwarehouse.concepts.runner.run_concept`) prefers the patch over
the vendored file when the registry validates (:func:`check_registry`, once per build):
every entry names a vendored concept, its ``applies_to_upstream_commit`` equals the EP-8
pin (:func:`mimicwarehouse.concepts.vendor_info`), its file exists with exactly the
registered ``sql_sha256``, cites its ``upstream_ref`` and the MIT attribution, creates
the concept it claims, and adds no ``mimiciv_derived`` / core reference the vendored
file does not already make (the generated DAG spec stays valid); a ``.sql`` under
``patches/`` without an entry is an orphan. Any problem refuses **every** concept step
(:class:`PatchError`) — a re-vendor to a new commit therefore forces a review of each
patch before any concept builds again. ``meta.concept_versions`` records the
``patch_id`` and the executed SQL's sha256; the docs deviations table is rendered from
the registry (:func:`render_patch_table`).

Nothing here touches data: files, hashes, names and text only. Import budget: stdlib +
yaml + pydantic + :mod:`mimicwarehouse.concepts` (+ the inventory module) — never on the
``mwh --help`` path.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import sys
from collections.abc import Sequence
from importlib.resources import files
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from mimicwarehouse.concepts import vendor_info
from mimicwarehouse.concepts.inventory import (
    CATALOG_STEP,
    VERSIONS_STEP,
    Inventory,
    load_inventory,
    scan_references,
)

#: ``concepts/patches/`` — the registry and one ``<concept>.sql`` per patched concept.
PATCHES_DIRNAME = "patches"
REGISTRY_FILENAME = "patches.yaml"
REGISTRY_VERSION = 1
#: Every upstream reference is a URL under the mimic-code repository.
UPSTREAM_REPO_URL = "https://github.com/MIT-LCP/mimic-code/"
#: Every patch file keeps mimic-code's MIT attribution in its header comment.
LICENSE_MARK = "MIT License"
STATUSES: tuple[str, ...] = ("ported-unmerged", "ported-merged")
#: ``changes-values``: the port changes what the table holds on MIMIC-IV; ``intent-only``:
#: the port corrects the SQL's intent but produces identical rows on MIMIC-IV data
#: (the reason says why) — the crafted regression test still demonstrates the fix.
SEMANTICS: tuple[str, ...] = ("changes-values", "intent-only")

Status = Literal["ported-unmerged", "ported-merged"]
Semantics = Literal["changes-values", "intent-only"]


class PatchError(RuntimeError):
    """The patch registry (or one patch) is unusable — the concept build is refused."""


class Patch(BaseModel):
    """One registry entry (module docstring). Text and hashes only — never a value."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    patch_id: str = Field(pattern=r"^[a-z][a-z0-9]*(?:[_-][a-z0-9]+)*$")
    concept: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    reason: str = Field(min_length=1, description="what the port changes and why")
    upstream_ref: str = Field(pattern=r"^https://", description="the PR / issue / commit ported")
    related_refs: tuple[str, ...] = Field(default=(), description="further PRs / issues")
    applies_to_upstream_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    sql_sha256: str = Field(pattern=r"^[0-9a-f]{64}$", description="sha256 of <concept>.sql")
    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$", description="ISO date of the port")
    status: Status = "ported-unmerged"
    semantics: Semantics
    demo_effect: str = Field(
        min_length=1, description="effect on the committed demo count-pins (before -> after)"
    )

    @field_validator("date", mode="before")
    @classmethod
    def _date_as_iso_text(cls, value: Any) -> Any:
        """YAML resolves an unquoted ``2026-09-06`` to a date; keep the ISO text."""
        return value.isoformat() if isinstance(value, _dt.date) else value

    @property
    def file_name(self) -> str:
        return f"{self.concept}.sql"


class PatchRegistry(BaseModel):
    """The committed ``patches.yaml`` (empty when no patch exists)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int = Field(default=REGISTRY_VERSION, ge=1)
    patches: tuple[Patch, ...] = ()

    @property
    def concepts(self) -> tuple[str, ...]:
        """Patched concept names in registry order."""
        return tuple(p.concept for p in self.patches)

    def by_concept(self) -> dict[str, Patch]:
        return {p.concept: p for p in self.patches}

    def by_id(self) -> dict[str, Patch]:
        return {p.patch_id: p for p in self.patches}

    def for_concept(self, name: str) -> Patch | None:
        return self.by_concept().get(name)


# ---------------------------------------------------------------------------
# Paths + loading
# ---------------------------------------------------------------------------


def patches_root() -> Path:
    """``concepts/patches/`` inside the installed package (source tree or wheel)."""
    return Path(str(files(__package__).joinpath(PATCHES_DIRNAME)))


def registry_path(root: Path | None = None) -> Path:
    return (root or patches_root()) / REGISTRY_FILENAME


def patch_path(concept: str, root: Path | None = None) -> Path:
    """``concepts/patches/<concept>.sql``."""
    return (root or patches_root()) / f"{concept}.sql"


def sha256_of(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_registry_from(path: Path) -> PatchRegistry:
    """Parse one registry YAML (a missing or empty file is an empty registry)."""
    path = Path(path)
    if not path.is_file():
        return PatchRegistry()
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    if doc is None:
        return PatchRegistry()
    if not isinstance(doc, dict):
        raise PatchError(f"{path}: expected a mapping with a 'patches' list")
    try:
        return PatchRegistry.model_validate(doc)
    except ValidationError as exc:
        raise PatchError(f"{path}: {exc}") from None


def load_registry(root: Path | None = None) -> PatchRegistry:
    """The committed registry under ``root`` (default: the package's ``patches/``).
    Cheap (one small YAML), so it is read per call — the runner validates it once per
    build and keeps it in the build context."""
    return load_registry_from(registry_path(root))


def load_patch_sql(patch: Patch, root: Path | None = None) -> str:
    """The patch file's text, after checking its bytes against the registry's sha256."""
    path = patch_path(patch.concept, root)
    if not path.is_file():
        raise PatchError(f"{patch.patch_id}: {path.name} missing under {path.parent}")
    digest = sha256_of(path)
    if digest != patch.sql_sha256:
        raise PatchError(
            f"{patch.patch_id}: {path.name} sha256 {digest[:12]} differs from the registry's "
            f"{patch.sql_sha256[:12]} — edit the registry with the reviewed file's hash"
        )
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Validation (module docstring)
# ---------------------------------------------------------------------------


def validate_registry(
    registry: PatchRegistry,
    *,
    root: Path | None = None,
    pin: str | None = None,
    inventory: Inventory | None = None,
) -> list[str]:
    """Human-readable problems (empty = the registry is usable). ``pin`` defaults to the
    EP-8 vendored commit, ``inventory`` to the committed concept inventory."""
    from mimicwarehouse.concepts.runner import ConceptError, strip_header

    root = root or patches_root()
    pin = pin or vendor_info().sha
    inventory = inventory or load_inventory()
    known = inventory.by_name()
    problems: list[str] = []
    ids = [p.patch_id for p in registry.patches]
    for dup in sorted({i for i in ids if ids.count(i) > 1}):
        problems.append(f"{dup}: duplicate patch_id")
    concepts = [p.concept for p in registry.patches]
    for dup in sorted({c for c in concepts if concepts.count(c) > 1}):
        problems.append(f"{dup}: more than one patch for the concept (one full replacement each)")
    for p in registry.patches:
        concept = known.get(p.concept)
        if concept is None:
            problems.append(f"{p.patch_id}: unknown concept {p.concept!r} (not in the inventory)")
        if p.applies_to_upstream_commit != pin:
            problems.append(
                f"{p.patch_id}: applies_to_upstream_commit {p.applies_to_upstream_commit[:12]} "
                f"differs from the EP-8 pin {pin[:12]} — re-vendored? review the patch against "
                "the new upstream file, then update the registry entry"
            )
        for ref in (p.upstream_ref, *p.related_refs):
            if not ref.startswith(UPSTREAM_REPO_URL):
                problems.append(f"{p.patch_id}: reference {ref!r} is not under {UPSTREAM_REPO_URL}")
        path = patch_path(p.concept, root)
        if not path.is_file():
            problems.append(f"{p.patch_id}: {path.name} missing under {root}")
            continue
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if digest != p.sql_sha256:
            problems.append(
                f"{p.patch_id}: {path.name} sha256 {digest[:12]} != registry {p.sql_sha256[:12]}"
            )
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            problems.append(f"{p.patch_id}: {path.name} is not UTF-8")
            continue
        if p.upstream_ref not in text:
            problems.append(f"{p.patch_id}: {path.name} does not cite {p.upstream_ref}")
        if LICENSE_MARK not in text:
            problems.append(f"{p.patch_id}: {path.name} lacks the mimic-code MIT attribution")
        try:
            target, _body = strip_header(text)
        except ConceptError as exc:
            problems.append(f"{p.patch_id}: {path.name}: {exc}")
            continue
        if target != p.concept:
            problems.append(
                f"{p.patch_id}: {path.name} creates {target}, the registry says {p.concept}"
            )
        if concept is not None:
            derived, core = scan_references(text)
            derived.discard(p.concept)
            extra = sorted(derived - set(concept.depends_on)) + sorted(core - set(concept.sources))
            if extra:
                problems.append(
                    f"{p.patch_id}: {path.name} references {extra}, which the vendored file does "
                    "not — the generated DAG spec would miss the dependency"
                )
    if root.is_dir():
        entries = set(concepts)
        for sql in sorted(root.glob("*.sql")):
            if sql.stem not in entries:
                problems.append(f"{sql.name}: no registry entry (orphan patch file)")
    return problems


def check_registry(
    registry: PatchRegistry | None = None,
    *,
    root: Path | None = None,
    pin: str | None = None,
    inventory: Inventory | None = None,
) -> PatchRegistry:
    """:func:`validate_registry` or raise :class:`PatchError` listing every problem."""
    registry = registry if registry is not None else load_registry(root)
    problems = validate_registry(registry, root=root, pin=pin, inventory=inventory)
    if problems:
        raise PatchError(
            "concept patch registry refused (no concept builds until it is fixed):\n  "
            + "\n  ".join(problems)
        )
    return registry


# ---------------------------------------------------------------------------
# Rebuild selection + docs rendering
# ---------------------------------------------------------------------------


def rebuild_steps(
    inventory: Inventory, registry: PatchRegistry, *, with_dependents: bool = True
) -> list[str]:
    """The ``--select`` list that re-materialises the patched concepts: their steps (plus,
    by default, every concept that transitively reads one of them — its rows may change),
    in execution order, then ``meta.concept_versions`` and the shared ``catalog`` step."""
    names = list(registry.concepts)
    unknown = sorted(set(names) - set(inventory.by_name()))
    if unknown:
        raise PatchError(f"registry names unknown concept(s) {unknown}")
    closure = (
        inventory.dependents_of(names)
        if with_dependents
        else tuple(c.name for c in inventory.concepts if c.name in names)
    )
    return [inventory.concept(n).step_name for n in closure] + [VERSIONS_STEP, CATALOG_STEP]


def render_patch_table(registry: PatchRegistry) -> str:
    """The Markdown deviations table of ``docs/resources/concepts.md`` (generated block):
    one row per patch — id, concept, upstream reference(s), status, date, what changes,
    semantics, the effect on the demo count-pins, the patch file's sha256 (12 hex).
    Text only; the demo effect is the ODbL demo tier's committed pins."""
    lines = [
        "| patch id | concept | ported from | status | date | change | semantics | "
        "effect on demo counts | patch sha256 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    if not registry.patches:
        lines.append("| *(none)* | - | - | - | - | - | - | - | - |")
    for p in registry.patches:
        refs = ", ".join(f"<{r}>" for r in (p.upstream_ref, *p.related_refs))
        lines.append(
            f"| `{p.patch_id}` | `{p.concept}` | {refs} | {p.status} | {p.date} | "
            f"{p.reason} | {p.semantics} | {p.demo_effect} | `{p.sql_sha256[:12]}` |"
        )
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    """``python -m mimicwarehouse.concepts.patching [--check] [--select-list
    [--no-dependents]] [--table]``: validate the registry (always), print the
    ``--select`` list of a patched rebuild, or the docs table. Exit 1 on any problem."""
    args = list(sys.argv[1:] if argv is None else argv)
    registry = load_registry()
    problems = validate_registry(registry)
    if "--select-list" in args:
        print(
            ",".join(
                rebuild_steps(
                    load_inventory(), registry, with_dependents="--no-dependents" not in args
                )
            )
        )
    if "--table" in args:
        print(render_patch_table(registry), end="")
    for problem in problems:
        print(problem)
    print(f"{len(registry.patches)} patch(es); {'refused' if problems else 'ok'}")
    return 1 if problems else 0


def registry_summary(registry: PatchRegistry) -> dict[str, Any]:
    """``{concept: patch_id}`` — what the count-pins record beside the upstream commit."""
    return {p.concept: p.patch_id for p in registry.patches}


__all__ = [
    "LICENSE_MARK",
    "PATCHES_DIRNAME",
    "REGISTRY_FILENAME",
    "REGISTRY_VERSION",
    "SEMANTICS",
    "STATUSES",
    "UPSTREAM_REPO_URL",
    "Patch",
    "PatchError",
    "PatchRegistry",
    "check_registry",
    "load_patch_sql",
    "load_registry",
    "load_registry_from",
    "main",
    "patch_path",
    "patches_root",
    "rebuild_steps",
    "registry_path",
    "registry_summary",
    "render_patch_table",
    "sha256_of",
    "validate_registry",
]

if __name__ == "__main__":
    sys.exit(main())
