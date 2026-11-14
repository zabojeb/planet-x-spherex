"""Positions of a moving object from a tabulated ephemeris (MJD, RA, Dec), with no other dependencies.

RA is unwrapped before interpolating so tracks that cross RA = 0/360 stay continuous. Times outside the
table are extrapolated linearly from the nearest segment (exposure mid-times sit a minute after the
tabulated start times, where the positions come from).
"""

from __future__ import annotations

import numpy as np


def normalise(rows) -> np.ndarray:
    """[[mjd, ra, dec], ...] sorted by time, duplicate times removed, as an (n, 3) float array."""
    arr = np.asarray(rows, dtype=float).reshape(-1, 3)
    arr = arr[np.argsort(arr[:, 0], kind="stable")]
    keep = np.concatenate([[True], np.diff(arr[:, 0]) > 1e-9]) if len(arr) else np.zeros(0, bool)
    return arr[keep]


def interpolate(rows, mjd):
    """(ra, dec) in degrees at MJD value(s) from an ephemeris table; scalars in, scalars out."""
    arr = normalise(rows)
    if len(arr) == 0:
        raise ValueError("empty ephemeris")
    t = np.asarray(mjd, dtype=float)
    if len(arr) == 1:
        return (np.full(t.shape, arr[0, 1])[()], np.full(t.shape, arr[0, 2])[()])
    ra_u = np.degrees(np.unwrap(np.radians(arr[:, 1])))
    ra = _interp_extrap(t, arr[:, 0], ra_u) % 360.0
    dec = _interp_extrap(t, arr[:, 0], arr[:, 2])
    return ra[()], dec[()]


def _interp_extrap(t: np.ndarray, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    out = np.interp(t, x, y)
    lo, hi = t < x[0], t > x[-1]
    if np.any(lo):
        slope = (y[1] - y[0]) / (x[1] - x[0])
        out = np.where(lo, y[0] + slope * (t - x[0]), out)
    if np.any(hi):
        slope = (y[-1] - y[-2]) / (x[-1] - x[-2])
        out = np.where(hi, y[-1] + slope * (t - x[-1]), out)
    return np.asarray(out, dtype=float)
