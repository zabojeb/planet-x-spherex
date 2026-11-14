"""Turn per-frame results into the sequence: epochs, co-adds per epoch and detector, tables for the client.

Pure computation, no disk I/O: returns (meta, arrays) for pipeline.results.write_result.
"""

from __future__ import annotations

from dataclasses import asdict

import numpy as np

from ..archive import Frame
from ..bands import BAND_INFO, BANDS
from ..config import settings
from ..flags import FILLED, MAX_MASKED_FRACTION, flag_bits_table
from ..photometry import (PSF_FIT_RADIUS, RING_CORE_PERCENTILE, RING_PROFILE_RADIUS, RING_SATURATED_SHARE,
                          RING_WIDTH, SYSTEMATIC_FLOOR, ab_mag, ring_flux)
from ..processing import MERGE_GAP_DAYS, MIN_EPOCH_FRAMES, Grid, coadd, fill_holes, group_epochs
from ..targets import Target
from ..timeutil import mjd_to_iso, utc_now_iso
from .params import ALGO_VERSION, SequenceParams
from .results import ARRAY_FILES, DTYPES, EXPOSURES_FILE

MAX_EXPOSURE_VOXELS = 80_000_000  # single exposures are stored as float16 -> 160 MB at most

# Fields a photometry record needs when a ring measurement replaces a missing PSF/aperture one.
_EMPTY_PHOT = {"flux_ap_mjy": None, "err_ap_mjy": None, "ap_flags": 0, "masked_frac": None, "npix": None,
               "chi2": None, "psf_weight": None, "saturated": True}


def _saturated(r: dict) -> bool:
    """Saturated in this exposure: the photometry says so, or it failed and a bad-pixel blob covers the source."""
    if r["phot"] is not None:
        return bool(r["phot"].get("saturated"))
    return r.get("ring") is not None and r["ring"]["core_r"] > 1.5


def apply_rings(ok: list[dict]) -> dict[str, dict]:
    """Switch every detector in which the target saturates to ring photometry, in place.

    A detector switches when at least RING_SATURATED_SHARE of its exposures are saturated (or could not be
    measured). All its exposures then use one ring, starting 1 px outside the RING_CORE_PERCENTILE of the
    saturated-core radii, so the fluxes stay comparable while the star's brightness and core size change.
    Exposures whose ring cannot be measured lose their photometry rather than mixing methods within a
    detector. Returns per-detector details for meta.processing.
    """
    info: dict[str, dict] = {}
    for b in BANDS:
        sel = [r for r in ok if r["band"] == b]
        profiles = [r["ring"] for r in sel if r.get("ring") is not None]
        if not sel or not profiles:
            continue
        share = float(np.mean([_saturated(r) for r in sel]))
        if share < RING_SATURATED_SHARE:
            continue
        core = float(np.percentile([p["core_r"] for p in profiles], RING_CORE_PERCENTILE))
        r_in = int(np.ceil(core)) + 1
        r_out = min(r_in + RING_WIDTH, RING_PROFILE_RADIUS)
        detail = {"saturated_share": share, "core_radius_px": core, "r_in": r_in, "r_out": r_out, "exposures": 0}
        if r_out - r_in < 2:
            info[b] = {**detail, "r_in": None, "r_out": None, "note": "saturated core too large for a ring"}
            continue
        for r in sel:
            m = ring_flux(r["ring"], r_in, r_out, r["omega"]) if r.get("ring") is not None else None
            if m is None:
                r["phot"] = None
                continue
            old = r["phot"] or {}
            r["phot"] = {**_EMPTY_PHOT, **old, **m, "method": "ring", "relative": True, "ring_r_in": r_in,
                         "ring_r_out": r_out, "saturated": bool(old.get("saturated", True)), "chi2": None,
                         "psf_weight": None, "flagged": False}
            detail["exposures"] += 1
        info[b] = detail
    return info


def assemble(p: SequenceParams, target: Target, grid: Grid, frames: list[Frame], results: list[dict | None],
             errors: list[str], flag_defs: dict) -> tuple[dict, dict[str, np.ndarray]]:
    ok = [r for r in results if r is not None]
    if not ok:
        raise RuntimeError("Could not read any image. " + "; ".join(errors[:3]))
    n = grid.n
    mjds = np.array([r["mjd"] for r in ok])
    ep, partial = group_epochs(mjds, p.gap_days, p.max_epoch_days)
    n_ep = len(partial)
    for r, e in zip(ok, ep):
        r["epoch"] = int(e)
    ok.sort(key=lambda r: (r["epoch"], BANDS.index(r["band"]), r["mjd"]))
    rings = apply_rings(ok)

    coadds = np.full((n_ep, len(BANDS), n, n), np.nan, np.float32)
    coverage = np.zeros((n_ep, len(BANDS), n, n), np.uint16)
    flagmap = np.zeros((n_ep, len(BANDS), n, n), np.uint32)
    epochs = []
    for e in range(n_ep):
        members = [r for r in ok if r["epoch"] == e]
        bands = {}
        for bi, b in enumerate(BANDS):
            sel = [r for r in members if r["band"] == b]
            if not sel:
                continue
            stack = np.stack([r["aligned"] for r in sel])
            weights = np.array([r["weight"] for r in sel])
            noise = float(np.nanmedian([r["rms"] for r in sel]))
            coadds[e, bi], coverage[e, bi] = coadd(stack, weights, noise)
            flagmap[e, bi] = np.bitwise_or.reduce(np.stack([r["flags"] for r in sel]), axis=0)
            filled = fill_holes(coadds[e, bi], coverage[e, bi], np.stack([r["aligned_raw"] for r in sel]))
            flagmap[e, bi][filled] |= np.uint32(FILLED)
            wls = [r["wl"] for r in sel]
            bands[b] = {"n": len(sel), "wl_min": min(wls), "wl_max": max(wls),
                        "mjd_mean": float(np.mean([r["mjd"] for r in sel])), "noise": noise,
                        "filled_pixels": int(filled.sum())}
        em = [r["mjd"] for r in members]
        epochs.append({"index": e, "n": len(members), "partial": partial[e], "mjd_mean": float(np.mean(em)),
                       "mjd_min": min(em), "mjd_max": max(em), "date_min": mjd_to_iso(min(em)),
                       "date_max": mjd_to_iso(max(em)), "bands": bands})

    arrays: dict[str, np.ndarray] = {"coadds": coadds, "coverage": coverage, "flags": flagmap}
    keep_exposures = len(ok) * n * n <= MAX_EXPOSURE_VOXELS
    if keep_exposures:
        arrays["exposures"] = np.stack([r["aligned"] for r in ok])

    exposures, photometry = [], []
    tx_all, ty_all = grid.world_to_pixel(*target.position_at(np.array([r["mjd"] for r in ok])))
    for i, r in enumerate(ok):
        exposures.append({"index": i, "epoch": r["epoch"], "band": r["band"], "mjd": r["mjd"],
                          "date": mjd_to_iso(r["mjd"]), "wl": r["wl"], "dwl": r["dwl"], "rms": r["rms"],
                          "psf_fwhm": r["psf_fwhm"], "obs_id": r["obs_id"], "collection": r["collection"],
                          "file": r["file"], "flag_counts": r["flag_counts"],
                          "target_px": [float(tx_all[i]), float(ty_all[i])]})
        ph = r["phot"]
        if ph is not None:
            relative = bool(ph.get("relative", False))
            photometry.append({"index": i, "epoch": r["epoch"], "band": r["band"], "mjd": r["mjd"],
                               "wl": r["wl"], "dwl": r["dwl"], "flux_mjy": ph["flux_mjy"],
                               "err_mjy": ph["err_mjy"], "ab_mag": None if relative else ab_mag(ph["flux_mjy"]),
                               "flagged": ph["flagged"], "ap_flags": ph["ap_flags"],
                               "masked_frac": ph["masked_frac"], "method": ph["method"],
                               "saturated": ph["saturated"], "npix": ph["npix"], "chi2": ph["chi2"],
                               "psf_weight": ph["psf_weight"], "flux_ap_mjy": ph["flux_ap_mjy"],
                               "err_ap_mjy": ph["err_ap_mjy"], "relative": relative,
                               "ring_r_in": ph.get("ring_r_in"), "ring_r_out": ph.get("ring_r_out")})

    track = []
    for e in epochs:
        ra_e, dec_e = target.position_at(e["mjd_mean"])
        x, y = grid.world_to_pixel(ra_e, dec_e)
        track.append({"epoch": e["index"], "ra": float(ra_e), "dec": float(dec_e), "x": float(x), "y": float(y)})

    def spec(name: str, file: str, shape: list[int], **extra) -> dict:
        return {"file": file, "dtype": DTYPES[name][1], "shape": shape, **extra}

    cube = [n_ep, len(BANDS), n, n]
    meta = {
        "id": p.key(), "version": ALGO_VERSION,
        "created": utc_now_iso(),
        "params": asdict(p),
        "target": {**target.to_dict(), "track": track},
        "grid": grid.to_dict(),
        "bands": [{"id": b, **BAND_INFO[b]} for b in BANDS],
        "epochs": epochs,
        "arrays": {
            "coadds": spec("coadds", ARRAY_FILES["coadds"], cube, unit="MJy/sr (background subtracted)"),
            "coverage": spec("coverage", ARRAY_FILES["coverage"], cube),
            "flags": spec("flags", ARRAY_FILES["flags"], cube),
            "exposures": spec("exposures", EXPOSURES_FILE, [len(ok), n, n]) if keep_exposures else None,
        },
        "exposures": exposures,
        "photometry": photometry,
        "flag_bits": flag_bits_table(flag_defs),
        "processing": {"min_epoch_frames": MIN_EPOCH_FRAMES, "merge_gap_days": MERGE_GAP_DAYS,
                       "photometry": "ePSF fit (fallback: aperture); detectors where the target saturates use "
                                     "a ring outside the saturated core (relative fluxes)",
                       "psf_fit_radius_px": PSF_FIT_RADIUS, "systematic_floor": SYSTEMATIC_FLOOR,
                       "max_masked_fraction": MAX_MASKED_FRACTION, "rings": rings},
        "stats": {"frames_found": len(frames), "frames_used": len(ok), "errors": errors[:20]},
        "sources": {"archive": "NASA/IPAC IRSA", "collections": list(p.collections or settings.collections),
                    "data": settings.s3_base},
    }
    return meta, arrays
