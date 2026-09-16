"""EP-47 — Cohort compiler, materialization, attrition, snapshot.

Fixture tier (default): the compiler emits the golden CTE chain for the tracer cohort
(``tests/ep/golden/first_icu_adults@1.0.0.sql``), deterministic and free of
non-deterministic functions, with the brief's output columns and one step per criterion
(``custom_sql`` wrapped as a semi-joined CTE and flagged); per-step attrition on a crafted
synthetic population in a temp DuckDB (ids >= 90 000 000) matches hand counts including a
``washout`` and a ``phenotype`` criterion, ``age_capped`` is set for a synthetic
91-year-old and the follow-up columns follow the ``dod`` rule; a fixture lake built
through the runner carries both seeds under ``lake/marts/fixture/cohorts/`` with
``marts.cohort_<id>_v<major>`` views and the ``marts.cohorts`` registry (readable through
``safe_query`` without a count column), a forced rebuild is byte-identical, an unforced
one is skipped, a moved definition is refused unless forced, the run and mart manifests
cite the layer snapshot ids and the run's attrition carries no small cell; ``attrition()``
on a chain with a small drop shows no exact count below k; the CLI (``build``,
``attrition``, ``validate --tier`` over the compiled cohort), the DAG wiring and the
import budget; the session fixture lake and the docs page. ``tier("dev")``: both seeds
built on dev, the registry and the suppressed chains through ``safe_query`` / the
accessor; ``tier("full")``: the tracer cohort's build on full.

Everything asserted or printed is SQL text, hashes, step names and suppressed aggregate
counts — never a patient-level row.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

import duckdb
import pytest

import helpers
from mimicwarehouse import config
from mimicwarehouse.cli import app
from mimicwarehouse.codesets import registry as codesets_registry
from mimicwarehouse.cohort import build as build_mod
from mimicwarehouse.cohort import compiler as compiler_mod
from mimicwarehouse.cohort import probe as probe_mod
from mimicwarehouse.cohort import registry as registry_mod
from mimicwarehouse.cohort.compiler import (
    OUTPUT_COLUMNS,
    CompileError,
    compile_entry,
    compile_spec,
)
from mimicwarehouse.cohort.spec import CohortSpecError, spec_from_text, sql_hash
from mimicwarehouse.dag import runner as runner_mod
from mimicwarehouse.dag.spec import load_dag

if TYPE_CHECKING:
    from mimicwarehouse.cohort.registry import Registry
    from mimicwarehouse.config import Settings

pytestmark = pytest.mark.ep_47

HOSP = "mimiciv_hosp"
ICU = "mimiciv_icu"
TRACER = "first_icu_adults@1.0.0"
HF = "hf_admissions@1.0.0"
GOLDEN = helpers.WORKSPACE / "tests" / "ep" / "golden"
BAND_TOKEN = re.compile(r"(?<![\w.])[123]\d{7}(?![\w.])")
NON_DETERMINISTIC = re.compile(r"\b(random|now|current_timestamp|uuid|gen_random_uuid)\s*\(", re.I)
#: The stage steps a cohort build needs on the fixture (the chains' core relations + the
#: dims the code-set registry expands against).
LAKE_STEPS: tuple[str, ...] = (
    f"stage.{HOSP}.patients",
    f"stage.{HOSP}.admissions",
    f"stage.{HOSP}.diagnoses_icd",
    f"stage.{ICU}.icustays",
    f"stage.{HOSP}.d_icd_diagnoses",
    f"stage.{HOSP}.d_icd_procedures",
    f"stage.{HOSP}.d_hcpcs",
    f"stage.{HOSP}.d_labitems",
    f"stage.{ICU}.d_items",
)
_BASE = """\
id: crafted
version: "1.0.0"
title: crafted
grain: hadm
index_event: {rule: each_hadm}
exclusion: []
follow_up: {outcome: in_hospital_mortality}
inclusion:
  - {label: adult, age: {min: 18}}
"""


@pytest.fixture(scope="module")
def registry() -> Registry:
    return registry_mod.load_registry()


def _drop_progress_handlers() -> None:
    import logging

    logger = logging.getLogger("mimicwarehouse")
    for handler in list(logger.handlers):
        logger.removeHandler(handler)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _no_small_cells(values: list[Any], k: int) -> None:
    for v in values:
        if v is not None:
            assert v == 0 or v >= k, f"small cell {v} released at k={k}"


# ---------------------------------------------------------------------------
# 1. The compiler: golden file, determinism, shapes, criterion mapping
# ---------------------------------------------------------------------------


def test_compiler_golden_and_shapes(registry: Registry) -> None:
    tracer = compile_entry(registry.get(TRACER), registry, "fixture")
    golden = GOLDEN / f"{TRACER}.sql"
    assert golden.is_file(), "the golden SQL file is committed"
    assert golden.read_text(encoding="utf-8") == tracer.sql.rstrip("\n") + "\n", (
        f"the compiled SQL drifted from tests/ep/golden/{TRACER}.sql (a deliberate compiler "
        "change re-writes the golden file; anything else is a regression)"
    )
    again = compile_entry(registry.get(TRACER), registry, "fixture")
    assert again.sql == tracer.sql and again.sql_sha256 == tracer.sql_sha256
    assert tracer.sql_sha256 == hashlib.sha256(tracer.sql.encode("utf-8")).hexdigest()
    assert tracer.tier == "fixture" and tracer.ref == TRACER and not tracer.custom
    # the chain: base -> idx -> one CTE per criterion in spec order -> cohort
    assert tracer.step_names == (
        "base",
        "idx",
        "crit_01_adult",
        "crit_02_short_icu_stay",
        "cohort",
    )
    assert [s.polarity for s in tracer.steps] == [
        "population",
        "index",
        "inclusion",
        "exclusion",
        "cohort",
    ]
    assert [s.kind for s in tracer.steps] == [None, None, "age", "los", None]
    assert tracer.keys == ("subject_id", "hadm_id", "stay_id")
    assert tracer.columns == (*tracer.keys, *OUTPUT_COLUMNS)
    assert tracer.sql.startswith("WITH base AS (") and tracer.sql.endswith("SELECT *\nFROM cohort")
    assert "ORDER BY c.subject_id, c.hadm_id, c.stay_id" in tracer.sql
    assert not NON_DETERMINISTIC.search(tracer.sql) and not NON_DETERMINISTIC.search(
        tracer.attrition_sql
    )
    # timesem's fragments are embedded verbatim: the age rule, the cap, the era index
    from mimicwarehouse import timesem

    age = timesem.sql_age_at("c.anchor_age", "c.anchor_year", "c.index_time")
    assert f"{age} >= 18" in tracer.sql and f"least({age}, 91) AS age_at_index" in tracer.sql
    assert timesem.sql_is_age_capped(age) in tracer.sql
    assert timesem.sql_era_index("c.anchor_year_group") in tracer.sql
    assert timesem.index_event_sql("first_icu_stay").splitlines()[0] in tracer.sql
    assert timesem.sql_hours_since("c.stay_intime", "c.stay_outtime") + " < 4" in tracer.sql
    # identifiers never leave the chain into a count-family-free select list: the attrition
    # statement counts them (real count-family pair, EP-33 B1) once per step
    assert tracer.attrition_sql.count("count(*) AS n_units") == len(tracer.steps)
    assert tracer.attrition_sql.count("count(DISTINCT subject_id) AS n_subjects") == len(
        tracer.steps
    )
    assert tracer.attrition_sql.endswith("ORDER BY 1")
    assert tracer.sources == (
        "mimiciv_hosp.admissions",
        "mimiciv_hosp.patients",
        "mimiciv_icu.icustays",
    )
    assert compiler_mod.describe(tracer).startswith(f"-- {TRACER}: 5 step(s)")
    # hf_admissions: codeset criterion + codeset washout read meta.codeset_members
    hf = compile_entry(registry.get(HF), registry)
    assert hf.step_names == (
        "base",
        "idx",
        "crit_01_hf_coded",
        "crit_02_adult",
        "crit_03_hospice_discharge",
        "washout",
        "cohort",
    )
    assert hf.keys == ("subject_id", "hadm_id") and "meta.codeset_members" in hf.sources
    assert "mimiciv_hosp.diagnoses_icd" in hf.sources and hf.tier is None
    assert "m.codeset_id = 'heart_failure'" in hf.sql and "m.version = '1.0.0'" in hf.sql
    assert "c.discharge_location IN ('HOSPICE')" in hf.sql
    assert "to_days(CAST(365 AS BIGINT))" in hf.sql
    assert (
        "least(c.index_time + to_days(CAST(30 AS BIGINT)), c.last_dischtime + INTERVAL 365 DAY)"
        in hf.sql
    )
    # every criterion kind compiles; custom_sql is wrapped as a CTE and semi-joined, flagged
    sql = "SELECT hadm_id FROM mimiciv_hosp.admissions WHERE admission_type = 'URGENT'"
    crafted = spec_from_text(
        _BASE
        + f'  - {{label: urgent_sql, custom_sql: {{sql: "{sql}", hash: {sql_hash(sql)}}}}}\n'
        + "  - {label: hf, codeset: {ref: heart_failure@1.0.0, position: primary, "
        "lookback: prior_days, prior_days: 30}}\n"
        + "  - {label: sep, phenotype: {ref: sepsis3@1.0.0, when: within_hours, hours: 48}}\n"
        + "  - {label: lab, data_availability: {table: mimiciv_hosp.labevents, itemids: [50912], "
        "min_rows: 2}}\n"
        + "  - {label: prior, prior_admissions: {min: 1, max: 3, lookback_days: 365}}\n"
        + "  - {label: unit, demographic: {gender: [F], insurance: [Medicare, Medicaid]}}\n"
        + "  - {label: c, concept: {table: mimiciv_derived.charlson, "
        "column: charlson_comorbidity_index, op: '>=', value: 3}}\n"
        + "  - {label: stay, los: {min_hours: 24, of: hosp}}\n"
        + "era_filter: ['2014 - 2016']\n"
        + "washout: {rule: no_prior_icu, days: 30}\n"
    )
    compiled = compile_spec(
        crafted,
        codeset_kinds={"heart_failure@1.0.0": "icd_dx"},
        phenotype_grains={"sepsis3@1.0.0": "icustay"},
    )
    assert compiled.custom and compiled.step_names[2] == "era"
    custom_step = next(s for s in compiled.steps if s.name == "crit_02_urgent_sql")
    assert custom_step.custom and custom_step.kind == "custom_sql"
    assert "custom_02 AS (\n" in compiled.sql and "FROM custom_02 AS x" in compiled.sql
    assert "WHERE x.subject_id = c.subject_id AND x.hadm_id = c.hadm_id" in compiled.sql
    assert sum(1 for s in compiled.steps if s.custom) == 1
    assert "d.seq_num = 1" in compiled.sql and "to_days(CAST(30 AS BIGINT))" in compiled.sql
    assert 'phenotypes."sepsis3@1.0.0"' in compiled.sources and "abs(" in compiled.sql
    assert "t.itemid IN (50912)" in compiled.sql and ") >= 2" in compiled.sql
    assert "c.anchor_year_group IN ('2014 - 2016')" in compiled.sql
    assert "FROM mimiciv_icu.icustays AS e" in compiled.sql and compiled.step_names[-2] == "washout"
    assert "true AS custom_flag" in compiled.sql and "false AS custom_flag" in tracer.sql
    # refusals: los of icu without a stay index; a bin grain under a phenotype onset
    with pytest.raises(CompileError, match="los of icu"):
        compile_spec(
            spec_from_text(_BASE.replace("age: {min: 18}", "los: {max_hours: 4, of: icu}"))
        )
    onset_bins = spec_from_text(
        _BASE.replace(
            "grain: hadm\nindex_event: {rule: each_hadm}",
            "grain: icu_day\nindex_event: {phenotype_onset: sepsis3@1.0.0}",
        )
    )
    with pytest.raises(CompileError, match="does not expand"):
        compile_spec(onset_bins, phenotype_grains={"sepsis3@1.0.0": "icu_day"})
    with pytest.raises(CohortSpecError, match="placeholder"):
        spec_from_text(_BASE.replace("grain: hadm", "grain: note"))
    # every other grain / index form compiles (the SQL is a pure function of the spec)
    for grain, index in (
        ("subject", "{rule: first_hadm}"),
        ("subject", "{rule: first_icu_stay}"),
        ("icustay", "{rule: each_icustay}"),
        ("icu_day", "{rule: first_icu_stay}"),
        ("hour_bin", "{rule: each_icustay}"),
        ("hadm", "{phenotype_onset: sepsis_explicit@1.1.0}"),
        ("subject", "{concept_time: {table: mimiciv_hosp.labevents, column: charttime}}"),
        (
            "icustay",
            "{concept_time: {table: mimiciv_icu.chartevents, column: charttime, pick: last}}",
        ),
    ):
        text = _BASE.replace(
            "grain: hadm\nindex_event: {rule: each_hadm}", f"grain: {grain}\nindex_event: {index}"
        )
        one = compile_spec(spec_from_text(text), phenotype_grains={"sepsis_explicit@1.1.0": "hadm"})
        assert one.step_names[:2] == ("base", "idx") and one.step_names[-1] == "cohort"
        assert one.columns[-len(OUTPUT_COLUMNS) :] == OUTPUT_COLUMNS
        assert "day_index" in one.keys if grain == "icu_day" else "day_index" not in one.keys


# ---------------------------------------------------------------------------
# 2. Per-step attrition on a crafted synthetic population (temp DuckDB)
# ---------------------------------------------------------------------------

_CRAFTED = """\
id: crafted
version: "1.0.0"
title: crafted attrition
grain: hadm
index_event: {rule: each_hadm}
inclusion:
  - {label: hf_coded, codeset: {ref: hf@1.0.0}}
  - {label: adult, age: {min: 18}}
  - {label: ph, phenotype: {ref: ph@1.0.0, when: at}}
exclusion:
  - {label: hospice, demographic: {discharge_location: [HOSPICE]}}
washout: {rule: no_prior_hadm, days: 365, codeset: hf@1.0.0}
follow_up: {outcome: mortality_30d}
"""


def _crafted_population() -> duckdb.DuckDBPyConnection:
    """Six admissions of four synthetic patients (ids >= 90 000 000): every step of
    ``_CRAFTED`` drops something predictable."""
    con = duckdb.connect(":memory:")
    for schema in ("mimiciv_hosp", "mimiciv_icu", "meta", "phenotypes"):
        con.execute(f"CREATE SCHEMA {schema}")
    con.execute(
        "CREATE TABLE mimiciv_hosp.patients (subject_id INTEGER, gender VARCHAR, "
        "anchor_age INTEGER, anchor_year INTEGER, anchor_year_group VARCHAR, dod DATE)"
    )
    con.execute(
        "INSERT INTO mimiciv_hosp.patients VALUES "
        "(90000001, 'F', 91, 2150, '2014 - 2016', NULL), "  # the capped 91-year-old
        "(90000002, 'M', 40, 2150, '2017 - 2019', DATE '2150-01-20'), "  # dies day 20
        "(90000003, 'F', 10, 2150, '2011 - 2013', NULL), "  # a child
        "(90000004, 'M', 70, 2150, '2008 - 2010', NULL)"
    )
    con.execute(
        "CREATE TABLE mimiciv_hosp.admissions (subject_id INTEGER, hadm_id INTEGER, "
        "admittime TIMESTAMP, dischtime TIMESTAMP, admission_type VARCHAR, "
        "admission_location VARCHAR, discharge_location VARCHAR, insurance VARCHAR, "
        "language VARCHAR, marital_status VARCHAR, hospital_expire_flag INTEGER)"
    )
    rows = [
        (90000001, 91000001, "2150-01-01 08:00:00", "2150-01-05 12:00:00", "URGENT", "HOME", 0),
        (90000002, 91000002, "2150-01-01 09:00:00", "2150-01-03 10:00:00", "EW EMER.", "HOME", 0),
        (90000002, 91000003, "2150-06-01 09:00:00", "2150-06-05 10:00:00", "EW EMER.", "HOME", 0),
        (90000003, 91000004, "2150-03-01 09:00:00", "2150-03-02 10:00:00", "URGENT", "HOME", 0),
        (90000004, 91000005, "2150-01-01 09:00:00", "2150-01-10 10:00:00", "URGENT", "HOSPICE", 0),
        (90000004, 91000006, "2149-01-01 09:00:00", "2149-01-04 10:00:00", "URGENT", "HOME", 0),
    ]
    con.executemany(
        "INSERT INTO mimiciv_hosp.admissions VALUES (?, ?, ?, ?, ?, 'EMERGENCY ROOM', ?, "
        "'Medicare', 'ENGLISH', 'SINGLE', ?)",
        rows,
    )
    con.execute(
        "CREATE TABLE mimiciv_hosp.diagnoses_icd (subject_id INTEGER, hadm_id INTEGER, "
        "seq_num INTEGER, icd_code VARCHAR, icd_version INTEGER)"
    )
    con.executemany(
        "INSERT INTO mimiciv_hosp.diagnoses_icd VALUES (?, ?, ?, ?, ?)",
        [
            (90000001, 91000001, 1, "I509", 10),
            (90000002, 91000002, 1, "I509", 10),
            (90000002, 91000003, 2, "I509", 10),
            (90000003, 91000004, 1, "I509", 10),
            (90000004, 91000005, 1, "4280", 9),
            (90000004, 91000006, 1, "K219", 10),  # not heart failure
        ],
    )
    con.execute(
        "CREATE TABLE mimiciv_icu.icustays (subject_id INTEGER, hadm_id INTEGER, stay_id INTEGER, "
        "first_careunit VARCHAR, intime TIMESTAMP, outtime TIMESTAMP)"
    )
    con.execute(
        "CREATE TABLE meta.codeset_members (codeset_id VARCHAR, version VARCHAR, "
        "system VARCHAR, code VARCHAR)"
    )
    con.execute(
        "INSERT INTO meta.codeset_members VALUES ('hf', '1.0.0', 'icd10', 'I509'), "
        "('hf', '1.0.0', 'icd9', '4280')"
    )
    con.execute(
        'CREATE TABLE phenotypes."ph@1.0.0" (subject_id INTEGER, hadm_id INTEGER, flag BOOLEAN, '
        "onset_time TIMESTAMP)"
    )
    con.executemany(
        'INSERT INTO phenotypes."ph@1.0.0" VALUES (?, ?, ?, ?)',
        [
            (90000001, 91000001, True, "2150-01-02 00:00:00"),
            (90000002, 91000002, True, "2150-01-01 12:00:00"),
            (90000002, 91000003, True, "2150-06-02 00:00:00"),
            (90000003, 91000004, False, None),
            (90000004, 91000005, True, "2150-01-03 00:00:00"),
            (90000004, 91000006, False, None),
        ],
    )
    return con


def test_attrition_matches_hand_counts_on_a_crafted_population() -> None:
    spec = spec_from_text(_CRAFTED)
    compiled = compile_spec(
        spec, "fixture", codeset_kinds={"hf@1.0.0": "icd_dx"}, phenotype_grains={"ph@1.0.0": "hadm"}
    )
    assert compiled.step_names == (
        "base",
        "idx",
        "crit_01_hf_coded",
        "crit_02_adult",
        "crit_03_ph",
        "crit_04_hospice",
        "washout",
        "cohort",
    )
    con = _crafted_population()
    try:
        counts = {
            str(step): (int(n_units), int(n_subjects))
            for _i, step, n_units, n_subjects in con.execute(compiled.attrition_sql).fetchall()
        }
        # hand counts: 6 admissions / 4 patients; hf_coded drops the K21.9 admission;
        # adult drops the child's; the phenotype flags every remaining admission; hospice
        # drops the hospice discharge; the washout drops the June admission (a heart-failure
        # admission 151 days earlier); two admissions of two patients remain
        assert counts == {
            "base": (6, 4),
            "idx": (6, 4),
            "crit_01_hf_coded": (5, 4),
            "crit_02_adult": (4, 3),
            "crit_03_ph": (4, 3),
            "crit_04_hospice": (3, 2),
            "washout": (2, 2),
            "cohort": (2, 2),
        }
        rows = con.execute(compiled.sql).fetchall()
        columns = [d[0] for d in con.execute(f"DESCRIBE {compiled.sql}").fetchall()]
        assert (
            tuple(columns)
            == compiled.columns
            == (
                "subject_id",
                "hadm_id",
                *OUTPUT_COLUMNS,
            )
        )
        by_subject = {r[0]: dict(zip(columns, r, strict=True)) for r in rows}
        assert sorted(by_subject) == [90000001, 90000002], "ordered by the keys"
        capped = by_subject[90000001]
        assert capped["age_at_index"] == 91 and capped["age_capped"] is True
        assert capped["era_index"] == 2 and capped["censor_reason"] == "horizon"
        # the observation window [-24, 0) h ends at the index
        assert capped["obs_start"].isoformat() == "2149-12-31T08:00:00"
        assert capped["obs_end"].isoformat() == "2150-01-01T08:00:00"
        assert capped["follow_up_end"].isoformat() == "2150-01-31T08:00:00"
        assert capped["custom_flag"] is False
        died = by_subject[90000002]
        assert died["age_at_index"] == 40 and died["age_capped"] is False
        assert died["censor_reason"] == "death" and died["era_index"] == 3
        # the raw attrition rows and their suppression (chain mode, both count columns)
        raw = build_mod.attrition_rows_raw(compiled, con.execute(compiled.attrition_sql).fetchall())
        assert [r[1] for r in raw] == list(compiled.step_names)
        assert raw[-1][6:] == [2, 2] and raw[0][3] == "population"
        records = [
            dict(zip([c for c, _t in build_mod.ATTRITION_COLUMNS], r, strict=True)) for r in raw
        ]
        suppressed, report = build_mod.suppress_attrition(
            [{k: v for k, v in r.items() if k != "step_index"} for r in records], 11
        )
        assert len(suppressed) == 8 and all(r["n_units"] is None for r in suppressed)
        assert report["n_units_hidden"] >= 1 and all(r["dropped_units"] is None for r in suppressed)
        exact, _ = build_mod.suppress_attrition(
            [{k: v for k, v in r.items() if k != "step_index"} for r in records], 1
        )
        assert [r["n_units"] for r in exact] == [6, 6, 5, 4, 4, 3, 2, 2]
        assert [r["dropped_units"] for r in exact] == [None, 0, 1, 1, 0, 1, 1, 0]
        assert [r["dropped_subjects"] for r in exact] == [None, 0, 0, 1, 0, 1, 0, 0]
    finally:
        con.close()


# ---------------------------------------------------------------------------
# 3. A fixture lake through the runner: marts, registry, reproducibility, refusals
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def lake(tmp_path_factory: pytest.TempPathFactory) -> Settings:
    """A fixture lake with the chains' core tables, the dims, the compiled code sets, the
    registry index, both seed cohorts and the catalog."""
    root = tmp_path_factory.mktemp("cohort-marts")
    settings = config.Settings(data_root=root)
    result = runner_mod.run(
        load_dag(),
        "fixture",
        select=[
            *LAKE_STEPS,
            codesets_registry.STEP_COMPILE,
            registry_mod.STEP_SPECS,
            build_mod.STEP_BUILD,
            "catalog",
        ],
        settings=settings,
    )
    failed = [f"{s.name}: {s.error}" for s in result.steps if s.status == "failed"]
    assert not failed, failed
    return settings


def test_fixture_lake_marts_registry_and_reproducibility(
    lake: Settings, registry: Registry
) -> None:
    from mimicwarehouse.catalog.connect import open_catalog
    from mimicwarehouse.dag.snapshot import read_snapshots
    from mimicwarehouse.run import read_manifest
    from mimicwarehouse.safe import safe_query

    settings = lake
    lake_root = settings.lake_root("fixture")
    k = settings.k_suppression
    manifests: dict[str, dict[str, Any]] = {}
    for entry in registry:
        directory = build_mod.cohort_dir(lake_root, "fixture", entry.ref)
        assert directory == lake_root / "marts" / "fixture" / "cohorts" / entry.ref
        for name in (
            build_mod.COHORT_FILE,
            build_mod.ATTRITION_FILE,
            build_mod.SPEC_FILE,
            build_mod.MANIFEST_FILE,
        ):
            assert (directory / name).is_file(), name
        assert (directory / build_mod.SPEC_FILE).read_text(
            encoding="utf-8"
        ) == entry.path.read_text(encoding="utf-8")
        mart = build_mod.read_mart_manifest(lake_root, "fixture", entry.ref)
        assert mart is not None
        manifests[entry.ref] = mart
        assert mart["def_hash"] == entry.def_hash and mart["ref"] == entry.ref
        assert mart["cohort_sha256"] == _sha(directory / build_mod.COHORT_FILE)
        assert mart["sql_sha256"] == compile_entry(entry, registry).sql_sha256
        assert set(mart["snapshot_ids"]) == {"core"} and mart["rows"] >= mart["n_subjects"] > 0
        assert mart["steps"][0]["step"] == "base" and mart["attrition"][-1]["step"] == "cohort"
        assert mart["attrition"][-1]["n_units"] == mart["rows"]
        assert build_mod.cohort_complete(lake_root, "fixture", entry.ref)
        # the run record: statements, refs, the layer snapshot ids, a suppressed chain
        run = read_manifest(mart["run_id"], settings)
        assert run.kind == "cohort" and run.status == "ok" and run.tier == "fixture"
        assert set(run.sql) == {"cohort", "attrition"}
        assert run.snapshot_ids == mart["snapshot_ids"]
        assert run.snapshot_ids["core"] in {
            e["snapshot_id"] for e in read_snapshots(lake_root) if e["layer"] == "core"
        }
        assert {(r.kind, r.name) for r in run.refs} >= {("cohort", entry.spec.id)}
        assert run.params["def_hash"] == entry.def_hash and run.params["k"] == k
        assert [r.step for r in run.attrition] == list(
            mart["attrition"][i]["step"] for i in range(len(run.attrition))
        )
        _no_small_cells([r.n_units for r in run.attrition], k)
        _no_small_cells([r.n_subjects for r in run.attrition], k)
        # the marts layer snapshot is recorded and cites the cohort file
        marts_ids = [e for e in read_snapshots(lake_root) if e["layer"] == "marts"]
        assert marts_ids and marts_ids[-1]["tier"] == "fixture"
    hf_refs = {
        (r.kind, r.name, r.version) for r in read_manifest(manifests[HF]["run_id"], settings).refs
    }
    assert ("codeset", "heart_failure", "1.0.0") in hf_refs
    # the catalog: marts.cohorts (registry shape) + one view per (id, major)
    con = open_catalog("fixture", settings=settings)
    try:
        cols = [d[0] for d in con.execute("DESCRIBE marts.cohorts").fetchall()]
        assert cols == [c for c, _t in build_mod.REGISTRY_COLUMNS]
        assert not any(
            c.endswith("_id") and c != "cohort_id" and c != "run_id" and c != "build_id"
            for c in cols
        )
        rows = {
            f"{r[0]}@{r[1]}": r
            for r in con.execute(
                "SELECT cohort_id, version, def_hash, grain, tier, rows, n_subjects, n_steps, "
                "custom, run_id, sql_sha256, cohort_sha256, snapshot_core, path, view "
                "FROM marts.cohorts ORDER BY 1, 2"
            ).fetchall()
        }
        assert set(rows) == {TRACER, HF}
        for entry in registry:
            row = rows[entry.ref]
            mart = manifests[entry.ref]
            assert row[2] == entry.def_hash and row[3] == entry.spec.grain and row[4] == "fixture"
            assert row[5] == mart["rows"] and row[6] == mart["n_subjects"]
            assert row[7] == len(mart["steps"]) and row[8] is False
            assert row[9] == mart["run_id"] and row[10] == mart["sql_sha256"]
            assert row[11] == mart["cohort_sha256"] and row[12] == mart["snapshot_ids"]["core"]
            assert row[13] == f"lake/fixture/marts/fixture/cohorts/{entry.ref}"
            assert row[14] == build_mod.view_name(entry.spec.id, entry.spec.version)
            assert len(row[13]) <= 4 * build_mod.VALUE_MAX_CHARS
        views = {
            r[0]
            for r in con.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'marts'"
            ).fetchall()
        }
        assert views == {"cohorts", "cohort_first_icu_adults_v1", "cohort_hf_admissions_v1"}
        view_cols = [
            d[0] for d in con.execute("DESCRIBE marts.cohort_first_icu_adults_v1").fetchall()
        ]
        assert view_cols == ["subject_id", "hadm_id", "stay_id", *OUTPUT_COLUMNS]
        comment = con.execute(
            "SELECT comment FROM duckdb_tables() WHERE schema_name = 'marts' "
            "AND table_name = 'cohorts'"
        ).fetchone()
        assert comment is not None and "EP-47" in comment[0]
        n = con.execute("SELECT count(*) FROM marts.cohort_first_icu_adults_v1").fetchone()
        assert n is not None and n[0] == manifests[TRACER]["rows"]
    finally:
        con.close()
    # the brief's acceptance query: a registry read without a count column
    listing = safe_query(
        "SELECT cohort_id, version, tier, rows FROM marts.cohorts ORDER BY 1, 2, 3",
        tier="fixture",
        settings=settings,
        actor="test_ep47",
    )
    assert [r[0] for r in listing.df.rows()] == ["first_icu_adults", "hf_admissions"]
    # the view is a subject-keyed read: aggregates only, k-gated
    by_reason = safe_query(
        "SELECT censor_reason, count(*) AS n FROM marts.cohort_hf_admissions_v1 "
        "GROUP BY 1 ORDER BY 1",
        tier="fixture",
        settings=settings,
        actor="test_ep47",
    )
    assert set(by_reason.df["censor_reason"].to_list()) <= {"death", "horizon", "dod_visibility"}
    # a forced rebuild from the same spec + snapshot ids is byte-identical; unforced = skipped
    before = {ref: m["cohort_sha256"] for ref, m in manifests.items()}
    with build_mod.build_options(force=True):
        again = runner_mod.run(
            load_dag(), "fixture", select=[build_mod.STEP_BUILD], settings=settings
        )
    assert again.ok and again.steps[0].files == 2
    for ref, sha in before.items():
        assert _sha(build_mod.cohort_part(lake_root, "fixture", ref)) == sha, ref
        rebuilt = build_mod.read_mart_manifest(lake_root, "fixture", ref)
        assert rebuilt is not None and rebuilt["cohort_sha256"] == sha
        assert rebuilt["run_id"] != manifests[ref]["run_id"], "a new run per build"
    skipped = runner_mod.run(
        load_dag(), "fixture", select=[build_mod.STEP_BUILD], settings=settings
    )
    assert skipped.ok and skipped.steps[0].files == 0 and skipped.steps[0].rows == 0
    # a moved definition under a released version refuses unless forced
    study = Path(str(settings.data_root)) / "study"
    study.mkdir(exist_ok=True)
    path = study / "moved.yaml"
    path.write_text(_BASE.replace("id: crafted", "id: moved"), encoding="utf-8", newline="\n")
    with build_mod.build_options(select=["moved@1.0.0"], extra_dirs=[study]):
        first = runner_mod.run(
            load_dag(), "fixture", select=[build_mod.STEP_BUILD], settings=settings
        )
    assert first.ok and build_mod.cohort_complete(lake_root, "fixture", "moved@1.0.0")
    path.write_text(
        _BASE.replace("id: crafted", "id: moved").replace("age: {min: 18}", "age: {min: 65}"),
        encoding="utf-8",
        newline="\n",
    )
    with build_mod.build_options(select=["moved@1.0.0"], extra_dirs=[study]):
        refused = runner_mod.run(
            load_dag(), "fixture", select=[build_mod.STEP_BUILD], settings=settings
        )
    assert not refused.ok and "--force" in (refused.steps[0].error or "")
    assert "CohortBuildError" in (refused.steps[0].error or "")
    attempt = build_mod.cohort_attempt(lake_root, "fixture", "moved@1.0.0") or {}
    assert attempt["status"] == "failed" and attempt["error_class"] == "CohortBuildError"
    with build_mod.build_options(select=["moved@1.0.0"], extra_dirs=[study], force=True):
        forced = runner_mod.run(
            load_dag(), "fixture", select=[build_mod.STEP_BUILD], settings=settings
        )
    assert forced.ok
    moved = build_mod.read_mart_manifest(lake_root, "fixture", "moved@1.0.0")
    assert moved is not None and moved["def_hash"] != first.build_id
    # a missing reference refuses with the remedy (keep-going: recorded as failed)
    (study / "needs_ph.yaml").write_text(
        _BASE.replace("id: crafted", "id: needs_ph").replace(
            "age: {min: 18}", "phenotype: {ref: t2dm@1.0.0, when: before}"
        ),
        encoding="utf-8",
        newline="\n",
    )
    with build_mod.build_options(select=["needs_ph@1.0.0"], extra_dirs=[study]):
        missing = runner_mod.run(
            load_dag(), "fixture", select=[build_mod.STEP_BUILD], settings=settings
        )
    assert not missing.ok and "mwh phenotype compile t2dm@1.0.0 --tier fixture" in (
        missing.steps[0].error or ""
    )
    _drop_progress_handlers()


# ---------------------------------------------------------------------------
# 4. attrition(): suppressed chains, by ref and by run id; the probe over the cohort
# ---------------------------------------------------------------------------


def test_attrition_accessor_and_probe_over_the_compiled_cohort(
    lake: Settings, registry: Registry
) -> None:
    settings = lake
    k = settings.k_suppression
    lake_root = settings.lake_root("fixture")
    result = build_mod.attrition(TRACER, "fixture", settings=settings)
    assert result.ref == TRACER and result.k == k == 11 and result.tier == "fixture"
    df = result.df
    assert df.columns[:9] == [
        "step",
        "label",
        "polarity",
        "kind",
        "custom",
        "n_units",
        "n_units_suppressed",
        "n_units_banded",
        "dropped_units",
    ]
    assert df["step"].to_list() == [
        "base",
        "idx",
        "crit_01_adult",
        "crit_02_short_icu_stay",
        "cohort",
    ]
    # the fixture's tracer chain has a small drop (the 4-hour exclusion): no exact count
    # below k leaves the accessor - small totals are None, banded totals are marked
    for column, banded in (("n_units", "n_units_banded"), ("n_subjects", "n_subjects_banded")):
        exact = [
            v for v, b in zip(df[column].to_list(), df[banded].to_list(), strict=True) if not b
        ]
        _no_small_cells(exact, k)
    _no_small_cells(df["dropped_units"].to_list(), k)
    _no_small_cells(df["dropped_subjects"].to_list(), k)
    assert result.report["n_units_hidden"] + result.report["n_units_banded"] > 0
    assert result.def_hash == registry.get(TRACER).def_hash and result.run_id
    # k = 1 on the synthetic tier releases the raw chain; the run id resolves the same mart
    raw = build_mod.attrition(TRACER, "fixture", settings=settings, k=1).df
    mart = build_mod.read_mart_manifest(lake_root, "fixture", TRACER)
    assert mart is not None
    assert raw["n_units"].to_list() == [r["n_units"] for r in mart["attrition"]]
    assert raw["dropped_units"].to_list()[1:] == [
        a["n_units"] - b["n_units"] for a, b in itertools.pairwise(mart["attrition"])
    ]
    by_run = build_mod.attrition(mart["run_id"], "fixture", settings=settings, k=1)
    assert by_run.ref == TRACER and by_run.run_id == mart["run_id"]
    with pytest.raises(CohortSpecError, match="no built cohort"):
        build_mod.attrition("nope@1.0.0", "fixture", settings=settings)
    with pytest.raises(CohortSpecError, match="not a reference"):
        build_mod.attrition("nope", "fixture", settings=settings)
    # the EP-31 policy's spec-level half now runs over the compiled cohort
    tv = probe_mod.validate_on_tier(
        registry.get(TRACER), tier="fixture", settings=settings, k=1, actor="test_ep47"
    )
    assert tv.ok and tv.population == "cohort"
    assert tv.cohort_relation == "marts.cohort_first_icu_adults_v1"
    for probe in tv.probes:
        assert sum(r.n for r in probe.rows) == mart["rows"], probe.column
        assert "FROM marts.cohort_first_icu_adults_v1" in probe.sql
    assert tv.to_dict()["population"] == "cohort"
    index_only = probe_mod.validate_on_tier(
        registry.get(TRACER), tier="fixture", settings=settings, k=1, population="index"
    )
    assert index_only.population == "index" and index_only.cohort_relation is None
    assert sum(r.n for r in index_only.probes[0].rows) == mart["attrition"][1]["n_units"]
    assert (
        probe_mod.built_relation(registry.get(HF), tier="fixture", settings=settings)
        == "marts.cohort_hf_admissions_v1"
    )
    with pytest.raises(ValueError, match=re.escape("auto | index | cohort")):
        probe_mod.validate_on_tier(
            registry.get(TRACER), tier="fixture", settings=settings, population="x"
        )
    _drop_progress_handlers()


# ---------------------------------------------------------------------------
# 5. The CLI: build (dry-run, end to end, refusals), attrition, validate --tier
# ---------------------------------------------------------------------------


def test_cli_build_attrition_and_validate(lake: Settings, registry: Registry) -> None:
    runner = helpers.cli_runner()
    root = str(lake.data_root)
    try:
        dry = runner.invoke(app, ["cohort", "build", TRACER, "--dry-run"])
        assert dry.exit_code == 0, dry.output
        assert "WITH base AS (" in dry.stdout and f"-- {TRACER}: 5 step(s)" in dry.stdout
        dry_json = runner.invoke(app, ["cohort", "build", HF, "--dry-run", "--json"])
        assert dry_json.exit_code == 0, dry_json.output
        payload = json.loads(dry_json.stdout)["cohorts"][0]
        assert payload["ref"] == HF and payload["steps"][-1]["step"] == "cohort"
        assert payload["sql_sha256"] == compile_entry(registry.get(HF), registry).sql_sha256
        bad = runner.invoke(app, ["cohort", "build", TRACER, "--dry-run", "--background"])
        assert bad.exit_code == 2 and "drop --background" in bad.stderr
        no_job = runner.invoke(
            app, ["--data-root", root, "cohort", "build", TRACER, "--background"]
        )
        assert no_job.exit_code == 2 and "--job" in no_job.stderr
        unknown = runner.invoke(app, ["cohort", "build", "nope@1.0.0", "--dry-run"])
        assert unknown.exit_code == 2 and "known ids" in unknown.stderr
        # end to end on the fixture lake (forced, so it rebuilds): the table, the counts, the probe
        built = runner.invoke(
            app,
            [
                "--data-root",
                root,
                "cohort",
                "build",
                TRACER,
                "--tier",
                "fixture",
                "--force",
                "--json",
            ],
        )
        assert built.exit_code == 0, built.output
        out = json.loads(built.stdout)
        assert out["ok"] and out["cohorts"][0]["status"] == "done"
        assert out["cohorts"][0]["built_def_hash"] == registry.get(TRACER).def_hash
        assert out["probes"] and out["probes"][0]["population"] == "cohort"
        plain = runner.invoke(
            app, ["--data-root", root, "cohort", "build", TRACER, "--tier", "fixture", "--force"]
        )
        assert plain.exit_code == 0, plain.output
        assert (
            "cohorts.build" in plain.stdout
            and "degeneracy probe over the compiled cohort" in plain.stdout
        )
        assert f"{TRACER}: done" in plain.stdout and "cohort sha256" in plain.stdout
        # attrition: the suppressed chain, --json carries the markers, a missing cohort exits 1
        att = runner.invoke(
            app, ["--data-root", root, "cohort", "attrition", TRACER, "--tier", "fixture"]
        )
        assert att.exit_code == 0, att.output
        assert "crit_02_short_icu_stay" in att.stdout and "k=11" in att.stdout
        assert "<11" in att.stdout or "~" in att.stdout
        att_json = runner.invoke(
            app,
            [
                "--data-root",
                root,
                "cohort",
                "attrition",
                TRACER,
                "--tier",
                "fixture",
                "--k",
                "1",
                "--json",
            ],
        )
        assert att_json.exit_code == 0, att_json.output
        chain = json.loads(att_json.stdout)
        assert chain["k"] == 1 and chain["rows"][0]["step"] == "base"
        assert all("n_units_banded" in r for r in chain["rows"])
        missing = runner.invoke(
            app, ["--data-root", root, "cohort", "attrition", "nope@1.0.0", "--tier", "fixture"]
        )
        assert missing.exit_code == 1 and "no built cohort" in missing.stderr
        bad_tier = runner.invoke(
            app, ["--data-root", root, "cohort", "attrition", TRACER, "--tier", "x"]
        )
        assert bad_tier.exit_code == 2
        # validate --tier reports the population the probe ran over
        validate = runner.invoke(
            app, ["--data-root", root, "cohort", "validate", HF, "--tier", "fixture", "--k", "1"]
        )
        assert validate.exit_code == 0, validate.output
        assert (
            "degeneracy probe over the compiled cohort (marts.cohort_hf_admissions_v1)"
            in validate.stdout
        )
        as_json = runner.invoke(
            app,
            [
                "--data-root",
                root,
                "cohort",
                "validate",
                HF,
                "--tier",
                "fixture",
                "--k",
                "1",
                "--json",
            ],
        )
        assert json.loads(as_json.stdout)["tier"]["population"] == "cohort"
    finally:
        config.configure()
        _drop_progress_handlers()
    top = runner.invoke(app, ["cohort", "--help"])
    assert top.exit_code == 0
    for sub in ("list", "show", "validate", "schema", "lock", "build", "attrition"):
        assert sub in top.stdout, sub
    helpers.assert_import_budget(
        "mimicwarehouse.cohort.cli",
        lazy=(
            "mimicwarehouse.safe",
            "mimicwarehouse.catalog.build",
            "mimicwarehouse.dag.runner",
            "mimicwarehouse.concepts.runner",
            "mimicwarehouse.concepts.inventory",
            "mimicwarehouse.units",
            "mimicwarehouse.cohort.probe",
            "mimicwarehouse.cohort.build",
            "mimicwarehouse.cohort.compiler",
            "mimicwarehouse.schema.contract",
        ),
    )
    helpers.assert_import_budget(lazy=("mimicwarehouse.safe", "mimicwarehouse.catalog.build"))


# ---------------------------------------------------------------------------
# 6. Wiring: the DAG step, the catalog extension, the session fixture lake, the docs
# ---------------------------------------------------------------------------


def test_dag_wiring_and_session_lake(
    fixture_lake_catalog: duckdb.DuckDBPyConnection, registry: Registry
) -> None:
    dag = load_dag()
    step = dag.step(build_mod.STEP_BUILD)
    assert step.kind == "python" and step.callable_name == "mimicwarehouse.cohort.build:run_build"
    assert step.qualified_table is None and "marts" in step.tags and "cohorts" not in step.tags
    assert set(step.depends_on) >= {
        registry_mod.STEP_SPECS,
        codesets_registry.STEP_COMPILE,
        "phenotypes.compile",
        f"stage.{HOSP}.patients",
        f"stage.{HOSP}.admissions",
        f"stage.{HOSP}.diagnoses_icd",
        f"stage.{ICU}.icustays",
    }
    catalog = dag.step("catalog")
    assert build_mod.STEP_BUILD in catalog.depends_on and "marts" in catalog.tags
    assert [s.name for s in dag.ordered(tags=["marts"], tier="fixture")] == [
        build_mod.STEP_BUILD,
        "catalog",
    ]
    assert [s.name for s in dag.ordered(tags=[registry_mod.DAG_TAG], tier="fixture")] == [
        registry_mod.STEP_SPECS,
        "catalog",
    ], "EP-46's --tag cohorts is unchanged"
    from mimicwarehouse import safe

    assert "marts.cohorts" in safe.REGISTRY_TABLES
    # the session lake (conftest, the full DAG) built both seeds and registered them
    rows = fixture_lake_catalog.execute(
        "SELECT cohort_id, version, def_hash, view FROM marts.cohorts ORDER BY 1"
    ).fetchall()
    assert [(r[0], r[1]) for r in rows] == [
        ("first_icu_adults", "1.0.0"),
        ("hf_admissions", "1.0.0"),
    ]
    assert {r[2] for r in rows} == {e.def_hash for e in registry}
    assert [r[3] for r in rows] == ["cohort_first_icu_adults_v1", "cohort_hf_admissions_v1"]
    for view in ("cohort_first_icu_adults_v1", "cohort_hf_admissions_v1"):
        n = fixture_lake_catalog.execute(f"SELECT count(*) FROM marts.{view}").fetchone()
        assert n is not None and n[0] > 0


def test_methods_doc_compilation_section() -> None:
    text = registry_mod.methods_doc_path().read_text(encoding="utf-8")
    for needle in (
        "## 7. Compilation",
        "crit_",
        "byte-identical",
        "lake/marts/<tier>/cohorts/",
        "marts.cohort_<id>_v<major>",
        "mwh cohort build",
        "mwh cohort attrition",
        "attrition.parquet",
        "chain mode",
        "censor_reason",
        "age_capped",
        "custom_sql",
        "EP-48",
    ):
        assert needle in text, needle
    assert not BAND_TOKEN.search(text)


# ---------------------------------------------------------------------------
# 7. Dev / full tiers (aggregates only)
# ---------------------------------------------------------------------------


@pytest.mark.tier("dev")
def test_dev_builds_registry_and_chains(dev_catalog: Path, registry: Registry) -> None:
    from mimicwarehouse.safe import safe_query

    settings = config.load_settings()
    remedy = (
        "run `mwh cohort build first_icu_adults@1.0.0 --tier dev` and "
        "`... hf_admissions@1.0.0 --tier dev` (EP-47) first"
    )
    index = safe_query(
        "SELECT cohort_id, version, def_hash, rows, view FROM marts.cohorts ORDER BY 1, 2",
        tier="dev",
        settings=settings,
        actor="test_ep47",
    ).df
    built = {f"{i}@{v}": (h, n, view) for i, v, h, n, view in index.rows()}
    assert set(built) >= {TRACER, HF}, remedy
    for entry in registry:
        def_hash, rows, view = built[entry.ref]
        assert def_hash == entry.def_hash, entry.ref
        assert rows is None or rows >= 11
        assert view == build_mod.view_name(entry.spec.id, entry.spec.version)
        chain = build_mod.attrition(entry.ref, "dev", settings=settings)
        assert chain.k == 11 and chain.df["step"][0] == "base"
        for column, banded in (("n_units", "n_units_banded"), ("n_subjects", "n_subjects_banded")):
            exact = [
                v
                for v, b in zip(chain.df[column].to_list(), chain.df[banded].to_list(), strict=True)
                if not b
            ]
            _no_small_cells(exact, 11)
        _no_small_cells(chain.df["dropped_units"].to_list(), 11)
        probe = probe_mod.validate_on_tier(entry, tier="dev", settings=settings, actor="test_ep47")
        assert probe.ok and probe.population == "cohort", (entry.ref, probe.problems)
        print(f"dev: {entry.ref} {len(chain.df)} step(s), probe over {probe.cohort_relation}")
    with pytest.raises(CohortSpecError, match="credentialed floor"):
        build_mod.attrition(TRACER, "dev", settings=settings, k=1)


@pytest.mark.tier("full")
def test_full_tracer_cohort_built(full_catalog: Path, registry: Registry) -> None:
    from mimicwarehouse.safe import safe_query

    settings = config.load_settings()
    index = safe_query(
        "SELECT cohort_id, version, def_hash, view FROM marts.cohorts "
        "WHERE cohort_id = 'first_icu_adults'",
        tier="full",
        settings=settings,
        actor="test_ep47",
    ).df
    rows = {f"{i}@{v}": (h, view) for i, v, h, view in index.rows()}
    assert TRACER in rows, (
        "run `mwh cohort build first_icu_adults@1.0.0 --tier full --background "
        "--job ep47-cohort-full`"
    )
    assert rows[TRACER] == (registry.get(TRACER).def_hash, "cohort_first_icu_adults_v1")
    chain = build_mod.attrition(TRACER, "full", settings=settings)
    assert chain.df["step"].to_list() == [
        "base",
        "idx",
        "crit_01_adult",
        "crit_02_short_icu_stay",
        "cohort",
    ]
    _no_small_cells(chain.df["dropped_units"].to_list(), 11)
