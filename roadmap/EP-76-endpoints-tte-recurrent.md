# EP-76 — Endpoints B: time-to-event + recurrent

**Size:** M · **Tier:** fixture+dev+full · **Core/Stretch:** core · **Depends on:** EP-75 (Endpoints A: binary/continuous/count/ordinal) · **Blocks:** EP-89 (Capstone #3), EP-91 (KM / Cox / Schoenfeld)

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-74 (Re-plan P4) before execution.

## Context

Capability category 13 continued: the time-to-event and recurrent-event endpoint tables that P6
survival (EP-91–94) and Signature #3 (EP-112) consume. Extends the `Endpoint` spec and registry of
EP-75, uses the EP-34 `dod` censoring rule (`dod` is available only ~1 year after the last
discharge, so follow-up beyond ~365 d is unreliable), codes discharge-alive as a competing event
for in-hospital outcomes, and derives recurrent AKI episodes from the KDIGO stage phenotype (EP-42).
Output formats follow lifelines / counting-process conventions so P6 needs no reshaping. Themes per
D-5: tracer cohort for mortality, first ICU stays for AKI recurrence.

## Scope sketch (refine at re-plan)

1. **Spec extension** in `src/mimicwarehouse/stats/endpoints.py`: `TimeToEvent` (origin anchor
   from the grain's index event, event definition, competing events coded `0` censored / `1` event
   / `2…` competing, censoring rule = min(follow-up cap, EP-34 `dod` horizon), time unit) and
   `Recurrent` (episode definition, minimum gap, counting-process output).
2. **Seed endpoints** (`src/mimicwarehouse/endpoints/*.yaml`): `time_to_in_hospital_death`
   (origin ICU intime; `1` = in-hospital death, `2` = discharge alive; hours and days),
   `time_to_death_365d` (origin hospital discharge or admission; event = `dod` within 365 d;
   censor at cap or `dod` horizon), `recurrent_aki_episodes` (per icustay: KDIGO stage ≥ 1
   episodes separated by ≥ 48 h at stage 0; episode start/stop, count, gap times; counting-process
   table `(id, start, stop, event, episode_no)` for Andersen–Gill in EP-94).
3. **Materialisation** under `marts/endpoints/…` via DAG step; run record cites cohort + phenotype
   versions; suppressed summaries via `disclose.suppress`: events by type, censoring proportion,
   follow-up median/IQR, episodes-per-stay distribution.
4. **Representative workflow**: tracer cohort → time to in-hospital death with discharge alive as
   competing event, plus 365-day mortality with `dod` censoring; first ICU stays → recurrent AKI
   episodes → summary tables + follow-up/episode-count figures (`viz/`, bins n ≥ 11) → Markdown
   report via EP-59 (claim type *exploratory*; retrospective statement).
5. **Tests** `tests/ep/test_ep76.py` (`@pytest.mark.ep_76`): competing events mutually exclusive;
   times ≥ 0; no follow-up beyond the cap or `dod` horizon; episodes non-overlapping and ≥ 48 h
   apart; counting-process rows contiguous per id; dev aggregates pinned via `safe_query`.

## Out of scope

- KM / Cox estimation → EP-91; Aalen–Johansen / cause-specific → EP-93; Andersen–Gill → EP-94.
- AKI-within-7-d prediction → EP-112; utilization counts and rates → EP-84.
- Landmarking and time-dependent covariate tables → EP-92.

## Verification / acceptance (sketch)

- `uv run poe test -m ep_76` green on fixture + dev; `uv run --group dev mwh verify EP-76` green.
- Full-tier endpoint compile as a logged background job (`uv run --group dev mwh build --tier full
  --select analysis.endpoints_tte --background --job ep76-endpoints-tte`; log under
  `%MWH_DATA_ROOT%\runs\jobs\`); run id + wall time recorded in the completion note.
- Report artifact passes `mwh disclose check`; the P6 briefs can consume the tables unchanged
  (column contract documented in the module docstring).

## Parked → final-roadmap.md

- Multi-state (illness–death) transition tables; joint longitudinal–survival endpoints (SURV-2).
