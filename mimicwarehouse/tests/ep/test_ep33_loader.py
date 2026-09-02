"""EP-33 Workstream B — loader / dag / catalog-build migrations (agent A section; merged
into ``test_ep33.py`` by the orchestrator).

Ledger findings covered: LDR-1 (stage-level coverage refusal), WIN-1/LDR-3 (manifest line
before the progress record), LDR-4 (resume identity: source fingerprint / sha / sort_by,
old-format progress files still resume), WIN-2 (retrying pass-2 replace/unlink), DAG-1
(``--background`` + ``--dry-run`` refused), DAG-2/DAG-3 (exclusive lock create, pid +
create_time identity), LGR-1 (tolerant benchmark / manifest readers), B2 (``CatalogSwapError``
is a ``publish.SwapBlockedError``; ``mimicwarehouse.paths`` is gone), B8 (lazy
``loader/__init__``).

Fixture tier only: the committed synthetic fixture tree and crafted CSVs with ids >= 90 000 000
under ``tmp_path``. Everything asserted is counts, paths, hashes and file layout — never a row.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

import helpers
from mimicwarehouse import config, publish
from mimicwarehouse.catalog import build as build_mod
from mimicwarehouse.cli import app
from mimicwarehouse.dag import benchmarks as benchmarks_mod
from mimicwarehouse.dag import jobs as jobs_mod
from mimicwarehouse.dag import runner as runner_mod
from mimicwarehouse.dag import snapshot as snapshot_mod
from mimicwarehouse.loader import buckets as buckets_mod
from mimicwarehouse.loader import engine, stage
from mimicwarehouse.loader import manifest as manifest_mod
from mimicwarehouse.loader.buckets import StageCoverageError, stage_partitioned

if TYPE_CHECKING:
    from mimicwarehouse.schema.contract import Contract, Table

pytestmark = pytest.mark.ep_33

HOSP = "mimiciv_hosp"
#: The unpatched sorter, pinned at import so stacked monkeypatches never wrap each other.
_REAL_SORT = buckets_mod._sort_bucket


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


@pytest.fixture(scope="session")
def admissions(contract: Contract) -> Table:
    return contract.table(HOSP, "admissions")


@pytest.fixture
def crafted_csv(tmp_path: Path, admissions: Table) -> Path:
    """200 one-admission subjects, ids 90 000 000 - 90 000 199 -> 2 rows in every bucket
    (the EP-18 crafted shape; only ids, admittime and two flavour columns are filled)."""
    cols = list(admissions.column_names)
    lines = [",".join(cols)]
    for i in range(200):
        row = dict.fromkeys(cols, "")
        row["subject_id"] = str(90_000_000 + i)
        row["hadm_id"] = str(91_000_000 + i)
        row["admittime"] = f"2130-01-{28 - (i % 28):02d} {23 - (i % 24):02d}:00:00"
        row["admission_type"] = "EW EMER."
        row["hospital_expire_flag"] = "0"
        lines.append(",".join(row[c] for c in cols))
    path = tmp_path / "crafted_admissions.csv"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return path


def _dest(lake_root: Path, table: Table) -> Path:
    return lake_root / "core" / table.schema_name / table.name


def _open(settings: config.Settings):
    return engine.open_build_connection(settings, tier="fixture", memory_limit="4GB")


def _stage_large(
    settings: config.Settings,
    admissions: Table,
    source: Path,
    lake_root: Path,
    *,
    build_id: str,
    buckets: list[int] | None,
) -> stage.StageResult:
    con = _open(settings)
    try:
        return stage_partitioned(
            con,
            admissions,
            source,
            _dest(lake_root, admissions),
            lake_root=lake_root,
            build_id=build_id,
            settings=settings,
            size_class="large",
            buckets=buckets,
        )
    finally:
        con.close()


def _crash_sort_after(monkeypatch: pytest.MonkeyPatch, n: int) -> list[Path]:
    """Monkeypatch ``_sort_bucket`` to crash after ``n`` real sorts; returns the call log."""
    calls: list[Path] = []

    def crashing(con, bucket_dir, order_by):
        if len(calls) == n:
            raise RuntimeError("simulated crash (EP-33 loader test)")
        calls.append(bucket_dir)
        return _REAL_SORT(con, bucket_dir, order_by)

    monkeypatch.setattr(buckets_mod, "_sort_bucket", crashing)
    return calls


def _record_sorts(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    recorded: list[int] = []

    def recording(con, bucket_dir, order_by):
        recorded.append(int(bucket_dir.name.split("=", 1)[1]))
        return _REAL_SORT(con, bucket_dir, order_by)

    monkeypatch.setattr(buckets_mod, "_sort_bucket", recording)
    return recorded


def _bucket_shas(dest: Path) -> dict[str, str]:
    return {
        p.parent.name: manifest_mod.sha256_streamed(p)
        for p in sorted(dest.glob(f"{buckets_mod.BUCKET_COLUMN}=*/{stage.PART_FILENAME}"))
    }


# ---------------------------------------------------------------------------
# LDR-1: a strict-subset request over a wider table is refused at the stage level
# ---------------------------------------------------------------------------


def test_subset_request_over_complete_table_is_refused(
    settings: config.Settings,
    contract: Contract,
    fixture_root: Path,
    lake_root: Path,
) -> None:
    patients = contract.table(HOSP, "patients")  # small partitioned path
    source = fixture_root / "mimic-iv-3.1" / patients.csv_path
    dest = _dest(lake_root, patients)
    con = _open(settings)
    try:
        first = stage_partitioned(
            con, patients, source, dest, lake_root=lake_root, build_id="first", settings=settings
        )
        before = _bucket_shas(dest)
        assert first.rows > 0 and before
        assert (
            manifest_mod.read_status(lake_root)["steps"][patients.qualified_name]["tier_complete"]
            == "full"
        )

        # the dev subset (what `mwh build --tier dev --force` would request) is refused
        # by the stage itself — the runner's --force never reaches this guard
        with pytest.raises(StageCoverageError, match="mwh build --tier full --force"):
            stage_partitioned(
                con,
                patients,
                source,
                dest,
                lake_root=lake_root,
                build_id="dev-subset",
                settings=settings,
                buckets=settings.dev_buckets,
            )
        assert _bucket_shas(dest) == before, "lake bytes untouched by the refusal"
        assert not publish.new_path_for(dest).exists() and not publish.old_path_for(dest).exists()
        progress = buckets_mod.read_progress(dest)
        assert progress is not None and progress.build_id == "first"
        assert (
            manifest_mod.read_status(lake_root)["steps"][patients.qualified_name]["tier_complete"]
            == "full"
        )

        # equal coverage (all 100 buckets again) restages as before
        second = stage_partitioned(
            con, patients, source, dest, lake_root=lake_root, build_id="second", settings=settings
        )
        assert second.rows == first.rows
        progress = buckets_mod.read_progress(dest)
        assert progress is not None and progress.build_id == "second" and progress.complete
    finally:
        con.close()


def test_wider_request_over_dev_table_proceeds(
    settings: config.Settings, admissions: Table, crafted_csv: Path, lake_root: Path
) -> None:
    dev = list(settings.dev_buckets)
    dest = _dest(lake_root, admissions)
    con = _open(settings)
    try:
        stage_partitioned(
            con,
            admissions,
            crafted_csv,
            dest,
            lake_root=lake_root,
            build_id="dev",
            settings=settings,
            buckets=dev,
        )
        progress = buckets_mod.read_progress(dest)
        assert progress is not None and progress.buckets_requested == dev
        # a superset request (all buckets) is never a loss of coverage: it proceeds
        result = stage_partitioned(
            con,
            admissions,
            crafted_csv,
            dest,
            lake_root=lake_root,
            build_id="full",
            settings=settings,
        )
        assert result.rows == 200
        progress = buckets_mod.read_progress(dest)
        assert progress is not None and len(progress.buckets_requested) == buckets_mod.NUM_BUCKETS
    finally:
        con.close()


# ---------------------------------------------------------------------------
# WIN-1 / LDR-3: manifest line before the progress record; resume re-appends harmlessly
# ---------------------------------------------------------------------------


def test_manifest_line_precedes_progress_record_and_resume_reappends(
    settings: config.Settings,
    admissions: Table,
    crafted_csv: Path,
    lake_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dev = list(settings.dev_buckets)
    dest = _dest(lake_root, admissions)
    real_append = buckets_mod.append_manifest
    appends: list[int] = []

    def append_then_crash(lake_root_, build_id, lines):
        path = real_append(lake_root_, build_id, lines)  # the line IS on disk ...
        appends.append(len(lines))
        if len(appends) == 2:  # ... and the crash lands before the progress record
            raise RuntimeError("simulated crash between manifest append and progress write")
        return path

    monkeypatch.setattr(buckets_mod, "append_manifest", append_then_crash)
    with pytest.raises(RuntimeError, match="simulated crash"):
        _stage_large(
            settings, admissions, crafted_csv, lake_root, build_id="aaa-crash", buckets=dev
        )

    progress = buckets_mod.read_progress(dest)
    assert progress is not None and progress.pass1_done and not progress.complete
    assert len(progress.sorted_buckets) == 1
    crash_lines = list(
        manifest_mod.iter_manifest(manifest_mod.manifest_path(lake_root, "aaa-crash"))
    )
    assert len(crash_lines) == 2, "the second bucket's line was appended before the crash"
    # invariant: every recorded bucket has its manifest line
    recorded_paths = {snapshot_mod._bucket_of(line.path) for line in crash_lines}
    assert set(progress.sorted_buckets) <= recorded_paths
    crashed_bucket = next(b for b in recorded_paths if b not in progress.sorted_buckets)
    # publish-before-delete: the crashed bucket still has its raws (it will re-sort)
    assert any((dest / f"{buckets_mod.BUCKET_COLUMN}={crashed_bucket}").glob(buckets_mod.RAW_GLOB))

    monkeypatch.setattr(buckets_mod, "append_manifest", real_append)
    recorded = _record_sorts(monkeypatch)
    result = _stage_large(
        settings, admissions, crafted_csv, lake_root, build_id="bbb-resume", buckets=dev
    )
    assert result.pass1_wall_s is None, "resumed: pass 1 was not redone"
    assert sorted(recorded) == sorted(b for b in dev if b not in progress.sorted_buckets)
    assert crashed_bucket in recorded

    # the re-appended line is harmless: exactly one latest line per path, newest build wins
    latest = snapshot_mod._latest_lines(lake_root)
    by_bucket = {snapshot_mod._bucket_of(p): line for p, line in latest.items()}
    assert set(by_bucket) == set(dev)
    assert by_bucket[crashed_bucket].build_id == "bbb-resume"
    resume_lines = list(
        manifest_mod.iter_manifest(manifest_mod.manifest_path(lake_root, "bbb-resume"))
    )
    assert len(crash_lines) + len(resume_lines) == len(dev) + 1
    # a dev-bucket request completes the table for the dev tier (tier_complete = "dev")
    stats = snapshot_mod.table_file_stats(lake_root, "dev", settings=settings)
    assert stats[admissions.qualified_name] == (result.rows, result.bytes, len(dev))


# ---------------------------------------------------------------------------
# LDR-4: resume identity — sort_by / source fingerprint force a restage; old files resume
# ---------------------------------------------------------------------------


def _tamper_progress(dest: Path, **changes: Any) -> None:
    path = buckets_mod.progress_path(dest)
    data = json.loads(path.read_text(encoding="utf-8"))
    for key, value in changes.items():
        if value is ...:
            data.pop(key, None)
        else:
            data[key] = value
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def test_progress_records_identity_and_resume_requires_it(
    settings: config.Settings,
    admissions: Table,
    crafted_csv: Path,
    lake_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dev = list(settings.dev_buckets)
    dest = _dest(lake_root, admissions)

    # 1. crash after one sorted bucket: the progress file carries the identity fields
    _crash_sort_after(monkeypatch, 1)
    with pytest.raises(RuntimeError, match="simulated crash"):
        _stage_large(settings, admissions, crafted_csv, lake_root, build_id="c1", buckets=dev)
    progress = buckets_mod.read_progress(dest)
    assert progress is not None and len(progress.sorted_buckets) == 1
    assert progress.source_fingerprint == buckets_mod.source_fingerprint(crafted_csv)
    assert progress.source_sha256 is None  # fixture tier: no raw manifest
    assert progress.sort_by == list(admissions.sort_keys[1:])

    # 2. a different recorded sort_by is not resumable: pass 1 runs again, every bucket sorts
    _tamper_progress(dest, sort_by=["hadm_id"])
    recorded = _record_sorts(monkeypatch)
    result = _stage_large(settings, admissions, crafted_csv, lake_root, build_id="r1", buckets=dev)
    assert result.pass1_wall_s is not None and sorted(recorded) == dev

    # 3. a pre-EP-33 progress file (no identity keys, plus an unknown key) still resumes
    _crash_sort_after(monkeypatch, 1)
    with pytest.raises(RuntimeError, match="simulated crash"):
        _stage_large(settings, admissions, crafted_csv, lake_root, build_id="c2", buckets=dev)
    _tamper_progress(dest, source_sha256=..., source_fingerprint=..., sort_by=..., future_key=1)
    old = buckets_mod.read_progress(dest)
    assert old is not None and old.sort_by is None and old.source_fingerprint is None
    recorded = _record_sorts(monkeypatch)
    result = _stage_large(settings, admissions, crafted_csv, lake_root, build_id="r2", buckets=dev)
    assert result.pass1_wall_s is None and sorted(recorded) == sorted(
        set(dev) - set(old.sorted_buckets)
    )

    # 4. a source file with a different fingerprint (new mtime) forces a restage
    _crash_sort_after(monkeypatch, 1)
    with pytest.raises(RuntimeError, match="simulated crash"):
        _stage_large(settings, admissions, crafted_csv, lake_root, build_id="c3", buckets=dev)
    st = crafted_csv.stat()
    os.utime(crafted_csv, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000_000))
    recorded = _record_sorts(monkeypatch)
    result = _stage_large(settings, admissions, crafted_csv, lake_root, build_id="r3", buckets=dev)
    assert result.pass1_wall_s is not None and sorted(recorded) == dev
    final = buckets_mod.read_progress(dest)
    assert final is not None and final.complete and final.dev_ready


def test_progress_resumable_for_predicate() -> None:
    new = buckets_mod.Progress(
        build_id="b", source_sha256="abc", source_fingerprint="f:1:2", sort_by=["admittime"]
    )
    kw = {"source_sha256": "abc", "source_fingerprint": "f:1:2", "sort_by": ["admittime"]}
    assert new.resumable_for(**kw)
    assert not new.resumable_for(**{**kw, "source_sha256": None})
    assert not new.resumable_for(**{**kw, "source_fingerprint": "f:1:3"})
    assert not new.resumable_for(**{**kw, "sort_by": []})
    old = buckets_mod.Progress(build_id="b")  # pre-EP-33: nothing recorded, old predicate
    assert old.resumable_for(**kw) and old.resumable_for(**{**kw, "sort_by": []})
    assert buckets_mod.read_progress(Path("does-not-exist")) is None


# ---------------------------------------------------------------------------
# WIN-2: pass-2 replace / unlink go through the retry policy
# ---------------------------------------------------------------------------


def test_pass2_file_ops_retry_transient_permission_errors(
    settings: config.Settings,
    admissions: Table,
    crafted_csv: Path,
    lake_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dev = list(settings.dev_buckets)
    monkeypatch.setattr(publish.time, "sleep", lambda s: None)
    real_replace, real_unlink = publish.os.replace, publish.os.unlink
    flaky: dict[str, int] = {"replace": 0, "unlink": 0}

    def replace_once_locked(src, dst):
        if Path(src).name == buckets_mod.SORTING_TMP and flaky["replace"] == 0:
            flaky["replace"] += 1
            raise PermissionError(5, "held by a scanner")
        real_replace(src, dst)

    def unlink_once_locked(path):
        if Path(path).name.startswith("raw_") and flaky["unlink"] == 0:
            flaky["unlink"] += 1
            raise PermissionError(5, "held by a scanner")
        real_unlink(path)

    monkeypatch.setattr(publish.os, "replace", replace_once_locked)
    monkeypatch.setattr(publish.os, "unlink", unlink_once_locked)
    result = _stage_large(settings, admissions, crafted_csv, lake_root, build_id="w2", buckets=dev)
    assert flaky == {"replace": 1, "unlink": 1}, "both transient holds were hit and retried"
    assert result.rows == 2 * len(dev)
    dest = _dest(lake_root, admissions)
    for d in dest.glob(f"{buckets_mod.BUCKET_COLUMN}=*"):
        assert [p.name for p in sorted(d.iterdir())] == [stage.PART_FILENAME]


# ---------------------------------------------------------------------------
# DAG-1: --background with --dry-run is refused
# ---------------------------------------------------------------------------


def test_background_dry_run_refused(data_root: Path) -> None:
    runner = helpers.cli_runner()
    result = runner.invoke(
        app, ["build", "--tier", "fixture", "--background", "--job", "ep33-dry", "--dry-run"]
    )
    assert result.exit_code == 2, result.output
    assert "--dry-run cannot be combined with --background" in result.output
    assert not (data_root / "runs").exists(), "nothing launched, no job state file"


# ---------------------------------------------------------------------------
# DAG-2 / DAG-3: exclusive lock create; pid + create_time identity; --break-lock clears
# ---------------------------------------------------------------------------


def test_pid_alive_uses_create_time_identity() -> None:
    me = os.getpid()
    mine = jobs_mod.process_create_time(me)
    assert mine is not None
    assert jobs_mod.pid_alive(me) and jobs_mod.pid_alive(me, mine)
    assert not jobs_mod.pid_alive(me, mine - 3600.0), "same pid, other creation time: recycled"
    assert not jobs_mod.pid_alive(0) and not jobs_mod.pid_alive(-1, mine)


def test_lock_recycled_pid_is_breakable_and_live_pid_refuses(
    settings: config.Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock = runner_mod.lock_path(settings)
    lock.parent.mkdir(parents=True, exist_ok=True)
    me = os.getpid()
    mine = jobs_mod.process_create_time(me)
    assert mine is not None

    # a live pid whose create_time differs is a recycled pid: stale, breakable
    lock.write_text(
        json.dumps({"pid": me, "create_time": mine - 3600.0, "build_id": "orphan"}),
        encoding="utf-8",
    )
    with pytest.raises(runner_mod.BuildLockError, match="break-lock"):
        runner_mod.acquire_lock(settings, "b1")
    assert lock.is_file()
    taken = runner_mod.acquire_lock(settings, "b1", break_lock=True)
    payload = json.loads(taken.read_text(encoding="utf-8"))
    assert payload["pid"] == me and abs(payload["create_time"] - mine) <= 1.0
    assert payload["build_id"] == "b1"

    # the lock we hold (live pid + matching create_time) refuses even --break-lock
    with pytest.raises(runner_mod.BuildLockError, match="is running"):
        runner_mod.acquire_lock(settings, "b2", break_lock=True)
    runner_mod.release_lock(settings, "b1")
    assert not lock.exists()

    # a pre-EP-33 lock (no create_time) with a live pid is still classified live
    lock.write_text(json.dumps({"pid": me, "build_id": "old-format"}), encoding="utf-8")
    with pytest.raises(runner_mod.BuildLockError, match="is running"):
        runner_mod.acquire_lock(settings, "b3")
    lock.unlink()


def test_lock_create_is_exclusive(
    settings: config.Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock = runner_mod.lock_path(settings)
    lock.parent.mkdir(parents=True, exist_ok=True)
    assert runner_mod._try_create_lock(lock, {"pid": 1})
    assert not runner_mod._try_create_lock(lock, {"pid": 2}), "O_EXCL: an existing lock wins"
    assert json.loads(lock.read_text(encoding="utf-8")) == {"pid": 1}
    lock.unlink()

    # a take-over that loses the re-create race (another build got there first) fails
    # loudly instead of proceeding without a lock
    lock.write_text(json.dumps({"pid": 0, "build_id": "dead"}), encoding="utf-8")
    monkeypatch.setattr(runner_mod, "_try_create_lock", lambda path, payload: False)
    with pytest.raises(runner_mod.BuildLockError, match="could not take over"):
        runner_mod.acquire_lock(settings, "b4", break_lock=True)


def test_job_state_file_without_create_time_still_loads(settings: config.Settings) -> None:
    path = jobs_mod.job_json_path("old-job", settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "job": "old-job",
                "pid": 0,
                "argv": ["--version"],
                "started": "t",
                "log": "x.log",
                "state": "done",
                "exit_code": 0,
                "finished": "t",
            }
        ),
        encoding="utf-8",
    )
    info = jobs_mod.read_job("old-job", settings)
    assert info is not None and info.create_time is None
    assert not jobs_mod.pid_alive(info.pid, info.create_time)


# ---------------------------------------------------------------------------
# LGR-1: tolerant readers — benchmark ledger and lake manifests
# ---------------------------------------------------------------------------


def _bench_line(build_id: str, step: str | None = "stage.x") -> benchmarks_mod.BenchmarkLine:
    return benchmarks_mod.BenchmarkLine(
        ts="2130-01-01T00:00:00+00:00",
        build_id=build_id,
        tier="fixture",
        step=step,
        kind="stage" if step else "build",
        wall_s=1.0,
        rows=3,
        bytes_in=10,
        bytes_out=5,
        files=1,
        duckdb_version="0",
        host=benchmarks_mod.HostInfo(cpu=1, ram_gb=1.0),
        ok=True,
    )


def test_benchmarks_read_tolerates_torn_trailing_line(settings: config.Settings) -> None:
    benchmarks_mod.append(_bench_line("a"), settings)
    benchmarks_mod.append(_bench_line("b"), settings)
    path = benchmarks_mod.benchmarks_path(settings)
    with path.open("ab") as f:
        f.write(b'{"ts": "2130", "torn": tr')
    with pytest.warns(UserWarning, match="torn trailing line"):
        ledger = benchmarks_mod.read(settings)
    assert ledger.height == 2
    with pytest.warns(UserWarning, match="torn trailing line"):
        summary = benchmarks_mod.summarize(settings, tier="fixture")
    # DAG-8: identical ts resolves deterministically by build_id (the later one wins)
    assert summary["build_id"].to_list() == ["b"]
    assert benchmarks_mod.read(config.Settings(data_root=settings.data_root / "empty")).is_empty()


def test_manifest_reader_tolerates_torn_trailing_line(
    settings: config.Settings, lake_root: Path, contract: Contract
) -> None:
    table = contract.table(HOSP, "d_labitems")
    line = manifest_mod.ManifestLine(
        schema=HOSP,
        table=table.name,
        path=f"core/{HOSP}/{table.name}/part-0.parquet",
        sha256="0" * 64,
        bytes=1,
        rows=1,
        schema_hash=manifest_mod.table_schema_hash(table),
        writer_version="w",
        build_id="b",
        ts="2130-01-01T00:00:00+00:00",
    )
    path = manifest_mod.append_manifest(lake_root, "b", [line])
    raw = path.read_bytes()
    assert raw.endswith(b"\n") and b"\r\n" not in raw, "fsio canon: LF line ends"
    with path.open("ab") as f:
        f.write(b'{"schema": "mimiciv_hosp", "torn')
    with pytest.warns(UserWarning, match="torn trailing line"):
        assert [ln.path for ln in manifest_mod.iter_manifest(path)] == [line.path]
    manifest_mod.update_status(lake_root, table.qualified_name, tier_complete="full")
    with pytest.warns(UserWarning, match="torn trailing line"):
        stats = snapshot_mod.table_file_stats(lake_root, "fixture", settings=settings)
    assert stats[table.qualified_name] == (1, 1, 1)


# ---------------------------------------------------------------------------
# B2: the catalog swap is the publish primitive; mimicwarehouse.paths is gone
# ---------------------------------------------------------------------------


def test_catalog_swap_error_is_a_publish_swap_blocked_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert issubclass(build_mod.CatalogSwapError, publish.SwapBlockedError)
    assert issubclass(build_mod.CatalogSwapError, build_mod.CatalogBuildError)
    dest = tmp_path / "fixture.duckdb"
    new = publish.new_path_for(dest)
    new.write_bytes(b"new")
    dest.write_bytes(b"live")

    def blocked(new_, dest_, *, blocked_hint, observer=None):
        raise publish.SwapBlockedError(f"{dest_}: cannot replace the live file — {blocked_hint}")

    monkeypatch.setattr(build_mod.publish, "swap_file", blocked)
    with pytest.raises(
        build_mod.CatalogSwapError, match="close the app/notebooks and rerun"
    ) as info:
        build_mod._publish_catalog(new, dest, "fixture")
    assert "mwh build --tier fixture --select catalog" in str(info.value)
    assert isinstance(info.value, publish.SwapBlockedError)
    assert dest.read_bytes() == b"live"
    for gone in ("swap_catalog", "catalog_new_path", "catalog_old_path", "NEW_SUFFIX"):
        assert not hasattr(build_mod, gone)


def test_top_level_paths_module_is_gone() -> None:
    with pytest.raises(ImportError):
        __import__("mimicwarehouse.paths")
    from mimicwarehouse.loader import paths as loader_paths  # the reader-glob module stays

    assert loader_paths.PARTITION_PATTERN.endswith("part-*.parquet")


# ---------------------------------------------------------------------------
# B8: loader/__init__ re-exports lazily
# ---------------------------------------------------------------------------


def test_loader_package_is_lazy() -> None:
    import mimicwarehouse.loader as loader_pkg

    assert "stage_partitioned" in dir(loader_pkg)
    assert loader_pkg.stage_partitioned is stage_partitioned
    with pytest.raises(AttributeError):
        loader_pkg.no_such_name  # noqa: B018
    proc = helpers.fresh_interpreter(
        [
            "-c",
            "import sys, mimicwarehouse.loader; print(sorted(m for m in sys.modules if "
            "m.startswith('mimicwarehouse.loader.') or m in ('duckdb', 'polars')))",
        ]
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "[]"
