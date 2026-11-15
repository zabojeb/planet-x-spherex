"""SPHEREx on the archival bands: synthetic J/H/Ks/W1/W2 per time bin, and SPHEREx-only narrow bands.

Time bins are the sequence's epochs, split into chunks of at most BIN_DAYS (deep fields are observed almost
continuously, and a Mira changes noticeably within a 45-day epoch).

Calibrated photometry (ePSF / aperture): each bin's exposures are binned into a spectrum (spectral.assemble)
and integrated over the filter's relative response per photon, <F_nu> = int F_nu R / lambda / int R / lambda
(spectral.synphot convention: log-linear interpolation, power law past the ends, so W2 reaches 5.3 um). The
error is propagated through the integral (d ln F / d ln F_k per bin); the bin needs measured spectrum over
MIN_COVERAGE of the band's photon weight.

Ring photometry (saturated stars): there is no absolute spectrum, so a band is the response-weighted mean of
the relative fluxes (each exposure over the median of its wavelength bin) inside the band: a relative light
curve whose scale is fitted against the other surveys later (`relative: true`).

Narrow bands: per detector D1-D6, the bin's spectrum over the all-epoch spectrum (inverse-variance mean of
bin ratios), times the detector's mean flux, so a missing channel does not change the brightness.
"""

from __future__ import annotations

import asyncio
import math
import time
from collections import defaultdict

import numpy as np

from .. import cache
from ..analysis.lightcurve import relative_fluxes
from ..archival.build import visits
from ..bands import BAND_INFO, BANDS
from ..spectral.assemble import arrays, bin_spectrum
from ..spectral.synphot import FILTERS, curve
from ..targets import Target
from ..timeutil import mjd_to_iso, mjd_to_year
from .instruments import VEGA_F0_JY

BIN_DAYS = 15.0
MIN_COVERAGE = 0.75
NEAR_UM = 0.06  # the response within this distance of a measured bin counts as covered
SYNTH_FLOOR = 0.02  # fractional systematic per synthetic point (channel-to-channel calibration)
NARROW_FLOOR = 0.02
SYNTH_BANDS = ("J", "H", "Ks", "W1", "W2")


# ---------------------------------------------------------------- band integration
def _integrate(lw: np.ndarray, w_r: np.ndarray, x: np.ndarray, logy: np.ndarray) -> float:
    logf = np.interp(lw, x, logy)
    for edge, sel in ((x.max(), lw > x.max()), (x.min(), lw < x.min())):
        if sel.any():
            near = np.argsort(np.abs(x - edge))[:3]
            if near.size >= 2 and np.ptp(np.log(x[near])) > 0:
                k, c = np.polyfit(np.log(x[near]), logy[near], 1)
                logf[sel] = k * np.log(lw[sel]) + c
    return float((np.exp(logf) * w_r).sum() / w_r.sum())


def band_flux(wl: np.ndarray, f: np.ndarray, e: np.ndarray, band: str,
              response: tuple[np.ndarray, np.ndarray] | None = None) -> dict | None:
    """Synthetic flux density (mJy) of a binned spectrum in a 2MASS/WISE band, with its propagated error and
    the covered share of the band's photon weight. None when there are too few bins."""
    lw, resp = response if response is not None else curve(band)
    ok = np.isfinite(f) & np.isfinite(e) & (f > 0) & (e > 0)
    if ok.sum() < 3:
        return None
    order = np.argsort(wl[ok])
    x, y, s = wl[ok][order], f[ok][order], e[ok][order]
    w_r = resp / lw
    dist = np.min(np.abs(lw[:, None] - x[None, :]), axis=1)
    coverage = float((w_r * (dist <= NEAR_UM)).sum() / w_r.sum())
    if coverage < MIN_COVERAGE:
        return {"coverage": coverage, "flux_mjy": None}
    logy = np.log(y)
    flux = _integrate(lw, w_r, x, logy)
    # d ln F / d ln F_k for the bins that matter (inside the band's support or next to it)
    lo, hi = lw[resp > 0.01 * resp.max()].min(), lw[resp > 0.01 * resp.max()].max()
    rel_var = 0.0
    idx = np.nonzero((x >= lo - 0.3) & (x <= hi + 0.3))[0]
    for k in idx:
        d = logy.copy()
        d[k] += 1e-3
        jac = (math.log(_integrate(lw, w_r, x, d)) - math.log(flux)) / 1e-3
        rel_var += (jac * s[k] / y[k]) ** 2
    rel = math.sqrt(rel_var)
    err = flux * math.hypot(rel, SYNTH_FLOOR)
    return {"coverage": coverage, "flux_mjy": flux, "err_mjy": err, "stat_rel_err": rel,
            "extrapolated": bool(hi > x.max() or lo < x.min())}


def band_weights(band: str, wl: np.ndarray) -> np.ndarray:
    """Photon-weighted response of a band at wavelengths wl (0 outside)."""
    lw, resp = curve(band)
    return np.interp(wl, lw, resp / lw, left=0.0, right=0.0)


# ---------------------------------------------------------------- time bins
def time_bins(records: list[dict], bin_days: float = BIN_DAYS) -> dict[tuple[int, int], list[dict]]:
    """Records grouped by (epoch, chunk): each sequence epoch split where it spans more than bin_days."""
    by_epoch: dict[int, list[dict]] = defaultdict(list)
    for r in records:
        if r.get("epoch") is not None and r.get("mjd") is not None:
            by_epoch[int(r["epoch"])].append(r)
    out: dict[tuple[int, int], list[dict]] = {}
    for e, recs in sorted(by_epoch.items()):
        idx = visits([r["mjd"] for r in recs], gap=bin_days / 3.0, max_len=bin_days)
        for r, c in zip(recs, idx):
            out.setdefault((e, c), []).append(r)
    return out


def _point(band: str, recs: list[dict], **kw) -> dict:
    t = np.array([r["mjd"] for r in recs])
    mjd = float(t.mean())
    return {"survey": "SPHEREx", "survey_id": "spherex", "band": band, "mjd": mjd, "mjd_min": float(t.min()),
            "mjd_max": float(t.max()), "year": float(mjd_to_year(mjd)), "date": mjd_to_iso(mjd)[:10],
            "n": len(recs), **kw}


def _mag(flux: float | None, err: float | None, band: str) -> tuple[float | None, float | None, str]:
    if flux is None or not flux > 0:
        return None, None, "Vega" if band in VEGA_F0_JY else "AB"
    f0 = VEGA_F0_JY.get(band, 3631.0)
    return (float(-2.5 * math.log10(flux * 1e-3 / f0)), float(1.0857 * err / flux) if err else None,
            "Vega" if band in VEGA_F0_JY else "AB")


def is_relative(records: list[dict]) -> bool:
    """True when the target's usable SPHEREx photometry is mostly ring (relative) measurements."""
    use = [r for r in records if r.get("flux_mjy") is not None and not r.get("flagged")]
    return bool(use) and sum(bool(r.get("relative")) for r in use) > 0.5 * len(use)


def synthetic_points(records: list[dict], bands=SYNTH_BANDS,
                     bin_days: float = BIN_DAYS) -> tuple[list[dict], list[str]]:
    """Synthetic band points per time bin: calibrated fluxes, or relative ones for ring photometry."""
    notes: list[str] = []
    relative = is_relative(records)
    responses = {}
    for b in bands:
        try:
            responses[b] = curve(b)
        except Exception as e:  # noqa: BLE001 - SVO unreachable and not cached
            notes.append(f"{b}: filter curve unavailable ({str(e)[:80]})")
    out = []
    bins = time_bins(records, bin_days)
    if relative:
        rel = [p for p in relative_fluxes(records) if p.get("relative") and not p.get("flagged")]
        by_bin: dict[tuple[int, int], list[dict]] = defaultdict(list)
        bin_of = {(r.get("index"), r.get("mjd")): k for k, rs in bins.items() for r in rs}
        for p in rel:
            k = bin_of.get((p.get("index"), p.get("mjd")))
            if k is not None:
                by_bin[k].append(p)
        for b, (lw, resp) in responses.items():
            for k in sorted(by_bin):
                pts = by_bin[k]
                wl = np.array([p["wl"] for p in pts])
                w_r = np.interp(wl, lw, resp / lw, left=0.0, right=0.0)
                sel = w_r > 0.02 * (resp / lw).max()
                if sel.sum() < 3:
                    continue
                y = np.array([p["rel"] for p in pts])[sel]
                ey = np.array([p["rel_err"] or 0.05 for p in pts])[sel]
                w = w_r[sel] / np.maximum(ey, 1e-3) ** 2
                mean = float((w * y).sum() / w.sum())
                n = int(sel.sum())
                scatter = math.sqrt(float((w * (y - mean) ** 2).sum() / w.sum()) / max(n - 1, 1))
                err = max(math.sqrt(1.0 / w.sum()), scatter)
                mem = [p for p, s_ in zip(pts, sel) if s_]
                out.append(_point(b, mem, flux_mjy=None, err_mjy=None, rel=mean, rel_err=math.hypot(err, 0.03 * mean),
                                  mag=None, mag_err=None, mag_system="relative", method="synthetic-relative",
                                  relative=True, saturated=True, flagged=False, epoch=k[0]))
        return out, notes
    for k in sorted(bins):
        spec = bin_spectrum(bins[k])
        if len(spec) < 3:
            continue
        wl, f, e = arrays(spec)
        for b, resp in responses.items():
            r = band_flux(wl, f, e, b, resp)
            if not r or r.get("flux_mjy") is None:
                continue
            mag, mag_err, system = _mag(r["flux_mjy"], r["err_mjy"], b)
            out.append(_point(b, bins[k], flux_mjy=r["flux_mjy"], err_mjy=r["err_mjy"], mag=mag, mag_err=mag_err,
                              mag_system=system, coverage=r["coverage"], extrapolated=r["extrapolated"],
                              method="synthetic", relative=False, saturated=False, flagged=False, epoch=k[0]))
    return out, notes


def mean_synthetic(records: list[dict], bands=SYNTH_BANDS) -> dict[str, dict]:
    """Synthetic band fluxes of the all-epoch spectrum (constant sources, calibration stars)."""
    spec = bin_spectrum(records)
    wl, f, e = arrays(spec)
    out = {}
    for b in bands:
        try:
            r = band_flux(wl, f, e, b)
        except Exception:  # noqa: BLE001
            r = None
        if r and r.get("flux_mjy") is not None:
            mag, mag_err, _ = _mag(r["flux_mjy"], r["err_mjy"], b)
            out[b] = {**r, "mag": mag, "mag_err": mag_err}
    return out


def narrowband_points(records: list[dict], bin_days: float = BIN_DAYS) -> list[dict]:
    """Per-detector SPHEREx points per time bin (calibrated fluxes or relative values)."""
    relative = is_relative(records)
    bins = time_bins(records, bin_days)
    out = []
    if relative:
        rel = {(p.get("index"), p.get("mjd")): p for p in relative_fluxes(records)
               if p.get("relative") and not p.get("flagged")}
        for k in sorted(bins):
            for d in BANDS:
                pts = [rel[(r.get("index"), r.get("mjd"))] for r in bins[k] if r["band"] == d
                       and (r.get("index"), r.get("mjd")) in rel]
                if len(pts) < 2:
                    continue
                y = np.array([p["rel"] for p in pts])
                ey = np.array([p["rel_err"] or 0.05 for p in pts])
                w = 1.0 / np.maximum(ey, 1e-3) ** 2
                mean = float((w * y).sum() / w.sum())
                err = max(math.sqrt(1.0 / w.sum()), float(np.std(y, ddof=1) / math.sqrt(len(y))))
                out.append(_point(d, pts, flux_mjy=None, err_mjy=None, rel=mean,
                                  rel_err=math.hypot(err, NARROW_FLOOR * mean),
                                  wl_um=float(np.mean([p["wl"] for p in pts])),
                                  mag=None, mag_err=None, mag_system="relative", method="narrow-relative",
                                  relative=True, saturated=True, flagged=False, epoch=k[0]))
        return out
    overall = {b["bin"]: b for b in bin_spectrum(records)}
    for k in sorted(bins):
        spec = {b["bin"]: b for b in bin_spectrum(bins[k])}
        for d in BANDS:
            common = [i for i in spec if spec[i]["band"] == d and i in overall and overall[i]["flux_mjy"] > 0]
            if len(common) < 2:
                continue
            ratio = np.array([spec[i]["flux_mjy"] / overall[i]["flux_mjy"] for i in common])
            rerr = np.array([math.hypot(spec[i]["err_mjy"] / spec[i]["flux_mjy"] if spec[i]["flux_mjy"] > 0 else 1.0,
                                        overall[i]["err_mjy"] / overall[i]["flux_mjy"]) * abs(r_)
                             for i, r_ in zip(common, ratio)])
            w = 1.0 / np.maximum(rerr, 1e-4) ** 2
            r_mean = float((w * ratio).sum() / w.sum())
            r_err = max(math.sqrt(1.0 / w.sum()), float(np.std(ratio, ddof=1) / math.sqrt(len(ratio))))
            det = [b for b in overall.values() if b["band"] == d]
            level = float(np.mean([b["flux_mjy"] for b in det]))
            flux, err = r_mean * level, math.hypot(r_err, NARROW_FLOOR) * level
            mag, mag_err, system = _mag(flux, err, d)
            out.append(_point(d, [r for r in bins[k] if r["band"] == d] or bins[k], flux_mjy=flux, err_mjy=err,
                              wl_um=float(np.mean([b["wl"] for b in det])), mag=mag, mag_err=mag_err,
                              mag_system=system, method="narrow", relative=False, saturated=False, flagged=False,
                              epoch=k[0]))
    return out


def detector_ranges() -> dict[str, list[float]]:
    return {b: [BAND_INFO[b]["wl_min"], BAND_INFO[b]["wl_max"]] for b in BANDS}


# ---------------------------------------------------------------- many positions on the cached cutouts
def _measure_many(cut_path: str, wave_path: str, epsf_path: str, band: str, mjd: float,
                  targets: list[Target]) -> list[dict | None] | None:
    """remeasure.measure_exposure for several targets on one cached cutout (pool worker: load it once)."""
    from astropy.io import fits

    from ..analysis.remeasure import _table, measure_exposure
    from ..io import Cutout, slim_header

    try:
        with np.load(cut_path, allow_pickle=False) as z:
            if int(z["x0"]) < 0:
                return [None] * len(targets)
            cut = Cutout("", slim_header(fits.Header.fromstring(str(z["header"]))), int(z["x0"]), int(z["y0"]),
                         z["image"], z["variance"], z["flags"])
    except Exception:  # noqa: BLE001 - missing or damaged cache entry
        return None
    wave, epsf = _table(wave_path, "wave"), _table(epsf_path, "epsf")
    out: list[dict | None] = []
    for t in targets:
        ra_t, dec_t = t.position_at(mjd)
        xt, yt = cut.wcs.all_world2pix(ra_t, dec_t, 0, quiet=True)
        xt, yt = float(xt), float(yt)
        if not (cut.x0 <= xt < cut.x0 + cut.image.shape[1] and cut.y0 <= yt < cut.y0 + cut.image.shape[0]):
            out.append(None)
            continue
        psf_os, _ = epsf.nearest(xt, yt)
        out.append(measure_exposure(cut, band, mjd, t, wave, psf_os, epsf.oversampling))
    return out


async def measure_positions(ctx, targets: list[Target], check=None) -> tuple[list[list[dict]], dict]:
    """SPHEREx photometry records (meta.json shape) of several positions, from the sequence's cached cutouts
    only (no downloads; exposures whose cutout is not cached are skipped and counted)."""
    from ..analysis.common import cutout_half, sequence_frames
    from ..analysis.remeasure import _record
    from ..pipeline.assemble import apply_rings
    from ..pipeline.sequence import cpu_pool

    t0 = time.perf_counter()
    exposures = ctx.meta["exposures"]
    frames = await asyncio.to_thread(sequence_frames, ctx)
    half = cutout_half(ctx.grid)
    layout = cache.layout()
    loop = asyncio.get_running_loop()
    pool = cpu_pool()
    per: list[list[dict | None] | None] = [None] * len(exposures)
    missing = 0

    async def one(i: int) -> None:
        nonlocal missing
        f = frames[i]
        if f is None:
            missing += 1
            return
        cpath = layout.cutout(f.s3_url, ctx.grid.ra, ctx.grid.dec, half)
        wpath, epath = layout.wave(f.detector), layout.epsf(f.detector)
        if not (cpath.exists() and wpath.exists() and epath.exists()):
            missing += 1
            return
        if check is not None:
            check()
        per[i] = await loop.run_in_executor(pool, _measure_many, str(cpath), str(wpath), str(epath),
                                            exposures[i]["band"], exposures[i]["mjd"], targets)

    sem = asyncio.Semaphore(256)

    async def guarded(i: int) -> None:
        async with sem:
            await one(i)

    await asyncio.gather(*(guarded(i) for i in range(len(exposures))))
    out: list[list[dict]] = []
    for j in range(len(targets)):
        ok = []
        for i, rs in enumerate(per):
            if rs is None or rs[j] is None:
                continue
            r = dict(rs[j])
            r["index"], r["epoch"] = i, exposures[i]["epoch"]
            ok.append(r)
        apply_rings(ok)
        recs = [_record(r["index"], r["epoch"], r) for r in ok if r.get("phot") is not None]
        recs.sort(key=lambda p: p["index"])
        out.append(recs)
    return out, {"exposures": len(exposures), "not_cached": missing, "seconds": time.perf_counter() - t0}


__all__ = ["BIN_DAYS", "FILTERS", "band_flux", "band_weights", "detector_ranges", "is_relative", "mean_synthetic",
           "measure_positions", "narrowband_points", "synthetic_points", "time_bins"]
