# EP-90 — Re-plan P5 (writes full P6, re-charters P7)

**Size:** M · **Tier:** n/a · **Core/Stretch:** core · **Depends on:** EP-89 (Capstone #3) · **Blocks:** —

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-74 (Re-plan P4) before execution.

## Context

Phase-boundary re-plan (D-8, D-9): retro on P5, timings, DECISIONS addenda, ☑ reconciliation via
`roadmap_check.py`, mirroring of every P5 `## Parked → final-roadmap.md` section into
`roadmap/final-roadmap.md`, and — the main deliverable — full briefs for P6 (EP-91 … EP-101) plus
refreshed charters for P7 (EP-102 … EP-127), pinning what P5 fixed: the tidy schema (EP-77/79),
`GlmSpec` / `rcs()` (EP-79/80), `boot` adapters (EP-78), endpoint tables and competing-event
coding (EP-75/76), utilization definitions (EP-84). Docs-only (`n/a`), no data access.

## Scope sketch (refine at re-plan)

1. **Retro** — per-EP planned vs actual size, full-tier timings from the run / benchmark ledgers
   (`uv run --group dev mwh runs …`), toolchain fights (statsmodels + pandas 3, formulaic,
   Bambi / pytensor / nutpie, statsforecast, ruptures) → `> **Completion note (date).**` on this
   brief; ☑ hashes for EP-75 … EP-89 in `roadmap/README.md`; Risks strike-throughs.
2. **DECISIONS addenda** — standing defaults refined in P5 (cluster-robust default, `B` default,
   RCS knot rule, ordinal disposition mapping, follow-up caps, unobservable-readmission caveat) as
   `> **Addendum (date, EP-n).**` entries; DESIGN.md dated note for module / CLI additions
   (e.g. `stats/inference.py`, endpoint registry path).
3. **Full P6 briefs** (EP-91 … EP-101) in the full template (Context / In scope ≤ 6 / Out of scope
   / Verification / Parked), header facts checked against the master table, lifelines version and
   `gpl` group use (D-34) named, protocol freeze (EP-51) wired into EP-95.
4. **P7 re-charter** (EP-102 … EP-127) refreshed with the P5 APIs and the notes-track inputs
   EP-127 will need; dependency corrections found in P5 (e.g. EP-77 → EP-88, EP-79 → EP-84)
   applied to the master table.
5. **Coverage re-audit** of README capability rows 7, 9–17 and 32 against the six-part definition;
   optional toolchain-remediation S brief allocated for P6 if fights were observed.

## Out of scope

- Any code or data work; executing P6 briefs; final-roadmap ordering for release (EP-163).

## Verification / acceptance (sketch)

- `uv run --group dev python roadmap_check.py` clean; README ☑ boxes for EP-75 … EP-89 carry
  commit hashes; P6 files are full briefs and P7 files refreshed charters; `final-roadmap.md`
  contains every P5 parked item; DECISIONS / DESIGN notes appended; committed as
  `docs(roadmap): re-plan P5 — full P6, re-charter P7`.
