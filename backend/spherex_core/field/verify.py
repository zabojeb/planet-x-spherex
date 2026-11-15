"""Verification of a candidate on the native exposures (Astrometrica's Object Verification window).

For each exposure near the candidate's time, the detector's ePSF is fitted with free position, flux and
background (weights 1/VARIANCE, masked and saturated pixels excluded) on the cached native cutout, at the
position predicted from (ra, dec, mjd_ref) and an optional motion (\"/h and PA). The report gives position
and its sigma (from the fit covariance) through the exposure WCS, flux, SNR, reduced chi^2 and PSF weight,
plus small "stare" (fixed sky position) and "track" (following the motion) cutout strips from the aligned
exposures, so a person can judge the candidate.
"""

from __future__ import annotations

import asyncio

import numpy as np
from scipy.optimize import least_squares

from .. import cache
from ..analysis.common import SequenceContext, cutout_half, sequence_frames
from ..config import settings
from ..flags import MASK_DEFAULT, SATURATION
from ..io import Level2Reader, RangeClient
from ..photometry import mjy_per_unit, psf_model
from ..pipeline import results
from .common import FieldData

FIT_HALF = 3  # 7x7 native pixels
STRIP_HALF = 5  # 11x11 grid pixels


def _fit(image, variance, flags, x0, y0, psf_os, oversampling, omega) -> dict | None:
    ny, nx = image.shape
    xi, yi = int(round(x0)), int(round(y0))
    ys, xs = slice(max(yi - FIT_HALF, 0), min(yi + FIT_HALF + 1, ny)), slice(max(xi - FIT_HALF, 0),
                                                                            min(xi + FIT_HALF + 1, nx))
    data = image[ys, xs].astype(float)
    var = variance[ys, xs].astype(float)
    bad = ((flags[ys, xs].astype(np.int64) & (MASK_DEFAULT | SATURATION)) != 0) | ~np.isfinite(data) \
        | ~np.isfinite(var) | (var <= 0)
    gy, gx = np.mgrid[ys, xs]
    use = ~bad
    if use.sum() < 8:
        return None
    sig = np.sqrt(var[use])

    def model(p):
        x, y, f, b = p
        return f * psf_model(psf_os, oversampling, gx[use] - x, gy[use] - y, x - np.round(x), y - np.round(y)) + b

    f0 = max(float(np.nansum(data[use] - np.nanmedian(data[use]))), 1e-3)
    p0 = [x0, y0, f0, float(np.nanmedian(data[use]))]
    try:
        r = least_squares(lambda p: (data[use] - model(p)) / sig, p0, x_scale=[0.3, 0.3, f0, 0.1])
    except (ValueError, np.linalg.LinAlgError):
        return None
    dof = max(int(use.sum()) - 4, 1)
    chi2 = float((r.fun ** 2).sum() / dof)
    try:
        cov = np.linalg.inv(r.jac.T @ r.jac) * max(chi2, 1.0)
    except np.linalg.LinAlgError:
        return None
    x, y, f, b = r.x
    if abs(x - x0) > 2.5 or abs(y - y0) > 2.5:
        return None
    scale = mjy_per_unit(omega)
    ferr = float(np.sqrt(cov[2, 2]))
    return {"x": float(x), "y": float(y), "sigma_x": float(np.sqrt(cov[0, 0])), "sigma_y": float(np.sqrt(cov[1, 1])),
            "flux_mjy": float(f * scale), "err_mjy": ferr * scale, "snr": float(f / ferr) if ferr > 0 else None,
            "bkg": float(b), "chi2_red": chi2, "npix": int(use.sum())}


def _strip(img: np.ndarray, x: float, y: float) -> list | None:
    n = img.shape[0]
    xi, yi = int(round(x)), int(round(y))
    out = np.full((2 * STRIP_HALF + 1, 2 * STRIP_HALF + 1), np.nan, np.float32)
    y0, y1 = max(yi - STRIP_HALF, 0), min(yi + STRIP_HALF + 1, n)
    x0, x1 = max(xi - STRIP_HALF, 0), min(xi + STRIP_HALF + 1, n)
    if y1 <= y0 or x1 <= x0:
        return None
    out[y0 - (yi - STRIP_HALF):y1 - (yi - STRIP_HALF), x0 - (xi - STRIP_HALF):x1 - (xi - STRIP_HALF)] = img[y0:y1, x0:x1]
    return [[None if not np.isfinite(v) else round(float(v), 5) for v in row] for row in out]


async def verify(ctx: SequenceContext, fd: FieldData, ra: float, dec: float, mjd_ref: float | None = None,
                 rate: float = 0.0, pa: float = 0.0, epoch: int | None = None, max_exposures: int = 40) -> dict:
    exposures = ctx.meta["exposures"]
    cand = [e for e in exposures if epoch is None or e["epoch"] == epoch]
    if not cand:
        raise ValueError("no exposures in this epoch")
    mjd_ref = float(mjd_ref if mjd_ref is not None else np.median([e["mjd"] for e in cand]))
    cand = sorted(cand, key=lambda e: abs(e["mjd"] - mjd_ref))[:max_exposures]
    cand.sort(key=lambda e: e["mjd"])
    frames = await asyncio.to_thread(sequence_frames, ctx)
    half = cutout_half(ctx.grid)
    cosd = np.cos(np.radians(dec))

    def sky_at(mjd: float) -> tuple[float, float]:
        dt = (mjd - mjd_ref) * 24.0
        return (ra + rate * np.sin(np.radians(pa)) * dt / 3600.0 / cosd,
                dec + rate * np.cos(np.radians(pa)) * dt / 3600.0)

    rows: list[dict] = []
    async with RangeClient(settings.max_connections) as client:
        reader = Level2Reader(client, cache.layout().root)

        async def one(e: dict) -> None:
            f = frames[e["index"]]
            if f is None:
                return
            cut = await reader.cutout(f.s3_url, ctx.grid.ra, ctx.grid.dec, half)
            if cut is None:
                return
            epsf = await reader.epsf_table(f.s3_url, f.detector)
            ra_t, dec_t = sky_at(e["mjd"])
            xf, yf = (float(v) for v in cut.wcs.all_world2pix(ra_t, dec_t, 0, quiet=True))
            psf_os, _ = epsf.nearest(xf, yf)
            omega = float(cut.header.get("OMEGA_MEDIAN", 37.86))
            fit = await asyncio.to_thread(_fit, cut.image, cut.variance, cut.flags, xf - cut.x0, yf - cut.y0,
                                          psf_os, epsf.oversampling, omega)
            row = {"index": e["index"], "mjd": e["mjd"], "band": e["band"], "wl": e["wl"], "predicted_ra": ra_t,
                   "predicted_dec": dec_t, "fit": None}
            if fit is not None:
                fra, fdec = (float(v) for v in cut.wcs.all_pix2world(fit["x"] + cut.x0, fit["y"] + cut.y0, 0))
                pix = np.sqrt(omega)
                fit.update({"ra": fra, "dec": fdec, "sigma_arcsec": float(np.hypot(fit["sigma_x"], fit["sigma_y"]) * pix),
                            "offset_arcsec": float(np.hypot(((fra - ra_t + 180.0) % 360.0 - 180.0) * np.cos(np.radians(fdec)),
                                                            fdec - dec_t) * 3600.0)})
                for k in ("x", "y"):
                    fit.pop(k)
                row["fit"] = fit
            rows.append(row)

        await asyncio.gather(*(one(e) for e in cand))
    rows.sort(key=lambda r: r["mjd"])

    stare, track = [], []
    x_ref, y_ref = (float(v) for v in fd.grid.world_to_pixel(ra, dec))
    by_epoch_band: dict = {}
    for r in rows:
        ex = exposures[r["index"]]
        key = (ex["epoch"], ex["band"])
        if key not in by_epoch_band:
            try:
                by_epoch_band[key] = results.read_exposures(ctx.dir, ctx.meta, *key)
            except Exception:  # noqa: BLE001 - strips are optional
                by_epoch_band[key] = None
        got = by_epoch_band[key]
        if got is None:
            continue
        st, idx = got
        img = st[idx.index(r["index"])]
        xt, yt = (float(v) for v in fd.grid.world_to_pixel(*sky_at(r["mjd"])))
        stare.append({"index": r["index"], "pixels": _strip(img, x_ref, y_ref)})
        track.append({"index": r["index"], "pixels": _strip(img, xt, yt)})
    good = [r["fit"] for r in rows if r["fit"] and r["fit"]["snr"] and r["fit"]["snr"] >= 3]
    summary = {"exposures": len(rows), "fitted": sum(r["fit"] is not None for r in rows), "detected_snr3": len(good)}
    if good:
        summary.update({"median_snr": float(np.median([g["snr"] for g in good])),
                        "median_offset_arcsec": float(np.median([g["offset_arcsec"] for g in good])),
                        "median_sigma_arcsec": float(np.median([g["sigma_arcsec"] for g in good])),
                        "median_chi2_red": float(np.median([g["chi2_red"] for g in good]))})
    return {"sequence": ctx.seq_id, "input": {"ra": ra, "dec": dec, "mjd_ref": mjd_ref, "rate_arcsec_per_hr": rate,
                                              "pa_deg": pa, "epoch": epoch},
            "summary": summary, "exposures": rows, "strips": {"half_size_px": STRIP_HALF, "stare": stare,
                                                              "track": track}}
