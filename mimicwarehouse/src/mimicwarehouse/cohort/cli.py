"""``mwh cohort …`` — the cohort-spec registry commands (EP-46 item 2) and the compiler /
materialisation commands (EP-47 items 1-3); attached in :mod:`mimicwarehouse.cli`.

``list`` / ``show`` / ``schema`` / ``lock`` read the packaged specs (plus ``--specs DIR``
study directories, resolved against the code-set registry plus ``--codesets DIR`` and the
phenotype registry plus ``--phenotypes DIR``) and touch no data; ``validate <ref>`` runs
the static checks (grain, index rule, reference kinds, tables) and, with ``--tier``, the
tier-level ones through ``safe_query`` — every reference compiled / built on the tier
with the resolved hash, and the level-degeneracy probe as suppressed aggregates over the
compiled cohort when it is built on the tier, else over the index population
(:mod:`~mimicwarehouse.cohort.probe`). ``build [refs …] --tier <t>`` runs the
``cohorts.specs`` + ``cohorts.build`` DAG steps and the catalog step through the runner
(the only lake writer; ``--dry-run`` prints the compiled SQL instead; ``--background
--job NAME`` detaches it through the EP-19 launcher, the ⏱ standard for the full tier)
and then re-runs the probe over the compiled cohort; ``attrition <ref | run_id> --tier
<t>`` prints the suppressed attrition chain (:func:`mimicwarehouse.cohort.build.attrition`).
Errors follow the EP-33 canon (:func:`mimicwarehouse.console.fail`): a frozen ``(id,
version)`` — spec, code set or phenotype — refuses with ``EXIT_REFUSED`` (3), a
``safe_query`` refusal too; usage / environment errors exit 2; a validation with
problems or a failed build exits 1.

Import budget: this module is on the ``mwh --help`` path — the spec / registry modules are
pydantic + yaml + stdlib (+ ``timesem``); the compiler, the build module, the probe,
duckdb, polars, the runner and ``safe`` load inside the command bodies.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

import typer
from rich.markup import escape

from mimicwarehouse.codesets.spec import CodeSetError, CodeSetFrozenError
from mimicwarehouse.cohort import registry as registry_mod
from mimicwarehouse.cohort.spec import (
    CohortSpecError,
    CohortSpecFrozenError,
    criterion_text,
    json_schema,
)
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
from mimicwarehouse.phenotypes.spec import PhenotypeError, PhenotypeFrozenError

if TYPE_CHECKING:  # pragma: no cover
    from mimicwarehouse.cli import CliState
    from mimicwarehouse.cohort.registry import Entry, Registry
    from mimicwarehouse.config import Settings

TIERS = ("fixture", "demo", "dev", "full")

cohort_app = typer.Typer(
    name="cohort",
    help=(
        "Cohort specs (EP-46) + the compiler (EP-47): list | show | validate | schema | lock | "
        "build | attrition. Cohort specs are versioned YAML (id@version, def_hash pinned to the "
        "code sets and phenotypes they reference), compiled to one deterministic CTE chain and "
        "materialised per tier under lake/marts/<tier>/cohorts/ (marts.cohort_<id>_v<major>, "
        "marts.cohorts); sessions read them through safe_query as aggregates, the attrition "
        "chain k-suppressed."
    ),
    no_args_is_help=True,
    rich_markup_mode="rich",
)

SpecsOption = Annotated[
    list[Path] | None,
    typer.Option(
        "--specs",
        help="Extra cohort-spec directory (repeatable; e.g. a study's cohorts/ folder).",
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
PhenotypesOption = Annotated[
    list[Path] | None,
    typer.Option(
        "--phenotypes",
        help="Extra phenotype directory the references resolve against (repeatable).",
        show_default=False,
    ),
]
TierOption = Annotated[
    str | None,
    typer.Option(
        "--tier",
        help="Also check the references and run the degeneracy probe on this tier "
        "catalog: fixture | demo | dev | full.",
        show_default=False,
    ),
]
JsonOption = Annotated[bool, typer.Option("--json", help="Machine output (raw integers).")]

_FROZEN = (CohortSpecFrozenError, PhenotypeFrozenError, CodeSetFrozenError)
_USAGE = (CohortSpecError, PhenotypeError, CodeSetError)


def _raw(text: str) -> None:
    """Machine text (YAML / JSON) to stdout verbatim — never through the rich console,
    which would wrap long lines (the ``emit_json`` rule)."""
    sys.stdout.write(text)
    sys.stdout.flush()


def _registry(
    prefix: str,
    specs: list[Path] | None,
    codesets: list[Path] | None,
    phenotypes: list[Path] | None,
) -> Registry:
    try:
        return registry_mod.load_registry(
            specs or [], codeset_dirs=codesets or [], phenotype_dirs=phenotypes or []
        )
    except _FROZEN as exc:
        fail(prefix, f"refused: {exc}", code=EXIT_REFUSED)
    except _USAGE as exc:
        fail(prefix, str(exc), code=EXIT_USAGE)


def _entry(prefix: str, registry: Registry, ref: str) -> Entry:
    try:
        return registry.get(ref)
    except CohortSpecError as exc:
        fail(prefix, str(exc), code=EXIT_USAGE)


def _tier(prefix: str, tier: str | None, settings: Settings) -> str:
    resolved = tier or str(settings.default_tier)
    if resolved not in TIERS:
        fail(prefix, f"unknown tier {resolved!r}; expected one of {', '.join(TIERS)}")
    return resolved


def _entry_payload(entry: Entry) -> dict[str, Any]:
    spec = entry.spec
    return {
        "ref": spec.ref,
        "id": spec.id,
        "version": spec.version,
        "title": spec.title,
        "grain": spec.grain,
        "index_event": spec.index_event.render(),
        "def_hash": entry.def_hash,
        "n_inclusion": len(spec.inclusion),
        "n_exclusion": len(spec.exclusion),
        "custom": spec.custom,
        "criteria": [
            {
                "polarity": polarity,
                "label": c.label,
                "kind": c.kind,
                "custom": c.custom,
                "definition": criterion_text(c),
            }
            for polarity, c in spec.criteria()
        ],
        "observation_window": spec.observation_window.canonical(),
        "washout": spec.washout.canonical(),
        "follow_up": spec.follow_up.canonical(),
        "era_filter": list(spec.era_filter),
        "degeneracy_probe": list(spec.degeneracy_probe.columns),
        "references": {k: dict(v) for k, v in entry.resolved.items()},
        "locked": entry.locked,
        "path": entry.display_path,
    }


# ---------------------------------------------------------------------------
# list / show / schema / lock
# ---------------------------------------------------------------------------


@cohort_app.command("list")
def list_command(
    specs: SpecsOption = None,
    codesets: CodesetsOption = None,
    phenotypes: PhenotypesOption = None,
    json_output: JsonOption = False,
) -> None:
    """The registered cohort specs: id@version, grain, index event, criteria, references,
    locked, def_hash (no data access)."""
    registry = _registry("mwh cohort list", specs, codesets, phenotypes)
    if json_output:
        emit_json({"cohorts": [_entry_payload(e) for e in registry]})
        return
    from rich.table import Table as RichTable

    from mimicwarehouse.inventory import fmt_int

    table = RichTable(title="mwh cohort list", pad_edge=False)
    for col, justify in (
        ("cohort", "left"),
        ("grain", "left"),
        ("index", "left"),
        ("title", "left"),
        ("criteria", "right"),
        ("references", "right"),
        ("locked", "left"),
        ("def_hash", "left"),
    ):
        table.add_column(col, justify=justify)  # type: ignore[arg-type]
    for entry in registry:
        spec = entry.spec
        table.add_row(
            escape(spec.ref),
            spec.grain,
            escape(spec.index_event.render()),
            escape(spec.title),
            fmt_int(len(spec.inclusion) + len(spec.exclusion))
            + (" (custom)" if spec.custom else ""),
            fmt_int(len(entry.flat_refs)),
            "yes" if entry.locked else "no",
            entry.def_hash[:12],
        )
    console.print(table)
    unlocked = [e.ref for e in registry if not e.locked]
    verdict = (
        f"{fmt_int(len(unlocked))} unlocked ({', '.join(unlocked)}) - run `mwh cohort lock`"
        if unlocked
        else "all locked"
    )
    console.print(
        console_safe(f"{fmt_int(len(registry))} cohort spec(s); {verdict}"), highlight=False
    )


@cohort_app.command("show")
def show_command(
    ref: Annotated[
        str, typer.Argument(help="Cohort spec as <id>@<version>, e.g. first_icu_adults@1.0.0.")
    ],
    specs: SpecsOption = None,
    codesets: CodesetsOption = None,
    phenotypes: PhenotypesOption = None,
    json_output: JsonOption = False,
    as_yaml: Annotated[
        bool, typer.Option("--yaml", help="Print the spec as canonical YAML (to_yaml).")
    ] = False,
) -> None:
    """One cohort spec in full: the card, every criterion in order, the resolved references
    and what it does not claim (no data access)."""
    prefix = "mwh cohort show"
    registry = _registry(prefix, specs, codesets, phenotypes)
    entry = _entry(prefix, registry, ref)
    spec = entry.spec
    if json_output:
        payload = {
            **_entry_payload(entry),
            "description": spec.description,
            "notes": spec.notes,
            "what_it_does_not_claim": list(spec.what_it_does_not_claim),
        }
        if as_yaml:
            payload["yaml"] = spec.to_yaml()
        emit_json(payload)
        return
    if as_yaml:
        _raw(spec.to_yaml())
        return
    console.print(f"[bold]{escape(spec.ref)}[/]  {escape(spec.title)}", highlight=False)
    locked = "yes" if entry.locked else "no"
    console.print(
        f"grain {spec.grain} - index {escape(spec.index_event.render())} - def_hash "
        f"{entry.def_hash} - locked {locked} - {escape(entry.display_path)}",
        highlight=False,
    )
    if spec.description:
        console.print(escape(spec.description.strip()), highlight=False)
    for polarity, c in spec.criteria():
        console.print(
            f"  {polarity} {escape(c.label)}: {escape(criterion_text(c))}"
            + (" [custom]" if c.custom else ""),
            highlight=False,
        )
    console.print(
        f"observation window: [{spec.observation_window.start_h:g}, "
        f"{spec.observation_window.end_h:g}) h - washout: {escape(spec.washout.render())} - "
        f"follow-up: {escape(spec.follow_up.render())} - era filter: "
        f"{escape(', '.join(spec.era_filter)) or 'none'}",
        highlight=False,
    )
    console.print(
        f"degeneracy probe: {escape(', '.join(spec.degeneracy_probe.columns)) or 'off'}",
        highlight=False,
    )
    for key, def_hash in sorted(entry.flat_refs.items()):
        console.print(f"reference: {escape(key)} -> {def_hash[:12]}", highlight=False)
    if spec.what_it_does_not_claim:
        console.print("what it does not claim:", highlight=False)
        for item in spec.what_it_does_not_claim:
            console.print(f"  - {escape(item.strip())}", highlight=False)
    if spec.notes:
        console.print(f"notes: {escape(spec.notes.strip())}", highlight=False)


@cohort_app.command("schema")
def schema_command(
    out: Annotated[
        Path | None,
        typer.Option("--out", help="Write the JSON schema to this file instead of stdout."),
    ] = None,
) -> None:
    """Print the JSON schema of a cohort spec (what the EP-62 Cohort Builder form renders;
    the exactly-one-kind-key rule included). No data access."""
    text = json.dumps(json_schema(), indent=2) + "\n"
    if out is not None:
        from mimicwarehouse import fsio

        fsio.atomic_write_text(out, text)
        console.print(f"schema written: {escape(str(out))}", highlight=False)
        return
    _raw(text)


@cohort_app.command("lock")
def lock_command(
    specs: Annotated[
        Path | None,
        typer.Option(
            "--specs",
            help="The cohort-spec directory to lock (default: the packaged specs/).",
            show_default=False,
        ),
    ] = None,
    codesets: CodesetsOption = None,
    phenotypes: PhenotypesOption = None,
    check: Annotated[
        bool,
        typer.Option("--check", help="Report unlocked specs and exit 1 instead of writing."),
    ] = False,
) -> None:
    """Record every not-yet-locked id@version of a directory in its cohorts.lock.json (the
    (id, version) immutability rule; a frozen pair whose hash moved is refused)."""
    prefix = "mwh cohort lock"
    root = specs if specs is not None else registry_mod.packaged_specs_dir()
    try:
        from mimicwarehouse.codesets import registry as codesets_registry
        from mimicwarehouse.phenotypes import registry as phenotypes_registry

        codeset_registry = codesets_registry.load_registry(codesets or [])
        phenotype_registry = phenotypes_registry.load_registry(
            phenotypes or [], codesets=codeset_registry
        )
        if check:
            entries = registry_mod.load_dir(root, codeset_registry, phenotype_registry)
            unlocked = [e.ref for e in entries if not e.locked]
            console.print(
                f"{len(entries)} cohort spec(s) under {escape(str(root))}: "
                f"{len(entries) - len(unlocked)} locked, {len(unlocked)} unlocked"
                + (f" ({', '.join(unlocked)})" if unlocked else ""),
                highlight=False,
            )
            if unlocked:
                raise typer.Exit(code=EXIT_FINDINGS)
            return
        result = registry_mod.lock_dir(
            root, codesets=codeset_registry, phenotypes=phenotype_registry
        )
    except _FROZEN as exc:
        fail(prefix, f"refused: {exc}", code=EXIT_REFUSED)
    except _USAGE as exc:
        fail(prefix, str(exc), code=EXIT_USAGE)
    console.print(
        f"{escape(str(result.path))}: {len(result.added)} added"
        + (f" ({', '.join(result.added)})" if result.added else "")
        + f", {len(result.unchanged)} already locked",
        highlight=False,
    )


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


@cohort_app.command("validate")
def validate_command(
    ctx: typer.Context,
    ref: Annotated[str, typer.Argument(help="Cohort spec as <id>@<version>.")],
    tier: TierOption = None,
    k: Annotated[
        int | None,
        typer.Option("--k", help="Suppression threshold for the probe (>= 11 on dev/full)."),
    ] = None,
    specs: SpecsOption = None,
    codesets: CodesetsOption = None,
    phenotypes: PhenotypesOption = None,
    json_output: JsonOption = False,
) -> None:
    """Check one cohort spec beyond the schema (grain, index rule, reference kinds,
    tables; no data access) and, with --tier, on a tier catalog through safe_query: every
    referenced code set / phenotype compiled with the resolved hash, and the
    level-degeneracy probe (zero-event / all-event levels of the probe columns against the
    follow-up outcome, k-suppressed) over the compiled cohort when `mwh cohort build` has
    materialised it on the tier, else over the index population. Problems exit 1;
    degenerate levels are warnings."""
    prefix = "mwh cohort validate"
    state: CliState = ctx.obj
    registry = _registry(prefix, specs, codesets, phenotypes)
    entry = _entry(prefix, registry, ref)
    static = registry_mod.validate(entry, registry)
    tier_result = None
    if tier is not None:
        settings = state.settings
        resolved_tier = _tier(prefix, tier, settings)
        from mimicwarehouse.catalog.cli import safe_cli_errors
        from mimicwarehouse.cohort import probe as probe_mod

        with safe_cli_errors(prefix):
            tier_result = probe_mod.validate_on_tier(
                entry, tier=resolved_tier, settings=settings, k=k
            )
    problems = list(static.problems) + (list(tier_result.problems) if tier_result else [])
    if json_output:
        payload: dict[str, Any] = {**static.to_dict(), "problems": problems, "ok": not problems}
        if tier_result is not None:
            payload["tier"] = tier_result.to_dict()
        emit_json(payload)
    else:
        spec = entry.spec
        console.print(
            f"{escape(spec.ref)} (grain {spec.grain}, index {escape(spec.index_event.render())}, "
            f"def_hash {entry.def_hash[:12]}): {len(spec.inclusion)} inclusion + "
            f"{len(spec.exclusion)} exclusion criteria, {len(entry.flat_refs)} reference(s)",
            highlight=False,
        )
        for warning in static.warnings:
            console.print(f"[yellow]warning:[/] {escape(warning)}", highlight=False)
        for problem in static.problems:
            console.print(f"[red]problem:[/] {escape(problem)}", highlight=False)
        if tier_result is not None:
            _print_tier_result(tier_result)
        console.print("valid" if not problems else f"{len(problems)} problem(s)", highlight=False)
    if problems:
        raise typer.Exit(code=EXIT_FINDINGS)


def _print_tier_result(result: Any) -> None:
    from rich.table import Table as RichTable

    from mimicwarehouse.inventory import fmt_int

    for check in result.references:
        state = "ok" if check.ok else "PROBLEM"
        tier_hash = (check.tier_hash or "-")[:12]
        console.print(
            f"{check.kind} {escape(check.ref)} on {result.tier}: {state} (tier {tier_hash}, "
            f"registry {check.expected_hash[:12]}"
            + (f", status {check.status}" if check.status else "")
            + ")",
            highlight=False,
        )
    if result.probes:
        over = (
            f"the compiled cohort ({result.cohort_relation})"
            if result.population == "cohort"
            else "the index population (not built on the tier)"
        )
        console.print(f"degeneracy probe over {escape(over)}", highlight=False)
    for probe in result.probes:
        table = RichTable(
            title=f"degeneracy probe {probe.column} ({result.tier}, k={result.k})", pad_edge=False
        )
        for col, justify in (
            ("level", "left"),
            ("n", "right"),
            ("n_events", "right"),
            ("verdict", "left"),
        ):
            table.add_column(col, justify=justify)  # type: ignore[arg-type]
        for row in probe.rows:
            table.add_row(escape(row.level), fmt_int(row.n), fmt_int(row.n_events), row.verdict)
        console.print(table)
        console.print(
            console_safe(
                f"{probe.column}: {fmt_int(len(probe.rows))} level(s) released, "
                f"{fmt_int(probe.rows_suppressed)} withheld at k={result.k}, "
                f"{fmt_int(len(probe.degenerate))} degenerate; audit {probe.audit_id[:12]}"
            ),
            highlight=False,
        )
    for warning in result.warnings:
        console.print(f"[yellow]warning:[/] {escape(warning)}", highlight=False)
    for problem in result.problems:
        console.print(f"[red]problem:[/] {escape(problem)}", highlight=False)


# ---------------------------------------------------------------------------
# build (EP-47)
# ---------------------------------------------------------------------------


@cohort_app.command("build")
def build_command(
    ctx: typer.Context,
    refs: Annotated[
        list[str] | None,
        typer.Argument(
            help="Cohort specs to build as <id>@<version>; default: every registered one.",
            show_default=False,
        ),
    ] = None,
    tier: TierOption = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Print the compiled SQL and the steps; touch nothing."),
    ] = False,
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            help="Rebuild a version already built with the same def_hash, or replace a directory "
            "built from a different definition.",
        ),
    ] = False,
    specs: SpecsOption = None,
    codesets: CodesetsOption = None,
    phenotypes: PhenotypesOption = None,
    background: Annotated[
        bool,
        typer.Option(
            "--background",
            help="Detach: launch this build as a background job (requires --job) and return.",
        ),
    ] = False,
    job: Annotated[
        str | None,
        typer.Option("--job", help="Job name for --background (state under runs/jobs/)."),
    ] = None,
    json_output: JsonOption = False,
) -> None:
    """Compile and materialise the selected cohort specs on a tier through the DAG runner
    (build lock, one kind: cohort run each, a kind: mart benchmark line), rebuild the
    catalog (marts.cohort_<id>_v<major>, marts.cohorts) and re-run the degeneracy probe
    over the compiled cohort. A frozen (edited without a version bump) spec, code set or
    phenotype is refused before anything runs; a referenced set / phenotype must be
    compiled / built on the tier with the resolved hash."""
    prefix = "mwh cohort build"
    state: CliState = ctx.obj
    registry = _registry(prefix, specs, codesets, phenotypes)
    selection = [r.strip() for r in (refs or []) if r.strip()]
    entries = [_entry(prefix, registry, ref) for ref in selection] or list(registry)
    for entry in entries:
        result = registry_mod.validate(entry, registry)
        if not result.ok:
            fail(
                prefix,
                f"{entry.ref} does not validate: " + "; ".join(result.problems),
                code=EXIT_FINDINGS,
            )
    settings = state.settings
    resolved_tier = _tier(prefix, tier, settings)
    if dry_run:
        if background:
            fail(prefix, "--dry-run prints the SQL and touches nothing; drop --background")
        from mimicwarehouse.cohort.compiler import CompileError, compile_entry, describe

        payload: list[dict[str, Any]] = []
        for entry in entries:
            try:
                compiled = compile_entry(entry, registry, resolved_tier)
            except CompileError as exc:
                fail(prefix, str(exc), code=EXIT_FINDINGS)
            if json_output:
                payload.append(
                    {
                        "ref": compiled.ref,
                        "tier": compiled.tier,
                        "sql": compiled.sql,
                        "attrition_sql": compiled.attrition_sql,
                        "steps": [s.to_dict() for s in compiled.steps],
                        "keys": list(compiled.keys),
                        "columns": list(compiled.columns),
                        "sources": list(compiled.sources),
                        "sql_sha256": compiled.sql_sha256,
                        "custom": compiled.custom,
                    }
                )
                continue
            _raw(compiled.sql + "\n")
            console.print(escape(describe(compiled)), highlight=False)
        if json_output:
            emit_json({"cohorts": payload})
        return

    if background:
        if not job:
            fail(prefix, "--background requires --job NAME")
        from mimicwarehouse.dag import jobs as jobs_mod

        argv: list[str] = []
        if state.data_root_override is not None:
            argv += ["--data-root", str(state.data_root_override)]
        argv += ["cohort", "build", *selection, "--tier", resolved_tier]
        if force:
            argv.append("--force")
        for directory in specs or []:
            argv += ["--specs", str(directory)]
        for directory in codesets or []:
            argv += ["--codesets", str(directory)]
        for directory in phenotypes or []:
            argv += ["--phenotypes", str(directory)]
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
    from mimicwarehouse.cohort import build as build_mod
    from mimicwarehouse.dag import runner as runner_mod
    from mimicwarehouse.dag.spec import DagError, load_dag

    # --json keeps stdout for the payload: no progress handler (the console shares stdout
    # with emit_json); warnings then reach stderr through logging's last-resort handler
    if not json_output:
        configure_progress_logging()
    try:
        with build_mod.build_options(
            select=selection,
            extra_dirs=specs or [],
            codeset_dirs=codesets or [],
            phenotype_dirs=phenotypes or [],
            force=force,
        ):
            result = runner_mod.run(
                load_dag(),
                resolved_tier,
                select=[registry_mod.STEP_SPECS, build_mod.STEP_BUILD, "catalog"],
                settings=settings,
                provenance=True,
            )
    except (DagError, runner_mod.BuildLockError, config.ConfigError) as exc:
        fail(prefix, str(exc))

    from mimicwarehouse.inventory import fmt_int

    lake_root = settings.lake_root(resolved_tier)
    builds: list[dict[str, Any]] = []
    for entry in entries:
        attempt = build_mod.cohort_attempt(lake_root, resolved_tier, entry.ref) or {}
        mart = build_mod.read_mart_manifest(lake_root, resolved_tier, entry.ref) or {}
        builds.append(
            {
                "ref": entry.ref,
                "status": attempt.get("status"),
                "def_hash": entry.def_hash,
                "built_def_hash": mart.get("def_hash"),
                "run_id": mart.get("run_id"),
                "build_id": attempt.get("build_id"),
                "rows": mart.get("rows"),
                "n_subjects": mart.get("n_subjects"),
                "sql_sha256": mart.get("sql_sha256"),
                "cohort_sha256": mart.get("cohort_sha256"),
                "snapshot_ids": mart.get("snapshot_ids"),
                "error_class": attempt.get("error_class"),
            }
        )
    if not json_output:
        from rich.table import Table as RichTable

        table = RichTable(title=f"mwh cohort build {result.build_id}", pad_edge=False)
        for col, justify in (
            ("step", "left"),
            ("status", "left"),
            ("rows", "right"),
            ("wall s", "right"),
        ):
            table.add_column(col, justify=justify)  # type: ignore[arg-type]
        for s in result.steps:
            table.add_row(
                escape(s.name),
                s.status,
                fmt_int(s.rows) if s.rows is not None else "-",
                f"{s.wall_s:.1f}",
            )
        console.print(table)
        k = settings.k_suppression

        def cell(n: int | None) -> str:
            return fmt_int(n) if n is None or n == 0 or n >= k else f"<{k}"

        for b in builds:
            counts = f"{cell(b['rows'])} units, {cell(b['n_subjects'])} subjects"
            console.print(
                console_safe(
                    f"{b['ref']}: {b['status'] or 'not attempted'} - {counts} - run "
                    f"{b['run_id'] or '-'} - cohort sha256 "
                    f"{(b['cohort_sha256'] or '-')[:12]}"
                    + (f" - {b['error_class']}" if b["error_class"] else "")
                ),
                highlight=False,
            )
    if not result.ok:
        failed = [f"{s.name}: {s.error}" for s in result.steps if s.status in ("failed", "blocked")]
        if json_output:
            emit_json(
                {"build_id": result.build_id, "ok": False, "failed": failed, "cohorts": builds}
            )
            raise typer.Exit(code=EXIT_FINDINGS)
        fail(prefix, "; ".join(failed), code=EXIT_FINDINGS)
    # the EP-31 policy's spec-level half, over the compiled cohort (EP-46 handoff)
    from mimicwarehouse.catalog.cli import safe_cli_errors
    from mimicwarehouse.cohort import probe as probe_mod

    probes: list[dict[str, Any]] = []
    with safe_cli_errors(prefix):
        for entry in entries:
            attempt = build_mod.cohort_attempt(lake_root, resolved_tier, entry.ref) or {}
            if attempt.get("status") != "done":
                continue
            tier_result = probe_mod.validate_on_tier(
                entry, tier=resolved_tier, settings=settings, population="auto"
            )
            probes.append(tier_result.to_dict())
            if not json_output:
                _print_tier_result(tier_result)
    if json_output:
        emit_json(
            {
                "build_id": result.build_id,
                "ok": True,
                "run_id": result.run_id,
                "snapshot_ids": result.snapshot_ids,
                "cohorts": builds,
                "probes": probes,
            }
        )


# ---------------------------------------------------------------------------
# attrition (EP-47 item 3)
# ---------------------------------------------------------------------------


@cohort_app.command("attrition")
def attrition_command(
    ctx: typer.Context,
    ref: Annotated[
        str,
        typer.Argument(help="A built cohort as <id>@<version>, or the run id of its build."),
    ],
    tier: TierOption = None,
    k: Annotated[
        int | None,
        typer.Option("--k", help="Suppression threshold (>= 11 on dev/full)."),
    ] = None,
    json_output: JsonOption = False,
) -> None:
    """Print the attrition chain of a built cohort — one row per step: units and subjects
    left, units and subjects dropped — k-suppressed in chain mode (small totals withheld,
    small drops withheld and their neighbours banded to the nearest ten; disclose, EP-43).
    The raw counts stay in the mart under the data root."""
    prefix = "mwh cohort attrition"
    state: CliState = ctx.obj
    settings = state.settings
    resolved_tier = _tier(prefix, tier, settings)
    from mimicwarehouse.cohort import build as build_mod
    from mimicwarehouse.cohort.spec import UnknownCohortSpecError

    try:
        result = build_mod.attrition(ref, resolved_tier, k=k, settings=settings)
    except UnknownCohortSpecError as exc:
        fail(prefix, str(exc), code=EXIT_FINDINGS)
    except CohortSpecError as exc:
        fail(prefix, str(exc), code=EXIT_USAGE)
    if json_output:
        emit_json(result.to_dict())
        return
    from rich.table import Table as RichTable

    from mimicwarehouse.disclose import render_cell

    table = RichTable(title=f"attrition {result.ref} ({result.tier}, k={result.k})", pad_edge=False)
    for col, justify in (
        ("step", "left"),
        ("label", "left"),
        ("n_units", "right"),
        ("dropped_units", "right"),
        ("n_subjects", "right"),
        ("dropped_subjects", "right"),
    ):
        table.add_column(col, justify=justify)  # type: ignore[arg-type]
    for row in result.df.to_dicts():
        table.add_row(
            escape(str(row["step"])) + (" [custom]" if row["custom"] else ""),
            escape(str(row["label"])),
            render_cell(row["n_units"], result.k, banded=bool(row["n_units_banded"])),
            "-"
            if row["dropped_units"] is None and row["step"] == result.df["step"][0]
            else (render_cell(row["dropped_units"], result.k)),
            render_cell(row["n_subjects"], result.k, banded=bool(row["n_subjects_banded"])),
            "-"
            if row["dropped_subjects"] is None and row["step"] == result.df["step"][0]
            else (render_cell(row["dropped_subjects"], result.k)),
        )
    console.print(table)
    hidden = sum(v for kk, v in result.report.items() if kk.endswith("_hidden"))
    banded = sum(v for kk, v in result.report.items() if kk.endswith("_banded"))
    console.print(
        console_safe(
            f"{len(result.df)} step(s); {hidden} cell(s) withheld, {banded} banded at "
            f"k={result.k}; build run {result.run_id or '-'}, def_hash "
            f"{(result.def_hash or '-')[:12]}"
        ),
        highlight=False,
    )


__all__ = ["TIERS", "cohort_app"]
