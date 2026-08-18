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
