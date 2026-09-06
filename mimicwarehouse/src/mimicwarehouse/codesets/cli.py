"""``mwh codeset …`` — the code-set registry and GEM commands (EP-40 items 2 and 4;
attached in :mod:`mimicwarehouse.cli`).

``list`` / ``show`` read the packaged seeds (plus ``--defs DIR`` study directories) and
touch no data; ``lock`` records new ``(id, version)`` pairs in a directory's lock;
``validate`` expands one set against the tier catalog's dictionaries through
``safe_query`` (audited, registry-exempt dims — printable); ``compile`` runs the
``codesets.compile`` DAG step and the catalog step through the runner (the only lake
writer); ``expand --via-gem`` writes the GEM review file; ``gem fetch`` / ``gem status``
land and describe the CMS 2018 GEMs. Errors follow the EP-33 canon
(:func:`mimicwarehouse.console.fail`): a :class:`CodeSetFrozenError` refuses with
``EXIT_REFUSED`` (3), usage / environment errors exit 2, a failed compile exits 1.

Import budget: this module is on the ``mwh --help`` path — the registry / spec / gem
modules are pydantic + yaml + stdlib; duckdb, polars, the runner and ``safe`` load inside
the command bodies.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

import typer
from rich.markup import escape

from mimicwarehouse.codesets import gem as gem_mod
from mimicwarehouse.codesets import registry as registry_mod
from mimicwarehouse.codesets.spec import CodeSetError, CodeSetFrozenError
from mimicwarehouse.console import (
    EXIT_FINDINGS,
    EXIT_REFUSED,
    EXIT_USAGE,
    configure_progress_logging,
    console,
    console_safe,
    emit_json,
    fail,
)

if TYPE_CHECKING:  # pragma: no cover
    from mimicwarehouse.cli import CliState
    from mimicwarehouse.codesets.registry import Entry, Registry
    from mimicwarehouse.config import Settings

TIERS = ("fixture", "demo", "dev", "full")

codeset_app = typer.Typer(
    name="codeset",
    help=(
        "Code-set registry + ICD-9 <-> ICD-10 GEM utility (EP-40): list | show | validate | "
        "compile | lock | expand --via-gem | gem fetch | gem status. Code sets are versioned "
        "YAML (id@version, def_hash); dictionary-level output only."
    ),
    no_args_is_help=True,
    rich_markup_mode="rich",
)
gem_app = typer.Typer(
    name="gem",
    help="The CMS 2018 General Equivalence Mappings: fetch into ext/vocab/gem/2018, status.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
codeset_app.add_typer(gem_app, name="gem")

DefsOption = Annotated[
    list[Path] | None,
    typer.Option(
        "--defs",
        help="Extra code-set directory (repeatable; e.g. a study's codesets/ folder).",
        show_default=False,
    ),
]
TierOption = Annotated[
    str | None,
    typer.Option("--tier", help="Tier catalog: fixture | demo | dev | full (default: settings)."),
]
JsonOption = Annotated[bool, typer.Option("--json", help="Machine output (raw integers).")]


def _registry(prefix: str, defs: list[Path] | None) -> Registry:
    try:
        return registry_mod.load_registry(defs or [])
    except CodeSetFrozenError as exc:
        fail(prefix, f"refused: {exc}", code=EXIT_REFUSED)
    except CodeSetError as exc:
        fail(prefix, str(exc), code=EXIT_USAGE)


def _entry(prefix: str, registry: Registry, ref: str) -> Entry:
    try:
        return registry.get(ref)
    except CodeSetError as exc:
        fail(prefix, str(exc), code=EXIT_USAGE)


def _tier(prefix: str, tier: str | None, settings: Settings) -> str:
    resolved = tier or str(settings.default_tier)
    if resolved not in TIERS:
        fail(prefix, f"unknown tier {resolved!r}; expected one of {', '.join(TIERS)}")
    return resolved


def _entry_payload(entry: Entry) -> dict[str, Any]:
    cs = entry.codeset
    return {
        "ref": cs.ref,
        "id": cs.id,
        "version": cs.version,
        "kind": cs.kind,
        "name": cs.name,
        "def_hash": cs.def_hash,
        "n_declared": cs.n_declared,
        "systems": list(cs.declared_systems),
        "groups": list(cs.groups),
        "locked": entry.locked,
        "path": entry.display_path,
        "provenance": cs.provenance.model_dump(mode="json"),
    }


# ---------------------------------------------------------------------------
# list / show / lock
# ---------------------------------------------------------------------------


@codeset_app.command("list")
def list_command(defs: DefsOption = None, json_output: JsonOption = False) -> None:
    """The registered code sets: id@version, kind, members declared, locked, def_hash
    (no data access)."""
    registry = _registry("mwh codeset list", defs)
    if json_output:
        emit_json({"codesets": [_entry_payload(e) for e in registry]})
        return
    from rich.table import Table as RichTable

    from mimicwarehouse.inventory import fmt_int

    table = RichTable(title="mwh codeset list", pad_edge=False)
    for col, justify in (
        ("code set", "left"),
        ("kind", "left"),
        ("name", "left"),
        ("declared", "right"),
        ("systems", "left"),
        ("groups", "right"),
        ("locked", "left"),
        ("def_hash", "left"),
    ):
        table.add_column(col, justify=justify)  # type: ignore[arg-type]
    for entry in registry:
        cs = entry.codeset
        table.add_row(
            escape(cs.ref),
            cs.kind,
            escape(cs.name),
            fmt_int(cs.n_declared),
            ", ".join(cs.declared_systems),
            fmt_int(len(cs.groups)) if cs.groups else "-",
            "yes" if entry.locked else "no",
            cs.def_hash[:12],
        )
    console.print(table)
    unlocked = [e.ref for e in registry if not e.locked]
    verdict = (
        f"{fmt_int(len(unlocked))} unlocked ({', '.join(unlocked)}) - run `mwh codeset lock`"
        if unlocked
        else "all locked"
    )
    console.print(console_safe(f"{fmt_int(len(registry))} code set(s); {verdict}"), highlight=False)


@codeset_app.command("show")
def show_command(
    ref: Annotated[str, typer.Argument(help="Code set as <id>@<version>, e.g. t2dm@1.0.0.")],
    defs: DefsOption = None,
    json_output: JsonOption = False,
) -> None:
    """One code set in full: header, provenance, members per system (no data access)."""
    prefix = "mwh codeset show"
    registry = _registry(prefix, defs)
    entry = _entry(prefix, registry, ref)
    cs = entry.codeset
    if json_output:
        emit_json({**_entry_payload(entry), "members": cs.members.canonical(), "notes": cs.notes})
        return
    console.print(f"[bold]{escape(cs.ref)}[/]  {escape(cs.name)}", highlight=False)
    console.print(
        f"kind {cs.kind} - def_hash {cs.def_hash} - locked {'yes' if entry.locked else 'no'} - "
        f"{escape(entry.display_path)}",
        highlight=False,
    )
    if cs.description:
        console.print(escape(cs.description), highlight=False)
    prov = cs.provenance
    console.print(
        f"provenance: {prov.source}"
        + (f" - {escape(prov.ref)}" if prov.ref else "")
        + (f" - {escape(prov.url)}" if prov.url else "")
        + f" (accessed {prov.accessed})",
        highlight=False,
    )
    for reference in cs.references:
        console.print(f"reference: {escape(reference)}", highlight=False)
    m = cs.members
    for system, entries in (("icd9", m.icd9), ("icd10", m.icd10), ("hcpcs", m.hcpcs)):
        if entries:
            spelled = [
                e.code
                + ("*" if e.match == "prefix" else "")
                + (f" [{e.group}]" if e.group is not None else "")
                for e in entries
            ]
            console.print(
                f"{system} ({len(entries)}): {escape(', '.join(spelled))}", highlight=False
            )
    if m.itemids:
        console.print(
            f"itemids ({len(m.itemids)}): {', '.join(str(i) for i in m.itemids)}", highlight=False
        )
    if m.drugs is not None:
        console.print(
            f"drug names ({len(m.drugs.names)}, match {m.drugs.match}): "
            f"{escape(', '.join(m.drugs.names))}",
            highlight=False,
        )
        if m.drugs.rxnorm:
            console.print(f"rxnorm ({len(m.drugs.rxnorm)}): {', '.join(m.drugs.rxnorm)}")
    if m.atc:
        console.print(f"atc ({len(m.atc)}): {', '.join(m.atc)}", highlight=False)
    if m.loinc:
        console.print(f"loinc ({len(m.loinc)}): {', '.join(m.loinc)}", highlight=False)
    if cs.notes:
        console.print(f"notes: {escape(cs.notes)}", highlight=False)


@codeset_app.command("lock")
def lock_command(
    defs: Annotated[
        Path | None,
        typer.Option(
            "--defs",
            help="The code-set directory to lock (default: the packaged defs/).",
            show_default=False,
        ),
    ] = None,
    check: Annotated[
        bool,
        typer.Option("--check", help="Report unlocked sets and exit 1 instead of writing."),
    ] = False,
) -> None:
    """Record every not-yet-locked id@version of a directory in its codesets.lock.json
    (the (id, version) immutability rule; a frozen pair whose hash moved is refused)."""
    prefix = "mwh codeset lock"
    root = defs if defs is not None else registry_mod.packaged_defs_dir()
    try:
        if check:
            entries = registry_mod.load_dir(root)
            unlocked = [e.ref for e in entries if not e.locked]
            console.print(
                f"{len(entries)} code set(s) under {escape(str(root))}: "
                f"{len(entries) - len(unlocked)} locked, {len(unlocked)} unlocked"
                + (f" ({', '.join(unlocked)})" if unlocked else ""),
                highlight=False,
            )
            if unlocked:
                raise typer.Exit(code=EXIT_FINDINGS)
            return
        result = registry_mod.lock_dir(root)
    except CodeSetFrozenError as exc:
        fail(prefix, f"refused: {exc}", code=EXIT_REFUSED)
    except CodeSetError as exc:
        fail(prefix, str(exc), code=EXIT_USAGE)
    console.print(
        f"{escape(str(result.path))}: {len(result.added)} added"
        + (f" ({', '.join(result.added)})" if result.added else "")
        + f", {len(result.unchanged)} already locked",
        highlight=False,
    )


# ---------------------------------------------------------------------------
# validate / compile
# ---------------------------------------------------------------------------


@codeset_app.command("validate")
def validate_command(
    ctx: typer.Context,
    ref: Annotated[str, typer.Argument(help="Code set as <id>@<version>.")],
    tier: TierOption = None,
    defs: DefsOption = None,
    json_output: JsonOption = False,
) -> None:
    """Dictionary coverage of one code set on a tier: codes declared / matched / unmatched
    per system, unmatched codes listed (dictionary text only; every read is audited)."""
    prefix = "mwh codeset validate"
    state: CliState = ctx.obj
    settings = state.settings
    resolved_tier = _tier(prefix, tier, settings)
    registry = _registry(prefix, defs)
    _entry(prefix, registry, ref)
    from mimicwarehouse.catalog.cli import safe_cli_errors

    with safe_cli_errors(prefix):
        result = registry_mod.validate(
            ref, tier=resolved_tier, settings=settings, extra_dirs=defs or []
        )
    if json_output:
        emit_json(result.to_dict())
        return
    from rich.table import Table as RichTable

    from mimicwarehouse.inventory import fmt_int

    cs = result.entry.codeset
    table = RichTable(title=f"mwh codeset validate {cs.ref} ({resolved_tier})", pad_edge=False)
    for col, justify in (
        ("system", "left"),
        ("declared", "right"),
        ("matched", "right"),
        ("share", "right"),
        ("rows", "right"),
        ("unmatched", "left"),
    ):
        table.add_column(col, justify=justify)  # type: ignore[arg-type]
    for cov in result.coverages:
        shown = list(cov.unmatched[:40])
        if len(cov.unmatched) > 40:
            shown.append(f"(+{len(cov.unmatched) - 40} more)")
        table.add_row(
            cov.system,
            fmt_int(cov.declared),
            "n/a" if cov.matched is None else fmt_int(cov.matched),
            "" if cov.share is None else f"{cov.share:.1%}",
            fmt_int(cov.rows),
            escape(", ".join(shown)) if cov.matched is not None else "(no dictionary on this tier)",
        )
    console.print(table)
    console.print(
        console_safe(
            f"{cs.ref} ({cs.kind}, def_hash {cs.def_hash[:12]}): {fmt_int(cs.n_declared)} "
            f"declared, {fmt_int(len(result.rows))} compiled row(s), {fmt_int(result.n_matched)} "
            f"matched in the {resolved_tier} dictionaries"
        ),
        highlight=False,
    )


@codeset_app.command("compile")
def compile_command(
    ctx: typer.Context,
    refs: Annotated[
        list[str] | None,
        typer.Argument(
            help="Code sets to (re)compile as <id>@<version>; default: every registered set.",
            show_default=False,
        ),
    ] = None,
    tier: TierOption = None,
    defs: DefsOption = None,
) -> None:
    """Expand every registered set against the tier's dictionary tables and (re)build
    meta.codesets / meta.codeset_members, then the catalog — through the DAG runner
    (build lock, benchmark lines, a provenance run). A selection keeps the other sets'
    rows. A frozen (edited without a version bump) set is refused before anything runs."""
    prefix = "mwh codeset compile"
    state: CliState = ctx.obj
    settings = state.settings
    resolved_tier = _tier(prefix, tier, settings)
    registry = _registry(prefix, defs)
    selection = [r.strip() for r in (refs or []) if r.strip()]
    for ref in selection:
        _entry(prefix, registry, ref)

    from mimicwarehouse import config
    from mimicwarehouse.dag import runner as runner_mod
    from mimicwarehouse.dag.spec import DagError, load_dag

    configure_progress_logging()
    try:
        with registry_mod.compile_options(select=selection, extra_dirs=defs or []):
            result = runner_mod.run(
                load_dag(),
                resolved_tier,
                select=[registry_mod.STEP_COMPILE, "catalog"],
                settings=settings,
                provenance=True,
            )
    except (DagError, runner_mod.BuildLockError, config.ConfigError) as exc:
        fail(prefix, str(exc))

    from rich.table import Table as RichTable

    table = RichTable(title=f"mwh codeset compile {result.build_id}", pad_edge=False)
    for col, justify in (
        ("step", "left"),
        ("status", "left"),
        ("rows", "right"),
        ("wall s", "right"),
    ):
        table.add_column(col, justify=justify)  # type: ignore[arg-type]
    for s in result.steps:
        table.add_row(
            escape(s.name), s.status, "" if s.rows is None else f"{s.rows:,}", f"{s.wall_s:,.1f}"
        )
    console.print(table)
    if result.run_id is not None:
        console.print(f"run {result.run_id} (mwh runs show {result.run_id})", highlight=False)
    failed = [s for s in result.steps if s.status == "failed"]
    if failed:
        message = failed[0].error or "step failed"
        if message.startswith(CodeSetFrozenError.__name__):
            fail(prefix, f"refused: {message}", code=EXIT_REFUSED)
        fail(prefix, message, code=EXIT_FINDINGS)
    if not result.ok:
        raise typer.Exit(code=EXIT_FINDINGS)
    console.print(
        f"compiled {len(selection) or len(registry)} code set(s) on tier {resolved_tier}; "
        f'query: mwh sql "SELECT codeset_id, system, count(*) AS n FROM meta.codeset_members '
        f'GROUP BY 1, 2 ORDER BY 1, 2" --tier {resolved_tier}',
        highlight=False,
    )


# ---------------------------------------------------------------------------
# expand --via-gem
# ---------------------------------------------------------------------------


@codeset_app.command("expand")
def expand_command(
    ctx: typer.Context,
    ref: Annotated[str, typer.Argument(help="Dual ICD code set as <id>@<version>.")],
    via_gem: Annotated[
        bool, typer.Option("--via-gem", help="Propose counterpart codes through the CMS GEMs.")
    ] = False,
    tier: TierOption = None,
    defs: DefsOption = None,
    out: Annotated[
        Path | None,
        typer.Option(
            "--out",
            help="Where to write the review (default: studies/codesets/reviews/<ref>.gem-review.md "
            "under the data root).",
            show_default=False,
        ),
    ] = None,
) -> None:
    """Write <id>@<version>.gem-review.md: the ICD-9 -> ICD-10 and ICD-10 -> ICD-9
    counterparts the GEM proposes for the set's members (with flags and dictionary titles)
    that the set does not cover yet — for a human to fold into a new version. Never
    applied automatically."""
    prefix = "mwh codeset expand"
    if not via_gem:
        fail(prefix, "only --via-gem is implemented (EP-40 item 4)")
    state: CliState = ctx.obj
    settings = state.settings
    resolved_tier = _tier(prefix, tier, settings)
    registry = _registry(prefix, defs)
    entry = _entry(prefix, registry, ref)
    kind = gem_mod.GEM_KIND_OF_CODESET.get(entry.codeset.kind)
    if kind is None:
        fail(prefix, f"{entry.ref} is a {entry.codeset.kind} set; only icd_dx / icd_px sets expand")
    try:
        table = gem_mod.load_gem(kind, settings=settings)
    except gem_mod.GemError as exc:
        fail(prefix, str(exc))
    from mimicwarehouse.catalog.cli import safe_cli_errors

    with safe_cli_errors(prefix):
        dictionaries = registry_mod.dictionaries_via_safe_query(
            registry_mod.dictionary_keys(entry.codeset), tier=resolved_tier, settings=settings
        )
    rows = registry_mod.expand(entry.codeset, dictionaries)
    try:
        review = gem_mod.build_review(
            entry, table=table, rows=rows, dictionaries=dictionaries, tier=resolved_tier
        )
    except gem_mod.GemError as exc:
        fail(prefix, str(exc))
    path = gem_mod.write_review(
        out if out is not None else gem_mod.review_path(settings, entry.ref),
        gem_mod.render_review(review),
    )
    console.print(
        f"{entry.ref}: {len(review.forward.proposals)} ICD-10 and "
        f"{len(review.backward.proposals)} ICD-9 proposal(s) "
        f"({review.forward.covered + review.backward.covered} mapping(s) already covered, "
        f"{len(review.forward.no_map) + len(review.backward.no_map)} no-map source(s))",
        highlight=False,
    )
    console.print(f"review written: {escape(str(path))}", highlight=False)


# ---------------------------------------------------------------------------
# gem fetch / gem status
# ---------------------------------------------------------------------------


@gem_app.command("fetch")
def gem_fetch_command(
    ctx: typer.Context,
    force: Annotated[
        bool, typer.Option("--force", help="Re-download and re-verify even verified files.")
    ] = False,
) -> None:
    """Download the public CMS 2018 GEM zips into ext/vocab/gem/2018, extract the four
    text files, verify their sha256 and write source.yaml (metadata only)."""
    state: CliState = ctx.obj
    settings = state.settings
    from mimicwarehouse.config import DiskGuardError

    try:
        register, counts = gem_mod.fetch(settings, force=force)
    except (gem_mod.GemError, DiskGuardError) as exc:
        fail("mwh codeset gem fetch", str(exc), code=EXIT_USAGE)
    console.print(
        f"cms-gem {register.version}: {len(register.files)} file(s) verified "
        f"({counts.downloaded} downloaded, {counts.skipped} already verified, "
        f"{counts.bytes / 2**20:,.1f} MB) from {len(register.archives)} archive(s)",
        highlight=False,
    )
    console.print(
        f"register written: {escape(str(gem_mod.register_path(gem_mod.gem_root(settings))))}",
        highlight=False,
    )


@gem_app.command("status")
def gem_status_command(ctx: typer.Context) -> None:
    """Print the GEM landing register (ext/vocab/gem/2018/source.yaml) — metadata only."""
    state: CliState = ctx.obj
    settings = state.settings
    root = gem_mod.gem_root(settings)
    path = gem_mod.register_path(root)
    if not path.is_file():
        fail(
            "mwh codeset gem status",
            f"no register at {path} - run `mwh codeset gem fetch` first (or place the files "
            f"by hand: {', '.join(f.name for a in gem_mod.GEM_ARCHIVES for f in a.files)})",
        )
    try:
        register = gem_mod.load_register(path)
    except gem_mod.GemError as exc:
        fail("mwh codeset gem status", str(exc), code=EXIT_USAGE)
    from rich.table import Table as RichTable

    table = RichTable(title=f"gem register - {path}", pad_edge=False)
    for col, justify in (
        ("file", "left"),
        ("kind", "left"),
        ("direction", "left"),
        ("bytes", "right"),
        ("sha256", "left"),
        ("verified now", "left"),
    ):
        table.add_column(col, justify=justify)  # type: ignore[arg-type]
    verified = gem_mod.verify_files(root, (f for a in gem_mod.GEM_ARCHIVES for f in a.files))
    for record in register.files:
        table.add_row(
            escape(record.name),
            record.kind,
            record.direction,
            f"{record.bytes:,}",
            record.sha256[:12],
            "yes" if verified.get(record.name) else "no",
        )
    console.print(table)
    console.print(
        f"{register.name} {register.version} - {register.license} - obtained "
        f"{register.obtained_on} by {register.obtained_by} - {escape(register.url)}",
        highlight=False,
    )


__all__ = ["TIERS", "codeset_app", "gem_app"]
