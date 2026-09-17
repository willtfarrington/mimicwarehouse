"""The protocol schema — the pydantic :class:`Protocol`, its content hash, YAML loading and
the JSON schema (EP-51 item 1; DESIGN §13; GOVERNANCE §7/§12; D-25).

A protocol is one YAML document that pre-specifies an analysis over a registered cohort
(EP-46, by ``id@version``): the **claim type** the report may make (``exploratory`` /
``confirmatory`` / ``predictive`` / ``associational`` / ``causal``), the **unit of
analysis** (a ``timesem`` grain, which must be the cohort's), an optional **exposure**
(a code set / phenotype / concept / column definition with a timing rule relative to the
index — ``null`` for descriptive and predictive protocols), one or more **outcomes**
(each with a definition, an optional window or horizon, its ``timesem`` **censoring
rule** and competing events), the **covariates** (a ``cohort.<column>`` or
``<schema>.<table>.<column>`` source, an optional window, a transform), the **feature
windows** (observation ``[start_h, end_h)``, a gap and a prediction horizon), the
**analysis plan** (method family, estimand, model spec, hyperparameter policy, subgroups,
sensitivity analyses, multiplicity rule, missing-data policy, sample-size note), the
optional **temporal holdout** (``development_eras`` / ``holdout_eras`` / ``sealed_eras``
— disjoint subsets of ``timesem.ERAS``, the three-list shape EP-129's runner consumes),
the fixed **seeds policy** (:data:`SEEDS_POLICY`, EP-36's ``derive_seed`` rule), the fixed
**retrospective statement** (:data:`RETROSPECTIVE_STATEMENT`, GOVERNANCE §7), the
declared **references** and the **amendment link** (``amends`` = the previous frozen
hash, D-25).

**The content hash** (:meth:`Protocol.content_hash`) is the sha256 of the canonical JSON
(key-sorted, whitespace-free — :func:`codesets.spec.canonical_json`) of the *definition*:
every field except the documentation fields (:data:`DOCUMENTATION_FIELDS` — ``title``,
``description``, ``notes``, ``amendment_reason``), with defaults made explicit and the
resolved reference hashes inlined — the cohort's ``def_hash``, every code set's /
phenotype's ``def_hash`` and every concept's executed-SQL sha256 — so a frozen protocol
pins the definitions it was written against and moves when any of them moves (the D-25
addendum of EP-46, one layer up). Key order, whitespace, comments and omitted defaults
never move the hash; any definition change does.

**Validators refuse** (MIMIC caveats baked in, GOVERNANCE §6, DESIGN §7): absolute dates
anywhere in the definition (per-patient date shift — eras and relative windows only), an
era label outside ``timesem.ERAS`` or a holdout whose lists overlap, an unavailable grain
(``edstay`` / ``note`` placeholders), an unknown censoring rule or competing event, a
horizon on an in-hospital outcome, an identifier column (``subject_id``, ``hadm_id``,
``stay_id``, ``note_id``, …) as an outcome / covariate / exposure source (GOVERNANCE §4),
a seeds policy or retrospective statement other than the fixed text, an ``amends`` that
is not a 64-hex hash, an ``amendment_reason`` without ``amends``, and the claim / plan
consistency rules (:data:`CLAIM_RULES`): an ``associational`` or ``causal`` claim needs an
exposure, a ``causal`` claim needs the ``causal`` method family, a ``predictive`` claim
needs ``prediction`` / ``bayes``, and the ``causal`` family needs an exposure. Unresolved
references (an unknown cohort, code set, phenotype or vendored concept) are refused at
freeze time by :mod:`~mimicwarehouse.protocol.registry`, which is where the registries
live.

Import budget: pydantic + yaml + stdlib plus ``timesem`` and the EP-46 spec module — this
module sits on the ``mwh --help`` path through ``protocol.cli``; the safe module (for the
contract's identifier column names) loads inside the validator that needs it.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator, Mapping
from datetime import date, datetime
from functools import cache
from pathlib import Path
from typing import Any, Literal, get_args

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from mimicwarehouse import timesem
from mimicwarehouse.codesets.spec import canonical_json
from mimicwarehouse.cohort.spec import Window, format_ref, parse_ref

# ---------------------------------------------------------------------------
# Vocabularies and fixed texts
# ---------------------------------------------------------------------------

ClaimType = Literal["exploratory", "confirmatory", "predictive", "associational", "causal"]
#: The five claim types a report may label itself with (GOVERNANCE §7).
CLAIM_TYPES: tuple[str, ...] = get_args(ClaimType)
#: Claim types whose runs must cite a frozen protocol hash (D-25; ``run.start`` refuses
#: one without a ``protocol_hash``).
HASH_REQUIRED_CLAIM_TYPES: tuple[str, ...] = ("confirmatory", "causal")

MethodFamily = Literal["descriptive", "glm", "survival", "causal", "prediction", "bayes"]
METHOD_FAMILIES: tuple[str, ...] = get_args(MethodFamily)

#: The claim / plan consistency rules (module docstring), as ``(rule, text)`` for the docs.
CLAIM_RULES: tuple[tuple[str, str], ...] = (
    ("exposure_for_claim", "an associational or causal claim needs an exposure"),
    ("causal_family", "a causal claim needs the causal method family"),
    ("prediction_family", "a predictive claim needs the prediction or bayes method family"),
    ("exposure_for_family", "the causal method family needs an exposure"),
)

#: The fixed seeds policy (EP-36): validator-enforced verbatim.
SEEDS_POLICY = (
    "derive_seed: every stochastic stage draws from run.derive_seed(protocol_id, stage, salt) "
    "(docs/methods/determinism.md); the seed scope is this protocol's id"
)
#: The fixed retrospective statement every protocol carries and every protocol-driven
#: report repeats (GOVERNANCE §7): validator-enforced verbatim.
RETROSPECTIVE_STATEMENT = (
    "All MIMIC-IV analyses in this repository are retrospective: MIMIC-IV is a de-identified, "
    "date-shifted record of care already delivered, so a frozen protocol pre-specifies the "
    "analysis, never the data collection."
)

#: Documentation fields: never hashed, never scanned for dates.
DOCUMENTATION_FIELDS: frozenset[str] = frozenset(
    {"title", "description", "notes", "amendment_reason"}
)
#: The definition kinds an exposure / outcome definition may use (exactly one).
DEFINITION_KINDS: tuple[str, ...] = ("codeset", "phenotype", "concept", "column")
#: Covariate transforms.
Transform = Literal["identity", "log", "standardize", "categorical", "age_band", "indicator"]
TRANSFORMS: tuple[str, ...] = get_args(Transform)
#: The censoring rules an outcome may name and the competing events.
CENSORING_RULES: tuple[str, ...] = tuple(timesem.CENSORING_RULES)
COMPETING_EVENTS: tuple[str, ...] = (timesem.DISCHARGE_ALIVE,)
#: The grains a protocol may name (available ones) — the JSON-schema enum.
AVAILABLE_GRAINS: tuple[str, ...] = tuple(g.name for g in timesem.available_grains())
#: The pseudo-schema of the compiled cohort's own columns (EP-47 output columns).
COHORT_SOURCE = "cohort"
#: The schema of the vendored concepts (EP-37/38).
CONCEPT_SCHEMA = "mimiciv_derived"
#: Longest plan / definition text (folded YAML paragraphs; never data).
TEXT_MAX_CHARS = 500
#: Longest name / label (an outcome or covariate name; a report column).
NAME_MAX_CHARS = 40

_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_SEMVER = r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$"
_SEMVER_RE = re.compile(_SEMVER)
_QUALIFIED_RE = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")
_COLUMN_RE = re.compile(r"^[a-z][a-z0-9_]*$")
#: A frozen protocol hash (sha256 hex).
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
#: The absolute-date shapes refused in a definition field (``cohort.spec``'s twin: ISO /
#: slashed dates and SQL date-literal keywords; a bare 4-digit number is not a date).
DATE_LIKE_RE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}\b|\b\d{4}/\d{1,2}/\d{1,2}\b|\b\d{1,2}/\d{1,2}/\d{4}\b"
    r"|\b(?:DATE|TIMESTAMP)\s*'",
    re.IGNORECASE,
)


class ProtocolError(ValueError):
    """A protocol YAML is malformed, a reference cannot be resolved, or a registry
    operation cannot proceed."""


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _ref(value: Any, what: str) -> str:
    try:
        spec_id, version = parse_ref(str(value))
    except ValueError as exc:
        raise ValueError(f"{what}: {exc}") from None
    return format_ref(spec_id, version)


def _text(value: Any, what: str, *, required: bool = True) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ValueError(f"{what} must be a string")
    text = value.strip()
    if required and not text:
        raise ValueError(f"{what} must be a non-empty string")
    if len(text) > TEXT_MAX_CHARS:
        raise ValueError(f"{what} exceeds {TEXT_MAX_CHARS} characters")
    return text


def _name(value: Any, what: str) -> str:
    text = str(value).strip().lower() if value is not None else ""
    if not _NAME_RE.match(text):
        raise ValueError(f"{what} {value!r} is not a lower [a-z0-9_] name of at most 40 chars")
    return text


def _texts(value: Any, what: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list | tuple):
        raise ValueError(f"{what} must be a list of strings")
    return tuple(_text(v, f"{what}[{i}]") for i, v in enumerate(value))


@cache
def identifier_columns() -> frozenset[str]:
    """The contract's identifier column names (``safe.identifier_column_names``),
    loaded once — what a definition, covariate or exposure source may never name."""
    from mimicwarehouse.safe import identifier_column_names

    return identifier_column_names()


def _source(value: Any, what: str) -> str:
    """``cohort.<column>`` or ``<schema>.<table>.<column>``, lower-cased; identifier
    columns refused."""
    text = str(value).strip().lower() if value is not None else ""
    parts = text.split(".")
    if len(parts) == 2 and parts[0] == COHORT_SOURCE and _COLUMN_RE.match(parts[1]):
        column = parts[1]
    elif (
        len(parts) == 3 and _QUALIFIED_RE.match(".".join(parts[:2])) and _COLUMN_RE.match(parts[2])
    ):
        column = parts[2]
    else:
        raise ValueError(f"{what} {value!r} must be cohort.<column> or <schema>.<table>.<column>")
    if column in identifier_columns():
        raise ValueError(
            f"{what}: {column!r} is an identifier column and may not be analysed (GOVERNANCE §4)"
        )
    return text


def _concept(value: Any, what: str) -> str:
    """``mimiciv_derived.<name>`` (a bare ``<name>`` is qualified)."""
    text = str(value).strip().lower() if value is not None else ""
    if _COLUMN_RE.match(text):
        text = f"{CONCEPT_SCHEMA}.{text}"
    if not _QUALIFIED_RE.match(text) or not text.startswith(f"{CONCEPT_SCHEMA}."):
        raise ValueError(f"{what} {value!r} must be a vendored concept: {CONCEPT_SCHEMA}.<name>")
    return text


def find_dates(obj: Any, path: str = "") -> list[str]:
    """The paths of every absolute date inside a raw definition subtree: YAML date /
    datetime objects and date-like strings (:data:`DATE_LIKE_RE`). Documentation keys
    are skipped at every depth."""
    found: list[str] = []
    if isinstance(obj, Mapping):
        for key, value in obj.items():
            if str(key) in DOCUMENTATION_FIELDS:
                continue
            found.extend(find_dates(value, f"{path}.{key}" if path else str(key)))
    elif isinstance(obj, list | tuple):
        for i, value in enumerate(obj):
            found.extend(find_dates(value, f"{path}[{i}]"))
    elif isinstance(obj, date | datetime) or (isinstance(obj, str) and DATE_LIKE_RE.search(obj)):
        found.append(path or "<root>")
    return found


# ---------------------------------------------------------------------------
# Building blocks
# ---------------------------------------------------------------------------


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


Scalar = str | int | float | bool


class Definition(_Frozen):
    """What an exposure or outcome *is*: exactly one of a code set (``id@version``), a
    phenotype (``id@version``), a vendored concept table (``mimiciv_derived.<name>``) or
    a column (``cohort.<column>`` / ``<schema>.<table>.<column>``), plus an optional
    ``equals`` value the column / concept must take. References resolve to hashes at
    freeze time."""

    codeset: str | None = Field(default=None, description="a code set as id@version")
    phenotype: str | None = Field(default=None, description="a phenotype as id@version")
    concept: str | None = Field(default=None, description="a vendored concept table")
    column: str | None = Field(default=None, description="cohort.<col> or <schema>.<table>.<col>")
    equals: Scalar | None = Field(default=None, description="the value the column must take")

    @model_validator(mode="before")
    @classmethod
    def _one_kind(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            raise ValueError(
                "a definition is a mapping with exactly one of " + ", ".join(DEFINITION_KINDS)
            )
        kinds = [k for k in DEFINITION_KINDS if data.get(k) is not None]
        if len(kinds) != 1:
            raise ValueError(
                f"a definition names exactly one of {', '.join(DEFINITION_KINDS)}; got "
                f"{kinds or 'none'}"
            )
        return data

    @field_validator("codeset", "phenotype")
    @classmethod
    def _refs(cls, value: str | None, info: Any) -> str | None:
        return None if value is None else _ref(value, f"definition.{info.field_name}")

    @field_validator("concept")
    @classmethod
    def _concept_field(cls, value: str | None) -> str | None:
        return None if value is None else _concept(value, "definition.concept")

    @field_validator("column")
    @classmethod
    def _column_field(cls, value: str | None) -> str | None:
        return None if value is None else _source(value, "definition.column")

    @field_validator("equals")
    @classmethod
    def _equals(cls, value: Scalar | None) -> Scalar | None:
        if isinstance(value, str):
            return _text(value, "definition.equals")
        return value

    @model_validator(mode="after")
    def _rules(self) -> Definition:
        if self.equals is not None and self.column is None and self.concept is None:
            raise ValueError("definition.equals applies to a column or concept definition only")
        return self

    @property
    def kind(self) -> str:
        return next(k for k in DEFINITION_KINDS if getattr(self, k) is not None)

    @property
    def target(self) -> str:
        return str(getattr(self, self.kind))

    def canonical(self) -> dict[str, Any]:
        out: dict[str, Any] = {self.kind: self.target}
        if self.equals is not None:
            out["equals"] = self.equals
        return out

    def render(self) -> str:
        text = f"{self.kind} {self.target}"
        if self.equals is not None:
            text += f" = {self.equals!r}"
        return text


class Timing(_Frozen):
    """When an exposure counts: a window relative to the index."""

    relative_to: Literal["index"] = Field(default="index", description="the anchor")
    window: Window = Field(description="[start_h, end_h) hours relative to the anchor")

    def canonical(self) -> dict[str, Any]:
        return {"relative_to": self.relative_to, "window": self.window.canonical()}


class Exposure(_Frozen):
    """The exposure of an associational / causal protocol (``null`` for descriptive and
    predictive ones)."""

    name: str = Field(description="a slug; the report's exposure label")
    definition: Definition
    timing: Timing

    @field_validator("name")
    @classmethod
    def _name_field(cls, value: str) -> str:
        return _name(value, "exposure.name")

    def canonical(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "definition": self.definition.canonical(),
            "timing": self.timing.canonical(),
        }


class Outcome(_Frozen):
    """One outcome: its definition, an optional window (hours after the index) **or**
    horizon (days), its ``timesem`` censoring rule and the competing events."""

    name: str = Field(description="a slug; the report's outcome label")
    definition: Definition
    window_h: float | None = Field(default=None, gt=0, description="hours after the index")
    horizon_days: int | None = Field(default=None, ge=0, description="days after the index")
    censoring: str = Field(
        description="a timesem censoring-rule name",
        json_schema_extra={"enum": list(CENSORING_RULES)},
    )
    competing_events: tuple[str, ...] = Field(default=(), description="competing events")

    @field_validator("name")
    @classmethod
    def _name_field(cls, value: str) -> str:
        return _name(value, "outcome.name")

    @field_validator("censoring")
    @classmethod
    def _censoring(cls, value: str) -> str:
        if value not in timesem.CENSORING_RULES:
            raise ValueError(
                f"outcome.censoring {value!r} is not a censoring rule; expected one of "
                f"{', '.join(CENSORING_RULES)}"
            )
        return value

    @field_validator("competing_events", mode="before")
    @classmethod
    def _events(cls, value: Any) -> tuple[str, ...]:
        if value is None:
            return ()
        if not isinstance(value, list | tuple):
            raise ValueError("outcome.competing_events must be a list")
        events = tuple(sorted({str(e) for e in value}))
        unknown = sorted(set(events) - set(COMPETING_EVENTS))
        if unknown:
            raise ValueError(
                f"outcome.competing_events {unknown} unknown; expected a subset of "
                f"{list(COMPETING_EVENTS)}"
            )
        return events

    @model_validator(mode="before")
    @classmethod
    def _defaults(cls, data: Any) -> Any:
        if isinstance(data, dict):
            out = dict(data)
            rule = timesem.CENSORING_RULES.get(str(out.get("censoring")))
            if (
                rule is not None
                and rule.censored
                and out.get("horizon_days") is None
                and out.get("window_h") is None
            ):
                out["horizon_days"] = rule.horizon_days
            return out
        return data

    @model_validator(mode="after")
    def _rules(self) -> Outcome:
        if self.window_h is not None and self.horizon_days is not None:
            raise ValueError(f"outcome {self.name}: window_h and horizon_days are alternatives")
        rule = timesem.CENSORING_RULES[self.censoring]
        if not rule.censored and self.horizon_days is not None:
            raise ValueError(
                f"outcome {self.name}: {self.censoring} is an in-hospital outcome resolved at "
                "discharge; it takes no horizon_days"
            )
        if self.competing_events and rule.censored:
            raise ValueError(
                f"outcome {self.name}: {self.censoring} is followed past discharge; "
                "discharge_alive competes with in-hospital outcomes only"
            )
        return self

    @property
    def rule(self) -> timesem.CensoringRule:
        """The resolved :class:`timesem.CensoringRule` (horizon and competing events)."""
        import dataclasses

        base = timesem.CENSORING_RULES[self.censoring]
        return dataclasses.replace(
            base,
            horizon_days=self.horizon_days if base.censored else None,
            competing_events=self.competing_events,
        )

    def canonical(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "definition": self.definition.canonical(),
            "window_h": self.window_h,
            "horizon_days": self.horizon_days,
            "censoring": self.censoring,
            "competing_events": list(self.competing_events),
        }

    def render(self) -> str:
        when = ""
        if self.window_h is not None:
            when = f" within {self.window_h:g} h"
        elif self.horizon_days is not None:
            when = f" within {self.horizon_days} d"
        competing = (
            f"; competing {', '.join(self.competing_events)}" if self.competing_events else ""
        )
        return (
            f"{self.name}: {self.definition.render()}{when}; censoring {self.censoring}{competing}"
        )


class Covariate(_Frozen):
    """One covariate: where it comes from, over which window, how it is transformed."""

    name: str = Field(description="a slug; the model term / table column")
    source: str = Field(description="cohort.<column> or <schema>.<table>.<column>")
    window: Window | None = Field(
        default=None, description="[start_h, end_h) hours; static when null"
    )
    transform: Transform = Field(default="identity", description="the transform applied")

    @field_validator("name")
    @classmethod
    def _name_field(cls, value: str) -> str:
        return _name(value, "covariate.name")

    @field_validator("source")
    @classmethod
    def _source_field(cls, value: str) -> str:
        return _source(value, "covariate.source")

    def canonical(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "source": self.source,
            "window": None if self.window is None else self.window.canonical(),
            "transform": self.transform,
        }

    def render(self) -> str:
        window = f" over [{self.window.start_h:g}, {self.window.end_h:g}) h" if self.window else ""
        transform = "" if self.transform == "identity" else f" ({self.transform})"
        return f"{self.name} <- {self.source}{window}{transform}"


class FeatureWindows(_Frozen):
    """The observation window ``[start_h, end_h)`` relative to the index, the gap after
    it and the prediction horizon (hours)."""

    observation: Window = Field(description="the feature window relative to the index")
    gap_h: float = Field(default=0.0, ge=0, description="hours between observation end and target")
    prediction_h: float | None = Field(default=None, gt=0, description="prediction horizon, hours")

    def canonical(self) -> dict[str, Any]:
        return {
            "observation": self.observation.canonical(),
            "gap_h": self.gap_h,
            "prediction_h": self.prediction_h,
        }


class AnalysisPlan(_Frozen):
    """The pre-specified analysis: method family, estimand, model spec, hyperparameter
    policy, subgroups, sensitivity analyses, multiplicity rule, missing-data policy and
    the sample-size note (short texts; hashed)."""

    method_family: MethodFamily = Field(description="the method family")
    estimand: str = Field(description="what is estimated (or described)")
    model_spec: str = Field(description="the model / summary specification")
    hyperparameter_policy: str = Field(
        default="none (no tuned hyperparameters)", description="how hyperparameters are chosen"
    )
    subgroups: tuple[str, ...] = Field(default=(), description="pre-specified subgroups")
    sensitivity_analyses: tuple[str, ...] = Field(
        default=(), description="pre-specified sensitivity analyses"
    )
    multiplicity: str = Field(
        default="none (single pre-specified analysis)", description="the multiplicity rule"
    )
    missing_data: str = Field(
        default="complete-case; missingness reported per covariate",
        description="the missing-data policy",
    )
    sample_size_note: str = Field(
        default="fixed by the cohort (retrospective); no power calculation",
        description="sample size",
    )

    @field_validator(
        "estimand",
        "model_spec",
        "hyperparameter_policy",
        "multiplicity",
        "missing_data",
        "sample_size_note",
    )
    @classmethod
    def _texts_(cls, value: str, info: Any) -> str:
        return _text(value, f"analysis_plan.{info.field_name}")

    @field_validator("subgroups", "sensitivity_analyses", mode="before")
    @classmethod
    def _lists(cls, value: Any, info: Any) -> tuple[str, ...]:
        return _texts(value, f"analysis_plan.{info.field_name}")

    def canonical(self) -> dict[str, Any]:
        return {
            "method_family": self.method_family,
            "estimand": self.estimand,
            "model_spec": self.model_spec,
            "hyperparameter_policy": self.hyperparameter_policy,
            "subgroups": list(self.subgroups),
            "sensitivity_analyses": list(self.sensitivity_analyses),
            "multiplicity": self.multiplicity,
            "missing_data": self.missing_data,
            "sample_size_note": self.sample_size_note,
        }


def _eras(value: Any, what: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list | tuple):
        raise ValueError(f"{what} must be a list of anchor_year_group labels")
    eras: dict[int, str] = {}
    for item in value:
        try:
            era = timesem.era_of(str(item))
        except timesem.UnknownEraError:
            raise ValueError(
                f"{what}: {item!r} is not an anchor_year_group; expected a subset of "
                f"{', '.join(repr(e) for e in timesem.ERAS)}"
            ) from None
        eras[era.index] = era.label
    return tuple(eras[i] for i in sorted(eras))


class TemporalHoldout(_Frozen):
    """The era partition of a temporal holdout (by ``anchor_year_group`` only, never
    calendar dates): development, holdout and sealed eras — disjoint subsets of
    ``timesem.ERAS``; development and holdout non-empty. Declared here, consumed by
    EP-129's runner (which adds the one-look rule)."""

    development_eras: tuple[str, ...] = Field(
        description="eras the plan is fitted on",
        json_schema_extra={"items": {"type": "string", "enum": list(timesem.ERAS)}},
    )
    holdout_eras: tuple[str, ...] = Field(
        description="eras evaluated once",
        json_schema_extra={"items": {"type": "string", "enum": list(timesem.ERAS)}},
    )
    sealed_eras: tuple[str, ...] = Field(
        default=(),
        description="eras never entering any frame",
        json_schema_extra={"items": {"type": "string", "enum": list(timesem.ERAS)}},
    )

    @field_validator("development_eras", "holdout_eras", "sealed_eras", mode="before")
    @classmethod
    def _era_lists(cls, value: Any, info: Any) -> tuple[str, ...]:
        return _eras(value, f"temporal_holdout.{info.field_name}")

    @model_validator(mode="after")
    def _rules(self) -> TemporalHoldout:
        if not self.development_eras or not self.holdout_eras:
            raise ValueError(
                "temporal_holdout: development_eras and holdout_eras must be non-empty"
            )
        lists = {
            "development_eras": set(self.development_eras),
            "holdout_eras": set(self.holdout_eras),
            "sealed_eras": set(self.sealed_eras),
        }
        names = list(lists)
        for i, a in enumerate(names):
            for b in names[i + 1 :]:
                shared = sorted(lists[a] & lists[b])
                if shared:
                    raise ValueError(f"temporal_holdout: {a} and {b} share {shared}")
        return self

    def canonical(self) -> dict[str, Any]:
        return {
            "development_eras": list(self.development_eras),
            "holdout_eras": list(self.holdout_eras),
            "sealed_eras": list(self.sealed_eras),
        }


class References(_Frozen):
    """The declared references (optional; when given they must equal what the
    definitions name)."""

    cohorts: tuple[str, ...] = Field(default=(), description="cohort specs as id@version")
    codesets: tuple[str, ...] = Field(default=(), description="code sets as id@version")
    phenotypes: tuple[str, ...] = Field(default=(), description="phenotypes as id@version")
    concepts: tuple[str, ...] = Field(default=(), description="vendored concept tables")

    @field_validator("cohorts", "codesets", "phenotypes", mode="before")
    @classmethod
    def _refs(cls, value: Any, info: Any) -> tuple[str, ...]:
        if value is None:
            return ()
        if not isinstance(value, list | tuple):
            raise ValueError(f"references.{info.field_name} must be a list of id@version")
        return tuple(sorted({_ref(v, f"references.{info.field_name}") for v in value}))

    @field_validator("concepts", mode="before")
    @classmethod
    def _concepts(cls, value: Any) -> tuple[str, ...]:
        if value is None:
            return ()
        if not isinstance(value, list | tuple):
            raise ValueError("references.concepts must be a list of concept tables")
        return tuple(sorted({_concept(v, "references.concepts") for v in value}))


# ---------------------------------------------------------------------------
# The protocol
# ---------------------------------------------------------------------------


class Protocol(_Frozen):
    """One versioned analysis protocol (module docstring)."""

    id: str = Field(description="a slug; with version the reference id@version")
    version: str = Field(description="semver", json_schema_extra={"pattern": _SEMVER})
    title: str = Field(min_length=1, description="documentation (not hashed)")
    description: str = Field(default="", description="documentation (not hashed)")
    claim_type: ClaimType = Field(description="the claim the report may make")
    cohort: str = Field(description="the EP-46 cohort spec as id@version")
    unit_of_analysis: str = Field(
        description="the grain (timesem's registry; must be the cohort's)",
        json_schema_extra={"enum": list(AVAILABLE_GRAINS)},
    )
    exposure: Exposure | None = Field(default=None, description="null for descriptive / predictive")
    outcomes: tuple[Outcome, ...] = Field(description="at least one")
    covariates: tuple[Covariate, ...] = Field(default=(), description="ordered")
    feature_windows: FeatureWindows
    analysis_plan: AnalysisPlan
    temporal_holdout: TemporalHoldout | None = Field(
        default=None, description="declared eras, or null"
    )
    seeds_policy: str = Field(default=SEEDS_POLICY, description="fixed text (EP-36)")
    retrospective_statement: str = Field(
        default=RETROSPECTIVE_STATEMENT, description="fixed text (GOVERNANCE §7)"
    )
    references: References | None = Field(default=None, description="optional; must match")
    amends: str | None = Field(default=None, description="the previous frozen hash, or null")
    amendment_reason: str = Field(default="", description="documentation (not hashed)")
    notes: str = Field(default="", description="documentation (not hashed)")

    @model_validator(mode="before")
    @classmethod
    def _no_dates(cls, data: Any) -> Any:
        if isinstance(data, dict):
            found = find_dates(data)
            if found:
                raise ValueError(
                    "absolute dates are forbidden in a protocol (per-patient date shift; use "
                    f"relative windows and anchor_year_group eras): {', '.join(found[:5])}"
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

    @field_validator("cohort")
    @classmethod
    def _cohort(cls, value: str) -> str:
        return _ref(value, "cohort")

    @field_validator("unit_of_analysis")
    @classmethod
    def _grain(cls, value: str) -> str:
        try:
            grain = timesem.grain(value)
        except timesem.GrainError as exc:
            raise ValueError(str(exc)) from None
        if not grain.available:
            raise ValueError(
                f"unit_of_analysis {value!r} is not available (a placeholder until "
                f"{grain.available_from}); available grains: {', '.join(AVAILABLE_GRAINS)}"
            )
        return value

    @field_validator("outcomes", "covariates", mode="before")
    @classmethod
    def _lists(cls, value: Any) -> Any:
        return () if value is None else value

    @field_validator("seeds_policy")
    @classmethod
    def _seeds(cls, value: str) -> str:
        if value.strip() != SEEDS_POLICY:
            raise ValueError(
                "seeds_policy is fixed text; omit it or copy protocol.spec.SEEDS_POLICY"
            )
        return SEEDS_POLICY

    @field_validator("retrospective_statement")
    @classmethod
    def _statement(cls, value: str) -> str:
        if " ".join(value.split()) != RETROSPECTIVE_STATEMENT:
            raise ValueError(
                "retrospective_statement is fixed text; omit it or copy "
                "protocol.spec.RETROSPECTIVE_STATEMENT"
            )
        return RETROSPECTIVE_STATEMENT

    @field_validator("amends")
    @classmethod
    def _amends(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = str(value).strip().lower()
        if not HASH_RE.match(text):
            raise ValueError("amends must be the previous frozen protocol hash (64 hex chars)")
        return text

    @field_validator("amendment_reason", "notes", "description", mode="before")
    @classmethod
    def _doc(cls, value: Any) -> str:
        return "" if value is None else str(value)

    @model_validator(mode="after")
    def _rules(self) -> Protocol:
        if not self.outcomes:
            raise ValueError(f"{self.ref}: at least one outcome is required")
        names = [o.name for o in self.outcomes]
        dupes = sorted({n for n in names if names.count(n) > 1})
        if dupes:
            raise ValueError(f"{self.ref}: duplicate outcome name(s) {dupes}")
        names = [c.name for c in self.covariates]
        dupes = sorted({n for n in names if names.count(n) > 1})
        if dupes:
            raise ValueError(f"{self.ref}: duplicate covariate name(s) {dupes}")
        if self.exposure is not None and self.exposure.name in names:
            raise ValueError(f"{self.ref}: the exposure {self.exposure.name!r} is also a covariate")
        if self.claim_type in ("associational", "causal") and self.exposure is None:
            raise ValueError(f"{self.ref}: a {self.claim_type} claim needs an exposure")
        family = self.analysis_plan.method_family
        if self.claim_type == "causal" and family != "causal":
            raise ValueError(
                f"{self.ref}: a causal claim needs the causal method family, not {family}"
            )
        if self.claim_type == "predictive" and family not in ("prediction", "bayes"):
            raise ValueError(
                f"{self.ref}: a predictive claim needs the prediction or bayes method family, "
                f"not {family}"
            )
        if family == "causal" and self.exposure is None:
            raise ValueError(f"{self.ref}: the causal method family needs an exposure")
        if self.amends is None and self.amendment_reason.strip():
            raise ValueError(f"{self.ref}: amendment_reason without amends")
        if self.references is not None:
            declared = {
                "cohorts": set(self.references.cohorts),
                "codesets": set(self.references.codesets),
                "phenotypes": set(self.references.phenotypes),
                "concepts": set(self.references.concepts),
            }
            named = {
                "cohorts": {self.cohort},
                "codesets": set(self.codeset_refs),
                "phenotypes": set(self.phenotype_refs),
                "concepts": set(self.concept_tables),
            }
            for kind, have in declared.items():
                if have != named[kind]:
                    raise ValueError(
                        f"{self.ref}: references.{kind} {sorted(have)} differ from what the "
                        f"definitions name {sorted(named[kind])}"
                    )
        return self

    # -- derived views ---------------------------------------------------------------------

    @property
    def ref(self) -> str:
        return format_ref(self.id, self.version)

    def definitions(self) -> Iterator[tuple[str, Definition]]:
        """Every definition with its owner (``exposure`` / ``outcome:<name>``)."""
        if self.exposure is not None:
            yield "exposure", self.exposure.definition
        for outcome in self.outcomes:
            yield f"outcome:{outcome.name}", outcome.definition

    @property
    def codeset_refs(self) -> tuple[str, ...]:
        return tuple(sorted({d.codeset for _o, d in self.definitions() if d.codeset}))

    @property
    def phenotype_refs(self) -> tuple[str, ...]:
        return tuple(sorted({d.phenotype for _o, d in self.definitions() if d.phenotype}))

    @property
    def concept_tables(self) -> tuple[str, ...]:
        return tuple(sorted({d.concept for _o, d in self.definitions() if d.concept}))

    @property
    def hash_required(self) -> bool:
        """Whether runs of this protocol must cite its frozen hash (D-25)."""
        return self.claim_type in HASH_REQUIRED_CLAIM_TYPES

    # -- the hash ---------------------------------------------------------------------------

    def canonical(self, reference_hashes: Mapping[str, Mapping[str, str]]) -> dict[str, Any]:
        """The hashed shape (module docstring). ``reference_hashes`` is
        ``{"cohort": {ref: def_hash}, "codeset": {...}, "phenotype": {...},
        "concept": {table: sha256}}`` and must cover every reference the definition
        names."""
        cohorts = reference_hashes.get("cohort", {})
        codesets = reference_hashes.get("codeset", {})
        phenotypes = reference_hashes.get("phenotype", {})
        concepts = reference_hashes.get("concept", {})
        missing = sorted(
            ([f"cohort {self.cohort}"] if self.cohort not in cohorts else [])
            + [f"codeset {r}" for r in self.codeset_refs if r not in codesets]
            + [f"phenotype {r}" for r in self.phenotype_refs if r not in phenotypes]
            + [f"concept {t}" for t in self.concept_tables if t not in concepts]
        )
        if missing:
            raise ProtocolError(f"{self.ref}: unresolved reference(s) {missing}")
        return {
            "id": self.id,
            "version": self.version,
            "claim_type": self.claim_type,
            "cohort": {self.cohort: cohorts[self.cohort]},
            "unit_of_analysis": self.unit_of_analysis,
            "exposure": None if self.exposure is None else self.exposure.canonical(),
            "outcomes": [o.canonical() for o in self.outcomes],
            "covariates": [c.canonical() for c in self.covariates],
            "feature_windows": self.feature_windows.canonical(),
            "analysis_plan": self.analysis_plan.canonical(),
            "temporal_holdout": (
                None if self.temporal_holdout is None else self.temporal_holdout.canonical()
            ),
            "seeds_policy": self.seeds_policy,
            "retrospective_statement": self.retrospective_statement,
            "references": {
                "codeset": {r: codesets[r] for r in self.codeset_refs},
                "phenotype": {r: phenotypes[r] for r in self.phenotype_refs},
                "concept": {t: concepts[t] for t in self.concept_tables},
            },
            "amends": self.amends,
        }

    def content_hash(self, reference_hashes: Mapping[str, Mapping[str, str]]) -> str:
        """sha256 of the canonical JSON (module docstring)."""
        payload = canonical_json(self.canonical(reference_hashes))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Loading, the JSON schema
# ---------------------------------------------------------------------------


def _format_validation_error(where: str, exc: ValidationError) -> str:
    lines = [f"{where}: {exc.error_count()} validation error(s)"]
    for e in exc.errors():
        loc = ".".join(str(p) for p in e["loc"])
        lines.append(f"  {loc}: {e['msg']}")
    return "\n".join(lines)


def protocol_from_text(text: str, *, where: str = "<text>") -> Protocol:
    """Parse one YAML document into a :class:`Protocol` (:class:`ProtocolError` names
    every validation problem)."""
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ProtocolError(f"{where}: cannot parse ({exc})") from exc
    if not isinstance(doc, dict):
        raise ProtocolError(f"{where}: top level must be a mapping")
    try:
        return Protocol.model_validate(doc)
    except ValidationError as exc:
        raise ProtocolError(_format_validation_error(where, exc)) from None


def read_text(path: Path | str) -> str:
    """The file's text with its line endings intact (what the frozen copy preserves
    byte for byte)."""
    path = Path(path)
    try:
        return path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ProtocolError(f"{path.name}: cannot read ({exc})") from exc


def load_protocol(path: Path | str) -> Protocol:
    """Parse and validate one protocol YAML file."""
    path = Path(path)
    return protocol_from_text(read_text(path), where=path.name)


def json_schema() -> dict[str, Any]:
    """The JSON schema of :class:`Protocol` (the Freezer page, EP-128, renders it):
    pydantic's schema plus the exactly-one-kind constraint on ``Definition``."""
    schema = Protocol.model_json_schema()
    defs = schema.get("$defs", {})
    if "Definition" in defs:
        defs["Definition"]["oneOf"] = [{"required": [kind]} for kind in DEFINITION_KINDS]
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = "Protocol"
    schema["description"] = (
        "A mimicwarehouse analysis protocol (EP-51): claim type, cohort reference, unit of "
        "analysis, exposure, outcomes, covariates, feature windows, analysis plan, temporal "
        "holdout by anchor_year_group era, the fixed seeds policy and retrospective "
        "statement, references and the amendment link. No absolute dates; windows are "
        "[start, end) hours relative to the index."
    )
    return schema


__all__ = [
    "AVAILABLE_GRAINS",
    "CENSORING_RULES",
    "CLAIM_RULES",
    "CLAIM_TYPES",
    "COHORT_SOURCE",
    "COMPETING_EVENTS",
    "CONCEPT_SCHEMA",
    "DATE_LIKE_RE",
    "DEFINITION_KINDS",
    "DOCUMENTATION_FIELDS",
    "HASH_RE",
    "HASH_REQUIRED_CLAIM_TYPES",
    "METHOD_FAMILIES",
    "NAME_MAX_CHARS",
    "RETROSPECTIVE_STATEMENT",
    "SEEDS_POLICY",
    "TEXT_MAX_CHARS",
    "TRANSFORMS",
    "AnalysisPlan",
    "ClaimType",
    "Covariate",
    "Definition",
    "Exposure",
    "FeatureWindows",
    "MethodFamily",
    "Outcome",
    "Protocol",
    "ProtocolError",
    "References",
    "Scalar",
    "TemporalHoldout",
    "Timing",
    "Transform",
    "find_dates",
    "identifier_columns",
    "json_schema",
    "load_protocol",
    "protocol_from_text",
    "read_text",
]
