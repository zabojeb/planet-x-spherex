"""Shared pieces of the field analysis: the loaded sequence, robust statistics, peaks and pixel stencils.

Everything works on the sequence's aligned grid (TAN, north up, east left, native 6.15" pixels): co-adds
[epoch, band, n, n], their flags, and the aligned single exposures. Fluxes from the co-adds are in mJy with
an approximate aperture correction (the co-add PSF is broadened by resampling), good for colours and
relative changes, not for calibrated photometry (use the per-exposure ePSF photometry for that).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np
from scipy.ndimage import label, maximum_filter

from ..analysis.common import SequenceContext
from ..bands import BANDS, NATIVE_PIXSCALE
from ..flags import FILLED, SATURATION
from ..pipeline import results
from ..timeutil import mjd_to_year

ARCSEC2_TO_SR = (np.pi / 180.0 / 3600.0) ** 2
MJY_PER_UNIT = NATIVE_PIXSCALE ** 2 * ARCSEC2_TO_SR * 1e9  # (MJy/sr) summed over grid pixels -> mJy
AP_RADIUS = 2.0  # px, aperture on co-adds
AP_CORRECTION = 1.0 / 0.80  # light outside r = 2 px of the resampled co-add PSF (approximate)
BKG_ANNULUS = (4.0, 7.0)


class FieldCancelled(Exception):
    """Raised inside the analysis when nobody needs the result any more."""


Check = Callable[[], None]


def no_check() -> None:
    return None


@dataclass
class FieldData:
    """A built sequence loaded for field analysis."""

    ctx: SequenceContext
    coadds: np.ndarray  # float32 [E, B, n, n]
    flags: np.ndarray  # uint32 [E, B, n, n]
    years: np.ndarray  # Julian year per epoch (mean of its exposures)
    t_ref: float
    notes: list[str] = field(default_factory=list)

    @property
    def meta(self) -> dict:
        return self.ctx.meta

    @property
    def grid(self):
        return self.ctx.grid

    @property
    def n(self) -> int:
        return self.ctx.grid.n

    def sky(self, x, y) -> tuple[np.ndarray, np.ndarray]:
        ra, dec = self.grid.wcs.all_pix2world(np.asarray(x, float), np.asarray(y, float), 0)
        return np.asarray(ra, float), np.asarray(dec, float)

    def bands_present(self) -> list[int]:
        return [b for b in range(len(BANDS)) if np.isfinite(self.coadds[:, b]).any()]


def load_field(ctx: SequenceContext) -> FieldData:
    coadds = results.read_array(ctx.dir, ctx.meta, "coadds")
    flags = results.read_array(ctx.dir, ctx.meta, "flags")
    years = np.array([float(mjd_to_year(e["mjd_mean"])) for e in ctx.meta["epochs"]])
    return FieldData(ctx, coadds, flags, years, float(np.mean(years)))


def robust_noise(a: np.ndarray) -> tuple[float, float]:
    """Median and 1.4826*MAD of the finite values."""
    v = np.asarray(a, float)
    v = v[np.isfinite(v)]
    if v.size < 10:
        return 0.0, float("nan")
    med = float(np.median(v))
    return med, float(1.4826 * np.median(np.abs(v - med)))


def snr_image(img: np.ndarray) -> np.ndarray:
    med, noise = robust_noise(img)
    if not (noise > 0):
        return np.full_like(img, np.nan, dtype=float)
    return (np.asarray(img, float) - med) / noise


def local_peaks(snr: np.ndarray, threshold: float, size: int = 3, edge: int = 2) -> tuple[np.ndarray, np.ndarray]:
    """Integer (y, x) of local maxima above threshold, away from the edges."""
    s = np.where(np.isfinite(snr), snr, -np.inf)
    peak = (s == maximum_filter(s, size=size, mode="constant", cval=-np.inf)) & (s >= threshold)
    if edge:
        peak[:edge], peak[-edge:], peak[:, :edge], peak[:, -edge:] = False, False, False, False
    return np.nonzero(peak)


def stencil(r_out: float, r_in: float = 0.0) -> np.ndarray:
    """Integer offsets (dy, dx) with r_in <= r <= r_out (a disk when r_in == 0, an annulus otherwise)."""
    k = int(np.ceil(r_out))
    dy, dx = np.mgrid[-k:k + 1, -k:k + 1]
    r = np.hypot(dx, dy)
    sel = (r <= r_out) & ((r >= r_in) if r_in > 0 else True)
    return np.stack([dy[sel], dx[sel]], axis=1)


def gather(stack: np.ndarray, xs, ys, offsets: np.ndarray) -> np.ndarray:
    """Values of stack[..., y, x] at rounded (xs, ys) + offsets -> [..., m, p]; NaN outside the image."""
    n_y, n_x = stack.shape[-2:]
    xi = np.rint(np.asarray(xs, float)).astype(int)[:, None] + offsets[None, :, 1]
    yi = np.rint(np.asarray(ys, float)).astype(int)[:, None] + offsets[None, :, 0]
    valid = (xi >= 0) & (xi < n_x) & (yi >= 0) & (yi < n_y)
    vals = np.asarray(stack[..., np.clip(yi, 0, n_y - 1), np.clip(xi, 0, n_x - 1)], dtype=np.float64)
    vals[..., ~valid] = np.nan
    return vals


def aperture_fluxes(stack: np.ndarray, xs, ys, r: float = AP_RADIUS,
                    annulus: tuple[float, float] = BKG_ANNULUS) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Background-subtracted aperture sums, their errors (annulus scatter) and the masked fraction of the
    aperture, for every position in every image of stack ([..., n, n] -> [..., m])."""
    ap = gather(stack, xs, ys, stencil(r))
    ann = gather(stack, xs, ys, stencil(annulus[1], annulus[0]))
    with np.errstate(all="ignore"):
        bkg = np.nanmedian(ann, axis=-1)
        noise = 1.4826 * np.nanmedian(np.abs(ann - bkg[..., None]), axis=-1)
    good = np.isfinite(ap)
    npix = good.sum(axis=-1)
    flux = np.nansum(ap, axis=-1) - bkg * npix
    err = noise * np.sqrt(np.maximum(npix, 1))
    masked = 1.0 - npix / ap.shape[-1]
    return flux, err, masked


def to_mjy(flux_units: np.ndarray) -> np.ndarray:
    return np.asarray(flux_units) * MJY_PER_UNIT * AP_CORRECTION


def saturated_regions(fd: FieldData, margin: float = 3.0, min_fraction: float = 0.2,
                      min_pixels: int = 2) -> list[tuple[float, float, float]]:
    """(x, y, radius) of the saturated / pipeline-filled cores of bright stars: pixels flagged in at least
    `min_fraction` of the epoch x detector planes (isolated NONLINEAR or OVERFLOW pixels flagged once are not
    stars), grouped into footprints of >= min_pixels; radius = equivalent circle + `margin`."""
    planes = [(e, b) for e in range(fd.flags.shape[0]) for b in range(fd.flags.shape[1])
              if np.isfinite(fd.coadds[e, b]).any()]
    if not planes:
        return []
    count = np.zeros((fd.n, fd.n), int)
    for e, b in planes:
        count += (fd.flags[e, b].astype(np.int64) & (SATURATION | FILLED)) != 0
    sat = count >= max(2, int(np.ceil(min_fraction * len(planes))))
    lab, n = label(sat)
    out = []
    for k in range(1, n + 1):
        yy, xx = np.nonzero(lab == k)
        if len(xx) < min_pixels:
            continue
        out.append((float(xx.mean()), float(yy.mean()), float(np.sqrt(len(xx) / np.pi)) + margin))
    return out


def region_mask(n: int, regions: list[tuple[float, float, float]], scale: float = 1.0, extra: float = 0.0) -> np.ndarray:
    yy, xx = np.mgrid[0:n, 0:n]
    m = np.zeros((n, n), bool)
    for x, y, r in regions:
        m |= np.hypot(xx - x, yy - y) <= r * scale + extra
    return m
