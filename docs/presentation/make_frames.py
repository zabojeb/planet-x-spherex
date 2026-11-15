"""Render the Barnard's Star filmstrip for slide 3 from the sequence's per-visit co-adds.

Data: GET https://seekin.space/api/sequences/4261e28d6e0c3cb6df94/coadds.bin, float32 [visit, detector, 59, 59],
saved as barnard-coadds.f32. Usage: python docs/presentation/make_frames.py
"""
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
a = np.fromfile(HERE / "barnard-coadds.f32", dtype="<f4").reshape(3, 6, 59, 59)
a = np.nan_to_num(a)
norm = np.array([np.percentile(a[0, b], 99.5) for b in range(6)])
img = (a / norm[None, :, None, None]).mean(axis=1)[:, ::-1, :]     # all detectors, north up
C, H = 29, 7                                                       # crop centre and half-size, pixels
crop = img[:, C - H:C + H + 1, C - H:C + H + 1]
SCALE = 48
N = crop.shape[1] * SCALE


def centroid(m):
    w = np.clip(m - np.percentile(m, 90), 0, None)
    ys, xs = np.indices(m.shape)
    return (xs * w).sum() / w.sum(), (ys * w).sum() / w.sum()


ref = centroid(crop[0])                                           # where the star was in August 2025


def mark(im):
    d = ImageDraw.Draw(im)
    x, y = (ref[0] + .5) * SCALE, (ref[1] + .5) * SCALE
    col, g, L, w = (255, 200, 69, 235), 70, 90, 5
    for (x0, y0, x1, y1) in [(x - g - L, y, x - g, y), (x + g, y, x + g + L, y), (x, y - g - L, x, y - g), (x, y + g, x, y + g + L)]:
        d.line([(x0, y0), (x1, y1)], fill=col, width=w)
    return im


def gray(m):
    top = crop[0].max()
    v = np.clip((m - 0.012 * top) / (0.9 * top), 0, 1) ** 0.5           # compact core, dark sky
    rgb = np.stack([5 + v * 231, 5 + v * 227, 5 + v * 218], axis=-1)   # black to the deck's warm white
    return Image.fromarray(rgb.astype(np.uint8)).resize((N, N), Image.NEAREST).convert("RGBA")


def diverging(m):
    lim = np.percentile(np.abs(m), 99.7)
    t = np.clip(m / lim, -1, 1)
    t = np.sign(t) * np.arcsinh(np.abs(t) * 6) / np.arcsinh(6)
    base, red, blue = np.array([10, 10, 10]), np.array([240, 102, 90]), np.array([61, 143, 239])
    rgb = np.where(t[..., None] >= 0, base + t[..., None] * (red - base), base + (-t[..., None]) * (blue - base))
    return Image.fromarray(rgb.astype(np.uint8)).resize((N, N), Image.NEAREST).convert("RGBA")


for i in range(3):
    mark(gray(crop[i])).convert("RGB").save(HERE / "img" / f"barnard-visit{i + 1}.png", optimize=True)
mark(diverging(crop[2] - crop[0])).convert("RGB").save(HERE / "img" / "barnard-difference.png", optimize=True)
print("frames written", N, "px; reference", [round(v, 2) for v in ref])
