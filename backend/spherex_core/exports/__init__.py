"""Downloadable products of a built sequence.

fits      co-adds as FITS with the grid WCS and provenance headers (one image, or all epochs x detectors)
tables    photometry as CSV and VOTable
scripts   a shell script that downloads the original Level 2 files (whole files or IRSA cutouts)
"""

from .fits import coadds_fits
from .scripts import download_script
from .tables import photometry_csv, photometry_table, photometry_votable

__all__ = ["coadds_fits", "download_script", "photometry_csv", "photometry_table", "photometry_votable"]
