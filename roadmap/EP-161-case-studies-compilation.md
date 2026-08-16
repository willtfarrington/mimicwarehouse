# EP-161 — Case studies compilation (3–5)

**Size:** M · **Tier:** n/a · **Core/Stretch:** core · **Depends on:** EP-157 (Docs refresh (README/DESIGN/GOVERNANCE/DECISIONS; two reading paths)) · **Blocks:** EP-162 (Executive one-pager + demo script + screenshots), EP-163 (final-roadmap.md compilation + release v1.0.0 + final retro)

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-147 (Re-plan P9, which writes full P10/P11) before execution; EP-136 (Re-plan P8)
> re-charters it first.

## Context

Each capstone (#0 EP-32 staging benchmark, #1 EP-53 concepts/QC, #2 EP-73 EDA, #3 EP-89, #4 EP-100
survival/causal, #5 EP-126 ML signatures, #6 EP-135 prospective/reporting, #7 EP-146 linkage/ED,
#8 EP-155 text if the track shipped) left a case study under `docs/analyses/NN-slug.md` in the EP-32
convention (hupsim precedent: "What it deliberately does not claim" + Reproduction blocks). This brief
compiles the showcase set of 3–5 for both audiences (D-1): signature depth means the mortality signature
(EP-110) is always in (D-6). Every artifact carries a claim-type label, the retrospective statement and
a `.disclosure.json` sidecar (GOVERNANCE §7, D-40); every number reproduces from a recorded run id
(GOVERNANCE §12). Docs-only (tier `n/a`): no new analysis; full-tier numbers are cited from the ledger,
never re-run in the foreground; reproduction blocks are executed on dev (fixture where dev is not
meaningful).

## Scope sketch (refine at re-plan)

1. **Selection + index** — pick 3–5 by rule: capstone #5 (signature #1) always; ≥ 1 clinical-informatics-
   forward study (#1 concepts/QC or the cohort/phenotype path); ≥ 1 provenance/prospective (#6); ≥ 1
   linkage (#7) if shipped; ≥ 4 capability categories covered. Update `docs/analyses/README.md`: abstract,
   audience tag(s), claim type, categories, run ids, and a reading order per path (feeds EP-157/EP-160).
2. **Standardise each study** — sections: Question · Data & cohort (attrition diagram, EP-48) · Method ·
   Results · What it deliberately does not claim · Reproduction (run ids, snapshot ids, protocol hash where
   frozen, exact `uv run --group dev mwh …` commands) · Provenance footer (git sha, env hash) · claim-type
   label + "MIMIC-IV analyses are retrospective"; MIMIC caveats stated where they bite (per-patient date
   shift / `anchor_year_group` only, `dod` ~1 y horizon, ICD-9→10 switch, discharge-alive competing
   event, ages ≥ 89 = 91, ED 2.2 = 2011–2019).
3. **Numbers re-checked** — every table, figure and inline number matched against
   `uv run --group dev mwh runs show <run_id>` (aggregate-only); discrepancies fixed or footnoted; no
   number without a run id.
4. **Reproduction executed** — each Reproduction block run verbatim on dev (or fixture); artifacts
   regenerate; outcome and wall time in the completion note. Full-tier numbers cited from
   `runs/ledger.jsonl` only.
5. **Disclosure + PDF** — `uv run --group dev mwh disclose check docs/analyses` on every table, figure,
   HTML; PDFs via the EP-131 Typst path into `docs/analyses/pdf/` only after the check passes.
6. **Cross-links** — root README reading paths (EP-157) point at the set; docs site nav (EP-160) reads the
   index; `tests/ep/test_ep161.py` (`@pytest.mark.ep_161`) checks index ↔ files ↔ sidecars ↔ run ids.

## Out of scope

- Any new analysis or re-run at full tier (numbers come from recorded runs).
- One-pager, demo script, screenshots → EP-162; site build → EP-160; release → EP-163.
- STROBE / TRIPOD+AI checklists, Quarto narratives → `final-roadmap.md` (already under 33).

## Verification / acceptance (sketch)

- 3–5 case studies listed in `docs/analyses/README.md` with run ids; every run id resolves in the ledger
  (`mwh runs show`); `uv run poe test -m ep_161` and `uv run --group dev mwh verify EP-161` green.
- `mwh disclose check` clean over `docs/analyses/` (sidecar per artifact, no small cells, no identifiers).
- Reproduction blocks executed on dev/fixture with outcomes recorded in the completion note.
- Each study carries a claim-type label and the retrospective statement; links resolve.

## Parked → final-roadmap.md

- Archival of the case-study PDF bundle with a DOI (Zenodo) after the repo is public.
