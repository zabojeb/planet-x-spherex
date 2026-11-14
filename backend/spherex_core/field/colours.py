"""Colour searches: red excess (cold brown dwarfs) and an explainable anomaly score.

Red excess: flux at 3.8-5 um (D5 + D6) against flux below 2.4 um (D1 + D2 + D3). Ordinary stars follow a
Rayleigh-Jeans tail (ratio ~0.1-0.3 in flux density); T and Y dwarfs are bright at 4-5 um and faint below
2.5 um (eps Indi B ~1.5, WISE 0855 far higher). A source with a strong long-wavelength detection and no
short-wavelength one is a candidate even when the ratio is undefined. Sources without a Gaia counterpart
are marked: cold brown dwarfs are usually too faint for Gaia.

Anomaly: an isolation forest (implemented here in NumPy, 200 trees) on the log flux ratios of adjacent
detectors for well-measured sources. The explanation lists the ratios that deviate most from the field's
robust median, in units of its robust scatter.
"""

from __future__ import annotations

import numpy as np

from ..bands import BANDS

LONG = ("D5", "D6")
SHORT = ("D1", "D2", "D3")
RED_RATIO = 0.8
LONG_SNR = 5.0
SHORT_SNR_ABSENT = 3.0
ANOMALY_SNR = 10.0
ANOMALY_THRESHOLD = 0.62
RATIO_NAMES = [f"{BANDS[i + 1]}/{BANDS[i]}" for i in range(len(BANDS) - 1)]


def _sum(s: dict, bands: tuple[str, ...]) -> tuple[float, float, int]:
    f = [s["flux_mjy"].get(b) for b in bands]
    e = [s["flux_err_mjy"].get(b) for b in bands]
    ok = [(fi, ei) for fi, ei in zip(f, e) if fi is not None and ei]
    if not ok:
        return float("nan"), float("nan"), 0
    return float(sum(fi for fi, _ in ok)), float(np.sqrt(sum(ei ** 2 for _, ei in ok))), len(ok)


def red_excess(sources: list[dict]) -> list[dict]:
    out = []
    for s in sources:
        fl, el, nl = _sum(s, LONG)
        fs, es, ns = _sum(s, SHORT)
        if nl == 0 or not (el > 0):
            continue
        snr_l = fl / el
        snr_s = fs / es if ns and es > 0 else 0.0
        ratio = fl / fs if ns and fs > 0 else None
        if snr_l < LONG_SNR:
            continue
        absent = snr_s < SHORT_SNR_ABSENT
        if not absent and (ratio is None or ratio < RED_RATIO):
            continue
        no_gaia = not (s.get("matches") or {}).get("gaia")
        score = snr_l / 5.0 * (2.0 if absent else np.log10(max(ratio or 1.0, 1.0)) + 1.0) * (1.5 if no_gaia else 1.0)
        out.append({"id": "", "source": s["id"], "x": s["x"], "y": s["y"], "ra": s["ra"], "dec": s["dec"],
                    "ratio_long_short": ratio, "snr_long": float(snr_l), "snr_short": float(snr_s),
                    "short_absent": bool(absent), "no_gaia": bool(no_gaia), "flux_mjy": s["flux_mjy"],
                    "saturated": s.get("saturated", False), "score": float(score)})
    out.sort(key=lambda c: -c["score"])
    for i, c in enumerate(out):
        c["id"] = f"R{i + 1:03d}"
    return out[:50]


# ------------------------------------------------------------------ isolation forest
def _c(n: float) -> float:
    """Average path length of an unsuccessful search in a binary search tree of n points."""
    if n <= 1:
        return 0.0
    return 2.0 * (np.log(n - 1) + 0.5772156649) - 2.0 * (n - 1) / n


def _grow(x: np.ndarray, depth: int, limit: int, rng: np.random.Generator):
    if depth >= limit or len(x) <= 1:
        return ("leaf", len(x))
    j = int(rng.integers(x.shape[1]))
    lo, hi = x[:, j].min(), x[:, j].max()
    if hi <= lo:
        return ("leaf", len(x))
    t = float(rng.uniform(lo, hi))
    left = x[:, j] < t
    return ("node", j, t, _grow(x[left], depth + 1, limit, rng), _grow(x[~left], depth + 1, limit, rng))


def _path(tree, v: np.ndarray, depth: int = 0) -> float:
    while tree[0] == "node":
        _, j, t, l, r = tree
        tree = l if v[j] < t else r
        depth += 1
    return depth + _c(tree[1])


def isolation_scores(x: np.ndarray, n_trees: int = 200, sample: int = 256, seed: int = 0) -> np.ndarray:
    """Anomaly score in (0, 1); ~0.5 ordinary, -> 1 isolated (Liu, Ting & Zhou 2008)."""
    rng = np.random.default_rng(seed)
    n = len(x)
    m = min(sample, n)
    limit = int(np.ceil(np.log2(max(m, 2))))
    trees = [_grow(x[rng.choice(n, m, replace=False)], 0, limit, rng) for _ in range(n_trees)]
    depth = np.array([np.mean([_path(t, v) for t in trees]) for v in x])
    return 2.0 ** (-depth / _c(m))


def anomalies(sources: list[dict]) -> tuple[list[dict], dict]:
    rows, keep = [], []
    for s in sources:
        f = [s["flux_mjy"].get(b) for b in BANDS]
        snr = [s["snr_band"].get(b) for b in BANDS]
        good = [fi is not None and fi > 0 and (si or 0) >= ANOMALY_SNR for fi, si in zip(f, snr)]
        if sum(good) < 4 or s.get("saturated"):
            continue
        r = [np.log10(f[i + 1] / f[i]) if good[i] and good[i + 1] else np.nan for i in range(len(BANDS) - 1)]
        rows.append(r)
        keep.append(s)
    info = {"sources_scored": len(keep)}
    if len(keep) < 20:
        info["note"] = "fewer than 20 well-measured sources; anomaly scores skipped"
        return [], info
    x = np.array(rows, float)
    med = np.nanmedian(x, axis=0)
    mad = 1.4826 * np.nanmedian(np.abs(x - med), axis=0)
    mad = np.where(mad > 0, mad, 0.05)
    xf = np.where(np.isfinite(x), x, med)
    z = (xf - med) / mad
    scores = isolation_scores(z)
    out = []
    for s, sc, zr in zip(keep, scores, z):
        if sc < ANOMALY_THRESHOLD:
            continue
        top = np.argsort(-np.abs(zr))[:2]
        out.append({"id": "", "source": s["id"], "x": s["x"], "y": s["y"], "ra": s["ra"], "dec": s["dec"],
                    "score": float(sc),
                    "explain": [{"ratio": RATIO_NAMES[k], "z": float(zr[k]),
                                 "direction": "high" if zr[k] > 0 else "low"} for k in top],
                    "no_gaia": not (s.get("matches") or {}).get("gaia")})
    out.sort(key=lambda c: -c["score"])
    for i, c in enumerate(out):
        c["id"] = f"A{i + 1:03d}"
    info["threshold"] = ANOMALY_THRESHOLD
    return out[:30], info
