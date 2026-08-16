# EP-51 — Protocol schema + freeze registry + `mwh protocol`

**Size:** M · **Tier:** fixture+dev · **Core/Stretch:** core · **Depends on:** EP-35 (Provenance run ledger), EP-46 (Cohort spec + registry) · **Blocks:** EP-52 (Backup of non-reproducible state (`mwh backup`)), EP-54 (Re-plan P3), EP-95 (Target-trial emulation harness), EP-110 (Signature #1: first-24h → in-hospital mortality), EP-128 (Protocol Freezer page + amendments UI), EP-129 (Temporal holdout runner)

## Context

Capability 37 (prospective-style inquiry over retrospective data) rests on D-25: a YAML protocol
is content-hashed and registered **before** it is run; runs must cite a frozen hash; amendments
append a new hash linked to the previous one; an unfrozen or modified protocol cannot run. This
brief builds `src/mimicwarehouse/protocol/` (DESIGN §13, §15): the pydantic `Protocol` (cohort
reference to an EP-46 spec, exposure, outcome, covariates, feature windows, analysis plan,
temporal holdout, claim type), the registry `runs/protocols.jsonl` + immutable copies, and the
`mwh protocol freeze|verify|amend|list|show|run` CLI wired into the run ledger (EP-35) so every
protocol run carries `protocol_hash` and `claim_type`. Caveats encoded: the temporal holdout is by
`anchor_year_group` only (no calendar dates); every protocol-driven report states that MIMIC-IV
analyses remain retrospective (GOVERNANCE §7); censoring rules come from `timesem` (EP-34);
`claim_type` is one of exploratory / confirmatory / predictive / associational / causal. The
Freezer page (EP-128), temporal-holdout runner (EP-129), signature workflows (EP-110–112) and the
target-trial harness (EP-95) sit on this module; the seed protocol is the tracer question.

## In scope

1. **`Protocol` schema** (`src/mimicwarehouse/protocol/spec.py`) — fields: `id`, `version`,
   `title`, `claim_type`, `cohort` (`id@version` → resolved `def_hash`), `unit_of_analysis`
   (grain), `exposure` {name, definition: codeset|phenotype|concept ref + timing rule relative
   to index, null for descriptive/predictive protocols}, `outcomes` [{name, definition (codeset|
   phenotype|concept|column), window_h or horizon_days, censoring rule name (`timesem`),
   competing_events}], `covariates` [{name, source, window_h, transform}], `feature_windows`
   {observation [start_h, end_h), gap_h, prediction_h}, `analysis_plan` {method_family
   (`descriptive|glm|survival|causal|prediction|bayes`), estimand, model spec text, hyperparameter
   policy, subgroups [], sensitivity_analyses [], multiplicity rule, missing_data policy,
   sample_size_note}, `temporal_holdout` {development_eras [], holdout_eras [], sealed_eras [] —
   disjoint subsets of `timesem.ERAS`, or null}, `seeds_policy` (`derive_seed` per EP-36, fixed
   text), `retrospective_statement` (constant, validator-enforced), `references` (all
   `id@version` → hashes), `amends`
   (previous hash or null), `amendment_reason`. `content_hash` = sha256 of canonical JSON of the
   full model minus `title`/free-text notes; validators refuse absolute dates, unknown eras,
   unavailable grains, and unresolved references.
2. **Freeze registry** (`registry.py`) — `mwh protocol freeze <yaml>`: validate, resolve refs,
   compute hash, copy the YAML byte-for-byte to `%MWH_DATA_ROOT%\runs\protocols\<hash>.yaml`
   (read-only attribute set), append `{hash, protocol_id, version, timestamp_utc, git_sha, path,
   cohort_hash, ref_hashes, amends}` to `runs/protocols.jsonl` (append-only), print the hash;
   `mwh protocol verify <yaml|hash>` recomputes and compares (exit ≠ 0 on drift); `mwh protocol
   amend <yaml> --previous <hash> --reason "…"` freezes a new hash with `amends` set; `mwh
   protocol list|show <hash>`; the EP-35 `runs.duckdb` gains a `runs.protocols` view.
3. **`mwh protocol run <hash> [--tier dev] [--runner cohort_only]`** — refuses (non-zero exit,
   explicit message) when the hash is not in the registry, when the frozen copy's hash no longer
   matches, or when a `--yaml` override differs from the frozen copy; otherwise starts
   `run.start(kind="protocol", protocol_id, protocol_hash, claim_type)` and dispatches to a
   registered runner (`protocol/runners.py`: v1 ships `cohort_only` — builds the cohort via EP-47,
   records attrition, writes `runs/<run_id>/protocol_summary.md` with claim type, the
   retrospective statement, references and the reproduction block). `run.py` policy hook: a run
   with `claim_type in {confirmatory, causal}` and no `protocol_hash` is refused (D-25).
4. **Seed protocol** (`src/mimicwarehouse/protocol/specs/tracer_mortality.yaml`,
   `tracer_mortality@1.0.0`) — cohort `first_icu_adults@1.0.0`, outcome in-hospital mortality
   (competing: discharge alive), covariates age/gender/admission type/first care unit, feature
   window [−24, 0) h, analysis plan `descriptive` (tracer), claim type `exploratory`, temporal
   holdout `development 2008–2010, 2011–2013, 2014–2016 / holdout 2017–2019 / sealed 2020–2022`
   (declared, unused until EP-129; the same three-list shape EP-129's `TemporalHoldout` consumes);
   frozen on dev during this EP (hash recorded in the completion note); `docs/methods/protocols.md`
   (new): schema, freeze/amend/run lifecycle, refusal rules, the retrospective statement.
5. **Tests** (`tests/ep/test_ep51.py`, `@pytest.mark.ep_51`; fixture, `dev`) — hash invariance
   (whitespace/key order) and sensitivity (any field change); freeze appends a ledger line and an
   immutable copy; `verify` fails after editing the YAML; `run` refuses an unknown hash, a modified
   frozen copy and an unfrozen YAML; `amend` links hashes; a `confirmatory` run without a hash is
   refused by `run.py`; `cohort_only` runner on fixture writes `protocol_summary.md` containing
   the claim type and the retrospective sentence; on dev, freeze + run `tracer_mortality@1.0.0`
   with `--runner cohort_only`.

## Out of scope

- Freezer page + amendments UI → EP-128; temporal-holdout runner → EP-129.
- Prediction/causal runners (`predictive`, `target_trial`) → EP-110 / EP-95 register their own.
- Backup of `runs/protocols.jsonl` and copies → EP-52 (this brief only names them).
- OSF-style pre-registration export → parked (`final-roadmap.md` § 36–38).

## Verification / acceptance

- `uv run poe test -m ep_51` green on fixture and dev; `uv run --group dev mwh verify EP-51` green.
- `uv run --group dev mwh protocol freeze src/mimicwarehouse/protocol/specs/tracer_mortality.yaml`
  prints a hash; `mwh protocol list` shows it; `mwh protocol run <hash> --tier dev --runner
  cohort_only` produces a run whose manifest has `protocol_hash` and `claim_type`.
- Editing the YAML then `mwh protocol verify` exits non-zero; `mwh protocol run` on an unfrozen
  copy exits non-zero with the refusal message (demonstrated in tests and once by hand).
- `docs/methods/protocols.md` exists; the seed protocol hash is in the completion note.
