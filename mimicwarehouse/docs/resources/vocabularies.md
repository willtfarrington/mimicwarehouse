# Ontologies & vocabularies inventory (EP-14, D-35)

Every external vocabulary, code system and mapping the v1 roadmap touches, with steward,
license, registration path and an explicit verdict — so the code-set (EP-40), curation
(EP-39), phenotype (EP-41/42) and linkage (EP-143) briefs cite one line here instead of
re-researching acquisition and licensing. **D-35** fixes the order: free vocabularies first
(ICD-9/10, LOINC, RxNorm, ATC, AHRQ CCSR/Elixhauser/Charlson, CMS GEMs); UMLS/SNOMED/OMOP
Athena later and optional (the owner has no UTS account). GOVERNANCE §10 requires every
vocabulary to appear in this register and, once downloaded, in an
`ext\vocab\<source>\<version>\source.yaml` (template below; DESIGN §19).

- **Checked on:** 2026-08-28. Every URL below was fetched live this day — via the harness
  web fetcher, or via `curl` with a browser user-agent for cms.gov / fda.gov / loinc.org
  (their servers refuse the default fetcher); `www.cdc.gov` refuses all automated fetch,
  so the register cites NCHS's file host `ftp.cdc.gov` (fetched, directory listing
  verified). Quan 2005 was verified via PubMed; the mimic-code `concept_map` listing via
  the GitHub REST API (`gh api`).
- **Verdict vocabulary:** **use** = a v1 brief needs it and a free path is confirmed ·
  **later** = optional, parked for v2 (acquisition trigger recorded in the roadmap) ·
  **flag** = proprietary system whose codes appear in MIMIC-IV; we label those rows and
  never obtain the licensed product.
- **How to add a row:** fetch the steward URL the same day, fill every cell (ISO dates,
  hyphenated), pick a verdict from the vocabulary above, and name the EP that uses or
  parks it; once a file is actually downloaded, write its `source.yaml` (below) the same
  day.

## Register

| Vocabulary | Steward / URL | Version cadence | License | Registration / DUA | Redistributable in this repo? | Where it appears in MIMIC-IV | Needed by EP | v1 verdict | How to obtain (steps) |
|---|---|---|---|---|---|---|---|---|---|
| ICD-9-CM (diagnoses + procedures) | CMS/NCHS — https://www.cms.gov/medicare/coding-billing/icd-10-codes/icd-9-cm-diagnosis-procedure-codes-abbreviated-and-full-code-titles | frozen — v32 (FY2015) is final; no further updates | US public domain | none | yes | `diagnoses_icd` / `procedures_icd` rows with `icd_version = 9`; dims `d_icd_diagnoses` / `d_icd_procedures` | EP-40, EP-41/42 | use | 1. Open the CMS page. 2. Download the v32 abbreviated + full code-title ZIPs. 3. Land under `ext\vocab\icd9cm\v32\` and write `source.yaml`. |
| ICD-10-CM | NCHS/CDC (guidelines also on CMS) — https://ftp.cdc.gov/pub/Health_Statistics/NCHS/Publications/ICD10CM/ | annual (Oct 1) + mid-year April updates; FY2026 current, FY2027 posted | US public domain | none | yes | `diagnoses_icd` rows with `icd_version = 10`; `d_icd_diagnoses` | EP-40, EP-41/42 | use | 1. Open the NCHS FTP directory. 2. Enter the fiscal-year folder (e.g. `2026/` or `2026-update/`). 3. Download the code descriptions + tabular/index ZIPs. 4. Land under `ext\vocab\icd10cm\fy2026\` and write `source.yaml`. |
| ICD-10-PCS | CMS — https://www.cms.gov/medicare/coding-billing/icd-10-codes | annual (Oct 1); FY2026 current, FY2027 guidelines posted | US public domain | none | yes | `procedures_icd` rows with `icd_version = 10`; `d_icd_procedures` | EP-40 | use | 1. Open the CMS ICD-10 page. 2. Download the fiscal-year PCS code tables/index and codes-file ZIPs. 3. Land under `ext\vocab\icd10pcs\fy2026\` and write `source.yaml`. |
| LOINC | Regenstrief Institute — https://loinc.org/downloads/ | ~2 releases/yr; 2.83 released 2026-08-19 | LOINC License (free; **do not redistribute the table**) — https://loinc.org/license/ | free account; downloads sit behind login | no | not directly: `d_labitems` dropped `loinc_code` in MIMIC-IV 2.x; itemid → LOINC via the vendored mimic-code `concept_map` (see notes) | EP-39, EP-143 | use | 1. Create a free account at loinc.org. 2. Accept the license on the downloads page. 3. Download the LOINC table (core CSV release). 4. Land under `ext\vocab\loinc\2.83\`; only the `source.yaml` hash record may ever be committed. |
| RxNorm (full release) | NLM — https://www.nlm.nih.gov/research/umls/rxnorm/index.html | monthly full release + weekly SPL updates | UMLS Metathesaurus License | UTS account + UMLS license (owner has none) | no | `prescriptions` drug names / `ndc` / `gsn`; `emar.medication` | EP-41/42, EP-143 (alternate path) | later | 1. Owner obtains a UTS account (parked; D-35). 2. Accept the UMLS license. 3. Download RxNorm full monthly release. 4. Land under `ext\vocab\rxnorm\<release>\`. |
| RxNorm Current Prescribable Content | NLM — https://www.nlm.nih.gov/research/umls/rxnorm/docs/prescribe.html | monthly, with weekly SPL updates in between | US public domain (NLM provides this subset without licensing restrictions) | none (no login) | yes | same columns as the full release; US-marketed prescribables only, no First Databank / Micromedex content | EP-41/42, EP-143 | use | 1. Open the Prescribable Content page. 2. Download the current monthly RxNorm-in-RRF subset ZIP. 3. Land under `ext\vocab\rxnorm-cpc\<release>\` and write `source.yaml`. |
| ATC/DDD | WHO Collaborating Centre for Drug Statistics Methodology (FHI, Oslo) — https://atcddd.fhi.no/atc_ddd_index/ | annual edition; 2026 edition updated 2026-01-20 | copyrighted; complete index files are purchased from the WHO CC | purchase order for bulk files; online index search is free | no | not directly — reached from `prescriptions` via NDC/GSN → RxNorm → ATC (that relationship path needs UMLS) or via manually curated class lists | EP-143 | later | 1. Use the free online index for spot lookups. 2. Bulk files: order via the site's "Order ATC Index" (owner decision; parked). 3. EP-143 picks whichever free path exists at execution (Elixhauser or the LOINC map are its free fallbacks). |
| AHRQ HCUP CCSR (ICD-10-CM dx + ICD-10-PCS px) | AHRQ HCUP — https://hcup-us.ahrq.gov/toolssoftware/ccsr/ccs_refined.jsp | annual; v2026.1 (Nov 2025), covers codes valid 2015-10 → 2026-09 | public; HCUP citation requested | none | yes | groups `diagnoses_icd` / `procedures_icd` v10 codes into ~530 diagnosis / ~320 procedure categories | EP-40 | use | 1. Open the CCSR page. 2. Download the CM and PCS CCSR tool ZIPs (CSV mapping + documentation). 3. Land under `ext\vocab\ccsr\v2026-1\` and write `source.yaml`. 4. Cite per the page's Internet Citation block. |
| AHRQ Elixhauser Comorbidity Software Refined | AHRQ HCUP — https://hcup-us.ahrq.gov/toolssoftware/comorbidityicd10/comorbidity_icd10.jsp | annual; v2026.1 (Nov 2025), 38 measures | public; HCUP citation requested | none | yes | secondary `diagnoses_icd` v10 codes → 38 comorbidity flags | EP-37/38 (the Elixhauser free path: this download, or SQL written there — no Elixhauser SQL is vendored), EP-40, EP-143 | use | 1. Open the Elixhauser CSR page. 2. Download the software ZIP (SAS program + CSV reference files). 3. Land under `ext\vocab\elixhauser\v2026-1\` and write `source.yaml`. |
| Charlson + Elixhauser ICD-9/10 code lists (Quan 2005) | Med Care 43(11):1130-9 — https://doi.org/10.1097/01.mlr.0000182534.19832.83 | static (2005 paper; 17 Charlson + 30 Elixhauser conditions, ICD-9-CM and ICD-10 algorithms) | journal article; the code lists are reproduced in open implementations (vendored mimic-code, MIT) | none | yes | `diagnoses_icd` both versions; the Charlson arm is implemented by the vendored `comorbidity/charlson.sql` (both SQL dialects); the Elixhauser arm is **not** vendored (AHRQ row above) | EP-37, EP-40 | use | 1. Charlson: nothing to download — the vendored mimic-code `charlson.sql` implements it. 2. Elixhauser: the AHRQ download in the row above, or SQL written at EP-37/38. 3. Cite the paper (DOI at left) wherever the lists are used. |
| CMS GEMs (ICD-9-CM ↔ ICD-10-CM/PCS) | CMS — https://www.cms.gov/medicare/coding-billing/icd-10-codes/icd-10-cm-icd-10-pcs-gem-archive | frozen — the 2018 files are the final GEMs release | US public domain | none | yes | crosswalk between `icd_version` 9 and 10 codes; carries many-to-many and no-map flags | EP-40 (GEM utility) | use | 1. Open the CMS GEM archive ("ICD-10 Files & News Archive"). 2. Download the 2018 ICD-10-CM GEM and 2018 ICD-10-PCS GEM ZIPs. 3. Land under `ext\vocab\gem\2018\` and write `source.yaml` — since EP-40 `mwh codeset gem fetch` does all three (both zips, the four text files verified against pinned sha256, the register). |
| NDC Directory | FDA — https://www.fda.gov/drugs/drug-approvals-and-databases/national-drug-code-directory | updated daily | US public domain | none | yes | `prescriptions.ndc` | EP-41/42 | use | 1. Open the FDA NDC Directory page. 2. Download the NDC database file (ZIP of the SPL-derived directory; also served via open.fda.gov). 3. Land under `ext\vocab\ndc\<date>\` and write `source.yaml`. |
| HCPCS Level II | CMS — https://www.cms.gov/medicare/coding-billing/healthcare-common-procedure-system/quarterly-update | quarterly | US public domain | none | yes | `d_hcpcs` / `hcpcsevents` Level II (alphanumeric) codes | EP-40 | use | 1. Open the CMS HCPCS Quarterly Update page. 2. Download the current quarter's code-set ZIP. 3. Land under `ext\vocab\hcpcs2\<quarter>\` and write `source.yaml`. |
| CPT (HCPCS Level I) | AMA — https://www.ama-assn.org/practice-management/cpt | annual | AMA copyright; use and distribution require an AMA license | AMA license (paid) | no | CPT-shaped rows in `d_hcpcs` / `hcpcsevents` (MIMIC ships short descriptions only) | EP-40 (labelling only) | flag | 1. Do not obtain. 2. EP-40 labels `d_hcpcs` / `hcpcsevents` rows as CPT vs Level II by code shape and works with the shipped short descriptions. |
| MS-DRG | CMS — https://www.cms.gov/medicare/payment/prospective-payment-systems/acute-inpatient-pps/ms-drg-classifications-and-software | annual with the IPPS final rule; FY2027 (V44) errata already posted | US public domain (definitions manual + grouper software free) | none | yes | `drgcodes` rows with `drg_type = 'HCFA'` | EP-40 | use | 1. Open the CMS MS-DRG page. 2. Download the definitions manual (text version) for the fiscal years spanned by the data. 3. Land under `ext\vocab\msdrg\<version>\` and write `source.yaml`. |
| APR-DRG | Solventum (formerly 3M HIS) — https://www.solventum.com/en-us/home/health-information-technology/solutions/apr-drg/ | annual | proprietary (commercial grouper) | commercial license | no | `drgcodes` rows with `drg_type = 'APR'` (severity-of-illness / risk-of-mortality subclasses in `description`) | EP-40 (labelling only) | flag | 1. Do not obtain. 2. EP-40 labels `drgcodes` rows by `drg_type` and keeps APR severity/mortality parsing to the shipped description strings. |
| OMOP Athena vocabulary bundle | OHDSI — https://athena.ohdsi.org/ | rolling releases per download bundle | open source unless otherwise specified per vocabulary; some (e.g. CPT4) need their own license | free account | no | mapping target of the vendored `concept_map` `*_to_omop.csv` files | none in v1 — parked (v2 OMOP-1) | later | 1. Create a free Athena account (parked). 2. Select vocabularies, accepting each per-vocab license. 3. Download the bundle; land under `ext\vocab\athena\<bundle-date>\`. |
| SNOMED CT (US Edition) | SNOMED International; NLM is the US National Release Center — https://www.nlm.nih.gov/healthit/snomedct/index.html | twice-yearly US Edition releases | SNOMED affiliate terms via the UMLS license; not redistributable | UTS account + UMLS license (owner has none) | no | mapping target of the vendored `concept_map/procedureevents_to_snomed.csv` | P10 / TXT-1 (parked) | later | 1. Owner obtains a UTS account (parked; D-35). 2. Accept the UMLS license. 3. Download the US Edition RF2 release; land under `ext\vocab\snomedct-us\<release>\`. |
| UMLS Metathesaurus | NLM — https://www.nlm.nih.gov/research/umls/index.html | twice yearly (versioned `<year>AA` / `<year>AB`) | UMLS license — free, issued to individuals only; source vocabularies keep their own restrictions | UTS account | no | not directly; unlocks the RxNorm→ATC and SNOMED relationship paths (EP-143 alternates, P10) | P10 (parked); alternate path for EP-143 | later | 1. Owner requests a UTS account + UMLS license (parked; D-35). 2. Download the Metathesaurus full release. 3. Land under `ext\vocab\umls\<release>\`. |
| MIMIC-IV internal dictionaries | PhysioNet / MIT-LCP — https://physionet.org/content/mimiciv/3.1/ | with MIMIC-IV releases; 3.1 published 2024-10-11 | PhysioNet Credentialed Health Data License 1.5.0 (the ODbL Demo, EP-22, ships the same dims redistributably) | CITI training + per-dataset DUA (owner holds both) | no | the vocabulary of record for what is *in* the data: `d_items` (MetaVision itemids), `d_labitems`, `d_hcpcs`, `d_icd_diagnoses` / `d_icd_procedures`, microbiology organism/antibiotic names, `omr.result_name` | EP-39, EP-41/42 | use | 1. Nothing to download — already on disk under `source material/` (EP-10 inventory). 2. Query dims only through `safe_query` / `mwh sql` once EP-30 ships. 3. Row-level screenshots only from the ODbL Demo tier. |

### Notes on the register

- **WHO ICD-10 ≠ ICD-10-CM.** MIMIC-IV codes diagnoses in the US *clinical modification*
  (ICD-10-CM) and procedures in ICD-10-PCS; WHO's international ICD-10 is a different,
  coarser code set — never mix the two when building code sets.
- **Every diagnosis code set is dual.** BIDMC switched ICD-9 → ICD-10 around 2015 and
  MIMIC-IV admissions span both eras, so every EP-40/EP-41 code set needs an ICD-9-CM
  *and* an ICD-10-CM/PCS arm, cross-walked with the (frozen, 2018-final) CMS GEMs.
- **itemid → LOINC.** `d_labitems` no longer carries `loinc_code` (removed in
  MIMIC-IV 2.x). The vendored mimic-code `concept_map` provides
  `chartevents_to_loinc.csv`, `chartevents_to_omop.csv`, `procedureevents_to_omop.csv`
  and `procedureevents_to_snomed.csv` (listing verified 2026-08-28); EP-39/EP-143 build
  on these, EP-138 fetches/refreshes them.
- **Proprietary columns to flag, never resolve.** `prescriptions.gsn` is a First Databank
  Generic Sequence Number (proprietary — label only); `drgcodes.drg_type` separates the
  public MS-DRG lineage (`HCFA`) from the proprietary Solventum APR-DRG (`APR`).
- **RxNorm → ATC needs UMLS.** The relationship path from RxNorm to ATC classes ships in
  UMLS-licensed content, so EP-143 decides at execution which free ingestion target it
  uses (ATC if a free path has appeared, else Elixhauser or the LOINC map).

## `ext/vocab/` landing convention

Downloaded vocabularies land under the data root at
`<data_root>\ext\vocab\<source>\<version>\` — built in code as
`settings.layout["ext"] / "vocab" / <source> / <version>` and created by the consumer.
Neither the `vocab` level nor any source below it is a `Settings.layout` key: EP-3 ships
`ext` and `ext_demo` only, and the layout contract that `mwh paths` and `test_ep03`
assert (15 keys at EP-3; 18 since EP-167 added `lake_fixture` / `lake_demo` /
`lake_rejects`) gains no vocabulary key until the first brief that actually writes here
(EP-40 or EP-143) decides otherwise (EP-7 re-plan amendment; count corrected at EP-33,
retro RES-3). `<source>` is a short lowercase slug (the
register rows above name them), `<version>` the steward's own version string.

Every landed version directory carries a `source.yaml` (DESIGN §19, GOVERNANCE §10):

```yaml
name: loinc
version: "2.83"
release_date: 2026-08-19
url: https://loinc.org/downloads/
license: LOINC License (free registration; no redistribution)
license_url: https://loinc.org/license/
registration_required: true
redistributable: false
obtained_on: 2026-08-28
obtained_by: owner
files:
  - name: Loinc.csv
    sha256: "0000000000000000000000000000000000000000000000000000000000000000"
    bytes: 0
columns_of_interest: [LOINC_NUM, COMPONENT, SYSTEM, EXAMPLE_UCUM_UNITS]
used_by_eps: [EP-39, EP-143]
notes: >
  Example record (hash and size are placeholders). The Linkage Wizard profiler (EP-137)
  writes this same shape for any external source it ingests.
```

Rules:

- **Non-redistributable vocabularies never enter git** — only their `source.yaml` hash
  record may be committed (e.g. quoted in a brief's completion note). The mechanical
  backstop is `mwh guard` G1 (`.csv` / `.parquet` / other data extensions are refused
  anywhere outside `mimicwarehouse/tests/fixtures/`) plus `.gitignore`; this holds even
  for public-domain vocabularies — license permission is recorded in the register, but
  data files stay out of the repository regardless.
- A real `source.yaml` must be a **single-document, tag-free** YAML file (the template
  above sits in a fenced block, so pre-commit's `check-yaml` only sees the real ones).
- Dates are hyphenated ISO (`YYYY-MM-DD`) — compact dates are refused by guard G4.
- The Linkage Wizard's profiler (EP-137) writes the same `source.yaml` shape for every
  external source, vocabulary or not; EP-143's reference-table ingestion is the first
  consumer of this convention.

## Which EP needs what

| EP | Vocabulary | Use | Free path in v1? |
|---|---|---|---|
| EP-37 / EP-38 | Charlson (Quan 2005), Elixhauser | comorbidity concepts: Charlson via the vendored mimic-code `charlson.sql`; Elixhauser via the AHRQ CSR download (or SQL written at EP-37/38 — nothing Elixhauser is vendored) | yes — Charlson vendored, nothing to download; Elixhauser a free AHRQ download |
| EP-39 | LOINC + MIMIC `d_labitems` / `d_items` | itemid → LOINC map, unit and itemid curation | yes — free LOINC registration + vendored `concept_map` |
| EP-40 | ICD-9-CM, ICD-10-CM/PCS, CCSR, GEMs, Elixhauser/Quan lists, HCPCS Level II, MS-DRG | dual ICD code sets, groupers, 9 ↔ 10 crosswalk, DRG/HCPCS labelling | yes — all public domain or public-with-citation |
| EP-41 / EP-42 | ICD (both versions), RxNorm Current Prescribable Content, NDC Directory, MIMIC dims | phenotype definitions: diagnosis code lists, drug-name sets, itemid lists | yes — public domain (RxNorm via the no-license subset) |
| EP-143 | ATC (preferred) or Elixhauser / LOINC map | reference-table ingestion test of the Linkage Wizard | partial — Elixhauser and the LOINC map are free; ATC bulk files are purchased and RxNorm → ATC needs UMLS, so EP-143 picks the free path at execution |
| P10 (TXT-1) | SNOMED CT, UMLS | note concept-extraction targets | no — parked until the owner obtains a UTS account |

Every **use**-verdict row in the register is public domain, public-with-citation or
free-registration, so EP-16's coverage audit can confirm each v1 vocabulary need has a
free path; the only *partial* is EP-143's preferred (but substitutable) ATC target.
