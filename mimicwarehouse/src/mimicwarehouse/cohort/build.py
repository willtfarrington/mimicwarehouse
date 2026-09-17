"""Cohort materialisation — the ``cohorts.build`` DAG step, the per-tier marts layout, the
run record, the attrition accessor and the ``marts.cohorts`` registry (EP-47 items 2-3;
DESIGN §3/§9/§11/§15; GOVERNANCE §4/§5/§12; D-17, D-20, D-24, D-33).

:func:`run_build` (``python`` step ``cohorts.build``, ``dag/specs/cohorts.yaml``)
    Loads the cohort registry (packaged specs + the options' study directories; a frozen
    pair fails the step before anything is written), and for every selected spec whose
    ``(id, version, def_hash)`` is not already complete for the tier (or when forced):
    compiles the chain (:mod:`~mimicwarehouse.cohort.compiler`), binds every relation it
    reads on the build connection (the staged core through
    :func:`concepts.runner.ensure_source_views`; ``meta.codeset_members`` over EP-40's
    compiled members — refused with the remedy when a referenced set is not compiled on
    the tier with the resolved hash; ``phenotypes."<id>@<version>"`` over EP-41's built
    file — same rule; ``mimiciv_derived.<concept>`` through
    :func:`concepts.runner.ensure_derived_view`), and materialises it inside **one
    ``kind: cohort`` run per spec** (:func:`run.start`, EP-35): ``COPY`` of the ordered
    statement to ``cohort.parquet`` (ZSTD), the attrition statement executed once ->
    ``attrition.parquet`` (raw counts, data root only), ``spec.yaml`` (the registered
    file verbatim), ``manifest.json`` (spec def_hash, sql sha256, run id, snapshot ids of
    every layer read, rows / subjects, the cohort file's sha256, built_at), all staged
    under ``<dir>.new`` and published by :func:`publish.swap_dir` into the **per-tier
    marts layout** ``<lake_root>/marts/<tier>/cohorts/<id>@<version>/`` (EP-37's
    convention; versions coexist). The run records ``sql/cohort.sql`` and
    ``sql/attrition.sql``, the cohort / code-set / phenotype refs with hashes, the
    snapshot ids, a ``kind: mart`` benchmark line and the **suppressed** attrition chain
    (:func:`disclose.suppress` chain mode at the tier's ``k``: a run manifest is a
    session-readable surface — ``mwh runs show`` — so small cells and banded totals are
    blanked there; the raw counts stay in the mart). The manifest line (``schema``
    ``cohorts``, ``table`` ``<id>@<version>``, the marts path) joins the lake manifests,
    the status entry ``cohorts.<id>@<version>`` is per-tier, and the marts layer snapshot
    id is stamped through :func:`dag.snapshot.record_snapshot_once`.

    **Refusals.** An existing directory whose ``manifest.json`` carries a different
    ``def_hash`` for the same ``id@version`` refuses unless ``force`` (a definition that
    moved under a released version — the registry lock should already have caught it);
    a build whose ``def_hash`` matches and whose file exists is skipped unless ``force``.
    The step attempts every selected spec, records each failure (``status: failed`` +
    the error class) and raises the summary at the end (keep-going, EP-42 shape).

:func:`attrition` (EP-47 item 3)
    The attrition table of a built cohort — by ``id@version`` or by the run id of the
    build — as a polars frame ``step, label, polarity, kind, custom, n_units,
    n_subjects, dropped_units, dropped_subjects`` **after** :func:`disclose.suppress`
    (chain mode, ``k`` = the tier's, both count columns): a small total is ``None``, a
    small drop is withheld and its two neighbours banded to the nearest ten, marker
    columns say which (``*_suppressed`` / ``*_banded``; since EP-48 also
    ``dropped_*_small`` — the withheld drops that are themselves below k, which the
    diagram renderer draws as ``<k`` while a drop withheld beside a banded total is
    drawn as the rounded difference of the released totals). Never the raw parquet.

:func:`register_marts` (called by :func:`cohort.registry.register_cohorts`, a
``CATALOG_EXTENSIONS`` entry)
    ``marts.cohort_<id>_v<major>`` = the latest built patch of each ``(id, major)`` on the
    tier, a view over ``cohort.parquet`` (subject-keyed: sessions read it through
    ``safe_query`` as aggregates, EP-33 P3C-5), and the registry table ``marts.cohorts``
    (pre-registered in ``safe.REGISTRY_TABLES``, EP-33 B1c): one row per built
    ``id@version`` — def_hash, grain, tier, rows / n_subjects (blanked below ``k``,
    D-33), run / build ids, sql and cohort sha256, the snapshot ids, ``built_at``, the
    data-root-relative ``path`` and the session ``view`` it owns (NULL when a later
    patch supersedes it). Registry shape: no subject-level columns, label values <= 64
    characters.

Everything written, logged or returned is DDL, paths, hashes, counts and timings —
never a row (GOVERNANCE §4). Import budget: not on the ``mwh`` start-up path (the CLI
imports this module inside the command bodies); duckdb, polars, ``run``, ``disclose``
and the runner modules load inside the functions.
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

from mimicwarehouse import fsio, publish
from mimicwarehouse.cohort.compiler import (
    MEMBERS_TABLE,
    PHENOTYPES_SCHEMA,
    CompiledCohort,
    Step,
    compile_entry,
    sql_str,
)
from mimicwarehouse.cohort.registry import Entry, Registry, load_registry
from mimicwarehouse.cohort.spec import CohortSpecError, UnknownCohortSpecError
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
    import polars

    from mimicwarehouse.config import Settings
    from mimicwarehouse.dag.runner import StepContext, StepOutcome
    from mimicwarehouse.dag.spec import Step as DagStep

_LOG = logging.getLogger(__name__)


class CohortBuildError(CohortSpecError):
    """A cohort cannot be built on the tier (a relation it reads is not staged /
    compiled / built with the pinned hash, a DuckDB failure, a refused overwrite)."""


#: The DAG step / tag (``dag/specs/cohorts.yaml``).
STEP_BUILD = "cohorts.build"
DAG_TAG = "cohorts"
#: The lake layer and the per-tier directory: ``<lake_root>/marts/<tier>/cohorts/<ref>/``.
MARTS_LAYER = "marts"
SCHEMA = "cohorts"
#: The catalog schema / view prefix and the registry table.
MARTS_SCHEMA = "marts"
VIEW_PREFIX = "cohort_"
REGISTRY_TABLE = "cohorts"
#: The files of one built cohort.
COHORT_FILE = "cohort.parquet"
ATTRITION_FILE = "attrition.parquet"
SPEC_FILE = "spec.yaml"
MANIFEST_FILE = "manifest.json"
#: Run-ledger kind of the per-cohort runs and benchmark-ledger kind of their lines.
RUN_KIND = "cohort"
BENCH_KIND = "mart"
#: Free-text ceiling of a registry value (``safe.FREE_TEXT_MAX_CHARS`` mirrored).
VALUE_MAX_CHARS = 64
#: Chain-mode marker of a **small** drop (``0 < drop < k``, the primary case — EP-48
#: renders it ``<k``) beside ``dropped_*_suppressed``, which also covers drops withheld
#: only because a neighbouring total is banded or below k (EP-48 shows those as the
#: rounded difference of the released totals).
DROP_SMALL_SUFFIX = "_small"
#: ``attrition.parquet`` columns.
ATTRITION_COLUMNS: tuple[tuple[str, str], ...] = (
    ("step_index", "INTEGER"),
    ("step", "VARCHAR"),
    ("label", "VARCHAR"),
    ("polarity", "VARCHAR"),
    ("kind", "VARCHAR"),
    ("custom", "BOOLEAN"),
    ("n_units", "BIGINT"),
    ("n_subjects", "BIGINT"),
)


def _parquet_ref(path: Path) -> str:
    return f"read_parquet({sql_str(path.resolve().as_posix())})"


def _sql_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _cell(value: int | None, k: int) -> int | None:
    """``value`` unless it is a small cell (``1 .. k-1``), then None (D-33)."""
    if value is None:
        return None
    return None if 0 < value < k else value


def semver_key(version: str) -> tuple[int, ...]:
    return tuple(int(p) for p in version.split("."))


# ---------------------------------------------------------------------------
# Options (the CLI -> step channel) and the layout
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BuildOptions:
    """What ``mwh cohort build`` hands the step: an ``id@version`` selection (empty =
    every registered spec), the extra directories, ``force``."""

    select: tuple[str, ...] = ()
    extra_dirs: tuple[Path, ...] = ()
    codeset_dirs: tuple[Path, ...] = ()
    phenotype_dirs: tuple[Path, ...] = ()
    force: bool = False


_OPTIONS: ContextVar[BuildOptions | None] = ContextVar("mwh_cohorts_build", default=None)


@contextmanager
def build_options(
    *,
    select: Iterable[str] = (),
    extra_dirs: Iterable[Path | str] = (),
    codeset_dirs: Iterable[Path | str] = (),
    phenotype_dirs: Iterable[Path | str] = (),
    force: bool = False,
) -> Iterator[BuildOptions]:
    """Install :class:`BuildOptions` for the duration of a runner call in this thread."""
    options = BuildOptions(
        select=tuple(select),
        extra_dirs=tuple(Path(d) for d in extra_dirs),
        codeset_dirs=tuple(Path(d) for d in codeset_dirs),
        phenotype_dirs=tuple(Path(d) for d in phenotype_dirs),
        force=force,
    )
    token = _OPTIONS.set(options)
    try:
        yield options
    finally:
        _OPTIONS.reset(token)


def current_options() -> BuildOptions:
    return _OPTIONS.get() or BuildOptions()


def status_key(ref: str) -> str:
    """The ``status.json`` key of one built cohort: ``cohorts.<id>@<version>``."""
    return f"{SCHEMA}.{ref}"


def cohorts_dir(lake_root: Path | str, tier: str) -> Path:
    """``<lake_root>/marts/<tier>/cohorts``."""
    return Path(lake_root) / MARTS_LAYER / tier / SCHEMA


def cohort_dir(lake_root: Path | str, tier: str, ref: str) -> Path:
    """``<lake_root>/marts/<tier>/cohorts/<id>@<version>``."""
    return cohorts_dir(lake_root, tier) / ref


def cohort_part(lake_root: Path | str, tier: str, ref: str) -> Path:
    return cohort_dir(lake_root, tier, ref) / COHORT_FILE


def view_name(cohort_id: str, version: str) -> str:
    """``cohort_<id>_v<major>`` — the session view of a built cohort."""
    return f"{VIEW_PREFIX}{cohort_id}_v{semver_key(version)[0]}"


def cohort_entry(lake_root: Path | str, ref: str) -> dict[str, Any] | None:
    return read_status(Path(lake_root))["steps"].get(status_key(ref))


def cohort_attempt(lake_root: Path | str, tier: str, ref: str) -> dict[str, Any] | None:
    entry = cohort_entry(lake_root, ref)
    if entry is None:
        return None
    attempt = (entry.get("tiers") or {}).get(tier)
    return dict(attempt) if attempt else None


def cohort_complete(lake_root: Path | str, tier: str, ref: str) -> bool:
    """Complete for ``tier`` per :func:`complete_for_tier` **and** the file exists."""
    entry = cohort_entry(lake_root, ref)
    if entry is None or not complete_for_tier(entry, tier):
        return False
    return cohort_part(lake_root, tier, ref).is_file()


def read_mart_manifest(lake_root: Path | str, tier: str, ref: str) -> dict[str, Any] | None:
    """The mart's ``manifest.json`` (hashes, ids, counts), or None."""
    path = cohort_dir(lake_root, tier, ref) / MANIFEST_FILE
    if not path.is_file():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def _record_status(
    ctx: StepContext,
    entry: Entry,
    *,
    status: str,
    rows: int | None = None,
    n_subjects: int | None = None,
    nbytes: int | None = None,
    error_class: str | None = None,
    sql_sha256: str | None = None,
    run_id: str | None = None,
) -> None:
    """Merge this attempt into the per-tier status entry (the phenotype runner's shape)."""
    key = status_key(entry.ref)
    current = cohort_entry(ctx.lake_root, entry.ref) or {}
    tiers = dict(current.get("tiers") or {})
    tiers[ctx.tier] = {
        "status": status,
        "build_id": ctx.build_id,
        "run_id": run_id,
        "rows": rows,
        "n_subjects": n_subjects,
        "bytes": nbytes,
        "files": 1 if status == "done" else None,
        "finished_at": utc_now_iso(),
        "error_class": error_class,
        "sql_sha256": sql_sha256,
        "def_hash": entry.def_hash,
        "grain": entry.spec.grain,
        "refs": dict(entry.resolved),
    }
    fields: dict[str, Any] = {
        "per_tier": True,
        "layer": MARTS_LAYER,
        "tiers": tiers,
        "cohort_id": entry.spec.id,
        "version": entry.spec.version,
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
# Binding the relations a chain reads on the build connection
# ---------------------------------------------------------------------------


def _meta_parquet(ctx: StepContext, table: str) -> Path:
    from mimicwarehouse.units import meta_table_path

    return meta_table_path(ctx.lake_root, ctx.tier, table)


def _ensure_codesets(ctx: StepContext, entry: Entry) -> None:
    """``meta.codesets`` / ``meta.codeset_members`` views over the tier's compiled files;
    every referenced set must be compiled with the hash the registry resolved."""
    from mimicwarehouse.codesets.registry import CODESETS_TABLE
    from mimicwarehouse.codesets.registry import MEMBERS_TABLE as MEMBERS

    index = _meta_parquet(ctx, CODESETS_TABLE)
    members = _meta_parquet(ctx, MEMBERS)
    remedy = f"`mwh codeset compile --tier {ctx.tier}`"
    if not index.is_file() or not members.is_file():
        raise CohortBuildError(
            f"{entry.ref}: the code sets are not compiled on tier {ctx.tier} — run {remedy} first"
        )
    ctx.con.execute("CREATE SCHEMA IF NOT EXISTS meta")
    ctx.con.execute(
        f'CREATE OR REPLACE VIEW meta."{CODESETS_TABLE}" AS SELECT * FROM {_parquet_ref(index)}'
    )
    ctx.con.execute(
        f'CREATE OR REPLACE VIEW meta."{MEMBERS}" AS SELECT * FROM {_parquet_ref(members)}'
    )
    compiled = {
        f"{i}@{v}": str(h)
        for i, v, h in ctx.con.execute(
            f'SELECT codeset_id, version, def_hash FROM meta."{CODESETS_TABLE}"'
        ).fetchall()
    }
    for ref, expected in sorted(entry.resolved.get("codeset", {}).items()):
        have = compiled.get(ref)
        if have is None:
            raise CohortBuildError(
                f"{entry.ref}: code set {ref} is not compiled on tier {ctx.tier} — run "
                f"`mwh codeset compile --tier {ctx.tier} {ref}` first"
            )
        if have != expected:
            raise CohortBuildError(
                f"{entry.ref}: code set {ref} on tier {ctx.tier} was compiled with def_hash "
                f"{have[:12]}, the registry resolves {expected[:12]} — run "
                f"`mwh codeset compile --tier {ctx.tier} {ref}` first"
            )


def _ensure_phenotypes(ctx: StepContext, entry: Entry) -> bool:
    """``phenotypes."<id>@<version>"`` views over the tier's built files; every referenced
    phenotype must be built (``status: done``) with the resolved hash. Returns whether
    any was bound (the derived layer is then a read of the run)."""
    from mimicwarehouse.phenotypes.runner import (
        phenotype_attempt,
        phenotype_complete,
        phenotype_part,
    )

    refs = sorted(entry.resolved.get("phenotype", {}).items())
    if not refs:
        return False
    ctx.con.execute(f"CREATE SCHEMA IF NOT EXISTS {PHENOTYPES_SCHEMA}")
    for ref, expected in refs:
        attempt = phenotype_attempt(ctx.lake_root, ctx.tier, ref)
        remedy = f"`mwh phenotype compile {ref} --tier {ctx.tier}`"
        if attempt is None or not phenotype_complete(ctx.lake_root, ctx.tier, ref):
            raise CohortBuildError(
                f"{entry.ref}: phenotype {ref} is not built on tier {ctx.tier} — run {remedy} first"
            )
        if attempt.get("status") != "done":
            raise CohortBuildError(
                f"{entry.ref}: phenotype {ref} on tier {ctx.tier}: last attempt "
                f"{attempt.get('status')} — run {remedy} first"
            )
        have = str(attempt.get("def_hash") or "")
        if have != expected:
            raise CohortBuildError(
                f"{entry.ref}: phenotype {ref} on tier {ctx.tier} was built with def_hash "
                f"{have[:12]}, the registry resolves {expected[:12]} — run {remedy} --force first"
            )
        part = phenotype_part(ctx.lake_root, ctx.tier, ref)
        ctx.con.execute(
            f"CREATE OR REPLACE VIEW {PHENOTYPES_SCHEMA}.{_sql_ident(ref)} AS "
            f"SELECT * FROM {_parquet_ref(part)}"
        )
    return True


def bind_sources(ctx: StepContext, entry: Entry, compiled: CompiledCohort) -> set[str]:
    """Expose every relation ``compiled`` reads on the build connection (module
    docstring); returns the lake layers read (``core`` always, ``derived`` when a
    phenotype or a concept is read)."""
    from mimicwarehouse.concepts.runner import (
        ConceptError,
        ensure_derived_view,
        ensure_source_views,
    )

    ensure_source_views(ctx)
    layers = {"core"}
    present = {
        str(r[0])
        for r in ctx.con.execute(
            "SELECT table_schema || '.' || table_name FROM information_schema.tables"
        ).fetchall()
    }
    need_codesets = False
    need_phenotypes = False
    for source in compiled.sources:
        schema, _, name = source.partition(".")
        if source == MEMBERS_TABLE:
            need_codesets = True
        elif schema == PHENOTYPES_SCHEMA:
            need_phenotypes = True
        elif schema == "mimiciv_derived":
            try:
                ensure_derived_view(ctx, name)
            except ConceptError as exc:
                raise CohortBuildError(f"{entry.ref}: {exc}") from None
            layers.add("derived")
        elif schema in ("mimiciv_hosp", "mimiciv_icu"):
            if source not in present:
                raise CohortBuildError(
                    f"{entry.ref}: {source} is not staged for tier {ctx.tier} — run "
                    f"`mwh build --tier {ctx.tier} --select stage.{source}` first"
                )
        else:
            raise CohortBuildError(
                f"{entry.ref}: {source} is outside the schemas a cohort may read "
                "(mimiciv_hosp, mimiciv_icu, mimiciv_derived, meta.codeset_members, phenotypes)"
            )
    if need_codesets:
        _ensure_codesets(ctx, entry)
    if need_phenotypes and _ensure_phenotypes(ctx, entry):
        layers.add("derived")
    return layers


# ---------------------------------------------------------------------------
# Materialisation
# ---------------------------------------------------------------------------


def _schema_hash(columns: list[tuple[str, str]]) -> str:
    blob = json.dumps([[n, t] for n, t in columns], separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()


def _layer_snapshot_id(ctx: StepContext, layer: str) -> str:
    key = f"cohorts.{layer}_snapshot_id"
    if key not in ctx.state:
        ctx.state[key] = layer_snapshot(ctx.lake_root, layer, ctx.tier, settings=ctx.settings)
    return str(ctx.state[key])


def attrition_rows_raw(compiled: CompiledCohort, counts: list[tuple[Any, ...]]) -> list[list[Any]]:
    """The ``attrition.parquet`` rows (:data:`ATTRITION_COLUMNS`) from the attrition
    statement's result (``step_index, step, n_units, n_subjects``) and the steps."""
    by_name = {s.name: s for s in compiled.steps}
    rows: list[list[Any]] = []
    for step_index, name, n_units, n_subjects in sorted(counts, key=lambda r: int(r[0])):
        step = by_name[str(name)]
        rows.append(
            [
                int(step_index),
                step.name,
                step.label,
                step.polarity,
                step.kind,
                step.custom,
                int(n_units),
                int(n_subjects),
            ]
        )
    return rows


def suppress_attrition(
    rows: Iterable[dict[str, Any]], k: int
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """The chain-mode suppression of raw attrition rows (``step``, ``label``, ...,
    ``n_units``, ``n_subjects``): both count columns as chains, small totals ``None``,
    small drops withheld and their neighbours banded (``*_banded``), the drops derived
    only from two exact neighbours; ``dropped_*_small`` (EP-48) marks the withheld drops
    that are themselves below k. Returns ``(rows, report)`` with the counts of hidden /
    banded cells per column."""
    import polars as pl

    from mimicwarehouse.disclose import (
        BAND_SUFFIX,
        DROP_COLUMN,
        DROP_MARKER,
        MARKER_SUFFIX,
        suppress,
    )

    source = list(rows)
    if not source:
        return [], {}
    frame = pl.DataFrame(source)
    out: dict[str, list[Any]] = {
        "step": frame.get_column("step").to_list(),
        "label": frame.get_column("label").to_list(),
        "polarity": frame.get_column("polarity").to_list(),
        "kind": frame.get_column("kind").to_list(),
        "custom": frame.get_column("custom").to_list(),
    }
    report: dict[str, int] = {}
    for column, drop_name in (("n_units", "dropped_units"), ("n_subjects", "dropped_subjects")):
        chain = frame.select(["step", column])
        suppressed, rep = suppress(chain, k=k, count_cols=[column], mode="chain")
        out[column] = suppressed.get_column(column).to_list()
        out[f"{column}{MARKER_SUFFIX}"] = suppressed.get_column(
            f"{column}{MARKER_SUFFIX}"
        ).to_list()
        out[f"{column}{BAND_SUFFIX}"] = suppressed.get_column(f"{column}{BAND_SUFFIX}").to_list()
        out[drop_name] = suppressed.get_column(DROP_COLUMN).to_list()
        out[f"{drop_name}{MARKER_SUFFIX}"] = suppressed.get_column(DROP_MARKER).to_list()
        small_rows = {c.row for c in rep.cells if c.column == DROP_COLUMN and c.kind == "drop"}
        out[f"{drop_name}{DROP_SMALL_SUFFIX}"] = [i in small_rows for i in range(frame.height)]
        report[f"{column}_hidden"] = rep.n_primary + rep.n_complementary
        report[f"{column}_banded"] = rep.n_banded
    n = len(source)
    return [{key: values[i] for key, values in out.items()} for i in range(n)], report


def _manifest_attrition(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The run manifest's attrition rows: ``{step, label, n_units, n_subjects}`` with a
    hidden **or banded** cell as None (a banded value without its marker would read as
    exact)."""
    out: list[dict[str, Any]] = []
    for r in rows:
        out.append(
            {
                "step": r["step"],
                "label": r["label"][:VALUE_MAX_CHARS],
                "n_units": None if r["n_units_banded"] else r["n_units"],
                "n_subjects": None if r["n_subjects_banded"] else r["n_subjects"],
            }
        )
    return out


@dataclass(frozen=True, slots=True)
class BuildOutcome:
    """What one materialisation produced (counts, ids and paths only)."""

    ref: str
    rows: int
    n_subjects: int
    nbytes: int
    sql_sha256: str
    cohort_sha256: str
    run_id: str | None
    snapshot_ids: dict[str, str]
    steps: tuple[Step, ...]


def _refuse_overwrite(ctx: StepContext, entry: Entry, force: bool) -> None:
    """A directory built from a different definition under the same ``id@version``
    refuses unless forced (module docstring)."""
    existing = read_mart_manifest(ctx.lake_root, ctx.tier, entry.ref)
    if existing is None or force:
        return
    have = str(existing.get("def_hash") or "")
    if have and have != entry.def_hash:
        raise CohortBuildError(
            f"{entry.ref}: {cohort_dir(ctx.lake_root, ctx.tier, entry.ref)} was built from "
            f"def_hash {have[:12]} but the registry now resolves {entry.def_hash[:12]} — a "
            "released version changed (bump the version), or pass --force to replace it"
        )


def _materialize(
    ctx: StepContext, entry: Entry, compiled: CompiledCohort, layers: set[str]
) -> BuildOutcome:
    """COPY the compiled chain into the tier's cohort directory inside a ``kind: cohort``
    run (module docstring)."""
    import duckdb

    from mimicwarehouse import run as run_mod
    from mimicwarehouse.run import ResourceLog

    spec = entry.spec
    dest = cohort_dir(ctx.lake_root, ctx.tier, entry.ref)
    new = publish.new_path_for(dest)
    if new.exists():
        publish.rmtree(new)
    new.mkdir(parents=True, exist_ok=True)
    part_new = new / COHORT_FILE
    attrition_new = new / ATTRITION_FILE
    k = ctx.settings.k_suppression
    snapshot_ids = {layer: _layer_snapshot_id(ctx, layer) for layer in sorted(layers)}

    def work() -> tuple[int, list[tuple[Any, ...]], list[tuple[str, str]]]:
        ctx.con.execute(
            f"COPY ({compiled.sql}) TO {sql_str(part_new.resolve().as_posix())} "
            "(FORMAT PARQUET, COMPRESSION ZSTD)"
        )
        counts = ctx.con.execute(compiled.attrition_sql).fetchall()
        rows = attrition_rows_raw(compiled, counts)
        ddl = ", ".join(f'"{name}" {typ}' for name, typ in ATTRITION_COLUMNS)
        ctx.con.execute(f"CREATE OR REPLACE TEMP TABLE _mwh_attrition ({ddl})")
        try:
            placeholders = ", ".join("?" for _ in ATTRITION_COLUMNS)
            ctx.con.executemany(f"INSERT INTO _mwh_attrition VALUES ({placeholders})", rows)
            ctx.con.execute(
                "COPY (SELECT * FROM _mwh_attrition ORDER BY step_index) TO "
                f"{sql_str(attrition_new.resolve().as_posix())} (FORMAT PARQUET, COMPRESSION ZSTD)"
            )
        finally:
            ctx.con.execute("DROP TABLE IF EXISTS _mwh_attrition")
        described = ctx.con.execute(f"DESCRIBE SELECT * FROM {_parquet_ref(part_new)}").fetchall()
        n_rows = ctx.con.execute(f"SELECT count(*) FROM {_parquet_ref(part_new)}").fetchone()
        assert n_rows is not None
        return int(n_rows[0]), counts, [(str(r[0]), str(r[1])) for r in described]

    params = {
        "ref": entry.ref,
        "cohort_id": spec.id,
        "version": spec.version,
        "grain": spec.grain,
        "def_hash": entry.def_hash,
        "build_id": ctx.build_id,
        "tier": ctx.tier,
        "steps": list(compiled.step_names),
        "custom": compiled.custom,
    }
    with run_mod.start(
        f"cohort {entry.ref}", tier=ctx.tier, kind=RUN_KIND, params=params, settings=ctx.settings
    ) as prun:
        prun.record_sql("cohort", compiled.sql)
        prun.record_sql("attrition", compiled.attrition_sql)
        prun.record_ref("cohort", spec.id, version=spec.version, hash=entry.def_hash)
        for kind in ("codeset", "phenotype"):
            for ref, def_hash in sorted(entry.resolved.get(kind, {}).items()):
                ref_id, ref_version = ref.split("@", 1)
                prun.record_ref(kind, ref_id, version=ref_version, hash=def_hash)
        for layer, snapshot_id in snapshot_ids.items():
            prun.record_snapshot(layer, snapshot_id)
        try:
            (rows, counts, columns), usage = ResourceLog.measure(
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
                sql_sha256=compiled.sql_sha256,
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
            raise CohortBuildError(
                f"{entry.ref}: {error_class}: {sanitize_error_text(str(exc))}"
            ) from None
        raw_rows = [
            dict(zip([c for c, _t in ATTRITION_COLUMNS], r, strict=True))
            for r in attrition_rows_raw(compiled, counts)
        ]
        final = raw_rows[-1]
        n_subjects = int(final["n_subjects"])
        cohort_sha = sha256_streamed(part_new)
        built_at = utc_now_iso()
        mart_manifest = {
            "ref": entry.ref,
            "cohort_id": spec.id,
            "version": spec.version,
            "grain": spec.grain,
            "def_hash": entry.def_hash,
            "sql_sha256": compiled.sql_sha256,
            "cohort_sha256": cohort_sha,
            "run_id": prun.run_id,
            "build_id": ctx.build_id,
            "tier": ctx.tier,
            "snapshot_ids": snapshot_ids,
            "rows": rows,
            "n_subjects": n_subjects,
            "keys": list(compiled.keys),
            "columns": list(compiled.columns),
            "sources": list(compiled.sources),
            "refs": {k: dict(v) for k, v in entry.resolved.items()},
            "custom": compiled.custom,
            "steps": [s.to_dict() for s in compiled.steps],
            "attrition": raw_rows,
            "built_at": built_at,
            "writer_version": writer_version(),
        }
        fsio.atomic_write_text(new / SPEC_FILE, entry.path.read_text(encoding="utf-8"))
        fsio.atomic_write_text(
            new / MANIFEST_FILE, json.dumps(mart_manifest, indent=2, sort_keys=True) + "\n"
        )
        publish.swap_dir(new, dest)
        part = dest / COHORT_FILE
        nbytes = part.stat().st_size
        line = ManifestLine(
            schema=SCHEMA,
            table=entry.ref,
            path=lake_relative_posix(part, ctx.lake_root),
            sha256=cohort_sha,
            bytes=nbytes,
            rows=rows,
            schema_hash=_schema_hash(columns),
            writer_version=writer_version(),
            source_sha256=compiled.sql_sha256,
            raw_snapshot_id=snapshot_ids.get("core"),
            build_id=ctx.build_id,
            ts=built_at,
        )
        append_manifest(ctx.lake_root, ctx.build_id, [line])
        _record_status(
            ctx,
            entry,
            status="done",
            rows=rows,
            n_subjects=n_subjects,
            nbytes=nbytes,
            sql_sha256=compiled.sql_sha256,
            run_id=prun.run_id,
        )
        suppressed, report = suppress_attrition(raw_rows, k)
        prun.record_attrition(_manifest_attrition(suppressed))
        prun.manifest.params = {
            **params,
            "rows": _cell(rows, k),
            "rows_suppressed": _cell(rows, k) is None and rows != 0,
            "n_subjects": _cell(n_subjects, k),
            "n_subjects_suppressed": _cell(n_subjects, k) is None and n_subjects != 0,
            "k": k,
            "attrition_k": k,
            "attrition_cells_hidden": sum(v for kk, v in report.items() if kk.endswith("_hidden")),
            "attrition_cells_banded": sum(v for kk, v in report.items() if kk.endswith("_banded")),
            "sql_sha256": compiled.sql_sha256,
            "cohort_sha256": cohort_sha,
            "path": lake_relative_posix(dest, ctx.lake_root),
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
        for warning in compiled.warnings:
            prun.warn(warning)
        run_id = prun.run_id
    if ctx.run is not None:
        ctx.run.record_ref("cohort", spec.id, version=spec.version, hash=entry.def_hash)
    _LOG.info(
        "cohort %s (%s, grain %s): %s units, %s subjects, %s bytes, wall=%.2fs, run %s",
        entry.ref,
        ctx.tier,
        spec.grain,
        f"{rows:,}",
        f"{n_subjects:,}",
        f"{nbytes:,}",
        usage.wall_s,
        run_id,
    )
    return BuildOutcome(
        ref=entry.ref,
        rows=rows,
        n_subjects=n_subjects,
        nbytes=nbytes,
        sql_sha256=compiled.sql_sha256,
        cohort_sha256=cohort_sha,
        run_id=run_id,
        snapshot_ids=snapshot_ids,
        steps=compiled.steps,
    )


def build_cohort(
    ctx: StepContext, entry: Entry, registry: Registry, *, force: bool = False
) -> BuildOutcome | None:
    """Build one entry on the step context's tier: skip when already built with the
    same hash (unless ``force``), refuse a moved definition, else compile, bind and
    materialise. None when skipped."""
    attempt = cohort_attempt(ctx.lake_root, ctx.tier, entry.ref)
    if (
        not force
        and attempt is not None
        and attempt.get("status") == "done"
        and attempt.get("def_hash") == entry.def_hash
        and cohort_complete(ctx.lake_root, ctx.tier, entry.ref)
    ):
        _LOG.info(
            "cohort %s: already built for %s with def_hash %s — skipped (--force rebuilds)",
            entry.ref,
            ctx.tier,
            entry.def_hash[:12],
        )
        return None
    _refuse_overwrite(ctx, entry, force)
    compiled = compile_entry(entry, registry, ctx.tier)
    layers = bind_sources(ctx, entry, compiled)
    return _materialize(ctx, entry, compiled, layers)


def run_build(step: DagStep, ctx: StepContext) -> StepOutcome:
    """The ``cohorts.build`` handler (module docstring)."""
    from mimicwarehouse.dag.runner import StepOutcome
    from mimicwarehouse.dag.snapshot import record_snapshot_once

    options = current_options()
    registry = load_registry(
        options.extra_dirs,
        codeset_dirs=options.codeset_dirs,
        phenotype_dirs=options.phenotype_dirs,
    )
    selected = registry.select(options.select) if options.select else registry
    total_rows = 0
    total_bytes = 0
    built: list[str] = []
    skipped: list[str] = []
    failures: list[str] = []
    for entry in selected:
        try:
            outcome = build_cohort(ctx, entry, registry, force=options.force)
        except CohortSpecError as exc:
            # a compile / binding refusal or a DuckDB failure: recorded so the status
            # entry shows it (a DuckDB failure was already recorded by _materialize);
            # the other cohorts still run (keep-going, EP-42 shape)
            attempt = cohort_attempt(ctx.lake_root, ctx.tier, entry.ref) or {}
            if not (attempt.get("status") == "failed" and attempt.get("build_id") == ctx.build_id):
                _record_status(ctx, entry, status="failed", error_class=type(exc).__name__)
            _LOG.error("cohort %s (%s): %s", entry.ref, ctx.tier, exc)
            failures.append(f"{entry.ref}: {exc}")
            continue
        if outcome is None:
            skipped.append(entry.ref)
            continue
        total_rows += outcome.rows
        total_bytes += outcome.nbytes
        built.append(entry.ref)
    if built:
        snapshot_id = layer_snapshot(ctx.lake_root, MARTS_LAYER, ctx.tier, settings=ctx.settings)
        record_snapshot_once(
            ctx.lake_root,
            layer=MARTS_LAYER,
            tier=ctx.tier,
            snapshot_id=snapshot_id,
            build_id=ctx.build_id,
        )
    _LOG.info(
        "cohorts.build (%s): %d cohort(s) selected, %d built now, %d skipped, %d failed",
        ctx.tier,
        len(selected),
        len(built),
        len(skipped),
        len(failures),
    )
    if failures:
        raise CohortBuildError(
            f"{len(failures)} of {len(selected)} cohort(s) failed on tier {ctx.tier}: "
            + " | ".join(failures)
        )
    return StepOutcome(
        rows=total_rows,
        bytes_out=total_bytes,
        files=len(built),
        layer=MARTS_LAYER if built else None,
    )


# ---------------------------------------------------------------------------
# The attrition accessor (item 3)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Attrition:
    """``attrition()``'s result: the suppressed frame, the tier's ``k``, the build's ids
    and the suppression report (cells hidden / banded per column)."""

    ref: str
    tier: str
    k: int
    df: polars.DataFrame
    run_id: str | None
    def_hash: str | None
    report: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ref": self.ref,
            "tier": self.tier,
            "k": self.k,
            "run_id": self.run_id,
            "def_hash": self.def_hash,
            "report": dict(self.report),
            "rows": self.df.to_dicts(),
        }


def _resolve_ref(ref_or_run_id: str, tier: str, settings: Settings) -> tuple[str, str | None]:
    """``(ref, run_id)`` — a run id resolves through its manifest's params."""
    from mimicwarehouse.run import RUN_ID_RE, read_manifest

    if RUN_ID_RE.match(ref_or_run_id):
        manifest = read_manifest(ref_or_run_id, settings)
        if manifest.kind != RUN_KIND or "ref" not in manifest.params:
            raise CohortBuildError(f"run {ref_or_run_id} is not a cohort build")
        if manifest.tier != tier:
            raise CohortBuildError(f"run {ref_or_run_id} built on tier {manifest.tier}, not {tier}")
        return str(manifest.params["ref"]), ref_or_run_id
    from mimicwarehouse.cohort.spec import parse_ref

    parse_ref(ref_or_run_id)
    return ref_or_run_id, None


def attrition(
    ref_or_run_id: str,
    tier: str,
    *,
    k: int | None = None,
    settings: Settings | None = None,
) -> Attrition:
    """The suppressed attrition chain of a built cohort (module docstring). ``k`` may be
    lowered below the tier's only on the synthetic tiers (``fixture`` / ``demo``)."""
    import polars as pl

    from mimicwarehouse.config import get_settings

    settings = settings or get_settings()
    resolved_k = k if k is not None else settings.k_suppression
    if resolved_k < settings.k_suppression and tier in ("dev", "full"):
        raise CohortBuildError(
            f"k = {resolved_k} is below the credentialed floor {settings.k_suppression} on "
            f"tier {tier}"
        )
    ref, run_id = _resolve_ref(ref_or_run_id, tier, settings)
    lake_root = settings.lake_root(tier)
    path = cohort_dir(lake_root, tier, ref) / ATTRITION_FILE
    if not path.is_file():
        raise UnknownCohortSpecError(
            f"no built cohort {ref} on tier {tier} — run `mwh cohort build {ref} --tier {tier}` "
            "first"
        )
    mart = read_mart_manifest(lake_root, tier, ref) or {}
    if run_id is not None and mart.get("run_id") not in (None, run_id):
        _LOG.warning(
            "run %s built %s on %s, but the mart was rebuilt since by run %s — showing the "
            "current build",
            run_id,
            ref,
            tier,
            mart.get("run_id"),
        )
    raw = pl.read_parquet(path).sort("step_index")
    rows, report = suppress_attrition(raw.drop("step_index").to_dicts(), resolved_k)
    df = pl.DataFrame(rows)
    return Attrition(
        ref=ref,
        tier=tier,
        k=resolved_k,
        df=df,
        run_id=str(mart.get("run_id")) if mart.get("run_id") else None,
        def_hash=str(mart.get("def_hash")) if mart.get("def_hash") else None,
        report=report,
    )


# ---------------------------------------------------------------------------
# Catalog extension: marts.cohort_<id>_v<major> + marts.cohorts
# ---------------------------------------------------------------------------

REGISTRY_COLUMNS: tuple[tuple[str, str], ...] = (
    ("cohort_id", "VARCHAR"),
    ("version", "VARCHAR"),
    ("def_hash", "VARCHAR"),
    ("grain", "VARCHAR"),
    ("tier", "VARCHAR"),
    ("rows", "BIGINT"),
    ("rows_suppressed", "BOOLEAN"),
    ("n_subjects", "BIGINT"),
    ("n_subjects_suppressed", "BOOLEAN"),
    ("n_steps", "INTEGER"),
    ("custom", "BOOLEAN"),
    ("run_id", "VARCHAR"),
    ("build_id", "VARCHAR"),
    ("sql_sha256", "VARCHAR"),
    ("cohort_sha256", "VARCHAR"),
    ("snapshot_core", "VARCHAR"),
    ("snapshot_derived", "VARCHAR"),
    ("k", "INTEGER"),
    ("built_at", "VARCHAR"),
    ("path", "VARCHAR"),
    ("view", "VARCHAR"),
)

_REGISTRY_COMMENT = (
    "The cohort build registry (cohort/build.py, EP-47): one row per cohort id@version "
    "materialised on this tier under lake/marts/<tier>/cohorts/<id>@<version>/ - def_hash "
    "(the spec's, EP-46), grain, rows / n_subjects (NULL below k, *_suppressed), n_steps, "
    "custom (a custom_sql criterion), run_id (the kind: cohort run: sql/cohort.sql, "
    "sql/attrition.sql, the suppressed attrition, refs, snapshot ids), build_id, "
    "sql_sha256 / cohort_sha256 (a rebuild from the same spec + snapshot ids is "
    "byte-identical), the core / derived snapshot ids read, built_at, path (relative to "
    "the data root) and view: the session view marts.cohort_<id>_v<major> this build owns "
    "(the latest patch of its major; NULL when superseded). Registry read under safe_query "
    "(safe.REGISTRY_TABLES, EP-33 B1c); the views are subject-keyed - aggregates only."
)


def _catalog_lake_root(con: duckdb.DuckDBPyConnection) -> Path:
    row = con.execute("SELECT lake_root FROM meta.catalog_info").fetchone()
    if row is None or not row[0]:
        raise CohortBuildError("meta.catalog_info carries no lake_root — extension order?")
    return Path(str(row[0]))


def _catalog_k(con: duckdb.DuckDBPyConnection) -> int:
    try:
        row = con.execute("SELECT k_default FROM meta.catalog_info").fetchone()
    except Exception:  # pragma: no cover - an older catalog_info shape
        return 11
    return int(row[0]) if row and row[0] is not None else 11


def built_cohorts(lake_root: Path | str, tier: str) -> list[dict[str, Any]]:
    """Every built cohort of ``tier`` from its mart manifest, only those whose status
    entry is complete for the tier and whose file exists (id, then semver order)."""
    root = cohorts_dir(lake_root, tier)
    status = read_status(Path(lake_root))["steps"]
    out: list[dict[str, Any]] = []
    if not root.is_dir():
        return out
    for directory in sorted(p for p in root.iterdir() if p.is_dir()):
        if directory.name.endswith((publish.NEW_SUFFIX, publish.OLD_SUFFIX)):
            continue
        ref = directory.name
        entry = status.get(status_key(ref))
        if entry is None or not complete_for_tier(entry, tier):
            _LOG.warning("cohort discovery: %s skipped — not complete for %s", ref, tier)
            continue
        if not (directory / COHORT_FILE).is_file():
            continue
        manifest = read_mart_manifest(lake_root, tier, ref)
        if manifest is None:
            _LOG.warning("cohort discovery: %s skipped — no manifest.json", ref)
            continue
        out.append(manifest)
    out.sort(key=lambda m: (str(m.get("cohort_id")), semver_key(str(m.get("version", "0.0.0")))))
    return out


def registry_rows(
    manifests: list[dict[str, Any]],
    *,
    tier: str,
    k: int,
    data_root: Path | str,
    lake_root: Path | str,
) -> tuple[list[list[Any]], dict[str, str]]:
    """The ``marts.cohorts`` rows (:data:`REGISTRY_COLUMNS`) and ``{view: ref}`` of the
    views to create (the latest patch per ``(id, major)``)."""
    latest: dict[tuple[str, int], str] = {}
    for m in manifests:
        cohort_id, version = str(m["cohort_id"]), str(m["version"])
        key = (cohort_id, semver_key(version)[0])
        if key not in latest or semver_key(version) > semver_key(latest[key]):
            latest[key] = version
    views: dict[str, str] = {}
    rows: list[list[Any]] = []
    for m in manifests:
        cohort_id, version = str(m["cohort_id"]), str(m["version"])
        ref = f"{cohort_id}@{version}"
        owns = latest[(cohort_id, semver_key(version)[0])] == version
        view = view_name(cohort_id, version) if owns else None
        if view is not None:
            views[view] = ref
        snapshots = m.get("snapshot_ids") or {}
        rows_n = m.get("rows")
        subjects_n = m.get("n_subjects")
        directory = cohort_dir(lake_root, tier, ref)
        try:
            rel = directory.resolve().relative_to(Path(data_root).resolve()).as_posix()
        except ValueError:
            rel = directory.as_posix()
        rows.append(
            [
                cohort_id,
                version,
                m.get("def_hash"),
                m.get("grain"),
                tier,
                _cell(rows_n, k),
                rows_n is not None and _cell(rows_n, k) is None and rows_n != 0,
                _cell(subjects_n, k),
                subjects_n is not None and _cell(subjects_n, k) is None and subjects_n != 0,
                len(m.get("steps") or []),
                bool(m.get("custom")),
                m.get("run_id"),
                m.get("build_id"),
                m.get("sql_sha256"),
                m.get("cohort_sha256"),
                snapshots.get("core"),
                snapshots.get("derived"),
                k,
                m.get("built_at"),
                rel,
                view,
            ]
        )
    return rows, views


def register_marts(con: duckdb.DuckDBPyConnection, tier: str) -> None:
    """The catalog extension body (module docstring): the session views and the
    ``marts.cohorts`` registry on the build connection. DDL + registry text only."""
    lake_root = _catalog_lake_root(con)
    data_root = lake_root.parent
    if lake_root.name != "lake":  # per-tier lake roots (lake/fixture, lake/demo)
        data_root = lake_root.parent.parent
    k = _catalog_k(con)
    manifests = built_cohorts(lake_root, tier)
    rows, views = registry_rows(manifests, tier=tier, k=k, data_root=data_root, lake_root=lake_root)
    con.execute(f"CREATE SCHEMA IF NOT EXISTS {MARTS_SCHEMA}")
    ddl = ", ".join(f'"{name}" {typ}' for name, typ in REGISTRY_COLUMNS)
    con.execute(f"DROP TABLE IF EXISTS {MARTS_SCHEMA}.{REGISTRY_TABLE}")
    con.execute(f"CREATE TABLE {MARTS_SCHEMA}.{REGISTRY_TABLE} ({ddl})")
    if rows:
        placeholders = ", ".join("?" for _ in REGISTRY_COLUMNS)
        con.executemany(
            f"INSERT INTO {MARTS_SCHEMA}.{REGISTRY_TABLE} VALUES ({placeholders})", rows
        )
    con.execute(f"COMMENT ON TABLE {MARTS_SCHEMA}.{REGISTRY_TABLE} IS {sql_str(_REGISTRY_COMMENT)}")
    for view, ref in sorted(views.items()):
        part = cohort_part(lake_root, tier, ref)
        con.execute(
            f"CREATE OR REPLACE VIEW {MARTS_SCHEMA}.{_sql_ident(view)} AS "
            f"SELECT * FROM {_parquet_ref(part)}"
        )
        con.execute(
            f"COMMENT ON VIEW {MARTS_SCHEMA}.{_sql_ident(view)} IS "
            + sql_str(
                f"Cohort {ref} (EP-47), the latest built patch of its major on tier {tier}: one "
                "row per unit of the grain - the grain keys, index_time, era_index, "
                "age_at_index (capped at 91) + age_capped, obs_start / obs_end (the observation "
                "window), follow_up_end + censor_reason (death / discharge_alive / horizon / "
                "dod_visibility), custom_flag. marts.cohorts carries the hashes and ids; the "
                "attrition chain is `mwh cohort attrition`. Subject-keyed: read through "
                "safe_query as aggregates."
            )
        )
    _LOG.info(
        "catalog extension cohorts (%s): %d built cohort(s) in marts.cohorts, %d view(s)",
        tier,
        len(rows),
        len(views),
    )


__all__ = [
    "ATTRITION_COLUMNS",
    "ATTRITION_FILE",
    "BENCH_KIND",
    "COHORT_FILE",
    "DAG_TAG",
    "DROP_SMALL_SUFFIX",
    "MANIFEST_FILE",
    "MARTS_LAYER",
    "MARTS_SCHEMA",
    "REGISTRY_COLUMNS",
    "REGISTRY_TABLE",
    "RUN_KIND",
    "SCHEMA",
    "SPEC_FILE",
    "STEP_BUILD",
    "VIEW_PREFIX",
    "Attrition",
    "BuildOptions",
    "BuildOutcome",
    "CohortBuildError",
    "attrition",
    "attrition_rows_raw",
    "bind_sources",
    "build_cohort",
    "build_options",
    "built_cohorts",
    "cohort_attempt",
    "cohort_complete",
    "cohort_dir",
    "cohort_entry",
    "cohort_part",
    "cohorts_dir",
    "current_options",
    "read_mart_manifest",
    "register_marts",
    "registry_rows",
    "run_build",
    "semver_key",
    "status_key",
    "suppress_attrition",
    "view_name",
]
