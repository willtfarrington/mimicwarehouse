"""EP-51 — Protocol schema + freeze registry + ``mwh protocol``.

Fixture tier (default): the packaged seed protocol loads, resolves its cohort to the
EP-46 ``def_hash`` and hashes deterministically; the content hash is invariant to key
order, whitespace, quoting, omitted defaults and documentation and moves with every
definition change and with a referenced hash; the schema refuses the crafted violations
(an absolute date, an unknown or overlapping era, an unavailable grain, an unknown
censoring rule, an identifier column, the fixed texts, the claim / plan rules, ...) with
a clear message and resolution refuses an unknown cohort or a grain mismatch; ``freeze``
appends one ledger line, writes a byte-identical read-only copy and an audit line, is
idempotent, and refuses an ``id@version`` frozen with another hash (exit 3); ``verify``
fails after editing the YAML or the frozen copy; ``run`` refuses an unknown hash, a
modified frozen copy and an unfrozen ``--yaml`` (exit 3, audited); ``amend`` links hashes
and refuses the crafted misuse; the D-25 policy hook in ``run.start`` refuses a
confirmatory / causal run without a hash; the ``cohort_only`` runner on a fixture lake
builds the cohort through EP-47, records the suppressed attrition and writes
``protocol_summary.md`` with the claim type and the retrospective sentence, then reuses
the build; the ``runs.protocols`` view and its parity with the ledger line; the CLI; the
docs page is in sync; the import budget. ``tier("dev")``: freeze + run
``tracer_mortality@1.0.0`` with ``--runner cohort_only`` on the real dev catalog.

Everything asserted or printed is definition text, hashes, ids, step names and
k-suppressed aggregate counts — never a patient-level row (the fixture is synthetic,
ids >= 90 000 000).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import TYPE_CHECKING, Any

import duckdb
import pytest
import yaml

import helpers
from mimicwarehouse import config
from mimicwarehouse import run as run_mod
from mimicwarehouse.cli import app
from mimicwarehouse.codesets import registry as codesets_registry
from mimicwarehouse.cohort import build as cohort_build
from mimicwarehouse.cohort import registry as cohort_registry_mod
from mimicwarehouse.dag import runner as runner_mod
from mimicwarehouse.dag.spec import load_dag
from mimicwarehouse.protocol import registry as registry_mod
from mimicwarehouse.protocol import runners as runners_mod
from mimicwarehouse.protocol import spec as spec_mod
from mimicwarehouse.protocol.registry import (
    PROTOCOLS_COLUMNS,
    ProtocolFrozenError,
    ProtocolReferenceError,
    ProtocolRefusedError,
    RegistryLine,
    UnknownProtocolError,
)
from mimicwarehouse.protocol.spec import (
    CLAIM_TYPES,
    HASH_REQUIRED_CLAIM_TYPES,
    RETROSPECTIVE_STATEMENT,
    SEEDS_POLICY,
    Protocol,
    ProtocolError,
    protocol_from_text,
)
from mimicwarehouse.safe import RUNS_DB_VIEWS, audit_path, build_runs_db, runs_db_path, safe_query

if TYPE_CHECKING:
    from mimicwarehouse.config import Settings

pytestmark = pytest.mark.ep_51

HOSP, ICU = "mimiciv_hosp", "mimiciv_icu"
SEED = "tracer_mortality@1.0.0"
TRACER = "first_icu_adults@1.0.0"
DOCS = helpers.WORKSPACE / "docs"
BAND_TOKEN = re.compile(r"(?<![\w.])[123]\d{7}(?![\w.])")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
#: The stage steps a cohort build needs on the fixture (EP-47's list).
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


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def data_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    yield helpers.tmp_data_root(monkeypatch, tmp_path)
    config.configure()


@pytest.fixture(scope="module")
def lake(tmp_path_factory: pytest.TempPathFactory) -> Settings:
    """A fixture lake with the chains' core tables, the dims, the compiled code sets, the
    registry index and the catalog — but **no** built cohort, so the runner builds it."""
    root = tmp_path_factory.mktemp("protocol-lake")
    settings = config.Settings(data_root=root)
    result = runner_mod.run(
        load_dag(),
        "fixture",
        select=[
            *LAKE_STEPS,
            codesets_registry.STEP_COMPILE,
            cohort_registry_mod.STEP_SPECS,
            "catalog",
        ],
        settings=settings,
    )
    failed = [f"{s.name}: {s.error}" for s in result.steps if s.status == "failed"]
    assert not failed, failed
    return settings


def _settings() -> Settings:
    return config.get_settings()


def _seed_doc() -> dict[str, Any]:
    return yaml.safe_load(registry_mod.seed_path().read_text(encoding="utf-8"))


def _seed_resolution() -> dict[str, dict[str, str]]:
    entry = cohort_registry_mod.load_registry().get(TRACER)
    return {"cohort": {TRACER: entry.def_hash}}


def _hash_of(doc: dict[str, Any]) -> str:
    return Protocol.model_validate(doc).content_hash(_seed_resolution())


def _write(path: Path, doc: dict[str, Any]) -> Path:
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8", newline="\n")
    return path


def _audit_lines(settings: Settings) -> list[dict[str, Any]]:
    path = audit_path(settings)
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _make_writable(path: Path) -> None:
    os.chmod(path, stat.S_IWRITE | stat.S_IREAD)


def _view_count(settings: Settings, view: str, where: str = "") -> int:
    con = duckdb.connect(str(runs_db_path(settings)), read_only=True)
    try:
        row = con.execute(f"SELECT count(*) FROM {view} {where}").fetchone()
        assert row is not None
        return int(row[0])
    finally:
        con.close()


# ---------------------------------------------------------------------------
# 1. The seed and the hash
# ---------------------------------------------------------------------------


def test_seed_loads_resolves_and_hashes() -> None:
    protocol, resolved, digest = registry_mod.hash_file(registry_mod.seed_path())
    assert protocol.ref == SEED == registry_mod.SEED_REF
    assert protocol.claim_type == "exploratory" and not protocol.hash_required
    assert protocol.cohort == TRACER and protocol.unit_of_analysis == "icustay"
    assert protocol.exposure is None and [o.name for o in protocol.outcomes] == [
        "in_hospital_mortality"
    ]
    assert protocol.outcomes[0].rule.competing_events == ("discharge_alive",)
    assert [c.name for c in protocol.covariates] == [
        "age",
        "gender",
        "admission_type",
        "first_careunit",
    ]
    assert protocol.temporal_holdout is not None
    assert protocol.temporal_holdout.development_eras == (
        "2008 - 2010",
        "2011 - 2013",
        "2014 - 2016",
    )
    assert protocol.temporal_holdout.holdout_eras == ("2017 - 2019",)
    assert protocol.temporal_holdout.sealed_eras == ("2020 - 2022",)
    assert protocol.seeds_policy == SEEDS_POLICY
    assert protocol.retrospective_statement == RETROSPECTIVE_STATEMENT
    assert "retrospective" in RETROSPECTIVE_STATEMENT and "derive_seed" in SEEDS_POLICY
    assert resolved.cohort_grain == "icustay"
    assert resolved.cohort_hash == cohort_registry_mod.load_registry().get(TRACER).def_hash
    assert resolved.flat == {f"cohort:{TRACER}": resolved.cohort_hash}
    assert HEX64.match(digest) and digest == _hash_of(_seed_doc())
    again = registry_mod.hash_file(registry_mod.seed_path())[2]
    assert again == digest
    assert set(HASH_REQUIRED_CLAIM_TYPES) < set(CLAIM_TYPES)
    text = registry_mod.seed_path().read_text(encoding="utf-8")
    assert text.isascii() and not BAND_TOKEN.search(text)


def _reversed(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _reversed(v) for k, v in reversed(list(obj.items()))}
    if isinstance(obj, list):
        return [_reversed(v) for v in obj]
    return obj


def test_hash_is_invariant_to_form_and_documentation() -> None:
    base = _seed_doc()
    digest = _hash_of(base)
    # key order, whitespace / quoting (a re-dump), omitted defaults, documentation
    assert _hash_of(_reversed(base)) == digest
    redumped = yaml.safe_load(yaml.safe_dump(base, sort_keys=True, default_style='"'))
    assert _hash_of(redumped) == digest
    sparse = json.loads(json.dumps(base))
    for key in ("seeds_policy", "retrospective_statement", "references", "amends"):
        sparse.pop(key)
    sparse["feature_windows"].pop("gap_h")
    sparse["feature_windows"].pop("prediction_h")
    sparse["analysis_plan"].pop("hyperparameter_policy")
    sparse["analysis_plan"].pop("subgroups")
    sparse["covariates"][1].pop("transform")
    sparse["covariates"][1]["transform"] = "categorical"
    assert _hash_of(sparse) == digest
    documented = json.loads(json.dumps(base))
    documented["title"] = "another title"
    documented["description"] = "another description"
    documented["notes"] = "and other notes"
    assert _hash_of(documented) == digest
    # the seed protocol's YAML round-trips through protocol_from_text
    assert protocol_from_text(yaml.safe_dump(base)).content_hash(_seed_resolution()) == digest


@pytest.mark.parametrize(
    "path, value",
    [
        (("version",), "1.0.1"),
        (("claim_type",), "confirmatory"),
        (("feature_windows", "observation", "end_h"), 6),
        (("feature_windows", "gap_h"), 2),
        (("analysis_plan", "estimand"), "something else"),
        (("analysis_plan", "subgroups"), ["by gender"]),
        (("temporal_holdout", "development_eras"), ["2008 - 2010", "2011 - 2013"]),
        (("temporal_holdout", "sealed_eras"), []),
        (("outcomes", 0, "competing_events"), []),
        (("covariates", 0, "transform"), "identity"),
        (("temporal_holdout",), None),
    ],
)
def test_hash_moves_with_every_definition_change(path: tuple[Any, ...], value: Any) -> None:
    base = _seed_doc()
    changed = json.loads(json.dumps(base))
    node: Any = changed
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    assert _hash_of(changed) != _hash_of(base)


def test_hash_moves_with_a_referenced_hash_and_needs_every_reference() -> None:
    protocol = Protocol.model_validate(_seed_doc())
    real = protocol.content_hash(_seed_resolution())
    assert protocol.content_hash({"cohort": {TRACER: "0" * 64}}) != real
    with pytest.raises(ProtocolError, match="unresolved reference"):
        protocol.content_hash({"cohort": {}})
    canonical = protocol.canonical(_seed_resolution())
    assert "title" not in canonical and "notes" not in canonical
    assert canonical["cohort"] == _seed_resolution()["cohort"]
    assert canonical["amends"] is None and canonical["seeds_policy"] == SEEDS_POLICY


# ---------------------------------------------------------------------------
# 2. Schema refusals and resolution refusals
# ---------------------------------------------------------------------------


def _mutated(**changes: Any) -> dict[str, Any]:
    doc = json.loads(json.dumps(_seed_doc()))
    for dotted, value in changes.items():
        node: Any = doc
        keys = dotted.split("__")
        for key in keys[:-1]:
            node = node[int(key)] if key.isdigit() else node[key]
        last = keys[-1]
        if last.isdigit():
            node[int(last)] = value
        else:
            node[last] = value
    return doc


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"analysis_plan__estimand": "rates from 2020-01-01 on"}, "absolute dates"),
        (
            {"covariates__0__window": {"start_h": -24, "end_h": 0, "since": "DATE '2020-01-01'"}},
            "absolute dates|extra",
        ),
        ({"temporal_holdout__holdout_eras": ["2023 - 2025"]}, "not an anchor_year_group"),
        ({"temporal_holdout__sealed_eras": ["2017 - 2019"]}, "share"),
        ({"temporal_holdout__development_eras": []}, "non-empty"),
        ({"unit_of_analysis": "edstay"}, "not available"),
        ({"unit_of_analysis": "week"}, "unknown grain"),
        ({"outcomes__0__censoring": "mortality_2y"}, "not a censoring rule"),
        ({"outcomes__0__horizon_days": 30}, "takes no horizon_days"),
        ({"outcomes__0__window_h": 24, "outcomes__0__horizon_days": 30}, "alternatives|takes no"),
        ({"outcomes__0__definition": {"column": "cohort.subject_id"}}, "identifier column"),
        ({"covariates__0__source": "mimiciv_icu.icustays.stay_id"}, "identifier column"),
        ({"covariates__0__source": "gender"}, "cohort.<column>"),
        (
            {"outcomes__0__definition": {"column": "cohort.censor_reason", "codeset": "x@1.0.0"}},
            "exactly one",
        ),
        ({"outcomes__0__definition": {"codeset": "x@1.0.0", "equals": 1}}, "column or concept"),
        ({"outcomes": []}, "at least one outcome"),
        ({"seeds_policy": "whatever we like"}, "fixed text"),
        ({"retrospective_statement": "prospective"}, "fixed text"),
        ({"amends": "abc"}, "64 hex"),
        ({"amendment_reason": "because"}, "without amends"),
        ({"claim_type": "causal"}, "needs an exposure"),
        ({"claim_type": "associational"}, "needs an exposure"),
        ({"claim_type": "predictive"}, "prediction or bayes"),
        ({"analysis_plan__method_family": "causal"}, "needs an exposure"),
        (
            {"references": {"cohorts": [], "codesets": [], "phenotypes": [], "concepts": []}},
            "differ from",
        ),
        ({"version": "1.0"}, "semver"),
        ({"id": "Tracer"}, "slug"),
        ({"covariates__1__name": "age"}, "duplicate covariate"),
        ({"covariates__0__transform": "square"}, "transform"),
    ],
)
def test_schema_refuses_crafted_violations(changes: dict[str, Any], message: str) -> None:
    with pytest.raises(ProtocolError, match=message):
        protocol_from_text(yaml.safe_dump(_mutated(**changes)), where="crafted")


def test_causal_protocol_with_an_exposure_validates_and_needs_a_hash() -> None:
    doc = _mutated(
        claim_type="causal",
        exposure={
            "name": "early_abx",
            "definition": {"concept": "antibiotic"},
            "timing": {"relative_to": "index", "window": {"start_h": 0, "end_h": 6}},
        },
        analysis_plan__method_family="causal",
        references=None,
    )
    protocol = protocol_from_text(yaml.safe_dump(doc))
    assert protocol.hash_required and protocol.concept_tables == ("mimiciv_derived.antibiotic",)
    assert protocol.exposure is not None and protocol.exposure.definition.kind == "concept"
    with pytest.raises(ProtocolError, match="unresolved reference"):
        protocol.content_hash(_seed_resolution())
    resolved = registry_mod.resolve(protocol)
    assert set(resolved.hashes["concept"]) == {"mimiciv_derived.antibiotic"}
    assert HEX64.match(resolved.hashes["concept"]["mimiciv_derived.antibiotic"])
    assert protocol.content_hash(resolved.hashes) != _hash_of(_seed_doc())


def test_resolution_refuses_unknown_cohort_and_grain_mismatch() -> None:
    unknown = protocol_from_text(yaml.safe_dump(_mutated(cohort="nobody@1.0.0", references=None)))
    with pytest.raises(ProtocolReferenceError, match="cohort"):
        registry_mod.resolve(unknown)
    mismatch = protocol_from_text(yaml.safe_dump(_mutated(unit_of_analysis="hadm")))
    with pytest.raises(ProtocolReferenceError, match="not the grain"):
        registry_mod.resolve(mismatch)
    concept = protocol_from_text(
        yaml.safe_dump(
            _mutated(
                outcomes__0__definition={"concept": "mimiciv_derived.not_a_concept"},
                references=None,
            )
        )
    )
    with pytest.raises(ProtocolReferenceError, match="not a vendored concept"):
        registry_mod.resolve(concept)


def test_json_schema_exports_and_revalidates_the_seed() -> None:
    schema = spec_mod.json_schema()
    assert schema["title"] == "Protocol" and "Definition" in schema["$defs"]
    assert schema["$defs"]["Definition"]["oneOf"] == [
        {"required": [k]} for k in spec_mod.DEFINITION_KINDS
    ]
    assert schema["properties"]["claim_type"]["enum"] == list(CLAIM_TYPES)
    assert schema["properties"]["unit_of_analysis"]["enum"] == list(spec_mod.AVAILABLE_GRAINS)
    assert json.dumps(schema)  # serialisable


# ---------------------------------------------------------------------------
# 3. Freeze: the ledger line, the immutable copy, the audit line, idempotence
# ---------------------------------------------------------------------------


def test_freeze_appends_a_line_and_an_immutable_copy(data_root: Path) -> None:
    settings = _settings()
    seed = registry_mod.seed_path()
    result = registry_mod.freeze(seed, settings)
    assert result.created and HEX64.match(result.hash)
    lines = registry_mod.read_registry(settings)
    assert [line.hash for line in lines] == [result.hash]
    line = lines[0]
    assert line.protocol_id == "tracer_mortality" and line.version == "1.0.0"
    assert line.claim_type == "exploratory" and line.cohort == TRACER
    assert line.cohort_hash == result.resolved.cohort_hash and line.unit_of_analysis == "icustay"
    assert line.git_sha and line.amends is None and line.amendment_reason is None
    assert line.path == seed.resolve().as_posix()
    assert line.frozen_path == f"runs/protocols/{result.hash}.yaml"
    assert line.source_sha256 == hashlib.sha256(seed.read_bytes()).hexdigest()
    assert line.ref_hashes == {f"cohort:{TRACER}": line.cohort_hash}
    assert line.actor == settings.role == "agent"
    copy = registry_mod.frozen_path(result.hash, settings)
    assert copy == result.frozen_path and copy.is_file()
    assert copy.read_bytes() == seed.read_bytes(), "byte for byte"
    assert registry_mod.is_read_only(copy)
    with pytest.raises(PermissionError):
        copy.write_text("edited", encoding="utf-8")
    audits = [a for a in _audit_lines(settings) if a["sql_text"].startswith("protocol ")]
    assert audits and audits[-1]["sql_text"] == f"protocol freeze {result.hash}"
    assert audits[-1]["allowed"] is True and audits[-1]["actor"] == "agent"
    # idempotent: the same content freezes to the same hash, no second line
    again = registry_mod.freeze(seed, settings)
    assert not again.created and again.hash == result.hash
    assert len(registry_mod.read_registry(settings)) == 1
    assert registry_mod.get(result.hash, settings).hash == result.hash
    with pytest.raises(UnknownProtocolError):
        registry_mod.get("f" * 64, settings)
    with pytest.raises(ProtocolError, match="not a protocol hash"):
        registry_mod.get("nope", settings)
    # the runs.protocols view (typed, parity with the line model)
    assert list(PROTOCOLS_COLUMNS) == list(RegistryLine.model_fields)
    assert RUNS_DB_VIEWS[-1] == "protocols"
    assert [name for name, _ in run_mod.runs_db_views(settings)][-1] == "protocols"
    build_runs_db(settings)
    assert _view_count(settings, "protocols") == 1
    assert _view_count(settings, "protocols", f"WHERE hash = '{result.hash}'") == 1
    con = duckdb.connect(str(runs_db_path(settings)), read_only=True)
    try:
        columns = [r[0] for r in con.execute("DESCRIBE protocols").fetchall()]
        assert columns == list(PROTOCOLS_COLUMNS)
        row = con.execute(
            "SELECT json_extract_string(ref_hashes, '$.\"cohort:first_icu_adults@1.0.0\"') "
            "FROM protocols"
        ).fetchone()
        assert row is not None and row[0] == line.cohort_hash
    finally:
        con.close()


def test_freeze_refuses_a_frozen_version_with_another_hash(data_root: Path, tmp_path: Path) -> None:
    settings = _settings()
    first = registry_mod.freeze(registry_mod.seed_path(), settings)
    edited = _write(tmp_path / "tracer_mortality.yaml", _mutated(feature_windows__gap_h=1))
    with pytest.raises(ProtocolFrozenError, match="already frozen"):
        registry_mod.freeze(edited, settings)
    assert len(registry_mod.read_registry(settings)) == 1
    refused = [a for a in _audit_lines(settings) if a["allowed"] is False]
    assert refused and "already frozen" in refused[-1]["refusal_reason"]
    # a version bump loads, freezes and coexists
    bumped = _write(tmp_path / "bumped.yaml", _mutated(version="1.1.0", feature_windows__gap_h=1))
    second = registry_mod.freeze(bumped, settings)
    assert second.created and second.hash != first.hash
    assert [line.ref for line in registry_mod.read_registry(settings)] == [
        SEED,
        "tracer_mortality@1.1.0",
    ]
    assert registry_mod.lines_for(SEED, settings)[0].hash == first.hash


# ---------------------------------------------------------------------------
# 4. verify / amend
# ---------------------------------------------------------------------------


def test_verify_detects_drift_unfrozen_unknown_and_tampering(
    data_root: Path, tmp_path: Path
) -> None:
    settings = _settings()
    seed = registry_mod.seed_path()
    frozen = registry_mod.freeze(seed, settings)
    assert registry_mod.verify(seed, settings).ok
    assert registry_mod.verify(frozen.hash, settings).ok
    assert registry_mod.verify(frozen.hash.upper(), settings).ok
    unknown = registry_mod.verify("e" * 64, settings)
    assert unknown.status == "unknown" and not unknown.ok
    edited = _write(tmp_path / "edited.yaml", _mutated(feature_windows__observation__end_h=6))
    drift = registry_mod.verify(edited, settings)
    assert drift.status == "drift" and "changed since" in drift.message
    fresh = _write(tmp_path / "fresh.yaml", _mutated(id="other_question"))
    assert registry_mod.verify(fresh, settings).status == "unfrozen"
    with pytest.raises(ProtocolError):
        registry_mod.verify(tmp_path / "missing.yaml", settings)
    # tamper with the frozen copy: verify reports drift and run refuses
    copy = registry_mod.frozen_path(frozen.hash, settings)
    _make_writable(copy)
    copy.write_text(
        copy.read_text(encoding="utf-8").replace("end_h: 0", "end_h: 6"), encoding="utf-8"
    )
    tampered = registry_mod.verify(frozen.hash, settings)
    assert tampered.status == "drift" and "edited" in tampered.message
    with pytest.raises(ProtocolRefusedError, match="refusing to run"):
        registry_mod.check_frozen(frozen.hash, settings)
    copy.unlink()
    assert registry_mod.verify(frozen.hash, settings).status == "missing_copy"
    with pytest.raises(ProtocolRefusedError, match="missing"):
        registry_mod.check_frozen(frozen.hash, settings)


def test_amend_links_hashes_and_refuses_misuse(data_root: Path, tmp_path: Path) -> None:
    settings = _settings()
    first = registry_mod.freeze(registry_mod.seed_path(), settings)
    amended_doc = _mutated(
        version="1.1.0",
        amends=first.hash,
        amendment_reason="add a gender subgroup",
        analysis_plan__subgroups=["by gender"],
    )
    amended = _write(tmp_path / "amended.yaml", amended_doc)
    with pytest.raises(ProtocolError, match="use `mwh protocol amend"):
        registry_mod.freeze(amended, settings)
    with pytest.raises(ProtocolError, match="must declare"):
        registry_mod.amend(amended, previous="a" * 64, settings=settings)
    with pytest.raises(UnknownProtocolError):
        registry_mod.amend(
            _write(tmp_path / "orphan.yaml", {**amended_doc, "amends": "a" * 64}),
            previous="a" * 64,
            settings=settings,
        )
    with pytest.raises(ProtocolError, match="bumps the version"):
        registry_mod.amend(
            _write(tmp_path / "same.yaml", {**amended_doc, "version": "1.0.0"}),
            previous=first.hash,
            settings=settings,
        )
    with pytest.raises(ProtocolError, match="keeps the protocol id"):
        registry_mod.amend(
            _write(tmp_path / "other.yaml", {**amended_doc, "id": "other_question"}),
            previous=first.hash,
            settings=settings,
        )
    with pytest.raises(ProtocolError, match="needs a reason"):
        registry_mod.amend(
            _write(tmp_path / "noreason.yaml", {**amended_doc, "amendment_reason": ""}),
            previous=first.hash,
            settings=settings,
        )
    result = registry_mod.amend(amended, previous=first.hash, settings=settings)
    assert result.created and result.hash != first.hash
    assert result.line.amends == first.hash
    assert result.line.amendment_reason == "add a gender subgroup"
    chain = registry_mod.lineage(result.hash, settings)
    assert [c.hash for c in chain] == [first.hash, result.hash]
    # the reason may also come from the flag, and the amendment link is hashed content
    third = _write(
        tmp_path / "third.yaml",
        {**amended_doc, "version": "1.2.0", "amends": result.hash, "amendment_reason": ""},
    )
    result3 = registry_mod.amend(
        third, previous=result.hash, reason="cli reason", settings=settings
    )
    assert result3.line.amendment_reason == "cli reason" and result3.line.amends == result.hash
    assert [c.ref for c in registry_mod.lineage(result3.hash, settings)] == [
        SEED,
        "tracer_mortality@1.1.0",
        "tracer_mortality@1.2.0",
    ]
    assert result.hash != Protocol.model_validate(
        {**amended_doc, "amends": None, "amendment_reason": ""}
    ).content_hash(_seed_resolution())
    audits = [
        a["sql_text"].split(" ")[1]
        for a in _audit_lines(settings)
        if a["sql_text"].startswith("protocol ")
    ]
    assert audits.count("amend") == 2 and audits.count("freeze") >= 1


# ---------------------------------------------------------------------------
# 5. The run-side refusals and the D-25 policy hook in run.start
# ---------------------------------------------------------------------------


def test_run_refuses_unknown_modified_and_unfrozen(data_root: Path, tmp_path: Path) -> None:
    settings = _settings()
    frozen = registry_mod.freeze(registry_mod.seed_path(), settings)
    with pytest.raises(UnknownProtocolError):
        runners_mod.run_protocol("d" * 64, tier="fixture", settings=settings)
    with pytest.raises(ProtocolError, match="unknown runner"):
        runners_mod.run_protocol(frozen.hash, tier="fixture", runner="nope", settings=settings)
    with pytest.raises(ProtocolError, match="unknown tier"):
        runners_mod.run_protocol(frozen.hash, tier="prod", settings=settings)
    unfrozen = _write(tmp_path / "unfrozen.yaml", _mutated(feature_windows__gap_h=3))
    with pytest.raises(ProtocolRefusedError, match="not the frozen"):
        runners_mod.run_protocol(
            frozen.hash, tier="fixture", yaml_override=unfrozen, settings=settings
        )
    copy = registry_mod.frozen_path(frozen.hash, settings)
    _make_writable(copy)
    copy.write_text(
        copy.read_text(encoding="utf-8").replace("gap_h: 0", "gap_h: 4"), encoding="utf-8"
    )
    with pytest.raises(ProtocolRefusedError, match="now hashes"):
        runners_mod.run_protocol(frozen.hash, tier="fixture", settings=settings)
    refused = [
        a
        for a in _audit_lines(settings)
        if a["sql_text"].startswith("protocol run") and not a["allowed"]
    ]
    assert len(refused) == 3 and all(a["tier"] == "fixture" for a in refused)
    assert not list(settings.layout["runs"].glob("*/manifest.json")), "no run record for a refusal"


def test_run_start_policy_hook(data_root: Path) -> None:
    settings = _settings()

    def opened(**kwargs: Any) -> None:
        with run_mod.start("p", tier="fixture", settings=settings, doctor=False, **kwargs):
            pass

    for claim in HASH_REQUIRED_CLAIM_TYPES:
        with pytest.raises(run_mod.ProtocolPolicyError, match="frozen protocol hash"):
            opened(claim_type=claim)
    with pytest.raises(run_mod.ProtocolPolicyError, match="unknown claim_type"):
        opened(claim_type="speculative")
    with pytest.raises(run_mod.ProtocolPolicyError, match="64 hex"):
        opened(protocol_hash="abc")
    # a parenthesised qualifier is the earlier modules' convention: judged by its label
    with pytest.raises(run_mod.ProtocolPolicyError, match="frozen protocol hash"):
        opened(claim_type="causal (target trial)")
    opened(claim_type="exploratory (measurement process)")
    opened(claim_type="associational (exploratory)")
    assert run_mod.claim_label("Confirmatory (pre-registered)") == "confirmatory"
    assert run_mod.claim_label("exploratory") == "exploratory"
    assert issubclass(run_mod.ProtocolPolicyError, run_mod.RunLedgerError)
    with run_mod.start(
        "p",
        tier="fixture",
        kind="protocol",
        claim_type="confirmatory",
        protocol_id="x",
        protocol_hash="a" * 64,
        settings=settings,
        doctor=False,
    ) as r:
        pass
    manifest = run_mod.read_manifest(r.run_id, settings)
    assert manifest.claim_type == "confirmatory" and manifest.protocol_hash == "a" * 64
    with run_mod.start(
        "p", tier="fixture", claim_type="exploratory", settings=settings, doctor=False
    ):
        pass
    assert len(run_mod.read_ledger(settings)) == 4


# ---------------------------------------------------------------------------
# 6. The cohort_only runner on a fixture lake + the CLI end to end
# ---------------------------------------------------------------------------


def test_cohort_only_runner_builds_then_reuses_the_cohort(lake: Settings) -> None:
    settings = lake
    k = settings.k_suppression
    lake_root = settings.lake_root("fixture")
    assert not cohort_build.cohort_complete(lake_root, "fixture", TRACER)
    frozen = registry_mod.freeze(registry_mod.seed_path(), settings)
    outcome = runners_mod.run_protocol(frozen.hash, tier="fixture", settings=settings)
    assert outcome.protocol_hash == frozen.hash and outcome.runner == "cohort_only"
    manifest = run_mod.read_manifest(outcome.run_id, settings)
    assert manifest.kind == "protocol" and manifest.status == "ok" and manifest.tier == "fixture"
    assert manifest.protocol_id == "tracer_mortality" and manifest.protocol_hash == frozen.hash
    assert manifest.claim_type == "exploratory"
    refs = {(r.kind, r.name, r.version, r.hash) for r in manifest.refs}
    assert ("protocol", "tracer_mortality", "1.0.0", frozen.hash) in refs
    assert ("cohort", "first_icu_adults", "1.0.0", frozen.resolved.cohort_hash) in refs
    assert manifest.params["cohort_built_now"] is True and manifest.params["cohort_build_id"]
    assert manifest.params["cohort_def_hash"] == frozen.resolved.cohort_hash
    cohort_run = run_mod.read_manifest(manifest.params["cohort_run_id"], settings)
    assert cohort_run.kind == "cohort" and cohort_run.params["ref"] == TRACER
    assert cohort_build.cohort_complete(lake_root, "fixture", TRACER)
    assert set(manifest.snapshot_ids) == {"core"}
    assert [a.step for a in manifest.attrition] == [
        "base",
        "idx",
        "crit_01_adult",
        "crit_02_short_icu_stay",
        "cohort",
    ]
    for row in manifest.attrition:
        for value in (row.n_units, row.n_subjects):
            assert value is None or value == 0 or value >= k, "no small cell in the manifest"
    assert manifest.params["k"] == k and "attrition_report" in manifest.params
    ledger = [line for line in run_mod.read_ledger(settings) if line["run_id"] == outcome.run_id]
    assert ledger and ledger[0]["protocol_hash"] == frozen.hash and ledger[0]["kind"] == "protocol"
    # the summary
    summary = outcome.summary_path
    assert summary == runners_mod.summary_path(outcome.run_id, settings) and summary.is_file()
    text = summary.read_text(encoding="utf-8")
    assert text.isascii() and not BAND_TOKEN.search(text)
    assert "Claim type: **exploratory**" in text
    assert RETROSPECTIVE_STATEMENT in text
    assert f"frozen at `{frozen.hash}`" in text and "## Reproduction" in text
    assert "## Attrition (suppressed)" in text and "| `cohort` |" in text
    assert "Built by this run" in text and TRACER in text and "## References" in text
    assert "development 2008 - 2010" in text and "EP-129" in text
    assert "subject_id" not in text and "hadm_id" not in text
    for token in re.findall(r"\| `crit_[^|]*\| [^|]*\| ([^|]*) \| ([^|]*) \|", text):
        for cell in token:
            cell = cell.strip().lstrip("~").replace(",", "")
            assert cell.startswith("<") or cell == "-" or int(cell) == 0 or int(cell) >= k
    # a second run reuses the build
    again = runners_mod.run_protocol(frozen.hash, tier="fixture", settings=settings)
    assert again.run_id != outcome.run_id
    assert again.params["cohort_built_now"] is False and again.params["cohort_build_id"] is None
    assert again.params["cohort_run_id"] == manifest.params["cohort_run_id"]
    assert "Reused the tier build" in again.summary_path.read_text(encoding="utf-8")
    audits = [a for a in _audit_lines(settings) if a["sql_text"] == f"protocol run {frozen.hash}"]
    assert len(audits) == 2 and all(a["allowed"] for a in audits)
    # the views: runs.protocols joins the ledger on protocol_hash
    build_runs_db(settings)
    assert _view_count(settings, "protocols") == 1
    assert _view_count(settings, "ledger", f"WHERE protocol_hash = '{frozen.hash}'") == 2
    joined = safe_query(
        "SELECT p.claim_type, count(*) AS n FROM runs.ledger AS l JOIN runs.protocols AS p "
        "ON l.protocol_hash = p.hash GROUP BY 1",
        tier="fixture",
        k=1,
        settings=settings,
        actor="test_ep51",
    )
    assert joined.df.rows() == [("exploratory", 2)]


def test_cli_freeze_verify_list_show_amend_run(lake: Settings, tmp_path: Path) -> None:
    runner = helpers.cli_runner()
    root = ["--data-root", str(lake.data_root)]
    seed = str(registry_mod.seed_path())
    frozen = runner.invoke(app, [*root, "protocol", "freeze", seed, "--json"])
    assert frozen.exit_code == 0, frozen.output
    payload = json.loads(frozen.stdout)
    digest = payload["hash"]
    assert HEX64.match(digest) and payload["protocol"] == SEED
    plain = runner.invoke(app, [*root, "protocol", "freeze", seed])
    assert plain.exit_code == 0 and "already frozen" in plain.stdout and digest in plain.stdout
    listed = runner.invoke(app, [*root, "protocol", "list"])
    assert listed.exit_code == 0 and "tracer_" in listed.stdout and digest[:8] in listed.stdout
    listed_json = runner.invoke(
        app, [*root, "protocol", "list", "--json", "--id", "tracer_mortality"]
    )
    assert listed_json.exit_code == 0 and json.loads(listed_json.stdout)[0]["hash"] == digest
    shown = runner.invoke(app, [*root, "protocol", "show", digest])
    assert shown.exit_code == 0, shown.output
    assert "read-only" in shown.stdout and "id: tracer_mortality" in shown.stdout
    shown_yaml = runner.invoke(app, [*root, "protocol", "show", digest, "--yaml"])
    assert shown_yaml.exit_code == 0 and shown_yaml.stdout == registry_mod.seed_path().read_text(
        encoding="utf-8"
    )
    missing = runner.invoke(app, [*root, "protocol", "show", "b" * 64])
    assert missing.exit_code == 3 and "not in the registry" in missing.stderr
    ok = runner.invoke(app, [*root, "protocol", "verify", seed])
    assert ok.exit_code == 0 and "ok" in ok.stdout
    ok_hash = runner.invoke(app, [*root, "protocol", "verify", digest, "--json"])
    assert ok_hash.exit_code == 0 and json.loads(ok_hash.stdout)["status"] == "ok"
    edited = _write(tmp_path / "edited.yaml", _mutated(feature_windows__gap_h=2))
    drift = runner.invoke(app, [*root, "protocol", "verify", str(edited)])
    assert drift.exit_code == 1 and "drift" in drift.stdout
    unknown = runner.invoke(app, [*root, "protocol", "verify", "c" * 64])
    assert unknown.exit_code == 1 and "unknown" in unknown.stdout
    refused = runner.invoke(app, [*root, "protocol", "freeze", str(edited)])
    assert refused.exit_code == 3 and "already frozen" in refused.stderr
    invalid = _write(tmp_path / "invalid.yaml", _mutated(unit_of_analysis="edstay"))
    bad = runner.invoke(app, [*root, "protocol", "freeze", str(invalid)])
    assert bad.exit_code == 2 and "not available" in bad.stderr
    # run: refusals then a real run (the cohort is built by the module's earlier test)
    unfrozen_run = runner.invoke(
        app, [*root, "protocol", "run", digest, "--tier", "fixture", "--yaml", str(edited)]
    )
    assert unfrozen_run.exit_code == 3 and "not the frozen" in unfrozen_run.stderr
    unknown_run = runner.invoke(app, [*root, "protocol", "run", "c" * 64, "--tier", "fixture"])
    assert unknown_run.exit_code == 3 and "freeze it first" in unknown_run.stderr
    bad_runner = runner.invoke(
        app, [*root, "protocol", "run", digest, "--tier", "fixture", "--runner", "nope"]
    )
    assert bad_runner.exit_code == 2 and "unknown runner" in bad_runner.stderr
    ran = runner.invoke(
        app, [*root, "protocol", "run", digest, "--tier", "fixture", "--yaml", seed, "--json"]
    )
    assert ran.exit_code == 0, ran.output
    out = json.loads(ran.stdout)
    assert out["protocol_hash"] == digest and out["claim_type"] == "exploratory"
    manifest = run_mod.read_manifest(out["run_id"], lake)
    assert manifest.protocol_hash == digest and manifest.claim_type == "exploratory"
    assert Path(out["summary_path"]).is_file()
    ran_plain = runner.invoke(app, [*root, "protocol", "run", digest, "--tier", "fixture"])
    assert ran_plain.exit_code == 0 and "protocol_summary.md" in ran_plain.stdout
    # amend through the CLI
    amended = _write(
        tmp_path / "amended.yaml",
        _mutated(version="1.1.0", amends=digest, analysis_plan__subgroups=["by gender"]),
    )
    no_reason = runner.invoke(app, [*root, "protocol", "amend", str(amended), "--previous", digest])
    assert no_reason.exit_code == 2 and "needs a reason" in no_reason.stderr
    amend = runner.invoke(
        app,
        [*root, "protocol", "amend", str(amended), "--previous", digest, "--reason", "subgroup"],
    )
    assert amend.exit_code == 0, amend.output
    assert "amends" in amend.stdout and digest in amend.stdout
    lines = registry_mod.read_registry(lake)
    assert lines[-1].amends == digest and lines[-1].amendment_reason == "subgroup"
    shown2 = runner.invoke(app, [*root, "protocol", "show", lines[-1].hash])
    assert shown2.exit_code == 0 and "lineage:" in shown2.stdout


# ---------------------------------------------------------------------------
# 7. Docs, wiring, import budget
# ---------------------------------------------------------------------------


def test_methods_doc_is_in_sync(tmp_path: Path) -> None:
    page = registry_mod.methods_doc_path()
    assert page == DOCS / "methods" / "protocols.md" and page.is_file()
    text = page.read_text(encoding="utf-8")
    for needle in (
        "retrospective",
        "content hash",
        "runs/protocols.jsonl",
        "runs/protocols/<hash>.yaml",
        "ProtocolFrozenError",
        "ProtocolPolicyError",
        "runs.protocols",
        "cohort_only",
        "protocol_summary.md",
        "amends",
        "development_eras",
        "id: tracer_mortality",
        "mwh sql",
        "EP-128",
        "EP-129",
        "EP-52",
        "**`Protocol`**",
        "| `claim_type` |",
        "| `run` |",
        "byte for byte",
    ):
        assert needle in text, needle
    copy = tmp_path / "protocols.md"
    copy.write_text(text, encoding="utf-8", newline="\n")
    registry_mod.sync_methods_doc(copy)
    assert copy.read_text(encoding="utf-8") == text, "re-run `python -m mimicwarehouse.protocol`"
    assert registry_mod.render_example().rstrip("\n") in text
    assert registry_mod.render_refusals().rstrip("\n") in text
    assert not BAND_TOKEN.search(text)
    assert RETROSPECTIVE_STATEMENT.split(":")[0] in text


def test_cli_wiring_and_import_budget() -> None:
    result = helpers.cli_runner().invoke(app, ["protocol", "--help"])
    assert result.exit_code == 0
    for command in ("freeze", "verify", "amend", "list", "show", "run"):
        assert command in result.stdout
    assert runners_mod.runner_names() == ("cohort_only",)
    helpers.assert_import_budget(
        lazy=(
            "mimicwarehouse.run",
            "mimicwarehouse.safe",
            "mimicwarehouse.protocol.runners",
            "mimicwarehouse.cohort.build",
        )
    )


# ---------------------------------------------------------------------------
# 8. Dev tier: freeze + run the seed with cohort_only on the real dev catalog
# ---------------------------------------------------------------------------


@pytest.mark.tier("dev")
def test_dev_freeze_and_run_seed_protocol(dev_catalog: Path) -> None:
    settings = config.load_settings()
    frozen = registry_mod.freeze(registry_mod.seed_path(), settings)
    assert registry_mod.verify(frozen.hash, settings).ok
    outcome = runners_mod.run_protocol(
        frozen.hash,
        tier="dev",
        runner="cohort_only",
        settings=settings,
        command="uv run poe test -m ep_51 --tier dev",
    )
    manifest = run_mod.read_manifest(outcome.run_id, settings)
    assert manifest.kind == "protocol" and manifest.status == "ok" and manifest.tier == "dev"
    assert manifest.protocol_hash == frozen.hash and manifest.claim_type == "exploratory"
    assert manifest.params["cohort_def_hash"] == frozen.resolved.cohort_hash
    k = settings.k_suppression
    for row in manifest.attrition:
        for value in (row.n_units, row.n_subjects):
            assert value is None or value == 0 or value >= k
    text = outcome.summary_path.read_text(encoding="utf-8")
    assert text.isascii() and RETROSPECTIVE_STATEMENT in text and "**exploratory**" in text
    assert not BAND_TOKEN.search(text)
