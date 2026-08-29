"""Read-only catalog opener (EP-21 item 2; DESIGN §6, GOVERNANCE §4).

:func:`open_catalog` is the **one** way any reader (tests, the app, EP-30's
``safe_query``, notebooks) attaches a tier catalog: ``read_only=True`` with the explicit
app-profile DuckDB configuration (``memory_limit`` = ``settings.duckdb_app_memory_limit``,
``threads``, ``temp_directory``, ``max_temp_directory_size``), then the hardening
``SET``\\ s (no extension autoinstall/autoload, ``HTTPFileSystem`` disabled — the catalog
must never reach the network). It asserts the catalog's recorded DuckDB version equals
the running one (catalogs are derived and disposable: on mismatch the fix is
``mwh build --tier <t> --select catalog``, ledger ARCH-16), refuses a ``.new`` file
(an unpublished build), warns when ``settings.dev_buckets`` drifts from the recorded
ones (EP-170/ARCH-8), and retries the sub-millisecond ``FileNotFoundError`` window of
the rename-aside swap (DESIGN §6 note).

``role`` defaults to ``settings.role`` (``MWH_ROLE``): ``agent`` for every session;
only the owner sets ``owner`` in their own shell (CLAUDE.md §2). Nothing here reads a
row — the connection is handed to callers who are bound by GOVERNANCE §4/§5.
"""

from __future__ import annotations

import json
import time
import warnings
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mimicwarehouse.config import Role, Settings, Tier, get_settings

if TYPE_CHECKING:  # pragma: no cover
    import duckdb

#: How long :func:`open_catalog` retries the swap's no-file window before giving up.
OPEN_RETRY_S = 0.5
OPEN_RETRY_SLEEP_S = 0.01

#: The hardening applied to every catalog connection (GOVERNANCE §4: a catalog reader
#: must never install extensions or touch the network).
HARDENING_SQL: tuple[str, ...] = (
    "SET autoinstall_known_extensions = false",
    "SET autoload_known_extensions = false",
    "SET disabled_filesystems = 'HTTPFileSystem'",
)


class CatalogOpenError(RuntimeError):
    """The catalog cannot be opened (missing, unpublished ``.new``, version mismatch,
    not a mimicwarehouse catalog, unknown role)."""


def catalog_path(tier: Tier | str, settings: Settings | None = None) -> Path:
    """``<data_root>/warehouse/<tier>.duckdb`` (uniform across all four tiers, EP-167)."""
    return (settings or get_settings()).catalog_path(tier)


def _connect_with_retry(path: Path, config: dict[str, Any], tier: str) -> duckdb.DuckDBPyConnection:
    """``duckdb.connect(read_only=True)``, retrying while the file is transiently absent
    (the rename-aside swap's sub-millisecond window, EP-170 amendment 1)."""
    import duckdb

    deadline = time.monotonic() + OPEN_RETRY_S
    while True:
        try:
            return duckdb.connect(str(path), read_only=True, config=config)
        except (duckdb.Error, FileNotFoundError) as exc:
            if path.exists() or time.monotonic() >= deadline:
                if not path.exists():
                    raise CatalogOpenError(
                        f"no {tier} catalog at {path} — build it with "
                        f"`mwh build --tier {tier} --select catalog`"
                    ) from exc
                raise
            time.sleep(OPEN_RETRY_SLEEP_S)


def open_catalog(
    tier: Tier | str,
    *,
    settings: Settings | None = None,
    role: Role | str | None = None,
    path: Path | None = None,
) -> duckdb.DuckDBPyConnection:
    """A READ_ONLY connection to the tier's catalog (module docstring).

    ``path`` overrides the resolved catalog file (tests only); a ``.new`` path — an
    unpublished build — is always refused. Close the connection when done.
    """
    import duckdb

    settings = settings or get_settings()
    resolved_role = role if role is not None else settings.role
    if resolved_role not in ("agent", "owner"):
        raise CatalogOpenError(f"unknown role {resolved_role!r}; expected 'agent' or 'owner'")
    target = Path(path) if path is not None else settings.catalog_path(tier)
    if target.name.endswith(".new"):
        raise CatalogOpenError(
            f"{target}: refusing to open an unpublished .new catalog — only `mwh build` "
            "writes it, and only the published <tier>.duckdb is ever read (DESIGN §6)"
        )
    # DuckDB 1.5.5 needs the temp-directory parent to exist before the first spill
    # (EP-167, retro CFG-3) — every connection site ensures it.
    settings.layout["tmp_duckdb"].mkdir(parents=True, exist_ok=True)
    con = _connect_with_retry(target, dict(settings.duckdb_settings("app")), str(tier))
    try:
        for statement in HARDENING_SQL:
            con.execute(statement)
        try:
            row = con.execute(
                "SELECT duckdb_version, dev_buckets FROM meta.catalog_info"
            ).fetchone()
        except duckdb.Error as exc:
            raise CatalogOpenError(
                f"{target}: no meta.catalog_info — not a mimicwarehouse catalog; rebuild "
                f"with `mwh build --tier {tier} --select catalog`"
            ) from exc
        if row is None:
            raise CatalogOpenError(f"{target}: meta.catalog_info is empty — rebuild it")
        recorded_version, recorded_buckets = row
        if recorded_version != duckdb.__version__:
            raise CatalogOpenError(
                f"{target}: built with DuckDB {recorded_version}, this process runs "
                f"{duckdb.__version__} — catalogs are derived and disposable: rebuild with "
                f"`mwh build --tier {tier} --select catalog` (DESIGN §6, ARCH-16)"
            )
        try:
            buckets = [int(b) for b in json.loads(recorded_buckets)]
        except (TypeError, ValueError):
            buckets = None
        if buckets is not None and buckets != list(settings.dev_buckets):
            warnings.warn(
                f"{tier} catalog was built with dev_buckets {buckets} but settings now say "
                f"{list(settings.dev_buckets)} — rebuild with `mwh build --tier {tier} "
                "--select catalog` (EP-170/ARCH-8)",
                stacklevel=2,
            )
    except Exception:
        con.close()
        raise
    return con


__all__ = [
    "HARDENING_SQL",
    "OPEN_RETRY_S",
    "CatalogOpenError",
    "catalog_path",
    "open_catalog",
]
