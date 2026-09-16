"""The cohort compiler: a :class:`~mimicwarehouse.cohort.spec.CohortSpec` -> one
deterministic CTE chain, its attrition statement and the ordered step list (EP-47 item 1;
DESIGN §9; GOVERNANCE §4/§12; D-5, D-17, D-25).

The chain, in order (one CTE per step; every step name is an attrition row)::

    WITH base AS (<the grain's source population>),          -- every unit of the grain
    idx AS (<the index event joined to admissions / patients [/ the stay]>),
    era AS (... WHERE anchor_year_group IN (...)),           -- only with an era_filter
    crit_01_<label> AS (SELECT c.* FROM idx AS c WHERE <predicate>),    -- inclusion
    crit_02_<label> AS (SELECT c.* FROM crit_01_<label> AS c
                        WHERE NOT coalesce(<predicate>, false)),                  -- exclusion
    ...                                                      -- spec order
    washout AS (...),                                        -- only when washout.rule != none
    cohort AS (SELECT <output columns> FROM <last> ORDER BY <keys>)
    SELECT * FROM cohort

* **base** is the grain's source relation (``timesem.Grain.source``: patients for the
  subject grain, admissions for ``hadm``, ICU stays for the stay-based grains), the
  denominator of the attrition table (the tracer's ``base``, EP-31).
* **idx** is the index event: the grain's named rule (:func:`timesem.Grain.index_event_sql`
  — ``first_icu_stay`` is the tracer's ``first_stay`` step), the onset rows of a
  ``phenotype_onset`` (``phenotypes."<id>@<version>"``, the built version the spec pins)
  or the ``first`` / ``last`` value of a ``concept_time`` column per unit. It joins the
  admission (``mimiciv_hosp.admissions``) and the patient (``mimiciv_hosp.patients``) —
  and the index ICU stay when the index yields one — so every later step reads one flat
  relation: keys, ``index_time`` / ``end_time``, the demographic fields, ``anchor_age`` /
  ``anchor_year`` / ``anchor_year_group`` / ``dod`` and the subject's last discharge (the
  ``dod`` visibility anchor). A subject-grain index without an admission of its own
  (a ``phenotype_onset`` / ``concept_time``) picks the admission whose stay contains the
  index time, else the latest one started before it, else the earliest (one
  ``row_number`` — deterministic).
* **criteria** map to predicates over ``c`` (the previous step's alias) — the table in
  the module's docs (``docs/methods/cohorts.md`` § compilation) and inline below. A
  ``custom_sql`` criterion is wrapped as a CTE (``custom_NN``) and **semi-joined on the
  grain keys**; its step is flagged ``custom`` in :attr:`CompiledCohort.steps`.
* **cohort** projects the brief's output columns — the grain keys (``subject_id``,
  ``hadm_id``, ``stay_id`` as applicable, plus the bin index of the ``icu_day`` /
  ``hour_bin`` grains), ``index_time``, ``era_index``, ``age_at_index`` (capped at
  :data:`timesem.AGE_CAP`), ``age_capped``, ``obs_start`` / ``obs_end`` (the observation
  window in absolute patient time), ``follow_up_end`` / ``censor_reason`` (the follow-up
  rule: an in-hospital outcome ends at discharge — ``death`` / ``discharge_alive``; a
  censored outcome at ``min(index + horizon, last discharge + 365 d)`` —
  ``death`` when ``dod`` falls inside, else ``horizon`` / ``dod_visibility``) and
  ``custom_flag`` (any custom criterion in the definition) — under a total ``ORDER BY``
  over the keys, so a rebuild from the same inputs is byte-identical (EP-47 item 2).
  Identifiers stay inside the chain and in the mart; sessions read the mart through
  ``safe_query`` as aggregates (GOVERNANCE §4).
* **attrition_sql** is one ``UNION ALL`` of ``count(*) AS n_units, count(DISTINCT
  subject_id) AS n_subjects`` per step (a real count-family pair, EP-33 B1) — one query,
  computed at build time and stored raw in the mart (EP-47 item 3 suppresses on read).
* **Determinism.** No non-deterministic function (``random`` / ``now`` / ``uuid``), no
  ``LIMIT`` without ``ORDER BY``, every ``row_number`` over a total order (time, then id);
  the SQL text is a pure function of the spec's canonical form, so ``sql_sha256`` moves
  exactly when the definition does. The golden file
  ``tests/ep/golden/first_icu_adults@1.0.0.sql`` pins the tracer cohort's text.

Relations the chain reads: ``mimiciv_hosp.*`` / ``mimiciv_icu.*`` (the staged core),
``meta.codeset_members`` (EP-40's compiled members, ``system`` = ``icd9`` / ``icd10`` on
``diagnoses_icd.icd_version``), ``phenotypes."<id>@<version>"`` (EP-41's built versions)
and ``mimiciv_derived.<concept>`` (EP-37/38) — the build step exposes each on the build
connection before executing (:mod:`~mimicwarehouse.cohort.build`). No data access here;
import budget: stdlib + the spec module + ``timesem`` (the contract loads lazily for
``data_availability`` criteria).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

from mimicwarehouse import timesem
from mimicwarehouse.cohort.spec import (
    AGE_CAP,
    CohortSpec,
    CohortSpecError,
    Criterion,
    Washout,
    Window,
    normalize_sql,
)

#: The fixed CTE names (a criterion label can never collide: criteria are ``crit_NN_*``).
STEP_BASE = "base"
STEP_IDX = "idx"
STEP_ERA = "era"
STEP_WASHOUT = "washout"
STEP_COHORT = "cohort"
#: The catalog schema of EP-41's per-version phenotype views.
PHENOTYPES_SCHEMA = "phenotypes"
#: EP-40's compiled members (``lake/meta/<tier>/codeset_members.parquet``).
MEMBERS_TABLE = "meta.codeset_members"
#: The output columns after the grain keys, in order.
OUTPUT_COLUMNS: tuple[str, ...] = (
    "index_time",
    "era_index",
    "age_at_index",
    "age_capped",
    "obs_start",
    "obs_end",
    "follow_up_end",
    "censor_reason",
    "custom_flag",
)
#: ``censor_reason`` values.
REASON_DEATH = "death"
REASON_DISCHARGE_ALIVE = "discharge_alive"
REASON_HORIZON = "horizon"
REASON_DOD_VISIBILITY = "dod_visibility"
REASON_UNKNOWN = "unknown"
#: The key columns in the order they are projected / ordered by.
KEY_ORDER: tuple[str, ...] = ("subject_id", "hadm_id", "stay_id", "day_index", "hour_bin")
#: The demographic fields the index relation carries and where they come from.
_DEMOGRAPHIC_SOURCES: dict[str, str] = {
    "gender": "p.gender",
    "admission_type": "a.admission_type",
    "admission_location": "a.admission_location",
    "discharge_location": "a.discharge_location",
    "insurance": "a.insurance",
    "language": "a.language",
    "marital_status": "a.marital_status",
}
_SLUG_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_STAY_RULES: frozenset[str] = frozenset(
    {"first_icu_stay", "each_icustay", "first_icu_stay_of_first_hadm"}
)
_BIN_GRAINS: dict[str, str] = {"icu_day": "day_index", "hour_bin": "hour_bin"}


class CompileError(CohortSpecError):
    """The spec cannot be compiled (a grain without an index template, a criterion the
    index cannot support such as ``los: icu`` without a stay index, an unknown table)."""


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Step:
    """One attrition step of the chain: its CTE name, the human label, the polarity
    (``population`` / ``index`` / ``era`` / ``inclusion`` / ``exclusion`` / ``washout`` /
    ``cohort``), the criterion kind and whether it is a ``custom_sql`` step."""

    name: str
    label: str
    polarity: str
    kind: str | None = None
    custom: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "step": self.name,
            "label": self.label,
            "polarity": self.polarity,
            "kind": self.kind,
            "custom": self.custom,
        }


@dataclass(frozen=True, slots=True)
class CompiledCohort:
    """What :func:`compile_spec` returns: the single statement, the attrition statement,
    the ordered steps, the output columns, the relations read and the sha256 of ``sql``."""

    ref: str
    tier: str | None
    sql: str
    attrition_sql: str
    steps: tuple[Step, ...]
    keys: tuple[str, ...]
    columns: tuple[str, ...]
    sources: tuple[str, ...]
    sql_sha256: str
    custom: bool = False
    warnings: tuple[str, ...] = field(default=())

    @property
    def step_names(self) -> tuple[str, ...]:
        return tuple(s.name for s in self.steps)


# ---------------------------------------------------------------------------
# Small SQL helpers
# ---------------------------------------------------------------------------


def sql_str(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def _ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _literal(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return repr(value)
    return sql_str(str(value))


def _seconds_interval(hours: float) -> str:
    """``to_seconds(CAST(<n> AS BIGINT))`` for a relative offset in hours (whole seconds
    — MIMIC timestamps are whole seconds, so a window edge never falls between two)."""
    return f"to_seconds(CAST({round(hours * 3600)} AS BIGINT))"


def _window_predicate(anchor: str, event: str, window: Window) -> str:
    """``event`` lies in ``[start_h, end_h)`` hours from ``anchor``."""
    hours = timesem.sql_hours_since(anchor, event)
    return f"{hours} >= {window.start_h!r} AND {hours} < {window.end_h!r}"


def _indent(text: str, spaces: int = 4) -> str:
    pad = " " * spaces
    return "\n".join(pad + line if line else line for line in text.splitlines())


def _step_name(position: int, label: str) -> str:
    return f"crit_{position:02d}_{label}"


def _phenotype_relation(ref: str) -> str:
    return f"{PHENOTYPES_SCHEMA}.{_ident(ref)}"


# ---------------------------------------------------------------------------
# The index relation
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Shape:
    """What the index relation carries for this spec: the grain, its keys, whether a
    stay / an admission is present, the bin column of an expanded grain."""

    grain: timesem.Grain
    keys: tuple[str, ...]
    has_hadm: bool
    has_stay: bool
    bin_column: str | None

    @property
    def unit_key(self) -> str:
        """The finest unit key of the grain (what a concept relation is joined on)."""
        if self.has_stay:
            return "stay_id"
        if self.has_hadm:
            return "hadm_id"
        return "subject_id"

    def finest_common_key(self, available: tuple[str, ...]) -> str | None:
        """The finest of ``stay_id`` / ``hadm_id`` / ``subject_id`` present both here
        and in ``available``."""
        for key in ("stay_id", "hadm_id", "subject_id"):
            if key in available and key in self.keys_available:
                return key
        return None

    @property
    def keys_available(self) -> tuple[str, ...]:
        out = ["subject_id"]
        if self.has_hadm:
            out.append("hadm_id")
        if self.has_stay:
            out.append("stay_id")
        return tuple(out)


def _shape_of(spec: CohortSpec) -> _Shape:
    grain = spec.grain_entry
    if not grain.available:
        raise CompileError(
            f"{spec.ref}: grain {spec.grain!r} is a placeholder until {grain.available_from}"
        )
    bin_column = _BIN_GRAINS.get(grain.name)
    index = spec.index_event
    if index.rule is not None:
        has_stay = index.rule in _STAY_RULES or grain.name in ("icustay", *_BIN_GRAINS)
        has_hadm = True
    elif index.phenotype_onset is not None or index.concept_time is not None:
        has_stay = grain.name in ("icustay", *_BIN_GRAINS)
        has_hadm = True  # resolved through the admission that contains the index
    else:  # pragma: no cover - the schema guarantees one form
        raise CompileError(f"{spec.ref}: index_event carries no form")
    keys: list[str] = ["subject_id"]
    if has_hadm:
        keys.append("hadm_id")
    if has_stay:
        keys.append("stay_id")
    if bin_column is not None:
        keys.append(bin_column)
    return _Shape(grain, tuple(keys), has_hadm, has_stay, bin_column)


def _grain_key_columns(shape: _Shape) -> tuple[str, ...]:
    """The grain's own key columns (what ``cohort`` orders by, in :data:`KEY_ORDER`)."""
    return tuple(k for k in KEY_ORDER if k in shape.keys)


def _base_sql(spec: CohortSpec, shape: _Shape) -> str:
    """Every unit of the grain's source relation, keyed (the attrition denominator). An
    expanded grain (``icu_day`` / ``hour_bin``) counts every bin of every stay, so the
    chain stays non-increasing from ``base`` on (the attrition suppressor's contract)."""
    grain = shape.grain
    if grain.name == "subject":
        return "SELECT subject_id\nFROM mimiciv_hosp.patients"
    if grain.name == "hadm":
        return "SELECT subject_id, hadm_id\nFROM mimiciv_hosp.admissions"
    if shape.bin_column is not None:
        body = grain.index_event_sql("each_icustay")
        return (
            f"SELECT subject_id, hadm_id, stay_id, {shape.bin_column}\n"
            f"FROM (\n{_indent(body)}\n) AS bins"
        )
    return "SELECT subject_id, hadm_id, stay_id\nFROM mimiciv_icu.icustays"


def _rule_index_sql(spec: CohortSpec, shape: _Shape) -> str:
    """The grain's named rule, keys + ``index_time`` / ``end_time`` (+ the bin index)."""
    assert spec.index_event.rule is not None
    body = shape.grain.index_event_sql(spec.index_event.rule)
    if shape.bin_column is not None:
        return (
            f"SELECT subject_id, hadm_id, stay_id, {shape.bin_column}, index_time, end_time\n"
            f"FROM (\n{_indent(body)}\n) AS bins"
        )
    return body


def _containing_admission_sql(units_sql: str, keys: str) -> str:
    """Attach ``hadm_id`` to a subject-keyed index relation (``subject_id, index_time``
    [+ more]): the admission containing the index time, else the latest started before
    it, else the earliest — one ``row_number`` per unit (module docstring)."""
    partition = ", ".join(f"u.{k}" for k in keys.split(", "))
    return (
        f"SELECT {keys}, index_time, hadm_id, end_time\n"
        "FROM (\n"
        f"    SELECT u.*, a.hadm_id, a.dischtime AS end_time,\n"
        f"           row_number() OVER (PARTITION BY {partition} ORDER BY\n"
        "               CASE WHEN a.admittime <= u.index_time AND u.index_time <= a.dischtime"
        " THEN 0\n"
        "                    WHEN a.admittime <= u.index_time THEN 1 ELSE 2 END,\n"
        "               CASE WHEN a.admittime <= u.index_time THEN a.admittime END DESC,\n"
        "               a.admittime, a.hadm_id) AS seq\n"
        "    FROM (\n"
        f"{_indent(units_sql, 8)}\n"
        "    ) AS u\n"
        "    LEFT JOIN mimiciv_hosp.admissions AS a ON a.subject_id = u.subject_id\n"
        ")\n"
        "WHERE seq = 1"
    )


def _onset_index_sql(spec: CohortSpec, shape: _Shape) -> str:
    """``phenotype_onset``: the flagged units with an onset; ``index_time`` = the onset."""
    ref = spec.index_event.phenotype_onset
    assert ref is not None
    relation = _phenotype_relation(ref)
    grain = shape.grain.name
    if grain == "subject":
        units = (
            "SELECT subject_id, onset_time AS index_time\n"
            f"FROM {relation}\n"
            "WHERE flag AND onset_time IS NOT NULL"
        )
        return _containing_admission_sql(units, "subject_id")
    if grain == "hadm":
        return (
            "SELECT ph.subject_id, ph.hadm_id, ph.onset_time AS index_time, "
            "a.dischtime AS end_time\n"
            f"FROM {relation} AS ph\n"
            "JOIN mimiciv_hosp.admissions AS a ON a.hadm_id = ph.hadm_id\n"
            "WHERE ph.flag AND ph.onset_time IS NOT NULL"
        )
    stays = (
        "SELECT ph.subject_id, ph.hadm_id, ph.stay_id, ph.onset_time AS index_time, "
        "i.outtime AS end_time\n"
        f"FROM {relation} AS ph\n"
        "JOIN mimiciv_icu.icustays AS i ON i.stay_id = ph.stay_id\n"
        "WHERE ph.flag AND ph.onset_time IS NOT NULL"
    )
    if shape.bin_column is None:
        return stays
    raise CompileError(
        f"{spec.ref}: a phenotype_onset index does not expand into {grain} bins — index the "
        "icustay grain or use a named rule"
    )


def _concept_time_index_sql(spec: CohortSpec, shape: _Shape) -> str:
    """``concept_time``: the ``first`` / ``last`` value of ``column`` in ``table`` per
    unit of the grain's source (units without a value are not indexed)."""
    ct = spec.index_event.concept_time
    assert ct is not None
    agg = "min" if ct.pick == "first" else "max"
    grain = shape.grain.name
    if shape.bin_column is not None:
        raise CompileError(
            f"{spec.ref}: a concept_time index does not expand into {grain} bins — index the "
            "icustay grain or use a named rule"
        )
    key = "subject_id" if grain == "subject" else ("hadm_id" if grain == "hadm" else "stay_id")
    picked = (
        f"SELECT {_ident(key)} AS {key}, {agg}({_ident(ct.column)}) AS index_time\n"
        f"FROM {ct.table}\n"
        f"WHERE {_ident(ct.column)} IS NOT NULL\n"
        f"GROUP BY 1"
    )
    if grain == "subject":
        units = (
            "SELECT b.subject_id, t.index_time\n"
            "FROM mimiciv_hosp.patients AS b\n"
            f"JOIN (\n{_indent(picked)}\n) AS t ON t.subject_id = b.subject_id"
        )
        return _containing_admission_sql(units, "subject_id")
    if grain == "hadm":
        return (
            "SELECT a.subject_id, a.hadm_id, t.index_time, a.dischtime AS end_time\n"
            "FROM mimiciv_hosp.admissions AS a\n"
            f"JOIN (\n{_indent(picked)}\n) AS t ON t.hadm_id = a.hadm_id"
        )
    return (
        "SELECT i.subject_id, i.hadm_id, i.stay_id, t.index_time, i.outtime AS end_time\n"
        "FROM mimiciv_icu.icustays AS i\n"
        f"JOIN (\n{_indent(picked)}\n) AS t ON t.stay_id = i.stay_id"
    )


def _idx_sql(spec: CohortSpec, shape: _Shape) -> str:
    """The flat index relation (module docstring): the index event joined to the
    admission, the patient, the index stay (or the admission's first stay for the
    ``first_careunit`` field) and the subject's last discharge."""
    index = spec.index_event
    if index.rule is not None:
        inner = _rule_index_sql(spec, shape)
    elif index.phenotype_onset is not None:
        inner = _onset_index_sql(spec, shape)
    else:
        inner = _concept_time_index_sql(spec, shape)
    key_cols = ", ".join(f"x.{k}" for k in shape.keys)
    if shape.has_stay:
        stay_join = "JOIN mimiciv_icu.icustays AS i ON i.stay_id = x.stay_id\n"
        careunit = "i.first_careunit"
        stay_cols = "    i.intime AS stay_intime,\n    i.outtime AS stay_outtime,\n"
    else:
        stay_join = (
            "LEFT JOIN (\n"
            "    SELECT hadm_id, first_careunit\n"
            "    FROM (\n"
            "        SELECT hadm_id, first_careunit,\n"
            "               row_number() OVER (PARTITION BY hadm_id ORDER BY intime, stay_id)"
            " AS seq\n"
            "        FROM mimiciv_icu.icustays\n"
            "    )\n"
            "    WHERE seq = 1\n"
            ") AS fs ON fs.hadm_id = x.hadm_id\n"
        )
        careunit = "fs.first_careunit"
        stay_cols = ""
    demographics = ",\n".join(f"    {src} AS {name}" for name, src in _DEMOGRAPHIC_SOURCES.items())
    return (
        "SELECT\n"
        f"    {key_cols},\n"
        "    x.index_time,\n"
        "    x.end_time,\n"
        "    a.admittime,\n"
        "    a.dischtime,\n"
        "    a.hospital_expire_flag,\n"
        f"{demographics},\n"
        f"    {careunit} AS first_careunit,\n"
        f"{stay_cols}"
        "    p.anchor_age,\n"
        "    p.anchor_year,\n"
        "    p.anchor_year_group,\n"
        "    p.dod,\n"
        "    ld.last_dischtime\n"
        "FROM (\n"
        f"{_indent(inner)}\n"
        ") AS x\n"
        "LEFT JOIN mimiciv_hosp.admissions AS a ON a.hadm_id = x.hadm_id\n"
        "JOIN mimiciv_hosp.patients AS p ON p.subject_id = x.subject_id\n"
        f"{stay_join}"
        "LEFT JOIN (\n"
        "    SELECT subject_id, max(dischtime) AS last_dischtime\n"
        "    FROM mimiciv_hosp.admissions\n"
        "    GROUP BY subject_id\n"
        ") AS ld ON ld.subject_id = x.subject_id"
    )


# ---------------------------------------------------------------------------
# Criterion predicates (over the alias ``c``)
# ---------------------------------------------------------------------------

_AGE_EXPR = timesem.sql_age_at("c.anchor_age", "c.anchor_year", "c.index_time")


def _codeset_exists(
    ref: str,
    *,
    kind_table: str,
    position: str,
    hadm_expr: str,
) -> str:
    """``EXISTS`` over the billed codes of ``hadm_expr`` that are members of ``ref``
    (``meta.codeset_members``: ``system`` ``icd9`` / ``icd10`` <-> ``icd_version``)."""
    codeset_id, _, version = ref.partition("@")
    seq = "\n          AND d.seq_num = 1" if position == "primary" else ""
    return (
        "EXISTS (\n"
        "        SELECT 1\n"
        f"        FROM {kind_table} AS d\n"
        f"        JOIN {MEMBERS_TABLE} AS m\n"
        "          ON m.code = d.icd_code\n"
        "         AND m.system = CASE d.icd_version WHEN 9 THEN 'icd9' WHEN 10 THEN 'icd10' END\n"
        f"         AND m.codeset_id = {sql_str(codeset_id)}\n"
        f"         AND m.version = {sql_str(version)}\n"
        f"        WHERE d.hadm_id = {hadm_expr}{seq}\n"
        "    )"
    )


def _codeset_predicate(spec: CohortSpec, shape: _Shape, c: Criterion, kind: str) -> str:
    p = c.codeset
    assert p is not None
    table = "mimiciv_hosp.procedures_icd" if kind == "icd_px" else "mimiciv_hosp.diagnoses_icd"
    if p.lookback == "same_admission":
        if not shape.has_hadm:
            raise CompileError(f"{spec.ref}: {c.label}: same_admission needs an admission index")
        return _codeset_exists(p.ref, kind_table=table, position=p.position, hadm_expr="c.hadm_id")
    within = ""
    if p.lookback == "prior_days":
        within = (
            "\n          AND a2.admittime >= c.index_time - "
            f"to_days(CAST({p.prior_days} AS BIGINT))"
        )
    not_index = "\n          AND a2.hadm_id <> c.hadm_id" if shape.has_hadm else ""
    inner = _codeset_exists(p.ref, kind_table=table, position=p.position, hadm_expr="a2.hadm_id")
    return (
        "EXISTS (\n"
        "        SELECT 1\n"
        "        FROM mimiciv_hosp.admissions AS a2\n"
        "        WHERE a2.subject_id = c.subject_id\n"
        f"          AND a2.admittime < c.index_time{within}{not_index}\n"
        f"          AND {_indent(inner, 4).lstrip()}\n"
        "    )"
    )


def _phenotype_predicate(
    spec: CohortSpec, shape: _Shape, c: Criterion, phenotype_grain: str
) -> str:
    p = c.phenotype
    assert p is not None
    relation = _phenotype_relation(p.ref)
    ph_keys = {
        "subject": ("subject_id",),
        "hadm": ("subject_id", "hadm_id"),
        "icustay": ("subject_id", "hadm_id", "stay_id"),
    }.get(phenotype_grain, ("subject_id",))
    if p.when == "at":
        key = shape.finest_common_key(ph_keys)
        assert key is not None  # subject_id is always common
        onset = (
            "\n          AND (ph.onset_time IS NULL OR ph.onset_time <= c.index_time)"
            if key == "subject_id" and phenotype_grain != shape.grain.name
            else ""
        )
        return (
            "EXISTS (\n"
            "        SELECT 1\n"
            f"        FROM {relation} AS ph\n"
            f"        WHERE ph.{key} = c.{key}\n"
            f"          AND ph.flag{onset}\n"
            "    )"
        )
    if p.when == "before":
        return (
            "EXISTS (\n"
            "        SELECT 1\n"
            f"        FROM {relation} AS ph\n"
            "        WHERE ph.subject_id = c.subject_id\n"
            "          AND ph.flag\n"
            "          AND ph.onset_time < c.index_time\n"
            "    )"
        )
    hours = timesem.sql_hours_since("c.index_time", "ph.onset_time")
    return (
        "EXISTS (\n"
        "        SELECT 1\n"
        f"        FROM {relation} AS ph\n"
        "        WHERE ph.subject_id = c.subject_id\n"
        "          AND ph.flag\n"
        f"          AND abs({hours}) <= {p.hours!r}\n"
        "    )"
    )


def _concept_predicate(spec: CohortSpec, shape: _Shape, c: Criterion) -> str:
    p = c.concept
    assert p is not None
    key = shape.unit_key
    col = f"t.{_ident(p.column)}"
    if p.op in ("is_null", "is_not_null"):
        test = f"{col} IS NULL" if p.op == "is_null" else f"{col} IS NOT NULL"
    elif p.op in ("in", "not_in"):
        assert isinstance(p.value, list)
        values = ", ".join(_literal(v) for v in p.value)
        test = f"{col} {'IN' if p.op == 'in' else 'NOT IN'} ({values})"
    else:
        test = f"{col} {p.op} {_literal(p.value)}"
    window = ""
    if p.window is not None and p.time_column is not None:
        window = "\n          AND " + _window_predicate(
            "c.index_time", f"t.{_ident(p.time_column)}", p.window
        )
    return (
        "EXISTS (\n"
        "        SELECT 1\n"
        f"        FROM {p.table} AS t\n"
        f"        WHERE t.{key} = c.{key}\n"
        f"          AND {test}{window}\n"
        "    )"
    )


def _los_predicate(spec: CohortSpec, shape: _Shape, c: Criterion) -> str:
    p = c.los
    assert p is not None
    if p.of == "icu":
        if not shape.has_stay:
            raise CompileError(
                f"{spec.ref}: {c.label}: los of icu needs an index that yields an ICU stay "
                "(first_icu_stay / each_icustay / first_icu_stay_of_first_hadm)"
            )
        hours = timesem.sql_hours_since("c.stay_intime", "c.stay_outtime")
    else:
        hours = timesem.sql_hours_since("c.admittime", "c.dischtime")
    parts = []
    if p.min_hours is not None:
        parts.append(f"{hours} >= {p.min_hours!r}")
    if p.max_hours is not None:
        parts.append(f"{hours} < {p.max_hours!r}")
    return " AND ".join(parts)


def _data_availability_predicate(spec: CohortSpec, shape: _Shape, c: Criterion) -> str:
    from mimicwarehouse.schema.contract import load_contract

    p = c.data_availability
    assert p is not None
    contract = load_contract()
    try:
        table = contract.table(p.table)
    except (KeyError, ValueError):
        raise CompileError(f"{spec.ref}: {c.label}: {p.table} is not a contract table") from None
    if table.time_column is None:
        raise CompileError(f"{spec.ref}: {c.label}: {p.table} has no time column")
    columns = tuple(col.name for col in table.columns)
    key = shape.finest_common_key(columns)
    if key is None:
        raise CompileError(
            f"{spec.ref}: {c.label}: {p.table} carries none of the index keys "
            f"({', '.join(shape.keys_available)})"
        )
    window = p.window if p.window is not None else spec.observation_window
    items = (
        f"\n          AND t.itemid IN ({', '.join(str(i) for i in p.itemids)})" if p.itemids else ""
    )
    return (
        "(\n"
        "        SELECT count(*)\n"
        f"        FROM {p.table} AS t\n"
        f"        WHERE t.{key} = c.{key}{items}\n"
        f"          AND {_window_predicate('c.index_time', f't.{table.time_column}', window)}\n"
        f"    ) >= {p.min_rows}"
    )


def _prior_admissions_predicate(spec: CohortSpec, shape: _Shape, c: Criterion) -> str:
    p = c.prior_admissions
    assert p is not None
    not_index = "\n          AND a2.hadm_id <> c.hadm_id" if shape.has_hadm else ""
    lookback = (
        f"\n          AND a2.admittime >= c.index_time - to_days(CAST({p.lookback_days} AS BIGINT))"
        if p.lookback_days is not None
        else ""
    )
    count = (
        "(\n"
        "        SELECT count(*)\n"
        "        FROM mimiciv_hosp.admissions AS a2\n"
        "        WHERE a2.subject_id = c.subject_id\n"
        f"          AND a2.admittime < c.index_time{not_index}{lookback}\n"
        "    )"
    )
    parts = []
    if p.min is not None:
        parts.append(f"{count} >= {p.min}")
    if p.max is not None:
        parts.append(f"{count} <= {p.max}")
    return " AND ".join(parts)


def _demographic_predicate(c: Criterion) -> str:
    p = c.demographic
    assert p is not None
    return " AND ".join(
        f"c.{name} IN ({', '.join(sql_str(v) for v in values)})" for name, values in p.fields()
    )


def _age_predicate(c: Criterion) -> str:
    p = c.age
    assert p is not None
    parts = []
    if p.min is not None:
        parts.append(f"{_AGE_EXPR} >= {p.min!r}")
    if p.max is not None:
        parts.append(f"{_AGE_EXPR} < {p.max!r}")
    return " AND ".join(parts)


def _washout_predicate(spec: CohortSpec, shape: _Shape, washout: Washout) -> str:
    """The **exclusion** predicate of the washout (true = a prior event exists)."""
    days = (
        f"\n          AND e.{'admittime' if washout.rule == 'no_prior_hadm' else 'intime'} >= "
        f"c.index_time - to_days(CAST({washout.days} AS BIGINT))"
        if washout.days is not None
        else ""
    )
    if washout.rule == "no_prior_hadm":
        not_index = "\n          AND e.hadm_id <> c.hadm_id" if shape.has_hadm else ""
        codes = ""
        if washout.codeset is not None:
            inner = _codeset_exists(
                washout.codeset,
                kind_table="mimiciv_hosp.diagnoses_icd",
                position="any",
                hadm_expr="e.hadm_id",
            )
            codes = f"\n          AND {_indent(inner, 4).lstrip()}"
        return (
            "EXISTS (\n"
            "        SELECT 1\n"
            "        FROM mimiciv_hosp.admissions AS e\n"
            "        WHERE e.subject_id = c.subject_id\n"
            f"          AND e.admittime < c.index_time{not_index}{days}{codes}\n"
            "    )"
        )
    not_index = "\n          AND e.stay_id <> c.stay_id" if shape.has_stay else ""
    return (
        "EXISTS (\n"
        "        SELECT 1\n"
        "        FROM mimiciv_icu.icustays AS e\n"
        "        WHERE e.subject_id = c.subject_id\n"
        f"          AND e.intime < c.index_time{not_index}{days}\n"
        "    )"
    )


# ---------------------------------------------------------------------------
# The final projection
# ---------------------------------------------------------------------------


def _follow_up_columns(spec: CohortSpec) -> tuple[str, str]:
    """``(follow_up_end, censor_reason)`` expressions over the last step's alias ``c``."""
    rule = spec.follow_up.rule
    if not rule.censored:
        end = "c.dischtime"
        reason = (
            "CASE WHEN c.hospital_expire_flag = 1 THEN "
            f"{sql_str(REASON_DEATH)} WHEN c.dischtime IS NOT NULL THEN "
            f"{sql_str(REASON_DISCHARGE_ALIVE)} ELSE {sql_str(REASON_UNKNOWN)} END"
        )
        return end, reason
    horizon = rule.horizon_days or 0
    horizon_end = f"c.index_time + to_days(CAST({int(horizon)} AS BIGINT))"
    visibility_end = timesem.sql_dod_visibility_end("c.last_dischtime")
    end = f"least({horizon_end}, {visibility_end})"
    reason = (
        f"CASE WHEN c.dod IS NOT NULL AND c.dod <= CAST({end} AS DATE) "
        f"THEN {sql_str(REASON_DEATH)} "
        f"WHEN {horizon_end} <= {visibility_end} THEN {sql_str(REASON_HORIZON)} "
        f"ELSE {sql_str(REASON_DOD_VISIBILITY)} END"
    )
    return end, reason


def _cohort_sql(spec: CohortSpec, shape: _Shape, last: str) -> tuple[str, tuple[str, ...]]:
    keys = _grain_key_columns(shape)
    age = timesem.sql_age_at("c.anchor_age", "c.anchor_year", "c.index_time")
    follow_up_end, reason = _follow_up_columns(spec)
    window = spec.observation_window
    lines = [f"    c.{k}" for k in keys]
    lines += [
        "    c.index_time",
        f"    {timesem.sql_era_index('c.anchor_year_group')} AS era_index",
        f"    least({age}, {AGE_CAP}) AS age_at_index",
        f"    {timesem.sql_is_age_capped(age)} AS age_capped",
        f"    c.index_time + {_seconds_interval(window.start_h)} AS obs_start",
        f"    c.index_time + {_seconds_interval(window.end_h)} AS obs_end",
        f"    {follow_up_end} AS follow_up_end",
        f"    {reason} AS censor_reason",
        f"    {'true' if spec.custom else 'false'} AS custom_flag",
    ]
    order = ", ".join(f"c.{k}" for k in keys)
    sql = "SELECT\n" + ",\n".join(lines) + f"\nFROM {last} AS c\nORDER BY {order}"
    return sql, (*keys, *OUTPUT_COLUMNS)


# ---------------------------------------------------------------------------
# compile
# ---------------------------------------------------------------------------


def compile_spec(
    spec: CohortSpec,
    tier: str | None = None,
    *,
    codeset_kinds: dict[str, str] | None = None,
    phenotype_grains: dict[str, str] | None = None,
) -> CompiledCohort:
    """Compile ``spec`` into its CTE chain (module docstring). ``codeset_kinds`` maps each
    referenced code set (``id@version``) to its EP-40 ``kind`` (``icd_dx`` / ``icd_px``;
    ``icd_dx`` assumed when absent) and ``phenotype_grains`` each referenced phenotype to
    its grain (``subject`` assumed when absent) — the registry supplies both
    (:func:`compile_entry`). ``tier`` is recorded only: the SQL text is tier-independent
    (the build step binds the relations per tier)."""
    shape = _shape_of(spec)
    kinds = codeset_kinds or {}
    grains = phenotype_grains or {}
    ctes: list[tuple[str, str]] = []
    steps: list[Step] = []
    # icustays is always read: the index stay, or the admission's first stay (first_careunit)
    sources: set[str] = {
        shape.grain.source,
        "mimiciv_hosp.admissions",
        "mimiciv_hosp.patients",
        "mimiciv_icu.icustays",
    }
    warnings: list[str] = []

    ctes.append((STEP_BASE, _base_sql(spec, shape)))
    steps.append(
        Step(STEP_BASE, f"every {shape.grain.name} unit ({shape.grain.source})", "population")
    )
    ctes.append((STEP_IDX, _idx_sql(spec, shape)))
    steps.append(Step(STEP_IDX, f"index event {spec.index_event.render()}", "index"))
    if spec.index_event.phenotype_onset is not None:
        sources.add(_phenotype_relation(spec.index_event.phenotype_onset))
    if spec.index_event.concept_time is not None:
        sources.add(spec.index_event.concept_time.table)
    last = STEP_IDX
    if spec.era_filter:
        eras = ", ".join(sql_str(e) for e in spec.era_filter)
        ctes.append(
            (STEP_ERA, f"SELECT c.*\nFROM {last} AS c\nWHERE c.anchor_year_group IN ({eras})")
        )
        steps.append(Step(STEP_ERA, f"era filter {', '.join(spec.era_filter)}", "era"))
        last = STEP_ERA
    for position, (polarity, criterion) in enumerate(spec.criteria(), start=1):
        name = _step_name(position, criterion.label)
        kind = criterion.kind
        if kind == "custom_sql":
            assert criterion.custom_sql is not None
            custom_name = f"custom_{position:02d}"
            keys = _grain_key_columns(shape)
            ctes.append((custom_name, normalize_sql(criterion.custom_sql.sql)))
            on = " AND ".join(f"x.{k} = c.{k}" for k in keys)
            predicate = (
                f"EXISTS (\n        SELECT 1\n        FROM {custom_name} AS x\n"
                f"        WHERE {on}\n    )"
            )
        elif kind == "age":
            predicate = _age_predicate(criterion)
        elif kind == "demographic":
            predicate = _demographic_predicate(criterion)
        elif kind == "codeset":
            assert criterion.codeset is not None
            predicate = _codeset_predicate(
                spec, shape, criterion, kinds.get(criterion.codeset.ref, "icd_dx")
            )
            sources.add(MEMBERS_TABLE)
            sources.add(
                "mimiciv_hosp.procedures_icd"
                if kinds.get(criterion.codeset.ref) == "icd_px"
                else "mimiciv_hosp.diagnoses_icd"
            )
        elif kind == "phenotype":
            assert criterion.phenotype is not None
            predicate = _phenotype_predicate(
                spec, shape, criterion, grains.get(criterion.phenotype.ref, "subject")
            )
            sources.add(_phenotype_relation(criterion.phenotype.ref))
        elif kind == "concept":
            assert criterion.concept is not None
            predicate = _concept_predicate(spec, shape, criterion)
            sources.add(criterion.concept.table)
        elif kind == "los":
            predicate = _los_predicate(spec, shape, criterion)
        elif kind == "data_availability":
            assert criterion.data_availability is not None
            predicate = _data_availability_predicate(spec, shape, criterion)
            sources.add(criterion.data_availability.table)
        elif kind == "prior_admissions":
            predicate = _prior_admissions_predicate(spec, shape, criterion)
        else:  # pragma: no cover - the schema's kinds are exhausted above
            raise CompileError(f"{spec.ref}: unknown criterion kind {kind!r}")
        if polarity == "inclusion":
            where = f"coalesce({predicate}, false)"
        else:
            where = f"NOT coalesce({predicate}, false)"
        ctes.append((name, f"SELECT c.*\nFROM {last} AS c\nWHERE {where}"))
        steps.append(Step(name, f"{polarity} {criterion.label}", polarity, kind, criterion.custom))
        last = name
    if spec.washout.rule != "none":
        predicate = _washout_predicate(spec, shape, spec.washout)
        ctes.append(
            (STEP_WASHOUT, f"SELECT c.*\nFROM {last} AS c\nWHERE NOT coalesce({predicate}, false)")
        )
        steps.append(Step(STEP_WASHOUT, f"washout {spec.washout.render()}", "washout"))
        if spec.washout.codeset is not None:
            sources.add(MEMBERS_TABLE)
            sources.add("mimiciv_hosp.diagnoses_icd")
        last = STEP_WASHOUT
    cohort_sql, columns = _cohort_sql(spec, shape, last)
    ctes.append((STEP_COHORT, cohort_sql))
    steps.append(Step(STEP_COHORT, "cohort", "cohort"))

    with_clause = "WITH " + ",\n".join(f"{name} AS (\n{_indent(body)}\n)" for name, body in ctes)
    sql = f"{with_clause}\nSELECT *\nFROM {STEP_COHORT}"
    unions = "\nUNION ALL\n".join(
        f"SELECT {i} AS step_index, {sql_str(s.name)} AS step, count(*) AS n_units, "
        f"count(DISTINCT subject_id) AS n_subjects\nFROM {s.name}"
        for i, s in enumerate(steps)
    )
    attrition_sql = f"{with_clause}\n{unions}\nORDER BY 1"
    digest = hashlib.sha256(sql.encode("utf-8")).hexdigest()
    return CompiledCohort(
        ref=spec.ref,
        tier=tier,
        sql=sql,
        attrition_sql=attrition_sql,
        steps=tuple(steps),
        keys=_grain_key_columns(shape),
        columns=columns,
        sources=tuple(sorted(sources)),
        sql_sha256=digest,
        custom=spec.custom,
        warnings=tuple(warnings),
    )


def compile_entry(entry: Any, registry: Any, tier: str | None = None) -> CompiledCohort:
    """:func:`compile_spec` for a registry :class:`~mimicwarehouse.cohort.registry.Entry`:
    the referenced code sets' kinds and phenotypes' grains come from the registries the
    entry was resolved against."""
    spec = entry.spec
    kinds = {ref: registry.codesets.get(ref).codeset.kind for ref in spec.codeset_refs}
    grains = {ref: registry.phenotypes.get(ref).phenotype.grain for ref in spec.phenotype_refs}
    return compile_spec(spec, tier, codeset_kinds=kinds, phenotype_grains=grains)


def describe(compiled: CompiledCohort) -> str:
    """A one-paragraph, value-free description of a compiled chain (the CLI's
    ``--dry-run`` footer)."""
    custom = " (custom_sql present)" if compiled.custom else ""
    return (
        f"-- {compiled.ref}: {len(compiled.steps)} step(s) "
        f"({' -> '.join(compiled.step_names)}){custom}; keys {', '.join(compiled.keys)}; "
        f"sources {', '.join(compiled.sources)}; sql sha256 {compiled.sql_sha256[:12]}"
    )


__all__ = [
    "KEY_ORDER",
    "MEMBERS_TABLE",
    "OUTPUT_COLUMNS",
    "PHENOTYPES_SCHEMA",
    "REASON_DEATH",
    "REASON_DISCHARGE_ALIVE",
    "REASON_DOD_VISIBILITY",
    "REASON_HORIZON",
    "REASON_UNKNOWN",
    "STEP_BASE",
    "STEP_COHORT",
    "STEP_ERA",
    "STEP_IDX",
    "STEP_WASHOUT",
    "CompileError",
    "CompiledCohort",
    "Step",
    "compile_entry",
    "compile_spec",
    "describe",
    "sql_str",
]
