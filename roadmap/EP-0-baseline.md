# EP-0 — Baseline & hygiene

**Size:** S · **Tier:** n/a · **Core/Stretch:** core · **Depends on:** — · **Blocks:** EP-1 (Toolchain bootstrap (uv + CPython 3.13 + pyproject)), EP-7 (Re-plan P0)

## Context

The planning session of 2026-08-16 left the repository at commit `2d08314` with a dirty working
tree: the three design docs (`mimicwarehouse/DESIGN.md`, `GOVERNANCE.md`, `DECISIONS.md`),
`CLAUDE.md`, the roadmap (`roadmap/README.md`, `roadmap/final-roadmap.md`, every
`roadmap/EP-*.md` brief), the rewritten READMEs, and the three enforcement files that must exist
before any data code does — `.gitignore` (data patterns), `.gitattributes` (LF normalisation;
data-shaped extensions marked binary) and `.claude/settings.json` (deny rules, D-39). Nothing under
`source material/` except `README.md` may ever be tracked (GOVERNANCE §2–3; D-29, D-30). This brief
captures that state as a clean baseline so every later EP produces a reviewable diff, sets the git
option Windows needs for MAX_PATH (`core.longpaths`), and records the owner-side Windows tuning
status that D-38 assigns to EP-0/EP-3 (BitLocker on C:, Defender exclusion for the data root,
`LongPathsEnabled`, power plan, cloud-sync mounts on G:/D:). No toolchain exists yet, so everything
here is git + PowerShell, and no data file is opened. Git root = repository root
(`C:\Users\willi\Documents\DATA\mimicwarehouse`); run every command there.

## In scope

1. **Exclusion audit before staging anything.**
   - `git ls-files "source material"` must list only `source material/README.md`;
     `git ls-files -o --exclude-standard "source material"` must print nothing (paths only are ever
     printed — never open, size, or grep files there).
   - `git check-ignore -v` on probe *strings* (create no files): `source material/mimic-iv-3.1/hosp/patients.csv`
     → ignored by the `source material/*` rule; `mimicwarehouse/foo.parquet`,
     `mimicwarehouse/warehouse/dev.duckdb`, `mimicwarehouse/runs/audit.jsonl`,
     `.claude/settings.local.json`, `mimicwarehouse/.env` → ignored;
     `mimicwarehouse/tests/fixtures/hosp/patients.csv`, `mimicwarehouse/.env.example`,
     `.claude/settings.json`, `mimicwarehouse/.streamlit/config.toml` → **not** ignored.
   - Additive fixes only if a probe fails (this brief is the owner's approval for additive data
     patterns, CLAUDE.md §6); also add `*.duckdb.new` and `*.duckdb.tmp` (the EP-21
     build-to-`.new`-and-swap suffixes). Never loosen a rule.
   - `.claude/settings.json` parses (`Get-Content .claude/settings.json | ConvertFrom-Json`).
     Deny-rule smoke test with a synthetic probe only: write a two-line `probe.csv` (`a,b` / `1,2`)
     in the session scratchpad, attempt to Read it with the Read tool, expect a denial, delete it.
     Never probe with a real data path.
2. **Git options.** `git config core.longpaths true` (repo-local; unset at planning). Then
   `git add --renormalize .` so every already-tracked text file is stored LF as `.gitattributes`
   (`* text=auto eol=lf`) demands; `core.autocrlf` is irrelevant once the attributes file exists.
3. **Owner tuning status (D-38) — check, ask, record.** Non-elevated probes only:
   `LongPathsEnabled` = `(Get-ItemProperty HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem).LongPathsEnabled`
   (was 1 at planning); BitLocker on C: via
   `(New-Object -ComObject Shell.Application).NameSpace('C:\').Self.ExtendedProperty('System.Volume.BitLockerProtection')`
   (1 = on; `Get-BitLockerVolume` needs elevation) — the owner verified "on" on 2026-08-16; active
   power scheme `powercfg /getactivescheme`; mounted cloud/virtual drives
   `Get-Volume | Select-Object DriveLetter, FileSystemLabel` (G: = Google Drive, D: = Cryptomator at
   planning) and confirm the repo path is on C:. The Defender exclusion for `C:\mimicdata` cannot be
   read without elevation → ask the owner (done / not yet) and record the command they run elevated
   (`Add-MpPreference -ExclusionPath 'C:\mimicdata'`). Ask the owner for the GOVERNANCE §1 dates
   (CITI completion, three DUA acceptances, renewal due) and whether the claude.ai training toggle
   is off; fill only those blanks in `GOVERNANCE.md` §1 with what they dictate — no other
   GOVERNANCE edit. Record everything as a table in this brief's completion note and as
   `> **Addendum (date, EP-0).**` under **D-38** in `DECISIONS.md`.
4. **Baseline commits.** `git add -A`, review `git status --porcelain` — only docs, config and
   briefs; nothing data-shaped; nothing under `source material/` but the README. Commit
   `chore(mimicwarehouse): planning baseline — design docs, governance, roadmap, guard config (EP-0)`.
   Tick EP-0 in `roadmap/README.md` (☑ + short hash), commit `docs(roadmap): record EP-0 commit hash`.
5. **Root README status line.** Under "Project status" add one line: "Baseline committed (EP-0,
   <date>); toolchain arrives with EP-1." Nothing else in the README changes.

## Out of scope

- Installing uv / Python, `pyproject.toml`, `.venv` → EP-1 (Toolchain bootstrap).
- The `mwh guard` hook and `.pre-commit-config.yaml` → EP-4 (until then `.gitignore` +
  `.gitattributes` are the only guard).
- Creating `C:\mimicdata` or any data-root layout → EP-3 (Config & data root).
- Any inventory or reading of `source material/` contents → EP-10 (Raw inventory manifest ⏱),
  through the loader/safe modules only.
- Editing design-doc content beyond the D-38 addendum and the GOVERNANCE §1 blanks → EP-7 and later.

## Verification / acceptance

- `git status` clean after the two commits; `git log --oneline -3` shows the `chore(… (EP-0)` and
  `docs(roadmap): record EP-0 commit hash` commits.
- `git ls-files "source material"` → exactly `source material/README.md`;
  `git ls-files | Select-String -Pattern '\.(csv|csv\.gz|parquet|duckdb|wal|jsonl|pkl|joblib|pt|safetensors)$'`
  → no output (synthetic fixtures do not exist yet).
- `git config --get core.longpaths` → `true`; `git ls-files --eol | Select-String 'i/crlf'` → no output.
- Every probe in item 1 gives the stated result; the deny-rule smoke test refused the synthetic
  `probe.csv`; `.gitignore` contains `*.duckdb.new`.
- Completion note appended to this brief with the D-38 status table (LongPathsEnabled, BitLocker,
  power scheme, cloud mounts, Defender exclusion done/not-yet, GOVERNANCE §1 filled yes/partial)
  and the matching addendum present under D-38 in `DECISIONS.md`.

> **Completion note (2026-08-17).** Executed by one Claude session, git + PowerShell only; no data
> file opened. The planning docs had already been committed by the owner as `cd67743` (the
> "dirty tree" in Context was gone), so item 4's baseline commit is that hash plus this brief's
> hygiene commit (two-hash ☑ box in `README.md`).
>
> **Item 1 — exclusion audit.** `git ls-files "source material"` → exactly `source material/README.md`;
> `git ls-files -o --exclude-standard "source material"` → nothing. `git check-ignore -v` probes:
> `source material/mimic-iv-3.1/hosp/patients.csv` → `.gitignore:15 source material/*`;
> `mimicwarehouse/foo.parquet` → `*.parquet`; `mimicwarehouse/warehouse/dev.duckdb` → `warehouse/`;
> `mimicwarehouse/runs/audit.jsonl` → `runs/`; `.claude/settings.local.json` → exact rule;
> `mimicwarehouse/.env` → `.env`; not ignored: `mimicwarehouse/tests/fixtures/hosp/patients.csv`
> (negation `!mimicwarehouse/tests/fixtures/**/*.csv`), `mimicwarehouse/.env.example` (`!.env.example`),
> `.claude/settings.json`, `mimicwarehouse/.streamlit/config.toml`. All as stated. Additive fix:
> `*.duckdb.new` and `*.duckdb.tmp` added to `.gitignore` (and marked `binary` in `.gitattributes`);
> `mimicwarehouse/x.duckdb.new` / `.tmp` now ignored anywhere, not only under `warehouse/`.
> `.claude/settings.json` parses (`ConvertFrom-Json`, 67 deny rules). Deny-rule smoke test with a
> synthetic two-line `probe.csv`: (a) in the session scratchpad under `%TEMP%` the Read tool
> **returned the file** — `Read(**/*.csv)` without a leading `//` is project-relative and does not
> reach paths outside the repo; (b) the same probe placed at `mimicwarehouse/probe.csv` (gitignored)
> was **refused** by Read ("directory that is denied by your permission settings"), by Bash `cat`
> and by PowerShell `Get-Content`. Probe deleted. Recorded in `roadmap/README.md` Risks §8 and the
> D-38 addendum; no rule loosened, no settings change (deny rules already carry explicit
> `//C:/mimicdata/**` and `source material/…` paths, which is where the real data lives).
>
> **Item 2 — git options.** `core.longpaths` was unset → set to `true` (repo-local).
> `git add --renormalize .` produced no changes: `git ls-files --eol | Select-String 'i/crlf'` was
> already empty (all tracked files stored LF).
>
> **Item 3 — D-38 owner tuning status** (non-elevated probes; same table as the D-38 addendum):
>
> | Check | Result 2026-08-17 | Status |
> |---|---|---|
> | `LongPathsEnabled` | 1 | done |
> | BitLocker C: (`System.Volume.BitLockerProtection`) | 1 | on |
> | Power scheme / Win11 power mode | Balanced `381b4222…`; AC overlay `00000000…` (default, **not** Best performance) | **owner to-do** |
> | Cloud / virtual mounts | D: Cryptomator (`cryptoFs`), G: Google Drive (FAT32); repo on C: (NTFS, 416 GB free); `C:\mimicdata` absent (EP-3) | ok |
> | Defender exclusion `C:\mimicdata` | not readable without elevation | **ask owner** (`Add-MpPreference -ExclusionPath 'C:\mimicdata'`, elevated) |
> | GOVERNANCE §1 dates + claude.ai training toggle | not yet dictated in this session | **partial — pending owner**; §1 blanks untouched |
>
> **Item 4/5.** Commits: `cd67743` (owner, planning baseline) + this hygiene commit
> (`.gitignore`/`.gitattributes` additions, README status line, D-38 addendum, Risks §8 note, this
> note), then `docs(roadmap): record EP-0 commit hash`. Root README gained the single status line
> "Baseline committed (EP-0, 2026-08-17); toolchain arrives with EP-1."
>
> **Open for the owner** (does not block EP-1): set power mode to Best performance when plugged
> in; run the elevated Defender exclusion; dictate the GOVERNANCE §1 dates and confirm the claude.ai
> training toggle is off — a follow-up `docs(governance): fill §1 dates (EP-0)` commit records them.
