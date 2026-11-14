"""Helpers shared by the catalog modules. Every source dict has: id, ra, dec, epoch (Julian year of the
position), pmra, pmdec (mas/yr, pmra includes cos dec; None if unknown), mag, mag_band."""

from __future__ import annotations

MAX_ROWS = 3000


def num(v) -> float | None:
    """Float from a CSV cell, None for empty or non-numeric cells."""
    try:
        return float(v) if v not in (None, "") else None
    except ValueError:
        return None


def circle(ra: float, dec: float, r: float) -> str:
    """ADQL cone constraint on columns named ra/dec."""
    return f"1 = CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS', {ra:.7f}, {dec:.7f}, {r:.6f}))"
