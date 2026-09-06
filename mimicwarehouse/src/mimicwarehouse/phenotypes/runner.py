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

:func:`register_phenotypes` (``CATALOG_EXTENSIONS`` entry, after the walker, before ``units``)
    ``mimiciv_derived.phenotype_<id>`` = the **latest built version** of each id (semver
    order) over the walker's ``phenotypes.*`` view, plus the per-admission companion
    ``mimiciv_derived.phenotype_<id>_hadm`` for subject-grain phenotypes
    (:func:`compiler.hadm_companion_sql`), and the comment on ``meta.phenotype_versions``.
    Sessions read the ``mimiciv_derived`` views through ``safe_query`` (subject-keyed,
    non-registry reads: the ``phenotypes`` schema itself is not on the allow list).

:func:`summarize` / :func:`summary`
    The prevalence helper: ``n_units`` / ``n_positive`` (+ ``share``) for the phenotype's
    grain, then by era through ``mimiciv_derived.hadm_era`` (the ``_hadm`` companion for
    subject-grain phenotypes), every number read through ``safe_query`` (k = 11 row-wise
    suppression on the credentialed tiers, audited); ``mwh phenotype summary`` prints it.

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
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mimicwarehouse import publish
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
    sql_str,
)
from mimicwarehouse.phenotypes.registry import Entry, Registry, load_registry
from mimicwarehouse.phenotypes.spec import Leaf, PhenotypeError

if TYPE_CHECKING:  # pragma: no cover
    import duckdb
    import polars

    from mimicwarehouse.config import Settings
    from mimicwarehouse.dag.runner import StepContext, StepOutcome
    from mimicwarehouse.dag.spec import Step

_LOG = logging.getLogger(__name__)

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
    views on the build connection (:func:`concepts.runner.ensure_derived_view`)."""
    from mimicwarehouse.concepts.runner import ConceptError, ensure_derived_view

    for table in entry.phenotype.concept_tables:
        schema, _, name = table.partition(".")
        if schema != DERIVED_SCHEMA:
            continue
        try:
            ensure_derived_view(ctx, name)
        except ConceptError as exc:
            raise PhenotypeError(f"{entry.ref}: {exc}") from None


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
        _ensure_concepts(ctx, entry)
        compiled = compile_phenotype(entry.phenotype, registry.codesets, resolved=entry.resolved)
        outcome = _materialize(ctx, entry, compiled)
        total_rows += outcome.rows
        total_bytes += outcome.nbytes
        built.append(entry.ref)
    rows = versions_rows(
        ctx.lake_root, ctx.tier, k=ctx.settings.k_suppression, settings=ctx.settings
    )
    dest = versions_path(ctx.lake_root, ctx.tier)
    meta_bytes = write_meta_parquet(ctx.con, dest, VERSIONS_COLUMNS, rows)
    _LOG.info(
        "meta.phenotype_versions (%s): %d version(s) attempted on the tier, %d built now, "
        "%d skipped — %s",
        ctx.tier,
        len(rows),
        len(built),
        len(skipped),
        dest,
    )
    return StepOutcome(
        rows=total_rows,
        bytes_out=total_bytes + meta_bytes,
        files=len(built) + 1,
        layer=DERIVED_LAYER,
    )


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
)


def versions_rows(
    lake_root: Path | str, tier: str, *, k: int, settings: Settings | None = None
) -> list[list[Any]]:
    """The ``meta.phenotype_versions`` rows for ``tier`` from ``status.json``: one per
    ``phenotypes.<id>@<version>`` entry attempted on the tier (id, then semver order);
    ``n_positive`` blanked (``n_positive_suppressed = true``) below ``k`` (D-33)."""
    status = read_status(Path(lake_root))["steps"]
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
            ]
        )
    return rows


# ---------------------------------------------------------------------------
# Catalog extension: mimiciv_derived.phenotype_<id> (+ _hadm), the meta comment
# ---------------------------------------------------------------------------

_VERSIONS_COMMENT = (
    "One row per phenotype version attempted on this tier (phenotypes/runner.py, EP-41): "
    "def_hash (sha256 of grain + criteria + onset + the referenced code-set hashes), "
    "grain, refs (JSON of id@version -> def_hash), rows, n_positive (NULL below k, "
    "n_positive_suppressed), built_at, run_id / build_id, sql_sha256, the derived "
    "snapshot id, status, error_class. The latest built version of each id is exposed as "
    "mimiciv_derived.phenotype_<id> (+ phenotype_<id>_hadm for subject-grain phenotypes)."
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
        con.execute(
            f"COMMENT ON VIEW {view} IS "
            + sql_str(
                f"Phenotype {ref} (grain {grain}; the latest built version on tier {tier}; "
                f"EP-41 engine): {', '.join(c for c in columns if c in OUTPUT_COLUMNS)} per "
                "unit — flag = the boolean criteria tree, onset_time = the onset rule over "
                "the positive leaves, evidence_json = per-leaf event counts. Every built "
                f'version lives under {SCHEMA}."<id>@<version>"; meta.phenotype_versions '
                "carries the hashes. Subject-keyed: read through safe_query as aggregates."
            )
        )
        views += 1
        if grain == "subject" and "mimiciv_hosp.admissions" in present:
            companion = f"{DERIVED_SCHEMA}.{_sql_ident(VIEW_PREFIX + phenotype_id + HADM_SUFFIX)}"
            con.execute(
                f"CREATE OR REPLACE VIEW {companion} AS "
                + hadm_companion_sql(source, has_onset="onset_time" in columns)
            )
            con.execute(
                f"COMMENT ON VIEW {companion} IS "
                + sql_str(
                    f"Per-admission companion of phenotype {ref} (EP-41): every admission, "
                    "flag = the subject's onset lies at or before this dischtime (prevalent "
                    "by discharge), onset_time carried over. Join hadm_era for the era axis."
                )
            )
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
    ``n_positive``, ``share``), the version actually read, ``k``, suppressed row counts and
    the audit ids of the reads."""

    ref: str
    tier: str
    grain: str
    k: int
    df: polars.DataFrame
    rows_suppressed: int
    audit_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ref": self.ref,
            "tier": self.tier,
            "grain": self.grain,
            "k": self.k,
            "rows_suppressed": self.rows_suppressed,
            "rows": self.df.to_dicts(),
        }


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


def summarize(
    ref: str,
    *,
    tier: str,
    settings: Settings | None = None,
    k: int | None = None,
    actor: str | None = None,
) -> Summary:
    """The prevalence summary of ``ref`` on ``tier`` (module docstring)."""
    import polars as pl

    from mimicwarehouse.config import get_settings
    from mimicwarehouse.safe import safe_query

    settings = settings or get_settings()
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
    view = f"{DERIVED_SCHEMA}.{VIEW_PREFIX}{phenotype_id}"
    era_relation = view if grain != "subject" else view + HADM_SUFFIX
    era_unit = grain if grain != "subject" else "hadm"
    resolved_k = k if k is not None else settings.k_suppression
    audit_ids: list[str] = []
    suppressed = 0
    total = safe_query(
        f"SELECT count(*) AS n_units, count(*) FILTER (WHERE flag) AS n_positive FROM {view}",
        tier=tier,
        k=resolved_k,
        settings=settings,
        actor=actor,
    )
    audit_ids.append(total.audit_id)
    suppressed += total.rows_suppressed
    by_era = safe_query(
        "SELECT e.anchor_year_group AS era, count(*) AS n_units, "
        "count(*) FILTER (WHERE p.flag) AS n_positive "
        f"FROM {era_relation} AS p "
        f"JOIN {DERIVED_SCHEMA}.hadm_era AS e ON e.hadm_id = p.hadm_id "
        "GROUP BY 1 ORDER BY 1",
        tier=tier,
        k=resolved_k,
        settings=settings,
        actor=actor,
    )
    audit_ids.append(by_era.audit_id)
    suppressed += by_era.rows_suppressed
    records: list[dict[str, Any]] = []
    for row in total.df.to_dicts():
        records.append({"scope": "all", "unit": grain, **row})
    for row in by_era.df.to_dicts():
        era = row.pop("era")
        records.append({"scope": str(era), "unit": era_unit, **row})
    df = pl.DataFrame(
        records,
        schema={
            "scope": pl.String,
            "unit": pl.String,
            "n_units": pl.Int64,
            "n_positive": pl.Int64,
        },
    ).with_columns(
        pl.when(pl.col("n_units") > 0)
        .then(pl.col("n_positive") / pl.col("n_units"))
        .otherwise(None)
        .alias("share")
    )
    return Summary(
        ref=f"{phenotype_id}@{latest['version']}",
        tier=tier,
        grain=grain,
        k=resolved_k,
        df=df,
        rows_suppressed=suppressed,
        audit_ids=tuple(audit_ids),
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
        return f"concept({p.table}.{p.column} {p.op} {p.value!r}, key {p.key}{time})"
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
    "DAG_TAG",
    "DERIVED_SCHEMA",
    "EVIDENCE_MAX_CHARS",
    "HADM_SUFFIX",
    "METHODS_DOC_RELPATH",
    "SCHEMA",
    "SMALL_CELL",
    "STEP_COMPILE",
    "VERSIONS_COLUMNS",
    "VERSIONS_TABLE",
    "VIEW_PREFIX",
    "BuildOutcome",
    "CompileOptions",
    "Summary",
    "built_versions",
    "compile_options",
    "current_options",
    "latest_versions",
    "leaf_text",
    "methods_doc_path",
    "phenotype_attempt",
    "phenotype_complete",
    "phenotype_dir",
    "phenotype_entry",
    "phenotype_part",
    "register_phenotypes",
    "render_card",
    "render_cards",
    "run_compile",
    "semver_key",
    "status_key",
    "summarize",
    "summary",
    "sync_methods_doc",
    "versions_path",
    "versions_rows",
]
