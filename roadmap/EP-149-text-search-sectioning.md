# EP-149 — Note search + sectioning

**Size:** M · **Tier:** fixture+full · **Core/Stretch:** stretch · **Depends on:** EP-148 (Notes staging ⏱ (segregated lake + notes.duckdb FTS)) · **Blocks:** EP-150 (Concept extraction + negation/temporal context (medspaCy)), EP-151 (Embeddings (CPU-capable, GPU-accelerated) + similarity search), EP-154 (Text pages in app (search only)), EP-155 (Capstone #8)

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-147 (Re-plan P9) before execution (track gated at EP-127; re-chartered by EP-136).

## Context

First step of the representative text workflow (D-3: search → concept/negation extraction →
linkage; capability category 27) and the verifying brief for the EP-148 ⏱ staging job. Builds on
the segregated notes lake, `notes.duckdb` FTS index, attach gate and synthetic notes fixture from
EP-148. Governance shapes the API: search returns **k-suppressed aggregates**; note ids only through
an owner-gated path used by the app (EP-154); note text never in CLI or tool output (GOVERNANCE §4,
§9). Sectioning is rule-based (medspaCy + regex baseline is the P10 standing decision) and stores
offsets, not text.

## Scope sketch (refine at re-plan)

1. **Verify EP-148** — confirm via `uv run --group dev mwh jobs --job notes-stage` (state + INFO
   lines only, never the log file directly; INFO lines contain no note text); record wall
   time, peak RSS, disk delta, FTS engine and FTS build time in `runs/benchmarks.jsonl`; append the
   `> **Completion note**` to `EP-148-text-notes-staging.md`; count-pin tests (rows per table vs the
   notes manifest, `note_id` uniqueness, subject/hadm coverage vs core 3.1 as suppressed shares).
2. **Search API** (`src/mimicwarehouse/text/search.py`) — `search_notes(query, *, note_type=None,
   section=None, cohort_id=None, k=11)` runs BM25 (`match_bm25`) or FTS5 per the EP-148 engine and
   returns a `SearchSummary`: total hits, hits by `note_type`, by section, by `anchor_year_group`
   (owner-role attach of the core catalog joined on `subject_id`), by membership of a cohort id
   (EP-47) when given — every count through `disclose.suppress` (EP-43); **no text, no ids**.
   `text/search.py` and `text/sections.py` call `text.guard.ensure_local_only()` at import. An
   owner-only `owner_hits()` path (mirrors `safe.owner_rows`, audited) returns note ids for the app.
   Every search writes an audit line (EP-30) and a run record (EP-35).
3. **Sectioning** (`text/sections.py`, rules in `text/sections.yaml`) — regex header rules mapping
   shipped headers to canonical names (discharge: `chief_complaint`, `hpi`, `past_medical_history`,
   `physical_exam`, `pertinent_results`, `brief_hospital_course`, `discharge_diagnosis`,
   `discharge_medications`, `discharge_condition`, `discharge_instructions`; radiology:
   `examination`, `indication`, `technique`, `comparison`, `findings`, `impression`); output
   `notes.derived.note_sections (note_id, section_name, ordinal, char_start, char_end)` — offsets
   only — materialised under `notes\lake\derived\note_sections\` by
   `uv run --group text mwh build --tier full --dag notes-sections --with-notes` (background job,
   log `%MWH_DATA_ROOT%\runs\jobs\notes-sections.log`); section coverage (share of notes with each
   canonical section, by `note_type`) as a suppressed aggregate. Section-scoped search = FTS over a
   `note_sections_text` view (or a second index for the heaviest sections — measure at re-plan).
4. **CLI** — `uv run --group dev mwh text search "<query>" --with-notes [--note-type DS|RR]
   [--section impression]` prints the suppressed summary table only; refuses when the actor is not
   `owner` or notes are not attached; there is no flag that prints text.
5. **Tests on the fixture notes** (`tests/ep/test_ep149.py`) — planted terms → expected hit counts;
   sectioner recovers the synthetic notes' known sections (≥ 0.95 exact-boundary agreement);
   k-suppression applied; a crafted `owner_hits()` call under the agent actor is **refused**.

## Out of scope

- App page → EP-154 (Text pages in app (search only)).
- Concept extraction → EP-150; embeddings / semantic search → EP-151.
- ML section classifiers → Parked below.

## Verification / acceptance (sketch)

- `uv run poe test -m ep_149` green on fixture; `uv run --group dev mwh verify EP-149` green;
  import with `MWH_ALLOW_REMOTE=true` is refused (test).
- EP-148 completion note written with timing / peak RSS / disk; benchmark ledger rows exist.
- `notes-sections` full-tier build launched as a logged background job; run id and docs/s recorded
  in this brief's completion note (EP-150 records them if the job outlives the session).
- One full-tier search summary (e.g. "septic shock" by `note_type` × `anchor_year_group`) saved
  under `runs/<run_id>/tables/` passes `uv run --group dev mwh disclose check`.
- One full-tier BM25 count query latency recorded (≤ 5 s target, D-28).

## Parked → final-roadmap.md

- medspaCy `Sectionizer` / trained clinical section classifiers — trigger: regex rules find a
  `brief_hospital_course` section in < 90 % of discharge summaries.
- Hybrid BM25 + embedding retrieval — trigger: after EP-151, if keyword search misses paraphrases
  the capstone needs.
