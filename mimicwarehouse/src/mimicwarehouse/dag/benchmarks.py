"""Benchmark ledger — build telemetry, append-only JSONL (EP-19 item 4; D-24).

``runs/benchmarks.jsonl``: one canonical JSON object per line, written through
:func:`mimicwarehouse.fsio.append_jsonl` (``O_APPEND`` + short-write check + **fsync**;
EP-33 B8). Two writers exist: the EP-19 runner appends one :class:`BenchmarkLine` per
executed step (``phase: total``; a large partitioned stage adds a ``pass1`` line —
absent when a resume skipped pass 1 — and a ``pass2`` line before it, EP-23) plus one
``kind: build`` summary line per run, and the EP-28 full-tier verify test appends
``kind: verify`` lines. Appends are **not** atomic between processes on Windows (the CRT
implements ``O_APPEND`` as seek-then-write, DAG-4): the ledger relies on
single-writer-by-sequencing — builds are serialized by the build lock and a verify run
follows the build it verifies — not on OS-level locking. :func:`read` tolerates one torn
trailing line (LGR-1). EP-28/EP-32 read it. Distinct from the analysis run ledger
(``runs/ledger.jsonl``, EP-35).

EP-35 formalises :class:`BenchmarkLine` as **the** benchmark-ledger schema (ledger P3C-6:
no second model): it gains the optional ``run_id`` (the analysis run a line belongs to)
and ``disk_delta_mb`` (default ``None``, so every older line still validates), and the
``kind`` vocabulary is :data:`BENCHMARK_KINDS` — ``stage`` / ``build`` (the runner),
``verify`` (EP-28), ``concept`` / ``mart`` / ``query`` / ``page`` / ``bench`` (EP-35+;
written through :func:`mimicwarehouse.run.bench`, which builds a line and calls
:func:`append` — this module stays the only writer). ``kind`` stays a plain ``str`` so a
later brief can add a value without a schema change. ``mwh runs benchmarks [--kind]``
(EP-32) and the ``runs.benchmarks`` view (``mwh runs refresh``, EP-35) read it.

EP-32 adds the case-study renderer over :func:`summarize`: :func:`render_markdown`
(one Markdown row per step, integers via ``inventory.fmt_int`` — guard G4) and
:func:`replace_marked_block` (swap the block between the ``benchmarks:begin`` /
``benchmarks:end`` HTML comments in a doc, leaving the narrative untouched), both
driven by ``mwh runs benchmarks`` (:mod:`mimicwarehouse.runs_cli`).

Everything recorded is counts, versions and timings — never a row.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field

from mimicwarehouse import fsio
from mimicwarehouse.config import Settings, get_settings

if TYPE_CHECKING:  # pragma: no cover
    import polars

BENCHMARKS_FILENAME = "benchmarks.jsonl"

Phase = Literal["pass1", "pass2", "total"]

#: The ``kind`` vocabulary (EP-35 item 2): the runner's step kinds (``dag.spec.Kind`` —
#: ``stage`` / ``sql`` / ``python`` / ``catalog``) and its per-run ``build`` summary,
#: EP-28's full-tier ``verify`` lines, and the analysis-side kinds ``run.bench`` writes.
BENCHMARK_KINDS: tuple[str, ...] = (
    "stage",
    "sql",
    "python",
    "catalog",
    "build",
    "verify",
    "concept",
    "mart",
    "query",
    "page",
    "bench",
)


class HostInfo(BaseModel):
    """The host facts a benchmark line carries (cpu count, RAM GB)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    cpu: int
    ram_gb: float


class BenchmarkLine(BaseModel):
    """One ledger line: a step's telemetry, or the per-run ``kind: build`` summary."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ts: str
    build_id: str
    tier: str
    step: str | None = Field(default=None, description="None on the kind: build summary line")
    kind: str
    phase: Phase = "total"
    wall_s: float
    peak_rss_mb: float | None = None
    rows: int | None = None
    bytes_in: int | None = None
    bytes_out: int | None = None
    files: int | None = None
    duckdb_version: str
    git_sha: str | None = None
    host: HostInfo
    ok: bool
    error: str | None = None
    #: EP-35: the analysis run this line belongs to (None for runner / verify lines).
    run_id: str | None = None
    #: EP-35: free-space delta of the data-root drive over the step (MB; None = unmeasured).
    disk_delta_mb: float | None = None


def host_info() -> HostInfo:
    """Current host facts via psutil (the EP-19 core dependency)."""
    import psutil

    return HostInfo(
        cpu=psutil.cpu_count(logical=True) or 0,
        ram_gb=round(psutil.virtual_memory().total / 2**30, 1),
    )


def benchmarks_path(settings: Settings | None = None) -> Path:
    settings = settings or get_settings()
    return settings.layout["runs"] / BENCHMARKS_FILENAME


def append(line: BenchmarkLine, settings: Settings | None = None) -> Path:
    """Append one line (``O_APPEND`` + fsync via :func:`fsio.append_jsonl`; one canonical
    JSON object per line)."""
    return fsio.append_jsonl(benchmarks_path(settings), line.model_dump(mode="json"))


def read(settings: Settings | None = None) -> polars.DataFrame:
    """The whole ledger as a polars DataFrame (empty frame when the file is missing or
    empty). The lines go through :func:`fsio.read_jsonl` first, so one torn trailing line
    (a crash mid-append) is skipped with a warning instead of failing every summary
    (LGR-1); polars then infers the schema from the surviving canonical lines."""
    import polars

    rows = fsio.read_jsonl(benchmarks_path(settings))
    if not rows:
        return polars.DataFrame()
    # infer over every line, not polars' first-100 default: a column that is null on the
    # early runner lines and set later (run_id / disk_delta_mb since EP-35, first seen when
    # EP-37's kind: concept lines followed them) would otherwise bind NULL-typed and fail
    # on its first value
    return polars.read_ndjson(
        b"".join(fsio.encode_line(row) for row in rows), infer_schema_length=None
    )


def summarize(
    settings: Settings | None = None,
    *,
    tier: str | None = None,
    kind: str | None = "stage",
) -> polars.DataFrame:
    """Per-step telemetry pivoted by phase — what the EP-28 completion notes and the
    EP-32 benchmark case study read (EP-28 item 5).

    One row per ``(tier, step)``: the **latest** ``phase: total`` line (newest ``ts``)
    joined with the *same build's* ``pass1`` / ``pass2`` walls (null when the step has
    no phase lines, or when a resume skipped pass 1). Columns: ``tier``, ``step``,
    ``build_id``, ``ts``, ``pass1_wall_s``, ``pass2_wall_s``, ``wall_s``,
    ``peak_rss_mb``, ``rows``, ``bytes_in``, ``bytes_out``, ``files``, ``mb_in_per_s``
    (``bytes_in / 1e6 / wall_s``), ``mb_out_per_s``, ``ok``. ``kind`` filters the step
    lines (default ``"stage"``; ``None`` keeps every kind); the per-run ``kind: build``
    summary lines (``step`` null) are always excluded. Everything here is timings and
    counts — never a row of data. The "latest" pick is deterministic: lines are ordered
    by ``(ts, build_id)`` with a stable sort, so two lines sharing a one-second ``ts``
    resolve the same way on every run (carried finding DAG-8).
    """
    import polars

    df = read(settings)
    if df.is_empty() or "step" not in df.columns:
        return polars.DataFrame()
    df = df.filter(polars.col("step").is_not_null())
    if kind is not None:
        df = df.filter(polars.col("kind") == kind)
    if tier is not None:
        df = df.filter(polars.col("tier") == tier)
    if df.is_empty():
        return polars.DataFrame()
    order = ["ts", "build_id"]
    df = df.sort(order, maintain_order=True)
    keys = ["tier", "step", "build_id"]
    totals = df.filter(polars.col("phase") == "total").group_by(keys, maintain_order=True).last()
    out = totals
    for phase in ("pass1", "pass2"):
        walls = (
            df.filter(polars.col("phase") == phase)
            .group_by(keys, maintain_order=True)
            .last()
            .select(*keys, polars.col("wall_s").alias(f"{phase}_wall_s"))
        )
        out = out.join(walls, on=keys, how="left")
    # latest total per (tier, step): the group_by above kept ts order within each build
    out = (
        out.sort(order, maintain_order=True).group_by(["tier", "step"], maintain_order=True).last()
    )
    wall = polars.col("wall_s")
    out = out.with_columns(
        polars.when(wall > 0)
        .then(polars.col("bytes_in") / 1_000_000 / wall)
        .otherwise(None)
        .round(1)
        .alias("mb_in_per_s"),
        polars.when(wall > 0)
        .then(polars.col("bytes_out") / 1_000_000 / wall)
        .otherwise(None)
        .round(1)
        .alias("mb_out_per_s"),
    )
    return out.select(
        "tier",
        "step",
        "build_id",
        "ts",
        "pass1_wall_s",
        "pass2_wall_s",
        "wall_s",
        "peak_rss_mb",
        "rows",
        "bytes_in",
        "bytes_out",
        "files",
        "mb_in_per_s",
        "mb_out_per_s",
        "ok",
    ).sort(["tier", "step"])


MARK_BEGIN = "<!-- benchmarks:begin -->"
MARK_END = "<!-- benchmarks:end -->"

#: Column order of :func:`render_markdown` (EP-32 item 2; the brief's per-table facts).
RENDER_COLUMNS = (
    "table",
    "rows",
    "CSV GB",
    "Parquet GB",
    "ratio",
    "files",
    "pass 1 s",
    "pass 2 s",
    "total s",
    "MB/s",
    "peak RSS MB",
)


def _fmt_gb(value: float | int | None) -> str:
    return "-" if value is None else f"{value / 1e9:.2f}"


def _fmt_s(value: float | None) -> str:
    return "-" if value is None else f"{value:.1f}"


def render_rows(summary: polars.DataFrame) -> list[list[str]]:
    """Formatted body + totals rows over a :func:`summarize` frame (shared by
    :func:`render_markdown` and the ``mwh runs benchmarks`` rich table).

    One row per summary line in its (tier, step) order — the ``stage.`` prefix is
    stripped from step names — then a ``total`` row: sums for rows/bytes/files/walls,
    overall CSV→Parquet ratio and MB/s, and the peak-RSS **high-water** (max, not sum).
    Integers go through :func:`mimicwarehouse.inventory.fmt_int` (guard G4); absent
    telemetry renders ``-``. Callers pre-filter tier/kind; everything here is counts
    and timings, never a row of data.
    """
    from mimicwarehouse.inventory import fmt_int

    body: list[list[str]] = []
    sums = {"rows": 0, "bytes_in": 0, "bytes_out": 0, "files": 0}
    walls = {"pass1_wall_s": 0.0, "pass2_wall_s": 0.0, "wall_s": 0.0}
    rss_max: float | None = None
    for rec in summary.to_dicts():
        bytes_in, bytes_out = rec["bytes_in"], rec["bytes_out"]
        ratio = "-" if not bytes_in or not bytes_out else f"{bytes_in / bytes_out:.1f}x"
        body.append(
            [
                str(rec["step"]).removeprefix("stage."),
                fmt_int(rec["rows"]),
                _fmt_gb(bytes_in),
                _fmt_gb(bytes_out),
                ratio,
                fmt_int(rec["files"]),
                _fmt_s(rec["pass1_wall_s"]),
                _fmt_s(rec["pass2_wall_s"]),
                _fmt_s(rec["wall_s"]),
                _fmt_s(rec["mb_in_per_s"]),
                fmt_int(None if rec["peak_rss_mb"] is None else round(rec["peak_rss_mb"])),
            ]
        )
        for key in sums:
            if rec[key] is not None:
                sums[key] += rec[key]
        for key in walls:
            if rec[key] is not None:
                walls[key] += rec[key]
        if rec["peak_rss_mb"] is not None:
            rss_max = max(rss_max or 0.0, rec["peak_rss_mb"])
    total_ratio = "-" if not sums["bytes_out"] else f"{sums['bytes_in'] / sums['bytes_out']:.1f}x"
    total_mb_s = "-" if not walls["wall_s"] else f"{sums['bytes_in'] / 1e6 / walls['wall_s']:.1f}"
    body.append(
        [
            "total",
            fmt_int(sums["rows"]),
            _fmt_gb(sums["bytes_in"]),
            _fmt_gb(sums["bytes_out"]),
            total_ratio,
            fmt_int(sums["files"]),
            _fmt_s(walls["pass1_wall_s"]),
            _fmt_s(walls["pass2_wall_s"]),
            _fmt_s(walls["wall_s"]),
            total_mb_s,
            fmt_int(None if rss_max is None else round(rss_max)),
        ]
    )
    return body


def render_markdown(summary: polars.DataFrame) -> str:
    """The :func:`summarize` frame as one Markdown table (EP-32 item 2) — the block
    ``mwh runs benchmarks --format md`` prints and ``--out`` splices between the
    :data:`MARK_BEGIN` / :data:`MARK_END` markers. Columns per :data:`RENDER_COLUMNS`;
    the last row is the totals row (peak RSS = high-water)."""
    lines = [
        "| " + " | ".join(RENDER_COLUMNS) + " |",
        "|" + "|".join("---" if i == 0 else "---:" for i in range(len(RENDER_COLUMNS))) + "|",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in render_rows(summary))
    return "\n".join(lines) + "\n"


def replace_marked_block(
    text: str, block: str, *, begin: str = MARK_BEGIN, end: str = MARK_END
) -> str:
    """``text`` with the content between the ``begin`` and ``end`` markers (default
    :data:`MARK_BEGIN` / :data:`MARK_END`; EP-34's methods page passes its own pairs)
    replaced by ``block`` — markers kept, narrative untouched, idempotent for the
    same ``block``. Raises :class:`ValueError` unless exactly one well-ordered
    marker pair exists."""
    if text.count(begin) != 1 or text.count(end) != 1:
        raise ValueError(f"the target needs exactly one {begin!r} and one {end!r} marker")
    start = text.index(begin) + len(begin)
    stop = text.index(end)
    if stop < start:
        raise ValueError(f"{end!r} precedes {begin!r} in the target")
    return text[:start] + "\n" + block.rstrip("\n") + "\n" + text[stop:]


__all__ = [
    "BENCHMARKS_FILENAME",
    "BENCHMARK_KINDS",
    "MARK_BEGIN",
    "MARK_END",
    "RENDER_COLUMNS",
    "BenchmarkLine",
    "HostInfo",
    "append",
    "benchmarks_path",
    "host_info",
    "read",
    "render_markdown",
    "render_rows",
    "replace_marked_block",
    "summarize",
]
