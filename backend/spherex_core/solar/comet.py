"""Comet analysis of a co-moving sequence (ADR-0060).

Works on the static-sky subtracted exposures when the sequence has them (static_sky build), otherwise on the plain
aligned exposures with a local background (stars then bias the apertures; the result says which was used).

* Photometry in apertures of fixed projected radius rho (km): theta = rho / (725.27 km/arcsec/au x Delta), Delta
  being the SPHEREx-centric distance of every exposure (geometry.py). Masked pixels are filled with the mean of
  their ring about the nucleus. Apertures smaller than half the PSF FWHM are flagged psf_limited.
* Radial profile of every epoch's clean co-add per detector, its log-log slope (a steady-state coma has
  surface brightness ~ rho^-1, a point source falls much faster) and a band image (window minus sideband
  exposures) for each gas band, whose profile shows the extent of the gas coma.
* Afrho (A'Hearn et al. 1984) in reflected light (lambda <= 2.5 um): (2 Delta r_h)^2 / rho x F_comet / F_sun, Delta
  and rho in cm, r_h in au, F_sun at 1 au through the same channel (sun.py); A(0)frho with the Halley-Marcus
  composite phase function (quadratic fit log10 Phi = -0.01807 alpha + 0.000177 alpha^2).
* Bands: a weighted linear continuum through sidebands, then for emission bands the integrated line flux from a
  least-squares fit of each channel's response to a band at the nominal centre (Gaussian channel of FWHM
  dlambda convolved with the band width), and for absorption bands the depth 1 - F/C. Errors and significance
  from Monte Carlo draws of the measured fluxes. Emission band fluxes become production-rate proxies with a
  fluorescence efficiency g (at 1 au, scaled r_h^-2) and the Haser approximation for rho much smaller than the
  scale length: N = 4 pi Delta^2 L / (g h nu), Q = 2 v N / (pi rho), v = 0.85 r_h^-0.5 km/s.
* Activity: per epoch (visit) r_h, Delta, phase, Afrho per detector, band fluxes and Q proxies; a power law
  Afrho ~ r_h^k when the visits span more than 5 % in r_h.

Everything here is a quick-look proxy, not a calibrated production rate: see `caveats` in the result.
"""

from __future__ import annotations

import math
import warnings
from dataclasses import dataclass

import numpy as np

from ..analysis.common import SequenceContext
from ..bands import BANDS
from ..errors import NotFound
from ..pipeline import results
from . import sun
from .common import ObjectFrames, aperture_flux, load_frames, radial_profile, require_comoving, weighted_mean
from .geometry import KM_PER_ARCSEC_AU, exposure_geometry, km_to_arcsec

COMET_VERSION = 1
DEFAULT_RHO_KM = (10_000.0, 20_000.0, 50_000.0, 100_000.0)
REFLECTED_MAX_UM = 2.5
AU_CM = 1.495978707e13
AU_M = 1.495978707e11
H_PLANCK = 6.62607015e-34
C_M_S = 2.99792458e8
MC_DRAWS = 400

# window and sidebands in um; width = intrinsic band FWHM (um) for the channel response; g at 1 au (s^-1).
FEATURES: list[dict] = [
    {"id": "h2o_ice_1.5", "label": "H2O ice 1.5 um", "kind": "absorption", "window": (1.45, 1.60),
     "side": ((1.30, 1.42), (1.68, 1.80))},
    {"id": "h2o_ice_2.0", "label": "H2O ice 2.0 um", "kind": "absorption", "window": (1.93, 2.10),
     "side": ((1.72, 1.85), (2.20, 2.32))},
    {"id": "h2o_gas", "label": "H2O gas 2.7 um (nu1+nu3 fluorescence)", "kind": "emission", "center": 2.69,
     "width": 0.08, "window": (2.62, 2.78), "side": ((2.42, 2.56), (3.60, 3.75)), "g1au": 2.8e-4,
     "ref": "Crovisier & Encrenaz 1983"},
    {"id": "h2o_ice_3.0", "label": "H2O ice 3.0 um", "kind": "absorption", "window": (2.85, 3.25),
     "side": ((2.42, 2.56), (3.60, 3.75))},
    {"id": "organics", "label": "C-H stretch 3.3-3.5 um (organics, CH3OH)", "kind": "emission", "center": 3.42,
     "width": 0.15, "window": (3.32, 3.55), "side": ((3.18, 3.27), (3.60, 3.75))},
    {"id": "co2", "label": "CO2 nu3 4.26 um", "kind": "emission", "center": 4.26, "width": 0.05,
     "window": (4.20, 4.32), "side": ((3.95, 4.15), (4.40, 4.55)), "g1au": 2.86e-3,
     "ref": "Crovisier & Encrenaz 1983"},
    {"id": "co", "label": "CO 1-0 4.67 um", "kind": "emission", "center": 4.67, "width": 0.10,
     "window": (4.59, 4.75), "side": ((4.42, 4.55), (4.80, 4.98)), "g1au": 2.6e-4, "ref": "Debout et al. 2016"},
]
CAVEATS = [
    "Production rates are order-of-magnitude proxies: fixed fluorescence efficiencies, a Haser 1/rho coma with "
    "v = 0.85 r_h^-0.5 km/s, no opacity, and SPHEREx channels (R ~ 40-130) that blend each band with its "
    "neighbours and the continuum.",
    "Afrho assumes all light below 2.5 um is scattered sunlight; thermal emission is ignored there.",
    "Apertures smaller than half the PSF FWHM (psf_limited) measure the PSF, not the coma.",
    "Static-sky templates come from other epochs: a coma larger than ~0.3 of the field biases the clean images "
    "low at large radii (the outer region sets the zero level); build a larger field for big comae.",
    "Ice absorption depths depend on the continuum sidebands; dust colour and thermal emission can mimic them.",
]


@dataclass
class CometOptions:
    rho_km: tuple[float, ...] = DEFAULT_RHO_KM
    use_clean: bool = True

    def to_dict(self) -> dict:
        return {"rho_km": [float(r) for r in self.rho_km], "use_clean": self.use_clean, "v": COMET_VERSION}

    @classmethod
    def from_dict(cls, d: dict | None) -> "CometOptions":
        d = d or {}
        rho = tuple(float(r) for r in (d.get("rho_km") or DEFAULT_RHO_KM))
        if not rho or len(rho) > 8 or min(rho) <= 0 or max(rho) > 5e6:
            raise ValueError("rho_km: 1-8 radii between 0 and 5e6 km")
        return cls(rho_km=tuple(sorted(rho)), use_clean=bool(d.get("use_clean", True)))


# ---------------------------------------------------------------- pure helpers (unit-tested)
def afrho_cm(flux_mjy, sun_mjy, r_au, delta_au, rho_km):
    """A'Hearn Afrho in cm from the comet flux and the solar flux at 1 au through the same channel."""
    return (2.0 * delta_au * AU_CM * r_au) ** 2 / (rho_km * 1e5) * (flux_mjy / sun_mjy)


def phase_factor(alpha_deg):
    """Halley-Marcus composite dust phase function normalised to 1 at alpha = 0 (quadratic fit in log10)."""
    a = np.asarray(alpha_deg, float)
    return 10 ** (-0.01807 * a + 0.000177 * a ** 2)


def _line_response(wl, dwl, center, width):
    """Mean F_nu (mJy) in each channel per unit integrated band flux (mJy Hz): Gaussian channel of FWHM dwl
    convolved with a Gaussian band of FWHM width."""
    fw = np.sqrt(np.asarray(dwl, float) ** 2 + width ** 2)
    dnu = C_M_S * 1e6 * fw / np.asarray(wl, float) ** 2  # Hz
    return 0.9394372787 / dnu * np.exp(-4 * math.log(2) * ((center - np.asarray(wl, float)) / fw) ** 2)


def _fit_continuum(wl, f, e, center):
    """Weighted straight line through sideband points: returns (coef, design function)."""
    x = np.asarray(wl, float) - center
    w = 1.0 / np.asarray(e, float) ** 2
    A = np.stack([np.ones_like(x), x], axis=1)
    coef = np.linalg.lstsq(A * np.sqrt(w)[:, None], np.asarray(f, float) * np.sqrt(w), rcond=None)[0]
    return coef


def band_fit(wl, dwl, flux, err, feature: dict, rng: np.random.Generator | None = None) -> dict:
    """Fit one feature to a spectrum given per channel (wl, dwl, flux, err in mJy). See the module docstring."""
    wl, dwl, flux, err = (np.asarray(a, float) for a in (wl, dwl, flux, err))
    ok = np.isfinite(wl) & np.isfinite(flux) & np.isfinite(err) & (err > 0)
    lo, hi = feature["window"]
    win = ok & (wl >= lo) & (wl <= hi)
    sides = [ok & (wl >= a) & (wl <= b) for a, b in feature["side"]]
    out = {"id": feature["id"], "label": feature["label"], "kind": feature["kind"], "n_window": int(win.sum()),
           "n_side": [int(s.sum()) for s in sides], "value": None, "err": None, "snr": None, "detected": False}
    if win.sum() < 1 or min(s.sum() for s in sides) < 1 or sum(s.sum() for s in sides) < 3:
        out["note"] = "not enough channels in the window or sidebands"
        return out
    side = sides[0] | sides[1]
    centre = feature.get("center", 0.5 * (lo + hi))
    rng = rng or np.random.default_rng(0)

    def estimate(fs: np.ndarray) -> tuple[float, float]:
        coef = _fit_continuum(wl[side], fs[side], err[side], centre)
        cont = coef[0] + coef[1] * (wl[win] - centre)
        if feature["kind"] == "emission":
            m = _line_response(wl[win], dwl[win], centre, feature.get("width", 0.0))
            w = 1.0 / err[win] ** 2
            return float(np.sum(w * m * (fs[win] - cont)) / np.sum(w * m * m)), float(coef[0])
        w = 1.0 / err[win] ** 2
        return float(1.0 - np.sum(w * fs[win]) / np.sum(w * cont)), float(coef[0])

    value, cont0 = estimate(flux)
    draws = np.array([estimate(flux + rng.normal(0.0, err))[0] for _ in range(MC_DRAWS)])
    sd = float(np.std(draws))
    out.update(value=value, err=sd, snr=value / sd if sd > 0 else None, continuum_mjy=cont0,
               detected=bool(sd > 0 and value / sd >= 3.0))
    if feature["kind"] == "emission":
        out["unit"] = "W m-2"
        out["value_w_m2"], out["err_w_m2"] = value * 1e-29, sd * 1e-29
    else:
        out["unit"] = "fractional depth"
    return out


def production_proxy(line_w_m2: float, feature: dict, r_au: float, delta_au: float, rho_km: float) -> float | None:
    """Molecules/s from an integrated band flux (W m-2): N = 4 pi Delta^2 L / (g h nu), Q = 2 v N / (pi rho)."""
    g1 = feature.get("g1au")
    if not g1 or not np.isfinite(line_w_m2) or line_w_m2 <= 0:
        return None
    g = g1 / r_au ** 2
    h_nu = H_PLANCK * C_M_S / (feature["center"] * 1e-6)
    n_mol = 4 * math.pi * (delta_au * AU_M) ** 2 * line_w_m2 / (g * h_nu)
    v = 0.85 / math.sqrt(r_au)  # km/s
    return 2.0 * v * n_mol / (math.pi * rho_km)


def log_slope(r, sb, err, r1, r2) -> dict | None:
    """Weighted log-log slope of a surface-brightness profile between radii r1 and r2 (positive values only)."""
    r, sb, err = (np.asarray(a, float) for a in (r, sb, err))
    sel = (r >= r1) & (r <= r2) & np.isfinite(sb) & (sb > 0) & np.isfinite(err) & (err > 0)
    if sel.sum() < 3:
        return None
    x, y = np.log10(r[sel]), np.log10(sb[sel])
    w = (sb[sel] / err[sel]) ** 2  # 1/sigma^2 of log10(sb), up to a constant
    A = np.stack([np.ones_like(x), x], axis=1)
    cov = np.linalg.pinv(A.T @ (A * w[:, None]))
    coef = cov @ (A.T @ (w * y))
    resid = y - A @ coef
    chi2 = float(np.sum(w * resid ** 2) / max(1, sel.sum() - 2))
    return {"slope": float(coef[1]), "err": float(math.sqrt(cov[1, 1] * max(chi2, 1.0))), "n": int(sel.sum()),
            "r_min_px": float(r1), "r_max_px": float(r2)}


# ---------------------------------------------------------------- the analysis
def _spectrum_rows(exps, geom, frames: ObjectFrames, rho_km, pixscale, check) -> list[dict]:
    rows = []
    n = frames.images.shape[1]
    c = (n - 1) / 2.0
    for i, e in enumerate(exps):
        if i % 50 == 0:
            check()
        g = geom[i]
        if not frames.usable[i] or not g.get("delta_au"):
            continue
        fwhm_px = float(e.get("psf_fwhm") or 1.5 * pixscale) / pixscale
        row = {"index": i, "epoch": e["epoch"], "band": e["band"], "mjd": e["mjd"], "wl": e["wl"], "dwl": e["dwl"],
               "r_au": g["r_au"], "delta_au": g["delta_au"], "phase_deg": g["phase_deg"], "ap": []}
        for rho in rho_km:
            theta = float(km_to_arcsec(rho, g["delta_au"]))
            r_px = theta / pixscale
            m = aperture_flux(frames.images[i], c, c, r_px, pixscale, frames.noise[i])
            ap = {"rho_km": rho, "theta_arcsec": theta, "psf_limited": bool(r_px < 0.5 * fwhm_px)}
            if m is not None:
                ap.update(m)
            row["ap"].append(ap)
        rows.append(row)
    return rows


def _activity(rows, rho_km, sun_f) -> tuple[list[dict], dict]:
    """Afrho per exposure (reflected light) and per epoch/detector."""
    per_exp = []
    for row in rows:
        if row["wl"] > REFLECTED_MAX_UM or not row["r_au"]:
            continue
        s = sun_f[row["index"]]
        for ap in row["ap"]:
            if ap.get("flux_mjy") is None or ap["psf_limited"]:
                continue
            a = afrho_cm(ap["flux_mjy"], s, row["r_au"], row["delta_au"], ap["rho_km"])
            ae = afrho_cm(ap["err_mjy"], s, row["r_au"], row["delta_au"], ap["rho_km"])
            per_exp.append({"index": row["index"], "epoch": row["epoch"], "band": row["band"], "wl": row["wl"],
                            "rho_km": ap["rho_km"], "afrho_cm": a, "err_cm": ae,
                            "a0frho_cm": a / float(phase_factor(row["phase_deg"] or 0.0)),
                            "a0err_cm": ae / float(phase_factor(row["phase_deg"] or 0.0))})
    summary = {}
    for rho in rho_km:
        sel = [p for p in per_exp if p["rho_km"] == rho]
        if sel:
            v, e = weighted_mean([p["afrho_cm"] for p in sel], [p["err_cm"] for p in sel])
            summary[str(int(rho))] = {"afrho_cm": v, "err_cm": e, "n": len(sel)}
    return per_exp, summary


def _dust_colour(rows, sun_f, rho_km) -> dict | None:
    """Reflectance slope (% per 100 nm, normalised at 1.25 um) of the coma continuum below 2.5 um."""
    rho = rho_km
    wl, rf, er = [], [], []
    for row in rows:
        if row["wl"] > REFLECTED_MAX_UM:
            continue
        ap = next((a for a in row["ap"] if a["rho_km"] == rho and a.get("flux_mjy") is not None), None)
        if ap is None:
            continue
        s = sun_f[row["index"]]
        wl.append(row["wl"])
        rf.append(ap["flux_mjy"] / s)
        er.append(ap["err_mjy"] / s)
    if len(wl) < 5:
        return None
    wl, rf, er = map(np.asarray, (wl, rf, er))
    w = 1 / er ** 2
    A = np.stack([np.ones_like(wl), wl - 1.25], axis=1)
    cov = np.linalg.pinv(A.T @ (A * w[:, None]))
    coef = cov @ (A.T @ (w * rf))
    if coef[0] <= 0:
        return None
    slope = coef[1] / coef[0] * 10.0  # per um -> % per 100 nm (x100 / 10)
    return {"rho_km": rho, "slope_pct_per_100nm": float(slope),
            "err": float(math.sqrt(cov[1, 1]) / coef[0] * 10.0), "n": int(len(wl)), "norm_um": 1.25}


def _band_images(meta, frames: ObjectFrames, epoch: int) -> dict:
    """Per emission band: mean of window exposures minus the mean of sideband exposures (MJy/sr), as a profile."""
    exps = meta["exposures"]
    n = frames.images.shape[1]
    c = (n - 1) / 2.0
    out = {}
    for ft in FEATURES:
        if ft["kind"] != "emission":
            continue
        lo, hi = ft["window"]
        wi = [i for i, e in enumerate(exps) if e["epoch"] == epoch and frames.usable[i] and lo <= e["wl"] <= hi]
        si = [i for i, e in enumerate(exps) if e["epoch"] == epoch and frames.usable[i]
              and any(a <= e["wl"] <= b for a, b in ft["side"])]
        if len(wi) < 1 or len(si) < 2:
            continue
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=RuntimeWarning)
            img = np.nanmean(frames.images[wi], axis=0) - np.nanmedian(frames.images[si], axis=0)
        prof = radial_profile(img, c, c, 0.95 * n / 2)
        out[ft["id"]] = {"n_window": len(wi), "n_side": len(si), "profile": prof}
    return out


def comet_analysis(ctx: SequenceContext, opts: CometOptions, progress=None, check=None) -> dict:
    progress = progress or (lambda stage, done, total: None)
    check = check or (lambda: None)
    require_comoving(ctx)
    meta = ctx.meta
    exps = meta["exposures"]
    pixscale = float(ctx.grid.pixscale)
    progress("geometry", 0, 1)
    geom = exposure_geometry(meta)
    if not any(g.get("delta_au") for g in geom):
        raise NotFound("no distances for this object (MOST and Horizons unavailable)")
    progress("load", 0, 1)
    frames = load_frames(ctx, prefer_clean=opts.use_clean)
    check()
    progress("photometry", 0, len(exps))
    rows = _spectrum_rows(exps, geom, frames, opts.rho_km, pixscale, check)
    sun_f = {row["index"]: float(sun.sun_fnu(row["wl"], row["dwl"])) for row in rows}
    afrho_exp, afrho_all = _activity(rows, opts.rho_km, sun_f)

    progress("bands", 0, len(meta["epochs"]))
    rng = np.random.default_rng(0)
    epochs_out = []
    for ep in meta["epochs"]:
        check()
        e_idx = ep["index"]
        er = [r for r in rows if r["epoch"] == e_idx]
        if not er:
            continue
        geo = {k: float(np.nanmedian([r[k] for r in er if r[k] is not None])) for k in ("r_au", "delta_au", "phase_deg")}
        bands_by_rho = {}
        for k, rho in enumerate(opts.rho_km):
            pts = [(r["wl"], r["dwl"], r["ap"][k].get("flux_mjy"), r["ap"][k].get("err_mjy")) for r in er
                   if r["ap"][k].get("flux_mjy") is not None]
            if len(pts) < 5:
                continue
            wl, dwl, f, e = (np.array(x, float) for x in zip(*pts))
            fits = []
            for ft in FEATURES:
                bf = band_fit(wl, dwl, f, e, ft, rng)
                if bf["kind"] == "emission" and bf["value"] is not None and ft.get("g1au"):
                    q = production_proxy(bf["value_w_m2"], ft, geo["r_au"], geo["delta_au"], rho)
                    qe = production_proxy(bf["err_w_m2"], ft, geo["r_au"], geo["delta_au"], rho)
                    bf.update(q_proxy=q, q_err=qe, molecule=ft["id"].split("_")[0].upper())
                    if not bf["detected"]:  # 3 sigma above the measured value (never below 3 sigma)
                        ul = max(bf["value_w_m2"], 0.0) + 3.0 * bf["err_w_m2"]
                        bf["q_upper_3sigma"] = production_proxy(ul, ft, geo["r_au"], geo["delta_au"], rho)
                fits.append(bf)
            bands_by_rho[str(int(rho))] = fits
        af = [p for p in afrho_exp if p["epoch"] == e_idx]
        afrho = {}
        for rho in opts.rho_km:
            for b in BANDS[:3]:
                sel = [p for p in af if p["rho_km"] == rho and p["band"] == b]
                if sel:
                    v, s = weighted_mean([p["afrho_cm"] for p in sel], [p["err_cm"] for p in sel])
                    v0, s0 = weighted_mean([p["a0frho_cm"] for p in sel], [p["a0err_cm"] for p in sel])
                    afrho.setdefault(str(int(rho)), {})[b] = {"afrho_cm": v, "err_cm": s, "a0frho_cm": v0,
                                                             "a0err_cm": s0, "n": len(sel)}
        epochs_out.append({"epoch": e_idx, "date_min": ep["date_min"], "date_max": ep["date_max"],
                           "mjd_mean": ep["mjd_mean"], **geo, "n_exposures": len(er),
                           "afrho": afrho, "bands": bands_by_rho,
                           "dust_colour": _dust_colour(er, sun_f, opts.rho_km[min(1, len(opts.rho_km) - 1)]),
                           "band_images": _band_images(meta, frames, e_idx)})
        progress("bands", len(epochs_out), len(meta["epochs"]))

    profiles = _coadd_profiles(ctx, geom, opts.use_clean and frames.clean)
    trend = _afrho_trend(epochs_out, opts.rho_km)
    return {
        "kind": "comet", "version": COMET_VERSION, "sequence": ctx.seq_id, "object": (meta.get("moving") or {}).get("object"),
        "options": opts.to_dict(), "static_sky": frames.clean,
        "image_source": "clean_exposures (static sky subtracted)" if frames.clean else
                        "exposures (stars not removed; rebuild with static_sky for clean apertures)",
        "geometry_source": sorted({g["source"] for g in geom if g.get("source")}),
        "solar_spectrum": sun.source(), "km_per_arcsec_per_au": KM_PER_ARCSEC_AU,
        "features": [{k: v for k, v in f.items()} for f in FEATURES],
        "spectrum": rows, "afrho_exposures": afrho_exp, "afrho_summary": afrho_all,
        "epochs": epochs_out, "profiles": profiles, "activity_trend": trend, "caveats": CAVEATS,
    }


def _coadd_profiles(ctx: SequenceContext, geom, clean: bool) -> list[dict]:
    """Radial profile and slope of every epoch/detector co-add (clean when available)."""
    meta = ctx.meta
    name = "clean" if clean and (meta.get("arrays") or {}).get("clean") else "coadds"
    try:
        cube = results.read_array(ctx.dir, meta, name)
    except NotFound:
        return []
    n = cube.shape[-1]
    c = (n - 1) / 2.0
    pixscale = float(ctx.grid.pixscale)
    out = []
    for ep in meta["epochs"]:
        e_idx = ep["index"]
        deltas = [geom[i]["delta_au"] for i, e in enumerate(meta["exposures"])
                  if e["epoch"] == e_idx and geom[i].get("delta_au")]
        delta = float(np.median(deltas)) if deltas else None
        fw = [float(e.get("psf_fwhm") or 0) for e in meta["exposures"] if e["epoch"] == e_idx]
        fwhm_px = (float(np.median(fw)) if fw else 1.5 * pixscale) / pixscale
        for bi, b in enumerate(BANDS):
            img = cube[e_idx, bi]
            if not np.isfinite(img).any():
                continue
            if name == "coadds":  # no static sky: subtract a robust outer level
                c_out = img[np.hypot(*np.mgrid[0:n, 0:n] - c) > 0.8 * n / 2]
                img = img - np.nanmedian(c_out)
            prof = radial_profile(img, c, c, 0.95 * n / 2)
            sl = log_slope(prof["r_px"], prof["sb"], prof["err"], max(2.0 * fwhm_px, 2.0), 0.6 * n / 2)
            out.append({"epoch": e_idx, "band": b, "source": name, "fwhm_px": fwhm_px, "delta_au": delta,
                        "km_per_px": pixscale * KM_PER_ARCSEC_AU * delta if delta else None,
                        "profile": prof, "slope": sl})
    return out


def _afrho_trend(epochs_out: list[dict], rho_km) -> dict | None:
    """Afrho ~ r_h^k from the D1 (or first available) detector at the second aperture, when visits span > 5 %."""
    if len(epochs_out) < 2:
        return None
    rho = str(int(rho_km[min(1, len(rho_km) - 1)]))
    pts = []
    for e in epochs_out:
        a = (e["afrho"].get(rho) or {})
        b = next((a[k] for k in BANDS[:3] if k in a), None)
        if b and b["a0frho_cm"] and b["a0frho_cm"] > 0:
            pts.append((e["r_au"], b["a0frho_cm"], b["a0err_cm"]))
    if len(pts) < 2:
        return None
    r, a, s = map(np.asarray, zip(*pts))
    if r.max() / r.min() < 1.05:
        return {"points": len(pts), "note": "visits span less than 5 % in r_h"}
    x, y = np.log10(r), np.log10(a)
    w = (a / np.maximum(s, 1e-30)) ** 2
    A = np.stack([np.ones_like(x), x], axis=1)
    cov = np.linalg.pinv(A.T @ (A * w[:, None]))
    coef = cov @ (A.T @ (w * y))
    return {"rho_km": float(rho), "k": float(coef[1]), "err": float(math.sqrt(cov[1, 1])), "points": len(pts),
            "r_au": r.tolist(), "a0frho_cm": a.tolist()}
