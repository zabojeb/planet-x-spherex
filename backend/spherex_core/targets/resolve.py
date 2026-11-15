"""Turn user input into a Target: decimal or sexagesimal coordinates, preset ids, or names.

Names are looked up in order: presets, the local object index (objects.py, no network), answers cached on
disk, then CDS Sesame, asking all mirrors at once. Sesame answers are cached for RESOLVE_CACHE_DAYS, and
unknown names for a few minutes in memory, so a repeated search never waits on CDS.
"""

from __future__ import annotations

import json
import logging
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict

from astropy import units as u
from astropy.coordinates import SkyCoord

from .. import cache, upstreams
from ..config import settings
from .model import Target
from .objects import compact, index
from .presets import PRESETS

log = logging.getLogger(__name__)

# Asked together with settings.sesame_url (which may itself list several URLs, comma separated). The CDS
# address redirects to a mirror when CDS is down; the CfA mirror answers directly.
SESAME_MIRRORS = ("https://vizier.cfa.harvard.edu/viz-bin/nph-sesame/-oxp/~SNV",)
RESOLVE_CACHE_DAYS = 30
MISS_TTL_S = 600
_misses: dict[str, float] = {}
_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="sesame")

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


def sesame_urls() -> list[str]:
    urls = [u.strip() for u in settings.sesame_url.split(",") if u.strip()]
    return urls + [m for m in SESAME_MIRRORS if m not in urls]


def _parse_sesame(xml: bytes, name: str) -> Target | None:
    root = ET.fromstring(xml)
    for res in root.iter("Resolver"):
        ra, dec = _num(res, "jradeg"), _num(res, "jdedeg")
        if ra is None or dec is None:
            continue
        pm = res.find("pm")
        plx = res.find("plx")
        otype = (res.findtext("otype") or "").strip()
        sp = (res.findtext("spType") or "").strip()
        return Target(
            ra=ra, dec=dec, name=" ".join((res.findtext("oname") or name).split()).removeprefix("NAME "),
            pmra=_num(pm, "pmRA") or 0.0, pmdec=_num(pm, "pmDE") or 0.0, ref_epoch=2000.0,
            parallax=_num(plx, "v"), source=f"Sesame ({res.get('name', '').split('(')[0].strip()})",
            description=" · ".join(x for x in (otype, sp) if x) or None,
        )
    return None


def _endpoint(host: str) -> upstreams.Endpoint:
    """The Sesame mirror at `host` as an upstreams endpoint (kind "names"; statistics only, they are raced)."""
    label = "cds" if "unistra" in host else host.split(".")[-2] if host.count(".") >= 1 else host
    ep = upstreams.Endpoint(f"sesame-{label}", "names", host, f"Sesame ({host})", note="all mirrors raced")
    upstreams.register(ep)
    return ep


def _ask(base: str, name: str, timeout: float) -> Target | None:
    host = urllib.parse.urlsplit(base).netloc
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(base + "?" + urllib.parse.quote(name.strip()), timeout=timeout) as r:
            t = _parse_sesame(r.read(), name)
    except Exception as e:
        log.warning("sesame failed s=%.2f host=%s name=%r error=%s", time.perf_counter() - t0, host, name, e)
        upstreams.record(_endpoint(host), time.perf_counter() - t0, False, e, breaker=False)
        raise RuntimeError(f"{host}: {e}") from e
    log.info("sesame done s=%.2f host=%s name=%r found=%s", time.perf_counter() - t0, host, name, t is not None)
    upstreams.record(_endpoint(host), time.perf_counter() - t0, True, breaker=False)
    return t


def query_sesame(name: str, timeout: float = 8.0) -> Target:
    """Ask Sesame (SIMBAD, NED, VizieR) on every mirror at once and take the first answer: when one mirror is
    slow or down (CDS was, during the hackathon) the lookup costs what the fastest one does. An answer without
    a position is final, since all mirrors serve the same databases. LookupError if unknown or unreachable."""
    futures = [_pool.submit(_ask, base, name, timeout) for base in sesame_urls()]
    errors = []
    for f in as_completed(futures):
        try:
            t = f.result()
        except RuntimeError as e:
            errors.append(str(e))
            continue
        for other in futures:
            other.cancel()
        if t is None:
            raise LookupError(f"Unknown object: {name!r}")
        return t
    raise LookupError(f"Name resolver unavailable ({'; '.join(errors)})")


def resolve_name(name: str, timeout: float = 8.0) -> Target:
    """Preset id/name, local object index, cache, else Sesame. Raises LookupError if unknown or unreachable."""
    key = name.strip().lower().replace("’", "'")
    for t in PRESETS.values():
        if key in (t.id, (t.name or "").lower()):
            return t
    local = index().lookup(name)
    if local is not None:
        return local
    ckey = compact(name) or key
    path = cache.layout().resolve(ckey)
    if cache.is_fresh(path, RESOLVE_CACHE_DAYS * 24):
        try:
            return Target(**json.loads(path.read_text()))
        except (OSError, ValueError, TypeError):
            pass
    if time.monotonic() - _misses.get(ckey, -1e9) < MISS_TTL_S:
        raise LookupError(f"Unknown object: {name!r}")
    try:
        t = query_sesame(name, timeout=timeout)
    except LookupError as e:
        if str(e).startswith("Unknown object"):
            if len(_misses) > 4096:
                _misses.clear()
            _misses[ckey] = time.monotonic()
        raise
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(t)))
        tmp.replace(path)
    except OSError as e:
        log.warning("cannot cache name lookup %r: %s", name, e)
    return t


def resolve(query: str) -> Target:
    """Coordinates if the query parses as coordinates, otherwise a name lookup."""
    t = parse_coordinates(query)
    if t is not None:
        t.name = query.strip()
        return t
    return resolve_name(query)
