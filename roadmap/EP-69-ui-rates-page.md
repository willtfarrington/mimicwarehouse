# EP-69 — Prevalence/incidence page

**Size:** S · **Tier:** fixture+dev+full · **Core/Stretch:** core · **Depends on:** EP-68 (Prevalence/incidence/event-rate module), EP-57 (App shell A (Streamlit multipage)) · **Blocks:** EP-73 (Capstone #2: EDA case study + screenshots)

## Context

The UI half of capability 5 (DESIGN §16 "Prevalence & Rates"). EP-68 built `stats.rates`
(`RateSpec`, `estimate`, shipped specs, `mwh stats rates`, run records); EP-57 the shell and
query path; EP-58 the wrappers and export controls; EP-62 the `ui/forms.model_form` helper and
`ui/jobs.launch`; EP-63 the `viz/prevalence.py` builder. This page is a thin, small-cell-aware
front end: edit or pick a spec, run it on the current tier, show the table with explicit
denominators and CIs, a forest chart by strata, definitions and the run id. Full-tier runs are
in-process only when cheap; otherwise the background-job route. Small cells are badged in-app
and suppressed on export (D-33); the page never shows rows.

## In scope

1. **Page** `app/pages/40_rates.py` (registry id `rates`, section Explore) — spec editor
   (`ui/forms.model_form(RateSpec)`) or pick a shipped spec from `stats/specs/rates/`; run:
   demo/dev in-process via `stats.rates.estimate(spec, tier, conn=get_conn(tier))`; full
   in-process only if the same spec ran < 3 s on dev in this session, else
   `ui.jobs.launch("mwh stats rates … --tier full", …)` with status; results:
   `safe_dataframe` (strata, n, denominator, estimate, CI, method, small-cell badge),
   definitions panel (numerator, denominator, windows, censoring rule), run id.
2. **Forest chart** — `src/mimicwarehouse/viz/forest.py: forest(df, estimate, ci_low, ci_high,
   label, group=None, ref=None)` (dot + interval per stratum, sorted, small-cell strata marked;
   reused by EP-70) rendered via `safe_altair`; "Open in Subgroups" handoff via
   `st.query_params` (`?spec=<path>`; consumed by EP-70).
3. **Latency + screenshot** — full-tier page latency for the sepsis-3-by-era spec recorded
   (`MWH_APP_RECORD_LATENCY=1`; ≤ 5 s or job route noted); manifest entry `rates-sepsis3-era`
   captured on demo.
4. **Tests** `tests/ep/test_ep69.py` (`@pytest.mark.ep_69`, ui group): AppTest on fixture —
   the shipped sepsis-3 spec runs and renders table + forest; a crafted tiny stratum in the
   fixture triggers a small-cell warning under `mwh.tier="dev"`; with `mwh.tier="full"` and no
   dev timing the run goes through `ui.jobs.launch` (mocked); `ui_lint`; dev-marked run.

## Out of scope

- Estimation logic, CIs, person-time → EP-68; subgroup module/page → EP-70.
- Export of tables into docs → EP-59/EP-73 (`mwh export` from the run id).

## Verification / acceptance

- `uv run --group ui poe test -m ep_69` green (fixture; dev-marked); `uv run --group ui mwh
  verify EP-69` green (includes `ui_lint`).
- On dev: pick spec → run → table with denominators + CI, forest, definitions, run id.
- Full-tier page latency recorded in the completion note; demo screenshot
  `rates-sepsis3-era-*.png` + sidecars.
