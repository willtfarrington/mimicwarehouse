"""The one publish primitive — rename-aside swaps for directories and single files
(EP-33 B2; DESIGN §5/§6; supersedes ``mimicwarehouse.paths`` and
``catalog.build.swap_catalog``; ledger findings CLI-1/LDR-2, WIN-2/WIN-3/WIN-4).

``os.replace(new → dest)`` fails on Windows whenever ``dest`` is a directory that exists,
and a single-file replace fails while a non-sharing reader holds the file, so every
publish in this project is the **rename-aside two-step** over one retry/crash-recovery
core:

1. crash recovery — a ``<dest>.old`` beside a *missing* ``dest`` is the previous live
   copy of an interrupted swap: rename it back before doing anything else;
2. a stale ``.old`` beside an existing ``dest`` is dead weight from a crash after step 4:
   remove it;
3. ``os.rename(dest → dest.old)`` (a first publish skips this — no ``dest`` yet);
4. ``os.rename(new → dest)`` — then **verify ``dest`` exists** before touching ``.old``:
   a ``new`` that vanished (anti-virus quarantine, D-42) must roll ``.old`` back and
   raise, never read as success (CLI-1/LDR-2);
5. remove ``dest.old``; when the remove still fails after the retry budget (a scanner
   walking a just-renamed multi-GB tree), warn and leave it for the next swap's step 2
   (WIN-3) — the new data is already live.

Two variants share that core and differ only where the OS forces them to:

* :func:`swap_dir` (loader tables): every rename/remove retries on ``PermissionError``
  (AV/indexer holds on directories are transient); crash-safe, **not** atomic — between 3
  and 4 there is no ``dest``, and readers must be closed first (the dev catalog's views
  point at the same files a restage rewrites).
* :func:`swap_file` (catalogs, ``runs.duckdb``): the aside rename in step 3 is **not**
  retried — a plain (non-``FILE_SHARE_DELETE``) handle is not transient — and fails fast
  with :class:`SwapBlockedError` carrying the caller's ``blocked_hint`` (the live file is
  intact); step 4 is a bare ``os.replace`` with a sub-millisecond no-``dest`` window that
  ``catalog.connect.open_catalog`` compensates for by retrying its open.

``FileNotFoundError`` is tolerated **only** on the remove/restore operations (something
else already cleaned up), never on the publishing renames. An ``observer(event, path)``
callable sees every step (``restore-old``, ``remove-stale-old``, ``aside``, ``publish``,
``rollback``, ``remove-old``, ``defer-old``) — the seam the EP-171 canary pattern uses.

The retry helpers (:func:`retry_permission`, :func:`rmtree`, :func:`unlink`,
:func:`replace`) are the same policy for every other rename/remove the loader performs
on freshly written files (pass-2 publish, stale-``.new`` sweeps — WIN-2/WIN-4).

Nothing here reads file contents; the module only renames and deletes what the project
itself wrote.
"""

from __future__ import annotations

import logging
import os
import shutil
import time
from collections.abc import Callable
from pathlib import Path

from mimicwarehouse.fsio import RETRIES, RETRY_BASE_SLEEP_S

_LOG = logging.getLogger(__name__)

#: Suffixes of the staging / aside paths beside a live path.
NEW_SUFFIX = ".new"
OLD_SUFFIX = ".old"

Observer = Callable[[str, Path], None]


class SwapError(RuntimeError):
    """A swap could not complete (bad arguments, or the publish rename failed and the
    previous live copy was rolled back)."""


class SwapBlockedError(SwapError):
    """The aside rename of a live file failed (a non-sharing reader holds it); the live
    file is intact and nothing was changed."""


def new_path_for(dest: Path) -> Path:
    """``<dest>.new`` — where a build writes before a swap publishes it."""
    dest = Path(dest)
    return dest.with_name(dest.name + NEW_SUFFIX)


def old_path_for(dest: Path) -> Path:
    """``<dest>.old`` — the aside name the previous live copy briefly holds."""
    dest = Path(dest)
    return dest.with_name(dest.name + OLD_SUFFIX)


def retry_permission(
    op: Callable[[], None],
    *,
    tolerate_missing: bool = False,
    retries: int = RETRIES,
) -> None:
    """Run ``op`` retrying on the transient Windows ``PermissionError`` (linear back-off,
    ~10 s total at the defaults). ``tolerate_missing`` returns silently on
    ``FileNotFoundError`` — for remove/restore operations only."""
    for attempt in range(retries):
        try:
            op()
            return
        except PermissionError:
            if attempt == retries - 1:
                raise
            time.sleep(RETRY_BASE_SLEEP_S * (attempt + 1))
        except FileNotFoundError:
            if tolerate_missing:
                return
            raise


def rmtree(path: Path) -> None:
    """``shutil.rmtree`` with the retry policy; a missing tree is not an error."""
    retry_permission(lambda: shutil.rmtree(path), tolerate_missing=True)


def unlink(path: Path) -> None:
    """``os.unlink`` with the retry policy; a missing file is not an error."""
    retry_permission(lambda: os.unlink(path), tolerate_missing=True)


def replace(src: Path, dst: Path) -> None:
    """``os.replace`` with the retry policy (a missing ``src`` raises)."""
    retry_permission(lambda: os.replace(src, dst))


def _notify(observer: Observer | None, event: str, path: Path) -> None:
    if observer is not None:
        observer(event, path)


def _recover_and_sweep(
    dest: Path,
    old: Path,
    *,
    is_kind: Callable[[Path], bool],
    remove: Callable[[Path], None],
    observer: Observer | None,
) -> None:
    # 1. crash recovery: an interrupted swap left the live copy under `.old`.
    if not dest.exists() and is_kind(old):
        _notify(observer, "restore-old", old)
        retry_permission(lambda: os.rename(old, dest), tolerate_missing=True)
    # 2. a stale `.old` beside a live dest is a leftover: remove it.
    if old.exists():
        _notify(observer, "remove-stale-old", old)
        remove(old)


def _finish(
    dest: Path, old: Path, *, remove: Callable[[Path], None], observer: Observer | None
) -> None:
    # 5. drop the aside copy; defer when a scanner still holds it (WIN-3).
    if old.exists():
        _notify(observer, "remove-old", old)
        try:
            remove(old)
        except OSError as exc:
            _notify(observer, "defer-old", old)
            _LOG.warning(
                "%s: could not remove yet (%s: %s); the next swap's stale-.old sweep removes it",
                old,
                exc.__class__.__name__,
                exc,
            )


def swap_dir(new: Path, dest: Path, *, observer: Observer | None = None) -> None:
    """Publish directory ``new`` as ``dest`` via the rename-aside two-step (module note).

    ``new`` must be an existing directory; ``dest`` may or may not exist (first publish).
    Raises :class:`SwapError` on bad arguments or when the publish rename fails after
    ``.old`` was rolled back; :class:`PermissionError` when a rename still fails after the
    retry loop (an open handle inside one of the directories).
    """
    new, dest = Path(new), Path(dest)
    if not new.is_dir():
        raise SwapError(f"swap_dir: {new} is not a directory (nothing staged?)")
    if new == dest:
        raise SwapError(f"swap_dir: new and dest are the same path ({dest})")
    old = old_path_for(dest)
    _recover_and_sweep(dest, old, is_kind=Path.is_dir, remove=rmtree, observer=observer)
    # 3./4. the two renames (3 is skipped on a first publish).
    if dest.exists():
        _notify(observer, "aside", old)
        retry_permission(lambda: os.rename(dest, old))
    _notify(observer, "publish", dest)
    try:
        retry_permission(lambda: os.rename(new, dest))
        if not dest.is_dir():
            raise SwapError(f"swap_dir: {new} vanished during the publish rename (quarantine?)")
    except OSError as exc:
        # roll back so the table is not left without a live directory
        if not dest.exists() and old.is_dir():
            _notify(observer, "rollback", old)
            os.rename(old, dest)
        if isinstance(exc, FileNotFoundError):
            raise SwapError(
                f"swap_dir: {new} vanished before it could be published (quarantine?); the "
                f"previous {dest} was restored"
            ) from exc
        raise
    _finish(dest, old, remove=rmtree, observer=observer)


def swap_file(
    new: Path, dest: Path, *, blocked_hint: str, observer: Observer | None = None
) -> None:
    """Publish file ``new`` as ``dest``. Crash-safe, not atomic: there is a sub-millisecond
    window with no ``dest``. Raises :class:`SwapBlockedError` — with ``dest`` intact —
    when the aside rename fails (a reader without ``FILE_SHARE_DELETE`` holds the file);
    ``blocked_hint`` names the caller's remedy in that message."""
    new, dest = Path(new), Path(dest)
    if not new.is_file():
        raise SwapError(f"swap_file: {new} is not a file (nothing built?)")
    if new == dest:
        raise SwapError(f"swap_file: new and dest are the same path ({dest})")
    old = old_path_for(dest)
    _recover_and_sweep(dest, old, is_kind=Path.is_file, remove=unlink, observer=observer)
    if dest.exists():
        _notify(observer, "aside", old)
        try:
            os.rename(dest, old)
        except OSError as exc:
            raise SwapBlockedError(
                f"{dest}: cannot replace the live file while a reader holds it open — "
                f"{blocked_hint} (the old file is intact; DuckDB READ_ONLY readers share the "
                "file, a plain file handle does not)"
            ) from exc
    _notify(observer, "publish", dest)
    try:
        os.replace(new, dest)
        if not dest.is_file():
            raise SwapError(f"swap_file: {new} vanished during the publish (quarantine?)")
    except OSError as exc:
        if not dest.exists() and old.is_file():
            _notify(observer, "rollback", old)
            os.rename(old, dest)
        if isinstance(exc, FileNotFoundError):
            raise SwapError(
                f"swap_file: {new} vanished before it could be published (quarantine?); the "
                f"previous {dest} was restored"
            ) from exc
        raise
    _finish(dest, old, remove=unlink, observer=observer)


__all__ = [
    "NEW_SUFFIX",
    "OLD_SUFFIX",
    "RETRIES",
    "RETRY_BASE_SLEEP_S",
    "Observer",
    "SwapBlockedError",
    "SwapError",
    "new_path_for",
    "old_path_for",
    "replace",
    "retry_permission",
    "rmtree",
    "swap_dir",
    "swap_file",
    "unlink",
]
