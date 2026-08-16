# EP-91 — KM / Cox / Schoenfeld

**Size:** M · **Tier:** fixture+dev+full · **Core/Stretch:** core · **Depends on:** EP-76 (Endpoints B: time-to-event + recurrent), EP-78 (Cluster bootstrap `boot` module) · **Blocks:** EP-92 (Parametric AFT, landmark, time-dependent covariates), EP-93 (Competing risks (Aalen–Johansen; cause-specific; Fine–Gray via gpl optional)), EP-100 (Capstone #4)

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-90 before execution.

## Context

Opens capability 18 (survival and event-history analysis) and the `src/mimicwarehouse/survival/`
package (DESIGN §15). It consumes the time-to-event endpoint tables of EP-76 (dod-based time to
death with the EP-34 censoring rule; discharge-alive flagged as a competing event) and the cluster
bootstrap of EP-78; lifelines (MIT) is the engine per the P6 standing decision and D-34
(permissive-only imports). MIMIC caveats that bite: `dod` is reliable only ~1 year past the last
discharge (administrative censoring at the EP-34 horizon), the per-patient date shift means every
time axis is relative to an index event, `anchor_year_group` is the only era covariate, and ages
≥ 89 appear as 91. Results are labelled **associational** (P6 standing decision); every table leaving
`runs/` passes `mimicwarehouse.disclose` (D-33, D-40).

## Scope sketch (refine at re-plan)

1. **`survival/km.py`** — `km(df, time, event, group=None, ci="log-log")` → tidy survival table
   (time, at-risk, events, S(t), CI), median/quantile survival, log-rank and stratified log-rank
   tests; the numbers-at-risk table goes through `disclose.suppress(k=11)` before any export; an
   Altair KM spec builder in `viz/` (curves + CI bands + at-risk strip).
2. **`survival/cox.py`** — `cox(df, covariates, cluster="subject_id", strata=None, robust=True)`
   over lifelines `CoxPHFitter`; `tidy()` in the EP-79 schema (term, estimate, std_error, ci_low,
   ci_high, statistic, p_value) plus exponentiated `hr` / CI columns, n and events; cluster-robust
   SEs by `subject_id` by default; optional cluster-bootstrap CIs via EP-78 `boot`.
3. **PH diagnostics** — Schoenfeld residual test per covariate + global, scaled-Schoenfeld-vs-time
   spec builder, martingale/deviance residual summaries; violations land in the run record's
   `warnings` list (EP-35).
4. **Representative workflow** — cohort: the registered tracer cohort (first ICU stay, adults;
   EP-31 → EP-46/47); exposure: sepsis-3 at ICU admission (EP-42 phenotype); outcome: all-cause
   death within 90 d of ICU intime — an EP-76 `TimeToEvent` instance registered here as
   `time_to_death_90d_icu` (origin ICU intime, event = `dod`, censor at min(90 d, EP-34 `dod`
   horizon)); KM by sepsis status + log-rank; Cox adjusted for age, sex, `anchor_year_group`,
   admission type and first-day SOFA (mimic-code concept, EP-37/38); Schoenfeld check;
   cluster-bootstrap CI for the sepsis HR.
   Runs under `mimicwarehouse.run` (EP-35) as a registered analysis step, e.g. `uv run --group
   dev mwh build --tier dev --select analysis.surv_km_cox` (convention set by EP-75); the
   full-tier run is a logged background job (`%MWH_DATA_ROOT%\runs\jobs\ep91-full.log`).
5. **Report artifact** — `runs/<run_id>/report/` (Markdown + figures) written with the EP-59
   export primitives: KM figure, Cox table, PH-test table, methods paragraph, claim type
   **associational**,
   and the statement that MIMIC-IV analyses are retrospective.
6. **Tests** — `tests/ep/test_ep91.py` (`@pytest.mark.ep_91`, tier markers): a known-truth
   synthetic (exponential times, fixed HR, ids ≥ 90 000 000) recovers the HR within tolerance; KM
   equals lifelines `KaplanMeierFitter` on the fixture; Schoenfeld flags a crafted non-PH
   covariate; the exported at-risk table has no cell < 11.

## Out of scope

- Parametric AFT, landmark analysis, time-dependent covariates, IPCW → EP-92.
- Competing risks (in-hospital death vs discharge alive) → EP-93; recurrent events → EP-94.
- Survival page in the app → EP-99; case-study narrative → EP-100; survival ML → parked below.

## Verification / acceptance (sketch)

- `uv run poe test -m ep_91` green on fixture (+dev); `uv run --group dev mwh verify EP-91` green.
- Full-tier run id + wall time recorded in the completion note; the report artifact passes
  `uv run --group dev mwh disclose check <path>` and carries the associational claim label.
- No numbers-at-risk < 11 in any exported table or figure (sidecar records the check).

## Parked → final-roadmap.md

- Random survival forests / gradient-boosted survival (scikit-survival, `gpl` group) — trigger:
  after EP-112.
