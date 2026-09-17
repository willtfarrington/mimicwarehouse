"""The cohort-spec registry: packaged specs + lock, reference resolution against the EP-40
code-set and EP-41 phenotype registries, static validation, the ``cohorts.specs`` DAG
step (``meta.cohort_specs``), the catalog extension and the docs renderer (EP-46 items 2
and 4; DESIGN §9, §15; GOVERNANCE §12; D-25).

* **Registry.** Every ``specs/*.yaml`` of the package (:func:`packaged_specs_dir`) plus any
  study directory passed by path (``studies/<study_id>/cohorts/`` under the data root in
  the brief's shorthand) is loaded into a :class:`Registry` of :class:`Entry` records keyed
  by ``id@version``. Loading resolves every code-set reference (``codeset`` criteria, the
  washout's set) against the code-set registry and every phenotype reference
  (``phenotype`` criteria, a ``phenotype_onset`` index) against the phenotype registry —
  each to that definition's ``def_hash`` (:func:`resolve_references`) — and computes the
  spec's ``def_hash`` from them (:meth:`~mimicwarehouse.cohort.spec.CohortSpec.def_hash`),
  so a spec version pins the definitions it was written against.
* **Lock.** A directory carries ``cohorts.lock.json`` (written by ``mwh cohort lock``
  through :func:`fsio.atomic_write_text`): the record of every ``(id, version)`` pair's
  ``def_hash``. **Immutability rule** (the EP-40 / EP-41 rule, same shape): loading a YAML
  whose hash differs from the recorded one raises :class:`CohortSpecFrozenError` — the
  definition, or a code set / phenotype it references, changed without a version bump. A
  pair not yet in the lock loads as *unlocked* (``lock --check`` fails while a packaged
  spec is unlocked).
* **Validation** (:func:`validate`) checks what the schema cannot: every ``codeset``
  reference is a dual ICD set (``icd_dx`` / ``icd_px``; the washout's set ``icd_dx``), a
  ``phenotype_onset`` index names a phenotype of the spec's grain (a ``phenotype``
  criterion of another grain warns — the compiler maps it), a ``data_availability`` table
  is a contract table with a time column (``itemids`` only where it has an ``itemid``
  column), a ``concept`` table outside the vendored inventory warns, and a ``custom_sql``
  criterion is named as flagged. No data access; the tier-level checks (references
  against ``meta.codesets`` / ``meta.phenotype_versions``, the degeneracy probe) live in
  :mod:`~mimicwarehouse.cohort.probe`.
* **The DAG step** ``cohorts.specs`` (``dag/specs/cohorts.yaml``, tag ``cohorts``;
  :func:`run_specs`) writes ``lake/meta/<tier>/cohort_specs.parquet`` — the registry
  index: id, version, def_hash, grain, index event, criterion counts, the custom flag,
  refs (JSON), outcome, locked, path — which EP-37's discovery walker registers as
  ``meta.cohort_specs`` (a ``meta.*`` registry read under ``safe_query``, no count column
  needed; EP-33 B1c — no second exemption mechanism, EP-46 amendment 2);
  :func:`register_cohorts` (a ``CATALOG_EXTENSIONS`` entry) comments it. EP-47's
  ``marts.cohorts`` (already in ``safe.REGISTRY_TABLES``) is the build registry beside it.

Everything written, printed or returned is definition text and hashes — never a
patient-level row (GOVERNANCE §4). Import budget: pydantic + yaml + stdlib plus the EP-40 /
EP-41 spec and registry modules (``cli.py`` imports the ``cohort`` sub-app at start-up);
duckdb, the runner, the contract and the concept modules load inside function bodies.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from mimicwarehouse import fsio
from mimicwarehouse.codesets import registry as codesets_registry
from mimicwarehouse.codesets.spec import CodeSetError, UnknownCodeSetError
from mimicwarehouse.cohort.spec import (
    CohortSpec,
    CohortSpecError,
    CohortSpecFrozenError,
    Criterion,
    UnknownCohortSpecError,
    criterion_text,
    format_ref,
    json_schema,
    load_spec,
    parse_ref,
)
from mimicwarehouse.phenotypes import registry as phenotypes_registry
from mimicwarehouse.phenotypes.spec import PhenotypeError, UnknownPhenotypeError

if TYPE_CHECKING:  # pragma: no cover
    import duckdb

    from mimicwarehouse.codesets.registry import Registry as CodeSetRegistry
    from mimicwarehouse.dag.runner import StepContext, StepOutcome
    from mimicwarehouse.dag.spec import Step
    from mimicwarehouse.phenotypes.registry import Registry as PhenotypeRegistry

_LOG = logging.getLogger(__name__)

#: ``src/mimicwarehouse/cohort/specs/`` — the packaged specs and their lock.
SPECS_DIRNAME = "specs"
LOCK_FILENAME = "cohorts.lock.json"
#: ``lake/meta/<tier>/cohort_specs.parquet`` -> ``meta.cohort_specs`` (EP-37 walker).
SPECS_TABLE = "cohort_specs"
#: The DAG step / tag (``dag/specs/cohorts.yaml``).
STEP_SPECS = "cohorts.specs"
DAG_TAG = "cohorts"
#: The code-set kinds a ``codeset`` criterion / the washout may reference.
CODESET_KINDS: tuple[str, ...] = ("icd_dx", "icd_px")
WASHOUT_CODESET_KINDS: tuple[str, ...] = ("icd_dx",)
#: The schema of the vendored concepts (EP-37/38) a ``concept`` criterion usually reads.
CONCEPT_SCHEMA = "mimiciv_derived"


def _today() -> str:
    return datetime.now(UTC).date().isoformat()


def _sql_str(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


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
    """``cohorts.lock.json``: ``{"version": 1, "cohorts": {"<id>@<version>": {…}}}``."""

    version: int = 1
    cohorts: dict[str, LockEntry] = Field(default_factory=dict)


def lock_path(specs_dir: Path | str) -> Path:
    return Path(specs_dir) / LOCK_FILENAME


def load_lock(specs_dir: Path | str) -> LockFile:
    """The directory's lock (an empty one when the file does not exist)."""
    path = lock_path(specs_dir)
    if not path.is_file():
        return LockFile()
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        return LockFile.model_validate(doc)
    except (OSError, ValueError, ValidationError) as exc:
        raise CohortSpecError(f"{path}: not a valid cohort-spec lock ({exc})") from exc


def write_lock(specs_dir: Path | str, lock: LockFile) -> Path:
    """Write the lock atomically (:func:`fsio.atomic_write_text`; sorted keys, LF)."""
    path = lock_path(specs_dir)
    payload = lock.model_dump(mode="json")
    payload["cohorts"] = dict(sorted(payload["cohorts"].items()))
    fsio.atomic_write_text(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return path


def check_lock(ref: str, def_hash: str, lock: LockFile, *, where: str) -> bool:
    """Whether ``ref`` is recorded in ``lock`` with the same hash; raises
    :class:`CohortSpecFrozenError` when the pair is recorded with a different hash."""
    entry = lock.cohorts.get(ref)
    if entry is None:
        return False
    if entry.def_hash != def_hash:
        raise CohortSpecFrozenError(
            f"{where}: {ref} is frozen at def_hash {entry.def_hash[:12]} but now hashes "
            f"{def_hash[:12]} — the definition (or a code set / phenotype it references) "
            "changed without a version bump; bump `version` (and lock the new pair) instead "
            "of editing a released version"
        )
    return True


# ---------------------------------------------------------------------------
# Reference resolution, entries, the registry
# ---------------------------------------------------------------------------


def resolve_references(
    spec: CohortSpec, codesets: CodeSetRegistry, phenotypes: PhenotypeRegistry
) -> dict[str, dict[str, str]]:
    """``{"codeset": {id@version: def_hash}, "phenotype": {id@version: def_hash}}`` for
    every reference the spec names (:class:`CohortSpecError` names an unknown one)."""
    resolved: dict[str, dict[str, str]] = {"codeset": {}, "phenotype": {}}
    for ref in spec.codeset_refs:
        try:
            resolved["codeset"][ref] = codesets.get(ref).codeset.def_hash
        except UnknownCodeSetError as exc:
            raise CohortSpecError(f"{spec.ref}: {exc}") from None
        except CodeSetError as exc:
            raise CohortSpecError(f"{spec.ref}: code-set reference {ref!r}: {exc}") from None
    for ref in spec.phenotype_refs:
        try:
            resolved["phenotype"][ref] = phenotypes.get(ref).def_hash
        except UnknownPhenotypeError as exc:
            raise CohortSpecError(f"{spec.ref}: {exc}") from None
        except PhenotypeError as exc:
            raise CohortSpecError(f"{spec.ref}: phenotype reference {ref!r}: {exc}") from None
    return resolved


@dataclass(frozen=True, slots=True)
class Entry:
    """One registered cohort spec: the parsed YAML, the resolved reference hashes, its
    ``def_hash``, where it came from, whether locked."""

    spec: CohortSpec
    resolved: dict[str, dict[str, str]]
    def_hash: str
    path: Path
    locked: bool
    packaged: bool

    @property
    def ref(self) -> str:
        return self.spec.ref

    @property
    def display_path(self) -> str:
        """``specs/<file>`` for a packaged spec, the absolute posix path otherwise."""
        if self.packaged:
            return f"{SPECS_DIRNAME}/{self.path.name}"
        return self.path.resolve().as_posix()

    @property
    def flat_refs(self) -> dict[str, str]:
        """``{"codeset:<ref>": hash, "phenotype:<ref>": hash}`` — one flat mapping."""
        out: dict[str, str] = {}
        for kind in ("codeset", "phenotype"):
            for ref, def_hash in sorted(self.resolved.get(kind, {}).items()):
                out[f"{kind}:{ref}"] = def_hash
        return out


class Registry:
    """The loaded cohort specs, addressable by ``id@version``, plus the code-set and
    phenotype registries they were resolved against."""

    def __init__(
        self, entries: Iterable[Entry], codesets: CodeSetRegistry, phenotypes: PhenotypeRegistry
    ) -> None:
        self.entries: tuple[Entry, ...] = tuple(entries)
        self.codesets = codesets
        self.phenotypes = phenotypes
        self._by_ref: dict[str, Entry] = {}
        for entry in self.entries:
            if entry.ref in self._by_ref:
                other = self._by_ref[entry.ref]
                raise CohortSpecError(
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
        return tuple(sorted({e.spec.id for e in self.entries}))

    def versions_of(self, spec_id: str) -> tuple[str, ...]:
        return tuple(e.spec.version for e in self.entries if e.spec.id == spec_id)

    def get(self, ref: str) -> Entry:
        """The entry for ``id@version`` (:class:`UnknownCohortSpecError` names the known
        versions of that id, or says the id is unknown)."""
        spec_id, version = parse_ref(ref)
        entry = self._by_ref.get(format_ref(spec_id, version))
        if entry is not None:
            return entry
        versions = self.versions_of(spec_id)
        if versions:
            raise UnknownCohortSpecError(
                f"no cohort spec {spec_id}@{version}; known versions of {spec_id}: "
                f"{', '.join(versions)}"
            )
        raise UnknownCohortSpecError(
            f"no cohort spec {spec_id!r}; known ids: {', '.join(self.ids()) or '(none)'}"
        )

    def select(self, refs: Iterable[str]) -> Registry:
        """The sub-registry of ``refs`` (every one must exist), registry order."""
        wanted = {self.get(r).ref for r in refs}
        return Registry(
            (e for e in self.entries if e.ref in wanted), self.codesets, self.phenotypes
        )


def packaged_specs_dir() -> Path:
    """``src/mimicwarehouse/cohort/specs/`` inside the installed package (tests monkeypatch
    this to point at crafted directories)."""
    return Path(str(files("mimicwarehouse.cohort").joinpath(SPECS_DIRNAME)))


def load_dir(
    specs_dir: Path | str,
    codesets: CodeSetRegistry,
    phenotypes: PhenotypeRegistry,
    *,
    packaged: bool = False,
) -> list[Entry]:
    """Every ``*.yaml`` of ``specs_dir`` (sorted by name), resolved against the two
    registries and checked against the directory's lock; :class:`CohortSpecError` for a
    malformed file or an unknown reference, :class:`CohortSpecFrozenError` for a frozen
    pair whose hash moved."""
    root = Path(specs_dir)
    if not root.is_dir():
        raise CohortSpecError(f"{root}: not a cohort-spec directory")
    lock = load_lock(root)
    entries: list[Entry] = []
    for path in sorted(root.glob("*.yaml")):
        spec = load_spec(path)
        resolved = resolve_references(spec, codesets, phenotypes)
        def_hash = spec.def_hash(resolved)
        locked = check_lock(spec.ref, def_hash, lock, where=path.name)
        entries.append(
            Entry(
                spec=spec,
                resolved=resolved,
                def_hash=def_hash,
                path=path,
                locked=locked,
                packaged=packaged,
            )
        )
    return entries


def load_registry(
    extra_dirs: Iterable[Path | str] = (),
    *,
    codeset_dirs: Iterable[Path | str] = (),
    phenotype_dirs: Iterable[Path | str] = (),
    codesets: CodeSetRegistry | None = None,
    phenotypes: PhenotypeRegistry | None = None,
) -> Registry:
    """The packaged specs plus every ``extra_dirs`` directory, resolved against the
    code-set registry (packaged seeds + ``codeset_dirs``) and the phenotype registry
    (packaged definitions + ``phenotype_dirs``), or the registries given. A frozen code
    set or phenotype (``CodeSetFrozenError`` / ``PhenotypeFrozenError``) propagates
    unchanged — it refuses the specs that read it."""
    if codesets is None:
        codesets = codesets_registry.load_registry(codeset_dirs)
    if phenotypes is None:
        phenotypes = phenotypes_registry.load_registry(phenotype_dirs, codesets=codesets)
    entries = load_dir(packaged_specs_dir(), codesets, phenotypes, packaged=True)
    for extra in extra_dirs:
        entries.extend(load_dir(Path(extra), codesets, phenotypes))
    return Registry(entries, codesets, phenotypes)


@dataclass(frozen=True, slots=True)
class LockResult:
    path: Path
    added: tuple[str, ...]
    unchanged: tuple[str, ...]


def lock_dir(
    specs_dir: Path | str,
    *,
    codeset_dirs: Iterable[Path | str] = (),
    phenotype_dirs: Iterable[Path | str] = (),
    codesets: CodeSetRegistry | None = None,
    phenotypes: PhenotypeRegistry | None = None,
) -> LockResult:
    """Record every not-yet-locked ``(id, version)`` of ``specs_dir`` in its lock (a frozen
    pair whose hash moved refuses first, as :func:`load_dir` does); the file is rewritten
    only when something was added."""
    root = Path(specs_dir)
    if codesets is None:
        codesets = codesets_registry.load_registry(codeset_dirs)
    if phenotypes is None:
        phenotypes = phenotypes_registry.load_registry(phenotype_dirs, codesets=codesets)
    entries = load_dir(root, codesets, phenotypes)
    lock = load_lock(root)
    cohorts = dict(lock.cohorts)
    added: list[str] = []
    unchanged: list[str] = []
    for entry in entries:
        if entry.ref in cohorts:
            unchanged.append(entry.ref)
            continue
        cohorts[entry.ref] = LockEntry(
            def_hash=entry.def_hash, grain=entry.spec.grain, locked_at=_today()
        )
        added.append(entry.ref)
    path = lock_path(root)
    if added:
        path = write_lock(root, LockFile(version=lock.version, cohorts=cohorts))
    return LockResult(path=path, added=tuple(added), unchanged=tuple(unchanged))


def spec_filename(spec: CohortSpec) -> str:
    """``<id>_<major>_<minor>_<patch>.yaml`` — the file name a saved spec gets."""
    return f"{spec.id}_{spec.version.replace('.', '_')}.yaml"


def save_spec(spec: CohortSpec, directory: Path | str, *, overwrite: bool = False) -> Path:
    """Write ``spec`` as YAML into ``directory`` (a study's ``cohorts/`` folder; the
    Cohort Builder's "save as new version", EP-62) through :func:`fsio.atomic_write_text`;
    an existing file is refused unless ``overwrite``. Locking is a separate, deliberate
    act (``mwh cohort lock --specs <dir>``)."""
    target = Path(directory) / spec_filename(spec)
    if target.exists() and not overwrite:
        raise CohortSpecError(f"{target}: exists (pass overwrite=True to replace it)")
    target.parent.mkdir(parents=True, exist_ok=True)
    return spec.save_yaml(target)


# ---------------------------------------------------------------------------
# Static validation (no data access)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Validation:
    """``mwh cohort validate``'s static result: problems (empty = valid) and warnings."""

    entry: Entry
    problems: tuple[str, ...]
    warnings: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.problems

    def to_dict(self) -> dict[str, Any]:
        spec = self.entry.spec
        return {
            "ref": spec.ref,
            "grain": spec.grain,
            "index_event": spec.index_event.render(),
            "def_hash": self.entry.def_hash,
            "references": {k: dict(v) for k, v in self.entry.resolved.items()},
            "criteria": [
                {"polarity": polarity, "label": c.label, "kind": c.kind, "custom": c.custom}
                for polarity, c in spec.criteria()
            ],
            "custom": spec.custom,
            "problems": list(self.problems),
            "warnings": list(self.warnings),
            "ok": self.ok,
        }


def _criterion_problems(
    spec: CohortSpec, polarity: str, criterion: Criterion, registry: Registry
) -> tuple[list[str], list[str]]:
    problems: list[str] = []
    warnings: list[str] = []
    where = f"{polarity} {criterion.label}"
    p = criterion.payload
    kind = criterion.kind
    if kind == "codeset":
        codeset = registry.codesets.get(p.ref).codeset
        if codeset.kind not in CODESET_KINDS:
            problems.append(
                f"{where}: {p.ref} is a {codeset.kind} set; a codeset criterion needs a billed "
                f"ICD set ({' / '.join(CODESET_KINDS)})"
            )
        if p.lookback != "same_admission" and spec.grain == "subject":
            warnings.append(
                f"{where}: lookback {p.lookback} on the subject grain counts admissions before "
                "the index admission of the rule"
            )
    elif kind == "phenotype":
        entry = registry.phenotypes.get(p.ref)
        if entry.phenotype.grain != spec.grain:
            warnings.append(
                f"{where}: phenotype {p.ref} has grain {entry.phenotype.grain}, the spec "
                f"{spec.grain} — the compiler maps it through the admission / subject (EP-47)"
            )
    elif kind == "concept":
        schema, _, name = p.table.partition(".")
        if schema == CONCEPT_SCHEMA and phenotypes_registry.concept_pin(name) is None:
            warnings.append(
                f"{where}: {p.table} is not a vendored concept — its build is not pinned by "
                "the spec hash"
            )
        elif schema not in (CONCEPT_SCHEMA, "mimiciv_hosp", "mimiciv_icu", "marts"):
            warnings.append(f"{where}: concept table {p.table} is outside the cataloged schemas")
    elif kind == "data_availability":
        from mimicwarehouse.schema.contract import load_contract

        contract = load_contract()
        try:
            table = contract.table(p.table)
        except (KeyError, ValueError):
            problems.append(f"{where}: {p.table} is not a contract table")
        else:
            if table.time_column is None:
                problems.append(
                    f"{where}: {p.table} has no time column, so a window cannot apply to it"
                )
            if p.itemids and "itemid" not in {c.name for c in table.columns}:
                problems.append(f"{where}: {p.table} has no itemid column (drop itemids)")
    elif kind == "los":
        if p.of == "icu" and spec.grain in ("subject", "hadm"):
            warnings.append(
                f"{where}: los of icu on the {spec.grain} grain is the index ICU stay's when the "
                "index rule yields one (first_icu_stay), else the compiler refuses (EP-47)"
            )
    elif kind == "custom_sql":
        warnings.append(
            f"{where}: custom_sql criterion (hash {p.hash[:12]}) — flagged custom in attrition "
            "and reports"
        )
    return problems, warnings


def validate(entry: Entry, registry: Registry) -> Validation:
    """Validate one entry beyond the schema (module docstring)."""
    spec = entry.spec
    problems: list[str] = []
    warnings: list[str] = []
    if spec.index_event.phenotype_onset is not None:
        onset = registry.phenotypes.get(spec.index_event.phenotype_onset).phenotype
        if onset.grain != spec.grain:
            problems.append(
                f"index_event: phenotype_onset {spec.index_event.phenotype_onset} has grain "
                f"{onset.grain}; a phenotype onset indexes units of its own grain ({spec.grain} "
                "expected)"
            )
    if spec.washout.codeset is not None:
        codeset = registry.codesets.get(spec.washout.codeset).codeset
        if codeset.kind not in WASHOUT_CODESET_KINDS:
            problems.append(
                f"washout: {spec.washout.codeset} is a {codeset.kind} set; the washout's set "
                f"is a diagnosis set ({' / '.join(WASHOUT_CODESET_KINDS)})"
            )
    if spec.washout.rule == "no_prior_icu" and spec.grain not in ("icustay", "icu_day", "hour_bin"):
        warnings.append(
            f"washout: no_prior_icu on the {spec.grain} grain counts ICU stays before the index"
        )
    for polarity, criterion in spec.criteria():
        c_problems, c_warnings = _criterion_problems(spec, polarity, criterion, registry)
        problems.extend(c_problems)
        warnings.extend(c_warnings)
    if not spec.inclusion and not spec.exclusion:
        warnings.append("no criteria: the cohort is the whole index population")
    return Validation(
        entry=entry, problems=tuple(problems), warnings=tuple(dict.fromkeys(warnings))
    )


# ---------------------------------------------------------------------------
# The DAG step: meta.cohort_specs
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SpecsOptions:
    """What a caller hands the step: extra spec / code-set / phenotype directories."""

    extra_dirs: tuple[Path, ...] = ()
    codeset_dirs: tuple[Path, ...] = ()
    phenotype_dirs: tuple[Path, ...] = ()


_OPTIONS: ContextVar[SpecsOptions | None] = ContextVar("mwh_cohorts_specs", default=None)


@contextmanager
def specs_options(
    *,
    extra_dirs: Iterable[Path | str] = (),
    codeset_dirs: Iterable[Path | str] = (),
    phenotype_dirs: Iterable[Path | str] = (),
) -> Iterator[SpecsOptions]:
    """Install :class:`SpecsOptions` for the duration of a runner call in this thread."""
    options = SpecsOptions(
        extra_dirs=tuple(Path(d) for d in extra_dirs),
        codeset_dirs=tuple(Path(d) for d in codeset_dirs),
        phenotype_dirs=tuple(Path(d) for d in phenotype_dirs),
    )
    token = _OPTIONS.set(options)
    try:
        yield options
    finally:
        _OPTIONS.reset(token)


def current_options() -> SpecsOptions:
    """The options installed by :func:`specs_options`, or the defaults."""
    return _OPTIONS.get() or SpecsOptions()


SPECS_COLUMNS: tuple[tuple[str, str], ...] = (
    ("cohort_id", "VARCHAR"),
    ("version", "VARCHAR"),
    ("def_hash", "VARCHAR"),
    ("grain", "VARCHAR"),
    ("index_event", "VARCHAR"),
    ("n_inclusion", "INTEGER"),
    ("n_exclusion", "INTEGER"),
    ("custom", "BOOLEAN"),
    ("refs", "VARCHAR"),
    ("outcome", "VARCHAR"),
    ("locked", "BOOLEAN"),
    ("path", "VARCHAR"),
    ("registered_at", "VARCHAR"),
    ("tier", "VARCHAR"),
    ("build_id", "VARCHAR"),
)


def specs_rows(
    registry: Registry, *, tier: str, build_id: str, registered_at: str
) -> list[list[Any]]:
    """The ``meta.cohort_specs`` rows for every entry, :data:`SPECS_COLUMNS` order."""
    rows: list[list[Any]] = []
    for entry in registry:
        spec = entry.spec
        rows.append(
            [
                spec.id,
                spec.version,
                entry.def_hash,
                spec.grain,
                spec.index_event.render(),
                len(spec.inclusion),
                len(spec.exclusion),
                spec.custom,
                json.dumps(entry.flat_refs, sort_keys=True, separators=(",", ":")),
                spec.follow_up.outcome,
                entry.locked,
                entry.display_path,
                registered_at,
                tier,
                build_id,
            ]
        )
    return rows


def specs_path(lake_root: Path | str, tier: str) -> Path:
    from mimicwarehouse.units import meta_table_path

    return meta_table_path(lake_root, tier, SPECS_TABLE)


def run_specs(step: Step, ctx: StepContext) -> StepOutcome:
    """The ``cohorts.specs`` handler (module docstring): the registry (packaged + the
    options' extra dirs; a frozen mismatch fails the step) -> one meta file. No data is
    read."""
    from mimicwarehouse.dag.runner import StepOutcome
    from mimicwarehouse.loader.manifest import utc_now_iso
    from mimicwarehouse.units import write_meta_parquet

    options = current_options()
    registry = load_registry(
        options.extra_dirs,
        codeset_dirs=options.codeset_dirs,
        phenotype_dirs=options.phenotype_dirs,
    )
    rows = specs_rows(registry, tier=ctx.tier, build_id=ctx.build_id, registered_at=utc_now_iso())
    dest = specs_path(ctx.lake_root, ctx.tier)
    nbytes = write_meta_parquet(ctx.con, dest, SPECS_COLUMNS, rows)
    _LOG.info(
        "meta.cohort_specs (%s): %d cohort spec(s) registered (%d locked) — %s",
        ctx.tier,
        len(rows),
        sum(1 for e in registry if e.locked),
        dest,
    )
    return StepOutcome(rows=len(rows), bytes_out=nbytes, files=1)


_SPECS_COMMENT = (
    "The cohort-spec registry index (cohort/registry.py, EP-46): one row per registered "
    "id@version — def_hash (sha256 of the canonical definition with the referenced code-set "
    "and phenotype hashes inlined), grain, index_event, n_inclusion / n_exclusion, custom "
    "(a custom_sql criterion is present), refs (JSON of codeset:<id@version> / "
    "phenotype:<id@version> -> def_hash), outcome, locked (recorded in cohorts.lock.json), "
    "the YAML path, registered_at, tier, build_id. Definitions only — marts.cohorts (EP-47) "
    "records the builds."
)


def register_cohorts(con: duckdb.DuckDBPyConnection, tier: str) -> None:
    """The catalog extension (:data:`mimicwarehouse.catalog.build.CATALOG_EXTENSIONS`
    entry, after EP-37's discovery walker): ``COMMENT ON`` ``meta.cohort_specs`` when the
    walker registered it, then (EP-47) the built cohorts —
    ``marts.cohort_<id>_v<major>`` views over ``lake/marts/<tier>/cohorts/`` and the
    ``marts.cohorts`` registry table (:func:`mimicwarehouse.cohort.build.register_marts`).
    DDL and registry text only; never opens a connection of its own."""
    from mimicwarehouse.cohort.build import register_marts

    present = {
        str(r[0])
        for r in con.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'meta'"
        ).fetchall()
    }
    if SPECS_TABLE in present:
        con.execute(f'COMMENT ON TABLE meta."{SPECS_TABLE}" IS {_sql_str(_SPECS_COMMENT)}')
    _LOG.info(
        "catalog extension cohorts: meta.%s %s on tier %s",
        SPECS_TABLE,
        "commented" if SPECS_TABLE in present else "absent",
        tier,
    )
    register_marts(con, tier)


# ---------------------------------------------------------------------------
# docs/methods/cohorts.md — the generated blocks
# ---------------------------------------------------------------------------

METHODS_DOC_RELPATH = Path("docs") / "methods" / "cohorts.md"
SCHEMA_MARK = ("<!-- schema:begin -->", "<!-- schema:end -->")
CARDS_MARK = ("<!-- cards:begin -->", "<!-- cards:end -->")
EXAMPLE_MARK = ("<!-- example:begin -->", "<!-- example:end -->")
#: The packaged spec the worked example renders (the tracer bullet's cohort).
EXAMPLE_REF = "first_icu_adults@1.0.0"


def _md_table(header: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(lines) + "\n"


def _md_cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _schema_type(prop: dict[str, Any], defs: dict[str, Any]) -> str:
    """A short type rendering of one JSON-schema property."""
    if "$ref" in prop:
        return "`" + str(prop["$ref"]).rsplit("/", 1)[-1] + "`"
    if "enum" in prop and "anyOf" not in prop:
        return "one of " + ", ".join(f"`{v}`" for v in prop["enum"])
    if "anyOf" in prop:
        parts = [
            _schema_type(p, defs) for p in prop["anyOf"] if p.get("type") != "null" or "$ref" in p
        ]
        text = " \\| ".join(parts) or "null"
        if any(p.get("type") == "null" for p in prop["anyOf"]):
            text += " (optional)"
        if "enum" in prop:
            text += "; one of " + ", ".join(f"`{v}`" for v in prop["enum"])
        return text
    if prop.get("type") == "array":
        items = prop.get("items", {})
        if isinstance(items, dict) and "enum" in items:
            return "list of " + ", ".join(f"`{v}`" for v in items["enum"])
        inner = _schema_type(items, defs) if isinstance(items, dict) else "any"
        return f"list of {inner}"
    kind = prop.get("type", "any")
    if isinstance(kind, list):
        kind = " / ".join(str(k) for k in kind)
    extra = ""
    if "pattern" in prop:
        extra = f" (`{prop['pattern']}`)"
    return f"{kind}{extra}"


def _schema_table(name: str, node: dict[str, Any], defs: dict[str, Any]) -> str:
    required = set(node.get("required", []))
    rows: list[list[str]] = []
    for field, prop in node.get("properties", {}).items():
        default = prop.get("default")
        default_text = "" if default in (None, [], {}, "") else f"`{json.dumps(default)}`"
        rows.append(
            [
                f"`{field}`",
                _schema_type(prop, defs),
                "yes" if field in required else "",
                _md_cell(str(prop.get("description", ""))),
                _md_cell(default_text),
            ]
        )
    intro = _md_cell(str(node.get("description", "")))
    return (
        f"**`{name}`**"
        + (f" — {intro}" if intro else "")
        + "\n\n"
        + _md_table(["field", "type", "required", "meaning", "default"], rows)
    )


def render_schema_reference(
    schema: dict[str, Any] | None = None, *, title: str = "CohortSpec"
) -> str:
    """The schema reference (generated from :func:`json_schema`): the top-level fields
    (labelled ``title``), then every nested object in the schema's ``$defs`` order. The
    EP-51 protocol page renders its own schema through the same function."""
    doc = schema if schema is not None else json_schema()
    defs: dict[str, Any] = doc.get("$defs", {})
    parts = [_schema_table(title, doc, defs)]
    parts.extend(_schema_table(name, node, defs) for name, node in defs.items())
    return "\n".join(parts)


def render_card(entry: Entry) -> str:
    """One packaged spec as a Markdown card (definition text and hashes only)."""
    spec = entry.spec
    lines = [
        f"### `{spec.ref}` — {spec.title}",
        "",
        f"- **grain** `{spec.grain}` · **index** `{spec.index_event.render()}` · **def_hash** "
        f"`{entry.def_hash[:12]}` · **locked** {'yes' if entry.locked else 'no'} · "
        f"`{entry.display_path}`",
        f"- **observation window** `[{spec.observation_window.start_h:g}, "
        f"{spec.observation_window.end_h:g})` h · **washout** {spec.washout.render()} · "
        f"**follow-up** {spec.follow_up.render()} · **era filter** "
        f"{', '.join(f'`{e}`' for e in spec.era_filter) or 'none'}",
        "- **references** "
        + (
            ", ".join(f"`{k}` (`{v[:12]}`)" for k, v in sorted(entry.flat_refs.items())) or "(none)"
        ),
        "- **degeneracy probe** "
        + (", ".join(f"`{c}`" for c in spec.degeneracy_probe.columns) or "off"),
        "",
        _md_table(
            ["step", "polarity", "kind", "definition"],
            [
                [
                    f"`{c.label}`",
                    polarity,
                    c.kind + (" (custom)" if c.custom else ""),
                    _md_cell(criterion_text(c)),
                ]
                for polarity, c in spec.criteria()
            ],
        ).rstrip("\n"),
        "",
    ]
    if spec.description:
        lines += [_md_cell(spec.description.strip()), ""]
    if spec.what_it_does_not_claim:
        lines += ["What it does not claim:", ""]
        lines += [f"- {_md_cell(item.strip())}" for item in spec.what_it_does_not_claim]
        lines.append("")
    return "\n".join(lines)


def render_cards(registry: Registry | None = None) -> str:
    reg = registry if registry is not None else load_registry()
    return "\n".join(render_card(e) for e in reg if e.packaged) + "\n"


def render_example(registry: Registry | None = None) -> str:
    """The worked example: the packaged tracer spec's YAML as a fenced block."""
    reg = registry if registry is not None else load_registry()
    text = reg.get(EXAMPLE_REF).path.read_text(encoding="utf-8").rstrip("\n")
    return "```yaml\n" + text + "\n```\n"


def methods_doc_path() -> Path:
    """``mimicwarehouse/docs/methods/cohorts.md``."""
    from mimicwarehouse.config import workspace_root

    return workspace_root() / METHODS_DOC_RELPATH


def sync_methods_doc(path: Path | None = None, registry: Registry | None = None) -> Path:
    """Re-render the three generated blocks of the methods page in place (idempotent;
    ``python -m mimicwarehouse.cohort`` runs it; ``test_ep46`` asserts the page is in
    sync). The narrative around the markers is never touched."""
    from mimicwarehouse.dag.benchmarks import replace_marked_block

    reg = registry if registry is not None else load_registry()
    target = Path(path) if path is not None else methods_doc_path()
    text = target.read_text(encoding="utf-8")
    for (begin, end), block in (
        (SCHEMA_MARK, render_schema_reference()),
        (CARDS_MARK, render_cards(reg)),
        (EXAMPLE_MARK, render_example(reg)),
    ):
        text = replace_marked_block(text, block, begin=begin, end=end)
    target.write_text(text.rstrip("\n") + "\n", encoding="utf-8", newline="\n")
    return target


__all__ = [
    "CARDS_MARK",
    "CODESET_KINDS",
    "CONCEPT_SCHEMA",
    "DAG_TAG",
    "EXAMPLE_MARK",
    "EXAMPLE_REF",
    "LOCK_FILENAME",
    "METHODS_DOC_RELPATH",
    "SCHEMA_MARK",
    "SPECS_COLUMNS",
    "SPECS_DIRNAME",
    "SPECS_TABLE",
    "STEP_SPECS",
    "WASHOUT_CODESET_KINDS",
    "Entry",
    "LockEntry",
    "LockFile",
    "LockResult",
    "Registry",
    "SpecsOptions",
    "Validation",
    "check_lock",
    "current_options",
    "load_dir",
    "load_lock",
    "load_registry",
    "lock_dir",
    "lock_path",
    "methods_doc_path",
    "packaged_specs_dir",
    "register_cohorts",
    "render_card",
    "render_cards",
    "render_example",
    "render_schema_reference",
    "resolve_references",
    "run_specs",
    "save_spec",
    "spec_filename",
    "specs_options",
    "specs_path",
    "specs_rows",
    "sync_methods_doc",
    "validate",
    "write_lock",
]
