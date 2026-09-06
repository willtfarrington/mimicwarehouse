# Phenotype engine (EP-41)

The one written answer to "how does a computable clinical trait get defined, versioned,
compiled and materialised here?" - the prose twin of `src/mimicwarehouse/phenotypes/`
(DESIGN §8, §15). A phenotype is a declarative YAML: a **grain** (subject, admission or
ICU stay), a **boolean criteria tree** over leaves that read diagnoses, procedures,
medications, labs, microbiology, mimic-code concepts and temporal relations, and an
**onset rule**. The engine compiles it to one deterministic SQL statement over the tier
catalog's relations, materialises the result in the derived layer, versions it like a
code set (EP-40) and pins every code set it references by hash (GOVERNANCE §12). Nothing
on this page is derived from patient data: the definition cards are rendered from the
packaged YAML (`python -m mimicwarehouse.phenotypes` re-renders the marked block;
`test_ep41` asserts it is in sync), and the only data-derived surfaces - the
`mimiciv_derived.phenotype_<id>` views and `meta.phenotype_versions` - are described,
never reproduced. All MIMIC-IV analyses in this repository are retrospective.

## 1. The schema

A phenotype is one YAML document - a packaged definition under
`src/mimicwarehouse/phenotypes/defs/` or a study file passed by path (`mwh phenotype …
--defs <dir>`; the code sets it references may come from a study directory too,
`--codesets <dir>`):

```yaml
id: t2dm                          # slug; with version it is the reference "t2dm@1.0.0"
version: "1.0.0"                  # semver, quoted
name: Type 2 diabetes mellitus
grain: subject                    # subject | hadm | icustay (timesem's grain registry)
criteria:                         # all / any (lists), not (one node), leaves
  any:
    - {id: dx, diagnosis: {codeset: t2dm@1.0.0, position: any, min_admissions: 1}}
    - all:
        - any:
            - {id: med, medication: {codeset: noninsulin_antidiabetics@1.0.0, source: prescriptions}}
            - {id: a1c, lab: {itemids: [50852], op: ">=", threshold: 6.5, unit: "%"}}
        - not: {id: t1dm, diagnosis: {codeset: t1dm@1.0.0}}
onset: earliest                   # earliest | latest | {first_of: [leaf ids]}
outputs: {flag: true, onset_time: true, evidence: true}
references: [noninsulin_antidiabetics@1.0.0, t1dm@1.0.0, t2dm@1.0.0]   # optional; must match the leaves
provenance: {source: hand, url: ..., ref: ..., accessed: "2026-09-06"}
citations: [https://doi.org/...]
what_it_does_not_claim: [...]
```

- **Leaves** are mappings with an `id` (a slug, unique in the document) and exactly one
  kind key. Every leaf yields *events* `(subject_id, hadm_id, stay_id, event_time)`:

  | leaf | reads | events | knobs |
  |---|---|---|---|
  | `diagnosis(codeset, position, min_admissions)` | `diagnoses_icd` × a dual ICD set | one per billed code; time = the admission's `dischtime` (diagnoses carry no timestamp) | `position: primary` keeps `seq_num = 1`; `min_admissions` (subject grain) needs the codes on that many admissions |
  | `procedure(codeset)` | `procedures_icd` × a dual ICD-PCS set | time = `chartdate` | - |
  | `medication(codeset, source, min_orders)` | `prescriptions.drug` (`starttime`), `emar.medication` (`charttime`) by the set's name rules, or `inputevents.itemid` (`starttime`) by its ICU itemids | an order / administration record, never adherence | `min_orders` |
  | `lab(codeset\|itemids, op, threshold, unit, min_count)` | `labevents` | the EP-39 harmonised value `op threshold` - the canonical unit for curated itemids (the leaf's conversions are inlined into the SQL, the same arithmetic as `mwh_harmonize`), `valuenum` as recorded otherwise; `unit` documents the threshold and must equal a curated itemid's canonical unit | `min_count` |
  | `microbiology(spec_itemids, org_itemids, positive_only)` | `microbiologyevents` | time = `charttime` when known, else `chartdate`; `positive_only` keeps rows with an organism | - |
  | `concept(table, column, op, value, key, time_column)` | a materialised `mimiciv_derived.*` concept (EP-37/38) | keys filled from `icustays` / `admissions` by `key`; time = `time_column` or the key table's anchor | EP-42 uses it for sepsis-3 / KDIGO |
  | `temporal(a, relation, b, hours)` | two inline leaves | the `a` events with a `b` event `before` / `after` / `within_hours` on the same unit; the operands count as evidence only through the temporal leaf | `hours` for `within_hours` |

- **Grain mapping.** `subject` keys events by `subject_id`; `hadm` by `hadm_id`, with a
  subject-level event that carries none (an outpatient lab, an emar row) attached to the
  admission whose `[admittime, dischtime]` contains it; `icustay` by `stay_id`, otherwise
  the ICU stay of the same admission / subject whose `[intime, outtime]` contains the
  event - timeless events (diagnoses, procedures) attach to every stay of their admission.
- **Onset.** `earliest` / `latest` take the min / max first-event time over the
  *positive* leaves (those not under a `not`) a unit satisfies; `first_of` names the
  leaves. A unit whose flag is false has no onset; a tree made only of negations has
  none either.
- **Outputs.** `<grain keys>, flag, onset_time, evidence_json` - the evidence is a compact
  JSON object `{"<leaf id>": n_events, …}` (keys sorted). Sessions read phenotype views as
  **subject-keyed, non-registry** reads under `safe_query` (EP-33 B1c, ledger P3C-5), so
  the 64-character free-text ceiling applies to any string a session selects: keep leaf
  ids short (the engine warns when a materialised value exceeds it) and select counts
  (`count(*) FILTER (WHERE flag)`), never the evidence itself.
- **`def_hash`** = sha256 of the canonical JSON of `{grain, criteria, onset, references:
  {id@version: code-set def_hash}}` - invariant to YAML key order and whitespace, and it
  moves exactly when the definition *or a referenced code set* moves. `name`,
  `description`, `provenance`, `citations`, `notes` and `what_it_does_not_claim` are
  documentation and stay out of the hash.

## 2. Versioning: the `(id, version)` pair is immutable

Every definition directory carries `phenotypes.lock.json` mapping each `id@version` to its
`def_hash` (`mwh phenotype lock` records new pairs through `fsio.atomic_write_text`; the
packaged lock is committed). Loading a YAML whose hash differs from the recorded one raises
`PhenotypeFrozenError`, and every command refuses with exit 3 (`EXIT_REFUSED`) before
anything runs - `mwh phenotype compile` included, so a threshold edited without a version
bump never reaches the lake. Because the hash pins the referenced code sets, a frozen code
set (EP-40's `CodeSetFrozenError`) refuses the phenotypes that read it the same way. The
fix is never to edit the released version: copy the file to the next version, change it,
lock it, and let the consumers reference the new pair. Both versions then coexist on
disk and in `meta.phenotype_versions`; the session view follows the latest.

## 3. Compile: the catalog surfaces

`mwh phenotype compile [id@version …] [--tier <t>] [--force]` runs the `phenotypes.compile`
DAG step (`dag/specs/phenotypes.yaml`, tag `phenotypes`) and the shared `catalog` step
through the runner - the only lake writer, with the build lock, benchmark lines and a
`run.start(kind="build")` record - and, inside it, **one `kind: phenotype` run per
phenotype** (EP-35: the SQL under `sql/phenotype.sql`, the phenotype and every code set as
refs with hashes, the core snapshot id, a benchmark line; `mwh runs list --kind
phenotype`). `--dry-run` prints the SQL and touches nothing; a version already built with
the same `def_hash` is skipped unless `--force`. `mwh build --tier <t> --tag phenotypes`
builds every registered phenotype.

- `lake/derived/<tier>/phenotypes/<id>@<version>/part-0.parquet` - one ZSTD file per
  built version (EP-37's per-tier derived layout), with a manifest line
  (`source_sha256` = the SQL's sha256) and a per-tier `status.json` entry
  `phenotypes.<id>@<version>`; EP-37's discovery walker registers it as the catalog view
  `phenotypes."<id>@<version>"` (a schema sessions cannot read directly).
- `mimiciv_derived.phenotype_<id>` - the **latest built version** of each id on the tier
  (semver order), the session-facing view; `mimiciv_derived.phenotype_<id>_hadm` - the
  per-admission companion of a subject-grain phenotype: every admission, flagged when the
  subject's onset lies at or before its `dischtime` (prevalent by that discharge), with
  the onset carried over. Join `mimiciv_derived.hadm_era` for the era axis.
- `meta.phenotype_versions` - one row per version attempted on the tier: `phenotype_id`,
  `version`, `def_hash`, `grain`, `refs` (JSON of `id@version -> def_hash`), `rows`,
  `n_positive` (NULL below k, `n_positive_suppressed`), `k`, `built_at`, `run_id`,
  `build_id`, `sql_sha256`, the derived `snapshot_id`, `status`, `error_class`.

The compiled statement is a pure function of the YAML and the code-set members (code sets
are inlined: exact codes as `IN` lists, prefixes as `LIKE`, drug names as `contains` /
`regexp_matches`, lab unit conversions as a `CASE` over the normalised unit), so
identical specs compile to identical text - `tests/ep/golden/` pins the packaged
definitions and `test_ep41` refuses a drift - and the statement runs on any connection
that sees the tier's relations (no macro needed).

## 4. Summary

`mwh phenotype summary <id@version> [--tier <t>] [--k n] [--json]` prints `n_units`,
`n_positive` and `share` for the phenotype's grain and by era (`hadm_era`; the `_hadm`
companion stands in for a subject-grain phenotype) - every number read through
`safe_query` (audited, k = 11 row-wise suppression on dev / full; only the synthetic
tiers may lower `--k`). `phenotypes.summary(ref, tier)` returns the same as a Polars
frame. Prevalence with denominators and confidence intervals is EP-68's.

## 5. Definitions

The packaged definitions (rendered from `defs/*.yaml`):

<!-- cards:begin -->
### `t2dm@1.0.0` — Type 2 diabetes mellitus (billed codes, non-insulin antidiabetics or HbA1c >= 6.5 %)

- **grain** `subject` · **def_hash** `f0511c05bf8b` · **locked** yes · `defs/t2dm.yaml`
- **criteria** `any(dx, all(any(med, a1c), not(t1dm)))` · **onset** `earliest`
- **references** `noninsulin_antidiabetics@1.0.0` (`bba2843889fe`), `t1dm@1.0.0` (`024460ebe6e4`), `t2dm@1.0.0` (`780eafccc3a8`)

| leaf | kind | definition | polarity |
|---|---|---|---|
| `dx` | diagnosis | diagnosis(t2dm@1.0.0, position any, min_admissions 1) | positive |
| `med` | medication | medication(noninsulin_antidiabetics@1.0.0, source prescriptions, min_orders 1) | positive |
| `a1c` | lab | lab(itemids [50852], >= 6.5 %, min_count 1) | positive |
| `t1dm` | diagnosis | diagnosis(t1dm@1.0.0, position any, min_admissions 1) | negated |

A subject has type 2 diabetes mellitus when any admission carries a T2DM diagnosis code, or when a non-insulin antidiabetic was prescribed or an HbA1c of 6.5 % or more was measured and no admission ever carried a type 1 code. The onset is the earliest qualifying event (a coded admission's discharge, a prescription start or a lab draw).

What it does not claim:

- Not a validated eMERGE / PheKB algorithm; no chart review or adjudication backs it.
- HbA1c alone does not distinguish type 1 from type 2 diabetes; the type 1 exclusion rests on billed codes only.
- Gestational, secondary and drug-induced diabetes (O24, E08, E09, E13; 648.0x, 249.x) are not excluded and a metformin order has other indications.
- A billed code or an order is not a confirmed diagnosis, and the onset is the earliest recorded evidence in MIMIC-IV, not the clinical onset.
<!-- cards:end -->

## 6. What this does not claim

A phenotype here is a **computable operationalisation** of billed codes, orders and
results - not a validated algorithm: no chart review backs the packaged definitions, a
billed code under-records, an order is not an administration, and the onset is the earliest
*recorded* evidence, never the clinical onset. Every definition states its own limits in
`what_it_does_not_claim` (above). Concept-backed phenotypes (sepsis-3, KDIGO AKI) arrive
with EP-42 through the `concept` leaf; the Phenotype Studio page (EP-63) and cohort
criteria referencing phenotypes (EP-46/47) build on the same registry.
