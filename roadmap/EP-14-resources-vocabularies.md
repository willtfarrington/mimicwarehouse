# EP-14 — Ontologies & vocabularies inventory

**Size:** M · **Tier:** n/a · **Core/Stretch:** core · **Depends on:** — · **Blocks:** EP-16 (Re-plan P1), EP-143 (Reference-table ingestion via wizard (ATC / Elixhauser / LOINC map))

> **Amended at EP-7 re-plan (2026-08-17).** Checked against the P0 code; header facts unchanged.
> (1) **Landing path — one convention:** the Context said `<data_root>\ext\<source>\`, item 2 says
> `<data_root>\ext\vocab\<source>\<version>\`; the brief now uses the **item-2 form** everywhere. Neither
> level is a `Settings.layout` key (EP-3 ships `ext` and `ext_demo` only) — consumers build the path as
> `settings.layout["ext"] / "vocab" / <source> / <version>` and create it themselves; do **not** add a layout
> key here (that would change the 15-key contract `mwh paths` and `test_ep03` assert — a decision for the
> brief that first writes there, EP-40/EP-143). `%MWH_DATA_ROOT%` in the text means "under
> `settings.data_root`" (no env var is set on this machine; default `C:\mimicdata`). (2) The mechanical
> backstop for "non-redistributable vocabularies never enter git" is `mwh guard` G1 (`.csv`/`.parquet`/… are
> refused anywhere outside `mimicwarehouse/tests/fixtures/`) plus `.gitignore` — cite it. (3) `mwh verify
> EP-14` runs `pytest -m ep_14` because the test module exists (EP-5 precedent) — `pytestmark` required.
> (4) G4 hygiene: hyphenated ISO dates in the register (compact `YYYYMMDD` is refused). (5) The `source.yaml`
> template lives in a fenced block inside `.md`, so `check-yaml` does not see it; a real `source.yaml` (EP-40+)
> must be single-document, tag-free. Command forms: `uv run mwh …` ≡ `uv run --group dev mwh …`.

## Context

Code sets (EP-40), phenotypes (EP-41/42), unit/itemid curation (EP-39), the reference-table ingestion
test of the Linkage Wizard (EP-143) and the optional text track (P10) all need external vocabularies,
each with its own license and acquisition path. **D-35** fixes the order: free vocabularies first
(ICD-9/10, LOINC, RxNorm, ATC, AHRQ CCSR/Elixhauser/Charlson, CMS GEMs); UMLS/SNOMED/OMOP Athena
later and optional (the owner has no UTS account). GOVERNANCE §10 requires every vocabulary to be
recorded in `docs/resources/vocabularies.md` and, once downloaded, in an `ext/vocab/<source>/<version>/source.yaml`
(DESIGN §19; `settings.layout["ext"] / "vocab" / …` — amended EP-7, one convention with item 2). MIMIC's own dimension tables (`d_icd_diagnoses`,
`d_icd_procedures`, `d_labitems`, `d_items`, `d_hcpcs`) are the vocabulary of record for what is *in* the
data; the 3.1 dims are under the credentialed license, while the ODbL Demo (EP-22) ships the same dims
redistributably. Two MIMIC facts shape the register: the ICD-9 → ICD-10 switch (~2015) means every
diagnosis code set is dual (ICD-9-CM + ICD-10-CM/PCS, GEMs to cross-walk), and `d_labitems` no longer
carries `loinc_code` (removed in MIMIC-IV 2.x), so itemid → LOINC comes from mimic-code's `concept_map`.
Docs-only (tier n/a): research and write; download nothing (that is EP-40/EP-143's job).

## In scope

1. **`mimicwarehouse/docs/resources/vocabularies.md`** — header (purpose, D-35 order, "checked on" date) and the
   **register table**: `Vocabulary | Steward / URL | Version cadence | License | Registration / DUA | Redistributable
   in this repo? | Where it appears in MIMIC-IV | Needed by EP | v1 verdict | How to obtain (steps)`. Rows (verify
   each live): ICD-9-CM (CMS/CDC, public domain; `icd_version = 9`), ICD-10-CM and ICD-10-PCS (CMS/CDC, public;
   `icd_version = 10`; note WHO ICD-10 ≠ ICD-10-CM), LOINC (Regenstrief; free registration; **do not redistribute
   the table**; itemid → LOINC via mimic-code `concept_map` → EP-39/EP-143), RxNorm (NLM; full release needs a
   UMLS license; check the "Current Prescribable Content" subset terms; `prescriptions.ndc`/`gsn` are NDC and
   First Databank GSN, GSN proprietary), ATC/DDD (WHO CC Oslo; index copyrighted, bulk purchase; note the
   RxNorm-ATC relationship path needs UMLS → EP-143 picks whichever free path exists at execution),
   AHRQ HCUP CCSR for ICD-10-CM/PCS (public, cite; → EP-40), AHRQ Elixhauser Comorbidity Software Refined
   (public; plus Quan 2005 ICD-9/10 lists → EP-40/EP-143), Charlson (Quan 2005 mapping; mimic-code
   `comorbidity/charlson.sql` implements it → EP-37), CMS GEMs ICD-9-CM ↔ ICD-10-CM/PCS (public, 2018 final;
   many-to-many/no-map flags → EP-40 GEM utility), NDC directory (FDA, public), HCPCS Level II (CMS, public) vs
   CPT (AMA, licensed — flag `d_hcpcs`/`hcpcsevents` accordingly), MS-DRG (CMS, public) vs APR-DRG (3M,
   proprietary; `drgcodes.drg_type`), OMOP Athena bundle (free account, per-vocab licenses; later), SNOMED CT
   (UMLS/NLM in the US; not redistributable; later; text track TXT-1), UMLS Metathesaurus (UTS license; later),
   MIMIC internal dictionaries (`d_items` MetaVision itemids, `d_labitems`, microbiology organism/antibiotic
   names, `omr.result_name` — PhysioNet credentialed license for 3.1; ODbL via the Demo).
2. **`ext/vocab/` landing convention** — a section specifying `<data_root>\ext\vocab\<source>\<version>\`
   (built as `settings.layout["ext"] / "vocab" / …`; not a layout key — amended EP-7)
   with a `source.yaml` template: `name, version, release_date, url, license, license_url, registration_required,
   redistributable, obtained_on, obtained_by, files: [{name, sha256, bytes}], columns_of_interest, used_by_eps,
   notes`; rule that non-redistributable vocabularies never enter git (only their `source.yaml` hash record does),
   and that the wizard's profiler (EP-137) writes the same shape for any external source. Cross-reference DESIGN
   §19 and GOVERNANCE §10.
3. **"Which EP needs what" table** — `EP | Vocabulary | Use | Free path in v1?` for EP-37/38 (Charlson, Elixhauser
   via mimic-code), EP-39 (LOINC map, unit names), EP-40 (ICD-9/10 dual sets, CCSR, GEMs), EP-41/42 (ICD, drug
   names, itemids), EP-143 (ATC / Elixhauser / LOINC map ingestion), P10 (SNOMED/UMLS — parked unless a UTS account
   appears), so EP-16's coverage audit can confirm every v1 vocabulary need has a free path.
4. **Index** — add the `vocabularies.md` row to `mimicwarehouse/docs/resources/README.md` (create the index if EP-13/15
   have not yet).
5. **Test** (`tests/ep/test_ep14.py`, `@pytest.mark.ep_14`): file exists; the register table has the ten column
   headers and ≥ 15 rows; every row has a non-empty License and a `yes`/`no` Redistributable cell; the `source.yaml`
   template block parses as YAML with the listed keys; the file contains no token in the real id bands.

## Out of scope

- Downloading any vocabulary or building code sets → EP-40 (ICD sets, GEMs), EP-143 (ATC/Elixhauser/LOINC map via
  the wizard), EP-138 (`concept_map` fetch).
- Itemid/unit curation → EP-39; phenotype definitions → EP-41/42.
- UMLS/SNOMED acquisition (owner action; parked v2 PHE-3 / TXT-1).

## Verification / acceptance

- `uv run poe test -m ep_14` and `uv run --group dev mwh verify EP-14` green (docs test).
- Every URL fetched during the session; each "How to obtain" cell is a numbered owner-executable step list; the
  "Needed by EP" column names EP-39, EP-40, EP-41/42, EP-143 at least once each.
- `docs/resources/README.md` lists `vocabularies.md`.
- Commit `feat(mimicwarehouse): vocabularies inventory + ext/vocab convention (EP-14)`, then
  `docs(roadmap): record EP-14 commit hash`.

## Parked → final-roadmap.md

- OMOP Athena bundle download + `ref.concept` schema; SNOMED/UMLS concept sets; trigger: owner obtains a UTS
  account (v2 PHE-3, TXT-1).
- Full RxNorm/ATC drug-class normalisation of `prescriptions`/`emar` (beyond drug-name regex sets); trigger: an
  exposure workflow (EP-86/144) needs class-level exposure.
