"""Static-sky subtraction for co-moving sequences: "different background, same comet" (ADR-0057).

In a co-moving sequence every exposure i sits on its own grid G_i centred on the object at time t_i, so the stars
trail and the object stays put. For each exposure this module builds a template of the same sky without the
object, subtracts it and co-adds the differences in the object frame:

1. Candidates: Level 2 images of the same detector that cover the centre of G_i (skyframes.frames_covering: TAP,
   or the MOST fixed-point search when TAP is down), taken when the object was elsewhere (more than the grid's
   half-diagonal plus a coma margin away, from the ephemeris: interpolated inside a visit, extrapolated along the
   nearest visit's motion up to EXTRAPOLATE_DAYS with a 10 % allowance for curvature; images taken later than
   that are assumed clear of it).
2. Wavelength: the LVF filter puts a different wavelength on every detector row, so each candidate sees G_i at
   its own wavelength. It is predicted at the grid centre from the footprint corners (or the header WCS) and the
   detector's wavelength map, then measured again from the template's own WCS. Only candidates within
   LAMBDA_TOL_FAR resolution elements (the exposure's dlambda) are read; those within LAMBDA_TOL_NEAR are
   preferred.
3. Template: each candidate is masked (MASK_DEFAULT), background-subtracted and resampled on G_i exactly like the
   science exposure (pipeline.frame). Two or more near candidates: per-pixel median. Otherwise the nearest
   candidates below and above the exposure's wavelength are interpolated linearly per pixel. A single candidate
   is used alone (flagged 'single'/'nearest').
4. Clean exposure: aligned exposure minus template, minus the sigma-clipped median of the outer part of the grid
   (zodiacal light differs between the two times, and the science background estimate includes some coma).
   Stars leave a residual fraction eps of their flux (PSF and wavelength mismatch), measured per exposure on bright
   template pixels away from the object; pixels where eps x template would exceed RESID_SIGMA x the exposure noise
   are masked, dilated by one pixel (a 3-sigma cut on the template alone blanked ~20 % of a Milky Way field and the
   nucleus whenever a star passed under it).
5. Clean stack: the clean exposures of each epoch and detector are co-added like the normal co-adds
   (processing.coadd: weighted, clipped mean). Star masks fall on different object-frame pixels in every
   exposure, so the stack is filled where the single exposures are masked.

Outputs (pipeline.results): clean [epochs, 6, n, n] float32 and clean_coverage uint16, plus clean_exposures
[frames, n, n] float16 when the single exposures are stored; meta.static_sky and per-exposure ss_* fields.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
import warnings

import numpy as np
from astropy.stats import sigma_clipped_stats
from scipy.ndimage import binary_dilation

from .. import cache, logs
from ..bands import BANDS
from ..config import settings
from ..flags import MASK_DEFAULT
from ..io import Cutout, Level2Reader, RangeClient, WaveMap
from ..processing import Grid, background, coadd, pixel_map, resample
from ..targets import Target
from .skyframes import SkyFrame, footprint_pixel, frames_covering

log = logging.getLogger(__name__)

STATIC_SKY_VERSION = 1  # bump when the clean images change for the same inputs
LAMBDA_TOL_NEAR = 0.5  # resolution elements: candidates this close are median-combined
LAMBDA_TOL_FAR = 1.5  # resolution elements: farthest candidate read (interpolation, single fallback)
MAX_TEMPLATES = 4  # candidates read per exposure
# Star residuals: the subtraction leaves a fraction eps of every star (PSF and wavelength mismatch), measured per
# exposure on template pixels brighter than EPS_MIN_SIGMA x noise away from the object. Pixels whose expected
# residual eps x template exceeds RESID_SIGMA x noise (and at least STAR_SIGMA x noise) are masked.
STAR_SIGMA = 3.0
RESID_SIGMA = 3.0
EPS_MIN_SIGMA = 20.0
EPS_DEFAULT = 0.08  # when an exposure has too few bright star pixels to measure eps
OUTER_FRAC = 0.6  # the offset is measured beyond this fraction of the grid half-width from the object
CLUSTER_DEG = 0.5  # exposures within this of a cluster centre share one footprint search
COMA_MARGIN_ARCMIN = {"Comet": 2.0, "Asteroid": 0.5}
VISIT_GAP_DAYS = 1.0  # ephemeris samples closer than this belong to one visit (interpolation is valid)
EXTRAPOLATE_DAYS = 30.0  # beyond this from every ephemeris sample a candidate is assumed clear of the object
CURVATURE_ALLOWANCE = 0.1  # extra exclusion radius, as a fraction of the extrapolated motion
METHODS = ("median", "interp", "single", "nearest", "none")


# ---------------------------------------------------------------- pure helpers (unit-tested)
def combine_templates(temps: list[tuple[float, np.ndarray]], near: float = LAMBDA_TOL_NEAR,
                      far: float = LAMBDA_TOL_FAR) -> tuple[np.ndarray | None, str, list[float]]:
    """One template from aligned candidates [(dl, image)], dl = (lambda_j - lambda_i) / dlambda_i.

    Returns (template or None, method, the dl values used). See the module docstring for the rules."""
    temps = [(float(d), a) for d, a in temps if abs(d) <= far]
    if not temps:
        return None, "none", []
    temps.sort(key=lambda t: abs(t[0]))
    close = [t for t in temps if abs(t[0]) <= near]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)  # all-NaN pixels
        if len(close) >= 2:
            return np.nanmedian(np.stack([a for _, a in close]), axis=0).astype(np.float32), "median", \
                [d for d, _ in close]
        below = [t for t in temps if t[0] < 0]
        above = [t for t in temps if t[0] >= 0]
        if below and above:
            (d1, a1), (d2, a2) = below[0], above[0]
            w = (0.0 - d1) / (d2 - d1) if d2 != d1 else 0.5
            out = (1 - w) * a1 + w * a2
            out = np.where(np.isfinite(out), out, np.where(np.isfinite(a1), a1, a2))
            return out.astype(np.float32), "interp", [d1, d2]
    d, a = temps[0]
    return a.astype(np.float32), ("single" if abs(d) <= near else "nearest"), [d]


def outer_mask(n: int, frac: float = OUTER_FRAC) -> np.ndarray:
    c = (n - 1) / 2.0
    yy, xx = np.mgrid[0:n, 0:n]
    return np.hypot(xx - c, yy - c) > frac * n / 2.0


def clean_exposure(aligned: np.ndarray, template: np.ndarray, rms: float,
                   star_sigma: float = STAR_SIGMA) -> tuple[np.ndarray, dict]:
    """aligned - template - outer offset, with star residuals masked where they would exceed RESID_SIGMA x noise.
    Returns the clean image and its statistics (offset, residual fraction eps, masked fraction, outer robust noise
    and the 99th percentile of |pixel|/rms before and after)."""
    n = aligned.shape[0]
    diff = aligned.astype(np.float32) - template
    outer = outer_mask(n) & np.isfinite(diff)
    offset = 0.0
    if outer.sum() >= 20:
        _, offset, _ = sigma_clipped_stats(diff[outer], sigma=3.0, maxiters=5)
        offset = float(offset)
    diff -= offset
    noise = rms if np.isfinite(rms) and rms > 0 else float(np.nanstd(diff))
    away = outer_mask(n, 0.25)  # stars near the object would measure the object, not the residual
    bright = away & np.isfinite(template) & np.isfinite(diff) & (template > EPS_MIN_SIGMA * noise)
    eps = float(np.median(np.abs(diff[bright]) / template[bright])) if bright.sum() >= 10 else EPS_DEFAULT
    eps = float(np.clip(eps, 0.01, 0.5))
    thresh = max(star_sigma, RESID_SIGMA / eps) * noise
    star = binary_dilation(np.isfinite(template) & (template > thresh), iterations=1)
    clean = np.where(star | ~np.isfinite(template), np.nan, diff).astype(np.float32)
    stats = {"offset": offset, "eps": eps, "eps_measured": bool(bright.sum() >= 10),
             "masked_frac": float(np.mean(~np.isfinite(clean)))}
    o = outer_mask(n)
    for key, img in (("p99_before", aligned), ("p99_after", clean)):
        v = np.abs(img[o & np.isfinite(img)])
        stats[key] = float(np.percentile(v, 99) / noise) if v.size >= 20 and noise > 0 else None
    v = clean[o & np.isfinite(clean)]
    stats["noise_after"] = float(1.4826 * np.median(np.abs(v - np.median(v)))) if v.size >= 20 else None
    return clean, stats


def clusters(centres: np.ndarray, radius_deg: float = CLUSTER_DEG) -> tuple[list[tuple[float, float]], np.ndarray]:
    """Greedy grouping of (ra, dec) points: cluster centres and the cluster index of every point."""
    out: list[tuple[float, float]] = []
    label = np.full(len(centres), -1, int)
    for i, (ra, dec) in enumerate(centres):
        for k, (rc, dc) in enumerate(out):
            if _sep_deg(ra, dec, rc, dc) <= radius_deg:
                label[i] = k
                break
        else:
            out.append((float(ra), float(dec)))
            label[i] = len(out) - 1
    return out, label


def _sep_deg(ra1, dec1, ra2, dec2):
    r1, d1, r2, d2 = map(np.radians, (ra1, dec1, ra2, dec2))
    s = np.sin((d2 - d1) / 2) ** 2 + np.cos(d1) * np.cos(d2) * np.sin((r2 - r1) / 2) ** 2
    return np.degrees(2 * np.arcsin(np.sqrt(np.clip(s, 0, 1))))


def local_position(eph: np.ndarray, mjd: float) -> tuple[float, float, float, float]:
    """(ra, dec, dt_days, speed_deg_per_day) of the object at mjd from an (n, 3) ephemeris [mjd, ra, dec]:
    interpolated between samples of one visit, else extrapolated linearly along the nearest visit segment (a
    single-sample visit stays put). dt is the time to the nearest sample."""
    t = eph[:, 0]
    i = int(np.searchsorted(t, mjd))
    if 0 < i < len(t) and t[i] - t[i - 1] <= VISIT_GAP_DAYS:
        k0, k1 = i - 1, i
    else:
        k = i - 1 if i == len(t) or (i > 0 and mjd - t[i - 1] < t[i] - mjd) else i
        if k > 0 and t[k] - t[k - 1] <= VISIT_GAP_DAYS:
            k0, k1 = k - 1, k
        elif k + 1 < len(t) and t[k + 1] - t[k] <= VISIT_GAP_DAYS:
            k0, k1 = k, k + 1
        else:
            return float(eph[k, 1]), float(eph[k, 2]), float(abs(mjd - t[k])), 0.0
    dt_seg = t[k1] - t[k0]
    dra = ((eph[k1, 1] - eph[k0, 1] + 180.0) % 360.0) - 180.0
    ddec = eph[k1, 2] - eph[k0, 2]
    f = (mjd - t[k0]) / dt_seg if dt_seg > 0 else 0.0
    ra, dec = (eph[k0, 1] + f * dra) % 360.0, eph[k0, 2] + f * ddec
    speed = math.hypot(dra * math.cos(math.radians(eph[k0, 2])), ddec) / dt_seg if dt_seg > 0 else 0.0
    return float(ra), float(dec), float(min(abs(mjd - t[k0]), abs(mjd - t[k1]))), float(speed)


def object_near(target: Target, mjd: float, ra: float, dec: float, radius_arcmin: float) -> bool:
    """Whether the object may be within radius_arcmin of (ra, dec) at time mjd, from the ephemeris (see
    local_position). Times more than EXTRAPOLATE_DAYS from every ephemeris sample are treated as clear; between a
    visit and that limit the radius grows by CURVATURE_ALLOWANCE of the distance moved."""
    eph = np.asarray(target.ephemeris or [], dtype=float).reshape(-1, 3)
    if eph.size == 0:
        return False
    eph = eph[np.argsort(eph[:, 0], kind="stable")]
    if np.min(np.abs(eph[:, 0] - mjd)) > EXTRAPOLATE_DAYS:
        return False
    ro, do, dt, speed = local_position(eph, mjd)
    allowance = CURVATURE_ALLOWANCE * speed * dt * 60.0 if dt > 0 else 0.0
    return bool(_sep_deg(ro, do, ra, dec) * 60.0 < radius_arcmin + allowance)


# ---------------------------------------------------------------- pool work
def align_template(cut: Cutout, grid: Grid, wave: WaveMap) -> dict:
    """A candidate cutout prepared like a science exposure (mask, background, resample on the exposure grid), and
    its wavelength at the grid centre from its own WCS. Module-level: pickled for the process pool."""
    masked = (cut.flags & MASK_DEFAULT) != 0
    raw = cut.image.astype(np.float32)
    bkg, rms = background(np.where(masked, np.nan, raw))
    img = np.where(masked, np.nan, raw) - bkg
    xs, ys = pixel_map(cut.wcs, cut.x0, cut.y0, grid)
    aligned = resample(img, xs, ys).astype(np.float32)
    x, y = cut.wcs.all_world2pix(grid.ra, grid.dec, 0, quiet=True)
    wl, dwl = wave(float(x), float(y))
    return {"aligned": aligned, "wl": float(wl), "dwl": float(dwl), "rms": float(rms), "bkg": float(bkg)}


# ---------------------------------------------------------------- the build step
async def _header_wcs(reader: Level2Reader, url: str):
    """WCS of a Level 2 image from its first header block (cached on disk): locates a point without a footprint."""
    import hashlib

    from astropy.io import fits
    from astropy.wcs import WCS

    from ..io import HEAD_BYTES, parse_header, slim_header

    path = cache.layout().root / "solar" / "headers" / (hashlib.sha1(url.encode()).hexdigest()[:20] + ".hdr")
    if path.exists():
        return WCS(fits.Header.fromstring(path.read_text()))
    head = await reader.c.get(url, 0, HEAD_BYTES)
    prim = parse_header(head)
    img = parse_header(head, prim[1]) if prim else None
    if img is None:
        head = await reader.c.get(url, 0, 4 * HEAD_BYTES)
        img = parse_header(head, prim[1]) if prim else None
        if img is None:
            raise RuntimeError("IMAGE header not found")
    hdr = slim_header(img[0])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(hdr.tostring())
    return WCS(hdr)


def _object_type(meta: dict) -> str:
    q = (meta.get("moving") or {}).get("query") or {}
    t = (meta.get("params") or {}).get("object_type") or q.get("obj_type") or "Asteroid"
    return "Comet" if str(t).lower().startswith("comet") else "Asteroid"


async def add_static_sky(meta: dict, arrays: dict[str, np.ndarray], aligned_by_file: dict[str, np.ndarray],
                         target: Target, grid: Grid, half: int, collections: tuple[str, ...] | None,
                         progress=None) -> dict:
    """Add clean (static-sky subtracted) exposures and stacks to a co-moving sequence result, in place.

    meta/arrays: the assembled result after patch_comoving_meta; aligned_by_file: the aligned science exposure of
    every file. Returns meta['static_sky']. Network (footprint searches, S3 reads) and the process pool.
    """
    from ..pipeline.sequence import run_in_pool  # late import: the pipeline imports this module lazily too

    report = progress or (lambda stage, done, total: None)
    t0 = time.perf_counter()
    exps = meta["exposures"]
    if not exps or "center" not in exps[0]:
        raise ValueError("static-sky subtraction needs a co-moving (solar-system object) sequence")
    n = grid.n
    otype = _object_type(meta)
    excl = n * grid.pixscale / 60.0 / math.sqrt(2.0) + COMA_MARGIN_ARCMIN[otype]
    centres = np.array([e["center"] for e in exps], dtype=float)
    cl_centres, label = clusters(centres)

    report("static sky: search", 0, len(cl_centres))
    sem_search = asyncio.Semaphore(4)
    sources: dict[str, int] = {}

    search_errors: list[str] = []

    async def search(k: int, rc: float, dc: float) -> list[SkyFrame]:
        """One cluster's footprint search, retried once; a cluster that still fails only loses its own
        templates (its exposures get method 'none'), never the whole step."""
        async with sem_search:
            for attempt in (1, 2):
                try:
                    found, src = await asyncio.to_thread(frames_covering, rc, dc, collections)
                    break
                except Exception as ex:  # noqa: BLE001 - archive outages
                    if attempt == 2:
                        search_errors.append(f"search {rc:.3f} {dc:+.3f}: {ex}"[:200])
                        found, src = [], "failed"
            sources[src] = sources.get(src, 0) + 1
            report("static sky: search", sum(sources.values()), len(cl_centres))
            return found

    with logs.timed(log, "static sky search", clusters=len(cl_centres)) as info:
        per_cluster = await asyncio.gather(*(search(k, rc, dc) for k, (rc, dc) in enumerate(cl_centres)))
        info["frames"] = sum(len(c) for c in per_cluster)
        info["sources"] = ",".join(f"{k}:{v}" for k, v in sources.items())
    t_search = time.perf_counter() - t0

    plan: list[list] = [[] for _ in exps]  # per exposure: [(SkyFrame, predicted dl)]
    n_cand = np.zeros(len(exps), int)
    async with RangeClient(settings.max_connections) as client:
        reader = Level2Reader(client, cache.layout().root)
        waves: dict[int, WaveMap] = {}
        for sfs in per_cluster:
            for sf in sfs:
                if sf.frame.detector not in waves:
                    waves[sf.frame.detector] = await reader.wave_map(sf.frame.s3_url, sf.frame.detector)
        wcs_cache: dict[str, object] = {}

        # 1-2. candidates and their predicted wavelength at each exposure centre
        for i, e in enumerate(exps):
            ra_i, dec_i = e["center"]
            dwl = e.get("dwl") or 0.0
            if not (e.get("wl") and dwl > 0):
                continue
            cands = []
            for sf in per_cluster[label[i]]:
                f = sf.frame
                if f.band != e["band"] or f.file_name == e["file"]:
                    continue
                if object_near(target, f.mjd, ra_i, dec_i, excl):
                    continue
                if sf.corners is not None:
                    x, y, inside = footprint_pixel(sf.corners, ra_i, dec_i)
                    if not inside:
                        continue
                else:
                    try:
                        w = wcs_cache.get(f.s3_url) or await _header_wcs(reader, f.s3_url)
                        wcs_cache[f.s3_url] = w
                        x, y = (float(v) for v in w.all_world2pix(ra_i, dec_i, 0, quiet=True))
                    except Exception as ex:  # noqa: BLE001
                        log.debug("header read failed %s %r", f.file_name, ex)
                        continue
                    if not (0 <= x < 2040 and 0 <= y < 2040):
                        continue
                wl_j, _ = waves[f.detector](x, y)
                d = (float(wl_j) - e["wl"]) / dwl
                if abs(d) <= LAMBDA_TOL_FAR + 0.25:  # margin for the footprint approximation
                    cands.append((sf, d))
            n_cand[i] = len(cands)
            cands.sort(key=lambda t: abs(t[1]))
            near = [c for c in cands if abs(c[1]) <= LAMBDA_TOL_NEAR]
            pick = near[:MAX_TEMPLATES]
            if len(pick) < 2:
                below = [c for c in cands if c[1] < 0 and c not in pick][:1]
                above = [c for c in cands if c[1] >= 0 and c not in pick][:1]
                pick = (pick + below + above)[:MAX_TEMPLATES]
            plan[i] = pick

        # 3. read and align every planned template
        jobs = [(i, sf) for i, p in enumerate(plan) for sf, _ in p]
        aligned_t: list[list[tuple[float, np.ndarray]]] = [[] for _ in exps]
        sem = asyncio.Semaphore(settings.max_frames_in_flight)
        done = 0
        errors: list[str] = list(search_errors)
        report("static sky: templates", 0, len(jobs))

        async def one(i: int, sf: SkyFrame) -> None:
            nonlocal done
            e = exps[i]
            g = Grid(float(e["center"][0]), float(e["center"][1]), n, grid.pixscale)
            async with sem:
                try:
                    cut = await reader.cutout(sf.frame.s3_url, g.ra, g.dec, half)
                    if cut is not None:
                        r = await run_in_pool(align_template, cut, g, waves[sf.frame.detector])
                        if np.isfinite(r["wl"]):
                            aligned_t[i].append(((r["wl"] - e["wl"]) / e["dwl"], r["aligned"]))
                except Exception as ex:  # noqa: BLE001 - one bad template must not fail the build
                    errors.append(f"{sf.frame.file_name}: {ex!r}"[:200])
                finally:
                    done += 1
                    report("static sky: templates", done, len(jobs))

        with logs.timed(log, "static sky templates", reads=len(jobs)) as info:
            await asyncio.gather(*(one(i, sf) for i, sf in jobs))
            info["mb"] = round(client.bytes_read / 1e6, 1)
            info["errors"] = len(errors) or None
        net = {"requests": client.requests, "bytes": client.bytes_read, "cache_hits": reader.cache_hits}
    t_read = time.perf_counter() - t0 - t_search

    # 4-5. clean exposures and stacks
    clean_exp = np.full((len(exps), n, n), np.nan, np.float32)
    methods = {m: 0 for m in METHODS}
    stats_all = []
    for i, e in enumerate(exps):
        tmpl, method, used = combine_templates(aligned_t[i])
        methods[method] += 1
        e.update(ss_method=method, ss_n=len(used), ss_candidates=int(n_cand[i]),
                 ss_dl=max((abs(d) for d in used), default=None))
        src = aligned_by_file.get(e["file"])
        if tmpl is None or src is None:
            continue
        c, st = clean_exposure(src, tmpl, float(e.get("rms") or float("nan")))
        clean_exp[i] = c
        e.update(ss_offset=st["offset"], ss_eps=st["eps"], ss_masked=st["masked_frac"],
                 ss_p99_before=st["p99_before"], ss_p99_after=st["p99_after"])
        stats_all.append(st)

    n_ep = len(meta["epochs"])
    stack = np.full((n_ep, len(BANDS), n, n), np.nan, np.float32)
    cover = np.zeros((n_ep, len(BANDS), n, n), np.uint16)
    for ep in range(n_ep):
        for bi, b in enumerate(BANDS):
            sel = [i for i, e in enumerate(exps) if e["epoch"] == ep and e["band"] == b
                   and e.get("ss_method", "none") != "none" and np.isfinite(clean_exp[i]).any()]
            if not sel:
                continue
            w = np.array([1.0 / exps[i]["rms"] ** 2 if exps[i].get("rms") else 0.0 for i in sel])
            noise = float(np.nanmedian([exps[i]["rms"] for i in sel]))
            stack[ep, bi], cover[ep, bi] = coadd(clean_exp[sel], w, noise)

    from ..pipeline.results import CLEAN_EXPOSURES_FILE, DTYPES, ARRAY_FILES

    def spec(name: str, file: str, shape: list[int], **extra) -> dict:
        return {"file": file, "dtype": DTYPES[name][1], "shape": shape, **extra}

    cube = [n_ep, len(BANDS), n, n]
    arrays["clean"], arrays["clean_coverage"] = stack, cover
    meta["arrays"]["clean"] = spec("clean", ARRAY_FILES["clean"], cube,
                                   unit="MJy/sr, static sky subtracted (object frame)")
    meta["arrays"]["clean_coverage"] = spec("clean_coverage", ARRAY_FILES["clean_coverage"], cube)
    if "exposures" in arrays:
        arrays["clean_exposures"] = clean_exp
        meta["arrays"]["clean_exposures"] = spec("clean_exposures", CLEAN_EXPOSURES_FILE, [len(exps), n, n])
    else:
        meta["arrays"]["clean_exposures"] = None

    def med(key):
        v = [s[key] for s in stats_all if s.get(key) is not None]
        return float(np.median(v)) if v else None

    info = {
        "version": STATIC_SKY_VERSION,
        "method": "per-exposure template from other epochs at the same detector and wavelength; median of "
                  "near-wavelength templates or linear interpolation in wavelength; outer-region offset; "
                  "star residuals masked",
        "object_type": otype, "exclusion_arcmin": excl,
        "lambda_tol_near": LAMBDA_TOL_NEAR, "lambda_tol_far": LAMBDA_TOL_FAR, "max_templates": MAX_TEMPLATES,
        "star_sigma": STAR_SIGMA, "resid_sigma": RESID_SIGMA, "outer_frac": OUTER_FRAC,
        "template_sources": sources, "clusters": len(cl_centres), "failed_searches": len(search_errors),
        "exposures": len(exps), "with_template": len(stats_all), "methods": methods,
        "templates_read": len(jobs), "median_candidates": float(np.median(n_cand)) if len(n_cand) else 0.0,
        "median_eps": med("eps"), "median_masked_frac": med("masked_frac"), "median_p99_before": med("p99_before"),
        "median_p99_after": med("p99_after"), "errors": errors[:20],
        "network": net,
        "seconds": {"search": t_search, "templates": t_read, "total": time.perf_counter() - t0},
    }
    meta["static_sky"] = info
    logs.event(log, logging.INFO, "static sky done", s=info["seconds"]["total"], exposures=len(exps),
               cleaned=len(stats_all), reads=len(jobs), **{k: v for k, v in methods.items() if v})
    return info
