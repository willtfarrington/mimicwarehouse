"""``marts.itemid_daily`` and ``marts.itemid_summary`` -- the itemid rollups (EP-175, split
from EP-55's item 3 at its pickup; DESIGN §3/§7; D-17, D-18, D-28, D-33).

**What is rolled up.** The EP-39 catalogue's curated itemids whose ``source`` is
``chartevents`` or ``labevents`` (:func:`rollup_items`; the outputevents / inputevents
items stay out). Before anything is built each one is checked against its ``source_ref``
(:func:`check_source_refs`): an itemid the catalogue copied from a vendored mimic-code
concept file must appear in that file under ``concepts/vendor/mimic-code/`` -- read, never
retyped; the few items EP-39 verified by a dictionary lookup instead carry no concept file
and are reported as such.

**One value per event row** (:func:`values_sql`): a row with a non-NULL ``valuenum``,
converted to the item's canonical unit **inline** by
:func:`~mimicwarehouse.phenotypes.compiler.harmonised_value_sql` -- one ``CASE`` over a
unit string normalised once per row, and only on the rows of items with a non-identity
conversion (``docs/gotchas.md`` §1: the ``mwh_harmonize`` macro is for point lookups,
never a scan); a unit the catalogue does not accept passes the value through unconverted,
exactly as the macro and EP-44's implausible-value check do. ``chartevents`` rows attach
to their ``stay_id``; ``labevents`` rows (no ``stay_id``) to the ICU stay of the same
subject whose ``[intime, outtime)`` contains ``charttime`` -- a lab outside every stay is
dropped, and, as in EP-45, a lab inside two overlapping stays counts for both.
``icu_day = floor(hours since intime / 24)`` (:func:`timesem.sql_hours_since`, ``[start,
end)`` days from 0, EP-34's ``icu_day`` grain); rows before ``intime`` (day < 0) are
dropped. A value is *plausible* inside the item's inclusive EP-39 bounds.

**``marts.itemid_daily``** (:func:`daily_cte_sql`) -- one row per (``stay_id``,
``itemid``, ``icu_day``) with at least one value: ``n`` plausible values, their ``min`` /
``max`` / ``mean``, ``first`` / ``last`` (by ``charttime``, ties broken by the value:
``arg_min`` / ``arg_max`` over ``(charttime, value)``, deterministic and streaming) and
``n_implausible``, the values outside the bounds, which no statistic includes -- so ``n +
n_implausible`` is the stay-day's raw count of valued rows. Partitioned by
``subject_bucket``, one sorted file per bucket (:func:`registry.write_mart`).

**``marts.itemid_summary``** (:func:`compute_summary`, :func:`release_summary`) -- one row
per rolled-up itemid (the dictionary-shaped grain ``itemid``): label, source table and
canonical unit from the catalogue, ``n_stays`` / ``n_measurements`` (plausible values, so
they agree with the daily mart's ``n``), ``pct_stays_measured`` (of every ICU stay on the
tier) and the ``p01`` / ``p50`` / ``p99`` of the plausible values (exact
``quantile_cont``). Published the way the other registry-shaped tables are
(``meta.item_unit_variants``, D-33): every row kept, the two counts passed through
:data:`safe.SUPPRESSOR` (complementary, so two nested counts whose difference is small lose
the smaller), and on every row the hook would withhold the counts, the share and the
percentiles are blanked, ``suppressed`` is true and ``k`` records the threshold. It is a
:data:`safe.REGISTRY_TABLES` member, readable without a count column. Its aggregate scan
runs before the writer, so it is measured as one ``kind: query`` benchmark line
(:data:`SUMMARY_BENCH_STEP`) beside the writer's ``kind: mart`` line.

Steps ``marts.itemid_daily`` / ``marts.itemid_summary`` (``dag/specs/marts.yaml``) bind
the three core views they read **uncached** (:func:`qc.profile.ensure_table_views`, as
``marts.first_day`` does) and publish through :func:`registry.write_mart` (layer
``core``). Everything logged or returned is counts, hashes and timings, never a row
(GOVERNANCE §4).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mimicwarehouse.marts.registry import (
    MartBuildError,
    MartSpec,
    mart_sql,
    packaged_registry,
    record_failure,
    write_mart,
)

if TYPE_CHECKING:  # pragma: no cover
    import duckdb
    import polars

    from mimicwarehouse.dag.runner import StepContext, StepOutcome
    from mimicwarehouse.dag.spec import Step
    from mimicwarehouse.units import ItemCatalogue, ItemSpec

_LOG = logging.getLogger(__name__)

#: The specs / DAG steps of the two marts.
SPEC_DAILY = "itemid_daily"
SPEC_SUMMARY = "itemid_summary"
STEP_DAILY = "marts.itemid_daily"
STEP_SUMMARY = "marts.itemid_summary"
#: The EP-39 sources rolled up (outputevents / inputevents items stay out).
ROLLUP_SOURCES: tuple[str, ...] = ("chartevents", "labevents")
#: The stays every value attaches to (and the summary's denominator).
STAYS_TABLE = "mimiciv_icu.icustays"
#: Schemas whose relations the steps bind as core views.
CORE_SCHEMAS: tuple[str, ...] = ("mimiciv_hosp", "mimiciv_icu")
#: ``icu_day`` bins: ``[start, end)`` days of 24 h from ``intime``.
HOURS_PER_DAY = 24.0
#: The summary's percentiles (``p01`` / ``p50`` / ``p99``).
QUANTILES: tuple[float, ...] = (0.01, 0.5, 0.99)
#: A ``source_ref`` naming a vendored mimic-code file (else an EP-39 dictionary lookup).
CONCEPT_REF_PREFIX = "mimic-iv/"
#: The temp table the summary's released frame is staged in for the writer.
RELEASED_TABLE = "_mwh_itemid_summary_released"
#: The benchmark-ledger step of the summary's aggregate scan (``kind: query``).
SUMMARY_BENCH_STEP = "marts.itemid_summary.aggregate"
#: The summary's count columns (``safe.SUPPRESSOR``) and the columns blanked beside them.
SUMMARY_COUNT_COLUMNS: tuple[str, ...] = ("n_stays", "n_measurements")
SUMMARY_BLANKED: tuple[str, ...] = (
    "n_stays",
    "n_measurements",
    "pct_stays_measured",
    "p01",
    "p50",
    "p99",
)


# ---------------------------------------------------------------------------
# The items and their source references
# ---------------------------------------------------------------------------


def rollup_items(catalogue: ItemCatalogue | None = None) -> tuple[ItemSpec, ...]:
    """The curated items of :data:`ROLLUP_SOURCES`, ascending by itemid."""
    from mimicwarehouse.units import load_catalogue

    cat = catalogue or load_catalogue()
    return tuple(
        sorted((i for i in cat.items if i.source in ROLLUP_SOURCES), key=lambda i: i.itemid)
    )


@dataclass(frozen=True, slots=True)
class SourceRefCheck:
    """What :func:`check_source_refs` found: ``(itemid, concept file)`` pairs found in
    their file, the dictionary-verified itemids (no concept file), and the problems."""

    concept: tuple[tuple[int, str], ...]
    dictionary: tuple[int, ...]
    problems: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.problems


def vendor_tree() -> Path:
    """``concepts/vendor/mimic-code/`` inside the installed package (the ``source_ref``
    root)."""
    from mimicwarehouse.concepts import TREE_DIRNAME, vendor_root

    return vendor_root() / TREE_DIRNAME


def check_source_refs(
    items: Sequence[ItemSpec] | None = None, tree: Path | None = None
) -> SourceRefCheck:
    """Each item whose ``source_ref`` names a vendored concept file must appear in that
    file as a whole number (module docstring); a missing file or an absent itemid is a
    problem. Items verified by a dictionary lookup are listed, not checked."""
    chosen = rollup_items() if items is None else tuple(items)
    root = vendor_tree() if tree is None else Path(tree)
    texts: dict[str, str | None] = {}
    concept: list[tuple[int, str]] = []
    dictionary: list[int] = []
    problems: list[str] = []
    for item in chosen:
        ref = item.source_ref.strip()
        if not ref.startswith(CONCEPT_REF_PREFIX):
            dictionary.append(item.itemid)
            continue
        if ref not in texts:
            try:
                texts[ref] = (root / ref).read_text(encoding="utf-8")
            except OSError:
                texts[ref] = None
        text = texts[ref]
        if text is None:
            problems.append(f"itemid {item.itemid}: source_ref {ref} is not a vendored file")
        elif re.search(rf"(?<![0-9]){item.itemid}(?![0-9])", text) is None:
            problems.append(f"itemid {item.itemid}: not found in its source_ref {ref}")
        else:
            concept.append((item.itemid, ref))
    return SourceRefCheck(tuple(concept), tuple(dictionary), tuple(problems))


# ---------------------------------------------------------------------------
# SQL builders (text only; the steps run it)
# ---------------------------------------------------------------------------


def _ids(itemids: Sequence[int]) -> str:
    return ", ".join(str(int(i)) for i in itemids)


def _needs_unit(item: ItemSpec) -> bool:
    """Whether any accepted unit of ``item`` converts by something other than identity
    (only those rows need the normalised unit string)."""
    return any(not affine.is_identity for affine in item.conversions().values())


def _bounds_case(items: Sequence[ItemSpec], attr: str) -> str:
    arms = " ".join(f"WHEN {i.itemid} THEN {float(getattr(i, attr))!r}" for i in items)
    return f"CASE l.itemid {arms} END"


def source_values_sql(source: str, items: Sequence[ItemSpec]) -> str:
    """The harmonised values of one source's ``items``: ``subject_id, stay_id, itemid,
    charttime, icu_day, v, lo, hi`` (module docstring: chartevents by ``stay_id``,
    labevents by subject inside ``[intime, outtime)``)."""
    from mimicwarehouse.phenotypes.compiler import harmonised_value_sql
    from mimicwarehouse.timesem import sql_hour_bin, sql_hours_since
    from mimicwarehouse.units import SOURCE_COLUMNS, sql_normalize_unit

    if source not in ROLLUP_SOURCES:
        raise MartBuildError(f"source {source!r} is not rolled up ({', '.join(ROLLUP_SOURCES)})")
    if not items or any(i.source != source for i in items):
        raise MartBuildError(f"source_values_sql({source!r}) needs that source's items")
    schema, table, value_col, unit_col = SOURCE_COLUMNS[source]
    ids = [i.itemid for i in items]
    needs = [i.itemid for i in items if _needs_unit(i)]
    harmonised, _needs_norm = harmonised_value_sql(tuple(ids))
    unit_norm = (
        f"CASE WHEN itemid IN ({_ids(needs)}) THEN {sql_normalize_unit(unit_col)} END"
        if needs
        else "CAST(NULL AS VARCHAR)"
    )
    key = "stay_id" if source == "chartevents" else "subject_id"
    inner = (
        f"SELECT {key}, itemid, charttime, CAST({value_col} AS DOUBLE) AS valuenum, "
        f"{unit_norm} AS unit_norm\n"
        f"  FROM {schema}.{table}\n"
        f"  WHERE itemid IN ({_ids(ids)}) AND {value_col} IS NOT NULL"
    )
    if source == "chartevents":
        join = f"JOIN {STAYS_TABLE} AS s ON s.stay_id = l.stay_id"
    else:
        join = (
            f"JOIN {STAYS_TABLE} AS s ON s.subject_id = l.subject_id\n"
            "  AND l.charttime >= s.intime AND l.charttime < s.outtime"
        )
    icu_day = sql_hour_bin(sql_hours_since("s.intime", "l.charttime"), HOURS_PER_DAY)
    return (
        f"SELECT s.subject_id, s.stay_id, l.itemid, l.charttime, {icu_day} AS icu_day,\n"
        f"  {harmonised} AS v,\n"
        f"  {_bounds_case(items, 'plausible_low')} AS lo,\n"
        f"  {_bounds_case(items, 'plausible_high')} AS hi\n"
        f"FROM (\n  {inner}\n) AS l\n{join}"
    )


def values_sql(items: Sequence[ItemSpec] | None = None) -> str:
    """Every harmonised value of the rolled-up items on or after ``intime``:
    ``subject_id, stay_id, itemid, charttime, icu_day, v, plausible``."""
    chosen = rollup_items() if items is None else tuple(items)
    arms = [
        source_values_sql(source, subset)
        for source in ROLLUP_SOURCES
        if (subset := [i for i in chosen if i.source == source])
    ]
    if not arms:
        raise MartBuildError("no curated chartevents / labevents itemid to roll up")
    union = "\nUNION ALL\n".join(arms)
    return (
        "SELECT subject_id, stay_id, itemid, charttime, icu_day, v, "
        "v BETWEEN lo AND hi AS plausible\n"
        f"FROM (\n{union}\n) AS x\nWHERE icu_day >= 0"
    )


def daily_cte_sql(items: Sequence[ItemSpec] | None = None) -> str:
    """The daily mart's rows (``cte_r``): one per ``(stay_id, itemid, icu_day)``."""
    return (
        "SELECT subject_id, stay_id, itemid, icu_day,\n"
        "  count(*) FILTER (WHERE plausible) AS n,\n"
        '  min(v) FILTER (WHERE plausible) AS "min",\n'
        '  max(v) FILTER (WHERE plausible) AS "max",\n'
        '  avg(v) FILTER (WHERE plausible) AS "mean",\n'
        '  arg_min(v, (charttime, v)) FILTER (WHERE plausible) AS "first",\n'
        '  arg_max(v, (charttime, v)) FILTER (WHERE plausible) AS "last",\n'
        "  count(*) FILTER (WHERE NOT plausible) AS n_implausible\n"
        f"FROM (\n{values_sql(items)}\n) AS val\n"
        "GROUP BY subject_id, stay_id, itemid, icu_day"
    )


def summary_aggregate_sql(items: Sequence[ItemSpec] | None = None) -> str:
    """The summary's raw aggregate per itemid (before suppression): ``itemid, n_stays,
    n_measurements, q`` (the :data:`QUANTILES` of the plausible values)."""
    quantiles = ", ".join(repr(q) for q in QUANTILES)
    return (
        "SELECT itemid,\n"
        "  count(DISTINCT stay_id) FILTER (WHERE plausible) AS n_stays,\n"
        "  count(*) FILTER (WHERE plausible) AS n_measurements,\n"
        f"  quantile_cont(v, [{quantiles}]) FILTER (WHERE plausible) AS q\n"
        f"FROM (\n{values_sql(items)}\n) AS val\n"
        "GROUP BY itemid ORDER BY itemid"
    )


def daily_spec() -> MartSpec:
    return packaged_registry().spec(SPEC_DAILY)


def summary_spec() -> MartSpec:
    return packaged_registry().spec(SPEC_SUMMARY)


def daily_sql(mart: MartSpec | None = None) -> str:
    """The daily mart's full statement (spec SELECT list over ``cte_r``)."""
    return mart_sql(mart or daily_spec(), {"r": daily_cte_sql()})


def summary_sql(mart: MartSpec | None = None) -> str:
    """The summary's full statement over the staged released frame (``cte_s``)."""
    return mart_sql(mart or summary_spec(), {"s": f"SELECT * FROM {RELEASED_TABLE}"})


# ---------------------------------------------------------------------------
# The summary: raw aggregate -> released frame
# ---------------------------------------------------------------------------


def compute_summary(
    con: duckdb.DuckDBPyConnection, items: Sequence[ItemSpec] | None = None
) -> polars.DataFrame:
    """One raw row per rolled-up itemid (zero counts for an item the tier never
    measures): ``itemid, label, source_table, unit, n_stays, n_measurements,
    pct_stays_measured, p01, p50, p99``. Unsuppressed -- never returned to a caller
    outside the build (:func:`release_summary` is the published shape)."""
    import polars as pl

    chosen = rollup_items() if items is None else tuple(items)
    agg = {int(r[0]): r for r in con.execute(summary_aggregate_sql(chosen)).fetchall()}
    row = con.execute(f"SELECT count(*) FROM {STAYS_TABLE}").fetchone()
    total = int(row[0]) if row else 0
    rows: list[dict[str, Any]] = []
    for item in chosen:
        _itemid, n_stays, n_measurements, q = agg.get(item.itemid, (item.itemid, 0, 0, None))
        stays = int(n_stays or 0)
        quantiles = list(q) if q is not None else [None] * len(QUANTILES)
        rows.append(
            {
                "itemid": item.itemid,
                "label": item.label,
                "source_table": item.source,
                "unit": item.canonical_unit,
                "n_stays": stays,
                "n_measurements": int(n_measurements or 0),
                "pct_stays_measured": None if total == 0 else 100.0 * stays / total,
                "p01": quantiles[0],
                "p50": quantiles[1],
                "p99": quantiles[2],
            }
        )
    schema = {
        "itemid": pl.Int64,
        "label": pl.String,
        "source_table": pl.String,
        "unit": pl.String,
        "n_stays": pl.Int64,
        "n_measurements": pl.Int64,
        "pct_stays_measured": pl.Float64,
        "p01": pl.Float64,
        "p50": pl.Float64,
        "p99": pl.Float64,
    }
    return pl.DataFrame(rows, schema=schema)


def release_summary(raw: polars.DataFrame, k: int) -> polars.DataFrame:
    """The published shape (module docstring): every row kept, the counts, share and
    percentiles blanked on the rows :data:`safe.SUPPRESSOR` withholds at ``k``, plus
    ``suppressed`` and ``k``."""
    import polars as pl

    from mimicwarehouse import safe

    rid = "_mwh_rid"
    indexed = raw.with_row_index(rid)
    kept, _withheld = safe.SUPPRESSOR(indexed, k, list(SUMMARY_COUNT_COLUMNS))
    kept_ids = set(kept.get_column(rid).to_list())
    flags = [i not in kept_ids for i in indexed.get_column(rid).to_list()]
    out = indexed.with_columns(pl.Series("suppressed", flags, dtype=pl.Boolean))
    out = out.with_columns(
        [
            pl.when(pl.col("suppressed")).then(None).otherwise(pl.col(c)).alias(c)
            for c in SUMMARY_BLANKED
        ]
    ).with_columns(pl.lit(k, dtype=pl.Int32).alias("k"))
    return out.drop(rid)


def _stage_released(con: duckdb.DuckDBPyConnection, mart: MartSpec, frame: Any) -> None:
    """The released frame as the temp table :data:`RELEASED_TABLE`, typed by the spec."""
    ddl = ", ".join(f'"{c.name}" {c.type}' for c in mart.expanded)
    con.execute(f"CREATE OR REPLACE TEMP TABLE {RELEASED_TABLE} ({ddl})")
    rows = [list(r) for r in frame.select(list(mart.column_names)).rows()]
    if rows:
        placeholders = ", ".join("?" for _ in mart.expanded)
        con.executemany(f"INSERT INTO {RELEASED_TABLE} VALUES ({placeholders})", rows)


# ---------------------------------------------------------------------------
# The steps
# ---------------------------------------------------------------------------


def core_relations(mart: MartSpec) -> list[str]:
    """The core relations among the spec's sources (base or read), sorted."""
    return sorted({s.relation for s in mart.sources if s.relation.split(".")[0] in CORE_SCHEMAS})


def prepare(ctx: StepContext, mart: MartSpec) -> SourceRefCheck:
    """The source-reference check, then the core views on the build connection;
    :class:`MartBuildError` names the problem and the remedy."""
    from mimicwarehouse.qc.profile import ensure_table_views

    check = check_source_refs()
    if not check.ok:
        raise MartBuildError(
            f"{mart.name}: the EP-39 catalogue disagrees with the vendored concepts - "
            + "; ".join(check.problems)
        )
    core = core_relations(mart)
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
    _LOG.info(
        "mart %s (%s): %d itemid(s) found in their concept file, %d dictionary-verified",
        mart.name,
        ctx.tier,
        len(check.concept),
        len(check.dictionary),
    )
    return check


def run_itemid_daily(step: Step, ctx: StepContext) -> StepOutcome:
    """The ``marts.itemid_daily`` handler (module docstring)."""
    mart = daily_spec()
    try:
        prepare(ctx, mart)
    except MartBuildError as exc:
        record_failure(ctx, mart, type(exc).__name__)
        raise
    return write_mart(ctx, mart, daily_sql(mart), layers_read=("core",))


def run_itemid_summary(step: Step, ctx: StepContext) -> StepOutcome:
    """The ``marts.itemid_summary`` handler (module docstring): the raw aggregate
    (measured, one ``kind: query`` line), the released frame, the writer."""
    import duckdb

    from mimicwarehouse import run as run_mod
    from mimicwarehouse.run import ResourceLog
    from mimicwarehouse.safe import sanitize_error_text

    mart = summary_spec()
    try:
        prepare(ctx, mart)
        raw, usage = ResourceLog.measure(
            lambda: compute_summary(ctx.con), data_root=ctx.settings.data_root
        )
    except MartBuildError as exc:
        record_failure(ctx, mart, type(exc).__name__)
        raise
    except duckdb.Error as exc:
        error_class = type(exc).__name__
        record_failure(ctx, mart, error_class)
        raise MartBuildError(
            f"{mart.name}: {error_class}: {sanitize_error_text(str(exc))}"
        ) from None
    run_mod.bench(
        "query",
        SUMMARY_BENCH_STEP,
        wall_s=usage.wall_s,
        tier=ctx.tier,
        run_id=ctx.run.run_id if ctx.run is not None else None,
        build_id=ctx.build_id,
        settings=ctx.settings,
        ok=True,
        peak_rss_mb=usage.peak_rss_mb,
        disk_delta_mb=usage.disk_delta_mb,
        rows=raw.height,
    )
    k = ctx.settings.k_suppression
    released = release_summary(raw, k)
    _LOG.info(
        "mart %s (%s): %d itemid(s), %d with counts blanked at k=%d",
        mart.name,
        ctx.tier,
        released.height,
        int(released.get_column("suppressed").sum()),
        k,
    )
    _stage_released(ctx.con, mart, released)
    try:
        return write_mart(ctx, mart, summary_sql(mart), layers_read=("core",))
    finally:
        ctx.con.execute(f"DROP TABLE IF EXISTS {RELEASED_TABLE}")


__all__ = [
    "CONCEPT_REF_PREFIX",
    "CORE_SCHEMAS",
    "HOURS_PER_DAY",
    "QUANTILES",
    "RELEASED_TABLE",
    "ROLLUP_SOURCES",
    "SPEC_DAILY",
    "SPEC_SUMMARY",
    "STAYS_TABLE",
    "STEP_DAILY",
    "STEP_SUMMARY",
    "SUMMARY_BENCH_STEP",
    "SUMMARY_BLANKED",
    "SUMMARY_COUNT_COLUMNS",
    "SourceRefCheck",
    "check_source_refs",
    "compute_summary",
    "core_relations",
    "daily_cte_sql",
    "daily_spec",
    "daily_sql",
    "prepare",
    "release_summary",
    "rollup_items",
    "run_itemid_daily",
    "run_itemid_summary",
    "source_values_sql",
    "summary_aggregate_sql",
    "summary_spec",
    "summary_sql",
    "values_sql",
    "vendor_tree",
]
