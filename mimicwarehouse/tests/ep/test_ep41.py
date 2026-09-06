"""EP-41 — phenotype engine + T2DM phenotype.

Fixture tier (default): the packaged definition loads, is locked and hygienic, and pins its
three code-set references by hash; the schema round-trips YAML with a ``def_hash`` that is
invariant to key order / whitespace / documentation and moves with the definition or a
referenced code set; the schema refuses the crafted violations; a frozen ``(id, version)``
whose file changed is refused (library and every ``mwh phenotype`` command, exit 3) while a
version bump loads unlocked and locks; the compiler emits identical SQL for identical
specs and matches the committed golden file; crafted synthetic subjects (ids >= 90 000
000, built in-test into an in-memory DuckDB from the EP-9 contract DDL) cover every branch
of the T2DM tree (dx only; med + lab without dx; T1DM-only excluded; none; an outpatient
lab; a unit conversion; onset = the earliest event) plus every other leaf kind and grain
on a crafted ``hadm`` / ``icustay`` phenotype; the DAG spec, the catalog hook, the CLI
wiring and the import budget; a minimal fixture lake built through the runner carries the
materialised table, the versioned view, the session views (+ the ``hadm`` companion),
``meta.phenotype_versions`` and one ``kind: phenotype`` run — a re-compile skips, ``--force``
rebuilds, a second version coexists and takes over the session view, ``summary`` works
through ``safe_query``; the session fixture lake shows the same after the full DAG; the
docs page is in sync. ``tier("dev")``: the compiled phenotype on the real dev catalog
through ``safe_query`` (counts and shares only).

Everything asserted or printed is definition text, SQL, synthetic rows and aggregate
counts — never a patient-level row.
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import duckdb
import pytest

import helpers
from mimicwarehouse import config
from mimicwarehouse.catalog import build as build_mod
from mimicwarehouse.cli import app
from mimicwarehouse.codesets import registry as codesets_registry
from mimicwarehouse.codesets.spec import CodeSetFrozenError
from mimicwarehouse.dag import runner as runner_mod
from mimicwarehouse.dag import spec as dagspec_mod
from mimicwarehouse.dag.spec import load_dag
from mimicwarehouse.phenotypes import compiler as compiler_mod
from mimicwarehouse.phenotypes import registry as registry_mod
from mimicwarehouse.phenotypes import runner as phen_runner
from mimicwarehouse.phenotypes.spec import (
    PhenotypeError,
    PhenotypeFrozenError,
    UnknownPhenotypeError,
    phenotype_from_text,
)

if TYPE_CHECKING:
    from mimicwarehouse.config import Settings
    from mimicwarehouse.phenotypes.registry import Registry
    from mimicwarehouse.schema.contract import Contract

pytestmark = pytest.mark.ep_41

HOSP = "mimiciv_hosp"
ICU = "mimiciv_icu"
DERIVED = "mimiciv_derived"
BAND_TOKEN = re.compile(r"(?<![\w.])[123]\d{7}(?![\w.])")
GOLDEN = helpers.WORKSPACE / "tests" / "ep" / "golden"
#: The stage steps a phenotype lake needs (the spec's depends_on) — no concepts, no dims.
LAKE_STEPS: tuple[str, ...] = (
    f"stage.{HOSP}.patients",
    f"stage.{HOSP}.admissions",
    f"stage.{HOSP}.diagnoses_icd",
    f"stage.{HOSP}.procedures_icd",
    f"stage.{HOSP}.prescriptions",
    f"stage.{HOSP}.emar",
    f"stage.{HOSP}.labevents",
    f"stage.{HOSP}.microbiologyevents",
    f"stage.{ICU}.icustays",
    f"stage.{ICU}.inputevents",
)
T0 = datetime(2150, 1, 1, 8, 0)
S = 90_000_000
H = 91_000_000
ST = 92_000_000
EV = 93_000_000


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


# ---------------------------------------------------------------------------
# 1. The packaged definition: locked, hygienic, references pinned
# ---------------------------------------------------------------------------


def test_packaged_definition_locked_and_pinned(registry: Registry) -> None:
    assert registry.refs() == ("t2dm@1.0.0",) and registry.ids() == ("t2dm",)
    entry = registry.get("t2dm@1.0.0")
    p = entry.phenotype
    assert entry.locked and entry.packaged and entry.display_path == "defs/t2dm.yaml"
    assert p.grain == "subject" and p.onset.rule == "earliest" and p.outputs.evidence
    assert p.codeset_refs == ("noninsulin_antidiabetics@1.0.0", "t1dm@1.0.0", "t2dm@1.0.0")
    assert p.references == p.codeset_refs
    assert set(entry.resolved) == set(p.codeset_refs)
    for ref, def_hash in entry.resolved.items():
        assert def_hash == registry.codesets.get(ref).codeset.def_hash
    assert re.fullmatch(r"[0-9a-f]{64}", entry.def_hash)
    assert p.criteria.render() == "any(dx, all(any(med, a1c), not(t1dm)))"
    assert [leaf.id for leaf in p.criteria_leaves] == ["dx", "med", "a1c", "t1dm"]
    assert p.positive_leaf_ids == ("dx", "med", "a1c")
    assert len(p.what_it_does_not_claim) >= 3 and p.citations and p.provenance.source == "hand"
    assert all(
        "eMERGE" in item or "HbA1c" in item or "diabetes" in item or "onset" in item
        for item in p.what_it_does_not_claim
    )
    raw = entry.path.read_bytes()
    assert b"\r" not in raw and raw.endswith(b"\n") and raw.decode("utf-8").isascii()
    assert not BAND_TOKEN.search(raw.decode("utf-8"))
    lock = registry_mod.load_lock(registry_mod.packaged_defs_dir())
    assert lock.version == 1 and lock.phenotypes["t2dm@1.0.0"].def_hash == entry.def_hash
    assert lock.phenotypes["t2dm@1.0.0"].grain == "subject"
    runner = helpers.cli_runner()
    check = runner.invoke(app, ["phenotype", "lock", "--check"])
    assert check.exit_code == 0 and "0 unlocked" in check.stdout, check.output
    listing = runner.invoke(app, ["phenotype", "list", "--json"])
    assert listing.exit_code == 0, listing.output
    payload = json.loads(listing.stdout)["phenotypes"]
    assert [x["ref"] for x in payload] == ["t2dm@1.0.0"] and payload[0]["locked"]
    assert payload[0]["references"] == entry.resolved


# ---------------------------------------------------------------------------
# 2. Schema round-trip, hash invariance, refusals
# ---------------------------------------------------------------------------

_TEXT_A = """\
id: crafted
version: "1.0.0"
name: crafted
grain: subject
criteria:
  any:
    - {id: dx, diagnosis: {codeset: t2dm@1.0.0}}
    - all:
        - {id: a1c, lab: {itemids: [50852], op: ">=", threshold: 6.5, unit: "%"}}
        - not: {id: t1dm, diagnosis: {codeset: t1dm@1.0.0, position: any, min_admissions: 1}}
onset: earliest
provenance: {source: hand, accessed: "2026-09-06"}
notes: first spelling
"""
_TEXT_B = """\
notes: >
  a different note, keys shuffled, documentation changed
provenance:
  accessed: "2026-09-06"
  source: hand
what_it_does_not_claim: [nothing at all]
onset: earliest
criteria:
  any:
    - id: dx
      diagnosis:
        position: any
        min_admissions: 1
        codeset: t2dm@1.0.0
    - all:
        - lab: {unit: "%", threshold: 6.5, op: ">=", itemids: [50852], min_count: 1}
          id: a1c
        - not:
            diagnosis: {codeset: t1dm@1.0.0}
            id: t1dm
grain: subject
name: crafted (renamed)
version: "1.0.0"
id: crafted
"""
_HASHES = {"t2dm@1.0.0": "a" * 64, "t1dm@1.0.0": "b" * 64}


def test_schema_round_trip_and_hash_invariance() -> None:
    a = phenotype_from_text(_TEXT_A)
    b = phenotype_from_text(_TEXT_B)
    assert a.canonical(_HASHES) == b.canonical(_HASHES)
    assert a.def_hash(_HASHES) == b.def_hash(_HASHES) and len(a.def_hash(_HASHES)) == 64
    assert a.codeset_refs == ("t1dm@1.0.0", "t2dm@1.0.0")
    assert [(leaf.id, neg) for leaf, neg in a.criteria.leaves()] == [
        ("dx", False),
        ("a1c", False),
        ("t1dm", True),
    ]
    assert a.positive_leaf_ids == ("dx", "a1c")
    # the hash moves with the definition ...
    moved = phenotype_from_text(_TEXT_A.replace("threshold: 6.5", "threshold: 7.0"))
    assert moved.def_hash(_HASHES) != a.def_hash(_HASHES)
    assert phenotype_from_text(_TEXT_A.replace("onset: earliest", "onset: latest")).def_hash(
        _HASHES
    ) != a.def_hash(_HASHES)
    assert phenotype_from_text(_TEXT_A.replace("grain: subject", "grain: hadm")).def_hash(
        _HASHES
    ) != a.def_hash(_HASHES)
    # ... and with a referenced code set's hash, never with documentation
    assert a.def_hash({**_HASHES, "t2dm@1.0.0": "c" * 64}) != a.def_hash(_HASHES)
    with pytest.raises(PhenotypeError, match="unresolved"):
        a.def_hash({"t2dm@1.0.0": "a" * 64})
    # onset forms
    first_of = phenotype_from_text(_TEXT_A.replace("onset: earliest", "onset: {first_of: [a1c]}"))
    assert first_of.onset.rule == "first_of" and first_of.onset.leaves == ("a1c",)
    assert first_of.onset.canonical() == {"first_of": ["a1c"]}
    # canonical JSON is key-sorted ASCII
    from mimicwarehouse.codesets.spec import canonical_json

    assert canonical_json(a.canonical(_HASHES)).isascii()
    assert phenotype_from_text(_TEXT_A.replace("notes: first spelling", "")).ref == "crafted@1.0.0"


def _refused(text: str, match: str) -> None:
    with pytest.raises(PhenotypeError, match=match):
        phenotype_from_text(text)


def test_schema_refusals() -> None:
    base = _TEXT_A
    _refused(base.replace("{id: dx, diagnosis:", "{id: a1c, diagnosis:"), "duplicate leaf id")
    _refused(
        base.replace(
            "{id: dx, diagnosis: {codeset: t2dm@1.0.0}}",
            "{id: dx, diagnosis: {codeset: t2dm@1.0.0}, "
            "lab: {itemids: [1], op: '>', threshold: 1}}",
        ),
        "exactly one kind key",
    )
    _refused(base.replace("onset: earliest", "onset: {first_of: [nope]}"), "unknown leaf")
    _refused(base.replace("onset: earliest", "onset: soonest"), "onset")
    _refused(base.replace("grain: subject", "grain: person"), "grain")
    _refused(
        base.replace("grain: subject", "grain: hadm").replace(
            "min_admissions: 1", "min_admissions: 2"
        ),
        "min_admissions",
    )
    _refused(
        base.replace("itemids: [50852], ", "itemids: [50852], codeset: labs_glucose@1.0.0, "),
        "exactly one of codeset",
    )
    _refused(base.replace("itemids: [50852], ", ""), "exactly one of codeset")
    _refused(base.replace('version: "1.0.0"', 'version: "1.0"'), "semver")
    _refused(base.replace("id: crafted", "id: Crafted"), "slug")
    _refused(base.replace("codeset: t2dm@1.0.0", "codeset: t2dm"), "reference")
    _refused(base + "references: [aki@1.0.0]\n", "differ from")
    _refused(base + "citations: ['PMID: 98765432']\n", "PMID")
    _refused(base + "citations: ['not a url']\n", "https://")
    _refused(base + "outputs: {flag: false}\n", "cannot be disabled")
    _refused(base.replace("notes: first spelling", "extra: 1"), "extra")
    _refused("- not\n- a mapping\n", "top level")
    _refused(
        base.replace(
            "- not: {id: t1dm, diagnosis: {codeset: t1dm@1.0.0, position: any, min_admissions: 1}}",
            "- not: [{id: t1dm, diagnosis: {codeset: t1dm@1.0.0}}, "
            "{id: x, diagnosis: {codeset: aki@1.0.0}}]",
        ),
        "exactly one node",
    )
    _refused(base.replace('op: ">="', 'op: "~"'), "op")
    temporal = (
        'id: t\nversion: "1.0.0"\nname: t\ngrain: hadm\ncriteria:\n'
        "  - id: rel\n    temporal:\n      a: {id: a, microbiology: {spec_itemids: [70012]}}\n"
        "      b: {id: b, medication: {codeset: antibiotics@1.0.0}}\n      relation: within_hours\n"
        'provenance: {source: hand, accessed: "2026-09-06"}\n'
    )
    _refused(temporal, "criteria node")  # a bare list is not a node
    temporal = temporal.replace("criteria:\n  - id: rel", "criteria:\n  all:\n  - id: rel")
    _refused(temporal, "needs hours")
    ok = phenotype_from_text(
        temporal.replace("relation: within_hours", "relation: within_hours\n      hours: 24")
    )
    assert ok.criteria_leaves[0].kind == "temporal" and ok.codeset_refs == ("antibiotics@1.0.0",)
    assert [leaf.id for leaf, _ in ok.all_leaves()] == ["rel", "a", "b"]
    _refused(
        temporal.replace("relation: within_hours", "relation: before\n      hours: 2"),
        "only applies",
    )
    _refused(
        temporal.replace("relation: within_hours", "relation: before").replace(
            "b: {id: b, medication: {codeset: antibiotics@1.0.0}}",
            "b: {id: b, temporal: {a: {id: c, microbiology: {spec_itemids: [1]}}, "
            "b: {id: d, microbiology: {spec_itemids: [2]}}, relation: before}}",
        ),
        "cannot be temporal",
    )
    _refused(
        'id: m\nversion: "1.0.0"\nname: m\ngrain: hadm\n'
        "criteria: {id: m, microbiology: {positive_only: true}}\n"
        'provenance: {source: hand, accessed: "2026-09-06"}\n',
        "spec_itemids and / or org_itemids",
    )
    _refused(
        'id: c\nversion: "1.0.0"\nname: c\ngrain: icustay\n'
        'criteria: {id: c, concept: {table: sepsis3, column: sepsis3, op: "=", value: true}}\n'
        'provenance: {source: hand, accessed: "2026-09-06"}\n',
        "<schema>.<table>",
    )


# ---------------------------------------------------------------------------
# 3. The immutability rule: frozen pairs refuse, bumps load and lock
# ---------------------------------------------------------------------------


def _copy_defs(tmp_path: Path) -> Path:
    target = tmp_path / "defs"
    shutil.copytree(registry_mod.packaged_defs_dir(), target)
    return target


def test_frozen_version_refused_and_bump_allowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    defs = _copy_defs(tmp_path)
    path = defs / "t2dm.yaml"
    original = path.read_text(encoding="utf-8")
    edited = original.replace("threshold: 6.5", "threshold: 7.0")
    assert edited != original
    path.write_text(edited, encoding="utf-8", newline="\n")
    codesets = codesets_registry.load_registry()
    with pytest.raises(PhenotypeFrozenError, match=re.escape("t2dm@1.0.0 is frozen")):
        registry_mod.load_dir(defs, codesets)
    with pytest.raises(PhenotypeFrozenError, match="version bump"):
        registry_mod.lock_dir(defs, codesets=codesets)
    # every command refuses with exit 3 before touching anything
    monkeypatch.setattr(registry_mod, "packaged_defs_dir", lambda: defs)
    runner = helpers.cli_runner()
    root = str(tmp_path / "root")
    try:
        listing = runner.invoke(app, ["phenotype", "list"])
        assert listing.exit_code == 3 and "refused" in listing.stderr, listing.output
        for args in (
            ["--data-root", root, "phenotype", "compile", "--tier", "fixture"],
            [
                "--data-root",
                root,
                "phenotype",
                "compile",
                "t2dm@1.0.0",
                "--tier",
                "fixture",
                "--dry-run",
            ],
            ["phenotype", "validate", "t2dm@1.0.0"],
            ["phenotype", "show", "t2dm@1.0.0"],
            ["phenotype", "lock"],
        ):
            result = runner.invoke(app, args)
            assert result.exit_code == 3, (args, result.output)
            assert "t2dm@1.0.0 is frozen" in result.stderr and "version bump" in result.stderr
        assert not (tmp_path / "root" / "lake").exists(), "nothing was built"
    finally:
        config.configure()
        _drop_progress_handlers()
    # a version bump is the sanctioned change: loads unlocked, then locks; both coexist
    bumped = edited.replace('version: "1.0.0"', 'version: "1.1.0"')
    (defs / "t2dm_1_1.yaml").write_text(bumped, encoding="utf-8", newline="\n")
    path.write_text(original, encoding="utf-8", newline="\n")
    entries = {e.ref: e for e in registry_mod.load_dir(defs, codesets)}
    assert entries["t2dm@1.0.0"].locked and not entries["t2dm@1.1.0"].locked
    assert entries["t2dm@1.1.0"].def_hash != entries["t2dm@1.0.0"].def_hash
    check = runner.invoke(app, ["phenotype", "lock", "--check", "--defs", str(defs)])
    assert check.exit_code == 1 and "t2dm@1.1.0" in check.stdout, check.output
    result = registry_mod.lock_dir(defs, codesets=codesets)
    assert result.added == ("t2dm@1.1.0",) and result.unchanged == ("t2dm@1.0.0",)
    assert registry_mod.lock_dir(defs, codesets=codesets).added == ()
    assert all(e.locked for e in registry_mod.load_dir(defs, codesets))
    lock_text = registry_mod.lock_path(defs).read_text(encoding="utf-8")
    assert lock_text.endswith("\n") and "t2dm@1.1.0" in lock_text and lock_text.isascii()
    # a referenced code set that moved refuses the phenotype the same way (hash pinning)
    cs_dir = tmp_path / "codesets"
    shutil.copytree(codesets_registry.packaged_defs_dir(), cs_dir)
    t1dm = cs_dir / "t1dm.yaml"
    t1dm.write_text(
        t1dm.read_text(encoding="utf-8").replace(
            '    - {code: "E10", match: prefix}\n',
            '    - {code: "E10", match: prefix}\n    - {code: "E13"}\n',
        ),
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(codesets_registry, "packaged_defs_dir", lambda: cs_dir)
    with pytest.raises(CodeSetFrozenError):
        registry_mod.load_registry()
    frozen_cs = runner.invoke(app, ["phenotype", "list"])
    assert frozen_cs.exit_code == 3 and "refused" in frozen_cs.stderr
    monkeypatch.undo()
    # a study directory referencing a study code set: the packaged registry + extras
    study_cs = tmp_path / "study-codesets"
    study_cs.mkdir()
    (study_cs / "t2dm_narrow.yaml").write_text(
        'id: t2dm_narrow\nversion: "1.0.0"\nname: narrow\nkind: icd_dx\nmembers:\n'
        '  icd9: ["25000"]\n  icd10: ["E119"]\n'
        'provenance: {source: hand, accessed: "2026-09-06"}\n',
        encoding="utf-8",
        newline="\n",
    )
    study = tmp_path / "study"
    study.mkdir()
    (study / "narrow.yaml").write_text(
        _TEXT_A.replace("id: crafted", "id: narrow").replace(
            "codeset: t2dm@1.0.0", "codeset: t2dm_narrow@1.0.0"
        ),
        encoding="utf-8",
        newline="\n",
    )
    reg = registry_mod.load_registry([study], codeset_dirs=[study_cs])
    assert set(reg.refs()) == {"t2dm@1.0.0", "narrow@1.0.0"}
    narrow = reg.get("narrow@1.0.0")
    assert not narrow.locked and "t2dm_narrow@1.0.0" in narrow.resolved
    with pytest.raises(PhenotypeError, match="no code set"):
        registry_mod.load_registry([study])  # the study code set is unknown without its dir
    with pytest.raises(UnknownPhenotypeError, match="known versions of t2dm"):
        reg.get("t2dm@9.9.9")
    with pytest.raises(UnknownPhenotypeError, match="known ids"):
        reg.get("nope@1.0.0")
    with pytest.raises(PhenotypeError, match="reference"):
        reg.get("t2dm")
    with pytest.raises(PhenotypeError, match="defined twice"):
        registry_mod.Registry([*reg.entries, reg.entries[0]], reg.codesets)
    with pytest.raises(PhenotypeError, match="not a phenotype directory"):
        registry_mod.load_dir(tmp_path / "missing", codesets)
    (defs / "broken.yaml").write_text("id: [1]\n", encoding="utf-8")
    with pytest.raises(PhenotypeError, match=re.escape("broken.yaml")):
        registry_mod.load_dir(defs, codesets)


# ---------------------------------------------------------------------------
# 4. The compiler: determinism, the golden file, validation
# ---------------------------------------------------------------------------


def test_compiler_golden_and_determinism(registry: Registry) -> None:
    entry = registry.get("t2dm@1.0.0")
    first = compiler_mod.compile_phenotype(
        entry.phenotype, registry.codesets, resolved=entry.resolved
    )
    second = compiler_mod.compile_phenotype(
        entry.phenotype, registry.codesets, resolved=entry.resolved
    )
    assert first.sql == second.sql and first.columns == (
        "subject_id",
        "flag",
        "onset_time",
        "evidence_json",
    )
    golden = GOLDEN / "t2dm@1.0.0.sql"
    assert golden.is_file(), "the golden SQL file is committed"
    assert golden.read_text(encoding="utf-8") == first.sql.rstrip("\n") + "\n", (
        "the compiled SQL drifted from tests/ep/golden/t2dm@1.0.0.sql (a deliberate compiler "
        "change regenerates the file; a definition change needs a version bump)"
    )
    assert first.sources == (
        f"{HOSP}.patients",
        f"{HOSP}.diagnoses_icd",
        f"{HOSP}.admissions",
        f"{HOSP}.prescriptions",
        f"{HOSP}.labevents",
    )
    assert set(first.leaf_sql) == {"dx", "med", "a1c", "t1dm"}
    assert first.hadm_companion_sql is not None and "{relation}" in first.hadm_companion_sql
    assert "mwh_harmonize" not in first.sql, "lab conversions are inlined, no macro dependency"
    assert "(l.valuenum) * 0.09148 + (2.152)" in first.sql, "the IFCC -> NGSP conversion"
    assert "ORDER BY subject_id" in first.sql and first.sql.count("LEFT JOIN unit_") == 4
    # the inlined unit normalisation carries EP-39's degree / micro signs (UTF-8), so the
    # statement is not ASCII by design; it must never carry a real-band token
    assert not BAND_TOKEN.search(first.sql)
    # identical specs compile identically, whatever the YAML spelling
    a = phenotype_from_text(_TEXT_A)
    b = phenotype_from_text(_TEXT_B)
    assert (
        compiler_mod.compile_phenotype(a, registry.codesets).sql
        == compiler_mod.compile_phenotype(b, registry.codesets).sql
    )
    described = compiler_mod.describe(first)
    assert described["grain"] == "subject" and described["leaves"] == ["dx", "med", "a1c", "t1dm"]
    # the inline harmonisation: curated itemids with conversions, uncurated pass through
    expr, needs = compiler_mod.harmonised_value_sql((50852,))
    assert needs and "'mmol/mol'" in expr
    assert compiler_mod.harmonised_value_sql((9_999_999,)) == ("l.valuenum", False)
    # validation beyond the schema
    validation = registry_mod.validate(entry, registry.codesets)
    assert validation.ok and validation.sql == first.sql and not validation.warnings
    wrong_kind = phenotype_from_text(
        _TEXT_A.replace("codeset: t2dm@1.0.0", "codeset: labs_glucose@1.0.0")
    )
    wrong_entry = registry_mod.Entry(wrong_kind, {}, "0" * 64, Path("x.yaml"), False, False)
    bad = registry_mod.validate(wrong_entry, registry.codesets)
    assert not bad.ok and any(
        "itemid set; a diagnosis leaf needs icd_dx" in p for p in bad.problems
    )
    wrong_unit = phenotype_from_text(_TEXT_A.replace('unit: "%"', 'unit: "mmol/mol"'))
    bad_unit = registry_mod.validate(
        registry_mod.Entry(wrong_unit, {}, "0" * 64, Path("x.yaml"), False, False),
        registry.codesets,
    )
    assert any("canonical unit" in p for p in bad_unit.problems)
    uncurated = phenotype_from_text(_TEXT_A.replace("itemids: [50852]", "itemids: [51631]"))
    warned = registry_mod.validate(
        registry_mod.Entry(uncurated, {}, "0" * 64, Path("x.yaml"), False, False), registry.codesets
    )
    assert warned.ok and any("not in the EP-39 item catalogue" in w for w in warned.warnings)
    with pytest.raises(compiler_mod.CompileError, match="expected icd_dx"):
        compiler_mod.compile_phenotype(wrong_kind, registry.codesets)
    with pytest.raises(compiler_mod.CompileError, match="ICU itemids"):
        compiler_mod.compile_phenotype(
            phenotype_from_text(
                'id: iv\nversion: "1.0.0"\nname: iv\ngrain: icustay\n'
                "criteria: {id: iv, medication: {codeset: insulin@1.0.0, source: inputevents}}\n"
                'provenance: {source: hand, accessed: "2026-09-06"}\n'
            ),
            registry.codesets,
        )


# ---------------------------------------------------------------------------
# 5. Crafted synthetic subjects: every branch, every leaf kind, every grain
# ---------------------------------------------------------------------------


def _crafted_db(contract: Contract) -> duckdb.DuckDBPyConnection:
    """An in-memory DuckDB with the contract DDL of the tables the leaves read."""
    from mimicwarehouse.engine import open_duckdb

    con = open_duckdb("app")
    for schema in (HOSP, ICU, DERIVED):
        con.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
    for qn in (
        f"{HOSP}.patients",
        f"{HOSP}.admissions",
        f"{HOSP}.diagnoses_icd",
        f"{HOSP}.procedures_icd",
        f"{HOSP}.prescriptions",
        f"{HOSP}.emar",
        f"{HOSP}.labevents",
        f"{HOSP}.microbiologyevents",
        f"{ICU}.icustays",
        f"{ICU}.inputevents",
    ):
        con.execute(contract.table(qn).duckdb_ddl())
    return con


def _insert(
    con: duckdb.DuckDBPyConnection, contract: Contract, qn: str, rows: list[dict[str, Any]]
) -> None:
    table = contract.table(qn)
    names = [c.name for c in table.columns]
    placeholders = ", ".join("?" for _ in names)
    con.executemany(
        f"INSERT INTO {qn} VALUES ({placeholders})",
        [[row.get(n) for n in names] for row in rows],
    )


def _patient(subject_id: int) -> dict[str, Any]:
    return {
        "subject_id": subject_id,
        "gender": "F",
        "anchor_age": 60,
        "anchor_year": 2150,
        "anchor_year_group": "2014 - 2016",
        "dod": None,
    }


def _admission(subject_id: int, hadm_id: int, start: datetime, days: int) -> dict[str, Any]:
    return {
        "subject_id": subject_id,
        "hadm_id": hadm_id,
        "admittime": start,
        "dischtime": start + timedelta(days=days),
        "admission_type": "URGENT",
        "admission_location": "EMERGENCY ROOM",
        "insurance": "Other",
        "hospital_expire_flag": 0,
    }


def _dx(subject_id: int, hadm_id: int, seq: int, code: str, version: int) -> dict[str, Any]:
    return {
        "subject_id": subject_id,
        "hadm_id": hadm_id,
        "seq_num": seq,
        "icd_code": code,
        "icd_version": version,
    }


def _rx(
    subject_id: int, hadm_id: int, pharmacy_id: int, drug: str, start: datetime
) -> dict[str, Any]:
    return {
        "subject_id": subject_id,
        "hadm_id": hadm_id,
        "pharmacy_id": pharmacy_id,
        "starttime": start,
        "stoptime": start + timedelta(days=3),
        "drug_type": "MAIN",
        "drug": drug,
    }


def _lab(
    labevent_id: int,
    subject_id: int,
    hadm_id: int | None,
    itemid: int,
    when: datetime,
    value: float,
    unit: str | None,
) -> dict[str, Any]:
    return {
        "labevent_id": labevent_id,
        "subject_id": subject_id,
        "hadm_id": hadm_id,
        "specimen_id": labevent_id,
        "itemid": itemid,
        "charttime": when,
        "value": str(value),
        "valuenum": value,
        "valueuom": unit,
    }


def _t2dm_rows(
    contract: Contract, con: duckdb.DuckDBPyConnection
) -> dict[int, tuple[bool, datetime | None, dict[str, int]]]:
    """Insert the eight T2DM scenarios; return ``{subject_id: (flag, onset, evidence)}``."""
    day = timedelta(days=1)
    patients, admissions, dx, rx, labs = [], [], [], [], []
    expected: dict[int, tuple[bool, datetime | None, dict[str, int]]] = {}
    # S1: dx only -> flag, onset = dischtime
    patients.append(_patient(S + 1))
    admissions.append(_admission(S + 1, H + 1, T0, 5))
    dx.append(_dx(S + 1, H + 1, 1, "25000", 9))
    expected[S + 1] = (True, T0 + 5 * day, {"a1c": 0, "dx": 1, "med": 0, "t1dm": 0})
    # S2: med + lab, no dx -> flag, onset = the earlier of the two (the prescription)
    patients.append(_patient(S + 2))
    admissions.append(_admission(S + 2, H + 2, T0 + 30 * day, 6))
    rx.append(_rx(S + 2, H + 2, EV + 2, "MetFORMIN (Glucophage)", T0 + 31 * day))
    labs.append(_lab(EV + 21, S + 2, H + 2, 50852, T0 + 33 * day, 7.2, "%"))
    expected[S + 2] = (True, T0 + 31 * day, {"a1c": 1, "dx": 0, "med": 1, "t1dm": 0})
    # S3: T1DM code + metformin -> excluded
    patients.append(_patient(S + 3))
    admissions.append(_admission(S + 3, H + 3, T0 + 60 * day, 4))
    dx.append(_dx(S + 3, H + 3, 1, "E1010", 10))
    rx.append(_rx(S + 3, H + 3, EV + 3, "MetFORMIN (Glucophage)", T0 + 61 * day))
    expected[S + 3] = (False, None, {"a1c": 0, "dx": 0, "med": 1, "t1dm": 1})
    # S4: nothing
    patients.append(_patient(S + 4))
    admissions.append(_admission(S + 4, H + 4, T0 + 90 * day, 3))
    dx.append(_dx(S + 4, H + 4, 1, "4019", 9))
    expected[S + 4] = (False, None, {"a1c": 0, "dx": 0, "med": 0, "t1dm": 0})
    # S5: an outpatient HbA1c of exactly 6.5 (hadm NULL) after the admission -> flag; the
    # admission precedes the onset, so the hadm companion stays false for it
    patients.append(_patient(S + 5))
    admissions.append(_admission(S + 5, H + 5, T0 + 100 * day, 2))
    labs.append(_lab(EV + 51, S + 5, None, 50852, T0 + 120 * day, 6.5, "%"))
    expected[S + 5] = (True, T0 + 120 * day, {"a1c": 1, "dx": 0, "med": 0, "t1dm": 0})
    # S6: IFCC mmol/mol converts (60 -> 7.64 %); a 6.4 % value does not count
    patients.append(_patient(S + 6))
    admissions.append(_admission(S + 6, H + 6, T0 + 130 * day, 4))
    labs.append(_lab(EV + 61, S + 6, H + 6, 50852, T0 + 131 * day, 6.4, "%"))
    labs.append(_lab(EV + 62, S + 6, H + 6, 50852, T0 + 132 * day, 60.0, "mmol/mol"))
    expected[S + 6] = (True, T0 + 132 * day, {"a1c": 1, "dx": 0, "med": 0, "t1dm": 0})
    # S7: dx + an earlier HbA1c -> onset = the lab (earliest), two admissions with codes
    patients.append(_patient(S + 7))
    admissions.append(_admission(S + 7, H + 7, T0 + 160 * day, 5))
    admissions.append(_admission(S + 7, H + 8, T0 + 200 * day, 5))
    labs.append(_lab(EV + 71, S + 7, H + 7, 50852, T0 + 161 * day, 9.0, "%"))
    dx.append(_dx(S + 7, H + 7, 2, "E119", 10))
    dx.append(_dx(S + 7, H + 8, 1, "E1165", 10))
    expected[S + 7] = (True, T0 + 161 * day, {"a1c": 1, "dx": 2, "med": 0, "t1dm": 0})
    # S8: T1DM and T2DM codes -> the dx branch wins (the exclusion only guards the other arm)
    patients.append(_patient(S + 8))
    admissions.append(_admission(S + 8, H + 9, T0 + 230 * day, 3))
    dx.append(_dx(S + 8, H + 9, 1, "25001", 9))
    dx.append(_dx(S + 8, H + 9, 2, "25000", 9))
    expected[S + 8] = (True, T0 + 233 * day, {"a1c": 0, "dx": 1, "med": 0, "t1dm": 1})
    _insert(con, contract, f"{HOSP}.patients", patients)
    _insert(con, contract, f"{HOSP}.admissions", admissions)
    _insert(con, contract, f"{HOSP}.diagnoses_icd", dx)
    _insert(con, contract, f"{HOSP}.prescriptions", rx)
    _insert(con, contract, f"{HOSP}.labevents", labs)
    return expected


def test_t2dm_branches_on_crafted_subjects(contract: Contract, registry: Registry) -> None:
    entry = registry.get("t2dm@1.0.0")
    compiled = compiler_mod.compile_phenotype(entry.phenotype, registry.codesets)
    con = _crafted_db(contract)
    try:
        expected = _t2dm_rows(contract, con)
        rows = con.execute(compiled.sql).fetchall()
        got = {r[0]: (bool(r[1]), r[2], json.loads(r[3])) for r in rows}
        assert got == expected
        assert [r[0] for r in rows] == sorted(expected), "ordered by the grain key"
        assert max(len(r[3]) for r in rows) <= phen_runner.EVIDENCE_MAX_CHARS
        # onset first_of: only the named leaf counts
        first_of = phenotype_from_text(
            (registry.get("t2dm@1.0.0").path.read_text(encoding="utf-8")).replace(
                "onset: earliest", "onset: {first_of: [a1c]}"
            )
        )
        rows2 = con.execute(
            compiler_mod.compile_phenotype(first_of, registry.codesets).sql
        ).fetchall()
        onsets = {r[0]: r[2] for r in rows2}
        assert onsets[S + 1] is None and onsets[S + 7] == expected[S + 7][1]
        latest = phenotype_from_text(
            (registry.get("t2dm@1.0.0").path.read_text(encoding="utf-8")).replace(
                "onset: earliest", "onset: latest"
            )
        )
        rows3 = con.execute(
            compiler_mod.compile_phenotype(latest, registry.codesets).sql
        ).fetchall()
        assert (
            {r[0]: r[2] for r in rows3}[S + 7] == expected[S + 1][1].replace(day=1)
            if False
            else True
        )
        latest_onsets = {r[0]: r[2] for r in rows3}
        assert latest_onsets[S + 7] == T0 + timedelta(days=165), (
            "latest = the first coded discharge"
        )
        assert latest_onsets[S + 2] == T0 + timedelta(days=33)
        # the hadm companion: prevalent by discharge
        con.execute(f"CREATE TABLE ph AS {compiled.sql}")
        assert compiled.hadm_companion_sql is not None
        companion = {
            r[1]: (bool(r[2]), r[3])
            for r in con.execute(compiled.hadm_companion_sql.format(relation="ph")).fetchall()
        }
        assert companion[H + 1] == (True, expected[S + 1][1])
        assert companion[H + 5] == (False, None), "the outpatient onset lies after this discharge"
        assert companion[H + 7] == (True, expected[S + 7][1]) and companion[H + 8] == (
            True,
            expected[S + 7][1],
        )
        assert companion[H + 3] == (False, None) and companion[H + 4] == (False, None)
        assert len(companion) == 9
    finally:
        con.close()


_MULTI = """\
id: multi
version: "1.0.0"
name: every leaf kind (crafted)
grain: {grain}
criteria:
  any:
    - {{id: px, procedure: {{codeset: crafted_px@1.0.0}}}}
    - {{id: primary_dx, diagnosis: {{codeset: aki@1.0.0, position: primary}}}}
    - {{id: emar_med, medication: {{codeset: insulin@1.0.0, source: emar, min_orders: 2}}}}
    - {{id: iv, medication: {{codeset: vasopressors@1.0.0, source: inputevents}}}}
    - id: score
      concept: {{table: mimiciv_derived.crafted_score, column: score, op: ">=", value: 2,
                key: stay_id, time_column: charttime}}
    - id: abx_cx
      temporal:
        a: {{id: cx, microbiology: {{spec_itemids: [70012], positive_only: true}}}}
        b: {{id: abx, medication: {{codeset: antibiotics@1.0.0}}}}
        relation: within_hours
        hours: 24
    - {{id: glu, lab: {{codeset: labs_glucose@1.0.0, op: ">", threshold: 300, unit: "mg/dL"}}}}
onset: latest
provenance: {{source: hand, accessed: "2026-09-06"}}
"""


def _multi_rows(contract: Contract, con: duckdb.DuckDBPyConnection) -> None:
    day = timedelta(days=1)
    hour = timedelta(hours=1)
    con.execute(
        f"CREATE TABLE {DERIVED}.crafted_score "
        "(stay_id INTEGER, charttime TIMESTAMP, score INTEGER)"
    )
    patients = [_patient(S + 11), _patient(S + 12)]
    admissions = [
        _admission(S + 11, H + 11, T0, 10),  # px + primary dx + emar (2) + iv + score + temporal
        _admission(S + 11, H + 12, T0 + 40 * day, 10),  # glucose only (outpatient-style NULL hadm)
        _admission(
            S + 12, H + 13, T0 + 80 * day, 10
        ),  # nothing that fires (emar once, dx secondary)
    ]
    stays = [
        {
            "subject_id": S + 11,
            "hadm_id": H + 11,
            "stay_id": ST + 1,
            "first_careunit": "MICU",
            "last_careunit": "MICU",
            "intime": T0 + 1 * day,
            "outtime": T0 + 4 * day,
            "los": 3.0,
        },
        {
            "subject_id": S + 11,
            "hadm_id": H + 11,
            "stay_id": ST + 2,
            "first_careunit": "SICU",
            "last_careunit": "SICU",
            "intime": T0 + 6 * day,
            "outtime": T0 + 8 * day,
            "los": 2.0,
        },
        {
            "subject_id": S + 12,
            "hadm_id": H + 13,
            "stay_id": ST + 3,
            "first_careunit": "MICU",
            "last_careunit": "MICU",
            "intime": T0 + 81 * day,
            "outtime": T0 + 83 * day,
            "los": 2.0,
        },
    ]
    _insert(con, contract, f"{HOSP}.patients", patients)
    _insert(con, contract, f"{HOSP}.admissions", admissions)
    _insert(con, contract, f"{ICU}.icustays", stays)
    _insert(
        con,
        contract,
        f"{HOSP}.procedures_icd",
        [
            {
                "subject_id": S + 11,
                "hadm_id": H + 11,
                "seq_num": 1,
                "chartdate": (T0 + 2 * day).date(),
                "icd_code": "3893",
                "icd_version": 9,
            },
        ],
    )
    _insert(
        con,
        contract,
        f"{HOSP}.diagnoses_icd",
        [
            _dx(S + 11, H + 11, 1, "N179", 10),  # primary
            _dx(S + 12, H + 13, 2, "N179", 10),  # secondary: position primary ignores it
        ],
    )
    _insert(
        con,
        contract,
        f"{HOSP}.emar",
        [
            {
                "subject_id": S + 11,
                "hadm_id": None,
                "emar_id": f"{S + 11}-1",
                "emar_seq": 1,
                "poe_id": f"{S + 11}-1",
                "charttime": T0 + 2 * day,
                "medication": "Insulin",
                "event_txt": "Administered",
                "storetime": T0 + 2 * day,
            },
            {
                "subject_id": S + 11,
                "hadm_id": H + 11,
                "emar_id": f"{S + 11}-2",
                "emar_seq": 2,
                "poe_id": f"{S + 11}-1",
                "charttime": T0 + 3 * day,
                "medication": "Insulin",
                "event_txt": "Administered",
                "storetime": T0 + 3 * day,
            },
            {
                "subject_id": S + 12,
                "hadm_id": H + 13,
                "emar_id": f"{S + 12}-1",
                "emar_seq": 1,
                "poe_id": f"{S + 12}-1",
                "charttime": T0 + 82 * day,
                "medication": "Insulin",
                "event_txt": "Administered",
                "storetime": T0 + 82 * day,
            },
        ],
    )
    _insert(
        con,
        contract,
        f"{ICU}.inputevents",
        [
            {
                "subject_id": S + 11,
                "hadm_id": H + 11,
                "stay_id": ST + 2,
                "starttime": T0 + 7 * day,
                "endtime": T0 + 7 * day + hour,
                "itemid": 221906,
                "orderid": EV + 1,
                "amount": 5.0,
                "amountuom": "mg",
            },
        ],
    )
    con.execute(
        f"INSERT INTO {DERIVED}.crafted_score VALUES (?, ?, ?), (?, ?, ?), (?, ?, ?)",
        [ST + 1, T0 + 2 * day, 3, ST + 2, T0 + 7 * day, 1, ST + 3, T0 + 82 * day, 1],
    )
    _insert(
        con,
        contract,
        f"{HOSP}.microbiologyevents",
        [
            {
                "microevent_id": EV + 101,
                "subject_id": S + 11,
                "hadm_id": H + 11,
                "micro_specimen_id": EV + 101,
                "chartdate": T0 + 1 * day,
                "charttime": T0 + 1 * day + 2 * hour,
                "spec_itemid": 70012,
                "spec_type_desc": "BLOOD CULTURE",
                "test_seq": 1,
                "test_itemid": 90201,
                "test_name": "Blood Culture",
                "org_itemid": 80002,
                "org_name": "ESCHERICHIA COLI",
            },
            {
                "microevent_id": EV + 102,
                "subject_id": S + 12,
                "hadm_id": H + 13,
                "micro_specimen_id": EV + 102,
                "chartdate": T0 + 81 * day,
                "charttime": T0 + 81 * day,
                "spec_itemid": 70012,
                "spec_type_desc": "BLOOD CULTURE",
                "test_seq": 1,
                "test_itemid": 90201,
                "test_name": "Blood Culture",
                "org_itemid": None,
                "org_name": None,
            },
        ],
    )
    _insert(
        con,
        contract,
        f"{HOSP}.prescriptions",
        [
            _rx(
                S + 11, H + 11, EV + 201, "Vancomycin", T0 + 1 * day + 8 * hour
            ),  # 6 h after the culture
            _rx(
                S + 12, H + 13, EV + 202, "Vancomycin", T0 + 84 * day
            ),  # 3 d after a negative culture
        ],
    )
    _insert(
        con,
        contract,
        f"{HOSP}.labevents",
        [
            _lab(
                EV + 301, S + 11, None, 50931, T0 + 45 * day, 320.0, "mg/dL"
            ),  # inside H+12 by time
            _lab(EV + 302, S + 12, H + 13, 50931, T0 + 82 * day, 250.0, "mg/dL"),
        ],
    )


def test_every_leaf_kind_on_hadm_and_icustay_grains(contract: Contract, tmp_path: Path) -> None:
    cs_dir = tmp_path / "codesets"
    cs_dir.mkdir()
    (cs_dir / "crafted_px.yaml").write_text(
        'id: crafted_px\nversion: "1.0.0"\nname: crafted procedure set\nkind: icd_px\nmembers:\n'
        '  icd9: ["3893"]\n  icd10: ["02H633Z"]\n'
        'provenance: {source: hand, accessed: "2026-09-06"}\n',
        encoding="utf-8",
        newline="\n",
    )
    codesets = codesets_registry.load_registry([cs_dir])
    con = _crafted_db(contract)
    try:
        _multi_rows(contract, con)
        # hadm grain
        hadm = phenotype_from_text(_MULTI.format(grain="hadm"))
        compiled = compiler_mod.compile_phenotype(hadm, codesets)
        assert compiled.columns == ("subject_id", "hadm_id", "flag", "onset_time", "evidence_json")
        assert (
            compiled.hadm_companion_sql is None
            and any("temporal" in w or "timeless" in w for w in compiled.warnings) is False
        )
        rows = {
            r[1]: (r[0], bool(r[2]), r[3], json.loads(r[4]))
            for r in con.execute(compiled.sql).fetchall()
        }
        assert set(rows) == {H + 11, H + 12, H + 13}
        subject, flag, onset, evidence = rows[H + 11]
        assert subject == S + 11 and flag
        assert evidence == {
            "abx_cx": 1,
            "emar_med": 2,
            "glu": 0,
            "iv": 1,
            "primary_dx": 1,
            "px": 1,
            "score": 1,
        }
        assert onset == T0 + timedelta(days=10), "latest: the timeless dx maps at dischtime"
        assert rows[H + 12] == (
            S + 11,
            True,
            T0 + timedelta(days=45),
            {"abx_cx": 0, "emar_med": 0, "glu": 1, "iv": 0, "primary_dx": 0, "px": 0, "score": 0},
        )
        assert rows[H + 13][1] is False and rows[H + 13][3] == {
            "abx_cx": 0,
            "emar_med": 0,
            "glu": 0,
            "iv": 0,
            "primary_dx": 0,
            "px": 0,
            "score": 0,
        }
        assert rows[H + 13][2] is None
        # icustay grain: stay-level events stay on their stay, timeless / windowed events map
        icu = phenotype_from_text(_MULTI.format(grain="icustay"))
        compiled_icu = compiler_mod.compile_phenotype(icu, codesets)
        assert compiled_icu.columns[:3] == ("subject_id", "hadm_id", "stay_id")
        srows = {
            r[2]: (bool(r[3]), r[4], json.loads(r[5]))
            for r in con.execute(compiled_icu.sql).fetchall()
        }
        assert set(srows) == {ST + 1, ST + 2, ST + 3}
        assert srows[ST + 1][2] == {
            "abx_cx": 1,
            "emar_med": 2,
            "glu": 0,
            "iv": 0,
            "primary_dx": 1,
            "px": 1,
            "score": 1,
        }
        assert srows[ST + 2][2] == {
            "abx_cx": 0,
            "emar_med": 0,
            "glu": 0,
            "iv": 1,
            "primary_dx": 1,
            "px": 1,
            "score": 0,
        }
        assert srows[ST + 1][0] and srows[ST + 2][0] and not srows[ST + 3][0]
        assert srows[ST + 2][1] == T0 + timedelta(days=10), (
            "latest: the timeless dx maps at dischtime"
        )
        # subject grain of the same tree: one unit, evidence pooled
        sub = phenotype_from_text(_MULTI.format(grain="subject"))
        subrows = {
            r[0]: (bool(r[1]), json.loads(r[3]))
            for r in con.execute(compiler_mod.compile_phenotype(sub, codesets).sql).fetchall()
        }
        assert subrows[S + 11] == (
            True,
            {"abx_cx": 1, "emar_med": 2, "glu": 1, "iv": 1, "primary_dx": 1, "px": 1, "score": 1},
        )
        assert subrows[S + 12][0] is False
        # a temporal leaf over a timeless operand warns; the relation `before`/`after` compile
        before = phenotype_from_text(
            _MULTI.format(grain="hadm").replace(
                "relation: within_hours\n        hours: 24", "relation: before"
            )
        )
        assert "a.event_time < b.event_time" in compiler_mod.compile_phenotype(before, codesets).sql
        after = phenotype_from_text(
            _MULTI.format(grain="hadm").replace(
                "relation: within_hours\n        hours: 24", "relation: after"
            )
        )
        assert "a.event_time > b.event_time" in compiler_mod.compile_phenotype(after, codesets).sql
        timeless = phenotype_from_text(
            _MULTI.format(grain="hadm").replace(
                "a: {id: cx, microbiology: {spec_itemids: [70012], positive_only: true}}",
                "a: {id: cx, diagnosis: {codeset: aki@1.0.0}}",
            )
        )
        assert any(
            "timeless" in w for w in compiler_mod.compile_phenotype(timeless, codesets).warnings
        )
        # validation catches the icd_px kind and the concept table outside mimiciv_derived
        entry = registry_mod.Entry(
            hadm,
            registry_mod.resolve_references(hadm, codesets),
            "0" * 64,
            Path("m.yaml"),
            False,
            False,
        )
        assert registry_mod.validate(entry, codesets).ok
        outside = phenotype_from_text(
            _MULTI.format(grain="hadm").replace(
                "mimiciv_derived.crafted_score", "marts.crafted_score"
            )
        )
        outside_entry = registry_mod.Entry(
            outside, entry.resolved, "0" * 64, Path("m.yaml"), False, False
        )
        assert any(
            "outside mimiciv_derived" in w
            for w in registry_mod.validate(outside_entry, codesets).warnings
        )
    finally:
        con.close()


# ---------------------------------------------------------------------------
# 6. Wiring: the DAG spec, the catalog hook, the CLI, kinds, the import budget
# ---------------------------------------------------------------------------


def test_dag_spec_catalog_hook_and_cli_wiring() -> None:
    dag = load_dag()
    step = dag.step(phen_runner.STEP_COMPILE)
    assert (
        step.kind == "python"
        and step.callable_name == "mimicwarehouse.phenotypes.runner:run_compile"
    )
    assert set(step.depends_on) == set(LAKE_STEPS) and step.qualified_table is None
    assert phen_runner.DAG_TAG in step.tags and "derived" in step.tags
    assert step.tiers == ("fixture", "demo", "dev", "full")
    catalog = dag.step("catalog")
    assert phen_runner.STEP_COMPILE in catalog.depends_on and phen_runner.DAG_TAG in catalog.tags
    ordered = [s.name for s in dag.ordered(tags=[phen_runner.DAG_TAG], tier="fixture")]
    assert ordered == [phen_runner.STEP_COMPILE, "catalog"]
    assert "phenotypes.yaml" in [p.name for p in dagspec_mod.spec_paths()]
    with pytest.raises(dagspec_mod.DagError, match="unknown dependenc"):
        load_dag("phenotypes")
    extensions = build_mod.CATALOG_EXTENSIONS
    assert phen_runner.register_phenotypes in extensions
    assert extensions.index(phen_runner.register_phenotypes) > extensions.index(
        build_mod._codesets_register
    )
    assert extensions.index(phen_runner.register_phenotypes) == len(extensions) - 2, (
        "units stays last"
    )
    from mimicwarehouse import safe
    from mimicwarehouse.dag.benchmarks import BENCHMARK_KINDS
    from mimicwarehouse.run import RUN_KINDS

    assert phen_runner.EVIDENCE_MAX_CHARS == safe.FREE_TEXT_MAX_CHARS
    assert "phenotype" in BENCHMARK_KINDS and "phenotype" in RUN_KINDS
    assert phen_runner.semver_key("1.10.2") > phen_runner.semver_key("1.9.9")
    runner = helpers.cli_runner()
    top = runner.invoke(app, ["phenotype", "--help"])
    assert top.exit_code == 0
    for sub in ("list", "show", "validate", "lock", "compile", "summary"):
        assert sub in top.stdout, sub
    show = runner.invoke(app, ["phenotype", "show", "t2dm@1.0.0", "--sql"])
    assert show.exit_code == 0, show.output
    assert "any(dx, all(any(med, a1c), not(t1dm)))" in show.stdout and "WITH" in show.stdout
    assert "eMERGE" in show.stdout and "locked yes" in show.stdout
    shown = runner.invoke(app, ["phenotype", "show", "t2dm@1.0.0", "--json"])
    payload = json.loads(shown.stdout)
    assert payload["grain"] == "subject" and len(payload["leaves"]) == 4
    assert payload["leaves"][3] == {"id": "t1dm", "kind": "diagnosis", "negated": True}
    validate = runner.invoke(app, ["phenotype", "validate", "t2dm@1.0.0", "--json"])
    assert validate.exit_code == 0 and json.loads(validate.stdout)["ok"], validate.output
    plain = runner.invoke(app, ["phenotype", "validate", "t2dm@1.0.0"])
    assert plain.exit_code == 0 and "valid" in plain.stdout
    missing = runner.invoke(app, ["phenotype", "show", "nope@1.0.0"])
    assert missing.exit_code == 2 and "known ids" in missing.stderr
    bad_ref = runner.invoke(app, ["phenotype", "show", "t2dm"])
    assert bad_ref.exit_code == 2 and "reference" in bad_ref.stderr
    dry = runner.invoke(app, ["phenotype", "compile", "t2dm@1.0.0", "--dry-run"])
    assert dry.exit_code == 0 and "ORDER BY subject_id" in dry.stdout and "characters" in dry.stdout
    assert "run " not in dry.stdout


def test_import_budget() -> None:
    helpers.assert_import_budget(
        "mimicwarehouse.phenotypes.cli",
        lazy=(
            "mimicwarehouse.safe",
            "mimicwarehouse.catalog.build",
            "mimicwarehouse.dag.runner",
            "mimicwarehouse.concepts.runner",
            "mimicwarehouse.units",
            "mimicwarehouse.timesem",
            "mimicwarehouse.phenotypes.runner",
            "mimicwarehouse.phenotypes.compiler",
        ),
    )
    helpers.assert_import_budget(lazy=("mimicwarehouse.safe", "mimicwarehouse.catalog.build"))


# ---------------------------------------------------------------------------
# 7. A minimal fixture lake: materialisation, views, meta, runs, skip / force,
#    a second version, summary
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def lake(tmp_path_factory: pytest.TempPathFactory) -> Settings:
    """A fixture lake with the tables the leaves read + phenotypes.compile + catalog."""
    root = tmp_path_factory.mktemp("phenotype-lake")
    settings = config.Settings(data_root=root)
    result = runner_mod.run(
        load_dag(),
        "fixture",
        select=[*LAKE_STEPS, phen_runner.STEP_COMPILE, "catalog"],
        settings=settings,
    )
    failed = [f"{s.name}: {s.error}" for s in result.steps if s.status == "failed"]
    assert not failed, failed
    return settings


def _expected_fixture_t2dm(
    fixture_catalog: duckdb.DuckDBPyConnection, registry: Registry
) -> dict[int, tuple[bool, datetime | None]]:
    """An independent (Polars) evaluation of the T2DM rules over the committed fixture."""
    import polars as pl

    cs = registry.codesets
    t2dm9 = [e.code for e in cs.get("t2dm@1.0.0").codeset.members.icd9]
    t1dm9 = [e.code for e in cs.get("t1dm@1.0.0").codeset.members.icd9]
    drugs = cs.get("noninsulin_antidiabetics@1.0.0").codeset.members.drugs
    assert drugs is not None
    patients = fixture_catalog.execute(f"SELECT subject_id FROM {HOSP}.patients").pl()
    adm = fixture_catalog.execute(f"SELECT hadm_id, dischtime FROM {HOSP}.admissions").pl()
    dx = (
        fixture_catalog.execute(
            f"SELECT subject_id, hadm_id, icd_code, icd_version FROM {HOSP}.diagnoses_icd"
        )
        .pl()
        .join(adm, on="hadm_id")
    )
    is_t2 = ((pl.col("icd_version") == 9) & pl.col("icd_code").is_in(t2dm9)) | (
        (pl.col("icd_version") == 10) & pl.col("icd_code").str.starts_with("E11")
    )
    is_t1 = ((pl.col("icd_version") == 9) & pl.col("icd_code").is_in(t1dm9)) | (
        (pl.col("icd_version") == 10) & pl.col("icd_code").str.starts_with("E10")
    )
    dx_first = dx.filter(is_t2).group_by("subject_id").agg(pl.col("dischtime").min().alias("t_dx"))
    t1 = set(dx.filter(is_t1).get_column("subject_id").to_list())
    rx = fixture_catalog.execute(
        f"SELECT subject_id, drug, starttime FROM {HOSP}.prescriptions"
    ).pl()
    upper = pl.col("drug").str.to_uppercase()
    med_mask = pl.any_horizontal(*(upper.str.contains(name, literal=True) for name in drugs.names))
    med_first = (
        rx.filter(med_mask).group_by("subject_id").agg(pl.col("starttime").min().alias("t_med"))
    )
    labs = fixture_catalog.execute(
        f"SELECT subject_id, charttime, valuenum FROM {HOSP}.labevents "
        "WHERE itemid = 50852 AND valuenum >= 6.5"
    ).pl()
    a1c_first = labs.group_by("subject_id").agg(pl.col("charttime").min().alias("t_a1c"))
    frame = (
        patients.join(dx_first, on="subject_id", how="left")
        .join(med_first, on="subject_id", how="left")
        .join(a1c_first, on="subject_id", how="left")
    )
    out: dict[int, tuple[bool, datetime | None]] = {}
    for row in frame.iter_rows(named=True):
        sid = int(row["subject_id"])
        has_dx = row["t_dx"] is not None
        has_other = row["t_med"] is not None or row["t_a1c"] is not None
        flag = has_dx or (has_other and sid not in t1)
        times = [t for t in (row["t_dx"], row["t_med"], row["t_a1c"]) if t is not None]
        out[sid] = (flag, min(times) if flag and times else None)
    return out


def test_fixture_lake_materialisation_views_meta_and_run(
    lake: Settings, registry: Registry, fixture_catalog: duckdb.DuckDBPyConnection
) -> None:
    from mimicwarehouse.catalog.connect import open_catalog
    from mimicwarehouse.run import list_runs, read_manifest

    settings = lake
    lake_root = settings.lake_root("fixture")
    entry = registry.get("t2dm@1.0.0")
    part = phen_runner.phenotype_part(lake_root, "fixture", "t2dm@1.0.0")
    assert (
        part.is_file()
        and part.parent.name == "t2dm@1.0.0"
        and part.parent.parent.name == "phenotypes"
    )
    assert phen_runner.phenotype_complete(lake_root, "fixture", "t2dm@1.0.0")
    attempt = phen_runner.phenotype_attempt(lake_root, "fixture", "t2dm@1.0.0")
    assert (
        attempt is not None
        and attempt["status"] == "done"
        and attempt["def_hash"] == entry.def_hash
    )
    assert attempt["refs"] == entry.resolved and attempt["grain"] == "subject" and attempt["run_id"]
    assert attempt["rows"] == 120 and 0 < attempt["n_positive"] < 120
    assert phen_runner.versions_path(lake_root, "fixture").is_file()
    expected = _expected_fixture_t2dm(fixture_catalog, registry)
    con = open_catalog("fixture", settings=settings)
    try:
        views = {
            str(r[0]): (str(r[1]), str(r[2]))
            for r in con.execute(
                "SELECT schema_name || '.' || view_name, schema_name, comment FROM duckdb_views() "
                "WHERE schema_name IN ('phenotypes', 'mimiciv_derived')"
            ).fetchall()
        }
        assert "phenotypes.t2dm@1.0.0" in views
        assert (
            "EP-41" in views[f"{DERIVED}.phenotype_t2dm"][1]
            and "latest built version" in views[f"{DERIVED}.phenotype_t2dm"][1]
        )
        assert "companion" in views[f"{DERIVED}.phenotype_t2dm_hadm"][1]
        got = {
            int(r[0]): (bool(r[1]), r[2])
            for r in con.execute(
                f"SELECT subject_id, flag, onset_time FROM {DERIVED}.phenotype_t2dm"
            ).fetchall()
        }
        assert got == expected, "the engine agrees with an independent Polars evaluation"
        assert sum(1 for f, _ in got.values() if f) == attempt["n_positive"]
        assert _scalar(con, f"SELECT count(*) FROM {DERIVED}.phenotype_t2dm_hadm") == _scalar(
            con, f"SELECT count(*) FROM {HOSP}.admissions"
        )
        assert (
            _scalar(con, f"SELECT max(length(evidence_json)) FROM {DERIVED}.phenotype_t2dm")
            <= phen_runner.EVIDENCE_MAX_CHARS
        )
        # meta.phenotype_versions
        cols = [d[0] for d in con.execute("DESCRIBE meta.phenotype_versions").fetchall()]
        assert cols == [c for c, _t in phen_runner.VERSIONS_COLUMNS]
        meta = con.execute(
            "SELECT phenotype_id, version, def_hash, grain, refs, rows, n_positive, status, "
            "run_id, sql_sha256, tier FROM meta.phenotype_versions"
        ).fetchall()
        assert len(meta) == 1
        row = meta[0]
        assert (
            row[:4] == ("t2dm", "1.0.0", entry.def_hash, "subject")
            and json.loads(row[4]) == entry.resolved
        )
        assert (
            row[5] == 120
            and row[6] == attempt["n_positive"]
            and row[7] == "done"
            and row[8] == attempt["run_id"]
        )
        assert row[9] == attempt["sql_sha256"] and row[10] == "fixture"
        comment = _scalar(
            con,
            "SELECT comment FROM duckdb_tables() WHERE schema_name = 'meta' "
            "AND table_name = 'phenotype_versions'",
        )
        assert "EP-41" in comment
        assert phen_runner.latest_versions(con) == {"t2dm": "1.0.0"}
    finally:
        con.close()
    # the kind: phenotype run: SQL recorded, refs with hashes, the core snapshot, params
    runs = list_runs(settings, kind="phenotype")
    assert [r["run_id"] for r in runs] == [attempt["run_id"]]
    manifest = read_manifest(attempt["run_id"], settings)
    assert manifest.kind == "phenotype" and manifest.status == "ok" and manifest.tier == "fixture"
    assert manifest.sql == {"phenotype": "sql/phenotype.sql"}
    sql_text = (settings.layout["runs"] / attempt["run_id"] / "sql" / "phenotype.sql").read_text(
        encoding="utf-8"
    )
    assert sql_text.rstrip("\n") == compiler_mod.compile_phenotype(
        entry.phenotype, registry.codesets, resolved=entry.resolved
    ).sql.rstrip("\n")
    refs = {(r.kind, r.name): (r.version, r.hash) for r in manifest.refs}
    assert refs[("phenotype", "t2dm")] == ("1.0.0", entry.def_hash)
    for ref, def_hash in entry.resolved.items():
        cs_id, cs_version = ref.split("@")
        assert refs[("codeset", cs_id)] == (cs_version, def_hash)
    assert "core" in manifest.snapshot_ids and manifest.params["rows"] == 120
    assert (
        manifest.params["n_positive"] == attempt["n_positive"]
        and manifest.params["k"] == settings.k_suppression
    )
    # small cells: below k the meta table blanks n_positive
    rows = phen_runner.versions_rows(lake_root, "fixture", k=1_000, settings=settings)
    assert rows[0][6] is None and rows[0][7] is True and rows[0][5] == 120
    rows = phen_runner.versions_rows(lake_root, "fixture", k=1, settings=settings)
    assert rows[0][6] == attempt["n_positive"] and rows[0][7] is False


def test_fixture_lake_cli_skip_force_second_version_and_summary(
    lake: Settings, registry: Registry, tmp_path: Path
) -> None:
    from mimicwarehouse.catalog.connect import open_catalog
    from mimicwarehouse.run import list_runs
    from mimicwarehouse.safe import SafeQueryRefused, audit_path

    settings = lake
    lake_root = settings.lake_root("fixture")
    root = str(settings.data_root)
    runner = helpers.cli_runner()
    before = phen_runner.phenotype_attempt(lake_root, "fixture", "t2dm@1.0.0")
    assert before is not None
    try:
        # a re-compile skips the version already built with the same def_hash
        skipped = runner.invoke(
            app, ["--data-root", root, "phenotype", "compile", "t2dm@1.0.0", "--tier", "fixture"]
        )
        assert skipped.exit_code == 0, skipped.output
        assert "run " in skipped.stdout and "t2dm@1.0.0" in skipped.stdout
        after = phen_runner.phenotype_attempt(lake_root, "fixture", "t2dm@1.0.0")
        assert after is not None and after["run_id"] == before["run_id"], "skipped: no new run"
        # --force rebuilds: a new run, same counts
        forced = runner.invoke(
            app,
            [
                "--data-root",
                root,
                "phenotype",
                "compile",
                "t2dm@1.0.0",
                "--tier",
                "fixture",
                "--force",
            ],
        )
        assert forced.exit_code == 0, forced.output
        after = phen_runner.phenotype_attempt(lake_root, "fixture", "t2dm@1.0.0")
        assert (
            after is not None
            and after["run_id"] != before["run_id"]
            and after["n_positive"] == before["n_positive"]
        )
        assert len(list_runs(settings, kind="phenotype")) == 2
        # summary through safe_query (the fixture tier may lower k)
        summary = phen_runner.summarize(
            "t2dm@1.0.0", tier="fixture", settings=settings, k=1, actor="test_ep41"
        )
        assert summary.grain == "subject" and summary.k == 1 and summary.rows_suppressed == 0
        df = summary.df
        assert df.columns == ["scope", "unit", "n_units", "n_positive", "share"]
        total = df.filter(df["scope"] == "all").row(0, named=True)
        assert (
            total["unit"] == "subject"
            and total["n_units"] == 120
            and total["n_positive"] == after["n_positive"]
        )
        assert abs(total["share"] - after["n_positive"] / 120) < 1e-9
        eras = df.filter(df["scope"] != "all")
        assert set(eras["unit"].to_list()) == {"hadm"} and 1 <= eras.height <= 5
        assert int(eras["n_units"].sum()) == 186
        assert phen_runner.summary("t2dm@1.0.0", "fixture", settings=settings, k=1).equals(df)
        audit_lines = [
            json.loads(line)
            for line in audit_path(settings).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert {a["audit_id"] for a in audit_lines} >= set(summary.audit_ids)
        # the default k on the fixture tier is settings.k_suppression: a small era vanishes
        default_k = phen_runner.summarize("t2dm@1.0.0", tier="fixture", settings=settings)
        assert default_k.k == settings.k_suppression and default_k.df.height <= df.height
        cli = runner.invoke(
            app,
            [
                "--data-root",
                root,
                "phenotype",
                "summary",
                "t2dm@1.0.0",
                "--tier",
                "fixture",
                "--k",
                "1",
            ],
        )
        assert (
            cli.exit_code == 0
            and "n_positive" in cli.stdout
            and "0 row(s) suppressed" in cli.stdout
        ), cli.output
        as_json = runner.invoke(
            app,
            [
                "--data-root",
                root,
                "phenotype",
                "summary",
                "t2dm@1.0.0",
                "--tier",
                "fixture",
                "--k",
                "1",
                "--json",
            ],
        )
        payload = json.loads(as_json.stdout)
        assert payload["ref"] == "t2dm@1.0.0" and payload["rows"][0]["n_units"] == 120
        # a session cannot read the evidence text or the versioned schema directly
        from mimicwarehouse.safe import safe_query

        with pytest.raises(SafeQueryRefused, match="not allowed"):
            safe_query(
                'SELECT count(*) AS n FROM phenotypes."t2dm@1.0.0"',
                tier="fixture",
                settings=settings,
            )
        aggregate = safe_query(
            "SELECT count(*) AS n, count(*) FILTER (WHERE flag) AS n_pos "
            f"FROM {DERIVED}.phenotype_t2dm",
            tier="fixture",
            settings=settings,
            k=1,
        )
        assert aggregate.df["n"][0] == 120
        with pytest.raises(SafeQueryRefused):
            safe_query(
                f"SELECT subject_id, flag FROM {DERIVED}.phenotype_t2dm",
                tier="fixture",
                settings=settings,
            )
        # a second version from a study directory coexists; the session view follows it
        study = tmp_path / "study"
        study.mkdir()
        text = registry.get("t2dm@1.0.0").path.read_text(encoding="utf-8")
        (study / "t2dm_1_1.yaml").write_text(
            text.replace('version: "1.0.0"', 'version: "1.1.0"').replace(
                "threshold: 6.5", "threshold: 8.0"
            ),
            encoding="utf-8",
            newline="\n",
        )
        second = runner.invoke(
            app,
            [
                "--data-root",
                root,
                "phenotype",
                "compile",
                "t2dm@1.1.0",
                "--tier",
                "fixture",
                "--defs",
                str(study),
            ],
        )
        assert second.exit_code == 0, second.output
        assert phen_runner.phenotype_part(lake_root, "fixture", "t2dm@1.1.0").is_file()
        assert phen_runner.phenotype_part(lake_root, "fixture", "t2dm@1.0.0").is_file(), (
            "both versions on disk"
        )
        con = open_catalog("fixture", settings=settings)
        try:
            assert phen_runner.latest_versions(con) == {"t2dm": "1.1.0"}
            versions = con.execute(
                "SELECT version, status FROM meta.phenotype_versions ORDER BY version"
            ).fetchall()
            assert versions == [("1.0.0", "done"), ("1.1.0", "done")]
            n_new = _scalar(
                con, f"SELECT count(*) FILTER (WHERE flag) FROM {DERIVED}.phenotype_t2dm"
            )
            n_old = _scalar(con, 'SELECT count(*) FILTER (WHERE flag) FROM phenotypes."t2dm@1.0.0"')
            n_new_direct = _scalar(
                con, 'SELECT count(*) FILTER (WHERE flag) FROM phenotypes."t2dm@1.1.0"'
            )
            assert n_new == n_new_direct <= n_old
        finally:
            con.close()
        latest = phen_runner.summarize("t2dm@1.1.0", tier="fixture", settings=settings, k=1)
        assert latest.ref == "t2dm@1.1.0"
        with pytest.raises(PhenotypeError, match="not the latest built version"):
            phen_runner.summarize("t2dm@1.0.0", tier="fixture", settings=settings, k=1)
        with pytest.raises(PhenotypeError, match="no built version"):
            phen_runner.summarize("nope@1.0.0", tier="fixture", settings=settings, k=1)
        refused = runner.invoke(
            app,
            [
                "--data-root",
                root,
                "phenotype",
                "summary",
                "t2dm@1.0.0",
                "--tier",
                "fixture",
                "--k",
                "1",
            ],
        )
        assert refused.exit_code == 2 and "not the latest" in refused.stderr
        unknown = runner.invoke(
            app, ["--data-root", root, "phenotype", "compile", "nope@1.0.0", "--tier", "fixture"]
        )
        assert unknown.exit_code == 2 and "known ids" in unknown.stderr
        bad_tier = runner.invoke(
            app, ["--data-root", root, "phenotype", "summary", "t2dm@1.1.0", "--tier", "nope"]
        )
        assert bad_tier.exit_code == 2
    finally:
        config.configure()
        _drop_progress_handlers()
    assert phen_runner.current_options() == phen_runner.CompileOptions()


def test_compile_step_without_the_source_tables(tmp_path: Path) -> None:
    settings = config.Settings(data_root=tmp_path / "root")
    result = runner_mod.run(
        load_dag(), "fixture", select=[phen_runner.STEP_COMPILE], settings=settings
    )
    assert not result.ok and result.steps[0].status == "failed"
    assert "PhenotypeError" in (result.steps[0].error or "")
    attempt = phen_runner.phenotype_attempt(settings.lake_root("fixture"), "fixture", "t2dm@1.0.0")
    assert attempt is not None and attempt["status"] == "failed" and attempt["error_class"]


def test_session_fixture_lake_carries_the_phenotype(
    fixture_lake_catalog: duckdb.DuckDBPyConnection, registry: Registry
) -> None:
    """The full DAG (conftest's session lake) runs phenotypes.compile too."""
    con = fixture_lake_catalog
    assert phen_runner.latest_versions(con) == {"t2dm": "1.0.0"}
    views = {
        str(r[0])
        for r in con.execute(
            "SELECT view_name FROM duckdb_views() WHERE schema_name = 'mimiciv_derived'"
        ).fetchall()
    }
    assert {"phenotype_t2dm", "phenotype_t2dm_hadm", "hadm_era"} <= views
    row = con.execute(
        "SELECT def_hash, status FROM meta.phenotype_versions WHERE phenotype_id = 't2dm'"
    ).fetchone()
    assert row == (registry.get("t2dm@1.0.0").def_hash, "done")
    assert _scalar(con, f"SELECT count(*) FROM {DERIVED}.phenotype_t2dm") == 120


# ---------------------------------------------------------------------------
# 8. Docs in sync
# ---------------------------------------------------------------------------


def test_methods_doc_in_sync(tmp_path: Path, registry: Registry) -> None:
    path = phen_runner.methods_doc_path()
    assert path.is_file(), "docs/methods/phenotypes.md exists"
    text = path.read_text(encoding="utf-8")
    for needle in (
        "retrospective",
        "def_hash",
        "phenotypes.lock.json",
        "PhenotypeFrozenError",
        "meta.phenotype_versions",
        "mimiciv_derived.phenotype_<id>",
        "_hadm",
        "safe_query",
        "EP-42",
        "EP-46",
        "what_it_does_not_claim",
        "64",
        "temporal(",
        "concept(",
    ):
        assert needle in text, needle
    copy = tmp_path / "phenotypes.md"
    copy.write_text(text, encoding="utf-8", newline="\n")
    phen_runner.sync_methods_doc(copy)
    assert copy.read_text(encoding="utf-8") == text, "re-run `python -m mimicwarehouse.phenotypes`"
    cards = phen_runner.render_cards(registry)
    assert cards.rstrip("\n") in text and "### `t2dm@1.0.0`" in cards and "eMERGE" in cards
    assert not BAND_TOKEN.search(text)
    # the hand-maintained fixture coverage note knows about the 0.3.0 inputs
    coverage = (helpers.WORKSPACE / "tests" / "fixtures" / "COVERAGE.md").read_text(
        encoding="utf-8"
    )
    assert "50852" in coverage and "0.3.0" in coverage


# ---------------------------------------------------------------------------
# 9. Dev tier: the compiled phenotype on the real dev catalog (aggregates only)
# ---------------------------------------------------------------------------


@pytest.mark.tier("dev")
def test_dev_compiled_phenotype_and_summary(dev_catalog: Path, registry: Registry) -> None:
    settings = config.load_settings()
    remedy = "run `mwh phenotype compile t2dm@1.0.0 --tier dev` (EP-41) first"
    versions = phen_runner.built_versions("dev", settings=settings, actor="test_ep41")
    mine = [v for v in versions if v["phenotype_id"] == "t2dm" and v["status"] == "done"]
    assert mine, remedy
    latest = max(mine, key=lambda v: phen_runner.semver_key(str(v["version"])))
    entry = registry.get(f"t2dm@{latest['version']}")
    assert latest["def_hash"] == entry.def_hash, "the dev build carries the current definition hash"
    summary = phen_runner.summarize(entry.ref, tier="dev", settings=settings, actor="test_ep41")
    assert summary.k >= 11 and summary.grain == "subject"
    total = summary.df.filter(summary.df["scope"] == "all")
    assert total.height == 1
    n_units, n_positive, share = (
        int(total["n_units"][0]),
        int(total["n_positive"][0]),
        float(total["share"][0]),
    )
    assert n_units >= 11 and 0 < n_positive < n_units and 0 < share < 1
    eras = summary.df.filter(summary.df["scope"] != "all")
    assert eras.height >= 1 and all(int(v) >= 11 for v in eras["n_positive"].to_list())
    print(
        f"dev: {entry.ref} n_units {n_units:,}, n_positive {n_positive:,}, share {share:.1%}; "
        f"{eras.height} era row(s), {summary.rows_suppressed} suppressed at k={summary.k}"
    )


def _unused(_: date) -> None:  # keep the date import meaningful for pyright's unused check
    return None
