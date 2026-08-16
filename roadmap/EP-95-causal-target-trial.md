# EP-95 — Target-trial emulation harness

**Size:** M · **Tier:** fixture+dev+full · **Core/Stretch:** core · **Depends on:** EP-51 (Protocol schema + freeze registry + `mwh protocol`), EP-92 (Parametric AFT, landmark, time-dependent covariates) · **Blocks:** EP-100 (Capstone #4)

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-90 before execution.

## Context

Opens capability 19 (observational comparative-effectiveness / causal inference) with the
prospective-style design tool: a target-trial emulation harness that sits on the protocol freeze
registry (EP-51, D-25) — the protocol must be frozen before the harness runs — and on the
start–stop and IPCW machinery of EP-92. Sequential trials with artificial censoring are the core
design (packet: "sequential trials; IPCW"). Reports are labelled **causal-with-assumptions** and
state that MIMIC-IV analyses are retrospective (P6 standing decision, GOVERNANCE §7). Time zero is
always a within-patient index (per-patient date shift, EP-34).

## Scope sketch (refine at re-plan)

1. **`causal/target_trial.py` — spec** — `TargetTrialSpec` (pydantic) mapping the seven protocol
   components (eligibility, treatment strategies, assignment, time zero, grace period, follow-up +
   outcome, causal contrast, analysis plan) onto the EP-51 `Protocol`; the harness runs only via
   `uv run --group dev mwh protocol run <hash>` and refuses an unfrozen or modified protocol.
2. **Sequential-trial expansion** — emulate trial *k* at each eligible time origin on an hourly
   or daily grid → person-trial table at the `person_time` grain (EP-34); arms by initiation
   status at the origin; artificial censoring at strategy deviation; IPCW from EP-92
   `survival/ipcw.py`; pooled logistic model (discrete-time hazard, EP-79 GLM) with trial and
   follow-up-time terms; standardized cumulative-incidence curves per strategy; risk difference /
   ratio at the horizon; cluster bootstrap by `subject_id` across trials (EP-78).
3. **Diagnostics** — positivity/overlap per trial, weight distribution table (mean, max, ESS),
   number of trials and person-trials (suppressed at k = 11 on export via `disclose`), an
   assumption list rendered into the report; clone-censor-weight grace-period variant optional.
4. **Representative workflow** — cohort: sepsis-3 ICU adults (EP-42) from the tracer cohort;
   strategies: "initiate norepinephrine within 6 h of sepsis-3 onset" vs "do not initiate within
   6 h" (mimic-code vasopressor concepts, EP-37/38); origins hourly 0–6 h after onset; outcome:
   all-cause death within 28 d of the trial origin (`dod`; an EP-76 `TimeToEvent` instance whose
   origin is the trial time zero — `dod` covers ~1 year after the last discharge, so 28-day follow-up
   is inside the window; the EP-76 censoring rule is stated in the report); time-origin covariates
   from EP-49 windows / EP-55 marts (age, sex,
   `anchor_year_group`, SOFA components, lactate, MAP, fluids in the prior 6 h). Protocol
   `protocols/tt_vasopressor_timing_sepsis3.yaml` frozen with `mwh protocol freeze` before the run;
   full tier as a logged background job.
5. **Report + tests** — `runs/<run_id>/report/` (Markdown + figures) via EP-59 (protocol hash,
   standardized curves, effect table, weight diagnostics, assumptions, claim label). Claim type is
   `causal` in the EP-59/EP-130 enum, rendered as "causal-with-assumptions" with the mandatory
   assumptions list (exchangeability, positivity, consistency, no interference) directly under the
   badge;
   `tests/ep/test_ep95.py` (`@pytest.mark.ep_95`): a small time-varying synthetic DGP with known
   effect is recovered by the IPCW harness while the naive estimate is biased; the harness refuses
   an unfrozen protocol; person-trial counts match a closed form on the fixture.

## Out of scope

- Point-exposure PS / IPTW / matching / standardization → EP-96; sensitivity → EP-97;
  simulation battery → EP-98; Protocol Freezer page → EP-128; temporal-holdout runner → EP-129.
- Marginal structural models with time-varying treatment → parked (final-roadmap § 11–13, EXP-1).

## Verification / acceptance (sketch)

- `uv run poe test -m ep_95` green on fixture (+dev); `uv run --group dev mwh verify EP-95` green;
  the refusal test (unfrozen protocol) is a governance-style check and must pass.
- Full-tier run id, protocol hash and wall time recorded in the completion note; report artifact
  passes `uv run --group dev mwh disclose check <path>` and carries the causal-with-assumptions
  label plus the retrospective statement.

## Parked → final-roadmap.md

- Clone-censor-weight grace-period emulation and dynamic strategies (if not built here) — trigger:
  capstone or reviewer request; marginal structural models — after EP-95/96 (already EXP-1).
