"""WISE 0855-0714 in the SPHEREx coadds: is the brown dwarf at the predicted position, blended with a star?

Usage (dev server):  python wise0855_spherex.py /workspace/cache/sequences/<id> [out.png]
"""
import json
import sys

import numpy as np

d = sys.argv[1]
png = sys.argv[2] if len(sys.argv) > 2 else None
m = json.load(open(f"{d}/meta.json"))
sh = m["arrays"]["coadds"]["shape"]
E, B, n, _ = sh
c = np.fromfile(f"{d}/coadds.f32", "<f4").reshape(sh)
PIX = m["grid"]["pixscale_arcsec"]
ctr = (n - 1) / 2
# Gaia DR3 5756956644387831296 (G=16.3, BP-RP=0.72) at epoch 2016.0; its own motion is negligible (6 mas/yr)
STAR_RA, STAR_DEC = 133.75818250, -7.24115591
g_ra, g_dec = m["grid"]["ra"], m["grid"]["dec"]
cosd = np.cos(np.radians(g_dec))
star_x = ctr - (STAR_RA - g_ra) * cosd * 3600 / PIX  # east is to the left (x decreases)
star_y = ctr + (STAR_DEC - g_dec) * 3600 / PIX
print(f"grid centre {g_ra:.6f} {g_dec:.6f}; Gaia star at px ({star_x:.2f}, {star_y:.2f})")


def snr(img):
    med = np.nanmedian(img)
    mad = 1.4826 * np.nanmedian(np.abs(img - med))
    return (img - med) / mad


def combo(e, bands):
    imgs = [snr(c[e, b]) for b in bands if not np.all(np.isnan(c[e, b]))]
    return np.nanmean(np.stack(imgs), axis=0) if imgs else None


def centroid(img, x, y, box=2):
    xi, yi = int(round(x)), int(round(y))
    sub = np.nan_to_num(img[yi - box:yi + box + 1, xi - box:xi + box + 1]).clip(0, None)
    gy, gx = np.mgrid[yi - box:yi + box + 1, xi - box:xi + box + 1]
    return (gx * sub).sum() / sub.sum(), (gy * sub).sum() / sub.sum(), float(img[yi, xi])


def off(x, y, rx, ry):
    """Offset of (x, y) from (rx, ry) in arcsec: (+east, +north)."""
    return -(x - rx) * PIX, (y - ry) * PIX


rows = []
for e in range(E):
    blue, red = combo(e, (0, 1, 2)), combo(e, (4, 5))
    if blue is None or red is None:
        continue
    tr = m["target"]["track"][e]
    bx, by, bpk = centroid(blue, star_x, star_y)
    rx, ry, rpk = centroid(red, (star_x + tr["x"]) / 2, (star_y + tr["y"]) / 2)
    # red excess: red S/N minus blue S/N scaled to cancel the field stars
    k = np.nanmedian((red / blue)[blue > 8])
    exc = red - k * blue
    ex, ey, epk = centroid(exc, tr["x"], tr["y"])
    rows.append((e, tr, (bx, by), (rx, ry), (ex, ey), epk, k))
    date = m["epochs"][e]["date_min"][:10]
    print(f"epoch {e} {date}: BD predicted px ({tr['x']:.2f},{tr['y']:.2f})")
    print(f"   blue (D1-D3) centroid off star {off(bx, by, star_x, star_y)[0]:+.1f}\"E {off(bx, by, star_x, star_y)[1]:+.1f}\"N"
          f" | red (D5+D6) centroid off BD-pred {off(rx, ry, tr['x'], tr['y'])[0]:+.1f}\"E "
          f"{off(rx, ry, tr['x'], tr['y'])[1]:+.1f}\"N, off star {off(rx, ry, star_x, star_y)[0]:+.1f}\"E")
    print(f"   red-excess peak S/N {epk:.1f} (k={k:.2f}) centroid off BD-pred "
          f"{off(ex, ey, tr['x'], tr['y'])[0]:+.1f}\"E {off(ex, ey, tr['x'], tr['y'])[1]:+.1f}\"N")

if len(rows) >= 2:
    (e1, t1, _, _, p1, _, _), (e2, t2, _, _, p2, _, _) = rows[0], rows[-1]
    dt = (m["epochs"][e2]["mjd_mean"] - m["epochs"][e1]["mjd_mean"]) / 365.25
    dx, dy = off(p2[0], p2[1], p1[0], p1[1])
    print(f"red-excess motion {m['epochs'][e1]['date_min'][:10]} -> {m['epochs'][e2]['date_min'][:10]} ({dt:.2f} yr): "
          f"{dx:+.1f}\"E {dy:+.1f}\"N = {dx / dt:+.1f}, {dy / dt:+.1f} \"/yr (expected -8.1, +0.7)")

if png:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    sel = [r[0] for r in rows]
    fig, ax = plt.subplots(3, len(sel), figsize=(2.3 * len(sel), 7), squeeze=False)
    s = slice(int(ctr) - 10, int(ctr) + 11)
    for j, e in enumerate(sel):
        blue, red = combo(e, (0, 1, 2)), combo(e, (4, 5))
        k = rows[j][6]
        for i, (img, lab) in enumerate([(blue, "D1–D3"), (red, "D5+D6"), (red - k * blue, "red excess")]):
            a = ax[i, j]
            a.imshow(img[s, s], origin="lower", cmap="gray", vmin=-3, vmax=15, interpolation="nearest",
                     extent=(s.start - .5, s.stop - .5, s.start - .5, s.stop - .5))
            tr = m["target"]["track"][e]
            a.plot(tr["x"], tr["y"], "+", color="tab:orange", ms=12, mew=1.2)
            a.plot(star_x, star_y, "x", color="tab:cyan", ms=8, mew=1)
            a.set_xticks([]), a.set_yticks([])
            if j == 0:
                a.set_ylabel(lab)
            if i == 0:
                a.set_title(m["epochs"][e]["date_min"][:10], fontsize=9)
    fig.suptitle("WISE 0855−0714: + predicted brown dwarf, × Gaia G=16.3 star (S/N maps, 2′ wide)", fontsize=9)
    fig.tight_layout()
    fig.savefig(png, dpi=110)
    print("figure:", png)
