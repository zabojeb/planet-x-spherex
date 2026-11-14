"""Variability in the forced photometry: normalised light curve, chi^2, per-epoch levels, Lomb-Scargle.

Each point is divided by the median flux of all points in the same narrow wavelength bin (R ~ 40), so
the colour of the star cancels and only time variations remain.

Usage: python variables.py <seq_dir> <label> [period_guess_days] [lightcurve.png]
"""
import sys

import numpy as np
from astropy.timeseries import LombScargle

from common import BANDS, Seq

seq = Seq(sys.argv[1])
label = sys.argv[2]
guess = float(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[3] != "-" else None
png = sys.argv[4] if len(sys.argv) > 4 else None
ph = seq.meta["photometry"]
nflag = sum(p["flagged"] for p in ph)
print(f"{label}: {len(ph)} points, {nflag} flagged ({100 * nflag / max(len(ph), 1):.0f}%)")
names = {f["bit"]: f["name"] for f in seq.meta["flag_bits"]}
for b in BANDS:
    pts = [p for p in ph if p["band"] == b]
    if pts:
        bits = {}
        for p in pts:
            if p["flagged"]:
                for bit, nm in names.items():
                    if p["ap_flags"] >> bit & 1:
                        bits[nm] = bits.get(nm, 0) + 1
        top = sorted(bits.items(), key=lambda kv: -kv[1])[:4]
        psf = [p for p in pts if p.get("method") == "psf"]
        extra = ""
        if "method" in pts[0]:  # ALGO_VERSION >= 2
            extra = (f"; psf fits {len(psf)}, saturated {sum(bool(p.get('saturated')) for p in pts)}, "
                     f"median psf weight {np.median([p['psf_weight'] for p in psf]) if psf else float('nan'):.2f}")
        print(f"  {b}: {len(pts)} pts, {sum(p['flagged'] for p in pts)} flagged; top bits {top}; "
              f"median flux {np.median([p['flux_mjy'] for p in pts]):.1f} mJy{extra}")


def normalised(points):
    wl = np.array([p["wl"] for p in points])
    edges = np.exp(np.arange(np.log(0.7), np.log(5.1), 0.025))
    idx = np.digitize(wl, edges)
    f = np.array([p["flux_mjy"] for p in points])
    e = np.array([p["err_mjy"] for p in points])
    ref = np.array([np.median(f[idx == i]) for i in idx])
    cnt = np.array([(idx == i).sum() for i in idx])
    ok = (cnt >= 3) & (ref > 0)
    t = np.array([p["mjd"] for p in points])
    bands = np.array([p["band"] for p in points])
    return t[ok], (f / ref)[ok], (e / ref)[ok], np.array([p["epoch"] for p in points])[ok], bands[ok]


subsets = [("unflagged", [p for p in ph if not p["flagged"]])]
if ph and "method" in ph[0]:
    subsets.append(("psf fits incl. flagged", [p for p in ph if p["method"] == "psf"]))
subsets.append(("all", ph))
for name, pts in subsets:
    if len(pts) < 20:
        print(f"  [{name}] too few points")
        continue
    t, r, er, ep, bands_ok = normalised(pts)
    if name.startswith("psf"):  # per-detector levels: the light curve each detector sees
        rows = []
        for b in BANDS:
            cells = []
            for e in sorted(set(ep)):
                sel = (ep == e) & (bands_ok == b)
                cells.append(f"{np.median(r[sel]):.2f}({sel.sum()})" if sel.sum() >= 3 else "  -  ")
            rows.append(f"     {b}: " + "  ".join(cells))
        print(f"  [{name}] per-detector median ratio per epoch:\n" + "\n".join(rows))
    chi2 = np.sum(((r - 1) / er) ** 2) / max(len(r) - 1, 1)
    levels = []
    for e in sorted(set(ep)):
        sel = ep == e
        if sel.sum() >= 5:
            levels.append((seq.epochs[e]["date_min"][:10], int(sel.sum()), float(np.median(r[sel])),
                           float(1.4826 * np.median(np.abs(r[sel] - np.median(r[sel]))) / np.sqrt(sel.sum()))))
    print(f"  [{name}] {len(r)} pts, reduced chi2 vs constant = {chi2:.1f}, robust rms of ratio "
          f"{1.4826 * np.median(np.abs(r - np.median(r))):.3f}, median err/flux {np.median(er):.3f}")
    print("     per-epoch median ratio (date, n, level, s.e.): "
          + "; ".join(f"{d} {k} {lv:.3f}±{se:.3f}" for d, k, lv, se in levels))
    if guess and len(levels) >= 4:
        freq, power = LombScargle(t, r).autopower(minimum_frequency=1 / 800, maximum_frequency=1 / 60,
                                                  samples_per_peak=20)
        best = 1 / freq[np.argmax(power)]
        near = power[(1 / freq > 0.8 * guess) & (1 / freq < 1.2 * guess)].max()
        print(f"     Lomb-Scargle: best period {best:.0f} d (power {power.max():.2f}); "
              f"max power within ±20% of {guess:.0f} d: {near:.2f}")
    if png and name == ("unflagged" if nflag < len(ph) * 0.8 else "all"):
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(figsize=(8, 3.2))
        ax.errorbar(t, r, yerr=er, fmt="o", ms=2.5, alpha=0.5, lw=0.5)
        ax.set_xlabel("MJD")
        ax.set_ylabel("flux / median at same λ")
        ax.set_title(f"{label}: normalised light curve ({name} points)", fontsize=10)
        fig.tight_layout()
        fig.savefig(png, dpi=110)
        print("     figure:", png)
