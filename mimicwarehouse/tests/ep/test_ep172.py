"""EP-172 — GEM review adjudication: t2dm + sepsis_explicit (owner-supervised).

Fixture tier (default): every packaged code-set and phenotype pair is locked (``mwh codeset
lock --check`` / ``mwh phenotype lock --check`` exit 0); the reviewed set
``sepsis_explicit@1.1.0`` is a superset of its 1.0.0 members with a different ``def_hash``
while the 1.0.0 pairs keep their recorded hashes; ``t2dm`` has no 1.1.0 (the review rejected
every proposal — the "all rejected" outcome EP-46's gate accepts); the bumped phenotype
``sepsis_explicit@1.1.0`` references the new pair, keeps the 1.0.0 criteria and compiles to
its committed golden SQL (the 1.0.0 golden still holds); the decision ledger in the brief's
completion note lists every proposed code of the two pasted review files exactly once with a
ruling and a reason, its accepted rows are exactly the codes 1.1.0 added, its rejected rows
stay out; the methods pages and the decision / README records carry the new version.
``tier("dev")``: ``meta.codesets`` and ``meta.phenotype_versions`` carry the new pairs with
the current hashes (through ``safe_query``).

Everything asserted or printed is code-set text, public vocabulary titles, SQL and
registry hashes — never a patient-level row.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

import helpers
from mimicwarehouse.cli import app
from mimicwarehouse.codesets import registry as codesets_registry
from mimicwarehouse.phenotypes import compiler as compiler_mod
from mimicwarehouse.phenotypes import registry as phenotypes_registry
from mimicwarehouse.phenotypes import runner as phen_runner

if TYPE_CHECKING:
    from mimicwarehouse.codesets.registry import Registry as CodeSetRegistry
    from mimicwarehouse.phenotypes.registry import Registry as PhenotypeRegistry

pytestmark = pytest.mark.ep_172

BAND_TOKEN = re.compile(r"(?<![\w.])[123]\d{7}(?![\w.])")
GOLDEN = helpers.WORKSPACE / "tests" / "ep" / "golden"
BRIEF = helpers.WORKSPACE.parent / "roadmap" / "EP-172-gem-review-adjudication.md"
DECISIONS = helpers.WORKSPACE / "DECISIONS.md"
README = helpers.WORKSPACE / "README.md"
OLD = "sepsis_explicit@1.0.0"
NEW = "sepsis_explicit@1.1.0"
#: The two pasted review files' proposal counts, per (set, direction) — the ledger must
#: carry exactly these many rows.
PROPOSED: dict[tuple[str, str], int] = {
    ("t2dm", "icd9->icd10"): 1,
    ("t2dm", "icd10->icd9"): 19,
    ("sepsis_explicit", "icd9->icd10"): 4,
    ("sepsis_explicit", "icd10->icd9"): 10,
}
#: The owner's accepted codes (2026-09-16), per system — what 1.1.0 adds over 1.0.0.
ACCEPTED: dict[str, frozenset[str]] = {
    "icd9": frozenset({"0031", "1125", "67022", "67024", "99802"}),
    "icd10": frozenset({"A207", "A394", "B007", "I76"}),
}
REJECTED_SEPSIS: frozenset[str] = frozenset({"0270", "0271", "09889", "9093", "V5889"})
RULINGS = ("accept", "reject", "defer")
LEDGER_MARK = "**Decision ledger"
LEDGER_COLUMNS = ("set", "direction", "code", "title", "GEM flags", "ruling", "reason")


@pytest.fixture(scope="module")
def codesets() -> CodeSetRegistry:
    return codesets_registry.load_registry()


@pytest.fixture(scope="module")
def phenotypes() -> PhenotypeRegistry:
    return phenotypes_registry.load_registry()


def _exact_codes(codeset: Any, system: str) -> set[str]:
    return {e.code for e in getattr(codeset.members, system) if e.match == "exact"}


def _prefixes(codeset: Any, system: str) -> set[str]:
    return {e.code for e in getattr(codeset.members, system) if e.match == "prefix"}


# ---------------------------------------------------------------------------
# 1. Every packaged pair is locked; the registries carry the reviewed versions
# ---------------------------------------------------------------------------


def test_every_packaged_pair_is_locked(
    codesets: CodeSetRegistry, phenotypes: PhenotypeRegistry
) -> None:
    runner = helpers.cli_runner()
    for group in ("codeset", "phenotype"):
        check = runner.invoke(app, [group, "lock", "--check"])
        assert check.exit_code == 0 and "0 unlocked" in check.stdout, check.output
    assert all(e.locked and e.packaged for e in codesets), [e.ref for e in codesets if not e.locked]
    assert all(e.locked and e.packaged for e in phenotypes)
    assert codesets.versions_of("sepsis_explicit") == ("1.0.0", "1.1.0")
    assert phenotypes.versions_of("sepsis_explicit") == ("1.0.0", "1.1.0")
    # the t2dm review rejected every proposal: no new version, the 1.0.0 pairs untouched
    assert codesets.versions_of("t2dm") == ("1.0.0",)
    assert phenotypes.versions_of("t2dm") == ("1.0.0",)
    cs_lock = codesets_registry.load_lock(codesets_registry.packaged_defs_dir())
    assert cs_lock.codesets["t2dm@1.0.0"].def_hash.startswith("780eafccc3a8")
    assert cs_lock.codesets[OLD].def_hash.startswith("5d172007c621")
    assert cs_lock.codesets[NEW].locked_at == "2026-09-16"
    ph_lock = phenotypes_registry.load_lock(phenotypes_registry.packaged_defs_dir())
    assert ph_lock.phenotypes[OLD].def_hash.startswith("edcd2302f046")
    assert ph_lock.phenotypes["t2dm@1.0.0"].def_hash.startswith("f0511c05bf8b")
    assert ph_lock.phenotypes[NEW].grain == "hadm" and ph_lock.phenotypes[NEW].locked_at == (
        "2026-09-16"
    )
    for entry in (codesets.get(NEW), phenotypes.get(NEW)):
        raw = entry.path.read_bytes()
        assert entry.display_path == "defs/sepsis_explicit_1_1_0.yaml"
        assert b"\r" not in raw and raw.endswith(b"\n") and raw.decode("utf-8").isascii()
        assert not BAND_TOKEN.search(raw.decode("utf-8"))


# ---------------------------------------------------------------------------
# 2. The reviewed code set: a superset of 1.0.0 with a new hash
# ---------------------------------------------------------------------------


def test_new_codeset_is_a_superset_with_a_new_hash(codesets: CodeSetRegistry) -> None:
    old = codesets.get(OLD).codeset
    new = codesets.get(NEW).codeset
    assert new.def_hash != old.def_hash and new.kind == old.kind == "icd_dx"
    assert new.def_hash.startswith("c9aed261d647")
    for system in ("icd9", "icd10"):
        assert _prefixes(new, system) == _prefixes(old, system), system
        assert _exact_codes(new, system) >= _exact_codes(old, system), system
        assert _exact_codes(new, system) - _exact_codes(old, system) == ACCEPTED[system], system
    assert new.n_declared == old.n_declared + sum(len(v) for v in ACCEPTED.values())
    assert not (REJECTED_SEPSIS & (_exact_codes(new, "icd9") | _exact_codes(new, "icd10")))
    assert new.provenance.source == old.provenance.source == "hand"
    assert new.provenance.accessed == "2026-09-16" and new.references == old.references
    assert "EP-172" in new.notes and "1.0.0" in new.notes
    for reference in new.references:
        assert reference.startswith("https://doi.org/")
    # the release note names every accepted code and every rejected one, dotted
    for dotted in ("003.1", "112.5", "670.22", "670.24", "998.02", "A20.7", "A39.4", "B00.7"):
        assert dotted in new.description or dotted in new.notes, dotted
    for dotted in ("027.0", "027.1", "098.89", "909.3", "V58.89"):
        assert dotted in new.notes, dotted
    runner = helpers.cli_runner()
    shown = runner.invoke(app, ["codeset", "show", NEW])
    assert shown.exit_code == 0 and "locked yes" in shown.stdout and "I76" in shown.stdout


# ---------------------------------------------------------------------------
# 3. The bumped phenotype: references the new pair, compiles to its golden SQL
# ---------------------------------------------------------------------------


def test_new_phenotype_references_new_pair_and_matches_golden(
    phenotypes: PhenotypeRegistry,
) -> None:
    old = phenotypes.get(OLD)
    new = phenotypes.get(NEW)
    assert new.phenotype.codeset_refs == (NEW,) and new.phenotype.references == (NEW,)
    assert new.resolved == {NEW: phenotypes.codesets.get(NEW).codeset.def_hash}
    assert new.def_hash != old.def_hash and new.def_hash.startswith("034f3d6bd7e2")
    # only the reference moved: the tree, onset, grain and outputs are the 1.0.0 ones
    assert new.phenotype.criteria.render() == old.phenotype.criteria.render() == "dx"
    assert new.phenotype.grain == old.phenotype.grain == "hadm"
    assert new.phenotype.onset.canonical() == old.phenotype.onset.canonical()
    assert new.phenotype.outputs == old.phenotype.outputs
    assert not new.concepts and new.phenotype.parameters == {}
    assert len(new.phenotype.what_it_does_not_claim) >= 3 and new.phenotype.citations
    validation = phenotypes_registry.validate(new, phenotypes.codesets)
    assert validation.ok and not validation.warnings
    for entry in (old, new):
        compiled = compiler_mod.compile_phenotype(
            entry.phenotype, phenotypes.codesets, resolved=entry.resolved
        )
        golden = GOLDEN / f"{entry.ref}.sql"
        assert golden.is_file(), golden
        assert golden.read_text(encoding="utf-8") == compiled.sql.rstrip("\n") + "\n", (
            f"the compiled SQL drifted from {golden.name}"
        )
        assert compiled.sources == ("mimiciv_hosp.admissions", "mimiciv_hosp.diagnoses_icd")
        assert not BAND_TOKEN.search(compiled.sql)
    new_sql = (GOLDEN / f"{NEW}.sql").read_text(encoding="utf-8")
    old_sql = (GOLDEN / f"{OLD}.sql").read_text(encoding="utf-8")
    assert f"references: {NEW}=c9aed261d647" in new_sql
    for code in ACCEPTED["icd9"] | ACCEPTED["icd10"]:
        assert f"'{code}'" in new_sql and f"'{code}'" not in old_sql, code
    for code in REJECTED_SEPSIS:
        assert f"'{code}'" not in new_sql, code
    runner = helpers.cli_runner()
    listing = runner.invoke(app, ["phenotype", "list"])
    assert listing.exit_code == 0 and NEW in listing.stdout and "all locked" in listing.stdout


# ---------------------------------------------------------------------------
# 4. The decision ledger: every proposed code exactly once, rulings applied
# ---------------------------------------------------------------------------


def parse_ledger(text: str) -> list[dict[str, str]]:
    """The rows of the completion note's decision ledger (a blockquoted Markdown table
    after the ``**Decision ledger`` marker) as ``{column: cell}`` dicts."""
    start = text.find(LEDGER_MARK)
    assert start >= 0, "the brief carries no decision ledger"
    rows: list[dict[str, str]] = []
    header: list[str] | None = None
    for line in text[start:].splitlines():
        stripped = line.lstrip("> ").rstrip()
        if not stripped.startswith("|"):
            if header is not None:
                break
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if header is None:
            header = cells
            continue
        if all(set(c) <= set("-: ") for c in cells):
            continue
        assert len(cells) == len(header), stripped
        rows.append(dict(zip(header, cells, strict=True)))
    assert header is not None and tuple(header) == LEDGER_COLUMNS, header
    return rows


def test_ledger_lists_every_proposed_code_exactly_once(codesets: CodeSetRegistry) -> None:
    text = BRIEF.read_text(encoding="utf-8")
    assert "Completion note" in text
    rows = parse_ledger(text)
    assert len(rows) == sum(PROPOSED.values()) == 34
    keys = [(r["set"], r["direction"], r["code"].strip("`")) for r in rows]
    assert len(set(keys)) == len(keys), "a code is ruled twice"
    counts: dict[tuple[str, str], int] = {}
    for set_id, direction, _code in keys:
        counts[(set_id, direction)] = counts.get((set_id, direction), 0) + 1
    assert counts == PROPOSED
    for row in rows:
        assert row["ruling"] in RULINGS, row
        assert row["title"] and row["reason"] and row["GEM flags"], row
        code = row["code"].strip("`")
        assert re.fullmatch(r"[A-Z0-9]+", code), code
        assert not BAND_TOKEN.search(code)
        # the direction names the proposed code's system (ICD-10-CM codes start with a
        # letter; the proposed ICD-9-CM codes with a digit or the V of a V-code)
        assert row["direction"] in ("icd9->icd10", "icd10->icd9"), row
        if row["direction"] == "icd9->icd10":
            assert code[0].isalpha() and code[0] != "V", code
        else:
            assert code[0].isdigit() or code[0] == "V", code
    accepted = {(r["set"], r["code"].strip("`")) for r in rows if r["ruling"] == "accept"}
    assert {c for s, c in accepted if s == "t2dm"} == set(), "t2dm: all rejected"
    assert {c for s, c in accepted if s == "sepsis_explicit"} == ACCEPTED["icd9"] | ACCEPTED[
        "icd10"
    ]
    rejected = {r["code"].strip("`") for r in rows if r["ruling"] == "reject"}
    assert rejected >= REJECTED_SEPSIS and "E1310" in rejected and "3572" in rejected
    assert not any(r["ruling"] == "defer" for r in rows), "nothing was deferred"
    # the accepted rows are exactly what 1.1.0 added; the rejected ones stay out
    new = codesets.get(NEW).codeset
    old = codesets.get(OLD).codeset
    added = (_exact_codes(new, "icd9") | _exact_codes(new, "icd10")) - (
        _exact_codes(old, "icd9") | _exact_codes(old, "icd10")
    )
    assert added == {c for s, c in accepted if s == "sepsis_explicit"}
    members = _exact_codes(new, "icd9") | _exact_codes(new, "icd10")
    assert not (rejected & members)
    t2dm = codesets.get("t2dm@1.0.0").codeset
    assert not (rejected & (_exact_codes(t2dm, "icd9") | _exact_codes(t2dm, "icd10")))
    assert "all rejected" in text and "EP-46" in text


# ---------------------------------------------------------------------------
# 5. Docs and records in sync
# ---------------------------------------------------------------------------


def test_docs_and_records_in_sync(tmp_path: Path) -> None:
    for module, path in (
        (codesets_registry, codesets_registry.methods_doc_path()),
        (phen_runner, phen_runner.methods_doc_path()),
    ):
        text = path.read_text(encoding="utf-8")
        assert NEW in text and "EP-172" in text, path.name
        copy = tmp_path / path.name
        copy.write_text(text, encoding="utf-8", newline="\n")
        module.sync_methods_doc(copy)
        assert copy.read_text(encoding="utf-8") == text, f"re-render {path.name}"
        assert not BAND_TOKEN.search(text)
    codesets_doc = codesets_registry.methods_doc_path().read_text(encoding="utf-8")
    assert f"| `{NEW}` |" in codesets_doc and f"| `{OLD}` |" in codesets_doc
    assert "manifestation" in codesets_doc
    phenotypes_doc = phen_runner.methods_doc_path().read_text(encoding="utf-8")
    assert f"### `{NEW}`" in phenotypes_doc and f"### `{OLD}`" in phenotypes_doc
    decisions = DECISIONS.read_text(encoding="utf-8")
    d35 = decisions[decisions.find("**D-35 Vocabularies") :]
    d35 = d35[: d35.find("**D-36")]
    assert "EP-172" in d35 and "manifestation" in d35 and NEW in d35
    readme = README.read_text(encoding="utf-8")
    assert NEW in readme and "EP-172" in readme


# ---------------------------------------------------------------------------
# 6. Dev tier: the catalog carries the new pairs
# ---------------------------------------------------------------------------


@pytest.mark.tier("dev")
def test_dev_catalog_carries_the_new_pairs(
    dev_catalog: Path, codesets: CodeSetRegistry, phenotypes: PhenotypeRegistry
) -> None:
    from mimicwarehouse import config
    from mimicwarehouse.safe import safe_query

    settings = config.load_settings()
    remedy = (
        f"run `mwh codeset compile --tier dev {NEW}` and `mwh phenotype compile {NEW} --tier dev`"
    )

    def q(sql: str) -> Any:
        return safe_query(sql, tier="dev", settings=settings, actor="test_ep172", row_cap=1_000).df

    index = {
        f"{i}@{v}": (h, bool(locked))
        for i, v, h, locked in q(
            "SELECT codeset_id, version, def_hash, locked FROM meta.codesets "
            "WHERE codeset_id IN ('sepsis_explicit', 't2dm')"
        ).rows()
    }
    assert set(index) == {OLD, NEW, "t2dm@1.0.0"}, remedy
    for ref in (OLD, NEW, "t2dm@1.0.0"):
        assert index[ref] == (codesets.get(ref).codeset.def_hash, True), ref
    members = {
        str(r[0])
        for r in q(
            "SELECT code FROM meta.codeset_members WHERE codeset_id = 'sepsis_explicit' "
            "AND version = '1.1.0' AND match_kind = 'exact'"
        ).rows()
    }
    assert ACCEPTED["icd9"] | ACCEPTED["icd10"] <= members
    versions = {
        f"{i}@{v}": (h, s)
        for i, v, h, s in q(
            "SELECT phenotype_id, version, def_hash, status FROM meta.phenotype_versions "
            "WHERE phenotype_id = 'sepsis_explicit'"
        ).rows()
    }
    assert set(versions) >= {OLD, NEW}, remedy
    for ref in (OLD, NEW):
        assert versions[ref] == (phenotypes.get(ref).def_hash, "done"), ref
    latest = phen_runner.built_versions("dev", settings=settings, actor="test_ep172")
    done = [v for v in latest if v["phenotype_id"] == "sepsis_explicit" and v["status"] == "done"]
    newest = max(done, key=lambda v: phen_runner.semver_key(str(v["version"])))
    assert newest["version"] == "1.1.0", "the session view follows the latest built version"
    print(f"dev: {len(index)} reviewed code-set row(s), {len(versions)} sepsis_explicit version(s)")
