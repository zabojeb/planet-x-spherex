"""Build the public evidence pages served at https://seekin.space/evidence/ while the repository is private.

Renders docs/methods.md and analysis/validation/REPORT.md to HTML in the app's colours and copies the deck,
into web/public/evidence/ (Vite copies web/public into web/dist, which the API serves).
Usage: python scripts/build_evidence.py   (needs the `markdown` package)
"""
from pathlib import Path
import re
import shutil

import markdown

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "web" / "public" / "evidence"
REPO = "https://github.com/zabojeb/planet-x-spherex/blob/main/"
CSS = """
:root { color-scheme: dark; }
body { margin: 0; background: #050505; color: #ece8df; font: 17px/1.6 "Geist", system-ui, -apple-system, sans-serif; }
main { max-width: 980px; margin: 0 auto; padding: 48px 20px 96px; }
h1, h2, h3 { font-family: "Instrument Serif", Georgia, serif; font-weight: 400; line-height: 1.15; }
h1 { font-size: 46px; margin: 0 0 8px; } h2 { font-size: 32px; margin-top: 48px; } h3 { font-size: 24px; margin-top: 32px; }
a { color: #ffc845; } p, li { color: #d8d3c8; }
table { border-collapse: collapse; width: 100%; margin: 16px 0; font-size: 14.5px; display: block; overflow-x: auto; }
th, td { border-bottom: 1px solid rgba(236,232,223,.14); padding: 7px 10px; text-align: left; vertical-align: top; }
th { color: #b8b3a7; font-weight: 500; }
code, pre { font-family: ui-monospace, Menlo, monospace; font-size: 14px; background: rgba(236,232,223,.06); border-radius: 3px; }
code { padding: 1px 4px; } pre { padding: 12px; overflow-x: auto; }
img { max-width: 100%; }
.top { color: #858074; font-size: 14px; margin-bottom: 32px; } .top a { margin-right: 18px; }
"""
PAGES = [("methods", ROOT / "docs" / "methods.md", "Methods"),
         ("validation", ROOT / "analysis" / "validation" / "REPORT.md", "Validation report")]
NAV = '<div class="top"><a href="./">Evidence</a><a href="methods.html">Methods</a><a href="validation.html">Validation</a><a href="presentation.pdf">Presentation (PDF)</a><a href="/">Open the app</a></div>'


def page(title: str, body: str) -> str:
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
            f"<title>{title} · Seek In Space</title><style>{CSS}</style></head><body><main>{NAV}{body}</main></body></html>")


def fix_links(html: str, src: Path) -> str:
    # Relative links point into the repository; send them to GitHub (readable once the repository is public).
    base = src.parent.relative_to(ROOT).as_posix() + "/"
    return re.sub(r'href="(?!https?:|#|mailto:)([^"]+)"', lambda m: f'href="{REPO}{base}{m.group(1)}"', html)


OUT.mkdir(parents=True, exist_ok=True)
for name, src, title in PAGES:
    html = markdown.markdown(src.read_text(), extensions=["tables", "fenced_code", "sane_lists"])
    (OUT / f"{name}.html").write_text(page(title, fix_links(html, src)))
shutil.copy(ROOT / "docs" / "presentation.pdf", OUT / "presentation.pdf")
index = """<h1>Seek In Space: evidence</h1>
<p>Public copies of the project's methods, validation report and presentation, for judges and anyone else, while the code repository is private.</p>
<ul>
<li><a href="presentation.pdf">Presentation</a> (7 slides, PDF)</li>
<li><a href="methods.html">Methods</a>: pipeline parameters, the checks on the five challenge targets, known limitations</li>
<li><a href="validation.html">Validation report</a>: target-by-target measurements and controls</li>
<li><a href="/docs">HTTP API reference</a> (interactive) and the MCP server at <code>https://seekin.space/mcp</code></li>
</ul>
<h2>Try these</h2>
<ul>
<li><a href="/?target=barnard&amp;mode=diff">Barnard's Star, difference of two visits</a>: blue is where it was, red where it is a year later</li>
<li><a href="/?target=barnard&amp;v3d=time">Barnard's Star in the time cube</a></li>
<li><a href="/?target=eps-indi-b">ε Indi B</a>: open Spectrum for water and methane bands</li>
<li><a href="/?target=r-dra">R Draconis</a>: open Light curve and Period</li>
</ul>"""
(OUT / "index.html").write_text(page("Evidence", index))
print("wrote", sorted(p.name for p in OUT.iterdir()))
