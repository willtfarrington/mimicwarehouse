"""``mwh build`` and ``mwh jobs`` (EP-19 item 6; attached in :mod:`mimicwarehouse.cli`).

``build`` runs the DAG for one tier (foreground) or, with ``--background --job NAME``,
launches itself detached through :func:`mimicwarehouse.dag.jobs.launch` and returns
immediately — the shape every full-tier brief uses (foreground shells are capped at
~10 min). ``jobs`` lists background jobs or prints one job's state and log tail.

``--data-root`` is the **global** ``mwh`` option (EP-167): ``mwh --data-root X build ...``.
``--background`` with ``--dry-run`` is refused (EP-33 DAG-1): a dry run prints its plan to
the foreground console and must never detach a real build. Errors go through
:func:`mimicwarehouse.console.fail` (stderr, exit 2); progress logging through
:func:`mimicwarehouse.console.configure_progress_logging` (EP-33 B8).
Import budget: heavy modules (duckdb, polars, psutil, the runner) are imported inside
the command bodies (cli.py rule).
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Annotated

import typer
from rich.markup import escape

from mimicwarehouse.console import EXIT_FINDINGS, configure_progress_logging, console, fail

if TYPE_CHECKING:  # pragma: no cover
    from mimicwarehouse.cli import CliState

TIERS = ("fixture", "demo", "dev", "full")

#: PowerShell fallback for when detaching misbehaves (EP-170 amendment: resolve the log
#: directory via ``mwh paths --json`` key ``runs_jobs`` — never ``$env:MWH_DATA_ROOT``).
FALLBACK_RECIPE = (
    "Fallback when detaching misbehaves: resolve the jobs directory with "
    "`mwh paths --json` (layout key `runs_jobs`), then run "
    "Start-Process pwsh -ArgumentList '-NoProfile','-c','uv run --group dev mwh build "
    "... *> <runs_jobs>\\<name>.log'"
)


def _split_csv(values: list[str]) -> list[str]:
    out: list[str] = []
    for value in values:
        out.extend(part.strip() for part in value.split(",") if part.strip())
    return out


def build_command(
    ctx: typer.Context,
    tier: Annotated[
        str,
        typer.Option("--tier", help="Tier to build: fixture | demo | dev | full."),
    ],
    select: Annotated[
        list[str] | None,
        typer.Option(
            "--select",
            help="Step name(s) to run (repeatable or comma-separated); default: every step.",
        ),
    ] = None,
    tag: Annotated[
        list[str] | None,
        typer.Option("--tag", help="Only steps carrying one of these tags (repeatable)."),
    ] = None,
    force: Annotated[
        bool, typer.Option("--force", help="Rerun steps already complete for the tier.")
    ] = False,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Print the ordered plan and exit; nothing runs.")
    ] = False,
    background: Annotated[
        bool,
        typer.Option(
            "--background",
            help="Detach: launch this build as a background job (requires --job) and return. "
            + FALLBACK_RECIPE,
        ),
    ] = False,
    job: Annotated[
        str | None,
        typer.Option(
            "--job",
            help="Job name: with --background the name to launch under; without it, the "
            "name of the job state file this (child) run reports into.",
        ),
    ] = None,
    break_lock: Annotated[
        bool,
        typer.Option(
            "--break-lock",
            help="Take over a stale build lock (dead pid). A live build is never broken.",
        ),
    ] = False,
    keep_going: Annotated[
        bool,
        typer.Option(
            "--keep-going",
            help="Record a step failure and continue with the steps that do not depend on "
            "it (dependents are reported blocked); default: stop at the first failure.",
        ),
    ] = False,
    with_deps: Annotated[
        bool,
        typer.Option(
            "--with-deps",
            help="With --select: also run the selected steps' transitive dependencies "
            "(already-complete ones are skipped; --force then applies to the selected "
            "steps only).",
        ),
    ] = False,
) -> None:
    """Run the DAG for one tier (EP-19; the only writer of the lake, DESIGN 6). Every
    packaged spec is merged (EP-37): stage steps, meta.profile, the concept steps
    (--tag concepts) and the shared catalog step."""
    state: CliState = ctx.obj
    if tier not in TIERS:
        fail("mwh build", f"unknown tier {tier!r}; expected one of {', '.join(TIERS)}")
    settings = state.settings
    selects = _split_csv(select or [])
    tags = _split_csv(tag or [])
    if with_deps and not selects:
        fail("mwh build", "--with-deps only applies to a --select list")

    if background:
        if dry_run:  # DAG-1: the plan needs the foreground console; never detach a real build
            fail(
                "mwh build",
                "--dry-run cannot be combined with --background (a dry run prints "
                "its plan to this console; drop --background to see it)",
            )
        if not job:
            fail("mwh build", "--background requires --job NAME")
        from mimicwarehouse.dag import jobs as jobs_mod

        argv: list[str] = []
        if state.data_root_override is not None:
            argv += ["--data-root", str(state.data_root_override)]
        argv += ["build", "--tier", tier]
        for name in selects:
            argv += ["--select", name]
        for t in tags:
            argv += ["--tag", t]
        if force:
            argv.append("--force")
        if break_lock:
            argv.append("--break-lock")
        if keep_going:
            argv.append("--keep-going")
        if with_deps:
            argv.append("--with-deps")
        assert job is not None
        argv += ["--job", job]
        try:
            info = jobs_mod.launch(argv, job, settings)
        except jobs_mod.JobError as exc:
            fail("mwh build", str(exc))
        console.print(
            f"launched job [bold]{escape(info.job)}[/] (pid {info.pid}) - log {escape(info.log)}",
            highlight=False,
        )
        console.print(f"check it with: mwh jobs --job {escape(info.job)}", highlight=False)
        return

    from mimicwarehouse import config
    from mimicwarehouse.dag import jobs as jobs_mod
    from mimicwarehouse.dag import runner as runner_mod
    from mimicwarehouse.dag.spec import DagError, load_dag
    from mimicwarehouse.loader.manifest import utc_now_iso

    configure_progress_logging()

    def report_job(state_: str, exit_code: int) -> None:
        # a bad name only means "no state file to report into" — never fail the build
        if job:
            with contextlib.suppress(jobs_mod.JobError):
                jobs_mod.update_job(
                    job, settings, state=state_, exit_code=exit_code, finished=utc_now_iso()
                )

    try:
        dag = load_dag()
        result = runner_mod.run(
            dag,
            tier,
            select=selects or None,
            tags=tags or None,
            force=force,
            dry_run=dry_run,
            job=job,
            break_lock=break_lock,
            settings=settings,
            keep_going=keep_going,
            with_deps=with_deps,
            provenance=True,  # EP-37: every CLI build is a runs/<run_id> record (EP-35)
        )
    except (DagError, runner_mod.BuildLockError, config.ConfigError) as exc:
        report_job("failed", 2)
        fail("mwh build", str(exc))

    if dry_run:
        console.print(f"[bold]plan[/] ({result.tier}, {len(result.steps)} step(s)):")
        for i, step in enumerate(result.steps, 1):
            console.print(f"  {i}. {escape(step.name)} ({step.kind})", highlight=False)
        return

    from rich.table import Table as RichTable

    table = RichTable(title=f"mwh build {result.build_id}", pad_edge=False)
    for col, justify in (
        ("step", "left"),
        ("kind", "left"),
        ("status", "left"),
        ("rows", "right"),
        ("bytes", "right"),
        ("wall s", "right"),
    ):
        table.add_column(col, justify=justify)  # type: ignore[arg-type]
    for s in result.steps:
        table.add_row(
            escape(s.name),
            s.kind,
            s.status,
            "" if s.rows is None else f"{s.rows:,}",
            "" if s.bytes_out is None else f"{s.bytes_out:,}",
            f"{s.wall_s:,.1f}",
        )
    console.print(table)
    for layer, snapshot_id in sorted(result.snapshot_ids.items()):
        console.print(f"snapshot {layer}/{result.tier} = {snapshot_id}", highlight=False)
    if result.run_id is not None:
        console.print(f"run {result.run_id} (mwh runs show {result.run_id})", highlight=False)
    report_job("done" if result.ok else "failed", 0 if result.ok else EXIT_FINDINGS)
    if not result.ok:
        raise typer.Exit(code=EXIT_FINDINGS)


def jobs_command(
    ctx: typer.Context,
    job: Annotated[
        str | None,
        typer.Option("--job", help="Show one job's state and its last --tail log lines."),
    ] = None,
    tail: Annotated[int, typer.Option("--tail", help="Log lines to print with --job.", min=0)] = 20,
) -> None:
    """List background jobs, or show one job's state and log tail (EP-19)."""
    state: CliState = ctx.obj
    settings = state.settings
    from mimicwarehouse.dag import jobs as jobs_mod

    if job is not None:
        info = jobs_mod.read_job(job, settings)
        if info is None:
            fail("mwh jobs", f"no job {job}")
        alive = jobs_mod.pid_alive(info.pid, info.create_time)
        console.print(
            f"job [bold]{escape(info.job)}[/]  state={info.state}"
            f"{' (pid alive)' if info.state == 'running' and alive else ''}"
            f"{' (pid gone)' if info.state == 'running' and not alive else ''}  "
            f"pid={info.pid}  exit={info.exit_code}",
            highlight=False,
        )
        console.print(f"started={info.started}  finished={info.finished or '-'}", highlight=False)
        console.print(f"log={escape(info.log)}", highlight=False)
        for line in jobs_mod.tail_log(job, tail, settings):
            console.print(escape(line), highlight=False)
        return

    from rich.table import Table as RichTable

    infos = jobs_mod.list_jobs(settings)
    table = RichTable(title="mwh jobs", pad_edge=False)
    for col in ("job", "state", "pid", "alive", "exit", "started", "finished"):
        table.add_column(col)
    for info in infos:
        table.add_row(
            escape(info.job),
            info.state,
            str(info.pid),
            "yes" if jobs_mod.pid_alive(info.pid, info.create_time) else "no",
            "" if info.exit_code is None else str(info.exit_code),
            info.started,
            info.finished or "",
        )
    console.print(table)
    if not infos:
        console.print("no jobs recorded (runs/jobs/ is empty)", highlight=False)


__all__ = ["FALLBACK_RECIPE", "build_command", "jobs_command"]
