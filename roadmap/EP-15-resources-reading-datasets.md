# EP-15 — Reading list + companion datasets + methods notes

**Size:** M · **Tier:** n/a · **Core/Stretch:** core · **Depends on:** — · **Blocks:** EP-16 (Re-plan P1)

## Context

The last of the three resource-gathering briefs (**D-10**): the papers/chapters each capability
category's representative workflow should stand on, the open companion datasets we may load or cite,
and the project's own methods notes — the MIMIC caveats and default analytic choices that every later
brief must not rediscover (README Risk 9). Audience is both reading paths (**D-1**): DS/ML hiring
managers and clinical-informatics readers. Docs-only (tier n/a); nothing is downloaded (EP-22 fetches
the demo; eICU-CRD needs its own DUA and is parked as v2 EXT-1). Prefer free-to-read sources with a DOI
or a stable URL; every link is fetched during the session. No data, no ids, no row-level examples; cite DOIs,
never bare 8-digit PMIDs (the EP-4 guard's real-id-band rule would refuse the file).

## In scope

1. **`mimicwarehouse/docs/resources/reading.md`** — one section per capability category (all 38, numbered
   exactly as the roadmap README coverage table) with ≥ 1 entry each (≥ 60 total): `citation (authors, year,
   venue) — DOI/URL — free? — 1–2 line takeaway for MIMIC-IV`. Anchor entries to include: MIMIC-IV
   (Johnson 2023 *Sci Data*), MIMIC-IV-ED and MIMIC-IV-Note PhysioNet pages, mimic-code (Johnson 2018 *JAMIA*),
   sepsis-3 (Singer 2016; Johnson 2018 *Crit Care Med* implementation), KDIGO 2012, PhysioNet's LLM
   responsible-use guidance and the CMS cell-size suppression policy (n < 11), STROBE/RECORD and TRIPOD+AI,
   Hernán & Robins target-trial emulation (2016) and *Causal Inference: What If* (free), Austin propensity-score
   tutorials, van Buuren *Flexible Imputation of Missing Data* (free), Harrell *RMS* / Steyerberg *Clinical
   Prediction Models*, Van Calster calibration hierarchy, Vickers decision-curve analysis, Mitchell model
   cards, Kaufman leakage, MEDS (McDermott 2024), Fine–Gray / Aalen–Johansen and competing risks in ICU studies
   (e.g. Wolkewitz), temporal validation by `anchor_year_group`, informative presence / measurement-process
   papers, care-pathway/process-mining and trajectory-modelling reviews, TabPFN (Hollmann 2025) for **D-7**.
2. **`mimicwarehouse/docs/resources/datasets.md`** — table `Dataset | Version | Steward / URL | License | Access
   (open / credentialed DUA) | Size | Schema note | Planned use | May enter git?`: MIMIC-IV Clinical Database
   Demo 2.2 (ODbL, 100 subjects, hosp+icu, **v2.2 schema** → EP-9 column map, EP-22 demo tier, screenshots),
   MIMIC-IV-ED Demo 2.2 (ODbL → EP-22/142), MIMIC-IV-FHIR demo (2.1; separate DUA/lag → parked FHIR-1),
   MIMIC-IV MEDS demo and OMOP demo (v1.0-era → parked), MIMIC-III Clinical Database Demo 1.4 (ODbL; older
   schema; ignore), eICU-CRD 2.0 + eICU demo (credentialed / open; external validation → parked EXT-1),
   Synthea (Apache-2.0 synthetic; not MIMIC-shaped; possible fixture enrichment → parked), MIMIC-CXR / MIMIC-IV-ECG
   / waveform indices (separate DUAs → parked LINK-*), and our own synthetic fixture (EP-11/12; MIT; committed).
   State plainly that no note demo exists (synthetic notes for text tests) and that MIMIC-IV-ED 2.2 covers 2011–2019.
3. **`mimicwarehouse/docs/resources/methods-notes.md`** — (a) **MIMIC caveats catalogue**, one entry each with
   "what / why it matters / default handling / D-n or EP": per-patient date shift (no cross-patient calendar
   analyses; `anchor_year_group` the only temporal axis; temporal holdouts split on it), `dod` available ~1 y after
   last discharge (explicit censoring rule per outcome), ICD-9 → ICD-10 (~2015; dual code sets + GEMs), discharge
   alive as competing event for in-hospital outcomes, ages ≥ 89 shown as 91, ED 2.2 = 2011–2019 (partial linkage
   by design), Demo = v2.2 schema and no note demo, `labevents` rows without `hadm_id`, itemid/unit heterogeneity
   (EP-39), duplicated `storetime`s, `emar` vs `prescriptions` as exposure sources, time-of-day preservation
   (verify before diurnal claims); (b) **default analytic choices** referenced by D-n: cluster-robust SEs by
   `subject_id`, Wilson / exact-Poisson CIs, k = 11 small cells (**D-33**), unit-of-analysis registry grains
   (DESIGN §7), temporal + grouped splits, claim-type ladder (exploratory / confirmatory / predictive /
   associational / causal) with one-line definitions and the mandatory "MIMIC-IV analyses are retrospective"
   sentence, tiers and full-tier background-job rule (**D-18**), disclosure review before anything leaves the data
   root (**D-40**); (c) a "how to cite this project's runs" stub (run ids, snapshot ids, protocol hash) that
   EP-32's `docs/analyses/README.md` will extend.
4. **Index** — add three rows to `mimicwarehouse/docs/resources/README.md` (create it if EP-13/14 have not).
5. **Test** (`tests/ep/test_ep15.py`, `@pytest.mark.ep_15`): the three files exist; `reading.md` has 38 numbered
   category headings whose numbers/titles match the roadmap README coverage table (parse `roadmap/README.md`) and
   ≥ 60 entries with a DOI or `https://` link; `datasets.md` table has the nine columns and ≥ 10 rows with a
   non-empty License and `yes`/`no` "May enter git?"; `methods-notes.md` mentions each of the eleven caveats above
   (keyword check) and cites ≥ 5 distinct D-numbers; no file contains a token in the real id bands.

## Out of scope

- Repos/awesome-lists → EP-13; vocabularies → EP-14.
- Downloading the demo (EP-22) or any credentialed dataset (eICU-CRD parked).
- Case-study/analysis conventions beyond the stub → EP-32 (`docs/analyses/README.md`); docs site → EP-160.

## Verification / acceptance

- `uv run poe test -m ep_15` and `uv run --group dev mwh verify EP-15` green (docs test).
- Every link fetched during the session (DOI resolver or HTTP 200); "checked on" date present in each file.
- `docs/resources/README.md` lists `reading.md`, `datasets.md`, `methods-notes.md`.
- Commit `feat(mimicwarehouse): reading list, companion datasets, methods notes (EP-15)`, then
  `docs(roadmap): record EP-15 commit hash`.

## Parked → final-roadmap.md

- eICU-CRD external validation (v2 EXT-1), MIMIC-CXR/ECG/waveform linkage (v2 LINK-*), Synthea-enriched fixtures
  — each with the trigger noted in `datasets.md`; EP-16 mirrors them.
