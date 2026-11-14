"""Light curves from the pipeline's own photometry in meta.json, normalised the way the web client does it.

Each unflagged measurement is divided by the median of all measurements of the same method in its narrow
wavelength bin (log step 0.025, R ~ 40), so colour and the ring's share of the PSF cancel and only time
variations remain. Prints the methods used per detector, per-epoch medians per detector, the robust scatter
and, with a period guess, a Lomb-Scargle periodogram.

Usage: python pipeline_lightcurves.py <seq_dir> <label> [period_guess_days]
"""
import json
import sys
from collections import Counter

import numpy as np
from astropy.timeseries import LombScargle

BANDS = ("D1", "D2", "D3", "D4", "D5", "D6")
d, label = sys.argv[1], sys.argv[2]
guess = float(sys.argv[3]) if len(sys.argv) > 3 else None
m = json.load(open(f"{d}/meta.json"))
ph = [p for p in m["photometry"] if p["flux_mjy"] is not None]
print(f"{label}: {len(ph)} measurements of {len(m['exposures'])} exposures, ALGO {m['version']}")
rings = m.get("processing", {}).get("rings", {})
for b in BANDS:
    c = Counter(p["method"] for p in ph if p["band"] == b)
    nflag = sum(p["flagged"] for p in ph if p["band"] == b)
    ring = rings.get(b)
    rtxt = f"  ring {ring['r_in']}-{ring['r_out']} px (core p95 {ring['core_radius_px']:.1f} px, " \
           f"saturated share {ring['saturated_share']:.2f})" if ring and ring.get("r_in") else ""
    print(f"  {b}: {dict(c)} flagged {nflag}{rtxt}")

use = [p for p in ph if not p["flagged"] and p["flux_mjy"] > 0]
t = np.array([p["mjd"] for p in use])
wl = np.array([p["wl"] for p in use])
f = np.array([p["flux_mjy"] for p in use])
band = np.array([p["band"] for p in use])
ep = np.array([p["epoch"] for p in use])
meth = np.array([p["method"] for p in use])
edges = np.exp(np.arange(np.log(0.7), np.log(5.1), 0.025))
key = np.array([f"{mt}:{i}" for mt, i in zip(meth, np.digitize(wl, edges))])
ref = {k: np.median(f[key == k]) for k in set(key)}
cnt = Counter(key)
ok = np.array([cnt[k] >= 3 for k in key])
ratio = f / np.array([ref[k] for k in key])
t, ratio, band, ep = t[ok], ratio[ok], band[ok], ep[ok]
print(f"  usable for the light curve: {ok.sum()}, robust rms of the ratio "
      f"{1.4826 * np.median(np.abs(ratio - np.median(ratio))):.3f}")
dates = [x["date_min"][:10] for x in m["epochs"]]
print("  epoch    " + "  ".join(f"{dt[2:]:>8}" for dt in dates))
for b in BANDS + ("all",):
    cells = []
    for k in range(len(dates)):
        s = (ep == k) & ((band == b) if b != "all" else True)
        cells.append(f"{np.median(ratio[s]):8.2f}" if s.sum() >= 3 else "       -")
    print(f"  {b:<8} " + "  ".join(cells))
if guess and len(t) > 10:
    freq, power = LombScargle(t, ratio).autopower(minimum_frequency=1 / 800, maximum_frequency=1 / 60,
                                                  samples_per_peak=20)
    periods = 1 / freq
    best = periods[np.argmax(power)]
    near = (periods > 0.8 * guess) & (periods < 1.2 * guess)
    print(f"  Lomb-Scargle: best period {best:.0f} d (power {power.max():.2f}); best within ±20 % of "
          f"{guess:.0f} d: {periods[near][np.argmax(power[near])]:.0f} d (power {power[near].max():.2f})")
