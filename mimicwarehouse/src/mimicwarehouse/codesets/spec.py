"""The code-set schema: YAML code sets with semver + a definition hash (EP-40 item 1;
DESIGN §8, §15; GOVERNANCE §12).

A **code set** is one YAML document (``defs/<id>.yaml`` in the package, or a study file
passed by path) that names a clinical concept by codes, never by data::

    id: aki
    version: 1.0.0
    name: Acute kidney injury (diagnosis codes)
    kind: icd_dx
    members:
      icd9:  [{code: "584", match: prefix}]
      icd10: [{code: "N17", match: prefix}]
    provenance: {source: hand, accessed: 2026-09-06}

* ``kind`` decides which member systems the set must and may carry
  (:data:`KIND_FIELDS`): ``icd_dx`` / ``icd_px`` are **dual** — both an ``icd9`` and an
  ``icd10`` list, because BIDMC switched coding around 2015 and MIMIC-IV admissions span
  both eras (DESIGN §7; the switch is per row, ``icd_version``, never a date) — ``itemid``
  sets list ``itemids``, ``drug`` sets carry ``drugs: {names, match, rxnorm}`` (plus the
  optional ICU ``itemids`` of the same agents), ``atc`` / ``loinc`` / ``hcpcs`` list their
  classes / codes. ICD and HCPCS entries are ``{code, match: exact|prefix, group}`` (a bare
  string is an exact code); ``group`` labels the category inside a grouped set such as the
  17 Charlson categories — all entries of a grouped set carry one.
* **Normalisation** (:func:`normalize_code`): codes are upper-cased, stripped and lose their
  dots (MIMIC stores ICD codes without dots); drug names lose surrounding / repeated
  whitespace and are upper-cased unless the match kind is ``regex`` (patterns stay
  verbatim); each list is de-duplicated and sorted, so member *order* never matters.
  Codes must be YAML **strings** — ``0010`` unquoted would parse as an octal integer and
  lose its leading zero, so an integer code is refused with that message.
* **def_hash** (:attr:`CodeSet.def_hash`): sha256 of the canonical JSON — key-sorted,
  whitespace-free — of ``{"kind": …, "members": …}`` with the normalised members, so the
  hash is invariant to YAML key order, whitespace, code dots, list order and the
  ``match: exact`` default, and changes exactly when the *definition* changes. ``name``,
  ``description``, ``provenance``, ``references`` and ``notes`` are documentation and do
  not enter the hash.
* ``version`` is semver (``MAJOR.MINOR.PATCH``); the ``(id, version)`` pair is immutable
  once recorded in the registry lock (:mod:`~mimicwarehouse.codesets.registry` raises
  :class:`CodeSetFrozenError` when a locked pair's hash moved — bump the version).
* References are DOIs / stable ``https://`` URLs (``docs/committed-text.md`` rule 6 — a
  bare PMID is refused).

Everything here is schema and dictionary text; no data access. Import budget: pydantic +
yaml + stdlib only (the ``codeset`` sub-app imports this module at ``mwh`` start-up).
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

Kind = Literal["icd_dx", "icd_px", "itemid", "drug", "atc", "loinc", "hcpcs"]
KINDS: tuple[str, ...] = ("icd_dx", "icd_px", "itemid", "drug", "atc", "loinc", "hcpcs")
CodeMatch = Literal["exact", "prefix"]
DrugMatch = Literal["contains", "regex", "exact"]
ProvenanceSource = Literal["mimic-code", "AHRQ-CCSR", "Charlson", "Elixhauser", "hand"]

#: The member fields of :class:`Members`, canonical order.
MEMBER_FIELDS: tuple[str, ...] = ("icd9", "icd10", "itemids", "drugs", "atc", "loinc", "hcpcs")
#: The ``system`` names compiled member rows carry (``drugs`` yields two).
SYSTEMS: tuple[str, ...] = (
    "icd9",
    "icd10",
    "itemid",
    "drug_name",
    "rxnorm",
    "atc",
    "loinc",
    "hcpcs",
)
#: Per kind: (required member fields, optional member fields); anything else is refused.
KIND_FIELDS: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "icd_dx": (frozenset({"icd9", "icd10"}), frozenset()),
    "icd_px": (frozenset({"icd9", "icd10"}), frozenset()),
    "itemid": (frozenset({"itemids"}), frozenset()),
    "drug": (frozenset({"drugs"}), frozenset({"itemids"})),
    "atc": (frozenset({"atc"}), frozenset()),
    "loinc": (frozenset({"loinc"}), frozenset()),
    "hcpcs": (frozenset({"hcpcs"}), frozenset()),
}

_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9-]*$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_REF_RE = re.compile(r"^([a-z][a-z0-9_]*)@((?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*))$")
_PMID_RE = re.compile(r"\bPMID\b", re.IGNORECASE)
_WS_RE = re.compile(r"\s+")


class CodeSetError(ValueError):
    """A code-set YAML is malformed, or a reference / registry operation cannot proceed."""


class CodeSetFrozenError(CodeSetError):
    """An ``(id, version)`` pair recorded in the registry lock now hashes differently —
    the definition changed without a version bump (bump ``version`` instead)."""


class UnknownCodeSetError(CodeSetError, LookupError):
    """No code set with that ``id@version`` in the registry."""


# ---------------------------------------------------------------------------
# Normalisation, references, canonical JSON
# ---------------------------------------------------------------------------


def normalize_code(code: Any) -> str:
    """The canonical spelling of a code: stripped, upper-cased, dots removed
    (``e11.9`` -> ``E119``, ``250.00`` -> ``25000``). An integer is refused — a code such
    as ``0010`` must be quoted in YAML or its leading zero is lost."""
    if isinstance(code, bool) or not isinstance(code, str):
        raise ValueError(
            f"code {code!r} must be a YAML string (quote it: an unquoted 0010 parses as an "
            "octal integer and loses its leading zero)"
        )
    text = code.strip().upper().replace(".", "")
    if not _CODE_RE.match(text):
        raise ValueError(f"code {code!r} is not [A-Z0-9-]+ after normalisation")
    return text


def normalize_name(name: Any, match: str) -> str:
    """A drug name for ``match`` — surrounding and repeated whitespace collapsed, upper-cased
    for ``contains`` / ``exact`` (matching is case-insensitive), verbatim for ``regex``."""
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"drug name {name!r} must be a non-empty string")
    if match == "regex":
        pattern = name.strip()
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ValueError(f"drug pattern {name!r} is not a valid regex ({exc})") from None
        return pattern
    return _WS_RE.sub(" ", name.strip()).upper()


def parse_ref(ref: str) -> tuple[str, str]:
    """``"t2dm@1.0.0"`` -> ``("t2dm", "1.0.0")``; :class:`CodeSetError` otherwise."""
    m = _REF_RE.match(ref.strip()) if isinstance(ref, str) else None
    if m is None:
        raise CodeSetError(
            f"{ref!r} is not a code-set reference; expected <id>@<major>.<minor>.<patch> "
            "(e.g. t2dm@1.0.0)"
        )
    return m.group(1), m.group(2)


def format_ref(codeset_id: str, version: str) -> str:
    return f"{codeset_id}@{version}"


def canonical_json(obj: Any) -> str:
    """Key-sorted, whitespace-free, ASCII JSON — the hashed form."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class CodeEntry(_Frozen):
    """One ICD / HCPCS member: ``code`` (normalised), ``match`` (``exact`` = the code
    itself; ``prefix`` = every dictionary code starting with it) and the optional
    ``group`` of a grouped set."""

    code: str
    match: CodeMatch = "exact"
    group: str | None = None

    @field_validator("code", mode="before")
    @classmethod
    def _norm_code(cls, value: Any) -> str:
        return normalize_code(value)

    @field_validator("group")
    @classmethod
    def _slug(cls, value: str | None) -> str | None:
        if value is not None and not _ID_RE.match(value):
            raise ValueError(f"group {value!r} is not a lower [a-z0-9_] slug")
        return value

    @property
    def sort_key(self) -> tuple[str, str, str]:
        return (self.code, self.match, self.group or "")

    def canonical(self) -> dict[str, str]:
        out = {"code": self.code, "match": self.match}
        if self.group is not None:
            out["group"] = self.group
        return out


def _coerce_entries(raw: Any, field_name: str) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(f"{field_name} must be a list of codes / {{code, match, group}} entries")
    entries: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, dict):
            entries.append(dict(item))
        else:
            entries.append({"code": item})
    return entries


def _dedupe_entries(entries: list[CodeEntry]) -> tuple[CodeEntry, ...]:
    seen: dict[tuple[str, str, str], CodeEntry] = {}
    for entry in entries:
        seen.setdefault(entry.sort_key, entry)
    return tuple(seen[k] for k in sorted(seen))


def _coerce_codes(raw: Any, field_name: str) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ValueError(f"{field_name} must be a list of codes")
    return tuple(sorted({normalize_code(item) for item in raw}))


class DrugMembers(_Frozen):
    """Drug-name members: ``names`` matched by ``match`` against ``prescriptions.drug`` /
    ``emar.medication`` (``contains`` = case-insensitive substring, the mimic-code
    ``LOWER(drug) LIKE '%name%'`` idiom; ``exact`` = the whole normalised name; ``regex`` =
    a case-insensitive pattern), plus optional RxNorm concept ids (``rxnorm``, RXCUI
    digits) — names only here, no table download (D-35)."""

    names: tuple[str, ...] = Field(min_length=1)
    match: DrugMatch = "contains"
    rxnorm: tuple[str, ...] = ()

    @model_validator(mode="before")
    @classmethod
    def _normalise(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            raise ValueError("drugs must be a mapping {names: [...], match: ..., rxnorm: [...]}")
        match = data.get("match", "contains")
        names = data.get("names")
        if not isinstance(names, list) or not names:
            raise ValueError("drugs.names must be a non-empty list")
        normalised = sorted({normalize_name(n, str(match)) for n in names})
        rx_raw = data.get("rxnorm") or []
        if not isinstance(rx_raw, list):
            raise ValueError("drugs.rxnorm must be a list of RXCUI strings")
        rxnorm: list[str] = []
        for cui in rx_raw:
            text = str(cui).strip() if not isinstance(cui, bool) else ""
            if not text.isdigit():
                raise ValueError(f"drugs.rxnorm entry {cui!r} is not an RXCUI (digits)")
            rxnorm.append(text)
        return {**data, "names": normalised, "match": match, "rxnorm": sorted(set(rxnorm))}

    def canonical(self) -> dict[str, Any]:
        out: dict[str, Any] = {"match": self.match, "names": list(self.names)}
        if self.rxnorm:
            out["rxnorm"] = list(self.rxnorm)
        return out


class Members(_Frozen):
    """The members by system (module docstring). Lists are normalised, de-duplicated and
    sorted on load; ``canonical()`` is the hashed shape."""

    icd9: tuple[CodeEntry, ...] = ()
    icd10: tuple[CodeEntry, ...] = ()
    itemids: tuple[int, ...] = ()
    drugs: DrugMembers | None = None
    atc: tuple[str, ...] = ()
    loinc: tuple[str, ...] = ()
    hcpcs: tuple[CodeEntry, ...] = ()

    @model_validator(mode="before")
    @classmethod
    def _coerce(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            raise ValueError("members must be a mapping keyed by system")
        out = dict(data)
        for name in ("icd9", "icd10", "hcpcs"):
            if name in out:
                out[name] = _coerce_entries(out[name], name)
        for name in ("atc", "loinc"):
            if name in out:
                out[name] = _coerce_codes(out[name], name)
        if "itemids" in out:
            raw = out["itemids"] or []
            if not isinstance(raw, list):
                raise ValueError("itemids must be a list of integers")
            ids: set[int] = set()
            for item in raw:
                if isinstance(item, bool) or not isinstance(item, int) or item < 1:
                    raise ValueError(f"itemid {item!r} is not a positive integer")
                ids.add(item)
            out["itemids"] = tuple(sorted(ids))
        return out

    @model_validator(mode="after")
    def _sorted(self) -> Members:
        # de-duplicate + sort the entry tuples after CodeEntry normalised the codes
        # (model_copy skips validation, so this cannot recurse)
        update = {
            name: _dedupe_entries(list(getattr(self, name)))
            for name in ("icd9", "icd10", "hcpcs")
            if getattr(self, name)
        }
        return self.model_copy(update=update) if update else self

    def declared_fields(self) -> tuple[str, ...]:
        """The non-empty member fields in canonical order."""
        out: list[str] = []
        for name in MEMBER_FIELDS:
            present = self.drugs is not None if name == "drugs" else bool(getattr(self, name))
            if present:
                out.append(name)
        return tuple(out)

    def declared_systems(self) -> tuple[str, ...]:
        """The ``system`` names the compiled rows of this set carry, canonical order."""
        out: list[str] = []
        for name in self.declared_fields():
            if name == "drugs":
                out.append("drug_name")
                if self.drugs is not None and self.drugs.rxnorm:
                    out.append("rxnorm")
            elif name == "itemids":
                out.append("itemid")
            else:
                out.append(name)
        return tuple(out)

    @property
    def n_declared(self) -> int:
        n = len(self.icd9) + len(self.icd10) + len(self.itemids) + len(self.atc)
        n += len(self.loinc) + len(self.hcpcs)
        if self.drugs is not None:
            n += len(self.drugs.names) + len(self.drugs.rxnorm)
        return n

    @property
    def groups(self) -> tuple[str, ...]:
        """The distinct ``group`` labels (empty for an ungrouped set), sorted."""
        found = {e.group for e in (*self.icd9, *self.icd10, *self.hcpcs) if e.group is not None}
        return tuple(sorted(found))

    def canonical(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for name in ("icd9", "icd10", "hcpcs"):
            entries: tuple[CodeEntry, ...] = getattr(self, name)
            if entries:
                out[name] = [e.canonical() for e in entries]
        if self.itemids:
            out["itemids"] = list(self.itemids)
        if self.drugs is not None:
            out["drugs"] = self.drugs.canonical()
        if self.atc:
            out["atc"] = list(self.atc)
        if self.loinc:
            out["loinc"] = list(self.loinc)
        return out


class Provenance(_Frozen):
    """Where the members came from: ``source`` (a closed list), the URL or DOI, the file /
    table within the source (``ref``) and the ISO date it was consulted."""

    source: ProvenanceSource
    url: str | None = None
    ref: str | None = None
    accessed: str

    @field_validator("accessed", mode="before")
    @classmethod
    def _iso_date(cls, value: Any) -> str:
        text = value.isoformat() if hasattr(value, "isoformat") else str(value)
        if not _DATE_RE.match(text):
            raise ValueError(f"accessed {value!r} must be an ISO date YYYY-MM-DD")
        return text


class CodeSet(_Frozen):
    """One versioned code set (module docstring)."""

    id: str
    version: str
    name: str = Field(min_length=1)
    description: str = ""
    kind: Kind
    members: Members
    provenance: Provenance
    references: tuple[str, ...] = ()
    notes: str = ""

    @field_validator("id")
    @classmethod
    def _id(cls, value: str) -> str:
        if not _ID_RE.match(value):
            raise ValueError(f"id {value!r} is not a lower [a-z0-9_] slug")
        return value

    @field_validator("version", mode="before")
    @classmethod
    def _semver(cls, value: Any) -> str:
        text = str(value)
        if not isinstance(value, str) or not _SEMVER_RE.match(text):
            raise ValueError(f"version {value!r} must be a quoted semver string MAJOR.MINOR.PATCH")
        return text

    @field_validator("references")
    @classmethod
    def _refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for ref in value:
            if _PMID_RE.search(ref):
                raise ValueError(f"reference {ref!r}: cite a DOI or stable URL, never a bare PMID")
            if not (ref.startswith(("https://", "http://", "doi:"))):
                raise ValueError(f"reference {ref!r} must be an https:// URL or a doi: identifier")
        return value

    @model_validator(mode="after")
    def _kind_rules(self) -> CodeSet:
        required, optional = KIND_FIELDS[self.kind]
        declared = set(self.members.declared_fields())
        missing = sorted(required - declared)
        if missing:
            raise ValueError(
                f"{self.id}@{self.version}: kind {self.kind} requires member system(s) "
                f"{missing}"
                + (
                    " (every diagnosis / procedure set is dual: icd9 and icd10)"
                    if self.kind in ("icd_dx", "icd_px")
                    else ""
                )
            )
        extra = sorted(declared - required - optional)
        if extra:
            raise ValueError(
                f"{self.id}@{self.version}: member system(s) {extra} do not belong to kind "
                f"{self.kind}"
            )
        coded = (*self.members.icd9, *self.members.icd10, *self.members.hcpcs)
        grouped = [e for e in coded if e.group is not None]
        if grouped and len(grouped) != len(coded):
            raise ValueError(
                f"{self.id}@{self.version}: a grouped set gives every ICD / HCPCS entry a "
                f"group ({len(grouped)} of {len(coded)} have one)"
            )
        return self

    @property
    def ref(self) -> str:
        return format_ref(self.id, self.version)

    @property
    def canonical(self) -> dict[str, Any]:
        """The hashed shape: kind + normalised members."""
        return {"kind": self.kind, "members": self.members.canonical()}

    @property
    def def_hash(self) -> str:
        return def_hash_of(self.kind, self.members)

    @property
    def n_declared(self) -> int:
        return self.members.n_declared

    @property
    def groups(self) -> tuple[str, ...]:
        return self.members.groups

    @property
    def declared_systems(self) -> tuple[str, ...]:
        return self.members.declared_systems()


def def_hash_of(kind: str, members: Members) -> str:
    """sha256 of the canonical JSON of ``kind`` + ``members`` (module docstring)."""
    payload = canonical_json({"kind": kind, "members": members.canonical()})
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def _format_validation_error(where: str, exc: ValidationError) -> str:
    lines = [f"{where}: {exc.error_count()} validation error(s)"]
    for e in exc.errors():
        loc = ".".join(str(p) for p in e["loc"])
        lines.append(f"  {loc}: {e['msg']}")
    return "\n".join(lines)


def codeset_from_text(text: str, *, where: str = "<text>") -> CodeSet:
    """Parse one YAML document into a :class:`CodeSet` (:class:`CodeSetError` names every
    validation problem)."""
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise CodeSetError(f"{where}: cannot parse ({exc})") from exc
    if not isinstance(doc, dict):
        raise CodeSetError(f"{where}: top level must be a mapping")
    try:
        return CodeSet.model_validate(doc)
    except ValidationError as exc:
        raise CodeSetError(_format_validation_error(where, exc)) from None


def load_codeset(path: Path | str) -> CodeSet:
    """Parse and validate one code-set YAML file."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CodeSetError(f"{path.name}: cannot read ({exc})") from exc
    return codeset_from_text(text, where=path.name)


__all__ = [
    "KINDS",
    "KIND_FIELDS",
    "MEMBER_FIELDS",
    "SYSTEMS",
    "CodeEntry",
    "CodeMatch",
    "CodeSet",
    "CodeSetError",
    "CodeSetFrozenError",
    "DrugMatch",
    "DrugMembers",
    "Kind",
    "Members",
    "Provenance",
    "ProvenanceSource",
    "UnknownCodeSetError",
    "canonical_json",
    "codeset_from_text",
    "def_hash_of",
    "format_ref",
    "load_codeset",
    "normalize_code",
    "normalize_name",
    "parse_ref",
]
