# mimicwarehouse — Final roadmap (v2 extension)

The extension roadmap: everything **named but deliberately not built in v1**. v1's completion
bar (see [`README.md`](README.md)) is one tested end-to-end representative workflow per
capability category; the named algorithms, tools, datasets and directions that v1 parks are
collected here, per category, so that v1.0.0 hands the next planning session a ready-made
backlog. This file is **seeded** in the planning session (2026-08-16) and **grown** by every
phase re-plan EP, which mirrors each executed brief's `## Parked → final-roadmap.md` section
into the matching table. EP-163 compiles and orders it for release.

Format: one table per capability category — *Parked item · Trigger (when it becomes worth
doing) · Hazard / dependency · Candidate EP (v2)*. Cross-cutting items are at the end.

Owner decisions that shaped what is parked: D-3 (notes late/optional), D-4 (ED via wizard),
D-7 (tabular FM), D-15/D-16 (Python 3.13, CPU-first), D-19/D-20 (mimic-code concepts, custom
runner), D-21–D-23 (Streamlit, Altair, Jinja/Typst), D-24 (JSONL ledgers), D-30 (CSVs kept),
D-34 (permissive deps), D-35 (free vocabularies first) — see
[`../mimicwarehouse/DECISIONS.md`](../mimicwarehouse/DECISIONS.md).

---

## 1 Data inventory & quality profiling
| Parked item | Trigger | Hazard / dependency | Candidate EP (v2) |
|---|---|---|---|
| great_expectations / pandera declarative suites | if hand-rolled DuckDB checks sprawl | extra framework; overlap with `mwh verify` | v2 QC-1 |
| Data-quality dashboards over time (drift of profiles across builds) | after ≥ 3 full rebuilds | needs build history | v2 QC-2 |

## 2 Reproducible cohort construction
| Parked item | Trigger | Hazard / dependency | Candidate EP (v2) |
|---|---|---|---|
| ACES (Automatic Cohort Extraction System) as an alternative cohort DSL over the MEDS spine | validating our compiler against an external one | MEDS spine must include the events a task needs; Python < 3.14 fine on 3.13 | v2 COH-1 |
| OHDSI ATLAS/Circe cohort definitions | only if OMOP conversion happens (see cross-cutting) | Docker/JVM stack | v2 COH-2 |
| Cohort diff/versions viewer (attrition deltas between spec versions) | after several phenotype/cohort revisions | — | v2 COH-3 |

## 3 Computable clinical phenotypes
| Parked item | Trigger | Hazard / dependency | Candidate EP (v2) |
|---|---|---|---|
| Additional mimic-code-style phenotypes beyond T2DM / sepsis-3 / KDIGO (heart failure, COPD, CKD stages, delirium, ARDS, VAP) | portfolio breadth | dual ICD-9/10 sets; validation vs literature | v2 PHE-1… |
| Phenotype validation study vs notes-derived labels | requires the notes track | note text never leaves the machine | v2 PHE-2 |
| SNOMED/UMLS-based concept sets (via OMOP Athena) | owner obtains a UTS license | non-redistributable vocabularies | v2 PHE-3 |

## 4–6 Cross-sectional EDA · Prevalence/incidence · Stratified/subgroup
| Parked item | Trigger | Hazard / dependency | Candidate EP (v2) |
|---|---|---|---|
| tableone-style automatic Table 1 with journal formatting (Great Tables) | reporting polish | — | v2 EDA-1 |
| Age–period–cohort style era analyses limited to `anchor_year_group` | if reviewers ask for secular trends | date shift; only 3-year bins | v2 EDA-2 |
| Small-area / disparities analyses | not possible — MIMIC has no geography; document explicitly | — | — |

## 7 Missing-data & measurement process
| Parked item | Trigger | Hazard / dependency | Candidate EP (v2) |
|---|---|---|---|
| miceforest / IterativeImputer with tree learners; multiple-imputation pooling for prediction | after EP-87 comparison | compute; leakage across folds | v2 MISS-1 |
| Informative-presence models (measurement indicators as features, formal MNAR sensitivity) | after EP-45/72 | — | v2 MISS-2 |

## 8–10 Event-aligned timelines · Trajectories · Care pathways
| Parked item | Trigger | Hazard / dependency | Candidate EP (v2) |
|---|---|---|---|
| Events spine including a chartevents subset (vitals) | if timelines need bedside vitals at scale | +size/time; EP-50 excludes raw chartevents | v2 SPINE-1 |
| Group-based trajectory modelling (GBTM), latent class growth, tslearn/DTW clustering | after EP-82 | compute; interpretability | v2 TRAJ-1 |
| PrefixSpan/SPADE frequent-sequence mining; process-mining (pm4py) | after EP-83 | dependency licenses (pm4py GPL) | v2 PATH-1 |
| anywidget/D3 timeline component replacing Plotly lanes | polish | JS toolchain (bun) | v2 UI-T1 |

## 11–13 Utilization · Exposure-response · Endpoints
| Parked item | Trigger | Hazard / dependency | Candidate EP (v2) |
|---|---|---|---|
| Recurrent-event frailty models (beyond Andersen–Gill) | after EP-94 | R (frailtypack) or PyMC | v2 UTIL-1 |
| Dose–response with time-varying exposure (marginal structural models) | after EP-95/96 | IPTW at each time step; compute | v2 EXP-1 |
| Composite / win-ratio endpoints | reviewer request | — | v2 END-1 |

## 14–16 Inference · GLM · Multilevel
| Parked item | Trigger | Hazard / dependency | Candidate EP (v2) |
|---|---|---|---|
| R bridge (Rscript + Parquet exchange) for lme4 / glmmTMB / mgcv / cmprsk / TrialEmulation | any frequentist GLMM or Fine–Gray need beyond PyMC/Bambi | second toolchain on Windows; never rpy2 | v2 R-1 |
| pygam / interpret (EBM) | if statsmodels GAM proves insufficient | pygam pins scipy<1.17 | v2 GAM-1 |
| Permutation/randomization inference framework beyond EP-77 | — | compute | v2 INF-1 |

## 17 Time-series & forecasting
| Parked item | Trigger | Hazard / dependency | Candidate EP (v2) |
|---|---|---|---|
| Time-series foundation models (Chronos / MOMENT / TimesFM) for vitals forecasting | after EP-85; GPU available | weights licenses; VRAM | v2 TS-1 |
| darts / sktime / neural forecasters | if statsforecast/statsmodels are insufficient | dependency weight | v2 TS-2 |

## 18 Survival & event-history
| Parked item | Trigger | Hazard / dependency | Candidate EP (v2) |
|---|---|---|---|
| Fine–Gray subdistribution hazards (hand-rolled IPCW/Geskus weighting, or R `cmprsk` via the R bridge — neither lifelines nor scikit-survival implements it) | after EP-93 if the optional route was not taken | R toolchain / bespoke estimator to validate on simulation | v2 SURV-1 |
| Joint longitudinal–survival models | after EP-82 + EP-92 | R (JM/JMbayes) or PyMC | v2 SURV-2 |
| Random survival forests / gradient-boosted survival / DeepSurv | after EP-112 | sksurv (`gpl`), torch | v2 SURV-3 |

## 19 Causal / comparative effectiveness
| Parked item | Trigger | Hazard / dependency | Candidate EP (v2) |
|---|---|---|---|
| dowhy refutation tests; econml heterogeneous effects (DR-learner, causal forests) | after EP-96–98 | dowhy caps < 3.14 (fine on 3.13); compute | v2 CAUS-1 |
| Instrumental variables / regression discontinuity where plausible | rare in MIMIC | design validity | v2 CAUS-2 |
| Quantitative bias analysis beyond E-values | reviewer request | — | v2 CAUS-3 |

## 20–24 Prediction · Nonlinear · Trees · Unsupervised · Dimensionality reduction
| Parked item | Trigger | Hazard / dependency | Candidate EP (v2) |
|---|---|---|---|
| CatBoost GPU; LightGBM OpenCL on the hybrid Arc + NVIDIA laptop | after EP-121 | driver coin-flip | v2 ML-1 |
| Nested-CV hyperparameter search at scale (Optuna) | after EP-124 | compute | v2 ML-2 |
| UMAP / HDBSCAN density clustering; pgmpy graphical models | after EP-114/118 | UMAP numba pins | v2 ML-3 |
| Latent patient groups via mixture of experts / deep clustering | research | — | v2 ML-4 |

## 25 Probabilistic & Bayesian
| Parked item | Trigger | Hazard / dependency | Candidate EP (v2) |
|---|---|---|---|
| Hierarchical Bayesian models on > 50k rows (sufficient statistics / minibatch ADVI) | after EP-117 | compute; nutpie/numba only (no JAX-GPU on Windows) | v2 BAY-1 |
| Bayesian model comparison (LOO/WAIC), posterior predictive checks at scale | after EP-117 | — | v2 BAY-2 |

## 26 Resource-aware neural / deep learning
| Parked item | Trigger | Hazard / dependency | Candidate EP (v2) |
|---|---|---|---|
| Sequence models beyond the stretch GRU/GRU-D (TCN, small transformer, RETAIN-style attention) | after EP-123 | 8 GB VRAM; bf16; batch caps | v2 DL-1 |
| EHR event-sequence foundation models (MOTOR/CLMBR via MEDS/FEMR) | after MEDS spine matures | Python/wheels; weights license; compute | v2 DL-2 |
| Clinical text encoder fine-tune (Bio_ClinicalBERT/LoRA) | notes track completed | note text never leaves the machine | v2 DL-3 |
| Local LLM extraction (Ollama/llama.cpp ≤ 8B Q4) with no-egress guard | notes track + guard tested | PhysioNet LLM policy; VRAM | v2 DL-4 |

## 27 Clinical text
| Parked item | Trigger | Hazard / dependency | Candidate EP (v2) |
|---|---|---|---|
| scispaCy UMLS entity linking / QuickUMLS (separate 3.12 uv project) | owner obtains a UTS license | scispaCy requires < 3.13; UMLS non-redistributable | v2 TXT-1 |
| Full-corpus embeddings (all 2.65 M documents) + LanceDB / DuckDB VSS index | after EP-151 samples | hours on 8 GB VRAM; VSS persistence experimental | v2 TXT-2 |
| Radiology report structured extraction | after EP-150 | — | v2 TXT-3 |
| Everything else the notes track does not reach if the go/no-go at EP-127 is *no* | — | — | v2 TXT-* |

## 28–31 Assessment · Leakage/drift · Interpretability · Benchmarking
| Parked item | Trigger | Hazard / dependency | Candidate EP (v2) |
|---|---|---|---|
| MLflow local mirror of the run ledger / model registry | if a UI over experiments is wanted beyond the Runs page | schema mapping | v2 TRK-1 |
| captum / integrated gradients for neural models | after EP-123 | — | v2 INT-1 |
| External validation on eICU-CRD | separate PhysioNet DUA | licensing; schema mapping via the wizard | v2 EXT-1 |
| Continuous benchmark ledger dashboards | after ≥ 3 phases of ledger data | — | v2 BENCH-1 |

## 32 Interactive visualization
| Parked item | Trigger | Hazard / dependency | Candidate EP (v2) |
|---|---|---|---|
| marimo-as-app lane (reactive notebooks served as apps) | if Streamlit's rerun model bites on Freezer/Wizard pages | two app frameworks | v2 UI-1 |
| Panel / hvPlot / Datashader big-data lane (chartevents-scale rasterized scatter/heatmaps) | when Explorer needs 1e7+ points | heavier stack | v2 UI-2 |
| Mosaic / DuckDB-WASM public aggregate site (Evidence / Observable Framework) | democratization beyond docs | JS toolchain; WASM 4 GB ceiling; aggregates only | v2 UI-3 |
| PreToolUse output-scanning hook for Claude sessions | if deny rules + wrapper prove insufficient | false positives | v2 GOV-1 |

## 33 Reproducible reporting
| Parked item | Trigger | Hazard / dependency | Candidate EP (v2) |
|---|---|---|---|
| Quarto narrative case studies / docs site | if Jinja/MkDocs authoring feels limiting | Quarto install; Chrome for PDF/mermaid | v2 REP-1 |
| DOCX export | non-technical sharing | pandoc/python-docx | v2 REP-2 |
| STROBE / TRIPOD+AI checklists auto-filled from protocol + run records | reporting polish | — | v2 REP-3 |

## 34–35 Model-ready datasets · Additional-data linkage
| Parked item | Trigger | Hazard / dependency | Candidate EP (v2) |
|---|---|---|---|
| MEDS export of the spine + meds-tab baselines + MEDS-DEV tasks | after EP-50 | Python < 3.14 (fine on 3.13); disk | v2 MEDS-1 |
| OMOP CDM conversion (CogStack dbt_mimic_omop, DuckDB + dbt, MIMIC-IV 3.1) | OHDSI tooling wanted | ~200 GB disk; immature; dbt | v2 OMOP-1 |
| MIMIC-IV-FHIR (2.1) via kind-lab/mimic-fhir | FHIR interop demo | separate DUA; lags at 2.2 | v2 FHIR-1 |
| More PhysioNet sources via the wizard (MIMIC-IV-ECG, MIMIC-CXR metadata, MIMIC-IV waveform indices, eICU-CRD) | after EP-145 | separate DUAs; keys | v2 LINK-* |
| dbt-duckdb as an alternative transform runner (resume keyword) | if the custom runner needs docs/lineage features | vendored SQL → Jinja models | v2 DAG-1 |

## 36–38 Disclosure · Prospective inquiry · Provenance
| Parked item | Trigger | Hazard / dependency | Candidate EP (v2) |
|---|---|---|---|
| Formal pre-registration exports (OSF-style) from frozen protocols | external sharing | — | v2 PRO-1 |
| Differential-privacy noise for published aggregates | public aggregate site | utility loss | v2 DIS-1 |
| Full re-computation checks (`mwh reproduce <run_id>`) | after ≥ 20 runs recorded | compute | v2 PROV-1 |

## Cross-cutting
| Parked item | Trigger | Hazard / dependency | Candidate EP (v2) |
|---|---|---|---|
| Re-download `.csv.gz` archives for checksum-verifiable raw (then optionally delete plain CSVs to free ~85 GB) | after ≥ 2 EPs have used the lake and disk pressure appears | must download to local C: only; SHA256SUMS then verify | v2 RAW-1 |
| Docker Compose portability demo (Postgres/Metabase or the app in a container) | sharing with a peer who has no Windows box | bind-mount I/O; GPU passthrough | v2 PORT-1 |
| GitHub Actions CI on the demo tier / synthetic fixtures | after v1.0.0 goes public | demo download in CI; runtime | v2 CI-1 |
| Rust hot-path extension (PyO3/maturin) if a profiled bottleneck appears | only with a measured bottleneck DuckDB/Polars cannot solve | MSVC build tools; test matrix | v2 RS-1 |
| Python 3.14 upgrade (once spaCy ships cp314 wheels and dowhy lifts its cap) | annual toolchain review | re-verify wheels | v2 PY-1 |
| Split into a uv workspace (core package + app package, separate lockfiles) — parked by EP-1 (2026-08-17) | the `ui` conflict set grows beyond `gpu`/`text`, or a page test needs `ui` and `gpu` together | `requires-python` is intersected across members; two lockfiles to keep in step; `mwh` entry point moves | v2 PY-2 |
| keyring-backed secret storage for `MWH_*` tokens (pydantic-settings secrets dir / Windows Credential Manager) — parked by EP-3 (2026-08-17) | the first remote credential enters the project (none in v1; `MWH_ALLOW_REMOTE=false`) | Windows Credential Manager quirks under uv-managed Python; `.env` stays gitignored meanwhile | v2 CFG-1 |
| Multi-user / role model (beyond owner + agent) | if collaborators join | DUA is per-person; access control | v2 GOV-2 |
