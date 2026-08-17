# mimicwarehouse — brand & visual identity (EP-5, D-11)

The single source of truth is [`src/mimicwarehouse/theme.py`](../../src/mimicwarehouse/theme.py):
two frozen `Palette` instances (`LIGHT`, `DARK`), the Altair theme (`mwh_light` / `mwh_dark`),
the Streamlit theme, and the string templates that render the four SVGs in this folder and
`.streamlit/config.toml`. Everything here is code + hand-written SVG — no raster assets, no
embedded or web fonts, no external requests.

<p>
  <img src="wordmark-light.svg" alt="mimicwarehouse wordmark (light)" width="320">
</p>

| Asset | File | Use |
|---|---|---|
| Wordmark | `wordmark-light.svg` · `wordmark-dark.svg` (320 × 56, transparent) | app sidebar, docs site header, report title block |
| Banner | `banner-light.svg` · `banner-dark.svg` (1200 × 240) | root `README.md` (`<picture>` with a `prefers-color-scheme: dark` source), one-pager, social preview stand-in |

The glyph is three stacked bars — **raw** (widest, `primary`) → **lake** (`accent`) → **marts**
(narrowest, `muted`) — the three layers of the warehouse (DESIGN §3).

## Palette

| Token | Light | Dark | Use |
|---|---|---|---|
| `background` | `#FFFFFF` | `#0F1419` | page / chart background |
| `surface` | `#F5F7F9` | `#1A2027` | cards, sidebar, secondary background (Streamlit `secondaryBackgroundColor`) |
| `text` | `#1B1F23` | `#E6EDF3` | body text, axis titles, chart titles |
| `muted` | `#5F6B76` | `#9AA4AE` | secondary text, axis labels, legend labels, subtitles, the marts bar |
| `grid` | `#E3E7EB` | `#2A323B` | gridlines, axis domain, ticks, hairline borders |
| `primary` | `#1F6F8B` | `#1F6F8B` | brand teal: default mark colour, links, buttons (Streamlit `primaryColor`), the raw bar |
| `accent` | `#E07A5F` | `#E07A5F` | highlight / selection, the lake bar |
| `positive` | `#2E8B57` | `#2E8B57` | pass / OK badges, favourable deltas |
| `warning` | `#B8860B` | `#B8860B` | **warn badge for small cells (n < 11) in-app** (D-33), caution states |
| `danger` | `#B23A48` | `#B23A48` | failures, refusals, unsafe states |
| `suppressed` | `#9AA0A6` | `#9AA0A6` | **the one grey for suppressed cells** on export/commit (D-33) and greyed-out marks |

Contrast (WCAG 2.x, `theme.contrast_ratio`): `text`/`background` 16.6 (light) · 15.7 (dark);
`muted`/`background` 5.5 · 7.3; `muted`/`surface` 5.1 · 6.5 — all ≥ 4.5 (AA), enforced by
`tests/ep/test_ep05.py`.

### Categorical (`Palette.categorical`, Okabe–Ito, colour-blind safe on both backgrounds)

Order matters — Vega/Altair assign colours in this order, so yellow only appears at ≥ 8 categories:

| # | Name | Hex |
|---|---|---|
| 1 | blue | `#0072B2` |
| 2 | orange | `#E69F00` |
| 3 | bluish green | `#009E73` |
| 4 | vermilion | `#D55E00` |
| 5 | sky blue | `#56B4E9` |
| 6 | reddish purple | `#CC79A7` |
| 7 | grey | `#999999` |
| 8 | yellow | `#F0E442` |

Continuous scales use Vega built-in schemes: `Palette.sequential = "viridis"` (heatmaps,
ordinal, ramps) and `Palette.diverging = "blueorange"`.

## Rules

1. **Charts take colours only from `theme.Palette`** — never literal hex in app pages, report
   templates or notebooks. Need a colour? Add a token to `theme.py` (and this table).
2. **Suppressed cells and warn badges** use `suppressed` / `warning` respectively (GOVERNANCE §5,
   D-33); nothing else uses those two tokens.
3. **Screenshots** use the light theme unless the document they land in is dark; they are taken
   on the `demo` / `fixture` tiers only (GOVERNANCE §6) and pass `mwh disclose check` before
   entering `docs/` or git.
4. **Fonts** are the system stack (`theme.FONT_STACK`); nothing is embedded or fetched.
5. **Generated files are never hand-edited.** `.streamlit/config.toml` and the four SVGs here are
   written by `uv run --group dev python -m mimicwarehouse.theme --write` (idempotent) and
   checked by `... --check` and the EP-5 drift tests. Change `theme.py`, re-run `--write`.

## Enabling the theme

```python
from mimicwarehouse.theme import enable_altair, palette

enable_altair("light")  # registers mwh_light + mwh_dark, enables one — process-wide
p = palette("light")  # p.primary, p.suppressed, p.categorical, …
```

The Streamlit app shell (EP-57) calls `enable_altair(...)` once at start-up and reads
`.streamlit/config.toml` for the app chrome; report generation (P8) enables the theme before
rendering; the Plotly timeline template (EP-67) and report CSS / Typst styles (EP-130/131)
read the same `Palette` tokens.
