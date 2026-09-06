"""Item dictionary curation + unit harmonization (EP-39; DESIGN §15, GOVERNANCE §4/§5).

``d_items`` / ``d_labitems`` say what an itemid *is*; nothing upstream says which itemids
are the canonical vitals and labs, which unit each should be in, how the unit variants
seen in ``chartevents`` / ``labevents`` / ``outputevents`` convert, or what values are
physiologically implausible. This module is that curation, written once so QC (EP-44),
the first-day marts (EP-55) and the linkage mapping guide (EP-138) import it instead of
re-deriving it:

* **The catalogue** — ``data/item_units.yaml`` (package data, :func:`load_catalogue`):
  one :class:`ItemSpec` per curated itemid (source table, label, ``concept_group``,
  canonical unit, accepted unit strings with their conversion, inclusive plausibility
  bounds in the canonical unit, a curation note, the mimic-code file the itemid was copied
  from, a version). Itemids come from the vendored ``concepts_duckdb`` files and were
  verified against ``meta.itemids`` through ``mwh sql`` (dictionary lookups only);
  validation refuses duplicates, ``plausible_low >= plausible_high``, a canonical unit that
  is not among the accepted ones or does not convert by identity, an unknown formula, and
  two items of one concept group with different canonical units.
* **Unit strings** are compared after :func:`normalize_unit` (whitespace removed, degree
  sign and a leading ``deg`` dropped, micro signs folded to ``u``, casefolded, trailing dot
  removed); :func:`sql_normalize_unit` is the DuckDB twin and the ``mwh_unit_norm`` macro.
  The key ``""`` stands for a NULL / blank unit — accepted where the itemid implies the
  unit (GCS points, INR, pH, the pounds and inches items).
* **Conversions** are affine (:class:`Affine`: ``canonical = value * scale + offset``),
  either a bare factor or a named formula in :data:`FORMULAS` (``f_to_c``, ``lb_to_kg``,
  ``in_to_cm``, ``mmol_to_mgdl_glucose``, ``umol_to_mgdl_creatinine``, …) so the Python,
  Polars and SQL twins are the same arithmetic.
* **The API** — :func:`harmonize` (scalar), :func:`harmonize_frame` /
  :func:`plausible_mask` (Polars), :func:`bounds`, and the DuckDB macro
  ``mwh_harmonize(itemid, value, valueuom)`` returning a ``STRUCT(value_canonical,
  unit_canonical, converted, plausible)`` — generated from the same catalogue
  (:func:`macro_statements`) and installed in every tier catalog by
  :func:`register_units`, the :data:`mimicwarehouse.catalog.build.CATALOG_EXTENSIONS`
  entry. An unknown unit passes the value through with ``converted = false``; an unknown
  itemid also yields ``unit_canonical`` / ``plausible`` NULL. ``plausible`` is inclusive at
  both bounds (mimic-code's strict filters differ only at the exact boundary).
* **The DAG steps** (``dag/specs/units.yaml``, tag ``units``; ``python`` kind):
  ``units.item_units`` materialises the catalogue as ``meta.item_units`` (one row per
  itemid x accepted unit); ``units.variants`` aggregates ``(itemid, source, valueuom,
  n_rows, share)`` over the curated itemids of the tier's event tables into
  ``meta.item_unit_variants`` — the raw counts stay in the data root under
  ``lake/meta/<tier>/raw/`` (never walked into a catalog, never exported) while the
  published table keeps every variant row but blanks ``n_rows`` / ``share`` of the rows
  the :data:`mimicwarehouse.safe.SUPPRESSOR` hook marks small (``suppressed = true``;
  EP-43 swaps the hook for ``disclose.suppress``), and ``share`` is the share among the
  released rows of the itemid so a blanked cell cannot be backed out; ``units.dictionary``
  joins EP-29's ``meta.itemids`` shape with the curation columns into
  ``meta.item_dictionary`` (``curated``, ``concept_group``, ``canonical_unit``,
  ``plausible_low`` / ``plausible_high``; ``unit_hint`` stays ``meta.columns``'). All three
  write ``lake/meta/<tier>/<table>.parquet``, which EP-37's discovery walker registers.
* **The report** — :func:`report` reads the two meta tables through
  :func:`mimicwarehouse.safe.safe_query` (audited) and :func:`summarize_variants` folds
  them into one row per curated itemid (``n_variants``, ``dominant_unit``,
  ``dominant_share``, ``unexpected_units``, ``flagged``); ``mwh units report --tier <t>``
  renders it. Flagged itemids feed EP-44's checks.

Everything written, logged, printed or returned is dictionary text, unit strings,
suppressed counts and shares — never a row (GOVERNANCE §4). ``docs/methods/units.md`` is
the prose twin; its generated blocks come from :func:`sync_methods_doc`.

Import budget: ``cli.py`` imports this module at start-up for the ``units`` sub-app, so
duckdb / polars / the safe module / the concept runner / the catalog builder load only
inside function bodies (pydantic, yaml, typer and rich are already in the start-up set).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass
from functools import cache
from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal

import typer
import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from rich.markup import escape

from mimicwarehouse.console import (
    EXIT_FINDINGS,
    console,
    console_safe,
    emit_json,
    fail,
)

if TYPE_CHECKING:  # pragma: no cover
    import duckdb
    import polars

    from mimicwarehouse.cli import CliState
    from mimicwarehouse.config import Settings
    from mimicwarehouse.dag.runner import StepContext, StepOutcome
    from mimicwarehouse.dag.spec import Step

_LOG = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------

#: The packaged catalogue: ``src/mimicwarehouse/data/item_units.yaml``.
DATA_DIRNAME = "data"
CATALOGUE_FILENAME = "item_units.yaml"
#: The three ``lake/meta/<tier>/<table>.parquet`` files → ``meta.<table>`` (EP-37 walker).
ITEM_UNITS_TABLE = "item_units"
VARIANTS_TABLE = "item_unit_variants"
DICTIONARY_TABLE = "item_dictionary"
#: Where the raw (unsuppressed) variant counts live: ``lake/meta/<tier>/raw/`` — a
#: subdirectory the discovery walker's non-recursive glob never enters.
RAW_DIRNAME = "raw"
#: The DAG step names (``dag/specs/units.yaml``).
STEP_ITEM_UNITS = "units.item_units"
STEP_VARIANTS = "units.variants"
STEP_DICTIONARY = "units.dictionary"
DAG_TAG = "units"
#: The macros :func:`register_units` installs (``mwh_harmonize`` composes the helpers).
MACRO_NAME = "mwh_harmonize"
MACRO_UNIT_NORM = "mwh_unit_norm"
MACRO_UNIT_CANONICAL = "mwh_unit_canonical"
MACRO_UNIT_KNOWN = "mwh_unit_known"
MACRO_VALUE_CANONICAL = "mwh_value_canonical"
MACRO_PLAUSIBLE = "mwh_plausible"
#: The event tables a curated itemid may come from → ``(schema, table, value column,
#: unit column)``; ``meta.itemids`` names the dimension (``icu`` / ``hosp``), this names
#: the fact table the variants are counted over.
Source = Literal["chartevents", "labevents", "outputevents", "inputevents"]
SOURCES: tuple[str, ...] = ("chartevents", "labevents", "outputevents", "inputevents")
SOURCE_COLUMNS: dict[str, tuple[str, str, str, str]] = {
    "chartevents": ("mimiciv_icu", "chartevents", "valuenum", "valueuom"),
    "labevents": ("mimiciv_hosp", "labevents", "valuenum", "valueuom"),
    "outputevents": ("mimiciv_icu", "outputevents", "value", "valueuom"),
    "inputevents": ("mimiciv_icu", "inputevents", "amount", "amountuom"),
}
#: How a NULL unit is spelled in the report (the catalogue key is ``""``).
NULL_UNIT_LABEL = "(null)"
#: The row cap :func:`report` passes to ``safe_query`` (one row per itemid x unit — a few
#: hundred; deliberately above the CLI default of 200).
REPORT_ROW_CAP = 10_000
#: The brief's floor for the curated catalogue.
MIN_CURATED_ITEMS = 40

_GROUP_RE = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")


class UnitsError(ValueError):
    """The catalogue is malformed, or a step / report cannot run."""


class UnknownItemError(UnitsError, KeyError):
    """An itemid the catalogue does not curate."""


# ---------------------------------------------------------------------------
# Unit-string normalisation (Python + SQL twins)
# ---------------------------------------------------------------------------

_WS_RE = re.compile(r"\s+")
_DEG_PREFIX_RE = re.compile(r"^deg(?:rees?)?\.?")
_TRAILING_DOT_RE = re.compile(r"\.$")
#: Degree sign (U+00B0) and the masculine ordinal (U+00BA, a common mistype); both dropped.
_DEGREE_SIGNS: tuple[str, ...] = (chr(0x00B0), chr(0x00BA))
#: Micro sign (U+00B5) and Greek small mu (U+03BC); both fold to ``u``.
_MICRO_SIGNS: tuple[str, ...] = (chr(0x00B5), chr(0x03BC))
_SQL_DEG_PREFIX = r"^deg(?:rees?)?\.?"
_SQL_TRAILING_DOT = r"\.$"
_SQL_WS = r"\s+"


def normalize_unit(text: str | None) -> str:
    """The comparison key of a unit string: whitespace removed, degree signs dropped,
    micro signs folded to ``u``, casefolded, a leading ``deg`` / ``degree(s)`` (with an
    optional dot) and a trailing dot removed; ``None`` and blank read as ``""``. So
    ``"°F"``, ``"deg F"``, ``"DegF."`` and ``"F"`` all normalise to ``"f"``, ``"mm Hg"``
    to ``"mmhg"``, ``"µmol/L"`` to ``"umol/l"``."""
    s = "" if text is None else str(text)
    s = _WS_RE.sub("", s)
    for sign in _DEGREE_SIGNS:
        s = s.replace(sign, "")
    for sign in _MICRO_SIGNS:
        s = s.replace(sign, "u")
    s = s.casefold()
    s = _DEG_PREFIX_RE.sub("", s)
    return _TRAILING_DOT_RE.sub("", s)


def _sql_str(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def sql_normalize_unit(expr: str) -> str:
    """The DuckDB twin of :func:`normalize_unit` over the expression ``expr`` (RE2
    patterns; DuckDB string literals keep backslashes verbatim)."""
    out = f"regexp_replace(coalesce(CAST({expr} AS VARCHAR), ''), {_sql_str(_SQL_WS)}, '', 'g')"
    for sign in _DEGREE_SIGNS:
        out = f"replace({out}, {_sql_str(sign)}, '')"
    for sign in _MICRO_SIGNS:
        out = f"replace({out}, {_sql_str(sign)}, 'u')"
    out = f"lower({out})"
    out = f"regexp_replace({out}, {_sql_str(_SQL_DEG_PREFIX)}, '')"
    return f"regexp_replace({out}, {_sql_str(_SQL_TRAILING_DOT)}, '')"


# ---------------------------------------------------------------------------
# Conversions: affine formulas
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Affine:
    """``canonical = value * scale + offset`` — every unit conversion the catalogue needs
    is affine, so Python, Polars and SQL share one arithmetic."""

    scale: float
    offset: float = 0.0

    def apply(self, value: float) -> float:
        return value * self.scale + self.offset

    def invert(self, value: float) -> float:
        return (value - self.offset) / self.scale

    @property
    def is_identity(self) -> bool:
        return self.scale == 1.0 and self.offset == 0.0

    def sql(self, expr: str) -> str:
        """The DuckDB expression applying this conversion to ``expr``."""
        if self.is_identity:
            return expr
        if self.offset == 0.0:
            return f"({expr}) * {self.scale!r}"
        return f"({expr}) * {self.scale!r} + ({self.offset!r})"


IDENTITY = "identity"
FACTOR = "factor"

#: The named formulas the catalogue may cite (``accepted_units`` values); a bare number
#: is a factor. Molar masses: glucose 180.156, creatinine 113.12, urea nitrogen 2 x
#: 14.007, lactate 90.08, bilirubin 584.66, calcium 40.078, magnesium 24.305, phosphorus
#: 30.974, haemoglobin monomer 16 114.5 g/mol; 1 kPa = 7.50062 mmHg; NGSP % =
#: 0.09148 x IFCC mmol/mol + 2.152 (the master equation).
FORMULAS: dict[str, Affine] = {
    IDENTITY: Affine(1.0),
    "f_to_c": Affine(5.0 / 9.0, -160.0 / 9.0),
    "lb_to_kg": Affine(0.45359237),
    "in_to_cm": Affine(2.54),
    "mmol_to_mgdl_glucose": Affine(18.0156),
    "umol_to_mgdl_creatinine": Affine(1.0 / 88.42),
    "mmol_to_mgdl_bun": Affine(2.8013),
    "mgdl_to_mmol_lactate": Affine(1.0 / 9.008),
    "umol_to_mgdl_bilirubin": Affine(1.0 / 17.104),
    "mmol_to_mgdl_calcium": Affine(4.008),
    "mmol_to_mgdl_magnesium": Affine(2.4305),
    "mmol_to_mgdl_phosphate": Affine(3.0974),
    "kpa_to_mmhg": Affine(7.50062),
    "ifcc_to_ngsp_hba1c": Affine(0.09148, 2.152),
    "g_l_to_g_dl": Affine(0.1),
    "mmol_to_gdl_hemoglobin": Affine(1.6114),
    "ng_l_to_ng_ml": Affine(0.001),
    "fraction_to_percent": Affine(100.0),
}
#: One line per formula for the docs table.
FORMULA_NOTES: dict[str, str] = {
    IDENTITY: "the unit is already canonical",
    "f_to_c": "degrees Fahrenheit to Celsius: (F - 32) / 1.8",
    "lb_to_kg": "pounds to kilograms (international avoirdupois pound)",
    "in_to_cm": "inches to centimetres",
    "mmol_to_mgdl_glucose": "glucose mmol/L to mg/dL (x 18.0156; 180.156 g/mol)",
    "umol_to_mgdl_creatinine": "creatinine umol/L to mg/dL (/ 88.42; 113.12 g/mol)",
    "mmol_to_mgdl_bun": "urea mmol/L to urea nitrogen mg/dL (x 2.8013; two N of 14.007)",
    "mgdl_to_mmol_lactate": "lactate mg/dL to mmol/L (/ 9.008; 90.08 g/mol)",
    "umol_to_mgdl_bilirubin": "bilirubin umol/L to mg/dL (/ 17.104; 584.66 g/mol)",
    "mmol_to_mgdl_calcium": "calcium mmol/L to mg/dL (x 4.008; 40.078 g/mol)",
    "mmol_to_mgdl_magnesium": "magnesium mmol/L to mg/dL (x 2.4305; 24.305 g/mol)",
    "mmol_to_mgdl_phosphate": "phosphate (as phosphorus) mmol/L to mg/dL (x 3.0974; 30.974 g/mol)",
    "kpa_to_mmhg": "kilopascal to mmHg (x 7.50062)",
    "ifcc_to_ngsp_hba1c": "HbA1c IFCC mmol/mol to NGSP percent (x 0.09148 + 2.152)",
    "g_l_to_g_dl": "g/L to g/dL (x 0.1)",
    "mmol_to_gdl_hemoglobin": "haemoglobin mmol/L (Fe) to g/dL (x 1.6114)",
    "ng_l_to_ng_ml": "ng/L to ng/mL (x 0.001)",
    "fraction_to_percent": "a fraction (L/L) to percent (x 100)",
}

Conversion = str | float


def formula_of(conversion: Conversion) -> Affine:
    """The :class:`Affine` behind an ``accepted_units`` value (a formula name or a
    factor); :class:`UnitsError` for an unknown name or a non-positive factor."""
    if isinstance(conversion, str):
        try:
            return FORMULAS[conversion]
        except KeyError:
            raise UnitsError(
                f"unknown formula {conversion!r}; expected one of {', '.join(FORMULAS)}"
            ) from None
    factor = float(conversion)
    if not factor > 0:
        raise UnitsError(f"a unit factor must be positive, got {conversion!r}")
    return Affine(factor)


def formula_name(conversion: Conversion) -> str:
    """``identity`` / the formula name / ``factor`` — the ``meta.item_units.formula`` cell."""
    return conversion if isinstance(conversion, str) else FACTOR


# ---------------------------------------------------------------------------
# The catalogue models
# ---------------------------------------------------------------------------


class ItemSpec(BaseModel):
    """One curated itemid (module docstring). ``accepted_units`` maps the unit string as
    written in the data (``""`` = NULL / blank) to its conversion: ``identity``, a
    positive factor, or a :data:`FORMULAS` name; keys are matched after
    :func:`normalize_unit`, so two keys with one normal form are refused."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    itemid: int = Field(ge=1)
    source: Source
    label: str = Field(min_length=1)
    concept_group: str
    canonical_unit: str = Field(min_length=1)
    accepted_units: dict[str, Conversion]
    plausible_low: float
    plausible_high: float
    curation_note: str = ""
    source_ref: str = Field(min_length=1)
    version: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def _check(self) -> ItemSpec:
        if not _GROUP_RE.match(self.concept_group):
            raise ValueError(
                f"itemid {self.itemid}: concept_group {self.concept_group!r} is not "
                "'<family>.<measure>' in lower [a-z0-9_]"
            )
        if not self.plausible_low < self.plausible_high:
            raise ValueError(
                f"itemid {self.itemid}: plausible_low {self.plausible_low!r} must be below "
                f"plausible_high {self.plausible_high!r}"
            )
        if not self.accepted_units:
            raise ValueError(f"itemid {self.itemid}: accepted_units is empty")
        seen: dict[str, str] = {}
        for unit, conversion in self.accepted_units.items():
            norm = normalize_unit(unit)
            if norm in seen:
                raise ValueError(
                    f"itemid {self.itemid}: accepted units {seen[norm]!r} and {unit!r} "
                    f"normalise to the same key {norm!r}"
                )
            seen[norm] = unit
            try:
                formula_of(conversion)
            except UnitsError as exc:
                raise ValueError(f"itemid {self.itemid}: unit {unit!r}: {exc}") from None
        canonical = normalize_unit(self.canonical_unit)
        if canonical not in seen:
            raise ValueError(
                f"itemid {self.itemid}: canonical unit {self.canonical_unit!r} is not among "
                f"the accepted units {sorted(self.accepted_units)}"
            )
        if not formula_of(self.accepted_units[seen[canonical]]).is_identity:
            raise ValueError(
                f"itemid {self.itemid}: the canonical unit {self.canonical_unit!r} must "
                "convert by identity"
            )
        return self

    @property
    def canonical_norm(self) -> str:
        return normalize_unit(self.canonical_unit)

    @property
    def bounds(self) -> tuple[float, float]:
        return self.plausible_low, self.plausible_high

    def conversions(self) -> dict[str, Affine]:
        """``{normalised unit: Affine}`` over the accepted units."""
        return {normalize_unit(u): formula_of(c) for u, c in self.accepted_units.items()}

    def conversion(self, unit: str | None) -> Affine | None:
        """The conversion of ``unit`` (matched normalised), or None when not accepted."""
        return self.conversions().get(normalize_unit(unit))

    def accepts(self, unit: str | None) -> bool:
        return normalize_unit(unit) in self.conversions()


class ItemCatalogue(BaseModel):
    """The validated catalogue: unique itemids, one canonical unit per concept group."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int = Field(ge=1)
    version_note: str = ""
    items: tuple[ItemSpec, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _check(self) -> ItemCatalogue:
        ids = [s.itemid for s in self.items]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ValueError(f"duplicate itemids {dupes}")
        group_units: dict[str, tuple[int, str]] = {}
        for s in self.items:
            first = group_units.setdefault(s.concept_group, (s.itemid, s.canonical_norm))
            if first[1] != s.canonical_norm:
                raise ValueError(
                    f"concept group {s.concept_group!r}: itemid {s.itemid} is canonical in "
                    f"{s.canonical_unit!r} but itemid {first[0]} in another unit — one "
                    "canonical unit per group"
                )
        return self

    def by_itemid(self) -> dict[int, ItemSpec]:
        return {s.itemid: s for s in self.items}

    def spec(self, itemid: int) -> ItemSpec:
        """The :class:`ItemSpec` of ``itemid`` (:class:`UnknownItemError` otherwise)."""
        for s in self.items:
            if s.itemid == itemid:
                return s
        raise UnknownItemError(f"itemid {itemid} is not curated (units.py, EP-39)")

    def itemids(self, source: str | None = None) -> tuple[int, ...]:
        """The curated itemids (of one ``source`` table when given), ascending."""
        return tuple(sorted(s.itemid for s in self.items if source is None or s.source == source))

    @property
    def concept_groups(self) -> tuple[str, ...]:
        return tuple(sorted({s.concept_group for s in self.items}))

    def __len__(self) -> int:
        return len(self.items)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def catalogue_path() -> Path:
    """``src/mimicwarehouse/data/item_units.yaml`` inside the installed package."""
    return Path(str(files("mimicwarehouse").joinpath(DATA_DIRNAME, CATALOGUE_FILENAME)))


def load_catalogue_from(path: Path) -> ItemCatalogue:
    """Parse and validate one catalogue YAML (tests point this at crafted files);
    :class:`UnitsError` names every validation problem."""
    path = Path(path)
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise UnitsError(f"{path.name}: cannot read ({exc})") from exc
    if not isinstance(doc, dict):
        raise UnitsError(f"{path.name}: top level must be a mapping")
    try:
        return ItemCatalogue.model_validate(doc)
    except ValidationError as exc:
        lines = [f"{path.name}: {exc.error_count()} validation error(s)"]
        for e in exc.errors():
            loc = ".".join(str(p) for p in e["loc"])
            lines.append(f"  {loc}: {e['msg']}")
        raise UnitsError("\n".join(lines)) from None


@cache
def load_catalogue() -> ItemCatalogue:
    """The packaged catalogue (validated once per process)."""
    return load_catalogue_from(catalogue_path())


@cache
def _packaged_index() -> dict[int, ItemSpec]:
    return load_catalogue().by_itemid()


def _index(catalogue: ItemCatalogue | None) -> dict[int, ItemSpec]:
    return _packaged_index() if catalogue is None else catalogue.by_itemid()


def spec(itemid: int, catalogue: ItemCatalogue | None = None) -> ItemSpec:
    """The curated :class:`ItemSpec` of ``itemid`` (:class:`UnknownItemError` otherwise)."""
    try:
        return _index(catalogue)[itemid]
    except KeyError:
        raise UnknownItemError(f"itemid {itemid} is not curated (units.py, EP-39)") from None


def is_curated(itemid: int, catalogue: ItemCatalogue | None = None) -> bool:
    return itemid in _index(catalogue)


def bounds(itemid: int, catalogue: ItemCatalogue | None = None) -> tuple[float, float]:
    """``(plausible_low, plausible_high)`` of ``itemid`` in its canonical unit, inclusive
    at both ends (:class:`UnknownItemError` for an itemid the catalogue does not curate)
    — what EP-44's implausible-value counts read."""
    return spec(itemid, catalogue).bounds


def curated_itemids(
    source: str | None = None, catalogue: ItemCatalogue | None = None
) -> tuple[int, ...]:
    return (catalogue or load_catalogue()).itemids(source)


# ---------------------------------------------------------------------------
# harmonize — scalar, Polars, SQL
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Harmonized:
    """What :func:`harmonize` returns (the macro's STRUCT fields, same names)."""

    value_canonical: float | None
    unit_canonical: str | None
    converted: bool
    plausible: bool | None


def harmonize(
    itemid: int,
    value: float | None,
    valueuom: str | None,
    *,
    catalogue: ItemCatalogue | None = None,
) -> Harmonized:
    """Express ``value`` (charted in ``valueuom``) in the itemid's canonical unit.
    ``converted`` says the unit was recognised (identity conversions included) — an
    unknown unit passes the value through with ``converted = False``; an itemid the
    catalogue does not curate returns the value unchanged with ``unit_canonical`` and
    ``plausible`` None. ``plausible`` is inclusive at both bounds and None when the value
    is None."""
    item = _index(catalogue).get(itemid)
    if item is None:
        return Harmonized(None if value is None else float(value), None, False, None)
    affine = item.conversion(valueuom)
    if affine is None:
        canonical = None if value is None else float(value)
        converted = False
    else:
        canonical = None if value is None else affine.apply(float(value))
        converted = True
    low, high = item.bounds
    plausible = None if canonical is None else (low <= canonical <= high)
    return Harmonized(canonical, item.canonical_unit, converted, plausible)


def _polars_normalize_unit(expr: Any) -> Any:
    """The Polars twin of :func:`normalize_unit` over a string expression."""
    import polars as pl

    out = expr.cast(pl.String).fill_null("").str.replace_all(_SQL_WS, "")
    for sign in _DEGREE_SIGNS:
        out = out.str.replace_all(sign, "", literal=True)
    for sign in _MICRO_SIGNS:
        out = out.str.replace_all(sign, "u", literal=True)
    out = out.str.to_lowercase()
    out = out.str.replace(_SQL_DEG_PREFIX, "")
    return out.str.replace(_SQL_TRAILING_DOT, "")


def _conversion_frames(catalogue: ItemCatalogue) -> tuple[Any, Any]:
    """``(conversions, items)`` Polars frames: one row per (itemid, normalised unit)
    with scale / offset, and one row per itemid with canonical unit and bounds."""
    import polars as pl

    conv_rows: list[tuple[int, str, float, float]] = []
    item_rows: list[tuple[int, str, float, float]] = []
    for item in catalogue.items:
        item_rows.append(
            (item.itemid, item.canonical_unit, item.plausible_low, item.plausible_high)
        )
        for norm, affine in item.conversions().items():
            conv_rows.append((item.itemid, norm, affine.scale, affine.offset))
    conversions = pl.DataFrame(
        conv_rows,
        schema={
            "itemid": pl.Int64,
            "_mwh_norm": pl.String,
            "_mwh_scale": pl.Float64,
            "_mwh_offset": pl.Float64,
        },
        orient="row",
    )
    items = pl.DataFrame(
        item_rows,
        schema={
            "itemid": pl.Int64,
            "_mwh_unit": pl.String,
            "_mwh_low": pl.Float64,
            "_mwh_high": pl.Float64,
        },
        orient="row",
    )
    return conversions, items


def harmonize_frame(
    df: polars.DataFrame,
    *,
    itemid_col: str = "itemid",
    value_col: str = "valuenum",
    unit_col: str = "valueuom",
    catalogue: ItemCatalogue | None = None,
) -> polars.DataFrame:
    """The Polars twin of :func:`harmonize`: ``df`` plus the four columns
    ``value_canonical`` / ``unit_canonical`` / ``converted`` / ``plausible`` (row order
    kept; the input columns are untouched)."""
    import polars as pl

    cat = catalogue or load_catalogue()
    conversions, items = _conversion_frames(cat)
    rid = "_mwh_rid"
    out = (
        df.with_row_index(rid)
        .with_columns(
            pl.col(itemid_col).cast(pl.Int64).alias("_mwh_itemid"),
            _polars_normalize_unit(pl.col(unit_col)).alias("_mwh_norm"),
            pl.col(value_col).cast(pl.Float64).alias("_mwh_value"),
        )
        .join(
            conversions,
            left_on=["_mwh_itemid", "_mwh_norm"],
            right_on=["itemid", "_mwh_norm"],
            how="left",
        )
        .join(items, left_on="_mwh_itemid", right_on="itemid", how="left")
        .with_columns(
            pl.when(pl.col("_mwh_scale").is_not_null())
            .then(pl.col("_mwh_value") * pl.col("_mwh_scale") + pl.col("_mwh_offset"))
            .otherwise(pl.col("_mwh_value"))
            .alias("value_canonical"),
            pl.col("_mwh_unit").alias("unit_canonical"),
            pl.col("_mwh_scale").is_not_null().alias("converted"),
        )
        .with_columns(
            pl.when(pl.col("_mwh_unit").is_not_null() & pl.col("value_canonical").is_not_null())
            .then(pl.col("value_canonical").is_between(pl.col("_mwh_low"), pl.col("_mwh_high")))
            .otherwise(None)
            .alias("plausible")
        )
        .sort(rid)
    )
    return out.drop(
        rid,
        "_mwh_itemid",
        "_mwh_norm",
        "_mwh_value",
        "_mwh_scale",
        "_mwh_offset",
        "_mwh_unit",
        "_mwh_low",
        "_mwh_high",
    )


def plausible_mask(
    df: polars.DataFrame,
    *,
    itemid_col: str = "itemid",
    value_col: str = "valuenum",
    unit_col: str = "valueuom",
    catalogue: ItemCatalogue | None = None,
) -> polars.Series:
    """The ``plausible`` column of :func:`harmonize_frame` as a Boolean Series (null for
    uncurated itemids and null values) — what a mart filters on."""
    return harmonize_frame(
        df, itemid_col=itemid_col, value_col=value_col, unit_col=unit_col, catalogue=catalogue
    ).get_column("plausible")


def macro_statements(catalogue: ItemCatalogue | None = None) -> list[str]:
    """The ``CREATE OR REPLACE MACRO`` statements, in dependency order, that install
    ``mwh_harmonize`` and its helpers from the catalogue (module docstring): the same
    normalisation and affine arithmetic as the Python twins, so a value harmonised in SQL
    equals one harmonised in Python."""
    cat = catalogue or load_catalogue()
    # DOUBLE throughout: a DECIMAL literal times a DECIMAL scale overflows DuckDB's
    # DECIMAL(18) arithmetic, and the Python twin is float arithmetic anyway
    value = "CAST(value AS DOUBLE)"
    value_cases: list[str] = []
    known_cases: list[str] = []
    unit_cases: list[str] = []
    plausible_cases: list[str] = []
    for item in cat.items:
        conversions = item.conversions()
        branches = [
            f"WHEN {_sql_str(norm)} THEN {affine.sql(value)}"
            for norm, affine in conversions.items()
            if not affine.is_identity
        ]
        if branches:
            value_cases.append(
                f"WHEN itemid = {item.itemid} THEN (CASE {MACRO_UNIT_NORM}(valueuom) "
                f"{' '.join(branches)} ELSE {value} END)"
            )
        keys = ", ".join(_sql_str(norm) for norm in conversions)
        known_cases.append(
            f"WHEN itemid = {item.itemid} THEN ({MACRO_UNIT_NORM}(valueuom) IN ({keys}))"
        )
        unit_cases.append(f"WHEN {item.itemid} THEN {_sql_str(item.canonical_unit)}")
        plausible_cases.append(
            f"WHEN {item.itemid} THEN ({value} BETWEEN {item.plausible_low!r} "
            f"AND {item.plausible_high!r})"
        )
    value_body = "CASE " + " ".join(value_cases) + f" ELSE {value} END" if value_cases else value
    known_body = "CASE " + " ".join(known_cases) + " ELSE false END"
    unit_body = "CASE itemid " + " ".join(unit_cases) + " ELSE CAST(NULL AS VARCHAR) END"
    plausible_body = "CASE itemid " + " ".join(plausible_cases) + " ELSE CAST(NULL AS BOOLEAN) END"
    return [
        f"CREATE OR REPLACE MACRO {MACRO_UNIT_NORM}(u) AS {sql_normalize_unit('u')}",
        f"CREATE OR REPLACE MACRO {MACRO_UNIT_CANONICAL}(itemid) AS {unit_body}",
        f"CREATE OR REPLACE MACRO {MACRO_UNIT_KNOWN}(itemid, valueuom) AS {known_body}",
        f"CREATE OR REPLACE MACRO {MACRO_VALUE_CANONICAL}(itemid, value, valueuom) AS {value_body}",
        f"CREATE OR REPLACE MACRO {MACRO_PLAUSIBLE}(itemid, value) AS {plausible_body}",
        (
            f"CREATE OR REPLACE MACRO {MACRO_NAME}(itemid, value, valueuom) AS struct_pack("
            f"value_canonical := {MACRO_VALUE_CANONICAL}(itemid, value, valueuom), "
            f"unit_canonical := {MACRO_UNIT_CANONICAL}(itemid), "
            f"converted := {MACRO_UNIT_KNOWN}(itemid, valueuom), "
            f"plausible := {MACRO_PLAUSIBLE}(itemid, "
            f"{MACRO_VALUE_CANONICAL}(itemid, value, valueuom)))"
        ),
    ]


def install_macros(con: duckdb.DuckDBPyConnection, catalogue: ItemCatalogue | None = None) -> None:
    """Run :func:`macro_statements` on ``con`` (any connection — the catalog build
    connection, or an in-memory one in tests)."""
    for statement in macro_statements(catalogue):
        con.execute(statement)


_META_COMMENTS: dict[str, str] = {
    ITEM_UNITS_TABLE: (
        "The curated item catalogue (units.py, EP-39): one row per itemid x accepted unit "
        "— source table, label, concept_group, canonical_unit, the accepted unit as "
        "written and normalised, its formula / scale / intercept, inclusive plausible_low / "
        "plausible_high in the canonical unit, curation_note, source_ref, versions."
    ),
    VARIANTS_TABLE: (
        "Unit variants per curated itemid on this tier (EP-39): (itemid, source, "
        "valueuom, unit_norm, n_rows, share, suppressed, expected, k). n_rows / share are "
        "NULL where the k-rule suppressed the cell (suppressed = true); share is the share "
        "among the released rows of the itemid; expected = the unit is accepted by the "
        "catalogue. Raw counts stay in the data root, never in a catalog."
    ),
    DICTIONARY_TABLE: (
        "meta.itemids (d_items + d_labitems) joined with the curation columns curated, "
        "concept_group, canonical_unit, plausible_low, plausible_high (units.py, EP-39); "
        "unit_hint stays on meta.columns."
    ),
}


def register_units(con: duckdb.DuckDBPyConnection, tier: str) -> None:
    """The catalog extension (:data:`mimicwarehouse.catalog.build.CATALOG_EXTENSIONS`
    entry, after EP-37's discovery walker): install the ``mwh_harmonize`` macro family in
    the tier catalog and comment the three ``meta.item_*`` tables the walker registered
    from ``lake/meta/<tier>/``. Never opens a connection of its own; DDL only."""
    install_macros(con)
    present = {
        str(r[0])
        for r in con.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'meta'"
        ).fetchall()
    }
    commented = 0
    for table, comment in _META_COMMENTS.items():
        if table in present:
            con.execute(f'COMMENT ON TABLE meta."{table}" IS {_sql_str(comment)}')
            commented += 1
    _LOG.info(
        "catalog extension units: %s + helpers installed, %d meta.item_* table(s) on tier %s",
        MACRO_NAME,
        commented,
        tier,
    )


# ---------------------------------------------------------------------------
# The meta tables — DAG steps (units.item_units / units.variants / units.dictionary)
# ---------------------------------------------------------------------------

ITEM_UNITS_COLUMNS: tuple[tuple[str, str], ...] = (
    ("itemid", "INTEGER"),
    ("source", "VARCHAR"),
    ("label", "VARCHAR"),
    ("concept_group", "VARCHAR"),
    ("canonical_unit", "VARCHAR"),
    ("accepted_unit", "VARCHAR"),
    ("unit_norm", "VARCHAR"),
    ("formula", "VARCHAR"),
    ("scale", "DOUBLE"),
    ("intercept", "DOUBLE"),  # `offset` is a reserved word in DuckDB SQL
    ("plausible_low", "DOUBLE"),
    ("plausible_high", "DOUBLE"),
    ("curation_note", "VARCHAR"),
    ("source_ref", "VARCHAR"),
    ("version", "INTEGER"),
    ("catalogue_version", "INTEGER"),
)
VARIANTS_COLUMNS: tuple[tuple[str, str], ...] = (
    ("itemid", "INTEGER"),
    ("source", "VARCHAR"),
    ("valueuom", "VARCHAR"),
    ("unit_norm", "VARCHAR"),
    ("n_rows", "BIGINT"),
    ("share", "DOUBLE"),
    ("suppressed", "BOOLEAN"),
    ("expected", "BOOLEAN"),
    ("k", "INTEGER"),
    ("tier", "VARCHAR"),
    ("build_id", "VARCHAR"),
)
RAW_VARIANTS_COLUMNS: tuple[tuple[str, str], ...] = tuple(
    c for c in VARIANTS_COLUMNS if c[0] not in ("suppressed", "k")
)


def meta_table_path(lake_root: Path | str, tier: str, table: str) -> Path:
    """``<lake_root>/meta/<tier>/<table>.parquet`` (EP-29's meta layout)."""
    from mimicwarehouse.catalog.profile import meta_dir

    return meta_dir(lake_root, tier) / f"{table}.parquet"


def raw_variants_path(lake_root: Path | str, tier: str) -> Path:
    """``<lake_root>/meta/<tier>/raw/item_unit_variants.parquet`` — the unsuppressed
    counts (data root only; the walker never registers a subdirectory)."""
    from mimicwarehouse.catalog.profile import meta_dir

    return meta_dir(lake_root, tier) / RAW_DIRNAME / f"{VARIANTS_TABLE}.parquet"


def _write_parquet(
    con: duckdb.DuckDBPyConnection,
    dest: Path,
    columns: tuple[tuple[str, str], ...],
    rows: Iterable[Iterable[Any]],
) -> int:
    """Rows -> ``dest`` through a temp table + ``COPY`` + :func:`publish.replace` (the
    EP-29 / EP-37 meta writer shape); returns the file size."""
    from mimicwarehouse import publish

    dest.parent.mkdir(parents=True, exist_ok=True)
    ddl = ", ".join(f'"{name}" {typ}' for name, typ in columns)
    con.execute(f"CREATE OR REPLACE TEMP TABLE _mwh_units ({ddl})")
    try:
        materialised = [list(r) for r in rows]
        if materialised:
            placeholders = ", ".join("?" for _ in columns)
            con.executemany(f"INSERT INTO _mwh_units VALUES ({placeholders})", materialised)
        tmp = dest.with_name(dest.name + ".tmp")
        con.execute(
            f"COPY _mwh_units TO {_sql_str(tmp.resolve().as_posix())} "
            "(FORMAT PARQUET, COMPRESSION ZSTD)"
        )
        publish.replace(tmp, dest)
    finally:
        con.execute("DROP TABLE IF EXISTS _mwh_units")
    return dest.stat().st_size


def item_units_rows(catalogue: ItemCatalogue | None = None) -> list[list[Any]]:
    """The ``meta.item_units`` rows: one per itemid x accepted unit, catalogue order."""
    cat = catalogue or load_catalogue()
    rows: list[list[Any]] = []
    for item in cat.items:
        for unit, conversion in item.accepted_units.items():
            affine = formula_of(conversion)
            rows.append(
                [
                    item.itemid,
                    item.source,
                    item.label,
                    item.concept_group,
                    item.canonical_unit,
                    unit,
                    normalize_unit(unit),
                    formula_name(conversion),
                    affine.scale,
                    affine.offset,
                    item.plausible_low,
                    item.plausible_high,
                    item.curation_note,
                    item.source_ref,
                    item.version,
                    cat.version,
                ]
            )
    return rows


def run_item_units(step: Step, ctx: StepContext) -> StepOutcome:
    """The ``units.item_units`` handler: materialise the catalogue as
    ``lake/meta/<tier>/item_units.parquet``."""
    from mimicwarehouse.dag.runner import StepOutcome

    rows = item_units_rows()
    dest = meta_table_path(ctx.lake_root, ctx.tier, ITEM_UNITS_TABLE)
    nbytes = _write_parquet(ctx.con, dest, ITEM_UNITS_COLUMNS, rows)
    _LOG.info(
        "meta.item_units (%s): %d itemid(s), %d accepted-unit row(s) — %s",
        ctx.tier,
        len(load_catalogue()),
        len(rows),
        dest,
    )
    return StepOutcome(rows=len(rows), bytes_out=nbytes, files=1)


def _present_tables(con: duckdb.DuckDBPyConnection) -> set[str]:
    rows = con.execute(
        "SELECT table_schema || '.' || table_name FROM information_schema.tables"
    ).fetchall()
    return {str(r[0]) for r in rows}


def variants_sql(catalogue: ItemCatalogue, present: set[str]) -> str:
    """The one aggregate over every source table present on the connection: ``(itemid,
    source, valueuom, unit_norm, n_rows)`` for the curated itemids only; an empty string
    when no source table is present."""
    selects: list[str] = []
    for source, (schema, table, _value_col, unit_col) in SOURCE_COLUMNS.items():
        ids = catalogue.itemids(source)
        if not ids or f"{schema}.{table}" not in present:
            continue
        id_list = ", ".join(str(i) for i in ids)
        selects.append(
            f"SELECT itemid, {_sql_str(source)} AS source, {unit_col} AS valueuom, "
            f"{sql_normalize_unit(unit_col)} AS unit_norm, count(*) AS n_rows "
            f'FROM {schema}."{table}" WHERE itemid IN ({id_list}) GROUP BY 1, 2, 3, 4'
        )
    return " UNION ALL ".join(selects)


def compute_variants(
    con: duckdb.DuckDBPyConnection, catalogue: ItemCatalogue | None = None
) -> polars.DataFrame:
    """The raw variants frame — ``itemid, source, valueuom, unit_norm, n_rows, share,
    expected`` (``share`` over all rows of the itemid) — from the source tables present
    on ``con`` (:class:`UnitsError` when none is)."""
    import polars as pl

    cat = catalogue or load_catalogue()
    sql = variants_sql(cat, _present_tables(con))
    if not sql:
        raise UnitsError(
            "no source table (chartevents / labevents / outputevents / inputevents) is "
            "present on the connection — stage the event tables first"
        )
    df = con.execute(sql).pl()
    index = cat.by_itemid()
    expected = [
        index[int(i)].accepts(u) if int(i) in index else False
        for i, u in zip(
            df.get_column("itemid").to_list(), df.get_column("valueuom").to_list(), strict=True
        )
    ]
    return (
        df.with_columns(
            pl.col("itemid").cast(pl.Int64),
            pl.col("n_rows").cast(pl.Int64),
            pl.Series("expected", expected, dtype=pl.Boolean),
        )
        .with_columns((pl.col("n_rows") / pl.col("n_rows").sum().over("itemid")).alias("share"))
        .select("itemid", "source", "valueuom", "unit_norm", "n_rows", "share", "expected")
        .sort("itemid", "source", "unit_norm")
    )


def suppress_variants(raw: polars.DataFrame, k: int) -> polars.DataFrame:
    """The published shape of a raw variants frame: every row kept, ``n_rows`` / ``share``
    blanked and ``suppressed = true`` on the rows :data:`mimicwarehouse.safe.SUPPRESSOR`
    drops at ``k``, ``share`` recomputed among the released rows of each itemid (module
    docstring), plus the ``k`` column."""
    import polars as pl

    from mimicwarehouse import safe

    rid = "_mwh_rid"
    indexed = raw.with_row_index(rid)
    kept, _dropped = safe.SUPPRESSOR(indexed, k, ["n_rows"])
    kept_ids = set(kept.get_column(rid).to_list())
    suppressed = pl.Series(
        "suppressed",
        [i not in kept_ids for i in indexed.get_column(rid).to_list()],
        dtype=pl.Boolean,
    )
    released_rows = pl.when(~pl.col("suppressed")).then(pl.col("n_rows")).otherwise(None)
    return (
        indexed.with_columns(suppressed)
        .with_columns(released_rows.alias("n_rows"))
        .with_columns(
            (pl.col("n_rows") / pl.col("n_rows").sum().over("itemid")).alias("share"),
            pl.lit(k, dtype=pl.Int32).alias("k"),
        )
        .drop(rid)
        .select(
            "itemid",
            "source",
            "valueuom",
            "unit_norm",
            "n_rows",
            "share",
            "suppressed",
            "expected",
            "k",
        )
    )


def run_variants(step: Step, ctx: StepContext) -> StepOutcome:
    """The ``units.variants`` handler (module docstring): the aggregate over the tier's
    staged source tables, the raw file under ``meta/<tier>/raw/``, the suppressed table
    as ``meta/<tier>/item_unit_variants.parquet``."""
    from mimicwarehouse.concepts.runner import ensure_source_views
    from mimicwarehouse.dag.runner import StepOutcome

    ensure_source_views(ctx)
    raw = compute_variants(ctx.con)
    k = ctx.settings.k_suppression
    published = suppress_variants(raw, k)
    tier, build_id = ctx.tier, ctx.build_id
    raw_rows = [[*r, tier, build_id] for r in raw.rows()]
    published_rows = [[*r, tier, build_id] for r in published.rows()]
    raw_bytes = _write_parquet(
        ctx.con, raw_variants_path(ctx.lake_root, tier), RAW_VARIANTS_COLUMNS, raw_rows
    )
    dest = meta_table_path(ctx.lake_root, tier, VARIANTS_TABLE)
    nbytes = _write_parquet(ctx.con, dest, VARIANTS_COLUMNS, published_rows)
    n_suppressed = int(published.get_column("suppressed").sum())
    _LOG.info(
        "meta.item_unit_variants (%s): %d variant row(s) over %d itemid(s), %d suppressed at "
        "k=%d — %s",
        tier,
        published.height,
        published.get_column("itemid").n_unique(),
        n_suppressed,
        k,
        dest,
    )
    return StepOutcome(rows=published.height, bytes_out=nbytes + raw_bytes, files=2)


DICTIONARY_CURATION_COLUMNS: tuple[tuple[str, str], ...] = (
    ("itemid", "INTEGER"),
    ("concept_group", "VARCHAR"),
    ("canonical_unit", "VARCHAR"),
    ("plausible_low", "DOUBLE"),
    ("plausible_high", "DOUBLE"),
)


def run_dictionary(step: Step, ctx: StepContext) -> StepOutcome:
    """The ``units.dictionary`` handler: EP-29's ``meta.itemids`` SELECT (over the tier's
    ``d_items`` / ``d_labitems`` views on the build connection) LEFT JOIN the curation
    columns → ``lake/meta/<tier>/item_dictionary.parquet``."""
    from mimicwarehouse import publish
    from mimicwarehouse.catalog.build import ITEMIDS_SELECT_SQL
    from mimicwarehouse.concepts.runner import ensure_source_views
    from mimicwarehouse.dag.runner import StepOutcome

    ensure_source_views(ctx)
    present = _present_tables(ctx.con)
    missing = [t for t in ("mimiciv_icu.d_items", "mimiciv_hosp.d_labitems") if t not in present]
    if missing:
        raise UnitsError(
            f"{', '.join(missing)} not staged for tier {ctx.tier} — the item dictionary needs "
            "both item dimensions (stage them first)"
        )
    cat = load_catalogue()
    ddl = ", ".join(f'"{name}" {typ}' for name, typ in DICTIONARY_CURATION_COLUMNS)
    ctx.con.execute(f"CREATE OR REPLACE TEMP TABLE _mwh_item_curation ({ddl})")
    dest = meta_table_path(ctx.lake_root, ctx.tier, DICTIONARY_TABLE)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".tmp")
    try:
        ctx.con.executemany(
            "INSERT INTO _mwh_item_curation VALUES (?, ?, ?, ?, ?)",
            [
                [s.itemid, s.concept_group, s.canonical_unit, s.plausible_low, s.plausible_high]
                for s in cat.items
            ],
        )
        ctx.con.execute(
            f"COPY (SELECT i.*, c.itemid IS NOT NULL AS curated, c.concept_group, "
            f"c.canonical_unit, c.plausible_low, c.plausible_high "
            f"FROM ({ITEMIDS_SELECT_SQL}) AS i LEFT JOIN _mwh_item_curation AS c "
            f"ON i.itemid = c.itemid ORDER BY i.source, i.itemid) "
            f"TO {_sql_str(tmp.resolve().as_posix())} (FORMAT PARQUET, COMPRESSION ZSTD)"
        )
        publish.replace(tmp, dest)
    finally:
        ctx.con.execute("DROP TABLE IF EXISTS _mwh_item_curation")
    counted = ctx.con.execute(
        f"SELECT count(*), count(*) FILTER (WHERE curated) FROM read_parquet("
        f"{_sql_str(dest.resolve().as_posix())})"
    ).fetchone()
    rows, curated = (int(counted[0]), int(counted[1])) if counted else (0, 0)
    _LOG.info(
        "meta.item_dictionary (%s): %d itemid(s), %d curated — %s", ctx.tier, rows, curated, dest
    )
    return StepOutcome(rows=rows, bytes_out=dest.stat().st_size, files=1)


# ---------------------------------------------------------------------------
# The unit-inconsistency report
# ---------------------------------------------------------------------------

REPORT_COLUMNS: tuple[str, ...] = (
    "itemid",
    "label",
    "canonical_unit",
    "n_variants",
    "n_suppressed",
    "dominant_unit",
    "dominant_share",
    "unexpected_units",
    "flagged",
)
_VARIANTS_SQL = (
    "SELECT itemid, source, valueuom, unit_norm, n_rows, share, suppressed, expected "
    f"FROM meta.{VARIANTS_TABLE} ORDER BY itemid, source, unit_norm"
)
_UNITS_SQL = (
    f"SELECT DISTINCT itemid, label, canonical_unit FROM meta.{ITEM_UNITS_TABLE} ORDER BY itemid"
)


def summarize_variants(variants: polars.DataFrame, units: polars.DataFrame) -> polars.DataFrame:
    """One row per curated itemid (``units``: itemid, label, canonical_unit) from the
    published variants frame: ``n_variants`` (rows, suppressed ones included),
    ``n_suppressed``, ``dominant_unit`` / ``dominant_share`` (the released row with the
    most rows; ``(null)`` spells a NULL unit), ``unexpected_units`` (the unit strings the
    catalogue does not accept, sorted) and ``flagged`` (more than one variant, or any
    unexpected unit). An itemid without variant rows on the tier reads ``n_variants = 0``."""
    import polars as pl

    labelled = variants.with_columns(pl.col("valueuom").fill_null(NULL_UNIT_LABEL))
    dominant = (
        labelled.filter(~pl.col("suppressed"))
        .sort(["itemid", "n_rows", "valueuom"], descending=[False, True, False])
        .group_by("itemid", maintain_order=True)
        .agg(
            pl.col("valueuom").first().alias("dominant_unit"),
            pl.col("share").first().alias("dominant_share"),
        )
    )
    per_item = labelled.group_by("itemid").agg(
        pl.len().alias("n_variants"),
        pl.col("suppressed").sum().cast(pl.Int64).alias("n_suppressed"),
        pl.col("valueuom").filter(~pl.col("expected")).unique().sort().alias("unexpected_units"),
    )
    empty_list = pl.lit([]).cast(pl.List(pl.String))
    out = (
        units.select("itemid", "label", "canonical_unit")
        .with_columns(pl.col("itemid").cast(pl.Int64))
        .join(per_item.with_columns(pl.col("itemid").cast(pl.Int64)), on="itemid", how="left")
        .join(dominant.with_columns(pl.col("itemid").cast(pl.Int64)), on="itemid", how="left")
        .with_columns(
            pl.col("n_variants").fill_null(0).cast(pl.Int64),
            pl.col("n_suppressed").fill_null(0).cast(pl.Int64),
            pl.when(pl.col("unexpected_units").is_null())
            .then(empty_list)
            .otherwise(pl.col("unexpected_units"))
            .alias("unexpected_units"),
        )
        .with_columns(
            ((pl.col("n_variants") > 1) | (pl.col("unexpected_units").list.len() > 0)).alias(
                "flagged"
            )
        )
        .sort("itemid")
    )
    return out.select(*REPORT_COLUMNS)


def report(
    tier: str,
    *,
    settings: Settings | None = None,
    actor: str | None = None,
) -> polars.DataFrame:
    """The unit-inconsistency report of one tier catalog (module docstring): the two
    ``meta.item_*`` tables read through :func:`mimicwarehouse.safe.safe_query` (audited;
    registry-exempt, already k-suppressed at build time), folded by
    :func:`summarize_variants`. Refusals and a missing catalog propagate as
    ``safe_query``'s own errors."""
    from mimicwarehouse.config import get_settings
    from mimicwarehouse.safe import safe_query

    resolved = settings or get_settings()
    variants = safe_query(
        _VARIANTS_SQL, tier=tier, row_cap=REPORT_ROW_CAP, settings=resolved, actor=actor
    ).df
    units = safe_query(
        _UNITS_SQL, tier=tier, row_cap=REPORT_ROW_CAP, settings=resolved, actor=actor
    ).df
    return summarize_variants(variants, units)


# ---------------------------------------------------------------------------
# mwh units — the CLI
# ---------------------------------------------------------------------------

units_app = typer.Typer(
    name="units",
    help=(
        "Itemid curation + unit harmonization (EP-39): mwh units check (validate the "
        "packaged catalogue), mwh units report --tier <t> (unit variants per curated itemid, "
        "aggregates only)."
    ),
    no_args_is_help=True,
    rich_markup_mode="rich",
)


@units_app.command("check")
def check_command(
    json_output: Annotated[
        bool, typer.Option("--json", help="Print the catalogue summary as JSON.")
    ] = False,
) -> None:
    """Validate the packaged item catalogue and print its summary (no data access)."""
    prefix = "mwh units check"
    try:
        cat = load_catalogue_from(catalogue_path())
    except UnitsError as exc:
        fail(prefix, str(exc), code=EXIT_FINDINGS)
    rows = item_units_rows(cat)
    summary = {
        "catalogue_version": cat.version,
        "items": len(cat),
        "concept_groups": len(cat.concept_groups),
        "accepted_unit_rows": len(rows),
        "sources": {s: len(cat.itemids(s)) for s in SOURCES if cat.itemids(s)},
        "formulas": sorted({formula_name(c) for i in cat.items for c in i.accepted_units.values()}),
    }
    if json_output:
        emit_json(summary)
        return
    console.print(
        f"item catalogue v{cat.version}: {len(cat)} item(s) in {len(cat.concept_groups)} "
        f"concept group(s), {len(rows)} accepted-unit row(s); ok",
        highlight=False,
    )


def _fmt_share(value: Any) -> str:
    return "" if value is None else f"{float(value):.3f}"


@units_app.command("report")
def report_command(
    ctx: typer.Context,
    tier: Annotated[
        str, typer.Option("--tier", help="Tier catalog to report on: fixture | demo | dev | full.")
    ],
    output_format: Annotated[
        str, typer.Option("--format", help="Output format: table | json.")
    ] = "table",
) -> None:
    """Unit variants per curated itemid from meta.item_unit_variants (aggregates only;
    small cells were suppressed when the table was built; every read is audited)."""
    prefix = "mwh units report"
    state: CliState = ctx.obj
    if output_format not in ("table", "json"):
        fail(prefix, f"unknown --format {output_format!r}; expected table | json")
    from mimicwarehouse.catalog.cli import TIERS, safe_cli_errors

    if tier not in TIERS:
        fail(prefix, f"unknown tier {tier!r}; expected one of {', '.join(TIERS)}")
    settings = state.settings
    with safe_cli_errors(prefix):
        df = report(tier, settings=settings)
    flagged = int(df.get_column("flagged").sum())
    if output_format == "json":
        emit_json({"tier": tier, "flagged": flagged, "items": df.to_dicts()})
        return
    from rich.table import Table as RichTable

    from mimicwarehouse.inventory import fmt_int

    listing = RichTable(title=f"meta.{VARIANTS_TABLE} ({tier})", pad_edge=False)
    for name, justify in (
        ("itemid", "right"),
        ("label", "left"),
        ("canonical", "left"),
        ("variants", "right"),
        ("suppressed", "right"),
        ("dominant unit", "left"),
        ("share", "right"),
        ("unexpected units", "left"),
        ("flag", "left"),
    ):
        listing.add_column(name, justify=justify)  # type: ignore[arg-type]
    for row in df.to_dicts():
        listing.add_row(
            fmt_int(int(row["itemid"])),
            escape(str(row["label"])),
            escape(str(row["canonical_unit"])),
            fmt_int(int(row["n_variants"])),
            fmt_int(int(row["n_suppressed"])),
            escape("" if row["dominant_unit"] is None else str(row["dominant_unit"])),
            _fmt_share(row["dominant_share"]),
            escape(", ".join(str(u) for u in (row["unexpected_units"] or []))),
            "!" if row["flagged"] else "",
        )
    console.print(listing)
    console.print(
        console_safe(
            f"{fmt_int(df.height)} curated itemid(s), {fmt_int(flagged)} flagged; cells below "
            "k were suppressed when the table was built (GOVERNANCE section 5)"
        ),
        highlight=False,
    )


# ---------------------------------------------------------------------------
# docs/methods/units.md — the generated blocks
# ---------------------------------------------------------------------------

METHODS_DOC_RELPATH = Path("docs") / "methods" / "units.md"
FORMULAS_MARK = ("<!-- formulas:begin -->", "<!-- formulas:end -->")
ITEMS_MARK = ("<!-- items:begin -->", "<!-- items:end -->")


def _md_table(header: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(lines) + "\n"


def _fmt_num(value: float) -> str:
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return text if text not in ("", "-0") else "0"


def render_formula_table() -> str:
    """The named formulas as a Markdown table (from :data:`FORMULAS`)."""
    rows = []
    for name, affine in FORMULAS.items():
        forward = affine.sql("x").replace("(x)", "x") if not affine.is_identity else "x"
        rows.append([f"`{name}`", f"`{forward}`", FORMULA_NOTES.get(name, "")])
    return _md_table(["formula", "canonical = f(x)", "note"], rows)


def _accepted_cell(item: ItemSpec) -> str:
    parts = []
    for unit, conversion in item.accepted_units.items():
        shown = "(blank)" if unit == "" else unit
        name = formula_name(conversion)
        if name == IDENTITY:
            parts.append(f"`{shown}`")
        elif name == FACTOR:
            parts.append(f"`{shown}` (x {_fmt_num(float(conversion))})")
        else:
            parts.append(f"`{shown}` ({name})")
    return ", ".join(parts)


def render_item_table(catalogue: ItemCatalogue | None = None) -> str:
    """The catalogue as a Markdown table (bounds inclusive, canonical unit)."""
    cat = catalogue or load_catalogue()
    rows = []
    for item in cat.items:
        rows.append(
            [
                str(item.itemid),
                item.source,
                item.label,
                f"`{item.concept_group}`",
                f"`{item.canonical_unit}`",
                _accepted_cell(item),
                f"{_fmt_num(item.plausible_low)} to {_fmt_num(item.plausible_high)}",
                f"`{item.source_ref}`",
            ]
        )
    return _md_table(
        [
            "itemid",
            "source",
            "label",
            "concept group",
            "canonical unit",
            "accepted units (conversion)",
            "plausible (inclusive)",
            "source_ref",
        ],
        rows,
    )


def methods_doc_path() -> Path:
    """``mimicwarehouse/docs/methods/units.md``."""
    from mimicwarehouse.config import workspace_root

    return workspace_root() / METHODS_DOC_RELPATH


def sync_methods_doc(path: Path | None = None) -> Path:
    """Re-render the generated blocks of the methods page in place (idempotent;
    ``python -m mimicwarehouse.units`` runs it; ``test_ep39`` asserts the page is in
    sync). The narrative around the markers is never touched."""
    from mimicwarehouse.dag.benchmarks import replace_marked_block

    target = Path(path) if path is not None else methods_doc_path()
    text = target.read_text(encoding="utf-8")
    for (begin, end), block in (
        (FORMULAS_MARK, render_formula_table()),
        (ITEMS_MARK, render_item_table()),
    ):
        text = replace_marked_block(text, block, begin=begin, end=end)
    target.write_text(text.rstrip("\n") + "\n", encoding="utf-8", newline="\n")
    return target


__all__ = [
    "CATALOGUE_FILENAME",
    "DAG_TAG",
    "DICTIONARY_TABLE",
    "FACTOR",
    "FORMULAS",
    "FORMULAS_MARK",
    "FORMULA_NOTES",
    "IDENTITY",
    "ITEMS_MARK",
    "ITEM_UNITS_COLUMNS",
    "ITEM_UNITS_TABLE",
    "MACRO_NAME",
    "MACRO_PLAUSIBLE",
    "MACRO_UNIT_CANONICAL",
    "MACRO_UNIT_KNOWN",
    "MACRO_UNIT_NORM",
    "MACRO_VALUE_CANONICAL",
    "METHODS_DOC_RELPATH",
    "MIN_CURATED_ITEMS",
    "NULL_UNIT_LABEL",
    "RAW_DIRNAME",
    "RAW_VARIANTS_COLUMNS",
    "REPORT_COLUMNS",
    "REPORT_ROW_CAP",
    "SOURCES",
    "SOURCE_COLUMNS",
    "STEP_DICTIONARY",
    "STEP_ITEM_UNITS",
    "STEP_VARIANTS",
    "VARIANTS_COLUMNS",
    "VARIANTS_TABLE",
    "Affine",
    "Harmonized",
    "ItemCatalogue",
    "ItemSpec",
    "UnitsError",
    "UnknownItemError",
    "bounds",
    "catalogue_path",
    "compute_variants",
    "curated_itemids",
    "formula_name",
    "formula_of",
    "harmonize",
    "harmonize_frame",
    "install_macros",
    "is_curated",
    "item_units_rows",
    "load_catalogue",
    "load_catalogue_from",
    "macro_statements",
    "meta_table_path",
    "methods_doc_path",
    "normalize_unit",
    "plausible_mask",
    "raw_variants_path",
    "register_units",
    "render_formula_table",
    "render_item_table",
    "report",
    "run_dictionary",
    "run_item_units",
    "run_variants",
    "spec",
    "sql_normalize_unit",
    "summarize_variants",
    "suppress_variants",
    "sync_methods_doc",
    "units_app",
    "variants_sql",
]


if __name__ == "__main__":
    print(sync_methods_doc())
