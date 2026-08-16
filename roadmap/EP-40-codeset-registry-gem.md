# EP-40 — Code-set registry + ICD-9→10 GEM utility

**Size:** M · **Tier:** fixture+dev · **Core/Stretch:** core · **Depends on:** EP-21 (Catalog builder (per-tier .duckdb)) · **Blocks:** EP-41 (Phenotype engine + T2DM phenotype), EP-46 (Cohort spec + registry), EP-54 (Re-plan P3)

## Context

Phenotypes (EP-41/42) and cohort specs (EP-46) reference diagnosis, procedure, itemid, drug and
ATC code sets **by id and version**, and every run records the definition hash (GOVERNANCE §12).
This brief builds that registry (`src/mimicwarehouse/codesets/`, DESIGN §8, §15): YAML code sets
with semver + content hash, compiled into `meta.codeset_members` per tier, validated against the
dictionary tables (`d_icd_diagnoses`, `d_icd_procedures`, `d_items`, `d_labitems` — dictionary
data, safe to inspect), plus a CMS GEM crosswalk utility. MIMIC caveat: ICD-9 → ICD-10 switched
around 2015, so every diagnosis/procedure code set is **dual** (`icd9` and `icd10` lists) and
compiled members carry `icd_version`; the GEM utility helps *author* the counterpart list but
never silently applies it — a human reviews the expansion. D-35: free vocabularies first (ICD
public; GEMs public from CMS); LOINC/RxNorm/SNOMED sets are only names here (no table download).
Sessions inspect code sets and dictionary matches freely; patient-level hits stay in the catalog.

## In scope

1. **Schema + registry** (`src/mimicwarehouse/codesets/spec.py`, `registry.py`) — pydantic
   `CodeSet`: `id` (slug), `version` (semver), `name`, `description`, `kind`
   (`icd_dx|icd_px|itemid|drug|atc|loinc|hcpcs`), `members` by system: `icd9: [{code, match:
   exact|prefix}]`, `icd10: [...]`, `itemids: [int]`, `drugs: {names: [...], match:
   contains|regex|exact, rxnorm: [...]}`, `atc: [class]`, `loinc: [...]`; `provenance` (source:
   `mimic-code|AHRQ-CCSR|Charlson|Elixhauser|hand`, url, accessed), `references`, `notes`.
   `def_hash` = sha256 of canonical JSON of `kind + members` (key-sorted, whitespace-free,
   codes normalized: upper-case, no dots). Registry rule: an `(id, version)` pair is immutable —
   loading a YAML whose hash differs from the recorded one raises `CodeSetFrozenError` (bump the
   version). Registry index `meta.codesets` (id, version, def_hash, kind, n_members, path).
   Built-in YAMLs live under `src/mimicwarehouse/codesets/defs/`; user/study YAMLs may be passed
   by path (`%MWH_DATA_ROOT%\studies\<study_id>\codesets\`).
2. **Compile + validate** — `mwh codeset compile [--tier dev] [id@version …]` expands
   prefix rules against the dictionary tables and writes `meta.codeset_members` (codeset_id,
   version, def_hash, system, code, match_kind, label, matched_in_dictionary bool);
   `mwh codeset validate <id@version>` prints coverage: n codes declared / matched / unmatched
   (dictionary-level, printable), unmatched codes listed; `mwh codeset list|show`. Add `mwh
   codeset` to the CLI and a dated note to DESIGN.md §15.
3. **Seed code sets** (`defs/*.yaml`, ≥ 12, each with a provenance line) — dual ICD-dx: `t2dm`,
   `t1dm`, `sepsis_explicit` (septicemia/severe sepsis/septic shock), `aki`, `ckd`,
   `heart_failure`, `mi`, `copd`, `hypertension`, `atrial_fibrillation`; `charlson_groups` (one
   YAML with the 17 Charlson categories transcribed from the vendored `charlson.sql`); itemid sets:
   `labs_creatinine`, `labs_lactate`, `labs_glucose` (from EP-39's YAML if present, else the
   concept SQL); drug sets: `vasopressors` (norepinephrine, epinephrine, phenylephrine,
   vasopressin, dopamine, dobutamine — names from mimic-code medication concepts), `insulin`,
   `antibiotics` (mimic-code `antibiotic.sql` name list), `noninsulin_antidiabetics` (metformin,
   sulfonylureas, DPP-4, SGLT2, GLP-1 agonists, thiazolidinediones); ATC: `atc_a10a_insulins`,
   `atc_c01ca_adrenergics`.
4. **GEM utility** (`src/mimicwarehouse/codesets/gem.py`) — `mwh codeset gem fetch` downloads
   the public CMS 2018 GEM zip into `%MWH_DATA_ROOT%\ext\vocab\gem\` (record URL, sha256, license
   in `source.yaml` per EP-14's landing convention; the owner may place the files manually
   instead), loads `meta.gem_i9_to_i10` / `meta.gem_i10_to_i9` (source, target, approximate,
   no_map, combination, scenario, choice_list); `gem.forward(codes)`, `gem.backward(codes)`;
   `mwh codeset expand <id@version> --via-gem` writes `<id>@<version>.gem-review.md` (proposed
   counterpart codes with flags + dictionary labels) for the owner to fold into a new version.
5. **Tests + docs** — `tests/ep/test_ep40.py` (`@pytest.mark.ep_40`; fixture, `dev`): hash
   is invariant to YAML key order/whitespace/code dots; frozen-version refusal; prefix expansion
   against the fixture dictionaries; every seed YAML validates and has ≥ 1 member per system it
   declares; GEM round-trip on a tiny committed GEM sample (`tests/fixtures/gem_sample.txt`,
   public data); on dev, `mwh codeset compile` populates `meta.codeset_members` and `validate`
   reports ≥ 90 % dictionary match for the ICD sets. `docs/methods/codesets.md` (new): schema,
   versioning rule, dual-era rule, GEM review workflow.

## Out of scope

- Phenotype logic that combines code sets with labs/meds/time → EP-41.
- Cohort criteria referencing code sets → EP-46. Phenotype Studio UI → EP-63.
- OMOP Athena / SNOMED / UMLS concept sets → parked (`final-roadmap.md` § 3).
- Reference-table ingestion via the Linkage Wizard (ATC/Elixhauser/LOINC maps) → EP-143.

## Verification / acceptance

- `uv run poe test -m ep_40` green on fixture and dev; `uv run --group dev mwh verify EP-40` green.
- `uv run --group dev mwh codeset list` shows ≥ 12 sets with versions and hashes; `mwh codeset
  validate t2dm@1.0.0 --tier dev` prints declared/matched/unmatched counts.
- `uv run --group dev mwh sql "SELECT codeset_id, system, count(*) FROM meta.codeset_members GROUP
  BY 1,2 ORDER BY 1,2"` works on dev; `meta.gem_i9_to_i10` exists (row count printed).
- Editing a member in a seed YAML without bumping `version` makes `mwh codeset compile` refuse
  with `CodeSetFrozenError` (demonstrated in a test).

## Parked → final-roadmap.md

- AHRQ CCSR full category YAML generation from the CCSR reference file (public) — trigger:
  P4/P5 subgroup or utilization analyses need broad dx groupings; hazard: file size/versioning.
