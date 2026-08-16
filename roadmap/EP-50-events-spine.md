# EP-50 — Events spine (MEDS-compatible) ⏱

**Size:** M · **Tier:** fixture+dev (full ⏱ → verified by EP-54) · **Core/Stretch:** core · **Depends on:** EP-19 (DAG runner `mwh build`) · **Blocks:** EP-54 (Re-plan P3), EP-83 (Event-sequence / care-pathway analysis)

## Context

Care-pathway analysis (EP-83) and, optionally, MEDS/ACES tooling need one long event table
instead of a dozen wide ones. DESIGN §10 fixes it: `(subject_id, hadm_id, stay_id, time, code,
numeric_value, text_value, source_table)` under `lake/derived/spine/`, covering admissions,
transfers, diagnoses, procedures, labs, microbiology, prescriptions/emar, ICU inputs/outputs/
procedures — **excluding raw `chartevents`** in v1 (size) — with a column set matching MEDS 0.4
(`subject_id` int64, `time` timestamp µs, `code` string, `numeric_value` float32, `text_value`
string; extra columns allowed) so external tooling is an optional validation lane, not the build.
This brief builds `src/mimicwarehouse/spine.py` + a DAG spec (`mwh build --tag spine`) through
the EP-19 runner (D-20), using the per-tier derived layout from EP-37 (`lake/derived/<tier>/spine/`).
Governance: `text_value` may only carry dictionary-like short strings (lab `value` flags,
`emar.event_txt`, careunits, ICD codes are in `code`); free-text columns (`comments`, note text)
are never copied (GOVERNANCE §4). Caveats: diagnoses have no timestamp (assign `dischtime`,
`seq_num` kept in `code`), `dod` is a `MEDS_DEATH` event with the ~1-year horizon caveat, times are
shifted per patient (fine within-subject). Full size ≈ 250 M rows / 5–8 GB Parquet; the full build
is a logged background job verified by EP-54; expected 15–45 min with `memory_limit` 36–40 GB,
`threads` 12, ≥ 100 GB free (DESIGN §3, §6).

## In scope

1. **Code conventions** (`src/mimicwarehouse/spine.py`, `SPINE_SOURCES` registry) — one
   `SpineSource` per source with its SQL projection: `patients` → `MEDS_BIRTH` (time =
   `anchor_year − anchor_age` Jan 1, documented as synthetic-birth) and `MEDS_DEATH` (`dod`);
   `admissions` → `HOSPITAL_ADMISSION//<admission_type>` at `admittime`,
   `HOSPITAL_DISCHARGE//<discharge_location>` at `dischtime`; `transfers` →
   `TRANSFER_TO//<careunit>` (`eventtype` in `text_value`); `icustays` →
   `ICU_ADMISSION//<first_careunit>` / `ICU_DISCHARGE//<last_careunit>`; `diagnoses_icd` →
   `DIAGNOSIS//ICD<version>//<code>` at `dischtime` (numeric_value = `seq_num`);
   `procedures_icd` → `PROCEDURE//ICD<version>//<code>` at `chartdate`; `labevents` →
   `LAB//<itemid>` (`numeric_value = valuenum`, `text_value = value` only when non-numeric and
   ≤ 32 chars, `valueuom` appended to code); `microbiologyevents` →
   `MICRO//<spec_itemid>//<org_itemid|NONE>` at `charttime` (interpretation → `text_value`;
   `comments` excluded); `prescriptions` → `MEDICATION_START//<drug>` (`starttime`) and
   `MEDICATION_STOP//<drug>`; `emar` → `EMAR//<medication>//<event_txt>` at `charttime`;
   `inputevents` → `INPUT//<itemid>` at `starttime` (`amount` numeric; `rate` as a second row
   `INPUT_RATE//<itemid>`); `outputevents` → `OUTPUT//<itemid>`; `procedureevents` →
   `ICU_PROCEDURE//<itemid>` at `starttime`. Documented in `docs/methods/spine.md` (new).
2. **DAG + layout** — spec `src/mimicwarehouse/dag/specs/spine.yaml`: one `python` step
   `spine.<source>` per source (callable `mimicwarehouse.spine:build_source`, tag `spine`) writing
   `lake/derived/<tier>/spine/source=<source>/subject_bucket=NN/*.parquet` (sorted `(subject_id,
   time)`, ZSTD-3, ~1 M-row groups; demo under `lake/derived/demo/spine/`), then a `union` step
   registering `mimiciv_derived.spine` (view over all sources, MEDS column order first) and
   `meta.spine_codes` (code prefix, source_table, n_events, n_subjects — aggregate). Resumable
   per source (`status.json`); subsets via `--select spine.<source>`; runner manifests + snapshot
   id per source; `run.bench(kind="mart")` per source.
3. **MEDS conformance + governance checks** — `spine.validate(tier)`: expected pyarrow schema
   (hard-coded MEDS 0.4 core columns/types; the `meds` package is **not** a dependency), `time`
   non-null except `MEDS_BIRTH` rows, `subject_id` present in `patients`, no `text_value` longer
   than 64 chars, no `text_value` from a denied column list (`comments`, `text`, `note`), monotone
   sort within files; a `meta.spine_validation` row per tier/run.
4. **Full-tier launch (⏱)** — from `mimicwarehouse/`: `uv run --group dev mwh build --tier full
   --tag spine --background --job spine-full` (EP-19 job runner; state `runs/jobs/spine-full.json`,
   log `runs/jobs/spine-full.log`; `mwh jobs --job spine-full --tail 20` to peek); record job name,
   PID, start time and log path in the completion note; do not wait — EP-54 verifies row counts per
   source, disk and timing.
5. **Tests + docs** (`tests/ep/test_ep50.py`, `@pytest.mark.ep_50`; fixture, `dev`) — every
   source projection compiles on the fixture catalog; a crafted synthetic admission produces the
   expected event set in order; MEDS schema check passes; a projection that (deliberately, in the
   test) selects `microbiologyevents.comments` is refused by `validate`; per-source row counts on
   fixture equal the source table filters; on dev, `mwh build --tier dev --tag spine` completes,
   `mimiciv_derived.spine` is queryable and `meta.spine_codes` lists every source. `docs/methods/
   spine.md`: code grammar, exclusions (chartevents; free text), MEDS mapping table, sizes.

## Out of scope

- Care-pathway mining over the spine → EP-83. Timeline API → EP-49 (independent).
- MEDS export / ACES lane / meds-tab baselines; chartevents (vitals) subset in the spine → parked
  (`final-roadmap.md` § 8–10 and § 34–35).
- ED and Note events → EP-142 / P10 (add sources then).

## Verification / acceptance

- `uv run poe test -m ep_50` green on fixture and dev; `uv run --group dev mwh verify EP-50` green.
- `uv run --group dev mwh sql "SELECT source_table, count(*) AS n FROM mimiciv_derived.spine GROUP BY 1
  ORDER BY 1"` works on dev; `spine.validate("dev")` passes and writes `meta.spine_validation`.
- Launched `mwh build --tier full --tag spine --background --job spine-full`; log at
  `%MWH_DATA_ROOT%\runs\jobs\spine-full.log`; job name/PID/start recorded here; timing, row counts
  and disk verified by EP-54.
- `docs/methods/spine.md` exists with the code grammar table.
