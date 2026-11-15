"""Asteroid analysis of a co-moving sequence (ADR-0057): rotational light curve, period, colours and a rough
taxonomy.

Fluxes: the ePSF photometry of the build (flagged, saturated and ring/relative measurements dropped). When the
sequence has static-sky subtracted exposures, aperture fluxes (radius 2 x FWHM) on those replace them, scaled per
detector to the ePSF flux scale, so field stars passing the asteroid no longer bias single points.

Light curve. Every exposure is at its own wavelength (the LVF steps through the spectrum), so brightness changes
mix rotation and the reflectance spectrum. Each flux becomes a reduced reflectance magnitude
    m = -2.5 log10(F / F_sun(lambda)) - 5 log10(r Delta)
(F_sun at 1 au through the same channel, r and Delta from geometry.py) at light-time corrected time t - Delta/c, and
each epoch is fitted with
    m = S_d(lambda) + beta alpha + sum_{k=1,2} a_k cos(2 pi k f t) + b_k sin(2 pi k f t)
where S_d is a quadratic in wavelength per detector (the spectrum) and beta the phase coefficient. The chi-square
reduction over a frequency grid (periods 1.5 h to half the epoch length, 10x oversampled) is the periodogram; the
epochs' reductions are summed for the joint period (each epoch keeps its own Fourier coefficients, since the
viewing aspect changes between visits). Two harmonics give the double-peaked curve of an elongated body, so the
best frequency is the rotation period itself; P/2 and 2P are reported as aliases to check.

Spectrum and taxonomy. The rotation-corrected reflectances (normalised at 1.25 um) are binned on the SPHEREx grid.
Thermal emission (important beyond ~3.5 um for near-Earth objects) is fitted as a blackbody added to a straight
continuum over 3.5-5 um and removed before the 3 um band is measured. Features: NIR slope (0.8-2.4 um, %/um),
1 um and 2 um band depths (olivine/pyroxene) and the 3 um hydration band depth. The class is a rough S/C/X-like
call from those (DeMeo et al. 2009 feature definitions; thresholds in TAXONOMY_RULES); albedo, which separates the
C and X complexes, is not measured, so the result carries its caveats.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from ..analysis.common import SequenceContext
from ..bands import BANDS
from ..errors import NotFound
from . import sun
from .common import (
    aperture_flux,
    load_frames,
    require_comoving,
    ring_flux,
    weighted_mean,
)
from .geometry import exposure_geometry

ASTEROID_VERSION = 1
LIGHT_DAY_PER_AU = 499.004784 / 86400.0
MIN_PERIOD_H = 1.5
OVERSAMPLE = 10
REFINE = 20  # fine-grid points per coarse step around the best peak
ORBIT_H = 1.643  # SPHEREx's orbital period (~98.6 min): sampling at this cadence aliases rotation signals
MIN_EPOCH_POINTS = 12
CLIP_SIGMA = 4.0
NORM_UM = 1.25
TAXONOMY_RULES = {
    "V": "1 um depth >= 0.30 and 2 um depth >= 0.15",
    "S": "1 um depth >= 0.07",
    "D": "1 um depth < 0.07 and slope >= 35 %/um",
    "X": "1 um depth < 0.07 and 8 <= slope < 35 %/um",
    "C": "1 um depth < 0.07 and slope < 8 %/um",
}
CAVEATS = [
    "SPHEREx starts at 0.75 um: the visible slope and the blue side of the 1 um band, which the Bus-DeMeo classes "
    "use, are missing, so the class is a rough complex (S/C/X/D/V), not a Bus-DeMeo type.",
    "Albedo is not measured; C-, X- (E/M/P) and some D-types differ mainly in albedo.",
    "Phase reddening and the per-detector calibration change slopes by several %/um.",
    "The 3 um band depth depends on the thermal model for warm (near-Earth) objects.",
    "The period search assumes a double-peaked light curve; check the 2P and P/2 aliases against the folded data.",
    "SPHEREx samples at its ~98-minute orbital cadence; periods near its harmonics are flagged as possible aliases "
    "(period.confidence and period.warnings).",
]


@dataclass
class AsteroidOptions:
    min_period_h: float = MIN_PERIOD_H
    max_period_h: float | None = None
    use_clean: bool = True

    def to_dict(self) -> dict:
        return {"min_period_h": self.min_period_h, "max_period_h": self.max_period_h, "use_clean": self.use_clean,
                "v": ASTEROID_VERSION}

    @classmethod
    def from_dict(cls, d: dict | None) -> "AsteroidOptions":
        d = d or {}
        lo = float(d.get("min_period_h") or MIN_PERIOD_H)
        hi = d.get("max_period_h")
        hi = float(hi) if hi else None
        if lo < 0.05 or (hi is not None and hi <= lo):
            raise ValueError("periods: min_period_h >= 0.05 and max_period_h > min_period_h")
        return cls(min_period_h=lo, max_period_h=hi, use_clean=bool(d.get("use_clean", True)))


# ---------------------------------------------------------------- light curve model (unit-tested)
def _base_design(wl, det, alpha) -> np.ndarray:
    """Columns of the non-periodic model: a quadratic in wavelength per detector and a phase-angle slope."""
    cols = []
    for d in np.unique(det):
        sel = (det == d).astype(float)
        x = np.where(det == d, wl - np.mean(wl[det == d]), 0.0)
        cols += [sel, sel * x, sel * x * x]
    if np.ptp(alpha) > 0.5:
        cols.append(alpha - np.mean(alpha))
    return np.stack(cols, axis=1)


def _whiten(A, w):
    return A * np.sqrt(w)[:, None]


def chi2_reduction(t, y, err, base: np.ndarray, freqs: np.ndarray, nterms: int = 2) -> tuple[np.ndarray, float]:
    """Chi-square reduction of adding a Fourier series at each frequency to a linear base model.
    Returns (delta_chi2 per frequency, chi2 of the base model)."""
    w = 1.0 / err ** 2
    sw = np.sqrt(w)
    Q, _ = np.linalg.qr(_whiten(base, w))
    yw = y * sw
    r0 = yw - Q @ (Q.T @ yw)
    chi0 = float(r0 @ r0)
    out = np.empty(freqs.size)
    for k, f in enumerate(freqs):
        ph = 2 * np.pi * f * t
        F = np.stack([fn(h * ph) for h in range(1, nterms + 1) for fn in (np.cos, np.sin)], axis=1) * sw[:, None]
        F = F - Q @ (Q.T @ F)  # orthogonal to the base model
        coef, *_ = np.linalg.lstsq(F, r0, rcond=None)
        out[k] = float(r0 @ (F @ coef))
    return out, chi0


def fit_at(t, y, err, base, f, nterms: int = 2) -> dict:
    """Full model at one frequency: coefficients, residuals, amplitude (peak-to-peak of the Fourier part)."""
    ph = 2 * np.pi * f * t
    F = np.stack([fn(h * ph) for h in range(1, nterms + 1) for fn in (np.cos, np.sin)], axis=1)
    A = np.concatenate([base, F], axis=1)
    w = 1.0 / err ** 2
    coef, *_ = np.linalg.lstsq(_whiten(A, w), y * np.sqrt(w), rcond=None)
    model = A @ coef
    four = F @ coef[base.shape[1]:]
    grid = np.linspace(0, 1, 200)
    g = np.stack([fn(h * 2 * np.pi * grid) for h in range(1, nterms + 1) for fn in (np.cos, np.sin)], axis=1)
    curve = g @ coef[base.shape[1]:]
    return {"model": model, "fourier": four, "resid": y - model, "amp": float(np.ptp(curve)),
            "curve_phase": grid, "curve": curve, "base_part": base @ coef[:base.shape[1]]}


def frequency_grid(span_days: float, min_period_h: float, max_period_h: float | None) -> np.ndarray:
    fmax = 24.0 / min_period_h
    pmax_d = (max_period_h / 24.0) if max_period_h else max(span_days / 2.0, 2 * min_period_h / 24.0)
    fmin = 1.0 / pmax_d
    df = 1.0 / (OVERSAMPLE * max(span_days, 1e-3))
    return np.arange(fmin, fmax + df, df)


def _peaks(freqs, power, n=5, min_sep=None) -> list[int]:
    min_sep = min_sep or 3 * (freqs[1] - freqs[0]) if freqs.size > 1 else 0
    loc = np.flatnonzero((power[1:-1] >= power[:-2]) & (power[1:-1] >= power[2:])) + 1
    out: list[int] = []
    for i in loc[np.argsort(power[loc])[::-1]]:
        if all(abs(freqs[i] - freqs[j]) >= min_sep for j in out):
            out.append(int(i))
        if len(out) == n:
            break
    return out


def _period_error(freqs, dchi, i, chi_red) -> float | None:
    """Half-width (hours) of the peak where delta-chi2 drops by chi_red (Delta chi2 = 1 after rescaling)."""
    if i <= 0 or i >= freqs.size - 1:
        return None
    target = dchi[i] - max(chi_red, 1.0)
    j, k = i, i
    while j > 0 and dchi[j] > target:
        j -= 1
    while k < freqs.size - 1 and dchi[k] > target:
        k += 1

    def cross(a, b):  # linear interpolation of the crossing between grid points a (below) and b (above target)
        if dchi[a] > target or dchi[b] == dchi[a]:
            return freqs[a]  # the grid ended first: conservative
        return freqs[a] + (target - dchi[a]) / (dchi[b] - dchi[a]) * (freqs[b] - freqs[a])

    df = 0.5 * (cross(k, k - 1) - cross(j, j + 1))
    f = freqs[i]
    return float(24.0 * df / f ** 2)


def _maxima(curve: np.ndarray, frac: float = 0.1) -> int:
    """Local maxima of a periodic model curve whose topographic prominence (the drop to the higher of the two
    minima separating it from a higher point) exceeds frac of the peak-to-peak amplitude: a shallow dip that
    splits one broad maximum does not make two."""
    amp = float(np.ptp(curve))
    if amp <= 0:
        return 0
    c = np.asarray(curve)[:-1]  # the last sample repeats the first
    m = c.size
    n = 0
    for k in range(m):
        if c[k] >= c[k - 1] and c[k] > c[(k + 1) % m]:
            lows = []
            for step in (1, -1):
                lo, j = c[k], 1
                while j < m:
                    q = (k + step * j) % m
                    if c[q] > c[k] or (c[q] == c[k] and q < k):  # ties: the first of equal peaks is the parent
                        break
                    lo = min(lo, c[q])
                    j += 1
                lows.append(lo)
            if c[k] - max(lows) >= frac * amp:
                n += 1
    return n


def rotation_decision(data: dict, f_ls: float, chi_red: float) -> dict:
    """Rotation frequency from the periodogram peak f_ls (two harmonics). Magnitudes: the model curve is in mag, so
    a brightness maximum is a minimum of the curve. One brightness maximum per cycle means f_ls is the second
    harmonic of the rotation (an elongated body shows two per rotation): f_rot = f_ls / 2. The odd harmonics of f_ls/2
    (which make the two maxima unequal) are then tested: delta chi2 of adding them, scaled by the reduced chi2,
    against a chi2 with 4 degrees of freedom per epoch."""
    from scipy.stats import chi2 as chi2_dist

    maxima = []
    dchi_odd, dof = 0.0, 0
    for t, y, s, base, keep, _ in data.values():
        tt, yy, ss, bb = t[keep], y[keep], s[keep], base[keep]
        fit = fit_at(tt, yy, ss, bb, f_ls)
        maxima.append(_maxima(-fit["curve"]))
        two = fit_at(tt, yy, ss, bb, f_ls / 2.0, nterms=4)
        r1 = float(np.sum((fit["resid"] / ss) ** 2))
        r2 = float(np.sum((two["resid"] / ss) ** 2))
        dchi_odd += max(0.0, r1 - r2)
        dof += 4
    single = int(np.median(maxima)) <= 1 if maxima else True
    stat = dchi_odd / max(chi_red, 1.0)
    p_odd = float(chi2_dist.sf(stat, dof)) if dof else None
    if single:
        return {"f_rot": f_ls / 2.0, "rule": "one brightness maximum per periodogram cycle: the periodogram found "
                                             "the second harmonic, rotation period = 2 x peak",
                "maxima_per_cycle": maxima, "odd_harmonics_delta_chi2": stat, "odd_harmonics_p": p_odd,
                "double_peak_confirmed": bool(p_odd is not None and p_odd < 1e-3)}
    return {"f_rot": f_ls, "rule": "two brightness maxima per periodogram cycle: the peak is the rotation period",
            "maxima_per_cycle": maxima, "odd_harmonics_delta_chi2": None, "odd_harmonics_p": None,
            "double_peak_confirmed": True}


def period_search(points: list[dict], opts: AsteroidOptions) -> dict:
    """Per-epoch and joint periodograms of reduced magnitudes. points: dicts with t, mag, err, det, wl, alpha,
    epoch."""
    by_ep: dict[int, list[dict]] = {}
    for p in points:
        by_ep.setdefault(p["epoch"], []).append(p)

    def enough(ps: list[dict]) -> bool:  # the base model plus 4 Fourier terms, with MIN_EPOCH_POINTS to spare
        base = _base_design(np.array([p["wl"] for p in ps]), np.array([p["det"] for p in ps]),
                            np.array([p["alpha"] for p in ps]))
        return len(ps) >= base.shape[1] + 4 + MIN_EPOCH_POINTS

    eps = {e: ps for e, ps in by_ep.items() if enough(ps)}
    if not eps:
        return {"note": f"fewer than {MIN_EPOCH_POINTS} usable points in every epoch", "epochs": []}
    span = max(np.ptp([p["t"] for p in ps]) for ps in eps.values())
    freqs = frequency_grid(span, opts.min_period_h, opts.max_period_h)
    total = np.zeros(freqs.size)
    out_ep = []
    data = {}
    for e, ps in sorted(eps.items()):
        t = np.array([p["t"] for p in ps])
        y = np.array([p["mag"] for p in ps])
        s = np.array([p["err"] for p in ps])
        det = np.array([p["det"] for p in ps])
        wl = np.array([p["wl"] for p in ps])
        al = np.array([p["alpha"] for p in ps])
        keep = np.ones(t.size, bool)
        base = _base_design(wl, det, al)
        for _ in range(2):  # clip outliers (blends, cosmic rays) against the best model
            dchi, chi0 = chi2_reduction(t[keep], y[keep], s[keep], base[keep], freqs)
            fit = fit_at(t, y, s, base, freqs[int(np.argmax(dchi))])
            dof = max(1, keep.sum() - base.shape[1] - 4)
            chi_red = float(np.sum((fit["resid"][keep] / s[keep]) ** 2) / dof)
            new = np.abs(fit["resid"] / s) < CLIP_SIGMA * math.sqrt(max(chi_red, 1.0))
            if (new == keep).all():
                break
            keep = new
        dchi, chi0 = chi2_reduction(t[keep], y[keep], s[keep], base[keep], freqs)
        total += dchi
        i = int(np.argmax(dchi))
        fit = fit_at(t[keep], y[keep], s[keep], base[keep], freqs[i])
        dof = max(1, keep.sum() - base.shape[1] - 4)
        chi_red = float(np.sum((fit["resid"] / s[keep]) ** 2) / dof)
        data[e] = (t, y, s, base, keep, chi_red)
        out_ep.append({"epoch": e, "n": int(t.size), "used": int(keep.sum()), "span_days": float(np.ptp(t)),
                       "best_period_h": 24.0 / freqs[i], "period_err_h": _period_error(freqs, dchi, i, chi_red),
                       "power": float(dchi[i] / chi0) if chi0 > 0 else None, "delta_chi2": float(dchi[i]),
                       "amplitude_mag": fit["amp"], "chi2_red": chi_red,
                       "peaks_h": [24.0 / freqs[j] for j in _peaks(freqs, dchi)]})
    i = int(np.argmax(total))
    chi_sum = float(np.mean([d[5] for d in data.values()]))
    # the coarse grid (1/10 of 1/span) limits the precision far below the peak width at high S/N: zoom in
    fine = np.linspace(freqs[max(0, i - 1)], freqs[min(freqs.size - 1, i + 1)], 4 * REFINE + 1)
    total_fine = np.zeros(fine.size)
    for t, y, s, base, keep, _ in data.values():
        total_fine += chi2_reduction(t[keep], y[keep], s[keep], base[keep], fine)[0]
    j = int(np.argmax(total_fine))
    f_ls = fine[j]
    err_ls = _period_error(fine, total_fine, j, chi_sum) if 0 < j < fine.size - 1 else None
    if err_ls is None:
        err_ls = _period_error(freqs, total, i, chi_sum)
    decision = rotation_decision(data, f_ls, chi_sum)
    best_f = decision["f_rot"]
    nterms = 4 if best_f < f_ls else 2  # at f_ls / 2 the f_ls harmonics are the 2nd and 4th
    folded = []
    for e, (t, y, s, base, keep, _) in data.items():
        fit = fit_at(t[keep], y[keep], s[keep], base[keep], best_f, nterms=nterms)
        ph = np.mod(t[keep] * best_f, 1.0)
        folded.append({"epoch": e, "phase": ph.tolist(), "mag": (y[keep] - fit["base_part"]).tolist(),
                       "err": s[keep].tolist(), "model_phase": fit["curve_phase"].tolist(),
                       "model": fit["curve"].tolist(), "amplitude_mag": fit["amp"]})
    # keep the returned periodogram small: maxima of 1500 chunks
    edges = np.linspace(0, freqs.size, min(1500, freqs.size) + 1).astype(int)
    idx = [a + int(np.argmax(total[a:b])) for a, b in zip(edges[:-1], edges[1:]) if b > a]
    conf = period_confidence(out_ep, 24.0 / f_ls, 24.0 / best_f)
    return {
        "best_period_h": 24.0 / best_f, **conf,
        "period_err_h": (err_ls * (f_ls / best_f)) if err_ls else None, "nterms": nterms,
        "periodogram_peak_h": 24.0 / f_ls, "rotation": decision,
        "aliases_h": {"half": 12.0 / best_f, "double": 48.0 / best_f},
        "peaks_h": [24.0 / freqs[j] for j in _peaks(freqs, total)],
        "epochs": out_ep, "folded": folded,
        "periodogram": {"period_h": (24.0 / freqs[idx]).tolist(), "delta_chi2": total[idx].tolist()},
        "grid": {"min_period_h": 24.0 / freqs[-1], "max_period_h": 24.0 / freqs[0], "n": int(freqs.size)},
    }


def _harmonic_of(p: float, ref: float, tol: float) -> bool:
    return any(abs(p / (ref * m) - 1.0) < tol for m in (0.5, 1.0, 2.0))


def period_confidence(epochs: list[dict], peak_h: float, best_h: float) -> dict:
    """high: at least two visits peak on the joint period (or its half/double, within 1 %) and it is not an alias
    of SPHEREx's orbit; medium: one visit agrees, or the visits agree but the orbit alias is possible; else low."""
    agree = [e["epoch"] for e in epochs if _harmonic_of(e["best_period_h"], peak_h, 0.01)]
    warnings_ = []
    alias = _harmonic_of(best_h, ORBIT_H, 0.03) or _harmonic_of(peak_h, ORBIT_H, 0.03)
    if alias:
        warnings_.append(f"within 3 % of a harmonic of SPHEREx's {ORBIT_H * 60:.0f}-min orbit: possible sampling alias")
    bad = [e["epoch"] for e in epochs if _harmonic_of(e["best_period_h"], ORBIT_H, 0.03)]
    if bad:
        warnings_.append(f"visits {bad} peak near SPHEREx's orbital period")
    if len(agree) < len(epochs):
        warnings_.append(f"only visits {agree} of {[e['epoch'] for e in epochs]} peak on the joint period")
    if len(agree) >= 2 and not alias:
        level = "high"
    elif len(agree) >= 1:
        level = "medium" if len(agree) >= 2 or len(epochs) == 1 else "low"
    else:
        level = "low"
    return {"confidence": level, "agreeing_epochs": agree, "warnings": warnings_}


# ---------------------------------------------------------------- spectrum features (unit-tested)
def _mean_in(wl, r, e, lo, hi):
    sel = (wl >= lo) & (wl <= hi) & np.isfinite(r)
    if sel.sum() < 1:
        return float("nan"), float("nan")
    return weighted_mean(r[sel], e[sel])


def band_depth(wl, r, e, left, right, centre) -> dict | None:
    """Depth 1 - R/C at the minimum of a band, the continuum being the line through two shoulders."""
    (l1, l2), (r1, r2), (c1, c2) = left, right, centre
    cl, el = _mean_in(wl, r, e, l1, l2)
    cr, er = _mean_in(wl, r, e, r1, r2)
    sel = (wl >= c1) & (wl <= c2) & np.isfinite(r)
    if not (np.isfinite(cl) and np.isfinite(cr)) or sel.sum() < 2:
        return None
    xl, xr = 0.5 * (l1 + l2), 0.5 * (r1 + r2)
    lam, val, err = wl[sel], r[sel], e[sel]
    order = np.argsort(lam)
    lam, val, err = lam[order], val[order], err[order]
    k = int(np.argmin(val))
    lo, hi = max(0, k - 1), min(val.size, k + 2)  # the minimum bin and its neighbours
    rmin, emin = weighted_mean(val[lo:hi], err[lo:hi])
    cont = cl + (cr - cl) * (float(np.mean(lam[lo:hi])) - xl) / (xr - xl)
    depth = 1.0 - rmin / cont
    derr = math.hypot(emin / cont, rmin * math.hypot(el, er) / (2 * cont ** 2))
    return {"depth": float(depth), "err": float(derr), "snr": float(depth / derr) if derr > 0 else None,
            "center_um": float(np.mean(lam[lo:hi]))}


def bin_spectrum(wl, band, r, e, clip: float = 3.0) -> dict:
    """Weighted mean reflectance per SPHEREx grid bin (spectral.grid, 17 per detector) after one clipping pass."""
    from ..spectral.grid import GRID

    wl, r, e, band = np.asarray(wl, float), np.asarray(r, float), np.asarray(e, float), np.asarray(band)
    idx = np.full(wl.size, -1)
    for b in np.unique(band):
        sel = band == b
        idx[sel] = GRID.index(str(b), wl[sel])
    out = {"wl": [], "r": [], "err": [], "n": []}
    for k in np.unique(idx[idx >= 0]):
        sel = (idx == k) & np.isfinite(r)
        v, s = r[sel], e[sel]
        if v.size >= 3:
            med = np.median(v)
            mad = 1.4826 * np.median(np.abs(v - med))
            ok = np.abs(v - med) <= clip * max(mad, float(np.median(s)))
            v, s = v[ok], s[ok]
        if v.size == 0:
            continue
        m, me = weighted_mean(v, s)
        if v.size >= 3:  # the scatter (rotation residuals) when it exceeds the formal error
            me = max(me, float(np.std(v, ddof=1) / math.sqrt(v.size)))
        out["wl"].append(float(GRID.center[k]))
        out["r"].append(m)
        out["err"].append(me)
        out["n"].append(int(v.size))
    return {k: np.asarray(v) for k, v in out.items()}


def thermal_fit(wl, r, e, sun_f) -> dict | None:
    """Straight reflected continuum (fitted at 1.5-2.5 um) plus a blackbody in reflectance units fitted at
    3.5-5 um, scanning temperature. Returns the temperature, scale and thermal fraction at 3 um."""
    cont = (wl >= 1.5) & (wl <= 2.5) & np.isfinite(r)
    hot = (wl >= 3.5) & np.isfinite(r)
    if cont.sum() < 3 or hot.sum() < 3:
        return None
    A = np.stack([np.ones(cont.sum()), wl[cont] - 2.0], axis=1)
    c0, c1 = np.linalg.lstsq(A / e[cont][:, None], r[cont] / e[cont], rcond=None)[0]
    line = lambda x: c0 + c1 * (x - 2.0)  # noqa: E731
    best = None
    for T in np.arange(120.0, 450.0, 5.0):
        bb = _bb_nu(wl, T) / sun_f
        x = bb[hot]
        resid = r[hot] - line(wl[hot])
        k = float(np.sum(x * resid / e[hot] ** 2) / np.sum(x * x / e[hot] ** 2))
        chi = float(np.sum(((resid - k * x) / e[hot]) ** 2))
        if k > 0 and (best is None or chi < best[0]):
            best = (chi, T, k)
    if best is None:
        return {"temperature_k": None, "thermal_fraction_3um": 0.0, "k": 0.0}
    _, T, k = best
    at3 = float(k * _bb_nu(np.array([3.0]), T)[0] / float(sun.sun_fnu(3.0)))
    return {"temperature_k": float(T), "k": k, "thermal_fraction_3um": float(at3 / max(line(3.0) + at3, 1e-12))}


def _bb_nu(wl_um, T):
    h, c, kb = 6.62607015e-34, 2.99792458e8, 1.380649e-23
    nu = c / (np.asarray(wl_um, float) * 1e-6)
    with np.errstate(over="ignore"):
        return 2 * h * nu ** 3 / c ** 2 / np.expm1(h * nu / (kb * T))


def classify(feat: dict) -> dict:
    """Rough complex from the NIR features, with the rule that fired (TAXONOMY_RULES)."""
    b1 = (feat.get("band_1um") or {}).get("depth")
    b2 = (feat.get("band_2um") or {}).get("depth")
    slope = (feat.get("slope") or {}).get("pct_per_um")
    if b1 is None or slope is None:
        return {"complex": None, "rule": "1 um band or slope not measured"}
    if b1 >= 0.30 and (b2 or 0) >= 0.15:
        c = "V"
    elif b1 >= 0.07:
        c = "S"
    elif slope >= 35:
        c = "D"
    elif slope >= 8:
        c = "X"
    else:
        c = "C"
    out = {"complex": c, "rule": TAXONOMY_RULES[c]}
    b3 = feat.get("band_3um") or {}
    if b3.get("depth") is not None and b3.get("snr") is not None:
        out["hydrated"] = bool(b3["depth"] >= 0.05 and b3["snr"] >= 3)
    return out


def spectral_features(wl, r, e, sun_f) -> dict:
    """Slope, 1/2/3 um band depths and the thermal model from a binned, normalised reflectance spectrum (the thermal
    model is subtracted first)."""
    out: dict = {}
    th = thermal_fit(wl, r, e, sun_f)
    out["thermal"] = th
    if th and th.get("temperature_k"):
        r = r - th["k"] * _bb_nu(wl, th["temperature_k"]) / sun_f
    sel = (wl >= 0.8) & (wl <= 2.4) & np.isfinite(r)
    if sel.sum() >= 5:
        A = np.stack([np.ones(sel.sum()), wl[sel] - NORM_UM], axis=1)
        w = 1 / e[sel] ** 2
        cov = np.linalg.pinv(A.T @ (A * w[:, None]))
        coef = cov @ (A.T @ (w * r[sel]))
        out["slope"] = {"pct_per_um": float(100 * coef[1] / coef[0]),
                        "err": float(100 * math.sqrt(cov[1, 1]) / coef[0]), "range_um": [0.8, 2.4]}
    out["band_1um"] = band_depth(wl, r, e, (0.74, 0.80), (1.40, 1.60), (0.85, 1.15))
    out["band_2um"] = band_depth(wl, r, e, (1.40, 1.60), (2.30, 2.45), (1.80, 2.20))
    out["band_3um"] = band_depth(wl, r, e, (2.40, 2.65), (3.55, 3.75), (2.80, 3.10))
    return out


# ---------------------------------------------------------------- the analysis
def _fluxes(ctx: SequenceContext, opts: AsteroidOptions) -> tuple[dict[int, tuple[float, float]], str,
                                                                   dict[int, tuple[float, float]]]:
    """({exposure index: (flux mJy, error)} from the ePSF photometry, or clean apertures scaled to it; source;
    relative fluxes). Saturated detectors (ring photometry, ADR-0020) are measured again in the same ring on the
    clean exposures: a constant share of the light per detector, so usable in the light curve (the model has its
    own level per detector) but not in the spectrum. The build's own ring fluxes are not used: in a co-moving frame
    stars stream through the ring."""
    meta = ctx.meta
    psf = {}
    for p in meta.get("photometry") or []:
        if p.get("flux_mjy") is None or p.get("err_mjy") is None or p.get("relative") or p.get("saturated") \
                or p.get("flagged") or p["flux_mjy"] <= 0:
            continue
        psf[p["index"]] = (float(p["flux_mjy"]), float(p["err_mjy"]))
    if not (opts.use_clean and (meta.get("arrays") or {}).get("clean_exposures")):
        return psf, "ePSF photometry of the build", {}
    frames = load_frames(ctx, prefer_clean=True)
    n = frames.images.shape[1]
    c = (n - 1) / 2.0
    pix = float(ctx.grid.pixscale)
    ap = {}
    for i, e in enumerate(meta["exposures"]):
        if not frames.usable[i]:
            continue
        fw = float(e.get("psf_fwhm") or 1.5 * pix) / pix
        m = aperture_flux(frames.images[i], c, c, max(2.0, 2.0 * fw), pix, frames.noise[i])
        if m is not None and m["flux_mjy"] > 0:
            ap[i] = (m["flux_mjy"], m["err_mjy"])
    out = {}
    for b in BANDS:
        both = [i for i in ap if i in psf and meta["exposures"][i]["band"] == b]
        if len(both) < 3:
            continue
        scale = float(np.median([psf[i][0] / ap[i][0] for i in both]))
        for i in ap:
            if meta["exposures"][i]["band"] == b:
                out[i] = (ap[i][0] * scale, ap[i][1] * scale)
    rel = {}
    for p in meta.get("photometry") or []:
        i = p["index"]
        if not p.get("relative") or not p.get("ring_r_out") or not frames.usable[i]:
            continue
        m = ring_flux(frames.images[i], c, c, float(p["ring_r_in"]), float(p["ring_r_out"]), pix, frames.noise[i])
        if m is not None and m["flux_mjy"] > 0:
            rel[i] = (m["flux_mjy"], m["err_mjy"])
    if len(out) < 0.5 * len(psf):
        return psf, "ePSF photometry of the build (too few clean apertures)", rel
    return out, "static-sky subtracted apertures (2 FWHM), scaled per detector to the ePSF photometry", rel


def asteroid_analysis(ctx: SequenceContext, opts: AsteroidOptions, progress=None, check=None) -> dict:
    progress = progress or (lambda stage, done, total: None)
    check = check or (lambda: None)
    require_comoving(ctx)
    meta = ctx.meta
    exps = meta["exposures"]
    progress("geometry", 0, 1)
    geom = exposure_geometry(meta)
    progress("photometry", 0, 1)
    flux, source, rel = _fluxes(ctx, opts)
    rel = {i: v for i, v in rel.items() if i not in flux}
    if rel:
        source += "; saturated detectors: ring photometry on the clean exposures (relative, light curve only)"
    check()
    pts = []
    for i, (f, fe) in [*flux.items(), *rel.items()]:
        e, g = exps[i], geom[i]
        if not (g.get("r_au") and g.get("delta_au")) or not e.get("wl"):
            continue
        s = float(sun.sun_fnu(e["wl"], e["dwl"]))
        refl = f / s * (g["r_au"] * g["delta_au"]) ** 2
        pts.append({"index": i, "epoch": e["epoch"], "band": e["band"], "det": BANDS.index(e["band"]),
                    "wl": e["wl"], "dwl": e["dwl"], "t": e["mjd"] - g["delta_au"] * LIGHT_DAY_PER_AU,
                    "mag": -2.5 * math.log10(refl), "err": 1.0857 * fe / f, "alpha": g["phase_deg"] or 0.0,
                    "refl": refl, "refl_err": refl * fe / f, "sun_mjy": s, "relative": i in rel})
    if len(pts) < MIN_EPOCH_POINTS:
        raise NotFound("too few usable measurements of the object (flagged, saturated or missing)")
    progress("period", 0, 1)
    ps = period_search(pts, opts)
    check()

    # rotation-corrected reflectance spectrum: remove each point's Fourier part at the joint period
    corr = {}
    if ps.get("best_period_h"):
        f = 24.0 / ps["best_period_h"]
        for e in {p["epoch"] for p in pts}:
            sel = [p for p in pts if p["epoch"] == e]
            if e not in {x["epoch"] for x in ps.get("epochs", [])}:
                continue
            t = np.array([p["t"] for p in sel])
            base = _base_design(np.array([p["wl"] for p in sel]), np.array([p["det"] for p in sel]),
                                np.array([p["alpha"] for p in sel]))
            fit = fit_at(t, np.array([p["mag"] for p in sel]), np.array([p["err"] for p in sel]), base, f,
                         nterms=ps.get("nterms", 2))
            for p, four in zip(sel, fit["fourier"]):
                corr[p["index"]] = float(four)
    spectra = []
    for e in sorted({p["epoch"] for p in pts}):
        sel = [p for p in pts if p["epoch"] == e and not p["relative"]]
        if not sel:
            continue
        wl = np.array([p["wl"] for p in sel])
        r = np.array([p["refl"] * 10 ** (0.4 * corr.get(p["index"], 0.0)) for p in sel])
        er = np.array([p["refl_err"] for p in sel])
        norm_sel = np.abs(wl - NORM_UM) < 0.08
        if norm_sel.sum() < 1:
            norm_sel = np.argsort(np.abs(wl - NORM_UM))[:2]
        norm, _ = weighted_mean(r[norm_sel], er[norm_sel])
        if not np.isfinite(norm) or norm <= 0:
            continue
        rn, en = r / norm, er / norm
        binned = bin_spectrum(wl, np.array([p["band"] for p in sel]), rn, en)
        if binned["wl"].size < 5:
            continue
        sfb = np.asarray(sun.sun_fnu(binned["wl"]), float)
        feats = spectral_features(binned["wl"], binned["r"], binned["err"], sfb)
        order = np.argsort(wl)
        alpha = float(np.median([p["alpha"] for p in sel]))
        spectra.append({"epoch": e, "n": len(sel), "phase_deg": alpha,
                        "r_au": float(np.median([geom[p["index"]]["r_au"] for p in sel])),
                        "delta_au": float(np.median([geom[p["index"]]["delta_au"] for p in sel])),
                        "wl": wl[order].tolist(), "reflectance": rn[order].tolist(), "err": en[order].tolist(),
                        "binned": {k: v.tolist() for k, v in binned.items()},
                        "features": feats, "taxonomy": classify(feats)})
    # combined call: the epoch with the most points and the smallest phase angle decides, others listed
    main = max(spectra, key=lambda s: (s["taxonomy"]["complex"] is not None, s["n"] / (1 + s["phase_deg"] / 30))) \
        if spectra else None
    return {
        "kind": "asteroid", "version": ASTEROID_VERSION, "sequence": ctx.seq_id,
        "object": (meta.get("moving") or {}).get("object"), "options": opts.to_dict(),
        "flux_source": source, "solar_spectrum": sun.source(),
        "geometry_source": sorted({g["source"] for g in geom if g.get("source")}),
        "n_points": len(pts), "n_relative": len(rel),
        "points": [{k: p[k] for k in ("index", "epoch", "band", "wl", "t", "mag", "err", "alpha", "relative")}
                   for p in pts],
        "period": ps, "spectra": spectra,
        "taxonomy": main["taxonomy"] if main else None, "taxonomy_epoch": main["epoch"] if main else None,
        "taxonomy_rules": TAXONOMY_RULES, "caveats": CAVEATS,
    }
