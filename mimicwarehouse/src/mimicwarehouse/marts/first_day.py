"""``marts.icustay_first_day`` -- the wide stay-grain first-day mart (EP-55 item 2;
DESIGN §3/§7; D-17, D-18, D-28).

One row per ICU stay of ``mimiciv_icu.icustays`` (the base table, so the row count is the
tier's stay count by construction): keys, demographics and admission context, durations,
outcomes, the first ICU day's vitals, labs, arterial blood gas, GCS, urine output,
admission weight, SOFA and its six sub-scores, the Charlson index and five first-day flags.
The column contract is the spec ``marts/specs/icustay_first_day.yaml``; this module holds
only what the spec cannot: :func:`ctes`, the four windowed sources reduced to one row per
key, and the step handler.

**Windows.** Every first-day column keeps mimic-code's own window, ``icu_intime - 6 h`` to
``icu_intime + 24 h`` inclusive (the ``first_day_*`` concepts as shipped), and the flags
derived here use the same one: an ``InvasiveVent`` episode or a vasopressor infusion
(:data:`VASOPRESSORS`, rate above zero; dobutamine and milrinone are inotropes) whose
interval overlaps the window, and the highest KDIGO ``aki_stage`` charted inside it.
``first_service`` is the admission's first ``curr_service`` by ``transfertime``.

**MIMIC caveats** (spec descriptions): ages >= 89 are shipped as 91, so ``admission_age``
is capped at 91 with ``age_capped``; ``era`` (``anchor_year_group``) is the only
cross-patient time axis; ``icu_intime`` is a per-patient shifted naive timestamp, the
anchor of every window, never a calendar date.

:func:`run_first_day` (``python`` step ``marts.icustay_first_day``,
``dag/specs/marts.yaml``) binds the core views it reads **uncached**
(:func:`qc.profile.ensure_table_views`: the concept runner's cached walker can miss a table
staged later in the same build, ``docs/gotchas.md`` §1) and one
``mimiciv_derived.<concept>`` view per concept source (refused with the remedy when the
concept is not built for the tier), generates the statement from the spec
(:func:`registry.mart_sql`) and publishes it (:func:`registry.write_mart`, layers ``core``
and ``derived``). Counts, hashes and timings only (GOVERNANCE §4).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from mimicwarehouse.marts.registry import (
    MartBuildError,
    MartSpec,
    mart_sql,
    packaged_registry,
    record_failure,
    write_mart,
)

if TYPE_CHECKING:  # pragma: no cover
    from mimicwarehouse.dag.runner import StepContext, StepOutcome
    from mimicwarehouse.dag.spec import Step

#: The spec / DAG step of this mart.
SPEC_NAME = "icustay_first_day"
STEP = "marts.icustay_first_day"
#: mimic-code's first-day window around ``icu_intime`` (hours before / after, inclusive).
WINDOW_BEFORE_H = 6
WINDOW_AFTER_H = 24
#: The vasoactive agents that count as vasopressors (``mimiciv_derived.vasoactive_agent``).
VASOPRESSORS: tuple[str, ...] = (
    "norepinephrine",
    "epinephrine",
    "dopamine",
    "phenylephrine",
    "vasopressin",
)
#: Schemas a source relation may live in: the staged core and the concept layer.
CORE_SCHEMAS: tuple[str, ...] = ("mimiciv_hosp", "mimiciv_icu")
DERIVED_SCHEMA = "mimiciv_derived"


def ctes() -> dict[str, str]:
    """``alias -> SELECT`` of the ``cte: true`` sources, one row per key (module
    docstring)."""
    lo = f"i.intime - INTERVAL {WINDOW_BEFORE_H} HOUR"
    hi = f"i.intime + INTERVAL {WINDOW_AFTER_H} HOUR"
    pressor = " OR ".join(f"coalesce(va.{agent}, 0) > 0" for agent in VASOPRESSORS)
    return {
        "svc": (
            "SELECT hadm_id, first(curr_service ORDER BY transfertime, curr_service) "
            "AS first_service\nFROM mimiciv_hosp.services\nGROUP BY hadm_id"
        ),
        "vent": (
            "SELECT v.stay_id, true AS flag\nFROM mimiciv_derived.ventilation AS v\n"
            "JOIN mimiciv_icu.icustays AS i ON i.stay_id = v.stay_id\n"
            f"WHERE v.ventilation_status = 'InvasiveVent' AND v.starttime <= {hi} "
            f"AND v.endtime >= {lo}\nGROUP BY v.stay_id"
        ),
        "vaso": (
            "SELECT va.stay_id, true AS flag\nFROM mimiciv_derived.vasoactive_agent AS va\n"
            "JOIN mimiciv_icu.icustays AS i ON i.stay_id = va.stay_id\n"
            f"WHERE ({pressor}) AND va.starttime <= {hi} AND va.endtime >= {lo}\n"
            "GROUP BY va.stay_id"
        ),
        "kd": (
            "SELECT k.stay_id, max(k.aki_stage) AS max_stage\n"
            "FROM mimiciv_derived.kdigo_stages AS k\n"
            "JOIN mimiciv_icu.icustays AS i ON i.stay_id = k.stay_id\n"
            f"WHERE k.charttime >= {lo} AND k.charttime <= {hi}\nGROUP BY k.stay_id"
        ),
    }


def spec() -> MartSpec:
    """The packaged ``icustay_first_day`` spec."""
    return packaged_registry().spec(SPEC_NAME)


def build_sql(mart: MartSpec | None = None) -> str:
    """The mart's full statement (spec SELECT list over the join skeleton + :func:`ctes`)."""
    return mart_sql(mart or spec(), ctes())


def source_relations(mart: MartSpec) -> tuple[list[str], list[str]]:
    """``(core relations, concept names)`` the mart reads, from its declared sources."""
    core: list[str] = []
    concepts: list[str] = []
    for s in mart.sources:
        schema, _, table = s.relation.partition(".")
        if schema in CORE_SCHEMAS:
            core.append(s.relation)
        elif schema == DERIVED_SCHEMA:
            concepts.append(table)
        else:
            raise MartBuildError(
                f"{mart.name}: source {s.relation} is outside {', '.join(CORE_SCHEMAS)} and "
                f"{DERIVED_SCHEMA}"
            )
    return sorted(set(core)), sorted(set(concepts))


def bind_sources(ctx: StepContext, mart: MartSpec) -> None:
    """Every relation the statement reads, on the build connection (module docstring);
    :class:`MartBuildError` names the missing one and the remedy."""
    from mimicwarehouse.concepts.runner import ConceptError, ensure_derived_view
    from mimicwarehouse.qc.profile import ensure_table_views

    core, concepts = source_relations(mart)
    ensure_table_views(ctx, core)
    present = {
        str(r[0])
        for r in ctx.con.execute(
            "SELECT table_schema || '.' || table_name FROM information_schema.tables"
        ).fetchall()
    }
    missing = [r for r in core if r not in present]
    if missing:
        raise MartBuildError(
            f"{mart.name}: {', '.join(missing)} not staged for tier {ctx.tier} - run "
            f"`mwh build --tier {ctx.tier} --select stage.{missing[0]}` first"
        )
    ctx.con.execute(f"CREATE SCHEMA IF NOT EXISTS {DERIVED_SCHEMA}")
    for name in concepts:
        try:
            ensure_derived_view(ctx, name)
        except ConceptError as exc:
            raise MartBuildError(f"{mart.name}: {exc}") from None


def run_first_day(step: Step, ctx: StepContext) -> StepOutcome:
    """The ``marts.icustay_first_day`` handler (module docstring)."""
    mart = spec()
    try:
        bind_sources(ctx, mart)
    except MartBuildError as exc:
        record_failure(ctx, mart, type(exc).__name__)
        raise
    return write_mart(ctx, mart, build_sql(mart), layers_read=("core", "derived"))


__all__ = [
    "CORE_SCHEMAS",
    "DERIVED_SCHEMA",
    "SPEC_NAME",
    "STEP",
    "VASOPRESSORS",
    "WINDOW_AFTER_H",
    "WINDOW_BEFORE_H",
    "bind_sources",
    "build_sql",
    "ctes",
    "run_first_day",
    "source_relations",
    "spec",
]
