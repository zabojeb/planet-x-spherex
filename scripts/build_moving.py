"""Find SPHEREx exposures of a solar-system object with IRSA MOST and build its co-moving sequence.

    python scripts/build_moving.py 3I --begin 2025-08-01 --end 2025-08-20
    python scripts/build_moving.py Eros --type Asteroid --search-only
    python scripts/build_moving.py --naif-id 20000433 --begin 2025-09-01 --end 2025-12-31 --fov 8
"""

from __future__ import annotations

import argparse
import asyncio
import time

import numpy as np

from spherex_core import cache
from spherex_core.moving import query_most, search_summary
from spherex_core.pipeline import SequenceParams, build_sequence
from spherex_core.pipeline.results import read_array


def centre_check(meta: dict, coadds: np.ndarray, box: int = 3) -> None:
    """Where the brightest pixel near the centre sits in each epoch/detector co-add."""
    n = coadds.shape[-1]
    c = (n - 1) // 2
    for e, ep in enumerate(meta["epochs"]):
        parts = []
        for b, band in enumerate(meta["bands"]):
            img = coadds[e, b]
            if not np.isfinite(img).any():
                continue
            sub = img[c - box: c + box + 1, c - box: c + box + 1]
            if not np.isfinite(sub).any():
                continue
            jy, jx = np.unravel_index(np.nanargmax(sub), sub.shape)
            snr = (np.nanmax(sub) - np.nanmedian(img)) / (1.4826 * np.nanmedian(np.abs(img - np.nanmedian(img))))
            parts.append(f"{band['id']}:({jx - box:+d},{jy - box:+d}) S/N {snr:.0f}")
        print(f"  epoch {e} {ep['date_min'][:10]} n={ep['n']}: " + "  ".join(parts))


async def run(a) -> None:
    t0 = time.perf_counter()
    res = await asyncio.to_thread(query_most, a.name, a.type, a.begin, a.end, a.naif_id)
    s = search_summary(res)
    print(f"MOST: {s['object']}: {s['n_files']} files, {s['n_exposures']} exposures, {s['date_min']} .. "
          f"{s['date_max']} ({time.perf_counter() - t0:.1f} s)")
    if a.search_only or not s["n_files"]:
        return
    req = dict(s["suggested_request"])
    req["fov_arcmin"] = a.fov
    p = SequenceParams(**req)
    out = cache.layout().sequence(p.key())
    last = [0.0]

    def progress(stage, done, total):
        if time.perf_counter() - last[0] > 3 or done == total:
            last[0] = time.perf_counter()
            print(f"  {stage} {done}/{total}", flush=True)

    t1 = time.perf_counter()
    meta = await build_sequence(p, out, progress)
    st = meta["stats"]
    print(f"sequence {p.key()}: {st['frames_used']}/{st['frames_found']} frames, {len(meta['epochs'])} epochs, "
          f"frame={meta['grid'].get('frame')}, {time.perf_counter() - t1:.1f} s -> {out}")
    centre_check(meta, read_array(out, meta, "coadds"))
    ph = [x for x in meta["photometry"] if not x["flagged"]]
    for band in ("D1", "D3", "D5", "D6"):
        v = [x for x in ph if x["band"] == band]
        if v:
            flux = np.median([x["flux_ap_mjy"] if x.get("flux_ap_mjy") is not None else x["flux_mjy"] for x in v])
            print(f"  {band}: {len(v)} unflagged points, median aperture flux {flux:.3f} mJy, "
                  f"wl {min(x['wl'] for x in v):.2f}-{max(x['wl'] for x in v):.2f} um")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("name", nargs="?")
    ap.add_argument("--type", choices=["Asteroid", "Comet"])
    ap.add_argument("--naif-id", type=int)
    ap.add_argument("--begin")
    ap.add_argument("--end")
    ap.add_argument("--fov", type=float, default=6.0)
    ap.add_argument("--search-only", action="store_true")
    asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    main()
