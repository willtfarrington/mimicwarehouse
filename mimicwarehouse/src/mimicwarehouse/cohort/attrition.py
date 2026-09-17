"""Attrition diagram renderer — Mermaid (primary), Altair / Vega-Lite (fallback), the
Markdown report and the run-folder artefacts of a built cohort's attrition chain (EP-48;
DESIGN §9 / §14 / §15; GOVERNANCE §5 / §7; D-33, D-40).

The renderer never sees a raw count: its input is the **suppressed** frame of
:func:`mimicwarehouse.cohort.build.attrition` (chain mode, EP-43, on both count columns),
whose marker columns say what to draw. Four surfaces:

* :func:`render_mermaid` — the primary form (GitHub and Streamlit render it): one node
  per step (``Step k - <name>``, the step label, ``units n = X`` / ``subjects n = Y``), a
  dotted side node per exclusion (``Excluded at <step>`` with the units / subjects
  dropped; a step that drops nothing exactly has none), a footer node (``id@version -
  tier <t> - run <id> - def_hash <12 hex> - k = 11``) pinned under the last step with an
  invisible link, deterministic ids (``s0 …``, ``x1 …``, ``f``), user text escaped with
  Mermaid entity codes (``#35;`` ``#quot;`` ``#lt;`` ``#gt;``), colours from
  :mod:`mimicwarehouse.theme`, an optional front-matter ``title``.
* :func:`render_altair` / :func:`to_vegalite` / :func:`to_png` — the fallback: a
  horizontal funnel bar of the units remaining per step, the two counts beside each bar
  and the exclusion under it; a withheld total has no bar (omitted, never zeroed); the
  EP-5 theme is applied at serialisation (``alt.theme.enable`` as a context manager, so
  nothing leaks into the process); the PNG comes from ``vl-convert-python`` (a core
  dependency since this brief).
* :func:`render_markdown` — the paste-ready report: the claim type and the
  retrospective sentence, the disclosure line, the suppressed table (the drops as
  ``n = X`` text cells, so ``disclose.check``'s prose rule verifies them and its
  nested-totals heuristic cannot read a drop column as a total), the Mermaid block, the
  spec's *what it does not claim* list and the EP-35 reproduction block.
* :func:`save_attrition` — ``runs/<run_id>/figures/attrition.{mmd,vl.json,png,csv,md}``
  (no run id or compact date in a file name, ``docs/committed-text.md`` rule 3): every
  file is rendered into a staging directory under ``<data_root>/tmp``, checked with
  :func:`mimicwarehouse.disclose.check`, and published only when **every** file passes —
  a failing check raises :class:`~mimicwarehouse.disclose.DisclosureError` and writes
  nothing. Written into the build run's own folder, the files are recorded in that
  manifest's ``figures`` map so ``mwh runs show`` names them.

**The cell forms.** ``disclose.render_cell``'s three — ``1,234``, ``<11`` (a total below
k, or a drop below k), ``~1,000`` (a total banded beside a small drop) — plus two the
chain needs. A drop withheld only because a neighbouring total is banded or hidden is
shown as the *rounded difference of the released totals* (``~20``): any reader could
compute it, so it discloses nothing, and it is far more honest than ``<11`` for a drop
that may be large. ``suppressed`` marks a total withheld by the **pair guard**:
``disclose.check`` refuses a published pair of nested totals whose difference lies in
``(0, k)`` (the EP-31 ``n`` / ``n_fit`` lesson), and a step's ``n_units - n_subjects`` is
exactly such a pair, so the subjects total — and the two drops beside it — of every step
where that difference would be small is withheld before anything is rendered. ``<k``
therefore always means a count below k, never "hidden for another reason". The
``cohort`` step — the materialisation of the last criterion's result, no predicate of its
own — excludes nothing by construction, so it carries no drop cell and no exclusion node
(chain mode may still withhold its zero drop beside a banded neighbour, and drawing that
as ``~10`` would mislead).

Import budget: not on the ``mwh`` start-up path (``cohort/cli.py`` imports this module
inside the command bodies); altair, polars, vl-convert and ``run`` load inside the
functions (``test_ep48`` pins it).
"""

from __future__ import annotations

import itertools
import json
import os
import shutil
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

from mimicwarehouse import disclose, fsio, theme
from mimicwarehouse.cohort.build import DROP_SMALL_SUFFIX, VALUE_MAX_CHARS, Attrition
from mimicwarehouse.disclose import BAND_SUFFIX, MARKER_SUFFIX, DisclosureError, render_cell

if TYPE_CHECKING:  # pragma: no cover
    import altair
    import polars

    from mimicwarehouse.config import Settings

#: The small-cell threshold the renderer assumes for a bare frame (``Attrition`` carries
#: the tier's).
K_DEFAULT = disclose.K_DEFAULT
#: The committed file names (rule 3 of ``docs/committed-text.md``: no run id, no date).
MERMAID_FILE = "attrition.mmd"
VEGALITE_FILE = "attrition.vl.json"
PNG_FILE = "attrition.png"
CSV_FILE = "attrition.csv"
MARKDOWN_FILE = "attrition.md"
ALL_FILES: tuple[str, ...] = (MERMAID_FILE, VEGALITE_FILE, PNG_FILE, CSV_FILE, MARKDOWN_FILE)
#: ``--format`` → the files it writes (``altair`` carries the PNG's source siblings).
FORMAT_FILES: dict[str, tuple[str, ...]] = {
    "mermaid": (MERMAID_FILE,),
    "altair": (VEGALITE_FILE, PNG_FILE, CSV_FILE),
    "all": ALL_FILES,
}
FORMATS: tuple[str, ...] = tuple(FORMAT_FILES)
#: Mermaid flowchart directions.
DIRECTIONS: tuple[str, ...] = ("TD", "TB", "LR", "BT", "RL")
#: The report's claim-type label and the retrospective sentence (EP-31 / EP-44 wording).
CLAIM_TYPE = "exploratory (cohort description)"
RETROSPECTIVE_SENTENCE = "MIMIC-IV analyses are retrospective."
#: The fourth cell form (module docstring); matches ``disclose.SUPPRESSED_CELL_RE``.
WITHHELD_TEXT = "suppressed"
#: The polarity of the compiler's materialisation step (``compiler.STEP_COHORT``): no
#: predicate, hence no drop cell and no exclusion node.
COHORT_POLARITY = "cohort"
#: The Vega-Lite figure's geometry and the PNG scale.
CHART_WIDTH = 480
ROW_STEP = 40
BAR_SIZE = 16
BAR_OFFSET = 7
PNG_SCALE = 2
#: Run-manifest ``figures`` keys are the file names (``run.NAME_RE`` admits dots).
MANIFEST_PREFIX = "figures/"

CellKind = Literal["exact", "banded", "small", "withheld", "approx"]

_MERMAID_ESCAPES: tuple[tuple[str, str], ...] = (
    ("#", "#35;"),  # first: the codes below carry a '#'
    ('"', "#quot;"),
    ("<", "#lt;"),
    (">", "#gt;"),
)


class AttritionRenderError(RuntimeError):
    """A usage / environment problem of the renderer (a frame without the chain columns,
    an unknown format, a mart without a run id, ``vl-convert`` missing)."""


# ---------------------------------------------------------------------------
# The diagram model: cells, steps, the chain
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Cell:
    """One released number of the chain and how it is shown: ``exact`` (the count),
    ``banded`` (chain mode rounded it to the nearest ten), ``small`` (below k — a
    ``<k`` cell), ``withheld`` (the pair guard hid it — ``suppressed``) or ``approx``
    (a drop estimated from released totals — ``~``)."""

    value: int | None
    kind: CellKind

    @property
    def released(self) -> int | None:
        """The value a reader is given as a number (exact or banded totals only)."""
        return self.value if self.kind in ("exact", "banded") else None

    @property
    def is_zero(self) -> bool:
        return self.kind == "exact" and self.value == 0

    def text(self, k: int = K_DEFAULT) -> str:
        """The rendered cell (module docstring: the five forms)."""
        if self.kind == "withheld":
            return WITHHELD_TEXT
        if self.kind == "small" or self.value is None:
            return render_cell(None, k)
        return render_cell(self.value, k, banded=self.kind in ("banded", "approx"))


@dataclass(frozen=True, slots=True)
class StepRow:
    """One step of the chain with its four cells (the first step has no drops)."""

    index: int
    step: str
    label: str
    polarity: str
    kind: str | None
    custom: bool
    units: Cell
    subjects: Cell
    dropped_units: Cell | None
    dropped_subjects: Cell | None

    @property
    def has_exclusion(self) -> bool:
        """Whether an ``Excluded`` node is drawn: anything but two exact zero drops."""
        if self.dropped_units is None or self.dropped_subjects is None:
            return False
        return not (self.dropped_units.is_zero and self.dropped_subjects.is_zero)


@dataclass(frozen=True, slots=True)
class Chain:
    """The prepared chain every renderer draws from: the rows, ``k`` and the provenance
    of the footer (``ref``, ``tier``, ``run_id``, ``def_hash``); ``guarded`` lists the
    step indexes whose subjects total the pair guard withheld."""

    rows: tuple[StepRow, ...]
    k: int
    ref: str | None = None
    tier: str | None = None
    run_id: str | None = None
    def_hash: str | None = None
    title: str | None = None
    guarded: tuple[int, ...] = ()

    def footer_parts(self) -> list[str]:
        parts: list[str] = []
        if self.ref:
            parts.append(self.ref)
        if self.tier:
            parts.append(f"tier {self.tier}")
        if self.run_id:
            parts.append(f"run {self.run_id}")
        if self.def_hash:
            parts.append(f"def_hash {self.def_hash[:12]}")
        parts.append(f"k = {self.k}")
        return parts

    def footer(self) -> str:
        return " - ".join(self.footer_parts())

    def default_title(self) -> str:
        head = f"Attrition: {self.ref}" if self.ref else "Attrition"
        return f"{head} ({self.tier})" if self.tier else head


def _truncate(text: str) -> str:
    """Labels are bounded like every run-folder string (``VALUE_MAX_CHARS``, the
    committed-text canon rule 4)."""
    text = " ".join(text.split())
    if len(text) <= VALUE_MAX_CHARS:
        return text
    return text[: VALUE_MAX_CHARS - 3] + "..."


def _rows(source: Any) -> list[dict[str, Any]]:
    """The suppressed frame as records: a polars / pandas frame or a record sequence
    (the ``rows`` of ``mwh cohort attrition --json``)."""
    if hasattr(source, "to_dicts"):
        records = list(source.to_dicts())
    elif hasattr(source, "to_dict") and hasattr(source, "columns"):
        records = list(source.to_dict("records"))
    elif isinstance(source, Sequence) and not isinstance(source, str | bytes):
        records = [dict(r) for r in source]
    else:
        raise AttritionRenderError(
            f"expected the suppressed attrition frame (polars / pandas) or its records, got "
            f"{type(source).__name__}"
        )
    if not records:
        raise AttritionRenderError("an attrition chain needs at least one step")
    missing = {"step", "n_units", "n_subjects"} - set(records[0])
    if missing:
        raise AttritionRenderError(
            f"the frame lacks the chain column(s) {', '.join(sorted(missing))} - render the "
            "frame `cohort.attrition()` returns"
        )
    return records


def _total(row: Mapping[str, Any], column: str) -> Cell:
    value = row.get(column)
    hidden = bool(row.get(f"{column}{MARKER_SUFFIX}", False))
    if hidden or value is None:
        return Cell(None, "small")
    banded = bool(row.get(f"{column}{BAND_SUFFIX}", False))
    return Cell(int(value), "banded" if banded else "exact")


def _drops(
    rows: Sequence[Mapping[str, Any]], column: str, totals: Sequence[Cell]
) -> list[Cell | None]:
    """The drop cells of one count column (module docstring): exact when chain mode
    released it, ``<k`` when it is small, ``suppressed`` beside a guarded total, else
    the rounded difference of the released neighbours."""
    out: list[Cell | None] = [None]
    for i in range(1, len(rows)):
        prev, cur = totals[i - 1], totals[i]
        if prev.kind == "withheld" or cur.kind == "withheld":
            out.append(Cell(None, "withheld"))
            continue
        row = rows[i]
        value = row.get(column)
        hidden = bool(row.get(f"{column}{MARKER_SUFFIX}", False))
        if not hidden and value is not None:
            out.append(Cell(int(value), "exact"))
            continue
        small = row.get(f"{column}{DROP_SMALL_SUFFIX}")
        if small is None:  # a frame without the EP-48 marker: both neighbours touched
            small = prev.kind != "exact" and cur.kind != "exact"
        if bool(small) or prev.released is None:
            # a small drop, or a drop out of a total that is itself below k (then so is
            # the drop: the chain is non-increasing)
            out.append(Cell(None, "small"))
            continue
        estimate = max(prev.released - (cur.released or 0), 0)
        out.append(Cell(disclose.band(estimate), "approx"))
    return out


def prepare(
    source: Any,
    *,
    k: int = K_DEFAULT,
    ref: str | None = None,
    tier: str | None = None,
    run_id: str | None = None,
    def_hash: str | None = None,
    title: str | None = None,
) -> Chain:
    """Type the suppressed frame's cells and apply the pair guard (module docstring)."""
    if k < 1:
        raise AttritionRenderError(f"k = {k} is invalid (k >= 1)")
    rows = _rows(source)
    units = [_total(r, "n_units") for r in rows]
    subjects = [_total(r, "n_subjects") for r in rows]
    guarded: list[int] = []
    for i in range(len(rows)):
        u, s = units[i].released, subjects[i].released
        if u is not None and s is not None and 0 < abs(u - s) < k:
            subjects[i] = Cell(None, "withheld")
            guarded.append(i)
    dropped_units = _drops(rows, "dropped_units", units)
    dropped_subjects = _drops(rows, "dropped_subjects", subjects)
    for i, r in enumerate(rows):
        if str(r.get("polarity") or "") == COHORT_POLARITY:
            dropped_units[i] = None  # the materialisation step excludes nothing (docstring)
            dropped_subjects[i] = None
    steps = tuple(
        StepRow(
            index=i,
            step=str(r["step"]),
            label=_truncate(str(r.get("label") or r["step"])),
            polarity=str(r.get("polarity") or ""),
            kind=None if r.get("kind") in (None, "") else str(r["kind"]),
            custom=bool(r.get("custom", False)),
            units=units[i],
            subjects=subjects[i],
            dropped_units=dropped_units[i],
            dropped_subjects=dropped_subjects[i],
        )
        for i, r in enumerate(rows)
    )
    return Chain(
        rows=steps,
        k=k,
        ref=ref,
        tier=tier,
        run_id=run_id,
        def_hash=def_hash,
        title=title,
        guarded=tuple(guarded),
    )


def from_attrition(attrition: Attrition, *, title: str | None = None) -> Chain:
    """The chain of an :class:`~mimicwarehouse.cohort.build.Attrition` result (its
    frame, ``k``, ref, tier, run id and def_hash)."""
    return prepare(
        attrition.df,
        k=attrition.k,
        ref=attrition.ref,
        tier=attrition.tier,
        run_id=attrition.run_id,
        def_hash=attrition.def_hash,
        title=title,
    )


def _coerce(source: Any, **meta: Any) -> Chain:
    """A :class:`Chain`, an :class:`Attrition` or a frame → the chain to draw."""
    title = meta.pop("title", None)
    if isinstance(source, Chain):
        return replace(source, title=title) if title is not None else source
    if isinstance(source, Attrition):
        return from_attrition(source, title=title)
    return prepare(source, title=title, **meta)


# ---------------------------------------------------------------------------
# Mermaid
# ---------------------------------------------------------------------------


def escape_label(text: str) -> str:
    """User text inside a quoted Mermaid label: the entity codes for ``#`` ``"`` ``<``
    ``>`` (Mermaid renders them back) and no line breaks."""
    for raw, code in _MERMAID_ESCAPES:
        text = text.replace(raw, code)
    return " ".join(text.split())


def _step_label(row: StepRow, k: int) -> str:
    head = f"Step {row.index} - {escape_label(row.step)}"
    if row.custom:
        head += " (custom)"
    parts = [head]
    if row.label != row.step:
        parts.append(escape_label(row.label))
    parts.append(f"units n = {row.units.text(k)}")
    parts.append(f"subjects n = {row.subjects.text(k)}")
    return "<br/>".join(parts)


def _excluded_label(row: StepRow, k: int) -> str:
    assert row.dropped_units is not None and row.dropped_subjects is not None
    return "<br/>".join(
        [
            f"Excluded at {escape_label(row.step)}",
            f"units n = {row.dropped_units.text(k)}",
            f"subjects n = {row.dropped_subjects.text(k)}",
        ]
    )


def render_mermaid(
    source: Any,
    title: str | None = None,
    direction: str = "TD",
    *,
    k: int = K_DEFAULT,
    ref: str | None = None,
    tier: str | None = None,
    run_id: str | None = None,
    def_hash: str | None = None,
) -> str:
    """The Mermaid flowchart (module docstring). ``source`` is an :class:`Attrition`,
    a :class:`Chain` or the suppressed frame (then ``k`` and the footer facts come from
    the keyword arguments); ``title`` becomes Mermaid front matter."""
    if direction not in DIRECTIONS:
        raise ValueError(f"direction {direction!r} is not one of {', '.join(DIRECTIONS)}")
    chain = _coerce(source, k=k, ref=ref, tier=tier, run_id=run_id, def_hash=def_hash, title=title)
    palette = theme.palette("light")
    lines: list[str] = []
    if chain.title:
        lines += ["---", f"title: {json.dumps(chain.title)}", "---"]
    lines.append(f"flowchart {direction}")
    step_ids = [f"s{row.index}" for row in chain.rows]
    for row, node in zip(chain.rows, step_ids, strict=True):
        lines.append(f'    {node}["{_step_label(row, chain.k)}"]')
    for a, b in itertools.pairwise(step_ids):
        lines.append(f"    {a} --> {b}")
    excluded_ids: list[str] = []
    for row in chain.rows[1:]:
        if not row.has_exclusion:
            continue
        node = f"x{row.index}"
        excluded_ids.append(node)
        lines.append(f'    {node}["{_excluded_label(row, chain.k)}"]')
        lines.append(f"    s{row.index - 1} -.-> {node}")
    lines.append(f'    f["{escape_label(chain.footer())}"]')
    lines.append(f"    {step_ids[-1]} ~~~ f")
    lines.append(
        f"    classDef step fill:{palette.surface},stroke:{palette.primary},color:{palette.text}"
    )
    lines.append(
        f"    classDef excluded fill:{palette.background},stroke:{palette.suppressed},"
        f"color:{palette.muted}"
    )
    lines.append(
        f"    classDef footer fill:{palette.background},stroke:{palette.grid},color:{palette.muted}"
    )
    lines.append(f"    class {','.join(step_ids)} step")
    if excluded_ids:
        lines.append(f"    class {','.join(excluded_ids)} excluded")
    lines.append("    class f footer")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Altair / Vega-Lite / PNG
# ---------------------------------------------------------------------------


def records(source: Any, **meta: Any) -> list[dict[str, Any]]:
    """The aggregate records behind the figure and the CSV's text columns: the step,
    its label / polarity / custom flag, the released ``n_units`` / ``n_subjects``
    (``null`` when withheld — never zeroed) and the rendered texts."""
    chain = _coerce(source, **meta)
    k = chain.k
    out: list[dict[str, Any]] = []
    for row in chain.rows:
        units, subjects = row.units.text(k), row.subjects.text(k)
        exclusion = ""
        if row.has_exclusion and row.dropped_units and row.dropped_subjects:
            exclusion = (
                f"excluded: units n = {row.dropped_units.text(k)} - "
                f"subjects n = {row.dropped_subjects.text(k)}"
            )
        out.append(
            {
                "order": row.index,
                "step": row.step,
                "label": row.label,
                "polarity": row.polarity,
                "custom": row.custom,
                "n_units": row.units.released,
                "n_subjects": row.subjects.released,
                "units_shown": units,
                "subjects_shown": subjects,
                "counts": f"units n = {units} - subjects n = {subjects}",
                "dropped_units_shown": row.dropped_units.text(k) if row.dropped_units else "",
                "dropped_subjects_shown": (
                    row.dropped_subjects.text(k) if row.dropped_subjects else ""
                ),
                "exclusion": exclusion,
            }
        )
    return out


def render_altair(source: Any, *, mode: str = "light", **meta: Any) -> altair.LayerChart:
    """The fallback figure (module docstring): a horizontal funnel of ``n_units`` per
    step, the counts beside each bar (a withheld step keeps its text at the axis and no
    bar), the exclusion under it. The theme is applied by :func:`to_vegalite`."""
    import altair as alt

    chain = _coerce(source, **meta)
    palette = theme.palette(mode)
    data = alt.InlineData(values=records(chain))
    # an explicit order: a field sort is dropped when the layers filter the data
    y = alt.Y(
        "step:N",
        sort=[row.step for row in chain.rows],
        title=None,
        axis=alt.Axis(labelLimit=220),
    )
    base = alt.Chart(data)
    released = base.transform_filter("isValid(datum.n_units)")
    # the bar sits in the lower half of its band; the exclusion that produced the step
    # is written in the upper half, i.e. between the previous bar and this one
    bars = released.mark_bar(size=BAR_SIZE, yOffset=BAR_OFFSET, color=palette.primary).encode(
        y=y,
        x=alt.X("n_units:Q", title="units remaining"),
        tooltip=[
            alt.Tooltip("step:N", title="step"),
            alt.Tooltip("label:N", title="label"),
            alt.Tooltip("units_shown:N", title="units"),
            alt.Tooltip("subjects_shown:N", title="subjects"),
        ],
    )
    counts = released.mark_text(
        align="left", baseline="middle", dx=4, dy=BAR_OFFSET, color=palette.text
    ).encode(y=y, x=alt.X("n_units:Q"), text="counts:N")
    withheld = (
        base.transform_filter("!isValid(datum.n_units)")
        .mark_text(align="left", baseline="middle", dx=4, dy=BAR_OFFSET, color=palette.suppressed)
        .encode(y=y, x=alt.value(0), text="counts:N")
    )
    exclusions = (
        base.transform_filter("datum.exclusion != ''")
        .mark_text(
            align="left",
            baseline="middle",
            dx=4,
            dy=-(BAR_OFFSET + 4),
            fontSize=10,
            color=palette.muted,
        )
        .encode(y=y, x=alt.value(0), text="exclusion:N")
    )
    chart = alt.layer(bars, counts, withheld, exclusions).properties(
        width=CHART_WIDTH,
        height=alt.Step(ROW_STEP),
        title=alt.TitleParams(text=chain.title or chain.default_title(), subtitle=chain.footer()),
    )
    return cast("alt.LayerChart", chart)


def to_vegalite(source: Any, *, mode: str = "light", **meta: Any) -> dict[str, Any]:
    """The Vega-Lite spec of :func:`render_altair` with the EP-5 theme merged in
    (``theme.register_altair`` + ``alt.theme.enable`` as a context, so the process-wide
    theme is untouched). Plain JSON: aggregates only in ``data.values``."""
    import altair as alt

    chart = render_altair(source, mode=mode, **meta)
    theme.register_altair()
    with alt.theme.enable(theme.THEME_NAMES[theme.palette(mode).mode]):
        spec = chart.to_dict()
    # Altair consolidates inline data into a top-level `datasets` map; a self-contained
    # `data.values` spec is what the checker's docs describe and what EP-62 / EP-130 embed
    datasets = spec.get("datasets")
    name = spec.get("data", {}).get("name") if isinstance(spec.get("data"), dict) else None
    if isinstance(datasets, dict) and name in datasets:
        spec["data"] = {"values": datasets.pop(name)}
        if not datasets:
            del spec["datasets"]
    return spec


def to_png(spec: Mapping[str, Any], *, scale: float = PNG_SCALE) -> bytes:
    """The static PNG of a Vega-Lite spec through ``vl-convert-python``."""
    try:
        import vl_convert as vlc
    except ImportError as exc:
        raise AttritionRenderError(
            "vl-convert-python is not installed (a core dependency since EP-48) - run "
            "`uv sync --group dev`"
        ) from exc
    return bytes(vlc.vegalite_to_png(json.dumps(dict(spec)), scale=scale))


# ---------------------------------------------------------------------------
# The suppressed table (CSV) and the Markdown report
# ---------------------------------------------------------------------------


def export_frame(source: Any, **meta: Any) -> polars.DataFrame:
    """The ``attrition.csv`` frame: the accessor's columns (released values and their
    ``_suppressed`` / ``_banded`` markers, the pair guard applied) plus the rendered
    texts (``*_shown``)."""
    import polars as pl

    chain = _coerce(source, **meta)
    k = chain.k
    rows = chain.rows

    def total_columns(name: str, cells: Sequence[Cell]) -> list[pl.Series]:
        return [
            pl.Series(name, [c.released for c in cells], dtype=pl.Int64),
            pl.Series(
                f"{name}{MARKER_SUFFIX}",
                [c.kind in ("small", "withheld") for c in cells],
                dtype=pl.Boolean,
            ),
            pl.Series(
                f"{name}{BAND_SUFFIX}", [c.kind == "banded" for c in cells], dtype=pl.Boolean
            ),
        ]

    def drop_columns(name: str, cells: Sequence[Cell | None]) -> list[pl.Series]:
        return [
            pl.Series(
                name,
                [c.value if c is not None and c.kind == "exact" else None for c in cells],
                dtype=pl.Int64,
            ),
            pl.Series(
                f"{name}{MARKER_SUFFIX}",
                [c is not None and c.kind != "exact" for c in cells],
                dtype=pl.Boolean,
            ),
        ]

    columns: list[pl.Series] = [
        pl.Series("step_index", [r.index for r in rows], dtype=pl.Int64),
        pl.Series("step", [r.step for r in rows], dtype=pl.String),
        pl.Series("label", [r.label for r in rows], dtype=pl.String),
        pl.Series("polarity", [r.polarity for r in rows], dtype=pl.String),
        pl.Series("kind", [r.kind for r in rows], dtype=pl.String),
        pl.Series("custom", [r.custom for r in rows], dtype=pl.Boolean),
        *total_columns("n_units", [r.units for r in rows]),
        *drop_columns("dropped_units", [r.dropped_units for r in rows]),
        *total_columns("n_subjects", [r.subjects for r in rows]),
        *drop_columns("dropped_subjects", [r.dropped_subjects for r in rows]),
        pl.Series("units_shown", [r.units.text(k) for r in rows], dtype=pl.String),
        pl.Series("subjects_shown", [r.subjects.text(k) for r in rows], dtype=pl.String),
        pl.Series(
            "dropped_units_shown",
            [r.dropped_units.text(k) if r.dropped_units else "" for r in rows],
            dtype=pl.String,
        ),
        pl.Series(
            "dropped_subjects_shown",
            [r.dropped_subjects.text(k) if r.dropped_subjects else "" for r in rows],
            dtype=pl.String,
        ),
    ]
    return pl.DataFrame(columns)


def csv_text(source: Any, **meta: Any) -> str:
    """:func:`export_frame` as CSV text (LF, UTF-8)."""
    return str(export_frame(source, **meta).write_csv())


def _md_cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _drop_cell(cell: Cell | None, k: int) -> str:
    """A drop in the Markdown table: ``n = X`` text (the checker's prose rule reads it;
    an integer cell would join the nested-totals heuristic) or ``-`` on the first step."""
    return "-" if cell is None else f"n = {cell.text(k)}"


def disclosure_line(k: int) -> str:
    return (
        'Disclosure: every count below passed `disclose.suppress(mode="chain")` at '
        f"k = {k} (EP-43) - a total below k reads `<{k}`; a drop below k is withheld and "
        "its two neighbours are banded to the nearest ten (`~1,000`); a drop withheld only "
        "because a neighbour is banded is shown as the rounded difference of the released "
        "totals (`~`); a subjects total whose difference from the units total would fall "
        f"below k reads `{WITHHELD_TEXT}`, together with the drops beside it. Run "
        "`mwh disclose check --write-sidecar` before promoting this file."
    )


def _not_claimed(ref: str | None) -> list[str]:
    """The spec's *what it does not claim* items when the registry resolves ``ref``."""
    generic = [
        "The chain counts records selected by the compiled criteria as recorded; it is a "
        "computable definition of a study population, not a validated clinical cohort "
        "(docs/methods/cohorts.md)."
    ]
    if not ref:
        return generic
    try:
        from mimicwarehouse.cohort.registry import load_registry
        from mimicwarehouse.cohort.spec import CohortSpecError

        try:
            items = list(load_registry().get(ref).spec.what_it_does_not_claim)
        except CohortSpecError:
            return generic
    except Exception:  # pragma: no cover - a broken registry never blocks a report
        return generic
    return [_truncate_line(item) for item in items] + generic


def _truncate_line(text: str) -> str:
    return " ".join(text.split())


def render_markdown(
    source: Any,
    *,
    settings: Settings | None = None,
    mermaid: str | None = None,
    **meta: Any,
) -> str:
    """The paste-ready report (module docstring). ``settings`` (with a run id in the
    chain) adds the EP-35 reproduction block; ``mermaid`` reuses an already rendered
    diagram."""
    chain = _coerce(source, **meta)
    k = chain.k
    ref = chain.ref or "(cohort)"
    tier = chain.tier or "-"
    rows = [
        [
            _md_cell(r.step),
            _md_cell(r.polarity or "-"),
            _md_cell(r.label),
            r.units.text(k),
            r.subjects.text(k),
            _drop_cell(r.dropped_units, k),
            _drop_cell(r.dropped_subjects, k),
        ]
        for r in chain.rows
    ]
    header = [
        "step",
        "polarity",
        "label",
        "n_units",
        "n_subjects",
        "units dropped",
        "subjects dropped",
    ]
    table = [
        "| " + " | ".join(header) + " |",
        "|---|---|---|---:|---:|---:|---:|",
        *("| " + " | ".join(r) + " |" for r in rows),
    ]
    facts = f"Cohort `{ref}`"
    if chain.def_hash:
        facts += f" (def_hash `{chain.def_hash[:12]}`)"
    facts += (
        f" - tier `{tier}` - build run `{chain.run_id or '-'}` - k = {k} - "
        f"{len(chain.rows)} step(s)"
    )
    if chain.guarded:
        facts += f" - pair guard applied at step(s) {', '.join(str(i) for i in chain.guarded)}"
    facts += "."
    lines = [
        f"# {chain.title or chain.default_title()}",
        "",
        f"**Claim type: {CLAIM_TYPE}.** {RETROSPECTIVE_SENTENCE}",
        "",
        disclosure_line(k),
        "",
        facts,
        "",
        *table,
        "",
        "Steps run top to bottom: `base` is the grain's source population, `idx` the index "
        "event, then one `crit_NN_<label>` per criterion in spec order (inclusion keeps, "
        "exclusion removes), the washout when the spec has one, and `cohort` the "
        "materialised table. `units` are the grain's units, `subjects` distinct patients.",
        "",
        "## Diagram",
        "",
        "```mermaid",
        (mermaid if mermaid is not None else render_mermaid(chain)).rstrip("\n"),
        "```",
        "",
        f"The same chain as a static figure: `{PNG_FILE}` (rendered from `{VEGALITE_FILE}`, "
        f"the Vega-Lite spec); the suppressed table is `{CSV_FILE}`; the diagram source is "
        f"`{MERMAID_FILE}` (`mwh cohort attrition <id@version> --tier <t> --format all`).",
        "",
        "## What this does not claim",
        "",
        *(f"- {_md_cell(item)}" for item in _not_claimed(chain.ref)),
        "",
    ]
    if chain.run_id and settings is not None:
        from mimicwarehouse import run as run_mod

        try:
            lines.append(run_mod.reproduction_block(chain.run_id, settings))
        except run_mod.RunLedgerError as exc:
            lines += [
                "## Reproduction",
                "",
                f"Run `{chain.run_id}`: manifest unavailable ({exc}).",
                "",
            ]
    return "\n".join(lines).rstrip("\n") + "\n"


# ---------------------------------------------------------------------------
# save_attrition — the run-folder artefacts behind the disclosure gate
# ---------------------------------------------------------------------------


def files_for(formats: Iterable[str]) -> tuple[str, ...]:
    """The file names ``formats`` (``mermaid`` / ``altair`` / ``all``) write, in
    :data:`ALL_FILES` order."""
    wanted: set[str] = set()
    for fmt in formats:
        if fmt not in FORMAT_FILES:
            raise AttritionRenderError(
                f"unknown format {fmt!r}; expected one of {', '.join(FORMATS)}"
            )
        wanted.update(FORMAT_FILES[fmt])
    return tuple(name for name in ALL_FILES if name in wanted)


@dataclass(frozen=True, slots=True)
class SavedAttrition:
    """What :func:`save_attrition` published: the files, their check results and
    whether the build run's manifest recorded them."""

    ref: str
    tier: str
    k: int
    run_id: str | None
    out_dir: Path
    files: dict[str, Path]
    checks: dict[str, disclose.CheckResult]
    recorded: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "ref": self.ref,
            "tier": self.tier,
            "k": self.k,
            "run_id": self.run_id,
            "out_dir": str(self.out_dir),
            "files": {name: str(path) for name, path in self.files.items()},
            "checks": {
                name: {"passed": r.passed, "n_fail": r.n_fail, "n_warn": r.n_warn}
                for name, r in self.checks.items()
            },
            "recorded": self.recorded,
        }


def render_files(
    chain: Chain,
    names: Sequence[str],
    *,
    settings: Settings | None = None,
    direction: str = "TD",
    mode: str = "light",
) -> dict[str, str | bytes]:
    """Render the requested files in memory (text, or bytes for the PNG)."""
    out: dict[str, str | bytes] = {}
    mermaid = render_mermaid(chain, direction=direction)
    if MERMAID_FILE in names:
        out[MERMAID_FILE] = mermaid
    if VEGALITE_FILE in names or PNG_FILE in names:
        spec = to_vegalite(chain, mode=mode)
        if VEGALITE_FILE in names:
            out[VEGALITE_FILE] = json.dumps(spec, indent=2, sort_keys=True) + "\n"
        if PNG_FILE in names:
            out[PNG_FILE] = to_png(spec)
    if CSV_FILE in names:
        out[CSV_FILE] = csv_text(chain)
    if MARKDOWN_FILE in names:
        out[MARKDOWN_FILE] = render_markdown(chain, settings=settings, mermaid=mermaid)
    return out


def _write(path: Path, payload: str | bytes) -> None:
    if isinstance(payload, bytes):
        path.write_bytes(payload)
    else:
        fsio.atomic_write_text(path, payload)


def _publish(src: Path, dest: Path) -> None:
    try:
        os.replace(src, dest)
    except OSError:  # another volume: copy + delete
        shutil.move(str(src), str(dest))


def _record_figures(run_id: str, names: Sequence[str], settings: Settings) -> None:
    """Add the files to the build run's ``figures`` map (the manifest is rewritten
    atomically; the ledger line is untouched)."""
    from mimicwarehouse.run import Run, read_manifest, run_dir

    manifest = read_manifest(run_id, settings)
    manifest.figures = {**manifest.figures, **{n: f"{MANIFEST_PREFIX}{n}" for n in names}}
    Run(manifest, run_dir(run_id, settings), settings).write_manifest()


def save_attrition(
    ref_or_run_id: str,
    tier: str,
    out_dir: Path | str | None = None,
    *,
    formats: Iterable[str] = ("all",),
    k: int | None = None,
    settings: Settings | None = None,
    title: str | None = None,
    direction: str = "TD",
    mode: str = "light",
    record: bool = True,
) -> SavedAttrition:
    """Render and publish the attrition artefacts of a built cohort (module docstring).
    ``out_dir`` defaults to the build run's ``figures/`` folder (then the manifest
    records the files unless ``record`` is false); every file passes
    :func:`disclose.check` before anything is written, else
    :class:`~mimicwarehouse.disclose.DisclosureError`."""
    from mimicwarehouse.cohort import build as build_mod
    from mimicwarehouse.config import get_settings
    from mimicwarehouse.run import FIGURES_DIRNAME, run_dir

    settings = settings or get_settings()
    names = files_for(formats)
    if direction not in DIRECTIONS:
        raise AttritionRenderError(f"direction {direction!r} is not one of {', '.join(DIRECTIONS)}")
    attrition = build_mod.attrition(ref_or_run_id, tier, k=k, settings=settings)
    chain = from_attrition(attrition, title=title)
    in_run = out_dir is None
    if in_run:
        if attrition.run_id is None:
            raise AttritionRenderError(
                f"the mart of {attrition.ref} on {tier} records no build run id - pass out_dir"
            )
        target = run_dir(attrition.run_id, settings) / FIGURES_DIRNAME
    else:
        target = Path(out_dir)  # type: ignore[arg-type]
    payloads = render_files(chain, names, settings=settings, direction=direction, mode=mode)
    tmp_root = settings.layout["tmp"]
    tmp_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="attrition-", dir=tmp_root) as staging:
        stage = Path(staging)
        for name, payload in payloads.items():
            _write(stage / name, payload)
        checks = {name: disclose.check(stage / name, chain.k) for name in payloads}
        failing = {name: result for name, result in checks.items() if not result.passed}
        if failing:
            details = "; ".join(
                f"{name}: "
                + ", ".join(
                    f"{f.code} {f.where} - {f.detail}"
                    for f in result.findings
                    if f.status == disclose.FAIL
                )
                for name, result in failing.items()
            )
            raise DisclosureError(
                f"attrition artefact(s) refused by the disclosure gate, nothing written: {details}"
            )
        target.mkdir(parents=True, exist_ok=True)
        files: dict[str, Path] = {}
        for name in payloads:
            _publish(stage / name, target / name)
            files[name] = target / name
    recorded = False
    if in_run and record and attrition.run_id is not None:
        _record_figures(attrition.run_id, list(files), settings)
        recorded = True
    return SavedAttrition(
        ref=attrition.ref,
        tier=attrition.tier,
        k=chain.k,
        run_id=attrition.run_id,
        out_dir=target,
        files=files,
        checks=checks,
        recorded=recorded,
    )


__all__ = [
    "ALL_FILES",
    "BAR_OFFSET",
    "BAR_SIZE",
    "CHART_WIDTH",
    "CLAIM_TYPE",
    "COHORT_POLARITY",
    "CSV_FILE",
    "DIRECTIONS",
    "FORMATS",
    "FORMAT_FILES",
    "K_DEFAULT",
    "MANIFEST_PREFIX",
    "MARKDOWN_FILE",
    "MERMAID_FILE",
    "PNG_FILE",
    "PNG_SCALE",
    "RETROSPECTIVE_SENTENCE",
    "ROW_STEP",
    "VEGALITE_FILE",
    "WITHHELD_TEXT",
    "AttritionRenderError",
    "Cell",
    "Chain",
    "SavedAttrition",
    "StepRow",
    "csv_text",
    "disclosure_line",
    "escape_label",
    "export_frame",
    "files_for",
    "from_attrition",
    "prepare",
    "records",
    "render_altair",
    "render_files",
    "render_markdown",
    "render_mermaid",
    "save_attrition",
    "to_png",
    "to_vegalite",
]
