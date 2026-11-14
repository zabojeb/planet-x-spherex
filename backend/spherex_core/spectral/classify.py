"""Classify a binned SPHEREx spectrum.

* blackbody (+ near-IR extinction) grid fit over the whole spectrum and over the short-wavelength part;
  the short fit extrapolated to 3-5 um measures an infrared excess (discs, cold companions, blends);
* template chi^2 ranking against Pickles stars and SpeX dwarf standards (0.75-2.45 um, see templates.py),
  with relative probabilities and per-class sums;
* infrared spectral index alpha = dlog(lambda F_lambda)/dlog(lambda) over 2-5 um with the Lada classes
  (indicative only: the classical index uses 2-24 um);
* a short summary combining the above with the methane indices, which cover the 3.3 um band templates miss.

Fits add a 3 % systematic floor to the bin errors (calibration between detectors), otherwise bins built
from many exposures carry formal errors far below the calibration accuracy.
"""

from __future__ import annotations

import math

import numpy as np

from .assemble import arrays
from .templates import TemplateLibrary, clean_type, library

SYSTEMATIC = 0.03
HC_K = 14387.77  # um K
TEMPLATE_MAX_WL = 2.45
EXCESS_SHORT_MAX = 2.45

# Near-IR extinction law A_lambda / A_Ks (Indebetouw et al. 2005), log-log interpolated, extrapolated below J.
_EXT_WL = np.array([1.24, 1.66, 2.16, 3.6, 4.5, 5.0])
_EXT_RATIO = np.array([2.50, 1.55, 1.00, 0.56, 0.43, 0.40])


def ext_ratio(wl: np.ndarray) -> np.ndarray:
    lx, ly = np.log(_EXT_WL), np.log(_EXT_RATIO)
    x = np.log(wl)
    out = np.interp(x, lx, ly)
    slope = (ly[1] - ly[0]) / (lx[1] - lx[0])
    out = np.where(x < lx[0], ly[0] + slope * (x - lx[0]), out)
    return np.exp(out)


def planck_nu(wl: np.ndarray, t: np.ndarray) -> np.ndarray:
    """B_nu shape (arbitrary scale) for wavelengths [n] and temperatures [m] -> [m, n]."""
    x = HC_K / (wl[None, :] * t[:, None])
    with np.errstate(over="ignore"):
        return wl[None, :] ** -3 / np.expm1(np.clip(x, 1e-6, 700))


def _weights(f: np.ndarray, e: np.ndarray) -> np.ndarray:
    return 1.0 / (e ** 2 + (SYSTEMATIC * np.abs(f)) ** 2)


def _scale_chi2(models: np.ndarray, f: np.ndarray, w: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Best linear scale and chi^2 of each model row against f (NaN model entries ignored)."""
    m = np.where(np.isfinite(models), models, 0.0)
    ww = np.where(np.isfinite(models), w[None, :], 0.0)
    s = (ww * m * f).sum(1) / np.maximum((ww * m * m).sum(1), 1e-300)
    chi2 = (ww * (f - s[:, None] * m) ** 2).sum(1)
    return s, chi2


def fit_blackbody(wl, f, e, *, max_wl: float | None = None, extinction: bool = True) -> dict | None:
    sel = np.isfinite(f) & (e > 0) & (f > 0)
    if max_wl is not None:
        sel &= wl <= max_wl
    if sel.sum() < 5:
        return None
    wl, f, e = wl[sel], f[sel], e[sel]
    w = _weights(f, e)
    temps = np.geomspace(250, 40000, 160)
    aks = np.arange(0, 6.01, 0.1) if extinction else np.array([0.0])
    base = planck_nu(wl, temps)  # [T, n]
    att = 10 ** (-0.4 * aks[:, None] * ext_ratio(wl)[None, :])  # [A, n]
    models = (base[None, :, :] * att[:, None, :]).reshape(-1, wl.size)
    s, chi2 = _scale_chi2(models, f, w)
    k = int(np.argmin(chi2))
    ia, it = divmod(k, temps.size)
    dof = max(sel.sum() - (3 if extinction else 2), 1)
    # 1-sigma range of T from the chi^2 surface (errors inflated so the best fit has chi2_red = 1)
    scale = max(chi2[k] / dof, 1.0)
    grid = (chi2.reshape(aks.size, temps.size) - chi2[k]) / scale
    ok_t = temps[(grid <= 1.0).any(axis=0)]
    return {"teff_k": float(temps[it]), "teff_range_k": [float(ok_t.min()), float(ok_t.max())],
            "a_k_mag": float(aks[ia]), "chi2_red": float(chi2[k] / dof), "n_bins": int(sel.sum()),
            "max_wl_um": max_wl, "scale": float(s[k]),
            "_model": lambda x, T=temps[it], A=aks[ia], S=s[k]: S * planck_nu(np.asarray(x), np.array([T]))[0]
            * 10 ** (-0.4 * A * ext_ratio(np.asarray(x)))}


def fit_templates(wl, f, e, lib: TemplateLibrary, bins_idx: np.ndarray, top: int = 8) -> dict | None:
    """chi^2 of every template on the bins it covers below TEMPLATE_MAX_WL."""
    use = (wl <= TEMPLATE_MAX_WL) & (e > 0) & np.isfinite(f)
    if use.sum() < 8:
        return None
    models = lib.model[:, bins_idx[use]]
    f_u, w_u = f[use], _weights(f[use], e[use])
    cover = np.isfinite(models).sum(1)
    s, chi2 = _scale_chi2(models, f_u, w_u)
    valid = cover >= max(8, int(0.7 * use.sum()))
    if not valid.any():
        return None
    # Templates are compared on the bins they cover; both libraries span 0.75-2.45 um, so coverage differs by
    # at most a bin and chi^2 per degree of freedom ranks them fairly.
    chi2_red = np.where(valid, chi2 / np.maximum(cover - 1, 1), np.inf)
    best = int(np.argmin(chi2_red))
    inflate = max(chi2_red[best], 1.0)  # errors inflated so the best template has chi2_red = 1
    logp = np.where(valid, -0.5 * (chi2_red - chi2_red[best]) * cover[best] / inflate, -np.inf)
    p = np.exp(logp - logp[best])
    p /= p.sum()
    order = np.argsort(-p)
    ranked = [{"name": lib.names[i], "sptype": lib.sptype[i], "family": lib.family[i], "source": lib.source[i],
               "chi2_red": float(chi2_red[i]), "probability": float(p[i]), "n_bins": int(cover[i])}
              for i in order[:top]]
    classes: dict[str, float] = {}
    for i in np.nonzero(valid)[0]:
        letter = clean_type(lib.sptype[i])[:1].upper()
        classes[letter] = classes.get(letter, 0.0) + float(p[i])
    # probability-weighted numeric type over the dominant letter class
    return {"best": ranked[0], "ranked": ranked,
            "class_probabilities": dict(sorted(classes.items(), key=lambda kv: -kv[1])),
            "max_wl_um": TEMPLATE_MAX_WL, "n_bins": int(use.sum()),
            "_model": (lib.model[best], float(s[best]))}


def ir_index(wl, f, e, lo: float = 2.0, hi: float = 5.0) -> dict | None:
    sel = (wl >= lo) & (wl <= hi) & (f > 0) & (e > 0)
    if sel.sum() < 6:
        return None
    x = np.log10(wl[sel])
    lam_flam = f[sel] / wl[sel]  # nu F_nu up to a constant
    y = np.log10(lam_flam)
    sy = np.hypot(e[sel] / f[sel], SYSTEMATIC) / math.log(10)
    coef, cov = np.polyfit(x, y, 1, w=1 / sy, cov="unscaled")
    alpha, err = float(coef[0]), float(math.sqrt(cov[0, 0]))
    if alpha > 0.3:
        cls = "Class I (rising)"
    elif alpha > -0.3:
        cls = "flat spectrum"
    elif alpha > -1.6:
        cls = "Class II (disc excess)"
    else:
        cls = "Class III / photosphere"
    return {"alpha": alpha, "alpha_err": err, "range_um": [lo, hi], "lada_class": cls,
            "note": "Indicative: the classical index is defined over 2-24 um."}


def ir_excess(wl, f, e, short_fit: dict | None) -> dict | None:
    if short_fit is None:
        return None
    model = short_fit["_model"]
    out = {}
    for name, lo, hi in (("3_3.8um", 3.0, 3.8), ("3.8_5um", 3.8, 5.05)):
        sel = (wl >= lo) & (wl <= hi) & (e > 0)
        if sel.sum() < 2:
            out[name] = None
            continue
        pred = model(wl[sel])
        w = 1.0 / e[sel] ** 2
        obs = float((w * f[sel]).sum() / w.sum())
        exp_ = float((w * pred).sum() / w.sum())
        err = float(math.sqrt(1.0 / w.sum()))
        sig_err = math.hypot(err, SYSTEMATIC * exp_ * 3)  # extrapolation from <2.45 um: allow 9 %
        out[name] = {"observed_mjy": obs, "predicted_mjy": exp_, "ratio": obs / exp_ if exp_ > 0 else None,
                     "significance": (obs - exp_) / sig_err if sig_err > 0 else None}
    out["reference_fit"] = f"blackbody to {EXCESS_SHORT_MAX} um (T = {short_fit['teff_k']:.0f} K, "\
                           f"A_K = {short_fit['a_k_mag']:.1f})"
    return out


# Features that need a background light source behind molecular clouds or a disc; in cool-dwarf
# atmospheres the same windows are eaten by H2O/CH4/CO bands, so they are not reported as detections there.
CONTEXT_FEATURES = {"ice", "pah"}


def summarize(templates: dict | None, bb: dict | None, alpha: dict | None, excess: dict | None,
              idx: dict, features: list[dict]) -> dict:
    """One label, a family and short notes. Families: star, late_m_dwarf, brown_dwarf, composite, yso_candidate."""
    notes: list[str] = []
    label, family = "unclassified", None
    ch4 = (idx.get("ch4_3.3") or {}).get("value")
    peak = (idx.get("peak_4um_J") or {}).get("value")
    ch4_16 = (idx.get("ch4_1.6") or {}).get("value")
    methane = ch4 is not None and ch4 < 0.6 and (peak is None or peak > 0.6)
    best = templates["best"] if templates else None
    best_letter = clean_type(best["sptype"])[:1] if best else ""
    e45 = (excess or {}).get("3.8_5um") or {}
    big_excess = bool(e45.get("ratio") and e45["ratio"] > 1.5 and (e45.get("significance") or 0) > 5)
    if best:
        label, family = best["sptype"], best["family"]
    if methane and best_letter in "LT":
        label, family = f"{best['sptype']} (methane at 3.3 µm)", "brown_dwarf"
    elif methane and best_letter not in "LT" and big_excess:
        # Short wavelengths look like a warmer star, long wavelengths show methane and a 4-5 um excess.
        label = f"composite: {best['sptype'] if best else 'star'} + cold methane-rich source"
        family = "composite"
        notes.append("The 0.75-2.4 µm spectrum matches a normal star while 3-5 µm shows methane and a strong "
                     "excess: most likely a cold brown dwarf blended with a neighbouring star.")
    elif methane:
        label, family = "T dwarf (methane at 3.3 µm)", "brown_dwarf"
    elif best_letter in "LT":
        family = "brown_dwarf"
    elif best_letter == "M" and best and best["family"] == "dwarf_standard":
        family = "late_m_dwarf"
    elif best:
        family = "star"
    if methane:
        notes.append(f"Methane: CH4(3.3)/4 µm = {ch4:.2f}" + (f", CH4(1.6) index {ch4_16:.2f}"
                                                              if ch4_16 is not None and 0 < ch4_16 < 3 else "") + ".")
    if big_excess and family != "composite":
        notes.append(f"Infrared excess at 3.8-5 µm: {e45['ratio']:.1f}× the short-wavelength fit "
                     f"({e45['significance']:.0f}σ). For a star: a disc or a cold companion; for a cool dwarf it "
                     "is its own 4 µm peak.")
        if family == "star" and alpha and alpha["alpha"] > -1.6:
            family = "yso_candidate"
    if alpha and alpha["alpha"] > -1.6 and family in ("star", "yso_candidate"):
        notes.append(f"IR spectral index α = {alpha['alpha']:.2f} ({alpha['lada_class']}).")
    cool = family in ("brown_dwarf", "late_m_dwarf", "composite")
    det = [f["name"] for f in features if f["detected"] and not (cool and f["phase"] in CONTEXT_FEATURES)]
    if det:
        notes.append("Detected features: " + ", ".join(det) + ".")
    if bb and bb["teff_range_k"][1] / max(bb["teff_range_k"][0], 1) < 1.5:
        notes.append(f"Blackbody fit: T ≈ {bb['teff_k']:.0f} K, A_K ≈ {bb['a_k_mag']:.1f} mag "
                     f"(χ²/dof {bb['chi2_red']:.1f}).")
    elif bb:
        notes.append("Temperature is not constrained by a blackbody over 0.75-5 µm (Rayleigh-Jeans regime); "
                     "use the template type.")
    return {"label": label, "family": family, "notes": notes,
            "context_suppressed": sorted(f["id"] for f in features
                                         if f["detected"] and cool and f["phase"] in CONTEXT_FEATURES)}


def classify(bins: list[dict]) -> dict:
    """Full classification of a binned absolute spectrum."""
    wl, f, e = arrays(bins)
    if wl.size == 0:
        return {"available": False, "reason": "no calibrated (unflagged, non-relative) measurements"}
    from .features import continuum, indices, measure_features

    try:
        lib = library()
    except Exception as exc:  # noqa: BLE001 - templates are optional
        lib, lib_error = None, str(exc)
    else:
        lib_error = None
    idx_bins = np.array([b["bin"] for b in bins])
    tpl = fit_templates(wl, f, e, lib, idx_bins) if lib is not None else None
    bb_all = fit_blackbody(wl, f, e)
    bb_short = fit_blackbody(wl, f, e, max_wl=EXCESS_SHORT_MAX)
    alpha = ir_index(wl, f, e)
    exc = ir_excess(wl, f, e, bb_short)
    idx = indices(bins)
    feats = measure_features(bins, continuum(bins))
    summary = summarize(tpl, bb_all, alpha, exc, idx, feats)

    def public(d):
        return None if d is None else {k: v for k, v in d.items() if not k.startswith("_")}

    model_curve = None
    if tpl is not None:
        m, s = tpl["_model"]
        model_curve = {"source": tpl["best"]["name"], "wl": [float(x) for x in wl],
                       "flux_mjy": [None if not np.isfinite(m[i]) else float(s * m[i]) for i in idx_bins]}
    bb_curve = None
    if bb_all is not None:
        bb_curve = {"wl": [float(x) for x in wl], "flux_mjy": [float(v) for v in bb_all["_model"](wl)]}
    return {"available": True, "summary": summary, "templates": public(tpl), "template_error": lib_error,
            "blackbody": public(bb_all), "blackbody_short": public(bb_short), "ir_index": alpha,
            "ir_excess": exc, "model_curves": {"best_template": model_curve, "blackbody": bb_curve}}
