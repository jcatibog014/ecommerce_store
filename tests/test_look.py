"""The stylesheet's files exist, both themes have every colour, and text is readable (contrast)."""
import re
from pathlib import Path

import pytest

STATIC = Path(__file__).parent.parent / "dfjc" / "static"
CSS = (STATIC / "style.css").read_text(encoding="utf-8")


def block(selector_start):
    """The colours set in one theme block (light, device dark, or dark chosen in Settings)."""
    start = CSS.index(selector_start)
    body = CSS[start:CSS.index("\n}", start)]
    return dict(re.findall(r"(--[\w-]+): (#[0-9a-f]{6});", body))


LIGHT = block(":root {")
DEVICE_DARK = block(':root:not([data-theme="light"]) {')
CHOSEN_DARK = block(':root[data-theme="dark"] {')


def luminance(hex_colour):
    def channel(value):
        value /= 255
        return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
    r, g, b = (int(hex_colour[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast(a, b):
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def test_every_file_the_stylesheet_loads_exists():
    paths = re.findall(r'url\("([^"]+)"\)', CSS)
    assert paths, "the fonts should be loaded with url(...)"
    assert [path for path in paths if not (STATIC / path).exists()] == []


def test_the_pages_preload_the_heading_font(client):
    page = client.get("/login").get_data(as_text=True)
    preloaded = re.search(r'<link rel="preload" href="/static/(fonts/[^"]+)"', page).group(1)
    assert f'url("{preloaded}")' in CSS


def test_both_dark_blocks_are_the_same():
    # Dark mode is written twice (device setting + Settings choice). They must never drift apart.
    assert DEVICE_DARK == CHOSEN_DARK


def test_dark_mode_sets_every_colour_that_changes():
    missing = [name for name in ("--bg", "--surface", "--ink", "--accent", "--link", "--red", "--panel", "--bamboo")
               if name not in DEVICE_DARK]
    assert missing == []


# (text, background, the least contrast). 4.5 for normal text, 3 for big text and borders.
PAIRS = [
    ("--ink", "--bg", 4.5), ("--ink", "--surface", 4.5), ("--ink-soft", "--surface", 4.5),
    ("--ink-soft", "--surface-soft", 4.5), ("--link", "--surface", 4.5), ("--link", "--bg", 4.5),
    ("--on-accent", "--accent", 4.5), ("--on-accent", "--accent-hover", 4.5),
    ("--red", "--surface", 4.5), ("--red", "--red-soft", 4.5), ("--green", "--green-soft", 4.5),
    ("--warning-ink", "--warning-soft", 4.5), ("--accent-strong", "--accent-soft", 4.5),
    ("--sign-ink", "--sign", 4.5), ("--panel-ink", "--panel", 4.5), ("--control-border", "--surface", 3),
    ("--tile-0-ink", "--tile-0-bg", 4.5), ("--tile-1-ink", "--tile-1-bg", 4.5), ("--tile-2-ink", "--tile-2-bg", 4.5),
    ("--tile-3-ink", "--tile-3-bg", 4.5), ("--tile-4-ink", "--tile-4-bg", 4.5), ("--tile-5-ink", "--tile-5-bg", 4.5),
]


@pytest.mark.parametrize("theme_name", ["light", "dark"])
def test_text_is_readable_in_both_themes(theme_name):
    theme = LIGHT if theme_name == "light" else {**LIGHT, **DEVICE_DARK}
    too_faint = [(text, background, round(contrast(theme[text], theme[background]), 2))
                 for text, background, least in PAIRS if contrast(theme[text], theme[background]) < least]
    assert too_faint == []


def test_the_login_panel_is_wood_never_the_yellow_accent():
    assert ".welcome-panel { background-color: var(--panel);" in CSS
    assert len(re.findall(r"--panel: #[0-9a-f]{6};", CSS)) == 3
