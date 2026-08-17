# EP-5 — Visual identity

**Size:** S · **Tier:** n/a · **Core/Stretch:** core · **Depends on:** EP-1 (Toolchain bootstrap (uv + CPython 3.13 + pyproject)) · **Blocks:** EP-7 (Re-plan P0), EP-57 (App shell A (Streamlit multipage))

## Context

D-11 asks for the visual identity *early* — one S brief so every later chart, page and screenshot
(P4 app, capstones, docs site, one-pager) is consistent from the first pixel: a wordmark, a light
and a dark chart-safe palette, an Altair theme and a Streamlit theme config, and a README banner.
D-21 makes Altair/Vega-Lite the primary chart layer (Plotly only for timelines), so the theme is an
Altair theme registered from `mimicwarehouse.theme` (DESIGN §15) that report generation (P8) reuses.
Governance shapes it too: suppressed small cells (D-33) need a dedicated colour, and screenshots
are only ever taken on the demo/fixture tiers. Everything is code plus hand-written SVG — no
raster assets, no embedded or web fonts, no external requests. Commands run in `mimicwarehouse/`.

## In scope

1. **`src/mimicwarehouse/theme.py`** — a frozen `Palette` dataclass with a `LIGHT` and a `DARK`
   instance: `background`, `surface`, `text`, `muted`, `grid`, `primary`, `accent`, `positive`,
   `warning`, `danger`, `suppressed` (grey used wherever a cell is suppressed or a warn badge is
   shown), `categorical` (8 colours: the Okabe–Ito set ordered blue, orange, bluish-green,
   vermilion, sky blue, reddish-purple, grey, yellow — colour-blind safe on both backgrounds; yellow
   last so it appears only at ≥ 8 categories), `sequential = "viridis"` and
   `diverging = "blueorange"` (Vega built-in scheme names). Defaults for the brand tokens (a session
   may tune, the tests fix the constraints): light `background #FFFFFF`, `surface #F5F7F9`,
   `text #1B1F23`, `muted #5F6B76`, `grid #E3E7EB`; dark `background #0F1419`, `surface #1A2027`,
   `text #E6EDF3`, `muted #9AA4AE`, `grid #2A323B`; both: `primary #1F6F8B`, `accent #E07A5F`,
   `positive #2E8B57`, `warning #B8860B`, `danger #B23A48`, `suppressed #9AA0A6`.
   Functions: `altair_theme(mode) -> dict` (Vega-Lite `config`: fonts = system stack, axis/legend/
   title colours from the palette, `range.category` = categorical, `range.heatmap` = sequential,
   `range.diverging`, `view.stroke` transparent, `mark` defaults), `register_altair()` registering
   `mwh_light` and `mwh_dark` with the Altair theme API of the pinned version, `enable_altair(mode)`,
   `streamlit_theme(mode) -> dict` (`base`, `primaryColor`, `backgroundColor`,
   `secondaryBackgroundColor`, `textColor`, `font`), and `contrast_ratio(hex_a, hex_b)` (WCAG 2.x).
   No plotly / streamlit imports at module level (both live in the `ui` group).
2. **Streamlit theme config** — `mimicwarehouse/.streamlit/config.toml` (`[theme]` from
   `streamlit_theme("light")`, plus `[server] address = "127.0.0.1"`, `headless = true`,
   `[browser] gatherUsageStats = false`), written by `python -m mimicwarehouse.theme --write`
   (an `if __name__ == "__main__":` entry in `theme.py` that also emits the SVGs of item 3) so the
   files and the palette cannot drift; tracked in git (`.streamlit/secrets.toml` stays ignored).
3. **Wordmark and banner (SVG from string templates in `theme.py`, text-based, system font
   stack):** `docs/brand/wordmark-light.svg`, `docs/brand/wordmark-dark.svg` (glyph = three
   stacked bars for raw → lake → marts in `primary` / `accent` / `muted` + the word
   "mimicwarehouse"), `docs/brand/banner-light.svg` and `banner-dark.svg` (1200 × 240, wordmark +
   tagline "a local MIMIC-IV data lab — DuckDB · Polars · Streamlit"), each ≤ 8 KB, colours taken
   from the matching `Palette`. Root `README.md` gets the banner at the top via `<picture>` with a
   `prefers-color-scheme: dark` source (paths under `mimicwarehouse/docs/brand/`); nothing else in
   the README changes.
4. **`docs/brand/README.md`** — palette table (token · light hex · dark hex · use), the categorical
   swatch order, rules: charts take colours only from `theme.Palette` (never literal hex in pages or
   reports), suppressed cells and warn badges use `suppressed` / `warning`, screenshots use the
   light theme unless the doc is dark, demo/fixture tiers only; how to enable the theme
   (`enable_altair("light")`; the app shell EP-57 does it once).
5. **Tests `tests/ep/test_ep05.py`** (`@pytest.mark.ep_5`): every text/background and
   muted/background pair in both palettes has contrast ≥ 4.5; the 8 categorical colours are valid
   hex, pairwise distinct and none equals either background; `register_altair()` then
   `enable_altair("light")` and a tiny synthetic `alt.Chart(...).mark_bar().to_dict()` carries the
   palette in `config`; `streamlit_theme("light")` values equal `.streamlit/config.toml` (parsed
   with `tomllib`) and the four SVGs equal a fresh render from the templates — the drift tests;
   the SVGs parse as XML and reference no external URL (`href="http`, `@import`, `url(` absent).

## Out of scope

- The Streamlit app shell and theme wiring at runtime → EP-57; screenshot tooling → EP-60.
- Plotly template for lane/Gantt timelines → EP-67 (must read `theme.Palette`).
- Report CSS / Typst styles → EP-130 / EP-131 (same tokens); docs-site theme → EP-160.
- Raster logos, favicons, web fonts → parked below.

## Verification / acceptance

- Files exist at exactly: `src/mimicwarehouse/theme.py`, `mimicwarehouse/.streamlit/config.toml`,
  `docs/brand/{wordmark-light,wordmark-dark,banner-light,banner-dark}.svg`, `docs/brand/README.md`.
- `uv run poe test -m ep_5` green; `uv run poe lint` and `typecheck` green;
  `uv run --group dev python -m mimicwarehouse.theme --write` is idempotent (`git status` clean
  after a second run); `mwh verify EP-5` (from EP-6) green when EP-6 runs it.
- Root `README.md` renders the banner in light and dark GitHub themes (checked in the repo web
  view or a Markdown preview); SVGs display in a browser without network access.

## Parked → final-roadmap.md

- Raster logo set (PNG/ICO favicon, social-preview image) and a self-hosted brand font — trigger:
  the docs site (EP-160) or a public release asset needs them; hazard: binary assets in git.

> **Completion note (2026-08-17).** Delivered as briefed; `uv run poe check` green (ruff, pyright
> basic, 147 tests of which 34 are `ep_5`), `python -m mimicwarehouse.theme --write` idempotent
> (second run: "0 file(s) written; 5 unchanged", `git status` clean), `mwh guard` clean on every
> new file.
> - `src/mimicwarehouse/theme.py`: frozen `Palette` (`mode` + the 11 tokens + `categorical` /
>   `sequential` / `diverging`; validated in `__post_init__`), `LIGHT` / `DARK` / `PALETTES` /
>   `palette(mode)`, brief defaults kept unchanged; `contrast_ratio` (+ `relative_luminance`):
>   text/background 16.6 · 15.7, muted/background 5.5 · 7.3, muted/surface 5.1 · 6.5 (light · dark).
>   `altair_theme(mode)` returns the Altair *ThemeConfig* shape `{"config": <Vega-Lite config>}`
>   (background, system font stack, axis/legend/title/header colours, `range.category` =
>   Okabe-Ito, `range.heatmap`/`ordinal`/`ramp` = viridis, `range.diverging` = blueorange,
>   `view.stroke` transparent, mark defaults in `primary`); `register_altair()` uses the Altair
>   6.2.2 `alt.theme.register(name, enable=False)` decorator (idempotent, returns
>   `("mwh_light", "mwh_dark")`); `enable_altair(mode)`; `streamlit_theme(mode)`
>   (`font = "sans serif"` — Streamlit accepts only generic families); `streamlit_config_toml`,
>   `wordmark_svg` / `banner_svg`, `render_assets` / `check_assets` / `write_assets`, and a
>   `__main__` with `--write` **and** `--check` (exit 1 on drift; EP-6's `mwh verify EP-5` can
>   call `theme.check_assets()` directly). altair is imported lazily inside the two functions
>   that need it (import budget); no plotly/streamlit anywhere.
> - Generated, tracked: `.streamlit/config.toml` (396 B), `docs/brand/wordmark-{light,dark}.svg`
>   (~0.8 KB, 320 × 56, transparent) and `banner-{light,dark}.svg` (~1.4 KB, 1200 × 240, palette
>   background + `grid` hairline). Glyph = three stacked bars raw (widest, `primary`) → lake
>   (`accent`) → marts (`muted`), rendered from `_glyph()`; the same glyph at two scales.
>   Rendered in headless Edge (scratchpad only, no PNG committed) — both banners and wordmarks
>   display correctly on their backgrounds.
> - Root `README.md`: `<picture>` block with the dark `srcset` above the title; nothing else
>   changed. `docs/brand/README.md`: palette table, categorical order, contrast, rules, enabling.
>   Workspace `README.md` gained one sentence + the `docs/brand/` layout line.
> - Tests (`tests/ep/test_ep05.py`, 34): contrast ≥ 4.5 for text/muted × background/surface in
>   both modes; categorical = Okabe-Ito order, valid hex, pairwise distinct, ≠ either background;
>   `register_altair()` + `enable_altair()` put the palette into a synthetic `mark_bar()` spec's
>   `config` (dark too; the previously active theme is restored); `.streamlit/config.toml`
>   parsed with `tomllib` equals `streamlit_theme("light")` and the rendered TOML; SVG drift vs
>   fresh render (byte-equal), XML parse, ≤ 8 KB, no `href="http` / `@import` / `url(` /
>   `<script` / `<image` / `<foreignObject`, LF only; `write_assets` idempotence + `--check`
>   exit codes in `tmp_path`; brand README lists every palette hex; root README banner block;
>   no ui imports at module level (AST).
> - Parked item mirrored into `final-roadmap.md` (Cross-cutting, `v2 BRAND-1`).
> - Nothing was full-tier; no data touched.
