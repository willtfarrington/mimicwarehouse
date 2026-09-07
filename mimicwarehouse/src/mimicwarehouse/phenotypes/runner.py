"""Phenotype materialisation — the ``phenotypes.compile`` DAG step, ``meta.phenotype_versions``,
the catalog extension, the prevalence summary and the docs renderer (EP-41 items 2 and 4;
DESIGN §3/§8/§15; D-19/D-20/D-33).

:func:`run_compile` (``python`` step ``phenotypes.compile``, ``dag/specs/phenotypes.yaml``)
    Loads the registry (packaged definitions + the option's study directories, resolved
    against the code-set registry — a frozen pair fails the step before anything is
    written), exposes the tier's staged tables as views on the build connection
    (:func:`concepts.runner.ensure_source_views`), and for every selected phenotype whose
    ``(id, version, def_hash)`` is not already complete for the tier (or when forced)
    compiles the statement (:mod:`.compiler`), ``COPY``s it to one ZSTD Parquet file
    published by :func:`publish.swap_dir` under the **per-tier derived layout**
    ``<lake_root>/derived/<tier>/phenotypes/<id>@<version>/part-0.parquet`` (EP-37's
    convention; every built version keeps its own directory, so versions coexist), appends
    the manifest line (``source_sha256`` = the SQL's sha256, ``raw_snapshot_id`` = the core
    snapshot id), writes the ``status.json`` entry ``phenotypes.<id>@<version>``
    (``per_tier``, a ``tiers[<tier>]`` attempt with def_hash, refs, rows, n_positive, run
    and build ids) and records the build inside **one ``kind: phenotype`` run per
    phenotype** (:func:`run.start`, EP-35: the SQL under ``sql/phenotype.sql``, the
    phenotype and every code-set reference as refs with hashes, the core snapshot id, a
    ``kind: phenotype`` benchmark line). Finally it writes
    ``lake/meta/<tier>/phenotype_versions.parquet`` from the status entries — one row per
    version attempted on the tier — which EP-37's discovery walker registers as
    ``meta.phenotype_versions``; the walker also registers every built version as the view
    ``phenotypes."<id>@<version>"``. A DuckDB failure leaves the previous file intact,
    records ``status: failed`` + the error class and re-raises a sanitized
    :class:`~mimicwarehouse.phenotypes.spec.PhenotypeError`.

    **Concept leaves (EP-42).** Before compiling, every ``mimiciv_derived.*`` concept a
    leaf reads must be complete for the tier (:func:`concepts.runner.ensure_derived_view`;
    :class:`ConceptNotBuiltError` otherwise) **and** built from the SQL the phenotype
    pins (:class:`~mimicwarehouse.phenotypes.registry.ConceptPin` vs the concept's status
    entry — the executed sha256, patch included; :class:`ConceptPinMismatchError` names
    the rebuild). The step attempts **every** selected phenotype, records each failure
    as ``status: failed`` + the error class, writes ``meta.phenotype_versions`` (so failed
    and blocked rows are visible) and only then raises the summary of failures.

:func:`register_phenotypes` (``CATALOG_EXTENSIONS`` entry, after the walker, before ``units``)
    ``mimiciv_derived.phenotype_<id>`` = the **latest built version** of each id (semver
    order) over the walker's ``phenotypes.*`` view, plus the per-admission companion
    ``mimiciv_derived.phenotype_<id>_hadm`` for subject-grain phenotypes
    (:func:`compiler.hadm_companion_sql`: every admission, prevalent by discharge) and for
    icustay-grain phenotypes (:func:`compiler.icustay_hadm_companion_sql`: admissions
    with >= 1 ICU stay, flagged when any stay is; EP-42), and the comment on
    ``meta.phenotype_versions``. Sessions read the ``mimiciv_derived`` views through
    ``safe_query`` (subject-keyed, non-registry reads: the ``phenotypes`` schema itself is
    not on the allow list).

:func:`summarize` / :func:`summary` / :func:`distribution` / :func:`agreement` /
:func:`prevalence_report`
    The prevalence helpers: ``n_units`` / ``n_positive`` (+ ``share``) for the phenotype's
    grain, then by era through ``mimiciv_derived.hadm_era`` (the ``_hadm`` companion for
    subject-grain phenotypes); the distribution of a typed evidence column with declared
    ``levels`` (the KDIGO stages); the 2x2 agreement of two phenotypes per admission
    (their ``hadm``-grain relations; EP-42) — every number read through ``safe_query``
    (k = 11 row-wise suppression on the credentialed tiers, audited; through
    ``Run.safe_query`` when a run is open so the SQL and audit ids are recorded).
    :func:`prevalence_report` bundles them for ``mwh phenotype summary``, and
    :func:`write_prevalence_report` renders ``phenotype_prevalence.md`` (claim type
    exploratory, the retrospective statement, a "disclosure sidecar pending EP-43" line)
    into a run folder — the artefact EP-43 checks retroactively and EP-53 promotes.

Small cells (D-33): ``n_positive`` values in ``1 .. k-1`` are blanked in every surface a
session can read — ``meta.phenotype_versions``, the run manifest's params and the progress
log — while ``status.json`` (data root only, read by code) keeps the raw count.
Everything written, logged or returned is DDL, paths, hashes, counts and timings — never a
row (GOVERNANCE §4). Import budget: not on the ``mwh`` start-up path; duckdb, polars,
``run``, ``safe`` and the runner modules load inside the functions.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mimicwarehouse import fsio, publish
from mimicwarehouse.concepts.runner import (
    DERIVED_LAYER,
    PART,
    derived_part,
    derived_table_dir,
)
from mimicwarehouse.dag.snapshot import complete_for_tier, layer_snapshot
from mimicwarehouse.loader.manifest import (
    ManifestLine,
    append_manifest,
    lake_relative_posix,
    read_status,
    sha256_streamed,
    update_status,
    utc_now_iso,
    writer_version,
)
from mimicwarehouse.phenotypes.compiler import (
    OUTPUT_COLUMNS,
    Compiled,
    compile_phenotype,
    hadm_companion_sql,
    icustay_hadm_companion_sql,
    sql_str,
)
from mimicwarehouse.phenotypes.registry import Entry, Registry, load_registry
from mimicwarehouse.phenotypes.spec import (
    RESERVED_COLUMNS,
    Leaf,
    ParamValue,
    PhenotypeError,
    UnknownPhenotypeError,
)

if TYPE_CHECKING:  # pragma: no cover
    import duckdb
    import polars

    from mimicwarehouse.config import Settings
    from mimicwarehouse.dag.runner import StepContext, StepOutcome
    from mimicwarehouse.dag.spec import Step
    from mimicwarehouse.run import Run
    from mimicwarehouse.safe import SafeResult

_LOG = logging.getLogger(__name__)


class ConceptNotBuiltError(PhenotypeError):
    """A concept leaf's table is not complete for the tier (build the concept first)."""


class ConceptPinMismatchError(PhenotypeError):
    """The tier's concept was built from different SQL than the phenotype pins (EP-42)."""


#: The DAG step / tag (``dag/specs/phenotypes.yaml``).
STEP_COMPILE = "phenotypes.compile"
DAG_TAG = "phenotypes"
#: The schema directory under ``derived/<tier>/`` and the catalog schema of the versioned
#: views the walker registers (``phenotypes."<id>@<version>"``).
SCHEMA = "phenotypes"
#: The session-facing views: ``mimiciv_derived.phenotype_<id>`` (+ ``_hadm``).
DERIVED_SCHEMA = "mimiciv_derived"
VIEW_PREFIX = "phenotype_"
HADM_SUFFIX = "_hadm"
#: ``meta.phenotype_versions`` <- ``lake/meta/<tier>/phenotype_versions.parquet``.
VERSIONS_TABLE = "phenotype_versions"
#: Benchmark-ledger / run-ledger kind of the per-phenotype lines.
BENCH_KIND = "phenotype"
#: The free-text ceiling ``safe_query`` applies to a subject-keyed read; mirrors
#: ``safe.FREE_TEXT_MAX_CHARS`` (asserted equal by ``test_ep41``; ``safe`` is not imported
#: at module level on purpose — the catalog builder imports this module).
EVIDENCE_MAX_CHARS = 64
#: The three-way suppression sentinel a session may see instead of a small count.
SMALL_CELL = "<k"
#: The prevalence artefact ``mwh phenotype summary --report`` writes into a run folder.
PREVALENCE_REPORT = "phenotype_prevalence.md"
CLAIM_TYPE = "exploratory"
RETROSPECTIVE_SENTENCE = "MIMIC-IV analyses are retrospective."
#: The header line's disclosure sentence (the name dates from EP-42, when the sidecar was
#: pending; since EP-43 it names the gate the file must pass before promotion).
SIDECAR_PENDING = "Disclosure: run `mwh disclose check --write-sidecar` before promoting this file"

_COLUMN_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def _sql_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _parquet_ref(path: Path) -> str:
    return f"read_parquet({sql_str(path.resolve().as_posix())})"


def _cell(value: int | None, k: int) -> int | None:
    """``value`` unless it is a small cell (``1 .. k-1``), then None (D-33)."""
    if value is None:
        return None
    return None if 0 < value < k else value


def _cell_text(value: int | None, k: int) -> str:
    if value is None:
        return "-"
    return SMALL_CELL if 0 < value < k else f"{value:,}"


def semver_key(version: str) -> tuple[int, ...]:
    return tuple(int(p) for p in version.split("."))


# ---------------------------------------------------------------------------
# Compile options (the CLI -> step channel) and the layout
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CompileOptions:
    """What ``mwh phenotype compile`` hands the step: an ``id@version`` selection (empty =
    every registered phenotype), extra definition / code-set directories, ``force``."""

    select: tuple[str, ...] = ()
    extra_dirs: tuple[Path, ...] = ()
    codeset_dirs: tuple[Path, ...] = ()
    force: bool = False


_OPTIONS: ContextVar[CompileOptions | None] = ContextVar("mwh_phenotypes_compile", default=None)


@contextmanager
def compile_options(
    *,
    select: Iterable[str] = (),
    extra_dirs: Iterable[Path | str] = (),
    codeset_dirs: Iterable[Path | str] = (),
    force: bool = False,
) -> Iterator[CompileOptions]:
    """Install :class:`CompileOptions` for the duration of a runner call in this thread."""
    options = CompileOptions(
        select=tuple(select),
        extra_dirs=tuple(Path(d) for d in extra_dirs),
        codeset_dirs=tuple(Path(d) for d in codeset_dirs),
        force=force,
    )
    token = _OPTIONS.set(options)
    try:
        yield options
    finally:
        _OPTIONS.reset(token)


def current_options() -> CompileOptions:
    """The options installed by :func:`compile_options`, or the defaults."""
    return _OPTIONS.get() or CompileOptions()


def status_key(ref: str) -> str:
    """The ``status.json`` key of one built version: ``phenotypes.<id>@<version>``."""
    return f"{SCHEMA}.{ref}"


def phenotype_dir(lake_root: Path | str, tier: str, ref: str) -> Path:
    """``<lake_root>/derived/<tier>/phenotypes/<id>@<version>``."""
    return derived_table_dir(lake_root, tier, SCHEMA, ref)


def phenotype_part(lake_root: Path | str, tier: str, ref: str) -> Path:
    return derived_part(lake_root, tier, SCHEMA, ref)


def versions_path(lake_root: Path | str, tier: str) -> Path:
    from mimicwarehouse.units import meta_table_path

    return meta_table_path(lake_root, tier, VERSIONS_TABLE)


def phenotype_entry(lake_root: Path | str, ref: str) -> dict[str, Any] | None:
    return read_status(Path(lake_root))["steps"].get(status_key(ref))


def phenotype_attempt(lake_root: Path | str, tier: str, ref: str) -> dict[str, Any] | None:
    """The tier's last attempt of ``ref`` (the ``tiers[<tier>]`` sub-entry), or None."""
    entry = phenotype_entry(lake_root, ref)
    if entry is None:
        return None
    attempt = (entry.get("tiers") or {}).get(tier)
    return dict(attempt) if attempt else None


def phenotype_complete(lake_root: Path | str, tier: str, ref: str) -> bool:
    """Complete for ``tier`` per :func:`complete_for_tier` **and** the tier's file exists."""
    entry = phenotype_entry(lake_root, ref)
    if entry is None or not complete_for_tier(entry, tier):
        return False
    return phenotype_part(lake_root, tier, ref).is_file()


def _record_status(
    ctx: StepContext,
    entry: Entry,
    *,
    status: str,
    rows: int | None = None,
    n_positive: int | None = None,
    nbytes: int | None = None,
    error_class: str | None = None,
    sql_sha256: str | None = None,
    run_id: str | None = None,
) -> None:
    """Merge this attempt into the per-tier status entry (module docstring)."""
    key = status_key(entry.ref)
    current = phenotype_entry(ctx.lake_root, entry.ref) or {}
    tiers = dict(current.get("tiers") or {})
    tiers[ctx.tier] = {
        "status": status,
        "build_id": ctx.build_id,
        "run_id": run_id,
        "rows": rows,
        "n_positive": n_positive,
        "bytes": nbytes,
        "files": 1 if status == "done" else None,
        "finished_at": utc_now_iso(),
        "error_class": error_class,
        "sql_sha256": sql_sha256,
        "def_hash": entry.def_hash,
        "grain": entry.phenotype.grain,
        "refs": dict(entry.resolved),
        "concepts": entry.concept_hashes,
    }
    fields: dict[str, Any] = {
        "per_tier": True,
        "layer": DERIVED_LAYER,
        "tiers": tiers,
        "phenotype_id": entry.phenotype.id,
        "version": entry.phenotype.version,
    }
    if status == "done":
        if ctx.tier == "dev":
            fields["dev_ready"] = True
            if current.get("tier_complete") != "full":
                fields["tier_complete"] = "dev"
        else:  # fixture / demo (own lake roots) and full
            fields["tier_complete"] = "full"
    update_status(ctx.lake_root, key, **fields)


# ---------------------------------------------------------------------------
# Materialisation
# ---------------------------------------------------------------------------


def _schema_hash(columns: list[tuple[str, str]]) -> str:
    blob = json.dumps([[n, t] for n, t in columns], separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()


def _core_snapshot_id(ctx: StepContext) -> str:
    key = "phenotypes.core_snapshot_id"
    if key not in ctx.state:
        ctx.state[key] = layer_snapshot(ctx.lake_root, "core", ctx.tier, settings=ctx.settings)
    return str(ctx.state[key])


@dataclass(frozen=True, slots=True)
class BuildOutcome:
    """What one materialisation produced (counts, ids and paths only)."""

    ref: str
    rows: int
    n_positive: int
    nbytes: int
    sql_sha256: str
    run_id: str | None
    max_evidence_chars: int


def _materialize(ctx: StepContext, entry: Entry, compiled: Compiled) -> BuildOutcome:
    """COPY the compiled statement into the tier's phenotype directory inside a
    ``kind: phenotype`` run (module docstring)."""
    import duckdb

    from mimicwarehouse import run as run_mod
    from mimicwarehouse.run import ResourceLog

    phenotype = entry.phenotype
    sql_sha = hashlib.sha256(compiled.sql.encode("utf-8")).hexdigest()
    dest = phenotype_dir(ctx.lake_root, ctx.tier, entry.ref)
    new = publish.new_path_for(dest)
    if new.exists():
        publish.rmtree(new)
    new.mkdir(parents=True, exist_ok=True)
    part_new = new / PART
    ref_sql = _parquet_ref(part_new)
    k = ctx.settings.k_suppression
    evidence_length = "length(evidence_json)" if "evidence_json" in compiled.columns else "0"

    def work() -> tuple[int, int, int, list[tuple[str, str]]]:
        ctx.con.execute(
            f"COPY ({compiled.sql}) TO {sql_str(part_new.resolve().as_posix())} "
            "(FORMAT PARQUET, COMPRESSION ZSTD)"
        )
        counts = ctx.con.execute(
            "SELECT count(*), count(*) FILTER (WHERE flag), "
            f"coalesce(max({evidence_length}), 0) FROM {ref_sql}"
        ).fetchone()
        described = ctx.con.execute(f"DESCRIBE SELECT * FROM {ref_sql}").fetchall()
        assert counts is not None
        return (
            int(counts[0]),
            int(counts[1]),
            int(counts[2]),
            [(str(r[0]), str(r[1])) for r in described],
        )

    params = {
        "ref": entry.ref,
        "phenotype_id": phenotype.id,
        "version": phenotype.version,
        "grain": phenotype.grain,
        "def_hash": entry.def_hash,
        "build_id": ctx.build_id,
        "tier": ctx.tier,
    }
    with run_mod.start(
        f"phenotype {entry.ref}",
        tier=ctx.tier,
        kind=BENCH_KIND,
        params=params,
        settings=ctx.settings,
    ) as prun:
        prun.record_sql("phenotype", compiled.sql)
        prun.record_ref("phenotype", phenotype.id, version=phenotype.version, hash=entry.def_hash)
        for ref, def_hash in sorted(entry.resolved.items()):
            cs_id, cs_version = ref.split("@", 1)
            prun.record_ref("codeset", cs_id, version=cs_version, hash=def_hash)
        for _table, pin in sorted(entry.concepts.items()):
            # the same shape the concept runner records (EP-37/38): the executed SQL's
            # sha256 under `concept`, the patch under `concept_patch` when one applies
            prun.record_ref(
                "concept", pin.name, version=pin.upstream_commit, hash=pin.executed_sha256
            )
            if pin.patch_id is not None:
                prun.record_ref(
                    "concept_patch",
                    pin.patch_id,
                    version=pin.upstream_commit,
                    hash=pin.executed_sha256,
                )
        prun.record_snapshot("core", _core_snapshot_id(ctx))
        try:
            (rows, n_positive, max_evidence, columns), usage = ResourceLog.measure(
                work, data_root=ctx.settings.data_root
            )
        except duckdb.Error as exc:
            from mimicwarehouse.safe import sanitize_error_text

            publish.rmtree(new)
            error_class = type(exc).__name__
            _record_status(
                ctx,
                entry,
                status="failed",
                error_class=error_class,
                sql_sha256=sql_sha,
                run_id=prun.run_id,
            )
            prun.bench(
                BENCH_KIND,
                entry.ref,
                wall_s=0.0,
                ok=False,
                error=error_class,
                build_id=ctx.build_id,
            )
            raise PhenotypeError(
                f"{entry.ref}: {error_class}: {sanitize_error_text(str(exc))}"
            ) from None
        publish.swap_dir(new, dest)
        part = dest / PART
        nbytes = part.stat().st_size
        line = ManifestLine(
            schema=SCHEMA,
            table=entry.ref,
            path=lake_relative_posix(part, ctx.lake_root),
            sha256=sha256_streamed(part),
            bytes=nbytes,
            rows=rows,
            schema_hash=_schema_hash(columns),
            writer_version=writer_version(),
            source_sha256=sql_sha,
            raw_snapshot_id=_core_snapshot_id(ctx),
            build_id=ctx.build_id,
            ts=utc_now_iso(),
        )
        append_manifest(ctx.lake_root, ctx.build_id, [line])
        _record_status(
            ctx,
            entry,
            status="done",
            rows=rows,
            n_positive=n_positive,
            nbytes=nbytes,
            sql_sha256=sql_sha,
            run_id=prun.run_id,
        )
        prun.manifest.params = {
            **params,
            "rows": rows,
            "n_positive": _cell(n_positive, k),
            "n_positive_suppressed": _cell(n_positive, k) is None and n_positive != 0,
            "k": k,
            "sql_sha256": sql_sha,
        }
        prun.bench(
            BENCH_KIND,
            entry.ref,
            wall_s=usage.wall_s,
            peak_rss_mb=usage.peak_rss_mb,
            disk_delta_mb=usage.disk_delta_mb,
            rows=rows,
            bytes_out=nbytes,
            files=1,
            build_id=ctx.build_id,
        )
        if max_evidence > EVIDENCE_MAX_CHARS:
            prun.warn(
                f"evidence_json reaches {max_evidence} characters (> {EVIDENCE_MAX_CHARS}): a "
                "session selecting it through safe_query will be refused — shorten the leaf ids"
            )
        run_id = prun.run_id
    if ctx.run is not None:
        ctx.run.record_ref(
            "phenotype", phenotype.id, version=phenotype.version, hash=entry.def_hash
        )
    _LOG.info(
        "phenotype %s (%s, grain %s): %s units, %s positive, %s bytes, wall=%.2fs, run %s",
        entry.ref,
        ctx.tier,
        phenotype.grain,
        f"{rows:,}",
        _cell_text(n_positive, k),
        f"{nbytes:,}",
        usage.wall_s,
        run_id,
    )
    return BuildOutcome(
        ref=entry.ref,
        rows=rows,
        n_positive=n_positive,
        nbytes=nbytes,
        sql_sha256=sql_sha,
        run_id=run_id,
        max_evidence_chars=max_evidence,
    )


def _ensure_concepts(ctx: StepContext, entry: Entry) -> None:
    """Expose the ``mimiciv_derived`` concept tables a phenotype's concept leaves read as
    views on the build connection (:func:`concepts.runner.ensure_derived_view`) and check
    every pinned concept against the tier's build: the status entry's executed-SQL
    sha256 must equal the pin's (EP-42 — the phenotype hash pins the concept build)."""
    from mimicwarehouse.concepts.runner import ConceptError, concept_entry, ensure_derived_view

    for table in entry.phenotype.concept_tables:
        schema, _, name = table.partition(".")
        if schema != DERIVED_SCHEMA:
            continue
        try:
            ensure_derived_view(ctx, name)
        except ConceptError as exc:
            raise ConceptNotBuiltError(f"{entry.ref}: {exc}") from None
        pin = entry.concepts.get(table)
        if pin is None:
            continue
        attempt = ((concept_entry(ctx.lake_root, name) or {}).get("tiers") or {}).get(ctx.tier)
        built = str((attempt or {}).get("sql_sha256") or "")
        if built and built != pin.executed_sha256:
            built_patch = (attempt or {}).get("patch_id") or "none"
            raise ConceptPinMismatchError(
                f"{entry.ref}: {table} on tier {ctx.tier} was built from SQL {built[:12]} "
                f"(patch {built_patch}) but the phenotype pins {pin.executed_sha256[:12]} "
                f"(patch {pin.patch_id or 'none'}) — rebuild the concept "
                f"(`mwh build --tier {ctx.tier} --select {pin.step} --force`) or bump the "
                "phenotype version against the current concept"
            )


def run_compile(step: Step, ctx: StepContext) -> StepOutcome:
    """The ``phenotypes.compile`` handler (module docstring)."""
    from mimicwarehouse.concepts.runner import ensure_source_views
    from mimicwarehouse.dag.runner import StepOutcome
    from mimicwarehouse.units import write_meta_parquet

    options = current_options()
    registry = load_registry(options.extra_dirs, codeset_dirs=options.codeset_dirs)
    selected = registry.select(options.select) if options.select else registry
    ensure_source_views(ctx)
    total_rows = 0
    total_bytes = 0
    built: list[str] = []
    skipped: list[str] = []
    failures: list[str] = []
    for entry in selected:
        attempt = phenotype_attempt(ctx.lake_root, ctx.tier, entry.ref)
        if (
            not options.force
            and attempt is not None
            and attempt.get("status") == "done"
            and attempt.get("def_hash") == entry.def_hash
            and phenotype_complete(ctx.lake_root, ctx.tier, entry.ref)
        ):
            _LOG.info(
                "phenotype %s: already built for %s with def_hash %s — skipped (--force rebuilds)",
                entry.ref,
                ctx.tier,
                entry.def_hash[:12],
            )
            skipped.append(entry.ref)
            continue
        try:
            _ensure_concepts(ctx, entry)
            compiled = compile_phenotype(
                entry.phenotype,
                registry.codesets,
                resolved=entry.resolved,
                concepts=entry.concept_hashes,
            )
        except PhenotypeError as exc:
            # a missing / mismatched concept or a compile error: recorded like a DuckDB
            # failure so meta.phenotype_versions shows it; the other phenotypes still run
            _record_status(ctx, entry, status="failed", error_class=type(exc).__name__)
            _LOG.error("phenotype %s (%s): %s", entry.ref, ctx.tier, exc)
            failures.append(f"{entry.ref}: {exc}")
            continue
        try:
            outcome = _materialize(ctx, entry, compiled)
        except PhenotypeError as exc:  # status + ok:false line already recorded
            _LOG.error("phenotype %s (%s): %s", entry.ref, ctx.tier, exc)
            failures.append(f"{entry.ref}: {exc}")
            continue
        total_rows += outcome.rows
        total_bytes += outcome.nbytes
        built.append(entry.ref)
    snapshot_id = layer_snapshot(ctx.lake_root, DERIVED_LAYER, ctx.tier, settings=ctx.settings)
    rows = versions_rows(
        ctx.lake_root,
        ctx.tier,
        k=ctx.settings.k_suppression,
        settings=ctx.settings,
        snapshot_id=snapshot_id,
    )
    dest = versions_path(ctx.lake_root, ctx.tier)
    meta_bytes = write_meta_parquet(ctx.con, dest, VERSIONS_COLUMNS, rows)
    if rows:
        # the stamped derived id joins the snapshot history (dag.snapshot docstring)
        from mimicwarehouse.dag.snapshot import record_snapshot_once

        record_snapshot_once(
            ctx.lake_root,
            layer=DERIVED_LAYER,
            tier=ctx.tier,
            snapshot_id=snapshot_id,
            build_id=ctx.build_id,
        )
    _LOG.info(
        "meta.phenotype_versions (%s): %d version(s) attempted on the tier, %d built now, "
        "%d skipped, %d failed — %s",
        ctx.tier,
        len(rows),
        len(built),
        len(skipped),
        len(failures),
        dest,
    )
    if failures:
        raise PhenotypeError(
            f"{len(failures)} of {len(selected)} phenotype(s) failed on tier {ctx.tier}: "
            + " | ".join(failures)
        )
    return StepOutcome(
        rows=total_rows,
        bytes_out=total_bytes + meta_bytes,
        files=len(built) + 1,
        layer=DERIVED_LAYER,
    )


def concept_steps(registry: Registry | None = None) -> tuple[str, ...]:
    """The DAG steps that build every concept the **packaged** definitions pin — the
    ``depends_on`` the ``phenotypes.compile`` step carries (``dag/specs/phenotypes.yaml``)
    so a full build orders the concepts first; ``test_ep42`` asserts the two agree."""
    reg = registry if registry is not None else load_registry()
    return tuple(sorted({pin.step for e in reg if e.packaged for pin in e.concepts.values()}))


# ---------------------------------------------------------------------------
# meta.phenotype_versions
# ---------------------------------------------------------------------------

VERSIONS_COLUMNS: tuple[tuple[str, str], ...] = (
    ("phenotype_id", "VARCHAR"),
    ("version", "VARCHAR"),
    ("def_hash", "VARCHAR"),
    ("grain", "VARCHAR"),
    ("refs", "VARCHAR"),
    ("rows", "BIGINT"),
    ("n_positive", "BIGINT"),
    ("n_positive_suppressed", "BOOLEAN"),
    ("k", "INTEGER"),
    ("built_at", "VARCHAR"),
    ("run_id", "VARCHAR"),
    ("build_id", "VARCHAR"),
    ("sql_sha256", "VARCHAR"),
    ("snapshot_id", "VARCHAR"),
    ("status", "VARCHAR"),
    ("error_class", "VARCHAR"),
    ("tier", "VARCHAR"),
    ("concept_refs", "VARCHAR"),
)


def versions_rows(
    lake_root: Path | str,
    tier: str,
    *,
    k: int,
    settings: Settings | None = None,
    snapshot_id: str | None = None,
) -> list[list[Any]]:
    """The ``meta.phenotype_versions`` rows for ``tier`` from ``status.json``: one per
    ``phenotypes.<id>@<version>`` entry attempted on the tier (id, then semver order);
    ``n_positive`` blanked (``n_positive_suppressed = true``) below ``k`` (D-33);
    ``snapshot_id`` = the derived layer's current id unless given."""
    status = read_status(Path(lake_root))["steps"]
    if snapshot_id is None:
        snapshot_id = layer_snapshot(Path(lake_root), DERIVED_LAYER, tier, settings=settings)
    rows: list[list[Any]] = []
    prefix = f"{SCHEMA}."
    entries = sorted(
        ((key, entry) for key, entry in status.items() if key.startswith(prefix)),
        key=lambda kv: (
            str(kv[1].get("phenotype_id", "")),
            semver_key(str(kv[1].get("version", "0.0.0"))),
        ),
    )
    for key, entry in entries:
        attempt = (entry.get("tiers") or {}).get(tier)
        if not attempt:
            continue
        ref = key[len(prefix) :]
        phenotype_id, _, version = ref.partition("@")
        n_positive = attempt.get("n_positive")
        rows.append(
            [
                str(entry.get("phenotype_id") or phenotype_id),
                str(entry.get("version") or version),
                attempt.get("def_hash"),
                attempt.get("grain"),
                json.dumps(attempt.get("refs") or {}, sort_keys=True, separators=(",", ":")),
                attempt.get("rows"),
                _cell(n_positive, k),
                n_positive is not None and _cell(n_positive, k) is None and n_positive != 0,
                k,
                attempt.get("finished_at"),
                attempt.get("run_id"),
                attempt.get("build_id"),
                attempt.get("sql_sha256"),
                snapshot_id,
                attempt.get("status"),
                attempt.get("error_class"),
                tier,
                json.dumps(attempt.get("concepts") or {}, sort_keys=True, separators=(",", ":")),
            ]
        )
    return rows


# ---------------------------------------------------------------------------
# Catalog extension: mimiciv_derived.phenotype_<id> (+ _hadm), the meta comment
# ---------------------------------------------------------------------------

_VERSIONS_COMMENT = (
    "One row per phenotype version attempted on this tier (phenotypes/runner.py, EP-41): "
    "def_hash (sha256 of grain + criteria + onset + the referenced code-set hashes, plus "
    "parameters and the pinned concept hashes since EP-42), grain, refs (JSON of "
    "id@version -> def_hash), concept_refs (JSON of mimiciv_derived.<concept> -> executed "
    "SQL sha256), rows, n_positive (NULL below k, n_positive_suppressed), built_at, "
    "run_id / build_id, sql_sha256, the derived snapshot id, status, error_class. The "
    "latest built version of each id is exposed as mimiciv_derived.phenotype_<id> "
    "(+ phenotype_<id>_hadm for subject- and icustay-grain phenotypes)."
)


def latest_versions(con: duckdb.DuckDBPyConnection) -> dict[str, str]:
    """``{id: version}`` of the latest built version per phenotype id among the
    ``phenotypes."<id>@<version>"`` views of an open catalog connection."""
    rows = con.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = ?", [SCHEMA]
    ).fetchall()
    latest: dict[str, str] = {}
    for (name,) in rows:
        phenotype_id, sep, version = str(name).partition("@")
        if not sep:
            continue
        try:
            key = semver_key(version)
        except ValueError:
            continue
        if phenotype_id not in latest or key > semver_key(latest[phenotype_id]):
            latest[phenotype_id] = version
    return latest


def register_phenotypes(con: duckdb.DuckDBPyConnection, tier: str) -> None:
    """The catalog extension (module docstring). DDL only; never opens a connection."""
    latest = latest_versions(con)
    present = {
        str(r[0])
        for r in con.execute(
            "SELECT table_schema || '.' || table_name FROM information_schema.tables"
        ).fetchall()
    }
    views = 0
    companions = 0
    for phenotype_id, version in sorted(latest.items()):
        ref = f"{phenotype_id}@{version}"
        source = f"{SCHEMA}.{_sql_ident(ref)}"
        view = f"{DERIVED_SCHEMA}.{_sql_ident(VIEW_PREFIX + phenotype_id)}"
        columns = [str(r[0]) for r in con.execute(f"DESCRIBE {source}").fetchall()]
        grain = (
            "icustay" if "stay_id" in columns else ("hadm" if "hadm_id" in columns else "subject")
        )
        con.execute(f"CREATE OR REPLACE VIEW {view} AS SELECT * FROM {source}")
        key_columns = {"subject_id", "hadm_id", "stay_id"}
        extra = [c for c in columns if c not in key_columns and c not in OUTPUT_COLUMNS]
        evidence_note = (
            f"; typed evidence columns {', '.join(extra)} (EP-42: per-leaf aggregates of the "
            "concept columns)"
            if extra
            else ""
        )
        con.execute(
            f"COMMENT ON VIEW {view} IS "
            + sql_str(
                f"Phenotype {ref} (grain {grain}; the latest built version on tier {tier}; "
                f"EP-41 engine): {', '.join(c for c in columns if c in OUTPUT_COLUMNS)} per "
                "unit — flag = the boolean criteria tree, onset_time = the onset rule over "
                f"the positive leaves, evidence_json = per-leaf event counts{evidence_note}. "
                f'Every built version lives under {SCHEMA}."<id>@<version>"; '
                "meta.phenotype_versions carries the hashes. Subject-keyed: read through "
                "safe_query as aggregates."
            )
        )
        views += 1
        if grain in ("subject", "icustay") and "mimiciv_hosp.admissions" in present:
            companion = f"{DERIVED_SCHEMA}.{_sql_ident(VIEW_PREFIX + phenotype_id + HADM_SUFFIX)}"
            has_onset = "onset_time" in columns
            if grain == "subject":
                body = hadm_companion_sql(source, has_onset=has_onset)
                comment = (
                    f"Per-admission companion of phenotype {ref} (EP-41): every admission, "
                    "flag = the subject's onset lies at or before this dischtime (prevalent "
                    "by discharge), onset_time carried over. Join hadm_era for the era axis."
                )
            else:
                body = icustay_hadm_companion_sql(source, has_onset=has_onset)
                comment = (
                    f"Per-admission companion of phenotype {ref} (EP-42): the admissions "
                    "with at least one ICU stay, flag = any of the admission's stays is "
                    "flagged, onset_time = the earliest flagged onset, n_stays = its ICU "
                    "stays. Join hadm_era for the era axis; join a hadm-grain phenotype for "
                    "an agreement cross-tab."
                )
            con.execute(f"CREATE OR REPLACE VIEW {companion} AS " + body)
            con.execute(f"COMMENT ON VIEW {companion} IS " + sql_str(comment))
            companions += 1
    if f"meta.{VERSIONS_TABLE}" in present:
        con.execute(
            f"COMMENT ON TABLE meta.{_sql_ident(VERSIONS_TABLE)} IS {sql_str(_VERSIONS_COMMENT)}"
        )
    _LOG.info(
        "catalog extension phenotypes (%s): %d latest-version view(s), %d hadm companion(s)",
        tier,
        views,
        companions,
    )


# ---------------------------------------------------------------------------
# Prevalence summary (through safe_query)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Summary:
    """``mwh phenotype summary``'s result: the frame (``scope``, ``unit``, ``n_units``,
    ``n_positive``, ``share``), the version actually read, ``k``, suppressed row counts,
    the audit ids of the reads and the catalog's core snapshot id."""

    ref: str
    tier: str
    grain: str
    k: int
    df: polars.DataFrame
    rows_suppressed: int
    audit_ids: tuple[str, ...]
    snapshot_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ref": self.ref,
            "tier": self.tier,
            "grain": self.grain,
            "k": self.k,
            "rows_suppressed": self.rows_suppressed,
            "rows": self.df.to_dicts(),
        }


@dataclass(frozen=True, slots=True)
class Distribution:
    """The distribution of one typed evidence column of a built phenotype (EP-42): one
    row per ``level`` present after suppression — ``n_units`` / ``n_positive`` / ``share``
    (an absent level is zero units or a suppressed small cell)."""

    ref: str
    column: str
    tier: str
    k: int
    df: polars.DataFrame
    levels: tuple[ParamValue, ...]
    rows_suppressed: int
    audit_id: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "ref": self.ref,
            "column": self.column,
            "tier": self.tier,
            "k": self.k,
            "levels": list(self.levels),
            "rows_suppressed": self.rows_suppressed,
            "rows": self.df.to_dicts(),
        }


@dataclass(frozen=True, slots=True)
class Agreement:
    """The 2x2 agreement of two built phenotypes per admission (EP-42): their
    ``hadm``-grain relations joined on the admission — ``n_hadm`` admissions in the join,
    ``n_both`` / ``n_a_only`` / ``n_b_only`` / ``n_neither``; every cell None when the
    row was suppressed (any cell in ``1 .. k-1``)."""

    ref_a: str
    ref_b: str
    tier: str
    k: int
    denominator: str
    n_hadm: int | None
    n_both: int | None
    n_a_only: int | None
    n_b_only: int | None
    n_neither: int | None
    rows_suppressed: int
    audit_id: str

    @property
    def suppressed(self) -> bool:
        return self.n_hadm is None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ref_a": self.ref_a,
            "ref_b": self.ref_b,
            "tier": self.tier,
            "k": self.k,
            "denominator": self.denominator,
            "n_hadm": self.n_hadm,
            "n_both": self.n_both,
            "n_a_only": self.n_a_only,
            "n_b_only": self.n_b_only,
            "n_neither": self.n_neither,
            "rows_suppressed": self.rows_suppressed,
        }


def _run_query(
    sql: str,
    *,
    name: str,
    tier: str,
    k: int,
    settings: Settings,
    actor: str | None,
    run: Run | None,
) -> SafeResult:
    """One audited read: through ``Run.safe_query`` (statement + audit id recorded on the
    run) when a run is open, else plain :func:`safe_query`."""
    if run is not None:
        return run.safe_query(sql, name=name, tier=tier, k=k, settings=settings, actor=actor)
    from mimicwarehouse.safe import safe_query

    return safe_query(sql, tier=tier, k=k, settings=settings, actor=actor)


def _latest_built(
    ref: str, *, tier: str, settings: Settings, actor: str | None
) -> tuple[str, str, str]:
    """``(resolved ref, grain, the session view)`` of the latest built version of
    ``ref``'s id on ``tier`` — refusing a ref that is not that latest version (only the
    latest has a session view) or an id with no built version."""
    phenotype_id, sep, version = ref.partition("@")
    if not sep:
        raise PhenotypeError(f"{ref!r} is not a phenotype reference (id@version)")
    versions = [
        v
        for v in built_versions(tier, settings=settings, actor=actor)
        if v["phenotype_id"] == phenotype_id and v["status"] == "done"
    ]
    if not versions:
        raise PhenotypeError(
            f"no built version of {phenotype_id} on tier {tier} — run "
            f"`mwh phenotype compile {ref} --tier {tier}` first"
        )
    latest = max(versions, key=lambda v: semver_key(str(v["version"])))
    if str(latest["version"]) != version:
        raise PhenotypeError(
            f"{ref} is not the latest built version on tier {tier} ({phenotype_id}@"
            f"{latest['version']} is); only the latest version has a session view "
            f"(mimiciv_derived.{VIEW_PREFIX}{phenotype_id})"
        )
    grain = str(latest["grain"])
    return (
        f"{phenotype_id}@{latest['version']}",
        grain,
        f"{DERIVED_SCHEMA}.{VIEW_PREFIX}{phenotype_id}",
    )


def _hadm_relation(grain: str, view: str) -> str:
    """The admission-level relation of a built phenotype: the view itself for the
    ``hadm`` grain, the ``_hadm`` companion for the subject and icustay grains."""
    return view if grain == "hadm" else view + HADM_SUFFIX


def built_versions(
    tier: str, *, settings: Settings | None = None, actor: str | None = None
) -> list[dict[str, Any]]:
    """The ``meta.phenotype_versions`` rows of a tier through ``safe_query`` (a registry
    read, printable); ``[]`` when the table does not exist yet."""
    from mimicwarehouse.safe import safe_query

    present = safe_query(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'meta' "
        f"AND table_name = '{VERSIONS_TABLE}'",
        tier=tier,
        settings=settings,
        actor=actor,
    )
    if present.df.height == 0:
        return []
    result = safe_query(
        f"SELECT phenotype_id, version, def_hash, grain, status FROM meta.{VERSIONS_TABLE}",
        tier=tier,
        settings=settings,
        actor=actor,
        row_cap=10_000,
    )
    return result.df.to_dicts()


def _with_share(df: polars.DataFrame) -> polars.DataFrame:
    import polars as pl

    return df.with_columns(
        pl.when(pl.col("n_units") > 0)
        .then(pl.col("n_positive") / pl.col("n_units"))
        .otherwise(None)
        .alias("share")
    )


def summarize(
    ref: str,
    *,
    tier: str,
    settings: Settings | None = None,
    k: int | None = None,
    actor: str | None = None,
    run: Run | None = None,
) -> Summary:
    """The prevalence summary of ``ref`` on ``tier`` (module docstring); ``run`` records
    the two statements and their audit ids on an open run."""
    import polars as pl

    from mimicwarehouse.config import get_settings

    settings = settings or get_settings()
    resolved_ref, grain, view = _latest_built(ref, tier=tier, settings=settings, actor=actor)
    phenotype_id = resolved_ref.partition("@")[0]
    era_relation = view if grain != "subject" else view + HADM_SUFFIX
    era_unit = grain if grain != "subject" else "hadm"
    resolved_k = k if k is not None else settings.k_suppression
    audit_ids: list[str] = []
    suppressed = 0
    total = _run_query(
        f"SELECT count(*) AS n_units, count(*) FILTER (WHERE flag) AS n_positive FROM {view}",
        name=f"{phenotype_id}_total",
        tier=tier,
        k=resolved_k,
        settings=settings,
        actor=actor,
        run=run,
    )
    audit_ids.append(total.audit_id)
    suppressed += total.rows_suppressed
    by_era = _run_query(
        "SELECT e.anchor_year_group AS era, count(*) AS n_units, "
        "count(*) FILTER (WHERE p.flag) AS n_positive "
        f"FROM {era_relation} AS p "
        f"JOIN {DERIVED_SCHEMA}.hadm_era AS e ON e.hadm_id = p.hadm_id "
        "GROUP BY 1 ORDER BY 1",
        name=f"{phenotype_id}_by_era",
        tier=tier,
        k=resolved_k,
        settings=settings,
        actor=actor,
        run=run,
    )
    audit_ids.append(by_era.audit_id)
    suppressed += by_era.rows_suppressed
    records: list[dict[str, Any]] = []
    for row in total.df.to_dicts():
        records.append({"scope": "all", "unit": grain, **row})
    for row in by_era.df.to_dicts():
        era = row.pop("era")
        records.append({"scope": str(era), "unit": era_unit, **row})
    df = _with_share(
        pl.DataFrame(
            records,
            schema={
                "scope": pl.String,
                "unit": pl.String,
                "n_units": pl.Int64,
                "n_positive": pl.Int64,
            },
        )
    )
    return Summary(
        ref=resolved_ref,
        tier=tier,
        grain=grain,
        k=resolved_k,
        df=df,
        rows_suppressed=suppressed,
        audit_ids=tuple(audit_ids),
        snapshot_id=total.snapshot_id,
    )


def summary(
    ref: str,
    tier: str,
    *,
    settings: Settings | None = None,
    k: int | None = None,
    actor: str | None = None,
) -> polars.DataFrame:
    """The brief's helper: ``summarize(...).df`` — ``n_units`` / ``n_positive`` / ``share``
    overall and by era, k-suppressed through ``safe_query``."""
    return summarize(ref, tier=tier, settings=settings, k=k, actor=actor).df


def distribution(
    ref: str,
    column: str,
    *,
    tier: str,
    settings: Settings | None = None,
    k: int | None = None,
    actor: str | None = None,
    run: Run | None = None,
    levels: Iterable[ParamValue] = (),
) -> Distribution:
    """The distribution of the typed evidence column ``column`` of ``ref``'s latest built
    version (EP-42): ``n_units`` and ``n_positive`` per level, k-suppressed row-wise."""
    import polars as pl

    from mimicwarehouse.config import get_settings

    settings = settings or get_settings()
    name = column.strip().lower()
    if not _COLUMN_RE.match(name) or name in RESERVED_COLUMNS or name.endswith("_id"):
        raise PhenotypeError(f"{column!r} is not a typed evidence column name")
    resolved_ref, _grain, view = _latest_built(ref, tier=tier, settings=settings, actor=actor)
    phenotype_id = resolved_ref.partition("@")[0]
    resolved_k = k if k is not None else settings.k_suppression
    result = _run_query(
        f"SELECT {name} AS level, count(*) AS n_units, count(*) FILTER (WHERE flag) AS "
        f"n_positive FROM {view} GROUP BY 1 ORDER BY 1",
        name=f"{phenotype_id}_{name}_distribution",
        tier=tier,
        k=resolved_k,
        settings=settings,
        actor=actor,
        run=run,
    )
    df = result.df.with_columns(
        pl.col("n_units").cast(pl.Int64), pl.col("n_positive").cast(pl.Int64)
    )
    return Distribution(
        ref=resolved_ref,
        column=name,
        tier=tier,
        k=resolved_k,
        df=_with_share(df),
        levels=tuple(levels),
        rows_suppressed=result.rows_suppressed,
        audit_id=result.audit_id,
    )


def agreement(
    ref_a: str,
    ref_b: str,
    *,
    tier: str,
    settings: Settings | None = None,
    k: int | None = None,
    actor: str | None = None,
    run: Run | None = None,
) -> Agreement:
    """The 2x2 agreement of two built phenotypes per admission (EP-42): their
    admission-level relations (:func:`_hadm_relation`) joined on the admission, so an
    icustay-grain phenotype restricts the denominator to admissions with >= 1 ICU stay
    and a subject-grain one contributes its prevalent-by-discharge flag."""
    from mimicwarehouse.config import get_settings

    settings = settings or get_settings()
    resolved_a, grain_a, view_a = _latest_built(ref_a, tier=tier, settings=settings, actor=actor)
    resolved_b, grain_b, view_b = _latest_built(ref_b, tier=tier, settings=settings, actor=actor)
    if resolved_a == resolved_b:
        raise PhenotypeError(f"agreement needs two different phenotypes, got {resolved_a} twice")
    resolved_k = k if k is not None else settings.k_suppression
    denominator = (
        "admissions with at least one ICU stay"
        if "icustay" in (grain_a, grain_b)
        else "all admissions"
    )
    id_a = resolved_a.partition("@")[0]
    id_b = resolved_b.partition("@")[0]
    result = _run_query(
        "SELECT count(*) AS n_hadm, "
        "count(*) FILTER (WHERE a.flag AND b.flag) AS n_both, "
        "count(*) FILTER (WHERE a.flag AND NOT b.flag) AS n_a_only, "
        "count(*) FILTER (WHERE NOT a.flag AND b.flag) AS n_b_only, "
        "count(*) FILTER (WHERE NOT a.flag AND NOT b.flag) AS n_neither "
        f"FROM {_hadm_relation(grain_a, view_a)} AS a "
        f"JOIN {_hadm_relation(grain_b, view_b)} AS b ON b.hadm_id = a.hadm_id",
        name=f"agreement_{id_a}_{id_b}",
        tier=tier,
        k=resolved_k,
        settings=settings,
        actor=actor,
        run=run,
    )
    cells: dict[str, int | None] = dict.fromkeys(
        ("n_hadm", "n_both", "n_a_only", "n_b_only", "n_neither")
    )
    if result.df.height == 1:
        row = result.df.row(0, named=True)
        cells = {name: int(row[name]) for name in cells}
    return Agreement(
        ref_a=resolved_a,
        ref_b=resolved_b,
        tier=tier,
        k=resolved_k,
        denominator=denominator,
        n_hadm=cells["n_hadm"],
        n_both=cells["n_both"],
        n_a_only=cells["n_a_only"],
        n_b_only=cells["n_b_only"],
        n_neither=cells["n_neither"],
        rows_suppressed=result.rows_suppressed,
        audit_id=result.audit_id,
    )


# ---------------------------------------------------------------------------
# The prevalence report (EP-42 item 4): summaries + distributions + agreement -> Markdown
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Definition:
    """What the report says about a phenotype's definition (from the registry when the
    built version is registered; documentation text only)."""

    ref: str
    name: str
    grain: str
    def_hash: str
    what_it_does_not_claim: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PrevalenceReport:
    """``mwh phenotype summary``'s bundle: one :class:`Summary` per phenotype, the
    distributions of every evidence column that declares ``levels``, the pairwise
    :class:`Agreement` tables, the definitions, and the run that recorded the reads."""

    tier: str
    k: int
    summaries: tuple[Summary, ...]
    distributions: tuple[Distribution, ...]
    agreements: tuple[Agreement, ...]
    definitions: dict[str, Definition] = field(default_factory=dict)
    run_id: str | None = None
    generated: str = ""

    @property
    def audit_ids(self) -> tuple[str, ...]:
        ids: list[str] = []
        for s in self.summaries:
            ids.extend(s.audit_ids)
        ids.extend(d.audit_id for d in self.distributions)
        ids.extend(a.audit_id for a in self.agreements)
        return tuple(dict.fromkeys(ids))

    @property
    def rows_suppressed(self) -> int:
        return (
            sum(s.rows_suppressed for s in self.summaries)
            + sum(d.rows_suppressed for d in self.distributions)
            + sum(a.rows_suppressed for a in self.agreements)
        )

    @property
    def snapshot_id(self) -> str | None:
        return next((s.snapshot_id for s in self.summaries if s.snapshot_id), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tier": self.tier,
            "k": self.k,
            "run_id": self.run_id,
            "generated": self.generated,
            "rows_suppressed": self.rows_suppressed,
            "phenotypes": [s.to_dict() for s in self.summaries],
            "distributions": [d.to_dict() for d in self.distributions],
            "agreement": [a.to_dict() for a in self.agreements],
        }


def prevalence_report(
    refs: Sequence[str],
    *,
    tier: str,
    settings: Settings | None = None,
    k: int | None = None,
    actor: str | None = None,
    run: Run | None = None,
    registry: Registry | None = None,
    agreements: bool = True,
) -> PrevalenceReport:
    """Summaries of ``refs`` (each the latest built version of its id on ``tier``), the
    distribution of every evidence column with declared ``levels`` (when the definition
    is in ``registry``), and the agreement 2x2 of every pair (module docstring)."""
    from mimicwarehouse.config import get_settings

    settings = settings or get_settings()
    resolved_k = k if k is not None else settings.k_suppression
    wanted = list(dict.fromkeys(r.strip() for r in refs if r.strip()))
    if not wanted:
        raise PhenotypeError("prevalence_report needs at least one phenotype reference")
    summaries: list[Summary] = []
    distributions: list[Distribution] = []
    definitions: dict[str, Definition] = {}
    for ref in wanted:
        s = summarize(ref, tier=tier, settings=settings, k=resolved_k, actor=actor, run=run)
        summaries.append(s)
        entry: Entry | None = None
        if registry is not None:
            try:
                entry = registry.get(s.ref)
            except UnknownPhenotypeError:
                entry = None
        if entry is None:
            continue
        p = entry.phenotype
        definitions[s.ref] = Definition(
            ref=s.ref,
            name=p.name,
            grain=p.grain,
            def_hash=entry.def_hash,
            what_it_does_not_claim=tuple(p.what_it_does_not_claim),
        )
        for e in p.evidence_columns:
            if e.levels:
                distributions.append(
                    distribution(
                        s.ref,
                        e.name,
                        tier=tier,
                        settings=settings,
                        k=resolved_k,
                        actor=actor,
                        run=run,
                        levels=e.levels,
                    )
                )
    pairs: list[Agreement] = []
    if agreements:
        for i, a in enumerate(summaries):
            for b in summaries[i + 1 :]:
                pairs.append(
                    agreement(
                        a.ref,
                        b.ref,
                        tier=tier,
                        settings=settings,
                        k=resolved_k,
                        actor=actor,
                        run=run,
                    )
                )
    if run is not None:
        for s in summaries:
            phenotype_id, _, version = s.ref.partition("@")
            definition = definitions.get(s.ref)
            run.record_ref(
                "phenotype",
                phenotype_id,
                version=version,
                hash=definition.def_hash if definition else None,
            )
    return PrevalenceReport(
        tier=tier,
        k=resolved_k,
        summaries=tuple(summaries),
        distributions=tuple(distributions),
        agreements=tuple(pairs),
        definitions=definitions,
        run_id=run.run_id if run is not None else None,
        generated=datetime.now(UTC).date().isoformat(),
    )


def _md_table(header: list[str], rows: list[list[str]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return lines


def _share_text(value: Any) -> str:
    return "-" if value is None else f"{float(value) * 100:.1f} %"


def _count_text(value: int | None, k: int) -> str:
    from mimicwarehouse.inventory import fmt_int

    return f"suppressed (< {fmt_int(k)})" if value is None else fmt_int(value)


def render_prevalence_report(report: PrevalenceReport) -> str:
    """``phenotype_prevalence.md`` (module docstring): ASCII, every integer through
    ``fmt_int``, no identifier column anywhere, the claim-type label, the retrospective
    statement and the disclosure line (:data:`SIDECAR_PENDING`) in the header."""
    from mimicwarehouse.inventory import fmt_int

    k = report.k
    lines = [
        "# Phenotype prevalence (EP-42)",
        "",
        f"**Claim type: {CLAIM_TYPE}.** {RETROSPECTIVE_SENTENCE}",
        "",
        f"{SIDECAR_PENDING} - every number below came through `safe_query` (k = "
        f"{fmt_int(k)} suppression through the `disclose` hook: a row with any count in "
        f"1..{fmt_int(k - 1)}, or a row that would let such a cell be backed out, is "
        "withheld; audited), so the check is a re-verification (EP-42 amendment, EP-43).",
        "",
        f"Run `{report.run_id or '-'}` - tier `{report.tier}` - core snapshot "
        f"`{report.snapshot_id or '-'}` - generated {report.generated or '-'} - "
        f"{fmt_int(report.rows_suppressed)} row(s) suppressed in total - "
        f"{fmt_int(len(report.audit_ids))} audited read(s).",
        "",
        "## Prevalence",
        "",
    ]
    for s in report.summaries:
        definition = report.definitions.get(s.ref)
        title = f"### `{s.ref}`" + (f" - {definition.name}" if definition else "")
        lines += [
            title,
            "",
            f"Grain `{s.grain}`"
            + (f" - def_hash `{definition.def_hash[:12]}`" if definition else "")
            + f" - {fmt_int(s.rows_suppressed)} row(s) suppressed at k = {fmt_int(k)}.",
            "",
            *_md_table(
                ["scope", "unit", "n_units", "n_positive", "share"],
                [
                    [
                        str(r["scope"]),
                        str(r["unit"]),
                        fmt_int(int(r["n_units"])),
                        fmt_int(int(r["n_positive"])),
                        _share_text(r["share"]),
                    ]
                    for r in s.df.to_dicts()
                ],
            ),
            "",
        ]
    if report.distributions:
        lines += ["## Distributions", ""]
        for d in report.distributions:
            present = {str(r["level"]): r for r in d.df.to_dicts()}
            levels = [str(v) for v in d.levels] or list(present)
            rows: list[list[str]] = []
            for level in levels:
                r = present.get(level)
                if r is None:
                    rows.append([level, "-", "-", "-"])
                else:
                    rows.append(
                        [
                            level,
                            fmt_int(int(r["n_units"])),
                            fmt_int(int(r["n_positive"])),
                            _share_text(r["share"]),
                        ]
                    )
            lines += [
                f"### `{d.column}` of `{d.ref}`",
                "",
                *_md_table(["level", "n_units", "n_positive", "share"], rows),
                "",
                f"An absent level (`-`) has zero units or was suppressed (< {fmt_int(k)}); "
                f"{fmt_int(d.rows_suppressed)} row(s) suppressed.",
                "",
            ]
    if report.agreements:
        lines += ["## Agreement (per admission)", ""]
        for a in report.agreements:
            id_a = a.ref_a.partition("@")[0]
            id_b = a.ref_b.partition("@")[0]
            lines += [
                f"### `{a.ref_a}` x `{a.ref_b}`",
                "",
                f"Denominator: {a.denominator} - n = {_count_text(a.n_hadm, k)}.",
                "",
                *_md_table(
                    ["", f"{id_b} yes", f"{id_b} no"],
                    [
                        [f"{id_a} yes", _count_text(a.n_both, k), _count_text(a.n_a_only, k)],
                        [f"{id_a} no", _count_text(a.n_b_only, k), _count_text(a.n_neither, k)],
                    ],
                ),
                "",
            ]
    claims = [d for d in report.definitions.values() if d.what_it_does_not_claim]
    if claims:
        lines += ["## What these definitions do not claim", ""]
        for d in claims:
            lines.append(f"- `{d.ref}`:")
            lines.extend(f"  - {item.strip()}" for item in d.what_it_does_not_claim)
        lines.append("")
    refs = " ".join(s.ref for s in report.summaries)
    lines += [
        "## Reproduction",
        "",
        "```powershell",
        "cd mimicwarehouse",
        f"uv run --group dev mwh phenotype summary {refs} --tier {report.tier} --report",
        "```",
        "",
        f"The run folder `runs/{report.run_id or '<run_id>'}/` carries every statement "
        "under `sql/` and the audit ids of the reads in `manifest.json`; `mwh runs show "
        "<run_id>` prints them.",
        "",
    ]
    return "\n".join(lines)


def write_prevalence_report(report: PrevalenceReport, out_dir: Path | str) -> Path:
    """Render :func:`render_prevalence_report` to ``<out_dir>/phenotype_prevalence.md``
    (``fsio.atomic_write_text``; the directory is created)."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / PREVALENCE_REPORT
    fsio.atomic_write_text(path, render_prevalence_report(report).rstrip("\n") + "\n")
    return path


# ---------------------------------------------------------------------------
# docs/methods/phenotypes.md — the generated block
# ---------------------------------------------------------------------------

METHODS_DOC_RELPATH = Path("docs") / "methods" / "phenotypes.md"
CARDS_MARK = ("<!-- cards:begin -->", "<!-- cards:end -->")


def leaf_text(leaf: Leaf) -> str:
    """A one-line rendering of a leaf's definition (documentation and ``show``)."""
    p = leaf.payload
    if leaf.kind == "diagnosis":
        return f"diagnosis({p.codeset}, position {p.position}, min_admissions {p.min_admissions})"
    if leaf.kind == "procedure":
        return f"procedure({p.codeset})"
    if leaf.kind == "medication":
        return f"medication({p.codeset}, source {p.source}, min_orders {p.min_orders})"
    if leaf.kind == "lab":
        source = p.codeset if p.codeset else f"itemids {list(p.itemids)}"
        unit = f" {p.unit}" if p.unit else ""
        return f"lab({source}, {p.op} {p.threshold}{unit}, min_count {p.min_count})"
    if leaf.kind == "microbiology":
        parts = []
        if p.spec_itemids:
            parts.append(f"spec_itemids {list(p.spec_itemids)}")
        if p.org_itemids:
            parts.append(f"org_itemids {list(p.org_itemids)}")
        parts.append(f"positive_only {str(p.positive_only).lower()}")
        return f"microbiology({', '.join(parts)})"
    if leaf.kind == "concept":
        time = f", time {p.time_column}" if p.time_column else ""
        window = (
            f", window [{p.window.from_hours:g}, {p.window.to_hours:g}) h from the anchor"
            if p.window is not None
            else ""
        )
        evidence = (
            ", evidence " + ", ".join(f"{e.name} = {e.agg}({e.column})" for e in p.evidence)
            if p.evidence
            else ""
        )
        return (
            f"concept({p.table}.{p.column} {p.op} {p.value!r}, key {p.key}{time}{window}{evidence})"
        )
    hours = f", {p.hours} h" if p.hours is not None else ""
    return f"temporal({p.a.id} {p.relation} {p.b.id}{hours})"


def render_card(entry: Entry) -> str:
    """One definition card (Markdown) for the methods page."""
    p = entry.phenotype
    lines = [
        f"### `{p.ref}` — {p.name}",
        "",
        f"- **grain** `{p.grain}` · **def_hash** `{entry.def_hash[:12]}` · **locked** "
        f"{'yes' if entry.locked else 'no'} · `{entry.display_path}`",
        f"- **criteria** `{p.criteria.render()}` · **onset** `{p.onset.canonical()}`",
    ]
    refs = ", ".join(f"`{ref}` (`{h[:12]}`)" for ref, h in sorted(entry.resolved.items()))
    lines.append(f"- **references** {refs or '(none)'}")
    if p.parameters:
        listed = ", ".join(f"`{k}` = `{v!r}`" for k, v in sorted(p.parameters.items()))
        lines.append(f"- **parameters** {listed}")
    if entry.concepts:
        pins = ", ".join(
            f"`{table}` (`{pin.executed_sha256[:12]}`, "
            f"{'patch ' + pin.patch_id if pin.patch_id else 'unpatched'})"
            for table, pin in sorted(entry.concepts.items())
        )
        lines.append(f"- **concepts pinned** {pins}")
    if p.evidence_columns:
        evidence = ", ".join(
            f"`{e.name}` = {e.agg}(`{e.column}`)"
            + (f", default `{e.default!r}`" if e.default is not None else "")
            + (f", levels `{list(e.levels)}`" if e.levels else "")
            for e in p.evidence_columns
        )
        lines.append(f"- **evidence columns** {evidence}")
    lines.append("")
    lines.append("| leaf | kind | definition | polarity |")
    lines.append("|---|---|---|---|")
    for leaf, neg in p.all_leaves():
        polarity = "negated" if neg else "positive"
        lines.append(f"| `{leaf.id}` | {leaf.kind} | {leaf_text(leaf)} | {polarity} |")
    lines.append("")
    if p.description:
        lines.append(p.description.strip())
        lines.append("")
    if p.what_it_does_not_claim:
        lines.append("What it does not claim:")
        lines.append("")
        lines.extend(f"- {item.strip()}" for item in p.what_it_does_not_claim)
        lines.append("")
    return "\n".join(lines)


def render_cards(registry: Registry | None = None) -> str:
    reg = registry if registry is not None else load_registry()
    return "\n".join(render_card(e) for e in reg) + "\n"


def methods_doc_path() -> Path:
    """``mimicwarehouse/docs/methods/phenotypes.md``."""
    from mimicwarehouse.config import workspace_root

    return workspace_root() / METHODS_DOC_RELPATH


def sync_methods_doc(path: Path | None = None) -> Path:
    """Re-render the generated block of the methods page in place (idempotent;
    ``python -m mimicwarehouse.phenotypes`` runs it; ``test_ep41`` asserts the page is in
    sync). The narrative around the markers is never touched."""
    from mimicwarehouse.dag.benchmarks import replace_marked_block

    target = Path(path) if path is not None else methods_doc_path()
    text = target.read_text(encoding="utf-8")
    begin, end = CARDS_MARK
    text = replace_marked_block(text, render_cards(), begin=begin, end=end)
    target.write_text(text.rstrip("\n") + "\n", encoding="utf-8", newline="\n")
    return target


__all__ = [
    "BENCH_KIND",
    "CARDS_MARK",
    "CLAIM_TYPE",
    "DAG_TAG",
    "DERIVED_SCHEMA",
    "EVIDENCE_MAX_CHARS",
    "HADM_SUFFIX",
    "METHODS_DOC_RELPATH",
    "PREVALENCE_REPORT",
    "RETROSPECTIVE_SENTENCE",
    "SCHEMA",
    "SIDECAR_PENDING",
    "SMALL_CELL",
    "STEP_COMPILE",
    "VERSIONS_COLUMNS",
    "VERSIONS_TABLE",
    "VIEW_PREFIX",
    "Agreement",
    "BuildOutcome",
    "CompileOptions",
    "ConceptNotBuiltError",
    "ConceptPinMismatchError",
    "Definition",
    "Distribution",
    "PrevalenceReport",
    "Summary",
    "agreement",
    "built_versions",
    "compile_options",
    "concept_steps",
    "current_options",
    "distribution",
    "latest_versions",
    "leaf_text",
    "methods_doc_path",
    "phenotype_attempt",
    "phenotype_complete",
    "phenotype_dir",
    "phenotype_entry",
    "phenotype_part",
    "prevalence_report",
    "register_phenotypes",
    "render_card",
    "render_cards",
    "render_prevalence_report",
    "run_compile",
    "semver_key",
    "status_key",
    "summarize",
    "summary",
    "sync_methods_doc",
    "versions_path",
    "versions_rows",
    "write_prevalence_report",
]
