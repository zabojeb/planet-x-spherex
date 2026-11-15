"""Build or refresh the local SPHEREx file-catalogue mirror (archive/mirror.py).

    python scripts/sync_mirror.py                 # list the bucket, mirror every new file (first run: all ~1.6 M)
    python scripts/sync_mirror.py --max-new 5000  # a quick partial run
    python scripts/sync_mirror.py --tap           # relabel from IRSA TAP (wide/deep collections), when TAP is up
    python scripts/sync_mirror.py --stats
"""

from __future__ import annotations

import argparse
import json
import logging
import time

from spherex_core.archive import mirror


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--releases", nargs="*", help="e.g. dr1 qr3 (default: those of SPHEREX_COLLECTIONS)")
    ap.add_argument("--max-new", type=int)
    ap.add_argument("--workers", type=int)
    ap.add_argument("--concurrency", type=int, default=24, help="header reads in flight per worker process")
    ap.add_argument("--tap", action="store_true", help="sync from IRSA TAP instead of the bucket")
    ap.add_argument("--stats", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    m = mirror.Mirror()
    if a.stats:
        print(json.dumps(m.stats(), indent=1))
        return
    t0 = time.perf_counter()
    last = [0.0]

    def progress(done, total):
        if time.perf_counter() - last[0] > 10 or done == total:
            last[0] = time.perf_counter()
            rate = done / max(time.perf_counter() - t0, 1e-9)
            print(f"  {done}/{total} files, {rate:.0f}/s, eta {(total - done) / max(rate, 1e-9) / 60:.1f} min",
                  flush=True)

    if a.tap:
        print(json.dumps(mirror.sync_tap(mirror=m), indent=1))
    else:
        out = mirror.sync_s3(a.releases, max_new=a.max_new, workers=a.workers, concurrency=a.concurrency, mirror=m,
                             progress=progress)
        print(json.dumps(out, indent=1))
    print(json.dumps(m.stats(), indent=1))


if __name__ == "__main__":
    main()
