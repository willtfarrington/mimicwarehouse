"""The phenotype schema: a declarative YAML with a boolean criteria tree over leaves, a
grain, an onset rule and a definition hash (EP-41 item 1; DESIGN §8, §15; GOVERNANCE §12).

A **phenotype** is one YAML document (``defs/<id>.yaml`` in the package, or a study file
passed by path) that names a computable clinical trait by *rules*, never by data::

    id: t2dm
    version: "1.0.0"
    name: Type 2 diabetes mellitus
    grain: subject                     # subject | hadm | icustay (timesem's registry)
    criteria:
      any:
        - {id: dx, diagnosis: {codeset: t2dm@1.0.0, position: any, min_admissions: 1}}
        - all:
            - any:
                - {id: med, medication: {codeset: noninsulin_antidiabetics@1.0.0}}
                - {id: a1c, lab: {itemids: [50852], op: ">=", threshold: 6.5, unit: "%"}}
            - not: {id: t1dm, diagnosis: {codeset: t1dm@1.0.0}}
    onset: earliest                    # earliest | latest | {first_of: [leaf ids]}
    provenance: {source: hand, accessed: 2026-09-06}
    what_it_does_not_claim: [...]

* **Criteria** is a tree of ``all`` / ``any`` (lists) and ``not`` (one node) over
  **leaves**; a leaf is a mapping with an ``id`` (a slug, unique in the document) and
  exactly one kind key: ``diagnosis(codeset, position: any|primary, min_admissions)``,
  ``procedure(codeset)``, ``medication(codeset, source: prescriptions|emar|inputevents,
  min_orders)``, ``lab(codeset|itemids, op, threshold, unit, min_count)``,
  ``microbiology(spec_itemids|org_itemids, positive_only)``, ``concept(table, column, op,
  value, key, time_column)`` (EP-42's door to the mimic-code concepts) and
  ``temporal(a, relation: before|after|within_hours, b, hours)`` whose operands ``a`` /
  ``b`` are inline leaves that count as evidence only through the temporal leaf.
* **Onset** — ``earliest`` / ``latest`` take the min / max first-event time over the
  *positive* leaves (those not under a ``not``) a unit satisfies; ``first_of`` names the
  leaves to take the earliest of. A unit whose ``flag`` is false has no onset.
* **References** are the code sets the leaves name (``id@version``); the registry
  resolves every reference to the code set's ``def_hash`` and the phenotype's
  **``def_hash``** is the sha256 of the canonical JSON of ``{grain, criteria, onset,
  references: {ref: hash}}`` — so a phenotype version pins its inputs and moves exactly
  when the definition or a referenced definition moves. ``name``, ``description``,
  ``provenance``, ``citations``, ``notes`` and ``what_it_does_not_claim`` are
  documentation and stay out of the hash.
* ``version`` is semver; the ``(id, version)`` pair is immutable once recorded in the
  registry lock (:mod:`~mimicwarehouse.phenotypes.registry` raises
  :class:`PhenotypeFrozenError` when a locked pair's hash moved — bump the version).

Everything here is schema text; no data access. Import budget: pydantic + yaml + stdlib
plus the EP-40 code-set spec (same budget) — the ``phenotype`` sub-app imports this
module at ``mwh`` start-up.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from mimicwarehouse.codesets.spec import (
    CodeSetError,
    Provenance,
    canonical_json,
    format_ref,
    parse_ref,
)

GrainName = Literal["subject", "hadm", "icustay"]
GRAINS: tuple[str, ...] = ("subject", "hadm", "icustay")
LeafKind = Literal[
    "diagnosis", "procedure", "medication", "lab", "microbiology", "concept", "temporal"
]
LEAF_KINDS: tuple[str, ...] = (
    "diagnosis",
    "procedure",
    "medication",
    "lab",
    "microbiology",
    "concept",
    "temporal",
)
NodeOp = Literal["all", "any", "not", "leaf"]
Comparison = Literal["=", "!=", "<", "<=", ">", ">="]
COMPARISONS: tuple[str, ...] = ("=", "!=", "<", "<=", ">", ">=")
OnsetKind = Literal["earliest", "latest", "first_of"]
Relation = Literal["before", "after", "within_hours"]
MedicationSource = Literal["prescriptions", "emar", "inputevents"]
ConceptKey = Literal["stay_id", "hadm_id", "subject_id"]

_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_PMID_RE = re.compile(r"\bPMID\b", re.IGNORECASE)
_QUALIFIED_RE = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")
_COLUMN_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class PhenotypeError(ValueError):
    """A phenotype YAML is malformed, a reference cannot be resolved, or a registry /
    compile operation cannot proceed."""


class PhenotypeFrozenError(PhenotypeError):
    """An ``(id, version)`` pair recorded in the registry lock now hashes differently —
    the definition (or a referenced code set) changed without a version bump."""


class UnknownPhenotypeError(PhenotypeError, LookupError):
    """No phenotype with that ``id@version`` in the registry."""


# ---------------------------------------------------------------------------
# Leaves
# ---------------------------------------------------------------------------


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


def _codeset_ref(value: Any) -> str:
    try:
        codeset_id, version = parse_ref(str(value))
    except CodeSetError as exc:
        raise ValueError(str(exc)) from None
    return format_ref(codeset_id, version)


def _positive_ints(value: Any, what: str) -> tuple[int, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError(f"{what} must be a list of positive integers")
    out: set[int] = set()
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int) or item < 1:
            raise ValueError(f"{what}: {item!r} is not a positive integer")
        out.add(item)
    return tuple(sorted(out))


class DiagnosisLeaf(_Frozen):
    """Billed diagnoses in ``diagnoses_icd`` matching a dual ICD code set; the event time
    is the admission's ``dischtime`` (diagnoses carry no timestamp). ``position: primary``
    keeps ``seq_num = 1`` only; ``min_admissions`` (subject grain) requires the codes on
    that many distinct admissions."""

    codeset: str
    position: Literal["any", "primary"] = "any"
    min_admissions: int = Field(default=1, ge=1)

    @field_validator("codeset", mode="before")
    @classmethod
    def _ref(cls, value: Any) -> str:
        return _codeset_ref(value)

    def canonical(self) -> dict[str, Any]:
        return {
            "codeset": self.codeset,
            "position": self.position,
            "min_admissions": self.min_admissions,
        }


class ProcedureLeaf(_Frozen):
    """Billed procedures in ``procedures_icd`` matching a dual ICD procedure set; the
    event time is ``chartdate``."""

    codeset: str

    @field_validator("codeset", mode="before")
    @classmethod
    def _ref(cls, value: Any) -> str:
        return _codeset_ref(value)

    def canonical(self) -> dict[str, Any]:
        return {"codeset": self.codeset}


class MedicationLeaf(_Frozen):
    """Medication records matching a drug code set: ``prescriptions.drug`` (event time
    ``starttime``), ``emar.medication`` (``charttime``) by the set's name rules, or
    ``inputevents.itemid`` (``starttime``) by the set's ICU itemids. A name match is an
    order / administration record, never adherence."""

    codeset: str
    source: MedicationSource = "prescriptions"
    min_orders: int = Field(default=1, ge=1)

    @field_validator("codeset", mode="before")
    @classmethod
    def _ref(cls, value: Any) -> str:
        return _codeset_ref(value)

    def canonical(self) -> dict[str, Any]:
        return {"codeset": self.codeset, "source": self.source, "min_orders": self.min_orders}


class LabLeaf(_Frozen):
    """``labevents`` results of the named itemids (or an ``itemid`` code set) whose
    harmonised value (EP-39's conversions inlined: the canonical unit for curated itemids,
    ``valuenum`` as-is otherwise) satisfies ``op threshold``; ``unit`` documents the
    threshold's unit and must equal the canonical unit of a curated itemid."""

    codeset: str | None = None
    itemids: tuple[int, ...] = ()
    op: Comparison
    threshold: float
    unit: str | None = None
    min_count: int = Field(default=1, ge=1)

    @field_validator("codeset", mode="before")
    @classmethod
    def _ref(cls, value: Any) -> str | None:
        return None if value is None else _codeset_ref(value)

    @field_validator("itemids", mode="before")
    @classmethod
    def _ids(cls, value: Any) -> tuple[int, ...]:
        return _positive_ints(value, "lab.itemids")

    @model_validator(mode="after")
    def _one_source(self) -> LabLeaf:
        if (self.codeset is None) == (not self.itemids):
            raise ValueError("lab: give exactly one of codeset (an itemid set) or itemids")
        return self

    def canonical(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "op": self.op,
            "threshold": self.threshold,
            "min_count": self.min_count,
        }
        if self.codeset is not None:
            out["codeset"] = self.codeset
        if self.itemids:
            out["itemids"] = list(self.itemids)
        if self.unit is not None:
            out["unit"] = self.unit
        return out


class MicrobiologyLeaf(_Frozen):
    """``microbiologyevents`` rows by specimen (``spec_itemids``) and / or organism
    (``org_itemids``); ``positive_only`` keeps rows with an organism. The event time is
    ``charttime`` when known, else ``chartdate``."""

    spec_itemids: tuple[int, ...] = ()
    org_itemids: tuple[int, ...] = ()
    positive_only: bool = False

    @field_validator("spec_itemids", mode="before")
    @classmethod
    def _spec(cls, value: Any) -> tuple[int, ...]:
        return _positive_ints(value, "microbiology.spec_itemids")

    @field_validator("org_itemids", mode="before")
    @classmethod
    def _org(cls, value: Any) -> tuple[int, ...]:
        return _positive_ints(value, "microbiology.org_itemids")

    @model_validator(mode="after")
    def _some(self) -> MicrobiologyLeaf:
        if not self.spec_itemids and not self.org_itemids:
            raise ValueError("microbiology: give spec_itemids and / or org_itemids")
        return self

    def canonical(self) -> dict[str, Any]:
        out: dict[str, Any] = {"positive_only": self.positive_only}
        if self.spec_itemids:
            out["spec_itemids"] = list(self.spec_itemids)
        if self.org_itemids:
            out["org_itemids"] = list(self.org_itemids)
        return out


class ConceptLeaf(_Frozen):
    """Rows of a materialised concept (``mimiciv_derived.<table>``, EP-37/38) where
    ``column op value``; ``key`` names the concept's key column (``stay_id`` for the ICU
    concepts, joined to ``icustays`` for the other keys), ``time_column`` the event time
    (default: the key table's anchor — ``intime`` / ``admittime``). Used by EP-42."""

    table: str
    column: str
    op: Comparison
    value: bool | int | float | str
    key: ConceptKey = "stay_id"
    time_column: str | None = None

    @field_validator("table")
    @classmethod
    def _table(cls, value: str) -> str:
        text = value.strip().lower()
        if not _QUALIFIED_RE.match(text):
            raise ValueError(f"concept.table {value!r} must be <schema>.<table>")
        return text

    @field_validator("column", "time_column")
    @classmethod
    def _column(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip().lower()
        if not _COLUMN_RE.match(text):
            raise ValueError(f"column {value!r} is not a lower [a-z0-9_] name")
        return text

    def canonical(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "table": self.table,
            "column": self.column,
            "op": self.op,
            "value": self.value,
            "key": self.key,
        }
        if self.time_column is not None:
            out["time_column"] = self.time_column
        return out


class TemporalLeaf(_Frozen):
    """Two inline leaves ``a`` and ``b`` on the same unit whose events stand in
    ``relation``: ``before`` (an ``a`` event precedes a ``b`` event), ``after``, or
    ``within_hours`` (``|a - b| <= hours``). The temporal leaf's events are the ``a``
    events with a qualifying partner."""

    a: Leaf
    b: Leaf
    relation: Relation
    hours: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _hours(self) -> TemporalLeaf:
        if self.relation == "within_hours" and self.hours is None:
            raise ValueError("temporal: within_hours needs hours")
        if self.relation != "within_hours" and self.hours is not None:
            raise ValueError(f"temporal: hours only applies to within_hours, not {self.relation}")
        if self.a.kind == "temporal" or self.b.kind == "temporal":
            raise ValueError("temporal: operands cannot be temporal leaves themselves")
        return self

    def canonical(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "a": self.a.canonical(),
            "b": self.b.canonical(),
            "relation": self.relation,
        }
        if self.hours is not None:
            out["hours"] = self.hours
        return out


LeafPayload = (
    DiagnosisLeaf
    | ProcedureLeaf
    | MedicationLeaf
    | LabLeaf
    | MicrobiologyLeaf
    | ConceptLeaf
    | TemporalLeaf
)
_LEAF_MODELS: dict[str, type[BaseModel]] = {
    "diagnosis": DiagnosisLeaf,
    "procedure": ProcedureLeaf,
    "medication": MedicationLeaf,
    "lab": LabLeaf,
    "microbiology": MicrobiologyLeaf,
    "concept": ConceptLeaf,
    "temporal": TemporalLeaf,
}


class Leaf(_Frozen):
    """One leaf of the criteria tree: ``id`` + exactly one kind key (module docstring)."""

    id: str
    kind: LeafKind
    diagnosis: DiagnosisLeaf | None = None
    procedure: ProcedureLeaf | None = None
    medication: MedicationLeaf | None = None
    lab: LabLeaf | None = None
    microbiology: MicrobiologyLeaf | None = None
    concept: ConceptLeaf | None = None
    temporal: TemporalLeaf | None = None

    @model_validator(mode="before")
    @classmethod
    def _one_kind(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            raise ValueError("a leaf is a mapping {id, <kind>: {...}}")
        present = [k for k in LEAF_KINDS if k in data]
        if "kind" in data and data["kind"] not in present:
            present.append(str(data["kind"]))
        if len(present) != 1:
            raise ValueError(
                f"leaf {data.get('id', '?')!r}: exactly one kind key expected "
                f"({', '.join(LEAF_KINDS)}), got {present or 'none'}"
            )
        kind = present[0]
        payload = data.get(kind)
        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            raise ValueError(f"leaf {data.get('id', '?')!r}: {kind} must be a mapping")
        return {**data, "kind": kind, kind: payload}

    @field_validator("id")
    @classmethod
    def _id(cls, value: str) -> str:
        if not _ID_RE.match(value):
            raise ValueError(f"leaf id {value!r} is not a lower [a-z0-9_] slug")
        return value

    @property
    def payload(self) -> Any:
        return getattr(self, self.kind)

    @property
    def codeset_refs(self) -> tuple[str, ...]:
        """The ``id@version`` code-set references this leaf (and its operands) name."""
        refs: list[str] = []
        payload = self.payload
        if self.kind == "temporal":
            for operand in (payload.a, payload.b):
                refs.extend(operand.codeset_refs)
        else:
            ref = getattr(payload, "codeset", None)
            if ref:
                refs.append(ref)
        return tuple(dict.fromkeys(refs))

    @property
    def concept_tables(self) -> tuple[str, ...]:
        if self.kind == "concept":
            return (self.payload.table,)
        if self.kind == "temporal":
            return tuple(
                dict.fromkeys(self.payload.a.concept_tables + self.payload.b.concept_tables)
            )
        return ()

    def canonical(self) -> dict[str, Any]:
        return {"id": self.id, self.kind: self.payload.canonical()}


# ---------------------------------------------------------------------------
# The criteria tree
# ---------------------------------------------------------------------------


class Node(_Frozen):
    """``all`` / ``any`` over children, ``not`` over one child, or a leaf."""

    op: NodeOp
    children: tuple[Node, ...] = ()
    leaf: Leaf | None = None

    @model_validator(mode="before")
    @classmethod
    def _parse(cls, data: Any) -> Any:
        if isinstance(data, dict) and "op" in data:
            return data  # already in model form
        if not isinstance(data, dict):
            raise ValueError(
                "a criteria node is a mapping: {all: [...]}, {any: [...]}, {not: {...}} or a leaf"
            )
        ops = [k for k in ("all", "any", "not") if k in data]
        if len(ops) > 1 or (ops and len(data) != 1):
            raise ValueError(
                f"a criteria node carries exactly one of all / any / not, got {sorted(data)}"
            )
        if not ops:
            return {"op": "leaf", "leaf": data}
        op = ops[0]
        body = data[op]
        if op == "not":
            if isinstance(body, list):
                if len(body) != 1:
                    raise ValueError("not: takes exactly one node")
                body = body[0]
            return {"op": "not", "children": [body]}
        if not isinstance(body, list) or not body:
            raise ValueError(f"{op}: must be a non-empty list of nodes")
        return {"op": op, "children": list(body)}

    @model_validator(mode="after")
    def _shape(self) -> Node:
        if self.op == "leaf":
            if self.leaf is None or self.children:
                raise ValueError("a leaf node carries a leaf and no children")
        elif self.op == "not":
            if len(self.children) != 1 or self.leaf is not None:
                raise ValueError("not: takes exactly one node")
        elif not self.children or self.leaf is not None:
            raise ValueError(f"{self.op}: needs children")
        return self

    def leaves(self, *, under_not: bool = False) -> Iterator[tuple[Leaf, bool]]:
        """Every leaf of the tree in document order with its polarity (``True`` when it
        sits under an odd number of ``not``)."""
        if self.op == "leaf":
            assert self.leaf is not None
            yield self.leaf, under_not
            return
        flip = under_not != (self.op == "not")
        for child in self.children:
            yield from child.leaves(under_not=flip)

    def canonical(self) -> dict[str, Any]:
        if self.op == "leaf":
            assert self.leaf is not None
            return self.leaf.canonical()
        if self.op == "not":
            return {"not": self.children[0].canonical()}
        return {self.op: [c.canonical() for c in self.children]}

    def render(self) -> str:
        """A one-line human rendering: ``any(dx, all(any(med, a1c), not(t1dm)))``."""
        if self.op == "leaf":
            assert self.leaf is not None
            return self.leaf.id
        return f"{self.op}({', '.join(c.render() for c in self.children)})"


# ---------------------------------------------------------------------------
# Onset, outputs, the phenotype
# ---------------------------------------------------------------------------


class Onset(_Frozen):
    """``earliest`` / ``latest`` / ``{first_of: [leaf ids]}`` (module docstring)."""

    rule: OnsetKind
    leaves: tuple[str, ...] = ()

    @model_validator(mode="before")
    @classmethod
    def _parse(cls, data: Any) -> Any:
        if isinstance(data, dict) and "rule" in data:
            return data
        if isinstance(data, str):
            if data not in ("earliest", "latest"):
                raise ValueError(
                    f"onset {data!r}: expected earliest, latest or {{first_of: [...]}}"
                )
            return {"rule": data}
        if isinstance(data, dict) and set(data) == {"first_of"}:
            leaves = data["first_of"]
            if not isinstance(leaves, list) or not leaves:
                raise ValueError("onset.first_of must be a non-empty list of leaf ids")
            return {"rule": "first_of", "leaves": [str(x) for x in leaves]}
        raise ValueError("onset: expected earliest, latest or {first_of: [leaf ids]}")

    def canonical(self) -> Any:
        return {"first_of": list(self.leaves)} if self.rule == "first_of" else self.rule


class Outputs(_Frozen):
    """Which output columns the materialised table carries (``flag`` always)."""

    flag: bool = True
    onset_time: bool = True
    evidence: bool = True

    @model_validator(mode="after")
    def _flag(self) -> Outputs:
        if not self.flag:
            raise ValueError("outputs.flag cannot be disabled")
        return self


class Phenotype(_Frozen):
    """One versioned phenotype (module docstring)."""

    id: str
    version: str
    name: str = Field(min_length=1)
    description: str = ""
    grain: GrainName
    criteria: Node
    onset: Onset = Onset(rule="earliest")
    outputs: Outputs = Outputs()
    references: tuple[str, ...] = Field(
        default=(),
        description="the code-set references (id@version); optional in YAML — when given, "
        "must equal the set the leaves name",
    )
    provenance: Provenance
    citations: tuple[str, ...] = ()
    notes: str = ""
    what_it_does_not_claim: tuple[str, ...] = ()

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

    @field_validator("references", mode="before")
    @classmethod
    def _refs(cls, value: Any) -> tuple[str, ...]:
        if value is None:
            return ()
        if not isinstance(value, list):
            raise ValueError("references must be a list of id@version code-set references")
        return tuple(sorted({_codeset_ref(v) for v in value}))

    @field_validator("citations")
    @classmethod
    def _citations(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for ref in value:
            if _PMID_RE.search(ref):
                raise ValueError(f"citation {ref!r}: cite a DOI or stable URL, never a bare PMID")
            if not ref.startswith(("https://", "http://", "doi:")):
                raise ValueError(f"citation {ref!r} must be an https:// URL or a doi: identifier")
        return value

    @model_validator(mode="after")
    def _rules(self) -> Phenotype:
        ids: list[str] = []
        for leaf, _neg in self.criteria.leaves():
            ids.append(leaf.id)
            if leaf.kind == "temporal":
                ids.extend((leaf.payload.a.id, leaf.payload.b.id))
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ValueError(f"{self.id}@{self.version}: duplicate leaf id(s) {dupes}")
        criteria_ids = {leaf.id for leaf, _neg in self.criteria.leaves()}
        unknown = sorted(set(self.onset.leaves) - criteria_ids)
        if unknown:
            raise ValueError(
                f"{self.id}@{self.version}: onset.first_of names unknown leaf(s) {unknown}"
            )
        inferred = self.codeset_refs
        if self.references and set(self.references) != set(inferred):
            raise ValueError(
                f"{self.id}@{self.version}: references {list(self.references)} differ from "
                f"the code sets the leaves name {list(inferred)}"
            )
        if self.grain != "subject":
            for leaf, _neg in self.all_leaves():
                if leaf.kind == "diagnosis" and leaf.payload.min_admissions > 1:
                    raise ValueError(
                        f"{self.id}@{self.version}: leaf {leaf.id}: min_admissions > 1 only "
                        "applies to the subject grain"
                    )
        return self

    # -- derived views ---------------------------------------------------------------------

    @property
    def ref(self) -> str:
        return format_ref(self.id, self.version)

    def all_leaves(self) -> Iterator[tuple[Leaf, bool]]:
        """Every leaf including temporal operands, with polarity (operands inherit the
        temporal leaf's polarity)."""
        for leaf, neg in self.criteria.leaves():
            yield leaf, neg
            if leaf.kind == "temporal":
                yield leaf.payload.a, neg
                yield leaf.payload.b, neg

    @property
    def criteria_leaves(self) -> tuple[Leaf, ...]:
        """The leaves that take part in the boolean reduction (temporal operands excluded)."""
        return tuple(leaf for leaf, _neg in self.criteria.leaves())

    @property
    def positive_leaf_ids(self) -> tuple[str, ...]:
        """Criteria leaves not under a ``not`` — the onset candidates."""
        return tuple(leaf.id for leaf, neg in self.criteria.leaves() if not neg)

    @property
    def codeset_refs(self) -> tuple[str, ...]:
        """Every code-set reference the leaves name, sorted."""
        refs: set[str] = set()
        for leaf, _neg in self.criteria.leaves():
            refs.update(leaf.codeset_refs)
        return tuple(sorted(refs))

    @property
    def concept_tables(self) -> tuple[str, ...]:
        tables: set[str] = set()
        for leaf, _neg in self.criteria.leaves():
            tables.update(leaf.concept_tables)
        return tuple(sorted(tables))

    def canonical(self, reference_hashes: Mapping[str, str]) -> dict[str, Any]:
        """The hashed shape: grain + criteria + onset + the resolved reference hashes
        (every reference the leaves name must be present)."""
        missing = sorted(set(self.codeset_refs) - set(reference_hashes))
        if missing:
            raise PhenotypeError(f"{self.ref}: unresolved reference(s) {missing}")
        return {
            "grain": self.grain,
            "criteria": self.criteria.canonical(),
            "onset": self.onset.canonical(),
            "references": {ref: reference_hashes[ref] for ref in self.codeset_refs},
        }

    def def_hash(self, reference_hashes: Mapping[str, str]) -> str:
        """sha256 of the canonical JSON (module docstring)."""
        payload = canonical_json(self.canonical(reference_hashes))
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


def phenotype_from_text(text: str, *, where: str = "<text>") -> Phenotype:
    """Parse one YAML document into a :class:`Phenotype` (:class:`PhenotypeError` names
    every validation problem)."""
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise PhenotypeError(f"{where}: cannot parse ({exc})") from exc
    if not isinstance(doc, dict):
        raise PhenotypeError(f"{where}: top level must be a mapping")
    try:
        return Phenotype.model_validate(doc)
    except ValidationError as exc:
        raise PhenotypeError(_format_validation_error(where, exc)) from None


def load_phenotype(path: Path | str) -> Phenotype:
    """Parse and validate one phenotype YAML file."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PhenotypeError(f"{path.name}: cannot read ({exc})") from exc
    return phenotype_from_text(text, where=path.name)


__all__ = [
    "COMPARISONS",
    "GRAINS",
    "LEAF_KINDS",
    "Comparison",
    "ConceptKey",
    "ConceptLeaf",
    "DiagnosisLeaf",
    "GrainName",
    "LabLeaf",
    "Leaf",
    "LeafKind",
    "LeafPayload",
    "MedicationLeaf",
    "MedicationSource",
    "MicrobiologyLeaf",
    "Node",
    "NodeOp",
    "Onset",
    "OnsetKind",
    "Outputs",
    "Phenotype",
    "PhenotypeError",
    "PhenotypeFrozenError",
    "ProcedureLeaf",
    "Relation",
    "TemporalLeaf",
    "UnknownPhenotypeError",
    "load_phenotype",
    "phenotype_from_text",
]
