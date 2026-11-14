"""Photometry checks: (1) empirical curve of growth vs the pipeline's Gaussian aperture correction,
(2) a field star against 2MASS / AllWISE, (3) which flag bits actually mark outliers.

Usage: python photometry_check.py <seq_16arcmin_field> <seq_comparison_star>
"""
import csv
import io
import sys
import urllib.parse
import urllib.request

import numpy as np
from photutils.aperture import CircularAnnulus, CircularAperture, aperture_photometry
from scipy.ndimage import maximum_filter

from common import BANDS, Seq

field, comp = Seq(sys.argv[1]), Seq(sys.argv[2])
n = field.n

# ---- 1. curve of growth on coadds -------------------------------------------------------------------------
e = max(range(len(field.epochs)), key=lambda k: field.epochs[k]["n"])
print(f"curve of growth on epoch {field.epochs[e]['date_min'][:10]} coadds of {sys.argv[1].rsplit('/', 1)[-1]}")
print(f"{'band':>4} {'FWHM px':>7} {'r_ap':>5} {'stars':>5} {'f(r_ap) meas':>12} {'Gaussian':>8} {'pipeline/true':>13}")
for b, band in enumerate(BANDS):
    if not field.has(e, b):
        continue
    fw = np.median([x["psf_fwhm"] for x in field.meta["exposures"] if x["band"] == band]) / field.pix
    r_ap = max(1.5, fw)
    s = field.snr(e, b)
    img = field.coadds[e, b]
    pk = (s == maximum_filter(np.nan_to_num(s), 15)) & (s > 30) & (s < 3000)
    ys, xs = np.nonzero(pk)
    sel = [(x, y) for x, y in zip(xs, ys) if 14 < x < n - 14 and 14 < y < n - 14
           and np.isfinite(img[y - 8:y + 9, x - 8:x + 9]).all()]
    fr = []
    for x, y in sel:
        ann = CircularAnnulus((x, y), 8, 11)
        bk = np.nanmedian(ann.to_mask("center").get_values(img))
        f1 = aperture_photometry(img - bk, CircularAperture((x, y), r_ap))["aperture_sum"][0]
        f6 = aperture_photometry(img - bk, CircularAperture((x, y), 6.0))["aperture_sum"][0]
        if f6 > 0:
            fr.append(f1 / f6)
    if not fr:
        continue
    f = float(np.median(fr))
    sig = fw / 2.3548
    g = 1 - np.exp(-r_ap ** 2 / (2 * sig ** 2))
    print(f"{band:>4} {fw:7.2f} {r_ap:5.2f} {len(fr):5d} {f:12.3f} {g:8.3f} {f / g:13.3f}")
print("(coadds are bilinearly resampled, which broadens the PSF slightly; single exposures are a bit sharper)")

# ---- 2. comparison star vs 2MASS / AllWISE -----------------------------------------------------------------
RA, DEC = 331.1718829, -56.7933374


def irsa(q):
    url = "https://irsa.ipac.caltech.edu/TAP/sync?" + urllib.parse.urlencode({"QUERY": q, "FORMAT": "csv"})
    return list(csv.DictReader(io.StringIO(urllib.request.urlopen(url, timeout=300).read().decode())))


circ = f"1 = CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS', {RA}, {DEC}, {3 / 3600}))"
tm = irsa(f"SELECT j_m, h_m, k_m FROM fp_psc WHERE {circ}")
aw = irsa(f"SELECT w1mpro, w2mpro FROM allwise_p3as_psd WHERE {circ}")
ref = {}
if tm:
    t = tm[0]
    ref.update({"2MASS J (1.24)": (1594 * 10 ** (-float(t["j_m"]) / 2.5) * 1e3, (1.17, 1.32)),
                "2MASS H (1.66)": (1024 * 10 ** (-float(t["h_m"]) / 2.5) * 1e3, (1.50, 1.78)),
                "2MASS Ks (2.16)": (666.7 * 10 ** (-float(t["k_m"]) / 2.5) * 1e3, (2.03, 2.30))})
if aw:
    w = aw[0]
    ref.update({"WISE W1 (3.35)": (309.5 * 10 ** (-float(w["w1mpro"]) / 2.5) * 1e3, (3.0, 3.8)),
                "WISE W2 (4.60)": (171.8 * 10 ** (-float(w["w2mpro"]) / 2.5) * 1e3, (4.1, 5.0))})
ph = [p for p in comp.meta["photometry"] if not p["flagged"]]
print("\ncomparison star (Gaia DR3 6412593435166417536): SPHEREx forced photometry vs catalog fluxes")
for k, (fref, (lo, hi)) in ref.items():
    v = [p["flux_mjy"] for p in ph if lo <= p["wl"] < hi]
    if v:
        print(f"  {k:16s} catalog {fref:7.2f} mJy   SPHEREx median {np.median(v):7.2f} mJy (n={len(v)})   ratio {np.median(v) / fref:.3f}")

# ---- 3. which flags mark outliers --------------------------------------------------------------------------
print("\nflag bits vs outliers (comparison star; |flux/median_at_same_λ - 1|):")
allp = comp.meta["photometry"]
wl = np.array([p["wl"] for p in allp])
edges = np.exp(np.arange(np.log(0.7), np.log(5.1), 0.025))
idx = np.digitize(wl, edges)
f = np.array([p["flux_mjy"] for p in allp])
ratio = f / np.array([np.median(f[idx == i]) for i in idx])
dev = np.abs(ratio - 1)
af = np.array([p["ap_flags"] for p in allp])
mf = np.array([p["masked_frac"] for p in allp])
print(f"  baseline (no PHOT_BAD bits, masked_frac<=0.05): median dev "
      f"{np.median(dev[~np.array([p['flagged'] for p in allp])]):.3f}")
for fb in comp.meta["flag_bits"]:
    has = (af >> fb["bit"]) & 1 == 1
    if has.sum() >= 5:
        print(f"  {fb['name']:13s} n={has.sum():3d}  median dev with {np.median(dev[has]):.3f}  without {np.median(dev[~has]):.3f}"
              f"  {'(counts as bad)' if fb['phot_bad'] else ''}")
for lo, hi in ((0, 1e-9), (1e-9, 0.05), (0.05, 0.2), (0.2, 1.01)):
    s = (mf >= lo) & (mf < hi) if hi > 1e-9 else (mf <= 0)
    if s.sum():
        print(f"  masked_frac in [{lo:.2f},{hi:.2f}): n={s.sum():3d} median dev {np.median(dev[s]):.3f}")
