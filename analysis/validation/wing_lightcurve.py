"""Relative light curve of a saturated star from its PSF wings in the aligned single exposures.

For every exposure: the sum of the background-subtracted pixels in an annulus r_in..r_out around the predicted
target pixel (NaN = masked pixels skipped, the sum scaled up by the valid fraction), with the background taken
as the median of a wider annulus. Each value is divided by the median of all exposures in the same narrow
wavelength bin (R ~ 40), so the star's colour cancels and only time variations remain. This needs no PSF model,
so it works where the core saturates beyond the ePSF support (R Dra), but it only gives relative brightness.

Usage: python wing_lightcurve.py <seq_dir> <label> [r_in r_out] [period_guess_days]
"""
import json
import sys

import numpy as np
from astropy.timeseries import LombScargle

BANDS = ("D1", "D2", "D3", "D4", "D5", "D6")
d, label = sys.argv[1], sys.argv[2]
r_in = float(sys.argv[3]) if len(sys.argv) > 3 else 4.0
r_out = float(sys.argv[4]) if len(sys.argv) > 4 else 7.0
guess = float(sys.argv[5]) if len(sys.argv) > 5 else None

m = json.load(open(f"{d}/meta.json"))
spec = m["arrays"]["exposures"]
ex = np.memmap(f"{d}/{spec['file']}", dtype="<f2", mode="r", shape=tuple(spec["shape"]))
n = spec["shape"][1]
yy, xx = np.mgrid[0:n, 0:n]
rows = []
for e in m["exposures"]:
    x, y = e["target_px"]
    r = np.hypot(xx - x, yy - y)
    img = np.asarray(ex[e["index"]], dtype=np.float32)
    ring, bg = img[(r >= r_in) & (r < r_out)], img[(r >= r_out + 3) & (r < r_out + 7)]
    good = np.isfinite(ring)
    if good.mean() < 0.6 or np.isfinite(bg).sum() < 10:
        continue
    flux = float((ring[good] - np.nanmedian(bg)).sum() / good.mean())
    rows.append((e["mjd"], e["wl"], e["band"], e["epoch"], flux))

t, wl, band, ep, f = (np.array(c) for c in zip(*rows))
edges = np.exp(np.arange(np.log(0.7), np.log(5.1), 0.025))
idx = np.digitize(wl, edges)
ref = np.array([np.median(f[idx == i]) for i in idx])
cnt = np.array([(idx == i).sum() for i in idx])
ok = (cnt >= 3) & (ref > 0) & (f > 0)
t, ratio, band, ep = t[ok], (f / ref)[ok], band[ok], ep[ok]
print(f"{label}: wing annulus {r_in:g}-{r_out:g} px, {ok.sum()} of {len(m['exposures'])} exposures usable, "
      f"robust rms of ratio {1.4826 * np.median(np.abs(ratio - np.median(ratio))):.3f}")
dates = [x["date_min"][:10] for x in m["epochs"]]
print("  epoch    " + "  ".join(f"{dt[2:]:>8}" for dt in dates))
for b in BANDS:
    cells = []
    for k in range(len(dates)):
        s = (ep == k) & (band == b)
        cells.append(f"{np.median(ratio[s]):8.2f}" if s.sum() >= 3 else "       -")
    print(f"  {b}       " + "  ".join(cells))
allc = []
for k in range(len(dates)):
    s = ep == k
    allc.append(f"{np.median(ratio[s]):8.2f}" if s.sum() >= 3 else "       -")
print("  all      " + "  ".join(allc))
if guess:
    freq, power = LombScargle(t, ratio).autopower(minimum_frequency=1 / 800, maximum_frequency=1 / 60,
                                                  samples_per_peak=20)
    best = 1 / freq[np.argmax(power)]
    near = (1 / freq > 0.8 * guess) & (1 / freq < 1.2 * guess)
    print(f"  Lomb-Scargle: best period {best:.0f} d (power {power.max():.2f}); "
          f"best within ±20 % of {guess:.0f} d: {1 / freq[near][np.argmax(power[near])]:.0f} d "
          f"(power {power[near].max():.2f})")
