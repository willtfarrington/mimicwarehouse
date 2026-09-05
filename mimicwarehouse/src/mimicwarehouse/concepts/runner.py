"""Concept runner — the DAG step bodies behind ``concept.<group>.<name>`` and
``meta.concept_versions`` (EP-37 items 2-3; D-19/D-20, DESIGN §3/§8/§11).

Each concept step (``python`` kind, ``target`` ``mimiciv_derived.<name>``) is
:func:`run_concept`: it reads the concept from the committed inventory (never the vendor
tree — the file's ``sql_sha256`` is re-checked so a stale inventory fails loudly), takes
the **patch** file instead when ``concepts/patches/patches.yaml`` registers one for the
concept (EP-38; :func:`mimicwarehouse.concepts.patches.effective_sql` — the registry is
validated once per build before the first concept runs, and an entry written against
another upstream commit than the vendored pin refuses the build), strips
upstream's ``DROP TABLE … ; CREATE TABLE … AS`` header, prepares the tier's **sources** on
the runner's in-memory build connection — ``mimiciv_hosp`` / ``mimiciv_icu`` views over
the staged lake with the contract columns (the catalog's own DDL: dev = the bucket
filter) plus a ``mimiciv_derived`` view per concept already complete for the tier — and
sinks the SELECT straight to Parquet::

    COPY (<select>) TO '<lake_root(tier)>/derived/<tier>/mimiciv_derived/<name>/part-0.parquet.tmp'
        (FORMAT PARQUET, COMPRESSION ZSTD)

(``lake/derived/<tier>/…`` for dev and full, which share ``lake/``; ``lake/fixture/derived/
fixture/…`` and ``lake/demo/derived/demo/…`` for the synthetic tiers — their own lake roots,
never the credentialed tree), then publishes the file (``publish.replace``), appends one
:class:`~mimicwarehouse.loader.manifest.ManifestLine` to the lake's manifests (path
relative to that lake root: ``derived/<tier>/…``; ``source_sha256`` = the sha256 of the
SQL file that ran — the patch file's when patched, ``raw_snapshot_id`` = the core snapshot
it read), marks ``status.json`` (``layer: derived``, the effective ``sql_sha256`` beside
the ``vendored_sha256``, the ``patch_id`` or ``None``, plus the per-tier completeness
fields — a dev build sets ``dev_ready``,
every other tier ``tier_complete: full`` on its own lake root) and registers the new view
for the concepts that follow in the same run. No bucket partitions (EP-37 amendment 3):
one ZSTD file per table per tier, which DuckDB scans with pushdown. The EP-19 runner
skips a complete concept unless forced (``Step.status_key``), so a rerun resumes per
concept; a dev rebuild never touches the full tier's files.

:func:`run_concept_versions` (the ``meta.concept_versions`` step, after every concept)
records the derived layer snapshot (``lake/manifests/snapshots.json``), opens one
provenance run (``run.start("concepts", kind="build")``) citing the core and derived
snapshot ids and one ``concept`` ref per attempted concept, appends a
``BenchmarkLine(kind="concept", run_id=…)`` per concept attempted **in this build**
(``run.bench``; read back with ``mwh runs benchmarks --kind concept``) and writes
``lake/meta/<tier>/concept_versions.parquet`` — one row per attempted concept (complete
or failed on this tier): concept, group, upstream_commit, sql_sha256 (of the SQL that
ran), patch_id (the registry id when the table was built from a patch; NULL otherwise —
read back from the concept's ``status.json`` entry, so it says what *this* file was built
from, not what the registry says today), rows, bytes, wall_s, status, error, built_at,
build_id, run_id, snapshot_id, tier — which the catalog discovery walker loads as
``meta.concept_versions``.
Failures come from the benchmark ledger's latest ``kind: python`` line for the step
(EP-19 writes one per attempt) and are recorded, not hidden; a failing concept's error
text passes :func:`mimicwarehouse.safe.sanitize_error_text` before it reaches any ledger.

Everything written or logged is Parquet under the data root, counts, hashes and
timings — never a row outside the lake.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mimicwarehouse import publish
from mimicwarehouse.concepts.inventory import (
    DERIVED_SCHEMA,
    VERSIONS_STEP,
    Concept,
    Inventory,
    load_inventory,
    split_header,
)
from mimicwarehouse.concepts.patches import (
    Patch,
    PatchError,
    effective_sql,
    load_registry,
    validate_registry,
)
from mimicwarehouse.config import Settings
from mimicwarehouse.dag.snapshot import (
    complete_for_tier,
    entry_layer,
    layer_lines,
    layer_snapshot,
    record_snapshot,
)
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
from mimicwarehouse.loader.paths import PART_FILENAME, layer_table_dir, single_file_sql

if TYPE_CHECKING:  # pragma: no cover
    import duckdb

    from mimicwarehouse.dag.runner import StepContext, StepOutcome
    from mimicwarehouse.dag.spec import Step

_LOG = logging.getLogger(__name__)

#: The lake layer concepts write (``Settings.layout["lake_derived"]``; status ``layer``).
DERIVED_LAYER = "derived"
#: ``lake/meta/<tier>/<this>`` — what the catalog walker loads as ``meta.concept_versions``.
VERSIONS_FILENAME = "concept_versions.parquet"
#: Columns of ``meta.concept_versions`` (DDL order = row order in :func:`run_concept_versions`).
VERSIONS_DDL = (
    'concept VARCHAR, "group" VARCHAR, upstream_commit VARCHAR, sql_sha256 VARCHAR, '
    "patch_id VARCHAR, rows BIGINT, bytes BIGINT, wall_s DOUBLE, status VARCHAR, "
    "error VARCHAR, built_at VARCHAR, build_id VARCHAR, run_id VARCHAR, snapshot_id VARCHAR, "
    "tier VARCHAR"
)
VERSIONS_COLUMNS: tuple[str, ...] = (
    "concept",
    "group",
    "upstream_commit",
    "sql_sha256",
    "patch_id",
    "rows",
    "bytes",
    "wall_s",
    "status",
    "error",
    "built_at",
    "build_id",
    "run_id",
    "snapshot_id",
    "tier",
)


class ConceptError(RuntimeError):
    """A concept step cannot run or failed (message already sanitized)."""


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def derived_root(settings: Settings, tier: str) -> Path:
    """``<lake_root(tier)>/derived`` — ``layout["lake_derived"]`` for dev/full (they share
    ``lake/``), ``lake/fixture/derived`` / ``lake/demo/derived`` for the synthetic tiers, so
    a fixture or demo build never writes into the credentialed lake tree (EP-167/ARCH-3)
    and every manifest path resolves under its own lake root (EP-28's invariant)."""
    return settings.lake_root(tier) / DERIVED_LAYER


def derived_dir(settings: Settings, tier: str, name: str) -> Path:
    """``<derived root>/<tier>/mimiciv_derived/<name>`` (EP-37 amendment 3; DESIGN §3)."""
    return layer_table_dir(derived_root(settings, tier), tier, DERIVED_SCHEMA, name)


def derived_file(settings: Settings, tier: str, name: str) -> Path:
    """The concept's single ``part-0.parquet``."""
    return derived_dir(settings, tier, name) / PART_FILENAME


def versions_path(lake_root: Path, tier: str) -> Path:
    """``<lake_root>/meta/<tier>/concept_versions.parquet``."""
    from mimicwarehouse.catalog.profile import meta_dir

    return meta_dir(lake_root, tier) / VERSIONS_FILENAME


# ---------------------------------------------------------------------------
# Sources on the build connection
# ---------------------------------------------------------------------------

_PREPARED: dict[int, str] = {}  # id(con) -> build_id whose core views it holds
_CORE_SNAPSHOTS: dict[tuple[str, str, str], str] = {}  # (lake_root, tier, build_id) -> id
_REGISTRY_CHECKED: set[str] = set()  # build_ids whose patch registry validated (EP-38)


def ensure_registry(build_id: str) -> None:
    """Validate the patch registry once per build before any concept runs (EP-38 item 2):
    every entry's file, sha256 and ``applies_to_upstream_commit`` must match the vendored
    pin, else the build refuses to start (:class:`ConceptError`)."""
    if build_id in _REGISTRY_CHECKED:
        return
    try:
        registry = validate_registry()
    except PatchError as exc:
        raise ConceptError(f"concept patches: {exc}") from None
    _REGISTRY_CHECKED.add(build_id)
    if registry.patches:
        _LOG.info(
            "concepts: %d patch(es) registered and valid against upstream %s: %s",
            len(registry.patches),
            registry.patches[0].applies_to_upstream_commit[:12],
            ", ".join(p.patch_id for p in registry.patches),
        )


def _core_snapshot(ctx: StepContext) -> str:
    key = (str(ctx.lake_root), ctx.tier, ctx.build_id)
    if key not in _CORE_SNAPSHOTS:
        _CORE_SNAPSHOTS[key] = layer_snapshot(
            ctx.lake_root, "core", ctx.tier, settings=ctx.settings
        )
    return _CORE_SNAPSHOTS[key]


def register_derived_view(
    con: duckdb.DuckDBPyConnection, settings: Settings, tier: str, name: str
) -> bool:
    """``CREATE OR REPLACE VIEW mimiciv_derived.<name>`` over the concept's file (when it
    exists) on ``con`` — how a concept sees the ones built before it."""
    path = derived_file(settings, tier, name)
    if not path.is_file():
        return False
    con.execute(
        f'CREATE OR REPLACE VIEW {DERIVED_SCHEMA}."{name}" AS SELECT * FROM {single_file_sql(path)}'
    )
    return True


def prepare_sources(con: duckdb.DuckDBPyConnection, ctx: StepContext) -> int:
    """Create the tier's source views on the runner's build connection (module docstring)
    — once per connection per build for the core tables, plus every complete derived
    table each time (cheap: one file each). Returns the number of views present."""
    from mimicwarehouse.catalog.build import STAGED_SCHEMAS
    from mimicwarehouse.catalog.profile import relation_sql
    from mimicwarehouse.schema.contract import load_contract

    for schema in (*STAGED_SCHEMAS, DERIVED_SCHEMA):
        con.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
    status = read_status(ctx.lake_root)["steps"]
    n = 0
    if _PREPARED.get(id(con)) != ctx.build_id:
        contract = load_contract()
        for schema in STAGED_SCHEMAS:
            for table in contract.by_schema(schema):
                entry = status.get(table.qualified_name)
                if entry is None or not complete_for_tier(entry, ctx.tier):
                    continue
                columns = ", ".join(f'"{c.name}"' for c in table.columns)
                relation = relation_sql(table, ctx.lake_root, ctx.buckets)
                con.execute(
                    f'CREATE OR REPLACE VIEW {schema}."{table.name}" AS '
                    f"SELECT {columns} FROM {relation}"
                )
                n += 1
        _PREPARED[id(con)] = ctx.build_id
        _LOG.info("concepts: %d core source view(s) on the build connection (%s)", n, ctx.tier)
    for key, entry in status.items():
        if entry_layer(entry) != DERIVED_LAYER or not complete_for_tier(entry, ctx.tier):
            continue
        schema, name = key.split(".", 1)
        if schema == DERIVED_SCHEMA and register_derived_view(con, ctx.settings, ctx.tier, name):
            n += 1
    return n


# ---------------------------------------------------------------------------
# The concept step
# ---------------------------------------------------------------------------


def _concept_of(step: Step, inv: Inventory) -> Concept:
    if not step.target or not step.target.startswith(f"{DERIVED_SCHEMA}."):
        raise ConceptError(f"step {step.name}: expected target {DERIVED_SCHEMA}.<concept>")
    return inv.concept(step.target.split(".", 1)[1])


def _tier_status_fields(tier: str, existing: dict[str, Any]) -> dict[str, Any]:
    """The per-tier completeness fields a derived table's ``status.json`` entry gets
    (:func:`~mimicwarehouse.dag.snapshot.complete_for_tier` per-tier rules): a dev build
    marks ``dev_ready`` (keeping a recorded ``full``), every other tier ``full``."""
    if tier == "dev":
        keep_full = existing.get("tier_complete") == "full"
        return {"dev_ready": True, "tier_complete": "full" if keep_full else "dev"}
    return {"tier_complete": "full"}


def _schema_hash(described: list[tuple[Any, ...]]) -> str:
    payload = [[str(row[0]), str(row[1])] for row in described]
    return hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode()).hexdigest()


def _sanitized(exc: BaseException) -> str:
    from mimicwarehouse.safe import sanitize_error_text

    return f"{type(exc).__name__}: {sanitize_error_text(str(exc))}"


def build_concept(
    concept: Concept, ctx: StepContext, *, sql_text: str | None = None
) -> tuple[int, int]:
    """Execute one concept for the tier (module docstring) and return ``(rows, bytes)``.
    ``sql_text`` overrides the vendored file and the registry (tests feed a crafted
    concept); otherwise the registered patch, if any, replaces the vendored file."""
    import duckdb

    patch: Patch | None = None
    if sql_text is not None:
        text = sql_text
    else:
        ensure_registry(ctx.build_id)
        try:
            text, patch = effective_sql(concept)
        except PatchError as exc:
            raise ConceptError(f"{concept.step_name}: {exc}") from None
    effective_sha = patch.sql_sha256 if patch is not None else concept.sql_sha256
    table, body = split_header(text)
    if table != concept.name:
        raise ConceptError(
            f"{concept.step_name}: file creates {table!r}, inventory says {concept.name!r}"
        )
    prepare_sources(ctx.con, ctx)

    dest = derived_file(ctx.settings, ctx.tier, concept.name)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".tmp")
    publish.unlink(tmp)
    escaped = tmp.resolve().as_posix().replace("'", "''")
    t0 = time.perf_counter()
    try:
        ctx.con.execute(f"COPY ({body}) TO '{escaped}' (FORMAT PARQUET, COMPRESSION ZSTD)")
        row = ctx.con.execute(f"SELECT count(*) FROM {single_file_sql(tmp)}").fetchone()
        described = ctx.con.execute(f"DESCRIBE SELECT * FROM {single_file_sql(tmp)}").fetchall()
    except duckdb.Error as exc:
        publish.unlink(tmp)
        raise ConceptError(f"{concept.step_name}: {_sanitized(exc)}") from None
    rows = int(row[0]) if row else 0
    publish.replace(tmp, dest)
    size = dest.stat().st_size
    wall = time.perf_counter() - t0

    line = ManifestLine(
        schema=DERIVED_SCHEMA,
        table=concept.name,
        path=lake_relative_posix(dest, ctx.lake_root),
        sha256=sha256_streamed(dest),
        bytes=size,
        rows=rows,
        schema_hash=_schema_hash(described),
        writer_version=writer_version(),
        source_sha256=effective_sha,
        raw_snapshot_id=_core_snapshot(ctx),
        build_id=ctx.build_id,
        ts=utc_now_iso(),
    )
    append_manifest(ctx.lake_root, ctx.build_id, [line])
    existing = read_status(ctx.lake_root)["steps"].get(concept.target, {})
    patched = (
        f"; patched by `{patch.patch_id}` ({patch.short_ref}, EP-38)" if patch is not None else ""
    )
    update_status(
        ctx.lake_root,
        concept.target,
        layer=DERIVED_LAYER,
        group=concept.group,
        sql_sha256=effective_sha,
        vendored_sha256=concept.sql_sha256,
        patch_id=patch.patch_id if patch is not None else None,
        upstream_commit=concept.upstream_commit,
        comment=(
            f"mimic-code concept {concept.group}/{concept.name} at {concept.upstream_commit[:12]} "
            f"(concepts_duckdb, MIT; built by EP-37's concept runner{patched})"
        ),
        **_tier_status_fields(ctx.tier, existing),
    )
    register_derived_view(ctx.con, ctx.settings, ctx.tier, concept.name)
    _LOG.info(
        "concept %s: %s rows, %s bytes, wall=%.1fs%s -> %s",
        concept.step_name,
        f"{rows:,}",
        f"{size:,}",
        wall,
        f" [patch {patch.patch_id}]" if patch is not None else "",
        dest.parent,
    )
    return rows, size


def run_concept(step: Step, ctx: StepContext) -> StepOutcome:
    """The ``concept.<group>.<name>`` step handler body (DAG ``python`` kind)."""
    from mimicwarehouse.dag.runner import StepOutcome

    concept = _concept_of(step, load_inventory())
    rows, size = build_concept(concept, ctx)
    return StepOutcome(rows=rows, bytes_out=size, files=1)


# ---------------------------------------------------------------------------
# meta.concept_versions
# ---------------------------------------------------------------------------


def latest_concept_lines(settings: Settings, tier: str) -> dict[str, dict[str, Any]]:
    """The benchmark ledger's latest ``kind: python`` / ``phase: total`` line per concept
    step for ``tier`` (``{step name: line}``) — what says whether a concept was attempted
    and whether its last attempt failed (EP-19 writes one line per attempt)."""
    import polars as pl

    from mimicwarehouse.dag import benchmarks

    df = benchmarks.read(settings)
    if df.is_empty() or "step" not in df.columns:
        return {}
    df = df.filter(
        pl.col("step").is_not_null()
        & (pl.col("kind") == "python")
        & (pl.col("tier") == tier)
        & (pl.col("phase") == "total")
        & pl.col("step").str.starts_with("concept.")
    )
    if df.is_empty():
        return {}
    df = df.sort(["ts", "build_id"], maintain_order=True)
    return {str(rec["step"]): rec for rec in df.to_dicts()}


def run_concept_versions(step: Step, ctx: StepContext) -> StepOutcome:
    """The ``meta.concept_versions`` step handler body (module docstring)."""
    from mimicwarehouse import run as run_mod
    from mimicwarehouse.catalog.profile import write_meta_parquet
    from mimicwarehouse.dag.runner import StepOutcome

    inv = load_inventory()
    registry = load_registry()
    tier, settings, lake_root = ctx.tier, ctx.settings, ctx.lake_root
    status = read_status(lake_root)["steps"]
    lines = dict(layer_lines(lake_root, tier, layer=DERIVED_LAYER, settings=settings))
    ledger = latest_concept_lines(settings, tier)
    core_snapshot = _core_snapshot(ctx)
    derived_snapshot = layer_snapshot(lake_root, DERIVED_LAYER, tier, settings=settings)
    record_snapshot(
        lake_root,
        layer=DERIVED_LAYER,
        tier=tier,
        snapshot_id=derived_snapshot,
        build_id=ctx.build_id,
    )

    rows: list[list[Any]] = []
    n_failed = n_patched = 0
    with run_mod.start(
        "concepts",
        tier=tier,
        kind="build",
        params={
            "build_id": ctx.build_id,
            "step": VERSIONS_STEP,
            "concepts": len(inv.concepts),
            "upstream_commit": inv.upstream_commit,
            "patches": [p.patch_id for p in registry.patches],
        },
        settings=settings,
    ) as r:
        r.record_snapshot("core", core_snapshot)
        r.record_snapshot(DERIVED_LAYER, derived_snapshot)
        for concept in inv.concepts:
            entry = status.get(concept.target)
            line = lines.get(concept.target)
            bench = ledger.get(concept.step_name)
            complete = entry is not None and complete_for_tier(entry, tier) and line is not None
            if complete:
                assert entry is not None and line is not None
                status_str, error = "ok", None
                n_rows: int | None = line.rows
                n_bytes: int | None = line.bytes
                built_at, build = line.ts, line.build_id
                # what this file was built from (status.json), not today's registry
                patch_id = entry.get("patch_id")
                sha = str(entry.get("sql_sha256") or line.source_sha256 or concept.sql_sha256)
            elif bench is not None and not bench.get("ok", True):
                n_failed += 1
                status_str, error = "failed", bench.get("error")
                n_rows = n_bytes = None
                built_at, build = str(bench.get("ts")), str(bench.get("build_id"))
                failed_patch = registry.patch_for(concept.name)
                patch_id = failed_patch.patch_id if failed_patch is not None else None
                sha = failed_patch.sql_sha256 if failed_patch is not None else concept.sql_sha256
            else:
                continue  # never attempted on this tier
            if patch_id is not None:
                n_patched += 1
            # one `concept` ref per attempted concept (EP-37 shape): the hash is the sha256
            # of the SQL that ran, i.e. the patch file's for a patched concept; the patch
            # ids themselves are in params["patches"] and the versions table
            r.record_ref("concept", concept.name, version=concept.upstream_commit, hash=sha)
            wall = (
                float(bench["wall_s"])
                if bench is not None and bench.get("build_id") == build and bench.get("wall_s")
                else None
            )
            if bench is not None and bench.get("build_id") == ctx.build_id:
                r.bench(
                    "concept",
                    concept.step_name,
                    wall_s=float(bench["wall_s"]),
                    peak_rss_mb=bench.get("peak_rss_mb"),
                    rows=n_rows,
                    bytes_out=n_bytes,
                    files=1 if complete else None,
                    build_id=ctx.build_id,
                    ok=complete,
                    error=error,
                )
            rows.append(
                [
                    concept.name,
                    concept.group,
                    concept.upstream_commit,
                    sha,
                    patch_id,
                    n_rows,
                    n_bytes,
                    wall,
                    status_str,
                    error,
                    built_at,
                    build,
                    r.run_id,
                    derived_snapshot,
                    tier,
                ]
            )
        dest = versions_path(lake_root, tier)
        dest.parent.mkdir(parents=True, exist_ok=True)
        size = write_meta_parquet(ctx.con, dest, VERSIONS_DDL, rows)
        r.manifest.params = {
            **r.manifest.params,
            "attempted": len(rows),
            "ok": len(rows) - n_failed,
            "failed": n_failed,
            "patched": n_patched,
        }
    _LOG.info(
        "concept_versions %s: %d attempted (%d ok, %d failed, %d patched), derived snapshot "
        "%s -> %s",
        tier,
        len(rows),
        len(rows) - n_failed,
        n_failed,
        n_patched,
        derived_snapshot[:12],
        dest,
    )
    return StepOutcome(rows=len(rows), bytes_out=size, files=1)


__all__ = [
    "DERIVED_LAYER",
    "VERSIONS_COLUMNS",
    "VERSIONS_DDL",
    "VERSIONS_FILENAME",
    "ConceptError",
    "build_concept",
    "derived_dir",
    "derived_file",
    "derived_root",
    "ensure_registry",
    "latest_concept_lines",
    "prepare_sources",
    "register_derived_view",
    "run_concept",
    "run_concept_versions",
    "versions_path",
]
