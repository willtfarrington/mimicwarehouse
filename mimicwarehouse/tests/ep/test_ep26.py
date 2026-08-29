"""EP-26 — stage chartevents (the largest single table; two-pass bucketed loader).

Fixture tier (default): the contract facts the brief pins (item 1: ``load_class: large``,
``partitioned``, tie-broken sort keys ``[subject_id, charttime, itemid]`` — a verify since
EP-169, no primary key upstream), the EP-17 governance flags (item 1: ``caregiver_id``
identifier, ``value`` deliberately unflagged — stamped from keys.yaml, verify only), the spec
shape of the new ``stage.mimiciv_icu.chartevents`` step (no overrides — the contract stays
the authority), one real ``mwh build --tier fixture --select stage.mimiciv_icu.chartevents``
into a temp data root (EP-18 layout, per-partition sortedness, manifest lines,
``status.json`` complete, benchmark-ledger ``phase = pass1|pass2|total`` lines), and the
sweeps determinism the brief asks for (item 5): ``sweeps=1`` and ``sweeps=3`` through the
large path produce identical per-bucket sha256 sets, with every loader progress line matching
the expected counts-only patterns (never a row value).

``tier("dev", needs="lake")``-marked (item 6): runs once the real lake's ``status.json``
marks ``mimiciv_icu.chartevents`` ``dev_ready`` (the ⏱ background job sorts buckets 0-4
first), else skips with a reason; skips too while the dev catalog predates the step.
Everything printed or asserted is counts, paths and booleans — never a row, never an
identifier value (a sortedness failure reports the file, not the ids).
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

import pytest

import helpers
from mimicwarehouse import config, paths
from mimicwarehouse.cli import app
from mimicwarehouse.dag import benchmarks as benchmarks_mod
from mimicwarehouse.dag.spec import load_dag
from mimicwarehouse.fixtures import write as fixtures_write
from mimicwarehouse.loader import buckets as buckets_mod
from mimicwarehouse.loader import engine, stage
from mimicwarehouse.loader import manifest as manifest_mod
from mimicwarehouse.loader import paths as loader_paths
from mimicwarehouse.loader.buckets import stage_partitioned
from mimicwarehouse.schema.contract import Contract

pytestmark = pytest.mark.ep_26

ICU = "mimiciv_icu"
QN = f"{ICU}.chartevents"
STEP = f"stage.{QN}"
LOGGER = "mimicwarehouse.loader.buckets"
#: The tie-broken contract sort keys the brief pins (EP-169 adopted them; item 1 verify).
SORT_KEYS = ("subject_id", "charttime", "itemid")
#: Every line the large path logs while staging — counts, paths and timings only (the
#: brief's "no row values" check is a closed allow-list, not a denylist).
PROGRESS_PATTERNS = (
    re.compile(rf"^pass1 {re.escape(QN)}: rss=\d+ tmp_duckdb=\d+ written=\d+ bytes$"),
    re.compile(r"^bucket \d{2}/100 sorted rows=\d+ wall=\d+\.\d+s$"),
    re.compile(rf"^dev-ready {re.escape(QN)}$"),
    re.compile(rf"^complete {re.escape(QN)} tier=(?:full|dev|partial)$"),
)


@pytest.fixture
def data_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    root = helpers.tmp_data_root(monkeypatch, tmp_path)
    yield root
    config.configure()


# ---------------------------------------------------------------------------
# 1. Contract facts (brief item 1 — verify; fix in the EP-9 YAML with a dated note)
# ---------------------------------------------------------------------------


def test_contract_matches_brief(contract: Contract) -> None:
    table = contract.table(QN)
    assert table.load_class == "large"
    assert table.partitioned and table.subject_keyed
    assert table.sort_keys == SORT_KEYS
    assert table.time_column == "charttime"
    # no upstream primary key (known duplicates); the uniqueness hint is the tie-break basis
    assert not table.primary_key
    assert table.uniqueness_hint == ("stay_id", "charttime", "itemid")
    by_name = {c.name: c for c in table.columns}
    assert by_name["value"].duckdb_type == "VARCHAR", "mixed numeric/categorical — stays text"
    assert by_name["valuenum"].duckdb_type == "DOUBLE"
    assert by_name["warning"].duckdb_type == "SMALLINT"
    assert not by_name["hadm_id"].nullable and not by_name["stay_id"].nullable
    assert by_name["caregiver_id"].nullable, "caregiver_id arrived in v2.2 — nullable"
    for name in ("storetime", "valueuom"):
        assert name in by_name, f"contract lost column {name!r}"
    # the contract types must load with zero tolerated rejects (item 1)
    assert config.get_settings().loader_reject_max == 0


# ---------------------------------------------------------------------------
# 2. Governance flags (brief item 1 — verify; stamped from keys.yaml, EP-17/FC-5)
# ---------------------------------------------------------------------------


def test_contract_flags(contract: Contract) -> None:
    table = contract.table(QN)
    assert table.column("caregiver_id").identifier, (
        "caregiver_id is an anonymised staff id — safe_query (EP-30) must refuse it"
    )
    # the GOVERNANCE real-band ids are identifiers too; itemid is a dimension code, not one
    for name in ("subject_id", "hadm_id", "stay_id"):
        assert table.column(name).identifier, name
    assert not table.column("itemid").identifier
    # value is short mixed numeric/categorical text, not note-like — deliberately unflagged
    assert table.free_text_columns() == ()
    assert not table.column("value").free_text


# ---------------------------------------------------------------------------
# 3. Spec shape: the new step carries no overrides; catalog depends on it
# ---------------------------------------------------------------------------


def test_spec_step_shape() -> None:
    dag = load_dag()
    step = dag.step(STEP)
    assert step.kind == "stage" and step.qualified_table == QN
    assert step.source == "mimic-iv-3.1/icu/chartevents.csv"
    assert {"stage", ICU, "large", "chartevents"} <= set(step.tags) and "dims" not in step.tags
    assert step.tiers == ("fixture", "demo", "dev", "full")
    # size_class / partitioned / sort_by come from the contract, never the spec
    assert step.size_class is None and step.partitioned is None and step.sort_by is None
    assert STEP in dag.step("catalog").depends_on


# ---------------------------------------------------------------------------
# 4. Fixture build through the runner: layout, sortedness, manifests, status, ledger
# ---------------------------------------------------------------------------


def test_fixture_build_large_path(data_root: Path, contract: Contract) -> None:
    runner = helpers.cli_runner()
    result = runner.invoke(app, ["build", "--tier", "fixture", "--select", STEP])
    assert result.exit_code == 0, result.output

    settings = config.get_settings()
    lake = settings.lake_root("fixture")
    table = contract.table(QN)
    tdir = loader_paths.table_dir(lake, ICU, "chartevents")

    # EP-18 layout: one sorted part-0.parquet per partition; no raw_* / _sorting.tmp / .new
    bucket_dirs = {
        int(p.name.split("=", 1)[1]): p
        for p in tdir.iterdir()
        if p.is_dir() and p.name.startswith("subject_bucket=")
    }
    assert bucket_dirs, "no partition directories staged"
    for bdir in bucket_dirs.values():
        assert [p.name for p in sorted(bdir.iterdir())] == ["part-0.parquet"], bdir.name
    assert not paths.new_dir_for(tdir).exists() and not paths.old_dir_for(tdir).exists()
    progress = buckets_mod.read_progress(tdir)
    assert progress is not None and progress.pass1_done and progress.complete

    con = engine.open_build_connection(settings, tier="fixture", memory_limit="4GB")
    try:
        for bdir in bucket_dirs.values():
            part = (bdir / "part-0.parquet").as_posix().replace("'", "''")
            tup = f"row({', '.join(SORT_KEYS)})"
            (ok,) = con.execute(
                f"SELECT bool_and(cur >= prev) FROM ("
                f"SELECT {tup} AS cur, lag({tup}, 1, {tup}) OVER (ORDER BY file_row_number) "
                f"AS prev FROM read_parquet('{part}', file_row_number=true, "
                f"hive_partitioning=false))"
            ).fetchone()  # type: ignore[misc]
            assert ok is True, f"{bdir.name} is not sorted by {SORT_KEYS}"
    finally:
        con.close()

    # status.json complete + rows equal the committed fixture manifest's
    entry = manifest_mod.read_status(lake)["steps"][QN]
    fixture_rows = fixtures_write.load_manifest(fixtures_write.default_out_dir())["files"][
        f"mimic-iv-3.1/{table.csv_path}"
    ]["rows"]
    assert entry["rows"] == fixture_rows and entry["rejects"] == 0
    assert entry["tier_complete"] == "full" and entry["dev_ready"] is True
    assert entry["files"] == len(bucket_dirs)

    # one manifest line per partition file (the large path appends per finished bucket)
    lines: dict[str, dict[str, Any]] = {}
    for mpath in manifest_mod.manifests_dir(lake).glob("*.jsonl"):
        for line in mpath.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                lines[rec["path"]] = rec
    expected_paths = {
        f"core/{ICU}/chartevents/subject_bucket={n}/part-0.parquet" for n in bucket_dirs
    }
    assert set(lines) == expected_paths
    assert sum(rec["rows"] for rec in lines.values()) == fixture_rows

    # benchmark ledger: pass1 + pass2 + total for the step, one build summary line
    ledger = benchmarks_mod.read(settings)
    step_lines = ledger.filter(ledger["step"] == STEP)
    assert set(step_lines["phase"].to_list()) == {"pass1", "pass2", "total"}
    assert all(step_lines["ok"].to_list())
    assert ledger.filter(ledger["kind"] == "build").height == 1


# ---------------------------------------------------------------------------
# 5. Sweeps determinism + progress-line hygiene (brief item 5)
# ---------------------------------------------------------------------------


def test_sweeps_identical_and_progress_lines_counts_only(
    data_root: Path, contract: Contract, fixture_root: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=LOGGER)
    settings = config.get_settings()
    lake = settings.lake_root("fixture")
    lake.mkdir(parents=True, exist_ok=True)
    table = contract.table(QN)
    source = fixture_root / "mimic-iv-3.1" / table.csv_path

    con = engine.open_build_connection(settings, tier="fixture", memory_limit="4GB")
    results = {}
    try:
        for sweeps in (1, 3):
            dest = lake / f"sweep{sweeps}_run" / ICU / "chartevents"
            # a tiny heartbeat so at least pass 1 has a chance to log rss/tmp/bytes lines
            results[sweeps] = stage_partitioned(
                con,
                table,
                source,
                dest,
                lake_root=lake,
                build_id=f"s{sweeps}",
                settings=settings,
                sweeps=sweeps,
                heartbeat_s=0.05,
            )
    finally:
        con.close()

    r1, r3 = results[1], results[3]
    assert r1.rows == r3.rows > 0 and r1.rejects == r3.rejects == 0
    assert r1.files == r3.files > 0

    def bucket_shas(name: str) -> dict[int, str]:
        dest = lake / name / ICU / "chartevents"
        dirs = {
            int(p.name.split("=", 1)[1]): p
            for p in dest.iterdir()
            if p.is_dir() and p.name.startswith("subject_bucket=")
        }
        assert dirs, f"no partition dirs under {dest}"
        for d in dirs.values():
            assert [p.name for p in sorted(d.iterdir())] == [stage.PART_FILENAME]
        assert not paths.new_dir_for(dest).exists() and not paths.old_dir_for(dest).exists()
        return {n: manifest_mod.sha256_streamed(d / stage.PART_FILENAME) for n, d in dirs.items()}

    # identical per-bucket sha256 sets across sweeps=1 and sweeps=3 (byte-identical output)
    assert bucket_shas("sweep1_run") == bucket_shas("sweep3_run")

    # manifests agree bucket-by-bucket; both runs merged the same status entry
    def manifest_rows(build_id: str) -> dict[str, int]:
        mpath = manifest_mod.manifest_path(lake, build_id)
        recs = [json.loads(ln) for ln in mpath.read_text(encoding="utf-8").splitlines() if ln]
        return {rec["path"].rsplit("subject_bucket=", 1)[1]: rec["rows"] for rec in recs}

    assert manifest_rows("s1") == manifest_rows("s3")
    entry = manifest_mod.read_status(lake)["steps"][QN]
    assert entry["rows"] == r3.rows and entry["rejects"] == 0

    # every progress line the large path logged is a counts-only pattern — no row values
    messages = [rec.message for rec in caplog.records if rec.name == LOGGER]
    assert messages, "the large path logged no progress lines"
    for message in messages:
        assert any(p.match(message) for p in PROGRESS_PATTERNS), (
            f"unexpected progress line shape: {message!r}"
        )
    assert sum(1 for m in messages if m.startswith("complete ")) == 2


# ---------------------------------------------------------------------------
# 6. Dev tier (brief item 6): green once the ⏱ job marks the step dev_ready
# ---------------------------------------------------------------------------


@pytest.mark.tier("dev", needs="lake")
def test_dev_counts_and_sortedness(dev_ready, contract: Contract) -> None:
    import duckdb

    from mimicwarehouse.catalog.connect import open_catalog

    # status.json keys steps by <schema>.<table> (loader/manifest.py), so the readiness
    # fixture takes the qualified table name, not the spec's step name
    entry = dev_ready(QN)
    assert entry["dev_ready"] is True

    settings = config.load_settings()
    lake = settings.lake_root("dev")

    # manifest rows per lake-relative path; last line wins per path (rebuilds append)
    rows_by_path: dict[str, int] = {}
    for mpath in manifest_mod.manifests_dir(lake).glob("*.jsonl"):
        for line in mpath.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            if f"{rec['schema']}.{rec['table']}" == QN:
                rows_by_path[rec["path"]] = rec["rows"]
    manifest_rows = sum(
        rows
        for path, rows in rows_by_path.items()
        if any(f"subject_bucket={b}/" in path for b in settings.dev_buckets)
    )
    assert manifest_rows > 0, "no manifest rows recorded for the dev buckets"

    con = open_catalog("dev")
    try:
        try:
            (total,) = con.execute(f"SELECT count(*) FROM {QN}").fetchone()  # type: ignore[misc]
        except duckdb.CatalogException:
            pytest.skip(
                "dev catalog predates the chartevents step — refresh it with "
                "`mwh build --tier dev --select catalog`"
            )
        assert total > 0
        assert total == manifest_rows, (
            f"dev view count {total} != manifest rows {manifest_rows} for buckets "
            f"{list(settings.dev_buckets)}"
        )
        # the brief asks for the NULL-stay_id count (a count only); the contract says
        # NOT NULL and the loader tolerates zero rejects, so it must be exactly 0
        (null_stays,) = con.execute(f"SELECT count(*) FROM {QN} WHERE stay_id IS NULL").fetchone()  # type: ignore[misc]
        assert null_stays == 0, f"{null_stays} chartevents rows with NULL stay_id"
    finally:
        con.close()

    # per-file row-group subject_id min/max are non-decreasing (sorted; EP-18 pass 2).
    # Values are compared, never printed — a failure names the file only (GOVERNANCE §4).
    meta_con = duckdb.connect()
    try:
        for b in settings.dev_buckets:
            part = loader_paths.table_dir(lake, ICU, "chartevents") / f"subject_bucket={b}"
            part = part / "part-0.parquet"
            assert part.is_file(), f"dev bucket {b} has no part-0.parquet"
            escaped = part.as_posix().replace("'", "''")
            groups = meta_con.execute(
                f"SELECT stats_min_value, stats_max_value FROM parquet_metadata('{escaped}') "
                "WHERE path_in_schema = 'subject_id' ORDER BY row_group_id"
            ).fetchall()
            assert groups, f"{part.name}: no row-group statistics"
            bounds = [(int(lo), int(hi)) for lo, hi in groups]
            ok = all(lo <= hi for lo, hi in bounds) and all(
                bounds[i][1] <= bounds[i + 1][0] for i in range(len(bounds) - 1)
            )
            assert ok, f"row-group subject_id ranges are not non-decreasing in bucket {b}"
    finally:
        meta_con.close()
