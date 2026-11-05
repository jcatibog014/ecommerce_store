"""Draw the app icons (dfjc/static/icons/): the tindahan with its red awning, on a yellow tile.

It is the same drawing as the logo (mark() in templates/_macros.html and
static/icon.svg). Phones and browsers want PNG pictures in fixed sizes, plus
favicon.ico. Run this again after changing the design. Like take_screenshots.py,
it uses Playwright + Edge (Playwright isn't in requirements.txt: borrow another
project's):

    ..\\expense_tracker\\.venv\\Scripts\\python docs\\make_icons.py
"""
import struct
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).parent.parent
ICONS = ROOT / "dfjc" / "static" / "icons"

TILE = "#f2b200"     # store-sign yellow: stands out on light and dark home screens
AWNING = "#c62828"
SHOP = "#3a2614"     # dark wood

# (file, size, shape). "rounded" has see-through corners, for browser tabs and
# installed apps. "square" fills every pixel: iPhones round the corners
# themselves, and Android crops a "maskable" icon into its own shape.
PNGS = [
    ("icon-32.png", 32, "rounded"),
    ("icon-192.png", 192, "rounded"),
    ("icon-512.png", 512, "rounded"),
    ("icon-maskable-512.png", 512, "square"),
    ("apple-touch-icon.png", 180, "square"),
]
ICO_SIZES = (16, 32, 48)


def page_html(size, shape):
    radius = round(size * 0.22) if shape == "rounded" else 0
    # A maskable icon can be cropped to a circle, so its drawing stays in the middle.
    drawing = size * (0.66 if shape == "square" else 0.9)
    return f"""<!doctype html>
<style>
  html, body {{ margin: 0; background: transparent; }}
  .tile {{ width: {size}px; height: {size}px; background: {TILE}; border-radius: {radius}px;
           display: flex; align-items: center; justify-content: center; overflow: hidden; }}
</style>
<div class="tile">
  <svg width="{drawing}" height="{drawing}" viewBox="2 2 28 28">
    <path d="M5 7h22v4.5a2.75 2.75 0 0 1-5.5 0a2.75 2.75 0 0 1-5.5 0a2.75 2.75 0 0 1-5.5 0a2.75 2.75 0 0 1-5.5 0z" fill="{AWNING}"/>
    <path d="M8 15.5v10h16v-10M13 25.5v-5.5h6v5.5" fill="none" stroke="{SHOP}" stroke-width="2.2" stroke-linejoin="round" stroke-linecap="round"/>
  </svg>
</div>"""


def render(browser, size, shape):
    page = browser.new_page(viewport={"width": size, "height": size})
    page.set_content(page_html(size, shape))
    png = page.locator(".tile").screenshot(omit_background=True)
    page.close()
    return png


def write_ico(path, pictures):
    """favicon.ico is a small header, a list, then the PNG files themselves."""
    header = struct.pack("<HHH", 0, 1, len(pictures))
    offset = 6 + 16 * len(pictures)
    entries, data = b"", b""
    for size, png in pictures:
        entries += struct.pack("<BBBBHHII", size, size, 0, 0, 1, 32, len(png), offset + len(data))
        data += png
    path.write_bytes(header + entries + data)


def main():
    ICONS.mkdir(exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge")
        for name, size, shape in PNGS:
            (ICONS / name).write_bytes(render(browser, size, shape))
            print("saved", name)
        write_ico(ICONS / "favicon.ico", [(size, render(browser, size, "rounded")) for size in ICO_SIZES])
        print("saved favicon.ico")
        browser.close()


if __name__ == "__main__":
    main()
