"""Homogeneous multi-decade light curves: 2MASS, AllWISE, NEOWISE and SPHEREx on common band scales (ADR-0059).

instruments   survey/band photometry settings: apertures, PSF enclosed energy, saturation, WISE mask bits
frames        native-pixel windows of archival frames around a field (range reads, disk cache)
framephot     aperture photometry, saturated-core radius and radial profiles on one frame (pure)
archival_lc   positions measured on every frame (proper-motion aware), ring for saturated stars, per visit
spherex_lc    SPHEREx synthetic J/H/Ks/W1/W2 per time bin, narrow bands, batch re-measure on cached cutouts
calibrate     calibration stars, frame -> catalogue and SPHEREx -> archive zero points
model         multiband period search with fitted zero points, folding, amplitudes, trends, events, verdict
lightcurve    one position, end to end (the job body)
search        field-wide ranking of sources by long-baseline variability
export        CSV of a light curve
"""

from .lightcurve import LB_VERSION, LongBaselineRequest, run
from .search import SearchOptions, search_field

__all__ = ["LB_VERSION", "LongBaselineRequest", "SearchOptions", "run", "search_field"]
