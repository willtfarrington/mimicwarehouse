"""EP-53 — Capstone #1: concepts / QC case study (``mimicwarehouse.analyses.c01_concepts_qc``).

Fixture tier (default): the DAG spec is wired (the ``analyses.c01_concepts_qc`` python step
after the shared ``catalog`` step; the session lake ran it); ``build("fixture")`` runs end to
end on the session fixture lake inside a ``kind: report`` run and every table / figure it
writes passes ``disclose.check`` with its markers honoured; the table contents on the
synthetic fixture (the inventory's 65 concepts, the committed demo pins, the QC highlights,
the phenotype prevalence with Wilson intervals, the KDIGO levels, the 2x2, the measurement
teaser); the helpers (Wilson against statsmodels, the inventory tiers, the headline block
round trip, the Markdown renderer and its parser); ``promote`` writes every table as a
Markdown table and every figure with a sidecar (no CSV leaves the run folder: the owner's
EP-53 decision) and refuses a failing artefact; the EP-31 tracer folder promotes with
chain-mode / nested-total suppression (``suppress_tracer_payloads`` on crafted payloads
too); the committed case study (claim type, the retrospective sentence, both reader
guides, the required sections, every relative link resolves to a sidecar-carrying
artefact, the headline numbers equal the promoted table); the promoted tracer case
study; the docs index; the import budget.
``tier("dev")``: ``build("dev")`` completes on the real dev catalog. ``tier("full")``: the
run id the case study cites exists, is ``ok`` on full, and its tables reproduce the two
headline numbers.

Everything asserted or printed is counts, shares, labels, paths and synthetic values -
never a row.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

import polars as pl
import pytest
import yaml

import helpers
from mimicwarehouse import config, disclose, timesem, units
from mimicwarehouse import run as run_mod
from mimicwarehouse.analyses import c01_concepts_qc as c
from mimicwarehouse.cli import app
from mimicwarehouse.config import Settings
from mimicwarehouse.dag.spec import load_dag
from mimicwarehouse.disclose import MARKER_SUFFIX
from mimicwarehouse.tracer import run_tracer

pytestmark = pytest.mark.ep_53

K = 11
BAND_TOKEN = re.compile(r"(?<![\w.])[123]\d{7}(?![\w.])")
ANALYSES = helpers.WORKSPACE / "docs" / "analyses"
CASE_STUDY = ANALYSES / f"{c.CASE_STUDY_SLUG}.md"
ARTEFACTS = ANALYSES / c.CASE_STUDY_SLUG
TRACER_DOC = ANALYSES / f"{c.TRACER_SLUG}.md"
TRACER_ARTEFACTS = ANALYSES / c.TRACER_SLUG
REQUIRED_SECTIONS: tuple[str, ...] = (
    "## Question",
    "## Data & tiers",
    "## Method",
    "## Concept layer",
    "## Data quality",
    "## Phenotypes",
    "## Measurement process teaser",
    "## What it deliberately does not claim",
    "## Reproduction",
    "## Provenance",
    "## Limitations",
    "## Next",
)


@pytest.fixture(scope="module")
def capstone_run(fixture_lake_settings: Settings) -> str:
    return c.build("fixture", settings=fixture_lake_settings)


@pytest.fixture(scope="module")
def run_folder(capstone_run: str, fixture_lake_settings: Settings) -> Path:
    return run_mod.run_dir(capstone_run, fixture_lake_settings)


@pytest.fixture(scope="module")
def promoted(
    capstone_run: str, fixture_lake_settings: Settings, tmp_path_factory: pytest.TempPathFactory
) -> Path:
    dest = tmp_path_factory.mktemp("c01-promoted")
    c.promote(capstone_run, dest, settings=fixture_lake_settings)
    return dest


def _csv(folder: Path, name: str) -> pl.DataFrame:
    return pl.read_csv(folder / "tables" / f"{name}.csv")


def _assert_markers_honoured(frame: pl.DataFrame, where: str) -> None:
    for column in frame.columns:
        marker = f"{column}{MARKER_SUFFIX}"
        if marker not in frame.columns:
            continue
        hidden = frame.filter(pl.col(marker).fill_null(False))
        assert hidden.get_column(column).is_null().all(), (where, column)
        shown = frame.filter(~pl.col(marker).fill_null(False)).get_column(column).drop_nulls()
        if shown.dtype.is_numeric():
            assert not ((shown > 0) & (shown < K)).any(), (where, column)


# ---------------------------------------------------------------------------
# 1. Wiring
# ---------------------------------------------------------------------------


def test_spec_wired_and_session_lake_ran_the_step(fixture_lake_settings: Settings) -> None:
    dag = load_dag()
    step = dag.step(c.STEP_NAME)
    assert step.kind == "python" and step.target is None and step.params is None
    assert step.callable_name == "mimicwarehouse.analyses.c01_concepts_qc:run_step"
    assert set(step.tags) == {c.DAG_TAG, "capstone"}
    assert set(step.tiers) == {"fixture", "demo", "dev", "full"}
    assert list(step.depends_on) == ["catalog"]
    ordered = [s.name for s in dag.ordered(tier="fixture")]
    assert ordered.index("catalog") < ordered.index(c.STEP_NAME)
    assert [s.name for s in dag.ordered(tags=[c.DAG_TAG], tier="fixture")] == [c.STEP_NAME]
    path = Path(c.__file__).parents[1] / "dag" / "specs" / "analyses.yaml"
    raw = path.read_bytes()
    assert b"\r" not in raw and raw.decode("utf-8").isascii()
    assert not BAND_TOKEN.search(raw.decode("utf-8"))
    doc = yaml.safe_load(raw.decode("utf-8"))
    assert [s["name"] for s in doc["steps"]] == [c.STEP_NAME]
    # the session lake (every step, no tag filter) ran the capstone once already
    runs = run_mod.list_runs(fixture_lake_settings, kind=c.RUN_KIND)
    assert any(r.get("name") == c.RUN_NAME and r.get("status") == "ok" for r in runs)
    assert c.RUN_KIND in run_mod.RUN_KINDS


# ---------------------------------------------------------------------------
# 2. build("fixture"): the run, the artefacts, the gate
# ---------------------------------------------------------------------------


def test_build_fixture_run_and_artefacts_pass_the_gate(
    capstone_run: str, run_folder: Path, fixture_lake_settings: Settings
) -> None:
    assert run_mod.RUN_ID_RE.match(capstone_run)
    manifest = run_mod.read_manifest(capstone_run, fixture_lake_settings)
    assert manifest.name == c.RUN_NAME and manifest.kind == c.RUN_KIND
    assert manifest.status == "ok" and manifest.tier == "fixture"
    assert (
        manifest.claim_type == c.CLAIM_TYPE and run_mod.claim_label(c.CLAIM_TYPE) == "exploratory"
    )
    assert {"core", "derived"} <= set(manifest.snapshot_ids)
    assert set(manifest.tables) == set(c.TABLES) | {f"{t}.csv" for t in c.TABLES}
    assert set(manifest.figures) == set(c.FIGURES) | {f"{f}.vl" for f in c.FIGURES}
    assert len(manifest.sql) >= 10 and len(manifest.audit_ids) == len(manifest.sql)
    assert {r.kind for r in manifest.refs} >= {"mimic_code", "phenotype", "measurement_slice"}
    assert {r.name for r in manifest.refs if r.kind == "phenotype"} >= set(c.PHENOTYPE_IDS)
    assert manifest.params["k"] == K and manifest.params["tables"] == list(c.TABLES)
    assert manifest.params["inventory_tiers"] == ["fixture"]
    assert manifest.wall_s is not None and manifest.finished is not None
    files = sorted(p for sub in ("tables", "figures") for p in (run_folder / sub).iterdir())
    names = {p.name for p in files}
    for table in c.TABLES:
        assert {f"{table}.csv", f"{table}.parquet"} <= names
    for figure in c.FIGURES:
        assert {f"{figure}.vl.json", f"{figure}.png"} <= names
    identifiers = {"subject_id", "hadm_id", "stay_id", "note_id"}
    for path in files:
        result = disclose.check(path, k=K)
        assert result.passed, (path.name, [f.as_dict() for f in result.findings])
        if path.suffix == ".csv":
            text = path.read_text(encoding="utf-8")
            assert not BAND_TOKEN.search(text), path.name
            frame = pl.read_csv(path)
            assert not identifiers & set(frame.columns), path.name
            _assert_markers_honoured(frame, path.name)
    spec = json.loads((run_folder / "figures" / "phenotype_prevalence_by_era.vl.json").read_text())
    assert "values" in spec["data"] and "datasets" not in spec
    assert spec["config"]["range"]["category"], "the EP-5 theme is merged into the spec"
    assert any(
        r.get("kind") == "report" and r.get("run_id") == capstone_run
        for r in run_mod.read_ledger(fixture_lake_settings)
    )


def test_table_contents_on_the_fixture(run_folder: Path, fixture_lake_settings: Settings) -> None:
    inventory = _csv(run_folder, "concept_inventory")
    assert inventory.height == 65, "every vendored concept"
    assert {"concept", "concept_group", "upstream_commit", "patch_id", "status"} <= set(
        inventory.columns
    )
    assert {"n_rows_fixture", f"n_rows_fixture{MARKER_SUFFIX}", "wall_s", "peak_rss_mb"} <= set(
        inventory.columns
    )
    assert inventory.get_column("upstream_commit").n_unique() == 1
    assert (inventory.get_column("status") == "done").all()
    assert inventory.get_column("wall_s").drop_nulls().len() == 65, "the ledger has every step"
    pins = _csv(run_folder, "demo_pins_vs_tier")
    committed = c.demo_pins()
    assert pins.height == 65 and (pins.get_column("tier") == "fixture").all()
    for row in pins.to_dicts():
        expected = committed.get(row["concept"])
        if expected is not None and not row[f"n_pin_demo{MARKER_SUFFIX}"]:
            assert row["n_pin_demo"] == expected, row["concept"]
        if row["tier_to_demo_ratio"] is not None:
            assert row["n_pin_demo"] and row["n_rows_tier"] is not None
            assert row["tier_to_demo_ratio"] == pytest.approx(
                row["n_rows_tier"] / row["n_pin_demo"]
            )
    status = _csv(run_folder, "qc_status_by_table")
    assert {"checks_pass", "checks_warn", "checks_fail", "worst_status"} <= set(status.columns)
    assert status.height >= 30, "one row per profiled table"
    assert set(status.get_column("worst_status").to_list()) <= {"pass", "warn", "fail"}
    top = _csv(run_folder, "qc_top_checks")
    assert top.height <= c.TOP_CHECKS
    statuses = top.get_column("status").to_list()
    assert set(statuses) <= {"warn", "fail"}
    assert statuses == sorted(statuses, key=lambda s: c.QC_STATUS_ORDER.index(s))
    variants = _csv(run_folder, "unit_variants")
    assert variants.height == len(units.load_catalogue()) and "unit_strings" in variants.columns
    assert not any(str(v).startswith("shape:") for v in variants.get_column("unexpected_units"))
    implausible = _csv(run_folder, "implausible_values")
    assert implausible.height > 0 and "implausible_share" in implausible.columns
    ordering = _csv(run_folder, "timestamp_ordering")
    assert set(ordering.get_column("check_id").to_list()) <= set(c.ORDERING_CHECKS)
    prevalence = _csv(run_folder, "phenotype_prevalence")
    assert set(prevalence.get_column("phenotype").to_list()) <= set(c.PHENOTYPE_IDS)
    assert {"t2dm", "kdigo_aki"} <= set(prevalence.get_column("phenotype").to_list())
    for row in prevalence.to_dicts():
        if row["share"] is not None:
            assert row["ci_low"] <= row["share"] <= row["ci_high"]
            assert row["ci_low"] >= 0.0 and row["ci_high"] <= 1.0
            assert row["share"] == pytest.approx(row["n_positive"] / row["n_units"])
        else:
            assert row["ci_low"] is None and row["ci_high"] is None
    assert set(prevalence.get_column("scope").to_list()) <= set(timesem.ERAS) | {"all"}
    stages = _csv(run_folder, "kdigo_stage_distribution")
    assert set(stages.get_column("level").to_list()) <= set(c.KDIGO_LEVELS)
    agreement = _csv(run_folder, "sepsis_agreement_2x2")
    assert agreement.get_column("cell").to_list() == [
        "both",
        "sepsis3_only",
        "sepsis_explicit_only",
        "neither",
        "all",
    ]
    teaser = _csv(run_folder, "measurement_first24h_by_era")
    catalogue = units.load_catalogue()
    expected_items = {i for i in c.MP_ITEMIDS if i in catalogue.itemids()}
    assert set(teaser.get_column("itemid").to_list()) == expected_items
    assert set(teaser.get_column("era").to_list()) <= set(timesem.ERAS) | {"unknown"}
    for row in teaser.to_dicts():
        if row[f"n_stays_measured_first_24h{MARKER_SUFFIX}"] or row[f"n_stays{MARKER_SUFFIX}"]:
            assert row["measured_first_24h_share"] is None and row["ci_low"] is None
    print(
        f"fixture: {inventory.height} concepts, {status.height} QC tables, "
        f"{prevalence.height} prevalence rows, {teaser.height} teaser rows"
    )


# ---------------------------------------------------------------------------
# 3. Helpers
# ---------------------------------------------------------------------------


def test_wilson_interval_and_inventory_tiers() -> None:
    from statsmodels.stats.proportion import proportion_confint

    low, high = proportion_confint(20, 100, alpha=0.05, method="wilson")
    assert c.wilson_interval(20, 100) == pytest.approx((float(low), float(high)))
    assert c.wilson_interval(None, 100) is None and c.wilson_interval(3, 0) is None
    zero_low, zero_high = c.wilson_interval(0, 11) or (math.nan, math.nan)
    assert zero_low == 0.0 and 0.0 < zero_high < 0.3
    assert c.inventory_tiers("fixture") == ("fixture",)
    assert c.inventory_tiers("demo") == ("demo",)
    assert c.inventory_tiers("dev") == ("demo", "dev")
    assert c.inventory_tiers("full") == ("demo", "dev", "full")
    assert len(c.MP_ITEMIDS) == 10 and set(c.MP_ITEMIDS) <= set(units.load_catalogue().itemids())
    assert len(c.TABLES) == 11 and len(c.FIGURES) == 2


def test_headline_block_round_trip_and_markdown_renderer(promoted: Path, run_folder: Path) -> None:
    numbers = c.headline_numbers(promoted)
    assert set(numbers) <= set(c.FIGURE_PHENOTYPES)
    block = c.render_headline_block(numbers)
    assert block.isascii() and not BAND_TOKEN.search(block)
    parsed = c.parse_headline_block(f"{c.HEADLINE_MARK[0]}\n{block}{c.HEADLINE_MARK[1]}\n")
    assert set(parsed) == set(numbers)
    for pid, row in numbers.items():
        assert parsed[pid]["n_positive"] == row["n_positive"]
        assert parsed[pid]["n_units"] == row["n_units"]
        if row["share"] is not None:
            assert parsed[pid]["share_pct"] == pytest.approx(row["share"] * 100, abs=0.051)
    with pytest.raises(c.CapstoneError, match="markers"):
        c.parse_headline_block("no markers here")
    text = c.render_tables(promoted, k=K)
    assert not BAND_TOKEN.search(text)  # unit labels may carry a degree sign: not ASCII
    for table in c.TABLES:
        assert f"### {table}" in text
    assert f"{MARKER_SUFFIX} |" not in text, "marker columns fold into <k cells"
    assert "| 220045 |" in text, "itemids render as plain digits"
    limited = c.render_tables(run_folder / "tables", k=K, limit=3)
    assert "more row(s) in the CSV" in limited and len(limited) < len(text)
    assert "| # warn |" in limited and "checks_warn" not in limited, "the # header convention"
    back = c.md_table_frame(c.render_table(run_folder / "tables" / "qc_status_by_table.csv", k=K))
    assert "checks_warn" in back.columns, "the parser maps the header back"
    summary = c.render_summary(promoted)
    assert not BAND_TOKEN.search(summary)
    for needle in ("### Concept groups", "### Demo pins", "### QC", "### Sepsis 2x2", "65 in 9"):
        assert needle in summary, needle
    single = c.render_table(promoted / "phenotype_prevalence.md", k=K)
    assert single.startswith("| phenotype | version |")
    # the Markdown round trip: the promoted table parses back typed
    parsed = c.md_table_frame(promoted / "phenotype_prevalence.md")
    assert {"phenotype", "scope", "n_units", "n_positive", "share"} <= set(parsed.columns)
    assert parsed.get_column("n_units").dtype == pl.Int64
    assert parsed.get_column("share").dtype == pl.Float64
    with pytest.raises(c.CapstoneError, match="no Markdown table"):
        c.md_table_frame("no table here")
    assert c.parse_run_id("Run `20260901T000000Z-abcdef` - kind `report`, tier `full`") == (
        "20260901T000000Z-abcdef"
    )
    assert c.parse_run_id("nothing") is None
    assert c.relative_links("[a](x/y.csv) [b](https://h/z) [c](#anchor) [d](f.md#s)") == [
        "x/y.csv",
        "f.md",
    ]


# ---------------------------------------------------------------------------
# 4. Promotion through the gate
# ---------------------------------------------------------------------------


def test_promote_writes_every_artefact_with_a_sidecar(
    promoted: Path, capstone_run: str, fixture_lake_settings: Settings, tmp_path: Path
) -> None:
    names = {p.name for p in promoted.iterdir()}
    for table in c.TABLES:
        assert f"{table}.md" in names and f"{table}.md{disclose.SIDECAR_SUFFIX}" in names
        assert f"{table}.csv" not in names and f"{table}.parquet" not in names, (
            "CSV and Parquet twins stay in the run folder (owner decision, EP-53)"
        )
        frame = c.md_table_frame(promoted / f"{table}.md")
        assert (
            frame.height == _csv(run_mod.run_dir(capstone_run, fixture_lake_settings), table).height
        )
    for figure in c.FIGURES:
        for suffix in (".vl.json", ".png"):
            assert f"{figure}{suffix}" in names
            assert f"{figure}{suffix}{disclose.SIDECAR_SUFFIX}" in names
    for path in promoted.iterdir():
        if path.name.endswith(disclose.SIDECAR_SUFFIX):
            continue
        verdict = disclose.verify(path)
        assert verdict.ok, (path.name, verdict.reason)
        sidecar = disclose.read_sidecar(path)
        assert sidecar["k"] == K and sidecar["reviewer"] == "owner"
    # a run of another kind is refused; a failing artefact is never copied
    tracer = run_tracer("fixture", settings=fixture_lake_settings)
    with pytest.raises(c.CapstoneError, match="not a c01_concepts_qc"):
        c.promote(tracer.manifest["ledger_run_id"], tmp_path / "x", settings=fixture_lake_settings)
    bad = tmp_path / "bad.csv"
    pl.DataFrame({"era": ["a", "b"], "n_units": [5, 40]}).write_csv(bad)
    with pytest.raises(c.CapstoneError, match="refused by the disclosure gate"):
        c._promote_file(bad, tmp_path / "out" / "bad.csv", K, git_sha=None)
    assert not (tmp_path / "out" / "bad.csv").exists()
    with pytest.raises(run_mod.RunLedgerError):
        c.promote("20000101T000000Z-000000", tmp_path / "y", settings=fixture_lake_settings)


def test_tracer_promotion_on_the_fixture(fixture_lake_settings: Settings, tmp_path: Path) -> None:
    tracer = run_tracer("fixture", settings=fixture_lake_settings)
    dest_md = tmp_path / "docs" / f"{c.TRACER_SLUG}.md"
    dest_dir = tmp_path / "docs" / c.TRACER_SLUG
    paths = c.promote_tracer(
        tracer.run_id, settings=fixture_lake_settings, dest_md=dest_md, dest_dir=dest_dir
    )
    assert [p.name for p in paths] == [dest_md.name, *c.TRACER_JSON_FILES]
    for path in paths:
        assert disclose.verify(path).ok, path.name
    text = dest_md.read_text(encoding="utf-8")
    assert text.isascii() and "\r" not in text and not BAND_TOKEN.search(text)
    for needle in (
        "**Claim type: associational (exploratory).**",
        c.RETROSPECTIVE_SENTENCE,
        "*Reader guide (DS/ML):*",
        "*Reader guide (clinical informatics):*",
        f"Run `{tracer.run_id}`",
        f"ledger run `{tracer.manifest['ledger_run_id']}`",
        "## Question",
        "## Cohort",
        "## Model",
        "## Reproduction (run ledger, EP-35)",
        "## Provenance (run ledger, EP-35)",
        f"[attrition.json]({c.TRACER_SLUG}/attrition.json)",
    ):
        assert needle in text, needle
    steps = json.loads((dest_dir / "attrition.json").read_text(encoding="utf-8"))["steps"]
    assert steps and {"step", "n", "n_suppressed", "n_banded"} <= set(steps[0])
    assert [s["step"] for s in steps] == [s["step"] for s in tracer.attrition]
    with pytest.raises(c.CapstoneError, match="not a tracer run stamp"):
        c.promote_tracer("20260901T000000Z-abcdef", settings=fixture_lake_settings)
    with pytest.raises(c.CapstoneError, match="no tracer report"):
        c.promote_tracer("20000101T000000-fixture", settings=fixture_lake_settings)


def test_suppress_tracer_payloads_on_crafted_values() -> None:
    att = [{"step": s, "n": n, "audit_id": "x"} for s, n in (("a", 100), ("b", 95), ("c", 40))]
    desc = {
        "k": K,
        "by_first_careunit": {
            "rows": [
                {"first_careunit": "u1", "n": 30, "n_deaths": 25},
                {"first_careunit": "u2", "n": 60, "n_deaths": 20},
                {"first_careunit": "u3", "n": 80, "n_deaths": 40},
            ],
            "rows_suppressed": 0,
            "audit_id": "y",
        },
    }
    model = {"status": "fit", "n": 100, "n_fit": 97, "n_events": 30, "n_events_fit": 30}
    steps, desc_out, model_out = c.suppress_tracer_payloads(att, desc, model, K)
    by_step = {s["step"]: s for s in steps}
    assert by_step["b"]["n_banded"] and by_step["a"]["n_banded"], "the 5-drop bands both"
    assert by_step["a"]["n"] == 100 and by_step["b"]["n"] == 100, "banded to the nearest 10"
    assert by_step["c"]["n"] == 40 and not by_step["c"]["n_banded"]
    assert att[0]["n"] == 100 and "n_banded" not in att[0], "inputs untouched"
    rows = {r["first_careunit"]: r for r in desc_out["by_first_careunit"]["rows"]}
    assert rows["u1"]["n_deaths_suppressed"] or rows["u1"]["n_suppressed"], (
        "30 - 25 = 5 survivors: the nested pair loses a cell"
    )
    assert model_out["n_fit"] is None and model_out["n_fit_suppressed"]
    assert model_out["n_events_fit"] == 30 and "n_events_fit_suppressed" not in model_out
    assert model["n_fit"] == 97
    # no drop below k: nothing banded, nothing hidden
    clean, _, clean_model = c.suppress_tracer_payloads(
        [{"step": "a", "n": 100}, {"step": "b", "n": 50}], {"k": K}, {"n": 50, "n_fit": 50}, K
    )
    assert all(not s["n_banded"] and not s["n_suppressed"] for s in clean)
    assert clean_model["n_fit"] == 50


# ---------------------------------------------------------------------------
# 5. The committed case studies and the docs index
# ---------------------------------------------------------------------------


def _resolved_links(doc: Path) -> list[Path]:
    text = doc.read_text(encoding="utf-8")
    return [(doc.parent / target).resolve() for target in c.relative_links(text)]


def test_case_study_document_and_artefacts() -> None:
    assert CASE_STUDY.is_file(), "docs/analyses/01-concepts-and-qc.md (EP-53)"
    text = CASE_STUDY.read_text(encoding="utf-8")
    assert "\r" not in text and not BAND_TOKEN.search(text)
    for needle in (
        "**Claim type: exploratory",
        c.RETROSPECTIVE_SENTENCE,
        "*Reader guide (DS/ML):*",
        "*Reader guide (clinical informatics):*",
        "For ML/DS readers",
        "For clinical-informatics readers",
        c.HEADLINE_MARK[0],
        c.HEADLINE_MARK[1],
        "docs/resources/concepts.md",
        "docs/methods/phenotypes.md",
    ):
        assert needle in text, needle
    positions = [text.index(section) for section in REQUIRED_SECTIONS]
    assert positions == sorted(positions), "the EP-32 sections, in order"
    assert disclose.verify(CASE_STUDY).ok, "the case study carries a current sidecar"
    assert disclose.check(CASE_STUDY, k=K).passed
    links = _resolved_links(CASE_STUDY)
    assert links, "the case study links its tables and figures"
    for target in links:
        assert target.exists(), target
        if target.is_file() and target.parent == ARTEFACTS.resolve():
            verdict = disclose.verify(target)
            assert verdict.ok, (target.name, verdict.reason)
    for table in c.TABLES:
        assert (ARTEFACTS / f"{table}.md").is_file(), table
        assert disclose.verify(ARTEFACTS / f"{table}.md").ok, table
        assert not (ARTEFACTS / f"{table}.csv").exists(), "no CSV enters git (EP-53 decision)"
    for figure in c.FIGURES:
        assert f"{c.CASE_STUDY_SLUG}/{figure}.png" in text, "the PNGs are embedded"
        for suffix in (".vl.json", ".png"):
            assert disclose.verify(ARTEFACTS / f"{figure}{suffix}").ok, figure
    run_id = c.parse_run_id(text)
    assert run_id is not None and run_mod.RUN_ID_RE.match(run_id)
    quoted = c.parse_headline_block(text)
    numbers = c.headline_numbers(ARTEFACTS)
    assert set(quoted) == set(numbers) == set(c.FIGURE_PHENOTYPES)
    for pid, row in numbers.items():
        assert quoted[pid]["n_positive"] == row["n_positive"], pid
        assert quoted[pid]["n_units"] == row["n_units"], pid
        assert quoted[pid]["share_pct"] == pytest.approx(row["share"] * 100, abs=0.051), pid
    for path in ARTEFACTS.iterdir():
        if not path.name.endswith(disclose.SIDECAR_SUFFIX):
            assert disclose.check(path, k=K).passed, path.name


def test_tracer_case_study_document() -> None:
    assert TRACER_DOC.is_file(), "docs/analyses/02-tracer-first-icu-mortality.md (EP-53)"
    text = TRACER_DOC.read_text(encoding="utf-8")
    assert text.isascii() and "\r" not in text and not BAND_TOKEN.search(text)
    for needle in (
        "**Claim type: associational (exploratory).**",
        c.RETROSPECTIVE_SENTENCE,
        "*Reader guide (DS/ML):*",
        "*Reader guide (clinical informatics):*",
        "tier `full`",
        "ledger run `",
        "## Reproduction (run ledger, EP-35)",
    ):
        assert needle in text, needle
    assert disclose.verify(TRACER_DOC).ok and disclose.check(TRACER_DOC, k=K).passed
    for name in c.TRACER_JSON_FILES:
        path = TRACER_ARTEFACTS / name
        assert path.is_file() and disclose.verify(path).ok, name
    for target in _resolved_links(TRACER_DOC):
        assert target.exists(), target


def test_docs_index_and_status_surfaces() -> None:
    index = (ANALYSES / "README.md").read_text(encoding="utf-8")
    assert f"{c.CASE_STUDY_SLUG}.md" in index and f"{c.TRACER_SLUG}.md" in index
    assert "EP-53" in index
    for doc in ("README.md", "DESIGN.md"):
        assert "EP-53" in (helpers.WORKSPACE / doc).read_text(encoding="utf-8"), doc
    brief = helpers.REPO_ROOT / "roadmap" / "EP-53-capstone-1-concepts-qc.md"
    assert brief.is_file()


def test_cli_entry_point_and_import_budget(promoted: Path) -> None:
    assert c.main(["headline", "--dir", str(promoted)]) == 0
    assert c.main(["tables", "--dir", str(promoted), "--limit", "2"]) == 0
    assert c.main(["summary", "--dir", str(promoted)]) == 0
    assert c.main(["promote", "--run", "20000101T000000Z-000000"]) != 0
    runner = helpers.cli_runner()
    try:
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
    finally:
        config.configure()
    helpers.assert_import_budget(
        "mimicwarehouse.cli",
        lazy=(
            "mimicwarehouse.analyses",
            "mimicwarehouse.analyses.c01_concepts_qc",
            "statsmodels",
            "altair",
        ),
    )


# ---------------------------------------------------------------------------
# 6. Dev and full tiers (opt-in)
# ---------------------------------------------------------------------------


@pytest.mark.tier("dev")
def test_dev_build_completes(dev_catalog: Path) -> None:
    settings = config.load_settings()
    run_id = c.build("dev", settings=settings)
    manifest = run_mod.read_manifest(run_id, settings)
    assert manifest.status == "ok" and manifest.tier == "dev"
    assert manifest.params["inventory_tiers"] == ["demo", "dev"]
    folder = run_mod.run_dir(run_id, settings)
    prevalence = _csv(folder, "phenotype_prevalence")
    overall = {r["phenotype"]: r for r in prevalence.filter(pl.col("scope") == "all").to_dicts()}
    assert set(overall) == set(c.PHENOTYPE_IDS), "every phenotype released on dev"
    for row in overall.values():
        assert row["n_units"] >= K and row["share"] is not None
    for path in (folder / "tables").glob("*"):
        assert disclose.check(path, k=K).passed, path.name
    print(f"dev: run {run_id}, wall {manifest.wall_s} s")


@pytest.mark.tier("full")
def test_full_run_cited_by_the_case_study_reproduces_the_headlines(full_catalog: Path) -> None:
    settings = config.load_settings()
    text = CASE_STUDY.read_text(encoding="utf-8")
    run_id = c.parse_run_id(text)
    assert run_id is not None
    manifest = run_mod.read_manifest(run_id, settings)
    assert manifest.status == "ok" and manifest.tier == "full"
    assert manifest.name == c.RUN_NAME and manifest.kind == c.RUN_KIND
    folder = run_mod.run_dir(run_id, settings)
    numbers = c.headline_numbers(folder / "tables")
    quoted = c.parse_headline_block(text)
    for pid, row in numbers.items():
        assert quoted[pid]["n_positive"] == row["n_positive"], pid
        assert quoted[pid]["n_units"] == row["n_units"], pid
    promoted_numbers = c.headline_numbers(ARTEFACTS)
    assert promoted_numbers == numbers, "the promoted Markdown table is the run's CSV"
    print(f"full: run {run_id} reproduces {len(numbers)} headline number(s)")


def _crafted_frame_passes(frame: pl.DataFrame) -> bool:
    return not [f for f in disclose.check_frame(frame, K) if f.status == disclose.FAIL]


def test_helpers_never_release_a_small_cell() -> None:
    """The private frame helpers on crafted counts: a 5 hides, its neighbour follows."""
    frame = pl.DataFrame(
        {
            "phenotype": ["p", "p", "p"],
            "scope": ["all", "e1", "e2"],
            "n_units": [100, 95, 5],
            "n_positive": [50, 48, 2],
        }
    )
    out = c._suppress(
        frame, K, count_cols=["n_units", "n_positive"], group_cols=["phenotype", "scope"]
    )
    assert out.filter(pl.col("scope") == "e2").get_column("n_units_suppressed").all()
    assert _crafted_frame_passes(out)
    with_ci = c._with_interval(out, positive="n_positive", units="n_units")
    e2: dict[str, Any] = with_ci.filter(pl.col("scope") == "e2").to_dicts()[0]
    assert e2["share"] is None and e2["ci_low"] is None
    assert c._cut("x" * 100) is not None and len(c._cut("x" * 100) or "") == c.VALUE_MAX_CHARS
    assert c._cut(None) is None
