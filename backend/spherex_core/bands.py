"""SPHEREx detectors (D1-D6) and instrument constants.

Each detector carries a linear variable filter: wavelength changes across the detector, so a small
cutout samples one of the 17 channels of that detector (102 channels in total).
"""

from __future__ import annotations

BANDS = ("D1", "D2", "D3", "D4", "D5", "D6")

# Nominal detector ranges in microns and resolving power (IRSA SPHEREx mission page).
BAND_INFO = {
    "D1": {"wl_min": 0.75, "wl_max": 1.11, "R": 39},
    "D2": {"wl_min": 1.11, "wl_max": 1.64, "R": 41},
    "D3": {"wl_min": 1.64, "wl_max": 2.42, "R": 41},
    "D4": {"wl_min": 2.42, "wl_max": 3.82, "R": 35},
    "D5": {"wl_min": 3.82, "wl_max": 4.42, "R": 112},
    "D6": {"wl_min": 4.42, "wl_max": 5.00, "R": 128},
}

NATIVE_PIXSCALE = 6.15  # arcsec per pixel (sqrt of OMEGA_MEDIAN ~ 37.9 arcsec^2)


def band_index(band: str) -> int:
    return BANDS.index(band)
