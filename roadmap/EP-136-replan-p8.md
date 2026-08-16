# EP-136 — Re-plan P8 (writes full P9, re-charters P10/P11)

**Size:** M · **Tier:** n/a · **Core/Stretch:** core · **Depends on:** EP-135 (Capstone #6 + full-tier regression) · **Blocks:** EP-157 (Docs refresh (README/DESIGN/GOVERNANCE/DECISIONS; two reading paths))

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-127 (Re-plan P7) before execution.

## Context

Phase-closing re-plan (D-8, D-9): retro on P8, timings, DECISIONS addenda, ☑ reconciliation, then
full briefs for P9 (EP-137–147: additional-data ingestion and linkage with ED as the test case,
D-4, D-36) and re-charters for P10 (EP-148–156, honouring the EP-127 notes go/no-go, D-3) and P11
(EP-157–163, D-12, D-41). It also closes out EP-135's full-tier regression if that job outlived its
session, and re-audits the capability coverage table. Docs-only; no data access.

## Scope sketch (refine at re-plan)

1. **Retro** — per-EP timings vs S/M/L (D-2) and a benchmark-ledger table for P8; strike through
   resolved risks in `README.md` § Risks (e.g. Streamlit rerun model on the Freezer, DESIGN §21;
   typst-py wheel); DECISIONS addenda for decisions taken in P8 (report bundle home `reports/` vs a
   `docs/analyses/` mirror, the one-look rule, sidecar schema v2, review actor rule); dated notes in
   `DESIGN.md` §13/§14/§17 describing what was actually built; if EP-135's regression overran,
   append its `> **Completion note (date).**` from `runs/regression/<build_id>/summary.json`.
2. **Reconciliation** — run `roadmap_check.py` (EP-6) on EP-128–135 ☑ hashes and table ↔ file
   parity; mirror every P8 `## Parked → final-roadmap.md` item into `final-roadmap.md` (categories
   33 / 36 / 37 / 38 tables and cross-cutting).
3. **Full P9 briefs (EP-137–147)** written to the brief-writing guide: profiler + licence register,
   mapping guide + YAML, key validation / join cardinality / coverage, Linkage Wizard A/B, ED
   ingestion into `mimiciv_ed` (ED 2.2 = 2011–2019 → partial linkage by design; ED Demo for tests),
   reference-table ingestion, ED workflow (triage → admission; time-to-antibiotics), stretch second
   source, capstone #7, re-plan; embed the report engine (EP-130) for coverage reports and the
   review tool (EP-133) for any promoted linkage table; pin cross-phase edges (EP-142 → EP-144,
   EP-143 ← EP-14).
4. **Re-charter P10 and P11** — P10 per the EP-127 go/no-go (if no-go: mark dropped, move items to
   `final-roadmap.md` §27); P11 (docs refresh with two reading paths, `mwh init` + cloner, demo
   mode, docs site, case-study compilation, one-pager + demo script, release with the
   full-history guard sweep); time-budget check (README risk 11) and, if wheel/version fights
   surfaced in P8, allocate the optional toolchain-remediation S slot; re-audit the coverage table.
5. **Roadmap update** — refresh README P9–P11 tables and links; commit
   `docs(roadmap): re-plan P8 — full P9 briefs, re-chartered P10/P11`.

## Out of scope

- Any code change; executing P9 briefs; editing `GOVERNANCE.md` without the owner's ask
  (CLAUDE.md §6).

## Verification / acceptance (sketch)

- A file exists for every P9 / P10 / P11 brief named in `README.md`; `roadmap_check.py` is clean;
  README links resolve.
- Every new full brief satisfies the guide's hard rules (cites a D-n, states its tier, ≤ 6 In-scope
  items, `safe_query` / `mwh sql` only, uv groups named, no in-band ids, background full-tier jobs).
- `final-roadmap.md` grew by the P8 parked items; DECISIONS / DESIGN notes are dated; the EP-135
  completion note is present.
