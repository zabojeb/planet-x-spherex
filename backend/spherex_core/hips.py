"""A caching proxy for the all-sky HiPS behind the viewer's map (ADR-0061).

The map draws IRSA's SPHEREx DR1 HiPS cube: 102 frames of 512-pixel PNG tiles, orders 0-5. Tiles never change
within a release, so every tile is fetched from the source once, written under `<cache>/hips/<survey>/` with the
HiPS path layout, and served from disk afterwards (`Cache-Control: immutable`). The source is chosen with
`upstreams` (IRSA, or a mirror with the same layout when SPHEREX_HIPS_MIRROR_BASE is set), so a dying IRSA does
not stall every request: tiles already on disk keep being served, and a missing tile fails fast once its
service's breaker is open. `properties` and `Moc.fits` are refreshed hourly and served stale if the source is
down. Concurrent requests for the same missing tile share one download. A tile the source does not have (404)
is remembered for a day, so the client's misses do not reach IRSA again.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

import aiohttp

from . import cache, netpolicy, upstreams
from .config import settings
from .endpoints import host
from .upstreams import Endpoint

log = logging.getLogger(__name__)

# The frames the viewer shows: the middle channel of each detector (web/src/skymap.ts frameFor), frame index
# 17 * (detector - 1) + 8. Prewarming these covers the whole map the viewer can draw.
VIEWER_FRAMES = (8, 25, 42, 59, 76, 93)
MAX_ORDER = 5  # hips_order of the SPHEREx cube
META_TTL_S = 3600.0  # properties / Moc.fits
MISSING_TTL_S = 86400.0  # remembered 404s

_TILE = re.compile(r"^Norder(\d{1,2})/Dir(\d{1,7})/Npix(\d{1,9})(?:_(\d{1,3}))?\.(png|jpg|jpeg|fits)$")
_ALLSKY = re.compile(r"^Norder(\d{1,2})/Allsky(?:_(\d{1,3}))?\.(png|jpg|jpeg|fits)$")
_META = {"properties", "Moc.fits"}
CONTENT_TYPES = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "fits": "application/fits"}


@dataclass(frozen=True)
class Survey:
    id: str
    label: str
    base: str
    mirror: str = ""


SURVEYS: dict[str, Survey] = {
    "spherex-dr1": Survey("spherex-dr1", "SPHEREx DR1 all-sky cube (IRSA)", settings.hips_spherex_base.rstrip("/"),
                          settings.hips_mirror_base.rstrip("/")),
}

# IRSA serves the HiPS from its web server, not from TAP: a pseudo-service so a TAP outage does not close it.
HIPS_IRSA = Endpoint("irsa-hips", "hips", "irsa-hips", "IRSA HiPS (data/hips)")
HIPS_MIRROR = (Endpoint("hips-mirror", "hips", host(settings.hips_mirror_base), "HiPS mirror (SPHEREX_HIPS_MIRROR_BASE)",
                        priority=-1) if settings.hips_mirror_base else None)
upstreams.register(*(e for e in (HIPS_MIRROR, HIPS_IRSA) if e is not None))


class NotHipsPath(ValueError):
    """Not a path this proxy serves (guards the cache directory against arbitrary paths)."""


class Missing(LookupError):
    """The source has no such file (HTTP 404)."""

    health_neutral = True  # a missing tile says nothing about the service's health


def check_path(path: str) -> str:
    """The HiPS path if it is a tile, an Allsky image, properties or Moc.fits; raises NotHipsPath otherwise."""
    if path in _META or _TILE.match(path) or _ALLSKY.match(path):
        return path
    raise NotHipsPath(path)


def is_immutable(path: str) -> bool:
    return path not in _META


def content_type(path: str) -> str:
    if path == "properties":
        return "text/plain; charset=utf-8"
    return CONTENT_TYPES.get(path.rsplit(".", 1)[-1].lower(), "application/octet-stream")


def local_path(survey: str, path: str) -> Path:
    return cache.layout().root / "hips" / survey / check_path(path)


def _fresh(p: Path, ttl: float) -> bool:
    try:
        return time.time() - p.stat().st_mtime < ttl
    except OSError:
        return False


_session: aiohttp.ClientSession | None = None
_session_loop: asyncio.AbstractEventLoop | None = None
_inflight: dict[tuple[str, str], asyncio.Future] = {}


def _client() -> aiohttp.ClientSession:
    global _session, _session_loop
    loop = asyncio.get_running_loop()
    if _session is None or _session.closed or _session_loop is not loop:
        _session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=settings.hips_timeout_s, sock_connect=8),
            connector=aiohttp.TCPConnector(limit=64, limit_per_host=32, ttl_dns_cache=600),
            headers={"User-Agent": "planet-x-spherex-hips-proxy"},
        )
        _session_loop = loop
    return _session


def _source_url(ep: Endpoint, s: Survey, path: str) -> str:
    base = s.mirror if (HIPS_MIRROR is not None and ep is HIPS_MIRROR and s.mirror) else s.base
    return f"{base}/{path}"


async def _download(s: Survey, path: str) -> bytes:
    missing: list[str] = []

    async def one(ep: Endpoint) -> bytes:
        netpolicy.check(ep.service)  # an open breaker answers at once instead of a 20 s timeout per tile
        async with _client().get(_source_url(ep, s, path)) as r:
            if r.status == 404:
                missing.append(ep.name)
                raise Missing(path)
            if r.status != 200:
                raise RuntimeError(f"HTTP {r.status} from {ep.label} for {path}")
            return await r.read()

    try:
        data, _ = await upstreams.call_async("hips", one, hedge=False)
    except upstreams.AllFailed as e:
        if missing and len(missing) == len(e.errors):  # every source answered 404: the file does not exist
            raise Missing(path) from e
        raise
    return data


def _write(p: Path, data: bytes) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f"{p.name}.{os.getpid()}.{time.time_ns()}.tmp")
    tmp.write_bytes(data)
    tmp.replace(p)


async def get(survey: str, path: str) -> Path:
    """Local file for `path` of `survey`, downloading it once. Raises KeyError (unknown survey), NotHipsPath,
    Missing (the source has no such file) or upstreams.AllFailed / RuntimeError (source unavailable and nothing
    on disk)."""
    s = SURVEYS[survey]
    p = local_path(survey, path)
    meta = not is_immutable(path)
    if p.exists() and (not meta or _fresh(p, META_TTL_S)):
        return p
    gone = p.with_name(p.name + ".404")
    if _fresh(gone, MISSING_TTL_S):
        raise Missing(path)
    key = (survey, path)
    fut = _inflight.get(key)
    if fut is None:
        fut = asyncio.get_running_loop().create_future()
        _inflight[key] = fut
        try:
            data = await _download(s, path)
            await asyncio.to_thread(_write, p, data)
            fut.set_result(p)
        except Missing as e:
            await asyncio.to_thread(_write, gone, b"")
            fut.set_exception(e)
        except Exception as e:  # noqa: BLE001 - a stale properties file beats an error
            if meta and p.exists():
                log.warning("hips %s/%s refresh failed (%s); serving the cached copy", survey, path, e)
                fut.set_result(p)
            else:
                fut.set_exception(e)
        finally:
            _inflight.pop(key, None)
            if not fut.done():
                fut.cancel()
    return await asyncio.shield(fut)


# ---------------------------------------------------------------- prewarm


def tiles(order: int) -> int:
    return 12 * 4 ** order


def tile_paths(order: int, frame: int | None) -> list[str]:
    """Every tile of one order (and frame of a cube), in HiPS layout (Dir = 10000 tiles each)."""
    sfx = "" if frame is None else f"_{frame}"
    return [f"Norder{order}/Dir{(n // 10000) * 10000}/Npix{n}{sfx}.png" for n in range(tiles(order))]


def plan(frames: tuple[int, ...] = VIEWER_FRAMES, max_order: int = MAX_ORDER) -> list[str]:
    """What a prewarm fetches: properties, Moc.fits, the order-3 Allsky image and every tile up to max_order,
    for each frame; low orders first so a partial run already helps the zoomed-out map."""
    out = ["properties", "Moc.fits"] + [f"Norder3/Allsky_{f}.png" for f in frames]
    for order in range(max_order + 1):
        for f in frames:
            out += tile_paths(order, f)
    return out


def cached_bytes(survey: str = "spherex-dr1") -> tuple[int, int]:
    """(files, bytes) of the survey's cache on disk."""
    root = cache.layout().root / "hips" / survey
    n = size = 0
    for dirpath, _, files in os.walk(root):
        for name in files:
            if name.endswith((".png", ".jpg", ".fits")) or name in _META:
                n += 1
                size += os.path.getsize(os.path.join(dirpath, name))
    return n, size


async def prewarm(paths: list[str], survey: str = "spherex-dr1", concurrency: int = 24, progress=None) -> dict:
    """Fetch every path not on disk yet, `concurrency` at a time. Returns counts and bytes downloaded."""
    sem = asyncio.Semaphore(concurrency)
    done = {"fetched": 0, "cached": 0, "missing": 0, "failed": 0, "bytes": 0}
    t0 = time.perf_counter()

    async def one(path: str) -> None:
        p = local_path(survey, path)
        if p.exists() and is_immutable(path):
            done["cached"] += 1
            return
        async with sem:
            try:
                got = await get(survey, path)
                done["fetched"] += 1
                done["bytes"] += got.stat().st_size
            except Missing:
                done["missing"] += 1
            except Exception as e:  # noqa: BLE001 - keep going, report at the end
                done["failed"] += 1
                if done["failed"] <= 5:
                    log.warning("hips prewarm %s failed: %s", path, e)
        if progress:
            progress(done)

    step = 2000  # bounded task count so a 100k-tile plan does not create 100k coroutines at once
    for i in range(0, len(paths), step):
        await asyncio.gather(*(one(x) for x in paths[i:i + step]))
    done["seconds"] = round(time.perf_counter() - t0, 1)
    return done
