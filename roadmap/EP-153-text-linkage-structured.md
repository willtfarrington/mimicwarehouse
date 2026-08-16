# EP-153 — Linkage to structured events

**Size:** M · **Tier:** fixture+full · **Core/Stretch:** stretch · **Depends on:** EP-150 (Concept extraction + negation/temporal context (medspaCy)), EP-42 (Phenotypes: sepsis-3 + KDIGO AKI stage) · **Blocks:** EP-155 (Capstone #8)

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-147 (Re-plan P9) before execution (track gated at EP-127; re-chartered by EP-136).

## Context

Third step of the representative text workflow (D-3; capability category 27): the EP-150 mentions
are linked to structured phenotypes and concepts — `sepsis` ↔ `sepsis3@<version>` and `aki` ↔
`kdigo_aki@<version>` stage ≥ 1 (EP-42), `mech_vent` ↔ invasive ventilation episodes (mimic-code
`ventilation`, EP-37) — at the hadm grain, and `ett` mentions in radiology reports are aligned in
time to ventilation episodes.
The claim is **agreement between two imperfect sources**, labelled associational, never validity
of either. Caveats that bite: discharge summaries summarise the whole stay (`charttime` ≈
discharge) while radiology reports carry in-stay times; many radiology reports have no `hadm_id`;
per-patient date shift is identical for a subject's notes and events, so within-patient offsets
are valid; `anchor_year_group` is the only cross-patient axis (EP-34). hadm-keyed outputs stay in
the notes lake (owner-only, GOVERNANCE §9); only suppressed aggregates leave it.

## Scope sketch (refine at re-plan)

1. **hadm-level text flags** (`src/mimicwarehouse/text/linkage.py`; mart
   `notes\lake\marts\text_flags\<concept>@<rules_hash>\`) — per `hadm_id` and concept:
   `n_affirmed` (not negated / historical / hypothetical / family), `n_negated`,
   `first_affirmed_hours` relative to `admittime` (`timesem.py`, EP-34), sections mentioned; built
   for `sepsis`, `aki`, `mech_vent`, `ett`; run record via `run.py` (EP-35); calls
   `text.guard.ensure_local_only()` at import; hadm-keyed outputs never leave
   `%MWH_DATA_ROOT%\notes\`.
2. **Agreement analysis** — 2×2 at hadm grain, text-affirmed vs structured-positive, for the three
   hadm-level pairs; sensitivity / PPV / specificity / NPV in **both** directions plus Cohen's κ,
   cluster-bootstrap CIs by `subject_id` (`stats/boot`, EP-78); stratified by `anchor_year_group`
   and `note_type`; restricted to hadms with ≥ 1 discharge summary; every cell through
   `disclose.suppress` (EP-43).
3. **Temporal alignment** — for affirmed `ett` mentions in radiology reports: share whose
   `charttime` falls inside an invasive-ventilation episode ± 6 h, and an offset histogram (hour
   bins; bins < 11 suppressed) using the relative-time helpers of `timeline.py` (EP-49) — the
   event-aligned demonstration of the track.
4. **CLI + full run** — `uv run --group text mwh text link --concept sepsis --reference sepsis3
   --with-notes --tier full` (one invocation per pair; always a logged background job:
   `--background --job notes-link-<concept>`, log `%MWH_DATA_ROOT%\runs\jobs\notes-link.log`; poll
   with `mwh jobs`); tables and figures rendered through the report engine (EP-130) with claim type
   **associational** and the retrospective statement.
5. **Tests on the fixture notes** (`tests/ep/test_ep153.py`) — the EP-148 generator plants mentions
   concordant with the fixture's phenotype / ventilation tables at known rates (e.g. sensitivity
   0.8, PPV 0.9) → recovered within the bootstrap CI; the ETT offset test recovers planted report
   times; no ids or text in outputs.

## Out of scope

- Note-derived flags as a phenotype-engine criterion type (`text_concept`) → Parked (with PHE-2).
- Note lanes in the Timeline viewer (EP-67) → Parked; using note-derived features in prediction
  models → never in v1 (leakage; EP-152 Out of scope).
- Capstone narrative → EP-155 (Capstone #8).

## Verification / acceptance (sketch)

- `uv run poe test -m ep_153` green on fixture; `uv run --group dev mwh verify EP-153` green;
  import with `MWH_ALLOW_REMOTE=true` is refused (test).
- Full-tier run ids for the three hadm-level pairs and the ETT alignment recorded in the completion
  note (plus the EP-150 job timing if EP-150 could not record it).
- Agreement tables and the offset histogram under `runs/<run_id>/` pass
  `uv run --group dev mwh disclose check`; the report artifact is labelled associational and
  retrospective.

## Parked → final-roadmap.md

- `text_concept` criterion type in the phenotype engine (note-derived flags as inclusion /
  exclusion evidence) — trigger: a phenotype validation study (PHE-2) is scheduled.
- Owner-only note lane in the Timeline viewer (EP-67) — trigger: v2 UI work on the text track.
- Three-way agreement adding ICD billing codes as a third source — trigger: reviewer interest.
