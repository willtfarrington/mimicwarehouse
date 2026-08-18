"""Seed vocabularies for the synthetic fixture (EP-11).

Loads the hand-typed YAML under ``fixtures/vocab/`` (package data): ``d_labitems.yaml``,
``icd.yaml``, ``d_hcpcs.yaml``, ``drugs.yaml``, ``categories.yaml``. Everything here is
dictionary / category text typed from public documentation - never a patient row, never a
value read from ``source material/`` (GOVERNANCE section 4). The generators in
:mod:`mimicwarehouse.fixtures.hosp` only ever sample from these lists.

``load_vocab()`` is cached; ``load_vocab_from(root)`` reads a directory of the same shape
(tests point it at an edited copy).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import cache
from importlib.resources import files
from pathlib import Path
from typing import Any

import yaml

VOCAB_DIRNAME = "vocab"
VOCAB_FILES: tuple[str, ...] = (
    "d_labitems.yaml",
    "icd.yaml",
    "d_hcpcs.yaml",
    "drugs.yaml",
    "categories.yaml",
)


class VocabError(ValueError):
    """A vocab YAML is missing, malformed or internally inconsistent."""


@dataclass(frozen=True, slots=True)
class LabItem:
    itemid: int
    label: str
    fluid: str
    category: str
    valueuom: str | None
    decimals: int
    low: float
    high: float
    ref_lower: float | None
    ref_upper: float | None
    panel: str
    text_values: tuple[str, ...] = ()
    below_detection: str | None = None
    below_detection_prob: float = 0.0


@dataclass(frozen=True, slots=True)
class IcdCode:
    code: str
    version: int
    title: str
    weight: float = 1.0
    tags: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class Hcpcs:
    code: str
    category: int
    long_description: str
    short_description: str


@dataclass(frozen=True, slots=True)
class Drug:
    drug: str
    formulary_drug_cd: str
    gsn: str
    ndc: str
    prod_strength: str
    form_rx: str
    dose_val_rx: str | None
    dose_unit_rx: str | None
    form_val_disp: str
    form_unit_disp: str
    route: str
    frequency: str
    proc_type: str
    kind: str
    base: str | None = None
    weight: float = 1.0
    tags: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class Weighted:
    """A categorical list ``[(value | None, weight), ...]``."""

    values: tuple[Any, ...]
    weights: tuple[float, ...]

    @classmethod
    def parse(cls, raw: Any, *, where: str) -> Weighted:
        if not isinstance(raw, list) or not raw:
            raise VocabError(f"{where}: expected a non-empty list of [value, weight] pairs")
        values: list[Any] = []
        weights: list[float] = []
        for entry in raw:
            if not (isinstance(entry, list) and len(entry) == 2):
                raise VocabError(f"{where}: entry {entry!r} is not a [value, weight] pair")
            value, weight = entry
            if not isinstance(weight, int | float) or weight <= 0:
                raise VocabError(f"{where}: weight of {value!r} must be a positive number")
            values.append(value)
            weights.append(float(weight))
        return cls(tuple(values), tuple(weights))

    @property
    def probabilities(self) -> tuple[float, ...]:
        total = sum(self.weights)
        return tuple(w / total for w in self.weights)


@dataclass(frozen=True, slots=True)
class Vocab:
    """Every seed the hosp generators sample from (see the YAML files for provenance)."""

    version_notes: dict[str, str]
    lab_items: tuple[LabItem, ...]
    lab_panels: dict[str, tuple[int, ...]]
    lab_panel_weights: Weighted
    icd_diagnoses: dict[int, tuple[IcdCode, ...]]
    icd_procedures: dict[int, tuple[IcdCode, ...]]
    hcpcs: tuple[Hcpcs, ...]
    drugs: tuple[Drug, ...]
    drug_bases: tuple[dict[str, Any], ...]
    categories: dict[str, Any] = field(repr=False)

    # -- convenience lookups ------------------------------------------------------------------

    def lab_item(self, itemid: int) -> LabItem:
        for item in self.lab_items:
            if item.itemid == itemid:
                return item
        raise KeyError(f"no lab item {itemid}")

    def weighted(self, key: str) -> Weighted:
        return Weighted.parse(self.categories[key], where=f"categories.yaml: {key}")

    def nested_weighted(self, key: str, sub: str) -> Weighted:
        return Weighted.parse(self.categories[key][sub], where=f"categories.yaml: {key}.{sub}")

    def drugs_tagged(self, tag: str) -> tuple[Drug, ...]:
        return tuple(d for d in self.drugs if tag in d.tags)

    def icd_tagged(self, version: int, tag: str) -> tuple[IcdCode, ...]:
        return tuple(c for c in self.icd_diagnoses[version] if tag in c.tags)

    def base_named(self, name: str) -> dict[str, Any]:
        for b in self.drug_bases:
            if b["drug"] == name:
                return b
        raise KeyError(f"no BASE product named {name!r}")


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def vocab_root() -> Path:
    """``fixtures/vocab/`` inside the installed package (source tree or wheel)."""
    return Path(str(files(__package__).joinpath(VOCAB_DIRNAME)))


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise VocabError(f"{path.name}: missing")
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise VocabError(f"{path.name}: invalid YAML - {exc}") from exc
    if not isinstance(doc, dict):
        raise VocabError(f"{path.name}: top level must be a mapping")
    return doc


def _lab_items(doc: dict[str, Any]) -> tuple[LabItem, ...]:
    out: list[LabItem] = []
    for raw in doc.get("items") or ():
        try:
            out.append(
                LabItem(
                    itemid=int(raw["itemid"]),
                    label=str(raw["label"]),
                    fluid=str(raw["fluid"]),
                    category=str(raw["category"]),
                    valueuom=None if raw.get("valueuom") is None else str(raw["valueuom"]),
                    decimals=int(raw["decimals"]),
                    low=float(raw["low"]),
                    high=float(raw["high"]),
                    ref_lower=None if raw.get("ref_lower") is None else float(raw["ref_lower"]),
                    ref_upper=None if raw.get("ref_upper") is None else float(raw["ref_upper"]),
                    panel=str(raw["panel"]),
                    text_values=tuple(str(v) for v in raw.get("text_values") or ()),
                    below_detection=raw.get("below_detection"),
                    below_detection_prob=float(raw.get("below_detection_prob") or 0.0),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise VocabError(f"d_labitems.yaml: bad item {raw!r} ({exc})") from exc
    ids = [i.itemid for i in out]
    if len(set(ids)) != len(ids):
        raise VocabError("d_labitems.yaml: duplicate itemid")
    return tuple(out)


def _icd(doc: dict[str, Any], key: str) -> dict[int, tuple[IcdCode, ...]]:
    out: dict[int, tuple[IcdCode, ...]] = {}
    for version in (9, 10):
        codes: list[IcdCode] = []
        for raw in (doc.get(key) or {}).get(version) or ():
            try:
                codes.append(
                    IcdCode(
                        code=str(raw["code"]),
                        version=version,
                        title=str(raw["title"]),
                        weight=float(raw.get("weight", 1.0)),
                        tags=frozenset(str(t) for t in raw.get("tags") or ()),
                    )
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise VocabError(f"icd.yaml: bad {key} entry {raw!r} ({exc})") from exc
        if not codes:
            raise VocabError(f"icd.yaml: {key} has no ICD-{version} codes")
        seen = [c.code for c in codes]
        if len(set(seen)) != len(seen):
            raise VocabError(f"icd.yaml: duplicate ICD-{version} {key} code")
        out[version] = tuple(codes)
    return out


def _drugs(doc: dict[str, Any]) -> tuple[Drug, ...]:
    out: list[Drug] = []
    for raw in doc.get("drugs") or ():
        try:
            out.append(
                Drug(
                    drug=str(raw["drug"]),
                    formulary_drug_cd=str(raw["formulary_drug_cd"]),
                    gsn=str(raw["gsn"]),
                    ndc=str(raw["ndc"]),
                    prod_strength=str(raw["prod_strength"]),
                    form_rx=str(raw["form_rx"]),
                    dose_val_rx=None if raw.get("dose_val_rx") is None else str(raw["dose_val_rx"]),
                    dose_unit_rx=None
                    if raw.get("dose_unit_rx") is None
                    else str(raw["dose_unit_rx"]),
                    form_val_disp=str(raw["form_val_disp"]),
                    form_unit_disp=str(raw["form_unit_disp"]),
                    route=str(raw["route"]),
                    frequency=str(raw["frequency"]),
                    proc_type=str(raw["proc_type"]),
                    kind=str(raw["kind"]),
                    base=None if raw.get("base") is None else str(raw["base"]),
                    weight=float(raw.get("weight", 1.0)),
                    tags=frozenset(str(t) for t in raw.get("tags") or ()),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise VocabError(f"drugs.yaml: bad drug {raw!r} ({exc})") from exc
    names = [d.drug for d in out]
    if len(set(names)) != len(names):
        raise VocabError("drugs.yaml: duplicate drug name")
    return tuple(out)


def load_vocab_from(root: Path) -> Vocab:
    """Build a :class:`Vocab` from a directory shaped like ``fixtures/vocab/``."""
    root = Path(root)
    docs = {name: _read_yaml(root / name) for name in VOCAB_FILES}
    labs = docs["d_labitems.yaml"]
    icd = docs["icd.yaml"]
    hcpcs_doc = docs["d_hcpcs.yaml"]
    drugs_doc = docs["drugs.yaml"]
    cats = docs["categories.yaml"]

    lab_items = _lab_items(labs)
    known = {i.itemid for i in lab_items}
    panels: dict[str, tuple[int, ...]] = {}
    for name, ids in (labs.get("panels") or {}).items():
        ids_t = tuple(int(i) for i in ids)
        unknown = [i for i in ids_t if i not in known]
        if unknown:
            raise VocabError(f"d_labitems.yaml: panel {name} names unknown itemids {unknown}")
        panels[str(name)] = ids_t
    weights_raw = labs.get("panel_weights") or {}
    panel_weights = Weighted(
        tuple(str(k) for k in weights_raw), tuple(float(v) for v in weights_raw.values())
    )
    for p in panel_weights.values:
        if p not in panels:
            raise VocabError(f"d_labitems.yaml: panel_weights names unknown panel {p!r}")

    hcpcs = tuple(
        Hcpcs(
            code=str(r["code"]),
            category=int(r["category"]),
            long_description=str(r["long_description"]),
            short_description=str(r["short_description"]),
        )
        for r in hcpcs_doc.get("items") or ()
    )
    if not hcpcs:
        raise VocabError("d_hcpcs.yaml: no items")

    drugs = _drugs(drugs_doc)
    bases = tuple(dict(b) for b in drugs_doc.get("bases") or ())
    base_names = {b["drug"] for b in bases}
    for d in drugs:
        if d.base is not None and d.base not in base_names:
            raise VocabError(f"drugs.yaml: {d.drug} names unknown base {d.base!r}")

    required = (
        "anchor_year_groups",
        "admission_types",
        "admission_locations",
        "discharge_locations",
        "insurance",
        "language",
        "marital_status",
        "race",
        "gender",
        "ward_careunits",
        "icu_careunits",
        "services",
        "drg_hcfa",
        "drg_apr",
        "omr_result_names",
        "micro_specimens",
        "micro_organisms",
        "micro_antibiotics",
        "poe_nonmed_types",
        "emar_events",
        "lab_comments",
    )
    missing = [k for k in required if k not in cats]
    if missing:
        raise VocabError(f"categories.yaml: missing keys {missing}")

    return Vocab(
        version_notes={name: str(doc.get("version_note", "")) for name, doc in docs.items()},
        lab_items=lab_items,
        lab_panels=panels,
        lab_panel_weights=panel_weights,
        icd_diagnoses=_icd(icd, "diagnoses"),
        icd_procedures=_icd(icd, "procedures"),
        hcpcs=hcpcs,
        drugs=drugs,
        drug_bases=bases,
        categories=cats,
    )


@cache
def load_vocab() -> Vocab:
    """The packaged seed vocabularies (cached; ``load_vocab.cache_clear()`` in tests)."""
    return load_vocab_from(vocab_root())


__all__ = [
    "VOCAB_FILES",
    "Drug",
    "Hcpcs",
    "IcdCode",
    "LabItem",
    "Vocab",
    "VocabError",
    "Weighted",
    "load_vocab",
    "load_vocab_from",
    "vocab_root",
]
