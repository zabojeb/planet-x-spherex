"""The common output grid and resampling of native cutouts onto it."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property

import numpy as np
from astropy.wcs import WCS
from scipy.interpolate import RectBivariateSpline
from scipy.ndimage import map_coordinates

from ..bands import NATIVE_PIXSCALE


@dataclass
class Grid:
    """Gnomonic projection, north up, east left, target on the central pixel."""

    ra: float
    dec: float
    n: int
    pixscale: float = NATIVE_PIXSCALE  # arcsec

    @cached_property
    def wcs(self) -> WCS:
        w = WCS(naxis=2)
        w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
        w.wcs.crval = [self.ra, self.dec]
        w.wcs.crpix = [(self.n + 1) / 2.0, (self.n + 1) / 2.0]
        w.wcs.cdelt = [-self.pixscale / 3600.0, self.pixscale / 3600.0]
        return w

    @property
    def fov_arcmin(self) -> float:
        return self.n * self.pixscale / 60.0

    def to_dict(self) -> dict:
        return {"ra": self.ra, "dec": self.dec, "n": self.n, "pixscale_arcsec": self.pixscale,
                "fov_arcmin": self.fov_arcmin, "projection": "TAN", "orientation": "north up, east left"}

    def world_to_pixel(self, ra, dec):
        x, y = self.wcs.all_world2pix(ra, dec, 0)
        return np.asarray(x, float), np.asarray(y, float)


def make_grid(ra: float, dec: float, fov_arcmin: float, pixscale: float = NATIVE_PIXSCALE) -> Grid:
    """Odd-sized square grid covering at least fov_arcmin, centred on (ra, dec)."""
    n = max(9, int(np.ceil(fov_arcmin * 60.0 / pixscale)))
    return Grid(ra, dec, n + (n % 2 == 0), pixscale)


def pixel_map(cut_wcs: WCS, x0: int, y0: int, grid: Grid, k: int = 9) -> tuple[np.ndarray, np.ndarray]:
    """Cutout pixel coordinates of every grid pixel, from a k x k control grid and a smooth spline."""
    n = grid.n
    k = min(k, n)
    idx = np.linspace(0.0, n - 1.0, k)
    gx, gy = np.meshgrid(idx, idx)
    ra, dec = grid.wcs.all_pix2world(gx, gy, 0)
    px, py = cut_wcs.all_world2pix(ra, dec, 0, quiet=True)
    px, py = np.asarray(px) - x0, np.asarray(py) - y0
    full = np.arange(n, dtype=float)
    deg = min(3, k - 1)
    xs = RectBivariateSpline(idx, idx, px, kx=deg, ky=deg)(full, full)
    ys = RectBivariateSpline(idx, idx, py, kx=deg, ky=deg)(full, full)
    return xs, ys


def resample(arr: np.ndarray, xs: np.ndarray, ys: np.ndarray, order: int = 1,
             min_weight: float = 0.5) -> np.ndarray:
    """Bilinear resampling that ignores NaN (masked) input pixels.

    Data and validity are resampled separately and divided, so a masked pixel does not wipe out its
    neighbours: masked areas keep their size instead of growing by the interpolation kernel. Output points
    with less than min_weight of valid input (inside masked areas, outside the cutout) are NaN.
    """
    a = np.asarray(arr, dtype=np.float64)
    valid = np.isfinite(a)
    coords = [ys, xs]
    num = map_coordinates(np.where(valid, a, 0.0), coords, order=order, mode="constant", cval=0.0,
                          prefilter=False)
    den = map_coordinates(valid.astype(np.float64), coords, order=order, mode="constant", cval=0.0,
                          prefilter=False)
    out = np.full(num.shape, np.nan, dtype=np.float32)
    ok = den >= min_weight
    out[ok] = (num[ok] / den[ok]).astype(np.float32)
    return out


def resample_flags(flags: np.ndarray, xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
    """Nearest-neighbour resampling of a bitmask (bits must not be interpolated)."""
    out = map_coordinates(flags.astype(np.float64), [ys, xs], order=0, mode="constant", cval=0.0,
                          prefilter=False)
    return out.astype(np.int64).astype(np.uint32)
