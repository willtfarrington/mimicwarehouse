"""The CMS General Equivalence Mappings (GEMs): fetch, parse, map, materialise, review
(EP-40 item 4; DESIGN §8, §19; D-35, D-36; GOVERNANCE §10).

CMS published the ICD-9-CM <-> ICD-10-CM/PCS GEMs annually until the **2018** files, the
final release (public domain; register row in ``docs/resources/vocabularies.md``). Two
zips — diagnoses (``2018_I9gem.txt`` / ``2018_I10gem.txt``) and procedures
(``gem_i9pcs.txt`` / ``gem_pcsi9.txt``) — each line ``<source> <target> <flags>`` where the
five flag digits are *approximate*, *no map*, *combination*, *scenario* and *choice list*
(the last two are small integers naming the combination scenario and the choice list a
target belongs to); a target ``NoDx`` / ``NoPCS`` with the no-map flag means the source
has no counterpart. A code with several lines is a one-to-many (choose one) or, with the
combination flag, a many-codes-together mapping; the GEM is deliberately **not** a
crosswalk — hence ``mwh codeset expand --via-gem`` only *proposes* counterpart codes for a
human to fold into a new code-set version (module contract, brief item 4).

* **Landing** (:func:`gem_root`): ``settings.layout["ext"] / "vocab" / "gem" / "2018"``
  (EP-14's ``ext/vocab/<source>/<version>/`` convention; not a ``Settings.layout`` key —
  the vocabularies register says why). ``mwh codeset gem fetch`` (:func:`fetch`) downloads
  the two public zips (``urllib`` through the :func:`_urlopen` seam — no
  ``MWH_ALLOW_REMOTE`` gate, that covers text modules only), extracts exactly the four
  expected text members, verifies every extracted file against the sha256 pinned in
  :data:`GEM_ARCHIVES` (a mismatch deletes the file and refuses) and writes the
  ``source.yaml`` register (:class:`GemRegister`: EP-14's template fields — name, version,
  release date, URL, license, registration / redistribution flags, obtained-on/by, the
  archives and files with sha256 + bytes, columns of interest, consuming EPs). The owner
  may place the four files by hand instead; :func:`landed_kinds` only looks at the files.
* **Parsing / mapping**: :func:`parse_gem_text` -> :class:`GemEntry` rows;
  :class:`GemTable` (per kind ``dx`` / ``px``) with :meth:`GemTable.forward` (ICD-9 ->
  ICD-10) and :meth:`GemTable.backward` (ICD-10 -> ICD-9), and the module-level
  :func:`forward` / :func:`backward` conveniences over the landed table.
* **The DAG step** ``codesets.gem`` (:func:`run_gem`; ``dag/specs/codesets.yaml``) writes
  ``lake/meta/<tier>/gem_i9_to_i10.parquet`` and ``gem_i10_to_i9.parquet`` — one row per
  GEM line, both kinds, with the flags decoded — which EP-37's walker registers as
  ``meta.gem_i9_to_i10`` / ``meta.gem_i10_to_i9`` (registry tables, printable through
  ``mwh sql``). While nothing is landed the step logs a warning and writes nothing (the
  session fixture lake builds without a download).
* **The review** (:func:`build_review` / :func:`render_review`): for a dual ICD set,
  expand its declared members against the tier dictionary, map every ICD-9 code forward and
  every ICD-10 code backward, drop the proposals the set already covers (an exact member or
  a declared prefix) and write ``<id>@<version>.gem-review.md`` — proposed code, the
  dictionary title, the source codes that proposed it, the flags — plus the no-map and
  unmapped sources, under ``studies/codesets/reviews/`` (``--out`` elsewhere), through
  :func:`fsio.atomic_write_text`.

Everything read, written or printed is vocabulary text (public codes and titles) — never a
patient-level row (GOVERNANCE §4). Import budget: stdlib + yaml + pydantic (the ``codeset``
sub-app imports this module at ``mwh`` start-up); duckdb and the registry / safe modules
load inside function bodies.
"""

from __future__ import annotations

import hashlib
import logging
import re
import shutil
import time
import urllib.error
import urllib.request
import zipfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, BinaryIO, Literal, cast

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from mimicwarehouse import fsio, publish

if TYPE_CHECKING:  # pragma: no cover
    from mimicwarehouse.codesets.registry import Dictionaries, Entry, MemberRow
    from mimicwarehouse.config import Settings
    from mimicwarehouse.dag.runner import StepContext, StepOutcome
    from mimicwarehouse.dag.spec import Step

_LOG = logging.getLogger(__name__)

#: The final CMS release; the landing directory's version segment.
GEM_VERSION = "2018"
VOCAB_DIRNAME = "vocab"
GEM_DIRNAME = "gem"
REGISTER_FILENAME = "source.yaml"
GEM_ARCHIVE_PAGE = (
    "https://www.cms.gov/medicare/coding-billing/icd-10-codes/icd-10-cm-icd-10-pcs-gem-archive"
)
LICENSE_ID = "US public domain (CMS)"
#: PhysioNet's / CMS's CDNs answer plain library user agents inconsistently (EP-22 precedent).
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) mimicwarehouse-gem-fetch"
RETRIES = 3
RETRY_BASE_SLEEP_S = 0.5
_PART_SUFFIX = ".part"

GemKind = Literal["dx", "px"]
Direction = Literal["i9_to_i10", "i10_to_i9"]
KINDS: tuple[str, ...] = ("dx", "px")
#: Code-set kind -> GEM kind.
GEM_KIND_OF_CODESET: dict[str, str] = {"icd_dx": "dx", "icd_px": "px"}


@dataclass(frozen=True, slots=True)
class GemFileSpec:
    """One text member of a GEM zip and its pinned content hash."""

    name: str
    kind: GemKind
    direction: Direction
    sha256: str


@dataclass(frozen=True, slots=True)
class GemArchive:
    """One CMS zip: where it is served, its sha256 as fetched on 2026-09-06, its members."""

    kind: GemKind
    filename: str
    url: str
    sha256: str
    files: tuple[GemFileSpec, ...]


#: The two 2018 archives (CMS "ICD-10 Files & News Archive"; hashes recorded from the
#: 2026-09-06 download). Tests monkeypatch this tuple with crafted archives.
GEM_ARCHIVES: tuple[GemArchive, ...] = (
    GemArchive(
        kind="dx",
        filename="2018-icd-10-cm-general-equivalence-mappings.zip",
        url=(
            "https://www.cms.gov/medicare/coding/icd10/downloads/"
            "2018-icd-10-cm-general-equivalence-mappings.zip"
        ),
        sha256="1c5e5f14026ace48437a0d1c485d282fb5d171c357c6ab4f7798fc0a1e3624a2",
        files=(
            GemFileSpec(
                "2018_I9gem.txt",
                "dx",
                "i9_to_i10",
                "44f4079c698efa2c0a8cc159f70075cf6cf23277fdd35ae3416b2bc516652ce3",
            ),
            GemFileSpec(
                "2018_I10gem.txt",
                "dx",
                "i10_to_i9",
                "fe679ddbdc2d3e2275574299ca8c2a5e21e0143136a6ae6290cc7dcec1042f2b",
            ),
        ),
    ),
    GemArchive(
        kind="px",
        filename="2018-icd-10-pcs-general-equivalence-mappings.zip",
        url=(
            "https://www.cms.gov/medicare/coding/icd10/downloads/"
            "2018-icd-10-pcs-general-equivalence-mappings.zip"
        ),
        sha256="7410a56e04411030739bfa2d300ce7e42ea23e8f9281c45d04dc4d6c678f9db0",
        files=(
            GemFileSpec(
                "gem_i9pcs.txt",
                "px",
                "i9_to_i10",
                "818fa8414ea43f8e523d5d87d3ba3033c111a9246ed794d2e59c70767b145c44",
            ),
            GemFileSpec(
                "gem_pcsi9.txt",
                "px",
                "i10_to_i9",
                "81594417f81fb88f722d3074246e5537398d2cd6863118112244d21cfb174b2b",
            ),
        ),
    ),
)

NO_MAP_TARGETS: frozenset[str] = frozenset({"NODX", "NOPCS"})
_LINE_RE = re.compile(r"^(\S+)\s+(\S+)\s+([01])([01])([01])(\d)(\d)$")


class GemError(RuntimeError):
    """The GEM files are missing, malformed, or the fetch cannot proceed (file names,
    hashes and counts only — never content)."""


# ---------------------------------------------------------------------------
# Parsing and the mapping table
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GemEntry:
    """One GEM line: ``target`` is None for a no-map line (``NoDx`` / ``NoPCS``)."""

    source: str
    target: str | None
    approximate: bool
    no_map: bool
    combination: bool
    scenario: int
    choice_list: int

    @property
    def flags(self) -> str:
        """The five flag digits as written."""
        return (
            f"{int(self.approximate)}{int(self.no_map)}{int(self.combination)}"
            f"{self.scenario}{self.choice_list}"
        )


def _norm(code: str) -> str:
    return code.strip().upper().replace(".", "")


def parse_gem_text(text: str, *, where: str = "<gem>") -> list[GemEntry]:
    """Every ``<source> <target> <flags>`` line of a GEM file as :class:`GemEntry` rows;
    blank lines and ``#`` comments are skipped; anything else is a :class:`GemError`."""
    entries: list[GemEntry] = []
    for line_no, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _LINE_RE.match(line)
        if m is None:
            raise GemError(f"{where}:{line_no}: not a '<source> <target> <5 flag digits>' GEM line")
        target = _norm(m.group(2))
        entries.append(
            GemEntry(
                source=_norm(m.group(1)),
                target=None if target in NO_MAP_TARGETS else target,
                approximate=m.group(3) == "1",
                no_map=m.group(4) == "1",
                combination=m.group(5) == "1",
                scenario=int(m.group(6)),
                choice_list=int(m.group(7)),
            )
        )
    return entries


def _index(entries: Sequence[GemEntry]) -> dict[str, tuple[GemEntry, ...]]:
    grouped: dict[str, list[GemEntry]] = {}
    for entry in entries:
        grouped.setdefault(entry.source, []).append(entry)
    return {source: tuple(rows) for source, rows in grouped.items()}


class GemTable:
    """Both directions of one GEM kind, indexed by source code."""

    def __init__(
        self,
        kind: str,
        version: str,
        i9_to_i10: Sequence[GemEntry],
        i10_to_i9: Sequence[GemEntry],
    ) -> None:
        self.kind = kind
        self.version = version
        self.i9_to_i10: tuple[GemEntry, ...] = tuple(i9_to_i10)
        self.i10_to_i9: tuple[GemEntry, ...] = tuple(i10_to_i9)
        self._forward = _index(self.i9_to_i10)
        self._backward = _index(self.i10_to_i9)

    def forward(self, codes: Iterable[str]) -> dict[str, tuple[GemEntry, ...]]:
        """ICD-9 -> ICD-10: the GEM entries of each (normalised) code; an unknown code maps
        to an empty tuple."""
        return {_norm(c): self._forward.get(_norm(c), ()) for c in codes}

    def backward(self, codes: Iterable[str]) -> dict[str, tuple[GemEntry, ...]]:
        """ICD-10 -> ICD-9 (see :meth:`forward`)."""
        return {_norm(c): self._backward.get(_norm(c), ()) for c in codes}

    @staticmethod
    def targets(mapping: dict[str, tuple[GemEntry, ...]]) -> set[str]:
        """The distinct mapped codes of a :meth:`forward` / :meth:`backward` result."""
        return {e.target for rows in mapping.values() for e in rows if e.target is not None}

    @property
    def n_forward(self) -> int:
        return len(self.i9_to_i10)

    @property
    def n_backward(self) -> int:
        return len(self.i10_to_i9)


def gem_root(settings: Settings, version: str = GEM_VERSION) -> Path:
    """``<data_root>/ext/vocab/gem/<version>/`` — the landing directory."""
    return settings.layout["ext"] / VOCAB_DIRNAME / GEM_DIRNAME / version


def _archives(archives: Sequence[GemArchive] | None) -> Sequence[GemArchive]:
    """``archives`` or the module's :data:`GEM_ARCHIVES`, looked up at call time (tests
    monkeypatch the tuple; a definition-time default would bind the original)."""
    return GEM_ARCHIVES if archives is None else archives


def file_specs(kind: str, archives: Sequence[GemArchive] | None = None) -> tuple[GemFileSpec, ...]:
    """The ``(i9_to_i10, i10_to_i9)`` file specs of ``kind``."""
    for archive in _archives(archives):
        if archive.kind == kind:
            by_direction = {f.direction: f for f in archive.files}
            return (by_direction["i9_to_i10"], by_direction["i10_to_i9"])
    raise GemError(f"unknown GEM kind {kind!r}; expected one of {', '.join(KINDS)}")


def landed_kinds(root: Path, archives: Sequence[GemArchive] | None = None) -> tuple[str, ...]:
    """The kinds whose two text files exist under ``root`` (no hashing)."""
    return tuple(
        a.kind for a in _archives(archives) if all((Path(root) / f.name).is_file() for f in a.files)
    )


def load_gem(
    kind: str,
    *,
    root: Path | None = None,
    settings: Settings | None = None,
    version: str = GEM_VERSION,
    archives: Sequence[GemArchive] | None = None,
) -> GemTable:
    """Parse the landed files of ``kind`` into a :class:`GemTable`; :class:`GemError` with
    the remedy when they are not landed."""
    if root is None:
        from mimicwarehouse.config import get_settings

        root = gem_root(settings or get_settings(), version)
    forward_spec, backward_spec = file_specs(kind, archives)
    tables: list[list[GemEntry]] = []
    for spec in (forward_spec, backward_spec):
        path = Path(root) / spec.name
        if not path.is_file():
            raise GemError(
                f"GEM file {spec.name} not landed under {root} — run `mwh codeset gem fetch` "
                "(or place the CMS 2018 GEM text files there by hand, EP-40)"
            )
        tables.append(
            parse_gem_text(path.read_text(encoding="utf-8", errors="replace"), where=spec.name)
        )
    return GemTable(kind, version, tables[0], tables[1])


def forward(
    codes: Iterable[str],
    *,
    kind: str = "dx",
    table: GemTable | None = None,
    settings: Settings | None = None,
) -> dict[str, tuple[GemEntry, ...]]:
    """ICD-9 -> ICD-10 over ``table`` (or the landed GEM of ``kind``)."""
    return (table or load_gem(kind, settings=settings)).forward(codes)


def backward(
    codes: Iterable[str],
    *,
    kind: str = "dx",
    table: GemTable | None = None,
    settings: Settings | None = None,
) -> dict[str, tuple[GemEntry, ...]]:
    """ICD-10 -> ICD-9 over ``table`` (or the landed GEM of ``kind``)."""
    return (table or load_gem(kind, settings=settings)).backward(codes)


# ---------------------------------------------------------------------------
# The register (source.yaml) and the fetch
# ---------------------------------------------------------------------------


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class GemFileRecord(_Frozen):
    name: str
    kind: str
    direction: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    bytes: int = Field(ge=0)


class GemArchiveRecord(_Frozen):
    name: str
    url: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    bytes: int = Field(ge=0)
    files: tuple[str, ...]


class GemRegister(_Frozen):
    """``ext/vocab/gem/<version>/source.yaml`` — EP-14's landing template."""

    name: str = "cms-gem"
    version: str
    release_date: str
    url: str
    license: str
    license_url: str | None = None
    registration_required: bool = False
    redistributable: bool = True
    obtained_on: str
    obtained_by: str
    archives: tuple[GemArchiveRecord, ...]
    files: tuple[GemFileRecord, ...]
    columns_of_interest: tuple[str, ...]
    used_by_eps: tuple[str, ...]
    notes: str = ""


#: The FY2018 GEMs (the PCS zip is dated 2017-08-03 on the CMS archive page).
RELEASE_DATE = "2017-08-03"
COLUMNS_OF_INTEREST: tuple[str, ...] = (
    "source",
    "target",
    "approximate",
    "no_map",
    "combination",
    "scenario",
    "choice_list",
)
USED_BY_EPS: tuple[str, ...] = ("EP-40", "EP-41", "EP-46")


def register_path(root: Path) -> Path:
    return Path(root) / REGISTER_FILENAME


def load_register(path: Path) -> GemRegister:
    """Parse and validate a ``source.yaml`` (:class:`GemError` when malformed)."""
    try:
        doc = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        return GemRegister.model_validate(doc)
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise GemError(f"{path}: not a valid GEM register ({exc})") from exc


def write_register(path: Path, register: GemRegister) -> Path:
    """Write the register atomically (:func:`fsio.atomic_write_text`; tag-free YAML)."""
    payload = register.model_dump(mode="json")
    text = yaml.safe_dump(payload, sort_keys=False, allow_unicode=False, width=100)
    fsio.atomic_write_text(Path(path), text)
    return Path(path)


def _urlopen(url: str) -> BinaryIO:  # tests monkeypatch this — the single network seam
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    return cast("BinaryIO", urllib.request.urlopen(request, timeout=120))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as f:
        while chunk := f.read(8 * 2**20):
            digest.update(chunk)
    return digest.hexdigest()


def _download(url: str, target: Path) -> None:
    """Stream ``url`` to ``target`` via a ``.part`` sibling, with retries; the final
    rename happens only after a complete read (the EP-22 shape)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + _PART_SUFFIX)
    last: Exception | None = None
    for attempt in range(RETRIES):
        try:
            with _urlopen(url) as response, part.open("wb") as out:
                shutil.copyfileobj(response, out)
            publish.replace(part, target)
            return
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            last = exc
            part.unlink(missing_ok=True)
            if attempt < RETRIES - 1:
                time.sleep(RETRY_BASE_SLEEP_S * (attempt + 1))
    raise GemError(f"cannot download {url} after {RETRIES} attempt(s): {last}")


def _zip_member(names: Sequence[str], wanted: str) -> str:
    """The archive member named ``wanted`` (at the root or under one directory)."""
    for name in names:
        pure = PurePosixPath(name.replace("\\", "/"))
        if pure.is_absolute() or ".." in pure.parts:
            continue
        if pure.name == wanted:
            return name
    raise GemError(f"zip has no member {wanted!r}; members: {', '.join(sorted(names)[:8])}")


def _extract(zip_path: Path, root: Path, specs: Sequence[GemFileSpec]) -> None:
    """Extract exactly the expected members into ``root`` (temp file + publish.replace)."""
    try:
        with zipfile.ZipFile(zip_path) as archive:
            names = archive.namelist()
            for spec in specs:
                member = _zip_member(names, spec.name)
                dest = Path(root) / spec.name
                tmp = dest.with_name(dest.name + _PART_SUFFIX)
                with archive.open(member) as src, tmp.open("wb") as out:
                    shutil.copyfileobj(src, out)
                publish.replace(tmp, dest)
    except zipfile.BadZipFile as exc:
        raise GemError(f"{zip_path.name}: not a zip archive ({exc})") from exc


def verify_files(root: Path, specs: Iterable[GemFileSpec]) -> dict[str, bool]:
    """``{file name: sha256 matches the pin}`` for the specs' files under ``root``
    (missing = False)."""
    out: dict[str, bool] = {}
    for spec in specs:
        path = Path(root) / spec.name
        out[spec.name] = path.is_file() and _sha256_file(path) == spec.sha256
    return out


@dataclass(slots=True)
class FetchCounts:
    """What one archive fetch did (counts only)."""

    downloaded: int = 0
    skipped: int = 0
    bytes: int = 0
    archives: list[str] = field(default_factory=list)


def fetch(
    settings: Settings,
    *,
    force: bool = False,
    root: Path | None = None,
    archives: Sequence[GemArchive] | None = None,
    obtained_by: str | None = None,
) -> tuple[GemRegister, FetchCounts]:
    """Download + verify the GEM archives into the landing directory and write the
    register (module docstring). Files already verified are skipped unless ``force``; a
    verification failure deletes the file and raises :class:`GemError` before the register
    is (re)written."""
    from mimicwarehouse.config import require_free_space

    require_free_space(settings.data_root, settings.min_free_gb)
    target_root = Path(root) if root is not None else gem_root(settings)
    target_root.mkdir(parents=True, exist_ok=True)
    counts = FetchCounts()
    archive_records: list[GemArchiveRecord] = []
    file_records: list[GemFileRecord] = []
    for archive in _archives(archives):
        zip_path = target_root / archive.filename
        verified = verify_files(target_root, archive.files)
        if all(verified.values()) and not force and zip_path.is_file():
            counts.skipped += len(archive.files)
        else:
            _download(archive.url, zip_path)
            actual = _sha256_file(zip_path)
            if actual != archive.sha256:
                _LOG.warning(
                    "%s: archive sha256 %s differs from the recorded %s — CMS may have "
                    "re-packaged it; the extracted files are verified individually",
                    archive.filename,
                    actual[:12],
                    archive.sha256[:12],
                )
            _extract(zip_path, target_root, archive.files)
            for spec in archive.files:
                path = target_root / spec.name
                got = _sha256_file(path)
                if got != spec.sha256:
                    publish.unlink(path)
                    raise GemError(
                        f"{spec.name}: sha256 mismatch (expected {spec.sha256}, got {got}) — "
                        "file deleted; rerun `mwh codeset gem fetch` (CMS content changed or "
                        "the download was corrupted?)"
                    )
                counts.downloaded += 1
        counts.archives.append(archive.filename)
        archive_records.append(
            GemArchiveRecord(
                name=archive.filename,
                url=archive.url,
                sha256=_sha256_file(zip_path),
                bytes=zip_path.stat().st_size,
                files=tuple(f.name for f in archive.files),
            )
        )
        for spec in archive.files:
            path = target_root / spec.name
            size = path.stat().st_size
            counts.bytes += size
            file_records.append(
                GemFileRecord(
                    name=spec.name,
                    kind=spec.kind,
                    direction=spec.direction,
                    sha256=spec.sha256,
                    bytes=size,
                )
            )
    register = GemRegister(
        version=GEM_VERSION,
        release_date=RELEASE_DATE,
        url=GEM_ARCHIVE_PAGE,
        license=LICENSE_ID,
        obtained_on=datetime.now(UTC).date().isoformat(),
        obtained_by=obtained_by or settings.role,
        archives=tuple(archive_records),
        files=tuple(file_records),
        columns_of_interest=COLUMNS_OF_INTEREST,
        used_by_eps=USED_BY_EPS,
        notes=(
            "The 2018 (FY2018) General Equivalence Mappings are the final CMS release; "
            "diagnoses (ICD-9-CM <-> ICD-10-CM) and procedures (ICD-9-CM <-> ICD-10-PCS). "
            "Fetched by `mwh codeset gem fetch` (EP-40); every text file verified against "
            "the sha256 pinned in mimicwarehouse.codesets.gem.GEM_ARCHIVES."
        ),
    )
    write_register(register_path(target_root), register)
    return register, counts


# ---------------------------------------------------------------------------
# The DAG step — meta.gem_i9_to_i10 / meta.gem_i10_to_i9
# ---------------------------------------------------------------------------

STEP_GEM = "codesets.gem"
I9_TO_I10_TABLE = "gem_i9_to_i10"
I10_TO_I9_TABLE = "gem_i10_to_i9"
GEM_COLUMNS: tuple[tuple[str, str], ...] = (
    ("kind", "VARCHAR"),
    ("source", "VARCHAR"),
    ("target", "VARCHAR"),
    ("approximate", "BOOLEAN"),
    ("no_map", "BOOLEAN"),
    ("combination", "BOOLEAN"),
    ("scenario", "INTEGER"),
    ("choice_list", "INTEGER"),
    ("gem_version", "VARCHAR"),
    ("tier", "VARCHAR"),
    ("build_id", "VARCHAR"),
)
GEM_META_COMMENTS: dict[str, str] = {
    I9_TO_I10_TABLE: (
        "CMS 2018 General Equivalence Mappings, ICD-9-CM -> ICD-10-CM (kind dx) and "
        "ICD-9-CM -> ICD-10-PCS (kind px): one row per GEM line — source, target (NULL "
        "for a no-map line), the decoded flags approximate / no_map / combination and the "
        "scenario / choice_list integers (codesets/gem.py, EP-40). A GEM is not a "
        "crosswalk: one-to-many and combination entries need a human review."
    ),
    I10_TO_I9_TABLE: (
        "CMS 2018 General Equivalence Mappings, ICD-10-CM -> ICD-9-CM (kind dx) and "
        "ICD-10-PCS -> ICD-9-CM (kind px): one row per GEM line, same columns as "
        "meta.gem_i9_to_i10 (codesets/gem.py, EP-40)."
    ),
}


def gem_rows(
    entries: Iterable[GemEntry], *, kind: str, version: str, tier: str, build_id: str
) -> list[list[Any]]:
    return [
        [
            kind,
            e.source,
            e.target,
            e.approximate,
            e.no_map,
            e.combination,
            e.scenario,
            e.choice_list,
            version,
            tier,
            build_id,
        ]
        for e in entries
    ]


def run_gem(step: Step, ctx: StepContext) -> StepOutcome:
    """The ``codesets.gem`` handler (module docstring): the landed kinds -> the two meta
    files; a warning and no files while nothing is landed."""
    from mimicwarehouse.dag.runner import StepOutcome
    from mimicwarehouse.units import meta_table_path, write_meta_parquet

    root = gem_root(ctx.settings)
    kinds = landed_kinds(root)
    if not kinds:
        _LOG.warning(
            "GEM not landed under %s — run `mwh codeset gem fetch` (EP-40); "
            "meta.gem_i9_to_i10 / meta.gem_i10_to_i9 are not written for tier %s",
            root,
            ctx.tier,
        )
        return StepOutcome()
    forward_rows: list[list[Any]] = []
    backward_rows: list[list[Any]] = []
    for kind in kinds:
        table = load_gem(kind, root=root)
        forward_rows += gem_rows(
            table.i9_to_i10, kind=kind, version=table.version, tier=ctx.tier, build_id=ctx.build_id
        )
        backward_rows += gem_rows(
            table.i10_to_i9, kind=kind, version=table.version, tier=ctx.tier, build_id=ctx.build_id
        )
    fwd_dest = meta_table_path(ctx.lake_root, ctx.tier, I9_TO_I10_TABLE)
    bwd_dest = meta_table_path(ctx.lake_root, ctx.tier, I10_TO_I9_TABLE)
    fwd_bytes = write_meta_parquet(ctx.con, fwd_dest, GEM_COLUMNS, forward_rows)
    bwd_bytes = write_meta_parquet(ctx.con, bwd_dest, GEM_COLUMNS, backward_rows)
    _LOG.info(
        "meta.gem_i9_to_i10 / meta.gem_i10_to_i9 (%s): %d + %d row(s), kinds %s — %s",
        ctx.tier,
        len(forward_rows),
        len(backward_rows),
        ", ".join(kinds),
        fwd_dest.parent,
    )
    return StepOutcome(
        rows=len(forward_rows) + len(backward_rows), bytes_out=fwd_bytes + bwd_bytes, files=2
    )


# ---------------------------------------------------------------------------
# The review file — `mwh codeset expand <id@version> --via-gem`
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Proposal:
    """One proposed counterpart code: its dictionary title, the source codes that map to
    it, and the GEM flags seen (any approximate / combination; the scenario/choice pairs)."""

    code: str
    label: str | None
    sources: tuple[str, ...]
    approximate: bool
    combination: bool
    scenarios: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DirectionReview:
    """One mapping direction of a :class:`Review`."""

    direction: str
    from_system: str
    to_system: str
    expanded: tuple[str, ...]
    proposals: tuple[Proposal, ...]
    covered: int
    no_map: tuple[str, ...]
    unmapped: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Review:
    ref: str
    def_hash: str
    kind: str
    gem_version: str
    tier: str
    generated_at: str
    forward: DirectionReview
    backward: DirectionReview


def _expanded_codes(rows: Sequence[MemberRow], system: str) -> tuple[str, ...]:
    """The concrete codes of one system: dictionary hits of prefix rules plus every exact
    code (matched or not — an exact code the dictionary lacks still maps in the GEM)."""
    codes = {
        r.code for r in rows if r.system == system and (r.match_kind == "exact" or bool(r.matched))
    }
    return tuple(sorted(codes))


def _covers(code: str, exact: set[str], prefixes: Sequence[str]) -> bool:
    return code in exact or any(code.startswith(p) for p in prefixes)


def _direction(
    *,
    direction: str,
    from_system: str,
    to_system: str,
    sources: Sequence[str],
    mapping: dict[str, tuple[GemEntry, ...]],
    covered_exact: set[str],
    covered_prefixes: Sequence[str],
    labels: dict[str, str | None],
) -> DirectionReview:
    proposals: dict[str, dict[str, Any]] = {}
    covered = 0
    no_map: list[str] = []
    unmapped: list[str] = []
    for source in sources:
        entries = mapping.get(source, ())
        if not entries:
            unmapped.append(source)
            continue
        for entry in entries:
            if entry.target is None:
                no_map.append(source)
                continue
            if _covers(entry.target, covered_exact, covered_prefixes):
                covered += 1
                continue
            slot = proposals.setdefault(
                entry.target,
                {"sources": set(), "approximate": False, "combination": False, "scenarios": set()},
            )
            slot["sources"].add(source)
            slot["approximate"] = slot["approximate"] or entry.approximate
            slot["combination"] = slot["combination"] or entry.combination
            if entry.combination:
                slot["scenarios"].add(f"{entry.scenario}/{entry.choice_list}")
    return DirectionReview(
        direction=direction,
        from_system=from_system,
        to_system=to_system,
        expanded=tuple(sources),
        proposals=tuple(
            Proposal(
                code=code,
                label=labels.get(code),
                sources=tuple(sorted(slot["sources"])),
                approximate=bool(slot["approximate"]),
                combination=bool(slot["combination"]),
                scenarios=tuple(sorted(slot["scenarios"])),
            )
            for code, slot in sorted(proposals.items())
        ),
        covered=covered,
        no_map=tuple(sorted(set(no_map))),
        unmapped=tuple(unmapped),
    )


def build_review(
    entry: Entry,
    *,
    table: GemTable,
    rows: Sequence[MemberRow],
    dictionaries: Dictionaries,
    tier: str,
) -> Review:
    """Map a dual ICD set's expanded members both ways and keep what the set does not
    cover yet (module docstring). ``rows`` are the set's compiled rows against the
    tier's dictionaries (:func:`mimicwarehouse.codesets.registry.expand`)."""
    codeset = entry.codeset
    if codeset.kind not in GEM_KIND_OF_CODESET:
        raise GemError(f"{codeset.ref}: kind {codeset.kind} has no ICD-9/10 members to expand")
    icd9 = _expanded_codes(rows, "icd9")
    icd10 = _expanded_codes(rows, "icd10")
    prefixes9 = [e.code for e in codeset.members.icd9 if e.match == "prefix"]
    prefixes10 = [e.code for e in codeset.members.icd10 if e.match == "prefix"]
    dict9 = dictionaries.get(codeset.kind, "icd9")
    dict10 = dictionaries.get(codeset.kind, "icd10")
    labels9 = dict9.labels if dict9 is not None else {}
    labels10 = dict10.labels if dict10 is not None else {}
    forward_review = _direction(
        direction="i9_to_i10",
        from_system="icd9",
        to_system="icd10",
        sources=icd9,
        mapping=table.forward(icd9),
        covered_exact=set(icd10),
        covered_prefixes=prefixes10,
        labels=labels10,
    )
    backward_review = _direction(
        direction="i10_to_i9",
        from_system="icd10",
        to_system="icd9",
        sources=icd10,
        mapping=table.backward(icd10),
        covered_exact=set(icd9),
        covered_prefixes=prefixes9,
        labels=labels9,
    )
    return Review(
        ref=codeset.ref,
        def_hash=codeset.def_hash,
        kind=table.kind,
        gem_version=table.version,
        tier=tier,
        generated_at=datetime.now(UTC).replace(microsecond=0).isoformat(),
        forward=forward_review,
        backward=backward_review,
    )


def _md_escape(text: str) -> str:
    return text.replace("|", "\\|")


def _render_direction(review: DirectionReview) -> list[str]:
    arrow = "ICD-9 -> ICD-10" if review.direction == "i9_to_i10" else "ICD-10 -> ICD-9"
    lines = [
        f"## {arrow}",
        "",
        f"{len(review.expanded)} {review.from_system} code(s) after expansion; "
        f"{len(review.proposals)} proposed {review.to_system} code(s) not yet in the set; "
        f"{review.covered} mapping(s) already covered; {len(review.no_map)} no-map source(s); "
        f"{len(review.unmapped)} source(s) absent from the GEM.",
        "",
    ]
    if review.proposals:
        lines += [
            f"| proposed {review.to_system} | title | from | flags |",
            "|---|---|---|---|",
        ]
        for p in review.proposals:
            flags = []
            if p.approximate:
                flags.append("approximate")
            if p.combination:
                flags.append("combination " + ", ".join(p.scenarios))
            lines.append(
                f"| `{p.code}` | {_md_escape(p.label or '')} | {', '.join(p.sources)} | "
                f"{'; '.join(flags) or 'exact'} |"
            )
        lines.append("")
    if review.no_map:
        lines += [f"No-map sources ({review.from_system}): " + ", ".join(review.no_map), ""]
    if review.unmapped:
        lines += [
            f"Sources absent from the GEM ({review.from_system}): " + ", ".join(review.unmapped),
            "",
        ]
    return lines


def render_review(review: Review) -> str:
    """The ``.gem-review.md`` text: header, both directions, the fold-in instructions."""
    lines = [
        f"# GEM review: {review.ref}",
        "",
        f"- code set: `{review.ref}` (def_hash `{review.def_hash[:12]}`)",
        f"- GEM: CMS {review.gem_version}, kind `{review.kind}`; dictionary tier `{review.tier}`",
        f"- generated: {review.generated_at} by `mwh codeset expand --via-gem` (EP-40)",
        "",
        "A GEM is not a crosswalk: approximate, one-to-many and combination entries are "
        "proposals for a human to accept or reject. Fold accepted codes into a **new "
        "version** of the YAML (the released version is frozen), then `mwh codeset lock` "
        "and `mwh codeset compile`. Public vocabulary text only; no patient data.",
        "",
        *_render_direction(review.forward),
        *_render_direction(review.backward),
    ]
    return "\n".join(lines).rstrip("\n") + "\n"


def review_path(settings: Settings, ref: str) -> Path:
    """``<data_root>/studies/codesets/reviews/<id>@<version>.gem-review.md``."""
    return settings.layout["studies"] / "codesets" / "reviews" / f"{ref}.gem-review.md"


def write_review(path: Path, text: str) -> Path:
    """Write the review atomically (:func:`fsio.atomic_write_text`)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fsio.atomic_write_text(path, text)
    return path


__all__ = [
    "COLUMNS_OF_INTEREST",
    "GEM_ARCHIVES",
    "GEM_ARCHIVE_PAGE",
    "GEM_COLUMNS",
    "GEM_DIRNAME",
    "GEM_KIND_OF_CODESET",
    "GEM_META_COMMENTS",
    "GEM_VERSION",
    "I9_TO_I10_TABLE",
    "I10_TO_I9_TABLE",
    "KINDS",
    "LICENSE_ID",
    "NO_MAP_TARGETS",
    "REGISTER_FILENAME",
    "RELEASE_DATE",
    "STEP_GEM",
    "USED_BY_EPS",
    "VOCAB_DIRNAME",
    "DirectionReview",
    "FetchCounts",
    "GemArchive",
    "GemArchiveRecord",
    "GemEntry",
    "GemError",
    "GemFileRecord",
    "GemFileSpec",
    "GemRegister",
    "GemTable",
    "Proposal",
    "Review",
    "backward",
    "build_review",
    "fetch",
    "file_specs",
    "forward",
    "gem_root",
    "gem_rows",
    "landed_kinds",
    "load_gem",
    "load_register",
    "parse_gem_text",
    "register_path",
    "render_review",
    "review_path",
    "run_gem",
    "verify_files",
    "write_register",
    "write_review",
]
