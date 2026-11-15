"""Level 2 images that cover a fixed sky point: the candidate templates of the static sky behind a moving object.

Two sources, tried in order:

1. The IRSA TAP footprint search (archive.query_frames), the same as fixed-position sequences. Its rows carry no
   footprint, so the wavelength at the point is predicted from the image header (one small read, cached).
2. IRSA MOST with a pseudo-object parked at the point ("fixed point"). MOST matches an ephemeris against the
   image footprints; a circular orbit with a = 1e5 au moves < 0.01" over the mission and its parallax is 2",
   so it stands still on the sky. The MOST workspace file irsa_catalog.tbl lists every image of the search region
   with its footprint corners (pixel corners (-0.5,-0.5), (nx-0.5,-0.5), (nx-0.5,ny-0.5), (-0.5,ny-0.5) in that
   order, checked against the WCS of real files), which locate the point on the detector without reading
   anything. MOST runs on a different IRSA service than TAP, which answered 502 for days in November 2026.

Answers are cached on disk under <cache>/solar/.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import date

import numpy as np

from .. import cache
from ..archive import Frame, query_frames
from ..errors import UpstreamError
from ..moving.most import MISSION_START, MOST_URL, MostRow
from ..moving import rows_to_frames

log = logging.getLogger(__name__)

DETECTOR_PIX = 2040  # SPHEREx Level 2 images are 2040 x 2040 pixels
CACHE_HOURS = 24 * 7
_OBLIQUITY = math.radians(23.4392911)  # J2000 mean obliquity; MPC elements are J2000 ecliptic


@dataclass
class SkyFrame:
    """A Level 2 image that covers a sky point, with its footprint corners when the source gives them."""

    frame: Frame
    corners: list[list[float]] | None = None  # 4 x [ra, dec] of the pixel corners, see the module docstring

    def to_dict(self) -> dict:
        return {"frame": asdict(self.frame), "corners": self.corners}

    @classmethod
    def from_dict(cls, d: dict) -> "SkyFrame":
        return cls(Frame(**d["frame"]), d.get("corners"))


# ---------------------------------------------------------------- footprint geometry
def _tangent(ra0: float, dec0: float, ra, dec) -> tuple[np.ndarray, np.ndarray]:
    """Gnomonic (xi, eta) in radians of (ra, dec) about (ra0, dec0), all in degrees."""
    a0, d0 = math.radians(ra0), math.radians(dec0)
    a, d = np.radians(ra), np.radians(dec)
    cosc = math.sin(d0) * np.sin(d) + math.cos(d0) * np.cos(d) * np.cos(a - a0)
    xi = np.cos(d) * np.sin(a - a0) / cosc
    eta = (math.cos(d0) * np.sin(d) - math.sin(d0) * np.cos(d) * np.cos(a - a0)) / cosc
    return xi, eta


def footprint_pixel(corners, ra: float, dec: float, nx: int = DETECTOR_PIX,
                    ny: int = DETECTOR_PIX) -> tuple[float, float, bool]:
    """Approximate 0-based pixel (x, y) of (ra, dec) in an image from its four footprint corners, and whether it
    falls inside. Inverts the bilinear map of the corners in the tangent plane (a few pixels of optical
    distortion remain; the LVF wavelength changes by one resolution element over ~120 pixels)."""
    c = np.asarray(corners, dtype=float)
    ra0, dec0 = float(np.mean(c[:, 0])), float(np.mean(c[:, 1]))
    if np.ptp(c[:, 0]) > 180:  # footprint across RA = 0
        ra0 = float(np.degrees(np.arctan2(np.mean(np.sin(np.radians(c[:, 0]))), np.mean(np.cos(np.radians(c[:, 0]))))) % 360)
    xi, eta = _tangent(ra0, dec0, c[:, 0], c[:, 1])
    p = np.array(_tangent(ra0, dec0, ra, dec), dtype=float)
    q = np.stack([xi, eta], axis=1)  # corners 0..3: (u,v) = (0,0), (1,0), (1,1), (0,1)
    u, v = 0.5, 0.5
    for _ in range(20):
        f = (q[0] * (1 - u) * (1 - v) + q[1] * u * (1 - v) + q[2] * u * v + q[3] * (1 - u) * v) - p
        du = -q[0] * (1 - v) + q[1] * (1 - v) + q[2] * v - q[3] * v
        dv = -q[0] * (1 - u) - q[1] * u + q[2] * u + q[3] * (1 - u)
        jac = np.stack([du, dv], axis=1)
        try:
            step = np.linalg.solve(jac, f)
        except np.linalg.LinAlgError:
            return float("nan"), float("nan"), False
        u, v = u - step[0], v - step[1]
        if abs(step[0]) + abs(step[1]) < 1e-9:
            break
    x, y = -0.5 + u * nx, -0.5 + v * ny
    return float(x), float(y), bool(0.0 <= u <= 1.0 and 0.0 <= v <= 1.0)


def contains(corners, ra: float, dec: float, margin_px: float = 0.0) -> bool:
    x, y, _ = footprint_pixel(corners, ra, dec)
    return bool(np.isfinite(x) and margin_px - 0.5 <= x <= DETECTOR_PIX - 0.5 - margin_px
                and margin_px - 0.5 <= y <= DETECTOR_PIX - 0.5 - margin_px)


# ---------------------------------------------------------------- MOST fixed point
def _ecliptic(ra: float, dec: float) -> tuple[float, float]:
    a, d = math.radians(ra), math.radians(dec)
    x, y, z = math.cos(d) * math.cos(a), math.cos(d) * math.sin(a), math.sin(d)
    ye = y * math.cos(_OBLIQUITY) + z * math.sin(_OBLIQUITY)
    ze = -y * math.sin(_OBLIQUITY) + z * math.cos(_OBLIQUITY)
    return math.degrees(math.atan2(ye, x)) % 360.0, math.degrees(math.asin(max(-1.0, min(1.0, ze))))


def fixed_point_elements(ra: float, dec: float, a_au: float = 1e5, h_mag: float = -35.0) -> str:
    """One-line MPC orbit of a pseudo-object that stays at (ra, dec): circular, a = 1e5 au, inclination 90 deg,
    node at the ecliptic longitude and argument of latitude equal to the latitude. H = -35 puts it at V ~ 15,
    inside MOST's magnitude cut."""
    lam, bet = _ecliptic(ra, dec)
    n = 0.9856076686 / a_au ** 1.5  # mean motion, deg/day
    return (f"STAT1 {h_mag:.1f} 0.15 K25AD 0.0000000 {bet % 360:.7f} {lam:.7f} 90.0000000 0.0000000 "
            f"{n:.9f} {a_au:.3f} 0 MPO000000 1 1 2025-2025 0.50 M-v 38h MPCLINUX 0000 (STAT1) static 20251013")


def parse_ipac_fixed(text: str) -> list[dict]:
    """Rows of a fixed-width IPAC table as dicts of strings (columns located by the '|' of the header)."""
    lines = [ln for ln in text.splitlines() if not ln.startswith("\\")]
    if not lines or "|" not in lines[0]:
        return []
    hdr = lines[0]
    pos = [i for i, ch in enumerate(hdr) if ch == "|"]
    names = [hdr[pos[k] + 1:pos[k + 1]].strip() for k in range(len(pos) - 1)]
    body = [ln for ln in lines[1:] if not ln.startswith("|") and ln.strip()]
    return [{names[k]: ln[pos[k] + 1:pos[k + 1]].strip() for k in range(len(names))} for ln in body]


def _fixed_cache(ra: float, dec: float, begin: str, end: str):
    key = hashlib.sha1(json.dumps([round(ra, 4), round(dec, 4), begin, end]).encode()).hexdigest()[:16]
    return cache.layout().root / "solar" / f"fixed_{key}.json"


def query_fixed_point(ra: float, dec: float, collections: tuple[str, ...] | None = None, begin: str | None = None,
                      end: str | None = None, timeout: float = 300, use_cache: bool = True) -> list[SkyFrame]:
    """Images that cover (ra, dec) according to IRSA MOST (fixed-point pseudo-object), with footprints."""
    begin, end = begin or MISSION_START, end or date.today().isoformat()
    path = _fixed_cache(ra, dec, begin, end)
    if use_cache and cache.is_fresh(path, CACHE_HOURS):
        out = [SkyFrame.from_dict(d) for d in json.loads(path.read_text())]
    else:
        q = {"catalog": "spherex", "input_type": "mpc_input", "obj_type": "Asteroid",
             "mpc_data": fixed_point_elements(ra, dec), "obs_begin": begin, "obs_end": end, "output_mode": "Brief"}
        try:
            with urllib.request.urlopen(MOST_URL + "?" + urllib.parse.urlencode(q), timeout=timeout) as r:
                brief = r.read().decode("utf-8", "replace")
            m = re.search(r'output_url\s*=\s*"(.*?)"', brief)
            if m is None:
                raise UpstreamError("IRSA MOST gave no workspace for the fixed-point search")
            with urllib.request.urlopen(m.group(1) + "/irsa_catalog.tbl", timeout=timeout) as r:
                table = r.read().decode("utf-8", "replace")
        except UpstreamError:
            raise
        except Exception as e:  # noqa: BLE001 - network failures
            raise UpstreamError(f"IRSA MOST fixed-point search failed: {e}") from e
        out = []
        for row in parse_ipac_fixed(table):
            uri, band = row.get("uri", ""), row.get("productid", "")
            if not uri.startswith("ibe/") or not band.startswith("D"):
                continue
            try:
                corners = [[float(row[f"ra{k}"]), float(row[f"dec{k}"])] for k in range(1, 5)]
                mr = MostRow(mjd_start=float(row["mjd_start"]), mjd_end=float(row["mjd_end"]), ra=ra, dec=dec,
                             band=band, uri=uri, obs_id=row.get("observationid", ""))
            except (KeyError, ValueError):
                continue
            fr = rows_to_frames([mr], tuple(collections or ()) or None)
            if fr:
                out.append(SkyFrame(fr[0], corners))
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps([s.to_dict() for s in out]))
        tmp.replace(path)
    return [s for s in out if s.corners is None or contains(s.corners, ra, dec)]


def frames_covering(ra: float, dec: float, collections: tuple[str, ...] | None = None) -> tuple[list[SkyFrame], str]:
    """Blocking (network). Images that cover (ra, dec) and which source answered ('tap' or 'most')."""
    try:
        frames = query_frames(ra, dec, collections)
        return [SkyFrame(f) for f in frames], "tap"
    except Exception as e:  # noqa: BLE001 - TAP down: fall back to MOST
        log.warning("TAP footprint search failed (%s); using the MOST fixed-point search", str(e)[:120])
    return query_fixed_point(ra, dec, collections), "most"
