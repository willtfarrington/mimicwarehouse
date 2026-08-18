# mimicwarehouse — DECISIONS

Architecture-decision log. Numbered decisions are **settled with the project owner**
(they/them) in the planning session of **2026-08-16** after ~60 clarifying questions
across stack, data architecture, methods, UI, governance and roadmap process, informed by
a research pass over the 2026 MIMIC-IV / Python / Windows tooling landscape. Later
sessions append addenda (`> **Addendum (date, EP-n).** …`) under the decision they refine
and add new numbered decisions at the end; nothing is rewritten.

Format: **D-n Title.** Decision. *Why.* *Alternatives considered.*

---

## Purpose & scope

**D-1 Tie-breaker purpose = portfolio / employability.** When depth, breadth, polish
and speed conflict, prefer the demonstrable, documented, reproducible "pre-employment
v1". Audience: **both** data-science/ML hiring managers and clinical-informatics readers →
docs carry two reading paths. *Why:* the owner's stated goal for the DATA portfolio.
*Alternatives:* personal research instrument; open-source community tool; learning
vehicle.

**D-2 Horizon ≈ 3 months at several sessions/week; EP sizes S ≈ 30 min, M ≈ 1 h,
L ≈ 2 h.** Anything larger is split. Yields ~160 briefs, tagged core/stretch. *Why:* the
owner's split-when-in-doubt rule and hupsim's lesson that long sessions die uncommitted.
*Alternatives:* S/M/L = 1/2/4 h with ~110 briefs; 6-month horizon.

**D-3 MIMIC-IV-Note = optional late track.** Loaded into a segregated store late (P10,
after linkage), one representative text workflow (search + concept/negation extraction +
linkage to structured events); everything else → `final-roadmap.md`; go/no-go at the P7
re-plan. *Why:* separate DUA, highest-risk asset, GPU/time pressure. *Alternatives:*
first-class module in v1; excluded from v1.

**D-4 MIMIC-IV-ED enters through the Linkage Wizard.** Core warehouse = hosp + icu; ED
is ingested later (EP-142) as the real-data test of the additional-data ingestion &
linkage capability. *Why:* proves the wizard on real data with shared keys. *Alternatives:*
stage ED on day one; both.

**D-5 Clinical themes vary per category.** Each capability's representative workflow
picks its own best-fit clinical theme (portfolio variety); the tracer bullet is
first-ICU-stay adults → in-hospital mortality. *Alternatives:* a single sepsis-3 anchor;
AKI; ventilation.

**D-6 Signature depth = prediction + assessment + leakage/drift.** Three representative
workflows and the most polish; every other category exactly one. *Alternatives:* causal /
target-trial; cohort-tooling; survival/longitudinal.

**D-7 Deep-learning workflow = pretrained tabular foundation model (TabPFN-class) on
structured features vs GBM.** VRAM-bounded, licensed weights only, CPU fallback; a small
sequence model (GRU/GRU-D) is stretch. *Alternatives:* clinical text encoder (depends on
notes), time-series FM, EHR event-sequence FM (MEDS/FEMR).

**D-8 Ordering = foundation → early tracer bullet → breadth; re-plan EP at every phase
boundary; capstone/showcase EP per phase + final showcase phase.** *Alternatives:* strict
foundation-first; vertical slices from the start; ad-hoc addenda only.

**D-9 Brief depth = full briefs for P0–P4 now, charter briefs for P5–P11.** Each re-plan
writes full briefs for phase N+1 and re-charters N+2 (cross-phase edges pin two phases
ahead). *Alternatives:* everything full now; full P0–P2 only.

**D-10 Explicit resource-gathering EPs** for repos/awesome lists, ontologies/vocabularies,
papers/chapters reading list, open companion datasets (owner template steps 2–3).

**D-11 Visual identity early** — one S brief (wordmark, light+dark chart-safe palette,
Altair/Streamlit themes, README banner) so every later screenshot is consistent.

**D-12 Democratization = bootstrap script + docs site + demo mode** on the ODbL
MIMIC-IV Demo. *Alternatives:* README only; full PyPI packaging + CI.

## Stack

**D-13 Python throughout.** No Rust or JS toolchain in v1. *Why:* ~30 of 38 categories
exist only as Python libraries; DuckDB/Polars give compiled-engine speed from Python.
*Alternatives:* Python + Rust hot paths (PyO3); Rust-first (owner's hupsim precedent).

**D-14 Native Windows** (PowerShell + uv). Docker only for optional future services.
*Why:* full RAM/NVMe bandwidth to 98 GB of CSVs; CUDA via the installed driver.
*Alternatives:* WSL2 (3–10× slower on /mnt/c or duplicate the data); Docker Compose.

**D-15 uv-managed CPython 3.13, one venv** (`python-preference = only-managed`; system
3.14 untouched). scispaCy (requires < 3.13) only via a separate 3.12 uv project if ever
needed — never a workspace member (requires-python is intersected). *Why:* verified
cp313 Windows wheels for every library in the stack; spaCy has no cp314 wheels; dowhy caps
< 3.14. *Alternatives:* system 3.14 + 3.12 sidecar; 3.12 everywhere.

> **Addendum (2026-08-17, EP-7).** As shipped by EP-1 and re-verified at the P0 re-plan:
> **uv 0.12.5** (winget, user scope, `%LOCALAPPDATA%\Microsoft\WinGet\Links\uv.exe`; cache
> `%LOCALAPPDATA%\uv\cache`), **uv-managed CPython 3.13.15** (`%APPDATA%\uv\python`;
> `mimicwarehouse/.python-version` = `3.13`; `.venv` in the workspace), system CPython 3.14.7
> untouched. `pyproject.toml` `[tool.uv]`: `package = true`, `python-preference = "only-managed"`,
> **`default-groups = ["dev"]`** — so `uv run mwh …` and `uv run --group dev mwh …` are the same
> environment and briefs may write either; `conflicts = [[ui, gpu], [ui, text]]` was accepted by
> uv 0.12.5 while `gpu`/`text` are still empty and appears in `uv.lock` `[[conflicts]]`. **Why `dev`
> stays co-installable with `ui`:** page tests must import pytest next to Streamlit
> (`--group dev --group ui`), so `dev` is not in the conflict set; `ui` is isolated only from the
> heavy groups (`gpu`: torch/xgboost, `text`: sentence-transformers) that may pull `pyarrow ≥ 25`
> against Streamlit 1.61.1's `pyarrow<25`. On the 2026-08-17 lock (100 packages) that fight did not
> happen — uv unified both resolver forks on **pyarrow 24.0.0** — so one venv serves core + dev + ui;
> the fork machinery is in place for the day they diverge (parked PY-2 = uv workspace split if `ui`
> ever needs `gpu`/`text` together). Exact pins: `duckdb==1.5.5` (one DuckDB version in every
> process, DESIGN §6; `test_ep01` asserts it) and `streamlit==1.61.*`; everything else floats in
> `uv.lock`. **Decision EP-1 asked EP-7 for:** the wheel-availability check
> (`test_ep01::test_uv_lock_every_package_has_a_wheel_for_this_interpreter`) **keeps its allow-list**
> — `autograd-gamma 0.5.0` (lifelines transitive, pure Python, no wheel ever published, builds in
> < 1 s with no compiler) is the only sdist in the lock; vendoring or replacing a transitive of a core
> dependency is not worth it. Rule for later briefs: a new dependency that is sdist-only may be
> allow-listed only if it is pure Python (say so in the completion note); a compiled sdist is refused
> — find a wheel-bearing alternative or park the item. Also recorded: pyright 1.1.411 downloads its
> Node runtime to the user cache on first `poe typecheck` (no system change); `uv python
> update-shell` was not run — everything goes through `uv run`. *Alternatives considered at EP-7:*
> vendor `autograd-gamma`; drop lifelines to an optional group (rejected — survival is core, P6).

**D-16 CPU-first; GPU is an opt-in late track.** `gpu` dependency group installs torch
from `https://download.pytorch.org/whl/cu130` (`explicit=true`; PyPI torch is CPU-only on
Windows; cu126 lacks sm_120); XGBoost `device="cuda"` as comparator; LightGBM CPU is the
workhorse; PyMC + nutpie for Bayesian (JAX has no Windows CUDA). *Alternatives:* GPU from
the start; ignore GPU.

**D-17 DuckDB + Parquet lake canonical; layers raw CSV → typed Parquet → DuckDB
conformed catalog → derived concepts → marts; Polars primary, pandas at library
boundaries.** *Alternatives:* Postgres in Docker; Polars-only; ClickHouse; CSV → DuckDB
native only; pandas primary.

**D-18 Tiers fixture / demo / dev (5 %) / full.** Every EP passes tests on fixture+dev
and records a full-tier run with timing where meaningful; long full jobs run as
resumable background jobs verified by the next EP. *Alternatives:* sample only until late;
full only; full runs batched per phase.

**D-19 Adopt mimic-code `concepts_duckdb` (MIT), vendored at a pinned commit, tested,
fixes ported; re-derive only what is missing.** *Alternatives:* re-derive everything;
adopt as-is untested.

**D-20 Custom lightweight transform runner (`mwh build`).** YAML DAG of SQL/Python
steps, tier-aware, manifests/snapshot ids, timings. dbt-duckdb and SQLMesh → final-roadmap.
*Why:* provenance capture and tier switching are the point; ~600 LOC we control.

**D-21 App = Streamlit 1.61 multipage "Lab" app, one process; Altair/Vega-Lite
(+VegaFusion) primary, Plotly for timelines; linked brushing essential on Explorer.**
*Alternatives:* marimo apps (ranked first by the research panel for a solo builder —
see Judgment calls), Panel/HoloViz, Dash, notebook-first, CLI-only.

**D-22 marimo for scratch notebooks only** (zero-output `.py`); canonical logic lives in
the package. *Alternatives:* Jupyter + nbstripout; none.

**D-23 Reporting = Jinja2 → Markdown + self-contained HTML; PDF via Typst.** Formats
MD + HTML + PDF (no DOCX). *Alternatives:* Quarto (parked for narrative case studies);
notebook export.

**D-24 Run/provenance store = DuckDB `runs` views over per-run JSON sidecars +
append-only JSONL ledgers.** *Alternatives:* MLflow (parked as mirror); plain files.

**D-25 Protocol freeze = YAML protocol → content hash → registry entry before run;
amendments logged; runs must cite a frozen hash.** *Alternatives:* git commit as freeze;
documentation only.

**D-26 Raw provenance = local manifest (SHA256/size/rows) + row-count reconciliation vs
mimic-code `validate.sql`.** Plain CSVs cannot be checked against PhysioNet's
`SHA256SUMS.txt` (covers `.csv.gz` only). *Alternatives:* re-download `.csv.gz` (parked);
skip.

**D-27 Fixtures = synthetic mini-MIMIC generator (ids ≥ 90 000 000) committed +
on-demand MIMIC-IV Demo 2.2 (+ ED Demo) tier.** *Alternatives:* demo only; synthetic only.

**D-28 Latency ≤ 5 s typical on full data via marts; interactive pages default to
dev.** *Alternatives:* ≤ 2 s always; whatever DuckDB gives.

## Governance

**D-29 Data placement.** Repo + raw CSVs stay under `Documents` (local-only, BitLocker
on); derived data in a short data root outside the repo (`C:\mimicdata`, `MWH_DATA_ROOT`);
nothing on G:/D:. *Alternatives:* inside repo `data/`; inside `source material/`.

> **Addendum (2026-08-17, EP-3).** Enforced in code (`mimicwarehouse.config`): a data root
> is **refused** (`UnsafeLocationError`, exit 2, nothing created) when its volume is not
> `DRIVE_FIXED`, its filesystem is not NTFS/ReFS, its volume label matches
> `google drive|onedrive|dropbox|\bbox\b|cryptomator|icloud`, the path lies under
> `%OneDrive%`/`%OneDriveConsumer%`/`%OneDriveCommercial%`, or its drive letter is in
> `forbidden_drives` (default `["G","D"]`, configurable via `MWH_FORBIDDEN_DRIVES`); the
> DuckDB temp dir must share the data-root volume. The same test is warn-only for the
> repository tree (`mwh doctor` `cloud_mounts`). Every `mwh` command receives validated
> settings; only `doctor` and `paths` run against an unsafe root, to report it. Verified on
> this machine: D: (remote, cryptoFs, "Google Cryptomator") and G: (fixed, FAT32, "Google
> Drive") are each refused on three independent criteria; `Test-Path G:\mimicdata` stays
> false. Judgment calls: `box` is word-bounded (Toolbox ≠ Box); relative paths in `.env` /
> `mwh.toml` are anchored at the workspace root rather than the shell CWD; an empty
> `MWH_*` value means "default"; keyring/secrets storage parked (final-roadmap CFG-1).

> **Addendum (2026-08-17, EP-7).** Confirmed as shipped, nothing changed since EP-3. The
> drive-detection heuristics, in the order `location_problem` applies them: (1) `GetDriveTypeW`
> must return `DRIVE_FIXED` (remote / removable / CD / RAM disk / unknown are refused — this alone
> catches the Cryptomator vault, which mounts as a *network* drive); (2) `GetVolumeInformationW`
> filesystem must be NTFS or ReFS (catches Google Drive's FAT32 virtual volume; exFAT/FAT USB
> sticks too); (3) the volume label must not match `google drive|onedrive|dropbox|\bbox\b|
> cryptomator|icloud` (case-insensitive; `box` word-bounded); (4) the path must not lie under
> `%OneDrive%` / `%OneDriveConsumer%` / `%OneDriveCommercial%`; (5) the drive letter must not be in
> **`forbidden_drives`** (`Settings` field, default `["G","D"]`, override `MWH_FORBIDDEN_DRIVES=
> ["G","D","E"]` as JSON) — the letter rule is the belt for the day a sync client changes label or
> filesystem. `duckdb_temp_dir` must share the data-root volume (`check_same_volume`); `mwh paths
> --create` (now) and `mwh build` (EP-19) additionally require `min_free_gb` (100) free. The same
> probes are warn-only for the repository (`mwh doctor` `cloud_mounts`, `info` on this machine:
> "D: Google Cryptomator (cryptoFs, remote) · G: Google Drive (FAT32, fixed) …; repository on C:
> (fixed)"). Off Windows every probe returns `unknown` and only rules (4)–(5) apply. EP-7 doctor run:
> `data_root` pass (`C:\mimicdata`, fixed NTFS, writable), `temp_dir` pass, 414.9 / 951.5 GB free.
> New since EP-3: keeping the local copy "secured" (GOVERNANCE §1) now also means the data root and
> `source material\` are excluded from **both** real-time products (D-38 addenda) so no scanner
> ever uploads a detected object from either location.

**D-30 Keep plain CSVs untouched** (~180 GB total footprint). *Alternatives:* re-gzip;
delete after verified Parquet.

**D-31 Claude sessions: aggregate-only via a safe-query wrapper** (k = 11 suppression,
no identifiers, no note text, audit-logged) + `CLAUDE.md`. *Alternatives:* schema-only;
same access as the owner.

**D-32 Row display allowed in-app for the owner** behind an explicit toggle with audit
entry; never exported; never in tool output. *Alternatives:* aggregate-only everywhere;
unrestricted.

**D-33 Small cells: warn at n < 11 in-app; suppress n < 11 on export/commit** with
complementary suppression. *Alternatives:* suppress everywhere; n < 5; none.

**D-34 MIT license; permissive-only imports; GPL tools only in the optional `gpl`
extra** (e.g. scikit-survival for one EP). *Alternatives:* Apache-2.0; allow GPL freely;
no exceptions.

**D-35 Vocabularies: free first** (ICD-9/10 dims, LOINC, RxNorm, ATC, AHRQ CCSR/
Elixhauser/Charlson code sets, CMS GEMs); UMLS/SNOMED/OMOP Athena as later optional EPs
(owner has no UTS account yet). *Alternatives:* Athena early; MIMIC dims only.

**D-36 Future data = reference/knowledge tables + other PhysioNet datasets.** Wizard =
profile → map concepts/units → validate keys/cardinality → measure linkage coverage →
commit, with a license register. *Alternatives:* external context datasets; generic only.

**D-37 Roadmap format = hupsim verbatim** (flat `EP-n-slug.md`, S/M/L, Depends-on/Blocks,
Context / In scope / Out of scope / Verification, ☑ commit-hash tables, two commits per
EP) with additions (Tier, Core/Stretch, ⏱, Parked). Design docs `DESIGN.md`,
`GOVERNANCE.md`, `DECISIONS.md` in `mimicwarehouse/`; `CLAUDE.md` at the repo root.
*Alternatives:* phase-prefixed ids; zero-padded ids.

**D-38 Owner-side Windows tuning** (owner performs; EP-0/EP-3 check and record):
Defender real-time exclusion for `C:\mimicdata` only, `LongPathsEnabled` (registry +
reboot), "Best performance" power plan when plugged in. *Alternatives:* none.

> **Addendum (2026-08-17, EP-0).** Non-elevated status probes run from the EP-0 session
> (repo root `C:\Users\willi\Documents\DATA\mimicwarehouse`, on C:):
>
> | Item | Probe | Status 2026-08-17 |
> |---|---|---|
> | `LongPathsEnabled` | `HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem` | **1 (done)** |
> | BitLocker on C: | `System.Volume.BitLockerProtection` via Shell COM | **1 (on)** — matches owner check of 2026-08-16 |
> | Power plan / mode | `powercfg /getactivescheme`; registry `ActiveOverlayAcPowerScheme` | **Balanced** (scheme `381b4222…`, overlay `00000000…` = default) → **not yet** "Best performance"; owner: Settings › System › Power & battery › Power mode › *Best performance* (plugged in), or elevated `powercfg /overlaysetactive ded574b5-45a0-4f42-8737-46345c09c238` |
> | Cloud / virtual mounts | `Win32_LogicalDisk` | C: = Windows NTFS fixed (951 GB, 416 GB free); D: = Cryptomator vault (`cryptoFs`, network); G: = Google Drive (FAT32 virtual). Repo is on C:. `C:\mimicdata` does not exist yet (EP-3) |
> | Defender exclusion `C:\mimicdata` | `Get-MpPreference` → "Must be an administrator to view exclusions" | **unknown / pending owner** — owner runs elevated `Add-MpPreference -ExclusionPath 'C:\mimicdata'` (before or at EP-3) |
> | GOVERNANCE §1 dates (CITI, 3 DUAs, renewal) + claude.ai training toggle | owner-only | **pending owner input** — blanks left untouched; fill at EP-0 follow-up or EP-3/EP-7 |
> | `core.longpaths` (git, repo-local) | `git config --get core.longpaths` | set to `true` by EP-0 |
>
> Also recorded by EP-0: the `.claude/settings.json` deny rules refused a synthetic
> `probe.csv` placed **inside the repo** via Read, Bash `cat` and PowerShell `Get-Content`,
> but the same probe under `%TEMP%` (session scratchpad) was readable — `Read(**/*.csv)`
> patterns without a leading `//` are project-relative. `C:\mimicdata` is covered by the
> explicit `//C:/mimicdata/**` rules; `source material/` by path rules. No rule loosened.

> **Addendum (2026-08-17, EP-0 follow-up).** Owner answers, same day: (1) **Defender
> exclusion done** — owner created `C:\mimicdata` (empty; EP-3 lays out the tree) and ran
> `Add-MpPreference -ExclusionPath 'C:\mimicdata'` in an elevated PowerShell (not readable
> non-elevated; taken on the owner's word). (2) **Power mode switched** to Best performance —
> re-probe: `ActiveOverlayAcPowerScheme = ded574b5-45a0-4f42-8737-46345c09c238` (scheme
> stays Balanced `381b4222…`; the Win11 overlay is what matters). (3) **claude.ai training
> toggle confirmed off** (GOVERNANCE §4 item 6). (4) **GOVERNANCE §1 filled**: CITI
> 2024-01-29 · MIMIC-IV 3.1 DUA 2026-08-15 · MIMIC-IV-ED 2.2 DUA 2024-02-02 · MIMIC-IV-Note 2.2
> DUA 2024-02-02 · CITI renewal due 2027-01-29. All D-38 items are now done; EP-3 re-checks
> `LongPathsEnabled`, BitLocker and free disk in `mwh doctor`.

> **Addendum (2026-08-17, EP-6 → for EP-7).** D-38 assumed Defender was the only real-time
> protection on the host. It is not: **Malwarebytes 5.1 Premium** (installed since 2026-04,
> real-time stack reconfigured 2026-08-15 23:04, licence refreshed 2026-08-16) is registered in
> `root/SecurityCenter2` next to Defender and keeps its **own** allow list. Found because its
> Ransomware Protection module (ARW) killed and quarantined the unsigned MSYS2
> `C:\Program Files\Git\usr\bin\bash.exe` at 19:28:43–51 during EP-6's scratchpad check
> (`cp -r roadmap` + `sed -i` + `rm -rf`, 166 files in seconds = its ransomware heuristic;
> `mbamservice.log`: "WinVerifyTrust failed … NOT whitelisted … kill this process …
> Quarantining"; quarantine id `64336cee-9a93-11f1-b147-38186875c8ac`; the executable was also
> uploaded to Malwarebytes' cloud). Defender was not involved that day; its own record is three
> `Trojan:Win32/ClickFix.FFQ!MTB` hits on 2026-08-16 15:34–15:35 against Claude Code heredoc
> command lines (`bash -c … cat > roadmap/EP-1… <<'EOF'`; action Remove = process killed,
> file untouched — the reason sessions write files with the Write/Edit tools, not heredocs).
> **Owner actions (2026-08-17):** bash.exe restored from quarantine and allow-listed; Malwarebytes
> folder exclusions (malware/ransomware/PUP) for `C:\Program Files\Git`,
> `%APPDATA%\uv\python`, `mimicwarehouse\.venv`, `C:\mimicdata`, `source material\`; Ransomware
> Protection left **on**. **D-38 is amended:** the owner-side list now reads "Defender exclusion
> for `C:\mimicdata` **and Malwarebytes allow-list entries for the toolchain (Git, uv, uv's
> CPython, the venv, pre-commit's hook venvs) and both data locations**"; the two remaining
> entries — `%LOCALAPPDATA%\Microsoft\WinGet\Packages\astral-sh.uv_*` (unsigned `uv.exe`) and
> `%USERPROFILE%\.cache\pre-commit` — were added by the owner the same evening (seven paths in
> total). Governance reading (GOVERNANCE §2/§4): the data-root and source-material entries are a
> **disclosure control** — a product that ships detected objects to a vendor cloud must never
> have reason to look at them; the owner confirmed Malwarebytes › Settings › usage/threat
> statistics and sample submission are **off** (2026-08-17). Neither product's exclusion list is
> readable non-elevated, so — like the Defender item — these are taken on the owner's word and
> `mwh doctor` cannot verify them. Impact on EP-0…EP-6: none (the incident post-dates every
> acceptance run; `roadmap_check` and all 194 tests are green after the restore). **Owner
> decision (2026-08-17): allocate** the P0 toolchain-remediation slot — EP-7 writes
> `EP-164-toolchain-remediation-p1.md` (S) adding a `mwh doctor` `antivirus` check
> (`root/SecurityCenter2` products; warn when a non-Defender real-time product is present;
> states that exclusions are unreadable non-elevated). Roadmap Risk 12 mirrors this note.

> **Addendum (2026-08-17, EP-7 — owner tuning status as finally recorded for P0).**
>
> | Item | Status at the P0 re-plan | Verified by |
> |---|---|---|
> | `LongPathsEnabled` = 1 + repo `core.longpaths=true` | done | `mwh doctor` `longpaths` pass |
> | BitLocker on C: | on | `mwh doctor` `bitlocker` pass |
> | Power mode "Best performance" (AC overlay `ded574b5…`) | done | `mwh doctor` `power_scheme` info: "Balanced · AC power mode: Best performance" |
> | Defender real-time exclusion `C:\mimicdata` | done (owner, EP-0 follow-up) | owner's word — not readable non-elevated (`defender` info) |
> | **Malwarebytes 5.1 Premium** allow list — seven paths: `C:\Program Files\Git`, `%APPDATA%\uv\python`, workspace `.venv`, `C:\mimicdata`, `source material\`, `%LOCALAPPDATA%\Microsoft\WinGet\Packages\astral-sh.uv_*`, `%USERPROFILE%\.cache\pre-commit`; `bash.exe` restored + allow-listed; Ransomware Protection **on**; usage/threat statistics + sample submission **off** | done (owner, 2026-08-17 evening) | owner's word — not readable non-elevated; product *presence* checked by `mwh doctor` `antivirus` from **EP-164** |
> | GOVERNANCE §1 dates (CITI 2024-01-29 · MIMIC-IV 3.1 DUA 2026-08-15 · ED 2.2 DUA 2024-02-02 · Note 2.2 DUA 2024-02-02 · renewal 2027-01-29) | filled | GOVERNANCE.md §1 (EP-0 follow-up) |
> | claude.ai "improve the model" / training toggle | off (owner, 2026-08-17) | owner's word (GOVERNANCE §4 item 6) |
> | ≥ 100 GB free on C: | 414.9 / 951.5 GB free | `mwh doctor` `disk_free` pass (EP-7 run) |
>
> D-38 therefore reads, finally: *owner-side Windows tuning = LongPathsEnabled + reboot, Best
> performance power mode, Defender exclusion for `C:\mimicdata`, **and** Malwarebytes exclusions
> for the toolchain (Git, uv, uv's CPython, the venv, pre-commit's hook venvs) and both data
> locations, with both products' telemetry/sample submission off.* `mwh doctor` verifies the first
> two, reports the exclusions on the owner's word, and (EP-164) names every real-time product it
> can see. Doctor summary at EP-7 (2026-08-17): exit 0, **8 pass · 0 warn · 0 fail · 5 info** —
> identical statuses to the EP-3 run.

> **Addendum (2026-08-17, EP-164).** Product presence is now checked (names and Security Center
> states only, never either exclusion list) by `mwh doctor` `antivirus` from EP-164: on this host
> it lists Malwarebytes (`productState 0x060000` — "real-time off" *per Security Center*, i.e. not
> the registered WSC antivirus, so Defender stays active; its own modules run regardless, D-42) next
> to Windows Defender (`0x061100`, on) and **warns**, naming the seven D-38 paths that must be
> excluded in Malwarebytes too — the allow list itself stays on the owner's word (elevated
> verification parked, `final-roadmap.md` DOC-1). Doctor summary at EP-164: exit 0, **8 pass ·
> 1 warn · 0 fail · 5 info** (the one warn is this row, by design).

**D-39 Enforcement of the Claude data policy = `CLAUDE.md` + safe-query wrapper +
repo-shared `.claude/settings.json` deny rules** (reading `source material/**` except
`*.md`, `C:\mimicdata\**`, `*.csv/*.parquet/*.duckdb`, the `duckdb` executable). A
PreToolUse output-scanning hook is parked. *Alternatives:* prose only; hook.

> **Addendum (2026-08-17, EP-7).** The git-side layer of D-39 shipped in EP-4 as `mwh guard`
> (`guard.py`), rules **G1–G5 as shipped**: **G1** data-shaped extension anywhere (`.csv .csv.gz
> .parquet .duckdb .duckdb.wal .duckdb.new .duckdb.tmp .wal .jsonl .feather .arrow .pkl .joblib
> .skops .pt .safetensors .npy .npz .h5`) except under `mimicwarehouse/tests/fixtures/`, where only
> `.csv .csv.gz .parquet .jsonl .json .yaml` pass; **G2** anything under `source material/` other
> than `*.md` — refused by name, the file is never opened; **G3** `.ipynb` with outputs /
> `execution_count` (or invalid JSON) and any path with a `__marimo__` segment; **G4** in text files
> (`.py .md .yaml .yml .json .toml .sql .txt .csv .jsonl .html .svg .cff .ps1 .ini .cfg` or no
> extension, UTF-8, no NUL) an isolated 8-digit token starting 1/2/3 whose value lies in the
> `subject_id` / `hadm_id` / `stay_id` bands — exempt only when the same line carries the pragma
> **`mwh-guard: allow`** (documented examples); compact `YYYYMMDD` dates are *not* exempt (write
> ISO dates with hyphens); the message masks the token (`1*******`), names the band, never quotes
> the line, and caps at 25 rows per file; **G5** any blob > 20 000 KiB. Modes: `mwh guard [PATHS…]`
> (working tree), `--staged` (index blobs — what the commit records; the hook's mode), `--all-tracked`
> (`git ls-files` / `git ls-tree -r <rev>`, the EP-163 sweep primitive), `--selfcheck` (EP-0
> `.gitignore` / `.gitattributes` probes, `.pre-commit-config.yaml` carries `mwh-guard`, hook
> installed); exit 0 / 1 / 2; `guard` is a `DIAGNOSTIC_COMMANDS` member so a mis-set
> `MWH_DATA_ROOT` never blocks a commit. Hook order (repo-root `.pre-commit-config.yaml`):
> `mwh-guard` → `ruff-check` → `ruff-format --check` → `pre-commit-hooks v6.0.0`
> (`check-added-large-files --maxkb=20000`, merge-conflict, yaml, toml, json, end-of-file-fixer,
> trailing-whitespace `--markdown-linebreak-ext=md`, detect-private-key). Verified at EP-4 with a
> refused real commit attempt; at EP-7 `mwh guard --selfcheck` = 16 rows ok (14 probes +
> `pre-commit-config` + `hook-installed`) and every P0 commit was hook-guarded. Session-side layers unchanged: `CLAUDE.md`, the `.claude/settings.json` deny rules
> (EP-0 finding: `Read(**/*.csv)` is project-relative — the explicit `//C:/mimicdata/**` and
> `source material/…` rules are what protect the real data, so the guard and EP-30 `safe_query`
> remain necessary layers), and `safe_query` (EP-30). The PreToolUse output-scanning hook stays
> parked (final-roadmap GOV-1); secret scanning parked (GOV-3).

**D-40 Remote content = code + docs + gated aggregates** — results committed only after
`mwh disclose check` passes and a `.disclosure.json` sidecar is recorded.
*Alternatives:* code + docs only; two repos.

**D-41 MIT now; repo public at v1.0.0 after a full-history guard sweep.**
*Alternatives:* public from day one; private indefinitely.

---

## Defaults assumed by the planning session (owner may veto any; say so and the
## brief-writing session updates the affected briefs)

Stack & repo: `mwh` typer + rich CLI · pydantic-settings (`MWH_` env + `.env` + TOML) and
pydantic models for cohort/phenotype/protocol specs (JSON-schema → UI forms) · poethepoet
tasks · pytest + hypothesis + DuckDB data checks · ruff + pyright(basic) · pre-commit +
`mwh guard` · semver tags + CHANGELOG + separate warehouse `build_id` · `.env` + keyring
for any future tokens · `MWH_ALLOW_REMOTE=false` gate · single process + engine threads +
joblib for CV · `if __name__ == "__main__"` guards (Windows spawn) · dependency groups
`core / dev / ui / gpu / gpl / text` with `[tool.uv] conflicts` isolating `ui` (Streamlit
pins `pyarrow<25`) — commands in briefs always name their groups · commit `uv.lock` ·
env-export hash in every run manifest · `roadmap_check.py` · `mwh verify EP-n` · commit
pairs `feat(mimicwarehouse): … (EP-n)` + `docs(roadmap): record EP-n commit hash` · slug
scope tokens (`stage-`, `ui-`, `cohort-`, `surv-`, `ml-`, `text-`, `report-`, `link-`,
`replan-`, `capstone-`).

Data: Hive `subject_bucket = subject_id % 100` (dims unpartitioned), sorted
`(subject_id, time)`, ZSTD-3, ~1 M-row groups; two-pass bucketed load for the large
tables, resumable per bucket, `store_rejects`; DuckDB `memory_limit` 36–40 GB, `threads`
12, `temp_directory` under the data root, `max_temp_directory_size` explicit,
`preserve_insertion_order=false`; one pinned DuckDB version; ≥ 100 GB free during builds;
single-writer rule with build-to-`.new`-and-swap catalogs opened `READ_ONLY`; audit / run
ledger / benchmark ledger as append-only JSONL under `runs/` with `runs.duckdb` views;
schemas `mimiciv_hosp/icu/ed/derived`, `meta`, `marts`; notes in a separate lake +
`notes.duckdb` attached on demand; naive timestamps + `anchor_year_group` era + relative
times; `dod` censoring rule; ICD-9→10 dual code sets; snapshot id = hash(manifests);
loader accepts `.csv`/`.csv.gz` + column maps (demo 2.2 → 3.1); MEDS-shaped events spine
excluding raw chartevents.

Methods: statsmodels + scipy (cluster-robust SEs by `subject_id` by default); lifelines
(+ hand-rolled Aalen–Johansen and cause-specific Cox; Fine–Gray has no lifelines/scikit-survival
implementation → hand-rolled IPCW/Geskus weighting or R `cmprsk`, parked in final-roadmap;
scikit-survival (GPL-3, `gpl` group) only for survival-ML/IPCW metrics); PyMC + nutpie +
ArviZ (+ Bambi); statsforecast + statsmodels; scikit-learn + LightGBM (CPU) + XGBoost
(CUDA comparator after the GPU EP); SHAP tree/linear only; statsmodels MICE (inference) +
sklearn imputers (prediction); own `boot`, `assess`, `causal`, `disclose`, `run` modules;
medspaCy + regex baseline; local sentence-transformers (CPU-capable); unit-of-analysis
registry (subject / hadm / icustay / edstay / icu_day / hour_bin / person_time / note);
`docs/analyses/NN-slug.md` case studies with "What it deliberately does not claim" +
Reproduction blocks (hupsim precedent).

## Judgment calls made during planning (owner saw these at plan approval)

- The research panel ranked **marimo-as-app** first for a solo builder; the owner chose
  **Streamlit** knowingly (employer recognition, conventional multipage/wizard shape).
  Streamlit is the app, marimo is scratch; a "marimo app lane" is parked in
  `final-roadmap.md`.
- The panel recommends **staging notes early (cheap) but analysing late**; the owner chose
  to load notes late → notes staging stays in P10 (EP-148); the P7 re-plan may pull the
  staging brief forward if disk/time allow.
- The panel recommends **re-downloading `.csv.gz` and deleting plain CSVs**; the owner
  chose to keep CSVs → parked as an optional EP ("checksum-verifiable raw").
- Bucket scheme `subject_id % 100`, dev = buckets 0–4, fixture ids ≥ 90 000 000 — chosen
  over a hash for SQL simplicity and guard recognisability.
- `.gitattributes` and `.claude/settings.json` are written in the planning session (docs/
  config), not deferred to EP-0, because they must exist before any data code.
- Numbering = planned execution order (allocation order, hupsim); a per-phase optional
  "toolchain remediation" S slot may be allocated at re-plan for wheel/version fights.
- The extension roadmap file is named `roadmap/final-roadmap.md` (owner wrote "final
  roadmap.md"; hyphenated for shell-friendliness).
- CLI name `mwh`; data root default `C:\mimicdata`; environment prefix `MWH_`.

## Addenda

*(sessions append new numbered decisions here, and refinements under the decision they refine)*

**D-42 Endpoint security stays two products, both on; sessions adapt their I/O pattern
(2026-08-17, EP-6 → recorded at EP-7).** The owner keeps Windows Defender *and* Malwarebytes 5.1
Premium real-time protection — including Malwarebytes' Ransomware Protection — enabled on the
host, and instead allow-lists the toolchain and both data locations in each product (D-38 addenda:
seven Malwarebytes paths, Defender exclusion for `C:\mimicdata`; telemetry / sample submission off
in Malwarebytes). Consequences for every Claude session and every brief: (1) sessions write files
with the Write/Edit tools, **never** shell heredocs (Defender killed three `bash -c … <<'EOF'`
command lines as `Trojan:Win32/ClickFix.FFQ!MTB` on 2026-08-16); (2) no burst copy / `sed -i` /
delete loops over hundreds of files in scratch directories from an unsigned process (that is the
ransomware heuristic that quarantined `bash.exe`); (3) long-running writers — the EP-17+ loader,
the EP-11/12 fixture generators, EP-10's hashing pass — run under the allow-listed managed
`python.exe` from the allow-listed `.venv`, log progress, and are resumable, so a killed process
costs a restart, not a corrupt lake; (4) a "process killed / binary vanished / access denied
mid-command" symptom is checked against Malwarebytes › Detection History › Quarantine and
`C:\ProgramData\Malwarebytes\MBAMService\logs\mbamservice.log` **before** Defender; (5) `mwh
doctor` names the products it can see (EP-164 `antivirus`) but takes both exclusion lists on the
owner's word. *Why:* the owner's endpoint policy is not the project's to change; the data-location
exclusions are also a GOVERNANCE §2 disclosure control (a scanner that uploads detected objects must
never have reason to look at MIMIC files). *Alternatives:* disable Ransomware Protection (rejected —
owner keeps it on); uninstall Malwarebytes (rejected); code-sign the toolchain (not possible for
uv-managed CPython / MSYS2 binaries).
