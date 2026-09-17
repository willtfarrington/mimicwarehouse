# Protocols: schema, freeze registry and `mwh protocol` (EP-51)

The one written answer to "how is an analysis pre-specified, frozen and run here?" - the
prose twin of `src/mimicwarehouse/protocol/` (DESIGN §13, §15; GOVERNANCE §7, §8, §12;
D-25). Capability 37 - prospective-style inquiry over retrospective data - rests on one
rule: **a YAML protocol is content-hashed and registered before it runs; runs cite the
frozen hash; amendments append a new hash linked to the previous one; an unfrozen or
modified protocol cannot run.** A protocol names an EP-46 cohort by `id@version`, an
exposure, outcomes with `timesem` censoring rules, covariates, feature windows, an
analysis plan, a temporal holdout by `anchor_year_group` era, a claim type, and the two
fixed texts every protocol carries (the seeds policy, the retrospective statement).
Nothing on this page is derived from patient data: the schema reference, the worked
example and the refusal table are rendered from the package
(`python -m mimicwarehouse.protocol` re-renders the marked blocks; `test_ep51` asserts
they are in sync). All MIMIC-IV analyses in this repository are retrospective.

## 1. The shape

A protocol is one YAML document. The worked example is the seed protocol, the tracer
question of EP-31 over the tracer cohort of EP-46 (`tracer_mortality@1.0.0`, packaged
under `src/mimicwarehouse/protocol/specs/`):

<!-- example:begin -->
```yaml
# mimicwarehouse protocol (EP-51, D-25). ASCII only; no calendar dates (per-patient date
# shift) - relative windows and anchor_year_group eras only. Freeze it before running it
# (`mwh protocol freeze <this file>`): the content hash pins everything below except the
# documentation fields (title, description, notes, amendment_reason) and inlines the
# cohort's def_hash and every referenced hash; an edited frozen version is refused - bump
# `version` and amend. The seed protocol is the tracer question (EP-31): first ICU stay of
# adult patients -> in-hospital mortality, described (not modelled) over the EP-46 cohort.
id: tracer_mortality
version: "1.0.0"
title: Tracer question - first ICU stay of adults and in-hospital mortality (descriptive)
description: >
  The tracer bullet's question as a frozen protocol: describe in-hospital mortality (with
  discharge alive as the competing event) over the first ICU stay of adult patients, by
  age, gender, admission type and first care unit, with the 24 hours before the ICU
  admission as the covariate window. It exists so every later protocol-driven workflow
  (the Freezer page, the temporal-holdout runner, the signature workflows, the
  target-trial harness) has a frozen, registered example to run against.
claim_type: exploratory
cohort: first_icu_adults@1.0.0
unit_of_analysis: icustay
exposure: null
outcomes:
  - name: in_hospital_mortality
    definition: {column: cohort.censor_reason, equals: death}
    censoring: in_hospital_mortality
    competing_events: [discharge_alive]
covariates:
  - {name: age, source: cohort.age_at_index, transform: age_band}
  - {name: gender, source: mimiciv_hosp.patients.gender, transform: categorical}
  - {name: admission_type, source: mimiciv_hosp.admissions.admission_type, transform: categorical}
  - {name: first_careunit, source: mimiciv_icu.icustays.first_careunit, transform: categorical}
feature_windows:
  observation: {start_h: -24, end_h: 0}
  gap_h: 0
  prediction_h: null
analysis_plan:
  method_family: descriptive
  estimand: >
    The proportion of first ICU stays of adults ending in in-hospital death, overall and
    by each covariate level (k-suppressed counts and proportions).
  model_spec: >
    Attrition chain of the cohort (EP-47) and descriptive tables per covariate level; no
    model is fitted under this protocol (the EP-31 logistic fit is the tracer's own report).
  hyperparameter_policy: none (no tuned hyperparameters)
  subgroups: []
  sensitivity_analyses:
    - Ages at or above 89 are shipped as 91 (age_capped); the top age band is reported as capped.
  multiplicity: none (single pre-specified descriptive analysis)
  missing_data: complete-case; missingness reported per covariate
  sample_size_note: fixed by the cohort (retrospective); no power calculation
temporal_holdout:
  development_eras: ["2008 - 2010", "2011 - 2013", "2014 - 2016"]
  holdout_eras: ["2017 - 2019"]
  sealed_eras: ["2020 - 2022"]
seeds_policy: >-
  derive_seed: every stochastic stage draws from run.derive_seed(protocol_id, stage, salt)
  (docs/methods/determinism.md); the seed scope is this protocol's id
retrospective_statement: >-
  All MIMIC-IV analyses in this repository are retrospective: MIMIC-IV is a de-identified,
  date-shifted record of care already delivered, so a frozen protocol pre-specifies the
  analysis, never the data collection.
references: {cohorts: [first_icu_adults@1.0.0], codesets: [], phenotypes: [], concepts: []}
amends: null
notes: >
  Seeded by EP-51 as the first frozen protocol. The temporal holdout is declared and
  unused until EP-129 (the same three-list shape its TemporalHoldout consumes); the
  descriptive plan is served by the `cohort_only` runner, which builds (or reuses) the
  cohort on the tier, records the suppressed attrition and writes protocol_summary.md.
```
<!-- example:end -->

Five MIMIC caveats are baked into the schema and refused at load time:

- **No calendar dates anywhere in the definition.** PhysioNet shifts every patient's
  timestamps (DESIGN §7), so a date-like string or YAML date object in any non-documentation
  field is refused; time is relative to the index (`[start_h, end_h)` windows, horizons in
  days) and the only cross-patient axis is the `anchor_year_group` era.
- **The temporal holdout is by era only.** `development_eras` / `holdout_eras` /
  `sealed_eras` are disjoint subsets of the five `timesem.ERAS` labels (development and
  holdout non-empty); it is *declared* here and *consumed* by the temporal-holdout runner
  (EP-129), which adds the one-look rule.
- **Grains come from the registry.** `unit_of_analysis` must be an available `timesem`
  grain (`edstay` / `note` placeholders are refused) **and** the grain of the referenced
  cohort (checked at freeze, when the cohort resolves).
- **Censoring rules come from `timesem`.** An outcome names one of the
  `timesem.CENSORING_RULES` (`in_hospital_mortality`, `mortality_30d`, ...); an
  in-hospital outcome takes no horizon and `discharge_alive` competes with in-hospital
  outcomes only (the EP-46 follow-up rules, one layer up).
- **No identifier columns.** A definition, covariate or exposure source may not name
  `subject_id`, `hadm_id`, `stay_id`, `note_id` or any other contract identifier
  (GOVERNANCE §4): a protocol analyses attributes, never identities.

And four **claim / plan consistency rules** (`protocol.spec.CLAIM_RULES`): an
`associational` or `causal` claim needs an exposure; a `causal` claim needs the `causal`
method family; a `predictive` claim needs the `prediction` or `bayes` family; the
`causal` family needs an exposure. Descriptive and predictive protocols set
`exposure: null`.

## 2. Claim types and the retrospective statement

| claim type | the report may say | needs a frozen hash to run? |
|---|---|---|
| `exploratory` | what the data shows; hypotheses generated, not tested | no (recommended) |
| `confirmatory` | a pre-specified hypothesis was tested as frozen | **yes** (D-25) |
| `predictive` | a model predicts an outcome with the reported performance on held-out eras | no (recommended; EP-110 signatures freeze) |
| `associational` | an exposure is associated with an outcome, adjusted as pre-specified | no (recommended) |
| `causal` | an effect estimate under the stated identification assumptions | **yes** (D-25) |

Every protocol carries the fixed retrospective statement
(`protocol.spec.RETROSPECTIVE_STATEMENT`, verbatim, validator-enforced), and every
protocol-driven artefact repeats it (GOVERNANCE §7):

> All MIMIC-IV analyses in this repository are retrospective: MIMIC-IV is a
> de-identified, date-shifted record of care already delivered, so a frozen protocol
> pre-specifies the analysis, never the data collection.

The seeds policy is fixed text too (`protocol.spec.SEEDS_POLICY`): every stochastic
stage draws from `run.derive_seed(protocol_id, stage, salt)`
([determinism.md](determinism.md)), and the seed scope of a protocol run is the
protocol's id, so a frozen protocol reproduces its numbers across runs.

## 3. The content hash

`Protocol.content_hash(resolved)` is the sha256 of the canonical JSON (key-sorted,
whitespace-free) of the *definition*: every field except the documentation fields
(`title`, `description`, `notes`, `amendment_reason`), with defaults made explicit and
the **resolved reference hashes inlined** - the cohort's `def_hash`, every code set's
and phenotype's `def_hash`, every vendored concept's executed-SQL sha256 (EP-42's pin).
So:

- key order, whitespace, comments, quoting and omitted defaults never move the hash;
- any definition change does - a window, a covariate, an era, the plan text, the claim
  type, `amends`;
- a change *underneath* moves it too: a cohort spec, code set or phenotype re-released
  under the same `id@version` (which their own locks refuse) or a re-ported concept
  changes the hash of every protocol that references it, and `verify` says which.

The cohort's `def_hash` already inlines the code sets and phenotypes *it* references
(D-25 addendum, EP-46), so a protocol pins the whole definition tree it was written
against. The hash never covers the YAML bytes; the registry line records the file's
sha256 separately (`source_sha256`) so `verify` can tell an edited copy from a moved
reference.

## 4. The lifecycle: freeze, verify, amend, run

```powershell
uv run mwh protocol freeze src/mimicwarehouse/protocol/specs/tracer_mortality.yaml   # -> the hash
uv run mwh protocol list                                                             # the registry (newest first)
uv run mwh protocol show <hash> [--yaml]                                             # the line, lineage, the frozen YAML
uv run mwh protocol verify <yaml | hash>                                             # exit 1 on drift / unfrozen / unknown
uv run mwh protocol amend studies/x/protocol.yaml --previous <hash> --reason "..."   # a linked amendment
uv run mwh protocol run <hash> --tier dev --runner cohort_only                       # a kind: protocol run
```

**Freeze.** `mwh protocol freeze <yaml>` validates the document, resolves every
reference, computes the hash and then, in this order: copies the YAML **byte for byte**
to `<data_root>/runs/protocols/<hash>.yaml` (`fsio.atomic_write_text`, then the
read-only attribute - a later replace onto it fails, which is the point), appends one
line to `<data_root>/runs/protocols.jsonl` (`fsio.append_jsonl`, append-only; the ledger
canon of EP-33) and writes a `protocol freeze <hash>` audit line to `runs/audit.jsonl`
(GOVERNANCE §8). The line carries `hash`, `protocol_id`, `version`, `claim_type`,
`cohort` + `cohort_hash`, `unit_of_analysis`, `timestamp_utc`, `git_sha`, the source
`path`, the data-root-relative `frozen_path`, `source_sha256`, `ref_hashes`
(`{"<kind>:<ref>": hash}`), `amends`, `amendment_reason` and the `actor`. Freezing
content that is already frozen is a no-op that prints the existing hash; freezing an
`id@version` that is already frozen **with another hash** is refused with exit 3
(`ProtocolFrozenError`) - the immutability rule code sets, phenotypes and cohort specs
already follow: bump the version and amend. Both `runs/protocols.jsonl` and the copies
are non-reproducible state that `mwh backup` (EP-52) carries.

**Verify.** `mwh protocol verify <yaml>` hashes the file and looks it up: `ok` when
that hash is registered and its frozen copy still hashes to its name; `drift` when the
file's `id@version` is frozen at another hash (the file changed since) or the frozen
copy no longer hashes to its name (the message says whether the copy was edited -
`source_sha256` differs - or a referenced definition moved underneath a byte-identical
copy); `unfrozen` when nothing is registered. `mwh protocol verify <hash>` checks the
copy of a registered hash (`ok` / `drift` / `missing_copy`) or reports `unknown`. Every
non-`ok` status exits 1.

**Amend.** An amendment is a new protocol version that declares `amends: <previous
hash>` in its YAML (the link is *content*, so it is hashed and travels with the frozen
copy) and is frozen with `mwh protocol amend <yaml> --previous <hash> --reason "..."`:
`--previous` must equal the YAML's `amends`, the previous hash must be frozen under the
**same protocol id**, the version must be greater, and a reason - `--reason`, else the
YAML's `amendment_reason` (documentation, unhashed) - is recorded in the ledger line.
`mwh protocol show` prints the lineage (`v1 <hash> -> v2 <hash> -> ...`). A plain
`freeze` refuses a YAML that carries `amends`.

**Run.** `mwh protocol run <hash> [--tier t] [--runner cohort_only] [--yaml PATH]`
first applies the D-25 refusals (§5) - each refusal is audited and exits 3 - then opens
`run.start(kind="protocol", protocol_id=<id>, protocol_hash=<hash>, claim_type=...)`
(EP-35), so the manifest and the `runs/ledger.jsonl` line carry the hash and the claim
type, records the protocol, the cohort and every resolved reference as refs, dispatches
to the runner, and writes `runs/<run_id>/protocol_summary.md`. A `--yaml` override is
accepted only when it hashes to the frozen value (the working copy has not drifted).

## 5. Refusals

<!-- refusals:begin -->
| command | refuses when | exit | how |
|---|---|---|---|
| `freeze / amend` | the YAML does not validate (schema, dates, eras, grain, claim rules) | 2 | `ProtocolError` names every problem |
| `freeze / amend` | a reference does not resolve (cohort, code set, phenotype, concept) or the unit of analysis is not the cohort's grain | 2 | `ProtocolReferenceError` |
| `freeze` | the YAML carries `amends` | 2 | use `amend --previous` |
| `freeze / amend` | the same `id@version` is already frozen with another hash | 3 | `ProtocolFrozenError` — bump the version or amend |
| `amend` | `--previous` is not the YAML's `amends`, is unknown, froze another protocol id, or the version is not bumped; no reason | 2 (unknown: 3) | `ProtocolError` / `UnknownProtocolError` |
| `verify` | the frozen copy no longer hashes to its name, the YAML's content is not the frozen one, the hash is unknown, the copy is missing | 1 | `drift` / `unfrozen` / `unknown` / `missing_copy` |
| `run` | the hash is not in the registry | 3 | `UnknownProtocolError` |
| `run` | the frozen copy no longer hashes to its name (edited, or a referenced definition moved) | 3 | `ProtocolRefusedError` |
| `run` | `--yaml` differs from the frozen copy | 3 | `ProtocolRefusedError` |
| `run` | the runner fails (cohort build error) | 1 | the run is marked `failed` in the ledger |
| `any run (`run.start`)` | `claim_type` confirmatory or causal without a `protocol_hash` | raises | `run.ProtocolPolicyError` (D-25) |
<!-- refusals:end -->

The last row is the policy hook in the run ledger itself (`run.py`, EP-35): any
`run.start(...)` whose `claim_type` label is `confirmatory` or `causal` without a
`protocol_hash` raises `run.ProtocolPolicyError` - no module can produce a confirmatory
or causal run record outside a frozen protocol, whichever CLI opened it. An unknown
claim label or a malformed hash is refused the same way. The label is the first word:
the earlier reports qualify it in parentheses (`exploratory (measurement process)`,
`associational (exploratory)`), which `run.claim_label` strips before judging.

## 6. Runners and the run record

Runners are registered by name in `protocol.runners.RUNNERS`
(`@register_runner("<name>")`); later briefs add theirs without touching the dispatch
(`predictive` -> EP-110, `target_trial` -> EP-95, the temporal holdout -> EP-129). v1
ships **`cohort_only`**:

1. materialise the protocol's cohort on the tier through EP-47 - reuse the tier's build
   when its status entry and mart manifest carry the frozen `def_hash` and the file is
   complete, else run the `cohorts.specs` / `cohorts.build` / `catalog` steps through
   the DAG runner (one `kind: cohort` run inside the protocol run, as `mwh cohort build`
   does);
2. record the mart's layer snapshot ids, the cohort build's run id and the cohort file's
   sha256 in the protocol run's manifest (`params`), the cohort / code-set / phenotype /
   concept refs with hashes, and the **suppressed** attrition chain
   (`cohort.build.attrition`, chain mode at the tier's `k`: a small total is withheld, a
   small drop is withheld and its neighbours banded) - a run manifest is a
   session-readable surface (`mwh runs show`), so it never carries a raw small cell;
3. render the cohort card and the chain into the summary.

`runs/<run_id>/protocol_summary.md` holds, in order: the title and **claim type**, the
retrospective statement, the frozen identity (`id@version`, hash, amendment link, cohort
and `def_hash`), the pre-specified analysis as declared (exposure, outcomes, covariates,
feature windows, the plan, subgroups, sensitivity analyses, the temporal holdout, the
seeds policy), the references table (kind, reference, hash), the runner's sections and
the EP-35 reproduction block (`run.reproduction_block`). It is ASCII, cites ids inline
only, and stays under the data root; a report promoted into `docs/` goes through
`mwh disclose check` like any other artefact (GOVERNANCE §7).

## 7. Reading the registry

`mwh protocol list` is the session-side listing (a rich table over the ledger: hash,
`id@version`, claim type, cohort, amendment link, timestamp, git sha; `--json` for the
raw lines). `mwh runs refresh` rebuilds `warehouse/runs.duckdb` with a **`protocols`**
view beside `ledger` / `manifests` / `attrition` (typed by
`protocol.registry.PROTOCOLS_COLUMNS`; `run.runs_db_views` adds it, `safe.build_runs_db`
creates it), so the registry joins the run ledger on `protocol_hash`:

```powershell
uv run mwh runs refresh
uv run mwh sql "SELECT p.claim_type, count(*) AS n FROM runs.ledger AS l JOIN runs.protocols AS p ON l.protocol_hash = p.hash GROUP BY 1" --tier dev
```

`runs.*` is **not** a safe-query registry exemption (the EP-33 amendment on the brief):
a `mwh sql` over `runs.protocols` needs a count-family column and is k-suppressed like
any other statement, so a registry with fewer than eleven protocols answers only through
`mwh protocol list`. That is by design - the view exists for joins and the app (EP-128,
EP-134), not as a second listing.

## 8. Schema reference

Generated from `protocol.spec.json_schema()` (the Freezer page, EP-128, renders the
same schema as a form). Windows are `[start_h, end_h)` hours relative to the index.

<!-- schema:begin -->
**`Protocol`** — A mimicwarehouse analysis protocol (EP-51): claim type, cohort reference, unit of analysis, exposure, outcomes, covariates, feature windows, analysis plan, temporal holdout by anchor_year_group era, the fixed seeds policy and retrospective statement, references and the amendment link. No absolute dates; windows are [start, end) hours relative to the index.

| field | type | required | meaning | default |
|---|---|---|---|---|
| `id` | string | yes | a slug; with version the reference id@version |  |
| `version` | string (`^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$`) | yes | semver |  |
| `title` | string | yes | documentation (not hashed) |  |
| `description` | string |  | documentation (not hashed) |  |
| `claim_type` | one of `exploratory`, `confirmatory`, `predictive`, `associational`, `causal` | yes | the claim the report may make |  |
| `cohort` | string | yes | the EP-46 cohort spec as id@version |  |
| `unit_of_analysis` | one of `subject`, `hadm`, `icustay`, `icu_day`, `hour_bin`, `person_time` | yes | the grain (timesem's registry; must be the cohort's) |  |
| `exposure` | `Exposure` (optional) |  | null for descriptive / predictive |  |
| `outcomes` | list of `Outcome` | yes | at least one |  |
| `covariates` | list of `Covariate` |  | ordered |  |
| `feature_windows` | `FeatureWindows` | yes |  |  |
| `analysis_plan` | `AnalysisPlan` | yes |  |  |
| `temporal_holdout` | `TemporalHoldout` (optional) |  | declared eras, or null |  |
| `seeds_policy` | string |  | fixed text (EP-36) | `"derive_seed: every stochastic stage draws from run.derive_seed(protocol_id, stage, salt) (docs/methods/determinism.md); the seed scope is this protocol's id"` |
| `retrospective_statement` | string |  | fixed text (GOVERNANCE §7) | `"All MIMIC-IV analyses in this repository are retrospective: MIMIC-IV is a de-identified, date-shifted record of care already delivered, so a frozen protocol pre-specifies the analysis, never the data collection."` |
| `references` | `References` (optional) |  | optional; must match |  |
| `amends` | string (optional) |  | the previous frozen hash, or null |  |
| `amendment_reason` | string |  | documentation (not hashed) |  |
| `notes` | string |  | documentation (not hashed) |  |

**`AnalysisPlan`** — The pre-specified analysis: method family, estimand, model spec, hyperparameter policy, subgroups, sensitivity analyses, multiplicity rule, missing-data policy and the sample-size note (short texts; hashed).

| field | type | required | meaning | default |
|---|---|---|---|---|
| `method_family` | one of `descriptive`, `glm`, `survival`, `causal`, `prediction`, `bayes` | yes | the method family |  |
| `estimand` | string | yes | what is estimated (or described) |  |
| `model_spec` | string | yes | the model / summary specification |  |
| `hyperparameter_policy` | string |  | how hyperparameters are chosen | `"none (no tuned hyperparameters)"` |
| `subgroups` | list of string |  | pre-specified subgroups |  |
| `sensitivity_analyses` | list of string |  | pre-specified sensitivity analyses |  |
| `multiplicity` | string |  | the multiplicity rule | `"none (single pre-specified analysis)"` |
| `missing_data` | string |  | the missing-data policy | `"complete-case; missingness reported per covariate"` |
| `sample_size_note` | string |  | sample size | `"fixed by the cohort (retrospective); no power calculation"` |

**`Covariate`** — One covariate: where it comes from, over which window, how it is transformed.

| field | type | required | meaning | default |
|---|---|---|---|---|
| `name` | string | yes | a slug; the model term / table column |  |
| `source` | string | yes | cohort.<column> or <schema>.<table>.<column> |  |
| `window` | `Window` (optional) |  | [start_h, end_h) hours; static when null |  |
| `transform` | one of `identity`, `log`, `standardize`, `categorical`, `age_band`, `indicator` |  | the transform applied | `"identity"` |

**`Definition`** — What an exposure or outcome *is*: exactly one of a code set (``id@version``), a phenotype (``id@version``), a vendored concept table (``mimiciv_derived.<name>``) or a column (``cohort.<column>`` / ``<schema>.<table>.<column>``), plus an optional ``equals`` value the column / concept must take. References resolve to hashes at freeze time.

| field | type | required | meaning | default |
|---|---|---|---|---|
| `codeset` | string (optional) |  | a code set as id@version |  |
| `phenotype` | string (optional) |  | a phenotype as id@version |  |
| `concept` | string (optional) |  | a vendored concept table |  |
| `column` | string (optional) |  | cohort.<col> or <schema>.<table>.<col> |  |
| `equals` | string \| integer \| number \| boolean (optional) |  | the value the column must take |  |

**`Exposure`** — The exposure of an associational / causal protocol (``null`` for descriptive and predictive ones).

| field | type | required | meaning | default |
|---|---|---|---|---|
| `name` | string | yes | a slug; the report's exposure label |  |
| `definition` | `Definition` | yes |  |  |
| `timing` | `Timing` | yes |  |  |

**`FeatureWindows`** — The observation window ``[start_h, end_h)`` relative to the index, the gap after it and the prediction horizon (hours).

| field | type | required | meaning | default |
|---|---|---|---|---|
| `observation` | `Window` | yes | the feature window relative to the index |  |
| `gap_h` | number |  | hours between observation end and target | `0.0` |
| `prediction_h` | number (optional) |  | prediction horizon, hours |  |

**`Outcome`** — One outcome: its definition, an optional window (hours after the index) **or** horizon (days), its ``timesem`` censoring rule and the competing events.

| field | type | required | meaning | default |
|---|---|---|---|---|
| `name` | string | yes | a slug; the report's outcome label |  |
| `definition` | `Definition` | yes |  |  |
| `window_h` | number (optional) |  | hours after the index |  |
| `horizon_days` | integer (optional) |  | days after the index |  |
| `censoring` | one of `in_hospital_mortality`, `mortality_30d`, `mortality_90d`, `mortality_1y` | yes | a timesem censoring-rule name |  |
| `competing_events` | list of string |  | competing events |  |

**`References`** — The declared references (optional; when given they must equal what the definitions name).

| field | type | required | meaning | default |
|---|---|---|---|---|
| `cohorts` | list of string |  | cohort specs as id@version |  |
| `codesets` | list of string |  | code sets as id@version |  |
| `phenotypes` | list of string |  | phenotypes as id@version |  |
| `concepts` | list of string |  | vendored concept tables |  |

**`TemporalHoldout`** — The era partition of a temporal holdout (by ``anchor_year_group`` only, never calendar dates): development, holdout and sealed eras — disjoint subsets of ``timesem.ERAS``; development and holdout non-empty. Declared here, consumed by EP-129's runner (which adds the one-look rule).

| field | type | required | meaning | default |
|---|---|---|---|---|
| `development_eras` | list of `2008 - 2010`, `2011 - 2013`, `2014 - 2016`, `2017 - 2019`, `2020 - 2022` | yes | eras the plan is fitted on |  |
| `holdout_eras` | list of `2008 - 2010`, `2011 - 2013`, `2014 - 2016`, `2017 - 2019`, `2020 - 2022` | yes | eras evaluated once |  |
| `sealed_eras` | list of `2008 - 2010`, `2011 - 2013`, `2014 - 2016`, `2017 - 2019`, `2020 - 2022` |  | eras never entering any frame |  |

**`Timing`** — When an exposure counts: a window relative to the index.

| field | type | required | meaning | default |
|---|---|---|---|---|
| `relative_to` | string |  | the anchor | `"index"` |
| `window` | `Window` | yes | [start_h, end_h) hours relative to the anchor |  |

**`Window`** — A half-open window ``[start_h, end_h)`` in hours relative to the index event (negative = before it; ``timesem.sql_hours_since`` in the compiled SQL).

| field | type | required | meaning | default |
|---|---|---|---|---|
| `start_h` | number | yes | window start in hours relative to the index (inclusive) |  |
| `end_h` | number | yes | window end in hours relative to the index (exclusive) |  |
<!-- schema:end -->

## 9. What later briefs add

- **EP-52** backs up `runs/protocols.jsonl` and `runs/protocols/**` with the other
  non-reproducible state.
- **EP-128** (Protocol Freezer page) edits, diffs, freezes and amends through the same
  `registry.freeze` / `amend` the CLI uses, and launches dev runs in-process.
- **EP-129** (temporal-holdout runner) consumes `temporal_holdout` - the same three-list
  shape - adds the one-look rule and the `runs/holdouts.jsonl` ledger.
- **EP-110** / **EP-95** register the `predictive` and `target_trial` runners.
- Parked (`final-roadmap.md`): OSF-style pre-registration exports from frozen protocols;
  a causal-language linter over protocol and report text (EP-128's parked list).
