"""Period and variability analysis of multi-survey light curves over a decades-long baseline.

Points are in magnitudes. Each point belongs to a band (W1, W2, D1..D6, J, H, Ks) and to an offset group:
points on one calibrated scale share a group (all W1 aperture / synthetic points after cross-calibration),
while relative or approximately scaled segments (rings of saturated stars, SPHEREx relative bands) get their
own group whose zero point is fitted. The periodic model at frequency f is

    m_i = c_group(i) + sum_h [a_band,h sin(2 pi h f t_i) + b_band,h cos(2 pi h f t_i)]  (h = 1..nterms)

solved by weighted linear least squares on a frequency grid (generalised multiband Lomb-Scargle with free
zero points per group, VanderPlas & Ivezic 2015 with nterms_band amplitudes and no shared base term).
Power is the fractional chi^2 reduction against the offsets-only model. The false-alarm probability is an
F-test at the peak times the number of independent frequencies; the period error comes from the curvature
of chi^2 at the peak after scaling chi^2/dof to 1 (cycle-to-cycle changes of Miras dominate the residuals).
"""

from __future__ import annotations

import math
from collections import defaultdict

import numpy as np
from scipy import stats

MAG_FLOOR = 0.02  # mag added in quadrature to every point's error in the fits
MIN_POINTS = 8
DEFAULT_MIN_PERIOD = 50.0  # days: 6-month visits cannot constrain shorter periods without dense SPHEREx data
SAMPLES_PER_PEAK = 10
MAX_FREQUENCIES = 20000
FAP_PERIODIC = 1e-3
POWER_PERIODIC = 0.3
VARIABLE_CHI2 = 5.0
VARIABLE_AMPLITUDE = 0.1  # mag, robust 5-95 % range
VARIABLE_RANGE_NOISE = 5.0  # the range must also exceed this many median errors (pure noise gives ~3.3)
WISE_CADENCE_DAYS = 365.25  # WISE sees a field every ~6 months with the scan direction alternating, so a
CADENCE_HARMONICS = 7  # systematic of the visit geometry looks periodic at 365.25/k d (k = 1..7 for P >= 50 d)
EVENT_SIGMA = 3.0
EVENT_MIN_MAG = 0.1
TREND_MIN_YEARS = 3.0  # shorter spans (one 2MASS or SPHEREx season) cannot separate a trend from variability


def _weights(err: np.ndarray) -> np.ndarray:
    return 1.0 / (np.asarray(err, float) ** 2 + MAG_FLOOR ** 2)


def _design(t: np.ndarray, groups: np.ndarray, bands: np.ndarray, f: float | None, nterms: int,
            trend_bands: list[str] | None = None, t_ref: float = 0.0) -> np.ndarray:
    ug = sorted(set(groups.tolist()))
    cols = [(groups == g).astype(float) for g in ug]
    if f is not None:
        for b in sorted(set(bands.tolist())):
            sel = (bands == b).astype(float)
            for h in range(1, nterms + 1):
                ph = 2 * math.pi * h * f * t
                cols += [sel * np.sin(ph), sel * np.cos(ph)]
    for b in trend_bands or []:
        cols.append((bands == b).astype(float) * (t - t_ref) / 365.25)
    return np.stack(cols, axis=1)


def _solve(x: np.ndarray, y: np.ndarray, w: np.ndarray) -> tuple[np.ndarray, float, np.ndarray]:
    sw = np.sqrt(w)
    a, b = x * sw[:, None], y * sw
    coef, *_ = np.linalg.lstsq(a, b, rcond=None)
    resid = y - x @ coef
    chi2 = float((w * resid ** 2).sum())
    try:
        cov = np.linalg.pinv(a.T @ a)
    except np.linalg.LinAlgError:
        cov = np.full((x.shape[1], x.shape[1]), np.nan)
    return coef, chi2, cov


def frequency_grid(baseline: float, min_period: float, max_period: float) -> np.ndarray:
    fmin, fmax = 1.0 / max_period, 1.0 / min_period
    df = 1.0 / (SAMPLES_PER_PEAK * baseline)
    n = int(min(MAX_FREQUENCIES, max(50, math.ceil((fmax - fmin) / df))))
    return np.linspace(fmin, fmax, n)


def periodogram(t: np.ndarray, y: np.ndarray, err: np.ndarray, groups: np.ndarray, bands: np.ndarray,
                freqs: np.ndarray, nterms: int = 1) -> tuple[np.ndarray, float, int]:
    """Power at each frequency, chi^2 of the offsets-only model, and the number of sinusoid parameters."""
    w = _weights(err)
    _, chi2_0, _ = _solve(_design(t, groups, bands, None, nterms), y, w)
    ub = sorted(set(bands.tolist()))
    ug = sorted(set(groups.tolist()))
    base = np.stack([(groups == g).astype(float) for g in ug], axis=1)
    bsel = np.stack([(bands == b).astype(float) for b in ub], axis=1)
    sw = np.sqrt(w)
    yw = y * sw
    power = np.empty(freqs.size)
    for i, f in enumerate(freqs):
        cols = [base]
        for h in range(1, nterms + 1):
            ph = 2 * math.pi * h * f * t
            cols += [bsel * np.sin(ph)[:, None], bsel * np.cos(ph)[:, None]]
        a = np.concatenate(cols, axis=1) * sw[:, None]
        coef, *_ = np.linalg.lstsq(a, yw, rcond=None)
        r = yw - a @ coef
        power[i] = 1.0 - float(r @ r) / chi2_0 if chi2_0 > 0 else 0.0
    return power, chi2_0, 2 * nterms * len(ub)


def _peaks(freqs: np.ndarray, power: np.ndarray, n: int, min_sep: float) -> list[int]:
    if freqs.size < 3:
        return [int(np.argmax(power))] if freqs.size else []
    loc = np.flatnonzero((power[1:-1] >= power[:-2]) & (power[1:-1] >= power[2:])) + 1
    out: list[int] = []
    for i in loc[np.argsort(power[loc])[::-1]]:
        if all(abs(freqs[i] - freqs[j]) >= min_sep for j in out):
            out.append(int(i))
        if len(out) == n:
            break
    return out


def _period_error(t, y, err, groups, bands, f_best: float, chi2_red: float, baseline: float,
                  nterms: int) -> float | None:
    """sigma_P from the chi^2 curvature at the peak (chi^2 scaled to chi^2/dof = 1)."""
    w = _weights(err) / max(chi2_red, 1e-9)
    h = 0.05 / baseline
    chis = []
    for df in (-h, 0.0, h):
        _, c, _ = _solve(_design(t, groups, bands, f_best + df, nterms), y, w)
        chis.append(c)
    curv = (chis[0] - 2 * chis[1] + chis[2]) / h ** 2
    if not curv > 0:
        return None
    sigma_f = math.sqrt(2.0 / curv)
    return float(sigma_f / f_best ** 2)


def _model_curve(coef: np.ndarray, groups_u: list, bands_u: list, nterms: int, phase: np.ndarray,
                 band: str) -> np.ndarray:
    """Model of one band versus phase (offset 0), from the coefficients of a _design fit."""
    g = len(groups_u)
    j = bands_u.index(band)
    out = np.zeros_like(phase)
    for h in range(1, nterms + 1):
        k = g + 2 * (j * nterms + (h - 1))
        out += coef[k] * np.sin(2 * math.pi * h * phase) + coef[k + 1] * np.cos(2 * math.pi * h * phase)
    return out


def _design_ordered(t, groups, bands, f, nterms):
    """Like _design but with the sinusoid columns ordered band-major then harmonic (for _model_curve)."""
    ug = sorted(set(groups.tolist()))
    ub = sorted(set(bands.tolist()))
    cols = [(groups == g).astype(float) for g in ug]
    for b in ub:
        sel = (bands == b).astype(float)
        for h in range(1, nterms + 1):
            ph = 2 * math.pi * h * f * t
            cols += [sel * np.sin(ph), sel * np.cos(ph)]
    return np.stack(cols, axis=1), ug, ub


def offsets_only(t, y, err, groups) -> tuple[dict, float, int]:
    """Zero point per group of a constant model, its chi^2 and degrees of freedom."""
    w = _weights(err)
    x = _design(t, groups, groups, None, 1)
    coef, chi2, _ = _solve(x, y, w)
    ug = sorted(set(groups.tolist()))
    return {g: float(c) for g, c in zip(ug, coef)}, chi2, len(y) - len(ug)


def _family(survey: str) -> str:
    """Independent measurements: SPHEREx synthetic W1/W2 and the narrow bands all come from the same spectra."""
    return "SPHEREx" if survey == "SPHEREx" else survey


def events(t: np.ndarray, resid: np.ndarray, err: np.ndarray, bands: np.ndarray, sigma: np.ndarray,
           surveys: np.ndarray | None = None) -> list[dict]:
    """Runs of points far from the model (positive residual = fainter, a dimming). An event is either a run of
    consecutive same-sign outliers in one band (two NEOWISE / AllWISE / 2MASS visits, three SPHEREx time bins), or
    one outlier confirmed within 5 days by an outlier of an independent measurement: another survey, or another
    archival band. SPHEREx bands never confirm each other (one spectrum), and NaN residuals are ignored."""
    surveys = np.array([""] * len(t)) if surveys is None else np.asarray(surveys)
    ok = np.isfinite(resid)
    thr = np.maximum(EVENT_SIGMA * np.hypot(sigma, err), EVENT_MIN_MAG)
    dev = ok & (np.abs(np.where(ok, resid, 0.0)) > thr)
    hits = []
    for b in sorted(set(bands[ok].tolist())):
        idx = np.flatnonzero((bands == b) & ok)
        idx = idx[np.argsort(t[idx])]
        run: list[int] = []
        for i in list(idx) + [None]:
            same = i is not None and dev[i] and (not run or np.sign(resid[i]) == np.sign(resid[run[-1]]))
            if same:
                run.append(i)
                continue
            if run:
                hits.append(run)
            run = [i] if (i is not None and dev[i]) else []

    def independent(i: int, j: int) -> bool:
        fi, fj = _family(str(surveys[i])), _family(str(surveys[j]))
        if fi == "SPHEREx" and fj == "SPHEREx":
            return False
        return bands[i] != bands[j] or fi != fj

    merged: list[dict] = []
    for run in hits:
        others = [j for j in np.flatnonzero(dev) if j not in run and independent(run[0], j)
                  and np.sign(resid[j]) == np.sign(resid[run[0]]) and min(abs(t[j] - t[k]) for k in run) <= 5.0]
        need = 3 if _family(str(surveys[run[0]])) == "SPHEREx" else 2
        if len(run) < need and not others:
            continue
        mem = sorted(set(run) | set(others))
        ev = {"start_mjd": float(t[mem].min()), "end_mjd": float(t[mem].max()),
              "kind": "dimming" if resid[run[0]] > 0 else "brightening",
              "peak_mag": float(resid[mem][np.argmax(np.abs(resid[mem]))]),
              "bands": sorted({str(bands[j]) for j in mem}), "n_points": len(mem),
              "surveys": sorted({str(surveys[j]) for j in mem if surveys[j]}),
              "significance": float(np.max(np.abs(resid[mem]) / np.hypot(sigma[mem], err[mem])))}
        for m in merged:  # same episode seen in several bands
            if m["kind"] == ev["kind"] and ev["start_mjd"] <= m["end_mjd"] + 5 and ev["end_mjd"] >= m["start_mjd"] - 5:
                m["start_mjd"], m["end_mjd"] = min(m["start_mjd"], ev["start_mjd"]), max(m["end_mjd"], ev["end_mjd"])
                m["bands"] = sorted(set(m["bands"]) | set(ev["bands"]))
                m["surveys"] = sorted(set(m["surveys"]) | set(ev["surveys"]))
                m["n_points"] = m["n_points"] + ev["n_points"]
                if abs(ev["peak_mag"]) > abs(m["peak_mag"]):
                    m["peak_mag"] = ev["peak_mag"]
                m["significance"] = max(m["significance"], ev["significance"])
                break
        else:
            merged.append(ev)
    return sorted(merged, key=lambda e: e["start_mjd"])


def analyze(points: list[dict], *, period_bands: tuple[str, ...] | None = None, min_period: float | None = None,
            max_period: float | None = None, nterms_fit: int = 2, n_peaks: int = 5) -> dict:
    """Period search, folded curves, amplitudes, trends, era means, events and a classification.

    points: dicts with mjd, band, group, mag, mag_err (and survey, method for reporting). period_bands limits the
    period search to these bands (all by default); every band still gets trends, era means and events.
    """
    pts = [p for p in points if p.get("mag") is not None and math.isfinite(p["mag"]) and p.get("mag_err") is not None]
    if len(pts) < MIN_POINTS:
        raise ValueError(f"need at least {MIN_POINTS} light-curve points, have {len(pts)}")
    t_all = np.array([p["mjd"] for p in pts], float)
    y_all = np.array([p["mag"] for p in pts], float)
    e_all = np.array([p["mag_err"] for p in pts], float)
    g_all = np.array([p["group"] for p in pts])
    b_all = np.array([p["band"] for p in pts])
    s_all = np.array([p.get("survey", "") for p in pts])
    out: dict = {"n_points": len(pts), "bands": sorted(set(b_all.tolist())), "groups": sorted(set(g_all.tolist())),
                 "baseline_days": float(t_all.max() - t_all.min()),
                 "first_mjd": float(t_all.min()), "last_mjd": float(t_all.max())}

    # constant model: variability statistics
    offs, chi2_c, dof_c = offsets_only(t_all, y_all, e_all, g_all)
    resid_c = y_all - np.array([offs[g] for g in g_all])
    per_band = {}
    for b in out["bands"]:
        sel = b_all == b
        r = resid_c[sel]
        per_band[b] = {"n": int(sel.sum()),
                       "chi2_red_constant": float((_weights(e_all[sel]) * r ** 2).sum() / max(sel.sum() - 1, 1)),
                       "range_5_95_mag": float(np.percentile(r, 95) - np.percentile(r, 5)) if sel.sum() >= 3 else None,
                       "robust_sigma_mag": float(1.4826 * np.median(np.abs(r - np.median(r)))),
                       "median_err_mag": float(math.hypot(np.median(e_all[sel]), MAG_FLOOR))}
    out["constant_model"] = {"chi2_red": float(chi2_c / max(dof_c, 1)), "offsets": offs}

    # period search
    sel = np.ones(len(pts), bool) if not period_bands else np.isin(b_all, list(period_bands))
    t, y, e, g, b = t_all[sel], y_all[sel], e_all[sel], g_all[sel], b_all[sel]
    period: dict | None = None
    # residuals for events and trends: from the constant model, bands with too few points excluded (NaN)
    n_band = {bb: int((b_all == bb).sum()) for bb in out["bands"]}
    model_resid = np.where(np.array([n_band[bb] >= 4 for bb in b_all]), resid_c, np.nan)
    if sel.sum() >= MIN_POINTS and len(set(b.tolist())) >= 1:
        baseline = float(t.max() - t.min())
        pmin = float(min_period or DEFAULT_MIN_PERIOD)
        pmax = float(max_period or max(min(baseline / 1.5, 6000.0), pmin * 2))
        if baseline > 0 and pmin < pmax:
            freqs = frequency_grid(baseline, pmin, pmax)
            power, chi2_0, k = periodogram(t, y, e, g, b, freqs)
            best = int(np.argmax(power))
            f_best = float(freqs[best])
            n_par = len(set(g.tolist())) + k
            nu2 = max(len(y) - n_par, 1)
            p_best = float(power[best])
            fstat = (p_best / k) / max((1 - p_best) / nu2, 1e-12)
            p_single = float(stats.f.sf(fstat, k, nu2))
            m_eff = max(1.0, (freqs[-1] - freqs[0]) * baseline)
            fap = float(min(1.0, -math.expm1(m_eff * math.log1p(-min(p_single, 1 - 1e-16)))))
            nterms = nterms_fit if len(y) >= 3 * (len(set(g.tolist())) + 2 * nterms_fit * len(set(b.tolist()))) else 1
            x, ug, ub = _design_ordered(t, g, b, f_best, nterms)
            w = _weights(e)
            coef, chi2_p, _ = _solve(x, y, w)
            chi2_red = chi2_p / max(len(y) - x.shape[1], 1)
            p_err = _period_error(t, y, e, g, b, f_best, chi2_red, baseline, nterms)
            phase_grid = np.linspace(0, 1, 101)
            t0 = float(t_all.min())
            amps, curves = {}, {}
            for band in ub:
                c = _model_curve(coef, ug, ub, nterms, phase_grid + (t0 * f_best) % 1.0, band)
                amps[band] = float(c.max() - c.min())
                curves[band] = c.tolist()
            off_p = {gr: float(coef[i]) for i, gr in enumerate(ug)}
            peak_idx = _peaks(freqs, power, n_peaks, 1.0 / baseline)
            ds = np.linspace(0, freqs.size - 1, min(freqs.size, 1500)).astype(int)
            period = {"period_days": 1.0 / f_best, "period_err_days": p_err, "frequency_per_day": f_best,
                      "power": p_best, "false_alarm_probability": fap, "n_points": int(len(y)),
                      "bands": ub, "nterms": nterms, "chi2_red": float(chi2_red),
                      "min_period_days": pmin, "max_period_days": pmax, "baseline_days": baseline,
                      "amplitudes_mag": amps, "offsets": off_p, "t0_mjd": t0,
                      "peaks": [{"period_days": 1.0 / float(freqs[i]), "power": float(power[i])} for i in peak_idx],
                      "periodogram": {"period_days": (1.0 / freqs[ds]).tolist(), "power": power[ds].tolist()},
                      "model": {"phase": phase_grid.tolist(), "mag_by_band": curves}}
            alias = cadence_alias(f_best, t[np.char.startswith(np.array(s_all[sel], str), "NEOWISE")])
            period["cadence_alias"] = alias
            periodic = fap < FAP_PERIODIC and p_best >= POWER_PERIODIC and alias is None
            period["significant"] = bool(periodic)
            if periodic:  # residuals from the periodic model; other bands get their own sinusoid at f_best
                xa, _, _ = _design_ordered(t_all[sel], g_all[sel], b_all[sel], f_best, nterms)
                model_resid[sel] = y_all[sel] - xa @ coef
                for band in out["bands"]:
                    sb = (b_all == band) & ~sel
                    if not sb.any():
                        continue
                    if sb.sum() >= 3 + len(set(g_all[sb].tolist())):
                        xb, _, _ = _design_ordered(t_all[sb], g_all[sb], b_all[sb], f_best, 1)
                        cb, _, _ = _solve(xb, y_all[sb], _weights(e_all[sb]))
                        model_resid[sb] = y_all[sb] - xb @ cb
                    else:  # a few points of a large-amplitude variable say nothing about events or trends
                        model_resid[sb] = np.nan
    out["period"] = period

    # trends: per band, slope in mag/yr on top of the constant (or periodic) model, over at least TREND_MIN_YEARS
    trends = {}
    for band in out["bands"]:
        s = (b_all == band) & np.isfinite(model_resid)
        if s.sum() < 6 or np.ptp(t_all[s]) < TREND_MIN_YEARS * 365.25:
            continue
        r = model_resid[s]
        tt = t_all[s]
        gg = g_all[s]
        x = _design(tt, gg, np.array([band] * s.sum()), None, 1, [band], t_ref=float(tt.mean()))
        coef, chi2_t, cov = _solve(x, r, _weights(e_all[s]))
        dof = max(s.sum() - x.shape[1], 1)
        scale = max(chi2_t / dof, 1.0)
        slope, slope_err = float(coef[-1]), float(math.sqrt(max(cov[-1, -1] * scale, 0.0)))
        trends[band] = {"slope_mag_per_year": slope, "slope_err": slope_err,
                        "significance": abs(slope) / slope_err if slope_err > 0 else None,
                        "span_years": float(np.ptp(tt) / 365.25), "n": int(s.sum())}
    out["trends"] = trends

    # era means: one weighted mean per band, survey and group (changes between 2MASS / AllWISE / NEOWISE / SPHEREx)
    eras: dict[str, list[dict]] = defaultdict(list)
    keys = sorted({(p["band"], p.get("survey", ""), p["group"]) for p in pts})
    for band, survey, grp in keys:
        mem = [p for p in pts if p["band"] == band and p.get("survey", "") == survey and p["group"] == grp]
        yy = np.array([p["mag"] for p in mem])
        ww = _weights(np.array([p["mag_err"] for p in mem]))
        mean = float((ww * yy).sum() / ww.sum())
        err = max(math.sqrt(1.0 / ww.sum()), float(np.std(yy, ddof=1) / math.sqrt(len(yy))) if len(yy) > 1 else 0.0)
        mj = [p["mjd"] for p in mem]
        eras[band].append({"survey": survey, "group": grp, "mean_mag": mean, "err_mag": err, "n": len(mem),
                           "first_mjd": float(min(mj)), "last_mjd": float(max(mj))})
    out["era_means"] = dict(eras)

    # events relative to the model, with each band's robust scatter
    sig = np.full(len(pts), 0.05)
    for band in out["bands"]:
        s = (b_all == band) & np.isfinite(model_resid)
        r = model_resid[s]
        if s.sum() >= 5:
            sig[s] = 1.4826 * np.median(np.abs(r - np.median(r)))
    out["events"] = events(t_all, model_resid, e_all, b_all, sig, s_all)
    out["band_stats"] = per_band
    out["classification"] = classify(out)
    return out


def cadence_alias(f: float, t_neowise: np.ndarray) -> int | None:
    """k when frequency f lies within half the NEOWISE frequency resolution of k / 365.25 d (k = 1..7), else
    None. NEOWISE alternates its scan direction every visit, so diffraction spikes, ghosts and latents of a
    neighbour come and go with a 1-yr pattern that the 6-monthly sampling aliases onto these periods."""
    if t_neowise.size < 4:
        return None
    half = 0.5 / max(float(np.ptp(t_neowise)), WISE_CADENCE_DAYS)
    for k in range(1, CADENCE_HARMONICS + 1):
        if abs(f - k / WISE_CADENCE_DAYS) < half:
            return k
    return None


def classify(res: dict) -> dict:
    """Plain-language verdict from the statistics (explicit rules, reasons listed).

    Variable: in one of W1/W2 (all bands when neither is present), chi2/dof of the constant model >= 5 and a
    5-95 % range >= max(0.1 mag, 5 median errors). Periodic: a significant period on top (FAP < 1e-3, power >= 0.3,
    not a NEOWISE cadence alias)."""
    reasons = []
    per = res.get("band_stats", {})
    main = [b for b in ("W1", "W2") if b in per] or list(per)

    def need(b: str) -> float:
        return max(VARIABLE_AMPLITUDE, VARIABLE_RANGE_NOISE * per[b].get("median_err_mag", 0.0))

    var_bands = [b for b in main if per[b]["chi2_red_constant"] >= VARIABLE_CHI2
                 and (per[b]["range_5_95_mag"] or 0.0) >= need(b)]
    variable = bool(var_bands)
    shown = var_bands or main
    reasons.append("; ".join(f"{b}: constant-model chi2/dof {per[b]['chi2_red_constant']:.1f} (variable if >= "
                             f"{VARIABLE_CHI2}), 5-95 % range {per[b]['range_5_95_mag'] or 0.0:.2f} mag "
                             f"(>= {need(b):.2f})" for b in shown))
    p = res.get("period") or {}
    periodic = bool(p.get("significant")) and variable
    label = "constant"
    if variable:
        label = "variable (aperiodic)"
    if periodic:
        per_d = p["period_days"]
        label = "long-period variable" if per_d >= 100 else "periodic variable"
        reasons.append(f"period {per_d:.1f} d, power {p['power']:.2f}, FAP {p['false_alarm_probability']:.1e}")
    elif p and p.get("cadence_alias"):
        reasons.append(f"best period {p['period_days']:.1f} d is the NEOWISE cadence alias 365.25/"
                       f"{p['cadence_alias']} d (visit-geometry systematics), not counted")
    elif p:
        reasons.append(f"best period {p.get('period_days', 0):.1f} d not significant (power {p.get('power', 0):.2f}, "
                       f"FAP {p.get('false_alarm_probability', 1):.1e})")
    trend_bands = [b for b, tr in res.get("trends", {}).items()
                   if tr.get("significance") and tr["significance"] >= 5 and abs(tr["slope_mag_per_year"]) >= 0.01]
    ev = [e for e in res.get("events", []) if e["kind"] == "dimming"]
    if trend_bands:
        reasons.append("long-term trend in " + ", ".join(trend_bands))
    if ev:
        reasons.append(f"{len(ev)} dimming event(s)")
    return {"label": label, "variable": bool(variable), "periodic": periodic,
            "trend_bands": trend_bands, "dimming_events": len(ev), "reasons": reasons}
