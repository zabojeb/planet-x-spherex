"""Archival epochs: 2MASS, AllWISE and NEOWISE images on a sequence's grid, for a decades-long baseline.

surveys   bands, IRSA collections, DN -> MJy/sr calibration, S3 locations
sources   CAOM TAP search around the field (cached)
reader    range reads of uncompressed FITS images (S3), whole-file reads of gzipped 2MASS images
build     the pipeline: search -> read -> calibrate -> smooth + resample -> combine per night / visit
store     the result format under <cache>/archival/<sequence id>/<n>/
"""

from .build import ARCHIVAL_VERSION, archival_dir, build_archival, grid_size, to_grid, visits
from .surveys import BANDS, SURVEYS, SurveyBand, dn_to_mjy_sr, wise_s3_url

__all__ = ["ARCHIVAL_VERSION", "BANDS", "SURVEYS", "SurveyBand", "archival_dir", "build_archival", "dn_to_mjy_sr",
           "grid_size", "to_grid", "visits", "wise_s3_url"]
