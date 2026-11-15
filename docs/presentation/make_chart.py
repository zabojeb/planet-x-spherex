"""Build the ε Indi B spectrum chart (inline SVG) for slides.html from the saved API answer.

Data: GET https://seekin.space/api/sequences/d651cb1431ed20027191/spectrum, saved as epsind-spectrum.json.
Usage: python docs/presentation/make_chart.py   # rewrites the chart between the CHART markers in slides.html
"""
import json, math
from pathlib import Path

HERE = Path(__file__).resolve().parent
d = json.loads((HERE / "epsind-spectrum.json").read_text())
W, H = 844, 540
L, R, T, B = 84, 20, 56, 72          # plot margins
X0, X1 = 0.75, 5.0                   # µm
Y0, Y1 = -1.0, 2.0                   # log10 mJy

def x(um): return L + (um - X0) / (X1 - X0) * (W - L - R)
def y(mjy): return T + (Y1 - math.log10(mjy)) / (Y1 - Y0) * (H - T - B)

bands = [  # (label, lo, hi, highlight)
    ("H₂O", 1.34, 1.47, False), ("H₂O", 1.82, 1.97, False), ("CH₄", 2.20, 2.35, True),
    ("H₂O", 2.55, 2.85, False), ("CH₄", 3.15, 3.45, True), ("CO", 4.55, 4.75, False),
]
out = [f'<svg class="chart" viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg">']
for lab, lo, hi, hl in bands:
    fill = "rgba(255,200,69,.16)" if hl else "rgba(236,232,223,.07)"
    col = "#ffc845" if hl else "#b8b3a7"
    out.append(f'<rect x="{x(lo):.1f}" y="{T}" width="{x(hi)-x(lo):.1f}" height="{H-T-B}" fill="{fill}"/>')
    out.append(f'<text x="{(x(lo)+x(hi))/2:.1f}" y="{T-16}" fill="{col}" font-size="22" text-anchor="middle">{lab}</text>')
for v in (0.1, 1, 10, 100):
    out.append(f'<line x1="{L}" x2="{W-R}" y1="{y(v):.1f}" y2="{y(v):.1f}" stroke="rgba(236,232,223,.10)"/>')
    out.append(f'<text x="{L-14}" y="{y(v)+7:.1f}" fill="#858074" font-size="20" text-anchor="end">{v:g}</text>')
for um in (1, 2, 3, 4, 5):
    out.append(f'<text x="{x(um):.1f}" y="{H-B+34}" fill="#858074" font-size="20" text-anchor="middle">{um}</text>')
out.append(f'<text x="{(L+W-R)/2:.1f}" y="{H-8}" fill="#858074" font-size="20" text-anchor="middle">wavelength, µm</text>')
out.append(f'<text transform="translate(22 {(T+H-B)/2:.1f}) rotate(-90)" fill="#858074" font-size="20" text-anchor="middle">flux, mJy</text>')
for b in d["bins"]:
    f, e, w = b["flux_mjy"], b["err_mjy"], b["wl"]
    if f is None or f <= 0.1 or w < X0 or w > X1:
        continue
    lo, hi = max(f - e, 0.1), f + e
    out.append(f'<line x1="{x(w):.1f}" x2="{x(w):.1f}" y1="{y(hi):.1f}" y2="{y(lo):.1f}" stroke="rgba(57,135,229,.55)" stroke-width="2"/>')
    out.append(f'<circle cx="{x(w):.1f}" cy="{y(f):.1f}" r="5" fill="#3987e5"/>')
out.append("</svg>")
svg = "\n".join(out)
p = HERE / "slides.html"
s = p.read_text()
a, b = s.index("<!--CHART-->"), s.index("<!--/CHART-->")
p.write_text(s[: a + len("<!--CHART-->")] + "\n" + svg + "\n" + s[b:])
print("chart written")
