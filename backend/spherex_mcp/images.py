"""Small PNG renderings of co-adds for an AI agent to look at. No imaging dependencies: numpy + zlib only."""

from __future__ import annotations

import struct
import zlib

import numpy as np

MAX_SIDE = 512  # rendered images are upscaled (nearest) to about this many pixels per panel


def encode_png(rgb: np.ndarray) -> bytes:
    """uint8 [h, w] (grey) or [h, w, 3] (RGB) -> PNG bytes."""
    a = np.ascontiguousarray(rgb, dtype=np.uint8)
    if a.ndim == 2:
        colour, rows = 0, a
    else:
        colour, rows = 2, a.reshape(a.shape[0], -1)
    h, w = a.shape[:2]
    raw = b"".join(b"\x00" + rows[y].tobytes() for y in range(h))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, colour, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def limits(stack: np.ndarray, lo: float = 1.0, hi: float = 99.7) -> tuple[float, float]:
    """Percentile limits over every finite pixel of all given planes, so frames blink at one brightness."""
    v = stack[np.isfinite(stack)]
    if v.size == 0:
        return 0.0, 1.0
    a, b = np.percentile(v, [lo, hi])
    return float(a), float(b if b > a else a + 1e-9)


def stretch(img: np.ndarray, lo: float, hi: float, kind: str = "asinh") -> np.ndarray:
    """Float image -> 0..1 (NaN stays NaN)."""
    t = np.clip((img - lo) / (hi - lo), 0.0, 1.0)
    if kind == "asinh":
        t = np.arcsinh(10.0 * t) / np.arcsinh(10.0)
    elif kind == "log":
        t = np.log1p(1000.0 * t) / np.log1p(1000.0)
    return t


def to_grey(t: np.ndarray) -> np.ndarray:
    """0..1 with NaN -> uint8 grey with no-data as a dark slate."""
    out = np.where(np.isfinite(t), t * 255.0, 24.0)
    return np.flipud(out).astype(np.uint8)  # north up: row 0 of the grid is south


def diverging(d: np.ndarray, scale: float) -> np.ndarray:
    """Difference image -> RGB: positive (new) red, negative (old) blue, zero dark grey."""
    t = np.clip(np.nan_to_num(d / max(scale, 1e-12)), -1.0, 1.0)
    base = 40.0
    r = base + np.clip(t, 0, 1) * (255 - base)
    b = base + np.clip(-t, 0, 1) * (255 - base)
    g = base + np.abs(t) * 30.0
    rgb = np.stack([r, g, b], axis=-1)
    rgb[~np.isfinite(d)] = 20.0
    return np.flipud(rgb).astype(np.uint8)


def upscale(a: np.ndarray, side: int = MAX_SIDE) -> np.ndarray:
    f = max(1, side // max(a.shape[0], a.shape[1]))
    return np.repeat(np.repeat(a, f, axis=0), f, axis=1)


def mark(a: np.ndarray, x: float, y: float, n: int, colour=(255, 200, 69)) -> np.ndarray:
    """Draw an open crosshair (gap in the middle) at grid pixel (x, y) on an upscaled north-up image."""
    if not (np.isfinite(x) and np.isfinite(y)):
        return a
    out = a if a.ndim == 3 else np.repeat(a[..., None], 3, axis=2)
    out = out.copy()
    f = out.shape[0] / n
    cx, cy = int((x + 0.5) * f), int((n - 1 - y + 0.5) * f)
    g, arm = max(3, int(2 * f)), max(6, int(4 * f))
    h, w = out.shape[:2]
    for d in range(g, g + arm):
        for px, py in ((cx + d, cy), (cx - d, cy), (cx, cy + d), (cx, cy - d)):
            if 0 <= px < w and 0 <= py < h:
                out[py, px] = colour
    return out


def hstack(panels: list[np.ndarray], gap: int = 4) -> np.ndarray:
    """Side-by-side panels (same height) separated by a thin amber line."""
    rgb = [p if p.ndim == 3 else np.repeat(p[..., None], 3, axis=2) for p in panels]
    h = max(p.shape[0] for p in rgb)
    sep = np.zeros((h, gap, 3), np.uint8)
    sep[..., 0], sep[..., 1], sep[..., 2] = 255, 200, 69
    parts = []
    for i, p in enumerate(rgb):
        if i:
            parts.append(sep)
        parts.append(p)
    return np.concatenate(parts, axis=1)
