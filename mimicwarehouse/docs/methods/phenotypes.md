# Phenotype engine (EP-41, EP-42)

The one written answer to "how does a computable clinical trait get defined, versioned,
compiled and materialised here?" - the prose twin of `src/mimicwarehouse/phenotypes/`
(DESIGN §8, §15). A phenotype is a declarative YAML: a **grain** (subject, admission or
ICU stay), a **boolean criteria tree** over leaves that read diagnoses, procedures,
medications, labs, microbiology, mimic-code concepts and temporal relations, an
**onset rule** and, since EP-42, named **parameters** the leaves reference. The engine
compiles it to one deterministic SQL statement over the tier catalog's relations,
materialises the result in the derived layer, versions it like a code set (EP-40) and
pins every code set **and every vendored concept** it references by hash (GOVERNANCE
§12). Nothing on this page is derived from patient data: the definition cards are
rendered from the packaged YAML (`python -m mimicwarehouse.phenotypes` re-renders the
marked block; `test_ep41` / `test_ep42` assert it is in sync), and the only data-derived
surfaces - the `mimiciv_derived.phenotype_<id>` views, `meta.phenotype_versions` and the
prevalence artefact under `runs/` - are described, never reproduced. All MIMIC-IV
analyses in this repository are retrospective.

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
  | `concept(table, column, op, value, key, time_column, window, evidence)` | a materialised `mimiciv_derived.*` concept (EP-37/38) | keys filled from `icustays` / `admissions` by `key`; time = `time_column` or the key table's anchor | `window: {from_hours, to_hours}` keeps events with `from_hours <= hours since the anchor < to_hours` (`intime` for `stay_id`, `admittime` for `hadm_id`; `timesem.sql_hours_since`); `evidence: [{name, column, agg, default, levels}]` materialises typed per-unit columns (EP-42: sepsis-3 / KDIGO) |
  | `temporal(a, relation, b, hours)` | two inline leaves | the `a` events with a `b` event `before` / `after` / `within_hours` on the same unit; the operands count as evidence only through the temporal leaf | `hours` for `within_hours` |

- **Parameters (EP-42).** A top-level `parameters: {name: scalar}` mapping declares named
  defaults; inside `criteria` any scalar field may be written as the placeholder `$name`
  (`value: $min_stage`, `to_hours: $window_hours`) and is resolved before validation, so
  the leaves always hold concrete values and the hash moves when a parameter moves -
  editing `window_hours` in a locked file is refused like any other edit. A study that
  wants another value copies the file as the next version; tests and in-memory variants
  pass `parameters=` overrides to `phenotype_from_text` / `load_phenotype` (a variant is a
  different definition and is never materialised under the locked pair).
- **Concept evidence (EP-42).** A concept leaf's `evidence` entries become typed output
  columns after `evidence_json`: `agg: first` / `last` take the concept column at the
  unit's earliest / latest qualifying event (`arg_min` / `arg_max` by event time),
  `min` / `max` aggregate over the qualifying events; `default` fills units the leaf does
  not satisfy (else NULL); `levels` declares an ordinal's values (the KDIGO stages
  `[0, 1, 2, 3]`) so `mwh phenotype summary` reports its distribution. Names are slugs
  outside the reserved set (the grain keys, `flag`, `onset_time`, `evidence_json`,
  anything ending in `_id`), unique across the phenotype, and never on a temporal
  operand. Because the aggregates run over the *qualifying* events, `max_stage_in_window`
  of `kdigo_aki` reports the maximum qualifying stage (with `min_stage` 1 that is the
  maximum stage in the window; 0 means no qualifying row, data or not).
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
  {id@version: code-set def_hash}}` - plus `parameters` when declared and `concepts:
  {mimiciv_derived.<name>: executed-SQL sha256}` for every vendored concept a leaf reads
  (EP-42) - invariant to YAML key order and whitespace, and it moves exactly when the
  definition, a referenced code set *or a referenced concept's SQL* moves. `name`,
  `description`, `provenance`, `citations`, `notes` and `what_it_does_not_claim` are
  documentation and stay out of the hash. A definition without parameters or concept
  leaves hashes exactly as it did at EP-41 (`t2dm@1.0.0`'s lock entry is unchanged).
- **Concept pins (EP-42).** The registry resolves `mimiciv_derived.<name>` against the
  committed concept inventory and patch registry (`concepts/concepts.yaml`,
  `concepts/patches/patches.yaml` - definition text, no data): the pin is the sha256 of
  the SQL the concept runner executes, the EP-38 patch's when the concept is patched,
  else the vendored file's, together with the DAG step that builds it. A table outside
  the inventory (a study's own derived table) is read unpinned and `validate` warns.

## 2. Versioning: the `(id, version)` pair is immutable

Every definition directory carries `phenotypes.lock.json` mapping each `id@version` to its
`def_hash` (`mwh phenotype lock` records new pairs through `fsio.atomic_write_text`; the
packaged lock is committed). Loading a YAML whose hash differs from the recorded one raises
`PhenotypeFrozenError`, and every command refuses with exit 3 (`EXIT_REFUSED`) before
anything runs - `mwh phenotype compile` included, so a threshold (or a parameter such as
`window_hours`) edited without a version bump never reaches the lake. Because the hash
pins the referenced code sets and concepts, a frozen code set (EP-40's
`CodeSetFrozenError`) refuses the phenotypes that read it the same way, and a re-vendor or
a new concept patch that changes the executed SQL refuses every locked phenotype that
reads that concept until its version is bumped against the new concept. The fix is never
to edit the released version: copy the file to the next version, change it, lock it, and
let the consumers reference the new pair. Both versions then coexist on disk and in
`meta.phenotype_versions`; the session view follows the latest. `test_ep42` demonstrates
all three refusals (the parameter edit, the moved concept hash) and the coexistence of
`kdigo_aki@1.0.0` / `@1.1.0`.

## 3. Compile: the catalog surfaces

`mwh phenotype compile [id@version …] [--tier <t>] [--force]` runs the `phenotypes.compile`
DAG step (`dag/specs/phenotypes.yaml`, tag `phenotypes`) and the shared `catalog` step
through the runner - the only lake writer, with the build lock, benchmark lines and a
`run.start(kind="build")` record - and, inside it, **one `kind: phenotype` run per
phenotype** (EP-35: the SQL under `sql/phenotype.sql`, the phenotype, every code set and
every pinned concept as refs with hashes, the core snapshot id, a benchmark line; `mwh runs
list --kind phenotype`). `--dry-run` prints the SQL and touches nothing; a version already
built with the same `def_hash` is skipped unless `--force`; `--background --job NAME`
detaches the compile through the EP-19 launcher (the full-tier standard: `mwh jobs --job
NAME --tail N` reads the log). `mwh build --tier <t> --tag phenotypes` builds every
registered phenotype. The step attempts every selected phenotype and records each failure
(`status: failed` + the error class) before raising, so one broken definition never hides
the others' state.

- **Concept leaves need their concept first (EP-42).** The step exposes every
  `mimiciv_derived.*` table a leaf reads only when the concept is complete for the tier
  (`ConceptNotBuiltError` names `mwh build --tier <t> --tag concepts` otherwise) **and**
  was built from the SQL the phenotype pins - the status entry's executed-SQL sha256 must
  equal the pin's, patch included (`ConceptPinMismatchError` names the concept rebuild or
  the version bump). `dag/specs/phenotypes.yaml` lists the pinned concept steps
  (`concept.sepsis.sepsis3`, `concept.organfailure.kdigo_stages`;
  `phenotypes.runner.concept_steps` renders the list) as dependencies, so a full build
  orders them first; a tag or compile run does not pull them in.
- `lake/derived/<tier>/phenotypes/<id>@<version>/part-0.parquet` - one ZSTD file per
  built version (EP-37's per-tier derived layout), with a manifest line
  (`source_sha256` = the SQL's sha256) and a per-tier `status.json` entry
  `phenotypes.<id>@<version>`; EP-37's discovery walker registers it as the catalog view
  `phenotypes."<id>@<version>"` (a schema sessions cannot read directly).
- `mimiciv_derived.phenotype_<id>` - the **latest built version** of each id on the tier
  (semver order), the session-facing view, its typed evidence columns after
  `evidence_json`; `mimiciv_derived.phenotype_<id>_hadm` - the per-admission companion:
  for a subject-grain phenotype every admission, flagged when the subject's onset lies at
  or before its `dischtime` (prevalent by that discharge), with the onset carried over;
  for an icustay-grain phenotype (EP-42) the admissions with at least one ICU stay,
  flagged when any of their stays is, with the earliest flagged onset and `n_stays`. Join
  `mimiciv_derived.hadm_era` for the era axis; join a hadm-grain phenotype on the
  admission for an agreement cross-tab.
- `meta.phenotype_versions` - one row per version attempted on the tier: `phenotype_id`,
  `version`, `def_hash`, `grain`, `refs` (JSON of `id@version -> def_hash`), `rows`,
  `n_positive` (NULL below k, `n_positive_suppressed`), `k`, `built_at`, `run_id`,
  `build_id`, `sql_sha256`, the derived `snapshot_id`, `status`, `error_class`, `tier`,
  and since EP-42 `concept_refs` (JSON of `mimiciv_derived.<name> -> executed-SQL
  sha256`).

The compiled statement is a pure function of the YAML and the code-set members (code sets
are inlined: exact codes as `IN` lists, prefixes as `LIKE`, drug names as `contains` /
`regexp_matches`, lab unit conversions as a `CASE` over the normalised unit), so
identical specs compile to identical text - `tests/ep/golden/` pins the packaged
definitions and `test_ep41` refuses a drift - and the statement runs on any connection
that sees the tier's relations (no macro needed).

## 4. Summary and the prevalence artefact

`mwh phenotype summary <id@version> [<id@version> …] [--tier <t>] [--k n] [--json]
[--no-agreement] [--report | --out DIR]` prints, for each phenotype, `n_units`,
`n_positive` and `share` for its grain and by era (`hadm_era`; the `_hadm` companion
stands in for a subject-grain phenotype); the **distribution** of every evidence column
that declares `levels` (`max_stage_in_window` of `kdigo_aki`: stays per KDIGO stage 0-3,
where an absent level is zero units or a suppressed cell); and the pairwise
**agreement** 2x2 per admission (each phenotype's admission-level relation joined on
the admission - an icustay-grain phenotype restricts the denominator to admissions with
at least one ICU stay; the sepsis-3 vs explicit-codes cross-tab is `sepsis3@1.0.0` x
`sepsis_explicit@1.0.0`). Every number is read through `safe_query` (audited, k = 11
row-wise suppression on dev / full: a 2x2 with any cell in 1..k-1 is suppressed whole;
only the synthetic tiers may lower `--k`). `phenotypes.summary(ref, tier)` returns the
prevalence frame; `summarize` / `distribution` / `agreement` / `prevalence_report` are
the library twins (each takes an open `run` to record its statements and audit ids).

`--report` (or `--out DIR`) records the reads in a `kind: analysis` run
(`claim_type: exploratory`) and writes **`phenotype_prevalence.md`** - the prevalence
tables, the distributions, the 2x2 tables, every definition's "what it does not claim"
and a reproduction block - into the run folder `runs/<run_id>/` (or `DIR`). The file
carries the claim-type label, the retrospective statement and a "disclosure sidecar
pending EP-43" line: it stays under the data root until EP-43's `mwh disclose check`
verifies it retroactively (EP-42 amendment, D-43 item 14) and EP-53 promotes it.
Prevalence with denominators and confidence intervals is EP-68's, over the same views.

## 5. Definitions

The packaged definitions (rendered from `defs/*.yaml`):

<!-- cards:begin -->
### `kdigo_aki@1.0.0` — KDIGO acute kidney injury within the first 7 ICU days (smoothed stage >= 1)

- **grain** `icustay` · **def_hash** `8ee86ad30045` · **locked** yes · `defs/kdigo_aki.yaml`
- **criteria** `aki` · **onset** `earliest`
- **references** (none)
- **parameters** `min_stage` = `1`, `window_hours` = `168`
- **concepts pinned** `mimiciv_derived.kdigo_stages` (`c441af84a639`, unpatched)
- **evidence columns** `max_stage_in_window` = max(`aki_stage_smoothed`), default `0`, levels `[0, 1, 2, 3]`, `stage_at_onset` = first(`aki_stage_smoothed`)

| leaf | kind | definition | polarity |
|---|---|---|---|
| `aki` | concept | concept(mimiciv_derived.kdigo_stages.aki_stage_smoothed >= 1, key stay_id, time charttime, window [0, 168) h from the anchor, evidence max_stage_in_window = max(aki_stage_smoothed), stage_at_onset = first(aki_stage_smoothed)) | positive |

An ICU stay has acute kidney injury when the mimic-code `kdigo_stages` concept records a smoothed KDIGO stage of at least `min_stage` at a chart time within `window_hours` of the ICU admission (`[intime, intime + window_hours)`; 168 h = 7 days, the EP-112 window). The onset is the first qualifying chart time; `max_stage_in_window` (0-3, 0 when no qualifying row exists) and `stage_at_onset` are carried as typed evidence columns.

What it does not claim:

- Not an adjudicated AKI diagnosis - KDIGO staging is applied to charted creatinine, urine output and CRRT as mimic-code computes them, with mimic-code's baseline creatinine (creatinine_baseline) rather than a pre-admission baseline.
- The urine-output criteria depend on charting completeness (hourly output and a recorded weight); sparse charting understages, and the CRRT route to stage 3 needs the CRRT settings itemids.
- Stage 0 in the distribution includes stays with no creatinine or urine-output row in the window, not only stays assessed as normal.
- The onset is the first qualifying chart time within the window, not the clinical onset; AKI that develops after the window is negative under the default 7 days.

### `sepsis3@1.0.0` — Sepsis-3 per ICU stay (mimic-code operationalisation, suspected infection + SOFA >= 2)

- **grain** `icustay` · **def_hash** `8a1762166ae9` · **locked** yes · `defs/sepsis3.yaml`
- **criteria** `s3` · **onset** `earliest`
- **references** (none)
- **concepts pinned** `mimiciv_derived.sepsis3` (`3ba6b0f7008f`, unpatched)
- **evidence columns** `sofa_time` = first(`sofa_time`), `sofa_score` = first(`sofa_score`)

| leaf | kind | definition | polarity |
|---|---|---|---|
| `s3` | concept | concept(mimiciv_derived.sepsis3.sepsis3 = True, key stay_id, time suspected_infection_time, evidence sofa_time = first(sofa_time), sofa_score = first(sofa_score)) | positive |

An ICU stay meets Sepsis-3 when the mimic-code `sepsis3` concept flags it: a suspicion of infection (an antibiotic order paired with a culture within the concept's window) and a SOFA score of 2 or more within -48 h / +24 h of the suspected-infection time. The onset is the suspected-infection time; the SOFA window end (`sofa_time`) and the score (`sofa_score`) are carried as typed evidence columns.

What it does not claim:

- Not a clinical adjudication of sepsis - it is the mimic-code operationalisation of the Sepsis-3 criteria over charted antibiotics, cultures and the SOFA components.
- The suspicion-of-infection rule depends on antibiotic and microbiology charting; a stay treated without a paired culture, or with an infection first suspected before the ICU stay, can be missed.
- The SOFA score inherits the concept's imputation choices (a missing component counts as 0), so a stay with sparse charting can fall below the threshold.
- The onset is the recorded suspected-infection time, not the clinical onset; ages >= 89 and the per-patient date shift do not affect this per-stay definition.

### `sepsis_explicit@1.0.0` — Sepsis, explicitly coded per admission (Angus explicit septicemia / sepsis codes)

- **grain** `hadm` · **def_hash** `edcd2302f046` · **locked** yes · `defs/sepsis_explicit.yaml`
- **criteria** `dx` · **onset** `earliest`
- **references** `sepsis_explicit@1.0.0` (`5d172007c621`)

| leaf | kind | definition | polarity |
|---|---|---|---|
| `dx` | diagnosis | diagnosis(sepsis_explicit@1.0.0, position any, min_admissions 1) | positive |

An admission carries explicit sepsis when any of its billed diagnoses is in the sepsis_explicit@1.0.0 code set (septicemia / sepsis by organism, severe sepsis, septic shock, puerperal and postprocedural sepsis; ICD-9-CM and ICD-10-CM). The onset is the admission's discharge time (diagnoses carry no timestamp). The billing-code companion of sepsis3@1.0.0 for the classic explicit-codes vs Sepsis-3 agreement cross-tab.

What it does not claim:

- Explicit codes under-ascertain sepsis - coding practice varies by era and by organism-specific coding, and the ICD-10-CM codes T81.12- / T81.44- exist only from FY2017 / FY2020.
- A billed code is not a clinical adjudication and says nothing about timing within the admission; the onset is the discharge time by convention.
- Bacteremia without a sepsis code (790.7 / R78.81) is deliberately outside the set.

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

A phenotype here is a **computable operationalisation** of billed codes, orders, results
and mimic-code concepts - not a validated algorithm: no chart review backs the packaged
definitions, a billed code under-records, an order is not an administration, and the
onset is the earliest *recorded* evidence, never the clinical onset. Every definition
states its own limits in `what_it_does_not_claim` (above). The EP-42 trio in particular:
`sepsis3@1.0.0` is mimic-code's Sepsis-3 operationalisation (suspicion of infection from
charted antibiotics and cultures, SOFA >= 2), not adjudicated sepsis; `kdigo_aki@1.0.0`
applies KDIGO staging to charted creatinine, urine output and CRRT with mimic-code's
baseline creatinine, so its urine-output criteria depend on charting completeness and its
stage 0 includes unassessed stays; `sepsis_explicit@1.0.0` is a billing-code definition
whose codes under-ascertain sepsis and vary by era. The Phenotype Studio page (EP-63),
cohort criteria referencing phenotypes (EP-46/47), the rates module (EP-68) and the
notes linkage (EP-153) build on the same registry and views.
