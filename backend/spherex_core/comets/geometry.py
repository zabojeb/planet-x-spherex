"""Sky geometry for comet discovery: the Sun's direction, position angles, the observer's heliocentric track.

* `sun_radec`, `elongation`, `antisolar_pa`: the projected anti-solar direction at a position does not depend on
  the object's distance (the component of the heliocentric radius vector perpendicular to the line of sight is the
  observer's own, so a tail points away from the Sun's apparent position along the great circle). Positions of
  the Sun and the Earth come from astropy's built-in ephemeris (no network).
* `observer_helio`: SPHEREx's heliocentric position (au, ICRS) = Earth's + the spacecraft's geocentric offset from
  JPL Horizons (field.observer, cached); without Horizons the geocentre, flagged.
* `parallax_basis`: per time the apparent offset (east, north, arcsec) that the observer's displacement since t_ref
  gives an object at 1 au; an object at distance D au is displaced by basis / D. Its 98-min part is SPHEREx's
  orbital wobble (9.7"/D), its slow curvature Earth's orbit; the linear part is absorbed by a fitted motion.
"""

from __future__ import annotations

import logging
import math

import numpy as np

log = logging.getLogger(__name__)

AU_KM = 149_597_870.7
ARCSEC_PER_RAD = 206264.806
OBLIQUITY = math.radians(23.4392911)
# equatorial (ICRS) -> ecliptic (J2000) rotation
EQ_TO_ECL = np.array([[1.0, 0.0, 0.0],
                      [0.0, math.cos(OBLIQUITY), math.sin(OBLIQUITY)],
                      [0.0, -math.sin(OBLIQUITY), math.cos(OBLIQUITY)]])


def unit(ra, dec) -> np.ndarray:
    a, d = np.radians(ra), np.radians(dec)
    return np.stack([np.cos(d) * np.cos(a), np.cos(d) * np.sin(a), np.sin(d)], axis=-1)


def radec(v) -> tuple[np.ndarray, np.ndarray]:
    v = np.asarray(v, float)
    r = np.linalg.norm(v, axis=-1)
    return np.degrees(np.arctan2(v[..., 1], v[..., 0])) % 360.0, np.degrees(np.arcsin(np.clip(v[..., 2] / r, -1, 1)))


def tangent_basis(ra: float, dec: float) -> tuple[np.ndarray, np.ndarray]:
    """Unit vectors towards local east and north at (ra, dec)."""
    a, d = math.radians(ra), math.radians(dec)
    east = np.array([-math.sin(a), math.cos(a), 0.0])
    north = np.array([-math.sin(d) * math.cos(a), -math.sin(d) * math.sin(a), math.cos(d)])
    return east, north


def pa_of(vec_east: float, vec_north: float) -> float:
    """Position angle (deg east of north) of a sky vector."""
    return float(math.degrees(math.atan2(vec_east, vec_north)) % 360.0)


def pa_toward(ra0: float, dec0: float, ra1: float, dec1: float) -> float:
    """Position angle at (ra0, dec0) of the great circle towards (ra1, dec1)."""
    e, n = tangent_basis(ra0, dec0)
    u = unit(ra1, dec1)
    return pa_of(float(u @ e), float(u @ n))


def angle_diff(a: float, b: float) -> float:
    """|a - b| folded to 0..180 deg."""
    return abs((a - b + 180.0) % 360.0 - 180.0)


def _time(mjd):
    from astropy.time import Time

    return Time(np.atleast_1d(np.asarray(mjd, float)), format="mjd", scale="utc")


def earth_helio(mjd) -> tuple[np.ndarray, np.ndarray]:
    """Earth's heliocentric position (au) and velocity (au/day), ICRS axes, shape [k, 3]."""
    from astropy.coordinates import get_body_barycentric_posvel

    t = _time(mjd)
    pe, ve = get_body_barycentric_posvel("earth", t)
    ps, vs = get_body_barycentric_posvel("sun", t)
    p = (pe - ps).xyz.to_value("au").T
    v = (ve - vs).xyz.to_value("au/day").T
    return np.atleast_2d(p), np.atleast_2d(v)


def sun_radec(mjd) -> tuple[np.ndarray, np.ndarray]:
    """Apparent direction of the Sun from the Earth (geocentric; SPHEREx's 7,000 km offset is negligible)."""
    p, _ = earth_helio(mjd)
    return radec(-p)


def elongation(ra: float, dec: float, mjd: float) -> float:
    """Solar elongation (deg) of a position at a time."""
    sra, sdec = sun_radec(mjd)
    c = float(unit(ra, dec) @ unit(float(sra[0]), float(sdec[0])))
    return float(math.degrees(math.acos(max(-1.0, min(1.0, c)))))


def antisolar_pa(ra: float, dec: float, mjd: float) -> float:
    """Position angle of the projected anti-solar direction (where an ion tail points; Horizons' PsAng)."""
    sra, sdec = sun_radec(mjd)
    return (pa_toward(ra, dec, float(sra[0]), float(sdec[0])) + 180.0) % 360.0


def ecliptic(ra, dec) -> tuple[np.ndarray, np.ndarray]:
    """Ecliptic longitude and latitude (deg, J2000) of equatorial positions."""
    v = unit(ra, dec) @ EQ_TO_ECL.T
    return radec(v)


def observer_helio(mjds, spacecraft: bool = True) -> tuple[np.ndarray, np.ndarray, str]:
    """SPHEREx heliocentric position (au) and velocity (au/day), ICRS, [k, 3]; and the observer used
    ('SPHEREx' or 'geocentre' when Horizons cannot be reached)."""
    mjds = np.atleast_1d(np.asarray(mjds, float))
    p, v = earth_helio(mjds)
    if not spacecraft:
        return p, v, "geocentre"
    try:
        from ..field import observer

        st = observer.states(mjds)
        off = np.array([st[observer._key(m)] for m in mjds], float)
    except Exception as e:  # noqa: BLE001 - Horizons down: the geocentre, 9.7"/D off
        log.warning("SPHEREx states unavailable (%r); using the geocentre", e)
        return p, v, "geocentre"
    return p + off[:, :3] / AU_KM, v + off[:, 3:] * 86400.0 / AU_KM, "SPHEREx"


def parallax_basis(mjds, ra: float, dec: float, t_ref: float, spacecraft: bool = True
                   ) -> tuple[np.ndarray, str]:
    """[k, 2] apparent (east, north) offsets in arcsec of an object at 1 au caused by the observer's displacement
    since t_ref (an object at D au moves by basis / D); and the observer used."""
    mjds = np.atleast_1d(np.asarray(mjds, float))
    o, _, who = observer_helio(np.append(mjds, t_ref), spacecraft)
    d = o[:-1] - o[-1]
    e, n = tangent_basis(ra, dec)
    return -np.stack([d @ e, d @ n], axis=1) * ARCSEC_PER_RAD, who
