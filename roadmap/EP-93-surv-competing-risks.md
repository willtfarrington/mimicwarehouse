# EP-93 — Competing risks (Aalen–Johansen; cause-specific; Fine–Gray via gpl optional)

**Size:** M · **Tier:** fixture+dev+full · **Core/Stretch:** core · **Depends on:** EP-91 (KM / Cox / Schoenfeld) · **Blocks:** EP-99 (Survival / causal app pages), EP-100 (Capstone #4), EP-112 (Signature #3: AKI within 7 d (time-to-event prediction))

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-90 before execution.

## Context

Third survival brief (capability 18). In MIMIC every in-hospital outcome has discharge alive as a
competing event, and `1 − KM` overstates cumulative incidence when the competing event is treated
as censoring — the canonical caveat baked into the roadmap (README § Risks 9). The P6 standing
decision is lifelines + a **hand-rolled Aalen–Johansen** estimator; Fine–Gray is optional and, if
built with GPL code, only inside the `gpl` dependency group (D-34). The functions must be generic
over event sets because EP-112 (AKI within 7 d, with death/discharge competing) and EP-99 reuse
them. Results are labelled **associational**.

## Scope sketch (refine at re-plan)

1. **`survival/competing.py` — Aalen–Johansen** — `aalen_johansen(df, time, event_type, group=None)`
   with deterministic tie handling (no jitter), Aalen-type variance and CIs, per-cause CIF tables;
   cross-checked in tests against lifelines `AalenJohansenFitter`; the at-risk/event table goes
   through `disclose.suppress(k=11)` on export; a stacked-CIF spec builder in `viz/`.
2. **Cause-specific Cox** — `cause_specific_cox()` fitting EP-91 `cox()` per cause with the other
   causes censored, tidy output stacked by cause, cluster-robust SEs by `subject_id`, and an
   interpretation note (cause-specific HR ≠ effect on the CIF) written into the report.
3. **Fine–Gray (optional)** — neither lifelines nor scikit-survival ships a Fine–Gray fitter; the
   permissive route is an IPCW-weighted expanded-dataset Cox (Geskus) over lifelines
   `CoxTimeVaryingFitter` using EP-92 `survival/ipcw.py`. EP-90 decides whether to build it here;
   the default is parked (final-roadmap § 18, SURV-1). Any GPL dependency used must be named in
   this brief and live in the `gpl` group.
4. **Representative workflow** — tracer cohort (first ICU stay, adults); endpoint: EP-76
   `time_to_in_hospital_death` (origin ICU intime; `1` = in-hospital death, `2` = discharge alive),
   event of interest death, competing event discharge alive, horizon 28 d; groups:
   KDIGO AKI maximum stage in the first 48 h (0 / 1 / 2 / 3, EP-42 phenotype); Aalen–Johansen CIFs
   by group with the `1 − KM` overestimate shown alongside; cause-specific Cox for death and for
   discharge adjusted for age, sex, `anchor_year_group` and first-day SOFA. Registered analysis
   step (`analysis.surv_competing`); full tier as a logged background job.
5. **Report + tests** — `runs/<run_id>/report/` (Markdown + figures) via EP-59 (CIF figure,
   cause-specific tables, claim label, retrospective statement); `tests/ep/test_ep93.py`
   (`@pytest.mark.ep_93`): AJ equals lifelines on the fixture; Σ CIF + S(t) = 1 at every time;
   `1 − KM ≥ CIF` for the event of interest; a synthetic two-cause DGP with known CIFs is
   recovered; cause-specific HR recovered.

## Out of scope

- Competing-risk *prediction* metrics (IPCW Brier / dynamic AUC) → EP-112 (may use `gpl`).
- Recurrent events → EP-94; target-trial harness → EP-95; page → EP-99.
- Full Fine–Gray implementation unless EP-90 allots it (see item 3).

## Verification / acceptance (sketch)

- `uv run poe test -m ep_93` green on fixture (+dev); `uv run --group dev mwh verify EP-93` green.
- Full-tier run id + wall time recorded in the completion note; report artifact passes
  `uv run --group dev mwh disclose check <path>` and carries the associational claim label.
- The CIF figure shows both causes and the `1 − KM` overestimate; no cell < 11 in exports.

## Parked → final-roadmap.md

- Fine–Gray subdistribution hazards (hand-rolled Geskus weights or `gpl`/R cmprsk) — trigger:
  after this brief, if EP-90 did not allot it (already listed as SURV-1).
- Gray's test for CIF differences; pseudo-value regression on the CIF — trigger: reviewer request.
