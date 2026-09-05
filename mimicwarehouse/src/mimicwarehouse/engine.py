"""The one DuckDB connection factory (EP-33 B3; DESIGN §6; retro CFG-2/FC-7/INV-15).

Every ``duckdb.connect`` in ``src/`` goes through :func:`open_duckdb` — the guard test in
``tests/ep/test_ep33.py`` pins that — so one place applies the explicit per-profile
settings (``Settings.duckdb_settings``: memory_limit / threads / temp_directory /
max_temp_directory_size / insertion order), creates the temp-directory parent DuckDB
1.5.5 needs before its first spill (retro CFG-3), and documents the two engine gotchas
the catalog and safe-query layers learned the hard way:

* **The in-process instance cache is keyed on the database path.** Reconnecting while any
  connection in this process still holds the path returns the *same* instance — a stale
  catalog after the rename-aside swap, and an ``ATTACH`` done on one connection is
  visible instance-wide and outlives that connection. Hence :func:`attach_read_only` is
  always ``ATTACH IF NOT EXISTS``; never assume a fresh instance.
* **``10**9`` binds as DOUBLE.** DuckDB 1.5.5 evaluates the ``**`` operator in floating
  point, so an integer spelled ``10**9`` reaches ``range()`` and friends as a DOUBLE —
  spell large integer literals out in full (``1000000000``).

Profiles are a required positional so ad-hoc callers choose consciously: ``build`` is the
one 36 GB build-profile connection per machine (ARCH-11 — the loader's
``open_build_connection`` adds its version-pin and free-space guards *around* this
factory); ``app`` is the read-side profile for catalogs, ``runs.duckdb`` and the safe-query
parser connection.

The module imports ``duckdb`` lazily (``mwh --help`` import budget, DESIGN §15).
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from mimicwarehouse.config import Settings, get_settings

if TYPE_CHECKING:  # pragma: no cover
    import duckdb

Profile = Literal["build", "app"]

#: Poll interval while ``retry_missing_s`` waits for a file to appear.
RETRY_MISSING_SLEEP_S = 0.01


def open_duckdb(
    profile: Profile,
    *,
    database: str | Path | None = None,
    read_only: bool = False,
    settings: Settings | None = None,
    memory_limit: str | None = None,
    retry_missing_s: float = 0.0,
) -> duckdb.DuckDBPyConnection:
    """Connect with the explicit ``profile`` settings (module docstring).

    ``database`` None is in-memory (``read_only`` must then be False). ``memory_limit``
    overrides the profile's for this connection only. ``retry_missing_s`` > 0 retries the
    open while the file is transiently absent — the rename-aside swap's sub-millisecond
    window (EP-170 amendment 1) — and re-raises DuckDB's own error once the deadline
    passes with the file still missing (callers wrap it in their own error type).
    """
    import duckdb

    settings = settings or get_settings()
    settings.layout["tmp_duckdb"].mkdir(parents=True, exist_ok=True)
    cfg: dict[str, Any] = dict(settings.duckdb_settings(profile))
    target = ":memory:" if database is None else str(database)
    if target == ":memory:" and read_only:
        raise ValueError("open_duckdb: an in-memory database cannot be read_only")
    deadline = time.monotonic() + retry_missing_s
    while True:
        try:
            con = duckdb.connect(target, read_only=read_only, config=cfg)
            break
        except (duckdb.Error, FileNotFoundError):
            if target == ":memory:" or Path(target).exists() or time.monotonic() >= deadline:
                raise
            time.sleep(RETRY_MISSING_SLEEP_S)
    if memory_limit is not None:
        escaped = memory_limit.replace("'", "''")
        con.execute(f"SET memory_limit = '{escaped}'")
    return con


def attach_read_only(con: duckdb.DuckDBPyConnection, path: Path, alias: str) -> None:
    """``ATTACH IF NOT EXISTS '<path>' AS <alias> (READ_ONLY)`` — ``IF NOT EXISTS`` because
    of the path-keyed instance cache (module docstring): an earlier connection's attach
    may still be present instance-wide."""
    escaped = Path(path).resolve().as_posix().replace("'", "''")
    con.execute(f"ATTACH IF NOT EXISTS '{escaped}' AS {alias} (READ_ONLY)")


def detach(con: duckdb.DuckDBPyConnection, alias: str) -> None:
    """``DETACH DATABASE IF EXISTS <alias>`` — the one detach (EP-35). Because an attach
    lives on the path-keyed instance, a file republished by the rename-aside swap while
    the instance lived (``runs.duckdb`` after ``mwh runs refresh``) would otherwise keep
    being served from the stale, delete-pending handle; ``detach`` then
    :func:`attach_read_only` re-opens the current file, instance-wide — fine for a store
    that only one caller attaches."""
    con.execute(f"DETACH DATABASE IF EXISTS {alias}")


__all__ = ["RETRY_MISSING_SLEEP_S", "Profile", "attach_read_only", "detach", "open_duckdb"]
