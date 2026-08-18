# EP-170 — Retro F: P2/P3 brief reconciliation (pickup notes from the retro + P1)

**Size:** M · **Tier:** n/a · **Core/Stretch:** core · **Depends on:** EP-16 (Re-plan P1), EP-166 (Retro B: docs consolidation & status surface), EP-167 (Retro C: CLI, settings & inventory consolidation), EP-168 (Retro D: test-infrastructure consolidation (tier readiness, demo marker, churn)), EP-169 (Retro E: contract tie-breaks, structural hash & fixture regeneration (0.2.0)) · **Blocks:** EP-17 (Loader core A: typed CSV → Parquet)

> **Origin.** Sixth retro brief (origin note in EP-165; ledger =
> [`retro-2026-08-18-findings.md`](retro-2026-08-18-findings.md); decisions = D-43). Docs-only. It runs
> **after** EP-16 (which verifies P1 and does its retro) and after the retro code briefs, so every pickup
> note below cites the names that actually shipped. It does what EP-7 did for P1 — an
> `> **Amended at EP-170 (date).**` block at the top of each affected brief, in-place edits marked
> "amended EP-170", header facts unchanged unless a row in this brief says otherwise (then table + header
> move together and `roadmap_check` stays 0/0). Ledger ids: SCH-3, FC-5 … FC-16, ARCH-2, ARCH-8, ARCH-10,
> CMP-5 (+ low FC-17 … FC-30, ARCH-11 … ARCH-16, SCH-4 … SCH-12, CMP-4, CMP-6 … CMP-9).

## Context

The forward-compat lens read EP-13 … EP-33 fully and EP-34 … EP-56 by header/In-scope against the shipped
P0/P1 code and found ~30 concrete mismatches; the retro session then *decided* several of them (D-43)
rather than leaving each brief to re-decide: catalog swap protocol, lake roots, tier readiness fixtures,
demo marker, sort-key tie-breaks, structural hash, csv dialect, logical snapshot id, `settings.dev_buckets`
as the only bucket source, `psutil` into core at EP-19, connector policy. This brief lands those facts in
the briefs that will read them, and adds a *notation glossary* to `roadmap/README.md` (EP-166 writes the
table; this brief makes every P2/P3 brief defer to it) so private notation (`%MWH_DATA_ROOT%` in 89
briefs, `roadmap_check.py` in 18, `DEV_BUCKETS` in 3, "Command forms" pasted into 9) stops needing
per-brief repair.

## In scope — the mismatch table (one pickup note per row; ledger id in brackets)

| Brief | What is wrong / undecided today | Pickup note says |
|---|---|---|
| EP-16 | items 1–2 already done by EP-10 (full run finished 2026-08-18T03:50:04Z, docs page committed) [FC-21]; item 3's "EP-165" name is consumed; item 6 (amend P2 briefs) is now EP-170; Depends omits EP-164/165…169 (owner deferral, CMP-7) | verify-only for items 1–2; P2 remediation slot = next free number (EP-171); item 6 → "EP-170 does it after this brief; EP-16 records the retro table + DECISIONS addenda only"; the retro block was already added on 2026-08-18 |
| EP-17 | "if EP-3 already ships a connection factory" — it does not; `inventory.open_connection()` exists [FC-7]; `settings.loader_reject_max` is a new field → `.env.example` parity test [FC-14, ARCH-13]; `source_manifest_id` = per-file sha256 **and** `raw_snapshot_id` both carried [FC-8, ARCH-6]; `os.replace(dest.new → dest)` fails on Windows when `dest` exists → `paths.swap_dir` [FC-6, ARCH-2]; item 3 `ORDER BY <primary key>` → `Table.sort_keys` (provider/caregiver have no PK) [SCH-3]; hard-coded `timestampformat` → `schema/csv_dialect` + the dev-tier `max(length)` probe [SCH-1]; `csv_path` is dataset-relative, DAG `source` raw-root-relative, note dir = long PhysioNet name [FC-29]; ensure temp-dir parent exists [CFG-3]; identifier/free_text column flags: EP-17 owns adding `identifier`/`free_text` to `Column` + `identifiers:` in keys.yaml, EP-23…30 verify [FC-5]; dev tests use `raw_root` fixture [FC-1]; timing inputs (EP-10: 2.0–2.4 GB/s reads) [FC-26] | one block with the eight facts + the new API names from EP-167/168/169 (`Settings.lake_root/rejects_root/min_free_gb_for`, `console`, `inventory.rel_path_for/for_table`, `Table.read_csv_options`, `structural_hash`, `raw_root`) |
| EP-18 | `DEV_BUCKETS` constant "once in config" → `settings.dev_buckets` (EP-3) [FC-15, ARCH-8]; per-bucket sort must use the tie-break `sort_keys` [ARCH-4]; swap helper from EP-17 | note |
| EP-19 | job launcher must spawn `[sys.executable, "-m", "mimicwarehouse.cli", …]` (allow-listed venv python; real pid), never bare `uv run`; `--data-root` is the global option; log path via `mwh paths --json` `runs_jobs`, never `$env:MWH_DATA_ROOT` [FC-10, CMP-5]; `psutil` joins core here (say so; wheel check) [FC-9]; `StepContext.lake_root = settings.lake_root(tier)` + `assert_not_credentialed_lake` [ARCH-3]; free-space guard per tier [ARCH-9]; layer `snapshot_id` = logical definition (DESIGN §11) [ARCH-5]; concurrent budget note (36 GB build + dev tests) [ARCH-11] | note |
| EP-20 | coverage assertion scoped to hosp+icu (ED/Note excluded until EP-142/148) [ARCH-14]; reconcile only where `expected_rows_source` set; provider/caregiver/ingredientevents vs raw manifest rows [FC-12]; declare all 31 steps (event tables `tiers: [fixture]`) **or** keep 20 and drop `--tag small` in EP-21 item 4 — pick option B unless EP-17/18 make A trivial [FC-11, FXT-11]; DATE-grain columns [ARCH-15] | note |
| EP-21 | swap = rename-aside two-step (DESIGN §6) [ARCH-1]; `catalog_path('fixture')` under the data root, fixture lake = `lake/fixture` [ARCH-3]; record `dev_buckets` in `meta.catalog_info` and warn on drift [ARCH-8]; item 4 fixture-catalog re-pointing → `fixture_lake_catalog` separate from the in-memory `fixture_catalog` [FC-11]; catalogs embed absolute lake paths / version pin → note relocation + rebuild cost [ARCH-16]; `MWH_ROLE` is a new Settings field (parity) [ARCH-13] | note |
| EP-22 | demo tests = `@pytest.mark.demo` + `--with-demo` (not `--tier demo`) [ARCH-7, FC-2]; the demo map is the identity → drop/rename `map_notes`/`add_null` vocabulary; column-map unit test uses a synthetic non-identity map [SCH-3, FC-19]; `MWH_ALLOW_REMOTE` gate wording (physionet fetch is allowed; text modules only) [FC-22]; demo lake = `lake/demo` [ARCH-3] | note |
| EP-23/24/25/26/27 | contract already carries the tie-break sort keys — no edit [FC-3]; microbiologyevents stays `large` (EP-25) [FC-3]; dev tests request `dev_ready(step)` [VT-1]; identifier flags verified not added [FC-5]; the same "amended" line in each | five short blocks |
| EP-28 | reconcile via `inventory.reconcile()` semantics; strike "validate.sql predates 3.1" [FC-12]; re-run of `ep_17..20 --tier full` not needed once EP-168's fixtures land (state why) | note |
| EP-29 | `comment` exists on all 41 tables / 421 columns — item 1 becomes "expose", field is `comment` not `description`; ints via `fmt_int` [FC-17, FC-16] | note |
| EP-30 | free-text heuristic scoped to subject-keyed tables; `is_dim` and `meta.*` exempt; `drgcodes.description` / `hcpcsevents.short_description` allow-listed; `free_text` flag from EP-17 [ARCH-10, FC-18]; `mwh sql` output must never trip the deny/hook layer (JSON, thousands separators) [FC-16]; `snapshot_ids` dict in the audit line [ARCH-6] | note |
| EP-31/32/33 | `poe roadmap-check` not `python roadmap_check.py` [FC-20]; ints via `fmt_int` [FC-16] | one-line notes |
| EP-35/36 | `psutil` (from EP-19); `snapshot_ids` glossary; run manifest embeds `mwh doctor --json` (15 checks) | note |
| EP-40 | GEM landing `ext/vocab/gem/<version>/` per EP-14 convention [FC-24]; network fetch allowed (not a text module) [FC-22] | note |
| EP-42 | uses EP-43's `mwh disclose check` before EP-43 exists → write via EP-30's suppressor, file stays under `runs/` until EP-43's retroactive check; **or** move EP-43 earlier — owner call at EP-33; default = wording fix, no table change [FC-13] | note + EP-43 acceptance line |
| EP-43 | `tests/fixtures/disclose/bad_ids.csv` must not carry real-band ids (G4 scans .csv) — use `9x`-band ids and a pragma-free crafted case [FC-25]; band scanner must skip count-like cells [FC-16] | note |
| EP-56/57 | `MWH_APP_MEMORY_LIMIT`/`MWH_APP_THREADS`/`MWH_APP_TIER`/`MWH_APP_ROLE`/`settings.app_dir` do not exist → shipped names (`MWH_DUCKDB_APP_MEMORY_LIMIT`; threads shared) or new fields with parity; EP-57 tier switcher includes `fixture` and caches results, not connections (ARCH-1) [FC-14] | note |
| EP-138/142/148 | ED/Note fixture modules do not exist (`fixtures/` is hosp+icu) — the brief that needs them adds `fixtures/ed.py` / synthetic notes and regenerates (0.3.0+); concept coverage per `tests/fixtures/COVERAGE.md` | note |
| all re-plan briefs (EP-33, 54, 74, …) | cite `poe roadmap-check --strict`; refresh the workspace README § State; distribute new decisions as addenda; mirror Parked items [FC-20, DOC-11] | one shared sentence via the notation table |

Also: (a) `roadmap/README.md` "Notation used in briefs" table exists (EP-166) — add the sentence "pickup
notes never repeat what the table says"; (b) the acceptance clause "EP-0 … EP-n tests unchanged" is
rewritten in the roadmap README conventions paragraph as "a new EP must not *need* to edit an earlier
`test_ep*.py`; where a shipped fact legitimately changed, list the touched modules in the completion
note" (CMP-6); (c) `EP-16` Depends row: leave as is (owner deferral) — the linear order carries it;
(d) EP-13's amendment: `docs/resources/` exists (raw-inventory.md) without `README.md` — EP-13 creates
it (FC-21).

## Out of scope

- Any code; any change to header facts other than those listed (none by default); writing full briefs
  (first re-plan that writes N+1 is EP-74).

## Verification / acceptance

- Every brief in the table carries the block; `uv run poe roadmap-check --strict` 0/0 (165 → 171 rows =
  171 briefs, parity/header/hashes/charters ok); `mwh guard --all-tracked` clean (dates hyphenated, no
  bare 8-digit tokens); the completion note lists briefs amended and states "no amendment needed" for
  any brief in EP-17 … EP-33 not in the table.
- Commit `docs(roadmap): reconcile P2/P3 briefs with shipped P1 + retro decisions (EP-170)`, then
  `docs(roadmap): record EP-170 commit hash`.
