"""Small helpers shared by the validation scripts. They only read a sequence directory written by the
pipeline (meta.json + raw arrays), so they keep working while the backend package is refactored."""
import json

import numpy as np
from astropy.wcs import WCS

BANDS = ("D1", "D2", "D3", "D4", "D5", "D6")
MJD_J2000 = 51544.5


class Seq:
    def __init__(self, path):
        self.path = path
        self.meta = json.load(open(f"{path}/meta.json"))
        sh = self.meta["arrays"]["coadds"]["shape"]
        self.shape = sh
        self.coadds = np.fromfile(f"{path}/coadds.f32", "<f4").reshape(sh)
        self.flags = np.fromfile(f"{path}/flags.u32", "<u4").reshape(sh)
        g = self.meta["grid"]
        self.pix = g["pixscale_arcsec"]
        self.n = g["n"]
        w = WCS(naxis=2)
        w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
        w.wcs.crval = [g["ra"], g["dec"]]
        w.wcs.crpix = [(g["n"] + 1) / 2, (g["n"] + 1) / 2]
        w.wcs.cdelt = [-g["pixscale_arcsec"] / 3600, g["pixscale_arcsec"] / 3600]
        self.wcs = w

    @property
    def epochs(self):
        return self.meta["epochs"]

    def has(self, e, b):
        return not np.all(np.isnan(self.coadds[e, b]))

    def snr(self, e, b):
        img = self.coadds[e, b]
        med = np.nanmedian(img)
        mad = 1.4826 * np.nanmedian(np.abs(img - med))
        return (img - med) / mad

    def combo(self, e, bands):
        imgs = [self.snr(e, b) for b in bands if self.has(e, b)]
        return np.nanmean(np.stack(imgs), axis=0) if imgs else None

    def to_pix(self, ra, dec):
        x, y = self.wcs.all_world2pix(ra, dec, 0)
        return float(x), float(y)

    def to_sky(self, x, y):
        ra, dec = self.wcs.all_pix2world(x, y, 0)
        return float(ra), float(dec)


def year(mjd):
    return 2000.0 + (mjd - MJD_J2000) / 365.25


def propagate(ra, dec, pmra, pmdec, ref_epoch, yr):
    dt = yr - ref_epoch
    d = dec + pmdec * dt / 3.6e6
    return ra + pmra * dt / 3.6e6 / np.cos(np.radians(d)), d


def centroid(img, x, y, box=2, bkg_box=6):
    """Flux-weighted centroid in a (2*box+1)^2 window around the brightest pixel near (x, y),
    after subtracting the median of a surrounding annulus box. Returns (x, y, peak) or None."""
    xi, yi = int(round(x)), int(round(y))
    n = img.shape[0]
    if not (bkg_box <= xi < n - bkg_box and bkg_box <= yi < n - bkg_box):
        return None
    win = np.nan_to_num(img[yi - 1:yi + 2, xi - 1:xi + 2], nan=-np.inf)
    j, i = np.unravel_index(np.argmax(win), win.shape)
    yi, xi = yi - 1 + j, xi - 1 + i
    outer = img[yi - bkg_box:yi + bkg_box + 1, xi - bkg_box:xi + bkg_box + 1].copy()
    outer[bkg_box - box - 1:bkg_box + box + 2, bkg_box - box - 1:bkg_box + box + 2] = np.nan
    bkg = np.nanmedian(outer)
    sub = np.nan_to_num(img[yi - box:yi + box + 1, xi - box:xi + box + 1] - bkg).clip(0, None)
    if sub.sum() <= 0:
        return None
    gy, gx = np.mgrid[yi - box:yi + box + 1, xi - box:xi + box + 1]
    return (gx * sub).sum() / sub.sum(), (gy * sub).sum() / sub.sum(), float(img[yi, xi])


def offset_arcsec(seq, x1, y1, x0, y0):
    """(east, north) arcsec of point 1 relative to point 0, using the grid WCS."""
    ra1, de1 = seq.to_sky(x1, y1)
    ra0, de0 = seq.to_sky(x0, y0)
    return (ra1 - ra0) * np.cos(np.radians(de0)) * 3600, (de1 - de0) * 3600


def window_median(phot, lo, hi, use_flagged=False):
    v = [p["flux_mjy"] for p in phot if lo <= p["wl"] < hi and (use_flagged or not p["flagged"])]
    return (float(np.median(v)), len(v)) if v else (np.nan, 0)
