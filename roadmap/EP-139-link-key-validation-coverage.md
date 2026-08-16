# EP-139 — Key validation, join cardinality, linkage coverage

**Size:** M · **Tier:** fixture · **Core/Stretch:** core · **Depends on:** EP-137 (Importer profiler + provenance/licensing register) · **Blocks:** EP-141 (Linkage Wizard B (validate → coverage → commit)), EP-146 (Capstone #7)

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-136 (Re-plan P8) before execution.

## Context

Third and fourth wizard steps (D-36: validate → coverage). Before an external source is committed
into the lake, its declared keys must be checked, its join cardinalities against the core catalog
classified, and its linkage coverage measured — overall and by `anchor_year_group`, the only
cross-patient temporal axis. Coverage-by-era is the diagnostic that makes partial linkage visible:
MIMIC-IV-ED 2.2 covers 2011–2019 by design, so ED coverage should be ~0 for the 2008–2010 and
2020–2022 eras and partial in between; a source whose events fall outside its linked admission
windows reveals a shift or key problem. Category 35. Uses the mapped view from EP-138 when a
mapping exists (else raw typed columns), `mimicwarehouse.run` for the run record (EP-35) and
`mimicwarehouse.disclose.suppress` for every count (EP-43, D-33). All checks run inside DuckDB;
results are aggregates only (D-31).

## Scope sketch (refine at re-plan)

1. **`src/mimicwarehouse/linkage/validation.py`** — `validate_keys(source, mapping, tier)`:
   declared PK uniqueness and null rate per table; FK resolution of `subject_id` → `patients`,
   `hadm_id` → `admissions`, ICU `stay_id` → `icustays` (grain-aware: an `edstay` key is *not*
   checked against `icustays`); orphan counts and rates; pass/warn/fail against thresholds from the
   mapping's `validation:` block (defaults: PK unique = fail, FK orphan rate > 0.1 % = warn, > 5 % = fail).
2. **Join cardinality** — `join_cardinality(source_table, key, core_table)`: classify 1:1 / 1:n /
   n:1 / n:m with max fan-out and the share of keys above 1; e.g. `edstays.hadm_id → admissions`
   expected n:1 with mostly ≤ 1 ED stay per admission.
3. **Linkage coverage** — `coverage(source, mapping, tier)`: source-side (share of source rows and
   subjects whose keys resolve), core-side (share of `patients` / `admissions` with ≥ 1 linked row,
   overall and per `anchor_year_group`), temporal consistency (share of linked source events with a
   time inside `[admittime − 24 h, dischtime + 24 h]`; for ED: `edstays.outtime` within 24 h of
   `admittime` for admitted stays), all suppressed at k = 11 with complementary suppression;
   tier-aware — on dev the mapped view is filtered to `subject_bucket IN (0..4)` so source-side and
   core-side shares compare like for like.
4. **Report artifact** — `ValidationReport` (pydantic) → `ext/<source_id>/validation.json` and
   `linkage_report.md` (tables + a coverage-by-era spec built with `viz/`; rendered through the
   EP-130 report engine once it exists, plain Markdown until then), labelled *exploratory*
   (data-quality report; retrospective statement in the footer), written inside a `run` context so it
   carries a run id and snapshot ids;
   `mwh link validate <source_id> --tier <tier>` and `mwh link coverage <source_id> --tier <tier>`.
5. **Tests** `tests/ep/test_ep139.py` (`@pytest.mark.ep_139`, fixture): the ED-like fixture against
   the fixture catalog passes keys and shows n:1 `hadm_id` cardinality; a crafted duplicate
   `stay_id` fails PK; a crafted orphan `subject_id` batch trips the warn/fail thresholds; era
   coverage rows below 11 are suppressed in the markdown; the reference-table fixture takes the
   non-subject branch (coverage = share of `d_labitems`/`d_icd_diagnoses` entries covered).

## Out of scope

- Committing anything to the lake or catalog → EP-141 (Linkage Wizard B).
- Wizard UI rendering of these tables → EP-141; real ED / reference numbers → EP-142 / EP-143.

## Verification / acceptance (sketch)

- `uv run poe test -m ep_139` and `uv run --group dev mwh verify EP-139` green on fixture.
- `uv run --group dev mwh link validate edlike --tier fixture` writes `validation.json` and
  `linkage_report.md`; `uv run --group dev mwh disclose check <linkage_report.md>` passes; the report
  carries a run id visible via `mwh runs`.
- Crafted violations (duplicate PK, orphan FK) are *refused* (fail status, non-zero exit) in tests.
- No full-tier run here; EP-142 records the first real coverage run.

## Parked → final-roadmap.md

- Probabilistic record linkage (Splink / Fellegi–Sunter) for sources without shared keys — trigger:
  a source keyed by something other than PhysioNet subject ids.
