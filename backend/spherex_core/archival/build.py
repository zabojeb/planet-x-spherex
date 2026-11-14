"""Build archival epochs for a sequence: older-survey images resampled onto the sequence's own grid.

2MASS (~1997-2001) and AllWISE (~2010.5) add one epoch per band; NEOWISE-R (2014-2024) adds one co-add
per visit (~6 months apart, ~12-20 single exposures each, median-combined to reject cosmic rays and
asteroids). Every image is converted to MJy/sr, background-subtracted, smoothed to the SPHEREx pixel scale
(Gaussian, sigma = 0.35 x the pixel-scale ratio, NaN-aware) and bilinearly resampled onto the grid, so the
web client can blink it next to the SPHEREx co-adds.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
import warnings
from collections import defaultdict
from typing import Callable

import numpy as np
from astropy.wcs import WCS
from astropy.wcs.utils import proj_plane_pixel_area
from scipy.ndimage import gaussian_filter

from ..config import settings
from ..errors import NotFound
from ..io import RangeClient
from ..processing import Grid, background, make_grid, pixel_map, resample
from ..targets import Target
from ..timeutil import mjd_to_iso, mjd_to_year, utc_now_iso
from . import store
from .reader import Window, fetch_file, read_window, window_from_file
from .sources import ArchivalImage, find_images
from .surveys import BANDS, SURVEYS, SurveyBand, dn_to_mjy_sr, wise_s3_url

log = logging.getLogger(__name__)
ARCHIVAL_VERSION = 2  # 2: AllWISE tiles mosaicked, 2MASS nights within 3 days merged
SMOOTH_FACTOR = 0.35  # Gaussian sigma in units of (grid pixel / native pixel)
VISIT_GAP_DAYS = 20.0  # NEOWISE: a new visit after a gap this long...
VISIT_MAX_DAYS = 30.0  # ...or once a visit spans this long (polar fields are observed continuously)
MIN_VISIT_FRAMES = 3
MAX_VISIT_FRAMES = 40  # polar fields have ~900 NEOWISE frames per band; more per visit adds little depth
TWOMASS_GROUP_DAYS = 3.0  # 2MASS images closer than this in time are mosaicked into one epoch
Progress = Callable[[str, int, int], None]
_BAND = {(b.survey, b.band): b for b in BANDS}


def to_grid(data: np.ndarray, header_text: str, x0: int, y0: int, grid: Grid, native_pix: float,
            f0_jy: float, default_magzp: float) -> np.ndarray:
    """DN window -> MJy/sr, background-subtracted, smoothed and resampled onto the grid (pure function)."""
    from astropy.io import fits

    h = fits.Header.fromstring(header_text)
    wcs = WCS(h)
    area_sr = proj_plane_pixel_area(wcs) * (math.pi / 180.0) ** 2
    img = data.astype(np.float64) * dn_to_mjy_sr(float(h.get("MAGZP", default_magzp)), f0_jy, area_sr)
    img[~np.isfinite(img)] = np.nan
    bkg, _ = background(img.astype(np.float32))
    img = img - bkg
    sigma = SMOOTH_FACTOR * grid.pixscale / native_pix
    valid = np.isfinite(img)
    num = gaussian_filter(np.where(valid, img, 0.0), sigma, mode="constant")
    den = gaussian_filter(valid.astype(np.float64), sigma, mode="constant")
    smooth = np.where(den > 0.3, num / np.maximum(den, 1e-12), np.nan)
    xs, ys = pixel_map(wcs, x0, y0, grid)
    return resample(smooth, xs, ys)


def visits(times: list[float], gap: float = VISIT_GAP_DAYS, max_len: float = VISIT_MAX_DAYS) -> list[int]:
    """Visit index per time (input order), new visit after a gap > gap or once a visit spans max_len."""
    order = np.argsort(times)
    out = [0] * len(times)
    cur, start, prev = 0, None, None
    for i in order:
        t = times[i]
        if prev is not None and (t - prev > gap or t - start > max_len):
            cur += 1
            start = t
        if start is None:
            start = t
        out[i] = cur
        prev = t
    return out


def limit_per_visit(ims: list[ArchivalImage], max_frames: int = MAX_VISIT_FRAMES) -> list[ArchivalImage]:
    """At most max_frames images per NEOWISE visit, evenly spread in time (bounds reads for polar fields)."""
    usable = [im for im in ims if im.mjd is not None]
    idx = visits([im.mjd for im in usable])
    groups: dict[int, list[ArchivalImage]] = defaultdict(list)
    for im, v in zip(usable, idx):
        groups[v].append(im)
    out: list[ArchivalImage] = []
    for v in sorted(groups):
        g = sorted(groups[v], key=lambda im: im.mjd)
        if len(g) > max_frames:
            g = [g[int(round(i))] for i in np.linspace(0, len(g) - 1, max_frames)]
        out += g
    return out


def _grid_for(seq_meta: dict, fov_arcmin: float | None) -> Grid:
    g = seq_meta["grid"]
    grid = Grid(float(g["ra"]), float(g["dec"]), int(g["n"]), float(g["pixscale_arcsec"]))
    if fov_arcmin:
        wider = make_grid(grid.ra, grid.dec, float(fov_arcmin), grid.pixscale)
        if wider.n > grid.n:
            return wider
    return grid


def grid_size(seq_meta: dict, fov_arcmin: float | None) -> int:
    return _grid_for(seq_meta, fov_arcmin).n


def _target(seq_meta: dict) -> Target:
    t = seq_meta["target"]
    if t.get("ephemeris"):
        raise NotFound("Archival epochs are for fixed or proper-motion targets, not solar-system objects.")
    return Target(ra=float(t["ra"]), dec=float(t["dec"]), name=t.get("name"), pmra=float(t.get("pmra") or 0.0),
                  pmdec=float(t.get("pmdec") or 0.0), ref_epoch=float(t.get("ref_epoch") or 2000.0))


async def build_archival(seq_meta: dict, out_dir, fov_arcmin: float | None = None,
                         surveys: tuple[str, ...] = SURVEYS, progress: Progress | None = None) -> dict:
    """Build and write archival epochs for a built sequence (its meta.json dict). Returns the metadata."""
    report = progress or (lambda stage, done, total: None)
    t0 = time.perf_counter()
    grid = _grid_for(seq_meta, fov_arcmin)
    target = _target(seq_meta)
    surveys = tuple(s for s in surveys if s in SURVEYS) or SURVEYS
    radius = grid.n * grid.pixscale / 3600.0 * 0.5 * math.sqrt(2.0)

    report("search", 0, 0)
    images = await asyncio.to_thread(find_images, grid.ra, grid.dec, radius, surveys)
    by_band: dict[tuple[str, str], list[ArchivalImage]] = defaultdict(list)
    for im in images:
        by_band[(im.survey, im.band)].append(im)
    for key in [k for k in by_band if k[0] == "neowise"]:
        by_band[key] = limit_per_visit(by_band[key])

    errors: list[str] = []
    planned = sum(len(v) for v in by_band.values())
    done = 0
    report("fetch", 0, planned)

    def half_for(band: SurveyBand) -> int:
        smooth_margin = int(math.ceil(3 * SMOOTH_FACTOR * grid.pixscale / band.pixscale)) + 2
        return int(math.ceil(grid.n * grid.pixscale / band.pixscale * 0.5 * 1.42)) + smooth_margin

    async def regrid(win: Window, band: SurveyBand) -> np.ndarray:
        # Threads, not the shared process pool: the work is ndimage filtering and resampling, and a crashed
        # pool worker (e.g. thread exhaustion) would break sequence builds that share the pool.
        return await asyncio.to_thread(to_grid, win.data, win.header.tostring(), win.x0, win.y0, grid,
                                       band.pixscale, band.f0_jy, band.default_magzp)

    frames: list[dict] = []
    stack: list[np.ndarray] = []
    async with RangeClient(min(settings.max_connections, 64), timeout=180.0) as client:
        sem = asyncio.Semaphore(32)

        async def one_image(im: ArchivalImage, band: SurveyBand) -> np.ndarray | None:
            nonlocal done
            async with sem:
                try:
                    if band.survey == "2mass":
                        blob = await fetch_file(client, im.url)
                        win = await asyncio.to_thread(window_from_file, blob, grid.ra, grid.dec, half_for(band))
                    else:
                        url = wise_s3_url(im.url) or im.url
                        win = await read_window(client, url, grid.ra, grid.dec, half_for(band))
                    return None if win is None else await regrid(win, band)
                except Exception as e:  # noqa: BLE001 - one bad file must not stop the rest
                    errors.append(f"{im.url.rsplit('/', 1)[-1]}: {e!r}"[:300])
                    log.warning("archival image failed: %s %r", im.url, e)
                    return None
                finally:
                    done += 1
                    report("fetch", done, planned)

        tasks = []
        for (survey, bname), ims in sorted(by_band.items()):
            band = _BAND[(survey, bname)]
            if survey == "allwise":  # one epoch: every Atlas tile touching the field, mosaicked
                tasks.append(_grouped(band, ims, one_image, 1e9, 1e9, "mean", 1))
            elif survey == "2mass":
                tasks.append(_grouped(band, ims, one_image, TWOMASS_GROUP_DAYS, TWOMASS_GROUP_DAYS, "mean", 1))
            else:
                tasks.append(_grouped(band, ims, one_image, VISIT_GAP_DAYS, VISIT_MAX_DAYS, "median",
                                      MIN_VISIT_FRAMES))
        for group in await asyncio.gather(*tasks):
            for f, img in group:
                frames.append(f)
                stack.append(img)
        net = {"requests": client.requests, "bytes": client.bytes_read}

    order = sorted(range(len(frames)), key=lambda i: (frames[i]["mjd"] or 0.0, frames[i]["survey"], frames[i]["band"]))
    frames = [frames[i] for i in order]
    stack = [stack[i] for i in order]
    for i, f in enumerate(frames):
        f["index"] = i
        ra_t, dec_t = target.position_at(f["mjd"])
        x, y = grid.world_to_pixel(ra_t, dec_t)
        f["target_radec"] = [float(ra_t), float(dec_t)]
        f["target_px"] = [float(x), float(y)]
        f["target_in_field"] = bool(0 <= x <= grid.n - 1 and 0 <= y <= grid.n - 1)
    arr = np.stack(stack).astype(np.float32) if stack else np.zeros((0, grid.n, grid.n), np.float32)
    meta = {
        "sequence": seq_meta["id"], "version": ARCHIVAL_VERSION, "created": utc_now_iso(),
        "grid": grid.to_dict(), "surveys": list(surveys),
        "frames": frames,
        "array": {"file": store.FRAMES, "dtype": "float32", "shape": list(arr.shape),
                  "unit": "MJy/sr (background subtracted)"},
        "processing": {"smoothing_sigma_grid_px": SMOOTH_FACTOR, "visit_gap_days": VISIT_GAP_DAYS,
                       "visit_max_days": VISIT_MAX_DAYS, "min_visit_frames": MIN_VISIT_FRAMES,
                       "max_visit_frames": MAX_VISIT_FRAMES,
                       "calibration": "DN * F0 * 10**(-0.4 MAGZP) / pixel solid angle (Vega zero points)"},
        "stats": {"images_found": len(images), "errors": errors[:20], **net,
                  "seconds": time.perf_counter() - t0},
        "sources": {"archive": "NASA/IPAC IRSA (CAOM TAP, IBE)", "wise_s3": "s3://nasa-irsa-wise (us-west-2)"},
    }
    await asyncio.to_thread(store.write, out_dir, meta, arr)
    return meta


def _frame(band: SurveyBand, ims: list[ArchivalImage], used: int) -> dict:
    t = [im.mjd for im in ims if im.mjd is not None]
    lo = [im.mjd_start for im in ims if im.mjd_start is not None]
    hi = [im.mjd_end if im.mjd_end is not None else im.mjd_start for im in ims if im.mjd_start is not None]
    mjd = float(np.mean(t)) if t else None
    return {"survey": band.label, "survey_id": band.survey, "band": band.band, "label": f"{band.label} {band.band}",
            "wl_um": band.wl_um, "mjd": mjd, "mjd_min": min(lo) if lo else None, "mjd_max": max(hi) if hi else None,
            "date": mjd_to_iso(mjd) if mjd is not None else None,
            "epoch_year": float(mjd_to_year(mjd)) if mjd is not None else None,
            "native_pixscale_arcsec": band.pixscale, "n_images": used, "unit": "MJy/sr",
            "sources": [im.url for im in ims][:50]}


async def _grouped(band, ims, one_image, gap, max_len, how, min_frames) -> list[tuple[dict, np.ndarray]]:
    """Images grouped in time (2MASS nights, NEOWISE visits), each group combined into one epoch."""
    usable = [im for im in ims if im.mjd is not None]
    if not usable:
        return []
    idx = visits([im.mjd for im in usable], gap, max_len)
    imgs = await asyncio.gather(*(one_image(im, band) for im in usable))
    groups: dict[int, list[tuple[ArchivalImage, np.ndarray]]] = defaultdict(list)
    for im, v, img in zip(usable, idx, imgs):
        if img is not None and np.isfinite(img).any():
            groups[v].append((im, img))
    out = []
    for v in sorted(groups):
        members = groups[v]
        if len(members) < min_frames:
            continue
        cube = np.stack([img for _, img in members])
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN pixels outside the coverage
            comb = np.nanmedian(cube, axis=0) if how == "median" else np.nanmean(cube, axis=0)
        f = _frame(band, [im for im, _ in members], len(members))
        f["coverage"] = float(np.isfinite(comb).mean())
        out.append((f, comb.astype(np.float32)))
    return out


def archival_dir(seq_id: str, seq_meta: dict, fov_arcmin: float | None):
    return store.result_dir(seq_id, grid_size(seq_meta, fov_arcmin))


__all__ = ["ARCHIVAL_VERSION", "archival_dir", "build_archival", "grid_size", "limit_per_visit", "to_grid",
           "visits"]
