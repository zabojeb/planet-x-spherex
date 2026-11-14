"""2MASS point sources from IRSA (each position at its own observation date, ~1997-2001)."""

from __future__ import annotations

from ..archive.tap import tap_csv
from ..timeutil import jd_to_mjd, mjd_to_year
from .common import MAX_ROWS, circle, num

LABEL = "2MASS point sources (IRSA)"


def query(ra: float, dec: float, r: float) -> list[dict]:
    rows = tap_csv(f"""SELECT TOP {MAX_ROWS} designation, ra, dec, j_m, h_m, k_m, jdate
        FROM fp_psc WHERE {circle(ra, dec, r)} ORDER BY k_m""", timeout=240)
    out = []
    for x in rows:
        jd = num(x["jdate"])
        out.append({"id": f"2MASS J{x['designation']}", "ra": num(x["ra"]), "dec": num(x["dec"]),
                    "epoch": float(mjd_to_year(jd_to_mjd(jd))) if jd else 2000.0, "pmra": None, "pmdec": None,
                    "mag": num(x["k_m"]), "mag_band": "Ks", "j": num(x["j_m"]), "h": num(x["h_m"])})
    return out
