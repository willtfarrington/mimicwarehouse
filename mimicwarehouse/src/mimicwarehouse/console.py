"""Shared rich consoles + the UTF-8 ``mwh`` entry point (EP-167, retro CFG-6; DESIGN §2).

One place for the CLI's console handling instead of five ``Console()`` instances and three
encoding strategies (roadmap Risk 13):

* :data:`console` / :data:`err_console` — the single stdout / stderr rich consoles every
  command module imports (``from mimicwarehouse.console import console``).
* :func:`console_safe` — moved from ``verify._console_safe`` (an alias remains there):
  replaces glyphs the current console cannot encode (⏱, ☑ on cp1252) instead of crashing.
* :func:`run` — the ``mwh`` entry point (``pyproject.toml`` ``[project.scripts]``): forces
  UTF-8 stdio with ``errors="replace"`` before handing over to the typer app, so hooks,
  redirects and owner shells that still run cp1252 get valid UTF-8 instead of a
  ``UnicodeEncodeError``. ``PYTHONUTF8=1`` from ``.claude/settings.json`` (EP-165) is the
  session belt; this is the braces for every other host. JSON outputs keep plain ``\\n``
  line ends.

Import cost: rich.console only (already paid by ``cli.py``); the typer app is imported
inside :func:`run` so importing this module never imports the CLI (no cycle: cli, doctor,
config, guard, verify, inventory, schema.cli and fixtures.cli all import from here).
"""

from __future__ import annotations

import sys

from rich.console import Console

#: The one stdout console (rich markup, auto-detected width/encoding).
console = Console()
#: The one stderr console (warnings, ``_fail`` messages — never JSON payloads).
err_console = Console(stderr=True)


def console_safe(text: str) -> str:
    """Replace glyphs the current console cannot encode (⏱, ☑ on cp1252) instead of
    crashing; a no-op on UTF-8 streams (moved from ``verify._console_safe``, EP-167)."""
    enc = getattr(sys.stdout, "encoding", None) or "utf-8"
    return text.encode(enc, errors="replace").decode(enc, errors="replace")


def run() -> None:
    """``mwh`` entry point: reconfigure stdio to UTF-8, then run the typer app."""
    for s in (sys.stdout, sys.stderr):
        reconfigure = getattr(s, "reconfigure", None)  # replaced streams may lack it
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")
    from mimicwarehouse.cli import app

    app()


__all__ = ["console", "console_safe", "err_console", "run"]
