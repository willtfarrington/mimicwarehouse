<picture>
  <source media="(prefers-color-scheme: dark)" srcset="mimicwarehouse/docs/brand/banner-dark.svg">
  <img alt="mimicwarehouse — a local MIMIC-IV data lab — DuckDB · Polars · Streamlit" src="mimicwarehouse/docs/brand/banner-light.svg" width="100%">
</picture>

# mimicwarehouse
local EMR data warehouse — MIMIC-IV · DuckDB · Polars · Streamlit (MIT-licensed code; data not included)

# Project status (2026-08-18)

**Work in progress — public as a governed, in-flight project.** `mimicwarehouse` is a local,
single-machine data lab over MIMIC-IV 3.1 (hosp + icu), MIMIC-IV-ED 2.2 and MIMIC-IV-Note 2.2
— a DuckDB + Parquet warehouse with a Python backend and a Streamlit "Lab" app for exploratory
analysis/visualization **and** prospective-style, protocol-frozen inquiry over retrospective
data, with end-to-end provenance and disclosure discipline. The v1 roadmap is 171
self-contained session briefs across 12 phases; one tested end-to-end representative workflow
per capability category (38) is the completion bar, and everything named-but-not-built is
parked in the extension roadmap.

**Where it stands (2026-08-18): 14 of 171 briefs done — phase P0 and the first half of P1.**
Shipped: uv/CPython 3.13 toolchain with DuckDB 1.5.5 pinned (EP-1); the `mwh` CLI with
`doctor`, `paths`, `guard`, `verify`, `schema`, `inventory`, `fixtures` (EP-2 … EP-12);
settings + data-root safety checks (EP-3); the pre-commit data-leak guard and repo-shared
Claude Code deny rules (EP-4, EP-164); visual identity (EP-5); `mwh verify` + roadmap
consistency checker (EP-6); vendored, pinned MIT-LCP/mimic-code concepts (EP-8); the YAML
schema contract for all four datasets (EP-9); a hash/row-count raw-inventory manifest
reconciled against upstream (EP-10); and a deterministic **synthetic** fixture generator
(hosp + icu, 31 tables, ids ≥ 90 000 000) with tiered pytest markers (EP-11/12). 394 fixture-tier
tests and `roadmap_check` are green at every ☑ commit. Loader, warehouse build, concepts,
cohorts, safe-query and the Lab app are next (P1b onward — see the roadmap). Development is
paused as of 2026-08-18 and resumes at EP-165; the roadmap tables in
[roadmap/README.md](roadmap/README.md) are the source of truth for what is and isn't built.

The data is **not** in this repository and never will be (PhysioNet credentialed
license; ~98 GB). See `source material/README.md`. Everything committed here — code, docs,
schema, fixtures, aggregate manifests — passed the repository's own history-wide guard sweep
before the repo was made public (GOVERNANCE §3, D-41 addendum).

- **Code:** [mimicwarehouse/README.md](mimicwarehouse/README.md) — the Python (uv)
  workspace; quick start `cd mimicwarehouse && uv sync --group dev && uv run mwh doctor`
  (no data needed for `doctor`, `guard`, `fixtures`, `schema` or the fixture-tier tests).
- **Start here (design):** [mimicwarehouse/DESIGN.md](mimicwarehouse/DESIGN.md) —
  layers, tiers, engine, catalogs, cohort/phenotype/protocol specs, run ledger, safe-query,
  app structure, module map (each module tagged with the EP that builds it).
- **Governance:** [mimicwarehouse/GOVERNANCE.md](mimicwarehouse/GOVERNANCE.md) — the
  license/PHI/LLM/small-cell/export contract every session must read first; session
  rules for Claude Code in [CLAUDE.md](CLAUDE.md).
- **Decisions:** [mimicwarehouse/DECISIONS.md](mimicwarehouse/DECISIONS.md) — D-1 … D-41
  settled with the owner on 2026-08-16, D-42/D-43 and dated addenda from execution, plus
  assumed defaults and judgment calls.
- **Roadmap:** [roadmap/README.md](roadmap/README.md) — phase tables with ☑ commit
  hashes, capability coverage, risks; briefs `roadmap/EP-<n>-*.md`; extension roadmap
  [roadmap/final-roadmap.md](roadmap/final-roadmap.md).
- **Source material:** [source material/README.md](source%20material/README.md) — the
  three PhysioNet datasets, how to obtain them, expected local layout, handling
  obligations, citations.
- **License:** code, docs and synthetic fixtures are MIT ([LICENSE](LICENSE)); third-party
  notices (vendored MIT-LCP/mimic-code) in [NOTICE](NOTICE). The MIMIC-IV data are
  PhysioNet-licensed and are not part of this repository.
