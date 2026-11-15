"""Cutouts cut on IRSA's side: the IBE cutout service for SPHEREx Level 2 files (ADR-0038).

    GET https://irsa.ipac.caltech.edu/ibe/data/spherex/.../level2_....fits?center=<ra>,<dec>deg&size=<n>pix

returns a MEF with every extension cut to the same window: PRIMARY, IMAGE, FLAGS (uncompressed int32), VARIANCE,
ZODI, then the EPSF table unmodified (~4 MB) and WCS-WAVE. The response is read as a stream and closed after the
VARIANCE data, so about 160 KB of the 4.2 MB answer is transferred for a 6' field, in one request instead of
the three rounds of range reads, and FLAGS needs no decoding. EPSF and WCS-WAVE are read once per detector from
S3 as before (Level2Reader.epsf_table / wave_map).

Where the window is: the cutout's alternate WCS "A" maps its pixels to detector pixels (CRPIX1A = 1 - x0,
CRVAL1A = 0, CDELT1A = 1). The detector-frame header is restored by adding (x0, y0) to CRPIX1/2 and setting
NAXIS1/2 to the detector size; that gives back the slim header of the file itself, and the cutout's pixels are
the file's pixels (both checked against the range reader, value by value).

Limits: the service answers "503 Server busy" to part of the requests (20-35 % with 1-8 requests in flight
from the dev server, ~65 % with 16), so the reader keeps only a few requests in flight and reads from S3 when the
service is busy, slow or answers with something unusable.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

import aiohttp
import numpy as np
from astropy.io import fits

from ..flags import FlagDefs, read_flag_defs
from .fitsheader import CARD, padded, slim_header

log = logging.getLogger(__name__)

DETECTOR_NPIX = 2040  # SPHEREx detectors are 2040 x 2040 (IMAGE NAXIS1/2 of every Level 2 file)
WANTED = ("IMAGE", "FLAGS", "VARIANCE")  # extensions read from the stream, in file order
READ_SIZE = 1 << 16


class IrsaUnavailable(Exception):
    """The cutout service declined (busy), failed, timed out or sent something the reader cannot use."""


@dataclass
class IrsaCut:
    header: fits.Header  # slim IMAGE header of the detector frame (CRPIX and NAXIS restored)
    x0: int  # detector position of the cutout's first column / row (0-based)
    y0: int
    image: np.ndarray  # float32 [h, w]
    flags: np.ndarray  # int32
    variance: np.ndarray  # float32
    flag_defs: FlagDefs  # bit definitions of the FLAGS header, only when asked for


def _cards(buf: bytes, start: int, end: int):
    for pos in range(start, end, CARD):
        yield buf[pos:pos + CARD]


def _header_end(buf: bytes | bytearray, start: int) -> int | None:
    """Offset just past the 2880-byte block holding END of the header at buf[start], or None if not there yet."""
    pos = start
    while pos + CARD <= len(buf):
        card = buf[pos:pos + CARD]
        if card[:3] == b"END" and not card[3:].strip():
            return start + padded(pos + CARD - start)
        pos += CARD
    return None


def _value(card: bytes) -> str:
    return card[10:].split(b"/", 1)[0].strip().decode("ascii", "replace").strip("'").strip()


def _hdu_shape(buf: bytes | bytearray, start: int, end: int) -> tuple[str, int, tuple[int, ...], int]:
    """(EXTNAME, BITPIX, shape (NAXISn..NAXIS1), data bytes before padding) from the raw header cards."""
    keys: dict[str, str] = {}
    for card in _cards(buf, start, end):
        k = card[:8].decode("ascii", "replace").strip()
        if k in ("EXTNAME", "BITPIX", "NAXIS", "PCOUNT", "GCOUNT") or (k.startswith("NAXIS") and k[5:].isdigit()):
            keys[k] = _value(card)
    naxis = int(keys.get("NAXIS", 0))
    dims = tuple(int(keys[f"NAXIS{i}"]) for i in range(naxis, 0, -1))
    bitpix = int(keys.get("BITPIX", 8))
    n = int(np.prod(dims)) if naxis else 0
    nbytes = 0 if naxis == 0 else abs(bitpix) // 8 * int(keys.get("GCOUNT", 1) or 1) * (int(keys.get("PCOUNT", 0) or 0) + n)
    return keys.get("EXTNAME", ""), bitpix, dims, nbytes


def needed_bytes(buf: bytes | bytearray) -> int | None:
    """Bytes of the cutout up to the end of the VARIANCE data, once the headers up to it have arrived."""
    pos, seen = 0, 0
    while True:
        he = _header_end(buf, pos)
        if he is None:
            return None
        name, _, _, nbytes = _hdu_shape(buf, pos, he)
        if name in WANTED:
            seen += 1
            if seen == len(WANTED):
                return he + nbytes
        pos = he + padded(nbytes)


def parse_cutout(buf: bytes | bytearray, want_flag_defs: bool = False) -> IrsaCut | None:
    """The IMAGE, FLAGS and VARIANCE of a cutout MEF from its first bytes; None while they are incomplete."""
    pos, ext = 0, {}
    image_header = flags_header = None
    while len(ext) < len(WANTED):
        he = _header_end(buf, pos)
        if he is None:
            return None
        name, bitpix, dims, nbytes = _hdu_shape(buf, pos, he)
        de = he + padded(nbytes)
        if name in WANTED:
            if len(buf) < he + nbytes:
                return None
            if len(dims) != 2 or bitpix not in (-32, 32):
                raise IrsaUnavailable(f"unexpected {name} extension: BITPIX {bitpix}, shape {dims}")
            ext[name] = np.frombuffer(bytes(buf[he:he + nbytes]), dtype=">f4" if bitpix == -32 else ">i4").reshape(dims)
            if name == "IMAGE":
                image_header = fits.Header.fromstring(bytes(buf[pos:he]).decode("ascii", "replace"))
            elif name == "FLAGS" and want_flag_defs:
                flags_header = fits.Header.fromstring(bytes(buf[pos:he]).decode("ascii", "replace"))
        pos = de
    h = image_header
    try:
        if abs(float(h["CDELT1A"]) - 1) > 1e-9 or abs(float(h["CDELT2A"]) - 1) > 1e-9 \
                or float(h.get("CRVAL1A", 0)) != 0 or float(h.get("CRVAL2A", 0)) != 0:
            raise IrsaUnavailable("unexpected detector WCS 'A' in the cutout")
        x0, y0 = 1.0 - float(h["CRPIX1A"]), 1.0 - float(h["CRPIX2A"])
    except KeyError as e:
        raise IrsaUnavailable(f"cutout header lacks {e}") from None
    if x0 != round(x0) or y0 != round(y0):
        raise IrsaUnavailable("cutout offset is not a whole pixel")
    x0, y0 = int(round(x0)), int(round(y0))
    img = ext["IMAGE"]
    hh, ww = img.shape
    if x0 < 0 or y0 < 0 or x0 + ww > DETECTOR_NPIX or y0 + hh > DETECTOR_NPIX \
            or ext["FLAGS"].shape != img.shape or ext["VARIANCE"].shape != img.shape:
        raise IrsaUnavailable("cutout window does not fit the detector")
    slim = slim_header(h)
    slim["NAXIS1"], slim["NAXIS2"] = DETECTOR_NPIX, DETECTOR_NPIX
    slim["CRPIX1"] = float(h["CRPIX1"]) + x0
    slim["CRPIX2"] = float(h["CRPIX2"]) + y0
    return IrsaCut(slim, x0, y0, img.astype(np.float32), ext["FLAGS"].astype(np.int32),
                   ext["VARIANCE"].astype(np.float32), read_flag_defs(flags_header) if flags_header is not None else {})


class IrsaCutouts:
    """Client for the cutout service, shared by all reads of a build: at most `max_in_flight` requests at once
    (callers that find no free slot read from S3 instead), each with its own deadline."""

    def __init__(self, max_in_flight: int = 8, timeout: float = 15.0, gate=None):
        self.max_in_flight = max(0, max_in_flight)
        self.timeout = timeout
        self.gate = gate  # a semaphore shared by every process of a build (multiprocessing), or None
        self.in_flight = 0
        self.session: aiohttp.ClientSession | None = None
        self.requests = self.served = self.busy = self.failed = 0
        self.bytes_read = 0
        self.seconds = 0.0  # summed wall time of successful requests

    async def __aenter__(self) -> "IrsaCutouts":
        conn = aiohttp.TCPConnector(limit=max(self.max_in_flight, 1), ttl_dns_cache=600)
        self.session = aiohttp.ClientSession(connector=conn, timeout=aiohttp.ClientTimeout(
            total=self.timeout, sock_connect=min(self.timeout, 8.0)))
        return self

    async def __aexit__(self, *exc) -> None:
        if self.session is not None:
            await self.session.close()
            self.session = None

    def try_slot(self) -> bool:
        """Take a request slot if one is free (do not wait: the caller reads from S3 otherwise)."""
        if self.session is None or self.in_flight >= self.max_in_flight:
            return False
        if self.gate is not None and not self.gate.acquire(False):
            return False
        self.in_flight += 1
        return True

    def release(self) -> None:
        if self.in_flight > 0:
            self.in_flight -= 1
            if self.gate is not None:
                self.gate.release()

    def stats(self) -> dict:
        return {"requests": self.requests, "served": self.served, "busy": self.busy, "failed": self.failed,
                "bytes": self.bytes_read, "s_mean": round(self.seconds / self.served, 3) if self.served else None}

    async def fetch(self, url: str, ra: float, dec: float, size: int, want_flag_defs: bool = False) -> IrsaCut:
        """A size x size pixel cutout centred on (ra, dec) of the file at IRSA url; releases the slot taken
        with try_slot(). Raises IrsaUnavailable when the service cannot deliver it."""
        assert self.session is not None, "use IrsaCutouts as an async context manager"
        self.requests += 1
        t0 = time.perf_counter()
        q = f"{url}?center={ra:.8f},{dec:.8f}deg&size={int(size)}pix"
        try:
            async with self.session.get(q) as r:
                if r.status != 200:
                    if r.status in (429, 503):
                        self.busy += 1
                    else:
                        self.failed += 1
                    raise IrsaUnavailable(f"HTTP {r.status}")
                buf = bytearray()
                need = None
                while need is None or len(buf) < need:
                    chunk = await r.content.read(READ_SIZE)
                    if not chunk:
                        raise IrsaUnavailable("cutout ended before the VARIANCE data")
                    buf += chunk
                    if need is None:
                        need = needed_bytes(buf)
                cut = parse_cutout(buf, want_flag_defs)
                if cut is None:
                    raise IrsaUnavailable("cutout could not be parsed")
                # Leaving the block with the body unread closes this connection: the rest (EPSF, ~4 MB) is
                # never transferred.
            self.bytes_read += len(buf)
            self.served += 1
            self.seconds += time.perf_counter() - t0
            return cut
        except IrsaUnavailable:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, KeyError) as e:
            self.failed += 1
            raise IrsaUnavailable(repr(e)[:200]) from None
        finally:
            self.release()
