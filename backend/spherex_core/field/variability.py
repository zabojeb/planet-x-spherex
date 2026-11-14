"""Variability search for every source in the field.

Each source is measured in every aligned single exposure: an aperture (r = 2 px, local annulus background)
or, for a saturated star, a ring just outside its saturated core (relative flux only, as the pipeline's ring
photometry). Movers are followed with their fitted motion. Fluxes are normalised by the source's own median
spectrum per wavelength bin (analysis.lightcurve), then summarised: reduced chi^2 about a constant, Stetson
J (correlated deviations of consecutive exposures), and the amplitude between epoch medians. The strongest
candidates get a Lomb-Scargle periodogram when the time coverage allows.

Exposure wavelengths are those at the sequence target; across a small field the linear variable filter
shifts them by under one spectral channel.
"""

from __future__ import annotations

import numpy as np

from ..analysis.lightcurve import relative_fluxes
from ..analysis.periodogram import periodogram
from ..bands import BANDS
from ..errors import NotFound
from ..pipeline import results
from .common import (BKG_ANNULUS, MJY_PER_UNIT, Check, FieldData, gather, no_check, stencil)

SYSTEMATIC = 0.03  # fractional floor (constant stars give reduced chi^2 ~ 1 with it)
MIN_POINTS = 20
MIN_AMP_APERTURE = 0.10
MIN_AMP_RING = 0.20  # ring photometry drifts up to ~14 % on constant stars
MIN_CHI2 = 4.0
MAX_SOURCES = 150
PERIODOGRAM_TOP = 5
EDGE = 7.0  # px: aperture and background annulus inside the image
MIN_EPOCHS_FOR_PERIOD = 5


def _positions(fd: FieldData, s: dict, mjds: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    m = s.get("motion")
    if not m or m.get("significance", 0) < 4:
        return np.full(len(mjds), s["x"]), np.full(len(mjds), s["y"])
    years = 2000.0 + (mjds - 51544.5) / 365.25
    dt = years - fd.t_ref
    k = 1.0 / 1000.0 / fd.grid.pixscale
    return s["x"] - m["pmra"] * dt * k, s["y"] + m["pmdec"] * dt * k


def _core_radius(stack: np.ndarray, x: float, y: float, max_r: int = 16) -> float:
    """Radius of the masked (saturated) core in the exposures around (x, y); 0 when the core is clean."""
    sample = stack[:: max(1, len(stack) // 60)]
    best = 0.0
    for r in range(0, max_r + 1):
        ring = stencil(r + 0.5, max(r - 0.5, 0.01)) if r else stencil(0.5)
        vals = gather(sample, [x], [y], ring)[:, 0, :]
        if np.mean(~np.isfinite(vals)) > 0.2:
            best = float(r)
    return best


def _metrics(rel: np.ndarray, err: np.ndarray, epochs: np.ndarray, mjd: np.ndarray, bands: np.ndarray) -> dict:
    sig = np.sqrt(err ** 2 + (SYSTEMATIC * rel) ** 2)
    ok = np.isfinite(rel) & np.isfinite(sig) & (sig > 0)
    rel, sig, epochs, mjd, bands = rel[ok], sig[ok], epochs[ok], mjd[ok], bands[ok]
    n = len(rel)
    if n < MIN_POINTS:
        return {"n": n}
    w = 1.0 / sig ** 2
    mean = float((rel * w).sum() / w.sum())
    chi2 = float((((rel - mean) / sig) ** 2).sum() / max(n - 1, 1))
    order = np.argsort(mjd)
    delta = np.sqrt(n / (n - 1)) * (rel[order] - mean) / sig[order]
    p = delta[:-1] * delta[1:]
    j = float(np.mean(np.sign(p) * np.sqrt(np.abs(p)))) if len(p) else 0.0
    meds, amp, amp_band = [], 0.0, None
    for band in np.unique(bands):
        sel = bands == band
        per = []
        for e in np.unique(epochs[sel]):
            v = rel[sel & (epochs == e)]
            if len(v) >= 5:
                per.append((int(e), float(np.median(v)), int(len(v))))
        meds += [(e, str(band), v, k) for e, v, k in per]
        if len(per) >= 2:
            a = max(p[1] for p in per) - min(p[1] for p in per)
            if a > amp:  # the largest change in any detector: colour changes cancel in a mixed median
                amp, amp_band = a, str(band)
    return {"n": n, "chi2_red": chi2, "stetson_j": j, "amplitude": float(amp), "amplitude_band": amp_band,
            "epoch_medians": [{"epoch": e, "band": b, "rel": v, "n": k} for e, b, v, k in meds]}


def search(fd: FieldData, sources: list[dict], check: Check = no_check) -> list[dict]:
    meta = fd.meta
    exposures = meta["exposures"]
    stacks, idxs = [], []
    for e in range(fd.coadds.shape[0]):
        for band in BANDS:
            try:
                st, idx = results.read_exposures(fd.ctx.dir, meta, e, band)
            except NotFound:
                continue
            stacks.append(st)
            idxs.extend(idx)
    if not stacks:
        return []
    stack = np.concatenate(stacks).astype(np.float32)
    mjds = np.array([exposures[i]["mjd"] for i in idxs])
    wls = np.array([exposures[i]["wl"] or 0.0 for i in idxs])
    bands = [exposures[i]["band"] for i in idxs]
    epochs = np.array([exposures[i]["epoch"] for i in idxs])
    out = []
    for s in sources[:MAX_SOURCES]:
        check()
        if s.get("blended_with") or min(s["x"], s["y"], fd.n - 1 - s["x"], fd.n - 1 - s["y"]) < EDGE:
            continue
        xs, ys = _positions(fd, s, mjds)
        core = _core_radius(stack, s["x"], s["y"]) if s.get("saturated") else 0.0
        if core > 0:
            r_in, r_out = core + 1.0, core + 4.0
            ap_off = stencil(r_out, r_in)
            ann_off = stencil(r_out + 5.0, r_out + 1.0)
            method = "ring"
        else:
            ap_off, ann_off, method = stencil(2.0), stencil(BKG_ANNULUS[1], BKG_ANNULUS[0]), "aperture"
        if ap_off[:, 0].max() >= fd.n // 2:
            continue
        if np.ptp(xs) < 0.5 and np.ptp(ys) < 0.5:  # static: one vectorised gather over all exposures
            ap = gather(stack, [xs[0]], [ys[0]], ap_off)[:, 0, :]
            ann = gather(stack, [xs[0]], [ys[0]], ann_off)[:, 0, :]
        else:
            ap = np.stack([gather(stack[k], [xs[k]], [ys[k]], ap_off)[0] for k in range(len(idxs))])
            ann = np.stack([gather(stack[k], [xs[k]], [ys[k]], ann_off)[0] for k in range(len(idxs))])
        with np.errstate(all="ignore"):
            bkg = np.nanmedian(ann, axis=1)
            noise = 1.4826 * np.nanmedian(np.abs(ann - bkg[:, None]), axis=1)
        good = np.isfinite(ap)
        npix = good.sum(axis=1)
        masked = 1.0 - npix / ap.shape[1]
        flux = (np.nansum(ap, axis=1) - bkg * npix) * MJY_PER_UNIT
        err = noise * np.sqrt(np.maximum(npix, 1)) * MJY_PER_UNIT
        records = [{"mjd": float(mjds[k]), "wl": float(wls[k]), "band": bands[k], "flux_mjy": float(flux[k]),
                    "err_mjy": float(err[k]), "flagged": bool(masked[k] > 0.2), "relative": method == "ring",
                    "epoch": int(epochs[k])}
                   for k in range(len(idxs)) if np.isfinite(flux[k]) and wls[k] > 0]
        if method == "aperture":
            records = [r for r in records if r["flux_mjy"] > 0]
        rel = relative_fluxes([r for r in records if not r["flagged"]])
        if len(rel) < MIN_POINTS:
            continue
        m = _metrics(np.array([r["rel"] for r in rel]),
                     np.array([r["rel_err"] if r["rel_err"] is not None else np.nan for r in rel]),
                     np.array([r["epoch"] for r in rel]), np.array([r["mjd"] for r in rel]),
                     np.array([r["band"] for r in rel]))
        s["variability"] = {k: v for k, v in m.items() if k != "epoch_medians"} | {"method": method}
        min_amp = MIN_AMP_RING if method == "ring" else MIN_AMP_APERTURE
        if m.get("n", 0) < MIN_POINTS or m["amplitude"] < min_amp or m["chi2_red"] < MIN_CHI2:
            continue
        out.append({"id": "", "source": s["id"], "x": s["x"], "y": s["y"], "ra": s["ra"], "dec": s["dec"],
                    "method": method, **m, "period": None,
                    "score": float(m["amplitude"] * np.sqrt(min(m["chi2_red"], 400.0)) * (1 + max(m["stetson_j"], 0))),
                    "_records": records})
    out.sort(key=lambda v: -v["score"])
    n_ep = len({int(e) for e in epochs})
    for i, v in enumerate(out):
        v["id"] = f"V{i + 1:03d}"
        recs = v.pop("_records")
        if i < PERIODOGRAM_TOP and n_ep >= MIN_EPOCHS_FOR_PERIOD:
            try:
                p = periodogram(recs, method="standard")
                v["period"] = {"best_period_days": p["best"]["period_days"], "power": p["best"]["power"],
                               "false_alarm_probability": p["best"]["false_alarm_probability"],
                               "peaks": p["peaks"][:3]}
            except (ValueError, KeyError) as e:
                v["period"] = {"error": str(e)}
    return out[:50]
