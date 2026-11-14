"""Turn user input into a Target: decimal or sexagesimal coordinates, preset ids, or names (CDS Sesame)."""

from __future__ import annotations

import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from astropy import units as u
from astropy.coordinates import SkyCoord

from ..config import settings
from .model import Target
from .presets import PRESETS

_DECIMAL = re.compile(r"^[+-]?\d+(\.\d*)?$")


def parse_coordinates(text: str) -> Target | None:
    """Parse 'ra dec' in decimal degrees or sexagesimal ('17:57:48.5 +04:41:36', '17h57m48.5s 4d41m36s')."""
    t = text.strip().replace(",", " ")
    parts = t.split()
    try:
        if len(parts) == 2 and all(_DECIMAL.match(p) for p in parts):
            ra, dec = float(parts[0]), float(parts[1])
            if 0 <= ra < 360 and -90 <= dec <= 90:
                return Target(ra=ra, dec=dec)
            return None
        if re.search(r"\d[:hH ]\s*\d", t) and len(parts) >= 2:
            c = SkyCoord(t, unit=(u.hourangle, u.deg), frame="icrs")
            return Target(ra=float(c.ra.deg), dec=float(c.dec.deg))
    except (ValueError, TypeError):
        return None
    return None


def _num(el: ET.Element | None, tag: str) -> float | None:
    if el is None:
        return None
    txt = el.findtext(tag)
    try:
        return float(txt) if txt not in (None, "") else None
    except ValueError:
        return None


def resolve_name(name: str, timeout: float = 20.0) -> Target:
    """Preset id/name, else CDS Sesame (SIMBAD, NED, VizieR). Raises LookupError if unknown or unreachable."""
    key = name.strip().lower().replace("’", "'")
    for t in PRESETS.values():
        if key in (t.id, (t.name or "").lower()):
            return t
    url = settings.sesame_url + "?" + urllib.parse.quote(name.strip())
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            root = ET.fromstring(r.read())
    except Exception as e:  # noqa: BLE001 - surface network problems as lookup failures
        raise LookupError(f"Name resolver unavailable: {e}") from e
    for res in root.iter("Resolver"):
        ra, dec = _num(res, "jradeg"), _num(res, "jdedeg")
        if ra is None or dec is None:
            continue
        pm = res.find("pm")
        plx = res.find("plx")
        return Target(
            ra=ra, dec=dec, name=(res.findtext("oname") or name).strip(),
            pmra=_num(pm, "pmRA") or 0.0, pmdec=_num(pm, "pmDE") or 0.0, ref_epoch=2000.0,
            parallax=_num(plx, "v"), source=f"Sesame ({res.get('name', '').split('(')[0].strip()})",
        )
    raise LookupError(f"Unknown object: {name!r}")


def resolve(query: str) -> Target:
    """Coordinates if the query parses as coordinates, otherwise a name lookup."""
    t = parse_coordinates(query)
    if t is not None:
        t.name = query.strip()
        return t
    return resolve_name(query)
