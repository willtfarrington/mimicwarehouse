"""EP-31 — tracer bullet: first-ICU-stay adults -> in-hospital mortality.

Acceptance on the fixture lake: attrition counts are non-increasing across the CTE steps
and the first equals ``count(*)`` of icustays; every descriptive table carries ``n`` /
``n_deaths`` and no identifier columns; the run folder holds all five files; the report
labels the claim type and states the analysis is retrospective; the audit grew by exactly
the number of ``actor = "tracer"`` safe_query calls; the model either produced an OR
table or the ``not_fit`` note. ``tier("dev")``-marked: the dev run completes,
``rows_suppressed`` is reported, and no run-folder file contains an identifier column
name or a string value longer than 64 characters.

Everything asserted here is counts, file shapes and metadata — the fixture data is the
committed synthetic tree (ids >= 90 000 000); the dev-tier test looks only at the
k-suppressed run-folder artefacts.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

import helpers
from mimicwarehouse import config
from mimicwarehouse.cli import app
from mimicwarehouse.safe import audit_path, safe_query
from mimicwarehouse.tracer import (
    CLAIM_TYPE,
    RETROSPECTIVE_SENTENCE,
    STEPS,
    VALUE_MAX_CHARS,
    TracerResult,
    run_tracer,
)

if TYPE_CHECKING:
    from mimicwarehouse.config import Settings

pytestmark = pytest.mark.ep_31

RUN_FILES = ("manifest.json", "attrition.json", "descriptives.json", "model.json", "report.md")
DESCRIPTIVE_TABLES = ("by_age_gender", "by_first_careunit")


def _tracer_audit_count(settings: Settings) -> int:
    path = audit_path(settings)
    if not path.is_file():
        return 0
    lines = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    return sum(1 for line in lines if line["actor"] == "tracer")


def _identifier_names() -> frozenset[str]:
    from mimicwarehouse.schema.contract import load_contract

    contract = load_contract()
    return frozenset(c.name for t in contract.tables for c in t.columns if c.identifier)


@pytest.fixture(scope="module")
def tracer_run(fixture_lake_settings: Settings) -> tuple[TracerResult, int]:
    """One fixture-tier run per module; returns (result, tracer-audit-line growth)."""
    before = _tracer_audit_count(fixture_lake_settings)
    result = run_tracer("fixture", settings=fixture_lake_settings)
    return result, _tracer_audit_count(fixture_lake_settings) - before


# ---------------------------------------------------------------------------
# 1. Attrition
# ---------------------------------------------------------------------------


def test_attrition_steps_non_increasing(
    tracer_run: tuple[TracerResult, int], fixture_lake_settings: Settings
) -> None:
    result, _ = tracer_run
    assert [row["step"] for row in result.attrition] == list(STEPS)
    counts = [row["n"] for row in result.attrition]
    assert all(isinstance(n, int) for n in counts), "no fixture step count may be suppressed"
    assert counts == sorted(counts, reverse=True), "attrition must be non-increasing"
    icustays = safe_query(
        "SELECT count(*) AS n FROM mimiciv_icu.icustays",
        tier="fixture",
        settings=fixture_lake_settings,
    )
    assert counts[0] == int(icustays.df["n"][0])


# ---------------------------------------------------------------------------
# 2. Descriptives
# ---------------------------------------------------------------------------


def test_descriptive_tables_shape(tracer_run: tuple[TracerResult, int]) -> None:
    result, _ = tracer_run
    identifiers = _identifier_names()
    expected_keys = {
        "by_age_gender": {"age_band", "gender", "n", "n_deaths"},
        "by_first_careunit": {"first_careunit", "n", "n_deaths"},
    }
    for name in DESCRIPTIVE_TABLES:
        table = result.descriptives[name]
        assert isinstance(table["rows_suppressed"], int) and table["rows_suppressed"] >= 0
        assert table["audit_id"]
        for row in table["rows"]:
            assert set(row) == expected_keys[name]
            assert not (set(row) & identifiers), "no identifier column may appear"
            assert row["n_deaths"] <= row["n"]


# ---------------------------------------------------------------------------
# 3. Run folder + report
# ---------------------------------------------------------------------------


def test_run_folder_has_all_five_files(tracer_run: tuple[TracerResult, int]) -> None:
    result, _ = tracer_run
    for name in RUN_FILES:
        assert (result.out_dir / name).is_file(), name
    manifest = json.loads((result.out_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["tier"] == "fixture"
    assert manifest["core_snapshot_id"], "manifest carries the queried catalog's snapshot id"
    assert manifest["run_id"] == result.out_dir.name
    assert manifest["wall_s"] >= 0


def test_report_labels_claim_type_and_retrospective(
    tracer_run: tuple[TracerResult, int],
) -> None:
    result, _ = tracer_run
    report = (result.out_dir / "report.md").read_text(encoding="utf-8")
    assert f"Claim type: {CLAIM_TYPE}" in report
    assert RETROSPECTIVE_SENTENCE in report
    assert "post-index" in report, "the report must state why ICU LOS is not a covariate"
    assert report.isascii()


# ---------------------------------------------------------------------------
# 4. Audit growth
# ---------------------------------------------------------------------------


def test_tracer_audit_lines_grew_by_call_count(tracer_run: tuple[TracerResult, int]) -> None:
    result, grew = tracer_run
    expected = len(STEPS) + len(DESCRIPTIVE_TABLES)  # one call per step + per table
    assert len(result.manifest["audit_ids"]) == expected
    assert grew == expected, "actor='tracer' audit lines must match the calls made"
    assert len(set(result.manifest["audit_ids"])) == expected


# ---------------------------------------------------------------------------
# 5. Model
# ---------------------------------------------------------------------------


def test_model_or_table_or_not_fit(tracer_run: tuple[TracerResult, int]) -> None:
    result, _ = tracer_run
    model = result.model
    # EP-33 (TST-4): the committed fixture is deterministic and fits — pinned so a
    # silent regression to not_fit fails instead of passing the either/or
    assert model["status"] == "fit", model.get("reason")
    assert model["n"] >= 0 and model["n_events"] >= 0
    if model["status"] == "fit":
        assert model["terms"], "a fit model reports an OR table"
        for term in model["terms"]:
            assert {"term", "odds_ratio", "ci_low", "ci_high"} <= set(term)
            assert term["ci_low"] <= term["ci_high"]
        assert 0.0 <= model["auc_in_sample"] <= 1.0
        # degenerate (zero-cell) levels are named and their rows excluded exactly
        assert model["n_fit"] + model.get("n_excluded", 0) == model["n"]
        assert model["n_events_fit"] <= model["n_events"]
    else:
        assert model["reason"], "not_fit records why"


# ---------------------------------------------------------------------------
# 6. CLI
# ---------------------------------------------------------------------------


def test_cli_tracer_fixture(fixture_lake_settings: Settings) -> None:
    runner = helpers.cli_runner()
    root = ["--data-root", str(fixture_lake_settings.data_root)]
    try:
        ok = runner.invoke(app, [*root, "tracer", "--tier", "fixture"])
        assert ok.exit_code == 0, ok.output
        assert "wrote" in ok.output and "model:" in ok.output

        missing_job = runner.invoke(app, [*root, "tracer", "--tier", "fixture", "--background"])
        assert missing_job.exit_code == 2
        assert "--job" in missing_job.output

        bad_tier = runner.invoke(app, [*root, "tracer", "--tier", "bogus"])
        assert bad_tier.exit_code == 2
    finally:
        config.configure()


# ---------------------------------------------------------------------------
# 7. Dev tier: run completes; run folder carries nothing row-like
# ---------------------------------------------------------------------------


def _assert_short_values(value: Any, where: str) -> None:
    if isinstance(value, str):
        assert len(value) <= VALUE_MAX_CHARS, f"{where}: string value over {VALUE_MAX_CHARS} chars"
    elif isinstance(value, dict):
        for key, inner in value.items():
            _assert_short_values(inner, f"{where}.{key}")
    elif isinstance(value, list):
        for i, inner in enumerate(value):
            _assert_short_values(inner, f"{where}[{i}]")


@pytest.mark.tier("dev")
def test_dev_run_completes_and_run_folder_is_clean(dev_catalog: Path) -> None:
    settings = config.load_settings()
    result = run_tracer("dev", settings=settings)
    assert result.out_dir.is_dir()
    for name in DESCRIPTIVE_TABLES:
        assert isinstance(result.descriptives[name]["rows_suppressed"], int), (
            "rows_suppressed must be reported on dev"
        )
    identifiers = _identifier_names()
    for path in sorted(result.out_dir.iterdir()):
        text = path.read_text(encoding="utf-8").casefold()
        for ident in identifiers:
            assert ident not in text, f"{path.name} mentions identifier column {ident!r}"
        if path.suffix == ".json":
            _assert_short_values(json.loads(path.read_text(encoding="utf-8")), path.name)
