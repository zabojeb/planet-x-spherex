"""Render slides.html to docs/presentation.pdf (1920×1080 pages) with headless Chrome.

Needs the web fonts (`npm --prefix web ci`) and Playwright for Python (`pip install playwright`);
it drives the installed Google Chrome, so no browser download is needed.
Usage: python docs/presentation/render.py [--png DIR]   # --png also writes one image per slide
"""
from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
SRC = HERE / "slides.html"
PDF = HERE.parent / "presentation.pdf"


def main() -> None:
    png_dir = Path(sys.argv[sys.argv.index("--png") + 1]) if "--png" in sys.argv else None
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1920, "height": 1080})
        page.goto(SRC.as_uri(), wait_until="load")
        page.evaluate("document.fonts.ready.then(() => true)")
        missing = page.evaluate(
            "[...document.fonts].filter(f => f.status === 'error').map(f => f.family + ' ' + f.style)"
        )
        if missing:
            raise SystemExit(f"fonts not loaded: {missing} (run `npm --prefix web ci`)")
        page.pdf(path=str(PDF), width="1920px", height="1080px", print_background=True, prefer_css_page_size=True)
        print(f"wrote {PDF.relative_to(HERE.parent.parent)}")
        if png_dir:
            png_dir.mkdir(parents=True, exist_ok=True)
            for i, slide in enumerate(page.query_selector_all(".slide"), 1):
                slide.screenshot(path=str(png_dir / f"slide-{i}.png"))
            print(f"wrote slide images to {png_dir}")
        browser.close()


if __name__ == "__main__":
    main()
