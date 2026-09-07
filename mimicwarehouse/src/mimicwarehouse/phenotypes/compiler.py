"""The phenotype compiler: a resolved :class:`~mimicwarehouse.phenotypes.spec.Phenotype`
-> one deterministic SQL statement (EP-41 item 2; DESIGN §8; D-19/D-20).

The statement is a CTE chain over the tier's staged tables (the catalog's own relation
names — ``mimiciv_hosp.*`` / ``mimiciv_icu.*`` / ``mimiciv_derived.*`` — so the same
text runs on the build connection's source views and on a tier catalog):

1. ``units`` — every unit of the grain (``patients`` / ``admissions`` / ``icustays``);
2. ``leaf_NN_<id>`` — one **event** CTE per leaf, temporal operands included, each
   yielding ``(subject_id, hadm_id, stay_id, event_time)`` (``NULL`` where the source has
   no such key); code sets are **inlined** (exact codes as ``IN`` lists, prefixes as
   ``LIKE``, drug names as ``contains`` / ``regexp_matches``), so the SQL is the definition;
3. ``mapped_NN_<id>`` — the leaf's events mapped onto the grain's keys: ``subject`` by
   ``subject_id``; ``hadm`` by ``hadm_id``, with a subject-level event that carries no
   ``hadm_id`` (an outpatient lab, an emar row) attached to the admission whose
   ``[admittime, dischtime]`` contains it; ``icustay`` by ``stay_id``, otherwise the ICU
   stay of the same admission / subject whose ``[intime, outtime]`` contains the event
   (timeless events — diagnoses, procedures — attach to every stay of their admission).
   A temporal leaf's ``mapped`` CTE joins its operands' mapped events on the grain keys
   under the relation and keeps the distinct ``a`` events;
4. ``unit_NN_<id>`` — one row per unit that satisfies the leaf: ``first_time`` /
   ``last_time`` / ``n_events`` (``HAVING`` the leaf's ``min_*`` thresholds);
5. ``reduced`` — ``units`` LEFT JOIN every criteria leaf's ``unit`` CTE: ``has_<id>``,
   ``t_<id>`` (first event), ``n_<id>`` (evidence count, 0 when absent);
6. the final ``SELECT <grain keys>, flag, onset_time, evidence_json[, <evidence …>] …
   ORDER BY <keys>`` — ``flag`` is the boolean tree over ``has_*``; ``onset_time`` the
   ``earliest`` / ``latest`` / ``first_of`` time over the **positive** leaves present
   (``least`` / ``greatest`` ignore NULLs), NULL when the flag is false; ``evidence_json``
   a compact JSON object ``{"<leaf id>": n_events, …}`` with keys sorted (kept short on
   purpose: sessions read phenotype views as subject-keyed, so a value must stay <= 64
   characters — EP-41 amendment, ledger P3C-5); then one **typed evidence column** per
   ``evidence`` entry of the concept leaves (EP-42): the leaf CTE carries the concept
   column, the unit CTE aggregates it over the unit's qualifying events (``first`` /
   ``last`` = ``arg_min`` / ``arg_max`` by event time, ``min`` / ``max``), and the final
   select fills units the leaf does not satisfy with the declared ``default`` (else
   NULL).

Concept leaves (EP-42) join the concept table to its key table and, when the leaf
declares a ``window``, keep only events whose ``time_column`` lies ``from_hours <= hours
since the anchor < to_hours`` — ``timesem.sql_hours_since(anchor, time)`` over
``icustays.intime`` (``stay_id``) or ``admissions.admittime`` (``hadm_id``). Lab leaves
compare the EP-39 harmonised value — the canonical unit for curated itemids, ``valuenum``
as recorded otherwise — with the conversions of the leaf's itemids **inlined**
(:func:`harmonised_value_sql`, the same arithmetic as ``mwh_harmonize``), so the
statement runs on any connection that sees the tier's relations and needs no macro.
Identical specs compile to identical text — ``tests/ep/golden/`` pins the packaged
definitions. No data access here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from mimicwarehouse.codesets.spec import CodeSetError, DrugMembers, Members
from mimicwarehouse.phenotypes.spec import (
    ConceptLeaf,
    DiagnosisLeaf,
    EvidenceColumn,
    LabLeaf,
    Leaf,
    MedicationLeaf,
    MicrobiologyLeaf,
    Node,
    Phenotype,
    PhenotypeError,
    ProcedureLeaf,
    TemporalLeaf,
)

if TYPE_CHECKING:  # pragma: no cover
    from mimicwarehouse.codesets.registry import Registry as CodeSetRegistry

#: The output columns of every materialised phenotype, after the grain keys.
OUTPUT_COLUMNS: tuple[str, ...] = ("flag", "onset_time", "evidence_json")
#: Grain -> (the key columns the unit table carries, the column the leaves join on).
GRAIN_KEYS: dict[str, tuple[tuple[str, ...], str]] = {
    "subject": (("subject_id",), "subject_id"),
    "hadm": (("subject_id", "hadm_id"), "hadm_id"),
    "icustay": (("subject_id", "hadm_id", "stay_id"), "stay_id"),
}
GRAIN_UNITS_SQL: dict[str, tuple[str, str]] = {
    "subject": ("mimiciv_hosp.patients", "SELECT subject_id FROM mimiciv_hosp.patients"),
    "hadm": ("mimiciv_hosp.admissions", "SELECT subject_id, hadm_id FROM mimiciv_hosp.admissions"),
    "icustay": (
        "mimiciv_icu.icustays",
        "SELECT subject_id, hadm_id, stay_id FROM mimiciv_icu.icustays",
    ),
}
ADMISSIONS = "mimiciv_hosp.admissions"
ICUSTAYS = "mimiciv_icu.icustays"


class CompileError(PhenotypeError):
    """The phenotype cannot be compiled (a reference of the wrong kind, an empty set, …)."""


@dataclass(frozen=True, slots=True)
class Compiled:
    """One compiled phenotype: the statement, its per-leaf event SQL, the tables read,
    the output columns (``evidence_columns`` = the typed evidence columns after
    ``evidence_json``) and the per-admission companion SQL (subject and icustay grains,
    with ``{relation}`` to fill in)."""

    ref: str
    grain: str
    sql: str
    leaf_sql: dict[str, str]
    sources: tuple[str, ...]
    columns: tuple[str, ...]
    hadm_companion_sql: str | None
    warnings: tuple[str, ...] = ()
    evidence_columns: tuple[str, ...] = ()


@dataclass(slots=True)
class _Events:
    """A leaf's event CTE and how its rows key into the grain."""

    index: str
    leaf: Leaf
    body: str
    timeless: bool
    hadm_nullable: bool
    stay_nullable: bool
    min_events: int = 1
    min_admissions: int = 1
    sources: list[str] = field(default_factory=list)
    evidence: tuple[EvidenceColumn, ...] = ()

    @property
    def evidence_names(self) -> tuple[str, ...]:
        """The ``ev_<name>`` columns the leaf's CTEs carry alongside ``event_time``."""
        return tuple(f"ev_{e.name}" for e in self.evidence)

    @property
    def cte(self) -> str:
        return f"leaf_{self.index}_{self.leaf.id}"

    @property
    def mapped(self) -> str:
        return f"mapped_{self.index}_{self.leaf.id}"

    @property
    def unit(self) -> str:
        return f"unit_{self.index}_{self.leaf.id}"


# ---------------------------------------------------------------------------
# SQL fragments
# ---------------------------------------------------------------------------


def sql_str(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def sql_value(value: bool | int | float | str) -> str:
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int | float):
        return repr(value)
    return sql_str(value)


def _in_list(values: list[str] | tuple[str, ...]) -> str:
    return ", ".join(sql_str(v) for v in values)


def _int_list(values: tuple[int, ...]) -> str:
    return ", ".join(str(v) for v in values)


def icd_predicate(alias: str, members: Members) -> str:
    """``(alias.icd_version = 9 AND (...)) OR (alias.icd_version = 10 AND (...))`` from a
    dual ICD set: exact codes as an ``IN`` list, prefixes as ``LIKE`` rules."""
    arms: list[str] = []
    for version, entries in ((9, members.icd9), (10, members.icd10)):
        if not entries:
            continue
        exact = [e.code for e in entries if e.match == "exact"]
        prefixes = [e.code for e in entries if e.match == "prefix"]
        parts: list[str] = []
        if exact:
            parts.append(f"{alias}.icd_code IN ({_in_list(exact)})")
        parts.extend(f"{alias}.icd_code LIKE {sql_str(p + '%')}" for p in prefixes)
        arms.append(f"({alias}.icd_version = {version} AND ({' OR '.join(parts)}))")
    if not arms:
        raise CompileError("an ICD code set without members cannot be compiled")
    return " OR ".join(arms)


def drug_predicate(column: str, drugs: DrugMembers) -> str:
    """The name rules of a drug set over ``column``: ``contains`` (case-insensitive
    substring, the mimic-code idiom), ``exact`` (the whole normalised name) or ``regex``
    (case-insensitive pattern)."""
    if drugs.match == "contains":
        parts = [f"contains(upper({column}), {sql_str(name)})" for name in drugs.names]
    elif drugs.match == "exact":
        parts = [f"upper(trim({column})) = {sql_str(name)}" for name in drugs.names]
    else:
        parts = [f"regexp_matches({column}, {sql_str(name)}, 'i')" for name in drugs.names]
    return " OR ".join(parts)


def _null_int(name: str) -> str:
    return f"CAST(NULL AS INTEGER) AS {name}"


# ---------------------------------------------------------------------------
# Leaf event CTEs
# ---------------------------------------------------------------------------


def _codeset(codesets: CodeSetRegistry, ref: str, kind: str, leaf_id: str) -> Members:
    try:
        cs = codesets.get(ref).codeset
    except CodeSetError as exc:
        raise CompileError(f"leaf {leaf_id}: {exc}") from None
    if cs.kind != kind:
        raise CompileError(f"leaf {leaf_id}: {ref} is a {cs.kind} set; expected {kind}")
    return cs.members


def _diagnosis(
    index: str, leaf: Leaf, payload: DiagnosisLeaf, codesets: CodeSetRegistry
) -> _Events:
    members = _codeset(codesets, payload.codeset, "icd_dx", leaf.id)
    where = f"({icd_predicate('d', members)})"
    if payload.position == "primary":
        where += "\n    AND d.seq_num = 1"
    body = (
        f"SELECT d.subject_id, d.hadm_id, {_null_int('stay_id')}, a.dischtime AS event_time\n"
        "  FROM mimiciv_hosp.diagnoses_icd AS d\n"
        f"  JOIN {ADMISSIONS} AS a ON a.hadm_id = d.hadm_id\n"
        f"  WHERE {where}"
    )
    return _Events(
        index,
        leaf,
        body,
        timeless=True,
        hadm_nullable=False,
        stay_nullable=True,
        min_admissions=payload.min_admissions,
        sources=["mimiciv_hosp.diagnoses_icd", ADMISSIONS],
    )


def _procedure(
    index: str, leaf: Leaf, payload: ProcedureLeaf, codesets: CodeSetRegistry
) -> _Events:
    members = _codeset(codesets, payload.codeset, "icd_px", leaf.id)
    body = (
        f"SELECT p.subject_id, p.hadm_id, {_null_int('stay_id')}, "
        "CAST(p.chartdate AS TIMESTAMP) AS event_time\n"
        "  FROM mimiciv_hosp.procedures_icd AS p\n"
        f"  WHERE ({icd_predicate('p', members)})"
    )
    return _Events(
        index,
        leaf,
        body,
        timeless=True,
        hadm_nullable=False,
        stay_nullable=True,
        sources=["mimiciv_hosp.procedures_icd"],
    )


def _medication(
    index: str, leaf: Leaf, payload: MedicationLeaf, codesets: CodeSetRegistry
) -> _Events:
    members = _codeset(codesets, payload.codeset, "drug", leaf.id)
    if payload.source == "inputevents":
        if not members.itemids:
            raise CompileError(
                f"leaf {leaf.id}: source inputevents needs ICU itemids in {payload.codeset}"
            )
        body = (
            "SELECT i.subject_id, i.hadm_id, i.stay_id, i.starttime AS event_time\n"
            "  FROM mimiciv_icu.inputevents AS i\n"
            f"  WHERE i.itemid IN ({_int_list(members.itemids)})"
        )
        return _Events(
            index,
            leaf,
            body,
            timeless=False,
            hadm_nullable=False,
            stay_nullable=True,
            min_events=payload.min_orders,
            sources=["mimiciv_icu.inputevents"],
        )
    assert members.drugs is not None  # the drug kind requires it
    if payload.source == "emar":
        body = (
            f"SELECT e.subject_id, e.hadm_id, {_null_int('stay_id')}, e.charttime AS event_time\n"
            "  FROM mimiciv_hosp.emar AS e\n"
            f"  WHERE ({drug_predicate('e.medication', members.drugs)})"
        )
        return _Events(
            index,
            leaf,
            body,
            timeless=False,
            hadm_nullable=True,
            stay_nullable=True,
            min_events=payload.min_orders,
            sources=["mimiciv_hosp.emar"],
        )
    body = (
        f"SELECT p.subject_id, p.hadm_id, {_null_int('stay_id')}, p.starttime AS event_time\n"
        "  FROM mimiciv_hosp.prescriptions AS p\n"
        f"  WHERE ({drug_predicate('p.drug', members.drugs)})"
    )
    return _Events(
        index,
        leaf,
        body,
        timeless=False,
        hadm_nullable=False,
        stay_nullable=True,
        min_events=payload.min_orders,
        sources=["mimiciv_hosp.prescriptions"],
    )


def harmonised_value_sql(itemids: tuple[int, ...]) -> tuple[str, bool]:
    """``(expression over l.valuenum / l.unit_norm, needs_unit_norm)``: the EP-39
    harmonisation of ``itemids`` **inlined** — for every curated itemid with a non-identity
    accepted unit, a ``CASE`` over the normalised unit string applying the affine
    conversion; ``l.valuenum`` unchanged otherwise (an uncurated itemid, an unknown unit) —
    exactly ``mwh_harmonize(...).value_canonical``. Inlined on purpose: the macro family
    re-binds its nested 63-arm ``CASE`` per call and scanned the fixture's labevents in
    tens of seconds (``docs/gotchas.md`` §1); the inline form touches only the leaf's
    itemids and normalises the unit once per row."""
    from mimicwarehouse.units import load_catalogue

    by_itemid = load_catalogue().by_itemid()
    arms: list[str] = []
    for itemid in itemids:
        item = by_itemid.get(itemid)
        if item is None:
            continue
        branches = [
            f"WHEN {sql_str(norm)} THEN {affine.sql('l.valuenum')}"
            for norm, affine in sorted(item.conversions().items())
            if not affine.is_identity
        ]
        if branches:
            arms.append(
                f"WHEN l.itemid = {itemid} THEN (CASE l.unit_norm {' '.join(branches)} "
                "ELSE l.valuenum END)"
            )
    if not arms:
        return "l.valuenum", False
    return "(CASE " + " ".join(arms) + " ELSE l.valuenum END)", True


def _lab(index: str, leaf: Leaf, payload: LabLeaf, codesets: CodeSetRegistry) -> _Events:
    from mimicwarehouse.units import sql_normalize_unit

    itemids = payload.itemids
    if payload.codeset is not None:
        itemids = _codeset(codesets, payload.codeset, "itemid", leaf.id).itemids
    if not itemids:
        raise CompileError(f"leaf {leaf.id}: no itemids to compare")
    harmonised, needs_norm = harmonised_value_sql(itemids)
    columns = "subject_id, hadm_id, charttime, itemid, valuenum"
    if needs_norm:
        columns += f", {sql_normalize_unit('valueuom')} AS unit_norm"
    body = (
        f"SELECT l.subject_id, l.hadm_id, {_null_int('stay_id')}, l.charttime AS event_time\n"
        f"  FROM (SELECT {columns}\n"
        "        FROM mimiciv_hosp.labevents\n"
        f"        WHERE itemid IN ({_int_list(itemids)}) AND valuenum IS NOT NULL) AS l\n"
        f"  WHERE {harmonised} {payload.op} {sql_value(payload.threshold)}"
    )
    return _Events(
        index,
        leaf,
        body,
        timeless=False,
        hadm_nullable=True,
        stay_nullable=True,
        min_events=payload.min_count,
        sources=["mimiciv_hosp.labevents"],
    )


def _microbiology(index: str, leaf: Leaf, payload: MicrobiologyLeaf) -> _Events:
    parts: list[str] = []
    if payload.spec_itemids:
        parts.append(f"m.spec_itemid IN ({_int_list(payload.spec_itemids)})")
    if payload.org_itemids:
        parts.append(f"m.org_itemid IN ({_int_list(payload.org_itemids)})")
    if payload.positive_only:
        parts.append("m.org_itemid IS NOT NULL")
    body = (
        f"SELECT m.subject_id, m.hadm_id, {_null_int('stay_id')}, "
        "coalesce(m.charttime, m.chartdate) AS event_time\n"
        "  FROM mimiciv_hosp.microbiologyevents AS m\n"
        f"  WHERE {' AND '.join(parts)}"
    )
    return _Events(
        index,
        leaf,
        body,
        timeless=False,
        hadm_nullable=True,
        stay_nullable=True,
        sources=["mimiciv_hosp.microbiologyevents"],
    )


def _window_predicate(payload: ConceptLeaf, anchor: str) -> str:
    """``AND from <= hours since the anchor < to`` (EP-42; ``timesem.sql_hours_since``)."""
    if payload.window is None:
        return ""
    from mimicwarehouse.timesem import sql_hours_since

    hours = sql_hours_since(anchor, f"c.{payload.time_column}")
    return (
        f"\n    AND {hours} >= {sql_value(payload.window.from_hours)}"
        f"\n    AND {hours} < {sql_value(payload.window.to_hours)}"
    )


def _evidence_select(payload: ConceptLeaf) -> str:
    return "".join(f", c.{e.column} AS ev_{e.name}" for e in payload.evidence)


def _concept(index: str, leaf: Leaf, payload: ConceptLeaf) -> _Events:
    predicate = f"c.{payload.column} {payload.op} {sql_value(payload.value)}"
    evidence = _evidence_select(payload)
    if payload.key == "stay_id":
        time = f"c.{payload.time_column}" if payload.time_column else "s.intime"
        body = (
            f"SELECT s.subject_id, s.hadm_id, c.stay_id, {time} AS event_time{evidence}\n"
            f"  FROM {payload.table} AS c\n"
            f"  JOIN {ICUSTAYS} AS s ON s.stay_id = c.stay_id\n"
            f"  WHERE {predicate}{_window_predicate(payload, 's.intime')}"
        )
        return _Events(
            index,
            leaf,
            body,
            timeless=payload.time_column is None,
            hadm_nullable=False,
            stay_nullable=False,
            sources=[payload.table, ICUSTAYS],
            evidence=payload.evidence,
        )
    if payload.key == "hadm_id":
        time = f"c.{payload.time_column}" if payload.time_column else "a.admittime"
        body = (
            f"SELECT a.subject_id, c.hadm_id, {_null_int('stay_id')}, {time} AS event_time"
            f"{evidence}\n"
            f"  FROM {payload.table} AS c\n"
            f"  JOIN {ADMISSIONS} AS a ON a.hadm_id = c.hadm_id\n"
            f"  WHERE {predicate}{_window_predicate(payload, 'a.admittime')}"
        )
        return _Events(
            index,
            leaf,
            body,
            timeless=payload.time_column is None,
            hadm_nullable=False,
            stay_nullable=True,
            sources=[payload.table, ADMISSIONS],
            evidence=payload.evidence,
        )
    time = f"c.{payload.time_column}" if payload.time_column else "CAST(NULL AS TIMESTAMP)"
    body = (
        f"SELECT c.subject_id, {_null_int('hadm_id')}, {_null_int('stay_id')}, "
        f"{time} AS event_time{evidence}\n"
        f"  FROM {payload.table} AS c\n"
        f"  WHERE {predicate}"
    )
    return _Events(
        index,
        leaf,
        body,
        timeless=payload.time_column is None,
        hadm_nullable=True,
        stay_nullable=True,
        sources=[payload.table],
        evidence=payload.evidence,
    )


def _events_of(index: str, leaf: Leaf, codesets: CodeSetRegistry) -> _Events:
    payload = leaf.payload
    if isinstance(payload, DiagnosisLeaf):
        return _diagnosis(index, leaf, payload, codesets)
    if isinstance(payload, ProcedureLeaf):
        return _procedure(index, leaf, payload, codesets)
    if isinstance(payload, MedicationLeaf):
        return _medication(index, leaf, payload, codesets)
    if isinstance(payload, LabLeaf):
        return _lab(index, leaf, payload, codesets)
    if isinstance(payload, MicrobiologyLeaf):
        return _microbiology(index, leaf, payload)
    if isinstance(payload, ConceptLeaf):
        return _concept(index, leaf, payload)
    raise CompileError(f"leaf {leaf.id}: kind {leaf.kind} has no event compiler")


# ---------------------------------------------------------------------------
# Grain mapping, units, reduction
# ---------------------------------------------------------------------------


def _mapping_sql(grain: str, ev: _Events) -> tuple[str, str | None]:
    """``(the mapped CTE body, the source table the mapping joins or None)``; the
    leaf's ``ev_*`` evidence columns ride along with ``event_time``."""
    plain = "".join(f", {c}" for c in ev.evidence_names)
    aliased = "".join(f", e.{c}" for c in ev.evidence_names)
    if grain == "subject":
        return f"SELECT subject_id, hadm_id, event_time{plain}\n  FROM {ev.cte}", None
    if grain == "hadm":
        if not ev.hadm_nullable:
            return f"SELECT subject_id, hadm_id, event_time{plain}\n  FROM {ev.cte}", None
        return (
            "SELECT e.subject_id, coalesce(e.hadm_id, w.hadm_id) AS hadm_id, e.event_time"
            f"{aliased}\n"
            f"  FROM {ev.cte} AS e\n"
            f"  LEFT JOIN {ADMISSIONS} AS w\n"
            "    ON e.hadm_id IS NULL AND w.subject_id = e.subject_id\n"
            "    AND e.event_time >= w.admittime AND e.event_time <= w.dischtime",
            ADMISSIONS,
        )
    if not ev.stay_nullable:
        return f"SELECT subject_id, hadm_id, stay_id, event_time{plain}\n  FROM {ev.cte}", None
    window = "TRUE" if ev.timeless else "(e.event_time >= s.intime AND e.event_time <= s.outtime)"
    return (
        "SELECT e.subject_id, coalesce(e.hadm_id, s.hadm_id) AS hadm_id, "
        f"coalesce(e.stay_id, s.stay_id) AS stay_id, e.event_time{aliased}\n"
        f"  FROM {ev.cte} AS e\n"
        f"  LEFT JOIN {ICUSTAYS} AS s\n"
        "    ON e.stay_id IS NULL AND s.subject_id = e.subject_id\n"
        "    AND (e.hadm_id IS NULL OR s.hadm_id = e.hadm_id)\n"
        f"    AND {window}",
        ICUSTAYS,
    )


def _mapped_columns(grain: str) -> tuple[str, ...]:
    return {
        "subject": ("subject_id", "hadm_id"),
        "hadm": ("subject_id", "hadm_id"),
        "icustay": ("subject_id", "hadm_id", "stay_id"),
    }[grain]


def _temporal_sql(grain: str, ev: _Events, a: _Events, b: _Events, payload: TemporalLeaf) -> str:
    key = GRAIN_KEYS[grain][1]
    if payload.relation == "before":
        relation = "a.event_time < b.event_time"
    elif payload.relation == "after":
        relation = "a.event_time > b.event_time"
    else:
        assert payload.hours is not None
        seconds = round(payload.hours * 3600)
        relation = f"abs(date_diff('second', a.event_time, b.event_time)) <= {seconds}"
    columns = ", ".join(f"a.{c}" for c in _mapped_columns(grain))
    return (
        f"SELECT DISTINCT {columns}, a.event_time\n"
        f"  FROM {a.mapped} AS a\n"
        f"  JOIN {b.mapped} AS b ON b.{key} = a.{key}\n"
        f"    AND b.{key} IS NOT NULL AND {relation}"
    )


def _evidence_agg(e: EvidenceColumn) -> str:
    """The per-unit aggregate of one evidence column over the leaf's qualifying events."""
    column = f"ev_{e.name}"
    if e.agg == "first":
        return f"arg_min({column}, event_time) AS {column}"
    if e.agg == "last":
        return f"arg_max({column}, event_time) AS {column}"
    return f"{e.agg}({column}) AS {column}"


def _unit_sql(grain: str, ev: _Events) -> str:
    key = GRAIN_KEYS[grain][1]
    having = [f"count(*) >= {ev.min_events}"]
    if ev.min_admissions > 1:
        having.append(f"count(DISTINCT hadm_id) >= {ev.min_admissions}")
    evidence = "".join(f", {_evidence_agg(e)}" for e in ev.evidence)
    return (
        f"SELECT {key}, min(event_time) AS first_time, max(event_time) AS last_time, "
        f"count(*) AS n_events{evidence}\n"
        f"  FROM {ev.mapped}\n"
        f"  WHERE {key} IS NOT NULL\n"
        f"  GROUP BY {key}\n"
        f"  HAVING {' AND '.join(having)}"
    )


def _flag_expr(node: Node) -> str:
    if node.op == "leaf":
        assert node.leaf is not None
        return f"has_{node.leaf.id}"
    if node.op == "not":
        return f"(NOT {_flag_expr(node.children[0])})"
    joiner = " AND " if node.op == "all" else " OR "
    return "(" + joiner.join(_flag_expr(c) for c in node.children) + ")"


def _onset_expr(phenotype: Phenotype) -> str:
    onset = phenotype.onset
    if onset.rule == "first_of":
        leaves = list(onset.leaves)
        fn = "least"
    else:
        leaves = list(phenotype.positive_leaf_ids)
        fn = "least" if onset.rule == "earliest" else "greatest"
    if not leaves:
        return "CAST(NULL AS TIMESTAMP)"
    if len(leaves) == 1:
        return f"t_{leaves[0]}"
    return f"{fn}({', '.join(f't_{leaf}' for leaf in leaves)})"


def _evidence_expr(leaf_ids: list[str]) -> str:
    parts: list[str] = []
    for i, leaf_id in enumerate(sorted(leaf_ids)):
        prefix = "{" if i == 0 else ","
        parts.append(
            f"{sql_str(prefix + chr(34) + leaf_id + chr(34) + ':')} || CAST(n_{leaf_id} AS VARCHAR)"
        )
    return " || ".join(parts) + " || '}'"


def _cte(name: str, body: str) -> str:
    return f"{name} AS (\n  {body}\n)"


# ---------------------------------------------------------------------------
# The compiler
# ---------------------------------------------------------------------------


def compile_phenotype(
    phenotype: Phenotype,
    codesets: CodeSetRegistry,
    *,
    resolved: dict[str, str] | None = None,
    concepts: dict[str, str] | None = None,
) -> Compiled:
    """Compile ``phenotype`` (module docstring). ``resolved`` (reference -> code-set
    ``def_hash``) and ``concepts`` (concept table -> executed-SQL sha256, EP-42) only
    decorate the header comment; the statement itself is a pure function of the
    phenotype and the code-set members."""
    grain = phenotype.grain
    if grain not in GRAIN_KEYS:
        raise CompileError(f"grain {grain!r} has no compiler (subject | hadm | icustay)")
    key_columns, join_key = GRAIN_KEYS[grain]
    units_source, units_sql = GRAIN_UNITS_SQL[grain]
    warnings: list[str] = []

    # 1. every leaf (temporal operands included) gets an index in document order
    ordered: list[tuple[Leaf, bool]] = list(phenotype.all_leaves())
    events: dict[str, _Events] = {}
    temporal: list[tuple[_Events, TemporalLeaf]] = []
    for position, (leaf, _neg) in enumerate(ordered, start=1):
        index = f"{position:02d}"
        if leaf.kind == "temporal":
            payload = leaf.payload
            ev = _Events(index, leaf, "", timeless=False, hadm_nullable=False, stay_nullable=False)
            events[leaf.id] = ev
            temporal.append((ev, payload))
            continue
        events[leaf.id] = _events_of(index, leaf, codesets)

    ctes: list[str] = [_cte("units", units_sql)]
    sources: list[str] = [units_source]
    leaf_sql: dict[str, str] = {}
    # 2. + 3. event CTEs and their grain mapping (operands before the temporal leaf that
    # reads them: document order already guarantees it)
    for leaf, _neg in ordered:
        ev = events[leaf.id]
        if leaf.kind == "temporal":
            continue
        ctes.append(_cte(ev.cte, ev.body))
        leaf_sql[leaf.id] = ev.body
        mapped_sql, mapping_source = _mapping_sql(grain, ev)
        ctes.append(_cte(ev.mapped, mapped_sql))
        sources.extend(ev.sources)
        if mapping_source is not None:
            sources.append(mapping_source)
    for ev, payload in temporal:
        a = events[payload.a.id]
        b = events[payload.b.id]
        body = _temporal_sql(grain, ev, a, b, payload)
        ctes.append(_cte(ev.mapped, body))
        leaf_sql[ev.leaf.id] = body
        if a.timeless or b.timeless:
            warnings.append(
                f"leaf {ev.leaf.id}: a timeless operand (diagnosis / procedure) enters the "
                "relation with its proxy time (dischtime / chartdate)"
            )
    # 4. one unit CTE per criteria leaf (document order)
    criteria = phenotype.criteria_leaves
    for leaf in criteria:
        ev = events[leaf.id]
        ctes.append(_cte(ev.unit, _unit_sql(grain, ev)))
    # 5. the reduction
    select = [f"u.{c}" for c in key_columns]
    joins: list[str] = []
    evidence_columns: list[tuple[str, EvidenceColumn]] = []
    for leaf in criteria:
        ev = events[leaf.id]
        alias = f"l{ev.index}"
        select.append(f"({alias}.{join_key} IS NOT NULL) AS has_{leaf.id}")
        select.append(f"{alias}.first_time AS t_{leaf.id}")
        select.append(f"coalesce({alias}.n_events, 0) AS n_{leaf.id}")
        for e in ev.evidence:
            select.append(f"{alias}.ev_{e.name} AS ev_{e.name}")
            evidence_columns.append((leaf.id, e))
        joins.append(f"  LEFT JOIN {ev.unit} AS {alias} ON {alias}.{join_key} = u.{join_key}")
    reduced = "SELECT\n    " + ",\n    ".join(select) + "\n  FROM units AS u\n" + "\n".join(joins)
    ctes.append(_cte("reduced", reduced))
    # 6. the final select
    flag = _flag_expr(phenotype.criteria)
    outputs = [*key_columns, f"{flag} AS flag"]
    columns = [*key_columns, "flag"]
    if phenotype.outputs.onset_time:
        outputs.append(f"CASE WHEN {flag} THEN {_onset_expr(phenotype)} END AS onset_time")
        columns.append("onset_time")
    if phenotype.outputs.evidence:
        outputs.append(f"{_evidence_expr([leaf.id for leaf in criteria])} AS evidence_json")
        columns.append("evidence_json")
    for _leaf_id, e in evidence_columns:
        if e.default is not None:
            outputs.append(f"coalesce(ev_{e.name}, {sql_value(e.default)}) AS {e.name}")
        else:
            outputs.append(f"ev_{e.name} AS {e.name}")
        columns.append(e.name)
    refs = ", ".join(
        f"{ref}={(resolved or {}).get(ref, '?')[:12]}" for ref in phenotype.codeset_refs
    )
    header = (
        f"-- phenotype {phenotype.ref} (grain {grain}); compiled by "
        "mimicwarehouse.phenotypes.compiler (EP-41)\n"
        f"-- criteria: {phenotype.criteria.render()}; onset: {phenotype.onset.canonical()}\n"
        f"-- references: {refs or '(none)'}\n"
    )
    if phenotype.parameters:
        listed = ", ".join(f"{k}={v!r}" for k, v in sorted(phenotype.parameters.items()))
        header += f"-- parameters: {listed}\n"
    pinned = [t for t in phenotype.concept_tables if concepts and t in concepts]
    if pinned:
        assert concepts is not None
        listed = ", ".join(f"{t}={concepts[t][:12]}" for t in pinned)
        header += f"-- concepts: {listed}\n"
    sql = (
        header
        + "WITH\n"
        + ",\n".join(ctes)
        + "\nSELECT\n  "
        + ",\n  ".join(outputs)
        + "\nFROM reduced\nORDER BY "
        + ", ".join(key_columns)
    )
    if grain == "subject":
        companion = hadm_companion_sql("{relation}", has_onset=phenotype.outputs.onset_time)
    elif grain == "icustay":
        companion = icustay_hadm_companion_sql("{relation}", has_onset=phenotype.outputs.onset_time)
    else:
        companion = None
    return Compiled(
        ref=phenotype.ref,
        grain=grain,
        sql=sql,
        leaf_sql=leaf_sql,
        sources=tuple(dict.fromkeys(sources)),
        columns=tuple(columns),
        hadm_companion_sql=companion,
        warnings=tuple(dict.fromkeys(warnings)),
        evidence_columns=tuple(e.name for _l, e in evidence_columns),
    )


def hadm_companion_sql(relation: str, *, has_onset: bool = True) -> str:
    """The per-admission companion of a ``subject``-grain phenotype: every admission
    flagged when the subject's onset lies at or before its ``dischtime`` (the phenotype is
    prevalent by that discharge); ``relation`` is the materialised phenotype's relation
    (a view name or a ``read_parquet`` call). Without an ``onset_time`` column the
    subject's flag carries over to every admission."""
    if has_onset:
        condition = "p.flag AND p.onset_time <= a.dischtime"
        onset = (
            "CASE WHEN p.flag AND p.onset_time <= a.dischtime THEN p.onset_time END AS onset_time"
        )
    else:
        condition = "p.flag"
        onset = "CAST(NULL AS TIMESTAMP) AS onset_time"
    return (
        "SELECT a.subject_id, a.hadm_id,\n"
        f"  coalesce({condition}, FALSE) AS flag,\n"
        f"  {onset}\n"
        f"FROM {ADMISSIONS} AS a\n"
        f"LEFT JOIN {relation} AS p ON p.subject_id = a.subject_id"
    )


def icustay_hadm_companion_sql(relation: str, *, has_onset: bool = True) -> str:
    """The per-admission companion of an ``icustay``-grain phenotype (EP-42): one row
    per admission **with at least one ICU stay** (the phenotype is only assessable
    there), ``flag`` = any of its stays is flagged, ``onset_time`` = the earliest onset
    among the flagged stays, ``n_stays`` = the admission's ICU stays. The sepsis-3 vs
    explicit-code cross-tab joins this companion to the ``hadm``-grain phenotype."""
    onset = (
        "min(p.onset_time) FILTER (WHERE p.flag) AS onset_time"
        if has_onset
        else "CAST(NULL AS TIMESTAMP) AS onset_time"
    )
    return (
        "SELECT a.subject_id, a.hadm_id,\n"
        "  coalesce(bool_or(p.flag), FALSE) AS flag,\n"
        f"  {onset},\n"
        "  count(*) AS n_stays\n"
        f"FROM {ADMISSIONS} AS a\n"
        f"JOIN {relation} AS p ON p.hadm_id = a.hadm_id\n"
        "GROUP BY a.subject_id, a.hadm_id"
    )


def describe(compiled: Compiled) -> dict[str, Any]:
    """A JSON-able description (no SQL text) for ``--json`` outputs."""
    return {
        "ref": compiled.ref,
        "grain": compiled.grain,
        "columns": list(compiled.columns),
        "evidence_columns": list(compiled.evidence_columns),
        "sources": list(compiled.sources),
        "leaves": list(compiled.leaf_sql),
        "sql_chars": len(compiled.sql),
        "warnings": list(compiled.warnings),
    }


__all__ = [
    "ADMISSIONS",
    "GRAIN_KEYS",
    "GRAIN_UNITS_SQL",
    "ICUSTAYS",
    "OUTPUT_COLUMNS",
    "CompileError",
    "Compiled",
    "compile_phenotype",
    "describe",
    "drug_predicate",
    "hadm_companion_sql",
    "harmonised_value_sql",
    "icd_predicate",
    "icustay_hadm_companion_sql",
    "sql_str",
    "sql_value",
]
