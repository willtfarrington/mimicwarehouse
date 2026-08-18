"""EP-11 — synthetic fixture generator A (hosp): spec + plan, seed vocabularies, the 22 hosp
table generators, contract + integrity checks, the hook-clean writer, ``mwh fixtures build`` and
the committed fixture under ``tests/fixtures/mimic-iv-3.1/hosp/``.

Fixture tier only: everything here is synthetic (ids >= 90 000 000, D-27) and generated in
memory or into ``tmp_path``; the committed fixture is compared byte-for-byte against a fresh
regeneration ("fixture drift"). No data root is read; the DuckDB connections are in-memory and
opened with ``get_settings().duckdb_settings("app")`` (house rule, DESIGN §6). No test prints a
row.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import re
import subprocess
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any

import polars as pl
import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from mimicwarehouse import guard
from mimicwarehouse.cli import DIAGNOSTIC_COMMANDS, app
from mimicwarehouse.fixtures import (
    FIXTURE_ID_FLOOR,
    GENERATOR_VERSION,
    FixtureError,
    FixturePlan,
    FixtureSpec,
    assert_valid,
    build_hosp_frames,
    build_plan,
    default_out_dir,
    load_vocab,
    validate,
    write_fixture,
)
from mimicwarehouse.fixtures import hosp as hosp_mod
from mimicwarehouse.fixtures import spec as spec_mod
from mimicwarehouse.fixtures import write as write_mod
from mimicwarehouse.fixtures.check import EXTRA_FKS, ID_COLUMNS
from mimicwarehouse.fixtures.vocab import VOCAB_FILES, VocabError, load_vocab_from, vocab_root
from mimicwarehouse.schema import Contract, load_contract

pytestmark = pytest.mark.ep_11

runner = CliRunner()

WORKSPACE = Path(__file__).resolve().parents[2]
REPO_ROOT = WORKSPACE.parent
FIXTURE_DIR = WORKSPACE / "tests" / "fixtures"
HOSP_DIR = FIXTURE_DIR / "mimic-iv-3.1" / "hosp"
HOSP_TABLES = [
    "admissions",
    "d_hcpcs",
    "diagnoses_icd",
    "d_icd_diagnoses",
    "d_icd_procedures",
    "d_labitems",
    "drgcodes",
    "emar_detail",
    "emar",
    "hcpcsevents",
    "labevents",
    "microbiologyevents",
    "omr",
    "patients",
    "pharmacy",
    "poe_detail",
    "poe",
    "prescriptions",
    "procedures_icd",
    "provider",
    "services",
    "transfers",
]
HOSP_BUDGET_BYTES = 6_000_000
TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
#: The concept-critical lab itemids the brief names.
KEY_ITEMIDS = {50912, 50971, 50983, 50813, 51301, 51222, 51265, 50885, 50931, 50820}
READ_CSV = (
    "SELECT * FROM read_csv(?, columns=?, header=true, delim=',', quote='\"', escape='\"', "
    "timestampformat='%Y-%m-%d %H:%M:%S', dateformat='%Y-%m-%d', ignore_errors=false)"
)


# ---------------------------------------------------------------------------
# Fixtures (module-scoped: one plan / one frame set for the whole module)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def contract() -> Contract:
    load_contract.cache_clear()
    return load_contract()


@pytest.fixture(scope="module")
def spec() -> FixtureSpec:
    return FixtureSpec()


@pytest.fixture(scope="module")
def plan(spec: FixtureSpec) -> FixturePlan:
    return build_plan(spec)


@pytest.fixture(scope="module")
def frames(plan: FixturePlan, contract: Contract) -> dict[str, pl.DataFrame]:
    return build_hosp_frames(plan, contract=contract)


@pytest.fixture(scope="module")
def regenerated(
    tmp_path_factory: pytest.TempPathFactory,
    frames: dict[str, pl.DataFrame],
    spec: FixtureSpec,
    contract: Contract,
) -> write_mod.WriteResult:
    """The whole tree regenerated into tmp_path (hosp + icu since EP-12: manifest.json and
    README.md cover both modules, so the byte comparison below needs the full build); the hosp
    frames written are the module's ``frames``."""
    out = tmp_path_factory.mktemp("fixture-regen")
    result = write_mod.build_and_write(out, spec=spec)
    again = write_fixture(
        frames, out / "hosp-only", spec=spec, contract_hash=contract.content_hash()
    )
    hosp_entries = {e.rel_path: e.sha256 for e in again.entries}
    assert {
        e.rel_path: e.sha256 for e in result.entries if e.rel_path in hosp_entries
    } == hosp_entries
    return result


@pytest.fixture(scope="module")
def manifest() -> dict[str, Any]:
    return json.loads((FIXTURE_DIR / "manifest.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def con():
    import duckdb

    from mimicwarehouse.config import get_settings

    config: dict[str, Any] = dict(get_settings().duckdb_settings("app"))
    connection = duckdb.connect(":memory:", config=config)
    try:
        yield connection
    finally:
        connection.close()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# 1. Spec + plan
# ---------------------------------------------------------------------------


def test_spec_defaults(spec: FixtureSpec) -> None:
    assert spec.seed == 2026 and spec.n_subjects == 120
    assert spec.first_subject_id == spec.first_hadm_id == spec.first_stay_id == FIXTURE_ID_FLOOR
    assert spec.first_event_id == FIXTURE_ID_FLOOR == guard.FIXTURE_ID_FLOOR
    assert spec.admissions_per_subject_mean == 1.5 and spec.icu_fraction == 0.4
    assert spec.mortality_rate == 0.08 and spec.labs_per_admission == 40
    assert spec.canonical()["seed"] == 2026


@pytest.mark.parametrize(
    "overrides",
    [
        {"seed": 10_000_000 + 5},  # inside the real subject band (guard G4)
        {"seed": 39_999_999},
        {"first_subject_id": 89_999_999},
        {"first_hadm_id": 1},
        {"first_event_id": 12},
        {"n_subjects": 0},
        {"icu_fraction": 1.5},
        {"anchor_age_range": (18, 91)},
        {"los_days": (0.0, 3.0)},
        {"not_a_field": 1},
    ],
)
def test_spec_rejects_bad_values(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        FixtureSpec(**overrides)


def test_plan_subjects_and_buckets(plan: FixturePlan, spec: FixtureSpec) -> None:
    ids = [s.subject_id for s in plan.subjects]
    assert ids == list(range(FIXTURE_ID_FLOOR, FIXTURE_ID_FLOOR + spec.n_subjects))
    # dev filter (DESIGN §4): subject_id % 100 < 5 keeps 10 of the 120 consecutive ids
    assert sum(1 for i in ids if i % 100 < 5) == 10
    assert {s.gender for s in plan.subjects} == {"F", "M"}
    ages = [s.anchor_age for s in plan.subjects]
    assert min(ages) >= 18 and max(ages) == spec_mod.AGE_CAP_LABEL
    assert not any(a in (89, 90) for a in ages) and ages.count(91) >= 2
    years = [s.anchor_year for s in plan.subjects]
    assert min(years) >= 2110 and max(years) <= 2200
    assert {s.anchor_year_group for s in plan.subjects} == set(spec_mod.ANCHOR_YEAR_GROUPS)
    assert len(plan.providers) == spec.n_providers
    assert all(re.fullmatch(r"P[0-9A-Z]{5}", p) for p in plan.providers)


def test_plan_admissions_deaths_dod(plan: FixturePlan, spec: FixtureSpec) -> None:
    hadm_ids = [a.hadm_id for a in plan.admissions]
    assert hadm_ids == list(range(spec.first_hadm_id, spec.first_hadm_id + len(hadm_ids)))
    assert len(hadm_ids) >= spec.n_subjects
    for s in plan.subjects:
        prev = None
        for a in s.admissions:
            assert a.subject_id == s.subject_id
            assert timedelta(days=1) <= a.los <= timedelta(days=20)
            if prev is not None:
                assert a.admittime > prev.dischtime
            prev = a
        died = [a for a in s.admissions if a.died]
        assert len(died) <= 1 and (not died or died[0] is s.admissions[-1])
        if died:
            assert s.dod == died[0].dischtime.date()
            assert died[0].discharge_location == "DIED"
        elif s.dod is not None:
            assert (
                s.last_dischtime.date() <= s.dod <= (s.last_dischtime + timedelta(days=366)).date()
            )
    n_died = sum(a.died for a in plan.admissions)
    assert 0 < n_died / len(plan.admissions) < 0.2
    assert sum(s.dod is not None for s in plan.subjects) > n_died


def test_plan_segments_and_icu(plan: FixturePlan, spec: FixtureSpec) -> None:
    n_icu = 0
    for a in plan.admissions:
        segs = a.segments
        assert segs[0].intime == a.admittime and segs[-1].outtime == a.dischtime
        for x, y in itertools.pairwise(segs):
            assert x.outtime == y.intime
        assert all(seg.outtime > seg.intime for seg in segs)
        icu_segs = [seg for seg in segs if seg.is_icu]
        assert len(icu_segs) <= 1 and (a.icu is not None) == bool(icu_segs)
        if a.icu is not None:
            n_icu += 1
            assert a.icu.hadm_id == a.hadm_id and a.icu.subject_id == a.subject_id
            assert a.admittime <= a.icu.intime < a.icu.outtime <= a.dischtime
            assert a.icu.careunit == icu_segs[0].careunit
            assert a.icu.los_days > 0
        if a.ed:
            assert a.edregtime is not None and a.edregtime < a.admittime == a.edouttime
        else:
            assert a.edregtime is None and a.edouttime is None
        assert a.icd_version in (9, 10)
    assert 0.25 <= n_icu / len(plan.admissions) <= 0.55
    stays = [seg.stay_id for seg in plan.icu_segments]
    assert stays == list(range(spec.first_stay_id, spec.first_stay_id + len(stays)))
    for trait in spec_mod.TRAITS:
        assert len(plan.admissions_with(trait)) == spec.planted_per_trait


def test_icd_version_follows_anchor_year_group(plan: FixturePlan) -> None:
    for s in plan.subjects:
        for a in s.admissions:
            if s.anchor_year_group in spec_mod.ICD9_GROUPS:
                assert a.icd_version == 9
            elif s.anchor_year_group in spec_mod.ICD10_GROUPS:
                assert a.icd_version == 10
    mixed = [
        a.icd_version
        for s in plan.subjects
        if s.anchor_year_group == spec_mod.MIXED_GROUP
        for a in s.admissions
    ]
    assert {9, 10} <= set(mixed)


def test_plan_is_deterministic_and_seed_sensitive(spec: FixtureSpec, plan: FixturePlan) -> None:
    again = build_plan(spec)
    assert again == plan
    other = build_plan(FixtureSpec(seed=7))
    assert other != plan
    assert [s.subject_id for s in other.subjects] == [s.subject_id for s in plan.subjects]


def test_table_rng_is_stable_per_name(spec: FixtureSpec) -> None:
    a = spec_mod.table_rng(spec, "labevents").random(3).tolist()
    b = spec_mod.table_rng(spec, "labevents").random(3).tolist()
    c = spec_mod.table_rng(spec, "emar").random(3).tolist()
    assert a == b and a != c
    assert spec_mod.table_rng(FixtureSpec(seed=7), "labevents").random(3).tolist() != a


# ---------------------------------------------------------------------------
# 2. Vocab
# ---------------------------------------------------------------------------


def test_vocab_loads_and_covers_the_brief() -> None:
    vocab = load_vocab()
    assert set(VOCAB_FILES) == {name.name for name in vocab_root().glob("*.yaml")}
    itemids = {i.itemid for i in vocab.lab_items}
    assert itemids >= KEY_ITEMIDS and len(itemids) >= 40
    assert all(i.itemid < 10_000_000 for i in vocab.lab_items)
    assert len(vocab.icd_diagnoses[9]) >= 40 and len(vocab.icd_diagnoses[10]) >= 40
    assert len(vocab.icd_procedures[9]) >= 10 and len(vocab.icd_procedures[10]) >= 10
    assert {c.code for c in vocab.icd_tagged(9, "t2dm")} >= {"25000"}
    assert {c.code for c in vocab.icd_tagged(10, "t2dm")} >= {"E119"}
    assert {c.code for c in vocab.icd_tagged(9, "sepsis")} >= {"99591"}
    assert {c.code for c in vocab.icd_tagged(10, "sepsis")} >= {"A419"}
    assert {c.code for c in vocab.icd_tagged(9, "aki")} >= {"5849"}
    assert {c.code for c in vocab.icd_tagged(10, "aki")} >= {"N179"}
    assert all("." not in c.code for v in (9, 10) for c in vocab.icd_diagnoses[v])
    assert len(vocab.hcpcs) >= 10 and len(vocab.drugs) >= 30
    names = {d.drug.lower() for d in vocab.drugs}
    for needle in (
        "vancomycin",
        "piperacillin",
        "norepinephrine",
        "insulin",
        "heparin",
        "metoprolol",
        "furosemide",
        "propofol",
    ):
        assert any(needle in n for n in names), needle
    assert vocab.drugs_tagged("antibiotic") and vocab.drugs_tagged("insulin")
    for key in ("admission_types", "discharge_locations", "icu_careunits", "services"):
        assert key in vocab.categories
    icus = [v for v, _ in vocab.categories["icu_careunits"]]
    assert "Medical Intensive Care Unit (MICU)" in icus and "Trauma SICU (TSICU)" in icus
    assert any("\n" in c for c in vocab.categories["lab_comments"])
    assert vocab.version_notes["d_labitems.yaml"]


def test_vocab_yaml_is_ascii_and_hook_clean() -> None:
    for path in vocab_root().glob("*.yaml"):
        data = path.read_bytes()
        assert data.isascii(), path.name
        assert data.endswith(b"\n") and not data.endswith(b"\n\n"), path.name
        assert b"\r" not in data, path.name
        assert not any(line != line.rstrip() for line in data.decode().split("\n")), path.name
        assert not guard.id_band_hits(data), path.name


def test_vocab_loader_rejects_broken_copy(tmp_path: Path) -> None:
    root = tmp_path / "vocab"
    root.mkdir()
    for name in VOCAB_FILES:
        (root / name).write_bytes((vocab_root() / name).read_bytes())
    ok = load_vocab_from(root)
    assert len(ok.lab_items) == len(load_vocab().lab_items)
    text = (root / "d_labitems.yaml").read_text(encoding="utf-8")
    (root / "d_labitems.yaml").write_text(
        text.replace("bmp: [50983,", "bmp: [59999,", 1), encoding="utf-8"
    )
    with pytest.raises(VocabError, match="unknown itemids"):
        load_vocab_from(root)
    (root / "drugs.yaml").unlink()
    with pytest.raises(VocabError, match="missing"):
        load_vocab_from(root)


# ---------------------------------------------------------------------------
# 3. Frames: contract shape + integrity
# ---------------------------------------------------------------------------


def test_frames_have_exact_contract_columns_and_dtypes(
    frames: dict[str, pl.DataFrame], contract: Contract
) -> None:
    assert list(frames) == HOSP_TABLES == [t.name for t in contract.by_schema("mimiciv_hosp")]
    for name, frame in frames.items():
        table = contract.table("mimiciv_hosp", name)
        assert list(frame.columns) == list(table.column_names), name
        assert dict(frame.schema) == hosp_mod.polars_schema(table), name
        assert frame.height > 0, name
        for c in table.columns:
            if not c.nullable:
                assert frame.get_column(c.name).null_count() == 0, f"{name}.{c.name}"


def test_validate_is_clean(
    frames: dict[str, pl.DataFrame], contract: Contract, plan: FixturePlan
) -> None:
    assert validate(frames, contract, plan) == []
    assert_valid(frames, contract, plan)  # does not raise


def test_validate_catches_broken_frames(
    frames: dict[str, pl.DataFrame], contract: Contract, plan: FixturePlan
) -> None:
    broken = dict(frames)
    broken["diagnoses_icd"] = frames["diagnoses_icd"].with_columns(
        pl.when(pl.arange(0, pl.len()) == 0)
        .then(pl.lit(1))
        .otherwise(pl.col("hadm_id"))
        .alias("hadm_id")
    )
    problems = validate(broken, contract, plan)
    assert any("diagnoses_icd" in p and ("id below" in p) for p in problems)
    broken = dict(frames)
    broken["labevents"] = frames["labevents"].with_columns(pl.lit(51_999_999).alias("itemid"))
    problems = validate(broken, contract, plan)
    assert any("labevents(itemid) -> d_labitems" in p for p in problems)
    broken = dict(frames)
    broken["patients"] = frames["patients"].drop("dod")
    assert any("patients: columns" in p for p in validate(broken, contract, plan))
    broken = dict(frames)
    broken["admissions"] = frames["admissions"].with_columns(pl.col("admittime").alias("dischtime"))
    assert any("dischtime <= admittime" in p for p in validate(broken, contract, plan))
    with pytest.raises(FixtureError, match="fixture problem"):
        assert_valid(broken, contract, plan)
    # a real-band value in a non-id column is caught too (guard G4 scans every column)
    broken = dict(frames)
    broken["drgcodes"] = frames["drgcodes"].with_columns(
        pl.lit(20_000_001).cast(pl.Int32).alias("subject_id")
    )
    assert any("real id band" in p for p in validate(broken, contract, plan))


def test_id_columns_and_extra_fks_are_declared() -> None:
    assert {
        "subject_id",
        "hadm_id",
        "stay_id",
        "labevent_id",
        "pharmacy_id",
        "transfer_id",
    } <= ID_COLUMNS
    assert ("emar", "poe_id", "poe", "poe_id") in EXTRA_FKS


def test_admissions_transfers_services_shape(
    frames: dict[str, pl.DataFrame], plan: FixturePlan
) -> None:
    adm = frames["admissions"]
    assert adm.height == len(plan.admissions)
    assert set(adm.get_column("hospital_expire_flag").unique().to_list()) == {0, 1}
    assert adm.filter(pl.col("edregtime").is_not_null()).height > 0
    assert adm.filter(pl.col("admit_provider_id").is_null()).height > 0
    tr = frames["transfers"]
    per_adm = tr.group_by("hadm_id").agg(
        pl.col("eventtype").first().alias("first"),
        pl.col("eventtype").last().alias("last"),
        pl.col("careunit").last().alias("last_unit"),
        pl.col("outtime").last().alias("last_out"),
    )
    assert set(per_adm.get_column("first").to_list()) <= {"ED", "admit"}
    assert set(per_adm.get_column("last").to_list()) == {"discharge"}
    assert per_adm.get_column("last_unit").null_count() == per_adm.height
    assert per_adm.get_column("last_out").null_count() == per_adm.height
    icu_units = {v for v, _ in load_vocab().categories["icu_careunits"]}
    icu_rows = tr.filter(pl.col("careunit").is_in(list(icu_units)))
    assert icu_rows.height == len(plan.icu_segments)
    svc = frames["services"]
    firsts = svc.filter(pl.col("prev_service").is_null())
    assert firsts.height == adm.height
    assert svc.filter(pl.col("prev_service").is_not_null()).height > 0


def test_labs_shape_and_free_text(
    frames: dict[str, pl.DataFrame], spec: FixtureSpec, plan: FixturePlan
) -> None:
    lab = frames["labevents"]
    n_adm = len(plan.admissions)
    assert lab.height >= spec.labs_per_admission * n_adm
    outpatient = lab.filter(pl.col("hadm_id").is_null())
    assert 0.1 * lab.height <= outpatient.height <= 0.3 * lab.height
    comments = lab.get_column("comments").drop_nulls().to_list()
    assert any("\n" in c for c in comments)
    assert any("," in c for c in comments)
    assert any('"' in c for c in comments)
    assert not any(line != line.rstrip() for c in comments for line in c.split("\n"))
    assert lab.filter(pl.col("storetime") < pl.col("charttime")).height == 0
    assert lab.filter(pl.col("flag") == "abnormal").height > 0
    assert set(lab.get_column("priority").unique().to_list()) == {"ROUTINE", "STAT"}
    assert lab.filter(pl.col("valuenum").is_null() & pl.col("value").is_not_null()).height > 0
    # every itemid the brief names is present
    assert set(lab.get_column("itemid").unique().to_list()) >= KEY_ITEMIDS
    # outpatient labs sit outside every admission of their subject
    windows = frames["admissions"].select("subject_id", "admittime", "dischtime")
    inside = (
        outpatient.join(windows, on="subject_id", how="inner")
        .filter(
            (pl.col("charttime") >= pl.col("admittime"))
            & (pl.col("charttime") <= pl.col("dischtime"))
        )
        .height
    )
    assert inside == 0


def test_planted_phenotype_signal(frames: dict[str, pl.DataFrame], plan: FixturePlan) -> None:
    lab = frames["labevents"]
    # AKI: creatinine (50912) doubling within 48 h in a handful of admissions
    cr = lab.filter((pl.col("itemid") == 50912) & pl.col("hadm_id").is_not_null()).select(
        "hadm_id", "charttime", "valuenum"
    )
    pairs = cr.join(cr, on="hadm_id", suffix="_b").filter(
        (pl.col("charttime_b") > pl.col("charttime"))
        & (pl.col("charttime_b") <= pl.col("charttime") + pl.duration(hours=48))
        & (pl.col("valuenum_b") >= 2 * pl.col("valuenum"))
    )
    aki = set(pairs.get_column("hadm_id").unique().to_list())
    assert {a.hadm_id for a in plan.admissions_with("aki")} <= aki
    # sepsis: blood culture + IV antibiotic prescription within 24 h
    micro = (
        frames["microbiologyevents"]
        .filter(pl.col("spec_type_desc") == "BLOOD CULTURE")
        .select("hadm_id", pl.coalesce("charttime", "chartdate").alias("t"))
    )
    abx = (
        frames["prescriptions"]
        .filter(
            pl.col("drug").str.to_lowercase().str.contains("vancomycin|piperacillin|cefepime")
            & (pl.col("route") == "IV")
        )
        .select("hadm_id", "starttime")
    )
    hits = micro.join(abx, on="hadm_id").filter(
        (pl.col("starttime") >= pl.col("t"))
        & (pl.col("starttime") <= pl.col("t") + pl.duration(hours=24))
    )
    sepsis = set(hits.get_column("hadm_id").unique().to_list())
    assert {a.hadm_id for a in plan.admissions_with("sepsis")} <= sepsis
    # T2DM: code + insulin + glucose >= 180
    dx = frames["diagnoses_icd"].filter(
        pl.col("icd_code").is_in(["25000", "E119", "25002", "E1165"])
    )
    ins = frames["prescriptions"].filter(
        pl.col("drug").str.to_lowercase().str.contains("insulin|glargine")
    )
    glu = lab.filter((pl.col("itemid") == 50931) & (pl.col("valuenum") >= 180))
    t2dm = (
        set(dx.get_column("hadm_id").to_list())
        & set(ins.get_column("hadm_id").to_list())
        & set(glu.get_column("hadm_id").drop_nulls().to_list())
    )
    assert {a.hadm_id for a in plan.admissions_with("t2dm")} <= t2dm


def test_orders_chain_is_consistent(frames: dict[str, pl.DataFrame]) -> None:
    poe, pharmacy, rx, emar, detail = (
        frames["poe"],
        frames["pharmacy"],
        frames["prescriptions"],
        frames["emar"],
        frames["emar_detail"],
    )
    assert poe.filter(pl.col("poe_id") != pl.format("{}-{}", "subject_id", "poe_seq")).height == 0
    assert (
        emar.filter(pl.col("emar_id") != pl.format("{}-{}", "subject_id", "emar_seq")).height == 0
    )
    assert set(poe.get_column("order_type").unique().to_list()) >= {
        "Medications",
        "Lab",
        "ADT orders",
    }
    dc = poe.filter(pl.col("transaction_type") == "D/C")
    assert dc.height > 0 and dc.get_column("discontinue_of_poe_id").null_count() == 0
    assert poe.filter(pl.col("discontinued_by_poe_id").is_not_null()).height == dc.height
    assert set(rx.get_column("drug_type").unique().to_list()) == {"MAIN", "BASE"}
    assert rx.filter(pl.col("drug_type") == "MAIN").height == pharmacy.height
    assert set(emar.get_column("event_txt").unique().to_list()) >= {
        "Administered",
        "Not Given",
        "Started",
    }
    assert detail.filter(pl.col("parent_field_ordinal").is_null()).height == emar.height
    assert detail.filter(pl.col("parent_field_ordinal") == "1.1").height > 0
    assert frames["poe_detail"].height > 0
    assert emar.filter(pl.col("charttime") < pl.col("scheduletime")).height == 0
    assert emar.filter(pl.col("storetime") < pl.col("charttime")).height == 0


# ---------------------------------------------------------------------------
# 4. Writer + committed fixture (drift, DuckDB, guard, hooks, budget)
# ---------------------------------------------------------------------------


def test_committed_layout(manifest: dict[str, Any]) -> None:
    assert (FIXTURE_DIR / "README.md").is_file() and (FIXTURE_DIR / "manifest.json").is_file()
    files = sorted(p.name for p in HOSP_DIR.glob("*.csv"))
    assert files == sorted(f"{t}.csv" for t in HOSP_TABLES)
    keys = sorted(manifest["files"])
    assert [k for k in keys if k.startswith("mimic-iv-3.1/hosp/")] == sorted(
        f"mimic-iv-3.1/hosp/{t}.csv" for t in HOSP_TABLES
    )
    assert manifest["generator"] == write_mod.GENERATOR_NAME
    assert manifest["generator_version"] == GENERATOR_VERSION
    assert manifest["seed"] == 2026 and manifest["spec"]["n_subjects"] == 120
    for entry in manifest["files"].values():
        assert set(entry) == {"sha256", "bytes", "rows", "seed", "generator_version"}
        assert entry["seed"] == 2026 and entry["generator_version"] == GENERATOR_VERSION
        assert re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])
    readme = (FIXTURE_DIR / "README.md").read_text(encoding="utf-8")
    assert "synthetic" in readme.lower() and write_mod.REGENERATE_COMMAND in readme
    assert "90_000_000" in readme and "MIT" in readme


def test_fixture_drift(
    regenerated: write_mod.WriteResult, manifest: dict[str, Any], contract: Contract
) -> None:
    """Regeneration into tmp_path reproduces the committed files byte-for-byte."""
    fresh = {e.rel_path: e for e in regenerated.entries}
    hosp_keys = {k for k in manifest["files"] if k.startswith("mimic-iv-3.1/hosp/")}
    assert {k for k in fresh if k.startswith("mimic-iv-3.1/hosp/")} == hosp_keys
    assert set(fresh) == set(manifest["files"])  # EP-12: icu entries too
    for rel, entry in fresh.items():
        committed = FIXTURE_DIR / Path(rel)
        assert entry.sha256 == manifest["files"][rel]["sha256"], f"{rel}: manifest sha drift"
        assert entry.sha256 == _sha256(committed), f"{rel}: committed file drift"
        assert entry.bytes == committed.stat().st_size == manifest["files"][rel]["bytes"]
        assert entry.rows == manifest["files"][rel]["rows"]
    assert manifest["contract_hash"] == contract.content_hash()
    # manifest + README themselves are reproducible too (EP-12 extends both)
    assert regenerated.manifest_path.read_bytes() == (FIXTURE_DIR / "manifest.json").read_bytes()
    assert regenerated.readme_path.read_bytes() == (FIXTURE_DIR / "README.md").read_bytes()


def test_duckdb_reads_all_22_typed_with_zero_rejects(
    con, contract: Contract, manifest: dict[str, Any]
) -> None:
    for t in contract.by_schema("mimiciv_hosp"):
        path = HOSP_DIR / f"{t.name}.csv"
        rel = con.execute(READ_CSV, [str(path), t.read_csv_columns()])
        cols = [d[0] for d in rel.description]
        assert cols == list(t.column_names), t.name
        rows = rel.fetchall()
        assert len(rows) == manifest["files"][f"mimic-iv-3.1/hosp/{t.name}.csv"]["rows"], t.name
        del rows
        for c in t.columns:
            if not c.nullable:
                nulls = con.execute(
                    f"SELECT count(*) FROM ({READ_CSV}) WHERE {c.name} IS NULL",
                    [str(path), t.read_csv_columns()],
                ).fetchone()[0]
                assert nulls == 0, f"{t.name}.{c.name}"


def test_duckdb_era_split_and_multiline_comments(con, contract: Contract) -> None:
    def rc(name: str) -> str:
        t = contract.table("mimiciv_hosp", name)
        path = (HOSP_DIR / f"{name}.csv").as_posix()
        return f"read_csv('{path}', columns={t.read_csv_columns()!r}, header=true)"

    rows = con.execute(
        f"SELECT p.anchor_year_group, d.icd_version, count(*) FROM {rc('diagnoses_icd')} d "
        f"JOIN {rc('admissions')} a USING (hadm_id) "
        f"JOIN {rc('patients')} p ON a.subject_id = p.subject_id "
        "GROUP BY 1, 2 ORDER BY 1, 2"
    ).fetchall()
    by_group: dict[str, set[int]] = {}
    for group, version, n in rows:
        assert n > 0
        by_group.setdefault(group, set()).add(version)
    assert by_group["2008 - 2010"] == {9} and by_group["2011 - 2013"] == {9}
    assert by_group["2014 - 2016"] == {9, 10}
    assert by_group["2017 - 2019"] == {10} and by_group["2020 - 2022"] == {10}
    n_nl = con.execute(
        f"SELECT count(*) FROM {rc('labevents')} WHERE comments LIKE '%' || chr(10) || '%'"
    ).fetchone()[0]
    assert n_nl >= 1


def test_guard_accepts_fixture_directory() -> None:
    violations = guard.scan([FIXTURE_DIR], REPO_ROOT)
    assert violations == [], [v.as_dict() for v in violations[:5]]
    for path in sorted(FIXTURE_DIR.rglob("*")):
        if path.is_file():
            assert not guard.id_band_hits(path.read_bytes()), path.name


def test_committed_bytes_are_hook_clean_and_lf() -> None:
    for path in sorted(FIXTURE_DIR.rglob("*")):
        if not path.is_file():
            continue
        data = path.read_bytes()
        assert data.endswith(b"\n") and not data.endswith(b"\n\n"), path.name
        assert b"\r" not in data, path.name
        assert not any(line != line.rstrip(b" \t") for line in data.split(b"\n")), path.name
        if path.suffix == ".csv":
            assert write_mod.check_bytes(data, name=path.name) == []


def test_committed_csv_formats(contract: Contract) -> None:
    for name in ("admissions", "labevents", "hcpcsevents", "patients", "microbiologyevents"):
        t = contract.table("mimiciv_hosp", name)
        frame = pl.read_csv(HOSP_DIR / f"{name}.csv", infer_schema=False)
        assert frame.columns == list(t.column_names)
        for c in t.columns:
            values = frame.get_column(c.name).drop_nulls().to_list()
            if c.duckdb_type == "TIMESTAMP":
                assert all(TIMESTAMP_RE.match(v) for v in values), f"{name}.{c.name}"
            elif c.duckdb_type == "DATE":
                assert all(DATE_RE.match(v) for v in values), f"{name}.{c.name}"
            elif c.duckdb_type in ("DOUBLE", "FLOAT"):
                assert not any("e" in v.lower() for v in values), f"{name}.{c.name}"


def test_hosp_size_budget(manifest: dict[str, Any]) -> None:
    hosp_bytes = sum(
        v["bytes"] for k, v in manifest["files"].items() if k.startswith("mimic-iv-3.1/hosp/")
    )
    on_disk = sum(p.stat().st_size for p in HOSP_DIR.glob("*.csv"))
    assert hosp_bytes == on_disk <= HOSP_BUDGET_BYTES
    assert manifest["total_bytes"] >= hosp_bytes


def test_check_bytes_flags_violations() -> None:
    assert write_mod.check_bytes(b"a,b\n1,2\n") == []
    assert any("newline" in p for p in write_mod.check_bytes(b"a,b\n1,2"))
    assert any("blank line" in p for p in write_mod.check_bytes(b"a,b\n1,2\n\n"))
    assert any("carriage" in p for p in write_mod.check_bytes(b"a,b\r\n1,2\r\n"))
    assert any("trailing whitespace" in p for p in write_mod.check_bytes(b"a,b\n1,2 \n"))
    band = str(guard.SUBJECT_BAND[0] + 1).encode()
    assert any("band" in p for p in write_mod.check_bytes(b"a,b\n" + band + b",2\n"))


def test_writer_refuses_dirty_frames(tmp_path: Path, spec: FixtureSpec, contract: Contract) -> None:
    table = contract.table("mimiciv_hosp", "provider")
    dirty = hosp_mod.to_frame(table, [{"provider_id": "P90001 "}])  # trailing blank
    with pytest.raises(write_mod.WriteError, match="trailing whitespace"):
        write_fixture({"provider": dirty}, tmp_path / "out", spec=spec, contract_hash="x")
    assert not (tmp_path / "out").exists()


def test_default_out_dir_is_workspace_fixtures() -> None:
    assert default_out_dir() == WORKSPACE / "tests" / "fixtures" == FIXTURE_DIR


# ---------------------------------------------------------------------------
# 5. CLI
# ---------------------------------------------------------------------------


def test_fixtures_is_a_diagnostic_command() -> None:
    assert "fixtures" in DIAGNOSTIC_COMMANDS


def test_mwh_fixtures_build_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COLUMNS", "200")
    out = tmp_path / "fx"
    result = runner.invoke(
        app, ["fixtures", "build", "--out", str(out), "--subjects", "15", "--seed", "7"]
    )
    assert result.exit_code == 0, result.output
    # 22 hosp + 9 icu files since EP-12 (the hosp count is asserted on the directory below)
    assert "wrote 31 files" in result.output and "seed 7, 15 subjects" in result.output
    assert (out / "manifest.json").is_file() and (out / "README.md").is_file()
    assert len(list((out / "mimic-iv-3.1" / "hosp").glob("*.csv"))) == 22
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["seed"] == 7 and manifest["spec"]["n_subjects"] == 15
    assert manifest["files"]["mimic-iv-3.1/hosp/patients.csv"]["rows"] == 15
    # second run: byte-identical
    before = {p.name: _sha256(p) for p in (out / "mimic-iv-3.1" / "hosp").glob("*.csv")}
    result = runner.invoke(
        app, ["fixtures", "build", "--out", str(out), "--subjects", "15", "--seed", "7", "--json"]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["total_bytes"] > 0 and len(payload["files"]) == 31
    assert sum(1 for f in payload["files"] if f["path"].startswith("mimic-iv-3.1/hosp/")) == 22
    after = {p.name: _sha256(p) for p in (out / "mimic-iv-3.1" / "hosp").glob("*.csv")}
    assert before == after
    assert not (out / "mimic-iv-3.1" / "hosp" / "chartevents.csv").exists()


def test_mwh_fixtures_build_rejects_band_seed(tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["fixtures", "build", "--out", str(tmp_path / "x"), "--seed", str(10_000_000 + 5)]
    )
    assert result.exit_code == 2
    assert not (tmp_path / "x").exists()


def test_mwh_help_does_not_import_polars_or_numpy() -> None:
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
