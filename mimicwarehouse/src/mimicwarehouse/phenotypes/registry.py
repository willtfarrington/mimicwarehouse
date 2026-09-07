"""The phenotype registry: packaged definitions + lock, reference resolution against the
EP-40 code-set registry, validation (EP-41 item 1; DESIGN §8, §15; GOVERNANCE §12).

* **Registry.** Every ``defs/*.yaml`` of the package (:func:`packaged_defs_dir`) plus any
  study directory passed by path is loaded into a :class:`Registry` of :class:`Entry`
  records keyed by ``id@version``. Loading resolves every code-set reference the leaves
  name against the code-set registry (the packaged seeds plus ``codeset_dirs``) to that
  set's ``def_hash`` (:func:`resolve_references`), resolves every vendored concept a
  ``concept`` leaf reads to a :class:`ConceptPin` — the sha256 of the SQL the concept
  runner executes for it (the EP-38 patch's when the concept is patched, else the
  vendored file's, from the committed inventory + patch registry; EP-42) — and computes
  the phenotype's ``def_hash`` from both
  (:meth:`~mimicwarehouse.phenotypes.spec.Phenotype.def_hash`), so a phenotype version
  pins the concept build it was defined against; the runner refuses to materialise over a
  tier whose concept was built from different SQL.
* **Lock.** A directory carries ``phenotypes.lock.json`` (written by ``mwh phenotype
  lock`` through :func:`fsio.atomic_write_text`): the record of every ``(id, version)``
  pair's ``def_hash``. **Immutability rule** (the EP-40 rule, same shape): loading a YAML
  whose hash differs from the recorded one raises :class:`PhenotypeFrozenError` — the
  definition, or a code set it references, changed without a version bump. A pair not yet
  in the lock loads as *unlocked* (``lock --check`` fails while a packaged definition is
  unlocked).
* **Validation** (:func:`validate`) checks what the schema cannot: the grain is available
  in ``timesem``'s registry, every referenced code set has the kind the leaf needs
  (``icd_dx`` for ``diagnosis``, ``icd_px`` for ``procedure``, ``drug`` for
  ``medication``, ``itemid`` for a ``lab`` code set), a ``lab`` leaf's declared unit
  equals the canonical unit of a curated itemid (EP-39), an ``inputevents`` medication
  leaf's set carries ICU itemids, and the compiled SQL is well-formed. No data access.

Everything here is definition text and hashes. Import budget: pydantic + yaml + stdlib
(``cli.py`` imports the ``phenotype`` sub-app at start-up); the units catalogue and
``timesem`` load inside function bodies.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import cache
from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from mimicwarehouse import fsio
from mimicwarehouse.codesets import registry as codesets_registry
from mimicwarehouse.codesets.spec import CodeSetError, UnknownCodeSetError, format_ref, parse_ref
from mimicwarehouse.phenotypes.spec import (
    Leaf,
    Phenotype,
    PhenotypeError,
    PhenotypeFrozenError,
    UnknownPhenotypeError,
    load_phenotype,
)

if TYPE_CHECKING:  # pragma: no cover
    from mimicwarehouse.codesets.registry import Registry as CodeSetRegistry
    from mimicwarehouse.concepts.inventory import Inventory
    from mimicwarehouse.concepts.patching import PatchRegistry

_LOG = logging.getLogger(__name__)

#: ``src/mimicwarehouse/phenotypes/defs/`` — the packaged definitions and their lock.
DEFS_DIRNAME = "defs"
LOCK_FILENAME = "phenotypes.lock.json"
#: The schema of the vendored concepts a ``concept`` leaf pins (EP-37/38).
CONCEPT_SCHEMA = "mimiciv_derived"
#: The code-set kind each leaf kind's ``codeset`` must have.
LEAF_CODESET_KINDS: dict[str, str] = {
    "diagnosis": "icd_dx",
    "procedure": "icd_px",
    "medication": "drug",
    "lab": "itemid",
}


def _today() -> str:
    return datetime.now(UTC).date().isoformat()


# ---------------------------------------------------------------------------
# The lock file
# ---------------------------------------------------------------------------


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class LockEntry(_Frozen):
    """One recorded ``(id, version)`` pair."""

    def_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    grain: str
    locked_at: str


class LockFile(_Frozen):
    """``phenotypes.lock.json``: ``{"version": 1, "phenotypes": {"<id>@<version>": {…}}}``."""

    version: int = 1
    phenotypes: dict[str, LockEntry] = Field(default_factory=dict)


def lock_path(defs_dir: Path | str) -> Path:
    return Path(defs_dir) / LOCK_FILENAME


def load_lock(defs_dir: Path | str) -> LockFile:
    """The directory's lock (an empty one when the file does not exist)."""
    path = lock_path(defs_dir)
    if not path.is_file():
        return LockFile()
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        return LockFile.model_validate(doc)
    except (OSError, ValueError, ValidationError) as exc:
        raise PhenotypeError(f"{path}: not a valid phenotype lock ({exc})") from exc


def write_lock(defs_dir: Path | str, lock: LockFile) -> Path:
    """Write the lock atomically (:func:`fsio.atomic_write_text`; sorted keys, LF)."""
    path = lock_path(defs_dir)
    payload = lock.model_dump(mode="json")
    payload["phenotypes"] = dict(sorted(payload["phenotypes"].items()))
    fsio.atomic_write_text(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return path


def check_lock(ref: str, def_hash: str, lock: LockFile, *, where: str) -> bool:
    """Whether ``ref`` is recorded in ``lock`` with the same hash; raises
    :class:`PhenotypeFrozenError` when the pair is recorded with a different hash."""
    entry = lock.phenotypes.get(ref)
    if entry is None:
        return False
    if entry.def_hash != def_hash:
        raise PhenotypeFrozenError(
            f"{where}: {ref} is frozen at def_hash {entry.def_hash[:12]} but now hashes "
            f"{def_hash[:12]} — the definition (or a code set it references) changed without "
            "a version bump; bump `version` (and lock the new pair) instead of editing a "
            "released version"
        )
    return True


# ---------------------------------------------------------------------------
# Reference resolution, entries, the registry
# ---------------------------------------------------------------------------


def resolve_references(phenotype: Phenotype, codesets: CodeSetRegistry) -> dict[str, str]:
    """``{id@version: def_hash}`` for every code set the phenotype's leaves name
    (:class:`PhenotypeError` names an unknown reference)."""
    resolved: dict[str, str] = {}
    for ref in phenotype.codeset_refs:
        try:
            resolved[ref] = codesets.get(ref).codeset.def_hash
        except UnknownCodeSetError as exc:
            raise PhenotypeError(f"{phenotype.ref}: {exc}") from None
        except CodeSetError as exc:
            raise PhenotypeError(f"{phenotype.ref}: reference {ref!r}: {exc}") from None
    return resolved


@dataclass(frozen=True, slots=True)
class ConceptPin:
    """How a phenotype pins one vendored concept it reads (EP-42): the DAG step that
    builds it and the sha256 of the SQL the concept runner executes — the EP-38 patch's
    when the concept is patched (``patch_id`` set), else the vendored file's. Hashes and
    names only, resolved from the committed inventory / patch registry (no data)."""

    table: str
    name: str
    step: str
    executed_sha256: str
    vendored_sha256: str
    patch_id: str | None
    upstream_commit: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "table": self.table,
            "step": self.step,
            "executed_sha256": self.executed_sha256,
            "vendored_sha256": self.vendored_sha256,
            "patch_id": self.patch_id,
            "upstream_commit": self.upstream_commit,
        }


@cache
def _concept_catalog() -> tuple[Inventory, PatchRegistry]:
    """The committed concept inventory and patch registry, loaded once per process
    (definition text; the concept modules stay off the ``mwh --help`` path)."""
    from mimicwarehouse.concepts import patching
    from mimicwarehouse.concepts.inventory import load_inventory

    return load_inventory(), patching.load_registry()


def concept_pin(name: str) -> ConceptPin | None:
    """The :class:`ConceptPin` of ``mimiciv_derived.<name>``, or None when ``name`` is
    not a vendored concept (a study's own derived table is read unpinned; ``validate``
    warns). The seam tests monkeypatch to simulate a concept whose SQL moved."""
    from mimicwarehouse.concepts.inventory import InventoryError

    inventory, patches = _concept_catalog()
    try:
        concept = inventory.concept(name)
    except InventoryError:
        return None
    patch = patches.for_concept(name)
    return ConceptPin(
        table=concept.qualified_name,
        name=concept.name,
        step=concept.step_name,
        executed_sha256=patch.sql_sha256 if patch is not None else concept.sql_sha256,
        vendored_sha256=concept.sql_sha256,
        patch_id=patch.patch_id if patch is not None else None,
        upstream_commit=concept.upstream_commit,
    )


def resolve_concepts(phenotype: Phenotype) -> dict[str, ConceptPin]:
    """``{table: ConceptPin}`` for every ``mimiciv_derived.*`` concept table the
    phenotype's leaves read that the vendored inventory knows (module docstring)."""
    pins: dict[str, ConceptPin] = {}
    for table in phenotype.concept_tables:
        schema, _, name = table.partition(".")
        if schema != CONCEPT_SCHEMA:
            continue
        pin = concept_pin(name)
        if pin is not None:
            pins[table] = pin
    return pins


def concept_hashes(pins: dict[str, ConceptPin]) -> dict[str, str]:
    """The ``concept_hashes`` argument of :meth:`Phenotype.def_hash` from resolved pins."""
    return {table: pin.executed_sha256 for table, pin in pins.items()}


@dataclass(frozen=True, slots=True)
class Entry:
    """One registered phenotype: the parsed YAML, the resolved reference hashes, its
    ``def_hash``, where it came from, whether locked, and the concept pins (EP-42)."""

    phenotype: Phenotype
    resolved: dict[str, str]
    def_hash: str
    path: Path
    locked: bool
    packaged: bool
    concepts: dict[str, ConceptPin] = field(default_factory=dict)

    @property
    def ref(self) -> str:
        return self.phenotype.ref

    @property
    def concept_hashes(self) -> dict[str, str]:
        return concept_hashes(self.concepts)

    @property
    def display_path(self) -> str:
        """``defs/<file>`` for a packaged definition, the absolute posix path otherwise."""
        if self.packaged:
            return f"{DEFS_DIRNAME}/{self.path.name}"
        return self.path.resolve().as_posix()


class Registry:
    """The loaded phenotypes, addressable by ``id@version``, plus the code-set registry
    they were resolved against."""

    def __init__(self, entries: Iterable[Entry], codesets: CodeSetRegistry) -> None:
        self.entries: tuple[Entry, ...] = tuple(entries)
        self.codesets = codesets
        self._by_ref: dict[str, Entry] = {}
        for entry in self.entries:
            if entry.ref in self._by_ref:
                other = self._by_ref[entry.ref]
                raise PhenotypeError(
                    f"{entry.ref} is defined twice: {other.display_path} and {entry.display_path}"
                )
            self._by_ref[entry.ref] = entry

    def __len__(self) -> int:
        return len(self.entries)

    def __iter__(self) -> Iterator[Entry]:
        return iter(self.entries)

    def refs(self) -> tuple[str, ...]:
        return tuple(e.ref for e in self.entries)

    def ids(self) -> tuple[str, ...]:
        return tuple(sorted({e.phenotype.id for e in self.entries}))

    def versions_of(self, phenotype_id: str) -> tuple[str, ...]:
        return tuple(e.phenotype.version for e in self.entries if e.phenotype.id == phenotype_id)

    def get(self, ref: str) -> Entry:
        """The entry for ``id@version`` (:class:`UnknownPhenotypeError` names the known
        versions of that id, or says the id is unknown)."""
        try:
            phenotype_id, version = parse_ref(ref)
        except CodeSetError as exc:
            raise PhenotypeError(str(exc).replace("code-set", "phenotype")) from None
        entry = self._by_ref.get(format_ref(phenotype_id, version))
        if entry is not None:
            return entry
        versions = self.versions_of(phenotype_id)
        if versions:
            raise UnknownPhenotypeError(
                f"no phenotype {phenotype_id}@{version}; known versions of {phenotype_id}: "
                f"{', '.join(versions)}"
            )
        raise UnknownPhenotypeError(
            f"no phenotype {phenotype_id!r}; known ids: {', '.join(self.ids()) or '(none)'}"
        )

    def select(self, refs: Iterable[str]) -> Registry:
        """The sub-registry of ``refs`` (every one must exist), registry order."""
        wanted = {self.get(r).ref for r in refs}
        return Registry((e for e in self.entries if e.ref in wanted), self.codesets)


def packaged_defs_dir() -> Path:
    """``src/mimicwarehouse/phenotypes/defs/`` inside the installed package (tests
    monkeypatch this to point at crafted directories)."""
    return Path(str(files("mimicwarehouse.phenotypes").joinpath(DEFS_DIRNAME)))


def load_dir(
    defs_dir: Path | str, codesets: CodeSetRegistry, *, packaged: bool = False
) -> list[Entry]:
    """Every ``*.yaml`` of ``defs_dir`` (sorted by name), resolved against ``codesets``
    and checked against the directory's lock; :class:`PhenotypeError` for a malformed file
    or an unknown reference, :class:`PhenotypeFrozenError` for a frozen pair whose hash
    moved."""
    root = Path(defs_dir)
    if not root.is_dir():
        raise PhenotypeError(f"{root}: not a phenotype directory")
    lock = load_lock(root)
    entries: list[Entry] = []
    for path in sorted(root.glob("*.yaml")):
        phenotype = load_phenotype(path)
        resolved = resolve_references(phenotype, codesets)
        concepts = resolve_concepts(phenotype)
        def_hash = phenotype.def_hash(resolved, concept_hashes(concepts))
        locked = check_lock(phenotype.ref, def_hash, lock, where=path.name)
        entries.append(
            Entry(
                phenotype=phenotype,
                resolved=resolved,
                def_hash=def_hash,
                path=path,
                locked=locked,
                packaged=packaged,
                concepts=concepts,
            )
        )
    return entries


def load_registry(
    extra_dirs: Iterable[Path | str] = (),
    *,
    codeset_dirs: Iterable[Path | str] = (),
    codesets: CodeSetRegistry | None = None,
) -> Registry:
    """The packaged definitions plus every ``extra_dirs`` directory, resolved against the
    code-set registry (the packaged seeds + ``codeset_dirs``, or ``codesets`` when given).
    A code-set :class:`~mimicwarehouse.codesets.spec.CodeSetFrozenError` propagates
    unchanged — a frozen code set refuses the phenotypes that read it."""
    if codesets is None:
        codesets = codesets_registry.load_registry(codeset_dirs)
    entries = load_dir(packaged_defs_dir(), codesets, packaged=True)
    for extra in extra_dirs:
        entries.extend(load_dir(Path(extra), codesets))
    return Registry(entries, codesets)


@dataclass(frozen=True, slots=True)
class LockResult:
    path: Path
    added: tuple[str, ...]
    unchanged: tuple[str, ...]


def lock_dir(
    defs_dir: Path | str,
    *,
    codeset_dirs: Iterable[Path | str] = (),
    codesets: CodeSetRegistry | None = None,
) -> LockResult:
    """Record every not-yet-locked ``(id, version)`` of ``defs_dir`` in its lock (a frozen
    pair whose hash moved refuses first, as :func:`load_dir` does); the file is rewritten
    only when something was added."""
    root = Path(defs_dir)
    if codesets is None:
        codesets = codesets_registry.load_registry(codeset_dirs)
    entries = load_dir(root, codesets)
    lock = load_lock(root)
    phenotypes = dict(lock.phenotypes)
    added: list[str] = []
    unchanged: list[str] = []
    for entry in entries:
        if entry.ref in phenotypes:
            unchanged.append(entry.ref)
            continue
        phenotypes[entry.ref] = LockEntry(
            def_hash=entry.def_hash, grain=entry.phenotype.grain, locked_at=_today()
        )
        added.append(entry.ref)
    path = lock_path(root)
    if added:
        path = write_lock(root, LockFile(version=lock.version, phenotypes=phenotypes))
    return LockResult(path=path, added=tuple(added), unchanged=tuple(unchanged))


# ---------------------------------------------------------------------------
# Validation (no data access)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Validation:
    """``mwh phenotype validate``'s result: problems (empty = valid), warnings, the
    leaves with the source tables they read, and the compiled SQL when it compiled."""

    entry: Entry
    problems: tuple[str, ...]
    warnings: tuple[str, ...]
    sources: tuple[str, ...]
    sql: str | None

    @property
    def ok(self) -> bool:
        return not self.problems

    def to_dict(self) -> dict[str, Any]:
        p = self.entry.phenotype
        return {
            "ref": p.ref,
            "grain": p.grain,
            "def_hash": self.entry.def_hash,
            "references": dict(self.entry.resolved),
            "concepts": {t: pin.to_dict() for t, pin in sorted(self.entry.concepts.items())},
            "parameters": dict(p.parameters),
            "evidence": [e.name for e in p.evidence_columns],
            "leaves": [
                {"id": leaf.id, "kind": leaf.kind, "negated": neg} for leaf, neg in p.all_leaves()
            ],
            "sources": list(self.sources),
            "problems": list(self.problems),
            "warnings": list(self.warnings),
            "ok": self.ok,
        }


def _leaf_problems(
    phenotype: Phenotype, leaf: Leaf, codesets: CodeSetRegistry
) -> tuple[list[str], list[str]]:
    """Problems / warnings of one leaf (and, recursively, a temporal leaf's operands)."""
    problems: list[str] = []
    warnings: list[str] = []
    payload = leaf.payload
    if leaf.kind == "temporal":
        for operand in (payload.a, payload.b):
            sub_p, sub_w = _leaf_problems(phenotype, operand, codesets)
            problems.extend(sub_p)
            warnings.extend(sub_w)
        return problems, warnings
    ref = getattr(payload, "codeset", None)
    codeset = None
    if ref:
        try:
            codeset = codesets.get(ref).codeset
        except CodeSetError as exc:
            problems.append(f"leaf {leaf.id}: {exc}")
            return problems, warnings
        wanted = LEAF_CODESET_KINDS.get(leaf.kind)
        if wanted is not None and codeset.kind != wanted:
            problems.append(
                f"leaf {leaf.id}: {ref} is a {codeset.kind} set; a {leaf.kind} leaf needs {wanted}"
            )
    if (
        leaf.kind == "medication"
        and payload.source == "inputevents"
        and codeset is not None
        and not codeset.members.itemids
    ):
        problems.append(
            f"leaf {leaf.id}: source inputevents needs a drug set with ICU itemids "
            f"({ref} carries none)"
        )
    if leaf.kind == "lab":
        from mimicwarehouse.units import load_catalogue, normalize_unit

        itemids = list(payload.itemids)
        if codeset is not None:
            itemids = list(codeset.members.itemids)
        catalogue = load_catalogue()
        curated = {item.itemid: item for item in catalogue.items}
        for itemid in itemids:
            item = curated.get(itemid)
            if item is None:
                warnings.append(
                    f"leaf {leaf.id}: itemid {itemid} is not in the EP-39 item catalogue — "
                    "valuenum is compared as recorded (no unit harmonisation)"
                )
                continue
            if payload.unit is not None and normalize_unit(payload.unit) != normalize_unit(
                item.canonical_unit
            ):
                problems.append(
                    f"leaf {leaf.id}: unit {payload.unit!r} differs from the canonical unit "
                    f"{item.canonical_unit!r} of curated itemid {itemid}"
                )
        if payload.unit is None:
            warnings.append(f"leaf {leaf.id}: no unit declared for the threshold")
    if leaf.kind == "concept":
        schema, _, name = payload.table.partition(".")
        if schema != CONCEPT_SCHEMA:
            warnings.append(
                f"leaf {leaf.id}: concept table {payload.table} is outside {CONCEPT_SCHEMA}"
            )
        elif concept_pin(name) is None:
            warnings.append(
                f"leaf {leaf.id}: {payload.table} is not a vendored concept — its build is "
                "not pinned by the phenotype hash"
            )
    return problems, warnings


def validate(entry: Entry, codesets: CodeSetRegistry) -> Validation:
    """Validate one entry beyond the schema (module docstring) and compile its SQL."""
    from mimicwarehouse import timesem
    from mimicwarehouse.phenotypes.compiler import CompileError, compile_phenotype

    phenotype = entry.phenotype
    problems: list[str] = []
    warnings: list[str] = []
    try:
        grain = timesem.grain(phenotype.grain)
        if not grain.available:
            problems.append(
                f"grain {phenotype.grain} is a placeholder until {grain.available_from}"
            )
    except timesem.GrainError as exc:
        problems.append(str(exc))
    for leaf, _neg in phenotype.criteria.leaves():
        leaf_p, leaf_w = _leaf_problems(phenotype, leaf, codesets)
        problems.extend(leaf_p)
        warnings.extend(leaf_w)
    sql: str | None = None
    sources: tuple[str, ...] = ()
    if not problems:
        try:
            compiled = compile_phenotype(
                phenotype, codesets, resolved=entry.resolved, concepts=entry.concept_hashes
            )
        except CompileError as exc:
            problems.append(f"compile: {exc}")
        else:
            sql = compiled.sql
            sources = compiled.sources
            warnings.extend(compiled.warnings)
    return Validation(
        entry=entry,
        problems=tuple(problems),
        warnings=tuple(dict.fromkeys(warnings)),
        sources=sources,
        sql=sql,
    )


__all__ = [
    "CONCEPT_SCHEMA",
    "DEFS_DIRNAME",
    "LEAF_CODESET_KINDS",
    "LOCK_FILENAME",
    "ConceptPin",
    "Entry",
    "LockEntry",
    "LockFile",
    "LockResult",
    "Registry",
    "Validation",
    "check_lock",
    "concept_hashes",
    "concept_pin",
    "load_dir",
    "load_lock",
    "load_registry",
    "lock_dir",
    "lock_path",
    "packaged_defs_dir",
    "resolve_concepts",
    "resolve_references",
    "validate",
    "write_lock",
]
