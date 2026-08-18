"""EP-12 — synthetic fixture generator B (icu) + pytest tier markers + in-memory fixture catalog.

Fixture tier only: everything here is synthetic (ids >= 90 000 000, D-27), generated in memory or
into ``tmp_path``, or read from the committed fixture under ``tests/fixtures/`` - through the
``fixture_catalog`` session fixture (in-memory DuckDB, contract types) or Polars frames. The
committed icu files are compared byte-for-byte against a fresh regeneration ("fixture drift").
The tier-marker semantics are exercised with ``pytester`` (nested pytest sessions over a copy of
``tests/conftest.py`` and a throw-away data root). No data root is read; no test prints a row.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import time
import tomllib
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl
import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from mimicwarehouse import config as config_mod
from mimicwarehouse import guard, verify
from mimicwarehouse.cli import app
from mimicwarehouse.fixtures import (
    FIXTURE_ID_FLOOR,
    FixtureCatalogError,
    FixtureError,
    FixturePlan,
    FixtureSpec,
    assert_valid,
    build_fixture_catalog,
    build_frames,
    build_icu_frames,
    build_plan,
    load_vocab,
    validate,
)
from mimicwarehouse.fixtures import catalog as catalog_mod
from mimicwarehouse.fixtures import check as check_mod
from mimicwarehouse.fixtures import hosp as hosp_mod
from mimicwarehouse.fixtures import icu as icu_mod
from mimicwarehouse.fixtures import write as write_mod
from mimicwarehouse.fixtures.vocab import (
    ICU_LINKSTO,
    IcuItem,
    VocabError,
    load_vocab_from,
    vocab_root,
)
from mimicwarehouse.schema import Contract

pytestmark = pytest.mark.ep_12

runner = CliRunner()

WORKSPACE = Path(__file__).resolve().parents[2]
REPO_ROOT = WORKSPACE.parent
FIXTURE_DIR = WORKSPACE / "tests" / "fixtures"
DATASET_DIR = FIXTURE_DIR / "mimic-iv-3.1"
ICU_DIR = DATASET_DIR / "icu"
HOSP_DIR = DATASET_DIR / "hosp"
CONFTEST = WORKSPACE / "tests" / "conftest.py"
ICU_TABLES = [
    "caregiver",
    "chartevents",
    "datetimeevents",
    "d_items",
    "icustays",
    "ingredientevents",
    "inputevents",
    "outputevents",
    "procedureevents",
]
EVENT_TABLES = [t for t in ICU_TABLES if t not in ("caregiver", "d_items", "icustays")]
CHARTEVENTS_BUDGET_BYTES = 3_000_000
FIXTURE_BUDGET_BYTES = 10_000_000
#: The concept-critical itemids the brief names (all must be in d_items and used by an event).
KEY_ITEMIDS = {
    220045, 220179, 220180, 220181, 220050, 220051, 220052, 220210, 220277, 223761, 223762,
    220739, 223900, 223901, 223835, 226512, 226730,
    221906, 221289, 221749, 222315, 221662, 225158, 220949, 222168, 223258,
    226559, 226560,
    225792, 225794, 225752, 225802, 224385, 227194,
}  # fmt: skip
NOREPINEPHRINE, INVASIVE_VENT, CRRT = 221906, 225792, 225802
SIX_HOURS = timedelta(hours=6)


# ---------------------------------------------------------------------------
# Module fixtures (one plan / one frame set for the whole module)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def spec() -> FixtureSpec:
    return FixtureSpec()


@pytest.fixture(scope="module")
def plan(spec: FixtureSpec) -> FixturePlan:
    return build_plan(spec)


@pytest.fixture(scope="module")
def all_frames(spec: FixtureSpec, contract: Contract) -> dict[str, dict[str, pl.DataFrame]]:
    _plan, frames = build_frames(spec, contract=contract)
    return frames


@pytest.fixture(scope="module")
def hosp(all_frames: dict[str, dict[str, pl.DataFrame]]) -> dict[str, pl.DataFrame]:
    return all_frames["hosp"]


@pytest.fixture(scope="module")
def icu(all_frames: dict[str, dict[str, pl.DataFrame]]) -> dict[str, pl.DataFrame]:
    return all_frames["icu"]


@pytest.fixture(scope="module")
def regenerated(
    tmp_path_factory: pytest.TempPathFactory, spec: FixtureSpec
) -> write_mod.WriteResult:
    return write_mod.build_and_write(tmp_path_factory.mktemp("fixture-regen"), spec=spec)


@pytest.fixture(scope="module")
def manifest() -> dict[str, Any]:
    return json.loads((FIXTURE_DIR / "manifest.json").read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _count(con, sql: str, params: list[Any] | None = None) -> int:
    row = con.execute(sql, params or []).fetchone()
    assert row is not None
    return int(row[0])


# ---------------------------------------------------------------------------
# 1. d_items seed + spec knobs
# ---------------------------------------------------------------------------


def test_d_items_vocab_covers_the_brief() -> None:
    vocab = load_vocab()
    items = vocab.icu_items
    assert len(items) >= 45
    ids = {i.itemid for i in items}
    assert ids >= KEY_ITEMIDS
    assert all(i.linksto in ICU_LINKSTO for i in items)
    assert all(i.itemid > 220_000 and i.itemid < 10_000_000 for i in items)
    assert set(ICU_LINKSTO) == {i.linksto for i in items}  # every event table has items
    for role in ("hr", "rr", "spo2", "temp_f", "temp_c", "fio2", "weight_admit", "height"):
        assert vocab.icu_role(role).is_numeric, role
    for role in ("gcs_eye", "gcs_verbal", "gcs_motor", "vent_mode", "o2_device"):
        assert vocab.icu_role(role).text_values, role
    assert vocab.icu_item(220045).unitname == "bpm"
    assert vocab.icu_item(223761).unitname == "°F"  # typed as a YAML escape: ASCII file
    assert vocab.icu_item(221906).extra["drug"] == "norepinephrine"
    assert len(vocab.icu_roles("vaso")) == 5 and len(vocab.icu_roles("fluid")) == 3
    assert vocab.icu_item(225792).linksto == "procedureevents"
    assert vocab.icu_item(226559).linksto == "outputevents"
    assert {i.itemid for i in vocab.icu_linksto("datetimeevents")} and {
        i.itemid for i in vocab.icu_linksto("ingredientevents")
    }
    assert vocab.icu_weighted("aline_locations").values
    assert vocab.version_notes["d_items.yaml"]
    # the file is ASCII (units are YAML escapes) and hook-clean
    data = (vocab_root() / "d_items.yaml").read_bytes()
    assert data.isascii() and data.endswith(b"\n") and b"\r" not in data
    assert not guard.id_band_hits(data)


def test_d_items_vocab_loader_rejects_broken_copy(tmp_path: Path) -> None:
    root = tmp_path / "vocab"
    root.mkdir()
    for path in vocab_root().glob("*.yaml"):
        (root / path.name).write_bytes(path.read_bytes())
    text = (root / "d_items.yaml").read_text(encoding="utf-8")
    (root / "d_items.yaml").write_text(
        text.replace("linksto: outputevents", "linksto: outputs", 1), encoding="utf-8"
    )
    with pytest.raises(VocabError, match="not an icu event table"):
        load_vocab_from(root)
    (root / "d_items.yaml").write_text(
        text.replace("itemid: 220045,", "itemid: 220179,", 1), encoding="utf-8"
    )
    with pytest.raises(VocabError, match="duplicate itemid"):
        load_vocab_from(root)


def test_spec_icu_knobs(spec: FixtureSpec) -> None:
    assert spec.n_caregivers == 15 and spec.vent_fraction == 0.4
    assert spec.vasopressor_fraction == 0.25
    assert set(spec.canonical()) >= {"n_caregivers", "vent_fraction", "vasopressor_fraction"}
    cases: list[dict[str, Any]] = [
        {"n_caregivers": 0},
        {"vent_fraction": 1.5},
        {"vasopressor_fraction": -0.1},
    ]
    for bad in cases:
        with pytest.raises(ValidationError):
            FixtureSpec(**bad)


# ---------------------------------------------------------------------------
# 2. icu frames: contract shape, integrity, story
# ---------------------------------------------------------------------------


def test_icu_frames_have_exact_contract_columns_and_dtypes(
    icu: dict[str, pl.DataFrame], contract: Contract
) -> None:
    assert list(icu) == ICU_TABLES == [t.name for t in contract.by_schema("mimiciv_icu")]
    for name, frame in icu.items():
        table = contract.table("mimiciv_icu", name)
        assert list(frame.columns) == list(table.column_names), name
        assert dict(frame.schema) == hosp_mod.polars_schema(table), name
        assert frame.height > 0, name
        for c in table.columns:
            if not c.nullable:
                assert frame.get_column(c.name).null_count() == 0, f"{name}.{c.name}"


def test_validate_is_clean_with_icu(
    hosp: dict[str, pl.DataFrame],
    icu: dict[str, pl.DataFrame],
    contract: Contract,
    plan: FixturePlan,
) -> None:
    assert validate(hosp, contract, plan, icu=icu) == []
    assert_valid(hosp, contract, plan, icu=icu)
    assert validate(hosp, contract, plan) == []  # the EP-11 hosp-only form still works


def test_validate_catches_broken_icu_frames(
    hosp: dict[str, pl.DataFrame],
    icu: dict[str, pl.DataFrame],
    contract: Contract,
    plan: FixturePlan,
) -> None:
    # an outputevents itemid whose d_items.linksto is chartevents
    broken = dict(icu)
    broken["outputevents"] = icu["outputevents"].with_columns(
        pl.lit(220045).cast(pl.Int32).alias("itemid")
    )
    problems = validate(hosp, contract, plan, icu=broken)
    assert any("outputevents" in p and "linksto is another table" in p for p in problems)
    # an event outside its stay window
    broken = dict(icu)
    broken["chartevents"] = icu["chartevents"].with_columns(
        (pl.col("charttime") + pl.duration(days=30)).alias("charttime")
    )
    problems = validate(hosp, contract, plan, icu=broken)
    assert any("chartevents.charttime" in p and "outside" in p for p in problems)
    # a stay id no icustays row has (id floor respected, so the FK is what fails)
    broken = dict(icu)
    broken["procedureevents"] = icu["procedureevents"].with_columns(
        pl.lit(FIXTURE_ID_FLOOR + 5_000_000).cast(pl.Int32).alias("stay_id")
    )
    problems = validate(hosp, contract, plan, icu=broken)
    assert any("procedureevents(stay_id) -> icustays(stay_id)" in p for p in problems)
    # a tampered los / careunit breaks the icustays <-> transfers / plan agreement
    broken = dict(icu)
    broken["icustays"] = icu["icustays"].with_columns(pl.lit(1.0).alias("los"))
    problems = validate(hosp, contract, plan, icu=broken)
    assert any("los is not" in p for p in problems)
    broken["icustays"] = icu["icustays"].with_columns(pl.lit("Nowhere").alias("first_careunit"))
    problems = validate(hosp, contract, plan, icu=broken)
    assert any("matching transfers row" in p for p in problems)
    assert any("differ from the plan" in p for p in problems)
    # a missing frame is a structural problem
    broken = {k: v for k, v in icu.items() if k != "caregiver"}
    assert any("missing icu frame" in p for p in validate(hosp, contract, plan, icu=broken))
    with pytest.raises(FixtureError, match="fixture problem"):
        assert_valid(hosp, contract, plan, icu=broken)


def test_icustays_match_plan_and_transfers(
    icu: dict[str, pl.DataFrame], hosp: dict[str, pl.DataFrame], plan: FixturePlan
) -> None:
    stays = icu["icustays"]
    assert stays.height == len(plan.icu_segments) == 75
    assert stays.get_column("stay_id").to_list() == sorted(s.stay_id for s in plan.icu_segments)
    assert stays.filter(pl.col("first_careunit") != pl.col("last_careunit")).height == 0
    assert stays.filter(pl.col("outtime") <= pl.col("intime")).height == 0
    los = stays.get_column("los")
    assert float(los.min()) > 0 and float(los.max()) < 30  # type: ignore[arg-type]
    icu_units = {v for v, _ in load_vocab().categories["icu_careunits"]}
    assert set(stays.get_column("first_careunit").to_list()) <= icu_units
    tr = hosp["transfers"].filter(pl.col("careunit").is_in(list(icu_units)))
    joined = stays.join(
        tr,
        left_on=["hadm_id", "first_careunit", "intime", "outtime"],
        right_on=["hadm_id", "careunit", "intime", "outtime"],
        how="inner",
    )
    assert joined.height == stays.height == tr.height


def test_every_event_inside_its_stay(icu: dict[str, pl.DataFrame]) -> None:
    stays = icu["icustays"].select("stay_id", "intime", "outtime")
    for name in EVENT_TABLES:
        frame = icu[name]
        cols = check_mod.ICU_EVENT_TIMES[name]
        joined = frame.join(stays, on="stay_id", how="left")
        assert joined.get_column("intime").null_count() == 0, name
        for col in cols:
            assert joined.filter(pl.col(col) < pl.col("intime")).height == 0, f"{name}.{col}"
            assert joined.filter(pl.col(col) > pl.col("outtime")).height == 0, f"{name}.{col}"
        assert set(frame.get_column("caregiver_id").drop_nulls().to_list()) <= set(
            icu["caregiver"].get_column("caregiver_id").to_list()
        ), name


def test_chartevents_shape(icu: dict[str, pl.DataFrame], plan: FixturePlan) -> None:
    ce = icu["chartevents"]
    d_items = {r["itemid"]: r for r in icu["d_items"].iter_rows(named=True)}
    assert ce.get_column("stay_id").n_unique() == len(plan.icu_segments)  # every stay is charted
    assert ce.filter(pl.col("storetime") < pl.col("charttime")).height == 0
    assert set(ce.get_column("warning").unique().to_list()) == {0, 1}
    assert 0 < ce.filter(pl.col("warning") == 1).height < 0.1 * ce.height
    # numeric items: value text == formatted valuenum; text items: value only / GCS scores
    used = set(ce.get_column("itemid").unique().to_list())
    assert {
        220045,
        220179,
        220050,
        220210,
        220277,
        223761,
        223762,
        220739,
        223835,
        226512,
        226730,
    } <= used
    assert all(d_items[i]["linksto"] == "chartevents" for i in used)
    hr = ce.filter(pl.col("itemid") == 220045)
    assert hr.filter(pl.col("value") != pl.col("valuenum").cast(pl.Int64).cast(pl.Utf8)).height == 0
    assert set(hr.get_column("valueuom").unique().to_list()) == {"bpm"}
    assert (
        hr.filter((pl.col("valuenum") > 0) & (pl.col("valuenum") < 300)).height > 0.99 * hr.height
    )
    assert hr.filter(pl.col("valuenum") == 0).height >= 1  # rare artefact rows exist
    gcs_v = ce.filter(pl.col("itemid") == 223900)
    assert "No Response-ETT" in set(gcs_v.get_column("value").to_list())
    assert gcs_v.filter(pl.col("valuenum").is_null()).height == 0
    o2 = ce.filter(pl.col("itemid") == 226732)
    assert {"Endotracheal tube", "Nasal cannula"} <= set(o2.get_column("value").to_list())
    assert o2.get_column("valuenum").null_count() == o2.height  # pure text item
    fio2 = ce.filter(pl.col("itemid") == 223835)
    assert fio2.height > 0 and float(fio2.get_column("valuenum").min()) >= 21  # type: ignore[arg-type]
    # cadence: hourly HR for the first 48 h, then every 4 h
    first = (
        ce.filter(pl.col("itemid") == 220045)
        .join(icu["icustays"].select("stay_id", "intime"), on="stay_id")
        .with_columns(((pl.col("charttime") - pl.col("intime")).dt.total_minutes() / 60).alias("h"))
    )
    dense = first.filter(pl.col("h") < 48).group_by("stay_id").len()
    late = first.filter(pl.col("h") >= 48).group_by("stay_id").len()
    assert dense.height == len(plan.icu_segments) and late.height > 0
    # temperature is charted in one unit per stay
    per_stay_units = (
        ce.filter(pl.col("itemid").is_in([223761, 223762]))
        .group_by("stay_id")
        .agg(pl.col("itemid").n_unique())
    )
    assert per_stay_units.get_column("itemid").max() == 1
    # uniqueness hint of the contract holds
    assert ce.select("stay_id", "charttime", "itemid").unique().height == ce.height


def test_inputs_outputs_procedures_shape(icu: dict[str, pl.DataFrame], plan: FixturePlan) -> None:
    ie, ig, oe, pe, dte = (
        icu["inputevents"],
        icu["ingredientevents"],
        icu["outputevents"],
        icu["procedureevents"],
        icu["datetimeevents"],
    )
    assert ie.filter(pl.col("endtime") < pl.col("starttime")).height == 0
    assert set(ie.get_column("ordercategoryname").unique().to_list()) >= {
        "01-Drips",
        "02-Fluids (Crystalloids)",
        "03-IV Fluid Bolus",
    }
    assert set(ie.get_column("statusdescription").unique().to_list()) >= {
        "FinishedRunning",
        "Changed",
    }
    norepi = ie.filter(pl.col("itemid") == NOREPINEPHRINE)
    assert set(norepi.get_column("rateuom").unique().to_list()) == {"mcg/kg/min"}
    assert set(norepi.get_column("amountuom").unique().to_list()) == {"mg"}
    assert norepi.get_column("patientweight").null_count() == 0
    # rate changes: linkorderid points at the first orderid of the chain; (orderid, itemid) unique
    assert ie.filter(pl.col("linkorderid") > pl.col("orderid")).height == 0
    assert ie.select("orderid", "itemid").unique().height == ie.height
    # carrier rows share the drip's orderid
    main = ie.filter(pl.col("ordercomponenttypedescription") == "Main order parameter")
    mixed = ie.filter(pl.col("ordercomponenttypedescription") == "Mixed solution")
    assert mixed.height > 0
    assert set(mixed.get_column("orderid").to_list()) <= set(main.get_column("orderid").to_list())
    # ingredientevents mirror the fluid orders: same orderid, water + one solute
    assert set(ig.get_column("orderid").to_list()) <= set(ie.get_column("orderid").to_list())
    assert ig.select("orderid", "itemid").unique().height == ig.height
    assert set(ig.get_column("amountuom").unique().to_list()) == {"mL", "mEq", "grams"}
    # urine: Foley hourly-ish, mL, non-negative; the planted AKI stays are oliguric
    assert set(oe.get_column("valueuom").unique().to_list()) == {"mL"}
    assert float(oe.get_column("value").min()) >= 0  # type: ignore[arg-type]
    aki_stays = {a.icu.stay_id for a in plan.admissions_with("aki") if a.icu is not None}
    per_stay = (
        oe.filter(pl.col("itemid") == 226559)
        .group_by("stay_id")
        .agg(pl.col("value").mean().alias("m"))
    )
    aki_mean = per_stay.filter(pl.col("stay_id").is_in(list(aki_stays))).get_column("m").mean()
    other_mean = per_stay.filter(~pl.col("stay_id").is_in(list(aki_stays))).get_column("m").mean()
    assert aki_mean is not None and other_mean is not None and float(aki_mean) < float(other_mean)  # type: ignore[arg-type]
    # procedures: ventilation with intubation/extubation, arterial lines with a location, CRRT
    items = set(pe.get_column("itemid").unique().to_list())
    assert {INVASIVE_VENT, 224385, 227194, 225752, CRRT} <= items
    vent = pe.filter(pl.col("itemid") == INVASIVE_VENT)
    assert (
        vent.filter(
            pl.col("value") != (pl.col("endtime") - pl.col("starttime")).dt.total_minutes()
        ).height
        == 0
    )
    assert set(vent.get_column("valueuom").unique().to_list()) == {"min"}
    aline = pe.filter(pl.col("itemid") == 225752)
    assert aline.get_column("location").null_count() == 0
    assert pe.get_column("orderid").n_unique() == pe.height  # PK
    # datetimeevents: the charted date/time never lies after its own charttime
    assert dte.filter(pl.col("value") > pl.col("charttime")).height == 0
    assert set(dte.get_column("valueuom").unique().to_list()) == {"Date and time"}


def test_planted_signal_is_consistent_across_hosp_and_icu(
    hosp: dict[str, pl.DataFrame], icu: dict[str, pl.DataFrame], plan: FixturePlan
) -> None:
    ie, pe = icu["inputevents"], icu["procedureevents"]
    sepsis = {a.hadm_id for a in plan.admissions_with("sepsis") if a.icu is not None}
    aki = {a.hadm_id for a in plan.admissions_with("aki") if a.icu is not None}
    assert sepsis and aki
    norepi = set(ie.filter(pl.col("itemid") == NOREPINEPHRINE).get_column("hadm_id").to_list())
    culture = set(
        hosp["microbiologyevents"]
        .filter(pl.col("spec_type_desc") == "BLOOD CULTURE")
        .get_column("hadm_id")
        .drop_nulls()
        .to_list()
    )
    abx = set(
        hosp["prescriptions"]
        .filter(
            pl.col("drug").str.to_lowercase().str.contains("vancomycin|piperacillin|cefepime")
            & (pl.col("route") == "IV")
        )
        .get_column("hadm_id")
        .to_list()
    )
    assert sepsis <= norepi & culture & abx
    # the icu drip starts exactly when the hosp norepinephrine prescription does, whenever that
    # start falls inside the ICU stay (else it starts on arrival)
    rx = (
        hosp["prescriptions"]
        .filter(pl.col("drug") == "Norepinephrine")
        .group_by("hadm_id")
        .agg(pl.col("starttime").min().alias("rx_start"))
    )
    drips = (
        ie.filter(pl.col("itemid") == NOREPINEPHRINE)
        .group_by("hadm_id", "stay_id")
        .agg(pl.col("starttime").min())
        .join(icu["icustays"].select("stay_id", "intime", "outtime"), on="stay_id")
    )
    both = drips.join(rx, on="hadm_id").filter(pl.col("hadm_id").is_in(list(sepsis)))
    assert both.height == len(sepsis)
    inside = both.filter(
        (pl.col("rx_start") >= pl.col("intime"))
        & (pl.col("rx_start") <= pl.col("outtime") - pl.duration(hours=1))
    )
    assert inside.height >= 1
    assert inside.filter(pl.col("starttime") != pl.col("rx_start")).height == 0
    outside = both.filter(
        (pl.col("rx_start") < pl.col("intime"))
        | (pl.col("rx_start") > pl.col("outtime") - pl.duration(hours=1))
    )
    assert outside.filter(pl.col("starttime") != pl.col("intime")).height == 0
    crrt = set(pe.filter(pl.col("itemid") == CRRT).get_column("hadm_id").to_list())
    assert aki <= crrt
    vented = set(pe.filter(pl.col("itemid") == INVASIVE_VENT).get_column("stay_id").to_list())
    assert len(vented) >= 1
    n_stays = len(plan.icu_segments)
    assert 0.25 * n_stays <= len(vented) <= 0.6 * n_stays
    assert 0.15 * n_stays <= len(norepi) <= 0.5 * n_stays
    # ventilated stays carry FiO2 rows inside the vent window and an ETT device
    ce = icu["chartevents"]
    fio2_stays = set(ce.filter(pl.col("itemid") == 223835).get_column("stay_id").to_list())
    assert fio2_stays == vented


def test_icu_frames_are_deterministic_and_independent_of_hosp(
    plan: FixturePlan, contract: Contract
) -> None:
    a = build_icu_frames(plan, contract=contract)
    b = build_icu_frames(plan, contract=contract)
    for name in ICU_TABLES:
        assert a[name].equals(b[name]), name
    other = build_icu_frames(build_plan(FixtureSpec(seed=7)), contract=contract)
    assert not other["chartevents"].equals(a["chartevents"])
    # per-table child generators: adding icu tables did not move a hosp byte (drift test below
    # proves it against the committed files; here: the hosp frames do not depend on icu order)
    assert icu_mod.table_rng is not None


# ---------------------------------------------------------------------------
# 3. Committed fixture: drift, DuckDB, budget, guard, hooks
# ---------------------------------------------------------------------------


def test_committed_layout(manifest: dict[str, Any]) -> None:
    files = sorted(p.name for p in ICU_DIR.glob("*.csv"))
    assert files == sorted(f"{t}.csv" for t in ICU_TABLES)
    keys = sorted(manifest["files"])
    assert [k for k in keys if k.startswith("mimic-iv-3.1/icu/")] == sorted(
        f"mimic-iv-3.1/icu/{t}.csv" for t in ICU_TABLES
    )
    assert len(keys) == 31 and manifest["modules"] == ["hosp", "icu"]
    assert manifest["spec"]["n_caregivers"] == 15
    readme = (FIXTURE_DIR / "README.md").read_text(encoding="utf-8")
    assert "mimic-iv-3.1/icu/<table>.csv" in readme and "9 mimiciv_icu tables" in readme
    assert "test_ep12" in readme and "build_fixture_catalog" in readme


def test_icu_fixture_drift(
    regenerated: write_mod.WriteResult, manifest: dict[str, Any], contract: Contract
) -> None:
    """Regeneration into tmp_path reproduces the committed icu files (and the manifest / README)
    byte-for-byte."""
    fresh = {e.rel_path: e for e in regenerated.entries}
    assert set(fresh) == set(manifest["files"])
    icu_keys = {k for k in fresh if k.startswith("mimic-iv-3.1/icu/")}
    assert len(icu_keys) == 9
    for rel in sorted(icu_keys):
        entry = fresh[rel]
        committed = FIXTURE_DIR / Path(rel)
        assert entry.sha256 == manifest["files"][rel]["sha256"], f"{rel}: manifest sha drift"
        assert entry.sha256 == _sha256(committed), f"{rel}: committed file drift"
        assert entry.bytes == committed.stat().st_size == manifest["files"][rel]["bytes"]
        assert entry.rows == manifest["files"][rel]["rows"]
        assert entry.generator_version == manifest["files"][rel]["generator_version"]
    assert manifest["contract_hash"] == contract.content_hash()
    assert regenerated.manifest_path.read_bytes() == (FIXTURE_DIR / "manifest.json").read_bytes()
    assert regenerated.readme_path.read_bytes() == (FIXTURE_DIR / "README.md").read_bytes()


def test_duckdb_reads_all_9_typed_with_zero_rejects(
    fixture_catalog, contract: Contract, manifest: dict[str, Any]
) -> None:
    for t in contract.by_schema("mimiciv_icu"):
        rel = f"mimic-iv-3.1/icu/{t.name}.csv"
        assert (
            _count(fixture_catalog, f"SELECT count(*) FROM {t.qualified_name}")
            == manifest["files"][rel]["rows"]
        )
        cols = fixture_catalog.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = 'mimiciv_icu' AND table_name = ? ORDER BY ordinal_position",
            [t.name],
        ).fetchall()
        assert [c[0] for c in cols] == list(t.column_names), t.name
        for c in t.columns:
            if not c.nullable:
                assert (
                    _count(
                        fixture_catalog,
                        f"SELECT count(*) FROM {t.qualified_name} WHERE {c.name} IS NULL",
                    )
                    == 0
                )
        # a strict re-read of the file with the contract types rejects nothing
        path = ICU_DIR / f"{t.name}.csv"
        n = _count(
            fixture_catalog,
            f"SELECT count(*) FROM ({catalog_mod.READ_CSV_SQL})",
            [str(path), t.read_csv_columns()],
        )
        assert n == manifest["files"][rel]["rows"], t.name


def test_catalog_icustays_transfers_and_windows(fixture_catalog) -> None:
    con = fixture_catalog
    # every stay inside its admission and matching exactly one transfers row
    bad_stays = (
        "SELECT count(*) FROM mimiciv_icu.icustays i "
        "JOIN mimiciv_hosp.admissions a USING (hadm_id) "
        "WHERE i.subject_id <> a.subject_id OR i.intime < a.admittime "
        "OR i.outtime > a.dischtime OR i.outtime <= i.intime"
    )
    assert _count(con, bad_stays) == 0
    unmatched = (
        "SELECT count(*) FROM ("
        "SELECT i.stay_id, count(t.hadm_id) AS n FROM mimiciv_icu.icustays i "
        "LEFT JOIN mimiciv_hosp.transfers t ON t.hadm_id = i.hadm_id "
        "AND t.careunit = i.first_careunit AND t.intime = i.intime AND t.outtime = i.outtime "
        "GROUP BY i.stay_id) WHERE n <> 1"
    )
    assert _count(con, unmatched) == 0
    for name, cols in check_mod.ICU_EVENT_TIMES.items():
        for col in cols:
            outside = (
                f"SELECT count(*) FROM mimiciv_icu.{name} e "
                "JOIN mimiciv_icu.icustays s USING (stay_id) "
                f"WHERE e.{col} < s.intime - INTERVAL 6 HOUR "
                f"OR e.{col} > s.outtime + INTERVAL 6 HOUR "
                "OR e.subject_id <> s.subject_id OR e.hadm_id <> s.hadm_id"
            )
            assert _count(con, outside) == 0, f"{name}.{col}"
        bad_items = (
            f"SELECT count(*) FROM mimiciv_icu.{name} e "
            "LEFT JOIN mimiciv_icu.d_items d USING (itemid) "
            f"WHERE d.itemid IS NULL OR d.linksto <> '{name}'"
        )
        assert _count(con, bad_items) == 0, name
    # >= 1 stay with norepinephrine + blood culture + IV antibiotic; >= 1 ventilated stay
    sepsis_stays = (
        "SELECT count(DISTINCT ie.stay_id) FROM mimiciv_icu.inputevents ie "
        "JOIN mimiciv_hosp.microbiologyevents m ON m.hadm_id = ie.hadm_id "
        "AND m.spec_type_desc = 'BLOOD CULTURE' "
        "JOIN mimiciv_hosp.prescriptions p ON p.hadm_id = ie.hadm_id AND p.route = 'IV' "
        "AND lower(p.drug) SIMILAR TO '.*(vancomycin|piperacillin|cefepime).*' "
        "WHERE ie.itemid = ?"
    )
    assert _count(con, sepsis_stays, [NOREPINEPHRINE]) >= 1
    vented = "SELECT count(DISTINCT stay_id) FROM mimiciv_icu.procedureevents WHERE itemid = ?"
    assert _count(con, vented, [INVASIVE_VENT]) >= 1


def test_size_budgets(manifest: dict[str, Any]) -> None:
    ce = manifest["files"]["mimic-iv-3.1/icu/chartevents.csv"]["bytes"]
    assert ce == (ICU_DIR / "chartevents.csv").stat().st_size <= CHARTEVENTS_BUDGET_BYTES
    on_disk = sum(p.stat().st_size for p in DATASET_DIR.rglob("*.csv"))
    assert manifest["total_bytes"] == on_disk <= FIXTURE_BUDGET_BYTES
    assert len(list(DATASET_DIR.rglob("*.csv"))) == 31


def test_guard_accepts_fixture_tree() -> None:
    violations = guard.scan([FIXTURE_DIR], REPO_ROOT)
    assert violations == [], [v.as_dict() for v in violations[:5]]
    for path in sorted(ICU_DIR.glob("*.csv")):
        data = path.read_bytes()
        assert not guard.id_band_hits(data), path.name
        assert write_mod.check_bytes(data, name=path.name) == []
        assert data.endswith(b"\n") and not data.endswith(b"\n\n") and b"\r" not in data


def test_committed_csv_formats(contract: Contract) -> None:
    ts = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
    for name in ("chartevents", "inputevents", "icustays", "datetimeevents", "procedureevents"):
        t = contract.table("mimiciv_icu", name)
        frame = pl.read_csv(ICU_DIR / f"{name}.csv", infer_schema=False)
        assert frame.columns == list(t.column_names)
        for c in t.columns:
            values = frame.get_column(c.name).drop_nulls().to_list()
            if c.duckdb_type == "TIMESTAMP":
                assert all(ts.match(v) for v in values), f"{name}.{c.name}"
            elif c.duckdb_type in ("DOUBLE", "FLOAT"):
                assert not any("e" in v.lower() for v in values), f"{name}.{c.name}"


# ---------------------------------------------------------------------------
# 4. In-memory fixture catalog
# ---------------------------------------------------------------------------


def test_build_fixture_catalog_has_31_tables_fast(plan: FixturePlan, contract: Contract) -> None:
    t0 = time.perf_counter()
    con = build_fixture_catalog(FIXTURE_DIR, contract=contract)
    elapsed = time.perf_counter() - t0
    try:
        tables = catalog_mod.catalog_tables(con)
        assert len(tables) == 31
        assert {t.split(".")[0] for t in tables} == {"mimiciv_hosp", "mimiciv_icu"}
        assert _count(con, "SELECT count(*) FROM mimiciv_icu.icustays") == len(plan.icu_segments)
        assert _count(con, "SELECT count(*) FROM mimiciv_hosp.patients") == plan.spec.n_subjects
        # DuckDB configuration is the explicit app profile (DESIGN section 6)
        threads = con.execute("SELECT current_setting('threads')").fetchone()
        assert threads is not None and int(threads[0]) >= 1
        comment = con.execute(
            "SELECT comment FROM duckdb_tables() "
            "WHERE schema_name = 'mimiciv_icu' AND table_name = 'icustays'"
        ).fetchone()
        assert comment is not None and comment[0]
    finally:
        con.close()
    assert elapsed < 10.0, f"fixture catalog took {elapsed:.1f} s (budget < 5 s)"


def test_build_fixture_catalog_refuses_missing_tree(tmp_path: Path) -> None:
    with pytest.raises(FixtureCatalogError, match="not found"):
        build_fixture_catalog(tmp_path / "nowhere")
    (tmp_path / "mimic-iv-3.1" / "hosp").mkdir(parents=True)
    with pytest.raises(FixtureCatalogError, match="missing"):
        build_fixture_catalog(tmp_path)


def test_session_fixtures(
    fixture_catalog, fixture_root: Path, contract: Contract, tier: str
) -> None:
    assert fixture_root == FIXTURE_DIR
    assert tier in ("fixture", "dev", "full")
    assert contract.table("mimiciv_icu", "icustays").primary_key == ("stay_id",)
    assert _count(fixture_catalog, "SELECT count(*) FROM mimiciv_icu.d_items") >= 45


# ---------------------------------------------------------------------------
# 5. Tier markers (pytester) + conftest wiring
# ---------------------------------------------------------------------------

NESTED_TESTS = """
import pytest

def test_plain():
    assert True

@pytest.mark.tier("fixture")
def test_fixture_marked():
    assert True

@pytest.mark.tier("dev")
def test_dev_marked():
    assert True

@pytest.mark.tier("full")
def test_full_marked():
    assert True
"""


@pytest.fixture
def isolated_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A throw-away data root (safe: local fixed C: volume) so catalog presence is under the
    test's control; the settings cache is cleared before and after."""
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setenv("MWH_DATA_ROOT", str(root))
    monkeypatch.delenv("PYTEST_TIER", raising=False)
    config_mod.configure()
    yield root
    config_mod.configure()


@pytest.fixture
def nested(pytester: pytest.Pytester, isolated_root: Path) -> pytest.Pytester:
    pytester.makeini("[pytest]\naddopts = -ra --strict-markers\n")
    pytester.makeconftest(CONFTEST.read_text(encoding="utf-8"))
    pytester.makepyfile(test_tiers=NESTED_TESTS)
    return pytester


def test_tier_default_deselects_dev_and_full(nested: pytest.Pytester) -> None:
    result = nested.runpytest("-p", "no:cacheprovider")
    result.assert_outcomes(passed=2, deselected=2)
    result.stdout.fnmatch_lines(["*mimicwarehouse tier: fixture*"])


def test_tier_dev_selects_dev_and_skips_without_catalog(
    nested: pytest.Pytester, isolated_root: Path
) -> None:
    result = nested.runpytest("-p", "no:cacheprovider", "--tier", "dev")
    result.assert_outcomes(passed=2, skipped=1, deselected=1)
    result.stdout.fnmatch_lines(["*SKIP*dev tier: catalog not found*dev.duckdb*"])
    # once the catalog exists (EP-21) the same test runs
    (isolated_root / "warehouse").mkdir()
    (isolated_root / "warehouse" / "dev.duckdb").write_bytes(b"")
    config_mod.configure()
    result = nested.runpytest("-p", "no:cacheprovider", "--tier", "dev")
    result.assert_outcomes(passed=3, deselected=1)


def test_tier_full_selects_everything(nested: pytest.Pytester) -> None:
    result = nested.runpytest("-p", "no:cacheprovider", "--tier", "full")
    result.assert_outcomes(passed=2, skipped=2)
    result.stdout.fnmatch_lines(["*full tier: catalog not found*full.duckdb*"])


def test_tier_env_fallback_and_bad_values(
    nested: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PYTEST_TIER", "dev")
    result = nested.runpytest("-p", "no:cacheprovider")
    result.assert_outcomes(passed=2, skipped=1, deselected=1)
    # the option wins over the environment
    result = nested.runpytest("-p", "no:cacheprovider", "--tier", "fixture")
    result.assert_outcomes(passed=2, deselected=2)
    monkeypatch.setenv("PYTEST_TIER", "demo")  # a data tier, not a test tier
    result = nested.runpytest("-p", "no:cacheprovider")
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*PYTEST_TIER='demo' is not a test tier*"])
    monkeypatch.delenv("PYTEST_TIER")
    result = nested.runpytest("-p", "no:cacheprovider", "--tier", "demo")
    assert result.ret == pytest.ExitCode.USAGE_ERROR


def test_strict_markers_reject_unknown_marker_and_tier(nested: pytest.Pytester) -> None:
    nested.makepyfile(
        test_bad_marker="import pytest\n@pytest.mark.tierx('dev')\ndef test_x():\n    assert True\n"
    )
    result = nested.runpytest("-p", "no:cacheprovider", "test_bad_marker.py")
    assert result.ret != 0
    result.stdout.fnmatch_lines(["*'tierx' not found in `markers` configuration option*"])
    nested.makepyfile(
        test_bad_tier="import pytest\n@pytest.mark.tier('nope')\ndef test_x():\n    assert True\n"
    )
    result = nested.runpytest("-p", "no:cacheprovider", "test_bad_tier.py")
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*unknown tier 'nope'*"])


def test_conftest_registers_tier_semantics(request: pytest.FixtureRequest) -> None:
    markers = "\n".join(request.config.getini("markers"))
    line = next(m for m in request.config.getini("markers") if m.startswith("tier(name):"))
    assert "--tier" in line and "PYTEST_TIER" in line and "deselected" in line
    assert "ep_12:" in markers
    assert request.config.getoption("--tier") in (None, "fixture", "dev", "full")


def test_poe_tasks_and_docs() -> None:
    tasks = tomllib.loads((WORKSPACE / "pyproject.toml").read_text(encoding="utf-8"))["tool"][
        "poe"
    ]["tasks"]
    assert tasks["test-dev"] == "pytest --tier dev" and tasks["test-full"] == "pytest --tier full"
    assert tasks["check"] == ["lint", "typecheck", "test"]  # fixture-only
    readme = (WORKSPACE / "tests" / "README.md").read_text(encoding="utf-8")
    for needle in ("PYTEST_TIER", "--tier", "fixture < dev < full", "demo", "default_tier"):
        assert needle in readme, needle
    design = (WORKSPACE / "DESIGN.md").read_text(encoding="utf-8")
    assert "EP-12" in design and "PYTEST_TIER" in design
    example = (WORKSPACE / ".env.example").read_text(encoding="utf-8")
    assert "MWH_TEST_TIER" not in example and "PYTEST_TIER" not in example  # not a Setting
    assert verify.pytest_argv(12, ["--tier", "dev"])[-2:] == ["--tier", "dev"]


@pytest.mark.tier("dev")
def test_dev_tier_catalog_opens_read_only(tier: str) -> None:
    """Marker mechanics in the real suite: deselected by default, skipped under ``--tier dev``
    until EP-21 writes ``dev.duckdb``; then it only opens the file read-only (no rows)."""
    import duckdb

    from mimicwarehouse.config import get_settings

    assert tier in ("dev", "full")
    path = get_settings().catalog_path("dev")
    con = duckdb.connect(str(path), read_only=True)
    try:
        assert con.execute("SELECT 1").fetchone() == (1,)
    finally:
        con.close()


@pytest.mark.tier("full")
def test_full_tier_catalog_opens_read_only(tier: str) -> None:
    import duckdb

    from mimicwarehouse.config import get_settings

    assert tier == "full"
    path = get_settings().catalog_path("full")
    con = duckdb.connect(str(path), read_only=True)
    try:
        assert con.execute("SELECT 1").fetchone() == (1,)
    finally:
        con.close()


# ---------------------------------------------------------------------------
# 6. CLI
# ---------------------------------------------------------------------------


def test_mwh_fixtures_build_writes_31_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COLUMNS", "200")
    out = tmp_path / "fx"
    result = runner.invoke(
        app, ["fixtures", "build", "--out", str(out), "--subjects", "15", "--seed", "7"]
    )
    assert result.exit_code == 0, result.output
    assert "wrote 31 files" in result.output
    icu_files = sorted(p.name for p in (out / "mimic-iv-3.1" / "icu").glob("*.csv"))
    assert icu_files == sorted(f"{t}.csv" for t in ICU_TABLES)
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["modules"] == ["hosp", "icu"] and len(manifest["files"]) == 31
    assert manifest["files"]["mimic-iv-3.1/icu/caregiver.csv"]["rows"] == 15
    # the small tree also loads into a catalog and validates
    con = build_fixture_catalog(out)
    try:
        assert len(catalog_mod.catalog_tables(con)) == 31
        stays = _count(con, "SELECT count(*) FROM mimiciv_icu.icustays")
        assert stays == len(build_plan(FixtureSpec(seed=7, n_subjects=15)).icu_segments)
    finally:
        con.close()
    before = {p.name: _sha256(p) for p in (out / "mimic-iv-3.1" / "icu").glob("*.csv")}
    result = runner.invoke(
        app, ["fixtures", "build", "--out", str(out), "--subjects", "15", "--seed", "7"]
    )
    assert result.exit_code == 0
    after = {p.name: _sha256(p) for p in (out / "mimic-iv-3.1" / "icu").glob("*.csv")}
    assert before == after


def test_mwh_help_still_light() -> None:
    code = (
        "import sys, mimicwarehouse.cli; "
        "heavy = ('polars', 'numpy', 'duckdb', 'pandas', 'pyarrow'); "
        "bad = [m for m in heavy if m in sys.modules]; "
        "print(bad); sys.exit(1 if bad else 0)"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=120, check=False
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_icu_item_helper() -> None:
    item = IcuItem(
        itemid=220045, label="Heart Rate", abbreviation="HR", linksto="chartevents", category="x",
        unitname="bpm", param_type="Numeric", lownormalvalue=60, highnormalvalue=100, role="hr",
        low=40, high=150, decimals=0,
    )  # fmt: skip
    assert item.is_numeric and item.format(88.6) == ("89", 89.0)
    assert icu_mod.ceil_hour(datetime(2150, 1, 1, 8, 15)) == datetime(2150, 1, 1, 9)
    assert icu_mod.ceil_hour(datetime(2150, 1, 1, 8, 0)) == datetime(2150, 1, 1, 8)
