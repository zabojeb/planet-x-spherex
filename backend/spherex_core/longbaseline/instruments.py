"""Photometric settings of the archival surveys measured on their own frames (ADR-0059).

One `Instrument` per survey and band: native pixel scale, PSF width, the aperture and background annulus
(the surveys' own standard apertures, so our numbers compare with their catalogues), the enclosed-energy
curve of the PSF (aperture correction and ring fractions for saturated stars), the saturation limit and
the mask bits of the frames.

Enclosed energy EE(r) is the flux within r native pixels over the catalogue flux. It was measured on
unsaturated, isolated stars in the frames of the validation fields: NEOWISE and AllWISE against AllWISE w1mpro /
w2mpro, 2MASS against the 2MASS PSC (analysis/validation/long_baseline_calibration.py). A job re-derives
the zero point from the field's own catalogue stars when enough are available (calibrate.py) and falls back
to these values otherwise.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# WISE L1b mask (msk-1b) bits, Explanatory Supplement IV.4.a.iii.
WISE_SAT_BITS = 0x1FF << 10  # 10-18: saturated in sample read 1..9 (the int value is NaN or extrapolated)
WISE_BAD_BITS = sum(1 << b for b in (0, 1, 2, 3, 4, 6, 7, 9, 21, 26, 27, 28))  # static bad, transient, outliers


@dataclass(frozen=True)
class Instrument:
    survey: str  # "2mass" | "allwise" | "neowise"
    label: str  # display survey name
    band: str  # "J" | "H" | "Ks" | "W1" | "W2"
    pixscale: float  # arcsec per native pixel
    fwhm_arcsec: float
    r_ap_arcsec: float  # standard aperture radius
    bkg_arcsec: tuple[float, float]  # background annulus for unsaturated sources
    ee_r: tuple[float, ...]  # radii (px) of the enclosed-energy table
    ee: tuple[float, ...]  # EE at ee_r (fraction of the total flux)
    sat_mag: float  # Vega magnitude brighter than which the frames saturate (NEOWISE: W1 8.0, W2 7.0,
    # NEOWISE Explanatory Supplement II.1.c.iv)
    profile_px: int  # radial profile radius kept per frame (for rings)
    ring_bkg_px: tuple[float, float]  # local background of the profile
    ring_width_px: int
    has_mask: bool  # frames come with a pixel mask (NEOWISE)
    f0_jy: float  # Vega zero-magnitude flux density
    wl_um: float  # isophotal wavelength

    @property
    def r_ap_px(self) -> float:
        return self.r_ap_arcsec / self.pixscale

    @property
    def bkg_px(self) -> tuple[float, float]:
        return self.bkg_arcsec[0] / self.pixscale, self.bkg_arcsec[1] / self.pixscale

    def enclosed(self, r_px) -> np.ndarray:
        """Enclosed energy at radius r (px), linear in the table, 1 beyond its last radius."""
        return np.interp(np.asarray(r_px, float), (0.0, *self.ee_r), (0.0, *self.ee), right=self.ee[-1])

    @property
    def apcorr(self) -> float:
        """Multiplier from the standard-aperture flux to the total flux."""
        return float(1.0 / self.enclosed(self.r_ap_px))

    def ring_fraction(self, r_in: float, r_out: float) -> float:
        return float(self.enclosed(r_out) - self.enclosed(r_in))

    @property
    def window_margin_px(self) -> int:
        """Pixels needed around a position for its aperture, annulus and ring profile."""
        return int(np.ceil(max(self.bkg_px[1], self.ring_bkg_px[1]))) + 2


def _ee(r: tuple[float, ...], frac: tuple[float, ...]) -> dict:
    return {"ee_r": r, "ee": frac}


# Enclosed energy against the catalogue (total) flux, from analysis/validation/long_baseline_calibration.py
# (2026-11-15; 5 fields, 31 NEOWISE W1 / 22 W2 / 61 2MASS stars, median over stars; the standard-aperture value is
# the exact aperture sum). EE exceeds 1 at large radii: the PSF wings hold flux beyond the catalogue's profile-fit
# normalisation, and faint neighbours not removed by the faint-source correction. Star-to-star scatter of the
# aperture value: NEOWISE W1 0.005, W2 0.008, AllWISE 0.008-0.009, 2MASS J/H/Ks 0.012-0.026 mag.
_NEO_R = (1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 8.0, 10.0, 12.0, 14.0, 16.0, 18.0, 20.0, 22.0, 24.0)
_NEO_W1 = (0.3375, 0.7441, 0.8575, 0.9072, 0.9294, 0.9413, 0.9536, 0.9758, 0.9872, 1.0001, 1.0044, 1.0203, 1.0304,
           1.0394, 1.0399)
_NEO_W2 = (0.2889, 0.6843, 0.8267, 0.8859, 0.9154, 0.9324, 0.944, 0.9524, 0.9608, 0.967, 0.9816, 0.9816, 0.9843,
           0.9843, 0.9843)
_AW_R = (2.0, 4.0, 6.0, 8.0, 10.0, 12.0, 16.0, 20.0, 24.0, 28.0, 32.0, 36.0, 40.0, 44.0, 48.0)
_AW_W1 = (0.1961, 0.5614, 0.7799, 0.8784, 0.9202, 0.9407, 0.964, 0.9756, 0.9906, 1.0067, 1.0175, 1.0285, 1.0592,
          1.0848, 1.0981)
_AW_W2 = (0.1723, 0.5, 0.7425, 0.8685, 0.9185, 0.9402, 0.9661, 0.9818, 0.9922, 1.0102, 1.0298, 1.0453, 1.0529,
          1.062, 1.0846)
_TM_R = (1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 8.0, 10.0, 12.0, 14.0, 18.0, 22.0, 26.0, 30.0)
_TM_J = (0.2253, 0.6621, 0.8749, 0.9549, 0.9854, 0.9986, 1.0105, 1.0208, 1.0276, 1.0313, 1.043, 1.043, 1.0519, 1.0715)
_TM_H = (0.2447, 0.6803, 0.8876, 0.9553, 0.9826, 0.9967, 1.0116, 1.0181, 1.0236, 1.0318, 1.0382, 1.0441, 1.06,
         1.0905)
_TM_K = (0.2406, 0.6748, 0.8793, 0.9545, 0.9775, 0.9893, 1.0045, 1.0068, 1.0116, 1.0181, 1.0317, 1.0317, 1.0317,
         1.0528)

INSTRUMENTS: dict[tuple[str, str], Instrument] = {}
for _i in (
    Instrument("neowise", "NEOWISE", "W1", 2.75, 6.1, 8.25, (50.0, 70.0), **_ee(_NEO_R, _NEO_W1), sat_mag=8.0,
               profile_px=24, ring_bkg_px=(24.0, 30.0), ring_width_px=4, has_mask=True, f0_jy=309.540, wl_um=3.3526),
    Instrument("neowise", "NEOWISE", "W2", 2.75, 6.4, 8.25, (50.0, 70.0), **_ee(_NEO_R, _NEO_W2), sat_mag=7.0,
               profile_px=24, ring_bkg_px=(24.0, 30.0), ring_width_px=4, has_mask=True, f0_jy=171.787, wl_um=4.6028),
    Instrument("allwise", "AllWISE", "W1", 1.375, 8.3, 8.25, (50.0, 70.0), **_ee(_AW_R, _AW_W1), sat_mag=8.0,
               profile_px=48, ring_bkg_px=(48.0, 60.0), ring_width_px=8, has_mask=False, f0_jy=309.540,
               wl_um=3.3526),
    Instrument("allwise", "AllWISE", "W2", 1.375, 8.6, 8.25, (50.0, 70.0), **_ee(_AW_R, _AW_W2), sat_mag=7.0,
               profile_px=48, ring_bkg_px=(48.0, 60.0), ring_width_px=8, has_mask=False, f0_jy=171.787,
               wl_um=4.6028),
    Instrument("2mass", "2MASS", "J", 1.0, 2.8, 4.0, (14.0, 20.0), **_ee(_TM_R, _TM_J), sat_mag=9.0, profile_px=30,
               ring_bkg_px=(30.0, 40.0), ring_width_px=4, has_mask=False, f0_jy=1594.0, wl_um=1.235),
    Instrument("2mass", "2MASS", "H", 1.0, 2.8, 4.0, (14.0, 20.0), **_ee(_TM_R, _TM_H), sat_mag=8.5, profile_px=30,
               ring_bkg_px=(30.0, 40.0), ring_width_px=4, has_mask=False, f0_jy=1024.0, wl_um=1.662),
    Instrument("2mass", "2MASS", "Ks", 1.0, 2.8, 4.0, (14.0, 20.0), **_ee(_TM_R, _TM_K), sat_mag=8.0, profile_px=30,
               ring_bkg_px=(30.0, 40.0), ring_width_px=4, has_mask=False, f0_jy=666.7, wl_um=2.159),
):
    INSTRUMENTS[(_i.survey, _i.band)] = _i

# Vega zero points of the common bands, for magnitudes of combined light curves.
VEGA_F0_JY = {"J": 1594.0, "H": 1024.0, "Ks": 666.7, "W1": 309.540, "W2": 171.787}
WISE_BANDS = ("W1", "W2")
TWOMASS_BANDS = ("J", "H", "Ks")


def instrument(survey: str, band: str) -> Instrument:
    return INSTRUMENTS[(survey, band)]
