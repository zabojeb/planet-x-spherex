"""Pooled async HTTP client for byte-range reads (S3 or any server honouring Range), with retries.

SPHEREx files exist in more than one place with the same bytes: the AWS open-data bucket, IRSA's ibe file
server and, optionally, our own S3-compatible mirror (SPHEREX_FILE_MIRROR_BASE). Callers keep using the
canonical AWS URL (it is also the cache key); `get` maps it to every source, asks the one that has been fastest
lately (upstreams "spherex-files"), and starts the next source when a read runs past that source's usual time
(hedging), so one slow or failing read no longer holds up a build. Identical reads in flight at the same time
are shared.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import time

import aiohttp

from .. import endpoints, upstreams
from ..config import settings

log = logging.getLogger(__name__)
HEDGE_READS = os.environ.get("SPHEREX_HEDGE_READS", "1") not in ("0", "false", "no")


class NotHere(Exception):
    """The source does not have this file (a partial mirror): try the next one, not a health failure."""

    health_neutral = True


class RangeClient:
    """Use as `async with RangeClient() as c: data = await c.get(url, start, length)`.

    Retries throttling (429) and server errors (5xx) with jittered exponential backoff and counts
    requests and bytes for the build statistics.
    """

    def __init__(self, max_connections: int = 96, timeout: float = 120.0, retries: int = 5):
        self.max_connections, self.timeout, self.retries = max_connections, timeout, retries
        self.session: aiohttp.ClientSession | None = None
        self.bytes_read = 0
        self.requests = 0
        self.retries_done = 0
        self.hedges = 0
        self.by_source: dict[str, int] = {}
        self._inflight: dict[tuple[str, int, int], asyncio.Future] = {}
        self._slots: dict[str, asyncio.Semaphore] = {}

    async def __aenter__(self) -> "RangeClient":
        conn = aiohttp.TCPConnector(limit=self.max_connections, limit_per_host=self.max_connections,
                                    ttl_dns_cache=600)
        self.session = aiohttp.ClientSession(
            connector=conn, timeout=aiohttp.ClientTimeout(total=self.timeout, sock_connect=20))
        return self

    async def __aexit__(self, *exc) -> None:
        if self.session is not None:
            await self.session.close()

    async def get(self, url: str, start: int, length: int) -> bytes:
        if length <= 0:
            return b""
        assert self.session is not None, "use RangeClient as an async context manager"
        key = (url, start, length)
        fut = self._inflight.get(key)
        if fut is not None:  # the same bytes are already on their way
            return await asyncio.shield(fut)
        fut = asyncio.ensure_future(self._get_any(url, start, length))
        self._inflight[key] = fut
        fut.add_done_callback(lambda _f: self._inflight.pop(key, None))
        return await asyncio.shield(fut)  # a cancelled caller must not cancel a read others are waiting for

    async def _get_any(self, url: str, start: int, length: int) -> bytes:
        if not url.startswith(settings.s3_base):
            return await self._get_one(url, start, length, self.retries)
        mappable = [e for e in upstreams.ordered("spherex-files") if endpoints.file_url(e, url)]
        if len(mappable) <= 1:
            return await self._get_one(url, start, length, self.retries)
        last = mappable[-1].name
        hedging = HEDGE_READS

        async def ask(ep):
            # earlier sources get fewer retries: falling through to the next source beats a long back-off
            retries = self.retries if ep.name == last else 2
            slot = self._slots.setdefault(ep.name, asyncio.Semaphore(ep.concurrency or self.max_connections))
            async with slot:
                data = await self._get_one(endpoints.file_url(ep, url), start, length, retries, ep)
            self.by_source[ep.name] = self.by_source.get(ep.name, 0) + 1
            return data

        try:
            data, ep = await upstreams.call_async("spherex-files", ask, hedge=hedging,
                                                  only=lambda e: e in mappable)
        except upstreams.AllFailed as e:
            raise RuntimeError(f"range read failed on every source: {url} ({e})") from e
        if ep is not mappable[0]:
            self.hedges += 1  # served by a hedge or a fallback source
        return data

    async def _get_one(self, url: str, start: int, length: int, retries: int, ep=None) -> bytes:
        headers = {"Range": f"bytes={start}-{start + length - 1}"}
        delay = 0.5
        name = url.rsplit("/", 1)[-1]
        for attempt in range(retries):
            t0 = time.perf_counter()
            reason = None
            try:
                async with self.session.get(url, headers=headers) as r:
                    if r.status in (200, 206):
                        data = await r.read()
                        self.requests += 1
                        self.bytes_read += len(data)
                        return data if r.status == 206 else data[start:start + length]
                    if r.status in (403, 404) and ep is not None and ep is endpoints.FILE_MIRROR:
                        raise NotHere(f"HTTP {r.status}")
                    if r.status < 500 and r.status != 429:
                        raise RuntimeError(f"HTTP {r.status} for {url}")
                    reason = f"HTTP {r.status}"
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                reason = f"{e.__class__.__name__}: {e}"[:200]
                if attempt == retries - 1:
                    log.warning("range read gave up after %d attempts: %s bytes=%d+%d (%s, last try %.1f s)",
                                retries, name, start, length, reason, time.perf_counter() - t0)
                    raise
            self.retries_done += 1
            wait = delay * (1.0 + random.random())
            log.debug("range read retry %d/%d in %.1f s: %s bytes=%d+%d (%s after %.1f s)", attempt + 1,
                      retries, wait, name, start, length, reason, time.perf_counter() - t0)
            await asyncio.sleep(wait)
            delay *= 2
        raise RuntimeError(f"range read failed after {retries} attempts: {url}")
