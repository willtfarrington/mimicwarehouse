"""Capstone #1 — concepts / QC case study (EP-53; D-8, D-33, D-40; GOVERNANCE §5/§7).

The P3 capstone turns the phase's machinery — the concept layer (EP-37/38), unit curation
(EP-39), phenotypes (EP-41/42), the disclosure primitives (EP-43), QC profiles (EP-44) and
the measurement-process summaries (EP-45) — into one reproducible, disclosed artefact:
``docs/analyses/01-concepts-and-qc.md`` plus the tables and figures it embeds.

:func:`build` is the one entry point (``build(tier) -> run_id``): inside
``run.start(kind="report", claim_type="exploratory ...")`` it reads **only published
aggregates** — the ``meta.*`` registry tables through ``safe_query`` (every statement and
audit id on the run), the phenotype views through EP-42's summary helpers (subject-keyed,
count-family aggregates, k = 11 row-wise), the benchmark ledger (telemetry) and EP-45's
raw structural slice (data-root only; summed per item x era and k-suppressed here before
anything is written) — passes every frame through ``disclose.suppress`` / ``check_frame``
and writes:

* ``runs/<run_id>/tables/<name>.csv`` (+ a Parquet twin) — :data:`TABLES`:
  the concept inventory with rows per tier and full-tier wall time, the demo count-pins
  against the tier's live counts, the QC highlights (checks by status per table, the top
  warn / fail checks, the curated-item unit variants, the implausible-value shares, the
  timestamp-ordering rates), the phenotype prevalence overall and by ``anchor_year_group``
  era with Wilson intervals from released counts (statsmodels), the KDIGO stage
  distribution, the sepsis-3 vs explicit-code 2x2, and the measurement-process teaser
  (share of ICU stays measured in the first 24 h for ten curated items, by era);
* ``runs/<run_id>/figures/<name>.vl.json`` + ``.png`` — :data:`FIGURES`: concept build
  wall time by group, sepsis-3 / AKI prevalence by era with Wilson intervals (Altair, the
  EP-5 theme merged at serialisation, aggregates only in ``data.values``; PNG through
  ``vl-convert-python``).

:func:`promote` renders the run's CSVs as Markdown tables (``<name>.md``; the committed
form — ``.gitignore``, ``.gitattributes`` and the guard's G1 / G4 refuse committed CSVs by
design, owner decision at EP-53) and copies the figures into
``docs/analyses/01-concepts-and-qc/`` **only** through ``disclose.check`` and writes the
``.disclosure.json`` sidecar beside every file — the programmatic twin of ``mwh disclose
check <path> --write-sidecar``; a failing artefact is never written. :func:`load_table` /
:func:`md_table_frame` read a promoted table back as a typed frame. :func:`promote_tracer`
promotes the EP-31 tracer run folder (``report.md`` + the three JSON aggregates) into
``docs/analyses/02-tracer-first-icu-mortality.md`` + folder the same way (EP-33 amendment
(1)), prepending the claim-type label, the reader guides and the EP-35 ledger run's
reproduction block. The case study itself is hand-authored Markdown per the EP-32
convention; :func:`headline_numbers` / :func:`render_headline_block` render the two
headline numbers ``test_ep53`` re-checks against the promoted CSVs.

The tier rules (CLAUDE.md §3): fixture and dev in the foreground; full through the EP-19
job runner (``mwh build --tier full --select analyses.c01_concepts_qc --background --job
ep53-capstone``), the DAG step :func:`run_step` of ``dag/specs/analyses.yaml``. Nothing
here prints a row: every integer that reaches a committed file goes through
``inventory.fmt_int`` (committed-text rule 1) or a suppressed frame.

Import budget: not on the ``mwh`` start-up path (the DAG loads the callable lazily);
polars, altair, statsmodels, ``disclose``, ``run`` and ``safe`` load inside function bodies.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import shutil
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mimicwarehouse.config import Settings, get_settings, workspace_root

if TYPE_CHECKING:  # pragma: no cover
    import polars

    from mimicwarehouse.dag.runner import StepContext, StepOutcome
    from mimicwarehouse.dag.spec import Step
    from mimicwarehouse.run import Run

_LOG = logging.getLogger(__name__)

#: The DAG step (``dag/specs/analyses.yaml``) and its tags.
STEP_NAME = "analyses.c01_concepts_qc"
DAG_TAG = "analyses"
#: The EP-35 run: name, kind and the claim-type label (GOVERNANCE §7).
RUN_NAME = "c01_concepts_qc"
RUN_KIND = "report"
CLAIM_TYPE = "exploratory (concepts and data quality)"
RETROSPECTIVE_SENTENCE = "MIMIC-IV analyses are retrospective."
ACTOR = "c01_concepts_qc"
#: The tiers whose concept row counts the inventory carries, in order (the run's tier
#: and every listed tier before it; the fixture tier reads itself only).
INVENTORY_TIERS: tuple[str, ...] = ("demo", "dev", "full")
#: The phenotypes summarised (the latest built version per id on the tier).
PHENOTYPE_IDS: tuple[str, ...] = ("t2dm", "sepsis3", "kdigo_aki")
#: The 2x2 agreement pair (per admission with >= 1 ICU stay, EP-42).
AGREEMENT_PAIR: tuple[str, str] = ("sepsis3", "sepsis_explicit")
#: The KDIGO stage distribution column and its levels (``defs/kdigo_aki.yaml``).
KDIGO_ID = "kdigo_aki"
KDIGO_STAGE_COLUMN = "max_stage_in_window"
KDIGO_LEVELS: tuple[int, ...] = (0, 1, 2, 3)
#: The ten curated itemids of the measurement-process teaser (EP-39 catalogue): heart
#: rate, respiratory rate, SpO2, non-invasive mean BP, temperature (C), GCS eye opening,
#: creatinine, potassium, hemoglobin, lactate.
MP_ITEMIDS: tuple[int, ...] = (
    220045,
    220210,
    220277,
    220181,
    223762,
    220739,
    50912,
    50971,
    51222,
    50813,
)
#: The prevalence figure's phenotypes (icustay grain).
FIGURE_PHENOTYPES: tuple[str, ...] = ("sepsis3", "kdigo_aki")
#: How many warn / fail checks the QC highlight table keeps.
TOP_CHECKS = 10
#: The Wilson interval's confidence level.
CI_LEVEL = 0.95
#: Longest string value written into a table (the safe-query / run-folder bound).
VALUE_MAX_CHARS = 64
PNG_SCALE = 2
CHART_WIDTH = 480

#: The tables :func:`build` writes (``tables/<name>.csv`` + ``.parquet``).
TABLES: tuple[str, ...] = (
    "concept_inventory",
    "demo_pins_vs_tier",
    "qc_status_by_table",
    "qc_top_checks",
    "unit_variants",
    "implausible_values",
    "timestamp_ordering",
    "phenotype_prevalence",
    "kdigo_stage_distribution",
    "sepsis_agreement_2x2",
    "measurement_first24h_by_era",
)
#: The figures :func:`build` writes (``figures/<name>.vl.json`` + ``.png``).
FIGURES: tuple[str, ...] = ("concept_wall_by_group", "phenotype_prevalence_by_era")

#: The case study and its artefact folder (docs/analyses/README.md naming; committed-text
#: rule 3: no run id in a tracked name).
CASE_STUDY_SLUG = "01-concepts-and-qc"
TRACER_SLUG = "02-tracer-first-icu-mortality"
ANALYSES_RELPATH = Path("docs") / "analyses"
HEADLINE_MARK: tuple[str, str] = ("<!-- headline:begin -->", "<!-- headline:end -->")
#: The reproduction block's run line (``run.reproduction_block``).
RUN_LINE_RE = re.compile(r"Run `(?P<run_id>\d{8}T\d{6}Z-[0-9a-f]{6})` - kind `report`")
#: A relative Markdown link target (never http / mailto / an anchor).
LINK_RE = re.compile(r"\]\((?!https?://|mailto:|#)([^)\s]+)\)")
#: The tracer run-folder stamp (``runs/tracer/<yyyymmddThhmmss>-<tier>``).
TRACER_STAMP_RE = re.compile(r"^\d{8}T\d{6}-(fixture|demo|dev|full)$")

#: The QC check ids of the timestamp-ordering table.
ORDERING_CHECKS: tuple[str, ...] = ("ts_order", "ts_store_lag", "event_window")
IMPLAUSIBLE_CHECK = "implausible_values"
QC_STATUS_ORDER: tuple[str, ...] = ("fail", "warn", "pass")


class CapstoneError(RuntimeError):
    """A usage / environment problem (missing tier artefact, a frame that would not pass
    the disclosure gate, an unknown run) — never a data value."""


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _cut(value: Any) -> str | None:
    """A string value bounded to :data:`VALUE_MAX_CHARS` (None stays None)."""
    if value is None:
        return None
    text = str(value)
    return text if len(text) <= VALUE_MAX_CHARS else text[: VALUE_MAX_CHARS - 3] + "..."


def wilson_interval(n_positive: int | None, n_units: int | None) -> tuple[float, float] | None:
    """The Wilson score interval of ``n_positive / n_units`` at :data:`CI_LEVEL`
    (statsmodels), or None when either count is hidden or the denominator is zero —
    computed from **released** counts only (DIS-3)."""
    if n_positive is None or n_units is None or n_units <= 0:
        return None
    from statsmodels.stats.proportion import proportion_confint

    low, high = proportion_confint(n_positive, n_units, alpha=1 - CI_LEVEL, method="wilson")
    return float(low), float(high)


def _suppress(
    df: polars.DataFrame,
    k: int,
    *,
    count_cols: Sequence[str],
    group_cols: Sequence[str] | None,
) -> polars.DataFrame:
    """``disclose.suppress`` (table mode, complementary) — the frame only."""
    from mimicwarehouse.disclose import suppress

    if df.height == 0:
        return df
    out, _report = suppress(
        df, k=k, count_cols=list(count_cols), group_cols=list(group_cols or []), complementary=True
    )
    return out


def _with_interval(
    df: polars.DataFrame, *, positive: str, units: str, prefix: str = "ci"
) -> polars.DataFrame:
    """``share`` + ``<prefix>_low`` / ``<prefix>_high`` from the released ``positive`` /
    ``units`` counts of a suppressed frame (None beside a hidden count)."""
    import polars as pl

    lows: list[float | None] = []
    highs: list[float | None] = []
    shares: list[float | None] = []
    for row in df.to_dicts():
        n_pos, n_all = row.get(positive), row.get(units)
        ci = wilson_interval(n_pos, n_all)
        if ci is None:
            lows.append(None)
            highs.append(None)
            shares.append(None)
        else:
            lows.append(ci[0])
            highs.append(ci[1])
            shares.append(float(n_pos) / float(n_all))  # type: ignore[arg-type]
    return df.with_columns(
        pl.Series("share", shares, dtype=pl.Float64),
        pl.Series(f"{prefix}_low", lows, dtype=pl.Float64),
        pl.Series(f"{prefix}_high", highs, dtype=pl.Float64),
    )


def _meta_read(
    r: Run, sql: str, *, name: str, tier: str, k: int, row_cap: int = 10_000
) -> polars.DataFrame:
    """One audited ``safe_query`` read recorded on the run: ``Run.safe_query`` on the
    run's own tier; on another tier the statement, audit id and that tier's core
    snapshot id (``core.<tier>``) are recorded by hand so the run's ``core`` id stays the
    run tier's."""
    if tier == r.tier:
        return r.safe_query(sql, name=name, k=k, actor=ACTOR, row_cap=row_cap).df
    from mimicwarehouse.safe import safe_query

    result = safe_query(sql, tier=tier, k=k, actor=ACTOR, settings=r.settings, row_cap=row_cap)
    r.record_sql(name, sql)
    r.record_audit(result.audit_id)
    if result.snapshot_id:
        r.record_snapshot(f"core.{tier}", result.snapshot_id)
    return result.df


def inventory_tiers(tier: str) -> tuple[str, ...]:
    """The tiers whose concept row counts the inventory reads for a run on ``tier``:
    :data:`INVENTORY_TIERS` up to and including ``tier``, else ``(tier,)``."""
    if tier in INVENTORY_TIERS:
        return INVENTORY_TIERS[: INVENTORY_TIERS.index(tier) + 1]
    return (tier,)


# ---------------------------------------------------------------------------
# (a) concept inventory, (b) demo pins
# ---------------------------------------------------------------------------

#: ``rows`` keeps its name: the gate reserves count-named aliases for count aggregates
#: (EP-30); the frame renames it per tier below.
CONCEPT_VERSIONS_SQL = (
    'SELECT concept, "group" AS concept_group, upstream_commit, patch_id, "rows", status '
    "FROM meta.concept_versions"
)


def _concept_rows_by_tier(r: Run, tier: str, k: int) -> dict[str, polars.DataFrame]:
    """``{tier: meta.concept_versions frame}`` for :func:`inventory_tiers`; a tier whose
    catalog or table is absent is warned about and skipped (never the run's own tier)."""
    from mimicwarehouse.catalog.connect import CatalogOpenError
    from mimicwarehouse.safe import SafeQueryError, SafeQueryRefused

    frames: dict[str, polars.DataFrame] = {}
    for t in inventory_tiers(tier):
        try:
            frames[t] = _meta_read(
                r, CONCEPT_VERSIONS_SQL, name=f"concept_versions_{t}", tier=t, k=k
            )
        except (CatalogOpenError, SafeQueryError, SafeQueryRefused) as exc:
            if t == tier:
                raise CapstoneError(
                    f"meta.concept_versions is not readable on tier {tier} - run "
                    f"`mwh build --tier {tier} --tag concepts` first ({type(exc).__name__})"
                ) from exc
            r.warn(f"concept rows of tier {t} skipped: {type(exc).__name__}")
    return frames


def concept_wall(settings: Settings, tier: str) -> polars.DataFrame:
    """Per-concept build telemetry from the benchmark ledger (``kind: concept``, latest
    line per step on ``tier``): ``concept``, ``wall_s``, ``peak_rss_mb``."""
    import polars as pl

    from mimicwarehouse.dag import benchmarks

    summary = benchmarks.summarize(settings, tier=tier, kind="concept")
    if summary.is_empty():
        return pl.DataFrame(
            schema={"concept": pl.String, "wall_s": pl.Float64, "peak_rss_mb": pl.Float64}
        )
    return (
        summary.with_columns(pl.col("step").str.split(".").list.last().alias("concept"))
        .select("concept", "wall_s", "peak_rss_mb")
        .unique(subset=["concept"], keep="first", maintain_order=True)
    )


def build_concept_inventory(r: Run, tier: str, k: int) -> polars.DataFrame:
    """Table (a): concept, group, upstream commit, patch id, status, ``n_rows_<tier>`` per
    inventory tier (k-suppressed per concept, nested pairs handled by the primitive) and
    the tier's wall time / peak RSS from the benchmark ledger."""
    import polars as pl

    frames = _concept_rows_by_tier(r, tier, k)
    base = frames[tier].select("concept", "concept_group", "upstream_commit", "patch_id", "status")
    count_cols: list[str] = []
    out = base
    for t, frame in frames.items():
        column = f"n_rows_{t}"
        out = out.join(
            frame.select("concept", pl.col("rows").cast(pl.Int64).alias(column)),
            on="concept",
            how="left",
        )
        count_cols.append(column)
    out = _suppress(out, k, count_cols=count_cols, group_cols=["concept"])
    return out.join(concept_wall(r.settings, tier), on="concept", how="left")


def demo_pins() -> dict[str, int | None]:
    """The committed demo count-pins (``tests/ep/pins/concepts_demo.json``, EP-37/38):
    ``{concept: count}`` with a suppressed pin (``<11``) as None."""
    from mimicwarehouse.concepts.pins import demo_pins_path, read_pins

    path = demo_pins_path()
    if not path.is_file():
        return {}
    counts = read_pins(path).get("counts", {})
    return {
        str(name): (int(value) if isinstance(value, int) and not isinstance(value, bool) else None)
        for name, value in counts.items()
    }


def build_demo_pins(inventory: polars.DataFrame, tier: str, k: int) -> polars.DataFrame:
    """Table (b): the committed demo pin against the live demo count (when the inventory
    read it) and the run tier's count, ``pin_matches`` and the ``tier_to_demo_ratio``
    computed in Python from released counts (blanked beside a hidden count)."""
    import polars as pl

    pins = demo_pins()
    tier_col = f"n_rows_{tier}"
    live_col = "n_rows_demo" if "n_rows_demo" in inventory.columns else None
    records: list[dict[str, Any]] = []
    for row in inventory.to_dicts():
        name = str(row["concept"])
        pin = pins.get(name)
        live = row.get(live_col) if live_col else None
        live_hidden = bool(row.get(f"{live_col}_suppressed")) if live_col else False
        n_tier = row.get(tier_col)
        records.append(
            {
                "concept": name,
                "n_pin_demo": pin,
                "n_pin_demo_suppressed": pin is None and name in pins,
                "n_rows_demo": live,
                "n_rows_demo_suppressed": live_hidden,
                "n_rows_tier": n_tier,
                "n_rows_tier_suppressed": bool(row.get(f"{tier_col}_suppressed")),
                "tier": tier,
                "pin_matches": (
                    None
                    if live_col is None or name not in pins
                    else (pin == live if not live_hidden else pin is None)
                ),
            }
        )
    schema = {
        "concept": pl.String,
        "n_pin_demo": pl.Int64,
        "n_pin_demo_suppressed": pl.Boolean,
        "n_rows_demo": pl.Int64,
        "n_rows_demo_suppressed": pl.Boolean,
        "n_rows_tier": pl.Int64,
        "n_rows_tier_suppressed": pl.Boolean,
        "tier": pl.String,
        "pin_matches": pl.Boolean,
    }
    df = pl.DataFrame(records, schema=schema)
    df = _suppress(
        df, k, count_cols=["n_pin_demo", "n_rows_demo", "n_rows_tier"], group_cols=["concept"]
    )
    ratios = [
        (float(row["n_rows_tier"]) / float(row["n_pin_demo"]))
        if row.get("n_rows_tier") is not None and row.get("n_pin_demo")
        else None
        for row in df.to_dicts()
    ]
    return df.with_columns(pl.Series("tier_to_demo_ratio", ratios, dtype=pl.Float64))


# ---------------------------------------------------------------------------
# (c) QC highlights
# ---------------------------------------------------------------------------

QC_CHECKS_SQL = (
    'SELECT check_id, "schema", "table", "column", itemid, label, metric, value, threshold, '
    "status, n_affected, n_affected_suppressed, detail FROM meta.qc_checks"
)
UNIT_VARIANTS_SQL = (
    "SELECT itemid, source, valueuom, unit_norm, n_rows, share, suppressed, expected "
    "FROM meta.item_unit_variants"
)
ITEM_UNITS_SQL = "SELECT itemid, label, canonical_unit FROM meta.item_units"


def read_qc_checks(r: Run, k: int) -> polars.DataFrame:
    """``meta.qc_checks`` of the run's tier (already suppressed at build time, EP-44)."""
    from mimicwarehouse.safe import SafeQueryError, SafeQueryRefused

    try:
        return _meta_read(r, QC_CHECKS_SQL, name="qc_checks", tier=r.tier, k=k, row_cap=50_000)
    except (SafeQueryError, SafeQueryRefused) as exc:
        raise CapstoneError(
            f"meta.qc_checks is not readable on tier {r.tier} - run "
            f"`mwh build --tier {r.tier} --tag qc` first ({type(exc).__name__})"
        ) from exc


def build_qc_status_by_table(checks: polars.DataFrame) -> polars.DataFrame:
    """Checks by status per table (counts of checks, never of people): ``checks_pass`` /
    ``checks_warn`` / ``checks_fail`` and the worst status."""
    import polars as pl

    if checks.height == 0:
        return pl.DataFrame(
            schema={
                "schema": pl.String,
                "table": pl.String,
                "checks_pass": pl.Int64,
                "checks_warn": pl.Int64,
                "checks_fail": pl.Int64,
                "worst_status": pl.String,
            }
        )
    out = checks.group_by("schema", "table", maintain_order=True).agg(
        pl.col("status").eq("pass").sum().cast(pl.Int64).alias("checks_pass"),
        pl.col("status").eq("warn").sum().cast(pl.Int64).alias("checks_warn"),
        pl.col("status").eq("fail").sum().cast(pl.Int64).alias("checks_fail"),
    )
    worst = (
        pl.when(pl.col("checks_fail") > 0)
        .then(pl.lit("fail"))
        .when(pl.col("checks_warn") > 0)
        .then(pl.lit("warn"))
        .otherwise(pl.lit("pass"))
    )
    return out.with_columns(worst.alias("worst_status")).sort("schema", "table")


def _severity(row: Mapping[str, Any]) -> tuple[int, float, str, str]:
    status = str(row.get("status"))
    value, threshold = row.get("value"), row.get("threshold")
    ratio = 0.0
    if isinstance(value, int | float) and isinstance(threshold, int | float) and threshold:
        ratio = float(value) / float(threshold)
    return (
        QC_STATUS_ORDER.index(status) if status in QC_STATUS_ORDER else len(QC_STATUS_ORDER),
        -ratio,
        str(row.get("check_id")),
        f"{row.get('schema')}.{row.get('table')}",
    )


def build_qc_top_checks(checks: polars.DataFrame, top: int = TOP_CHECKS) -> polars.DataFrame:
    """The ``top`` warn / fail checks: failures first, then by the value-to-threshold
    ratio; ``n_affected`` keeps its build-time marker."""
    import polars as pl

    flagged = [row for row in checks.to_dicts() if row.get("status") in ("warn", "fail")]
    flagged.sort(key=_severity)
    records = [
        {
            "status": row["status"],
            "check_id": row["check_id"],
            "table": f"{row['schema']}.{row['table']}",
            "column": row.get("column"),
            "itemid": row.get("itemid"),
            "label": _cut(row.get("label")),
            "metric": row.get("metric"),
            "value": row.get("value"),
            "threshold": row.get("threshold"),
            "n_affected": row.get("n_affected"),
            "n_affected_suppressed": bool(row.get("n_affected_suppressed")),
            "detail": _cut(row.get("detail")),
        }
        for row in flagged[:top]
    ]
    return pl.DataFrame(
        records,
        schema={
            "status": pl.String,
            "check_id": pl.String,
            "table": pl.String,
            "column": pl.String,
            "itemid": pl.Int64,
            "label": pl.String,
            "metric": pl.String,
            "value": pl.Float64,
            "threshold": pl.Float64,
            "n_affected": pl.Int64,
            "n_affected_suppressed": pl.Boolean,
            "detail": pl.String,
        },
    )


def build_unit_variants(r: Run, k: int) -> polars.DataFrame:
    """Unit variants per curated itemid (EP-39's ``summarize_variants`` over the
    published, k-suppressed ``meta.item_unit_variants``): unit-string counts are counts
    of strings, named so (``unit_strings``), never of people."""
    import polars as pl

    from mimicwarehouse.safe import SafeQueryError, SafeQueryRefused
    from mimicwarehouse.units import summarize_variants

    try:
        variants = _meta_read(r, UNIT_VARIANTS_SQL, name="item_unit_variants", tier=r.tier, k=k)
        units = _meta_read(r, ITEM_UNITS_SQL, name="item_units", tier=r.tier, k=k)
    except (SafeQueryError, SafeQueryRefused) as exc:
        raise CapstoneError(
            f"meta.item_unit_variants / meta.item_units are not readable on tier {r.tier} - "
            f"run `mwh build --tier {r.tier} --tag units` first ({type(exc).__name__})"
        ) from exc
    summary = summarize_variants(variants, units.unique(subset=["itemid"], maintain_order=True))
    return summary.select(
        pl.col("itemid").cast(pl.Int64),
        pl.col("label").map_elements(_cut, return_dtype=pl.String),
        "canonical_unit",
        pl.col("n_variants").cast(pl.Int64).alias("unit_strings"),
        pl.col("n_suppressed").cast(pl.Int64).alias("hidden_unit_strings"),
        pl.col("dominant_unit").map_elements(_cut, return_dtype=pl.String),
        "dominant_share",
        pl.col("unexpected_units")
        .cast(pl.List(pl.String))
        .list.join(", ")
        .map_elements(_cut, return_dtype=pl.String),
        "flagged",
    ).sort("itemid")


def build_implausible_values(checks: polars.DataFrame) -> polars.DataFrame:
    """The implausible-value shares of the curated itemids (``implausible_values`` checks)."""
    import polars as pl

    rows = [
        {
            "itemid": row.get("itemid"),
            "label": _cut(row.get("label")),
            "table": f"{row['schema']}.{row['table']}",
            "bounds": _cut(str(row.get("detail") or "").removeprefix("bounds ")),
            "implausible_share": row.get("value"),
            "n_affected": row.get("n_affected"),
            "n_affected_suppressed": bool(row.get("n_affected_suppressed")),
            "status": row.get("status"),
        }
        for row in checks.to_dicts()
        if row.get("check_id") == IMPLAUSIBLE_CHECK
    ]
    rows.sort(key=lambda x: (-(x["implausible_share"] or 0.0), int(x["itemid"] or 0)))
    return pl.DataFrame(
        rows,
        schema={
            "itemid": pl.Int64,
            "label": pl.String,
            "table": pl.String,
            "bounds": pl.String,
            "implausible_share": pl.Float64,
            "n_affected": pl.Int64,
            "n_affected_suppressed": pl.Boolean,
            "status": pl.String,
        },
    )


def build_timestamp_ordering(checks: polars.DataFrame) -> polars.DataFrame:
    """The timestamp-ordering rates (``ts_order`` / ``ts_store_lag`` / ``event_window``)."""
    import polars as pl

    rows = [
        {
            "table": f"{row['schema']}.{row['table']}",
            "check_id": row["check_id"],
            "rule": _cut(row.get("detail")),
            "violation_share": row.get("value"),
            "n_affected": row.get("n_affected"),
            "n_affected_suppressed": bool(row.get("n_affected_suppressed")),
            "status": row.get("status"),
        }
        for row in checks.to_dicts()
        if row.get("check_id") in ORDERING_CHECKS
    ]
    rows.sort(key=lambda x: (x["table"], x["check_id"], x["rule"] or ""))
    return pl.DataFrame(
        rows,
        schema={
            "table": pl.String,
            "check_id": pl.String,
            "rule": pl.String,
            "violation_share": pl.Float64,
            "n_affected": pl.Int64,
            "n_affected_suppressed": pl.Boolean,
            "status": pl.String,
        },
    )


# ---------------------------------------------------------------------------
# (d) phenotypes
# ---------------------------------------------------------------------------


def latest_phenotypes(r: Run) -> dict[str, dict[str, Any]]:
    """``{phenotype_id: meta.phenotype_versions row}`` of the latest **done** version per
    id on the run's tier (semver order)."""
    from mimicwarehouse.phenotypes.runner import built_versions, semver_key

    latest: dict[str, dict[str, Any]] = {}
    for row in built_versions(r.tier, settings=r.settings, actor=ACTOR):
        if row.get("status") != "done":
            continue
        pid = str(row["phenotype_id"])
        if pid not in latest or semver_key(str(row["version"])) > semver_key(
            str(latest[pid]["version"])
        ):
            latest[pid] = row
    return latest


def build_phenotype_prevalence(
    r: Run, k: int, latest: Mapping[str, Mapping[str, Any]]
) -> polars.DataFrame:
    """Table (d1): prevalence of :data:`PHENOTYPE_IDS` overall and by era (EP-42's
    ``summarize`` through the run: subject-keyed count-family reads, k row-wise), then
    ``disclose.suppress`` over (phenotype, scope) and Wilson intervals from released
    counts. A phenotype not built on the tier is warned about and skipped."""
    import polars as pl

    from mimicwarehouse.phenotypes.runner import summarize
    from mimicwarehouse.phenotypes.spec import PhenotypeError

    records: list[dict[str, Any]] = []
    for pid in PHENOTYPE_IDS:
        entry = latest.get(pid)
        if entry is None:
            r.warn(f"phenotype {pid} has no built version on tier {r.tier}; skipped")
            continue
        ref = f"{pid}@{entry['version']}"
        try:
            summary = summarize(ref, tier=r.tier, settings=r.settings, k=k, actor=ACTOR, run=r)
        except PhenotypeError as exc:
            r.warn(f"phenotype {pid} summary skipped: {type(exc).__name__}")
            continue
        r.record_ref("phenotype", pid, version=str(entry["version"]), hash=entry.get("def_hash"))
        for row in summary.df.to_dicts():
            records.append(
                {
                    "phenotype": pid,
                    "version": str(entry["version"]),
                    "grain": summary.grain,
                    "scope": str(row["scope"]),
                    "unit": str(row["unit"]),
                    "n_units": row.get("n_units"),
                    "n_positive": row.get("n_positive"),
                }
            )
    df = pl.DataFrame(
        records,
        schema={
            "phenotype": pl.String,
            "version": pl.String,
            "grain": pl.String,
            "scope": pl.String,
            "unit": pl.String,
            "n_units": pl.Int64,
            "n_positive": pl.Int64,
        },
    )
    df = _suppress(df, k, count_cols=["n_units", "n_positive"], group_cols=["phenotype", "scope"])
    return _with_interval(df, positive="n_positive", units="n_units")


def build_kdigo_stages(r: Run, k: int, latest: Mapping[str, Mapping[str, Any]]) -> polars.DataFrame:
    """Table (d2): the KDIGO ``max_stage_in_window`` distribution over ICU stays."""
    import polars as pl

    from mimicwarehouse.phenotypes.runner import distribution
    from mimicwarehouse.phenotypes.spec import PhenotypeError

    schema = {
        "phenotype": pl.String,
        "level": pl.Int64,
        "n_units": pl.Int64,
        "n_positive": pl.Int64,
    }
    entry = latest.get(KDIGO_ID)
    if entry is None:
        r.warn(f"phenotype {KDIGO_ID} has no built version on tier {r.tier}; stages skipped")
        return pl.DataFrame(schema=schema)
    ref = f"{KDIGO_ID}@{entry['version']}"
    try:
        dist = distribution(
            ref,
            KDIGO_STAGE_COLUMN,
            tier=r.tier,
            settings=r.settings,
            k=k,
            actor=ACTOR,
            run=r,
            levels=KDIGO_LEVELS,
        )
    except PhenotypeError as exc:
        r.warn(f"{KDIGO_ID} stage distribution skipped: {type(exc).__name__}")
        return pl.DataFrame(schema=schema)
    records = [
        {
            "phenotype": KDIGO_ID,
            "level": int(row["level"]) if row.get("level") is not None else None,
            "n_units": row.get("n_units"),
            "n_positive": row.get("n_positive"),
        }
        for row in dist.df.to_dicts()
    ]
    df = pl.DataFrame(records, schema=schema).sort("level")
    df = _suppress(df, k, count_cols=["n_units", "n_positive"], group_cols=["level"])
    total = df.get_column("n_units").sum() if df.height else 0
    shares = [
        (float(v) / float(total)) if v is not None and total and not hidden else None
        for v, hidden in zip(
            df.get_column("n_units").to_list(),
            df.get_column("n_units_suppressed").to_list(),
            strict=True,
        )
    ]
    return df.with_columns(pl.Series("share_of_released", shares, dtype=pl.Float64))


def build_sepsis_agreement(
    r: Run, k: int, latest: Mapping[str, Mapping[str, Any]]
) -> polars.DataFrame:
    """Table (d3): the sepsis-3 vs explicit-code 2x2 per admission with >= 1 ICU stay
    (EP-42's ``agreement``), long form: one row per cell plus the ``all`` total."""
    import polars as pl

    from mimicwarehouse.phenotypes.runner import agreement
    from mimicwarehouse.phenotypes.spec import PhenotypeError

    schema = {
        "cell": pl.String,
        "n_admissions": pl.Int64,
        "denominator": pl.String,
        "ref_a": pl.String,
        "ref_b": pl.String,
    }
    a, b = AGREEMENT_PAIR
    if a not in latest or b not in latest:
        r.warn(f"agreement {a} vs {b} skipped: a phenotype is not built on tier {r.tier}")
        return pl.DataFrame(schema=schema)
    ref_a, ref_b = f"{a}@{latest[a]['version']}", f"{b}@{latest[b]['version']}"
    try:
        result = agreement(ref_a, ref_b, tier=r.tier, settings=r.settings, k=k, actor=ACTOR, run=r)
    except PhenotypeError as exc:
        r.warn(f"agreement {a} vs {b} skipped: {type(exc).__name__}")
        return pl.DataFrame(schema=schema)
    r.record_ref("phenotype", b, version=str(latest[b]["version"]), hash=latest[b].get("def_hash"))
    cells = [
        ("both", result.n_both),
        (f"{a}_only", result.n_a_only),
        (f"{b}_only", result.n_b_only),
        ("neither", result.n_neither),
        ("all", result.n_hadm),
    ]
    df = pl.DataFrame(
        [
            {
                "cell": cell,
                "n_admissions": n,
                "denominator": _cut(result.denominator),
                "ref_a": ref_a,
                "ref_b": ref_b,
            }
            for cell, n in cells
        ],
        schema=schema,
    )
    df = _suppress(df, k, count_cols=["n_admissions"], group_cols=["cell"])
    if result.suppressed:
        df = df.with_columns(
            pl.lit(None, dtype=pl.Int64).alias("n_admissions"),
            pl.lit(True).alias("n_admissions_suppressed"),
        )
    total = result.n_hadm
    shares = [
        (float(n) / float(total)) if n is not None and total and not hidden else None
        for n, hidden in zip(
            df.get_column("n_admissions").to_list(),
            df.get_column("n_admissions_suppressed").to_list(),
            strict=True,
        )
    ]
    return df.with_columns(pl.Series("share", shares, dtype=pl.Float64))


# ---------------------------------------------------------------------------
# (e) measurement-process teaser
# ---------------------------------------------------------------------------


def build_measurement_teaser(r: Run, k: int) -> polars.DataFrame:
    """Table (e): for :data:`MP_ITEMIDS`, the ICU stays measured in the first 24 h by
    era — EP-45's raw structural slice (itemid x care unit x era; data-root only) summed
    over care units per (item, era), k-suppressed here with the same primitive the
    published ``meta.mp_*`` tables went through, then the share and its Wilson interval
    from the released counts."""
    import polars as pl

    from mimicwarehouse.qc import measurement as mp
    from mimicwarehouse.qc.profile import QcError
    from mimicwarehouse.units import load_catalogue

    lake = r.settings.lake_root(r.tier)
    try:
        slice_ = mp.read_slice(lake, r.tier, mp.STEP_STRUCTURAL)
    except QcError as exc:
        raise CapstoneError(str(exc)) from exc
    cells = slice_.frames["cells"]
    catalogue = load_catalogue()
    present = [i for i in MP_ITEMIDS if i in catalogue.itemids()]
    if len(present) < len(MP_ITEMIDS):
        r.warn(f"{len(MP_ITEMIDS) - len(present)} teaser itemid(s) are not in the catalogue")
    r.record_ref(
        "measurement_slice",
        mp.STEP_STRUCTURAL,
        hash=str(slice_.meta.get("snapshot_id") or "") or None,
    )
    grouped = (
        cells.filter(pl.col("itemid").is_in(present))
        .group_by("itemid", "era", maintain_order=True)
        .agg(
            pl.col("n_stays").sum().cast(pl.Int64).alias("n_stays"),
            pl.col("n_stays_measured_first_24h")
            .sum()
            .cast(pl.Int64)
            .alias("n_stays_measured_first_24h"),
        )
    )
    labels = {i: _cut(catalogue.spec(i).label) for i in present}
    sources = {i: catalogue.spec(i).source for i in present}
    grouped = grouped.with_columns(
        pl.col("itemid").cast(pl.Int64),
        pl.col("itemid")
        .map_elements(lambda i: labels.get(int(i)), return_dtype=pl.String)
        .alias("label"),
        pl.col("itemid")
        .map_elements(lambda i: sources.get(int(i)), return_dtype=pl.String)
        .alias("source"),
        pl.col("era").cast(pl.String),
    ).select("itemid", "label", "source", "era", "n_stays", "n_stays_measured_first_24h")
    grouped = grouped.sort("itemid", "era")
    df = _suppress(
        grouped,
        k,
        count_cols=["n_stays", "n_stays_measured_first_24h"],
        group_cols=["itemid", "era"],
    )
    out = _with_interval(df, positive="n_stays_measured_first_24h", units="n_stays")
    return out.rename({"share": "measured_first_24h_share"})


# ---------------------------------------------------------------------------
# Figures
# ---------------------------------------------------------------------------


def _vegalite(chart: Any, *, mode: str = "light") -> dict[str, Any]:
    """The Vega-Lite spec of an Altair chart with the EP-5 theme merged in (as a
    context, so the process-wide theme is untouched) and the inline data kept under
    ``data.values`` (aggregates only) — the EP-48 pattern."""
    import altair as alt

    from mimicwarehouse import theme

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


def concept_wall_chart(inventory: polars.DataFrame, *, tier: str, mode: str = "light") -> Any:
    """Figure 1: concept build wall time by group (bar; the tier's latest build)."""
    import altair as alt
    import polars as pl

    from mimicwarehouse import theme

    palette = theme.palette(mode)
    groups = (
        inventory.filter(pl.col("wall_s").is_not_null())
        .group_by("concept_group", maintain_order=True)
        .agg(
            pl.len().cast(pl.Int64).alias("concepts"),
            pl.col("wall_s").sum().round(3).alias("wall_s"),
            pl.col("wall_s").max().round(3).alias("max_wall_s"),
        )
        .sort("wall_s", descending=True)
    )
    data = alt.InlineData(values=groups.to_dicts())
    chart = (
        alt.Chart(data)
        .mark_bar(color=palette.primary)
        .encode(
            y=alt.Y("concept_group:N", sort="-x", title="concept group"),
            x=alt.X("wall_s:Q", title="build wall time (s), summed over the group's concepts"),
            tooltip=[
                alt.Tooltip("concept_group:N", title="group"),
                alt.Tooltip("concepts:Q", title="concepts"),
                alt.Tooltip("wall_s:Q", title="wall s", format=".2f"),
                alt.Tooltip("max_wall_s:Q", title="slowest concept s", format=".2f"),
            ],
        )
        .properties(
            width=CHART_WIDTH,
            height=alt.Step(22),
            title=alt.TitleParams(
                text=f"Concept build wall time by group ({tier} tier)",
                subtitle="mimic-code concepts_duckdb on DuckDB 1.5.5; benchmark ledger",
            ),
        )
    )
    return chart


def prevalence_chart(prevalence: polars.DataFrame, *, tier: str, mode: str = "light") -> Any:
    """Figure 2: sepsis-3 / KDIGO AKI prevalence by era (grouped bar) with Wilson
    intervals (rules); released rows only."""
    import altair as alt
    import polars as pl

    from mimicwarehouse import theme

    palette = theme.palette(mode)
    rows = prevalence.filter(
        (pl.col("scope") != "all")
        & pl.col("phenotype").is_in(list(FIGURE_PHENOTYPES))
        & pl.col("share").is_not_null()
    ).select(pl.col("scope").alias("era"), "phenotype", "share", "ci_low", "ci_high", "n_units")
    data = alt.InlineData(values=rows.to_dicts())
    base = alt.Chart(data)
    bars = base.mark_bar().encode(
        x=alt.X("era:N", title="anchor_year_group era"),
        xOffset=alt.XOffset("phenotype:N"),
        y=alt.Y("share:Q", title="prevalence among ICU stays", axis=alt.Axis(format="%")),
        color=alt.Color("phenotype:N", title="phenotype"),
        tooltip=[
            alt.Tooltip("phenotype:N", title="phenotype"),
            alt.Tooltip("era:N", title="era"),
            alt.Tooltip("share:Q", title="prevalence", format=".1%"),
            alt.Tooltip("ci_low:Q", title="Wilson low", format=".1%"),
            alt.Tooltip("ci_high:Q", title="Wilson high", format=".1%"),
            alt.Tooltip("n_units:Q", title="ICU stays", format=","),
        ],
    )
    rules = base.mark_rule(color=palette.text).encode(
        x=alt.X("era:N"),
        xOffset=alt.XOffset("phenotype:N"),
        y=alt.Y("ci_low:Q"),
        y2=alt.Y2("ci_high:Q"),
    )
    chart = alt.layer(bars, rules).properties(
        width=CHART_WIDTH,
        height=260,
        title=alt.TitleParams(
            text=f"Sepsis-3 and KDIGO AKI prevalence by era ({tier} tier)",
            subtitle=f"ICU stays; rules = {CI_LEVEL:.0%} Wilson intervals from released counts",
        ),
    )
    return chart


# ---------------------------------------------------------------------------
# Writing into the run folder
# ---------------------------------------------------------------------------


def _gate_frame(name: str, df: polars.DataFrame, k: int) -> None:
    from mimicwarehouse import disclose

    fails = [f for f in disclose.check_frame(df, k, where=name) if f.status == disclose.FAIL]
    if fails:
        codes = ", ".join(f"{f.code} ({f.where})" for f in fails[:5])
        raise CapstoneError(f"table {name} would not pass the disclosure gate: {codes}")


def _gate_path(path: Path, k: int) -> None:
    from mimicwarehouse import disclose

    result = disclose.check(path, k=k)
    if not result.passed:
        codes = ", ".join(f"{f.code} ({f.where})" for f in result.findings if f.status == "fail")
        raise CapstoneError(f"{path.name} would not pass the disclosure gate: {codes}")


def _save_table(r: Run, name: str, df: polars.DataFrame, k: int) -> Path:
    """Gate the frame, write ``tables/<name>.parquet`` (``Run.save_table``: the
    identifier-column guard) and the CSV twin, register both in the manifest."""
    from mimicwarehouse.run import TABLES_DIRNAME

    _gate_frame(name, df, k)
    r.save_table(name, df)
    csv_path = r.dir / TABLES_DIRNAME / f"{name}.csv"
    df.write_csv(csv_path)
    r.manifest.tables = {**r.manifest.tables, f"{name}.csv": f"{TABLES_DIRNAME}/{name}.csv"}
    _gate_path(csv_path, k)
    return csv_path


def _save_figure(r: Run, name: str, chart: Any, k: int) -> tuple[Path, Path]:
    """Write ``figures/<name>.vl.json`` (theme merged, inline aggregates) and the PNG,
    register both, gate both."""
    from mimicwarehouse import fsio
    from mimicwarehouse.run import FIGURES_DIRNAME

    spec = _vegalite(chart)
    directory = r.dir / FIGURES_DIRNAME
    directory.mkdir(parents=True, exist_ok=True)
    vl_path = directory / f"{name}.vl.json"
    fsio.atomic_write_text(vl_path, json.dumps(spec, indent=2) + "\n")
    r.manifest.figures = {**r.manifest.figures, f"{name}.vl": f"{FIGURES_DIRNAME}/{name}.vl.json"}
    png_path = r.save_figure(name, to_png(spec))
    _gate_path(vl_path, k)
    _gate_path(png_path, k)
    return vl_path, png_path


def build(tier: str = "full", *, settings: Settings | None = None, k: int | None = None) -> str:
    """The capstone run (module docstring): returns the run id."""
    from mimicwarehouse import run as run_mod
    from mimicwarehouse.concepts import vendor_info

    settings = settings or get_settings()
    resolved_k = k if k is not None else settings.k_suppression
    params = {
        "k": resolved_k,
        "inventory_tiers": list(inventory_tiers(tier)),
        "phenotypes": list(PHENOTYPE_IDS),
        "agreement": list(AGREEMENT_PAIR),
        "kdigo_stage_column": KDIGO_STAGE_COLUMN,
        "mp_itemids": list(MP_ITEMIDS),
        "top_checks": TOP_CHECKS,
        "ci_level": CI_LEVEL,
        "tables": list(TABLES),
        "figures": list(FIGURES),
    }
    with run_mod.start(
        RUN_NAME, tier=tier, kind=RUN_KIND, params=params, settings=settings, claim_type=CLAIM_TYPE
    ) as r:
        for layer in ("core", "derived"):
            try:
                r.read_layer(layer)
            except run_mod.RunLedgerError as exc:
                r.warn(f"snapshot of layer {layer} not recorded: {type(exc).__name__}")
        try:
            r.record_ref("mimic_code", "concepts_duckdb", hash=vendor_info().sha)
        except Exception as exc:  # the vendor manifest is a packaging fact, never fatal here
            r.warn(f"mimic-code pin not recorded: {type(exc).__name__}")

        inventory = build_concept_inventory(r, tier, resolved_k)
        _save_table(r, "concept_inventory", inventory, resolved_k)
        _save_table(
            r, "demo_pins_vs_tier", build_demo_pins(inventory, tier, resolved_k), resolved_k
        )

        checks = read_qc_checks(r, resolved_k)
        _save_table(r, "qc_status_by_table", build_qc_status_by_table(checks), resolved_k)
        _save_table(r, "qc_top_checks", build_qc_top_checks(checks), resolved_k)
        _save_table(r, "unit_variants", build_unit_variants(r, resolved_k), resolved_k)
        _save_table(r, "implausible_values", build_implausible_values(checks), resolved_k)
        _save_table(r, "timestamp_ordering", build_timestamp_ordering(checks), resolved_k)

        latest = latest_phenotypes(r)
        prevalence = build_phenotype_prevalence(r, resolved_k, latest)
        _save_table(r, "phenotype_prevalence", prevalence, resolved_k)
        _save_table(
            r, "kdigo_stage_distribution", build_kdigo_stages(r, resolved_k, latest), resolved_k
        )
        _save_table(
            r, "sepsis_agreement_2x2", build_sepsis_agreement(r, resolved_k, latest), resolved_k
        )

        _save_table(
            r, "measurement_first24h_by_era", build_measurement_teaser(r, resolved_k), resolved_k
        )

        _save_figure(
            r, "concept_wall_by_group", concept_wall_chart(inventory, tier=tier), resolved_k
        )
        _save_figure(
            r, "phenotype_prevalence_by_era", prevalence_chart(prevalence, tier=tier), resolved_k
        )
        _LOG.info(
            "capstone c01 (%s): run %s - %d table(s), %d figure(s) under %s",
            tier,
            r.run_id,
            len(TABLES),
            len(FIGURES),
            r.dir,
        )
        return r.run_id


def run_step(step: Step, ctx: StepContext) -> StepOutcome:
    """The ``analyses.c01_concepts_qc`` DAG handler: :func:`build` on the build's tier."""
    from mimicwarehouse.dag.runner import StepOutcome
    from mimicwarehouse.run import run_dir

    run_id = build(ctx.tier, settings=ctx.settings)
    ctx.state[f"{STEP_NAME}.run_id"] = run_id
    folder = run_dir(run_id, ctx.settings)
    files = [p for sub in ("tables", "figures") for p in (folder / sub).glob("*") if p.is_file()]
    ctx.log.info("%s: run %s (%d file(s))", step.name, run_id, len(files))
    return StepOutcome(
        rows=len(TABLES), bytes_out=sum(p.stat().st_size for p in files), files=len(files)
    )


# ---------------------------------------------------------------------------
# Promotion into docs/analyses/ (through the disclosure gate)
# ---------------------------------------------------------------------------


def analyses_dir() -> Path:
    """``<workspace>/docs/analyses``."""
    return workspace_root() / ANALYSES_RELPATH


def case_study_path() -> Path:
    return analyses_dir() / f"{CASE_STUDY_SLUG}.md"


def artefact_dir() -> Path:
    return analyses_dir() / CASE_STUDY_SLUG


def tracer_case_study_path() -> Path:
    return analyses_dir() / f"{TRACER_SLUG}.md"


def tracer_artefact_dir() -> Path:
    return analyses_dir() / TRACER_SLUG


def _promote_file(src: Path, dst: Path, k: int, *, git_sha: str | None) -> Path:
    """Copy ``src`` to ``dst`` only if it passes ``disclose.check``; re-check the copy and
    write its sidecar (the programmatic ``mwh disclose check --write-sidecar``)."""
    from mimicwarehouse import disclose

    source = disclose.check(src, k=k)
    if not source.passed:
        codes = ", ".join(f"{f.code} ({f.where})" for f in source.findings if f.status == "fail")
        raise CapstoneError(f"{src.name} refused by the disclosure gate, not promoted: {codes}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    copied = disclose.check(dst, k=k)
    if not copied.passed:  # pragma: no cover - a byte copy of a passing file passes
        dst.unlink(missing_ok=True)
        raise CapstoneError(f"{dst.name} refused after copy; removed")
    disclose.write_sidecar(dst, copied, k, git_sha=git_sha)
    return dst


def promote(
    run_id: str,
    dest: Path | str | None = None,
    *,
    settings: Settings | None = None,
    k: int | None = None,
) -> list[Path]:
    """Promote the run's tables and figures into ``dest`` (default :func:`artefact_dir`):
    every ``tables/<name>.csv`` is **rendered as a Markdown table** ``<name>.md``
    (:func:`render_table`: integers through ``fmt_int``, hidden cells ``<k``, marker
    columns folded) and gated + sidecar'd by :func:`_publish_text`; the figures
    (``.vl.json`` before ``.png`` so the PNG's source sibling exists) are copied through
    :func:`_promote_file`. Returns the promoted paths. The CSV and Parquet twins stay in
    the run folder: committed CSVs are refused by ``.gitignore``, ``.gitattributes`` and
    the guard's G1 / G4 by design (GOVERNANCE section 3; owner decision at EP-53), so the
    committed form of a table is Markdown. A previous promotion's ``<name>.csv`` (+
    sidecar) under ``dest`` is removed."""
    from mimicwarehouse.run import git_sha, read_manifest, run_dir

    settings = settings or get_settings()
    resolved_k = k if k is not None else settings.k_suppression
    manifest = read_manifest(run_id, settings)
    if manifest.name != RUN_NAME or manifest.kind != RUN_KIND:
        raise CapstoneError(f"run {run_id} is not a {RUN_NAME} report run (kind {manifest.kind})")
    folder = run_dir(run_id, settings)
    target = Path(dest) if dest is not None else artefact_dir()
    sha = git_sha()
    rels = [rel for rel in manifest.tables.values() if rel.endswith(".csv")]
    figure_rels = sorted(
        manifest.figures.values(), key=lambda rel: (not rel.endswith(".vl.json"), rel)
    )
    out: list[Path] = []
    for rel in rels:
        src = folder / rel
        if not src.is_file():
            raise CapstoneError(f"run {run_id}: {rel} is missing from the run folder")
        name = src.name.removesuffix(".csv")
        for stale in (target / src.name, target / f"{src.name}{_sidecar_suffix()}"):
            if stale.is_file():
                stale.unlink()
                _LOG.info("promote: removed the previous CSV form %s", stale.name)
        out.append(
            _publish_text(
                target / f"{name}.md", render_table(src, k=resolved_k), resolved_k, git_sha=sha
            )
        )
    for rel in figure_rels:
        src = folder / rel
        if not src.is_file():
            raise CapstoneError(f"run {run_id}: {rel} is missing from the run folder")
        out.append(_promote_file(src, target / src.name, resolved_k, git_sha=sha))
    return out


def _sidecar_suffix() -> str:
    from mimicwarehouse.disclose import SIDECAR_SUFFIX

    return SIDECAR_SUFFIX


def promote_case_study(*, settings: Settings | None = None, k: int | None = None) -> Path:
    """Check the hand-authored case study and write its sidecar in place."""
    from mimicwarehouse import disclose
    from mimicwarehouse.run import git_sha

    settings = settings or get_settings()
    resolved_k = k if k is not None else settings.k_suppression
    path = case_study_path()
    result = disclose.check(path, k=resolved_k)
    if not result.passed:
        codes = ", ".join(f"{f.code} ({f.where})" for f in result.findings if f.status == "fail")
        raise CapstoneError(f"{path.name} refused by the disclosure gate: {codes}")
    disclose.write_sidecar(path, result, resolved_k, git_sha=git_sha())
    return path


# ---------------------------------------------------------------------------
# The EP-31 tracer report (EP-33 amendment 1)
# ---------------------------------------------------------------------------

TRACER_JSON_FILES: tuple[str, ...] = ("attrition.json", "descriptives.json", "model.json")
TRACER_TITLE = "Tracer bullet: first ICU stay of adult patients -> in-hospital mortality (P2)"
TRACER_READER_GUIDES: tuple[str, str] = (
    "*Reader guide (DS/ML):* the first end-to-end analysis through the safe-query gate "
    "- cohort attrition, k-suppressed descriptives and a logistic regression with robust "
    "errors, every number audited and reproducible from a run id; the zero-cell handling "
    "is the interesting engineering.",
    "*Reader guide (clinical informatics):* admission characteristics associated with "
    "in-hospital death on a first ICU stay - an association in retrospective, "
    "de-identified data with shifted dates and a capped age; not a risk score and not a "
    "causal claim.",
)


def _tracer_json(source: Path, name: str) -> Any:
    path = source / name
    if not path.is_file():
        raise CapstoneError(f"{source.name}/{name} is missing (mwh tracer --tier <t> writes it)")
    return json.loads(path.read_text(encoding="utf-8"))


def suppress_tracer_payloads(
    att: Sequence[Mapping[str, Any]],
    desc: Mapping[str, Any],
    model: Mapping[str, Any],
    k: int,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """The EP-43 rules applied to the tracer's JSON payloads before promotion (the
    tracer itself withholds rows through the safe-query hook; what remains derivable
    across published totals is settled here): the attrition chain through
    ``disclose.suppress(mode="chain")`` (small drops withheld, both neighbours banded
    ``~n``), each descriptive table through table mode (margins, the ``n`` / ``deaths``
    nested pair), and the model's ``n`` / ``n_fit`` and ``n_events`` / ``n_events_fit``
    pairs (amendment b): the fitted total is withheld when its difference from the
    cohort total lies in ``1..k-1``. Returns new payloads; the inputs are untouched."""
    import polars as pl

    from mimicwarehouse.disclose import suppress

    steps = [dict(row) for row in att]
    if steps and all(isinstance(row.get("n"), int) for row in steps):
        frame = pl.DataFrame(
            {"step": [str(r["step"]) for r in steps], "n": [int(r["n"]) for r in steps]}
        )
        chained, _ = suppress(frame, k=k, count_cols=["n"], mode="chain")
        for row, out in zip(steps, chained.to_dicts(), strict=True):
            row["n"] = out["n"]
            row["n_suppressed"] = bool(out["n_suppressed"])
            row["n_banded"] = bool(out["n_banded"])
    tables = {key: dict(value) for key, value in desc.items() if isinstance(value, dict)}
    for name, table in tables.items():
        rows = [dict(r) for r in table.get("rows", [])]
        if not rows:
            continue
        labels = [c for c in rows[0] if c not in ("n", "n_deaths")]
        frame = pl.DataFrame(rows)
        out, _ = suppress(
            frame, k=k, count_cols=["n", "n_deaths"], group_cols=labels, complementary=True
        )
        table["rows"] = out.to_dicts()
        tables[name] = table
    desc_out = {**dict(desc), **tables}
    model_out = dict(model)
    for total, fitted in (("n", "n_fit"), ("n_events", "n_events_fit")):
        a, b = model_out.get(total), model_out.get(fitted)
        if isinstance(a, int) and isinstance(b, int) and 0 < a - b < k:
            model_out[fitted] = None
            model_out[f"{fitted}_suppressed"] = True
    if isinstance(model_out.get("n_excluded"), int) and 0 < model_out["n_excluded"] < k:
        model_out["n_excluded"] = None
        model_out["n_excluded_suppressed"] = True
    return steps, desc_out, model_out


def _publish_text(dst: Path, text: str, k: int, *, git_sha: str | None) -> Path:
    """Write ``text`` to ``dst``, gate it, sidecar it — or remove it and refuse."""
    from mimicwarehouse import disclose, fsio

    dst.parent.mkdir(parents=True, exist_ok=True)
    fsio.atomic_write_text(dst, text)
    result = disclose.check(dst, k=k)
    if not result.passed:
        dst.unlink(missing_ok=True)
        codes = ", ".join(f"{f.code} ({f.where})" for f in result.findings if f.status == "fail")
        raise CapstoneError(f"{dst.name} refused by the disclosure gate, not promoted: {codes}")
    disclose.write_sidecar(dst, result, k, git_sha=git_sha)
    return dst


def promote_tracer(
    stamp: str,
    *,
    settings: Settings | None = None,
    k: int | None = None,
    dest_md: Path | str | None = None,
    dest_dir: Path | str | None = None,
    source_dir: Path | str | None = None,
) -> list[Path]:
    """Promote the tracer run folder ``runs/tracer/<stamp>/`` (EP-31; EP-33 amendment 1):
    the report body **re-rendered** by ``tracer.render_report`` from the folder's JSON
    payloads after :func:`suppress_tracer_payloads` (chain mode over the attrition,
    table mode over the descriptives, the n-vs-n_fit rule of EP-43 amendment b), under a
    case-study header (claim type, the retrospective sentence, reader guides, the run
    and ledger ids), plus the EP-35 reproduction block of the ledger run when the folder
    records one (``manifest.json`` ``ledger_run_id``, the EP-35 retrofit); the three
    suppressed JSON payloads go into ``dest_dir``. Every file passes ``disclose.check``
    and gets its sidecar, or nothing is written."""
    from mimicwarehouse import run as run_mod
    from mimicwarehouse import tracer

    settings = settings or get_settings()
    resolved_k = k if k is not None else settings.k_suppression
    if not TRACER_STAMP_RE.match(stamp):
        raise CapstoneError(f"{stamp!r} is not a tracer run stamp (yyyymmddThhmmss-<tier>)")
    source = (
        Path(source_dir) if source_dir is not None else tracer.runs_tracer_dir(settings) / stamp
    )
    if not (source / "report.md").is_file():
        raise CapstoneError(f"no tracer report under runs/tracer/{stamp} (mwh tracer --tier <t>)")
    manifest = _tracer_json(source, "manifest.json")
    att_raw = _tracer_json(source, "attrition.json").get("steps", [])
    desc_raw = _tracer_json(source, "descriptives.json")
    model_raw = _tracer_json(source, "model.json")
    tier = str(manifest.get("tier") or stamp.rsplit("-", 1)[-1])
    ledger_run_id = manifest.get("ledger_run_id")
    desc_k = int(desc_raw.get("k") or resolved_k)
    att, desc, model = suppress_tracer_payloads(
        att_raw, desc_raw, model_raw, max(resolved_k, desc_k)
    )
    desc["k"] = max(resolved_k, desc_k)
    rendered = tracer.render_report(
        run_id=stamp,
        tier=tier,
        snapshot_id=manifest.get("core_snapshot_id"),
        att=att,
        desc=desc,
        model=model,
        manifest=manifest,
    ).splitlines()
    try:
        start = next(i for i, line in enumerate(rendered) if line.startswith("## Question"))
    except StopIteration as exc:  # pragma: no cover - the renderer always writes it
        raise CapstoneError("the tracer report has no '## Question' section") from exc
    body = rendered[start:]
    from mimicwarehouse.tracer import CLAIM_TYPE as TRACER_CLAIM_TYPE

    ledger_note = (
        f"ledger run `{ledger_run_id}` (EP-35)"
        if ledger_run_id
        else "no ledger run (the folder predates the EP-35 retrofit)"
    )
    header = [
        f"# 02 - {TRACER_TITLE}",
        "",
        f"> Promoted at EP-53 from the EP-31 run folder `runs/tracer/{stamp}/` through the",
        "> disclosure gate (`mwh disclose check --write-sidecar`, EP-43): the body is re-rendered",
        "> from the run's JSON aggregates after chain-mode suppression of the attrition,",
        "> table-mode suppression of the descriptives and the n-vs-n_fit rule (EP-43 amendment",
        "> b), so a `~n` total or a `suppressed` cell here may be exact in the run folder. The",
        "> sidecar sits beside this file and beside every JSON aggregate in the same-named folder.",
        f"> Regenerate with `mwh tracer --tier {tier} --background --job tracer-{tier}` and",
        "> `python -m mimicwarehouse.analyses.c01_concepts_qc promote-tracer --run <stamp>`.",
        "",
        f"**Claim type: {TRACER_CLAIM_TYPE}.** {RETROSPECTIVE_SENTENCE}",
        "",
        *TRACER_READER_GUIDES,
        "",
        f"Run `{stamp}` - tier `{tier}` (EP-31); {ledger_note}. The JSON aggregates the report",
        f"renders from are in [{TRACER_SLUG}/]({TRACER_SLUG}/): "
        + ", ".join(f"[{name}]({TRACER_SLUG}/{name})" for name in TRACER_JSON_FILES)
        + ".",
        "",
    ]
    tail: list[str] = []
    if ledger_run_id:
        try:
            block = run_mod.reproduction_block(str(ledger_run_id), settings)
        except run_mod.RunLedgerError as exc:
            block = f"## Reproduction (run ledger)\n\nRun `{ledger_run_id}`: {exc}.\n"
        block = block.replace("## Reproduction", "## Reproduction (run ledger, EP-35)", 1)
        block = block.replace("## Provenance", "## Provenance (run ledger, EP-35)", 1)
        tail = ["", block.rstrip("\n"), ""]
    text = "\n".join([*header, *body, *tail]).rstrip("\n") + "\n"
    md_target = Path(dest_md) if dest_md is not None else tracer_case_study_path()
    dir_target = Path(dest_dir) if dest_dir is not None else tracer_artefact_dir()
    sha = run_mod.git_sha()
    out = [_publish_text(md_target, text, resolved_k, git_sha=sha)]
    payloads: dict[str, Any] = {
        "attrition.json": {"steps": att},
        "descriptives.json": desc,
        "model.json": model,
    }
    for name in TRACER_JSON_FILES:
        text = json.dumps(payloads[name], indent=2, sort_keys=True) + "\n"
        out.append(_publish_text(dir_target / name, text, resolved_k, git_sha=sha))
    return out


# ---------------------------------------------------------------------------
# The case study's headline numbers and link checks (test_ep53)
# ---------------------------------------------------------------------------


def _parse_md_cell(cell: str) -> Any:
    """The typed value of one rendered Markdown cell (the inverse of :func:`_md_cell`):
    ``-`` -> None, ``yes`` / ``no`` -> bool, ``1,234`` -> int, ``0.437`` -> float, else
    the text; a suppressed cell (``<11``) is handled by the caller."""
    text = cell.strip()
    if text in ("", "-"):
        return None
    if text in ("yes", "no"):
        return text == "yes"
    compact = text.replace(",", "")
    if re.fullmatch(r"-?\d+", compact):
        return int(compact)
    if re.fullmatch(r"-?\d+\.\d+", compact):
        return float(compact)
    return text


def md_table_frame(source: Path | str) -> polars.DataFrame:
    """A promoted Markdown table (:func:`render_table`'s output, or a file holding one)
    back as a typed Polars frame: a column whose cells are all integers reads as
    ``Int64``, floats as ``Float64``, ``yes`` / ``no`` as ``Boolean``, else ``String``;
    a ``<k`` cell becomes null with ``<column>_suppressed = true`` (the marker column is
    added only where some cell was hidden)."""
    import polars as pl

    from mimicwarehouse.disclose import MARKER_SUFFIX, SUPPRESSED_CELL_RE

    text = Path(source).read_text(encoding="utf-8") if Path(str(source)).is_file() else str(source)
    rows: list[list[str]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if all(re.fullmatch(r":?-{3,}:?", c) for c in cells):
            continue
        rows.append(cells)
    if not rows:
        raise CapstoneError("no Markdown table found")
    header, body = [_MD_HEADERS_BACK.get(h, h) for h in rows[0]], rows[1:]
    columns: dict[str, list[Any]] = {}
    for j, name in enumerate(header):
        raw = [r[j] if j < len(r) else "" for r in body]
        hidden = [bool(c.startswith("<") and SUPPRESSED_CELL_RE.match(c)) for c in raw]
        values = [None if h else _parse_md_cell(c) for c, h in zip(raw, hidden, strict=True)]
        columns[name] = values
        if any(hidden):
            columns[f"{name}{MARKER_SUFFIX}"] = hidden
    frame_columns: list[pl.Series] = []
    for name, values in columns.items():
        present = [v for v in values if v is not None]
        is_marker = name.endswith(MARKER_SUFFIX)
        if is_marker or (present and all(isinstance(v, bool) for v in present)):
            frame_columns.append(pl.Series(name, values, dtype=pl.Boolean))
        elif present and all(isinstance(v, int) and not isinstance(v, bool) for v in present):
            frame_columns.append(pl.Series(name, values, dtype=pl.Int64))
        elif present and all(
            isinstance(v, int | float) and not isinstance(v, bool) for v in present
        ):
            frame_columns.append(
                pl.Series(name, [None if v is None else float(v) for v in values], dtype=pl.Float64)
            )
        else:
            frame_columns.append(
                pl.Series(name, [None if v is None else str(v) for v in values], dtype=pl.String)
            )
    return pl.DataFrame(frame_columns)


def load_table(directory: Path | str, name: str) -> polars.DataFrame:
    """Table ``name`` under ``directory``: the run folder's ``<name>.csv`` when present,
    else the promoted ``<name>.md`` parsed by :func:`md_table_frame`."""
    import polars as pl

    folder = Path(directory)
    csv_path = folder / f"{name}.csv"
    if csv_path.is_file():
        return pl.read_csv(csv_path)
    md_path = folder / f"{name}.md"
    if md_path.is_file():
        return md_table_frame(md_path)
    raise CapstoneError(f"no {name} table (CSV or Markdown) under {folder}")


def headline_numbers(table_dir: Path | str | None = None) -> dict[str, dict[str, Any]]:
    """``{phenotype: {n_positive, n_units, share, unit}}`` for :data:`FIGURE_PHENOTYPES`
    (scope ``all``) from the ``phenotype_prevalence`` table under ``table_dir`` (a run's
    ``tables/`` CSV or the promoted Markdown; default :func:`artefact_dir`) — what the
    case study quotes and what ``test_ep53`` re-checks. ``share`` is recomputed from the
    two released counts (the CSV's share is the same quotient)."""
    import polars as pl

    frame = load_table(
        Path(table_dir) if table_dir is not None else artefact_dir(), "phenotype_prevalence"
    )
    out: dict[str, dict[str, Any]] = {}
    for row in frame.filter(pl.col("scope") == "all").to_dicts():
        pid = str(row["phenotype"])
        if pid not in FIGURE_PHENOTYPES:
            continue
        n_pos, n_all = row.get("n_positive"), row.get("n_units")
        share = float(n_pos) / float(n_all) if n_pos is not None and n_all else None
        out[pid] = {
            "n_positive": None if n_pos is None else int(n_pos),
            "n_units": None if n_all is None else int(n_all),
            "share": share,
            "unit": row.get("unit"),
        }
    return out


def render_headline_block(numbers: Mapping[str, Mapping[str, Any]]) -> str:
    """The Markdown table between :data:`HEADLINE_MARK` (integers via ``fmt_int``)."""
    from mimicwarehouse.disclose import render_cell
    from mimicwarehouse.inventory import fmt_int

    lines = [
        "| phenotype | positive / units | prevalence |",
        "|---|---|---|",
    ]
    for pid in FIGURE_PHENOTYPES:
        row = numbers.get(pid)
        if row is None:
            continue
        n_pos, n_all = row.get("n_positive"), row.get("n_units")
        pos = render_cell(None) if n_pos is None else fmt_int(int(n_pos))
        units = render_cell(None) if n_all is None else fmt_int(int(n_all))
        share = row.get("share")
        pct = "-" if share is None else f"{float(share) * 100:.1f} %"
        lines.append(f"| {pid} ({row.get('unit') or 'unit'}) | {pos} / {units} | {pct} |")
    return "\n".join(lines) + "\n"


def parse_headline_block(text: str) -> dict[str, dict[str, Any]]:
    """The inverse of :func:`render_headline_block` over a case-study text."""
    begin, end = HEADLINE_MARK
    if begin not in text or end not in text:
        raise CapstoneError("no headline block markers in the case study")
    block = text.split(begin, 1)[1].split(end, 1)[0]
    out: dict[str, dict[str, Any]] = {}
    for line in block.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != 3 or cells[0] in ("phenotype", "---") or cells[0].startswith("---"):
            continue
        pid = cells[0].split(" (", 1)[0]
        pos_text, units_text = (c.strip() for c in cells[1].split("/", 1))
        pct_text = cells[2].removesuffix("%").strip()
        out[pid] = {
            "n_positive": None if pos_text.startswith("<") else int(pos_text.replace(",", "")),
            "n_units": None if units_text.startswith("<") else int(units_text.replace(",", "")),
            "share_pct": None if pct_text == "-" else float(pct_text),
        }
    return out


#: Integer columns that are codes or levels, not counts: rendered as plain digits.
CODE_COLUMNS: frozenset[str] = frozenset({"itemid", "level", "version"})
#: Columns that count checks or unit strings (never people) and legitimately hold small
#: integers: rendered under a ``# ...`` header, which the Markdown gate exempts from the
#: small-cell scan (the EP-44 report's ``# fail`` convention); :func:`md_table_frame`
#: maps the header back.
MD_HEADERS: dict[str, str] = {
    "checks_pass": "# pass",
    "checks_warn": "# warn",
    "checks_fail": "# fail",
    "unit_strings": "# unit strings",
    "hidden_unit_strings": "# hidden unit strings",
}
_MD_HEADERS_BACK: dict[str, str] = {v: k for k, v in MD_HEADERS.items()}


def _md_cell(value: Any, *, hidden: bool, k: int, column: str = "") -> str:
    """One Markdown cell: ``<k`` for a hidden count, ``-`` for None, integers through
    ``fmt_int`` (codes and levels as plain digits), floats with three decimals,
    booleans as yes / no, text with ``|`` escaped."""
    from mimicwarehouse.disclose import render_cell
    from mimicwarehouse.inventory import fmt_int

    if hidden:
        return render_cell(None, k)
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int):
        return str(value) if column in CODE_COLUMNS else fmt_int(value)
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value).replace("|", "/").replace("\n", " ")


def render_table(path: Path | str, *, k: int | None = None, limit: int | None = None) -> str:
    """One run-folder CSV as a Markdown table: marker columns (``*_suppressed``) fold into
    ``<k`` cells and are dropped; at most ``limit`` rows (all when None). A ``.md`` path
    (an already promoted table) is returned as it is."""
    import polars as pl

    from mimicwarehouse.disclose import MARKER_SUFFIX

    resolved_k = k if k is not None else get_settings().k_suppression
    if str(path).endswith(".md"):
        return Path(path).read_text(encoding="utf-8")
    frame = pl.read_csv(path)
    shown = [c for c in frame.columns if not c.endswith(MARKER_SUFFIX)]
    headers = [MD_HEADERS.get(c, c) for c in shown]
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(shown)]
    rows = frame.to_dicts() if limit is None else frame.head(limit).to_dicts()
    for row in rows:
        cells = [
            _md_cell(
                row.get(c), hidden=bool(row.get(f"{c}{MARKER_SUFFIX}")), k=resolved_k, column=c
            )
            for c in shown
        ]
        lines.append("| " + " | ".join(cells) + " |")
    if limit is not None and frame.height > limit:
        lines.append(f"\n*{frame.height - limit} more row(s) in the CSV.*")
    return "\n".join(lines) + "\n"


def render_tables(
    csv_dir: Path | str | None = None, *, k: int | None = None, limit: int | None = None
) -> str:
    """Every :data:`TABLES` CSV under ``csv_dir`` (default :func:`artefact_dir`) as
    Markdown sections — what the case study's tables are pasted from and what a session
    may print (k-suppressed aggregates, integers through ``fmt_int``)."""
    directory = Path(csv_dir) if csv_dir is not None else artefact_dir()
    parts: list[str] = []
    for name in TABLES:
        path = directory / f"{name}.csv"
        if not path.is_file():
            path = directory / f"{name}.md"
        if not path.is_file():
            continue
        parts.append(f"### {name}\n\n{render_table(path, k=k, limit=limit)}")
    return "\n".join(parts)


def render_summary(csv_dir: Path | str | None = None) -> str:
    """The derived figures the case-study narrative quotes, computed from the promoted
    CSVs (released counts only): concept wall time and rows per group, the slowest
    concepts, the patched ones, the pin-match count and ratio range, the QC totals,
    the flagged unit items, the implausible / ordering warnings, the teaser's hidden
    cells and the 2x2 — as Markdown, integers through ``fmt_int``."""
    import polars as pl

    from mimicwarehouse.inventory import fmt_int

    directory = Path(csv_dir) if csv_dir is not None else artefact_dir()

    def load(name: str) -> pl.DataFrame:
        return load_table(directory, name)

    def num(value: Any) -> str:
        return "-" if value is None else fmt_int(int(value))

    def fnum(value: Any) -> float:
        return float(value) if isinstance(value, int | float) else 0.0

    lines: list[str] = ["## Summary of the promoted tables", ""]
    inv = load("concept_inventory")
    row_cols = [c for c in inv.columns if c.startswith("n_rows_") and not c.endswith("_suppressed")]
    groups = (
        inv.group_by("concept_group", maintain_order=True)
        .agg(
            pl.len().alias("concepts"),
            pl.col("wall_s").sum().alias("wall_s"),
            pl.col("wall_s").max().alias("max_wall_s"),
            pl.col("peak_rss_mb").max().alias("max_rss_mb"),
            *[pl.col(c).sum().alias(c) for c in row_cols],
        )
        .sort("wall_s", descending=True)
    )
    lines += [
        "### Concept groups (wall time from the benchmark ledger; rows summed over the group)",
        "",
        "| group | # concepts | wall s | slowest concept s | max peak RSS MB | "
        + " | ".join(row_cols)
        + " |",
        "|---|---|---|---|---|" + "---|" * len(row_cols),
    ]
    for g in groups.to_dicts():
        lines.append(
            f"| {g['concept_group']} | {num(g['concepts'])} | {g['wall_s']:.1f} | "
            f"{g['max_wall_s']:.1f} | {num(round(g['max_rss_mb'] or 0))} | "
            + " | ".join(num(g[c]) for c in row_cols)
            + " |"
        )
    total_wall = float(inv.get_column("wall_s").sum())
    lines += [
        "",
        f"- concepts: {num(inv.height)} in {num(inv.get_column('concept_group').n_unique())} "
        f"groups; status done: {num(int((inv.get_column('status') == 'done').sum()))}; "
        f"total wall {total_wall:.1f} s; max peak RSS "
        f"{num(round(fnum(inv.get_column('peak_rss_mb').max())))} MB.",
        "- rows: " + "; ".join(f"{c} {num(inv.get_column(c).sum())}" for c in row_cols) + ".",
        "- patched: "
        + ", ".join(
            f"{r['concept']} ({r['patch_id']})"
            for r in inv.filter(pl.col("patch_id").is_not_null()).to_dicts()
        )
        + ".",
        "- slowest: "
        + ", ".join(
            f"{r['concept']} {r['wall_s']:.1f} s / {num(round(r['peak_rss_mb'] or 0))} MB"
            for r in inv.sort("wall_s", descending=True).head(6).to_dicts()
        )
        + ".",
    ]
    pins = load("demo_pins_vs_tier")
    # an all-null boolean column reads back from CSV as text: cast before counting
    matches = pins.get_column("pin_matches").cast(pl.Boolean, strict=False).fill_null(False)
    ratios = pins.get_column("tier_to_demo_ratio").cast(pl.Float64, strict=False).drop_nulls()
    ranked = pins.filter(pl.col("tier_to_demo_ratio").is_not_null())
    lo = ranked.sort("tier_to_demo_ratio").head(3).to_dicts()
    hi = ranked.sort("tier_to_demo_ratio", descending=True).head(3).to_dicts()
    lines += [
        "",
        "### Demo pins",
        "",
        f"- pin matches the live demo count: {num(int(matches.sum()))} of {num(pins.height)}; "
        f"ratio tier / demo pin over {num(ratios.len())} concepts: min {fnum(ratios.min()):.1f}, "
        f"median {fnum(ratios.median()):.1f}, max {fnum(ratios.max()):.1f}.",
        "- lowest ratios: "
        + ", ".join(f"{r['concept']} {r['tier_to_demo_ratio']:.1f}" for r in lo)
        + "; highest: "
        + ", ".join(f"{r['concept']} {r['tier_to_demo_ratio']:.1f}" for r in hi)
        + ".",
    ]
    qc = load("qc_status_by_table")
    lines += [
        "",
        "### QC",
        "",
        f"- checks: pass {num(qc.get_column('checks_pass').sum())} / warn "
        f"{num(qc.get_column('checks_warn').sum())} / fail "
        f"{num(qc.get_column('checks_fail').sum())} over {num(qc.height)} tables; tables with a "
        f"warning: {num(int((qc.get_column('worst_status') == 'warn').sum()))}; with a failure: "
        f"{num(int((qc.get_column('worst_status') == 'fail').sum()))}.",
    ]
    variants = load("unit_variants")
    flagged = variants.filter(pl.col("flagged"))
    lines.append(
        f"- unit variants: {num(flagged.height)} of {num(variants.height)} curated items flagged: "
        + ", ".join(
            f"{r['itemid']} {r['label']} ({num(r['unit_strings'])} strings, dominant "
            f"{r['dominant_unit']} {float(r['dominant_share'] or 0):.3f})"
            for r in flagged.to_dicts()
        )
        + "."
    )
    imp = load("implausible_values")
    warned = imp.filter(pl.col("status") != "pass")
    lines.append(
        "- implausible values: "
        + (
            ", ".join(
                f"{r['itemid']} {r['label']} share {float(r['implausible_share'] or 0):.3f} "
                "(n_affected "
                f"{num(r['n_affected']) if not r.get('n_affected_suppressed') else '<k'})"
                for r in warned.to_dicts()
            )
            or "no warning"
        )
        + f"; highest share {fnum(imp.get_column('implausible_share').max()):.3f}."
    )
    ordering = load("timestamp_ordering")
    lines.append(
        "- timestamp ordering warnings: "
        + ", ".join(
            f"{r['table']} {r['check_id']} "
            + (
                f"{float(r['violation_share']) * 100:.1f} %"
                if r["violation_share"] is not None
                else "share hidden"
            )
            for r in ordering.filter(pl.col("status") != "pass").to_dicts()
        )
        + "."
    )
    teaser = load("measurement_first24h_by_era")
    teaser_marker = "n_stays_measured_first_24h_suppressed"
    hidden = (
        teaser.filter(pl.col(teaser_marker)) if teaser_marker in teaser.columns else teaser.clear()
    )
    lines += [
        "",
        "### Measurement teaser",
        "",
        f"- {num(teaser.get_column('itemid').n_unique())} items x "
        f"{num(teaser.get_column('era').n_unique())} eras; hidden measured counts: "
        f"{num(hidden.height)} ("
        + ", ".join(f"{r['label']} {r['era']}" for r in hidden.to_dicts())
        + ").",
    ]
    agreement = load("sepsis_agreement_2x2")
    lines += [
        "",
        "### Sepsis 2x2",
        "",
        "- "
        + "; ".join(
            f"{r['cell']} "
            f"{num(r['n_admissions']) if not r.get('n_admissions_suppressed') else '<k'}"
            + (f" ({float(r['share']) * 100:.1f} %)" if r["share"] is not None else "")
            for r in agreement.to_dicts()
        )
        + ".",
    ]
    prevalence = load("phenotype_prevalence")
    lines += ["", "### Prevalence (share, Wilson interval)", ""]
    for r in prevalence.to_dicts():
        if r["share"] is None:
            lines.append(f"- {r['phenotype']} {r['scope']}: hidden")
        else:
            lines.append(
                f"- {r['phenotype']} {r['scope']} ({r['unit']}): {float(r['share']) * 100:.1f} % "
                f"[{float(r['ci_low']) * 100:.1f}, {float(r['ci_high']) * 100:.1f}] of "
                f"{num(r['n_units'])}"
            )
    return "\n".join(lines) + "\n"


def parse_run_id(text: str) -> str | None:
    """The report run id of a case study's Reproduction block, or None."""
    match = RUN_LINE_RE.search(text)
    return match.group("run_id") if match else None


def relative_links(text: str) -> list[str]:
    """Every relative Markdown link target in ``text`` (anchors stripped)."""
    return [m.group(1).split("#", 1)[0] for m in LINK_RE.finditer(text) if m.group(1)]


# ---------------------------------------------------------------------------
# python -m mimicwarehouse.analyses.c01_concepts_qc
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    """``build --tier T`` · ``promote --run RUN_ID [--dest DIR]`` · ``promote-tracer --run
    STAMP`` · ``check-doc`` (sidecar for the case study) · ``headline [--dir DIR]``.
    Prints run ids and paths only."""
    from mimicwarehouse.console import EXIT_FINDINGS, EXIT_OK, EXIT_USAGE
    from mimicwarehouse.run import RunLedgerError

    parser = argparse.ArgumentParser(prog="python -m mimicwarehouse.analyses.c01_concepts_qc")
    sub = parser.add_subparsers(dest="command", required=True)
    build_p = sub.add_parser("build", help="run the capstone on a tier (fixture / dev foreground)")
    build_p.add_argument("--tier", default="full")
    promote_p = sub.add_parser("promote", help="copy a run's tables + figures into docs/analyses")
    promote_p.add_argument("--run", required=True, help="the c01_concepts_qc report run id")
    promote_p.add_argument("--dest", default=None)
    tracer_p = sub.add_parser("promote-tracer", help="promote the EP-31 tracer run folder")
    tracer_p.add_argument("--run", required=True, help="runs/tracer/<stamp> (yyyymmddThhmmss-tier)")
    sub.add_parser("check-doc", help="check the case study and write its sidecar")
    headline_p = sub.add_parser("headline", help="print the headline block from the promoted CSVs")
    headline_p.add_argument("--dir", default=None)
    tables_p = sub.add_parser("tables", help="print the promoted CSVs as Markdown tables")
    tables_p.add_argument("--dir", default=None)
    tables_p.add_argument("--limit", type=int, default=None)
    summary_p = sub.add_parser("summary", help="print the derived figures the case study quotes")
    summary_p.add_argument("--dir", default=None)
    args = parser.parse_args(list(argv) if argv is not None else None)
    from mimicwarehouse.console import console_safe

    try:
        if args.command == "build":
            print(build(args.tier))
        elif args.command == "promote":
            for path in promote(args.run, args.dest):
                print(path)
        elif args.command == "promote-tracer":
            for path in promote_tracer(args.run):
                print(path)
        elif args.command == "check-doc":
            print(promote_case_study())
        elif args.command == "headline":
            sys.stdout.write(console_safe(render_headline_block(headline_numbers(args.dir))))
        elif args.command == "tables":
            sys.stdout.write(console_safe(render_tables(args.dir, limit=args.limit)))
        elif args.command == "summary":
            sys.stdout.write(console_safe(render_summary(args.dir)))
    except CapstoneError as exc:
        print(f"c01_concepts_qc: {exc}", file=sys.stderr)
        return EXIT_FINDINGS
    except (FileNotFoundError, ValueError, RunLedgerError) as exc:
        print(f"c01_concepts_qc: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_USAGE
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "ACTOR",
    "AGREEMENT_PAIR",
    "CASE_STUDY_SLUG",
    "CI_LEVEL",
    "CLAIM_TYPE",
    "DAG_TAG",
    "FIGURES",
    "FIGURE_PHENOTYPES",
    "HEADLINE_MARK",
    "INVENTORY_TIERS",
    "KDIGO_LEVELS",
    "KDIGO_STAGE_COLUMN",
    "MD_HEADERS",
    "MP_ITEMIDS",
    "PHENOTYPE_IDS",
    "RETROSPECTIVE_SENTENCE",
    "RUN_KIND",
    "RUN_NAME",
    "STEP_NAME",
    "TABLES",
    "TOP_CHECKS",
    "TRACER_JSON_FILES",
    "TRACER_SLUG",
    "CapstoneError",
    "analyses_dir",
    "artefact_dir",
    "build",
    "case_study_path",
    "concept_wall_chart",
    "headline_numbers",
    "inventory_tiers",
    "load_table",
    "main",
    "md_table_frame",
    "parse_headline_block",
    "parse_run_id",
    "prevalence_chart",
    "promote",
    "promote_case_study",
    "promote_tracer",
    "relative_links",
    "render_headline_block",
    "render_summary",
    "render_table",
    "render_tables",
    "run_step",
    "suppress_tracer_payloads",
    "to_png",
    "tracer_artefact_dir",
    "tracer_case_study_path",
    "wilson_interval",
]
