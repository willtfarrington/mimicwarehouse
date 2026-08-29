"""Build connection for staging (EP-17 item 1; DESIGN §6).

:func:`open_build_connection` is the loader's DuckDB opener: it refuses to run when the
data-root volume is below the tier's free-space guard, refuses a DuckDB other than the
EP-1 pin (one storage format across every process), then delegates the actual connect to
:func:`mimicwarehouse.inventory.open_connection` — the de-facto opener since EP-10/167 and
deliberately the **one** implementation (retro FC-7): in-memory DuckDB with the explicit
``duckdb_settings("build")`` profile (memory_limit / threads / temp_directory /
max_temp_directory_size / ``preserve_insertion_order = false``), after creating the
``temp_directory`` parent (DuckDB 1.5.5 errors on first spill otherwise, retro CFG-3).

``memory_limit`` overrides the build profile for one connection (a small stage does not
need 36 GB); everything else always comes from :meth:`Settings.duckdb_settings`.
"""

from __future__ import annotations

import re
from importlib import metadata
from typing import TYPE_CHECKING

from mimicwarehouse.config import Settings, Tier, get_settings, require_free_space

if TYPE_CHECKING:  # pragma: no cover
    import duckdb

#: The requirement pin this package declares for duckdb (EP-1, ``pyproject.toml``).
_DUCKDB_PIN_RE = re.compile(r"^duckdb\s*==\s*([0-9][0-9a-zA-Z.]*)\s*$")


class DuckDBVersionError(RuntimeError):
    """The installed DuckDB does not match the EP-1 pin (storage formats differ across
    versions; DESIGN §6 demands one version in every process)."""


def pinned_duckdb_version() -> str:
    """The exact DuckDB version this package pins (``duckdb==X`` in its requirements)."""
    for req in metadata.requires("mimicwarehouse") or ():
        m = _DUCKDB_PIN_RE.match(req.split(";")[0].strip())
        if m:
            return m.group(1)
    raise DuckDBVersionError(
        "mimicwarehouse declares no exact 'duckdb==<version>' requirement — the EP-1 pin "
        "is missing (pyproject.toml)"
    )


def require_pinned_duckdb() -> str:
    """Raise :class:`DuckDBVersionError` unless the installed DuckDB equals the pin."""
    import duckdb

    pinned = pinned_duckdb_version()
    if duckdb.__version__ != pinned:
        raise DuckDBVersionError(
            f"installed duckdb {duckdb.__version__} != pinned {pinned} (EP-1/DESIGN §6: one "
            "DuckDB version per machine — re-run `uv sync` or bump the pin deliberately)"
        )
    return pinned


def open_build_connection(
    settings: Settings | None = None,
    *,
    tier: Tier | str | None = None,
    memory_limit: str | None = None,
) -> duckdb.DuckDBPyConnection:
    """In-memory DuckDB for a build, after the guards (EP-17 item 1).

    Order: DuckDB version pin → tier-aware free-space guard
    (:meth:`Settings.min_free_gb_for`; 1 GB for ``fixture`` so tmp-root test builds pass,
    the full ``min_free_gb`` for the credentialed tiers) → the one connection factory
    (:func:`inventory.open_connection`). ``memory_limit`` (e.g. ``'8GB'``) overrides the
    build profile's for this connection only.
    """
    from mimicwarehouse.inventory import open_connection

    settings = settings or get_settings()
    resolved_tier = tier if tier is not None else settings.default_tier
    require_pinned_duckdb()
    require_free_space(settings.data_root, settings.min_free_gb_for(resolved_tier))
    con = open_connection(settings)
    if memory_limit is not None:
        escaped = memory_limit.replace("'", "''")
        con.execute(f"SET memory_limit = '{escaped}'")
    return con


__all__ = [
    "DuckDBVersionError",
    "open_build_connection",
    "pinned_duckdb_version",
    "require_pinned_duckdb",
]
