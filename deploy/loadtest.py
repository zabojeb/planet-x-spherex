"""Tiny concurrent load test of the read path: python deploy/loadtest.py BASE SEQ_ID [requests] [concurrency]"""
import asyncio
import sys
import time

import aiohttp

BASE, SID = sys.argv[1], sys.argv[2]
N = int(sys.argv[3]) if len(sys.argv) > 3 else 2000
C = int(sys.argv[4]) if len(sys.argv) > 4 else 100
PATHS = [f"/api/sequences/{SID}", f"/api/sequences/{SID}/lite", f"/api/sequences/{SID}/coadds.bin?epoch=0&band=D1",
         f"/api/sequences/{SID}/coadds.bin?epoch=1&band=D6", "/api/targets", "/"]


async def main():
    lat, codes, nbytes = [], {}, 0
    sem = asyncio.Semaphore(C)
    async with aiohttp.ClientSession(headers={"Accept-Encoding": "br, gzip"}) as s:
        async def one(i):
            nonlocal nbytes
            async with sem:
                t = time.perf_counter()
                async with s.get(BASE + PATHS[i % len(PATHS)]) as r:
                    nbytes += len(await r.read())
                    codes[r.status] = codes.get(r.status, 0) + 1
                lat.append(time.perf_counter() - t)
        t0 = time.perf_counter()
        await asyncio.gather(*(one(i) for i in range(N)))
        wall = time.perf_counter() - t0
    lat.sort()
    print(f"{N} requests, concurrency {C}: {N / wall:.0f} req/s, p50 {lat[N // 2] * 1000:.0f} ms, "
          f"p95 {lat[int(N * .95)] * 1000:.0f} ms, p99 {lat[int(N * .99)] * 1000:.0f} ms, codes {codes}, "
          f"{nbytes / 1e6:.1f} MB")


asyncio.run(main())
