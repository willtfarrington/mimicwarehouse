<picture>
  <source media="(prefers-color-scheme: dark)" srcset="mimicwarehouse/docs/brand/banner-dark.svg">
  <img alt="mimicwarehouse — a local MIMIC-IV data lab — DuckDB · Polars · Streamlit" src="mimicwarehouse/docs/brand/banner-light.svg" width="100%">
</picture>

# mimicwarehouse
local emr data warehouse v1.0.0 - mimic-iv

# Project status (2026-08-16)

**Planning complete; no code yet.** `mimicwarehouse` is a local, single-machine data lab
over MIMIC-IV 3.1 (hosp + icu), MIMIC-IV-ED 2.2 and MIMIC-IV-Note 2.2 — a DuckDB +
Parquet warehouse with a Python backend and a Streamlit "Lab" app for exploratory
analysis/visualization **and** prospective-style, protocol-frozen inquiry over
retrospective data, with end-to-end provenance and disclosure discipline. The initial
roadmap (v1, "pre-employment") is 164 self-contained session briefs across 12 phases;
one tested end-to-end representative workflow per capability category (38) is the
completion bar, and everything named-but-not-built is parked in the extension roadmap.

The data is **not** in this repository and never will be (PhysioNet credentialed
license; ~98 GB). See `source material/README.md`.

Baseline committed (EP-0, 2026-08-17); toolchain bootstrapped (EP-1, 2026-08-17: uv 0.12.5,
uv-managed CPython 3.13.15, `mimicwarehouse/pyproject.toml` + `uv.lock`, DuckDB 1.5.5 pinned,
pytest/ruff/pyright green) — see [mimicwarehouse/README.md § Install](mimicwarehouse/README.md#install-ep-1);
`mwh` CLI arrives with EP-2.

- **Code:** [mimicwarehouse/README.md](mimicwarehouse/README.md) — the Python (uv)
  workspace; planned quick start `uv sync --group dev && uv run mwh doctor`.
- **Start here (design):** [mimicwarehouse/DESIGN.md](mimicwarehouse/DESIGN.md) —
  layers, tiers, engine, catalogs, cohort/phenotype/protocol specs, run ledger, safe-query,
  app structure, module map (each module tagged with the EP that builds it).
- **Governance:** [mimicwarehouse/GOVERNANCE.md](mimicwarehouse/GOVERNANCE.md) — the
  license/PHI/LLM/small-cell/export contract every session must read first; session
  rules for Claude Code in [CLAUDE.md](CLAUDE.md).
- **Decisions:** [mimicwarehouse/DECISIONS.md](mimicwarehouse/DECISIONS.md) — D-1 … D-41
  settled with the owner on 2026-08-16, plus assumed defaults and judgment calls.
- **Roadmap:** [roadmap/README.md](roadmap/README.md) — phase tables with ☑ commit
  hashes, capability coverage, risks; briefs `roadmap/EP-<n>-*.md`; extension roadmap
  [roadmap/final-roadmap.md](roadmap/final-roadmap.md).
- **Source material:** [source material/README.md](source%20material/README.md) — the
  three PhysioNet datasets, how to obtain them, expected local layout, handling
  obligations, citations.
