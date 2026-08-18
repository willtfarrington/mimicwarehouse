"""Contract + integrity checks over generated fixture frames (EP-11 item 5, EP-12 item 2).

:func:`validate` returns a list of human-readable problems (empty = valid); :func:`assert_valid`
raises :class:`FixtureError` with them. Checked, per the briefs: every frame has exactly the
contract columns (order and castable dtypes); every id column is >= 90 000 000 (D-27) and no
integer column ever holds a value inside the real MIMIC bands (guard G4, whatever the column);
foreign keys (``hadm_id`` -> admissions, ``subject_id`` -> patients, ``itemid`` -> d_labitems,
ICD codes -> ``d_icd_*``, ``hcpcs_cd`` -> d_hcpcs, ``emar_id`` -> emar, ``poe_id`` -> poe,
``pharmacy_id`` -> pharmacy, provider ids -> provider, ``(subject_id, hadm_id)`` consistency);
declared primary keys are unique; ``dischtime > admittime``; ``deathtime`` <-> flag; ``dod`` >=
last ``dischtime`` and >= ``deathtime``; every ICU segment lies inside its admission and matches
a transfers row when a plan is given.

With the icu frames (``validate(hosp, contract, plan, icu=icu_frames)``, EP-12): the same
structural checks over the 9 ``mimiciv_icu`` tables, the contract's icu -> hosp / icu -> icu
foreign keys, ``caregiver_id`` -> caregiver, every ``icustays`` row inside its admission with
exactly one matching ``transfers`` ICU row (and equal to the plan's segments), ``los`` = the
window in days, every event's ``(subject_id, hadm_id)`` = its stay's, every event inside
``[intime - 6 h, outtime + 6 h]``, ``storetime >= charttime`` / ``endtime >= starttime``, and
every ``itemid`` present in ``d_items`` with the ``linksto`` of the table it appears in.
Everything runs on Polars frames in memory - it never prints a row.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

import polars as pl

from mimicwarehouse.fixtures.hosp import HOSP_SCHEMA, polars_schema
from mimicwarehouse.fixtures.icu import EVENT_WINDOW_SLACK, ICU_SCHEMA
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
#: Documented icu links keys.yaml leaves to integrity tests: caregiver ids -> caregiver.
ICU_EXTRA_FKS: tuple[tuple[str, str, str, str], ...] = tuple(
    (t, "caregiver_id", "caregiver", "caregiver_id")
    for t in (
        "chartevents",
        "datetimeevents",
        "inputevents",
        "ingredientevents",
        "outputevents",
        "procedureevents",
    )
)
#: icu event tables: (table, time columns whose values must lie inside the stay window).
ICU_EVENT_TIMES: dict[str, tuple[str, ...]] = {
    "chartevents": ("charttime",),
    "datetimeevents": ("charttime",),
    "inputevents": ("starttime", "endtime"),
    "ingredientevents": ("starttime", "endtime"),
    "outputevents": ("charttime",),
    "procedureevents": ("starttime", "endtime"),
}


class FixtureError(RuntimeError):
    """The generated fixture violates the contract or an integrity rule."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = list(problems)
        head = f"{len(self.problems)} fixture problem(s)"
        super().__init__("\n".join([head, *(f"  - {p}" for p in self.problems[:50])]))


# ---------------------------------------------------------------------------
# Structural checks (any schema)
# ---------------------------------------------------------------------------


def _schema_problems(schema: str, name: str, frame: pl.DataFrame, contract: Contract) -> list[str]:
    table = contract.table(schema, name)
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
    frames: Mapping[str, pl.DataFrame],
    child: str,
    cols: str,
    parent: str,
    ref_cols: str,
    parents: Mapping[str, pl.DataFrame] | None = None,
) -> list[str]:
    parents = frames if parents is None else parents
    if child not in frames or parent not in parents:
        return []
    ccols = cols.split("|")
    pcols = ref_cols.split("|")
    c = frames[child].select(ccols).drop_nulls().unique()
    p = parents[parent].select(pcols).unique()
    if c.is_empty():
        return []
    missing = c.join(p, left_on=ccols, right_on=pcols, how="anti")
    if missing.height:
        return [f"{child}({cols}) -> {parent}({ref_cols}): {missing.height} orphan value(s)"]
    return []


def _pk_problems(schema: str, name: str, frame: pl.DataFrame, contract: Contract) -> list[str]:
    table = contract.table(schema, name)
    keys = table.primary_key or table.uniqueness_hint
    if not keys or frame.is_empty():
        return []
    dupes = frame.height - frame.select(list(keys)).unique().height
    label = "primary key" if table.primary_key else "uniqueness hint"
    return [f"{name}: {dupes} duplicate row(s) on {label} {list(keys)}"] if dupes else []


def _sort_problems(schema: str, name: str, frame: pl.DataFrame, contract: Contract) -> list[str]:
    table = contract.table(schema, name)
    if not table.sort_keys or frame.height < 2:
        return []
    sorted_frame = frame.sort(list(table.sort_keys), maintain_order=True)
    if not frame.select(list(table.sort_keys)).equals(sorted_frame.select(list(table.sort_keys))):
        return [f"{name}: rows are not sorted by {list(table.sort_keys)}"]
    return []


def _structural_problems(
    schema: str, frames: Mapping[str, pl.DataFrame], contract: Contract
) -> list[str]:
    """Missing / extra frames, columns + dtypes + NOT NULL, id floors, PKs, sort keys."""
    label = schema.split("_", 1)[-1]
    problems: list[str] = []
    expected = [t.name for t in contract.by_schema(schema)]
    missing = [t for t in expected if t not in frames]
    if missing:
        problems.append(f"missing {label} frame(s): {missing}")
    extra = [t for t in frames if t not in expected]
    if extra:
        problems.append(f"unexpected {label} frame(s): {extra}")
    for name in expected:
        if name not in frames:
            continue
        frame = frames[name]
        problems += _schema_problems(schema, name, frame, contract)
        problems += _id_problems(name, frame)
        problems += _pk_problems(schema, name, frame, contract)
        problems += _sort_problems(schema, name, frame, contract)
    return problems


def _contract_fk_problems(
    contract: Contract,
    child_schema: str,
    frames: Mapping[str, pl.DataFrame],
    parents_by_schema: Mapping[str, Mapping[str, pl.DataFrame]],
) -> list[str]:
    problems: list[str] = []
    for fk in contract.foreign_keys:
        if not fk.table.startswith(f"{child_schema}."):
            continue
        parent_schema, parent = fk.ref_table.split(".", 1)
        if parent_schema not in parents_by_schema:
            continue
        child = fk.table.split(".", 1)[1]
        problems += _fk_problems(
            frames,
            child,
            "|".join(fk.columns),
            parent,
            "|".join(fk.ref_columns),
            parents=parents_by_schema[parent_schema],
        )
    return problems


def _pair_problems(
    frames: Mapping[str, pl.DataFrame], admissions: pl.DataFrame, *, skip: str | None
) -> list[str]:
    """``(subject_id, hadm_id)`` pairs must be the admissions' pairs everywhere."""
    problems: list[str] = []
    pairs = admissions.select("subject_id", "hadm_id").unique()
    for name, frame in frames.items():
        if {"subject_id", "hadm_id"} <= set(frame.columns) and name != skip:
            got = frame.select("subject_id", "hadm_id").drop_nulls().unique()
            bad = got.join(pairs, on=["subject_id", "hadm_id"], how="anti").height
            if bad:
                problems.append(f"{name}: {bad} (subject_id, hadm_id) pair(s) not in admissions")
    return problems


# ---------------------------------------------------------------------------
# hosp semantics
# ---------------------------------------------------------------------------


def _admission_problems(frames: Mapping[str, pl.DataFrame]) -> list[str]:
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


def _plan_problems(frames: Mapping[str, pl.DataFrame], plan: FixturePlan) -> list[str]:
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


# ---------------------------------------------------------------------------
# icu semantics
# ---------------------------------------------------------------------------


def _icustays_problems(
    hosp: Mapping[str, pl.DataFrame], icu: Mapping[str, pl.DataFrame], plan: FixturePlan | None
) -> list[str]:
    problems: list[str] = []
    stays = icu["icustays"]
    adm = hosp["admissions"].select("hadm_id", "subject_id", "admittime", "dischtime")
    joined = stays.join(adm, on="hadm_id", how="left", suffix="_adm")
    bad = joined.filter(pl.col("subject_id_adm").is_null()).height
    if bad:
        problems.append(f"icustays: {bad} row(s) whose hadm_id is not in admissions")
    bad = joined.filter(pl.col("subject_id") != pl.col("subject_id_adm")).height
    if bad:
        problems.append(f"icustays: {bad} row(s) whose subject_id differs from the admission's")
    bad = joined.filter(
        pl.col("subject_id_adm").is_not_null()
        & ~(
            (pl.col("admittime") <= pl.col("intime"))
            & (pl.col("intime") < pl.col("outtime"))
            & (pl.col("outtime") <= pl.col("dischtime"))
        )
    ).height
    if bad:
        problems.append(f"icustays: {bad} stay(s) outside [admittime, dischtime]")
    los = ((pl.col("outtime") - pl.col("intime")).dt.total_seconds() / 86_400.0).alias("los_calc")
    bad = stays.with_columns(los).filter((pl.col("los") - pl.col("los_calc")).abs() > 1e-6).height
    if bad:
        problems.append(f"icustays: {bad} row(s) whose los is not (outtime - intime) in days")
    tr = hosp["transfers"].filter(pl.col("careunit").is_not_null())
    matches = (
        stays.select("stay_id", "hadm_id", "first_careunit", "intime", "outtime")
        .join(
            tr.select("hadm_id", "careunit", "intime", "outtime"),
            left_on=["hadm_id", "first_careunit", "intime", "outtime"],
            right_on=["hadm_id", "careunit", "intime", "outtime"],
            how="left",
            coalesce=False,
        )
        .group_by("stay_id")
        .agg(pl.col("hadm_id_right").is_not_null().sum().alias("n"))
    )
    bad = matches.filter(pl.col("n") != 1).height
    if bad:
        problems.append(f"icustays: {bad} stay(s) without exactly one matching transfers row")
    if plan is not None:
        expected = {
            (s.stay_id, s.subject_id, s.hadm_id, s.careunit, s.intime, s.outtime)
            for s in plan.icu_segments
        }
        got = {
            (
                r["stay_id"],
                r["subject_id"],
                r["hadm_id"],
                r["first_careunit"],
                r["intime"],
                r["outtime"],
            )
            for r in stays.select(
                "stay_id", "subject_id", "hadm_id", "first_careunit", "intime", "outtime"
            ).iter_rows(named=True)
        }
        if got != expected:
            problems.append(
                f"icustays: {len(got ^ expected)} row(s) differ from the plan's icu segments"
            )
    return problems


def _icu_event_problems(icu: Mapping[str, pl.DataFrame]) -> list[str]:
    problems: list[str] = []
    stays = icu["icustays"].select(
        "stay_id",
        pl.col("subject_id").alias("stay_subject"),
        pl.col("hadm_id").alias("stay_hadm"),
        (pl.col("intime") - EVENT_WINDOW_SLACK).alias("lo"),
        (pl.col("outtime") + EVENT_WINDOW_SLACK).alias("hi"),
    )
    d_items = icu["d_items"].select("itemid", "linksto")
    for name, time_cols in ICU_EVENT_TIMES.items():
        frame = icu[name]
        rows = frame.filter(pl.col("stay_id").is_not_null())
        joined = rows.join(stays, on="stay_id", how="left")
        bad = joined.filter(pl.col("stay_subject").is_null()).height
        if bad:
            problems.append(f"{name}: {bad} row(s) whose stay_id is not in icustays")
        bad = joined.filter(
            (pl.col("subject_id") != pl.col("stay_subject"))
            | (pl.col("hadm_id") != pl.col("stay_hadm"))
        ).height
        if bad:
            problems.append(
                f"{name}: {bad} row(s) whose (subject_id, hadm_id) differ from the stay's"
            )
        for col in time_cols:
            bad = joined.filter((pl.col(col) < pl.col("lo")) | (pl.col(col) > pl.col("hi"))).height
            if bad:
                problems.append(
                    f"{name}.{col}: {bad} value(s) outside [intime - 6 h, outtime + 6 h]"
                )
        if "storetime" in frame.columns:
            first = time_cols[0]
            bad = frame.filter(pl.col("storetime") < pl.col(first)).height
            if bad:
                problems.append(f"{name}: {bad} row(s) with storetime < {first}")
        if len(time_cols) == 2:
            bad = frame.filter(pl.col(time_cols[1]) < pl.col(time_cols[0])).height
            if bad:
                problems.append(f"{name}: {bad} row(s) with {time_cols[1]} < {time_cols[0]}")
        items = frame.select("itemid").unique().join(d_items, on="itemid", how="left")
        bad = items.filter(pl.col("linksto").is_null()).height
        if bad:
            problems.append(f"{name}: {bad} itemid(s) not in d_items")
        bad = items.filter(pl.col("linksto").is_not_null() & (pl.col("linksto") != name)).height
        if bad:
            problems.append(f"{name}: {bad} itemid(s) whose d_items.linksto is another table")
    ce = icu["chartevents"]
    bad = ce.filter(pl.col("valuenum").is_not_null() & pl.col("value").is_null()).height
    if bad:
        problems.append(f"chartevents: {bad} row(s) with valuenum but no value text")
    bad = icu["outputevents"].filter(pl.col("value") < 0).height
    if bad:
        problems.append(f"outputevents: {bad} negative value(s)")
    return problems


def _icu_problems(
    hosp: Mapping[str, pl.DataFrame],
    icu: Mapping[str, pl.DataFrame],
    contract: Contract,
    plan: FixturePlan | None,
) -> list[str]:
    problems = _structural_problems(ICU_SCHEMA, icu, contract)
    if problems:
        return problems
    problems += _contract_fk_problems(
        contract, ICU_SCHEMA, icu, {HOSP_SCHEMA: hosp, ICU_SCHEMA: icu}
    )
    for child, cols, parent, ref in ICU_EXTRA_FKS:
        problems += _fk_problems(icu, child, cols, parent, ref)
    problems += _pair_problems(icu, hosp["admissions"], skip=None)
    problems += _icustays_problems(hosp, icu, plan)
    problems += _icu_event_problems(icu)
    return problems


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def validate(
    frames: Mapping[str, pl.DataFrame],
    contract: Contract | None = None,
    plan: FixturePlan | None = None,
    *,
    icu: Mapping[str, pl.DataFrame] | None = None,
) -> list[str]:
    """Every problem with ``frames`` (``{table: frame}`` for the 22 hosp tables) and, when
    given, ``icu`` (``{table: frame}`` for the 9 icu tables); ``[]`` = valid."""
    from mimicwarehouse.schema.contract import load_contract

    contract = contract or load_contract()
    problems = _structural_problems(HOSP_SCHEMA, frames, contract)
    if problems:
        return problems  # structural problems first; the joins below assume the contract shape
    problems += _contract_fk_problems(contract, HOSP_SCHEMA, frames, {HOSP_SCHEMA: frames})
    for child, cols, parent, ref in EXTRA_FKS:
        problems += _fk_problems(frames, child, cols, parent, ref)
    problems += _pair_problems(frames, frames["admissions"], skip="admissions")
    problems += _admission_problems(frames)
    if plan is not None:
        problems += _plan_problems(frames, plan)
    if icu is not None:
        problems += _icu_problems(frames, icu, contract, plan)
    return problems


def assert_valid(
    frames: Mapping[str, pl.DataFrame],
    contract: Contract | None = None,
    plan: FixturePlan | None = None,
    *,
    icu: Mapping[str, pl.DataFrame] | None = None,
) -> None:
    problems = validate(frames, contract, plan, icu=icu)
    if problems:
        raise FixtureError(problems)


__all__ = [
    "EXTRA_FKS",
    "ICU_EVENT_TIMES",
    "ICU_EXTRA_FKS",
    "ID_COLUMNS",
    "FixtureError",
    "assert_valid",
    "validate",
]
