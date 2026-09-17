"""Events spine — one long, MEDS-shaped event table over the core lake (EP-50; DESIGN
§10, §3 note; ``docs/methods/spine.md`` is the prose twin).

Care-pathway analysis (EP-83) and, optionally, MEDS / ACES tooling need **one** long table
``(subject_id, time, code, numeric_value, text_value, hadm_id, stay_id, source_table)``
instead of a dozen wide ones. This module is that table's build, its registry, its
governance checks and its catalog registration:

* **The registry** — :data:`SPINE_SOURCES`: one :class:`SpineSource` per core table the
  spine reads (thirteen: ``patients`` … ``procedureevents``; raw ``chartevents`` is
  excluded in v1 for size, DESIGN §21). Each source is a pure SQL *projection* over
  ``{<table>}`` placeholders (:meth:`SpineSource.select_sql`) that emits the spine
  columns in order, plus the :class:`CodeRule` rows the methods page renders. The code
  grammar is ``PREFIX//segment//segment`` (:data:`SEP`), a missing segment is
  :data:`NONE`, free-text-ish segments (drug / medication names, eMAR event text) are cut
  so every code stays within :data:`CODE_MAX_CHARS` = the safe-query free-text bound —
  which is what lets a session ``GROUP BY code`` through ``mwh sql`` (P3C-5).
* **The build** — the DAG ``python`` steps of ``dag/specs/spine.yaml`` (EP-19/EP-29
  handler contract, ``(step, ctx)``): :func:`build_source` (``spine.<source>``) writes
  ``<lake_root(tier)>/derived/<tier>/spine/source=<source>/subject_bucket=NN/part-0.parquet``
  — the one bucketed exception to EP-37's single-file derived rule — **one sorted
  single-file ``COPY`` per bucket** (the loader's pass-2 shape, DESIGN §5; the core
  read is Hive-pruned to that bucket's directory, so a source is still read once): a
  sorted ``COPY … PARTITION_BY`` is *not* order-preserving inside its partition files on
  DuckDB 1.5.5 under 12 threads, whatever ``preserve_insertion_order`` says
  (``docs/gotchas.md`` §1), publishes through :func:`mimicwarehouse.publish.swap_dir`, appends one
  :class:`~mimicwarehouse.loader.manifest.ManifestLine` per file (schema ``spine``,
  ``source_sha256`` = the projection's sha256, ``raw_snapshot_id`` = the core snapshot
  id), writes the per-tier ``status.json`` entry ``spine.<source>`` (``per_tier``, the
  EP-37 rule, so the runner resumes per source and ``--select spine.<source>`` works)
  and one ``kind: mart`` benchmark line through :func:`mimicwarehouse.run.bench`.
  :func:`build_union` (``spine.union``) reads every source complete for the tier, writes
  ``meta.spine_codes`` (code prefix x source: ``n_events`` / ``n_subjects``, k-suppressed
  **at build time** through :func:`mimicwarehouse.disclose.suppress` because ``meta.*``
  is a registry exemption under ``safe_query`` — the EP-39 precedent, D-33 addendum; raw
  counts stay under ``lake/meta/<tier>/raw/``), runs :func:`validate`, appends the
  ``meta.spine_validation`` row and records the ``mimiciv_derived.spine`` status entry
  the catalog extension reads.
* **Validation** — :func:`validate`: the MEDS core schema per file (hard-coded
  :data:`MEDS_COLUMNS`; pyarrow types, the ``meds`` package is **not** a dependency),
  ``time`` non-null except ``MEDS_BIRTH``, every ``subject_id`` present in ``patients``,
  no ``text_value`` / ``code`` over :data:`TEXT_MAX_CHARS` / :data:`CODE_MAX_CHARS` and no
  newline, ``(subject_id, time)`` monotone within every file, and the static
  :func:`check_source` walk that refuses a projection reading a denied column
  (:data:`DENIED_COLUMNS` — ``comments`` / ``text`` / ``note`` — or any contract
  ``free_text`` column, GOVERNANCE §4/§9).
* **Registration** — :func:`register_spine` (a ``CATALOG_EXTENSIONS`` entry): the view
  ``mimiciv_derived.spine`` over every complete source directory (MEDS column order first),
  once the union step recorded the tier complete; comments on the two ``meta`` tables
  EP-37's discovery walker registers from ``lake/meta/<tier>/``. The walker itself skips
  the ``spine`` directory (:data:`SPINE_DIRNAME`) — it is not a schema.

Time semantics are ``timesem``'s (EP-34): ``MEDS_BIRTH`` is a **synthetic** birth time
(``anchor_year - anchor_age``, January 1st; ``text_value = 'age_capped'`` where
``anchor_age`` sits in the ``>= 89`` bucket shipped as :data:`~mimicwarehouse.timesem.AGE_CAP`),
``MEDS_DEATH`` is ``patients.dod`` with its ~one-year visibility horizon
(``timesem.DOD_VISIBILITY_DAYS``; the event exists only where ``dod`` is recorded),
diagnoses carry no timestamp and are placed at the admission's ``dischtime``, ``DATE``
columns (``dod``, ``procedures_icd.chartdate``) are day-resolution (final-roadmap TIME-1),
and every timestamp is the per-patient shifted naive ``TIMESTAMP`` as shipped — fine
within a subject, meaningless across subjects.

Everything written, logged, printed or returned is DDL, paths, hashes, counts, timings
and suppressed aggregates — never a row (GOVERNANCE §4). Import budget: ``cli.py`` loads
this module at start-up for the ``spine`` sub-app, so duckdb / polars / pyarrow / the
contract / ``run`` / ``safe`` / ``disclose`` load only inside function bodies.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import re
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import typer

from mimicwarehouse.timesem import AGE_CAP

if TYPE_CHECKING:  # pragma: no cover
    import duckdb
    import polars
    import pyarrow

    from mimicwarehouse.cli import CliState
    from mimicwarehouse.config import Settings
    from mimicwarehouse.dag.runner import StepContext, StepOutcome
    from mimicwarehouse.dag.spec import Step

_LOG = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants — layout, columns, grammar bounds
# ---------------------------------------------------------------------------

#: ``<lake_root(tier)>/derived/<tier>/spine/`` — the bucketed exception to EP-37's
#: single-file derived layout; the discovery walker skips a directory of this name.
SPINE_DIRNAME = "spine"
DERIVED_LAYER = "derived"
DERIVED_SCHEMA = "mimiciv_derived"
#: The view the catalog extension creates: ``mimiciv_derived.spine``.
VIEW_NAME = "spine"
#: The union step's ``status.json`` entry (per tier); the per-source entries are
#: ``spine.<source>`` (:attr:`SpineSource.status_key`).
STATUS_KEY = f"{DERIVED_SCHEMA}.{VIEW_NAME}"
STATUS_PREFIX = "spine."
#: ``meta.spine_codes`` / ``meta.spine_validation`` ← ``lake/meta/<tier>/<name>.parquet``.
CODES_TABLE = "spine_codes"
VALIDATION_TABLE = "spine_validation"
RAW_DIRNAME = "raw"
PART = "part-0.parquet"
BUCKET_COLUMN = "subject_bucket"
NUM_BUCKETS = 100
SOURCE_PARTITION = "source"
#: DAG naming (``dag/specs/spine.yaml``): ``spine.<source>`` steps, the union step, the tag.
STEP_PREFIX = "spine."
UNION_STEP = "spine.union"
SPEC_NAME = "spine"
TAG = "spine"
#: Benchmark-ledger kind of the per-source and union lines (``mwh runs benchmarks --kind mart``).
BENCH_KIND = "mart"

#: Code grammar: ``PREFIX//segment//segment``; a missing segment is ``NONE``.
SEP = "//"
NONE = "NONE"
#: MEDS-standard static codes (MEDS 0.4 conventions).
BIRTH_CODE = "MEDS_BIRTH"
DEATH_CODE = "MEDS_DEATH"
AGE_CAPPED_TEXT = "age_capped"
#: The safe-query free-text bound (``safe.FREE_TEXT_MAX_CHARS``), mirrored rather than
#: imported (this module is on the ``mwh`` start-up path; ``test_ep50`` asserts equality):
#: every ``code`` and ``text_value`` stays within it so sessions can aggregate over both.
CODE_MAX_CHARS = 64
TEXT_MAX_CHARS = 64
#: Lab ``value`` text is carried only when non-numeric and at most this long (the brief).
LAB_TEXT_MAX_CHARS = 32
#: Segment cuts that keep every code within :data:`CODE_MAX_CHARS`.
DRUG_MAX_CHARS = 46  # "MEDICATION_START//" (18) + 46
EMAR_MEDICATION_MAX_CHARS = 34  # "EMAR//" (6) + 34 + "//" (2) + 22
EMAR_EVENT_MAX_CHARS = 22
#: Column names a projection may never read (free text; GOVERNANCE §4/§9) — beside the
#: contract's own ``free_text`` flags, which :func:`check_source` adds per table.
DENIED_COLUMNS: frozenset[str] = frozenset({"comment", "comments", "text", "note", "notes"})

#: The MEDS 0.4 core columns in order: (name, DuckDB type, pyarrow type). ``time`` is a
#: microsecond timestamp, ``numeric_value`` float32, ``subject_id`` int64 — extra columns
#: are allowed by the standard and follow (:data:`EXTRA_COLUMNS`).
MEDS_VERSION = "0.4"
MEDS_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("subject_id", "BIGINT", "int64"),
    ("time", "TIMESTAMP", "timestamp[us]"),
    ("code", "VARCHAR", "string"),
    ("numeric_value", "FLOAT", "float"),
    ("text_value", "VARCHAR", "string"),
)
#: The project's extra columns (DESIGN §10): the finer keys and the provenance column.
EXTRA_COLUMNS: tuple[tuple[str, str], ...] = (
    ("hadm_id", "INTEGER"),
    ("stay_id", "INTEGER"),
    ("source_table", "VARCHAR"),
)
SPINE_COLUMNS: tuple[tuple[str, str], ...] = tuple((n, t) for n, t, _ in MEDS_COLUMNS) + (
    EXTRA_COLUMNS
)
COLUMN_NAMES: tuple[str, ...] = tuple(n for n, _ in SPINE_COLUMNS)
#: The within-file sort — ``(subject_id, time)`` with a total tie-break (EP-169 rule).
ORDER_BY: tuple[str, ...] = (
    "subject_id",
    "time",
    "code",
    "hadm_id",
    "stay_id",
    "numeric_value",
    "text_value",
)

METHODS_DOC_RELPATH = Path("docs") / "methods" / "spine.md"
GRAMMAR_MARK = ("<!-- grammar:begin -->", "<!-- grammar:end -->")
MEDS_MARK = ("<!-- meds:begin -->", "<!-- meds:end -->")

_STATE_CORE_SNAPSHOT = "spine.core_snapshot_id"
_BUCKET_DIR = re.compile(rf"^{BUCKET_COLUMN}=(\d+)$")


class SpineError(RuntimeError):
    """A spine step cannot run, a projection is refused, or validation failed
    (sanitized message — never a cell value)."""


# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CodeRule:
    """One row of the code grammar (rendered into the methods page)."""

    code: str
    time: str
    numeric: str = "-"
    text: str = "-"
    note: str = ""


@dataclass(frozen=True, slots=True)
class SpineSource:
    """One core table's projection into the spine.

    ``projection`` is a SELECT template over ``{<table>}`` placeholders (the main table
    and every join) that yields exactly :data:`COLUMN_NAMES` in order with the
    :data:`SPINE_COLUMNS` types; :meth:`select_sql` renders it against a relation map
    (catalog names by default, bucket-filtered ``read_parquet`` fragments in the build).
    """

    name: str
    schema_name: str
    table: str
    projection: str
    rules: tuple[CodeRule, ...]
    joins: tuple[tuple[str, str], ...] = ()
    excluded: tuple[str, ...] = ()
    description: str = ""

    @property
    def qualified_name(self) -> str:
        return f"{self.schema_name}.{self.table}"

    @property
    def tables(self) -> tuple[tuple[str, str], ...]:
        """``(schema, table)`` of the main table and every join, main first."""
        return ((self.schema_name, self.table), *self.joins)

    @property
    def step_name(self) -> str:
        return f"{STEP_PREFIX}{self.name}"

    @property
    def status_key(self) -> str:
        return f"{STATUS_PREFIX}{self.name}"

    @property
    def stage_steps(self) -> tuple[str, ...]:
        """The stage steps the source depends on (``stage.<schema>.<table>``)."""
        return tuple(f"stage.{s}.{t}" for s, t in self.tables)

    def select_sql(self, relations: Mapping[str, str] | None = None) -> str:
        """The projection over ``relations`` (``{table: relation SQL}``; default the
        catalog names), wrapped so the column set / order is fixed and null-time rows
        are dropped — except ``MEDS_BIRTH``, the one static event MEDS lets be timeless."""
        rel = dict(catalog_relations()) if relations is None else dict(relations)
        missing = [t for _, t in self.tables if t not in rel]
        if missing:
            raise SpineError(f"{self.name}: no relation for table(s) {missing}")
        body = self.projection.format_map(rel)
        columns = ", ".join(f"e.{c}" for c in COLUMN_NAMES)
        return (
            f"SELECT {columns} FROM ({body}) AS e "
            f"WHERE e.time IS NOT NULL OR e.code = '{BIRTH_CODE}'"
        )

    @property
    def sql_sha256(self) -> str:
        """sha256 of the catalog-form projection — the manifest's ``source_sha256``."""
        return hashlib.sha256(self.select_sql().encode("utf-8")).hexdigest()


def _cols(
    *,
    subject: str,
    time: str,
    code: str,
    numeric: str = "NULL",
    text: str = "NULL",
    hadm: str = "NULL",
    stay: str = "NULL",
    source: str,
) -> str:
    """The eight typed output expressions of one projection branch."""
    return (
        f"CAST({subject} AS BIGINT) AS subject_id, CAST({time} AS TIMESTAMP) AS time, "
        f"CAST({code} AS VARCHAR) AS code, CAST({numeric} AS FLOAT) AS numeric_value, "
        f"CAST({text} AS VARCHAR) AS text_value, CAST({hadm} AS INTEGER) AS hadm_id, "
        f"CAST({stay} AS INTEGER) AS stay_id, '{source}' AS source_table"
    )


def _seg(expr: str, max_chars: int | None = None) -> str:
    """A code segment: ``NONE`` when NULL, optionally cut to ``max_chars``."""
    inner = f"left({expr}, {max_chars})" if max_chars is not None else expr
    return f"coalesce(CAST({inner} AS VARCHAR), '{NONE}')"


_PATIENTS_SQL = (
    "SELECT "
    + _cols(
        subject="p.subject_id",
        time=("make_date(CAST(p.anchor_year AS INTEGER) - CAST(p.anchor_age AS INTEGER), 1, 1)"),
        code=f"'{BIRTH_CODE}'",
        text=f"CASE WHEN p.anchor_age >= {AGE_CAP} THEN '{AGE_CAPPED_TEXT}' END",
        source="patients",
    )
    + " FROM {patients} p WHERE p.anchor_age IS NOT NULL"
    " UNION ALL SELECT "
    + _cols(subject="p.subject_id", time="p.dod", code=f"'{DEATH_CODE}'", source="patients")
    + " FROM {patients} p WHERE p.dod IS NOT NULL"
)

_ADMISSIONS_SQL = (
    "SELECT "
    + _cols(
        subject="a.subject_id",
        time="a.admittime",
        code=f"'HOSPITAL_ADMISSION{SEP}' || {_seg('a.admission_type')}",
        text="a.admission_location",
        hadm="a.hadm_id",
        source="admissions",
    )
    + " FROM {admissions} a"
    " UNION ALL SELECT "
    + _cols(
        subject="a.subject_id",
        time="a.dischtime",
        code=f"'HOSPITAL_DISCHARGE{SEP}' || {_seg('a.discharge_location')}",
        hadm="a.hadm_id",
        source="admissions",
    )
    + " FROM {admissions} a WHERE a.dischtime IS NOT NULL"
)

_TRANSFERS_SQL = (
    "SELECT "
    + _cols(
        subject="t.subject_id",
        time="t.intime",
        code=f"'TRANSFER_TO{SEP}' || {_seg('t.careunit')}",
        text="t.eventtype",
        hadm="t.hadm_id",
        source="transfers",
    )
    + " FROM {transfers} t"
)

_ICUSTAYS_SQL = (
    "SELECT "
    + _cols(
        subject="i.subject_id",
        time="i.intime",
        code=f"'ICU_ADMISSION{SEP}' || {_seg('i.first_careunit')}",
        hadm="i.hadm_id",
        stay="i.stay_id",
        source="icustays",
    )
    + " FROM {icustays} i"
    " UNION ALL SELECT "
    + _cols(
        subject="i.subject_id",
        time="i.outtime",
        code=f"'ICU_DISCHARGE{SEP}' || {_seg('i.last_careunit')}",
        hadm="i.hadm_id",
        stay="i.stay_id",
        source="icustays",
    )
    + " FROM {icustays} i WHERE i.outtime IS NOT NULL"
)

_DIAGNOSES_SQL = (
    "SELECT "
    + _cols(
        subject="d.subject_id",
        time="a.dischtime",
        code=f"'DIAGNOSIS{SEP}ICD' || {_seg('d.icd_version')} || '{SEP}' || {_seg('d.icd_code')}",
        numeric="d.seq_num",
        hadm="d.hadm_id",
        source="diagnoses_icd",
    )
    + " FROM {diagnoses_icd} d JOIN {admissions} a ON a.hadm_id = d.hadm_id"
)

_PROCEDURES_SQL = (
    "SELECT "
    + _cols(
        subject="p.subject_id",
        time="p.chartdate",
        code=f"'PROCEDURE{SEP}ICD' || {_seg('p.icd_version')} || '{SEP}' || {_seg('p.icd_code')}",
        numeric="p.seq_num",
        hadm="p.hadm_id",
        source="procedures_icd",
    )
    + " FROM {procedures_icd} p"
)

_LABEVENTS_SQL = (
    "SELECT "
    + _cols(
        subject="l.subject_id",
        time="l.charttime",
        code=f"'LAB{SEP}' || {_seg('l.itemid')} || '{SEP}' || {_seg('l.valueuom')}",
        numeric="l.valuenum",
        text=(
            "CASE WHEN l.valuenum IS NULL AND l.value IS NOT NULL "
            f"AND length(l.value) <= {LAB_TEXT_MAX_CHARS} THEN l.value END"
        ),
        hadm="l.hadm_id",
        source="labevents",
    )
    + " FROM {labevents} l"
)

_MICRO_SQL = (
    "SELECT "
    + _cols(
        subject="m.subject_id",
        time="coalesce(m.charttime, m.chartdate)",
        code=f"'MICRO{SEP}' || {_seg('m.spec_itemid')} || '{SEP}' || {_seg('m.org_itemid')}",
        text="m.interpretation",
        hadm="m.hadm_id",
        source="microbiologyevents",
    )
    + " FROM {microbiologyevents} m"
)

_PRESCRIPTIONS_SQL = (
    "SELECT "
    + _cols(
        subject="p.subject_id",
        time="p.starttime",
        code=f"'MEDICATION_START{SEP}' || {_seg('p.drug', DRUG_MAX_CHARS)}",
        text="p.route",
        hadm="p.hadm_id",
        source="prescriptions",
    )
    + " FROM {prescriptions} p"
    " UNION ALL SELECT "
    + _cols(
        subject="p.subject_id",
        time="p.stoptime",
        code=f"'MEDICATION_STOP{SEP}' || {_seg('p.drug', DRUG_MAX_CHARS)}",
        text="p.route",
        hadm="p.hadm_id",
        source="prescriptions",
    )
    + " FROM {prescriptions} p WHERE p.stoptime IS NOT NULL"
)

_EMAR_SQL = (
    "SELECT "
    + _cols(
        subject="e.subject_id",
        time="e.charttime",
        code=(
            f"'EMAR{SEP}' || {_seg('e.medication', EMAR_MEDICATION_MAX_CHARS)} || '{SEP}' || "
            f"{_seg('e.event_txt', EMAR_EVENT_MAX_CHARS)}"
        ),
        hadm="e.hadm_id",
        source="emar",
    )
    + " FROM {emar} e"
)

_INPUTEVENTS_SQL = (
    "SELECT "
    + _cols(
        subject="i.subject_id",
        time="i.starttime",
        code=f"'INPUT{SEP}' || {_seg('i.itemid')}",
        numeric="i.amount",
        text="i.amountuom",
        hadm="i.hadm_id",
        stay="i.stay_id",
        source="inputevents",
    )
    + " FROM {inputevents} i"
    " UNION ALL SELECT "
    + _cols(
        subject="i.subject_id",
        time="i.starttime",
        code=f"'INPUT_RATE{SEP}' || {_seg('i.itemid')}",
        numeric="i.rate",
        text="i.rateuom",
        hadm="i.hadm_id",
        stay="i.stay_id",
        source="inputevents",
    )
    + " FROM {inputevents} i WHERE i.rate IS NOT NULL"
)

_OUTPUTEVENTS_SQL = (
    "SELECT "
    + _cols(
        subject="o.subject_id",
        time="o.charttime",
        code=f"'OUTPUT{SEP}' || {_seg('o.itemid')}",
        numeric="o.value",
        text="o.valueuom",
        hadm="o.hadm_id",
        stay="o.stay_id",
        source="outputevents",
    )
    + " FROM {outputevents} o"
)

_PROCEDUREEVENTS_SQL = (
    "SELECT "
    + _cols(
        subject="p.subject_id",
        time="p.starttime",
        code=f"'ICU_PROCEDURE{SEP}' || {_seg('p.itemid')}",
        numeric="p.value",
        text="p.valueuom",
        hadm="p.hadm_id",
        stay="p.stay_id",
        source="procedureevents",
    )
    + " FROM {procedureevents} p"
)

_HOSP = "mimiciv_hosp"
_ICU = "mimiciv_icu"

#: The registry, in build order (the order of ``dag/specs/spine.yaml``).
SPINE_SOURCES: tuple[SpineSource, ...] = (
    SpineSource(
        "patients",
        _HOSP,
        "patients",
        _PATIENTS_SQL,
        (
            CodeRule(
                BIRTH_CODE,
                "anchor_year - anchor_age, Jan 1 (synthetic birth)",
                text=f"'{AGE_CAPPED_TEXT}' when anchor_age >= {AGE_CAP}",
                note="placeholder, not a birth date; capped ages are a bucket",
            ),
            CodeRule(
                DEATH_CODE,
                "dod (day resolution)",
                note="only where dod is recorded (~1-year horizon)",
            ),
        ),
        excluded=("gender", "anchor_year_group"),
        description="synthetic birth and the recorded date of death",
    ),
    SpineSource(
        "admissions",
        _HOSP,
        "admissions",
        _ADMISSIONS_SQL,
        (
            CodeRule(
                f"HOSPITAL_ADMISSION{SEP}<admission_type>",
                "admittime",
                text="admission_location",
            ),
            CodeRule(f"HOSPITAL_DISCHARGE{SEP}<discharge_location>", "dischtime"),
        ),
        excluded=("deathtime", "edregtime", "edouttime", "insurance", "language", "race"),
        description="hospital admission and discharge",
    ),
    SpineSource(
        "transfers",
        _HOSP,
        "transfers",
        _TRANSFERS_SQL,
        (CodeRule(f"TRANSFER_TO{SEP}<careunit>", "intime", text="eventtype"),),
        excluded=("outtime",),
        description="ward / unit arrivals (ADT); the discharge row has no careunit",
    ),
    SpineSource(
        "icustays",
        _ICU,
        "icustays",
        _ICUSTAYS_SQL,
        (
            CodeRule(f"ICU_ADMISSION{SEP}<first_careunit>", "intime"),
            CodeRule(f"ICU_DISCHARGE{SEP}<last_careunit>", "outtime"),
        ),
        excluded=("los",),
        description="ICU stay start and end",
    ),
    SpineSource(
        "diagnoses_icd",
        _HOSP,
        "diagnoses_icd",
        _DIAGNOSES_SQL,
        (
            CodeRule(
                f"DIAGNOSIS{SEP}ICD<icd_version>{SEP}<icd_code>",
                "admissions.dischtime",
                numeric="seq_num",
                note="no timestamp upstream: placed at discharge",
            ),
        ),
        joins=((_HOSP, "admissions"),),
        description="billed ICD-9 / ICD-10 diagnoses",
    ),
    SpineSource(
        "procedures_icd",
        _HOSP,
        "procedures_icd",
        _PROCEDURES_SQL,
        (
            CodeRule(
                f"PROCEDURE{SEP}ICD<icd_version>{SEP}<icd_code>",
                "chartdate (day resolution)",
                numeric="seq_num",
            ),
        ),
        description="billed ICD-9 / ICD-10-PCS procedures",
    ),
    SpineSource(
        "labevents",
        _HOSP,
        "labevents",
        _LABEVENTS_SQL,
        (
            CodeRule(
                f"LAB{SEP}<itemid>{SEP}<valueuom>",
                "charttime",
                numeric="valuenum",
                text=f"value, only when valuenum is NULL and <= {LAB_TEXT_MAX_CHARS} chars",
            ),
        ),
        excluded=("comments", "flag", "ref_range_lower", "ref_range_upper", "priority"),
        description="laboratory results (hospital-wide)",
    ),
    SpineSource(
        "microbiologyevents",
        _HOSP,
        "microbiologyevents",
        _MICRO_SQL,
        (
            CodeRule(
                f"MICRO{SEP}<spec_itemid>{SEP}<org_itemid|{NONE}>",
                "charttime, else chartdate",
                text="interpretation (S / I / R / P)",
                note="one row per specimen x organism x antibiotic",
            ),
        ),
        excluded=("comments", "ab_itemid", "dilution_text", "dilution_value", "quantity"),
        description="microbiology specimens and organisms",
    ),
    SpineSource(
        "prescriptions",
        _HOSP,
        "prescriptions",
        _PRESCRIPTIONS_SQL,
        (
            CodeRule(
                f"MEDICATION_START{SEP}<drug>",
                "starttime",
                text="route",
                note=f"drug cut to {DRUG_MAX_CHARS} chars",
            ),
            CodeRule(f"MEDICATION_STOP{SEP}<drug>", "stoptime", text="route"),
        ),
        excluded=("dose_val_rx", "dose_unit_rx", "prod_strength", "gsn", "ndc"),
        description="prescription courses",
    ),
    SpineSource(
        "emar",
        _HOSP,
        "emar",
        _EMAR_SQL,
        (
            CodeRule(
                f"EMAR{SEP}<medication>{SEP}<event_txt>",
                "charttime",
                note=(
                    f"medication cut to {EMAR_MEDICATION_MAX_CHARS}, event_txt to "
                    f"{EMAR_EVENT_MAX_CHARS} chars"
                ),
            ),
        ),
        excluded=("scheduletime", "storetime"),
        description="medication administration events",
    ),
    SpineSource(
        "inputevents",
        _ICU,
        "inputevents",
        _INPUTEVENTS_SQL,
        (
            CodeRule(f"INPUT{SEP}<itemid>", "starttime", numeric="amount", text="amountuom"),
            CodeRule(
                f"INPUT_RATE{SEP}<itemid>",
                "starttime",
                numeric="rate",
                text="rateuom",
                note="second row per input that carries a rate",
            ),
        ),
        excluded=("endtime", "ordercategoryname", "patientweight", "statusdescription"),
        description="ICU fluid / medication inputs",
    ),
    SpineSource(
        "outputevents",
        _ICU,
        "outputevents",
        _OUTPUTEVENTS_SQL,
        (CodeRule(f"OUTPUT{SEP}<itemid>", "charttime", numeric="value", text="valueuom"),),
        description="ICU fluid outputs",
    ),
    SpineSource(
        "procedureevents",
        _ICU,
        "procedureevents",
        _PROCEDUREEVENTS_SQL,
        (
            CodeRule(
                f"ICU_PROCEDURE{SEP}<itemid>",
                "starttime",
                numeric="value",
                text="valueuom",
            ),
        ),
        excluded=("endtime", "location", "locationcategory", "statusdescription"),
        description="ICU procedures with a duration",
    ),
)

_BY_NAME: dict[str, SpineSource] = {s.name: s for s in SPINE_SOURCES}


def source(name: str) -> SpineSource:
    """The registry entry ``name`` (a table name) or :class:`SpineError`."""
    try:
        return _BY_NAME[name]
    except KeyError:
        raise SpineError(f"no spine source {name!r}; known: {sorted(_BY_NAME)}") from None


def source_for_step(step_name: str) -> SpineSource:
    """The source of a ``spine.<source>`` DAG step."""
    if not step_name.startswith(STEP_PREFIX) or step_name == UNION_STEP:
        raise SpineError(f"step {step_name!r} is not a spine.<source> step")
    return source(step_name[len(STEP_PREFIX) :])


def catalog_relations() -> dict[str, str]:
    """``{table: schema.table}`` for every table any source reads (the catalog names)."""
    out: dict[str, str] = {}
    for s in SPINE_SOURCES:
        for schema, table in s.tables:
            out[table] = f"{schema}.{table}"
    return out


# ---------------------------------------------------------------------------
# Layout helpers
# ---------------------------------------------------------------------------


def spine_dir(lake_root: Path | str, tier: str) -> Path:
    """``<lake_root>/derived/<tier>/spine``."""
    return Path(lake_root) / DERIVED_LAYER / tier / SPINE_DIRNAME


def source_dir(lake_root: Path | str, tier: str, name: str) -> Path:
    """``<lake_root>/derived/<tier>/spine/source=<name>`` (bucket directories inside)."""
    return spine_dir(lake_root, tier) / f"{SOURCE_PARTITION}={name}"


def source_glob(directory: Path) -> str:
    """The published-files glob of one source directory (forward slashes)."""
    return f"{Path(directory).resolve().as_posix()}/{BUCKET_COLUMN}=*/part-*.parquet"


def _sql_str(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def read_sources_sql(dirs: Sequence[Path]) -> str:
    """The ``read_parquet([...])`` relation over the given source directories, Hive
    partitioning on (``source`` VARCHAR + ``subject_bucket`` INTEGER from the paths)."""
    if not dirs:
        raise SpineError("no spine source directory to read")
    globs = ", ".join(_sql_str(source_glob(d)) for d in dirs)
    return (
        f"read_parquet([{globs}], hive_partitioning = true, "
        f"hive_types = {{'{BUCKET_COLUMN}': INTEGER}})"
    )


def union_select_sql(dirs: Sequence[Path]) -> str:
    """``SELECT <spine columns> FROM read_parquet([...])`` — MEDS column order first."""
    columns = ", ".join(COLUMN_NAMES)
    return f"SELECT {columns} FROM {read_sources_sql(dirs)}"


def meta_path(lake_root: Path | str, tier: str, table: str, *, raw: bool = False) -> Path:
    """``<lake_root>/meta/<tier>/[raw/]<table>.parquet`` (EP-29's meta layout; the ``raw/``
    subdirectory is never walked into a catalog)."""
    from mimicwarehouse.catalog.profile import meta_dir

    base = meta_dir(lake_root, tier)
    return (base / RAW_DIRNAME / f"{table}.parquet") if raw else (base / f"{table}.parquet")


def existing_buckets(lake_root: Path | str, schema: str, table: str) -> list[int]:
    """The ``subject_bucket=<n>`` directories present under a staged core table."""
    from mimicwarehouse.loader.paths import table_dir

    root = table_dir(lake_root, schema, table)
    if not root.is_dir():
        return []
    out: list[int] = []
    with os.scandir(root) as it:
        for entry in it:
            m = _BUCKET_DIR.match(entry.name)
            if m and entry.is_dir():
                out.append(int(m.group(1)))
    return sorted(out)


def bucket_relations(
    lake_root: Path | str, src: SpineSource, buckets: Sequence[int]
) -> dict[str, str]:
    """``{table: read_parquet fragment}`` of the source's tables, pruned to ``buckets``."""
    from mimicwarehouse.loader.paths import read_parquet_sql

    return {
        table: read_parquet_sql(lake_root, schema, table, list(buckets))
        for schema, table in src.tables
    }


# ---------------------------------------------------------------------------
# Status (the per-tier entries)
# ---------------------------------------------------------------------------


def status_entry(lake_root: Path | str, key: str) -> dict[str, Any] | None:
    from mimicwarehouse.loader.manifest import read_status

    return read_status(Path(lake_root))["steps"].get(key)


def source_complete(lake_root: Path | str, tier: str, name: str) -> bool:
    """Complete for ``tier`` per :func:`~mimicwarehouse.dag.snapshot.complete_for_tier`
    **and** the tier's directory holds at least one published file."""
    from mimicwarehouse.dag.snapshot import complete_for_tier

    entry = status_entry(lake_root, f"{STATUS_PREFIX}{name}")
    if entry is None or not complete_for_tier(entry, tier):
        return False
    directory = source_dir(lake_root, tier, name)
    return directory.is_dir() and any(directory.glob(f"{BUCKET_COLUMN}=*/part-*.parquet"))


def complete_sources(lake_root: Path | str, tier: str) -> list[SpineSource]:
    """The registry entries complete for ``tier``, registry order."""
    return [s for s in SPINE_SOURCES if source_complete(lake_root, tier, s.name)]


def union_complete(lake_root: Path | str, tier: str) -> bool:
    """Whether the union step recorded the tier complete (validation passed)."""
    from mimicwarehouse.dag.snapshot import complete_for_tier

    entry = status_entry(lake_root, STATUS_KEY)
    if entry is None or not complete_for_tier(entry, tier):
        return False
    attempt = (entry.get("tiers") or {}).get(tier) or {}
    return attempt.get("status") == "done"


def _record_status(
    ctx: StepContext,
    key: str,
    *,
    status: str,
    **attempt_fields: Any,
) -> None:
    """Merge this attempt into the per-tier ``status.json`` entry ``key`` (the EP-37
    ``per_tier`` shape: ``tiers[<tier>]`` sub-entry, ``tier_complete`` / ``dev_ready`` set
    on success and **revoked** for the tier on failure)."""
    from mimicwarehouse.loader.manifest import update_status, utc_now_iso

    entry = status_entry(ctx.lake_root, key) or {}
    tiers = dict(entry.get("tiers") or {})
    tiers[ctx.tier] = {
        "status": status,
        "build_id": ctx.build_id,
        "run_id": ctx.run.run_id if ctx.run is not None else None,
        "finished_at": utc_now_iso(),
        **attempt_fields,
    }
    fields: dict[str, Any] = {"per_tier": True, "layer": DERIVED_LAYER, "tiers": tiers}
    if status == "done":
        if ctx.tier == "dev":
            fields["dev_ready"] = True
            if entry.get("tier_complete") != "full":
                fields["tier_complete"] = "dev"
        else:  # fixture / demo (own lake roots) and full
            fields["tier_complete"] = "full"
    elif ctx.tier == "dev":
        fields["dev_ready"] = False
        if entry.get("tier_complete") == "dev":
            fields["tier_complete"] = None
    else:
        fields["tier_complete"] = None
        fields["dev_ready"] = False
    update_status(ctx.lake_root, key, **fields)


def _core_snapshot_id(ctx: StepContext) -> str:
    if _STATE_CORE_SNAPSHOT not in ctx.state:
        from mimicwarehouse.dag.snapshot import layer_snapshot

        ctx.state[_STATE_CORE_SNAPSHOT] = layer_snapshot(
            ctx.lake_root, "core", ctx.tier, settings=ctx.settings
        )
    return str(ctx.state[_STATE_CORE_SNAPSHOT])


def _schema_hash() -> str:
    blob = json.dumps([[n, t] for n, t in SPINE_COLUMNS], separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()


def _bench(ctx: StepContext, name: str, *, wall_s: float, ok: bool, **fields: Any) -> None:
    from mimicwarehouse import run as run_mod

    run_mod.bench(
        BENCH_KIND,
        name,
        wall_s=wall_s,
        tier=ctx.tier,
        run_id=ctx.run.run_id if ctx.run is not None else None,
        build_id=ctx.build_id,
        settings=ctx.settings,
        ok=ok,
        **fields,
    )


@contextlib.contextmanager
def _insertion_order(con: duckdb.DuckDBPyConnection) -> Iterator[None]:
    """``preserve_insertion_order = true`` around a sorted partitioned ``COPY`` (the
    loader's small path: each partition file lands sorted), restored afterwards."""
    row = con.execute("SELECT current_setting('preserve_insertion_order')").fetchone()
    previous = str(row[0]).lower() if row else "false"
    con.execute("SET preserve_insertion_order = true")
    try:
        yield
    finally:
        con.execute(f"SET preserve_insertion_order = {previous}")


def _file_stats(con: duckdb.DuckDBPyConnection, directory: Path) -> list[tuple[Path, int]]:
    """``(file, rows)`` per published file of a source directory (Parquet metadata only;
    an empty directory yields nothing)."""
    if not any(Path(directory).glob(f"{BUCKET_COLUMN}=*/part-*.parquet")):
        return []
    rows = con.execute(
        "SELECT file_name, num_rows FROM parquet_file_metadata("
        f"{_sql_str(source_glob(directory))}) ORDER BY file_name"
    ).fetchall()
    return [(Path(str(f)), int(n)) for f, n in rows]


# ---------------------------------------------------------------------------
# build_source — the spine.<source> step
# ---------------------------------------------------------------------------


def build_source(step: Step, ctx: StepContext, *, src: SpineSource | None = None) -> StepOutcome:
    """The ``spine.<source>`` handler (module docstring)."""
    import duckdb

    from mimicwarehouse import publish
    from mimicwarehouse.dag.runner import StepOutcome
    from mimicwarehouse.loader.manifest import (
        ManifestLine,
        append_manifest,
        lake_relative_posix,
        sha256_streamed,
        utc_now_iso,
        writer_version,
    )
    from mimicwarehouse.loader.stage import COPY_OPTIONS
    from mimicwarehouse.run import ResourceLog

    src = src or source_for_step(step.name)
    problems = check_source(src, con=ctx.con)
    if problems:
        raise SpineError(f"{src.name}: projection reads denied column(s) {problems}")
    schema, table = src.tables[0]
    available = existing_buckets(ctx.lake_root, schema, table)
    if not available:
        raise SpineError(
            f"{src.name}: {schema}.{table} is not staged for tier {ctx.tier} under "
            f"{ctx.lake_root} — run `mwh build --tier {ctx.tier} --select "
            f"stage.{schema}.{table}` first"
        )
    wanted = [b for b in available if ctx.buckets is None or b in set(ctx.buckets)]
    dest = source_dir(ctx.lake_root, ctx.tier, src.name)
    new = publish.new_path_for(dest)
    if new.exists():
        publish.rmtree(new)
    new.mkdir(parents=True, exist_ok=True)
    sql_sha = src.sql_sha256
    order = ", ".join(ORDER_BY)
    columns = ", ".join(COLUMN_NAMES)

    def work() -> int:
        total = 0
        with _insertion_order(ctx.con):
            for bucket in wanted:
                # one sorted single-file COPY per bucket (module docstring): the core
                # read prunes to this bucket's directory, the file lands sorted
                inner = src.select_sql(bucket_relations(ctx.lake_root, src, [bucket]))
                select = (
                    f"SELECT {columns} FROM ({inner}) AS s "
                    f"WHERE subject_id % {NUM_BUCKETS} = {bucket} ORDER BY {order}"
                )
                bucket_dir = new / f"{BUCKET_COLUMN}={bucket}"
                bucket_dir.mkdir(parents=True, exist_ok=True)
                part = bucket_dir / PART
                row = ctx.con.execute(
                    f"COPY ({select}) TO {_sql_str(part.resolve().as_posix())} ({COPY_OPTIONS})"
                ).fetchone()
                n = int(row[0]) if row else 0
                if n == 0:  # a bucket without events leaves no file behind
                    publish.unlink(part)
                    bucket_dir.rmdir()
                total += n
        return total

    try:
        rows, usage = ResourceLog.measure(work, data_root=ctx.settings.data_root)
    except duckdb.Error as exc:
        from mimicwarehouse.safe import sanitize_error_text

        publish.rmtree(new)
        error_class = type(exc).__name__
        _record_status(
            ctx, src.status_key, status="failed", error_class=error_class, sql_sha256=sql_sha
        )
        _bench(ctx, step.name, wall_s=0.0, ok=False, error=error_class)
        raise SpineError(f"{src.name}: {error_class}: {sanitize_error_text(str(exc))}") from None

    publish.swap_dir(new, dest)
    files = _file_stats(ctx.con, dest)
    lines: list[ManifestLine] = []
    nbytes = 0
    core_snapshot = _core_snapshot_id(ctx)
    schema_hash = _schema_hash()
    version = writer_version()
    for path, n in files:
        size = path.stat().st_size
        nbytes += size
        lines.append(
            ManifestLine(
                schema=SPINE_DIRNAME,
                table=src.name,
                path=lake_relative_posix(path, ctx.lake_root),
                sha256=sha256_streamed(path),
                bytes=size,
                rows=n,
                schema_hash=schema_hash,
                writer_version=version,
                source_sha256=sql_sha,
                raw_snapshot_id=core_snapshot,
                build_id=ctx.build_id,
                ts=utc_now_iso(),
            )
        )
    if lines:
        append_manifest(ctx.lake_root, ctx.build_id, lines)
    _record_status(
        ctx,
        src.status_key,
        status="done",
        rows=rows,
        bytes=nbytes,
        files=len(files),
        buckets=len(files),
        error_class=None,
        sql_sha256=sql_sha,
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
        files=len(files),
    )
    if ctx.run is not None:
        ctx.run.record_ref("spine_source", src.name, version=MEDS_VERSION, hash=sql_sha)
    _LOG.info(
        "spine %s: %s rows, %s bytes, %d file(s), wall=%.2fs (tier %s)",
        src.name,
        f"{rows:,}",
        f"{nbytes:,}",
        len(files),
        usage.wall_s,
        ctx.tier,
    )
    return StepOutcome(rows=rows, bytes_out=nbytes, files=len(files), layer=DERIVED_LAYER)


# ---------------------------------------------------------------------------
# meta.spine_codes
# ---------------------------------------------------------------------------

CODES_COLUMNS: tuple[tuple[str, str], ...] = (
    ("code_prefix", "VARCHAR"),
    ("source_table", "VARCHAR"),
    ("n_events", "BIGINT"),
    ("n_events_suppressed", "BOOLEAN"),
    ("n_subjects", "BIGINT"),
    ("n_subjects_suppressed", "BOOLEAN"),
    ("k", "INTEGER"),
)
_RAW_CODES_COLUMNS: tuple[tuple[str, str], ...] = (
    ("code_prefix", "VARCHAR"),
    ("source_table", "VARCHAR"),
    ("n_events", "BIGINT"),
    ("n_subjects", "BIGINT"),
)


def codes_sql(dirs: Sequence[Path]) -> str:
    """The raw ``meta.spine_codes`` aggregate: one row per code prefix (the text before
    the first ``//``) and source table with event and subject counts."""
    return (
        f"SELECT split_part(code, '{SEP}', 1) AS code_prefix, source_table, "
        "count(*) AS n_events, count(DISTINCT subject_id) AS n_subjects "
        f"FROM {read_sources_sql(dirs)} GROUP BY 1, 2 ORDER BY 2, 1"
    )


def compute_codes(
    con: duckdb.DuckDBPyConnection, dirs: Sequence[Path], k: int
) -> tuple[polars.DataFrame, polars.DataFrame]:
    """``(released, raw)`` frames of ``meta.spine_codes``: the raw aggregate and its
    build-time k-suppressed twin (:func:`mimicwarehouse.disclose.suppress`, table mode,
    complementary) with the ``k`` column."""
    import polars as pl

    from mimicwarehouse.disclose import suppress

    raw = con.execute(codes_sql(dirs)).pl()
    released, _report = suppress(
        raw,
        k,
        count_cols=["n_events", "n_subjects"],
        group_cols=["code_prefix", "source_table"],
    )
    released = released.with_columns(pl.lit(k, dtype=pl.Int32).alias("k")).select(
        [name for name, _ in CODES_COLUMNS]
    )
    return released, raw


def write_codes(
    con: duckdb.DuckDBPyConnection,
    lake_root: Path | str,
    tier: str,
    released: polars.DataFrame,
    raw: polars.DataFrame,
) -> tuple[Path, int]:
    """Write the released frame as ``meta/<tier>/spine_codes.parquet`` (registered as
    ``meta.spine_codes`` by the discovery walker) and the raw counts under ``raw/``."""
    from mimicwarehouse.units import write_meta_parquet

    dest = meta_path(lake_root, tier, CODES_TABLE)
    nbytes = write_meta_parquet(con, dest, CODES_COLUMNS, released.rows())
    write_meta_parquet(
        con, meta_path(lake_root, tier, CODES_TABLE, raw=True), _RAW_CODES_COLUMNS, raw.rows()
    )
    return dest, nbytes


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

CHECK_SCHEMA = "meds_schema"
CHECK_TIME = "time_not_null"
CHECK_SUBJECTS = "subject_in_patients"
CHECK_TEXT = "text_value_bounded"
CHECK_CODE = "code_bounded"
CHECK_SORT = "sorted_within_file"
CHECK_DENIED = "no_denied_columns"
CHECK_NAMES: tuple[str, ...] = (
    CHECK_DENIED,
    CHECK_SCHEMA,
    CHECK_TIME,
    CHECK_SUBJECTS,
    CHECK_TEXT,
    CHECK_CODE,
    CHECK_SORT,
)

VALIDATION_COLUMNS: tuple[tuple[str, str], ...] = (
    ("tier", "VARCHAR"),
    ("validated_at", "VARCHAR"),
    ("build_id", "VARCHAR"),
    ("run_id", "VARCHAR"),
    ("n_sources", "INTEGER"),
    ("n_files", "INTEGER"),
    ("n_rows", "BIGINT"),
    ("n_checks", "INTEGER"),
    ("ok", "BOOLEAN"),
    ("failed_checks", "VARCHAR"),
)


@dataclass(frozen=True, slots=True)
class CheckRow:
    """One validation check: ``n_bad`` offending rows / files / sources, a short detail."""

    name: str
    ok: bool
    n_bad: int
    detail: str = ""


@dataclass(slots=True)
class SpineValidation:
    """What :func:`validate` found (counts and check names only)."""

    tier: str
    validated_at: str
    checks: list[CheckRow] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    n_files: int = 0
    n_rows: int = 0
    build_id: str | None = None
    run_id: str | None = None

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)

    @property
    def failed(self) -> list[str]:
        return [c.name for c in self.checks if not c.ok]

    def row(self) -> list[Any]:
        """The ``meta.spine_validation`` row (:data:`VALIDATION_COLUMNS` order)."""
        return [
            self.tier,
            self.validated_at,
            self.build_id,
            self.run_id,
            len(self.sources),
            self.n_files,
            self.n_rows,
            len(self.checks),
            self.ok,
            ", ".join(self.failed) or None,
        ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "tier": self.tier,
            "validated_at": self.validated_at,
            "build_id": self.build_id,
            "run_id": self.run_id,
            "sources": list(self.sources),
            "n_files": self.n_files,
            "n_rows": self.n_rows,
            "ok": self.ok,
            "checks": [
                {"name": c.name, "ok": c.ok, "n_bad": c.n_bad, "detail": c.detail}
                for c in self.checks
            ],
        }


def _iter_dicts(obj: Any) -> Iterator[dict[str, Any]]:
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _iter_dicts(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _iter_dicts(v)


def referenced_columns(sql: str, con: duckdb.DuckDBPyConnection) -> set[str]:
    """Every column name a statement references (``json_serialize_sql`` walk of the
    ``COLUMN_REF`` nodes; the statement is bound as a parameter, never executed)."""
    row = con.execute("SELECT json_serialize_sql(?)", [sql]).fetchone()
    doc = json.loads(str(row[0])) if row else {}
    if doc.get("error"):
        raise SpineError(f"projection does not parse: {doc.get('error_message', '?')}")
    names: set[str] = set()
    for node in _iter_dicts(doc):
        if node.get("class") == "COLUMN_REF":
            parts = node.get("column_names") or []
            if parts:
                names.add(str(parts[-1]).casefold())
    return names


def denied_columns_for(src: SpineSource) -> frozenset[str]:
    """:data:`DENIED_COLUMNS` plus the contract's ``free_text`` columns of the source's
    tables (``keys.yaml``; ``labevents.comments`` / ``microbiologyevents.comments`` today)."""
    from mimicwarehouse.schema.contract import load_contract

    contract = load_contract()
    names = set(DENIED_COLUMNS)
    for schema, table in src.tables:
        qn = f"{schema}.{table}"
        if contract.has_table(qn):
            names.update(c.casefold() for c in contract.table(qn).free_text_columns())
    return frozenset(names)


def check_source(src: SpineSource, *, con: duckdb.DuckDBPyConnection | None = None) -> list[str]:
    """The denied column names a source's projection references (empty = clean). The
    governance check of item 3: a projection that selects ``microbiologyevents.comments``
    is refused before anything is written."""
    own = con is None
    if own:
        from mimicwarehouse.engine import open_duckdb

        con = open_duckdb("app")
    assert con is not None
    try:
        found = referenced_columns(src.select_sql(), con) & denied_columns_for(src)
    finally:
        if own:
            con.close()
    return sorted(found)


def check_sources(
    sources: Iterable[SpineSource] = SPINE_SOURCES, *, con: duckdb.DuckDBPyConnection | None = None
) -> dict[str, list[str]]:
    """``{source: denied columns}`` for the sources that reference one."""
    own = con is None
    if own:
        from mimicwarehouse.engine import open_duckdb

        con = open_duckdb("app")
    try:
        out = {s.name: check_source(s, con=con) for s in sources}
    finally:
        if own and con is not None:
            con.close()
    return {k: v for k, v in out.items() if v}


def meds_schema() -> pyarrow.Schema:
    """The MEDS 0.4 core schema as pyarrow types (hard-coded; no ``meds`` dependency)."""
    import pyarrow as pa

    types = {
        "int64": pa.int64(),
        "timestamp[us]": pa.timestamp("us"),
        "string": pa.string(),
        "float": pa.float32(),
    }
    return pa.schema([pa.field(name, types[arrow]) for name, _, arrow in MEDS_COLUMNS])


def check_file_schema(path: Path) -> str | None:
    """None when the file's first columns are the MEDS core columns with the MEDS types
    (``pyarrow.parquet.read_schema``), else a short problem text."""
    import pyarrow.parquet as pq

    expected = meds_schema()
    actual = pq.read_schema(path)
    names = actual.names[: len(expected)]
    if names != expected.names:
        return f"columns {names} != {expected.names}"
    for want in expected:
        have = actual.field(want.name)
        if not have.type.equals(want.type):
            return f"{want.name}: {have.type} != {want.type}"
    extra = actual.names[len(expected) :]
    if extra != [n for n, _ in EXTRA_COLUMNS]:
        return f"extra columns {extra} != {[n for n, _ in EXTRA_COLUMNS]}"
    return None


def _scalar(con: duckdb.DuckDBPyConnection, sql: str) -> int:
    row = con.execute(sql).fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def validate(
    tier: str,
    *,
    settings: Settings | None = None,
    con: duckdb.DuckDBPyConnection | None = None,
    lake_root: Path | None = None,
    write: bool = True,
    build_id: str | None = None,
    run_id: str | None = None,
    sources: Iterable[SpineSource] | None = None,
) -> SpineValidation:
    """The MEDS-conformance + governance checks over every source complete for ``tier``
    (module docstring), read straight from the Parquet (no catalog needed). With
    ``write`` the ``meta.spine_validation`` row is appended (the file is registered as a
    ``meta`` table at the next catalog build). ``con`` defaults to an in-memory app-profile
    connection (the union step passes the build connection). ``sources`` replaces the
    registry (tests hand in a crafted projection the static check must refuse)."""
    from mimicwarehouse.config import get_settings
    from mimicwarehouse.loader.manifest import utc_now_iso
    from mimicwarehouse.loader.paths import read_parquet_sql

    settings = settings or get_settings()
    if tier not in ("fixture", "demo", "dev", "full"):
        raise SpineError(f"unknown tier {tier!r}; expected fixture | demo | dev | full")
    root = Path(lake_root) if lake_root is not None else settings.lake_root(tier)
    own = con is None
    if own:
        from mimicwarehouse.engine import open_duckdb

        con = open_duckdb("app", settings=settings)
    assert con is not None
    report = SpineValidation(
        tier=tier, validated_at=utc_now_iso(), build_id=build_id, run_id=run_id
    )
    try:
        registry = list(sources) if sources is not None else list(SPINE_SOURCES)
        sources = [s for s in registry if source_complete(root, tier, s.name)]
        if not sources:
            raise SpineError(
                f"no spine source is complete for tier {tier} under {root} — run "
                f"`mwh build --tier {tier} --tag {TAG}` first"
            )
        report.sources = [s.name for s in sources]
        dirs = [source_dir(root, tier, s.name) for s in sources]
        # 0. static: no projection reads a denied column
        denied = check_sources(sources, con=con)
        report.checks.append(
            CheckRow(
                CHECK_DENIED,
                not denied,
                len(denied),
                "; ".join(f"{n}: {', '.join(c)}" for n, c in denied.items()),
            )
        )
        # 1. MEDS core schema per file
        files: list[Path] = []
        for d in dirs:
            files.extend(p for p, _ in _file_stats(con, d))
        report.n_files = len(files)
        bad_schema = [(p, check_file_schema(p)) for p in files]
        bad_schema = [(p, why) for p, why in bad_schema if why]
        report.checks.append(
            CheckRow(
                CHECK_SCHEMA,
                not bad_schema,
                len(bad_schema),
                bad_schema[0][1] if bad_schema else "",
            )
        )
        rel = read_sources_sql(dirs)
        report.n_rows = _scalar(con, f"SELECT count(*) FROM {rel}")
        # 2. time non-null except MEDS_BIRTH
        n_time = _scalar(
            con, f"SELECT count(*) FROM {rel} WHERE time IS NULL AND code <> '{BIRTH_CODE}'"
        )
        report.checks.append(CheckRow(CHECK_TIME, n_time == 0, n_time))
        # 3. every subject_id present in patients (the tier's core view)
        buckets = list(settings.dev_buckets) if tier == "dev" else None
        patients = read_parquet_sql(root, _HOSP, "patients", buckets)
        n_orphans = _scalar(
            con,
            f"SELECT count(*) FROM (SELECT DISTINCT subject_id FROM {rel}) s "
            f"WHERE NOT EXISTS (SELECT 1 FROM {patients} p WHERE p.subject_id = s.subject_id)",
        )
        report.checks.append(CheckRow(CHECK_SUBJECTS, n_orphans == 0, n_orphans))
        # 4./5. text_value and code within the free-text bound, no newline
        n_text = _scalar(
            con,
            f"SELECT count(*) FROM {rel} WHERE length(text_value) > {TEXT_MAX_CHARS} "
            "OR contains(text_value, chr(10))",
        )
        report.checks.append(CheckRow(CHECK_TEXT, n_text == 0, n_text))
        n_code = _scalar(
            con,
            f"SELECT count(*) FROM {rel} WHERE length(code) > {CODE_MAX_CHARS} "
            "OR contains(code, chr(10)) OR code IS NULL",
        )
        report.checks.append(CheckRow(CHECK_CODE, n_code == 0, n_code))
        # 6. (subject_id, time) monotone within every file (NULLS LAST), file by file
        unsorted = 0
        for path in files:
            unsorted += _scalar(
                con,
                "SELECT count(*) FROM (SELECT subject_id, time, "
                "lag(subject_id) OVER w AS ps, lag(time) OVER w AS pt "
                f"FROM read_parquet({_sql_str(path.resolve().as_posix())}, "
                "file_row_number = true) WINDOW w AS (ORDER BY file_row_number)) "
                "WHERE ps IS NOT NULL AND (subject_id < ps OR (subject_id = ps AND "
                "((pt IS NULL AND time IS NOT NULL) OR time < pt)))",
            )
        report.checks.append(CheckRow(CHECK_SORT, unsorted == 0, unsorted))
        if write:
            write_validation(con, root, tier, report)
    finally:
        if own:
            con.close()
    return report


def write_validation(
    con: duckdb.DuckDBPyConnection, lake_root: Path | str, tier: str, report: SpineValidation
) -> Path:
    """Append the report's row to ``meta/<tier>/spine_validation.parquet`` (history of
    validation runs on the tier; the walker registers it as ``meta.spine_validation``)."""
    from mimicwarehouse.units import write_meta_parquet

    dest = meta_path(lake_root, tier, VALIDATION_TABLE)
    rows: list[list[Any]] = []
    if dest.is_file():
        columns = ", ".join(f'"{n}"' for n, _ in VALIDATION_COLUMNS)
        rows = [
            list(r)
            for r in con.execute(
                f"SELECT {columns} FROM read_parquet({_sql_str(dest.resolve().as_posix())})"
            ).fetchall()
        ]
    rows.append(report.row())
    write_meta_parquet(con, dest, VALIDATION_COLUMNS, rows)
    return dest


# ---------------------------------------------------------------------------
# build_union — the spine.union step
# ---------------------------------------------------------------------------


def build_union(step: Step, ctx: StepContext) -> StepOutcome:
    """The ``spine.union`` handler (module docstring): ``meta.spine_codes``, the
    validation row and the ``mimiciv_derived.spine`` status entry the catalog extension
    reads. A failed validation records the failure (the view is then **not** registered
    at the next catalog build) and raises :class:`SpineError`."""
    from mimicwarehouse.dag.runner import StepOutcome
    from mimicwarehouse.run import ResourceLog

    sources = complete_sources(ctx.lake_root, ctx.tier)
    if not sources:
        raise SpineError(
            f"no spine source is complete for tier {ctx.tier} — run `mwh build --tier "
            f"{ctx.tier} --tag {TAG}` (or --select spine.<source>) first"
        )
    dirs = [source_dir(ctx.lake_root, ctx.tier, s.name) for s in sources]
    k = ctx.settings.k_suppression

    def work() -> tuple[SpineValidation, int, int]:
        released, raw = compute_codes(ctx.con, dirs, k)
        _codes_path, codes_bytes = write_codes(ctx.con, ctx.lake_root, ctx.tier, released, raw)
        report = validate(
            ctx.tier,
            settings=ctx.settings,
            con=ctx.con,
            lake_root=ctx.lake_root,
            build_id=ctx.build_id,
            run_id=ctx.run.run_id if ctx.run is not None else None,
        )
        return report, released.height, codes_bytes

    (report, codes_rows, codes_bytes), usage = ResourceLog.measure(
        work, data_root=ctx.settings.data_root
    )
    nbytes = sum(
        p.stat().st_size for d in dirs for p in d.glob(f"{BUCKET_COLUMN}=*/part-*.parquet")
    )
    _record_status(
        ctx,
        STATUS_KEY,
        status="done" if report.ok else "failed",
        sources=[s.name for s in sources],
        rows=report.n_rows,
        files=report.n_files,
        bytes=nbytes,
        codes_rows=codes_rows,
        validation_ok=report.ok,
        failed_checks=report.failed,
        error_class=None if report.ok else "SpineValidationFailed",
    )
    _bench(
        ctx,
        step.name,
        wall_s=usage.wall_s,
        ok=report.ok,
        peak_rss_mb=usage.peak_rss_mb,
        disk_delta_mb=usage.disk_delta_mb,
        rows=report.n_rows,
        bytes_out=codes_bytes,
        files=report.n_files,
        error=None if report.ok else f"validation failed: {', '.join(report.failed)}",
    )
    _LOG.info(
        "spine union (%s): %d source(s), %s rows, %d file(s), %d code-prefix row(s), "
        "validation %s, wall=%.2fs",
        ctx.tier,
        len(sources),
        f"{report.n_rows:,}",
        report.n_files,
        codes_rows,
        "ok" if report.ok else "FAILED " + ", ".join(report.failed),
        usage.wall_s,
    )
    if not report.ok:
        details = "; ".join(
            f"{c.name}: {c.n_bad} {c.detail}".strip() for c in report.checks if not c.ok
        )
        raise SpineError(f"spine validation failed on tier {ctx.tier}: {details}")
    return StepOutcome(
        rows=report.n_rows, bytes_out=codes_bytes, files=report.n_files, layer=DERIVED_LAYER
    )


# ---------------------------------------------------------------------------
# Catalog extension
# ---------------------------------------------------------------------------

_CODES_COMMENT = (
    "Events spine summary (EP-50): one row per code prefix (the text before the first "
    "'//') and source table with n_events / n_subjects, k-suppressed at build time "
    "(blank + *_suppressed = true below k, complementary; raw counts stay in the data "
    "root). Aggregate only."
)
_VALIDATION_COMMENT = (
    "Events spine validation history (EP-50): one row per validation run on this tier - "
    "MEDS core schema per file, time non-null except MEDS_BIRTH, subjects present in "
    "patients, code / text_value within the 64-char bound, sorted files, no denied "
    "column read; failed_checks names what failed."
)


def _catalog_lake_root(con: duckdb.DuckDBPyConnection) -> Path:
    row = con.execute("SELECT lake_root FROM meta.catalog_info").fetchone()
    if row is None or not row[0]:
        raise SpineError("meta.catalog_info carries no lake_root — extension order?")
    return Path(str(row[0]))


def view_sql(dirs: Sequence[Path]) -> str:
    """The ``CREATE OR REPLACE VIEW mimiciv_derived.spine`` statement over ``dirs``."""
    return f"CREATE OR REPLACE VIEW {DERIVED_SCHEMA}.{VIEW_NAME} AS {union_select_sql(dirs)}"


def register_spine(con: duckdb.DuckDBPyConnection, tier: str) -> None:
    """The catalog extension (:data:`mimicwarehouse.catalog.build.CATALOG_EXTENSIONS`
    entry, after EP-37's discovery walker): ``mimiciv_derived.spine`` over every source
    complete for ``tier`` once the union step recorded the tier complete, plus comments
    on the ``meta.spine_codes`` / ``meta.spine_validation`` tables the walker registered.
    DDL only; never opens a connection, never creates an empty view."""
    lake_root = _catalog_lake_root(con)
    present = {
        str(r[0])
        for r in con.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'meta'"
        ).fetchall()
    }
    for table, comment in ((CODES_TABLE, _CODES_COMMENT), (VALIDATION_TABLE, _VALIDATION_COMMENT)):
        if table in present:
            con.execute(f'COMMENT ON TABLE meta."{table}" IS {_sql_str(comment)}')
    if not union_complete(lake_root, tier):
        _LOG.info("catalog extension spine (%s): union not complete — no view", tier)
        return
    sources = complete_sources(lake_root, tier)
    if not sources:
        _LOG.warning("catalog extension spine (%s): no complete source — no view", tier)
        return
    dirs = [source_dir(lake_root, tier, s.name) for s in sources]
    con.execute(f"CREATE SCHEMA IF NOT EXISTS {DERIVED_SCHEMA}")
    con.execute(view_sql(dirs))
    entry = status_entry(lake_root, STATUS_KEY) or {}
    attempt = (entry.get("tiers") or {}).get(tier) or {}
    comment = (
        f"Events spine (EP-50; MEDS {MEDS_VERSION} column order first): one row per "
        f"event over {len(sources)} source table(s) - {', '.join(s.name for s in sources)}; "
        "code = PREFIX//segment//segment, numeric_value float32, text_value short "
        "dictionary strings only (free text never copied), times per-patient shifted; "
        f"built {tier} by {attempt.get('build_id') or '?'}. Subject-keyed: read through "
        "safe_query as aggregates (code / text_value stay within 64 chars)."
    )
    con.execute(f"COMMENT ON VIEW {DERIVED_SCHEMA}.{VIEW_NAME} IS {_sql_str(comment)}")
    _LOG.info(
        "catalog extension spine (%s): %s over %d source(s)",
        tier,
        f"{DERIVED_SCHEMA}.{VIEW_NAME}",
        len(sources),
    )


# ---------------------------------------------------------------------------
# Spec (hand-written dag/specs/spine.yaml; the path for tests)
# ---------------------------------------------------------------------------


def spec_path() -> Path:
    """``src/mimicwarehouse/dag/specs/spine.yaml`` inside the installed package."""
    from mimicwarehouse.dag.spec import specs_root

    return specs_root() / f"{SPEC_NAME}.yaml"


# ---------------------------------------------------------------------------
# Methods page (docs/methods/spine.md) — generated blocks
# ---------------------------------------------------------------------------


def _md_table(header: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines.extend("| " + " | ".join(str(c) for c in row) + " |" for row in rows)
    return "\n".join(lines)


def grammar_rows() -> list[list[str]]:
    """One row per :class:`CodeRule` of the registry (source, code, time, values, notes)."""
    rows: list[list[str]] = []
    for s in SPINE_SOURCES:
        for r in s.rules:
            rows.append(
                [
                    f"`{s.qualified_name}`",
                    f"`{r.code}`",
                    f"`{r.time}`",
                    r.numeric if r.numeric == "-" else f"`{r.numeric}`",
                    r.text if r.text == "-" else f"`{r.text}`",
                    r.note or "-",
                ]
            )
    return rows


def render_grammar_table() -> str:
    """The code grammar as a Markdown table (generated)."""
    return _md_table(
        ["source table", "code", "time column", "numeric_value", "text_value", "caveat"],
        grammar_rows(),
    )


def render_meds_table() -> str:
    """The MEDS mapping as a Markdown table (generated)."""
    meaning = {
        "subject_id": "patient identifier (`patients.subject_id`)",
        "time": "event time (shifted, naive); NULL only for static events",
        "code": "`PREFIX//segment//segment` (grammar below)",
        "numeric_value": "measurement / amount / rate / sequence number, else NULL",
        "text_value": "a short dictionary-like string, else NULL",
        "hadm_id": "project extra: admission identifier when the event has one",
        "stay_id": "project extra: ICU stay identifier when the event has one",
        "source_table": "project extra: the core table the event came from",
    }
    rows: list[list[str]] = []
    for name, ddb, arrow in MEDS_COLUMNS:
        rows.append(
            [f"`{name}`", f"MEDS {MEDS_VERSION} core", f"`{ddb}`", f"`{arrow}`", meaning[name]]
        )
    for name, ddb in EXTRA_COLUMNS:
        rows.append([f"`{name}`", "extra (allowed)", f"`{ddb}`", "-", meaning[name]])
    return _md_table(["column", "standard", "DuckDB type", "pyarrow type", "meaning"], rows)


def methods_doc_path() -> Path:
    """``mimicwarehouse/docs/methods/spine.md``."""
    from mimicwarehouse.config import workspace_root

    return workspace_root() / METHODS_DOC_RELPATH


def sync_methods_doc(path: Path | None = None) -> Path:
    """Re-render the generated blocks of the methods page in place (idempotent;
    ``python -m mimicwarehouse.spine`` runs it; ``test_ep50`` asserts the page is in
    sync). The narrative around the markers is never touched."""
    from mimicwarehouse.dag.benchmarks import replace_marked_block

    target = Path(path) if path is not None else methods_doc_path()
    text = target.read_text(encoding="utf-8")
    for (begin, end), block in (
        (GRAMMAR_MARK, render_grammar_table()),
        (MEDS_MARK, render_meds_table()),
    ):
        text = replace_marked_block(text, block, begin=begin, end=end)
    target.write_text(text.rstrip("\n") + "\n", encoding="utf-8", newline="\n")
    return target


# ---------------------------------------------------------------------------
# CLI — `mwh spine sources | validate` (attached in cli.py)
# ---------------------------------------------------------------------------

spine_app = typer.Typer(
    help="Events spine (EP-50): the source registry and the MEDS-conformance / governance "
    "validation. Build it with `mwh build --tier <t> --tag spine`.",
    no_args_is_help=True,
)


@spine_app.command("sources")
def sources_command(
    ctx: typer.Context,
    as_json: bool = typer.Option(False, "--json", help="Emit the registry as JSON."),
) -> None:
    """List the source registry (tables, code grammar, step names; no data is read)."""
    from rich.markup import escape
    from rich.table import Table

    from mimicwarehouse.console import console, emit_json

    if as_json:
        emit_json(
            [
                {
                    "name": s.name,
                    "table": s.qualified_name,
                    "step": s.step_name,
                    "codes": [r.code for r in s.rules],
                    "joins": [f"{sc}.{t}" for sc, t in s.joins],
                    "excluded": list(s.excluded),
                    "sql_sha256": s.sql_sha256,
                }
                for s in SPINE_SOURCES
            ]
        )
        return
    table = Table(title="mwh spine sources", pad_edge=False)
    for col in ("source", "table", "codes", "time", "description"):
        table.add_column(col)
    for s in SPINE_SOURCES:
        table.add_row(
            escape(s.name),
            escape(s.qualified_name),
            escape("; ".join(r.code for r in s.rules)),
            escape("; ".join(r.time for r in s.rules)),
            escape(s.description),
        )
    console.print(table)
    console.print(
        f"{len(SPINE_SOURCES)} source(s); steps spine.<source> + {UNION_STEP}; "
        f"`mwh build --tier <t> --tag {TAG}`",
        highlight=False,
    )


@spine_app.command("validate")
def validate_command(
    ctx: typer.Context,
    tier: str = typer.Option(..., "--tier", help="Tier to validate: fixture | demo | dev | full."),
    as_json: bool = typer.Option(False, "--json", help="Emit the report as JSON."),
    no_write: bool = typer.Option(
        False, "--no-write", help="Do not append the meta.spine_validation row."
    ),
) -> None:
    """Run the MEDS-conformance + governance checks over the tier's built spine (reads
    the Parquet directly; exit 1 when a check fails)."""
    from rich.markup import escape
    from rich.table import Table

    from mimicwarehouse.console import EXIT_FINDINGS, console, emit_json, fail

    state: CliState = ctx.obj
    settings = state.settings
    if tier not in ("fixture", "demo", "dev", "full"):
        fail("mwh spine validate", f"unknown tier {tier!r}; expected fixture | demo | dev | full")
    try:
        report = validate(tier, settings=settings, write=not no_write)
    except SpineError as exc:
        fail("mwh spine validate", str(exc))
    if as_json:
        emit_json(report.to_dict())
    else:
        table = Table(title=f"mwh spine validate ({tier})", pad_edge=False)
        for col, justify in (
            ("check", "left"),
            ("ok", "left"),
            ("n_bad", "right"),
            ("detail", "left"),
        ):
            table.add_column(col, justify=justify)  # type: ignore[arg-type]
        for c in report.checks:
            table.add_row(c.name, "yes" if c.ok else "NO", f"{c.n_bad:,}", escape(c.detail))
        console.print(table)
        console.print(
            f"{len(report.sources)} source(s), {report.n_files:,} file(s), "
            f"{report.n_rows:,} row(s) - {'OK' if report.ok else 'FAILED'}",
            highlight=False,
        )
    if not report.ok:
        raise typer.Exit(code=EXIT_FINDINGS)


__all__ = [
    "AGE_CAPPED_TEXT",
    "BENCH_KIND",
    "BIRTH_CODE",
    "BUCKET_COLUMN",
    "CHECK_NAMES",
    "CODES_COLUMNS",
    "CODES_TABLE",
    "CODE_MAX_CHARS",
    "COLUMN_NAMES",
    "DEATH_CODE",
    "DENIED_COLUMNS",
    "DERIVED_LAYER",
    "DERIVED_SCHEMA",
    "EXTRA_COLUMNS",
    "GRAMMAR_MARK",
    "LAB_TEXT_MAX_CHARS",
    "MEDS_COLUMNS",
    "MEDS_MARK",
    "MEDS_VERSION",
    "METHODS_DOC_RELPATH",
    "NONE",
    "ORDER_BY",
    "SEP",
    "SPEC_NAME",
    "SPINE_COLUMNS",
    "SPINE_DIRNAME",
    "SPINE_SOURCES",
    "STATUS_KEY",
    "STATUS_PREFIX",
    "STEP_PREFIX",
    "TAG",
    "TEXT_MAX_CHARS",
    "UNION_STEP",
    "VALIDATION_COLUMNS",
    "VALIDATION_TABLE",
    "VIEW_NAME",
    "CheckRow",
    "CodeRule",
    "SpineError",
    "SpineSource",
    "SpineValidation",
    "bucket_relations",
    "build_source",
    "build_union",
    "catalog_relations",
    "check_file_schema",
    "check_source",
    "check_sources",
    "codes_sql",
    "complete_sources",
    "compute_codes",
    "denied_columns_for",
    "existing_buckets",
    "grammar_rows",
    "meds_schema",
    "meta_path",
    "methods_doc_path",
    "read_sources_sql",
    "referenced_columns",
    "register_spine",
    "render_grammar_table",
    "render_meds_table",
    "source",
    "source_complete",
    "source_dir",
    "source_for_step",
    "source_glob",
    "spec_path",
    "spine_app",
    "spine_dir",
    "status_entry",
    "sync_methods_doc",
    "union_complete",
    "union_select_sql",
    "validate",
    "view_sql",
    "write_codes",
    "write_validation",
]


if __name__ == "__main__":  # pragma: no cover
    print(sync_methods_doc())
