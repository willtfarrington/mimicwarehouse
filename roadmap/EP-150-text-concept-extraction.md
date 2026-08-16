# EP-150 — Concept extraction + negation/temporal context (medspaCy)

**Size:** M · **Tier:** fixture+full · **Core/Stretch:** stretch · **Depends on:** EP-149 (Note search + sectioning) · **Blocks:** EP-153 (Linkage to structured events), EP-155 (Capstone #8)

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-147 (Re-plan P9) before execution (track gated at EP-127; re-chartered by EP-136).

## Context

Second step of the representative text workflow (D-3; capability category 27): rule-based concept
extraction with assertion context (negated / historical / hypothetical / family / uncertain) using
medspaCy with a regex baseline (P10 standing decision), running entirely locally in the `text`
dependency group (spaCy and medspaCy ship cp313 wheels; scispaCy does not — D-15 — and is parked).
The four targets are chosen so that EP-153 can link them to structured phenotypes and concepts:
`sepsis` (sepsis-3, EP-42), `aki` (KDIGO stage, EP-42), `mech_vent` (mimic-code `ventilation`,
EP-37) and `ett` (endotracheal tube in radiology reports). Inputs are the notes lake and section
offsets (EP-148/149); outputs are offsets and flags — never spans of text (GOVERNANCE §9).

## Scope sketch (refine at re-plan)

1. **Target rule sets** (`src/mimicwarehouse/text/targets/<concept>.yaml`) — concept id, literal
   and regex patterns, applicable `note_type`s / sections, link to the code-set registry name
   (EP-40); versioned like code sets (semver + `rules_hash`).
2. **medspaCy pipeline** (`text/extract.py`) — blank `en` model (no pretrained NER, no downloads),
   PyRuSH sentencizer, `TargetMatcher` from the YAML, `ConText` with default modifiers plus a
   project modifier list, section tags from the EP-149 offsets (e.g. mentions inside
   `past_medical_history` → historical by section rule); `nlp.pipe` batches with `n_process`
   behind an `if __name__ == "__main__":` guard (Windows spawn). `text.guard.ensure_local_only()`
   at import.
3. **Regex baseline** (`text/extract_regex.py`) — same YAML, NegEx-style trigger window (± 6
   tokens); the fallback if medspaCy wheels fight the toolchain and the comparison row in every
   evaluation table.
4. **Output** `notes.derived.note_concepts (note_id, concept_id, section_name, char_start,
   char_end, pattern_id, is_negated, is_historical, is_hypothetical, is_family, is_uncertain,
   extractor, rules_hash)` materialised under `notes\lake\derived\note_concepts\<extractor>@
   <rules_hash>\subject_bucket=NN\` by `uv run --group text mwh build --tier full --dag
   notes-concepts --with-notes` (background job, log `%MWH_DATA_ROOT%\runs\jobs\notes-concepts.log`,
   resumable per bucket); CLI wrapper `mwh text extract --concept sepsis --with-notes --tier full`.
5. **Validation** — fixture ground truth (planted mentions from the EP-148 generator) → per-concept
   and per-modifier precision/recall; require F1 ≥ 0.90 for the medspaCy pipeline on the fixture and
   report the regex baseline alongside. Optional owner-only step: label a 100-note real sample in a
   local marimo notebook (labels under `%MWH_DATA_ROOT%\notes\annotations\`, never committed, never
   in tool output) → real-data PPV with a Wilson CI reported as an aggregate. The notebook runs
   only under the EP-148 attach gate (`attach_notes()` with actor = owner, `--with-notes`), writes
   an EP-30 audit line per note viewed, lives under `%MWH_DATA_ROOT%\notes\annotations\` (never in
   the repo, never executed or read by a Claude session), no cell output is pasted anywhere, and
   the exception to the app-only row-view path (GOVERNANCE §4.4) is recorded as a `DECISIONS.md`
   addendum under D-32.
6. **Aggregates** — mention prevalence per concept × `note_type` × section × `anchor_year_group`,
   negation/historical rates per concept, extractor throughput (docs/s, CPU) into the benchmark
   ledger; run record via `run.py` (EP-35); every table through `disclose.suppress`.

## Out of scope

- Agreement with structured events, hadm-level flags → EP-153 (Linkage to structured events).
- UMLS / scispaCy / QuickUMLS linking → `final-roadmap.md` (TXT-1); radiology structured extraction
  → TXT-3; local LLM extraction → DL-4.
- Topics / classification → EP-152.

## Verification / acceptance (sketch)

- `uv run poe test -m ep_150` green on fixture (F1 thresholds included);
  `uv run --group dev mwh verify EP-150` green.
- Import with `MWH_ALLOW_REMOTE=true` is **refused** in a test.
- Full-tier `notes-concepts` job launched in the background; run id, wall time and docs/s recorded
  in the completion note (EP-153 records them if the job outlives the session).
- Prevalence / negation-rate tables under `runs/<run_id>/tables/` pass
  `uv run --group dev mwh disclose check`; no output contains note text or ids.

## Parked → final-roadmap.md

- Trained assertion classifier (i2b2-style) beyond ConText rules — trigger: owner-labelled sample
  PPV < 0.85 for any concept.
- Additional target concepts (pneumonia, delirium, DNR/DNI code status, pressors) — trigger:
  portfolio breadth after the capstone.
