# P3 retrospective — concepts, QC, cohort engine, provenance, protocol freeze (EP-54)

Written at EP-54, the P3 re-plan (the brief's EP-33 amendment block is the charter; the
retro convention is `retro-p2.md`'s). P3 ran 2026-09-05 → 2026-09-17 as twenty-one briefs —
EP-34 … EP-45, EP-172 (allocated mid-phase by the owner), EP-46 … EP-53 — and built the
derived layer over the core lake: time semantics and the unit-of-analysis registry, the run
ledger with seeds and resources, the 65 vendored mimic-code concepts with a patch registry,
the curated item catalogue and unit harmonisation, code sets with the GEM utility, four
phenotypes, the disclosure primitives, QC profiling and measurement-process summaries, the
cohort spec / compiler / attrition diagram, the event-aligned timeline API, the MEDS-shaped
events spine, the protocol freeze, the backup of non-reproducible state and the first
capstone. This file is the phase record: pre-flight, the ⏱ verification, planned-vs-actual,
what dragged, the full-tier timings, the lake/temp measurements that replace the EP-33 D3
estimates, the toolchain and connector re-checks, the carried-low re-triage, the decisions
routed to this re-plan and — appended at the end of the session — the checkpoint minutes and
the commit series. Integers are thousands-separated (`inventory.fmt_int` style, guard G4).

## Pre-flight (recorded before any EP-54 edit)

Session start 2026-09-17 (evening, local); `mwh doctor` **10 pass · 1 warn · 0 fail · 5 info**
over 16 checks (the warn is `antivirus`, by design — D-38/D-42; `last_backup` reads the EP-52
backup, 0.3 days old); AC power mode **Best performance**; disk **380.4 / 951.5 GB free**;
DuckDB 1.5.5 == pin; working tree clean at `63b1e90` (EP-53's hash commit).

| Probe | Result |
|---|---|
| `uv run poe roadmap-check --strict` | OK — 173 rows, 173 briefs, **63 done**, 0 errors, 0 warnings |
| `mwh jobs` | 29 jobs, all `done`, exit 0 (the P3 jobs listed in § Full-tier timings) |
| `mwh verify EP-k`, k ∈ {34 … 45, 172, 46 … 53} (21 briefs, fresh interpreter each, concurrent with the gate queries below) | recorded in § Gates at the end of the session |
| `uv lock --check --offline` | exit 0 (101 packages; the lock is current) |
| connector roster (GOVERNANCE §4 re-check) | § Connector roster re-check |

## The ⏱ job — EP-50's full spine, verified

`mwh jobs --job spine-full --tail 40`: state `done`, pid 45636, exit 0, launched
2026-09-17T16:32:32Z, finished 16:35:15Z — build `20260917T163233-full-aef1cfd` ok in 161.4 s,
run `20260917T163234Z-2a6060` (wall 157.4 s, CPU 320.6 s, peak RSS 825.1 MB, disk delta
+1,104.2 MB). The gate query the brief names returns the thirteen sources row for row (0 rows
suppressed; audit `41e6417b…`), `mwh spine validate --tier full --no-write` passes all seven
checks over 13 sources / 1,300 files / 280,203,383 rows, `meta.spine_validation` carries the
EP-50 row (ok = true) and `meta.spine_codes` its 18 prefix × source rows with nothing
suppressed. The benchmark ledger (`mwh runs benchmarks --tier full --kind mart --format md`)
carries a `kind: mart` line for every source and for `spine.union`:

| source | rows | wall s | Parquet bytes |
|---|---:|---:|---:|
| `patients` | 402,928 | 1.7 | 1,877,244 |
| `admissions` | 1,092,056 | 2.0 | 10,554,855 |
| `transfers` | 2,413,581 | 2.2 | 21,928,338 |
| `icustays` | 188,902 | 1.5 | 2,327,955 |
| `diagnoses_icd` | 6,364,488 | 5.4 | 32,270,764 |
| `procedures_icd` | 859,655 | 1.5 | 7,770,667 |
| `labevents` | 158,374,764 | 53.4 | 370,708,168 |
| `microbiologyevents` | 3,988,224 | 3.2 | 16,724,482 |
| `prescriptions` | 40,531,896 | 19.8 | 130,054,648 |
| `emar` | 42,808,593 | 24.9 | 148,248,248 |
| `inputevents` | 17,010,195 | 10.2 | 89,460,869 |
| `outputevents` | 5,359,395 | 3.3 | 27,929,768 |
| `procedureevents` | 808,706 | 1.4 | 7,433,033 |
| `spine.union` (codes + validation) | 280,203,383 | 17.1 | 1,575 |
| **total** | **280,203,383** | 148.4 | **867,290,614** (0.81 GiB, 1,300 files) |

Nothing was relaunched or re-measured; the completion note appended to `EP-50-events-spine.md`
carries the same facts. **D3 vs actual:** the EP-33 re-estimate was 2.5–4 GB for ≈ 262 M rows;
actual 0.81 GiB for 280 M rows — 3.1 bytes per row, not 10 (sorted low-cardinality codes and a
float32 value compress far better than the planning assumption). The chartevents vitals
subset (SPINE-1) was sized with one aggregate this session: the 63 curated itemids (EP-39)
account for **65,929,526** of the 432,997,491 `chartevents` rows on full (vitals 55.7 M, GCS
6.6 M, glucose 1.8 M, FiO2 1.1 M, weight/height 0.6 M), i.e. **≈ 0.2 GB and well under a
minute** as a fourteenth `spine.chartevents_vitals` source at the spine's bytes-per-row — the
decision is in § Checkpoint minutes.

## Planned vs actual

Actuals from completion notes where a phrase exists ("note"), else the gap from the previous
☑-hash commit to the brief's `feat` commit on the same day ("gap" — it includes the owner's
interactive review round and the two-step commit, so it reads high against P2's "one session"
notes; a gap across a night is not a measure). Sizes: S ≈ 30 min, M ≈ 1 h (D-2).

| EP | Planned | Actual | What bit |
|---|---|---|---|
| EP-34 | M | one session (note; first commit of the day) | the first `mimiciv_derived` views narrowed `test_ep29`'s schema pin; flag / anchor naming calls; the session ran unattended at *Better performance* |
| EP-35 | M | ≈ 1 h 15 (gap) | `ATTACH IF NOT EXISTS` served a stale `runs.duckdb` → detach-before-attach engine helper; typed ledger views |
| EP-36 | S | ≈ 1 h (gap, redo) + ≈ 30 min reverted | `peak_wset` is a lifetime mark → sampled-maximum rule; DuckDB `REPEATABLE` parses an int32 seed |
| EP-37 | M | ≈ 2 h 50 (gap, redo; includes the 11 min 54 s full job and the step-0 data-root cleanup) + ≈ 1 h 15 reverted | ledger `read` typed a null `error` column; `test_ep20`'s catalog pin; the first attempt's "runner skips every concept" hazard |
| EP-38 | M | one session (note; redo; gap crosses the night) + ≈ 1 h reverted | the brief's five-panel `valueuom` filter was one upstream PR over two concepts; `inflammation` lost 56 rows |
| EP-39 | M | ≈ 2 h 05 (gap) | DECIMAL × DECIMAL overflow inside the macro; `offset` is reserved → a second dev build |
| EP-40 | M | ≈ 2 h 35 (gap) | GEM fetch + a 67 s `executemany`; Charlson's ICD-10 arm at 87.2 % by construction; PyYAML octal codes |
| EP-41 | M | ≈ 1 h 20 (gap) | the `mwh_harmonize` macro scan took 43 s over 9,619 rows → conversions inlined; fixture 0.3.0 regeneration folded in |
| EP-42 | M | ≈ 1 h 20 (gap) | snapshot-history stamping (`record_snapshot_once`) found by `poe check`; the D-34 heading repaired |
| EP-43 | M | one session ≈ M (note) | a prose-rule bug (`n = 3,588.`); a seventh code `OVERSIZE`; the hook swap changed four earlier tests' assertions — the leak they had pinned |
| EP-44 | M | one session (note) | "15–45 min" full estimate → a 2 min job; `bytes_parquet` tripped `ID_BAND` → `parquet_bytes`; `ts_order` bounds override |
| EP-45 | M | one session (note) | "5–20 min" → seconds; gate-forced renames (`_share`, `caveat`); the `measurement` tag overlap; Risk 17 |
| EP-172 | M | ≈ 35 min (gap) | 34 owner rulings; nothing engineering-wise |
| EP-46 | M | ≈ 1 h 05 (gap) | interpretation calls only (hospice via `discharge_location`, the tracer seed without `complete`) |
| EP-47 | M | ≈ 1 h 45 (gap) | the `marts` tag; EP-31 reconciled count for count; attrition suppressed in the run manifest |
| EP-48 | S | ≈ 2 h 15 (gap) — ≈ 4× the S budget | two `disclose.check` gate amendments (no layered Vega-Lite spec could pass; the `default` header); Altair 6 / vl-convert realities; the first data-root fixture build (144 steps) |
| EP-49 | M | one session ≈ M (note; gap crosses the night) | DECIMAL bin bounds reached Altair as `Decimal`; `n_events` withheld beside `n_units` |
| EP-50 | M | ≈ 1 h 30 (gap) | partitioned-`COPY` seams → the per-bucket writer and a `--force` dev rebuild; the 64-char code cuts |
| EP-51 | M | ≈ 1 h 15 (gap) | the policy hook refused the qualified claim labels inside the session lake (caught by the suite) |
| EP-52 | S | ≈ 1 h 10 (gap) — ≈ 2.4× the S budget | the 16th doctor check touched three earlier tests; the BitLocker / D-29 target refusals; the restore drill |
| EP-53 | M | one session ≈ M (note; a 5 h 53 commit gap is not a session measure) | promoted CSVs are uncommittable by design → Markdown tables (owner decision); `test_ep19`'s catalog-last pin; the 57-row derived sum (reconciled below) |

Phase total: **≈ 29 h** (gap basis for the fourteen measurable briefs, ≈ 22 h against their
12.5 h planned; note basis ≈ M for the other seven) **plus ≈ 2 h 45 min of reverted first
attempts** (EP-36/37/38, `798141c`), against **≈ 19.5 h planned** (3 S + 18 M) — P3 ran at
≈ 1.5× budget on the gap basis, the first phase over. Three readings: (1) the gap includes
the per-brief owner review round (four to five questions, every recommended option taken in
P3) and the two-step commit — a fixed ≈ 15–20 min the S/M sizing never budgeted; (2) the two
S overruns (EP-48, EP-52) are the briefs that touched the disclosure gate and the doctor —
anything that edits a governance or diagnostic surface should be sized M; (3) the revert cost
one afternoon and bought three cleaner modules — the owner's call, recorded as such.

## What dragged / surprised

- **The revert.** `798141c` returned the tree to the EP-35 state and EP-36/37/38 were redone
  from their brief text with no code reuse ("the effort level had been set wrong"); EP-37's
  step 0 was an owner-approved data-root cleanup of the first attempt's derived files, status
  and snapshot entries. Every later brief kept the redo's shape (provenance run per build,
  per-tier derived layout, patch registry).
- **DuckDB 1.5.x concept breakages: none.** All 65 vendored `concepts_duckdb` files executed on
  fixture, demo, dev and full with no local edit (65 / 65; the EP-33 D2 smoke predicted it).
  The "fixes" are five ports of open upstream PRs as full-replacement patches — none was a
  1.5.x fix.
- **Count-pin drift: none.** Demo pins matched 65 / 65 before and after the patches; the dev
  pins moved only on `inflammation` (fewer than 11 specimens) and `complete_blood_count`
  (MCHC values nulled, rows kept). **The 57-row hand-off is reconciled:** EP-38's completion
  note total (95,777,751 rows) is EP-37's pre-patch build; the patched rebuild dropped 56 CRP
  rows without a unit from `inflammation` (174,269 → 174,213) and one specimen from
  `complete_blood_count`, so `meta.concept_versions` and the ledger both read **95,777,694**
  — the derived snapshot id moved accordingly (`a681ed30…` → `c04b1bff…` → `61c31299…` once
  the phenotypes stamped it). No drift, one recorded patch effect.
- **The disclosure gate shaped names and shapes** more than any engine fact: `bytes_parquet`
  → `parquet_bytes` (`ID_BAND`), `share_measured_first_24h` → `measured_first_24h_share` and
  `note` → `caveat` (EP-45), `concepts` → `n_*` headers (EP-53), drops rendered as text cells
  (EP-48), `n_events` withheld beside `n_units` (EP-49), codes cut to 64 characters (EP-50).
  EP-48's request stands as a retro note: the checker's nested-totals heuristic pairs *any*
  two integer columns of a Markdown table, which is why attrition drops are text cells; a
  column-typed exemption (declare which columns are totals) is a candidate `disclose`
  refinement for EP-59/EP-130, not a governance change.
- **Engine lessons went into `docs/gotchas.md` §1 as they happened**: DECIMAL literal
  overflow in macros, `offset` reserved, macro expansion per call (43 s vs 0.13 s inlined),
  `executemany` throughput, `REPEATABLE` int32 seeds, DECIMAL bin bounds vs Altair, the
  partitioned-`COPY` seam, the `measurement` tag overlap, PyYAML octal codes.
- **Full-tier estimates were over-estimates in every case**: concepts 30–120 min → 11 min 54 s
  (of which one concept, `suspicion_of_infection`, is 442.8 s); QC 15–45 min → 2 min;
  measurement 5–20 min → seconds; spine 15–45 min → 2 min 43 s. Every ⏱ job finished inside
  its launching session; "verified by the next EP" stayed record-keeping (the P2 finding).
- **The fixture suite is the emerging cost.** `poe check` grew 853 → 1,183 tests and 297 s →
  745 s (12 min 18 s at EP-52) because the session fixture lake now runs the whole DAG (144
  steps, ≈ 2 min) and several modules build their own lakes. Not a wheel fight (no toolchain
  slot), but EP-74 should look at `poe test-fast` (xdist) or a shared session lake before P5
  doubles the suite again.
- **Thirteen of twenty-one briefs edited an earlier `test_ep*.py`** under the churn rule
  (count pins, order pins, the hook swap) — each recorded in its completion note; every
  earlier `mwh verify EP-k` still exits 0 (§ Gates).
- **Every brief closed with an interactive owner review** (the EP-34 pattern), four to five
  questions each; every recommended option was taken. The reviews are where the D-33 addenda
  come from — nine of them in P3.

## Full-tier timings (background jobs and provenance runs; `mwh runs show`, `mwh runs benchmarks`)

| job (EP) | run | wall | peak RSS MB | disk Δ MB | what |
|---|---|---:|---:|---:|---|
| `concepts-full` (EP-37) | `20260906T003510Z-9564c0` | 707.0 s run · 11 min 54 s job | 7,479.6 (`rhythm`) | +1,427.4 | 65 concepts, 95,777,751 rows, 1,337,952,596 bytes |
| `concepts-full-patched` (EP-38) | `20260906T154530Z-fca779` | 25.0 s | 7,081.3 (`sofa`) | −0.3 | 12 concepts rebuilt (5 patched + 7 readers), 14,016,426 rows; layer 1,336,919,249 bytes after |
| `meta-full` (EP-39/40/41, owner-launched) | `20260906T230902Z-cb954b` | 79.9 s run · 87 s job | 318.4 | +6.8 | `units.*` ×3, `codesets.*` ×2 (GEM 281,071 rows), `t2dm@1.0.0`, catalog |
| `phenotypes-full` (EP-42) | `20260907T001149Z-3df8ec` | 10.6 s job | — | — | `sepsis3`, `kdigo_aki`, `sepsis_explicit@1.0.0` — 734,944 rows |
| `qc-full` (EP-44) | `20260915T010618Z-8c5bb8` | 106.3 s run · 99.0 s step wall | 15,935.2 (`chartevents`) | −62.4 | 886,043,036 rows profiled; 574 checks: 491 pass / 83 warn / 0 fail |
| `measurement-full` (EP-45) | `20260916T194120Z-a8ee17` | 36.5 s run · 42 s job | 8,067.5 (`structural`) | +18.9 | 94,444 stays, six `meta.mp_*` tables |
| `codesets-full` (EP-172) | `20260916T203019Z-2e0d09` | 62.2 s run · 68 s job | 310.4 | +3.4 | GEM re-read 56.3 s; 5,508 member rows |
| `phenotypes-full-1-1` (EP-172) | `20260916T203429Z-836490` | 10 s job | — | — | `sepsis_explicit@1.1.0`, 546,028 rows |
| `ep47-cohort-full` / `ep47-hf-full` (EP-47) | `20260916T224746Z-ce6b7d` / `20260916T224902Z-855aca` | 0.43 s / 0.67 s materialisation · 10 s jobs | 243 / 355 | — | 65,090 / 42,497 units |
| `ep49-timeline-bench` (EP-49) | `20260917T151923Z-c25231` | 0.8 s query · 1.7 s run | 326 | +0.3 | 240 hourly bins, all released |
| `spine-full` (EP-50) | `20260917T163234Z-2a6060` | 157.4 s run · 2 min 43 s job | 825.1 (`labevents`) | +1,104.2 | 280,203,383 rows, 867,290,614 bytes, 1,300 files |
| `ep53-capstone` (EP-53) | build `20260917T213454Z-ee1995` → report `20260917T213458Z-3f1556` | 4.1 s + 4.0 s | 335 | +0.3 | eleven tables, two figures, 14 audited statements |
| `tracer-full-ep53` (EP-53) | `20260917T213345Z-4376d1` | 3.5 s | — | — | n = 65,366, AUC 0.731 — EP-31 reproduced count for count |

The five slowest concepts on full: `suspicion_of_infection` 442.8 s (1,107 MB), `rhythm`
159.7 s (7,479 MB), `sofa` 19.3 s (12.7 s after the APS III / SIRS patches; 7,177 MB),
`charlson` 13.3 s, `vitalsign` 10.2 s (3,702 MB). Per-group table: EP-37's completion note.

## Lake & temp vs the DESIGN §3 budget (the D3 estimates replaced)

| Layer (full tier) | EP-33 D3 re-estimate | Measured at EP-54 |
|---|---|---|
| derived concepts (`lake/derived/full/mimiciv_derived/`) | ≈ 90 M rows, 0.8–1.5 GB | **95,777,694 rows, 1,336,919,249 bytes (1.25 GiB), 65 files** |
| phenotypes (`lake/derived/full/phenotypes/`) | negligible | 5 files (four ids, two versions of one), 1,645,599 rows, ≈ 10 MB |
| events spine (`lake/derived/full/spine/`) | ≈ 262 M rows, 2.5–4 GB | **280,203,383 rows, 867,290,614 bytes (0.81 GiB), 1,300 files** |
| marts (`lake/marts/full/cohorts/`) | ≤ 1–2 GB incl. EP-55/56 | two cohorts, 107,587 rows, < 1 MB (the latency marts are EP-55/56's) |
| registry tables (`lake/meta/full/`) | — | 22 `meta.*` tables; the full catalog file is 21,245,952 bytes |
| **derived + spine** | **4–6 GB** | **≈ 2.2 GB** |
| build-temp spill | ≤ 20–40 GB worst case | **none observed**: the run disk deltas are ≤ the bytes written plus 0.24 GB (spine: catalog rebuild included), qc-full's is negative; the EP-19 runner does not sample `tmp_duckdb` (the loader's pass-1 heartbeat did), so the deltas are the bound |
| peak RSS | — | 15,935 MB (`qc.profile.mimiciv_icu.chartevents`, a natural-key `GROUP BY` over 433 M rows), 8,068 MB (measurement structural), 7,480 MB (concept `rhythm`), 825 MB (spine) — all under the 36 GB build `memory_limit` |
| free space after P3 | ≈ 380 GB | **380.4 GB** |

## Toolchain remediation slot (P4)

**Not needed — recommendation recorded for the owner (§ Checkpoint minutes).** The only
named candidate, Streamlit's `pyarrow<25` pin, is a settled lock: `streamlit 1.61.1`,
`vegafusion 2.0.3` and one `pyarrow 24.0.0` for core and `ui`, `vl-convert-python 1.9.0.post1`
in core since EP-48 (FC-9 settled), `altair 6.2.2`; `uv lock --check --offline` exits 0; the
`ui` ↔ `gpu` / `text` conflict sets are declared for the day they diverge. No sdist beyond the
accepted `autograd-gamma`. The one toolchain-shaped cost P3 surfaced is the fixture suite's
wall time (above) — an EP-74 topic, not a wheel fight.

## Connector roster re-check (GOVERNANCE §4, D-43 item 3)

The roster is claude.ai account state and has grown since 2026-08-18. Read-only lookups
(PubMed, bioRxiv, ICD-10 Codes, Context7, Clinical Trials, CMS Coverage, Consensus, Microsoft
Learn, Hugging Face repo search, Wispr Flow, Twilio search) need no change; Google Drive is
still unauthorised (off), as GOVERNANCE requires. **New write-capable tools not covered by the
thirteen denies in `.claude/settings.json`:** Claude Docs `create` / `batch` / `update` /
`delete` / `export` (hosted documents — a second egress path for free text), Excalidraw
`create_view` / `save_checkpoint` / `export_to_excalidraw`, the alphaXiv folder / metadata
writes, Gmail `create_label` / `update_label`. The session wrote no content through any of
them. Per D-45 item 4 (the B7 precedent) the deny additions are an owner-applied package in
the session scratchpad (`ep54-connector-denies.md`); the owner's decision is in
§ Checkpoint minutes. Next re-check: EP-74.

## Carried-low re-triage (`final-roadmap.md` AUDIT-1; the EP-33 amendment's item 3)

The 60 carried-low findings of `retro-p2-findings.md` had no verifier pass at EP-33; the XS
doc / dead-code items were swept where Workstreams B/C touched the file and the rest parked
as AUDIT-1 for this re-plan. Re-examined against the code at `63b1e90` (P3 built on all of
it). The verified `next-replan` rows close as the amendment expected: **CTR-1** (NULLS LAST;
writer and checker aligned at 0.3.0), **DAG-3 / DAG-4** (lock `create_time`; the `O_APPEND`
admission), **SGT-3**'s arithmetic half is DIS-3, the **P3C-\*** items landed through the D1
amendments (P3C-2 spec discovery, P3C-3 the EP-49 gate, P3C-4 `marts.cohorts`, P3C-5 the
derived-surface rule, P3C-6 the ledger names, P3C-8 the headers).

| Finding | Status at EP-54 | Evidence (anchor) | Outcome |
|---|---|---|---|
| DKB-3, DKB-4, SGT-6 | fixed (EP-33 B1) | `safe.py` — the catalog statements and the `runs` attach sit inside the audited `try`; the interrupt timer is suppressed while closing | **close** |
| P3C-9 | resolved (EP-43) | `safe.SUPPRESSOR = disclose.safe_suppressor`; `safe` owns the seam, `disclose` the policy | **close** |
| P3C-10, P3C-11 | obsolete | EP-52 uses the global `--data-root`; `test_ep44` copies the lake, not a catalog | **close** |
| P3C-7 | specified (EP-37) | `concepts.runner.register_derived`: two levels under `derived/<tier>/`, `part-0.parquet` only, `status.json` gate, `spine/` and `.new`/`.old` excluded, flat `meta/<tier>/*.parquet` minus `profile_` | residual: a multi-file derived dir is skipped silently → **fix-now (XS)** in the debt sweep |
| WIN-7 | half fixed (EP-33) | `fsio.atomic_write_text` unlinks the temp on failure; the fixed `.tmp` sibling name remains | **fix-now (XS)**: pid/uuid temp name + cleanup around `write_text` |
| LDR-9 | partly fixed (EP-33) | one interleaved window is asserted (`test_ep33_loader`); the pass-1 windows (LDR-7) have no test | **allocate (S)** with LDR-7 |
| SGT-5 | still present — **governance** | `safe.FORBIDDEN_FUNCTION_NAMES` blocks `duckdb_settings` only; `duckdb_databases()` / `duckdb_tables()` / `pragma_*` / `version()` enumerate the attached lake and `runs.duckdb` paths (P3 attaches a second database into every safe session) | **fix-now (XS)**: add the `duckdb_` / `pragma_` prefixes + `version` / `current_database` / `current_schema`, with refusal tests |
| DKB-6 | still present | `catalog/profile.py` derives the INSERT placeholder count by splitting the DDL on commas — a `DECIMAL(18,3)` column would over-count | **fix-now (XS)** |
| P01-2 | still present | `config.assert_not_credentialed_lake` tests equality, not containment | **fix-now (XS)** |
| LGR-6 | still present | `AuditLine.sql_text` unbounded; a torn line is filtered, not detected | **fix-now (XS)**: cap + `sql_truncated` marker |
| CTR-7 | still present — grew | orphaned raw-manifest lines feed `raw_snapshot_id`, which every P3 layer id inherits (`dag/snapshot.py`) | **fix-now (S)**: prune records with no contract table at `inventory build` |
| CLI-8 | still present — grew | two `parse_sha256sums` (`inventory`, `demo`); EP-52 added a private import (`doctor._bitlocker_protection`) | **fix-now (S)**: rename, promote the two private names |
| DAG-9 | still present | `report_job` has 0 test hits; the failed-job path is untested (and now carries a provenance run) | **fix-now (S)**: two fixture-tier tests |
| DKB-7 | still present | no pin test for `json_serialize_sql` spellings or the partitioned-`COPY` flags; the DuckDB pin is the only guard | **fix-now (S)** |
| TST-5 | still present — grew | the literal 31 is asserted in 13 test modules (five added in P3) | **fix-now (S)**: one `helpers.STAGED_TABLE_COUNT` |
| TST-7 | still present | a 200 ms wall budget on a cold-interpreter subprocess | **fix-now (XS)**: time `decide()` in-process, loose ceiling on the subprocess |
| SGD-5, SGD-6, SGD-7 | still present (session-guard class) | `ls` is an allow-listed launcher in the hook; the settings allow list lacks the `--group` / bare `--project` forms the hook accepts; the hook's token set lacks guard G1's extension classes (`.tsv`, `.xlsx`, `.jsonl`, …) | **owner-applied package** (D-45 item 4 class): authored with the debt sweep, applied by the owner |
| DAG-5 / WIN-5 | still present (one finding) | `dag/jobs.py` writes the `running` state after `Popen`, racing the supervisor's terminal rewrite | **allocate (S)**: write the state before `Popen` |
| DAG-10 | still present (doc vs code) | the lock is per data root; the docstring and message say per machine | **fix-now (XS)**: reword; the machine-scoped lock stays parked |
| TST-6, TST-8, TST-9 | still present | exact run-id / job-name pins; verbatim CLAUDE.md phrase pins + a 9,000-byte cap; the full-tier verify test appends ledger lines on every rerun | **park** (TST-9 → skip when a `verify` line exists for the snapshot; the others are churn-rule hygiene) |
| CTR-3, CTR-5, CTR-6, CTR-8 | still present | fixture id-floor ordering unvalidated; `manifest.json` `id_floor` single; `structural_hash` omits `dataset`; `write_fixture` partial-module rewrite | **park to the next fixture regeneration (0.4.0)** — three of the four move `manifest.json` bytes |
| DAG-7 | still present | `layer_snapshot` reads `sort_keys` from the live contract, not the manifest line | **park (M)**: needs a `ManifestLine` field; no `sort_keys` edit is planned |
| LGR-5 | still present | an executed statement can go unaudited in the crash window before the post-execution append | **park (M)**: two-phase audit; EP-42's audit-id counts make it load-bearing — revisit at EP-74 with the P4 export path |
| LDR-7 | still present | two crash windows discard a completed pass 1 (the multi-hour path) | **allocate (S)** with LDR-9 — before P9 restages anything (LOAD-4's window) |
| DRF-6 | closed by DESIGN §14 | both pre-EP-43 aggregates are named there and carry sidecars on disk; CLAUDE.md §5 stays a pointer (its byte cap) | **close** |

Routing: the fix-now and allocate rows form the candidate scope of a **P4 debt-sweep S brief**
(the per-phase slot, re-purposed from wheels to debt — the owner decides in § Checkpoint
minutes: allocate, fold into P4 pickup notes, or park all as AUDIT-1); the session-guard trio
is an owner-applied package either way; the parked rows stay in `final-roadmap.md` AUDIT-1
with this table as their evidence.

## Decisions routed to EP-54 (recommendations; outcomes in § Checkpoint minutes and D-47)

1. **SPINE-1 — chartevents vitals subset in the spine (DESIGN §21; EP-50 amendment 4).**
   Measured: ≈ 66 M rows / ≈ 0.2 GB / < 1 min. *Recommendation:* v1 keeps raw `chartevents`
   out; record the measured cost on the SPINE-1 row and let EP-83 (care pathways) or MEDS-1
   pull the trigger — no P4 consumer needs it, the timeline presets read `chartevents`
   directly.
2. **LOAD-5 — the loader's small-path seams (Risk 18).** *Recommendation:* (a) document and
   leave — no reader depends on physical order, sha256 determinism holds run to run, and a
   restage moves the core snapshot id that 65 concepts, five phenotypes, two cohorts, the
   spine and every run manifest cite; switch the small path to per-bucket single-file
   `COPY`s **before P9's ED staging** (the same window as LOAD-4), which is the next time the
   loader writes. DESIGN §5 gets the correcting note.
3. **MISS-4 — at-risk curve banding (Risk 17).** *Recommendation:* leave `meta.mp_item_hourly`
   under the catalog / `runs/` (nothing promoted), add a monotone-count rule to `disclose`
   (a `check` finding for non-increasing count columns; a chain floor at k) **in EP-72's
   scope** as the next consumer, with EP-91's KM tables the next shape — no separate brief.
4. **Toolchain slot (P4).** Not needed (§ above).
5. **Debt sweep (P4).** Allocate `EP-173-debt-sweep-p4.md` (S) at the head of P4 for the
   fix-now / allocate rows above, or fold / park.
6. **Connector denies.** Owner applies the scratchpad package.
7. **Runner adopts `ResourceLog` (EP-36).** *Decision (routine):* keep the EP-19 runner's
   per-step `_RssSampler`; every CLI build already runs inside `run.start`, so run-level
   resources (incl. `disk_delta_mb`) exist for every job — per-step disk deltas are parked
   as v2 PROV-2.
8. **`runs` registry exemption (EP-35).** *Decision (routine):* stays outside — per-run counts
   through `mwh sql` are k-suppressed; `mwh runs list | show` and `mwh protocol list` are the
   session listings; revisit at EP-136 for the Runs page.
9. **Concept-group tag overlap (EP-45).** *Decision (routine):* keep the tags; the rule is
   `--dry-run` before naming a tag after a mimic-code group (gotchas §3); a `concept-<group>`
   prefix is an XS item for the debt sweep if allocated.
10. **`cohorts.build` under `--tag cohorts` (EP-47).** *Decision (routine):* no — `cohorts` =
    the registry index, `marts` = every mart build (EP-55/56 join it).
11. **FC-9 (`vl-convert-python`).** Recorded: core since EP-48 (D-15 addendum).
12. **"One count column per released bin" (EP-49/EP-50).** *Decision (routine):* no per-table
    policy — `disclose.suppress` knows nested pairs and releases both counts when no
    difference is small (the EP-50 shape) and withholds otherwise; a consumer that cannot
    reach the primitive releases one count (the EP-49 shape). D-33 addendum.
13. **Role stamp in `engine.open_duckdb` (EP-49).** Deferred to EP-57/58 (pickup note): move
    it when the app opens connections outside `open_catalog`.
14. **57-row derived sum (EP-53).** Reconciled above (patch effect); D-19 addendum.

## Gates (end of the EP-54 session)

| Gate | Result |
|---|---|
| `mwh verify EP-k`, k ∈ {34 … 45, 172, 46 … 53} (21 briefs, fresh interpreter each, run concurrently with the gate queries) | **0 failures** — 347 tests; 4–171 s per brief, ≈ 26 min in total |
| `uv run poe check` (ruff check + `ruff format --check` + pyright + pytest, fixture tier) | exit 0 — pyright 0 errors, **1,183 passed, 58 deselected, 1 warning** (a crafted torn-ledger test), **729 s** (12 min 09 s) |
| `uv run poe roadmap-check --strict` | 0 errors / 0 warnings — **174 rows, 174 briefs, 63 done** before this brief's tick (EP-173 added) |
| `mwh guard <the 29 changed / new files>` | clean |
| `mwh disclose check roadmap/retro-p3.md` (advisory — a roadmap record of counts and telemetry, GOVERNANCE §3 class, not a promoted artefact) | no `SMALL_CELL`, `ID_BAND` or `ID_COL` finding; `FREE_TEXT` on prose cells over 64 characters only (the same shape as `retro-p2.md`'s tables) |
| `mwh doctor` | 10 pass · 1 warn · 0 fail · 5 info, unchanged from pre-flight; 380.4 GB free |

## Checkpoint minutes

> **Checkpoint minutes (2026-09-18).** One session on this brief (2026-09-17 evening →
> 2026-09-18 after midnight, local; ≈ 3 h against the S budget — the EP-33 amendment made
> it a six-item re-plan with a carried-low re-triage and twenty brief amendments, recorded
> under Risk 11 with the other over-runs). The owner answered two rounds (four + three
> questions) after the records were written and the gates had run; **every recommended
> option was taken** (D-47):
>
> 1. **SPINE-1 — keep raw `chartevents` out of the v1 spine**; the measured cost (≈ 66 M
>    rows, ≈ 0.2 GB, under a minute) is on the `final-roadmap.md` row for EP-83 / MEDS-1
>    to trigger; DESIGN §21 resolved. Rejected: an S brief in P4; folding into EP-55.
> 2. **LOAD-5 — document and leave; fix before P9's ED staging** (EP-147's window, with
>    LOAD-4): DESIGN §5 note, D-17 addendum, Risk 18 re-worded as a scheduled fix.
>    Rejected: an in-place re-sort now; switching the small path and restaging now.
> 3. **MISS-4 / Risk 17 — routed to EP-72** as a `disclose` refinement (monotone-count
>    check + a chain floor at k); D-33 addendum; Risk 17 stays open until then. Rejected: a
>    separate S brief; accepting the residual and closing the risk.
> 4. **Debt sweep — EP-173 allocated** (S, core) at the head of P4 before EP-55
>    (`EP-173-debt-sweep-p4.md`; 174 briefs, 24 S · 148 M · 2 L): the fix-now rows led by
>    SGT-5, the session-guard trio as an owner-applied package, LDR-7 + LDR-9 the named
>    overflow. Rejected: folding into the P4 briefs that touch the files; parking all for
>    EP-74.
> 5. **Connectors — the owner applies the full deny package** (`ep54-connector-denies.md`
>    in the session scratchpad; 19 lines; `mwh guard --selfcheck`; one `chore(session-guard)`
>    commit). Rejected: a narrower package; recording only. *(Applied 2026-09-26: on the
>    owner's explicit one-time instruction the session made the edit itself, the owner
>    reviewed the file and confirmed; JSON valid, 127 deny entries, `mwh guard --selfcheck`
>    passed, `mwh verify EP-165` 61 passed; the governance text is unchanged. The D-45
>    item 4 rule stands for every later package.)*
> 6. **Toolchain slot — not allocated** (no wheel / version fight open; D-15 addendum).
>    Rejected: a slot for the fixture-suite wall (EP-74's topic).
> 7. **Commit — the two standard steps, no push** (the owner pushes). Rejected: the first
>    step only; no commit.
>
> Routine calls stated in § Decisions routed to EP-54 and not objected to: the runner keeps
> `_RssSampler` (PROV-2 parked); `runs` stays non-registry; the concept-group tags keep their
> names (the prefix is EP-173's optional item); `cohorts.build` stays under `marts`; no
> per-table count-column policy; the role stamp moves into `engine.open_duckdb` only if
> EP-57/58 need it; FC-9 recorded; the 57-row sum reconciled; D-46 recorded.

## Commit series

- `f1c2536` — `docs(roadmap): re-plan P3 (EP-54)` — this file, the EP-50 completion note, the roadmap
  README (P3 → P4 name corrections, Risks 2/5/6/11/15/16/17/18, coverage, the EP-173 row),
  `final-roadmap.md` (SPINE-1, LOAD-5, MISS-4, AUDIT-1, CONC-2, PROV-2), the twenty P4
  pickup notes, `EP-173-debt-sweep-p4.md`, DESIGN §3/§5/§9/§10/§11/§13/§14/§15/§21 notes,
  DECISIONS D-15/D-17/D-19/D-20/D-24/D-25/D-33/D-43 addenda + D-46/D-47 + the status index,
  both READMEs.
- `ee90dc8` — `docs(roadmap): record EP-54 commit hash` — the ☑ tick.
- `chore(session-guard): deny the new write-capable connector tools (EP-54, owner-applied)`
  (2026-09-26) — the 19 deny lines; applied by the session on the owner's one-time
  instruction and owner-reviewed (checkpoint item 5).
