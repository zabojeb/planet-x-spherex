"""Real/bogus scoring of candidate detections in difference images.

Features are measured on the S/N image of a difference (single exposure minus its epoch co-add) around the
peak. The model is gradient boosting with decision stumps (depth-1 trees, log-loss), implemented here in
NumPy so the service needs no ML dependency; it is small enough to read (models/realbogus.json lists every
stump). Training: field.train_realbogus injects synthetic point sources into real difference images
(positives) and takes the detections found in the same images as negatives (almost all are residuals of
stars, cosmic rays and artifacts), holding out whole fields for testing.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import numpy as np

from ..flags import ARTIFACTS, MASK_DEFAULT, SATURATION

FEATURES = ("log_snr", "sharpness", "roundness", "dipole", "bright_dist", "flagged", "masked_frac", "edge_dist",
            "partner")
MODEL_PATH = Path(__file__).with_name("models") / "realbogus.json"
BAD_BITS = ARTIFACTS | MASK_DEFAULT | SATURATION


def features(s: np.ndarray, flags: np.ndarray | None, x: int, y: int, bright_xy: np.ndarray | None,
             partner: float = 0.5) -> np.ndarray:
    """Feature vector at integer peak (x, y) of S/N image s; `partner` is the simultaneous-pair check
    (1 seen in the partner detector, 0 not seen, 0.5 unknown)."""
    n_y, n_x = s.shape
    peak = float(s[y, x])

    def box(r: int) -> np.ndarray:
        return s[max(y - r, 0):y + r + 1, max(x - r, 0):x + r + 1]

    b1, b2, b3 = box(1), box(2), box(3)
    pos = np.clip(np.nan_to_num(b2, nan=0.0), 0, None)
    fin = np.isfinite(b1)
    # masked neighbours (bright-star mask) would inflate sharpness: scale the finite ones up to all eight
    total = float(np.nansum(np.clip(b1, 0, None)))
    if 1 < fin.sum() < b1.size:
        total = peak + (total - peak) * (b1.size - 1) / (fin.sum() - 1)
    sharp = peak / total if total > 0 else 1.0
    if pos.sum() > 0:
        yy, xx = np.mgrid[0:pos.shape[0], 0:pos.shape[1]]
        w = pos / pos.sum()
        mx, my = (xx * w).sum(), (yy * w).sum()
        cxx, cyy, cxy = ((xx - mx) ** 2 * w).sum(), ((yy - my) ** 2 * w).sum(), ((xx - mx) * (yy - my) * w).sum()
        ev = np.linalg.eigvalsh(np.array([[cxx, cxy], [cxy, cyy]]))
        round_ = float(ev[0] / ev[1]) if ev[1] > 0 else 0.0
    else:
        round_ = 0.0
    dipole = float(np.nanmin(b3) / peak) if peak > 0 and np.isfinite(b3).any() else 0.0
    if bright_xy is not None and len(bright_xy):
        bd = float(np.min(np.hypot(bright_xy[:, 0] - x, bright_xy[:, 1] - y)))
    else:
        bd = 15.0
    flagged = 0.0
    if flags is not None:
        fb = flags[max(y - 1, 0):y + 2, max(x - 1, 0):x + 2].astype(np.int64)
        flagged = float(bool(np.bitwise_or.reduce(fb.ravel()) & BAD_BITS))
    masked = float(np.mean(~np.isfinite(b2)))
    edge = float(min(x, y, n_x - 1 - x, n_y - 1 - y))
    return np.array([np.log10(max(peak, 1e-3)), sharp, round_, dipole, min(bd, 15.0), flagged, masked,
                     min(edge, 10.0), float(partner)])


# ------------------------------------------------------------------ gradient-boosted stumps
def _sigmoid(f: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(f, -30, 30)))


def fit(x: np.ndarray, y: np.ndarray, rounds: int = 80, rate: float = 0.3, n_thresholds: int = 32) -> dict:
    """Boosted stumps for log-loss (Newton leaf values). Returns a JSON-serialisable model."""
    y = y.astype(float)
    p0 = float(np.clip(y.mean(), 1e-3, 1 - 1e-3))
    f = np.full(len(y), np.log(p0 / (1 - p0)))
    thresholds = [np.unique(np.quantile(x[:, j], np.linspace(0.02, 0.98, n_thresholds))) for j in range(x.shape[1])]
    stumps = []
    for _ in range(rounds):
        p = _sigmoid(f)
        g, h = y - p, np.maximum(p * (1 - p), 1e-6)
        best = None
        for j in range(x.shape[1]):
            for t in thresholds[j]:
                left = x[:, j] <= t
                gl, hl = g[left].sum(), h[left].sum()
                gr, hr = g[~left].sum(), h[~left].sum()
                gain = gl * gl / (hl + 1e-9) + gr * gr / (hr + 1e-9)
                if best is None or gain > best[0]:
                    best = (gain, j, float(t), float(gl / (hl + 1e-9)), float(gr / (hr + 1e-9)))
        _, j, t, vl, vr = best
        f += rate * np.where(x[:, j] <= t, vl, vr)
        stumps.append({"feature": FEATURES[j], "threshold": t, "left": rate * vl, "right": rate * vr})
    return {"kind": "boosted-stumps", "features": list(FEATURES), "bias": float(np.log(p0 / (1 - p0))),
            "stumps": stumps}


def predict(model: dict, x: np.ndarray) -> np.ndarray:
    x = np.atleast_2d(x)
    idx = {name: i for i, name in enumerate(model["features"])}
    f = np.full(x.shape[0], model["bias"])
    for s in model["stumps"]:
        f += np.where(x[:, idx[s["feature"]]] <= s["threshold"], s["left"], s["right"])
    return _sigmoid(f)


@lru_cache(maxsize=1)
def load_model() -> dict | None:
    try:
        model = json.loads(MODEL_PATH.read_text())
    except (OSError, ValueError):
        return None
    return model if model.get("features") == list(FEATURES) else None  # stale model: features changed


def score(x: np.ndarray) -> np.ndarray:
    """Probability of being a real point source; a heuristic when no trained model is installed."""
    model = load_model()
    x = np.atleast_2d(x)
    if model is not None:
        return predict(model, x)
    sharp_ok = (x[:, 1] > 0.15) & (x[:, 1] < 0.6)
    partner = x[:, 8] if x.shape[1] > 8 else np.full(len(x), 0.5)
    return _sigmoid(2.0 * (x[:, 0] - np.log10(6)) * 3 + 2.0 * sharp_ok - 3.0 * x[:, 5] - 4.0 * x[:, 6]
                    + 0.3 * np.minimum(x[:, 4], 6) + 1.5 * (partner - 0.5) - 1.5)


def importance(model: dict) -> dict[str, float]:
    """Share of the total absolute leaf contribution per feature (an explanation of what the model uses)."""
    tot: dict[str, float] = {f: 0.0 for f in model["features"]}
    for s in model["stumps"]:
        tot[s["feature"]] += abs(s["left"]) + abs(s["right"])
    z = sum(tot.values()) or 1.0
    return {k: v / z for k, v in sorted(tot.items(), key=lambda kv: -kv[1])}
