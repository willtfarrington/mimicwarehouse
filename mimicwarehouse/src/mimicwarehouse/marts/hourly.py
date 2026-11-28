"""``marts.icustay_hourly``, ``marts.hourly_variables`` and ``marts.hourly_population`` -- the
hourly marts (EP-56, whose page-query benchmark became EP-176 at its pickup; DESIGN §3/§7;
D-17, D-18, D-28, D-33).

**The variables** (:data:`VARIABLES`, in the hourly spec's column order; a test pins the
pair): 21 bedside and laboratory variables, each read from one column of a vendored mimic-code
concept materialised in ``mimiciv_derived`` (EP-37/38). Vitals come from ``vitalsign``
(``heart_rate``, ``sbp``, ``dbp``, ``mbp``, ``resp_rate``, ``temperature`` in degC, ``spo2``
and the charted ``glucose``); labs come from ``chemistry``, ``complete_blood_count``,
``coagulation`` and the arterial rows of ``bg`` (``specimen = 'ART.'``, mimic-code's
``first_day_bg_art`` filter, so ``lactate`` / ``ph`` / ``pao2`` / ``pco2`` agree with EP-55's
first-day blood gas). Values are as the concepts ship them, with their own range filters (the
EP-39 bounds are the itemid rollups' business); ``vitalsign``'s ``sbp`` / ``dbp`` / ``mbp``
are mimic-code's mean of the arterial and non-invasive readings charted at one time.

**The hour grid** (:func:`source_hourly_sql`): ``hour_bin = floor(hours since icu_intime)``
(:func:`timesem.sql_hour_bin` over :func:`timesem.sql_hours_since`; ``[start, end)`` hours,
bin 0 first, EP-34's ``hour_bin`` grain). ``vitalsign`` rows attach to their ``stay_id``; lab
rows (no ``stay_id``) to the ICU stay of the same subject whose ``[intime, outtime)``
contains ``charttime``. A lab outside every stay is dropped, and, as in EP-45 / EP-175, a lab
inside two overlapping stays counts for both. A bin is kept from 0 to ``ceil(los_icu_hours)``
inclusive, so vitals charted up to the bin after ``outtime`` stay (the brief's range; EP-175's
note on post-``outtime`` charting, restated), and below the hour cap
``Settings.marts_hourly_max_hours`` (``MWH_MARTS_HOURLY_MAX_HOURS``, default 336 = bins 0 …
335, the first 14 days; recorded as the manifest's ``params``). A row exists only when at
least one variable was measured in the hour.

**``marts.icustay_hourly``** (:func:`hourly_cte_sql`): per source, the rows of the hour reduce
to ``<var>_mean`` / ``_min`` / ``_max`` / ``_n`` (the non-NULL values; 0, never NULL, when
none) / ``_last`` (by ``charttime``, ties broken by the highest value: ``arg_max(v,
(charttime, v))``). The five
reductions are outer-joined over the union of their ``(stay_id, hour_bin)`` keys; the mart is
partitioned by ``subject_bucket``, one sorted file per bucket.

**``marts.hourly_variables``** (:func:`variables_frame`): the dictionary generated from the
hourly spec's family, :data:`VARIABLES` / :data:`SOURCES` and the EP-39 catalogue. There is
one row per variable with its label, unit, concept group, source concept and column, row
filter, attachment rule, the curated itemids behind it (the catalogue's items of its concept
group whose ``source_ref`` is the concept file, each found in that file by EP-175's
:func:`itemid_rollups.check_source_refs`) and the statistics.

**``marts.hourly_population``** (:func:`population_sql`, :func:`compute_population`,
:func:`release_population`): for ``hour_bin <= 168`` (:data:`POPULATION_MAX_HOUR`), the
published hourly mart's ``<var>_mean`` per stay and hour, grouped by hour x variable x era
(``patients.anchor_year_group``; ``GROUPING SETS`` add the ``all`` row). Each row carries
``n_stays`` (the stays measured in the hour, one row each), the exact ``quantile_cont``
p05 / p25 / p50 / p75 / p95 and the mean of the stays' hourly means. It is released at build
time the way the other registry-shaped tables are (D-33). Within each hour x variable the era
rows go through :func:`disclose.suppress` with ``era`` as the group column, so the published
``all`` row is the margin it protects (a margin with exactly one hidden era hides its
next-smallest); the ``all`` row itself is hidden below k. On a hidden row the count and the
statistics are blanked, ``suppressed`` is true and ``k`` records the threshold. The table is
a :data:`safe.REGISTRY_TABLES` member, readable without a count column; its aggregate scan is
measured as one ``kind: query`` ledger line (:data:`POPULATION_BENCH_STEP`).

Steps ``marts.icustay_hourly`` / ``marts.hourly_variables`` / ``marts.hourly_population``
(``dag/specs/marts.yaml``) publish through :func:`registry.write_mart`. Everything logged or
returned is counts, hashes and timings, never a row (GOVERNANCE §4).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from mimicwarehouse.marts.registry import (
    ColumnFamily,
    MartBuildError,
    MartSpec,
    mart_complete,
    mart_dir,
    mart_glob,
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
    from mimicwarehouse.units import ItemCatalogue

_LOG = logging.getLogger(__name__)

#: The specs / DAG steps of the three marts.
SPEC_HOURLY = "icustay_hourly"
SPEC_VARIABLES = "hourly_variables"
SPEC_POPULATION = "hourly_population"
STEP_HOURLY = "marts.icustay_hourly"
STEP_VARIABLES = "marts.hourly_variables"
STEP_POPULATION = "marts.hourly_population"
#: The hourly spec's column family and its statistics, in column order.
FAMILY = "hourly"
STATS: tuple[str, ...] = ("mean", "min", "max", "n", "last")
#: The stays every row attaches to and the era source.
STAYS_TABLE = "mimiciv_icu.icustays"
PATIENTS_TABLE = "mimiciv_hosp.patients"
DERIVED_SCHEMA = "mimiciv_derived"
#: The population table: hour bins 0 .. 168, its percentiles and the era labels it adds.
POPULATION_MAX_HOUR = 168
QUANTILES: tuple[float, ...] = (0.05, 0.25, 0.5, 0.75, 0.95)
QUANTILE_COLUMNS: tuple[str, ...] = ("p05", "p25", "p50", "p75", "p95")
ALL_ERA = "all"
UNKNOWN_ERA = "unknown"
#: The population's count column and the columns blanked beside a hidden count.
POPULATION_COUNT = "n_stays"
POPULATION_BLANKED: tuple[str, ...] = (POPULATION_COUNT, *QUANTILE_COLUMNS, "mean")
#: The benchmark-ledger step of the population's aggregate scan (``kind: query``).
POPULATION_BENCH_STEP = "marts.hourly_population.aggregate"
#: Temp tables the released frames are staged in for the writer.
RELEASED_VARIABLES = "_mwh_hourly_variables_released"
RELEASED_POPULATION = "_mwh_hourly_population_released"
#: The view over the published hourly mart the population step binds on its connection.
HOURLY_VIEW = "marts.icustay_hourly"

Attach = Literal["stay", "subject"]


@dataclass(frozen=True, slots=True)
class HourlySource:
    """One mimic-code concept the hourly mart reads: how its rows reach a stay
    (``stay`` = by ``stay_id``; ``subject`` = by ``subject_id`` inside ``[intime,
    outtime)``) and an optional row filter (a condition on the concept's own columns)."""

    concept: str
    attach: Attach
    row_filter: str | None = None

    @property
    def relation(self) -> str:
        return f"{DERIVED_SCHEMA}.{self.concept}"

    @property
    def concept_file(self) -> str:
        """The vendored concept file name the EP-39 ``source_ref`` paths end with."""
        return f"{self.concept}.sql"

    @property
    def attach_rule(self) -> str:
        if self.attach == "stay":
            return "stay_id"
        return "subject_id inside [intime, outtime)"


#: The concepts read, in the order their variables first appear.
SOURCES: dict[str, HourlySource] = {
    "vitalsign": HourlySource("vitalsign", "stay"),
    "bg": HourlySource("bg", "subject", "specimen = 'ART.'"),
    "chemistry": HourlySource("chemistry", "subject"),
    "complete_blood_count": HourlySource("complete_blood_count", "subject"),
    "coagulation": HourlySource("coagulation", "subject"),
}
#: ``variable -> (concept, column)``, in the hourly spec's column order.
VARIABLES: dict[str, tuple[str, str]] = {
    "heart_rate": ("vitalsign", "heart_rate"),
    "sbp": ("vitalsign", "sbp"),
    "dbp": ("vitalsign", "dbp"),
    "mbp": ("vitalsign", "mbp"),
    "resp_rate": ("vitalsign", "resp_rate"),
    "temperature": ("vitalsign", "temperature"),
    "spo2": ("vitalsign", "spo2"),
    "glucose": ("vitalsign", "glucose"),
    "lactate": ("bg", "lactate"),
    "creatinine": ("chemistry", "creatinine"),
    "bun": ("chemistry", "bun"),
    "sodium": ("chemistry", "sodium"),
    "potassium": ("chemistry", "potassium"),
    "bicarbonate": ("chemistry", "bicarbonate"),
    "hemoglobin": ("complete_blood_count", "hemoglobin"),
    "wbc": ("complete_blood_count", "wbc"),
    "platelet": ("complete_blood_count", "platelet"),
    "ph": ("bg", "ph"),
    "pao2": ("bg", "po2"),
    "pco2": ("bg", "pco2"),
    "inr": ("coagulation", "inr"),
}


# ---------------------------------------------------------------------------
# The specs
# ---------------------------------------------------------------------------


def hourly_spec() -> MartSpec:
    return packaged_registry().spec(SPEC_HOURLY)


def variables_spec() -> MartSpec:
    return packaged_registry().spec(SPEC_VARIABLES)


def population_spec() -> MartSpec:
    return packaged_registry().spec(SPEC_POPULATION)


def hourly_family(mart: MartSpec | None = None) -> ColumnFamily:
    """The hourly spec's :data:`FAMILY` column family."""
    spec = mart or hourly_spec()
    for entry in spec.columns:
        if isinstance(entry, ColumnFamily) and entry.family == FAMILY:
            return entry
    raise MartBuildError(f"{spec.name}: no column family {FAMILY!r}")


def check_variables(mart: MartSpec | None = None) -> list[str]:
    """Disagreements between the spec's family and :data:`VARIABLES` / :data:`SOURCES`
    (empty when they agree): the same variables in the same order, the :data:`STATS`, a
    concept group on every variable, every concept a known source."""
    family = hourly_family(mart)
    problems: list[str] = []
    names = [v.name for v in family.vars]
    if names != list(VARIABLES):
        problems.append(f"family variables {names} != VARIABLES {list(VARIABLES)}")
    if tuple(family.stats) != STATS:
        problems.append(f"family stats {list(family.stats)} != {list(STATS)}")
    problems.extend(
        f"variable {v.name} has no concept_group" for v in family.vars if not v.concept_group
    )
    problems.extend(
        f"variable {name} reads unknown concept {concept}"
        for name, (concept, _column) in VARIABLES.items()
        if concept not in SOURCES
    )
    return problems


# ---------------------------------------------------------------------------
# SQL builders (text only; the steps run it)
# ---------------------------------------------------------------------------


def _bin_expr() -> str:
    from mimicwarehouse.timesem import sql_hour_bin, sql_hours_since

    return sql_hour_bin(sql_hours_since("s.intime", "x.charttime"))


def _last_bin_expr() -> str:
    """``ceil(los_icu_hours)``: the last hour bin a stay keeps (inclusive)."""
    from mimicwarehouse.timesem import sql_hours_since

    return f"CAST(ceil({sql_hours_since('s.intime', 's.outtime')}) AS INTEGER)"


def source_hourly_sql(concept: str, max_hours: int) -> str:
    """One concept's rows reduced per ``(stay_id, hour_bin)``: ``stay_id, hour_bin`` and
    ``<var>_mean / _min / _max / _n / _last`` for each of its variables (module
    docstring)."""
    if concept not in SOURCES:
        raise MartBuildError(f"concept {concept!r} is not an hourly source")
    if int(max_hours) < 1:
        raise MartBuildError(f"max_hours = {max_hours} is invalid (>= 1)")
    source = SOURCES[concept]
    columns = [(v, col) for v, (c, col) in VARIABLES.items() if c == concept]
    if not columns:
        raise MartBuildError(f"concept {concept!r} feeds no hourly variable")
    if source.attach == "stay":
        join = f"JOIN {STAYS_TABLE} AS s ON s.stay_id = x.stay_id"
    else:
        join = (
            f"JOIN {STAYS_TABLE} AS s ON s.subject_id = x.subject_id\n"
            "    AND x.charttime >= s.intime AND x.charttime < s.outtime"
        )
    measured = " OR ".join(f"x.{col} IS NOT NULL" for _v, col in columns)
    where = [f"({measured})"]
    if source.row_filter:
        where.append(f"x.{source.row_filter}")
    values = ",\n    ".join(f"CAST(x.{col} AS DOUBLE) AS {v}" for v, col in columns)
    reductions: list[str] = []
    for v, _col in columns:
        reductions.extend(
            [
                f"avg({v}) AS {v}_mean",
                f"min({v}) AS {v}_min",
                f"max({v}) AS {v}_max",
                f"count({v}) AS {v}_n",
                f"arg_max({v}, (charttime, {v})) FILTER (WHERE {v} IS NOT NULL) AS {v}_last",
            ]
        )
    return (
        "SELECT stay_id, hour_bin,\n  "
        + ",\n  ".join(reductions)
        + "\nFROM (\n"
        + f"  SELECT s.stay_id, x.charttime, {_bin_expr()} AS hour_bin,\n"
        + f"    {_last_bin_expr()} AS last_bin,\n    {values}\n"
        + f"  FROM {source.relation} AS x\n  {join}\n"
        + f"  WHERE {' AND '.join(where)}\n"
        + ") AS b\n"
        + f"WHERE hour_bin >= 0 AND hour_bin <= last_bin AND hour_bin < {int(max_hours)}\n"
        + "GROUP BY stay_id, hour_bin"
    )


def hourly_cte_sql(max_hours: int) -> str:
    """The hourly mart's rows (``cte_r``): one per ``(stay_id, hour_bin)`` with a value,
    ``subject_id, stay_id, hour_bin`` and every ``<var>_<stat>`` column."""
    concepts = list(dict.fromkeys(concept for concept, _col in VARIABLES.values()))
    parts = [f"src_{c} AS (\n{source_hourly_sql(c, max_hours)}\n)" for c in concepts]
    keys = "\n  UNION\n  ".join(f"SELECT stay_id, hour_bin FROM src_{c}" for c in concepts)
    # a count is 0, never NULL, when the variable's source has no row in the hour
    picks = [
        (
            f"coalesce(src_{concept}.{v}_{stat}, 0) AS {v}_{stat}"
            if stat == "n"
            else f"src_{concept}.{v}_{stat} AS {v}_{stat}"
        )
        for v, (concept, _col) in VARIABLES.items()
        for stat in STATS
    ]
    joins = "\n".join(
        f"LEFT JOIN src_{c} ON src_{c}.stay_id = k.stay_id AND src_{c}.hour_bin = k.hour_bin"
        for c in concepts
    )
    return (
        "WITH "
        + ",\n".join(parts)
        + "\nSELECT s.subject_id, k.stay_id, k.hour_bin,\n  "
        + ",\n  ".join(picks)
        + f"\nFROM (\n  {keys}\n) AS k\n"
        + f"JOIN {STAYS_TABLE} AS s ON s.stay_id = k.stay_id\n"
        + joins
    )


def hourly_sql(mart: MartSpec | None = None, max_hours: int = 336) -> str:
    """The hourly mart's full statement (the spec SELECT list over ``cte_r``)."""
    return mart_sql(mart or hourly_spec(), {"r": hourly_cte_sql(max_hours)})


def population_sql(relation: str = HOURLY_VIEW, max_hour: int = POPULATION_MAX_HOUR) -> str:
    """The population's raw aggregate (before release) over ``relation``, the hourly mart:
    ``hour_bin, variable, era, n_stays, q`` (the :data:`QUANTILES`), ``mean``."""
    names = list(VARIABLES)
    picks = ",\n    ".join(f'h."{v}_mean" AS {v}' for v in names)
    quantiles = ", ".join(repr(q) for q in QUANTILES)
    return (
        "WITH hv AS (\n"
        f"  SELECT h.stay_id, h.hour_bin, coalesce(p.anchor_year_group, '{UNKNOWN_ERA}') AS e,\n"
        f"    {picks}\n"
        f"  FROM {relation} AS h\n"
        f"  LEFT JOIN {PATIENTS_TABLE} AS p ON p.subject_id = h.subject_id\n"
        f"  WHERE h.hour_bin <= {int(max_hour)}\n"
        "), u AS (\n"
        "  SELECT stay_id, hour_bin, e, variable, v FROM hv\n"
        f"  UNPIVOT (v FOR variable IN ({', '.join(names)}))\n"
        ")\n"
        "SELECT hour_bin, variable,\n"
        f"  CASE WHEN grouping(e) = 1 THEN '{ALL_ERA}' ELSE e END AS era,\n"
        "  count(*) AS n_stays,\n"
        f"  quantile_cont(v, [{quantiles}]) AS q,\n"
        '  avg(v) AS "mean"\n'
        "FROM u\n"
        "GROUP BY GROUPING SETS ((hour_bin, variable, e), (hour_bin, variable))\n"
        "ORDER BY hour_bin, variable, era"
    )


# ---------------------------------------------------------------------------
# The dictionary
# ---------------------------------------------------------------------------


def variable_items(
    variable: str, mart: MartSpec | None = None, catalogue: ItemCatalogue | None = None
) -> list[int]:
    """The curated itemids behind ``variable``: the EP-39 catalogue's items of its concept
    group whose ``source_ref`` is its concept file, ascending."""
    from mimicwarehouse.units import load_catalogue

    family = hourly_family(mart)
    var = next((v for v in family.vars if v.name == variable), None)
    if var is None or variable not in VARIABLES:
        raise MartBuildError(f"{variable!r} is not an hourly variable")
    source = SOURCES[VARIABLES[variable][0]]
    cat = catalogue or load_catalogue()
    return sorted(
        i.itemid
        for i in cat.items
        if i.concept_group == var.concept_group
        and i.source_ref.strip().endswith("/" + source.concept_file)
    )


def variables_frame(
    mart: MartSpec | None = None, catalogue: ItemCatalogue | None = None
) -> polars.DataFrame:
    """The dictionary's rows (module docstring); :class:`MartBuildError` when the spec and
    :data:`VARIABLES` disagree, a variable has no curated itemid, or an itemid is missing
    from its concept file."""
    import polars as pl

    from mimicwarehouse.marts.itemid_rollups import check_source_refs
    from mimicwarehouse.units import load_catalogue

    spec = mart or hourly_spec()
    problems = check_variables(spec)
    if problems:
        raise MartBuildError(f"{spec.name}: " + "; ".join(problems))
    cat = catalogue or load_catalogue()
    by_id = {i.itemid: i for i in cat.items}
    rows: list[dict[str, Any]] = []
    for ordinal, var in enumerate(hourly_family(spec).vars, start=1):
        concept, column = VARIABLES[var.name]
        source = SOURCES[concept]
        ids = variable_items(var.name, spec, cat)
        if not ids:
            raise MartBuildError(
                f"{spec.name}: variable {var.name} has no curated itemid of "
                f"{var.concept_group} in {source.concept_file}"
            )
        check = check_source_refs([by_id[i] for i in ids])
        if not check.ok:
            raise MartBuildError(f"{spec.name}: " + "; ".join(check.problems))
        rows.append(
            {
                "ordinal": ordinal,
                "variable": var.name,
                "label": var.label,
                "unit": var.unit,
                "concept_group": var.concept_group,
                "source_concept": source.relation,
                "source_column": column,
                "row_filter": source.row_filter,
                "attach": source.attach_rule,
                "itemids": ", ".join(str(i) for i in ids),
                "stats": ", ".join(STATS),
            }
        )
    schema = {
        "ordinal": pl.Int32,
        "variable": pl.String,
        "label": pl.String,
        "unit": pl.String,
        "concept_group": pl.String,
        "source_concept": pl.String,
        "source_column": pl.String,
        "row_filter": pl.String,
        "attach": pl.String,
        "itemids": pl.String,
        "stats": pl.String,
    }
    return pl.DataFrame(rows, schema=schema)


# ---------------------------------------------------------------------------
# The population: raw aggregate -> released frame
# ---------------------------------------------------------------------------


def compute_population(
    con: duckdb.DuckDBPyConnection,
    relation: str = HOURLY_VIEW,
    max_hour: int = POPULATION_MAX_HOUR,
) -> polars.DataFrame:
    """The raw population rows (``hour_bin, variable, era, n_stays, p05 … p95, mean``).
    Unsuppressed -- never returned to a caller outside the build (:func:`release_population`
    is the published shape)."""
    import polars as pl

    rows: list[dict[str, Any]] = []
    for hour_bin, variable, era, n_stays, q, mean in con.execute(
        population_sql(relation, max_hour)
    ).fetchall():
        quantiles = list(q) if q is not None else [None] * len(QUANTILES)
        row: dict[str, Any] = {
            "hour_bin": int(hour_bin),
            "variable": str(variable),
            "era": str(era),
            POPULATION_COUNT: int(n_stays),
        }
        row.update(dict(zip(QUANTILE_COLUMNS, quantiles, strict=True)))
        row["mean"] = mean
        rows.append(row)
    schema: dict[str, Any] = {
        "hour_bin": pl.Int32,
        "variable": pl.String,
        "era": pl.String,
        POPULATION_COUNT: pl.Int64,
    }
    schema.update(dict.fromkeys(QUANTILE_COLUMNS, pl.Float64))
    schema["mean"] = pl.Float64
    return pl.DataFrame(rows, schema=schema)


def release_population(raw: polars.DataFrame, k: int) -> polars.DataFrame:
    """The published shape (module docstring): every row kept; per hour x variable the era
    rows suppressed through :func:`disclose.suppress` (``era`` the group column, the ``all``
    row the margin), the ``all`` row hidden below ``k``; the count and the statistics
    blanked on a hidden row, plus ``suppressed`` and ``k``."""
    import polars as pl

    from mimicwarehouse import disclose

    rid = "_mwh_rid"
    marker = f"{POPULATION_COUNT}{disclose.MARKER_SUFFIX}"
    indexed = raw.with_row_index(rid)
    hidden = [False] * indexed.height
    for _key, group in indexed.group_by(["hour_bin", "variable"], maintain_order=True):
        eras = group.filter(pl.col("era") != ALL_ERA)
        if eras.height:
            out, _report = disclose.suppress(
                eras.select(rid, "era", POPULATION_COUNT),
                k=k,
                count_cols=[POPULATION_COUNT],
                group_cols=["era"],
                complementary=True,
            )
            for r, flag in out.select(rid, marker).rows():
                hidden[int(r)] = bool(flag)
        for r, n in group.filter(pl.col("era") == ALL_ERA).select(rid, POPULATION_COUNT).rows():
            hidden[int(r)] = n is not None and 0 < int(n) < k
    released = indexed.with_columns(pl.Series("suppressed", hidden, dtype=pl.Boolean))
    released = released.with_columns(
        [
            pl.when(pl.col("suppressed")).then(None).otherwise(pl.col(c)).alias(c)
            for c in POPULATION_BLANKED
        ]
    ).with_columns(pl.lit(k, dtype=pl.Int32).alias("k"))
    return released.drop(rid)


def stage_frame(
    con: duckdb.DuckDBPyConnection, mart: MartSpec, frame: polars.DataFrame, table: str
) -> None:
    """``frame`` as the temp table ``table``, typed by the spec's columns: registered as
    Arrow and cast in one statement (an ``executemany`` of the population's 21k rows took
    about 10 s)."""
    source = f"{table}_frame"
    con.register(source, frame.select(list(mart.column_names)).to_arrow())
    try:
        casts = ", ".join(f'CAST("{c.name}" AS {c.type}) AS "{c.name}"' for c in mart.expanded)
        con.execute(f"CREATE OR REPLACE TEMP TABLE {table} AS SELECT {casts} FROM {source}")
    finally:
        con.unregister(source)


# ---------------------------------------------------------------------------
# The steps
# ---------------------------------------------------------------------------


def _sql_str(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def bind_population_sources(ctx: StepContext) -> None:
    """The patients view and :data:`HOURLY_VIEW` over the published hourly mart on the
    build connection; :class:`MartBuildError` names what is missing and the remedy."""
    from mimicwarehouse.qc.profile import ensure_table_views

    if not mart_complete(ctx.lake_root, ctx.tier, hourly_spec()):
        raise MartBuildError(
            f"{SPEC_POPULATION}: marts.{SPEC_HOURLY} is not built for tier {ctx.tier} - run "
            f"`mwh build --tier {ctx.tier} --select {STEP_HOURLY}` first"
        )
    ensure_table_views(ctx, [PATIENTS_TABLE])
    present = {
        str(r[0])
        for r in ctx.con.execute(
            "SELECT table_schema || '.' || table_name FROM information_schema.tables"
        ).fetchall()
    }
    if PATIENTS_TABLE not in present:
        raise MartBuildError(
            f"{SPEC_POPULATION}: {PATIENTS_TABLE} not staged for tier {ctx.tier} - run "
            f"`mwh build --tier {ctx.tier} --select stage.{PATIENTS_TABLE}` first"
        )
    glob = mart_glob(mart_dir(ctx.lake_root, ctx.tier, SPEC_HOURLY), partitioned=True)
    schema = HOURLY_VIEW.split(".", 1)[0]
    ctx.con.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
    ctx.con.execute(
        f"CREATE OR REPLACE VIEW {HOURLY_VIEW} AS SELECT * FROM "
        f"read_parquet({_sql_str(glob)}, hive_partitioning = false)"
    )


def run_icustay_hourly(step: Step, ctx: StepContext) -> StepOutcome:
    """The ``marts.icustay_hourly`` handler (module docstring)."""
    from mimicwarehouse.marts.first_day import bind_sources

    mart = hourly_spec()
    max_hours = int(ctx.settings.marts_hourly_max_hours)
    try:
        problems = check_variables(mart)
        if problems:
            raise MartBuildError(f"{mart.name}: " + "; ".join(problems))
        bind_sources(ctx, mart)
    except MartBuildError as exc:
        record_failure(ctx, mart, type(exc).__name__)
        raise
    _LOG.info(
        "mart %s (%s): %d variable(s) from %d concept(s), hour bins 0..%d",
        mart.name,
        ctx.tier,
        len(VARIABLES),
        len(SOURCES),
        max_hours - 1,
    )
    return write_mart(
        ctx,
        mart,
        hourly_sql(mart, max_hours),
        layers_read=("core", "derived"),
        params={"max_hours": max_hours},
    )


def run_hourly_variables(step: Step, ctx: StepContext) -> StepOutcome:
    """The ``marts.hourly_variables`` handler: the dictionary frame, staged, the writer
    (it reads nothing from the lake)."""
    mart = variables_spec()
    try:
        frame = variables_frame()
    except MartBuildError as exc:
        record_failure(ctx, mart, type(exc).__name__)
        raise
    stage_frame(ctx.con, mart, frame, RELEASED_VARIABLES)
    try:
        return write_mart(
            ctx, mart, mart_sql(mart, {"v": f"SELECT * FROM {RELEASED_VARIABLES}"}), layers_read=()
        )
    finally:
        ctx.con.execute(f"DROP TABLE IF EXISTS {RELEASED_VARIABLES}")


def run_hourly_population(step: Step, ctx: StepContext) -> StepOutcome:
    """The ``marts.hourly_population`` handler (module docstring): the raw aggregate over
    the published hourly mart (measured, one ``kind: query`` line), the released frame,
    the writer."""
    import duckdb

    from mimicwarehouse import run as run_mod
    from mimicwarehouse.run import ResourceLog
    from mimicwarehouse.safe import sanitize_error_text

    mart = population_spec()
    try:
        bind_population_sources(ctx)
        raw, usage = ResourceLog.measure(
            lambda: compute_population(ctx.con), data_root=ctx.settings.data_root
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
        POPULATION_BENCH_STEP,
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
    released = release_population(raw, k)
    _LOG.info(
        "mart %s (%s): %d row(s), %d withheld at k=%d",
        mart.name,
        ctx.tier,
        released.height,
        int(released.get_column("suppressed").sum()),
        k,
    )
    stage_frame(ctx.con, mart, released, RELEASED_POPULATION)
    try:
        return write_mart(
            ctx,
            mart,
            mart_sql(mart, {"p": f"SELECT * FROM {RELEASED_POPULATION}"}),
            layers_read=("core", "marts"),
            params={"max_hour": POPULATION_MAX_HOUR, "quantiles": list(QUANTILES)},
        )
    finally:
        ctx.con.execute(f"DROP TABLE IF EXISTS {RELEASED_POPULATION}")


__all__ = [
    "ALL_ERA",
    "DERIVED_SCHEMA",
    "FAMILY",
    "HOURLY_VIEW",
    "PATIENTS_TABLE",
    "POPULATION_BENCH_STEP",
    "POPULATION_BLANKED",
    "POPULATION_COUNT",
    "POPULATION_MAX_HOUR",
    "QUANTILES",
    "QUANTILE_COLUMNS",
    "RELEASED_POPULATION",
    "RELEASED_VARIABLES",
    "SOURCES",
    "SPEC_HOURLY",
    "SPEC_POPULATION",
    "SPEC_VARIABLES",
    "STATS",
    "STAYS_TABLE",
    "STEP_HOURLY",
    "STEP_POPULATION",
    "STEP_VARIABLES",
    "UNKNOWN_ERA",
    "VARIABLES",
    "HourlySource",
    "bind_population_sources",
    "check_variables",
    "compute_population",
    "hourly_cte_sql",
    "hourly_family",
    "hourly_spec",
    "hourly_sql",
    "population_spec",
    "population_sql",
    "release_population",
    "run_hourly_population",
    "run_hourly_variables",
    "run_icustay_hourly",
    "source_hourly_sql",
    "stage_frame",
    "variable_items",
    "variables_frame",
    "variables_spec",
]
