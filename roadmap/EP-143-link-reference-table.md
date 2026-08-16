# EP-143 — Reference-table ingestion via wizard (ATC / Elixhauser / LOINC map)

**Size:** M · **Tier:** fixture+dev+full · **Core/Stretch:** core · **Depends on:** EP-141 (Linkage Wizard B (validate → coverage → commit)), EP-14 (Ontologies & vocabularies inventory) · **Blocks:** EP-146 (Capstone #7)

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-136 (Re-plan P8) before execution.

## Context

The second wizard branch (D-36: "reference/knowledge tables"): a source that is *not* subject-keyed
and links to the catalog through a dimension key (itemid, ICD code + version, drug code). It proves
the unpartitioned `ref.*` path of EP-141 and adds a genuinely useful table. Licensing decides the
candidate (D-35, GOVERNANCE §10; verdicts recorded by EP-14 in `docs/resources/vocabularies.md`):
the **primary** is mimic-code's `concept_map/d_labitems_to_loinc.csv` (MIT, pinned commit from
EP-8 — no download, redistributable); the **second**, if time allows, is the AHRQ Elixhauser
Comorbidity Software Refined ICD-10-CM table (public, owner downloads to
`%MWH_DATA_ROOT%\ext\ahrq_elixhauser\`); **ATC** only if EP-14 recorded a license-clean,
downloadable source (the WHO index is not) — otherwise it stays parked. Category 35. Note the
ICD-9 → ICD-10 switch (~2015): an ICD-10-only comorbidity table covers only the later eras, which
the coverage step must show rather than hide.

## Scope sketch (refine at re-plan)

1. **LOINC map via wizard** — register entry (`ext/mimic_code_concept_map/source.yaml`, MIT, mimic-code
   commit hash + `NOTICE` attribution) → profile → map (`ref.d_labitems_to_loinc`, key `itemid`,
   `loinc_code`, label/units columns, no partition) → validate (`itemid` → `d_labitems` FK, expected
   1:1, flag n:1) → coverage (share of `d_labitems` mapped; share of dev/full `labevents` rows whose
   itemid has a LOINC code, taken from the EP-55 itemid rollups rather than a `labevents` scan,
   suppressed) → commit dev in the foreground, then full as the logged background job
   `ext_mimic_code_concept_map_full` (tiny, but the full-tier rule is uniform).
2. **Second table (Elixhauser ICD-10)** — same five steps into `ref.elixhauser_icd10`
   (key `icd_code` + `icd_version = 10`); coverage = share of `diagnoses_icd` rows per era hitting any
   category, which is expected to be ~0 before the switch — recorded as the era-aware caveat; the
   EP-40 code-set registry gains a `ref`-backed loader so a code set may be defined as "all codes in
   `ref.elixhauser_icd10` where category = X" (versioned by the source snapshot id).
3. **Reference-branch polish in the wizard** — cardinality/coverage tables phrased for dimension
   keys (no era chart when the source has no time or subject key; era chart of *catalog usage*
   instead), register `redistributable: true|false` drives whether the table may enter git
   (LOINC map yes; the LOINC table itself never — registration-only license).
4. **Tests** `tests/ep/test_ep143.py` (`@pytest.mark.ep_143`; fixture + dev, full opt-in): the
   reference fixture and the real LOINC map both commit unpartitioned into `ref.*`; FK to
   `d_labitems` resolves; a crafted map with an itemid absent from `d_labitems` warns; the code-set
   loader compiles a `ref`-backed set with a stable hash.

## Out of scope

- SNOMED / RxNorm / OMOP Athena vocabularies (UTS license) → final-roadmap (D-35).
- Using the tables in analyses (Elixhauser-adjusted models) → P5–P7 workflows / capstones.
- ATC unless EP-14 found a compatible source → parked.

## Verification / acceptance (sketch)

- `uv run poe test -m ep_143` green on fixture + dev; `uv run --group dev mwh verify EP-143` green.
- `mwh link status mimic_code_concept_map` = `committed` on dev and full; run ids and timings in
  the completion note; `mwh sql "SELECT count(*) FROM ref.d_labitems_to_loinc" --tier full` works.
- Coverage report (`ext/mimic_code_concept_map/linkage_report.md`) passes `mwh disclose check`;
  the register markdown (`docs/resources/external-sources.md`) lists both sources with licenses.

## Parked → final-roadmap.md

- WHO ATC/DDD index ingestion — trigger: a license-compatible ATC table (or UTS/RxNorm→ATC path).
- CCSR (diagnosis/procedure) tables and CMS GEMs as `ref.*` — trigger: phenotype briefs need them
  beyond EP-40's YAML sets.
