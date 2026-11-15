"""Synthetic 2MASS J/H/Ks and WISE W1/W2 photometry from a binned SPHEREx spectrum (calibration QA).

Filter curves come from the SVO Filter Profile Service (relative spectral responses per photon) and are
cached; zero points are Vega F_nu,0 (Cohen et al. 2003 for 2MASS; Jarrett et al. 2011 for WISE). The
synthetic flux of a photon-counting band is <F_nu> = int F_nu R / lambda dlambda / int R / lambda dlambda,
with the spectrum interpolated linearly in log F over the band and extended by a power law past its ends
(W2 runs to 5.3 um); a band is skipped when measured bins cover less than 75 % of its response, and the
covered fraction is reported.

Comparing these with catalogue magnitudes checks our absolute calibration (expect agreement to ~5 %
for unsaturated, isolated sources; WISE bands also differ in profile-fit vs aperture photometry).
"""

from __future__ import annotations

import json
import logging
import math
import urllib.parse
import urllib.request

import numpy as np

from .. import cache, netpolicy
from ..logs import timed
from .assemble import arrays

log = logging.getLogger(__name__)

SVO = "https://svo2.cab.inta-csic.es/theory/fps/getdata.php?format=ascii&id={id}"
FILTERS = {
    "J": {"svo": "2MASS/2MASS.J", "zp_jy": 1594.0, "catalog": "2mass", "column": "j"},
    "H": {"svo": "2MASS/2MASS.H", "zp_jy": 1024.0, "catalog": "2mass", "column": "h"},
    "Ks": {"svo": "2MASS/2MASS.Ks", "zp_jy": 666.7, "catalog": "2mass", "column": "mag"},
    "W1": {"svo": "WISE/WISE.W1", "zp_jy": 309.540, "catalog": "catwise", "column": "w1"},
    "W2": {"svo": "WISE/WISE.W2", "zp_jy": 171.787, "catalog": "catwise", "column": "mag"},
}
_CURVES: dict[str, tuple[np.ndarray, np.ndarray]] = {}
MIN_COVERAGE = 0.75
SUSPECT_DELTA_MAG = 0.75  # fraction of the photon-weighted response covered by measured bins


def curve(band: str) -> tuple[np.ndarray, np.ndarray]:
    """(wavelength um, relative response) of a filter, downloaded once and cached on disk."""
    if band in _CURVES:
        return _CURVES[band]
    path = cache.layout().root / "spectral" / "filters.json"
    data = json.loads(path.read_text()) if path.exists() else {}
    if band not in data:
        url = SVO.format(id=FILTERS[band]["svo"])
        service = urllib.parse.urlsplit(url).netloc
        netpolicy.check(service)  # interactive callers skip SVO for a few minutes after it failed
        timeout, _ = netpolicy.timeouts(30.0, 1)
        try:
            with timed(log, "spectral filter download", band=band), urllib.request.urlopen(url, timeout=timeout) as r:
                rows = [ln.split() for ln in r.read().decode().splitlines() if ln.strip() and not ln.startswith("#")]
        except Exception as e:
            netpolicy.failed(service, e)
            raise
        netpolicy.succeeded(service)
        data[band] = [[float(a) / 1e4, float(b)] for a, b in rows]
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data))
        tmp.replace(path)
    arr = np.array(data[band])
    _CURVES[band] = (arr[:, 0], arr[:, 1])
    return _CURVES[band]


def synthetic(bins: list[dict]) -> dict:
    """Synthetic Vega magnitudes and fluxes for every band the spectrum covers."""
    wl, f, e = arrays(bins)
    out = {}
    ok = f > 0
    for band, spec in FILTERS.items():
        try:
            lw, resp = curve(band)
        except Exception as exc:  # noqa: BLE001
            out[band] = {"error": f"filter curve unavailable: {exc}"}
            continue
        if ok.sum() < 3:
            out[band] = None
            continue
        x, y = wl[ok], f[ok]
        w_r = resp / lw
        inside = (lw >= x.min()) & (lw <= x.max())
        coverage = float((w_r * inside).sum() / w_r.sum())
        if coverage < MIN_COVERAGE:
            out[band] = {"coverage": coverage, "mag": None, "flux_mjy": None}
            continue
        logf = np.interp(lw, x, np.log(y))
        # W2 reaches 5.3 um, past SPHEREx's 5.0: extend with a power law through the last bins.
        for edge, sel in ((x.max(), lw > x.max()), (x.min(), lw < x.min())):
            if sel.any():
                near = np.argsort(np.abs(x - edge))[:3]
                k, c = np.polyfit(np.log(x[near]), np.log(y[near]), 1)
                logf[sel] = k * np.log(lw[sel]) + c
        fl = float((np.exp(logf) * w_r).sum() / w_r.sum())
        # relative error from the bins inside the band
        sel = (wl >= lw[inside].min()) & (wl <= lw[inside].max()) & ok
        rel = float(math.sqrt(np.sum((e[sel] / f[sel]) ** 2)) / max(sel.sum(), 1)) if sel.any() else None
        mag = -2.5 * math.log10(fl * 1e-3 / spec["zp_jy"])
        out[band] = {"coverage": coverage, "extrapolated": coverage < 0.999, "flux_mjy": fl, "mag": mag,
                     "mag_err": 1.0857 * rel if rel is not None else None}
    return out


def compare_with_catalogs(synth: dict, matches: dict[str, dict | None]) -> dict:
    """Synthetic minus catalogue magnitude per band (matches: catalog name -> matched source or None)."""
    out = {}
    for band, spec in FILTERS.items():
        s = synth.get(band) or {}
        src = matches.get(spec["catalog"])
        cat = src.get(spec["column"]) if src else None
        if s.get("mag") is None or cat is None:
            out[band] = None
            continue
        delta = s["mag"] - float(cat)
        out[band] = {"synthetic": s["mag"], "catalog": float(cat), "delta_mag": delta,
                     "flux_ratio": 10 ** (-0.4 * delta), "catalog_id": src.get("id"),
                     "separation_arcsec": src.get("separation_arcsec"),
                     # > 0.75 mag: a wrong match, a blend, a variable, or catalogue photometry corrupted by fast
                     # motion (CatWISE splits fast movers) — not a calibration problem
                     "suspect": bool(abs(delta) > SUSPECT_DELTA_MAG)}
    return out
