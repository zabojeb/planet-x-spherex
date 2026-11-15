"""The Sun at 1 au seen through SPHEREx channels: F_nu (mJy) averaged over a channel's response.

Source: the CALSPEC solar model sun_mod_001 (Kurucz, 0.15-300 um, erg s-1 cm-2 A-1 at 1 au) from the STScI CDBS
reference atlases, downloaded once and cached as <cache>/solar/sun_mod_001.npz. Without network the Sun falls back
to a 5772 K blackbody scaled to the solar constant (1361 W m-2), which is within ~5 % of the model over
0.75-5 um except in the 1.6 um H- bump (marked by `source`).
"""

from __future__ import annotations

import io
import logging
import urllib.request

import numpy as np

from .. import cache

log = logging.getLogger(__name__)
SUN_URL = "https://archive.stsci.edu/hlsps/reference-atlases/cdbs/calspec/sun_mod_001.fits"
C_UM_S = 2.99792458e14  # speed of light in um/s
_SUN: tuple[np.ndarray, np.ndarray, str] | None = None  # (wl_um, fnu_mjy, source)


def _blackbody() -> tuple[np.ndarray, np.ndarray, str]:
    wl = np.geomspace(0.3, 10.0, 4000)
    h, c, k, t = 6.62607015e-34, 2.99792458e8, 1.380649e-23, 5772.0
    nu = c / (wl * 1e-6)
    b_nu = 2 * h * nu ** 3 / c ** 2 / np.expm1(h * nu / (k * t))  # W m-2 Hz-1 sr-1
    r_sun, au = 6.957e8, 1.495978707e11
    f_nu = np.pi * b_nu * (r_sun / au) ** 2  # W m-2 Hz-1 at 1 au; integral = sigma T^4 (R/au)^2 = 1361 W m-2
    return wl, f_nu * 1e29, "blackbody 5772 K (offline fallback)"


def _load() -> tuple[np.ndarray, np.ndarray, str]:
    path = cache.layout().root / "solar" / "sun_mod_001.npz"
    if path.exists():
        with np.load(path) as z:
            return z["wl"], z["fnu"], "CALSPEC sun_mod_001"
    try:
        from astropy.io import fits

        with urllib.request.urlopen(SUN_URL, timeout=60) as r:
            raw = r.read()
        with fits.open(io.BytesIO(raw)) as h:
            d = h[1].data
            wl_a, flam = np.asarray(d["WAVELENGTH"], float), np.asarray(d["FLUX"], float)
        keep = (wl_a > 5000) & (wl_a < 60000)
        wl = wl_a[keep] / 1e4
        fnu = flam[keep] * (wl_a[keep] ** 2) / 2.99792458e18 * 1e26  # erg/s/cm2/Hz -> mJy (1 mJy = 1e-26)
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez(path.with_suffix(".tmp.npz"), wl=wl, fnu=fnu)
        path.with_suffix(".tmp.npz").replace(path)
        return wl, fnu, "CALSPEC sun_mod_001"
    except Exception as e:  # noqa: BLE001 - offline
        log.warning("solar spectrum download failed (%s); using a blackbody", e)
        return _blackbody()


def spectrum() -> tuple[np.ndarray, np.ndarray, str]:
    global _SUN
    if _SUN is None:
        _SUN = _load()
    return _SUN


def sun_fnu(wl_um, dwl_um=None) -> np.ndarray:
    """Solar F_nu at 1 au in mJy at wavelengths wl_um, averaged over Gaussian channels of FWHM dwl_um."""
    w, f, _ = spectrum()
    wl = np.atleast_1d(np.asarray(wl_um, float))
    dwl = np.broadcast_to(np.asarray(dwl_um if dwl_um is not None else 0.0, float), wl.shape)
    out = np.empty(wl.shape)
    for i, (x, d) in enumerate(zip(wl.ravel(), dwl.ravel())):
        if not np.isfinite(x):
            out.flat[i] = np.nan
            continue
        if not d or d <= 0:
            out.flat[i] = np.interp(x, w, f)
            continue
        s = d / 2.354820045
        m = (w > x - 4 * s) & (w < x + 4 * s)
        if m.sum() < 3:
            out.flat[i] = np.interp(x, w, f)
            continue
        wt = np.exp(-0.5 * ((w[m] - x) / s) ** 2)
        out.flat[i] = float(np.sum(wt * f[m]) / np.sum(wt))
    return out.reshape(np.shape(wl_um)) if np.ndim(wl_um) else out[0]


def source() -> str:
    return spectrum()[2]
