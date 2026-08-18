"""EP-10 — raw inventory manifest: per-file hash / header / row count, the manifest store and
raw snapshot id, reconciliation against the vendored ``validate.sql``, and ``mwh inventory``.

Fixture tier only: a synthetic source root is written to ``tmp_path`` with the contract's 41
CSVs (contract headers, ids >= 90 000 000, a sentinel cell value), the data root is a temp
directory behind the same fake-volume seams ``test_ep03`` uses, and no real path is read. Every
DuckDB connection is opened by the module itself with ``duckdb_settings("build")`` (DESIGN §6).
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
from collections import namedtuple
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from mimicwarehouse import config, guard, inventory
from mimicwarehouse.cli import DIAGNOSTIC_COMMANDS, app
from mimicwarehouse.config import DriveInfo
from mimicwarehouse.inventory import (
    DATASET_DIRS,
    FILES_EXPECTED,
    FileRecord,
    RawManifest,
    build_inventory,
    compute_snapshot_id,
    expected_counts,
    inventory_file,
    load_raw_manifest,
    parse_sha256sums,
    parse_validate_sql,
    plan_files,
    raw_snapshot_id,
    read_header,
    reconcile,
    render_docs,
    resolve_dataset,
    write_docs,
)
from mimicwarehouse.schema import Table, load_contract

pytestmark = pytest.mark.ep_10

runner = CliRunner()

# Synthetic ids (>= 90 000 000, D-27) and a sentinel that must never surface in any output.
SUBJECT_ID = 90_000_001
HADM_ID = 95_000_001
SENTINEL = "SENTINEL_CELL_zq7"
DiskUsage = namedtuple("DiskUsage", "total used free")
FIXED_NTFS = DriveInfo(letter="C", drive_type="DRIVE_FIXED", label="Windows", filesystem="NTFS")


# ---------------------------------------------------------------------------
# Fixtures: fake-safe environment, synthetic source root, temp data root
# ---------------------------------------------------------------------------


def _fake_disk_usage(free_gb: float, total_gb: float = 950.0):
    def fake(path):
        total = int(total_gb * config.GB)
        free = int(free_gb * config.GB)
        return DiskUsage(total=total, used=total - free, free=free)

    return fake


@pytest.fixture
def workspace(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    """Isolated settings environment: temp workspace, no MWH_* vars, healthy fake volume."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    for key in list(os.environ):
        if key.upper().startswith("MWH_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("COLUMNS", "200")  # rich: no cell folding / message wrapping under CliRunner
    monkeypatch.setattr(config, "workspace_root", lambda: ws)
    monkeypatch.setattr(config, "drive_info", lambda path: FIXED_NTFS)
    monkeypatch.setattr(config, "logical_drives", lambda: ["C"])
    monkeypatch.setattr(config, "volume_of", lambda path: "VOL")
    monkeypatch.setattr(config, "onedrive_roots", lambda: [])
    monkeypatch.setattr(config.shutil, "disk_usage", _fake_disk_usage(500.0))
    config.configure()
    yield ws
    config.configure()


def _cell(col_name: str, col_type: str, i: int) -> str:
    """A synthetic cell: ids in the >= 90 000 000 band, otherwise typed placeholders."""
    if col_name == "subject_id":
        return str(SUBJECT_ID + i)
    if col_name.endswith("_id") and col_type in {"INTEGER", "BIGINT", "SMALLINT"}:
        return str(HADM_ID + i)
    if col_type in {"INTEGER", "BIGINT", "SMALLINT"}:
        return str(i)
    if col_type in {"DOUBLE", "FLOAT"} or col_type.startswith("DECIMAL"):
        return f"{i}.5"
    if col_type == "TIMESTAMP":
        return "2150-01-01 00:00:00"
    if col_type == "DATE":
        return "2150-01-01"
    if col_type == "BOOLEAN":
        return "true"
    return SENTINEL


def rows_for(table: Table, n: int, *, embedded_newline: bool = False) -> list[list[str]]:
    rows = []
    for i in range(n):
        row = [_cell(c.name, c.duckdb_type, i) for c in table.columns]
        if embedded_newline:
            # the last VARCHAR column gets a quoted multi-line value + a comma + a quote
            for j in range(len(table.columns) - 1, -1, -1):
                if table.columns[j].duckdb_type == "VARCHAR":
                    row[j] = f'{SENTINEL} line1\nline2, with "quotes"'
                    break
        rows.append(row)
    return rows


def write_csv(path: Path, header: list[str], rows: list[list[str]], *, crlf: bool = False) -> None:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n" if crlf else "\n")
    w.writerow(header)
    w.writerows(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(buf.getvalue(), encoding="utf-8", newline="")


def expected_rows(table: Table) -> int:
    """Deterministic, distinct-ish row counts per table (all tiny)."""
    return 2 + (sum(map(ord, table.name)) % 7)


@pytest.fixture
def source_root(tmp_path: Path) -> Path:
    """The contract's 41 CSVs under ``<tmp>/src/<dataset-dir>/<csv_path>`` plus SHA256SUMS."""
    contract = load_contract()
    root = tmp_path / "src"
    for t in contract.tables:
        path = root / DATASET_DIRS[t.dataset] / Path(*t.csv_path.split("/"))
        n = expected_rows(t)
        embedded = t.name in {"labevents", "discharge"}  # quoted embedded newlines
        crlf = t.name in {"admissions", "edstays", "radiology_detail"}
        write_csv(path, list(t.column_names), rows_for(t, n, embedded_newline=embedded), crlf=crlf)
    # A SHA256SUMS.txt for the hosp/icu dataset (archive names, as PhysioNet ships them).
    sums = root / DATASET_DIRS["mimic-iv-3.1"] / "SHA256SUMS.txt"
    lines = [
        f"{hashlib.sha256(t.csv_path.encode()).hexdigest()}  {t.csv_path}.gz"
        for t in contract.by_dataset("mimic-iv-3.1")
    ]
    lines.insert(0, "# synthetic checksum list")
    lines.append(f"{hashlib.sha256(b'x').hexdigest()}  LICENSE.txt")
    sums.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return root


@pytest.fixture
def settings(workspace: Path, source_root: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Validated settings pointing at the temp data root + synthetic source root."""
    data_root = tmp_path / "mimicdata"
    data_root.mkdir()
    monkeypatch.setenv("MWH_DATA_ROOT", str(data_root))
    monkeypatch.setenv("MWH_SOURCE_ROOT", str(source_root))
    config.get_settings.cache_clear()
    s = config.get_settings()
    assert s.data_root == data_root and s.source_root == source_root
    return s


def _table(name: str) -> Table:
    return load_contract().table(name)


def _no_leak(text: str, *, band_check: bool = True) -> None:
    """Neither a fixture id nor a cell value may appear in ``text``; human-readable output (tables,
    logs, docs) must also carry no bare 8-digit band token (guard G4) — ``--json`` payloads keep
    raw integers for machine consumers, so they skip that last check."""
    assert str(SUBJECT_ID) not in text
    assert str(HADM_ID) not in text
    assert SENTINEL not in text
    if band_check:
        assert guard.id_band_hits(text.encode("utf-8")) == []


# ---------------------------------------------------------------------------
# inventory_file: hash, header, rows
# ---------------------------------------------------------------------------


def test_sha256_matches_hashlib(settings, source_root: Path) -> None:
    t = _table("mimiciv_hosp.patients")
    path = source_root / "mimic-iv-3.1" / "hosp" / "patients.csv"
    rec = inventory_file(path, t, rowcount=False)
    assert rec.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert rec.bytes == path.stat().st_size
    assert rec.mtime_ns == path.stat().st_mtime_ns
    assert rec.rows is None and rec.rowcount_method == "skipped"
    assert rec.rel_path == "mimic-iv-3.1/hosp/patients.csv"
    assert rec.dataset == "mimic-iv-3.1" and rec.module == "hosp"
    assert rec.schema_name == "mimiciv_hosp" and rec.table == "patients"
    assert rec.header == list(t.column_names) and rec.header_matches_contract
    assert rec.seconds_hash >= 0 and rec.seconds_rows == 0


def test_row_count_handles_quoted_newlines_and_crlf(settings, source_root: Path) -> None:
    # labevents: quoted embedded newlines (+ commas and quotes) in a VARCHAR cell, LF endings
    t = _table("mimiciv_hosp.labevents")
    path = source_root / "mimic-iv-3.1" / "hosp" / "labevents.csv"
    raw = path.read_bytes()
    assert raw.count(b"\n") > expected_rows(t) + 1, "fixture must carry embedded newlines"
    rec = inventory_file(path, t)
    assert rec.rows == expected_rows(t)
    assert rec.rowcount_method == "duckdb" and not rec.csv_parallel_fallback
    assert rec.rowcount_error is None and rec.seconds_rows >= 0
    # admissions: CRLF endings
    t2 = _table("mimiciv_hosp.admissions")
    p2 = source_root / "mimic-iv-3.1" / "hosp" / "admissions.csv"
    assert b"\r\n" in p2.read_bytes()
    rec2 = inventory_file(p2, t2)
    assert rec2.rows == expected_rows(t2)
    assert rec2.header == list(t2.column_names) and rec2.header_matches_contract
    # discharge (note): embedded newlines in the text column
    t3 = _table("mimiciv_note.discharge")
    p3 = source_root / DATASET_DIRS["mimic-iv-note-2.2"] / "note" / "discharge.csv"
    assert inventory_file(p3, t3).rows == expected_rows(t3)


def test_read_header_bom_and_crlf(tmp_path: Path) -> None:
    p = tmp_path / "h.csv"
    p.write_bytes(b'\xef\xbb\xbfsubject_id,hadm_id,"quoted,name"\r\n1,2,3\r\n')
    assert read_header(p) == ["subject_id", "hadm_id", "quoted,name"]
    (tmp_path / "empty.csv").write_bytes(b"")
    assert read_header(tmp_path / "empty.csv") == []


def test_header_mismatch_detected(settings, tmp_path: Path) -> None:
    t = _table("mimiciv_hosp.d_labitems")  # itemid, label, fluid, category
    cols = list(t.column_names)
    # missing one, extra one
    p = tmp_path / "bad.csv"
    write_csv(p, [c for c in cols if c != "fluid"] + ["bogus"], [["1", SENTINEL, SENTINEL, "x"]])
    rec = inventory_file(p, t, rel_path="mimic-iv-3.1/hosp/d_labitems.csv")
    assert not rec.header_matches_contract
    assert rec.missing_columns == ["fluid"] and rec.extra_columns == ["bogus"]
    assert rec.header_status == "mismatch"
    assert rec.rows == 1
    # same names, different order → not a match, but no missing/extra (status "order")
    p2 = tmp_path / "order.csv"
    write_csv(p2, list(reversed(cols)), [["a", "b", "c", "d"]])
    rec2 = inventory_file(p2, t, rel_path="mimic-iv-3.1/hosp/d_labitems.csv", rowcount=False)
    assert not rec2.header_matches_contract
    assert rec2.missing_columns == [] and rec2.extra_columns == []
    assert rec2.header_status == "order"


def test_known_sha256_is_reused_and_gz_hash_recorded(settings, source_root: Path) -> None:
    t = _table("mimiciv_hosp.omr")
    path = source_root / "mimic-iv-3.1" / "hosp" / "omr.csv"
    rec = inventory_file(path, t, known_sha256=("ab" * 32, 12.5), gz_sha256="cd" * 32)
    assert rec.sha256 == "ab" * 32 and rec.seconds_hash == 12.5
    assert rec.physionet_gz_sha256 == "cd" * 32
    assert rec.rows == expected_rows(t)


def test_parse_sha256sums(source_root: Path, tmp_path: Path) -> None:
    sums = parse_sha256sums(source_root / "mimic-iv-3.1" / "SHA256SUMS.txt")
    assert sums["hosp/admissions.csv.gz"] == hashlib.sha256(b"hosp/admissions.csv").hexdigest()
    assert "LICENSE.txt" in sums and "# synthetic checksum list" not in sums
    assert inventory.gz_sha256_for(sums, "hosp/admissions.csv") == sums["hosp/admissions.csv.gz"]
    assert inventory.gz_sha256_for(sums, "hosp/nothing.csv") is None
    # bare names (no module dir) and '*' binary markers are tolerated
    (tmp_path / "S.txt").write_text(f"{'0' * 64} *edstays.csv.gz\nnot a line\n", encoding="utf-8")
    assert (
        inventory.gz_sha256_for(parse_sha256sums(tmp_path / "S.txt"), "ed/edstays.csv") == "0" * 64
    )
    assert parse_sha256sums(tmp_path / "missing.txt") == {}


# ---------------------------------------------------------------------------
# validate.sql parsing / expected counts
# ---------------------------------------------------------------------------

VALIDATE_SNIPPET = """\
-- Validate ... known row counts
WITH expected AS
(
    SELECT 'admissions' AS tbl,         546028 AS row_count UNION ALL
    SELECT 'd_hcpcs' AS tbl,            89208 AS row_count UNION ALL
    SELECT 'pharmacy' AS tbl,           17847567 AS row_count UNION ALL -- mwh-guard: allow (count)
    -- icu data
    SELECT 'ICUSTAYS' as TBL,   94458 as ROW_COUNT
)
, observed as
(
    SELECT 'admissions' AS tbl, count(*) AS row_count FROM mimiciv_hosp.admissions UNION ALL
    SELECT 'd_hcpcs' AS tbl, count(*) AS row_count FROM mimiciv_hosp.d_hcpcs
)
SELECT 1;
"""


def test_parse_validate_sql_snippet(tmp_path: Path) -> None:
    p = tmp_path / "validate.sql"
    p.write_text(VALIDATE_SNIPPET, encoding="utf-8")
    counts = parse_validate_sql(p)
    assert counts == {
        "admissions": 546_028,
        "d_hcpcs": 89_208,
        "pharmacy": 17_847_567,
        "icustays": 94_458,
    }


def test_expected_counts_from_vendored_validate_sql() -> None:
    hosp_icu = expected_counts("mimic-iv-3.1")
    assert len(hosp_icu) == 28  # 21 hosp (no provider) + 7 icu (no caregiver/ingredientevents)
    assert hosp_icu["patients"] == 364_627 and hosp_icu["admissions"] == 546_028
    assert hosp_icu["chartevents"] == 432_997_491
    assert "provider" not in hosp_icu and "ingredientevents" not in hosp_icu
    ed = expected_counts("mimic-iv-ed-2.2")
    assert set(ed) == {"edstays", "diagnosis", "medrecon", "pyxis", "triage", "vitalsign"}
    assert ed["edstays"] == 425_087
    assert expected_counts("mimic-iv-note-2.2") == {}
    # a directory name resolves like the label
    assert expected_counts(DATASET_DIRS["mimic-iv-ed-2.2"]) == ed
    with pytest.raises(KeyError):
        expected_counts("mimic-v")


def test_resolve_dataset() -> None:
    for label, dirname in DATASET_DIRS.items():
        assert resolve_dataset(label) == label and resolve_dataset(dirname) == label
    with pytest.raises(KeyError):
        resolve_dataset("nope")


# ---------------------------------------------------------------------------
# Snapshot id
# ---------------------------------------------------------------------------


def _rec(rel: str, size: int, digest: str, rows: int | None) -> FileRecord:
    return FileRecord(
        dataset="mimic-iv-3.1",
        dataset_dir="mimic-iv-3.1",
        module="hosp",
        schema_name="mimiciv_hosp",
        table=rel.rsplit("/", 1)[-1][:-4],
        rel_path=rel,
        bytes=size,
        mtime="2026-01-01T00:00:00+00:00",
        mtime_ns=1,
        sha256=digest,
        header=["a"],
        header_matches_contract=True,
        rows=rows,
        rowcount_method="duckdb" if rows is not None else "skipped",
        seconds_hash=0.1,
        seconds_rows=0.1,
        recorded_at="2026-01-01T00:00:00+00:00",
    )


def test_snapshot_id_deterministic_and_order_independent() -> None:
    a = _rec("mimic-iv-3.1/hosp/a.csv", 10, "a" * 64, 5)
    b = _rec("mimic-iv-3.1/hosp/b.csv", 20, "b" * 64, 6)
    c = _rec("mimic-iv-3.1/hosp/c.csv", 30, "c" * 64, None)
    id1 = compute_snapshot_id([a, b, c], files_expected=3)
    id2 = compute_snapshot_id([c, a, b], files_expected=3)
    assert id1 == id2 and re.fullmatch(r"[0-9a-f]{64}", id1 or "")
    # any change to bytes / sha / rows changes the id
    b2 = b.model_copy(update={"rows": 7})
    assert compute_snapshot_id([a, b2, c], files_expected=3) != id1
    # incomplete → None (default expectation is the contract's 41 files)
    assert compute_snapshot_id([a, b], files_expected=3) is None
    assert compute_snapshot_id([a, b, c]) is None
    # timings / mtimes / recorded_at do not enter the id
    a2 = a.model_copy(update={"seconds_hash": 9.9, "mtime_ns": 99, "recorded_at": "x"})
    assert compute_snapshot_id([a2, b, c], files_expected=3) == id1


# ---------------------------------------------------------------------------
# build: manifest store, resume / force, filters, snapshot
# ---------------------------------------------------------------------------


def test_build_writes_manifest_store_and_snapshot(settings, source_root: Path) -> None:
    result = build_inventory(settings, quiet=True)
    assert result.ok and result.errors == []
    assert len(result.processed) == FILES_EXPECTED and result.skipped == []
    assert result.files_done == FILES_EXPECTED
    assert result.raw_snapshot_id and re.fullmatch(r"[0-9a-f]{64}", result.raw_snapshot_id)
    root = settings.layout["lake_manifests"] / "raw"
    assert root.is_dir()
    for dirname in DATASET_DIRS.values():
        assert (root / f"{dirname}.jsonl").is_file()
    snap = json.loads((root / "raw_snapshot.json").read_text(encoding="utf-8"))
    assert snap["raw_snapshot_id"] == result.raw_snapshot_id
    assert snap["files_expected"] == FILES_EXPECTED and snap["files_done"] == FILES_EXPECTED
    assert snap["started"] and snap["finished"] and snap["last_file"]
    assert snap["errors"] == [] and snap["pid"] == os.getpid()
    assert snap["duckdb_version"] and snap["contract_hash"] == load_contract().content_hash()
    assert set(snap["datasets"]) == set(DATASET_DIRS)
    assert snap["datasets"]["mimic-iv-3.1"]["files_done"] == 31
    assert snap["datasets"]["mimic-iv-3.1"]["files_expected"] == 31
    assert snap["datasets"]["mimic-iv-ed-2.2"]["files_done"] == 6
    assert snap["datasets"]["mimic-iv-note-2.2"]["files_done"] == 4
    assert snap["files_expected_per_dataset"] == {
        "mimic-iv-3.1": 31,
        "mimic-iv-ed-2.2": 6,
        "mimic-iv-note-2.2": 4,
    }
    assert len(snap["runs"]) == 1 and snap["runs"][0]["processed"] == FILES_EXPECTED
    # public accessors
    manifest = load_raw_manifest(settings)
    assert manifest.files_done == FILES_EXPECTED
    assert raw_snapshot_id(settings) == result.raw_snapshot_id
    contract = load_contract()
    for t in contract.tables:
        rec = manifest.records[f"{DATASET_DIRS[t.dataset]}/{t.csv_path}"]
        assert rec.rows == expected_rows(t), t.qualified_name
        assert rec.header_matches_contract, t.qualified_name
        assert rec.rowcount_method == "duckdb"
    # gz hashes only where SHA256SUMS.txt lists them (hosp/icu fixture)
    assert manifest.records["mimic-iv-3.1/hosp/admissions.csv"].physionet_gz_sha256
    assert manifest.records["mimic-iv-ed-2.2/ed/edstays.csv"].physionet_gz_sha256 is None
    # JSONL lines: one per file, canonical JSON, no cell values
    text = (root / "mimic-iv-3.1.jsonl").read_text(encoding="utf-8")
    assert text.count("\n") == 31 and text.endswith("\n")
    _no_leak(text)
    _no_leak((root / "raw_snapshot.json").read_text(encoding="utf-8"))


def test_build_resume_skips_unchanged_and_force_recomputes(settings, source_root: Path) -> None:
    first = build_inventory(settings, quiet=True)
    before = load_raw_manifest(settings)
    second = build_inventory(settings, quiet=True)
    assert second.processed == [] and len(second.skipped) == FILES_EXPECTED
    assert second.raw_snapshot_id == first.raw_snapshot_id
    after = load_raw_manifest(settings)
    assert {k: v.recorded_at for k, v in after.records.items()} == {
        k: v.recorded_at for k, v in before.records.items()
    }
    # change one file (bytes differ) → only that file is reprocessed
    t = _table("mimiciv_hosp.services")
    p = source_root / "mimic-iv-3.1" / "hosp" / "services.csv"
    write_csv(p, list(t.column_names), rows_for(t, expected_rows(t) + 1))
    third = build_inventory(settings, quiet=True)
    assert third.processed == ["mimic-iv-3.1/hosp/services.csv"]
    assert third.raw_snapshot_id != first.raw_snapshot_id
    assert load_raw_manifest(settings).records[third.processed[0]].rows == expected_rows(t) + 1
    # --force recomputes everything
    fourth = build_inventory(settings, force=True, quiet=True)
    assert len(fourth.processed) == FILES_EXPECTED and fourth.skipped == []
    assert fourth.raw_snapshot_id == third.raw_snapshot_id
    snap = load_raw_manifest(settings).snapshot
    assert len(snap["runs"]) == 4 and snap["runs"][-1]["options"]["force"] is True


def test_build_no_rowcount_then_resume_counts_without_rehash(settings, source_root: Path) -> None:
    r1 = build_inventory(settings, rowcount=False, quiet=True)
    m1 = load_raw_manifest(settings)
    assert len(r1.processed) == FILES_EXPECTED
    assert all(r.rows is None and r.rowcount_method == "skipped" for r in m1.records.values())
    # snapshot id exists (all files present) but differs from the counted one
    assert r1.raw_snapshot_id is not None
    r2 = build_inventory(settings, quiet=True)
    m2 = load_raw_manifest(settings)
    assert len(r2.processed) == FILES_EXPECTED  # rows completed for every file
    for rel, rec in m2.records.items():
        assert rec.rows is not None
        assert rec.sha256 == m1.records[rel].sha256
        assert rec.seconds_hash == m1.records[rel].seconds_hash  # hash reused, not recomputed
    assert r2.raw_snapshot_id != r1.raw_snapshot_id
    r3 = build_inventory(settings, quiet=True)
    assert r3.processed == []


def test_build_filters_max_bytes_and_dataset(settings, source_root: Path) -> None:
    contract = load_contract()
    planned = plan_files(contract, source_root)
    sizes = sorted(p.bytes for p in planned)
    cutoff = sizes[len(sizes) // 2]
    r = build_inventory(settings, max_bytes=cutoff, quiet=True)
    small = {p.rel_path for p in planned if p.bytes <= cutoff}
    assert set(r.processed) == small and set(r.filtered) == {p.rel_path for p in planned} - small
    assert r.raw_snapshot_id is None  # incomplete
    assert raw_snapshot_id(settings) is None
    # dataset filter accepts the label or the directory name
    r2 = build_inventory(settings, datasets=[DATASET_DIRS["mimic-iv-ed-2.2"]], quiet=True)
    assert all(rel.startswith("mimic-iv-ed-2.2/") for rel in r2.processed + r2.skipped)
    assert len(r2.processed) + len(r2.skipped) == 6
    r3 = build_inventory(settings, datasets=["mimic-iv-note-2.2"], quiet=True)
    assert len(r3.processed) + len(r3.skipped) == 4
    # processing order is smallest-first
    r4 = build_inventory(settings, force=True, quiet=True)
    by_rel = {p.rel_path: p.bytes for p in planned}
    assert [by_rel[x] for x in r4.processed] == sorted(by_rel[x] for x in r4.processed)


def test_build_missing_file_and_missing_source_root(settings, source_root: Path) -> None:
    (source_root / "mimic-iv-3.1" / "icu" / "caregiver.csv").unlink()
    r = build_inventory(settings, quiet=True)
    assert r.missing == ["mimic-iv-3.1/icu/caregiver.csv"]
    assert len(r.processed) == FILES_EXPECTED - 1 and r.raw_snapshot_id is None
    assert r.ok  # a missing file is not an error, the manifest is just incomplete
    bad = settings.model_copy(update={"source_root": source_root / "nowhere"})
    with pytest.raises(FileNotFoundError):
        build_inventory(bad, quiet=True)


def test_build_refuses_below_free_space_guard(
    settings, source_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config.shutil, "disk_usage", _fake_disk_usage(50.0))
    with pytest.raises(config.DiskGuardError):
        build_inventory(settings, quiet=True)
    assert not (settings.layout["lake_manifests"] / "raw" / "raw_snapshot.json").exists()


def test_build_records_rowcount_failure_and_continues(
    settings, source_root: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    original = inventory.count_rows

    def broken(path: Path, connection: Any):
        if path.name == "d_items.csv":
            return None, "failed", True, 0.01, "parallel: boom; serial: boom"
        return original(path, connection)

    monkeypatch.setattr(inventory, "count_rows", broken)
    log = tmp_path / "job.log"
    r = build_inventory(settings, quiet=True, log_path=log)
    assert not r.ok and len(r.errors) == 1
    assert r.errors[0]["rel_path"] == "mimic-iv-3.1/icu/d_items.csv"
    assert r.errors[0]["stage"] == "rows"
    assert len(r.processed) == FILES_EXPECTED  # the file is still recorded (hash + header)
    rec = load_raw_manifest(settings).records["mimic-iv-3.1/icu/d_items.csv"]
    assert rec.rows is None and rec.rowcount_method == "failed" and rec.csv_parallel_fallback
    assert rec.rowcount_error and "boom" in rec.rowcount_error
    snap = load_raw_manifest(settings).snapshot
    assert snap["errors"] == r.errors
    text = log.read_text(encoding="utf-8")
    assert "inventory build:" in text and "inventory build finished" in text
    assert "d_items.csv" in text and "rows failed" in text
    _no_leak(text)


def test_dataset_manifest_last_line_wins(tmp_path: Path) -> None:
    a = _rec("mimic-iv-3.1/hosp/a.csv", 10, "a" * 64, 5)
    a2 = a.model_copy(update={"rows": 6})
    p = tmp_path / "x.jsonl"
    p.write_text(
        "\n".join([a.model_dump_json(), "", a2.model_dump_json()]) + "\n", encoding="utf-8"
    )
    recs = inventory.read_dataset_manifest(p)
    assert len(recs) == 1 and recs[0].rows == 6
    # write_dataset_manifest sorts by rel_path and is canonical
    b = _rec("mimic-iv-3.1/hosp/b.csv", 20, "b" * 64, 6)
    out = inventory.write_dataset_manifest(tmp_path, "mimic-iv-3.1", [b, a2])
    lines = out.read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["rel_path"] for line in lines] == [a.rel_path, b.rel_path]
    assert load_raw_manifest(root=tmp_path).files_done == 2
    assert load_raw_manifest(root=tmp_path / "nope").files_done == 0


# ---------------------------------------------------------------------------
# Reconciliation + docs
# ---------------------------------------------------------------------------


def _fake_expected(counts_by_dataset: dict[str, dict[str, int]]):
    def fake(dataset: str = "mimic-iv-3.1", contract=None) -> dict[str, int]:
        return dict(counts_by_dataset.get(resolve_dataset(dataset), {}))

    return fake


def test_reconcile_statuses(settings, source_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    contract = load_contract()
    build_inventory(settings, quiet=True)
    manifest = load_raw_manifest(settings)
    # real expectations: every hosp/icu/ed table with an upstream count mismatches the tiny fixture
    rows = reconcile(manifest, contract)
    assert len(rows) == FILES_EXPECTED
    by = {f"{r.schema_name}.{r.table}": r for r in rows}
    assert by["mimiciv_hosp.patients"].status == "mismatch"
    assert by["mimiciv_hosp.patients"].expected == 364_627
    n_patients = expected_rows(_table("mimiciv_hosp.patients"))
    assert by["mimiciv_hosp.patients"].observed == n_patients
    assert by["mimiciv_hosp.patients"].delta == n_patients - 364_627
    for qn in ("mimiciv_hosp.provider", "mimiciv_icu.caregiver", "mimiciv_icu.ingredientevents"):
        assert by[qn].status == "no-expectation" and by[qn].expected is None
    for qn in ("mimiciv_note.discharge", "mimiciv_note.radiology_detail"):
        assert by[qn].status == "no-expectation"
    assert by["mimiciv_ed.edstays"].source == "mimic-iv-ed/buildmimic/postgres/validate.sql"
    # synthetic expectations: match / mismatch / pending
    fake = {
        "mimic-iv-3.1": {
            "patients": expected_rows(_table("mimiciv_hosp.patients")),
            "admissions": 999,
        },
        "mimic-iv-ed-2.2": {"edstays": expected_rows(_table("mimiciv_ed.edstays"))},
    }
    monkeypatch.setattr(inventory, "expected_counts", _fake_expected(fake))
    rows = reconcile(manifest, contract)
    by = {f"{r.schema_name}.{r.table}": r for r in rows}
    assert by["mimiciv_hosp.patients"].status == "match" and by["mimiciv_hosp.patients"].delta == 0
    assert by["mimiciv_hosp.admissions"].status == "mismatch"
    assert by["mimiciv_ed.edstays"].status == "match"
    assert by["mimiciv_hosp.labevents"].status == "no-expectation"
    # a file that is not in the manifest yet is pending
    del manifest.records["mimic-iv-3.1/hosp/patients.csv"]
    rows = reconcile(manifest, contract)
    by = {f"{r.schema_name}.{r.table}": r for r in rows}
    assert by["mimiciv_hosp.patients"].status == "pending"
    assert (
        by["mimiciv_hosp.patients"].observed is None and by["mimiciv_hosp.patients"].delta is None
    )


def test_docs_page_is_a_manifest_only(settings, source_root: Path, tmp_path: Path) -> None:
    contract = load_contract()
    build_inventory(settings, quiet=True)
    manifest = load_raw_manifest(settings)
    rows = reconcile(manifest, contract)
    out = write_docs(manifest, rows, contract, tmp_path / "docs" / "resources" / "raw-inventory.md")
    text = out.read_text(encoding="utf-8")
    assert text.startswith("# Raw inventory manifest")
    assert "**Status:** complete" in text
    assert f"`{manifest.raw_snapshot_id or compute_snapshot_id(manifest.records.values())}`" in text
    assert "| mimic-iv-3.1 | mimiciv_hosp.patients | 364,627 |" in text
    assert "`mimic-iv-3.1/hosp/patients.csv`" in text
    # every table and every file is listed
    for t in contract.tables:
        assert f"| {t.schema_name}.{t.name} |" in text
        assert f"`{DATASET_DIRS[t.dataset]}/{t.csv_path}`" in text
    # thousands separators only, no bare 8-digit band tokens, no ids, no cell values
    _no_leak(text)
    # hook-clean bytes: LF, single trailing newline, no trailing whitespace
    assert "\r" not in text and text.endswith("\n") and not text.endswith("\n\n")
    assert all(line == line.rstrip() for line in text.splitlines())
    # partial manifests say so
    del manifest.records["mimic-iv-3.1/hosp/patients.csv"]
    partial = render_docs(manifest, reconcile(manifest, contract), contract)
    assert "**Status:** partial (40 of 41 files)" in partial
    assert "raw_snapshot_id:** none (incomplete)" in partial
    assert "| pending |" in partial


def test_docs_path_is_under_the_workspace(workspace: Path) -> None:
    assert inventory.docs_path() == workspace / "docs" / "resources" / "raw-inventory.md"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_inventory_is_not_a_diagnostic_command() -> None:
    assert "inventory" not in DIAGNOSTIC_COMMANDS  # it writes under the data root


def test_cli_build_show_reconcile_leak_free(settings, source_root: Path, tmp_path: Path) -> None:
    log = tmp_path / "ep10.log"
    res = runner.invoke(app, ["inventory", "build", "--quiet", "--log", str(log)])
    assert res.exit_code == 0, res.output
    assert log.is_file() and "inventory build finished" in log.read_text(encoding="utf-8")
    # a second run resumes; --force is accepted
    res = runner.invoke(app, ["inventory", "build"])
    assert res.exit_code == 0, res.output
    assert "0 to process, 41 up to date" in res.output
    _no_leak(res.output)

    res = runner.invoke(app, ["inventory", "show"])
    assert res.exit_code == 0, res.output
    out = res.output
    assert "mimiciv_hosp.patients" in out and "mimiciv_note.radiology" in out
    assert "sha256[:12]" in out and "header ok" in out
    assert "41/41 files in manifest (0 pending, 0 header mismatch)" in out
    assert "job: started" in out and "raw_snapshot_id" in out and "files_done 41/41" in out
    assert "MB/s" not in out
    _no_leak(out)
    res = runner.invoke(app, ["inventory", "show", "--timing"])
    assert res.exit_code == 0, res.output
    assert "MB/s" in res.output and "per-dataset totals" in res.output
    _no_leak(res.output)
    res = runner.invoke(app, ["inventory", "show", "--json"])
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    assert payload["files_done"] == 41 and payload["pending"] == []
    assert payload["raw_snapshot_id"] and len(payload["files"]) == 41
    assert payload["snapshot"]["files_done"] == 41
    _no_leak(res.output, band_check=False)

    docs = tmp_path / "docs" / "raw-inventory.md"
    res = runner.invoke(app, ["inventory", "reconcile", "--docs-path", str(docs)])
    assert res.exit_code == 1, res.output  # the tiny fixture mismatches every upstream count
    assert "mimiciv_hosp.patients" in res.output and "364,627" in res.output
    assert "mismatch=34" in res.output and "no-expectation=7" in res.output
    assert docs.is_file() and f"wrote {docs}" in res.output
    _no_leak(res.output)
    res = runner.invoke(app, ["inventory", "reconcile", "--no-docs", "--json"])
    assert res.exit_code == 1, res.output
    payload = json.loads(res.output)
    assert payload["summary"] == {"match": 0, "mismatch": 34, "no-expectation": 7, "pending": 0}
    assert payload["docs"] is None and len(payload["rows"]) == 41
    _no_leak(res.output, band_check=False)


def test_cli_show_before_any_build(settings, source_root: Path) -> None:
    res = runner.invoke(app, ["inventory", "show"])
    assert res.exit_code == 0, res.output
    assert "0/41 files in manifest (41 pending" in res.output
    assert "no raw_snapshot.json yet" in res.output
    res = runner.invoke(app, ["inventory", "reconcile", "--no-docs"])
    assert res.exit_code == 0, res.output  # nothing to mismatch yet
    assert "pending=34" in res.output and "no-expectation=7" in res.output


def test_cli_build_usage_errors(
    settings, source_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    res = runner.invoke(app, ["inventory", "build", "--dataset", "mimic-v"])
    assert res.exit_code == 2 and "unknown dataset" in res.output
    monkeypatch.setattr(config.shutil, "disk_usage", _fake_disk_usage(50.0))
    res = runner.invoke(app, ["inventory", "build", "--quiet"])
    assert res.exit_code == 2 and "refuses to write below" in res.output


def test_cli_build_missing_source_root(
    settings, source_root: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MWH_SOURCE_ROOT", str(tmp_path / "absent"))
    config.get_settings.cache_clear()
    res = runner.invoke(app, ["inventory", "build", "--quiet"])
    assert res.exit_code == 2 and "does not exist" in res.output


def test_cli_build_max_bytes_and_dataset(settings, source_root: Path) -> None:
    res = runner.invoke(
        app, ["inventory", "build", "--quiet", "--dataset", "mimic-iv-ed-2.2", "--max-bytes", "1"]
    )
    assert res.exit_code == 0, res.output
    m = load_raw_manifest(settings)
    assert m.files_done == 0  # every file is bigger than 1 byte
    res = runner.invoke(app, ["inventory", "build", "--quiet", "--dataset", "mimic-iv-ed-2.2"])
    assert res.exit_code == 0, res.output
    m = load_raw_manifest(settings)
    assert m.files_done == 6 and all(r.dataset == "mimic-iv-ed-2.2" for r in m.records.values())
    res = runner.invoke(app, ["inventory", "show"])
    assert "6/41 files in manifest (35 pending" in res.output


def test_manifest_dataclass_helpers() -> None:
    m = RawManifest(root=Path("x"))
    assert m.files_done == 0 and m.raw_snapshot_id is None and m.by_dataset("mimic-iv-3.1") == []
