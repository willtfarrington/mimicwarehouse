# EP-7 — Re-plan P0

**Size:** S · **Tier:** n/a · **Core/Stretch:** core · **Depends on:** EP-0 (Baseline & hygiene), EP-1 (Toolchain bootstrap (uv + CPython 3.13 + pyproject)), EP-2 (`mwh` CLI skeleton + `mwh doctor`), EP-3 (Config & data root + safety checks), EP-4 (Governance enforcement: pre-commit + `mwh guard`), EP-5 (Visual identity), EP-6 (`mwh verify EP-n` + roadmap_check.py) · **Blocks:** —

## Context

D-8 closes every phase with a re-plan EP: retro, timings, DECISIONS addenda, ☑ reconciliation via
`roadmap_check.py`, and — from EP-74 on — writing the next phase's full briefs. P0 and P1 both
already have full briefs (D-9), so this re-plan is the light form: reconcile, record what the
toolchain actually looks like (versions, group conflicts, doctor/guard behaviour), fold the P0
`## Parked` items into `final-roadmap.md`, and amend the P1 briefs (EP-8 … EP-16) wherever P0
changed an API name, command or convention they rely on (`get_settings()`, `Settings.layout`,
`catalog_path`, `mwh paths --create`, `mwh guard` rules, `mwh verify` semantics, the `ep_<n>`
markers, poe tasks). It also collects the owner-side items P0 could only ask about (GOVERNANCE §1
dates, Defender exclusion, claude.ai training toggle — D-38, GOVERNANCE §4). No code is written;
no data is touched. Commands run in `mimicwarehouse/`; git at the repo root.

## In scope

1. **Reconcile.** `uv run poe roadmap-check --strict` must exit 0 after the P0 table in
   `roadmap/README.md` shows ☑ + hash for EP-0 … EP-6 (fix any missing hash from `git log
   --oneline`); `uv run --group dev mwh verify EP-1` … `EP-6` and `uv run poe check` all green on
   the current tree; `uv run --group dev mwh doctor` and `mwh guard --selfcheck` pass.
2. **Retro table** appended to this brief as the completion note: one row per P0 brief — planned
   size, actual wall time (from the two commit timestamps and the session's own notes), what
   surprised (resolver fights, Windows probes, pre-commit environments), what was skipped or
   parked. Below it: the installed versions table copied from EP-1's completion note (uv, Python,
   DuckDB, Polars, pyarrow, pandas, Streamlit) and the doctor summary from EP-3.
3. **DECISIONS addenda** (`> **Addendum (date, EP-7).**` under the decision each refines): D-15
   (exact uv / CPython versions; `default-groups = ["dev"]`; the `ui`↔`gpu`/`text` conflict set and
   the reason `dev` stays co-installable with `ui`), D-38 (owner tuning status as finally recorded),
   D-39 (guard rules G1–G5 as shipped, the `mwh-guard: allow` pragma), D-29 (drive-detection
   heuristics and `forbidden_drives`), plus any new numbered decision (D-42 …) a P0 session had to
   make. Design facts that changed → dated notes in `DESIGN.md` (§2 versions, §3 data-root tree,
   §15 `doctor.py`); do not rewrite history.
4. **Mirror parked items** from EP-1, EP-3, EP-4, EP-5, EP-6 `## Parked → final-roadmap.md`
   sections into the matching tables of `roadmap/final-roadmap.md` (Cross-cutting for toolchain /
   secrets / brand; 36–38 for guard extras) using the four-column row format; strike through any
   README Risk P0 resolved (Risk 3's Streamlit/pyarrow item if EP-1 settled it; Risk 4's
   MAX_PATH/CRLF items) as `~~risk~~ **Resolved by EP-n (date)**`; add new risks discovered.
5. **Amend P1 briefs.** Read EP-8 … EP-16 against the P0 code: names of settings fields, layout
   keys, CLI commands, marker/poe conventions, `.pre-commit` behaviour, tier vocabulary. Edit in
   place with a leading `> **Amended at EP-7 re-plan (date).** <what changed and why>` line; do not
   change their Size / Tier / Core / Depends / Blocks header facts (that is a README table change
   and needs the owner). If P0 revealed a genuine toolchain fight that P1 will hit (wheel gaps,
   pyarrow, pyright), allocate the optional per-phase remediation slot as
   `roadmap/EP-<next free number>-toolchain-remediation-p1.md` (S; EP-164 at planning time),
   insert its row into the P1 table before EP-8, and commit
   `docs(roadmap): add EP-<n> — toolchain remediation (P1)`.
6. **Owner checklist** (ask, record in the completion note): GOVERNANCE §1 dates filled; Defender
   exclusion for the data root done; claude.ai training toggle off; ≥ 100 GB free confirmed by
   doctor; whether to proceed to EP-8. Commit `docs(roadmap): re-plan P0 (EP-7)` then tick EP-7
   with `docs(roadmap): record EP-7 commit hash`.

> **EP-7 pickup note (2026-08-17, written at the end of EP-6).** Two inputs that did not exist
> when this brief was written: (a) `uv run poe roadmap-check --json` (EP-6) is the reconciliation
> tool named in step 1 — it currently reports 0 errors and one warning (the planning commit
> `cd67743` ticked under EP-0 has no `(EP-0)` in its subject; decide: accept, or make the EP-0 row
> cite only `707e9b4` + `795a044`); (b) **roadmap Risk 12 / D-38 addendum (EP-6 → EP-7)**: the
> host runs Malwarebytes 5.1 Premium next to Defender; its Ransomware Protection quarantined the
> unsigned Git `bash.exe` on 2026-08-17. Owner state at hand-off (all done 2026-08-17, taken on
> the owner's word — not readable non-elevated): bash.exe restored + allow-listed; Malwarebytes
> full exclusions for the seven paths in the D-38 addendum (`C:\Program Files\Git`,
> `%APPDATA%\uv\python`, the workspace `.venv`, `C:\mimicdata`, `source material\`,
> `%LOCALAPPDATA%\Microsoft\WinGet\Packages\astral-sh.uv_*`, `%USERPROFILE%\.cache\pre-commit`);
> Ransomware Protection on; usage/threat statistics + sample submission confirmed **off**. Record
> that list in the step-6 owner checklist. **Owner decision (2026-08-17): allocate** the step-5
> remediation slot — write `roadmap/EP-164-toolchain-remediation-p1.md` (S, fixture, core,
> Depends on EP-3, Blocks EP-16) whose single scope item is a `mwh doctor` **`antivirus`** check:
> list `root/SecurityCenter2` `AntiVirusProduct` rows (name, `productState` decoded to
> enabled/up-to-date), **warn** when a real-time product other than Defender is present with a
> one-line reminder of the D-38 allow-list paths, **info** otherwise; the check cannot read either
> product's exclusion list non-elevated and must say so (like `defender`); ≤ 2 s, no admin,
> Windows-only (info elsewhere), same `_run`/CIM seam as the other probes so tests fake it; JSON
> shape unchanged otherwise. Insert its row into the P1 table before EP-8 and commit
> `docs(roadmap): add EP-164 — toolchain remediation (P1)` as step 5 already describes.

## Out of scope

- Writing or re-chartering briefs beyond P1 amendments (P1 is already full; P2 full briefs exist;
  first re-charter is EP-74).
- Any code change — a bug found here becomes a note in the owning brief's completion note plus a
  follow-up line in the P1 brief that first needs it.
- Capability-coverage re-audit — meaningful only from EP-33 onward (nothing is covered yet).

## Verification / acceptance

- `roadmap/README.md` P0 table shows ☑ hashes for EP-0 … EP-7; `uv run poe roadmap-check --strict`
  exits 0.
- Completion note on this brief contains the retro table, versions table, doctor summary and the
  owner-checklist answers; DECISIONS addenda present under D-15, D-29, D-38, D-39; `final-roadmap.md`
  contains every P0 parked item; README Risks updated.
- Each amended P1 brief carries the `> **Amended at EP-7 re-plan (date).**` line and still passes
  `roadmap_check` (header facts unchanged); the completion note lists which briefs were amended
  (or "none").

> **Completion note (2026-08-17).** Docs-only, one session (started 20:14 local), no code written,
> no data touched; commands in `mimicwarehouse/`, git at the repo root. Everything below was
> verified on the tree at `4f7749d` plus this session's edits.
>
> **Step 1 — reconcile.** `uv run --group dev mwh verify EP-1` … `EP-6` → exit 0 each (10 / 27 / 32 /
> 44 / 34 / 47 tests); `mwh verify EP-0` docs-only line; `uv run poe check` green (ruff clean, pyright
> 0 errors, **194 passed** in 13.5 s); `uv run --group dev mwh doctor` → exit 0, **8 pass · 0 warn ·
> 0 fail · 5 info** (statuses identical to EP-3's run; C: 414.9 / 951.5 GB free); `mwh guard
> --selfcheck` → 16 rows ok (14 probes + `pre-commit-config` + `hook-installed`); `uv run poe
> roadmap-check` → **0 errors**, 165 rows = 165 briefs (after the EP-164 row), 7 ☑, **1 warning**;
> `--strict` therefore exits 1. **Decision on the pickup-note item (a):** the EP-0 cell keeps its
> three hashes (`cd67743` + `707e9b4` + `795a044`) and the warning is *accepted*, because
> `tests/ep/test_ep06.py::test_real_roadmap_row_and_brief_parsing` pins `len(rows[0].hashes) == 3`
> and a re-plan writes no code — dropping the planning commit would turn `mwh verify EP-6` red, which
> is worse than one documented warning. Recorded as roadmap **Risk 14** and handed to **EP-164 optional
> item 6** (relax the pin to `>= 2`, then cite only the two `(EP-0)` commits); until then "0 errors,
> 1 warning (EP-0)" *is* the green state of `--strict`. This is the one acceptance line of this brief
> that is not met literally; the owner may instead authorise the one-line test change as a hotfix.
>
> **Step 2 — retro.** Wall times are the transcript window of each brief's session (first → last
> message, local time, UTC−4) with the `feat` commit time in brackets; P0 was executed on one day,
> 2026-08-17, after the planning session of 2026-08-16 (13:31–17:38, commit `cd67743`).
>
> | Brief | Planned | Actual | What surprised | Skipped / parked |
> |---|---|---|---|---|
> | EP-0 Baseline & hygiene | S ≈ 30 min | ≈ 27 min (16:53–17:20; feat 16:59, owner follow-up 17:19) | planning docs were already committed by the owner (`cd67743`) → three-hash ☑ cell; `.claude/settings.json` `Read(**/*.csv)` is project-relative (probe under `%TEMP%` readable, in-repo probe refused); `*.duckdb.new/.tmp` added to `.gitignore`; renormalize was a no-op | nothing parked; all four owner items answered the same day |
> | EP-1 Toolchain bootstrap | M ≈ 60 min | ≈ 14 min (17:30–17:44; feat 17:42) | `uv sync --no-build` fails on the sdist-only `autograd-gamma` → allow-list in `test_ep01`; no pyarrow fork fight (uv unified core + `ui` on 24.0.0); pyright pulls its Node runtime on first run; `mwh` ImportError until EP-2 (expected) | parked: uv workspace split (PY-2) |
> | EP-2 `mwh` CLI + doctor | S | ≈ 10 min (17:54–18:04; feat 18:03) | the "warn until EP-3" `data_root` case never arose (owner had created `C:\mimicdata`); the BitLocker Shell-COM probe returns nothing on the Google Drive virtual volume; GiB labelled "GB"; Windows PowerShell (not pwsh) for the probe | nothing parked |
> | EP-3 Config & data root | M ≈ 60 min | ≈ 31 min (18:10–18:41; feat 18:40) | `mwh paths` crashed on `≥` under cp1252 → ASCII; pydantic wraps `ValueError` → errors subclass `RuntimeError`; `.env`/`mwh.toml` anchored at the workspace root, not CWD; `env_ignore_empty`; `test_ep02` had to follow the 13-check contract | parked: keyring secrets (CFG-1) |
> | EP-4 Guard + pre-commit | S | ≈ 16 min (18:47–19:04; feat 19:03) | pre-commit stashes *unstaged* edits → the first refusal proof failed for the wrong reason (stage code first); `git cat-file --batch -z` needs git ≥ 2.40 (have 2.55); the guard caught its own docstring example on first run | parked: gitleaks-style secret scan (GOV-3) |
> | EP-5 Visual identity | S | ≈ 10 min (19:06–19:16; feat 19:15) | Streamlit accepts only generic font families; Altair 6 `alt.theme.register` decorator API; SVGs render-checked in headless Edge in the scratchpad (no PNG committed) | parked: raster logos + brand font (BRAND-1) |
> | EP-6 verify + roadmap_check | S | ≈ 15 min to commit (19:18–19:33; feat 19:33); session ran to 20:12 (≈ 54 min) for the Malwarebytes incident + this brief's pickup note | `--strict` red on the planning commit; cp1252 again → `_console_safe`; **post-acceptance: Malwarebytes Ransomware Protection quarantined `bash.exe`** during a scratchpad `cp -r`/`sed -i`/`rm -rf` (Risk 12, D-38 addendum, D-42) | parked: `roadmap_check --fix` (RM-1, mirrored by EP-7) |
> | EP-7 Re-plan P0 | S | this session (20:14 → commit) | `test_ep06` pins EP-0's hash count → `--strict` cannot go green without code (Risk 14 → EP-164 item 6); EP-4's parked item already sat in Cross-cutting (GOV-3), left there rather than moved to §36–38; a Bash heredoc for this very note was refused by the shell — Write/Edit it is (D-42) | — |
>
> Totals: EP-0 … EP-6 ≈ **3 h 20 min** of session time (16:53 → 20:12, incl. owner Q&A and the
> incident) against ≈ 4 h 30 min planned (S+M+S+M+S+S+S); the two M briefs came in at ¼ and ½ of
> budget, the S briefs at ⅓–½. Two recurring surprises: (1) console encoding (cp1252) bit twice —
> Risk 13; (2) the Windows endpoint stack bit once, after acceptance — Risk 12 / D-42. No brief was
> split, none re-scoped, no `⏱` job launched (P0 has none). Every commit was hook-guarded
> (`mwh guard --staged` + ruff + hygiene hooks); no violation was ever committed.
>
> **Installed versions** (EP-1 completion note, lock of 2026-08-17; unchanged at EP-7 — `mwh doctor`
> `python`/`uv`/`duckdb` pass on the same numbers):
>
> | Tool / package | Version | | Package | Version |
> |---|---|---|---|---|
> | uv | 0.12.5 | | scipy | 1.18.0 |
> | CPython (managed) | 3.13.15 | | statsmodels | 0.14.6 |
> | duckdb (**exact pin**) | 1.5.5 | | lifelines | 0.30.0 |
> | polars | 1.43.2 | | scikit-learn | 1.9.0 |
> | pyarrow (core **and** ui) | 24.0.0 | | altair | 6.2.2 |
> | pandas | 3.0.5 | | streamlit (`ui`) | 1.61.1 |
> | numpy | 2.5.2 | | vegafusion / vl-convert-python / plotly (`ui`) | 2.0.3 / 1.9.0.post1 / 6.9.0 |
> | pytest / pytest-xdist / hypothesis | 9.1.1 / 3.8.0 / 6.165.10 | | pydantic / pydantic-settings | 2.13.4 / 2.15.0 |
> | ruff / pyright | 0.16.3 / 1.1.411 | | typer / rich / pyyaml / jinja2 | 0.27.1 / 15.0.0 / 6.0.3 / 3.1.6 |
> | poethepoet / pre-commit | 0.48.0 / 4.6.2 | | git (host) | 2.55 |
>
> Group facts: `default-groups = ["dev"]`; `ui` isolated from `gpu`/`text` (`[[conflicts]]` in the
> lock), `dev` co-installable with `ui`; `gpu`/`gpl`/`text` still empty; one sdist in the lock
> (`autograd-gamma`, allow-listed — D-15 addendum).
>
> **Doctor summary** (EP-3 completion note, reproduced at EP-7 with the same statuses): exit 0 —
> `python` pass (CPython 3.13.15 in `.venv`) · `uv` pass (0.12.5) · `duckdb` pass (1.5.5 == pin) ·
> `settings` info ("sources: defaults only · .env absent · mwh.toml absent · source_root present ·
> tier=dev · k=11 · allow_remote=false") · `disk_free` pass (C: 415.2 → **414.9** / 951.5 GB free) ·
> `data_root` pass (`C:\mimicdata` exists, writable, fixed NTFS) · `temp_dir` pass
> (`C:\mimicdata\tmp\duckdb`, same volume, max 150GB) · `cloud_mounts` info (D: Google Cryptomator
> cryptoFs remote · G: Google Drive FAT32 fixed · repository on C:) · `defender` info (not elevated —
> exclusions unreadable; owner ran the elevated `Add-MpPreference` at the EP-0 follow-up) · `bitlocker`
> pass (C: on) · `power_scheme` info (Balanced · AC power mode: Best performance) · `gpu` info (NVIDIA
> RTX PRO 2000 Blackwell Laptop, 8151 MiB, driver 595.71) · `longpaths` pass (registry 1,
> `core.longpaths=true`) — **8 pass · 0 warn · 0 fail · 5 info**.
>
> **Step 3 — DECISIONS / DESIGN.** Addenda written under **D-15** (exact versions, `default-groups`,
> the conflict set and why `dev` stays with `ui`, the `autograd-gamma` allow-list decision), **D-29**
> (the five drive heuristics in order, `forbidden_drives`, same-volume temp, `min_free_gb`; the
> two-product exclusion reading of "keep the copy secured"), **D-38** (owner tuning status table as
> finally recorded, incl. the seven Malwarebytes paths and telemetry off), **D-39** (G1–G5 as
> shipped, the `mwh-guard: allow` pragma, modes, hook order, the deny-rule finding); new numbered
> decision **D-42** (endpoint security stays two products, both on; sessions adapt their I/O pattern
> — Write/Edit not heredocs, no burst scratch loops, resumable long writers, quarantine checked
> first) in § Addenda. DESIGN dated notes: §2 (endpoint security = two products; console code page;
> versions re-verified) and §15 (P0 module map as real; planned `antivirus` check at EP-164;
> convention notes for P1+ authors). §3's tree note (EP-3) needed no change.
>
> **Step 4 — parked items / risks.** EP-1 (PY-2), EP-3 (CFG-1), EP-4 (GOV-3), EP-5 (BRAND-1) were
> already mirrored by their own sessions; EP-7 mirrored EP-6's `roadmap_check --fix` as **RM-1**
> (Cross-cutting) and marked the EP-6 brief. Judgment call: EP-4's secret-scanning item stays in
> Cross-cutting next to CFG-1 (which it references) rather than moving to §36–38. EP-2 and EP-0 parked
> nothing. README Risks: Risk 3's Streamlit/pyarrow fragment ~~struck~~ (Resolved by EP-1) + EP-7 note
> on the allow-list rule; Risk 4's MAX_PATH and CRLF fragments ~~struck~~ (Resolved by EP-0/EP-2);
> new **Risk 13** (cp1252 console vs rich/Unicode) and **Risk 14** (`--strict` red by one accepted
> warning); Risk 12 (Malwarebytes) was written at the end of EP-6 and now points at EP-164.
>
> **Step 5 — P1 amendments and the remediation slot.** `roadmap/EP-164-toolchain-remediation-p1.md`
> written (S · fixture · core · Depends on EP-3 · Blocks EP-16) with the owner-decided single scope
> item — `mwh doctor` **`antivirus`** (`root/SecurityCenter2` products, `productState` decoded,
> warn on a non-Defender real-time product with the seven-path reminder, info otherwise, ≤ 2 s,
> non-elevated, same `_run`/`_powershell` seam) — plus an *optional* item 6 (the `test_ep06` hash-pin
> relaxation, for the owner to confirm or strike); row inserted in the P1 table before EP-8;
> `roadmap_check` parses it (165 rows = 165 briefs, 0 errors); committed as `docs(roadmap): add
> EP-164 — toolchain remediation (P1)`. Header facts of EP-8 … EP-16 unchanged (EP-16's Depends
> deliberately not extended with EP-164 — a table change the owner may make; the linear order and
> EP-164's `Blocks: EP-16` already carry the dependency). **Amended briefs — all nine, EP-8 … EP-16**, each with a
> leading `> **Amended at EP-7 re-plan (2026-08-17).**` block plus in-place edits marked "amended EP-7";
> `roadmap_check` header/parity still 0 errors. What changed and why (the P0 facts P1 had guessed wrong):
> **EP-8** — hatchling-only backend (no setuptools include rule; honours `.gitignore`); `mwh guard` G1 does not
> know bare `.gz`; G4 flags only isolated 8-digit tokens starting 1/2/3; the two pre-commit fixer hooks would
> rewrite upstream SQL → add an `exclude:` for the vendor tree; `%TEMP%` is not Malwarebytes-excluded. **EP-9** —
> `mwh schema` joins `DIAGNOSTIC_COMMANDS` (validated-settings contract would otherwise block a drift check on
> a mis-set root); `check-yaml`/`check-json` hooks; `duckdb_settings("app")` for the DDL test. **EP-10** —
> `require_free_space` is module-level, not a `get_settings()` member; no `MWH_DATA_ROOT` env var exists →
> recipe reads `mwh paths --json`; `lake/manifests/raw/` is not a layout key; "dev" in the header ≠ a pytest
> tier (EP-12 comes later); Malwarebytes now excludes `source material\` too, ARW diagnosis line. **EP-11** —
> fixer hooks vs byte-identical CSVs; G4 scans every CSV column, not only ids; `workspace_root()` CWD fallback;
> `mwh fixtures` diagnostic-command choice. **EP-12** — `MWH_TEST_TIER` collides with `Settings`
> (`extra="forbid"`, `.env.example` parity test) → `PYTEST_TIER`; the pytest ladder is a subset of
> `config.Tier` (`demo` excluded, `default_tier` ignored); `pytest_plugins = ["pytester"]`; keep the
> `tier(name):` registration prefix (`test_ep01`); "EP-0 … EP-11 unchanged". **EP-13/14/15** — `mwh verify` runs
> pytest when the docs test module exists (`pytestmark` required, EP-5 precedent); G4 date/number hygiene;
> `gpl`/`text` are opt-in groups and only `ui`↔`gpu`/`text` conflict; EP-14 one landing convention
> (`ext/vocab/<source>/<version>/`, built from `layout["ext"]`, not a layout key); EP-15 exact PMID rule and
> "twelve" caveats. **EP-16** — `EP-16a`-style names do not parse (`_BRIEF_FILE`/`_ROW`/`resolve_ep`) → next
> free number (EP-165) like EP-164; `mwh doctor` cannot confirm Defender/LongPaths (info/warn only; "green" =
> no `fail`); EP-164 row + 14th check + Malwarebytes in the P2-readiness list; `%MWH_DATA_ROOT%` → layout;
> exact `poe roadmap-check` command and the Risk 14 caveat; pre-commit no-op acceptance for vendor/fixture
> trees. No Size / Tier / Core / Depends / Blocks header fact was changed.
>
> **Step 6 — owner checklist** (answers on record; nothing new was asked this session because every
> item except the last was answered at the EP-0 follow-up / EP-6 hand-off):
>
> | Item | Answer | Source |
> |---|---|---|
> | GOVERNANCE §1 dates filled | **yes** — CITI 2024-01-29 · MIMIC-IV 3.1 DUA 2026-08-15 · ED 2.2 DUA 2024-02-02 · Note 2.2 DUA 2024-02-02 · renewal due 2027-01-29 | GOVERNANCE.md §1, EP-0 follow-up (`795a044`) |
> | Defender exclusion for the data root | **done** (elevated `Add-MpPreference -ExclusionPath 'C:\mimicdata'`) — owner's word, unreadable non-elevated | EP-0 follow-up; `mwh doctor` `defender` info |
> | Malwarebytes allow list (Risk 12 / D-38) | **done** — seven paths: `C:\Program Files\Git`, `%APPDATA%\uv\python`, workspace `.venv`, `C:\mimicdata`, `source material\`, `%LOCALAPPDATA%\Microsoft\WinGet\Packages\astral-sh.uv_*`, `%USERPROFILE%\.cache\pre-commit`; `bash.exe` restored + allow-listed; Ransomware Protection on; usage/threat statistics + sample submission off | EP-6 hand-off (owner, 2026-08-17 evening) |
> | claude.ai training / "improve the model" toggle | **off** | EP-0 follow-up (owner) |
> | ≥ 100 GB free confirmed by doctor | **yes** — 414.9 / 951.5 GB free on C: | `mwh doctor` `disk_free` pass, EP-7 |
> | Proceed to EP-8? | **owner to confirm** at hand-off — with EP-164 now first in P1 (the doctor `antivirus` check), then EP-8; and whether to authorise the one-line `test_ep06` relaxation (Risk 14) as a hotfix or leave it to EP-164 item 6 | this note |
>
> **Commits.** `docs(roadmap): add EP-164 — toolchain remediation (P1)` → `docs(roadmap): re-plan P0
> (EP-7)` (this note, DECISIONS/DESIGN addenda, README risks + P1 amendments, final-roadmap RM-1)
> → `docs(roadmap): record EP-7 commit hash`. Hashes are recorded in `roadmap/README.md`.
