"""Continuum, spectral features and indices of a binned spectrum.

Two complementary measurements:

* curated features: a fixed list of molecular bands, ices and recombination lines, each measured as the
  flux in a band window against a continuum interpolated from side windows (one side when the other is
  itself absorbed in cool dwarfs). Gives depth, equivalent width and significance for every feature,
  detected or not, so absence is informative too;
* automatic detection: a smooth pseudo-continuum is fitted in log-log space with asymmetric clipping
  (absorption bins are dropped from the fit), and runs of bins significantly below or above it are
  reported and identified against the curated list.

At R ~ 35-130 narrow lines are diluted; their depths are lower limits of the true line strength.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import numpy as np
from scipy.interpolate import LSQUnivariateSpline

from .assemble import arrays


@dataclass(frozen=True)
class Feature:
    id: str
    name: str
    species: str
    kind: str  # "absorption" | "emission"
    phase: str  # "gas" | "ice" | "line" | "pah"
    center: float  # um
    band: tuple[float, float]
    blue: tuple[float, float] | None
    red: tuple[float, float] | None


FEATURES: tuple[Feature, ...] = (
    Feature("h2o_1.4", "H₂O 1.4 µm", "H2O", "absorption", "gas", 1.40, (1.34, 1.47), (1.22, 1.30), (1.55, 1.62)),
    Feature("ch4_1.65", "CH₄ 1.65 µm", "CH4", "absorption", "gas", 1.67, (1.63, 1.72), (1.54, 1.60), None),
    Feature("paa_1.875", "Paα 1.875 µm", "H I", "emission", "line", 1.875, (1.855, 1.895), (1.80, 1.84), (1.91, 1.95)),
    Feature("h2o_1.9", "H₂O 1.9 µm", "H2O", "absorption", "gas", 1.90, (1.82, 1.97), (1.67, 1.76), (2.05, 2.13)),
    Feature("brg_2.17", "Brγ 2.166 µm", "H I", "emission", "line", 2.166, (2.15, 2.18), (2.10, 2.14), (2.19, 2.23)),
    Feature("ch4_2.2", "CH₄ 2.2 µm", "CH4", "absorption", "gas", 2.27, (2.20, 2.35), (2.05, 2.12), None),
    Feature("co_2.3", "CO 2.3 µm", "CO", "absorption", "gas", 2.34, (2.29, 2.40), (2.18, 2.26), None),
    Feature("h2o_2.7", "H₂O 2.7 µm", "H2O", "absorption", "gas", 2.70, (2.55, 2.85), (2.12, 2.20), (3.80, 3.95)),
    Feature("h2o_ice_3.05", "H₂O ice 3.05 µm", "H2O", "absorption", "ice", 3.05, (2.92, 3.18), (2.40, 2.60), (3.65, 3.90)),
    Feature("ch4_3.3", "CH₄ 3.3 µm", "CH4", "absorption", "gas", 3.30, (3.15, 3.45), None, (3.85, 4.15)),
    Feature("pah_3.3", "PAH 3.3 µm", "PAH", "emission", "pah", 3.29, (3.25, 3.34), (3.10, 3.20), (3.40, 3.50)),
    Feature("bra_4.05", "Brα 4.05 µm", "H I", "emission", "line", 4.052, (4.03, 4.075), (3.95, 4.00), (4.10, 4.15)),
    Feature("co2_4.26", "CO₂ 4.26 µm", "CO2", "absorption", "gas", 4.26, (4.22, 4.32), (4.05, 4.15), (4.40, 4.48)),
    Feature("co2_ice_4.27", "CO₂ ice 4.27 µm", "CO2", "absorption", "ice", 4.27, (4.25, 4.30), (4.10, 4.18), (4.36, 4.44)),
    Feature("co_4.6", "CO 4.6 µm", "CO", "absorption", "gas", 4.65, (4.55, 4.75), (4.00, 4.20), (4.85, 5.00)),
    Feature("co_ice_4.67", "CO ice 4.67 µm", "CO", "absorption", "ice", 4.67, (4.64, 4.70), (4.50, 4.58), (4.76, 4.85)),
)

# Flux-ratio indices: numerator window / denominator window (um). Values < 1 mean absorption in the numerator.
INDICES = {
    "h2o_1.4": ((1.38, 1.45), (1.24, 1.30), "water at 1.4 µm vs J peak"),
    "ch4_1.6": ((1.64, 1.70), (1.56, 1.60), "methane in H band (T dwarfs < ~0.8)"),
    "h2o_1.9": ((1.85, 1.95), (1.65, 1.75), "water at 1.9 µm vs H band"),
    "ch4_2.2": ((2.20, 2.30), (2.08, 2.14), "methane in K band"),
    "ch4_3.3": ((3.20, 3.40), (3.90, 4.10), "methane fundamental vs 4 µm peak (T dwarfs << 1)"),
    "co_2.3": ((2.29, 2.36), (2.20, 2.26), "CO first overtone"),
    "peak_4um_J": ((3.90, 4.10), (1.20, 1.30), "4 µm peak vs J (cool dwarfs > 1)"),
    "slope_K_4.5": ((4.40, 4.70), (2.10, 2.30), "K to 4.5 µm flux ratio (IR excess, cool objects)"),
}


WINDOW_SYSTEMATIC = 0.02  # relative calibration between windows (often on different detectors)
MAX_DETECTION_WIDTH = 0.5  # um; wider runs mean the pseudo-continuum failed (cool dwarfs), not a feature


def window_mean(wl: np.ndarray, f: np.ndarray, e: np.ndarray, lo: float, hi: float) -> tuple[float, float, int]:
    """Inverse-variance mean flux of bins whose centre is in [lo, hi] with a 2 % systematic floor;
    (nan, nan, 0) when empty."""
    sel = (wl >= lo) & (wl <= hi) & np.isfinite(f) & np.isfinite(e) & (e > 0)
    if not sel.any():
        return math.nan, math.nan, 0
    w = 1.0 / e[sel] ** 2
    m = float((w * f[sel]).sum() / w.sum())
    return m, float(math.hypot(math.sqrt(1.0 / w.sum()), WINDOW_SYSTEMATIC * abs(m))), int(sel.sum())


def _clean(v):
    return None if v is None or (isinstance(v, float) and not math.isfinite(v)) else v


def measure_feature(feat: Feature, wl, f, e, slope=None) -> dict:
    """Depth of one curated feature against a continuum from its side windows.

    Two sides: power-law (log-log) interpolation, which follows a stellar SED (linear interpolation of a
    Rayleigh-Jeans F_nu ~ lambda^-2 fakes absorption). One side: that side's flux carried to the band
    centre with the local log-log slope of the smooth continuum (`slope(lambda1, lambda2)`), or flat when
    no continuum is available.
    """
    out = {**asdict(feat), "depth": None, "depth_err": None, "ew_um": None, "significance": None,
           "detected": False, "n_band": 0}
    fb, eb, nb = window_mean(wl, f, e, *feat.band)
    sides = []
    for win in (feat.blue, feat.red):
        if win is not None:
            m, s, n = window_mean(wl, f, e, *win)
            if n and m > 0:
                sides.append((math.sqrt(win[0] * win[1]), m, s))
    out["n_band"] = nb
    if not nb or not sides:
        return out
    if len(sides) == 2:
        (x1, m1, s1), (x2, m2, s2) = sides
        t = math.log(feat.center / x1) / math.log(x2 / x1)
        fc = math.exp((1 - t) * math.log(m1) + t * math.log(m2))
        ec = fc * math.hypot((1 - t) * s1 / m1, t * s2 / m2)
    else:
        x1, m1, s1 = sides[0]
        beta = slope(x1, feat.center) if slope is not None else 0.0
        fc = m1 * (feat.center / x1) ** beta
        ec = s1 * fc / m1
    if not (fc > 0):
        return out
    depth = 1.0 - fb / fc
    derr = math.hypot(eb / fc, fb * ec / fc ** 2)
    width = feat.band[1] - feat.band[0]
    sig = depth / derr if derr > 0 else math.nan
    if feat.kind == "emission":
        sig = -sig
    out.update(depth=_clean(depth), depth_err=_clean(derr), ew_um=_clean(depth * width),
               significance=_clean(sig), detected=bool(math.isfinite(sig) and sig >= 3.0))
    return out


def slope_function(bins: list[dict], cont: np.ndarray | None):
    """Local log-log slope of the smooth continuum between two wavelengths (None without a continuum)."""
    if cont is None:
        return None
    wl = np.array([b["wl"] for b in bins])
    ok = np.isfinite(cont) & (cont > 0)
    if ok.sum() < 2:
        return None
    lx, ly = np.log(wl[ok]), np.log(cont[ok])

    def slope(a: float, b: float) -> float:
        ya, yb = np.interp(math.log(a), lx, ly), np.interp(math.log(b), lx, ly)
        return float((yb - ya) / (math.log(b) - math.log(a))) if a != b else 0.0

    return slope


def measure_features(bins: list[dict], cont: np.ndarray | None = None) -> list[dict]:
    wl, f, e = arrays(bins)
    if cont is None and bins:
        cont = continuum(bins)
    slope = slope_function(bins, cont)
    return [measure_feature(ft, wl, f, e, slope) for ft in FEATURES]


def indices(bins: list[dict]) -> dict:
    wl, f, e = arrays(bins)
    out = {}
    for name, (num, den, desc) in INDICES.items():
        a, ea, na = window_mean(wl, f, e, *num)
        b, eb, nb = window_mean(wl, f, e, *den)
        val = err = None
        if na and nb and b > 3 * eb and a > -3 * ea:  # denominator detected; numerator may be ~0 (deep band)
            val = a / b
            err = abs(val) * math.hypot(ea / a if a else 0.0, eb / b)
        out[name] = {"value": _clean(val), "err": _clean(err), "numerator_um": list(num),
                     "denominator_um": list(den), "description": desc}
    return out


def continuum(bins: list[dict], iterations: int = 6) -> np.ndarray | None:
    """Smooth pseudo-continuum at each bin (mJy), fitted in log-log with absorption bins clipped out.

    None when fewer than 10 positive bins. A cubic spline with interior knots every ~0.12 dex in
    wavelength is flexible enough for a stellar SED and stiff enough not to follow individual bands.
    """
    wl, f, e = arrays(bins)
    ok = (f > 0) & (e > 0)
    if ok.sum() < 10:
        return None
    x, y = np.log10(wl[ok]), np.log10(f[ok])
    sy = np.clip(e[ok] / (f[ok] * math.log(10)), 0.005, None)
    keep = np.ones(x.size, bool)
    fit = None
    for _ in range(iterations):
        xs, ys, ss = x[keep], y[keep], sy[keep]
        n_knots = max(0, min(int((xs.max() - xs.min()) / 0.12), xs.size // 4 - 1))
        try:
            if n_knots > 0:
                knots = np.quantile(xs, np.linspace(0, 1, n_knots + 2)[1:-1])
                spl = LSQUnivariateSpline(xs, ys, knots, w=1 / ss, k=3)
                fit = spl(x)
            else:
                fit = np.polyval(np.polyfit(xs, ys, 3, w=1 / ss), x)
        except Exception:  # noqa: BLE001 - degenerate knots; fall back to a polynomial
            fit = np.polyval(np.polyfit(xs, ys, 3, w=1 / ss), x)
        r = (y - fit) / sy
        new = (r > -2.0) & (r < 3.0)
        if new.sum() < 8 or np.array_equal(new, keep):
            break
        keep = new
    out = np.full(wl.size, np.nan)
    out[ok] = 10 ** fit
    if (~ok).any():  # interpolate over non-positive bins
        out[~ok] = np.interp(wl[~ok], wl[ok], out[ok])
    return out


def detect_features(bins: list[dict], cont: np.ndarray | None, threshold: float = 3.0) -> list[dict]:
    """Runs of bins significantly below (absorption) or above (emission) the pseudo-continuum."""
    if cont is None:
        return []
    wl, f, e = arrays(bins)
    e = np.hypot(e, WINDOW_SYSTEMATIC * np.abs(f))
    lo = np.array([b["wl_lo"] for b in bins])
    hi = np.array([b["wl_hi"] for b in bins])
    z = (f - cont) / e
    found = []
    for sign, kind in ((-1, "absorption"), (1, "emission")):
        hit = sign * z > threshold
        i = 0
        while i < hit.size:
            if not hit[i]:
                i += 1
                continue
            j = i
            while j + 1 < hit.size and hit[j + 1]:
                j += 1
            run = slice(i, j + 1)
            peak = float(np.max(sign * z[run]))
            if (j > i or peak > 5.0) and hi[j] - lo[i] <= MAX_DETECTION_WIDTH:
                frac = 1 - f[run] / cont[run]
                dl = hi[run] - lo[run]
                deficit = (cont[run] - f[run]) * dl
                sig = float(sign * -deficit.sum() / math.sqrt(((e[run] * dl) ** 2).sum()))
                wmin, wmax = float(lo[i]), float(hi[j])
                ids = [ft.id for ft in FEATURES if ft.band[0] <= wmax and ft.band[1] >= wmin and ft.kind == kind]
                found.append({"kind": kind, "wl_min": wmin, "wl_max": wmax,
                              "wl_center": float(np.average(wl[run], weights=np.abs(frac) + 1e-9)),
                              "depth_max": float(frac[np.argmax(sign * -frac)] if kind == "absorption"
                                                 else frac[np.argmin(frac)]),
                              "ew_um": float((frac * dl).sum()), "significance": sig,
                              "n_bins": int(j - i + 1), "identified_as": ids})
            i = j + 1
    found.sort(key=lambda d: d["wl_center"])
    return found
