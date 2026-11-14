"""Show how saturated stars end up as NaN holes in the coadds (masked bits co-occur with saturation).

Usage: python saturated_core.py <seq_dir> <out.png> [label]
"""
import sys

import numpy as np

from common import BANDS, Seq

s = Seq(sys.argv[1])
png = sys.argv[2]
label = sys.argv[3] if len(sys.argv) > 3 else s.meta["target"]["name"]
cov = np.fromfile(f"{s.path}/coverage.u16", "<u2").reshape(s.shape)
e = max(range(len(s.epochs)), key=lambda k: s.epochs[k]["n"])
c = s.n // 2
h = 7
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

fig, ax = plt.subplots(2, 6, figsize=(12, 4.4))
cmap = plt.get_cmap("gray").copy()
cmap.set_bad("tab:red")
for b in range(6):
    img = s.coadds[e, b][c - h:c + h + 1, c - h:c + h + 1]
    fin = img[np.isfinite(img)]
    lo, hi = (np.percentile(fin, [5, 99.5]) if fin.size else (0, 1))
    ax[0, b].imshow(img, origin="lower", cmap=cmap, vmin=lo, vmax=hi, interpolation="nearest")
    ax[0, b].set_title(f"{BANDS[b]}  NaN={int(np.isnan(img).sum())}", fontsize=9)
    ax[1, b].imshow(cov[e, b][c - h:c + h + 1, c - h:c + h + 1], origin="lower", cmap="viridis",
                    interpolation="nearest")
    ax[1, b].set_title("frames per pixel", fontsize=8)
    for a in ax[:, b]:
        a.set_xticks([]), a.set_yticks([])
fig.suptitle(f"{label}, epoch {s.epochs[e]['date_min'][:10]}: coadd core (red = NaN) and coverage", fontsize=10)
fig.tight_layout()
fig.savefig(png, dpi=100)
print("figure:", png)
