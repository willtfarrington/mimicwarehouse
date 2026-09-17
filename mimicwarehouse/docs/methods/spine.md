# Events spine (EP-50)

The one written answer to "what is the long event table and what does a code mean?" -
the prose twin of `src/mimicwarehouse/spine.py` (DESIGN §10, §3 note). Care-pathway
analysis (EP-83) and, optionally, MEDS / ACES tooling need **one** long, sorted event
table instead of a dozen wide ones: `mimiciv_derived.spine` is that table, built from the
core lake by `mwh build --tier <t> --tag spine`, shaped after the MEDS 0.4 data schema so
external tooling is an optional validation lane rather than the build. Everything on this
page that is a table is rendered from the module's registry (`python -m mimicwarehouse.spine`
re-renders the marked blocks; `test_ep50` asserts they are in sync); nothing on it is
derived from data. All MIMIC-IV analyses in this repository are retrospective.

## 1. The shape

One row per event. The first five columns are the MEDS 0.4 core columns in the
standard's order and types (hard-coded in `spine.MEDS_COLUMNS`; the `meds` package is
**not** a dependency); the three that follow are the project's extras, which the standard
allows:

<!-- meds:begin -->
| column | standard | DuckDB type | pyarrow type | meaning |
|---|---|---|---|---|
| `subject_id` | MEDS 0.4 core | `BIGINT` | `int64` | patient identifier (`patients.subject_id`) |
| `time` | MEDS 0.4 core | `TIMESTAMP` | `timestamp[us]` | event time (shifted, naive); NULL only for static events |
| `code` | MEDS 0.4 core | `VARCHAR` | `string` | `PREFIX//segment//segment` (grammar below) |
| `numeric_value` | MEDS 0.4 core | `FLOAT` | `float` | measurement / amount / rate / sequence number, else NULL |
| `text_value` | MEDS 0.4 core | `VARCHAR` | `string` | a short dictionary-like string, else NULL |
| `hadm_id` | extra (allowed) | `INTEGER` | - | project extra: admission identifier when the event has one |
| `stay_id` | extra (allowed) | `INTEGER` | - | project extra: ICU stay identifier when the event has one |
| `source_table` | extra (allowed) | `VARCHAR` | - | project extra: the core table the event came from |
<!-- meds:end -->

Every file is sorted `(subject_id, time)` with a total tie-break (`code, hadm_id, stay_id,
numeric_value, text_value`), so a rebuild from the same lake is byte-identical and the
`sorted_within_file` check of §6 is well-defined. `time` is never NULL: a source row
without a timestamp is dropped (the one static event MEDS allows to be timeless,
`MEDS_BIRTH`, carries a synthetic time here).

## 2. The code grammar

A code is `PREFIX//segment//segment` (`spine.SEP` = `//`); a missing segment is `NONE`.
Prefixes are upper-case nouns of the event kind, segments are the dictionary value the
source carries (an `itemid`, an ICD code, a care unit, an admission type). Free-text-ish
segments are cut so every code fits the 64-character bound of §7: prescription drug
names to 46 characters, eMAR medication names to 34 and eMAR event text to 22.

<!-- grammar:begin -->
| source table | code | time column | numeric_value | text_value | caveat |
|---|---|---|---|---|---|
| `mimiciv_hosp.patients` | `MEDS_BIRTH` | `anchor_year - anchor_age, Jan 1 (synthetic birth)` | - | `'age_capped' when anchor_age >= 91` | placeholder, not a birth date; capped ages are a bucket |
| `mimiciv_hosp.patients` | `MEDS_DEATH` | `dod (day resolution)` | - | - | only where dod is recorded (~1-year horizon) |
| `mimiciv_hosp.admissions` | `HOSPITAL_ADMISSION//<admission_type>` | `admittime` | - | `admission_location` | - |
| `mimiciv_hosp.admissions` | `HOSPITAL_DISCHARGE//<discharge_location>` | `dischtime` | - | - | - |
| `mimiciv_hosp.transfers` | `TRANSFER_TO//<careunit>` | `intime` | - | `eventtype` | - |
| `mimiciv_icu.icustays` | `ICU_ADMISSION//<first_careunit>` | `intime` | - | - | - |
| `mimiciv_icu.icustays` | `ICU_DISCHARGE//<last_careunit>` | `outtime` | - | - | - |
| `mimiciv_hosp.diagnoses_icd` | `DIAGNOSIS//ICD<icd_version>//<icd_code>` | `admissions.dischtime` | `seq_num` | - | no timestamp upstream: placed at discharge |
| `mimiciv_hosp.procedures_icd` | `PROCEDURE//ICD<icd_version>//<icd_code>` | `chartdate (day resolution)` | `seq_num` | - | - |
| `mimiciv_hosp.labevents` | `LAB//<itemid>//<valueuom>` | `charttime` | `valuenum` | `value, only when valuenum is NULL and <= 32 chars` | - |
| `mimiciv_hosp.microbiologyevents` | `MICRO//<spec_itemid>//<org_itemid|NONE>` | `charttime, else chartdate` | - | `interpretation (S / I / R / P)` | one row per specimen x organism x antibiotic |
| `mimiciv_hosp.prescriptions` | `MEDICATION_START//<drug>` | `starttime` | - | `route` | drug cut to 46 chars |
| `mimiciv_hosp.prescriptions` | `MEDICATION_STOP//<drug>` | `stoptime` | - | `route` | - |
| `mimiciv_hosp.emar` | `EMAR//<medication>//<event_txt>` | `charttime` | - | - | medication cut to 34, event_txt to 22 chars |
| `mimiciv_icu.inputevents` | `INPUT//<itemid>` | `starttime` | `amount` | `amountuom` | - |
| `mimiciv_icu.inputevents` | `INPUT_RATE//<itemid>` | `starttime` | `rate` | `rateuom` | second row per input that carries a rate |
| `mimiciv_icu.outputevents` | `OUTPUT//<itemid>` | `charttime` | `value` | `valueuom` | - |
| `mimiciv_icu.procedureevents` | `ICU_PROCEDURE//<itemid>` | `starttime` | `value` | `valueuom` | - |
<!-- grammar:end -->

Two things the grammar deliberately does **not** do: it never puts a free-text column
into a code or a `text_value` (§3), and it keeps one code per (source row, event kind) -
an `inputevents` row with a rate yields two rows (`INPUT//…` for the amount,
`INPUT_RATE//…` for the rate), a prescription yields a start and a stop, an admission an
admission and a discharge.

## 3. What is excluded, and why

- **Raw `chartevents`** (bedside vitals, ~433 M rows) stays out of v1 for size; whether a
  curated vitals subset (the EP-39 `curated = true` itemids) joins the spine is decided at
  the P3 re-plan (EP-54) with the measured full size in hand (DESIGN §21;
  `roadmap/final-roadmap.md` SPINE-1).
- **Free text is never copied** (GOVERNANCE §4/§9): `labevents.comments`,
  `microbiologyevents.comments` and every contract `free_text` column are refused at
  build time - `spine.check_source` walks each projection's parsed statement
  (`json_serialize_sql`) and refuses any reference to a denied column name (`comments`,
  `comment`, `text`, `note`, `notes`) or to a column the contract flags `free_text`. The
  check runs before a source is written and again inside `validate` (§6).
- **Per source**, the columns the registry lists as `excluded` are the ones a MEDS reader
  might expect and should not look for here: `admissions.deathtime` / the ED times
  (in-hospital death is the `HOSPITAL_DISCHARGE//DIED` code; ED events arrive with
  EP-142), `labevents.flag` / reference ranges / `priority`, the microbiology antibiotic
  susceptibility columns (`ab_itemid`, `dilution_*`; the shipped row grain is specimen x
  organism x antibiotic, so an organism can appear on several rows with different
  interpretations), prescription dose fields, `inputevents.endtime` and the order
  bookkeeping columns. They are parked, not forgotten (`final-roadmap.md` § 8-10).
- **ED and Note events** join when their modules are staged (EP-142 / P10).

## 4. Time semantics

Every rule below is `timesem`'s (EP-34; [time-semantics.md](time-semantics.md)), never
re-derived here:

- `MEDS_BIRTH` is **synthetic**: January 1st of `anchor_year - anchor_age`. It exists so
  MEDS-style age arithmetic works within a subject; it is not a birth date. Where
  `anchor_age` sits in the `>= 89` bucket PhysioNet ships as 91 (`timesem.AGE_CAP`) the
  event carries `text_value = 'age_capped'`: the synthetic time is then a bound, not a value.
- `MEDS_DEATH` is `patients.dod`, which is populated only up to about one year after a
  patient's last discharge (`timesem.DOD_VISIBILITY_DAYS`); the absence of the event means
  "not observed within the horizon", never "alive".
- Billed diagnoses have no timestamp: they are placed at the admission's `dischtime`
  (the code keeps the ICD version, `numeric_value` the billing sequence number). A
  diagnosis is therefore a *retrospective* label of the admission, not an onset.
- `procedures_icd.chartdate` and `dod` are `DATE` columns: those events sit at midnight
  and carry day resolution (the TIME-1 parked item in `final-roadmap.md`); microbiology
  events use `charttime` when it is known and fall back to `chartdate` otherwise.
- All timestamps are the per-patient date-shifted naive `TIMESTAMP`s as shipped:
  intervals within a subject are real, calendar dates across subjects are not.

## 5. Build, layout, resume

`dag/specs/spine.yaml` declares thirteen `python` steps `spine.<source>` (handler
`mimicwarehouse.spine:build_source`), the `spine.union` step (`build_union`) and the
shared `catalog` step, all tagged `spine`, so:

```
uv run --group dev mwh build --tier dev --tag spine                       # everything, then the catalog
uv run --group dev mwh build --tier dev --select spine.labevents          # one source
uv run --group dev mwh build --tier dev --select spine.union,catalog      # re-summarise + register
uv run --group dev mwh build --tier full --tag spine --background --job spine-full   # the full tier (EP-19 job)
uv run --group dev mwh jobs --job spine-full --tail 20
```

Each source step reads its core table through the same `read_parquet` fragments the
catalog uses (the dev tier inherits the bucket filter), writes
`<lake_root(tier)>/derived/<tier>/spine/source=<source>/subject_bucket=NN/part-0.parquet`
as **one sorted single-file `COPY` per bucket** (ZSTD-3, ~1 M-row groups; the core read
is Hive-pruned to that bucket's directory, so a source is still read once and the sort
never holds more than one bucket) - the loader's pass-2 shape, chosen because a sorted
`COPY … PARTITION_BY` leaves thread seams inside its partition files on DuckDB 1.5.5
(`docs/gotchas.md` §1) - publishes through the rename-aside `publish.swap_dir`,
appends one manifest line per file (schema `spine`; `source_sha256` = the projection's
sha256, `raw_snapshot_id` = the core snapshot id), records the per-tier `status.json`
entry `spine.<source>` and one `kind: mart` benchmark line. The layout is the one
bucketed exception to EP-37's single-file derived rule: EP-37's discovery walker skips
the `spine/` directory and `spine.register_spine` (a `CATALOG_EXTENSIONS` entry)
registers the view instead. Resume follows the runner's rule: a source complete for the
tier is skipped unless `--force`; the union step carries no target and re-runs whenever
selected.

The union step reads every source complete for the tier and writes two `meta` tables
(registered by the walker from `lake/meta/<tier>/`):

- `meta.spine_codes` - one row per code prefix (the text before the first `//`) and
  source table with `n_events` / `n_subjects`. Because `meta.*` is a registry exemption
  under `safe_query`, the small-cell rule is applied when the table is **built**
  (`disclose.suppress`, complementary; `*_suppressed = true` marks a blank; the raw
  counts stay under `lake/meta/<tier>/raw/`, never walked into a catalog) - the EP-39
  precedent (D-33 addendum).
- `meta.spine_validation` - one row per validation run on the tier (§6).

It then records the `mimiciv_derived.spine` status entry; the next catalog build creates
the view over the complete sources, MEDS columns first. A failed validation records the
failure instead, fails the build step, and the view is **not** registered.

## 6. Validation

`spine.validate(tier)` (also `mwh spine validate --tier <t> [--json] [--no-write]`) reads
the Parquet directly and runs seven checks; every one must pass:

- `no_denied_columns` - the static projection walk of §3 over the registry;
- `meds_schema` - every file's first five columns are the MEDS core columns with the MEDS
  pyarrow types (`int64`, `timestamp[us]`, `string`, `float` (32-bit), `string`), followed
  by the three extras;
- `time_not_null` - no NULL `time` except on `MEDS_BIRTH` rows;
- `subject_in_patients` - every `subject_id` exists in the tier's `patients`;
- `text_value_bounded` / `code_bounded` - no value over 64 characters, no newline;
- `sorted_within_file` - `(subject_id, time)` non-decreasing in every file.

The union step runs the same function on the build connection and appends the
`meta.spine_validation` row (`tier`, `validated_at`, `build_id`, `run_id`, counts, `ok`,
`failed_checks`).

## 7. Reading it

`mimiciv_derived.spine` is a subject-keyed view, so a session reads it only as aggregates
through `mwh sql` / `safe_query` (GOVERNANCE §4): every statement needs a count column,
identifiers stay inside count calls, rows below k = 11 are suppressed. Because every
`code` and `text_value` is at most 64 characters and never contains a newline, the
free-text heuristic admits them as group keys - which is the point of the cuts in §2:

```
uv run --group dev mwh sql "SELECT source_table, count(*) AS n FROM mimiciv_derived.spine GROUP BY 1 ORDER BY 1" --tier dev
uv run --group dev mwh sql "SELECT code, count(*) AS n, count(DISTINCT subject_id) AS n_subjects FROM mimiciv_derived.spine WHERE code LIKE 'LAB//50912//%' GROUP BY 1 ORDER BY 2 DESC" --tier dev
uv run --group dev mwh sql "SELECT text_value, count(*) AS n FROM mimiciv_derived.spine WHERE source_table = 'microbiologyevents' GROUP BY 1 ORDER BY 2 DESC" --tier dev
uv run --group dev mwh sql "SELECT code_prefix, source_table, n_events, n_subjects FROM meta.spine_codes ORDER BY 2, 1" --tier dev
```

In Python the same statements go through `safe.safe_query(...)`; the timeline API's event
presets (`timeline.EventSource.events_sql()`, EP-49) are the normalised shape a spine
reader can substitute once EP-83 needs it.

## 8. Sizes

The synthetic fixture spine holds 20,254 events in 1,027 files across the thirteen
sources and builds in a few seconds; the dev and full sizes are recorded in the EP-50
completion note (`roadmap/EP-50-events-spine.md`) and verified by EP-54, against the
EP-33 re-estimate of 2.5-4 GB of Parquet for the full tier (DESIGN §3 note).
