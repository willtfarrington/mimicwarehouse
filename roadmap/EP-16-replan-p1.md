# EP-16 — Re-plan P1

**Size:** S · **Tier:** n/a · **Core/Stretch:** core · **Depends on:** EP-8 (mimic-code vendoring), EP-9 (Schema registry (YAML contract)), EP-10 (Raw inventory manifest ⏱), EP-11 (Synthetic fixture generator A (hosp)), EP-12 (Synthetic fixture generator B (icu) + pytest tier markers), EP-13 (Repos & awesome-lists inventory), EP-14 (Ontologies & vocabularies inventory), EP-15 (Reading list + companion datasets + methods notes) · **Blocks:** —

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
