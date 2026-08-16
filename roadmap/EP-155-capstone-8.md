# EP-155 — Capstone #8

**Size:** M · **Tier:** fixture+full · **Core/Stretch:** stretch · **Depends on:** EP-148 (Notes staging ⏱ (segregated lake + notes.duckdb FTS)), EP-149 (Note search + sectioning), EP-150 (Concept extraction + negation/temporal context (medspaCy)), EP-151 (Embeddings (CPU-capable, GPU-accelerated) + similarity search), EP-152 (Topic discovery + classification), EP-153 (Linkage to structured events), EP-154 (Text pages in app (search only)) · **Blocks:** EP-156 (Re-plan P10)

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-147 (Re-plan P9) before execution (track gated at EP-127; re-chartered by EP-136).

## Context

Phase capstone (D-8) for the clinical-text track: one case study and one report artifact that
tell the representative workflow of D-3 end to end — search (EP-149) → concept/negation extraction
(EP-150) → linkage to structured events (EP-153) — with the embeddings/topics/classification
results (EP-151/152) as supporting sections, in the `docs/analyses/NN-slug.md` convention set by
EP-32 and using the report engine (EP-130/131/132) and disclosure-review tool (EP-133) built in
P8. Two reading paths (D-1). Nothing in the artifacts is note text; every number reproduces from
recorded run ids; every table, figure and screenshot carries a `.disclosure.json` sidecar (D-40).

## Scope sketch (refine at re-plan)

1. **Close the ledger** — confirm every P10 full-tier job finished (`notes-stage`, `notes-sections`,
   `notes-concepts`, `notes-embed`, `notes-topics`/`notes-classify`, `notes-link`); write any
   missing timing / peak RSS / disk rows into `runs/benchmarks.jsonl` and completion notes;
   assemble the track benchmark table (staging, FTS build and engine, extraction docs/s,
   embedding docs/s CPU vs GPU, lake + FTS + embeddings disk vs the 5–15 GB budget).
2. **Case study** `docs/analyses/08-clinical-text.md` (NN per the EP-32 sequence) — question,
   data (Note 2.2 vs core 3.1 coverage), methods, results (search summary, concept prevalence and
   negation rates, agreement tables with κ and CIs, ETT–ventilation alignment, topic overview,
   sepsis-3 recovery AUROC), claim types (**exploratory** for topics/classification,
   **associational** for agreement), the retrospective statement, "What it deliberately does not
   claim" (no validity of either source; no prospective claim; discharge summaries post-date
   outcomes; sample-based embeddings), and a Reproduction block listing run ids, snapshot ids
   (core and notes), rules hashes and model shas.
3. **Report artifact** — MD + HTML (+ PDF via Typst) under `reports/text-track/` built with the
   `report/` engine, provenance footer, claim-type labels; promoted only after
   `uv run --group dev mwh disclose check` (EP-133 UI or CLI) writes sidecars.
4. **Screenshots** — the EP-154 fixture screenshot and a benchmark chart (via `viz/`), each with a
   sidecar; no dev/full row views.
5. **Tests** (`tests/ep/test_ep155.py`) — artifacts exist at the named paths, sidecars present, run
   ids resolve in `runs.duckdb`, internal links resolve, no identifier columns or free text in any
   promoted table.

## Out of scope

- Roadmap bookkeeping, DECISIONS addenda, mirroring Parked items → EP-156 (Re-plan P10).
- Compilation into the P11 case-study set → EP-161 (Case studies compilation (3–5)).

## Verification / acceptance (sketch)

- Named artifacts exist; numbers reproduce from recorded run ids; links resolve
  (docs / capstone class); `uv run poe test -m ep_155` and `uv run --group dev mwh verify EP-155`
  green on fixture.
- Every promoted table / figure / screenshot passes `mwh disclose check` with a sidecar; the report
  states claim types and that MIMIC-IV analyses are retrospective.
- Benchmark table recorded in the ledger and reproduced in the case study.
