"""Named objects from SIMBAD (CDS): name, object type, spectral type, motion and a few magnitudes.

SIMBAD positions are ICRS at epoch J2000. Rows come most-studied first (number of references), so the
3,000-row cap keeps the objects people know. The service has mirrors; they are tried in order, so the
overlay keeps working when one of them is down (CDS itself was unreachable on 2026-11-14).
"""

from __future__ import annotations

from ..archive.tap import tap_csv
from ..config import settings
from .common import MAX_ROWS, num

LABEL = "SIMBAD (CDS)"


def _mag(x: dict) -> tuple[float | None, str]:
    """The magnitude shown for an object: V, else Gaia G, else 2MASS J, else K."""
    for band in ("V", "G", "J", "K"):
        v = num(x.get(band))
        if v is not None:
            return v, band
    return None, ""


def query(ra: float, dec: float, r: float) -> list[dict]:
    adql = f"""SELECT TOP {MAX_ROWS} b.main_id, b.ra, b.dec, b.pmra, b.pmdec, b.plx_value, b.otype,
        d.description, b.sp_type, b.rvz_redshift, b.nbref AS refs, f.V, f.G, f.J, f.K
        FROM basic AS b LEFT JOIN allfluxes AS f ON f.oidref = b.oid LEFT JOIN otypedef AS d ON d.otype = b.otype
        WHERE 1 = CONTAINS(POINT('ICRS', b.ra, b.dec), CIRCLE('ICRS', {ra:.7f}, {dec:.7f}, {r:.6f}))
        ORDER BY refs DESC"""
    errors = []
    for url in settings.simbad_tap_urls:
        try:
            rows = tap_csv(adql, url=url, timeout=60, retries=1)
            break
        except RuntimeError as e:
            errors.append(str(e).splitlines()[0][:120])
    else:
        raise RuntimeError("; ".join(errors) or "no SIMBAD service configured")
    out = []
    for x in rows:
        if num(x["ra"]) is None or num(x["dec"]) is None:
            continue
        mag, band = _mag(x)
        name = " ".join(x["main_id"].split())
        out.append({
            "id": name.removeprefix("NAME "), "ra": num(x["ra"]), "dec": num(x["dec"]), "epoch": 2000.0,
            "pmra": num(x["pmra"]), "pmdec": num(x["pmdec"]), "parallax": num(x["plx_value"]),
            "mag": mag, "mag_band": band, "otype": x["otype"] or None, "type": x["description"] or None,
            "sp_type": x["sp_type"] or None, "z": num(x["rvz_redshift"]), "refs": int(num(x["refs"]) or 0),
        })
    return out
