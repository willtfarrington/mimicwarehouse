# Repos & awesome-lists inventory (EP-13, D-10)

Every repository, pipeline and awesome-list the planning research found around MIMIC-IV, with
license, activity, MIMIC/Python targets and an explicit verdict tied to the EP that uses it —
so later sessions borrow deliberately and never rebuild on dormant MIMIC-III tooling. Verdicts
respect D-19 (mimic-code adopted, vendored at EP-8), D-20 (custom runner; dbt-duckdb parked),
D-34 (permissive licenses only in core groups; GPL only via the opt-in `gpl` group) and the
parked lanes already in `roadmap/final-roadmap.md` (MEDS/ACES, OMOP, FHIR). No code is copied
from any repo; we link and summarise.

- **Checked on:** 2026-08-28. Every GitHub row was verified live via the GitHub REST API
  (`gh api repos/<owner>/<name>`, HTTP 200; license, last push, description as returned);
  PhysioNet rows were fetched directly (HTTP 200). "Last activity" is the repository's last
  push date (for PhysioNet, the version's release date).
- **Verdict vocabulary:** **adopt** = depend on or vendor it · **port** = re-implement the
  idea/logic in our package and cite it · **ignore** = do not use; the Notes cell records why
  (dormant, MIMIC-III-only, R-dependent, license, scope) and the one idea worth borrowing.
- **How to add a row:** fetch the URL the same day you add it, fill every cell (ISO dates,
  hyphenated), pick a verdict from the vocabulary above, and name the EP that uses or parks
  it; mirror any "port later" item into the owning brief's Parked section.

## Main table

| Resource | URL | License | Last activity (as of 2026-08-28) | MIMIC target | Python | What it offers | Verdict | Used by / borrowed by | Notes |
|---|---|---|---|---|---|---|---|---|---|
| mimic-code (MIT-LCP) | https://github.com/MIT-LCP/mimic-code | MIT | 2026-08-25 | MIMIC-IV 3.1, ED 2.2, Note 2.2 | any (`mimic_utils` uses sqlglot) | Community code for MIMIC: build scripts (Postgres + DuckDB), `validate.sql` row counts, 66 `concepts_duckdb` + 65 `concepts` SQL, `src/mimic_utils` transpiler, `concept_map/*.csv` | adopt | EP-8 (vendored), EP-10/28 (validate.sql), EP-17/18 (build script), EP-37/38 (concepts), EP-138 (mimic_utils + concept_map) | Vendored at the D-19 pin (`vendor_info().sha` = `8bcbd190ca75670cd5281f9ead3611ae1cefb73e`, recorded in `concepts/vendor/VENDOR.json`); attribution in the repo-root `NOTICE` |
| MEDS (Medical-Event-Data-Standard) | https://github.com/Medical-Event-Data-Standard/meds | Apache-2.0 | 2026-06-12 | dataset-agnostic | >= 3.10 | The Medical Event Data Standard: event-stream schema definitions + Python types; latest release 0.4.1 (2025-11-05) | port | EP-50 (spine column set) | We keep our own spine; borrow the MEDS 0.4 core event columns (subject_id, time, code, numeric_value) for interoperability |
| ACES | https://github.com/justin13601/ACES | MIT | 2026-06-04 | MEDS / ESGPT inputs | >= 3.10 | Automatic Cohort Extraction System: YAML predicate / trigger / window task specifications | port | EP-46 (spec shape) | PyPI package `es-aces`; ICLR 2025 paper; an optional ACES validation lane over the EP-50 spine stays parked |
| MIMIC_IV_MEDS ETL | https://github.com/Medical-Event-Data-Standard/MIMIC_IV_MEDS | MIT | 2026-08-10 | MIMIC-IV (PhysioNet download) | >= 3.11.4, < 3.14 | The official MIMIC-IV → MEDS extraction ETL (built on MEDS_transforms) | ignore | EP-50 (column set only, via the MEDS row) | Ignore as ETL: expects `.csv.gz` inputs and its own PhysioNet download step; Python < 3.14 cap; we port only the MEDS column set |
| MEDS_transforms | https://github.com/mmcdermott/MEDS_transforms | MIT | 2026-08-05 | MEDS datasets | >= 3.11 | Polars-based library of MEDS ETL / transform stages | ignore | — | D-20 keeps our custom runner; parked (port later): its stage-catalog design as a reference. Discovered via MIMIC_IV_MEDS |
| MEDS-DEV | https://github.com/Medical-Event-Data-Standard/MEDS-DEV | MIT | 2026-08-20 | MEDS datasets | >= 3.11 | Decentralized benchmark: reproducible task + dataset definitions for EHR ML | ignore | — | Parked (port later): its task definitions, if a MEDS validation lane is ever built |
| meds-tab (MEDS_Tabular_AutoML) | https://github.com/mmcdermott/MEDS_Tabular_AutoML | MIT | 2026-04-14 | MEDS datasets | >= 3.11 | Tabularization + AutoML baselines over MEDS datasets | ignore | — | Parked (port later): tabularized baselines as a comparator when EP-110/111 report models |
| PyHealth | https://github.com/sunlabuiuc/PyHealth | MIT | 2026-08-20 | MIMIC-III / MIMIC-IV; MEDS since 2.0 | >= 3.12, < 3.14 | Deep-learning toolkit for EHR: dataset loaders (`mimic4.py`), task definitions (in-hospital mortality, readmission), models | port | EP-110/111 (task windows / labels) | v2.0.1. Reference only, never a dependency. Its MIMIC-IV loader reads local CSVs with a 2.x-era column set — no explicit 3.1 pin found; re-check at EP-110 |
| healthylaife MIMIC-IV Data Pipeline | https://github.com/healthylaife/MIMIC-IV-Data-Pipeline | MIT | 2026-07-06 | MIMIC-IV (multimodal extension in progress) | notebooks | Configurable cohort selection + feature-extraction pipeline over MIMIC-IV | port | EP-102 (feature/cohort step ordering as reference) | Notebook-heavy; we re-implement, never import. Parked (port later): the full feature pipeline |
| philipdarke/mimic4 | https://github.com/philipdarke/mimic4 | MIT | 2024-09-04 | MIMIC-IV | Python | Personal script set loading MIMIC-IV into a DuckDB database | ignore | — | Confirmed what it is: a small dormant loader (2 stars); superseded by the vendored mimic-code build script and our EP-17 loader |
| mimic-iv-visualization (jaanli) | https://github.com/altosaar/mimic-iv-visualization | Apache-2.0 | 2024-11-15 | MIMIC-IV | dbt-duckdb | dbt + DuckDB models that publish aggregate-only ICU views for visualization | port | EP-58/64 (small-cell suppression pattern, aggregate-only views) | Named "jaanli" in the planning research; that account is gone — the repo lives under `altosaar`. Minimal repo: we borrow the aggregates-behind-the-UI idea, not code |
| dbt_mimic_omop (CogStack) | https://github.com/CogStack/dbt_mimic_omop | none found | 2026-07-06 | MIMIC-IV + Note → OMOP | dbt | dbt project converting MIMIC-IV and MIMIC-IV-Note to the OMOP CDM | ignore | — | OMOP is out of scope for v1 (parked v2 OMOP-1); no license file in the repo, so D-34 would block adoption anyway |
| mimic-fhir (kind-lab) | https://github.com/kind-lab/mimic-fhir | MIT | 2024-10-28 | MIMIC-IV 2.x | Python + notebooks | MIMIC-IV → FHIR conversion and validation | ignore | — | FHIR is out of scope for v1 (parked v2 FHIR-1); dormant since 2024 |
| OHDSI/MIMIC | https://github.com/OHDSI/MIMIC | Apache-2.0 | 2026-08-21 | MIMIC-IV (BigQuery-hosted copy; 2.2-era per the planning research) | Python + BigQuery SQL | The OHDSI ETL of MIMIC-IV into the OMOP CDM | ignore | — | BigQuery-only workflows, no local/DuckDB path; OMOP parked (v2 OMOP-1) |
| MIMIC-Extract | https://github.com/MLforHealth/MIMIC_Extract | MIT | 2024-07-18 | MIMIC-III | legacy (py3.7 era) | Extraction / preprocessing pipeline producing an hourly time-series representation | ignore | EP-102 (hourly-binning idea as reference) | MIMIC-III only, dormant. The one idea worth borrowing: hourly aggregation with typed feature grouping |
| mimic3-benchmarks (YerevaNN) | https://github.com/YerevaNN/mimic3-benchmarks | MIT | 2023-04-16 | MIMIC-III | legacy (py3.7 era) | Four benchmark tasks: in-hospital mortality, decompensation, length-of-stay, phenotyping | ignore | EP-110/111 (benchmark task definitions as reference) | MIMIC-III only, dormant. The idea worth borrowing: task windows and split conventions |
| FIDDLE | https://github.com/MLD3/FIDDLE | MIT | 2026-03-19 | MIMIC-III, eICU | Python | Systematic, data-driven feature engineering over structured EHR tables | ignore | — | No MIMIC-IV target. The idea worth borrowing: pre-/post-filter rules for automated feature type handling |
| TemporAI | https://github.com/vanderschaarlab/temporai | Apache-2.0 | 2023-12-14 | dataset-agnostic | Python | ML-centric toolkit for medical time series (successor to clairvoyance) | ignore | — | Dormant since 2023; no MIMIC-IV loader. The idea worth borrowing: its task taxonomy (prediction / time-to-event / treatment effects) |
| clairvoyance | https://github.com/vanderschaarlab/clairvoyance | GPL-3.0 | 2022-09-16 | MIMIC-III era | Python | End-to-end AutoML pipeline for medical time series | ignore | — | Dormant, MIMIC-III era, and GPL-3.0 — D-34 keeps GPL out of core groups; nothing here justifies the opt-in `gpl` group |
| pyICU (aidh-ms) | https://github.com/aidh-ms/pyICU | MIT | 2024-01-22 | ICU databases (ricu lineage) | Python | Early-stage Python toolbox for ICU time-series concepts, inspired by ricu | ignore | — | Dormant and early-stage (4 stars). The idea worth borrowing: ricu-style concept dictionaries expressed in Python |
| ricu | https://github.com/eth-mds/ricu | GPL-3.0 | 2025-09-03 | MIMIC-III/IV, eICU, HiRID, AUMCdb | R | Cross-dataset ICU concept dictionaries and loaders | ignore | — | R-dependent and GPL-3.0. The idea worth borrowing: dataset-agnostic named clinical concepts (informs our concept registry naming) |
| YAIB (Yet Another ICU Benchmark) | https://github.com/rvandewater/YAIB | MIT | 2026-06-08 | MIMIC-III/IV (extraction via the ricu ecosystem) | >= 3.10 | Standardized ICU prediction benchmarks: cohorts, tasks, models | ignore | EP-110/111 (harmonized task definitions as reference) | Extraction leans on R `ricu`-derived data, so we cannot adopt the pipeline; the repo itself is active — revisit at a re-plan |
| medspaCy | https://github.com/medspacy/medspacy | MIT | 2026-06-04 | free text (dataset-agnostic) | >= 3.8 | Clinical NLP on spaCy: sectionizer, ConText (negation/uncertainty), target rules | adopt | P10 text track (EP-148+), via the `text` dependency group | The `text` group is opt-in and still empty; `[tool.uv] conflicts` pins `ui` vs `text`, so it can never be co-installed with Streamlit (D-15 addendum) |
| MIMIC-IV Clinical Database Demo | https://physionet.org/content/mimic-iv-demo/2.2/ | ODbL 1.0 | 2023-01-31 (v2.2 release) | MIMIC-IV 2.2 subset | CSV files | Open 100-patient demo of hosp + icu (26 tables; no note text) | adopt | EP-22 (demo tier) | Redistributable (attribution + share-alike); demo-tier screenshots are the only row-level screenshots allowed (GOVERNANCE §6) |
| MIMIC-IV-ED Demo | https://physionet.org/content/mimic-iv-ed-demo/2.2/ | ODbL 1.0 | 2023-02-08 (v2.2 release) | MIMIC-IV-ED 2.2 subset | CSV files | Open 100-patient demo of the six ED tables | adopt | EP-22 (demo tier) | Same ODbL terms as the hosp/icu demo |
| mimic-iv-dbt (saywurdson) | https://github.com/saywurdson/mimic-iv-dbt | Apache-2.0 | 2026-06-15 | MIMIC-IV → OMOP CDM 5.4 | dbt-duckdb | dbt + DuckDB pipeline to OMOP CDM 5.4 with clinical marts | ignore | — | Found during the awesome-list sweep (GitHub search). dbt-duckdb is parked (D-20); the strongest local-OMOP reference for v2 OMOP-1 |
| deidentify (nedap) | https://github.com/nedap/deidentify | MIT | 2025-11-17 | free clinical text (dataset-agnostic) | Python | NLP de-identification of medical records | ignore | — | Discovered via awesome-healthcare-ai. MIMIC is already de-identified; method reference only for P10 note handling |

## Awesome lists

Three-plus curated lists were checked live (same method and date as above). The planning
research left open whether any of them carries a MIMIC-tooling section — the answer is **no**:
none of the four lists below has one, and a GitHub name search for `awesome-mimic*` on
2026-08-28 found no curated MIMIC awesome-list at all. The general lists carry health-IT
software, papers and other lists; the MIMIC-IV-relevant additions to the main table
(MEDS_transforms, MEDS-DEV, meds-tab, mimic-iv-dbt, deidentify) came out of this sweep —
deidentify directly from awesome-healthcare-ai, the rest from the GitHub searches run while
walking the lists.

| List | URL | License | Last activity (as of 2026-08-28) | MIMIC-tooling section? | Notes |
|---|---|---|---|---|---|
| awesome-healthcare (kakoni) | https://github.com/kakoni/awesome-healthcare | CC0-1.0 | 2026-05-05 | no — zero MIMIC mentions | Health-IT software: EHR systems, FHIR tooling, imaging; active and large, but orthogonal to MIMIC analytics |
| awesome-clinical-nlp (OHNLP) | https://github.com/OHNLP/awesome-clinical-nlp | GPL-3.0 | 2020-10-30 | no | Clinical NLP papers/tools; dormant since 2020 |
| awesome-ehr-deeplearning (hurcy) | https://github.com/hurcy/awesome-ehr-deeplearning | CC0-1.0 | 2022-10-07 | no — papers only (MIMIC-III papers such as MIMIC-Extract appear, no tooling section) | Curated EHR ML paper list; dormant |
| awesome-healthcare-ai (medtorch) | https://github.com/medtorch/awesome-healthcare-ai | CC0-1.0 | 2025-01-20 | no | Tools + datasets list; source of the deidentify row above |

## Borrow map

One line per borrow so the loader, cohort, spine, ML and UI briefs cite this table instead of
re-researching.

| Our EP | Resource | Exactly what we borrow |
|---|---|---|
| EP-10 / EP-28 | mimic-code | `mimic-iv/buildmimic/postgres/validate.sql` expected row counts (EP-10 shipped: the reconciliation source in `raw-inventory.md`) |
| EP-17 / EP-18 | mimic-code | `mimic-iv/buildmimic/duckdb/build_mimic.sh` COPY options and its progress-table pattern |
| EP-22 | PhysioNet demo datasets | the ODbL demo CSVs as the `demo` tier |
| EP-37 / EP-38 | mimic-code | `mimic-iv/concepts_duckdb` SQL (vendored; fixes ported per D-19) |
| EP-46 | ACES | the YAML predicate / trigger / window specification shape |
| EP-50 | MEDS | the 0.4 core event columns (subject_id, time, code, numeric_value) for the spine's interop surface |
| EP-58 / EP-64 | mimic-iv-visualization (jaanli/altosaar) | the small-cell suppression pattern and aggregate-only views behind the UI |
| EP-102 | healthylaife pipeline + MIMIC-Extract | cohort/feature extraction step ordering and hourly binning with typed feature grouping |
| EP-110 / EP-111 | PyHealth (+ mimic3-benchmarks, YAIB) | readmission / in-hospital-mortality task windows, labels and split conventions |
| EP-138 / EP-143 | mimic-code | the `src/mimic_utils` sqlglot transpiler and `mimic-iv/concepts/concept_map/*.csv` (chartevents → LOINC/OMOP, procedureevents → OMOP/SNOMED) |
