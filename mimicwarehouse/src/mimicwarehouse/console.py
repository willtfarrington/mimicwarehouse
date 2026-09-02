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

EP-33 (B8, the paradigm canon — ``docs/gotchas.md`` § One way to do each thing) adds the
CLI conventions every command module shares instead of re-implementing:

* the exit codes :data:`EXIT_OK` 0 / :data:`EXIT_FINDINGS` 1 / :data:`EXIT_USAGE` 2 /
  :data:`EXIT_REFUSED` 3 (``safe``, ``catalog.cli`` and ``verify`` re-export them);
* :func:`fail` — ``mwh <cmd>: <message>`` in bold red on **stderr**, then ``typer.Exit``
  (stdout stays machine output);
* :func:`emit_json` — the one ``--json`` writer: plain ``json.dumps(indent=2)`` + ``\\n`` to
  stdout, raw integers (``fmt_int`` is for humans only), ``default=str`` for the odd
  Decimal / datetime;
* :func:`configure_progress_logging` — INFO progress lines of the ``mimicwarehouse``
  logger (steps, counts, bytes, wall, rss — never rows) to stdout or a file, so a
  background job's log captures the runner's and the loader's progress.

Import cost: rich.console only (already paid by ``cli.py``); the typer app is imported
inside :func:`run` so importing this module never imports the CLI (no cycle: cli, doctor,
config, guard, verify, inventory, schema.cli and fixtures.cli all import from here).
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any, NoReturn

from rich.console import Console
from rich.markup import escape

#: The one stdout console (rich markup, auto-detected width/encoding).
console = Console()
#: The one stderr console (warnings, ``_fail`` messages — never JSON payloads).
err_console = Console(stderr=True)

#: Process exit codes (EP-33 B8): success · findings/failures reported · usage or
#: environment error · safe-query governance refusal.
EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_USAGE = 2
EXIT_REFUSED = 3


def fail(prefix: str, message: str, *, code: int = EXIT_USAGE) -> NoReturn:
    """Print ``<prefix>: <message>`` (bold red, markup-escaped) to stderr and exit with
    ``code`` — the one error-message shape of every ``mwh`` command."""
    import typer

    err_console.print(f"[bold red]{escape(prefix)}:[/] {escape(message)}", highlight=False)
    raise typer.Exit(code=code)


def emit_json(payload: Any) -> None:
    """Write ``payload`` as indented JSON + ``\\n`` to stdout (raw ints; ``default=str``)."""
    sys.stdout.write(json.dumps(payload, indent=2, default=str) + "\n")
    sys.stdout.flush()


def configure_progress_logging(*, to_file: Path | None = None, quiet: bool = False) -> None:
    """Attach one INFO handler to the ``mimicwarehouse`` logger (idempotent per target):
    stdout by default, ``to_file`` (UTF-8, appended) when given; ``quiet`` raises the
    level to WARNING instead."""
    logger = logging.getLogger("mimicwarehouse")
    logger.setLevel(logging.WARNING if quiet else logging.INFO)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    if to_file is not None:
        target = str(Path(to_file))
        if not any(
            isinstance(h, logging.FileHandler) and h.baseFilename == target for h in logger.handlers
        ):
            handler = logging.FileHandler(target, encoding="utf-8")
            handler.setFormatter(formatter)
            logger.addHandler(handler)
        return
    if not any(
        type(h) is logging.StreamHandler and getattr(h, "stream", None) is sys.stdout
        for h in logger.handlers
    ):
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)


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


__all__ = [
    "EXIT_FINDINGS",
    "EXIT_OK",
    "EXIT_REFUSED",
    "EXIT_USAGE",
    "configure_progress_logging",
    "console",
    "console_safe",
    "emit_json",
    "err_console",
    "fail",
    "run",
]
