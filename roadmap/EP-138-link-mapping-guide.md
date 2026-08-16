# EP-138 — Concept/unit mapping guide + mapping YAML

**Size:** M · **Tier:** fixture · **Core/Stretch:** core · **Depends on:** EP-137 (Importer profiler + provenance/licensing register), EP-39 (Itemid dictionary curation + unit harmonization) · **Blocks:** EP-140 (Linkage Wizard A (profile → map)), EP-146 (Capstone #7)

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-136 (Re-plan P8) before execution.

## Context

Second wizard step (D-36, "map"): an external table's columns must be mapped onto the warehouse's
schema contract (EP-9), its identifiers onto our concept vocabularies (itemid, LOINC, RxNorm/GSN,
ICD-9/10 with the ~2015 switch), and its measurements onto harmonized units (`units.py`, EP-39). This
brief writes the guide a human follows and the machine-readable mapping YAML the wizard and the
DAG runner consume. Category 35 (additional-data ingestion & linkage). mimic-code's
`concept_map/*.csv` (MIT, pinned commit from EP-8) seeds itemid → LOINC/RxNorm suggestions
(DESIGN §19). ED 2.2 is the design target: `edstays.stay_id` is an *ED* stay id (same 30 M band as
ICU `stay_id`, different entity → grain `edstay`, DESIGN §7), `triage.chiefcomplaint` is free text,
`vitalsign.pain` mixes numbers and strings, `pyxis.gsn` is a drug code, temperatures are °F.

## Scope sketch (refine at re-plan)

1. **`docs/resources/linkage-mapping-guide.md`** — the mapping guide: how to go from a
   `profile.json` to a `mapping.yaml`; rules for identifiers (which band, which grain, whether the
   column joins to `patients`/`admissions`/`icustays`), free-text columns (`load_flag: free_text` →
   loaded but refused by `safe_query`, or `drop`), timestamps (naive, per-patient shift preserved →
   only within-patient intervals; `anchor_year_group` for eras), concept mapping (itemid/LOINC/
   GSN/NDC/ICD with `icd_version`), unit conversion (via `units.py` tables), and licence notes.
2. **`src/mimicwarehouse/linkage/mapping.py`** — pydantic `MappingSpec`: per source table → target
   `schema.table` (`mimiciv_ed.*` or `ref.*`), grain, partition key (`subject_id` → bucketed, or
   unpartitioned reference), per column → target name, cast, `role` (`identifier` / `time` /
   `code` / `measure` / `category` / `free_text` / `drop`), unit source→target, concept map ref
   (`codesets/` or `concept_map/*.csv`), and a `validation:` block of thresholds read by EP-139.
   `mapping.yaml` gets a semver + content hash (recorded in `source.yaml`).
3. **Suggestion engine** — `suggest_mapping(profile, contract) -> MappingSpec` using exact/normalized
   name matches against the EP-9 contract (the `ed` contract exists since EP-9/EP-22), type
   compatibility, id-band flags from the profile, and the concept-map CSVs; every suggestion carries a
   `confidence` and `rationale`; unresolved columns are listed, never silently dropped.
4. **Validation of the mapping itself** — `check_mapping(spec, contract)`: target columns exist,
   casts are legal, identifier roles cover every id-band column, free-text columns are flagged,
   unit conversions are known to `units.py`; `mwh link map --suggest|--check` CLI verbs.
5. **Mapped view** — `mapped_view_sql(spec, source_path)`: deterministic DuckDB SQL that reads the raw
   files with the loader's typed reader and applies casts/renames/unit conversions; consumed by
   EP-139 (validation) and by the DAG step EP-141 emits.
6. **Tests** `tests/ep/test_ep138.py` (`@pytest.mark.ep_138`, fixture): suggestions for the ED-like
   fixture map `edstays.stay_id` → grain `edstay`, flag `chiefcomplaint` free text, convert
   triage temperature °F → °C; a mapping with an unmapped id column fails `check_mapping`;
   YAML round-trip and hash stability.

## Out of scope

- Key/cardinality/coverage checks → EP-139; wizard UI → EP-140; real ED / reference-table
  mappings → EP-142 / EP-143.
- New vocabularies (SNOMED/UMLS/OMOP Athena) → final-roadmap (D-35).

## Verification / acceptance (sketch)

- `uv run poe test -m ep_138` and `uv run --group dev mwh verify EP-138` green on fixture.
- `uv run --group dev mwh link map --suggest tests/fixtures/ext/edlike --source-id edlike` writes
  `ext/edlike/mapping.yaml`; `--check` passes on it and refuses a crafted mapping that maps a
  free-text column as `category`.
- Guide exists at `docs/resources/linkage-mapping-guide.md` and links resolve; no full-tier run here.

## Parked → final-roadmap.md

- Learned column-matching (embeddings over column names/values) — trigger: > 3 sources with
  non-MIMIC naming.
