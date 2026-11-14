"""Pixels of older-survey images: range reads of single-HDU FITS images, or whole small files.

AllWISE Atlas tiles (4095^2 float32, 67 MB) and NEOWISE-R frames (1016^2) are uncompressed in the public
S3 bucket, so only the rows that cover the field are read (io.RangeClient). 2MASS scan images are
gzip-compressed and small (~1.5 MB), so they are downloaded whole from IRSA.
"""

from __future__ import annotations

import asyncio
import gzip
import io
import random
from dataclasses import dataclass

import aiohttp
import numpy as np
from astropy.io import fits
from astropy.wcs import WCS

from ..io import RangeClient, parse_header, slim_header

HEAD_BYTES = 131072  # NEOWISE L1b headers are longer than 28 kB
EXTRA_KEYS = ("MAGZP", "MAGZPUNC", "BUNIT", "BAND", "ORDATE", "MJD_OBS", "DATE_OBS", "FILTER", "CROTA2")


@dataclass
class Window:
    header: fits.Header  # slim WCS + calibration keys
    x0: int
    y0: int
    data: np.ndarray  # float32, DN


def _slim(h: fits.Header) -> fits.Header:
    s = slim_header(h)
    for k in EXTRA_KEYS:
        if k in h and k not in s:
            s[k] = h[k]
    return s


def _window_bounds(wcs: WCS, nx: int, ny: int, ra: float, dec: float, half: int):
    x, y = wcs.all_world2pix(ra, dec, 0, quiet=True)
    x, y = float(x), float(y)
    if not (np.isfinite(x) and np.isfinite(y)):
        return None
    xc, yc = int(np.floor(x + 0.5)), int(np.floor(y + 0.5))
    x0, x1 = max(xc - half, 0), min(xc + half + 1, nx)
    y0, y1 = max(yc - half, 0), min(yc + half + 1, ny)
    if x1 - x0 < 3 or y1 - y0 < 3:
        return None
    return x0, x1, y0, y1


async def read_header(client: RangeClient, url: str) -> tuple[fits.Header, int]:
    head = await client.get(url, 0, HEAD_BYTES)
    p = parse_header(head)
    if p is None:
        head = await client.get(url, 0, 4 * HEAD_BYTES)
        p = parse_header(head)
        if p is None:
            raise RuntimeError(f"FITS header not found: {url}")
    return p


async def read_window(client: RangeClient, url: str, ra: float, dec: float, half: int,
                      header: tuple[fits.Header, int] | None = None) -> Window | None:
    """Square window of (2*half+1) native pixels around (ra, dec) from a primary-HDU image."""
    h, hl = header or await read_header(client, url)
    if int(h.get("NAXIS", 0)) != 2:
        raise RuntimeError(f"not a 2-D primary image: {url}")
    nx, ny, bitpix = int(h["NAXIS1"]), int(h["NAXIS2"]), int(h["BITPIX"])
    slim = _slim(h)
    bounds = _window_bounds(WCS(slim), nx, ny, ra, dec, half)
    if bounds is None:
        return None
    x0, x1, y0, y1 = bounds
    dtype = {-32: ">f4", -64: ">f8", 16: ">i2", 32: ">i4", 8: "u1"}[bitpix]
    row = nx * abs(bitpix) // 8
    raw = await client.get(url, hl + y0 * row, (y1 - y0) * row)
    data = np.frombuffer(raw, dtype=dtype).reshape(y1 - y0, nx)[:, x0:x1].astype(np.float32)
    bscale, bzero = float(h.get("BSCALE", 1.0)), float(h.get("BZERO", 0.0))
    if bscale != 1.0 or bzero != 0.0:
        data = data * np.float32(bscale) + np.float32(bzero)
    return Window(slim, x0, y0, data)


async def fetch_file(client: RangeClient, url: str, retries: int = 4) -> bytes:
    """Whole file (decompressed if gzip) with retries."""
    assert client.session is not None
    delay = 1.0
    for attempt in range(retries):
        try:
            async with client.session.get(url) as r:
                if r.status == 200:
                    raw = await r.read()
                    client.requests += 1
                    client.bytes_read += len(raw)
                    return gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw
                if r.status < 500 and r.status != 429:
                    raise RuntimeError(f"HTTP {r.status} for {url}")
        except (aiohttp.ClientError, asyncio.TimeoutError):
            if attempt == retries - 1:
                raise
        await asyncio.sleep(delay * (1.0 + random.random()))
        delay *= 2
    raise RuntimeError(f"download failed after {retries} attempts: {url}")


def window_from_file(blob: bytes, ra: float, dec: float, half: int) -> Window | None:
    """Window from an in-memory FITS file (2MASS)."""
    with fits.open(io.BytesIO(blob)) as hdul:
        hdu = hdul[0]
        h = hdu.header
        data = np.asarray(hdu.data, dtype=np.float32)
    slim = _slim(h)
    ny, nx = data.shape
    bounds = _window_bounds(WCS(slim), nx, ny, ra, dec, half)
    if bounds is None:
        return None
    x0, x1, y0, y1 = bounds
    return Window(slim, x0, y0, data[y0:y1, x0:x1].copy())
