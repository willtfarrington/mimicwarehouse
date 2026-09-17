"""EP-48 — Attrition diagram renderer.

Fixture tier (default): crafted chains through ``cohort.build.suppress_attrition`` (the
EP-47 accessor's suppression) render as Mermaid with ``<11`` and no literal small count,
exact counts when nothing is small, the pair guard withholds a derivable units - subjects
difference (and the unguarded frame is what ``disclose.check`` refuses), withheld drops
beside banded totals render as the rounded released difference, the Mermaid text passes a
minimal grammar check (balanced quotes, unique ids, declared edge endpoints, escaping),
the Vega-Lite spec carries aggregate fields only and the PNG is a PNG, every artefact
passes ``disclose.check`` (the checker walks a layered spec as structure and still
refuses a small cell inside a nested record), ``save_attrition`` on the session fixture
lake writes the five files into the build run's folder and records them, refuses to
write when a file fails the gate, the CLI formats, the docs example is in sync and the
import budget. ``tier("dev")``: the artefacts of both seeds on dev pass the gate.

Everything asserted or printed is synthetic or suppressed aggregate text — never a row.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import polars as pl
import pytest

import helpers
from mimicwarehouse import config, disclose
from mimicwarehouse.cli import app
from mimicwarehouse.cohort import attrition as diagram
from mimicwarehouse.cohort import build as build_mod
from mimicwarehouse.cohort import registry as registry_mod
from mimicwarehouse.cohort.spec import CohortSpecError
from mimicwarehouse.disclose import DisclosureError

if TYPE_CHECKING:
    from mimicwarehouse.config import Settings

pytestmark = pytest.mark.ep_48

TRACER = "first_icu_adults@1.0.0"
HF = "hf_admissions@1.0.0"
K = 11
RUN_ID_RE = re.compile(r"\d{8}T\d{6}Z-[0-9a-f]{6}")
BAND_TOKEN = re.compile(r"(?<![\w.])[123]\d{7}(?![\w.])")
NODE_RE = re.compile(r'^\s{4}(?P<id>[A-Za-z]\w*)\["(?P<label>[^"]*)"\]$')
EDGE_RE = re.compile(r"^\s{4}(?P<a>\w+) (?P<arrow>-->|-\.->|~~~) (?P<b>\w+)$")
DOCS_MARK = ("<!-- attrition:begin -->", "<!-- attrition:end -->")
#: The prose columns of docs/methods/cohorts.md the gate admits as labels (the
#: acceptance command's --allow-text flags; none of them is derived from data).
DOCS_ALLOW_TEXT = ("meaning", "type", "default", "fields", "remarks", "predicate", "definition")


def _chain(
    units: list[int],
    subjects: list[int] | None = None,
    *,
    steps: list[str] | None = None,
    k: int = K,
) -> pl.DataFrame:
    """A crafted attrition frame the way the accessor produces it: raw counts through
    ``suppress_attrition`` (chain mode on both columns, the ``_small`` markers)."""
    subjects = subjects if subjects is not None else list(units)
    n = len(units)
    names = steps or ["base", "idx", *[f"crit_{i:02d}_c{i}" for i in range(1, n - 2)], "cohort"]
    polarity = ["population", "index", *["inclusion"] * (n - 3), "cohort"]
    rows = [
        {
            "step": name,
            "label": name,
            "polarity": pol,
            "kind": None,
            "custom": False,
            "n_units": u,
            "n_subjects": s,
        }
        for name, pol, u, s in zip(names, polarity, units, subjects, strict=True)
    ]
    suppressed, _report = build_mod.suppress_attrition(rows, k)
    return pl.DataFrame(suppressed)


def _parse_mermaid(text: str) -> tuple[dict[str, str], list[tuple[str, str, str]]]:
    """``(nodes, edges)`` after a minimal grammar check: a ``flowchart`` header, node
    declarations with balanced quotes and unique ids, edges between declared ids."""
    lines = text.rstrip("\n").split("\n")
    body = lines
    if lines[0] == "---":
        close = lines.index("---", 1)
        body = lines[close + 1 :]
    assert body[0].startswith("flowchart "), body[0]
    nodes: dict[str, str] = {}
    edges: list[tuple[str, str, str]] = []
    for line in body[1:]:
        assert line.count('"') % 2 == 0, line
        node = NODE_RE.match(line)
        if node:
            assert node.group("id") not in nodes, f"duplicate node id {node.group('id')}"
            nodes[node.group("id")] = node.group("label")
            continue
        edge = EDGE_RE.match(line)
        if edge:
            edges.append((edge.group("a"), edge.group("arrow"), edge.group("b")))
            continue
        assert line.startswith(("    classDef ", "    class ")), line
    for a, _arrow, b in edges:
        assert a in nodes and b in nodes, (a, b)
    return nodes, edges


# ---------------------------------------------------------------------------
# 1. Mermaid: small drops, exact chains, the grammar, escaping
# ---------------------------------------------------------------------------


def test_small_drop_renders_lt_k_and_never_the_literal() -> None:
    df = _chain([1000, 1000, 996, 996])  # a drop of 4 at the criterion step
    text = diagram.render_mermaid(df, k=K, ref="crafted@1.0.0", tier="fixture")
    assert "<11" in text and "n = <11" in text
    assert not re.search(r"n = 4\b", text)
    assert not re.search(r"(?<![\w,.#~])4(?![\w,])", text), "no literal 4 anywhere"
    assert "996" in text, "the exact cohort total stays exact"
    assert "~1,000" in text, "both neighbours of the small drop are banded"
    assert "suppressed" not in text, "units == subjects: the pair guard has nothing to do"
    nodes, edges = _parse_mermaid(text)
    assert set(nodes) == {"s0", "s1", "s2", "s3", "x1", "x2", "f"}
    assert "x3" not in nodes, "the cohort step excludes nothing by construction"
    assert ("s0", "-->", "s1") in edges and ("s1", "-.->", "x2") in edges
    assert ("s3", "~~~", "f") in edges, "the footer hangs under the last step"
    assert nodes["f"] == "crafted@1.0.0 - tier fixture - k = 11"
    # the withheld drop beside the banded idx total is the rounded released difference
    assert "units n = ~0" in nodes["x1"]
    chain = diagram.prepare(df, k=K)
    assert [r.dropped_units.kind if r.dropped_units else None for r in chain.rows] == [
        None,
        "approx",
        "small",
        None,
    ]
    assert df.get_column("dropped_units_small").to_list() == [False, False, True, False]
    assert not BAND_TOKEN.search(text)


def test_clean_chain_renders_exact_counts_and_skips_zero_drops() -> None:
    df = _chain([1000, 1000, 800, 800, 800])  # drops 0, 200, 0
    text = diagram.render_mermaid(df, ref="clean@1.0.0", tier="fixture", run_id=None)
    nodes, edges = _parse_mermaid(text)
    labels = "\n".join(nodes.values())
    assert "~" not in labels and "<11" not in labels and "suppressed" not in labels
    assert set(nodes) == {"s0", "s1", "s2", "s3", "s4", "x2", "f"}
    assert "units n = 1,000" in nodes["s0"] and "units n = 800" in nodes["s4"]
    assert nodes["x2"] == "Excluded at crit_01_c1<br/>units n = 200<br/>subjects n = 200"
    assert ("s1", "-.->", "x2") in edges
    # the drop cells behind it
    chain = diagram.prepare(df)
    assert [r.dropped_units.value if r.dropped_units else None for r in chain.rows] == [
        None,
        0,
        200,
        0,
        None,
    ]
    assert chain.rows[1].has_exclusion is False and chain.rows[2].has_exclusion is True


def test_mermaid_grammar_title_direction_and_escaping() -> None:
    df = _chain([500, 400, 300, 300])
    titled = diagram.render_mermaid(df, title='Chain "A" <test>', direction="LR", ref="t@1.0.0")
    assert titled.startswith('---\ntitle: "Chain \\"A\\" <test>"\n---\nflowchart LR\n')
    _parse_mermaid(titled)
    with pytest.raises(ValueError, match="direction"):
        diagram.render_mermaid(df, direction="XX")
    # user text is escaped with entity codes; the renderer's own <br/> and <11 are not
    rows = df.to_dicts()
    rows[2]["label"] = 'he said "<b>" #1 ' + "x" * 80
    text = diagram.render_mermaid(rows, k=K)
    nodes, _ = _parse_mermaid(text)
    assert "#quot;#lt;b#gt;#quot; #35;1" in nodes["s2"]
    assert "..." in nodes["s2"] and len(diagram.prepare(rows).rows[2].label) == 64
    assert diagram.escape_label('a"b<c>d#e') == "a#quot;b#lt;c#gt;d#35;e"
    # deterministic: the same frame renders the same text
    assert diagram.render_mermaid(df, ref="t@1.0.0") == diagram.render_mermaid(df, ref="t@1.0.0")
    # a frame without the chain columns, an empty frame
    with pytest.raises(diagram.AttritionRenderError, match="chain column"):
        diagram.render_mermaid(pl.DataFrame({"step": ["a"], "n": [1]}))
    with pytest.raises(diagram.AttritionRenderError, match="at least one step"):
        diagram.render_mermaid([])
    with pytest.raises(diagram.AttritionRenderError, match="expected"):
        diagram.render_mermaid("not a frame")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 2. The pair guard and the withheld-drop estimates
# ---------------------------------------------------------------------------


def test_pair_guard_withholds_a_derivable_units_subjects_difference(tmp_path: Path) -> None:
    df = _chain([100, 100, 60, 60], [97, 97, 60, 60])
    # what the accessor releases would be refused by the gate: 100 - 97 = 3 is derivable
    unguarded = pl.DataFrame({"n_units": [100, 100, 60, 60], "n_subjects": [97, 97, 60, 60]})
    findings = disclose.check_frame(unguarded, K)
    assert [f.code for f in findings] == ["SMALL_CELL"] and "difference" in findings[0].detail
    chain = diagram.prepare(df, k=K, ref="guard@1.0.0", tier="fixture")
    assert chain.guarded == (0, 1)
    assert [r.subjects.kind for r in chain.rows] == ["withheld", "withheld", "exact", "exact"]
    assert [r.units.kind for r in chain.rows] == ["exact"] * 4
    # the two subjects drops beside a withheld total are withheld too (else derivable)
    assert [r.dropped_subjects.kind if r.dropped_subjects else None for r in chain.rows] == [
        None,
        "withheld",
        "withheld",
        None,
    ]
    assert [r.dropped_units.value if r.dropped_units else None for r in chain.rows] == [
        None,
        0,
        40,
        None,
    ]
    text = diagram.render_mermaid(chain)
    assert "subjects n = suppressed" in text and "units n = 100" in text
    assert "<11" not in text, "nothing here is below k"
    # the CSV frame carries the guard as suppression markers and passes the gate
    frame = diagram.export_frame(chain)
    assert frame.get_column("n_subjects").to_list() == [None, None, 60, 60]
    assert frame.get_column("n_subjects_suppressed").to_list() == [True, True, False, False]
    assert frame.get_column("dropped_subjects_suppressed").to_list() == [False, True, True, False]
    assert frame.get_column("subjects_shown").to_list() == ["suppressed", "suppressed", "60", "60"]
    csv = tmp_path / "attrition.csv"
    csv.write_text(diagram.csv_text(chain), encoding="utf-8", newline="\n")
    assert disclose.check(csv, K).passed
    md = tmp_path / "attrition.md"
    md.write_text(diagram.render_markdown(chain), encoding="utf-8", newline="\n")
    assert disclose.check(md, K).passed
    report = md.read_text(encoding="utf-8")
    assert "| idx | index | idx | 100 | suppressed | n = 0 | n = suppressed |" in report
    assert "pair guard applied at step(s) 0, 1" in report


def test_withheld_drops_are_rounded_released_differences() -> None:
    # drops 5 (small), 15 (withheld beside bands), 5 (small), 0 (the cohort step)
    df = _chain([1000, 995, 980, 975, 975])
    chain = diagram.prepare(df, k=K)
    cells = [r.dropped_units for r in chain.rows]
    assert [c.kind if c else None for c in cells] == [None, "small", "approx", "small", None]
    assert cells[2] is not None and cells[2].text(K) == "~20"
    assert [r.units.text(K) for r in chain.rows] == ["~1,000", "~1,000", "~980", "~980", "975"]
    # a drop into a total below k: the released difference is the previous total
    low = _chain([1000, 5, 5, 5])
    low_chain = diagram.prepare(low, k=K)
    assert [r.units.text(K) for r in low_chain.rows] == ["1,000", "<11", "<11", "<11"]
    assert low_chain.rows[1].dropped_units is not None
    assert low_chain.rows[1].dropped_units.text(K) == "~1,000"
    assert low_chain.rows[2].dropped_units is not None
    assert low_chain.rows[2].dropped_units.kind == "small"
    # a frame without the EP-48 marker falls back to "both neighbours touched" = small
    legacy = df.drop("dropped_units_small", "dropped_subjects_small")
    legacy_chain = diagram.prepare(legacy, k=K)
    assert [r.dropped_units.kind if r.dropped_units else None for r in legacy_chain.rows] == [
        None,
        "small",
        "small",
        "small",
        None,
    ]
    # the five cell forms
    assert diagram.Cell(None, "withheld").text(K) == "suppressed"
    assert diagram.Cell(None, "small").text(K) == "<11"
    assert diagram.Cell(1234, "exact").text(K) == "1,234"
    assert diagram.Cell(1000, "banded").text(K) == "~1,000"
    assert diagram.Cell(20, "approx").text(K) == "~20"


# ---------------------------------------------------------------------------
# 3. Vega-Lite / PNG and the checker over a layered spec
# ---------------------------------------------------------------------------


def test_vegalite_spec_carries_aggregate_fields_only_and_passes_the_gate(
    tmp_path: Path,
) -> None:
    df = _chain([1000, 1000, 996, 996], steps=["base", "idx", "crit_01_adult", "cohort"])
    chart = diagram.render_altair(df, k=K, ref="crafted@1.0.0", tier="fixture")
    assert hasattr(chart, "to_json")
    spec = diagram.to_vegalite(df, k=K, ref="crafted@1.0.0", tier="fixture")
    assert spec["$schema"].startswith("https://vega.github.io/schema/vega-lite/")
    assert len(spec["layer"]) == 4 and spec["config"]["background"] == "#FFFFFF"
    assert "datasets" not in spec, "self-contained: the records are inline under data.values"
    values = spec["data"]["values"]
    allowed = {
        "order",
        "step",
        "label",
        "polarity",
        "custom",
        "n_units",
        "n_subjects",
        "units_shown",
        "subjects_shown",
        "counts",
        "dropped_units_shown",
        "dropped_subjects_shown",
        "exclusion",
    }
    assert all(set(v) == allowed for v in values)
    assert not any(disclose.is_identifier_name(key) for key in allowed)
    assert [v["n_units"] for v in values] == [1000, 1000, 1000, 996], "bands, never raw"
    assert [v["step"] for v in values] == ["base", "idx", "crit_01_adult", "cohort"]
    assert spec["layer"][0]["encoding"]["y"]["sort"] == ["base", "idx", "crit_01_adult", "cohort"]
    assert values[3]["exclusion"] == ""
    assert values[2]["exclusion"].startswith("excluded: units n = <11")
    # a withheld total is omitted from the bars (null), never zeroed
    low = diagram.records(_chain([1000, 5, 5, 5]), k=K)
    assert [r["n_units"] for r in low] == [1000, None, None, None]
    assert low[1]["counts"] == "units n = <11 - subjects n = <11"
    # the PNG and the gate over the pair (the PNG needs its source sibling)
    png = diagram.to_png(spec)
    assert png.startswith(b"\x89PNG\r\n\x1a\n") and len(png) > 1000
    (tmp_path / diagram.VEGALITE_FILE).write_text(json.dumps(spec), encoding="utf-8")
    (tmp_path / diagram.PNG_FILE).write_bytes(png)
    assert disclose.check(tmp_path / diagram.VEGALITE_FILE, K).passed
    assert disclose.check(tmp_path / diagram.PNG_FILE, K).passed
    # the checker walks the layered spec as structure (EP-48 amendment) but still
    # refuses a small cell in a record array whose records carry nested values
    nested = tmp_path / "nested.json"
    nested.write_text(json.dumps([{"g": "a", "n": 4, "tags": ["x"]}]), encoding="utf-8")
    codes = {f.code for f in disclose.check(nested, K).findings if f.status == "fail"}
    assert codes == {"SMALL_CELL"}
    keyed = tmp_path / "keyed.json"
    keyed.write_text(json.dumps([{"mark": {"type": "bar"}, "subject_id": [1]}]), encoding="utf-8")
    assert {f.code for f in disclose.check(keyed, K).findings} == {"ID_COL"}


# ---------------------------------------------------------------------------
# 4. save_attrition on the session fixture lake (+ refusals)
# ---------------------------------------------------------------------------


def test_save_attrition_writes_checked_files_into_the_run_folder(
    fixture_lake_settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mimicwarehouse.run import FIGURES_DIRNAME, read_manifest, run_dir

    settings = fixture_lake_settings
    saved = diagram.save_attrition(TRACER, "fixture", settings=settings)
    assert saved.ref == TRACER and saved.tier == "fixture" and saved.k == K and saved.recorded
    assert saved.run_id is not None
    assert saved.out_dir == run_dir(saved.run_id, settings) / FIGURES_DIRNAME
    assert tuple(saved.files) == diagram.ALL_FILES
    for name, path in saved.files.items():
        assert path.is_file() and path.name == name and not RUN_ID_RE.search(name)
        assert saved.checks[name].passed and disclose.check(path, K).passed, name
    manifest = read_manifest(saved.run_id, settings)
    assert {k: v for k, v in manifest.figures.items() if k in diagram.ALL_FILES} == {
        name: f"figures/{name}" for name in diagram.ALL_FILES
    }
    assert manifest.status == "ok", "the closed run stays closed"
    mermaid = saved.files[diagram.MERMAID_FILE].read_text(encoding="utf-8")
    result = build_mod.attrition(TRACER, "fixture", settings=settings)
    assert mermaid == diagram.render_mermaid(result)
    assert f"run {saved.run_id}" in mermaid and result.def_hash is not None
    assert result.def_hash[:12] in mermaid
    report = saved.files[diagram.MARKDOWN_FILE].read_text(encoding="utf-8")
    assert report.isascii()
    for needle in (
        f"# Attrition: {TRACER} (fixture)",
        f"**Claim type: {diagram.CLAIM_TYPE}.** MIMIC-IV analyses are retrospective.",
        "Disclosure: every count below passed",
        f"build run `{saved.run_id}`",
        "| step | polarity | label | n_units | n_subjects | units dropped | subjects dropped |",
        "```mermaid",
        "## What this does not claim",
        "4-hour ICU exclusion is a data-quality floor",
        "## Reproduction",
        "## Provenance",
    ):
        assert needle in report, needle
    png = saved.files[diagram.PNG_FILE].read_bytes()
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    spec = json.loads(saved.files[diagram.VEGALITE_FILE].read_text(encoding="utf-8"))
    assert spec["title"]["subtitle"] == diagram.from_attrition(result).footer()
    frame = pl.read_csv(saved.files[diagram.CSV_FILE])
    assert frame.get_column("step").to_list() == result.df.get_column("step").to_list()
    assert disclose.check_frame(frame, K) == []
    # by run id, a subset of formats, elsewhere: nothing recorded
    subset = diagram.save_attrition(
        saved.run_id, "fixture", tmp_path / "mmd", formats=["mermaid"], settings=settings
    )
    assert tuple(subset.files) == (diagram.MERMAID_FILE,) and not subset.recorded
    assert sorted(p.name for p in (tmp_path / "mmd").iterdir()) == [diagram.MERMAID_FILE]
    assert diagram.files_for(["altair", "mermaid"]) == (
        diagram.MERMAID_FILE,
        diagram.VEGALITE_FILE,
        diagram.PNG_FILE,
        diagram.CSV_FILE,
    )
    with pytest.raises(diagram.AttritionRenderError, match="unknown format"):
        diagram.files_for(["svg"])
    with pytest.raises(CohortSpecError, match="no built cohort"):
        diagram.save_attrition("nope@1.0.0", "fixture", tmp_path / "x", settings=settings)
    with pytest.raises(diagram.AttritionRenderError, match="direction"):
        diagram.save_attrition(TRACER, "fixture", tmp_path / "x", settings=settings, direction="Z")
    # a file that fails the gate refuses the whole write: nothing lands
    leaking = diagram.render_mermaid(result).replace("units n = <11", "units n = 4")
    monkeypatch.setattr(diagram, "render_mermaid", lambda *a, **kw: leaking)
    with pytest.raises(DisclosureError, match="SMALL_CELL") as excinfo:
        diagram.save_attrition(TRACER, "fixture", tmp_path / "refused", settings=settings)
    assert "nothing written" in str(excinfo.value) and not (tmp_path / "refused").exists()
    monkeypatch.undo()
    # vl-convert missing is a usage error naming the remedy
    monkeypatch.setitem(sys.modules, "vl_convert", None)
    with pytest.raises(diagram.AttritionRenderError, match="uv sync"):
        diagram.to_png(spec)
    with pytest.raises(diagram.AttritionRenderError, match="uv sync"):
        diagram.save_attrition(TRACER, "fixture", tmp_path / "nopng", settings=settings)
    assert not (tmp_path / "nopng").exists()


# ---------------------------------------------------------------------------
# 5. The CLI
# ---------------------------------------------------------------------------


def test_cli_formats(fixture_lake_settings: Settings, tmp_path: Path) -> None:
    runner = helpers.cli_runner()
    root = ["--data-root", str(fixture_lake_settings.data_root)]
    base = [*root, "cohort", "attrition", TRACER, "--tier", "fixture"]
    try:
        mermaid = runner.invoke(app, [*base, "--format", "mermaid"])
        assert mermaid.exit_code == 0, mermaid.output
        assert mermaid.stdout.startswith("flowchart TD\n")
        _parse_mermaid(mermaid.stdout)
        titled = runner.invoke(
            app, [*base, "--format", "mermaid", "--direction", "LR", "--title", "T", "--json"]
        )
        assert titled.exit_code == 0, titled.output
        payload = json.loads(titled.stdout)
        assert payload["format"] == "mermaid" and payload["ref"] == TRACER
        assert payload["mermaid"].startswith('---\ntitle: "T"\n---\nflowchart LR\n')
        altair = runner.invoke(app, [*base, "--format", "altair", "--json"])
        assert altair.exit_code == 0, altair.output
        spec = json.loads(altair.stdout)["vegalite"]
        assert spec["$schema"].startswith("https://vega.github.io/schema/vega-lite/")
        raw = runner.invoke(app, [*base, "--format", "altair"])
        assert raw.exit_code == 0 and json.loads(raw.stdout)["layer"]
        out = tmp_path / "cli"
        written = runner.invoke(app, [*base, "--format", "all", "--out", str(out)])
        assert written.exit_code == 0, written.output
        assert sorted(p.name for p in out.iterdir()) == sorted(diagram.ALL_FILES)
        assert "5 file(s)" in written.stdout and "disclose check PASS" in written.stdout
        as_json = runner.invoke(
            app, [*base, "--format", "altair", "--out", str(tmp_path / "vl"), "--json"]
        )
        assert as_json.exit_code == 0, as_json.output
        files = json.loads(as_json.stdout)["files"]
        assert set(files) == {diagram.VEGALITE_FILE, diagram.PNG_FILE, diagram.CSV_FILE}
        bad = runner.invoke(app, [*base, "--format", "svg"])
        assert bad.exit_code == 2 and "unknown --format" in bad.stderr
        no_out = runner.invoke(app, [*base, "--out", str(tmp_path / "t")])
        assert no_out.exit_code == 2 and "--out needs" in no_out.stderr
        bad_dir = runner.invoke(app, [*base, "--format", "mermaid", "--direction", "ZZ"])
        assert bad_dir.exit_code == 2 and "direction" in bad_dir.stderr
        missing = runner.invoke(
            app,
            [*root, "cohort", "attrition", "nope@1.0.0", "--tier", "fixture", "--format", "all"],
        )
        assert missing.exit_code == 1 and "no built cohort" in missing.stderr
        table = runner.invoke(app, base)
        assert table.exit_code == 0 and "k=11" in table.stdout, "the table is unchanged"
    finally:
        config.configure()
    helpers.assert_import_budget(
        "mimicwarehouse.cohort.attrition",
        lazy=("altair", "vl_convert", "mimicwarehouse.safe", "mimicwarehouse.run"),
    )
    helpers.assert_import_budget(
        "mimicwarehouse.cohort.cli",
        lazy=("mimicwarehouse.cohort.attrition", "mimicwarehouse.cohort.build", "altair"),
    )


# ---------------------------------------------------------------------------
# 6. The docs example is the fixture render (run id aside) and passes the gate
# ---------------------------------------------------------------------------


def test_docs_example_is_in_sync_and_passes_the_gate(
    fixture_lake_settings: Settings, tmp_path: Path
) -> None:
    path = registry_mod.methods_doc_path()
    text = path.read_text(encoding="utf-8")
    assert "## 8. Attrition diagram (EP-48)" in text and "## 9. What this does not claim" in text
    begin, end = DOCS_MARK
    block = text[text.index(begin) + len(begin) : text.index(end)].strip()
    assert block.startswith("```mermaid\n") and block.endswith("```")
    docs_mermaid = block[len("```mermaid\n") : -len("```")]
    fixture = build_mod.attrition(TRACER, "fixture", settings=fixture_lake_settings)
    rendered = diagram.render_mermaid(fixture)
    assert RUN_ID_RE.sub("<run>", docs_mermaid) == RUN_ID_RE.sub("<run>", rendered), (
        "docs/methods/cohorts.md section 8 drifted from the fixture render - regenerate it with "
        "`mwh cohort attrition first_icu_adults@1.0.0 --tier fixture --format mermaid`"
    )
    _parse_mermaid(docs_mermaid)
    assert "<11" in docs_mermaid and "~" in docs_mermaid, "the example shows the suppressed forms"
    # the page passes the gate with its prose-table columns admitted as labels (the
    # EP-46 schema reference's long `meaning` / `type` cells; the acceptance command)
    result = disclose.check(path, K, allow_text=DOCS_ALLOW_TEXT)
    assert result.passed, [f.as_dict() for f in result.findings]
    # ... and the EP-48 section passes on its own, with no allowance at all
    section = text[text.index("## 8. Attrition diagram") : text.index("## 9. What this")]
    section_path = tmp_path / "section.md"
    section_path.write_text(section, encoding="utf-8", newline="\n")
    assert disclose.check(section_path, K).passed
    assert not BAND_TOKEN.search(text)
    for needle in ("pair guard", "suppressed", "--format all", "attrition.vl.json", "vl-convert"):
        assert needle in text, needle
    disclosure_doc = helpers.WORKSPACE / "docs" / "methods" / "disclosure.md"
    assert "EP-48" in disclosure_doc.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 7. Dev tier (aggregates only)
# ---------------------------------------------------------------------------


@pytest.mark.tier("dev")
def test_dev_artefacts_pass_the_gate(dev_catalog: Path, tmp_path: Path) -> None:
    settings = config.load_settings()
    for ref in (TRACER, HF):
        try:
            saved = diagram.save_attrition(ref, "dev", tmp_path / ref, settings=settings)
        except CohortSpecError as exc:
            pytest.skip(f"{exc} (EP-47 builds it)")
        assert tuple(saved.files) == diagram.ALL_FILES and saved.k == K
        for name, path in saved.files.items():
            assert disclose.check(path, K).passed, name
        mermaid = saved.files[diagram.MERMAID_FILE].read_text(encoding="utf-8")
        assert not re.search(r"n = (?:[1-9]|10)(?![\d,])", mermaid), "no count below k"
        print(f"dev: {ref} -> {len(saved.files)} artefact(s) pass the gate; run {saved.run_id}")
