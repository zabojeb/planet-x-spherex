"""2MASS point sources (each position at its own observation date, ~1997-2001) from IRSA, VizieR (TAP at CDS, ASU
at the CfA mirror) or NOIRLab Data Lab, whichever answers best (endpoints.py)."""

from __future__ import annotations

from .. import endpoints, upstreams
from ..archive.tap import tap_csv
from ..config import settings
from ..timeutil import jd_to_mjd, mjd_to_year
from .common import MAX_ROWS, box, circle, in_circle, num, vizier_asu

LABEL = "2MASS point sources"


def _row(name, ra, dec, jd, j, h, k) -> dict:
    jd = num(jd)
    return {"id": f"2MASS J{name.strip()}", "ra": num(ra), "dec": num(dec),
            "epoch": float(mjd_to_year(jd_to_mjd(jd))) if jd else 2000.0, "pmra": None, "pmdec": None,
            "mag": num(k), "mag_band": "Ks", "j": num(j), "h": num(h)}


def _irsa(ra, dec, r) -> list[dict]:
    rows = tap_csv(f"""SELECT TOP {MAX_ROWS} designation, ra, dec, j_m, h_m, k_m, jdate
        FROM fp_psc WHERE {circle(ra, dec, r)} ORDER BY k_m""", timeout=240)
    return [_row(x["designation"], x["ra"], x["dec"], x["jdate"], x["j_m"], x["h_m"], x["k_m"]) for x in rows]


def _vizier(ra, dec, r) -> list[dict]:
    rows = tap_csv(f"""SELECT TOP {MAX_ROWS} "2MASS", RAJ2000, DEJ2000, Jmag, Hmag, Kmag, JD
        FROM "II/246/out" WHERE {circle(ra, dec, r, 'RAJ2000', 'DEJ2000')} ORDER BY Kmag""",
                   url=settings.vizier_tap_url, timeout=120)
    return [_row(x["2MASS"], x["RAJ2000"], x["DEJ2000"], x.get("JD"), x["Jmag"], x["Hmag"], x["Kmag"]) for x in rows]


def _vizier_cfa(ra, dec, r) -> list[dict]:
    rows, _ = vizier_asu("II/246/out", ra, dec, r, ["2MASS", "RAJ2000", "DEJ2000", "Jmag", "Hmag", "Kmag", "JD"],
                         sort="Kmag")
    return [_row(x["2MASS"], x["RAJ2000"], x["DEJ2000"], x.get("JD"), x["Jmag"], x["Hmag"], x["Kmag"]) for x in rows]


def _datalab(ra, dec, r) -> list[dict]:  # same columns as IRSA's fp_psc; no ADQL geometry there
    rows = tap_csv(f"""SELECT TOP {MAX_ROWS} designation, ra, dec, j_m, h_m, k_m, jdate
        FROM twomass.psc WHERE {box(ra, dec, r)} ORDER BY k_m""", url=settings.datalab_tap_url, timeout=120)
    return [_row(x["designation"], x["ra"], x["dec"], x["jdate"], x["j_m"], x["h_m"], x["k_m"]) for x in rows
            if in_circle(ra, dec, r, num(x["ra"]), num(x["dec"]))]


def query(ra: float, dec: float, r: float) -> list[dict]:
    source = {endpoints.TWOMASS_IRSA: _irsa, endpoints.TWOMASS_VIZIER: _vizier, endpoints.TWOMASS_CFA: _vizier_cfa,
              endpoints.TWOMASS_DATALAB: _datalab}
    rows, _ = upstreams.call("2mass", lambda ep: source[ep](ra, dec, r))
    return rows
