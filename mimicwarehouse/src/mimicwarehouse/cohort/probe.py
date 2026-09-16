"""Tier-level checks of a cohort spec through ``safe_query`` (EP-46 items 2 and 5; the
EP-33 amendment's level-degeneracy policy, spec-level half; GOVERNANCE §4/§5; D-31/D-33).

``mwh cohort validate <ref> --tier <t>`` runs two checks that need a tier catalog, both
audited, aggregate-only reads:

* :func:`check_references` — every code set the spec names must be compiled on the tier
  (``meta.codesets``, EP-40) and every phenotype built (``meta.phenotype_versions``,
  EP-41) **with the def_hash the registry resolved** — a stale compile is named with its
  remedy. Registry reads (``meta.*``), so no count column is needed (EP-33 B1c).
* :func:`run_probe` — the **degeneracy probe**: for every column of the spec's
  ``degeneracy_probe`` (the tracer's four by default), the level x outcome cross-tab
  over the spec's index population — the grain's index-event rule (``timesem``), joined
  to the admission and the patient, under the era filter — with ``count(*) AS n`` and
  ``count(*) FILTER (WHERE event) AS n_events`` per level. A level with ``n_events = 0``
  (zero-event) or ``n_events = n`` (all-event) has no finite maximum-likelihood
  coefficient in a model that conditions on it (EP-31 lesson); the tracer excludes such
  rows and **names** them, and EP-79 inherits the model-side half. The probe reports the
  levels as **suppressed aggregates**: ``safe_query`` withholds every level whose counts
  fall in ``1 .. k-1`` (k = 11 on the credentialed tiers), so a small level comes back as
  "not assessable" rather than as a count. The population is the index population
  *before* the inclusion / exclusion criteria (the compiler is EP-47's; EP-47 re-runs the
  probe over the compiled cohort), and the event is the follow-up outcome:
  ``hospital_expire_flag = 1`` for ``in_hospital_mortality``, ``dod`` within the horizon
  of the index for the censored outcomes (the ``dod`` visibility rule leaves later deaths
  unrecorded, so the horizon alone is the observable event). A ``phenotype_onset`` /
  ``concept_time`` index has no population until it is compiled, so the probe is skipped
  with a warning.

Everything returned or printed is a level label (public vocabulary), counts and audit
ids — never a row. Import budget: not on the ``mwh`` start-up path; ``safe`` and polars
load inside the functions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from mimicwarehouse.cohort.registry import Entry
from mimicwarehouse.cohort.spec import PROBE_COLUMNS, CohortSpec, FollowUp

if TYPE_CHECKING:  # pragma: no cover
    from mimicwarehouse.config import Settings
    from mimicwarehouse.safe import SafeResult

#: Audit actor of the probe's ``safe_query`` calls.
ACTOR = "cohort"
#: The verdicts of one level.
ZERO_EVENT = "zero-event"
ALL_EVENT = "all-event"
OK = "ok"
#: How a NULL level is rendered.
NULL_LEVEL = "(null)"
#: Row cap for the registry reads (``meta.codesets`` / ``meta.phenotype_versions``).
REGISTRY_ROW_CAP = 10_000


def _sql_str(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


# ---------------------------------------------------------------------------
# Population + probe SQL
# ---------------------------------------------------------------------------


def event_sql(follow_up: FollowUp) -> str:
    """The boolean event expression of the follow-up outcome over the population's
    ``a`` (admissions), ``p`` (patients) and ``idx`` (index relation) aliases."""
    rule = follow_up.rule
    if not rule.censored:
        return "a.hospital_expire_flag = 1"
    horizon = rule.horizon_days or 0
    return (
        f"p.dod IS NOT NULL AND p.dod <= CAST(idx.index_time + INTERVAL {int(horizon)} DAY AS DATE)"
    )


def population_sql(spec: CohortSpec, cohort_relation: str | None = None) -> str | None:
    """The probe's population as one SELECT (module docstring): the probe columns plus
    ``event``. Without ``cohort_relation`` it is the **index population** of a
    rule-based spec (``None`` for a ``phenotype_onset`` / ``concept_time`` index, which
    has none before it is compiled); with it — the session view of the compiled cohort,
    ``marts.cohort_<id>_v<major>`` (EP-47) — it is the **compiled cohort**: the view's
    keys and ``index_time`` joined the same way."""
    grain = spec.grain_entry
    has_stay = "stay_id" in grain.keys or spec.index_event.rule in (
        "first_icu_stay",
        "each_icustay",
        "first_icu_stay_of_first_hadm",
    )
    if cohort_relation is not None:
        keys = "subject_id, hadm_id, stay_id" if has_stay else "subject_id, hadm_id"
        index_sql = f"SELECT {keys}, index_time\nFROM {cohort_relation}"
    elif spec.index_event.rule is None:
        return None
    else:
        index_sql = grain.index_event_sql(spec.index_event.rule)
    if has_stay:
        careunit = "i.first_careunit"
        stay_join = "JOIN mimiciv_icu.icustays AS i ON i.stay_id = idx.stay_id\n"
    else:
        careunit = "fs.first_careunit"
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
            ") AS fs ON fs.hadm_id = idx.hadm_id\n"
        )
    columns = {
        "gender": "p.gender",
        "admission_type": "a.admission_type",
        "admission_location": "a.admission_location",
        "discharge_location": "a.discharge_location",
        "insurance": "a.insurance",
        "first_careunit": careunit,
        "language": "a.language",
        "marital_status": "a.marital_status",
        "anchor_year_group": "p.anchor_year_group",
    }
    select = ",\n".join(f"    {columns[c]} AS {c}" for c in PROBE_COLUMNS)
    where = ""
    if spec.era_filter:
        eras = ", ".join(_sql_str(e) for e in spec.era_filter)
        where = f"WHERE p.anchor_year_group IN ({eras})\n"
    indented = "\n".join("    " + line for line in index_sql.splitlines())
    return (
        "SELECT\n"
        f"{select},\n"
        f"    ({event_sql(spec.follow_up)}) AS event\n"
        "FROM (\n"
        f"{indented}\n"
        ") AS idx\n"
        "JOIN mimiciv_hosp.admissions AS a ON a.hadm_id = idx.hadm_id\n"
        "JOIN mimiciv_hosp.patients AS p ON p.subject_id = idx.subject_id\n"
        f"{stay_join}"
        f"{where}"
    ).rstrip("\n")


def probe_sql(spec: CohortSpec, column: str, cohort_relation: str | None = None) -> str | None:
    """The suppressed cross-tab statement of one probe column: level, ``n``, ``n_events``
    (a CTE over :func:`population_sql`; identifiers stay inside the population)."""
    if column not in PROBE_COLUMNS:
        raise ValueError(f"{column!r} is not a probe column; expected one of {PROBE_COLUMNS}")
    population = population_sql(spec, cohort_relation)
    if population is None:
        return None
    body = "\n".join("    " + line for line in population.splitlines())
    return (
        f"WITH pop AS (\n{body}\n)\n"
        f"SELECT {column} AS level, count(*) AS n, count(*) FILTER (WHERE event) AS n_events\n"
        "FROM pop\nGROUP BY 1\nORDER BY 1"
    )


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ReferenceCheck:
    """One reference on the tier: whether the registry surface carries the pair, with
    which hash / status, and the remedy when it does not match."""

    kind: str
    ref: str
    expected_hash: str
    found: bool
    tier_hash: str | None
    status: str | None
    remedy: str | None

    @property
    def ok(self) -> bool:
        return self.remedy is None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "ref": self.ref,
            "expected_hash": self.expected_hash,
            "found": self.found,
            "tier_hash": self.tier_hash,
            "status": self.status,
            "ok": self.ok,
            "remedy": self.remedy,
        }


@dataclass(frozen=True, slots=True)
class LevelRow:
    """One released level: its counts and verdict."""

    column: str
    level: str
    n: int
    n_events: int
    verdict: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "column": self.column,
            "level": self.level,
            "n": self.n,
            "n_events": self.n_events,
            "verdict": self.verdict,
        }


@dataclass(frozen=True, slots=True)
class ProbeResult:
    """The probe of one column: the released levels, how many levels were withheld
    (small cells), the audit id and the statement."""

    column: str
    rows: tuple[LevelRow, ...]
    rows_suppressed: int
    audit_id: str
    sql: str

    @property
    def degenerate(self) -> tuple[LevelRow, ...]:
        return tuple(r for r in self.rows if r.verdict != OK)

    def to_dict(self) -> dict[str, Any]:
        return {
            "column": self.column,
            "rows": [r.to_dict() for r in self.rows],
            "rows_suppressed": self.rows_suppressed,
            "degenerate": [r.to_dict() for r in self.degenerate],
            "audit_id": self.audit_id,
        }


@dataclass(frozen=True, slots=True)
class TierValidation:
    """``mwh cohort validate --tier``'s result: the reference checks, the probes, the
    problems (a missing / stale reference) and warnings (degenerate or withheld levels,
    a skipped probe)."""

    entry: Entry
    tier: str
    k: int
    references: tuple[ReferenceCheck, ...]
    probes: tuple[ProbeResult, ...]
    problems: tuple[str, ...]
    warnings: tuple[str, ...]
    skipped: str | None = None
    snapshot_id: str | None = None
    audit_ids: tuple[str, ...] = field(default=())
    #: What the probe ran over: ``index`` (the index population, EP-46) or ``cohort``
    #: (the compiled cohort's session view, EP-47); the relation when ``cohort``.
    population: str = "index"
    cohort_relation: str | None = None

    @property
    def ok(self) -> bool:
        return not self.problems

    def to_dict(self) -> dict[str, Any]:
        return {
            "ref": self.entry.ref,
            "tier": self.tier,
            "k": self.k,
            "def_hash": self.entry.def_hash,
            "references": [r.to_dict() for r in self.references],
            "probes": [p.to_dict() for p in self.probes],
            "probe_skipped": self.skipped,
            "population": self.population,
            "cohort_relation": self.cohort_relation,
            "snapshot_id": self.snapshot_id,
            "audit_ids": list(self.audit_ids),
            "problems": list(self.problems),
            "warnings": list(self.warnings),
            "ok": self.ok,
        }


# ---------------------------------------------------------------------------
# The checks
# ---------------------------------------------------------------------------


def _query(
    sql: str,
    *,
    tier: str,
    settings: Settings,
    actor: str | None,
    k: int | None = None,
    row_cap: int = 200,
) -> SafeResult:
    from mimicwarehouse.safe import safe_query

    return safe_query(sql, tier=tier, k=k, row_cap=row_cap, settings=settings, actor=actor)


def _meta_table_present(
    table: str, *, tier: str, settings: Settings, actor: str | None, schema: str = "meta"
) -> bool:
    result = _query(
        f"SELECT table_name FROM information_schema.tables WHERE table_schema = {_sql_str(schema)} "
        f"AND table_name = {_sql_str(table)}",
        tier=tier,
        settings=settings,
        actor=actor,
    )
    return result.df.height > 0


def check_references(
    entry: Entry, *, tier: str, settings: Settings, actor: str | None = ACTOR
) -> list[ReferenceCheck]:
    """Every reference of ``entry`` against the tier's registry surfaces (module
    docstring)."""
    checks: list[ReferenceCheck] = []
    codeset_refs = sorted(entry.resolved.get("codeset", {}).items())
    phenotype_refs = sorted(entry.resolved.get("phenotype", {}).items())
    if codeset_refs:
        compiled: dict[str, str] = {}
        if _meta_table_present("codesets", tier=tier, settings=settings, actor=actor):
            ids = ", ".join(_sql_str(ref.partition("@")[0]) for ref, _h in codeset_refs)
            result = _query(
                "SELECT codeset_id, version, def_hash FROM meta.codesets "
                f"WHERE codeset_id IN ({ids})",
                tier=tier,
                settings=settings,
                actor=actor,
                row_cap=REGISTRY_ROW_CAP,
            )
            compiled = {f"{i}@{v}": str(h) for i, v, h in result.df.rows()}
        for ref, expected in codeset_refs:
            tier_hash = compiled.get(ref)
            remedy: str | None = None
            if tier_hash is None:
                remedy = f"`mwh codeset compile --tier {tier} {ref}` (not compiled on {tier})"
            elif tier_hash != expected:
                remedy = (
                    f"`mwh codeset compile --tier {tier} {ref}` (compiled with def_hash "
                    f"{tier_hash[:12]}, the registry resolves {expected[:12]})"
                )
            checks.append(
                ReferenceCheck(
                    "codeset", ref, expected, tier_hash is not None, tier_hash, None, remedy
                )
            )
    if phenotype_refs:
        built: dict[str, tuple[str, str]] = {}
        if _meta_table_present("phenotype_versions", tier=tier, settings=settings, actor=actor):
            ids = ", ".join(_sql_str(ref.partition("@")[0]) for ref, _h in phenotype_refs)
            result = _query(
                "SELECT phenotype_id, version, def_hash, status FROM meta.phenotype_versions "
                f"WHERE phenotype_id IN ({ids})",
                tier=tier,
                settings=settings,
                actor=actor,
                row_cap=REGISTRY_ROW_CAP,
            )
            built = {f"{i}@{v}": (str(h), str(s)) for i, v, h, s in result.df.rows()}
        for ref, expected in phenotype_refs:
            row = built.get(ref)
            tier_hash = row[0] if row else None
            status = row[1] if row else None
            remedy = None
            if row is None:
                remedy = f"`mwh phenotype compile {ref} --tier {tier}` (not built on {tier})"
            elif status != "done":
                remedy = f"`mwh phenotype compile {ref} --tier {tier}` (last attempt: {status})"
            elif tier_hash != expected:
                remedy = (
                    f"`mwh phenotype compile {ref} --tier {tier} --force` (built with def_hash "
                    f"{(tier_hash or '')[:12]}, the registry resolves {expected[:12]})"
                )
            checks.append(
                ReferenceCheck(
                    "phenotype", ref, expected, row is not None, tier_hash, status, remedy
                )
            )
    return checks


def _level_text(value: Any) -> str:
    return NULL_LEVEL if value is None else str(value)


def built_relation(
    entry: Entry, *, tier: str, settings: Settings, actor: str | None = ACTOR
) -> str | None:
    """``marts.cohort_<id>_v<major>`` when ``marts.cohorts`` on the tier carries this
    ``id@version`` built with the registry's ``def_hash`` **and** owning the view (the
    latest patch of its major); None otherwise (not built, stale, superseded, or no
    registry table yet). A registry read (no count column needed, EP-33 B1c)."""
    if not _meta_table_present(
        "cohorts", tier=tier, settings=settings, actor=actor, schema="marts"
    ):
        return None
    spec = entry.spec
    result = _query(
        "SELECT def_hash, view FROM marts.cohorts WHERE cohort_id = "
        f"{_sql_str(spec.id)} AND version = {_sql_str(spec.version)}",
        tier=tier,
        settings=settings,
        actor=actor,
        row_cap=REGISTRY_ROW_CAP,
    )
    for def_hash, view in result.df.rows():
        if str(def_hash) == entry.def_hash and view:
            return f"marts.{view}"
    return None


def run_probe(
    entry: Entry,
    *,
    tier: str,
    settings: Settings,
    k: int | None = None,
    actor: str | None = ACTOR,
    cohort_relation: str | None = None,
) -> list[ProbeResult]:
    """The degeneracy probe of every column of the spec's ``degeneracy_probe`` (module
    docstring) over the index population, or over ``cohort_relation`` (the compiled
    cohort's view, EP-47); ``[]`` when there is no population or the probe is off."""
    spec = entry.spec
    out: list[ProbeResult] = []
    for column in spec.degeneracy_probe.columns:
        sql = probe_sql(spec, column, cohort_relation)
        if sql is None:
            return []
        result = _query(sql, tier=tier, settings=settings, actor=actor, k=k)
        rows: list[LevelRow] = []
        for level, n, n_events in result.df.rows():
            n_int = int(n)
            events = int(n_events)
            verdict = OK
            if n_int > 0 and events == 0:
                verdict = ZERO_EVENT
            elif n_int > 0 and events == n_int:
                verdict = ALL_EVENT
            rows.append(LevelRow(column, _level_text(level), n_int, events, verdict))
        out.append(
            ProbeResult(
                column=column,
                rows=tuple(rows),
                rows_suppressed=result.rows_suppressed,
                audit_id=result.audit_id,
                sql=sql,
            )
        )
    return out


def validate_on_tier(
    entry: Entry,
    *,
    tier: str,
    settings: Settings | None = None,
    k: int | None = None,
    actor: str | None = ACTOR,
    population: str = "auto",
) -> TierValidation:
    """Both tier-level checks of one entry (module docstring). ``population`` picks what
    the probe runs over: ``"auto"`` (the compiled cohort when the tier's ``marts.cohorts``
    carries this ``id@version`` with the registry's hash — EP-47 — else the index
    population), ``"index"`` or ``"cohort"`` (a problem when it is not built)."""
    from mimicwarehouse.config import get_settings

    settings = settings or get_settings()
    resolved_k = k if k is not None else settings.k_suppression
    references = check_references(entry, tier=tier, settings=settings, actor=actor)
    problems = [f"{c.kind} {c.ref}: {c.remedy}" for c in references if not c.ok]
    warnings: list[str] = []
    skipped: str | None = None
    probes: list[ProbeResult] = []
    spec = entry.spec
    if population not in ("auto", "index", "cohort"):
        raise ValueError(f"population {population!r} is not auto | index | cohort")
    relation: str | None = None
    if population in ("auto", "cohort"):
        relation = built_relation(entry, tier=tier, settings=settings, actor=actor)
        if relation is None and population == "cohort":
            problems.append(
                f"{spec.ref} is not built on tier {tier} with def_hash {entry.def_hash[:12]} "
                f"(or a later patch owns the view) - run `mwh cohort build {spec.ref} --tier "
                f"{tier}` first"
            )
    probed = "cohort" if relation is not None else "index"
    if not spec.degeneracy_probe.columns:
        skipped = "degeneracy_probe.columns is empty"
    elif relation is None and spec.index_event.rule is None:
        skipped = (
            f"index event {spec.index_event.render()} has no population before it is "
            f"compiled - run `mwh cohort build {spec.ref} --tier {tier}` and validate again "
            "(the probe then runs over the compiled cohort)"
        )
    elif relation is None and population == "cohort":
        skipped = "the cohort is not built on the tier"
    else:
        probes = run_probe(
            entry,
            tier=tier,
            settings=settings,
            k=resolved_k,
            actor=actor,
            cohort_relation=relation,
        )
    if skipped:
        warnings.append(f"degeneracy probe skipped: {skipped}")
    for probe in probes:
        for row in probe.degenerate:
            warnings.append(
                f"{probe.column}={row.level} is {row.verdict} for {spec.follow_up.outcome} "
                f"(n {row.n:,}, events {row.n_events:,}) - a model conditioning on it must "
                "exclude and name the level (EP-31 policy; EP-79 model side)"
            )
        if probe.rows_suppressed:
            warnings.append(
                f"{probe.column}: {probe.rows_suppressed} level(s) withheld at k={resolved_k} "
                "(small cells; not assessable from a session)"
            )
    audit_ids = tuple(p.audit_id for p in probes)
    snapshot_id: str | None = None
    if probes:
        # the catalog's core snapshot id travels on every safe_query result; read it once
        from mimicwarehouse.safe import safe_query

        head = safe_query(
            "SELECT count(*) AS n FROM meta.grains", tier=tier, settings=settings, actor=actor
        )
        snapshot_id = head.snapshot_id
        audit_ids = (*audit_ids, head.audit_id)
    return TierValidation(
        entry=entry,
        tier=tier,
        k=resolved_k,
        references=tuple(references),
        probes=tuple(probes),
        problems=tuple(problems),
        warnings=tuple(warnings),
        skipped=skipped,
        snapshot_id=snapshot_id,
        audit_ids=audit_ids,
        population=probed,
        cohort_relation=relation,
    )


__all__ = [
    "ACTOR",
    "ALL_EVENT",
    "NULL_LEVEL",
    "OK",
    "REGISTRY_ROW_CAP",
    "ZERO_EVENT",
    "LevelRow",
    "ProbeResult",
    "ReferenceCheck",
    "TierValidation",
    "built_relation",
    "check_references",
    "event_sql",
    "population_sql",
    "probe_sql",
    "run_probe",
    "validate_on_tier",
]
