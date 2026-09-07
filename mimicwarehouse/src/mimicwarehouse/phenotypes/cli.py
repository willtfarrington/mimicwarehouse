"""``mwh phenotype …`` — the phenotype engine commands (EP-41 items 2 and 4; attached in
:mod:`mimicwarehouse.cli`).

``list`` / ``show`` / ``validate`` / ``lock`` read the packaged definitions (plus ``--defs
DIR`` study directories, resolved against the code-set registry plus ``--codesets DIR``)
and touch no data; ``compile`` runs the ``phenotypes.compile`` DAG step and the catalog
step through the runner (the only lake writer; ``--dry-run`` prints the compiled SQL
instead; ``--background --job NAME`` detaches it through the EP-19 launcher, the ⏱
standard for the full tier — EP-42); ``summary`` prints the prevalence of one or more
built phenotypes through ``safe_query`` (k-suppressed, audited) — overall, by era, the
distribution of every evidence column with declared levels and the pairwise per-admission
agreement — and with ``--report`` / ``--out DIR`` records the reads in a ``kind:
analysis`` run and writes ``phenotype_prevalence.md`` (EP-42 item 4). Errors follow the
EP-33 canon (:func:`mimicwarehouse.console.fail`): a frozen ``(id, version)`` — phenotype
or code set — refuses with ``EXIT_REFUSED`` (3), usage / environment errors exit 2, a
failed compile or a validation with problems exits 1.

Import budget: this module is on the ``mwh --help`` path — the spec / registry modules
are pydantic + yaml + stdlib; the compiler, duckdb, polars, the runner and ``safe`` load
inside the command bodies.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

import typer
from rich.markup import escape

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
from mimicwarehouse.phenotypes import registry as registry_mod
from mimicwarehouse.phenotypes.spec import PhenotypeError, PhenotypeFrozenError

if TYPE_CHECKING:  # pragma: no cover
    from mimicwarehouse.cli import CliState
    from mimicwarehouse.config import Settings
    from mimicwarehouse.phenotypes.registry import Entry, Registry

TIERS = ("fixture", "demo", "dev", "full")

phenotype_app = typer.Typer(
    name="phenotype",
    help=(
        "Phenotype engine (EP-41/42): list | show | validate | lock | compile | summary. "
        "Phenotypes are versioned YAML (id@version, def_hash pinned to the code sets and "
        "concepts they reference) compiled to deterministic SQL and materialised per tier; "
        "sessions read mimiciv_derived.phenotype_<id> as aggregates."
    ),
    no_args_is_help=True,
    rich_markup_mode="rich",
)

DefsOption = Annotated[
    list[Path] | None,
    typer.Option(
        "--defs",
        help="Extra phenotype directory (repeatable; e.g. a study's phenotypes/ folder).",
        show_default=False,
    ),
]
CodesetsOption = Annotated[
    list[Path] | None,
    typer.Option(
        "--codesets",
        help="Extra code-set directory the references resolve against (repeatable).",
        show_default=False,
    ),
]
TierOption = Annotated[
    str | None,
    typer.Option("--tier", help="Tier: fixture | demo | dev | full (default: settings)."),
]
JsonOption = Annotated[bool, typer.Option("--json", help="Machine output (raw integers).")]


def _registry(prefix: str, defs: list[Path] | None, codesets: list[Path] | None) -> Registry:
    try:
        return registry_mod.load_registry(defs or [], codeset_dirs=codesets or [])
    except (PhenotypeFrozenError, CodeSetFrozenError) as exc:
        fail(prefix, f"refused: {exc}", code=EXIT_REFUSED)
    except (PhenotypeError, CodeSetError) as exc:
        fail(prefix, str(exc), code=EXIT_USAGE)


def _entry(prefix: str, registry: Registry, ref: str) -> Entry:
    try:
        return registry.get(ref)
    except PhenotypeError as exc:
        fail(prefix, str(exc), code=EXIT_USAGE)


def _tier(prefix: str, tier: str | None, settings: Settings) -> str:
    resolved = tier or str(settings.default_tier)
    if resolved not in TIERS:
        fail(prefix, f"unknown tier {resolved!r}; expected one of {', '.join(TIERS)}")
    return resolved


def _entry_payload(entry: Entry) -> dict[str, Any]:
    p = entry.phenotype
    return {
        "ref": p.ref,
        "id": p.id,
        "version": p.version,
        "name": p.name,
        "grain": p.grain,
        "def_hash": entry.def_hash,
        "criteria": p.criteria.render(),
        "onset": p.onset.canonical(),
        "parameters": dict(p.parameters),
        "references": dict(entry.resolved),
        "concepts": {t: pin.to_dict() for t, pin in sorted(entry.concepts.items())},
        "evidence": [e.name for e in p.evidence_columns],
        "leaves": [
            {"id": leaf.id, "kind": leaf.kind, "negated": neg} for leaf, neg in p.all_leaves()
        ],
        "locked": entry.locked,
        "path": entry.display_path,
        "provenance": p.provenance.model_dump(mode="json"),
    }


# ---------------------------------------------------------------------------
# list / show / validate / lock
# ---------------------------------------------------------------------------


@phenotype_app.command("list")
def list_command(
    defs: DefsOption = None, codesets: CodesetsOption = None, json_output: JsonOption = False
) -> None:
    """The registered phenotypes: id@version, grain, criteria, references, locked,
    def_hash (no data access)."""
    registry = _registry("mwh phenotype list", defs, codesets)
    if json_output:
        emit_json({"phenotypes": [_entry_payload(e) for e in registry]})
        return
    from rich.table import Table as RichTable

    from mimicwarehouse.inventory import fmt_int

    table = RichTable(title="mwh phenotype list", pad_edge=False)
    for col, justify in (
        ("phenotype", "left"),
        ("grain", "left"),
        ("name", "left"),
        ("criteria", "left"),
        ("references", "right"),
        ("locked", "left"),
        ("def_hash", "left"),
    ):
        table.add_column(col, justify=justify)  # type: ignore[arg-type]
    for entry in registry:
        p = entry.phenotype
        table.add_row(
            escape(p.ref),
            p.grain,
            escape(p.name),
            escape(p.criteria.render()),
            fmt_int(len(entry.resolved)),
            "yes" if entry.locked else "no",
            entry.def_hash[:12],
        )
    console.print(table)
    unlocked = [e.ref for e in registry if not e.locked]
    verdict = (
        f"{fmt_int(len(unlocked))} unlocked ({', '.join(unlocked)}) - run `mwh phenotype lock`"
        if unlocked
        else "all locked"
    )
    console.print(
        console_safe(f"{fmt_int(len(registry))} phenotype(s); {verdict}"), highlight=False
    )


@phenotype_app.command("show")
def show_command(
    ref: Annotated[str, typer.Argument(help="Phenotype as <id>@<version>, e.g. t2dm@1.0.0.")],
    defs: DefsOption = None,
    codesets: CodesetsOption = None,
    json_output: JsonOption = False,
    sql: Annotated[bool, typer.Option("--sql", help="Also print the compiled SQL.")] = False,
) -> None:
    """One phenotype in full: the definition card, every leaf, the resolved references
    and what it does not claim (no data access)."""
    prefix = "mwh phenotype show"
    registry = _registry(prefix, defs, codesets)
    entry = _entry(prefix, registry, ref)
    p = entry.phenotype
    from mimicwarehouse.phenotypes.runner import leaf_text

    if json_output:
        payload = {
            **_entry_payload(entry),
            "description": p.description,
            "notes": p.notes,
            "what_it_does_not_claim": list(p.what_it_does_not_claim),
            "citations": list(p.citations),
        }
        if sql:
            from mimicwarehouse.phenotypes.compiler import compile_phenotype

            payload["sql"] = compile_phenotype(
                p, registry.codesets, resolved=entry.resolved, concepts=entry.concept_hashes
            ).sql
        emit_json(payload)
        return
    console.print(f"[bold]{escape(p.ref)}[/]  {escape(p.name)}", highlight=False)
    console.print(
        f"grain {p.grain} - def_hash {entry.def_hash} - locked {'yes' if entry.locked else 'no'} - "
        f"{escape(entry.display_path)}",
        highlight=False,
    )
    if p.description:
        console.print(escape(p.description.strip()), highlight=False)
    if p.parameters:
        listed = ", ".join(f"{k}={v!r}" for k, v in sorted(p.parameters.items()))
        console.print(f"parameters: {escape(listed)}", highlight=False)
    console.print(f"criteria: {escape(p.criteria.render())}", highlight=False)
    console.print(f"onset: {escape(str(p.onset.canonical()))}", highlight=False)
    for leaf, neg in p.all_leaves():
        console.print(
            f"  {escape(leaf.id)}: {escape(leaf_text(leaf))}{' [negated]' if neg else ''}",
            highlight=False,
        )
    for cs_ref, def_hash in sorted(entry.resolved.items()):
        console.print(f"reference: {escape(cs_ref)} -> {def_hash[:12]}", highlight=False)
    for table, pin in sorted(entry.concepts.items()):
        patch = f" (patch {pin.patch_id})" if pin.patch_id else " (unpatched)"
        console.print(
            f"concept: {escape(table)} -> {pin.executed_sha256[:12]}{escape(patch)} via "
            f"{escape(pin.step)}",
            highlight=False,
        )
    for e in p.evidence_columns:
        extra = ""
        if e.default is not None:
            extra += f", default {e.default!r}"
        if e.levels:
            extra += f", levels {list(e.levels)!r}"
        console.print(
            f"evidence: {escape(e.name)} = {e.agg}({escape(e.column)}){escape(extra)}",
            highlight=False,
        )
    prov = p.provenance
    console.print(
        f"provenance: {prov.source}"
        + (f" - {escape(prov.ref)}" if prov.ref else "")
        + (f" - {escape(prov.url)}" if prov.url else "")
        + f" (accessed {prov.accessed})",
        highlight=False,
    )
    for citation in p.citations:
        console.print(f"citation: {escape(citation)}", highlight=False)
    if p.what_it_does_not_claim:
        console.print("what it does not claim:", highlight=False)
        for item in p.what_it_does_not_claim:
            console.print(f"  - {escape(item.strip())}", highlight=False)
    if p.notes:
        console.print(f"notes: {escape(p.notes.strip())}", highlight=False)
    if sql:
        from mimicwarehouse.phenotypes.compiler import compile_phenotype

        console.print(
            escape(
                compile_phenotype(
                    p, registry.codesets, resolved=entry.resolved, concepts=entry.concept_hashes
                ).sql
            ),
            highlight=False,
        )


@phenotype_app.command("validate")
def validate_command(
    ref: Annotated[str, typer.Argument(help="Phenotype as <id>@<version>.")],
    defs: DefsOption = None,
    codesets: CodesetsOption = None,
    json_output: JsonOption = False,
) -> None:
    """Check one phenotype beyond the schema: the grain, every reference's kind, lab units
    against the EP-39 catalogue, and that the SQL compiles (no data access; problems exit 1)."""
    prefix = "mwh phenotype validate"
    registry = _registry(prefix, defs, codesets)
    entry = _entry(prefix, registry, ref)
    result = registry_mod.validate(entry, registry.codesets)
    if json_output:
        emit_json(result.to_dict())
    else:
        p = entry.phenotype
        console.print(
            f"{escape(p.ref)} (grain {p.grain}, def_hash {entry.def_hash[:12]}): "
            f"{len(list(p.all_leaves()))} leaf(s), {len(entry.resolved)} reference(s), "
            f"sources {escape(', '.join(result.sources)) or '-'}",
            highlight=False,
        )
        for warning in result.warnings:
            console.print(f"[yellow]warning:[/] {escape(warning)}", highlight=False)
        for problem in result.problems:
            console.print(f"[red]problem:[/] {escape(problem)}", highlight=False)
        console.print(
            "valid" if result.ok else f"{len(result.problems)} problem(s)", highlight=False
        )
    if not result.ok:
        raise typer.Exit(code=EXIT_FINDINGS)


@phenotype_app.command("lock")
def lock_command(
    defs: Annotated[
        Path | None,
        typer.Option(
            "--defs",
            help="The phenotype directory to lock (default: the packaged defs/).",
            show_default=False,
        ),
    ] = None,
    codesets: CodesetsOption = None,
    check: Annotated[
        bool,
        typer.Option("--check", help="Report unlocked phenotypes and exit 1 instead of writing."),
    ] = False,
) -> None:
    """Record every not-yet-locked id@version of a directory in its phenotypes.lock.json
    (the (id, version) immutability rule; a frozen pair whose hash moved is refused)."""
    prefix = "mwh phenotype lock"
    root = defs if defs is not None else registry_mod.packaged_defs_dir()
    try:
        from mimicwarehouse.codesets import registry as codesets_registry

        codeset_registry = codesets_registry.load_registry(codesets or [])
        if check:
            entries = registry_mod.load_dir(root, codeset_registry)
            unlocked = [e.ref for e in entries if not e.locked]
            console.print(
                f"{len(entries)} phenotype(s) under {escape(str(root))}: "
                f"{len(entries) - len(unlocked)} locked, {len(unlocked)} unlocked"
                + (f" ({', '.join(unlocked)})" if unlocked else ""),
                highlight=False,
            )
            if unlocked:
                raise typer.Exit(code=EXIT_FINDINGS)
            return
        result = registry_mod.lock_dir(root, codesets=codeset_registry)
    except (PhenotypeFrozenError, CodeSetFrozenError) as exc:
        fail(prefix, f"refused: {exc}", code=EXIT_REFUSED)
    except (PhenotypeError, CodeSetError) as exc:
        fail(prefix, str(exc), code=EXIT_USAGE)
    console.print(
        f"{escape(str(result.path))}: {len(result.added)} added"
        + (f" ({', '.join(result.added)})" if result.added else "")
        + f", {len(result.unchanged)} already locked",
        highlight=False,
    )


# ---------------------------------------------------------------------------
# compile
# ---------------------------------------------------------------------------


@phenotype_app.command("compile")
def compile_command(
    ctx: typer.Context,
    refs: Annotated[
        list[str] | None,
        typer.Argument(
            help="Phenotypes to build as <id>@<version>; default: every registered one.",
            show_default=False,
        ),
    ] = None,
    tier: TierOption = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Print the compiled SQL; touch nothing.")
    ] = False,
    force: Annotated[
        bool,
        typer.Option("--force", help="Rebuild a version already built with the same def_hash."),
    ] = False,
    defs: DefsOption = None,
    codesets: CodesetsOption = None,
    background: Annotated[
        bool,
        typer.Option(
            "--background",
            help="Detach: launch this compile as a background job (requires --job) and return.",
        ),
    ] = False,
    job: Annotated[
        str | None,
        typer.Option("--job", help="Job name for --background (state under runs/jobs/)."),
    ] = None,
) -> None:
    """Materialise the selected phenotypes on a tier through the DAG runner (build lock,
    benchmark lines, one kind: phenotype run each), then rebuild the catalog so
    mimiciv_derived.phenotype_<id> shows the latest version. A frozen (edited without a
    version bump) phenotype or code set is refused before anything runs; a concept leaf
    needs its concept built for the tier from the SQL the phenotype pins."""
    prefix = "mwh phenotype compile"
    state: CliState = ctx.obj
    registry = _registry(prefix, defs, codesets)
    selection = [r.strip() for r in (refs or []) if r.strip()]
    entries = [_entry(prefix, registry, ref) for ref in selection] or list(registry)
    for entry in entries:
        result = registry_mod.validate(entry, registry.codesets)
        if not result.ok:
            fail(
                prefix,
                f"{entry.ref} does not validate: " + "; ".join(result.problems),
                code=EXIT_FINDINGS,
            )
    if dry_run:
        if background:
            fail(prefix, "--dry-run prints the SQL and touches nothing; drop --background")
        from mimicwarehouse.phenotypes.compiler import compile_phenotype

        for entry in entries:
            compiled = compile_phenotype(
                entry.phenotype,
                registry.codesets,
                resolved=entry.resolved,
                concepts=entry.concept_hashes,
            )
            console.print(escape(compiled.sql), highlight=False)
            console.print(
                f"-- {escape(entry.ref)}: {len(compiled.sql):,} characters, sources "
                f"{escape(', '.join(compiled.sources))}",
                highlight=False,
            )
        return
    settings = state.settings
    resolved_tier = _tier(prefix, tier, settings)

    if background:
        if not job:
            fail(prefix, "--background requires --job NAME")
        from mimicwarehouse.dag import jobs as jobs_mod

        argv: list[str] = []
        if state.data_root_override is not None:
            argv += ["--data-root", str(state.data_root_override)]
        argv += ["phenotype", "compile", *selection, "--tier", resolved_tier]
        if force:
            argv.append("--force")
        for directory in defs or []:
            argv += ["--defs", str(directory)]
        for directory in codesets or []:
            argv += ["--codesets", str(directory)]
        try:
            info = jobs_mod.launch(argv, job, settings)
        except jobs_mod.JobError as exc:
            fail(prefix, str(exc))
        console.print(
            f"launched job [bold]{escape(info.job)}[/] (pid {info.pid}) - log {escape(info.log)}",
            highlight=False,
        )
        console.print(f"check it with: mwh jobs --job {escape(info.job)}", highlight=False)
        return

    from mimicwarehouse import config
    from mimicwarehouse.dag import runner as runner_mod
    from mimicwarehouse.dag.spec import DagError, load_dag
    from mimicwarehouse.phenotypes import runner as phen_runner

    configure_progress_logging()
    try:
        with phen_runner.compile_options(
            select=selection, extra_dirs=defs or [], codeset_dirs=codesets or [], force=force
        ):
            result = runner_mod.run(
                load_dag(),
                resolved_tier,
                select=[phen_runner.STEP_COMPILE, "catalog"],
                settings=settings,
                provenance=True,
            )
    except (DagError, runner_mod.BuildLockError, config.ConfigError) as exc:
        fail(prefix, str(exc))

    from rich.table import Table as RichTable

    from mimicwarehouse.inventory import fmt_int

    table = RichTable(title=f"mwh phenotype compile {result.build_id}", pad_edge=False)
    for col, justify in (
        ("step", "left"),
        ("status", "left"),
        ("rows", "right"),
        ("wall s", "right"),
    ):
        table.add_column(col, justify=justify)  # type: ignore[arg-type]
    for s in result.steps:
        table.add_row(
            escape(s.name), s.status, "" if s.rows is None else fmt_int(s.rows), f"{s.wall_s:,.1f}"
        )
    console.print(table)
    if result.run_id is not None:
        console.print(f"run {result.run_id} (mwh runs show {result.run_id})", highlight=False)
    failed = [s for s in result.steps if s.status == "failed"]
    if failed:
        message = failed[0].error or "step failed"
        if message.startswith((PhenotypeFrozenError.__name__, CodeSetFrozenError.__name__)):
            fail(prefix, f"refused: {message}", code=EXIT_REFUSED)
        fail(prefix, message, code=EXIT_FINDINGS)
    if not result.ok:
        raise typer.Exit(code=EXIT_FINDINGS)
    lake_root = settings.lake_root(resolved_tier)
    k = settings.k_suppression
    built = RichTable(title=f"phenotypes on tier {resolved_tier}", pad_edge=False)
    for col, justify in (
        ("phenotype", "left"),
        ("grain", "left"),
        ("units", "right"),
        ("positive", "right"),
        ("def_hash", "left"),
        ("run", "left"),
    ):
        built.add_column(col, justify=justify)  # type: ignore[arg-type]
    for entry in entries:
        attempt = phen_runner.phenotype_attempt(lake_root, resolved_tier, entry.ref) or {}
        n_positive = attempt.get("n_positive")
        built.add_row(
            escape(entry.ref),
            entry.phenotype.grain,
            fmt_int(int(attempt.get("rows") or 0)),
            phen_runner.SMALL_CELL
            if n_positive is not None and 0 < n_positive < k
            else fmt_int(int(n_positive or 0)),
            entry.def_hash[:12],
            escape(str(attempt.get("run_id") or "-")),
        )
    console.print(built)
    console.print(
        f"{fmt_int(len(entries))} phenotype(s) on tier {resolved_tier}; summary: "
        f"mwh phenotype summary {entries[0].ref} --tier {resolved_tier}",
        highlight=False,
    )


# ---------------------------------------------------------------------------
# summary
# ---------------------------------------------------------------------------


@phenotype_app.command("summary")
def summary_command(
    ctx: typer.Context,
    refs: Annotated[
        list[str],
        typer.Argument(help="Phenotype(s) as <id>@<version> (each the latest built version)."),
    ],
    tier: TierOption = None,
    k: Annotated[
        int | None,
        typer.Option("--k", help="Suppression threshold (default: settings; >= 11 on dev/full)."),
    ] = None,
    json_output: JsonOption = False,
    report: Annotated[
        bool,
        typer.Option(
            "--report",
            help="Record the reads in a kind: analysis run and write phenotype_prevalence.md "
            "into its run folder (claim type exploratory; sidecar pending EP-43).",
        ),
    ] = False,
    out: Annotated[
        Path | None,
        typer.Option(
            "--out",
            help="Directory for phenotype_prevalence.md (implies --report; default: the run "
            "folder runs/<run_id>/).",
            show_default=False,
        ),
    ] = None,
    no_agreement: Annotated[
        bool,
        typer.Option("--no-agreement", help="Skip the pairwise per-admission 2x2 tables."),
    ] = False,
    defs: DefsOption = None,
    codesets: CodesetsOption = None,
) -> None:
    """Prevalence of built phenotypes through safe_query: n_units, n_positive and share
    overall and by era (hadm_era), the distribution of every evidence column with
    declared levels, and the pairwise per-admission agreement 2x2 — k-suppressed,
    audited, aggregates only. --report / --out also write phenotype_prevalence.md."""
    prefix = "mwh phenotype summary"
    state: CliState = ctx.obj
    settings = state.settings
    resolved_tier = _tier(prefix, tier, settings)
    selection = [r.strip() for r in refs if r.strip()]
    if not selection:
        fail(prefix, "give at least one phenotype as <id>@<version>")
    registry = _registry(prefix, defs, codesets)
    from mimicwarehouse.catalog.cli import safe_cli_errors
    from mimicwarehouse.phenotypes import runner as phen_runner

    write_report = report or out is not None
    report_path: Path | None = None
    try:
        with safe_cli_errors(prefix):
            if write_report:
                from mimicwarehouse import run as run_mod

                with run_mod.start(
                    "phenotype prevalence",
                    tier=resolved_tier,
                    kind="analysis",
                    params={"refs": selection, "k": k, "agreement": not no_agreement},
                    settings=settings,
                    claim_type=phen_runner.CLAIM_TYPE,
                ) as r:
                    bundle = phen_runner.prevalence_report(
                        selection,
                        tier=resolved_tier,
                        settings=settings,
                        k=k,
                        run=r,
                        registry=registry,
                        agreements=not no_agreement,
                    )
                    report_path = phen_runner.write_prevalence_report(bundle, out or r.dir)
                    r.manifest.params = {
                        **r.manifest.params,
                        "report": str(report_path),
                        "rows_suppressed": bundle.rows_suppressed,
                    }
            else:
                bundle = phen_runner.prevalence_report(
                    selection,
                    tier=resolved_tier,
                    settings=settings,
                    k=k,
                    registry=registry,
                    agreements=not no_agreement,
                )
    except PhenotypeError as exc:
        fail(prefix, str(exc), code=EXIT_USAGE)
    if json_output:
        payload: dict[str, Any] = bundle.to_dict()
        if len(bundle.summaries) == 1:  # the EP-41 single-phenotype shape stays readable
            payload = {**bundle.summaries[0].to_dict(), **payload}
        if report_path is not None:
            payload["report"] = str(report_path)
        emit_json(payload)
        return
    from rich.table import Table as RichTable

    from mimicwarehouse.inventory import fmt_int

    def share_text(value: Any) -> str:
        return "" if value is None else f"{float(value):.1%}"

    for summary in bundle.summaries:
        table = RichTable(
            title=f"mwh phenotype summary {summary.ref} ({resolved_tier}, k={summary.k})",
            pad_edge=False,
        )
        for col, justify in (
            ("scope", "left"),
            ("unit", "left"),
            ("n_units", "right"),
            ("n_positive", "right"),
            ("share", "right"),
        ):
            table.add_column(col, justify=justify)  # type: ignore[arg-type]
        for row in summary.df.to_dicts():
            table.add_row(
                escape(str(row["scope"])),
                str(row["unit"]),
                fmt_int(int(row["n_units"])),
                fmt_int(int(row["n_positive"])),
                share_text(row["share"]),
            )
        console.print(table)
        console.print(
            console_safe(
                f"{summary.ref} on {resolved_tier} (grain {summary.grain}): "
                f"{fmt_int(summary.rows_suppressed)} row(s) suppressed at k={summary.k}; "
                f"audit {', '.join(a[:12] for a in summary.audit_ids)}"
            ),
            highlight=False,
        )
    for dist in bundle.distributions:
        table = RichTable(
            title=f"{dist.column} of {dist.ref} ({resolved_tier}, k={dist.k})", pad_edge=False
        )
        for col, justify in (
            ("level", "left"),
            ("n_units", "right"),
            ("n_positive", "right"),
            ("share", "right"),
        ):
            table.add_column(col, justify=justify)  # type: ignore[arg-type]
        for row in dist.df.to_dicts():
            table.add_row(
                escape(str(row["level"])),
                fmt_int(int(row["n_units"])),
                fmt_int(int(row["n_positive"])),
                share_text(row["share"]),
            )
        console.print(table)
        console.print(
            console_safe(
                f"levels {list(dist.levels)!r}; {fmt_int(dist.rows_suppressed)} row(s) "
                f"suppressed; audit {dist.audit_id[:12]}"
            ),
            highlight=False,
        )
    for pair in bundle.agreements:
        id_a = pair.ref_a.partition("@")[0]
        id_b = pair.ref_b.partition("@")[0]
        table = RichTable(
            title=f"{pair.ref_a} x {pair.ref_b} per admission ({resolved_tier}, k={pair.k})",
            pad_edge=False,
        )
        for col, justify in (("", "left"), (f"{id_b} yes", "right"), (f"{id_b} no", "right")):
            table.add_column(escape(col), justify=justify)  # type: ignore[arg-type]

        def cell(value: int | None) -> str:
            return "<k" if value is None else fmt_int(value)

        table.add_row(escape(f"{id_a} yes"), cell(pair.n_both), cell(pair.n_a_only))
        table.add_row(escape(f"{id_a} no"), cell(pair.n_b_only), cell(pair.n_neither))
        console.print(table)
        console.print(
            console_safe(
                f"denominator: {pair.denominator}, n = {cell(pair.n_hadm)}; "
                f"{fmt_int(pair.rows_suppressed)} row(s) suppressed; audit {pair.audit_id[:12]}"
            ),
            highlight=False,
        )
    if report_path is not None:
        console.print(
            f"report {escape(str(report_path))} (run {escape(bundle.run_id or '-')}; "
            f"mwh runs show {escape(bundle.run_id or '-')})",
            highlight=False,
        )


__all__ = ["TIERS", "phenotype_app"]
