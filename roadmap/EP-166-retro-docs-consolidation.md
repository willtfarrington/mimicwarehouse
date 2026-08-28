# EP-166 — Retro B: docs consolidation & status surface

**Size:** M · **Tier:** n/a · **Core/Stretch:** core · **Depends on:** EP-12 (Synthetic fixture generator B (icu) + pytest tier markers) · **Blocks:** EP-13 (Repos & awesome-lists inventory), EP-16 (Re-plan P1)

> **Origin.** Second retro brief (see EP-165 for the origin note; ledger =
> [`retro-2026-08-18-findings.md`](retro-2026-08-18-findings.md); owner decisions = D-43). Docs-only:
> no code, no data. `mwh verify EP-166` takes the docs-only shortcut (no `tests/ep/test_ep166.py`).

## Context

The dated-note discipline worked — DESIGN §15 notes, DECISIONS addenda and completion notes are accurate
and mutually consistent — but the *status prose a cold session reads first* is frozen at EP-1
(root README "Planning complete; no code yet", "mwh CLI arrives with EP-2"; workspace README "no data
code yet … from EP-8 on"; DESIGN intro/§15 header "nothing exists as code yet / (all planned)"; four
places say "D-1 … D-41", three say "164 briefs"), the same narratives are retold in four places
(DESIGN notes, DECISIONS addenda, roadmap Risks, completion notes — Risk 12 alone ≈ 2 900 chars), and
five completion notes deferred owner review points that never got a recorded verdict. The review also
settled three architecture facts that DESIGN must state before P2 codes them: the Windows catalog-swap
protocol, the layer snapshot-id definition, and the fixture/demo lake roots (code lands in EP-167;
the words land here). Owner decisions (2026-08-18, D-43): the single living status surface is
**`mimicwarehouse/README.md § State of the workspace`** (refreshed by every re-plan EP; CLAUDE.md §1 and
the root README point at it; DESIGN/DECISIONS notes cite completion notes instead of restating);
all deferred review points of EP-9/EP-10/EP-164 are **accepted as shipped**; catalog swap =
**rename-aside two-step**; snapshot id = **logical id + physical sha256**; lake roots =
`lake/fixture` + `lake/demo` (+ `lake/rejects`).

Ledger ids: DOC-2, DOC-3, DOC-4, DOC-11, DOC-18, CMP-2, CMP-3, ARCH-1, ARCH-2, ARCH-5, ARCH-6, INV-3,
FC-8 (+ low: DOC-5, DOC-7, DOC-9, DOC-10, DOC-12 … DOC-17, ARCH-11 …, INV-5 …, ENV-5 … — index rows
tagged EP-166).

## In scope

1. **Root `README.md`** (DOC-2) — replace the "Project status (2026-08-16)" block: P0 + EP-164 + EP-8 …
   EP-12 done (14 ☑ + retro), the `mwh` surface (`doctor paths guard verify schema inventory fixtures`),
   171 briefs / 13 phases after EP-165 … EP-170, `D-1 … D-43`, pointer to the workspace README § State;
   keep the banner and the doc table; strike nothing historical — rewrite only the status paragraph.
2. **Workspace `mimicwarehouse/README.md`** (DOC-3, DOC-11) — new **§ State of the workspace** right
   after the intro: table *module → EP → CLI → tests* for everything shipped (config/cli/doctor/guard/
   theme/verify/concepts+vendor/schema/inventory/fixtures + conftest tiers), the doctor summary line and
   test count as of the last re-plan, and an **"Environment realities"** list (D-42 rules, uv PATH
   restart, `PYTHONUTF8` via settings.json, both AV products + nine paths, cp1252 pointer, bare python
   = 3.14) that later re-plans refresh; fix the intro ("no data code yet … EP-8 on"), the Quick start
   (add `mwh schema list|show|ddl|check|transcribe`, `mwh inventory build|show|reconcile`,
   `mwh fixtures build`, `poe test-dev/test-full`, `poe vendor-mimic-code`), "13/14 checks", "15" → 18
   layout keys once EP-167 lands (write "15 (18 from EP-167)"), the "Planned layout" tree (concepts/,
   schema/, fixtures/, inventory.py exist; `app/`, `notebooks/` still planned), Contributing table only
   if EP-165 changed G1/G4 wording. Correct the "unknown MWH_* keys are rejected" sentence to "unknown
   keys in `.env`/`mwh.toml` are rejected; unknown `MWH_*` environment variables are reported by
   `mwh doctor` (EP-167)". Same correction in `.env.example`, `tests/README.md`, DESIGN §20 (CFG-1).
3. **DESIGN.md** — (a) intro + §15 header: "as of 2026-08-18 the P0/P1a modules exist; see the workspace
   README § State" (DOC-4); §15 tree: mark shipped modules with their EP + "shipped" and keep the plan
   for the rest; (b) **§6 dated note — catalog reader/writer protocol** (ARCH-1): the `.new` +
   `os.replace` swap is **not** atomic on Windows and fails (WinError 5) while any READ_ONLY handle is
   open; adopted protocol = *rename-aside two-step*: `os.rename(<tier>.duckdb → <tier>.duckdb.old)`
   (succeeds with readers open — DuckDB opens with FILE_SHARE_DELETE), `os.replace(.new → <tier>.duckdb)`,
   `os.remove(.old)` (delete-pending while a reader lives); readers keep serving the old snapshot;
   `open_catalog` retries on the sub-ms FileNotFoundError window; DuckDB's in-process instance cache is
   keyed on path → a process that still holds the old handle must close it before re-opening; same scheme
   for `runs.duckdb` (EP-30/35); the app re-opens on the next query (EP-57 caches results, not
   connections). Cross-link EP-21/30/35/57 (EP-170 amends the briefs); (c) **§5 dated note — lake
   directory swap** (ARCH-2): `os.replace(dir.new → dir)` fails when `dir` exists (empty or not) and
   renaming a directory holding an open file fails; EP-17 ships `paths.swap_dir(new, dest)` = restore
   stale `.old` if `dest` missing / rmtree stale `.old` / rename `dest → dest.old` / `new → dest` /
   rmtree `.old` with a PermissionError retry loop — crash-safe, not atomic; dev.duckdb views point at
   the same files, so a dev rebuild while the app is open needs the same courtesy; (d) **§11 identifier
   glossary + snapshot-id definition** (ARCH-5, ARCH-6, INV-3, FC-8): `raw_snapshot_id` (EP-10; all 41
   files), per-file `source_sha256` (EP-10 record; EP-17's `ManifestLine` carries both), `build_id`
   (EP-19), layer `snapshot_id` per `{core, derived, marts, notes}` = **logical** sha256 over the sorted
   JSON of `(schema, table, path, rows, schema_hash, source_sha256 or raw_snapshot_id, sort_keys,
   writer_version)` per file (the EP-10 pattern) — stable when raw + contract + code are unchanged; the
   dev id hashes only `dev_buckets` paths + unpartitioned tables and must not move when buckets 5–99
   finish; per-file Parquet `sha256` is integrity-only; catalog `build_id` + `core_snapshot_id` (EP-21),
   `run_id`, `audit_id`, protocol hash; every run/audit cites `snapshot_ids` (dict). Fix D-26's
   sentence "this id is the source manifest id" → "the per-file `source_sha256` plus this
   `raw_snapshot_id`" (addendum, not rewrite); (e) **§3 tree + §4** (ARCH-3 words): `lake/fixture`,
   `lake/demo`, `lake/rejects`; `warehouse/{fixture,demo,dev,full}.duckdb`; the fixture tier "built for
   keeps" lives there and the app/`mwh sql` may target `--tier fixture` (code: EP-167); (f) §2 note:
   console = `PYTHONUTF8=1` from settings.json + shared console (EP-167), Risk 13 reworded; (g) §20
   note: tier readiness fixtures + demo opt-in marker (words; code EP-168) and the "unknown env" fix.
4. **DECISIONS.md** — distribute **D-43** (written 2026-08-18) into addenda under the decisions it
   refines, keeping D-43 as the index: D-17 (sort-key tie-breaks adopted, microbiologyevents stays
   `large`, `structural_hash()`, csv-dialect constant, `allow_quoted_nulls=true` policy), D-18 (lake roots
   per tier, demo opt-in marker, tier readiness fixtures), D-24/D-26 (logical snapshot id + glossary),
   D-27 (fixture id floors 90/91/92/93 M, `GENERATOR_VERSION` 0.2.0, fixture-change protocol), D-29/D-38
   (nine AV paths; VS Code restart), D-39 (settings.json env/deny/allow; hook unparked; connectors),
   D-42 (CLAUDE.md carries the rules); **owner verdicts** (CMP-3) as `> **Addendum (2026-08-18, owner).**`
   lines: under D-17/EP-9 — `resprate` DOUBLE, the two `upstream_nullable` relaxations, the 13
   docs-sourced ED/Note FKs: accepted as shipped; under D-26/EP-10 — `pending` status, `reconcile` exit 1
   on mismatch, raw-int `--json`, docs page committed early, snapshot id allowed with `rows=null`:
   accepted; under D-38/EP-164 — presence-based `antivirus` warn rule and item 6: accepted. Add the
   D-15 note that psutil joins core at EP-19 (FC-9). Nothing rewritten.
5. **roadmap/README.md** — counts (171 briefs; sizes mix), "D-1 … D-43", the **"Notation used in
   briefs"** table under § How to use (CMP-5): `%MWH_DATA_ROOT%\x` / `$env:MWH_DATA_ROOT` =
   `get_settings().layout[...]` / `mwh paths --json` (no env var is set on this machine; never write
   `$env:MWH_DATA_ROOT` into a pwsh command line — it expands to empty); `roadmap_check.py` = `uv run poe
   roadmap-check [--strict] [--json]` ≡ `mwh verify --roadmap`; `DEV_BUCKETS` = `settings.dev_buckets`;
   "Command forms" = `uv run mwh …` ≡ `uv run --group dev mwh …`; ints in tracked Markdown are
   thousands-separated (`inventory.fmt_int`), `--json` output is never pasted (FC-16) — one sentence
   "this table overrides brief text"; Risks: Risk 12 and 13 trimmed to two lines + pointers (D-42 /
   DESIGN §2), Risk 8 gains the hook + connector sentence, **new Risk 15** = the retro summary (69/110
   findings, ledger link, EP-165…170), Risk 1 struck-through form kept; the retro paragraph in the P1
   ordering rationale (already added on 2026-08-18) checked.
6. **`tests/README.md`** — the "unknown env" sentence; a **"Changing the synthetic fixture"** subsection
   (FXT-3; hand-maintained here because `tests/fixtures/README.md` is generator-rendered and drift-tested):
   any change to `fixtures/{spec,vocab/*.yaml,hosp,icu,write}.py` or the contract → `uv run --group dev
   mwh fixtures build` → review `git diff mimicwarehouse/tests/fixtures/manifest.json` (CSVs are binary in
   `.gitattributes`) → bump `write.GENERATOR_VERSION` (patch = bytes of existing tables changed; minor =
   new tables/modules/spec keys) → one commit with CSVs + manifest + version; downstream tests read counts
   from `manifest.json` / `build_plan()`, never literals; byte identity assumes the locked numpy/polars.
7. **`final-roadmap.md`** — GOV-1 struck (Resolved by EP-165), RM-1 note unchanged, DOC-1 unchanged;
   FIX-1 note that EP-169 documents fixture concept coverage. **`NOTICE`** unchanged.
8. **Memory** — the session ends by updating the project-status memory to point at the README § State
   and CLAUDE.md §3 (retire the duplicated tooling notes).

## Out of scope

- Any code (EP-167/168/169), brief amendments (EP-170), GOVERNANCE/CLAUDE.md/settings.json (EP-165).

## Verification / acceptance

- Every quoted stale sentence in ledger DOC-2/3/4/11/18 and the low DOC-* rows is gone or corrected;
  all relative links resolve (`grep -o '](\S*\.md' | test -e`); `uv run poe roadmap-check --strict`
  0/0; `mwh guard --all-tracked` clean; `uv run poe test` still green (docs only).
- Commit `docs(mimicwarehouse): consolidate status docs — README § State, DESIGN §5/§6/§11 protocol + glossary, DECISIONS D-43 distribution + owner verdicts, roadmap notation table (EP-166)`, then `docs(roadmap): record EP-166 commit hash`.

> **Completion note (2026-08-28).** All eight In-scope items shipped in one session (M).
> Gates: `uv run poe test` **452 passed** (35.8 s) · `poe roadmap-check --strict` **0 errors /
> 0 warnings** (171 rows, 15 done) · `mwh guard --all-tracked` clean (432 files) and clean over
> the 15 edited working-tree files · all **211** relative `.md` links across the 185 tracked
> markdown files resolve — the three grep-level hits are not links (the regex lookahead
> `(?! README…)` in the EP-165 brief/ledger, and the ledger's two verbatim quotes of the stale
> text it was recording; the review record is append-only and was left untouched, EP-165
> precedent). Power mode confirmed Best performance (`ded574b5-…`) before the test run.
>
> **What landed where.** (1) Root README status block rewritten (2026-08-28; 16/171; `mwh`
> surface; pointer to the new § State). (2) Workspace README: new **§ State of the workspace**
> (module → EP → CLI → tests table, EP-165 gates line, Environment-realities list), rewritten
> intro, current Quick start (+ `schema`/`inventory`/`fixtures`/`poe test-dev`/`test-full`/
> `vendor-mimic-code`), corrected unknown-keys sentence, "15 (18 from EP-167)" layout notes,
> doc-table D-43 + resources rows, shipped-marked Layout tree (Contributing table needed
> nothing — EP-165 had already updated G1/G4). (3) DESIGN: intro + §15 header/tree de-staled;
> dated notes in §2 (console/`PYTHONUTF8`), §3 (tree growth: `lake/fixture|demo|rejects`,
> manifests, `runs.duckdb`, `.build.lock`), §4 (fixture built-for-keeps, `--tier fixture`),
> §5 (rename-aside dir swap + `_progress.json` placement + `partition_glob` pin; two-pass =
> `load_class`; 31-table stage scope), §6 (rename-aside catalog protocol, instance-cache
> caveat, one-build-connection rule, disposable catalogs), §11 (identifier glossary + logical
> snapshot id), §20 (tier readiness, demo opt-in marker, CFG-1 correction); §21 "Defender +
> Malwarebytes" fix (DOC-5); fixture byte total corrected to 5,370,674 here and in the EP-12
> note (DOC-13). (4) DECISIONS: D-43 distributed — addenda under D-15 (psutil, FC-9), D-17
> (item 9 + EP-9 owner verdict), D-18 (items 7–8), D-24 + D-26 (item 11 + EP-10 owner verdict
> + the "source manifest id" two-field refinement), D-27 (item 10), D-38 (EP-164 owner
> verdict), a D-43 closing addendum, and the keyring-parked parenthetical (DOC-17).
> (5) Roadmap README: D-1 … D-43 and 171-brief counts, the **"Notation used in briefs"** table
> (overrides brief text), Risk 1 and Risk 8's done fragments struck, Risk 8's hook + connector
> sentence, Risk 11 ≈ 160 h / 171, Risks 12/13 trimmed to two-line pointers, Risk 15 progress
> lines (EP-165/EP-166); the P1 ordering-rationale retro paragraph checked, accurate.
> (6) tests/README: corrected `PYTEST_TIER` rationale (CFG-1), PowerShell fallback form
> (CMP-9), EP-12 marker-probe sentence (DOC-14), new **"Changing the synthetic fixture"**
> protocol (FXT-3/D-43 item 10). (7) final-roadmap: FIX-1 gained the EP-169 `COVERAGE.md`
> note; GOV-1 was already struck by EP-165; RM-1/DOC-1 and `NOTICE` untouched. Plus: EP-6's
> placeholder link escaped (DOC-17), the stale `[project.scripts]` comment deleted (DOC-17),
> an EP-166 pickup note on EP-13 (DOC-15). (8) Project-status memory updated to point at
> § State and CLAUDE.md §3.
>
> **Owner-review points (deviations, alternatives, recommendations).**
> 1. *In-scope item 1 says "171 briefs / 13 phases"; written as **12 phases**.* The roadmap
>    has twelve `## Phase` sections (P0 … P11 — the retro added briefs, not a phase) and every
>    other document says 12, so 13 would have introduced a fresh inconsistency. Options:
>    (a) keep 12; (b) if the retro batch was meant to count as a phase, say so once in
>    roadmap README § How to use. Recommendation: (a).
> 2. *Item 2's "13/14 checks" fragment does not exist* — the public-flip commit (`08b15e8`)
>    had already refreshed the doctor sentences to "14 host checks". No action needed or taken.
> 3. *DOC-9 not implemented.* Its stale sentence lives in CLAUDE.md §5 (blanket "aggregates
>    only after `mwh disclose check` + sidecar", contradicting GOVERNANCE §3's manifest
>    exception and EP-10's committed page), but CLAUDE.md is this brief's Out-of-scope
>    (→ EP-165, which shipped without taking DOC-9), the ledger marks it **owner-decision**,
>    and D-43 records no verdict. Options: (a) owner confirms the GOVERNANCE §3 reading and a
>    one-sentence CLAUDE.md §5 amendment ("manifests of hashes/counts/schema need no sidecar;
>    before EP-43 nothing else derived from real data is committed") rides with EP-167;
>    (b) leave to EP-16. Recommendation: (a) — EP-10's precedent already relies on it.
> 4. *The owner's uncommitted no-AI-attribution edits rode into this commit*
>    (`.claude/settings.json`: `includeCoAuthoredBy: false` + `_readme` sentence; CLAUDE.md
>    §4(5) paragraph; both owner-authored 2026-08-28, present in the working tree before this
>    session). EP-165 precedent for pre-existing local edits; committing keeps the checkpoint
>    clean. Alternative: a separate owner commit. Recommendation: keep.
> 5. *Small out-of-letter touches, each argued by its ledger entry while the file was open:*
>    Risk 8's done owner items struck (DOC-12); ARCH-11 (one-build-connection) and ARCH-16
>    (disposable catalogs) folded into the §6 note; ARCH-12 (paths) into the §3/§5 notes;
>    ARCH-14 (stage-coverage scoping) into the §5 note.
> 6. *Left with their tags, not silently dropped:* **CMP-4** (owner: re-confirm the nine
>    Malwarebytes paths — the last two are still pending — and decide the doctor
>    "acknowledged" option), CMP-6 (acceptance-template rewording) and CMP-8 (doctor `git`
>    probe) → next re-plan, ARCH-13/ARCH-15 brief clauses → EP-170/re-plan.
> Ledger disposition: DOC-2/3/4/11/18, CMP-2 (this brief executing *is* the decided vehicle),
> CMP-3, ARCH-1/2/5/6 (words), INV-3, FC-8, FC-9 (D-15 note) implemented; low DOC-5/7/12/13/
> 14/15/17, CMP-5, CMP-9 (README half), ARCH-11/12/14/16 (words) implemented; DOC-8/10/16
> were already fixed by EP-165's CLAUDE.md rewrite; DOC-9 → owner (point 3); CMP-4 → owner;
> CMP-6/CMP-8, ARCH-13/ARCH-15 → EP-170/re-plan.
