"""Build (or rebuild) sequences from the command line and save a quick-look PNG.

    python scripts/build_sequence.py barnard            # a preset id
    python scripts/build_sequence.py all                # every preset at its own field of view (warms the cache)
    python scripts/build_sequence.py "133.79 -7.245" --pmra -8118 --pmdec 677 --ref-epoch 2010.4
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

import numpy as np

from spherex_core import cache
from spherex_core.pipeline import SequenceParams, build_sequence
from spherex_core.pipeline.results import read_array
from spherex_core.targets import PRESETS, resolve


def quicklook(meta: dict, out_dir: Path, png: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    shape = meta["arrays"]["coadds"]["shape"]
    data = read_array(out_dir, meta, "coadds")
    n_ep, n_b = shape[0], shape[1]
    fig, axes = plt.subplots(n_b, n_ep, figsize=(1.6 * n_ep + 1, 1.6 * n_b + 0.6), squeeze=False)
    for b in range(n_b):
        finite = data[:, b][np.isfinite(data[:, b])]
        lo, hi = (np.percentile(finite, [1, 99.7]) if finite.size else (0, 1))
        for e in range(n_ep):
            ax = axes[b, e]
            ax.imshow(np.arcsinh((data[e, b] - lo) / max(hi - lo, 1e-9) * 10), origin="lower", cmap="gray",
                      interpolation="nearest")
            ax.set_xticks([]), ax.set_yticks([])
            tr = meta["target"]["track"][e]
            ax.plot(tr["x"], tr["y"], "+", color="tab:orange", ms=10, mew=0.8)
            if b == 0:
                ax.set_title(meta["epochs"][e]["date_min"][:10], fontsize=7)
            if e == 0:
                ax.set_ylabel(meta["bands"][b]["id"], fontsize=8)
    fig.suptitle(f"{meta['target']['name']} — coadds per epoch (columns) and detector (rows)", fontsize=9)
    fig.tight_layout()
    fig.savefig(png, dpi=110)


async def run(params: SequenceParams, label: str) -> None:
    out = cache.layout().sequence(params.key())
    t0 = time.perf_counter()
    last = [0.0]

    def progress(stage, done, total):
        if time.perf_counter() - last[0] > 2 or done == total:
            last[0] = time.perf_counter()
            print(f"  [{label}] {stage} {done}/{total}", flush=True)

    meta = await build_sequence(params, out, progress)
    s = meta["stats"]
    print(f"{label}: {s['frames_used']}/{s['frames_found']} frames, {len(meta['epochs'])} epochs, "
          f"{s['requests']} requests, {s['bytes'] / 1e6:.0f} MB, cache hits {s['cache_hits']}, "
          f"{time.perf_counter() - t0:.1f} s (search {s['seconds']['search']:.1f} s) -> {out}")
    if s["errors"]:
        print("  errors:", s["errors"][:3])
    png = out / "quicklook.png"
    quicklook(meta, out, png)
    print("  quicklook:", png)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("target", help="preset id, 'all', a name or 'ra dec'")
    ap.add_argument("--fov", type=float, help="arcmin; default: the preset's field of view, else 6")
    ap.add_argument("--pmra", type=float)
    ap.add_argument("--pmdec", type=float)
    ap.add_argument("--ref-epoch", type=float)
    ap.add_argument("--max-epoch-days", type=float, default=45.0)
    a = ap.parse_args()
    targets = list(PRESETS.values()) if a.target == "all" else [resolve(a.target)]
    for t in targets:
        p = SequenceParams(ra=t.ra, dec=t.dec, pmra=a.pmra if a.pmra is not None else t.pmra,
                           pmdec=a.pmdec if a.pmdec is not None else t.pmdec,
                           ref_epoch=a.ref_epoch if a.ref_epoch is not None else t.ref_epoch,
                           name=t.name, fov_arcmin=a.fov if a.fov is not None else (t.fov_arcmin or 6.0),
                           max_epoch_days=a.max_epoch_days)
        asyncio.run(run(p, t.id or t.name or "target"))
        print(json.dumps({"id": p.key()}))


if __name__ == "__main__":
    main()
