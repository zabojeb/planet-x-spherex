"""Barnard's Star: per-epoch centroids from the pipeline's coadds vs the Gaia proper motion.

Usage: python barnard.py <seq_dir>
"""
import sys

import numpy as np
from scipy.ndimage import maximum_filter

from common import Seq, centroid, offset_arcsec, year

seq = Seq(sys.argv[1])
RA0, DEC0, EP0, PMRA, PMDEC = 269.4520769586, 4.6933649666, 2000.0, -801.551, 10362.394
n = seq.n
c = (n - 1) / 2
ref = seq.combo(0, range(6))
pk = (ref == maximum_filter(np.nan_to_num(ref), 7)) & (ref > 20)
stars = [(x, y) for y, x in zip(*np.nonzero(pk)) if 7 < x < n - 7 and 7 < y < n - 7 and np.hypot(x - c, y - c) > 10]
print(f"{len(stars)} reference field stars")
print(f"{'epoch':>10} {'n':>4} {'det':>4} {'dRA* meas':>9} {'pred':>7} {'dDec meas':>9} {'pred':>7}  {'resid E':>7} {'resid N':>7}"
      "  field shift")
for e, ep in enumerate(seq.epochs):
    dets = [b for b in range(6) if seq.has(e, b)]
    img = seq.combo(e, dets)
    shifts = []
    for x, y in stars:
        a, b = centroid(ref, x, y), centroid(img, x, y)
        if a and b:
            shifts.append(offset_arcsec(seq, b[0], b[1], a[0], a[1]))
    sh = np.median(shifts, axis=0) if shifts else np.zeros(2)
    tr = seq.meta["target"]["track"][e]
    cb = centroid(img, tr["x"], tr["y"])
    ra, dec = seq.to_sky(cb[0], cb[1])
    yr = year(ep["mjd_mean"])
    me = (ra - RA0) * np.cos(np.radians(dec)) * 3600 - sh[0]
    mn = (dec - DEC0) * 3600 - sh[1]
    pe, pn = PMRA * (yr - EP0) / 1000, PMDEC * (yr - EP0) / 1000
    print(f"{yr:10.3f} {ep['n']:4d} {len(dets):4d} {me:9.2f} {pe:7.2f} {mn:9.2f} {pn:7.2f}  {me - pe:+7.2f} {mn - pn:+7.2f}"
          f"  {sh[0]:+.2f},{sh[1]:+.2f}")
