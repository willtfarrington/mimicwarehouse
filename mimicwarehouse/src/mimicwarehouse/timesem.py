"""Time semantics + unit-of-analysis registry (EP-34; DESIGN §7, §15).

The one shared answer to "what is time in MIMIC-IV and what is a row?", written once as
code so cohorts (EP-46/47), timelines (EP-49), the events spine (EP-50), marts (EP-55/56),
rates (EP-68) and endpoints (EP-75/76) import it instead of re-deriving it. The MIMIC
caveats it encodes (``docs/methods/time-semantics.md`` is the prose twin):

* **Date shift.** PhysioNet shifts every patient's timestamps by a patient-specific
  offset, so calendar time is meaningless across patients. Timestamps are stored as naive
  ``TIMESTAMP`` exactly as shipped and this module never localizes or formats them; the
  only admissible cross-patient temporal axis is ``patients.anchor_year_group``
  (:data:`ERAS`, five 3-year eras). Analyses use within-patient **relative** time
  (:func:`sql_hours_since` & co., the ``hours_since_icu_intime`` naming convention).
* **Age.** ``anchor_age`` is the age in ``anchor_year``; the age at an event is the anchor
  age plus the calendar-year offset of the event from the anchor year — both years carry
  the same shift, so the difference is real. Ages >= 89 are shipped as 91
  (:data:`AGE_CAP`), so a computed age that reaches 91 is a censored bucket, never a
  value (:func:`is_age_capped`). :func:`age_at` / :func:`sql_age_at` are the **only**
  place a calendar year is read.
* **ICD versions.** ICD-9 -> ICD-10 coding switched around 2015 and is visible per row
  as ``diagnoses_icd.icd_version`` — an admission is classified ``icd9`` / ``icd10`` /
  ``mixed`` from its rows (:func:`icd_versions_of_hadm`), never from a date.
* **``dod``.** The date of death is populated only up to about one year after a
  patient's **last** discharge (:data:`DOD_VISIBILITY_DAYS`), so every out-of-hospital
  mortality outcome carries an explicit censoring horizon (:class:`CensoringRule`,
  :data:`CENSORING_RULES`); discharge alive is the competing event of every in-hospital
  outcome.
* **Grains.** The unit-of-analysis registry (:class:`Grain`, :data:`GRAINS`) names what a
  row is — ``subject`` / ``hadm`` / ``icustay`` / ``icu_day`` / ``hour_bin`` /
  ``person_time`` today, ``edstay`` (EP-142) and ``note`` (EP-148) as placeholders that
  specs may name but compilers refuse — with its keys, source, time anchor and named
  index-event rules (:data:`INDEX_RULES`) that render as deterministic SQL fragments.

Every SQL builder returns a DuckDB expression or fragment as text — the cohort compiler
(EP-47) and the marts (EP-55) embed identical logic by calling the same functions; the
Python twins exist for tests and for frames already in memory. :func:`create_views` is the
catalog extension registered in :data:`mimicwarehouse.catalog.build.CATALOG_EXTENSIONS`:
it adds ``mimiciv_derived.hadm_era``, ``mimiciv_derived.icustay_index`` and the
``meta.grains`` registry table to every tier catalog on the build connection it is handed.
The tracer (EP-31) cites this module for its age rule and age bands (its ``sql/`` file
embeds :func:`sql_age_at` fragments verbatim; the acceptance is count-for-count identity).

Import budget: stdlib only (``tracer.py`` imports this module on the ``mwh`` start-up
path, DESIGN §15) — DuckDB is reached only through the connection a caller hands in.
"""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:  # pragma: no cover
    import duckdb

_LOG = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Eras — the only cross-patient temporal axis (item 1)
# ---------------------------------------------------------------------------

#: ``patients.anchor_year_group`` in MIMIC's literal spelling, in chronological order;
#: the tuple index is :attr:`Era.index`.
ERAS: tuple[str, ...] = (
    "2008 - 2010",
    "2011 - 2013",
    "2014 - 2016",
    "2017 - 2019",
    "2020 - 2022",
)

_ERA_LABEL_RE = re.compile(r"^\s*(\d{4})\s*-\s*(\d{4})\s*$")


class UnknownEraError(ValueError):
    """A label that is not one of the five ``anchor_year_group`` values."""


@dataclass(frozen=True, slots=True)
class Era:
    """One 3-year era of the anchor-year axis: label, ordinal index and its year span
    (inclusive; the years are real calendar years — the anchor axis is not shifted)."""

    label: str
    index: int
    start_year: int
    end_year: int

    @property
    def years(self) -> range:
        return range(self.start_year, self.end_year + 1)


def _normalise_era_label(label: str) -> str:
    m = _ERA_LABEL_RE.match(label)
    if m is None:
        raise UnknownEraError(f"not an anchor_year_group label: {label!r}")
    return f"{m.group(1)} - {m.group(2)}"


def _era_from_label(label: str, index: int) -> Era:
    m = _ERA_LABEL_RE.match(label)
    assert m is not None  # ERAS is a constant of this module
    return Era(label=label, index=index, start_year=int(m.group(1)), end_year=int(m.group(2)))


#: The five eras as :class:`Era` records, index order.
ERA_TABLE: tuple[Era, ...] = tuple(_era_from_label(label, i) for i, label in enumerate(ERAS))
_ERA_BY_LABEL: dict[str, Era] = {era.label: era for era in ERA_TABLE}


def era_of(anchor_year_group: str) -> Era:
    """The :class:`Era` of a ``patients.anchor_year_group`` value. Whitespace around the
    hyphen is normalised (``"2008-2010"`` resolves); anything else raises
    :class:`UnknownEraError` — never a silent NULL in Python."""
    try:
        return _ERA_BY_LABEL[_normalise_era_label(anchor_year_group)]
    except KeyError:
        raise UnknownEraError(f"unknown anchor_year_group {anchor_year_group!r}") from None


def sql_era_index(anchor_year_group_expr: str) -> str:
    """DuckDB ``CASE`` expression mapping an ``anchor_year_group`` expression to the era
    index 0-4 (``NULL`` for any other value)."""
    whens = " ".join(f"WHEN '{era.label}' THEN {era.index}" for era in ERA_TABLE)
    return f"CASE {anchor_year_group_expr} {whens} END"


# ---------------------------------------------------------------------------
# Ages — the anchor rule and the 91 cap (item 1)
# ---------------------------------------------------------------------------

#: PhysioNet ships every age >= 89 as 91; a computed age that reaches it is censored.
AGE_CAP = 91


def age_at(anchor_age: int, anchor_year: int, event_ts: datetime | date) -> float:
    """Age at ``event_ts``: ``anchor_age`` plus the event's calendar-year offset from
    ``anchor_year``. Both years are shifted by the same patient offset, so the difference
    is real; this and :func:`sql_age_at` are the module's only calendar reads. Uncapped —
    combine with :func:`is_age_capped` (values >= 91 are the censored bucket)."""
    return float(anchor_age + (event_ts.year - anchor_year))


def is_age_capped(age: float) -> bool:
    """Whether an age value sits in the ``>= 89`` bucket PhysioNet ships as 91. True at
    91 itself too: a genuine 91 cannot be told apart from the sentinel."""
    return age >= AGE_CAP


def sql_age_at(anchor_age: str, anchor_year: str, event_ts: str, *, cap: bool = False) -> str:
    """The SQL twin of :func:`age_at`: ``<anchor_age> + (year(<event_ts>) - <anchor_year>)``,
    wrapped in ``least(..., 91)`` when ``cap`` is set (the tracer's ``age_at_admit``)."""
    expr = f"{anchor_age} + (year({event_ts}) - {anchor_year})"
    return f"least({expr}, {AGE_CAP})" if cap else expr


def sql_is_age_capped(age_expr: str) -> str:
    """The SQL twin of :func:`is_age_capped`."""
    return f"({age_expr}) >= {AGE_CAP}"


#: The tracer's age bands (EP-31; upper bounds exclusive, the cohort floor is 18) — the
#: named fragment the EP-33 amendment asked this module to carry.
AGE_BANDS: tuple[tuple[str, int | None], ...] = (
    ("18-39", 40),
    ("40-64", 65),
    ("65-79", 80),
    ("80+", None),
)


def age_band(age: float, bands: tuple[tuple[str, int | None], ...] = AGE_BANDS) -> str:
    """The band label of ``age`` under ``bands`` (upper bounds exclusive; the last band
    is open-ended)."""
    for label, upper in bands:
        if upper is None or age < upper:
            return label
    raise ValueError("bands must end with an open-ended (None) band")


def sql_age_band(age_expr: str, bands: tuple[tuple[str, int | None], ...] = AGE_BANDS) -> str:
    """DuckDB ``CASE`` expression banding ``age_expr`` — the tracer's descriptives cite it
    (``CASE WHEN age_at_admit < 40 THEN '18-39' ... ELSE '80+' END``)."""
    parts = []
    for label, upper in bands:
        if upper is None:
            parts.append(f"ELSE '{label}'")
        else:
            parts.append(f"WHEN {age_expr} < {upper} THEN '{label}'")
    return "CASE " + " ".join(parts) + " END"


# ---------------------------------------------------------------------------
# ICD versions — per row, never by date (item 1)
# ---------------------------------------------------------------------------

IcdVersions = Literal["icd9", "icd10", "mixed"]
#: The admission-level classification labels (short: they pass the free-text check).
ICD_VERSION_LABELS: tuple[IcdVersions, ...] = ("icd9", "icd10", "mixed")


def icd_versions_of_hadm(icd_versions: Iterable[int | None]) -> IcdVersions | None:
    """Classify one admission from its ``diagnoses_icd.icd_version`` values: ``icd9`` when
    every row is 9, ``icd10`` when every row is 10, ``mixed`` otherwise (a NULL or foreign
    version counts as neither); ``None`` for an admission without diagnosis rows."""
    versions = list(icd_versions)
    if not versions:
        return None
    if all(v == 9 for v in versions):
        return "icd9"
    if all(v == 10 for v in versions):
        return "icd10"
    return "mixed"


def sql_icd_versions(icd_version_expr: str) -> str:
    """The aggregate twin of :func:`icd_versions_of_hadm` for a ``GROUP BY hadm_id`` over
    ``diagnoses_icd`` (``count(*)`` is >= 1 inside a group, so the CASE never sees an
    empty admission — an admission without rows is absent from the grouped relation)."""
    return (
        f"CASE WHEN count(*) FILTER (WHERE {icd_version_expr} = 9) = count(*) THEN 'icd9' "
        f"WHEN count(*) FILTER (WHERE {icd_version_expr} = 10) = count(*) THEN 'icd10' "
        "ELSE 'mixed' END"
    )


# ---------------------------------------------------------------------------
# Relative time (item 2)
# ---------------------------------------------------------------------------

SECONDS_PER_HOUR = 3600.0
SECONDS_PER_DAY = 86400.0


def sql_hours_since(anchor_expr: str, event_expr: str) -> str:
    """Signed hours from ``anchor_expr`` to ``event_expr`` (negative before the anchor):
    ``date_diff('second', anchor, event) / 3600.0``. Naive timestamps in, DOUBLE out."""
    return f"date_diff('second', {anchor_expr}, {event_expr}) / 3600.0"


def sql_days_since(anchor_expr: str, event_expr: str) -> str:
    """Signed days from ``anchor_expr`` to ``event_expr`` (``/ 86400.0``)."""
    return f"date_diff('second', {anchor_expr}, {event_expr}) / 86400.0"


def seconds_between(anchor: datetime, event: datetime) -> int:
    """Whole seconds from ``anchor`` to ``event``, counting second boundaries exactly like
    DuckDB's ``date_diff('second', anchor, event)`` (sub-second parts are truncated on both
    sides first — MIMIC timestamps are whole seconds, so this only matters for crafted
    inputs)."""
    return int((event.replace(microsecond=0) - anchor.replace(microsecond=0)).total_seconds())


def hours_since(anchor: datetime, event: datetime) -> float:
    """The Python twin of :func:`sql_hours_since`."""
    return seconds_between(anchor, event) / SECONDS_PER_HOUR


def days_since(anchor: datetime, event: datetime) -> float:
    """The Python twin of :func:`sql_days_since`."""
    return seconds_between(anchor, event) / SECONDS_PER_DAY


def hour_bin(hours: float, width_h: float = 1.0) -> int:
    """The bin index of a relative time under ``[start, end)`` semantics: bin ``i`` covers
    ``[i * width_h, (i + 1) * width_h)``, so ``hour_bin(1.0) == 1`` and negative hours
    fall into negative bins (``hour_bin(-0.5) == -1``)."""
    if width_h <= 0:
        raise ValueError(f"width_h must be positive, got {width_h!r}")
    return math.floor(hours / width_h)


def bin_bounds(index: int, width_h: float = 1.0) -> tuple[float, float]:
    """``(start, end)`` in hours of bin ``index`` — the half-open interval it covers."""
    if width_h <= 0:
        raise ValueError(f"width_h must be positive, got {width_h!r}")
    return index * width_h, (index + 1) * width_h


def sql_hour_bin(hours_expr: str, width_h: float = 1.0) -> str:
    """The SQL twin of :func:`hour_bin`: ``CAST(floor(hours / width) AS INTEGER)``."""
    if width_h <= 0:
        raise ValueError(f"width_h must be positive, got {width_h!r}")
    return f"CAST(floor(({hours_expr}) / {float(width_h)!r}) AS INTEGER)"


Direction = Literal["since", "before"]
TimeUnit = Literal["hours", "days"]


@dataclass(frozen=True, slots=True)
class RelativeTime:
    """A named relative-time axis: ``name`` is the column name every consumer uses,
    ``anchor`` the qualified anchor column, ``direction`` whether the axis counts hours
    *since* the anchor (event - anchor) or *before* it (anchor - event)."""

    name: str
    anchor: str  # schema.table.column
    direction: Direction = "since"
    unit: TimeUnit = "hours"

    @property
    def anchor_column(self) -> str:
        return self.anchor.rsplit(".", 1)[-1]

    def sql(self, event_expr: str, anchor_expr: str | None = None) -> str:
        """The expression for ``event_expr`` on this axis; ``anchor_expr`` defaults to the
        bare anchor column name (the caller's FROM clause supplies it)."""
        anchor = anchor_expr if anchor_expr is not None else self.anchor_column
        builder = sql_hours_since if self.unit == "hours" else sql_days_since
        return (
            builder(anchor, event_expr)
            if self.direction == "since"
            else builder(event_expr, anchor)
        )


HOURS_SINCE_ICU_INTIME = RelativeTime("hours_since_icu_intime", "mimiciv_icu.icustays.intime")
HOURS_SINCE_HOSP_ADMIT = RelativeTime("hours_since_hosp_admit", "mimiciv_hosp.admissions.admittime")
HOURS_BEFORE_DISCHARGE = RelativeTime(
    "hours_before_discharge", "mimiciv_hosp.admissions.dischtime", direction="before"
)
#: The naming convention used everywhere (DESIGN §7): name -> axis.
RELATIVE_TIMES: dict[str, RelativeTime] = {
    axis.name: axis
    for axis in (HOURS_SINCE_ICU_INTIME, HOURS_SINCE_HOSP_ADMIT, HOURS_BEFORE_DISCHARGE)
}


# ---------------------------------------------------------------------------
# dod censoring + competing events (item 3)
# ---------------------------------------------------------------------------

#: ``patients.dod`` is populated up to about one year after the patient's last discharge.
DOD_VISIBILITY_DAYS = 365
#: The competing event of every in-hospital outcome.
DISCHARGE_ALIVE = "discharge_alive"

Anchor = Literal["dischtime", "intime", "index_time"]
ANCHORS: tuple[Anchor, ...] = ("dischtime", "intime", "index_time")


def dod_visibility_end(last_dischtime: datetime) -> datetime:
    """The last instant at which a death is observable for a patient: the last discharge
    plus :data:`DOD_VISIBILITY_DAYS`."""
    return last_dischtime + timedelta(days=DOD_VISIBILITY_DAYS)


def sql_dod_visibility_end(last_dischtime_expr: str) -> str:
    return f"{last_dischtime_expr} + INTERVAL {DOD_VISIBILITY_DAYS} DAY"


def follow_up_end(
    last_dischtime: datetime, horizon_days: int, *, anchor: datetime | None = None
) -> datetime:
    """The end of observable follow-up for a horizon measured from ``anchor`` (default:
    the last discharge itself): ``min(anchor + horizon, last_dischtime + 365 d)`` — the
    ``dod`` visibility horizon binds whenever the horizon reaches past it."""
    if horizon_days < 0:
        raise ValueError(f"horizon_days must be >= 0, got {horizon_days!r}")
    origin = anchor if anchor is not None else last_dischtime
    return min(origin + timedelta(days=horizon_days), dod_visibility_end(last_dischtime))


def sql_follow_up_end(anchor_expr: str, last_dischtime_expr: str, horizon_days: int) -> str:
    """The SQL twin of :func:`follow_up_end`."""
    if horizon_days < 0:
        raise ValueError(f"horizon_days must be >= 0, got {horizon_days!r}")
    return (
        f"least({anchor_expr} + INTERVAL {horizon_days} DAY, "
        f"{sql_dod_visibility_end(last_dischtime_expr)})"
    )


@dataclass(frozen=True, slots=True)
class CensoringRule:
    """How one mortality outcome is followed up: ``horizon_days`` after ``anchor``
    (``None`` = an in-hospital outcome that needs no calendar censoring), censored at the
    ``dod`` visibility end, with the named competing events."""

    outcome: str
    horizon_days: int | None
    anchor: Anchor
    competing_events: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.anchor not in ANCHORS:
            raise ValueError(f"anchor must be one of {ANCHORS}, got {self.anchor!r}")
        if self.horizon_days is not None and self.horizon_days < 0:
            raise ValueError(f"horizon_days must be >= 0 or None, got {self.horizon_days!r}")

    @property
    def censored(self) -> bool:
        return self.horizon_days is not None

    def censor_time(self, anchor_ts: datetime, last_dischtime: datetime) -> datetime | None:
        """``min(anchor + horizon, last_dischtime + 365 d)``; ``None`` for in-hospital
        outcomes (their follow-up ends at discharge by construction)."""
        if self.horizon_days is None:
            return None
        return follow_up_end(last_dischtime, self.horizon_days, anchor=anchor_ts)

    def sql_censor_time(self, anchor_expr: str, last_dischtime_expr: str) -> str | None:
        """The SQL twin of :meth:`censor_time`."""
        if self.horizon_days is None:
            return None
        return sql_follow_up_end(anchor_expr, last_dischtime_expr, self.horizon_days)


IN_HOSPITAL_MORTALITY = CensoringRule(
    "in_hospital_mortality", None, "dischtime", competing_events=(DISCHARGE_ALIVE,)
)
MORTALITY_30D = CensoringRule("mortality_30d", 30, "index_time")
MORTALITY_90D = CensoringRule("mortality_90d", 90, "index_time")
MORTALITY_1Y = CensoringRule("mortality_1y", 365, "index_time")
#: The default rules by outcome name; endpoints (EP-75/76) start from these and override
#: the anchor per protocol (``dataclasses.replace``).
CENSORING_RULES: dict[str, CensoringRule] = {
    rule.outcome: rule
    for rule in (IN_HOSPITAL_MORTALITY, MORTALITY_30D, MORTALITY_90D, MORTALITY_1Y)
}


# ---------------------------------------------------------------------------
# Index-event rules — named SQL templates (item 4)
# ---------------------------------------------------------------------------


class GrainError(ValueError):
    """A grain / index-rule request the registry refuses."""


class GrainUnavailableError(GrainError):
    """The grain is a placeholder (``available=False``) — specs may name it, compilers
    must refuse it until the EP named in ``available_from`` ships."""


class UnknownIndexRuleError(GrainError):
    """Not an :data:`INDEX_RULES` name, or not applicable to the grain."""


def _first_by(columns: str, source: str, partition: str, order: str) -> str:
    """One row per ``partition`` — the first by ``order`` (a total order: time then id)."""
    return (
        f"SELECT {columns}\n"
        f"FROM (\n"
        f"    SELECT {columns},\n"
        f"           row_number() OVER (PARTITION BY {partition} ORDER BY {order}) AS seq\n"
        f"    FROM {source}\n"
        f")\nWHERE seq = 1"
    )


_HADM_COLUMNS = "subject_id, hadm_id, admittime AS index_time, dischtime AS end_time"
_HADM_RAW = "subject_id, hadm_id, admittime, dischtime"
_STAY_COLUMNS = "subject_id, hadm_id, stay_id, intime AS index_time, outtime AS end_time"
_STAY_RAW = "subject_id, hadm_id, stay_id, intime, outtime"

#: Every index-event template yields ``subject_id, hadm_id[, stay_id], index_time,
#: end_time`` from schema-qualified tables only (safe_query-compatible as a CTE body;
#: identifiers stay inside the chain, never in a final select list — DESIGN §9).
_INDEX_RULE_SQL: dict[str, str] = {
    "each_hadm": f"SELECT {_HADM_COLUMNS}\nFROM mimiciv_hosp.admissions",
    "first_hadm": (
        f"SELECT {_HADM_COLUMNS}\n"
        "FROM (\n"
        f"    SELECT {_HADM_RAW},\n"
        "           row_number() OVER (PARTITION BY subject_id ORDER BY admittime, hadm_id)"
        " AS seq\n"
        "    FROM mimiciv_hosp.admissions\n"
        ")\nWHERE seq = 1"
    ),
    "each_icustay": f"SELECT {_STAY_COLUMNS}\nFROM mimiciv_icu.icustays",
    "first_icu_stay": (
        f"SELECT {_STAY_COLUMNS}\n"
        "FROM (\n"
        f"    SELECT {_STAY_RAW},\n"
        "           row_number() OVER (PARTITION BY subject_id ORDER BY intime, stay_id)"
        " AS seq\n"
        "    FROM mimiciv_icu.icustays\n"
        ")\nWHERE seq = 1"
    ),
    "first_icu_stay_of_first_hadm": (
        f"SELECT {_STAY_COLUMNS}\n"
        "FROM (\n"
        "    SELECT i.subject_id, i.hadm_id, i.stay_id, i.intime, i.outtime,\n"
        "           row_number() OVER (PARTITION BY i.subject_id ORDER BY i.intime, i.stay_id)"
        " AS seq\n"
        "    FROM mimiciv_icu.icustays AS i\n"
        "    JOIN (\n"
        "        SELECT hadm_id\n"
        "        FROM (\n"
        "            SELECT hadm_id,\n"
        "                   row_number() OVER (PARTITION BY subject_id"
        " ORDER BY admittime, hadm_id) AS seq\n"
        "            FROM mimiciv_hosp.admissions\n"
        "        )\n"
        "        WHERE seq = 1\n"
        "    ) AS h ON i.hadm_id = h.hadm_id\n"
        ")\nWHERE seq = 1"
    ),
}
#: The named index-event rules, in registry order. ``first_icu_stay`` is the tracer's
#: rule (first ICU stay of the **subject**, by ``intime, stay_id``) and equals the
#: ``icustay_index.first_icu_stay_of_subject`` flag; ``first_icu_stay_of_first_hadm`` is
#: the first stay *inside the first admission* (empty for a subject whose first
#: admission had no ICU stay).
INDEX_RULES: tuple[str, ...] = (
    "first_icu_stay",
    "first_hadm",
    "each_hadm",
    "each_icustay",
    "first_icu_stay_of_first_hadm",
)
_STAY_RULES: frozenset[str] = frozenset(
    {"first_icu_stay", "each_icustay", "first_icu_stay_of_first_hadm"}
)
_HADM_RULES: frozenset[str] = frozenset({"first_hadm", "each_hadm"})


def index_event_sql(rule: str) -> str:
    """The SQL template of one :data:`INDEX_RULES` name (a SELECT yielding the keys,
    ``index_time`` and ``end_time``); :class:`UnknownIndexRuleError` otherwise."""
    try:
        return _INDEX_RULE_SQL[rule]
    except KeyError:
        raise UnknownIndexRuleError(
            f"unknown index-event rule {rule!r}; expected one of {', '.join(INDEX_RULES)}"
        ) from None


def _expansion_sql(stay_sql: str, *, index_name: str, seconds: float, interval_fn: str) -> str:
    """Expand a stay-level index-event relation into one row per ``[start, end)`` bin of
    ``seconds`` width from ``index_time`` to ``end_time`` (bins numbered from 0; a bin
    starts inside the stay; stays without an ``end_time`` yield no rows)."""
    return (
        "SELECT subject_id, hadm_id, stay_id, index_time, end_time, "
        f"CAST({index_name} AS INTEGER) AS {index_name},\n"
        f"       index_time + {interval_fn}(CAST({index_name} AS INTEGER)) AS bin_start,\n"
        f"       index_time + {interval_fn}(CAST({index_name} AS INTEGER) + 1) AS bin_end\n"
        "FROM (\n"
        "    SELECT subject_id, hadm_id, stay_id, index_time, end_time,\n"
        f"           unnest(range(0, n_bins)) AS {index_name}\n"
        "    FROM (\n"
        "        SELECT subject_id, hadm_id, stay_id, index_time, end_time,\n"
        "               CAST(ceil(date_diff('second', index_time, end_time) / "
        f"{seconds!r}) AS INTEGER) AS n_bins\n"
        "        FROM (\n"
        + "\n".join("            " + line for line in stay_sql.splitlines())
        + "\n        ) AS stays\n"
        "        WHERE end_time IS NOT NULL\n"
        "    )\n"
        ")"
    )


# ---------------------------------------------------------------------------
# The unit-of-analysis registry (item 4)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Grain:
    """One unit of analysis: what a row is, keyed how, anchored on which time column,
    with which index-event rules (``default_index_rule`` first among ``index_rules``)."""

    name: str
    keys: tuple[str, ...]
    source: str
    time_anchor: str | None
    index_rules: tuple[str, ...]
    default_index_rule: str | None
    available_from: str
    available: bool = True
    description: str = ""

    def __post_init__(self) -> None:
        unknown = [r for r in self.index_rules if r not in _INDEX_RULE_SQL]
        if unknown:
            raise ValueError(f"grain {self.name!r}: unknown index rules {unknown}")
        if self.default_index_rule is not None and self.default_index_rule not in self.index_rules:
            raise ValueError(
                f"grain {self.name!r}: default rule {self.default_index_rule!r} is not in "
                f"index_rules {self.index_rules}"
            )

    def keys_sql(self) -> str:
        """The key columns as a select-list fragment (``subject_id`` / ``stay_id, day_index``)."""
        return ", ".join(self.keys)

    def index_event_sql(self, rule: str | None = None) -> str:
        """A deterministic SQL fragment yielding one row per index event of this grain
        under ``rule`` (default :attr:`default_index_rule`): keys, ``index_time`` and
        ``end_time``; ``icu_day`` / ``hour_bin`` expand each stay into ``[start, end)``
        bins (``day_index`` / ``hour_bin``, ``bin_start``, ``bin_end``). Refuses a
        placeholder grain (:class:`GrainUnavailableError`), a rule the grain does not
        list (:class:`UnknownIndexRuleError`) and a grain without index templates
        (``person_time``: its intervals are defined by the analysis, EP-68)."""
        if not self.available:
            raise GrainUnavailableError(
                f"grain {self.name!r} is a placeholder until {self.available_from} "
                "(available=False) — a spec may name it, a compiler must refuse it"
            )
        if not self.index_rules:
            raise GrainError(
                f"grain {self.name!r} has no index-event template — its rows are defined "
                "by the analysis that builds them"
            )
        chosen = rule if rule is not None else self.default_index_rule
        if chosen is None or chosen not in self.index_rules:
            raise UnknownIndexRuleError(
                f"index-event rule {chosen!r} does not apply to grain {self.name!r}; "
                f"expected one of {', '.join(self.index_rules)}"
            )
        base = index_event_sql(chosen)
        if self.name == "icu_day":
            return _expansion_sql(
                base, index_name="day_index", seconds=SECONDS_PER_DAY, interval_fn="to_days"
            )
        if self.name == "hour_bin":
            return _expansion_sql(
                base, index_name="hour_bin", seconds=SECONDS_PER_HOUR, interval_fn="to_hours"
            )
        return base


SUBJECT = Grain(
    "subject",
    ("subject_id",),
    "mimiciv_hosp.patients",
    None,
    ("first_hadm", "first_icu_stay"),
    "first_hadm",
    "EP-21",
    description="one row per patient; index events come from the rule (first admission "
    "or first ICU stay)",
)
HADM = Grain(
    "hadm",
    ("hadm_id",),
    "mimiciv_hosp.admissions",
    "admittime",
    ("each_hadm", "first_hadm"),
    "each_hadm",
    "EP-21",
    description="one row per hospital admission",
)
ICUSTAY = Grain(
    "icustay",
    ("stay_id",),
    "mimiciv_icu.icustays",
    "intime",
    ("each_icustay", "first_icu_stay", "first_icu_stay_of_first_hadm"),
    "each_icustay",
    "EP-21",
    description="one row per ICU stay",
)
ICU_DAY = Grain(
    "icu_day",
    ("stay_id", "day_index"),
    "mimiciv_icu.icustays",
    "intime",
    ("each_icustay", "first_icu_stay", "first_icu_stay_of_first_hadm"),
    "each_icustay",
    "EP-34",
    description="ICU stay x day index from intime ([start, end) days, day 0 first)",
)
HOUR_BIN = Grain(
    "hour_bin",
    ("stay_id", "hour_bin"),
    "mimiciv_icu.icustays",
    "intime",
    ("each_icustay", "first_icu_stay", "first_icu_stay_of_first_hadm"),
    "each_icustay",
    "EP-34",
    description="ICU stay x hour index from intime ([start, end) hours, bin 0 first)",
)
PERSON_TIME = Grain(
    "person_time",
    ("subject_id", "interval_start", "interval_end"),
    "mimiciv_hosp.patients",
    None,
    (),
    None,
    "EP-68",
    description="subject x [start, end) follow-up interval; intervals are built by the "
    "rates module",
)
EDSTAY = Grain(
    "edstay",
    ("stay_id",),
    "mimiciv_ed.edstays",
    "intime",
    (),
    None,
    "EP-142",
    available=False,
    description="one row per ED stay — placeholder until the ED linkage ships (P9)",
)
NOTE = Grain(
    "note",
    ("note_id",),
    "mimiciv_note",
    "charttime",
    (),
    None,
    "EP-148",
    available=False,
    description="one row per clinical note (segregated lake, owner-only) — placeholder "
    "until notes staging ships (P10)",
)
#: The registry, in the order ``meta.grains`` lists it.
GRAINS: dict[str, Grain] = {
    g.name: g for g in (SUBJECT, HADM, ICUSTAY, ICU_DAY, HOUR_BIN, PERSON_TIME, EDSTAY, NOTE)
}


def grain(name: str) -> Grain:
    """The registry entry for ``name`` (:class:`GrainError` when unknown)."""
    try:
        return GRAINS[name]
    except KeyError:
        raise GrainError(f"unknown grain {name!r}; expected one of {', '.join(GRAINS)}") from None


def available_grains() -> tuple[Grain, ...]:
    return tuple(g for g in GRAINS.values() if g.available)


# ---------------------------------------------------------------------------
# Catalog views + meta.grains (item 5) — the CATALOG_EXTENSIONS entry
# ---------------------------------------------------------------------------

HADM_ERA_VIEW = "mimiciv_derived.hadm_era"
ICUSTAY_INDEX_VIEW = "mimiciv_derived.icustay_index"
GRAINS_TABLE = "meta.grains"

_HADM_ERA_SOURCES: tuple[str, ...] = (
    "mimiciv_hosp.patients",
    "mimiciv_hosp.admissions",
    "mimiciv_hosp.diagnoses_icd",
)
_ICUSTAY_INDEX_SOURCES: tuple[str, ...] = ("mimiciv_icu.icustays",)


def hadm_era_view_sql() -> str:
    """The SELECT behind ``mimiciv_derived.hadm_era``: one row per admission with the
    era axis, the (capped) age at admission, the cap flag and the ICD classification."""
    age = sql_age_at("p.anchor_age", "p.anchor_year", "a.admittime")
    return (
        "SELECT\n"
        "    a.subject_id,\n"
        "    a.hadm_id,\n"
        "    p.anchor_year_group,\n"
        f"    {sql_era_index('p.anchor_year_group')} AS era_index,\n"
        f"    {sql_age_at('p.anchor_age', 'p.anchor_year', 'a.admittime', cap=True)}"
        " AS age_at_admit,\n"
        f"    {sql_is_age_capped(age)} AS age_capped,\n"
        "    d.icd_versions\n"
        "FROM mimiciv_hosp.admissions AS a\n"
        "JOIN mimiciv_hosp.patients AS p ON a.subject_id = p.subject_id\n"
        "LEFT JOIN (\n"
        f"    SELECT hadm_id, {sql_icd_versions('icd_version')} AS icd_versions\n"
        "    FROM mimiciv_hosp.diagnoses_icd\n"
        "    GROUP BY hadm_id\n"
        ") AS d ON a.hadm_id = d.hadm_id"
    )


def icustay_index_view_sql() -> str:
    """The SELECT behind ``mimiciv_derived.icustay_index``: every ICU stay with its
    sequence inside the admission and inside the subject (ordered ``intime, stay_id`` —
    the tracer's order) and the two first-stay flags."""
    return (
        "SELECT\n"
        "    stay_id,\n"
        "    hadm_id,\n"
        "    subject_id,\n"
        "    intime,\n"
        "    outtime,\n"
        "    icu_seq_in_hadm,\n"
        "    icu_seq_in_subject,\n"
        "    icu_seq_in_hadm = 1 AS first_icu_stay_in_hadm,\n"
        "    icu_seq_in_subject = 1 AS first_icu_stay_of_subject\n"
        "FROM (\n"
        "    SELECT\n"
        "        stay_id,\n"
        "        hadm_id,\n"
        "        subject_id,\n"
        "        intime,\n"
        "        outtime,\n"
        "        row_number() OVER (PARTITION BY hadm_id ORDER BY intime, stay_id)"
        " AS icu_seq_in_hadm,\n"
        "        row_number() OVER (PARTITION BY subject_id ORDER BY intime, stay_id)"
        " AS icu_seq_in_subject\n"
        "    FROM mimiciv_icu.icustays\n"
        ")"
    )


def _sql_str(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def _present_tables(con: duckdb.DuckDBPyConnection) -> set[str]:
    rows = con.execute(
        "SELECT table_schema || '.' || table_name FROM information_schema.tables "
        "WHERE table_schema IN ('mimiciv_hosp', 'mimiciv_icu')"
    ).fetchall()
    return {str(r[0]) for r in rows}


def _create_view(
    con: duckdb.DuckDBPyConnection, name: str, body: str, comment: str, sources: tuple[str, ...]
) -> bool:
    present = _present_tables(con)
    missing = [s for s in sources if s not in present]
    if missing:
        _LOG.warning("catalog extension: %s skipped — %s not cataloged", name, ", ".join(missing))
        return False
    con.execute(f"CREATE OR REPLACE VIEW {name} AS\n{body}")
    con.execute(f"COMMENT ON VIEW {name} IS {_sql_str(comment)}")
    return True


def create_views(con: duckdb.DuckDBPyConnection, tier: str) -> None:
    """The catalog extension (``CATALOG_EXTENSIONS`` entry): on the **build** connection
    of a tier catalog, create ``mimiciv_derived.hadm_era`` and
    ``mimiciv_derived.icustay_index`` (views only — cheap joins over the tier's own
    ``patients`` / ``admissions`` / ``diagnoses_icd`` / ``icustays`` views, so the dev
    bucket filter is inherited; a view whose sources are not cataloged is skipped with a
    warning, never created empty) and the ``meta.grains`` registry table. Never opens a
    connection of its own; writes schema DDL and registry text only."""
    con.execute("CREATE SCHEMA IF NOT EXISTS mimiciv_derived")
    con.execute("CREATE SCHEMA IF NOT EXISTS meta")
    _create_view(
        con,
        HADM_ERA_VIEW,
        hadm_era_view_sql(),
        "One row per admission: anchor_year_group + era_index (the only cross-patient "
        "time axis), age_at_admit (capped at 91) + age_capped, icd_versions "
        "(icd9 / icd10 / mixed from diagnoses_icd.icd_version) — timesem, EP-34.",
        _HADM_ERA_SOURCES,
    )
    _create_view(
        con,
        ICUSTAY_INDEX_VIEW,
        icustay_index_view_sql(),
        "One row per ICU stay: icu_seq_in_hadm / icu_seq_in_subject (ordered intime, "
        "stay_id) and the first_icu_stay_in_hadm / first_icu_stay_of_subject flags "
        "(the latter is the tracer's first_icu_stay rule) — timesem, EP-34.",
        _ICUSTAY_INDEX_SOURCES,
    )
    con.execute(f"DROP TABLE IF EXISTS {GRAINS_TABLE}")
    con.execute(
        f"CREATE TABLE {GRAINS_TABLE} (name VARCHAR, keys VARCHAR, source VARCHAR, "
        "time_anchor VARCHAR, default_index_rule VARCHAR, index_rules VARCHAR, "
        "available BOOLEAN, available_from VARCHAR, description VARCHAR)"
    )
    con.executemany(
        f"INSERT INTO {GRAINS_TABLE} VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            [
                g.name,
                g.keys_sql(),
                g.source,
                g.time_anchor,
                g.default_index_rule,
                ", ".join(g.index_rules),
                g.available,
                g.available_from,
                g.description,
            ]
            for g in GRAINS.values()
        ],
    )
    con.execute(
        f"COMMENT ON TABLE {GRAINS_TABLE} IS "
        + _sql_str(
            "The unit-of-analysis registry (timesem.GRAINS, EP-34): name, key columns, "
            "source, time anchor, index-event rules and availability; available = false "
            "marks a placeholder grain compilers must refuse."
        )
    )
    _LOG.info("catalog extension timesem: %s + %s objects on tier %s", "views", GRAINS_TABLE, tier)


# ---------------------------------------------------------------------------
# docs/methods/time-semantics.md — the generated blocks (item 6)
# ---------------------------------------------------------------------------

METHODS_DOC_RELPATH = Path("docs") / "methods" / "time-semantics.md"
ERAS_MARK = ("<!-- eras:begin -->", "<!-- eras:end -->")
GRAINS_MARK = ("<!-- grains:begin -->", "<!-- grains:end -->")
CENSORING_MARK = ("<!-- censoring:begin -->", "<!-- censoring:end -->")


def _md_table(header: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(lines) + "\n"


def render_era_table() -> str:
    """The era axis as a Markdown table (generated from :data:`ERA_TABLE`)."""
    return _md_table(
        ["era_index", "anchor_year_group", "years"],
        [[str(e.index), f"`{e.label}`", f"{e.start_year}-{e.end_year}"] for e in ERA_TABLE],
    )


def render_grain_table() -> str:
    """The registry as a Markdown table (generated from :data:`GRAINS`)."""
    rows = []
    for g in GRAINS.values():
        rules = (
            ", ".join(f"**{r}**" if r == g.default_index_rule else r for r in g.index_rules) or "-"
        )
        available = "yes" if g.available else f"no (placeholder until {g.available_from})"
        rows.append(
            [
                f"`{g.name}`",
                ", ".join(f"`{k}`" for k in g.keys),
                f"`{g.source}`",
                f"`{g.time_anchor}`" if g.time_anchor else "-",
                rules,
                available,
                g.description,
            ]
        )
    return _md_table(
        [
            "grain",
            "keys",
            "source",
            "time anchor",
            "index rules (default in bold)",
            "available",
            "what a row is",
        ],
        rows,
    )


def render_censoring_table() -> str:
    """The default censoring rules as a Markdown table (from :data:`CENSORING_RULES`)."""
    rows = []
    for rule in CENSORING_RULES.values():
        horizon = (
            "none (resolved at discharge)"
            if rule.horizon_days is None
            else (
                f"{rule.horizon_days} d after `{rule.anchor}`, censored at "
                f"min(anchor + horizon, last_dischtime + {DOD_VISIBILITY_DAYS} d)"
            )
        )
        competing = ", ".join(f"`{c}`" for c in rule.competing_events) or "-"
        rows.append([f"`{rule.outcome}`", f"`{rule.anchor}`", horizon, competing])
    return _md_table(["outcome", "anchor", "horizon / censoring", "competing events"], rows)


def methods_doc_path() -> Path:
    """``mimicwarehouse/docs/methods/time-semantics.md``."""
    from mimicwarehouse.config import workspace_root

    return workspace_root() / METHODS_DOC_RELPATH


def sync_methods_doc(path: Path | None = None) -> Path:
    """Re-render the three generated blocks of the methods page in place (idempotent;
    ``python -m mimicwarehouse.timesem`` runs it; ``test_ep34`` asserts the page is in
    sync). The narrative around the markers is never touched."""
    from mimicwarehouse.dag.benchmarks import replace_marked_block

    target = Path(path) if path is not None else methods_doc_path()
    text = target.read_text(encoding="utf-8")
    for (begin, end), block in (
        (ERAS_MARK, render_era_table()),
        (GRAINS_MARK, render_grain_table()),
        (CENSORING_MARK, render_censoring_table()),
    ):
        text = replace_marked_block(text, block, begin=begin, end=end)
    target.write_text(text.rstrip("\n") + "\n", encoding="utf-8", newline="\n")
    return target


__all__ = [
    "AGE_BANDS",
    "AGE_CAP",
    "ANCHORS",
    "CENSORING_MARK",
    "CENSORING_RULES",
    "DISCHARGE_ALIVE",
    "DOD_VISIBILITY_DAYS",
    "EDSTAY",
    "ERAS",
    "ERAS_MARK",
    "ERA_TABLE",
    "GRAINS",
    "GRAINS_MARK",
    "GRAINS_TABLE",
    "HADM",
    "HADM_ERA_VIEW",
    "HOURS_BEFORE_DISCHARGE",
    "HOURS_SINCE_HOSP_ADMIT",
    "HOURS_SINCE_ICU_INTIME",
    "HOUR_BIN",
    "ICD_VERSION_LABELS",
    "ICUSTAY",
    "ICUSTAY_INDEX_VIEW",
    "ICU_DAY",
    "INDEX_RULES",
    "IN_HOSPITAL_MORTALITY",
    "METHODS_DOC_RELPATH",
    "MORTALITY_1Y",
    "MORTALITY_30D",
    "MORTALITY_90D",
    "NOTE",
    "PERSON_TIME",
    "RELATIVE_TIMES",
    "SECONDS_PER_DAY",
    "SECONDS_PER_HOUR",
    "SUBJECT",
    "CensoringRule",
    "Era",
    "Grain",
    "GrainError",
    "GrainUnavailableError",
    "RelativeTime",
    "UnknownEraError",
    "UnknownIndexRuleError",
    "age_at",
    "age_band",
    "available_grains",
    "bin_bounds",
    "create_views",
    "days_since",
    "dod_visibility_end",
    "era_of",
    "follow_up_end",
    "grain",
    "hadm_era_view_sql",
    "hour_bin",
    "hours_since",
    "icd_versions_of_hadm",
    "icustay_index_view_sql",
    "index_event_sql",
    "is_age_capped",
    "methods_doc_path",
    "render_censoring_table",
    "render_era_table",
    "render_grain_table",
    "seconds_between",
    "sql_age_at",
    "sql_age_band",
    "sql_days_since",
    "sql_dod_visibility_end",
    "sql_era_index",
    "sql_follow_up_end",
    "sql_hour_bin",
    "sql_hours_since",
    "sql_icd_versions",
    "sql_is_age_capped",
    "sync_methods_doc",
]


if __name__ == "__main__":
    print(sync_methods_doc())
