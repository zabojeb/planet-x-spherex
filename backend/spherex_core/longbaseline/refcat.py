"""Calibration catalogues with fallbacks, so a slow or failing IRSA does not cost the cross-calibration.

W1 / W2 reference: CatWISE2020 from IRSA (catalogs.cone), else AllWISE from a VizieR mirror (II/328; CatWISE2020
photometry is tied to AllWISE, so unsaturated stars agree to ~0.01 mag), else Gaia DR3 stars without a catalogue
magnitude: frame zero points are then skipped (the enclosed-energy correction stays) while SPHEREx is still tied to
our NEOWISE photometry of the same stars. J / H / Ks reference: the 2MASS PSC from IRSA, else from VizieR (II/246).
Mirror answers are cached like catalogs.cone answers.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.parse
import urllib.request

from .. import cache, catalogs, netpolicy
from ..timeutil import jd_to_mjd, mjd_to_year

log = logging.getLogger(__name__)
VIZIER_MIRRORS = ("https://vizier.cfa.harvard.edu", "https://vizier.cds.unistra.fr")
ALLWISE_EPOCH = 2010.3  # mean AllWISE epoch of the positions (W1/W2 cryo + post-cryo)
MAX_ROWS = 3000


def _num(v: str) -> float | None:
    try:
        return float(v) if v.strip() else None
    except ValueError:
        return None


def parse_asu_tsv(text: str) -> list[dict]:
    """Rows of a VizieR asu-tsv answer: comment lines, a header, a units line, a dashes line, then data."""
    lines = [ln for ln in text.splitlines() if ln and not ln.startswith("#")]
    if len(lines) < 3:
        return []
    header = [h.strip() for h in lines[0].split("\t")]
    rows = []
    for ln in lines[3:]:
        cells = ln.split("\t")
        if len(cells) == len(header):
            rows.append({h: c.strip() for h, c in zip(header, cells)})
    return rows


def vizier_cone(source: str, ra: float, dec: float, radius_deg: float, columns: list[str],
                timeout: float = 60.0) -> list[dict]:
    """A cone on a VizieR table, trying each mirror in turn."""
    params = {"-source": source, "-c": f"{ra:.6f} {dec:+.6f}", "-c.rs": f"{radius_deg * 3600:.1f}",
              "-out.max": str(MAX_ROWS), "-out": ",".join(columns)}
    last: Exception | None = None
    for base in VIZIER_MIRRORS:
        url = f"{base}/viz-bin/asu-tsv?" + urllib.parse.urlencode(params)
        service = urllib.parse.urlsplit(base).netloc
        t0 = time.perf_counter()
        try:
            netpolicy.check(service)
            t, _ = netpolicy.timeouts(timeout, 1)
            with urllib.request.urlopen(url, timeout=t) as r:
                text = r.read().decode("utf-8", "replace")
            if "\t" not in text or text.lstrip().startswith("<"):
                raise RuntimeError(f"not a table: {text[:120]!r}")
            rows = parse_asu_tsv(text)
            log.info("vizier %s done s=%.2f service=%s rows=%d", source, time.perf_counter() - t0, service, len(rows))
            netpolicy.succeeded(service)
            return rows
        except Exception as e:  # noqa: BLE001 - next mirror
            last = e
            log.warning("vizier %s failed s=%.2f service=%s error=%s", source, time.perf_counter() - t0, service,
                        str(e).splitlines()[0][:160] if str(e) else e.__class__.__name__)
            netpolicy.failed(service, e)
    raise RuntimeError(f"VizieR {source} unavailable: {last}")


def _cached(name: str, ra: float, dec: float, radius_deg: float, fetch) -> list[dict]:
    path = cache.layout().catalog(name, ra, dec, radius_deg)
    if cache.is_fresh(path, 24 * 7):
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            pass
    rows = fetch()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(rows))
    tmp.replace(path)
    return rows


def allwise(ra: float, dec: float, radius_deg: float) -> list[dict]:
    """AllWISE sources in the catalogs.cone CatWISE shape (mag = W2, w1 = W1), at their 2010.3 positions without
    motion: AllWISE's apparent motions are dominated by systematics (several hundred mas/yr at 5-13 sigma for
    ordinary field stars), which would walk apertures off the sources over the 14 NEOWISE-era years. Ordinary
    field stars move < 0.2" over that span; the target's own motion comes from the request or Gaia DR3."""
    def fetch():
        rows = vizier_cone("II/328/allwise", ra, dec, radius_deg,
                           ["AllWISE", "RAJ2000", "DEJ2000", "W1mag", "W2mag", "ccf", "ex"])
        out = []
        for x in rows:
            r, d = _num(x.get("RAJ2000", "")), _num(x.get("DEJ2000", ""))
            if r is None or d is None:
                continue
            out.append({"id": f"AllWISE {x.get('AllWISE', '').strip()}", "ra": r, "dec": d, "epoch": ALLWISE_EPOCH,
                        "pmra": 0.0, "pmdec": 0.0,
                        "mag": _num(x.get("W2mag", "")), "mag_band": "W2", "w1": _num(x.get("W1mag", "")),
                        "ccf": x.get("ccf"), "extended": x.get("ex") not in (None, "", "0")})
        return out

    return _cached("allwise-vizier-v3", ra, dec, radius_deg, fetch)


def twomass_vizier(ra: float, dec: float, radius_deg: float) -> list[dict]:
    """2MASS PSC sources in the catalogs.cone shape (mag = Ks, j, h; epoch = observation date)."""
    def fetch():
        rows = vizier_cone("II/246/out", ra, dec, radius_deg, ["2MASS", "RAJ2000", "DEJ2000", "Jmag", "Hmag", "Kmag",
                                                               "JD"])
        out = []
        for x in rows:
            r, d = _num(x.get("RAJ2000", "")), _num(x.get("DEJ2000", ""))
            if r is None or d is None:
                continue
            jd = _num(x.get("JD", ""))
            out.append({"id": f"2MASS J{x.get('2MASS', '').strip()}", "ra": r, "dec": d,
                        "epoch": float(mjd_to_year(jd_to_mjd(jd))) if jd else 2000.0, "pmra": None, "pmdec": None,
                        "mag": _num(x.get("Kmag", "")), "mag_band": "Ks", "j": _num(x.get("Jmag", "")),
                        "h": _num(x.get("Hmag", ""))})
        return out

    return _cached("2mass-vizier", ra, dec, radius_deg, fetch)


def wise_reference(ra: float, dec: float, radius_deg: float, notes: list[str]) -> tuple[list[dict], str]:
    """(sources, catalogue label): CatWISE2020, else AllWISE, else Gaia DR3 (no W1/W2), else nothing."""
    try:
        return catalogs.cone("catwise", ra, dec, radius_deg), "CatWISE2020"
    except Exception as e:  # noqa: BLE001
        reason = str(e)[:100]
    try:
        rows = allwise(ra, dec, radius_deg)
        notes.append(f"CatWISE2020 unavailable ({reason}); calibration stars and W1/W2 reference from AllWISE")
        return rows, "AllWISE"
    except Exception as e2:  # noqa: BLE001
        reason2 = str(e2)[:80]
    try:
        rows = catalogs.cone("gaia", ra, dec, radius_deg)
        notes.append(f"CatWISE2020 ({reason}) and AllWISE ({reason2}) unavailable: calibration stars from Gaia DR3, "
                     "archival frames keep the enclosed-energy scale")
        return rows, "Gaia DR3"
    except Exception as e3:  # noqa: BLE001
        notes.append(f"no calibration catalogue answered (CatWISE2020: {reason}; Gaia: {str(e3)[:80]}): default "
                     "calibration")
        return [], ""


def twomass_reference(ra: float, dec: float, radius_deg: float, notes: list[str]) -> tuple[list[dict], str]:
    try:
        return catalogs.cone("2mass", ra, dec, radius_deg), "2MASS PSC (IRSA)"
    except Exception as e:  # noqa: BLE001
        reason = str(e)[:100]
    try:
        rows = twomass_vizier(ra, dec, radius_deg)
        notes.append(f"2MASS PSC from VizieR (IRSA: {reason})")
        return rows, "2MASS PSC (VizieR)"
    except Exception as e2:  # noqa: BLE001
        notes.append(f"2MASS PSC unavailable (IRSA: {reason}; VizieR: {str(e2)[:80]})")
        return [], ""


__all__ = ["allwise", "parse_asu_tsv", "twomass_reference", "twomass_vizier", "vizier_cone", "wise_reference"]
