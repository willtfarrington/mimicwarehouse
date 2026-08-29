# Methods notes (EP-15)

The MIMIC-IV caveats and default analytic choices every later brief inherits instead of
rediscovering (README Risk 9). Three parts: **A** the caveats catalogue, **B** the default
analytic choices the owner has already decided (cited as **D-n**), **C** a stub for citing
this project's runs that EP-32's `docs/analyses/README.md` will extend. Audience is both
reading paths (**D-1**). Blanket rule first: every report labels its claim type
(exploratory / confirmatory / predictive / associational / causal) and states that
**MIMIC-IV analyses are retrospective** (GOVERNANCE §7).

- **Checked on:** 2026-08-28. Sources for each caveat are the MIMIC-IV documentation and
  dataset papers listed in `reading.md` (§1, §29, §35); links were fetched live that day.

## A. MIMIC caveats catalogue

Each entry: **what / why it matters / default handling / where it is decided**.

### A1. Per-patient date shift

- **What:** every patient's timeline is shifted by an independent random offset into
  2100–2200; only intervals *within* a patient are real. `anchor_year_group` (a rolling
  3-year band, 2008–2022 overall) is the only true calendar signal.
- **Why it matters:** cross-patient calendar analyses (seasonality, epidemic waves,
  co-presence in a unit) are meaningless; naive "admission year" trends are noise.
- **Default handling:** no cross-patient calendar analyses, ever; `anchor_year_group` is
  the only temporal axis, and temporal holdouts split on it (EP-129).
- **Where:** DESIGN §7 time semantics; reading.md §29 (Nestor 2019).

### A2. `dod` availability and censoring

- **What:** out-of-hospital `dod` (date of death, state-record linkage) is only reliable
  for about one year after a patient's last discharge; later deaths are unrecorded.
- **Why it matters:** survival past ~1 year post-discharge is administratively censored;
  treating absence of `dod` as "alive" inflates long-horizon survival.
- **Default handling:** every outcome definition states its censoring rule explicitly;
  mortality endpoints beyond 1 year post-discharge are not constructed (EP-75/76).
- **Where:** EP-75/76 outcome registry; reading.md §18 competing-risks entries.

### A3. ICD-9 → ICD-10 switch (~2015)

- **What:** BIDMC switched diagnosis/procedure coding from ICD-9-CM to ICD-10-CM/PCS
  around 2015; `diagnoses_icd.icd_version` marks each row's code set.
- **Why it matters:** a single-code-set phenotype silently loses the other era; code
  frequencies shift at the boundary for coding (not clinical) reasons.
- **Default handling:** every code set carries both ICD-9 and ICD-10 arms, cross-walked
  with the frozen 2018 CMS GEMs (`vocabularies.md`); era is a standard stratifier.
- **Where:** EP-40 code sets; vocabularies.md register (GEMs row).

### A4. Discharge alive as a competing event

- **What:** for in-hospital outcomes (death, AKI, transfer), discharge alive removes the
  patient from risk without the event occurring.
- **Why it matters:** Kaplan–Meier "1 − survival" overestimates in-hospital incidence;
  competing-risks estimators (Aalen–Johansen, Fine–Gray) answer the right question.
- **Default handling:** in-hospital time-to-event analyses treat discharge alive as
  competing by default (cumulative incidence, not KM); cause-specific vs subdistribution
  hazards chosen per question (EP-93).
- **Where:** reading.md §18 (Austin 2016, Wolkewitz 2014); EP-91–94.

### A5. Ages ≥ 89 shown as 91

- **What:** patients aged over 89 at their anchor year have `anchor_age` set to 91
  (HIPAA-style aggregation of extreme ages).
- **Why it matters:** age 91 is a category, not a value: means, splines and age-based
  eligibility above 89 are distorted.
- **Default handling:** treat age ≥ 90 as a single band in descriptives and models
  (spline knots stop below it); never compute with 91 as an exact age.
- **Where:** EP-9 schema notes (`patients.anchor_age`); EP-71 descriptive defaults.

### A6. MIMIC-IV-ED 2.2 covers 2011–2019 (partial linkage by design)

- **What:** the ED module's window (2011–2019) is narrower than hosp/icu (2008–2022), and
  not every hospitalization routes through this ED.
- **Why it matters:** unlinked stays are not data errors; "% linked" is a property of the
  design, and ED-conditioned cohorts are a non-random subset of admissions.
- **Default handling:** ED-dependent analyses only after EP-142; linkage coverage is
  measured and reported by EP-139, never "cleaned up".
- **Where:** datasets.md ED rows; roadmap P9 standing decisions (D-4).

### A7. Demo = v2.2 schema and no note demo

- **What:** the open demo datasets sit on the v2.2 schema (pre-3.x), and no open sample
  of MIMIC-IV-Note exists at all.
- **Why it matters:** demo-derived column lists mislead against the 3.1 contract, and
  text pipelines cannot be smoke-tested on real notes outside the credentialed store.
- **Default handling:** the EP-9 column map bridges demo↔3.1; all text tests use
  synthetic notes (ids ≥ 90 000 000); screenshots come from demo/fixture tiers only.
- **Where:** datasets.md Notes; GOVERNANCE §6/§9; D-3.

### A8. `labevents` rows without `hadm_id`

- **What:** a large share of `labevents` (outpatient draws, ED-only visits, boundary
  cases) carries a null `hadm_id`.
- **Why it matters:** joining labs to admissions by `hadm_id` alone silently drops those
  rows; admission-window analyses undercount pre-admission labs.
- **Default handling:** lab↔stay attachment is done by time-window join on `subject_id`
  + `charttime` against the stay spine (EP-50), with the null-`hadm_id` fraction reported
  in curation profiles (EP-39).
- **Where:** DESIGN §7 keys; EP-39/EP-50.

### A9. itemid and unit heterogeneity

- **What:** the same measurement appears under multiple `itemid`s (chartevents vs
  labevents, era variants) and with heterogeneous `valueuom` strings for one itemid.
- **Why it matters:** single-itemid queries undercount; mixed units corrupt distributions
  (mg/dL vs µmol/L, °F vs °C).
- **Default handling:** concept-level item groups with unit harmonization live in one
  place (EP-39 curation registry, seeded from the vendored mimic-code concepts); briefs
  consume the registry, never raw itemid lists.
- **Where:** EP-39 (D-19 for the vendored concepts); reading.md §34 (MIMIC-Extract).

### A10. Duplicated `storetime`s / charting duplicates

- **What:** chartevents/labevents can hold repeated rows for one clinical measurement —
  re-charting, validation re-writes, device + manual entry — distinguishable (if at all)
  by `storetime`.
- **Why it matters:** duplicate rows inflate measurement counts and bias per-stay
  aggregates (min/max are robust, means and counts are not).
- **Default handling:** curation defines one deduplication rule per event family (latest
  `storetime` wins for corrected values; documented exceptions) and every aggregate
  states whether it ran pre- or post-dedup (EP-39).
- **Where:** EP-39 curation rules; DESIGN §7 time semantics (charttime vs storetime).

### A11. `emar` vs `prescriptions` as exposure sources

- **What:** `prescriptions` records orders; `emar` records administrations (barcode
  scans). Ordered ≠ given: doses are held, refused, or changed.
- **Why it matters:** exposure misclassification differs by source — order data
  overstates exposure; emar is closer to intake but has its own gaps (era coverage,
  infusions detailed in `emar_detail`).
- **Default handling:** exposure analyses name their source; `emar` is the default for
  "was it given", `prescriptions` for intention/prescribing-pattern questions (EP-86).
- **Where:** EP-86; reading.md §12 (Schneeweiss 2005).

### A12. Time-of-day preservation (verify before diurnal claims)

- **What:** the date shift moves dates, and the documentation implies clock time is
  preserved — but charting time reflects workflow (batch charting, shift boundaries) as
  much as physiology.
- **Why it matters:** "diurnal" patterns can be artifacts of nursing workflow; and any
  time-of-day claim rests on the preservation assumption actually holding in 3.1.
- **Default handling:** before any diurnal analysis, verify empirically (admission-hour
  and charting-hour distributions against known workflow anchors, e.g. med-pass and lab
  rounds); label results exploratory until then.
- **Where:** EP-49/67 timeline briefs; methods-notes §B claim-type ladder.

## B. Default analytic choices

Decided once by the owner; briefs cite the D-number instead of re-arguing.

- **Cluster-robust inference:** repeated admissions/stays within a patient are the rule,
  so regression SEs default to cluster-robust by `subject_id` (or an explicit multilevel
  model); reading.md §16 (Cameron & Miller 2015).
- **Interval estimates:** proportions get Wilson intervals, event rates get exact-Poisson
  intervals (reading.md §5); Wald intervals are not used for small n.
- **Small cells:** any count or denominator with **n < 11** is a small cell — warn in-app,
  suppress (with complementary suppression) on export, commit, or return to a Claude
  session (**D-33**, GOVERNANCE §5); implemented once in `mimicwarehouse.disclose`
  (EP-43).
- **Unit of analysis:** every analysis names its grain from the registry — `subject`,
  `hadm`, `icustay`, `edstay` (DESIGN §7); mixing grains without an explicit aggregation
  step is a review-blocking defect.
- **Splits:** model evaluation uses grouped splits by `subject_id` (no patient on both
  sides) and, for deployment-shaped claims, temporal splits on `anchor_year_group`
  (EP-104/EP-129).
- **Claim-type ladder:** every report carries exactly one label — **exploratory**
  (hypothesis-generating, no error control claimed) · **confirmatory** (pre-specified
  protocol, frozen before looking — see §C) · **predictive** (performance claims about a
  model on held-out data) · **associational** (adjusted association, no causal wording) ·
  **causal** (explicit target-trial emulation with stated identification assumptions) —
  plus the sentence "MIMIC-IV analyses are retrospective."
- **Tiers:** develop and test on `fixture` and `dev` (5 %); `full`-tier runs are
  background jobs with logs, verified by the *next* EP (**D-18**).
- **Disclosure:** nothing derived from real data enters `docs/`, `reports/` or git
  without `mwh disclose check` and its `.disclosure.json` sidecar (**D-40**,
  GOVERNANCE §7).

## C. How to cite this project's runs (stub)

Every claim that rests on real data cites, at minimum:

1. the **run id** (`runs/` ledger line; **D-24**) of the run that produced it,
2. the **snapshot ids** of every layer the run read (raw snapshot id from the EP-10
   manifest, **D-26**; lake/derived snapshot ids once EP-19+ ship them), and
3. for confirmatory claims, the **protocol hash** frozen before the run (**D-25**,
   EP-51).

Reports render these as a Reproduction block (git sha + `uv.lock` hash + tier + run id),
per GOVERNANCE §12. EP-32's `docs/analyses/README.md` extends this stub into the full
citation convention for case studies.
