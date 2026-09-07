"""EP-32 — Capstone #0: staging benchmark note + docs/analyses convention.

Fixture-tier only. A synthetic benchmark ledger (never real telemetry) proves
``render_markdown`` (columns, ``fmt_int`` integers, ``-`` placeholders, the totals
row) and the ``benchmarks:begin``/``benchmarks:end`` marker replacement (idempotent,
narrative untouched, refusals); the ``mwh runs benchmarks`` CLI is exercised end to
end against the same synthetic ledger. The committed docs — ``docs/analyses/README.md``
and ``00-staging-benchmark.md`` — are checked for every required section heading, the
claim-type label, the retrospective sentence, the pending-EP-43 disclosure header, the
tracer's pending index row, and a clean ``guard.scan`` (no real-band ids, no
data-shaped content).
"""

from __future__ import annotations

from pathlib import Path

import pytest

import helpers
from mimicwarehouse import config, guard
from mimicwarehouse.cli import app
from mimicwarehouse.dag import benchmarks as benchmarks_mod
from mimicwarehouse.dag.benchmarks import (
    MARK_BEGIN,
    MARK_END,
    RENDER_COLUMNS,
    BenchmarkLine,
    HostInfo,
    render_markdown,
    replace_marked_block,
)

pytestmark = pytest.mark.ep_32

ANALYSES = helpers.WORKSPACE / "docs" / "analyses"
CONVENTION = ANALYSES / "README.md"
NOTE = ANALYSES / "00-staging-benchmark.md"

_HOST = HostInfo(cpu=16, ram_gb=64.0)


def _line(step: str, phase: str, ts: str, wall_s: float, **kw) -> BenchmarkLine:
    return BenchmarkLine(
        ts=ts,
        build_id="20260830T000000-full-abc1234",
        tier="full",
        step=step,
        kind="stage",
        phase=phase,  # type: ignore[arg-type]
        wall_s=wall_s,
        duckdb_version="1.5.5",
        host=_HOST,
        ok=True,
        **kw,
    )


@pytest.fixture
def data_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    yield helpers.tmp_data_root(monkeypatch, tmp_path)
    config.configure()


@pytest.fixture
def synthetic_ledger(data_root: Path) -> Path:
    """A two-step full-tier stage ledger: one two-pass large table, one dim."""
    large = "stage.mimiciv_hosp.labevents"
    benchmarks_mod.append(_line(large, "pass1", "2026-08-30T00:00:01+00:00", 60.0))
    benchmarks_mod.append(_line(large, "pass2", "2026-08-30T00:00:02+00:00", 80.0))
    benchmarks_mod.append(
        _line(
            large,
            "total",
            "2026-08-30T00:00:03+00:00",
            140.0,
            rows=90_000_000,
            bytes_in=18_400_000_000,
            bytes_out=1_800_000_000,
            files=100,
            peak_rss_mb=9_800.0,
        )
    )
    benchmarks_mod.append(
        _line(
            "stage.mimiciv_hosp.d_labitems",
            "total",
            "2026-08-30T00:00:04+00:00",
            0.5,
            rows=4_000,
            bytes_in=2_000_000,
            bytes_out=500_000,
            files=1,
            peak_rss_mb=110.0,
        )
    )
    return data_root


# ---------------------------------------------------------------------------
# render_markdown on a synthetic ledger (brief item 5, first clause)
# ---------------------------------------------------------------------------


def test_render_markdown_columns_and_totals(synthetic_ledger: Path) -> None:
    summary = benchmarks_mod.summarize(tier="full")
    assert summary.height == 2
    md = render_markdown(summary)
    lines = md.splitlines()
    assert lines[0] == "| " + " | ".join(RENDER_COLUMNS) + " |"
    assert set(lines[1].strip("|").split("|")) <= {"---", "---:"}
    # one body row per step (stage. prefix stripped) plus the totals row
    assert len(lines) == 2 + 2 + 1
    dim, large, total = lines[2], lines[3], lines[4]
    assert dim.startswith("| mimiciv_hosp.d_labitems |")
    assert large.startswith("| mimiciv_hosp.labevents |")
    assert total.startswith("| total |")
    # integers via fmt_int; GB with two decimals; walls with one
    assert "90,000,000" in large and "18.40" in large and "10.2x" in large
    assert "60.0" in large and "80.0" in large and "140.0" in large and "9,800" in large
    # the dim never ran the two-pass path: '-' placeholders
    assert "| - | - |" in dim
    # totals: sums for counts/bytes/walls, high-water RSS
    assert "90,004,000" in total and "101" in total and "140.5" in total
    assert "9,800" in total and "9,910" not in total


def test_render_markdown_never_emits_bare_band_integer(synthetic_ledger: Path) -> None:
    md = render_markdown(benchmarks_mod.summarize(tier="full"))
    assert not guard.id_band_hits(md.encode("utf-8")), "guard G4 token in rendered markdown"


# ---------------------------------------------------------------------------
# Marker replacement (brief item 2)
# ---------------------------------------------------------------------------

_DOC = f"""# A note

Narrative above stays.

{MARK_BEGIN}
| stale | table |
{MARK_END}

Narrative below stays.
"""


def test_replace_marked_block_is_idempotent() -> None:
    block = "| fresh | table |\n"
    once = replace_marked_block(_DOC, block)
    assert replace_marked_block(once, block) == once
    assert "stale" not in once and "| fresh | table |" in once
    assert once.startswith("# A note\n\nNarrative above stays.")
    assert once.rstrip().endswith("Narrative below stays.")
    assert once.count(MARK_BEGIN) == 1 and once.count(MARK_END) == 1


@pytest.mark.parametrize(
    "text",
    [
        "no markers at all",
        f"{MARK_BEGIN} only",
        f"{MARK_END} only",
        f"{MARK_END} reversed {MARK_BEGIN}",
        f"{MARK_BEGIN} {MARK_END} {MARK_BEGIN} {MARK_END}",
    ],
)
def test_replace_marked_block_refuses_bad_markers(text: str) -> None:
    with pytest.raises(ValueError):
        replace_marked_block(text, "| t |\n")


# ---------------------------------------------------------------------------
# mwh runs benchmarks (brief item 2, CLI)
# ---------------------------------------------------------------------------


def test_cli_md_and_out(synthetic_ledger: Path, tmp_path: Path) -> None:
    runner = helpers.cli_runner()
    result = runner.invoke(app, ["runs", "benchmarks", "--format", "md"])
    assert result.exit_code == 0, result.output
    assert result.output.splitlines()[0] == "| " + " | ".join(RENDER_COLUMNS) + " |"

    doc = tmp_path / "note.md"
    doc.write_text(_DOC, encoding="utf-8")
    result = runner.invoke(app, ["runs", "benchmarks", "--out", str(doc)])
    assert result.exit_code == 0, result.output
    first = doc.read_text(encoding="utf-8")
    assert "mimiciv_hosp.labevents" in first and "stale" not in first
    assert first.rstrip().endswith("Narrative below stays.")
    result = runner.invoke(app, ["runs", "benchmarks", "--out", str(doc)])
    assert result.exit_code == 0 and "unchanged" in result.output
    assert doc.read_text(encoding="utf-8") == first


def test_cli_refusals(data_root: Path, tmp_path: Path) -> None:
    runner = helpers.cli_runner()
    # empty ledger, unknown format, missing --out target, marker-less --out target
    assert runner.invoke(app, ["runs", "benchmarks"]).exit_code == 2
    assert runner.invoke(app, ["runs", "benchmarks", "--format", "csv"]).exit_code == 2
    benchmarks_mod.append(
        _line("stage.mimiciv_hosp.d_labitems", "total", "2026-08-30T00:00:01+00:00", 0.5)
    )
    missing = tmp_path / "absent.md"
    assert runner.invoke(app, ["runs", "benchmarks", "--out", str(missing)]).exit_code == 2
    unmarked = tmp_path / "unmarked.md"
    unmarked.write_text("# no markers\n", encoding="utf-8")
    assert runner.invoke(app, ["runs", "benchmarks", "--out", str(unmarked)]).exit_code == 2
    assert unmarked.read_text(encoding="utf-8") == "# no markers\n"


# ---------------------------------------------------------------------------
# The committed docs (brief items 1 and 3)
# ---------------------------------------------------------------------------


def test_convention_doc_required_content() -> None:
    text = CONVENTION.read_text(encoding="utf-8")
    for required in (
        "NN-slug.md",
        "Question",
        "Data & tier",
        "Method",
        "Results",
        "What it deliberately does not claim",
        "Reproduction",
        "Provenance footer",
        "claim-type label",
        "MIMIC-IV analyses are retrospective",
        "mwh disclose check",
        ".disclosure.json",
        "pending EP-43",
        "## Index",
    ):
        assert required in text, f"docs/analyses/README.md is missing {required!r}"
    # reader guides for both D-1 audiences
    assert "hiring managers" in text and "clinical-informatics" in text


def test_convention_index_tracer_row_is_pending_only() -> None:
    text = CONVENTION.read_text(encoding="utf-8")
    assert "pending promotion at EP-53" in text  # EP-43 shipped the gate; EP-53 promotes
    assert "20260830T011037-full" in text, "tracer full-tier run id missing from the index"
    assert "runs/tracer/" in text and "no results copied" in text


def test_benchmark_note_required_content() -> None:
    text = NOTE.read_text(encoding="utf-8")
    for heading in (
        "## Question",
        "## Data & tier",
        "## Method",
        "## Results",
        "## What it deliberately does not claim",
        "## Reproduction",
        "## Provenance",
    ):
        assert heading in text, f"00-staging-benchmark.md is missing {heading!r}"
    # EP-43: the pending line became the check + sidecar line (the sidecar is committed)
    assert "00-staging-benchmark.md.disclosure.json" in text
    assert "sidecar pending" not in text.lower()
    assert "Claim type: exploratory" in text
    assert "MIMIC-IV" in text and "retrospective" in text
    assert text.count(MARK_BEGIN) == 1 and text.count(MARK_END) == 1
    # the rendered table is present inside the markers, with a totals row
    block = text[text.index(MARK_BEGIN) : text.index(MARK_END)]
    assert block.splitlines()[1] == "| " + " | ".join(RENDER_COLUMNS) + " |"
    assert "| total |" in block
    # reproduction cites the exact background jobs and the renderer command
    for command in (
        "--background --job stage-labevents-full",
        "--background --job stage-emar-full",
        "--background --job stage-hosp-rest-full",
        "--background --job stage-chartevents-full",
        "--background --job stage-icu-events-full",
        "mwh runs benchmarks --format md",
    ):
        assert command in text, f"Reproduction is missing {command!r}"


def test_docs_pass_guard_scan() -> None:
    violations = guard.scan([CONVENTION, NOTE], helpers.REPO_ROOT)
    assert not violations, [f"{v.rule}: {v.path}" for v in violations]
