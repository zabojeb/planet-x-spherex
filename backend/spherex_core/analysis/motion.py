"""Proper motion from per-epoch centroids in the co-adds.

For each epoch and detector the source is centroided near its predicted position (flux-weighted, above a
local background, iterated), then a straight-line motion is fitted to the tangent-plane offsets. The
combined fit shares one motion across detectors but gives each detector its own zero point, so small
per-detector offsets (blends with sources of other colours, detector WCS differences) do not leak into the
motion. Errors come from the fit covariance, scaled up by sqrt(reduced chi^2) when the scatter exceeds them.
"""

from __future__ import annotations

import numpy as np

from ..bands import BANDS
from ..processing import Grid
from ..timeutil import mjd_to_year

CENTROID_RADIUS = 2.5  # px, flux-weighted window
BKG_ANNULUS = (4.0, 7.0)  # px
MAX_FIRST_SHIFT = 3.0  # px allowed between the start position and the first centroid
MAX_TRACK_SHIFT = 2.0  # px allowed from the predicted position once a motion is known
COADD_FWHM_PX = 1.8  # typical co-add PSF FWHM (native PSF broadened by resampling)
SIGMA_FLOOR_PX = 0.02  # systematic floor per centroid (alignment scatter is ~0.1")
MIN_SNR = 5.0


def xi_arcsec(ra: float, dec: float, ra0: float) -> float:
    """East offset from ra0 in arcsec. The RA difference is wrapped into [-180, 180) before the cos(dec)
    projection, so RA 359.9999 -> 0.0001 at the equator is +0.72″, not -1,296,000″."""
    return ((ra - ra0 + 180.0) % 360.0 - 180.0) * np.cos(np.radians(dec)) * 3600.0


def centroid(img: np.ndarray, x0: float, y0: float, max_shift: float, radius: float = CENTROID_RADIUS,
             iterations: int = 4) -> dict | None:
    """Iterated flux-weighted centroid of the excess above the local background near (x0, y0)."""
    ny, nx = img.shape
    yy, xx = np.mgrid[0:ny, 0:nx]
    x, y = float(x0), float(y0)
    for _ in range(iterations):
        rr = np.hypot(xx - x, yy - y)
        ann = (rr >= BKG_ANNULUS[0]) & (rr < BKG_ANNULUS[1]) & np.isfinite(img)
        win = (rr <= radius) & np.isfinite(img)
        if ann.sum() < 8 or win.sum() < 5:
            return None
        bkg = float(np.median(img[ann]))
        w = np.clip(img[win] - bkg, 0.0, None)
        if w.sum() <= 0:
            return None
        xn, yn = float((xx[win] * w).sum() / w.sum()), float((yy[win] * w).sum() / w.sum())
        if np.hypot(xn - x0, yn - y0) > max_shift:
            return None
        if np.hypot(xn - x, yn - y) < 1e-3:
            x, y = xn, yn
            break
        x, y = xn, yn
    rr = np.hypot(xx - x, yy - y)
    ann = (rr >= BKG_ANNULUS[0]) & (rr < BKG_ANNULUS[1]) & np.isfinite(img)
    win = (rr <= radius) & np.isfinite(img)
    bkg = float(np.median(img[ann]))
    noise = 1.4826 * float(np.median(np.abs(img[ann] - bkg)))
    flux = float((img[win] - bkg).sum())
    snr = flux / (noise * np.sqrt(win.sum())) if noise > 0 else np.inf
    if not snr >= MIN_SNR:
        return None
    sigma = max(COADD_FWHM_PX / (2.3548 * snr), SIGMA_FLOOR_PX)
    return {"x": x, "y": y, "snr": float(snr), "sigma_px": float(sigma)}


def _wls(design: np.ndarray, values: np.ndarray, sigma: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
    """Weighted least squares: parameters, covariance (scaled by reduced chi^2 when > 1), reduced chi^2."""
    w = 1.0 / sigma ** 2
    ata = (design * w[:, None]).T @ design
    cov = np.linalg.pinv(ata)
    p = cov @ ((design * w[:, None]).T @ values)
    resid = values - design @ p
    dof = max(len(values) - design.shape[1], 1)
    chi2 = float((w * resid ** 2).sum() / dof)
    return p, cov * max(chi2, 1.0), chi2


def _fit(points: list[dict], t_ref: float) -> dict | None:
    """Common motion with one zero point per detector. Needs points at two or more distinct times."""
    if len({round(p["year"], 4) for p in points}) < 2:
        return None
    bands = sorted({p["band"] for p in points})
    design = np.zeros((len(points), len(bands) + 1))
    for i, p in enumerate(points):
        design[i, bands.index(p["band"])] = 1.0
        design[i, -1] = p["year"] - t_ref
    sig = np.array([p["sigma_arcsec"] for p in points])
    px, cx, chi_x = _wls(design, np.array([p["xi"] for p in points]), sig)
    py, cy, chi_y = _wls(design, np.array([p["eta"] for p in points]), sig)
    pmra, pmdec = px[-1] * 1000.0, py[-1] * 1000.0
    err_ra, err_dec = float(np.sqrt(cx[-1, -1])) * 1000.0, float(np.sqrt(cy[-1, -1])) * 1000.0
    for p in points:
        b = bands.index(p["band"])
        p["resid_ra_arcsec"] = float(p["xi"] - (px[b] + px[-1] * (p["year"] - t_ref)))
        p["resid_dec_arcsec"] = float(p["eta"] - (py[b] + py[-1] * (p["year"] - t_ref)))
    total = float(np.hypot(pmra, pmdec))
    return {"pmra": float(pmra), "pmdec": float(pmdec), "pmra_err": err_ra, "pmdec_err": err_dec,
            "pm_total": total, "pm_total_err": float(np.hypot(pmra * err_ra, pmdec * err_dec) / total) if total else None,
            "pa_deg": float(np.degrees(np.arctan2(pmra, pmdec)) % 360.0), "chi2_red_ra": chi_x, "chi2_red_dec": chi_y,
            "n_points": len(points), "n_epochs": len({p["epoch"] for p in points}), "bands": bands}


def fit_motion(meta: dict, coadds: np.ndarray, grid: Grid, x: float, y: float, bands: list[str] | None = None,
               pmra: float | None = None, pmdec: float | None = None) -> dict:
    """Centroid the source near grid pixel (x, y) in every epoch and detector and fit its proper motion.

    pmra/pmdec (mas/yr), when given, predict where to look in each epoch (e.g. a Gaia motion); otherwise the
    first centroids found are used to follow the source. Raises ValueError if the source is found in fewer
    than two epochs.
    """
    use = [b for b in (bands or BANDS) if b in BANDS]
    ra0, dec0 = (float(v) for v in grid.wcs.all_pix2world(x, y, 0))
    epochs = sorted(meta["epochs"], key=lambda e: e["mjd_mean"])
    t_ref = float(mjd_to_year(np.mean([e["mjd_mean"] for e in epochs])))
    cosd = np.cos(np.radians(dec0))
    have_prior = pmra is not None and pmdec is not None

    def predict(year: float, known: list[dict]) -> tuple[float, float, float]:
        """Predicted pixel position and the allowed shift around it."""
        if have_prior:
            dra = pmra / 1000.0 * (year - t_ref) / 3600.0 / cosd
            ddec = pmdec / 1000.0 * (year - t_ref) / 3600.0
            px, py = grid.world_to_pixel(ra0 + dra, dec0 + ddec)
            return float(px), float(py), MAX_TRACK_SHIFT
        if len({k["epoch"] for k in known}) >= 2:
            fit = _fit([dict(k) for k in known], t_ref)
            if fit is not None:
                xi0 = np.median([k["xi"] - fit["pmra"] / 1000.0 * (k["year"] - t_ref) for k in known])
                eta0 = np.median([k["eta"] - fit["pmdec"] / 1000.0 * (k["year"] - t_ref) for k in known])
                xi = xi0 + fit["pmra"] / 1000.0 * (year - t_ref)
                eta = eta0 + fit["pmdec"] / 1000.0 * (year - t_ref)
                px, py = grid.world_to_pixel(ra0 + xi / 3600.0 / cosd, dec0 + eta / 3600.0)
                return float(px), float(py), MAX_TRACK_SHIFT
        if known:
            return float(np.mean([k["x"] for k in known])), float(np.mean([k["y"] for k in known])), MAX_FIRST_SHIFT
        return float(x), float(y), MAX_FIRST_SHIFT

    points: list[dict] = []
    for e in epochs:
        for b in use:
            info = e["bands"].get(b)
            if not info:
                continue
            year = float(mjd_to_year(info["mjd_mean"]))
            px, py, shift = predict(year, points)
            c = centroid(coadds[e["index"], BANDS.index(b)], px, py, shift)
            if c is None:
                continue
            ra, dec = (float(v) for v in grid.wcs.all_pix2world(c["x"], c["y"], 0))
            xi = xi_arcsec(ra, dec, ra0)
            points.append({"epoch": e["index"], "band": b, "mjd": info["mjd_mean"], "year": year, "x": c["x"],
                           "y": c["y"], "ra": ra, "dec": dec, "xi": float(xi), "eta": float((dec - dec0) * 3600.0),
                           "snr": c["snr"], "sigma_arcsec": c["sigma_px"] * grid.pixscale})
    if len({p["epoch"] for p in points}) < 2:
        raise ValueError("the source was found in fewer than two epochs")

    combined = _fit(points, t_ref)
    per_band = {}
    for b in sorted({p["band"] for p in points}):
        sub = [dict(p) for p in points if p["band"] == b]
        fit = _fit(sub, t_ref)
        if fit is not None:
            per_band[b] = fit
    track = []
    for e in epochs:
        year = float(mjd_to_year(e["mjd_mean"]))
        xi0 = np.median([p["xi"] - combined["pmra"] / 1000.0 * (p["year"] - t_ref) for p in points])
        eta0 = np.median([p["eta"] - combined["pmdec"] / 1000.0 * (p["year"] - t_ref) for p in points])
        ra = ra0 + (xi0 + combined["pmra"] / 1000.0 * (year - t_ref)) / 3600.0 / cosd
        dec = dec0 + (eta0 + combined["pmdec"] / 1000.0 * (year - t_ref)) / 3600.0
        px, py = grid.world_to_pixel(ra, dec)
        track.append({"epoch": e["index"], "year": year, "ra": float(ra), "dec": float(dec), "x": float(px),
                      "y": float(py)})
    for p in points:
        p["dra_arcsec"], p["ddec_arcsec"] = p.pop("xi"), p.pop("eta")
    return {"start": {"x": float(x), "y": float(y), "ra": ra0, "dec": dec0}, "reference_year": t_ref,
            "prior": {"pmra": pmra, "pmdec": pmdec} if have_prior else None,
            "combined": combined, "per_band": per_band, "points": points, "track": track}
