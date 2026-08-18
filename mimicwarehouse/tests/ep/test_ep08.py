"""EP-8 — mimic-code vendoring: the pinned tree, its manifest, the guard / pre-commit interplay.

Everything here runs on the committed vendor tree (fixture tier — no data, no clone needed).
The only test that touches the ``%TEMP%\\mimic-code`` clone (``test_revendor_is_noop``) skips
when the clone is absent or not at the pinned sha. Synthetic real-band tokens used to exercise
the refusal / redaction paths are **built at runtime** (``"3" + "0" * 7``) so this file never
carries one literally and the guard stays happy with it.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from mimicwarehouse import guard
from mimicwarehouse.cli import app
from mimicwarehouse.concepts import (
    VendorInfo,
    vendor_info,
    vendor_manifest,
    vendor_root,
    vendored_path,
)
from mimicwarehouse.concepts import vendoring as v

pytestmark = pytest.mark.ep_8

WORKSPACE = Path(__file__).resolve().parents[2]  # mimicwarehouse/ (the uv project)
REPO = WORKSPACE.parent
VENDOR = WORKSPACE / "src" / "mimicwarehouse" / "concepts" / "vendor"
TREE = VENDOR / "mimic-code"
MANIFEST = VENDOR / "VENDOR.json"
SHA40 = re.compile(r"^[0-9a-f]{40}$")

# The 22 hosp + 9 icu tables of MIMIC-IV 3.1 (schema names, not data).
HOSP_TABLES = (
    "admissions",
    "d_hcpcs",
    "d_icd_diagnoses",
    "d_icd_procedures",
    "d_labitems",
    "diagnoses_icd",
    "drgcodes",
    "emar",
    "emar_detail",
    "hcpcsevents",
    "labevents",
    "microbiologyevents",
    "omr",
    "patients",
    "pharmacy",
    "poe",
    "poe_detail",
    "prescriptions",
    "procedures_icd",
    "provider",
    "services",
    "transfers",
)
ICU_TABLES = (
    "caregiver",
    "chartevents",
    "d_items",
    "datetimeevents",
    "icustays",
    "ingredientevents",
    "inputevents",
    "outputevents",
    "procedureevents",
)


def _sha256_lf(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def _band_token() -> str:
    """A synthetic real-band 8-digit token, assembled at runtime (never literal here)."""
    return "3" + "0" * 7


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads(MANIFEST.read_text("utf-8"))


# ---------------------------------------------------------------------------
# VENDOR.json
# ---------------------------------------------------------------------------


def test_manifest_parses_and_pins_a_full_sha(manifest: dict) -> None:
    assert len(HOSP_TABLES) == 22 and len(ICU_TABLES) == 9
    for key in (
        "upstream_url",
        "upstream_commit",
        "commit_date",
        "vendored_on",
        "mimic_iv_version_targeted",
        "duckdb_version_upstream_readme",
        "files",
        "known_upstream_issues",
        "excluded",
        "local_edits",
    ):
        assert key in manifest, key
    assert manifest["upstream_url"] == "https://github.com/MIT-LCP/mimic-code"
    assert SHA40.match(manifest["upstream_commit"])
    assert manifest["mimic_iv_version_targeted"] == "3.1"
    assert re.match(r"^\d{4}-\d{2}-\d{2}", manifest["commit_date"])
    assert re.match(r"^\d{4}-\d{2}-\d{2}$", manifest["vendored_on"])
    assert manifest["file_count"] == len(manifest["files"]) >= 100
    assert manifest["known_upstream_issues"], "the brief's caveats are recorded, not fixed"
    for f in manifest["files"]:
        assert set(f) == {"path", "sha256_lf", "bytes"}
        assert re.match(r"^[0-9a-f]{64}$", f["sha256_lf"])
        assert not f["path"].startswith(("/", "../")) and ".." not in f["path"].split("/")
    for e in manifest["excluded"]:
        assert set(e) == {"path", "kind", "reason", "url"}
        assert e["url"].startswith(manifest["upstream_url"] + "/")
        assert manifest["upstream_commit"] in e["url"]


def test_every_listed_file_exists_with_matching_lf_sha_and_nothing_else(manifest: dict) -> None:
    listed = {f["path"] for f in manifest["files"]}
    for f in manifest["files"]:
        path = TREE / Path(*f["path"].split("/"))
        assert path.is_file(), f["path"]
        assert _sha256_lf(path) == f["sha256_lf"], f["path"]
        assert path.stat().st_size == f["bytes"], f["path"]
        assert b"\r\n" not in path.read_bytes(), f"{f['path']} is not LF"
    on_disk = {p.relative_to(TREE).as_posix() for p in TREE.rglob("*") if p.is_file()}
    assert on_disk == listed, sorted(on_disk ^ listed)


def test_allow_list_landed(manifest: dict) -> None:
    listed = {f["path"] for f in manifest["files"]}
    for rel in (
        "LICENSE",
        "mimic-iv/buildmimic/postgres/create.sql",
        "mimic-iv/buildmimic/postgres/load.sql",
        "mimic-iv/buildmimic/postgres/constraint.sql",
        "mimic-iv/buildmimic/postgres/index.sql",
        "mimic-iv/buildmimic/postgres/validate.sql",
        "mimic-iv/buildmimic/duckdb/build_mimic.sh",
        "mimic-iv-ed/buildmimic/postgres/create.sql",
        "mimic-iv-ed/buildmimic/postgres/validate.sql",
        "mimic-iv-note/buildmimic/postgres/create.sql",
        "mimic-iv/concepts_duckdb/duckdb.sql",
        "mimic-iv/concepts_duckdb/score/sofa.sql",
        "mimic-iv/concepts_duckdb/sepsis/sepsis3.sql",
        "mimic-iv/concepts/score/sofa.sql",
    ):
        assert rel in listed, rel
    duck = [p for p in listed if p.startswith("mimic-iv/concepts_duckdb/")]
    bq = [p for p in listed if p.startswith("mimic-iv/concepts/")]
    assert len(duck) >= 60 and len(bq) >= 60
    for p in listed:
        assert p == "LICENSE" or p.endswith((".sql", ".sh")), p
    excluded = {e["path"] for e in manifest["excluded"]}
    assert {"mimic-iii/", "mimic-iv/concepts_postgres/", "mimic-iv/notebooks/"} <= excluded
    assert any(p.startswith("mimic-iv/concepts/concept_map/") for p in excluded)
    assert all(not p.endswith("README.md") for p in listed)


def test_no_csv_or_gz_under_vendor() -> None:
    """This test (not ``mwh guard``, which knows .csv/.csv.gz but not bare .gz) is the .gz rule."""
    bad = [
        p.relative_to(VENDOR).as_posix()
        for p in VENDOR.rglob("*")
        if p.is_file() and p.name.lower().endswith((".csv", ".gz", ".csv.gz", ".ipynb"))
    ]
    assert bad == []


# ---------------------------------------------------------------------------
# Pre-commit / guard interplay
# ---------------------------------------------------------------------------


def test_fixer_hooks_exclude_the_vendor_tree() -> None:
    cfg = yaml.safe_load((REPO / ".pre-commit-config.yaml").read_text("utf-8"))
    hooks = {h["id"]: h for r in cfg["repos"] for h in r["hooks"]}
    want = "^mimicwarehouse/src/mimicwarehouse/concepts/vendor/"
    for hook_id in ("end-of-file-fixer", "trailing-whitespace"):
        assert hooks[hook_id].get("exclude") == want, hook_id
        assert re.match(
            hooks[hook_id]["exclude"], "mimicwarehouse/src/mimicwarehouse/concepts/vendor/x"
        )
    for hook_id in ("mwh-guard", "check-added-large-files", "detect-private-key"):
        assert "exclude" not in hooks[hook_id], f"{hook_id} must keep running over the vendor tree"


def test_guard_is_clean_over_the_vendor_tree() -> None:
    assert guard.scan([VENDOR], REPO) == []
    result = CliRunner().invoke(app, ["guard", str(VENDOR)])
    assert result.exit_code == 0, result.output


def test_local_edits_are_exactly_the_recorded_pragmas_and_redactions(manifest: dict) -> None:
    edits = {e["path"]: e for e in manifest["local_edits"]}
    assert edits, "validate.sql row counts need the pragma at every plausible pin"
    listed = {f["path"]: f for f in manifest["files"]}
    for rel, e in edits.items():
        assert set(e) == {"path", "kind", "upstream_sha256_lf", "sha256_lf", "lines", "reason"}
        assert e["kind"] in {v.EDIT_PRAGMA, v.EDIT_REDACTION}
        assert e["sha256_lf"] == listed[rel]["sha256_lf"]
        assert e["upstream_sha256_lf"] != e["sha256_lf"]
        data = (TREE / Path(*rel.split("/"))).read_bytes()
        lines = data.split(b"\n")
        assert guard.id_band_hits(data) == [], rel
        if e["kind"] == v.EDIT_PRAGMA:
            assert rel.endswith("validate.sql")
            stripped = v.strip_guard_pragma(data)
            assert hashlib.sha256(stripped).hexdigest() == e["upstream_sha256_lf"]
            flagged = {no for no, *_ in guard.id_band_hits(stripped)}
            assert flagged == set(e["lines"]), "pragma only where the guard would fire"
            for no in e["lines"]:
                assert lines[no - 1].endswith(v.GUARD_PRAGMA.encode())
        else:
            assert not rel.endswith("validate.sql")
            for no in e["lines"]:
                assert v.REDACTED.encode() in lines[no - 1]
            assert v.REDACTED.encode() not in b"\n".join(
                line for i, line in enumerate(lines, start=1) if i not in e["lines"]
            )
    # every other file is untouched: no pragma / marker anywhere else
    for rel in listed:
        if rel in edits:
            continue
        data = (TREE / Path(*rel.split("/"))).read_bytes()
        assert v.GUARD_PRAGMA.encode() not in data and v.REDACTED.encode() not in data, rel


# ---------------------------------------------------------------------------
# Content sanity: DDL tables, concept headers
# ---------------------------------------------------------------------------


def test_create_sql_has_all_22_hosp_and_9_icu_tables() -> None:
    text = vendored_path("mimic-iv/buildmimic/postgres/create.sql").read_text("utf-8")
    created = set(re.findall(r"CREATE TABLE\s+(mimiciv_\w+\.\w+)", text))
    assert {f"mimiciv_hosp.{t}" for t in HOSP_TABLES} <= created
    assert {f"mimiciv_icu.{t}" for t in ICU_TABLES} <= created
    assert len(created) == 31


GENERATED_HEADER = "-- THIS SCRIPT IS AUTOMATICALLY GENERATED. DO NOT EDIT IT DIRECTLY."


def test_every_vendored_concept_sql_keeps_its_upstream_header() -> None:
    """Byte-identity to upstream is the sha test's job; this pins the *shape* EP-37/38 rely on:
    every transpiled concept opens with the generator banner (``duckdb.sql``, the driver, with
    ``-- dependencies``), and no BigQuery source lost a leading header comment (15 of them have
    none upstream at the pinned sha — that is upstream's choice, recorded here as a floor)."""
    duck = sorted((TREE / "mimic-iv" / "concepts_duckdb").rglob("*.sql"))
    assert len(duck) >= 60
    for path in duck:
        first = path.read_text("utf-8").lstrip("\n").splitlines()[0]
        if path.name == "duckdb.sql" and path.parent.name == "concepts_duckdb":
            assert first.startswith("-- dependencies"), first
        else:
            assert first == GENERATED_HEADER, f"{path.relative_to(TREE).as_posix()}: {first[:60]!r}"
    bq = sorted((TREE / "mimic-iv" / "concepts").rglob("*.sql"))
    assert len(bq) >= 60
    headed = [p for p in bq if p.read_text("utf-8").lstrip("\n").startswith("--")]
    assert len(headed) >= 50, "upstream header comments went missing"
    for path in bq:
        first = path.read_text("utf-8").lstrip("\n").splitlines()[0]
        assert v.GUARD_PRAGMA.strip() not in first and v.REDACTED not in first


# ---------------------------------------------------------------------------
# Package plumbing (installed package, importlib.resources)
# ---------------------------------------------------------------------------


def test_vendor_info_from_the_installed_package(manifest: dict) -> None:
    info = vendor_info()
    assert isinstance(info, VendorInfo)
    assert info.sha == manifest["upstream_commit"]
    assert info.short_sha == info.sha[:12]
    assert info.commit_date == manifest["commit_date"]
    assert info.vendored_on == manifest["vendored_on"]
    assert info.mimic_iv_version == "3.1"
    assert info.file_count == len(manifest["files"])
    assert set(info.local_edits) == {e["path"] for e in manifest["local_edits"]}
    assert info.root.is_dir() and (info.root / "VENDOR.json").is_file()
    assert info.tree.is_dir()
    assert vendor_root().resolve() == VENDOR.resolve()
    assert vendor_manifest()["upstream_commit"] == info.sha
    with pytest.raises(Exception):  # frozen model  # noqa: B017
        info.sha = "x"  # type: ignore[misc]


def test_vendor_info_via_fresh_interpreter() -> None:
    """The acceptance one-liner: prints the pinned sha from the installed package."""
    import sys

    out = subprocess.run(
        [
            sys.executable,
            "-c",
            "from mimicwarehouse.concepts import vendor_info; print(vendor_info().sha)",
        ],
        capture_output=True,
        text=True,
        check=True,
        cwd=WORKSPACE,
        timeout=120,
    ).stdout.strip()
    assert out == json.loads(MANIFEST.read_text("utf-8"))["upstream_commit"]


def test_vendored_path_resolves_and_refuses_traversal() -> None:
    assert vendored_path("LICENSE").read_text("utf-8").startswith("MIT License")
    assert vendored_path("mimic-iv/concepts_duckdb/duckdb.sql").is_file()
    with pytest.raises(FileNotFoundError):
        vendored_path("mimic-iv/concepts/README.md")  # excluded on purpose
    for bad in ("../VENDOR.json", "/etc/passwd", "a/../../x", ""):
        with pytest.raises(ValueError):
            vendored_path(bad)


def test_poe_task_and_hatch_packaging_declared() -> None:
    import tomllib

    py = tomllib.loads((WORKSPACE / "pyproject.toml").read_text("utf-8"))
    assert py["tool"]["poe"]["tasks"]["vendor-mimic-code"].startswith(
        "python -m mimicwarehouse.concepts.vendoring"
    )
    assert "vendor-mimic-code" not in py["tool"]["poe"]["tasks"]["check"]
    assert py["build-system"]["build-backend"] == "hatchling.build"
    assert py["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == ["src/mimicwarehouse"]


# ---------------------------------------------------------------------------
# The vendoring script's own refusals (pure functions, synthetic input)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rel",
    ["x/a.csv", "x/a.csv.gz", "x/a.gz", "x/a.CSV", "x/a.parquet", "x/notes.ipynb", "x/a.zip"],
)
def test_script_refuses_data_shaped_names(rel: str) -> None:
    assert v.refusal_reason(rel, b"a,b\n1,2\n") is not None


def test_script_refuses_binary_and_unknown_suffixes() -> None:
    assert v.refusal_reason("x/a.sql", b"SELECT 1;\0\n") == "binary content (NUL byte)"
    assert v.refusal_reason("x/a.sql", b"\xff\xfe\x00\x01") is not None
    assert v.refusal_reason("x/a.md", b"# hi\n") is not None  # READMEs are excluded by policy
    assert v.refusal_reason("x/a.sql", b"SELECT 1;\n") is None
    assert v.refusal_reason("x/a.sh", b"#!/bin/bash\n") is None
    assert v.refusal_reason("LICENSE", b"MIT License\n") is None


def test_script_normalises_crlf_and_hashes_lf() -> None:
    assert v.normalise_lf(b"a\r\nb\r\n") == b"a\nb\n"
    assert v.sha256_hex(v.normalise_lf(b"a\r\n")) == v.sha256_hex(b"a\n")


def test_script_pragma_and_redaction_paths() -> None:
    tok = _band_token().encode()
    row_counts = (
        b"WITH e AS (\n SELECT 'x' AS tbl, "
        + tok
        + b" AS row_count UNION ALL\n SELECT 'y', 12 AS row_count\n)\n"
    )
    content, lines = v.apply_guard_pragma(row_counts)
    assert lines == [2]
    assert content.split(b"\n")[1].endswith(v.GUARD_PRAGMA.encode())
    assert guard.id_band_hits(content) == []
    assert v.strip_guard_pragma(content) == row_counts
    assert v.apply_guard_pragma(b"SELECT 1;\n") == (b"SELECT 1;\n", [])

    comment = b"-- debug: stay_id = " + tok + b" is interesting\nSELECT " + tok + b";\n"
    redacted, lines = v.redact_band_ids(comment)
    assert lines == [1, 2]
    assert tok not in redacted and redacted.count(v.REDACTED.encode()) == 2
    assert guard.id_band_hits(redacted) == []
    assert len(redacted.split(b"\n")) == len(comment.split(b"\n"))

    # routing: validate.sql → pragma; other .sql → redaction; other text with a hit → refused
    c, kind, ln = v.local_edit_for("mimic-iv/buildmimic/postgres/validate.sql", row_counts)
    assert kind == v.EDIT_PRAGMA and ln == [2]
    c, kind, ln = v.local_edit_for("mimic-iv/concepts/treatment/x.sql", comment)
    assert kind == v.EDIT_REDACTION and ln == [1, 2]
    c, kind, ln = v.local_edit_for("mimic-iv/concepts/treatment/x.sql", b"SELECT 1;\n")
    assert kind is None and ln == [] and c == b"SELECT 1;\n"
    with pytest.raises(v.VendoringError):
        v.local_edit_for("LICENSE", b"id " + tok + b"\n")
    # a .sh is not scanned by the guard (not a text candidate) → passes through untouched
    c, kind, ln = v.local_edit_for("x/build.sh", b"echo " + tok + b"\n")
    assert kind is None


def test_script_version_parsers() -> None:
    assert v.mimic_iv_version_from(b"-- Validate ...\n-- of MIMIC-IV v3.1\nWITH") == "3.1"
    assert v.mimic_iv_version_from(b"-- Tested against MIMIC-IV-ED v2.2.\n") == "2.2"
    assert v.mimic_iv_version_from(b"WITH expected AS\n") is None
    assert v.duckdb_version_from(
        b"tested against the 1.4.x LTS line (currently\n1.4.5), which"
    ) == ("1.4.x LTS (currently 1.4.5)")
    assert v.duckdb_version_from(b"nothing here") == "unknown"


def test_script_cli_rejects_missing_clone(tmp_path: Path) -> None:
    rc = v.main(
        [
            "--sha",
            "deadbeef",
            "--src",
            str(tmp_path / "nope"),
            "--dest",
            str(tmp_path / "d"),
            "--dry-run",
        ]
    )
    assert rc == 1
    assert not (tmp_path / "d").exists()


# ---------------------------------------------------------------------------
# Re-vendoring is a no-op (needs the clone at the pinned sha; skipped otherwise)
# ---------------------------------------------------------------------------


def test_revendor_is_noop(manifest: dict) -> None:
    src = v.DEFAULT_SRC
    if not (src / ".git").exists():
        pytest.skip(f"no mimic-code clone at {src}")
    try:
        head = subprocess.run(
            [
                "git",
                "-C",
                str(src),
                "rev-parse",
                "--verify",
                manifest["upstream_commit"] + "^{commit}",
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):  # pragma: no cover
        pytest.skip("git unavailable")
    if head.returncode != 0:
        pytest.skip("clone does not contain the pinned commit")
    plan = v.build_plan(src, manifest["upstream_commit"], previous=manifest)
    fresh = plan.manifest()
    assert fresh == manifest, "re-vendoring at the pinned sha must reproduce VENDOR.json exactly"
    for f in plan.files:
        assert (TREE / Path(*f.rel.split("/"))).read_bytes() == f.content, f.rel
