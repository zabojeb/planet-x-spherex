"""eps Indi A + B: both in the 16' field? B's motion vs Gaia; alignment stability of field stars;
methane signature of B vs a comparison star.

Usage: python eps_indi.py <seq_epsindiB_16arcmin> <seq_comparison_star> [spectrum.png]
"""
import sys

import numpy as np
from scipy.ndimage import maximum_filter

from common import BANDS, Seq, centroid, offset_arcsec, propagate, window_median, year

seq = Seq(sys.argv[1])
comp = Seq(sys.argv[2])
png = sys.argv[3] if len(sys.argv) > 3 else None
# Gaia DR3 at epoch 2016.0
B = dict(ra=331.07645260, dec=-56.79381207, pmra=3981.977, pmdec=-2466.832)   # 6412596012146801152
A = dict(ra=330.87240788, dec=-56.79725466, pmra=3966.661, pmdec=-2536.192)   # 6412595290592307840
n = seq.n
print(f"grid n={n} ({seq.meta['grid']['fov_arcmin']:.1f}'), {len(seq.epochs)} epochs")


def pred(src, e):
    return seq.to_pix(*propagate(src["ra"], src["dec"], src["pmra"], src["pmdec"], 2016.0,
                                 year(seq.epochs[e]["mjd_mean"])))


ax, ay = pred(A, 0)
bx, by = pred(B, 0)
print(f"A predicted px ({ax:.1f},{ay:.1f}), B predicted px ({bx:.1f},{by:.1f}); separation "
      f"{np.hypot(ax - bx, ay - by) * seq.pix:.0f}\"  -> both inside: {0 <= ax < n and 0 <= ay < n}")
ai, aj = int(round(ax)), int(round(ay))
flag_names = {f["bit"]: f["name"] for f in seq.meta["flag_bits"]}
fl = int(np.bitwise_or.reduce(seq.flags[:, :, aj - 1:aj + 2, ai - 1:ai + 2].ravel()))
print("flags at A's core:", [nm for b, nm in flag_names.items() if fl >> b & 1])

# field stars for the astrometric reference: bright isolated peaks in epoch 0 (all detectors)
ref_e = 0
allimg = seq.combo(ref_e, range(6))
pk = (allimg == maximum_filter(np.nan_to_num(allimg), 9)) & (allimg > 40)
ys, xs = np.nonzero(pk)
stars = [(x, y) for x, y in zip(xs, ys) if 8 < x < n - 8 and 8 < y < n - 8
         and np.hypot(x - ax, y - ay) > 25 and np.hypot(x - bx, y - by) > 6]
print(f"{len(stars)} reference field stars")

rows = []
for e in range(len(seq.epochs)):
    img = seq.combo(e, range(6))
    offs = []
    for x, y in stars:
        c0 = centroid(allimg, x, y)
        c1 = centroid(img, x, y)
        if c0 and c1:
            offs.append(offset_arcsec(seq, c1[0], c1[1], c0[0], c0[1]))
    offs = np.array(offs)
    med = np.median(offs, axis=0)
    rms = np.sqrt(np.mean((offs - med) ** 2, axis=0))
    px, py = pred(B, e)
    cb = centroid(seq.combo(e, (0, 1)) if seq.combo(e, (0, 1)) is not None else img, px, py)
    rows.append((e, med, rms, cb, (px, py)))
    print(f"epoch {e} {seq.epochs[e]['date_min'][:10]}: field-star shift vs epoch 0 {med[0]:+.2f}\"E {med[1]:+.2f}\"N, "
          f"scatter {rms[0]:.2f}\"/{rms[1]:.2f}\" ({len(offs)} stars)")

(e0, m0, _, c0, p0), (e1, m1, _, c1, p1) = rows[0], rows[-1]
dt = year(seq.epochs[e1]["mjd_mean"]) - year(seq.epochs[e0]["mjd_mean"])
me, mn = offset_arcsec(seq, c1[0], c1[1], c0[0], c0[1])
me, mn = me - (m1[0] - m0[0]), mn - (m1[1] - m0[1])  # relative to the field stars
ee, en = B["pmra"] / 1000 * dt, B["pmdec"] / 1000 * dt
print(f"B motion {seq.epochs[e0]['date_min'][:10]} -> {seq.epochs[e1]['date_min'][:10]} ({dt:.2f} yr): "
      f"measured {me:+.2f}\"E {mn:+.2f}\"N (|{np.hypot(me, mn):.2f}\"|), expected {ee:+.2f}\"E {en:+.2f}\"N "
      f"(|{np.hypot(ee, en):.2f}\"|); residual {me - ee:+.2f}, {mn - en:+.2f}\"")
pe, pn = offset_arcsec(seq, c0[0], c0[1], p0[0], p0[1])
print(f"B centroid vs Gaia-predicted position at first epoch: {pe:+.2f}\"E {pn:+.2f}\"N")

# methane: flux ratios in windows (median of unflagged single-exposure photometry)
win = {"J 1.22-1.32": (1.22, 1.32), "H 1.52-1.60": (1.52, 1.60), "CH4 1.63-1.75": (1.63, 1.75),
       "K 2.03-2.15": (2.03, 2.15), "CH4 2.25-2.40": (2.25, 2.40), "CH4 3.20-3.45": (3.20, 3.45),
       "peak 3.95-4.20": (3.95, 4.20), "4.50-4.80": (4.50, 4.80)}
res = {}
for label, s in (("eps Ind B", seq), ("comparison G=13.9 star", comp)):
    ph = s.meta["photometry"]
    v = {k: window_median(ph, *w) for k, w in win.items()}
    res[label] = v
    r1 = v["CH4 1.63-1.75"][0] / v["H 1.52-1.60"][0]
    r2 = v["CH4 2.25-2.40"][0] / v["K 2.03-2.15"][0]
    r3 = v["CH4 3.20-3.45"][0] / v["peak 3.95-4.20"][0]
    nflag = sum(p["flagged"] for p in ph)
    print(f"{label}: {len(ph)} points, {nflag} flagged; CH4(1.7)/H {r1:.2f}  CH4(2.3)/K {r2:.2f}  "
          f"CH4(3.3)/F(4.1) {r3:.2f}; window medians (mJy, n): "
          + ", ".join(f"[{k}] {m:.2f} ({c})" for k, (m, c) in v.items()))

if png:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax_ = plt.subplots(figsize=(8, 4))
    for label, s, col in (("ε Indi B (T1+T6)", seq, "tab:orange"), ("G=13.9 field star", comp, "tab:blue")):
        ph = [p for p in s.meta["photometry"] if not p["flagged"]]
        wl = np.array([p["wl"] for p in ph])
        f = np.array([p["flux_mjy"] for p in ph])
        norm = np.median(f[(wl > 1.22) & (wl < 1.32)])
        ax_.scatter(wl, f / norm, s=8, color=col, label=label, alpha=0.7)
    for lo, hi, nm in ((1.63, 1.75, "CH₄"), (2.25, 2.40, "CH₄"), (3.20, 3.45, "CH₄")):
        ax_.axvspan(lo, hi, color="0.85", zorder=0)
        ax_.text((lo + hi) / 2, 0.02, nm, ha="center", fontsize=8)
    ax_.set_yscale("log")
    ax_.set_ylim(0.01, 3)
    ax_.set_xlabel("wavelength (µm)")
    ax_.set_ylabel("flux / flux(1.27 µm)")
    ax_.set_title("SPHEREx single-exposure photometry, normalised at J", fontsize=10)
    ax_.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(png, dpi=110)
    print("figure:", png)
