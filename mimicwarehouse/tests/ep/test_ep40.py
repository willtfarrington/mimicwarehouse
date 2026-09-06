"""EP-40 — code-set registry + ICD-9 <-> ICD-10 GEM utility.

Fixture tier (default): the packaged seeds load, cover the brief's list, are all locked and
hygienic; ``def_hash`` is invariant to YAML key order / whitespace / code dots / list order
and moves when a member moves; the schema refuses the crafted violations (integer codes,
a non-dual ICD set, foreign systems, partial groups, bad semver, a PMID reference); a
frozen ``(id, version)`` whose file changed is refused (library and ``mwh codeset``
commands, exit 3) while a version bump loads unlocked and locks; prefix rules expand
against the fixture dictionaries; the GEM parser, ``forward`` / ``backward`` and the
round-trip on the committed public sample; the fetcher against a fake CMS (crafted zips
through the ``_urlopen`` seam: landing + register, resume, a corrupted file refused and
deleted); the DAG spec, the catalog hook and the CLI wiring; a minimal fixture lake built
through the runner (dims + ``codesets.compile`` + ``codesets.gem`` + catalog) carries
``meta.codesets`` / ``meta.codeset_members`` / ``meta.gem_*`` with the expected rows, a
selection re-compile keeps the other sets, ``validate`` and ``expand --via-gem`` work on
it; the docs page is in sync; the import budget. ``tier("dev")``: the compiled registry
and the landed GEM on the real dev catalog through ``safe_query`` (counts and dictionary
coverage >= 90 % for the ICD sets).

Everything asserted or printed is code-set text, public vocabulary lines and dictionary
titles — never a patient-level row.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

import duckdb
import pytest
import yaml

import helpers
from mimicwarehouse import config
from mimicwarehouse.catalog import build as build_mod
from mimicwarehouse.cli import app
from mimicwarehouse.codesets import gem as gem_mod
from mimicwarehouse.codesets import registry as registry_mod
from mimicwarehouse.codesets.spec import (
    CodeSetError,
    CodeSetFrozenError,
    UnknownCodeSetError,
    codeset_from_text,
    parse_ref,
)
from mimicwarehouse.dag import runner as runner_mod
from mimicwarehouse.dag import spec as dagspec_mod
from mimicwarehouse.dag.spec import load_dag

if TYPE_CHECKING:
    from mimicwarehouse.codesets.registry import Registry
    from mimicwarehouse.config import Settings

pytestmark = pytest.mark.ep_40

HOSP = "mimiciv_hosp"
ICU = "mimiciv_icu"
BAND_TOKEN = re.compile(r"(?<![\w.])[123]\d{7}(?![\w.])")
#: The brief's item-3 list.
BRIEF_IDS: frozenset[str] = frozenset(
    {
        "t2dm",
        "t1dm",
        "sepsis_explicit",
        "aki",
        "ckd",
        "heart_failure",
        "mi",
        "copd",
        "hypertension",
        "atrial_fibrillation",
        "charlson_groups",
        "labs_creatinine",
        "labs_lactate",
        "labs_glucose",
        "vasopressors",
        "insulin",
        "antibiotics",
        "noninsulin_antidiabetics",
        "atc_a10a_insulins",
        "atc_c01ca_adrenergics",
    }
)
DIM_STEPS: tuple[str, ...] = (
    f"stage.{HOSP}.d_icd_diagnoses",
    f"stage.{HOSP}.d_icd_procedures",
    f"stage.{HOSP}.d_hcpcs",
    f"stage.{HOSP}.d_labitems",
    f"stage.{ICU}.d_items",
)
ALL_DICTS = (
    registry_mod.DICT_ICD_DX,
    registry_mod.DICT_ICD_PX,
    registry_mod.DICT_ITEMID,
    registry_mod.DICT_HCPCS,
)
SAMPLE = helpers.WORKSPACE / "tests" / "fixtures" / "gem_sample.txt"
GEM_NAMES = ("2018_I9gem.txt", "2018_I10gem.txt", "gem_i9pcs.txt", "gem_pcsi9.txt")

CRAFTED_HF = """\
id: crafted_hf
version: "1.0.0"
name: crafted heart failure (test)
kind: icd_dx
members:
  icd9: ["4280"]
  icd10: ["I509"]
provenance: {source: hand, accessed: "2026-09-06"}
"""


@pytest.fixture(scope="module")
def registry() -> Registry:
    return registry_mod.load_registry()


def _sections(text: str | None = None) -> dict[str, str]:
    """The sample split on its ``## file:`` markers -> ``{file name: GEM text}``."""
    raw = text if text is not None else SAMPLE.read_text(encoding="utf-8")
    out: dict[str, str] = {}
    current: str | None = None
    for line in raw.splitlines():
        if line.startswith("## file:"):
            current = line.split(":", 1)[1].strip()
            out[current] = ""
        elif current is not None and not line.startswith("#"):
            out[current] += line + "\n"
    return out


def _land_sample(settings: Settings) -> Path:
    """Place the sample's four files where the GEM step looks for them."""
    root = gem_mod.gem_root(settings)
    root.mkdir(parents=True, exist_ok=True)
    for name, text in _sections().items():
        (root / name).write_text(text, encoding="utf-8", newline="\r\n")
    return root


def _scalar(con: duckdb.DuckDBPyConnection, sql: str) -> Any:
    row = con.execute(sql).fetchone()
    assert row is not None
    return row[0]


def _sha(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def _drop_progress_handlers() -> None:
    """``mwh codeset compile`` attaches a stdout progress handler; under CliRunner that
    stream is closed once the invocation returns, so later runner calls in this process
    would log "I/O operation on closed file" noise — detach it after each CLI compile."""
    import logging

    logger = logging.getLogger("mimicwarehouse")
    for handler in list(logger.handlers):
        logger.removeHandler(handler)


# ---------------------------------------------------------------------------
# 1. The packaged seeds: coverage, lock, hygiene
# ---------------------------------------------------------------------------


def test_packaged_seeds_cover_the_brief_and_are_locked(registry: Registry) -> None:
    assert len(registry) >= registry_mod.MIN_SEED_SETS
    ids = set(registry.ids())
    assert ids >= BRIEF_IDS, sorted(BRIEF_IDS - ids)
    kinds = {e.codeset.id: e.codeset.kind for e in registry}
    assert kinds["t2dm"] == kinds["charlson_groups"] == "icd_dx"
    assert kinds["labs_glucose"] == "itemid" and kinds["antibiotics"] == "drug"
    assert kinds["atc_a10a_insulins"] == "atc"
    for entry in registry:
        cs = entry.codeset
        assert entry.locked and entry.packaged and entry.display_path.startswith("defs/")
        assert cs.version == "1.0.0" and cs.provenance.accessed == "2026-09-06"
        assert cs.name and cs.notes and cs.references, cs.ref
        assert cs.n_declared >= 1 and cs.declared_systems, cs.ref
        if cs.kind in ("icd_dx", "icd_px"):
            assert cs.members.icd9 and cs.members.icd10, f"{cs.ref} is not dual"
        raw = entry.path.read_bytes()
        assert b"\r" not in raw and raw.endswith(b"\n") and raw.decode("utf-8").isascii()
        assert not BAND_TOKEN.search(raw.decode("utf-8")), entry.path.name
    charlson = registry.get("charlson_groups@1.0.0").codeset
    assert len(charlson.groups) == 17 and "malignant_cancer" in charlson.groups
    assert all(e.match == "prefix" for e in (*charlson.members.icd9, *charlson.members.icd10))
    cancer10 = {e.code for e in charlson.members.icd10 if e.group == "malignant_cancer"}
    assert {"C45", "C49", "C50", "C58"} <= cancer10 and "C4A" not in cancer10, "C4A excluded"
    vaso = registry.get("vasopressors@1.0.0").codeset
    assert vaso.members.drugs is not None and "NOREPINEPHRINE" in vaso.members.drugs.names
    assert 221906 in vaso.members.itemids and vaso.declared_systems == ("itemid", "drug_name")
    assert registry.get("labs_glucose@1.0.0").codeset.members.itemids == (
        50809,
        50931,
        220621,
        225664,
        226537,
    )
    # the lock file: valid, ASCII, one entry per seed with the current hash
    lock_path = registry_mod.lock_path(registry_mod.packaged_defs_dir())
    lock = registry_mod.load_lock(registry_mod.packaged_defs_dir())
    assert lock_path.name == registry_mod.LOCK_FILENAME and lock.version == 1
    assert set(lock.codesets) >= set(registry.refs())
    for entry in registry:
        assert lock.codesets[entry.ref].def_hash == entry.codeset.def_hash
        assert lock.codesets[entry.ref].kind == entry.codeset.kind
    assert lock_path.read_text(encoding="utf-8").isascii()
    runner = helpers.cli_runner()
    check = runner.invoke(app, ["codeset", "lock", "--check"])
    assert check.exit_code == 0 and "0 unlocked" in check.stdout, check.output
    listing = runner.invoke(app, ["codeset", "list", "--json"])
    assert listing.exit_code == 0, listing.output
    payload = json.loads(listing.stdout)
    assert {c["ref"] for c in payload["codesets"]} == set(registry.refs())
    assert all(c["locked"] for c in payload["codesets"])


# ---------------------------------------------------------------------------
# 2. def_hash invariance; the schema refusals; references
# ---------------------------------------------------------------------------

_TEXT_A = """\
id: crafted
version: "1.0.0"
name: crafted
kind: icd_dx
members:
  icd9:
    - {code: "410", match: prefix}
    - {code: "412.0"}
  icd10:
    - "I21"
    - {code: "i22.9", match: exact}
provenance: {source: hand, accessed: "2026-09-06"}
notes: first spelling
"""
_TEXT_B = """\
notes: >
  a different note, and the keys shuffled
provenance:
  accessed: "2026-09-06"
  source: hand
kind: icd_dx
members:
  icd10:
    - code: I229
      match: exact
    - code: "I21"
      match: exact
  icd9:
    - code: "4120"
    - match: prefix
      code: "410"
    - {code: "412.0"}
name: crafted (renamed)
version: "1.0.0"
id: crafted
"""


def test_def_hash_invariance_and_normalisation() -> None:
    a = codeset_from_text(_TEXT_A)
    b = codeset_from_text(_TEXT_B)
    assert a.def_hash == b.def_hash and len(a.def_hash) == 64
    assert a.canonical == b.canonical
    assert [e.code for e in a.members.icd9] == ["410", "4120"]
    assert [e.code for e in a.members.icd10] == ["I21", "I229"], "sorted, dots dropped, upper"
    moved = codeset_from_text(_TEXT_A.replace('"412.0"', '"412.1"'))
    assert moved.def_hash != a.def_hash
    grouped = codeset_from_text(
        _TEXT_A.replace('{code: "410", match: prefix}', '{code: "410", match: prefix, group: g1}')
        .replace('{code: "412.0"}', '{code: "412.0", group: g1}')
        .replace('- "I21"', "- {code: I21, group: g1}")
        .replace('{code: "i22.9", match: exact}', '{code: "i22.9", group: g1}')
    )
    assert grouped.def_hash != a.def_hash and grouped.groups == ("g1",)
    # canonical JSON is key-sorted, whitespace-free ASCII
    from mimicwarehouse.codesets.spec import canonical_json

    assert canonical_json(a.canonical) == canonical_json(b.canonical)
    assert " " not in canonical_json(a.canonical) and canonical_json(a.canonical).isascii()
    # drug names: whitespace / case folded, sorted; a regex stays verbatim
    drug = codeset_from_text(
        'id: d\nversion: "1.0.0"\nname: d\nkind: drug\nmembers:\n  drugs:\n'
        '    names: [" Metformin ", "glipizide", "METFORMIN", "glucophage   xr"]\n'
        '    rxnorm: ["6809", 6809]\nprovenance: {source: hand, accessed: "2026-09-06"}\n'
    )
    assert drug.members.drugs is not None
    assert drug.members.drugs.names == ("GLIPIZIDE", "GLUCOPHAGE XR", "METFORMIN")
    assert drug.members.drugs.rxnorm == ("6809",) and drug.declared_systems == (
        "drug_name",
        "rxnorm",
    )
    regex = codeset_from_text(
        'id: r\nversion: "1.0.0"\nname: r\nkind: drug\nmembers:\n  drugs:\n'
        '    match: regex\n    names: ["^insulin\\\\s+(lispro|aspart)$"]\n'
        'provenance: {source: hand, accessed: "2026-09-06"}\n'
    )
    assert regex.members.drugs is not None
    assert regex.members.drugs.names == ("^insulin\\s+(lispro|aspart)$",)


def _refused(text: str, match: str) -> None:
    with pytest.raises(CodeSetError, match=match):
        codeset_from_text(text)


def test_schema_refusals() -> None:
    base = _TEXT_A
    _refused(base.replace('{code: "412.0"}', "{code: 0412}"), "quote")
    _refused(
        base.replace('  icd10:\n    - "I21"\n    - {code: "i22.9", match: exact}\n', ""), "dual"
    )
    _refused(base.replace("kind: icd_dx", "kind: drug"), "requires member system")
    _refused(base.replace("kind: icd_dx", "kind: itemid"), "requires member system")
    _refused(
        base.replace('{code: "410", match: prefix}', '{code: "410", match: prefix, group: g1}'),
        "grouped set",
    )
    _refused(base.replace('version: "1.0.0"', 'version: "1.0"'), "semver")
    _refused(base.replace('version: "1.0.0"', "version: 1"), "semver")
    _refused(base.replace("id: crafted", "id: Crafted-Set"), "slug")
    _refused(base.replace("kind: icd_dx", "kind: snomed"), "kind")
    _refused(base.replace('{code: "410", match: prefix}', '{code: "410", match: fuzzy}'), "match")
    _refused(base + "references: ['PMID: 98765432']\n", "PMID")  # 9…: outside the id bands
    _refused(base + "references: ['not a url']\n", "https://")
    _refused(base.replace("notes: first spelling", "extra: 1"), "extra")
    _refused("- not\n- a mapping\n", "top level")
    _refused(
        base.replace(
            'provenance: {source: hand, accessed: "2026-09-06"}',
            "provenance: {source: hand, accessed: 99991231}",  # an unquoted compact date
        ),
        "ISO date",
    )
    _refused(base.replace("kind: icd_dx", "kind: loinc"), "requires member system")
    _refused(
        base.replace("  icd9:", "  itemids: [0]\n  icd9:"),
        "positive integer",
    )


def test_references_and_registry_lookup(registry: Registry) -> None:
    assert parse_ref("t2dm@1.0.0") == ("t2dm", "1.0.0")
    for bad in ("t2dm", "t2dm@1.0", "T2DM@1.0.0", "t2dm@1.0.0.1", "@1.0.0", ""):
        with pytest.raises(CodeSetError, match="reference"):
            parse_ref(bad)
    entry = registry.get("t2dm@1.0.0")
    assert entry.codeset.id == "t2dm" and entry.ref == "t2dm@1.0.0"
    with pytest.raises(UnknownCodeSetError, match=re.escape("known versions of t2dm: 1.0.0")):
        registry.get("t2dm@9.9.9")
    with pytest.raises(UnknownCodeSetError, match="known ids"):
        registry.get("nope@1.0.0")
    subset = registry.select(["aki@1.0.0", "t2dm@1.0.0"])
    assert subset.refs() == ("aki@1.0.0", "t2dm@1.0.0"), "registry order, not request order"
    with pytest.raises(UnknownCodeSetError):
        registry.select(["nope@1.0.0"])
    with pytest.raises(CodeSetError, match="defined twice"):
        registry_mod.Registry([*registry.entries, registry.entries[0]])


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
    aki = defs / "aki.yaml"
    original = aki.read_text(encoding="utf-8")
    edited = original.replace(
        '    - {code: "N17", match: prefix}\n',
        '    - {code: "N17", match: prefix}\n    - {code: "N19"}\n',
    )
    assert edited != original
    aki.write_text(edited, encoding="utf-8", newline="\n")
    with pytest.raises(CodeSetFrozenError, match=re.escape("aki@1.0.0 is frozen")):
        registry_mod.load_dir(defs)
    with pytest.raises(CodeSetFrozenError, match="version bump"):
        registry_mod.lock_dir(defs)
    # the commands refuse with exit 3 before touching anything
    monkeypatch.setattr(registry_mod, "packaged_defs_dir", lambda: defs)
    runner = helpers.cli_runner()
    root = str(tmp_path / "root")
    try:
        listing = runner.invoke(app, ["codeset", "list"])
        assert listing.exit_code == 3 and "refused" in listing.stderr, listing.output
        compile_ = runner.invoke(
            app, ["--data-root", root, "codeset", "compile", "--tier", "fixture"]
        )
        assert compile_.exit_code == 3, compile_.output
        assert "aki@1.0.0 is frozen" in compile_.stderr and "version bump" in compile_.stderr
        assert not (tmp_path / "root" / "lake").exists(), "nothing was built"
        validate = runner.invoke(app, ["--data-root", root, "codeset", "validate", "aki@1.0.0"])
        assert validate.exit_code == 3, validate.output
    finally:
        config.configure()
        _drop_progress_handlers()
    # a version bump is the sanctioned change: loads unlocked, then locks
    bumped = edited.replace('version: "1.0.0"', 'version: "1.1.0"')
    (defs / "aki_1_1.yaml").write_text(bumped, encoding="utf-8", newline="\n")
    aki.write_text(original, encoding="utf-8", newline="\n")
    entries = {e.ref: e for e in registry_mod.load_dir(defs)}
    assert entries["aki@1.0.0"].locked and not entries["aki@1.1.0"].locked
    assert entries["aki@1.1.0"].codeset.def_hash != entries["aki@1.0.0"].codeset.def_hash
    check = runner.invoke(app, ["codeset", "lock", "--check", "--defs", str(defs)])
    assert check.exit_code == 1 and "aki@1.1.0" in check.stdout, check.output
    result = registry_mod.lock_dir(defs)
    assert result.added == ("aki@1.1.0",) and len(result.unchanged) == len(entries) - 1
    again = registry_mod.lock_dir(defs)
    assert again.added == () and len(again.unchanged) == len(entries)
    assert all(e.locked for e in registry_mod.load_dir(defs))
    lock_text = registry_mod.lock_path(defs).read_text(encoding="utf-8")
    assert lock_text.endswith("\n") and "aki@1.1.0" in lock_text
    # the packaged seeds + an extra directory: a duplicate pair is refused
    monkeypatch.undo()
    with pytest.raises(CodeSetError, match="defined twice"):
        registry_mod.load_registry([defs])
    with pytest.raises(CodeSetError, match="not a code-set directory"):
        registry_mod.load_dir(tmp_path / "missing")
    (defs / "broken.yaml").write_text("id: [1]\n", encoding="utf-8")
    with pytest.raises(CodeSetError, match=re.escape("broken.yaml")):
        registry_mod.load_dir(defs)


# ---------------------------------------------------------------------------
# 4. Expansion against the fixture dictionaries
# ---------------------------------------------------------------------------


def test_prefix_expansion_against_fixture_dictionaries(
    fixture_catalog: duckdb.DuckDBPyConnection, registry: Registry
) -> None:
    dicts = registry_mod.dictionaries_from_connection(fixture_catalog, ALL_DICTS, on_catalog=False)
    assert dicts.keys == tuple(sorted(ALL_DICTS)) and len(dicts) == 6
    dx9 = dicts.get(registry_mod.DICT_ICD_DX, "icd9")
    assert dx9 is not None and dx9.has("25000") and dx9.with_prefix("584") == ("5845", "5849")
    assert (
        dx9.with_prefix("zzz") == () and dx9.label("4019") == "Unspecified essential hypertension"
    )

    def rows_of(ref: str, system: str | None = None) -> list[registry_mod.MemberRow]:
        rows = registry_mod.expand(registry.get(ref).codeset, dicts)
        return [r for r in rows if system is None or r.system == system]

    aki9 = rows_of("aki@1.0.0", "icd9")
    assert [(r.code, r.match_kind, r.declared_code, r.matched) for r in aki9] == [
        ("5845", "prefix", "584", True),
        ("5849", "prefix", "584", True),
    ]
    assert all(r.label for r in aki9)
    assert {r.code for r in rows_of("aki@1.0.0", "icd10")} == {"N170", "N179"}
    t2dm9 = {r.code: r for r in rows_of("t2dm@1.0.0", "icd9")}
    assert len(t2dm9) == 20 and t2dm9["25000"].matched and t2dm9["25000"].label
    assert t2dm9["25010"].matched is False and t2dm9["25010"].label is None
    assert {r.code for r in rows_of("t2dm@1.0.0", "icd10")} == {"E119", "E1165", "E1122"}
    t1dm10 = rows_of("t1dm@1.0.0", "icd10")
    assert [(r.code, r.match_kind, r.matched) for r in t1dm10] == [("E10", "prefix", False)]
    glucose = {r.code: r for r in rows_of("labs_glucose@1.0.0")}
    assert glucose["50931"].matched and glucose["50931"].label == "Glucose"
    assert glucose["50809"].matched is False and glucose["225664"].matched is True
    vaso = rows_of("vasopressors@1.0.0")
    assert {r.matched for r in vaso if r.system == "drug_name"} == {None}
    assert {r.match_kind for r in vaso if r.system == "drug_name"} == {"contains"}
    by_item = {r.code: r.matched for r in vaso if r.system == "itemid"}
    assert by_item["221906"] is True and by_item["221653"] is False
    charlson = rows_of("charlson_groups@1.0.0")
    assert all(r.group for r in charlson) and len({r.group for r in charlson}) == 17
    cancer = [r for r in charlson if r.group == "malignant_cancer" and r.system == "icd10"]
    assert any(r.matched for r in cancer) is False, "the fixture has no neoplasm codes"
    atc = rows_of("atc_a10a_insulins@1.0.0")
    assert [(r.system, r.code, r.match_kind, r.matched) for r in atc] == [
        ("atc", "A10A", "prefix", None)
    ]
    # every seed yields rows for every system it declares
    for entry in registry:
        rows = registry_mod.expand(entry.codeset, dicts)
        assert {r.system for r in rows} == set(entry.codeset.declared_systems), entry.ref
    # coverage: per declared entry
    cov = {c.system: c for c in registry_mod.coverage(rows_of("t2dm@1.0.0"))}
    assert cov["icd9"].declared == 20 and cov["icd9"].matched == 3 and cov["icd9"].rows == 20
    assert len(cov["icd9"].unmatched) == 17 and "25010" in cov["icd9"].unmatched
    assert cov["icd10"].declared == 1 and cov["icd10"].matched == 1 and cov["icd10"].rows == 3
    assert cov["icd10"].share == 1.0 and cov["icd10"].unmatched == ()
    no_dict = {c.system: c for c in registry_mod.coverage(rows_of("vasopressors@1.0.0"))}
    assert no_dict["drug_name"].matched is None and no_dict["drug_name"].share is None
    assert no_dict["itemid"].matched == 5 and no_dict["itemid"].unmatched == ("221653",)
    # without dictionaries every row is unknown
    bare = registry_mod.expand(registry.get("aki@1.0.0").codeset)
    assert [(r.code, r.matched) for r in bare] == [("584", None), ("N17", None)]
    # the SQL texts: the itemid dictionary has two spellings, everything else one
    assert "meta.itemids" in registry_mod.dictionary_sql(registry_mod.DICT_ITEMID, on_catalog=True)
    assert "d_labitems" in registry_mod.dictionary_sql(registry_mod.DICT_ITEMID, on_catalog=False)
    with pytest.raises(CodeSetError, match="unknown dictionary key"):
        registry_mod.dictionary_sql("snomed", on_catalog=True)


# ---------------------------------------------------------------------------
# 5. The GEM: parser, forward / backward, round-trip on the public sample, fetch
# ---------------------------------------------------------------------------


def test_gem_sample_parse_and_round_trip(tmp_path: Path) -> None:
    sections = _sections()
    assert set(sections) == set(GEM_NAMES)
    parsed = {name: gem_mod.parse_gem_text(text, where=name) for name, text in sections.items()}
    assert len(parsed["2018_I9gem.txt"]) == 33 and len(parsed["2018_I10gem.txt"]) == 38
    assert len(parsed["gem_i9pcs.txt"]) == 18 and len(parsed["gem_pcsi9.txt"]) == 13
    dx = gem_mod.GemTable("dx", "2018", parsed["2018_I9gem.txt"], parsed["2018_I10gem.txt"])
    assert dx.n_forward == 33 and dx.n_backward == 38
    assert gem_mod.GemTable.targets(dx.forward(["25000"])) == {"E119"}
    assert gem_mod.GemTable.targets(dx.backward(["E119"])) == {"25000"}
    assert gem_mod.GemTable.targets(dx.forward(["428.0"])) == {"I50814", "I509"}, "dots dropped"
    assert gem_mod.GemTable.targets(dx.backward(["I509"])) == {"4280", "4289"}
    assert dx.forward(["nope"]) == {"NOPE": ()}
    # round trip: an ICD-9 code "round-trips" when one of its forward targets maps back to
    # it through the reverse file. Most sampled codes do; the GEM is asymmetric on purpose
    # (not a crosswalk): 250.02 -> E11.65, but E11.65 -> 250.80.
    round_trips: set[str] = set()
    for source, entries in dx.forward([e.source for e in parsed["2018_I9gem.txt"]]).items():
        targets = [e.target for e in entries if e.target is not None]
        if targets and source in gem_mod.GemTable.targets(dx.backward(targets)):
            round_trips.add(source)
    expected_round_trips = {
        "0389",
        "03811",
        "25000",
        "25001",
        "4019",
        "41071",
        "42731",
        "4280",
        "42823",
        "49121",
        "496",
        "5845",
        "5849",
        "5856",
        "5859",
        "78552",
        "99591",
        "99592",
        "V5867",
    }
    assert expected_round_trips <= round_trips, sorted(expected_round_trips - round_trips)
    assert "25002" not in round_trips
    assert gem_mod.GemTable.targets(dx.backward(["E1165"])) == {"25080"}, "asymmetric by design"
    # flags: no-map, one-to-many, combination scenarios / choice lists
    (no_map,) = dx.forward(["36570"])["36570"]
    assert (
        no_map.target is None and no_map.no_map and no_map.approximate and no_map.flags == "11000"
    )
    combo = dx.backward(["A4101"])["A4101"]
    assert [(e.target, e.combination, e.scenario, e.choice_list) for e in combo] == [
        ("03811", True, 1, 1),
        ("99591", True, 1, 2),
    ]
    assert all(e.approximate and not e.no_map for e in combo)
    exact = dx.forward(["5849"])["5849"]
    assert [(e.target, e.approximate, e.flags) for e in exact] == [("N179", False, "00000")]
    px = gem_mod.GemTable("px", "2018", parsed["gem_i9pcs.txt"], parsed["gem_pcsi9.txt"])
    assert gem_mod.GemTable.targets(px.forward(["3893"])) == {"02H633Z", "05HY33Z"}
    assert gem_mod.GemTable.targets(px.backward(["02H633Z"])) == {"3893"}
    (no_pcs,) = px.forward(["0016"])["0016"]
    assert no_pcs.target is None and no_pcs.no_map
    # module-level conveniences over an explicit table
    assert gem_mod.forward(["4280"], table=dx) == dx.forward(["4280"])
    assert gem_mod.backward(["I509"], table=dx) == dx.backward(["I509"])
    # parser hygiene
    assert gem_mod.parse_gem_text("# comment\n\n  0010  A000  00000 \n") == [
        gem_mod.GemEntry("0010", "A000", False, False, False, 0, 0)
    ]
    with pytest.raises(gem_mod.GemError, match="crafted:2"):
        gem_mod.parse_gem_text("0010 A000 00000\n0011 A001 0000\n", where="crafted")
    with pytest.raises(gem_mod.GemError, match="crafted:1"):
        gem_mod.parse_gem_text("0010 A000 20000\n", where="crafted")
    # a landed directory: load_gem reads the real file names; a missing kind names the remedy
    settings = config.Settings(data_root=tmp_path / "root")
    root = _land_sample(settings)
    assert gem_mod.landed_kinds(root) == ("dx", "px")
    loaded = gem_mod.load_gem("dx", root=root)
    assert loaded.n_forward == 33 and loaded.kind == "dx" and loaded.version == "2018"
    assert gem_mod.load_gem("px", settings=settings).n_backward == 13
    (root / "gem_pcsi9.txt").unlink()
    assert gem_mod.landed_kinds(root) == ("dx",)
    with pytest.raises(gem_mod.GemError, match="mwh codeset gem fetch"):
        gem_mod.load_gem("px", root=root)
    with pytest.raises(gem_mod.GemError, match="unknown GEM kind"):
        gem_mod.file_specs("cm")
    assert gem_mod.review_path(settings, "aki@1.0.0").name == "aki@1.0.0.gem-review.md"


def _zip_bytes(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, blob in members.items():
            archive.writestr(name, blob)
    return buf.getvalue()


def _crafted_archives(
    sections: dict[str, str], *, corrupt: str | None = None, drop_member: bool = False
) -> tuple[tuple[gem_mod.GemArchive, ...], dict[str, bytes]]:
    """Two fake CMS zips (members under a subdirectory, plus a stray PDF) with pinned
    hashes; ``corrupt`` pins a wrong hash for that file, ``drop_member`` omits a member."""
    urls: dict[str, bytes] = {}
    archives: list[gem_mod.GemArchive] = []
    layout = (
        ("dx", "fake-cm.zip", ("2018_I9gem.txt", "2018_I10gem.txt")),
        ("px", "fake-pcs.zip", ("gem_i9pcs.txt", "gem_pcsi9.txt")),
    )
    for kind, filename, names in layout:
        members = {f"gems/{n}": sections[n].encode("utf-8") for n in names}
        if drop_member and kind == "px":
            members.pop("gems/gem_pcsi9.txt")
        members["gems/guide.pdf"] = b"%PDF-1.4 fake guide"
        blob = _zip_bytes(members)
        url = f"https://fake.cms.test/{filename}"
        urls[url] = blob
        specs = tuple(
            gem_mod.GemFileSpec(
                n,
                kind,  # type: ignore[arg-type]
                direction,  # type: ignore[arg-type]
                "0" * 64 if corrupt == n else _sha(sections[n].encode("utf-8")),
            )
            for n, direction in zip(names, ("i9_to_i10", "i10_to_i9"), strict=True)
        )
        archives.append(
            gem_mod.GemArchive(
                kind=kind,  # type: ignore[arg-type]
                filename=filename,
                url=url,
                sha256=_sha(blob),
                files=specs,
            )
        )
    return tuple(archives), urls


def test_gem_fetch_against_a_fake_cms(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sections = _sections()
    archives, urls = _crafted_archives(sections)
    good_urls = dict(urls)
    counts: dict[str, int] = {}

    def fake_urlopen(url: str) -> io.BytesIO:
        counts[url] = counts.get(url, 0) + 1
        if url not in urls:
            import urllib.error

            raise urllib.error.URLError(f"fake 404: {url}")
        return io.BytesIO(urls[url])

    monkeypatch.setattr(gem_mod, "_urlopen", fake_urlopen)
    monkeypatch.setattr(gem_mod, "RETRY_BASE_SLEEP_S", 0.0)
    monkeypatch.setattr(gem_mod, "GEM_ARCHIVES", archives)
    settings = config.Settings(data_root=tmp_path / "root", min_free_gb=1)
    register, fetched = gem_mod.fetch(settings)
    root = gem_mod.gem_root(settings)
    assert root == settings.layout["ext"] / "vocab" / "gem" / "2018"
    assert fetched.downloaded == 4 and fetched.skipped == 0 and fetched.bytes > 0
    assert sorted(p.name for p in root.iterdir()) == sorted(
        [*GEM_NAMES, "fake-cm.zip", "fake-pcs.zip", "source.yaml"]
    )
    assert gem_mod.verify_files(root, (f for a in archives for f in a.files)) == dict.fromkeys(
        GEM_NAMES, True
    )
    assert register.version == "2018" and register.license == gem_mod.LICENSE_ID
    assert register.obtained_by == "agent" and register.redistributable
    assert [a.name for a in register.archives] == ["fake-cm.zip", "fake-pcs.zip"]
    assert {f.name for f in register.files} == set(GEM_NAMES)
    assert all(f.sha256 == _sha(sections[f.name].encode("utf-8")) for f in register.files)
    reloaded = gem_mod.load_register(gem_mod.register_path(root))
    assert reloaded == register
    doc = yaml.safe_load(gem_mod.register_path(root).read_text(encoding="utf-8"))
    assert doc["name"] == "cms-gem" and doc["files"][0]["bytes"] > 0
    assert gem_mod.load_gem("dx", settings=settings).n_forward == 33
    # a rerun skips verified files and downloads nothing
    _register2, again = gem_mod.fetch(settings)
    assert again.downloaded == 0 and again.skipped == 4
    assert all(n == 1 for n in counts.values()), counts
    _register3, forced = gem_mod.fetch(settings, force=True)
    assert forced.downloaded == 4 and all(n == 2 for n in counts.values())
    # a corrupted file is refused and deleted; a missing member is refused
    bad, bad_urls = _crafted_archives(sections, corrupt="gem_i9pcs.txt")
    urls.update(bad_urls)
    with pytest.raises(gem_mod.GemError, match="sha256 mismatch"):
        gem_mod.fetch(settings, force=True, archives=bad)
    assert not (root / "gem_i9pcs.txt").exists() and (root / "2018_I9gem.txt").exists()
    short, short_urls = _crafted_archives(sections, drop_member=True)
    urls.update(short_urls)
    with pytest.raises(gem_mod.GemError, match="no member"):
        gem_mod.fetch(settings, force=True, archives=short)
    with pytest.raises(gem_mod.GemError, match="cannot download"):
        gem_mod.fetch(
            settings,
            force=True,
            archives=(
                gem_mod.GemArchive(
                    kind="dx",
                    filename="x.zip",
                    url="https://fake.cms.test/missing.zip",
                    sha256="0" * 64,
                    files=(),
                ),
            ),
        )
    # the CLI: fetch + status (the fake CMS serves the good archives again)
    urls.update(good_urls)
    runner = helpers.cli_runner()
    root_arg = str(settings.data_root)
    try:
        monkeypatch.setenv("MWH_MIN_FREE_GB", "1")
        fetch_cli = runner.invoke(
            app, ["--data-root", root_arg, "codeset", "gem", "fetch", "--force"]
        )
        assert fetch_cli.exit_code == 0 and "4 file(s) verified" in fetch_cli.stdout, (
            fetch_cli.output
        )
        status = runner.invoke(app, ["--data-root", root_arg, "codeset", "gem", "status"])
        assert status.exit_code == 0 and "gem_pcsi9.txt" in status.stdout, status.output
        assert "cms-gem 2018" in status.stdout
        empty = runner.invoke(
            app, ["--data-root", str(tmp_path / "empty"), "codeset", "gem", "status"]
        )
        assert empty.exit_code == 2 and "mwh codeset gem fetch" in empty.stderr, empty.output
    finally:
        config.configure()


# ---------------------------------------------------------------------------
# 6. Wiring: the DAG spec, the catalog hook, the CLI, the import budget
# ---------------------------------------------------------------------------


def test_dag_spec_catalog_hook_and_cli_wiring() -> None:
    dag = load_dag()
    compile_step = dag.step(registry_mod.STEP_COMPILE)
    assert compile_step.kind == "python"
    assert compile_step.callable_name == "mimicwarehouse.codesets.registry:run_compile"
    assert set(compile_step.depends_on) == set(DIM_STEPS)
    gem_step = dag.step(gem_mod.STEP_GEM)
    assert gem_step.callable_name == "mimicwarehouse.codesets.gem:run_gem"
    assert gem_step.depends_on == () and gem_step.qualified_table is None
    for step in (compile_step, gem_step):
        assert registry_mod.DAG_TAG in step.tags and "meta" in step.tags
        assert step.tiers == ("fixture", "demo", "dev", "full")
    catalog = dag.step("catalog")
    assert {registry_mod.STEP_COMPILE, gem_mod.STEP_GEM} <= set(catalog.depends_on)
    assert registry_mod.DAG_TAG in catalog.tags
    ordered = [s.name for s in dag.ordered(tags=[registry_mod.DAG_TAG], tier="fixture")]
    assert set(ordered) == {registry_mod.STEP_COMPILE, gem_mod.STEP_GEM, "catalog"}
    assert ordered[-1] == "catalog"
    assert "codesets.yaml" in [p.name for p in dagspec_mod.spec_paths()]
    with pytest.raises(dagspec_mod.DagError, match="unknown dependenc"):
        load_dag("codesets")
    extensions = build_mod.CATALOG_EXTENSIONS
    assert registry_mod.register_codesets in extensions
    assert extensions.index(registry_mod.register_codesets) > extensions.index(
        build_mod._concepts_register_derived
    )
    assert extensions.index(registry_mod.register_codesets) < len(extensions) - 1, (
        "units stays last"
    )
    runner = helpers.cli_runner()
    top = runner.invoke(app, ["codeset", "--help"])
    assert top.exit_code == 0
    for sub in ("list", "show", "validate", "compile", "lock", "expand", "gem"):
        assert sub in top.stdout, sub
    show = runner.invoke(app, ["codeset", "show", "t2dm@1.0.0"])
    assert show.exit_code == 0 and "E11*" in show.stdout and "25000" in show.stdout, show.output
    assert "hand" in show.stdout and "locked yes" in show.stdout
    shown = runner.invoke(app, ["codeset", "show", "charlson_groups@1.0.0", "--json"])
    payload = json.loads(shown.stdout)
    assert payload["kind"] == "icd_dx" and len(payload["groups"]) == 17
    assert payload["members"]["icd9"][0] == {"code": "042", "match": "prefix", "group": "aids"}
    missing = runner.invoke(app, ["codeset", "show", "nope@1.0.0"])
    assert missing.exit_code == 2 and "known ids" in missing.stderr
    bad_ref = runner.invoke(app, ["codeset", "show", "t2dm"])
    assert bad_ref.exit_code == 2 and "reference" in bad_ref.stderr


def test_import_budget() -> None:
    helpers.assert_import_budget(
        "mimicwarehouse.codesets.cli",
        lazy=(
            "mimicwarehouse.safe",
            "mimicwarehouse.catalog.build",
            "mimicwarehouse.dag.runner",
            "mimicwarehouse.concepts.runner",
            "mimicwarehouse.units",
        ),
    )
    helpers.assert_import_budget(lazy=("mimicwarehouse.safe", "mimicwarehouse.catalog.build"))


# ---------------------------------------------------------------------------
# 7. A minimal fixture lake: compile + gem + catalog, selection, validate, expand
# ---------------------------------------------------------------------------


@pytest.fixture
def lake_settings(tmp_path: Path) -> Settings:
    return config.Settings(data_root=tmp_path / "root")


def test_compile_gem_and_catalog_on_a_minimal_fixture_lake(
    lake_settings: Settings, registry: Registry, tmp_path: Path
) -> None:
    from mimicwarehouse.catalog.connect import open_catalog

    settings = lake_settings
    _land_sample(settings)
    dag = load_dag()
    result = runner_mod.run(
        dag,
        "fixture",
        select=[*DIM_STEPS, registry_mod.STEP_COMPILE, gem_mod.STEP_GEM, "catalog"],
        settings=settings,
    )
    assert result.ok, [(s.name, s.error) for s in result.steps]
    reports = {s.name: s for s in result.steps}
    compiled_rows = reports[registry_mod.STEP_COMPILE].rows
    assert compiled_rows is not None and compiled_rows >= sum(
        len(registry_mod.expand(e.codeset)) for e in registry
    ), "prefix rules expand to at least one row each"
    assert reports[registry_mod.STEP_COMPILE].files == 2
    assert (
        reports[gem_mod.STEP_GEM].rows == 33 + 38 + 18 + 13 and reports[gem_mod.STEP_GEM].files == 2
    )
    lake = settings.lake_root("fixture")
    from mimicwarehouse.units import meta_table_path

    for table in (
        registry_mod.CODESETS_TABLE,
        registry_mod.MEMBERS_TABLE,
        gem_mod.I9_TO_I10_TABLE,
        gem_mod.I10_TO_I9_TABLE,
    ):
        assert meta_table_path(lake, "fixture", table).is_file(), table

    con = open_catalog("fixture", settings=settings)
    try:
        tables = {
            str(r[0]): str(r[1])
            for r in con.execute(
                "SELECT table_name, comment FROM duckdb_tables() WHERE schema_name = 'meta'"
            ).fetchall()
        }
        for table in (
            registry_mod.CODESETS_TABLE,
            registry_mod.MEMBERS_TABLE,
            gem_mod.I9_TO_I10_TABLE,
            gem_mod.I10_TO_I9_TABLE,
        ):
            assert "EP-40" in tables[table], table
        # meta.codesets = the registry index
        described = [d[0] for d in con.execute("DESCRIBE meta.codesets").fetchall()]
        assert described == [c for c, _t in registry_mod.CODESETS_COLUMNS]
        index = {
            f"{r[0]}@{r[1]}": r
            for r in con.execute(
                "SELECT codeset_id, version, def_hash, kind, name, n_declared, n_members, "
                "n_matched, n_groups, locked, path, tier FROM meta.codesets"
            ).fetchall()
        }
        assert set(index) == set(registry.refs())
        for entry in registry:
            row = index[entry.ref]
            assert row[2] == entry.codeset.def_hash and row[3] == entry.codeset.kind
            assert row[5] == entry.codeset.n_declared and row[9] is True
            assert row[10] == entry.display_path and row[11] == "fixture"
            assert row[6] >= row[7] >= 0
        assert index["charlson_groups@1.0.0"][8] == 17 and index["aki@1.0.0"][8] == 0
        assert index["aki@1.0.0"][6] == 4 and index["aki@1.0.0"][7] == 4
        # meta.codeset_members
        members_cols = [d[0] for d in con.execute("DESCRIBE meta.codeset_members").fetchall()]
        assert members_cols == [c for c, _t in registry_mod.MEMBERS_COLUMNS]
        pairs = {
            (str(r[0]), str(r[1]))
            for r in con.execute(
                "SELECT DISTINCT codeset_id, system FROM meta.codeset_members"
            ).fetchall()
        }
        for entry in registry:
            for system in entry.codeset.declared_systems:
                assert (entry.codeset.id, system) in pairs, (entry.ref, system)
        aki = con.execute(
            "SELECT code, match_kind, declared_code, label, matched_in_dictionary "
            "FROM meta.codeset_members WHERE codeset_id = 'aki' AND system = 'icd9' ORDER BY code"
        ).fetchall()
        assert [(r[0], r[1], r[2], r[4]) for r in aki] == [
            ("5845", "prefix", "584", True),
            ("5849", "prefix", "584", True),
        ]
        assert all(r[3] for r in aki)
        assert (
            _scalar(
                con,
                "SELECT count(*) FROM meta.codeset_members WHERE codeset_id = 't1dm' "
                "AND system = 'icd9' AND matched_in_dictionary",
            )
            == 0
        )
        assert (
            _scalar(
                con,
                "SELECT count(*) FROM meta.codeset_members WHERE codeset_id = 'charlson_groups' "
                "AND member_group IS NULL",
            )
            == 0
        )
        assert (
            _scalar(
                con,
                "SELECT count(*) FROM meta.codeset_members WHERE system IN ('drug_name', 'atc') "
                "AND matched_in_dictionary IS NOT NULL",
            )
            == 0
        )
        assert _scalar(con, "SELECT count(DISTINCT def_hash) FROM meta.codeset_members") == len(
            registry
        )
        assert _scalar(con, "SELECT count(*) FROM meta.codeset_members") == compiled_rows
        # meta.gem_*
        gem_cols = [d[0] for d in con.execute("DESCRIBE meta.gem_i9_to_i10").fetchall()]
        assert gem_cols == [c for c, _t in gem_mod.GEM_COLUMNS]
        assert _scalar(con, "SELECT count(*) FROM meta.gem_i9_to_i10") == 33 + 18
        assert _scalar(con, "SELECT count(*) FROM meta.gem_i10_to_i9") == 38 + 13
        kinds = con.execute(
            "SELECT kind, count(*) FROM meta.gem_i9_to_i10 GROUP BY 1 ORDER BY 1"
        ).fetchall()
        assert kinds == [("dx", 33), ("px", 18)]
        assert _scalar(
            con,
            "SELECT target IS NULL AND no_map FROM meta.gem_i9_to_i10 WHERE source = '36570'",
        )
        combo = con.execute(
            "SELECT target, combination, scenario, choice_list FROM meta.gem_i10_to_i9 "
            "WHERE source = 'A4101' ORDER BY choice_list"
        ).fetchall()
        assert combo == [("03811", True, 1, 1), ("99591", True, 1, 2)]
        assert _scalar(con, "SELECT DISTINCT gem_version FROM meta.gem_i9_to_i10") == "2018"
    finally:
        con.close()

    # a selection re-compiles those sets (an extra directory here) and keeps the others
    extra = tmp_path / "study"
    extra.mkdir()
    (extra / "crafted_hf.yaml").write_text(CRAFTED_HF, encoding="utf-8", newline="\n")
    with registry_mod.compile_options(select=["crafted_hf@1.0.0"], extra_dirs=[extra]):
        selected = runner_mod.run(
            dag, "fixture", select=[registry_mod.STEP_COMPILE, "catalog"], settings=settings
        )
    assert selected.ok, [(s.name, s.error) for s in selected.steps]
    con = open_catalog("fixture", settings=settings)
    try:
        assert _scalar(con, "SELECT count(*) FROM meta.codesets") == len(registry) + 1
        crafted = con.execute(
            "SELECT locked, path, n_members FROM meta.codesets WHERE codeset_id = 'crafted_hf'"
        ).fetchone()
        assert (
            crafted is not None and crafted[0] is False and crafted[1].endswith("crafted_hf.yaml")
        )
        assert crafted[2] == 2
        assert (
            _scalar(con, "SELECT count(*) FROM meta.codeset_members WHERE codeset_id = 't2dm'")
            == 23
        ), "the other sets' rows are kept"
        assert _scalar(con, "SELECT count(*) FROM meta.gem_i9_to_i10") == 51, "gem tables kept"
    finally:
        con.close()
    assert registry_mod.current_options() == registry_mod.CompileOptions()

    # validate: the library and the CLI, through safe_query
    validation = registry_mod.validate(
        "aki@1.0.0", tier="fixture", settings=settings, actor="test_ep40"
    )
    cov = {c.system: c for c in validation.coverages}
    assert cov["icd9"].declared == 1 and cov["icd9"].matched == 1 and cov["icd9"].rows == 2
    assert validation.to_dict()["n_matched"] == 4
    runner = helpers.cli_runner()
    root = str(settings.data_root)
    try:
        cli = runner.invoke(
            app, ["--data-root", root, "codeset", "validate", "t2dm@1.0.0", "--tier", "fixture"]
        )
        assert cli.exit_code == 0, cli.output
        assert "declared" in cli.stdout and "25010" in cli.stdout and "23 compiled" in cli.stdout
        as_json = runner.invoke(
            app,
            [
                "--data-root",
                root,
                "codeset",
                "validate",
                "t2dm@1.0.0",
                "--tier",
                "fixture",
                "--json",
            ],
        )
        payload = json.loads(as_json.stdout)
        systems = {s["system"]: s for s in payload["systems"]}
        assert systems["icd9"]["declared"] == 20 and systems["icd9"]["matched"] == 3
        assert len(systems["icd9"]["unmatched"]) == 17 and systems["icd10"]["rows"] == 3
        study = runner.invoke(
            app,
            [
                "--data-root",
                root,
                "codeset",
                "validate",
                "crafted_hf@1.0.0",
                "--tier",
                "fixture",
                "--defs",
                str(extra),
            ],
        )
        assert study.exit_code == 0 and "crafted_hf@1.0.0" in study.stdout, study.output
        bad_tier = runner.invoke(
            app, ["--data-root", root, "codeset", "validate", "aki@1.0.0", "--tier", "nope"]
        )
        assert bad_tier.exit_code == 2
        no_catalog = runner.invoke(
            app, ["--data-root", root, "codeset", "validate", "aki@1.0.0", "--tier", "demo"]
        )
        assert no_catalog.exit_code == 2, no_catalog.output
        # compile through the CLI: a selection, the run id, the catalog rebuilt
        compiled = runner.invoke(
            app,
            [
                "--data-root",
                root,
                "codeset",
                "compile",
                "--tier",
                "fixture",
                "aki@1.0.0",
                "crafted_hf@1.0.0",
                "--defs",
                str(extra),
            ],
        )
        assert compiled.exit_code == 0, compiled.output
        assert "run " in compiled.stdout and "compiled 2 code set(s)" in compiled.stdout
        unknown = runner.invoke(
            app, ["--data-root", root, "codeset", "compile", "--tier", "fixture", "nope@1.0.0"]
        )
        assert unknown.exit_code == 2 and "known ids" in unknown.stderr
        # expand --via-gem: proposals the set does not cover yet, both directions
        review_out = tmp_path / "review.md"
        expanded = runner.invoke(
            app,
            [
                "--data-root",
                root,
                "codeset",
                "expand",
                "crafted_hf@1.0.0",
                "--via-gem",
                "--tier",
                "fixture",
                "--defs",
                str(extra),
                "--out",
                str(review_out),
            ],
        )
        assert expanded.exit_code == 0, expanded.output
        assert "1 ICD-10 and 1 ICD-9 proposal(s)" in expanded.stdout
        text = review_out.read_text(encoding="utf-8")
        assert "# GEM review: crafted_hf@1.0.0" in text and "approximate" in text
        assert "| `I50814` |" in text and "| `4289` |" in text
        assert "| `I509` |" not in text and "| `4280` |" not in text, (
            "covered codes are not proposed"
        )
        assert "Acute on chronic systolic heart failure" not in text
        assert text.isascii() and not BAND_TOKEN.search(text)
        default_out = runner.invoke(
            app,
            [
                "--data-root",
                root,
                "codeset",
                "expand",
                "aki@1.0.0",
                "--via-gem",
                "--tier",
                "fixture",
            ],
        )
        assert default_out.exit_code == 0, default_out.output
        assert gem_mod.review_path(settings, "aki@1.0.0").is_file()
        aki_text = gem_mod.review_path(settings, "aki@1.0.0").read_text(encoding="utf-8")
        assert "0 proposed icd10 code(s)" in aki_text, "N170 / N179 are covered by the N17 prefix"
        not_icd = runner.invoke(
            app,
            [
                "--data-root",
                root,
                "codeset",
                "expand",
                "labs_glucose@1.0.0",
                "--via-gem",
                "--tier",
                "fixture",
            ],
        )
        assert not_icd.exit_code == 2 and "icd_dx / icd_px" in not_icd.stderr
        no_flag = runner.invoke(app, ["--data-root", root, "codeset", "expand", "aki@1.0.0"])
        assert no_flag.exit_code == 2 and "--via-gem" in no_flag.stderr
    finally:
        config.configure()
        _drop_progress_handlers()
    # a built-by-hand landing has no register: status says so, the step still ran
    assert not gem_mod.register_path(gem_mod.gem_root(settings)).exists()


def test_gem_step_without_a_landing_writes_nothing(lake_settings: Settings) -> None:
    result = runner_mod.run(
        load_dag(), "fixture", select=[gem_mod.STEP_GEM], settings=lake_settings
    )
    assert result.ok and result.steps[0].status == "done" and result.steps[0].rows is None
    from mimicwarehouse.units import meta_table_path

    lake = lake_settings.lake_root("fixture")
    assert not meta_table_path(lake, "fixture", gem_mod.I9_TO_I10_TABLE).exists()


def test_compile_step_refuses_without_the_dictionary_dims(lake_settings: Settings) -> None:
    result = runner_mod.run(
        load_dag(), "fixture", select=[registry_mod.STEP_COMPILE], settings=lake_settings
    )
    assert not result.ok
    assert "not staged for tier fixture" in (result.steps[0].error or "")


# ---------------------------------------------------------------------------
# 8. Docs in sync
# ---------------------------------------------------------------------------


def test_methods_doc_in_sync(tmp_path: Path, registry: Registry) -> None:
    path = registry_mod.methods_doc_path()
    assert path.is_file(), "docs/methods/codesets.md exists"
    text = path.read_text(encoding="utf-8")
    for needle in (
        "retrospective",
        "def_hash",
        "codesets.lock.json",
        "CodeSetFrozenError",
        "icd_version",
        "meta.codeset_members",
        "meta.gem_i9_to_i10",
        "gem fetch",
        "--via-gem",
        "safe_query",
        "EP-41",
        "EP-46",
        "not a crosswalk",
    ):
        assert needle in text, needle
    copy = tmp_path / "codesets.md"
    copy.write_text(text, encoding="utf-8", newline="\n")
    registry_mod.sync_methods_doc(copy)
    assert copy.read_text(encoding="utf-8") == text, "re-run `python -m mimicwarehouse.codesets`"
    table = registry_mod.render_seed_table(registry)
    assert table in text and table.count("\n") == len(registry) + 2
    for entry in registry:
        assert f"| `{entry.ref}` |" in table
    assert not BAND_TOKEN.search(text)


# ---------------------------------------------------------------------------
# 9. Dev tier: the compiled registry and the landed GEM on the real catalog
# ---------------------------------------------------------------------------


@pytest.mark.tier("dev")
def test_dev_compiled_registry_and_gem(dev_catalog: Path, registry: Registry) -> None:
    from mimicwarehouse.safe import safe_query

    settings = config.load_settings()
    remedy = "run `mwh codeset gem fetch` and `mwh codeset compile --tier dev` (EP-40) first"

    def q(sql: str) -> Any:
        return safe_query(sql, tier="dev", settings=settings, actor="test_ep40", row_cap=10_000).df

    index = q("SELECT codeset_id, version, def_hash FROM meta.codesets")
    refs = {f"{i}@{v}": h for i, v, h in index.rows()}
    assert set(refs) >= set(registry.refs()), remedy
    for entry in registry:
        assert refs[entry.ref] == entry.codeset.def_hash, entry.ref
    pairs = {
        tuple(r) for r in q("SELECT DISTINCT codeset_id, system FROM meta.codeset_members").rows()
    }
    for entry in registry:
        for system in entry.codeset.declared_systems:
            assert (entry.codeset.id, system) in pairs, (entry.ref, system)
    # dictionary coverage of the ICD sets on the full dictionaries (dims are identical in
    # every tier): >= 90 % of the declared entries per system match. The one documented
    # exception is the Charlson transcription: Quan's ICD-10 lists are WHO ICD-10, and its
    # WHO-only codes (E12, E14, F00, B21-B24, C97, I64, J46, ...) do not exist in ICD-10-CM,
    # so its icd10 arm sits at ~87 % by construction (the seed's notes say so).
    shares: dict[str, dict[str, float]] = {}
    floors = {"charlson_groups@1.0.0": {"icd10": 0.85}}
    for entry in registry:
        if entry.codeset.kind not in ("icd_dx", "icd_px"):
            continue
        validation = registry_mod.validate(
            entry.ref, tier="dev", settings=settings, actor="test_ep40"
        )
        shares[entry.ref] = {}
        for cov in validation.coverages:
            assert cov.share is not None, (entry.ref, cov.system)
            shares[entry.ref][cov.system] = cov.share
            floor = floors.get(entry.ref, {}).get(cov.system, 0.9)
            assert cov.share >= floor, (entry.ref, cov.system, cov.unmatched)
    labels = q(
        "SELECT code, label FROM meta.codeset_members WHERE codeset_id = 'aki' "
        "AND system = 'icd10' ORDER BY code"
    )
    assert labels.height >= 2 and all(str(c).startswith("N17") for c in labels["code"].to_list())
    assert all(labels["label"].to_list())
    forward_n = int(q("SELECT count(*) AS n FROM meta.gem_i9_to_i10")["n"][0])
    backward_n = int(q("SELECT count(*) AS n FROM meta.gem_i10_to_i9")["n"][0])
    assert forward_n > 0 and backward_n > 0, remedy
    assert forward_n == 24_860 + 73_593 and backward_n == 81_593 + 101_025, "the 2018 files"
    print(
        f"dev: {len(refs)} code set(s) compiled, {len(pairs)} (set, system) pair(s); "
        f"ICD coverage min {min(min(s.values()) for s in shares.values()):.1%}; "
        f"GEM rows {forward_n:,} forward / {backward_n:,} backward"
    )
