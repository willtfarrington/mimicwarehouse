# EP-101 — Re-plan P6 (writes full P7, re-charters P8)

**Size:** M · **Tier:** n/a · **Core/Stretch:** core · **Depends on:** EP-100 (Capstone #4) · **Blocks:** —

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-90 before execution.

## Context

Phase-boundary re-plan (D-8, D-9): retro on P6, reconcile ☑ hashes, mirror parked items into
`final-roadmap.md`, then write **full briefs for P7 (EP-102–127)** and **re-charter P8
(EP-128–136)** using the brief-writing template (README "How to use", hupsim format, D-37).
P7 is the signature-depth phase (D-6, D-7, D-16), so this re-plan also verifies the GPU and
licensing prerequisites those briefs assume. Docs-only; no data access.

## Scope sketch (refine at re-plan)

1. **Retro + records** — timings and peak RSS from the benchmark ledger for EP-91–99; DECISIONS
   addenda for decisions taken in P6 (Fine–Gray route, IPCW conventions, causal claim labels,
   analysis-step invocation form); README § Risks updates (strike-throughs / new items);
   `roadmap_check.py` clean for EP-91–100.
2. **Mirror parked items** — every `## Parked → final-roadmap.md` entry of EP-91–100 into the
   matching `final-roadmap.md` tables (§ 11–13, 18, 19, 32, 33).
3. **Full briefs for P7** — EP-102–127 written to the full template; pin cross-phase edges
   (EP-93 → EP-112, EP-84 → EP-111, EP-51 → EP-110, EP-78 → EP-105, EP-35 → EP-106/124);
   verify prerequisites: cu130 torch index + driver + `sm_120` capability check for EP-121,
   TabPFN-class weight license for EP-122 (D-7), scikit-survival `gpl` pin if EP-112 uses it
   (D-34); allocate an optional "toolchain remediation" S brief if wheel/version fights are
   expected (sksurv/econml sklearn pins, pytensor numba).
4. **Re-charter P8** — EP-128–136 charters refreshed against what P6/P7 actually built (report
   engine inputs from EP-59, Freezer page over EP-51 + EP-95, temporal-holdout runner over EP-104).
5. **README updates** — P6 table ☑, P7/P8 tables re-linked, capability coverage rows 18, 19, 31
   and 32 re-audited, time-budget check against cadence (Risk 11) with any cutline moves; commit
   `docs(roadmap): re-plan P6 …`.

## Out of scope

- Any code or data work; fixes discovered here go to the owning EP as an addendum or a new brief.
- The notes-track go/no-go (EP-127) and P9–P11 charters (EP-136/147).

## Verification / acceptance (sketch)

- Named artifacts exist: 26 full P7 briefs (EP-102–127), 9 P8 charters (EP-128–136), updated
  `README.md`, `final-roadmap.md`, `DECISIONS.md` addenda; links resolve; `roadmap_check.py`
  reports no ☑/hash or dependency inconsistencies.
- Every P7 brief states Size, Tier, Core/Stretch, Depends on, Blocks and cites at least one D-n;
  method briefs name their representative workflow (D-5, D-6).
