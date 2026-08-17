"""Visual identity — palette, Altair theme, Streamlit theme, brand SVGs (EP-5, D-11, D-21).

One source of truth for every colour the lab shows: the two frozen :class:`Palette`
instances :data:`LIGHT` and :data:`DARK`. Charts (Altair/Vega-Lite, DESIGN §15), the
Streamlit app (EP-57), the Plotly timeline template (EP-67), report CSS (EP-130) and the
docs site (EP-160) all read their colours from here — never literal hex in pages or reports.

Governance shapes the palette: ``suppressed`` is the one grey used wherever a small cell
(n < 11, D-33) is suppressed, ``warning`` colours the in-app warn badge, and the categorical
set is the colour-blind-safe Okabe-Ito palette (yellow last so it appears only at ≥ 8
categories).

Generated files (``python -m mimicwarehouse.theme --write``): ``.streamlit/config.toml`` and
the four ``docs/brand/*.svg`` assets are rendered from string templates below so they cannot
drift from the palette (``--check`` reports drift; ``tests/ep/test_ep05.py`` proves it).
Everything is code + hand-written SVG — no raster assets, no embedded or web fonts, no
external requests.

Import budget: no altair / plotly / streamlit imports at module level (``altair`` is a core
dependency but costs ~0.3 s; ``plotly``/``streamlit`` live in the ``ui`` group).
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, Literal, cast

Mode = Literal["light", "dark"]
ThemeName = Literal["mwh_light", "mwh_dark"]

#: Altair theme names registered by :func:`register_altair` (``mwh_<mode>``).
THEME_NAMES: dict[str, ThemeName] = {"light": "mwh_light", "dark": "mwh_dark"}

#: System font stack — no embedded / web fonts anywhere in the identity (brief item 3).
FONT_STACK = (
    "system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, 'Noto Sans', sans-serif"
)
#: Streamlit ``[theme] font`` accepts only these generic families.
STREAMLIT_FONT = "sans serif"

#: Okabe-Ito (2008) colour-blind-safe set, ordered blue, orange, bluish-green, vermilion,
#: sky blue, reddish-purple, grey, yellow — yellow last so it only appears at ≥ 8 categories.
OKABE_ITO: tuple[str, ...] = (
    "#0072B2",  # blue
    "#E69F00",  # orange
    "#009E73",  # bluish green
    "#D55E00",  # vermilion
    "#56B4E9",  # sky blue
    "#CC79A7",  # reddish purple
    "#999999",  # grey
    "#F0E442",  # yellow
)

_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")


@dataclass(frozen=True, slots=True)
class Palette:
    """The brand tokens of one colour mode. All colours are ``#RRGGBB`` strings."""

    mode: str
    background: str
    surface: str
    text: str
    muted: str
    grid: str
    primary: str
    accent: str
    positive: str
    warning: str
    danger: str
    #: Grey used wherever a cell is suppressed (n < 11 on export, D-33) or a warn badge shows.
    suppressed: str
    #: Eight categorical colours (Okabe-Ito order, see :data:`OKABE_ITO`).
    categorical: tuple[str, ...] = OKABE_ITO
    #: Vega built-in scheme names (``range.heatmap`` / ``range.diverging``).
    sequential: str = "viridis"
    diverging: str = "blueorange"

    def __post_init__(self) -> None:
        for name, value in self.colours().items():
            if not _HEX.match(value):
                raise ValueError(f"Palette.{name}: {value!r} is not #RRGGBB")
        if len(self.categorical) != 8 or len(set(self.categorical)) != 8:
            raise ValueError("Palette.categorical must hold 8 distinct colours")
        for value in self.categorical:
            if not _HEX.match(value):
                raise ValueError(f"Palette.categorical: {value!r} is not #RRGGBB")

    def colours(self) -> dict[str, str]:
        """The single-colour tokens (everything but ``categorical`` / scheme names)."""
        skip = {"mode", "categorical", "sequential", "diverging"}
        return {f.name: getattr(self, f.name) for f in fields(self) if f.name not in skip}

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {f.name: getattr(self, f.name) for f in fields(self)}
        out["categorical"] = list(self.categorical)
        return out


def _palette(mode: str, background: str, surface: str, text: str, muted: str, grid: str) -> Palette:
    """A palette with the brand tokens shared by both modes (a session may tune the defaults;
    the tests fix the constraints)."""
    return Palette(
        mode=mode,
        background=background,
        surface=surface,
        text=text,
        muted=muted,
        grid=grid,
        primary="#1F6F8B",
        accent="#E07A5F",
        positive="#2E8B57",
        warning="#B8860B",
        danger="#B23A48",
        suppressed="#9AA0A6",
    )


LIGHT = _palette("light", "#FFFFFF", "#F5F7F9", "#1B1F23", "#5F6B76", "#E3E7EB")
DARK = _palette("dark", "#0F1419", "#1A2027", "#E6EDF3", "#9AA4AE", "#2A323B")

PALETTES: dict[str, Palette] = {"light": LIGHT, "dark": DARK}


def palette(mode: str) -> Palette:
    """The :class:`Palette` for ``mode`` (``"light"`` | ``"dark"``)."""
    try:
        return PALETTES[mode]
    except KeyError:
        raise ValueError(f"unknown mode {mode!r}; expected one of {sorted(PALETTES)}") from None


# ---------------------------------------------------------------------------
# Contrast (WCAG 2.x)
# ---------------------------------------------------------------------------


def _rgb(hex_colour: str) -> tuple[int, int, int]:
    value = hex_colour.strip()
    if not _HEX.match(value):
        raise ValueError(f"{hex_colour!r} is not #RRGGBB")
    return int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16)


def _linear(channel: int) -> float:
    c = channel / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def relative_luminance(hex_colour: str) -> float:
    """WCAG 2.x relative luminance of an sRGB ``#RRGGBB`` colour (0 = black, 1 = white)."""
    r, g, b = (_linear(c) for c in _rgb(hex_colour))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(hex_a: str, hex_b: str) -> float:
    """WCAG 2.x contrast ratio between two colours (1.0 … 21.0; AA text needs ≥ 4.5)."""
    la, lb = relative_luminance(hex_a), relative_luminance(hex_b)
    lighter, darker = max(la, lb), min(la, lb)
    return (lighter + 0.05) / (darker + 0.05)


# ---------------------------------------------------------------------------
# Altair / Vega-Lite theme
# ---------------------------------------------------------------------------


def altair_theme(mode: str) -> dict[str, Any]:
    """The Altair theme for ``mode``: ``{"config": <Vega-Lite config>}``.

    Fonts = system stack; axis / legend / title / header colours from the palette;
    ``range.category`` = the categorical set, ``range.heatmap`` / ``ordinal`` / ``ramp`` =
    the sequential scheme, ``range.diverging`` = the diverging scheme; ``view.stroke``
    transparent; mark defaults in ``primary``. The dict is what the registered theme
    function returns (Altair merges it into every chart's top-level spec).
    """
    p = palette(mode)
    fonts = {"labelFont": FONT_STACK, "titleFont": FONT_STACK}
    config: dict[str, Any] = {
        "background": p.background,
        "font": FONT_STACK,
        "view": {"stroke": "transparent", "continuousWidth": 400, "continuousHeight": 260},
        "axis": {
            **fonts,
            "domainColor": p.grid,
            "gridColor": p.grid,
            "tickColor": p.grid,
            "labelColor": p.muted,
            "titleColor": p.text,
            "labelFontSize": 11,
            "titleFontSize": 12,
            "titleFontWeight": "normal",
            "titlePadding": 8,
        },
        "legend": {
            **fonts,
            "labelColor": p.muted,
            "titleColor": p.text,
            "labelFontSize": 11,
            "titleFontSize": 12,
            "titleFontWeight": "normal",
            "symbolType": "circle",
        },
        "header": {**fonts, "labelColor": p.text, "titleColor": p.text, "labelFontSize": 12},
        "title": {
            "font": FONT_STACK,
            "subtitleFont": FONT_STACK,
            "color": p.text,
            "subtitleColor": p.muted,
            "fontSize": 16,
            "subtitleFontSize": 12,
            "fontWeight": 600,
            "anchor": "start",
            "offset": 12,
        },
        "text": {"color": p.text, "font": FONT_STACK, "fontSize": 11},
        "mark": {"color": p.primary},
        "bar": {"color": p.primary},
        "area": {"color": p.primary, "opacity": 0.6},
        "line": {"color": p.primary, "strokeWidth": 2},
        "point": {"color": p.primary, "filled": True, "size": 60},
        "circle": {"color": p.primary, "size": 60},
        "rect": {"color": p.primary},
        "rule": {"color": p.muted},
        "tick": {"color": p.primary},
        "range": {
            "category": list(p.categorical),
            "heatmap": {"scheme": p.sequential},
            "ordinal": {"scheme": p.sequential},
            "ramp": {"scheme": p.sequential},
            "diverging": {"scheme": p.diverging},
        },
    }
    return {"config": config}


def register_altair() -> tuple[ThemeName, ...]:
    """Register ``mwh_light`` and ``mwh_dark`` with Altair's theme registry (idempotent).

    Nothing is enabled; call :func:`enable_altair` for that. Returns the theme names.
    """
    import altair as alt
    from altair.theme import ThemeConfig

    def _make(mode: str) -> Callable[[], ThemeConfig]:
        def _theme() -> ThemeConfig:
            return cast(ThemeConfig, altair_theme(mode))

        _theme.__name__ = THEME_NAMES[mode]
        return _theme

    for mode, name in THEME_NAMES.items():
        alt.theme.register(name, enable=False)(_make(mode))
    return tuple(THEME_NAMES.values())


def enable_altair(mode: str = "light") -> ThemeName:
    """Register (if needed) and enable the ``mode`` theme process-wide; returns its name."""
    import altair as alt

    name = THEME_NAMES[palette(mode).mode]
    register_altair()
    alt.theme.enable(name)
    return name


# ---------------------------------------------------------------------------
# Streamlit theme
# ---------------------------------------------------------------------------


def streamlit_theme(mode: str) -> dict[str, str]:
    """The ``[theme]`` table of ``.streamlit/config.toml`` for ``mode``."""
    p = palette(mode)
    return {
        "base": p.mode,
        "primaryColor": p.primary,
        "backgroundColor": p.background,
        "secondaryBackgroundColor": p.surface,
        "textColor": p.text,
        "font": STREAMLIT_FONT,
    }


#: The non-theme tables of ``.streamlit/config.toml`` (local-only, no telemetry).
STREAMLIT_SERVER: dict[str, dict[str, Any]] = {
    "server": {"address": "127.0.0.1", "headless": True},
    "browser": {"gatherUsageStats": False},
}


def _toml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def streamlit_config_toml(mode: str = "light") -> str:
    """Render ``.streamlit/config.toml`` (theme from :func:`streamlit_theme` + server/browser)."""
    lines = [
        "# Generated by `python -m mimicwarehouse.theme --write` (EP-5) from",
        f"# mimicwarehouse.theme.streamlit_theme({mode!r}) — do not edit by hand; edit theme.py.",
        "",
        "[theme]",
    ]
    lines += [f"{k} = {_toml_scalar(v)}" for k, v in streamlit_theme(mode).items()]
    for table, values in STREAMLIT_SERVER.items():
        lines += ["", f"[{table}]"]
        lines += [f"{k} = {_toml_scalar(v)}" for k, v in values.items()]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Wordmark and banner (SVG string templates; text-based, system fonts, no external refs)
# ---------------------------------------------------------------------------

WORDMARK_TEXT = "mimicwarehouse"
TAGLINE = "a local MIMIC-IV data lab — DuckDB · Polars · Streamlit"
BANNER_SIZE = (1200, 240)
#: The three stacked bars of the glyph: raw (widest, bottom) → lake → marts (top).
GLYPH_LAYERS = ("raw", "lake", "marts")


def _glyph(p: Palette, x: float, y: float, unit: float) -> str:
    """Three stacked bars raw → lake → marts (``primary`` / ``accent`` / ``muted``).

    ``unit`` is the bar height; the bars are 4/2.8/1.6 units wide, centred, 1.6 units apart.
    """
    colours = (p.primary, p.accent, p.muted)
    widths = (4.0, 2.8, 1.6)
    step = 1.6 * unit
    out = []
    for i, (layer, colour, w) in enumerate(zip(GLYPH_LAYERS, colours, widths, strict=True)):
        width = w * unit
        bx = x + (4.0 * unit - width) / 2
        by = y + (2 - i) * step  # raw at the bottom
        out.append(
            f'<rect class="layer layer-{layer}" x="{bx:g}" y="{by:g}" width="{width:g}" '
            f'height="{unit:g}" rx="{unit / 6:.2g}" fill="{colour}"/>'
        )
    return "\n    ".join(out)


def _svg_open(width: int, height: int) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}" role="img" aria-labelledby="t d">'
    )


def _svg_text(x: float, y: float, size: int, fill: str, content: str, **attrs: str) -> str:
    extra = "".join(f' {k.replace("_", "-")}="{v}"' for k, v in attrs.items())
    return (
        f'<text x="{x:g}" y="{y:g}" font-family="{FONT_STACK}" font-size="{size}"{extra} '
        f'fill="{fill}">{content}</text>'
    )


def wordmark_svg(mode: str) -> str:
    """The wordmark: glyph + the word ``mimicwarehouse`` on a transparent background."""
    p = palette(mode)
    lines = [
        _svg_open(320, 56),
        '  <title id="t">mimicwarehouse</title>',
        f'  <desc id="d">Wordmark ({p.mode}): three stacked bars for raw, lake and marts, '
        "then the word mimicwarehouse.</desc>",
        '  <g class="glyph">',
        f"    {_glyph(p, 4, 8, 10)}",
        "  </g>",
        "  "
        + _svg_text(58, 38, 27, p.text, WORDMARK_TEXT, font_weight="600", letter_spacing="-0.4"),
        "</svg>",
    ]
    return "\n".join(lines) + "\n"


def banner_svg(mode: str) -> str:
    """The README banner (1200 x 240): wordmark + tagline on the palette background."""
    p = palette(mode)
    w, h = BANNER_SIZE
    lines = [
        _svg_open(w, h),
        f'  <title id="t">mimicwarehouse — {TAGLINE}</title>',
        f'  <desc id="d">Banner ({p.mode}): the mimicwarehouse wordmark and tagline.</desc>',
        f'  <rect x="0.5" y="0.5" width="{w - 1}" height="{h - 1}" rx="16" '
        f'fill="{p.background}" stroke="{p.grid}"/>',
        '  <g class="glyph">',
        f"    {_glyph(p, 72, 80, 22)}",
        "  </g>",
        "  "
        + _svg_text(196, 118, 60, p.text, WORDMARK_TEXT, font_weight="600", letter_spacing="-1"),
        "  " + _svg_text(199, 164, 22, p.muted, TAGLINE),
        f'  <rect x="199" y="184" width="88" height="4" rx="2" fill="{p.primary}"/>',
        f'  <rect x="295" y="184" width="44" height="4" rx="2" fill="{p.accent}"/>',
        f'  <rect x="347" y="184" width="22" height="4" rx="2" fill="{p.muted}"/>',
        "</svg>",
    ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Generated assets: render / check / write
# ---------------------------------------------------------------------------

STREAMLIT_CONFIG_PATH = ".streamlit/config.toml"
BRAND_DIR = "docs/brand"
MAX_SVG_BYTES = 8 * 1024


def render_assets() -> dict[str, str]:
    """Every generated file, as ``{workspace-relative posix path: text}``."""
    assets = {STREAMLIT_CONFIG_PATH: streamlit_config_toml("light")}
    for mode in PALETTES:
        assets[f"{BRAND_DIR}/wordmark-{mode}.svg"] = wordmark_svg(mode)
        assets[f"{BRAND_DIR}/banner-{mode}.svg"] = banner_svg(mode)
    return assets


def _workspace_root() -> Path:
    from mimicwarehouse.config import workspace_root

    return workspace_root()


def check_assets(root: Path | None = None) -> list[str]:
    """Workspace-relative paths whose on-disk content differs from a fresh render (or is
    missing)."""
    base = root or _workspace_root()
    drift = []
    for rel, text in render_assets().items():
        path = base / rel
        if not path.is_file() or path.read_text(encoding="utf-8") != text:
            drift.append(rel)
    return drift


def write_assets(root: Path | None = None) -> list[str]:
    """Write the generated files (LF, UTF-8) under ``root``; returns the paths that changed."""
    base = root or _workspace_root()
    changed = []
    for rel, text in render_assets().items():
        path = base / rel
        if path.is_file() and path.read_text(encoding="utf-8") == text:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        changed.append(rel)
    return changed


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m mimicwarehouse.theme",
        description="Render the generated identity files "
        "(.streamlit/config.toml, docs/brand/*.svg).",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--write", action="store_true", help="write the files (idempotent)")
    group.add_argument("--check", action="store_true", help="exit 1 if any file drifted")
    parser.add_argument("--root", type=Path, default=None, help="workspace root (default: auto)")
    args = parser.parse_args(argv)
    if args.check:
        drift = check_assets(args.root)
        for rel in drift:
            print(f"drift: {rel}")
        print(f"{len(drift)} drifted" if drift else "ok: generated identity files match theme.py")
        return 1 if drift else 0
    changed = write_assets(args.root)
    for rel in changed:
        print(f"wrote: {rel}")
    print(f"{len(changed)} file(s) written; {len(render_assets()) - len(changed)} unchanged")
    return 0


if __name__ == "__main__":
    sys.exit(main())
