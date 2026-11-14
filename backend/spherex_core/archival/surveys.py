"""Older surveys used as extra epochs: bands, IRSA collections, file selection and flux calibration.

Images are converted from DN to MJy/sr with the header zero point (MAGZP, Vega) and the band's
zero-magnitude flux density, so they share units with the SPHEREx co-adds:
    I [MJy/sr] = DN * F0 * 10**(-0.4 * MAGZP) / pixel_solid_angle[sr] / 1e6
F0: 2MASS from Cohen, Wheaton & Megeath (2003); WISE from Jarrett et al. (2011).
"""

from __future__ import annotations

from dataclasses import dataclass

SURVEYS = ("2mass", "allwise", "neowise")
WISE_S3 = "https://nasa-irsa-wise.s3.us-west-2.amazonaws.com/"
IBE_PREFIX = "https://irsa.ipac.caltech.edu/ibe/data/"


@dataclass(frozen=True)
class SurveyBand:
    survey: str  # key in SURVEYS
    label: str  # display name of the survey
    band: str  # display band name
    collection: str  # IRSA CAOM collection
    caom_band: str  # energy_bandpassname in caom.plane
    file_suffix: str  # science file name ending
    wl_um: float  # isophotal wavelength
    f0_jy: float  # zero-magnitude flux density (Vega)
    pixscale: float  # native pixel scale, arcsec
    default_magzp: float  # used if the header has no MAGZP


BANDS: tuple[SurveyBand, ...] = (
    SurveyBand("2mass", "2MASS", "J", "twomass_allsky", "J", ".fits.gz", 1.235, 1594.0, 1.0, 20.9),
    SurveyBand("2mass", "2MASS", "H", "twomass_allsky", "H", ".fits.gz", 1.662, 1024.0, 1.0, 20.5),
    SurveyBand("2mass", "2MASS", "Ks", "twomass_allsky", "K", ".fits.gz", 2.159, 666.7, 1.0, 20.0),
    SurveyBand("allwise", "AllWISE", "W1", "wise_allwise", "W1", "-w1-int-3.fits", 3.3526, 309.540, 1.375, 20.5),
    SurveyBand("allwise", "AllWISE", "W2", "wise_allwise", "W2", "-w2-int-3.fits", 4.6028, 171.787, 1.375, 19.5),
    SurveyBand("neowise", "NEOWISE", "W1", "neowiser", "W1", "-w1-int-1b.fits", 3.3526, 309.540, 2.75, 20.752),
    SurveyBand("neowise", "NEOWISE", "W2", "neowiser", "W2", "-w2-int-1b.fits", 4.6028, 171.787, 2.75, 19.596),
)


def bands_of(survey: str) -> list[SurveyBand]:
    return [b for b in BANDS if b.survey == survey]


def band_for(collection: str, caom_band: str, uri: str) -> SurveyBand | None:
    """The survey band of a CAOM science artifact, or None if we do not use that file."""
    for b in BANDS:
        if b.collection == collection and b.caom_band == caom_band and uri.endswith(b.file_suffix):
            if b.survey == "2mass" and "/image/" not in uri:
                continue
            return b
    return None


def dn_to_mjy_sr(magzp: float, f0_jy: float, pixel_area_sr: float) -> float:
    """Multiplier from DN to MJy/sr."""
    return f0_jy * 10.0 ** (-0.4 * magzp) / pixel_area_sr / 1e6


def wise_s3_url(ibe_url: str) -> str | None:
    """Public S3 copy of an AllWISE Atlas or NEOWISE-R frame (the bucket inserts 'images/' after the
    dataset folder); None for files that are not in the bucket."""
    if not ibe_url.startswith(IBE_PREFIX + "wise/"):
        return None
    path = ibe_url[len(IBE_PREFIX):]
    for dataset in ("wise/allwise/", "wise/neowiser/"):
        if path.startswith(dataset):
            return WISE_S3 + dataset + "images/" + path[len(dataset):]
    return None
