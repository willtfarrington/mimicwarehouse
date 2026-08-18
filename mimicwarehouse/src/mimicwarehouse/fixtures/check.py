"""Contract + integrity checks over generated fixture frames (EP-11 item 5).

:func:`validate` returns a list of human-readable problems (empty = valid); :func:`assert_valid`
raises :class:`FixtureError` with them. Checked, per the brief: every frame has exactly the
contract columns (order and castable dtypes); every id column is >= 90 000 000 (D-27) and no
integer column ever holds a value inside the real MIMIC bands (guard G4, whatever the column);
foreign keys (``hadm_id`` -> admissions, ``subject_id`` -> patients, ``itemid`` -> d_labitems,
ICD codes -> ``d_icd_*``, ``hcpcs_cd`` -> d_hcpcs, ``emar_id`` -> emar, ``poe_id`` -> poe,
``pharmacy_id`` -> pharmacy, provider ids -> provider, ``(subject_id, hadm_id)`` consistency);
declared primary keys are unique; ``dischtime > admittime``; ``deathtime`` <-> flag; ``dod`` >=
last ``dischtime`` and >= ``deathtime``; every ICU segment lies inside its admission and matches
a transfers row when a plan is given. Everything runs on Polars frames in memory - it never
prints a row.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import polars as pl

from mimicwarehouse.fixtures.hosp import HOSP_SCHEMA, polars_schema
from mimicwarehouse.fixtures.spec import FIXTURE_ID_FLOOR, REAL_ID_BAND, FixturePlan

if TYPE_CHECKING:  # pragma: no cover
    from mimicwarehouse.schema.contract import Contract

#: Columns that must be >= FIXTURE_ID_FLOOR wherever they appear (nulls allowed if nullable).
ID_COLUMNS: frozenset[str] = frozenset(
    {
        "subject_id",
        "hadm_id",
        "stay_id",
        "labevent_id",
        "specimen_id",
        "microevent_id",
        "micro_specimen_id",
        "transfer_id",
        "pharmacy_id",
        "orderid",
        "linkorderid",
        "caregiver_id",
    }
)
#: Extra documented links inside hosp that keys.yaml leaves to integrity tests (EP-28) - the
#: fixture honours them by construction, so they are checked here too.
EXTRA_FKS: tuple[tuple[str, str, str, str], ...] = (
    ("emar", "poe_id", "poe", "poe_id"),
    ("emar", "pharmacy_id", "pharmacy", "pharmacy_id"),
    ("emar_detail", "pharmacy_id", "pharmacy", "pharmacy_id"),
    ("pharmacy", "poe_id", "poe", "poe_id"),
    ("prescriptions", "pharmacy_id", "pharmacy", "pharmacy_id"),
    ("prescriptions", "poe_id", "poe", "poe_id"),
    ("transfers", "hadm_id", "admissions", "hadm_id"),
    ("admissions", "admit_provider_id", "provider", "provider_id"),
    ("labevents", "order_provider_id", "provider", "provider_id"),
    ("microbiologyevents", "order_provider_id", "provider", "provider_id"),
    ("poe", "order_provider_id", "provider", "provider_id"),
    ("prescriptions", "order_provider_id", "provider", "provider_id"),
    ("emar", "enter_provider_id", "provider", "provider_id"),
    ("diagnoses_icd", "icd_code|icd_version", "d_icd_diagnoses", "icd_code|icd_version"),
    ("procedures_icd", "icd_code|icd_version", "d_icd_procedures", "icd_code|icd_version"),
)


class FixtureError(RuntimeError):
    """The generated fixture violates the contract or an integrity rule."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = list(problems)
        head = f"{len(self.problems)} fixture problem(s)"
        super().__init__("\n".join([head, *(f"  - {p}" for p in self.problems[:50])]))


def _schema_problems(name: str, frame: pl.DataFrame, contract: Contract) -> list[str]:
    table = contract.table(HOSP_SCHEMA, name)
    expected = polars_schema(table)
    problems: list[str] = []
    if list(frame.columns) != list(expected):
        problems.append(f"{name}: columns {list(frame.columns)} != contract {list(expected)}")
        return problems
    for col, dtype in expected.items():
        actual = frame.schema[col]
        if actual != dtype:
            try:
                frame.get_column(col).cast(dtype, strict=True)
            except pl.exceptions.PolarsError as exc:  # pragma: no cover - defensive
                problems.append(f"{name}.{col}: dtype {actual} not castable to {dtype}: {exc}")
            else:
                problems.append(f"{name}.{col}: dtype {actual} (contract {dtype}); cast first")
    for c in table.columns:
        if not c.nullable and frame.get_column(c.name).null_count() > 0:
            problems.append(f"{name}.{c.name}: NULLs in a NOT NULL column")
    return problems


def _id_problems(name: str, frame: pl.DataFrame) -> list[str]:
    problems: list[str] = []
    lo, hi = REAL_ID_BAND
    for col, dtype in frame.schema.items():
        if not dtype.is_integer():
            continue
        s = frame.get_column(col).drop_nulls().cast(pl.Int64)  # SMALLINT vs 10_000_000 overflows
        if s.is_empty():
            continue
        if col in ID_COLUMNS and int(s.min()) < FIXTURE_ID_FLOOR:  # type: ignore[arg-type]
            problems.append(f"{name}.{col}: id below {FIXTURE_ID_FLOOR:_} (min {int(s.min())})")  # type: ignore[arg-type]
        in_band = ((s >= lo) & (s <= hi)).sum()
        if in_band:
            problems.append(
                f"{name}.{col}: {in_band} value(s) inside the real id band {lo:_}-{hi:_}"
            )
    return problems


def _fk_problems(
    frames: dict[str, pl.DataFrame], child: str, cols: str, parent: str, ref_cols: str
) -> list[str]:
    if child not in frames or parent not in frames:
        return []
    ccols = cols.split("|")
    pcols = ref_cols.split("|")
    c = frames[child].select(ccols).drop_nulls().unique()
    p = frames[parent].select(pcols).unique()
    if c.is_empty():
        return []
    missing = c.join(p, left_on=ccols, right_on=pcols, how="anti")
    if missing.height:
        return [f"{child}({cols}) -> {parent}({ref_cols}): {missing.height} orphan value(s)"]
    return []


def _pk_problems(name: str, frame: pl.DataFrame, contract: Contract) -> list[str]:
    table = contract.table(HOSP_SCHEMA, name)
    keys = table.primary_key or table.uniqueness_hint
    if not keys or frame.is_empty():
        return []
    dupes = frame.height - frame.select(list(keys)).unique().height
    label = "primary key" if table.primary_key else "uniqueness hint"
    return [f"{name}: {dupes} duplicate row(s) on {label} {list(keys)}"] if dupes else []


def _sort_problems(name: str, frame: pl.DataFrame, contract: Contract) -> list[str]:
    table = contract.table(HOSP_SCHEMA, name)
    if not table.sort_keys or frame.height < 2:
        return []
    sorted_frame = frame.sort(list(table.sort_keys), maintain_order=True)
    if not frame.select(list(table.sort_keys)).equals(sorted_frame.select(list(table.sort_keys))):
        return [f"{name}: rows are not sorted by {list(table.sort_keys)}"]
    return []


def _admission_problems(frames: dict[str, pl.DataFrame]) -> list[str]:
    problems: list[str] = []
    adm = frames["admissions"]
    pat = frames["patients"]
    bad = adm.filter(pl.col("dischtime") <= pl.col("admittime")).height
    if bad:
        problems.append(f"admissions: {bad} row(s) with dischtime <= admittime")
    bad = adm.filter(
        (pl.col("hospital_expire_flag") == 1) != pl.col("deathtime").is_not_null()
    ).height
    if bad:
        problems.append(
            f"admissions: {bad} row(s) where deathtime and hospital_expire_flag disagree"
        )
    bad = adm.filter(
        pl.col("deathtime").is_not_null()
        & (
            (pl.col("deathtime") < pl.col("admittime"))
            | (pl.col("deathtime") > pl.col("dischtime"))
        )
    ).height
    if bad:
        problems.append(f"admissions: {bad} deathtime(s) outside [admittime, dischtime]")
    bad = adm.filter(
        pl.col("edregtime").is_not_null()
        & (
            (pl.col("edregtime") > pl.col("admittime"))
            | (pl.col("edouttime") < pl.col("edregtime"))
        )
    ).height
    if bad:
        problems.append(f"admissions: {bad} ED window(s) inconsistent with admittime")
    last = adm.group_by("subject_id").agg(
        pl.col("dischtime").max().alias("last_disch"),
        pl.col("deathtime").max().alias("last_death"),
    )
    joined = pat.join(last, on="subject_id", how="left")
    bad = joined.filter(
        pl.col("dod").is_not_null() & (pl.col("dod") < pl.col("last_disch").dt.date())
    ).height
    if bad:
        problems.append(f"patients: {bad} dod(s) before the last dischtime")
    bad = joined.filter(
        pl.col("last_death").is_not_null()
        & (pl.col("dod").is_null() | (pl.col("dod") < pl.col("last_death").dt.date()))
    ).height
    if bad:
        problems.append(f"patients: {bad} in-hospital death(s) without a matching dod")
    ages = pat.get_column("anchor_age").drop_nulls()
    if not ages.is_empty() and (((ages > 88) & (ages != 91)) | (ages < 0)).sum():
        problems.append("patients: anchor_age > 88 must be written as 91")
    return problems


def _plan_problems(frames: dict[str, pl.DataFrame], plan: FixturePlan) -> list[str]:
    problems: list[str] = []
    adm = {
        r["hadm_id"]: (r["admittime"], r["dischtime"])
        for r in frames["admissions"]
        .select("hadm_id", "admittime", "dischtime")
        .iter_rows(named=True)
    }
    tr = frames["transfers"]
    for seg in plan.icu_segments:
        window = adm.get(seg.hadm_id)
        if window is None:
            problems.append(f"icu segment {seg.stay_id}: hadm_id not in admissions")
            continue
        if not (window[0] <= seg.intime < seg.outtime <= window[1]):
            problems.append(f"icu segment {seg.stay_id}: outside its admission window")
        match = tr.filter(
            (pl.col("hadm_id") == seg.hadm_id)
            & (pl.col("careunit") == seg.careunit)
            & (pl.col("intime") == seg.intime)
            & (pl.col("outtime") == seg.outtime)
        ).height
        if match != 1:
            problems.append(
                f"icu segment {seg.stay_id}: {match} matching transfers row(s), expected 1"
            )
    ids = [s.subject_id for s in plan.subjects]
    if ids != list(range(plan.spec.first_subject_id, plan.spec.first_subject_id + len(ids))):
        problems.append("plan: subject ids are not consecutive from first_subject_id")
    return problems


def validate(
    frames: dict[str, pl.DataFrame],
    contract: Contract | None = None,
    plan: FixturePlan | None = None,
) -> list[str]:
    """Every problem with ``frames`` (``{table: frame}`` for the 22 hosp tables); [] = valid."""
    from mimicwarehouse.schema.contract import load_contract

    contract = contract or load_contract()
    problems: list[str] = []
    expected = [t.name for t in contract.by_schema(HOSP_SCHEMA)]
    missing = [t for t in expected if t not in frames]
    if missing:
        problems.append(f"missing hosp frame(s): {missing}")
    extra = [t for t in frames if t not in expected]
    if extra:
        problems.append(f"unexpected frame(s): {extra}")
    for name in expected:
        if name not in frames:
            continue
        frame = frames[name]
        problems += _schema_problems(name, frame, contract)
        problems += _id_problems(name, frame)
        problems += _pk_problems(name, frame, contract)
        problems += _sort_problems(name, frame, contract)
    if problems:
        return problems  # structural problems first; the joins below assume the contract shape
    for fk in contract.foreign_keys:
        if not fk.table.startswith(f"{HOSP_SCHEMA}.") or not fk.ref_table.startswith(
            f"{HOSP_SCHEMA}."
        ):
            continue
        child = fk.table.split(".", 1)[1]
        parent = fk.ref_table.split(".", 1)[1]
        problems += _fk_problems(
            frames, child, "|".join(fk.columns), parent, "|".join(fk.ref_columns)
        )
    for child, cols, parent, ref in EXTRA_FKS:
        problems += _fk_problems(frames, child, cols, parent, ref)
    # (subject_id, hadm_id) pairs must be the admissions' pairs everywhere
    pairs = frames["admissions"].select("subject_id", "hadm_id").unique()
    for name, frame in frames.items():
        if {"subject_id", "hadm_id"} <= set(frame.columns) and name != "admissions":
            got = frame.select("subject_id", "hadm_id").drop_nulls().unique()
            bad = got.join(pairs, on=["subject_id", "hadm_id"], how="anti").height
            if bad:
                problems.append(f"{name}: {bad} (subject_id, hadm_id) pair(s) not in admissions")
    problems += _admission_problems(frames)
    if plan is not None:
        problems += _plan_problems(frames, plan)
    return problems


def assert_valid(
    frames: dict[str, pl.DataFrame],
    contract: Contract | None = None,
    plan: FixturePlan | None = None,
) -> None:
    problems = validate(frames, contract, plan)
    if problems:
        raise FixtureError(problems)


__all__ = ["EXTRA_FKS", "ID_COLUMNS", "FixtureError", "assert_valid", "validate"]
