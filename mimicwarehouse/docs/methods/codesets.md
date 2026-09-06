# Code-set registry and the ICD-9 <-> ICD-10 GEM utility (EP-40)

The one written answer to "which codes mean *this* concept, in which version, and how do I
know a definition has not drifted?" - the prose twin of `src/mimicwarehouse/codesets/`
(DESIGN §8, §15). Phenotypes (EP-41/42) and cohort specs (EP-46) reference code sets **by
`id@version`** and record the definition hash of every reference (GOVERNANCE §12); this
page explains the schema, the versioning rule, the dual-era rule and the GEM review
workflow for readers and reviewers. Nothing on this page is derived from patient data: the
seed table is rendered from the packaged YAML (`python -m mimicwarehouse.codesets`
re-renders it; `test_ep40` asserts it is in sync), and the only data-derived surfaces -
`meta.codeset_members` on a tier and the review files - contain dictionary titles, never
rows. All MIMIC-IV analyses in this repository are retrospective.

## 1. The schema

A code set is one YAML document - a packaged seed under `src/mimicwarehouse/codesets/defs/`
or a study file passed by path (`mwh codeset … --defs <dir>`; the brief's
`%MWH_DATA_ROOT%\studies\<study_id>\codesets\`):

```yaml
id: aki                       # slug; with version it is the reference "aki@1.0.0"
version: "1.0.0"              # semver, quoted
name: Acute kidney injury (diagnosis codes)
kind: icd_dx                  # icd_dx | icd_px | itemid | drug | atc | loinc | hcpcs
members:
  icd9:  [{code: "584", match: prefix}]
  icd10: [{code: "N17", match: prefix}]
provenance: {source: hand, url: https://kdigo.org/..., accessed: "2026-09-06"}
references: [https://kdigo.org/guidelines/acute-kidney-injury/]
notes: >
  Traumatic and postprocedural kidney failure are left out on purpose.
```

- **`kind` decides the member systems.** `icd_dx` / `icd_px` sets carry `icd9` **and**
  `icd10` lists (§3); `itemid` sets list `itemids`; `drug` sets carry
  `drugs: {names, match: contains|regex|exact, rxnorm}` plus optionally the ICU
  `itemids` of the same agents; `atc` / `loinc` / `hcpcs` list their classes / codes.
  Anything else is refused at load.
- **ICD / HCPCS entries** are `{code, match: exact|prefix, group}`; a bare string is an
  exact code. `prefix` expands against the dictionary (every code starting with it);
  `group` labels the category of a grouped set (`charlson_groups` carries the 17 Quan
  categories) and every entry of a grouped set has one.
- **Normalisation.** Codes are upper-cased, stripped and lose their dots (MIMIC stores
  `E11.9` as `E119`); drug names lose surrounding / repeated whitespace and are upper-cased
  (`contains` / `exact` match case-insensitively; a `regex` stays verbatim); lists are
  de-duplicated and sorted. Codes must be **quoted** in YAML - an unquoted `0010` parses
  as an octal integer and loses its leading zero, so an integer code is refused.
- **`def_hash`** = sha256 of the canonical JSON (key-sorted, whitespace-free) of
  `{kind, members}` after normalisation: invariant to key order, whitespace, dots, list
  order and the `match: exact` default; it changes exactly when the definition changes.
  `name`, `description`, `provenance`, `references`, `notes` are documentation and stay out
  of the hash.
- **Provenance** is mandatory (`source` in `mimic-code | AHRQ-CCSR | Charlson | Elixhauser |
  hand`, a URL, the file or table within the source, the ISO date consulted); references
  are DOIs or stable URLs, never bare PMIDs (`docs/committed-text.md` rule 6).

## 2. Versioning: the `(id, version)` pair is immutable

Every definition directory carries a lock file, `codesets.lock.json`, mapping each
`id@version` to its `def_hash` (`mwh codeset lock` records new pairs, through
`fsio.atomic_write_text`; the packaged lock is committed). Loading a YAML whose hash differs
from the recorded one raises `CodeSetFrozenError` - `mwh codeset compile` / `list` /
`validate` refuse with exit 3 (`EXIT_REFUSED`) before anything runs. The fix is never to
edit the released version: copy the file to the next version (`t2dm@1.1.0`), change it,
lock it, and let the consumers reference the new pair. A pair not yet in the lock loads as
*unlocked* (visible in `mwh codeset list`; `mwh codeset lock --check` exits 1 while a
packaged seed is unlocked, which `test_ep40` asserts). Consumers pin what they used:
`meta.codesets.def_hash` per tier, and EP-41/46 resolve their references to hashes.

## 3. The dual-era rule

BIDMC switched ICD-9-CM -> ICD-10-CM/PCS coding around 2015 and MIMIC-IV admissions span
both eras; the switch is visible **per row** (`diagnoses_icd.icd_version`), never by date
(`timesem`, EP-34). So every diagnosis / procedure code set is **dual** - the schema
requires both lists - and every compiled member carries its `system` (`icd9` / `icd10`),
so a consumer joins on `(icd_code, icd_version)` and never mixes vocabularies. The GEM
utility (§6) helps *author* the counterpart list; it never applies it: the 2018 GEMs are
approximate, one-to-many and combination mappings, and a human folds accepted proposals
into a new version.

## 4. Compile: the catalog surfaces

`mwh codeset compile [--tier <t>] [id@version …]` runs the `codesets.compile` DAG step
(`dag/specs/codesets.yaml`, tag `codesets`) and the shared `catalog` step through the
runner - the only lake writer, with the build lock, benchmark lines and a `run.start`
record (EP-35). The step expands every registered set against the tier's staged
dictionary tables on the build connection and writes `lake/meta/<tier>/`, which EP-37's
discovery walker registers:

- `meta.codesets` - the index: `codeset_id`, `version`, `def_hash`, `kind`, `name`,
  `n_declared` (YAML entries), `n_members` (compiled rows), `n_matched`, `n_groups`,
  `locked`, `path`, `compiled_at`, `tier`, `build_id`.
- `meta.codeset_members` - one row per compiled member: `system` (`icd9`, `icd10`,
  `itemid`, `hcpcs` against a dictionary; `drug_name`, `rxnorm`, `atc`, `loinc` without
  one), `code`, `match_kind` (`exact` / `prefix` / `contains` / `regex`), `declared_code`
  (the YAML entry the row came from - the prefix for an expanded rule), `member_group`,
  `label` (the dictionary title), `matched_in_dictionary` (NULL where the system has no
  dictionary on this warehouse). A prefix nothing matches still yields one row (the prefix
  itself, unmatched), so an unmatched rule stays visible.
- `meta.gem_i9_to_i10` / `meta.gem_i10_to_i9` - the landed GEMs (§6), both kinds.

Dictionaries: `d_icd_diagnoses` (`icd_dx`), `d_icd_procedures` (`icd_px`), `d_items` +
`d_labitems` through EP-29's `meta.itemids` shape (`itemids`), `d_hcpcs` (`hcpcs`). Drug
names, RxNorm ids, ATC classes and LOINC codes have no table here (D-35: names first, no
table download; EP-143 lands reference tables) and carry `matched_in_dictionary = NULL`.
`mwh build --tier <t> --tag codesets` runs both steps and the catalog; a selection
(`mwh codeset compile t2dm@1.0.0`) re-compiles those sets and keeps the other sets' rows.

Sessions read the results through `mwh sql` - `meta.*` is a registry schema, so plain
dictionary reads pass the safe-query gate (EP-33 B1c) and counts need no suppression:

```
mwh sql "SELECT codeset_id, system, count(*) AS n FROM meta.codeset_members GROUP BY 1, 2 ORDER BY 1, 2" --tier dev
```

## 5. Validate

`mwh codeset validate <id@version> [--tier <t>] [--json]` expands one set against the
tier catalog's dictionaries **through `safe_query`** (audited; dims and `meta.itemids` are
registry exemptions, so the codes and titles are printable) and prints, per system, the
entries declared / matched / unmatched, the compiled row count and the unmatched codes.
`matched` is `n/a` for the systems without a dictionary. `mwh codeset list` / `show` never
touch the data root.

## 6. The GEM review workflow

A GEM is not a crosswalk: the flags say whether a mapping is approximate, whether a source
has no counterpart, and whether several target codes together (a combination scenario with
its choice lists) express one source; nothing here is applied automatically.

1. `mwh codeset gem fetch` downloads the two public CMS 2018 zips (diagnoses; procedures)
   into `<data_root>\ext\vocab\gem\2018\` (EP-14's landing convention; the owner may place
   the four text files there by hand), extracts exactly the four text members, verifies each
   against the sha256 pinned in `codesets.gem.GEM_ARCHIVES` and writes `source.yaml`
   (name, version, release date, URL, license, obtained on / by, archives and files with
   sha256 + bytes). `mwh codeset gem status` prints it. No `MWH_ALLOW_REMOTE` gate applies
   (that covers text modules only).
2. `mwh build --tier <t> --tag codesets` (or `mwh codeset compile`) materialises
   `meta.gem_i9_to_i10` / `meta.gem_i10_to_i9`: one row per GEM line with the flags
   decoded - `approximate`, `no_map`, `combination` (booleans) and the `scenario` /
   `choice_list` integers; a no-map line has `target` NULL.
3. `mwh codeset expand <id@version> --via-gem [--tier <t>] [--out PATH]` expands the set's
   declared members against the tier dictionary, maps every ICD-9 code forward and every
   ICD-10 code backward, drops what the set already covers (an exact member or a declared
   prefix) and writes `<id>@<version>.gem-review.md` under `studies\codesets\reviews\`:
   each proposed code with its dictionary title, the source codes that proposed it and the
   flags, plus the no-map and GEM-absent sources.
4. A human reads the review, folds accepted codes into a **new version** of the YAML,
   `mwh codeset lock`s it and re-compiles. The review is never applied automatically.

In code: `gem.forward(codes)` / `gem.backward(codes)` return the GEM entries per source
code (an unknown code maps to an empty tuple; `GemEntry.target` is None for a no-map
line). `tests/fixtures/gem_sample.txt` is a tiny public excerpt of the real files used by
the tests.

## 7. Seed sets

The packaged seeds (rendered from `defs/*.yaml`):

<!-- seeds:begin -->
| code set | kind | name | declared members | groups | provenance | def_hash |
|---|---|---|---|---|---|---|
| `aki@1.0.0` | `icd_dx` | Acute kidney injury (diagnosis codes) | icd9 1 (1 prefix), icd10 1 (1 prefix) | - | hand (`ICD-9-CM 584.- and ICD-10-CM N17.- (public code tables)`) | `ad96a0dbd8ad` |
| `antibiotics@1.0.0` | `drug` | Antibiotics (mimic-code antibiotic.sql name list) | names 152 (contains) | - | mimic-code (`mimic-iv/concepts_duckdb/medication/antibiotic.sql (the CASE WHEN LOWER(drug) LIKE list)`) | `0a34a3d3e808` |
| `atc_a10a_insulins@1.0.0` | `atc` | ATC A10A - insulins and analogues (class) | atc 1 | - | hand (`WHO Collaborating Centre for Drug Statistics Methodology, ATC/DDD index 2026`) | `c09806c851b5` |
| `atc_c01ca_adrenergics@1.0.0` | `atc` | ATC C01CA - adrenergic and dopaminergic agents (class) | atc 1 | - | hand (`WHO Collaborating Centre for Drug Statistics Methodology, ATC/DDD index 2026`) | `84cc3c30278c` |
| `atrial_fibrillation@1.0.0` | `icd_dx` | Atrial fibrillation (diagnosis codes) | icd9 1, icd10 8 | - | hand (`ICD-9-CM 427.31 and ICD-10-CM I48.0 / I48.1- / I48.2- / I48.91 (public code tables)`) | `bcf49b762e61` |
| `charlson_groups@1.0.0` | `icd_dx` | Charlson comorbidity categories (Quan 2005, 17 groups) | icd9 227 (227 prefix), icd10 289 (289 prefix) | 17 | Charlson (`concepts/patches/charlson.sql (EP-38 port of mimic-code PR 2142 over mimic-iv/concepts_duckdb/comorbidity/charlson.sql)`) | `bd5141bad989` |
| `ckd@1.0.0` | `icd_dx` | Chronic kidney disease (diagnosis codes, all stages) | icd9 1 (1 prefix), icd10 1 (1 prefix) | - | hand (`ICD-9-CM 585.- and ICD-10-CM N18.- (public code tables)`) | `13bc9ec37693` |
| `copd@1.0.0` | `icd_dx` | Chronic obstructive pulmonary disease (diagnosis codes) | icd9 3 (3 prefix), icd10 4 (4 prefix) | - | hand (`ICD-9-CM 491 / 492 / 496 and ICD-10-CM J41-J44 (public code tables)`) | `ce4f1b21313c` |
| `heart_failure@1.0.0` | `icd_dx` | Congestive heart failure (Quan 2005 Charlson definition) | icd9 17 (17 prefix), icd10 14 (14 prefix) | - | Charlson (`mimic-iv/concepts_duckdb/comorbidity/charlson.sql (congestive_heart_failure)`) | `f993dad36b9c` |
| `hypertension@1.0.0` | `icd_dx` | Hypertension (diagnosis codes, essential and secondary / complicated) | icd9 5 (5 prefix), icd10 6 (5 prefix) | - | hand (`ICD-9-CM 401-405 and ICD-10-CM I10-I16 (public code tables)`) | `e83dec8848de` |
| `insulin@1.0.0` | `drug` | Insulins (drug names) | names 21 (contains) | - | hand (`WHO ATC A10A "insulins and analogues" members, spelled as MIMIC prescriptions name them`) | `51289402448b` |
| `labs_creatinine@1.0.0` | `itemid` | Serum creatinine (lab itemids) | itemids 1 | - | mimic-code (`data/item_units.yaml (labs.creatinine) <- mimic-iv/concepts_duckdb/measurement/chemistry.sql`) | `56882cc01565` |
| `labs_glucose@1.0.0` | `itemid` | Glucose (lab and chart itemids) | itemids 5 | - | mimic-code (`data/item_units.yaml (labs.glucose) <- concepts_duckdb measurement/chemistry.sql, bg.sql, vitalsign.sql`) | `0a5d3e43e350` |
| `labs_lactate@1.0.0` | `itemid` | Blood lactate (lab itemids) | itemids 1 | - | mimic-code (`data/item_units.yaml (labs.lactate) <- mimic-iv/concepts_duckdb/measurement/bg.sql`) | `6c8c98e4e52b` |
| `mi@1.0.0` | `icd_dx` | Acute myocardial infarction (diagnosis codes) | icd9 1 (1 prefix), icd10 2 (2 prefix) | - | hand (`ICD-9-CM 410.- and ICD-10-CM I21.- / I22.- (public code tables)`) | `3c681d5b1e80` |
| `noninsulin_antidiabetics@1.0.0` | `drug` | Non-insulin antidiabetic drugs (drug names) | names 62 (contains) | - | hand (`WHO ATC A10B "blood glucose lowering drugs, excl. insulins" members`) | `bba2843889fe` |
| `sepsis_explicit@1.0.0` | `icd_dx` | Sepsis, explicitly coded (septicemia, sepsis, severe sepsis, septic shock) | icd9 9 (1 prefix), icd10 13 (5 prefix) | - | hand (`Angus et al. 2001 explicit septicemia codes, extended to the ICD-10-CM era by hand`) | `5d172007c621` |
| `t1dm@1.0.0` | `icd_dx` | Type 1 diabetes mellitus (diagnosis codes) | icd9 20, icd10 1 (1 prefix) | - | hand (`ICD-9-CM 250.x1 / 250.x3 and ICD-10-CM E10.- (public code tables)`) | `024460ebe6e4` |
| `t2dm@1.0.0` | `icd_dx` | Type 2 diabetes mellitus (diagnosis codes) | icd9 20, icd10 1 (1 prefix) | - | hand (`ICD-9-CM 250.x0 / 250.x2 and ICD-10-CM E11.- (public code tables)`) | `780eafccc3a8` |
| `vasopressors@1.0.0` | `drug` | Vasopressors (drug names + ICU infusion itemids) | itemids 6, names 10 (contains) | - | mimic-code (`mimic-iv/concepts_duckdb/medication/{norepinephrine,epinephrine,phenylephrine,vasopressin,dopamine,dobutamine}.sql`) | `8d34953c5039` |
<!-- seeds:end -->

The ICD sets are dual (§3); `charlson_groups` carries the 17 Quan categories as
`member_group`, transcribed from the vendored `charlson.sql` as executed here (the EP-38
C4A exclusion applied); the lab sets take their itemids from EP-39's item catalogue; the
drug sets are name lists (`antibiotics` transcribes mimic-code's `antibiotic.sql` list); the
two ATC sets are class prefixes awaiting a landed table (EP-143).

## 8. What this does not claim

A code set names codes; it is not a validated phenotype. Billing codes under-record
conditions, the ICD-10 lists in Quan's Charlson are WHO ICD-10 codes (the ICD-10-CM
extensions C7A / C7B stay unmapped, and 37 of its 289 ICD-10 rules — E12.x, E14.x, F00,
B21–B24, C97, I64, J46, ... — have no ICD-10-CM counterpart, so that arm validates at
about 87 % on the real dictionary while the hand-typed sets reach 100 %), and a
drug-name match is not an administration. The
EP-41/42 phenotypes combine sets with medications, labs and temporal logic and state their
own limits. Consumers: EP-41 (`diagnosis(codeset)` / `medication(codeset)` leaves), EP-46
(cohort criteria by `id@version`), EP-63 (Phenotype Studio).
