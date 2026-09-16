"""Measurement-process summaries — the descriptive half of capability 7 (EP-45; DESIGN
§14/§15, GOVERNANCE §4/§5/§7, D-5, D-17, D-33, D-40).

In ICU data *whether* something was measured carries information (informative
presence), and an absence has two very different causes: **structural** (the item is not
charted in that care unit or era at all) and **unmeasured** (the item is in use but this
stay did not get it). This module computes the descriptive summaries — aggregates only,
in DuckDB over the tier's staged lake — for a configurable itemid set (default: every
curated item of the EP-39 catalogue, ``meta.item_units``) and publishes them as
``lake/meta/<tier>/mp_*.parquet`` (EP-29's meta layout, registered as ``meta.mp_*`` by
EP-37's discovery walker at catalog time):

``meta.mp_item_hourly`` / ``meta.mp_item_daily``
    per itemid xICU hour bin (``[0, 1) … [0, 168)`` hours since ``intime`` — the
    ``timesem`` ``hour_bin`` grain) / ICU day (``timesem`` ``icu_day``, 14 days):
    ``n_stays_at_risk`` (stays still in the ICU when the bin starts), ``n_stays_measured``
    and ``n_measurements``.
``meta.mp_item_summary``
    per itemid: population size, stays with any measurement, stays measured in the first
    24 h and their share, the median measurements per stay-day, and the inter-measurement
    interval in minutes (p10 / median / p90).
``meta.mp_structural``
    per itemid x``first_careunit`` xera (``patients.anchor_year_group``, the only
    admissible era axis): ``n_stays``, ``n_stays_measured`` (any time in the stay),
    ``share`` and ``structural_flag`` — ``structural`` when the share is 0 in a cell of at
    least ``min_stays`` (50) stays, ``sparse`` below ``sparse_share`` (5 %), else
    ``in_use``.
``meta.mp_absence_summary``
    per itemid: stays without a measurement in the first 24 h, split into ``n_structural``
    (their unit xera cell is structural) and ``n_unmeasured`` (the rest).
``meta.mp_presence_outcome``
    per curated lab: in-hospital mortality (``admissions.hospital_expire_flag``) among
    stays *with* vs *without* a measurement in the first 24 h — ``n_stays``,
    ``n_deaths``, ``mortality_rate`` per arm and the rate ratio with a Wald 95 % CI
    (statsmodels ``Table2x2``, computed in Python from the released counts) — and the
    count-arm version (``0`` / ``1-2`` / ``>=3`` measurements); every row carries
    ``claim_type = 'exploratory'`` and the caveat that this is descriptive association.

**Definitions.** The population is every ICU stay with a valid ``[intime, outtime)`` and
a ``patients`` row; a *measurement occasion* is a distinct ``(stay, itemid, charttime)``
(chartevents' upstream duplicates count once); ``chartevents`` / ``outputevents`` /
``inputevents`` events attach to their ``stay_id``, ``labevents`` (no ``stay_id``) to the
stay of the same subject whose window contains ``charttime``; events outside the window
are not measurements of the stay (EP-44's ``event_window`` check counts them).

**Steps** (``dag/specs/measurement.yaml``, tag ``measurement``; ``python`` kind, each
with optional ``params`` — :class:`MeasurementParams`): ``measurement.hourly``,
``measurement.structural`` and ``measurement.presence`` each scan the event tables once
on the build connection (a temp table of occasions per source, EP-19's single writer),
measure themselves with :class:`~mimicwarehouse.run.ResourceLog` and write a raw *slice*
under ``lake/meta/<tier>/raw/measurement/`` (data root only; never walked into a
catalog); ``measurement.report`` reads the three slices (a missing or stale one refuses
with the remedy), opens the one ``run.start(kind="qc")`` run, applies EP-43's
``disclose.suppress`` (k = 11, complementary; the item summary and the absence summary
are suppressed as **one** frame so their shared counts cannot back each other out; the
presence arms carry the cross-contrast rule — the binary ``measured`` arm is the sum of
the two upper count arms), writes the six ``meta.mp_*`` tables with one ``kind: query``
benchmark line per compute step, and renders ``runs/<run_id>/measurement_process.md``
(+ six CSVs) once the run is closed so the reproduction block carries the final numbers.
``mwh qc measurement --tier <t>`` reads the published tables through ``safe_query``.

Everything computed, written, logged or returned is counts, shares, quantiles,
dictionary labels and timings — never a row (GOVERNANCE §4). Import budget: stdlib and
pydantic at import time (the catalog builder imports this module for its extension);
duckdb / polars / statsmodels / ``run`` / ``units`` load inside function bodies.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from mimicwarehouse.qc.profile import (
    VALUE_MAX_CHARS,
    QcError,
    ensure_table_views,
    meta_table_path,
    present_tables,
    relation,
    write_frame_parquet,
)
from mimicwarehouse.timesem import ERAS, HOUR_BIN, ICU_DAY, sql_hour_bin, sql_hours_since

if TYPE_CHECKING:  # pragma: no cover
    import duckdb

    from mimicwarehouse.config import Settings
    from mimicwarehouse.dag.runner import StepContext, StepOutcome
    from mimicwarehouse.dag.spec import Step
    from mimicwarehouse.units import ItemCatalogue

_LOG = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------

DAG_TAG = "measurement"
STEP_HOURLY = "measurement.hourly"
STEP_STRUCTURAL = "measurement.structural"
STEP_PRESENCE = "measurement.presence"
STEP_REPORT = "measurement.report"
#: The compute steps, in spec order — one slice and one ``kind: query`` line each.
COMPUTE_STEPS: tuple[str, ...] = (STEP_HOURLY, STEP_STRUCTURAL, STEP_PRESENCE)
#: The provenance run the report step opens (EP-35 ``RunKind``) and its benchmark kind.
RUN_NAME = "measurement"
RUN_KIND = "qc"
BENCH_KIND = "query"
#: The claim-type label the run, the tables and the report carry (GOVERNANCE §7).
CLAIM_TYPE = "exploratory (measurement process)"
CLAIM_TYPE_SHORT = "exploratory"
#: The one-line caveat every ``meta.mp_presence_outcome`` row carries (<= 64 chars).
CAVEAT = "descriptive association only; not adjusted, not causal"

#: The six ``lake/meta/<tier>/<table>.parquet`` files -> ``meta.<table>`` (EP-37 walker).
HOURLY_TABLE = "mp_item_hourly"
DAILY_TABLE = "mp_item_daily"
SUMMARY_TABLE = "mp_item_summary"
STRUCTURAL_TABLE = "mp_structural"
ABSENCE_TABLE = "mp_absence_summary"
PRESENCE_TABLE = "mp_presence_outcome"
META_TABLES: tuple[str, ...] = (
    HOURLY_TABLE,
    DAILY_TABLE,
    SUMMARY_TABLE,
    STRUCTURAL_TABLE,
    ABSENCE_TABLE,
    PRESENCE_TABLE,
)
#: Slices: ``meta/<tier>/raw/measurement/<step>.json`` + ``<step>.<frame>.parquet``.
SLICES_DIRNAME = "measurement"
#: The report files written into the ``kind: qc`` run's folder (``docs/committed-text.md``:
#: no run id in a file name).
REPORT_FILENAME = "measurement_process.md"
REPORT_FILES: tuple[str, ...] = (REPORT_FILENAME, *(f"{t}.csv" for t in META_TABLES))

#: Structural flags, from "in use" to "never charted".
FLAG_IN_USE = "in_use"
FLAG_SPARSE = "sparse"
FLAG_STRUCTURAL = "structural"
FLAGS: tuple[str, ...] = (FLAG_IN_USE, FLAG_SPARSE, FLAG_STRUCTURAL)
#: Presence-outcome contrasts and arms (the reference arm first).
CONTRAST_BINARY = "binary"
CONTRAST_COUNT = "count"
CONTRASTS: tuple[str, ...] = (CONTRAST_BINARY, CONTRAST_COUNT)
ARM_NOT_MEASURED = "not_measured"
ARM_MEASURED = "measured"
BINARY_ARMS: tuple[str, ...] = (ARM_NOT_MEASURED, ARM_MEASURED)
COUNT_ARMS: tuple[str, ...] = ("0", "1-2", ">=3")
REFERENCE_ARMS: dict[str, str] = {CONTRAST_BINARY: ARM_NOT_MEASURED, CONTRAST_COUNT: "0"}
#: What a NULL care unit / era reads as (a cell of its own).
UNKNOWN_LABEL = "(unknown)"

#: The core tables every step needs, and the event tables per EP-39 source:
#: ``(qualified table, time column, join key on the population)``.
STAYS_TABLE = "mimiciv_icu.icustays"
PATIENTS_TABLE = "mimiciv_hosp.patients"
ADMISSIONS_TABLE = "mimiciv_hosp.admissions"
SOURCE_EVENTS: dict[str, tuple[str, str, str]] = {
    "chartevents": ("mimiciv_icu.chartevents", "charttime", "stay_id"),
    "labevents": ("mimiciv_hosp.labevents", "charttime", "subject_id"),
    "outputevents": ("mimiciv_icu.outputevents", "charttime", "stay_id"),
    "inputevents": ("mimiciv_icu.inputevents", "starttime", "stay_id"),
}
#: The source whose items feed the presence-outcome summary (the brief: "each curated lab").
PRESENCE_SOURCE = "labevents"

#: Temp relations on the build connection (dropped when a compute step ends).
_POP = "_mwh_mp_pop"
_OCC = "_mwh_mp_occ"

#: Count columns per published table (the ``disclose.suppress`` count_cols).
N_AT_RISK = "n_stays_at_risk"
N_MEASURED = "n_stays_measured"
N_MEASUREMENTS = "n_measurements"
N_STAYS = "n_stays"
N_MEASURED_24H = "n_stays_measured_first_24h"
N_MISSING = "n_missing_first_24h"
N_STRUCTURAL = "n_structural"
N_UNMEASURED = "n_unmeasured"
N_DEATHS = "n_deaths"
SHARE_24H = "measured_first_24h_share"
STAT_COLUMNS: tuple[str, ...] = (
    "median_per_stay_day",
    "median_interval_min",
    "p10_interval_min",
    "p90_interval_min",
)
RR_COLUMNS: tuple[str, ...] = ("rate_ratio", "rr_ci_low", "rr_ci_high")
#: Wall-clock / RSS keys the slices carry (from ``ResourceLog``).
_TELEMETRY: tuple[str, ...] = ("wall_s", "peak_rss_mb", "disk_delta_mb")


# ---------------------------------------------------------------------------
# Parameters (a python step's ``params`` mapping)
# ---------------------------------------------------------------------------


class MeasurementParams(BaseModel):
    """The knobs of the measurement steps (module docstring). ``itemids`` is a subset of
    the curated catalogue (``None`` = every curated item); the bins and thresholds are
    the brief's defaults."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    itemids: tuple[int, ...] | None = None
    hours: int = Field(default=168, ge=1, le=24 * 365)
    days: int = Field(default=14, ge=1, le=365)
    first_window_hours: int = Field(default=24, ge=1)
    min_stays: int = Field(default=50, ge=1)
    sparse_share: float = Field(default=0.05, gt=0.0, lt=1.0)

    def resolve_itemids(self, catalogue: ItemCatalogue) -> tuple[int, ...]:
        """The curated itemids this run covers, ascending; an itemid outside the
        catalogue is refused (:class:`QcError`) — labels, sources and the presence
        source come from the catalogue."""
        curated = catalogue.itemids()
        if self.itemids is None:
            return curated
        unknown = sorted(set(self.itemids) - set(curated))
        if unknown:
            raise QcError(
                f"params.itemids: {unknown} not curated (units.py, EP-39) — the measurement "
                "steps cover curated itemids only"
            )
        return tuple(sorted(set(self.itemids)))

    def sha256(self) -> str:
        """sha256 of the canonical JSON (the ``measurement_params`` ref of the run)."""
        payload = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def params_of(step: Step | None) -> MeasurementParams:
    """The validated :class:`MeasurementParams` of a step (its ``params`` mapping, or the
    defaults); :class:`QcError` names every validation problem."""
    raw = dict(step.params or {}) if step is not None else {}
    try:
        return MeasurementParams.model_validate(raw)
    except ValidationError as exc:
        name = step.name if step is not None else "measurement"
        lines = [f"{name}: params: {exc.error_count()} validation error(s)"]
        for e in exc.errors():
            loc = ".".join(str(p) for p in e["loc"])
            lines.append(f"  {loc}: {e['msg']}")
        raise QcError("\n".join(lines)) from None


# ---------------------------------------------------------------------------
# SQL builders (every builder returns text; the callers run it)
# ---------------------------------------------------------------------------


def _sql_str(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _ids(itemids: Sequence[int]) -> str:
    return ", ".join(str(int(i)) for i in itemids)


def population_sql() -> str:
    """The population relation: every ICU stay with a valid ``[intime, outtime)`` and a
    ``patients`` row — ``stay_id, subject_id, hadm_id, careunit, era, intime, outtime,
    los_hours``; a NULL care unit / era reads :data:`UNKNOWN_LABEL`."""
    return (
        "SELECT i.stay_id, i.subject_id, i.hadm_id, "
        f"coalesce(i.first_careunit, {_sql_str(UNKNOWN_LABEL)}) AS careunit, "
        f"coalesce(p.anchor_year_group, {_sql_str(UNKNOWN_LABEL)}) AS era, "
        f"i.intime, i.outtime, {sql_hours_since('i.intime', 'i.outtime')} AS los_hours "
        f"FROM {relation(STAYS_TABLE)} AS i "
        f"JOIN {relation(PATIENTS_TABLE)} AS p ON i.subject_id = p.subject_id "
        "WHERE i.intime IS NOT NULL AND i.outtime IS NOT NULL AND i.outtime > i.intime"
    )


def population_counts_sql() -> str:
    """``(n_stays_total, n_population)`` — the stays the population keeps vs every
    ``icustays`` row (the difference is what the report calls excluded)."""
    return (
        f"SELECT (SELECT count(*) FROM {relation(STAYS_TABLE)}), "
        f"(SELECT count(*) FROM ({population_sql()}))"
    )


def occasions_sql(source: str, itemids: Sequence[int], *, pop: str = _POP) -> str:
    """The distinct measurement occasions of ``source``'s curated itemids inside the
    population stays' windows: ``stay_id, itemid, charttime, hours_since`` (module
    docstring: ``labevents`` attach by subject and window, the ICU tables by stay)."""
    table, time_col, key = SOURCE_EVENTS[source]
    t = f"e.{_q(time_col)}"
    return (
        f"SELECT DISTINCT p.stay_id, e.itemid, {t} AS charttime, "
        f"{sql_hours_since('p.intime', t)} AS hours_since "
        f"FROM {relation(table)} AS e JOIN {pop} AS p ON e.{_q(key)} = p.{_q(key)} "
        f"WHERE e.itemid IN ({_ids(itemids)}) AND {t} >= p.intime AND {t} < p.outtime"
    )


def at_risk_sql(n_bins: int, width_h: float, index_name: str, *, pop: str = _POP) -> str:
    """``(index, n_stays_at_risk)`` per bin: stays whose ``los_hours`` exceeds the bin
    start (a stay is at risk in every bin that starts before its ``outtime``)."""
    return (
        f"SELECT CAST(t.b AS INTEGER) AS {index_name}, count(*) AS {N_AT_RISK} "
        f"FROM {pop} AS p, range(0, {int(n_bins)}) AS t(b) "
        f"WHERE p.los_hours > t.b * {float(width_h)!r} GROUP BY 1"
    )


def binned_sql(n_bins: int, width_h: float, index_name: str, *, occ: str = _OCC) -> str:
    """``(itemid, index, n_stays_measured, n_measurements)`` per bin of ``width_h`` hours
    since ``intime`` (``timesem.sql_hour_bin`` — ``[start, end)`` bins from 0)."""
    bin_expr = sql_hour_bin("hours_since", width_h)
    return (
        f"SELECT itemid, {bin_expr} AS {index_name}, count(DISTINCT stay_id) AS {N_MEASURED}, "
        f"count(*) AS {N_MEASUREMENTS} FROM {occ} "
        f"WHERE hours_since >= 0 AND {bin_expr} < {int(n_bins)} GROUP BY 1, 2"
    )


def per_stay_day_sql(*, occ: str = _OCC, pop: str = _POP) -> str:
    """``(itemid, median_per_stay_day)``: the median over measured stays of occasions per
    stay-day (``n / (los_hours / 24)``)."""
    return (
        "SELECT itemid, quantile_cont(n_occ / (los_hours / 24.0), 0.5) AS median_per_stay_day "
        "FROM (SELECT o.itemid, o.stay_id, count(*) AS n_occ, any_value(p.los_hours) AS los_hours "
        f"FROM {occ} AS o JOIN {pop} AS p ON o.stay_id = p.stay_id GROUP BY 1, 2) GROUP BY 1"
    )


def intervals_sql(*, occ: str = _OCC) -> str:
    """``(itemid, p10_interval_min, median_interval_min, p90_interval_min)`` over the
    gaps between consecutive occasions of one stay (minutes)."""
    return (
        "SELECT itemid, quantile_cont(gap_min, 0.1) AS p10_interval_min, "
        "quantile_cont(gap_min, 0.5) AS median_interval_min, "
        "quantile_cont(gap_min, 0.9) AS p90_interval_min "
        "FROM (SELECT itemid, date_diff('minute', lag(charttime) OVER "
        "(PARTITION BY stay_id, itemid ORDER BY charttime), charttime) AS gap_min "
        f"FROM {occ}) WHERE gap_min IS NOT NULL GROUP BY 1"
    )


def cells_sql(*, pop: str = _POP) -> str:
    """``(careunit, era, n_stays)`` — the unit xera cells of the population."""
    return f"SELECT careunit, era, count(*) AS {N_STAYS} FROM {pop} GROUP BY 1, 2"


def measured_cells_sql(first_window_hours: int, *, occ: str = _OCC, pop: str = _POP) -> str:
    """``(itemid, careunit, era, n_stays_measured, n_stays_measured_first_24h)`` per cell."""
    return (
        f"SELECT o.itemid, p.careunit, p.era, count(DISTINCT o.stay_id) AS {N_MEASURED}, "
        f"count(DISTINCT CASE WHEN o.hours_since < {int(first_window_hours)} THEN o.stay_id END) "
        f"AS {N_MEASURED_24H} "
        f"FROM {occ} AS o JOIN {pop} AS p ON o.stay_id = p.stay_id GROUP BY 1, 2, 3"
    )


def presence_sql(itemids: Sequence[int], first_window_hours: int) -> str:
    """The standalone presence-outcome statement (one leaf SELECT over CTEs; the shape
    ``safe_query`` admits — identifiers stay inside the chain, the final select is
    group keys plus ``count(*)`` / ``count(*) FILTER``): ``(itemid, arm, n_stays,
    n_deaths)`` per curated lab, ``arm`` in :data:`COUNT_ARMS` by the number of
    occasions in the first ``first_window_hours``; the population is the stays with an
    ``admissions.hospital_expire_flag``."""
    if not itemids:
        raise QcError("presence_sql needs at least one itemid")
    occ = occasions_sql(PRESENCE_SOURCE, itemids, pop="pop")
    return (
        f"WITH pop AS ({population_sql()}), "
        f"occ AS ({occ}), "
        f"items AS (SELECT unnest([{_ids(itemids)}]) AS itemid), "
        "outcome AS (SELECT p.stay_id, a.hospital_expire_flag FROM pop AS p "
        f"JOIN {relation(ADMISSIONS_TABLE)} AS a ON p.hadm_id = a.hadm_id "
        "WHERE a.hospital_expire_flag IS NOT NULL), "
        "per_stay AS (SELECT s.stay_id, s.hospital_expire_flag, i.itemid, "
        "count(o.charttime) AS n_occ FROM outcome AS s CROSS JOIN items AS i "
        "LEFT JOIN occ AS o ON o.stay_id = s.stay_id AND o.itemid = i.itemid "
        f"AND o.hours_since < {int(first_window_hours)} GROUP BY 1, 2, 3), "
        "armed AS (SELECT itemid, hospital_expire_flag, CASE WHEN n_occ = 0 THEN '0' "
        "WHEN n_occ <= 2 THEN '1-2' ELSE '>=3' END AS arm FROM per_stay) "
        f"SELECT itemid, arm, count(*) AS {N_STAYS}, "
        f"count(*) FILTER (WHERE hospital_expire_flag = 1) AS {N_DEATHS} "
        "FROM armed GROUP BY 1, 2"
    )


# ---------------------------------------------------------------------------
# Slices (the compute steps' raw output; data root only)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Slice:
    """One compute step's raw result: named Polars frames plus metadata (params, the
    population counts, telemetry, build / snapshot ids). Raw = unsuppressed — a slice
    never leaves ``lake/meta/<tier>/raw/``."""

    step: str
    frames: dict[str, Any]
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def params(self) -> MeasurementParams:
        return MeasurementParams.model_validate(self.meta.get("params") or {})


def slices_dir(lake_root: Path | str, tier: str) -> Path:
    """``<lake_root>/meta/<tier>/raw/measurement/``."""
    from mimicwarehouse.catalog.profile import meta_dir
    from mimicwarehouse.units import RAW_DIRNAME

    return meta_dir(lake_root, tier) / RAW_DIRNAME / SLICES_DIRNAME


def slice_meta_path(lake_root: Path | str, tier: str, step: str) -> Path:
    return slices_dir(lake_root, tier) / f"{step}.json"


def slice_frame_path(lake_root: Path | str, tier: str, step: str, name: str) -> Path:
    return slices_dir(lake_root, tier) / f"{step}.{name}.parquet"


def write_slice(con: duckdb.DuckDBPyConnection, lake_root: Path | str, tier: str, s: Slice) -> int:
    """Write the slice's frames as Parquet and its metadata as JSON; returns the bytes."""
    from mimicwarehouse.fsio import atomic_write_text

    nbytes = 0
    tables: dict[str, str] = {}
    for name, frame in s.frames.items():
        path = slice_frame_path(lake_root, tier, s.step, name)
        nbytes += write_frame_parquet(con, path, frame)
        tables[name] = path.name
    meta = {**s.meta, "step": s.step, "tables": tables}
    path = slice_meta_path(lake_root, tier, s.step)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(meta, indent=1, sort_keys=True, default=str) + "\n")
    return nbytes + path.stat().st_size


def read_slice(lake_root: Path | str, tier: str, step: str) -> Slice:
    """The slice of ``step`` (:class:`QcError` with the remedy when missing)."""
    import polars as pl

    path = slice_meta_path(lake_root, tier, step)
    if not path.is_file():
        raise QcError(
            f"no measurement slice for {step} on tier {tier} — run `mwh build --tier {tier} "
            f"--tag {DAG_TAG}` first"
        )
    meta = json.loads(path.read_text(encoding="utf-8"))
    frames: dict[str, Any] = {}
    for name in dict(meta.get("tables") or {}):
        frame_path = slice_frame_path(lake_root, tier, step, name)
        if not frame_path.is_file():
            raise QcError(
                f"measurement slice {step} on tier {tier} lacks its {name} frame — rerun "
                f"`mwh build --tier {tier} --tag {DAG_TAG}`"
            )
        frames[name] = pl.read_parquet(frame_path)
    return Slice(step=step, frames=frames, meta=meta)


# ---------------------------------------------------------------------------
# The compute functions (any connection with the core views; tests use in-memory ones)
# ---------------------------------------------------------------------------


def _frame(rows: Any, schema: Mapping[str, Any]) -> Any:
    import polars as pl

    return pl.DataFrame(rows, schema=dict(schema), orient="row")


def _schemas() -> dict[str, dict[str, Any]]:
    import polars as pl

    return {
        "binned": {
            "itemid": pl.Int64,
            "index": pl.Int32,
            N_AT_RISK: pl.Int64,
            N_MEASURED: pl.Int64,
            N_MEASUREMENTS: pl.Int64,
        },
        "stats": {
            "itemid": pl.Int64,
            "median_per_stay_day": pl.Float64,
            "median_interval_min": pl.Float64,
            "p10_interval_min": pl.Float64,
            "p90_interval_min": pl.Float64,
        },
        "cells": {
            "itemid": pl.Int64,
            "careunit": pl.String,
            "era": pl.String,
            N_STAYS: pl.Int64,
            N_MEASURED: pl.Int64,
            N_MEASURED_24H: pl.Int64,
        },
        "arms": {"itemid": pl.Int64, "arm": pl.String, N_STAYS: pl.Int64, N_DEATHS: pl.Int64},
    }


def items_by_source(
    itemids: Sequence[int], catalogue: ItemCatalogue, present: set[str]
) -> dict[str, tuple[int, ...]]:
    """``{source: itemids}`` restricted to the sources whose event table is present on
    the connection (an absent table is logged, never an error — the dev tier stages
    everything, a partial fixture may not)."""
    out: dict[str, list[int]] = {}
    for itemid in itemids:
        spec = catalogue.spec(itemid)
        table = SOURCE_EVENTS[spec.source][0]
        if table not in present:
            _LOG.warning("measurement: %s not present — skipping itemid %d", table, itemid)
            continue
        out.setdefault(spec.source, []).append(itemid)
    return {s: tuple(sorted(ids)) for s, ids in out.items() if ids}


def _require_core(present: set[str]) -> None:
    missing = [t for t in (STAYS_TABLE, PATIENTS_TABLE) if t not in present]
    if missing:
        raise QcError(
            f"{', '.join(missing)} not present on the connection — stage the core tables first"
        )


def _create_population(con: duckdb.DuckDBPyConnection) -> tuple[int, int]:
    """``_mwh_mp_pop`` on ``con``; returns ``(n_population, n_excluded)``."""
    con.execute(f"CREATE OR REPLACE TEMP TABLE {_POP} AS {population_sql()}")
    row = con.execute(population_counts_sql()).fetchone()
    assert row is not None
    total, kept = int(row[0]), int(row[1])
    return kept, total - kept


def _drop_temp(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(f"DROP TABLE IF EXISTS {_OCC}")
    con.execute(f"DROP TABLE IF EXISTS {_POP}")


def _grid(itemids: Sequence[int], at_risk: Any, measured: Any, index_name: str, n_bins: int) -> Any:
    """The complete itemid xbin frame: at-risk per bin (0 where no stay is), measured
    counts (0 where no occasion is)."""
    import polars as pl

    schema = _schemas()["binned"]
    bins = pl.DataFrame({index_name: list(range(int(n_bins)))}, schema={index_name: pl.Int32})
    items = pl.DataFrame({"itemid": [int(i) for i in itemids]}, schema={"itemid": pl.Int64})
    risk = (
        at_risk.select(pl.col(index_name).cast(pl.Int32), pl.col(N_AT_RISK).cast(pl.Int64))
        if at_risk.height
        else pl.DataFrame(schema={index_name: pl.Int32, N_AT_RISK: pl.Int64})
    )
    meas = (
        measured.select(
            pl.col("itemid").cast(pl.Int64),
            pl.col(index_name).cast(pl.Int32),
            pl.col(N_MEASURED).cast(pl.Int64),
            pl.col(N_MEASUREMENTS).cast(pl.Int64),
        )
        if measured.height
        else pl.DataFrame(
            schema={
                "itemid": pl.Int64,
                index_name: pl.Int32,
                N_MEASURED: pl.Int64,
                N_MEASUREMENTS: pl.Int64,
            }
        )
    )
    out = (
        items.join(bins, how="cross")
        .join(risk, on=index_name, how="left")
        .join(meas, on=["itemid", index_name], how="left")
        .with_columns(
            pl.col(N_AT_RISK).fill_null(0),
            pl.col(N_MEASURED).fill_null(0),
            pl.col(N_MEASUREMENTS).fill_null(0),
        )
        .sort(["itemid", index_name])
    )
    return out.select(
        pl.col("itemid").cast(schema["itemid"]),
        pl.col(index_name).cast(schema["index"]),
        pl.col(N_AT_RISK).cast(schema[N_AT_RISK]),
        pl.col(N_MEASURED).cast(schema[N_MEASURED]),
        pl.col(N_MEASUREMENTS).cast(schema[N_MEASUREMENTS]),
    )


def compute_hourly(
    con: duckdb.DuckDBPyConnection,
    params: MeasurementParams | None = None,
    *,
    catalogue: ItemCatalogue | None = None,
    present: set[str] | None = None,
) -> Slice:
    """The ``measurement.hourly`` computation (module docstring): the hourly and daily
    grids (``itemid xbin``: at risk, measured, measurements) and the per-item statistics
    (median occasions per stay-day, interval quantiles) — raw, unsuppressed."""
    import polars as pl

    from mimicwarehouse.units import load_catalogue

    params = params or MeasurementParams()
    cat = catalogue or load_catalogue()
    have = present if present is not None else present_tables(con)
    _require_core(have)
    itemids = params.resolve_itemids(cat)
    by_source = items_by_source(itemids, cat, have)
    covered = tuple(sorted(i for ids in by_source.values() for i in ids))
    schemas = _schemas()
    stats_schema = schemas["stats"]
    try:
        n_population, n_excluded = _create_population(con)
        at_risk_h = con.execute(at_risk_sql(params.hours, 1.0, "hour_bin")).pl()
        at_risk_d = con.execute(at_risk_sql(params.days, 24.0, "day_index")).pl()
        hourly_parts: list[Any] = []
        daily_parts: list[Any] = []
        stats_parts: list[Any] = []
        for source, ids in by_source.items():
            con.execute(f"CREATE OR REPLACE TEMP TABLE {_OCC} AS {occasions_sql(source, ids)}")
            hourly_parts.append(con.execute(binned_sql(params.hours, 1.0, "hour_bin")).pl())
            daily_parts.append(con.execute(binned_sql(params.days, 24.0, "day_index")).pl())
            per_day = con.execute(per_stay_day_sql()).pl()
            gaps = con.execute(intervals_sql()).pl()
            stats_parts.append(
                pl.DataFrame({"itemid": [int(i) for i in ids]}, schema={"itemid": pl.Int64})
                .join(
                    per_day.with_columns(pl.col("itemid").cast(pl.Int64)), on="itemid", how="left"
                )
                .join(gaps.with_columns(pl.col("itemid").cast(pl.Int64)), on="itemid", how="left")
            )
    finally:
        _drop_temp(con)
    empty_binned = pl.DataFrame(
        schema={
            "itemid": pl.Int64,
            "index": pl.Int32,
            N_MEASURED: pl.Int64,
            N_MEASUREMENTS: pl.Int64,
        }
    )
    hourly_measured = (
        pl.concat(hourly_parts, how="vertical_relaxed")
        if hourly_parts
        else empty_binned.rename({"index": "hour_bin"})
    )
    daily_measured = (
        pl.concat(daily_parts, how="vertical_relaxed")
        if daily_parts
        else empty_binned.rename({"index": "day_index"})
    )
    stats = (
        pl.concat(stats_parts, how="vertical_relaxed").sort("itemid")
        if stats_parts
        else pl.DataFrame(schema=stats_schema)
    )
    stats = stats.select(pl.col(name).cast(dtype) for name, dtype in stats_schema.items())
    return Slice(
        step=STEP_HOURLY,
        frames={
            "hourly": _grid(covered, at_risk_h, hourly_measured, "hour_bin", params.hours),
            "daily": _grid(covered, at_risk_d, daily_measured, "day_index", params.days),
            "stats": stats,
        },
        meta={
            "params": params.model_dump(mode="json"),
            "itemids": list(covered),
            "sources": sorted(by_source),
            "n_population": n_population,
            "n_excluded": n_excluded,
        },
    )


def compute_structural(
    con: duckdb.DuckDBPyConnection,
    params: MeasurementParams | None = None,
    *,
    catalogue: ItemCatalogue | None = None,
    present: set[str] | None = None,
) -> Slice:
    """The ``measurement.structural`` computation: the complete itemid xcare unit xera
    grid with the cell's stays, stays measured (any time) and stays measured in the
    first window — raw counts; the flags and the absence split are derived at assembly
    (:func:`flag_cells`, :func:`item_counts`)."""
    import polars as pl

    from mimicwarehouse.units import load_catalogue

    params = params or MeasurementParams()
    cat = catalogue or load_catalogue()
    have = present if present is not None else present_tables(con)
    _require_core(have)
    itemids = params.resolve_itemids(cat)
    by_source = items_by_source(itemids, cat, have)
    covered = tuple(sorted(i for ids in by_source.values() for i in ids))
    schema = _schemas()["cells"]
    try:
        n_population, n_excluded = _create_population(con)
        cells = con.execute(cells_sql()).pl()
        parts: list[Any] = []
        for source, ids in by_source.items():
            con.execute(f"CREATE OR REPLACE TEMP TABLE {_OCC} AS {occasions_sql(source, ids)}")
            parts.append(con.execute(measured_cells_sql(params.first_window_hours)).pl())
    finally:
        _drop_temp(con)
    measured = (
        pl.concat(parts, how="vertical_relaxed")
        if parts
        else pl.DataFrame(
            schema={
                "itemid": pl.Int64,
                "careunit": pl.String,
                "era": pl.String,
                N_MEASURED: pl.Int64,
                N_MEASURED_24H: pl.Int64,
            }
        )
    )
    items = pl.DataFrame({"itemid": [int(i) for i in covered]}, schema={"itemid": pl.Int64})
    grid = (
        items.join(
            cells.select(
                pl.col("careunit").cast(pl.String),
                pl.col("era").cast(pl.String),
                pl.col(N_STAYS).cast(pl.Int64),
            ),
            how="cross",
        )
        .join(
            measured.select(
                pl.col("itemid").cast(pl.Int64),
                pl.col("careunit").cast(pl.String),
                pl.col("era").cast(pl.String),
                pl.col(N_MEASURED).cast(pl.Int64),
                pl.col(N_MEASURED_24H).cast(pl.Int64),
            ),
            on=["itemid", "careunit", "era"],
            how="left",
        )
        .with_columns(pl.col(N_MEASURED).fill_null(0), pl.col(N_MEASURED_24H).fill_null(0))
        .sort(["itemid", "careunit", "era"])
    )
    grid = grid.select(pl.col(name).cast(dtype) for name, dtype in schema.items())
    return Slice(
        step=STEP_STRUCTURAL,
        frames={"cells": grid},
        meta={
            "params": params.model_dump(mode="json"),
            "itemids": list(covered),
            "sources": sorted(by_source),
            "n_population": n_population,
            "n_excluded": n_excluded,
        },
    )


def compute_presence(
    con: duckdb.DuckDBPyConnection,
    params: MeasurementParams | None = None,
    *,
    catalogue: ItemCatalogue | None = None,
    present: set[str] | None = None,
) -> Slice:
    """The ``measurement.presence`` computation: :func:`presence_sql` over the curated
    labs — ``(itemid, arm, n_stays, n_deaths)`` raw; empty when ``labevents`` or
    ``admissions`` is absent."""
    import polars as pl

    from mimicwarehouse.units import load_catalogue

    params = params or MeasurementParams()
    cat = catalogue or load_catalogue()
    have = present if present is not None else present_tables(con)
    _require_core(have)
    itemids = params.resolve_itemids(cat)
    labs = items_by_source(itemids, cat, have).get(PRESENCE_SOURCE, ())
    schema = _schemas()["arms"]
    sql = ""
    if labs and ADMISSIONS_TABLE in have:
        sql = presence_sql(labs, params.first_window_hours)
        arms = con.execute(sql).pl()
        arms = arms.select(pl.col(name).cast(dtype) for name, dtype in schema.items())
    else:
        arms = pl.DataFrame(schema=schema)
    n_outcome = 0
    if arms.height:
        first = int(arms.get_column("itemid")[0])
        n_outcome = int(arms.filter(pl.col("itemid") == first).get_column(N_STAYS).sum())
    return Slice(
        step=STEP_PRESENCE,
        frames={"arms": arms.sort(["itemid", "arm"])},
        meta={
            "params": params.model_dump(mode="json"),
            "itemids": list(labs),
            "sources": [PRESENCE_SOURCE] if labs else [],
            "n_population": n_outcome,
            "sql": sql,
        },
    )


COMPUTE_FUNCTIONS: dict[str, Any] = {
    STEP_HOURLY: compute_hourly,
    STEP_STRUCTURAL: compute_structural,
    STEP_PRESENCE: compute_presence,
}


# ---------------------------------------------------------------------------
# Derivations on raw frames (flags, absence split, rate ratios)
# ---------------------------------------------------------------------------


def flag_cells(cells: Any, params: MeasurementParams) -> Any:
    """The raw cells frame plus ``share`` and ``structural_flag`` (module docstring):
    ``structural`` when no stay of a cell with at least ``min_stays`` stays was ever
    measured, ``sparse`` when the share is below ``sparse_share``, else ``in_use``."""
    import polars as pl

    share = (
        pl.when(pl.col(N_STAYS) > 0)
        .then(pl.col(N_MEASURED) / pl.col(N_STAYS))
        .otherwise(None)
        .alias("share")
    )
    flag = (
        pl.when((pl.col(N_MEASURED) == 0) & (pl.col(N_STAYS) >= params.min_stays))
        .then(pl.lit(FLAG_STRUCTURAL))
        .when(pl.col("share") < params.sparse_share)
        .then(pl.lit(FLAG_SPARSE))
        .otherwise(pl.lit(FLAG_IN_USE))
        .alias("structural_flag")
    )
    return cells.with_columns(share).with_columns(flag)


def item_counts(flagged: Any) -> Any:
    """Per itemid, from the flagged cells: ``n_stays`` (the population), ``n_stays_measured``,
    ``n_stays_measured_first_24h``, ``n_missing_first_24h`` and its split
    ``n_structural`` (stays of structural cells) / ``n_unmeasured``."""
    import polars as pl

    return (
        flagged.group_by("itemid", maintain_order=True)
        .agg(
            pl.col(N_STAYS).sum().alias(N_STAYS),
            pl.col(N_MEASURED).sum().alias(N_MEASURED),
            pl.col(N_MEASURED_24H).sum().alias(N_MEASURED_24H),
            pl.col(N_STAYS)
            .filter(pl.col("structural_flag") == FLAG_STRUCTURAL)
            .sum()
            .alias(N_STRUCTURAL),
        )
        .with_columns((pl.col(N_STAYS) - pl.col(N_MEASURED_24H)).alias(N_MISSING))
        .with_columns((pl.col(N_MISSING) - pl.col(N_STRUCTURAL)).alias(N_UNMEASURED))
        .sort("itemid")
    )


def rate_ratio(
    deaths_exposed: int, n_exposed: int, deaths_reference: int, n_reference: int
) -> tuple[float, float, float] | None:
    """The mortality rate ratio (exposed / reference) with its Wald 95 % CI on the log
    scale — statsmodels ``Table2x2`` (rows = arms, columns = died / survived; a zero cell
    shifts every cell by 0.5, the library default); ``None`` when an arm is empty."""
    if n_exposed <= 0 or n_reference <= 0:
        return None
    import numpy as np
    from statsmodels.stats.contingency_tables import Table2x2

    table = np.array(
        [
            [deaths_exposed, n_exposed - deaths_exposed],
            [deaths_reference, n_reference - deaths_reference],
        ],
        dtype=float,
    )
    t = Table2x2(table)
    rr = float(t.riskratio)
    lo, hi = t.riskratio_confint(alpha=0.05)
    if not all(math.isfinite(v) for v in (rr, lo, hi)):
        return None
    return rr, float(lo), float(hi)


def presence_rows(arms: Any, labels: Mapping[int, str]) -> Any:
    """The presence-outcome frame before suppression: per lab, the binary contrast
    (``measured`` = the two upper count arms summed, ``not_measured`` = the ``0`` arm)
    and the count contrast, with ``mortality_rate`` (``None`` for an empty arm)."""
    import polars as pl

    counts: dict[int, dict[str, tuple[int, int]]] = {}
    for row in arms.to_dicts():
        counts.setdefault(int(row["itemid"]), {})[str(row["arm"])] = (
            int(row[N_STAYS] or 0),
            int(row[N_DEATHS] or 0),
        )
    rows: list[tuple[Any, ...]] = []
    for itemid in sorted(counts):
        by_arm = counts[itemid]
        per: dict[str, tuple[int, int]] = {arm: by_arm.get(arm, (0, 0)) for arm in COUNT_ARMS}
        n_meas = per["1-2"][0] + per[">=3"][0]
        d_meas = per["1-2"][1] + per[">=3"][1]
        binary = {ARM_NOT_MEASURED: per["0"], ARM_MEASURED: (n_meas, d_meas)}
        label = labels.get(itemid)
        for arm in BINARY_ARMS:
            n, d = binary[arm]
            rows.append((itemid, label, CONTRAST_BINARY, arm, n, d, d / n if n else None))
        for arm in COUNT_ARMS:
            n, d = per[arm]
            rows.append((itemid, label, CONTRAST_COUNT, arm, n, d, d / n if n else None))
    return _frame(
        rows,
        {
            "itemid": pl.Int64,
            "label": pl.String,
            "contrast": pl.String,
            "arm": pl.String,
            N_STAYS: pl.Int64,
            N_DEATHS: pl.Int64,
            "mortality_rate": pl.Float64,
        },
    )


# ---------------------------------------------------------------------------
# Assembly: suppression + the six published frames
# ---------------------------------------------------------------------------

#: Column orders of the published tables (the CSV / Parquet shapes); ``<count>_suppressed``
#: markers follow their count column.
_ID_COLUMNS: tuple[str, ...] = ("k", "tier", "build_id", "run_id")


def _marker(column: str) -> str:
    from mimicwarehouse.disclose import MARKER_SUFFIX

    return f"{column}{MARKER_SUFFIX}"


def _with_markers(*columns: str) -> list[str]:
    out: list[str] = []
    for c in columns:
        out.append(c)
        out.append(_marker(c))
    return out


def hourly_columns(index_name: str) -> tuple[str, ...]:
    return (
        "itemid",
        index_name,
        *_with_markers(N_AT_RISK, N_MEASURED, N_MEASUREMENTS),
        *_ID_COLUMNS,
    )


SUMMARY_COLUMNS: tuple[str, ...] = (
    "itemid",
    "label",
    "source",
    *_with_markers(N_STAYS, N_MEASURED, N_MEASURED_24H),
    SHARE_24H,
    *STAT_COLUMNS,
    *_ID_COLUMNS,
)
ABSENCE_COLUMNS: tuple[str, ...] = (
    "itemid",
    "label",
    *_with_markers(N_MISSING, N_STRUCTURAL, N_UNMEASURED),
    *_ID_COLUMNS,
)
STRUCTURAL_COLUMNS: tuple[str, ...] = (
    "itemid",
    "label",
    "first_careunit",
    "era",
    *_with_markers(N_STAYS, N_MEASURED),
    "share",
    "structural_flag",
    *_ID_COLUMNS,
)
PRESENCE_COLUMNS: tuple[str, ...] = (
    "itemid",
    "label",
    "contrast",
    "arm",
    *_with_markers(N_STAYS, N_DEATHS),
    "mortality_rate",
    *RR_COLUMNS,
    "claim_type",
    "caveat",
    *_ID_COLUMNS,
)


def _stamp(frame: Any, ids: Mapping[str, Any]) -> Any:
    import polars as pl

    return frame.with_columns(
        pl.lit(int(ids["k"]), dtype=pl.Int32).alias("k"),
        pl.lit(ids["tier"], dtype=pl.String).alias("tier"),
        pl.lit(ids["build_id"], dtype=pl.String).alias("build_id"),
        pl.lit(ids["run_id"], dtype=pl.String).alias("run_id"),
    )


def _any_hidden(frame: Any, counts: Sequence[str]) -> Any:
    """A boolean expression: the row lost at least one of ``counts`` to suppression."""
    import polars as pl

    return pl.any_horizontal(*(pl.col(_marker(c)) for c in counts))


def suppress_binned(frame: Any, index_name: str, k: int) -> Any:
    """The published shape of an hourly / daily grid: ``disclose.suppress`` over the
    three count columns with the ``(itemid, bin)`` margins (complementary; the nested
    pairs at-risk >= measured and measurements >= measured hide a derivable small
    difference)."""
    from mimicwarehouse.disclose import suppress

    ordered = frame.sort(["itemid", index_name])
    if ordered.is_empty():
        import polars as pl

        return ordered.with_columns(
            *(pl.lit(False).alias(_marker(c)) for c in (N_AT_RISK, N_MEASURED, N_MEASUREMENTS))
        )
    out, _report = suppress(
        ordered,
        k=k,
        count_cols=[N_AT_RISK, N_MEASURED, N_MEASUREMENTS],
        group_cols=["itemid", index_name],
        complementary=True,
    )
    return out


#: The additive identities of the item frame — ``total = sum(parts)`` per row — that the
#: pairwise nested-total rule of ``disclose.suppress`` cannot see (a hidden part would be
#: the published total minus the published other part); :func:`_enforce_identities`.
ITEM_IDENTITIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (N_STAYS, (N_MEASURED_24H, N_MISSING)),
    (N_MISSING, (N_STRUCTURAL, N_UNMEASURED)),
)
ITEM_COUNTS: tuple[str, ...] = (
    N_STAYS,
    N_MEASURED,
    N_MEASURED_24H,
    N_MISSING,
    N_STRUCTURAL,
    N_UNMEASURED,
)


def _enforce_identities(
    row: dict[str, Any], identities: Sequence[tuple[str, Sequence[str]]]
) -> bool:
    """One row of a suppressed frame: for every identity with exactly one hidden term,
    hide the smallest published *part* (a positive one before a zero; never the total
    when a part is hidden — the total is the population, published on every row).
    Returns whether anything changed."""
    changed = False
    for total, parts in identities:
        terms = [total, *parts]
        hidden = [t for t in terms if row.get(_marker(t))]
        if len(hidden) != 1:
            continue
        candidates = [p for p in parts if not row.get(_marker(p)) and row.get(p) is not None]
        if not candidates:
            continue
        pick = min(candidates, key=lambda p: (0 if row[p] > 0 else 1, row[p]))
        row[pick] = None
        row[_marker(pick)] = True
        changed = True
    return changed


def _blank_beside_hidden(frame: Any, counts: Sequence[str], columns: Sequence[str]) -> Any:
    """``columns`` blanked on every row with a hidden count (the rate rule of
    ``disclose.suppress``, re-applied after the post-passes that hide more)."""
    import polars as pl

    lost = _any_hidden(frame, counts)
    return frame.with_columns(
        *(pl.when(lost).then(None).otherwise(pl.col(c)).alias(c) for c in columns)
    )


def suppress_items(item_frame: Any, k: int) -> Any:
    """The item frame (summary + absence counts side by side) suppressed as **one**
    frame: ``disclose.suppress`` over the six count columns (its nested-total rule sees
    the pairs population >= measured >= measured-in-window and missing >= structural /
    unmeasured across the frame), then :data:`ITEM_IDENTITIES` to a fixpoint (each pass
    re-runs the primitive, which honours the markers), the share blanked beside any
    hidden count and the statistics blanked where ``n_stays_measured`` — the count of
    stays they describe — is hidden."""
    import polars as pl

    from mimicwarehouse.disclose import suppress

    counts = list(ITEM_COUNTS)
    ordered = item_frame.sort("itemid")
    if ordered.is_empty():
        return ordered.with_columns(*(pl.lit(False).alias(_marker(c)) for c in counts))
    out = ordered
    for _ in range(8):
        out, _report = suppress(
            out, k=k, count_cols=counts, group_cols=["itemid"], complementary=True
        )
        rows = out.to_dicts()
        changed = False
        for row in rows:
            changed = _enforce_identities(row, ITEM_IDENTITIES) or changed
        if not changed:
            break
        out = pl.DataFrame(rows, schema=out.schema)
    out = _blank_beside_hidden(out, counts, [SHARE_24H])
    hidden_measured = pl.col(_marker(N_MEASURED))
    return out.with_columns(
        *(pl.when(hidden_measured).then(None).otherwise(pl.col(c)).alias(c) for c in STAT_COLUMNS)
    )


def suppress_cells(flagged: Any, k: int) -> Any:
    """The published structural grid: ``disclose.suppress`` over ``n_stays`` /
    ``n_stays_measured`` with the ``(itemid, careunit, era)`` margins; ``share`` is
    blanked by the primitive beside a hidden count and ``structural_flag`` is blanked
    wherever ``n_stays_measured`` is hidden (a flag would bound the hidden count)."""
    import polars as pl

    from mimicwarehouse.disclose import suppress

    # the per-cell first-window count feeds the absence split (raw) and is never published
    ordered = flagged.sort(["itemid", "careunit", "era"]).drop(N_MEASURED_24H, strict=False)
    if ordered.is_empty():
        return ordered.with_columns(
            *(pl.lit(False).alias(_marker(c)) for c in (N_STAYS, N_MEASURED))
        )
    out, _report = suppress(
        ordered,
        k=k,
        count_cols=[N_STAYS, N_MEASURED],
        group_cols=["itemid", "careunit", "era"],
        complementary=True,
    )
    return out.with_columns(
        pl.when(pl.col(_marker(N_MEASURED)))
        .then(None)
        .otherwise(pl.col("structural_flag"))
        .alias("structural_flag")
    )


def _presence_cross_rules(frame: Any) -> tuple[Any, bool]:
    """Hide across the two contrasts what one contrast's suppression implies for the
    other (the binary ``measured`` arm is the sum of ``1-2`` and ``>=3``; ``not_measured``
    and ``0`` are the same stays): returns ``(frame, changed)``."""
    import polars as pl

    rows = frame.to_dicts()
    index: dict[tuple[int, str, str], int] = {
        (int(r["itemid"]), str(r["contrast"]), str(r["arm"])): i for i, r in enumerate(rows)
    }
    changed = False

    def hidden(i: int, c: str) -> bool:
        return bool(rows[i][_marker(c)])

    def hide(i: int, c: str) -> None:
        nonlocal changed
        if not hidden(i, c):
            rows[i][c] = None
            rows[i][_marker(c)] = True
            changed = True

    for itemid in sorted({key[0] for key in index}):
        try:
            meas = index[(itemid, CONTRAST_BINARY, ARM_MEASURED)]
            notm = index[(itemid, CONTRAST_BINARY, ARM_NOT_MEASURED)]
            zero = index[(itemid, CONTRAST_COUNT, "0")]
            low = index[(itemid, CONTRAST_COUNT, "1-2")]
            high = index[(itemid, CONTRAST_COUNT, ">=3")]
        except KeyError:  # pragma: no cover - the frame always carries every arm
            continue
        for c in (N_STAYS, N_DEATHS):
            if hidden(low, c) or hidden(high, c):
                hide(meas, c)
            if hidden(notm, c) or hidden(zero, c):
                hide(notm, c)
                hide(zero, c)
            if hidden(meas, c) and not hidden(low, c) and not hidden(high, c):
                # the sum is hidden but both parts are published: hide the smaller part
                lo_v, hi_v = rows[low][c], rows[high][c]
                pick = (
                    low
                    if (lo_v if lo_v is not None else 0) <= (hi_v if hi_v is not None else 0)
                    else high
                )
                hide(pick, c)
    if not changed:
        return frame, False
    return pl.DataFrame(rows, schema=frame.schema), True


def suppress_presence(frame: Any, k: int) -> Any:
    """The published presence-outcome frame: ``disclose.suppress`` over ``n_stays`` /
    ``n_deaths`` with the ``(itemid, contrast, arm)`` margins, the cross-contrast rule
    (:func:`_presence_cross_rules`) to a fixpoint, ``mortality_rate`` blanked by the
    primitive beside a hidden count, and the rate ratio + CI computed from the
    **released** counts of each ``(itemid, contrast)`` group — blanked whenever any arm
    of the group lost a cell."""
    import polars as pl

    from mimicwarehouse.disclose import suppress

    counts = [N_STAYS, N_DEATHS]
    ordered = frame.sort(["itemid", "contrast", "arm"])
    if ordered.is_empty():
        return ordered.with_columns(
            *(pl.lit(False).alias(_marker(c)) for c in counts),
            *(pl.lit(None, dtype=pl.Float64).alias(c) for c in RR_COLUMNS),
        )
    out = ordered
    for _ in range(8):
        out, _report = suppress(
            out,
            k=k,
            count_cols=counts,
            group_cols=["itemid", "contrast", "arm"],
            complementary=True,
        )
        out, changed = _presence_cross_rules(out)
        if not changed:
            break
    out = _blank_beside_hidden(out, counts, ["mortality_rate"])
    # rate ratios from the released counts, per (itemid, contrast) group
    rows = out.to_dicts()
    groups: dict[tuple[int, str], list[int]] = {}
    for i, r in enumerate(rows):
        groups.setdefault((int(r["itemid"]), str(r["contrast"])), []).append(i)
    rr_values: list[tuple[float | None, float | None, float | None]] = [
        (None, None, None) for _ in rows
    ]
    for (_itemid, contrast), members in groups.items():
        damaged = any(rows[i][_marker(c)] for i in members for c in counts)
        if damaged:
            continue
        reference = next((i for i in members if rows[i]["arm"] == REFERENCE_ARMS[contrast]), None)
        if reference is None:
            continue
        ref_n, ref_d = rows[reference][N_STAYS], rows[reference][N_DEATHS]
        for i in members:
            if i == reference:
                continue
            n, d = rows[i][N_STAYS], rows[i][N_DEATHS]
            if None in (n, d, ref_n, ref_d):
                continue
            estimate = rate_ratio(int(d), int(n), int(ref_d), int(ref_n))
            if estimate is not None:
                rr_values[i] = estimate
    return out.with_columns(
        pl.Series("rate_ratio", [v[0] for v in rr_values], dtype=pl.Float64),
        pl.Series("rr_ci_low", [v[1] for v in rr_values], dtype=pl.Float64),
        pl.Series("rr_ci_high", [v[2] for v in rr_values], dtype=pl.Float64),
        pl.lit(CLAIM_TYPE_SHORT, dtype=pl.String).alias("claim_type"),
        pl.lit(CAVEAT, dtype=pl.String).alias("caveat"),
    )


def _labels(catalogue: ItemCatalogue) -> tuple[dict[int, str], dict[int, str]]:
    labels = {s.itemid: s.label[:VALUE_MAX_CHARS] for s in catalogue.items}
    sources = {s.itemid: s.source for s in catalogue.items}
    return labels, sources


def assemble(
    slices: Mapping[str, Slice],
    *,
    k: int,
    tier: str,
    build_id: str,
    run_id: str | None,
    catalogue: ItemCatalogue | None = None,
) -> dict[str, Any]:
    """The six published frames from the three slices (module docstring): the grids,
    the item frame split into summary + absence, the flagged cells, the presence arms —
    every count k-suppressed through ``disclose.suppress``, every row stamped with
    ``k`` / ``tier`` / ``build_id`` / ``run_id``."""
    import polars as pl

    from mimicwarehouse.units import load_catalogue

    cat = catalogue or load_catalogue()
    labels, sources = _labels(cat)
    hourly_slice = slices[STEP_HOURLY]
    structural_slice = slices[STEP_STRUCTURAL]
    presence_slice = slices[STEP_PRESENCE]
    params = structural_slice.params
    ids = {"k": k, "tier": tier, "build_id": build_id, "run_id": run_id}

    hourly = _stamp(suppress_binned(hourly_slice.frames["hourly"], "hour_bin", k), ids)
    daily = _stamp(suppress_binned(hourly_slice.frames["daily"], "day_index", k), ids)

    flagged = flag_cells(structural_slice.frames["cells"], params)
    per_item = item_counts(flagged)
    label_frame = pl.DataFrame(
        {
            "itemid": [int(i) for i in sorted(labels)],
            "label": [labels[i] for i in sorted(labels)],
            "source": [sources[i] for i in sorted(labels)],
        },
        schema={"itemid": pl.Int64, "label": pl.String, "source": pl.String},
    )
    stats = hourly_slice.frames["stats"]
    item_frame = (
        per_item.join(label_frame, on="itemid", how="left")
        .join(stats.with_columns(pl.col("itemid").cast(pl.Int64)), on="itemid", how="left")
        .with_columns(
            pl.when(pl.col(N_STAYS) > 0)
            .then(pl.col(N_MEASURED_24H) / pl.col(N_STAYS))
            .otherwise(None)
            .alias(SHARE_24H)
        )
    )
    items = _stamp(suppress_items(item_frame, k), ids)
    summary = items.select(*SUMMARY_COLUMNS)
    absence = items.select(*ABSENCE_COLUMNS)

    cells = _stamp(suppress_cells(flagged, k), ids).join(
        label_frame.drop("source"), on="itemid", how="left"
    )
    structural = cells.rename({"careunit": "first_careunit"}).select(*STRUCTURAL_COLUMNS)

    presence_raw = presence_rows(presence_slice.frames["arms"], labels)
    presence = _stamp(suppress_presence(presence_raw, k), ids).select(*PRESENCE_COLUMNS)

    return {
        HOURLY_TABLE: hourly.select(*hourly_columns("hour_bin")),
        DAILY_TABLE: daily.select(*hourly_columns("day_index")),
        SUMMARY_TABLE: summary,
        STRUCTURAL_TABLE: structural,
        ABSENCE_TABLE: absence,
        PRESENCE_TABLE: presence,
    }


# ---------------------------------------------------------------------------
# The DAG step handlers
# ---------------------------------------------------------------------------


def _core_snapshot_id(ctx: StepContext) -> str:
    from mimicwarehouse.dag.snapshot import layer_snapshot

    key = "measurement.core_snapshot_id"
    if key not in ctx.state:
        ctx.state[key] = layer_snapshot(ctx.lake_root, "core", ctx.tier, settings=ctx.settings)
    return str(ctx.state[key])


def _prepare(step: Step, ctx: StepContext) -> tuple[MeasurementParams, ItemCatalogue, set[str]]:
    """Params, catalogue and the present tables, with the core + event views created on
    the build connection (uncached, ``docs/gotchas.md`` §1)."""
    from mimicwarehouse.concepts.runner import ensure_source_views
    from mimicwarehouse.units import load_catalogue

    params = params_of(step)
    cat = load_catalogue()
    ensure_source_views(ctx)
    ensure_table_views(
        ctx,
        [
            STAYS_TABLE,
            PATIENTS_TABLE,
            ADMISSIONS_TABLE,
            *(t for t, _c, _k in SOURCE_EVENTS.values()),
        ],
    )
    have = present_tables(ctx.con)
    _require_core(have)
    return params, cat, have


def _run_compute(step: Step, ctx: StepContext, compute: Any) -> StepOutcome:
    from mimicwarehouse.dag.runner import StepOutcome
    from mimicwarehouse.loader.manifest import utc_now_iso
    from mimicwarehouse.run import ResourceLog

    params, cat, have = _prepare(step, ctx)
    snapshot_id = _core_snapshot_id(ctx)

    def work() -> Slice:
        return compute(ctx.con, params, catalogue=cat, present=have)

    s, usage = ResourceLog.measure(work, data_root=ctx.settings.data_root)
    s.meta.update(
        {
            "snapshot_id": snapshot_id,
            "build_id": ctx.build_id,
            "built_at": utc_now_iso(),
            "wall_s": usage.wall_s,
            "peak_rss_mb": usage.peak_rss_mb,
            "disk_delta_mb": usage.disk_delta_mb,
        }
    )
    nbytes = write_slice(ctx.con, ctx.lake_root, ctx.tier, s)
    rows = sum(int(f.height) for f in s.frames.values())
    _LOG.info(
        "%s (%s): %d itemid(s) over %s, %d frame row(s), population %s, wall=%.1fs",
        step.name,
        ctx.tier,
        len(s.meta.get("itemids") or []),
        ", ".join(s.meta.get("sources") or []) or "no source table",
        rows,
        f"{int(s.meta.get('n_population') or 0):,}",
        usage.wall_s,
    )
    return StepOutcome(rows=rows, bytes_out=nbytes, files=len(s.frames) + 1)


def run_hourly(step: Step, ctx: StepContext) -> StepOutcome:
    """The ``measurement.hourly`` handler (module docstring)."""
    return _run_compute(step, ctx, compute_hourly)


def run_structural(step: Step, ctx: StepContext) -> StepOutcome:
    """The ``measurement.structural`` handler (module docstring)."""
    return _run_compute(step, ctx, compute_structural)


def run_presence(step: Step, ctx: StepContext) -> StepOutcome:
    """The ``measurement.presence`` handler (module docstring)."""
    return _run_compute(step, ctx, compute_presence)


def read_slices(
    lake_root: Path | str, tier: str, snapshot_id: str | None = None
) -> dict[str, Slice]:
    """The three compute slices of ``tier``; a missing one, one computed over another
    core snapshot (stale) or slices with differing params refuse with the remedy."""
    slices = {name: read_slice(lake_root, tier, name) for name in COMPUTE_STEPS}
    remedy = f"rerun `mwh build --tier {tier} --tag {DAG_TAG}`"
    if snapshot_id is not None:
        stale = [
            s.step
            for s in slices.values()
            if s.meta.get("snapshot_id") and s.meta["snapshot_id"] != snapshot_id
        ]
        if stale:
            raise QcError(
                f"stale measurement slice(s) {', '.join(stale)} (core snapshot changed) — {remedy}"
            )
    hashes = {s.params.sha256() for s in slices.values()}
    if len(hashes) > 1:
        raise QcError(f"the measurement slices of tier {tier} carry differing params — {remedy}")
    return slices


def run_report(step: Step, ctx: StepContext) -> StepOutcome:
    """The ``measurement.report`` handler (module docstring): the three slices -> the
    six ``meta.mp_*`` tables inside a ``kind: qc`` run (bench lines per compute step,
    the SQL recorded) -> ``runs/<run_id>/measurement_process.md`` + CSVs once the run is
    closed."""
    from mimicwarehouse import run as run_mod
    from mimicwarehouse.dag.runner import StepOutcome
    from mimicwarehouse.units import CATALOGUE_FILENAME, load_catalogue

    params = params_of(step)
    cat = load_catalogue()
    snapshot_id = _core_snapshot_id(ctx)
    slices = read_slices(ctx.lake_root, ctx.tier, snapshot_id)
    slice_params = slices[STEP_HOURLY].params
    if slice_params.sha256() != params.sha256():
        _LOG.warning(
            "%s: the report step's params differ from the slices' — the slices' params apply",
            step.name,
        )
    k = ctx.settings.k_suppression
    hourly_meta = slices[STEP_HOURLY].meta
    presence_meta = slices[STEP_PRESENCE].meta
    run_params: dict[str, Any] = {
        "build_id": ctx.build_id,
        "k": k,
        "params": slice_params.model_dump(mode="json"),
        "itemids": len(hourly_meta.get("itemids") or []),
        "sources": hourly_meta.get("sources") or [],
        "n_population": hourly_meta.get("n_population"),
        "n_excluded": hourly_meta.get("n_excluded"),
        "n_population_outcome": presence_meta.get("n_population"),
    }
    with run_mod.start(
        RUN_NAME,
        tier=ctx.tier,
        kind=RUN_KIND,
        params=run_params,
        settings=ctx.settings,
        claim_type=CLAIM_TYPE,
        doctor=ctx.run is not None,
    ) as r:
        r.record_snapshot("core", snapshot_id)
        r.record_ref("item_catalogue", CATALOGUE_FILENAME, version=str(cat.version))
        r.record_ref("measurement_params", "params", hash=slice_params.sha256())
        itemids = [int(i) for i in hourly_meta.get("itemids") or []]
        labs = [int(i) for i in presence_meta.get("itemids") or []]
        if itemids:
            r.record_sql("population", population_sql())
            for source in hourly_meta.get("sources") or []:
                ids = [i for i in itemids if cat.spec(i).source == source]
                if ids:
                    r.record_sql(f"occasions_{source}", occasions_sql(source, ids))
            r.record_sql("hourly", binned_sql(slice_params.hours, 1.0, "hour_bin"))
            r.record_sql("daily", binned_sql(slice_params.days, 24.0, "day_index"))
            r.record_sql("at_risk", at_risk_sql(slice_params.hours, 1.0, "hour_bin"))
            r.record_sql("intervals", intervals_sql())
            r.record_sql("cells", measured_cells_sql(slice_params.first_window_hours))
        if labs:
            r.record_sql("presence", presence_sql(labs, slice_params.first_window_hours))
        for name in COMPUTE_STEPS:
            meta = slices[name].meta
            r.bench(
                BENCH_KIND,
                name,
                wall_s=float(meta.get("wall_s") or 0.0),
                build_id=ctx.build_id,
                rows=sum(int(f.height) for f in slices[name].frames.values()),
                peak_rss_mb=meta.get("peak_rss_mb"),
                disk_delta_mb=meta.get("disk_delta_mb"),
                ok=True,
            )
        frames = assemble(
            slices, k=k, tier=ctx.tier, build_id=ctx.build_id, run_id=r.run_id, catalogue=cat
        )
        nbytes = 0
        for table in META_TABLES:
            nbytes += write_frame_parquet(
                ctx.con, meta_table_path(ctx.lake_root, ctx.tier, table), frames[table]
            )
        r.manifest.params = {
            **r.manifest.params,
            "tables": {table: int(frames[table].height) for table in META_TABLES},
            "structural_cells": int(
                (frames[STRUCTURAL_TABLE].get_column("structural_flag") == FLAG_STRUCTURAL).sum()
            ),
        }
        run_id = r.run_id
    out_dir = run_mod.run_dir(run_id, ctx.settings)
    inputs = ReportInputs(
        tier=ctx.tier,
        k=k,
        run_id=run_id,
        build_id=ctx.build_id,
        snapshot_id=snapshot_id,
        params=slice_params,
        n_population=int(hourly_meta.get("n_population") or 0),
        n_excluded=int(hourly_meta.get("n_excluded") or 0),
        n_population_outcome=int(presence_meta.get("n_population") or 0),
        frames=frames,
        generated=datetime.now(UTC).date().isoformat(),
    )
    paths = write_report(inputs, out_dir, settings=ctx.settings)
    _LOG.info(
        "meta.mp_* (%s): %s — %d structural cell(s); report %s; run %s",
        ctx.tier,
        ", ".join(f"{t} {frames[t].height}" for t in META_TABLES),
        int((frames[STRUCTURAL_TABLE].get_column("structural_flag") == FLAG_STRUCTURAL).sum()),
        paths[0],
        run_id,
    )
    return StepOutcome(
        rows=sum(int(frames[t].height) for t in META_TABLES),
        bytes_out=nbytes + sum(p.stat().st_size for p in paths),
        files=len(META_TABLES) + len(paths),
    )


# ---------------------------------------------------------------------------
# Reading the published tables (the report inputs; `mwh qc measurement` reads the catalog)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ReportInputs:
    """What the report renders: the six published frames plus the run identity."""

    tier: str
    k: int
    run_id: str | None
    build_id: str | None
    snapshot_id: str | None
    params: MeasurementParams
    n_population: int
    n_excluded: int
    n_population_outcome: int
    frames: Mapping[str, Any]
    generated: str


def read_meta_frame(lake_root: Path | str, tier: str, table: str) -> Any:
    """One published ``meta.mp_*`` table of a tier as a Polars frame."""
    import polars as pl

    path = meta_table_path(lake_root, tier, table)
    if not path.is_file():
        raise QcError(
            f"no meta.{table} for tier {tier} ({path}) — run `mwh build --tier {tier} "
            f"--tag {DAG_TAG}` first"
        )
    return pl.read_parquet(path)


# ---------------------------------------------------------------------------
# The Markdown report
# ---------------------------------------------------------------------------

RETROSPECTIVE_SENTENCE = "MIMIC-IV analyses are retrospective."
DISCLOSURE_LINE = (
    "Disclosure: every count below passed `disclose.suppress` (k-suppressed at build time, "
    "complementary; a suppressed count renders as `<k`, a statistic or rate beside it is "
    "blank) before rendering; run `mwh disclose check --write-sidecar` before promoting "
    "this file (EP-43)."
)
NON_CLAIMS: tuple[str, ...] = (
    "It is descriptive: a measurement is charted or it is not; nothing here says why, "
    "and the presence-outcome contrasts are unadjusted associations, not effects "
    "(informative-presence models and MNAR sensitivity are parked, final-roadmap.md "
    "MISS-2).",
    "It does not judge charting quality: a structural cell means the item is not charted "
    "in that care unit and era at all (a documentation-system fact - the ICU data are "
    "MetaVision-era), not that care was absent.",
    "It is not a calendar-time analysis: the only cross-patient temporal axis is "
    "anchor_year_group; timestamps are per-patient shifted, and eras mix charting "
    "practice with case mix.",
    "The unit of analysis is the ICU stay, not the admission: repeated stays of one "
    "admission share the in-hospital outcome; labs attach to the stay whose window "
    "contains the draw, so pre-ICU labs are not measurements of the stay.",
    "Medians, quantiles and intervals describe measurement occasions (distinct "
    "charttimes per stay and item); they are not clinical reference intervals.",
)


def _fmt_int(value: Any) -> str:
    from mimicwarehouse.inventory import fmt_int

    return "-" if value is None else fmt_int(int(value))


def _count_cell(value: Any, suppressed: Any, k: int) -> str:
    from mimicwarehouse.disclose import render_cell

    if suppressed:
        return render_cell(None, k)
    return _fmt_int(value)


def _pct(value: Any) -> str:
    return "-" if value is None else f"{float(value) * 100:.1f} %"


def _num(value: Any, digits: int = 2) -> str:
    return "-" if value is None else f"{float(value):.{digits}f}"


def _text(value: Any) -> str:
    if value is None:
        return "-"
    return str(value).replace("|", "/").replace("\n", " ")


def _md_table(header: list[str], rows: list[list[str]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return lines


def _rr_cell(row: Mapping[str, Any]) -> str:
    rr = row.get("rate_ratio")
    if rr is None:
        return "-"
    return f"{float(rr):.2f} ({_num(row.get('rr_ci_low'))}-{_num(row.get('rr_ci_high'))})"


def _at_risk_profile(frame: Any, index_name: str, picks: Sequence[int]) -> list[tuple[int, str]]:
    """``(bin, rendered at-risk count)`` at the picked bins — the at-risk count is the
    same for every itemid, so the first published value per bin is taken."""
    import polars as pl

    k_value = int(frame.get_column("k")[0]) if frame.height else 11
    out: list[tuple[int, str]] = []
    for b in picks:
        sub = frame.filter(pl.col(index_name) == b)
        if sub.height == 0:
            continue
        shown = sub.filter(~pl.col(_marker(N_AT_RISK)))
        if shown.height:
            out.append((b, _fmt_int(shown.get_column(N_AT_RISK)[0])))
        else:
            out.append((b, _count_cell(None, True, k_value)))
    return out


def render_report(
    inputs: ReportInputs,
    *,
    settings: Settings | None = None,
    reproduction: str | None = None,
) -> str:
    """The ``measurement_process.md`` text (module docstring). ``reproduction`` overrides
    the EP-35 block (tests); by default it is rendered from the run id's manifest."""
    from mimicwarehouse.inventory import fmt_int

    k = inputs.k
    p = inputs.params
    frames = inputs.frames
    summary = frames[SUMMARY_TABLE].to_dicts()
    structural = frames[STRUCTURAL_TABLE]
    absence = frames[ABSENCE_TABLE].to_dicts()
    presence = frames[PRESENCE_TABLE].to_dicts()
    hourly = frames[HOURLY_TABLE]
    daily = frames[DAILY_TABLE]
    n_items = len(summary)
    n_labs = len({int(r["itemid"]) for r in presence})
    lines = [
        "# Measurement-process summaries (EP-45)",
        "",
        f"**Claim type: {CLAIM_TYPE}.** {RETROSPECTIVE_SENTENCE}",
        "",
        DISCLOSURE_LINE,
        "",
        f"Run `{inputs.run_id or '-'}` - tier `{inputs.tier}` - build "
        f"`{inputs.build_id or '-'}` - core snapshot `{inputs.snapshot_id or '-'}` - "
        f"generated {inputs.generated} - k = {fmt_int(k)}.",
        "",
        f"Population: {fmt_int(inputs.n_population)} ICU stays with a valid "
        f"[intime, outtime) and a patients row ({fmt_int(inputs.n_excluded)} icustays rows "
        f"excluded); {fmt_int(inputs.n_population_outcome)} of them carry an in-hospital "
        f"outcome. {fmt_int(n_items)} curated itemids (EP-39 catalogue), {fmt_int(n_labs)} "
        f"of them labs. Bins: {fmt_int(p.hours)} ICU hours ({HOUR_BIN.name} grain), "
        f"{fmt_int(p.days)} ICU days ({ICU_DAY.name} grain); first window "
        f"{fmt_int(p.first_window_hours)} h; structural = no measurement in a unit x era cell "
        f"of at least {fmt_int(p.min_stays)} stays; sparse below {p.sparse_share * 100:g} %.",
        "",
        "## Measurement frequency",
        "",
        "One row per curated itemid; the interval columns are minutes between consecutive "
        "measurement occasions of one stay (p10 / median / p90).",
        "",
    ]
    lines += _md_table(
        [
            "itemid",
            "label",
            "source",
            "stays measured",
            "measured in first 24 h",
            "share first 24 h",
            "median per stay-day",
            "p10 interval (min)",
            "median interval (min)",
            "p90 interval (min)",
        ],
        [
            [
                _fmt_int(r["itemid"]),
                _text(r.get("label")),
                _text(r.get("source")),
                _count_cell(r.get(N_MEASURED), r.get(_marker(N_MEASURED)), k),
                _count_cell(r.get(N_MEASURED_24H), r.get(_marker(N_MEASURED_24H)), k),
                _pct(r.get(SHARE_24H)),
                _num(r.get("median_per_stay_day")),
                _num(r.get("p10_interval_min"), 0),
                _num(r.get("median_interval_min"), 0),
                _num(r.get("p90_interval_min"), 0),
            ]
            for r in summary
        ],
    )
    picks_h = [b for b in (0, 6, 12, 24, 48, 72, 96, 120, 144, p.hours - 1) if 0 <= b < p.hours]
    picks_d = list(range(p.days))
    profile_h = _at_risk_profile(hourly, "hour_bin", picks_h)
    profile_d = _at_risk_profile(daily, "day_index", picks_d)
    lines += [
        "",
        "Stays at risk (still in the ICU when the bin starts) by ICU hour and by ICU day - "
        "the denominators of `meta.mp_item_hourly` / `meta.mp_item_daily`:",
        "",
    ]
    lines += _md_table(
        ["ICU hour", "stays at risk"], [[_fmt_int(b), cell] for b, cell in profile_h]
    )
    lines.append("")
    lines += _md_table(["ICU day", "stays at risk"], [[_fmt_int(b), cell] for b, cell in profile_d])
    lines += ["", "## Structural absence (care unit x era)", ""]
    per_item_flags: dict[int, dict[str, int]] = {}
    for r in structural.to_dicts():
        entry = per_item_flags.setdefault(int(r["itemid"]), dict.fromkeys(FLAGS, 0))
        flag = r.get("structural_flag")
        if flag in entry:
            entry[str(flag)] += 1
    labels = {int(r["itemid"]): r.get("label") for r in summary}
    lines += _md_table(
        ["itemid", "label", "# cells in use", "# cells sparse", "# cells structural"],
        [
            [
                _fmt_int(itemid),
                _text(labels.get(itemid)),
                _fmt_int(flags[FLAG_IN_USE]),
                _fmt_int(flags[FLAG_SPARSE]),
                _fmt_int(flags[FLAG_STRUCTURAL]),
            ]
            for itemid, flags in sorted(per_item_flags.items())
        ],
    )
    lines += [
        "",
        "Cell counts are numbers of unit x era cells (not stays); a cell whose measured "
        "count is suppressed carries no flag and is counted in none of the columns.",
        "",
        "### Structural cells",
        "",
    ]
    import polars as pl

    structural_rows = (
        structural.filter(pl.col("structural_flag") == FLAG_STRUCTURAL)
        .sort(
            [N_STAYS, "itemid", "first_careunit", "era"],
            descending=[True, False, False, False],
            nulls_last=True,
        )
        .to_dicts()
    )
    if structural_rows:
        lines += _md_table(
            ["itemid", "label", "first_careunit", "era", "stays in cell"],
            [
                [
                    _fmt_int(r["itemid"]),
                    _text(r.get("label")),
                    _text(r.get("first_careunit")),
                    _text(r.get("era")),
                    _count_cell(r.get(N_STAYS), r.get(_marker(N_STAYS)), k),
                ]
                for r in structural_rows
            ],
        )
    else:
        lines.append(
            f"No unit x era cell of at least {fmt_int(p.min_stays)} stays is without a "
            "measurement of a curated item on this tier."
        )
    lines += ["", "### Absence in the first 24 h", ""]
    lines += _md_table(
        ["itemid", "label", "stays missing", "structural", "unmeasured"],
        [
            [
                _fmt_int(r["itemid"]),
                _text(r.get("label")),
                _count_cell(r.get(N_MISSING), r.get(_marker(N_MISSING)), k),
                _count_cell(r.get(N_STRUCTURAL), r.get(_marker(N_STRUCTURAL)), k),
                _count_cell(r.get(N_UNMEASURED), r.get(_marker(N_UNMEASURED)), k),
            ]
            for r in absence
        ],
    )
    lines += [
        "",
        "A stay without a measurement of the item in its first 24 h is *structural* when its "
        "care unit x era cell is structural (the item is never charted there) and "
        "*unmeasured* otherwise.",
        "",
        "## Informative presence (exploratory)",
        "",
        f"In-hospital mortality among stays with vs without a measurement of each curated lab "
        f"in the first {fmt_int(p.first_window_hours)} h; rate ratio (measured / not measured) "
        "with a Wald 95 % CI (statsmodels Table2x2, computed from the released counts). "
        f"{CAVEAT}.",
        "",
    ]
    binary: dict[int, dict[str, dict[str, Any]]] = {}
    count_rows: dict[int, dict[str, dict[str, Any]]] = {}
    for r in presence:
        target = binary if r["contrast"] == CONTRAST_BINARY else count_rows
        target.setdefault(int(r["itemid"]), {})[str(r["arm"])] = r
    if binary:
        lines += _md_table(
            [
                "itemid",
                "label",
                "measured: stays",
                "measured: deaths",
                "measured: mortality rate",
                "not measured: stays",
                "not measured: deaths",
                "not measured: mortality rate",
                "rate ratio (95 % CI)",
            ],
            [
                [
                    _fmt_int(itemid),
                    _text(arms[ARM_MEASURED].get("label")),
                    _count_cell(
                        arms[ARM_MEASURED].get(N_STAYS), arms[ARM_MEASURED].get(_marker(N_STAYS)), k
                    ),
                    _count_cell(
                        arms[ARM_MEASURED].get(N_DEATHS),
                        arms[ARM_MEASURED].get(_marker(N_DEATHS)),
                        k,
                    ),
                    _pct(arms[ARM_MEASURED].get("mortality_rate")),
                    _count_cell(
                        arms[ARM_NOT_MEASURED].get(N_STAYS),
                        arms[ARM_NOT_MEASURED].get(_marker(N_STAYS)),
                        k,
                    ),
                    _count_cell(
                        arms[ARM_NOT_MEASURED].get(N_DEATHS),
                        arms[ARM_NOT_MEASURED].get(_marker(N_DEATHS)),
                        k,
                    ),
                    _pct(arms[ARM_NOT_MEASURED].get("mortality_rate")),
                    _rr_cell(arms[ARM_MEASURED]),
                ]
                for itemid, arms in sorted(binary.items())
                if ARM_MEASURED in arms and ARM_NOT_MEASURED in arms
            ],
        )
        lines += ["", "### By number of measurements in the first 24 h", ""]
        lines += _md_table(
            [
                "itemid",
                "label",
                "arm",
                "stays",
                "deaths",
                "mortality rate",
                "rate ratio vs 0 (95 % CI)",
            ],
            [
                [
                    _fmt_int(itemid),
                    _text(arms[arm].get("label")),
                    _text(arm),
                    _count_cell(arms[arm].get(N_STAYS), arms[arm].get(_marker(N_STAYS)), k),
                    _count_cell(arms[arm].get(N_DEATHS), arms[arm].get(_marker(N_DEATHS)), k),
                    _pct(arms[arm].get("mortality_rate")),
                    "-" if arm == "0" else _rr_cell(arms[arm]),
                ]
                for itemid, arms in sorted(count_rows.items())
                for arm in COUNT_ARMS
                if arm in arms
            ],
        )
    else:
        lines.append("No curated lab was found on this tier (labevents or admissions absent).")
    lines += ["", "## What it deliberately does not claim", ""]
    lines += [f"- {item}" for item in NON_CLAIMS]
    lines.append("")
    if reproduction is None:
        reproduction = _reproduction(inputs.run_id, settings)
    lines.append(reproduction.rstrip("\n"))
    lines.append("")
    return "\n".join(lines)


def _reproduction(run_id: str | None, settings: Settings | None) -> str:
    from mimicwarehouse import run as run_mod

    if run_id is None:
        return "## Reproduction\n\nNo run id recorded (the tables predate the measurement run).\n"
    try:
        return run_mod.reproduction_block(run_id, settings)
    except run_mod.RunLedgerError as exc:
        return f"## Reproduction\n\nRun `{run_id}`: manifest unavailable ({exc}).\n"


def write_report(
    inputs: ReportInputs, out_dir: Path, *, settings: Settings | None = None
) -> list[Path]:
    """Write ``measurement_process.md`` + one CSV per published table into ``out_dir``;
    returns the paths (the Markdown first)."""
    from mimicwarehouse.fsio import atomic_write_text

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    target = out_dir / REPORT_FILENAME
    atomic_write_text(target, render_report(inputs, settings=settings))
    paths.append(target)
    for table in META_TABLES:
        csv_path = out_dir / f"{table}.csv"
        inputs.frames[table].write_csv(csv_path)
        paths.append(csv_path)
    return paths


# ---------------------------------------------------------------------------
# Catalog extension: comments on the meta.mp_* tables
# ---------------------------------------------------------------------------

_META_COMMENTS: dict[str, str] = {
    HOURLY_TABLE: (
        "Measurement frequency per curated itemid x ICU hour bin since intime (EP-45; "
        "timesem hour_bin grain, [0, 1) ... bins): n_stays_at_risk (still in the ICU when the "
        "bin starts), n_stays_measured, n_measurements (distinct charttimes per stay); every "
        "count k-suppressed at build time with disclose.suppress (NULL with "
        "<count>_suppressed = true). Raw slices stay in the data root."
    ),
    DAILY_TABLE: (
        "Measurement frequency per curated itemid x ICU day since intime (EP-45; timesem "
        "icu_day grain): the same columns and suppression as meta.mp_item_hourly."
    ),
    SUMMARY_TABLE: (
        "Per curated itemid (EP-45): n_stays (the population), n_stays_measured (any time), "
        "n_stays_measured_first_24h + measured_first_24h_share, median measurement occasions "
        "per stay-day, inter-measurement interval quantiles (minutes); k-suppressed jointly "
        "with meta.mp_absence_summary (statistics blanked beside a hidden count)."
    ),
    STRUCTURAL_TABLE: (
        "Structural absence per curated itemid x first_careunit x anchor_year_group era "
        "(EP-45): n_stays, n_stays_measured (any time in the stay), share, structural_flag "
        "in_use | sparse | structural (share = 0 in a cell of >= min_stays stays); flag NULL "
        "where the measured count is suppressed."
    ),
    ABSENCE_TABLE: (
        "Per curated itemid (EP-45): stays without a measurement in the first 24 h "
        "(n_missing_first_24h) split into n_structural (their unit x era cell is structural) "
        "and n_unmeasured; k-suppressed jointly with meta.mp_item_summary."
    ),
    PRESENCE_TABLE: (
        "Informative presence, exploratory (EP-45): per curated lab, in-hospital mortality "
        "(admissions.hospital_expire_flag) by arm - contrast binary (measured / not_measured "
        "in the first 24 h) and count (0 / 1-2 / >=3 measurements): n_stays, n_deaths, "
        "mortality_rate, rate_ratio vs the reference arm with a Wald 95 % CI (rr_ci_low / "
        "rr_ci_high) from the released counts; claim_type = exploratory - descriptive "
        "association only."
    ),
}


def register_measurement(con: duckdb.DuckDBPyConnection, tier: str) -> None:
    """The catalog extension (:data:`mimicwarehouse.catalog.build.CATALOG_EXTENSIONS`
    entry, after EP-37's discovery walker): comment the ``meta.mp_*`` tables the walker
    registered from ``lake/meta/<tier>/``. DDL only; never opens a connection."""
    present = {
        str(r[0])
        for r in con.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'meta'"
        ).fetchall()
    }
    commented = 0
    for table, comment in _META_COMMENTS.items():
        if table in present:
            con.execute(f'COMMENT ON TABLE meta."{table}" IS {_sql_str(comment)}')
            commented += 1
    _LOG.info("catalog extension measurement: %d meta.mp_* table(s) on tier %s", commented, tier)


# ---------------------------------------------------------------------------
# Spec (hand-written dag/specs/measurement.yaml; the path for tests)
# ---------------------------------------------------------------------------

SPEC_NAME = "measurement"


def spec_path() -> Path:
    """``src/mimicwarehouse/dag/specs/measurement.yaml`` inside the installed package."""
    from mimicwarehouse.dag.spec import specs_root

    return specs_root() / f"{SPEC_NAME}.yaml"


__all__ = [
    "ABSENCE_COLUMNS",
    "ABSENCE_TABLE",
    "ARM_MEASURED",
    "ARM_NOT_MEASURED",
    "BENCH_KIND",
    "BINARY_ARMS",
    "CAVEAT",
    "CLAIM_TYPE",
    "CLAIM_TYPE_SHORT",
    "COMPUTE_FUNCTIONS",
    "COMPUTE_STEPS",
    "CONTRASTS",
    "CONTRAST_BINARY",
    "CONTRAST_COUNT",
    "COUNT_ARMS",
    "DAG_TAG",
    "DAILY_TABLE",
    "DISCLOSURE_LINE",
    "ERAS",
    "FLAGS",
    "FLAG_IN_USE",
    "FLAG_SPARSE",
    "FLAG_STRUCTURAL",
    "HOURLY_TABLE",
    "META_TABLES",
    "NON_CLAIMS",
    "PRESENCE_COLUMNS",
    "PRESENCE_SOURCE",
    "PRESENCE_TABLE",
    "REFERENCE_ARMS",
    "REPORT_FILENAME",
    "REPORT_FILES",
    "RETROSPECTIVE_SENTENCE",
    "RR_COLUMNS",
    "RUN_KIND",
    "RUN_NAME",
    "SLICES_DIRNAME",
    "SOURCE_EVENTS",
    "SPEC_NAME",
    "STAT_COLUMNS",
    "STEP_HOURLY",
    "STEP_PRESENCE",
    "STEP_REPORT",
    "STEP_STRUCTURAL",
    "STRUCTURAL_COLUMNS",
    "STRUCTURAL_TABLE",
    "SUMMARY_COLUMNS",
    "SUMMARY_TABLE",
    "UNKNOWN_LABEL",
    "MeasurementParams",
    "ReportInputs",
    "Slice",
    "assemble",
    "at_risk_sql",
    "binned_sql",
    "cells_sql",
    "compute_hourly",
    "compute_presence",
    "compute_structural",
    "flag_cells",
    "hourly_columns",
    "intervals_sql",
    "item_counts",
    "items_by_source",
    "measured_cells_sql",
    "occasions_sql",
    "params_of",
    "per_stay_day_sql",
    "population_counts_sql",
    "population_sql",
    "presence_rows",
    "presence_sql",
    "rate_ratio",
    "read_meta_frame",
    "read_slice",
    "read_slices",
    "register_measurement",
    "render_report",
    "run_hourly",
    "run_presence",
    "run_report",
    "run_structural",
    "slice_frame_path",
    "slice_meta_path",
    "slices_dir",
    "spec_path",
    "suppress_binned",
    "suppress_cells",
    "suppress_items",
    "suppress_presence",
    "write_report",
    "write_slice",
]
