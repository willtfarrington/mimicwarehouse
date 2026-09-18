<picture>
  <source media="(prefers-color-scheme: dark)" srcset="mimicwarehouse/docs/brand/banner-dark.svg">
  <img alt="mimicwarehouse — a local MIMIC-IV data lab — DuckDB · Polars · Streamlit" src="mimicwarehouse/docs/brand/banner-light.svg" width="100%">
</picture>

# mimicwarehouse
local EMR data warehouse — MIMIC-IV · DuckDB · Polars · Streamlit (MIT-licensed code; data not included)

# Project status (2026-09-18)

**Work in progress — public as a governed, in-flight project.** `mimicwarehouse` is a local,
single-machine data lab over MIMIC-IV 3.1 (hosp + icu), MIMIC-IV-ED 2.2 and MIMIC-IV-Note 2.2
— a DuckDB + Parquet warehouse with a Python backend and a Streamlit "Lab" app for exploratory
analysis/visualization **and** prospective-style, protocol-frozen inquiry over retrospective
data, with end-to-end provenance and disclosure discipline. The v1 roadmap is 174
self-contained session briefs across 12 phases; one tested end-to-end representative workflow
per capability category (38) is the completion bar, and everything named-but-not-built is
parked in the extension roadmap.

**Where it stands (2026-09-18): 63 of 174 briefs done — phases P0 … P3 complete** (P0–P2
consolidated twice — the 2026-08-18 retrospective and the EP-33 re-plan; P3 closed by the
EP-54 re-plan, record in [roadmap/retro-p3.md](roadmap/retro-p3.md)). Shipped: the
uv/CPython 3.13 toolchain with DuckDB 1.5.5 pinned; the `mwh` CLI — `doctor`, `paths`,
`guard`, `verify`, `schema`, `inventory`, `fixtures`, `canary`, `build`, `jobs`, `catalog`,
`sql`, `demo`, `runs`, `tracer` from P0–P2 and `units`, `codeset`, `phenotype`, `disclose`,
`qc`, `cohort`, `timeline`, `spine`, `protocol`, `backup` from P3; the pre-commit data-leak
guard, repo-shared Claude Code deny rules and a live PreToolUse session guard; the YAML
schema contract for all four datasets and a deterministic **synthetic** fixture generator
(ids ≥ 90 000 000); the typed CSV → Parquet loader, the `mwh build` DAG runner with detached
background jobs and the **complete core lake — all 31 hosp + icu tables staged full-tier
(886 M rows, 7.0 GB Parquet from 97 GB of CSV)** with per-tier DuckDB catalogs, a generated
data dictionary and a benchmark ledger; the `safe_query` gate (read-only, aggregate-only,
k = 11 complementary suppression, audited) that is the only way an AI session touches the
data; and, from P3, **the derived layer** — time semantics and the unit-of-analysis
registry, a provenance run ledger with seeds and resource logs, the 65 vendored mimic-code
concepts executed as-is on DuckDB 1.5.5 behind a patch registry (95.8 M rows), a curated
item catalogue with unit harmonisation, versioned code sets with the ICD-9↔10 GEM utility,
four versioned phenotypes (T2DM, sepsis-3, KDIGO AKI, explicit sepsis) pinned to the
concept SQL they read, the disclosure primitives (complementary suppression, the artefact
checker, `.disclosure.json` sidecars), data-quality and measurement-process registries
(574 checks, no failure), a cohort spec → deterministic CTE compiler → materialised marts
with suppressed attrition and STROBE-style diagrams, an event-aligned timeline API, a
MEDS-shaped events spine (280 M events, 13 sources, 0.81 GiB), a protocol freeze registry
by content hash, a backup of the non-reproducible state, and the first capstone case
study promoted through the gate ([docs/analyses](mimicwarehouse/docs/analyses/README.md)).
The fixture-tier suite (`poe check`, 1,183 tests) and `roadmap_check --strict` are green at
every ☑ commit. What exists, in one page:
[mimicwarehouse/README.md § State of the workspace](mimicwarehouse/README.md#state-of-the-workspace)
(the living status surface, D-43; refreshed at every re-plan EP). Next: P4 — a debt-sweep
brief first (EP-173, the carried-low fixes led by a safe-query hardening), then the latency
marts (EP-55/56) and the Streamlit Lab app (EP-57 …): catalog & QC browser, cohort builder,
phenotype studio, the linked-brush Explorer, the patient-safe timeline viewer, rates,
subgroups, Table 1 and missingness views, closed by the EDA capstone (EP-73). The roadmap
tables in [roadmap/README.md](roadmap/README.md) are the source of truth for what is and
isn't built.

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
  settled with the owner on 2026-08-16, D-42 … D-47 and dated addenda from execution
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
