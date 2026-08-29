"""EP-171 — write canary: the five write-shape passes (Parquet burst, large sequential,
manifest churn, rename-aside swap, delete loop), re-read verification, refusals, and the
``mwh canary write`` CLI.

Fixture tier only: every run goes against a temp data root via ``helpers.tmp_data_root``
(never ``C:\\mimicdata``); everything written is synthetic bytes generated in-process
(ids >= 90 000 000). Runs are kept small (``--small 1..5``, ``--large-mb 1..2``) so the
suite stays fast; the real-size run is the brief's live item, not a test.
"""

from __future__ import annotations

import json
from collections import namedtuple
from pathlib import Path

import pytest

import helpers
from mimicwarehouse import canary, config, guard
from mimicwarehouse.canary import (
    LARGE_MB_CAP,
    MANIFEST_NAME,
    MANIFEST_REWRITES,
    PHASES,
    SWAP_NAME,
    CanaryError,
    canary_root,
    run_canary,
)
from mimicwarehouse.cli import DIAGNOSTIC_COMMANDS, app
from mimicwarehouse.config import DriveInfo

pytestmark = pytest.mark.ep_171

DiskUsage = namedtuple("DiskUsage", "total used free")


def _fake_disk_usage(free_gb: float, total_gb: float = 950.0):
    def fake(path):
        total = int(total_gb * config.GB)
        free = int(free_gb * config.GB)
        return DiskUsage(total=total, used=total - free, free=free)

    return fake


@pytest.fixture
def data_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Throw-away MWH_DATA_ROOT (helpers.tmp_data_root) with plenty of fake free space, so
    the guard's verdict never depends on the host's actual disk."""
    root = helpers.tmp_data_root(monkeypatch, tmp_path)
    monkeypatch.setattr(config.shutil, "disk_usage", _fake_disk_usage(500.0))
    yield root
    config.configure()


@pytest.fixture
def settings(data_root: Path) -> config.Settings:
    return config.get_settings()


def _no_leak(text: str, *, band_check: bool = True) -> None:
    """Canary output is counts/bytes/seconds only; human-readable output must also carry no
    bare 8-digit real-band token (guard G4) — ``--json`` keeps raw integers (EP-10 pattern)."""
    assert "SENTINEL" not in text  # nothing cell-like exists at all in this module
    if band_check:
        assert guard.id_band_hits(text.encode("utf-8")) == []


# ---------------------------------------------------------------------------
# run_canary: phases, layout, cleanup
# ---------------------------------------------------------------------------


def test_five_phases_run_in_order_and_leave_nothing_behind(settings) -> None:
    events: list[str] = []
    result = run_canary(settings, small_files=2, large_mb=1, observer=lambda e, p: events.append(e))
    starts = [e.split(":")[0] for e in events if e.endswith(":start")]
    assert starts == list(PHASES)
    assert [p.name for p in result.passes] == list(PHASES)
    assert not canary_root(settings).exists()
    assert result.passes[-1].ops == {"kept": 0, "deleted": 1}
    assert "cleanup:done" in events and "cleanup:kept" not in events
    assert result.seconds > 0 and result.finished


def test_keep_layout_sizes_and_manifest(settings) -> None:
    result = run_canary(settings, small_files=5, large_mb=2, keep=True)
    root = canary_root(settings)
    assert root.is_dir()

    # burst pass: 5 files, one per Hive-style bucket dir, ~1 MB each
    small = result.passes[0]
    assert small.name == "small" and small.files == 5 and small.ops == {"buckets": 5}
    buckets = sorted(p.name for p in (root / "small").iterdir())
    assert buckets == [f"subject_bucket={n}" for n in range(5)]
    parts = sorted((root / "small").rglob("part-*.parquet"))
    assert len(parts) == 5
    for p in parts:
        assert 500_000 < p.stat().st_size < 2_500_000
    assert small.bytes == sum(p.stat().st_size for p in parts)

    # large pass: exactly one file of ~large_mb
    large = result.passes[1]
    large_files = list((root / "large").iterdir())
    assert [p.name for p in large_files] == ["events.parquet"]
    assert large.files == 1 and large.bytes == large_files[0].stat().st_size
    assert 1_200_000 < large.bytes < 3_500_000  # ~2 MB
    assert large.mb_per_s is not None and large.mb_per_s > 0

    # manifest churn: one line per file written so far + the final rev line
    manifest = result.passes[2]
    lines = (root / MANIFEST_NAME).read_text(encoding="utf-8").splitlines()
    assert manifest.ops == {"appends": 6, "rewrites": MANIFEST_REWRITES}
    assert json.loads(lines[0]) == {"rev": MANIFEST_REWRITES - 1}
    records = [json.loads(line) for line in lines[1:]]
    assert len(records) == 6
    assert {r["rel_path"] for r in records} >= {"large/events.parquet"}

    # swap: only the live file remains, byte-for-byte the .new content (verified inside)
    assert [p.name for p in (root / "swap").iterdir()] == [SWAP_NAME]
    assert result.passes[3].ops == {"swaps": 1}

    # cleanup kept the tree
    assert result.passes[4].ops == {"kept": 1, "deleted": 0}
    assert result.keep is True


def test_stale_tree_from_a_previous_keep_run_is_removed_first(settings) -> None:
    root = canary_root(settings)
    root.mkdir(parents=True)
    (root / "stale.txt").write_text("leftover", encoding="utf-8")
    result = run_canary(settings, small_files=1, large_mb=1)
    assert result.removed_stale_tree is True
    assert not root.exists()


def test_swap_sequence_is_write_new_rename_aside_replace(settings) -> None:
    seen: dict[str, set[str]] = {}

    def obs(event: str, path: Path) -> None:
        if event.startswith("swap:"):
            d = path if path.is_dir() else path.parent
            seen[event] = {p.name for p in d.iterdir()}

    run_canary(settings, small_files=1, large_mb=1, keep=True, observer=obs)
    live, new, old = SWAP_NAME, SWAP_NAME + ".new", SWAP_NAME + ".old"
    assert seen["swap:start"] == set()
    assert seen["swap:live-written"] == {live}
    assert seen["swap:new-written"] == {live, new}  # written, live still in place
    assert seen["swap:aside"] == {new, old}  # live renamed aside
    assert seen["swap:replaced"] == {live, old}  # .new replaced in as live
    assert seen["swap:old-removed"] == {live}  # .old gone


# ---------------------------------------------------------------------------
# Re-read verification: deletion / alteration = hard error, tree left as evidence
# ---------------------------------------------------------------------------


def test_deleted_file_is_a_hard_error_and_tree_is_left_in_place(settings) -> None:
    def obs(event: str, path: Path) -> None:
        if event == "small:written":
            next(path.rglob("part-*.parquet")).unlink()  # a "silent quarantine"

    with pytest.raises(CanaryError, match="re-read verification failed in pass 'small'"):
        run_canary(settings, small_files=2, large_mb=1, observer=obs)
    assert canary_root(settings).exists()  # evidence for the D-42 triage, not cleaned up


def test_altered_file_is_a_hard_error(settings) -> None:
    def obs(event: str, path: Path) -> None:
        if event == "large:written":
            with path.open("ab") as f:
                f.write(b"tampered")

    with pytest.raises(CanaryError, match="size changed"):
        run_canary(settings, small_files=1, large_mb=1, observer=obs)


# ---------------------------------------------------------------------------
# Refusals: parameters, free space, unsafe root
# ---------------------------------------------------------------------------


def test_large_mb_cap_and_bad_parameters_refused(settings) -> None:
    with pytest.raises(ValueError, match="above the canary cap"):
        run_canary(settings, large_mb=LARGE_MB_CAP + 1)
    with pytest.raises(ValueError, match="--small"):
        run_canary(settings, small_files=0)
    with pytest.raises(ValueError, match="--large-mb"):
        run_canary(settings, large_mb=0)
    assert not canary_root(settings).exists()  # refused before touching the tree


def test_insufficient_free_space_refused(settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config.shutil, "disk_usage", _fake_disk_usage(50.0))
    with pytest.raises(config.DiskGuardError, match="refuses to write"):
        run_canary(settings, small_files=1, large_mb=1)
    assert not canary_root(settings).exists()


# ---------------------------------------------------------------------------
# CLI: mwh canary write
# ---------------------------------------------------------------------------

runner = helpers.cli_runner()


def test_cli_default_run_json_is_leak_free(data_root: Path) -> None:
    res = runner.invoke(app, ["canary", "write", "--small", "2", "--large-mb", "1", "--json"])
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    assert payload["survived"] is True
    assert [p["name"] for p in payload["passes"]] == list(PHASES)
    assert payload["files"] == 5  # 2 small + large + manifest + swap
    assert payload["small_files"] == 2 and payload["large_mb"] == 1
    assert not Path(payload["root"]).exists()  # default: nothing left behind
    _no_leak(res.output, band_check=False)  # JSON keeps raw integers (EP-10 pattern)


def test_cli_keep_human_output_is_leak_free_and_g4_clean(data_root: Path) -> None:
    res = runner.invoke(app, ["canary", "write", "--small", "2", "--large-mb", "1", "--keep"])
    assert res.exit_code == 0, res.output
    assert "canary write: OK" in res.output
    assert "tree kept" in res.output
    assert "," in res.output  # thousands separators on byte counts (G4)
    _no_leak(res.output)


def test_cli_refuses_large_mb_above_cap(data_root: Path) -> None:
    res = runner.invoke(app, ["canary", "write", "--large-mb", str(LARGE_MB_CAP + 1)])
    assert res.exit_code == 2 and "above the canary cap" in res.output
    assert not (data_root / "tmp" / "canary").exists()


def test_cli_refuses_insufficient_free_space(
    data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config.shutil, "disk_usage", _fake_disk_usage(50.0))
    res = runner.invoke(app, ["canary", "write", "--small", "1", "--large-mb", "1"])
    assert res.exit_code == 2 and "refuses to write below" in res.output


def test_cli_refuses_unsafe_data_root(data_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cloud = DriveInfo(letter="C", drive_type="DRIVE_FIXED", label="Google Drive", filesystem="NTFS")
    monkeypatch.setattr(config, "drive_info", lambda path: cloud)
    res = runner.invoke(app, ["canary", "write", "--small", "1", "--large-mb", "1"])
    assert res.exit_code == 2 and "sync client" in res.output
    assert not (data_root / "tmp" / "canary").exists()


def test_canary_is_not_a_diagnostic_command() -> None:
    """It writes under the data root, so the D-29 refusals must run (the inventory precedent);
    ``--help`` still works over an unsafe root through EP-167's lazy validation."""
    assert "canary" not in DIAGNOSTIC_COMMANDS
    assert canary.canary_app.info.name == "canary"
