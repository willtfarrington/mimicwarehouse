"""Directory swap for staged Parquet tables (EP-17; DESIGN §5 note of 2026-08-28, retro
ARCH-2/FC-6).

``os.replace(new → dest)`` fails on Windows with ``PermissionError [WinError 5]`` whenever
``dest`` is a directory that exists, so the loader publishes a freshly staged
``<table>.new`` directory with the **rename-aside two-step** instead:

1. crash recovery — a ``<table>.old`` beside a *missing* ``dest`` is the previous live
   directory of an interrupted swap: rename it back before doing anything else;
2. a stale ``.old`` beside an existing ``dest`` is dead weight from a crash after step 4:
   remove it;
3. ``os.rename(dest → dest.old)`` (first stage of a table skips this — no ``dest`` yet);
4. ``os.rename(new → dest)``;
5. remove ``dest.old``.

Crash-safe, **not** atomic: between 3 and 4 there is no ``dest``, and renaming a directory
that holds an open file fails — readers must be closed first (the dev catalog's views point
at the same files a restage rewrites). Every rename/remove retries briefly on
``PermissionError`` (anti-virus / indexer holds on Windows are transient).

Nothing here reads file contents; the module only renames and deletes directories the
loader itself wrote.
"""

from __future__ import annotations

import os
import shutil
import time
from collections.abc import Callable
from pathlib import Path

#: Suffixes of the staging / aside directories beside a live table directory.
NEW_SUFFIX = ".new"
OLD_SUFFIX = ".old"

#: ``PermissionError`` retry policy (matches ``inventory._atomic_write_text``).
RETRIES = 20
RETRY_BASE_SLEEP_S = 0.05


class SwapError(RuntimeError):
    """A directory swap could not complete (bad arguments or a persistent lock)."""


def new_dir_for(dest: Path) -> Path:
    """``<dest>.new`` — where a stage writes before :func:`swap_dir` publishes it."""
    return dest.with_name(dest.name + NEW_SUFFIX)


def old_dir_for(dest: Path) -> Path:
    """``<dest>.old`` — the aside name the previous live directory briefly holds."""
    return dest.with_name(dest.name + OLD_SUFFIX)


def _retry(op: Callable[[], None], what: str) -> None:
    for attempt in range(RETRIES):
        try:
            op()
            return
        except PermissionError:
            if attempt == RETRIES - 1:
                raise
            time.sleep(RETRY_BASE_SLEEP_S * (attempt + 1))
        except FileNotFoundError:
            return  # already gone (rmtree racing a previous cleanup)


def swap_dir(new: Path, dest: Path) -> None:
    """Publish directory ``new`` as ``dest`` via the rename-aside two-step (module note).

    ``new`` must be an existing directory; ``dest`` may or may not exist (first stage).
    Raises :class:`SwapError` on bad arguments, :class:`PermissionError` when a rename
    still fails after the retry loop (an open handle inside one of the directories).
    """
    new, dest = Path(new), Path(dest)
    if not new.is_dir():
        raise SwapError(f"swap_dir: {new} is not a directory (nothing staged?)")
    if new == dest:
        raise SwapError(f"swap_dir: new and dest are the same path ({dest})")
    old = old_dir_for(dest)
    # 1. crash recovery: an interrupted swap left the live data under `.old`.
    if not dest.exists() and old.is_dir():
        _retry(lambda: os.rename(old, dest), "restore .old")
    # 2. a stale `.old` beside a live dest is a leftover: remove it.
    if old.exists():
        _retry(lambda: shutil.rmtree(old), "remove stale .old")
    # 3./4. the two renames (3 is skipped on a first stage).
    if dest.exists():
        _retry(lambda: os.rename(dest, old), "rename dest aside")
    try:
        _retry(lambda: os.rename(new, dest), "rename new into place")
    except OSError:
        # roll back so the table is not left without a live directory
        if not dest.exists() and old.is_dir():
            os.rename(old, dest)
        raise
    # 5. drop the aside copy.
    if old.exists():
        _retry(lambda: shutil.rmtree(old), "remove .old")


__all__ = ["NEW_SUFFIX", "OLD_SUFFIX", "SwapError", "new_dir_for", "old_dir_for", "swap_dir"]
