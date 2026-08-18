"""Fixture spec and plan - the deterministic skeleton every fixture table hangs off (EP-11).

:class:`FixtureSpec` (pydantic, frozen) holds the knobs (seed, subject count, id floors, rates);
:func:`build_plan` turns a spec into a :class:`FixturePlan` with **one** ``numpy`` generator seeded
from ``spec.seed``: subjects (consecutive ids from ``first_subject_id``, so ``subject_id % 100``
spans the 100 buckets and the dev filter ``< 5`` keeps a known handful), admissions with
``admittime`` / ``dischtime`` (LOS lognormal, clipped to ``los_days``), in-hospital deaths and
``dod``, an ED prelude for ``ed_fraction`` of admissions, the ADT **segment chain** per admission
(ward / ICU intervals that :func:`hosp.transfers` writes verbatim) and the ICU segments
(``plan.icu_segments``) that EP-12 turns into ``icustays`` - so ``transfers`` and ``icustays``
agree by construction. Planted phenotype signal (``AdmissionPlan.traits``: ``aki`` / ``sepsis``
/ ``t2dm``) is chosen here so labs, cultures, prescriptions and codes agree too.

Everything downstream draws from **per-table child generators** (:func:`table_rng`) keyed by a
stable CRC of the table name, so adding a table (EP-12's icu generators) never perturbs the
bytes of an existing one. Same spec => byte-identical output (the "fixture drift" test).

MIMIC caveats mirrored on purpose (DESIGN section 7): ages >= 89 written as 91, shifted years
(``anchor_year`` 2110-2200), ``anchor_year_group`` from the five real labels, ``dod`` within a
year of the last discharge and never before an in-hospital ``deathtime``, ICD-9 for the
2008-2010 / 2011-2013 groups, ICD-10 from 2017 on, a coin flip inside 2014-2016 (the switch).
Guard rule (EP-4 G4): no constant here is an 8-digit number starting with 1, 2 or 3 - the seed
validator refuses one, and every fixture id starts at 90 000 000 (D-27).
"""

from __future__ import annotations

import math
import zlib
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

if TYPE_CHECKING:  # pragma: no cover
    import numpy as np

    from mimicwarehouse.fixtures.vocab import Vocab

#: Synthetic ids start here (guard.FIXTURE_ID_FLOOR, D-27).
FIXTURE_ID_FLOOR = 90_000_000
#: The real MIMIC id bands the guard refuses (CLAUDE.md section 2) - written with underscores.
REAL_ID_BAND: tuple[int, int] = (10_000_000, 39_999_999)
#: The five real ``anchor_year_group`` labels, in order.
ANCHOR_YEAR_GROUPS: tuple[str, ...] = (
    "2008 - 2010",
    "2011 - 2013",
    "2014 - 2016",
    "2017 - 2019",
    "2020 - 2022",
)
#: Groups that are ICD-9 only / ICD-10 only; the middle group is a coin flip per admission.
ICD9_GROUPS: frozenset[str] = frozenset({"2008 - 2010", "2011 - 2013"})
ICD10_GROUPS: frozenset[str] = frozenset({"2017 - 2019", "2020 - 2022"})
MIXED_GROUP = "2014 - 2016"
#: Ages >= 89 are shown as 91 in MIMIC-IV.
AGE_CAP_LABEL = 91
#: Planted phenotype traits (``AdmissionPlan.traits``).
TRAITS: tuple[str, ...] = ("aki", "sepsis", "t2dm")

MINUTE = timedelta(minutes=1)
HOUR = timedelta(hours=1)
DAY = timedelta(days=1)


class FixtureSpec(BaseModel):
    """Knobs of the synthetic mini-MIMIC. Defaults are the committed fixture (D-27)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    seed: int = 2026
    n_subjects: int = Field(default=120, ge=1, le=100_000)
    first_subject_id: int = Field(default=FIXTURE_ID_FLOOR, ge=FIXTURE_ID_FLOOR)
    first_hadm_id: int = Field(default=FIXTURE_ID_FLOOR, ge=FIXTURE_ID_FLOOR)
    first_stay_id: int = Field(default=FIXTURE_ID_FLOOR, ge=FIXTURE_ID_FLOOR)
    first_event_id: int = Field(
        default=FIXTURE_ID_FLOOR,
        ge=FIXTURE_ID_FLOOR,
        description="floor for the other row ids (labevent_id, specimen_id, microevent_id, "
        "micro_specimen_id, transfer_id, pharmacy_id)",
    )
    admissions_per_subject_mean: float = Field(default=1.5, ge=1.0, le=6.0)
    max_admissions_per_subject: int = Field(default=5, ge=1, le=20)
    icu_fraction: float = Field(default=0.4, ge=0.0, le=1.0)
    mortality_rate: float = Field(
        default=0.08, ge=0.0, le=1.0, description="in-hospital deaths as a fraction of admissions"
    )
    dod_fraction: float = Field(
        default=0.15,
        ge=0.0,
        le=1.0,
        description="fraction of subjects (surviving discharge) with a dod within a year",
    )
    ed_fraction: float = Field(default=0.5, ge=0.0, le=1.0)
    labs_per_admission: int = Field(default=40, ge=0, le=2000)
    outpatient_lab_fraction: float = Field(
        default=0.2,
        ge=0.0,
        le=2.0,
        description="extra hadm_id-less labs per subject, as a "
        "fraction of labs_per_admission (drawn outside every admission)",
    )
    n_providers: int = Field(default=40, ge=1, le=9999)
    anchor_year_range: tuple[int, int] = (2110, 2200)
    anchor_age_range: tuple[int, int] = (18, 88)
    age_cap_fraction: float = Field(default=0.04, ge=0.0, le=1.0)
    los_days: tuple[float, float] = (1.0, 20.0)
    los_lognormal: tuple[float, float] = Field(
        default=(math.log(3.5), 0.7), description="(mu, sigma) of ln(LOS in days)"
    )
    planted_per_trait: int = Field(default=6, ge=0, le=1000)
    # icu knobs (EP-12): read only by mimicwarehouse.fixtures.icu, never by build_plan
    n_caregivers: int = Field(
        default=15, ge=1, le=9999, description="caregiver ids, consecutive from first_event_id"
    )
    vent_fraction: float = Field(
        default=0.4,
        ge=0.0,
        le=1.0,
        description="ICU stays with invasive ventilation (sepsis / in-ICU deaths are boosted)",
    )
    vasopressor_fraction: float = Field(
        default=0.25,
        ge=0.0,
        le=1.0,
        description="non-sepsis ICU stays with a vasopressor drip (planted sepsis stays "
        "always get norepinephrine)",
    )

    @model_validator(mode="after")
    def _check(self) -> FixtureSpec:
        lo, hi = REAL_ID_BAND
        if lo <= self.seed <= hi:
            raise ValueError(
                f"seed {self.seed} lies in the real MIMIC id band {lo:_}-{hi:_} (guard G4); "
                "pick another"
            )
        if self.anchor_year_range[0] > self.anchor_year_range[1]:
            raise ValueError("anchor_year_range must be (lo, hi) with lo <= hi")
        if not (0 < self.anchor_age_range[0] <= self.anchor_age_range[1] < AGE_CAP_LABEL):
            raise ValueError(f"anchor_age_range must lie inside 1..{AGE_CAP_LABEL - 1}")
        if not (0 < self.los_days[0] <= self.los_days[1]):
            raise ValueError("los_days must be (lo, hi) with 0 < lo <= hi")
        return self

    def canonical(self) -> dict[str, Any]:
        """JSON-able dump for ``manifest.json``."""
        return self.model_dump(mode="json")


# ---------------------------------------------------------------------------
# Plan records
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Segment:
    """One ADT interval of an admission (a ward or an ICU careunit)."""

    careunit: str
    intime: datetime
    outtime: datetime
    is_icu: bool


@dataclass(frozen=True, slots=True)
class IcuSegment:
    """One ICU stay - EP-12 writes exactly these as ``icustays``."""

    stay_id: int
    subject_id: int
    hadm_id: int
    careunit: str
    intime: datetime
    outtime: datetime

    @property
    def los_days(self) -> float:
        return (self.outtime - self.intime) / DAY


@dataclass(frozen=True, slots=True)
class AdmissionPlan:
    hadm_id: int
    subject_id: int
    admittime: datetime
    dischtime: datetime
    died: bool
    icd_version: int
    ed: bool
    edregtime: datetime | None
    edouttime: datetime | None
    admission_type: str
    admission_location: str
    discharge_location: str | None
    insurance: str
    segments: tuple[Segment, ...]
    icu: IcuSegment | None
    traits: frozenset[str] = frozenset()

    @property
    def deathtime(self) -> datetime | None:
        return self.dischtime if self.died else None

    @property
    def los(self) -> timedelta:
        return self.dischtime - self.admittime

    def has(self, trait: str) -> bool:
        return trait in self.traits


@dataclass(frozen=True, slots=True)
class SubjectPlan:
    subject_id: int
    gender: str
    anchor_age: int
    anchor_year: int
    anchor_year_group: str
    language: str | None
    marital_status: str | None
    race: str
    dod: date | None
    admissions: tuple[AdmissionPlan, ...] = field(repr=False)

    @property
    def last_dischtime(self) -> datetime:
        return max(a.dischtime for a in self.admissions)

    @property
    def first_admittime(self) -> datetime:
        return min(a.admittime for a in self.admissions)


@dataclass(frozen=True, slots=True)
class FixturePlan:
    spec: FixtureSpec
    subjects: tuple[SubjectPlan, ...]
    providers: tuple[str, ...]

    @property
    def admissions(self) -> tuple[AdmissionPlan, ...]:
        return tuple(a for s in self.subjects for a in s.admissions)

    @property
    def icu_segments(self) -> tuple[IcuSegment, ...]:
        return tuple(a.icu for a in self.admissions if a.icu is not None)

    def subject(self, subject_id: int) -> SubjectPlan:
        for s in self.subjects:
            if s.subject_id == subject_id:
                return s
        raise KeyError(subject_id)

    def admissions_with(self, trait: str) -> tuple[AdmissionPlan, ...]:
        return tuple(a for a in self.admissions if a.has(trait))


# ---------------------------------------------------------------------------
# Random helpers
# ---------------------------------------------------------------------------


def table_rng(spec: FixtureSpec, name: str) -> np.random.Generator:
    """Child generator for one table / stage: ``default_rng([seed, crc32(name)])`` - stable across
    runs and independent of every other table's draws."""
    import numpy as np

    return np.random.default_rng([spec.seed, zlib.crc32(name.encode("ascii"))])


def pick(rng: np.random.Generator, weighted: Any) -> Any:
    """One value from a :class:`~mimicwarehouse.fixtures.vocab.Weighted` list."""
    idx = int(rng.choice(len(weighted.values), p=weighted.probabilities))
    return weighted.values[idx]


def pick_weighted(rng: np.random.Generator, items: Any, weights: Any) -> Any:
    total = float(sum(weights))
    idx = int(rng.choice(len(items), p=[w / total for w in weights]))
    return items[idx]


def minute(dt: datetime) -> datetime:
    """Truncate to the minute (fixture timestamps are minute-resolution)."""
    return dt.replace(second=0, microsecond=0)


def uniform_dt(rng: np.random.Generator, start: datetime, end: datetime) -> datetime:
    """A minute-resolution instant uniform in ``[start, end)`` (``start`` when the interval is
    shorter than a minute)."""
    span = (end - start) / MINUTE
    if span < 1:
        return minute(start)
    return minute(start + MINUTE * int(rng.integers(0, int(span))))


# ---------------------------------------------------------------------------
# Plan builder
# ---------------------------------------------------------------------------


def _split_durations(rng: np.random.Generator, total: timedelta, k: int) -> list[timedelta]:
    """Split ``total`` into ``k`` minute-rounded parts, each at least 8 % of the total."""
    if k == 1:
        return [total]
    floor = 0.08
    d = rng.dirichlet([1.0] * k)
    fractions = [floor + (1.0 - floor * k) * float(x) for x in d]
    minutes_total = int(total / MINUTE)
    parts = [int(minutes_total * f) for f in fractions]
    parts[-1] = minutes_total - sum(parts[:-1])
    return [MINUTE * p for p in parts]


def _segments(
    rng: np.random.Generator,
    vocab: Vocab,
    admittime: datetime,
    dischtime: datetime,
    *,
    icu: bool,
    died: bool,
) -> tuple[Segment, ...]:
    """The ward/ICU chain of one admission (contiguous, first starts at admittime, last ends
    at dischtime). ICU admissions: [ICU][ward], [ward][ICU][ward] or, for deaths, [ward][ICU] /
    [ICU]; non-ICU: [ward] or [ward][ward]."""
    wards = vocab.weighted("ward_careunits")
    icus = vocab.weighted("icu_careunits")
    if icu:
        if died:
            shape = ["icu"] if rng.random() < 0.5 else ["ward", "icu"]
        elif rng.random() < 0.5:
            shape = ["icu", "ward"]
        else:
            shape = ["ward", "icu", "ward"]
    else:
        shape = ["ward"] if rng.random() < 0.6 else ["ward", "ward"]
    parts = _split_durations(rng, dischtime - admittime, len(shape))
    out: list[Segment] = []
    t = admittime
    for kind, part in zip(shape, parts, strict=True):
        end = t + part
        unit = pick(rng, icus) if kind == "icu" else pick(rng, wards)
        out.append(Segment(careunit=unit, intime=t, outtime=end, is_icu=kind == "icu"))
        t = end
    # never two identical wards back to back
    if (
        len(out) == 2
        and not out[0].is_icu
        and not out[1].is_icu
        and out[0].careunit == out[1].careunit
    ):
        alt = pick(rng, wards)
        while alt == out[0].careunit:
            alt = pick(rng, wards)
        out[1] = Segment(alt, out[1].intime, out[1].outtime, False)
    return tuple(out)


def _admission_count(rng: np.random.Generator, spec: FixtureSpec) -> int:
    n = 1 + int(rng.poisson(spec.admissions_per_subject_mean - 1.0))
    return max(1, min(spec.max_admissions_per_subject, n))


def _los(rng: np.random.Generator, spec: FixtureSpec) -> timedelta:
    mu, sigma = spec.los_lognormal
    days = float(rng.lognormal(mu, sigma))
    days = min(max(days, spec.los_days[0]), spec.los_days[1])
    return MINUTE * int(days * 24 * 60)


def _icd_version(rng: np.random.Generator, group: str) -> int:
    if group in ICD9_GROUPS:
        return 9
    if group in ICD10_GROUPS:
        return 10
    return 9 if rng.random() < 0.5 else 10


def _plan_admissions(
    rng: np.random.Generator,
    spec: FixtureSpec,
    vocab: Vocab,
    subject_id: int,
    anchor_year: int,
    group: str,
    next_hadm: int,
    next_stay: int,
) -> tuple[list[AdmissionPlan], int, int]:
    n = _admission_count(rng, spec)
    p_death_last = min(1.0, spec.mortality_rate * spec.admissions_per_subject_mean)
    died_last = rng.random() < p_death_last
    admits: list[AdmissionPlan] = []
    t = (
        datetime(anchor_year, 1, 1)
        + DAY * int(rng.integers(0, 365))
        + MINUTE * int(rng.integers(0, 24 * 60))
    )
    for i in range(n):
        last = i == n - 1
        admittime = minute(t)
        dischtime = admittime + _los(rng, spec)
        died = died_last and last
        icu = rng.random() < spec.icu_fraction
        ed = rng.random() < spec.ed_fraction
        edreg = admittime - MINUTE * int(rng.integers(60, 8 * 60)) if ed else None
        adm_type = pick(rng, vocab.nested_weighted("admission_types", "ed" if ed else "non_ed"))
        adm_loc = pick(rng, vocab.nested_weighted("admission_locations", "ed" if ed else "non_ed"))
        disch_loc = (
            str(vocab.categories["death_discharge_location"])
            if died
            else pick(rng, vocab.weighted("discharge_locations"))
        )
        segments = _segments(rng, vocab, admittime, dischtime, icu=icu, died=died)
        icu_seg = None
        for s in segments:
            if s.is_icu:
                icu_seg = IcuSegment(
                    next_stay, subject_id, next_hadm, s.careunit, s.intime, s.outtime
                )
                next_stay += 1
        admits.append(
            AdmissionPlan(
                hadm_id=next_hadm,
                subject_id=subject_id,
                admittime=admittime,
                dischtime=dischtime,
                died=died,
                icd_version=_icd_version(rng, group),
                ed=ed,
                edregtime=edreg,
                edouttime=admittime if ed else None,
                admission_type=str(adm_type),
                admission_location=str(adm_loc),
                discharge_location=None if disch_loc is None else str(disch_loc),
                insurance=str(pick(rng, vocab.weighted("insurance"))),
                segments=segments,
                icu=icu_seg,
            )
        )
        next_hadm += 1
        t = dischtime + DAY * int(rng.integers(20, 500)) + MINUTE * int(rng.integers(0, 24 * 60))
    return admits, next_hadm, next_stay


def _plant_traits(rng: np.random.Generator, spec: FixtureSpec, subjects: list[SubjectPlan]) -> None:
    """Mark ``planted_per_trait`` admissions (LOS >= 3 d, preferring ICU stays for sepsis/aki)
    per trait; rebuilds the frozen records in place."""
    if spec.planted_per_trait == 0:
        return
    for trait in TRAITS:
        candidates = [
            (si, ai)
            for si, s in enumerate(subjects)
            for ai, a in enumerate(s.admissions)
            if a.los >= 3 * DAY and trait not in a.traits
        ]
        if trait in ("aki", "sepsis"):
            icu_first = [c for c in candidates if subjects[c[0]].admissions[c[1]].icu is not None]
            if len(icu_first) >= spec.planted_per_trait:
                candidates = icu_first
        if not candidates:
            continue
        k = min(spec.planted_per_trait, len(candidates))
        chosen = rng.choice(len(candidates), size=k, replace=False)
        for idx in sorted(int(c) for c in chosen):
            si, ai = candidates[idx]
            s = subjects[si]
            a = s.admissions[ai]
            new_a = AdmissionPlan(
                **{f: getattr(a, f) for f in a.__dataclass_fields__ if f != "traits"},
                traits=a.traits | {trait},
            )
            adms = list(s.admissions)
            adms[ai] = new_a
            subjects[si] = SubjectPlan(
                **{f: getattr(s, f) for f in s.__dataclass_fields__ if f != "admissions"},
                admissions=tuple(adms),
            )


def build_plan(spec: FixtureSpec | None = None, vocab: Vocab | None = None) -> FixturePlan:
    """Build the whole skeleton from ``spec`` (defaults: the committed fixture)."""
    import numpy as np

    from mimicwarehouse.fixtures.vocab import load_vocab

    spec = spec or FixtureSpec()
    vocab = vocab or load_vocab()
    rng = np.random.default_rng(spec.seed)
    groups = tuple(str(g) for g in vocab.categories["anchor_year_groups"])
    if tuple(groups) != ANCHOR_YEAR_GROUPS:
        raise ValueError("categories.yaml anchor_year_groups must be the five real labels")

    subjects: list[SubjectPlan] = []
    next_hadm = spec.first_hadm_id
    next_stay = spec.first_stay_id
    for i in range(spec.n_subjects):
        subject_id = spec.first_subject_id + i
        gender = str(pick(rng, vocab.weighted("gender")))
        lo, hi = spec.anchor_age_range
        age = (
            AGE_CAP_LABEL if rng.random() < spec.age_cap_fraction else int(rng.integers(lo, hi + 1))
        )
        ylo, yhi = spec.anchor_year_range
        anchor_year = int(rng.integers(ylo, yhi + 1))
        group = groups[int(rng.integers(0, len(groups)))]
        admits, next_hadm, next_stay = _plan_admissions(
            rng, spec, vocab, subject_id, anchor_year, group, next_hadm, next_stay
        )
        last = admits[-1]
        if last.died:
            dod: date | None = last.dischtime.date()
        elif rng.random() < spec.dod_fraction:
            dod = (last.dischtime + DAY * int(rng.integers(0, 366))).date()
        else:
            dod = None
        subjects.append(
            SubjectPlan(
                subject_id=subject_id,
                gender=gender,
                anchor_age=age,
                anchor_year=anchor_year,
                anchor_year_group=group,
                language=pick(rng, vocab.weighted("language")),
                marital_status=pick(rng, vocab.weighted("marital_status")),
                race=str(pick(rng, vocab.weighted("race"))),
                dod=dod,
                admissions=tuple(admits),
            )
        )
    _plant_traits(rng, spec, subjects)
    providers = tuple(f"P9{n:04d}" for n in range(1, spec.n_providers + 1))
    return FixturePlan(spec=spec, subjects=tuple(subjects), providers=providers)


__all__ = [
    "AGE_CAP_LABEL",
    "ANCHOR_YEAR_GROUPS",
    "DAY",
    "FIXTURE_ID_FLOOR",
    "HOUR",
    "ICD9_GROUPS",
    "ICD10_GROUPS",
    "MINUTE",
    "MIXED_GROUP",
    "REAL_ID_BAND",
    "TRAITS",
    "AdmissionPlan",
    "FixturePlan",
    "FixtureSpec",
    "IcuSegment",
    "Segment",
    "SubjectPlan",
    "build_plan",
    "minute",
    "pick",
    "pick_weighted",
    "table_rng",
    "uniform_dt",
]
