"""EP-52 — Backup of non-reproducible state (``mwh backup``).

Fixture tier only, against a temporary data root holding synthetic state: the four JSONL
ledgers (written through the EP-33 canon by the EP-35 run ledger, the EP-19 benchmark
bridge and the EP-51 freeze registry), two run records (``manifest.json`` + ``sql/`` + a
Parquet table and a figure), a frozen protocol (read-only copy), job state,
``warehouse/runs.duckdb``, model-registry metadata beside a weights file and a study
workspace with specs, notes, a JSONL record and data-shaped files. Covers: the set
(``backup run`` copies exactly it — run artifacts only with ``--include-run-artifacts``,
job state / ``runs.duckdb`` / weights / data files never — with a complete hashed
manifest; the frozen copy stays read-only, ``docs/gotchas.md`` §2); ``verify`` (a
flipped byte, a missing and an unexpected file are named; exit 1); ``restore`` (identical
hashes, the restored frozen copy still verifies, ``mwh --data-root <to> runs refresh``
builds the views over it; never over a non-empty ``runs/``; a damaged backup is refused;
``--dry-run`` writes nothing); the target refusals (the D-29 detector — monkeypatched —
the forbidden letter, inside the data root, inside the repository, BitLocker off; unknown
BitLocker = a warning; no ``--i-know``); an interrupted backup never looks complete and
its staging leftover is swept; a torn trailing ledger line survives backup + restore;
``list`` and the doctor's ``last_backup`` row (info / warn / pass / warn past 7 days);
``MWH_BACKUP_TARGET``; the CLI wiring, docs and the import budget.

Everything asserted or printed is paths, counts, hashes and timestamps — no data file is
ever opened beyond copying and hashing bytes, and the only Parquet involved is a two-row
aggregate frame the run ledger wrote (no identifier column, ids nowhere).
"""

from __future__ import annotations

import json
import os
import shutil
import warnings
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

import helpers
from mimicwarehouse import __version__, backup, config, doctor, fsio, guard
from mimicwarehouse import run as run_mod
from mimicwarehouse.backup import (
    BACKUP_DIR_PREFIX,
    BACKUP_SET,
    MANIFEST_FILENAME,
    MAX_AGE_DAYS,
    STUDY_EXCLUDED_SUFFIXES,
    BackupError,
    NothingToBackUp,
    TargetRefused,
    enumerate_set,
    last_backup,
    list_backups,
    restore_backup,
    rules_for,
    run_backup,
    sha256_of,
    target_problem,
    verify_backup,
)
from mimicwarehouse.cli import DIAGNOSTIC_COMMANDS, app
from mimicwarehouse.config import DriveInfo, Settings
from mimicwarehouse.protocol import registry as registry_mod
from mimicwarehouse.safe import build_runs_db, runs_db_path

pytestmark = pytest.mark.ep_52

DOCS = helpers.WORKSPACE / "docs"
PROVENANCE_DOC = DOCS / "methods" / "provenance.md"
CLOUD = DriveInfo(letter="C", drive_type="DRIVE_FIXED", label="Google Drive", filesystem="FAT32")
FIXED_NTFS = DriveInfo(letter="G", drive_type="DRIVE_FIXED", label="Windows", filesystem="NTFS")
T0 = datetime(2026, 9, 17, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def data_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """A throw-away data root **and** a throw-away workspace root, so the machine's own
    ``mimicwarehouse/.env`` (which carries the owner's ``MWH_BACKUP_TARGET`` since EP-52)
    and ``mwh.toml`` never reach these tests — a real target would turn the "no target"
    refusal below into a real backup of the temp root (the EP-167 ``workspace`` pattern)."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(config, "workspace_root", lambda: workspace)
    yield helpers.tmp_data_root(monkeypatch, tmp_path)
    config.configure()


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> dict[str, datetime]:
    """A fake UTC clock: every backup gets its own second, and ages are controllable."""
    state = {"now": T0}

    def now() -> datetime:
        state["now"] = state["now"] + timedelta(seconds=1)
        return state["now"]

    monkeypatch.setattr(backup, "_now", now)
    return state


@pytest.fixture
def encrypted(monkeypatch: pytest.MonkeyPatch) -> None:
    """BitLocker reads "on" for every drive (no PowerShell probe in the suite)."""
    monkeypatch.setattr(backup, "_bitlocker_state", lambda drive: 1)


@dataclass(frozen=True, slots=True)
class Populated:
    settings: Settings
    run_ids: tuple[str, str]
    protocol_hash: str
    expected: frozenset[str]
    artifacts: frozenset[str]


@pytest.fixture
def populated(data_root: Path, encrypted: None) -> Populated:
    """The synthetic non-reproducible state of the module docstring."""
    import polars as pl

    settings = config.get_settings()
    with run_mod.start(
        "first", tier="fixture", kind="analysis", settings=settings, doctor=False
    ) as r1:
        r1.record_sql("counts", "SELECT count(*) AS n FROM meta.tables")
        r1.save_table("summary", pl.DataFrame({"band": ["a", "b"], "n": [30, 40]}))
        r1.save_figure("bars", {"mark": "bar", "data": {"values": []}})
    with run_mod.start("second", tier="fixture", kind="qc", settings=settings, doctor=False) as r2:
        r2.record_sql("probe", "SELECT 1 AS n")
    run_mod.bench("bench", "standalone", wall_s=0.5, tier="fixture", settings=settings)
    frozen = registry_mod.freeze(registry_mod.seed_path(), settings)
    jobs = settings.layout["runs_jobs"]
    jobs.mkdir(parents=True, exist_ok=True)
    _write(jobs / "job.json", "{}\n")
    _write(jobs / "job.log", "log line\n")
    build_runs_db(settings)  # warehouse/runs.duckdb — rebuilt by `mwh runs refresh`, never copied
    registry = settings.layout["models"] / "registry"
    _write(registry / "model_card.json", '{"name": "m"}\n')
    _write(registry / "m.yaml", "name: m\n")
    _write(registry / "notes.txt", "not registry metadata\n")
    _write(settings.layout["models"] / "weights.pkl", "weights\n")
    study = settings.layout["studies"] / "s1"
    _write(study / "protocol.yaml", "id: s1\n")
    _write(study / "notes.md", "# notes\n")
    _write(study / "review.jsonl", '{"reviewed": true}\n')
    for name in ("cohort.parquet", "extract.csv", "cache.duckdb", "bundle.zip", "frame.pkl"):
        _write(study / name, "data-shaped\n")
    expected = frozenset(
        {
            "runs/audit.jsonl",
            "runs/benchmarks.jsonl",
            "runs/ledger.jsonl",
            "runs/protocols.jsonl",
            f"runs/protocols/{frozen.hash}.yaml",
            f"runs/{r1.run_id}/manifest.json",
            f"runs/{r1.run_id}/sql/counts.sql",
            f"runs/{r2.run_id}/manifest.json",
            f"runs/{r2.run_id}/sql/probe.sql",
            "models/registry/m.yaml",
            "models/registry/model_card.json",
            "studies/s1/notes.md",
            "studies/s1/protocol.yaml",
            "studies/s1/review.jsonl",
        }
    )
    artifacts = frozenset(
        {f"runs/{r1.run_id}/tables/summary.parquet", f"runs/{r1.run_id}/figures/bars.json"}
    )
    return Populated(settings, (r1.run_id, r2.run_id), frozen.hash, expected, artifacts)


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def _paths(manifest: dict[str, Any]) -> list[str]:
    return sorted(str(f["path"]) for f in manifest["files"])


def _flip_last_byte(path: Path) -> None:
    data = bytearray(path.read_bytes())
    data[-1] ^= 0x01
    path.write_bytes(bytes(data))


def _view_count(settings: Settings, view: str) -> int:
    import duckdb

    con = duckdb.connect(str(runs_db_path(settings)), read_only=True)
    try:
        row = con.execute(f"SELECT count(*) FROM {view}").fetchone()
    finally:
        con.close()
    assert row is not None
    return int(row[0])


# ---------------------------------------------------------------------------
# 1. The set and its enumeration (brief item 1)
# ---------------------------------------------------------------------------


def test_backup_set_is_the_briefed_globs_and_enumerates_exactly(populated: Populated) -> None:
    assert [r.pattern for r in BACKUP_SET] == [
        "runs/*.jsonl",
        "runs/protocols/**",
        "runs/*/manifest.json",
        "runs/*/sql/**",
        "models/registry/**",
        "studies/**",
        "runs/*/tables/**",
        "runs/*/figures/**",
    ]
    assert [r.pattern for r in rules_for(False)] == [r.pattern for r in BACKUP_SET[:6]]
    assert all(r.artifacts for r in BACKUP_SET[6:]) and not any(r.artifacts for r in BACKUP_SET[:6])
    assert {".parquet", ".duckdb", ".csv", ".pkl", ".zip"} <= STUDY_EXCLUDED_SUFFIXES
    assert set(guard.DATA_EXTENSIONS) >= STUDY_EXCLUDED_SUFFIXES
    assert ".jsonl" not in STUDY_EXCLUDED_SUFFIXES, "a study's own JSONL record is state"
    root = populated.settings.data_root
    selected = enumerate_set(root)
    assert list(selected) == [r.pattern for r in rules_for(False)]
    flat = sorted(backup.relative_posix(p, root) for files in selected.values() for p in files)
    assert flat == sorted(populated.expected)
    assert selected["runs/*.jsonl"] and len(selected["runs/*.jsonl"]) == 4
    assert len(selected["runs/protocols/**"]) == 1 and len(selected["studies/**"]) == 3
    with_artifacts = enumerate_set(root, include_run_artifacts=True)
    flat_all = sorted(
        backup.relative_posix(p, root) for files in with_artifacts.values() for p in files
    )
    assert flat_all == sorted(populated.expected | populated.artifacts)
    assert not any("/jobs/" in p or p.startswith("warehouse/") for p in flat_all)
    assert not any(p.endswith((".pkl", ".log", ".txt", ".zip")) for p in flat_all)


# ---------------------------------------------------------------------------
# 2. backup run: exactly the set, a complete manifest, read-only frozen copies
# ---------------------------------------------------------------------------


def test_run_copies_exactly_the_set_with_a_complete_manifest(
    populated: Populated, tmp_path: Path, clock: dict[str, datetime]
) -> None:
    settings = populated.settings
    target = tmp_path / "backups"
    result = run_backup(target, settings)
    assert result.path == target / result.backup_id and result.path.is_dir()
    assert result.backup_id.startswith(BACKUP_DIR_PREFIX) and result.backup_id.endswith("Z")
    assert result.warnings == ()
    manifest = json.loads((result.path / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    assert manifest == result.manifest
    assert _paths(manifest) == sorted(populated.expected)
    assert set(manifest) == {
        "backup_id",
        "tool",
        "tool_version",
        "timestamp_utc",
        "data_root",
        "git_sha",
        "include_run_artifacts",
        "rules",
        "files_per_rule",
        "n_files",
        "total_bytes",
        "files",
    }
    assert manifest["backup_id"] == result.backup_id and manifest["tool"] == "mimicwarehouse"
    assert manifest["tool_version"] == __version__
    assert Path(manifest["data_root"]) == settings.data_root.resolve()
    assert manifest["git_sha"] == run_mod.git_sha()
    assert datetime.fromisoformat(manifest["timestamp_utc"]) == clock["now"]
    assert manifest["include_run_artifacts"] is False
    assert manifest["rules"] == [r.pattern for r in rules_for(False)]
    assert (
        sum(manifest["files_per_rule"].values()) == manifest["n_files"] == len(populated.expected)
    )
    assert manifest["total_bytes"] == sum(f["bytes"] for f in manifest["files"]) > 0
    for entry in manifest["files"]:
        rel = Path(*entry["path"].split("/"))
        source, copy = settings.data_root / rel, result.path / rel
        assert copy.is_file() and copy.read_bytes() == source.read_bytes()
        assert entry["sha256"] == sha256_of(source) == sha256_of(copy)
        assert entry["bytes"] == source.stat().st_size == copy.stat().st_size
    # the frozen copy travels with its read-only attribute (docs/gotchas.md section 2)
    frozen_copy = result.path / "runs" / "protocols" / f"{populated.protocol_hash}.yaml"
    assert registry_mod.is_read_only(frozen_copy)
    assert not (result.path / "runs" / "jobs").exists()
    assert not (result.path / "warehouse").exists()
    assert not list(result.path.rglob("*.parquet"))
    # run artifacts opt in
    second = run_backup(target, settings, include_run_artifacts=True)
    assert second.backup_id != result.backup_id
    assert _paths(second.manifest) == sorted(populated.expected | populated.artifacts)
    assert second.manifest["include_run_artifacts"] is True
    assert second.manifest["rules"] == [r.pattern for r in BACKUP_SET]
    assert (second.path / "runs" / populated.run_ids[0] / "tables" / "summary.parquet").is_file()
    listed = list_backups(target)
    assert [b.backup_id for b in listed] == [second.backup_id, result.backup_id], "newest first"
    assert all(b.valid and b.n_files and b.total_bytes for b in listed)
    newest = last_backup(target)
    assert newest is not None and newest.backup_id == second.backup_id
    assert newest.age_days is not None and 0 <= newest.age_days < 0.01
    assert verify_backup(result.path).ok and verify_backup(second.path).ok


def test_run_on_an_empty_root_refuses_and_a_second_run_in_the_same_second_is_an_error(
    data_root: Path, encrypted: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = config.get_settings()
    with pytest.raises(NothingToBackUp, match="nothing to back up"):
        run_backup(tmp_path / "backups", settings)
    assert not (tmp_path / "backups").exists() or not list((tmp_path / "backups").iterdir())
    fsio.append_jsonl(settings.layout["runs"] / "audit.jsonl", {"audit_id": "x"})
    monkeypatch.setattr(backup, "_now", lambda: T0)
    first = run_backup(tmp_path / "backups", settings)
    assert _paths(first.manifest) == ["runs/audit.jsonl"]
    with pytest.raises(BackupError, match="already exists"):
        run_backup(tmp_path / "backups", settings)


# ---------------------------------------------------------------------------
# 3. verify: a flipped byte, a missing file, an unexpected file (brief item 3)
# ---------------------------------------------------------------------------


def test_verify_names_a_tampered_missing_and_unexpected_file(
    populated: Populated, tmp_path: Path, clock: dict[str, datetime]
) -> None:
    result = run_backup(tmp_path / "backups", populated.settings)
    runner = helpers.cli_runner()
    ok = runner.invoke(app, ["backup", "verify", str(result.path)])
    assert ok.exit_code == 0, ok.output
    assert "ok" in ok.stdout and f"{result.n_files:,} file(s)" in ok.stdout
    _flip_last_byte(result.path / "runs" / "ledger.jsonl")
    verdict = verify_backup(result.path)
    assert not verdict.ok and verdict.mismatched == ("runs/ledger.jsonl",)
    assert verdict.missing == () and verdict.unexpected == ()
    failed = runner.invoke(app, ["backup", "verify", str(result.path)])
    assert failed.exit_code == 1, failed.output
    assert "mismatch runs/ledger.jsonl" in failed.stdout and "failed" in failed.stdout
    (result.path / "studies" / "s1" / "notes.md").unlink()
    _write(result.path / "extra.txt", "not in the manifest\n")
    verdict = verify_backup(result.path)
    assert verdict.missing == ("studies/s1/notes.md",) and verdict.unexpected == ("extra.txt",)
    assert verdict.findings == [
        "mismatch runs/ledger.jsonl",
        "missing studies/s1/notes.md",
        "unexpected extra.txt",
    ]
    as_json = runner.invoke(app, ["backup", "verify", str(result.path), "--json"])
    assert as_json.exit_code == 1
    payload = json.loads(as_json.stdout)
    assert payload["ok"] is False and payload["mismatched"] == ["runs/ledger.jsonl"]
    assert payload["missing"] == ["studies/s1/notes.md"] and payload["unexpected"] == ["extra.txt"]
    with pytest.raises(BackupError, match="not a backup directory"):
        verify_backup(tmp_path)
    not_a_backup = runner.invoke(app, ["backup", "verify", str(tmp_path)])
    assert not_a_backup.exit_code == 2 and "not a backup directory" in not_a_backup.stderr


# ---------------------------------------------------------------------------
# 4. restore + the drill: identical hashes, `runs refresh` over the restored ledgers
# ---------------------------------------------------------------------------


def test_restore_reproduces_hashes_and_runs_refresh_builds_views_over_it(
    populated: Populated, tmp_path: Path, clock: dict[str, datetime]
) -> None:
    settings = populated.settings
    result = run_backup(tmp_path / "backups", settings)
    to = tmp_path / "restored"
    dry = restore_backup(result.path, to, settings, dry_run=True)
    assert dry.dry_run and dry.n_files == result.n_files and not to.exists()
    restored = restore_backup(result.path, to, settings)
    assert restored.destination == to.resolve() and restored.n_files == result.n_files
    for entry in result.manifest["files"]:
        rel = Path(*entry["path"].split("/"))
        assert sha256_of(to / rel) == entry["sha256"] == sha256_of(settings.data_root / rel)
    assert sorted(backup.relative_posix(p, to) for p in to.rglob("*") if p.is_file()) == sorted(
        populated.expected
    )
    restored_settings = Settings(data_root=to)
    frozen_copy = registry_mod.frozen_path(populated.protocol_hash, restored_settings)
    assert registry_mod.is_read_only(frozen_copy), "the read-only attribute is restored too"
    assert registry_mod.verify(populated.protocol_hash, restored_settings).ok
    assert [line.hash for line in registry_mod.read_registry(restored_settings)] == [
        populated.protocol_hash
    ]
    assert [line["run_id"] for line in run_mod.read_ledger(restored_settings)] == list(
        populated.run_ids
    )
    assert run_mod.read_manifest(populated.run_ids[0], restored_settings).sql == {
        "counts": "sql/counts.sql"
    }
    # the drill: rebuild runs.duckdb from the restored ledgers (the global --data-root form)
    runner = helpers.cli_runner()
    refreshed = runner.invoke(app, ["--data-root", str(to), "runs", "refresh"])
    assert refreshed.exit_code == 0, refreshed.output
    assert runs_db_path(restored_settings).is_file()
    assert _view_count(restored_settings, "ledger") == 2
    assert _view_count(restored_settings, "manifests") == 2
    assert _view_count(restored_settings, "protocols") == 1
    assert _view_count(restored_settings, "benchmarks") == 1
    assert _view_count(restored_settings, "audit") >= 1
    config.configure()
    # never over an existing non-empty runs/
    with pytest.raises(TargetRefused, match="not empty"):
        restore_backup(result.path, to, settings)
    refused = runner.invoke(app, ["backup", "restore", "--from", str(result.path), "--to", str(to)])
    assert refused.exit_code == 3 and "not empty" in refused.stderr
    config.configure()
    # a damaged backup is never restored
    _flip_last_byte(result.path / "runs" / "protocols.jsonl")
    elsewhere = tmp_path / "elsewhere"
    with pytest.raises(BackupError, match="fails verification"):
        restore_backup(result.path, elsewhere, settings)
    assert not elsewhere.exists()
    damaged = runner.invoke(
        app, ["backup", "restore", "--from", str(result.path), "--to", str(elsewhere)]
    )
    assert damaged.exit_code == 1 and "fails verification" in damaged.stderr
    config.configure()


def test_restore_cli_dry_run_and_the_next_step_hint(
    populated: Populated, tmp_path: Path, clock: dict[str, datetime]
) -> None:
    result = run_backup(tmp_path / "backups", populated.settings)
    runner = helpers.cli_runner()
    to = tmp_path / "drill"
    dry = runner.invoke(
        app, ["backup", "restore", "--from", str(result.path), "--to", str(to), "--dry-run"]
    )
    assert dry.exit_code == 0, dry.output
    assert "would restore" in dry.stdout and not to.exists()
    real = runner.invoke(
        app, ["backup", "restore", "--from", str(result.path), "--to", str(to), "--json"]
    )
    assert real.exit_code == 0, real.output
    payload = json.loads(real.stdout)
    assert payload["n_files"] == result.n_files and payload["dry_run"] is False
    assert (to / "runs" / "ledger.jsonl").is_file()
    plain = runner.invoke(
        app,
        ["backup", "restore", "--from", str(result.path), "--to", str(tmp_path / "drill2")],
    )
    assert plain.exit_code == 0 and "runs refresh" in plain.stdout
    config.configure()


# ---------------------------------------------------------------------------
# 5. Target safety (brief item 2)
# ---------------------------------------------------------------------------


def _drive_info_with(monkeypatch: pytest.MonkeyPatch, marker: str, info: DriveInfo) -> None:
    original = config.drive_info

    def fake(path: Path | str) -> DriveInfo:
        return info if marker.lower() in str(path).lower() else original(path)

    monkeypatch.setattr(config, "drive_info", fake)


def test_target_refusals(
    populated: Populated, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = populated.settings
    runner = helpers.cli_runner()
    # inside the data root
    inside = settings.data_root / "backups"
    assert "inside the data root" in (target_problem(inside, settings).problem or "")
    with pytest.raises(TargetRefused, match="inside the data root"):
        run_backup(inside, settings)
    assert not inside.exists()
    cli = runner.invoke(app, ["backup", "run", "--target", str(inside)])
    assert cli.exit_code == 3 and "refused" in cli.stderr and "data root" in cli.stderr
    # inside the repository
    in_repo = helpers.REPO_ROOT / "scratch-backup-probe"
    assert "inside the repository" in (target_problem(in_repo, settings).problem or "")
    with pytest.raises(TargetRefused, match="inside the repository"):
        run_backup(in_repo, settings)
    assert not in_repo.exists()
    # a volume the EP-3 detector flags (monkeypatched: a Google Drive FAT32 volume)
    _drive_info_with(monkeypatch, "cloudmount", CLOUD)
    cloud = tmp_path / "cloudmount" / "backups"
    verdict = target_problem(cloud, settings)
    assert verdict.problem and "sync client" in verdict.problem and "D-29" in verdict.problem
    with pytest.raises(TargetRefused, match="sync client"):
        run_backup(cloud, settings)
    assert not cloud.exists()
    cli = runner.invoke(app, ["backup", "run", "--target", str(cloud)])
    assert cli.exit_code == 3 and "D-29" in cli.stderr
    # the forbidden drive letter alone (volume info reads as a fixed NTFS disk)
    _drive_info_with(monkeypatch, "G:", FIXED_NTFS)
    letter = target_problem(Path(r"G:\anything"), settings)
    assert letter.problem and "forbidden list" in letter.problem
    cli = runner.invoke(app, ["backup", "run", "--target", r"G:\anything"])
    assert cli.exit_code == 3 and "forbidden" in cli.stderr
    # BitLocker off refuses; unknown is a warning and the backup proceeds
    monkeypatch.setattr(backup, "_bitlocker_state", lambda drive: 2)
    with pytest.raises(TargetRefused, match="BitLocker is off"):
        run_backup(tmp_path / "plain", settings)
    monkeypatch.setattr(backup, "_bitlocker_state", lambda drive: None)
    monkeypatch.setattr(backup, "_now", lambda: T0)
    result = run_backup(tmp_path / "unknown-state", settings)
    assert result.warnings and "unknown" in result.warnings[0]
    assert "BitLocker" in result.warnings[0] and verify_backup(result.path).ok
    monkeypatch.setattr(backup, "_now", lambda: T0 + timedelta(seconds=1))
    cli = runner.invoke(app, ["backup", "run", "--target", str(tmp_path / "unknown-state")])
    assert cli.exit_code == 0, cli.output
    assert "BitLocker" in cli.stderr and "backup mwh-backup-" in cli.stdout
    # the override flag of the planning text does not exist
    no_override = runner.invoke(app, ["backup", "run", "--target", str(tmp_path / "x"), "--i-know"])
    assert no_override.exit_code == 2 and "--i-know" in no_override.output
    # no target at all
    none = runner.invoke(app, ["backup", "run"])
    assert none.exit_code == 2 and "MWH_BACKUP_TARGET" in none.stderr
    config.configure()


def test_restore_destination_is_checked_by_the_same_detector(
    populated: Populated,
    tmp_path: Path,
    clock: dict[str, datetime],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = run_backup(tmp_path / "backups", populated.settings)
    _drive_info_with(monkeypatch, "cloudmount", CLOUD)
    with pytest.raises(TargetRefused, match="D-29"):
        restore_backup(result.path, tmp_path / "cloudmount" / "restore", populated.settings)
    assert not (tmp_path / "cloudmount").exists()


# ---------------------------------------------------------------------------
# 6. Interrupted / damaged copies never look complete
# ---------------------------------------------------------------------------


def test_interrupted_backup_never_looks_complete_and_its_leftover_is_swept(
    populated: Populated,
    tmp_path: Path,
    clock: dict[str, datetime],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = populated.settings
    target = tmp_path / "backups"
    real_copy2 = shutil.copy2
    calls = {"n": 0}

    def failing_copy2(src: Any, dst: Any, **kw: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 3:
            raise OSError("disk pulled")
        return real_copy2(src, dst, **kw)

    monkeypatch.setattr(backup.shutil, "copy2", failing_copy2)
    with pytest.raises(OSError, match="disk pulled"):
        run_backup(target, settings)
    leftovers = [p.name for p in target.iterdir()]
    assert leftovers and all(name.endswith(".new") for name in leftovers), leftovers
    assert list_backups(target) == [] and last_backup(target) is None
    runner = helpers.cli_runner()
    listed = runner.invoke(app, ["backup", "list", "--target", str(target)])
    assert listed.exit_code == 0 and "no backups" in listed.stdout
    # a copy that does not re-hash (a silent quarantine) is a hard error, nothing published
    monkeypatch.setattr(backup, "_now", lambda: T0 + timedelta(minutes=1))

    def truncating_copy2(src: Any, dst: Any, **kw: Any) -> Any:
        out = real_copy2(src, dst, **kw)
        Path(dst).write_bytes(b"")
        return out

    monkeypatch.setattr(backup.shutil, "copy2", truncating_copy2)
    with pytest.raises(BackupError, match="does not hash to its source"):
        run_backup(target, settings)
    assert list_backups(target) == []
    cli = runner.invoke(app, ["backup", "run", "--target", str(target)])
    assert cli.exit_code == 1 and "does not hash" in cli.stderr
    # the next successful run sweeps the staging leftovers and is the only backup listed
    monkeypatch.setattr(backup.shutil, "copy2", real_copy2)
    monkeypatch.setattr(backup, "_now", lambda: T0 + timedelta(minutes=2))
    result = run_backup(target, settings)
    assert [p.name for p in target.iterdir()] == [result.backup_id]
    assert [b.backup_id for b in list_backups(target)] == [result.backup_id]
    config.configure()


def test_torn_trailing_ledger_line_survives_backup_and_restore(
    populated: Populated, tmp_path: Path, clock: dict[str, datetime]
) -> None:
    settings = populated.settings
    with run_mod.ledger_path(settings).open("ab") as f:
        f.write(b'{"run_id": "2026')
    result = run_backup(tmp_path / "backups", settings)
    assert verify_backup(result.path).ok
    to = tmp_path / "restored"
    restore_backup(result.path, to, settings)
    restored_settings = Settings(data_root=to)
    assert (to / "runs" / "ledger.jsonl").read_bytes() == run_mod.ledger_path(settings).read_bytes()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        lines = fsio.read_jsonl(to / "runs" / "ledger.jsonl")
    assert [line["run_id"] for line in lines] == list(populated.run_ids)
    assert any("torn trailing line" in str(w.message) for w in caught)
    refreshed = helpers.cli_runner().invoke(app, ["--data-root", str(to), "runs", "refresh"])
    assert refreshed.exit_code == 0, refreshed.output
    assert _view_count(restored_settings, "ledger") == 2
    config.configure()


# ---------------------------------------------------------------------------
# 7. list, the doctor's last-backup row, MWH_BACKUP_TARGET
# ---------------------------------------------------------------------------


def test_list_and_the_doctor_last_backup_row(
    populated: Populated,
    tmp_path: Path,
    clock: dict[str, datetime],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = populated.settings
    target = tmp_path / "backups"
    assert list_backups(target) == [] and last_backup(None) is None
    # no target configured → info; an empty target → warn; a fresh backup → pass
    assert settings.backup_target is None
    none = doctor.check_last_backup(settings)
    assert none.status == "info" and "MWH_BACKUP_TARGET" in none.detail
    assert none.value["target"] is None and none.value["max_age_days"] == MAX_AGE_DAYS
    configured = Settings(data_root=settings.data_root, backup_target=target)
    assert configured.backup_target == target.resolve() or configured.backup_target == target
    empty = doctor.check_last_backup(configured)
    assert empty.status == "warn" and "no backup under" in empty.detail
    result = run_backup(target, settings)
    fresh = doctor.check_last_backup(configured)
    assert fresh.status == "pass" and result.backup_id in fresh.detail
    assert fresh.value["backup_id"] == result.backup_id and fresh.value["n_files"] == result.n_files
    assert fresh.value["age_days"] is not None and fresh.value["age_days"] < 0.01
    # eight days later → warn
    clock["now"] = clock["now"] + timedelta(days=MAX_AGE_DAYS + 1)
    stale = doctor.check_last_backup(configured)
    assert stale.status == "warn" and f"older than {MAX_AGE_DAYS} days" in stale.detail
    assert stale.value["age_days"] is not None and stale.value["age_days"] > MAX_AGE_DAYS
    # list: an unreadable backup dir is shown as such, staging leftovers are ignored
    (target / f"{BACKUP_DIR_PREFIX}20260101T000000Z").mkdir()
    (target / f"{BACKUP_DIR_PREFIX}20260102T000000Z.new").mkdir()
    listed = list_backups(target)
    assert [(b.backup_id, b.valid) for b in listed] == [
        (result.backup_id, True),
        (f"{BACKUP_DIR_PREFIX}20260101T000000Z", False),
    ]
    newest = last_backup(target)
    assert newest is not None and newest.backup_id == result.backup_id
    runner = helpers.cli_runner()
    table = runner.invoke(app, ["backup", "list", "--target", str(target)])
    assert table.exit_code == 0, table.output
    assert result.backup_id in table.stdout and "MB" in table.stdout
    assert "missing/unreadable" in table.stdout and ".new" not in table.stdout
    as_json = runner.invoke(app, ["backup", "list", "--target", str(target), "--json"])
    payload = json.loads(as_json.stdout)
    assert payload["target"] == str(target) and len(payload["backups"]) == 2
    assert payload["backups"][0]["backup_id"] == result.backup_id
    # the check sits right after bitlocker and the CLI names it
    ids = list(doctor.CHECK_IDS)
    assert len(ids) == 16 and ids.index("last_backup") == ids.index("bitlocker") + 1
    assert "last_backup" in (doctor.doctor_command.__doc__ or "")
    # MWH_BACKUP_TARGET is the default target of run / list and the doctor row
    monkeypatch.setenv("MWH_BACKUP_TARGET", str(target))
    config.configure()
    assert config.get_settings().backup_target == target
    clock["now"] = clock["now"] + timedelta(days=1)
    env_run = runner.invoke(app, ["backup", "run", "--json"])
    assert env_run.exit_code == 0, env_run.output
    summary = json.loads(env_run.stdout)
    assert summary["path"].startswith(str(target)) and "files" not in summary
    assert summary["n_files"] == result.n_files and summary["total_bytes"] == result.total_bytes
    env_list = runner.invoke(app, ["backup", "list", "--json"])
    assert json.loads(env_list.stdout)["backups"][0]["backup_id"] == summary["backup_id"]
    report = runner.invoke(app, ["doctor", "--json"])
    assert report.exit_code in (0, 1), report.output
    row = next(c for c in json.loads(report.stdout)["checks"] if c["id"] == "last_backup")
    assert row["status"] == "pass" and row["value"]["backup_id"] == summary["backup_id"]
    config.configure()


def test_plain_run_output_prints_the_path_and_totals_only(
    populated: Populated, tmp_path: Path, clock: dict[str, datetime]
) -> None:
    runner = helpers.cli_runner()
    result = runner.invoke(app, ["backup", "run", "--target", str(tmp_path / "backups")])
    assert result.exit_code == 0, result.output
    assert "backup mwh-backup-" in result.stdout and "MB" in result.stdout
    assert "file(s)" in result.stdout and "set:" in result.stdout and "verify with" in result.stdout
    for run_id in populated.run_ids:
        assert run_id not in result.stdout, "file names never appear in the plain output"
    assert populated.protocol_hash not in result.stdout
    config.configure()


# ---------------------------------------------------------------------------
# 8. Settings, wiring, docs, import budget
# ---------------------------------------------------------------------------


def test_settings_field_wiring_docs_and_import_budget(data_root: Path) -> None:
    assert "backup_target" in Settings.model_fields
    assert Settings(data_root=data_root).backup_target is None
    relative = Settings(data_root=data_root, backup_target="rel-backups")
    assert relative.backup_target is not None and relative.backup_target.is_absolute()
    example = (helpers.WORKSPACE / ".env.example").read_text(encoding="utf-8")
    assert "MWH_BACKUP_TARGET=" in example
    result = helpers.cli_runner().invoke(app, ["backup", "--help"])
    assert result.exit_code == 0
    for command in ("run", "verify", "restore", "list"):
        assert command in result.stdout
    assert "backup" in helpers.cli_runner().invoke(app, ["--help"]).stdout
    assert "backup" not in DIAGNOSTIC_COMMANDS, "backup reads the data root"
    text = PROVENANCE_DOC.read_text(encoding="utf-8")
    for needle in (
        "Backup & restore",
        "mwh backup run",
        "mwh backup verify",
        "mwh backup restore",
        "mwh backup list",
        "MWH_BACKUP_TARGET",
        "backup_manifest.json",
        "runs/protocols",
        "--include-run-artifacts",
        "runs refresh",
        "last_backup",
        "BitLocker",
        "GOVERNANCE",
    ):
        assert needle in text, f"docs/methods/provenance.md lacks {needle!r}"
    readme = (helpers.WORKSPACE / "README.md").read_text(encoding="utf-8")
    assert "mwh backup run" in readme and "16 host checks" in readme
    governance = (helpers.WORKSPACE / "GOVERNANCE.md").read_text(encoding="utf-8")
    assert "`mwh backup` (EP-52)" in governance
    violations = guard.scan([PROVENANCE_DOC], helpers.REPO_ROOT)
    assert not violations, [f"{v.rule}: {v.path}" for v in violations]
    helpers.assert_import_budget(lazy=("mimicwarehouse.run", "mimicwarehouse.safe"))
    helpers.assert_import_budget(
        "mimicwarehouse.backup", lazy=("mimicwarehouse.run", "mimicwarehouse.doctor")
    )
    assert os.environ.get("MWH_BACKUP_TARGET") is None
