"""``mwh backup`` — backup of the non-reproducible state (EP-52; GOVERNANCE §11, DESIGN
§3/§15, D-29; attached in :mod:`mimicwarehouse.cli`).

The lake, catalogs, derived layers and marts are rebuildable from raw + code (``mwh init``
+ ``mwh build``), so they are never backed up. What is **not** reproducible is the record
of what was done, and that is :data:`BACKUP_SET` — globs relative to the data root:

* ``runs/*.jsonl`` — the ledgers (``ledger`` EP-35, ``audit`` EP-30, ``benchmarks``
  EP-19, ``protocols`` EP-51), each written only by :func:`fsio.append_jsonl`;
* ``runs/protocols/**`` — the frozen protocol copies (read-only; ``shutil.copy2`` carries
  the attribute along, so the backup copies are read-only too — ``docs/gotchas.md`` §2);
* ``runs/*/manifest.json`` and ``runs/*/sql/**`` — every run's record and statements;
* ``models/registry/**`` (``.json`` / ``.yaml`` only — EP-106) and ``studies/**`` minus
  the data-shaped suffixes of :data:`guard.DATA_EXTENSIONS` (specs and notes only);
* ``--include-run-artifacts`` adds ``runs/*/tables/**`` and ``runs/*/figures/**`` (off by
  default: they may hold derived row-level tables inside the data root).

Excluded on purpose: ``runs/jobs/`` (transient job state and logs, ``layout["runs_jobs"]``)
and ``warehouse/runs.duckdb`` (rebuilt by ``mwh runs refresh``).

``run`` copies the set to ``<target>/mwh-backup-<UTC>/`` mirroring the data-root paths,
re-hashes every copy (a silent quarantine is a hard error, the EP-171 canary rule) and
writes ``backup_manifest.json`` (files with sha256 + bytes, data root, git sha, timestamp,
tool version) with :func:`fsio.atomic_write_text`; the directory is staged under
``publish.new_path_for(target)`` and published with :func:`publish.swap_dir`, so an
interrupted backup never looks complete. **Target safety** (:func:`target_problem`,
refused with exit 3, no override flag — the owner changes the target instead): a volume
the EP-3 detector flags (sync-client label, not a fixed disk, not NTFS/ReFS, OneDrive,
G:/D:), a path inside the data root, a path inside the repository, a volume whose
BitLocker protection is **off** (unknown → a warning). ``verify`` re-hashes a backup
against its manifest; ``restore --from --to`` copies back (never over a non-empty
``runs/``; hashes re-checked) — then ``mwh --data-root <to> runs refresh`` rebuilds
``runs.duckdb`` from the restored ledgers; ``list`` shows the backups under a target with
age and size, and ``mwh doctor``'s ``last_backup`` row warns when the newest is older
than :data:`MAX_AGE_DAYS` days. Nothing here reads a data file's *content* beyond hashing
and copying bytes, and nothing printed is a value from data: paths, counts, hashes,
timestamps only (GOVERNANCE §4).

Import budget: stdlib + typer + :mod:`config` / :mod:`console` / :mod:`fsio` /
:mod:`publish` / :mod:`guard` (all already on the ``mwh --help`` path); ``doctor``'s
BitLocker probe and ``run.git_sha`` load inside function bodies.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

import typer
from rich.markup import escape

from mimicwarehouse import __version__, config, fsio, publish
from mimicwarehouse.config import Settings
from mimicwarehouse.console import (
    EXIT_FINDINGS,
    EXIT_REFUSED,
    EXIT_USAGE,
    console,
    emit_json,
    err_console,
    fail,
)
from mimicwarehouse.guard import DATA_EXTENSIONS

if TYPE_CHECKING:  # pragma: no cover
    from mimicwarehouse.cli import CliState

#: ``<target>/<PREFIX><UTC stamp>/`` — one directory per backup.
BACKUP_DIR_PREFIX = "mwh-backup-"
#: The stamp in the directory name (the ``T`` glues the date: never an isolated 8-digit
#: token for the guard's G4 rule, committed-text canon).
STAMP_FORMAT = "%Y%m%dT%H%M%SZ"
MANIFEST_FILENAME = "backup_manifest.json"
#: ``mwh doctor`` warns when the newest backup is older than this (brief item 3).
MAX_AGE_DAYS = 7
#: sha256 read chunk.
CHUNK_BYTES = 1 << 20
#: Metadata-only file suffixes of the model registry (weights are re-trainable, out of scope).
REGISTRY_SUFFIXES: frozenset[str] = frozenset({".json", ".yaml", ".yml"})
#: What a study workspace never contributes: the guard's data-shaped extensions (G1) —
#: the brief's ``*.parquet | *.duckdb | *.csv`` and every other row-level / archive shape —
#: except the JSONL ledger shapes, which are exactly the append-only record this backup
#: exists for.
STUDY_EXCLUDED_SUFFIXES: frozenset[str] = frozenset(DATA_EXTENSIONS) - {".jsonl", ".ndjson"}


class BackupError(RuntimeError):
    """A backup / verify / restore failure (a copy that does not re-hash, a missing
    manifest, an interrupted publish)."""


class TargetRefused(BackupError):
    """The backup target (or restore destination) is refused — exit 3."""


class NothingToBackUp(BackupError):
    """The data root holds no non-reproducible state yet — exit 2."""


# ---------------------------------------------------------------------------
# The backup set
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BackupRule:
    """One glob of the set, relative to the data root. ``**`` means every file below;
    ``only_suffixes`` keeps just those suffixes, ``skip_suffixes`` drops them (longest
    suffix wins, so ``.duckdb.wal`` is judged as itself); ``artifacts`` marks the rules
    that need ``--include-run-artifacts``."""

    pattern: str
    only_suffixes: frozenset[str] = frozenset()
    skip_suffixes: frozenset[str] = frozenset()
    artifacts: bool = False

    def admits(self, path: Path) -> bool:
        name = path.name.lower()
        if self.only_suffixes:
            return any(name.endswith(s) for s in self.only_suffixes)
        if self.skip_suffixes:
            return not any(name.endswith(s) for s in self.skip_suffixes)
        return True


#: The set (brief item 1), in enumeration order.
BACKUP_SET: tuple[BackupRule, ...] = (
    BackupRule("runs/*.jsonl"),
    BackupRule("runs/protocols/**"),
    BackupRule("runs/*/manifest.json"),
    BackupRule("runs/*/sql/**"),
    BackupRule("models/registry/**", only_suffixes=REGISTRY_SUFFIXES),
    BackupRule("studies/**", skip_suffixes=STUDY_EXCLUDED_SUFFIXES),
    BackupRule("runs/*/tables/**", artifacts=True),
    BackupRule("runs/*/figures/**", artifacts=True),
)


def rules_for(include_run_artifacts: bool) -> tuple[BackupRule, ...]:
    return tuple(r for r in BACKUP_SET if include_run_artifacts or not r.artifacts)


@dataclass(frozen=True, slots=True)
class FileEntry:
    """One file of a backup: data-root-relative posix path, sha256, size."""

    path: str
    sha256: str
    bytes: int

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "sha256": self.sha256, "bytes": self.bytes}


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(CHUNK_BYTES), b""):
            h.update(chunk)
    return h.hexdigest()


def _glob_files(root: Path, pattern: str) -> Iterable[Path]:
    """The regular files ``pattern`` selects under ``root`` (``x/**`` = every file below
    ``x``); symlinks and junctions are never followed or copied."""
    files_pattern = pattern[:-2] + "**/*" if pattern.endswith("/**") else pattern
    for p in root.glob(files_pattern):
        try:
            if p.is_symlink() or p.is_junction():
                continue
            if p.is_file():
                yield p
        except OSError:  # a vanished or unreadable entry is not part of the set
            continue


def enumerate_set(data_root: Path, *, include_run_artifacts: bool = False) -> dict[str, list[Path]]:
    """``{pattern: [files]}`` for the rules in force, each list sorted, a file counted once
    (the first rule that selects it keeps it). Paths are absolute."""
    root = Path(data_root)
    seen: set[Path] = set()
    out: dict[str, list[Path]] = {}
    for rule in rules_for(include_run_artifacts):
        found: list[Path] = []
        for p in sorted(_glob_files(root, rule.pattern)):
            if p in seen or not rule.admits(p):
                continue
            seen.add(p)
            found.append(p)
        out[rule.pattern] = found
    return out


def relative_posix(path: Path, root: Path) -> str:
    return Path(path).relative_to(Path(root)).as_posix()


# ---------------------------------------------------------------------------
# Target safety (brief item 2)
# ---------------------------------------------------------------------------


def _resolved(path: Path | str) -> Path:
    return Path(path).expanduser().resolve()


def _is_within(path: Path, ancestor: Path | None) -> bool:
    if ancestor is None:
        return False
    try:
        return _resolved(path).is_relative_to(_resolved(ancestor))
    except OSError:  # pragma: no cover - unresolvable ancestor
        return False


def _bitlocker_state(drive: str) -> int | None:
    """``System.Volume.BitLockerProtection`` of ``drive`` (``"C:"``) via the doctor's
    probe; None when unknown (off Windows, probe failure). Tests monkeypatch this."""
    if not config.IS_WINDOWS or not drive:
        return None
    from mimicwarehouse.doctor import ProbeError, _bitlocker_protection

    try:
        return _bitlocker_protection(drive)
    except ProbeError:
        return None


@dataclass(frozen=True, slots=True)
class TargetCheck:
    """The verdict on a target: ``problem`` (refuse) and ``warnings`` (proceed)."""

    target: Path
    problem: str | None
    warnings: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.problem is None


def target_problem(
    target: Path | str,
    settings: Settings,
    *,
    check_bitlocker: bool = True,
) -> TargetCheck:
    """Why ``target`` may not hold a backup (module note), or a clean verdict with any
    warnings. Order: the D-29 detector · inside the data root · inside the repository ·
    BitLocker off (unknown → warning)."""
    path = _resolved(target)
    problem = config.location_problem(path, settings.forbidden_drives)
    if problem is not None:
        return TargetCheck(
            path,
            f"{path}: {problem} — a backup never lives on a synced, "
            "virtual or network drive (D-29, GOVERNANCE §11)",
        )
    if _is_within(path, settings.data_root):
        return TargetCheck(
            path,
            f"{path} lies inside the data root {settings.data_root} — a backup must survive "
            "the data root (GOVERNANCE §11)",
        )
    repo = config.repo_root() or config.workspace_root().parent
    if _is_within(path, repo):
        return TargetCheck(
            path,
            f"{path} lies inside the repository {repo} — ledgers are never committed "
            "(GOVERNANCE §3)",
        )
    warnings: list[str] = []
    if check_bitlocker:
        letter = config.drive_letter(path)
        drive = f"{letter}:" if letter else ""
        state = _bitlocker_state(drive) if drive else None
        if state == 2:
            return TargetCheck(
                path,
                f"{path}: BitLocker is off on {drive} — the backup target must be an "
                "encrypted local volume (GOVERNANCE §11); encrypt it or choose another",
            )
        if state != 1:
            label = {3: "encrypting", 4: "decrypting", 5: "suspended", 6: "locked"}.get(
                state or -1, "unknown"
            )
            warnings.append(
                f"BitLocker state of {drive or path} is {label} — confirm the target is "
                "encrypted (GOVERNANCE §11)"
            )
    return TargetCheck(path, None, tuple(warnings))


# ---------------------------------------------------------------------------
# run (brief item 1)
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC)


def _git_sha() -> str | None:
    from mimicwarehouse.run import git_sha

    return git_sha()


@dataclass(frozen=True, slots=True)
class BackupResult:
    """What one ``run`` produced."""

    backup_id: str
    path: Path
    manifest: dict[str, Any]
    warnings: tuple[str, ...] = ()

    @property
    def n_files(self) -> int:
        return int(self.manifest["n_files"])

    @property
    def total_bytes(self) -> int:
        return int(self.manifest["total_bytes"])

    def summary(self) -> dict[str, Any]:
        """The manifest without its file list (what ``--json`` prints)."""
        return {k: v for k, v in self.manifest.items() if k != "files"} | {
            "path": str(self.path),
            "warnings": list(self.warnings),
        }


def _copy_checked(src: Path, dest: Path, expected_sha: str) -> None:
    """``shutil.copy2`` (mtime + the read-only attribute travel along) then a re-hash of
    the copy: a copy that does not hash to its source is a hard error."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    got = sha256_of(dest)
    if got != expected_sha:
        raise BackupError(
            f"{dest}: the copy does not hash to its source ({got[:12]} != "
            f"{expected_sha[:12]}) — check the antivirus quarantine (D-42)"
        )


def build_manifest(
    files: list[FileEntry],
    *,
    backup_id: str,
    data_root: Path,
    rules: Iterable[BackupRule],
    include_run_artifacts: bool,
    per_rule: dict[str, int],
    timestamp: datetime,
) -> dict[str, Any]:
    return {
        "backup_id": backup_id,
        "tool": "mimicwarehouse",
        "tool_version": __version__,
        "timestamp_utc": timestamp.isoformat(timespec="seconds"),
        "data_root": str(data_root),
        "git_sha": _git_sha(),
        "include_run_artifacts": include_run_artifacts,
        "rules": [r.pattern for r in rules],
        "files_per_rule": per_rule,
        "n_files": len(files),
        "total_bytes": sum(f.bytes for f in files),
        "files": [f.to_dict() for f in files],
    }


def run_backup(
    target: Path | str,
    settings: Settings,
    *,
    include_run_artifacts: bool = False,
    check_bitlocker: bool = True,
) -> BackupResult:
    """Copy the set to ``<target>/mwh-backup-<UTC>/`` (module note). Raises
    :class:`TargetRefused`, :class:`NothingToBackUp` or :class:`BackupError`."""
    verdict = target_problem(target, settings, check_bitlocker=check_bitlocker)
    if verdict.problem is not None:
        raise TargetRefused(verdict.problem)
    target_dir = verdict.target
    data_root = _resolved(settings.data_root)
    selected = enumerate_set(data_root, include_run_artifacts=include_run_artifacts)
    sources = [p for files in selected.values() for p in files]
    if not sources:
        raise NothingToBackUp(
            f"no non-reproducible state under {settings.data_root} (no runs/ ledgers, run "
            "records, frozen protocols, registry metadata or study specs) — nothing to back up"
        )
    entries = [
        FileEntry(relative_posix(p, data_root), sha256_of(p), p.stat().st_size) for p in sources
    ]
    total = sum(e.bytes for e in entries)
    target_dir.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(str(config.nearest_existing(target_dir))).free
    if free < total:
        raise TargetRefused(
            f"{target_dir}: {free / 2**20:,.1f} MB free, the backup needs "
            f"{total / 2**20:,.1f} MB — free space or choose another target"
        )
    stamp = _now()
    backup_id = f"{BACKUP_DIR_PREFIX}{stamp:{STAMP_FORMAT}}"
    dest = target_dir / backup_id
    if dest.exists():
        raise BackupError(f"{dest} already exists — a backup per second; retry in a moment")
    # the staging leftovers of interrupted earlier attempts (never published, so never a
    # backup) are swept before a new one is staged
    for stale in target_dir.glob(f"{BACKUP_DIR_PREFIX}*{publish.NEW_SUFFIX}"):
        if stale.is_dir():
            publish.rmtree(stale)
    staging = publish.new_path_for(dest)
    staging.mkdir(parents=True)
    for src, entry in zip(sources, entries, strict=True):
        _copy_checked(src, staging / Path(*entry.path.split("/")), entry.sha256)
    manifest = build_manifest(
        entries,
        backup_id=backup_id,
        data_root=data_root,
        rules=rules_for(include_run_artifacts),
        include_run_artifacts=include_run_artifacts,
        per_rule={pattern: len(files) for pattern, files in selected.items()},
        timestamp=stamp,
    )
    fsio.atomic_write_text(
        staging / MANIFEST_FILENAME, json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    )
    publish.swap_dir(staging, dest)
    return BackupResult(backup_id, dest, manifest, verdict.warnings)


# ---------------------------------------------------------------------------
# verify (brief item 3)
# ---------------------------------------------------------------------------


def read_backup_manifest(backup_dir: Path | str) -> dict[str, Any]:
    """The ``backup_manifest.json`` of ``backup_dir``; :class:`BackupError` when absent
    or malformed."""
    path = Path(backup_dir) / MANIFEST_FILENAME
    if not path.is_file():
        raise BackupError(f"{backup_dir} is not a backup directory ({MANIFEST_FILENAME} missing)")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BackupError(f"{path}: cannot read the manifest ({exc.__class__.__name__})") from exc
    if not isinstance(data, dict) or not isinstance(data.get("files"), list):
        raise BackupError(f"{path}: not a backup manifest (no files list)")
    return data


@dataclass(frozen=True, slots=True)
class VerifyResult:
    """What ``verify`` found: counts and the findings (``kind path``), empty when clean."""

    backup_dir: Path
    n_files: int
    total_bytes: int
    mismatched: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    unexpected: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not (self.mismatched or self.missing or self.unexpected)

    @property
    def findings(self) -> list[str]:
        return (
            [f"mismatch {p}" for p in self.mismatched]
            + [f"missing {p}" for p in self.missing]
            + [f"unexpected {p}" for p in self.unexpected]
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "backup_dir": str(self.backup_dir),
            "ok": self.ok,
            "n_files": self.n_files,
            "total_bytes": self.total_bytes,
            "mismatched": list(self.mismatched),
            "missing": list(self.missing),
            "unexpected": list(self.unexpected),
        }


def verify_backup(backup_dir: Path | str) -> VerifyResult:
    """Re-hash every manifest entry under ``backup_dir`` and walk the directory for files
    the manifest does not list (the manifest itself excepted)."""
    root = _resolved(backup_dir)
    manifest = read_backup_manifest(root)
    mismatched: list[str] = []
    missing: list[str] = []
    listed: set[str] = set()
    total = 0
    for entry in manifest["files"]:
        rel = str(entry["path"])
        listed.add(rel)
        path = root / Path(*rel.split("/"))
        if not path.is_file():
            missing.append(rel)
            continue
        if sha256_of(path) != entry["sha256"] or path.stat().st_size != int(entry["bytes"]):
            mismatched.append(rel)
        total += int(entry["bytes"])
    unexpected = sorted(
        relative_posix(p, root)
        for p in root.rglob("*")
        if p.is_file() and p.name != MANIFEST_FILENAME and relative_posix(p, root) not in listed
    )
    return VerifyResult(
        root, len(manifest["files"]), total, tuple(mismatched), tuple(missing), tuple(unexpected)
    )


# ---------------------------------------------------------------------------
# restore (brief item 3)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RestoreResult:
    backup_dir: Path
    destination: Path
    n_files: int
    total_bytes: int
    dry_run: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "backup_dir": str(self.backup_dir),
            "destination": str(self.destination),
            "n_files": self.n_files,
            "total_bytes": self.total_bytes,
            "dry_run": self.dry_run,
        }


def restore_problem(destination: Path | str, settings: Settings) -> str | None:
    """Why ``destination`` may not receive a restore: a non-empty ``runs/`` already there
    (restores never overwrite a live record), or a location the D-29 detector flags."""
    dest = _resolved(destination)
    runs = dest / "runs"
    if runs.is_dir() and any(runs.iterdir()):
        return (
            f"{runs} exists and is not empty — a restore never overwrites an existing runs/; "
            "point --to at an empty directory (then `mwh --data-root <to> runs refresh`)"
        )
    problem = config.location_problem(dest, settings.forbidden_drives)
    if problem is not None:
        return f"{dest}: {problem} — project state never lives there (D-29)"
    return None


def restore_backup(
    backup_dir: Path | str,
    destination: Path | str,
    settings: Settings,
    *,
    dry_run: bool = False,
) -> RestoreResult:
    """Verify ``backup_dir`` (a backup that fails verification is never restored), then
    copy every listed file to ``destination/<path>`` with a re-hash of each copy."""
    verdict = verify_backup(backup_dir)
    if not verdict.ok:
        raise BackupError(
            f"{verdict.backup_dir} fails verification ({len(verdict.findings)} finding(s): "
            f"{', '.join(verdict.findings[:3])}{'...' if len(verdict.findings) > 3 else ''}) — "
            "refusing to restore a damaged backup"
        )
    problem = restore_problem(destination, settings)
    if problem is not None:
        raise TargetRefused(problem)
    dest = _resolved(destination)
    manifest = read_backup_manifest(verdict.backup_dir)
    if not dry_run:
        dest.mkdir(parents=True, exist_ok=True)
        for entry in manifest["files"]:
            rel = Path(*str(entry["path"]).split("/"))
            _copy_checked(verdict.backup_dir / rel, dest / rel, str(entry["sha256"]))
    return RestoreResult(verdict.backup_dir, dest, verdict.n_files, verdict.total_bytes, dry_run)


# ---------------------------------------------------------------------------
# list + the doctor's last-backup age (brief item 3)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BackupInfo:
    """One backup under a target: id, path, timestamp, age, size — read from its manifest
    (``valid`` is False when the manifest is missing or unreadable)."""

    backup_id: str
    path: Path
    timestamp_utc: str | None
    age_days: float | None
    n_files: int | None
    total_bytes: int | None
    valid: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "backup_id": self.backup_id,
            "path": str(self.path),
            "timestamp_utc": self.timestamp_utc,
            "age_days": self.age_days,
            "n_files": self.n_files,
            "total_bytes": self.total_bytes,
            "valid": self.valid,
        }


def _parse_stamp(name: str) -> datetime | None:
    try:
        return datetime.strptime(name.removeprefix(BACKUP_DIR_PREFIX), STAMP_FORMAT).replace(
            tzinfo=UTC
        )
    except ValueError:
        return None


def list_backups(target: Path | str) -> list[BackupInfo]:
    """The ``mwh-backup-*`` directories under ``target`` (``.new`` / ``.old`` siblings of
    an interrupted swap excluded), newest first; ``[]`` when the target does not exist."""
    root = Path(target)
    if not root.is_dir():
        return []
    now = _now()
    found: list[BackupInfo] = []
    for p in root.iterdir():
        if not p.is_dir() or not p.name.startswith(BACKUP_DIR_PREFIX):
            continue
        if p.name.endswith((publish.NEW_SUFFIX, publish.OLD_SUFFIX)):
            continue
        try:
            manifest = read_backup_manifest(p)
        except BackupError:
            stamp = _parse_stamp(p.name)
            age = (now - stamp).total_seconds() / 86400 if stamp else None
            found.append(BackupInfo(p.name, p, None, age, None, None, False))
            continue
        ts_text = manifest.get("timestamp_utc")
        stamp = None
        if isinstance(ts_text, str):
            try:
                stamp = datetime.fromisoformat(ts_text)
            except ValueError:
                stamp = None
        if stamp is None:
            stamp = _parse_stamp(p.name)
        age = (now - stamp).total_seconds() / 86400 if stamp else None
        found.append(
            BackupInfo(
                p.name,
                p,
                ts_text if isinstance(ts_text, str) else None,
                None if age is None else round(age, 3),
                int(manifest.get("n_files", len(manifest["files"]))),
                int(manifest.get("total_bytes", 0)),
                True,
            )
        )
    found.sort(key=lambda b: (b.timestamp_utc or "", b.backup_id), reverse=True)
    return found


def last_backup(target: Path | str | None) -> BackupInfo | None:
    """The newest valid backup under ``target``, or None."""
    if target is None:
        return None
    valid = [b for b in list_backups(target) if b.valid]
    return valid[0] if valid else None


def _mb(n: int | None) -> str:
    return "-" if n is None else f"{n / 2**20:,.1f} MB"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

backup_app = typer.Typer(
    name="backup",
    help=(
        "Backup of the non-reproducible state (EP-52, GOVERNANCE section 11): runs/ ledgers, "
        "frozen protocols, run manifests + SQL, model-registry metadata, study specs -> "
        "<target>/mwh-backup-<UTC>/ with a hashed manifest. run | verify | restore | list. "
        "The target must be a local, BitLocker-protected fixed volume outside the data root "
        "and the repository (never G:/D: or a synced drive); refusals exit 3."
    ),
    no_args_is_help=True,
    rich_markup_mode="rich",
)

JsonOption = Annotated[bool, typer.Option("--json", help="Machine output (raw integers).")]
TargetOption = Annotated[
    Path | None,
    typer.Option(
        "--target",
        help="Backup target directory (default: MWH_BACKUP_TARGET).",
        show_default=False,
    ),
]


def _target(prefix: str, target: Path | None, settings: Settings) -> Path:
    resolved = target if target is not None else settings.backup_target
    if resolved is None:
        fail(
            prefix,
            "no backup target: pass --target <dir> or set MWH_BACKUP_TARGET (an encrypted "
            "local directory outside the data root and the repository)",
            code=EXIT_USAGE,
        )
    return Path(resolved)


def _print_warnings(prefix: str, warnings: Iterable[str]) -> None:
    for w in warnings:
        err_console.print(f"[yellow]{escape(prefix)}:[/] {escape(w)}", highlight=False)


@backup_app.command("run")
def run_command(
    ctx: typer.Context,
    target: TargetOption = None,
    include_run_artifacts: Annotated[
        bool,
        typer.Option(
            "--include-run-artifacts",
            help="Also copy runs/*/tables/** and runs/*/figures/** (off by default: they may "
            "hold derived row-level tables).",
        ),
    ] = False,
    json_output: JsonOption = False,
) -> None:
    """Copy the backup set to <target>/mwh-backup-<UTC>/ with a hashed manifest; prints the
    path and the byte totals only. Refuses a synced / virtual / non-BitLocker target, a
    target inside the data root or the repository (exit 3)."""
    prefix = "mwh backup run"
    state: CliState = ctx.obj
    settings = state.settings
    target_dir = _target(prefix, target, settings)
    try:
        result = run_backup(target_dir, settings, include_run_artifacts=include_run_artifacts)
    except TargetRefused as exc:
        fail(prefix, f"refused: {exc}", code=EXIT_REFUSED)
    except NothingToBackUp as exc:
        fail(prefix, str(exc), code=EXIT_USAGE)
    except BackupError as exc:
        fail(prefix, str(exc), code=EXIT_FINDINGS)
    except OSError as exc:  # an I/O failure mid-copy: the staging dir is never published
        fail(prefix, f"{exc.__class__.__name__}: {exc} (nothing was published)", code=EXIT_FINDINGS)
    _print_warnings(prefix, result.warnings)
    if json_output:
        emit_json(result.summary())
        return
    console.print(
        f"backup [bold]{escape(result.backup_id)}[/] -> {escape(str(result.path))}: "
        f"{result.n_files:,} file(s), {_mb(result.total_bytes)} from "
        f"{escape(str(settings.data_root))}"
        + (" (run artifacts included)" if include_run_artifacts else ""),
        highlight=False,
    )
    per_rule = ", ".join(
        f"{pattern} {n}" for pattern, n in result.manifest["files_per_rule"].items()
    )
    console.print(f"set: {escape(per_rule)}", highlight=False)
    console.print(
        f"verify with: mwh backup verify {escape(str(result.path))}",
        highlight=False,
    )


@backup_app.command("verify")
def verify_command(
    backup_dir: Annotated[Path, typer.Argument(help="A mwh-backup-<UTC> directory.")],
    json_output: JsonOption = False,
) -> None:
    """Re-hash every file of a backup against its backup_manifest.json; exit 1 naming each
    mismatched, missing or unexpected file. Reads only the backup directory."""
    prefix = "mwh backup verify"
    try:
        result = verify_backup(backup_dir)
    except BackupError as exc:
        fail(prefix, str(exc), code=EXIT_USAGE)
    if json_output:
        emit_json(result.to_dict())
    elif result.ok:
        console.print(
            f"[green]ok[/]: {result.n_files:,} file(s), {_mb(result.total_bytes)} verified in "
            f"{escape(str(result.backup_dir))}",
            highlight=False,
        )
    else:
        where = escape(str(result.backup_dir))
        console.print(
            f"[red]failed[/]: {len(result.findings)} finding(s) in {where}", highlight=False
        )
        for finding in result.findings:
            console.print(f"  {escape(finding)}", highlight=False)
    if not result.ok:
        raise typer.Exit(code=EXIT_FINDINGS)


@backup_app.command("restore")
def restore_command(
    ctx: typer.Context,
    backup_dir: Annotated[Path, typer.Option("--from", help="The backup directory.")],
    destination: Annotated[
        Path,
        typer.Option(
            "--to",
            help="Destination data root (its runs/ must be empty or absent).",
        ),
    ],
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Verify and report; copy nothing.")
    ] = False,
    json_output: JsonOption = False,
) -> None:
    """Verify a backup, then copy it back under --to (never over a non-empty runs/); every
    copy is re-hashed. Then `mwh --data-root <to> runs refresh` rebuilds runs.duckdb."""
    prefix = "mwh backup restore"
    state: CliState = ctx.obj
    try:
        result = restore_backup(backup_dir, destination, state.settings, dry_run=dry_run)
    except TargetRefused as exc:
        fail(prefix, f"refused: {exc}", code=EXIT_REFUSED)
    except BackupError as exc:
        fail(prefix, str(exc), code=EXIT_FINDINGS)
    except OSError as exc:
        fail(prefix, f"{exc.__class__.__name__}: {exc}", code=EXIT_FINDINGS)
    if json_output:
        emit_json(result.to_dict())
        return
    verb = "would restore" if dry_run else "restored"
    console.print(
        f"{verb} {result.n_files:,} file(s), {_mb(result.total_bytes)} from "
        f"{escape(str(result.backup_dir))} to {escape(str(result.destination))}",
        highlight=False,
    )
    if not dry_run:
        console.print(
            f"next: mwh --data-root {escape(str(result.destination))} runs refresh",
            highlight=False,
        )


@backup_app.command("list")
def list_command(
    ctx: typer.Context,
    target: TargetOption = None,
    json_output: JsonOption = False,
) -> None:
    """List the backups under the target (id, age, files, size), newest first."""
    prefix = "mwh backup list"
    state: CliState = ctx.obj
    target_dir = _target(prefix, target, state.settings)
    backups = list_backups(target_dir)
    if json_output:
        emit_json({"target": str(target_dir), "backups": [b.to_dict() for b in backups]})
        return
    if not backups:
        console.print(
            f"no backups under {escape(str(target_dir))} (mwh backup run --target ...)",
            highlight=False,
        )
        return
    from rich.table import Table as RichTable

    table = RichTable(title=f"backups under {target_dir} ({len(backups)})", pad_edge=False)
    table.add_column("backup_id")
    table.add_column("age (days)", justify="right")
    table.add_column("files", justify="right")
    table.add_column("size", justify="right")
    table.add_column("manifest")
    for b in backups:
        table.add_row(
            escape(b.backup_id),
            "-" if b.age_days is None else f"{b.age_days:.1f}",
            "-" if b.n_files is None else f"{b.n_files:,}",
            _mb(b.total_bytes),
            "ok" if b.valid else "[red]missing/unreadable[/]",
        )
    console.print(table)


__all__ = [
    "BACKUP_DIR_PREFIX",
    "BACKUP_SET",
    "CHUNK_BYTES",
    "MANIFEST_FILENAME",
    "MAX_AGE_DAYS",
    "REGISTRY_SUFFIXES",
    "STAMP_FORMAT",
    "STUDY_EXCLUDED_SUFFIXES",
    "BackupError",
    "BackupInfo",
    "BackupResult",
    "BackupRule",
    "FileEntry",
    "NothingToBackUp",
    "RestoreResult",
    "TargetCheck",
    "TargetRefused",
    "VerifyResult",
    "backup_app",
    "build_manifest",
    "enumerate_set",
    "last_backup",
    "list_backups",
    "list_command",
    "read_backup_manifest",
    "relative_posix",
    "restore_backup",
    "restore_command",
    "restore_problem",
    "rules_for",
    "run_backup",
    "run_command",
    "sha256_of",
    "target_problem",
    "verify_backup",
    "verify_command",
]
