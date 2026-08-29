# Open companion datasets (EP-15, D-10)

Every open or openly-described dataset the roadmap may load, cite or park — with license,
access class and an explicit "may it enter git?" verdict, so later briefs (EP-22 demo tier,
EP-142 ED ingestion, the parked v2 items) cite one line here instead of re-researching.
Docs-only: nothing was downloaded for this brief (EP-22 fetches the demo; eICU-CRD needs
its own DUA and is parked as v2 EXT-1).

- **Checked on:** 2026-08-28. Every PhysioNet/GitHub URL below was fetched live this day
  (HTTP 200, `curl` with a browser user-agent); versions, licenses, access class and the
  open datasets' sizes are read from those pages. PhysioNet hides file sizes of
  credentialed projects from anonymous visitors, so those Size cells are approximate.
- **"May enter git?"** applies this repo's rules, not just the upstream license: the EP-4
  guard (G1) refuses data-shaped files outside `tests/fixtures/`, and GOVERNANCE §3 keeps
  even redistributable (ODbL) rows out of the tree — demo data is *fetched* by `mwh demo`
  (EP-22) into the data root, never committed. Screenshots of row-level views come only
  from the `demo`/`fixture` tiers (GOVERNANCE §6).
- **Plainly stated:** there is **no note demo** — no open sample of MIMIC-IV-Note exists,
  so all text-pipeline tests use synthetic notes (Risk 9). The demo datasets are on the
  **v2.2 schema** (pre-3.x; the EP-9 column map bridges the drift), and **MIMIC-IV-ED 2.2
  covers 2011–2019**, so partial linkage to hosp/icu stays is by design, not an error.

## Register

| Dataset | Version | Steward / URL | License | Access (open / credentialed DUA) | Size | Schema note | Planned use | May enter git? |
|---|---|---|---|---|---|---|---|---|
| MIMIC-IV Clinical Database Demo | 2.2 | PhysioNet / MIT-LCP — https://physionet.org/content/mimic-iv-demo/2.2/ | ODbL 1.0 | open | 15.5 MB uncompressed | 100 subjects, hosp + icu modules only; **v2.2 schema** — the EP-9 column map bridges it to our 3.1 contract | EP-22 `demo` tier; row-level screenshots (GOVERNANCE §6); EP-158 cloner smoke test | no |
| MIMIC-IV-ED Demo | 2.2 | PhysioNet / MIT-LCP — https://physionet.org/content/mimic-iv-ed-demo/2.2/ | ODbL 1.0 | open | 111.8 KB uncompressed | ED tables (edstays, triage, vitalsign, …) for the demo subjects; same 2011–2019 window as the full ED module | EP-22 (fetched beside the demo) → EP-142 Linkage Wizard development | no |
| MIMIC-IV Clinical Database Demo on FHIR | 2.1.0 | PhysioNet / MIT-LCP — https://physionet.org/content/mimic-iv-fhir-demo/2.1.0/ | ODbL 1.0 | open | 49.5 MB uncompressed | FHIR R4 NDJSON bundles of the demo; the full export (`mimic-iv-fhir` 2.1) is a **separate credentialed DUA** and lags MIMIC-IV releases | parked — v2 FHIR-1 (trigger: an interoperability EP needs FHIR shapes) | no |
| MIMIC-IV demo data in MEDS | 0.0.1 | PhysioNet / MIT-LCP — https://physionet.org/content/mimic-iv-demo-meds/0.0.1/ | ODbL 1.0 | open | 5.7 MB uncompressed | Medical Event Data Standard event-stream layout built from the v1.0-era demo | parked — optional MEDS validation lane over the EP-50 spine | no |
| MIMIC-IV demo data in OMOP CDM | 0.9 | PhysioNet / MIT-LCP — https://physionet.org/content/mimic-iv-demo-omop/0.9/ | ODbL 1.0 | open | 73.0 MB uncompressed | OMOP Common Data Model mapping of the v1.0-era demo (vocabulary tables included) | parked — v2 OMOP-1 (trigger: an OHDSI-tooling EP) | no |
| MIMIC-III Clinical Database Demo | 1.4 | PhysioNet / MIT-LCP — https://physionet.org/content/mimiciii-demo/1.4/ | ODbL 1.0 | open | 103.0 MB uncompressed | MIMIC-III schema (CareVue + MetaVision era, different table set); superseded by MIMIC-IV | ignore — kept here so nobody re-evaluates it | no |
| eICU Collaborative Research Database | 2.0 | PhysioNet / MIT-LCP — https://physionet.org/content/eicu-crd/2.0/ | PhysioNet Credentialed Health Data License 1.5.0 | credentialed DUA (**not held** — its own DUA, separate from MIMIC's) | hidden pre-login (multi-center, ~200k ICU stays, 2014–2015) | multi-center US ICU database; entirely different schema (per-hospital offsets, no calendar anchors) | parked — v2 EXT-1 external validation (trigger: a P6 signature model worth validating externally) | no |
| eICU Collaborative Research Database Demo | 2.0.1 | PhysioNet / MIT-LCP — https://physionet.org/content/eicu-crd-demo/2.0.1/ | ODbL 1.0 | open | 130.6 MB uncompressed | ~2,500-stay open sample of eICU-CRD | parked with EXT-1 — lets schema exploration start before the DUA | no |
| Synthea | rolling (GitHub `master`) | MITRE — https://github.com/synthetichealth/synthea | Apache-2.0 (generator and outputs) | open | n/a (generator; output size is configuration-dependent) | fully synthetic longitudinal EHR (FHIR/CSV); **not MIMIC-shaped** — no itemids, no ICU chart events | parked — possible fixture enrichment (trigger: a fixture gap our EP-11/12 generators cannot fill); any adoption regenerates through our fixture pipeline, ids ≥ 90 000 000 | no |
| MIMIC-CXR | 2.1.0 | PhysioNet / MIT-LCP — https://physionet.org/content/mimic-cxr/2.1.0/ | PhysioNet Credentialed Health Data License 1.5.0 | credentialed DUA (not held) | hidden pre-login (DICOM studies; multi-TB) | chest radiographs + reports, linkable to MIMIC-IV via `subject_id` band | parked — v2 LINK-CXR (trigger: an imaging-linkage EP) | no |
| MIMIC-IV-ECG | 1.0 | PhysioNet / MIT-LCP — https://physionet.org/content/mimic-iv-ecg/1.0/ | ODbL 1.0 | open | 90.4 GB uncompressed | ~800k diagnostic 12-lead ECGs matched to MIMIC-IV patients | parked — v2 LINK-ECG (trigger: a waveform/ECG EP; open access, so no DUA blocker) | no |
| MIMIC-IV Waveform Database | 0.1.0 | PhysioNet / MIT-LCP — https://physionet.org/content/mimic4wdb/0.1.0/ | ODbL 1.0 | open | 12.8 GB uncompressed | bedside monitor waveforms + numerics for a matched subset; index tables link to `icu` stays | parked — v2 LINK-WDB (trigger: a high-frequency-signals EP) | no |
| mimicwarehouse synthetic fixture | regenerated per EP (EP-11/12) | this repo — https://github.com/willtfarrington/mimicwarehouse (under `mimicwarehouse/tests/fixtures/`) | MIT (fully synthetic) | open | a few MB | MIMIC-IV 3.1 schema per the EP-9 contract; ids ≥ 90 000 000 by construction (guard G4 enforces the floor) | every fixture-tier test; the only data-shaped files git may carry | yes |

## Notes

- **No note demo exists.** MIMIC-IV-Note (v2.2, credentialed) has no open sample; the
  text-track EPs (P10) develop and test exclusively on synthetic notes, and note text
  never enters tool output or git regardless (GOVERNANCE §9).
- **Demo schema lag.** The demo family (clinical, ED, FHIR, MEDS, OMOP) trails the
  credentialed releases (2.2 / v1.0-era vs 3.1). Treat demo-derived column lists as
  *v2.2 evidence only*; the EP-9 schema contract is authoritative for 3.1.
- **ED window.** MIMIC-IV-ED 2.2 covers 2011–2019 while hosp/icu cover 2008–2022, so ED
  linkage coverage below 100 % is expected and is measured, not "fixed", by EP-139.
- **eICU offsets.** eICU-CRD uses minute offsets from ICU admission with no calendar
  anchor at all — porting anything there means re-deriving every time axis (EXT-1 scoping
  note).
- **Licenses.** ODbL 1.0 = attribution + share-alike; redistribution is upstream-legal but
  repo-forbidden (G1/GOVERNANCE §3) — cite and fetch instead. The PhysioNet license text
  is linked from `reading.md` §36.

## Demo tier (EP-22)

The `demo` tier is built from the two open datasets below, fetched on demand by
`mwh demo fetch` into `%MWH_DATA_ROOT%\ext\demo\` and recorded (name, version, license,
URL, per-file sha256) in `ext\demo\source.yaml` — the licensing-register precursor
(D-36). Both are distributed under the **Open Data Commons Open Database License (ODbL)
v1.0**: attribution + share-alike. Attribution for anything shown or published from the
demo tier (screenshots, demo mode, the cloner smoke test): *"Contains data from the
MIMIC-IV Clinical Database Demo and MIMIC-IV-ED Demo (PhysioNet / MIT-LCP), used under
ODbL 1.0."* The data itself never enters git (GOVERNANCE §3; demo `subject_id`s sit
inside the real MIMIC id bands, so the guard treats demo rows as real).

Citations (DOIs verified against doi.org and the PhysioNet pages, 2026-08-29):

**MIMIC-IV Clinical Database Demo v2.2**
Johnson, A., Bulgarelli, L., Pollard, T., Horng, S., Celi, L. A., & Mark, R. (2023).
MIMIC-IV Clinical Database Demo (version 2.2). PhysioNet.
<https://doi.org/10.13026/dp1f-ex47>

**MIMIC-IV-ED Demo v2.2**
Johnson, A., Bulgarelli, L., Pollard, T., Celi, L. A., Horng, S., & Mark, R. (2023).
MIMIC-IV-ED Demo (version 2.2). PhysioNet.
<https://doi.org/10.13026/jzz5-vs76>

**PhysioNet**
Goldberger, A., Amaral, L., Glass, L., Hausdorff, J., Ivanov, P. C., Mark, R.,
Mietus, J. E., Moody, G. B., Peng, C. K., & Stanley, H. E. (2000). PhysioBank,
PhysioToolkit, and PhysioNet: Components of a new research resource for complex
physiologic signals. *Circulation*, 101(23), e215–e220.
