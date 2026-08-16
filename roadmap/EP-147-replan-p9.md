# EP-147 — Re-plan P9 (writes full P10/P11)

**Size:** M · **Tier:** n/a · **Core/Stretch:** core · **Depends on:** EP-146 (Capstone #7) · **Blocks:** —

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-136 (Re-plan P8) before execution.

## Context

Phase-boundary re-plan (D-8, D-9): retro on P9, timing reconciliation, ☑ reconciliation, DECISIONS
addenda, mirroring of Parked items into `final-roadmap.md`, and the last full-brief writing pass —
P10 (clinical text; **only if** the EP-127 go/no-go was *go*) and P11 (democratization, showcase,
release) both become full briefs here, since P11 is the last phase and has no later re-plan to write
them. Docs-only brief: no data access.

## Scope sketch (refine at re-plan)

1. **Retro & ledger** — per-brief actual vs planned size for EP-137–EP-146, ED load / validation /
   coverage timings from `runs/benchmarks.jsonl`, wizard page latencies; risks struck through or
   added in README § Risks (`~~risk~~ **Resolved by EP-n (date)**` convention); update the
   README time budget line.
2. **Reconciliation** — `uv run --group dev python roadmap_check.py` (EP-6) shows every P9 brief ☑
   with commit hashes (EP-145 either ☑ or explicitly dropped as stretch, hupsim numbering-gap
   precedent); capability coverage table row 35 re-audited; `final-roadmap.md` §34–35 and
   cross-cutting tables receive the Parked items from EP-137–EP-146.
3. **DECISIONS addenda** — anything P9 settled beyond D-36 (e.g., `mwh link` CLI group, `edstay`
   grain, `ref` schema and redistributability flag, temporal key inference if EP-145 ran, the ATC
   decision) as dated addenda; DESIGN.md §15/§19 notes checked for consistency.
4. **Full briefs for P10** (if go): upgrade EP-148–EP-156 charters to full briefs using the full
   template — notes segregation (GOVERNANCE §9, D-3), `--with-notes` attach, `MWH_ALLOW_REMOTE=false`
   guard, synthetic notes for tests, disk check against §3 budget (5–15 GB); if *no-go*: mark P10
   dropped in the README (numbering gap, hupsim precedent), mirror its content into
   `final-roadmap.md` §27, and record the decision as a DECISIONS addendum under D-3.
5. **Full briefs for P11** — upgrade EP-157–EP-163 to full briefs: docs refresh with two reading
   paths (D-1), `mwh init` + cloner smoke on demo (D-12), demo mode, MkDocs site, case-study
   compilation (include `NN-linkage-ed.md`), one-pager, release v1.0.0 with the full-history guard
   sweep (D-41) and `final-roadmap.md` compilation; allocate the optional toolchain-remediation S
   slot if wheel/version fights are pending.
6. **Commit** — `docs(roadmap): re-plan P9 — full P10/P11 briefs, retro, addenda` after the owner
   reviews the diff.

## Out of scope

- Any code change or data run (hand to the P10/P11 briefs written here).
- Rewriting history in DESIGN/DECISIONS/GOVERNANCE (append only).

## Verification / acceptance (sketch)

- `roadmap_check.py` clean for P0–P9; every P10 (if go) and P11 brief exists as a full brief with the
  template sections, header facts matching the README tables, and cross-links resolving.
- `final-roadmap.md` contains every Parked item from EP-137–EP-146; DECISIONS/DESIGN addenda dated;
  README time budget and Risks updated; commit recorded in the P9 table.
