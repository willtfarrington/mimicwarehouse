# Reading list (EP-15, D-10)

The papers, chapters and reference pages each capability category's representative workflow
stands on — one section per category, numbered exactly as the roadmap README's
"Capability coverage (38 categories → briefs)" table, so a later brief cites one line here
instead of re-researching. Written for both reading paths (**D-1**): DS/ML hiring managers
and clinical-informatics readers.

- **Checked on:** 2026-08-28. Every DOI below resolved live this day via the doi.org handle
  API, and every plain `https://` URL was fetched live this day (HTTP 200; `curl` with a
  browser user-agent where a publisher refuses the default fetcher).
- **Entry format:** `authors (year), *title*, venue — DOI/URL — free: yes/no — takeaway for
  MIMIC-IV`. One line per entry.
- **Citation hygiene:** cite DOIs or stable URLs, never bare PMIDs — the EP-4 guard's G4
  rule refuses isolated 8-digit tokens starting 1, 2 or 3, which modern PMIDs are
  (amended EP-7). Free-to-read sources are preferred (brief EP-15); paywalled classics are
  kept only where no free substitute says the same thing.
- **Category titles** are pinned to the coverage table as it stands at EP-15; a re-plan
  that re-titles a category updates this file and the coverage table in
  `roadmap/README.md` in the same commit — `tests/ep/test_ep15.py` parses that table live,
  so it needs no edit (see `docs/resources/README.md`; corrected at EP-33, retro RES-5).

## 1. Data inventory & quality profiling

- Johnson AEW, Bulgarelli L, Shen L, et al. (2023), *MIMIC-IV, a freely accessible electronic health record dataset*, Scientific Data 10:1 — https://doi.org/10.1038/s41597-022-01899-x — free: yes — the dataset paper: modular hosp/icu design, anchor dates, de-identification; the citation of record for every analysis in this project.
- Weiskopf NG, Weng C (2013), *Methods and dimensions of electronic health record data quality assessment: enabling reuse for clinical research*, JAMIA 20(1) — https://doi.org/10.1136/amiajnl-2011-000681 — free: yes — the five DQ dimensions (completeness, correctness, concordance, plausibility, currency) that EP-29/EP-44 profiling checks operationalize.
- Kahn MG, Callahan TJ, Barnard J, et al. (2016), *A harmonized data quality assessment terminology and framework for the secondary use of electronic health record data*, eGEMs 4(1) — https://doi.org/10.13063/2327-9214.1244 — free: yes — conformance / completeness / plausibility vocabulary; EP-44's check names follow it so results read like the wider EHR-DQ literature.
- MIT-LCP (living), *MIMIC-IV documentation*, mimic.mit.edu — https://mimic.mit.edu/docs/iv/ — free: yes — the per-table reference (column semantics, provenance quirks) the schema contract (EP-9) and curation notes cite.

## 2. Reproducible cohort construction (+ attrition diagram)

- Johnson AEW, Stone DJ, Celi LA, Pollard TJ (2018), *The MIMIC Code Repository: enabling reproducibility in critical care research*, JAMIA 25(1) — https://doi.org/10.1093/jamia/ocx084 — free: yes — the community codebase this project vendors (D-19); its cohort/concept SQL is the reference implementation our DuckDB ports are count-pinned against.
- Benchimol EI, Smeeth L, Guttmann A, et al. (2015), *The REporting of studies Conducted using Observational Routinely-collected health Data (RECORD) statement*, PLoS Medicine 12(10) — https://doi.org/10.1371/journal.pmed.1001885 — free: yes — RECORD items 6.1/12.1 (codes/algorithms used to select the population) are exactly what EP-46's cohort definitions + attrition diagrams must report.

## 3. Computable clinical phenotypes (versioned)

- Singer M, Deutschman CS, Seymour CW, et al. (2016), *The Third International Consensus Definitions for Sepsis and Septic Shock (Sepsis-3)*, JAMA 315(8) — https://doi.org/10.1001/jama.2016.0287 — free: yes — the sepsis definition (suspected infection + SOFA rise ≥ 2) that EP-41's sepsis phenotype implements.
- Johnson AEW, Aboab J, Raffa JD, et al. (2018), *A comparative analysis of sepsis identification methods in an electronic database*, Critical Care Medicine 46(4) — https://doi.org/10.1097/CCM.0000000000002965 — free: yes — the reference Sepsis-3 implementation on MIMIC (suspicion-of-infection window + SOFA); our vendored `sepsis3.sql` follows it, and its sensitivity analysis shows why phenotypes must be versioned.
- KDIGO (2012), *KDIGO Clinical Practice Guideline for Acute Kidney Injury*, Kidney International Supplements 2(1) — https://kdigo.org/guidelines/acute-kidney-injury/ (also https://doi.org/10.1038/kisup.2012.1) — free: yes — the creatinine/urine-output AKI staging that EP-42's AKI phenotype implements; baseline-creatinine choice is the phenotype's biggest version-to-version lever.
- Quan H, Sundararajan V, Halfon P, et al. (2005), *Coding algorithms for defining comorbidities in ICD-9-CM and ICD-10 administrative data*, Medical Care 43(11) — https://doi.org/10.1097/01.mlr.0000182534.19832.83 — free: no — the Charlson and Elixhauser code lists (the Charlson arm is the vendored `charlson.sql`; no Elixhauser SQL is vendored — see `vocabularies.md`); both ICD arms matter because MIMIC-IV spans the ICD-9→10 switch.

## 4. Cross-sectional exploratory analysis

- Pollard TJ, Johnson AEW, Raffa JD, Mark RG (2018), *tableone: An open source Python package for producing summary statistics for research papers*, JAMIA Open 1(1) — https://doi.org/10.1093/jamiaopen/ooy012 — free: yes — the Table-1 package (born on MIMIC) whose output shape EP-71's descriptive pages mirror; its warnings (normality, multiple testing) are built into our defaults.
- Hayes-Larson E, Kezios KL, Mooney SJ, Lovasi G (2019), *Who is in this study, anyway? Guidelines for a useful Table 1*, Journal of Clinical Epidemiology 114 — https://doi.org/10.1016/j.jclinepi.2019.06.016 — free: no — what a Table 1 is for (and the case against p-values in it); shapes the EP-71 defaults (SMDs, not tests, for group description).

## 5. Prevalence, incidence, event-rate estimation

- Brown LD, Cai TT, DasGupta A (2001), *Interval estimation for a binomial proportion*, Statistical Science 16(2) — https://doi.org/10.1214/ss/1009213286 — free: yes — why Wald intervals fail at small n and extreme p, and why this project's proportion CIs default to Wilson (methods-notes §B); event-rate CIs use exact Poisson for the same reason.

## 6. Stratified and subgroup analysis

- Wang R, Lagakos SW, Ware JH, Hunter DJ, Drazen JM (2007), *Statistics in medicine — reporting of subgroup analyses in clinical trials*, NEJM 357(21) — https://doi.org/10.1056/NEJMsr077003 — free: no — the interaction-test-not-per-stratum-p rule and pre-specification discipline EP-70 enforces; equally binding for observational subgroups.

## 7. Missing-data and measurement-process analysis

- van Buuren S (2018), *Flexible Imputation of Missing Data* (2nd ed.), CRC Press — https://stefvanbuuren.name/fimd/ — free: yes — the MICE reference (free online); EP-72's imputation defaults (predictive mean matching, m by fraction missing, congenial models) come from here.
- Sterne JAC, White IR, Carlin JB, et al. (2009), *Multiple imputation for missing data in epidemiological and clinical research: potential and pitfalls*, BMJ 338 — https://doi.org/10.1136/bmj.b2393 — free: yes — the reporting checklist for imputed analyses (what was imputed, how, sensitivity to MNAR) that EP-72 reports follow.
- Agniel D, Kohane IS, Weber GM (2018), *Biases in electronic health record data due to processes within the healthcare system: retrospective observational study*, BMJ 361 — https://doi.org/10.1136/bmj.k1479 — free: yes — presence and timing of lab orders predict survival better than the values do; the informative-presence caveat (methods-notes §A) and EP-87's measurement-process analyses start here.
- Goldstein BA, Bhavsar NA, Phelan M, Pencina MJ (2016), *Controlling for informed presence bias due to the number of health encounters in an electronic health record*, American Journal of Epidemiology 184(11) — https://doi.org/10.1093/aje/kww112 — free: yes — conditioning on encounter count changes EHR associations; the default adjustment set discussion for EP-87.

## 8. Event-aligned timeline queries

- Suissa S (2008), *Immortal time bias in pharmaco-epidemiology*, American Journal of Epidemiology 167(4) — https://doi.org/10.1093/aje/kwm324 — free: yes — the canonical misalignment failure: time between cohort entry and exposure start misclassified as exposed; every EP-49/EP-67 event-aligned query states its time zero to avoid it.

## 9. Longitudinal trajectory analysis

- Nagin DS, Odgers CL (2010), *Group-based trajectory modeling in clinical research*, Annual Review of Clinical Psychology 6 — https://doi.org/10.1146/annurev.clinpsy.121208.131413 — free: no — the standard entry point for latent trajectory classes; EP-82 uses it for lab/vital trajectories with the caveat that classes are summaries, not disease entities.

## 10. Event-sequence and care-pathway analysis

- Munoz-Gama J, Martin N, Fernandez-Llatas C, et al. (2022), *Process mining for healthcare: Characteristics and challenges*, Journal of Biomedical Informatics 127 — https://doi.org/10.1016/j.jbi.2022.103994 — free: yes — what makes clinical event logs hard (concurrency, incompleteness, granularity); frames EP-83's transfers/ward-path analyses.
- Rojas E, Munoz-Gama J, Sepúlveda M, Capurro D (2016), *Process mining in healthcare: A literature review*, Journal of Biomedical Informatics 61 — https://doi.org/10.1016/j.jbi.2016.04.007 — free: yes — survey of healthcare process-mining methods and tools; the algorithm menu EP-83 chooses from.

## 11. Repeated-encounter and utilization analysis

- Kansagara D, Englander H, Salanitro A, et al. (2011), *Risk prediction models for hospital readmission: a systematic review*, JAMA 306(15) — https://doi.org/10.1001/jama.2011.1515 — free: yes — why readmission models underperform (missing social/community predictors); tempers EP-84/EP-111 readmission claims and motivates labeling them exploratory.

## 12. Exposure-response and treatment-pattern queries

- Schneeweiss S, Avorn J (2005), *A review of uses of health care utilization databases for epidemiologic research on therapeutics*, Journal of Clinical Epidemiology 58(4) — https://doi.org/10.1016/j.jclinepi.2004.10.012 — free: no — what secondary-use exposure data can and cannot support (confounding by indication, exposure misclassification); the checklist behind EP-86's emar-vs-prescriptions exposure sourcing rule.

## 13. Outcome and endpoint construction

- Cordoba G, Schwartz L, Woloshin S, Bae H, Gøtzsche PC (2010), *Definition, reporting, and interpretation of composite outcomes in clinical trials: systematic review*, BMJ 341 — https://doi.org/10.1136/bmj.c3920 — free: yes — composite endpoints mislead when components differ in importance or frequency; EP-75/76 outcome definitions declare components and report them separately.

## 14. Statistical inference and group comparison

- Greenland S, Senn SJ, Rothman KJ, et al. (2016), *Statistical tests, P values, confidence intervals, and power: a guide to misinterpretations*, European Journal of Epidemiology 31 — https://doi.org/10.1007/s10654-016-0149-3 — free: yes — the 25 misinterpretations; EP-77/78 report templates phrase intervals and tests to dodge each one.
- Wasserstein RL, Lazar NA (2016), *The ASA statement on p-values: context, process, and purpose*, The American Statistician 70(2) — https://doi.org/10.1080/00031305.2016.1154108 — free: yes — the six principles; grounds the claim-type ladder's rule that exploratory p-values are flagged as such (methods-notes §B).

## 15. Regression and generalized linear modeling

- Harrell FE (living), *Regression Modeling Strategies — course notes*, hbiostat — https://hbiostat.org/rmsc/ — free: yes — the free companion to the RMS book: spline defaults, shrinkage, validation by bootstrap; the EP-79/80 modeling defaults are lifted from here.

## 16. Repeated-measures and multilevel modeling

- Cameron AC, Miller DL (2015), *A practitioner's guide to cluster-robust inference*, Journal of Human Resources 50(2) — https://doi.org/10.3368/jhr.50.2.317 — free: no — when and how clustered SEs work (and fail with few clusters); the basis for this project's default of cluster-robust SEs by `subject_id` (methods-notes §B) whenever admissions/stays repeat within patient.

## 17. Time-series analysis and forecasting

- Petropoulos F, Apiletti D, Assimakopoulos V, et al. (2022), *Forecasting: theory and practice*, International Journal of Forecasting 38(3) — https://doi.org/10.1016/j.ijforecast.2021.11.001 — free: yes — encyclopedic open-access survey of forecasting methods and evaluation; the menu EP-85 picks from for census/utilization series.
- Bernal JL, Cummins S, Gasparrini A (2017), *Interrupted time series regression for the evaluation of public health interventions: a tutorial*, International Journal of Epidemiology 46(1) — https://doi.org/10.1093/ije/dyw098 — free: yes — segmented-regression ITS with worked code; the design EP-85 uses for before/after questions, with the caveat that MIMIC's date shift permits only `anchor_year_group`-level time axes.

## 18. Survival and event-history analysis

- Fine JP, Gray RJ (1999), *A proportional hazards model for the subdistribution of a competing risk*, JASA 94(446) — https://doi.org/10.1080/01621459.1999.10474144 — free: no — the subdistribution-hazard model for cumulative-incidence questions; EP-93 pairs it with cause-specific hazards and says which question each answers.
- Austin PC, Lee DS, Fine JP (2016), *Introduction to the analysis of survival data in the presence of competing risks*, Circulation 133(6) — https://doi.org/10.1161/CIRCULATIONAHA.115.017719 — free: yes — the readable competing-risks primer: Kaplan–Meier overestimates incidence when competitors exist; use the Aalen–Johansen / cumulative-incidence estimator instead — the default for in-hospital outcomes here (discharge alive competes).
- Putter H, Fiocco M, Geskus RB (2007), *Tutorial in biostatistics: competing risks and multi-state models*, Statistics in Medicine 26(11) — https://doi.org/10.1002/sim.2712 — free: no — the multi-state framework (Aalen–Johansen estimation, transition hazards) behind EP-94's ward→ICU→discharge/death models.
- Wolkewitz M, Cooper BS, Bonten MJM, Barnett AG, Schumacher M (2014), *Interpreting and comparing risks in the presence of competing events*, BMJ 349 — https://doi.org/10.1136/bmj.g5060 — free: no — ICU-flavored worked examples of competing-risk misreadings; the go-to citation when an EP explains why it did not run plain KM on in-hospital mortality.

## 19. Observational comparative-effectiveness / causal inference

- Hernán MA, Robins JM (2020), *Causal Inference: What If*, CRC Press (free PDF) — https://miguelhernan.org/whatifbook — free: yes — the causal-inference textbook (counterfactuals, g-methods, IPW, target trials); the project's causal vocabulary and the EP-95–98 curriculum follow it.
- Hernán MA, Robins JM (2016), *Using big data to emulate a target trial when a randomized trial is not available*, American Journal of Epidemiology 183(8) — https://doi.org/10.1093/aje/kwv254 — free: yes — the target-trial-emulation protocol (eligibility, assignment, time zero, outcome, analysis) that every EP-95+ causal analysis writes down before touching data.
- Austin PC (2011), *An introduction to propensity score methods for reducing the effects of confounding in observational studies*, Multivariate Behavioral Research 46(3) — https://doi.org/10.1080/00273171.2011.568786 — free: yes — matching/weighting/stratification mechanics, balance diagnostics (SMDs), and what propensity scores cannot fix; the EP-96 implementation guide.

## 20. Supervised prediction

- Steyerberg EW (2019), *Clinical Prediction Models* (2nd ed.), Springer — https://doi.org/10.1007/978-3-030-16399-0 — free: no — the development-validation-updating framework (sample size, shrinkage, internal vs external validation) that the P6 prediction EPs and their model cards follow.
- Christodoulou E, Ma J, Collins GS, Steyerberg EW, Verbakel JY, Van Calster B (2019), *A systematic review shows no performance benefit of machine learning over logistic regression for clinical prediction models*, Journal of Clinical Epidemiology 110 — https://doi.org/10.1016/j.jclinepi.2019.02.004 — free: no — why EP-107's regularized-regression baseline is mandatory before any ensemble/deep model gets credit.
- Harutyunyan H, Khachatrian H, Kale DC, Ver Steeg G, Galstyan A (2019), *Multitask learning and benchmarking with clinical time series data*, Scientific Data 6 — https://doi.org/10.1038/s41597-019-0103-9 — free: yes — the standard MIMIC benchmark tasks (in-hospital mortality, decompensation, LOS, phenotyping) our signature tasks (D-6) are calibrated against.

## 21. Nonlinear and flexible modeling

- Perperoglou A, Sauerbrei W, Abrahamowicz M, Schmid M (2019), *A review of spline function procedures in R*, BMC Medical Research Methodology 19 — https://doi.org/10.1186/s12874-019-0666-3 — free: yes — spline families, knot placement and df choices; EP-113's default (restricted cubic splines, knots by Harrell's quantiles) is justified from here.

## 22. Tree-based and ensemble learning

- Breiman L (2001), *Random forests*, Machine Learning 45 — https://doi.org/10.1023/A:1010933404324 — free: no — the original RF paper (bagging + random feature subsets, OOB error); EP-108's forest baseline and its variable-importance caveats.
- Chen T, Guestrin C (2016), *XGBoost: A scalable tree boosting system*, KDD '16 — https://doi.org/10.1145/2939672.2939785 — free: yes — regularized gradient boosting and its sparsity-aware handling of missing values — the reason boosted trees are the tabular workhorse EP-108 tunes first.

## 23. Unsupervised learning and pattern discovery

- Seymour CW, Kennedy JN, Wang S, et al. (2019), *Derivation, validation, and potential treatment implications of novel clinical phenotypes for sepsis*, JAMA 321(20) — https://doi.org/10.1001/jama.2019.5791 — free: yes — the α/β/γ/δ sepsis clusters: the model for how EP-114 derives, stability-tests and (crucially) refuses to over-interpret unsupervised phenotypes.

## 24. Dimensionality reduction and high-dimensional analysis

- McInnes L, Healy J, Saul N, Großberger L (2018), *UMAP: Uniform Manifold Approximation and Projection*, Journal of Open Source Software 3(29) — https://doi.org/10.21105/joss.00861 — free: yes — the embedding method EP-116 defaults to for cohort maps, with the standing caveat that distances/densities in the embedding are not quantitative evidence.

## 25. Probabilistic and Bayesian analysis

- Gelman A, Vehtari A, Simpson D, et al. (2020), *Bayesian workflow*, arXiv — https://arxiv.org/abs/2011.01808 — free: yes — prior predictive checks, computation diagnostics, model expansion; the workflow EP-117/118 follow rather than fit-once-and-report.
- Abril-Pla O, Andreani V, Carroll C, et al. (2023), *PyMC: a modern, and comprehensive probabilistic programming framework in Python*, PeerJ Computer Science 9 — https://doi.org/10.7717/peerj-cs.1516 — free: yes — the PPL this project uses for hierarchical models (EP-117); documents the NUTS/ADVI machinery and its Windows-friendly backends.

## 26. Resource-aware neural and deep-learning experiments

- Hollmann N, Müller S, Purucker L, et al. (2025), *Accurate predictions on small data with a tabular foundation model*, Nature 637 — https://doi.org/10.1038/s41586-024-08328-6 — free: yes — TabPFN v2: pretrained in-context learning for small tabular tasks; the **D-7** choice for this project's deep-learning representative workflow (EP-122) on an 8 GB-VRAM budget.
- Grinsztajn L, Oyallon E, Varoquaux G (2022), *Why do tree-based models still outperform deep learning on typical tabular data?*, NeurIPS Datasets & Benchmarks — https://arxiv.org/abs/2207.08815 — free: yes — the honest prior for EP-121–123: on medium tabular data, tuned trees usually win; deep experiments here must beat that bar, not assume it away.

## 27. Clinical text analysis (separately authorized notes)

- Johnson AEW, Pollard TJ, Horng S, Celi LA, Mark RG (2023), *MIMIC-IV-Note: Deidentified free-text clinical notes* (v2.2), PhysioNet — https://physionet.org/content/mimic-iv-note/2.2/ — free: no (separate credentialed DUA) — the notes asset (discharge summaries + radiology reports) staged only in P10 under GOVERNANCE §9: segregated store, local models only, text never in tool output.
- Eyre H, Chapman AB, Peterson KS, et al. (2021), *Launching into clinical space with medspaCy: a new clinical text processing toolkit in Python*, AMIA Annual Symposium / arXiv — https://arxiv.org/abs/2106.07799 — free: yes — the clinical-NLP toolkit (sectionizer, ConText negation/temporality) that EP-150's concept extraction builds on.
- PhysioNet (2023), *Responsible use of MIMIC data with online services like GPT*, PhysioNet news — https://physionet.org/news/post/gpt-responsible-use/ — free: yes — the policy behind GOVERNANCE §4: credentialed data must not be sent to non-compliant online services; the reason Claude sessions here see only k-suppressed aggregates and no note text.

## 28. Model assessment and selection

- Van Calster B, Nieboer D, Vergouwe Y, De Cock B, Pencina MJ, Steyerberg EW (2016), *A calibration hierarchy for risk models was defined: from utopia to empirical data*, Journal of Clinical Epidemiology 74 — https://doi.org/10.1016/j.jclinepi.2015.12.005 — free: no — mean/weak/moderate/strong calibration levels; EP-105 reports calibration at the level the sample supports instead of a lone Hosmer–Lemeshow p.
- Vickers AJ, Elkin EB (2006), *Decision curve analysis: a novel method for evaluating prediction models*, Medical Decision Making 26(6) — https://doi.org/10.1177/0272989X06295361 — free: yes — net benefit across threshold probabilities; EP-105's answer to "is this model clinically useful?" beyond AUROC.
- Steyerberg EW, Vergouwe Y (2014), *Towards better clinical prediction models: seven steps for development and an ABCD for validation*, European Heart Journal 35(29) — https://doi.org/10.1093/eurheartj/ehu207 — free: yes — the compact development/validation checklist EP-104/105 encode as their report skeleton.

## 29. Leakage, drift, and robustness testing

- Kaufman S, Rosset S, Perlich C, Stitelman O (2012), *Leakage in data mining: formulation, detection, and avoidance*, ACM TKDD 6(4) — https://doi.org/10.1145/2382577.2382579 — free: no — the leakage taxonomy (target leakage, split contamination); EP-119's leakage audit checks each named form against the feature registry.
- Nestor B, McDermott MBA, Boag W, et al. (2019), *Feature robustness in non-stationary health records: caveats to deployable model performance in common clinical machine learning tasks*, MLHC (PMLR 106) — https://arxiv.org/abs/1908.00690 — free: yes — MIMIC model performance degrades across care-era shifts (CareVue→MetaVision); the direct argument for temporal validation split on `anchor_year_group` (methods-notes §A/§B).
- Finlayson SG, Subbaswamy A, Singh K, et al. (2021), *The clinician and dataset shift in artificial intelligence*, NEJM 385(3) — https://doi.org/10.1056/NEJMc2104626 — free: yes — the clinical taxonomy of dataset shift; frames EP-119's drift monitoring and the deployment caveats in model cards.

## 30. Interpretability and error analysis

- Lundberg SM, Lee S-I (2017), *A unified approach to interpreting model predictions*, NeurIPS — https://arxiv.org/abs/1705.07874 — free: yes — SHAP values unify additive attributions; EP-120's default local/global explanation tool, reported with its correlated-features caveat.
- Rudin C (2019), *Stop explaining black box machine learning models for high stakes decisions and use interpretable models instead*, Nature Machine Intelligence 1 — https://doi.org/10.1038/s42256-019-0048-x — free: no — the counter-position EP-120 must answer: post-hoc explanation is not a substitute for an interpretable model when stakes are high.

## 31. Simulation, ablation, and benchmarking experiments

- Purushotham S, Meng C, Che Z, Liu Y (2018), *Benchmarking deep learning models on large healthcare datasets*, Journal of Biomedical Informatics 83 — https://doi.org/10.1016/j.jbi.2018.04.007 — free: yes — MIMIC benchmarking done carefully (feature sets, cohorts, baselines); the design EP-124's benchmark harness borrows, including reporting compute cost per result.

## 32. Interactive visualization

- VanderPlas J, Granger BE, Heer J, et al. (2018), *Altair: Interactive statistical visualizations for Python*, Journal of Open Source Software 3(32) — https://doi.org/10.21105/joss.01057 — free: yes — the declarative charting layer chosen in D-22; every app chart is an Altair/Vega-Lite spec, which is what makes the disclosure gate's "no embedded row data" check possible.
- Satyanarayan A, Moritz D, Wongsuphasawat K, Heer J (2017), *Vega-Lite: A grammar of interactive graphics*, IEEE TVCG 23(1) — https://doi.org/10.1109/TVCG.2016.2599030 — free: yes — the grammar underneath Altair: selections/composition as first-class spec objects; useful when EP-88/99 push past Altair's API.

## 33. Reproducible reporting

- von Elm E, Altman DG, Egger M, et al. (2007), *The Strengthening the Reporting of Observational Studies in Epidemiology (STROBE) statement: guidelines for reporting observational studies*, PLoS Medicine 4(10) — https://doi.org/10.1371/journal.pmed.0040296 — free: yes — the base observational reporting checklist; EP-132's report templates carry STROBE item slots.
- Collins GS, Moons KGM, Dhiman P, et al. (2024), *TRIPOD+AI statement: updated guidance for reporting clinical prediction models that use regression or machine learning methods*, BMJ 385 — https://doi.org/10.1136/bmj-2023-078378 — free: yes — the prediction-model reporting standard (including fairness and code availability items) EP-132's model-report template implements.
- Sandve GK, Nekrutenko A, Taylor J, Hovig E (2013), *Ten simple rules for reproducible computational research*, PLoS Computational Biology 9(10) — https://doi.org/10.1371/journal.pcbi.1003285 — free: yes — the ten rules (track everything, script everything, version everything) that GOVERNANCE §12's run-record requirements operationalize.
- Mitchell M, Wu S, Zaldivar A, et al. (2019), *Model cards for model reporting*, FAT* '19 — https://doi.org/10.1145/3287560.3287596 — free: yes — the model-card template (intended use, metrics by slice, caveats) EP-106 generates for every registered model.

## 34. Model-ready dataset generation

- McDermott MBA, et al. (2024), *The Medical Event Data Standard (MEDS)*, community specification — https://medical-event-data-standard.github.io/ (spec releases archived at https://doi.org/10.5281/zenodo.17535826) — free: yes — the minimal event-stream schema for EHR ML; the optional MEDS validation lane over the EP-50 spine (and the MEDS demo dataset in `datasets.md`) target it.
- Wang S, McDermott MBA, Chauhan G, et al. (2020), *MIMIC-Extract: a data extraction, preprocessing, and representation pipeline for MIMIC-III*, ACM CHIL — https://doi.org/10.1145/3368555.3384469 — free: yes — the canonical cohort/feature-extraction pipeline design (unit harmonization, outlier handling, hourly aggregation) EP-102/103 adapt to MIMIC-IV.

## 35. Additional-data ingestion and linkage

- Johnson AEW, Bulgarelli L, Pollard TJ, Celi LA, Mark RG, Horng S (2023), *MIMIC-IV-ED* (v2.2), PhysioNet — https://physionet.org/content/mimic-iv-ed/2.2/ — free: no (separate credentialed DUA, held) — the ED module (triage, vitals, meds) EP-142 ingests via the Linkage Wizard; covers 2011–2019, so partial linkage to hosp/icu is by design.
- Pollard TJ, Johnson AEW, Raffa JD, Celi LA, Mark RG, Badawi O (2018), *The eICU Collaborative Research Database, a freely available multi-center database for critical care research*, Scientific Data 5 — https://doi.org/10.1038/sdata.2018.178 — free: yes — the multi-center external-validation counterpart to MIMIC; needs its own DUA, so parked as v2 EXT-1 with its demo listed in `datasets.md`.

## 36. Ethical and disclosure-aware analysis

- CMS (2020), *CMS cell size suppression policy*, via ResDAC — https://resdac.org/articles/cms-cell-size-suppression-policy — free: yes — the n < 11 cell-suppression rule (with complementary suppression) this project adopts verbatim as **D-33**; implemented once in `mimicwarehouse.disclose` (EP-43).
- El Emam K, Jonker E, Arbuckle L, Malin B (2011), *A systematic review of re-identification attacks on health data*, PLoS ONE 6(12) — https://doi.org/10.1371/journal.pone.0028071 — free: yes — measured re-identification risk and its drivers; the evidence base for treating aggregates-only egress (GOVERNANCE §4) as a real control, not theater.
- PhysioNet (2023), *PhysioNet Credentialed Health Data License 1.5.0* — https://physionet.org/about/licenses/physionet-credentialed-health-data-license-150/ — free: yes — the license every governance rule in this repo traces back to; read alongside GOVERNANCE §1.

## 37. Prospective-style inquiry over retrospective data

- Hernán MA, Sauer BC, Hernández-Díaz S, Platt R, Shrier I (2016), *Specifying a target trial prevents immortal time bias and other self-inflicted injuries in observational analyses*, Journal of Clinical Epidemiology 79 — https://doi.org/10.1016/j.jclinepi.2016.04.014 — free: yes — align eligibility, assignment and time zero as a trial would; the design rule behind EP-51/EP-128's protocol-freeze-before-run and EP-129's temporal holdouts.

## 38. End-to-end provenance

- Gebru T, Morgenstern J, Vecchione B, et al. (2021), *Datasheets for datasets*, Communications of the ACM 64(12) — https://doi.org/10.1145/3458723 — free: yes — provenance/composition/collection questions every derived dataset should answer; the template behind the EP-103 dataset cards and this directory's inventories.
- Wilkinson MD, Dumontier M, Aalbersberg IJ, et al. (2016), *The FAIR guiding principles for scientific data management and stewardship*, Scientific Data 3 — https://doi.org/10.1038/sdata.2016.18 — free: yes — findable/accessible/interoperable/reusable as concrete metadata obligations; what the run ledgers and snapshot ids (GOVERNANCE §12) make true locally.
