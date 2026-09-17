# EP-50 — Events spine (MEDS-compatible) ⏱

**Size:** M · **Tier:** fixture+dev (full ⏱ → verified by EP-54) · **Core/Stretch:** core · **Depends on:** EP-19 (DAG runner `mwh build`), EP-34 (Time semantics + unit-of-analysis registry) · **Blocks:** EP-54 (Re-plan P3), EP-83 (Event-sequence / care-pathway analysis)

> **EP-33 amendment (2026-09-01).** **Depends-on changed** (carried P3C-8): now EP-19 and
> **EP-34** — the spine's `MEDS_BIRTH` synthetic-birth time, the `dod` horizon caveat on
> `MEDS_DEATH`, the diagnosis-at-`dischtime` rule and `AGE_CAP` cite `timesem` instead of
> re-deriving them; the roadmap README row is updated in the same session. EP-37 (spec
> discovery, derived layout) and EP-35 (`run.bench`) are prerequisites by the linear order
> (README "How to use": shared services). (1) **Spec discovery** (ledger P3C-2):
> `dag/specs/spine.yaml` is merged into the one graph by `dag.spec.load_dag()` (EP-37
> implements the merge; the shared `catalog` step is deduplicated with `depends_on` unioned),
> so `mwh build --tier <t> --tag spine` and `--select spine.<source>` work with no `--spec`;
> item 2's `union` step is a second `python` step in the same file. (2) **Step kind**: the
> shipped `python` kind — `callable: mimicwarehouse.spine:build_source`, called with `(step,
> ctx)`, returning `dag.runner.StepOutcome(rows=..., bytes_out=..., files=...)` — registered
> through `dag.runner.STEP_HANDLERS`; write on the build connection (`engine.open_duckdb(
> "build", ...)`), read sources via `loader.paths.read_parquet_sql`, publish each source
> directory with `publish.swap_dir` (`new_path_for`/`old_path_for`), manifests/snapshot ids via
> `dag.snapshot`, `run.bench` -> `BenchmarkLine(kind="mart", run_id=...)`. The layout
> `layout["lake_derived"]/<tier>/spine/source=<source>/subject_bucket=NN/` is the one bucketed
> exception to EP-37's single-file rule, registered by the `union` step. (3) **Size (EP-33 D3
> re-estimate; DESIGN §3 note)**: from staging's measured 13.8x CSV-to-Parquet compression the
> full spine is re-estimated at **2.5–4 GB** Parquet (not 5–8 GB); temp behaviour is bounded
> by the largest single-source sort (`labevents`), so the DESIGN §6 `memory_limit` stands.
> (4) **Vitals-subset question** (DESIGN §21): whether a curated `chartevents` vitals subset
> (EP-39's `curated=True` itemids only) joins the spine is decided at EP-54 with the measured
> full size in hand — v1 still excludes raw `chartevents`; record the D3-vs-actual delta in the
> completion note. (5) `validate` already refuses `text_value` > 64 chars; because
> `mimiciv_derived.spine` is a non-registry, subject-keyed read under `safe_query` (P3C-5),
> that same 64-char rule is what lets sessions aggregate over `code`/`text_value` (an
> exactly-64-char value is admitted — the heuristic refuses only strictly longer values).
> (6) `--tier dev --force` never replaces a full-complete spine source (EP-33 LDR-1); peek jobs
> with `mwh jobs --job spine-full --tail N`; refusals print on stderr via `console.fail`.

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

## Parked → final-roadmap.md

- Spine v2 event kinds the v1 grammar leaves out — microbiology antibiotic-susceptibility
  rows as their own code, `inputevents` / `procedureevents` end events, prescription and
  eMAR dose values, MEDS-style static demographic codes, `labevents.flag` — mirrored as
  `final-roadmap.md` § 8–10 SPINE-2 (the chartevents vitals subset stays SPINE-1, decided
  at EP-54).
- The loader's small-path partition files can carry a thread seam (a sorted
  `COPY … PARTITION_BY` is not order-preserving on DuckDB 1.5.5; found by this brief's
  dev validation, `docs/gotchas.md` §1) — mirrored as § Cross-cutting LOAD-5 for EP-54.

## Verification / acceptance

- `uv run poe test -m ep_50` green on fixture and dev; `uv run --group dev mwh verify EP-50` green.
- `uv run --group dev mwh sql "SELECT source_table, count(*) AS n FROM mimiciv_derived.spine GROUP BY 1
  ORDER BY 1"` works on dev; `spine.validate("dev")` passes and writes `meta.spine_validation`.
- Launched `mwh build --tier full --tag spine --background --job spine-full`; log at
  `%MWH_DATA_ROOT%\runs\jobs\spine-full.log`; job name/PID/start recorded here; timing, row counts
  and disk verified by EP-54.
- `docs/methods/spine.md` exists with the code grammar table.

> **Completion note (2026-09-17).** Executed as briefed on fixture + dev, with the full-tier
> job launched as briefed — and finished inside the session (2 min 43 s), so its numbers are
> recorded here for EP-54 to verify rather than re-measure. Shipped:
> `src/mimicwarehouse/spine.py` (the registry `SPINE_SOURCES` — one `SpineSource` per core
> table with its SQL projection template over `{<table>}` placeholders and the `CodeRule`
> rows the methods page renders; `select_sql(relations)`, `catalog_relations`,
> `bucket_relations`; `build_source` / `build_union` (the `python` step handlers),
> `validate` + `check_source` / `check_sources` / `check_file_schema` / `meds_schema`,
> `compute_codes` / `write_codes` / `write_validation`, `register_spine` (the
> `CATALOG_EXTENSIONS` entry, slotted after the cohort extension so the phenotype /
> units order pins hold), the `docs/methods/spine.md` renderer + `python -m
> mimicwarehouse.spine`, and `mwh spine sources | validate`), `dag/specs/spine.yaml`
> (13 `spine.<source>` steps with `target: spine.<source>`, `spine.union`, the shared
> `catalog` step tagged `spine`), `concepts/runner.py` (the discovery walker skips the
> bucketed `spine/` directory), `catalog/build.py` (the extension list), `cli.py` (one
> `add_typer` line), `tests/ep/test_ep50.py` (12 fixture + 1 dev tests),
> `docs/methods/spine.md` (new; the MEDS mapping and grammar tables generated),
> `docs/gotchas.md` §1 (the partitioned-`COPY` seam), DESIGN §10 note + §15 module map,
> README (State row, doc table, quick start, layout), roadmap Risk 18, the `final-roadmap.md`
> mirrors SPINE-2 / LOAD-5 / the TIME-1 note.
>
> **Interpretation choices.** (1) **Codes stay within 64 characters by construction.**
> Every `code` and `text_value` is bounded by `safe.FREE_TEXT_MAX_CHARS` (mirrored as
> `spine.CODE_MAX_CHARS`, asserted equal): the free-text-ish segments are cut (drug names
> to 46, eMAR medication to 34 and event text to 22 characters) and `validate` refuses a
> longer value — the brief's amendment (5) makes this the property that lets a session
> `GROUP BY code` through `mwh sql`, and it was exercised on dev and full. (2) **A missing
> segment is `NONE`** everywhere (the brief's `<org_itemid|NONE>` generalised), so every
> prefix has a fixed arity. (3) **`text_value` carries a dictionary string where the
> source has one the brief left unassigned:** `admission_location` on
> `HOSPITAL_ADMISSION`, `route` on `MEDICATION_START` / `STOP`, `amountuom` / `rateuom` /
> `valueuom` on the ICU inputs / outputs / procedures (the brief appends units to the
> `LAB` code only), `eventtype` on transfers and `interpretation` on micro as briefed,
> and `'age_capped'` on a `MEDS_BIRTH` whose `anchor_age` sits in the ≥ 89 bucket
> (`timesem.AGE_CAP`), because the synthetic birth time is then a bound, not a value.
> (4) **Micro time = `charttime`, else `chartdate`** (the contract: `chartdate` is always
> set, `charttime` only when a time is known) instead of dropping timeless specimens.
> (5) **Null-time rows are dropped**, except that the wrapper admits a timeless
> `MEDS_BIRTH` (MEDS' one static event) — a rule with no effect today, kept so `validate`'s
> "time non-null except `MEDS_BIRTH`" means what it says. (6) **`numeric_value` is
> `seq_num` on procedures too** (symmetry with diagnoses); dose fields, end times, the
> susceptibility columns and `labevents.flag` are parked (SPINE-2). (7) **One sorted
> single-file `COPY` per bucket** rather than the planned partitioned `COPY` in bucket
> chunks: the first dev-tier validation found 66 out-of-order rows in 65 files, a
> synthetic 3 M-row probe reproduced 2 seams per 5 partition files with
> `preserve_insertion_order` both on and off, and a single-file `COPY … ORDER BY` was
> sorted in both — so the spine copies the loader's pass-2 shape (the core read is
> Hive-pruned to the bucket, a source is still read once; `docs/gotchas.md` §1). The same
> probe run over the dev core files shows the seam in the loader's *small* path
> (`diagnoses_icd` 2, `outputevents` 1; the other small tables clean; pass 2 unaffected) —
> out of this brief's scope, recorded as Risk 18 / LOAD-5 for EP-54. (8) **The union step
> validates inside the build** (17 s on full) and records the `mimiciv_derived.spine`
> status entry only when validation passed; a failure fails the step and the catalog
> extension registers no view. (9) **`meta.spine_codes` is k-suppressed when built**
> (the EP-39 pattern, D-33 addendum): code prefix × source with `n_events` /
> `n_subjects`, both count columns through `disclose.suppress` (on the fixture the
> nested-pair rule blanks the two ICU subject counts; nothing is hidden on dev or full),
> raw counts under `lake/meta/<tier>/raw/`. (10) **`mwh spine sources | validate`** is a
> small CLI the brief did not name, added so `spine.validate("dev")` is an owner command
> (exit 1 on a failed check, `--no-write` for a dry read). (11) `register_spine` builds
> the view over every source complete for the tier at catalog-build time, gated by the
> union step's entry — `--select spine.<source>` then `--select spine.union,catalog` is
> the documented refresh path.
>
> **Runs.** fixture (session lake): **20,254 rows in 1,027 files** over 13 sources; the
> 13 source steps take 0.1–0.3 s each and the union (codes + validation) ≈ 2.4 s; the
> resume run skips every source and re-runs the union. dev: `mwh build --tier dev --tag
> spine` (after the `--force` rebuild with the per-bucket writer; build
> `20260917T162948-dev-aef1cfd`, run `20260917T162949Z-7d4957`) — **13,904,196 rows in
> 65 files, 43,004,507 bytes**, the 13 sources in 8.1 s (labevents 7,883,863 rows in
> 2.9 s), union 1.1 s, catalog 3.1 s; `mwh sql "SELECT source_table, count(*) AS n FROM
> mimiciv_derived.spine GROUP BY 1 ORDER BY 1" --tier dev` returns the 13 rows with 0
> suppressed, `meta.spine_codes` lists 18 prefix × source rows, `spine.validate("dev")`
> passes all seven checks. full: background job **`spine-full`** (pid 45636, launched
> 2026-09-17T16:32:32Z, finished 16:35:15Z, exit 0, log `runs/jobs/spine-full.log`;
> build `20260917T163233-full-aef1cfd`, run `20260917T163234Z-2a6060`):
>
> | source | rows | wall s | bytes |
> |---|---|---|---|
> | `patients` | 402,928 | 1.7 | 1,877,244 |
> | `admissions` | 1,092,056 | 2.0 | 10,554,855 |
> | `transfers` | 2,413,581 | 2.2 | 21,928,338 |
> | `icustays` | 188,902 | 1.5 | 2,327,955 |
> | `diagnoses_icd` | 6,364,488 | 5.4 | 32,270,764 |
> | `procedures_icd` | 859,655 | 1.5 | 7,770,667 |
> | `labevents` | 158,374,764 | 53.4 | 370,708,168 |
> | `microbiologyevents` | 3,988,224 | 3.2 | 16,724,482 |
> | `prescriptions` | 40,531,896 | 19.8 | 130,054,648 |
> | `emar` | 42,808,593 | 24.9 | 148,248,248 |
> | `inputevents` | 17,010,195 | 10.2 | 89,460,869 |
> | `outputevents` | 5,359,395 | 3.3 | 27,929,768 |
> | `procedureevents` | 808,706 | 1.4 | 7,433,033 |
> | `spine.union` (codes + validation) | 280,203,383 | 17.1 | 1,575 |
>
> Total **280,203,383 rows in 1,300 files, 867,290,614 bytes (0.81 GiB)** under
> `lake/derived/full/spine/`; peak RSS 825 MB (labevents), largest per-step free-space
> delta 557 MB (labevents; no spill directory growth reported), the catalog step 3.5 s;
> `mwh sql … GROUP BY source_table --tier full` and `SELECT … FROM
> meta.spine_validation --tier full` (13 sources, 1,300 files, ok = true) both answer
> through the gate. **D3-vs-actual (the brief's item 4):** the EP-33 re-estimate was
> 2.5–4 GB / ≈ 262 M rows; actual 0.81 GiB / 280 M rows — 3.1 bytes per row, not 10,
> because sorted low-cardinality codes and a float32 value compress far better than the
> planning assumption; the chartevents vitals-subset question (DESIGN §21) therefore has
> ample headroom and stays with EP-54.
>
> **Gates.** `uv run pytest -m ep_50` (fixture): 12 passed (≈ 90 s, the session lake
> included); `--tier dev -k dev_build`: 1 passed; `uv run mwh verify EP-50`: 12 passed;
> `mwh disclose check docs/methods/spine.md` exit 0 (no `--allow-text`); `uv run poe
> check` — ruff, `ruff format --check`, pyright 0 errors, the fixture suite **1,101
> passed**, 55 dev/full/demo probes deselected, 724 s (the session fixture lake now
> carries the 13 spine steps, ≈ 5 s); no earlier `test_ep*.py` edited; `mwh guard` clean
> over the changed files (pre-commit); `poe roadmap-check --strict` 0 errors.
>
> **Owner decisions at the interactive review (2026-09-17, every recommended option
> taken).** (1) Commit in the standard two steps, no push (the owner pushes). (2) The
> partitioned-`COPY` seams in the loader's small-path core files are **recorded for
> EP-54** (Risk 18, LOAD-5, the gotchas entry, a D-17 addendum) — rejected: an in-place
> per-bucket re-sort of the affected core tables this session, and switching the loader's
> small path to per-bucket `COPY`s plus a full restage (out of scope). (3) **Keep the
> code cuts** (drug 46 / eMAR medication 34 / event 22) so every code stays within the
> 64-character safe-query bound (a D-31 addendum) — rejected: full-length codes that the
> free-text heuristic would refuse as group keys, and one uniform cut. (4)
> `meta.spine_codes` **keeps both counts, k-suppressed at build** (a D-33 addendum) —
> rejected: `n_events` only, and deferring the shape to EP-54. (5) The grammar extras
> beyond the brief's literal mapping (`admission_location`, `route`, the ICU units, the
> `age_capped` flag, the micro `chartdate` fallback, `seq_num` on procedures) **stay as
> shipped** — rejected: stripping to the literal mapping, and dropping only the fallback.
> Routine choices logged above: `NONE` segments, null-time rows dropped, validation
> inside the union step, the `mwh spine` sub-app, the view over every complete source.
>
> **Handed on.** EP-54: verify the full numbers above from `runs.benchmarks` (`kind =
> mart`, build `20260917T163233-full-aef1cfd`) and `meta.spine_validation` on
> `full.duckdb`, record `lake/derived/full/spine/` (0.81 GiB) against Risk 6, decide the
> chartevents vitals subset (SPINE-1) with 3.1 bytes/row in hand, and pick the loader
> remedy for Risk 18 / LOAD-5; EP-83: `mimiciv_derived.spine` is the input (`code`
> prefixes are the pathway alphabet; `meta.spine_codes` the census); EP-49 / EP-55: the
> spine is a substitute event source for the presets' `events_sql()` shape; a MEDS export
> (MEDS-1) starts from the five MEDS columns of the view.
