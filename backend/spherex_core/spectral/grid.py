"""The spectral bin grid: 17 geometric bins per detector at the detector's resolving power.

SPHEREx has 102 channels, 17 per detector, each about lambda/R wide. Exposures of one source land at
arbitrary wavelengths inside a detector (the linear variable filter), so a spectrum is built by binning
them on a fixed grid. Using one grid for everything means template libraries can be integrated onto it
once and cached, and spectra of different epochs or sources compare bin by bin.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..bands import BAND_INFO, BANDS

BINS_PER_DETECTOR = 17
# Nominal ranges, widened slightly: measured wavelengths spill past them (D1 reaches 0.735 um, D6 5.02 um).
_PAD = 0.01


@dataclass(frozen=True)
class BinGrid:
    band: np.ndarray  # detector id per bin, e.g. "D3"
    lo: np.ndarray  # um
    hi: np.ndarray
    center: np.ndarray  # geometric centre, um
    resolving_power: np.ndarray

    @property
    def size(self) -> int:
        return int(self.center.size)

    def index(self, band: str, wl: np.ndarray) -> np.ndarray:
        """Bin index of each wavelength within the given detector (-1 outside its range)."""
        sel = np.nonzero(self.band == band)[0]
        lo, hi = self.lo[sel], self.hi[sel]
        idx = np.searchsorted(hi, wl, side="right")
        out = np.full(np.shape(wl), -1, dtype=int)
        ok = (idx < sel.size) & (wl >= lo[0])
        out[ok] = sel[np.clip(idx[ok], 0, sel.size - 1)]
        return out


def make_grid() -> BinGrid:
    bands, lo, hi, rp = [], [], [], []
    for b in BANDS:
        info = BAND_INFO[b]
        edges = np.geomspace(info["wl_min"] * (1 - _PAD), info["wl_max"] * (1 + _PAD), BINS_PER_DETECTOR + 1)
        bands += [b] * BINS_PER_DETECTOR
        lo.append(edges[:-1])
        hi.append(edges[1:])
        rp += [info["R"]] * BINS_PER_DETECTOR
    lo_a, hi_a = np.concatenate(lo), np.concatenate(hi)
    return BinGrid(np.array(bands), lo_a, hi_a, np.sqrt(lo_a * hi_a), np.array(rp, dtype=float))


GRID = make_grid()
