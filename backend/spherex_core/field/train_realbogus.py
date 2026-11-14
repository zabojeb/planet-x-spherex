"""Train the real/bogus model on injected sources and real difference-image detections.

    python -m spherex_core.field.train_realbogus <sequence id> [...] [--out path]

Positives: point sources (Gaussian, FWHM 1.2-2.0 px, peak S/N 6-30) injected into real exposure-minus-co-add
S/N maps away from bright stars, with a fainter or brighter copy in the simultaneous partner exposure (a real
object is seen by both detectors of a pair unless it is faint at one wavelength). Negatives: the detections our
own search finds in the same maps; nearly all are star residuals, cosmic rays and artefacts (the rare real
asteroid among them only makes the estimate conservative). Validation holds out one field at a time
(leave-one-sky-region-out) and reports precision and recall at p >= 0.5.
"""

from __future__ import annotations

import argparse
import json
import sys

import numpy as np

from ..analysis.common import load_context
from ..bands import BANDS
from . import realbogus
from .common import load_field, local_peaks
from .transients import DETECT_SNR, MAX_SHARPNESS, difference_maps, partner_index, partner_seen

N_INJECT = 1500
MAX_NEG = 4000


def field_samples(seq_id: str, rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    fd = load_field(load_context(seq_id))
    maps = difference_maps(fd)
    ctx = maps.pop(None)
    partners = partner_index(fd.meta)
    neg = []
    for i, (s, e, b, _) in maps.items():
        c = ctx[(e, b)]
        for y, x in zip(*local_peaks(s, DETECT_SNR, size=5, edge=3)):
            f = realbogus.features(s, fd.flags[e, b], int(x), int(y), c["bright_xy"],
                                   partner_seen(maps, partners, i, int(x), int(y)))
            if f[1] <= MAX_SHARPNESS:
                neg.append(f)
    if len(neg) > MAX_NEG:
        neg = [neg[k] for k in rng.choice(len(neg), MAX_NEG, replace=False)]
    keys = list(maps)
    pos = []
    n = fd.n
    yy, xx = np.mgrid[0:n, 0:n]
    tries = 0
    while len(pos) < N_INJECT and tries < N_INJECT * 10:
        tries += 1
        i = keys[int(rng.integers(len(keys)))]
        s, e, b, _ = maps[i]
        x0, y0 = rng.uniform(4, n - 5, 2)
        if not np.isfinite(s[int(round(y0)), int(round(x0))]):
            continue
        sigma = rng.uniform(1.2, 2.0) / 2.355
        amp = rng.uniform(DETECT_SNR, 30.0)
        psf = np.exp(-((xx - x0) ** 2 + (yy - y0) ** 2) / (2 * sigma ** 2))
        si = s + amp * psf
        trial = dict(maps)
        j = partners.get(i)
        if j is not None and j in maps:
            sp = maps[j][0] + amp * rng.uniform(0.0, 1.5) * psf
            trial[j] = (sp, *maps[j][1:])
        trial[i] = (si, e, b, maps[i][3])
        xi, yi = int(round(x0)), int(round(y0))
        win = si[yi - 1:yi + 2, xi - 1:xi + 2]
        if not np.isfinite(win).any():
            continue
        dy, dx = np.unravel_index(np.nanargmax(win), win.shape)
        px, py = xi - 1 + dx, yi - 1 + dy
        if si[py, px] < DETECT_SNR:
            continue
        f = realbogus.features(si, fd.flags[e, b], int(px), int(py), ctx[(e, b)]["bright_xy"],
                               partner_seen(trial, partners, i, int(px), int(py)))
        if f[1] <= MAX_SHARPNESS:
            pos.append(f)
    x = np.array(pos + neg)
    y = np.array([1] * len(pos) + [0] * len(neg))
    print(f"{seq_id}: {len(pos)} injected, {len(neg)} real detections", file=sys.stderr)
    return x, y


def precision_recall(y: np.ndarray, p: np.ndarray, threshold: float = 0.5) -> tuple[float, float]:
    pred = p >= threshold
    tp = int((pred & (y == 1)).sum())
    fp = int((pred & (y == 0)).sum())
    fn = int((~pred & (y == 1)).sum())
    return tp / max(tp + fp, 1), tp / max(tp + fn, 1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("sequences", nargs="+")
    ap.add_argument("--out", default=str(realbogus.MODEL_PATH))
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    rng = np.random.default_rng(a.seed)
    data = {s: field_samples(s, rng) for s in a.sequences}
    folds = []
    for held in a.sequences:
        xtr = np.concatenate([data[s][0] for s in a.sequences if s != held])
        ytr = np.concatenate([data[s][1] for s in a.sequences if s != held])
        model = realbogus.fit(xtr, ytr)
        xte, yte = data[held]
        prec, rec = precision_recall(yte, realbogus.predict(model, xte))
        folds.append({"held_out": held, "precision": prec, "recall": rec, "n_pos": int(yte.sum()),
                      "n_neg": int((yte == 0).sum())})
        print(f"held out {held}: precision {prec:.3f} recall {rec:.3f}", file=sys.stderr)
    xall = np.concatenate([d[0] for d in data.values()])
    yall = np.concatenate([d[1] for d in data.values()])
    model = realbogus.fit(xall, yall)
    model["validation"] = {"method": "leave-one-field-out, threshold 0.5", "folds": folds,
                           "mean_precision": float(np.mean([f["precision"] for f in folds])),
                           "mean_recall": float(np.mean([f["recall"] for f in folds])),
                           "note": "negatives are real detections assumed bogus; precision is a lower bound"}
    model["training"] = {"sequences": a.sequences, "positives": int(yall.sum()), "negatives": int((yall == 0).sum()),
                         "bands": list(BANDS)}
    with open(a.out, "w") as fh:
        json.dump(model, fh, indent=1)
    print(json.dumps(model["validation"], indent=1))


if __name__ == "__main__":
    main()
