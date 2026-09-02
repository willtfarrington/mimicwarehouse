"""EP-33 acceptance — the consolidation re-plan of P0-P2 (roadmap/EP-33-replan-p2.md).

Foundation primitives (written first, in-session): :mod:`mimicwarehouse.fsio`,
:mod:`mimicwarehouse.publish`, :mod:`mimicwarehouse.engine` and the EP-33 additions to
:mod:`mimicwarehouse.console`. The Workstream B migrations extend this module in their own
sections below (B1 safe-query, B2/loader, B3 engine canon, B4 hygiene, B5/B6 gates).

Fixture tier only; nothing here touches the data root except through ``tmp_path``.
"""

from __future__ import annotations

import json
import logging
import os
import warnings
from pathlib import Path

import pytest

import helpers
from mimicwarehouse import config, console, engine, fsio, publish

pytestmark = pytest.mark.ep_33


@pytest.fixture
def data_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    yield helpers.tmp_data_root(monkeypatch, tmp_path)
    config.configure()


# ---------------------------------------------------------------------------
# fsio — the JSONL canon (B8; LGR-1/LGR-2/LGR-4)
# ---------------------------------------------------------------------------


def test_append_jsonl_canonical_lines(tmp_path: Path) -> None:
    path = tmp_path / "runs" / "ledger.jsonl"  # parents created
    fsio.append_jsonl(path, {"b": 1, "a": [1, 2]})
    fsio.append_jsonl_lines(path, [{"z": None}, {"y": "x"}])
    raw = path.read_bytes()
    assert raw == b'{"a": [1, 2], "b": 1}\n{"z": null}\n{"y": "x"}\n'
    assert fsio.read_jsonl(path) == [{"a": [1, 2], "b": 1}, {"z": None}, {"y": "x"}]


def test_append_jsonl_fsyncs_and_checks_short_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "ledger.jsonl"
    synced: list[int] = []
    monkeypatch.setattr(os, "fsync", lambda fd: synced.append(fd))
    fsio.append_jsonl(path, {"k": 1})
    assert len(synced) == 1
    real_write = os.write
    monkeypatch.setattr(os, "write", lambda fd, blob: real_write(fd, blob[:3]))
    with pytest.raises(fsio.ShortWriteError, match="short write"):
        fsio.append_jsonl(path, {"k": 2})


def test_read_jsonl_tolerates_only_a_torn_trailing_line(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    fsio.append_jsonl_lines(path, [{"n": 1}, {"n": 2}])
    with path.open("ab") as f:
        f.write(b'{"n": 3, "torn": tr')  # crash mid-write, no newline
    with pytest.warns(UserWarning, match="torn trailing line"):
        assert fsio.read_jsonl(path) == [{"n": 1}, {"n": 2}]
    # a malformed line that is NOT the last one is corruption, not a tear
    path.write_bytes(b'{"n": 1}\nnot json\n{"n": 3}\n')
    with pytest.raises(fsio.TornLedgerError, match="line 2 of 3"):
        fsio.read_jsonl(path)
    assert fsio.read_jsonl(tmp_path / "missing.jsonl") == []


def test_atomic_write_text_retries_then_cleans_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "state.json"
    fsio.atomic_write_text(target, "one\n")
    assert target.read_text(encoding="utf-8") == "one\n"
    assert not target.with_name("state.json.tmp").exists()
    monkeypatch.setattr(fsio.time, "sleep", lambda s: None)

    def always_locked(src, dst):
        raise PermissionError(5, "locked")

    monkeypatch.setattr(fsio.os, "replace", always_locked)
    with pytest.raises(PermissionError):
        fsio.atomic_write_text(target, "two\n", retries=3)
    assert not target.with_name("state.json.tmp").exists()  # WIN-7: no stranded .tmp
    assert target.read_text(encoding="utf-8") == "one\n"


# ---------------------------------------------------------------------------
# publish — the one swap primitive (B2; CLI-1/LDR-2, WIN-3/WIN-4)
# ---------------------------------------------------------------------------


def _mkdir_with(path: Path, marker: str) -> Path:
    path.mkdir(parents=True)
    (path / "part-0.parquet").write_bytes(marker.encode())
    return path


def test_swap_dir_first_publish_restage_and_observer_order(tmp_path: Path) -> None:
    dest = tmp_path / "table"
    events: list[tuple[str, str]] = []
    obs = lambda event, path: events.append((event, path.name))  # noqa: E731
    publish.swap_dir(_mkdir_with(publish.new_path_for(dest), "v1"), dest, observer=obs)
    assert (dest / "part-0.parquet").read_bytes() == b"v1"
    assert events == [("publish", "table")]
    events.clear()
    publish.swap_dir(_mkdir_with(publish.new_path_for(dest), "v2"), dest, observer=obs)
    assert (dest / "part-0.parquet").read_bytes() == b"v2"
    assert events == [("aside", "table.old"), ("publish", "table"), ("remove-old", "table.old")]
    assert not publish.old_path_for(dest).exists()
    assert not publish.new_path_for(dest).exists()


def test_swap_dir_crash_recovery_restores_old(tmp_path: Path) -> None:
    dest = tmp_path / "table"
    _mkdir_with(publish.old_path_for(dest), "live-before-crash")  # crash between steps 3 and 4
    new = _mkdir_with(publish.new_path_for(dest), "fresh")
    events: list[str] = []
    publish.swap_dir(new, dest, observer=lambda e, p: events.append(e))
    assert events[0] == "restore-old"
    assert (dest / "part-0.parquet").read_bytes() == b"fresh"
    assert not publish.old_path_for(dest).exists()


def test_swap_dir_bad_arguments(tmp_path: Path) -> None:
    dest = tmp_path / "table"
    with pytest.raises(publish.SwapError, match="not a directory"):
        publish.swap_dir(publish.new_path_for(dest), dest)
    _mkdir_with(dest, "x")
    with pytest.raises(publish.SwapError, match="same path"):
        publish.swap_dir(dest, dest)


def test_swap_dir_vanished_new_rolls_back_instead_of_deleting_old(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CLI-1/LDR-2: a ``.new`` that vanishes at the publish rename (quarantine) must never
    read as success and must never delete the only live copy."""
    dest = tmp_path / "table"
    _mkdir_with(dest, "live")
    new = _mkdir_with(publish.new_path_for(dest), "fresh")
    real_rename = os.rename

    def rename_but_new_vanishes(src, dst):
        if Path(src) == new:
            import shutil

            shutil.rmtree(new)  # the quarantine scenario
            real_rename(src, dst)  # raises FileNotFoundError
        else:
            real_rename(src, dst)

    monkeypatch.setattr(publish.os, "rename", rename_but_new_vanishes)
    with pytest.raises(publish.SwapError, match="vanished"):
        publish.swap_dir(new, dest)
    assert (dest / "part-0.parquet").read_bytes() == b"live"  # rolled back
    assert not publish.old_path_for(dest).exists()


def test_swap_dir_defers_old_removal_when_locked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """WIN-3: a scanner holding ``.old`` after publish is a warning, not a failed stage."""
    dest = tmp_path / "table"
    _mkdir_with(dest, "v1")
    new = _mkdir_with(publish.new_path_for(dest), "v2")
    monkeypatch.setattr(publish.time, "sleep", lambda s: None)
    real_rmtree = publish.shutil.rmtree

    def rmtree_locked(path, *a, **kw):
        if Path(path) == publish.old_path_for(dest):
            raise PermissionError(5, "held by a scanner")
        real_rmtree(path, *a, **kw)

    monkeypatch.setattr(publish.shutil, "rmtree", rmtree_locked)
    events: list[str] = []
    with caplog.at_level(logging.WARNING, logger="mimicwarehouse.publish"):
        publish.swap_dir(new, dest, observer=lambda e, p: events.append(e))
    assert (dest / "part-0.parquet").read_bytes() == b"v2"
    assert events[-1] == "defer-old"
    assert "stale-.old sweep" in caplog.text
    monkeypatch.setattr(publish.shutil, "rmtree", real_rmtree)
    # the next swap's step 2 removes the leftover
    publish.swap_dir(_mkdir_with(publish.new_path_for(dest), "v3"), dest)
    assert not publish.old_path_for(dest).exists()


def test_swap_file_publish_and_blocked_fast_fail(tmp_path: Path) -> None:
    dest = tmp_path / "tier.duckdb"
    new = publish.new_path_for(dest)
    new.write_bytes(b"v1")
    events: list[str] = []
    publish.swap_file(new, dest, blocked_hint="close it", observer=lambda e, p: events.append(e))
    assert dest.read_bytes() == b"v1" and events == ["publish"]
    new.write_bytes(b"v2")
    publish.swap_file(new, dest, blocked_hint="close it")
    assert dest.read_bytes() == b"v2" and not publish.old_path_for(dest).exists()
    with pytest.raises(publish.SwapError, match="not a file"):
        publish.swap_file(new, dest, blocked_hint="close it")
    if os.name != "nt":
        pytest.skip("the non-sharing handle semantics are Windows-only")
    new.write_bytes(b"v3")
    with dest.open("rb"), pytest.raises(publish.SwapBlockedError, match="close it"):
        publish.swap_file(new, dest, blocked_hint="close it")  # plain handle: no share-delete
    assert dest.read_bytes() == b"v2" and new.read_bytes() == b"v3"  # nothing changed


def test_retry_helpers_tolerate_missing_only_where_meant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    publish.rmtree(tmp_path / "absent")  # no error
    publish.unlink(tmp_path / "absent.txt")
    with pytest.raises(FileNotFoundError):
        publish.replace(tmp_path / "absent.txt", tmp_path / "x.txt")
    calls = {"n": 0}

    def flaky() -> None:
        calls["n"] += 1
        if calls["n"] < 3:
            raise PermissionError(5, "transient")

    monkeypatch.setattr(publish.time, "sleep", lambda s: None)
    publish.retry_permission(flaky)
    assert calls["n"] == 3
    with pytest.raises(PermissionError):
        publish.retry_permission(lambda: (_ for _ in ()).throw(PermissionError(5, "x")), retries=2)


# ---------------------------------------------------------------------------
# engine — the one connection factory (B3)
# ---------------------------------------------------------------------------


def test_open_duckdb_applies_profile_and_creates_tmp_parent(data_root: Path) -> None:
    settings = config.get_settings()
    assert not settings.layout["tmp_duckdb"].exists()
    con = engine.open_duckdb("app", settings=settings)
    try:
        assert settings.layout["tmp_duckdb"].is_dir()
        (limit,) = con.execute("SELECT current_setting('memory_limit')").fetchone()  # type: ignore[misc]
        assert limit  # the explicit profile value, not DuckDB's default
    finally:
        con.close()
    con = engine.open_duckdb("build", settings=settings, memory_limit="1GB")
    try:
        (limit,) = con.execute("SELECT current_setting('memory_limit')").fetchone()  # type: ignore[misc]
        assert str(limit).startswith(("1.0 GiB", "1GB", "953.6 MiB"))
    finally:
        con.close()
    with pytest.raises(ValueError, match="read_only"):
        engine.open_duckdb("app", read_only=True, settings=settings)


def test_open_duckdb_retry_missing_reraises_duckdbs_error(data_root: Path) -> None:
    import duckdb

    settings = config.get_settings()
    missing = data_root / "warehouse" / "nope.duckdb"
    with pytest.raises((duckdb.Error, FileNotFoundError)):
        engine.open_duckdb(
            "app", database=missing, read_only=True, settings=settings, retry_missing_s=0.05
        )


def test_attach_read_only_is_idempotent(data_root: Path) -> None:
    settings = config.get_settings()
    side = data_root / "warehouse" / "side.duckdb"
    side.parent.mkdir(parents=True)
    w = engine.open_duckdb("app", database=side, settings=settings)
    w.execute("CREATE TABLE t AS SELECT 1 AS x")
    w.close()
    con = engine.open_duckdb("app", settings=settings)
    try:
        engine.attach_read_only(con, side, "side")
        engine.attach_read_only(con, side, "side")  # IF NOT EXISTS: no error
        assert con.execute("SELECT count(*) FROM side.t").fetchone() == (1,)
    finally:
        con.close()


# ---------------------------------------------------------------------------
# console — exit codes, fail, emit_json, progress logging (B8)
# ---------------------------------------------------------------------------


def test_console_exit_codes_and_fail(capsys: pytest.CaptureFixture[str]) -> None:
    import typer

    assert (console.EXIT_OK, console.EXIT_FINDINGS, console.EXIT_USAGE, console.EXIT_REFUSED) == (
        0,
        1,
        2,
        3,
    )
    with pytest.raises(typer.Exit) as info:
        console.fail("mwh sql", "refused: [not markup]", code=console.EXIT_REFUSED)
    assert info.value.exit_code == 3
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "mwh sql: refused: [not markup]" in captured.err


def test_emit_json_raw_ints_plain_newlines(capsys: pytest.CaptureFixture[str]) -> None:
    console.emit_json({"rows": 1234567, "when": Path("x")})
    out = capsys.readouterr().out
    assert out.endswith("\n") and "\r" not in out
    assert json.loads(out) == {"rows": 1234567, "when": "x"}


def test_configure_progress_logging_idempotent(tmp_path: Path) -> None:
    logger = logging.getLogger("mimicwarehouse")
    before = list(logger.handlers)
    try:
        log = tmp_path / "job.log"
        console.configure_progress_logging(to_file=log)
        console.configure_progress_logging(to_file=log)
        added = [h for h in logger.handlers if h not in before]
        assert len(added) == 1 and isinstance(added[0], logging.FileHandler)
        logging.getLogger("mimicwarehouse.test").info("progress line rows=3")
        for h in added:
            h.flush()
        assert "progress line rows=3" in log.read_text(encoding="utf-8")
    finally:
        for h in list(logger.handlers):
            if h not in before:
                logger.removeHandler(h)
                h.close()


def test_foundation_modules_are_light() -> None:
    """fsio/publish/engine/console must not add heavy imports to the mwh start-up set."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        proc = helpers.fresh_interpreter(
            [
                "-c",
                "import sys; import mimicwarehouse.fsio, mimicwarehouse.publish, "
                "mimicwarehouse.engine, mimicwarehouse.console; "
                "print(sorted(m for m in sys.modules if m in "
                "('duckdb','pandas','polars','pyarrow','numpy')))",
            ]
        )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "[]"
