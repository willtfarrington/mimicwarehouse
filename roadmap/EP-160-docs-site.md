# EP-160 — Docs site (MkDocs Material)

**Size:** M · **Tier:** n/a · **Core/Stretch:** core · **Depends on:** EP-157 (Docs refresh (README/DESIGN/GOVERNANCE/DECISIONS; two reading paths)) · **Blocks:** EP-163 (final-roadmap.md compilation + release v1.0.0 + final retro)

> **Charter.** This is a charter brief (D-9): scope sketch and acceptance sketch to be upgraded to a
> full brief by EP-147 (Re-plan P9, which writes full P10/P11) before execution; EP-136 (Re-plan P8)
> re-charters it first.

## Context

The docs site is the second leg of democratization (D-12) and the vehicle for the two reading paths
(D-1). It renders what already exists — the refreshed governing documents (EP-157), the generated
`DATA-DICTIONARY.md` (EP-29), `docs/resources/` (P1), `docs/analyses/` case studies (capstones,
compiled by EP-161), `docs/getting-started.md` (EP-158), `docs/demo-mode.md` (EP-159), screenshots
(EP-162), the roadmap and `final-roadmap.md` — with MkDocs Material (MIT, D-34) themed from `theme.py`
(D-11). Governance: only images/tables that carry a `.disclosure.json` sidecar may appear under `docs/`
(D-40, GOVERNANCE §7), so the build itself enforces that. Publishing (`mkdocs gh-deploy`) waits for the
public flip at v1.0.0 (D-41) → EP-163. Docs-only (tier `n/a`); no data access.

## Scope sketch (refine at re-plan)

1. **`mimicwarehouse/mkdocs.yml` + `docs` dependency group** (`mkdocs`, `mkdocs-material`,
   `pymdown-extensions`, `mkdocstrings[python]`; a DECISIONS addendum records the new group beside
   core/dev/ui/gpu/gpl/text); commands `uv run --group docs mkdocs serve -a 127.0.0.1:8000` and
   `uv run --group docs mkdocs build --strict` (output `site/` gitignored), wrapped as poe tasks
   `docs-serve` / `docs-build`.
2. **Single-source hook `docs/_hooks/sync_sources.py`** (MkDocs `hooks:`) — at build time copies the
   root `README.md`, `DESIGN.md`, `GOVERNANCE.md`, `DECISIONS.md`, `CLAUDE.md`, `DATA-DICTIONARY.md`,
   `roadmap/README.md`, `roadmap/final-roadmap.md` into `docs/_generated/` (gitignored), rewriting
   relative links; nothing is hand-copied, so the site cannot drift from the documents.
3. **Nav** — Home (the two reading paths) · Getting started · Demo mode · Governance · Architecture ·
   Decisions · Data dictionary · Capabilities (the 38-row coverage table generated from `roadmap/README.md`,
   linking briefs and case studies) · Case studies (`docs/analyses/README.md` index) · Resources ·
   Screenshots · Roadmap + final roadmap · API reference (mkdocstrings over the public modules: `safe`,
   `cohort`, `protocol`, `disclose`, `run`, `report`).
4. **Theme** — Material with the EP-5 light/dark palette in `docs/stylesheets/extra.css`, banner/logo and
   favicon from `docs/assets/`; Mermaid via `pymdownx.superfences` so attrition diagrams (EP-48) render.
5. **Disclosure guard hook `docs/_hooks/disclosure_guard.py`** — fails the build if any image, table,
   HTML or Vega spec under `docs/` (outside `_generated/`) lacks a valid sidecar or fails
   `mimicwarehouse.disclose.check`; calls the EP-43 module, never re-implements it.
6. **Tests `tests/ep/test_ep160.py`** (`@pytest.mark.ep_160`) — `mkdocs build --strict` into a temp dir
   succeeds; nav contains the required sections; a planted PNG without a sidecar fails the build;
   `_generated/` and `site/` are gitignored (checked via `git check-ignore`).

## Out of scope

- Writing the content itself → EP-157 (docs), EP-161 (case studies), EP-162 (screenshots, one-pager).
- Publishing to GitHub Pages / `mkdocs gh-deploy` → EP-163, after the repo is public.
- Quarto site, DuckDB-WASM public aggregate site → `final-roadmap.md` (already listed).

## Verification / acceptance (sketch)

- `uv run --group docs mkdocs build --strict` green; `uv run poe test -m ep_160` and
  `uv run --group dev mwh verify EP-160` green.
- `uv run --group docs mkdocs serve -a 127.0.0.1:8000` opens with light and dark palettes; every nav
  section resolves; the reading-path links from EP-157 resolve inside the site.
- The disclosure-guard test refuses a planted un-sidecarred image (governance-flavoured check).
- DECISIONS addendum for the `docs` group recorded; `site/` and `docs/_generated/` never committed.

## Parked → final-roadmap.md

- Versioned docs (`mike`) once v2 starts · Quarto narrative lane and DuckDB-WASM aggregate site (already
  under 33 / 32).
