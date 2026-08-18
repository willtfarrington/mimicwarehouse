"""icu table generators - one function per ``mimiciv_icu`` table (EP-12).

Same shape as :mod:`mimicwarehouse.fixtures.hosp`: every generator takes the shared
:class:`IcuContext` (plan + vocab + contract + cached cross-table stages) and returns a Polars
frame whose columns, order and dtypes come from the EP-9 contract, sorted by the contract
``sort_keys``. Draws come from per-table child generators
(:func:`~mimicwarehouse.fixtures.spec.table_rng`), so adding an icu table never perturbs a hosp
byte, and the cross-table stages are built once per context:

* :attr:`IcuContext.profiles` - one :class:`StayProfile` per ``plan.icu_segments`` entry (the
  stay ids EP-11 already assigned): weight / height, a severity score, the ventilation /
  non-invasive ventilation / arterial-line / CRRT / vasopressor / propofol / insulin **windows**
  and the caregivers on shift. ``icustays`` writes the segments verbatim, so ``transfers`` and
  ``icustays`` agree by construction; ``chartevents`` (FiO2 / ventilator settings / ABP vs NBP),
  ``inputevents`` (drips) and ``procedureevents`` (ventilation, lines, dialysis) all read the
  same windows, so the story is consistent across tables.
* :attr:`IcuContext.inputs` - the ``inputevents`` rows plus the fluid-shaped rows that
  ``ingredientevents`` mirrors (same ``orderid``).

Planted signal kept consistent with EP-11: every planted **sepsis** ICU stay gets a
norepinephrine drip starting exactly when the hosp norepinephrine prescription starts (when that
start falls inside the stay; from arrival otherwise), every planted **AKI** ICU stay gets low
hourly urine output and CRRT, ventilated
stays get FiO2 / PEEP / tidal volume / ventilator mode / ``Endotracheal tube`` rows, an
``Invasive Ventilation`` procedure with ``Intubation`` / ``Extubation`` and, mostly, propofol.
Real MetaVision itemids from ``vocab/d_items.yaml`` (typed from public docs) are used because
the vendored concepts look them up by number; the fixture-only ``datetimeevents`` /
``ingredientevents`` items live in the 240xxx band. Nothing here reads data; every id is
>= 90 000 000 (``stay_id`` from the plan, ``caregiver_id`` / ``orderid`` from
``spec.first_event_id``); every event lies inside its stay's ``[intime, outtime]``.

Row budget (brief item 3): chartevents <= 3 MB, whole fixture <= 10 MB - vitals hourly for the
first 48 h then 4-hourly, temperature 6-hourly, GCS / O2 device 4-hourly, ventilator settings
4-hourly inside the ventilation window, bedside glucose 6-hourly in half the stays, weight /
height once (+ a daily weight). Trim the cadences here before trimming stays.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from itertools import pairwise
from typing import TYPE_CHECKING, Any

import polars as pl

from mimicwarehouse.fixtures.hosp import HospContext, to_frame
from mimicwarehouse.fixtures.spec import (
    DAY,
    HOUR,
    MINUTE,
    AdmissionPlan,
    FixturePlan,
    IcuSegment,
    minute,
    pick,
    pick_weighted,
    table_rng,
)

if TYPE_CHECKING:  # pragma: no cover
    import numpy as np

    from mimicwarehouse.fixtures.vocab import IcuItem, Vocab
    from mimicwarehouse.schema.contract import Contract, Table

ICU_SCHEMA = "mimiciv_icu"
#: ``check.validate`` accepts icu events inside ``[intime - slack, outtime + slack]``.
EVENT_WINDOW_SLACK = 6 * HOUR
#: Hours of hourly vitals before the cadence drops to every :data:`LATE_STEP_HOURS`.
DENSE_HOURS = 48
LATE_STEP_HOURS = 4
#: Drip bags: drug content per bag (mg, or units for vasopressin) and bag volume (mL).
BAG_CONTENT: dict[str, tuple[float, float]] = {
    "norepinephrine": (8.0, 250.0),
    "epinephrine": (4.0, 250.0),
    "phenylephrine": (40.0, 250.0),
    "vasopressin": (40.0, 100.0),
    "dopamine": (400.0, 250.0),
}
#: Carrier ("Mixed solution") fluid per drip: D5W for the catecholamines mixed in dextrose.
CARRIER_FLUID: dict[str, str] = {
    "norepinephrine": "d5w",
    "dopamine": "d5w",
    "epinephrine": "nacl",
    "phenylephrine": "nacl",
    "vasopressin": "nacl",
}
#: Ingredient content per mL of each fluid (ingredient -> amount per mL, unit).
FLUID_INGREDIENTS: dict[str, tuple[tuple[str, float, str], ...]] = {
    "nacl": (("water", 1.0, "mL"), ("sodium", 0.154, "mEq")),
    "lr": (("water", 1.0, "mL"), ("sodium", 0.13, "mEq")),
    "d5w": (("water", 1.0, "mL"), ("dextrose", 0.05, "grams")),
}
MAINTENANCE_RATES: tuple[float, ...] = (75.0, 100.0, 125.0)
BOLUS_AMOUNTS: tuple[float, ...] = (500.0, 1000.0)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def ceil_hour(dt: datetime) -> datetime:
    """The first top-of-hour instant >= ``dt``."""
    floor = dt.replace(minute=0, second=0, microsecond=0)
    return floor if floor == dt else floor + HOUR


def _clip(v: float, lo: float, hi: float) -> float:
    return min(max(v, lo), hi)


def _minutes(rng: np.random.Generator, lo: float, hi: float) -> timedelta:
    """A whole number of minutes uniform in ``[lo, hi]`` (minutes)."""
    return MINUTE * int(rng.integers(int(lo), int(hi) + 1))


def _hours(rng: np.random.Generator, lo: float, hi: float) -> timedelta:
    return _minutes(rng, lo * 60, hi * 60)


@dataclass(frozen=True, slots=True)
class Window:
    """A half-open ``[start, end)`` interval of a stay."""

    start: datetime
    end: datetime

    def __contains__(self, t: object) -> bool:
        return isinstance(t, datetime) and self.start <= t < self.end

    @property
    def minutes(self) -> int:
        return int((self.end - self.start) / MINUTE)

    @property
    def hours(self) -> float:
        return (self.end - self.start) / HOUR


def _sub_window(
    rng: np.random.Generator,
    seg: IcuSegment,
    *,
    start_offset: tuple[float, float],
    duration: tuple[float, float],
    end_slack_hours: float = 0.0,
    to_outtime: bool = False,
) -> Window | None:
    """A window inside the stay: start = intime + U(start_offset) hours (capped at a quarter of
    the stay), duration U(duration) hours (clipped to outtime - end_slack); None if too short."""
    los_h = (seg.outtime - seg.intime) / HOUR
    off_hi = min(start_offset[1], los_h / 4)
    off_lo = min(start_offset[0], off_hi)
    start = minute(seg.intime + _hours(rng, off_lo, off_hi))
    latest = seg.outtime - HOUR * end_slack_hours
    if to_outtime:
        end = seg.outtime
    else:
        end = minute(min(latest, start + _hours(rng, duration[0], duration[1])))
    if end - start < 30 * MINUTE:
        return None
    return Window(start, end)


# ---------------------------------------------------------------------------
# Stay profiles (cross-table stage 1)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Drip:
    item: IcuItem
    window: Window


@dataclass(frozen=True, slots=True)
class StayProfile:
    """Everything the icu tables share about one ICU stay."""

    seg: IcuSegment
    admission: AdmissionPlan
    weight_kg: float
    height_cm: float
    severity: float
    died_in_icu: bool
    temp_itemid: int
    aline: Window | None
    vent: Window | None
    niv: Window | None
    crrt: Window | None
    vaso: Drip | None
    vaso_addon: Drip | None
    propofol: Window | None
    insulin: Window | None
    foley: bool
    day_caregiver: int
    night_caregiver: int
    o2_device: str

    @property
    def stay_id(self) -> int:
        return self.seg.stay_id

    @property
    def subject_id(self) -> int:
        return self.seg.subject_id

    @property
    def hadm_id(self) -> int:
        return self.seg.hadm_id

    @property
    def intime(self) -> datetime:
        return self.seg.intime

    @property
    def outtime(self) -> datetime:
        return self.seg.outtime

    @property
    def los_hours(self) -> float:
        return (self.seg.outtime - self.seg.intime) / HOUR

    def caregiver(self, t: datetime) -> int:
        """Day / night nurse by 12-hour block since intime."""
        block = int((t - self.seg.intime) / (12 * HOUR))
        return self.day_caregiver if block % 2 == 0 else self.night_caregiver

    def has(self, trait: str) -> bool:
        return self.admission.has(trait)


@dataclass(frozen=True, slots=True)
class InputStage:
    rows: tuple[dict[str, Any], ...]
    fluid_rows: tuple[tuple[dict[str, Any], str], ...]  # (inputevents row, fluid kind)


@dataclass
class IcuContext:
    """Plan + vocab + contract + cached cross-table stages, handed to every icu generator."""

    plan: FixturePlan
    vocab: Vocab
    contract: Contract
    _cache: dict[str, Any] = field(default_factory=dict, repr=False)

    def rng(self, name: str) -> np.random.Generator:
        return table_rng(self.plan.spec, name)

    def table(self, name: str) -> Table:
        return self.contract.table(ICU_SCHEMA, name)

    def _cached(self, key: str, build: Callable[[], Any]) -> Any:
        if key not in self._cache:
            self._cache[key] = build()
        return self._cache[key]

    @property
    def caregivers(self) -> tuple[int, ...]:
        spec = self.plan.spec
        return tuple(range(spec.first_event_id, spec.first_event_id + spec.n_caregivers))

    @property
    def norepinephrine_starts(self) -> dict[int, datetime]:
        """``hadm_id -> earliest hosp norepinephrine prescription start`` (the hosp order stage,
        rebuilt from its own child generator, so the icu drip starts exactly when the hosp
        prescription does in the planted sepsis stays)."""

        def build() -> dict[int, datetime]:
            hosp = HospContext(self.plan, self.vocab, self.contract)
            starts: dict[int, datetime] = {}
            for order in hosp.orders.meds:
                if order.drug.drug != "Norepinephrine":
                    continue
                current = starts.get(order.hadm_id)
                if current is None or order.starttime < current:
                    starts[order.hadm_id] = order.starttime
            return starts

        return self._cached("norepinephrine_starts", build)

    @property
    def profiles(self) -> tuple[StayProfile, ...]:
        return self._cached("profiles", lambda: _build_profiles(self))

    @property
    def inputs(self) -> InputStage:
        return self._cached("inputs", lambda: _build_inputs(self))


def _build_profiles(ctx: IcuContext) -> tuple[StayProfile, ...]:
    rng = ctx.rng("icu_stays")
    spec = ctx.plan.spec
    vocab = ctx.vocab
    by_hadm = {a.hadm_id: a for a in ctx.plan.admissions}
    caregivers = ctx.caregivers
    vaso_items = vocab.icu_roles("vaso")
    vaso_weights = [float(i.extra.get("weight", 1.0)) for i in vaso_items]
    norepi = next(i for i in vaso_items if i.extra.get("drug") == "norepinephrine")
    vasopressin = next(i for i in vaso_items if i.extra.get("drug") == "vasopressin")
    temp_f = vocab.icu_role("temp_f").itemid
    temp_c = vocab.icu_role("temp_c").itemid
    devices = [v for v, _ in vocab.icu_role("o2_device").text_values]
    out: list[StayProfile] = []
    for seg in ctx.plan.icu_segments:
        adm = by_hadm[seg.hadm_id]
        los_h = (seg.outtime - seg.intime) / HOUR
        sepsis, aki, t2dm = adm.has("sepsis"), adm.has("aki"), adm.has("t2dm")
        died_in_icu = adm.died and adm.segments[-1].is_icu
        severity = _clip(
            float(rng.beta(2.0, 3.0)) + 0.35 * sepsis + 0.35 * died_in_icu + 0.15 * aki, 0.0, 1.0
        )
        weight = round(_clip(float(rng.normal(80.0, 18.0)), 45.0, 130.0), 1)
        height = float(round(_clip(float(rng.normal(170.0, 10.0)), 150.0, 195.0)))
        temp_itemid = temp_f if rng.random() < 0.8 else temp_c
        # ventilation
        p_vent = spec.vent_fraction
        if sepsis:
            p_vent = max(p_vent, 0.7)
        if died_in_icu:
            p_vent = max(p_vent, 0.8)
        vent = None
        if los_h >= 6 and rng.random() < p_vent:
            vent = _sub_window(
                rng,
                seg,
                start_offset=(0.0, 6.0),
                duration=(4.0, 120.0),
                end_slack_hours=0.5,
                to_outtime=died_in_icu,
            )
        niv = None
        if vent is None and los_h >= 4 and rng.random() < 0.12:
            niv = _sub_window(rng, seg, start_offset=(0.0, 4.0), duration=(2.0, 12.0))
        # arterial line
        aline = None
        if los_h >= 3 and rng.random() < (0.85 if vent else 0.45):
            aline = _sub_window(rng, seg, start_offset=(0.0, 3.0), duration=(24.0, 120.0))
        # CRRT in the planted AKI stays
        crrt = None
        if aki and los_h >= 12:
            crrt = _sub_window(
                rng,
                seg,
                start_offset=(los_h / 4, los_h / 2),
                duration=(24.0, 72.0),
                end_slack_hours=1,
            )
        # vasopressors
        vaso: Drip | None = None
        addon: Drip | None = None
        if sepsis and los_h >= 2:
            # the hosp prescription's start when it falls inside the stay, else from arrival
            rx_start = ctx.norepinephrine_starts.get(seg.hadm_id)
            start = seg.intime
            if rx_start is not None and seg.intime <= rx_start <= seg.outtime - HOUR:
                start = rx_start
            end = minute(min(seg.outtime, start + _hours(rng, 6.0, 48.0)))
            vaso = Drip(norepi, Window(minute(start), end))
            if rng.random() < 0.35 and vaso.window.hours >= 4:
                a_start = minute(vaso.window.start + _hours(rng, 0.5, 2.0))
                a_end = minute(min(vaso.window.end, a_start + _hours(rng, 2.0, 24.0)))
                if a_end - a_start >= 30 * MINUTE:
                    addon = Drip(vasopressin, Window(a_start, a_end))
        elif los_h >= 3 and rng.random() < spec.vasopressor_fraction:
            item = pick_weighted(rng, vaso_items, vaso_weights)
            w = _sub_window(rng, seg, start_offset=(0.0, 6.0), duration=(4.0, 36.0))
            if w is not None:
                vaso = Drip(item, w)
        # propofol under ventilation
        propofol = None
        if vent is not None and rng.random() < 0.85:
            p_start = minute(vent.start + _minutes(rng, 0, 30))
            p_end = minute(min(vent.end, p_start + _hours(rng, 2.0, 72.0)))
            if p_end - p_start >= 30 * MINUTE:
                propofol = Window(p_start, p_end)
        insulin = None
        if los_h >= 6 and rng.random() < (0.8 if t2dm else 0.08):
            insulin = _sub_window(rng, seg, start_offset=(1.0, 12.0), duration=(6.0, 48.0))
        foley = rng.random() < (0.95 if (vent or aline) else 0.7)
        day = caregivers[int(rng.integers(0, len(caregivers)))]
        night = caregivers[int(rng.integers(0, len(caregivers)))]
        while night == day and len(caregivers) > 1:
            night = caregivers[int(rng.integers(0, len(caregivers)))]
        if severity > 0.75:
            device = "Non-rebreather"
        elif severity > 0.55:
            device = "High flow nasal cannula"
        elif rng.random() < 0.7:
            device = "Nasal cannula"
        else:
            device = "None"
        if device not in devices:  # pragma: no cover - vocab drift
            device = devices[-1]
        out.append(
            StayProfile(
                seg=seg,
                admission=adm,
                weight_kg=weight,
                height_cm=height,
                severity=severity,
                died_in_icu=died_in_icu,
                temp_itemid=temp_itemid,
                aline=aline,
                vent=vent,
                niv=niv,
                crrt=crrt,
                vaso=vaso,
                vaso_addon=addon,
                propofol=propofol,
                insulin=insulin,
                foley=foley,
                day_caregiver=day,
                night_caregiver=night,
                o2_device=device,
            )
        )
    return tuple(out)


# ---------------------------------------------------------------------------
# chartevents
# ---------------------------------------------------------------------------


class _AR1:
    """A smooth series around ``mean`` (first-order autoregression) for one vital sign."""

    __slots__ = ("mean", "phi", "rng", "sd", "state")

    def __init__(self, rng: np.random.Generator, mean: float, sd: float, phi: float = 0.7) -> None:
        self.rng = rng
        self.mean = mean
        self.sd = sd
        self.phi = phi
        self.state = mean + float(rng.normal(0.0, sd))

    def next(self) -> float:
        self.state = (
            self.mean + self.phi * (self.state - self.mean) + float(self.rng.normal(0.0, self.sd))
        )
        return self.state


def _slots(p: StayProfile) -> list[datetime]:
    """Charting instants: top of the hour, hourly for the first 48 h, then every 4 h."""
    out: list[datetime] = []
    t = ceil_hour(p.intime)
    dense_until = p.intime + HOUR * DENSE_HOURS
    while t < p.outtime:
        out.append(t)
        t += HOUR if t < dense_until else HOUR * LATE_STEP_HOURS
    return out


def _chart_row(
    p: StayProfile,
    rng: np.random.Generator,
    item: IcuItem,
    t: datetime,
    *,
    text: str | None,
    num: float | None,
    outside_normal: bool = False,
) -> dict[str, Any]:
    warning = 1 if outside_normal and rng.random() < 0.15 else 0
    return {
        "subject_id": p.subject_id,
        "hadm_id": p.hadm_id,
        "stay_id": p.stay_id,
        "caregiver_id": p.caregiver(t),
        "charttime": t,
        "storetime": t + _minutes(rng, 1, 90),
        "itemid": item.itemid,
        "value": text,
        "valuenum": num,
        "valueuom": item.unitname,
        "warning": warning,
    }


def _numeric_row(
    p: StayProfile, rng: np.random.Generator, item: IcuItem, t: datetime, v: float
) -> dict[str, Any]:
    text, num = item.format(v)
    outside = (item.lownormalvalue is not None and num < item.lownormalvalue) or (
        item.highnormalvalue is not None and num > item.highnormalvalue
    )
    return _chart_row(p, rng, item, t, text=text, num=num, outside_normal=outside)


def _text_row(
    p: StayProfile,
    rng: np.random.Generator,
    item: IcuItem,
    t: datetime,
    text: str,
    num: float | None = None,
) -> dict[str, Any]:
    return _chart_row(p, rng, item, t, text=text, num=num)


def _gcs_text(item: IcuItem, score: float) -> str:
    for text, num in item.text_values:
        if num == score:
            return text
    raise KeyError(f"{item.label}: no text for score {score}")


def _fahrenheit(c: float) -> float:
    return c * 9.0 / 5.0 + 32.0


def _stay_chartevents(ctx: IcuContext, rng: np.random.Generator, p: StayProfile) -> list[dict]:
    v = ctx.vocab
    role = v.icu_role
    hr, rr, spo2 = role("hr"), role("rr"), role("spo2")
    nbp = (role("nbp_sys"), role("nbp_dia"), role("nbp_mean"))
    abp = (role("abp_sys"), role("abp_dia"), role("abp_mean"))
    temp_item = v.icu_item(p.temp_itemid)
    gcs_eye, gcs_verbal, gcs_motor = role("gcs_eye"), role("gcs_verbal"), role("gcs_motor")
    fio2, peep, tv = role("fio2"), role("peep"), role("tidal_volume")
    vent_mode, o2_device = role("vent_mode"), role("o2_device")
    weight_admit, weight_daily, height = role("weight_admit"), role("weight_daily"), role("height")
    glucose = role("glucose")
    sev = p.severity
    rows: list[dict] = []
    # once-only rows shortly after arrival
    t_arrival = min(minute(p.intime + 5 * MINUTE), p.outtime - MINUTE)
    rows.append(_numeric_row(p, rng, weight_admit, t_arrival, p.weight_kg))
    rows.append(_numeric_row(p, rng, height, t_arrival, p.height_cm))
    series = {
        "hr": _AR1(rng, 72.0 + 45.0 * sev, 6.0),
        "rr": _AR1(rng, 15.0 + 12.0 * sev, 2.5),
        "spo2": _AR1(rng, 97.5 - 6.0 * sev, 1.2),
        "sbp": _AR1(rng, 128.0 - 40.0 * sev, 9.0),
        "temp": _AR1(rng, 36.8 + 1.7 * sev, 0.3, phi=0.8),
        "glu": _AR1(rng, 130.0 + 40.0 * sev + (80.0 if p.has("t2dm") else 0.0), 18.0),
    }
    charts_glucose = p.insulin is not None or p.has("t2dm") or rng.random() < 0.5
    modes = [t for t, _ in vent_mode.text_values]
    primary_mode = modes[0] if rng.random() < 0.6 else modes[1]
    weight_state = p.weight_kg
    for t in _slots(p):
        elapsed_h = int((t - p.intime) / HOUR)
        vent_now = p.vent is not None and t in p.vent
        niv_now = p.niv is not None and t in p.niv
        # -- vitals: HR / RR / SpO2 / BP -----------------------------------------------------
        hr_v = _clip(series["hr"].next(), 35, 185)
        rr_v = _clip(series["rr"].next(), 6, 45)
        sp_v = _clip(series["spo2"].next(), 70, 100)
        sbp_v = _clip(series["sbp"].next(), 60, 220)
        dbp_v = _clip(sbp_v * float(rng.uniform(0.5, 0.62)) + float(rng.normal(0, 3)), 30, 130)
        map_v = (sbp_v + 2 * dbp_v) / 3
        if rng.random() < 1 / 400:  # rare artefact: a disconnected sensor charts 0
            which = int(rng.integers(0, 4))
            if which == 0:
                hr_v = 0.0
            elif which == 1:
                rr_v = 0.0
            elif which == 2:
                sp_v = 0.0
            else:
                sbp_v, dbp_v, map_v = 0.0, 0.0, 0.0
        rows.append(_numeric_row(p, rng, hr, t, hr_v))
        rows.append(_numeric_row(p, rng, rr, t, rr_v))
        rows.append(_numeric_row(p, rng, spo2, t, sp_v))
        bp_items = abp if (p.aline is not None and t in p.aline) else nbp
        for item, val in zip(bp_items, (sbp_v, dbp_v, map_v), strict=True):
            rows.append(_numeric_row(p, rng, item, t, val))
        # -- temperature (6-hourly) ---------------------------------------------------------
        if elapsed_h % 6 == 0:
            c = _clip(series["temp"].next(), 34.5, 41.0)
            val = _fahrenheit(c) if temp_item.role == "temp_f" else c
            rows.append(_numeric_row(p, rng, temp_item, t, val))
        # -- GCS (4-hourly) -----------------------------------------------------------------
        if elapsed_h % 4 == 0:
            if vent_now:
                sedated = p.propofol is not None and t in p.propofol
                eye = 1 if sedated else int(rng.integers(1, 4))
                motor = int(rng.integers(1, 4)) if sedated else int(rng.integers(3, 6))
                verbal = 0  # No Response-ETT
            else:
                eye = 4 if (sev < 0.7 or rng.random() < 0.6) else 3
                verbal = 5 if (sev < 0.5 or rng.random() < 0.6) else 4
                motor = 6 if rng.random() < 0.92 else 5
            rows.append(_text_row(p, rng, gcs_eye, t, _gcs_text(gcs_eye, eye), float(eye)))
            rows.append(
                _text_row(p, rng, gcs_verbal, t, _gcs_text(gcs_verbal, verbal), float(verbal))
            )
            rows.append(_text_row(p, rng, gcs_motor, t, _gcs_text(gcs_motor, motor), float(motor)))
        # -- oxygen device (4-hourly) + ventilator settings inside the vent window ----------
        if elapsed_h % 4 == 0:
            if vent_now:
                device = "Endotracheal tube"
            elif niv_now:
                device = "Bipap mask "
            elif p.vent is not None and t < p.vent.start:
                device = "Non-rebreather"
            elif p.vent is not None and t >= p.vent.end:
                device = "Nasal cannula"
            else:
                device = p.o2_device
            rows.append(_text_row(p, rng, o2_device, t, device))
            if vent_now and p.vent is not None:
                since = (t - p.vent.start) / HOUR
                f = 40.0 + 55.0 * sev * math.exp(-since / 12.0) + float(rng.normal(0, 4))
                f = 5 * round(_clip(f, 21, 100) / 5)
                rows.append(_numeric_row(p, rng, fio2, t, f))
                rows.append(
                    _numeric_row(
                        p, rng, peep, t, _clip(5 + round(sev * 6) + int(rng.integers(-1, 2)), 5, 16)
                    )
                )
                ibw = 50.0 + 0.91 * (p.height_cm - 152.4)
                rows.append(
                    _numeric_row(p, rng, tv, t, 10 * round(_clip(ibw * 7.0, 350, 600) / 10))
                )
                remaining = (p.vent.end - t) / HOUR
                if remaining <= 4 and not p.died_in_icu:
                    mode = "PSV/SBT"
                elif since > 0.6 * p.vent.hours:
                    mode = "CPAP/PSV"
                else:
                    mode = primary_mode
                rows.append(_text_row(p, rng, vent_mode, t, mode))
        # -- bedside glucose (6-hourly) ------------------------------------------------------
        if charts_glucose and elapsed_h % 6 == 0:
            g = series["glu"].next()
            if p.insulin is not None and t > p.insulin.start:
                g -= 40.0
            rows.append(_numeric_row(p, rng, glucose, t, _clip(g, 55, 420)))
        # -- daily weight ------------------------------------------------------------------
        if elapsed_h > 0 and elapsed_h % 24 == 0:
            weight_state = _clip(weight_state + float(rng.normal(0.0, 0.6)), 40.0, 135.0)
            rows.append(_numeric_row(p, rng, weight_daily, t, weight_state))
    return rows


def chartevents(ctx: IcuContext) -> pl.DataFrame:
    rng = ctx.rng("chartevents")
    rows: list[dict[str, Any]] = []
    for p in ctx.profiles:
        rows.extend(_stay_chartevents(ctx, rng, p))
    return to_frame(ctx.table("chartevents"), rows)


# ---------------------------------------------------------------------------
# datetimeevents
# ---------------------------------------------------------------------------


def datetimeevents(ctx: IcuContext) -> pl.DataFrame:
    rng = ctx.rng("datetimeevents")
    v = ctx.vocab
    dt_foley, dt_aline, dt_dialysis = (
        v.icu_role("dt_foley"),
        v.icu_role("dt_aline"),
        v.icu_role("dt_dialysis"),
    )
    rows: list[dict[str, Any]] = []

    def row(p: StayProfile, item: IcuItem, charttime: datetime, value: datetime) -> None:
        rows.append(
            {
                "subject_id": p.subject_id,
                "hadm_id": p.hadm_id,
                "stay_id": p.stay_id,
                "caregiver_id": p.caregiver(charttime),
                "charttime": charttime,
                "storetime": charttime + _minutes(rng, 1, 60),
                "itemid": item.itemid,
                "value": value,
                "valueuom": "Date and time",
                "warning": 0,
            }
        )

    for p in ctx.profiles:
        if p.foley:
            charttime = minute(min(p.intime + HOUR, p.outtime - MINUTE))
            inserted = minute(min(p.intime + _minutes(rng, 10, 50), charttime))
            row(p, dt_foley, charttime, inserted)
        if p.aline is not None:
            charttime = minute(min(p.aline.start + 30 * MINUTE, p.outtime - MINUTE))
            row(p, dt_aline, charttime, min(p.aline.start, charttime))
        if p.crrt is not None or rng.random() < 0.08:
            charttime = minute(min(p.intime + 2 * HOUR, p.outtime - MINUTE))
            last = minute(p.intime - DAY * int(rng.integers(1, 11)) - _minutes(rng, 0, 600))
            row(p, dt_dialysis, charttime, last)
    return to_frame(ctx.table("datetimeevents"), rows)


# ---------------------------------------------------------------------------
# inputevents + ingredientevents (cross-table stage 2)
# ---------------------------------------------------------------------------


def _split_window(rng: np.random.Generator, w: Window, n: int) -> list[Window]:
    """``n`` consecutive sub-windows of ``w`` (each >= 30 min; fewer when ``w`` is short)."""
    total = w.minutes
    n = max(1, min(n, total // 30))
    if n == 1:
        return [w]
    cuts = sorted(int(rng.integers(30, total - 30 * (n - 1) + 1)) for _ in range(n - 1))
    # enforce >= 30 min gaps between cuts
    fixed: list[int] = []
    prev = 0
    for c in cuts:
        c = max(c, prev + 30)
        fixed.append(c)
        prev = c
    if fixed and fixed[-1] > total - 30:
        return [w]
    bounds = [0, *fixed, total]
    return [Window(w.start + MINUTE * a, w.start + MINUTE * b) for a, b in pairwise(bounds)]


def _base_input_row(
    p: StayProfile,
    rng: np.random.Generator,
    item: IcuItem,
    w: Window,
    *,
    orderid: int,
    linkorderid: int,
    amount: float,
    amountuom: str,
    rate: float | None,
    rateuom: str | None,
    ordercategoryname: str,
    ordercomponenttypedescription: str,
    ordercategorydescription: str,
    totalamount: float,
    totalamountuom: str,
    statusdescription: str,
    originalamount: float,
    originalrate: float | None,
    isopenbag: int = 0,
) -> dict[str, Any]:
    return {
        "subject_id": p.subject_id,
        "hadm_id": p.hadm_id,
        "stay_id": p.stay_id,
        "caregiver_id": p.caregiver(w.start),
        "starttime": w.start,
        "endtime": w.end,
        "storetime": w.end + _minutes(rng, 0, 30),
        "itemid": item.itemid,
        "amount": amount,
        "amountuom": amountuom,
        "rate": rate,
        "rateuom": rateuom,
        "orderid": orderid,
        "linkorderid": linkorderid,
        "ordercategoryname": ordercategoryname,
        "secondaryordercategoryname": None,
        "ordercomponenttypedescription": ordercomponenttypedescription,
        "ordercategorydescription": ordercategorydescription,
        "patientweight": p.weight_kg,
        "totalamount": totalamount,
        "totalamountuom": totalamountuom,
        "isopenbag": isopenbag,
        "continueinnextdept": 0,
        "statusdescription": statusdescription,
        "originalamount": originalamount,
        "originalrate": originalrate,
    }


def _build_inputs(ctx: IcuContext) -> InputStage:
    rng = ctx.rng("inputevents")
    v = ctx.vocab
    fluid_items = {i.extra["fluid"]: i for i in v.icu_roles("fluid")}
    fluid_weights = {k: float(i.extra.get("weight", 1.0)) for k, i in fluid_items.items()}
    propofol = v.icu_role("sedative")
    insulin = v.icu_role("insulin")
    status = v.icu_weighted("statusdescriptions")
    next_order = ctx.plan.spec.first_event_id
    rows: list[dict[str, Any]] = []
    fluid_rows: list[tuple[dict[str, Any], str]] = []

    def new_order() -> int:
        nonlocal next_order
        oid = next_order
        next_order += 1
        return oid

    def final_status() -> str:
        return str(pick(rng, status))

    def drip(
        p: StayProfile, item: IcuItem, w: Window, *, carrier: bool, bag: tuple[float, float]
    ) -> None:
        """A titrated drip: 1-3 rate segments (new orderid each, linkorderid = the first) plus,
        for the vasoactives, a 'Mixed solution' carrier row with the same orderid."""
        rateuom = str(item.extra["rateuom"])
        lo, hi = float(item.extra["rate_low"]), float(item.extra["rate_high"])
        n_seg = 1 + int(rng.random() < 0.6) + int(rng.random() < 0.3)
        segments = _split_window(rng, w, n_seg)
        rate = float(rng.uniform(lo, lo + 0.5 * (hi - lo)))
        first_rate = None
        link = None
        bag_amount, bag_ml = bag
        for i, s in enumerate(segments):
            last = i == len(segments) - 1
            oid = new_order()
            link = oid if link is None else link
            if i > 0:
                rate = _clip(rate * float(rng.uniform(0.6, 1.5)), lo, hi)
            decimals = 3 if rateuom.startswith("mcg") else 2
            rate = round(rate, decimals)
            first_rate = rate if first_rate is None else first_rate
            if rateuom == "mcg/kg/min":
                amount = round(rate * p.weight_kg * s.minutes / 1000.0, 4)  # mg
                amountuom = "mg"
                ml_per_hour = rate * p.weight_kg * 60.0 / 1000.0 / bag_amount * bag_ml
            else:  # units/hour
                amount = round(rate * s.hours, 3)
                amountuom = "units"
                ml_per_hour = rate / bag_amount * bag_ml
            st = "Changed" if not last else final_status()
            rows.append(
                _base_input_row(
                    p,
                    rng,
                    item,
                    s,
                    orderid=oid,
                    linkorderid=link,
                    amount=amount,
                    amountuom=amountuom,
                    rate=rate,
                    rateuom=rateuom,
                    ordercategoryname="01-Drips",
                    ordercomponenttypedescription="Main order parameter",
                    ordercategorydescription="Continuous Med",
                    totalamount=bag_ml,
                    totalamountuom="mL",
                    statusdescription=st,
                    originalamount=bag_amount,
                    originalrate=first_rate,
                    isopenbag=int(rng.random() < 0.1),
                )
            )
            if carrier:
                kind = CARRIER_FLUID[str(item.extra["drug"])]
                c_item = fluid_items[kind]
                ml_rate = round(ml_per_hour, 3)
                c_row = _base_input_row(
                    p,
                    rng,
                    c_item,
                    s,
                    orderid=oid,
                    linkorderid=link,
                    amount=round(ml_rate * s.hours, 3),
                    amountuom="mL",
                    rate=ml_rate,
                    rateuom="mL/hour",
                    ordercategoryname="01-Drips",
                    ordercomponenttypedescription="Mixed solution",
                    ordercategorydescription="Continuous Med",
                    totalamount=bag_ml,
                    totalamountuom="mL",
                    statusdescription=st,
                    originalamount=bag_ml,
                    originalrate=ml_rate,
                )
                rows.append(c_row)
                fluid_rows.append((c_row, kind))

    def fluid(p: StayProfile, kind: str, w: Window, *, rate: float, bolus: bool) -> None:
        item = fluid_items[kind]
        oid = new_order()
        amount = round(rate * w.hours, 3)
        total = amount if bolus else 1000.0
        row = _base_input_row(
            p,
            rng,
            item,
            w,
            orderid=oid,
            linkorderid=oid,
            amount=amount,
            amountuom="mL",
            rate=round(rate, 3),
            rateuom="mL/hour",
            ordercategoryname="03-IV Fluid Bolus" if bolus else "02-Fluids (Crystalloids)",
            ordercomponenttypedescription="Main order parameter",
            ordercategorydescription="Bolus" if bolus else "Continuous IV",
            totalamount=total,
            totalamountuom="mL",
            statusdescription="FinishedRunning" if bolus else final_status(),
            originalamount=total,
            originalrate=round(rate, 3),
        )
        rows.append(row)
        fluid_rows.append((row, kind))

    for p in ctx.profiles:
        if p.vaso is not None:
            drug = str(p.vaso.item.extra["drug"])
            drip(p, p.vaso.item, p.vaso.window, carrier=True, bag=BAG_CONTENT[drug])
        if p.vaso_addon is not None:
            drug = str(p.vaso_addon.item.extra["drug"])
            drip(p, p.vaso_addon.item, p.vaso_addon.window, carrier=True, bag=BAG_CONTENT[drug])
        if p.propofol is not None:
            drip(p, propofol, p.propofol, carrier=False, bag=(1000.0, 100.0))
        if p.insulin is not None:
            drip(p, insulin, p.insulin, carrier=False, bag=(100.0, 100.0))
        # maintenance fluids: consecutive orders through the stay
        if p.los_hours >= 3 and rng.random() < 0.85:
            t = minute(p.intime + _minutes(rng, 15, 120))
            n_orders = int(rng.integers(1, 4))
            for _ in range(n_orders):
                if p.outtime - t < HOUR:
                    break
                dur = _hours(rng, 6.0, 24.0)
                end = minute(min(p.outtime, t + dur))
                if end - t < 30 * MINUTE:
                    break
                kind = pick_weighted(
                    rng, list(fluid_items), [fluid_weights[k] for k in fluid_items]
                )
                rate = MAINTENANCE_RATES[int(rng.integers(0, len(MAINTENANCE_RATES)))]
                fluid(p, str(kind), Window(t, end), rate=rate, bolus=False)
                t = minute(end + _minutes(rng, 0, 120))
        # boluses: two in the planted sepsis stays, one in a third of the others
        n_bolus = 2 if p.has("sepsis") else int(rng.random() < 0.3)
        t = minute(p.intime + _minutes(rng, 0, 60))
        for _ in range(n_bolus):
            amount = BOLUS_AMOUNTS[int(rng.integers(0, len(BOLUS_AMOUNTS)))]
            dur = HOUR if amount >= 1000 else 30 * MINUTE
            end = minute(min(p.outtime, t + dur))
            if end - t < 15 * MINUTE:
                break
            kind = "nacl" if rng.random() < 0.6 else "lr"
            fluid(p, kind, Window(t, end), rate=amount / (dur / HOUR), bolus=True)
            t = minute(end + _minutes(rng, 30, 180))
    return InputStage(tuple(rows), tuple(fluid_rows))


def inputevents(ctx: IcuContext) -> pl.DataFrame:
    return to_frame(ctx.table("inputevents"), ctx.inputs.rows)


def ingredientevents(ctx: IcuContext) -> pl.DataFrame:
    rng = ctx.rng("ingredientevents")
    v = ctx.vocab
    ingredients = {i.extra["ingredient"]: i for i in v.icu_roles("ingredient")}
    rows: list[dict[str, Any]] = []
    for src, kind in ctx.inputs.fluid_rows:
        hours = (src["endtime"] - src["starttime"]) / HOUR
        for name, per_ml, unit in FLUID_INGREDIENTS[kind]:
            item = ingredients[name]
            amount = round(float(src["amount"]) * per_ml, 4)
            rate = round(amount / hours, 4) if hours > 0 else None
            rows.append(
                {
                    "subject_id": src["subject_id"],
                    "hadm_id": src["hadm_id"],
                    "stay_id": src["stay_id"],
                    "caregiver_id": src["caregiver_id"],
                    "starttime": src["starttime"],
                    "endtime": src["endtime"],
                    "storetime": src["endtime"] + _minutes(rng, 0, 30),
                    "itemid": item.itemid,
                    "amount": amount,
                    "amountuom": unit,
                    "rate": rate,
                    "rateuom": f"{unit}/hour" if rate is not None else None,
                    "orderid": src["orderid"],
                    "linkorderid": src["linkorderid"],
                    "statusdescription": src["statusdescription"],
                    "originalamount": round(float(src["originalamount"]) * per_ml, 4),
                    "originalrate": None
                    if src["originalrate"] is None
                    else round(float(src["originalrate"]) * per_ml, 4),
                }
            )
    return to_frame(ctx.table("ingredientevents"), rows)


# ---------------------------------------------------------------------------
# outputevents
# ---------------------------------------------------------------------------


def outputevents(ctx: IcuContext) -> pl.DataFrame:
    rng = ctx.rng("outputevents")
    foley = ctx.vocab.icu_role("urine_foley")
    void = ctx.vocab.icu_role("urine_void")
    rows: list[dict[str, Any]] = []
    for p in ctx.profiles:
        aki = p.has("aki")
        if p.foley:
            item = foley
            base_per_hour = 25.0 if aki else (45.0 if p.severity > 0.6 else 75.0)
            t = minute(p.intime + _minutes(rng, 30, 90))
            while t < p.outtime:
                step_h = 1 if aki else int(rng.choice([1, 1, 2, 2, 3]))
                value = float(round(base_per_hour * step_h * float(rng.uniform(0.5, 1.5))))
                if aki and rng.random() < 0.15:
                    value = 0.0  # anuric hours
                rows.append(
                    {
                        "subject_id": p.subject_id,
                        "hadm_id": p.hadm_id,
                        "stay_id": p.stay_id,
                        "caregiver_id": p.caregiver(t),
                        "charttime": t,
                        "storetime": t + _minutes(rng, 1, 60),
                        "itemid": item.itemid,
                        "value": value,
                        "valueuom": item.unitname,
                    }
                )
                t += HOUR * step_h
        else:
            t = minute(p.intime + _hours(rng, 2.0, 4.0))
            while t < p.outtime:
                value = float(round(float(rng.uniform(100.0, 450.0))))
                rows.append(
                    {
                        "subject_id": p.subject_id,
                        "hadm_id": p.hadm_id,
                        "stay_id": p.stay_id,
                        "caregiver_id": p.caregiver(t),
                        "charttime": t,
                        "storetime": t + _minutes(rng, 1, 60),
                        "itemid": void.itemid,
                        "value": value,
                        "valueuom": void.unitname,
                    }
                )
                t += _hours(rng, 3.0, 8.0)
    return to_frame(ctx.table("outputevents"), rows)


# ---------------------------------------------------------------------------
# procedureevents
# ---------------------------------------------------------------------------


def procedureevents(ctx: IcuContext) -> pl.DataFrame:
    rng = ctx.rng("procedureevents")
    v = ctx.vocab
    role = v.icu_role
    p_vent, p_niv, p_aline, p_crrt = (
        role("proc_vent"),
        role("proc_niv"),
        role("proc_aline"),
        role("proc_crrt"),
    )
    p_intub, p_extub = role("proc_intubation"), role("proc_extubation")
    locations = v.icu_weighted("aline_locations")
    location_category = str(v.icu_lists["aline_location_category"])
    next_order = ctx.plan.spec.first_event_id
    rows: list[dict[str, Any]] = []

    def row(
        p: StayProfile,
        item: IcuItem,
        w: Window,
        *,
        value: float,
        ordercategoryname: str,
        ordercategorydescription: str,
        location: str | None = None,
        locationcategory: str | None = None,
        statusdescription: str = "FinishedRunning",
    ) -> None:
        nonlocal next_order
        oid = next_order
        next_order += 1
        rows.append(
            {
                "subject_id": p.subject_id,
                "hadm_id": p.hadm_id,
                "stay_id": p.stay_id,
                "caregiver_id": p.caregiver(w.start),
                "starttime": w.start,
                "endtime": w.end,
                "storetime": w.end + _minutes(rng, 0, 60),
                "itemid": item.itemid,
                "value": value,
                "valueuom": item.unitname,
                "location": location,
                "locationcategory": locationcategory,
                "orderid": oid,
                "linkorderid": oid,
                "ordercategoryname": ordercategoryname,
                "ordercategorydescription": ordercategorydescription,
                "patientweight": p.weight_kg,
                "isopenbag": 0,
                "continueinnextdept": 0,
                "statusdescription": statusdescription,
                "originalamount": None,
                "originalrate": None,
            }
        )

    def task(p: StayProfile, item: IcuItem, t: datetime) -> None:
        end = min(t + MINUTE, p.outtime)
        row(
            p,
            item,
            Window(t, end),
            value=1.0,
            ordercategoryname="Intubation/Extubation",
            ordercategorydescription="Task",
        )

    for p in ctx.profiles:
        if p.vent is not None:
            task(p, p_intub, p.vent.start)
            row(
                p,
                p_vent,
                p.vent,
                value=float(p.vent.minutes),
                ordercategoryname="Ventilation",
                ordercategorydescription="ProcessDuration",
                statusdescription="Stopped" if p.died_in_icu else "FinishedRunning",
            )
            if not p.died_in_icu:
                task(p, p_extub, p.vent.end)
        if p.niv is not None:
            row(
                p,
                p_niv,
                p.niv,
                value=float(p.niv.minutes),
                ordercategoryname="Ventilation",
                ordercategorydescription="ProcessDuration",
            )
        if p.aline is not None:
            row(
                p,
                p_aline,
                p.aline,
                value=float(p.aline.minutes),
                ordercategoryname="Invasive Lines",
                ordercategorydescription="ProcessDuration",
                location=str(pick(rng, locations)),
                locationcategory=location_category,
            )
        if p.crrt is not None:
            row(
                p,
                p_crrt,
                p.crrt,
                value=float(p.crrt.minutes),
                ordercategoryname="Dialysis",
                ordercategorydescription="ProcessDuration",
            )
    return to_frame(ctx.table("procedureevents"), rows)


# ---------------------------------------------------------------------------
# icustays, caregiver, d_items
# ---------------------------------------------------------------------------


def icustays(ctx: IcuContext) -> pl.DataFrame:
    rows = [
        {
            "subject_id": seg.subject_id,
            "hadm_id": seg.hadm_id,
            "stay_id": seg.stay_id,
            "first_careunit": seg.careunit,
            "last_careunit": seg.careunit,
            "intime": seg.intime,
            "outtime": seg.outtime,
            "los": (seg.outtime - seg.intime) / DAY,
        }
        for seg in ctx.plan.icu_segments
    ]
    return to_frame(ctx.table("icustays"), rows)


def caregiver(ctx: IcuContext) -> pl.DataFrame:
    return to_frame(ctx.table("caregiver"), [{"caregiver_id": c} for c in ctx.caregivers])


def d_items(ctx: IcuContext) -> pl.DataFrame:
    rows = [
        {
            "itemid": i.itemid,
            "label": i.label,
            "abbreviation": i.abbreviation,
            "linksto": i.linksto,
            "category": i.category,
            "unitname": i.unitname,
            "param_type": i.param_type,
            "lownormalvalue": i.lownormalvalue,
            "highnormalvalue": i.highnormalvalue,
        }
        for i in sorted(ctx.vocab.icu_items, key=lambda i: i.itemid)
    ]
    return to_frame(ctx.table("d_items"), rows)


#: Generator per icu table (contract order; keys are the 9 ``mimiciv_icu`` table names).
GENERATORS: dict[str, Callable[[IcuContext], pl.DataFrame]] = {
    "caregiver": caregiver,
    "chartevents": chartevents,
    "datetimeevents": datetimeevents,
    "d_items": d_items,
    "icustays": icustays,
    "ingredientevents": ingredientevents,
    "inputevents": inputevents,
    "outputevents": outputevents,
    "procedureevents": procedureevents,
}


def build_icu_frames(
    plan: FixturePlan, *, vocab: Vocab | None = None, contract: Contract | None = None
) -> dict[str, pl.DataFrame]:
    """All 9 icu frames for ``plan`` (``{table: frame}`` in contract order)."""
    from mimicwarehouse.fixtures.vocab import load_vocab
    from mimicwarehouse.schema.contract import load_contract

    ctx = IcuContext(plan, vocab or load_vocab(), contract or load_contract())
    expected = [t.name for t in ctx.contract.by_schema(ICU_SCHEMA)]
    missing = [t for t in expected if t not in GENERATORS]
    if missing:
        raise RuntimeError(f"no generator for icu table(s) {missing}")
    return {name: GENERATORS[name](ctx) for name in expected}


__all__ = [
    "BAG_CONTENT",
    "CARRIER_FLUID",
    "DENSE_HOURS",
    "EVENT_WINDOW_SLACK",
    "FLUID_INGREDIENTS",
    "GENERATORS",
    "ICU_SCHEMA",
    "LATE_STEP_HOURS",
    "Drip",
    "IcuContext",
    "InputStage",
    "StayProfile",
    "Window",
    "build_icu_frames",
    "ceil_hour",
]
