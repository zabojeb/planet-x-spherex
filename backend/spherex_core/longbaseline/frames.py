"""Native-pixel windows of archival frames around a field, read once and cached on disk.

NEOWISE-R L1b frames and AllWISE Atlas tiles are uncompressed in the public S3 bucket, so only the rows that
cover the window are read (archival.reader); NEOWISE masks (msk-1b, ~150 kB gzip) are downloaded whole and cut
to the same window. 2MASS images are small gzip files read whole. Every window is stored as

    <cache>/longbaseline/windows/<k[:2]>/<sha1(url|ra|dec|half)>.npz   data (DN), mask, slim header, x0, y0, mjd

so measuring another position in the same field (a click, the field-wide search, calibration stars) costs no
download. Windows are centred on the sequence grid centre; their half-size covers every position the job may
measure plus the photometry margin of the instrument.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS

from .. import cache
from ..archival.reader import fetch_file, read_header, read_window, window_from_file
from ..archival.sources import ArchivalImage
from ..archival.surveys import wise_s3_url
from ..config import settings
from ..field.common import FieldCancelled
from ..io import RangeClient
from .instruments import Instrument, instrument

log = logging.getLogger(__name__)
WINDOW_VERSION = 1
MAX_IN_FLIGHT = 32


@dataclass
class FrameWindow:
    survey: str
    band: str
    url: str
    mjd: float
    header: str  # slim FITS header text (WCS + MAGZP)
    x0: int
    y0: int
    data: np.ndarray  # float32 DN, NaN where the survey left no value
    mask: np.ndarray | None  # int32 bits (NEOWISE) or None

    @property
    def inst(self) -> Instrument:
        return instrument(self.survey, self.band)

    def wcs(self) -> WCS:
        return WCS(fits.Header.fromstring(self.header))

    @property
    def magzp(self) -> float:
        h = fits.Header.fromstring(self.header)
        return float(h.get("MAGZP", _DEFAULT_MAGZP[(self.survey, self.band)]))


_DEFAULT_MAGZP = {("neowise", "W1"): 20.752, ("neowise", "W2"): 19.596, ("allwise", "W1"): 20.5,
                  ("allwise", "W2"): 19.5, ("2mass", "J"): 20.9, ("2mass", "H"): 20.5, ("2mass", "Ks"): 20.0}


def window_path(url: str, ra: float, dec: float, half: int) -> Path:
    key = hashlib.sha1(f"v{WINDOW_VERSION}|{url}|{ra:.6f}|{dec:.6f}|{half}".encode()).hexdigest()
    return cache.layout().root / "longbaseline" / "windows" / key[:2] / f"{key}.npz"


def half_size(inst: Instrument, radius_arcsec: float) -> int:
    """Window half-size in native pixels for positions up to radius_arcsec from the centre."""
    return int(math.ceil(radius_arcsec / inst.pixscale)) + inst.window_margin_px


def _save(path: Path, w: FrameWindow | None, im: ArchivalImage) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp.npz")
    if w is None:  # the frame does not cover the centre: remember it, so it is not read again
        np.savez(tmp, x0=np.int32(-1), mjd=np.float64(im.mjd or 0.0))
    else:
        mask = w.mask if w.mask is not None else np.zeros((0, 0), np.int32)
        np.savez(tmp, data=w.data.astype(np.float32), mask=mask,
                 header=np.str_(w.header), x0=np.int32(w.x0), y0=np.int32(w.y0), mjd=np.float64(w.mjd))
    tmp.replace(path)


def _load(path: Path, im: ArchivalImage) -> FrameWindow | None | bool:
    """The cached window, None for a cached 'not covered', False when not cached (or unreadable)."""
    if not path.exists():
        return False
    try:
        with np.load(path, allow_pickle=False) as z:
            if int(z["x0"]) < 0:
                return None
            mask = z["mask"]
            return FrameWindow(im.survey, im.band, im.url, float(z["mjd"]), str(z["header"]), int(z["x0"]),
                               int(z["y0"]), z["data"], mask if mask.size else None)
    except Exception:  # noqa: BLE001 - damaged entry: read again
        return False


def _mask_url(url: str) -> str:
    return url.replace("-int-1b.fits", "-msk-1b.fits.gz")


def _cut_mask(blob: bytes, x0: int, y0: int, shape: tuple[int, int]) -> np.ndarray:
    with fits.open(io.BytesIO(blob)) as hdul:
        m = np.asarray(hdul[0].data)
    return m[y0:y0 + shape[0], x0:x0 + shape[1]].astype(np.int32)


def _mjd_of(header: fits.Header, im: ArchivalImage) -> float:
    for k in ("MJD_OBS", "MJD-OBS"):
        if k in header:
            try:
                return float(header[k])
            except (TypeError, ValueError):
                pass
    return float(im.mjd or 0.0)


async def _read_one(client: RangeClient, im: ArchivalImage, ra: float, dec: float, half: int
                    ) -> FrameWindow | None:
    if im.survey == "2mass":
        blob = await fetch_file(client, im.url)
        win = await asyncio.to_thread(window_from_file, blob, ra, dec, half)
        if win is None:
            return None
        return FrameWindow(im.survey, im.band, im.url, float(im.mjd or 0.0), win.header.tostring(), win.x0, win.y0,
                           win.data, None)
    url = wise_s3_url(im.url) or im.url
    header = await read_header(client, url)
    win = await read_window(client, url, ra, dec, half, header)
    if win is None:
        return None
    mask = None
    if im.survey == "neowise":
        blob = await fetch_file(client, _mask_url(url))
        mask = await asyncio.to_thread(_cut_mask, blob, win.x0, win.y0, win.data.shape)
    return FrameWindow(im.survey, im.band, im.url, _mjd_of(header[0], im), win.header.tostring(), win.x0, win.y0,
                       win.data, mask)


async def read_windows(images: list[ArchivalImage], ra: float, dec: float, radius_arcsec: float,
                       progress: Callable[[int, int], None] | None = None,
                       check: Callable[[], None] | None = None) -> tuple[list[FrameWindow], dict]:
    """Windows of every image around (ra, dec) covering radius_arcsec (+ photometry margin), from the disk cache
    or the archive. Frames that do not cover the centre are skipped. Returns (windows, stats)."""
    report = progress or (lambda done, total: None)
    out: list[FrameWindow | None] = [None] * len(images)
    stats = {"frames": len(images), "cached": 0, "read": 0, "not_covered": 0, "errors": []}
    todo: list[int] = []
    for i, im in enumerate(images):
        half = half_size(instrument(im.survey, im.band), radius_arcsec)
        hit = await asyncio.to_thread(_load, window_path(im.url, ra, dec, half), im)
        if hit is False:
            todo.append(i)
        else:
            stats["cached"] += 1
            out[i] = hit
            stats["not_covered"] += hit is None
    done = len(images) - len(todo)
    report(done, len(images))
    if todo:
        async with RangeClient(min(settings.max_connections, 64), timeout=180.0) as client:
            sem = asyncio.Semaphore(MAX_IN_FLIGHT)

            async def one(i: int) -> None:
                nonlocal done
                im = images[i]
                half = half_size(instrument(im.survey, im.band), radius_arcsec)
                async with sem:
                    if check is not None:
                        check()
                    try:
                        w = await _read_one(client, im, ra, dec, half)
                        await asyncio.to_thread(_save, window_path(im.url, ra, dec, half), w, im)
                        out[i] = w
                        stats["read"] += 1
                        stats["not_covered"] += w is None
                    except FieldCancelled:
                        raise
                    except Exception as e:  # noqa: BLE001 - one bad file must not stop the rest
                        stats["errors"].append(f"{im.url.rsplit('/', 1)[-1]}: {e!r}"[:240])
                        log.warning("long-baseline window failed: %s %r", im.url, e)
                    finally:
                        done += 1
                        report(done, len(images))

            await asyncio.gather(*(one(i) for i in todo))
            stats["requests"], stats["bytes"] = client.requests, client.bytes_read
    stats["errors"] = stats["errors"][:20]
    return [w for w in out if w is not None], stats
