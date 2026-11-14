"""Proper-motion search over the whole field.

1. Every source bright enough gets a blind motion fit across epochs (analysis.motion: per-epoch centroids in
   the co-adds, one motion shared by all detectors). Significant movers are compared with Gaia/CatWISE:
   consistent ones are known movers; discrepant ones and movers without a catalog motion are candidates
   (nearby brown dwarfs, "Planet X"-type objects, or blends).
2. Faint movers that are not detected in single epochs: shift-and-stack of the static-subtracted epoch images
   over a grid of trial motions; a mover adds up at its true motion while static residuals do not.
"""

from __future__ import annotations

import numpy as np
from scipy.ndimage import shift as nd_shift

from ..analysis.motion import fit_motion
from .common import Check, FieldData, local_peaks, no_check, region_mask, robust_noise, saturated_regions
from .crossmatch import catalog_motion

FIT_MIN_SNR = 8.0
MAX_FITS = 200
MOVER_SIGMA = 4.0  # total motion / its error
MOVER_MIN_PM = 500.0  # mas/yr
DISCREPANT_SIGMA = 4.0
BAND_SIGMA = 3.0  # a supporting detector's own fit must be this significant...
BAND_ANGLE = 35.0  # ...and point within this angle of the combined motion
MAX_PM = 15000.0  # mas/yr; faster than any known star (Barnard's 10.4"/yr): a centroid jump, not a motion
STACK_SPEEDS = np.arange(1000.0, 15001.0, 1000.0)  # mas/yr
STACK_ANGLES = np.arange(0.0, 360.0, 15.0)  # deg, position angle east of north
STACK_THRESHOLD = 6.0
STACK_GAIN = 4.0  # the best trial motion must beat zero motion by this much S/N
STACK_CLEAR = 2.5  # px: a faint mover must not sit on a detected source (static residuals stack too)
BRIGHT_SNR = 50.0  # residuals of brighter stars are masked out of the search
BRIGHT_GROW = 4


def fit_all(fd: FieldData, sources: list[dict], check: Check = no_check) -> list[dict]:
    """Motion fits for the brightest sources (stored on each source) and the ranked mover candidates."""
    candidates = []
    regions = saturated_regions(fd)
    fitted = [s for s in sources if s["snr"] >= FIT_MIN_SNR and not s.get("blended_with")][:MAX_FITS]
    for s in fitted:
        check()
        try:
            fit = fit_motion(fd.meta, fd.coadds, fd.grid, s["x"], s["y"])
        except ValueError:
            continue
        c = fit["combined"]
        err = c.get("pm_total_err") or float(np.hypot(c["pmra_err"], c["pmdec_err"]))
        sig = c["pm_total"] / err if err else 0.0
        s["motion"] = {"pmra": c["pmra"], "pmdec": c["pmdec"], "pmra_err": c["pmra_err"], "pmdec_err": c["pmdec_err"],
                       "pm_total": c["pm_total"], "significance": float(sig), "n_epochs": c["n_epochs"],
                       "bands": c["bands"], "chi2_red": max(c["chi2_red_ra"], c["chi2_red_dec"])}
        if c["pm_total"] > MAX_PM:
            s["motion"]["implausible"] = True
            continue
        support = _band_support(c, fit["per_band"])
        s["motion"]["supporting_bands"] = support
        if sig < MOVER_SIGMA or c["pm_total"] < MOVER_MIN_PM:
            continue
        if len(support) < min(2, len(fit["per_band"])):
            s["motion"]["rejected"] = "detectors disagree"  # colour blends and halos shift per detector
            continue
        cat = catalog_motion(s)
        if cat is None:
            category, dsig = "unknown_mover", None
        else:
            dra = (c["pmra"] - cat["pmra"]) / max(c["pmra_err"], 1.0)
            ddec = (c["pmdec"] - cat["pmdec"]) / max(c["pmdec_err"], 1.0)
            dsig = float(np.hypot(dra, ddec))
            category = "known_mover" if dsig < DISCREPANT_SIGMA else "discrepant_motion"
        boost = {"unknown_mover": 2.0, "discrepant_motion": 1.5, "known_mover": 1.0}[category]
        near_sat = any(np.hypot(s["x"] - x, s["y"] - y) <= 2.0 * r for x, y, r in regions
                       if np.hypot(s["x"] - x, s["y"] - y) > 1.0)
        if near_sat:
            boost *= 0.3  # a bright star's halo changes with wavelength and visit; centroids follow it
        candidates.append({"id": f"M{len(candidates) + 1:03d}", "source": s["id"], "x": s["x"], "y": s["y"],
                           "ra": s["ra"], "dec": s["dec"], "category": category,
                           "score": float(sig * boost), "motion": s["motion"], "catalog_motion": cat,
                           "catalog_discrepancy_sigma": dsig, "saturated": s.get("saturated", False),
                           "near_saturated_star": bool(near_sat),
                           "track": fit["track"]})
    candidates.sort(key=lambda m: -m["score"])
    return candidates


def _band_support(c: dict, per_band: dict) -> list[str]:
    """Detectors whose own motion fit is significant and points the same way as the combined fit."""
    out = []
    pa = np.arctan2(c["pmra"], c["pmdec"])
    for b, f in per_band.items():
        err = f.get("pm_total_err") or float(np.hypot(f["pmra_err"], f["pmdec_err"]))
        if not err or f["pm_total"] / err < BAND_SIGMA:
            continue
        d = abs((np.degrees(np.arctan2(f["pmra"], f["pmdec"]) - pa) + 180.0) % 360.0 - 180.0)
        if d <= BAND_ANGLE:
            out.append(b)
    return out


def _difference_snr(fd: FieldData) -> tuple[list[np.ndarray], np.ndarray]:
    """Per-epoch combined S/N of (co-add - median over epochs), summed over detectors."""
    n_ep = fd.coadds.shape[0]
    z = [np.zeros((fd.n, fd.n)) for _ in range(n_ep)]
    cnt = [np.zeros((fd.n, fd.n)) for _ in range(n_ep)]
    for b in fd.bands_present():
        cube = fd.coadds[:, b].astype(float)
        have = [e for e in range(n_ep) if np.isfinite(cube[e]).any()]
        if len(have) < 2:
            continue
        with np.errstate(all="ignore"):
            med = np.nanmedian(cube[have], axis=0)
        for e in have:
            d = cube[e] - med
            _, noise = robust_noise(d)
            if noise > 0:
                ok = np.isfinite(d)
                z[e][ok] += d[ok] / noise
                cnt[e][ok] += 1
    out = []
    for e in range(n_ep):
        with np.errstate(all="ignore"):
            out.append(np.where(cnt[e] > 0, z[e] / np.sqrt(np.maximum(cnt[e], 1)), np.nan))
    return out, np.array([c.max() > 0 for c in cnt])


def shift_and_stack(fd: FieldData, sources: list[dict], movers: list[dict], check: Check = no_check) -> list[dict]:
    """Candidates whose stacked S/N peaks at a non-zero trial motion (bright-star zones excluded)."""
    z, have = _difference_snr(fd)
    epochs = [e for e in range(len(z)) if have[e]]
    if len(epochs) < 3:
        return []  # with two epochs every pair of residuals "moves"; the search needs three or more
    regions = saturated_regions(fd)
    regions += [(s["x"], s["y"], BRIGHT_GROW + 2.0 * np.log10(max(s["snr"], 1.0) / 10.0))
                for s in sources if s["snr"] >= BRIGHT_SNR]
    bright = region_mask(fd.n, regions, scale=1.5, extra=1.0)
    z = [np.where(bright, np.nan, zi) for zi in z]
    px_per_mas = 1.0 / 1000.0 / fd.grid.pixscale

    def stack_at(pmra: float, pmdec: float) -> np.ndarray:
        acc = np.zeros((fd.n, fd.n))
        k = np.zeros((fd.n, fd.n))
        for e in epochs:
            dt = fd.years[e] - fd.t_ref
            # a mover sits at x0 - pmra*dt (east is left), y0 + pmdec*dt: shift it back to x0, y0
            sh = (-pmdec * dt * px_per_mas, pmra * dt * px_per_mas)
            img = nd_shift(np.nan_to_num(z[e], nan=0.0), sh, order=1, mode="constant", cval=0.0)
            ok = nd_shift(np.isfinite(z[e]).astype(float), sh, order=1, mode="constant", cval=0.0) > 0.99
            acc[ok] += img[ok]
            k[ok] += 1
        return np.where(k >= 3, acc / np.sqrt(np.maximum(k, 1)), np.nan)

    zero = stack_at(0.0, 0.0)
    best = np.full((fd.n, fd.n), -np.inf)
    best_v = np.zeros((fd.n, fd.n, 2))
    for speed in STACK_SPEEDS:
        for pa in STACK_ANGLES:
            check()
            pmra, pmdec = speed * np.sin(np.radians(pa)), speed * np.cos(np.radians(pa))
            st = stack_at(pmra, pmdec)
            better = np.nan_to_num(st, nan=-np.inf) > best
            best[better] = st[better]
            best_v[better] = (pmra, pmdec)
    snr = np.where(np.isfinite(best), best, np.nan)
    out = []
    for y, x in zip(*local_peaks(snr, STACK_THRESHOLD, size=5, edge=3)):
        gain = float(snr[y, x] - (zero[y, x] if np.isfinite(zero[y, x]) else 0.0))
        if gain < STACK_GAIN or any(np.hypot(s["x"] - x, s["y"] - y) <= STACK_CLEAR for s in sources):
            continue
        pmra, pmdec = best_v[y, x]
        ra, dec = fd.sky(x, y)
        near = [m for m in movers if np.hypot(m["x"] - x, m["y"] - y) <= 3.0]
        near_src = min(sources, key=lambda s: np.hypot(s["x"] - x, s["y"] - y), default=None)
        edge_speed = bool(np.hypot(pmra, pmdec) >= STACK_SPEEDS[-1] - 1)
        out.append({"id": "", "x": float(x), "y": float(y), "ra": float(ra), "dec": float(dec),
                    "snr": float(snr[y, x]), "gain_over_static": gain, "pmra": float(pmra), "pmdec": float(pmdec),
                    "pm_total": float(np.hypot(pmra, pmdec)), "at_speed_limit": edge_speed,
                    "mover": near[0]["id"] if near else None,
                    "nearest_source": near_src["id"] if near_src is not None
                    and np.hypot(near_src["x"] - x, near_src["y"] - y) <= 2.0 else None,
                    "score": float(snr[y, x]) * (0.5 if edge_speed else 1.0)})
    out.sort(key=lambda c: -c["score"])
    for i, c in enumerate(out):
        c["id"] = f"F{i + 1:03d}"
    return out[:50]
