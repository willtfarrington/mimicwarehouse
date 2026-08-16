# EP-60 — Screenshot tooling

**Size:** S · **Tier:** demo · **Core/Stretch:** core · **Depends on:** EP-57 (App shell A (Streamlit multipage)) · **Blocks:** EP-73 (Capstone #2: EDA case study + screenshots), EP-162 (Executive one-pager + demo script + screenshots)

## Context

Screenshots may enter `docs/` or git only from the `demo` (ODbL MIMIC-IV Demo 2.2, EP-22) or
`fixture` tiers and, like every promoted artifact, need a `.disclosure.json` sidecar (GOVERNANCE
§3, §6, D-40). Consistent screenshots also depend on the EP-5 identity (light + dark). This brief
builds a repeatable, headless capture path over the EP-57 shell so every P4 page EP adds one
manifest line and the capstone/one-pager regenerate everything with one command. Playwright
(Apache-2.0) drives Chromium; its browser download lands in `%LOCALAPPDATA%\ms-playwright` (user
cache, not system software) — the owner runs the install once. The demo tier has 100 subjects, so
many cells are small; that is fine (redistributable data), and the app shows no small-cell badge
on demo. Demo = v2.2 schema mapped to 3.1 by EP-22; no notes.

## In scope

1. **Dependency** — add `playwright` to the `ui` group; document the one-time
   `uv run --group ui playwright install chromium` (owner runs it; the tool prints this hint when
   Chromium is missing).
2. **Tool** (`src/mimicwarehouse/ui/screenshot.py`, `mwh app screenshot --tier demo
   [--pages id,…] [--theme light|dark|both] [--out docs/screenshots] [--port 8599]`) — refuses
   any tier other than `demo`/`fixture` (hard-coded; error names GOVERNANCE §6); starts
   `mwh app --tier <t> --port <port> --headless` as a subprocess (spawn-safe, `__main__` guard)
   once per theme (`--theme.base light|dark` via env → EP-57 config), waits for
   `http://127.0.0.1:<port>/_stcore/health`; for each manifest entry navigates to the page
   route, optionally runs `actions` (`click: <text>`, `select: <label>=<value>`,
   `wait_for: <text>`), captures a full-page PNG at `viewport` (default 1440×900) to
   `<out>/<id>-<theme>.png`; writes a sidecar per PNG via `disclose.write_sidecar(path,
   checks={"tier": <t>, "source": "app-screenshot", "page": id, "theme": theme,
   "git_sha": …})`; stops the server. Extend `disclose.check` (EP-43) so images pass only when
   their sidecar declares tier ∈ {demo, fixture} (if that rule is missing).
3. **Manifest + convention** — `docs/screenshots/manifest.yaml` (`id`, `page` (registry id),
   `route`, `viewport`, `actions`) seeded with `home`; `docs/screenshots/README.md`: naming
   (`<id>-<theme>.png`), demo-tier-only rule, regeneration command, how a page EP adds its
   entry, alt-text list for docs, max width guidance for README embeds.
4. **Tests** `tests/ep/test_ep60.py` (`@pytest.mark.ep_60`, ui group): `--tier dev` raises
   before any subprocess starts; manifest schema validates; `pytest.importorskip("playwright")`
   + skip when Chromium is absent → smoke capture of `home` on the fixture tier into a temp dir
   produces PNG + sidecar and `disclose.check` passes; nothing is written outside `--out`.

## Out of scope

- Page-specific screenshots → each page EP (one manifest line each); the full set → EP-73.
- Executive one-pager / demo script → EP-162; demo mode features → EP-159.
- Video/GIF capture → parked below.

## Verification / acceptance

- `uv run --group ui poe test -m ep_60` green; `uv run --group ui mwh verify EP-60` green.
- `uv run --group ui mwh app screenshot --tier demo` produces `docs/screenshots/home-light.png`,
  `home-dark.png` and their `.disclosure.json` sidecars; `uv run --group dev mwh disclose check
  docs/screenshots` passes; PNGs + sidecars + README + manifest committed.
- The tool **refuses** `--tier dev` and `--tier full` (test + manual).

## Parked → final-roadmap.md

- Animated GIF/video walkthrough capture — trigger: EP-162 demo script wants motion.
