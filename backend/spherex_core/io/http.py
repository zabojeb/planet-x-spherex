"""Pooled async HTTP client for byte-range reads (S3 or any server honouring Range), with retries."""

from __future__ import annotations

import asyncio
import logging
import random
import time

import aiohttp

log = logging.getLogger(__name__)


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
        headers = {"Range": f"bytes={start}-{start + length - 1}"}
        delay = 0.5
        name = url.rsplit("/", 1)[-1]
        for attempt in range(self.retries):
            t0 = time.perf_counter()
            reason = None
            try:
                async with self.session.get(url, headers=headers) as r:
                    if r.status in (200, 206):
                        data = await r.read()
                        self.requests += 1
                        self.bytes_read += len(data)
                        return data if r.status == 206 else data[start:start + length]
                    if r.status < 500 and r.status != 429:
                        raise RuntimeError(f"HTTP {r.status} for {url}")
                    reason = f"HTTP {r.status}"
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                reason = f"{e.__class__.__name__}: {e}"[:200]
                if attempt == self.retries - 1:
                    log.warning("range read gave up after %d attempts: %s bytes=%d+%d (%s, last try %.1f s)",
                                self.retries, name, start, length, reason, time.perf_counter() - t0)
                    raise
            self.retries_done += 1
            wait = delay * (1.0 + random.random())
            log.debug("range read retry %d/%d in %.1f s: %s bytes=%d+%d (%s after %.1f s)", attempt + 1,
                      self.retries, wait, name, start, length, reason, time.perf_counter() - t0)
            await asyncio.sleep(wait)
            delay *= 2
        raise RuntimeError(f"range read failed after {self.retries} attempts: {url}")
