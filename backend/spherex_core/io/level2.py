"""Read cutouts, wavelength maps and effective PSFs from SPHEREx Level 2 MEF files with HTTP range requests.

File layout (checked on DR1 and QR3 files): PRIMARY (empty) · IMAGE (float32 2040x2040, MJy/sr) ·
FLAGS (int32 bitmask, RICE_1 tile-compressed with one tile per row) · VARIANCE (float32) ·
ZODI (float32) · EPSF (441 zone ePSFs, 33x33 oversampled 5x) · WCS-WAVE (pixel -> wavelength lookup
table). EPSF and WCS-WAVE are identical for all files of a detector and are read once per detector.

A file read for the first time costs three round trips: IMAGE header -> IMAGE rows + FLAGS header and tile
index -> FLAGS tiles + VARIANCE header and rows. Only the rows that cover the cutout are transferred, and
VARIANCE only near the target when the caller says where it is (`variance_at`).

The cache keeps one chunk per file: its slim header, its byte layout and the rows read so far over a band of
columns wider than the cutout (`keep_half`; rows arrive full-width anyway). A smaller or shifted window inside
them needs no request; a larger one reads only the missing rows, in one round trip (ADR-0037). Wavelength maps
and ePSF tables are cached per detector (see cache.py).
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import uuid
import warnings
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np
from astropy.io import fits
from astropy.io.fits.hdu.compressed._codecs import Rice1
from astropy.wcs import WCS, FITSFixedWarning

from ..cache import CacheLayout
from ..flags import FlagDefs, flag_defs_from_text, flag_defs_to_text, read_flag_defs
from .fitsheader import data_nbytes, padded, parse_header, slim_header
from .http import RangeClient
from .irsa import IrsaCut, IrsaCutouts, IrsaUnavailable

HEAD_BYTES = 65536
IRSA_MARGIN = 2  # px asked for around the window from the IRSA cutout service, which centres it by its own rounding
CHUNK_FORMAT = 1  # bump when the content of cached chunk files changes
VARIANCE_HDR_GUESS = 11520  # usual VARIANCE header length; learnt per detector and verified on every read

Rows = tuple[int, int]  # half-open range of detector rows [a, b)
SkyAt = Callable[[fits.Header], tuple[float, float]]  # sky position (ra, dec) from a file's IMAGE header

warnings.filterwarnings("ignore", category=FITSFixedWarning)
log = logging.getLogger(__name__)


@dataclass
class WaveMap:
    """Detector lookup table: pixel -> (central wavelength, bandwidth) in microns, bilinear as in FITS -TAB.

    The table is defined on raw pixel coordinates; the celestial SIP distortion does not apply to it.
    """

    x: np.ndarray  # control points along FITS axis 1, 1-based pixel coordinates
    y: np.ndarray  # control points along FITS axis 2
    values: np.ndarray  # [len(y), len(x), 2]

    def __post_init__(self) -> None:
        self.x, self.y, self.values = (np.asarray(a, dtype=float) for a in (self.x, self.y, self.values))
        if self.x[0] > self.x[-1]:
            self.x, self.values = self.x[::-1], self.values[:, ::-1]
        if self.y[0] > self.y[-1]:
            self.y, self.values = self.y[::-1], self.values[::-1]

    def __call__(self, xpix, ypix):
        """Wavelength and bandwidth (um) at 0-based numpy pixel coordinates."""
        xi = np.interp(np.asarray(xpix, dtype=float) + 1.0, self.x, np.arange(self.x.size))
        yi = np.interp(np.asarray(ypix, dtype=float) + 1.0, self.y, np.arange(self.y.size))
        i0 = np.clip(np.floor(xi).astype(int), 0, self.x.size - 2)
        j0 = np.clip(np.floor(yi).astype(int), 0, self.y.size - 2)
        tx, ty = (xi - i0)[..., None], (yi - j0)[..., None]
        v = self.values
        out = ((1 - tx) * (1 - ty) * v[j0, i0] + tx * (1 - ty) * v[j0, i0 + 1]
               + (1 - tx) * ty * v[j0 + 1, i0] + tx * ty * v[j0 + 1, i0 + 1])
        return out[..., 0], out[..., 1]


@dataclass
class EPSFTable:
    """Effective PSFs of one detector: an oversampled, pixel-convolved PSF per detector zone (21 x 21 zones of
    ~97 px, photutils ePSF convention, each array sums to 1). Identical in every DR1 and QR3 file of a
    detector (checked on three files), so it is read once per detector and cached like the WaveMap."""

    xcenter: np.ndarray  # zone centres, 0-based pixel coordinates
    ycenter: np.ndarray
    psf: np.ndarray  # [zones, ny_os, nx_os]
    oversampling: int
    cwave: np.ndarray  # central wavelength of each zone, um
    neff: np.ndarray  # effective PSF area, native pixels

    def nearest(self, x: float, y: float) -> tuple[np.ndarray, int]:
        """The ePSF of the zone whose centre is nearest to pixel (x, y), and its row index."""
        k = int(np.argmin((self.xcenter - x) ** 2 + (self.ycenter - y) ** 2))
        return self.psf[k], k


@dataclass
class Cutout:
    url: str
    header: fits.Header  # slim IMAGE header; its WCS describes the full detector frame
    x0: int  # position of the cutout's first column/row in the full frame (0-based)
    y0: int
    image: np.ndarray  # float32, MJy/sr, zodiacal light not subtracted
    variance: np.ndarray  # float32, (MJy/sr)^2
    flags: np.ndarray  # int32 bitmask
    _wcs: WCS | None = field(default=None, repr=False)
    flag_defs: str = ""  # this file's FLAGS bit definitions (flags.flag_defs_to_text), "" if not known

    @property
    def wcs(self) -> WCS:
        if self._wcs is None:
            self._wcs = WCS(self.header)
        return self._wcs


@dataclass
class FlagsLayout:
    """Where the FLAGS rows of a file are and how to decode them: a plain image, or row-tiled RICE/GZIP
    compression (one tile per detector row, as in every DR1 and QR3 file checked)."""

    start: int  # plain: first byte of the data; tiled: first byte of the heap
    row_bytes: int = 0  # plain only
    dtype: str = ">i4"  # plain only
    cmp: str = ""  # tiled only: RICE_1, GZIP_1 or GZIP_2
    tilesize: int = 0
    bytepix: int = 4
    blocksize: int = 32
    desc: np.ndarray | None = None  # tiled only: (nbytes, heap offset) of every row

    def span(self, a: int, b: int) -> tuple[int, int]:
        """(first byte, length) of the bytes holding rows a..b-1."""
        if self.desc is None:
            return self.start + a * self.row_bytes, (b - a) * self.row_bytes
        sel = self.desc[a:b]
        sel = sel[sel[:, 0] > 0]
        if not len(sel):
            return self.start, 0
        lo, hi = int(sel[:, 1].min()), int((sel[:, 1] + sel[:, 0]).max())
        return self.start + lo, hi - lo

    def decode(self, raw: bytes, a: int, b: int, x0: int, x1: int) -> np.ndarray:
        """Rows a..b-1, columns x0..x1-1 as int32, from the bytes at span(a, b)."""
        if self.desc is None:
            return np.frombuffer(raw, dtype=self.dtype).reshape(b - a, -1)[:, x0:x1].astype(np.int32)
        base = self.span(a, b)[0] - self.start
        rice = Rice1(blocksize=self.blocksize, bytepix=self.bytepix, tilesize=self.tilesize)
        rows = []
        for nbytes, off in self.desc[a:b]:
            if nbytes == 0:
                rows.append(np.zeros(x1 - x0, dtype=np.int32))
                continue
            chunk = raw[off - base: off - base + nbytes]
            if self.cmp == "RICE_1":
                vals = rice.decode(np.frombuffer(chunk, dtype=np.uint8))
            elif self.cmp in ("GZIP_1", "GZIP_2"):
                vals = np.frombuffer(zlib.decompress(chunk, 15 + 32), dtype=f">i{self.bytepix}")
            else:
                raise NotImplementedError(f"compression {self.cmp}")
            rows.append(np.asarray(vals)[x0:x1])
        return np.stack(rows).astype(np.int32)

    def meta(self) -> dict:
        return {"start": self.start, "row_bytes": self.row_bytes, "dtype": self.dtype, "cmp": self.cmp,
                "tilesize": self.tilesize, "bytepix": self.bytepix, "blocksize": self.blocksize}


@dataclass
class FileLayout:
    """Byte offsets of one Level 2 file, learnt while reading it and cached with it, so that later reads of any
    rows take one round trip."""

    nx: int
    ny: int
    img_off: int  # first byte of the IMAGE data
    var_hdr: int | None = None  # first byte of the VARIANCE header; known once the FLAGS header was read
    var_off: int | None = None  # first byte of the VARIANCE data; known once its header length was verified
    flags: FlagsLayout | None = None


def _empty(width: int, dtype) -> np.ndarray:
    return np.zeros((0, max(width, 0)), dtype=dtype)


@dataclass
class _Chunk:
    """What the cache holds of one file: slim IMAGE header, byte layout (None for an entry converted from the old
    per-request cache), and rows of IMAGE + FLAGS and of VARIANCE over the column band [cx0, cx1)."""

    header: fits.Header
    layout: FileLayout | None = None
    cx0: int = 0
    cx1: int = 0
    rows: Rows = (0, 0)
    image: np.ndarray = field(default_factory=lambda: _empty(0, np.float32))
    flags: np.ndarray = field(default_factory=lambda: _empty(0, np.int32))
    vrows: Rows = (0, 0)
    variance: np.ndarray = field(default_factory=lambda: _empty(0, np.float32))
    flag_defs: str = ""

    def reset_band(self, cx0: int, cx1: int) -> None:
        """Hold columns [cx0, cx1) from now on; rows read for another band are dropped."""
        self.cx0, self.cx1 = cx0, cx1
        self.rows = self.vrows = (0, 0)
        self.image, self.flags, self.variance = (_empty(cx1 - cx0, np.float32), _empty(cx1 - cx0, np.int32),
                                                 _empty(cx1 - cx0, np.float32))

    def save(self, path: Path) -> None:
        lay = self.layout
        meta = None if lay is None else {
            "nx": lay.nx, "ny": lay.ny, "img_off": lay.img_off, "var_hdr": lay.var_hdr, "var_off": lay.var_off,
            "flags": None if lay.flags is None else lay.flags.meta()}
        desc = lay.flags.desc if lay is not None and lay.flags is not None else None
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.stem}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp.npz")
        np.savez(tmp, v=CHUNK_FORMAT, header=self.header.tostring(), layout=json.dumps(meta),
                 desc=desc if desc is not None else np.zeros((0, 2), np.int64),
                 band=np.array([self.cx0, self.cx1]), rows=np.array(self.rows), image=self.image, flags=self.flags,
                 vrows=np.array(self.vrows), variance=self.variance, flag_defs=self.flag_defs)
        tmp.replace(path)

    @classmethod
    def load(cls, path: Path) -> "_Chunk | None":
        """The cached chunk, or None if there is none or it is unreadable or of another format."""
        try:
            with np.load(path, allow_pickle=False) as z:
                if int(z["v"]) != CHUNK_FORMAT:
                    return None
                meta = json.loads(str(z["layout"]))
                lay = None
                if meta is not None:
                    fm = meta.pop("flags")
                    lay = FileLayout(**meta)
                    if fm is not None:
                        lay.flags = FlagsLayout(**fm, desc=z["desc"] if len(z["desc"]) else None)
                cx0, cx1 = (int(v) for v in z["band"])
                return cls(fits.Header.fromstring(str(z["header"])), lay, cx0, cx1, tuple(int(v) for v in z["rows"]),
                           z["image"], z["flags"], tuple(int(v) for v in z["vrows"]), z["variance"],
                           str(z["flag_defs"]))
        except Exception:  # noqa: BLE001 - missing or corrupted entry: read the file again
            return None


Window = tuple[int, int, int, int, int]  # x0, x1, y0, y1 (half-open, detector pixels) and the centre column


def _window(wcs: WCS, header: fits.Header, ra: float, dec: float, half: int) -> Window | None:
    """The cutout window around (ra, dec), clipped to the detector; None when (almost) nothing is on it."""
    nx, ny = int(header["NAXIS1"]), int(header["NAXIS2"])
    x, y = wcs.all_world2pix(ra, dec, 0, quiet=True)
    x, y = float(x), float(y)
    if not (np.isfinite(x) and np.isfinite(y)):
        return None
    xc, yc = int(np.floor(x + 0.5)), int(np.floor(y + 0.5))
    x0, x1 = max(xc - half, 0), min(xc + half + 1, nx)
    y0, y1 = max(yc - half, 0), min(yc + half + 1, ny)
    if x1 - x0 < 3 or y1 - y0 < 3:
        return None
    return x0, x1, y0, y1, xc


def _holds(ch: "_Chunk", win: Window) -> bool:
    """The chunk has IMAGE and FLAGS for the whole window (VARIANCE may still be partial)."""
    x0, x1, y0, y1, _ = win
    return ch.cx0 <= x0 and x1 <= ch.cx1 and ch.rows[0] <= y0 and y1 <= ch.rows[1]


def _plan(held: Rows | None, need: Rows) -> tuple[Rows, list[Rows]]:
    """Rows to hold after a read and the row ranges to read: the union with what is held when the two touch or
    overlap, otherwise just what is needed (the rows held are then dropped)."""
    a, b = need
    if held is None or held[1] <= held[0] or b < held[0] or a > held[1]:
        return need, [need]
    lo, hi = min(a, held[0]), max(b, held[1])
    return (lo, hi), [r for r in ((lo, held[0]), (held[1], hi)) if r[1] > r[0]]


def _merge(old: np.ndarray, old_rows: Rows, rows: Rows, parts: list[tuple[Rows, np.ndarray]]) -> np.ndarray:
    """Rows `rows` built from the rows still held and the ones just read."""
    out = np.empty((rows[1] - rows[0], old.shape[1]), dtype=old.dtype)
    if old_rows[1] > old_rows[0] and rows[0] <= old_rows[0] and old_rows[1] <= rows[1]:
        out[old_rows[0] - rows[0]:old_rows[1] - rows[0]] = old
    for (a, b), arr in parts:
        out[a - rows[0]:b - rows[0]] = arr
    return out


async def _gather(jobs: list) -> list:
    return list(await asyncio.gather(*jobs))


class Level2Reader:
    """Cutouts and wavelength maps from Level 2 files. One instance per batch of requests.

    cache_dir: root of the on-disk cache (see cache.CacheLayout), or None to disable caching.
    """

    def __init__(self, client: RangeClient, cache_dir: Path | None = None, irsa: IrsaCutouts | None = None):
        self.c = client
        self.irsa = irsa  # cut on IRSA's side first when given (ADR-0038); S3 range reads otherwise and as fallback
        self.cache = CacheLayout(Path(cache_dir)) if cache_dir is not None else None
        self.var_hdr_len: dict[int, int] = {}
        self.wave_maps: dict[int, WaveMap] = {}
        self._wave_tasks: dict[int, asyncio.Future] = {}
        self.epsf_tables: dict[int, EPSFTable] = {}
        self._epsf_tasks: dict[int, asyncio.Future] = {}
        self.flag_defs: FlagDefs = {}  # bits of every file read so far (a bit's first definition kept)
        self._file_defs: dict[str, FlagDefs] = {}  # per file, from its FLAGS header, until stored in its chunk
        self.cache_hits = 0

    # ---------------------------------------------------------------- cutouts
    async def cutout(self, url: str, ra: float, dec: float, half: int, *, keep_half: int | None = None,
                     variance_at: SkyAt | None = None, variance_radius: int = 0,
                     irsa_url: str | None = None) -> Cutout | None:
        """Square cutout of (2*half+1) native pixels centred on (ra, dec); None if the point is off the frame.

        keep_half: columns kept in the cache on each side of the centre (at least half), so that a later, larger
        window needs only new rows. variance_at: where VARIANCE is needed, from the file's IMAGE header; it is then
        read only for the rows within variance_radius px of that point and is NaN elsewhere. None: everywhere.
        irsa_url: the file on IRSA; with a cutout service client, a window the cache cannot serve from rows it
        holds is first asked from the service (one request), then read from S3 if the service is busy or fails.
        """
        path = self.cache.chunk(url) if self.cache else None
        ch = _Chunk.load(path) if path is not None and path.exists() else None
        dirty = False  # the chunk changed and has to be written
        if ch is None and self.cache is not None:
            ch, off = self._legacy(url, ra, dec, half)
            if off:
                return None
            dirty = ch is not None
        network = False
        wcs = win = None
        if ch is not None:
            wcs = WCS(ch.header)
            win = _window(wcs, ch.header, ra, dec, half)
        # The service first when the cache cannot serve the window and S3 would need its header round anyway.
        if irsa_url and self.irsa is not None and (ch is None or ch.layout is None) \
                and (ch is None or (win is not None and not _holds(ch, win))) and self.irsa.try_slot():
            got = await self._from_irsa(irsa_url, ra, dec, half)
            if got is not None:
                ch, wcs, win = got
                dirty = network = True
        read_header = ch is None
        if read_header:
            ch = _Chunk(*await self._image_header(url))
            dirty = network = True
            wcs = WCS(ch.header)
            win = _window(wcs, ch.header, ra, dec, half)
        if ch.flag_defs:
            self._add_defs(flag_defs_from_text(ch.flag_defs))

        if win is None:
            if dirty and path is not None:
                ch.save(path)  # the header alone answers this question next time
            return None
        x0, x1, y0, y1, xc = win
        nx, ny = int(ch.header["NAXIS1"]), int(ch.header["NAXIS2"])

        vneed = (y0, y1)
        if variance_at is not None:
            rt, dt = variance_at(ch.header)
            yt = float(wcs.all_world2pix(rt, dt, 0, quiet=True)[1])
            k = int(math.floor(yt)) if np.isfinite(yt) else -10 * ny
            vneed = (max(k - variance_radius, y0), min(k + variance_radius + 1, y1))

        if not (ch.cx0 <= x0 and x1 <= ch.cx1):
            kh = max(half, keep_half or 0)
            ch.reset_band(max(xc - kh, 0), min(xc + kh + 1, nx))
        rows, img_miss = _plan(ch.rows, (y0, y1))
        vrows, var_miss = _plan(ch.vrows, vneed) if vneed[1] > vneed[0] else (ch.vrows, [])

        fetched = bool(img_miss or var_miss)
        if fetched:
            img_parts, flag_parts, var_parts = await self._read_rows(url, ch, img_miss, var_miss)
            ch.image = _merge(ch.image, ch.rows, rows, img_parts)
            ch.flags = _merge(ch.flags, ch.rows, rows, flag_parts)
            ch.rows = rows
            if var_miss:
                ch.variance = _merge(ch.variance, ch.vrows, vrows, var_parts)
                ch.vrows = vrows
            if url in self._file_defs:  # read with the FLAGS header just now
                ch.flag_defs = flag_defs_to_text(self._file_defs.pop(url))
                self._add_defs(flag_defs_from_text(ch.flag_defs))
            network = True
        elif not network:
            self.cache_hits += 1
        if (fetched or dirty) and path is not None:
            ch.save(path)

        sx = slice(x0 - ch.cx0, x1 - ch.cx0)
        r0 = ch.rows[0]
        image = np.ascontiguousarray(ch.image[y0 - r0:y1 - r0, sx])
        flags = np.ascontiguousarray(ch.flags[y0 - r0:y1 - r0, sx])
        variance = np.full((y1 - y0, x1 - x0), np.nan, dtype=np.float32)
        a, b = vneed
        if b > a:
            variance[a - y0:b - y0] = ch.variance[a - ch.vrows[0]:b - ch.vrows[0], sx]
        return Cutout(url, ch.header, x0, y0, image, variance, flags, _wcs=wcs if network else None,
                      flag_defs=ch.flag_defs)

    def _add_defs(self, defs: FlagDefs) -> None:
        for bit, d in defs.items():
            self.flag_defs.setdefault(bit, d)

    async def _from_irsa(self, irsa_url: str, ra: float, dec: float, half: int):
        """(chunk, wcs, window) from the IRSA cutout service, or None to read from S3 instead. The window is
        computed from the restored detector-frame header exactly as for S3 and must lie inside what came back."""
        try:
            cut: IrsaCut = await self.irsa.fetch(irsa_url, ra, dec, 2 * (half + IRSA_MARGIN) + 1,
                                                 want_flag_defs=True)
        except IrsaUnavailable as e:
            log.debug("irsa cutout unavailable, reading from S3: %s (%s)", irsa_url.rsplit("/", 1)[-1], e)
            return None
        h, w = cut.image.shape
        ch = _Chunk(cut.header, None, cut.x0, cut.x0 + w, (cut.y0, cut.y0 + h), cut.image, cut.flags,
                    (cut.y0, cut.y0 + h), cut.variance, flag_defs_to_text(cut.flag_defs))
        wcs = WCS(ch.header)
        win = _window(wcs, ch.header, ra, dec, half)
        if win is not None and not _holds(ch, win):
            log.warning("irsa cutout does not cover the window, reading from S3: %s", irsa_url.rsplit("/", 1)[-1])
            self.irsa.failed += 1
            return None
        return ch, wcs, win

    async def _image_header(self, url: str) -> tuple[fits.Header, FileLayout]:
        """Round trip 1 for a new file: the slim IMAGE header (WCS) and where the IMAGE data start."""
        head = await self.c.get(url, 0, HEAD_BYTES)
        prim = parse_header(head)
        if prim is None:
            raise RuntimeError("primary header not found")
        l0 = prim[1]
        img = parse_header(head, l0)
        if img is None:
            head = await self.c.get(url, 0, 4 * HEAD_BYTES)
            img = parse_header(head, l0)
            if img is None:
                raise RuntimeError("IMAGE header not found")
        ih, l1 = img
        return slim_header(ih), FileLayout(int(ih["NAXIS1"]), int(ih["NAXIS2"]), l0 + l1)

    async def _read_rows(self, url: str, ch: _Chunk, img_iv: list[Rows], var_iv: list[Rows]):
        """Read IMAGE + FLAGS rows img_iv and VARIANCE rows var_iv over the chunk's column band. With a known layout
        this is one round trip; otherwise the FLAGS header and the VARIANCE header are learnt on the way (rounds 2
        and 3 of a new file, the VARIANCE header length predicted per detector and verified)."""
        c = self.c
        if ch.layout is None:  # an entry from the old cache: only the IMAGE header offsets are missing
            _, ch.layout = await self._image_header(url)
        lay = ch.layout
        row = lay.nx * 4
        img_jobs = [c.get(url, lay.img_off + a * row, (b - a) * row) for a, b in img_iv]
        img_raw = None
        if lay.flags is None:
            img_raw, _ = await asyncio.gather(_gather(img_jobs), self._read_flags_layout(url, lay))
            img_jobs = []
        flag_jobs = [c.get(url, *lay.flags.span(a, b)) for a, b in img_iv]
        if lay.var_off is None:
            det = int(ch.header.get("DETECTOR", 0))
            vlen = self.var_hdr_len.get(det, VARIANCE_HDR_GUESS)
            guess = lay.var_hdr + vlen
            var_jobs = [c.get(url, guess + a * row, (b - a) * row) for a, b in var_iv]
            more_img, flag_raw, vhead, var_raw = await asyncio.gather(
                _gather(img_jobs), _gather(flag_jobs), c.get(url, lay.var_hdr, vlen), _gather(var_jobs))
            vp = parse_header(vhead)
            if vp is None or vp[1] != vlen:  # header length differs from the prediction: read it properly
                vhead = await c.get(url, lay.var_hdr, HEAD_BYTES)
                vp = parse_header(vhead)
                if vp is None:
                    raise RuntimeError("VARIANCE header not found")
                vlen = vp[1]
                var_raw = await _gather([c.get(url, lay.var_hdr + vlen + a * row, (b - a) * row) for a, b in var_iv])
            self.var_hdr_len[det] = vlen
            lay.var_off = lay.var_hdr + vlen
        else:
            more_img, flag_raw, var_raw = await asyncio.gather(
                _gather(img_jobs), _gather(flag_jobs),
                _gather([c.get(url, lay.var_off + a * row, (b - a) * row) for a, b in var_iv]))
        img_raw = img_raw if img_raw is not None else more_img

        def floats(raw: bytes, a: int, b: int) -> np.ndarray:
            return np.frombuffer(raw, dtype=">f4").reshape(b - a, lay.nx)[:, ch.cx0:ch.cx1].astype(np.float32)

        return ([(r, floats(raw, *r)) for r, raw in zip(img_iv, img_raw)],
                [(r, lay.flags.decode(raw, *r, ch.cx0, ch.cx1)) for r, raw in zip(img_iv, flag_raw)],
                [(r, floats(raw, *r)) for r, raw in zip(var_iv, var_raw)])

    async def _read_flags_layout(self, url: str, lay: FileLayout) -> None:
        """Learn where FLAGS rows are (plain, or the row-tile index of RICE/GZIP compression) and where the
        VARIANCE header starts."""
        flags_hdr = lay.img_off + padded(lay.nx * lay.ny * 4)
        fhead = await self.c.get(url, flags_hdr, HEAD_BYTES)
        fp = parse_header(fhead)
        if fp is None:
            fhead = await self.c.get(url, flags_hdr, 4 * HEAD_BYTES)
            fp = parse_header(fhead)
            if fp is None:
                raise RuntimeError("FLAGS header not found")
        fh, fl = fp
        # Files of different pipeline versions define different bits (e.g. 29 SATELLITE_HALO only in newer ones),
        # so every file's own definitions are kept with its chunk.
        self._file_defs[url] = read_flag_defs(fh)
        if not fh.get("ZIMAGE"):  # plain image extension
            bitpix = int(fh["BITPIX"])
            lay.flags = FlagsLayout(flags_hdr + fl, row_bytes=int(fh["NAXIS1"]) * abs(bitpix) // 8,
                                    dtype=">i4" if bitpix == 32 else ">i2")
            lay.var_hdr = flags_hdr + fl + padded(data_nbytes(fh))
            return
        ntiles, rowlen = int(fh["NAXIS2"]), int(fh["NAXIS1"])
        if fh.get("TTYPE1") != "COMPRESSED_DATA" or int(fh.get("ZTILE2", 1)) != 1 \
                or int(fh.get("ZTILE1", fh["ZNAXIS1"])) != int(fh["ZNAXIS1"]):
            raise NotImplementedError("only row-tiled COMPRESSED_DATA is supported")
        need = fl + rowlen * ntiles
        if len(fhead) < need:
            fhead = fhead + await self.c.get(url, flags_hdr + len(fhead), need - len(fhead))
        wide = fh["TFORM1"].lstrip("0123456789").startswith("Q")
        width, dt = (16, ">i8") if wide else (8, ">i4")
        table = np.frombuffer(fhead[fl:need], dtype=np.uint8).reshape(ntiles, rowlen)[:, :width]
        desc = np.frombuffer(table.tobytes(), dtype=dt).reshape(ntiles, 2).astype(np.int64)  # (nbytes, offset)
        params = {fh[f"ZNAME{i}"]: fh[f"ZVAL{i}"] for i in range(1, 10) if f"ZNAME{i}" in fh}
        lay.flags = FlagsLayout(flags_hdr + fl + int(fh.get("THEAP", rowlen * ntiles)), cmp=fh["ZCMPTYPE"].strip(),
                                tilesize=int(fh["ZNAXIS1"]),
                                bytepix=int(params.get("BYTEPIX", abs(int(fh.get("ZBITPIX", 32))) // 8)),
                                blocksize=int(params.get("BLOCKSIZE", 32)), desc=desc)
        lay.var_hdr = flags_hdr + fl + padded(rowlen * ntiles + int(fh["PCOUNT"]))

    def _legacy(self, url: str, ra: float, dec: float, half: int) -> tuple[_Chunk | None, bool]:
        """The same request in the old per-request cache, as a chunk: (chunk or None, off the frame)."""
        path = self.cache.cutout(url, ra, dec, half)
        if not path.exists():
            return None, False
        try:
            with np.load(path, allow_pickle=False) as z:
                x0 = int(z["x0"])
                if x0 < 0:
                    return None, True
                y0 = int(z["y0"])
                image = z["image"].astype(np.float32)
                h, w = image.shape
                return _Chunk(slim_header(fits.Header.fromstring(str(z["header"]))), None, x0, x0 + w, (y0, y0 + h),
                              image, z["flags"].astype(np.int32), (y0, y0 + h), z["variance"].astype(np.float32),
                              str(z["flag_defs"]) if "flag_defs" in z else ""), False
        except Exception:  # noqa: BLE001 - corrupted entry: read the file again
            return None, False

    # ---------------------------------------------------------------- wavelength
    async def wave_map(self, url: str, detector: int) -> WaveMap:
        if detector in self.wave_maps:
            return self.wave_maps[detector]
        if detector not in self._wave_tasks:
            self._wave_tasks[detector] = asyncio.ensure_future(self._load_wave_map(url, detector))
        wm = await self._wave_tasks[detector]
        self.wave_maps[detector] = wm
        return wm

    async def _load_wave_map(self, url: str, detector: int) -> WaveMap:
        path = self.cache.wave(detector) if self.cache else None
        if path is not None and path.exists():
            with np.load(path) as z:
                return WaveMap(z["x"], z["y"], z["values"])
        with await self._read_table_hdu(url, "WCS-WAVE") as hdul:
            tab = hdul[1].data
            wm = WaveMap(np.asarray(tab["X"][0]), np.asarray(tab["Y"][0]), np.asarray(tab["VALUES"][0]))
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            np.savez(path, x=wm.x, y=wm.y, values=wm.values)
        return wm

    # ---------------------------------------------------------------- effective PSF
    async def epsf_table(self, url: str, detector: int) -> EPSFTable:
        if detector in self.epsf_tables:
            return self.epsf_tables[detector]
        if detector not in self._epsf_tasks:
            self._epsf_tasks[detector] = asyncio.ensure_future(self._load_epsf(url, detector))
        table = await self._epsf_tasks[detector]
        self.epsf_tables[detector] = table
        return table

    async def _load_epsf(self, url: str, detector: int) -> EPSFTable:
        path = self.cache.epsf(detector) if self.cache else None
        if path is not None and path.exists():
            with np.load(path) as z:
                return EPSFTable(z["xcenter"], z["ycenter"], z["psf"], int(z["oversampling"]), z["cwave"], z["neff"])
        with await self._read_table_hdu(url, "EPSF") as hdul:
            hdu = hdul[1]
            tab = hdu.data
            ovs_x, ovs_y = int(hdu.header.get("OVSMPX", 1)), int(hdu.header.get("OVSMPY", 1))
            if ovs_x != ovs_y:
                raise NotImplementedError("anisotropic ePSF oversampling")
            table = EPSFTable(np.asarray(tab["XCENTER"], float), np.asarray(tab["YCENTER"], float),
                              np.asarray(tab["EPSF"], np.float32), ovs_x, np.asarray(tab["CWAVE"], float),
                              np.asarray(tab["NEFF_MEAN"], float))
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp.npz")
            np.savez(tmp, xcenter=table.xcenter, ycenter=table.ycenter, psf=table.psf,
                     oversampling=table.oversampling, cwave=table.cwave, neff=table.neff, source=url)
            tmp.replace(path)
        return table

    async def _read_table_hdu(self, url: str, extname: str) -> fits.HDUList:
        """Walk the HDU headers until EXTNAME == extname and return that HDU (header + data) as an HDUList
        whose element 1 is the extension. Only headers and the one data unit are transferred."""
        off = 0
        for _ in range(16):
            buf = await self.c.get(url, off, HEAD_BYTES)
            p = parse_header(buf)
            if p is None:
                buf = await self.c.get(url, off, 4 * HEAD_BYTES)
                p = parse_header(buf)
                if p is None:
                    break
            h, hl = p
            nbytes = data_nbytes(h)
            if str(h.get("EXTNAME", "")).strip() == extname:
                data = buf[hl:hl + nbytes] if len(buf) >= hl + nbytes else await self.c.get(url, off + hl, nbytes)
                blob = (fits.PrimaryHDU().header.tostring().encode() + buf[:hl] + data
                        + b"\0" * (padded(nbytes) - nbytes))
                return fits.HDUList.fromstring(blob)
            off += hl + padded(nbytes)
        raise RuntimeError(f"{extname} extension not found")
