"""Protocol runners and ``run_protocol`` — the D-25 refusal rules, the ``kind: protocol``
run and ``runs/<run_id>/protocol_summary.md`` (EP-51 item 3; DESIGN §11/§13; GOVERNANCE
§7/§12).

:func:`run_protocol` (``mwh protocol run <hash> [--tier t] [--runner cohort_only]
[--yaml PATH]``) first applies the refusals through
:func:`~mimicwarehouse.protocol.registry.check_frozen` — the hash must be in the
registry, the frozen copy must still hash to its name, and a ``--yaml`` override must
hash to the same value (each refusal is audited and raised as
:class:`~mimicwarehouse.protocol.registry.ProtocolRefusedError`; the CLI exits 3) — then
opens ``run.start(kind="protocol", protocol_id=..., protocol_hash=..., claim_type=...)``
(EP-35: the manifest and the ``runs/ledger.jsonl`` line carry the hash and the claim
type, ``Run.seed`` derives from the protocol id — EP-36) and records the protocol, the
cohort and every resolved reference as refs, then dispatches to the registered runner.
On success it writes ``protocol_summary.md`` beside the manifest: the title, the claim
type, the retrospective statement, the frozen identity (hash, amendment link), the
pre-specified analysis as declared, the references with their hashes, the runner's
sections and the EP-35 reproduction block. A ``protocol run`` audit line records the
outcome (GOVERNANCE §8).

**Runners** are registered by name in :data:`RUNNERS` (:func:`register_runner`); later
briefs add theirs (``predictive`` → EP-110, ``target_trial`` → EP-95, the temporal
holdout → EP-129) without touching this module. v1 ships **``cohort_only``**: it
materialises the protocol's cohort through EP-47 — reusing the tier's build when it is
complete under the frozen ``def_hash``, else running the ``cohorts.specs`` /
``cohorts.build`` / ``catalog`` steps through the DAG runner — records the mart's
snapshot ids and the cohort build's run id, the **suppressed** attrition chain
(:func:`cohort.build.attrition`, chain mode at the tier's ``k``) into the run manifest,
and renders the chain and the cohort card into the summary.

Everything written, logged or returned is hashes, ids, counts (suppressed), paths and
definition text — never a row (GOVERNANCE §4). Import budget: not on the ``mwh``
start-up path (``protocol.cli`` imports this module inside the command body); ``run``,
the cohort build module, the DAG runner and polars load inside function bodies.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mimicwarehouse.config import Settings, get_settings
from mimicwarehouse.protocol import registry as registry_mod
from mimicwarehouse.protocol.registry import (
    ProtocolRefusedError,
    RegistryLine,
    Resolved,
    UnknownProtocolError,
)
from mimicwarehouse.protocol.spec import Protocol, ProtocolError, load_protocol

if TYPE_CHECKING:  # pragma: no cover
    from mimicwarehouse.run import Run

_LOG = logging.getLogger(__name__)

RUN_KIND = "protocol"
SUMMARY_FILENAME = "protocol_summary.md"
TIERS: tuple[str, ...] = ("fixture", "demo", "dev", "full")
DEFAULT_RUNNER = "cohort_only"


class ProtocolRunError(ProtocolError):
    """The runner could not complete (a cohort build failed); the run is marked failed."""


@dataclass(slots=True)
class RunnerContext:
    """What a runner receives: the open run, the frozen protocol, its ledger line and
    resolution, the tier and settings."""

    run: Run
    protocol: Protocol
    line: RegistryLine
    resolved: Resolved
    tier: str
    settings: Settings


@dataclass(slots=True)
class RunnerReport:
    """What a runner hands back: Markdown sections for the summary (``(heading, body)``)
    and the parameters merged into the run manifest (counts suppressed, ids, paths)."""

    sections: list[tuple[str, str]] = field(default_factory=list)
    params: dict[str, Any] = field(default_factory=dict)


Runner = Callable[[RunnerContext], RunnerReport]

#: The runner registry: name -> callable (later briefs register theirs here).
RUNNERS: dict[str, Runner] = {}


def register_runner(name: str) -> Callable[[Runner], Runner]:
    """Register ``fn`` under ``name`` (``mwh protocol run --runner <name>``)."""

    def decorate(fn: Runner) -> Runner:
        if name in RUNNERS:
            raise ValueError(f"runner {name!r} is already registered")
        RUNNERS[name] = fn
        return fn

    return decorate


def runner_names() -> tuple[str, ...]:
    return tuple(RUNNERS)


# ---------------------------------------------------------------------------
# cohort_only
# ---------------------------------------------------------------------------


def _cell(value: int | None, k: int) -> int | None:
    """A count as a session-readable manifest may carry it: ``None`` in ``(0, k)``."""
    if value is None:
        return None
    return None if 0 < value < k else value


def _count_text(value: Any, suppressed: Any, banded: Any, k: int) -> str:
    from mimicwarehouse.inventory import fmt_int

    if value is None:
        return f"<{k}" if suppressed else "-"
    text = fmt_int(int(value))
    return f"~{text}" if banded else text


@register_runner(DEFAULT_RUNNER)
def cohort_only(ctx: RunnerContext) -> RunnerReport:
    """Build (or reuse) the protocol's cohort on the tier, record the suppressed
    attrition and describe the cohort (module docstring)."""
    from mimicwarehouse.cohort import build as build_mod
    from mimicwarehouse.cohort import registry as cohort_registry_mod
    from mimicwarehouse.inventory import fmt_int

    settings = ctx.settings
    tier = ctx.tier
    ref = ctx.protocol.cohort
    wanted = ctx.resolved.cohort_hash
    lake_root = settings.lake_root(tier)
    k = settings.k_suppression

    def built_under_hash() -> dict[str, Any] | None:
        attempt = build_mod.cohort_attempt(lake_root, tier, ref) or {}
        mart = build_mod.read_mart_manifest(lake_root, tier, ref)
        if (
            attempt.get("status") == "done"
            and attempt.get("def_hash") == wanted
            and mart is not None
            and mart.get("def_hash") == wanted
            and build_mod.cohort_complete(lake_root, tier, ref)
        ):
            return mart
        return None

    mart = built_under_hash()
    built_now = False
    build_id: str | None = None
    if mart is None:
        from mimicwarehouse.dag import runner as runner_mod
        from mimicwarehouse.dag.spec import DagError, load_dag

        _LOG.info("protocol %s: building cohort %s on %s", ctx.protocol.ref, ref, tier)
        try:
            with build_mod.build_options(select=[ref]):
                result = runner_mod.run(
                    load_dag(),
                    tier,
                    select=[cohort_registry_mod.STEP_SPECS, build_mod.STEP_BUILD, "catalog"],
                    settings=settings,
                    provenance=False,
                )
        except (DagError, runner_mod.BuildLockError) as exc:
            raise ProtocolRunError(f"cohort {ref} could not be built on {tier}: {exc}") from None
        build_id = result.build_id
        if not result.ok:
            failed = "; ".join(
                f"{s.name}: {s.error}" for s in result.steps if s.status in ("failed", "blocked")
            )
            raise ProtocolRunError(f"cohort {ref} failed to build on {tier}: {failed}")
        built_now = True
        mart = built_under_hash()
        if mart is None:
            raise ProtocolRunError(
                f"cohort {ref} is not complete on {tier} under def_hash {wanted[:12]} after "
                "the build"
            )
    for layer, snapshot_id in sorted((mart.get("snapshot_ids") or {}).items()):
        ctx.run.record_snapshot(str(layer), str(snapshot_id))
    cohort_run_id = str(mart.get("run_id") or "") or None
    attrition = build_mod.attrition(ref, tier, settings=settings)
    rows = attrition.df.to_dicts()
    ctx.run.record_attrition(
        [
            {
                "step": str(r["step"]),
                "label": str(r["label"]),
                "n_units": None if r.get("n_units") is None else int(r["n_units"]),
                "n_subjects": None if r.get("n_subjects") is None else int(r["n_subjects"]),
            }
            for r in rows
        ]
    )
    n_rows = int(mart.get("rows") or 0)
    n_subjects = int(mart.get("n_subjects") or 0)
    params = {
        "cohort_ref": ref,
        "cohort_def_hash": wanted,
        "cohort_built_now": built_now,
        "cohort_build_id": build_id,
        "cohort_run_id": cohort_run_id,
        "cohort_sha256": mart.get("cohort_sha256"),
        "cohort_sql_sha256": mart.get("sql_sha256"),
        "cohort_path": mart.get("path") or build_mod.cohort_dir(lake_root, tier, ref).as_posix(),
        "rows": _cell(n_rows, k),
        "rows_suppressed": _cell(n_rows, k) is None and n_rows != 0,
        "n_subjects": _cell(n_subjects, k),
        "n_subjects_suppressed": _cell(n_subjects, k) is None and n_subjects != 0,
        "k": k,
        "attrition_k": attrition.k,
        "attrition_report": dict(attrition.report),
    }

    def count(value: int) -> str:
        cell = _cell(value, k)
        return f"<{k}" if cell is None and value != 0 else fmt_int(value)

    card = "\n".join(
        [
            f"- Cohort `{ref}` (def_hash `{wanted}`), grain `{ctx.resolved.cohort_grain}`, "
            f"tier `{tier}`.",
            f"- {'Built by this run' if built_now else 'Reused the tier build'}; cohort build run "
            f"`{cohort_run_id or '-'}`; cohort file sha256 `{mart.get('cohort_sha256') or '-'}`.",
            f"- Units {count(n_rows)}; subjects {count(n_subjects)} (k = {k}; counts in (0, k) "
            "withheld, banded totals marked `~`).",
        ]
    )
    table = [
        "| step | label | units | subjects |",
        "|---|---|---|---|",
    ]
    for r in rows:
        table.append(
            "| `{step}` | {label} | {units} | {subjects} |".format(
                step=str(r["step"]),
                label=str(r["label"]).replace("|", "\\|"),
                units=_count_text(
                    r.get("n_units"), r.get("n_units_suppressed"), r.get("n_units_banded"), k
                ),
                subjects=_count_text(
                    r.get("n_subjects"),
                    r.get("n_subjects_suppressed"),
                    r.get("n_subjects_banded"),
                    k,
                ),
            )
        )
    report = RunnerReport(params=params)
    report.sections.append(("Cohort", card))
    report.sections.append(
        (
            "Attrition (suppressed)",
            "\n".join(table)
            + f"\n\nRendered from `cohort.build.attrition` after `disclose.suppress` (chain mode, "
            f"k = {attrition.k}); the raw counts stay in the mart under the data root.",
        )
    )
    return report


# ---------------------------------------------------------------------------
# run_protocol and the summary
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RunOutcome:
    """What one protocol run produced: ids, the tier, the runner, the summary path and
    the runner's manifest parameters."""

    run_id: str
    protocol_hash: str
    protocol_ref: str
    claim_type: str
    tier: str
    runner: str
    summary_path: Path
    params: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "protocol_hash": self.protocol_hash,
            "protocol": self.protocol_ref,
            "claim_type": self.claim_type,
            "tier": self.tier,
            "runner": self.runner,
            "summary_path": self.summary_path.as_posix(),
            "params": dict(self.params),
        }


def _ref_rows(line: RegistryLine) -> list[str]:
    rows = ["| kind | reference | hash |", "|---|---|---|"]
    rows.append(f"| protocol | `{line.ref}` | `{line.hash}` |")
    for key, digest in sorted(line.ref_hashes.items()):
        kind, _, ref = key.partition(":")
        rows.append(f"| {kind} | `{ref}` | `{digest}` |")
    return rows


def render_summary(
    run_id: str,
    protocol: Protocol,
    line: RegistryLine,
    report: RunnerReport,
    *,
    tier: str,
    runner: str,
    settings: Settings,
) -> str:
    """The Markdown of ``protocol_summary.md`` (module docstring). ASCII; the run id
    appears inline only (committed-text rule 3 — the file is never committed anyway)."""
    from mimicwarehouse.run import reproduction_block

    plan = protocol.analysis_plan
    holdout = protocol.temporal_holdout
    windows = protocol.feature_windows
    exposure = (
        f"{protocol.exposure.name} - {protocol.exposure.definition.render()}"
        if protocol.exposure
        else "none (descriptive / predictive protocol)"
    )
    horizon = "-" if windows.prediction_h is None else f"{windows.prediction_h:g} h"

    def clause(text: str) -> str:
        return " ".join(text.split()).rstrip(".")

    def listed(items: tuple[str, ...]) -> str:
        return "; ".join(clause(item) for item in items) if items else "none"

    declared = [
        f"- Exposure: {exposure}.",
        "- Outcomes:",
        *[f"  - {o.render()}" for o in protocol.outcomes],
        "- Covariates:" if protocol.covariates else "- Covariates: none.",
        *[f"  - {c.render()}" for c in protocol.covariates],
        f"- Feature windows: observation [{windows.observation.start_h:g}, "
        f"{windows.observation.end_h:g}) h; gap {windows.gap_h:g} h; "
        f"prediction horizon {horizon}.",
        f"- Analysis plan: method family `{plan.method_family}`; estimand: "
        f"{clause(plan.estimand)}; model spec: {clause(plan.model_spec)}; hyperparameters: "
        f"{clause(plan.hyperparameter_policy)}; multiplicity: {clause(plan.multiplicity)}; "
        f"missing data: {clause(plan.missing_data)}; sample size: "
        f"{clause(plan.sample_size_note)}.",
        f"- Subgroups: {listed(plan.subgroups)}.",
        f"- Sensitivity analyses: {listed(plan.sensitivity_analyses)}.",
        "- Temporal holdout: "
        + (
            f"development {', '.join(holdout.development_eras)}; holdout "
            f"{', '.join(holdout.holdout_eras)}; sealed "
            f"{', '.join(holdout.sealed_eras) or 'none'} (declared by anchor_year_group era; "
            "consumed by the temporal-holdout runner, EP-129)"
            if holdout is not None
            else "none declared"
        )
        + ".",
        f"- Seeds policy: {protocol.seeds_policy}.",
    ]
    lines = [
        f"# Protocol run: {protocol.title}",
        "",
        f"Claim type: **{protocol.claim_type}**. Runner `{runner}`, tier `{tier}`, run `{run_id}`.",
        "",
        f"> {protocol.retrospective_statement}",
        "",
        "## Frozen protocol",
        "",
        f"- `{line.ref}` frozen at `{line.hash}` on {line.timestamp_utc} "
        f"(git `{line.git_sha or 'nogit'}`).",
        f"- Amends: {('`' + line.amends + '`') if line.amends else 'nothing (first version)'}"
        + (f" - {line.amendment_reason}" if line.amendment_reason else "")
        + ".",
        f"- Cohort `{line.cohort}` (def_hash `{line.cohort_hash}`), unit of analysis "
        f"`{line.unit_of_analysis}`.",
        "",
        "## Pre-specified analysis (as declared)",
        "",
        *declared,
        "",
        "## References",
        "",
        *_ref_rows(line),
        "",
    ]
    for heading, body in report.sections:
        lines += [f"## {heading}", "", body.rstrip("\n"), ""]
    lines.append(reproduction_block(run_id, settings).rstrip("\n"))
    lines.append("")
    text = "\n".join(lines)
    if not text.isascii():
        text = text.encode("ascii", errors="replace").decode("ascii")
    return text


def summary_path(run_id: str, settings: Settings | None = None) -> Path:
    from mimicwarehouse.run import run_dir

    return run_dir(run_id, settings) / SUMMARY_FILENAME


def run_protocol(
    digest: str,
    *,
    tier: str,
    runner: str = DEFAULT_RUNNER,
    yaml_override: Path | str | None = None,
    settings: Settings | None = None,
    command: str | None = None,
    actor: str | None = None,
) -> RunOutcome:
    """Run a frozen protocol (module docstring). Refusals raise
    :class:`UnknownProtocolError` / :class:`ProtocolRefusedError` (audited); a usage error
    (tier, runner) raises :class:`ProtocolError`; a runner failure raises
    :class:`ProtocolRunError` after the run is marked failed."""
    from mimicwarehouse import run as run_mod

    settings = settings or get_settings()
    started = time.perf_counter()
    if tier not in TIERS:
        raise ProtocolError(f"unknown tier {tier!r}; expected one of {', '.join(TIERS)}")
    if runner not in RUNNERS:
        raise ProtocolError(
            f"unknown runner {runner!r}; expected one of {', '.join(runner_names())}"
        )
    digest = registry_mod.require_hash(digest)
    try:
        line, protocol, resolved = registry_mod.check_frozen(digest, settings)
    except (UnknownProtocolError, ProtocolRefusedError) as exc:
        registry_mod.audit(
            "run",
            digest,
            settings,
            tier=tier,
            allowed=False,
            reason=str(exc),
            actor=actor,
            started=started,
        )
        raise
    if yaml_override is not None:
        override = load_protocol(yaml_override)
        override_hash = registry_mod.content_hash(override, registry_mod.resolve(override))
        if override_hash != digest:
            reason = (
                f"{Path(yaml_override).name} hashes {override_hash[:12]}..., not the frozen "
                f"{digest[:12]}... - freeze it (or amend) before running it, or drop --yaml"
            )
            registry_mod.audit(
                "run",
                digest,
                settings,
                tier=tier,
                allowed=False,
                reason=reason,
                actor=actor,
                started=started,
            )
            raise ProtocolRefusedError(f"refusing to run {line.ref}: {reason}")
    params: dict[str, Any] = {
        "protocol": protocol.ref,
        "protocol_hash": digest,
        "runner": runner,
        "claim_type": protocol.claim_type,
        "cohort": protocol.cohort,
        "cohort_hash": resolved.cohort_hash,
        "unit_of_analysis": protocol.unit_of_analysis,
        "amends": line.amends,
    }
    try:
        with run_mod.start(
            f"protocol {protocol.ref}",
            tier=tier,
            kind=RUN_KIND,
            params=params,
            settings=settings,
            command=command,
            protocol_id=protocol.id,
            protocol_hash=digest,
            claim_type=protocol.claim_type,
        ) as r:
            r.record_ref("protocol", protocol.id, version=protocol.version, hash=digest)
            for kind, mapping in resolved.hashes.items():
                for ref, ref_hash in sorted(mapping.items()):
                    if kind == "concept":
                        r.record_ref(kind, ref, hash=ref_hash)
                    else:
                        ref_id, ref_version = ref.split("@", 1)
                        r.record_ref(kind, ref_id, version=ref_version, hash=ref_hash)
            report = RUNNERS[runner](
                RunnerContext(
                    run=r,
                    protocol=protocol,
                    line=line,
                    resolved=resolved,
                    tier=tier,
                    settings=settings,
                )
            )
            r.manifest.params = {**params, **report.params}
            run_id = r.run_id
    except ProtocolRunError:
        registry_mod.audit(
            "run",
            digest,
            settings,
            tier=tier,
            allowed=True,
            reason="runner failed",
            actor=actor,
            started=started,
        )
        raise
    target = summary_path(run_id, settings)
    text = render_summary(
        run_id, protocol, line, report, tier=tier, runner=runner, settings=settings
    )
    from mimicwarehouse import fsio

    fsio.atomic_write_text(target, text)
    registry_mod.audit(
        "run", digest, settings, tier=tier, allowed=True, actor=actor, started=started
    )
    _LOG.info(
        "protocol %s (%s): run %s ok via %s - summary %s",
        protocol.ref,
        tier,
        run_id,
        runner,
        target,
    )
    return RunOutcome(
        run_id=run_id,
        protocol_hash=digest,
        protocol_ref=protocol.ref,
        claim_type=protocol.claim_type,
        tier=tier,
        runner=runner,
        summary_path=target,
        params=dict(report.params),
    )


__all__ = [
    "DEFAULT_RUNNER",
    "RUNNERS",
    "RUN_KIND",
    "SUMMARY_FILENAME",
    "TIERS",
    "ProtocolRunError",
    "RunOutcome",
    "Runner",
    "RunnerContext",
    "RunnerReport",
    "cohort_only",
    "register_runner",
    "render_summary",
    "run_protocol",
    "runner_names",
    "summary_path",
]
