# EP-16 — Re-plan P1

**Size:** S · **Tier:** n/a · **Core/Stretch:** core · **Depends on:** EP-8 (mimic-code vendoring), EP-9 (Schema registry (YAML contract)), EP-10 (Raw inventory manifest ⏱), EP-11 (Synthetic fixture generator A (hosp)), EP-12 (Synthetic fixture generator B (icu) + pytest tier markers), EP-13 (Repos & awesome-lists inventory), EP-14 (Ontologies & vocabularies inventory), EP-15 (Reading list + companion datasets + methods notes) · **Blocks:** —

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
   as `EP-16a`-style addendum only if the owner agrees; otherwise note "not needed").
4. **DECISIONS addenda** — under D-19 (pinned sha, if EP-8 did not add it), D-26 (`raw_snapshot_id`, run date,
   timing), D-27 (fixture seed, subject count, byte size, layout), D-35 (which free vocabulary paths EP-14
   confirmed / which need owner action), and any new numbered decision that surfaced (append at the end, never
   rewrite).
5. **Roadmap reconciliation** — run `roadmap_check.py` as EP-6 documents it (from the workspace,
   `uv run --group dev …`): every P1 row ☑ with its commit hash, table ↔ file parity; mirror each EP-8…EP-15
   `## Parked → final-roadmap.md` item into the matching `final-roadmap.md` category table (mimic-code
   re-transpile → 3; OMOP/FHIR annotations → 34–35; Athena / RxNorm-ATC → 3 / 12; fixture-dim refresh, Synthea,
   property fixtures → 31 or Cross-cutting; eICU / CXR-ECG links are already there); re-audit the capability
   coverage table for categories 1 (EP-10 done) and 35 (EP-14 → EP-143 dependency intact).
6. **Amend P2 briefs** — read EP-17…EP-22 (and skim EP-28/29/30) and add `> **EP-16 pickup note (date).**` blocks
   wherever P1 shipped a different name or shape than assumed: contract API (`load_contract()`,
   `Table.read_csv_columns()`, `csv_path`, `load_class`, `sort_keys`, `Contract.column_map("demo_2_2")`),
   `mimicwarehouse.inventory.raw_snapshot_id()` as the loader manifest's `source manifest id`, fixture root
   `tests/fixtures/mimic-iv-3.1/` as `--source` for fixture-tier loads, `build_fixture_catalog()` for unit tests,
   `--tier` markers, `vendor_info().sha` for concept manifests. Confirm P2 readiness in the note: `mwh doctor` green,
   ≥ 100 GB free (staging temp peak 60–100 GB), Defender exclusion on the data root and LongPathsEnabled recorded
   (**D-38**), `MWH_DATA_ROOT` on C:.

## Out of scope

- Writing new full briefs (P2 is already full; the first re-plan that writes N+1 is EP-74).
- Any staging code or full-tier load → EP-17+.
- Rewriting history in DESIGN/DECISIONS/README (append only).

## Verification / acceptance

- `EP-10-raw-inventory.md` carries a completion note with `raw_snapshot_id`, 41/41 files, timing and reconciliation;
  `docs/resources/raw-inventory.md` exists and is committed; README Risk 1 struck through.
- `roadmap_check.py` exits 0; all P1 rows in `roadmap/README.md` show ☑ + hash; `final-roadmap.md` contains the
  mirrored Parked items; DECISIONS addenda present under D-19/D-26/D-27/D-35.
- P2 briefs carry pickup notes where needed (or the completion note states "no amendments needed" per brief).
- Commit `docs(roadmap): re-plan P1 — EP-10 verified, retro, addenda (EP-16)`, then tick ☑ EP-16 with that hash
  in `roadmap/README.md` (`docs(roadmap): record EP-16 commit hash`).
