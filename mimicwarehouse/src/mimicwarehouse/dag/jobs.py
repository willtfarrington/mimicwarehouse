"""Background jobs — detached ``mwh`` runs with a state file and a log (EP-19 item 5).

Foreground shell commands are capped at ~10 min, so every full-tier build is a logged
background job. :func:`launch` spawns a **detached** supervisor
(``DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW`` on Windows,
``start_new_session`` elsewhere; stdin ``DEVNULL``, stdout/stderr into the job log)
which runs ``[sys.executable, "-m", "mimicwarehouse.cli", *argv]`` — the allow-listed
workspace-venv python, never a ``uv`` shim (EP-170 amendment: the shim's pid is
useless to psutil) — waits, and rewrites the job's state file on exit. That makes the
final ``state``/``exit_code`` reliable for **any** argv, including hard crashes and
lock refusals; ``mwh build --job <name>`` additionally merges its own outcome into
the same file (the brief's child-side rewrite), which the supervisor then confirms.

State file ``runs/jobs/<job>.json``: ``{job, pid, argv, started, log, state:
running|done|failed, exit_code, finished}`` (``pid`` is the supervisor's — alive iff
the job is). Log ``runs/jobs/<job>.log``: INFO lines only (steps, counts, bytes,
wall, rss) — never rows (GOVERNANCE §4).

Fallback recipe, documented in ``mwh build --help``, for when detaching misbehaves:
resolve the log directory via ``mwh paths --json`` (key ``runs_jobs``, never
``$env:MWH_DATA_ROOT`` — unset it expands to the drive root, EP-170/FC-10) and run
``Start-Process pwsh -ArgumentList '-NoProfile','-c','uv run --group dev mwh build
... *> <runs_jobs>\\<name>.log'``.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from mimicwarehouse.config import Settings, get_settings, workspace_root

JOB_JSON_SUFFIX = ".json"
JOB_LOG_SUFFIX = ".log"
JOB_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

JobState = Literal["running", "done", "failed"]


class JobError(RuntimeError):
    """A job cannot be launched or read (bad name, unknown job, spawn failure)."""


class JobInfo(BaseModel):
    """The ``runs/jobs/<job>.json`` state file."""

    model_config = ConfigDict(extra="forbid")

    job: str
    pid: int
    argv: list[str]
    started: str
    log: str
    state: JobState = "running"
    exit_code: int | None = None
    finished: str | None = None


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def jobs_dir(settings: Settings | None = None) -> Path:
    return (settings or get_settings()).layout["runs_jobs"]


def require_job_name(job: str) -> str:
    if not JOB_NAME_RE.match(job):
        raise JobError(f"job name {job!r} must match {JOB_NAME_RE.pattern}")
    return job


def job_json_path(job: str, settings: Settings | None = None) -> Path:
    return jobs_dir(settings) / f"{require_job_name(job)}{JOB_JSON_SUFFIX}"


def job_log_path(job: str, settings: Settings | None = None) -> Path:
    return jobs_dir(settings) / f"{require_job_name(job)}{JOB_LOG_SUFFIX}"


def _write_info(path: Path, info: JobInfo) -> None:
    from mimicwarehouse.inventory import _atomic_write_text

    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(path, json.dumps(info.model_dump(mode="json"), indent=2) + "\n")


def read_job(job: str, settings: Settings | None = None) -> JobInfo | None:
    path = job_json_path(job, settings)
    if not path.is_file():
        return None
    try:
        return JobInfo.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def update_job(job: str, settings: Settings | None = None, **fields: object) -> JobInfo | None:
    """Merge ``fields`` into an existing job state file (no-op returning None when the
    job has no state file — a foreground ``--job`` run that was never launched)."""
    info = read_job(job, settings)
    if info is None:
        return None
    info = info.model_copy(update=fields)  # type: ignore[arg-type]
    _write_info(job_json_path(job, settings), info)
    return info


def list_jobs(settings: Settings | None = None) -> list[JobInfo]:
    root = jobs_dir(settings)
    out: list[JobInfo] = []
    for path in sorted(root.glob(f"*{JOB_JSON_SUFFIX}")) if root.is_dir() else []:
        try:
            out.append(JobInfo.model_validate_json(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return out


def tail_log(job: str, n: int = 20, settings: Settings | None = None) -> list[str]:
    """The last ``n`` lines of a job's log (INFO counts only — the log never holds rows)."""
    path = job_log_path(job, settings)
    if not path.is_file():
        return []
    return path.read_text(encoding="utf-8", errors="replace").splitlines()[-n:]


def pid_alive(pid: int) -> bool:
    import psutil

    return pid > 0 and psutil.pid_exists(pid)


# ---------------------------------------------------------------------------
# Launch (parent side)
# ---------------------------------------------------------------------------


def launch(argv: list[str], job: str, settings: Settings | None = None) -> JobInfo:
    """Spawn ``mwh <argv>`` detached under supervisor ``python -m mimicwarehouse.dag.jobs``
    and return the running :class:`JobInfo` (module docstring). Refuses to overwrite a
    job of the same name that is still running."""
    settings = settings or get_settings()
    require_job_name(job)
    existing = read_job(job, settings)
    if existing is not None and existing.state == "running" and pid_alive(existing.pid):
        raise JobError(f"job {job!r} is still running (pid {existing.pid}) — pick another name")

    json_path = job_json_path(job, settings)
    log_path = job_log_path(job, settings)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    supervisor = [
        sys.executable,
        "-m",
        "mimicwarehouse.dag.jobs",
        "--job-file",
        str(json_path),
        "--",
        *argv,
    ]
    env = {**os.environ, "PYTHONUTF8": "1"}
    creationflags = 0
    kwargs: dict[str, object] = {}
    if sys.platform == "win32":
        creationflags = (
            subprocess.DETACHED_PROCESS
            | subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.CREATE_NO_WINDOW
        )
    else:  # pragma: no cover - Windows is the target host
        kwargs["start_new_session"] = True
    with log_path.open("ab") as log:
        log.write(f"[launch] {_now()} job={job} argv={argv}\n".encode())
        log.flush()
        try:
            proc = subprocess.Popen(
                supervisor,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                close_fds=True,
                cwd=str(workspace_root()),
                env=env,
                creationflags=creationflags,
                **kwargs,  # type: ignore[arg-type]
            )
        except OSError as exc:
            raise JobError(f"cannot launch job {job!r}: {exc}") from exc

    info = JobInfo(
        job=job,
        pid=proc.pid,
        argv=list(argv),
        started=_now(),
        log=str(log_path),
        state="running",
    )
    _write_info(json_path, info)
    return info


# ---------------------------------------------------------------------------
# Supervisor (child side): python -m mimicwarehouse.dag.jobs --job-file <json> -- <argv...>
# ---------------------------------------------------------------------------


def _supervise(job_file: Path, argv: list[str]) -> int:
    """Run the ``mwh`` child, wait, rewrite the job state file; returns the exit code."""
    print(f"[job] {_now()} start argv={argv}", flush=True)
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "mimicwarehouse.cli", *argv],
            stdin=subprocess.DEVNULL,
            check=False,
        )
        code = proc.returncode
    except Exception as exc:  # spawn failure: record it, never leave state=running
        print(f"[job] {_now()} spawn failed: {type(exc).__name__}: {exc}", flush=True)
        code = -1
    state: JobState = "done" if code == 0 else "failed"
    try:
        info = JobInfo.model_validate_json(job_file.read_text(encoding="utf-8"))
        info = info.model_copy(update={"state": state, "exit_code": code, "finished": _now()})
        _write_info(job_file, info)
    except (OSError, ValueError) as exc:
        print(f"[job] {_now()} cannot rewrite {job_file}: {exc}", flush=True)
    print(f"[job] {_now()} exit code={code} state={state}", flush=True)
    return code


def _main(args: list[str]) -> int:
    try:
        sep = args.index("--")
    except ValueError:
        print("usage: python -m mimicwarehouse.dag.jobs --job-file <json> -- <mwh argv...>")
        return 2
    head, argv = args[:sep], args[sep + 1 :]
    if len(head) != 2 or head[0] != "--job-file" or not argv:
        print("usage: python -m mimicwarehouse.dag.jobs --job-file <json> -- <mwh argv...>")
        return 2
    return _supervise(Path(head[1]), argv)


if __name__ == "__main__":  # pragma: no cover - exercised by the launch test end-to-end
    raise SystemExit(_main(sys.argv[1:]))


__all__ = [
    "JOB_NAME_RE",
    "JobError",
    "JobInfo",
    "JobState",
    "job_json_path",
    "job_log_path",
    "jobs_dir",
    "launch",
    "list_jobs",
    "pid_alive",
    "read_job",
    "require_job_name",
    "tail_log",
    "update_job",
]
