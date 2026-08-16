# EP-146 — Capstone #7

**Size:** M · **Tier:** fixture+dev+full · **Core/Stretch:** core · **Depends on:** EP-137 (Importer profiler + provenance/licensing register), EP-138 (Concept/unit mapping guide + mapping YAML), EP-139 (Key validation, join cardinality, linkage coverage), EP-140 (Linkage Wizard A (profile → map)), EP-141 (Linkage Wizard B (validate → coverage → commit)), EP-142 (ED ingestion via wizard → mimiciv_ed + ED concepts), EP-143 (Reference-table ingestion via wizard (ATC / Elixhauser / LOINC map)), EP-144 (ED-enabled workflow (ED triage → admission; time-to-antibiotics)) · **Blocks:** EP-147 (Re-plan P9 (writes full P10/P11))

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-136 (Re-plan P8) before execution.

## Context

Phase capstone (D-8): turn P9's work into a portfolio artifact for both audiences (D-1) — a case
study on additional-data ingestion & linkage (category 35) that shows the wizard on real ED data,
the reference-table branch, the coverage-by-era findings, and the ED-enabled analysis, with every
number reproducible from recorded run ids and every promoted artifact disclosure-checked (D-40).
Screenshots of the wizard are taken on the **demo tier** (ED Demo 2.2, ODbL — redistributable), never
on dev/full (GOVERNANCE §6). Follows the `docs/analyses/NN-slug.md` convention from EP-32 (Reproduction
block, "What it deliberately does not claim").

## Scope sketch (refine at re-plan)

1. **Demo-tier wizard pass** — run the ED Demo through all five wizard steps on `--tier demo`
   (fresh `ext/mimic_iv_ed_demo_2_2/`), capture screenshots with the EP-60 tooling (profile, map,
   coverage-by-era, commit log) → `docs/analyses/assets/` after `mwh disclose check` (EP-133 review
   tool for anything promoted).
2. **Case study** `docs/analyses/NN-linkage-ed.md` (next free NN): the wizard sequence, the ED
   coverage-by-era table and temporal-consistency share (from the EP-142 full-tier report), the LOINC
   map (and Elixhauser if built) coverage from EP-143, a summary of the EP-144 result with its
   associational label and caveats (ED 2011–2019, confounding by indication, retrospective),
   licensing register excerpt (`docs/resources/external-sources.md`), Reproduction block with run
   ids / snapshot ids / protocol hash, "What it deliberately does not claim".
3. **Benchmark & ledger reconciliation** — ED full-tier load timing, validation/coverage run wall
   times and disk deltas in `runs/benchmarks.jsonl`; `mwh runs refresh`; note the ED lake footprint
   against the DESIGN §3 disk budget.
4. **Roadmap/docs upkeep** — README capability table row 35 marked as covered with the concrete
   artifacts; DESIGN.md dated notes for `mwh link`, `edstay` grain and `ref` schema; any P9
   deviation recorded as a DECISIONS addendum; Parked items from EP-137–EP-145 collected for EP-147.
5. **Tests** `tests/ep/test_ep146.py` (`@pytest.mark.ep_146`): links in the case study resolve;
   every promoted asset has a `.disclosure.json` sidecar; the numbers quoted in the case study match
   the referenced run records (read via `mwh runs`).

## Out of scope

- New features or fixes beyond what the case study needs → EP-147 allocates follow-ups.
- P10/P11 planning → EP-147.

## Verification / acceptance (sketch)

- Named artifacts exist (`docs/analyses/NN-linkage-ed.md`, assets with sidecars,
  `docs/resources/external-sources.md`); links resolve; numbers reproduce from recorded run ids.
- `uv run poe test -m ep_146` and `uv run --group dev mwh verify EP-146` green (fixture + dev);
  full-tier run ids cited from EP-142/EP-143/EP-144 completion notes.
- Every screenshot originates from the demo tier and passes `mwh disclose check`.
