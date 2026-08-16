# EP-83 — Event-sequence / care-pathway analysis

**Size:** M · **Tier:** fixture+dev+full · **Core/Stretch:** core · **Depends on:** EP-50 (Events spine (MEDS-compatible) ⏱) · **Blocks:** EP-89 (Capstone #3)

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-74 (Re-plan P4) before execution.

## Context

Capability category 10 (*Event-sequence and care-pathway analysis*). Reads the MEDS-shaped events
spine (EP-50; its full-tier build is verified by EP-54 — confirm ☑ before starting) and the
`transfers` table to derive care-unit sequences, transition matrices, inter-event delays and
within-window co-occurrence. Sankey diagrams use Plotly (D-21: Plotly for lane/Gantt/Sankey), the
rest Altair via `viz/`. `transfers` includes Emergency Department rows for admitted patients, so
ED-arrival → ICU delays are computable before ED ingestion (EP-142); ED-specific pathways wait for
EP-144. Pathway counts are small-cell prone → `disclose.suppress` with complementary suppression
(D-33). Themes per D-5: ICU transfer pathways + culture/antibiotic co-occurrence.

## Scope sketch (refine at re-plan)

1. **`src/mimicwarehouse/stats/pathways.py`** — `sequences(unit="hadm", source="transfers"|
   "spine", codes=…, collapse_repeats=True, max_len=…)`, `top_pathways(k, min_n=11)`,
   `transition_matrix(order=1)` (counts + row-normalised probabilities, k-suppressed),
   `delays(from_event, to_event)` (median / IQR by stratum), `cooccurrence(a, b, window)` (rate
   and lag distribution), pathway-length and loop (return-to-unit) statistics.
2. **Figures** — transition heatmap (Altair spec in `viz/`) and Sankey of the top-k pathways
   (Plotly; link arrays hold suppressed counts only, so the HTML passes `disclose.check`).
3. **Representative workflow**: first-ICU-stay adult hospitalisations → care-unit pathway
   (ED → ICU type → step-down / ward → discharge disposition), top-15 pathways, first-order
   transition matrix, delay ED arrival → ICU admission, and co-occurrence of a microbiology culture
   with the first antibiotic within ± 24 h (spine codes for microbiology and emar) → Markdown
   report via EP-59 (claim type *exploratory*; retrospective statement).
4. **Provenance** — run record cites the spine snapshot id and `transfers` catalog snapshot.
5. **Tests** `tests/ep/test_ep83.py` (`@pytest.mark.ep_83`): fixture sequences known; matrix rows
   sum to 1; cells < 11 suppressed (complementary); Sankey spec contains no ids; delays ≥ 0;
   dev-tier run.

## Out of scope

- ICU returns / readmissions as outcomes → EP-84; treatment episodes → EP-86.
- Process mining / frequent-sequence mining (pm4py, PrefixSpan) → parked (PATH-1).
- ED triage-level pathways → EP-144; row-level timeline viewer → EP-67.

## Verification / acceptance (sketch)

- `uv run poe test -m ep_83` green on fixture + dev; `uv run --group dev mwh verify EP-83` green.
- Full-tier run over the spine as a logged background job (`uv run --group dev mwh build --tier
  full --select analysis.pathways_icu --background --job ep83-pathways`); run id, wall time and
  spine snapshot id in the completion note.
- Heatmap, Sankey HTML and report pass `mwh disclose check`.

## Parked → final-roadmap.md

- Markov / semi-Markov unit-transition models with sojourn times; pm4py / PrefixSpan (PATH-1).
