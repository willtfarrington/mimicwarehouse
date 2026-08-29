"""EP-19 — DAG runner ``mwh build``.

Fixture tier (default): spec validation, dry-run planning, a real three-step build into
a temp data root (manifests, status, snapshot entry, ledger lines), skip/--force, the
build lock, resume-after-failure, and a detached ``jobs.launch``. Dev tier
(``--tier dev``): ``mwh build --tier dev --select stage.mimiciv_hosp.patients`` against
the real raw CSVs into the real lake — counts, schemas and hashes only; no row-level
output anywhere.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

import helpers
from mimicwarehouse import config
from mimicwarehouse.cli import app
from mimicwarehouse.dag import benchmarks as benchmarks_mod
from mimicwarehouse.dag import jobs as jobs_mod
from mimicwarehouse.dag import runner as runner_mod
from mimicwarehouse.dag import snapshot as snapshot_mod
from mimicwarehouse.dag.spec import DagError, DagSpec, load_dag
from mimicwarehouse.loader import manifest as manifest_mod

pytestmark = pytest.mark.ep_19

HOSP = "mimiciv_hosp"
THREE_STEPS = [
    f"stage.{HOSP}.patients",
    f"stage.{HOSP}.admissions",
    f"stage.{HOSP}.d_labitems",
]


@pytest.fixture
def data_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    root = helpers.tmp_data_root(monkeypatch, tmp_path)
    yield root
    config.configure()


@pytest.fixture
def settings(data_root: Path) -> config.Settings:
    return config.get_settings()


def _step(name: str, **extra) -> dict:
    return {"name": name, "kind": "catalog", **extra}


# ---------------------------------------------------------------------------
# 1. Spec validation: unique names, known dependencies, acyclic; selection errors
# ---------------------------------------------------------------------------


def test_spec_refuses_cycle_unknown_dep_and_dupes() -> None:
    with pytest.raises(ValueError, match="cycle"):
        DagSpec.model_validate(
            {
                "version": 1,
                "steps": [_step("a", depends_on=["b"]), _step("b", depends_on=["a"])],
            }
        )
    with pytest.raises(ValueError, match="unknown dependenc"):
        DagSpec.model_validate({"version": 1, "steps": [_step("a", depends_on=["ghost"])]})
    with pytest.raises(ValueError, match="duplicate step names"):
        DagSpec.model_validate({"version": 1, "steps": [_step("a"), _step("a")]})
    with pytest.raises(ValueError, match="missing field"):
        DagSpec.model_validate({"version": 1, "steps": [{"name": "s", "kind": "stage"}]})
    with pytest.raises(ValueError, match="belong to another kind"):
        DagSpec.model_validate({"version": 1, "steps": [_step("c", file="x.sql")]})


def test_shipped_spec_orders_and_selects() -> None:
    dag = load_dag()
    names = [s.name for s in dag.ordered()]
    # EP-20 grew the shipped spec from 3 stage steps to 20; the EP-19 mechanics still
    # hold: spec order is preserved and catalog (depending on every stage step) is last
    positions = [names.index(n) for n in THREE_STEPS]
    assert positions == sorted(positions)
    assert names[-1] == "catalog"
    assert f"stage.{HOSP}.d_labitems" in [s.name for s in dag.ordered(tags=["dims"])]
    with pytest.raises(DagError, match="unknown step"):
        dag.ordered(select=["nope"])
    with pytest.raises(DagError, match="unknown tag"):
        dag.ordered(tags=["nope"])


# ---------------------------------------------------------------------------
# 2. Dry run: the ordered plan only — no lock, no lake, nothing written
# ---------------------------------------------------------------------------


def test_dry_run_prints_plan_and_writes_nothing(data_root: Path) -> None:
    runner = helpers.cli_runner()
    result = runner.invoke(app, ["build", "--tier", "fixture", "--dry-run", "--tag", "stage"])
    assert result.exit_code == 0, result.output
    out = result.output
    positions = [out.find(name) for name in THREE_STEPS]
    assert all(p >= 0 for p in positions) and positions == sorted(positions)
    assert not (data_root / "lake").exists() and not (data_root / "warehouse").exists()


# ---------------------------------------------------------------------------
# 3. Fixture build: manifests, status, snapshot entry, ledger; skip and --force
# ---------------------------------------------------------------------------


def test_fixture_build_skip_and_force(settings: config.Settings) -> None:
    dag = load_dag()
    result = runner_mod.run(dag, "fixture", select=THREE_STEPS, settings=settings)
    assert result.ok and [s.status for s in result.steps] == ["done"] * 3
    assert all(s.rows and s.rows > 0 for s in result.steps)

    lake = settings.lake_root("fixture")
    # manifests + status under the fixture lake root, never the credentialed lake/
    assert manifest_mod.manifest_path(lake, result.build_id).is_file()
    status = manifest_mod.read_status(lake)["steps"]
    assert status[f"{HOSP}.patients"]["tier_complete"] == "full"
    assert status[f"{HOSP}.d_labitems"]["tier_complete"] == "full"  # dim completed by EP-19
    assert status[f"{HOSP}.d_labitems"]["dev_ready"] is True
    assert not (settings.layout["lake"] / "core").exists()

    # snapshot: printed id recorded in the history file, logical (rerun agrees)
    history = snapshot_mod.read_snapshots(lake)
    assert [h["build_id"] for h in history] == [result.build_id]
    assert history[0]["snapshot_id"] == result.snapshot_id
    assert history[0]["layer"] == "core" and history[0]["tier"] == "fixture"

    # ledger: one line per step (phase total) + one build summary line
    ledger = benchmarks_mod.read(settings)
    assert ledger.height == 4
    steps = ledger.filter(ledger["kind"] != "build")
    assert set(steps["step"].to_list()) == set(THREE_STEPS)
    assert all(steps["ok"].to_list()) and all(w > 0 for w in steps["wall_s"].to_list())
    build_line = ledger.filter(ledger["kind"] == "build")
    assert build_line.height == 1 and build_line["ok"].to_list() == [True]

    # a second run skips every completed step and the snapshot id does not move
    again = runner_mod.run(dag, "fixture", select=THREE_STEPS, settings=settings)
    assert [s.status for s in again.steps] == ["skipped"] * 3
    assert again.snapshot_id == result.snapshot_id

    # --force reruns
    forced = runner_mod.run(dag, "fixture", select=THREE_STEPS, force=True, settings=settings)
    assert [s.status for s in forced.steps] == ["done"] * 3
    assert forced.snapshot_id == result.snapshot_id  # logical id: identical rebuild agrees

    # dev id is a different, stable subset hash (5 buckets + dims vs all 100)
    dev_id = snapshot_mod.layer_snapshot(lake, "core", "dev", settings=settings)
    assert dev_id != result.snapshot_id
    assert dev_id == snapshot_mod.layer_snapshot(lake, "core", "dev", settings=settings)


# ---------------------------------------------------------------------------
# 4. Build lock: live pid refused; stale lock yields only to --break-lock
# ---------------------------------------------------------------------------


def test_lock_refuses_live_and_stale(
    settings: config.Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    dag = load_dag()
    lock = runner_mod.lock_path(settings)
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(
        json.dumps({"pid": os.getpid(), "build_id": "crafted", "started": "t"}),
        encoding="utf-8",
    )
    with pytest.raises(runner_mod.BuildLockError, match="is running"):
        runner_mod.run(dag, "fixture", select=[f"stage.{HOSP}.d_labitems"], settings=settings)
    assert lock.is_file()  # a refused run never removes someone else's lock

    # stale (dead pid): refused without --break-lock, taken over with it
    monkeypatch.setattr(jobs_mod, "pid_alive", lambda pid: False)
    with pytest.raises(runner_mod.BuildLockError, match="break-lock"):
        runner_mod.run(dag, "fixture", select=[f"stage.{HOSP}.d_labitems"], settings=settings)
    result = runner_mod.run(
        dag, "fixture", select=[f"stage.{HOSP}.d_labitems"], break_lock=True, settings=settings
    )
    assert result.ok and not lock.exists()  # released at the end of the run


# ---------------------------------------------------------------------------
# 5. A failing step stops the run; completed work stays complete; rerun resumes
# ---------------------------------------------------------------------------


def test_failure_stops_run_and_rerun_resumes(
    settings: config.Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    dag = load_dag()
    result = runner_mod.run(dag, "fixture", settings=settings)  # catalog raises EP-21
    assert not result.ok
    by_name = {s.name: s for s in result.steps}
    assert [by_name[n].status for n in THREE_STEPS] == ["done"] * 3
    assert by_name["catalog"].status == "failed"
    assert "EP-21" in (by_name["catalog"].error or "")
    assert result.snapshot_id is None  # no snapshot for a failed run
    ledger = benchmarks_mod.read(settings)
    failed = ledger.filter(~ledger["ok"])
    assert set(failed["kind"].to_list()) == {"catalog", "build"}

    # completed steps stayed complete: the rerun skips them and resumes at catalog
    monkeypatch.setitem(
        runner_mod.STEP_HANDLERS, "catalog", lambda step, ctx: runner_mod.StepOutcome()
    )
    again = runner_mod.run(dag, "fixture", settings=settings)
    assert again.ok
    by_name = {s.name: s for s in again.steps}
    assert [by_name[n].status for n in THREE_STEPS] == ["skipped"] * 3
    assert by_name["catalog"].status == "done"


# ---------------------------------------------------------------------------
# 6. jobs.launch: a trivial detached command reaches state=done with a log
# ---------------------------------------------------------------------------


def test_jobs_launch_trivial_command(settings: config.Settings) -> None:
    info = jobs_mod.launch(["--version"], "ep19-trivial", settings)
    assert info.state == "running" and info.pid > 0
    deadline = time.monotonic() + 120
    current = info
    while time.monotonic() < deadline:
        current = jobs_mod.read_job("ep19-trivial", settings) or current
        if current.state != "running":
            break
        time.sleep(0.5)
    assert current.state == "done" and current.exit_code == 0
    assert current.finished is not None
    log = Path(current.log)
    assert log.is_file()
    text = log.read_text(encoding="utf-8", errors="replace")
    assert "[launch]" in text and "exit code=0" in text
    # the CLI surfaces it
    runner = helpers.cli_runner()
    result = runner.invoke(app, ["jobs", "--job", "ep19-trivial", "--tail", "3"])
    assert result.exit_code == 0 and "state=done" in result.output


def test_job_name_validation(settings: config.Settings) -> None:
    with pytest.raises(jobs_mod.JobError, match="job name"):
        jobs_mod.launch(["--version"], "../escape", settings)


# ---------------------------------------------------------------------------
# 7. Dev tier: real patients, dev buckets only, into the real lake (counts only)
# ---------------------------------------------------------------------------


@pytest.mark.tier("dev", needs="raw")
def test_dev_build_real_patients(raw_root: Path) -> None:
    settings = config.load_settings()
    proc = helpers.fresh_interpreter(
        ["-m", "mimicwarehouse.cli", "build", "--tier", "dev", "--select", THREE_STEPS[0]]
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr

    lake = settings.lake_root("dev")
    table_dir = lake / "core" / HOSP / "patients"
    buckets = sorted(
        int(p.name.split("=", 1)[1])
        for p in table_dir.iterdir()
        if p.is_dir() and p.name.startswith("subject_bucket=")
    )
    # at least the five dev buckets (a later full run may have added the rest)
    assert set(settings.dev_buckets) <= set(buckets)
    entry = manifest_mod.read_status(lake)["steps"][f"{HOSP}.patients"]
    assert entry["dev_ready"] is True and entry["tier_complete"] in ("dev", "full")
    assert entry["rows"] > 0 and entry["rejects"] == 0

    # the run left a ledger line (either a fresh stage or a skip + build summary)
    ledger = benchmarks_mod.read(settings)
    assert ledger.height > 0
    assert (ledger["tier"] == "dev").any()
    # and a snapshot history entry for the dev tier
    history = snapshot_mod.read_snapshots(lake)
    assert any(h["tier"] == "dev" for h in history)
