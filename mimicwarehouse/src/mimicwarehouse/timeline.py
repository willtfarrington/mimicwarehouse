"""Event-aligned timeline API (EP-49; DESIGN §15; GOVERNANCE §5/§6).

Capability 8 — "take events from table X, align them to anchor A per unit, keep the window
``[-a, +b)`` hours, bin, join as-of" — written once so trajectories (EP-82), exposure-
response (EP-86), the hourly marts (EP-55/56) and the owner-only timeline viewer (EP-67)
import it. Everything is built on :mod:`mimicwarehouse.timesem` (EP-34: relative time is
``date_diff('second', anchor, event) / 3600.0``, bins are ``[start, end)``, grains come from
the unit-of-analysis registry) and returns **SQL text** first (``*_sql`` builders, pure
functions of their arguments — the cohort compiler's discipline) and lazy DuckDB relations
second (``con.sql(text)``), so a run can record the statement it executed.

The four layers:

1. **Anchors** — :class:`Anchor` + :data:`ANCHORS` (``hosp_admit`` … ``deterioration``) and
   the parameterised factories :func:`med_start`, :func:`procedure`, :func:`vent_start`,
   :func:`phenotype_onset`, :func:`custom_anchor`. :func:`anchor_sql` yields ``(grain keys,
   anchor_time)`` for every unit of the grain — a unit without an anchor event carries a
   NULL ``anchor_time`` (documented; consumers decide), ``selector`` picks the ``first`` /
   ``last`` event per unit or keeps ``each``.
2. **Alignment + windows** — :class:`EventSource` presets (:func:`labs`, :func:`vitals`,
   :func:`inputs`, :func:`outputs`, :func:`meds`, :func:`procedures`, :func:`micro`,
   :func:`transfers`, :func:`custom_source`), :func:`align` / :func:`align_sql` →
   ``(grain keys, anchor_time, event_time, hours_since_anchor, code, value, valueuom,
   source_table)``, :func:`window_join`, :func:`asof_join` (DuckDB ``ASOF JOIN``),
   :func:`event_at` (the last value before the anchor within a tolerance).
3. **Binning + aggregation** — :func:`hourly_bins` / :func:`daily_bins` (``[start, end)``
   bins over ``hours_since_anchor`` via :func:`timesem.sql_hour_bin`; ``fill=True`` adds the
   empty bins with ``n = 0``), :func:`population_summary` (per-bin counts and means across
   units, released through the ``safe.SUPPRESSOR`` seam — the only frame shape a session
   prints), :func:`to_mart` (Parquet through the rename-aside publisher).
4. **The owner-only single-stay path** — :func:`stay_events` returns row-level lane data
   for EP-67 and is reachable only through a connection opened as
   ``open_catalog(tier, role="owner")`` (the role is stamped on the connection as the
   session variable ``mwh_role``; any other connection raises ``PermissionError`` — in a
   Claude session ``MWH_ROLE`` is unset, so the gate is closed by construction, D-32); every
   call writes one ``row_view:`` audit line through the EP-30 seam without recording the
   row selection itself. Tested on the fixture tier only.

The full-tier benchmark (:func:`run_benchmark`, ``mwh timeline bench``): five curated lab
itemids aligned to ``icu_in`` over ``[-6, 48)`` hours → hourly bins → the suppressed
population summary, inside ``run.start(kind="bench")`` with a ``kind: query`` benchmark
line, the suppressed frame saved under the run and the disclosure-gated exports
(``exports/timeline_labs_icu_in_48h.{parquet,md}``, ``population_band.{png,csv}``) with
their ``.disclosure.json`` sidecars. Full tier runs only as a background job.

Governance: the API never prints a row; ``align`` and friends are lazy relations a caller
aggregates before anything leaves the process; ``population_summary`` is the released
shape; ``stay_events`` is the one row-level function and carries the gate above.

Import budget: this module sits behind ``mwh timeline`` (imported lazily by ``cli.py``);
DuckDB, polars, altair, the registries and ``run`` load inside function bodies.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import time
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import typer

from mimicwarehouse import timesem
from mimicwarehouse.config import Settings, get_settings

if TYPE_CHECKING:  # pragma: no cover
    import duckdb
    import polars

    from mimicwarehouse.run import Run

_LOG = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

Key = Literal["subject_id", "hadm_id", "stay_id"]
Selector = Literal["first", "last", "each"]
Direction = Literal["backward", "forward"]

KEYS: tuple[str, ...] = ("subject_id", "hadm_id", "stay_id")
SELECTORS: tuple[str, ...] = ("first", "last", "each")
#: The grains the API aligns on (``icu_day`` / ``hour_bin`` bin themselves here; the
#: placeholders are refused by the registry).
TIMELINE_GRAINS: tuple[str, ...] = ("icustay", "hadm", "subject")
#: The aligned relation's column order after the grain keys.
ALIGNED_COLUMNS: tuple[str, ...] = (
    "anchor_time",
    "event_time",
    "hours_since_anchor",
    "code",
    "value",
    "valueuom",
    "source_table",
)
#: ``code`` / ``valueuom`` are VARCHAR and cut to this length so a subject-keyed read
#: through ``safe_query`` never trips the free-text heuristic (P3C-5; mirrors
#: ``safe.FREE_TEXT_MAX_CHARS``, asserted equal by ``test_ep49``).
CODE_MAX_CHARS = 64
#: The aggregations :func:`hourly_bins` knows (name -> SQL over ``<col>``, ``event_time``).
AGGREGATIONS: dict[str, str] = {
    "count": "count({col})",
    "mean": "avg({col})",
    "min": "min({col})",
    "max": "max({col})",
    "sum": "sum({col})",
    "median": "median({col})",
    "first": "arg_min({col}, event_time)",
    "last": "arg_max({col}, event_time)",
}
DEFAULT_AGGS: dict[str, tuple[str, ...]] = {"value": ("count", "mean", "min", "max", "last")}
#: The session variable :func:`mimicwarehouse.catalog.connect.open_catalog` stamps.
ROLE_VARIABLE = "mwh_role"
OWNER_ROLE = "owner"
#: The ``statement_sha256`` / ``sql_text`` prefix of a row-view audit line (EP-33
#: amendment 1): marks the event in ``runs.audit`` without recording the row selection.
ROW_VIEW_PREFIX = "row_view:"

#: Ventilation statuses of ``mimiciv_derived.ventilation`` (mimic-code); the default
#: ``kinds`` of :func:`vent_start` are the two mechanical-support kinds.
VENT_KINDS: tuple[str, ...] = (
    "InvasiveVent",
    "Tracheostomy",
    "NonInvasiveVent",
    "HFNC",
    "SupplementalOxygen",
    "None",
)
DEFAULT_VENT_KINDS: tuple[str, ...] = ("InvasiveVent", "NonInvasiveVent")
#: The vasopressor columns of ``mimiciv_derived.vasoactive_agent`` (the inodilators
#: dobutamine / milrinone are not "deterioration").
VASOPRESSOR_COLUMNS: tuple[str, ...] = (
    "norepinephrine",
    "epinephrine",
    "phenylephrine",
    "vasopressin",
    "dopamine",
)

# The full-tier benchmark (brief item 5)
BENCH_NAME = "timeline_labs_icu_in_48h"
BENCH_FIGURE = "population_band"
BENCH_KIND = "query"
RUN_KIND = "bench"
RUN_NAME = "timeline bench"
#: Five curated lab itemids (``units.py`` catalogue): creatinine, lactate, potassium,
#: hemoglobin, white blood cells.
BENCH_ITEMIDS: tuple[int, ...] = (50912, 50813, 50971, 51222, 51301)
BENCH_WINDOW: tuple[float, float] = (-6.0, 48.0)
BENCH_ANCHOR = "icu_in"
EXPORTS_DIRNAME = "exports"
CLAIM_TYPE = "exploratory"
RETROSPECTIVE_SENTENCE = "MIMIC-IV analyses are retrospective."
PNG_SCALE = 2
CHART_WIDTH = 520
CHART_HEIGHT = 140

METHODS_DOC_RELPATH = Path("docs") / "methods" / "timelines.md"
ANCHORS_MARK = ("<!-- anchors:begin -->", "<!-- anchors:end -->")
SOURCES_MARK = ("<!-- sources:begin -->", "<!-- sources:end -->")


class TimelineError(ValueError):
    """A request the timeline API refuses (unknown anchor / grain / aggregation, a bad
    window, a source the grain cannot key)."""


# ---------------------------------------------------------------------------
# SQL helpers
# ---------------------------------------------------------------------------


def _sql_str(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def _int_list(values: Iterable[int]) -> str:
    ids = sorted({int(v) for v in values})
    if not ids:
        raise TimelineError("an itemid list must not be empty")
    return ", ".join(str(v) for v in ids)


def _indent(text: str, n: int = 4) -> str:
    pad = " " * n
    return "\n".join(pad + line if line else line for line in text.splitlines())


def _subquery(sql: str, alias: str) -> str:
    return f"(\n{_indent(sql.rstrip())}\n) AS {alias}"


def _require_grain(grain: str) -> timesem.Grain:
    g = timesem.grain(grain)  # GrainError for an unknown name
    if not g.available:
        raise timesem.GrainUnavailableError(
            f"grain {grain!r} is a placeholder until {g.available_from}"
        )
    if grain not in TIMELINE_GRAINS:
        raise TimelineError(
            f"grain {grain!r} is not a timeline grain; expected one of "
            f"{', '.join(TIMELINE_GRAINS)} (the API bins itself)"
        )
    return g


def _require_window(window: Sequence[float]) -> tuple[float, float]:
    if len(window) != 2:
        raise TimelineError(f"window must be (start_h, end_h), got {window!r}")
    start, end = float(window[0]), float(window[1])
    if not (math.isfinite(start) and math.isfinite(end)) or start >= end:
        raise TimelineError(f"window must satisfy start_h < end_h (finite), got {window!r}")
    return start, end


def _join_key(source_key: str, grain: str) -> str:
    """The key an anchor / event relation joins the grain's units on: the finest key both
    sides carry (a stay-keyed source on the ``hadm`` grain joins on ``hadm_id`` because
    every stay-keyed relation here also carries ``hadm_id``; a subject-keyed source
    joins on ``subject_id`` whatever the grain)."""
    if grain == "subject" or source_key == "subject_id":
        return "subject_id"
    if grain == "hadm" or source_key == "hadm_id":
        return "hadm_id"
    return "stay_id"


def _grain_keys(grain: str) -> tuple[str, ...]:
    return timesem.grain(grain).keys


def _units_sql(grain: str) -> str:
    """Every unit of ``grain`` with the three keys (NULL where not applicable) and its
    own ``[unit_start, unit_end)`` window (NULL for subjects)."""
    if grain == "icustay":
        return (
            "SELECT subject_id, hadm_id, stay_id, intime AS unit_start, outtime AS unit_end\n"
            "FROM mimiciv_icu.icustays"
        )
    if grain == "hadm":
        return (
            "SELECT subject_id, hadm_id, CAST(NULL AS INTEGER) AS stay_id, "
            "admittime AS unit_start, dischtime AS unit_end\n"
            "FROM mimiciv_hosp.admissions"
        )
    return (
        "SELECT subject_id, CAST(NULL AS INTEGER) AS hadm_id, CAST(NULL AS INTEGER) AS stay_id, "
        "CAST(NULL AS TIMESTAMP) AS unit_start, CAST(NULL AS TIMESTAMP) AS unit_end\n"
        "FROM mimiciv_hosp.patients"
    )


# ---------------------------------------------------------------------------
# 1. Anchors
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Anchor:
    """One anchor definition (module docstring, layer 1).

    ``source`` / ``time_column`` name the table or view and its timestamp; ``key`` is the
    finest identifier the source carries (``stay_id`` sources also carry ``hadm_id`` /
    ``subject_id`` unless ``join_stays`` says the source has ``stay_id`` only, in which case
    ``mimiciv_icu.icustays`` supplies the other two); ``filter`` is a predicate over the
    source alias ``s``; ``selector`` picks the first / last event per unit or keeps each;
    ``params`` are the rendered factory arguments (docs / provenance); ``sql`` replaces the
    source entirely (``custom``: a SELECT yielding ``subject_id, hadm_id, stay_id,
    anchor_time``)."""

    name: str
    grain: str
    source: str
    time_column: str
    key: Key = "hadm_id"
    selector: Selector = "first"
    filter: str | None = None
    join_stays: bool = False
    params: tuple[tuple[str, str], ...] = ()
    description: str = ""
    sql: str | None = None
    #: A short rendering of ``filter`` for the docs table when the predicate is long.
    filter_label: str | None = None

    def __post_init__(self) -> None:
        if self.key not in KEYS:
            raise TimelineError(f"anchor {self.name!r}: key must be one of {', '.join(KEYS)}")
        if self.selector not in SELECTORS:
            raise TimelineError(
                f"anchor {self.name!r}: selector must be one of {', '.join(SELECTORS)}"
            )
        if self.grain not in TIMELINE_GRAINS:
            raise TimelineError(
                f"anchor {self.name!r}: grain must be one of {', '.join(TIMELINE_GRAINS)}"
            )

    @property
    def label(self) -> str:
        """``name`` plus the rendered parameters (``med_start(codeset=…, source=…)``)."""
        if not self.params:
            return self.name
        return f"{self.name}({', '.join(f'{k}={v}' for k, v in self.params)})"

    def events_sql(self) -> str:
        """The normalised anchor events: ``subject_id, hadm_id, stay_id, anchor_time``
        (NULL keys where the source has none; rows without a time or without the key are
        dropped)."""
        if self.sql is not None:
            return (
                "SELECT subject_id, hadm_id, stay_id, anchor_time\n"
                f"FROM {_subquery(self.sql, 'c')}\n"
                "WHERE anchor_time IS NOT NULL"
            )
        t = f"s.{self.time_column}"
        where = [f"{t} IS NOT NULL", f"s.{self.key} IS NOT NULL"]
        if self.filter:
            where.append(f"({self.filter})")
        if self.join_stays:
            return (
                f"SELECT i.subject_id, i.hadm_id, s.stay_id, {t} AS anchor_time\n"
                f"FROM {self.source} AS s\n"
                "JOIN mimiciv_icu.icustays AS i ON i.stay_id = s.stay_id\n"
                f"WHERE {' AND '.join(where)}"
            )
        if self.key == "stay_id":
            cols = "s.subject_id, s.hadm_id, s.stay_id"
        elif self.key == "hadm_id":
            cols = "s.subject_id, s.hadm_id, CAST(NULL AS INTEGER) AS stay_id"
        else:
            cols = (
                "s.subject_id, CAST(NULL AS INTEGER) AS hadm_id, CAST(NULL AS INTEGER) AS stay_id"
            )
        return (
            f"SELECT {cols}, {t} AS anchor_time\nFROM {self.source} AS s\n"
            f"WHERE {' AND '.join(where)}"
        )


HOSP_ADMIT = Anchor(
    "hosp_admit",
    "hadm",
    "mimiciv_hosp.admissions",
    "admittime",
    key="hadm_id",
    description="hospital admission time",
)
HOSP_DISCHARGE = Anchor(
    "hosp_discharge",
    "hadm",
    "mimiciv_hosp.admissions",
    "dischtime",
    key="hadm_id",
    description="hospital discharge time",
)
ICU_IN = Anchor(
    "icu_in",
    "icustay",
    "mimiciv_icu.icustays",
    "intime",
    key="stay_id",
    description="ICU admission (the hours_since_icu_intime axis)",
)
ICU_OUT = Anchor(
    "icu_out",
    "icustay",
    "mimiciv_icu.icustays",
    "outtime",
    key="stay_id",
    description="ICU discharge",
)
FIRST_CULTURE = Anchor(
    "first_culture",
    "hadm",
    "mimiciv_hosp.microbiologyevents",
    "charttime",
    key="hadm_id",
    description="first microbiology specimen of the admission",
)
FIRST_ANTIBIOTIC = Anchor(
    "first_antibiotic",
    "hadm",
    "mimiciv_derived.antibiotic",
    "starttime",
    key="hadm_id",
    description="first antibiotic start of the admission (mimic-code antibiotic)",
)
SUSPECTED_INFECTION = Anchor(
    "suspected_infection",
    "hadm",
    "mimiciv_derived.suspicion_of_infection",
    "suspected_infection_time",
    key="hadm_id",
    filter="s.suspected_infection = 1",
    description="first suspected-infection time of the admission (Sepsis-3)",
)
VENT_START = Anchor(
    "vent_start",
    "icustay",
    "mimiciv_derived.ventilation",
    "starttime",
    key="stay_id",
    join_stays=True,
    filter="s.ventilation_status IN ('InvasiveVent', 'NonInvasiveVent')",
    params=(("kinds", "InvasiveVent,NonInvasiveVent"),),
    description="first ventilation episode of the stay of the given kinds",
)
DETERIORATION = Anchor(
    "deterioration",
    "icustay",
    "mimiciv_derived.vasoactive_agent",
    "starttime",
    key="stay_id",
    join_stays=True,
    filter=" OR ".join(f"s.{c} IS NOT NULL" for c in VASOPRESSOR_COLUMNS),
    filter_label="any vasopressor rate column IS NOT NULL",
    description="first vasopressor start of the stay (inodilators excluded)",
)

#: The fixed anchor registry, in documentation order; the parameterised factories below
#: (``med_start``, ``procedure``, ``vent_start``, ``phenotype_onset``, ``custom_anchor``)
#: are listed by :func:`anchor_table_rows` beside them.
ANCHORS: dict[str, Anchor] = {
    a.name: a
    for a in (
        HOSP_ADMIT,
        HOSP_DISCHARGE,
        ICU_IN,
        ICU_OUT,
        FIRST_CULTURE,
        FIRST_ANTIBIOTIC,
        SUSPECTED_INFECTION,
        VENT_START,
        DETERIORATION,
    )
}


def anchor(name: str) -> Anchor:
    """The registry entry for ``name`` (:class:`TimelineError` when unknown)."""
    try:
        return ANCHORS[name]
    except KeyError:
        raise TimelineError(
            f"unknown anchor {name!r}; expected one of {', '.join(ANCHORS)} or a factory "
            "(med_start, procedure, vent_start, phenotype_onset, custom_anchor)"
        ) from None


def _codeset_members(ref: str) -> Any:
    from mimicwarehouse.codesets.registry import load_registry

    entry = load_registry().get(ref)
    if entry.codeset.kind != "drug":
        raise TimelineError(
            f"{ref} is a {entry.codeset.kind} set; med_start / meds need a drug set"
        )
    return entry.codeset.members


def _drug_filter(ref: str, source: str, alias: str) -> tuple[str, str]:
    """``(table, predicate)`` of a drug code set over ``source`` (prescriptions by drug
    name through the phenotype compiler's ``drug_predicate``; inputevents by itemid)."""
    from mimicwarehouse.phenotypes.compiler import drug_predicate

    members = _codeset_members(ref)
    if source == "inputevents":
        if not members.itemids:
            raise TimelineError(f"{ref} carries no ICU itemids; source inputevents needs them")
        return "mimiciv_icu.inputevents", f"{alias}.itemid IN ({_int_list(members.itemids)})"
    if source != "prescriptions":
        raise TimelineError(f"source must be prescriptions or inputevents, got {source!r}")
    if members.drugs is None:
        raise TimelineError(f"{ref} carries no drug names; source prescriptions needs them")
    return "mimiciv_hosp.prescriptions", drug_predicate(f"{alias}.drug", members.drugs)


def med_start(codeset: str, source: str = "prescriptions", selector: Selector = "first") -> Anchor:
    """First (or last / each) start of a drug of ``codeset`` (``id@version``, kind
    ``drug``): ``prescriptions.starttime`` matched by drug name, or
    ``inputevents.starttime`` by the set's ICU itemids."""
    table, predicate = _drug_filter(codeset, source, "s")
    key: Key = "stay_id" if source == "inputevents" else "hadm_id"
    return Anchor(
        "med_start",
        "icustay" if key == "stay_id" else "hadm",
        table,
        "starttime",
        key=key,
        selector=selector,
        filter=predicate,
        params=(("codeset", codeset), ("source", source)),
        description="first start of a drug of the code set",
    )


def procedure(itemids: Iterable[int], selector: Selector = "first") -> Anchor:
    """First (or last / each) ``procedureevents.starttime`` of the given itemids."""
    ids = _int_list(itemids)
    return Anchor(
        "procedure",
        "icustay",
        "mimiciv_icu.procedureevents",
        "starttime",
        key="stay_id",
        selector=selector,
        filter=f"s.itemid IN ({ids})",
        params=(("itemids", ids.replace(", ", ",")),),
        description="first procedure of the itemids",
    )


def vent_start(kinds: Iterable[str] = DEFAULT_VENT_KINDS, selector: Selector = "first") -> Anchor:
    """First ventilation episode of the stay whose status is one of ``kinds``
    (:data:`VENT_KINDS`)."""
    chosen = tuple(dict.fromkeys(kinds))
    unknown = [k for k in chosen if k not in VENT_KINDS]
    if unknown or not chosen:
        raise TimelineError(f"vent_start kinds must be from {', '.join(VENT_KINDS)}; got {chosen}")
    return replace(
        VENT_START,
        selector=selector,
        filter="s.ventilation_status IN (" + ", ".join(_sql_str(k) for k in chosen) + ")",
        params=(("kinds", ",".join(chosen)),),
    )


def phenotype_onset(ref: str, selector: Selector = "first") -> Anchor:
    """The ``onset_time`` of phenotype ``id@version`` (EP-41) from the catalog view
    ``mimiciv_derived.phenotype_<id>`` — the latest **built** version of the id on the
    tier; the requested version is recorded in the anchor's params and checked against
    ``meta.phenotype_versions`` by the caller when it matters (``mwh phenotype``)."""
    from mimicwarehouse.phenotypes.registry import load_registry

    entry = load_registry().get(ref)
    grain = entry.phenotype.grain
    key: Key = "subject_id" if grain == "subject" else ("hadm_id" if grain == "hadm" else "stay_id")
    view = f"mimiciv_derived.phenotype_{entry.phenotype.id}"
    return Anchor(
        "phenotype_onset",
        grain if grain in TIMELINE_GRAINS else "icustay",
        view,
        "onset_time",
        key=key,
        selector=selector,
        filter="s.flag",
        params=(("phenotype", ref),),
        description="phenotype onset time (flagged units only)",
    )


def custom_anchor(
    name: str, sql: str, *, key: Key = "hadm_id", grain: str = "hadm", selector: Selector = "first"
) -> Anchor:
    """An anchor from your own SELECT yielding ``subject_id, hadm_id, stay_id,
    anchor_time`` (NULL keys where not applicable); ``key`` says which one is populated."""
    if not sql.strip():
        raise TimelineError("custom_anchor needs a SELECT")
    return Anchor(
        name,
        grain,
        "(custom)",
        "anchor_time",
        key=key,
        selector=selector,
        params=(("sql_sha256", hashlib.sha256(sql.encode("utf-8")).hexdigest()[:12]),),
        description="custom SQL anchor",
        sql=sql,
    )


def _anchored_sql(anchor: Anchor, grain: str) -> str:
    """Every unit of ``grain`` with its window and its anchor(s): ``subject_id, hadm_id,
    stay_id, unit_start, unit_end, anchor_time`` (NULL anchor_time = no event)."""
    _require_grain(grain)
    join = _join_key(anchor.key, grain)
    order = "anchor_time" + (" DESC" if anchor.selector == "last" else "")
    tiebreak = ", stay_id, hadm_id, subject_id"
    if anchor.selector == "each":
        picked = anchor.events_sql()
    else:
        picked = (
            "SELECT subject_id, hadm_id, stay_id, anchor_time\n"
            "FROM (\n"
            "    SELECT subject_id, hadm_id, stay_id, anchor_time,\n"
            f"           row_number() OVER (PARTITION BY {join} ORDER BY {order}{tiebreak})"
            " AS seq\n"
            f"    FROM {_subquery(anchor.events_sql(), 'ev')}\n"
            ")\nWHERE seq = 1"
        )
    return (
        "SELECT u.subject_id, u.hadm_id, u.stay_id, u.unit_start, u.unit_end, a.anchor_time\n"
        f"FROM {_subquery(_units_sql(grain), 'u')}\n"
        f"LEFT JOIN {_subquery(picked, 'a')} ON a.{join} = u.{join}"
    )


def anchor_sql(anchor: Anchor, grain: str = "icustay") -> str:
    """``(grain keys, anchor_time)`` for every unit of ``grain`` (module docstring; a
    unit without an anchor event has ``anchor_time`` NULL; ``selector="each"`` yields one
    row per event)."""
    keys = ", ".join(_grain_keys(grain))
    return f"SELECT {keys}, anchor_time\nFROM {_subquery(_anchored_sql(anchor, grain), 'x')}"


# ---------------------------------------------------------------------------
# 2. Event sources + alignment
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EventSource:
    """One event table as the aligner reads it: ``table`` / ``time_column``, the finest
    ``key`` it reliably carries (``labevents`` is subject-keyed: its ``hadm_id`` is often
    NULL), whether it carries ``stay_id`` at all, and the ``code`` / ``value`` / ``unit``
    expressions over the alias ``e`` (``code`` and ``unit`` are VARCHAR cut to
    :data:`CODE_MAX_CHARS`, ``value`` DOUBLE). ``sql`` replaces the table (``custom``: a
    SELECT yielding ``subject_id, hadm_id, stay_id, event_time, code, value, valueuom``)."""

    name: str
    table: str
    time_column: str
    key: Key
    has_stay: bool
    code_expr: str
    value_expr: str
    unit_expr: str
    filter: str | None = None
    params: tuple[tuple[str, str], ...] = ()
    description: str = ""
    sql: str | None = None

    def __post_init__(self) -> None:
        if self.key not in KEYS:
            raise TimelineError(f"source {self.name!r}: key must be one of {', '.join(KEYS)}")

    @property
    def label(self) -> str:
        if not self.params:
            return self.name
        return f"{self.name}({', '.join(f'{k}={v}' for k, v in self.params)})"

    def events_sql(self) -> str:
        """The normalised events: ``subject_id, hadm_id, stay_id, event_time, code, value,
        valueuom`` (rows without a time or the key dropped)."""
        if self.sql is not None:
            return (
                "SELECT subject_id, hadm_id, stay_id, event_time, "
                f"left(CAST(code AS VARCHAR), {CODE_MAX_CHARS}) AS code, "
                "CAST(value AS DOUBLE) AS value, "
                f"left(CAST(valueuom AS VARCHAR), {CODE_MAX_CHARS}) AS valueuom\n"
                f"FROM {_subquery(self.sql, 'c')}\n"
                "WHERE event_time IS NOT NULL"
            )
        t = f"e.{self.time_column}"
        where = [f"{t} IS NOT NULL", f"e.{self.key} IS NOT NULL"]
        if self.filter:
            where.append(f"({self.filter})")
        stay = "e.stay_id" if self.has_stay else "CAST(NULL AS INTEGER) AS stay_id"
        return (
            f"SELECT e.subject_id, e.hadm_id, {stay}, {t} AS event_time, "
            f"left(CAST({self.code_expr} AS VARCHAR), {CODE_MAX_CHARS}) AS code, "
            f"CAST({self.value_expr} AS DOUBLE) AS value, "
            f"left(CAST({self.unit_expr} AS VARCHAR), {CODE_MAX_CHARS}) AS valueuom\n"
            f"FROM {self.table} AS e\n"
            f"WHERE {' AND '.join(where)}"
        )


def _itemid_source(
    name: str,
    table: str,
    time_column: str,
    key: Key,
    has_stay: bool,
    value_expr: str,
    unit_expr: str,
    itemids: Iterable[int],
    description: str,
) -> EventSource:
    ids = _int_list(itemids)
    return EventSource(
        name,
        table,
        time_column,
        key,
        has_stay,
        "e.itemid",
        value_expr,
        unit_expr,
        filter=f"e.itemid IN ({ids})",
        params=(("itemids", ids.replace(", ", ",")),),
        description=description,
    )


def labs(itemids: Iterable[int]) -> EventSource:
    """``labevents`` (subject-keyed; ``valuenum`` / ``valueuom``) for the itemids."""
    return _itemid_source(
        "labs",
        "mimiciv_hosp.labevents",
        "charttime",
        "subject_id",
        False,
        "e.valuenum",
        "e.valueuom",
        itemids,
        "laboratory results (labevents.valuenum; subject-keyed, clipped to the unit window)",
    )


def vitals(itemids: Iterable[int]) -> EventSource:
    """``chartevents`` (stay-keyed; ``valuenum`` / ``valueuom``) for the itemids."""
    return _itemid_source(
        "vitals",
        "mimiciv_icu.chartevents",
        "charttime",
        "stay_id",
        True,
        "e.valuenum",
        "e.valueuom",
        itemids,
        "charted observations (chartevents.valuenum)",
    )


def inputs(itemids: Iterable[int]) -> EventSource:
    """``inputevents`` (stay-keyed; ``amount`` / ``amountuom`` at ``starttime``)."""
    return _itemid_source(
        "inputs",
        "mimiciv_icu.inputevents",
        "starttime",
        "stay_id",
        True,
        "e.amount",
        "e.amountuom",
        itemids,
        "infusions and boluses (inputevents.amount at starttime)",
    )


def outputs(itemids: Iterable[int]) -> EventSource:
    """``outputevents`` (stay-keyed; ``value`` / ``valueuom``)."""
    return _itemid_source(
        "outputs",
        "mimiciv_icu.outputevents",
        "charttime",
        "stay_id",
        True,
        "e.value",
        "e.valueuom",
        itemids,
        "outputs (outputevents.value)",
    )


def procedures(itemids: Iterable[int]) -> EventSource:
    """``procedureevents`` (stay-keyed; ``value`` / ``valueuom`` at ``starttime``)."""
    return _itemid_source(
        "procedures",
        "mimiciv_icu.procedureevents",
        "starttime",
        "stay_id",
        True,
        "e.value",
        "e.valueuom",
        itemids,
        "procedures (procedureevents at starttime)",
    )


def meds(codeset: str, source: str = "prescriptions") -> EventSource:
    """Drug starts of ``codeset`` (``id@version``, kind ``drug``): ``prescriptions``
    (code = drug name, value = ``dose_val_rx`` when numeric, unit = ``dose_unit_rx``) or
    ``inputevents`` (code = itemid, amount / amountuom)."""
    table, predicate = _drug_filter(codeset, source, "e")
    if source == "inputevents":
        return EventSource(
            "meds",
            table,
            "starttime",
            "stay_id",
            True,
            "e.itemid",
            "e.amount",
            "e.amountuom",
            filter=predicate,
            params=(("codeset", codeset), ("source", source)),
            description="drug starts of the code set (inputevents itemids)",
        )
    return EventSource(
        "meds",
        table,
        "starttime",
        "hadm_id",
        False,
        "e.drug",
        "TRY_CAST(e.dose_val_rx AS DOUBLE)",
        "e.dose_unit_rx",
        filter=predicate,
        params=(("codeset", codeset), ("source", source)),
        description="drug starts of the code set (prescriptions by drug name)",
    )


def micro() -> EventSource:
    """``microbiologyevents`` (admission-keyed; code = ``spec_itemid``, no value)."""
    return EventSource(
        "micro",
        "mimiciv_hosp.microbiologyevents",
        "charttime",
        "hadm_id",
        False,
        "e.spec_itemid",
        "NULL",
        "NULL",
        description="microbiology specimens (spec_itemid at charttime; undated rows dropped)",
    )


def transfers() -> EventSource:
    """``transfers`` (admission-keyed; code = ``careunit`` at ``intime``, no value)."""
    return EventSource(
        "transfers",
        "mimiciv_hosp.transfers",
        "intime",
        "hadm_id",
        False,
        "e.careunit",
        "NULL",
        "NULL",
        description="care-unit transfers (careunit at intime)",
    )


def custom_source(
    name: str, sql: str, *, key: Key = "stay_id", has_stay: bool = True
) -> EventSource:
    """Events from your own SELECT yielding ``subject_id, hadm_id, stay_id, event_time,
    code, value, valueuom``."""
    if not sql.strip():
        raise TimelineError("custom_source needs a SELECT")
    return EventSource(
        name,
        "(custom)",
        "event_time",
        key,
        has_stay,
        "code",
        "value",
        "valueuom",
        params=(("sql_sha256", hashlib.sha256(sql.encode("utf-8")).hexdigest()[:12]),),
        description="custom SQL source",
        sql=sql,
    )


#: The preset factories, in documentation order (``name`` -> ``(signature, description)``).
SOURCE_PRESETS: tuple[tuple[str, str, str, str], ...] = (
    (
        "labs",
        "labs(itemids)",
        "mimiciv_hosp.labevents",
        "charttime; subject-keyed; valuenum / valueuom",
    ),
    (
        "vitals",
        "vitals(itemids)",
        "mimiciv_icu.chartevents",
        "charttime; stay-keyed; valuenum / valueuom",
    ),
    (
        "inputs",
        "inputs(itemids)",
        "mimiciv_icu.inputevents",
        "starttime; stay-keyed; amount / amountuom",
    ),
    (
        "outputs",
        "outputs(itemids)",
        "mimiciv_icu.outputevents",
        "charttime; stay-keyed; value / valueuom",
    ),
    (
        "meds",
        "meds(codeset, source=prescriptions|inputevents)",
        "mimiciv_hosp.prescriptions or mimiciv_icu.inputevents",
        "starttime; admission/stay-keyed; drug or itemid, dose/amount",
    ),
    (
        "procedures",
        "procedures(itemids)",
        "mimiciv_icu.procedureevents",
        "starttime; stay-keyed; value / valueuom",
    ),
    (
        "micro",
        "micro()",
        "mimiciv_hosp.microbiologyevents",
        "charttime; admission-keyed; spec_itemid, no value",
    ),
    (
        "transfers",
        "transfers()",
        "mimiciv_hosp.transfers",
        "intime; admission-keyed; careunit, no value",
    ),
    (
        "custom",
        "custom_source(name, sql, key=, has_stay=)",
        "(your SELECT)",
        "subject_id, hadm_id, stay_id, event_time, code, value, valueuom",
    ),
)


def _align_sql(
    source: EventSource,
    anchor: Anchor,
    window: Sequence[float],
    grain: str,
    clip_to_stay: bool,
    *,
    end_inclusive: bool = False,
) -> str:
    start, end = _require_window(window)
    _require_grain(grain)
    if clip_to_stay and grain == "subject":
        raise TimelineError("clip_to_stay needs a unit window; the subject grain has none")
    join = _join_key(source.key, grain)
    keys = ", ".join(f"x.{k}" for k in _grain_keys(grain))
    hours = timesem.sql_hours_since("x.anchor_time", "e.event_time")
    where = [
        "x.anchor_time IS NOT NULL",
        f"{hours} >= {start!r}",
        f"{hours} {'<=' if end_inclusive else '<'} {end!r}",
    ]
    if clip_to_stay:
        where.append("e.event_time >= x.unit_start AND e.event_time < x.unit_end")
    table = source.table if source.sql is None else f"custom:{source.name}"
    return (
        f"SELECT {keys}, x.anchor_time, e.event_time, {hours} AS hours_since_anchor, "
        f"e.code, e.value, e.valueuom, {_sql_str(table)} AS source_table\n"
        f"FROM {_subquery(_anchored_sql(anchor, grain), 'x')}\n"
        f"JOIN {_subquery(source.events_sql(), 'e')} ON e.{join} = x.{join}\n"
        f"WHERE {' AND '.join(where)}"
    )


def align_sql(
    source: EventSource,
    anchor: Anchor,
    window: Sequence[float] = (-24, 72),
    grain: str = "icustay",
    clip_to_stay: bool = True,
) -> str:
    """The aligned relation as SQL: ``(grain keys, anchor_time, event_time,
    hours_since_anchor, code, value, valueuom, source_table)`` for every event of
    ``source`` whose signed hours from the unit's anchor lie in ``[window[0], window[1])``;
    units without an anchor contribute nothing; ``clip_to_stay`` additionally keeps only
    events inside the unit's own ``[unit_start, unit_end)`` (refused on the ``subject``
    grain, which has no window). Events join the units on the finest shared key
    (:func:`_join_key`): a subject-keyed source on the ``icustay`` grain reaches every stay
    of the subject and the window / clip decide which stays keep the event."""
    return _align_sql(source, anchor, window, grain, clip_to_stay)


def align(
    source: EventSource,
    anchor: Anchor,
    window: Sequence[float] = (-24, 72),
    grain: str = "icustay",
    clip_to_stay: bool = True,
    *,
    con: duckdb.DuckDBPyConnection,
) -> duckdb.DuckDBPyRelation:
    """:func:`align_sql` as a lazy relation on ``con`` (nothing is read until a consumer
    aggregates it; ``rel.sql_query()`` is the statement a run records)."""
    return con.sql(align_sql(source, anchor, window, grain, clip_to_stay))


def _rel_sql(rel: Any) -> str:
    """The SQL of a relation or the text itself."""
    if isinstance(rel, str):
        return rel
    return str(rel.sql_query())


def window_join_sql(
    left: Any,
    right: Any,
    by: Sequence[str],
    on: str | tuple[str, str],
    before_h: float,
    after_h: float,
) -> str:
    """Every ``right`` row whose time lies in ``[left.on - before_h, left.on + after_h)``
    of a ``left`` row with the same ``by`` keys: ``l.*`` plus the right columns (the
    right's ``by`` keys dropped, its time column kept) and ``hours_since`` (right - left)."""
    left_on, right_on = (on, on) if isinstance(on, str) else on
    if not by:
        raise TimelineError("window_join needs at least one by column")
    if before_h < 0 or after_h < 0:
        raise TimelineError("before_h / after_h must be >= 0")
    hours = timesem.sql_hours_since(f"l.{left_on}", f"r.{right_on}")
    cond = " AND ".join(f"l.{k} = r.{k}" for k in by)
    exclude = ", ".join(by)
    return (
        f"SELECT l.*, r.* EXCLUDE ({exclude}), {hours} AS hours_since\n"
        f"FROM {_subquery(_rel_sql(left), 'l')}\n"
        f"JOIN {_subquery(_rel_sql(right), 'r')} ON {cond}\n"
        f"WHERE {hours} >= {-float(before_h)!r} AND {hours} < {float(after_h)!r}"
    )


def window_join(
    left: Any,
    right: Any,
    by: Sequence[str],
    on: str | tuple[str, str],
    before_h: float,
    after_h: float,
    *,
    con: duckdb.DuckDBPyConnection,
) -> duckdb.DuckDBPyRelation:
    """:func:`window_join_sql` as a relation."""
    return con.sql(window_join_sql(left, right, by, on, before_h, after_h))


def asof_join_sql(
    left: Any,
    right: Any,
    by: Sequence[str],
    on: str | tuple[str, str],
    direction: Direction = "backward",
    tolerance_h: float | None = None,
    how: str = "inner",
) -> str:
    """DuckDB ``ASOF JOIN``: for every ``left`` row the one ``right`` row with the same
    ``by`` keys and the greatest ``right.on <= left.on`` (``backward``) or the smallest
    ``right.on >= left.on`` (``forward``); ``hours_since`` is right - left. With
    ``tolerance_h`` a match farther than that is dropped (``inner``) or blanked
    (``left``: the right columns become NULL, the left row stays)."""
    left_on, right_on = (on, on) if isinstance(on, str) else on
    if not by:
        raise TimelineError("asof_join needs at least one by column")
    if direction not in ("backward", "forward"):
        raise TimelineError("direction must be backward or forward")
    if how not in ("inner", "left"):
        raise TimelineError("how must be inner or left")
    op = ">=" if direction == "backward" else "<="
    hours = timesem.sql_hours_since(f"l.{left_on}", f"r.{right_on}")
    cond = " AND ".join(f"l.{k} = r.{k}" for k in by) + f" AND l.{left_on} {op} r.{right_on}"
    exclude = ", ".join(by)
    join_kind = "ASOF LEFT JOIN" if how == "left" else "ASOF JOIN"
    body = (
        f"SELECT l.*, r.* EXCLUDE ({exclude}), {hours} AS hours_since\n"
        f"FROM {_subquery(_rel_sql(left), 'l')}\n"
        f"{join_kind} {_subquery(_rel_sql(right), 'r')} ON {cond}"
    )
    if tolerance_h is None:
        return body
    tol = float(tolerance_h)
    if tol < 0:
        raise TimelineError("tolerance_h must be >= 0")
    if how == "inner":
        return body + f"\nWHERE abs({hours}) <= {tol!r}"
    # left: blank the right side beyond the tolerance, keep the left row
    return (
        "SELECT * REPLACE (CASE WHEN abs(hours_since) <= "
        f"{tol!r} THEN hours_since END AS hours_since)\n"
        f"FROM {_subquery(body, 'j')}"
    )


def asof_join(
    left: Any,
    right: Any,
    by: Sequence[str],
    on: str | tuple[str, str],
    direction: Direction = "backward",
    tolerance_h: float | None = None,
    *,
    con: duckdb.DuckDBPyConnection,
    how: str = "inner",
) -> duckdb.DuckDBPyRelation:
    """:func:`asof_join_sql` as a relation."""
    return con.sql(asof_join_sql(left, right, by, on, direction, tolerance_h, how))


def event_at_sql(
    source: EventSource,
    anchor: Anchor,
    tolerance_h: float,
    grain: str = "icustay",
    clip_to_stay: bool = False,
) -> str:
    """Per unit and code, the **last** event at or before the anchor within
    ``tolerance_h`` hours (the baseline value): the aligned columns of that one event."""
    tol = float(tolerance_h)
    if tol <= 0:
        raise TimelineError("tolerance_h must be > 0")
    # the window is [-tol, 0] — an event at the anchor itself counts as "at"
    aligned = _align_sql(source, anchor, (-tol, 0.0), grain, clip_to_stay, end_inclusive=True)
    keys = ", ".join(_grain_keys(grain))
    return (
        "SELECT * EXCLUDE (seq)\n"
        "FROM (\n"
        "    SELECT *, row_number() OVER ("
        f"PARTITION BY {keys}, code ORDER BY event_time DESC) AS seq\n"
        f"    FROM {_subquery(aligned, 'al')}\n"
        ")\nWHERE seq = 1"
    )


def event_at(
    source: EventSource,
    anchor: Anchor,
    tolerance_h: float,
    grain: str = "icustay",
    clip_to_stay: bool = False,
    *,
    con: duckdb.DuckDBPyConnection,
) -> duckdb.DuckDBPyRelation:
    """:func:`event_at_sql` as a relation."""
    return con.sql(event_at_sql(source, anchor, tolerance_h, grain, clip_to_stay))


# ---------------------------------------------------------------------------
# 3. Binning + aggregation
# ---------------------------------------------------------------------------


def _agg_columns(aggs: Mapping[str, Sequence[str]]) -> list[tuple[str, str]]:
    """``[(output column, SQL)]`` for an ``aggs`` spec (``count`` -> ``n_<col>``, the rest
    ``<col>_<agg>``)."""
    out: list[tuple[str, str]] = []
    for col, names in aggs.items():
        for agg in names:
            if agg not in AGGREGATIONS:
                raise TimelineError(
                    f"unknown aggregation {agg!r}; expected one of {', '.join(AGGREGATIONS)}"
                )
            name = f"n_{col}" if agg == "count" else f"{col}_{agg}"
            out.append((name, AGGREGATIONS[agg].format(col=col)))
    if not out:
        raise TimelineError("aggs must name at least one aggregation")
    return out


def hourly_bins_sql(
    aligned: Any,
    width_h: float = 1.0,
    aggs: Mapping[str, Sequence[str]] | None = None,
    *,
    keys: Sequence[str] = ("stay_id",),
    fill: bool = False,
    window: Sequence[float] | None = None,
) -> str:
    """Per unit (``keys``), ``code`` and ``[start, end)`` bin of ``width_h`` hours over
    ``hours_since_anchor`` (:func:`timesem.sql_hour_bin`: bin ``i`` covers
    ``[i * width, (i + 1) * width)``): ``bin_index``, ``bin_start_h``, ``bin_end_h`` and
    the aggregates of ``aggs`` (default: count / mean / min / max / last of ``value``).
    ``fill=True`` adds the empty bins of every unit x code present in ``aligned`` — over
    ``window`` when given, else between the smallest and largest bin seen — with the count
    0 and the other aggregates NULL."""
    if width_h <= 0:
        raise TimelineError(f"width_h must be positive, got {width_h!r}")
    spec = dict(aggs) if aggs is not None else DEFAULT_AGGS
    columns = _agg_columns(spec)
    count_names = [name for name, _ in columns if name.startswith("n_")]
    key_list = list(keys)
    if not key_list:
        raise TimelineError("hourly_bins needs at least one unit key")
    k_csv = ", ".join(key_list)
    bin_expr = timesem.sql_hour_bin("hours_since_anchor", width_h)
    agg_csv = ",\n       ".join(f"{sql} AS {name}" for name, sql in columns)
    w = float(width_h)
    grouped = (
        f"SELECT {k_csv}, code, {bin_expr} AS bin_index,\n"
        f"       {agg_csv}\n"
        f"FROM {_subquery(_rel_sql(aligned), 'al')}\n"
        f"GROUP BY {k_csv}, code, {bin_expr}"
    )
    if not fill:
        return (
            f"SELECT {k_csv}, code, bin_index, CAST(bin_index * {w!r} AS DOUBLE) AS bin_start_h, "
            f"CAST((bin_index + 1) * {w!r} AS DOUBLE) AS bin_end_h, "
            + ", ".join(name for name, _ in columns)
            + f"\nFROM {_subquery(grouped, 'g')}"
        )
    if window is not None:
        start, end = _require_window(window)
        lo = math.floor(start / w)
        hi = math.ceil(end / w)
        bins = f"(SELECT unnest(range({lo}, {hi})) AS bin_index)"
    else:
        bins = (
            "(SELECT unnest(range((SELECT min(bin_index) FROM g), "
            "(SELECT max(bin_index) + 1 FROM g))) AS bin_index)"
        )
    filled = ", ".join(
        f"coalesce(g.{name}, 0) AS {name}" if name in count_names else f"g.{name}"
        for name, _ in columns
    )
    return (
        f"WITH g AS (\n{_indent(grouped)}\n),\n"
        f"units AS (SELECT DISTINCT {k_csv}, code FROM g),\n"
        f"bins AS {bins}\n"
        f"SELECT {', '.join(f'units.{k}' for k in key_list)}, units.code, bins.bin_index, "
        f"CAST(bins.bin_index * {w!r} AS DOUBLE) AS bin_start_h, "
        f"CAST((bins.bin_index + 1) * {w!r} AS DOUBLE) AS bin_end_h, "
        f"{filled}\n"
        "FROM units CROSS JOIN bins\n"
        f"LEFT JOIN g ON {' AND '.join(f'g.{k} = units.{k}' for k in key_list)} "
        "AND g.code = units.code AND g.bin_index = bins.bin_index"
    )


def hourly_bins(
    aligned: Any,
    width_h: float = 1.0,
    aggs: Mapping[str, Sequence[str]] | None = None,
    *,
    con: duckdb.DuckDBPyConnection,
    keys: Sequence[str] = ("stay_id",),
    fill: bool = False,
    window: Sequence[float] | None = None,
) -> duckdb.DuckDBPyRelation:
    """:func:`hourly_bins_sql` as a relation."""
    return con.sql(hourly_bins_sql(aligned, width_h, aggs, keys=keys, fill=fill, window=window))


def daily_bins(
    aligned: Any,
    aggs: Mapping[str, Sequence[str]] | None = None,
    *,
    con: duckdb.DuckDBPyConnection,
    keys: Sequence[str] = ("stay_id",),
    fill: bool = False,
    window: Sequence[float] | None = None,
) -> duckdb.DuckDBPyRelation:
    """:func:`hourly_bins` with 24-hour bins (day 0 = ``[0, 24)`` hours)."""
    return hourly_bins(aligned, 24.0, aggs, con=con, keys=keys, fill=fill, window=window)


#: The population summary's columns (the released shape). One count column on purpose:
#: an event count beside the unit count is a nested pair whose small difference the gate
#: reads as a derivable cell (EP-33 amendment b), so events per bin are not released.
SUMMARY_COLUMNS: tuple[str, ...] = (
    "code",
    "bin_index",
    "bin_start_h",
    "bin_end_h",
    "n_units",
    "value_mean",
    "value_p25",
    "value_p75",
    "value_min",
    "value_max",
)
SUMMARY_COUNT_COLUMNS: tuple[str, ...] = ("n_units",)


def population_summary_sql(binned: Any) -> str:
    """Per ``code`` and bin across units of a :func:`hourly_bins` relation (over
    ``value`` with at least ``count``, ``mean``, ``min`` and ``max``): ``n_units`` (units
    with an event in the bin), the pooled ``value_mean``, the quartiles of the per-unit
    means and the extremes. Raw — :func:`population_summary` releases it."""
    return (
        "SELECT code, bin_index, any_value(bin_start_h) AS bin_start_h, "
        "any_value(bin_end_h) AS bin_end_h,\n"
        "       count(*) FILTER (WHERE n_value > 0) AS n_units,\n"
        "       sum(n_value * value_mean) / nullif(sum(n_value), 0) AS value_mean,\n"
        "       quantile_cont(value_mean, 0.25) AS value_p25,\n"
        "       quantile_cont(value_mean, 0.75) AS value_p75,\n"
        "       min(value_min) AS value_min,\n"
        "       max(value_max) AS value_max\n"
        f"FROM {_subquery(_rel_sql(binned), 'b')}\n"
        "GROUP BY code, bin_index\n"
        "ORDER BY code, bin_index"
    )


def _release(frame: Any, k: int) -> tuple[Any, int]:
    """``safe.SUPPRESSOR`` over the count columns (rows with a small count withheld with
    everything they carry, complementary over ``code`` / ``bin_index``; EP-43 policy)."""
    from mimicwarehouse.safe import SUPPRESSOR

    counts = [c for c in SUMMARY_COUNT_COLUMNS if c in frame.columns]
    return SUPPRESSOR(frame, k, counts)


def population_summary(
    binned: Any,
    k: int = 11,
    *,
    con: duckdb.DuckDBPyConnection | None = None,
) -> polars.DataFrame:
    """The released per-bin population frame (module docstring, layer 3): the
    :func:`population_summary_sql` aggregate through the ``safe.SUPPRESSOR`` seam (every
    row whose ``n_units`` lies in ``(0, k)`` is withheld, complementary cells with it).
    ``binned`` is a relation (``con`` optional) or SQL text (``con`` required)."""
    if k < 1:
        raise TimelineError(f"k = {k} is invalid (k >= 1)")
    if isinstance(binned, str) or con is not None:
        if con is None:
            raise TimelineError("population_summary over SQL text needs con=")
        frame = con.sql(population_summary_sql(binned)).pl()
    else:
        frame = binned.query("b", population_summary_sql("SELECT * FROM b")).pl()
    released, withheld = _release(frame, k)
    if withheld:
        _LOG.info("population_summary: %d bin row(s) withheld below k = %d", withheld, k)
    return released


def to_mart(relation: Any, path: Path | str, *, compression: str = "zstd") -> Path:
    """Write a relation as Parquet at ``path`` through the rename-aside publisher
    (``<path>.new`` then ``publish.swap_file``); the helper EP-55/56's marts call. Returns
    ``path``."""
    from mimicwarehouse import publish

    dest = Path(path)
    if dest.suffix != ".parquet":
        raise TimelineError(f"to_mart writes Parquet; {dest.name} has no .parquet suffix")
    dest.parent.mkdir(parents=True, exist_ok=True)
    new = publish.new_path_for(dest)
    if new.exists():
        publish.unlink(new)
    relation.write_parquet(str(new), compression=compression)
    publish.swap_file(new, dest, blocked_hint="close the readers of the mart and retry")
    return dest


# ---------------------------------------------------------------------------
# 4. The owner-only single-stay path
# ---------------------------------------------------------------------------


def connection_role(con: duckdb.DuckDBPyConnection) -> str | None:
    """The role :func:`mimicwarehouse.catalog.connect.open_catalog` stamped on ``con``
    (session variable :data:`ROLE_VARIABLE`); ``None`` for any other connection."""
    row = con.execute(f"SELECT getvariable({_sql_str(ROLE_VARIABLE)})").fetchone()
    return None if row is None or row[0] is None else str(row[0])


def require_owner(con: duckdb.DuckDBPyConnection) -> None:
    """Raise ``PermissionError`` unless ``con`` was opened as the owner role (the gate of
    module layer 4; a plain connection, an ``agent`` catalog connection — the default in
    every Claude session — and an in-memory database are all refused)."""
    role = connection_role(con)
    if role != OWNER_ROLE:
        raise PermissionError(
            "stay_events is the owner-only row-level path (GOVERNANCE section 6): open the "
            f"catalog with open_catalog(tier, role='owner'); this connection carries role "
            f"{role!r}"
        )


def _catalog_facts(con: duckdb.DuckDBPyConnection) -> tuple[str, str | None]:
    """``(tier, core_snapshot_id)`` from ``meta.catalog_info``."""
    row = con.execute("SELECT tier, core_snapshot_id FROM meta.catalog_info").fetchone()
    if row is None:
        raise TimelineError("meta.catalog_info is empty - not a mimicwarehouse catalog")
    return str(row[0]), None if row[1] is None else str(row[1])


def stay_events_sql(sources: Sequence[EventSource]) -> str:
    """The lane relation of one stay (``?`` = the stay id): every event of every source
    inside the stay's own window, aligned to ``icu_in``, with a ``lane`` column."""
    if not sources:
        raise TimelineError("stay_events needs at least one source")
    parts = []
    for src in sources:
        aligned = align_sql(src, ICU_IN, (-1e9, 1e9), "icustay", clip_to_stay=True)
        parts.append(
            f"SELECT {_sql_str(src.name)} AS lane, * FROM {_subquery(aligned, 'a')}\n"
            "WHERE stay_id = ?"
        )
    return "\nUNION ALL\n".join(parts) + "\nORDER BY event_time, lane, code"


def stay_events(
    stay_id: int,
    sources: Sequence[EventSource],
    *,
    conn: duckdb.DuckDBPyConnection,
    settings: Settings | None = None,
) -> polars.DataFrame:
    """The row-level lane data of one ICU stay for the owner-only viewer (EP-67):
    ``lane, stay_id, anchor_time, event_time, hours_since_anchor, code, value, valueuom,
    source_table`` ordered by time. Requires an owner-role connection
    (:func:`require_owner`, ``PermissionError`` otherwise) and writes one
    ``row_view:`` audit line (:class:`mimicwarehouse.safe.AuditLine` through
    :func:`mimicwarehouse.fsio.append_jsonl`) that records the request's hash and the
    lane names — never the stay id or a value. Fixture tier only in tests (D-32)."""
    import duckdb

    from mimicwarehouse import fsio
    from mimicwarehouse.safe import AuditLine, audit_path

    require_owner(conn)
    settings = settings or get_settings()
    started = time.perf_counter()
    tier, snapshot_id = _catalog_facts(conn)
    lanes = [s.label for s in sources]
    request = json.dumps(
        {"kind": "stay_events", "stay_id": int(stay_id), "sources": lanes, "tier": tier},
        sort_keys=True,
    )
    statement = ROW_VIEW_PREFIX + request
    sql = stay_events_sql(sources)
    frame = conn.execute(sql, [int(stay_id)] * len(sources)).pl()
    from mimicwarehouse.dag.runner import git_short_sha

    line = AuditLine(
        audit_id=uuid.uuid4().hex,
        ts=datetime.now(UTC).isoformat(timespec="milliseconds"),
        actor=OWNER_ROLE,
        tier=tier,
        statement_sha256=hashlib.sha256(statement.encode("utf-8")).hexdigest(),
        sql_text=f"{ROW_VIEW_PREFIX}stay_events sources={','.join(lanes)}",
        allowed=True,
        refusal_reason=None,
        n_rows=frame.height,
        rows_suppressed=0,
        k=settings.k_suppression,
        wall_ms=round((time.perf_counter() - started) * 1000, 1),
        duckdb_version=duckdb.__version__,
        snapshot_ids={"core": snapshot_id} if snapshot_id else {},
        git_sha=git_short_sha(),
    )
    fsio.append_jsonl(audit_path(settings), line.model_dump(mode="json"))
    return frame


# ---------------------------------------------------------------------------
# 5. The benchmark (brief item 5) + exports
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BenchResult:
    """What :func:`run_benchmark` produced: the run, the timing, the released frame and
    the checked exports."""

    run_id: str
    tier: str
    wall_s: float
    peak_rss_mb: float | None
    n_rows: int
    rows_withheld: int
    exports: dict[str, Path] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "tier": self.tier,
            "wall_s": self.wall_s,
            "peak_rss_mb": self.peak_rss_mb,
            "n_rows": self.n_rows,
            "rows_withheld": self.rows_withheld,
            "exports": {name: str(path) for name, path in self.exports.items()},
        }


def _code_labels(codes: Iterable[str]) -> dict[str, str]:
    """``itemid -> "<itemid> <label>"`` from the EP-39 catalogue where curated."""
    from mimicwarehouse.units import UnknownItemError, load_catalogue, spec

    catalogue = load_catalogue()
    out: dict[str, str] = {}
    for code in codes:
        try:
            label = spec(int(code), catalogue).label
        except (ValueError, UnknownItemError, KeyError):
            out[code] = code
            continue
        out[code] = f"{code} {label}"[:CODE_MAX_CHARS]
    return out


def band_chart(frame: Any, *, title: str, mode: str = "light") -> Any:
    """The ``population_band`` figure: per code, the pooled mean per bin as a line with the
    inter-quartile band of the per-unit means; aggregates only in the spec."""
    import altair as alt
    import polars as pl

    from mimicwarehouse import theme

    palette = theme.palette(mode)
    df = frame if isinstance(frame, pl.DataFrame) else pl.DataFrame(frame)
    labels = _code_labels(df.get_column("code").unique().to_list())
    plot = df.with_columns(
        pl.col("code").replace_strict(labels, default=pl.col("code")).alias("item")
    )
    columns = ["item", "bin_start_h", "value_mean", "value_p25", "value_p75", "n_units"]
    data = alt.InlineData(values=plot.select(columns).to_dicts())
    base = alt.Chart()
    x = alt.X("bin_start_h:Q", title="hours since ICU admission (bin start)")
    band = base.mark_area(opacity=0.25, color=palette.primary).encode(
        x=x, y=alt.Y("value_p25:Q", title="value"), y2="value_p75:Q"
    )
    line = base.mark_line(color=palette.primary).encode(
        x=x,
        y=alt.Y("value_mean:Q", title="value"),
        tooltip=[
            alt.Tooltip("item:N", title="item"),
            alt.Tooltip("bin_start_h:Q", title="bin start (h)"),
            alt.Tooltip("value_mean:Q", title="mean", format=".2f"),
            alt.Tooltip("n_units:Q", title="units"),
        ],
    )
    chart = (
        alt.layer(band, line, data=data)
        .properties(width=CHART_WIDTH, height=CHART_HEIGHT)
        .facet(row=alt.Row("item:N", title=None), title=alt.TitleParams(text=title))
        .resolve_scale(y="independent")
    )
    return chart


def band_spec(frame: Any, *, title: str, mode: str = "light") -> dict[str, Any]:
    """The Vega-Lite spec of :func:`band_chart` with the EP-5 theme merged in
    (``alt.theme.enable`` as a context, the process-wide theme untouched) and inline data
    under ``data.values``."""
    import altair as alt

    from mimicwarehouse import theme

    chart = band_chart(frame, title=title, mode=mode)
    theme.register_altair()
    with alt.theme.enable(theme.THEME_NAMES[theme.palette(mode).mode]):
        spec = chart.to_dict()
    datasets = spec.get("datasets")
    name = spec.get("data", {}).get("name") if isinstance(spec.get("data"), dict) else None
    if isinstance(datasets, dict) and name in datasets:
        spec["data"] = {"values": datasets.pop(name)}
        if not datasets:
            del spec["datasets"]
    return spec


def to_png(spec: Mapping[str, Any], *, scale: float = PNG_SCALE) -> bytes:
    """The static PNG of a Vega-Lite spec through ``vl-convert-python`` (core since EP-48)."""
    import vl_convert as vlc

    return bytes(vlc.vegalite_to_png(json.dumps(dict(spec)), scale=scale))


def _fmt_float(value: Any) -> str:
    if value is None:
        return "-"
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value)
    if math.isnan(f):
        return "-"
    return f"{f:.2f}"


def render_markdown(
    frame: Any,
    *,
    run_id: str,
    tier: str,
    k: int,
    anchor_label: str,
    window: Sequence[float],
    itemids: Sequence[int],
    rows_withheld: int,
    settings: Settings | None = None,
) -> str:
    """The ``.md`` twin of the benchmark table: claim type, the retrospective sentence, a
    disclosure line, the parameters, the released table (integers through ``fmt_int``,
    means to two decimals) and the EP-35 reproduction block."""
    from mimicwarehouse.inventory import fmt_int
    from mimicwarehouse.run import reproduction_block

    labels = _code_labels([str(c) for c in frame.get_column("code").unique().to_list()])
    lines = [
        "# Timeline benchmark: laboratory results aligned to ICU admission",
        "",
        f"Claim type: {CLAIM_TYPE}. {RETROSPECTIVE_SENTENCE} Descriptive population",
        "trajectories only - no comparison, no adjustment, no causal claim.",
        "",
        f"Disclosure: every bin row whose unit count lies in (0, {k}) is withheld,",
        "with the complementary cells the gate requires (`disclose.suppress`, EP-43);",
        f"{fmt_int(rows_withheld)} row(s) withheld. Nothing below k = {k} appears in this file.",
        "",
        "## Parameters",
        "",
        f"- Anchor: `{anchor_label}`; grain `icustay`; window "
        f"`[{window[0]:g}, {window[1]:g})` hours; events clipped to the stay; 1-hour "
        "`[start, end)` bins over `hours_since_anchor`.",
        "- Source: `labs("
        + ", ".join(str(i) for i in itemids)
        + ")` - "
        + "; ".join(labels.get(str(i), str(i)) for i in itemids)
        + ".",
        f"- Tier `{tier}`, run `{run_id}`, k = {k}.",
        "- Columns: `n_units` = ICU stays with at least one result in the bin (events per",
        "  bin are not released - a nested count beside it would leak small differences);",
        "  `mean` = the pooled mean; `p25` / `p75` = the quartiles of the per-stay means;",
        "  `min` / `max` = the extremes.",
        "",
        "## Population summary",
        "",
        "| item | bin_index | start (hours) | end (hours) | n_units | mean | p25 | p75 "
        "| min | max |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in frame.iter_rows(named=True):
        lines.append(
            "| "
            + " | ".join(
                [
                    labels.get(str(row["code"]), str(row["code"])),
                    str(int(row["bin_index"])),
                    f"{float(row['bin_start_h']):g}",
                    f"{float(row['bin_end_h']):g}",
                    fmt_int(None if row["n_units"] is None else int(row["n_units"])),
                    _fmt_float(row["value_mean"]),
                    _fmt_float(row["value_p25"]),
                    _fmt_float(row["value_p75"]),
                    _fmt_float(row["value_min"]),
                    _fmt_float(row["value_max"]),
                ]
            )
            + " |"
        )
    lines += ["", reproduction_block(run_id, settings).rstrip(), ""]
    return "\n".join(lines)


def _check_and_publish(
    payloads: Mapping[str, str | bytes], target: Path, k: int, settings: Settings
) -> dict[str, Path]:
    """Stage every export under ``<data_root>/tmp``, run ``disclose.check`` on each, and
    publish them all (with sidecars) only when every check passes — the EP-48 shape."""
    import shutil
    import tempfile

    from mimicwarehouse import disclose, fsio

    tmp_root = settings.layout["tmp"]
    tmp_root.mkdir(parents=True, exist_ok=True)
    published: dict[str, Path] = {}
    with tempfile.TemporaryDirectory(prefix="timeline-", dir=tmp_root) as staging:
        stage = Path(staging)
        for name, payload in payloads.items():
            if isinstance(payload, bytes):
                (stage / name).write_bytes(payload)
            else:
                fsio.atomic_write_text(stage / name, payload)
        checks = {name: disclose.check(stage / name, k) for name in payloads}
        failing = {name: r for name, r in checks.items() if not r.passed}
        if failing:
            details = "; ".join(
                f"{name}: "
                + ", ".join(
                    f"{f.code} {f.where} - {f.detail}"
                    for f in r.findings
                    if f.status == disclose.FAIL
                )
                for name, r in failing.items()
            )
            raise disclose.DisclosureError(
                f"timeline export(s) refused by the disclosure gate, nothing written: {details}"
            )
        target.mkdir(parents=True, exist_ok=True)
        for name in payloads:
            dest = target / name
            try:
                (stage / name).replace(dest)
            except OSError:
                shutil.move(str(stage / name), str(dest))
            disclose.write_sidecar(dest, checks[name], k)
            published[name] = dest
    return published


def _write_exports(
    r: Run,
    frame: Any,
    *,
    k: int,
    anchor_label: str,
    window: Sequence[float],
    itemids: Sequence[int],
    rows_withheld: int,
) -> dict[str, Path]:
    """The four gated exports of the benchmark run (module docstring)."""
    import io

    import polars as pl

    df = frame if isinstance(frame, pl.DataFrame) else pl.DataFrame(frame)
    r.write_manifest()  # the reproduction block reads the manifest as it stands
    parquet = io.BytesIO()
    df.write_parquet(parquet)
    csv_text = df.write_csv()
    spec = band_spec(df, title=f"{BENCH_NAME} ({r.tier}, k = {k})")
    payloads: dict[str, str | bytes] = {
        f"{BENCH_NAME}.parquet": parquet.getvalue(),
        f"{BENCH_NAME}.md": render_markdown(
            df,
            run_id=r.run_id,
            tier=r.tier,
            k=k,
            anchor_label=anchor_label,
            window=window,
            itemids=itemids,
            rows_withheld=rows_withheld,
            settings=r.settings,
        ),
        f"{BENCH_FIGURE}.csv": csv_text,
        f"{BENCH_FIGURE}.png": to_png(spec),
    }
    target = r.dir / EXPORTS_DIRNAME
    published = _check_and_publish(payloads, target, k, r.settings)
    for name in published:
        rel = f"{EXPORTS_DIRNAME}/{name}"
        if name.endswith((".png",)):
            r.manifest.figures = {**r.manifest.figures, name: rel}
        else:
            r.manifest.tables = {**r.manifest.tables, name: rel}
    return published


def run_benchmark(
    tier: str,
    *,
    settings: Settings | None = None,
    itemids: Sequence[int] = BENCH_ITEMIDS,
    window: Sequence[float] = BENCH_WINDOW,
    anchor_name: str = BENCH_ANCHOR,
    k: int | None = None,
    doctor: bool = True,
) -> BenchResult:
    """The alignment benchmark (brief item 5): ``align(labs(itemids), <anchor>, window)``
    → ``hourly_bins`` → ``population_summary`` on the tier catalog, inside
    ``run.start(kind="bench")`` with the statements recorded, a ``kind: query`` benchmark
    line named :data:`BENCH_NAME` (wall, peak RSS, rows), the released frame under
    ``tables/`` and the gated exports under ``exports/`` with sidecars. Full tier: only as
    a background job (``mwh timeline bench --tier full --background --job …``)."""
    from mimicwarehouse import run as run_mod
    from mimicwarehouse.catalog.connect import open_catalog
    from mimicwarehouse.run import ResourceLog

    settings = settings or get_settings()
    resolved_k = k if k is not None else settings.k_suppression
    if tier in ("dev", "full") and resolved_k < 11:
        raise TimelineError(f"k = {resolved_k} < 11 is refused on the {tier} tier (D-31/D-33)")
    anchor_obj = anchor(anchor_name)
    source = labs(itemids)
    start, end = _require_window(window)
    aligned_sql = align_sql(source, anchor_obj, (start, end), "icustay", True)
    binned_sql = hourly_bins_sql(aligned_sql, 1.0, keys=("stay_id",), fill=False)
    summary_sql = population_summary_sql(binned_sql)
    params = {
        "name": BENCH_NAME,
        "anchor": anchor_obj.label,
        "source": source.label,
        "grain": "icustay",
        "window_h": [start, end],
        "width_h": 1.0,
        "k": resolved_k,
        "itemids": list(itemids),
    }
    with run_mod.start(
        RUN_NAME,
        tier=tier,
        kind=RUN_KIND,
        params=params,
        settings=settings,
        claim_type=CLAIM_TYPE,
        doctor=doctor,
    ) as r:
        r.record_sql("align", aligned_sql)
        r.record_sql("hourly_bins", binned_sql)
        r.record_sql("population_summary", summary_sql)
        con = open_catalog(tier, settings=settings)
        try:
            _tier, snapshot_id = _catalog_facts(con)
            if snapshot_id:
                r.record_snapshot("core", snapshot_id)

            def work() -> Any:
                return con.sql(summary_sql).pl()

            raw, usage = ResourceLog.measure(work, data_root=settings.data_root)
        finally:
            con.close()
        released, withheld = _release(raw, resolved_k)
        r.bench(BENCH_KIND, BENCH_NAME, rows=int(raw.height), **usage.bench_fields())
        r.save_table(BENCH_NAME, released)
        exports = _write_exports(
            r,
            released,
            k=resolved_k,
            anchor_label=anchor_obj.label,
            window=(start, end),
            itemids=itemids,
            rows_withheld=withheld,
        )
        _LOG.info(
            "timeline bench %s on %s: %d raw bin rows, %d released, wall %.1f s",
            r.run_id,
            tier,
            raw.height,
            released.height,
            usage.wall_s,
        )
        return BenchResult(
            run_id=r.run_id,
            tier=tier,
            wall_s=float(usage.wall_s),
            peak_rss_mb=usage.peak_rss_mb,
            n_rows=int(released.height),
            rows_withheld=int(withheld),
            exports=exports,
        )


# ---------------------------------------------------------------------------
# 6. docs/methods/timelines.md — the generated blocks
# ---------------------------------------------------------------------------


def _md_table(header: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(lines) + "\n"


def anchor_table_rows() -> list[list[str]]:
    """One row per registry anchor plus the factories (generated from :data:`ANCHORS`)."""
    rows = []
    for a in ANCHORS.values():
        rows.append(
            [
                f"`{a.name}`",
                f"`{a.grain}`",
                f"`{a.source}`",
                f"`{a.time_column}`",
                f"`{a.key}`",
                f"`{a.selector}`",
                f"`{a.filter_label or a.filter}`" if a.filter else "-",
                a.description,
            ]
        )
    factories = (
        (
            "med_start(codeset, source)",
            "hadm / icustay",
            "prescriptions or inputevents",
            "starttime",
            "hadm_id / stay_id",
            "first",
            "drug names of the set (prescriptions) or its ICU itemids",
            "first start of a drug of the code set",
        ),
        (
            "procedure(itemids)",
            "icustay",
            "mimiciv_icu.procedureevents",
            "starttime",
            "stay_id",
            "first",
            "itemid IN (...)",
            "first procedure of the itemids",
        ),
        (
            "vent_start(kinds)",
            "icustay",
            "mimiciv_derived.ventilation",
            "starttime",
            "stay_id",
            "first",
            "ventilation_status IN (kinds)",
            "first ventilation episode of the kinds",
        ),
        (
            "phenotype_onset(id@version)",
            "the phenotype's",
            "mimiciv_derived.phenotype_<id>",
            "onset_time",
            "per grain",
            "first",
            "flag",
            "phenotype onset (latest built version)",
        ),
        (
            "custom_anchor(name, sql, key=, grain=)",
            "given",
            "(your SELECT)",
            "anchor_time",
            "given",
            "first",
            "-",
            "subject_id, hadm_id, stay_id, anchor_time",
        ),
    )
    for name, grain, source, col, key, selector, flt, desc in factories:
        rows.append(
            [
                f"`{name}`",
                f"`{grain}`",
                f"`{source}`",
                f"`{col}`",
                f"`{key}`",
                f"`{selector}`",
                f"`{flt}`" if flt != "-" else "-",
                desc,
            ]
        )
    return rows


def render_anchor_table() -> str:
    """The anchor registry as a Markdown table (generated)."""
    return _md_table(
        [
            "anchor",
            "native grain",
            "source",
            "time column",
            "key",
            "selector",
            "filter",
            "what it is",
        ],
        anchor_table_rows(),
    )


def render_source_table() -> str:
    """The event-source presets as a Markdown table (generated)."""
    return _md_table(
        ["preset", "call", "table", "time; key; code, value / unit"],
        [[f"`{n}`", f"`{sig}`", f"`{tbl}`", desc] for n, sig, tbl, desc in SOURCE_PRESETS],
    )


def methods_doc_path() -> Path:
    """``mimicwarehouse/docs/methods/timelines.md``."""
    from mimicwarehouse.config import workspace_root

    return workspace_root() / METHODS_DOC_RELPATH


def sync_methods_doc(path: Path | None = None) -> Path:
    """Re-render the generated blocks of the methods page in place (idempotent;
    ``python -m mimicwarehouse.timeline`` runs it; ``test_ep49`` asserts the page is in
    sync). The narrative around the markers is never touched."""
    from mimicwarehouse.dag.benchmarks import replace_marked_block

    target = Path(path) if path is not None else methods_doc_path()
    text = target.read_text(encoding="utf-8")
    for (begin, end), block in (
        (ANCHORS_MARK, render_anchor_table()),
        (SOURCES_MARK, render_source_table()),
    ):
        text = replace_marked_block(text, block, begin=begin, end=end)
    target.write_text(text.rstrip("\n") + "\n", encoding="utf-8", newline="\n")
    return target


# ---------------------------------------------------------------------------
# 7. CLI — `mwh timeline anchors | bench` (attached in cli.py)
# ---------------------------------------------------------------------------

timeline_app = typer.Typer(
    help="Event-aligned timeline API (EP-49): the anchor registry and the alignment benchmark.",
    no_args_is_help=True,
)


@timeline_app.command("anchors")
def anchors_command(
    ctx: typer.Context,
    as_json: bool = typer.Option(False, "--json", help="Emit the registry as JSON."),
) -> None:
    """List the anchor registry (names, sources, selectors; no data is read)."""
    from rich.markup import escape
    from rich.table import Table

    from mimicwarehouse.console import console, emit_json

    if as_json:
        emit_json(
            [
                {
                    "name": a.name,
                    "grain": a.grain,
                    "source": a.source,
                    "time_column": a.time_column,
                    "key": a.key,
                    "selector": a.selector,
                    "filter": a.filter,
                    "description": a.description,
                }
                for a in ANCHORS.values()
            ]
        )
        return
    table = Table(title="timeline anchors (EP-49)")
    for column in ("anchor", "grain", "source", "time", "key", "selector"):
        table.add_column(column)
    for a in ANCHORS.values():
        table.add_row(a.name, a.grain, escape(a.source), a.time_column, a.key, a.selector)
    console.print(table)
    console.print(
        "factories: med_start(codeset, source) - procedure(itemids) - vent_start(kinds) - "
        "phenotype_onset(id@version) - custom_anchor(name, sql)",
        highlight=False,
    )


@timeline_app.command("bench")
def bench_command(
    ctx: typer.Context,
    tier: str = typer.Option(..., "--tier", help="Tier catalog: fixture | demo | dev | full."),
    background: bool = typer.Option(
        False,
        "--background",
        help="Detach: launch this benchmark as a background job (requires --job) and return.",
    ),
    job: str | None = typer.Option(None, "--job", help="Job name for --background (runs/jobs/)."),
    as_json: bool = typer.Option(False, "--json", help="Emit the result as JSON."),
) -> None:
    """Run the alignment benchmark (five curated labs aligned to icu_in over [-6, 48) h ->
    hourly bins -> the suppressed population summary) inside a kind: bench run; the full
    tier only as --background --job NAME."""
    from rich.markup import escape

    from mimicwarehouse.console import EXIT_FINDINGS, EXIT_USAGE, console, emit_json, fail
    from mimicwarehouse.run import TIERS

    prefix = "mwh timeline bench"
    state: Any = ctx.obj
    if tier not in TIERS:
        fail(prefix, f"unknown tier {tier!r}; expected one of {', '.join(TIERS)}")
    settings: Settings = state.settings
    if background:
        if not job:
            fail(prefix, "--background requires --job NAME")
        from mimicwarehouse.dag import jobs as jobs_mod

        argv: list[str] = []
        if state.data_root_override is not None:
            argv += ["--data-root", str(state.data_root_override)]
        argv += ["timeline", "bench", "--tier", tier]
        try:
            info = jobs_mod.launch(argv, job, settings)
        except jobs_mod.JobError as exc:
            fail(prefix, str(exc))
        console.print(
            f"launched job [bold]{escape(info.job)}[/] (pid {info.pid}) - log {escape(info.log)}",
            highlight=False,
        )
        console.print(f"check it with: mwh jobs --job {escape(info.job)}", highlight=False)
        return
    from mimicwarehouse.catalog.connect import CatalogOpenError
    from mimicwarehouse.disclose import DisclosureError

    try:
        result = run_benchmark(tier, settings=settings)
    except (CatalogOpenError, TimelineError) as exc:
        fail(prefix, str(exc), code=EXIT_USAGE)
    except DisclosureError as exc:
        fail(prefix, str(exc), code=EXIT_FINDINGS)
    if as_json:
        emit_json(result.to_dict())
        return
    rss = "-" if result.peak_rss_mb is None else f"{result.peak_rss_mb:.0f} MB"
    console.print(
        f"run [bold]{result.run_id}[/] tier {result.tier}: wall {result.wall_s:.1f} s, "
        f"peak RSS {rss}, {result.n_rows} released bin row(s), {result.rows_withheld} withheld",
        highlight=False,
    )
    for path in result.exports.values():
        console.print(f"written: {escape(str(path))} (disclose check PASS)", highlight=False)


__all__ = [
    "AGGREGATIONS",
    "ALIGNED_COLUMNS",
    "ANCHORS",
    "ANCHORS_MARK",
    "BENCH_ANCHOR",
    "BENCH_FIGURE",
    "BENCH_ITEMIDS",
    "BENCH_KIND",
    "BENCH_NAME",
    "BENCH_WINDOW",
    "CLAIM_TYPE",
    "CODE_MAX_CHARS",
    "DEFAULT_AGGS",
    "DEFAULT_VENT_KINDS",
    "DETERIORATION",
    "EXPORTS_DIRNAME",
    "FIRST_ANTIBIOTIC",
    "FIRST_CULTURE",
    "HOSP_ADMIT",
    "HOSP_DISCHARGE",
    "ICU_IN",
    "ICU_OUT",
    "KEYS",
    "METHODS_DOC_RELPATH",
    "OWNER_ROLE",
    "RETROSPECTIVE_SENTENCE",
    "ROLE_VARIABLE",
    "ROW_VIEW_PREFIX",
    "RUN_KIND",
    "RUN_NAME",
    "SELECTORS",
    "SOURCES_MARK",
    "SOURCE_PRESETS",
    "SUMMARY_COLUMNS",
    "SUMMARY_COUNT_COLUMNS",
    "SUSPECTED_INFECTION",
    "TIMELINE_GRAINS",
    "VASOPRESSOR_COLUMNS",
    "VENT_KINDS",
    "VENT_START",
    "Anchor",
    "BenchResult",
    "EventSource",
    "TimelineError",
    "align",
    "align_sql",
    "anchor",
    "anchor_sql",
    "anchor_table_rows",
    "asof_join",
    "asof_join_sql",
    "band_chart",
    "band_spec",
    "connection_role",
    "custom_anchor",
    "custom_source",
    "daily_bins",
    "event_at",
    "event_at_sql",
    "hourly_bins",
    "hourly_bins_sql",
    "inputs",
    "labs",
    "med_start",
    "meds",
    "methods_doc_path",
    "micro",
    "outputs",
    "phenotype_onset",
    "population_summary",
    "population_summary_sql",
    "procedure",
    "procedures",
    "render_anchor_table",
    "render_markdown",
    "render_source_table",
    "require_owner",
    "run_benchmark",
    "stay_events",
    "stay_events_sql",
    "sync_methods_doc",
    "timeline_app",
    "to_mart",
    "to_png",
    "transfers",
    "vent_start",
    "vitals",
    "window_join",
    "window_join_sql",
]


if __name__ == "__main__":
    print(sync_methods_doc())
