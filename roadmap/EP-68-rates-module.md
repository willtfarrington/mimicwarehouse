# EP-68 — Prevalence/incidence/event-rate module

**Size:** M · **Tier:** fixture+dev+full · **Core/Stretch:** core · **Depends on:** EP-42 (Phenotypes: sepsis-3 + KDIGO AKI stage), EP-34 (Time semantics + unit-of-analysis registry) · **Blocks:** EP-69 (Prevalence/incidence page), EP-70 (Descriptive stratified/subgroup module + page), EP-73 (Capstone #2: EDA case study + screenshots)

## Context

Capability 5 (prevalence, incidence, event-rate estimation) as a package module — the first
member of `src/mimicwarehouse/stats/` (DESIGN §15 lists `stats/` under P5; it starts here with
`rates`, `subgroups`, `table1`, `missing` — add a dated DESIGN note). It builds on the versioned
phenotypes (T2DM EP-41; sepsis-3 via the `sepsis3` concept and KDIGO AKI stage via
`kdigo_stages`, EP-42), the code-set registry (EP-40), the grain registry and time semantics
(EP-34: `subject`/`hadm`/`icustay`/`icu_day`/`person_time`, relative windows, `dod` censoring
rule, ICD era), the run ledger (EP-35) and disclosure primitives (EP-43). Every estimate carries
an explicit denominator definition (D-5 representative workflow: sepsis-3 prevalence among
first ICU stays by era; KDIGO AKI incidence within 7 days of ICU admission; T2DM prevalence
across the ICD-9→10 switch). All computation is aggregate SQL through `safe_query` (EP-30);
suppression happens only on export (EP-59); small cells are flagged, not dropped. MIMIC caveats
baked in: `anchor_year_group` is the only era; `dod` is available ~1 year after the last
discharge (person-time censoring rule stated in every result); ICD version differs by era;
ages ≥ 89 = 91 (age bands place 91 in the top band).

## In scope

1. **Spec** (`src/mimicwarehouse/stats/__init__.py`, `stats/rates.py`) — pydantic `RateSpec`:
   `numerator` (one of `phenotype: name@version`, `codeset: name@version` (dx-based, per hadm),
   `concept_flag: table.column`, `event: table + predicate` for event rates);
   `denominator_grain` ∈ {subject, hadm, icustay, icu_day, person_time} (EP-34; person-time in
   ICU or hospital days with the `timesem` censoring rule named in the result); `cohort`
   (materialised cohort id@version or `all`); `measure` ∈ {prevalence (present within the
   baseline window), incidence (new onset within follow-up among units without it at
   baseline), event_rate (events per 1 000 person-days)}; `windows` (baseline/follow-up in
   hours relative to the grain's index event); `strata` ⊆ {era, gender, age_band,
   admission_type, first_service, icd_era, first_careunit}; `k=11`; `ci` ∈ {wilson,
   agresti_coull, exact}.
2. **Estimator** — `estimate(spec, tier, conn=None) -> RateResult`: compiles one aggregate SQL
   per measure with the EP-34 grain helpers and `timesem` (relative windows; `icd_era` from
   `diagnoses_icd.icd_version`), runs it through `safe_query` (never rows), returns a Polars
   frame (stratum columns, `n_events`/`n_cases`, `denominator`, `estimate`, `ci_low`,
   `ci_high`, `method`, `small_cell` mask via `disclose.small_cells`), plus `definitions`
   (numerator/denominator/window/censoring text) and `sql`. CIs: proportions via
   `statsmodels.stats.proportion.proportion_confint` (wilson default, agresti_coull, beta =
   exact); rates via an exact Poisson interval (`poisson_exact_ci(events, time)` using
   `scipy.stats.chi2`) per 1 000 person-days. Every call runs inside a `run` context (EP-35)
   recording spec hash, SQL, snapshot ids.
3. **Shipped specs** (`stats/specs/rates/`) — `sepsis3_prevalence_first_icu_by_era.yaml`
   (numerator concept `sepsis3` onset within stay; denominator icustay; cohort
   `first_icu_stay_adults`; strata era, gender); `aki_incidence_7d_icu.yaml` (numerator KDIGO
   stage ≥ 1 first occurring > 6 h and ≤ 168 h after ICU intime among stays without stage ≥ 1
   in the first 6 h; denominator icustay; strata era, age_band; plus an `event_rate` variant
   per 1 000 ICU person-days); `t2dm_prevalence_hadm_by_icd_era.yaml` (dual ICD-9/10 code set;
   denominator hadm; strata icd_era, era). CLI `mwh stats rates <spec.yaml> --tier <t> [--out
   DIR]` prints the suppressed table (through `disclose`) and writes run artifacts.
4. **Full-tier run + export** — run the three specs with `mwh stats rates … --tier full` as a
   logged background job (`%MWH_DATA_ROOT%\runs\jobs\ep68-rates-full.log`; aggregate over
   concept tables, minutes at most); record run ids; export each result with
   `viz.export.export_table` (EP-59; claim type exploratory) into the run's exports and confirm
   `mwh disclose check` passes.
5. **Tests** `tests/ep/test_ep68.py` (`@pytest.mark.ep_68`): Wilson/Agresti-Coull/exact
   intervals equal statsmodels references and `ci_low ≤ estimate ≤ ci_high` incl. 0 and N
   events; `poisson_exact_ci` vs a scipy reference; person-time for crafted stays (one censored
   by `dod` inside the horizon) equals a hand computation; strata rows sum to totals; `icd_era`
   assignment; the three specs run on fixture producing the schema; small-cell mask set on a
   crafted stratum; a run record exists; dev-marked spec run; full run ids recorded.

## Out of scope

- Subgroup forest/overlap/missingness → EP-70; the page → EP-69.
- Survival-based incidence (KM/Aalen–Johansen), competing discharge → EP-91/93; inference
  between strata → EP-77; age-standardised rates → parked below.

## Verification / acceptance

- `uv run poe test -m ep_68` green on fixture and dev; `uv run mwh verify EP-68` green.
- `uv run --group dev mwh stats rates stats/specs/rates/sepsis3_prevalence_first_icu_by_era.yaml
  --tier dev` prints a suppressed table with n, N, estimate, CI, method and definitions.
- Full-tier run ids for the three specs recorded in the completion note (background job log at
  `%MWH_DATA_ROOT%\runs\jobs\ep68-rates-full.log`); exported tables pass `mwh disclose check`.
- Dated DESIGN.md note: `stats/` package started in P4; `mwh stats` command group.

## Parked → final-roadmap.md

- Direct/indirect age-standardised rates and rate ratios with CIs — trigger: a case study
  compares eras or units and a reviewer asks for adjustment (v2 EDA-2 neighbourhood).
