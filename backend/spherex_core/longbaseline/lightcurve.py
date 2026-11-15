"""One position's homogeneous multi-decade light curve: 2MASS (~1999), AllWISE (2010), NEOWISE (2014-2024) and
SPHEREx (2025-) on common band scales, with the long-baseline period and variability analysis (ADR-0059).

Steps (each reported as a job stage):
  search      archival images around the field (CAOM TAP, cached), NEOWISE thinned to MAX_VISIT_FRAMES per visit
  catalogues  CatWISE2020 and 2MASS PSC cones for calibration stars (optional: failures become notes)
  frames      native windows of every frame (frames.py, cached) while SPHEREx re-measures the calibration stars
  photometry  target and stars on every frame, per visit (archival_lc.py)
  calibrate   frames -> catalogues, SPHEREx synthetic -> NEOWISE / 2MASS (calibrate.py)
  analysis    period search over W1/W2 and the SPHEREx narrow bands, folding, trends, events (model.py)
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from dataclasses import asdict, dataclass

import numpy as np

from ..archival.build import limit_per_visit
from ..archival.sources import find_images
from ..bands import BANDS
from ..targets import Target
from ..timeutil import mjd_to_iso, mjd_to_year, utc_now_iso, year_to_mjd
from . import calibrate, model, refcat
from .archival_lc import Position, lightcurves, measure_all
from .frames import read_windows
from .instruments import INSTRUMENTS, VEGA_F0_JY
from .spherex_lc import BIN_DAYS, mean_synthetic, measure_positions, narrowband_points, synthetic_points

log = logging.getLogger(__name__)
LB_VERSION = 2
SURVEYS = ("2mass", "allwise", "neowise")
WINDOW_RADIUS_ARCSEC = 150.0  # frame windows around the target (and the calibration-star radius)
MAX_WINDOW_RADIUS_ARCSEC = 360.0
TRACK_MARGIN_ARCSEC = 30.0
MAX_VISIT_FRAMES = 12
PERIOD_BANDS = ("W1", "W2", *BANDS)
SEQ_TARGET_MATCH_ARCSEC = 1.5


@dataclass
class LongBaselineRequest:
    ra: float | None = None  # None: the sequence's own target
    dec: float | None = None
    pmra: float | None = None  # None: a Gaia counterpart's motion, else 0
    pmdec: float | None = None
    ref_epoch: float = 2000.0
    name: str | None = None
    calibrate: bool = True
    min_period: float | None = None
    max_period: float | None = None
    field_radius: float = WINDOW_RADIUS_ARCSEC  # arcsec: frame windows and calibration stars around the target

    def key(self) -> dict:
        d = asdict(self)
        for k in ("ra", "dec"):
            if d[k] is not None:
                d[k] = round(float(d[k]), 5)
        return d


def _sep(ra1, dec1, ra2, dec2) -> float:
    return calibrate._sep_arcsec(float(ra1), float(dec1), float(ra2), float(dec2))


def _sequence_target(meta: dict) -> Target:
    t = meta["target"]
    return Target(ra=float(t["ra"]), dec=float(t["dec"]), name=t.get("name"), pmra=float(t.get("pmra") or 0.0),
                  pmdec=float(t.get("pmdec") or 0.0), ref_epoch=float(t.get("ref_epoch") or 2000.0))


def resolve_target(meta: dict, req: LongBaselineRequest, notes: list[str]) -> tuple[Target, bool]:
    """The position to measure and whether it is the sequence's own target (whose photometry is reused)."""
    seq_t = _sequence_target(meta)
    if meta["target"].get("ephemeris"):
        raise ValueError("long-baseline light curves are for fixed or proper-motion targets, not solar-system objects")
    if req.ra is None or req.dec is None:
        return seq_t, True
    t = Target(ra=float(req.ra), dec=float(req.dec), name=req.name, pmra=float(req.pmra or 0.0),
               pmdec=float(req.pmdec or 0.0), ref_epoch=float(req.ref_epoch))
    if req.pmra is None and req.pmdec is None:
        try:
            from .. import catalogs, netpolicy

            with netpolicy.fast():
                gaia = catalogs.cone("gaia", t.ra, t.dec, 3.0 / 3600.0)
            best = min(gaia, key=lambda s: _sep(s["ra"], s["dec"], t.ra, t.dec), default=None)
            if best is not None and _sep(best["ra"], best["dec"], t.ra, t.dec) <= 3.0 and best.get("pmra") is not None:
                t = Target(ra=best["ra"], dec=best["dec"], name=req.name or best.get("id"), pmra=best["pmra"],
                           pmdec=best.get("pmdec") or 0.0, ref_epoch=float(best.get("epoch") or 2016.0),
                           source="gaia")
                notes.append(f"position and motion from {best.get('id')}")
        except Exception as e:  # noqa: BLE001 - no motion is a fine default
            notes.append(f"no Gaia motion ({str(e)[:80]}); assuming none")
    mjd_ref = year_to_mjd(seq_t.ref_epoch)
    same = (_sep(*t.position_at(mjd_ref), *seq_t.position_at(mjd_ref)) <= SEQ_TARGET_MATCH_ARCSEC
            and abs(t.pmra - seq_t.pmra) < 50 and abs(t.pmdec - seq_t.pmdec) < 50)
    return t, bool(same)


def _window_radius(grid_ra: float, grid_dec: float, target: Target, base: float = WINDOW_RADIUS_ARCSEC) -> float:
    years = np.linspace(1997.5, 2027.0, 60)
    off = max(_sep(*target.position_at(year_to_mjd(y)), grid_ra, grid_dec) for y in years)
    return float(min(max(base, off + TRACK_MARGIN_ARCSEC), MAX_WINDOW_RADIUS_ARCSEC))


def _flux_to_mag(points: list[dict]) -> None:
    for p in points:
        if p.get("relative") and p.get("rel"):
            p["mag"] = float(-2.5 * math.log10(p["rel"])) if p["rel"] > 0 else None
            p["mag_err"] = float(1.0857 * p["rel_err"] / p["rel"]) if p["rel"] > 0 and p.get("rel_err") else None


def assign_groups(points: list[dict]) -> None:
    """Offset groups: one per band for points on a calibrated scale, one per (band, survey, method) for rings and
    relative segments, whose zero points the analysis fits."""
    for p in points:
        if p.get("relative"):
            p["group"] = f"{p['band']}:{p['survey']}-relative"
        elif p.get("method") == "ring":
            p["group"] = f"{p['band']}:{p['survey']}-ring"
        else:
            p["group"] = p["band"]


def anchor(points: list[dict], offsets: dict[str, float]) -> dict[str, dict]:
    """Shift points of fitted groups onto their band's reference group: the calibrated one, else the ring group with
    the most points (PSF-wing scale), else the largest group. Adds mag_anchored (and flux_anchored_mjy for Vega/AB
    bands)."""
    info: dict[str, dict] = {}
    by_band: dict[str, set[str]] = {}
    for p in points:
        by_band.setdefault(p["band"], set()).add(p["group"])
    for band, groups in by_band.items():
        ref = band if band in groups else None
        if ref is None:  # most points among ring groups, else among all
            pool = sorted(g for g in groups if g.endswith("-ring")) or sorted(groups)
            ref = max(pool, key=lambda g: sum(q["group"] == g for q in points))
        absolute = not ref.endswith("-relative")
        for g in groups:
            info[g] = {"band": band, "reference": ref, "shift_mag": (offsets.get(ref, 0.0) - offsets.get(g, 0.0))
                       if g != ref else 0.0, "fitted": g != ref, "absolute_scale": absolute}
    for p in points:
        if p.get("mag") is None:
            continue
        sh = info[p["group"]]["shift_mag"]
        p["mag_anchored"] = p["mag"] + sh
        p["anchored"] = bool(info[p["group"]]["fitted"])
        f0 = VEGA_F0_JY.get(p["band"], 3631.0)
        if info[p["group"]]["absolute_scale"]:
            p["flux_anchored_mjy"] = float(f0 * 1e3 * 10 ** (-0.4 * p["mag_anchored"]))
    return info


def _twomass_catalog_points(target: Target, psc: list[dict], have: set[str]) -> list[dict]:
    """2MASS PSC magnitudes of the target for bands its images saturate in (or do not cover)."""
    out = []
    if not psc:
        return out
    near = []
    for s in psc:
        if s.get("ra") is None:
            continue
        ra_t, dec_t = target.position_at(year_to_mjd(s.get("epoch") or 2000.0))
        near.append((_sep(s["ra"], s["dec"], ra_t, dec_t), s))
    if not near:
        return out
    d, s = min(near, key=lambda x: x[0])
    if d > 3.0:
        return out
    mjd = float(year_to_mjd(s.get("epoch") or 2000.0))
    for band, col in (("J", "j"), ("H", "h"), ("Ks", "mag")):
        m = s.get(col)
        if m is None or band in have:
            continue
        f0 = VEGA_F0_JY[band]
        flux = float(f0 * 1e3 * 10 ** (-0.4 * m))
        out.append({"survey": "2MASS", "survey_id": "2mass", "band": band, "mjd": mjd, "mjd_min": mjd, "mjd_max": mjd,
                    "year": float(mjd_to_year(mjd)), "date": mjd_to_iso(mjd)[:10], "flux_mjy": flux,
                    "err_mjy": flux * 0.05, "mag": float(m), "mag_err": 0.05, "mag_system": "Vega", "n": 1,
                    "method": "psc", "saturated": True, "flagged": False, "relative": False,
                    "catalog_id": s.get("id"), "separation_arcsec": d})
    return out


def archival_images(grid, surveys: tuple[str, ...] = SURVEYS, per_visit: int = MAX_VISIT_FRAMES) -> list:
    """Archival images covering the field (CAOM, cached), NEOWISE thinned to per_visit frames per visit and band."""
    search_r = grid.n * grid.pixscale / 3600.0 * 0.5 * math.sqrt(2.0)
    images = find_images(grid.ra, grid.dec, search_r, surveys)
    neo = [im for im in images if im.survey == "neowise"]
    other = [im for im in images if im.survey != "neowise"]
    thinned = []
    for band in ("W1", "W2"):
        thinned += limit_per_visit([im for im in neo if im.band == band], per_visit)
    return sorted(other + thinned, key=lambda im: (im.mjd or 0.0, im.survey, im.band))


def run(ctx, req: LongBaselineRequest, progress, check) -> dict:
    """The job body (runs in a worker thread)."""
    t0 = time.perf_counter()
    timings: dict[str, float] = {}
    notes: list[str] = []
    meta = ctx.meta
    grid = ctx.grid
    target, is_seq = resolve_target(meta, req, notes)
    # frame windows and calibration stars are centred on the target in the middle of the NEOWISE years
    ra_c, dec_c = (float(v) for v in target.position_at(year_to_mjd(2018.0)))
    px, py = grid.world_to_pixel(ra_c, dec_c)
    if not (0 <= float(px) <= grid.n - 1 and 0 <= float(py) <= grid.n - 1):
        raise ValueError("the position is outside the sequence's field; build a sequence on it")
    field_r = float(req.field_radius)
    if not 60.0 <= field_r <= MAX_WINDOW_RADIUS_ARCSEC:
        raise ValueError(f"field_radius must be within 60-{MAX_WINDOW_RADIUS_ARCSEC:.0f} arcsec")
    radius = _window_radius(ra_c, dec_c, target, field_r)

    progress("archive search", 0, 0)
    images = archival_images(grid)
    timings["search"] = time.perf_counter() - t0
    check()

    progress("catalogues", 0, 0)
    stars: list[dict] = []
    psc: list[dict] = []
    refs = {"wise": "", "2mass": ""}
    if req.calibrate:
        from concurrent.futures import ThreadPoolExecutor

        wise_notes: list[str] = []
        tm_notes: list[str] = []
        with ThreadPoolExecutor(2) as pool:  # both catalogues (and their fallbacks) at once
            fw = pool.submit(refcat.wise_reference, ra_c, dec_c, field_r / 3600.0, wise_notes)
            ft = pool.submit(refcat.twomass_reference, ra_c, dec_c, max(radius, field_r) / 3600.0, tm_notes)
            wise_rows, refs["wise"] = fw.result()
            psc, refs["2mass"] = ft.result()
        notes += wise_notes + tm_notes
        avoid = [target.position_at(year_to_mjd(2018.0))]
        pick = calibrate.select_gaia_stars if refs["wise"] == "Gaia DR3" else calibrate.select_stars
        stars = pick(wise_rows, (ra_c, dec_c), field_r - 10.0, avoid)
        if wise_rows and len(stars) < calibrate.MIN_STARS:
            notes.append(f"only {len(stars)} calibration star(s) in {refs['wise']} within "
                         f"{field_r - 10:.0f}\" of the target (a larger field_radius finds more)")
    timings["catalogues"] = time.perf_counter() - t0 - sum(timings.values())
    check()

    star_targets = [calibrate.star_target(s) for s in stars]
    positions = [Position("target", target)] + [Position(s["id"], st) for s, st in zip(stars, star_targets)]

    async def gather():
        def frames_progress(done, total):
            progress("frames", done, total)

        wins = read_windows(images, ra_c, dec_c, radius, frames_progress, check)
        sx_targets = ([] if is_seq else [target]) + star_targets
        sx = measure_positions(ctx, sx_targets, check) if sx_targets else asyncio.sleep(0, ([], {}))
        return await asyncio.gather(wins, sx)

    (windows, frame_stats), (sx_records, sx_stats) = asyncio.run(gather())
    timings["frames_and_spherex"] = time.perf_counter() - t0 - sum(timings.values())
    check()

    if is_seq:
        target_sx = list(meta["photometry"])
        star_sx = sx_records
    else:
        target_sx = sx_records[0] if sx_records else []
        star_sx = sx_records[1:] if sx_records else []
        if not target_sx and sx_stats.get("not_cached"):
            from ..analysis.remeasure import measure_position as remeasure

            target_sx = asyncio.run(remeasure(ctx, target))["photometry"]

    progress("photometry", 0, len(windows))
    per_frame = measure_all(windows, positions, check=check)
    arch = {}
    methods = {}
    for p in positions:
        arch[p.key], methods[p.key] = lightcurves(per_frame.get(p.key, []), INSTRUMENTS)
    timings["photometry"] = time.perf_counter() - t0 - sum(timings.values())
    check()

    progress("calibrate", 0, 0)
    target_pts = arch["target"]
    # 2MASS bands the images saturate in: the PSC magnitude (read-1 photometry) replaces our ring points, which stay
    # when the PSC has no answer
    have_2mass = {p["band"] for p in target_pts if p["survey_id"] == "2mass" and not p["saturated"]}
    psc_pts = _twomass_catalog_points(target, psc, have_2mass)
    psc_bands = {p["band"] for p in psc_pts}
    target_pts = [p for p in target_pts if not (p["survey_id"] == "2mass" and p["saturated"]
                                                and p["band"] in psc_bands)] + psc_pts
    psc_match = calibrate.match_2mass(stars, psc)
    star_pts = {s["id"]: arch.get(s["id"], []) for s in stars}
    cal: dict = {"references": refs,
                 "stars": [{k: s.get(k) for k in ("id", "ra", "dec", "w1", "mag", "g")} for s in stars]}
    frame_zp = calibrate.frame_zero_points(stars, star_pts, psc_match, refs["wise"] or "CatWISE2020") if stars else {}
    for key, zp in frame_zp.items():
        survey, band = key.split(":")
        if zp["applied"]:
            for pts in [target_pts, *star_pts.values()]:
                aperture = [p for p in pts if p.get("method") == "aperture"]
                calibrate.apply_offset(aperture, survey, band, zp["offset_mag"])
    cal["frames"] = frame_zp

    sx_synth, sx_notes = synthetic_points(target_sx, bin_days=BIN_DAYS)
    notes += sx_notes
    sx_narrow = narrowband_points(target_sx, bin_days=BIN_DAYS)
    star_synth = {s["id"]: mean_synthetic(r) for s, r in zip(stars, star_sx)}
    star_bins = {s["id"]: synthetic_points(r, bands=("W1", "W2", "J", "H", "Ks"))[0] for s, r in zip(stars, star_sx)}
    sx_zp = calibrate.spherex_zero_points(stars, star_pts, star_synth, star_bins, psc_match) if stars else {}
    for band in ("J", "H", "Ks", "W1", "W2"):
        zp = sx_zp.get(band) or {"applied": False, "default_offset_mag": calibrate.DEFAULT_SPHEREX_OFFSETS[band]}
        off = zp["offset_mag"] if zp.get("applied") else zp.get("default_offset_mag", 0.0)
        sx_zp.setdefault(band, {**zp, "offset_mag": off})
        sx_zp[band]["used_offset_mag"] = off
        sx_zp[band]["source"] = "field stars" if zp.get("applied") else "default"
        calibrate.apply_offset([p for p in sx_synth if not p.get("relative")], "spherex", band, off)
    cal["spherex"] = sx_zp
    cal["star_photometry"] = {s["id"]: {"neowise": {b: calibrate._median_mag(star_pts[s["id"]], "neowise", b)[0]
                                                    for b in ("W1", "W2")},
                                        "spherex_synthetic": {b: (star_synth[s["id"]].get(b) or {}).get("mag")
                                                              for b in ("W1", "W2", "J", "H", "Ks")},
                                        "catalogue": {"W1": s.get("w1"), "W2": s.get("mag"), "G": s.get("g")}}
                             for s in stars}

    points = target_pts + sx_synth + sx_narrow
    _flux_to_mag(points)
    assign_groups(points)
    timings["calibrate"] = time.perf_counter() - t0 - sum(timings.values())
    check()

    progress("analysis", 0, 0)
    analysis: dict = {}
    try:
        analysis = model.analyze(points, period_bands=PERIOD_BANDS, min_period=req.min_period,
                                 max_period=req.max_period)
        p = analysis.get("period") or {}
        offsets = p.get("offsets") if p.get("significant") else analysis["constant_model"]["offsets"]
        # bands outside the period search keep their constant-model offsets
        offsets = {**analysis["constant_model"]["offsets"], **(offsets or {})}
        analysis["anchoring"] = anchor(points, offsets)
        if p.get("significant"):
            analysis["folded"] = [{"band": q["band"], "survey": q["survey"], "group": q["group"],
                                   "phase": float(((q["mjd"] - p["t0_mjd"]) / p["period_days"]) % 1.0),
                                   "mag": q.get("mag_anchored"), "mag_err": q.get("mag_err"), "mjd": q["mjd"]}
                                  for q in points if q.get("mag_anchored") is not None and q["band"] in p["bands"]]
    except ValueError as e:
        analysis = {"error": str(e)}
        anchor(points, {})
    timings["analysis"] = time.perf_counter() - t0 - sum(timings.values())

    points.sort(key=lambda q: (q["mjd"], q["band"]))
    bands_out: dict[str, dict] = {}
    for q in points:
        b = bands_out.setdefault(q["band"], {"n": 0, "surveys": set(), "first": q["date"], "last": q["date"]})
        b["n"] += 1
        b["surveys"].add(q["survey"])
        b["last"] = q["date"]
    for b in bands_out.values():
        b["surveys"] = sorted(b["surveys"])
    years = [q["year"] for q in points]
    return {
        "version": LB_VERSION, "kind": "long-baseline light curve", "sequence": ctx.seq_id, "created": utc_now_iso(),
        "target": target.to_dict(), "is_sequence_target": is_seq, "request": req.key(),
        "coverage": {"first_year": min(years) if years else None, "last_year": max(years) if years else None,
                     "baseline_years": (max(years) - min(years)) if years else 0.0, "bands": bands_out},
        "points": points,
        "methods": methods.get("target", {}),
        "calibration": cal,
        "analysis": analysis,
        "processing": {"window_centre": {"ra": ra_c, "dec": dec_c}, "window_radius_arcsec": radius,
                       "max_visit_frames": MAX_VISIT_FRAMES,
                       "spherex_bin_days": BIN_DAYS, "period_bands": list(PERIOD_BANDS),
                       "mag_floor": model.MAG_FLOOR},
        "stats": {"images": len(images), "frames": frame_stats, "spherex": sx_stats, "seconds": timings,
                  "total_seconds": time.perf_counter() - t0},
        "notes": notes,
    }


__all__ = ["LB_VERSION", "LongBaselineRequest", "anchor", "archival_images", "assign_groups", "resolve_target", "run"]
