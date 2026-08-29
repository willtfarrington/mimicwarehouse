"""EP-18 — Loader core B: subject buckets, per-bucket sort, resume.

Fixture tier (default): every stage goes against a temp data root (``helpers.tmp_data_root``)
and either the committed synthetic fixture ``admissions`` CSV or a crafted 200-subject CSV
(ids 90 000 000 - 90 000 199 → exactly two subjects per bucket, all 100 buckets covered —
the fixture's *admissions* are not guaranteed to hit buckets 0-4). Dev tier (``--tier dev``):
stages real ``mimiciv_hosp.admissions`` restricted to ``settings.dev_buckets`` into a temp
lake under ``<data_root>/tmp/ep18`` (deleted at teardown) — counts, schemas and hashes
only; no row-level output anywhere.
"""

from __future__ import annotations

import json
import logging
import shutil
import time
from pathlib import Path
from typing import Any

import pytest

import helpers
from mimicwarehouse import config, paths
from mimicwarehouse.loader import buckets as buckets_mod
from mimicwarehouse.loader import engine, stage
from mimicwarehouse.loader import manifest as manifest_mod
from mimicwarehouse.loader import paths as loader_paths
from mimicwarehouse.loader.buckets import stage_partitioned
from mimicwarehouse.schema.contract import Contract, Table

pytestmark = pytest.mark.ep_18

HOSP = "mimiciv_hosp"
LOGGER = "mimicwarehouse.loader.buckets"

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


@pytest.fixture(scope="session")
def admissions(contract: Contract) -> Table:
    return contract.table(HOSP, "admissions")


@pytest.fixture
def crafted_csv(tmp_path: Path, admissions: Table) -> Path:
    """200 one-admission subjects, ids 90 000 000 - 90 000 199 → 2 rows in every bucket.

    Only ``subject_id`` / ``hadm_id`` / ``admittime`` (+ two flavour columns) are filled;
    the rest are empty → typed NULLs under the contract read (no rejects). ``admittime``
    varies within a bucket so the per-bucket sort has work to do.
    """
    cols = list(admissions.column_names)
    lines = [",".join(cols)]
    for i in range(200):
        row = dict.fromkeys(cols, "")
        row["subject_id"] = str(90_000_000 + i)
        row["hadm_id"] = str(91_000_000 + i)
        # descending within the file so the ORDER BY provably reorders
        row["admittime"] = f"2130-01-{28 - (i % 28):02d} {23 - (i % 24):02d}:00:00"
        row["admission_type"] = "EW EMER."
        row["hospital_expire_flag"] = "0"
        lines.append(",".join(row[c] for c in cols))
    path = tmp_path / "crafted_admissions.csv"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return path


def _dest(lake_root: Path, table: Table, name: str = "core") -> Path:
    return lake_root / name / table.schema_name / table.name


def _stage(
    con, table: Table, source: Path, lake_root: Path, dest: Path | None = None, **kwargs
) -> stage.StageResult:
    return stage_partitioned(
        con,
        table,
        source,
        dest if dest is not None else _dest(lake_root, table),
        lake_root=lake_root,
        **kwargs,
    )


def _partition_dirs(dest: Path) -> dict[int, Path]:
    return {
        int(p.name.split("=", 1)[1]): p
        for p in dest.iterdir()
        if p.is_dir() and p.name.startswith("subject_bucket=")
    }


def _bucket_shas(dest: Path) -> dict[int, str]:
    return {
        n: manifest_mod.sha256_streamed(d / stage.PART_FILENAME)
        for n, d in _partition_dirs(dest).items()
    }


def _assert_clean_layout(dest: Path) -> None:
    """Exactly one part-0.parquet per partition dir; no raw_* / _sorting.tmp anywhere."""
    dirs = _partition_dirs(dest)
    assert dirs, f"no partition dirs under {dest}"
    for d in dirs.values():
        assert [p.name for p in sorted(d.iterdir())] == [stage.PART_FILENAME]
    assert not paths.new_dir_for(dest).exists() and not paths.old_dir_for(dest).exists()


def _assert_sorted(con, part: Path, sort_cols: tuple[str, ...]) -> None:
    """Lag query over the file order: every row's sort tuple >= its predecessor's."""
    tup = f"row({', '.join(sort_cols)})"
    escaped = part.as_posix().replace("'", "''")
    (ok,) = con.execute(
        f"SELECT bool_and(cur >= prev) FROM ("
        f"SELECT {tup} AS cur, lag({tup}, 1, {tup}) OVER (ORDER BY file_row_number) AS prev "
        f"FROM read_parquet('{escaped}', file_row_number=true, hive_partitioning=false))"
    ).fetchone()
    assert ok is True


def _max_row_group(con, part: Path) -> int:
    escaped = part.as_posix().replace("'", "''")
    (mx,) = con.execute(
        f"SELECT max(row_group_num_rows) FROM parquet_metadata('{escaped}')"
    ).fetchone()
    return int(mx)


# ---------------------------------------------------------------------------
# 1. Small path: layout, counts, sortedness, manifests, status
# ---------------------------------------------------------------------------


def test_small_path_fixture_admissions(
    con,
    admissions: Table,
    fixture_root: Path,
    fixture_manifest: dict[str, Any],
    lake_root: Path,
    settings: config.Settings,
) -> None:
    source = fixture_root / "mimic-iv-3.1" / admissions.csv_path
    result = _stage(con, admissions, source, lake_root, build_id="b1", settings=settings)

    expected = fixture_manifest["files"][f"mimic-iv-3.1/{admissions.csv_path}"]["rows"]
    assert result.rows == expected and result.rejects == 0 and result.wall_s > 0

    dest = _dest(lake_root, admissions)
    _assert_clean_layout(dest)
    dirs = _partition_dirs(dest)
    assert result.files == len(dirs)
    # partition dirs are DuckDB's un-padded names; every subject landed in its bucket
    assert all(0 <= n < buckets_mod.NUM_BUCKETS for n in dirs)
    rel = loader_paths.read_parquet_sql(lake_root, HOSP, "admissions")
    (ok,) = con.execute(f"SELECT bool_and(subject_id % 100 = subject_bucket) FROM {rel}").fetchone()
    assert ok is True
    for d in dirs.values():
        part = d / stage.PART_FILENAME
        _assert_sorted(con, part, ("subject_id", "admittime"))
        assert _max_row_group(con, part) <= 1_000_000

    # one manifest line per partition file; status has tier_complete = "full" (all buckets)
    assert len(result.manifest_lines) == len(dirs)
    lines = [
        manifest_mod.ManifestLine.model_validate_json(line)
        for line in manifest_mod.manifest_path(lake_root, "b1")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert {ln.path for ln in lines} == {
        f"core/{HOSP}/admissions/subject_bucket={n}/{stage.PART_FILENAME}" for n in dirs
    }
    entry = manifest_mod.read_status(lake_root)["steps"][admissions.qualified_name]
    assert entry["rows"] == expected and entry["files"] == len(dirs)
    assert entry["tier_complete"] == "full" and entry["dev_ready"] is True

    progress = buckets_mod.read_progress(dest)
    assert progress is not None and progress.complete and progress.pass1_done


# ---------------------------------------------------------------------------
# 2. Large path == small path; sweeps; determinism
# ---------------------------------------------------------------------------


def test_large_path_and_sweeps_match_small_path(
    con, admissions: Table, fixture_root: Path, lake_root: Path, settings: config.Settings
) -> None:
    source = fixture_root / "mimic-iv-3.1" / admissions.csv_path
    small = _stage(
        con,
        admissions,
        source,
        lake_root,
        _dest(lake_root, admissions, "small_run"),
        build_id="s",
        settings=settings,
    )
    large = _stage(
        con,
        admissions,
        source,
        lake_root,
        _dest(lake_root, admissions, "large_run"),
        build_id="l",
        settings=settings,
        size_class="large",
    )
    swept = _stage(
        con,
        admissions,
        source,
        lake_root,
        _dest(lake_root, admissions, "swept_run"),
        build_id="w",
        settings=settings,
        size_class="large",
        sweeps=2,
    )
    again = _stage(
        con,
        admissions,
        source,
        lake_root,
        _dest(lake_root, admissions, "again_run"),
        build_id="a",
        settings=settings,
        size_class="large",
    )
    assert small.rows == large.rows == swept.rows == again.rows
    shas_small = _bucket_shas(_dest(lake_root, admissions, "small_run"))
    shas_large = _bucket_shas(_dest(lake_root, admissions, "large_run"))
    shas_swept = _bucket_shas(_dest(lake_root, admissions, "swept_run"))
    shas_again = _bucket_shas(_dest(lake_root, admissions, "again_run"))
    # identical per-bucket sha256 sets across the two paths, sweeps, and repeat runs
    assert shas_small == shas_large == shas_swept == shas_again
    for name in ("small_run", "large_run", "swept_run", "again_run"):
        _assert_clean_layout(_dest(lake_root, admissions, name))
    # the large path appended one manifest line per non-empty bucket
    assert len(large.manifest_lines) == len(shas_large)


# ---------------------------------------------------------------------------
# 3. Dev buckets: 5 partitions only; dev-ready logged before complete
# ---------------------------------------------------------------------------


def test_dev_buckets_writes_five_partitions_dev_ready_first(
    con,
    admissions: Table,
    crafted_csv: Path,
    lake_root: Path,
    settings: config.Settings,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger=LOGGER)
    result = _stage(
        con,
        admissions,
        crafted_csv,
        lake_root,
        build_id="dev",
        settings=settings,
        size_class="large",
        buckets=settings.dev_buckets,
    )
    dest = _dest(lake_root, admissions)
    dirs = _partition_dirs(dest)
    assert sorted(dirs) == settings.dev_buckets  # exactly the 5 dev partition dirs
    assert result.rows == 2 * len(settings.dev_buckets)  # 2 crafted subjects per bucket
    _assert_clean_layout(dest)

    messages = [r.message for r in caplog.records if r.name == LOGGER]
    dev_ready_at = next(i for i, m in enumerate(messages) if m.startswith("dev-ready"))
    complete_at = next(i for i, m in enumerate(messages) if m.startswith("complete"))
    assert dev_ready_at < complete_at
    sorted_lines = [m for m in messages if " sorted rows=" in m]
    assert len(sorted_lines) == len(settings.dev_buckets)

    entry = manifest_mod.read_status(lake_root)["steps"][admissions.qualified_name]
    assert entry["dev_ready"] is True and entry["tier_complete"] == "dev"

    # a later FULL request over the dev-complete table restages the whole table
    full = _stage(
        con,
        admissions,
        crafted_csv,
        lake_root,
        build_id="full",
        settings=settings,
        size_class="large",
    )
    assert full.rows == 200
    assert sorted(_partition_dirs(dest)) == list(range(buckets_mod.NUM_BUCKETS))
    entry = manifest_mod.read_status(lake_root)["steps"][admissions.qualified_name]
    assert entry["tier_complete"] == "full"


# ---------------------------------------------------------------------------
# 4. Resume: skip sorted buckets, discard a stale _sorting.tmp
# ---------------------------------------------------------------------------


def test_resume_skips_sorted_buckets_and_discards_stale_tmp(
    settings: config.Settings,
    admissions: Table,
    crafted_csv: Path,
    lake_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dest = _dest(lake_root, admissions)
    dev = settings.dev_buckets
    real_sort = buckets_mod._sort_bucket
    calls: list[Path] = []

    def crash_after_two(con, bucket_dir, order_by):
        if len(calls) == 2:
            raise RuntimeError("simulated crash (EP-18 resume test)")
        calls.append(bucket_dir)
        return real_sort(con, bucket_dir, order_by)

    monkeypatch.setattr(buckets_mod, "_sort_bucket", crash_after_two)
    con1 = engine.open_build_connection(settings, tier="fixture", memory_limit="4GB")
    try:
        with pytest.raises(RuntimeError, match="simulated crash"):
            _stage(
                con1,
                admissions,
                crafted_csv,
                lake_root,
                build_id="crash",
                settings=settings,
                size_class="large",
                buckets=dev,
            )
    finally:
        con1.close()  # the simulated crash drops the connection

    progress = buckets_mod.read_progress(dest)
    assert progress is not None and progress.pass1_done and not progress.complete
    assert len(progress.sorted_buckets) == 2 and not progress.dev_ready
    sorted_set = set(progress.sorted_buckets)
    unsorted = [b for b in dev if b not in sorted_set]
    # leave a stale _sorting.tmp in the first unsorted bucket, as a crash would
    stale = dest / f"subject_bucket={unsorted[0]}" / buckets_mod.SORTING_TMP
    stale.write_bytes(b"stale")

    recorded: list[Path] = []

    def recording(con, bucket_dir, order_by):
        recorded.append(bucket_dir)
        return real_sort(con, bucket_dir, order_by)

    monkeypatch.setattr(buckets_mod, "_sort_bucket", recording)
    con2 = engine.open_build_connection(settings, tier="fixture", memory_limit="4GB")
    try:
        result = _stage(
            con2,
            admissions,
            crafted_csv,
            lake_root,
            build_id="resume",
            settings=settings,
            size_class="large",
            buckets=dev,
        )
    finally:
        con2.close()

    # only the unsorted buckets were processed; the stale tmp is gone; layout is clean
    assert [int(p.name.split("=", 1)[1]) for p in recorded] == unsorted
    assert not stale.exists()
    _assert_clean_layout(dest)
    assert result.rows == 2 * len(dev)
    progress = buckets_mod.read_progress(dest)
    assert progress is not None and progress.complete and progress.dev_ready
    assert sorted(progress.sorted_buckets) == dev


# ---------------------------------------------------------------------------
# 5. Reader helpers: the layout is encoded in exactly one place
# ---------------------------------------------------------------------------


def test_partition_glob_and_read_parquet_sql(lake_root: Path) -> None:
    glob = loader_paths.partition_glob(lake_root, HOSP, "admissions")
    assert "\\" not in glob and glob.endswith(
        f"core/{HOSP}/admissions/subject_bucket=*/part-*.parquet"
    )
    assert Path(glob).is_absolute()

    plain = loader_paths.read_parquet_sql(lake_root, HOSP, "admissions")
    assert plain.startswith("read_parquet('")
    assert "hive_partitioning = true" in plain
    assert "hive_types = {'subject_bucket': INTEGER}" in plain
    assert "WHERE" not in plain

    filtered = loader_paths.read_parquet_sql(lake_root, HOSP, "admissions", buckets=[4, 0, 2])
    assert filtered.startswith("(SELECT * FROM read_parquet(")
    assert filtered.endswith("WHERE subject_bucket IN (0, 2, 4))")


def test_read_parquet_sql_filters_buckets(
    con, admissions: Table, crafted_csv: Path, lake_root: Path, settings: config.Settings
) -> None:
    _stage(con, admissions, crafted_csv, lake_root, build_id="rq", settings=settings)
    rel = loader_paths.read_parquet_sql(lake_root, HOSP, "admissions", buckets=settings.dev_buckets)
    (rows,) = con.execute(f"SELECT count(*) FROM {rel}").fetchone()
    assert rows == 2 * len(settings.dev_buckets)
    (total,) = con.execute(
        f"SELECT count(*) FROM {loader_paths.read_parquet_sql(lake_root, HOSP, 'admissions')}"
    ).fetchone()
    assert total == 200


# ---------------------------------------------------------------------------
# 6. Guards + heartbeat
# ---------------------------------------------------------------------------


def test_unpartitioned_table_is_refused(
    con, contract: Contract, crafted_csv: Path, lake_root: Path, settings: config.Settings
) -> None:
    dim = contract.table(HOSP, "d_labitems")
    with pytest.raises(stage.StageError, match="not partitioned"):
        _stage(con, dim, crafted_csv, lake_root, build_id="x", settings=settings)


def test_bad_buckets_and_sweeps_are_refused(
    con, admissions: Table, crafted_csv: Path, lake_root: Path, settings: config.Settings
) -> None:
    with pytest.raises(stage.StageError, match="buckets"):
        _stage(
            con,
            admissions,
            crafted_csv,
            lake_root,
            build_id="x",
            settings=settings,
            buckets=[100],
        )
    with pytest.raises(stage.StageError, match="sweeps"):
        _stage(
            con,
            admissions,
            crafted_csv,
            lake_root,
            build_id="x",
            settings=settings,
            sweeps=0,
        )


def test_heartbeat_logs_counts_only(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger=LOGGER)
    hb = buckets_mod._Heartbeat("mimiciv_hosp.admissions", tmp_path, tmp_path, 0.01)
    hb.start()
    time.sleep(0.1)
    hb.stop()
    hb.join(timeout=5)
    lines = [r.message for r in caplog.records if r.message.startswith("pass1 ")]
    assert lines and all("rss=" in ln and "tmp_duckdb=" in ln and "written=" in ln for ln in lines)


# ---------------------------------------------------------------------------
# 7. Dev tier: real admissions, dev buckets only, temp lake, deleted at teardown
# ---------------------------------------------------------------------------


@pytest.fixture
def dev_lake(raw_root: Path):
    settings = config.load_settings()
    lake = settings.layout["tmp"] / "ep18"
    if lake.exists():
        shutil.rmtree(lake)
    lake.mkdir(parents=True)
    yield settings, lake
    shutil.rmtree(lake, ignore_errors=True)


@pytest.mark.tier("dev", needs="raw")
def test_dev_stage_real_admissions_dev_buckets(admissions: Table, dev_lake) -> None:
    from mimicwarehouse.inventory import load_raw_manifest, rel_path_for
    from mimicwarehouse.loader.csv import csv_relation_sql

    settings, lake = dev_lake
    raw_manifest = load_raw_manifest(settings)
    record = raw_manifest.for_table(admissions)
    if record is None:
        pytest.skip("EP-10 raw manifest has no record for mimiciv_hosp.admissions")
    source = settings.source_root / rel_path_for(admissions)
    dev = settings.dev_buckets
    con = engine.open_build_connection(settings, tier="dev", memory_limit="8GB")
    try:
        dest = lake / "core" / admissions.schema_name / admissions.name
        result = stage_partitioned(
            con,
            admissions,
            source,
            dest,
            lake_root=lake,
            build_id="ep18-dev",
            settings=settings,
            source_sha256=record.sha256,
            raw_snapshot_id=raw_manifest.raw_snapshot_id,
            buckets=dev,
        )
        assert result.rejects == 0
        dirs = _partition_dirs(dest)
        assert sorted(dirs) == dev  # five partitions
        _assert_clean_layout(dest)
        # rows equal a count over the same typed read, filtered by bucket — in-process only
        in_list = ", ".join(str(b) for b in dev)
        row = con.execute(
            f"SELECT count(*) FROM {csv_relation_sql(source, admissions)} "
            f"WHERE subject_id % 100 IN ({in_list})"
        ).fetchone()
        assert row is not None
        assert result.rows == int(row[0]) and result.rows > 0
        for d in dirs.values():
            _assert_sorted(con, d / stage.PART_FILENAME, ("subject_id", "admittime"))
            assert _max_row_group(con, d / stage.PART_FILENAME) <= 1_000_000
        entry = manifest_mod.read_status(lake)["steps"][admissions.qualified_name]
        assert entry["dev_ready"] is True and entry["tier_complete"] == "dev"
        for line in result.manifest_lines:
            assert line.source_sha256 == record.sha256
            assert line.raw_snapshot_id == raw_manifest.raw_snapshot_id
    finally:
        con.close()
