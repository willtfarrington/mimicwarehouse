"""EP-43 — disclosure primitives (``mimicwarehouse.disclose``): the governance acceptance.

Fixture tier only. Every frame and file here is crafted in the test or committed under
``tests/fixtures/disclose/`` (synthetic, ids >= 90 000 000); the real MIMIC id bands enter
only as the GOVERNANCE §3 constants the guard already carries, used as *patterns* to refuse.

The module **refuses** the crafted violations of brief item 4 (a CSV with a ``subject_id``
column, a Parquet whose integer column holds a band value, a Markdown table with a cell
of 7, a ``.mmd`` attrition diagram with ``n = 4``, an HTML file with a 5 000-row embedded
Vega dataset, a Parquet with a 200-char ``text`` column) and **passes** the clean ones (an
aggregate CSV, a suppressed table with its marker columns, a ``.mmd`` with ``<11``).
Suppression: the 2x2 with one small cell, the 3x3 margin fixpoint, chain mode on
``[1000, 995, 400]``, idempotence, the n vs n_fit pair, rate blanking, pandas round-trip;
the ``safe.SUPPRESSOR`` hook now releases complementary suppression row-wise (the EP-30
crafted small group loses its complementary row too). Sidecars validate, ``verify`` fails
after a one-byte edit; the CLI exit codes; the retroactive checks on ``DATA-DICTIONARY.md``
and ``docs/analyses/00-staging-benchmark.md`` and their committed sidecars; the docs page
lists every code with an example; the import budget.

Everything asserted or printed is synthetic values, column names, codes and counts.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

import polars as pl
import pytest

import helpers
from mimicwarehouse import config, disclose, guard, safe
from mimicwarehouse.cli import app
from mimicwarehouse.disclose import (
    CODES,
    DiscloseUsageError,
    DisclosureError,
    assert_clean,
    check,
    check_frame,
    render_cell,
    suppress,
    verify,
    warn_badges,
    write_sidecar,
)
from mimicwarehouse.fixtures import disclose as disclose_fixtures

if TYPE_CHECKING:
    from mimicwarehouse.config import Settings

pytestmark = pytest.mark.ep_43

FIXTURES = helpers.WORKSPACE / "tests" / "fixtures" / "disclose"
DOCS_PAGE = helpers.WORKSPACE / "docs" / "methods" / "disclosure.md"
DICTIONARY = helpers.WORKSPACE / "DATA-DICTIONARY.md"
BENCHMARK_NOTE = helpers.WORKSPACE / "docs" / "analyses" / "00-staging-benchmark.md"
HOSP = "mimiciv_hosp"
K = 11


def _hidden(df: pl.DataFrame, col: str) -> list[bool]:
    return df.get_column(f"{col}{disclose.MARKER_SUFFIX}").to_list()


# ---------------------------------------------------------------------------
# 0. Constants mirror safe; the hook is installed
# ---------------------------------------------------------------------------


def test_constants_and_hook_wiring() -> None:
    assert disclose.FREE_TEXT_MAX_CHARS == safe.FREE_TEXT_MAX_CHARS
    assert disclose.K_DEFAULT == safe.K_FLOOR == K
    assert safe.SUPPRESSOR is disclose.safe_suppressor, "EP-43 swaps the EP-30 hook"
    assert tuple(CODES) == (
        "ID_COL",
        "ID_BAND",
        "FREE_TEXT",
        "SMALL_CELL",
        "EMBEDDED_ROWS",
        "NO_SOURCE",
        "OVERSIZE",
    )
    for band in (guard.SUBJECT_BAND, guard.HADM_BAND, guard.STAY_BAND):
        assert guard.band_of(band[0] + 5) is not None
    assert guard.band_of(90_000_001) is None, "fixture ids never fall in a band"


# ---------------------------------------------------------------------------
# 1. suppress: table mode
# ---------------------------------------------------------------------------


def test_2x2_one_small_cell_two_suppressed_totals_consistent() -> None:
    df = pl.DataFrame({"group": ["a", "b"], "n_yes": [5, 50], "n_no": [100, 60]})
    out, report = suppress(df, k=K)
    assert report.count_cols == ("n_yes", "n_no") and report.mode == "table"
    assert report.n_primary == 1 and report.n_complementary == 1 and report.n_suppressed == 2
    assert out.get_column("n_yes").to_list() == [None, None], "the column margin cannot back it out"
    assert out.get_column("n_no").to_list() == [100, 60], "the published totals are untouched"
    assert _hidden(out, "n_yes") == [True, True] and _hidden(out, "n_no") == [False, False]
    assert out.columns == ["group", "n_yes", "n_yes_suppressed", "n_no", "n_no_suppressed"]
    kinds = {(c.row, c.column): c.kind for c in report.cells}
    assert kinds == {(0, "n_yes"): "primary", (1, "n_yes"): "complementary"}
    assert report.rows_suppressed == 2
    # without complementary suppression only the primary cell goes
    out_primary, rep_primary = suppress(df, k=K, complementary=False)
    assert _hidden(out_primary, "n_yes") == [True, False] and rep_primary.n_complementary == 0
    # k = 1 (the synthetic tiers' floor) suppresses nothing
    same, none = suppress(df, k=1)
    assert none.n_suppressed == 0 and same.get_column("n_yes").to_list() == [5, 50]


def test_3x3_margin_case_reaches_fixpoint_and_is_idempotent() -> None:
    rows = [(r, c) for r in ("r1", "r2", "r3") for c in ("c1", "c2", "c3")]
    values = [5, 20, 30, 40, 25, 60, 70, 80, 90]
    df = pl.DataFrame(
        {"row": [r for r, _ in rows], "col": [c for _, c in rows], "n": values},
        schema={"row": pl.String, "col": pl.String, "n": pl.Int64},
    )
    out, report = suppress(df, k=K)
    hidden = _hidden(out, "n")
    assert report.n_primary == 1
    assert sum(hidden) == 4, "one primary + three complementary cells form a rectangle"
    for key in ("row", "col"):
        for level in out.get_column(key).unique().to_list():
            in_margin = [
                h for h, v in zip(hidden, out.get_column(key).to_list(), strict=True) if v == level
            ]
            assert sum(in_margin) != 1, f"margin {key}={level} would back a cell out"
    assert out.filter(pl.col("n").is_null()).height == 4
    again, report_again = suppress(out, k=K)
    assert again.equals(out), "suppress twice = suppress once"
    assert report_again.n_suppressed == 0
    # a total row is a margin like any other and is never the complementary pick
    with_total = pl.DataFrame({"g": ["a", "b", "c", "total"], "n": [5, 30, 40, 75]})
    out_t, rep_t = suppress(with_total, k=K)
    assert _hidden(out_t, "n") == [True, True, False, False] and rep_t.n_complementary == 1


def test_n_vs_n_fit_pair_and_rate_blanking() -> None:
    # the EP-31 lesson: two published totals whose difference is a small excluded cell
    fit = pl.DataFrame({"n": [1000], "n_fit": [995], "n_events": [200]})
    out, report = suppress(fit, k=K)
    assert out.get_column("n").to_list() == [1000] and out.get_column("n_fit").to_list() == [None]
    assert out.get_column("n_events").to_list() == [200]
    assert [c.kind for c in report.cells] == ["derived"] and report.n_complementary == 1
    findings = check_frame(fit, K)
    assert [f.code for f in findings] == ["SMALL_CELL"] and "difference" in findings[0].detail
    assert not check_frame(out, K), "the suppressed frame passes"
    # a rate beside a hidden count is blanked so it cannot restore the cell
    levels = pl.DataFrame(
        {
            "level": [0, 1, 2],
            "n_units": [100, 100, 100],
            "n_positive": [0, 95, 50],
            "share": [0.0, 0.95, 0.5],
        }
    )
    out_l, rep_l = suppress(levels, k=K)
    # the derived cell (level 1) opens the n_positive column margin, whose next-smallest
    # non-zero cell (level 2) is the complement; zero stays published
    assert out_l.get_column("n_positive").to_list() == [0, None, None]
    assert out_l.get_column("n_units").to_list() == [100, 100, 100]
    assert out_l.get_column("share").to_list() == [0.0, None, None]
    assert {c.kind for c in rep_l.cells} == {"derived", "complementary", "rate"}
    # without margins to protect (complementary=False) only the derived pair is hidden
    out_p, rep_p = suppress(levels, k=K, complementary=False)
    assert out_p.get_column("n_positive").to_list() == [0, 95, 50] and rep_p.n_suppressed == 0


def test_pandas_round_trip_explicit_columns_and_usage_errors() -> None:
    pd = pytest.importorskip("pandas")
    frame = pd.DataFrame(
        {"unit": ["a", "b", "c"], "count_x": [3, 40, 50], "mean_age": [60.0, 61.0, 62.0]}
    )
    out, report = suppress(frame, k=K)
    assert type(out).__module__.startswith("pandas") and report.count_cols == ("count_x",)
    assert out["count_x"].isna().tolist() == [True, True, False], "float value columns are not keys"
    assert out["count_x_suppressed"].tolist() == [True, True, False]
    # explicit count / group columns; a non-numeric count column is a usage error
    df = pl.DataFrame({"g": ["a", "b"], "total": [4, 40], "label": ["x", "y"]})
    out2, rep2 = suppress(df, k=K, count_cols=["total"], group_cols=["g"])
    assert rep2.count_cols == ("total",) and _hidden(out2, "total") == [True, True]
    with pytest.raises(DiscloseUsageError, match="not numeric"):
        suppress(df, k=K, count_cols=["label"])
    with pytest.raises(DiscloseUsageError, match="not in the frame"):
        suppress(df, k=K, count_cols=["nope"])
    with pytest.raises(DiscloseUsageError, match="mode"):
        suppress(df, k=K, mode="grid")
    with pytest.raises(DiscloseUsageError, match="k >= 1"):
        suppress(df, k=0)
    with pytest.raises(DiscloseUsageError, match="DataFrame"):
        suppress([1, 2, 3], k=K)  # type: ignore[arg-type]
    # no count-like column: nothing to do, report is empty
    plain, rep_plain = suppress(pl.DataFrame({"g": ["a"], "mean": [1.5]}), k=K)
    assert rep_plain.count_cols == () and plain.columns == ["g", "mean"]


# ---------------------------------------------------------------------------
# 2. suppress: chain mode + render_cell + warn_badges
# ---------------------------------------------------------------------------


def test_chain_mode_bands_both_neighbours_and_withholds_adjacent_drops() -> None:
    chain = pl.DataFrame({"step": ["all", "adults", "first stay"], "n": [1000, 995, 400]})
    out, report = suppress(chain, k=K, mode="chain")
    assert report.mode == "chain" and report.count_cols == ("n",)
    assert out.get_column("n").to_list() == [1000, 1000, 400], "995 -> ~1,000 (nearest 10)"
    assert out.get_column("n_banded").to_list() == [True, True, False]
    assert out.get_column("n_suppressed").to_list() == [False, False, False]
    assert out.get_column("drop").to_list() == [None, None, None], (
        "the small drop is withheld and so is the drop beside a banded total"
    )
    assert out.get_column("drop_suppressed").to_list() == [False, True, True]
    assert report.n_banded == 2 and report.n_primary == 1 and report.n_complementary == 1
    cells = [
        render_cell(n, K, banded=b)
        for n, b in zip(
            out.get_column("n").to_list(), out.get_column("n_banded").to_list(), strict=True
        )
    ]
    assert cells == ["~1,000", "~1,000", "400"]
    # a total below k is itself suppressed; exact drops between exact totals are published
    long = pl.DataFrame({"step": list("abcde"), "n": [500, 300, 100, 30, 5]})
    out_l, rep_l = suppress(long, k=K, mode="chain")
    assert out_l.get_column("n").to_list() == [500, 300, 100, 30, None]
    assert out_l.get_column("drop").to_list() == [None, 200, 200, 70, None]
    assert out_l.get_column("drop_suppressed").to_list() == [False, False, False, False, True]
    assert rep_l.n_primary == 1 and rep_l.n_banded == 0
    assert render_cell(None, K) == "<11" and render_cell(12345) == "12,345"
    assert disclose.band(995) == 1000 and disclose.band(994) == 990 and disclose.band(5) == 10
    with pytest.raises(DiscloseUsageError, match="non-increasing"):
        suppress(pl.DataFrame({"step": ["a", "b"], "n": [10, 20]}), k=K, mode="chain")
    with pytest.raises(DiscloseUsageError, match="no nulls"):
        suppress(pl.DataFrame({"step": ["a", "b"], "n": [10, None]}), k=K, mode="chain")
    with pytest.raises(DiscloseUsageError, match="count column"):
        suppress(pl.DataFrame({"step": ["a"], "mean": [1.0]}), k=K, mode="chain")


def test_warn_badges_flags_only_small_counts() -> None:
    df = pl.DataFrame(
        {"g": ["a", "b", "c"], "n": [3, 0, 40], "n_deaths": [None, 12, 7], "share": [0.1, 0.2, 0.3]}
    )
    flags = warn_badges(df, K)
    assert flags.columns == df.columns and flags.schema["g"] == pl.Boolean
    assert flags.get_column("g").to_list() == [False, False, False]
    assert flags.get_column("n").to_list() == [True, False, False]
    assert flags.get_column("n_deaths").to_list() == [False, False, True]
    assert flags.get_column("share").to_list() == [False, False, False]


# ---------------------------------------------------------------------------
# 3. The safe.SUPPRESSOR hook: complementary, released row-wise
# ---------------------------------------------------------------------------


def test_safe_suppressor_withholds_rows_and_keeps_the_hook_contract() -> None:
    df = pl.DataFrame({"g": ["a", "b", "c"], "n": [1, 50, 100], "max_age": [70, 80, 90]})
    kept, withheld = disclose.safe_suppressor(df, K, ["n"])
    assert withheld == 2 and kept.columns == df.columns
    assert kept.get_column("g").to_list() == ["c"], (
        "the complementary row leaves with the small one"
    )
    assert kept.get_column("max_age").to_list() == [90], "SGT-2: extreme values travel with rows"
    same, zero = disclose.safe_suppressor(df, 1, ["n"])
    assert zero == 0 and same.equals(df)
    same, zero = disclose.safe_suppressor(df, K, ["max_age_missing"])
    assert zero == 0 and same.equals(df)
    # EP-30's primary-only rule stays importable and differs exactly by the complement
    primary, dropped = safe.rowwise_suppress(df, K, ["n"])
    assert dropped == 1 and primary.height == 2


def test_safe_query_small_group_now_loses_its_complement(fixture_lake_settings: Settings) -> None:
    sql = (
        "SELECT CASE WHEN anchor_age = (SELECT min(anchor_age) FROM mimiciv_hosp.patients) "
        "THEN 'index' ELSE 'rest' END AS age_bucket, count(*) AS n "
        f"FROM {HOSP}.patients GROUP BY 1"
    )
    result = safe.safe_query(sql, tier="fixture", settings=fixture_lake_settings)
    assert result.rows_suppressed == 2 and result.n_rows == 0, (
        "the 'rest' row with the published total would back the crafted cell out (EP-30's "
        "test_small_group_suppressed_rowwise documents the change)"
    )
    total = safe.safe_query(
        f"SELECT count(*) AS n FROM {HOSP}.patients", tier="fixture", settings=fixture_lake_settings
    )
    assert total.rows_suppressed == 0 and total.n_rows == 1


# ---------------------------------------------------------------------------
# 4. check: the crafted violations are refused, the clean artefacts pass
# ---------------------------------------------------------------------------


def _codes(result: disclose.CheckResult, status: str = "fail") -> set[str]:
    return {f.code for f in result.findings if f.status == status}


def test_committed_fixtures_match_the_generator_and_are_guard_clean() -> None:
    assert disclose_fixtures.drift() == [], "run `mwh fixtures disclose` after editing FILES"
    assert set(disclose_fixtures.FILES) == {
        "bad_ids.csv",
        "bad_small_cell.md",
        "good_aggregate.csv",
    }
    violations = guard.scan([FIXTURES], helpers.REPO_ROOT)
    assert violations == [], [f"{v.rule}: {v.detail}" for v in violations]
    assert all(
        content.isascii() and "mwh-guard" not in content
        for content in disclose_fixtures.FILES.values()
    )


def test_check_refuses_crafted_violations(tmp_path: Path) -> None:
    # (a) a CSV with a subject_id column (committed fixture; fixture-band ids)
    bad_ids = check(FIXTURES / "bad_ids.csv", K)
    assert not bad_ids.passed and _codes(bad_ids) == {"ID_COL"}
    assert all("9000" not in f.detail for f in bad_ids.findings), "details never quote values"
    # (b) a Parquet whose integer column holds a band value (built from the guard constants)
    band_value = guard.HADM_BAND[0] + 5
    parquet = tmp_path / "bands.parquet"
    pl.DataFrame({"g": ["a", "b"], "value": [band_value, 12], "n": [20, 30]}).write_parquet(parquet)
    bands = check(parquet, K)
    assert _codes(bands) == {"ID_BAND"}
    assert (
        guard.mask(str(band_value)) in bands.findings[0].detail
        and str(band_value) not in bands.findings[0].detail
    )
    # a count-like column with a band-sized value is a count, not an id (EP-170 amendment 2)
    counts = tmp_path / "counts.parquet"
    pl.DataFrame(
        {"g": ["a"], "n_rows": [guard.SUBJECT_BAND[0] + 1], "total_bytes": [guard.STAY_BAND[0]]}
    ).write_parquet(counts)
    assert check(counts, K).passed
    # (c) a Markdown table with a cell of 7 (committed fixture)
    small = check(FIXTURES / "bad_small_cell.md", K)
    assert _codes(small) == {"SMALL_CELL"} and "row(s) 2" in small.findings[0].detail
    # (d) a Mermaid attrition diagram with n = 4
    mmd = tmp_path / "attrition.mmd"
    mmd.write_text("flowchart TD\n  A[all: n = 1,200] --> B[adults: n = 4]\n", encoding="utf-8")
    assert _codes(check(mmd, K)) == {"SMALL_CELL"}
    # (e) an HTML file with a 5 000-row embedded Vega dataset
    html_path = tmp_path / "chart.html"
    values = [{"x": i, "y": i % 7} for i in range(5000)]
    spec = {
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "data": {"values": values},
    }
    html_path.write_text(
        "<html><body><div id='vis'></div><script type='application/json'>"
        + json.dumps(spec)
        + '</script><script>vegaEmbed(\'#vis\', {"data": {"values": '
        + json.dumps(values)
        + "}});</script><p>n = 1,200</p></body></html>",
        encoding="utf-8",
    )
    embedded = check(html_path, K)
    assert (
        _codes(embedded) == {"EMBEDDED_ROWS"}
        and len([f for f in embedded.findings if f.status == "fail"]) == 2
    )
    # (f) a Parquet with a text column of 200-char strings, and a long-string column by value
    text_parquet = tmp_path / "notes.parquet"
    pl.DataFrame({"g": ["a", "b"], "text": ["x" * 200, "y" * 200], "n": [20, 30]}).write_parquet(
        text_parquet
    )
    assert _codes(check(text_parquet, K)) == {"FREE_TEXT"}
    long_parquet = tmp_path / "labels.parquet"
    pl.DataFrame({"label": ["z" * 70, "w"], "n": [20, 30]}).write_parquet(long_parquet)
    assert _codes(check(long_parquet, K)) == {"FREE_TEXT"}
    assert check(long_parquet, K, allow_text=["label"]).passed, (
        "--allow-text admits dictionary labels"
    )
    # identifier keys in JSON records; a band integer under a non-count key
    records = tmp_path / "rows.json"
    records.write_text(json.dumps([{"subject_id": 90_000_001, "n": 20}]), encoding="utf-8")
    assert _codes(check(records, K)) == {"ID_COL"}
    band_json = tmp_path / "band.json"
    band_json.write_text(json.dumps({"value": band_value, "rows": band_value}), encoding="utf-8")
    band_result = check(band_json, K)
    assert _codes(band_result) == {"ID_BAND"} and len(band_result.findings) == 1, "rows is a count"
    # a figure without its source table; with one, the source's findings are its findings
    png = tmp_path / "fig.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    assert _codes(check(png, K)) == {"NO_SOURCE"}
    pl.DataFrame({"g": ["a"], "n": [4]}).write_csv(tmp_path / "fig.csv")
    with_source = check(png, K)
    assert _codes(with_source) == {"SMALL_CELL"} and with_source.findings[0].where.startswith(
        "source fig.csv"
    )
    # usage errors: missing path, unsupported type, unreadable frame (sanitised, never raw)
    with pytest.raises(DiscloseUsageError, match="no such file"):
        check(tmp_path / "missing.csv", K)
    (tmp_path / "x.docx").write_bytes(b"x")
    with pytest.raises(DiscloseUsageError, match="unsupported"):
        check(tmp_path / "x.docx", K)
    broken = tmp_path / "broken.parquet"
    broken.write_bytes(b"not a parquet file at all 90000123")
    with pytest.raises(DiscloseUsageError, match="cannot load") as excinfo:
        check(broken, K)
    assert "90000123" not in str(excinfo.value)


def test_check_passes_clean_artefacts(tmp_path: Path) -> None:
    good = check(FIXTURES / "good_aggregate.csv", K)
    assert good.passed and good.n_fail == 0 and all(c.status == "pass" for c in good.checks)
    # a suppressed table (marker columns present) passes; the raw one does not
    raw = pl.DataFrame({"g": ["a", "b", "c"], "n": [4, 40, 50], "n_deaths": [0, 12, 30]})
    raw_path = tmp_path / "raw.parquet"
    raw.write_parquet(raw_path)
    assert _codes(check(raw_path, K)) == {"SMALL_CELL"}
    suppressed, _ = suppress(raw, k=K)
    sup_path = tmp_path / "suppressed.parquet"
    suppressed.write_parquet(sup_path)
    assert check(sup_path, K).passed
    # a marked cell that still carries a value is refused
    stale = suppressed.with_columns(pl.Series("n", [4, 40, 50]))
    stale_path = tmp_path / "stale.parquet"
    stale.write_parquet(stale_path)
    assert "still carrying a value" in check(stale_path, K).findings[0].detail
    # a .mmd with <11 passes; so does one with ~ bands
    mmd = tmp_path / "attrition.mmd"
    mmd.write_text("flowchart TD\n  A[all: n = ~1,000] --> B[adults: n = <11]\n", encoding="utf-8")
    assert check(mmd, K).passed
    # a Markdown table with level / files / telemetry headers and suppressed cells passes;
    # an attrition-shaped table whose consecutive totals differ by a small drop does not
    md = tmp_path / "report.md"
    md.write_text(
        "# Report\n\n| level | n_units | n_positive | share |\n|---|---:|---:|---:|\n"
        "| 0 | 100 | 0 | 0.0 % |\n| 1 | 40 | 40 | 100.0 % |\n| 2 | <11 | <11 | - |\n\n"
        "| table | rows | files | pass 1 s |\n|---|---:|---:|---:|\n| t | 1,234 | 1 | 0.4 |\n\n"
        "Denominator: admissions - n = 3,588.\n",
        encoding="utf-8",
    )
    assert check(md, K).passed
    chain_md = tmp_path / "tracer.md"
    chain_md.write_text(
        "| step | n |\n|---|---:|\n| all | 1,000 |\n| adults | 995 |\n| first stay | 400 |\n",
        encoding="utf-8",
    )
    chain_result = check(chain_md, K)
    assert (
        _codes(chain_result) == {"SMALL_CELL"}
        and "attrition drop" in chain_result.findings[0].detail
    )
    # a 300-row embedded array only warns
    html_path = tmp_path / "small.html"
    html_path.write_text(
        '<html><script type="application/json">'
        + json.dumps({"data": {"values": [{"x": i, "n": 20 + i} for i in range(300)]}})
        + "</script></html>",
        encoding="utf-8",
    )
    warned = check(html_path, K)
    assert warned.passed and _codes(warned, "warn") == {"EMBEDDED_ROWS"}
    # HTML tables follow the text-table rules
    table_html = tmp_path / "table.html"
    table_html.write_text(
        "<table><tr><th>g</th><th>n</th></tr><tr><td>a</td><td>3</td></tr></table>",
        encoding="utf-8",
    )
    assert _codes(check(table_html, K)) == {"SMALL_CELL"}
    # a YAML aggregate and a JSON sidecar-shaped document pass
    yaml_path = tmp_path / "agg.yaml"
    yaml_path.write_text(
        "rows:\n  - {g: a, n: 20}\n  - {g: b, n: 30}\nrun_id: 20260907T000000Z-abc123\n",
        encoding="utf-8",
    )
    assert check(yaml_path, K).passed
    # assert_clean for EP-59 / EP-130
    assert assert_clean(suppressed, K).passed
    with pytest.raises(DisclosureError, match="SMALL_CELL"):
        assert_clean(raw, K)
    with pytest.raises(DisclosureError, match="ID_COL"):
        assert_clean(pl.DataFrame({"hadm_id": [91_000_001], "n": [20]}), K)


# ---------------------------------------------------------------------------
# 5. Sidecar + verify + CLI
# ---------------------------------------------------------------------------


def test_sidecar_validates_and_verify_fails_after_a_one_byte_edit(tmp_path: Path) -> None:
    target = tmp_path / "good_aggregate.csv"
    shutil.copy(FIXTURES / "good_aggregate.csv", target)
    result = check(target, K)
    side = write_sidecar(target, result, K, reviewer="owner")
    assert (
        side == disclose.sidecar_path(target) and side.name == "good_aggregate.csv.disclosure.json"
    )
    payload = json.loads(side.read_text(encoding="utf-8"))
    assert payload["schema"] == disclose.SIDECAR_SCHEMA and payload["path"] == "good_aggregate.csv"
    assert (
        payload["sha256"] == disclose.sha256_of(target) and payload["size"] == target.stat().st_size
    )
    assert payload["k"] == K and payload["passed"] is True and payload["reviewer"] == "owner"
    assert [c["code"] for c in payload["checks"]] == list(CODES)
    assert all(c["status"] == "pass" for c in payload["checks"])
    assert (
        payload["tool_version"] and payload["git_sha"] and payload["timestamp"].endswith("+00:00")
    )
    assert verify(target).ok
    # one-byte edit -> verify fails; the sidecar itself stays valid JSON
    target.write_bytes(target.read_bytes()[:-1] + b"7")
    mismatch = verify(target)
    assert not mismatch.ok and "sha256 mismatch" in mismatch.reason
    assert mismatch.recorded_sha256 != mismatch.current_sha256
    # a failing result never gets a sidecar
    bad = check(FIXTURES / "bad_ids.csv", K)
    with pytest.raises(DiscloseUsageError, match="no sidecar"):
        write_sidecar(tmp_path / "bad_ids.csv", bad, K)
    with pytest.raises(DiscloseUsageError, match="no sidecar"):
        verify(tmp_path / "nothing.csv") if (tmp_path / "nothing.csv").write_text("a,n\n") else None


def test_cli_exit_codes_and_sidecar_flag(tmp_path: Path) -> None:
    runner = helpers.cli_runner()
    root = ["--data-root", str(tmp_path / "root")]
    try:
        refused = runner.invoke(app, [*root, "disclose", "check", str(FIXTURES / "bad_ids.csv")])
        assert refused.exit_code == 1 and "ID_COL" in refused.stdout, refused.output
        small = runner.invoke(
            app, [*root, "disclose", "check", str(FIXTURES / "bad_small_cell.md")]
        )
        assert small.exit_code == 1 and "SMALL_CELL" in small.stdout, small.output
        target = tmp_path / "good_aggregate.csv"
        shutil.copy(FIXTURES / "good_aggregate.csv", target)
        ok = runner.invoke(app, [*root, "disclose", "check", str(target), "--write-sidecar"])
        assert ok.exit_code == 0 and "PASS" in ok.stdout and "sidecar" in ok.stdout, ok.output
        assert disclose.sidecar_path(target).is_file()
        verified = runner.invoke(app, [*root, "disclose", "verify", str(target)])
        assert verified.exit_code == 0 and "OK" in verified.stdout
        target.write_bytes(target.read_bytes() + b"\n")
        edited = runner.invoke(app, [*root, "disclose", "verify", str(target), "--json"])
        assert edited.exit_code == 1 and json.loads(edited.stdout)["ok"] is False
        no_sidecar = runner.invoke(
            app, [*root, "disclose", "verify", str(FIXTURES / "bad_ids.csv")]
        )
        assert no_sidecar.exit_code == 2 and "no sidecar" in no_sidecar.stderr
        missing = runner.invoke(app, [*root, "disclose", "check", str(tmp_path / "nope.md")])
        assert missing.exit_code == 2 and "no such file" in missing.stderr
        # a failing artefact with --write-sidecar: exit 1, nothing written
        bad_copy = tmp_path / "bad_ids.csv"
        shutil.copy(FIXTURES / "bad_ids.csv", bad_copy)
        failed = runner.invoke(
            app, [*root, "disclose", "check", str(bad_copy), "--write-sidecar", "--json"]
        )
        assert failed.exit_code == 1 and not disclose.sidecar_path(bad_copy).exists()
        payload = json.loads(failed.stdout)
        assert payload["ok"] is False and payload["results"][0]["sidecar"] is None
        assert payload["results"][0]["checks"][0] == {
            "code": "ID_COL",
            "status": "fail",
            "detail": payload["results"][0]["findings"][0]["detail"],
        }
        # --k and --allow-text pass through
        labels = tmp_path / "labels.csv"
        pl.DataFrame({"label": ["z" * 70], "n": [5]}).write_csv(labels)
        lowered = runner.invoke(
            app, [*root, "disclose", "check", str(labels), "--k", "5", "--allow-text", "label"]
        )
        assert lowered.exit_code == 0, lowered.output
    finally:
        config.configure()


# ---------------------------------------------------------------------------
# 6. Retroactive checks (EP-33 amendment a) + docs + budget
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", [DICTIONARY, BENCHMARK_NOTE])
def test_retroactive_artefacts_pass_and_carry_committed_sidecars(path: Path) -> None:
    assert path.is_file()
    result = check(path, K)
    assert result.passed, [f.as_dict() for f in result.findings]
    text = path.read_text(encoding="utf-8")
    assert "sidecar pending" not in text.lower(), "the pending header was removed at EP-43"
    assert disclose.SIDECAR_SUFFIX in text, "the artefact names its sidecar"
    side = disclose.sidecar_path(path)
    assert side.is_file(), "the sidecar is committed beside the artefact"
    verified = verify(path)
    assert verified.ok, verified.reason
    payload = json.loads(side.read_text(encoding="utf-8"))
    assert payload["passed"] is True and payload["k"] == K and payload["reviewer"] == "owner"
    assert not re.search(r"(?<![\w.])[123]\d{7}(?![\w.])", side.read_text(encoding="utf-8"))


def test_docs_page_lists_every_code_with_an_example_and_the_sgt2_decision() -> None:
    text = DOCS_PAGE.read_text(encoding="utf-8")
    assert text.isascii()
    for code in CODES:
        assert f"`{code}`" in text, f"docs/methods/disclosure.md lacks {code}"
        assert re.search(rf"`{code}`.*?example", text, re.DOTALL | re.IGNORECASE)
    for needle in (
        "<11",
        "~1,000",
        ".disclosure.json",
        "sha256",
        "SGT-2",
        "complementary",
        "chain",
        "retrospective",
    ):
        assert needle in text, needle
    assert "--allow-text" in text and "mwh disclose verify" in text


def test_import_budget() -> None:
    helpers.assert_import_budget("mimicwarehouse.disclose", lazy=("mimicwarehouse.safe",))
    helpers.assert_import_budget(lazy=("mimicwarehouse.safe",))
