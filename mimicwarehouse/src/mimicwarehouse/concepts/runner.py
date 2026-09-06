"""Concept runner — the DAG step handlers behind ``mwh build --tag concepts`` (EP-37
items 2-3; D-19, D-20, DESIGN §3/§5/§8).

Three callables, all ``(step, ctx)`` handlers or catalog extensions on connections the
runner / catalog builder own (this module opens nothing itself):

:func:`run_concept` (``python`` step ``concept.<group>.<name>``)
    Loads the concept from the committed inventory (:mod:`.inventory`), refuses a vendored
    file whose bytes drifted from the recorded ``sql_sha256``, strips upstream's
    ``DROP TABLE … ; CREATE TABLE mimiciv_derived.<x> AS`` header (:func:`strip_header`),
    exposes the tier's staged core tables and the concept's already-built dependencies as
    views on the build connection (:func:`ensure_source_views` — the same
    ``read_parquet`` relations the catalog uses, so the dev tier inherits the bucket
    filter and the catalog file itself is never needed), then ``COPY (SELECT …) TO``
    one ZSTD Parquet file — no bucket partitions (EP-33 amendment 3) — published by the
    rename-aside :func:`mimicwarehouse.publish.swap_dir` under the **per-tier derived
    layout** ``<lake_root(tier)>/derived/<tier>/mimiciv_derived/<name>/part-0.parquet``
    (dev and full share ``lake/``, so the tier segment keeps their materialisations
    apart; fixture/demo own their roots, EP-167). It appends the table's
    :class:`~mimicwarehouse.loader.manifest.ManifestLine` (``source_sha256`` = the SQL's
    sha256, ``raw_snapshot_id`` = the tier's core snapshot id), writes the ``status.json``
    entry ``mimiciv_derived.<name>`` with ``per_tier: true`` (dev completeness from the
    dev build only — :func:`mimicwarehouse.dag.snapshot.complete_for_tier`) and a
    per-tier ``tiers[<tier>]`` sub-entry (status, rows, bytes, build/run id, error
    class), measures itself with :class:`mimicwarehouse.run.ResourceLog` and appends one
    ``kind: concept`` benchmark line through :func:`mimicwarehouse.run.bench` (EP-35/36;
    ``run_id`` when the build runs under ``run.start``), and reports
    ``StepOutcome(layer="derived")`` so the runner records the derived snapshot id. A
    DuckDB failure leaves the previous table intact, records ``status: failed`` + the
    error class, appends an ``ok: false`` concept line and re-raises a
    :class:`ConceptError` whose message is **sanitized**
    (:func:`mimicwarehouse.safe.sanitize_error_text`) — engine errors quote cell values.
    **Patches (EP-38).** The SQL comes through :func:`resolve_concept_sql`: the registry
    patch (:mod:`.patching`, ``concepts/patches/<name>.sql``) when one exists for the
    concept, else the vendored file. :func:`checked_registry` validates the registry once
    per build and refuses every concept step while it does not match the EP-8 pin (a
    re-vendor forces a review); the executed SQL's sha256 and the ``patch_id`` go into
    the status entry, the manifest line (``source_sha256``), ``meta.concept_versions``
    and the run's refs (``concept`` + ``concept_patch``).

:func:`run_concept_versions` (``python`` step ``meta.concept_versions``)
    Writes ``<lake_root>/meta/<tier>/concept_versions.parquet`` (EP-29's meta layout):
    one row per concept **attempted** on the tier — concept, group, upstream_commit,
    sql_sha256 (of the SQL that ran: the patch's when patched), patch_id (the registry
    id when the concept ran patched, else NULL), rows, built_at, run_id, snapshot_id
    (the derived layer's), status (done / failed), error_class, build_id. Failures are
    recorded, never hidden.

:func:`register_derived` (``CATALOG_EXTENSIONS`` entry, EP-34 hook)
    The generic discovery walker every later P3 spec relies on: on the build connection
    of a tier catalog, every ``derived/<tier>/<schema>/<table>/part-0.parquet`` whose
    status entry is complete for the tier becomes a ``<schema>.<table>`` view (with a
    provenance comment), and every ``meta/<tier>/<table>.parquet`` a ``meta.<table>``
    table — except EP-29's ``profile_*`` pair, which is already folded into
    ``meta.columns`` / ``meta.row_counts`` and carries per-column extrema. The walker
    reads ``meta.catalog_info.lake_root`` (populated before the extensions run) instead
    of settings, so a catalog built into a temp root by a test discovers that root.

Everything written, logged or returned is DDL, paths, hashes, counts and timings —
never a row (GOVERNANCE §4). Import budget: not on the ``mwh`` start-up path;
``duckdb``, the contract, ``run`` and ``safe`` load inside the handlers.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mimicwarehouse import publish
from mimicwarehouse.concepts import patching
from mimicwarehouse.concepts.inventory import (
    DERIVED_SCHEMA,
    Concept,
    Inventory,
    InventoryError,
    load_inventory,
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

if TYPE_CHECKING:  # pragma: no cover
    import duckdb

    from mimicwarehouse.concepts.patching import Patch, PatchRegistry
    from mimicwarehouse.dag.runner import StepContext, StepOutcome
    from mimicwarehouse.dag.spec import Step

_LOG = logging.getLogger(__name__)

#: The per-tier derived layer (DESIGN §3): ``<lake_root(tier)>/derived/<tier>/…``.
DERIVED_LAYER = "derived"
#: One file per derived table (EP-33 amendment 3: no bucket partitions).
PART = "part-0.parquet"
#: ``meta.concept_versions`` ← ``<lake_root>/meta/<tier>/concept_versions.parquet``.
VERSIONS_TABLE = "concept_versions"
#: EP-29's profile pair is consumed by the catalog build, never re-exposed as tables.
META_EXCLUDED_PREFIX = "profile_"
#: Benchmark-ledger kind of the per-concept lines (``mwh runs benchmarks --kind concept``).
BENCH_KIND = "concept"

_HEADER = re.compile(
    r"^\s*(?:--[^\n]*\n\s*)*"
    r"DROP\s+TABLE\s+IF\s+EXISTS\s+mimiciv_derived\.([a-z][a-z0-9_]*)\s*;\s*"
    r"CREATE\s+TABLE\s+mimiciv_derived\.([a-z][a-z0-9_]*)\s+AS\s*",
    re.IGNORECASE,
)

_STATE_SOURCES = "concepts.sources"
_STATE_CORE_SNAPSHOT = "concepts.core_snapshot_id"
_STATE_REGISTRY = "concepts.patch_registry"


class ConceptError(RuntimeError):
    """A concept step cannot run or failed (sanitized message — never a cell value)."""


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------


def derived_dir(lake_root: Path | str, tier: str) -> Path:
    """``<lake_root>/derived/<tier>`` — the tier's derived layer."""
    return Path(lake_root) / DERIVED_LAYER / tier


def derived_table_dir(lake_root: Path | str, tier: str, schema: str, table: str) -> Path:
    """``<lake_root>/derived/<tier>/<schema>/<table>`` (one ``part-0.parquet`` inside)."""
    return derived_dir(lake_root, tier) / schema / table


def derived_part(lake_root: Path | str, tier: str, schema: str, table: str) -> Path:
    return derived_table_dir(lake_root, tier, schema, table) / PART


def versions_path(lake_root: Path | str, tier: str) -> Path:
    from mimicwarehouse.catalog.profile import meta_dir

    return meta_dir(lake_root, tier) / f"{VERSIONS_TABLE}.parquet"


def _sql_str(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def _parquet_ref(path: Path) -> str:
    return f"read_parquet({_sql_str(path.resolve().as_posix())})"


# ---------------------------------------------------------------------------
# Header stripping
# ---------------------------------------------------------------------------


def strip_header(sql: str) -> tuple[str, str]:
    """``(target name, SELECT body)`` of a vendored concept file: upstream's leading
    comment lines and the ``DROP TABLE IF EXISTS mimiciv_derived.<x>; CREATE TABLE
    mimiciv_derived.<x> AS`` header are removed, a trailing ``;`` too. Raises
    :class:`ConceptError` when the header is missing or its two names differ."""
    m = _HEADER.match(sql)
    if m is None:
        raise ConceptError(
            "no `DROP TABLE IF EXISTS mimiciv_derived.<x>; CREATE TABLE mimiciv_derived.<x> "
            "AS` header at the top of the file"
        )
    dropped, created = m.group(1).lower(), m.group(2).lower()
    if dropped != created:
        raise ConceptError(f"header drops {dropped} but creates {created}")
    body = sql[m.end() :].strip()
    while body.endswith(";"):
        body = body[:-1].rstrip()
    if not body:
        raise ConceptError(f"{created}: empty SELECT body")
    return created, body


# ---------------------------------------------------------------------------
# Status helpers (the per-tier entry)
# ---------------------------------------------------------------------------


def concept_entry(lake_root: Path | str, name: str) -> dict[str, Any] | None:
    return read_status(Path(lake_root))["steps"].get(f"{DERIVED_SCHEMA}.{name}")


def concept_complete(lake_root: Path | str, tier: str, name: str) -> bool:
    """Complete for ``tier`` per :func:`complete_for_tier` **and** the tier's file exists."""
    entry = concept_entry(lake_root, name)
    if entry is None or not complete_for_tier(entry, tier):
        return False
    return derived_part(lake_root, tier, DERIVED_SCHEMA, name).is_file()


def _record_status(
    ctx: StepContext,
    concept: Concept,
    *,
    status: str,
    rows: int | None = None,
    nbytes: int | None = None,
    error_class: str | None = None,
    sql_sha256: str | None = None,
    patch_id: str | None = None,
) -> None:
    """Merge this attempt into the per-tier status entry (module docstring).
    ``sql_sha256`` is the executed SQL's (the patch's when ``patch_id`` is set, EP-38);
    the vendored file's hash is kept beside it as ``vendored_sha256``."""
    qn = concept.qualified_name
    entry = concept_entry(ctx.lake_root, concept.name) or {}
    tiers = dict(entry.get("tiers") or {})
    tiers[ctx.tier] = {
        "status": status,
        "build_id": ctx.build_id,
        "run_id": ctx.run.run_id if ctx.run is not None else None,
        "rows": rows,
        "bytes": nbytes,
        "files": 1 if status == "done" else None,
        "finished_at": utc_now_iso(),
        "error_class": error_class,
        "sql_sha256": sql_sha256 or concept.sql_sha256,
        "vendored_sha256": concept.sql_sha256,
        "patch_id": patch_id,
    }
    fields: dict[str, Any] = {"per_tier": True, "layer": DERIVED_LAYER, "tiers": tiers}
    if status == "done":
        if ctx.tier == "dev":
            fields["dev_ready"] = True
            if entry.get("tier_complete") != "full":
                fields["tier_complete"] = "dev"
        else:  # fixture / demo (own lake roots) and full
            fields["tier_complete"] = "full"
    update_status(ctx.lake_root, qn, **fields)


# ---------------------------------------------------------------------------
# Source views on the build connection
# ---------------------------------------------------------------------------


def source_relation_sql(table: Any, lake_root: Path, buckets: list[int] | None) -> str:
    """The ``read_parquet`` relation of one staged core table (the EP-18/EP-21 reader
    fragments: partition glob + dev bucket filter, or the single dim file)."""
    from mimicwarehouse.loader.paths import read_parquet_sql, table_dir

    if table.partitioned:
        return read_parquet_sql(lake_root, table.schema_name, table.name, buckets)
    return _parquet_ref(table_dir(lake_root, table.schema_name, table.name) / PART)


def ensure_source_views(ctx: StepContext) -> int:
    """Once per build connection: ``CREATE OR REPLACE VIEW <schema>.<table>`` for every
    core table complete for the tier (the catalog's own admission rule), plus the three
    schemas. Returns the number of source views (cached in ``ctx.state``)."""
    if _STATE_SOURCES in ctx.state:
        return int(ctx.state[_STATE_SOURCES])
    from mimicwarehouse.catalog.build import STAGED_SCHEMAS, qualifies
    from mimicwarehouse.schema.contract import load_contract

    status = read_status(ctx.lake_root)["steps"]
    contract = load_contract()
    for schema in (*STAGED_SCHEMAS, DERIVED_SCHEMA):
        ctx.con.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
    n = 0
    for schema in STAGED_SCHEMAS:
        for table in contract.by_schema(schema):
            if not qualifies(status.get(table.qualified_name), ctx.tier):
                continue
            columns = ", ".join(f'"{c.name}"' for c in table.columns)
            relation = source_relation_sql(table, ctx.lake_root, ctx.buckets)
            ctx.con.execute(
                f'CREATE OR REPLACE VIEW {schema}."{table.name}" AS '
                f"SELECT {columns} FROM {relation}"
            )
            n += 1
    ctx.state[_STATE_SOURCES] = n
    _LOG.info("concepts: %d core source view(s) on the build connection (tier %s)", n, ctx.tier)
    return n


def ensure_derived_view(ctx: StepContext, name: str) -> None:
    """``mimiciv_derived.<name>`` view over the tier's built table; refuses (with the
    remedy) when the dependency is not complete for the tier."""
    if not concept_complete(ctx.lake_root, ctx.tier, name):
        raise ConceptError(
            f"dependency mimiciv_derived.{name} is not built for tier {ctx.tier} — run "
            f"`mwh build --tier {ctx.tier} --tag concepts` (or --select the step with "
            "--with-deps) first"
        )
    part = derived_part(ctx.lake_root, ctx.tier, DERIVED_SCHEMA, name)
    ctx.con.execute(
        f'CREATE OR REPLACE VIEW {DERIVED_SCHEMA}."{name}" AS SELECT * FROM {_parquet_ref(part)}'
    )


def _core_snapshot_id(ctx: StepContext) -> str:
    if _STATE_CORE_SNAPSHOT not in ctx.state:
        ctx.state[_STATE_CORE_SNAPSHOT] = layer_snapshot(
            ctx.lake_root, "core", ctx.tier, settings=ctx.settings
        )
    return str(ctx.state[_STATE_CORE_SNAPSHOT])


# ---------------------------------------------------------------------------
# run_concept
# ---------------------------------------------------------------------------


def load_concept_sql(concept: Concept) -> str:
    """The vendored file's text, after checking its bytes against the inventory."""
    from mimicwarehouse.concepts import vendored_path

    data = vendored_path(concept.path).read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != concept.sql_sha256:
        raise ConceptError(
            f"{concept.path}: vendored bytes differ from the inventory's sql_sha256 — "
            "re-run `python -m mimicwarehouse.concepts.inventory` after a re-vendor"
        )
    return data.decode("utf-8")


def checked_registry(ctx: StepContext) -> PatchRegistry:
    """The validated patch registry, once per build connection (cached in ``ctx.state``).
    A registry that does not validate — an entry whose ``applies_to_upstream_commit``
    differs from the EP-8 pin, a missing or drifted patch file, an orphan file — refuses
    the step (and, since every concept step asks, the whole concept build)."""
    if _STATE_REGISTRY not in ctx.state:
        try:
            ctx.state[_STATE_REGISTRY] = patching.check_registry()
        except patching.PatchError as exc:
            raise ConceptError(str(exc)) from None
    registry: PatchRegistry = ctx.state[_STATE_REGISTRY]
    return registry


def resolve_concept_sql(
    concept: Concept, registry: PatchRegistry | None = None
) -> tuple[str, str, Patch | None]:
    """``(sql text, sha256 of that text, patch)``: the registry patch's file when the
    concept has an entry (EP-38), else the vendored file through
    :func:`load_concept_sql` with ``patch`` None. ``registry`` defaults to a freshly
    validated one (:func:`mimicwarehouse.concepts.patching.check_registry`)."""
    registry = registry if registry is not None else patching.check_registry()
    patch = registry.for_concept(concept.name)
    if patch is None:
        return load_concept_sql(concept), concept.sql_sha256, None
    return patching.load_patch_sql(patch), patch.sql_sha256, patch


def _schema_hash(columns: list[tuple[str, str]]) -> str:
    blob = json.dumps([[n, t] for n, t in columns], separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()


def _bench(ctx: StepContext, step_name: str, *, wall_s: float, ok: bool, **fields: Any) -> None:
    from mimicwarehouse import run as run_mod

    run_mod.bench(
        BENCH_KIND,
        step_name,
        wall_s=wall_s,
        tier=ctx.tier,
        run_id=ctx.run.run_id if ctx.run is not None else None,
        build_id=ctx.build_id,
        settings=ctx.settings,
        ok=ok,
        **fields,
    )


def run_concept(step: Step, ctx: StepContext, inventory: Inventory | None = None) -> StepOutcome:
    """The ``concept.<group>.<name>`` handler (module docstring)."""
    import duckdb

    from mimicwarehouse.dag.runner import StepOutcome
    from mimicwarehouse.run import ResourceLog

    inventory = inventory or load_inventory()
    concept = inventory.for_step(step.name)
    registry = checked_registry(ctx)
    text, sql_sha, patch = resolve_concept_sql(concept, registry)
    patch_id = patch.patch_id if patch is not None else None
    target, body = strip_header(text)
    if target != concept.name:
        raise ConceptError(
            f"{concept.path}: header creates {target}, the inventory says {concept.name}"
        )
    ensure_source_views(ctx)
    for dep in concept.depends_on:
        ensure_derived_view(ctx, dep)

    dest = derived_table_dir(ctx.lake_root, ctx.tier, DERIVED_SCHEMA, concept.name)
    new = publish.new_path_for(dest)
    if new.exists():
        publish.rmtree(new)
    new.mkdir(parents=True, exist_ok=True)
    part_new = new / PART
    ref = _parquet_ref(part_new)

    def work() -> tuple[int, list[tuple[str, str]]]:
        ctx.con.execute(
            f"COPY ({body}) TO {_sql_str(part_new.resolve().as_posix())} "
            "(FORMAT PARQUET, COMPRESSION ZSTD)"
        )
        count = ctx.con.execute(f"SELECT count(*) FROM {ref}").fetchone()
        described = ctx.con.execute(f"DESCRIBE SELECT * FROM {ref}").fetchall()
        return int(count[0]) if count else 0, [(str(r[0]), str(r[1])) for r in described]

    try:
        (rows, columns), usage = ResourceLog.measure(work, data_root=ctx.settings.data_root)
    except duckdb.Error as exc:
        from mimicwarehouse.safe import sanitize_error_text

        publish.rmtree(new)
        error_class = type(exc).__name__
        _record_status(
            ctx,
            concept,
            status="failed",
            error_class=error_class,
            sql_sha256=sql_sha,
            patch_id=patch_id,
        )
        _bench(ctx, step.name, wall_s=0.0, ok=False, error=error_class)
        raise ConceptError(
            f"{concept.name}: {error_class}: {sanitize_error_text(str(exc))}"
        ) from None

    publish.swap_dir(new, dest)
    part = dest / PART
    nbytes = part.stat().st_size
    line = ManifestLine(
        schema=DERIVED_SCHEMA,
        table=concept.name,
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
        concept,
        status="done",
        rows=rows,
        nbytes=nbytes,
        sql_sha256=sql_sha,
        patch_id=patch_id,
    )
    _bench(
        ctx,
        step.name,
        wall_s=usage.wall_s,
        ok=True,
        peak_rss_mb=usage.peak_rss_mb,
        disk_delta_mb=usage.disk_delta_mb,
        rows=rows,
        bytes_out=nbytes,
        files=1,
    )
    if ctx.run is not None:
        ctx.run.record_ref("concept", concept.name, version=concept.upstream_commit, hash=sql_sha)
        if patch is not None:
            ctx.run.record_ref(
                "concept_patch",
                patch.patch_id,
                version=patch.applies_to_upstream_commit,
                hash=patch.sql_sha256,
            )
    _LOG.info(
        "concept %s%s: %s rows, %s bytes, wall=%.2fs (tier %s)",
        concept.name,
        f" [patch {patch_id}]" if patch_id else "",
        f"{rows:,}",
        f"{nbytes:,}",
        usage.wall_s,
        ctx.tier,
    )
    return StepOutcome(rows=rows, bytes_out=nbytes, files=1, layer=DERIVED_LAYER)


# ---------------------------------------------------------------------------
# meta.concept_versions
# ---------------------------------------------------------------------------

VERSIONS_COLUMNS: tuple[tuple[str, str], ...] = (
    ("concept", "VARCHAR"),
    ("group", "VARCHAR"),
    ("upstream_commit", "VARCHAR"),
    ("sql_sha256", "VARCHAR"),
    ("patch_id", "VARCHAR"),
    ("rows", "BIGINT"),
    ("built_at", "VARCHAR"),
    ("run_id", "VARCHAR"),
    ("snapshot_id", "VARCHAR"),
    ("status", "VARCHAR"),
    ("error_class", "VARCHAR"),
    ("build_id", "VARCHAR"),
)


def concept_versions_rows(
    lake_root: Path | str, tier: str, inventory: Inventory | None = None, *, settings: Any = None
) -> list[list[Any]]:
    """The ``meta.concept_versions`` rows for ``tier`` from ``status.json`` (one per
    concept attempted on the tier, inventory order)."""
    inventory = inventory or load_inventory()
    status = read_status(Path(lake_root))["steps"]
    snapshot_id = layer_snapshot(Path(lake_root), DERIVED_LAYER, tier, settings=settings)
    rows: list[list[Any]] = []
    for c in inventory.concepts:
        entry = status.get(c.qualified_name)
        attempt = (entry or {}).get("tiers", {}).get(tier)
        if not attempt:
            continue
        rows.append(
            [
                c.name,
                c.group,
                c.upstream_commit,
                attempt.get("sql_sha256") or c.sql_sha256,
                attempt.get("patch_id"),
                attempt.get("rows"),
                attempt.get("finished_at"),
                attempt.get("run_id"),
                snapshot_id,
                attempt.get("status"),
                attempt.get("error_class"),
                attempt.get("build_id"),
            ]
        )
    return rows


def write_concept_versions(
    con: duckdb.DuckDBPyConnection, lake_root: Path | str, tier: str, rows: list[list[Any]]
) -> tuple[Path, int]:
    """Write the rows as ``meta/<tier>/concept_versions.parquet`` (temp table + ``COPY`` +
    ``publish.replace``, the EP-29 profile shape); returns ``(path, bytes)``."""
    dest = versions_path(lake_root, tier)
    dest.parent.mkdir(parents=True, exist_ok=True)
    ddl = ", ".join(f'"{name}" {typ}' for name, typ in VERSIONS_COLUMNS)
    con.execute(f"CREATE OR REPLACE TEMP TABLE _mwh_concept_versions ({ddl})")
    try:
        if rows:
            placeholders = ", ".join("?" for _ in VERSIONS_COLUMNS)
            con.executemany(f"INSERT INTO _mwh_concept_versions VALUES ({placeholders})", rows)
        tmp = dest.with_name(dest.name + ".tmp")
        con.execute(
            f"COPY _mwh_concept_versions TO {_sql_str(tmp.resolve().as_posix())} "
            "(FORMAT PARQUET, COMPRESSION ZSTD)"
        )
        publish.replace(tmp, dest)
    finally:
        con.execute("DROP TABLE IF EXISTS _mwh_concept_versions")
    return dest, dest.stat().st_size


def run_concept_versions(step: Step, ctx: StepContext) -> StepOutcome:
    """The ``meta.concept_versions`` handler (module docstring)."""
    from mimicwarehouse.dag.runner import StepOutcome

    rows = concept_versions_rows(ctx.lake_root, ctx.tier, settings=ctx.settings)
    dest, nbytes = write_concept_versions(ctx.con, ctx.lake_root, ctx.tier, rows)
    if rows and ctx.run is not None:
        ctx.run.record_snapshot(DERIVED_LAYER, str(rows[0][8]))
    failed = sum(1 for r in rows if r[9] == "failed")
    _LOG.info(
        "meta.concept_versions (%s): %d concept(s) attempted, %d failed — %s",
        ctx.tier,
        len(rows),
        failed,
        dest,
    )
    return StepOutcome(rows=len(rows), bytes_out=nbytes, files=1)


# ---------------------------------------------------------------------------
# Catalog discovery (CATALOG_EXTENSIONS entry)
# ---------------------------------------------------------------------------


def _catalog_lake_root(con: duckdb.DuckDBPyConnection) -> Path:
    row = con.execute("SELECT lake_root FROM meta.catalog_info").fetchone()
    if row is None or not row[0]:
        raise ConceptError("meta.catalog_info carries no lake_root — extension order?")
    return Path(str(row[0]))


def _derived_comment(schema: str, table: str, tier: str, entry: dict[str, Any]) -> str:
    attempt = (entry.get("tiers") or {}).get(tier) or {}
    built = f"built {tier} by {attempt.get('build_id') or entry.get('build_id') or '?'}"
    if schema == DERIVED_SCHEMA:
        try:
            c = load_inventory().concept(table)
        except InventoryError:
            c = None
        if c is not None:
            patch_id = attempt.get("patch_id")
            patched = (
                f"; patch {patch_id} applied (executed sql sha256 "
                f"{str(attempt.get('sql_sha256') or '')[:12]}; EP-38)"
                if patch_id
                else ""
            )
            return (
                f"mimic-code concept {c.group}/{c.name} (MIT) at upstream "
                f"{c.upstream_commit[:12]}, sql sha256 {c.sql_sha256[:12]}{patched}; {built}; "
                "EP-37 concept runner."
            )
    return f"derived table {schema}.{table}; {built}; EP-37 discovery."


def register_derived(con: duckdb.DuckDBPyConnection, tier: str) -> None:
    """The discovery walker (module docstring): derived views + meta tables of ``tier``
    on the catalog build connection. Skips (with a warning) a table directory whose
    status entry is not complete for the tier; never creates an empty view."""
    lake_root = _catalog_lake_root(con)
    status = read_status(lake_root)["steps"]
    views = 0
    root = derived_dir(lake_root, tier)
    if root.is_dir():
        for schema_dir in sorted(p for p in root.iterdir() if p.is_dir()):
            schema = schema_dir.name
            con.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
            for table_dir in sorted(p for p in schema_dir.iterdir() if p.is_dir()):
                if table_dir.name.endswith((publish.NEW_SUFFIX, publish.OLD_SUFFIX)):
                    continue
                part = table_dir / PART
                if not part.is_file():
                    continue
                qn = f"{schema}.{table_dir.name}"
                entry = status.get(qn)
                if entry is None or not complete_for_tier(entry, tier):
                    _LOG.warning("catalog discovery: %s skipped — not complete for %s", qn, tier)
                    continue
                con.execute(
                    f'CREATE VIEW {schema}."{table_dir.name}" AS SELECT * FROM {_parquet_ref(part)}'
                )
                con.execute(
                    f'COMMENT ON VIEW {schema}."{table_dir.name}" IS '
                    f"{_sql_str(_derived_comment(schema, table_dir.name, tier, entry))}"
                )
                views += 1
    tables = 0
    from mimicwarehouse.catalog.profile import meta_dir

    mdir = meta_dir(lake_root, tier)
    if mdir.is_dir():
        con.execute("CREATE SCHEMA IF NOT EXISTS meta")
        for parquet in sorted(mdir.glob("*.parquet")):
            name = parquet.stem
            if name.startswith(META_EXCLUDED_PREFIX):
                continue
            con.execute(f'CREATE TABLE meta."{name}" AS SELECT * FROM {_parquet_ref(parquet)}')
            comment = (
                "One row per mimic-code concept attempted on this tier: upstream commit, "
                "sql sha256, patch id (EP-38), rows, built_at, run/build ids, the derived "
                "snapshot id, status and error class — EP-37."
                if name == VERSIONS_TABLE
                else f"meta table from lake/meta/{tier}/{parquet.name} (EP-37 discovery)."
            )
            con.execute(f'COMMENT ON TABLE meta."{name}" IS {_sql_str(comment)}')
            tables += 1
    _LOG.info(
        "catalog discovery (%s): %d derived view(s), %d meta table(s) under %s",
        tier,
        views,
        tables,
        lake_root,
    )


__all__ = [
    "BENCH_KIND",
    "DERIVED_LAYER",
    "META_EXCLUDED_PREFIX",
    "PART",
    "VERSIONS_COLUMNS",
    "VERSIONS_TABLE",
    "ConceptError",
    "checked_registry",
    "concept_complete",
    "concept_entry",
    "concept_versions_rows",
    "derived_dir",
    "derived_part",
    "derived_table_dir",
    "ensure_derived_view",
    "ensure_source_views",
    "load_concept_sql",
    "register_derived",
    "resolve_concept_sql",
    "run_concept",
    "run_concept_versions",
    "source_relation_sql",
    "strip_header",
    "versions_path",
    "write_concept_versions",
]
