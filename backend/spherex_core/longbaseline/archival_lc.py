"""Archival light-curve points of positions measured on native frames: per exposure, then per visit.

1. Every position (with proper motion) is moved to each frame's own MJD and measured (framephot).
2. Per (position, survey, band) one method: standard aperture with aperture correction, or, when at least half
   of the frames saturate at the source, one fixed ring outside the saturated core for every frame, scaled to
   the total flux with the PSF's enclosed energy (approximate absolute scale, `method: "ring"`).
3. DN -> mJy with each frame's MAGZP (Vega, F0 of the band).
4. Frames are combined per NEOWISE visit (new visit after 20 d or 30 d span), per 2MASS night group (3 d) and
   for AllWISE into one point (its Atlas tiles co-add the 2010 passes): 3-sigma clipped mean, error the larger of
   the formal and the scatter-based error of the mean, with a systematic floor.
"""

from __future__ import annotations

import math
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import numpy as np

from ..archival.build import VISIT_GAP_DAYS, VISIT_MAX_DAYS, visits
from ..targets import Target
from ..timeutil import mjd_to_iso, mjd_to_year
from .framephot import choose_ring, measure_position, ring_dn
from .frames import FrameWindow
from .instruments import WISE_BAD_BITS, WISE_SAT_BITS, Instrument

FLOOR = {"aperture": 0.015, "ring": 0.03}  # fractional systematic floor per combined point
RING_SATURATED_SHARE = 0.5
TWOMASS_GROUP_DAYS = 3.0
CLIP = 3.0


@dataclass
class Position:
    key: str  # "target" or a calibration-star id
    target: Target


def _bad_and_core(w: FrameWindow) -> tuple[np.ndarray, np.ndarray, float]:
    d = w.data
    nan = ~np.isfinite(d)
    if w.mask is not None and w.mask.shape == d.shape:
        m = w.mask.astype(np.int64)
        flagged_bad = (m & WISE_BAD_BITS) != 0
        # L1b also sets bad pixels (static noisy, dead, outliers) to NaN: only saturation bits, or a NaN no bad-pixel
        # bit explains, make a saturated core
        core = ((m & WISE_SAT_BITS) != 0) | (nan & ~flagged_bad)
        bad = core | flagged_bad | nan
    else:
        core = nan
        bad = nan
    v = d[~bad]
    if v.size > 50:
        med = float(np.median(v))
        sig = float(1.4826 * np.median(np.abs(v - med)))
    else:
        sig = float("nan")
    return bad, core, sig


def measure_window(w: FrameWindow, positions: list[Position]) -> list[tuple[str, dict]]:
    """Measure every position on one frame window: [(position key, record)]."""
    inst = w.inst
    wcs = w.wcs()
    bad, core, sig = _bad_and_core(w)
    b_in, b_out = inst.bkg_px
    magzp, file = w.magzp, w.url.rsplit("/", 1)[-1]
    out = []
    for p in positions:
        ra, dec = p.target.position_at(w.mjd)
        x, y = wcs.all_world2pix(float(ra), float(dec), 0, quiet=True)
        x, y = float(x) - w.x0, float(y) - w.y0
        if not (math.isfinite(x) and math.isfinite(y)):
            continue
        m = measure_position(w.data, bad, core, x, y, inst.r_ap_px, b_in, b_out, inst.profile_px,
                             inst.ring_bkg_px, sig)
        if m is None:
            continue
        m.update(survey=w.survey, band=w.band, mjd=w.mjd, magzp=magzp, file=file, x=x + w.x0, y=y + w.y0)
        out.append((p.key, m))
    return out


def measure_all(windows: list[FrameWindow], positions: list[Position], threads: int = 8,
                check=None) -> dict[str, list[dict]]:
    """Per-frame records of every position on every window, grouped by position key."""
    res: dict[str, list[dict]] = defaultdict(list)

    def one(w: FrameWindow):
        if check is not None:
            check()
        return measure_window(w, positions)

    with ThreadPoolExecutor(max_workers=threads) as ex:
        for rows in ex.map(one, windows):
            for key, rec in rows:
                res[key].append(rec)
    return dict(res)


def _mjy_per_dn(inst: Instrument, magzp: float) -> float:
    return inst.f0_jy * 10.0 ** (-0.4 * magzp) * 1e3


def _vega_mag(flux_mjy: float, f0_jy: float) -> float | None:
    return float(-2.5 * math.log10(flux_mjy * 1e-3 / f0_jy)) if flux_mjy and flux_mjy > 0 else None


def calibrate_frames(recs: list[dict], inst: Instrument, core_arcsec: float | None = None
                     ) -> tuple[list[dict], dict]:
    """Per-frame fluxes in mJy for one position, survey and band, with one method for all frames.

    Returns (frame points, method info). A frame is `saturated` when its saturated core touches the source or
    reaches into the aperture; without a pixel mask (2MASS, AllWISE) also when the aperture flux is brighter
    than the band's saturation limit. core_arcsec (the NEOWISE ring's inner radius in the same band) forces the
    ring method and sets its smallest inner radius: AllWISE Atlas tiles have no mask, and their co-added cores
    are only partly NaN, so their own core radius is too small.
    """
    if not recs:
        return [], {}
    for r in recs:
        ap_mjy = r["flux_dn"] * inst.apcorr * _mjy_per_dn(inst, r["magzp"])
        too_bright = (not inst.has_mask) and ap_mjy > inst.f0_jy * 1e3 * 10 ** (-0.4 * inst.sat_mag)
        r["saturated"] = bool(r["core_r"] > 0 or r["sat_in_aperture"] or too_bright)
    share = float(np.mean([r["saturated"] for r in recs]))
    info: dict = {"saturated_share": share, "frames": len(recs)}
    out = []
    if share >= RING_SATURATED_SHARE or core_arcsec:
        ring = choose_ring([r["core_r"] for r in recs if r["core_r"] > 0] or [inst.r_ap_px],
                           inst.ring_width_px, inst.profile_px)
        if core_arcsec:
            r_min = int(math.ceil(core_arcsec / inst.pixscale))
            if ring is None or ring[0] < r_min:
                ring = (r_min, r_min + inst.ring_width_px) if r_min + inst.ring_width_px <= inst.profile_px - 1 \
                    else None
            info["core_from"] = "NEOWISE"

        info["method"] = "ring"
        if ring is None:
            info["note"] = "saturated core too large for a ring inside the profile"
            return [], info
        r_in, r_out = ring
        frac = inst.ring_fraction(r_in, r_out)
        info.update(r_in_px=r_in, r_out_px=r_out, ring_fraction=frac,
                    r_in_arcsec=r_in * inst.pixscale, r_out_arcsec=r_out * inst.pixscale)
        for r in recs:
            m = ring_dn(r["profile"], r_in, r_out)
            if m is None:
                continue
            k = _mjy_per_dn(inst, r["magzp"]) / frac
            out.append({"mjd": r["mjd"], "flux_mjy": m[0] * k, "err_mjy": m[1] * k, "method": "ring",
                        "saturated": True, "flagged": False, "file": r["file"]})
    else:
        info["method"] = "aperture"
        info["apcorr"] = inst.apcorr
        for r in recs:
            k = inst.apcorr * _mjy_per_dn(inst, r["magzp"])
            flagged = bool(r["saturated"] or r["masked_frac"] > 0.1)
            out.append({"mjd": r["mjd"], "flux_mjy": r["flux_dn"] * k, "err_mjy": r["err_dn"] * k,
                        "method": "aperture", "saturated": bool(r["saturated"]), "flagged": flagged,
                        "file": r["file"]})
    return out, info


def robust_mean(f: np.ndarray, e: np.ndarray, clip: float = CLIP) -> tuple[float, float, int, int]:
    """Clipped inverse-variance mean: (mean, error, n kept, n rejected); error >= scatter / sqrt(n)."""
    keep = np.ones(f.size, bool)
    if f.size >= 4:
        med = np.median(f)
        mad = 1.4826 * np.median(np.abs(f - med))
        scale = max(mad, float(np.median(e)) * 0.5)
        keep = np.abs(f - med) <= clip * scale
        if keep.sum() < 2:
            keep[:] = True
    w = 1.0 / np.maximum(e[keep], 1e-12) ** 2
    mean = float((w * f[keep]).sum() / w.sum())
    err = float(math.sqrt(1.0 / w.sum()))
    n = int(keep.sum())
    if n >= 2:
        err = max(err, float(np.std(f[keep], ddof=1) / math.sqrt(n)))
    return mean, err, n, int(f.size - n)


def combine(frames: list[dict], inst: Instrument) -> list[dict]:
    """Frame points -> one point per visit (NEOWISE), night group (2MASS) or survey (AllWISE)."""
    if not frames:
        return []
    good = [f for f in frames if not f["flagged"] and math.isfinite(f["flux_mjy"]) and f["err_mjy"] > 0]
    use_flagged = not good
    pool = good or [f for f in frames if math.isfinite(f["flux_mjy"]) and f["err_mjy"] > 0]
    if not pool:
        return []
    t = [f["mjd"] for f in pool]
    if inst.survey == "neowise":
        groups = visits(t, VISIT_GAP_DAYS, VISIT_MAX_DAYS)
    elif inst.survey == "2mass":
        groups = visits(t, TWOMASS_GROUP_DAYS, TWOMASS_GROUP_DAYS)
    else:
        groups = [0] * len(pool)
    by: dict[int, list[dict]] = defaultdict(list)
    for f, g in zip(pool, groups):
        by[g].append(f)
    out = []
    for g in sorted(by):
        mem = by[g]
        f = np.array([m["flux_mjy"] for m in mem])
        e = np.array([m["err_mjy"] for m in mem])
        mean, err, n, rej = robust_mean(f, e)
        method = mem[0]["method"]
        err = math.hypot(err, FLOOR[method] * abs(mean))
        mj = np.array([m["mjd"] for m in mem])
        mjd = float(mj.mean())
        mag = _vega_mag(mean, inst.f0_jy)
        out.append({"survey": inst.label, "survey_id": inst.survey, "band": inst.band, "wl_um": inst.wl_um,
                    "mjd": mjd, "mjd_min": float(mj.min()), "mjd_max": float(mj.max()),
                    "year": float(mjd_to_year(mjd)), "date": mjd_to_iso(mjd)[:10],
                    "flux_mjy": mean, "err_mjy": err, "mag": mag,
                    "mag_err": float(1.0857 * err / mean) if mean > 0 else None, "mag_system": "Vega",
                    "n": n, "n_rejected": rej, "method": method,
                    "saturated": bool(np.mean([m["saturated"] for m in mem]) >= 0.5),
                    "flagged": bool(use_flagged), "relative": False})
    return out


def lightcurves(per_frame: list[dict], insts: dict[tuple[str, str], Instrument]) -> tuple[list[dict], dict]:
    """All archival points of one position (every survey and band) and the method chosen for each."""
    by: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in per_frame:
        by[(r["survey"], r["band"])].append(r)
    points, methods = [], {}
    neo_core: dict[str, float] = {}  # band -> NEOWISE ring inner radius (arcsec)
    for key in sorted(by, key=lambda k: (k[0] != "neowise", k)):  # NEOWISE first
        inst = insts[key]
        core = neo_core.get(inst.band) if not inst.has_mask and inst.survey == "allwise" else None
        frames, info = calibrate_frames(by[key], inst, core)
        if inst.survey == "neowise" and info.get("method") == "ring" and info.get("r_in_arcsec"):
            neo_core[inst.band] = float(info["r_in_arcsec"])
        methods[f"{inst.label} {inst.band}"] = info
        points += combine(frames, inst)
    points.sort(key=lambda p: (p["mjd"], p["band"]))
    return points, methods
