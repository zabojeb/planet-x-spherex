"""Fill the server's HiPS cache (ADR-0061) so the sky map never waits for IRSA.

    python scripts/prewarm_hips.py --plan                 # tile counts per order and an estimated size
    python scripts/prewarm_hips.py                        # the viewer's six frames, every order (0-5)
    python scripts/prewarm_hips.py --frames 8 --max-order 3 --concurrency 32

Already cached files are skipped, so it can be stopped and restarted. Run it with SPHEREX_CACHE_DIR set to the
API's cache directory; low orders are fetched first, so a partial run already speeds up the zoomed-out map.
"""

from __future__ import annotations

import argparse
import asyncio
import random
import time
import urllib.request

from spherex_core import hips


def estimate(frames: tuple[int, ...], max_order: int, samples: int = 6) -> None:
    s = hips.SURVEYS["spherex-dr1"]
    total_tiles = total_bytes = 0
    print(f"{'order':>5} {'tiles/frame':>11} {'tiles':>8} {'avg KB':>7} {'GB':>6}")
    for order in range(max_order + 1):
        n = hips.tiles(order)
        sizes = []
        for _ in range(samples):
            pix = random.randrange(n)
            url = f"{s.base}/Norder{order}/Dir{(pix // 10000) * 10000}/Npix{pix}_{random.choice(frames)}.png"
            try:
                with urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=20) as r:
                    sizes.append(int(r.headers.get("Content-Length", "0")))
            except Exception:  # noqa: BLE001 - a sample less
                pass
        avg = sum(sizes) / len(sizes) if sizes else 50_000
        b = avg * n * len(frames)
        total_tiles += n * len(frames)
        total_bytes += b
        print(f"{order:>5} {n:>11} {n * len(frames):>8} {avg / 1e3:>7.0f} {b / 1e9:>6.2f}")
    print(f"total: {total_tiles} tiles, ~{total_bytes / 1e9:.1f} GB for frames {list(frames)}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames", type=int, nargs="*", default=list(hips.VIEWER_FRAMES))
    ap.add_argument("--max-order", type=int, default=hips.MAX_ORDER)
    ap.add_argument("--concurrency", type=int, default=24)
    ap.add_argument("--plan", action="store_true", help="only print tile counts and an estimated size")
    a = ap.parse_args()
    frames = tuple(a.frames)
    if a.plan:
        return estimate(frames, a.max_order)
    paths = hips.plan(frames, a.max_order)
    t0 = time.perf_counter()
    last = [0.0]

    def progress(d: dict) -> None:
        if time.perf_counter() - last[0] > 15:
            last[0] = time.perf_counter()
            n = d["fetched"] + d["cached"] + d["missing"] + d["failed"]
            print(f"  {n}/{len(paths)} fetched={d['fetched']} cached={d['cached']} missing={d['missing']} "
                  f"failed={d['failed']} {d['bytes'] / 1e9:.2f} GB {time.perf_counter() - t0:.0f} s", flush=True)

    out = asyncio.run(hips.prewarm(paths, concurrency=a.concurrency, progress=progress))
    files, size = hips.cached_bytes()
    print(f"done: {out}; cache now {files} files, {size / 1e9:.2f} GB")


if __name__ == "__main__":
    main()
