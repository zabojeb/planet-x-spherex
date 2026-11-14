"""Relative brightness of saturated stars from the PSF wings in the coadds (annulus 3-6 px, background 9-12 px).

The saturated core is NaN or unreliable, but the wings scale with the star's flux, so the ratio between epochs
in the same detector is a usable relative light curve.

Usage: python wing_photometry.py <seq_dir> [period_days]
"""
import sys

import numpy as np
from astropy.timeseries import LombScargle
from photutils.aperture import CircularAnnulus

from common import BANDS, Seq, year

s = Seq(sys.argv[1])
period = float(sys.argv[2]) if len(sys.argv) > 2 else None
print(s.meta["target"]["name"], f"({len(s.epochs)} epochs)")
for b, band in enumerate(BANDS):
    t, v = [], []
    for e, ep in enumerate(s.epochs):
        if not s.has(e, b) or ep["bands"].get(band, {}).get("n", 0) < 3:
            continue
        tr = s.meta["target"]["track"][e]
        img = s.coadds[e, b]
        bk = np.nanmedian(CircularAnnulus((tr["x"], tr["y"]), 9, 12).to_mask("center").get_values(img))
        vals = CircularAnnulus((tr["x"], tr["y"]), 3, 6).to_mask("center").get_values(img - bk)
        if np.isfinite(vals).mean() < 0.8:
            continue
        t.append(ep["bands"][band]["mjd_mean"])
        v.append(np.nansum(vals))
    if len(v) < 3:
        continue
    v = np.array(v) / np.median(v)
    line = " ".join(f"{year(tt):.2f}:{vv:.2f}" for tt, vv in zip(t, v))
    extra = ""
    if period and len(v) >= 6:
        fr, pw = LombScargle(np.array(t), v).autopower(minimum_frequency=1 / 600, maximum_frequency=1 / 80,
                                                         samples_per_peak=30)
        extra = f"  LS best {1 / fr[np.argmax(pw)]:.0f} d (power {pw.max():.2f})"
    print(f"  {band}: wing flux / median per epoch -> {line}  (max/min {v.max() / v.min():.2f}){extra}")
