"""Time conversions in one place: MJD, Julian year (epochs of positions) and display strings."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np

MJD_J2000 = 51544.5
_MJD_ZERO = datetime(1858, 11, 17)


def mjd_to_year(mjd):
    """Julian year(s) for MJD value(s); accepts scalars or arrays."""
    return 2000.0 + (np.asarray(mjd, dtype=float) - MJD_J2000) / 365.25


def year_to_mjd(year):
    return MJD_J2000 + (np.asarray(year, dtype=float) - 2000.0) * 365.25


def jd_to_mjd(jd: float) -> float:
    return float(jd) - 2400000.5


def mjd_to_iso(mjd: float) -> str:
    """'YYYY-MM-DD HH:MM' (UTC), the format used in meta.json."""
    return (_MJD_ZERO + timedelta(days=float(mjd))).strftime("%Y-%m-%d %H:%M")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
