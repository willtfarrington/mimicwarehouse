"""EP-17 — Loader core A: typed CSV → Parquet (unpartitioned stage, manifests, rejects,
column flags, ``paths.swap_dir``).

Fixture tier (default): every stage goes against a temp data root (``helpers.tmp_data_root``)
and the committed synthetic fixture CSVs (ids >= 90 000 000) or CSVs crafted in-test — never
the real root. Dev tier (``--tier dev``): stages the two real dimension files (d_labitems
64 KB, patients 12 MB) into a temp lake under ``<data_root>/tmp/ep17`` (deleted at teardown)
and runs the TIMESTAMP(3) fractional-seconds probe — counts, schemas and hashes only; no
row-level output anywhere.
"""

from __future__ import annotations

import gzip
import json
import shutil
from collections import namedtuple
from pathlib import Path
from typing import Any

import duckdb
import pytest

import helpers
from mimicwarehouse import config, paths
from mimicwarehouse.loader import csv as loader_csv
from mimicwarehouse.loader import engine, stage
from mimicwarehouse.loader import manifest as manifest_mod
from mimicwarehouse.loader.manifest import ManifestLine
from mimicwarehouse.schema.contract import (
    ColumnMap,
    Contract,
    SchemaError,
    Table,
    TableMap,
    load_contract_from,
    tables_root,
)

pytestmark = pytest.mark.ep_17

HOSP = "mimiciv_hosp"
DiskUsage = namedtuple("DiskUsage", "total used free")


def _fake_disk_usage(free_gb: float, total_gb: float = 950.0):
    def fake(path):
        total = int(total_gb * config.GB)
        free = int(free_gb * config.GB)
        return DiskUsage(total=total, used=total - free, free=free)

    return fake


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def data_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    root = helpers.tmp_data_root(monkeypatch, tmp_path)
    yield root
    config.configure()


@pytest.fixture
def settings(data_root: Path) -> config.Settings:
    return config.get_settings()


@pytest.fixture
def lake_root(settings: config.Settings) -> Path:
    root = settings.lake_root("fixture")
    root.mkdir(parents=True, exist_ok=True)
    return root


@pytest.fixture
def con(settings: config.Settings):
    c = engine.open_build_connection(settings, tier="fixture", memory_limit="4GB")
    yield c
    c.close()


@pytest.fixture(scope="session")
def fixture_manifest(fixture_root: Path) -> dict[str, Any]:
    return json.loads((fixture_root / "manifest.json").read_text(encoding="utf-8"))


def _fixture_source(fixture_root: Path, table: Table) -> Path:
    return fixture_root / "mimic-iv-3.1" / table.csv_path


def _dest(lake_root: Path, table: Table) -> Path:
    return lake_root / "core" / table.schema_name / table.name


def _stage(
    con, table: Table, source: Path, lake_root: Path, *, build_id: str, **kwargs
) -> stage.StageResult:
    return stage.stage_unpartitioned(
        con,
        table,
        source,
        _dest(lake_root, table),
        lake_root=lake_root,
        build_id=build_id,
        **kwargs,
    )


def _parquet_schema(con, path: Path) -> list[tuple[str, str]]:
    escaped = str(path).replace("'", "''")
    rows = con.execute(f"DESCRIBE SELECT * FROM read_parquet('{escaped}')").fetchall()
    return [(r[0], r[1]) for r in rows]


# ---------------------------------------------------------------------------
# 1. Build connection (engine)
# ---------------------------------------------------------------------------


def _setting(c, name: str):
    row = c.execute(f"SELECT current_setting('{name}')").fetchone()
    assert row is not None
    return row[0]


def test_open_build_connection_applies_settings(settings: config.Settings, con) -> None:
    assert _setting(con, "threads") == settings.duckdb_threads
    assert _setting(con, "temp_directory") == str(settings.layout["tmp_duckdb"])
    assert settings.layout["tmp_duckdb"].parent.is_dir()  # created before connect (CFG-3)
    assert not _setting(con, "preserve_insertion_order")
    # the memory_limit override is per-connection; a default connection differs
    override = _setting(con, "memory_limit")
    plain = engine.open_build_connection(settings, tier="fixture")
    try:
        assert _setting(plain, "memory_limit") != override
    finally:
        plain.close()


def test_open_build_connection_free_space_guard_is_tier_aware(
    monkeypatch: pytest.MonkeyPatch, settings: config.Settings
) -> None:
    monkeypatch.setattr(config.shutil, "disk_usage", _fake_disk_usage(50.0))
    con = engine.open_build_connection(settings, tier="fixture")  # 1 GB guard: passes
    con.close()
    with pytest.raises(config.DiskGuardError):
        engine.open_build_connection(settings, tier="dev")  # 100 GB guard: refused
    monkeypatch.setattr(config.shutil, "disk_usage", _fake_disk_usage(0.5))
    with pytest.raises(config.DiskGuardError):
        engine.open_build_connection(settings, tier="fixture")


def test_duckdb_version_pin(settings: config.Settings) -> None:
    assert engine.pinned_duckdb_version() == duckdb.__version__
    assert engine.require_pinned_duckdb() == duckdb.__version__


# ---------------------------------------------------------------------------
# 2/3/4. Unpartitioned stage: schema, rows, manifests, status
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("table_name", ["d_labitems", "patients"])
def test_stage_fixture_roundtrip(
    con,
    contract: Contract,
    fixture_root: Path,
    fixture_manifest: dict[str, Any],
    lake_root: Path,
    table_name: str,
) -> None:
    table = contract.table(HOSP, table_name)
    source = _fixture_source(fixture_root, table)
    result = _stage(con, table, source, lake_root, build_id="b1")

    expected_rows = fixture_manifest["files"][f"mimic-iv-3.1/{table.csv_path}"]["rows"]
    assert result.rows == expected_rows
    assert result.files == 1 and result.rejects == 0 and result.wall_s > 0

    part = _dest(lake_root, table) / stage.PART_FILENAME
    assert part.is_file() and result.bytes == part.stat().st_size
    assert _parquet_schema(con, part) == [(c.name, c.duckdb_type) for c in table.columns]

    # manifest line: validates, carries the provenance pair and the per-table schema hash
    manifest_file = manifest_mod.manifest_path(lake_root, "b1")
    assert manifest_file.is_file()
    lines = [
        ManifestLine.model_validate_json(line)
        for line in manifest_file.read_text(encoding="utf-8").splitlines()
    ]
    line = next(ln for ln in lines if ln.table == table_name)
    assert line == result.manifest_lines[0]
    assert line.schema_name == HOSP
    assert line.path == f"core/{HOSP}/{table_name}/{stage.PART_FILENAME}"
    assert line.rows == expected_rows and line.bytes == result.bytes
    assert line.sha256 == manifest_mod.sha256_streamed(part)
    assert line.schema_hash == manifest_mod.table_schema_hash(table)
    assert duckdb.__version__ in line.writer_version
    assert line.source_sha256 is None and line.raw_snapshot_id is None  # fixture tier

    # status.json: the step entry with the EP-17 fields
    status = manifest_mod.read_status(lake_root)
    entry = status["steps"][table.qualified_name]
    assert entry["build_id"] == "b1" and entry["rows"] == expected_rows
    assert entry["files"] == 1 and entry["rejects"] == 0 and entry["bytes"] == result.bytes
    assert entry["tier_complete"] is None and entry["dev_ready"] is False
    assert entry["finished_at"] == line.ts


def test_stage_is_deterministic_and_restages(
    con, contract: Contract, fixture_root: Path, lake_root: Path
) -> None:
    table = contract.table(HOSP, "d_labitems")
    source = _fixture_source(fixture_root, table)
    first = _stage(con, table, source, lake_root, build_id="b1")
    second = _stage(con, table, source, lake_root, build_id="b2")  # restage over the live dir
    assert first.manifest_lines[0].sha256 == second.manifest_lines[0].sha256
    dest = _dest(lake_root, table)
    assert [p.name for p in dest.iterdir()] == [stage.PART_FILENAME]
    assert not paths.new_dir_for(dest).exists() and not paths.old_dir_for(dest).exists()
    status = manifest_mod.read_status(lake_root)
    assert status["steps"][table.qualified_name]["build_id"] == "b2"


def test_csv_gz_stages_identically(
    con, contract: Contract, fixture_root: Path, lake_root: Path, tmp_path: Path
) -> None:
    table = contract.table(HOSP, "d_labitems")
    source = _fixture_source(fixture_root, table)
    gz = tmp_path / "d_labitems.csv.gz"
    with gzip.open(gz, "wb") as f:
        f.write(source.read_bytes())
    plain = _stage(con, table, source, lake_root, build_id="plain")
    lake2 = lake_root / "gzlake"
    gz_result = _stage(con, table, gz, lake2, build_id="gz")
    assert gz_result.rows == plain.rows
    assert gz_result.manifest_lines[0].sha256 == plain.manifest_lines[0].sha256


def test_column_map_rename_drop_and_null_fill(
    con, contract: Contract, lake_root: Path, tmp_path: Path
) -> None:
    table = contract.table(HOSP, "d_labitems")  # itemid, label, fluid, category
    cm = ColumnMap(
        name="test_2_2",
        description="crafted map: renamed + dropped + missing column",
        derivation="test_ep17",
        schemas=(HOSP,),
        tables={
            table.qualified_name: TableMap(
                renamed={"labelx": "label"},
                dropped_in_3_1=("junk",),
                added_in_3_1=("category",),
            )
        },
    )
    source = tmp_path / "mapped.csv"
    source.write_text(
        "itemid,labelx,junk,fluid\n90000001,Alpha,zzz,Blood\n90000002,Beta,zzz,Urine\n",
        encoding="utf-8",
        newline="\n",
    )
    result = _stage(con, table, source, lake_root, build_id="map", column_map=cm)
    assert result.rows == 2 and result.rejects == 0
    part = _dest(lake_root, table) / stage.PART_FILENAME
    assert _parquet_schema(con, part) == [(c.name, c.duckdb_type) for c in table.columns]
    escaped = str(part).replace("'", "''")
    labels, categories = con.execute(
        f"SELECT count(label), count(category) FROM read_parquet('{escaped}')"
    ).fetchone()
    assert labels == 2 and categories == 0  # renamed column loaded; missing one is NULL


def test_header_mismatch_is_refused(
    con, contract: Contract, lake_root: Path, tmp_path: Path
) -> None:
    table = contract.table(HOSP, "d_labitems")
    missing = tmp_path / "missing.csv"
    missing.write_text("itemid,label,fluid\n90000001,Alpha,Blood\n", encoding="utf-8")
    with pytest.raises(loader_csv.SchemaMismatchError, match="category"):
        _stage(con, table, missing, lake_root, build_id="bad")
    extra = tmp_path / "extra.csv"
    extra.write_text("itemid,label,fluid,category,surprise\n90000001,A,B,C,D\n", encoding="utf-8")
    with pytest.raises(loader_csv.SchemaMismatchError, match="surprise"):
        _stage(con, table, extra, lake_root, build_id="bad")
    reordered = tmp_path / "reordered.csv"
    reordered.write_text("label,itemid,fluid,category\nA,90000001,B,C\n", encoding="utf-8")
    with pytest.raises(loader_csv.SchemaMismatchError, match="order"):
        _stage(con, table, reordered, lake_root, build_id="bad")
    assert not _dest(lake_root, table).exists()


def test_reject_threshold(
    con, contract: Contract, settings: config.Settings, lake_root: Path, tmp_path: Path
) -> None:
    table = contract.table(HOSP, "d_labitems")
    source = tmp_path / "bad_row.csv"
    source.write_text(
        "itemid,label,fluid,category\n90000001,Alpha,Blood,Chemistry\noops,Beta,Urine,Chemistry\n",
        encoding="utf-8",
        newline="\n",
    )
    # default loader_reject_max = 0: any reject refuses the stage, dest stays absent
    assert settings.loader_reject_max == 0
    with pytest.raises(stage.RejectThresholdError) as excinfo:
        _stage(con, table, source, lake_root, build_id="strict")
    assert excinfo.value.rejects == 1 and excinfo.value.allowed == 0
    assert not _dest(lake_root, table).exists()
    assert not paths.new_dir_for(_dest(lake_root, table)).exists()
    # the row-level rejects landed on the (temp) data root — count only, never read back
    assert stage.rejects_parquet_path(lake_root, table, "strict").is_file()

    # a deliberately raised threshold tolerates the crafted bad row
    relaxed = config.load_settings(loader_reject_max=10)
    result = _stage(con, table, source, lake_root, build_id="relaxed", settings=relaxed)
    assert result.rejects == 1 and result.rows == 1
    status = manifest_mod.read_status(lake_root)
    assert status["steps"][table.qualified_name]["rejects"] == 1


def test_csv_relation_sql_shape(contract: Contract, fixture_root: Path) -> None:
    table = contract.table(HOSP, "d_labitems")
    sql = loader_csv.csv_relation_sql(_fixture_source(fixture_root, table), table)
    assert sql.startswith("read_csv(")
    assert "columns={'itemid': 'INTEGER'" in sql
    assert "store_rejects=true" in sql
    assert "rejects_table='d_labitems_rejects'" in sql and "rejects_scan='d_labitems_scans'" in sql
    assert "allow_quoted_nulls=true" in sql
    assert "timestampformat" not in sql and "dateformat" not in sql  # ISO cast (EP-169)


# ---------------------------------------------------------------------------
# 5. paths.swap_dir (rename-aside two-step)
# ---------------------------------------------------------------------------


def _make_dir(root: Path, name: str, content: str) -> Path:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "part-0.parquet").write_text(content, encoding="utf-8")
    return d


def test_swap_dir_first_publish_and_restage(tmp_path: Path) -> None:
    dest = tmp_path / "table"
    new = _make_dir(tmp_path, "table.new", "v1")
    paths.swap_dir(new, dest)
    assert (dest / "part-0.parquet").read_text(encoding="utf-8") == "v1"
    assert not new.exists()
    new2 = _make_dir(tmp_path, "table.new", "v2")
    paths.swap_dir(new2, dest)  # over an existing dest (the Windows os.replace failure case)
    assert (dest / "part-0.parquet").read_text(encoding="utf-8") == "v2"
    assert not paths.old_dir_for(dest).exists() and not new2.exists()


def test_swap_dir_crash_recovery_and_errors(tmp_path: Path) -> None:
    dest = tmp_path / "table"
    _make_dir(tmp_path, "table.old", "crashed-live")  # interrupted swap left dest missing
    new = _make_dir(tmp_path, "table.new", "v3")
    paths.swap_dir(new, dest)
    assert (dest / "part-0.parquet").read_text(encoding="utf-8") == "v3"
    assert not paths.old_dir_for(dest).exists()
    with pytest.raises(paths.SwapError):
        paths.swap_dir(tmp_path / "does-not-exist", dest)
    with pytest.raises(paths.SwapError):
        paths.swap_dir(dest, dest)


def test_update_status_merges(tmp_path: Path) -> None:
    manifest_mod.update_status(tmp_path, "mimiciv_hosp.x", build_id="a", rows=1)
    manifest_mod.update_status(tmp_path, "mimiciv_hosp.x", build_id="b", rejects=0)
    manifest_mod.update_status(tmp_path, "mimiciv_hosp.y", build_id="a", rows=2)
    status = manifest_mod.read_status(tmp_path)
    x = status["steps"]["mimiciv_hosp.x"]
    assert x["build_id"] == "b" and x["rows"] == 1 and x["rejects"] == 0
    assert x["tier_complete"] is None and x["dev_ready"] is False
    assert status["steps"]["mimiciv_hosp.y"]["rows"] == 2


# ---------------------------------------------------------------------------
# 6. Column flags (identifier / free_text; keys.yaml `identifiers:` — item 9)
# ---------------------------------------------------------------------------


def test_identifier_and_free_text_flags(contract: Contract, fixture_manifest) -> None:
    assert contract.table(HOSP, "patients").identifier_columns() == ("subject_id",)
    lab = contract.table(HOSP, "labevents")
    assert set(lab.identifier_columns()) == {
        "labevent_id",
        "subject_id",
        "hadm_id",
        "specimen_id",
        "order_provider_id",
    }
    assert lab.free_text_columns() == ("comments",)
    assert contract.table("mimiciv_note", "discharge").free_text_columns() == ("text",)
    assert contract.table("mimiciv_icu", "caregiver").identifier_columns() == ("caregiver_id",)
    # dimension codes are deliberately NOT identifiers
    assert contract.table(HOSP, "d_labitems").identifier_columns() == ()
    # flags move content_hash only: the fixture manifest still pins the structural hash
    assert contract.structural_hash() == fixture_manifest["contract_schema_hash"]
    assert contract.content_hash() != contract.structural_hash()


@pytest.fixture
def tables_copy(tmp_path: Path) -> Path:
    copy = tmp_path / "tables"
    shutil.copytree(tables_root(), copy)
    return copy


def test_keys_yaml_identifier_validation(tables_copy: Path) -> None:
    keys = tables_copy / "keys.yaml"
    text = keys.read_text(encoding="utf-8")
    keys.write_text(text.replace("names: [", "names: [not_a_column_xyz,"), encoding="utf-8")
    with pytest.raises(SchemaError, match="not_a_column_xyz"):
        load_contract_from(tables_copy)


def test_keys_yaml_free_text_validation(tables_copy: Path) -> None:
    keys = tables_copy / "keys.yaml"
    text = keys.read_text(encoding="utf-8")
    keys.write_text(
        text.replace("mimiciv_hosp.labevents: [comments]", "mimiciv_hosp.labevents: [commentz]"),
        encoding="utf-8",
    )
    with pytest.raises(SchemaError, match="commentz"):
        load_contract_from(tables_copy)


# ---------------------------------------------------------------------------
# 7. Dev tier: the two real dimension files + the TIMESTAMP(3) probe
# ---------------------------------------------------------------------------


@pytest.fixture
def dev_lake(raw_root: Path):
    """A temp lake under <data_root>/tmp/ep17 (deleted at teardown) + real settings."""
    settings = config.load_settings()
    lake = settings.layout["tmp"] / "ep17"
    if lake.exists():
        shutil.rmtree(lake)
    lake.mkdir(parents=True)
    yield settings, lake
    shutil.rmtree(lake, ignore_errors=True)


@pytest.mark.tier("dev", needs="raw")
def test_dev_stage_real_dimensions(contract: Contract, dev_lake) -> None:
    from mimicwarehouse.inventory import load_raw_manifest, rel_path_for

    settings, lake = dev_lake
    raw_manifest = load_raw_manifest(settings)
    con = engine.open_build_connection(settings, tier="dev", memory_limit="8GB")
    try:
        for table_name in ("d_labitems", "patients"):
            table = contract.table(HOSP, table_name)
            record = raw_manifest.for_table(table)
            if record is None or record.rows is None:
                pytest.skip(f"EP-10 raw manifest has no row count for {table.qualified_name}")
            source = settings.source_root / rel_path_for(table)
            result = stage.stage_unpartitioned(
                con,
                table,
                source,
                lake / "core" / table.schema_name / table.name,
                lake_root=lake,
                build_id="ep17-dev",
                settings=settings,
                source_sha256=record.sha256,
                raw_snapshot_id=raw_manifest.raw_snapshot_id,
            )
            assert result.rows == record.rows
            assert result.rejects == 0
            line = result.manifest_lines[0]
            assert line.source_sha256 == record.sha256
            assert line.raw_snapshot_id == raw_manifest.raw_snapshot_id
        assert manifest_mod.manifest_path(lake, "ep17-dev").is_file()
        assert manifest_mod.status_path(lake).is_file()
    finally:
        con.close()


@pytest.mark.tier("dev", needs="raw")
def test_dev_timestamp3_fractional_seconds_probe(contract: Contract, raw_root: Path) -> None:
    """The SCH-1 question, answered by counts: max(length) of the nine upstream TIMESTAMP(3)
    columns is 19 (no fractional seconds) or 21-23 (fractional seconds present) — either way
    the dialect's ISO cast handles it; a length outside that band fails loudly here, before
    any full-tier run under loader_reject_max = 0."""
    from mimicwarehouse.inventory import rel_path_for

    settings = config.load_settings()
    con = engine.open_build_connection(settings, tier="dev", memory_limit="8GB")
    probed = 0
    try:
        for table_name, schema in (
            ("pharmacy", HOSP),
            ("prescriptions", HOSP),
            ("outputevents", "mimiciv_icu"),
        ):
            table = contract.table(schema, table_name)
            cols = [c.name for c in table.columns if c.upstream_type == "TIMESTAMP(3)"]
            assert cols, f"{table.qualified_name}: expected recorded TIMESTAMP(3) columns"
            source = settings.source_root / rel_path_for(table)
            lengths = loader_csv.max_length_probe(con, source, cols)
            for col, maxlen in lengths.items():
                assert maxlen is None or maxlen == 19 or 21 <= maxlen <= 23, (
                    f"{table.qualified_name}.{col}: max timestamp length {maxlen}"
                )
                probed += 1
    finally:
        con.close()
    assert probed == 9  # pharmacy 5 + prescriptions 2 + outputevents 2
