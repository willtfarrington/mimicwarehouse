<picture>
  <source media="(prefers-color-scheme: dark)" srcset="mimicwarehouse/docs/brand/banner-dark.svg">
  <img alt="mimicwarehouse — a local MIMIC-IV data lab — DuckDB · Polars · Streamlit" src="mimicwarehouse/docs/brand/banner-light.svg" width="100%">
</picture>

# mimicwarehouse
local EMR data warehouse — MIMIC-IV · DuckDB · Polars · Streamlit (MIT-licensed code; data not included)

# Project status (2026-09-01)

**Work in progress — public as a governed, in-flight project.** `mimicwarehouse` is a local,
single-machine data lab over MIMIC-IV 3.1 (hosp + icu), MIMIC-IV-ED 2.2 and MIMIC-IV-Note 2.2
— a DuckDB + Parquet warehouse with a Python backend and a Streamlit "Lab" app for exploratory
analysis/visualization **and** prospective-style, protocol-frozen inquiry over retrospective
data, with end-to-end provenance and disclosure discipline. The v1 roadmap is 172
self-contained session briefs across 12 phases; one tested end-to-end representative workflow
per capability category (38) is the completion bar, and everything named-but-not-built is
parked in the extension roadmap.

**Where it stands (2026-09-01): 42 of 172 briefs done — phases P0, P1 and P2 complete,
consolidated twice (the 2026-08-18 retrospective, EP-165 … EP-171; the EP-33 P0–P2
consolidation re-plan).** Shipped: uv/CPython 3.13 toolchain with DuckDB 1.5.5 pinned; the
`mwh` CLI (`doctor`, `paths`, `guard`, `verify`, `schema`, `inventory`, `fixtures`, `canary`,
`build`, `jobs`, `catalog`, `sql`, `demo`, `runs`, `tracer`); the pre-commit data-leak guard,
repo-shared Claude Code deny rules and a live PreToolUse session guard; the YAML schema
contract for all four datasets and a deterministic **synthetic** fixture generator (ids
≥ 90 000 000); the typed CSV → Parquet loader with subject buckets and resume, the `mwh
build` DAG runner with detached background jobs, and the **complete core lake — all 31
hosp + icu tables staged full-tier (886 M rows, 7.0 GB Parquet from 97 GB of CSV) with
per-tier DuckDB catalogs, a generated data dictionary and a benchmark ledger**; the
`safe_query` gate (read-only, aggregate-only, k = 11 suppression, audited) that is the
only way an AI session touches the data; and the first end-to-end analysis (the EP-31
tracer bullet: first-ICU-stay adults → in-hospital mortality). EP-33 left one publish
primitive, one connection factory, one JSONL canon and one CLI error convention behind
the P3–P11 briefs. The fixture-tier suite (`poe check`) and `roadmap_check --strict` are
green at every ☑ commit. What exists, in one page:
[mimicwarehouse/README.md § State of the workspace](mimicwarehouse/README.md#state-of-the-workspace)
(the living status surface, D-43; refreshed at every re-plan EP). Next: P3 — time
semantics and the run ledger (EP-34/35), the mimic-code concept runner (EP-37/38),
code sets and phenotypes, disclosure primitives, the cohort engine and protocol freeze.
The roadmap tables in [roadmap/README.md](roadmap/README.md) are the source of truth
for what is and isn't built.

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
  settled with the owner on 2026-08-16, D-42 … D-45 and dated addenda from execution
  (with a status index since EP-33), plus assumed defaults and judgment calls.
- **Roadmap:** [roadmap/README.md](roadmap/README.md) — phase tables with ☑ commit
  hashes, capability coverage, risks; briefs `roadmap/EP-<n>-*.md`; extension roadmap
  [roadmap/final-roadmap.md](roadmap/final-roadmap.md).
- **Source material:** [source material/README.md](source%20material/README.md) — the
  three PhysioNet datasets, how to obtain them, expected local layout, handling
  obligations, citations.
- **License:** code, docs and synthetic fixtures are MIT ([LICENSE](LICENSE)); third-party
  notices (vendored MIT-LCP/mimic-code) in [NOTICE](NOTICE); citation metadata in
  [CITATION.cff](CITATION.cff). The MIMIC-IV data are PhysioNet-licensed and are not part
  of this repository.
