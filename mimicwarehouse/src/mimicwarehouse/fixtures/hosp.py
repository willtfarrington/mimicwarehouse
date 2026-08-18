"""hosp table generators - one function per ``mimiciv_hosp`` table (EP-11).

Every generator takes the shared :class:`HospContext` (plan + vocab + contract + a few cached
cross-table stages) and returns a Polars frame whose **columns, order and dtypes come from the
EP-9 contract** (:func:`polars_schema`), sorted by the contract ``sort_keys``. Draws come from
per-table child generators (:func:`~mimicwarehouse.fixtures.spec.table_rng`), so tables are
independent of each other's randomness; the cross-table stages (``orders`` -> poe / pharmacy /
prescriptions / emar / emar_detail, ``labs``, ``micro``, ``trait_times``) are built once per
context from their own child generator and read by every table that needs them, so foreign keys
agree by construction.

Realism target (brief): "plausible enough for the loader, concepts and phenotypes" - real
itemids / ICD codes / drug names from the seed vocabularies, MIMIC-shaped free text (quoted
commas, double quotes, embedded newlines in ``labevents.comments``), ``hadm_id``-less outpatient
labs, MetaVision ICU careunits, ``poe_id`` = ``<subject_id>-<poe_seq>``, ``emar_id`` =
``<subject_id>-<emar_seq>``, planted signal (creatinine doubling within 48 h, blood culture +
IV antibiotic within 24 h, T2DM codes + insulin + glucose). Nothing here reads data; the only
inputs are the plan and the hand-typed vocab. Guard (G4): every id starts at 90 000 000, every
other numeric field is far from the real id bands, and no compact ``YYYYMMDD`` is ever formatted.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

import polars as pl

from mimicwarehouse.fixtures.spec import (
    DAY,
    HOUR,
    MINUTE,
    AdmissionPlan,
    FixturePlan,
    minute,
    pick,
    pick_weighted,
    table_rng,
    uniform_dt,
)

if TYPE_CHECKING:  # pragma: no cover
    import numpy as np

    from mimicwarehouse.fixtures.vocab import Drug, LabItem, Vocab
    from mimicwarehouse.schema.contract import Contract, Table

HOSP_SCHEMA = "mimiciv_hosp"

#: DuckDB contract type -> Polars dtype (the closed set of ``contract.DUCKDB_TYPE_RE``).
POLARS_TYPES: dict[str, pl.DataType] = {
    "INTEGER": pl.Int32(),
    "SMALLINT": pl.Int16(),
    "BIGINT": pl.Int64(),
    "VARCHAR": pl.Utf8(),
    "TIMESTAMP": pl.Datetime("us"),
    "DATE": pl.Date(),
    "DOUBLE": pl.Float64(),
    "FLOAT": pl.Float32(),
    "BOOLEAN": pl.Boolean(),
}

#: Dosing frequency -> hours between administrations (None = not periodic).
FREQUENCY_HOURS: dict[str, int | None] = {
    "Q4H": 4,
    "Q6H": 6,
    "Q8H": 8,
    "Q12H": 12,
    "Q24H": 24,
    "DAILY": 24,
    "DAILY16": 24,
    "QHS": 24,
    "BID": 12,
    "TID": 8,
    "ONCE": None,
    "CONTINUOUS": None,
}
#: Dispensing schedules per periodic frequency (pharmacy.disp_sched).
DISP_SCHED: dict[str, str] = {
    "Q4H": "00,04,08,12,16,20",
    "Q6H": "00,06,12,18",
    "Q8H": "06,14,22",
    "Q12H": "08,20",
    "Q24H": "08",
    "DAILY": "08",
    "DAILY16": "16",
    "QHS": "22",
    "BID": "08,20",
    "TID": "08,14,20",
}
#: Max administration events written per order (row budget: hosp fixture <= 6 MB).
MAX_ADMINS: dict[str, int] = {
    "once": 1,
    "bolus": 6,
    "sliding": 6,
    "flush": 4,
    "prn": 3,
    "infusion": 4,
    "fluid": 2,
}


def polars_schema(table: Table) -> dict[str, pl.DataType]:
    """``{column: polars dtype}`` in contract order."""
    return {c.name: POLARS_TYPES[c.duckdb_type] for c in table.columns}


def to_frame(table: Table, rows: Iterable[dict[str, Any]]) -> pl.DataFrame:
    """Rows (dicts keyed by contract column) -> typed frame sorted by the contract sort keys."""
    schema = polars_schema(table)
    rows = list(rows)
    frame = pl.from_dicts(rows, schema=schema, strict=True) if rows else pl.DataFrame(schema=schema)
    frame = frame.select(list(schema))
    if table.sort_keys:
        frame = frame.sort(list(table.sort_keys), maintain_order=True)
    return frame


# ---------------------------------------------------------------------------
# Cross-table stages
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MedOrder:
    subject_id: int
    hadm_id: int
    poe_seq: int
    poe_id: str
    pharmacy_id: int
    drug: Drug
    provider: str
    ordertime: datetime
    starttime: datetime
    stoptime: datetime
    dc_poe_id: str | None  # the D/C order that discontinued this one, if any


@dataclass(frozen=True, slots=True)
class PoeRow:
    subject_id: int
    hadm_id: int
    poe_seq: int
    poe_id: str
    ordertime: datetime
    order_type: str
    order_subtype: str | None
    transaction_type: str
    discontinue_of_poe_id: str | None
    discontinued_by_poe_id: str | None
    provider: str
    order_status: str
    details: tuple[tuple[str, str | None], ...] = ()


@dataclass(frozen=True, slots=True)
class Admin:
    subject_id: int
    hadm_id: int
    emar_seq: int
    emar_id: str
    order: MedOrder
    charttime: datetime
    event_txt: str
    provider: str


@dataclass(frozen=True, slots=True)
class Orders:
    meds: tuple[MedOrder, ...]
    poe: tuple[PoeRow, ...]
    admins: tuple[Admin, ...]


@dataclass(frozen=True, slots=True)
class TraitTimes:
    """Anchors of the planted signal per admission (``hadm_id`` -> times)."""

    culture: dict[int, datetime]  # sepsis: blood culture drawn
    antibiotic: dict[int, datetime]  # sepsis: IV antibiotic ordered (< 24 h after culture)
    aki: dict[int, tuple[datetime, datetime]]  # baseline draw, doubled draw (< 48 h apart)


@dataclass
class HospContext:
    """Plan + vocab + contract + cached cross-table stages, handed to every generator."""

    plan: FixturePlan
    vocab: Vocab
    contract: Contract
    _cache: dict[str, Any] = field(default_factory=dict, repr=False)

    def rng(self, name: str) -> np.random.Generator:
        return table_rng(self.plan.spec, name)

    def table(self, name: str) -> Table:
        return self.contract.table(HOSP_SCHEMA, name)

    def provider(self, rng: np.random.Generator) -> str:
        return self.plan.providers[int(rng.integers(0, len(self.plan.providers)))]

    def provider_or_null(self, rng: np.random.Generator, null_prob: float) -> str | None:
        return None if rng.random() < null_prob else self.provider(rng)

    def _cached(self, key: str, build: Callable[[], Any]) -> Any:
        if key not in self._cache:
            self._cache[key] = build()
        return self._cache[key]

    @property
    def trait_times(self) -> TraitTimes:
        return self._cached("trait_times", lambda: _build_trait_times(self))

    @property
    def orders(self) -> Orders:
        return self._cached("orders", lambda: _build_orders(self))

    @property
    def labs(self) -> list[dict[str, Any]]:
        return self._cached("labs", lambda: _build_labs(self))

    @property
    def micro(self) -> list[dict[str, Any]]:
        return self._cached("micro", lambda: _build_micro(self))


def _build_trait_times(ctx: HospContext) -> TraitTimes:
    rng = ctx.rng("trait_times")
    culture: dict[int, datetime] = {}
    antibiotic: dict[int, datetime] = {}
    aki: dict[int, tuple[datetime, datetime]] = {}
    for a in ctx.plan.admissions:
        if a.has("sepsis"):
            t_c = a.admittime + MINUTE * int(rng.integers(30, 6 * 60))
            culture[a.hadm_id] = t_c
            antibiotic[a.hadm_id] = t_c + MINUTE * int(rng.integers(30, 12 * 60))
        if a.has("aki"):
            t0 = a.admittime + MINUTE * int(rng.integers(60, 4 * 60))
            aki[a.hadm_id] = (t0, t0 + MINUTE * int(rng.integers(24 * 60, 44 * 60)))
    return TraitTimes(culture, antibiotic, aki)


# -- orders: poe / pharmacy / prescriptions / emar / emar_detail ------------------------------


@dataclass(slots=True)
class _RawOrder:
    """A medication order before poe_seq assignment."""

    admission: AdmissionPlan
    drug: Drug
    ordertime: datetime
    starttime: datetime
    stoptime: datetime
    provider: str
    dc_time: datetime | None


@dataclass(slots=True)
class _RawNonMed:
    admission: AdmissionPlan
    ordertime: datetime
    order_type: str
    order_subtype: str | None
    provider: str
    details: tuple[tuple[str, str | None], ...]


def _order_window(
    rng: np.random.Generator, drug: Drug, admission: AdmissionPlan, ordertime: datetime
):
    """(starttime, stoptime, dc_time) for one order."""
    kind = drug.kind
    start = minute(ordertime + MINUTE * int(rng.integers(5, 90)))
    if kind == "once":
        stop = start + HOUR
    elif kind in ("infusion", "fluid"):
        stop = start + MINUTE * int(rng.integers(12 * 60, 72 * 60))
    else:
        stop = start + DAY * int(rng.integers(2, 8))
    stop = min(stop, admission.dischtime)
    if stop <= start:
        stop = min(start + HOUR, admission.dischtime)
    dc = None
    if kind not in ("once",) and stop < admission.dischtime - HOUR and rng.random() < 0.2:
        dc = stop
    return start, stop, dc


def _raw_med_orders(
    ctx: HospContext, rng: np.random.Generator, a: AdmissionPlan
) -> list[_RawOrder]:
    vocab = ctx.vocab
    drugs = list(vocab.drugs)
    weights = [d.weight for d in drugs]
    chosen: list[Drug] = []
    n = int(rng.integers(3, 8))
    while len(chosen) < n:
        d = pick_weighted(rng, drugs, weights)
        if d not in chosen:
            chosen.append(d)
    forced: list[tuple[Drug, datetime]] = []
    if a.has("sepsis"):
        t_ab = ctx.trait_times.antibiotic[a.hadm_id]
        abx = [d for d in vocab.drugs_tagged("sepsis") if d.route == "IV"]
        for d in abx[:2]:
            forced.append((d, t_ab))
        vaso = [d for d in vocab.drugs_tagged("vasopressor") if d.drug == "Norepinephrine"]
        if a.icu is not None and vaso:
            forced.append((vaso[0], max(a.icu.intime, t_ab)))
    if a.has("t2dm"):
        for d in vocab.drugs_tagged("insulin"):
            forced.append((d, a.admittime + MINUTE * int(rng.integers(60, 12 * 60))))
    out: list[_RawOrder] = []
    seen = {d.drug for d, _ in forced}
    for d, t in forced:
        start, stop, dc = _order_window(rng, d, a, t)
        out.append(_RawOrder(a, d, minute(t), start, stop, ctx.provider(rng), dc))
    for d in chosen:
        if d.drug in seen:
            continue
        seen.add(d.drug)
        # orders land in the first 3 days and never within 2 h of discharge (start <= order+90 min)
        horizon = max(HOUR, min(a.los - 2 * HOUR, 3 * DAY))
        t = uniform_dt(rng, a.admittime, a.admittime + horizon)
        start, stop, dc = _order_window(rng, d, a, t)
        out.append(_RawOrder(a, d, t, start, stop, ctx.provider(rng), dc))
    return out


def _raw_nonmed_orders(
    ctx: HospContext, rng: np.random.Generator, a: AdmissionPlan
) -> list[_RawNonMed]:
    types = ctx.vocab.categories["poe_nonmed_types"]
    out: list[_RawNonMed] = []
    # every admission: an ADT admit order at admission and a discharge order near the end
    for entry in types:
        if entry["order_type"] == "ADT orders" and entry["order_subtype"] == "Admit":
            details = tuple(
                (str(k), None if v is None else str(v)) for k, v in entry.get("details") or ()
            )
            out.append(
                _RawNonMed(
                    a,
                    a.admittime + MINUTE * int(rng.integers(0, 60)),
                    "ADT orders",
                    "Admit",
                    ctx.provider(rng),
                    details,
                )
            )
        if entry["order_type"] == "ADT orders" and entry["order_subtype"] == "Discharge":
            details = tuple(
                (str(k), None if v is None else str(v)) for k, v in entry.get("details") or ()
            )
            t = max(a.admittime, a.dischtime - MINUTE * int(rng.integers(60, 12 * 60)))
            out.append(
                _RawNonMed(a, minute(t), "ADT orders", "Discharge", ctx.provider(rng), details)
            )
    others = [e for e in types if e["order_type"] != "ADT orders"]
    weights = [float(e.get("weight", 1)) for e in others]
    for _ in range(int(rng.integers(2, 6))):
        e = pick_weighted(rng, others, weights)
        details = tuple((str(k), None if v is None else str(v)) for k, v in e.get("details") or ())
        t = uniform_dt(rng, a.admittime, a.dischtime)
        out.append(
            _RawNonMed(
                a, t, str(e["order_type"]), e.get("order_subtype"), ctx.provider(rng), details
            )
        )
    return out


def _admin_times(
    rng: np.random.Generator, drug: Drug, start: datetime, stop: datetime
) -> list[tuple[datetime, str]]:
    """(charttime, event_txt) per administration event of one order."""
    kind = drug.kind
    cap = MAX_ADMINS.get(kind, 4)
    if kind == "once":
        return [(start, "Administered")]
    if kind in ("infusion", "fluid"):
        n_mid = int(rng.integers(0, cap - 1)) if kind == "infusion" else 0
        events = ["Started", *(["Rate Change"] * n_mid), "Stopped"]
        mids = sorted(uniform_dt(rng, start + MINUTE, stop) for _ in range(n_mid))
        return list(zip([start, *mids, stop], events, strict=True))
    if kind == "prn":
        n = int(rng.integers(1, cap + 1))
        times = sorted(uniform_dt(rng, start, stop) for _ in range(n))
        return [(t, "Administered") for t in times]
    hours = FREQUENCY_HOURS.get(drug.frequency) or 8
    slots = int((stop - start) / HOUR / hours) + 1
    n = max(1, min(cap, slots))
    out: list[tuple[datetime, str]] = []
    for i in range(n):
        t = start + HOUR * (hours * i) + MINUTE * int(rng.integers(0, 20))
        if t > stop:
            break
        if kind == "flush":
            txt = "Flushed"
        else:
            txt = "Not Given" if rng.random() < 0.08 else "Administered"
        out.append((t, txt))
    return out or [(start, "Administered")]


def _build_orders(ctx: HospContext) -> Orders:
    """poe rows (med + non-med + D/C), pharmacy/prescription orders and administrations."""
    rng = ctx.rng("orders")
    spec = ctx.plan.spec
    next_pharmacy = spec.first_event_id
    meds: list[MedOrder] = []
    poe_rows: list[PoeRow] = []
    admins: list[Admin] = []
    for s in ctx.plan.subjects:
        events: list[tuple[datetime, int, str, Any]] = []  # (time, tiebreak, kind, payload)
        tie = 0
        for a in s.admissions:
            for raw in _raw_med_orders(ctx, rng, a):
                events.append((raw.ordertime, tie, "med", raw))
                tie += 1
                if raw.dc_time is not None:
                    events.append((raw.dc_time, tie, "dc", raw))
                    tie += 1
            for raw_nm in _raw_nonmed_orders(ctx, rng, a):
                events.append((raw_nm.ordertime, tie, "nonmed", raw_nm))
                tie += 1
        events.sort(key=lambda e: (e[0], e[1]))
        seq_of: dict[int, int] = {}  # id(raw) -> poe_seq of the med order
        dc_of: dict[int, str] = {}  # id(raw) -> poe_id of its D/C row
        # first pass: assign seq numbers
        for seq, (_, _, kind, payload) in enumerate(events, start=1):
            if kind == "med":
                seq_of[id(payload)] = seq
            elif kind == "dc":
                dc_of[id(payload)] = f"{s.subject_id}-{seq}"
        for seq, (t, _, kind, payload) in enumerate(events, start=1):
            poe_id = f"{s.subject_id}-{seq}"
            if kind == "med":
                raw = payload
                order = MedOrder(
                    subject_id=s.subject_id,
                    hadm_id=raw.admission.hadm_id,
                    poe_seq=seq,
                    poe_id=poe_id,
                    pharmacy_id=next_pharmacy,
                    drug=raw.drug,
                    provider=raw.provider,
                    ordertime=raw.ordertime,
                    starttime=raw.starttime,
                    stoptime=raw.stoptime,
                    dc_poe_id=dc_of.get(id(raw)),
                )
                next_pharmacy += 1
                meds.append(order)
                poe_rows.append(
                    PoeRow(
                        s.subject_id,
                        order.hadm_id,
                        seq,
                        poe_id,
                        t,
                        "Medications",
                        raw.drug.proc_type,
                        "New",
                        None,
                        order.dc_poe_id,
                        raw.provider,
                        "Inactive"
                        if order.dc_poe_id or raw.stoptime < raw.admission.dischtime
                        else "Active",
                    )
                )
            elif kind == "dc":
                raw = payload
                orig = f"{s.subject_id}-{seq_of[id(raw)]}"
                poe_rows.append(
                    PoeRow(
                        s.subject_id,
                        raw.admission.hadm_id,
                        seq,
                        poe_id,
                        t,
                        "Medications",
                        None,
                        "D/C",
                        orig,
                        None,
                        ctx.provider(rng),
                        "Inactive",
                    )
                )
            else:
                raw_nm = payload
                poe_rows.append(
                    PoeRow(
                        s.subject_id,
                        raw_nm.admission.hadm_id,
                        seq,
                        poe_id,
                        t,
                        raw_nm.order_type,
                        raw_nm.order_subtype,
                        "New",
                        None,
                        None,
                        raw_nm.provider,
                        "Inactive",
                        raw_nm.details,
                    )
                )
        # administrations for this subject, emar_seq in charttime order
        subject_admins: list[tuple[datetime, int, MedOrder, str]] = []
        tie = 0
        for order in [m for m in meds if m.subject_id == s.subject_id]:
            for t, txt in _admin_times(rng, order.drug, order.starttime, order.stoptime):
                subject_admins.append((t, tie, order, txt))
                tie += 1
        subject_admins.sort(key=lambda e: (e[0], e[1]))
        for emar_seq, (t, _, order, txt) in enumerate(subject_admins, start=1):
            admins.append(
                Admin(
                    s.subject_id,
                    order.hadm_id,
                    emar_seq,
                    f"{s.subject_id}-{emar_seq}",
                    order,
                    t,
                    txt,
                    ctx.provider(rng),
                )
            )
    return Orders(tuple(meds), tuple(poe_rows), tuple(admins))


# -- labs -------------------------------------------------------------------------------------


def _format_value(item: LabItem, v: float) -> tuple[str, float]:
    text = f"{v:.{item.decimals}f}"
    return text, float(text)


def _lab_row(
    ctx: HospContext,
    rng: np.random.Generator,
    ids: dict[str, int],
    *,
    subject_id: int,
    hadm_id: int | None,
    specimen_id: int,
    item: LabItem,
    charttime: datetime,
    priority: str,
    provider: str | None,
    forced_value: float | None = None,
) -> dict[str, Any]:
    ids["labevent"] += 1
    value: str | None
    valuenum: float | None
    if item.text_values:
        value = item.text_values[int(rng.integers(0, len(item.text_values)))]
        valuenum = None
    elif forced_value is None and item.below_detection and rng.random() < item.below_detection_prob:
        value, valuenum = item.below_detection, None
    else:
        v = forced_value if forced_value is not None else float(rng.uniform(item.low, item.high))
        if forced_value is None and rng.random() < 0.02:
            v = v * (1.8 if rng.random() < 0.5 else 0.5)
        value, valuenum = _format_value(item, max(v, 0.0) if item.low >= 0 else v)
    flag = None
    if (
        valuenum is not None
        and item.ref_lower is not None
        and item.ref_upper is not None
        and (valuenum < item.ref_lower or valuenum > item.ref_upper)
    ):
        flag = "abnormal"
    comments = None
    if rng.random() < 0.06:
        cs = ctx.vocab.categories["lab_comments"]
        comments = str(cs[int(rng.integers(0, len(cs)))])
    return {
        "labevent_id": ids["labevent"],
        "subject_id": subject_id,
        "hadm_id": hadm_id,
        "specimen_id": specimen_id,
        "itemid": item.itemid,
        "order_provider_id": provider,
        "charttime": charttime,
        "storetime": charttime + MINUTE * int(rng.integers(30, 240)),
        "value": value,
        "valuenum": valuenum,
        "valueuom": item.valueuom,
        "ref_range_lower": item.ref_lower,
        "ref_range_upper": item.ref_upper,
        "flag": flag,
        "priority": priority,
        "comments": comments,
    }


def _draw(
    ctx: HospContext,
    rng: np.random.Generator,
    ids: dict[str, int],
    rows: list[dict[str, Any]],
    *,
    subject_id: int,
    hadm_id: int | None,
    panel: str,
    charttime: datetime,
    forced: dict[int, float] | None = None,
) -> None:
    """One specimen: every item of ``panel`` at ``charttime`` (``forced`` pins itemid values)."""
    ids["specimen"] += 1
    priority = str(pick(rng, ctx.vocab.weighted("lab_priorities")))
    provider = ctx.provider_or_null(rng, 0.2)
    for itemid in ctx.vocab.lab_panels[panel]:
        item = ctx.vocab.lab_item(itemid)
        rows.append(
            _lab_row(
                ctx,
                rng,
                ids,
                subject_id=subject_id,
                hadm_id=hadm_id,
                specimen_id=ids["specimen"],
                item=item,
                charttime=charttime,
                priority=priority,
                provider=provider,
                forced_value=None if forced is None else forced.get(itemid),
            )
        )


def _outside_admissions(subject, t: datetime) -> bool:
    return all(not (a.admittime <= t <= a.dischtime) for a in subject.admissions)


def _build_labs(ctx: HospContext) -> list[dict[str, Any]]:
    rng = ctx.rng("labevents")
    spec = ctx.plan.spec
    ids = {"labevent": spec.first_event_id - 1, "specimen": spec.first_event_id - 1}
    rows: list[dict[str, Any]] = []
    panels = ctx.vocab.lab_panel_weights
    for s in ctx.plan.subjects:
        for a in s.admissions:
            n_target = spec.labs_per_admission
            n_before = len(rows)
            if a.has("aki"):
                t0, t1 = ctx.trait_times.aki[a.hadm_id]
                _draw(
                    ctx,
                    rng,
                    ids,
                    rows,
                    subject_id=s.subject_id,
                    hadm_id=a.hadm_id,
                    panel="bmp",
                    charttime=t0,
                    forced={50912: 0.9},
                )
                _draw(
                    ctx,
                    rng,
                    ids,
                    rows,
                    subject_id=s.subject_id,
                    hadm_id=a.hadm_id,
                    panel="bmp",
                    charttime=t1,
                    forced={50912: 2.1},
                )
                if a.dischtime - t1 > 12 * HOUR:
                    _draw(
                        ctx,
                        rng,
                        ids,
                        rows,
                        subject_id=s.subject_id,
                        hadm_id=a.hadm_id,
                        panel="bmp",
                        charttime=t1 + 22 * HOUR,
                        forced={50912: 2.6},
                    )
            if a.has("t2dm"):
                t = a.admittime + MINUTE * int(rng.integers(30, 6 * 60))
                _draw(
                    ctx,
                    rng,
                    ids,
                    rows,
                    subject_id=s.subject_id,
                    hadm_id=a.hadm_id,
                    panel="bmp",
                    charttime=t,
                    forced={50931: float(rng.integers(180, 340))},
                )
            if a.has("sepsis"):
                t = ctx.trait_times.culture[a.hadm_id]
                _draw(
                    ctx,
                    rng,
                    ids,
                    rows,
                    subject_id=s.subject_id,
                    hadm_id=a.hadm_id,
                    panel="bg",
                    charttime=t,
                    forced={50813: 3.4},
                )
                _draw(
                    ctx,
                    rng,
                    ids,
                    rows,
                    subject_id=s.subject_id,
                    hadm_id=a.hadm_id,
                    panel="cbc",
                    charttime=t,
                    forced={51301: 17.2},
                )
            while len(rows) - n_before < n_target:
                panel = str(pick(rng, panels))
                # front-loaded: 70 % of draws in the first 3 days
                if rng.random() < 0.7:
                    t = uniform_dt(rng, a.admittime, min(a.dischtime, a.admittime + 3 * DAY))
                else:
                    t = uniform_dt(rng, a.admittime, a.dischtime)
                _draw(
                    ctx,
                    rng,
                    ids,
                    rows,
                    subject_id=s.subject_id,
                    hadm_id=a.hadm_id,
                    panel=panel,
                    charttime=t,
                )
        # outpatient labs (hadm_id NULL), outside every admission
        n_out = round(spec.outpatient_lab_fraction * spec.labs_per_admission * len(s.admissions))
        made = 0
        tries = 0
        while made < n_out and tries < 50:
            tries += 1
            anchor = s.admissions[int(rng.integers(0, len(s.admissions)))]
            if rng.random() < 0.5:
                t = anchor.admittime - DAY * int(rng.integers(10, 200))
            else:
                t = anchor.dischtime + DAY * int(rng.integers(10, 200))
            t = minute(t + MINUTE * int(rng.integers(8 * 60, 17 * 60)))
            if not _outside_admissions(s, t):
                continue
            panel = "bmp" if rng.random() < 0.6 else "cbc"
            n_before = len(rows)
            _draw(
                ctx, rng, ids, rows, subject_id=s.subject_id, hadm_id=None, panel=panel, charttime=t
            )
            made += len(rows) - n_before
    return rows


# -- microbiology -----------------------------------------------------------------------------


def _build_micro(ctx: HospContext) -> list[dict[str, Any]]:
    rng = ctx.rng("microbiologyevents")
    vocab = ctx.vocab
    spec = ctx.plan.spec
    ids = {"event": spec.first_event_id - 1, "specimen": spec.first_event_id - 1}
    specimens = vocab.categories["micro_specimens"]
    spec_weights = [float(x.get("weight", 1)) for x in specimens]
    organisms = vocab.categories["micro_organisms"]
    org_weights = [float(x.get("weight", 1)) for x in organisms]
    antibiotics = vocab.categories["micro_antibiotics"]
    dilutions = vocab.categories["micro_dilutions"]
    interp = vocab.weighted("micro_interpretations")
    quantities = vocab.weighted("micro_quantities")
    rows: list[dict[str, Any]] = []

    def specimen(
        a: AdmissionPlan, spec_entry: dict[str, Any], t: datetime, *, positive: bool
    ) -> None:
        ids["specimen"] += 1
        has_time = rng.random() < 0.85
        chartdate = datetime(t.year, t.month, t.day)
        provider = ctx.provider_or_null(rng, 0.2)
        store = t + DAY * int(rng.integers(1, 4)) + MINUTE * int(rng.integers(0, 24 * 60))
        base = {
            "subject_id": a.subject_id,
            "hadm_id": a.hadm_id,
            "micro_specimen_id": ids["specimen"],
            "order_provider_id": provider,
            "chartdate": chartdate,
            "charttime": t if has_time else None,
            "spec_itemid": int(spec_entry["spec_itemid"]),
            "spec_type_desc": str(spec_entry["spec_type_desc"]),
            "test_seq": 1,
            "storedate": datetime(store.year, store.month, store.day),
            "storetime": store,
            "test_itemid": int(spec_entry["test_itemid"]),
            "test_name": str(spec_entry["test_name"]),
        }
        if not positive:
            ids["event"] += 1
            rows.append(
                {
                    **base,
                    "microevent_id": ids["event"],
                    "org_itemid": None,
                    "org_name": None,
                    "isolate_num": None,
                    "quantity": None,
                    "ab_itemid": None,
                    "ab_name": None,
                    "dilution_text": None,
                    "dilution_comparison": None,
                    "dilution_value": None,
                    "interpretation": None,
                    "comments": "NO GROWTH.",
                }
            )
            return
        org = pick_weighted(rng, organisms, org_weights)
        quantity = pick(rng, quantities)
        n_ab = int(rng.integers(3, 7))
        chosen = rng.choice(len(antibiotics), size=n_ab, replace=False)
        for idx in sorted(int(i) for i in chosen):
            ab = antibiotics[idx]
            dil = dilutions[int(rng.integers(0, len(dilutions)))]
            ids["event"] += 1
            rows.append(
                {
                    **base,
                    "microevent_id": ids["event"],
                    "org_itemid": int(org["org_itemid"]),
                    "org_name": str(org["org_name"]),
                    "isolate_num": 1,
                    "quantity": quantity,
                    "ab_itemid": int(ab["ab_itemid"]),
                    "ab_name": str(ab["ab_name"]),
                    "dilution_text": str(dil[0]),
                    "dilution_comparison": str(dil[1]),
                    "dilution_value": float(dil[2]),
                    "interpretation": str(pick(rng, interp)),
                    "comments": None,
                }
            )

    blood = next(x for x in specimens if x["spec_type_desc"] == "BLOOD CULTURE")
    for a in ctx.plan.admissions:
        if a.has("sepsis"):
            specimen(a, blood, ctx.trait_times.culture[a.hadm_id], positive=rng.random() < 0.6)
        if rng.random() < 0.35:
            for _ in range(int(rng.integers(1, 3))):
                entry = pick_weighted(rng, specimens, spec_weights)
                t = uniform_dt(rng, a.admittime, a.dischtime)
                specimen(a, entry, t, positive=rng.random() < 0.3)
    return rows


# ---------------------------------------------------------------------------
# Table generators (one per hosp table)
# ---------------------------------------------------------------------------


def patients(ctx: HospContext) -> pl.DataFrame:
    rows = [
        {
            "subject_id": s.subject_id,
            "gender": s.gender,
            "anchor_age": s.anchor_age,
            "anchor_year": s.anchor_year,
            "anchor_year_group": s.anchor_year_group,
            "dod": s.dod,
        }
        for s in ctx.plan.subjects
    ]
    return to_frame(ctx.table("patients"), rows)


def admissions(ctx: HospContext) -> pl.DataFrame:
    rng = ctx.rng("admissions")
    rows = []
    for s in ctx.plan.subjects:
        for a in s.admissions:
            rows.append(
                {
                    "subject_id": s.subject_id,
                    "hadm_id": a.hadm_id,
                    "admittime": a.admittime,
                    "dischtime": a.dischtime,
                    "deathtime": a.deathtime,
                    "admission_type": a.admission_type,
                    "admit_provider_id": ctx.provider_or_null(rng, 0.1),
                    "admission_location": a.admission_location,
                    "discharge_location": a.discharge_location,
                    "insurance": a.insurance,
                    "language": s.language,
                    "marital_status": s.marital_status,
                    "race": s.race,
                    "edregtime": a.edregtime,
                    "edouttime": a.edouttime,
                    "hospital_expire_flag": 1 if a.died else 0,
                }
            )
    return to_frame(ctx.table("admissions"), rows)


def transfers(ctx: HospContext) -> pl.DataFrame:
    ed_unit = str(ctx.vocab.categories["ed_careunit"])
    next_id = ctx.plan.spec.first_event_id
    rows = []
    for a in ctx.plan.admissions:
        if a.ed:
            rows.append(
                {
                    "subject_id": a.subject_id,
                    "hadm_id": a.hadm_id,
                    "transfer_id": next_id,
                    "eventtype": "ED",
                    "careunit": ed_unit,
                    "intime": a.edregtime,
                    "outtime": a.edouttime,
                }
            )
            next_id += 1
        for i, seg in enumerate(a.segments):
            rows.append(
                {
                    "subject_id": a.subject_id,
                    "hadm_id": a.hadm_id,
                    "transfer_id": next_id,
                    "eventtype": "admit" if i == 0 else "transfer",
                    "careunit": seg.careunit,
                    "intime": seg.intime,
                    "outtime": seg.outtime,
                }
            )
            next_id += 1
        rows.append(
            {
                "subject_id": a.subject_id,
                "hadm_id": a.hadm_id,
                "transfer_id": next_id,
                "eventtype": "discharge",
                "careunit": None,
                "intime": a.dischtime,
                "outtime": None,
            }
        )
        next_id += 1
    return to_frame(ctx.table("transfers"), rows)


def services(ctx: HospContext) -> pl.DataFrame:
    rng = ctx.rng("services")
    svc = ctx.vocab.weighted("services")
    rows = []
    for a in ctx.plan.admissions:
        first = str(pick(rng, svc))
        rows.append(
            {
                "subject_id": a.subject_id,
                "hadm_id": a.hadm_id,
                "transfertime": a.admittime,
                "prev_service": None,
                "curr_service": first,
            }
        )
        if rng.random() < 0.2 and a.los > 4 * HOUR:
            second = str(pick(rng, svc))
            while second == first:
                second = str(pick(rng, svc))
            t = uniform_dt(rng, a.admittime + HOUR, a.dischtime - HOUR)
            rows.append(
                {
                    "subject_id": a.subject_id,
                    "hadm_id": a.hadm_id,
                    "transfertime": t,
                    "prev_service": first,
                    "curr_service": second,
                }
            )
    return to_frame(ctx.table("services"), rows)


def _sample_codes(rng: np.random.Generator, codes, n: int, exclude: set[str]) -> list:
    pool = [c for c in codes if c.code not in exclude]
    weights = [c.weight for c in pool]
    out: list = []
    while pool and len(out) < n:
        c = pick_weighted(rng, pool, weights)
        i = pool.index(c)
        pool.pop(i)
        weights.pop(i)
        out.append(c)
    return out


def diagnoses_icd(ctx: HospContext) -> pl.DataFrame:
    rng = ctx.rng("diagnoses_icd")
    rows = []
    for a in ctx.plan.admissions:
        codes = ctx.vocab.icd_diagnoses[a.icd_version]
        chosen: list = []
        for trait in ("sepsis", "aki", "t2dm"):
            if a.has(trait):
                # planted: always the primary code (first tagged entry: 99591/A419, 5849/N179,
                # 25000/E119), plus one more tagged code half of the time
                tagged = ctx.vocab.icd_tagged(a.icd_version, trait)
                chosen.append(tagged[0])
                if rng.random() < 0.5:
                    chosen.extend(_sample_codes(rng, tagged[1:], 1, {c.code for c in chosen}))
        n = int(rng.integers(3, 13))
        chosen.extend(_sample_codes(rng, codes, max(0, n - len(chosen)), {c.code for c in chosen}))
        for seq, c in enumerate(chosen, start=1):
            rows.append(
                {
                    "subject_id": a.subject_id,
                    "hadm_id": a.hadm_id,
                    "seq_num": seq,
                    "icd_code": c.code,
                    "icd_version": a.icd_version,
                }
            )
    return to_frame(ctx.table("diagnoses_icd"), rows)


def procedures_icd(ctx: HospContext) -> pl.DataFrame:
    rng = ctx.rng("procedures_icd")
    rows = []
    for a in ctx.plan.admissions:
        if rng.random() >= 0.5:
            continue
        codes = _sample_codes(
            rng, ctx.vocab.icd_procedures[a.icd_version], int(rng.integers(1, 4)), set()
        )
        for seq, c in enumerate(codes, start=1):
            rows.append(
                {
                    "subject_id": a.subject_id,
                    "hadm_id": a.hadm_id,
                    "seq_num": seq,
                    "chartdate": uniform_dt(rng, a.admittime, a.dischtime).date(),
                    "icd_code": c.code,
                    "icd_version": a.icd_version,
                }
            )
    return to_frame(ctx.table("procedures_icd"), rows)


def drgcodes(ctx: HospContext) -> pl.DataFrame:
    rng = ctx.rng("drgcodes")
    hcfa = ctx.vocab.categories["drg_hcfa"]
    apr = ctx.vocab.categories["drg_apr"]

    def choose(entries, a: AdmissionPlan):
        for trait in ("sepsis", "aki", "t2dm"):
            tagged = [e for e in entries if trait in (e.get("tags") or ())]
            if a.has(trait) and tagged:
                return pick_weighted(rng, tagged, [float(e.get("weight", 1)) for e in tagged])
        return pick_weighted(rng, entries, [float(e.get("weight", 1)) for e in entries])

    rows = []
    for a in ctx.plan.admissions:
        h = choose(hcfa, a)
        rows.append(
            {
                "subject_id": a.subject_id,
                "hadm_id": a.hadm_id,
                "drg_type": "HCFA",
                "drg_code": str(h["code"]),
                "description": str(h["description"]),
                "drg_severity": None,
                "drg_mortality": None,
            }
        )
        if rng.random() < 0.9:
            p = choose(apr, a)
            rows.append(
                {
                    "subject_id": a.subject_id,
                    "hadm_id": a.hadm_id,
                    "drg_type": "APR",
                    "drg_code": str(p["code"]),
                    "description": str(p["description"]),
                    "drg_severity": int(rng.integers(1, 5)),
                    "drg_mortality": int(rng.integers(1, 5)),
                }
            )
    return to_frame(ctx.table("drgcodes"), rows)


def hcpcsevents(ctx: HospContext) -> pl.DataFrame:
    rng = ctx.rng("hcpcsevents")
    items = list(ctx.vocab.hcpcs)
    rows = []
    for a in ctx.plan.admissions:
        if rng.random() >= 0.3:
            continue
        k = int(rng.integers(1, 4))
        chosen = rng.choice(len(items), size=min(k, len(items)), replace=False)
        for seq, idx in enumerate(sorted(int(i) for i in chosen), start=1):
            h = items[idx]
            rows.append(
                {
                    "subject_id": a.subject_id,
                    "hadm_id": a.hadm_id,
                    "chartdate": uniform_dt(rng, a.admittime, a.dischtime).date(),
                    "hcpcs_cd": h.code,
                    "seq_num": seq,
                    "short_description": h.short_description,
                }
            )
    return to_frame(ctx.table("hcpcsevents"), rows)


def _omr_value(rng: np.random.Generator, name: str) -> str:
    if name.startswith("Blood Pressure"):
        return f"{int(rng.integers(100, 171))}/{int(rng.integers(55, 101))}"
    if name.startswith("Weight"):
        return f"{float(rng.uniform(110, 260)):.1f}"
    if name.startswith("BMI"):
        return f"{float(rng.uniform(19, 42)):.1f}"
    if name.startswith("Height"):
        return f"{int(rng.integers(58, 77))}"
    return ">60" if rng.random() < 0.6 else f"{int(rng.integers(20, 60))}"


def omr(ctx: HospContext) -> pl.DataFrame:
    rng = ctx.rng("omr")
    names = ctx.vocab.weighted("omr_result_names")
    rows = []
    for s in ctx.plan.subjects:
        dates: set[date] = set()
        for _ in range(int(rng.integers(2, 9))):
            anchor = s.admissions[int(rng.integers(0, len(s.admissions)))]
            d = (anchor.admittime - DAY * int(rng.integers(0, 400))).date()
            if d in dates:
                continue
            dates.add(d)
            picked: list[str] = []
            for _ in range(int(rng.integers(1, 4))):
                n = str(pick(rng, names))
                if n not in picked:
                    picked.append(n)
            for seq, n in enumerate(picked, start=1):
                rows.append(
                    {
                        "subject_id": s.subject_id,
                        "chartdate": d,
                        "seq_num": seq,
                        "result_name": n,
                        "result_value": _omr_value(rng, n),
                    }
                )
    return to_frame(ctx.table("omr"), rows)


def labevents(ctx: HospContext) -> pl.DataFrame:
    return to_frame(ctx.table("labevents"), ctx.labs)


def microbiologyevents(ctx: HospContext) -> pl.DataFrame:
    return to_frame(ctx.table("microbiologyevents"), ctx.micro)


def _dose_per_24(drug: Drug) -> float | None:
    hours = FREQUENCY_HOURS.get(drug.frequency)
    return None if hours is None else float(24 // hours)


def prescriptions(ctx: HospContext) -> pl.DataFrame:
    rows = []
    for o in ctx.orders.meds:
        d = o.drug
        common = {
            "subject_id": o.subject_id,
            "hadm_id": o.hadm_id,
            "pharmacy_id": o.pharmacy_id,
            "poe_id": o.poe_id,
            "poe_seq": o.poe_seq,
            "order_provider_id": o.provider,
            "starttime": o.starttime,
            "stoptime": o.stoptime,
            "route": d.route,
        }
        rows.append(
            {
                **common,
                "drug_type": "MAIN",
                "drug": d.drug,
                "formulary_drug_cd": d.formulary_drug_cd,
                "gsn": d.gsn,
                "ndc": d.ndc,
                "prod_strength": d.prod_strength,
                "form_rx": d.form_rx,
                "dose_val_rx": d.dose_val_rx,
                "dose_unit_rx": d.dose_unit_rx,
                "form_val_disp": d.form_val_disp,
                "form_unit_disp": d.form_unit_disp,
                "doses_per_24_hrs": _dose_per_24(d),
            }
        )
        if d.base is not None:
            b = ctx.vocab.base_named(d.base)
            rows.append(
                {
                    **common,
                    "drug_type": "BASE",
                    "drug": str(b["drug"]),
                    "formulary_drug_cd": str(b["formulary_drug_cd"]),
                    "gsn": str(b["gsn"]),
                    "ndc": str(b["ndc"]),
                    "prod_strength": str(b["prod_strength"]),
                    "form_rx": str(b["form_rx"]),
                    "dose_val_rx": None,
                    "dose_unit_rx": None,
                    "form_val_disp": str(b["form_val_disp"]),
                    "form_unit_disp": str(b["form_unit_disp"]),
                    "doses_per_24_hrs": None,
                }
            )
    return to_frame(ctx.table("prescriptions"), rows)


def pharmacy(ctx: HospContext) -> pl.DataFrame:
    rng = ctx.rng("pharmacy")
    rows = []
    for o in ctx.orders.meds:
        d = o.drug
        continuous = d.kind in ("infusion", "fluid")
        antibiotic = "antibiotic" in d.tags
        rows.append(
            {
                "subject_id": o.subject_id,
                "hadm_id": o.hadm_id,
                "pharmacy_id": o.pharmacy_id,
                "poe_id": o.poe_id,
                "starttime": o.starttime,
                "stoptime": o.stoptime,
                "medication": d.drug,
                "proc_type": d.proc_type,
                "status": "Discontinued"
                if o.dc_poe_id
                else ("Active" if rng.random() < 0.3 else "Expired"),
                "entertime": o.ordertime,
                "verifiedtime": o.ordertime + MINUTE * int(rng.integers(5, 60)),
                "route": d.route,
                "frequency": d.frequency,
                "disp_sched": DISP_SCHED.get(d.frequency),
                "infusion_type": "Continuous" if continuous else None,
                "sliding_scale": "Y" if d.kind == "sliding" else None,
                "lockout_interval": None,
                "basal_rate": None,
                "one_hr_max": None,
                "doses_per_24_hrs": _dose_per_24(d),
                "duration": float(int((o.stoptime - o.starttime) / DAY)) if antibiotic else None,
                "duration_interval": "Days" if antibiotic else None,
                "expiration_value": 24 if continuous else None,
                "expiration_unit": "Hours" if continuous else None,
                "expirationdate": None,
                "dispensation": "Omnicell"
                if d.kind in ("bolus", "prn", "sliding", "once")
                else "Floor Stock Item",
                "fill_quantity": None,
            }
        )
    return to_frame(ctx.table("pharmacy"), rows)


def poe(ctx: HospContext) -> pl.DataFrame:
    rows = [
        {
            "poe_id": p.poe_id,
            "poe_seq": p.poe_seq,
            "subject_id": p.subject_id,
            "hadm_id": p.hadm_id,
            "ordertime": p.ordertime,
            "order_type": p.order_type,
            "order_subtype": p.order_subtype,
            "transaction_type": p.transaction_type,
            "discontinue_of_poe_id": p.discontinue_of_poe_id,
            "discontinued_by_poe_id": p.discontinued_by_poe_id,
            "order_provider_id": p.provider,
            "order_status": p.order_status,
        }
        for p in ctx.orders.poe
    ]
    return to_frame(ctx.table("poe"), rows)


def poe_detail(ctx: HospContext) -> pl.DataFrame:
    rows = [
        {
            "poe_id": p.poe_id,
            "poe_seq": p.poe_seq,
            "subject_id": p.subject_id,
            "field_name": name,
            "field_value": value,
        }
        for p in ctx.orders.poe
        for name, value in p.details
    ]
    return to_frame(ctx.table("poe_detail"), rows)


def emar(ctx: HospContext) -> pl.DataFrame:
    rng = ctx.rng("emar")
    rows = []
    for ad in ctx.orders.admins:
        periodic = ad.order.drug.kind in ("bolus", "sliding", "flush", "once")
        rows.append(
            {
                "subject_id": ad.subject_id,
                "hadm_id": ad.hadm_id,
                "emar_id": ad.emar_id,
                "emar_seq": ad.emar_seq,
                "poe_id": ad.order.poe_id,
                "pharmacy_id": ad.order.pharmacy_id,
                "enter_provider_id": ad.provider,
                "charttime": ad.charttime,
                "medication": ad.order.drug.drug,
                "event_txt": ad.event_txt,
                "scheduletime": ad.charttime.replace(minute=0) if periodic else None,
                "storetime": ad.charttime + MINUTE * int(rng.integers(0, 30)),
            }
        )
    return to_frame(ctx.table("emar"), rows)


def _administration_type(drug: Drug) -> str:
    if drug.kind in ("infusion", "fluid"):
        return "IV Infusion (Continuous)"
    if drug.kind == "flush":
        return "IV Flush"
    if drug.route == "PO":
        return "Oral"
    if drug.route == "SC":
        return "Subcutaneous"
    if drug.proc_type == "IV Piggyback":
        return "IV Piggyback"
    return "Intravenous Push"


def emar_detail(ctx: HospContext) -> pl.DataFrame:
    rng = ctx.rng("emar_detail")
    empty = {c.name: None for c in ctx.table("emar_detail").columns}
    rows = []
    for ad in ctx.orders.admins:
        d = ad.order.drug
        head = {
            **empty,
            "subject_id": ad.subject_id,
            "emar_id": ad.emar_id,
            "emar_seq": ad.emar_seq,
            "pharmacy_id": ad.order.pharmacy_id,
            "route": d.route,
        }
        not_given = ad.event_txt == "Not Given"
        rows.append(
            {
                **head,
                "parent_field_ordinal": None,
                "complete_dose_not_given": "Y" if not_given else "N",
                "dose_due": d.dose_val_rx,
                "dose_due_unit": d.dose_unit_rx,
                "dose_given": None if not_given else d.dose_val_rx,
                "dose_given_unit": None if not_given else d.dose_unit_rx,
                "will_remainder_of_dose_be_given": "N" if not_given else None,
            }
        )
        if not_given:
            continue
        infusion = d.kind in ("infusion", "fluid")
        rate = f"{int(rng.integers(5, 125))}" if infusion else None
        rows.append(
            {
                **head,
                "parent_field_ordinal": "1.1",
                "administration_type": _administration_type(d),
                "barcode_type": "Standard" if rng.random() < 0.9 else "NAP",
                "reason_for_no_barcode": None if rng.random() < 0.95 else "Barcode Damaged",
                "dose_given": d.dose_val_rx,
                "dose_given_unit": d.dose_unit_rx,
                "product_amount_given": d.form_val_disp,
                "product_unit": d.form_unit_disp,
                "product_code": d.formulary_drug_cd,
                "product_description": f"{d.drug} {d.prod_strength}",
                "infusion_rate": rate,
                "infusion_rate_unit": "mL/hour" if infusion else None,
                "infusion_rate_adjustment": "Rate Change"
                if ad.event_txt == "Rate Change"
                else None,
                "prior_infusion_rate": f"{int(rng.integers(5, 125))}"
                if ad.event_txt == "Rate Change"
                else None,
                "site": "Left Arm" if d.route == "SC" and rng.random() < 0.5 else None,
                "side": None,
                "infusion_complete": "Y" if ad.event_txt == "Stopped" else None,
                "new_iv_bag_hung": "Y" if ad.event_txt == "Started" else None,
            }
        )
    return to_frame(ctx.table("emar_detail"), rows)


def provider(ctx: HospContext) -> pl.DataFrame:
    return to_frame(ctx.table("provider"), [{"provider_id": p} for p in ctx.plan.providers])


def d_labitems(ctx: HospContext) -> pl.DataFrame:
    rows = [
        {"itemid": i.itemid, "label": i.label, "fluid": i.fluid, "category": i.category}
        for i in ctx.vocab.lab_items
    ]
    return to_frame(ctx.table("d_labitems"), rows)


def d_icd_diagnoses(ctx: HospContext) -> pl.DataFrame:
    rows = [
        {"icd_code": c.code, "icd_version": v, "long_title": c.title}
        for v in (9, 10)
        for c in ctx.vocab.icd_diagnoses[v]
    ]
    return to_frame(ctx.table("d_icd_diagnoses"), rows)


def d_icd_procedures(ctx: HospContext) -> pl.DataFrame:
    rows = [
        {"icd_code": c.code, "icd_version": v, "long_title": c.title}
        for v in (9, 10)
        for c in ctx.vocab.icd_procedures[v]
    ]
    return to_frame(ctx.table("d_icd_procedures"), rows)


def d_hcpcs(ctx: HospContext) -> pl.DataFrame:
    rows = [
        {
            "code": h.code,
            "category": h.category,
            "long_description": h.long_description,
            "short_description": h.short_description,
        }
        for h in ctx.vocab.hcpcs
    ]
    return to_frame(ctx.table("d_hcpcs"), rows)


#: Generator per hosp table (contract order; keys are the 22 ``mimiciv_hosp`` table names).
GENERATORS: dict[str, Callable[[HospContext], pl.DataFrame]] = {
    "admissions": admissions,
    "d_hcpcs": d_hcpcs,
    "diagnoses_icd": diagnoses_icd,
    "d_icd_diagnoses": d_icd_diagnoses,
    "d_icd_procedures": d_icd_procedures,
    "d_labitems": d_labitems,
    "drgcodes": drgcodes,
    "emar_detail": emar_detail,
    "emar": emar,
    "hcpcsevents": hcpcsevents,
    "labevents": labevents,
    "microbiologyevents": microbiologyevents,
    "omr": omr,
    "patients": patients,
    "pharmacy": pharmacy,
    "poe_detail": poe_detail,
    "poe": poe,
    "prescriptions": prescriptions,
    "procedures_icd": procedures_icd,
    "provider": provider,
    "services": services,
    "transfers": transfers,
}


def build_hosp_frames(
    plan: FixturePlan, *, vocab: Vocab | None = None, contract: Contract | None = None
) -> dict[str, pl.DataFrame]:
    """All 22 hosp frames for ``plan`` (``{table: frame}`` in contract order)."""
    from mimicwarehouse.fixtures.vocab import load_vocab
    from mimicwarehouse.schema.contract import load_contract

    ctx = HospContext(plan, vocab or load_vocab(), contract or load_contract())
    expected = [t.name for t in ctx.contract.by_schema(HOSP_SCHEMA)]
    missing = [t for t in expected if t not in GENERATORS]
    if missing:
        raise RuntimeError(f"no generator for hosp table(s) {missing}")
    return {name: GENERATORS[name](ctx) for name in expected}


__all__ = [
    "GENERATORS",
    "HOSP_SCHEMA",
    "POLARS_TYPES",
    "Admin",
    "HospContext",
    "MedOrder",
    "Orders",
    "PoeRow",
    "TraitTimes",
    "build_hosp_frames",
    "polars_schema",
    "to_frame",
]
