"""Aligned exposures of a fixed-position sequence with the static sky removed, read once per comet search.

The template for an exposure of visit e and detector b is the median co-add of the other visits on that detector
(the same co-add when the sequence has one visit, which then also removes part of a slow mover's own light). A
mover is elsewhere six months later, so the difference keeps all its light, slow or not. Stars brighter than
S/N 10 in the static sky and saturated cores are masked, as in the field analysis' difference maps, because their
wavelength-dependent residuals (each exposure sees a star at its own LVF wavelength) dominate there.
"""

from __future__ import annotations

import numpy as np
from scipy.ndimage import binary_dilation

from ..bands import BANDS
from ..errors import NotFound
from ..field.common import Check, FieldData, no_check, region_mask, robust_noise, saturated_regions, snr_image
from ..field.transients import BRIGHT_GROW, BRIGHT_SNR
from ..pipeline import results


class FieldImages:
    """Per exposure: aligned image, template-subtracted image and its robust noise; per detector the mask."""

    def __init__(self, fd: FieldData, check: Check = no_check):
        self.fd = fd
        self.meta = fd.meta
        self.n = fd.n
        E = fd.coadds.shape[0]
        with np.errstate(all="ignore"):
            static = np.nanmedian(fd.coadds, axis=0)
        halos = region_mask(fd.n, saturated_regions(fd), scale=1.5, extra=2.0)
        self.mask: dict[int, np.ndarray] = {}
        self.static = static
        for b in range(len(BANDS)):
            if np.isfinite(static[b]).any():
                csnr = np.nan_to_num(snr_image(static[b]), nan=0.0)
                self.mask[b] = binary_dilation(csnr > BRIGHT_SNR, iterations=BRIGHT_GROW) | halos
        self.template: dict[tuple[int, int], np.ndarray] = {}
        self.single_visit = E < 2
        for e in range(E):
            for b in range(len(BANDS)):
                others = [fd.coadds[k, b] for k in range(E) if k != e and np.isfinite(fd.coadds[k, b]).any()]
                with np.errstate(all="ignore"):
                    tpl = (np.nanmedian(np.stack(others), axis=0) if len(others) >= 2 else others[0]) if others \
                        else fd.coadds[e, b]
                self.template[(e, b)] = tpl
        self.image: dict[int, np.ndarray] = {}
        self.diff: dict[int, np.ndarray] = {}
        self.noise: dict[int, float] = {}
        self.raw_noise: dict[int, float] = {}
        for e in range(E):
            for b, band in enumerate(BANDS):
                if not np.isfinite(fd.coadds[e, b]).any():
                    continue
                try:
                    stack, idx = results.read_exposures(fd.ctx.dir, self.meta, e, band)
                except NotFound:
                    continue
                tpl = self.template[(e, b)]
                for k, i in enumerate(idx):
                    img = stack[k].astype(np.float32)
                    d = img - tpl
                    _, noise = robust_noise(d)
                    if not (noise > 0):
                        continue
                    self.image[int(i)] = img
                    self.diff[int(i)] = d.astype(np.float32)
                    self.noise[int(i)] = float(noise)
                    _, rn = robust_noise(img)
                    self.raw_noise[int(i)] = float(rn)
                check()

    def exposures(self, epoch: int | None = None) -> list[int]:
        return sorted(i for i in self.diff if epoch is None or self.meta["exposures"][i]["epoch"] == epoch)

    def snr(self, i: int) -> np.ndarray:
        """S/N of the template-subtracted exposure with bright stars masked (NaN)."""
        b = BANDS.index(self.meta["exposures"][i]["band"])
        s = self.diff[i] / self.noise[i]
        m = self.mask.get(b)
        return np.where(m, np.nan, s) if m is not None else s
