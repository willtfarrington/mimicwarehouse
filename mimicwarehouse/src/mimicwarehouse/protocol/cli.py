"""``mwh protocol …`` — freeze / verify / amend / list / show / run (EP-51; attached in
:mod:`mimicwarehouse.cli`).

``freeze <yaml>`` validates, resolves and hashes a protocol, copies it byte for byte to
``runs/protocols/<hash>.yaml`` (read-only), appends the ``runs/protocols.jsonl`` line and
prints the hash; ``verify <yaml | hash>`` recomputes and compares (exit 1 on drift, an
unfrozen file, an unknown hash or a missing copy); ``amend <yaml> --previous <hash>
--reason …`` freezes an amendment linked to its predecessor; ``list`` renders the
registry (a rich table over the ledger — the session-side listing; ``--json`` raw lines);
``show <hash>`` prints the line, the amendment lineage and the frozen YAML; ``run <hash>
[--tier t] [--runner cohort_only] [--yaml PATH]`` runs a frozen protocol through
:func:`~mimicwarehouse.protocol.runners.run_protocol` and prints the run id and the
summary path. Errors follow the EP-33 canon (:func:`mimicwarehouse.console.fail`): the
D-25 refusals — an unknown hash, a modified frozen copy, an unfrozen / differing
``--yaml``, an ``id@version`` frozen with another hash — exit ``EXIT_REFUSED`` (3);
schema / reference / usage errors exit 2; a drift found by ``verify`` or a failed run
exits 1.

Import budget: this module is on the ``mwh --help`` path — the spec and registry modules
are pydantic + yaml + stdlib (+ ``timesem`` and the EP-46 registry, already paid by
``cohort.cli``); the runners, the run ledger, duckdb and polars load inside the command
bodies.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

import typer
from rich.markup import escape

from mimicwarehouse.console import (
    EXIT_FINDINGS,
    EXIT_REFUSED,
    EXIT_USAGE,
    configure_progress_logging,
    console,
    emit_json,
    fail,
)
from mimicwarehouse.protocol import registry as registry_mod
from mimicwarehouse.protocol.registry import (
    ProtocolFrozenError,
    ProtocolRefusedError,
    UnknownProtocolError,
)
from mimicwarehouse.protocol.spec import ProtocolError

if TYPE_CHECKING:  # pragma: no cover
    from mimicwarehouse.cli import CliState
    from mimicwarehouse.config import Settings

TIERS = ("fixture", "demo", "dev", "full")

protocol_app = typer.Typer(
    name="protocol",
    help=(
        "Protocol freeze registry (EP-51, D-25): freeze | verify | amend | list | show | run. "
        "A YAML protocol is content-hashed and registered before it runs (runs/protocols.jsonl "
        "+ a read-only copy under runs/protocols/); runs cite the frozen hash, amendments "
        "append a new hash linked to the previous one, and an unfrozen or modified protocol "
        "cannot run. Every protocol-driven report states that MIMIC-IV analyses are retrospective."
    ),
    no_args_is_help=True,
    rich_markup_mode="rich",
)

JsonOption = Annotated[bool, typer.Option("--json", help="Machine output (raw integers).")]

_REFUSED = (UnknownProtocolError, ProtocolFrozenError, ProtocolRefusedError)


def _raw(text: str) -> None:
    """Machine text (YAML / JSON) to stdout verbatim — never through the rich console."""
    sys.stdout.write(text)
    sys.stdout.flush()


def _tier(prefix: str, tier: str | None, settings: Settings) -> str:
    resolved = tier or str(settings.default_tier)
    if resolved not in TIERS:
        fail(prefix, f"unknown tier {resolved!r}; expected one of {', '.join(TIERS)}")
    return resolved


def _freeze_payload(result: registry_mod.FreezeResult) -> dict[str, Any]:
    return {
        "hash": result.hash,
        "created": result.created,
        "protocol": result.protocol.ref,
        "claim_type": result.protocol.claim_type,
        "cohort": result.protocol.cohort,
        "cohort_hash": result.resolved.cohort_hash,
        "frozen_path": result.frozen_path.as_posix(),
        "amends": result.line.amends,
        "line": result.line.model_dump(mode="json"),
    }


def _print_freeze(result: registry_mod.FreezeResult, verb: str) -> None:
    state = f"{verb}: frozen" if result.created else f"{verb}: already frozen"
    console.print(
        f"{state} [bold]{escape(result.hash)}[/] ({escape(result.protocol.ref)}, "
        f"{result.protocol.claim_type}; cohort {escape(result.protocol.cohort)} "
        f"{result.resolved.cohort_hash[:12]}) -> {escape(str(result.frozen_path))}",
        highlight=False,
    )
    if result.line.amends:
        console.print(f"amends {escape(result.line.amends)}", highlight=False)


# ---------------------------------------------------------------------------
# freeze / amend / verify
# ---------------------------------------------------------------------------


@protocol_app.command("freeze")
def freeze_command(
    ctx: typer.Context,
    yaml_path: Annotated[Path, typer.Argument(help="The protocol YAML to freeze.")],
    json_output: JsonOption = False,
) -> None:
    """Validate, resolve and content-hash a protocol, copy it byte for byte to
    runs/protocols/<hash>.yaml (read-only), append the registry line and print the hash.
    Freezing content already frozen is a no-op; an id@version frozen with another hash is
    refused (exit 3) - bump the version and `amend`."""
    prefix = "mwh protocol freeze"
    state: CliState = ctx.obj
    if not yaml_path.is_file():
        fail(prefix, f"{yaml_path} is not a file")
    try:
        result = registry_mod.freeze(yaml_path, state.settings)
    except _REFUSED as exc:
        fail(prefix, str(exc), code=EXIT_REFUSED)
    except ProtocolError as exc:
        fail(prefix, str(exc), code=EXIT_USAGE)
    if json_output:
        emit_json(_freeze_payload(result))
        return
    _print_freeze(result, "freeze")


@protocol_app.command("amend")
def amend_command(
    ctx: typer.Context,
    yaml_path: Annotated[
        Path, typer.Argument(help="The amended protocol YAML (declares `amends`).")
    ],
    previous: Annotated[
        str, typer.Option("--previous", help="The frozen hash this YAML amends (= its `amends`).")
    ],
    reason: Annotated[
        str | None,
        typer.Option(
            "--reason",
            help="Why (recorded in the registry line; default: the YAML's amendment_reason).",
        ),
    ] = None,
    json_output: JsonOption = False,
) -> None:
    """Freeze an amendment: the YAML's `amends` must equal --previous (a frozen hash of the
    same protocol id), the version must be bumped, and a reason is recorded; the new hash
    is linked to the previous one."""
    prefix = "mwh protocol amend"
    state: CliState = ctx.obj
    if not yaml_path.is_file():
        fail(prefix, f"{yaml_path} is not a file")
    try:
        result = registry_mod.amend(
            yaml_path, previous=previous, reason=reason, settings=state.settings
        )
    except _REFUSED as exc:
        fail(prefix, str(exc), code=EXIT_REFUSED)
    except ProtocolError as exc:
        fail(prefix, str(exc), code=EXIT_USAGE)
    if json_output:
        emit_json(_freeze_payload(result))
        return
    _print_freeze(result, "amend")


@protocol_app.command("verify")
def verify_command(
    ctx: typer.Context,
    target: Annotated[str, typer.Argument(help="A protocol YAML path or a frozen hash.")],
    json_output: JsonOption = False,
) -> None:
    """Recompute and compare: exit 0 when the YAML (or the frozen copy of the hash) still
    hashes to its registered value; exit 1 on drift, an unfrozen file, an unknown hash or
    a missing copy."""
    prefix = "mwh protocol verify"
    state: CliState = ctx.obj
    try:
        result = registry_mod.verify(target, state.settings)
    except ProtocolError as exc:
        fail(prefix, str(exc), code=EXIT_USAGE)
    if json_output:
        emit_json(result.to_dict())
    else:
        style = "green" if result.ok else "red"
        console.print(f"[{style}]{result.status}[/]: {escape(result.message)}", highlight=False)
    if not result.ok:
        raise typer.Exit(code=EXIT_FINDINGS)


# ---------------------------------------------------------------------------
# list / show
# ---------------------------------------------------------------------------

#: Columns of the ``mwh protocol list`` table.
LIST_COLUMNS: tuple[str, ...] = (
    "hash",
    "protocol",
    "claim_type",
    "cohort",
    "amends",
    "timestamp_utc",
    "git_sha",
)


@protocol_app.command("list")
def list_command(
    ctx: typer.Context,
    protocol_id: Annotated[
        str | None, typer.Option("--id", help="Keep only this protocol id.", show_default=False)
    ] = None,
    last: Annotated[
        int | None, typer.Option("--last", help="Show at most the N most recent entries.", min=1)
    ] = None,
    json_output: JsonOption = False,
) -> None:
    """List the frozen protocols from runs/protocols.jsonl, newest first (hashes, ids,
    claim types, cohorts, amendment links - never data)."""
    prefix = "mwh protocol list"
    state: CliState = ctx.obj
    settings = state.settings
    try:
        lines = registry_mod.read_registry(settings)
    except ProtocolError as exc:
        fail(prefix, str(exc), code=EXIT_USAGE)
    if protocol_id is not None:
        lines = [line for line in lines if line.protocol_id == protocol_id]
    lines = list(reversed(lines))
    if last is not None:
        lines = lines[:last]
    if json_output:
        emit_json([line.model_dump(mode="json") for line in lines])
        return
    if not lines:
        console.print(
            f"no frozen protocols in {escape(str(registry_mod.ledger_path(settings)))}"
            + (f" for id {escape(protocol_id)}" if protocol_id else ""),
            highlight=False,
        )
        return
    from rich.table import Table as RichTable

    table = RichTable(title=f"frozen protocols ({len(lines)})", pad_edge=False)
    for col in LIST_COLUMNS:
        table.add_column(col)
    for line in lines:
        table.add_row(
            escape(line.hash[:12]),
            escape(line.ref),
            line.claim_type,
            escape(line.cohort),
            escape(line.amends[:12]) if line.amends else "-",
            escape(line.timestamp_utc),
            escape((line.git_sha or "-")[:12]),
        )
    console.print(table)


@protocol_app.command("show")
def show_command(
    ctx: typer.Context,
    digest: Annotated[
        str, typer.Argument(help="A frozen hash (64 hex; a unique prefix is not accepted).")
    ],
    as_yaml: Annotated[
        bool,
        typer.Option("--yaml", help="Print the frozen YAML copy verbatim instead of the card."),
    ] = False,
    json_output: JsonOption = False,
) -> None:
    """Print a frozen protocol's registry line, its amendment lineage and its frozen YAML."""
    prefix = "mwh protocol show"
    state: CliState = ctx.obj
    settings = state.settings
    try:
        line = registry_mod.get(digest, settings)
    except UnknownProtocolError as exc:
        fail(prefix, str(exc), code=EXIT_REFUSED)
    except ProtocolError as exc:
        fail(prefix, str(exc), code=EXIT_USAGE)
    copy = registry_mod.frozen_path(line.hash, settings)
    text = copy.read_bytes().decode("utf-8") if copy.is_file() else None
    chain = registry_mod.lineage(line.hash, settings)
    if json_output:
        emit_json(
            {
                "line": line.model_dump(mode="json"),
                "lineage": [c.hash for c in chain],
                "frozen_path": copy.as_posix(),
                "frozen_copy_present": text is not None,
                "read_only": registry_mod.is_read_only(copy),
                "yaml": text,
            }
        )
        return
    if as_yaml:
        if text is None:
            fail(prefix, f"frozen copy {copy} is missing", code=EXIT_FINDINGS)
        _raw(text if text.endswith("\n") else text + "\n")
        return
    console.print(f"[bold]{escape(line.ref)}[/] frozen at {escape(line.hash)}", highlight=False)
    console.print(
        f"claim type {line.claim_type} - cohort {escape(line.cohort)} ({line.cohort_hash[:12]}) - "
        f"unit {line.unit_of_analysis} - {escape(line.timestamp_utc)} - "
        f"git {escape((line.git_sha or '-')[:12])} - actor {escape(line.actor)}",
        highlight=False,
    )
    console.print(
        f"frozen copy {escape(str(copy))} ({'present' if text is not None else 'MISSING'}"
        f"{', read-only' if registry_mod.is_read_only(copy) else ''}) - source sha256 "
        f"{line.source_sha256[:12]}",
        highlight=False,
    )
    for key, ref_hash in sorted(line.ref_hashes.items()):
        console.print(f"reference: {escape(key)} -> {ref_hash[:12]}", highlight=False)
    if line.amends:
        console.print(
            f"amends {escape(line.amends)}"
            + (f" - {escape(line.amendment_reason)}" if line.amendment_reason else ""),
            highlight=False,
        )
    if len(chain) > 1:
        console.print(
            "lineage: " + " -> ".join(f"{c.ref} {c.hash[:12]}" for c in chain), highlight=False
        )
    if text is not None:
        console.print("")
        _raw(text if text.endswith("\n") else text + "\n")


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


@protocol_app.command("run")
def run_command(
    ctx: typer.Context,
    digest: Annotated[str, typer.Argument(help="The frozen protocol hash to run.")],
    tier: Annotated[
        str | None,
        typer.Option(
            "--tier", help="fixture | demo | dev | full (default: settings.default_tier)."
        ),
    ] = None,
    runner: Annotated[
        str, typer.Option("--runner", help="The registered runner (v1: cohort_only).")
    ] = "cohort_only",
    yaml_override: Annotated[
        Path | None,
        typer.Option(
            "--yaml", help="A YAML that must hash to the frozen value (refused otherwise)."
        ),
    ] = None,
    json_output: JsonOption = False,
) -> None:
    """Run a frozen protocol: refuses an unknown hash, a modified frozen copy or a
    differing --yaml (exit 3); otherwise opens a kind: protocol run carrying the hash
    and claim type, dispatches to the runner and writes runs/<run_id>/protocol_summary.md."""
    prefix = "mwh protocol run"
    state: CliState = ctx.obj
    settings = state.settings
    resolved_tier = _tier(prefix, tier, settings)
    from mimicwarehouse.protocol import runners as runners_mod

    if runner not in runners_mod.RUNNERS:
        fail(
            prefix,
            f"unknown runner {runner!r}; expected one of {', '.join(runners_mod.runner_names())}",
        )
    if yaml_override is not None and not yaml_override.is_file():
        fail(prefix, f"--yaml {yaml_override} is not a file")
    if not json_output:
        configure_progress_logging()
    try:
        outcome = runners_mod.run_protocol(
            digest,
            tier=resolved_tier,
            runner=runner,
            yaml_override=yaml_override,
            settings=settings,
            command=" ".join(["mwh", *sys.argv[1:]]) if sys.argv else None,
        )
    except _REFUSED as exc:
        fail(prefix, str(exc), code=EXIT_REFUSED)
    except runners_mod.ProtocolRunError as exc:
        fail(prefix, str(exc), code=EXIT_FINDINGS)
    except ProtocolError as exc:
        fail(prefix, str(exc), code=EXIT_USAGE)
    if json_output:
        emit_json(outcome.to_dict())
        return
    console.print(
        f"run [bold]{escape(outcome.run_id)}[/] ok - protocol {escape(outcome.protocol_ref)} "
        f"{escape(outcome.protocol_hash[:12])} ({outcome.claim_type}) on {outcome.tier} via "
        f"{outcome.runner} - summary {escape(str(outcome.summary_path))}",
        highlight=False,
    )


__all__ = [
    "LIST_COLUMNS",
    "amend_command",
    "freeze_command",
    "list_command",
    "protocol_app",
    "run_command",
    "show_command",
    "verify_command",
]
