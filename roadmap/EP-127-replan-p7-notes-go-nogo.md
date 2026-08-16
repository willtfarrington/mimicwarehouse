# EP-127 — Re-plan P7 (writes full P8, re-charters P9; notes-track go/no-go)

**Size:** M · **Tier:** n/a · **Core/Stretch:** core · **Depends on:** EP-126 (Capstone #5) · **Blocks:** EP-148 (Notes staging ⏱ (segregated lake + notes.duckdb FTS))

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-101 (Re-plan P6) before execution.

## Context

The P7 phase-boundary re-plan (D-8, D-9): retro, ☑ reconciliation, DECISIONS addenda, upgrading the
P8 charters (EP-128–136) to full briefs and re-chartering P9 (EP-137–147), mirroring every P7 Parked
section into `final-roadmap.md` — plus the one decision the whole roadmap has deferred to this point:
the **notes-track go/no-go** (D-3). Docs-only; no data access. Also the natural moment to move stretch
items across the cutline if the owner's cadence is below plan (roadmap risk 11).

## Scope sketch (refine at re-plan)

1. **Retro + reconciliation** — `uv run --group dev python roadmap_check.py` (EP-6) over P7 ☑ hashes;
   phase timings and full-tier benchmarks summarised from the ledger; risks struck through or added in
   `roadmap/README.md`; completion notes present on every P7 brief; Parked items mirrored into
   `final-roadmap.md` tables (20–26, 28–31, 34).
2. **DECISIONS addenda** — under D-6 (which signature polish landed), D-7 (FM result; whether GRU-D ran),
   D-16 (XGBoost-CUDA vs LightGBM verdict from EP-121), and any toolchain fights (allocate the optional
   toolchain-remediation S slot if needed).
3. **Notes go/no-go (D-3)** — decide with the owner using recorded facts: MIMIC-IV-Note DUA active and
   CITI current (GOVERNANCE §1); disk headroom after the notes lake estimate (5–15 GB) with ≥ 100 GB free
   preserved; GPU path verified (EP-121); P8/P9 core not at risk; owner cadence vs the remaining hours.
   Record the outcome as a new numbered decision in `DECISIONS.md` (go / no-go / defer, with the
   criteria); on *go*, EP-148 stays as planned or is pulled forward per the planning judgment call; on
   *no-go*, mark EP-148–156 as dropped stretch and move their content to `final-roadmap.md` (27).
4. **Write full P8 briefs** (EP-128–136) using the full template, and **re-charter P9** (EP-137–147),
   pinning cross-phase edges two phases ahead; keep header facts in sync with the master tables.
5. **Cutline review** — confirm EP-123 (stretch) done or dropped; propose any moves of P9/P10 stretch
   items to the owner.

## Out of scope

- Any code, model or data work; any P8 execution (this brief only writes P8's briefs).

## Verification / acceptance (sketch)

- `roadmap_check.py` clean; every P7 brief has a completion note or a documented drop; P8 briefs exist
  as full briefs and P9 as charters with correct headers; `final-roadmap.md` grew by the P7 Parked
  items; the go/no-go decision is recorded in `DECISIONS.md` and reflected in the P10 table.
- Commit `docs(roadmap): re-plan P7 — full P8, re-chartered P9, notes go/no-go`.
