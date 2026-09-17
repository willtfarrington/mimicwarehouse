"""The freeze registry — reference resolution, ``freeze`` / ``verify`` / ``amend`` over
``runs/protocols.jsonl`` and the read-only frozen copies, the ``runs.protocols`` view
columns, the audit lines and the docs renderer (EP-51 item 2; DESIGN §13; GOVERNANCE
§8/§12; D-25; the EP-33 ledger canon).

* **Resolution** (:func:`resolve`): the protocol's cohort reference resolves against the
  EP-46 cohort registry (packaged specs + lock) to the spec's ``def_hash`` — and the
  protocol's ``unit_of_analysis`` must be that spec's grain; code-set and phenotype
  references resolve through the registries the cohort registry was loaded with; a
  vendored concept resolves to the sha256 of the SQL the concept runner executes
  (:func:`phenotypes.registry.concept_pin`, EP-42's pin). An unknown reference is
  :class:`ProtocolReferenceError`.
* **Freeze** (:func:`freeze`; ``mwh protocol freeze <yaml>``): validate, resolve, compute
  the content hash, copy the YAML **byte for byte** to ``runs/protocols/<hash>.yaml``
  (:func:`fsio.atomic_write_text`, then the read-only attribute — a later
  ``os.replace`` onto it fails, which is the point), append one :class:`RegistryLine`
  to ``runs/protocols.jsonl`` (:func:`fsio.append_jsonl`, append-only) and write a
  ``protocol freeze`` audit line (GOVERNANCE §8). Freezing content that is already
  frozen is a no-op that returns the existing hash; freezing an ``id@version`` that is
  already frozen **with another hash** is refused (:class:`ProtocolFrozenError` — the
  cohort / code-set immutability rule, one layer up: bump the version or amend); a YAML
  carrying ``amends`` is refused by ``freeze`` and taken by ``amend``.
* **Amend** (:func:`amend`; ``mwh protocol amend <yaml> --previous <hash> --reason …``):
  the YAML's ``amends`` must equal ``--previous``, which must be a frozen hash of the
  **same** protocol id; the version must be greater than the previous one; the reason
  (``--reason``, else the YAML's ``amendment_reason``) goes into the ledger line; then
  it freezes — the new hash is linked to the previous one through ``amends`` in both the
  YAML (hashed) and the ledger.
* **Verify** (:func:`verify`; ``mwh protocol verify <yaml | hash>``): recompute and
  compare — ``ok`` / ``drift`` (the frozen copy no longer hashes to its name, or the
  YAML's content is not the frozen one) / ``unfrozen`` / ``unknown`` / ``missing_copy``.
  :func:`check_frozen` is the run-side form: it returns the line, the parsed frozen copy
  and its resolution or raises :class:`ProtocolRefusedError` — what ``mwh protocol run``
  refuses with exit 3.
* **The view.** :data:`PROTOCOLS_COLUMNS` types the ``runs.protocols`` view that
  :func:`mimicwarehouse.run.runs_db_views` adds beside ``ledger`` / ``manifests`` (built by
  ``safe.build_runs_db``, ``mwh runs refresh``). ``runs.*`` is not a safe-query registry
  exemption (EP-33 amendment): a session lists protocols with ``mwh protocol list`` or a
  count-family ``mwh sql`` over ``runs.protocols``.

Everything written, printed or returned is definition text, hashes, ids and paths —
never a row (GOVERNANCE §4). Import budget: pydantic + stdlib plus the spec module and the
EP-46 registry (already on the ``mwh --help`` path through ``cohort.cli``); ``run``,
``safe``, ``duckdb`` and the phenotype registry's concept catalogue load inside function
bodies.
"""

from __future__ import annotations

import hashlib
import logging
import os
import stat
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import version as dist_version
from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from mimicwarehouse import fsio
from mimicwarehouse.codesets.spec import CodeSetError, UnknownCodeSetError
from mimicwarehouse.cohort import registry as cohort_registry_mod
from mimicwarehouse.cohort.spec import CohortSpecError, UnknownCohortSpecError
from mimicwarehouse.config import Settings, get_settings
from mimicwarehouse.phenotypes.spec import PhenotypeError, UnknownPhenotypeError
from mimicwarehouse.protocol.spec import (
    HASH_RE,
    Protocol,
    ProtocolError,
    json_schema,
    load_protocol,
    protocol_from_text,
    read_text,
)

if TYPE_CHECKING:  # pragma: no cover
    from mimicwarehouse.cohort.registry import Registry as CohortRegistry

_LOG = logging.getLogger(__name__)

#: ``<data_root>/runs/protocols.jsonl`` — the append-only registry (backed up by EP-52).
LEDGER_FILENAME = "protocols.jsonl"
#: ``<data_root>/runs/protocols/<hash>.yaml`` — the immutable copies (EP-52 backs them up).
FROZEN_DIRNAME = "protocols"
#: ``src/mimicwarehouse/protocol/specs/`` — the packaged seed protocol(s).
SPECS_DIRNAME = "specs"
#: The seed protocol (the tracer question, EP-31 / EP-46).
SEED_REF = "tracer_mortality@1.0.0"
SEED_FILENAME = "tracer_mortality.yaml"
#: The audit-line verbs (GOVERNANCE §8: "protocol freeze/run").
AUDIT_PREFIX = "protocol "


class ProtocolReferenceError(ProtocolError):
    """A cohort / code-set / phenotype / concept reference cannot be resolved, or the
    unit of analysis is not the cohort's grain."""


class UnknownProtocolError(ProtocolError, LookupError):
    """No frozen protocol with that hash in the registry."""


class ProtocolFrozenError(ProtocolError):
    """An ``id@version`` is already frozen with a different content hash — the file
    changed after the freeze; bump the version or amend."""


class ProtocolRefusedError(ProtocolError):
    """A run refusal (D-25): the hash is unknown, the frozen copy no longer hashes to its
    name, or a ``--yaml`` override differs from the frozen copy."""


# ---------------------------------------------------------------------------
# The ledger line and the view columns
# ---------------------------------------------------------------------------


class RegistryLine(BaseModel):
    """One ``runs/protocols.jsonl`` line — hashes, ids, paths and a timestamp; never a
    value from data."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    protocol_id: str
    version: str
    claim_type: str
    cohort: str
    cohort_hash: str
    unit_of_analysis: str
    timestamp_utc: str
    git_sha: str | None = None
    path: str
    frozen_path: str
    source_sha256: str
    ref_hashes: dict[str, str] = Field(default_factory=dict)
    amends: str | None = None
    amendment_reason: str | None = None
    actor: str

    @property
    def ref(self) -> str:
        return f"{self.protocol_id}@{self.version}"


#: DuckDB column types of the ``runs.protocols`` view (one per :class:`RegistryLine`
#: field — ``test_ep51`` pins the parity); the dict field binds as ``JSON``.
PROTOCOLS_COLUMNS: dict[str, str] = {
    "hash": "VARCHAR",
    "protocol_id": "VARCHAR",
    "version": "VARCHAR",
    "claim_type": "VARCHAR",
    "cohort": "VARCHAR",
    "cohort_hash": "VARCHAR",
    "unit_of_analysis": "VARCHAR",
    "timestamp_utc": "VARCHAR",
    "git_sha": "VARCHAR",
    "path": "VARCHAR",
    "frozen_path": "VARCHAR",
    "source_sha256": "VARCHAR",
    "ref_hashes": "JSON",
    "amends": "VARCHAR",
    "amendment_reason": "VARCHAR",
    "actor": "VARCHAR",
}


# ---------------------------------------------------------------------------
# Paths, the packaged seed, reading the ledger
# ---------------------------------------------------------------------------


def ledger_path(settings: Settings | None = None) -> Path:
    """``<data_root>/runs/protocols.jsonl``."""
    return (settings or get_settings()).layout["runs"] / LEDGER_FILENAME


def frozen_dir(settings: Settings | None = None) -> Path:
    """``<data_root>/runs/protocols/``."""
    return (settings or get_settings()).layout["runs"] / FROZEN_DIRNAME


def require_hash(value: str) -> str:
    """``value`` lower-cased when it is a 64-hex hash, else :class:`ProtocolError`."""
    text = str(value).strip().lower()
    if not HASH_RE.match(text):
        raise ProtocolError(f"{value!r} is not a protocol hash (expected 64 hex characters)")
    return text


def frozen_path(digest: str, settings: Settings | None = None) -> Path:
    """``<data_root>/runs/protocols/<hash>.yaml``."""
    return frozen_dir(settings) / f"{require_hash(digest)}.yaml"


def packaged_specs_dir() -> Path:
    """``src/mimicwarehouse/protocol/specs/`` inside the installed package."""
    return Path(str(files("mimicwarehouse.protocol").joinpath(SPECS_DIRNAME)))


def seed_path() -> Path:
    """The packaged seed protocol's YAML (``tracer_mortality@1.0.0``)."""
    return packaged_specs_dir() / SEED_FILENAME


def read_registry(settings: Settings | None = None) -> list[RegistryLine]:
    """Every ledger line in append order (one torn trailing line tolerated, LGR-1); ``[]``
    before the first freeze. A line that does not validate is corruption and raises."""
    lines: list[RegistryLine] = []
    for i, raw in enumerate(fsio.iter_jsonl(ledger_path(settings))):
        try:
            lines.append(RegistryLine.model_validate(raw))
        except ValidationError as exc:
            raise ProtocolError(
                f"{ledger_path(settings)}: line {i + 1} is not a registry line "
                f"({exc.error_count()} validation error(s))"
            ) from None
    return lines


def find(digest: str, settings: Settings | None = None) -> RegistryLine | None:
    """The line for ``digest`` (the first append wins; a hash is frozen once), or None."""
    wanted = require_hash(digest)
    for line in read_registry(settings):
        if line.hash == wanted:
            return line
    return None


def get(digest: str, settings: Settings | None = None) -> RegistryLine:
    """The line for ``digest``; :class:`UnknownProtocolError` when it was never frozen."""
    line = find(digest, settings)
    if line is None:
        raise UnknownProtocolError(
            f"protocol hash {require_hash(digest)[:12]}... is not in the registry "
            f"({ledger_path(settings)}); freeze it first (`mwh protocol freeze <yaml>`)"
        )
    return line


def lines_for(ref: str, settings: Settings | None = None) -> list[RegistryLine]:
    """Every line frozen under ``id@version`` (normally at most one)."""
    return [line for line in read_registry(settings) if line.ref == ref]


def lineage(digest: str, settings: Settings | None = None) -> list[RegistryLine]:
    """The amendment chain ending at ``digest``: oldest first (each line's ``amends``
    names its predecessor; a broken link stops the walk)."""
    lines = {line.hash: line for line in read_registry(settings)}
    chain: list[RegistryLine] = []
    current: str | None = require_hash(digest)
    seen: set[str] = set()
    while current is not None and current in lines and current not in seen:
        seen.add(current)
        chain.append(lines[current])
        current = lines[current].amends
    return list(reversed(chain))


# ---------------------------------------------------------------------------
# Reference resolution and the content hash
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Resolved:
    """What a protocol's references resolve to: the cohort's ``def_hash`` and grain, and
    ``{"cohort" | "codeset" | "phenotype" | "concept": {ref: hash}}``."""

    cohort_hash: str
    cohort_grain: str
    hashes: dict[str, dict[str, str]]

    @property
    def flat(self) -> dict[str, str]:
        """``{"<kind>:<ref>": hash}`` — the ledger's ``ref_hashes``."""
        out: dict[str, str] = {}
        for kind in ("cohort", "codeset", "phenotype", "concept"):
            for ref, digest in sorted(self.hashes.get(kind, {}).items()):
                out[f"{kind}:{ref}"] = digest
        return out


def resolve(protocol: Protocol, cohort_registry: CohortRegistry | None = None) -> Resolved:
    """Resolve every reference (module docstring); :class:`ProtocolReferenceError` names
    the first unresolved one or a grain mismatch."""
    registry = (
        cohort_registry if cohort_registry is not None else cohort_registry_mod.load_registry()
    )
    try:
        entry = registry.get(protocol.cohort)
    except UnknownCohortSpecError as exc:
        raise ProtocolReferenceError(f"{protocol.ref}: cohort {exc}") from None
    except CohortSpecError as exc:
        raise ProtocolReferenceError(f"{protocol.ref}: cohort reference: {exc}") from None
    if entry.spec.grain != protocol.unit_of_analysis:
        raise ProtocolReferenceError(
            f"{protocol.ref}: unit_of_analysis {protocol.unit_of_analysis!r} is not the grain of "
            f"cohort {protocol.cohort} ({entry.spec.grain!r})"
        )
    hashes: dict[str, dict[str, str]] = {
        "cohort": {protocol.cohort: entry.def_hash},
        "codeset": {},
        "phenotype": {},
        "concept": {},
    }
    for ref in protocol.codeset_refs:
        try:
            hashes["codeset"][ref] = registry.codesets.get(ref).codeset.def_hash
        except UnknownCodeSetError as exc:
            raise ProtocolReferenceError(f"{protocol.ref}: {exc}") from None
        except CodeSetError as exc:
            raise ProtocolReferenceError(
                f"{protocol.ref}: code-set reference {ref!r}: {exc}"
            ) from None
    for ref in protocol.phenotype_refs:
        try:
            hashes["phenotype"][ref] = registry.phenotypes.get(ref).def_hash
        except UnknownPhenotypeError as exc:
            raise ProtocolReferenceError(f"{protocol.ref}: {exc}") from None
        except PhenotypeError as exc:
            raise ProtocolReferenceError(
                f"{protocol.ref}: phenotype reference {ref!r}: {exc}"
            ) from None
    if protocol.concept_tables:
        from mimicwarehouse.phenotypes.registry import concept_pin

        for table in protocol.concept_tables:
            pin = concept_pin(table.split(".", 1)[1])
            if pin is None:
                raise ProtocolReferenceError(
                    f"{protocol.ref}: {table} is not a vendored concept (the EP-37 inventory)"
                )
            hashes["concept"][table] = pin.executed_sha256
    return Resolved(cohort_hash=entry.def_hash, cohort_grain=entry.spec.grain, hashes=hashes)


def content_hash(protocol: Protocol, resolved: Resolved) -> str:
    """The protocol's content hash over its resolution."""
    return protocol.content_hash(resolved.hashes)


def hash_file(
    path: Path | str, cohort_registry: CohortRegistry | None = None
) -> tuple[Protocol, Resolved, str]:
    """Parse, resolve and hash one YAML file: ``(protocol, resolved, hash)``."""
    protocol = load_protocol(path)
    resolved = resolve(protocol, cohort_registry)
    return protocol, resolved, content_hash(protocol, resolved)


# ---------------------------------------------------------------------------
# Audit lines (GOVERNANCE §8)
# ---------------------------------------------------------------------------


def _git_short_sha() -> str:
    from mimicwarehouse.dag.runner import git_short_sha

    return git_short_sha()


def audit(
    verb: str,
    digest: str,
    settings: Settings,
    *,
    tier: str,
    allowed: bool,
    reason: str | None = None,
    actor: str | None = None,
    started: float | None = None,
) -> str:
    """Append one ``protocol <verb> <hash>`` line to ``runs/audit.jsonl`` through the
    EP-30 :class:`~mimicwarehouse.safe.AuditLine` (the same writer every ``safe_query``
    uses); returns the audit id."""
    from mimicwarehouse.safe import AuditLine, audit_path

    text = f"{AUDIT_PREFIX}{verb} {digest}"
    wall_ms = 0.0 if started is None else round((time.perf_counter() - started) * 1000, 1)
    line = AuditLine(
        audit_id=uuid.uuid4().hex,
        ts=datetime.now(UTC).isoformat(timespec="milliseconds"),
        actor=actor or settings.role,
        tier=tier,
        statement_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        sql_text=text,
        allowed=allowed,
        refusal_reason=None if allowed else (reason or "refused")[:200],
        n_rows=None,
        rows_suppressed=None,
        k=settings.k_suppression,
        wall_ms=wall_ms,
        duckdb_version=_duckdb_version(),
        snapshot_ids={},
        git_sha=_git_short_sha(),
    )
    fsio.append_jsonl(audit_path(settings), line.model_dump(mode="json"))
    return line.audit_id


def _duckdb_version() -> str:
    try:
        return dist_version("duckdb")
    except Exception:  # pragma: no cover - metadata missing in a broken env
        return "unknown"


# ---------------------------------------------------------------------------
# Freeze / amend
# ---------------------------------------------------------------------------


def set_read_only(path: Path) -> None:
    """Set the read-only attribute (Windows) / clear the write bits (POSIX)."""
    os.chmod(path, stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)


def is_read_only(path: Path) -> bool:
    return path.is_file() and not os.access(path, os.W_OK)


def semver_key(version: str) -> tuple[int, ...]:
    return tuple(int(p) for p in version.split("."))


@dataclass(frozen=True, slots=True)
class FreezeResult:
    """What one freeze / amend did: the hash, the ledger line, the copy's path and
    whether this call created them (``False`` = the content was already frozen)."""

    hash: str
    line: RegistryLine
    frozen_path: Path
    created: bool
    protocol: Protocol
    resolved: Resolved


def _data_root_relative(path: Path, settings: Settings) -> str:
    try:
        return path.resolve().relative_to(settings.data_root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _freeze(
    path: Path,
    settings: Settings,
    *,
    actor: str | None,
    previous: str | None,
    reason: str | None,
    cohort_registry: CohortRegistry | None,
) -> FreezeResult:
    started = time.perf_counter()
    text = read_text(path)
    protocol = protocol_from_text(text, where=path.name)
    verb = "freeze" if previous is None else "amend"
    if previous is None and protocol.amends is not None:
        raise ProtocolError(
            f"{path.name}: {protocol.ref} carries amends {protocol.amends[:12]}...; use "
            "`mwh protocol amend <yaml> --previous <hash> --reason ...`"
        )
    reason_text: str | None = None
    if previous is not None:
        previous = require_hash(previous)
        if protocol.amends != previous:
            raise ProtocolError(
                f"{path.name}: {protocol.ref} must declare `amends: {previous}` (the YAML says "
                f"{protocol.amends[:12] + '...' if protocol.amends else 'nothing'}); the frozen "
                "copy is byte for byte, so the link lives in the file"
            )
        prior = get(previous, settings)
        if prior.protocol_id != protocol.id:
            raise ProtocolError(
                f"{path.name}: {protocol.ref} amends {previous[:12]}..., which froze "
                f"{prior.ref} — an amendment keeps the protocol id"
            )
        if semver_key(protocol.version) <= semver_key(prior.version):
            raise ProtocolError(
                f"{path.name}: an amendment bumps the version; {protocol.version} is not above "
                f"{prior.version} (frozen {previous[:12]}...)"
            )
        reason_text = (reason if reason is not None else protocol.amendment_reason).strip()
        if not reason_text:
            raise ProtocolError(
                f"{path.name}: an amendment needs a reason (--reason, or amendment_reason in "
                "the YAML)"
            )
    resolved = resolve(protocol, cohort_registry)
    digest = content_hash(protocol, resolved)
    existing = find(digest, settings)
    if existing is not None:
        audit(verb, digest, settings, tier="-", allowed=True, actor=actor, started=started)
        return FreezeResult(
            hash=digest,
            line=existing,
            frozen_path=frozen_path(digest, settings),
            created=False,
            protocol=protocol,
            resolved=resolved,
        )
    others = [line for line in lines_for(protocol.ref, settings) if line.hash != digest]
    if others:
        audit(
            verb,
            digest,
            settings,
            tier="-",
            allowed=False,
            reason=f"{protocol.ref} already frozen at {others[-1].hash[:12]}",
            actor=actor,
            started=started,
        )
        raise ProtocolFrozenError(
            f"{path.name}: {protocol.ref} is already frozen at {others[-1].hash[:12]}... but the "
            f"file now hashes {digest[:12]}... — the definition (or a referenced cohort / code set "
            "/ phenotype / concept) changed after the freeze; bump `version` (an amendment: "
            "`mwh protocol amend --previous <hash>`) instead of editing a frozen version"
        )
    target = frozen_path(digest, settings)
    target.parent.mkdir(parents=True, exist_ok=True)
    fsio.atomic_write_text(target, text)
    set_read_only(target)
    line = RegistryLine(
        hash=digest,
        protocol_id=protocol.id,
        version=protocol.version,
        claim_type=protocol.claim_type,
        cohort=protocol.cohort,
        cohort_hash=resolved.cohort_hash,
        unit_of_analysis=protocol.unit_of_analysis,
        timestamp_utc=datetime.now(UTC).isoformat(timespec="milliseconds"),
        git_sha=_git_sha(),
        path=path.resolve().as_posix(),
        frozen_path=_data_root_relative(target, settings),
        source_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        ref_hashes=resolved.flat,
        amends=protocol.amends,
        amendment_reason=reason_text,
        actor=actor or settings.role,
    )
    fsio.append_jsonl(ledger_path(settings), line.model_dump(mode="json"))
    audit(verb, digest, settings, tier="-", allowed=True, actor=actor, started=started)
    _LOG.info("protocol %s: %s frozen as %s (%s)", protocol.ref, verb, digest[:12], target)
    return FreezeResult(
        hash=digest,
        line=line,
        frozen_path=target,
        created=True,
        protocol=protocol,
        resolved=resolved,
    )


def _git_sha() -> str | None:
    from mimicwarehouse.run import git_sha

    return git_sha()


def freeze(
    path: Path | str,
    settings: Settings | None = None,
    *,
    actor: str | None = None,
    cohort_registry: CohortRegistry | None = None,
) -> FreezeResult:
    """Freeze one protocol YAML (module docstring)."""
    return _freeze(
        Path(path),
        settings or get_settings(),
        actor=actor,
        previous=None,
        reason=None,
        cohort_registry=cohort_registry,
    )


def amend(
    path: Path | str,
    *,
    previous: str,
    reason: str | None = None,
    settings: Settings | None = None,
    actor: str | None = None,
    cohort_registry: CohortRegistry | None = None,
) -> FreezeResult:
    """Freeze an amendment of ``previous`` (module docstring)."""
    return _freeze(
        Path(path),
        settings or get_settings(),
        actor=actor,
        previous=previous,
        reason=reason,
        cohort_registry=cohort_registry,
    )


# ---------------------------------------------------------------------------
# Verify / check_frozen
# ---------------------------------------------------------------------------

VerifyStatus = str  # "ok" | "drift" | "unfrozen" | "unknown" | "missing_copy"
VERIFY_STATUSES: tuple[str, ...] = ("ok", "drift", "unfrozen", "unknown", "missing_copy")


@dataclass(frozen=True, slots=True)
class VerifyResult:
    """What ``verify`` found: the target, the hash it resolved to (or expected), the
    status and a one-line message."""

    target: str
    hash: str | None
    status: str
    message: str
    line: RegistryLine | None = None

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "hash": self.hash,
            "status": self.status,
            "ok": self.ok,
            "message": self.message,
            "line": None if self.line is None else self.line.model_dump(mode="json"),
        }


def _check_copy(
    line: RegistryLine,
    settings: Settings,
    cohort_registry: CohortRegistry | None,
) -> tuple[str, str, Protocol | None, Resolved | None]:
    """``(status, message, protocol, resolved)`` for a registered hash: does the frozen
    copy still hash to its name?"""
    copy = frozen_path(line.hash, settings)
    if not copy.is_file():
        return (
            "missing_copy",
            f"frozen copy {copy} is missing (restore it from backup, EP-52)",
            None,
            None,
        )
    try:
        protocol = load_protocol(copy)
        resolved = resolve(protocol, cohort_registry)
    except ProtocolError as exc:
        return "drift", f"frozen copy {copy.name} no longer loads: {exc}", None, None
    recomputed = content_hash(protocol, resolved)
    if recomputed != line.hash:
        source_now = hashlib.sha256(read_text(copy).encode("utf-8")).hexdigest()
        if source_now == line.source_sha256:
            why = (
                "the file is byte-identical, so a referenced definition moved (cohort / code set / "
                "phenotype / concept)"
            )
        else:
            why = "the frozen copy was edited"
        return (
            "drift",
            f"frozen copy {copy.name} now hashes {recomputed[:12]}... ({why})",
            protocol,
            resolved,
        )
    return "ok", f"{line.ref} frozen at {line.hash[:12]}... verifies", protocol, resolved


def verify(
    target: str | Path,
    settings: Settings | None = None,
    *,
    cohort_registry: CohortRegistry | None = None,
) -> VerifyResult:
    """``mwh protocol verify <yaml | hash>`` (module docstring)."""
    settings = settings or get_settings()
    text = str(target)
    if HASH_RE.match(text.strip().lower()):
        digest = text.strip().lower()
        line = find(digest, settings)
        if line is None:
            return VerifyResult(text, digest, "unknown", f"{digest[:12]}... is not in the registry")
        status, message, _p, _r = _check_copy(line, settings, cohort_registry)
        return VerifyResult(text, digest, status, message, line)
    path = Path(text)
    protocol, _resolved, digest = hash_file(path, cohort_registry)
    line = find(digest, settings)
    if line is None:
        others = lines_for(protocol.ref, settings)
        if others:
            message = (
                f"{path.name} hashes {digest[:12]}..., but {protocol.ref} is frozen at "
                f"{others[-1].hash[:12]}... — the file changed since the freeze"
            )
            return VerifyResult(text, digest, "drift", message, others[-1])
        return VerifyResult(
            text,
            digest,
            "unfrozen",
            f"{path.name} ({protocol.ref}) is not frozen: hashes {digest[:12]}...",
        )
    status, message, _p, _r = _check_copy(line, settings, cohort_registry)
    return VerifyResult(text, digest, status, message, line)


def check_frozen(
    digest: str,
    settings: Settings | None = None,
    *,
    cohort_registry: CohortRegistry | None = None,
) -> tuple[RegistryLine, Protocol, Resolved]:
    """The run-side check (D-25): the line, the parsed frozen copy and its resolution,
    or :class:`ProtocolRefusedError` / :class:`UnknownProtocolError`."""
    settings = settings or get_settings()
    line = get(digest, settings)
    status, message, protocol, resolved = _check_copy(line, settings, cohort_registry)
    if status != "ok" or protocol is None or resolved is None:
        raise ProtocolRefusedError(f"refusing to run {line.ref}: {message}")
    return line, protocol, resolved


# ---------------------------------------------------------------------------
# Docs renderer (docs/methods/protocols.md)
# ---------------------------------------------------------------------------

METHODS_DOC_RELPATH = Path("docs") / "methods" / "protocols.md"
SCHEMA_MARK = ("<!-- schema:begin -->", "<!-- schema:end -->")
EXAMPLE_MARK = ("<!-- example:begin -->", "<!-- example:end -->")
REFUSALS_MARK = ("<!-- refusals:begin -->", "<!-- refusals:end -->")

#: The refusal rules, as rendered on the methods page: ``(command, condition, exit, what)``.
REFUSALS: tuple[tuple[str, str, str, str], ...] = (
    (
        "freeze / amend",
        "the YAML does not validate (schema, dates, eras, grain, claim rules)",
        "2",
        "`ProtocolError` names every problem",
    ),
    (
        "freeze / amend",
        "a reference does not resolve (cohort, code set, phenotype, concept) or the unit of "
        "analysis is not the cohort's grain",
        "2",
        "`ProtocolReferenceError`",
    ),
    ("freeze", "the YAML carries `amends`", "2", "use `amend --previous`"),
    (
        "freeze / amend",
        "the same `id@version` is already frozen with another hash",
        "3",
        "`ProtocolFrozenError` — bump the version or amend",
    ),
    (
        "amend",
        "`--previous` is not the YAML's `amends`, is unknown, froze another protocol id, or "
        "the version is not bumped; no reason",
        "2 (unknown: 3)",
        "`ProtocolError` / `UnknownProtocolError`",
    ),
    (
        "verify",
        "the frozen copy no longer hashes to its name, the YAML's content is not the frozen "
        "one, the hash is unknown, the copy is missing",
        "1",
        "`drift` / `unfrozen` / `unknown` / `missing_copy`",
    ),
    ("run", "the hash is not in the registry", "3", "`UnknownProtocolError`"),
    (
        "run",
        "the frozen copy no longer hashes to its name (edited, or a referenced definition moved)",
        "3",
        "`ProtocolRefusedError`",
    ),
    ("run", "`--yaml` differs from the frozen copy", "3", "`ProtocolRefusedError`"),
    (
        "run",
        "the runner fails (cohort build error)",
        "1",
        "the run is marked `failed` in the ledger",
    ),
    (
        "any run (`run.start`)",
        "`claim_type` confirmatory or causal without a `protocol_hash`",
        "raises",
        "`run.ProtocolPolicyError` (D-25)",
    ),
)


def render_schema_reference(schema: dict[str, Any] | None = None) -> str:
    """The schema reference (generated from :func:`json_schema`), in the EP-46 shape."""
    return cohort_registry_mod.render_schema_reference(
        schema if schema is not None else json_schema(), title="Protocol"
    )


def render_example() -> str:
    """The worked example: the packaged seed protocol's YAML as a fenced block."""
    return "```yaml\n" + read_text(seed_path()).rstrip("\n") + "\n```\n"


def render_refusals() -> str:
    rows = [[f"`{c}`", cond, code, what] for c, cond, code, what in REFUSALS]
    lines = ["| command | refuses when | exit | how |", "|---|---|---|---|"]
    lines += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(lines) + "\n"


def methods_doc_path() -> Path:
    """``mimicwarehouse/docs/methods/protocols.md``."""
    from mimicwarehouse.config import workspace_root

    return workspace_root() / METHODS_DOC_RELPATH


def sync_methods_doc(path: Path | None = None) -> Path:
    """Re-render the generated blocks of the methods page in place (idempotent;
    ``python -m mimicwarehouse.protocol`` runs it; ``test_ep51`` asserts the page is in
    sync). The narrative around the markers is never touched."""
    from mimicwarehouse.dag.benchmarks import replace_marked_block

    target = Path(path) if path is not None else methods_doc_path()
    text = target.read_text(encoding="utf-8")
    for (begin, end), block in (
        (SCHEMA_MARK, render_schema_reference()),
        (EXAMPLE_MARK, render_example()),
        (REFUSALS_MARK, render_refusals()),
    ):
        text = replace_marked_block(text, block, begin=begin, end=end)
    target.write_text(text.rstrip("\n") + "\n", encoding="utf-8", newline="\n")
    return target


__all__ = [
    "AUDIT_PREFIX",
    "EXAMPLE_MARK",
    "FROZEN_DIRNAME",
    "LEDGER_FILENAME",
    "METHODS_DOC_RELPATH",
    "PROTOCOLS_COLUMNS",
    "REFUSALS",
    "REFUSALS_MARK",
    "SCHEMA_MARK",
    "SEED_FILENAME",
    "SEED_REF",
    "SPECS_DIRNAME",
    "VERIFY_STATUSES",
    "FreezeResult",
    "ProtocolFrozenError",
    "ProtocolReferenceError",
    "ProtocolRefusedError",
    "RegistryLine",
    "Resolved",
    "UnknownProtocolError",
    "VerifyResult",
    "amend",
    "audit",
    "check_frozen",
    "content_hash",
    "find",
    "freeze",
    "frozen_dir",
    "frozen_path",
    "get",
    "hash_file",
    "is_read_only",
    "ledger_path",
    "lineage",
    "lines_for",
    "methods_doc_path",
    "packaged_specs_dir",
    "read_registry",
    "render_example",
    "render_refusals",
    "render_schema_reference",
    "require_hash",
    "resolve",
    "seed_path",
    "semver_key",
    "set_read_only",
    "sync_methods_doc",
    "verify",
]
