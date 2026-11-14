"""Analyses of a built sequence, on top of its stored result and cached cutouts.

common        loading a sequence (meta, grid, params), its Level 2 files, the cutout size it used
lightcurve    relative fluxes: each measurement over the median of its wavelength bin (as the web client)
periodogram   Lomb-Scargle (single and multiband) with phase folding
motion        per-epoch centroids in the co-adds and a proper-motion fit
remeasure     forced photometry of any position on the sequence's cached cutouts (process pool, synchronous)
"""

from .common import SequenceContext, cutout_half, load_context, sequence_frames
from .lightcurve import relative_fluxes
from .motion import fit_motion
from .periodogram import periodogram

__all__ = ["SequenceContext", "cutout_half", "fit_motion", "load_context", "periodogram", "relative_fluxes",
           "sequence_frames"]
