"""The cohort specification schema: a declarative, versioned YAML with a grain, an index
event, ordered criteria, windows, washout, follow-up, an era filter and a definition hash
(EP-46 item 1; DESIGN §9, §15; GOVERNANCE §12; D-5, D-18, D-25).

A **cohort spec** is one YAML document (``specs/<id>.yaml`` in the package, or a study
file under ``studies/<study_id>/cohorts/``) that names a study population by *rules*,
never by data::

    id: first_icu_adults
    version: "1.0.0"
    title: First ICU stay of adult patients
    grain: icustay                        # timesem's registry; placeholders are refused
    index_event: {rule: first_icu_stay}   # | {phenotype_onset: id@version} | {concept_time: {...}}
    inclusion:
      - {label: adult, age: {min: 18, at: index}}
    exclusion:
      - {label: short_icu_stay, los: {max_hours: 4, of: icu}}
    observation_window: {start_h: -24, end_h: 0}
    washout: {rule: none}
    follow_up: {outcome: in_hospital_mortality, competing_events: [discharge_alive]}
    era_filter: []
    degeneracy_probe: {columns: [gender, admission_type, first_careunit, anchor_year_group]}

* **Criteria** are ordered lists (``inclusion`` keeps the units a criterion's predicate
  selects, ``exclusion`` removes them; the compiler emits one CTE per criterion in this
  order, EP-47). Each criterion is a mapping with a ``label`` (a slug, unique in the
  document — the attrition step name) and exactly one kind key: ``age`` (``[min, max)``
  at the index), ``demographic`` (set membership per admission / patient / first-ICU-unit
  field), ``codeset`` (billed ICD codes of a dual EP-40 set: same admission, a prior
  window or any prior admission), ``phenotype`` (an EP-41 phenotype ``before`` / ``at`` /
  ``within_hours`` of the index), ``concept`` (a column predicate on a cataloged relation,
  optionally windowed), ``los`` (ICU / hospital length of stay in hours, ``[min, max)``),
  ``data_availability`` (at least ``min_rows`` rows of a table, optionally of some
  itemids, in a window), ``prior_admissions`` (an inclusive count of prior admissions
  within a lookback) and ``custom_sql`` (a hashed SELECT yielding the grain keys —
  allowed, and flagged ``custom`` in attrition and reports). Windows are ``[start_h,
  end_h)`` in hours relative to the index; lookbacks are within-patient relative time;
  counts are inclusive.
* **MIMIC caveats baked in.** No calendar date anywhere in the definition (per-patient
  date shift, DESIGN §7): a date-like string or a YAML date object is refused; the only
  cross-patient axis is ``era_filter`` over ``anchor_year_group``. Ages >= 89 are
  shipped as 91, so an age bound inside ``(89, 91]`` is refused (write ``89``). The
  follow-up outcome is one of ``timesem.CENSORING_RULES`` (the ``dod`` visibility horizon
  applies to every out-of-hospital outcome); ``discharge_alive`` is the optional
  competing event of an in-hospital outcome. Grains come from ``timesem.GRAINS`` and a
  placeholder grain (``edstay`` / ``note``) is refused with the EP that ships it.
* **References** are the code sets (``codeset`` criteria, the washout's set) and the
  phenotypes (``phenotype`` criteria, a ``phenotype_onset`` index) the spec names; the
  registry resolves each to its ``def_hash`` and the spec's **``def_hash``** is the sha256
  of the canonical JSON of everything except the documentation fields (``title``,
  ``description``, ``notes``, ``what_it_does_not_claim`` and a criterion's
  ``description``) with those hashes inlined — key order, whitespace and defaults never
  move it; the definition or a referenced definition does.
* ``version`` is semver; the ``(id, version)`` pair is immutable once recorded in the
  registry lock (:mod:`~mimicwarehouse.cohort.registry` raises
  :class:`CohortSpecFrozenError` when a locked pair's hash moved — bump the version).
* **Level-degeneracy probe** (EP-31 lesson, EP-33 amendment): ``degeneracy_probe.columns``
  names the categorical columns ``mwh cohort validate --tier <t>`` probes for zero-event /
  all-event levels against the follow-up outcome (default: the tracer's four); the
  model-side half is EP-79's.

Everything here is schema text; no data access. Import budget: pydantic + yaml + stdlib
plus :mod:`mimicwarehouse.timesem` (stdlib-only, already on the start-up path) — the
``cohort`` sub-app imports this module at ``mwh`` start-up.
"""

from __future__ import annotations

import dataclasses
import hashlib
import re
from collections.abc import Iterator, Mapping
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from mimicwarehouse import timesem
from mimicwarehouse.codesets.spec import canonical_json

#: The criterion kinds, canonical order (the one kind key a criterion carries).
CRITERION_KINDS: tuple[str, ...] = (
    "age",
    "demographic",
    "codeset",
    "phenotype",
    "concept",
    "los",
    "data_availability",
    "prior_admissions",
    "custom_sql",
)
#: The index-event forms (exactly one).
INDEX_EVENT_KINDS: tuple[str, ...] = ("rule", "phenotype_onset", "concept_time")
#: The admission / patient / first-ICU-unit fields a ``demographic`` criterion may test
#: (equality / set membership only; values are public vocabulary).
DEMOGRAPHIC_FIELDS: tuple[str, ...] = (
    "gender",
    "admission_type",
    "admission_location",
    "discharge_location",
    "insurance",
    "first_careunit",
    "language",
    "marital_status",
)
#: The categorical columns the degeneracy probe may name.
PROBE_COLUMNS: tuple[str, ...] = (*DEMOGRAPHIC_FIELDS, "anchor_year_group")
#: The tracer's four covariates (EP-31) — the probe's default.
DEFAULT_PROBE_COLUMNS: tuple[str, ...] = (
    "gender",
    "admission_type",
    "first_careunit",
    "anchor_year_group",
)
#: Documentation fields: never hashed, never scanned for dates.
DOCUMENTATION_FIELDS: frozenset[str] = frozenset(
    {"title", "description", "notes", "what_it_does_not_claim"}
)
#: The follow-up outcomes (``timesem.CENSORING_RULES`` names) and competing events.
OUTCOMES: tuple[str, ...] = tuple(timesem.CENSORING_RULES)
COMPETING_EVENTS: tuple[str, ...] = (timesem.DISCHARGE_ALIVE,)
#: The grains a spec may name (available ones) — the JSON-schema enum.
AVAILABLE_GRAINS: tuple[str, ...] = tuple(g.name for g in timesem.available_grains())
#: ``timesem.AGE_CAP`` — ages >= 89 are shipped as 91.
AGE_CAP = timesem.AGE_CAP
AGE_CAP_FLOOR = 89
#: Longest string value a definition field may carry (``safe.FREE_TEXT_MAX_CHARS``
#: mirrored: labels and values may reach a session through the registry index).
VALUE_MAX_CHARS = 64
#: Longest criterion label (a CTE name in EP-47's chain).
LABEL_MAX_CHARS = 40
#: The default observation window: the 24 hours before the index, ``[-24, 0)``.
DEFAULT_OBSERVATION_WINDOW: tuple[float, float] = (-24.0, 0.0)

_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_LABEL_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_SEMVER = r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$"
_SEMVER_RE = re.compile(_SEMVER)
_REF_RE = re.compile(r"^([a-z][a-z0-9_]*)@((?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*))$")
_QUALIFIED_RE = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")
_COLUMN_RE = re.compile(r"^[a-z][a-z0-9_]*$")
#: What counts as an absolute date in a definition field: ISO / slashed dates and SQL
#: date-literal keywords (a bare 4-digit number is not a date — it may be a count).
_DATE_LIKE_RE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}\b|\b\d{4}/\d{1,2}/\d{1,2}\b|\b\d{1,2}/\d{1,2}/\d{4}\b"
    r"|\b(?:DATE|TIMESTAMP)\s*'",
    re.IGNORECASE,
)
_WS_RE = re.compile(r"\s+")


class CohortSpecError(ValueError):
    """A cohort spec YAML is malformed, a reference cannot be resolved, or a registry
    operation cannot proceed."""


class CohortSpecFrozenError(CohortSpecError):
    """An ``(id, version)`` pair recorded in the registry lock now hashes differently —
    the definition (or a referenced code set / phenotype) changed without a version bump."""


class UnknownCohortSpecError(CohortSpecError, LookupError):
    """No cohort spec with that ``id@version`` in the registry."""


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def parse_ref(ref: str) -> tuple[str, str]:
    """``"first_icu_adults@1.0.0"`` -> ``("first_icu_adults", "1.0.0")``;
    :class:`CohortSpecError` otherwise."""
    m = _REF_RE.match(ref.strip()) if isinstance(ref, str) else None
    if m is None:
        raise CohortSpecError(
            f"{ref!r} is not a reference; expected <id>@<major>.<minor>.<patch> "
            "(e.g. first_icu_adults@1.0.0)"
        )
    return m.group(1), m.group(2)


def format_ref(spec_id: str, version: str) -> str:
    return f"{spec_id}@{version}"


def _ref(value: Any, what: str) -> str:
    try:
        spec_id, version = parse_ref(str(value))
    except CohortSpecError as exc:
        raise ValueError(f"{what}: {exc}") from None
    return format_ref(spec_id, version)


def _qualified(value: Any, what: str) -> str:
    text = str(value).strip().lower()
    if not _QUALIFIED_RE.match(text):
        raise ValueError(f"{what} {value!r} must be <schema>.<table>")
    return text


def _column(value: Any, what: str) -> str:
    text = str(value).strip().lower()
    if not _COLUMN_RE.match(text):
        raise ValueError(f"{what} {value!r} is not a lower [a-z0-9_] name")
    return text


def _short_text(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{what} must be a non-empty string")
    text = value.strip()
    if len(text) > VALUE_MAX_CHARS:
        raise ValueError(f"{what} {text[:20]!r}... exceeds {VALUE_MAX_CHARS} characters")
    return text


def _positive_ints(value: Any, what: str) -> tuple[int, ...]:
    if value is None:
        return ()
    if not isinstance(value, list | tuple):
        raise ValueError(f"{what} must be a list of positive integers")
    out: set[int] = set()
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int) or item < 1:
            raise ValueError(f"{what}: {item!r} is not a positive integer")
        out.add(item)
    return tuple(sorted(out))


def _check_age_bound(value: float | None, what: str) -> None:
    if value is None:
        return
    if value < 0:
        raise ValueError(f"age.{what} must be >= 0")
    if AGE_CAP_FLOOR < value <= AGE_CAP:
        raise ValueError(
            f"age.{what} {value!r} sits inside the cap band: ages >= {AGE_CAP_FLOOR} are "
            f"shipped as {AGE_CAP}, so write {AGE_CAP_FLOOR} (the bound is documented as capped)"
        )


def normalize_sql(sql: str) -> str:
    """Whitespace-collapsed SQL — what :func:`sql_hash` hashes."""
    return _WS_RE.sub(" ", sql.strip())


def sql_hash(sql: str) -> str:
    """sha256 of the normalised SQL of a ``custom_sql`` criterion."""
    return hashlib.sha256(normalize_sql(sql).encode("utf-8")).hexdigest()


def find_dates(obj: Any, path: str = "") -> list[str]:
    """The paths of every absolute date inside a raw definition subtree: YAML date /
    datetime objects and date-like strings (module docstring). Documentation keys are
    skipped at every depth."""
    found: list[str] = []
    if isinstance(obj, Mapping):
        for key, value in obj.items():
            if str(key) in DOCUMENTATION_FIELDS:
                continue
            found.extend(find_dates(value, f"{path}.{key}" if path else str(key)))
    elif isinstance(obj, list | tuple):
        for i, value in enumerate(obj):
            found.extend(find_dates(value, f"{path}[{i}]"))
    elif isinstance(obj, date | datetime) or (isinstance(obj, str) and _DATE_LIKE_RE.search(obj)):
        found.append(path or "<root>")
    return found


# ---------------------------------------------------------------------------
# Windows, criteria payloads
# ---------------------------------------------------------------------------


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Window(_Frozen):
    """A half-open window ``[start_h, end_h)`` in hours relative to the index event
    (negative = before it; ``timesem.sql_hours_since`` in the compiled SQL)."""

    start_h: float = Field(description="window start in hours relative to the index (inclusive)")
    end_h: float = Field(description="window end in hours relative to the index (exclusive)")

    @model_validator(mode="after")
    def _order(self) -> Window:
        if self.end_h <= self.start_h:
            raise ValueError(f"window: end_h ({self.end_h}) must exceed start_h ({self.start_h})")
        return self

    def canonical(self) -> dict[str, float]:
        return {"start_h": float(self.start_h), "end_h": float(self.end_h)}


class AgeCriterion(_Frozen):
    """Age at the index event in ``[min, max)`` years (``timesem.sql_age_at``, capped at
    91: an ``age >= 89`` criterion is written ``min: 89`` and documented as capped)."""

    min: float | None = Field(default=None, description="lower bound (inclusive), years")
    max: float | None = Field(default=None, description="upper bound (exclusive), years")
    at: Literal["index"] = Field(default="index", description="the age is taken at the index")

    @model_validator(mode="after")
    def _bounds(self) -> AgeCriterion:
        if self.min is None and self.max is None:
            raise ValueError("age: give min and / or max")
        _check_age_bound(self.min, "min")
        _check_age_bound(self.max, "max")
        if self.min is not None and self.max is not None and self.max <= self.min:
            raise ValueError(f"age: max ({self.max}) must exceed min ({self.min})")
        return self

    def canonical(self) -> dict[str, Any]:
        out: dict[str, Any] = {"at": self.at}
        if self.min is not None:
            out["min"] = float(self.min)
        if self.max is not None:
            out["max"] = float(self.max)
        return out


def _values(value: Any, what: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list | tuple):
        raise ValueError(f"{what} must be a list of values")
    return tuple(sorted({_short_text(v, what) for v in value}))


class DemographicCriterion(_Frozen):
    """Set membership on admission / patient / first-ICU-unit fields (public vocabulary
    values, equality only; several fields = AND). ``discharge_location`` is known only at
    discharge — a post-index attribute a retrospective definition may still exclude on."""

    gender: tuple[str, ...] = Field(default=(), description="patients.gender values")
    admission_type: tuple[str, ...] = Field(default=(), description="admissions.admission_type")
    admission_location: tuple[str, ...] = Field(
        default=(), description="admissions.admission_location"
    )
    discharge_location: tuple[str, ...] = Field(
        default=(), description="admissions.discharge_location (post-index)"
    )
    insurance: tuple[str, ...] = Field(default=(), description="admissions.insurance")
    first_careunit: tuple[str, ...] = Field(
        default=(), description="icustays.first_careunit of the index / first ICU stay"
    )
    language: tuple[str, ...] = Field(default=(), description="admissions.language")
    marital_status: tuple[str, ...] = Field(default=(), description="admissions.marital_status")

    @model_validator(mode="before")
    @classmethod
    def _coerce(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            raise ValueError("demographic must be a mapping of field -> [values]")
        out = dict(data)
        for name in DEMOGRAPHIC_FIELDS:
            if name in out:
                out[name] = _values(out[name], f"demographic.{name}")
        return out

    @model_validator(mode="after")
    def _some(self) -> DemographicCriterion:
        if not self.fields():
            raise ValueError(f"demographic: give at least one of {', '.join(DEMOGRAPHIC_FIELDS)}")
        return self

    def fields(self) -> tuple[tuple[str, tuple[str, ...]], ...]:
        """The non-empty ``(field, values)`` pairs, canonical field order."""
        return tuple(
            (name, getattr(self, name)) for name in DEMOGRAPHIC_FIELDS if getattr(self, name)
        )

    def canonical(self) -> dict[str, Any]:
        return {name: list(values) for name, values in self.fields()}


class CodesetCriterion(_Frozen):
    """Billed ICD codes of a dual EP-40 set (``icd_dx`` -> ``diagnoses_icd``, ``icd_px``
    -> ``procedures_icd``): on the index admission (``same_admission``), on an admission
    that started within ``prior_days`` before the index (``prior_days``), or on any prior
    admission (``any_prior``); ``position: primary`` keeps ``seq_num = 1``."""

    ref: str = Field(description="the code set as id@version")
    position: Literal["any", "primary"] = Field(default="any", description="diagnosis position")
    lookback: Literal["same_admission", "prior_days", "any_prior"] = Field(
        default="same_admission", description="which admissions are searched"
    )
    prior_days: int | None = Field(
        default=None, ge=1, description="the lookback in days (lookback: prior_days only)"
    )

    @field_validator("ref", mode="before")
    @classmethod
    def _ref_field(cls, value: Any) -> str:
        return _ref(value, "codeset.ref")

    @model_validator(mode="after")
    def _lookback(self) -> CodesetCriterion:
        if (self.lookback == "prior_days") != (self.prior_days is not None):
            raise ValueError("codeset: prior_days is given exactly when lookback is prior_days")
        return self

    def canonical(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "ref": self.ref,
            "position": self.position,
            "lookback": self.lookback,
        }
        if self.prior_days is not None:
            out["prior_days"] = self.prior_days
        return out


class PhenotypeCriterion(_Frozen):
    """An EP-41 phenotype: its onset lies ``before`` the index, the phenotype flags the
    index unit (``at``: the unit itself, or the subject's onset at or before the index
    for a subject-grain phenotype), or the onset lies ``within_hours`` of the index
    (``|hours since index| <= hours``, the one closed interval)."""

    ref: str = Field(description="the phenotype as id@version")
    when: Literal["before", "at", "within_hours"] = Field(
        default="at", description="how the phenotype relates to the index"
    )
    hours: float | None = Field(
        default=None, gt=0, description="the half-width in hours (when: within_hours only)"
    )

    @field_validator("ref", mode="before")
    @classmethod
    def _ref_field(cls, value: Any) -> str:
        return _ref(value, "phenotype.ref")

    @model_validator(mode="after")
    def _hours(self) -> PhenotypeCriterion:
        if (self.when == "within_hours") != (self.hours is not None):
            raise ValueError("phenotype: hours is given exactly when when is within_hours")
        return self

    def canonical(self) -> dict[str, Any]:
        out: dict[str, Any] = {"ref": self.ref, "when": self.when}
        if self.hours is not None:
            out["hours"] = float(self.hours)
        return out


ConceptOp = Literal["=", "!=", "<", "<=", ">", ">=", "in", "not_in", "is_null", "is_not_null"]
CONCEPT_OPS: tuple[str, ...] = (
    "=",
    "!=",
    "<",
    "<=",
    ">",
    ">=",
    "in",
    "not_in",
    "is_null",
    "is_not_null",
)
Scalar = bool | int | float | str


class ConceptCriterion(_Frozen):
    """``column op value`` on a cataloged relation keyed by one of the grain's keys
    (typically a materialised ``mimiciv_derived.<concept>``, EP-37/38; an admissions /
    stays attribute works the same way); ``window`` keeps only rows whose ``time_column``
    lies in ``[start_h, end_h)`` hours from the index."""

    table: str = Field(description="<schema>.<table> of the relation")
    column: str = Field(description="the tested column")
    op: ConceptOp = Field(description="the comparison")
    value: Scalar | list[Scalar] | None = Field(
        default=None, description="a scalar, a list for in / not_in, absent for the null tests"
    )
    time_column: str | None = Field(default=None, description="the event-time column")
    window: Window | None = Field(default=None, description="relative window on time_column")

    @field_validator("table", mode="before")
    @classmethod
    def _table(cls, value: Any) -> str:
        return _qualified(value, "concept.table")

    @field_validator("column", "time_column", mode="before")
    @classmethod
    def _columns(cls, value: Any) -> Any:
        return None if value is None else _column(value, "concept column")

    @model_validator(mode="after")
    def _rules(self) -> ConceptCriterion:
        if self.op in ("is_null", "is_not_null"):
            if self.value is not None:
                raise ValueError(f"concept: op {self.op} takes no value")
        elif self.op in ("in", "not_in"):
            if not isinstance(self.value, list) or not self.value:
                raise ValueError(f"concept: op {self.op} needs a non-empty list value")
        elif self.value is None or isinstance(self.value, list):
            raise ValueError(f"concept: op {self.op} needs a scalar value")
        values = self.value if isinstance(self.value, list) else [self.value]
        for v in values:
            if isinstance(v, str):
                _short_text(v, "concept.value")
        if self.window is not None and self.time_column is None:
            raise ValueError("concept.window needs a time_column to restrict")
        return self

    def canonical(self) -> dict[str, Any]:
        out: dict[str, Any] = {"table": self.table, "column": self.column, "op": self.op}
        if self.value is not None:
            out["value"] = (
                sorted(self.value, key=lambda v: (type(v).__name__, str(v)))
                if isinstance(self.value, list)
                else self.value
            )
        if self.time_column is not None:
            out["time_column"] = self.time_column
        if self.window is not None:
            out["window"] = self.window.canonical()
        return out


class LosCriterion(_Frozen):
    """Length of stay in hours, ``[min_hours, max_hours)``: ``icu`` = the index ICU stay
    (``hours_since(intime, outtime)``), ``hosp`` = the index admission
    (``hours_since(admittime, dischtime)``)."""

    min_hours: float | None = Field(default=None, ge=0, description="lower bound (inclusive)")
    max_hours: float | None = Field(default=None, gt=0, description="upper bound (exclusive)")
    of: Literal["icu", "hosp"] = Field(default="icu", description="which stay")

    @model_validator(mode="after")
    def _bounds(self) -> LosCriterion:
        if self.min_hours is None and self.max_hours is None:
            raise ValueError("los: give min_hours and / or max_hours")
        if (
            self.min_hours is not None
            and self.max_hours is not None
            and self.max_hours <= self.min_hours
        ):
            raise ValueError("los: max_hours must exceed min_hours")
        return self

    def canonical(self) -> dict[str, Any]:
        out: dict[str, Any] = {"of": self.of}
        if self.min_hours is not None:
            out["min_hours"] = float(self.min_hours)
        if self.max_hours is not None:
            out["max_hours"] = float(self.max_hours)
        return out


class DataAvailabilityCriterion(_Frozen):
    """At least ``min_rows`` rows of ``table`` for the unit (of the given ``itemids`` when
    the table has an ``itemid`` column) inside ``window`` — the spec's observation window
    when absent."""

    table: str = Field(description="<schema>.<table> of a contract table with a time column")
    itemids: tuple[int, ...] = Field(default=(), description="restrict to these itemids")
    min_rows: int = Field(default=1, ge=1, description="minimum row count (inclusive)")
    window: Window | None = Field(
        default=None, description="relative window; default: the observation window"
    )

    @field_validator("table", mode="before")
    @classmethod
    def _table(cls, value: Any) -> str:
        return _qualified(value, "data_availability.table")

    @field_validator("itemids", mode="before")
    @classmethod
    def _ids(cls, value: Any) -> tuple[int, ...]:
        return _positive_ints(value, "data_availability.itemids")

    def canonical(self) -> dict[str, Any]:
        out: dict[str, Any] = {"table": self.table, "min_rows": self.min_rows}
        if self.itemids:
            out["itemids"] = list(self.itemids)
        if self.window is not None:
            out["window"] = self.window.canonical()
        return out


class PriorAdmissionsCriterion(_Frozen):
    """The number of hospital admissions that started before the index (within
    ``lookback_days`` when given) lies in the inclusive range ``[min, max]``."""

    min: int | None = Field(default=None, ge=0, description="minimum count (inclusive)")
    max: int | None = Field(default=None, ge=0, description="maximum count (inclusive)")
    lookback_days: int | None = Field(default=None, ge=1, description="lookback in days")

    @model_validator(mode="after")
    def _bounds(self) -> PriorAdmissionsCriterion:
        if self.min is None and self.max is None:
            raise ValueError("prior_admissions: give min and / or max")
        if self.min is not None and self.max is not None and self.max < self.min:
            raise ValueError("prior_admissions: max must be >= min")
        return self

    def canonical(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.min is not None:
            out["min"] = self.min
        if self.max is not None:
            out["max"] = self.max
        if self.lookback_days is not None:
            out["lookback_days"] = self.lookback_days
        return out


class CustomSqlCriterion(_Frozen):
    """A hand-written SELECT yielding the grain's key column(s) of the units the criterion
    keeps (the compiler wraps it as a CTE and semi-joins, EP-47); ``hash`` must equal the
    sha256 of the whitespace-normalised SQL so an edit is visible. Flagged ``custom`` in
    attrition and reports."""

    sql: str = Field(description="a SELECT yielding the grain keys")
    hash: str = Field(description="sha256 of the whitespace-normalised sql")

    @model_validator(mode="after")
    def _rules(self) -> CustomSqlCriterion:
        text = normalize_sql(self.sql)
        if not text:
            raise ValueError("custom_sql: sql must not be empty")
        if not text.upper().startswith(("SELECT", "WITH")):
            raise ValueError("custom_sql: sql must be a SELECT (or WITH ... SELECT)")
        if ";" in text:
            raise ValueError("custom_sql: one statement, no ';'")
        expected = sql_hash(self.sql)
        if self.hash != expected:
            raise ValueError(
                f"custom_sql: hash {self.hash[:12]} does not match the sql "
                f"(expected {expected[:12]}...; recompute after editing the SQL)"
            )
        return self

    def canonical(self) -> dict[str, Any]:
        return {"sql": normalize_sql(self.sql), "hash": self.hash}


CriterionPayload = (
    AgeCriterion
    | DemographicCriterion
    | CodesetCriterion
    | PhenotypeCriterion
    | ConceptCriterion
    | LosCriterion
    | DataAvailabilityCriterion
    | PriorAdmissionsCriterion
    | CustomSqlCriterion
)


class Criterion(_Frozen):
    """One inclusion / exclusion criterion: ``label`` + exactly one kind key (module
    docstring). ``description`` is documentation (not hashed)."""

    label: str = Field(description="a slug, unique in the spec: the attrition step name")
    description: str = Field(default="", description="documentation (not hashed)")
    age: AgeCriterion | None = None
    demographic: DemographicCriterion | None = None
    codeset: CodesetCriterion | None = None
    phenotype: PhenotypeCriterion | None = None
    concept: ConceptCriterion | None = None
    los: LosCriterion | None = None
    data_availability: DataAvailabilityCriterion | None = None
    prior_admissions: PriorAdmissionsCriterion | None = None
    custom_sql: CustomSqlCriterion | None = None

    @model_validator(mode="before")
    @classmethod
    def _one_kind(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            raise ValueError("a criterion is a mapping {label, <kind>: {...}}")
        present = [k for k in CRITERION_KINDS if data.get(k) is not None]
        if len(present) != 1:
            raise ValueError(
                f"criterion {data.get('label', '?')!r}: exactly one kind key expected "
                f"({', '.join(CRITERION_KINDS)}), got {present or 'none'}"
            )
        if not isinstance(data[present[0]], dict):
            raise ValueError(
                f"criterion {data.get('label', '?')!r}: {present[0]} must be a mapping"
            )
        return data

    @field_validator("label")
    @classmethod
    def _label(cls, value: str) -> str:
        if not _LABEL_RE.match(value):
            raise ValueError(
                f"label {value!r} is not a lower [a-z0-9_] slug of at most {LABEL_MAX_CHARS} chars"
            )
        return value

    @property
    def kind(self) -> str:
        for name in CRITERION_KINDS:
            if getattr(self, name) is not None:
                return name
        raise AssertionError("a criterion always carries one kind")  # pragma: no cover

    @property
    def payload(self) -> Any:
        return getattr(self, self.kind)

    @property
    def custom(self) -> bool:
        """Whether this is a ``custom_sql`` criterion (flagged in attrition and reports)."""
        return self.kind == "custom_sql"

    @property
    def codeset_refs(self) -> tuple[str, ...]:
        return (self.codeset.ref,) if self.codeset is not None else ()

    @property
    def phenotype_refs(self) -> tuple[str, ...]:
        return (self.phenotype.ref,) if self.phenotype is not None else ()

    def canonical(self) -> dict[str, Any]:
        return {"label": self.label, self.kind: self.payload.canonical()}

    def render(self) -> str:
        """A one-line human rendering of the criterion."""
        return f"{self.label}: {criterion_text(self)}"


def criterion_text(criterion: Criterion) -> str:
    """A one-line rendering of a criterion's definition (documentation and ``show``)."""
    p = criterion.payload
    kind = criterion.kind
    if kind == "age":
        parts = []
        if p.min is not None:
            parts.append(f"{p.min:g} <=")
        parts.append("age at index")
        if p.max is not None:
            parts.append(f"< {p.max:g}")
        return f"age({' '.join(parts)}" + (", capped at 91" if p.min == AGE_CAP_FLOOR else "") + ")"
    if kind == "demographic":
        return "demographic(" + "; ".join(f"{n} in {list(v)}" for n, v in p.fields()) + ")"
    if kind == "codeset":
        lookback = f"prior {p.prior_days} d" if p.lookback == "prior_days" else p.lookback
        return f"codeset({p.ref}, position {p.position}, {lookback})"
    if kind == "phenotype":
        when = f"within {p.hours:g} h" if p.when == "within_hours" else p.when
        return f"phenotype({p.ref}, {when} index)"
    if kind == "concept":
        value = "" if p.value is None else f" {p.value!r}"
        window = (
            f", {p.time_column} in [{p.window.start_h:g}, {p.window.end_h:g}) h"
            if p.window is not None
            else ""
        )
        return f"concept({p.table}.{p.column} {p.op}{value}{window})"
    if kind == "los":
        lo = f"{p.min_hours:g} <= " if p.min_hours is not None else ""
        hi = f" < {p.max_hours:g}" if p.max_hours is not None else ""
        return f"los({lo}{p.of} hours{hi})"
    if kind == "data_availability":
        items = f" itemids {list(p.itemids)}" if p.itemids else ""
        window = (
            f" in [{p.window.start_h:g}, {p.window.end_h:g}) h"
            if p.window is not None
            else " in the observation window"
        )
        return f"data_availability({p.table}{items}, >= {p.min_rows} rows{window})"
    if kind == "prior_admissions":
        lo = f"{p.min} <= " if p.min is not None else ""
        hi = f" <= {p.max}" if p.max is not None else ""
        lookback = f" within {p.lookback_days} d" if p.lookback_days is not None else ""
        return f"prior_admissions({lo}n{hi}{lookback})"
    return f"custom_sql(hash {p.hash[:12]}, flagged custom)"


# ---------------------------------------------------------------------------
# Index event, washout, follow-up, probe, references
# ---------------------------------------------------------------------------


class ConceptTime(_Frozen):
    """``concept_time``: the index is the ``first`` / ``last`` value of ``column`` in
    ``table`` per grain unit (a relation keyed by the grain's keys)."""

    table: str = Field(description="<schema>.<table>")
    column: str = Field(description="the timestamp column")
    pick: Literal["first", "last"] = Field(default="first", description="which value")

    @field_validator("table", mode="before")
    @classmethod
    def _table(cls, value: Any) -> str:
        return _qualified(value, "concept_time.table")

    @field_validator("column", mode="before")
    @classmethod
    def _col(cls, value: Any) -> str:
        return _column(value, "concept_time.column")

    def canonical(self) -> dict[str, Any]:
        return {"table": self.table, "column": self.column, "pick": self.pick}


class IndexEvent(_Frozen):
    """Exactly one of: a named ``rule`` of the grain's registry entry
    (``timesem.INDEX_RULES``), the onset of a ``phenotype_onset`` (``id@version``, a
    phenotype of the spec's grain) or a ``concept_time``."""

    rule: str | None = Field(
        default=None,
        description="an index-event rule of timesem's registry",
        json_schema_extra={"enum": list(timesem.INDEX_RULES)},
    )
    phenotype_onset: str | None = Field(default=None, description="a phenotype as id@version")
    concept_time: ConceptTime | None = None

    @model_validator(mode="before")
    @classmethod
    def _one(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            raise ValueError(
                "index_event is a mapping: {rule: ...}, {phenotype_onset: ...} or "
                "{concept_time: {...}}"
            )
        present = [k for k in INDEX_EVENT_KINDS if data.get(k) is not None]
        if len(present) != 1:
            raise ValueError(
                f"index_event: exactly one of {', '.join(INDEX_EVENT_KINDS)} expected, got "
                f"{present or 'none'}"
            )
        return data

    @field_validator("rule")
    @classmethod
    def _rule(cls, value: str | None) -> str | None:
        if value is not None and value not in timesem.INDEX_RULES:
            raise ValueError(
                f"index_event.rule {value!r} is not an index-event rule; expected one of "
                f"{', '.join(timesem.INDEX_RULES)}"
            )
        return value

    @field_validator("phenotype_onset", mode="before")
    @classmethod
    def _onset(cls, value: Any) -> Any:
        return None if value is None else _ref(value, "index_event.phenotype_onset")

    @property
    def kind(self) -> str:
        for name in INDEX_EVENT_KINDS:
            if getattr(self, name) is not None:
                return name
        raise AssertionError("an index event always carries one form")  # pragma: no cover

    def canonical(self) -> dict[str, Any]:
        if self.rule is not None:
            return {"rule": self.rule}
        if self.phenotype_onset is not None:
            return {"phenotype_onset": self.phenotype_onset}
        assert self.concept_time is not None
        return {"concept_time": self.concept_time.canonical()}

    def render(self) -> str:
        if self.rule is not None:
            return self.rule
        if self.phenotype_onset is not None:
            return f"phenotype_onset({self.phenotype_onset})"
        assert self.concept_time is not None
        ct = self.concept_time
        return f"concept_time({ct.table}.{ct.column}, {ct.pick})"


class Washout(_Frozen):
    """``none``, ``no_prior_hadm`` (no hospital admission that started within ``days``
    before the index — any prior admission when ``days`` is absent — optionally only
    admissions carrying ``codeset``) or ``no_prior_icu`` (no ICU stay in the same window)."""

    rule: Literal["none", "no_prior_hadm", "no_prior_icu"] = Field(
        default="none", description="the washout rule"
    )
    days: int | None = Field(default=None, ge=1, description="the lookback in days (absent = any)")
    codeset: str | None = Field(
        default=None, description="only prior admissions carrying this set (no_prior_hadm)"
    )

    @field_validator("codeset", mode="before")
    @classmethod
    def _cs(cls, value: Any) -> Any:
        return None if value is None else _ref(value, "washout.codeset")

    @model_validator(mode="after")
    def _rules(self) -> Washout:
        if self.rule == "none" and (self.days is not None or self.codeset is not None):
            raise ValueError("washout: rule none takes neither days nor codeset")
        if self.codeset is not None and self.rule != "no_prior_hadm":
            raise ValueError("washout: codeset applies to no_prior_hadm only")
        return self

    def canonical(self) -> dict[str, Any]:
        out: dict[str, Any] = {"rule": self.rule}
        if self.days is not None:
            out["days"] = self.days
        if self.codeset is not None:
            out["codeset"] = self.codeset
        return out

    def render(self) -> str:
        if self.rule == "none":
            return "none"
        window = f" within {self.days} d" if self.days is not None else " ever"
        what = f" carrying {self.codeset}" if self.codeset else ""
        return f"{self.rule}{window}{what}"


class FollowUp(_Frozen):
    """The outcome (a ``timesem.CENSORING_RULES`` name), its horizon in days after the
    index (an in-hospital outcome has none; a censored outcome defaults to the rule's and
    is censored at ``min(index + horizon, last discharge + 365 d)`` — the ``dod``
    visibility rule) and the competing events (``discharge_alive`` for in-hospital
    outcomes, optional)."""

    outcome: str = Field(
        description="a timesem censoring-rule name", json_schema_extra={"enum": list(OUTCOMES)}
    )
    horizon_days: int | None = Field(
        default=None, ge=0, description="days after the index (censored outcomes only)"
    )
    competing_events: tuple[str, ...] = Field(default=(), description="competing events")

    @model_validator(mode="before")
    @classmethod
    def _defaults(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            raise ValueError("follow_up is a mapping {outcome, horizon_days, competing_events}")
        out = dict(data)
        rule = timesem.CENSORING_RULES.get(str(out.get("outcome")))
        if rule is not None and rule.censored and out.get("horizon_days") is None:
            out["horizon_days"] = rule.horizon_days
        events = out.get("competing_events")
        if events is None:
            out["competing_events"] = ()
        elif isinstance(events, list | tuple):
            out["competing_events"] = tuple(sorted({str(e) for e in events}))
        else:
            raise ValueError("follow_up.competing_events must be a list")
        return out

    @field_validator("outcome")
    @classmethod
    def _outcome(cls, value: str) -> str:
        if value not in timesem.CENSORING_RULES:
            raise ValueError(
                f"follow_up.outcome {value!r} is not a censoring rule; expected one of "
                f"{', '.join(OUTCOMES)}"
            )
        return value

    @model_validator(mode="after")
    def _rules(self) -> FollowUp:
        rule = timesem.CENSORING_RULES[self.outcome]
        if not rule.censored and self.horizon_days is not None:
            raise ValueError(
                f"follow_up: {self.outcome} is an in-hospital outcome resolved at discharge; "
                "it takes no horizon_days"
            )
        unknown = sorted(set(self.competing_events) - set(COMPETING_EVENTS))
        if unknown:
            raise ValueError(
                f"follow_up.competing_events {unknown} unknown; expected a subset of "
                f"{list(COMPETING_EVENTS)}"
            )
        if self.competing_events and rule.censored:
            raise ValueError(
                f"follow_up: {self.outcome} is followed past discharge; discharge_alive competes "
                "with in-hospital outcomes only"
            )
        return self

    @property
    def rule(self) -> timesem.CensoringRule:
        """The resolved :class:`timesem.CensoringRule` (horizon and competing events applied)."""
        base = timesem.CENSORING_RULES[self.outcome]
        return dataclasses.replace(
            base, horizon_days=self.horizon_days, competing_events=self.competing_events
        )

    def canonical(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "horizon_days": self.horizon_days,
            "competing_events": list(self.competing_events),
        }

    def render(self) -> str:
        horizon = f" within {self.horizon_days} d" if self.horizon_days is not None else ""
        competing = (
            f", competing {', '.join(self.competing_events)}" if self.competing_events else ""
        )
        return f"{self.outcome}{horizon}{competing}"


class DegeneracyProbe(_Frozen):
    """The categorical columns ``mwh cohort validate --tier`` probes for zero-event /
    all-event levels against the follow-up outcome (EP-31 policy); an empty list disables
    the probe."""

    columns: tuple[str, ...] = Field(
        default=DEFAULT_PROBE_COLUMNS,
        description="categorical columns to probe",
        json_schema_extra={"items": {"type": "string", "enum": list(PROBE_COLUMNS)}},
    )

    @field_validator("columns", mode="before")
    @classmethod
    def _cols(cls, value: Any) -> tuple[str, ...]:
        if value is None:
            return ()
        if not isinstance(value, list | tuple):
            raise ValueError("degeneracy_probe.columns must be a list of column names")
        out: list[str] = []
        for item in value:
            name = str(item).strip().lower()
            if name not in PROBE_COLUMNS:
                raise ValueError(
                    f"degeneracy_probe.columns: {item!r} is not probeable; expected a subset of "
                    f"{', '.join(PROBE_COLUMNS)}"
                )
            if name not in out:
                out.append(name)
        return tuple(out)

    def canonical(self) -> dict[str, Any]:
        return {"columns": list(self.columns)}


class References(_Frozen):
    """The declared references (optional in YAML; when given they must equal the ones the
    criteria, washout and index event name)."""

    codesets: tuple[str, ...] = Field(default=(), description="code sets as id@version")
    phenotypes: tuple[str, ...] = Field(default=(), description="phenotypes as id@version")

    @field_validator("codesets", "phenotypes", mode="before")
    @classmethod
    def _refs(cls, value: Any, info: Any) -> tuple[str, ...]:
        if value is None:
            return ()
        if not isinstance(value, list | tuple):
            raise ValueError(f"references.{info.field_name} must be a list of id@version")
        return tuple(sorted({_ref(v, f"references.{info.field_name}") for v in value}))


# ---------------------------------------------------------------------------
# The spec
# ---------------------------------------------------------------------------


class CohortSpec(_Frozen):
    """One versioned cohort specification (module docstring)."""

    id: str = Field(description="a slug; with version the reference id@version")
    version: str = Field(description="semver", json_schema_extra={"pattern": _SEMVER})
    title: str = Field(min_length=1, description="documentation (not hashed)")
    description: str = Field(default="", description="documentation (not hashed)")
    grain: str = Field(
        description="the unit of analysis (timesem's registry, available grains only)",
        json_schema_extra={"enum": list(AVAILABLE_GRAINS)},
    )
    index_event: IndexEvent
    inclusion: tuple[Criterion, ...] = Field(default=(), description="ordered; kept when true")
    exclusion: tuple[Criterion, ...] = Field(default=(), description="ordered; removed when true")
    observation_window: Window = Field(
        default=Window(start_h=DEFAULT_OBSERVATION_WINDOW[0], end_h=DEFAULT_OBSERVATION_WINDOW[1]),
        description="the covariate window relative to the index, default [-24, 0) h",
    )
    washout: Washout = Field(default=Washout(), description="the washout rule")
    follow_up: FollowUp
    era_filter: tuple[str, ...] = Field(
        default=(),
        description="anchor_year_group labels kept (empty = every era)",
        json_schema_extra={"items": {"type": "string", "enum": list(timesem.ERAS)}},
    )
    degeneracy_probe: DegeneracyProbe = Field(
        default=DegeneracyProbe(), description="the level-degeneracy probe columns"
    )
    references: References | None = Field(default=None, description="optional; must match")
    notes: str = Field(default="", description="documentation (not hashed)")
    what_it_does_not_claim: tuple[str, ...] = Field(default=(), description="documentation")

    @model_validator(mode="before")
    @classmethod
    def _no_dates(cls, data: Any) -> Any:
        if isinstance(data, dict):
            found = find_dates(data)
            if found:
                raise ValueError(
                    "absolute dates are forbidden in a cohort definition (per-patient date "
                    f"shift; use relative windows and era_filter): {', '.join(found[:5])}"
                )
        return data

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

    @field_validator("inclusion", "exclusion", "what_it_does_not_claim", mode="before")
    @classmethod
    def _lists(cls, value: Any) -> Any:
        return () if value is None else value

    @field_validator("grain")
    @classmethod
    def _grain(cls, value: str) -> str:
        try:
            grain = timesem.grain(value)
        except timesem.GrainError as exc:
            raise ValueError(str(exc)) from None
        if not grain.available:
            raise ValueError(
                f"grain {value!r} is not available (a placeholder until {grain.available_from}); "
                f"available grains: {', '.join(AVAILABLE_GRAINS)}"
            )
        return value

    @field_validator("era_filter", mode="before")
    @classmethod
    def _eras(cls, value: Any) -> tuple[str, ...]:
        if value is None:
            return ()
        if not isinstance(value, list | tuple):
            raise ValueError("era_filter must be a list of anchor_year_group labels")
        eras: dict[int, str] = {}
        for item in value:
            try:
                era = timesem.era_of(str(item))
            except timesem.UnknownEraError:
                raise ValueError(
                    f"era_filter: {item!r} is not an anchor_year_group; expected a subset of "
                    f"{', '.join(repr(e) for e in timesem.ERAS)}"
                ) from None
            eras[era.index] = era.label
        return tuple(eras[i] for i in sorted(eras))

    @model_validator(mode="after")
    def _rules(self) -> CohortSpec:
        grain = timesem.grain(self.grain)
        if self.index_event.rule is not None:
            if not grain.index_rules:
                raise ValueError(
                    f"{self.ref}: grain {self.grain} has no index-event template; use "
                    "phenotype_onset or concept_time"
                )
            if self.index_event.rule not in grain.index_rules:
                raise ValueError(
                    f"{self.ref}: index-event rule {self.index_event.rule!r} does not apply to "
                    f"grain {self.grain}; expected one of {', '.join(grain.index_rules)}"
                )
        labels = [c.label for c in (*self.inclusion, *self.exclusion)]
        dupes = sorted({x for x in labels if labels.count(x) > 1})
        if dupes:
            raise ValueError(f"{self.ref}: duplicate criterion label(s) {dupes}")
        if self.references is not None:
            if set(self.references.codesets) != set(self.codeset_refs):
                raise ValueError(
                    f"{self.ref}: references.codesets {list(self.references.codesets)} differ "
                    f"from the code sets the definition names {list(self.codeset_refs)}"
                )
            if set(self.references.phenotypes) != set(self.phenotype_refs):
                raise ValueError(
                    f"{self.ref}: references.phenotypes {list(self.references.phenotypes)} "
                    f"differ from the phenotypes the definition names "
                    f"{list(self.phenotype_refs)}"
                )
        return self

    # -- derived views ---------------------------------------------------------------------

    @property
    def ref(self) -> str:
        return format_ref(self.id, self.version)

    @property
    def grain_entry(self) -> timesem.Grain:
        return timesem.grain(self.grain)

    def criteria(self) -> Iterator[tuple[str, Criterion]]:
        """Every criterion in spec order with its polarity (``inclusion`` / ``exclusion``)."""
        for c in self.inclusion:
            yield "inclusion", c
        for c in self.exclusion:
            yield "exclusion", c

    @property
    def codeset_refs(self) -> tuple[str, ...]:
        """Every code-set reference the definition names, sorted."""
        refs: set[str] = set()
        for _polarity, c in self.criteria():
            refs.update(c.codeset_refs)
        if self.washout.codeset is not None:
            refs.add(self.washout.codeset)
        return tuple(sorted(refs))

    @property
    def phenotype_refs(self) -> tuple[str, ...]:
        """Every phenotype reference the definition names, sorted."""
        refs: set[str] = set()
        for _polarity, c in self.criteria():
            refs.update(c.phenotype_refs)
        if self.index_event.phenotype_onset is not None:
            refs.add(self.index_event.phenotype_onset)
        return tuple(sorted(refs))

    @property
    def custom(self) -> bool:
        """Whether any criterion is ``custom_sql``."""
        return any(c.custom for _p, c in self.criteria())

    def canonical(self, reference_hashes: Mapping[str, Mapping[str, str]]) -> dict[str, Any]:
        """The hashed shape (module docstring). ``reference_hashes`` is
        ``{"codeset": {id@version: def_hash}, "phenotype": {...}}`` and must cover every
        reference the definition names."""
        codesets = reference_hashes.get("codeset", {})
        phenotypes = reference_hashes.get("phenotype", {})
        missing = sorted(
            [f"codeset {r}" for r in self.codeset_refs if r not in codesets]
            + [f"phenotype {r}" for r in self.phenotype_refs if r not in phenotypes]
        )
        if missing:
            raise CohortSpecError(f"{self.ref}: unresolved reference(s) {missing}")
        return {
            "grain": self.grain,
            "index_event": self.index_event.canonical(),
            "inclusion": [c.canonical() for c in self.inclusion],
            "exclusion": [c.canonical() for c in self.exclusion],
            "observation_window": self.observation_window.canonical(),
            "washout": self.washout.canonical(),
            "follow_up": self.follow_up.canonical(),
            "era_filter": list(self.era_filter),
            "degeneracy_probe": self.degeneracy_probe.canonical(),
            "references": {
                "codeset": {r: codesets[r] for r in self.codeset_refs},
                "phenotype": {r: phenotypes[r] for r in self.phenotype_refs},
            },
        }

    def def_hash(self, reference_hashes: Mapping[str, Mapping[str, str]]) -> str:
        """sha256 of the canonical JSON (module docstring)."""
        payload = canonical_json(self.canonical(reference_hashes))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    # -- YAML --------------------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """The YAML document in fixed key order: identity, documentation, definition."""
        doc: dict[str, Any] = {"id": self.id, "version": self.version, "title": self.title}
        if self.description:
            doc["description"] = self.description
        doc["grain"] = self.grain
        doc["index_event"] = self.index_event.canonical()
        doc["inclusion"] = [_criterion_dict(c) for c in self.inclusion]
        doc["exclusion"] = [_criterion_dict(c) for c in self.exclusion]
        doc["observation_window"] = self.observation_window.canonical()
        doc["washout"] = self.washout.canonical()
        doc["follow_up"] = self.follow_up.canonical()
        doc["era_filter"] = list(self.era_filter)
        doc["degeneracy_probe"] = self.degeneracy_probe.canonical()
        if self.references is not None:
            doc["references"] = {
                "codesets": list(self.references.codesets),
                "phenotypes": list(self.references.phenotypes),
            }
        if self.notes:
            doc["notes"] = self.notes
        if self.what_it_does_not_claim:
            doc["what_it_does_not_claim"] = list(self.what_it_does_not_claim)
        return doc

    def to_yaml(self) -> str:
        """The spec as YAML text (fixed key order, ``version`` quoted, multi-line strings
        as literal blocks; ``from_yaml(spec.to_yaml()) == spec`` and the text is stable)."""
        return yaml.dump(
            self.to_dict(),
            Dumper=_Dumper,
            sort_keys=False,
            allow_unicode=False,
            width=100,
            default_flow_style=False,
        )

    @classmethod
    def from_yaml(cls, text: str, *, where: str = "<text>") -> CohortSpec:
        """Parse one YAML document (:class:`CohortSpecError` names every problem)."""
        return spec_from_text(text, where=where)

    def save_yaml(self, path: Path | str) -> Path:
        """Write :meth:`to_yaml` to ``path`` through :func:`fsio.atomic_write_text`."""
        from mimicwarehouse import fsio

        target = Path(path)
        fsio.atomic_write_text(target, self.to_yaml())
        return target


def _criterion_dict(criterion: Criterion) -> dict[str, Any]:
    out: dict[str, Any] = {"label": criterion.label}
    if criterion.description:
        out["description"] = criterion.description
    if criterion.custom_sql is not None:
        # the author's SQL layout is kept (the hash is over the normalised text anyway)
        out["custom_sql"] = {"sql": criterion.custom_sql.sql, "hash": criterion.custom_sql.hash}
    else:
        out[criterion.kind] = criterion.payload.canonical()
    return out


class _Dumper(yaml.SafeDumper):
    """``version`` quoted, multi-line strings as literal blocks."""


def _represent_str(dumper: yaml.SafeDumper, data: str) -> yaml.ScalarNode:
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    if _SEMVER_RE.match(data):
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style='"')
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


_Dumper.add_representer(str, _represent_str)


# ---------------------------------------------------------------------------
# Loading, the JSON schema
# ---------------------------------------------------------------------------


def _format_validation_error(where: str, exc: ValidationError) -> str:
    lines = [f"{where}: {exc.error_count()} validation error(s)"]
    for e in exc.errors():
        loc = ".".join(str(p) for p in e["loc"])
        lines.append(f"  {loc}: {e['msg']}")
    return "\n".join(lines)


def spec_from_text(text: str, *, where: str = "<text>") -> CohortSpec:
    """Parse one YAML document into a :class:`CohortSpec` (:class:`CohortSpecError` names
    every validation problem)."""
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise CohortSpecError(f"{where}: cannot parse ({exc})") from exc
    if not isinstance(doc, dict):
        raise CohortSpecError(f"{where}: top level must be a mapping")
    try:
        return CohortSpec.model_validate(doc)
    except ValidationError as exc:
        raise CohortSpecError(_format_validation_error(where, exc)) from None


def load_spec(path: Path | str) -> CohortSpec:
    """Parse and validate one cohort-spec YAML file."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CohortSpecError(f"{path.name}: cannot read ({exc})") from exc
    return spec_from_text(text, where=path.name)


def json_schema() -> dict[str, Any]:
    """The JSON schema of :class:`CohortSpec` (what ``mwh cohort schema`` prints and the
    EP-62 form renders): pydantic's schema plus the exactly-one-kind-key constraints on
    ``Criterion`` / ``IndexEvent`` that the model validators enforce."""
    schema = CohortSpec.model_json_schema()
    defs = schema.get("$defs", {})
    if "Criterion" in defs:
        defs["Criterion"]["oneOf"] = [{"required": [kind]} for kind in CRITERION_KINDS]
    if "IndexEvent" in defs:
        defs["IndexEvent"]["oneOf"] = [{"required": [kind]} for kind in INDEX_EVENT_KINDS]
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = "CohortSpec"
    schema["description"] = (
        "A mimicwarehouse cohort specification (EP-46): grain, index event, ordered "
        "inclusion / exclusion criteria, observation window, washout, follow-up, era filter "
        "and references by id@version. No absolute dates; windows are [start, end) hours "
        "relative to the index."
    )
    return schema


__all__ = [
    "AGE_CAP",
    "AGE_CAP_FLOOR",
    "AVAILABLE_GRAINS",
    "COMPETING_EVENTS",
    "CONCEPT_OPS",
    "CRITERION_KINDS",
    "DEFAULT_OBSERVATION_WINDOW",
    "DEFAULT_PROBE_COLUMNS",
    "DEMOGRAPHIC_FIELDS",
    "DOCUMENTATION_FIELDS",
    "INDEX_EVENT_KINDS",
    "LABEL_MAX_CHARS",
    "OUTCOMES",
    "PROBE_COLUMNS",
    "VALUE_MAX_CHARS",
    "AgeCriterion",
    "CodesetCriterion",
    "CohortSpec",
    "CohortSpecError",
    "CohortSpecFrozenError",
    "ConceptCriterion",
    "ConceptOp",
    "ConceptTime",
    "Criterion",
    "CriterionPayload",
    "CustomSqlCriterion",
    "DataAvailabilityCriterion",
    "DegeneracyProbe",
    "DemographicCriterion",
    "FollowUp",
    "IndexEvent",
    "LosCriterion",
    "PhenotypeCriterion",
    "PriorAdmissionsCriterion",
    "References",
    "Scalar",
    "UnknownCohortSpecError",
    "Washout",
    "Window",
    "criterion_text",
    "find_dates",
    "format_ref",
    "json_schema",
    "load_spec",
    "normalize_sql",
    "parse_ref",
    "spec_from_text",
    "sql_hash",
]
