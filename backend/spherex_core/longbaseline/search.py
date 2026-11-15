"""Field-wide long-period variability search on the archival frames (ADR-0059).

Every CatWISE2020 source (AllWISE or Gaia DR3 when IRSA does not answer) within the window radius (brighter
than w1_max, up to max_sources, brightest first) is measured on the same NEOWISE and AllWISE frame windows as
the light curves (frames.py, cached), combined per visit, and the NEOWISE visits are normalised by their
ensemble zero point: the median over unsaturated, well-measured sources of their flux over their own median,
so a visit-wide calibration error does not look like variability. SPHEREx adds one relative point per epoch
and detector from aperture photometry on the epoch co-adds (zero point fitted). Each source then goes through
model.analyze (period search over W1/W2 and the SPHEREx bands, trends, events, verdict) and the field is
ranked by a variability score.
"""

from __future__ import annotations

import asyncio
import math
import time
from collections import defaultdict
from dataclasses import asdict, dataclass

import numpy as np

from ..archival.build import visits
from ..bands import BANDS
from ..pipeline import results
from ..timeutil import mjd_to_iso, mjd_to_year, utc_now_iso
from . import calibrate, model, refcat
from .archival_lc import Position, lightcurves, measure_all
from .frames import read_windows
from .instruments import INSTRUMENTS
from .lightcurve import (MAX_WINDOW_RADIUS_ARCSEC, PERIOD_BANDS, WINDOW_RADIUS_ARCSEC, archival_images,
                         assign_groups)

SEARCH_VERSION = 2
COADD_R_PX = 2.0
COADD_ANNULUS_PX = (4.0, 7.0)
MIN_COADD_SNR = 5.0
ENSEMBLE_MIN_SOURCES = 5
GAIA_G_MINUS_W1 = 1.5
CCF_MATCH_ARCSEC = 3.0


def contamination_flags(sources: list[dict], label: str, ra: float, dec: float, radius_deg: float,
                        notes: list[str]) -> dict[str, str]:
    """AllWISE W1/W2 contamination flags (ccf: d spike, h halo, o ghost, p latent; upper case = likely spurious)
    of each source id that has any, from the source rows themselves (AllWISE) or a 3" AllWISE match."""
    if label == "AllWISE":
        ref = sources
    else:
        try:
            ref = refcat.allwise(ra, dec, radius_deg)
        except Exception as e:  # noqa: BLE001 - flags are a refinement; the search runs without them
            notes.append(f"AllWISE contamination flags unavailable ({str(e)[:80]}); no source is marked contaminated")
            return {}
    flagged = [r for r in ref if r.get("ccf") and r["ccf"][:2].strip("0")]
    out = {}
    for src in sources:
        if label == "AllWISE":
            f = (src.get("ccf") or "")[:2]
        else:
            near = [r for r in flagged if calibrate._sep_arcsec(src["ra"], src["dec"], r["ra"], r["dec"])
                    <= CCF_MATCH_ARCSEC]
            f = near[0]["ccf"][:2] if near else ""
        if f.strip("0"):
            out[src["id"]] = f
    return out


@dataclass
class SearchOptions:
    w1_max: float = 15.0
    max_sources: int = 150
    min_points: int = 8
    min_period: float = model.DEFAULT_MIN_PERIOD
    max_period: float | None = None
    radius_arcsec: float = WINDOW_RADIUS_ARCSEC  # around the field centre, at most MAX_WINDOW_RADIUS_ARCSEC

    def to_dict(self) -> dict:
        return asdict(self)


def coadd_points(ctx, src_targets: list, keys: list[str]) -> dict[str, list[dict]]:
    """Relative per-epoch, per-detector aperture fluxes of each source on the SPHEREx co-adds."""
    cube = results.read_array(ctx.dir, ctx.meta, "coadds")  # [epochs, 6, n, n]
    n = cube.shape[-1]
    yy, xx = np.mgrid[0:n, 0:n]
    out: dict[str, list[dict]] = defaultdict(list)
    epochs = ctx.meta["epochs"]
    for key, tgt in zip(keys, src_targets):
        rows = []
        for ei, ep in enumerate(epochs):
            mjd = float(ep.get("mjd_mean") or ep.get("mjd") or 0.0)
            x, y = ctx.grid.world_to_pixel(*tgt.position_at(mjd))
            x, y = float(x), float(y)
            if not (COADD_ANNULUS_PX[1] <= x <= n - 1 - COADD_ANNULUS_PX[1]
                    and COADD_ANNULUS_PX[1] <= y <= n - 1 - COADD_ANNULUS_PX[1]):
                continue
            r = np.hypot(xx - x, yy - y)
            ap = r <= COADD_R_PX
            ann = (r >= COADD_ANNULUS_PX[0]) & (r < COADD_ANNULUS_PX[1])
            for bi, band in enumerate(BANDS):
                img = cube[ei, bi]
                a, b = img[ap], img[ann]
                if not (np.isfinite(a).all() and np.isfinite(b).sum() > 10):
                    continue
                bk = float(np.nanmedian(b))
                sig = float(1.4826 * np.nanmedian(np.abs(b - bk)))
                f = float((a - bk).sum())
                e = sig * math.sqrt(a.size * (1 + a.size / np.isfinite(b).sum()))
                if f > 0 and e > 0 and f / e >= MIN_COADD_SNR:
                    rows.append({"band": band, "mjd": mjd, "flux": f, "err": e, "epoch": ei})
        by_band: dict[str, list[dict]] = defaultdict(list)
        for r_ in rows:
            by_band[r_["band"]].append(r_)
        for band, rs in by_band.items():
            if len(rs) < 2:
                continue
            med = float(np.median([r_["flux"] for r_ in rs]))
            for r_ in rs:
                rel = r_["flux"] / med
                out[key].append({"survey": "SPHEREx", "survey_id": "spherex", "band": band, "mjd": r_["mjd"],
                                 "year": float(mjd_to_year(r_["mjd"])), "date": mjd_to_iso(r_["mjd"])[:10],
                                 "rel": rel, "rel_err": math.hypot(r_["err"] / med, 0.03 * rel), "relative": True,
                                 "mag": -2.5 * math.log10(rel),
                                 "mag_err": 1.0857 * math.hypot(r_["err"] / r_["flux"], 0.03),
                                 "method": "coadd-relative", "epoch": r_["epoch"], "n": 1})
    return out


def ensemble_normalise(points: dict[str, list[dict]]) -> dict:
    """Divide every aperture point by its visit's ensemble zero point (per survey and band), in place."""
    info = {}
    by_sb: dict[tuple[str, str], list[tuple[str, dict]]] = defaultdict(list)
    for key, pts in points.items():
        for p in pts:
            if p.get("method") == "aperture" and p["survey_id"] == "neowise":  # AllWISE is one co-add point
                by_sb[(p["survey_id"], p["band"])].append((key, p))
    for (survey, band), rows in by_sb.items():
        med = {}
        for key in {k for k, _ in rows}:
            f = [p["flux_mjy"] for k, p in rows if k == key and not p["flagged"]]
            if len(f) >= 3:
                med[key] = float(np.median(f))
        ref = [(k, p) for k, p in rows if k in med and not p["flagged"] and not p["saturated"]
               and p["err_mjy"] > 0 and p["flux_mjy"] / p["err_mjy"] > 20]
        if len({k for k, _ in ref}) < ENSEMBLE_MIN_SOURCES:
            info[f"{survey}:{band}"] = {"applied": False, "reference_sources": len({k for k, _ in ref})}
            continue
        groups = visits([p["mjd"] for _, p in ref], gap=5.0, max_len=30.0)
        zp: dict[int, list[float]] = defaultdict(list)
        for (k, p), g in zip(ref, groups):
            zp[g].append(p["flux_mjy"] / med[k])
        centres = {g: (float(np.median([p["mjd"] for (_, p), gg in zip(ref, groups) if gg == g])),
                       float(np.median(v))) for g, v in zp.items() if len(v) >= ENSEMBLE_MIN_SOURCES}
        if not centres:
            continue
        cm = np.array([c[0] for c in centres.values()])
        cz = np.array([c[1] for c in centres.values()])
        for k, p in rows:
            i = int(np.argmin(np.abs(cm - p["mjd"])))
            if abs(cm[i] - p["mjd"]) <= 10.0 and cz[i] > 0:
                p["flux_mjy"] /= cz[i]
                p["err_mjy"] /= cz[i]
                if p.get("mag") is not None:
                    p["mag"] += 2.5 * math.log10(cz[i])
        zp_mag = 2.5 * np.log10(cz)
        info[f"{survey}:{band}"] = {"applied": True, "visits": len(centres),
                                    "zero_point_scatter_mag": float(1.4826 * np.median(np.abs(zp_mag
                                                                                            - np.median(zp_mag))))}
    return info


def _score(a: dict) -> float:
    st = a.get("band_stats", {})
    chi2 = max([st[b]["chi2_red_constant"] for b in ("W1", "W2") if b in st] or [1.0])
    amp = max([st[b]["range_5_95_mag"] or 0.0 for b in ("W1", "W2") if b in st] or [0.0])
    p = a.get("period") or {}
    s = math.log10(max(chi2, 1e-3)) + 2.0 * amp
    if p.get("significant"):
        s += 1.0 + p.get("power", 0.0)
    return float(s)


def search_field(ctx, opts: SearchOptions, progress, check) -> dict:
    t0 = time.perf_counter()
    grid = ctx.grid
    if ctx.meta["target"].get("ephemeris"):
        raise ValueError("the long-period search needs a fixed field, not a co-moving sequence")
    radius = float(opts.radius_arcsec)
    if not 30.0 <= radius <= MAX_WINDOW_RADIUS_ARCSEC:
        raise ValueError(f"radius_arcsec must be within 30-{MAX_WINDOW_RADIUS_ARCSEC:.0f}")
    progress("archive search", 0, 0)
    images = archival_images(grid, ("allwise", "neowise"))
    check()

    progress("catalogues", 0, 0)
    notes: list[str] = []
    cw, label = refcat.wise_reference(grid.ra, grid.dec, radius / 3600.0, notes)
    if not cw:
        raise ValueError("no source catalogue (CatWISE2020, AllWISE or Gaia DR3) answered; try again shortly")
    if label == "Gaia DR3":  # no W1: select by G, ~1.5 mag above W1 for the typical field star
        cw = [{**s, "g": s.get("mag"), "w1": s.get("mag") - GAIA_G_MINUS_W1 if s.get("mag") is not None else None,
               "mag": None} for s in cw]
    src = [s for s in cw if s.get("ra") is not None and s.get("w1") is not None and s["w1"] <= opts.w1_max
           and calibrate._sep_arcsec(s["ra"], s["dec"], grid.ra, grid.dec) <= radius - 10.0]
    src.sort(key=lambda s: s["w1"])
    src = src[:opts.max_sources]
    targets = [calibrate.star_target(s) for s in src]
    keys = [s["id"] for s in src]
    ccf = contamination_flags(src, label, grid.ra, grid.dec, radius / 3600.0, notes)
    check()

    def frames_progress(done, total):
        progress("frames", done, total)

    windows, frame_stats = asyncio.run(read_windows(images, grid.ra, grid.dec, radius,
                                                    frames_progress, check))
    t_frames = time.perf_counter() - t0
    progress("photometry", 0, len(src))
    per_frame = measure_all(windows, [Position(k, t) for k, t in zip(keys, targets)], check=check)
    pts = {k: lightcurves(per_frame.get(k, []), INSTRUMENTS)[0] for k in keys}
    ens = ensemble_normalise(pts)
    sx = coadd_points(ctx, targets, keys)
    t_phot = time.perf_counter() - t0 - t_frames

    rows = []
    for i, (s, k) in enumerate(zip(src, keys)):
        check()
        progress("analysis", i, len(src))
        p_all = [p for p in pts[k] if p.get("mag") is not None] + sx.get(k, [])
        assign_groups(p_all)
        row = {"id": k, "ra": s["ra"], "dec": s["dec"], "w1": s.get("w1"), "w2": s.get("mag"),
               "n_points": len(p_all), "saturated": any(p.get("method") == "ring" for p in p_all),
               "contamination": ccf.get(k)}
        if len(p_all) < opts.min_points:
            row.update(score=None, label="too few points")
            rows.append(row)
            continue
        try:
            a = model.analyze(p_all, period_bands=PERIOD_BANDS, min_period=opts.min_period,
                              max_period=opts.max_period)
        except ValueError as e:
            row.update(score=None, label=str(e))
            rows.append(row)
            continue
        per = a.get("period") or {}
        st = a.get("band_stats", {})
        row.update(score=_score(a), label=a["classification"]["label"], variable=a["classification"]["variable"],
                   periodic=a["classification"]["periodic"], reasons=a["classification"]["reasons"],
                   chi2_red={b: st[b]["chi2_red_constant"] for b in ("W1", "W2") if b in st},
                   range_mag={b: st[b]["range_5_95_mag"] for b in ("W1", "W2") if b in st},
                   period_days=per.get("period_days"), period_power=per.get("power"),
                   period_fap=per.get("false_alarm_probability"), cadence_alias=per.get("cadence_alias"),
                   amplitudes_mag=per.get("amplitudes_mag") if per.get("significant") else None,
                   trends={b: {"slope_mag_per_year": tr["slope_mag_per_year"], "significance": tr["significance"]}
                           for b, tr in a.get("trends", {}).items() if b in ("W1", "W2")},
                   events=[{k_: e[k_] for k_ in ("kind", "start_mjd", "end_mjd", "peak_mag", "bands")}
                           for e in a.get("events", [])],
                   baseline_years=a["baseline_days"] / 365.25,
                   points=[{k_: p.get(k_) for k_ in ("band", "survey", "mjd", "mag", "mag_err", "group")}
                           for p in sorted(p_all, key=lambda q: q["mjd"])])
        rows.append(row)
    # clean sources first; contaminated ones (bright-star halo, spikes, ghosts, latents) after them
    ranked = sorted(rows, key=lambda r: (r.get("score") is None, bool(r.get("contamination")),
                                         -(r.get("score") or 0.0)))
    measured = [r for r in ranked if r.get("score") is not None and not r.get("contamination")]
    labels: dict[str, int] = defaultdict(int)
    for r in measured:
        labels[r["label"]] += 1
    return {
        "version": SEARCH_VERSION, "kind": "long-period search", "sequence": ctx.seq_id, "created": utc_now_iso(),
        "options": opts.to_dict(), "field": {"ra": grid.ra, "dec": grid.dec, "radius_arcsec": radius},
        "summary": {"sources": len(src), "measured": len(measured),
                    "contaminated": sum(1 for r in rows if r.get("score") is not None and r.get("contamination")),
                    "labels": dict(labels),
                    "variable_fraction": (sum(bool(r.get("variable")) for r in measured) / len(measured))
                    if measured else None,
                    "long_period_candidates": [r["id"] for r in measured
                                               if r.get("periodic") and (r.get("period_days") or 0) >= 100][:20]},
        "candidates": ranked,
        "ensemble": ens,
        "stats": {"images": len(images), "frames": frame_stats, "seconds": {"frames": t_frames, "photometry": t_phot,
                                                                            "total": time.perf_counter() - t0}},
        "notes": [f"Sources are {label} within the window radius; SPHEREx points are relative epoch co-add fluxes "
                  "with fitted zero points. Sources with AllWISE W1/W2 contamination flags (halo, spike, ghost, "
                  "latent) are measured but left out of the summary and listed last.", *notes],
    }


__all__ = ["SEARCH_VERSION", "SearchOptions", "coadd_points", "contamination_flags", "ensemble_normalise",
           "search_field"]
