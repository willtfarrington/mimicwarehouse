# Event-aligned timelines (EP-49)

The one written answer to "how are events aligned to a clinical anchor and binned
here?" - the prose twin of `src/mimicwarehouse/timeline.py` (DESIGN §7, §15). Capability 8
(event-aligned timeline queries) is the query layer under the longitudinal trajectories
(EP-82), the exposure-response analyses (EP-86), the hourly marts (EP-55/56) and the
owner-only timeline viewer (EP-67): *take events from table X, align them to anchor A per
unit, keep the window `[-a, +b)` hours, bin, join as-of*. Everything is built on the
time-semantics module (EP-34, [time-semantics.md](time-semantics.md)): relative time is
`date_diff('second', anchor, event) / 3600.0`, bins are half-open `[start, end)`, and the
units come from the unit-of-analysis registry. Nothing on this page is derived from data:
the tables are rendered from the module's registries (`python -m mimicwarehouse.timeline`
re-renders the marked blocks; `test_ep49` asserts they are in sync).
All MIMIC-IV analyses in this repository are retrospective.

## 1. The shape of the API

Every builder is a **pure function that returns SQL text** (`anchor_sql`, `align_sql`,
`hourly_bins_sql`, `population_summary_sql`, `window_join_sql`, `asof_join_sql`,
`event_at_sql`, `stay_events_sql`) and has a twin that runs it as a lazy DuckDB relation on
the connection you hand in (`align(..., con=)`, `hourly_bins(..., con=)`, ...). A relation
reads nothing until a consumer aggregates it, and `rel.sql_query()` is the statement a
provenance run records (`run.record_sql`). The API never prints a row: `population_summary`
is the released shape a session may print, `to_mart` writes Parquet for the marts, and the
single row-level function (`stay_events`) sits behind the owner gate of §6.

Timestamps are the naive, per-patient date-shifted `TIMESTAMP`s as shipped; the API only
ever subtracts them within one patient (relative time), never reads a calendar.

## 2. Anchors

An **anchor** names the clinical time zero of a unit: a table or view, its timestamp
column, the finest identifier the source carries (`key`), an optional filter and a
`selector` - `first` / `last` event per unit, or `each` (one row per event).
`timeline.anchor_sql(anchor, grain)` yields `(grain keys, anchor_time)` for **every** unit
of the grain: a unit without an anchor event carries a NULL `anchor_time` (documented;
consumers decide whether to drop it), and the alignment step skips such units.

<!-- anchors:begin -->
| anchor | native grain | source | time column | key | selector | filter | what it is |
|---|---|---|---|---|---|---|---|
| `hosp_admit` | `hadm` | `mimiciv_hosp.admissions` | `admittime` | `hadm_id` | `first` | - | hospital admission time |
| `hosp_discharge` | `hadm` | `mimiciv_hosp.admissions` | `dischtime` | `hadm_id` | `first` | - | hospital discharge time |
| `icu_in` | `icustay` | `mimiciv_icu.icustays` | `intime` | `stay_id` | `first` | - | ICU admission (the hours_since_icu_intime axis) |
| `icu_out` | `icustay` | `mimiciv_icu.icustays` | `outtime` | `stay_id` | `first` | - | ICU discharge |
| `first_culture` | `hadm` | `mimiciv_hosp.microbiologyevents` | `charttime` | `hadm_id` | `first` | - | first microbiology specimen of the admission |
| `first_antibiotic` | `hadm` | `mimiciv_derived.antibiotic` | `starttime` | `hadm_id` | `first` | - | first antibiotic start of the admission (mimic-code antibiotic) |
| `suspected_infection` | `hadm` | `mimiciv_derived.suspicion_of_infection` | `suspected_infection_time` | `hadm_id` | `first` | `s.suspected_infection = 1` | first suspected-infection time of the admission (Sepsis-3) |
| `vent_start` | `icustay` | `mimiciv_derived.ventilation` | `starttime` | `stay_id` | `first` | `s.ventilation_status IN ('InvasiveVent', 'NonInvasiveVent')` | first ventilation episode of the stay of the given kinds |
| `deterioration` | `icustay` | `mimiciv_derived.vasoactive_agent` | `starttime` | `stay_id` | `first` | `any vasopressor rate column IS NOT NULL` | first vasopressor start of the stay (inodilators excluded) |
| `med_start(codeset, source)` | `hadm / icustay` | `prescriptions or inputevents` | `starttime` | `hadm_id / stay_id` | `first` | `drug names of the set (prescriptions) or its ICU itemids` | first start of a drug of the code set |
| `procedure(itemids)` | `icustay` | `mimiciv_icu.procedureevents` | `starttime` | `stay_id` | `first` | `itemid IN (...)` | first procedure of the itemids |
| `vent_start(kinds)` | `icustay` | `mimiciv_derived.ventilation` | `starttime` | `stay_id` | `first` | `ventilation_status IN (kinds)` | first ventilation episode of the kinds |
| `phenotype_onset(id@version)` | `the phenotype's` | `mimiciv_derived.phenotype_<id>` | `onset_time` | `per grain` | `first` | `flag` | phenotype onset (latest built version) |
| `custom_anchor(name, sql, key=, grain=)` | `given` | `(your SELECT)` | `anchor_time` | `given` | `first` | - | subject_id, hadm_id, stay_id, anchor_time |
<!-- anchors:end -->

How an anchor reaches a grain it was not defined on - the **join-key rule**: anchor events
are normalised to `(subject_id, hadm_id, stay_id, anchor_time)` (a stay-keyed concept view
such as `ventilation`, which carries `stay_id` only, is joined to `icustays` for the other
two keys) and joined to the grain's units on the finest key both sides carry. So `icu_in`
on the `hadm` grain is the admission's first ICU admission (`selector=first` per
`hadm_id`), `first_culture` on the `icustay` grain gives every stay of an admission the
admission's first culture, and a subject-keyed custom anchor reaches every admission of
the subject. `phenotype_onset` reads the `mimiciv_derived.phenotype_<id>` view, which is
the **latest built** version of the id on the tier (EP-41); the requested version is kept
in the anchor's parameters and `meta.phenotype_versions` is where a caller checks it. The
grains the API aligns on are `icustay`, `hadm` and `subject` (the `subject` grain has no
unit window, so `clip_to_stay` is refused there); `icu_day` / `hour_bin` are what §4
produces, not inputs.

## 3. Event sources and alignment

An **event source** is one event table as the aligner reads it: the timestamp, the finest
key it reliably carries (`labevents` is subject-keyed - its `hadm_id` is often NULL), and
the `code` / `value` / `valueuom` expressions. `code` and `valueuom` are VARCHAR cut to 64
characters (the free-text ceiling `safe_query` applies to a subject-keyed read), `value` is
DOUBLE (NULL for sources without a numeric value).

<!-- sources:begin -->
| preset | call | table | time; key; code, value / unit |
|---|---|---|---|
| `labs` | `labs(itemids)` | `mimiciv_hosp.labevents` | charttime; subject-keyed; valuenum / valueuom |
| `vitals` | `vitals(itemids)` | `mimiciv_icu.chartevents` | charttime; stay-keyed; valuenum / valueuom |
| `inputs` | `inputs(itemids)` | `mimiciv_icu.inputevents` | starttime; stay-keyed; amount / amountuom |
| `outputs` | `outputs(itemids)` | `mimiciv_icu.outputevents` | charttime; stay-keyed; value / valueuom |
| `meds` | `meds(codeset, source=prescriptions|inputevents)` | `mimiciv_hosp.prescriptions or mimiciv_icu.inputevents` | starttime; admission/stay-keyed; drug or itemid, dose/amount |
| `procedures` | `procedures(itemids)` | `mimiciv_icu.procedureevents` | starttime; stay-keyed; value / valueuom |
| `micro` | `micro()` | `mimiciv_hosp.microbiologyevents` | charttime; admission-keyed; spec_itemid, no value |
| `transfers` | `transfers()` | `mimiciv_hosp.transfers` | intime; admission-keyed; careunit, no value |
| `custom` | `custom_source(name, sql, key=, has_stay=)` | `(your SELECT)` | subject_id, hadm_id, stay_id, event_time, code, value, valueuom |
<!-- sources:end -->

`timeline.align(source, anchor, window=(-24, 72), grain="icustay", clip_to_stay=True,
con=)` yields `(grain keys, anchor_time, event_time, hours_since_anchor, code, value,
valueuom, source_table)`. The **window semantics**:

- `hours_since_anchor` is signed (`timesem.sql_hours_since`): negative before the anchor.
- The window is half-open in hours: an event is kept when `window[0] <= hours <
  window[1]`, so with `(-24, 72)` an event exactly 24 h before the anchor is in and one
  exactly 72 h after it is out.
- `clip_to_stay=True` additionally keeps only events inside the unit's own window -
  `[intime, outtime)` for a stay, `[admittime, dischtime)` for an admission - so a
  pre-ICU laboratory result is dropped even when the window reaches back to it; pass
  `clip_to_stay=False` for baselines.
- Events join the units on the finest shared key: a subject-keyed source on the
  `icustay` grain reaches every stay of the subject, and the window / clip decide which
  stays keep the event (two overlapping windows of one subject can both keep it).
- Units whose anchor is NULL contribute nothing; the relation carries no ordering (a
  consumer that needs one adds it).

Three joins complete the layer:

- `window_join(left, right, by, on, before_h, after_h, con=)` keeps every `right` row
  whose time lies in `[left.on - before_h, left.on + after_h)` of a `left` row with the
  same `by` keys, with `hours_since` = right - left.
- `asof_join(left, right, by, on, direction="backward", tolerance_h=None, con=,
  how="inner")` is DuckDB's `ASOF JOIN`: for every `left` row the one `right` row with the
  greatest `right.on <= left.on` (`backward`) or the smallest `right.on >= left.on`
  (`forward`). With a tolerance a match farther away is dropped (`inner`) or blanked
  (`left`: the left row stays, `hours_since` becomes NULL).
- `event_at(source, anchor, tolerance_h, grain, clip_to_stay=False, con=)` is the
  **as-of rule** for baselines: per unit and code, the last event at or before the anchor
  and within `tolerance_h` hours of it (an event at the anchor itself counts). It is
  `align` over `[-tolerance, 0]` followed by a `row_number()` pick, so it inherits the
  join-key rule above.

## 4. Bins and the population summary

`timeline.hourly_bins(aligned, width_h=1, aggs={"value": ["count", "mean", "min", "max",
"last"]}, con=, keys=("stay_id",), fill=False, window=None)` groups the aligned relation
per unit, `code` and `[start, end)` bin of `width_h` hours over `hours_since_anchor`
(`timesem.sql_hour_bin`: bin *i* covers `[i * width, (i + 1) * width)`; bins before the
anchor are negative). The columns are `bin_index`, `bin_start_h`, `bin_end_h` and one
per requested aggregation - `n_<col>` for `count`, `<col>_<agg>` otherwise (`mean`, `min`,
`max`, `sum`, `median`, `first`, `last`; the last two pick by `event_time`). `fill=True`
adds the empty bins of every unit x code present in the relation - over `window` when
given, else between the smallest and largest bin seen - with the count 0 and every other
aggregate NULL, which is how the bins of one unit sum to its raw event count.
`daily_bins` is the 24-hour form (day 0 = `[0, 24)` hours).

`timeline.population_summary(binned, k=11)` is the **only frame shape a session prints**:
per `code` and bin across units - `n_units` (units with at least one event in the bin),
the pooled `value_mean`, the quartiles of the per-unit means (`value_p25` / `value_p75`)
and the extremes - released through the `safe.SUPPRESSOR` seam (EP-43): every row whose
`n_units` lies in `(0, k)` is withheld together with everything it carries, and the
complementary cells the gate requires go with it. The event count per bin is deliberately
**not** released: beside the unit count it is a nested pair whose small difference the
disclosure gate reads as a derivable cell (EP-33 amendment b).

`timeline.to_mart(relation, path)` writes a relation as Parquet through the rename-aside
publisher (`<path>.new` then `publish.swap_file`); the hourly marts (EP-55/56) call it.

## 5. The alignment benchmark

`mwh timeline bench --tier <t>` runs `align(labs(50912, 50813, 50971, 51222, 51301),
icu_in, (-6, 48))` - creatinine, lactate, potassium, hemoglobin and white blood cells from
the EP-39 item catalogue, clipped to the stay - into hourly bins and the population
summary, inside a `kind: bench` run (EP-35) that records the three statements under
`sql/`, a `kind: query` benchmark line named `timeline_labs_icu_in_48h` (wall, peak RSS,
raw bin rows), the released frame under `tables/` and four gated exports under
`exports/`: `timeline_labs_icu_in_48h.parquet`, its `.md` twin (claim type
*exploratory*, the retrospective statement, the parameters, the table, the reproduction
block), `population_band.png` (per item the pooled mean per bin with the inter-quartile
band of the per-stay means) and its `population_band.csv` source table - each written
only after `disclose.check` passes and each with a `.disclosure.json` sidecar. The full
tier runs only as a background job (`--background --job ep49-timeline-bench`;
`mwh jobs --job ep49-timeline-bench`); `mwh runs benchmarks --kind query` reads the
ledger line.

## 6. The owner-only single-stay path

Single-stay timelines are row-level data (GOVERNANCE §6). `timeline.stay_events(stay_id,
sources, conn=)` returns the lane data of one ICU stay for the viewer (EP-67) - every
event of every source inside the stay, aligned to `icu_in`, with a `lane` column - and is
reachable only through a connection opened as `open_catalog(tier, role="owner")`: the
opener stamps the resolved role on the connection as the DuckDB session variable
`mwh_role` (per connection, never persisted), and `stay_events` raises `PermissionError`
for any connection that does not carry `owner` - the default `agent` role of every Claude
session, a plain `duckdb.connect()`, an in-memory database. In a Claude session
`MWH_ROLE` is unset, so the gate is closed by construction (D-32); it is a guard against
accidental use inside the session, not a security boundary - the prose rules are the
control. Every call writes one audit line through the EP-30 seam (`safe.AuditLine` via
`fsio.append_jsonl` into `runs/audit.jsonl`): actor `owner`, `statement_sha256` =
sha256 of `row_view:` + the canonical request, `sql_text` = `row_view:stay_events
sources=<lanes>` - the event is visible in `runs.audit` without the row selection being
recorded. Tests exercise it on the fixture tier only; EP-58's `owner_rows()` wraps the
same gate for the app.

## 7. Examples

Baseline creatinine before ICU admission, one row per stay (as-of, 24 h tolerance):

```python
from mimicwarehouse import timeline as tl
from mimicwarehouse.catalog.connect import open_catalog

con = open_catalog("dev")
baseline = tl.event_at(tl.labs([50912]), tl.ICU_IN, tolerance_h=24, con=con)
# columns: stay_id, anchor_time, event_time, hours_since_anchor, code, value, valueuom, source_table
```

Hourly heart-rate trajectory of the first 48 h after a vasopressor start, released:

```python
aligned = tl.align(tl.vitals([220045]), tl.DETERIORATION, window=(-6, 48), con=con)
binned = tl.hourly_bins(aligned, width_h=1, con=con)
summary = tl.population_summary(binned, k=11)  # the only frame shape a session prints
```

Antibiotic starts relative to the first culture, per admission, kept as a mart:

```python
aligned = tl.align(
    tl.meds("antibiotics@1.0.0"), tl.FIRST_CULTURE, window=(-72, 24), grain="hadm", con=con
)
tl.to_mart(tl.daily_bins(aligned, con=con, keys=("hadm_id",)), some_mart_path)
```

## 8. What this page deliberately does not decide

- The hourly-binned latency marts and their `<= 5 s` page budget (EP-55/56 call
  `to_mart`).
- The Plotly viewer page (EP-67), trajectory groups (EP-82) and exposure-response
  modelling (EP-86) - they consume the relations above.
- The long events spine (EP-50); the API may later read from it.
- Chartevents-scale vitals in the spine - parked (`final-roadmap.md` §8-10).
