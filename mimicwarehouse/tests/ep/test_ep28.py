"""EP-28 — verify full staging: the verifying brief for the P2 ⏱ background jobs.

Fixture tier (default): ``benchmarks.summarize()`` (the new EP-28 item-5 API used by the
completion notes and EP-32) is exercised against an empty ledger and against the session
fixture lake's real ledger (one row per stage step, pass1/pass2 walls on the large
steps), and the structural lake checks run against the runner-built fixture lake so the
checker logic itself is proven on synthetic data.

``tier("full", needs="lake")``-marked (items 1-5): the five ⏱ jobs are ``done`` with
exit 0 and ``snapshots.json`` carries a ``full`` entry newer than the last of them;
all 31 hosp+icu tables are structurally complete (``tier_complete = "full"``, bucket
dirs each holding exactly one ``part-0.parquet``, no ``raw_*``/``_sorting.tmp``/``.new``
leftovers, every latest manifest line's path existing with matching byte size); the
count reconciliation (``status.json`` rows == vendored ``validate.sql`` expected where
an ``expected_rows_source`` exists [FC-12: provider/caregiver/ingredientevents have
none] == EP-10 raw-manifest rows == the manifest-line sum, rejects 0) prints the
``(table, expected, lake, ok)`` table; the disk/file measurement appends ``kind:
verify`` lines to ``runs/benchmarks.jsonl`` (item 4) and asserts the free-space floor.
``tier("dev")``-marked: dev ⊂ full — every partitioned table's dev-catalog ``count(*)``
equals the manifest-row sum over ``settings.dev_buckets`` (no full-tier scan), and the
dims are identical (same counts) in both catalogs.

Everything printed or asserted is counts, bytes, paths and booleans — never a row,
never an identifier value (GOVERNANCE §4).
"""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

import pytest

import helpers
from mimicwarehouse import config, paths
from mimicwarehouse.config import Settings
from mimicwarehouse.dag import benchmarks as benchmarks_mod
from mimicwarehouse.dag import jobs as jobs_mod
from mimicwarehouse.dag import snapshot as snapshot_mod
from mimicwarehouse.dag.runner import new_build_id
from mimicwarehouse.dag.spec import load_dag
from mimicwarehouse.inventory import expected_counts, fmt_int, load_raw_manifest
from mimicwarehouse.loader import manifest as manifest_mod
from mimicwarehouse.loader import paths as loader_paths
from mimicwarehouse.loader.buckets import BUCKET_COLUMN, PROGRESS_FILENAME
from mimicwarehouse.schema.contract import Contract, Table

pytestmark = pytest.mark.ep_28

HOSP = "mimiciv_hosp"
ICU = "mimiciv_icu"
PART = "part-0.parquet"
#: The five ⏱ background jobs this brief verifies (EP-23 … EP-27 launch notes).
STAGE_JOBS = (
    "stage-labevents-full",
    "stage-emar-full",
    "stage-hosp-rest-full",
    "stage-chartevents-full",
    "stage-icu-events-full",
)


def _staged_tables(contract: Contract) -> list[Table]:
    """The 31 hosp + icu contract tables P2 stages (EP-27 proved the DAG covers them)."""
    tables = [t for t in contract.tables if t.schema_name in (HOSP, ICU)]
    assert len(tables) == 31
    return tables


def _latest_lines(lake: Path) -> dict[str, dict[str, Any]]:
    """Newest manifest line per lake-relative path across every build's jsonl (the
    EP-19 snapshot semantic: a restaged table contributes its latest line per path)."""
    latest: dict[str, dict[str, Any]] = {}
    mdir = manifest_mod.manifests_dir(lake)
    for jsonl in sorted(mdir.glob("*.jsonl")) if mdir.is_dir() else []:
        for raw in jsonl.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            rec = json.loads(raw)
            have = latest.get(rec["path"])
            if have is None or rec["ts"] >= have["ts"]:
                latest[rec["path"]] = rec
    return latest


def _bucket_of(path: str) -> int | None:
    """The ``subject_bucket=<n>`` value in a manifest path, or None (unpartitioned)."""
    marker = f"{BUCKET_COLUMN}="
    for part in path.split("/"):
        if part.startswith(marker):
            return int(part[len(marker) :])
    return None


def _structural_check(settings: Settings, contract: Contract, tier: str) -> None:
    """Brief item 2, shared between the fixture lake (checker proof) and the real lake:
    status complete, clean partition layout, manifest lines matching the files."""
    lake = settings.lake_root(tier)
    status = manifest_mod.read_status(lake)["steps"]
    for table in _staged_tables(contract):
        qn = table.qualified_name
        entry = status.get(qn)
        assert entry is not None and entry["tier_complete"] == "full", qn
        assert entry.get("rejects", 0) == 0, qn
        tdir = loader_paths.table_dir(lake, table.schema_name, table.name)
        assert tdir.is_dir(), qn
        assert not paths.new_dir_for(tdir).exists(), qn
        assert not paths.old_dir_for(tdir).exists(), qn
        entries = list(tdir.iterdir())
        if table.partitioned:
            bucket_dirs = [p for p in entries if p.is_dir()]
            stray = [p.name for p in entries if not p.is_dir() and p.name != PROGRESS_FILENAME]
            assert not stray, f"{qn}: unexpected files in the table dir: {stray}"
            assert 1 <= len(bucket_dirs) <= 100, qn
            seen: set[int] = set()
            for bdir in bucket_dirs:
                marker, _, num = bdir.name.partition("=")
                assert marker == BUCKET_COLUMN and num.isdigit() and 0 <= int(num) <= 99, (
                    f"{qn}: unexpected directory {bdir.name}"
                )
                seen.add(int(num))
                names = [p.name for p in bdir.iterdir()]
                # exactly one published file: no raw_* / _sorting.tmp leftovers
                assert names == [PART], f"{qn}/{bdir.name}: {names}"
            assert len(seen) == len(bucket_dirs), f"{qn}: duplicate bucket numbers"
            assert entry["files"] == len(bucket_dirs), qn
        else:
            names = sorted(p.name for p in entries)
            assert names == [PART], f"{qn} (dim): {names}"

    latest = _latest_lines(lake)
    assert latest, f"no manifest lines under {lake}"
    for rel, rec in latest.items():
        f = lake / Path(*rel.split("/"))
        assert f.is_file(), f"manifest path missing on disk: {rel}"
        assert f.stat().st_size == rec["bytes"], f"manifest bytes != file size: {rel}"
    # conversely: every published part file is covered by a manifest line
    for table in _staged_tables(contract):
        tdir = loader_paths.table_dir(lake, table.schema_name, table.name)
        for f in tdir.rglob(PART):
            rel = manifest_mod.lake_relative_posix(f, lake)
            assert rel in latest, f"file on disk without a manifest line: {rel}"


# ---------------------------------------------------------------------------
# 1. summarize() — the item-5 API (fixture tier)
# ---------------------------------------------------------------------------


@pytest.fixture
def data_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    root = helpers.tmp_data_root(monkeypatch, tmp_path)
    yield root
    config.configure()


def test_summarize_empty_ledger(data_root: Path) -> None:
    df = benchmarks_mod.summarize()
    assert df.is_empty()


def test_summarize_fixture_lake(fixture_lake_settings: Settings, contract: Contract) -> None:
    df = benchmarks_mod.summarize(fixture_lake_settings)
    assert set(df["tier"].to_list()) == {"fixture"}
    steps = {s.name for s in load_dag().steps if s.kind == "stage"}
    assert set(df["step"].to_list()) == steps, "one row per stage step"
    assert all(w > 0 for w in df["wall_s"].to_list())
    assert all(df["ok"].to_list())
    by_step = {row["step"]: row for row in df.to_dicts()}
    for table in _staged_tables(contract):
        row = by_step[f"stage.{table.qualified_name}"]
        assert row["rows"] is not None and row["bytes_out"] is not None
        if table.load_class == "large":
            # the large two-pass path writes pass1 + pass2 phase lines (EP-23)
            assert row["pass1_wall_s"] is not None and row["pass2_wall_s"] is not None
        assert row["mb_in_per_s"] is None or row["mb_in_per_s"] >= 0
    # kind filter: the catalog step only appears when asked for
    assert "catalog" not in set(df["step"].to_list())
    with_catalog = benchmarks_mod.summarize(fixture_lake_settings, kind=None)
    assert "catalog" in set(with_catalog["step"].to_list())


def test_fixture_lake_structural(fixture_lake_settings: Settings, contract: Contract) -> None:
    _structural_check(fixture_lake_settings, contract, "fixture")
    # the runner recorded a fixture-tier core snapshot for its build
    entries = snapshot_mod.read_snapshots(fixture_lake_settings.lake_root("fixture"))
    assert any(e["layer"] == "core" and e["tier"] == "fixture" for e in entries)


# ---------------------------------------------------------------------------
# 2. Full tier: jobs, snapshot, structure (brief items 1-2)
# ---------------------------------------------------------------------------


@pytest.mark.tier("full", needs="lake")
def test_full_jobs_done_and_snapshot_current() -> None:
    settings = config.get_settings()
    finishes: list[str] = []
    for job in STAGE_JOBS:
        info = jobs_mod.read_job(job, settings)
        assert info is not None, f"job {job} has no state file"
        assert info.state == "done" and info.exit_code == 0, f"{job}: {info.state}"
        assert info.finished is not None, job
        finishes.append(info.finished)
    entries = snapshot_mod.read_snapshots(settings.lake_root("full"))
    full_ts = [e["ts"] for e in entries if e["layer"] == "core" and e["tier"] == "full"]
    assert full_ts, "snapshots.json has no core/full entry"
    # ISO-8601 UTC strings in one format — lexicographic order is chronological
    assert max(full_ts) >= max(finishes), "core/full snapshot predates the last ⏱ job"


@pytest.mark.tier("full", needs="lake")
def test_full_structural(contract: Contract) -> None:
    _structural_check(config.get_settings(), contract, "full")


# ---------------------------------------------------------------------------
# 3. Count reconciliation (brief item 3)
# ---------------------------------------------------------------------------


@pytest.mark.tier("full", needs="lake")
def test_full_count_reconciliation(contract: Contract) -> None:
    settings = config.get_settings()
    lake = settings.lake_root("full")
    status = manifest_mod.read_status(lake)["steps"]
    expected = expected_counts("mimic-iv-3.1", contract)
    raw = load_raw_manifest(settings)
    manifest_rows: dict[str, int] = {}
    for rec in _latest_lines(lake).values():
        if rec["path"].startswith(f"{loader_paths.CORE_LAYER}/"):
            qn = f"{rec['schema']}.{rec['table']}"
            manifest_rows[qn] = manifest_rows.get(qn, 0) + rec["rows"]

    print(f"{'table':<36} {'expected':>15} {'lake':>15} ok")
    all_ok = True
    for table in _staged_tables(contract):
        qn = table.qualified_name
        lake_rows = status[qn]["rows"]
        exp = expected.get(table.name) if table.expected_rows_source else None
        record = raw.for_table(table)
        assert record is not None and record.rows is not None, f"{qn}: no EP-10 raw record"
        ok = (
            (exp is None or exp == lake_rows)
            and record.rows == lake_rows
            and manifest_rows.get(qn) == lake_rows
            and status[qn].get("rejects", 0) == 0
        )
        all_ok = all_ok and ok
        print(f"{qn:<36} {fmt_int(exp):>15} {fmt_int(lake_rows):>15} {'ok' if ok else 'MISMATCH'}")
    assert all_ok, "at least one table failed reconciliation (see the printed table)"


@pytest.mark.tier("dev")
def test_dev_is_subset_of_full(dev_catalog: Path, full_catalog: Path, contract: Contract) -> None:
    from mimicwarehouse.catalog.connect import open_catalog

    settings = config.get_settings()
    lake = settings.lake_root("dev")  # dev and full share the credentialed lake root
    dev_buckets = set(settings.dev_buckets)
    dev_rows: dict[str, int] = {}
    for rec in _latest_lines(lake).values():
        bucket = _bucket_of(rec["path"])
        if bucket is not None and bucket in dev_buckets:
            qn = f"{rec['schema']}.{rec['table']}"
            dev_rows[qn] = dev_rows.get(qn, 0) + rec["rows"]

    dims = [t for t in _staged_tables(contract) if not t.partitioned]
    partitioned = [t for t in _staged_tables(contract) if t.partitioned]
    con = open_catalog("dev", settings=settings)
    try:
        for table in partitioned:
            qn = table.qualified_name
            (total,) = con.execute(f"SELECT count(*) FROM {qn}").fetchone()  # type: ignore[misc]
            assert total == dev_rows.get(qn), (
                f"dev view count {total} != manifest rows {dev_rows.get(qn)} for {qn}, "
                f"buckets {sorted(dev_buckets)}"
            )
        dev_dim_counts = {
            t.qualified_name: con.execute(  # type: ignore[index]
                f"SELECT count(*) FROM {t.qualified_name}"
            ).fetchone()[0]
            for t in dims
        }
    finally:
        con.close()
    con = open_catalog("full", settings=settings)
    try:
        for table in dims:
            qn = table.qualified_name
            (total,) = con.execute(f"SELECT count(*) FROM {qn}").fetchone()  # type: ignore[misc]
            assert total == dev_dim_counts[qn], f"dim {qn} differs between dev and full catalogs"
    finally:
        con.close()


@pytest.mark.tier("full")
def test_full_catalog_lists_all_tables(full_catalog: Path) -> None:
    from mimicwarehouse.catalog.connect import open_catalog

    settings = config.get_settings()
    con = open_catalog("full", settings=settings)
    try:
        kinds = dict(
            con.execute("SELECT kind, count(*) FROM meta.catalog_tables GROUP BY kind").fetchall()
        )
        (snapshot_id,) = con.execute(  # type: ignore[misc]
            "SELECT core_snapshot_id FROM meta.catalog_info"
        ).fetchone()
    finally:
        con.close()
    assert kinds.get("missing", 0) == 0, f"missing tables in the full catalog: {kinds}"
    assert kinds.get("table", 0) + kinds.get("view", 0) == 31, kinds
    # the catalog was built from the lake state the snapshot history knows about
    entries = snapshot_mod.read_snapshots(settings.lake_root("full"))
    known = {e["snapshot_id"] for e in entries if e["layer"] == "core" and e["tier"] == "full"}
    assert snapshot_id in known


# ---------------------------------------------------------------------------
# 4. Disk + files + the kind: verify ledger lines (brief item 4)
# ---------------------------------------------------------------------------


@pytest.mark.tier("full", needs="lake")
def test_full_disk_files_and_verify_lines(contract: Contract, record_property: Any) -> None:
    import duckdb

    settings = config.get_settings()
    lake = settings.lake_root("full")
    status = manifest_mod.read_status(lake)["steps"]
    latest = [
        rec
        for rec in _latest_lines(lake).values()
        if rec["path"].startswith(f"{loader_paths.CORE_LAYER}/")
    ]
    raw = load_raw_manifest(settings)

    per_table: dict[str, dict[str, int]] = {}
    for rec in latest:
        agg = per_table.setdefault(
            f"{rec['schema']}.{rec['table']}", {"bytes": 0, "rows": 0, "files": 0}
        )
        agg["bytes"] += rec["bytes"]
        agg["rows"] += rec["rows"]
        agg["files"] += 1
    per_schema: dict[str, int] = {}
    for qn, agg in per_table.items():
        schema = qn.split(".", 1)[0]
        per_schema[schema] = per_schema.get(schema, 0) + agg["bytes"]
    lake_bytes = sum(a["bytes"] for a in per_table.values())
    lake_files = sum(a["files"] for a in per_table.values())
    lake_rows = sum(a["rows"] for a in per_table.values())

    # one timed os.scandir sweep over lake/core (DESIGN §21 bucket-count measurement)
    t0 = time.perf_counter()
    n_dirs = n_files = 0
    stack = [lake / loader_paths.CORE_LAYER]
    while stack:
        with os.scandir(stack.pop()) as it:
            for entry in it:
                if entry.is_dir(follow_symlinks=False):
                    n_dirs += 1
                    stack.append(Path(entry.path))
                else:
                    n_files += 1
    scan_wall = round(time.perf_counter() - t0, 3)
    progress_files = sum(
        1 for t in _staged_tables(contract) if t.partitioned and status[t.qualified_name]
    )
    # every scanned file is either a published part file or a _progress.json
    assert lake_files <= n_files <= lake_files + progress_files, (n_files, lake_files)

    csv_bytes_by_table = {
        t.qualified_name: rec.bytes
        for t in _staged_tables(contract)
        if (rec := raw.for_table(t)) is not None
    }
    csv_bytes = sum(csv_bytes_by_table.values())
    ratio = csv_bytes / lake_bytes if lake_bytes else 0.0
    free_gb = shutil.disk_usage(settings.data_root).free / 2**30
    assert free_gb >= settings.min_free_gb, f"free space {free_gb:.1f} GB under the floor"

    build_id = new_build_id("verify")
    host = benchmarks_mod.host_info()
    for qn, agg in sorted(per_table.items()):
        benchmarks_mod.append(
            benchmarks_mod.BenchmarkLine(
                ts=manifest_mod.utc_now_iso(),
                build_id=build_id,
                tier="full",
                step=f"verify.{qn}",
                kind="verify",
                wall_s=0.0,
                rows=agg["rows"],
                bytes_in=csv_bytes_by_table.get(qn),
                bytes_out=agg["bytes"],
                files=agg["files"],
                duckdb_version=duckdb.__version__,
                host=host,
                ok=True,
            ),
            settings,
        )
    benchmarks_mod.append(
        benchmarks_mod.BenchmarkLine(
            ts=manifest_mod.utc_now_iso(),
            build_id=build_id,
            tier="full",
            step="verify.lake_core",
            kind="verify",
            wall_s=scan_wall,
            rows=lake_rows,
            bytes_in=csv_bytes,
            bytes_out=lake_bytes,
            files=n_files,
            duckdb_version=duckdb.__version__,
            host=host,
            ok=True,
        ),
        settings,
    )

    record_property("lake_core_bytes", lake_bytes)
    record_property("scan_wall_s", scan_wall)
    print(
        f"lake/core: {fmt_int(lake_files)} part files + progress markers = "
        f"{fmt_int(n_files)} files in {fmt_int(n_dirs)} dirs; scandir sweep {scan_wall}s"
    )
    print(
        f"bytes: lake/core {fmt_int(lake_bytes)} vs raw CSV {fmt_int(csv_bytes)} "
        f"(compression ratio {ratio:.1f}x); free {free_gb:.1f} GB"
    )
    for schema, b in sorted(per_schema.items()):
        print(f"  {schema}: {fmt_int(b)} bytes")
    for qn, agg in sorted(per_table.items()):
        csv_b = csv_bytes_by_table.get(qn)
        r = f"{csv_b / agg['bytes']:.1f}x" if csv_b and agg["bytes"] else "-"
        print(f"  {qn:<36} {fmt_int(agg['bytes']):>15} bytes {agg['files']:>4} file(s) {r:>7}")


# ---------------------------------------------------------------------------
# 5. Ledger summary — the numbers the EP-23 … EP-27 completion notes cite
# ---------------------------------------------------------------------------


@pytest.mark.tier("full", needs="lake")
def test_full_ledger_summary(contract: Contract) -> None:
    df = benchmarks_mod.summarize(tier="full")
    assert not df.is_empty()
    by_step = {row["step"]: row for row in df.to_dicts()}
    large = [t for t in _staged_tables(contract) if t.load_class == "large"]
    assert len(large) == 11  # the ⏱ tables of EP-23, EP-24, EP-25, EP-26, EP-27
    header = (
        f"{'step':<42} {'p1 s':>7} {'p2 s':>7} {'total s':>8} {'rss MB':>9} "
        f"{'rows':>14} {'in MB/s':>8} {'files':>6} build_id"
    )
    print(header)
    for table in large:
        step = f"stage.{table.qualified_name}"
        row = by_step.get(step)
        assert row is not None and row["ok"], step
        # pass 2 always runs on the large path; pass 1 is absent only after a resume
        assert row["pass2_wall_s"] is not None, step
        p1 = "-" if row["pass1_wall_s"] is None else f"{row['pass1_wall_s']:.1f}"
        print(
            f"{step:<42} {p1:>7} {row['pass2_wall_s']:>7.1f} {row['wall_s']:>8.1f} "
            f"{row['peak_rss_mb'] or 0:>9,.0f} {row['rows']:>14,} "
            f"{row['mb_in_per_s'] or 0:>8.1f} {row['files']:>6} {row['build_id']}"
        )
    small_steps = [
        f"stage.{t.qualified_name}" for t in _staged_tables(contract) if t.load_class != "large"
    ]
    missing = [s for s in small_steps if s not in by_step]
    assert not missing, f"stage steps with no full-tier ledger line: {missing}"
    assert all(by_step[s]["ok"] for s in small_steps)
