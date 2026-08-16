# EP-100 — Capstone #4

**Size:** M · **Tier:** fixture+dev+full · **Core/Stretch:** core · **Depends on:** EP-91 (KM / Cox / Schoenfeld), EP-92 (Parametric AFT, landmark, time-dependent covariates), EP-93 (Competing risks (Aalen–Johansen; cause-specific; Fine–Gray via gpl optional)), EP-94 (Recurrent events (Andersen–Gill)), EP-95 (Target-trial emulation harness), EP-96 (PS / IPTW / matching / balance / standardization), EP-97 (Sensitivity analyses), EP-98 (Causal simulation tests (known truth)), EP-99 (Survival / causal app pages) · **Blocks:** EP-101 (Re-plan P6 (writes full P7, re-charters P8))

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-90 before execution.

## Context

The P6 showcase (D-8: capstone per phase). It compiles the survival and causal representative
workflows into one case study under the `docs/analyses/` convention set by EP-32 (two reading
paths, D-1; "What it deliberately does not claim"; Reproduction blocks with run ids), promotes
figures and tables only through `mwh disclose check` with sidecars (D-40), and records the
full-tier timings for the phase. Every section carries its claim-type label (associational or
causal-with-assumptions) and the statement that MIMIC-IV analyses are retrospective. EP-98's
known-truth battery is the stated prerequisite for citing any causal number.

## Scope sketch (refine at re-plan)

1. **`docs/analyses/04-survival-causal.md`** — sections: sepsis-3 → 90-day mortality (KM/Cox,
   EP-91); ventilation and immortal time (naive vs landmark vs TDC, AFT, EP-92); in-hospital death
   vs discharge alive by AKI stage (CIF, EP-93); recurrent AKI episodes (MCF/AG, EP-94);
   target-trial emulation of vasopressor timing (EP-95); transfusion → mortality (PS/IPTW/matching/
   standardization + sensitivity, EP-96/97); simulation coverage table (EP-98). Each section: claim
   label, figure(s), one table, "What it deliberately does not claim", Reproduction block (run ids,
   protocol hash, snapshot ids).
2. **Promoted artifacts** — figures/tables copied from `runs/<run_id>/` into `docs/analyses/assets/04/`
   only after `uv run --group dev mwh disclose check <path>` passes; demo-tier screenshots of the
   EP-99 pages via EP-60 tooling.
3. **Full-tier reconciliation + benchmark note** — a table of full-tier run ids, wall time, peak RSS
   and disk delta for EP-91–97 pulled from `runs.duckdb` (`uv run --group dev mwh runs …`), plus
   the EP-99 page latency; appended as `> **Completion note (date).**` blocks to the P6 briefs
   where missing.
4. **Consistency checks** — every number in the case study reproduces from its run id; all links
   resolve; README § Risks item 9 (discharge-alive competing event) gets a
   `~~risk~~ **Resolved by EP-93 (date)**` strike-through where warranted; `roadmap_check.py`
   clean for EP-91–99.

## Out of scope

- New analyses or model changes (fix-forward only via the owning EP's addendum).
- Report engine / PDF (EP-130/131) — this capstone is Markdown + PNG/Vega-JSON via EP-59.
- Re-plan actions (DECISIONS addenda, P7 full briefs) → EP-101.

## Verification / acceptance (sketch)

- `docs/analyses/04-survival-causal.md` and `docs/analyses/assets/04/*` exist; every promoted
  artifact has a `.disclosure.json` sidecar; `uv run --group dev mwh verify EP-100` green.
- Numbers reproduce from the recorded run ids; links resolve; the full-tier reconciliation table
  lists a run id for each of EP-91–97 and the EP-98 fixture run.
- No identifier column, free text or cell < 11 in anything committed (guard hook passes).

## Parked → final-roadmap.md

- Quarto narrative version of this case study — trigger: after EP-160 (docs site) if authoring
  in Markdown proves limiting (already REP-1).
