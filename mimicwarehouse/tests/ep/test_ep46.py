"""EP-46 — Cohort spec + registry.

Fixture tier (default): the two packaged seed specs load, are locked and hygienic, pin
their references by hash and mirror the EP-31 tracer (``first_icu_adults``) / reference a
code set by ``id@version`` (``hf_admissions``); the schema round-trips YAML with a
``def_hash`` that is invariant to key order, whitespace, defaults and documentation and
moves with the definition or a referenced hash; the schema refuses the crafted violations
(an absolute date, an unavailable grain, an unknown code-set version, …) with a clear
message; a frozen ``(id, version)`` whose file changed is refused (library and every ``mwh
cohort`` command, exit 3) while a version bump loads unlocked and locks; the JSON schema
exports and re-validates the seeds; static validation names the cross-registry problems;
the DAG spec, the catalog hook, the CLI wiring and the import budget; a minimal fixture
lake built through the runner carries ``meta.cohort_specs``, the references resolve
against ``meta.codesets`` and the degeneracy probe returns suppressed aggregates; the
session fixture lake shows the registry after the full DAG; the docs page is in sync.
``tier("dev")``: the references and the probe on the real dev catalog through
``safe_query`` (counts only), and ``meta.cohort_specs`` carries the seeds.

Everything asserted or printed is definition text, SQL, level labels (public vocabulary)
and aggregate counts — never a patient-level row.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

import duckdb
import pytest
import yaml

import helpers
from mimicwarehouse import config, timesem
from mimicwarehouse.catalog import build as build_mod
from mimicwarehouse.cli import app
from mimicwarehouse.codesets import registry as codesets_registry
from mimicwarehouse.codesets.spec import CodeSetFrozenError
from mimicwarehouse.cohort import probe as probe_mod
from mimicwarehouse.cohort import registry as registry_mod
from mimicwarehouse.cohort.spec import (
    CRITERION_KINDS,
    DEFAULT_PROBE_COLUMNS,
    INDEX_EVENT_KINDS,
    CohortSpec,
    CohortSpecError,
    CohortSpecFrozenError,
    UnknownCohortSpecError,
    json_schema,
    spec_from_text,
    sql_hash,
)
from mimicwarehouse.dag import runner as runner_mod
from mimicwarehouse.dag import spec as dagspec_mod
from mimicwarehouse.dag.spec import load_dag
from mimicwarehouse.phenotypes import registry as phenotypes_registry
from mimicwarehouse.phenotypes.spec import PhenotypeFrozenError

if TYPE_CHECKING:
    from mimicwarehouse.cohort.registry import Registry
    from mimicwarehouse.config import Settings

pytestmark = pytest.mark.ep_46

HOSP = "mimiciv_hosp"
ICU = "mimiciv_icu"
BAND_TOKEN = re.compile(r"(?<![\w.])[123]\d{7}(?![\w.])")
TRACER = "first_icu_adults@1.0.0"
HF = "hf_admissions@1.0.0"
#: The stage steps the tier-level checks need (the probe's population + the dims the
#: code-set registry expands against).
LAKE_STEPS: tuple[str, ...] = (
    f"stage.{HOSP}.patients",
    f"stage.{HOSP}.admissions",
    f"stage.{ICU}.icustays",
    f"stage.{HOSP}.d_icd_diagnoses",
    f"stage.{HOSP}.d_icd_procedures",
    f"stage.{HOSP}.d_hcpcs",
    f"stage.{HOSP}.d_labitems",
    f"stage.{ICU}.d_items",
)
#: ``inclusion`` is the last key so a test can append criteria (or top-level keys) to it.
_BASE = """\
id: crafted
version: "1.0.0"
title: crafted
grain: icustay
index_event: {rule: first_icu_stay}
exclusion: []
follow_up: {outcome: in_hospital_mortality}
inclusion:
  - {label: adult, age: {min: 18}}
"""
_HASHES: dict[str, dict[str, str]] = {"codeset": {}, "phenotype": {}}


@pytest.fixture(scope="module")
def registry() -> Registry:
    return registry_mod.load_registry()


def _drop_progress_handlers() -> None:
    import logging

    logger = logging.getLogger("mimicwarehouse")
    for handler in list(logger.handlers):
        logger.removeHandler(handler)


def _scalar(con: duckdb.DuckDBPyConnection, sql: str) -> Any:
    row = con.execute(sql).fetchone()
    assert row is not None
    return row[0]


def _refused(text: str, match: str) -> None:
    with pytest.raises(CohortSpecError, match=match):
        spec_from_text(text)


# ---------------------------------------------------------------------------
# 1. The seed specs: locked, hygienic, references pinned, the tracer mirrored
# ---------------------------------------------------------------------------


def test_seed_specs_locked_and_pinned(registry: Registry) -> None:
    assert registry.refs() == (TRACER, HF) and registry.ids() == (
        "first_icu_adults",
        "hf_admissions",
    )
    tracer = registry.get(TRACER)
    spec = tracer.spec
    assert (
        tracer.locked and tracer.packaged and tracer.display_path == "specs/first_icu_adults.yaml"
    )
    # the tracer bullet's cohort, worded like EP-31 (index rule = the tracer's first stay)
    assert spec.grain == "icustay" and spec.index_event.rule == "first_icu_stay"
    assert spec.index_event.rule in timesem.ICUSTAY.index_rules
    assert [c.label for c in spec.inclusion] == ["adult"] and spec.inclusion[0].kind == "age"
    assert spec.inclusion[0].age is not None and spec.inclusion[0].age.min == 18
    assert [c.label for c in spec.exclusion] == ["short_icu_stay"]
    assert spec.exclusion[0].los is not None and spec.exclusion[0].los.max_hours == 4
    assert spec.exclusion[0].los.of == "icu"
    assert spec.follow_up.outcome == "in_hospital_mortality"
    assert spec.follow_up.competing_events == (timesem.DISCHARGE_ALIVE,)
    assert spec.follow_up.rule.competing_events == (timesem.DISCHARGE_ALIVE,)
    assert spec.follow_up.rule.horizon_days is None
    assert (spec.observation_window.start_h, spec.observation_window.end_h) == (-24, 0)
    assert spec.washout.rule == "none" and spec.era_filter == ()
    assert spec.degeneracy_probe.columns == DEFAULT_PROBE_COLUMNS
    from mimicwarehouse import tracer as tracer_mod

    assert all(f"C({column})" in tracer_mod.MODEL_FORMULA for column in DEFAULT_PROBE_COLUMNS)
    assert spec.codeset_refs == () and spec.phenotype_refs == () and not spec.custom
    assert tracer.resolved == {"codeset": {}, "phenotype": {}} and tracer.flat_refs == {}
    assert re.fullmatch(r"[0-9a-f]{64}", tracer.def_hash)
    assert len(spec.what_it_does_not_claim) >= 3 and spec.notes and spec.description
    # hf_admissions: a code set referenced by id@version, resolved to the EP-40 hash
    hf = registry.get(HF)
    assert hf.locked and hf.spec.grain == "hadm" and hf.spec.index_event.rule == "each_hadm"
    assert hf.spec.codeset_refs == ("heart_failure@1.0.0",) and hf.spec.phenotype_refs == ()
    assert hf.resolved["codeset"] == {
        "heart_failure@1.0.0": registry.codesets.get("heart_failure@1.0.0").codeset.def_hash
    }
    assert hf.flat_refs == {
        "codeset:heart_failure@1.0.0": hf.resolved["codeset"]["heart_failure@1.0.0"]
    }
    assert [c.kind for _p, c in hf.spec.criteria()] == ["codeset", "age", "demographic"]
    assert hf.spec.washout.rule == "no_prior_hadm" and hf.spec.washout.days == 365
    assert hf.spec.washout.codeset == "heart_failure@1.0.0"
    assert hf.spec.follow_up.outcome == "mortality_30d" and hf.spec.follow_up.horizon_days == 30
    assert hf.spec.exclusion[0].demographic is not None
    assert hf.spec.exclusion[0].demographic.fields() == (("discharge_location", ("HOSPICE",)),)
    assert hf.def_hash != tracer.def_hash
    for entry in registry:
        raw = entry.path.read_bytes()
        assert b"\r" not in raw and raw.endswith(b"\n") and raw.decode("utf-8").isascii()
        assert not BAND_TOKEN.search(raw.decode("utf-8"))
        assert entry.spec.references is not None, "the seeds declare their references"
    # the lock file: one entry per seed with the current hash
    lock = registry_mod.load_lock(registry_mod.packaged_specs_dir())
    assert lock.version == 1 and set(lock.cohorts) == {TRACER, HF}
    for entry in registry:
        assert lock.cohorts[entry.ref].def_hash == entry.def_hash
        assert lock.cohorts[entry.ref].grain == entry.spec.grain
        assert lock.cohorts[entry.ref].locked_at == "2026-09-16"
    lock_text = registry_mod.lock_path(registry_mod.packaged_specs_dir()).read_text(
        encoding="utf-8"
    )
    assert lock_text.isascii() and lock_text.endswith("\n")
    runner = helpers.cli_runner()
    check = runner.invoke(app, ["cohort", "lock", "--check"])
    assert check.exit_code == 0 and "0 unlocked" in check.stdout, check.output
    listing = runner.invoke(app, ["cohort", "list", "--json"])
    assert listing.exit_code == 0, listing.output
    payload = {x["ref"]: x for x in json.loads(listing.stdout)["cohorts"]}
    assert set(payload) == {TRACER, HF} and payload[HF]["locked"]
    assert payload[HF]["def_hash"] == hf.def_hash and payload[HF]["references"] == hf.resolved
    assert payload[TRACER]["criteria"][0]["label"] == "adult"
    plain = runner.invoke(app, ["cohort", "list"])
    assert (
        plain.exit_code == 0 and "all locked" in plain.stdout and "2 cohort spec(s)" in plain.stdout
    )


# ---------------------------------------------------------------------------
# 2. YAML round trip, hash invariance and movement
# ---------------------------------------------------------------------------

_TEXT_A = """\
id: crafted
version: "1.0.0"
title: crafted
description: first spelling
grain: hadm
index_event: {rule: each_hadm}
inclusion:
  - {label: hf, codeset: {ref: heart_failure@1.0.0}}
  - {label: adult, age: {min: 18, at: index}}
exclusion:
  - {label: hospice, description: doc, demographic: {discharge_location: [HOSPICE]}}
observation_window: {start_h: -24, end_h: 0}
washout: {rule: none}
follow_up: {outcome: mortality_30d, horizon_days: 30, competing_events: []}
era_filter: []
notes: a note
"""
_TEXT_B = """\
notes: >
  a different note, keys shuffled, documentation changed
what_it_does_not_claim: [nothing at all]
follow_up:
  outcome: mortality_30d
exclusion:
  - demographic:
      discharge_location:
        - HOSPICE
    label: hospice
inclusion:
  - codeset:
      lookback: same_admission
      position: any
      ref: heart_failure@1.0.0
    label: hf
    description: documented differently
  - age: {at: index, min: 18.0}
    label: adult
degeneracy_probe: {columns: [gender, admission_type, first_careunit, anchor_year_group]}
index_event:
  rule: each_hadm
grain: hadm
title: crafted (renamed)
version: "1.0.0"
id: crafted
"""
_A_HASHES: dict[str, dict[str, str]] = {
    "codeset": {"heart_failure@1.0.0": "a" * 64},
    "phenotype": {},
}


def test_yaml_round_trip_and_hash_invariance(registry: Registry, tmp_path: Path) -> None:
    for entry in registry:
        spec = entry.spec
        text = spec.to_yaml()
        again = CohortSpec.from_yaml(text)
        assert again == spec and again.to_yaml() == text, "to_yaml is stable"
        assert list(yaml.safe_load(text))[:4] == ["id", "version", "title", "description"]
        assert 'version: "1.0.0"' in text and text.isascii()
        assert again.def_hash(entry.resolved) == entry.def_hash
        saved = registry_mod.save_spec(spec, tmp_path / "out")
        assert (
            saved.name == registry_mod.spec_filename(spec)
            and saved.read_text(encoding="utf-8") == text
        )
        with pytest.raises(CohortSpecError, match="exists"):
            registry_mod.save_spec(spec, tmp_path / "out")
        assert registry_mod.save_spec(spec, tmp_path / "out", overwrite=True) == saved
    a = spec_from_text(_TEXT_A)
    b = spec_from_text(_TEXT_B)
    assert a.canonical(_A_HASHES) == b.canonical(_A_HASHES)
    assert a.def_hash(_A_HASHES) == b.def_hash(_A_HASHES) and len(a.def_hash(_A_HASHES)) == 64
    assert a.codeset_refs == ("heart_failure@1.0.0",) and a.custom is False
    assert [(p, c.label) for p, c in a.criteria()] == [
        ("inclusion", "hf"),
        ("inclusion", "adult"),
        ("exclusion", "hospice"),
    ]
    from mimicwarehouse.codesets.spec import canonical_json

    assert canonical_json(a.canonical(_A_HASHES)).isascii()
    # the hash moves with the definition ...
    for old, new in (
        ("min: 18, at: index", "min: 21, at: index"),
        ("start_h: -24, end_h: 0", "start_h: -48, end_h: 0"),
        ("washout: {rule: none}", "washout: {rule: no_prior_hadm, days: 30}"),
        ("era_filter: []", "era_filter: ['2014 - 2016']"),
        ("horizon_days: 30", "horizon_days: 90"),
        ("label: adult", "label: grown"),
        (
            "grain: hadm\nindex_event: {rule: each_hadm}",
            "grain: hadm\nindex_event: {rule: first_hadm}",
        ),
        ("exclusion:\n  - {label: hospice", "exclusion:\n  - {label: hospice2"),
    ):
        moved = spec_from_text(_TEXT_A.replace(old, new))
        assert moved.def_hash(_A_HASHES) != a.def_hash(_A_HASHES), (old, new)
    # ... and with a referenced code set's hash, never with documentation
    assert a.def_hash({"codeset": {"heart_failure@1.0.0": "c" * 64}}) != a.def_hash(_A_HASHES)
    assert spec_from_text(_TEXT_A.replace("notes: a note", "notes: another")).def_hash(
        _A_HASHES
    ) == a.def_hash(_A_HASHES)
    with pytest.raises(CohortSpecError, match="unresolved"):
        a.def_hash(_HASHES)
    # defaults are explicit in the canonical form (an omitted lookback hashes as same_admission)
    explicit = spec_from_text(
        _TEXT_A.replace(
            "codeset: {ref: heart_failure@1.0.0}",
            "codeset: {ref: heart_failure@1.0.0, position: any, lookback: same_admission}",
        )
    )
    assert explicit.def_hash(_A_HASHES) == a.def_hash(_A_HASHES)
    # multi-line strings round-trip as literal blocks; a custom_sql spec round-trips too
    sql = "SELECT stay_id\nFROM mimiciv_icu.icustays\nWHERE los > 1"
    custom = spec_from_text(
        _BASE
        + "  - label: custom\n"
        + "    custom_sql:\n"
        + "      sql: |\n"
        + "        SELECT stay_id\n"
        + "        FROM mimiciv_icu.icustays\n"
        + "        WHERE los > 1\n"
        + f"      hash: {sql_hash(sql)}\n"
    )
    last = custom.inclusion[-1]
    assert custom.custom and last.custom and last.kind == "custom_sql"
    assert last.custom_sql is not None and last.custom_sql.sql.rstrip("\n") == sql
    assert CohortSpec.from_yaml(custom.to_yaml()) == custom and "sql: |" in custom.to_yaml()
    canonical_sql = custom.canonical(_HASHES)["inclusion"][-1]["custom_sql"]["sql"]
    assert canonical_sql == "SELECT stay_id FROM mimiciv_icu.icustays WHERE los > 1"


# ---------------------------------------------------------------------------
# 3. Refusals: dates, grains, rules, criteria, references
# ---------------------------------------------------------------------------


def test_schema_refusals() -> None:
    base = _BASE
    # absolute dates anywhere in the definition, as strings or YAML date objects
    dated = (
        "  - {label: x, concept: {table: mimiciv_hosp.admissions, column: admittime, "
        "op: '>=', value: VALUE}}\n"
    )
    _refused(base + dated.replace("VALUE", "'2150-01-01'"), "absolute dates")
    _refused(base + dated.replace("VALUE", "2150-01-01"), "absolute dates")
    _refused(base + dated.replace("VALUE", "'01/02/2150'"), "absolute dates")
    sql = "SELECT stay_id FROM mimiciv_icu.icustays WHERE intime > DATE '2150-01-01'"
    _refused(
        base + f'  - {{label: x, custom_sql: {{sql: "{sql}", hash: {sql_hash(sql)}}}}}\n',
        "absolute dates",
    )
    # documentation may carry a date; the definition may not
    ok = spec_from_text(base + "notes: reviewed 2026-09-16\n")
    assert ok.notes == "reviewed 2026-09-16"
    # grains: placeholders name the EP that ships them; unknown grains list the registry
    _refused(base.replace("grain: icustay", "grain: note"), "placeholder until EP-148")
    _refused(base.replace("grain: icustay", "grain: edstay"), "placeholder until EP-142")
    _refused(base.replace("grain: icustay", "grain: person"), "unknown grain")
    _refused(base.replace("grain: icustay", "grain: person_time"), "no index-event template")
    _refused(base.replace("rule: first_icu_stay", "rule: each_hadm"), "does not apply to grain")
    _refused(base.replace("rule: first_icu_stay", "rule: nope"), "not an index-event rule")
    _refused(
        base.replace(
            "index_event: {rule: first_icu_stay}",
            "index_event: {rule: first_icu_stay, phenotype_onset: t2dm@1.0.0}",
        ),
        "exactly one of",
    )
    _refused(
        base.replace("index_event: {rule: first_icu_stay}", "index_event: {}"), "exactly one of"
    )
    _refused(
        base.replace("index_event: {rule: first_icu_stay}", "index_event: first_icu_stay"),
        "mapping",
    )
    # criteria
    _refused(
        base.replace("age: {min: 18}", "age: {min: 18}, los: {max_hours: 4}"),
        "exactly one kind key",
    )
    _refused(
        base.replace("{label: adult, age: {min: 18}}", "{label: adult}"), "exactly one kind key"
    )
    _refused(base.replace("age: {min: 18}", "bmi: {min: 18}"), "exactly one kind key")
    _refused(base.replace("label: adult", "label: Adult"), "slug")
    _refused(base + "  - {label: adult, los: {max_hours: 4}}\n", "duplicate criterion label")
    _refused(base.replace("age: {min: 18}", "age: {at: index}"), "give min and / or max")
    _refused(base.replace("age: {min: 18}", "age: {min: 90}"), "cap band")
    _refused(base.replace("age: {min: 18}", "age: {max: 91}"), "cap band")
    _refused(base.replace("age: {min: 18}", "age: {min: 65, max: 40}"), "max .* must exceed min")
    assert (
        spec_from_text(base.replace("age: {min: 18}", "age: {min: 89}")).inclusion[0].age
        is not None
    )
    _refused(base.replace("age: {min: 18}", "los: {of: icu}"), "give min_hours")
    _refused(base.replace("age: {min: 18}", "demographic: {}"), "at least one of")
    _refused(base.replace("age: {min: 18}", "demographic: {race: [X]}"), "Extra inputs")
    _refused(
        base.replace("age: {min: 18}", "codeset: {ref: heart_failure@1.0.0, prior_days: 30}"),
        "prior_days is given exactly when",
    )
    _refused(base.replace("age: {min: 18}", "codeset: {ref: heart_failure}"), "not a reference")
    _refused(
        base.replace("age: {min: 18}", "phenotype: {ref: t2dm@1.0.0, when: within_hours}"),
        "hours is given exactly when",
    )
    _refused(
        base.replace(
            "age: {min: 18}",
            "concept: {table: mimiciv_derived.sepsis3, column: sepsis3, op: in, value: true}",
        ),
        "needs a non-empty list",
    )
    _refused(
        base.replace(
            "age: {min: 18}", "concept: {table: sepsis3, column: sepsis3, op: '=', value: true}"
        ),
        "<schema>.<table>",
    )
    _refused(
        base.replace(
            "age: {min: 18}",
            "concept: {table: mimiciv_derived.sepsis3, column: sepsis3, op: '=', value: true, "
            "window: {start_h: 0, end_h: 24}}",
        ),
        "needs a time_column",
    )
    _refused(
        base.replace(
            "age: {min: 18}",
            "concept: {table: mimiciv_derived.sepsis3, column: sepsis3, op: is_null, value: 1}",
        ),
        "takes no value",
    )
    _refused(base.replace("age: {min: 18}", "prior_admissions: {lookback_days: 30}"), "give min")
    _refused(
        base.replace("age: {min: 18}", "data_availability: {table: labevents}"), "<schema>.<table>"
    )
    _refused(
        base.replace("age: {min: 18}", "custom_sql: {sql: 'SELECT stay_id FROM x', hash: abc}"),
        "does not match the sql",
    )
    _refused(
        base.replace(
            "age: {min: 18}",
            f"custom_sql: {{sql: 'DELETE FROM x', hash: {sql_hash('DELETE FROM x')}}}",
        ),
        "must be a SELECT",
    )
    # windows, washout, follow-up, eras, probe, references, identity
    _refused(base + "observation_window: {start_h: 0, end_h: 0}\n", "must exceed start_h")
    _refused(base + "washout: {rule: none, days: 30}\n", "takes neither days nor codeset")
    _refused(
        base + "washout: {rule: no_prior_icu, codeset: heart_failure@1.0.0}\n", "no_prior_hadm only"
    )
    _refused(
        base.replace("outcome: in_hospital_mortality", "outcome: readmission_30d"),
        "not a censoring rule",
    )
    _refused(
        base.replace(
            "outcome: in_hospital_mortality", "outcome: in_hospital_mortality, horizon_days: 30"
        ),
        "takes no horizon_days",
    )
    _refused(
        base.replace(
            "outcome: in_hospital_mortality",
            "outcome: mortality_30d, competing_events: [discharge_alive]",
        ),
        "in-hospital outcomes only",
    )
    _refused(
        base.replace(
            "outcome: in_hospital_mortality",
            "outcome: in_hospital_mortality, competing_events: [transfer]",
        ),
        "unknown",
    )
    _refused(base + "era_filter: ['2005 - 2007']\n", "not an anchor_year_group")
    _refused(base + "degeneracy_probe: {columns: [race]}\n", "not probeable")
    _refused(base + "references: {codesets: [aki@1.0.0]}\n", "differ from")
    _refused(base + "extra: 1\n", "extra")
    _refused(base.replace('version: "1.0.0"', "version: 1.0"), "semver")
    _refused(base.replace("id: crafted", "id: Crafted"), "slug")
    _refused("- not\n- a mapping\n", "top level")
    # the default horizon comes from timesem; eras normalise and sort
    thirty = spec_from_text(
        base.replace("outcome: in_hospital_mortality", "outcome: mortality_30d")
    )
    assert thirty.follow_up.horizon_days == 30 and thirty.follow_up.rule.censored
    eras = spec_from_text(base + "era_filter: ['2014-2016', '2008 - 2010', '2014 - 2016']\n")
    assert eras.era_filter == ("2008 - 2010", "2014 - 2016")
    # registry-level: unknown code-set / phenotype versions name the known ones
    codesets = codesets_registry.load_registry()
    phenotypes = phenotypes_registry.load_registry(codesets=codesets)
    unknown_cs = spec_from_text(
        base.replace("age: {min: 18}", "codeset: {ref: heart_failure@9.9.9}")
    )
    with pytest.raises(CohortSpecError, match="known versions of heart_failure"):
        registry_mod.resolve_references(unknown_cs, codesets, phenotypes)
    unknown_ph = spec_from_text(base.replace("age: {min: 18}", "phenotype: {ref: nope@1.0.0}"))
    with pytest.raises(CohortSpecError, match="no phenotype"):
        registry_mod.resolve_references(unknown_ph, codesets, phenotypes)
    onset = spec_from_text(
        base.replace(
            "index_event: {rule: first_icu_stay}", "index_event: {phenotype_onset: sepsis3@1.0.0}"
        )
    )
    assert (
        onset.phenotype_refs == ("sepsis3@1.0.0",)
        and onset.index_event.render() == "phenotype_onset(sepsis3@1.0.0)"
    )
    assert (
        "sepsis3@1.0.0" in registry_mod.resolve_references(onset, codesets, phenotypes)["phenotype"]
    )


# ---------------------------------------------------------------------------
# 4. The immutability rule: frozen pairs refuse, bumps load and lock
# ---------------------------------------------------------------------------


def _copy_specs(tmp_path: Path) -> Path:
    target = tmp_path / "specs"
    shutil.copytree(registry_mod.packaged_specs_dir(), target)
    return target


def test_frozen_version_refused_and_bump_allowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    specs = _copy_specs(tmp_path)
    path = specs / "first_icu_adults.yaml"
    original = path.read_text(encoding="utf-8")
    edited = original.replace("age: {min: 18, at: index}", "age: {min: 21, at: index}")
    assert edited != original
    path.write_text(edited, encoding="utf-8", newline="\n")
    codesets = codesets_registry.load_registry()
    phenotypes = phenotypes_registry.load_registry(codesets=codesets)
    with pytest.raises(CohortSpecFrozenError, match=re.escape(f"{TRACER} is frozen")):
        registry_mod.load_dir(specs, codesets, phenotypes)
    with pytest.raises(CohortSpecFrozenError, match="version bump"):
        registry_mod.lock_dir(specs, codesets=codesets, phenotypes=phenotypes)
    # every command refuses with exit 3 before touching anything
    monkeypatch.setattr(registry_mod, "packaged_specs_dir", lambda: specs)
    runner = helpers.cli_runner()
    root = str(tmp_path / "root")
    try:
        for args in (
            ["cohort", "list"],
            ["cohort", "show", TRACER],
            ["cohort", "validate", TRACER],
            ["--data-root", root, "cohort", "validate", TRACER, "--tier", "fixture"],
            ["cohort", "lock"],
            ["cohort", "lock", "--check"],
        ):
            result = runner.invoke(app, args)
            assert result.exit_code == 3, (args, result.output)
            assert f"{TRACER} is frozen" in result.stderr and "version bump" in result.stderr
        assert not (tmp_path / "root").exists(), "nothing was touched"
        # the DAG step fails the same way (the registry refuses before writing)
        settings = config.Settings(data_root=tmp_path / "root")
        result = runner_mod.run(
            load_dag(), "fixture", select=[registry_mod.STEP_SPECS], settings=settings
        )
        assert not result.ok and "CohortSpecFrozenError" in (result.steps[0].error or "")
        assert not registry_mod.specs_path(settings.lake_root("fixture"), "fixture").exists()
    finally:
        config.configure()
        _drop_progress_handlers()
    # a version bump is the sanctioned change: loads unlocked, then locks; both coexist
    bumped = edited.replace('version: "1.0.0"', 'version: "1.1.0"')
    (specs / "first_icu_adults_1_1_0.yaml").write_text(bumped, encoding="utf-8", newline="\n")
    path.write_text(original, encoding="utf-8", newline="\n")
    entries = {e.ref: e for e in registry_mod.load_dir(specs, codesets, phenotypes)}
    assert entries[TRACER].locked and not entries["first_icu_adults@1.1.0"].locked
    assert entries["first_icu_adults@1.1.0"].def_hash != entries[TRACER].def_hash
    check = runner.invoke(app, ["cohort", "lock", "--check", "--specs", str(specs)])
    assert check.exit_code == 1 and "first_icu_adults@1.1.0" in check.stdout, check.output
    result_lock = registry_mod.lock_dir(specs, codesets=codesets, phenotypes=phenotypes)
    assert result_lock.added == ("first_icu_adults@1.1.0",) and set(result_lock.unchanged) == {
        TRACER,
        HF,
    }
    assert registry_mod.lock_dir(specs, codesets=codesets, phenotypes=phenotypes).added == ()
    assert all(e.locked for e in registry_mod.load_dir(specs, codesets, phenotypes))
    lock_text = registry_mod.lock_path(specs).read_text(encoding="utf-8")
    assert (
        lock_text.endswith("\n") and "first_icu_adults@1.1.0" in lock_text and lock_text.isascii()
    )
    locked = runner.invoke(app, ["cohort", "lock", "--specs", str(specs)])
    assert locked.exit_code == 0 and "0 added" in locked.stdout, locked.output
    # a referenced code set that moved refuses the specs that read it (hash pinning)
    cs_dir = tmp_path / "codesets"
    shutil.copytree(codesets_registry.packaged_defs_dir(), cs_dir)
    hf_yaml = cs_dir / "heart_failure.yaml"
    hf_yaml.write_text(
        hf_yaml.read_text(encoding="utf-8").replace(
            '    - {code: "I50", match: prefix}\n',
            '    - {code: "I50", match: prefix}\n    - {code: "I51"}\n',
        ),
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(codesets_registry, "packaged_defs_dir", lambda: cs_dir)
    with pytest.raises(CodeSetFrozenError):
        registry_mod.load_registry()
    frozen_cs = runner.invoke(app, ["cohort", "list"])
    assert frozen_cs.exit_code == 3 and "refused" in frozen_cs.stderr
    monkeypatch.undo()
    monkeypatch.setattr(registry_mod, "packaged_specs_dir", lambda: specs)
    # ... and a frozen phenotype does the same through the phenotype registry
    ph_dir = tmp_path / "phenotypes"
    shutil.copytree(phenotypes_registry.packaged_defs_dir(), ph_dir)
    t2dm = ph_dir / "t2dm.yaml"
    t2dm.write_text(
        t2dm.read_text(encoding="utf-8").replace("threshold: 6.5", "threshold: 7.0"),
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(phenotypes_registry, "packaged_defs_dir", lambda: ph_dir)
    with pytest.raises(PhenotypeFrozenError):
        registry_mod.load_registry()
    frozen_ph = runner.invoke(app, ["cohort", "list"])
    assert frozen_ph.exit_code == 3 and "refused" in frozen_ph.stderr
    monkeypatch.undo()
    # a study directory referencing a study code set: the packaged registry + extras
    study_cs = tmp_path / "study-codesets"
    study_cs.mkdir()
    (study_cs / "hf_narrow.yaml").write_text(
        'id: hf_narrow\nversion: "1.0.0"\nname: narrow\nkind: icd_dx\nmembers:\n'
        '  icd9: ["4280"]\n  icd10: ["I509"]\n'
        'provenance: {source: hand, accessed: "2026-09-16"}\n',
        encoding="utf-8",
        newline="\n",
    )
    study = tmp_path / "study"
    study.mkdir()
    (study / "narrow.yaml").write_text(
        _TEXT_A.replace("id: crafted", "id: narrow").replace(
            "heart_failure@1.0.0", "hf_narrow@1.0.0"
        ),
        encoding="utf-8",
        newline="\n",
    )
    reg = registry_mod.load_registry([study], codeset_dirs=[study_cs])
    assert set(reg.refs()) == {TRACER, HF, "narrow@1.0.0"}
    narrow = reg.get("narrow@1.0.0")
    assert not narrow.locked and not narrow.packaged and narrow.display_path.endswith("narrow.yaml")
    assert "hf_narrow@1.0.0" in narrow.resolved["codeset"]
    with pytest.raises(CohortSpecError, match="no code set"):
        registry_mod.load_registry([study])  # the study code set is unknown without its dir
    with pytest.raises(UnknownCohortSpecError, match="known versions of hf_admissions"):
        reg.get("hf_admissions@9.9.9")
    with pytest.raises(UnknownCohortSpecError, match="known ids"):
        reg.get("nope@1.0.0")
    with pytest.raises(CohortSpecError, match="not a reference"):
        reg.get("hf_admissions")
    assert reg.select([HF]).refs() == (HF,)
    with pytest.raises(CohortSpecError, match="defined twice"):
        registry_mod.Registry([*reg.entries, reg.entries[0]], reg.codesets, reg.phenotypes)
    with pytest.raises(CohortSpecError, match="not a cohort-spec directory"):
        registry_mod.load_dir(tmp_path / "missing", codesets, phenotypes)
    (study / "broken.yaml").write_text("id: [1]\n", encoding="utf-8")
    with pytest.raises(CohortSpecError, match=re.escape("broken.yaml")):
        registry_mod.load_dir(study, codesets, phenotypes)
    with pytest.raises(CohortSpecError, match="not a valid cohort-spec lock"):
        (tmp_path / "badlock").mkdir()
        (tmp_path / "badlock" / registry_mod.LOCK_FILENAME).write_text("{", encoding="utf-8")
        registry_mod.load_lock(tmp_path / "badlock")


# ---------------------------------------------------------------------------
# 5. Static validation beyond the schema
# ---------------------------------------------------------------------------


def _entry(text: str, registry: Registry) -> registry_mod.Entry:
    spec = spec_from_text(text)
    resolved = registry_mod.resolve_references(spec, registry.codesets, registry.phenotypes)
    return registry_mod.Entry(spec, resolved, spec.def_hash(resolved), Path("x.yaml"), False, False)


def test_static_validation(registry: Registry) -> None:
    for entry in registry:
        result = registry_mod.validate(entry, registry)
        assert result.ok and not result.warnings, (entry.ref, result.warnings)
        assert result.to_dict()["criteria"][0]["polarity"] == "inclusion"
    base = _BASE.replace(
        "grain: icustay\nindex_event: {rule: first_icu_stay}",
        "grain: hadm\nindex_event: {rule: each_hadm}",
    )
    wrong_kind = registry_mod.validate(
        _entry(base.replace("age: {min: 18}", "codeset: {ref: labs_glucose@1.0.0}"), registry),
        registry,
    )
    assert not wrong_kind.ok and any("billed ICD set" in p for p in wrong_kind.problems)
    washout = registry_mod.validate(
        _entry(base + "washout: {rule: no_prior_hadm, codeset: labs_glucose@1.0.0}\n", registry),
        registry,
    )
    assert any("washout" in p and "diagnosis set" in p for p in washout.problems)
    onset = registry_mod.validate(
        _entry(
            base.replace(
                "index_event: {rule: each_hadm}", "index_event: {phenotype_onset: t2dm@1.0.0}"
            ),
            registry,
        ),
        registry,
    )
    assert any("phenotype_onset" in p and "grain subject" in p for p in onset.problems)
    other_grain = registry_mod.validate(
        _entry(
            base.replace("age: {min: 18}", "phenotype: {ref: sepsis3@1.0.0, when: before}"),
            registry,
        ),
        registry,
    )
    assert other_grain.ok and any("grain icustay" in w for w in other_grain.warnings)
    not_contract = registry_mod.validate(
        _entry(
            base.replace("age: {min: 18}", "data_availability: {table: mimiciv_hosp.nope}"),
            registry,
        ),
        registry,
    )
    assert any("not a contract table" in p for p in not_contract.problems)
    no_time = registry_mod.validate(
        _entry(
            base.replace("age: {min: 18}", "data_availability: {table: mimiciv_icu.d_items}"),
            registry,
        ),
        registry,
    )
    assert any("no time column" in p for p in no_time.problems)
    no_itemid = registry_mod.validate(
        _entry(
            base.replace(
                "age: {min: 18}",
                "data_availability: {table: mimiciv_hosp.admissions, itemids: [1]}",
            ),
            registry,
        ),
        registry,
    )
    assert any("no itemid column" in p for p in no_itemid.problems)
    labs = registry_mod.validate(
        _entry(
            base.replace(
                "age: {min: 18}",
                "data_availability: {table: mimiciv_hosp.labevents, itemids: [50912], min_rows: 2}",
            ),
            registry,
        ),
        registry,
    )
    assert labs.ok
    unpinned = registry_mod.validate(
        _entry(
            base.replace(
                "age: {min: 18}",
                "concept: {table: mimiciv_derived.nope, column: x, op: '=', value: 1}",
            ),
            registry,
        ),
        registry,
    )
    assert unpinned.ok and any("not a vendored concept" in w for w in unpinned.warnings)
    pinned = registry_mod.validate(
        _entry(
            base.replace(
                "age: {min: 18}",
                "concept: {table: mimiciv_derived.sepsis3, column: sepsis3, op: '=', value: true}",
            ),
            registry,
        ),
        registry,
    )
    assert pinned.ok and not pinned.warnings
    sql = "SELECT hadm_id FROM mimiciv_hosp.admissions WHERE admission_type = 'URGENT'"
    custom = registry_mod.validate(
        _entry(
            base.replace("age: {min: 18}", f'custom_sql: {{sql: "{sql}", hash: {sql_hash(sql)}}}'),
            registry,
        ),
        registry,
    )
    assert custom.ok and any("flagged custom" in w for w in custom.warnings)
    assert custom.to_dict()["custom"] and custom.to_dict()["criteria"][0]["custom"]
    empty = registry_mod.validate(
        _entry(base.replace("  - {label: adult, age: {min: 18}}\n", ""), registry), registry
    )
    assert empty.ok and any("no criteria" in w for w in empty.warnings)


# ---------------------------------------------------------------------------
# 6. The JSON schema exports and re-validates the seeds
# ---------------------------------------------------------------------------


def test_json_schema_exports_and_revalidates(registry: Registry, tmp_path: Path) -> None:
    import jsonschema

    schema = json_schema()
    assert schema["title"] == "CohortSpec" and schema["$schema"].endswith("2020-12/schema")
    defs = schema["$defs"]
    assert defs["Criterion"]["oneOf"] == [{"required": [k]} for k in CRITERION_KINDS]
    assert defs["IndexEvent"]["oneOf"] == [{"required": [k]} for k in INDEX_EVENT_KINDS]
    assert schema["properties"]["grain"]["enum"] == [g.name for g in timesem.available_grains()]
    assert schema["properties"]["era_filter"]["items"]["enum"] == list(timesem.ERAS)
    assert defs["FollowUp"]["properties"]["outcome"]["enum"] == list(timesem.CENSORING_RULES)
    assert "pattern" in schema["properties"]["version"]
    validator = jsonschema.Draft202012Validator(schema)
    for entry in registry:
        raw = yaml.safe_load(entry.path.read_text(encoding="utf-8"))
        validator.validate(raw)
        validator.validate(yaml.safe_load(entry.spec.to_yaml()))
    two_kinds = yaml.safe_load(
        _BASE.replace("age: {min: 18}", "age: {min: 18}, los: {max_hours: 4}")
    )
    assert list(validator.iter_errors(two_kinds)), "two kind keys fail the schema too"
    extra = yaml.safe_load(_BASE + "extra: 1\n")
    assert list(validator.iter_errors(extra))
    bad_grain = yaml.safe_load(_BASE.replace("grain: icustay", "grain: note"))
    assert list(validator.iter_errors(bad_grain))
    runner = helpers.cli_runner()
    printed = runner.invoke(app, ["cohort", "schema"])
    assert printed.exit_code == 0, printed.output
    assert json.loads(printed.stdout)["$defs"]["Criterion"]["oneOf"] == defs["Criterion"]["oneOf"]
    out = tmp_path / "cohort-spec.schema.json"
    written = runner.invoke(app, ["cohort", "schema", "--out", str(out)])
    assert written.exit_code == 0 and json.loads(out.read_text(encoding="utf-8")) == schema


# ---------------------------------------------------------------------------
# 7. Wiring: the DAG spec, the catalog hook, the CLI, the import budget
# ---------------------------------------------------------------------------


def test_dag_spec_catalog_hook_cli_wiring_and_import_budget() -> None:
    dag = load_dag()
    step = dag.step(registry_mod.STEP_SPECS)
    assert (
        step.kind == "python" and step.callable_name == "mimicwarehouse.cohort.registry:run_specs"
    )
    assert step.depends_on == () and step.qualified_table is None
    assert registry_mod.DAG_TAG in step.tags and "meta" in step.tags
    assert step.tiers == ("fixture", "demo", "dev", "full")
    catalog = dag.step("catalog")
    assert registry_mod.STEP_SPECS in catalog.depends_on and registry_mod.DAG_TAG in catalog.tags
    ordered = [s.name for s in dag.ordered(tags=[registry_mod.DAG_TAG], tier="fixture")]
    assert ordered == [registry_mod.STEP_SPECS, "catalog"]
    assert "cohorts.yaml" in [p.name for p in dagspec_mod.spec_paths()]
    extensions = build_mod.CATALOG_EXTENSIONS
    assert registry_mod.register_cohorts in extensions
    index = extensions.index(registry_mod.register_cohorts)
    assert index > extensions.index(build_mod._measurement_register)
    assert index < extensions.index(build_mod._phenotypes_register)
    assert extensions.index(build_mod._phenotypes_register) == len(extensions) - 2, (
        "units stays last"
    )
    from mimicwarehouse import safe

    assert "marts.cohorts" in safe.REGISTRY_TABLES, "EP-47's build registry is pre-registered"
    assert safe.is_registry_ref("meta", registry_mod.SPECS_TABLE), "the index is a meta.* read"
    runner = helpers.cli_runner()
    top = runner.invoke(app, ["cohort", "--help"])
    assert top.exit_code == 0
    for sub in ("list", "show", "validate", "schema", "lock"):
        assert sub in top.stdout, sub
    show = runner.invoke(app, ["cohort", "show", HF])
    assert show.exit_code == 0, show.output
    assert "heart_failure@1.0.0" in show.stdout and "locked yes" in show.stdout
    assert "hospice_discharge" in show.stdout and "no_prior_hadm within 365 d" in show.stdout
    shown = runner.invoke(app, ["cohort", "show", TRACER, "--json", "--yaml"])
    payload = json.loads(shown.stdout)
    assert payload["grain"] == "icustay" and payload["yaml"].startswith("id: first_icu_adults\n")
    assert payload["criteria"][1]["definition"].startswith("los(")
    validate = runner.invoke(app, ["cohort", "validate", HF, "--json"])
    assert validate.exit_code == 0 and json.loads(validate.stdout)["ok"], validate.output
    plain = runner.invoke(app, ["cohort", "validate", TRACER])
    assert plain.exit_code == 0 and "valid" in plain.stdout
    missing = runner.invoke(app, ["cohort", "show", "nope@1.0.0"])
    assert missing.exit_code == 2 and "known ids" in missing.stderr
    bad_ref = runner.invoke(app, ["cohort", "show", "hf_admissions"])
    assert bad_ref.exit_code == 2 and "not a reference" in bad_ref.stderr
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
            "mimicwarehouse.schema.contract",
        ),
    )
    helpers.assert_import_budget(lazy=("mimicwarehouse.safe", "mimicwarehouse.catalog.build"))


# ---------------------------------------------------------------------------
# 8. A minimal fixture lake: meta.cohort_specs, references on the tier, the probe
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def lake(tmp_path_factory: pytest.TempPathFactory) -> Settings:
    """A fixture lake with the probe's population, the dims, the compiled code sets, the
    cohort registry step and the catalog."""
    root = tmp_path_factory.mktemp("cohort-lake")
    settings = config.Settings(data_root=root)
    result = runner_mod.run(
        load_dag(),
        "fixture",
        select=[*LAKE_STEPS, codesets_registry.STEP_COMPILE, registry_mod.STEP_SPECS, "catalog"],
        settings=settings,
    )
    failed = [f"{s.name}: {s.error}" for s in result.steps if s.status == "failed"]
    assert not failed, failed
    return settings


def test_fixture_lake_registry_index(lake: Settings, registry: Registry) -> None:
    from mimicwarehouse.catalog.connect import open_catalog
    from mimicwarehouse.safe import safe_query

    settings = lake
    assert registry_mod.specs_path(settings.lake_root("fixture"), "fixture").is_file()
    con = open_catalog("fixture", settings=settings)
    try:
        cols = [d[0] for d in con.execute(f"DESCRIBE meta.{registry_mod.SPECS_TABLE}").fetchall()]
        assert cols == [c for c, _t in registry_mod.SPECS_COLUMNS]
        rows = {
            f"{r[0]}@{r[1]}": r
            for r in con.execute(
                "SELECT cohort_id, version, def_hash, grain, index_event, n_inclusion, "
                "n_exclusion, custom, refs, outcome, locked, path, tier FROM meta.cohort_specs"
            ).fetchall()
        }
        assert set(rows) == {TRACER, HF}
        for entry in registry:
            row = rows[entry.ref]
            assert row[2] == entry.def_hash and row[3] == entry.spec.grain
            assert row[4] == entry.spec.index_event.render()
            assert (row[5], row[6]) == (len(entry.spec.inclusion), len(entry.spec.exclusion))
            assert row[7] is False and json.loads(row[8]) == entry.flat_refs
            assert row[9] == entry.spec.follow_up.outcome and row[10] is True
            assert row[11] == entry.display_path and row[12] == "fixture"
        comment = _scalar(
            con,
            "SELECT comment FROM duckdb_tables() WHERE schema_name = 'meta' "
            f"AND table_name = '{registry_mod.SPECS_TABLE}'",
        )
        assert "EP-46" in comment and "marts.cohorts" in comment
    finally:
        con.close()
    # a registry read through safe_query needs no count column (meta.* is exempt)
    result = safe_query(
        "SELECT cohort_id, version, grain, locked FROM meta.cohort_specs ORDER BY 1",
        tier="fixture",
        settings=settings,
        actor="test_ep46",
    )
    assert [r[0] for r in result.df.rows()] == ["first_icu_adults", "hf_admissions"]
    runner = helpers.cli_runner()
    root = str(settings.data_root)
    try:
        cli = runner.invoke(
            app,
            [
                "--data-root",
                root,
                "sql",
                "SELECT cohort_id, version, def_hash FROM meta.cohort_specs",
                "--tier",
                "fixture",
                "--format",
                "json",
            ],
        )
        assert cli.exit_code == 0, cli.output
    finally:
        config.configure()


def test_fixture_lake_references_and_degeneracy_probe(
    lake: Settings, registry: Registry, fixture_catalog: duckdb.DuckDBPyConnection
) -> None:
    settings = lake
    n_subjects_with_icu = _scalar(
        fixture_catalog, f"SELECT count(DISTINCT subject_id) FROM {ICU}.icustays"
    )
    n_admissions = _scalar(fixture_catalog, f"SELECT count(*) FROM {HOSP}.admissions")
    # the tracer spec: no references, the probe over the first ICU stays (k = 1 on fixture)
    tracer = probe_mod.validate_on_tier(
        registry.get(TRACER), tier="fixture", settings=settings, k=1, actor="test_ep46"
    )
    assert tracer.ok and tracer.references == () and tracer.skipped is None and tracer.k == 1
    assert [p.column for p in tracer.probes] == list(DEFAULT_PROBE_COLUMNS)
    for probe in tracer.probes:
        assert probe.rows_suppressed == 0 and probe.rows
        assert sum(r.n for r in probe.rows) == n_subjects_with_icu, probe.column
        assert all(0 <= r.n_events <= r.n for r in probe.rows)
        for row in probe.rows:
            expected = (
                probe_mod.ZERO_EVENT
                if row.n_events == 0
                else (probe_mod.ALL_EVENT if row.n_events == row.n else probe_mod.OK)
            )
            assert row.verdict == expected
        assert "WITH pop AS" in probe.sql and "subject_id" not in probe.sql.split("FROM pop")[1]
    eras = {r.level for r in tracer.probes[3].rows}
    assert eras <= set(timesem.ERAS) and tracer.snapshot_id and len(tracer.audit_ids) == 5
    assert all(
        w.endswith("(EP-31 policy; EP-79 model side)") for w in tracer.warnings if " is " in w
    )
    # hf_admissions: the code set resolves against meta.codesets; the population is every admission
    hf = probe_mod.validate_on_tier(
        registry.get(HF), tier="fixture", settings=settings, k=1, actor="test_ep46"
    )
    assert hf.ok and len(hf.references) == 1
    check = hf.references[0]
    assert check.kind == "codeset" and check.ref == "heart_failure@1.0.0" and check.ok
    assert (
        check.found
        and check.tier_hash
        == check.expected_hash
        == registry.get(HF).resolved["codeset"]["heart_failure@1.0.0"]
    )
    for probe in hf.probes:
        assert sum(r.n for r in probe.rows) == n_admissions, probe.column
    first_careunit = next(p for p in hf.probes if p.column == "first_careunit")
    assert any(r.level == probe_mod.NULL_LEVEL for r in first_careunit.rows), (
        "admissions without ICU"
    )
    payload = hf.to_dict()
    assert payload["ok"] and payload["references"][0]["ok"] and len(payload["probes"]) == 4
    # the default k withholds the small levels (complementary, row-wise) and says so
    default_k = probe_mod.validate_on_tier(
        registry.get(TRACER), tier="fixture", settings=settings, actor="test_ep46"
    )
    assert default_k.k == settings.k_suppression == 11
    withheld = sum(p.rows_suppressed for p in default_k.probes)
    assert withheld > 0 and any("withheld" in w for w in default_k.warnings)
    assert all(r.n >= 11 for p in default_k.probes for r in p.rows)
    assert all(r.n_events == 0 or r.n_events >= 11 for p in default_k.probes for r in p.rows)
    # a stale / missing reference is a problem with its remedy; a phenotype onset skips the probe
    study = Path(str(settings.data_root)) / "study"
    study.mkdir()
    (study / "t2dm_cohort.yaml").write_text(
        _BASE.replace("id: crafted", "id: t2dm_cohort")
        .replace(
            "grain: icustay\nindex_event: {rule: first_icu_stay}",
            "grain: subject\nindex_event: {rule: first_hadm}",
        )
        .replace("age: {min: 18}", "phenotype: {ref: t2dm@1.0.0, when: before}"),
        encoding="utf-8",
        newline="\n",
    )
    (study / "onset.yaml").write_text(
        _BASE.replace("id: crafted", "id: onset").replace(
            "index_event: {rule: first_icu_stay}", "index_event: {phenotype_onset: sepsis3@1.0.0}"
        ),
        encoding="utf-8",
        newline="\n",
    )
    reg = registry_mod.load_registry([study])
    stale = probe_mod.validate_on_tier(
        reg.get("t2dm_cohort@1.0.0"), tier="fixture", settings=settings, k=1
    )
    assert (
        not stale.ok and stale.references[0].kind == "phenotype" and not stale.references[0].found
    )
    assert "mwh phenotype compile t2dm@1.0.0 --tier fixture" in stale.problems[0]
    skipped = probe_mod.validate_on_tier(
        reg.get("onset@1.0.0"), tier="fixture", settings=settings, k=1
    )
    assert (
        skipped.skipped and skipped.probes == () and any("skipped" in w for w in skipped.warnings)
    )
    assert probe_mod.population_sql(reg.get("onset@1.0.0").spec) is None
    assert probe_mod.probe_sql(reg.get("onset@1.0.0").spec, "gender") is None
    with pytest.raises(ValueError, match="not a probe column"):
        probe_mod.probe_sql(registry.get(TRACER).spec, "race")
    assert "hospital_expire_flag = 1" in probe_mod.event_sql(registry.get(TRACER).spec.follow_up)
    assert "INTERVAL 30 DAY" in probe_mod.event_sql(registry.get(HF).spec.follow_up)
    # the CLI: validate --tier prints the tables, --json carries them, a missing reference exits 1
    runner = helpers.cli_runner()
    root = str(settings.data_root)
    try:
        cli = runner.invoke(
            app, ["--data-root", root, "cohort", "validate", HF, "--tier", "fixture", "--k", "1"]
        )
        assert cli.exit_code == 0, cli.output
        assert "degeneracy probe first_careunit" in cli.stdout and "ok (tier" in cli.stdout
        assert "valid" in cli.stdout and probe_mod.NULL_LEVEL in cli.stdout
        as_json = runner.invoke(
            app,
            [
                "--data-root",
                root,
                "cohort",
                "validate",
                TRACER,
                "--tier",
                "fixture",
                "--k",
                "1",
                "--json",
            ],
        )
        assert as_json.exit_code == 0, as_json.output
        payload = json.loads(as_json.stdout)
        assert payload["ok"] and payload["tier"]["k"] == 1 and len(payload["tier"]["probes"]) == 4
        failed = runner.invoke(
            app,
            [
                "--data-root",
                root,
                "cohort",
                "validate",
                "t2dm_cohort@1.0.0",
                "--tier",
                "fixture",
                "--specs",
                str(study),
            ],
        )
        assert (
            failed.exit_code == 1 and "PROBLEM" in failed.stdout and "1 problem(s)" in failed.stdout
        )
        bad_tier = runner.invoke(
            app, ["--data-root", root, "cohort", "validate", HF, "--tier", "nope"]
        )
        assert bad_tier.exit_code == 2
    finally:
        config.configure()
        _drop_progress_handlers()


def test_session_fixture_lake_carries_the_registry(
    fixture_lake_catalog: duckdb.DuckDBPyConnection, registry: Registry
) -> None:
    """The full DAG (conftest's session lake) runs cohorts.specs too."""
    rows = fixture_lake_catalog.execute(
        "SELECT cohort_id, version, def_hash FROM meta.cohort_specs ORDER BY 1"
    ).fetchall()
    assert [(r[0], r[1]) for r in rows] == [
        ("first_icu_adults", "1.0.0"),
        ("hf_admissions", "1.0.0"),
    ]
    assert {r[2] for r in rows} == {e.def_hash for e in registry}


# ---------------------------------------------------------------------------
# 9. Docs in sync
# ---------------------------------------------------------------------------


def test_methods_doc_in_sync(tmp_path: Path, registry: Registry) -> None:
    path = registry_mod.methods_doc_path()
    assert path.is_file(), "docs/methods/cohorts.md exists"
    text = path.read_text(encoding="utf-8")
    for needle in (
        "retrospective",
        "def_hash",
        "cohorts.lock.json",
        "CohortSpecFrozenError",
        "meta.cohort_specs",
        "marts.cohorts",
        "[start, end)",
        "within-patient relative time",
        "| `custom_sql` |",
        "| `prior_admissions` |",
        "zero-event",
        "never silently dropped",
        "EP-47",
        "EP-79",
        "EP-62",
        "id: first_icu_adults",
        "safe_query",
    ):
        assert needle in text, needle
    copy = tmp_path / "cohorts.md"
    copy.write_text(text, encoding="utf-8", newline="\n")
    registry_mod.sync_methods_doc(copy, registry)
    assert copy.read_text(encoding="utf-8") == text, "re-run `python -m mimicwarehouse.cohort`"
    cards = registry_mod.render_cards(registry)
    assert cards.rstrip("\n") in text and f"### `{TRACER}`" in cards and f"### `{HF}`" in cards
    reference = registry_mod.render_schema_reference()
    assert reference.rstrip("\n") in text and "**`Criterion`**" in reference
    assert not BAND_TOKEN.search(text)


# ---------------------------------------------------------------------------
# 10. Dev tier: references and the probe on the real dev catalog (aggregates only)
# ---------------------------------------------------------------------------


@pytest.mark.tier("dev")
def test_dev_references_probe_and_registry_index(dev_catalog: Path, registry: Registry) -> None:
    from mimicwarehouse.safe import safe_query

    settings = config.load_settings()
    remedy = (
        "run `mwh build --tier dev --tag cohorts` (EP-46) and `mwh codeset compile --tier dev` "
        "(EP-40) first"
    )
    index = safe_query(
        "SELECT cohort_id, version, def_hash FROM meta.cohort_specs",
        tier="dev",
        settings=settings,
        actor="test_ep46",
    ).df
    refs = {f"{i}@{v}": h for i, v, h in index.rows()}
    assert set(refs) >= {TRACER, HF}, remedy
    for entry in registry:
        assert refs[entry.ref] == entry.def_hash, entry.ref
    for ref in (TRACER, HF):
        result = probe_mod.validate_on_tier(
            registry.get(ref), tier="dev", settings=settings, actor="test_ep46"
        )
        assert result.ok, (ref, result.problems, remedy)
        assert result.k >= 11 and len(result.probes) == 4
        for probe in result.probes:
            assert all(r.n >= 11 for r in probe.rows), probe.column
            assert all(r.n_events == 0 or r.n_events >= 11 for r in probe.rows), probe.column
        degenerate = [r for p in result.probes for r in p.degenerate]
        withheld = sum(p.rows_suppressed for p in result.probes)
        print(
            f"dev: {ref} references {len(result.references)} ok, "
            f"{sum(len(p.rows) for p in result.probes)} level(s) released, {withheld} withheld, "
            f"{len(degenerate)} degenerate at k={result.k}"
        )
