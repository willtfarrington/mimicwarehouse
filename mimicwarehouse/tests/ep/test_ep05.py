"""EP-5 — Visual identity acceptance tests (palette, Altair/Streamlit themes, brand SVGs).

Docs-only brief with a code module: no data, no tier. The drift tests compare the tracked
generated files (``.streamlit/config.toml``, ``docs/brand/*.svg``) against a fresh render from
:mod:`mimicwarehouse.theme` so the palette and the assets cannot diverge.
"""

from __future__ import annotations

import itertools
import re
import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, cast

import pytest

from mimicwarehouse import theme
from mimicwarehouse.theme import (
    BANNER_SIZE,
    DARK,
    LIGHT,
    MAX_SVG_BYTES,
    OKABE_ITO,
    PALETTES,
    THEME_NAMES,
    Palette,
    altair_theme,
    banner_svg,
    check_assets,
    contrast_ratio,
    enable_altair,
    palette,
    register_altair,
    render_assets,
    streamlit_config_toml,
    streamlit_theme,
    wordmark_svg,
    write_assets,
)

pytestmark = pytest.mark.ep_5

WORKSPACE = Path(__file__).resolve().parents[2]  # mimicwarehouse/ (the uv project)
REPO = WORKSPACE.parent
BRAND = WORKSPACE / "docs" / "brand"
HEX = re.compile(r"^#[0-9A-F]{6}$")
SVG_FILES = ("wordmark-light.svg", "wordmark-dark.svg", "banner-light.svg", "banner-dark.svg")
EXTERNAL_MARKERS = ('href="http', "@import", "url(", "<script", "<image", "<foreignObject")


# ---------------------------------------------------------------------------
# Palette
# ---------------------------------------------------------------------------


def test_palettes_are_frozen_and_named() -> None:
    assert LIGHT.mode == "light" and DARK.mode == "dark"
    assert palette("light") is LIGHT and palette("dark") is DARK
    assert set(PALETTES) == {"light", "dark"}
    with pytest.raises(AttributeError):
        LIGHT.primary = "#000000"  # type: ignore[misc]
    with pytest.raises(ValueError, match="unknown mode"):
        palette("sepia")


@pytest.mark.parametrize("p", [LIGHT, DARK], ids=["light", "dark"])
def test_palette_tokens_are_uppercase_hex(p: Palette) -> None:
    expected = {
        "background",
        "surface",
        "text",
        "muted",
        "grid",
        "primary",
        "accent",
        "positive",
        "warning",
        "danger",
        "suppressed",
    }
    assert set(p.colours()) == expected
    for name, value in p.colours().items():
        assert HEX.match(value), (name, value)
    assert p.sequential == "viridis" and p.diverging == "blueorange"


def test_brand_tokens_shared_across_modes() -> None:
    for token in ("primary", "accent", "positive", "warning", "danger", "suppressed"):
        assert getattr(LIGHT, token) == getattr(DARK, token)
    for token in ("background", "surface", "text", "muted", "grid"):
        assert getattr(LIGHT, token) != getattr(DARK, token)


@pytest.mark.parametrize("p", [LIGHT, DARK], ids=["light", "dark"])
@pytest.mark.parametrize("fg", ["text", "muted"])
@pytest.mark.parametrize("bg", ["background", "surface"])
def test_text_contrast_meets_wcag_aa(p: Palette, fg: str, bg: str) -> None:
    ratio = contrast_ratio(getattr(p, fg), getattr(p, bg))
    assert ratio >= 4.5, (p.mode, fg, bg, ratio)


def test_contrast_ratio_reference_values() -> None:
    assert contrast_ratio("#000000", "#FFFFFF") == pytest.approx(21.0)
    assert contrast_ratio("#FFFFFF", "#000000") == pytest.approx(21.0)
    assert contrast_ratio("#777777", "#777777") == pytest.approx(1.0)
    assert contrast_ratio("#767676", "#FFFFFF") == pytest.approx(4.54, abs=0.01)  # WCAG example
    with pytest.raises(ValueError):
        contrast_ratio("red", "#FFFFFF")


def test_categorical_is_okabe_ito_in_brief_order() -> None:
    assert LIGHT.categorical == DARK.categorical == OKABE_ITO
    assert len(OKABE_ITO) == 8
    assert OKABE_ITO[0] == "#0072B2"  # blue first
    assert OKABE_ITO[-1] == "#F0E442"  # yellow last (only at >= 8 categories)


@pytest.mark.parametrize("p", [LIGHT, DARK], ids=["light", "dark"])
def test_categorical_valid_distinct_and_never_the_background(p: Palette) -> None:
    for c in p.categorical:
        assert HEX.match(c), c
    for a, b in itertools.combinations(p.categorical, 2):
        assert a != b
    for c in p.categorical:
        assert c not in {LIGHT.background, DARK.background}


def test_palette_rejects_bad_hex() -> None:
    with pytest.raises(ValueError, match="not #RRGGBB"):
        Palette(
            mode="x",
            background="white",
            surface="#FFFFFF",
            text="#000000",
            muted="#000000",
            grid="#000000",
            primary="#000000",
            accent="#000000",
            positive="#000000",
            warning="#000000",
            danger="#000000",
            suppressed="#000000",
        )


# ---------------------------------------------------------------------------
# Altair theme
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_altair_theme_config_carries_palette(mode: str) -> None:
    p = palette(mode)
    cfg = altair_theme(mode)["config"]
    assert cfg["background"] == p.background
    assert cfg["font"] == theme.FONT_STACK
    assert cfg["view"]["stroke"] == "transparent"
    assert cfg["axis"]["labelColor"] == p.muted and cfg["axis"]["titleColor"] == p.text
    assert cfg["axis"]["gridColor"] == p.grid
    assert cfg["legend"]["titleColor"] == p.text and cfg["title"]["color"] == p.text
    assert cfg["range"]["category"] == list(p.categorical)
    assert cfg["range"]["heatmap"] == {"scheme": p.sequential}
    assert cfg["range"]["diverging"] == {"scheme": p.diverging}
    assert cfg["mark"]["color"] == p.primary


def test_register_and_enable_altair_apply_palette_to_chart() -> None:
    import altair as alt

    names = register_altair()
    assert names == ("mwh_light", "mwh_dark") == tuple(THEME_NAMES.values())
    assert set(names) <= set(alt.theme.names())
    assert register_altair() == names  # idempotent

    previous = alt.theme.active
    try:
        assert enable_altair("light") == "mwh_light"
        assert alt.theme.active == "mwh_light"
        data = alt.Data(values=[{"k": "a", "n": 3}, {"k": "b", "n": 5}])  # synthetic
        spec = alt.Chart(data).mark_bar().encode(x="k:N", y="n:Q").to_dict()
        assert spec["config"]["range"]["category"] == list(LIGHT.categorical)
        assert spec["config"]["background"] == LIGHT.background
        assert spec["config"]["axis"]["labelColor"] == LIGHT.muted

        assert enable_altair("dark") == "mwh_dark"
        spec = alt.Chart(data).mark_bar().encode(x="k:N", y="n:Q").to_dict()
        assert spec["config"]["background"] == DARK.background
    finally:
        alt.theme.enable(cast("Any", previous))


def test_no_ui_imports_at_module_level() -> None:
    import ast

    tree = ast.parse((WORKSPACE / "src" / "mimicwarehouse" / "theme.py").read_text("utf-8"))
    top_level = {
        alias.name.split(".")[0]
        for node in tree.body
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        (node.module or "").split(".")[0] for node in tree.body if isinstance(node, ast.ImportFrom)
    }
    assert not top_level & {"altair", "plotly", "streamlit", "vegafusion", "vl_convert"}


# ---------------------------------------------------------------------------
# Streamlit theme + config.toml (drift)
# ---------------------------------------------------------------------------


def test_streamlit_theme_values() -> None:
    for mode in ("light", "dark"):
        p = palette(mode)
        t = streamlit_theme(mode)
        assert t == {
            "base": mode,
            "primaryColor": p.primary,
            "backgroundColor": p.background,
            "secondaryBackgroundColor": p.surface,
            "textColor": p.text,
            "font": "sans serif",
        }


def test_streamlit_config_toml_matches_streamlit_theme() -> None:
    path = WORKSPACE / ".streamlit" / "config.toml"
    assert path.is_file()
    parsed = tomllib.loads(path.read_text("utf-8"))
    assert parsed["theme"] == streamlit_theme("light")
    assert parsed["server"] == {"address": "127.0.0.1", "headless": True}
    assert parsed["browser"] == {"gatherUsageStats": False}
    assert path.read_text("utf-8") == streamlit_config_toml("light")
    assert tomllib.loads(streamlit_config_toml("dark"))["theme"] == streamlit_theme("dark")


# ---------------------------------------------------------------------------
# SVGs (drift, XML, size, no external references)
# ---------------------------------------------------------------------------


def test_render_assets_lists_the_five_generated_files() -> None:
    expected = {".streamlit/config.toml", *(f"docs/brand/{f}" for f in SVG_FILES)}
    assert set(render_assets()) == expected


def test_tracked_svgs_equal_fresh_render() -> None:
    fresh = {
        "wordmark-light.svg": wordmark_svg("light"),
        "wordmark-dark.svg": wordmark_svg("dark"),
        "banner-light.svg": banner_svg("light"),
        "banner-dark.svg": banner_svg("dark"),
    }
    for name, text in fresh.items():
        path = BRAND / name
        assert path.is_file(), name
        assert path.read_bytes() == text.encode("utf-8"), f"{name} drifted — run --write"
    assert check_assets(WORKSPACE) == []


@pytest.mark.parametrize("name", SVG_FILES)
def test_svg_parses_is_small_and_self_contained(name: str) -> None:
    raw = (BRAND / name).read_bytes()
    assert len(raw) <= MAX_SVG_BYTES, (name, len(raw))
    root = ET.fromstring(raw)
    assert root.tag == "{http://www.w3.org/2000/svg}svg"
    text = raw.decode("utf-8")
    for marker in EXTERNAL_MARKERS:
        assert marker not in text, (name, marker)
    assert "\r" not in text  # LF only


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_svgs_use_the_matching_palette(mode: str) -> None:
    p = palette(mode)
    other = palette("dark" if mode == "light" else "light")
    wordmark, banner = wordmark_svg(mode), banner_svg(mode)
    for svg in (wordmark, banner):
        assert "mimicwarehouse" in svg
        assert f'fill="{p.text}"' in svg
        for layer_colour in (p.primary, p.accent, p.muted):
            assert f'fill="{layer_colour}"' in svg  # raw / lake / marts bars
        assert 'class="layer layer-raw"' in svg
        assert other.text not in svg
        assert theme.FONT_STACK in svg
    assert theme.TAGLINE in banner
    w, h = BANNER_SIZE
    assert (w, h) == (1200, 240)
    assert f'viewBox="0 0 {w} {h}"' in banner
    assert f'fill="{p.background}"' in banner
    assert 'viewBox="0 0' in wordmark and p.background not in wordmark  # transparent


def test_write_assets_is_idempotent(tmp_path: Path) -> None:
    first = write_assets(tmp_path)
    assert set(first) == set(render_assets())
    assert write_assets(tmp_path) == []
    assert check_assets(tmp_path) == []
    (tmp_path / "docs" / "brand" / "banner-dark.svg").write_text("<svg/>", encoding="utf-8")
    assert check_assets(tmp_path) == ["docs/brand/banner-dark.svg"]
    assert theme.main(["--check", "--root", str(tmp_path)]) == 1
    assert theme.main(["--write", "--root", str(tmp_path)]) == 0
    assert theme.main(["--check", "--root", str(tmp_path)]) == 0


# ---------------------------------------------------------------------------
# Docs: brand README and the root README banner
# ---------------------------------------------------------------------------


def test_brand_readme_lists_every_palette_hex() -> None:
    text = (BRAND / "README.md").read_text("utf-8")
    for p in (LIGHT, DARK):
        for name, value in p.colours().items():
            assert value in text, (p.mode, name, value)
    for c in OKABE_ITO:
        assert c in text
    assert "viridis" in text and "blueorange" in text
    assert 'enable_altair("light")' in text


def test_root_readme_has_theme_aware_banner() -> None:
    text = (REPO / "README.md").read_text("utf-8")
    assert "<picture>" in text
    assert "prefers-color-scheme: dark" in text
    assert "mimicwarehouse/docs/brand/banner-dark.svg" in text
    assert "mimicwarehouse/docs/brand/banner-light.svg" in text
    assert text.index("<picture>") < text.index("# mimicwarehouse")
