# EP-74 — Re-plan P4 (writes full P5, re-charters P6)

**Size:** M · **Tier:** n/a · **Core/Stretch:** core · **Depends on:** EP-73 (Capstone #2: EDA case study + screenshots) · **Blocks:** —

## Context

The phase-boundary re-plan (D-8) and the first one that also writes briefs: D-9 fixes full
briefs for P0–P4 at planning time and charters for P5–P11, each re-plan upgrading phase N+1 to
full and re-chartering N+2 (edges pinned two phases ahead). By now the P4 work has changed the
facts the P5 briefs must cite: `stats/` started early (`rates`, `subgroups`, `table1`,
`missing`), `viz/` (agg service, spec builders, export), `ui/` (shell, gate, cells, forms,
jobs), the marts (`icustay_first_day`, `icustay_hourly`, `hourly_population`), new CLI groups
(`mwh app|bench|export|stats`), the VegaFusion policy and whatever the Streamlit rerun model
taught us about the Freezer/Wizard question (DESIGN §21). This brief performs the retro,
reconciles ☑ hashes with `roadmap_check.py` (EP-6), mirrors parked items into
`final-roadmap.md`, and writes the P5 full briefs / P6 charters using the brief-writing rules
that produced this file (README "How to use", tier vocabulary, acceptance phrasing by class,
six-part definition of done, ≤ 6 In-scope items, sizes S/M/L). Docs-only: no data access.

## In scope

1. **Retro + reconciliation** — run `roadmap_check.py`: every P4 ☑ hash matches `git log`,
   table ↔ file parity; build the retro table (EP, planned size, actual wall time from
   completion notes, full-tier timings, surprises) and append it as this brief's completion
   note; strike resolved risks in `roadmap/README.md` § Risks (`~~risk~~ **Resolved by EP-n
   (date)**`); append DECISIONS.md addenda (e.g. under D-20/D-21/D-33: `stats/` in P4, `ui/`
   package, `mwh bench/export/stats`, VegaFusion policy, Streamlit verdict for Freezer/Wizard,
   any conflict-group change for `ui` tests) — never rewrite history.
2. **Parked mirror** — copy every P4 brief's `## Parked → final-roadmap.md` items into the
   matching category tables of `roadmap/final-roadmap.md` (add rows; keep the four-column
   format; note the source EP).
3. **Upgrade P5 to full briefs** (EP-75 … EP-90) — using the full template (Context / In scope
   ≤ 6 / Out of scope / Verification / Parked), citing the now-real modules and CLI names, the
   tier vocabulary, D-numbers, and per-class acceptance; each method brief names its
   representative workflow concretely (cohort/theme, exposure/outcome, method, validation,
   report artifact with claim-type label) from the well-supported MIMIC themes; keep header
   facts (Size, Tier, Core/Stretch, Depends on, Blocks) unless the retro changes them — then
   edit the P5 table in `roadmap/README.md` and the affected Blocks lines by hand and re-run
   `roadmap_check.py`; split any brief that would exceed L.
4. **Re-charter P6** (EP-91 … EP-101) — refresh Context/Scope sketch/acceptance sketch with
   facts learned (module names, marts, tiers, `gpl` group decision for Fine–Gray, IPCW needs
   from EP-92); keep the `> **Charter.**` note pointing at EP-90; keep header facts.
5. **Graph / coverage / governance checks** — dependency graph acyclic and every Depends-on
   numbered lower (add `--graph` to `roadmap_check.py` if missing); capability coverage table
   (38 rows) re-audited with P4 categories 1–8 and 32/36 annotated as tested workflows or gaps
   named; governance grep over `roadmap/*.md`: every brief cites a D-n and a tier; no
   raw-file read instructions (pandas CSV readers, the `duckdb` executable); no numbers in the real id bands (guard
   regex); every full-tier instruction is a background job — fix offenders in the briefs being
   written.
6. **Optional toolchain remediation + commit** — if P4 hit wheel/version fights (Streamlit /
   pyarrow, vegafusion, playwright, plotly), allocate the per-phase remediation slot as
   `roadmap/EP-<next free number>-toolchain-remediation-p5.md` (S) — the same integer/next-phase
   convention EP-7 uses (never a letter suffix: `roadmap_check.py` and the `ep_<n>` pytest markers
   parse integers only) — insert its row into the P5 table before EP-75, and commit
   `docs(roadmap): add EP-<n> — toolchain remediation (P5)`; commit `docs(roadmap):
   re-plan P4 — full P5 briefs, re-chartered P6` and record the hash in the README table.

## Out of scope

- Any code change (log needs as risks or as items in the P5 briefs).
- Re-chartering P7+ (EP-90 does P7); notes go/no-go (EP-127).

## Verification / acceptance

- `uv run --group dev python mimicwarehouse/scripts/roadmap_check.py` (or `uv run poe
  roadmap-check`) clean (hashes, parity, graph); all 16 P5 briefs are full (no `> **Charter.**`
  marker, ≤ 6 In-scope items, valid sizes/tiers, headers equal the README table); all 11 P6
  briefs remain charters with refreshed content and the EP-90 pointer.
- `final-roadmap.md` contains every P4 parked item; README risks struck/added; DECISIONS
  addenda appended; links resolve.
- Retro table appended as this brief's completion note; commit hash recorded.
