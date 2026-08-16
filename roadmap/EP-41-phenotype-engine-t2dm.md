# EP-41 — Phenotype engine + T2DM phenotype

**Size:** M · **Tier:** fixture+dev · **Core/Stretch:** core · **Depends on:** EP-40 (Code-set registry + ICD-9→10 GEM utility) · **Blocks:** EP-42 (Phenotypes: sepsis-3 + KDIGO AKI stage), EP-54 (Re-plan P3)

## Context

Capability 3 (computable clinical phenotypes, versioned) needs an engine that turns a declarative
YAML into deterministic SQL over the tier catalog, materializes the result in the derived layer,
and versions it like a code set. This brief builds `src/mimicwarehouse/phenotypes/` (DESIGN §8,
§15) and its first phenotype, type 2 diabetes mellitus — chosen because it exercises diagnoses
(dual ICD-9/10 sets from EP-40), medications (`prescriptions`/`emar` drug-name sets), labs
(HbA1c via `labevents`, unit-aware through EP-39 when available) and temporal logic (onset =
earliest qualifying event) without needing mimic-code concepts (EP-42 adds concept-backed
phenotypes). Grain and relative time come from `timesem` (EP-34); code sets by `id@version` come
from EP-40 and are resolved to hashes so a phenotype version pins its inputs. MIMIC caveats:
diagnoses carry no timestamps (assign `dischtime` of the admission), the ICD switch is per row
(`icd_version`), and a per-subject phenotype cannot use calendar time across patients. D-19/D-20
(SQL through the DAG runner), D-33 (small cells) apply; all counts a session sees are aggregates.

## In scope

1. **Phenotype schema** (`src/mimicwarehouse/phenotypes/spec.py`) — pydantic `Phenotype`:
   `id`, `version` (semver), `name`, `grain` (`subject|hadm|icustay`, from EP-34's registry),
   `criteria` (a boolean tree `all/any/not` over leaves), `onset` (`earliest|latest|first_of:
   [leaf ids]`), `outputs` (flag, onset_time, per-leaf evidence counts), `references` (code sets
   `id@version` → resolved `def_hash`), `provenance`, `notes`, `what_it_does_not_claim`. Leaves:
   `diagnosis(codeset, position: any|primary, min_admissions)`, `procedure(codeset)`,
   `medication(codeset, source: prescriptions|emar|inputevents, min_orders)`,
   `lab(codeset|itemids, op, threshold, unit, min_count)`, `microbiology(spec_itemids|org_itemids,
   positive_only)`, `concept(table, column, op, value)` (used by EP-42), `temporal(a, relation:
   before|after|within_hours, b)`. `def_hash` = sha256 of canonical JSON of grain + criteria +
   resolved reference hashes; `(id, version)` immutable (same rule and error type as EP-40).
2. **Compiler + materialization** (`compiler.py`, `runner.py`) — deterministic CTE chain, one
   CTE per leaf (`leaf_01_dx …`) each yielding `(grain keys, event_time, evidence)`, a boolean
   reduction CTE, and a final `SELECT grain keys, flag, onset_time, evidence_json`; materialize
   under `lake/derived/<tier>/phenotypes/<id>@<version>/` via the DAG runner sink (per-tier layout
   from EP-37) with a manifest, register `mimiciv_derived.phenotype_<id>` view (latest version) and
   `meta.phenotype_versions` (id, version, def_hash, grain, refs, rows, n_positive, built_at,
   run_id); every build is a `run.start(kind="phenotype")` run (EP-35) recording the SQL and
   reference hashes. `mwh phenotype list|show|validate|compile <id@version> [--tier] [--dry-run]`;
   dated DESIGN §15 note for the new CLI group.
3. **T2DM phenotype** (`src/mimicwarehouse/phenotypes/defs/t2dm.yaml`, `t2dm@1.0.0`, grain
   `subject`) — `any( diagnosis(t2dm@1.0.0, any position, min_admissions=1),
   all( any( medication(noninsulin_antidiabetics@1.0.0, prescriptions), lab(HbA1c ≥ 6.5 %,
   min_count=1) ), not(diagnosis(t1dm@1.0.0)) ) )`; onset = earliest qualifying event; also emits
   a per-`hadm` companion view (`phenotype_t2dm_hadm`: hadm flagged if onset ≤ dischtime). Record
   in `what_it_does_not_claim`: not a validated eMERGE algorithm; HbA1c alone does not distinguish
   type; gestational/secondary diabetes not excluded.
4. **Prevalence summary helper** — `phenotypes.summary(id@version, tier) -> polars.DataFrame`
   (n_units, n_positive, share, by era via `hadm_era` for hadm-grain views), returned through the
   catalog's aggregate path (k = 11 suppression as EP-30 already enforces for sessions); printed by
   `mwh phenotype summary`.
5. **Tests + docs** — `tests/ep/test_ep41.py` (`@pytest.mark.ep_41`; fixture, `dev`): schema
   round-trips YAML; hash invariance and frozen-version refusal; compiler emits identical SQL for
   identical specs (golden SQL file under `tests/ep/golden/t2dm@1.0.0.sql`); crafted synthetic
   subjects (ids ≥ 90 000 000, built in-test into a temp DuckDB using EP-9's schema) cover each
   branch (dx only; med + lab without dx; T1DM-only excluded; none); onset equals the earliest
   event; on dev, `mwh phenotype compile t2dm@1.0.0 --tier dev` builds and `summary` prints
   n_units/n_positive/share (aggregate). `docs/methods/phenotypes.md` (new): schema, leaf
   semantics, versioning, T2DM definition card.

## Out of scope

- Concept-backed phenotypes (sepsis-3, KDIGO AKI) and full-tier prevalence → EP-42.
- Phenotype Studio page → EP-63. Prevalence/incidence with CIs by denominator → EP-68.
- Additional phenotypes (HF, COPD, CKD stages, delirium, ARDS, VAP) → parked (`final-roadmap.md` § 3).
- Cohort criteria that reference phenotypes → EP-46/47.

## Verification / acceptance

- `uv run poe test -m ep_41` green on fixture and dev; `uv run --group dev mwh verify EP-41` green.
- `uv run --group dev mwh phenotype compile t2dm@1.0.0 --tier dev` writes the derived table and a
  `meta.phenotype_versions` row with `def_hash` and resolved code-set hashes; `mwh runs list --kind
  phenotype` shows the run.
- `uv run --group dev mwh phenotype summary t2dm@1.0.0 --tier dev` prints n_units, n_positive and
  share (no cell < 11); the number is recorded in the completion note.
- Golden SQL file committed; changing a threshold in the YAML without a version bump is refused.
