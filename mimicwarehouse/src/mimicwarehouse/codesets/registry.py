"""The code-set registry: packaged seeds + lock, dictionary expansion, the ``codesets.compile``
DAG step and validation (EP-40 items 1-2; DESIGN §8, §15; GOVERNANCE §4/§12).

* **Registry.** Every ``defs/*.yaml`` of the package (:func:`packaged_defs_dir`) plus any
  study directory passed by path (``%MWH_DATA_ROOT%\\studies\\<study>\\codesets\\`` in the
  brief's shorthand) is loaded into a :class:`Registry` of :class:`Entry` records keyed by
  ``id@version``. A directory carries its own lock file ``codesets.lock.json`` (written by
  ``mwh codeset lock`` through :func:`fsio.atomic_write_text`): the record of every
  ``(id, version)`` pair's ``def_hash``. **Immutability rule:** loading a YAML whose hash
  differs from the recorded one raises :class:`CodeSetFrozenError` — the definition changed
  without a version bump. A pair not yet in the lock loads as *unlocked* (``mwh codeset lock``
  records it; ``lock --check`` fails while any packaged seed is unlocked).
* **Expansion** (:func:`expand`). Prefix rules are expanded against the dictionary tables —
  ``d_icd_diagnoses`` (``icd_dx``), ``d_icd_procedures`` (``icd_px``), ``d_items`` +
  ``d_labitems`` through EP-29's ``meta.itemids`` shape (``itemids``), ``d_hcpcs``
  (``hcpcs``) — into one :class:`MemberRow` per dictionary code, with the dictionary label
  and ``matched = True``; an exact code yields one row with ``matched`` saying whether the
  dictionary knows it; a prefix nothing matches yields one row (the prefix itself,
  ``matched = False``) so an unmatched rule stays visible. Drug names, RxNorm ids, ATC
  classes and LOINC codes have no dictionary on this warehouse (D-35: names only, no table
  download — EP-143 lands the tables) and carry ``matched = NULL``. Dictionary reads are
  contract dims / ``meta.itemids`` — registry exemptions under ``safe_query`` (EP-33 B1c),
  so validation output (declared / matched / unmatched codes, titles) is printable.
* **The DAG step** ``codesets.compile`` (``dag/specs/codesets.yaml``, tag ``codesets``;
  :func:`run_compile`) expands every registered set against the tier's staged dictionary
  views on the build connection and writes ``lake/meta/<tier>/codesets.parquet``
  (the index: id, version, def_hash, kind, name, counts, locked, path) and
  ``codeset_members.parquet`` (codeset_id, version, def_hash, kind, system, code,
  match_kind, declared_code, member_group, label, matched_in_dictionary), which EP-37's
  discovery walker registers as ``meta.codesets`` / ``meta.codeset_members``;
  :func:`register_codesets` (a ``CATALOG_EXTENSIONS`` entry) comments them. ``mwh codeset
  compile [--tier t] [id@version …]`` runs the step + the catalog step through the runner
  (build lock, benchmark lines, a ``run.start`` record); a selection re-compiles those sets
  and keeps the other sets' rows (:func:`compile_options` hands the selection to the step).
* **Validation** (:func:`validate`) is the same expansion over the tier catalog's
  dictionaries read through ``safe_query`` (audited) — ``mwh codeset validate <id@version>``
  prints declared / matched / unmatched per system.

Everything written, printed or returned is code-set text and dictionary titles — never a
patient-level row (GOVERNANCE §4). Import budget: pydantic + yaml + stdlib at import time
(``cli.py`` imports the ``codeset`` sub-app at start-up); duckdb, polars, ``safe``, the
concept runner and the catalog builder load inside function bodies.
"""

from __future__ import annotations

import bisect
import json
import logging
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from mimicwarehouse import fsio
from mimicwarehouse.codesets.spec import (
    SYSTEMS,
    CodeEntry,
    CodeSet,
    CodeSetError,
    CodeSetFrozenError,
    UnknownCodeSetError,
    format_ref,
    load_codeset,
    parse_ref,
)

if TYPE_CHECKING:  # pragma: no cover
    import duckdb

    from mimicwarehouse.config import Settings
    from mimicwarehouse.dag.runner import StepContext, StepOutcome
    from mimicwarehouse.dag.spec import Step

_LOG = logging.getLogger(__name__)

#: ``src/mimicwarehouse/codesets/defs/`` — the packaged seed sets and their lock.
DEFS_DIRNAME = "defs"
LOCK_FILENAME = "codesets.lock.json"
#: ``lake/meta/<tier>/<table>.parquet`` -> ``meta.<table>`` (EP-37 walker).
CODESETS_TABLE = "codesets"
MEMBERS_TABLE = "codeset_members"
#: The DAG step / tag (``dag/specs/codesets.yaml``).
STEP_COMPILE = "codesets.compile"
DAG_TAG = "codesets"
#: Row cap for the dictionary reads through ``safe_query`` (the full ICD-10-CM dictionary
#: is ~100 k rows; dims are registry-exempt, the cap only guards against a runaway read).
DICTIONARY_ROW_CAP = 1_000_000
#: The brief's floor for the packaged seeds.
MIN_SEED_SETS = 12

#: Dictionary keys and the tables behind them (the ``itemid`` key is ``meta.itemids`` in a
#: catalog and EP-29's ``ITEMIDS_SELECT_SQL`` over the source views on a build connection).
DICT_ICD_DX = "icd_dx"
DICT_ICD_PX = "icd_px"
DICT_ITEMID = "itemid"
DICT_HCPCS = "hcpcs"
DICTIONARY_TABLES: dict[str, str] = {
    DICT_ICD_DX: "mimiciv_hosp.d_icd_diagnoses",
    DICT_ICD_PX: "mimiciv_hosp.d_icd_procedures",
    DICT_HCPCS: "mimiciv_hosp.d_hcpcs",
    DICT_ITEMID: "meta.itemids",
}
#: The staged tables a dictionary key needs on the build connection.
DICTIONARY_SOURCES: dict[str, tuple[str, ...]] = {
    DICT_ICD_DX: ("mimiciv_hosp.d_icd_diagnoses",),
    DICT_ICD_PX: ("mimiciv_hosp.d_icd_procedures",),
    DICT_HCPCS: ("mimiciv_hosp.d_hcpcs",),
    DICT_ITEMID: ("mimiciv_icu.d_items", "mimiciv_hosp.d_labitems"),
}


def _sql_str(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def _today() -> str:
    return datetime.now(UTC).date().isoformat()


# ---------------------------------------------------------------------------
# The lock file — the record behind the immutability rule
# ---------------------------------------------------------------------------


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class LockEntry(_Frozen):
    """One recorded ``(id, version)`` pair."""

    def_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    kind: str
    locked_at: str


class LockFile(_Frozen):
    """``codesets.lock.json``: ``{"version": 1, "codesets": {"<id>@<version>": {…}}}``."""

    version: int = 1
    codesets: dict[str, LockEntry] = Field(default_factory=dict)


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
        raise CodeSetError(f"{path}: not a valid code-set lock ({exc})") from exc


def write_lock(defs_dir: Path | str, lock: LockFile) -> Path:
    """Write the lock atomically (:func:`fsio.atomic_write_text`; sorted keys, LF)."""
    path = lock_path(defs_dir)
    payload = lock.model_dump(mode="json")
    payload["codesets"] = dict(sorted(payload["codesets"].items()))
    fsio.atomic_write_text(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return path


def check_lock(codeset: CodeSet, lock: LockFile, *, where: str) -> bool:
    """Whether ``codeset`` is recorded in ``lock`` with the same hash; raises
    :class:`CodeSetFrozenError` when the pair is recorded with a different hash."""
    entry = lock.codesets.get(codeset.ref)
    if entry is None:
        return False
    if entry.def_hash != codeset.def_hash:
        raise CodeSetFrozenError(
            f"{where}: {codeset.ref} is frozen at def_hash {entry.def_hash[:12]} but the "
            f"file now hashes {codeset.def_hash[:12]} — the definition changed without a "
            "version bump; bump `version` (and lock the new pair) instead of editing a "
            "released version"
        )
    return True


# ---------------------------------------------------------------------------
# Entries and the registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Entry:
    """One registered code set: the parsed YAML, where it came from, whether locked."""

    codeset: CodeSet
    path: Path
    locked: bool
    packaged: bool

    @property
    def ref(self) -> str:
        return self.codeset.ref

    @property
    def display_path(self) -> str:
        """``defs/<file>`` for a packaged seed, the absolute posix path otherwise."""
        if self.packaged:
            return f"{DEFS_DIRNAME}/{self.path.name}"
        return self.path.resolve().as_posix()


class Registry:
    """The loaded code sets, addressable by ``id@version``."""

    def __init__(self, entries: Iterable[Entry]) -> None:
        self.entries: tuple[Entry, ...] = tuple(entries)
        self._by_ref: dict[str, Entry] = {}
        for entry in self.entries:
            if entry.ref in self._by_ref:
                other = self._by_ref[entry.ref]
                raise CodeSetError(
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
        return tuple(sorted({e.codeset.id for e in self.entries}))

    def versions_of(self, codeset_id: str) -> tuple[str, ...]:
        return tuple(e.codeset.version for e in self.entries if e.codeset.id == codeset_id)

    def get(self, ref: str) -> Entry:
        """The entry for ``id@version`` (:class:`UnknownCodeSetError` names the known
        versions of that id, or says the id is unknown)."""
        codeset_id, version = parse_ref(ref)
        entry = self._by_ref.get(format_ref(codeset_id, version))
        if entry is not None:
            return entry
        versions = self.versions_of(codeset_id)
        if versions:
            raise UnknownCodeSetError(
                f"no code set {codeset_id}@{version}; known versions of {codeset_id}: "
                f"{', '.join(versions)}"
            )
        raise UnknownCodeSetError(
            f"no code set {codeset_id!r}; known ids: {', '.join(self.ids()) or '(none)'}"
        )

    def select(self, refs: Iterable[str]) -> Registry:
        """The sub-registry of ``refs`` (every one must exist), registry order."""
        wanted = {self.get(r).ref for r in refs}
        return Registry(e for e in self.entries if e.ref in wanted)


def packaged_defs_dir() -> Path:
    """``src/mimicwarehouse/codesets/defs/`` inside the installed package (tests
    monkeypatch this to point at crafted directories)."""
    return Path(str(files("mimicwarehouse.codesets").joinpath(DEFS_DIRNAME)))


def load_dir(defs_dir: Path | str, *, packaged: bool = False) -> list[Entry]:
    """Every ``*.yaml`` of ``defs_dir`` (sorted by name) checked against the directory's
    lock; :class:`CodeSetError` for a malformed file, :class:`CodeSetFrozenError` for a
    frozen pair whose hash moved."""
    root = Path(defs_dir)
    if not root.is_dir():
        raise CodeSetError(f"{root}: not a code-set directory")
    lock = load_lock(root)
    entries: list[Entry] = []
    for path in sorted(root.glob("*.yaml")):
        codeset = load_codeset(path)
        locked = check_lock(codeset, lock, where=path.name)
        entries.append(Entry(codeset=codeset, path=path, locked=locked, packaged=packaged))
    return entries


def load_registry(extra_dirs: Iterable[Path | str] = ()) -> Registry:
    """The packaged seeds plus every ``extra_dirs`` directory, as one :class:`Registry`
    (an ``id@version`` defined twice is refused)."""
    entries = load_dir(packaged_defs_dir(), packaged=True)
    for extra in extra_dirs:
        entries.extend(load_dir(Path(extra)))
    return Registry(entries)


@dataclass(frozen=True, slots=True)
class LockResult:
    path: Path
    added: tuple[str, ...]
    unchanged: tuple[str, ...]


def lock_dir(defs_dir: Path | str) -> LockResult:
    """Record every not-yet-locked ``(id, version)`` of ``defs_dir`` in its lock (a frozen
    pair whose hash moved refuses first, as :func:`load_dir` does); the file is rewritten
    only when something was added."""
    root = Path(defs_dir)
    entries = load_dir(root)
    lock = load_lock(root)
    codesets = dict(lock.codesets)
    added: list[str] = []
    unchanged: list[str] = []
    for entry in entries:
        if entry.ref in codesets:
            unchanged.append(entry.ref)
            continue
        codesets[entry.ref] = LockEntry(
            def_hash=entry.codeset.def_hash, kind=entry.codeset.kind, locked_at=_today()
        )
        added.append(entry.ref)
    path = lock_path(root)
    if added:
        path = write_lock(root, LockFile(version=lock.version, codesets=codesets))
    return LockResult(path=path, added=tuple(added), unchanged=tuple(unchanged))


# ---------------------------------------------------------------------------
# Dictionaries
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Dictionary:
    """One system's dictionary: normalised code -> title, plus the sorted code list the
    prefix rules bisect."""

    key: str
    system: str
    labels: dict[str, str | None]
    codes: tuple[str, ...]

    @classmethod
    def from_labels(cls, key: str, system: str, labels: dict[str, str | None]) -> Dictionary:
        return cls(key=key, system=system, labels=labels, codes=tuple(sorted(labels)))

    def has(self, code: str) -> bool:
        return code in self.labels

    def label(self, code: str) -> str | None:
        return self.labels.get(code)

    def with_prefix(self, prefix: str) -> tuple[str, ...]:
        """Every code starting with ``prefix`` (bisect over the sorted codes)."""
        lo = bisect.bisect_left(self.codes, prefix)
        hi = bisect.bisect_left(self.codes, prefix + "\\uffff")
        return self.codes[lo:hi]

    def __len__(self) -> int:
        return len(self.codes)


class Dictionaries:
    """The dictionaries at hand, keyed by ``(dictionary key, system)``."""

    def __init__(self, tables: Iterable[Dictionary] = ()) -> None:
        self._tables: dict[tuple[str, str], Dictionary] = {}
        for table in tables:
            self._tables[(table.key, table.system)] = table

    def get(self, key: str, system: str) -> Dictionary | None:
        return self._tables.get((key, system))

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(sorted({k for k, _s in self._tables}))

    def __len__(self) -> int:
        return len(self._tables)


def dictionary_keys(codeset: CodeSet) -> tuple[str, ...]:
    """The dictionary keys a code set's members are checked against."""
    keys: list[str] = []
    if codeset.kind in (DICT_ICD_DX, DICT_ICD_PX):
        keys.append(codeset.kind)
    if codeset.members.itemids:
        keys.append(DICT_ITEMID)
    if codeset.kind == DICT_HCPCS:
        keys.append(DICT_HCPCS)
    return tuple(keys)


def dictionary_sql(key: str, *, on_catalog: bool) -> str:
    """The SELECT behind one dictionary key — the same text on a tier catalog and on the
    build connection's source views, except ``itemid``: ``meta.itemids`` exists only in a
    catalog, so the build side runs EP-29's ``ITEMIDS_SELECT_SQL`` directly."""
    if key in (DICT_ICD_DX, DICT_ICD_PX):
        return f"SELECT icd_code, icd_version, long_title FROM {DICTIONARY_TABLES[key]}"
    if key == DICT_HCPCS:
        return f"SELECT code, short_description FROM {DICTIONARY_TABLES[key]}"
    if key == DICT_ITEMID:
        if on_catalog:
            return f"SELECT itemid, label FROM {DICTIONARY_TABLES[key]}"
        from mimicwarehouse.catalog.build import ITEMIDS_SELECT_SQL

        return f"SELECT itemid, label FROM ({ITEMIDS_SELECT_SQL}) AS i"
    raise CodeSetError(
        f"unknown dictionary key {key!r}; expected one of {sorted(DICTIONARY_TABLES)}"
    )


def _norm_dict_code(code: Any) -> str:
    return str(code).strip().upper().replace(".", "")


def dictionaries_from_rows(key: str, rows: Iterable[Sequence[Any]]) -> list[Dictionary]:
    """The :class:`Dictionary` objects of one key from the SELECT's rows (an ICD key yields
    ``icd9`` and ``icd10``, always both, possibly empty)."""
    if key in (DICT_ICD_DX, DICT_ICD_PX):
        by_version: dict[str, dict[str, str | None]] = {"icd9": {}, "icd10": {}}
        for code, version, title in rows:
            system = "icd9" if int(version) == 9 else "icd10"
            by_version[system][_norm_dict_code(code)] = None if title is None else str(title)
        return [Dictionary.from_labels(key, s, labels) for s, labels in by_version.items()]
    if key == DICT_HCPCS:
        labels = {_norm_dict_code(code): None if t is None else str(t) for code, t in rows}
        return [Dictionary.from_labels(key, "hcpcs", labels)]
    if key == DICT_ITEMID:
        labels = {str(int(itemid)): None if t is None else str(t) for itemid, t in rows}
        return [Dictionary.from_labels(key, "itemid", labels)]
    raise CodeSetError(f"unknown dictionary key {key!r}")


def dictionaries_from_connection(
    con: duckdb.DuckDBPyConnection, keys: Iterable[str], *, on_catalog: bool
) -> Dictionaries:
    """Read the dictionaries of ``keys`` on an open connection (the build connection's
    source views, or a tier catalog opened elsewhere)."""
    tables: list[Dictionary] = []
    for key in dict.fromkeys(keys):
        rows = con.execute(dictionary_sql(key, on_catalog=on_catalog)).fetchall()
        tables.extend(dictionaries_from_rows(key, rows))
    return Dictionaries(tables)


def dictionaries_via_safe_query(
    keys: Iterable[str],
    *,
    tier: str,
    settings: Settings | None = None,
    actor: str | None = None,
) -> Dictionaries:
    """Read the dictionaries of ``keys`` from the tier catalog through
    :func:`mimicwarehouse.safe.safe_query` (audited; dims and ``meta.itemids`` are
    registry-exempt, so the rows come back unsuppressed)."""
    from mimicwarehouse.safe import safe_query

    tables: list[Dictionary] = []
    for key in dict.fromkeys(keys):
        result = safe_query(
            dictionary_sql(key, on_catalog=True),
            tier=tier,
            row_cap=DICTIONARY_ROW_CAP,
            settings=settings,
            actor=actor,
        )
        tables.extend(dictionaries_from_rows(key, result.df.rows()))
    return Dictionaries(tables)


# ---------------------------------------------------------------------------
# Expansion and coverage
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MemberRow:
    """One compiled member (module docstring): ``matched`` is None where the system has
    no dictionary on this warehouse."""

    system: str
    code: str
    match_kind: str
    declared_code: str
    group: str | None
    label: str | None
    matched: bool | None


def _expand_entries(
    system: str, entries: Sequence[CodeEntry], dictionary: Dictionary | None
) -> list[MemberRow]:
    rows: list[MemberRow] = []
    for entry in entries:
        if dictionary is None:
            rows.append(
                MemberRow(system, entry.code, entry.match, entry.code, entry.group, None, None)
            )
        elif entry.match == "exact":
            rows.append(
                MemberRow(
                    system,
                    entry.code,
                    "exact",
                    entry.code,
                    entry.group,
                    dictionary.label(entry.code),
                    dictionary.has(entry.code),
                )
            )
        else:
            hits = dictionary.with_prefix(entry.code)
            if hits:
                rows.extend(
                    MemberRow(
                        system, hit, "prefix", entry.code, entry.group, dictionary.label(hit), True
                    )
                    for hit in hits
                )
            else:
                rows.append(
                    MemberRow(system, entry.code, "prefix", entry.code, entry.group, None, False)
                )
    return rows


def expand(codeset: CodeSet, dictionaries: Dictionaries | None = None) -> list[MemberRow]:
    """The compiled member rows of ``codeset`` against ``dictionaries`` (module docstring);
    without dictionaries every row carries ``matched = None``."""
    dicts = dictionaries if dictionaries is not None else Dictionaries()
    members = codeset.members
    rows: list[MemberRow] = []
    for system, entries in (("icd9", members.icd9), ("icd10", members.icd10)):
        if entries:
            rows.extend(_expand_entries(system, entries, dicts.get(codeset.kind, system)))
    if members.hcpcs:
        rows.extend(_expand_entries("hcpcs", members.hcpcs, dicts.get(DICT_HCPCS, "hcpcs")))
    if members.itemids:
        table = dicts.get(DICT_ITEMID, "itemid")
        for itemid in members.itemids:
            code = str(itemid)
            known = None if table is None else table.has(code)
            label = table.label(code) if table is not None and known else None
            rows.append(MemberRow("itemid", code, "exact", code, None, label, known))
    if members.drugs is not None:
        rows.extend(
            MemberRow("drug_name", name, members.drugs.match, name, None, None, None)
            for name in members.drugs.names
        )
        rows.extend(
            MemberRow("rxnorm", cui, "exact", cui, None, None, None) for cui in members.drugs.rxnorm
        )
    rows.extend(MemberRow("atc", cls, "prefix", cls, None, None, None) for cls in members.atc)
    rows.extend(MemberRow("loinc", code, "exact", code, None, None, None) for code in members.loinc)
    return rows


@dataclass(frozen=True, slots=True)
class Coverage:
    """Dictionary coverage of one system: declared entries, how many matched at least one
    dictionary code (None = no dictionary), the unmatched declared codes, compiled rows."""

    system: str
    declared: int
    matched: int | None
    unmatched: tuple[str, ...]
    rows: int

    @property
    def share(self) -> float | None:
        if self.matched is None or self.declared == 0:
            return None
        return self.matched / self.declared


def coverage(rows: Sequence[MemberRow]) -> list[Coverage]:
    """Per-system :class:`Coverage` of compiled rows, :data:`SYSTEMS` order."""
    out: list[Coverage] = []
    for system in SYSTEMS:
        mine = [r for r in rows if r.system == system]
        if not mine:
            continue
        declared: dict[str, bool | None] = {}
        for row in mine:
            current = declared.get(row.declared_code)
            if row.matched is None:
                declared.setdefault(row.declared_code, None)
            elif current is None or not current:
                declared[row.declared_code] = bool(row.matched)
        if all(v is None for v in declared.values()):
            out.append(Coverage(system, len(declared), None, (), len(mine)))
            continue
        matched = sum(1 for v in declared.values() if v)
        unmatched = tuple(sorted(code for code, v in declared.items() if not v))
        out.append(Coverage(system, len(declared), matched, unmatched, len(mine)))
    return out


# ---------------------------------------------------------------------------
# Compile options (the CLI -> step channel) and the meta tables
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CompileOptions:
    """What ``mwh codeset compile`` hands the step: an ``id@version`` selection (empty =
    every registered set) and extra definition directories."""

    select: tuple[str, ...] = ()
    extra_dirs: tuple[Path, ...] = ()


_OPTIONS: ContextVar[CompileOptions | None] = ContextVar("mwh_codesets_compile", default=None)


@contextmanager
def compile_options(
    *, select: Iterable[str] = (), extra_dirs: Iterable[Path | str] = ()
) -> Iterator[CompileOptions]:
    """Install :class:`CompileOptions` for the duration of a runner call in this thread."""
    options = CompileOptions(select=tuple(select), extra_dirs=tuple(Path(d) for d in extra_dirs))
    token = _OPTIONS.set(options)
    try:
        yield options
    finally:
        _OPTIONS.reset(token)


def current_options() -> CompileOptions:
    """The options installed by :func:`compile_options`, or the defaults (every set)."""
    return _OPTIONS.get() or CompileOptions()


CODESETS_COLUMNS: tuple[tuple[str, str], ...] = (
    ("codeset_id", "VARCHAR"),
    ("version", "VARCHAR"),
    ("def_hash", "VARCHAR"),
    ("kind", "VARCHAR"),
    ("name", "VARCHAR"),
    ("n_declared", "INTEGER"),
    ("n_members", "INTEGER"),
    ("n_matched", "INTEGER"),
    ("n_groups", "INTEGER"),
    ("locked", "BOOLEAN"),
    ("path", "VARCHAR"),
    ("compiled_at", "VARCHAR"),
    ("tier", "VARCHAR"),
    ("build_id", "VARCHAR"),
)
MEMBERS_COLUMNS: tuple[tuple[str, str], ...] = (
    ("codeset_id", "VARCHAR"),
    ("version", "VARCHAR"),
    ("def_hash", "VARCHAR"),
    ("kind", "VARCHAR"),
    ("system", "VARCHAR"),
    ("code", "VARCHAR"),
    ("match_kind", "VARCHAR"),
    ("declared_code", "VARCHAR"),
    ("member_group", "VARCHAR"),
    ("label", "VARCHAR"),
    ("matched_in_dictionary", "BOOLEAN"),
    ("tier", "VARCHAR"),
    ("build_id", "VARCHAR"),
)


def compile_rows(
    registry: Registry,
    dictionaries: Dictionaries,
    *,
    tier: str,
    build_id: str,
    compiled_at: str,
) -> tuple[list[list[Any]], list[list[Any]]]:
    """``(codesets rows, member rows)`` for every entry of ``registry``, column order of
    :data:`CODESETS_COLUMNS` / :data:`MEMBERS_COLUMNS`."""
    index: list[list[Any]] = []
    members: list[list[Any]] = []
    for entry in registry:
        cs = entry.codeset
        rows = expand(cs, dictionaries)
        index.append(
            [
                cs.id,
                cs.version,
                cs.def_hash,
                cs.kind,
                cs.name,
                cs.n_declared,
                len(rows),
                sum(1 for r in rows if r.matched),
                len(cs.groups),
                entry.locked,
                entry.display_path,
                compiled_at,
                tier,
                build_id,
            ]
        )
        members.extend(
            [
                cs.id,
                cs.version,
                cs.def_hash,
                cs.kind,
                r.system,
                r.code,
                r.match_kind,
                r.declared_code,
                r.group,
                r.label,
                r.matched,
                tier,
                build_id,
            ]
            for r in rows
        )
    return index, members


def _present_tables(con: duckdb.DuckDBPyConnection) -> set[str]:
    rows = con.execute(
        "SELECT table_schema || '.' || table_name FROM information_schema.tables"
    ).fetchall()
    return {str(r[0]) for r in rows}


def _existing_rows(
    con: duckdb.DuckDBPyConnection,
    path: Path,
    columns: tuple[tuple[str, str], ...],
    exclude_refs: set[str],
) -> list[list[Any]]:
    """The rows of a previously written meta file minus the sets being re-compiled."""
    if not path.is_file():
        return []
    names = ", ".join(f'"{c}"' for c, _t in columns)
    rows = con.execute(
        f"SELECT {names} FROM read_parquet({_sql_str(path.resolve().as_posix())})"
    ).fetchall()
    return [list(r) for r in rows if format_ref(str(r[0]), str(r[1])) not in exclude_refs]


def run_compile(step: Step, ctx: StepContext) -> StepOutcome:
    """The ``codesets.compile`` handler (module docstring): the registry (packaged + the
    options' extra dirs; a frozen mismatch fails the step), the dictionaries from the
    tier's staged dims on the build connection, the two meta files."""
    from mimicwarehouse.concepts.runner import ensure_source_views
    from mimicwarehouse.dag.runner import StepOutcome
    from mimicwarehouse.loader.manifest import utc_now_iso
    from mimicwarehouse.units import meta_table_path, write_meta_parquet

    options = current_options()
    registry = load_registry(options.extra_dirs)
    selected = registry.select(options.select) if options.select else registry
    ensure_source_views(ctx)
    present = _present_tables(ctx.con)
    keys: list[str] = []
    for entry in selected:
        keys.extend(k for k in dictionary_keys(entry.codeset) if k not in keys)
    missing = [
        source for key in keys for source in DICTIONARY_SOURCES[key] if source not in present
    ]
    if missing:
        raise CodeSetError(
            f"{', '.join(sorted(set(missing)))} not staged for tier {ctx.tier} — the code-set "
            "dictionaries need the dimension tables (stage them first)"
        )
    dictionaries = dictionaries_from_connection(ctx.con, keys, on_catalog=False)
    index_rows, member_rows = compile_rows(
        selected, dictionaries, tier=ctx.tier, build_id=ctx.build_id, compiled_at=utc_now_iso()
    )
    index_dest = meta_table_path(ctx.lake_root, ctx.tier, CODESETS_TABLE)
    members_dest = meta_table_path(ctx.lake_root, ctx.tier, MEMBERS_TABLE)
    if options.select:
        refs = set(selected.refs())
        index_rows = _existing_rows(ctx.con, index_dest, CODESETS_COLUMNS, refs) + index_rows
        member_rows = _existing_rows(ctx.con, members_dest, MEMBERS_COLUMNS, refs) + member_rows
    index_bytes = write_meta_parquet(ctx.con, index_dest, CODESETS_COLUMNS, index_rows)
    members_bytes = write_meta_parquet(ctx.con, members_dest, MEMBERS_COLUMNS, member_rows)
    _LOG.info(
        "meta.codesets / meta.codeset_members (%s): %d code set(s) compiled (%d in the "
        "index), %d member row(s), dictionaries %s — %s",
        ctx.tier,
        len(selected),
        len(index_rows),
        len(member_rows),
        ", ".join(keys) or "none",
        members_dest,
    )
    return StepOutcome(rows=len(member_rows), bytes_out=index_bytes + members_bytes, files=2)


_META_COMMENTS: dict[str, str] = {
    CODESETS_TABLE: (
        "The code-set registry index (codesets/registry.py, EP-40): one row per compiled "
        "id@version — def_hash (sha256 of the canonical kind + members), kind, name, "
        "n_declared / n_members / n_matched / n_groups, locked (recorded in "
        "codesets.lock.json), the YAML path, compiled_at, tier, build_id."
    ),
    MEMBERS_TABLE: (
        "Compiled code-set members (EP-40): one row per dictionary code a rule expands to "
        "(system icd9 / icd10 / itemid / hcpcs, prefix rules expanded against the tier's "
        "dictionary tables, label = the dictionary title, matched_in_dictionary) or per "
        "declared name / class / code without a dictionary (drug_name, rxnorm, atc, loinc; "
        "matched_in_dictionary NULL); member_group carries the category of a grouped set "
        "(charlson_groups). Join on (codeset_id, version); def_hash pins the definition."
    ),
}


def register_codesets(con: duckdb.DuckDBPyConnection, tier: str) -> None:
    """The catalog extension (:data:`mimicwarehouse.catalog.build.CATALOG_EXTENSIONS`
    entry, after EP-37's discovery walker): ``COMMENT ON`` the code-set and GEM meta
    tables the walker registered from ``lake/meta/<tier>/``. DDL only; never opens a
    connection of its own."""
    from mimicwarehouse.codesets.gem import GEM_META_COMMENTS

    present = {
        str(r[0])
        for r in con.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'meta'"
        ).fetchall()
    }
    commented = 0
    for table, comment in {**_META_COMMENTS, **GEM_META_COMMENTS}.items():
        if table in present:
            con.execute(f'COMMENT ON TABLE meta."{table}" IS {_sql_str(comment)}')
            commented += 1
    _LOG.info("catalog extension codesets: %d meta table(s) commented on tier %s", commented, tier)


# ---------------------------------------------------------------------------
# Validation (the CLI path: dictionaries through safe_query)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Validation:
    """``mwh codeset validate``'s result: the entry, the tier whose dictionaries were
    read, the compiled rows and the per-system coverage."""

    entry: Entry
    tier: str
    rows: tuple[MemberRow, ...]
    coverages: tuple[Coverage, ...]

    @property
    def n_matched(self) -> int:
        return sum(1 for r in self.rows if r.matched)

    def to_dict(self) -> dict[str, Any]:
        cs = self.entry.codeset
        return {
            "ref": cs.ref,
            "kind": cs.kind,
            "def_hash": cs.def_hash,
            "tier": self.tier,
            "n_declared": cs.n_declared,
            "n_members": len(self.rows),
            "n_matched": self.n_matched,
            "systems": [
                {
                    "system": c.system,
                    "declared": c.declared,
                    "matched": c.matched,
                    "share": c.share,
                    "unmatched": list(c.unmatched),
                    "rows": c.rows,
                }
                for c in self.coverages
            ],
        }


def validate(
    ref: str,
    *,
    tier: str,
    settings: Settings | None = None,
    actor: str | None = None,
    extra_dirs: Iterable[Path | str] = (),
) -> Validation:
    """Expand ``ref`` against the tier catalog's dictionaries (through ``safe_query``) and
    report coverage (module docstring)."""
    registry = load_registry(extra_dirs)
    entry = registry.get(ref)
    keys = dictionary_keys(entry.codeset)
    dictionaries = (
        dictionaries_via_safe_query(keys, tier=tier, settings=settings, actor=actor)
        if keys
        else Dictionaries()
    )
    rows = expand(entry.codeset, dictionaries)
    return Validation(entry=entry, tier=tier, rows=tuple(rows), coverages=tuple(coverage(rows)))


# ---------------------------------------------------------------------------
# docs/methods/codesets.md — the generated block
# ---------------------------------------------------------------------------

METHODS_DOC_RELPATH = Path("docs") / "methods" / "codesets.md"
SEEDS_MARK = ("<!-- seeds:begin -->", "<!-- seeds:end -->")


def _md_table(header: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(lines) + "\n"


def _members_cell(codeset: CodeSet) -> str:
    m = codeset.members
    parts: list[str] = []
    for name, entries in (("icd9", m.icd9), ("icd10", m.icd10), ("hcpcs", m.hcpcs)):
        if entries:
            prefixes = sum(1 for e in entries if e.match == "prefix")
            detail = f"{len(entries)}"
            if prefixes:
                detail += f" ({prefixes} prefix)"
            parts.append(f"{name} {detail}")
    if m.itemids:
        parts.append(f"itemids {len(m.itemids)}")
    if m.drugs is not None:
        parts.append(f"names {len(m.drugs.names)} ({m.drugs.match})")
        if m.drugs.rxnorm:
            parts.append(f"rxnorm {len(m.drugs.rxnorm)}")
    if m.atc:
        parts.append(f"atc {len(m.atc)}")
    if m.loinc:
        parts.append(f"loinc {len(m.loinc)}")
    return ", ".join(parts)


def render_seed_table(registry: Registry | None = None) -> str:
    """The packaged seeds as a Markdown table (id@version, kind, name, members, groups,
    provenance, the first 12 hex digits of ``def_hash``)."""
    reg = (
        registry if registry is not None else Registry(load_dir(packaged_defs_dir(), packaged=True))
    )
    rows: list[list[str]] = []
    for entry in reg:
        cs = entry.codeset
        rows.append(
            [
                f"`{cs.ref}`",
                f"`{cs.kind}`",
                cs.name,
                _members_cell(cs),
                str(len(cs.groups)) if cs.groups else "-",
                cs.provenance.source + (f" (`{cs.provenance.ref}`)" if cs.provenance.ref else ""),
                f"`{cs.def_hash[:12]}`",
            ]
        )
    return _md_table(
        ["code set", "kind", "name", "declared members", "groups", "provenance", "def_hash"],
        rows,
    )


def methods_doc_path() -> Path:
    """``mimicwarehouse/docs/methods/codesets.md``."""
    from mimicwarehouse.config import workspace_root

    return workspace_root() / METHODS_DOC_RELPATH


def sync_methods_doc(path: Path | None = None) -> Path:
    """Re-render the generated block of the methods page in place (idempotent;
    ``python -m mimicwarehouse.codesets`` runs it; ``test_ep40`` asserts the page is in
    sync). The narrative around the markers is never touched."""
    from mimicwarehouse.dag.benchmarks import replace_marked_block

    target = Path(path) if path is not None else methods_doc_path()
    text = target.read_text(encoding="utf-8")
    begin, end = SEEDS_MARK
    text = replace_marked_block(text, render_seed_table(), begin=begin, end=end)
    target.write_text(text.rstrip("\n") + "\n", encoding="utf-8", newline="\n")
    return target


__all__ = [
    "CODESETS_COLUMNS",
    "CODESETS_TABLE",
    "DAG_TAG",
    "DEFS_DIRNAME",
    "DICTIONARY_ROW_CAP",
    "DICTIONARY_SOURCES",
    "DICTIONARY_TABLES",
    "DICT_HCPCS",
    "DICT_ICD_DX",
    "DICT_ICD_PX",
    "DICT_ITEMID",
    "LOCK_FILENAME",
    "MEMBERS_COLUMNS",
    "MEMBERS_TABLE",
    "METHODS_DOC_RELPATH",
    "MIN_SEED_SETS",
    "SEEDS_MARK",
    "STEP_COMPILE",
    "CompileOptions",
    "Coverage",
    "Dictionaries",
    "Dictionary",
    "Entry",
    "LockEntry",
    "LockFile",
    "LockResult",
    "MemberRow",
    "Registry",
    "Validation",
    "check_lock",
    "compile_options",
    "compile_rows",
    "coverage",
    "current_options",
    "dictionaries_from_connection",
    "dictionaries_from_rows",
    "dictionaries_via_safe_query",
    "dictionary_keys",
    "dictionary_sql",
    "expand",
    "load_dir",
    "load_lock",
    "load_registry",
    "lock_dir",
    "lock_path",
    "methods_doc_path",
    "packaged_defs_dir",
    "register_codesets",
    "render_seed_table",
    "run_compile",
    "sync_methods_doc",
    "validate",
    "write_lock",
]
