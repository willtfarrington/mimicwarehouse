# EP-16 — Re-plan P1

**Size:** S · **Tier:** n/a · **Core/Stretch:** core · **Depends on:** EP-8 (mimic-code vendoring), EP-9 (Schema registry (YAML contract)), EP-10 (Raw inventory manifest ⏱), EP-11 (Synthetic fixture generator A (hosp)), EP-12 (Synthetic fixture generator B (icu) + pytest tier markers), EP-13 (Repos & awesome-lists inventory), EP-14 (Ontologies & vocabularies inventory), EP-15 (Reading list + companion datasets + methods notes) · **Blocks:** —

> **Amended at the retro (2026-08-18).** Header facts unchanged (Depends still omits EP-164 and the
> retro briefs by the same owner deferral; the linear order carries them). The owner-directed
> retrospective review of EP-0 … EP-12 inserted **EP-165 … EP-169** between EP-12 and EP-13 and **EP-170**
> at the head of P2 (see `roadmap/README.md` Risk 15, `retro-2026-08-18-findings.md`, D-43). Consequences
> for this brief: (1) **items 1–2 are already done** — EP-10's own session finished the full run
> (41/41, 2026-08-18T03:50:04Z, `raw_snapshot_id 8209301d…`, 88 s) and committed
> `docs/resources/raw-inventory.md`; verify only, do **not** re-run `mwh inventory build` before
> `show`/`reconcile` (a no-op build rewrites the snapshot job block until EP-167 fixes it) and strike
> README Risk 1 as resolved by EP-10/EP-16; (2) item 3: the name `EP-165` is consumed — the optional P2
> toolchain-remediation slot is the **next free number (EP-171)**; (3) item 6 (amend P2 briefs) is
> **now EP-170** (runs after this brief with the shipped names of EP-167 … EP-169) — this brief records
> the retro table, DECISIONS addenda and ☑ reconciliation only, and confirms P2 readiness; (4) item 5:
> the P1 rows now include EP-165 … EP-169 (☑ each with its hash) — expect 171 rows = 171 briefs;
> (5) `--strict` is green (Risk 14 resolved by EP-164) — use it; (6) the acceptance clause "P2 briefs
> carry pickup notes" moves to EP-170.

> **Amended at EP-7 re-plan (2026-08-17).** Checked against the P0 code; header facts unchanged (EP-164 was
> **not** added to this brief's Depends — a README-table change the owner may make; EP-164 declares
> `Blocks: EP-16` and sits first in the P1 table, which carries the ordering). (1) **EP-164 exists** —
> `EP-164-toolchain-remediation-p1.md` (S; `mwh doctor` `antivirus`, 14th check) was allocated at EP-7 and
> executes before EP-8: item 5's "every P1 row ☑" includes its row; item 6's readiness list quotes the doctor
> as 14 checks; and item 3's "toolchain remediation slot for P2" is decided the same way — **as the next free
> number (`EP-165-…-p2.md`, same file/row conventions), never `EP-16a`**: `verify._BRIEF_FILE` /
> `_ROW` / `resolve_ep` accept only numeric EP tokens, so an `EP-16a` file is invisible to `roadmap_check`
> and `mwh verify`. (2) `mwh doctor` **cannot confirm** the Defender exclusion or `LongPathsEnabled` the way
> item 6 implies: `defender` is `info` when not elevated (exclusions unreadable) and only ever `warn`;
> `longpaths` is `pass`/`warn`; "doctor green" means exit 0 = no `fail` (`python`, `duckdb`, `disk_free`,
> `data_root`, `temp_dir`, `bitlocker` are the only checks that can fail). Record Defender / LongPaths /
> Malwarebytes on the owner's word (D-38 addenda) and add the **Malwarebytes seven-path allow list + the
> `antivirus` row** to the P2-readiness checklist (Risk 12, D-42 — the ARW heuristic is the likeliest killer
> of EP-17+ full-tier Parquet writes). (3) No `MWH_DATA_ROOT` env var exists on this machine and there is no
> `.env`/`mwh.toml`: "`%MWH_DATA_ROOT%\runs\jobs\…`" means `settings.layout["runs_jobs"]` (default
> `C:\mimicdata\runs\jobs`); "`MWH_DATA_ROOT` on C:" is checked with `mwh paths` (`data_root_source`
> = default). (4) The reconciliation command as EP-6 shipped it is `uv run poe roadmap-check [--strict]
> [--json]` (≡ `mwh verify --roadmap`); `--strict` is red by one accepted warning until EP-164 item 6 or a
> hotfix resolves it (README Risk 14) — treat "0 errors" as green unless that is done. (5) Add to the
> acceptance: `uv run --group dev pre-commit run --all-files` leaves the vendor tree (EP-8) and the fixture
> tree (EP-11/12) unmodified — the fixer hooks are the byte-identity hazard P0 found. Command forms: `uv run
> mwh …` ≡ `uv run --group dev mwh …`.

## Context

Every phase closes with a re-plan (**D-8**): verify the phase's ⏱ job, retro, DECISIONS addenda, ☑
reconciliation with `roadmap_check.py` (EP-6), mirror Parked items into `final-roadmap.md`, and amend
the next phase's briefs where this phase changed a fact. P1's ⏱ job is EP-10's full raw inventory
(background job, log at `%MWH_DATA_ROOT%\runs\jobs\ep10-raw-inventory.log`), whose finished manifest is the
raw snapshot id (**D-26**) that P2's loader manifests cite. P2 (staging) is already fully briefed; the
facts most likely to need amending there are the names P1 actually shipped: the contract API (EP-9),
`raw_snapshot_id()` (EP-10), the fixture root and in-memory catalog (EP-11/12), the tier-marker vocabulary
(EP-12), and the pinned mimic-code sha (EP-8). No data is read; only counts/hashes from `mwh inventory`.

## In scope

1. **Verify EP-10's full run** — confirm the job finished (`Get-Content` on the *log under runs/jobs* is denied to
   sessions: use `uv run --group dev mwh inventory show` and `mwh inventory reconcile`, which read the manifest
   and print counts/hashes only); if incomplete, relaunch with `--resume` (EP-10 recipe) and finish this item
   later in the session. Record in a `> **Completion note (date).**` appended to `EP-10-raw-inventory.md`: files
   done (41/41), wall time per dataset and MB/s (from the module's log summary as printed by `show --timing`),
   files that needed the parallel-CSV fallback, `raw_snapshot_id`, `mimic_code_sha`, `duckdb_version`, and the
   reconciliation summary (matches / mismatches / no-expectation, with the pinned `validate.sql` version) — all
   integers with thousands separators (guard rule G4). Any `header ok = False` or count mismatch becomes a README
   Risk with the affected table names.
2. **Commit the inventory doc** — `mimicwarehouse/docs/resources/raw-inventory.md` (hashes/bytes/rows/header/
   reconciliation per file; GOVERNANCE §3 manifest, no sidecar needed) and add its row to
   `docs/resources/README.md`; strike README Risk 1 as `~~…~~ **Resolved by EP-10/EP-16 (date)** — raw snapshot
   id <first 12 hex>`; the `.csv.gz` re-download stays parked (v2 RAW-1).
3. **Retro** — a short table in this brief's completion note: `EP | size planned | actual (from commit
   timestamps/session notes) | what bit` (wheel/version fights, MAX_PATH, marker plumbing, research time), plus
   three lessons; decide whether the optional per-phase "toolchain remediation" S slot is needed for P2 (write it
   as `roadmap/EP-165-toolchain-remediation-p2.md` — the next free number, EP-164 precedent; `EP-16a`-style
   names do not parse in `roadmap_check`/`mwh verify` — only if the owner agrees, insert its row before EP-17
   and commit `docs(roadmap): add EP-165 — toolchain remediation (P2)`; otherwise note "not needed"; amended
   EP-7).
4. **DECISIONS addenda** — under D-19 (pinned sha, if EP-8 did not add it), D-26 (`raw_snapshot_id`, run date,
   timing), D-27 (fixture seed, subject count, byte size, layout), D-35 (which free vocabulary paths EP-14
   confirmed / which need owner action), and any new numbered decision that surfaced (append at the end, never
   rewrite).
5. **Roadmap reconciliation** — run `uv run poe roadmap-check` (≡ `mwh verify --roadmap`; add `--strict` once
   README Risk 14 is resolved; amended EP-7) from the workspace: every P1 row ☑ with its commit hash — **EP-164
   included** — table ↔ file parity; mirror each EP-164, EP-8…EP-15
   `## Parked → final-roadmap.md` item into the matching `final-roadmap.md` category table (mimic-code
   re-transpile → 3; OMOP/FHIR annotations → 34–35; Athena / RxNorm-ATC → 3 / 12; fixture-dim refresh, Synthea,
   property fixtures → 31 or Cross-cutting; eICU / CXR-ECG links are already there); re-audit the capability
   coverage table for categories 1 (EP-10 done) and 35 (EP-14 → EP-143 dependency intact).
6. **Amend P2 briefs** — read EP-17…EP-22 (and skim EP-28/29/30) and add `> **EP-16 pickup note (date).**` blocks
   wherever P1 shipped a different name or shape than assumed: contract API (`load_contract()`,
   `Table.read_csv_columns()`, `csv_path`, `load_class`, `sort_keys`, `Contract.column_map("demo_2_2")`),
   `mimicwarehouse.inventory.raw_snapshot_id()` as the loader manifest's `source manifest id`, fixture root
   `tests/fixtures/mimic-iv-3.1/` as `--source` for fixture-tier loads, `build_fixture_catalog()` for unit tests,
   `--tier` markers, `vendor_info().sha` for concept manifests. Confirm P2 readiness in the note: `mwh doctor`
   green (exit 0 — no `fail`; 14 checks after EP-164), ≥ 100 GB free (staging temp peak 60–100 GB), Defender
   exclusion on the data root, LongPathsEnabled and the **Malwarebytes seven-path allow list** recorded on the
   owner's word (**D-38** addenda; the doctor cannot read either exclusion list — its `antivirus` row names the
   products; amended EP-7), data root on C: (`mwh paths`: `data_root_source` = default `C:\mimicdata`; no
   `MWH_DATA_ROOT` env var exists).

## Out of scope

- Writing new full briefs (P2 is already full; the first re-plan that writes N+1 is EP-74).
- Any staging code or full-tier load → EP-17+.
- Rewriting history in DESIGN/DECISIONS/README (append only).

## Verification / acceptance

- `EP-10-raw-inventory.md` carries a completion note with `raw_snapshot_id`, 41/41 files, timing and reconciliation;
  `docs/resources/raw-inventory.md` exists and is committed; README Risk 1 struck through.
- `uv run poe roadmap-check` exits 0 (0 errors; `--strict` too once Risk 14 is resolved); all P1 rows in
  `roadmap/README.md` — EP-164, EP-8 … EP-15 — show ☑ + hash; `final-roadmap.md` contains the mirrored Parked
  items; DECISIONS addenda present under D-19/D-26/D-27/D-35 (amended EP-7).
- P2 briefs carry pickup notes where needed (or the completion note states "no amendments needed" per brief).
- `uv run --group dev pre-commit run --all-files` modifies nothing under the vendor tree or `tests/fixtures/`
  (amended EP-7).
- Commit `docs(roadmap): re-plan P1 — EP-10 verified, retro, addenda (EP-16)`, then tick ☑ EP-16 with that hash
  in `roadmap/README.md` (`docs(roadmap): record EP-16 commit hash`).

> **Completion note (2026-08-28).** Executed as one autonomous session, tier n/a (docs-only;
> the only commands touching real-data state were the read-only `mwh inventory show |
> reconcile` and a `--resume` no-op `build` — outputs were counts, hashes and job status
> only, GOVERNANCE §4). Power mode confirmed **Best performance** on AC (`mwh doctor`
> `power_scheme`) before any test run. All Depends-on rows were ☑ at pickup, plus
> EP-164/EP-165 … EP-169 per the retro amendment.
>
> **Items 1–2 — EP-10 verified (per the 2026-08-18 amendment: verify only; the run and the
> docs page shipped with EP-10 itself).** The EP-10 verification recipe passed exactly:
> `mwh inventory show --timing` → 41/41 files (0 pending, 0 header mismatch), job finished
> 2026-08-18T03:50:04Z, errors 0, `raw_snapshot_id 8209301d8a06…`, totals 104,641,868,093
> bytes / 902,815,672 rows; `mwh inventory reconcile` → exit 0, **match=34 · mismatch=0 ·
> no-expectation=7 · pending=0** against the pinned `validate.sql` (MIMIC-IV 3.1 + ED 2.2);
> `mwh inventory build` (run *after* show/reconcile) → "0 to process, 41 up to date", and
> the snapshot job block was untouched afterwards — the EP-167 INV-1 fix confirmed live, so
> the amendment's do-not-build caution is obsolete. EP-10's completion note already carries
> everything item 1 asks for (timing, MB/s, zero fallbacks, shas, reconciliation summary),
> so nothing was appended there; `docs/resources/raw-inventory.md` was already committed and
> indexed in `docs/resources/README.md`, and README Risk 1 was already struck (all by
> EP-10's session). One file changed in this commit: `reconcile` now stamps the page's
> `Generated` line with the job's `finished` timestamp (deterministic since EP-167), so the
> page's one wall-clock line moved `03:50:27` → `03:50:04`; future reconciles rewrite it
> byte-identically. D-26 addendum records this verification.
>
> **Item 3 — retro.** Actuals from completion notes where stated ("note"), else the
> previous-commit → feat-commit gap ("gap" — understates sessions whose research preceded
> the prior tick; EP-13/14/15 ran back-to-back on 2026-08-28 evening):
>
> | EP | size planned | actual | what bit |
> |---|---|---|---|
> | EP-164 | S ≈ 30 min | ≈ 40 min (note) | SecurityCenter `productState` semantics — the WSC bit reads "off" for a deliberately unregistered product; warn rule rewritten to presence-based |
> | EP-8 | S ≈ 30 min | ≈ 50 min (note) | upstream debugging comments carrying real-band ids → redaction policy designed mid-session |
> | EP-9 | M ≈ 1 h | ≈ 55 min (note) | demo-2.2 column-map research (proving the identity map); upstream PK facts differed from the brief |
> | EP-10 | M ≈ 1 h | ≈ 1 h (note) | nothing — the ⏱ full job finished in 88 s against the 10–30 min planned |
> | EP-11 | M ≈ 1 h | ≈ 1¼ h (note) | hand-typing vocab seeds; byte-discipline plumbing so fixer hooks are provably no-ops |
> | EP-12 | M ≈ 1 h | ≈ 1½ h (note) | `d_items` breadth + cross-module planted signal; one `python -` stdin hang (D-42) |
> | EP-165 | M ≈ 1 h | ≈ 1 h (note) | live-fire probing of the PreToolUse hook and settings hot-reload |
> | EP-166 | M ≈ 1 h | ≈ 40 min (gap) | — |
> | EP-167 | M ≈ 1 h | ≈ 35 min (gap) | — |
> | EP-168 | S ≈ 30 min | ≈ 25 min (gap) | — |
> | EP-169 | M ≈ 1 h | ≈ 25 min (gap) | the single bundled fixture regeneration went as planned |
> | EP-13 | M ≈ 1 h | ≈ 45 min (gap) | live re-verification of every repo row (`gh api`); the "jaanli" viz repo had moved accounts |
> | EP-14 | M ≈ 1 h | ≈ 15 min (gap; understates the research) | cms/fda/loinc 403 the harness fetcher → curl + browser UA; cdc.gov refuses all automation; Quan 2005 cited by DOI because its PMID is an in-band 8-digit token (G4) |
> | EP-15 | M ≈ 1 h | ≈ 25 min (gap) | FPP3 sits behind a bot-check → citations swapped; doi.org handle API as the link-check tactic |
>
> Phase total ≈ 10½–11 h against ≈ 12½ h planned (3 S + 11 M) — P1 came in *under* budget
> even with five unplanned retro briefs absorbed into it. **Three lessons:** (1)
> planning-era I/O estimates for this NVMe are ~15× conservative (2.0–2.4 GB/s sustained
> with both endpoint-security engines live) — EP-17/18 loader budgets should assume reads
> are nearly free and Parquet *writes* into the data root are the bottleneck; (2) the cost
> of docs/inventory briefs is external verification, not writing — the reusable tactics
> (`gh api` per repo row, doi.org handle API for DOIs, curl + browser UA for
> government/vendor sites, DOIs never PMIDs) are recorded in the EP-13/14/15 notes and
> should be the starting point for every later citation pass; (3) mid-phase consolidation
> paid for itself — EP-165 … EP-169 cost ≈ 3 h total and every later P1 session started
> from true status docs and hardened session tooling; the real overruns (EP-11/12) came
> from underestimating hand-typed vocabulary breadth, not tooling fights.
>
> **P2 toolchain-remediation slot (would be EP-171, the next free number): not needed** —
> recommendation recorded for owner review, no row inserted. Rationale: no wheel/version
> fight is open (Risk 3's remnants — pygam, scikit-survival/`gpl`, econml, pytensor — are
> settled pins for P5+ phases, not P2 blockers; the one sdist allow-list entry
> `autograd-gamma` was accepted at EP-7); both endpoint-security products are allow-listed
> with `mwh doctor` naming them; and P2 already opens with a dedicated reconciliation brief
> (EP-170). If a fight surfaces mid-P2, allocate `EP-171-toolchain-remediation-p2.md` then
> (EP-164 precedent; `EP-16a`-style names do not parse in `roadmap_check`/`mwh verify`).
>
> **Item 4 — DECISIONS addenda.** D-19 needed nothing (EP-8's addendum + the `db38a30`
> owner verdict already record the pin). Added this session: **D-26** (EP-10 verification
> results + the deterministic `Generated` timestamp), **D-27** (the shipped fixture as P2
> codes against it: seed 2026, 120 subjects, 31 CSVs, 50,974 rows, 5,370,674 bytes,
> layout, `GENERATOR_VERSION 0.2.0`), **D-35** (which free vocabulary paths EP-14
> confirmed; owner actions = UTS/UMLS account, ATC bulk purchase, Athena account — all
> parked). Appended only; nothing rewritten.
>
> **Item 5 — reconciliation + mirrors.** `uv run poe roadmap-check --strict` → **OK — 171
> rows, 171 briefs, 23 done, 0 errors, 0 warnings** (22 before this EP's tick); every P1
> row — EP-164, EP-8 … EP-12, EP-165 … EP-169, EP-13 … EP-15 — ☑ with its hash; table ↔
> file parity ok. Parked-item audit: EP-8 (CONC-1), EP-9 (OMOP-1/FHIR-1 row), EP-10
> (RAW-1), EP-11 (FIX-1/2), EP-12 (FIX-3), EP-14 (PHE-3/PHE-4), EP-164 (DOC-1) and EP-15's
> pointers (EXT-1, LINK-*, FHIR-1, OMOP-1, MEDS-1, FIX-2) were all already mirrored by
> their own sessions — re-audited present. Newly mirrored this session (EP-13's five "port
> later" items): **v2 DAG-2** (MEDS_transforms stage catalog) and **v2 MRD-1** (healthylaife
> full port) as new §34–35 rows; MEDS-DEV + meds-tab folded into the existing **MEDS-1**
> row (dated re-affirmation); the **OMOP-1** row's reference implementation re-pointed to
> saywurdson/mimic-iv-dbt (Apache-2.0) with the CogStack no-license finding recorded.
> Capability-coverage re-audit: category 1 unchanged and its EP-10 leg is done (this EP
> verified it); category 35 unchanged — the EP-14 → EP-143 dependency is intact in the P9
> table and EP-14's register hands EP-143 its free fallbacks. No coverage row re-titled
> (so `reading.md`/`test_ep15` untouched).
>
> **Item 6 — handed to EP-170** (per the retro amendment), which runs next with the shipped
> names of EP-167 … EP-169. **P2 readiness confirmed:** `mwh doctor` exit 0 — 9 pass ·
> 1 warn · 0 fail · 5 info over **15 checks** (EP-167 added `deny_coverage` and
> `power_scheme` to EP-164's 14; the `antivirus` warn is the expected state on this host,
> notation table); disk 406.3 / 951.5 GB free (≥ 100 GB with ~300 GB headroom over the
> staging temp peak); Defender exclusion on `C:\mimicdata` and `LongPathsEnabled=1` +
> `core.longpaths=true` (doctor `longpaths` pass; Defender on the owner's word, EP-0
> follow-up — the doctor cannot read exclusions non-elevated); the Malwarebytes allow list
> — now **nine** paths, superseding this brief's "seven" — re-confirmed by the owner on
> 2026-08-28 (D-38 addendum, `cc40326`); data root on C: (`mwh paths`: `data_root
> C:\mimicdata (from default)`; no `MWH_DATA_ROOT` env var). Per-tier lake roots
> (`lake_fixture`, `lake_demo`, 18 layout keys) exist as settings since EP-167; the
> directories are created on demand by EP-17/19.
>
> **Gates.** `uv run --group dev pre-commit run --all-files` all hooks passed with the
> vendor tree and `tests/fixtures/` unmodified; `uv run mwh verify EP-16` green;
> `poe roadmap-check --strict` 0 errors / 0 warnings. Nothing parked by this brief.
> Next: **EP-170** (head of P2).

> **Completion-note addendum (2026-08-29).** The owner reviewed the "EP-171 not needed"
> recommendation above and directed allocation anyway: **EP-171 exists** —
> `EP-171-toolchain-remediation-p2.md` (S, core; a synthetic write canary rehearsing the
> loader's burst/large/manifest/swap/delete I/O shapes against the Malwarebytes ARW
> heuristic, Risk 12, and measuring the write-side MB/s baseline of EP-16 lesson 1),
> row inserted after EP-170 / before EP-17, `Blocks: EP-17` (EP-17's header untouched,
> EP-164 convention) → 172 briefs, 24 S · 147 M · 1 L; committed as
> `docs(roadmap): add EP-171 — toolchain remediation (P2)`. The recommendation paragraph
> stands as written (history, not error); P2's execution order is now EP-170 → EP-171 →
> EP-17.
