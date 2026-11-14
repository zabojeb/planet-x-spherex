"""CatWISE2020 sources from IRSA (positions at MJD 57170, motions measured over WISE/NEOWISE)."""

from __future__ import annotations

from ..archive.tap import tap_csv
from ..timeutil import mjd_to_year
from .common import MAX_ROWS, circle, num

LABEL = "CatWISE2020 (IRSA)"
REFERENCE_MJD = 57170.0


def query(ra: float, dec: float, r: float) -> list[dict]:
    rows = tap_csv(f"""SELECT TOP {MAX_ROWS} source_name, ra, dec, pmra, pmdec, w1mpro, w2mpro
        FROM catwise_2020 WHERE {circle(ra, dec, r)} ORDER BY w2mpro""", timeout=240)
    epoch = float(mjd_to_year(REFERENCE_MJD))
    out = []
    for x in rows:
        pmra, pmdec = num(x["pmra"]), num(x["pmdec"])
        out.append({"id": f"CatWISE {x['source_name']}", "ra": num(x["ra"]), "dec": num(x["dec"]), "epoch": epoch,
                    "pmra": pmra * 1000 if pmra is not None else None,  # arcsec/yr -> mas/yr
                    "pmdec": pmdec * 1000 if pmdec is not None else None,
                    "mag": num(x["w2mpro"]), "mag_band": "W2", "w1": num(x["w1mpro"])})
    return out
