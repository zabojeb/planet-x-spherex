"""Build archival epochs (2MASS, AllWISE, NEOWISE) for a built sequence, check the target's position in
each frame and save a quick-look PNG.

    python scripts/build_archival.py <sequence id> [--fov 12] [--surveys 2mass,allwise,neowise]
"""

from __future__ import annotations

import argparse
import asyncio
import time

import numpy as np

from spherex_core import services
from spherex_core.archival import SURVEYS, archival_dir, build_archival


def centroid_offsets(meta: dict, stack: np.ndarray, box: int = 4) -> list[tuple[str, float | None]]:
    """Light-weighted centroid near each frame's predicted target pixel, as an offset in arcsec."""
    out = []
    scale = meta["grid"]["pixscale_arcsec"]
    n = stack.shape[-1]
    for f, img in zip(meta["frames"], stack):
        x, y = f["target_px"]
        ix, iy = int(round(x)), int(round(y))
        if not (box <= ix < n - box and box <= iy < n - box):
            out.append((f["label"], None))
            continue
        sub = img[iy - box: iy + box + 1, ix - box: ix + box + 1].astype(float)
        sub = np.nan_to_num(sub - np.nanmedian(img))
        sub[sub < 0] = 0
        if sub.sum() <= 0:
            out.append((f["label"], None))
            continue
        gy, gx = np.mgrid[iy - box: iy + box + 1, ix - box: ix + box + 1]
        cx, cy = (gx * sub).sum() / sub.sum(), (gy * sub).sum() / sub.sum()
        out.append((f["label"], float(np.hypot(cx - x, cy - y) * scale)))
    return out


def quicklook(meta: dict, stack: np.ndarray, png: str) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    k = len(meta["frames"])
    cols = min(k, 8)
    rows = int(np.ceil(k / cols))
    fig, axes = plt.subplots(rows, cols, figsize=(1.8 * cols, 1.9 * rows), squeeze=False)
    for ax in axes.flat:
        ax.axis("off")
    for ax, f, img in zip(axes.flat, meta["frames"], stack):
        finite = img[np.isfinite(img)]
        lo, hi = np.percentile(finite, [1, 99.7]) if finite.size else (0, 1)
        ax.imshow(np.arcsinh((img - lo) / max(hi - lo, 1e-9) * 10), origin="lower", cmap="gray")
        ax.plot(*f["target_px"], "+", color="tab:orange", ms=9, mew=0.8)
        ax.set_title(f"{f['label']} {str(f['date'])[:7]}", fontsize=6)
    fig.tight_layout()
    fig.savefig(png, dpi=110)


async def run(seq_id: str, fov: float | None, surveys: tuple[str, ...]) -> None:
    smeta = services.load_sequence(seq_id)
    d = archival_dir(seq_id, smeta, fov)
    t0 = time.perf_counter()
    last = [0.0]

    def progress(stage, done, total):
        if time.perf_counter() - last[0] > 3 or done == total:
            last[0] = time.perf_counter()
            print(f"  {stage} {done}/{total}", flush=True)

    meta = await build_archival(smeta, d, fov, surveys, progress)
    stack = np.fromfile(d / "frames.f32", dtype="<f4").reshape(meta["array"]["shape"])
    s = meta["stats"]
    print(f"{seq_id}: {len(meta['frames'])} frames on a {meta['grid']['n']} px grid "
          f"({meta['grid']['fov_arcmin']:.1f}'), {s['images_found']} images found, {s['requests']} requests, "
          f"{s['bytes'] / 1e6:.0f} MB, {time.perf_counter() - t0:.1f} s -> {d}")
    for (label, off), f in zip(centroid_offsets(meta, stack), meta["frames"]):
        print(f"  {label:<11} {str(f['date'])[:10]}  n={f['n_images']:<3} cover={f.get('coverage', 0):.2f} "
              f"target_px=({f['target_px'][0]:.1f},{f['target_px'][1]:.1f}) in_field={f['target_in_field']} "
              f"centroid_offset={'-' if off is None else f'{off:.1f}'}\"")
    if s["errors"]:
        print("  errors:", s["errors"][:3])
    if len(meta["frames"]):
        quicklook(meta, stack, str(d / "quicklook.png"))
        print("  quicklook:", d / "quicklook.png")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("sequence")
    ap.add_argument("--fov", type=float)
    ap.add_argument("--surveys", default=",".join(SURVEYS))
    a = ap.parse_args()
    asyncio.run(run(a.sequence, a.fov, tuple(s.strip() for s in a.surveys.split(",") if s.strip())))


if __name__ == "__main__":
    main()
