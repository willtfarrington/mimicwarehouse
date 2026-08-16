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
