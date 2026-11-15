"""CatWISE2020 sources (positions at MJD 57170, motions measured over WISE/NEOWISE) from IRSA, VizieR or NOIRLab
Data Lab, whichever answers best (endpoints.py)."""

from __future__ import annotations

import functools

from .. import endpoints, upstreams
from ..archive.tap import tap_csv
from ..config import settings
from ..timeutil import mjd_to_year
from .common import MAX_ROWS, box, circle, in_circle, num

LABEL = "CatWISE2020"
REFERENCE_MJD = 57170.0


def _row(name, ra, dec, pmra_mas, pmdec_mas, w1, w2) -> dict:
    return {"id": f"CatWISE {name}", "ra": num(ra), "dec": num(dec), "epoch": float(mjd_to_year(REFERENCE_MJD)),
            "pmra": pmra_mas, "pmdec": pmdec_mas, "mag": num(w2), "mag_band": "W2", "w1": num(w1)}


def _irsa(ra, dec, r) -> list[dict]:
    rows = tap_csv(f"""SELECT TOP {MAX_ROWS} source_name, ra, dec, pmra, pmdec, w1mpro, w2mpro
        FROM catwise_2020 WHERE {circle(ra, dec, r)} ORDER BY w2mpro""", timeout=240)
    return _irsa_rows(rows)


def _irsa_rows(rows: list[dict]) -> list[dict]:
    out = []
    for x in rows:
        pmra, pmdec = num(x["pmra"]), num(x["pmdec"])
        out.append(_row(x["source_name"], x["ra"], x["dec"], pmra * 1000 if pmra is not None else None,  # arcsec/yr
                        pmdec * 1000 if pmdec is not None else None, x["w1mpro"], x["w2mpro"]))
    return out


@functools.lru_cache(maxsize=1)
def _vizier_pm_scale() -> float:
    """VizieR may store CatWISE motions in arcsec/yr or mas/yr: read the unit once from TAP_SCHEMA."""
    rows = tap_csv("SELECT column_name, unit FROM TAP_SCHEMA.columns WHERE table_name = 'II/365/catwise' "
                   "AND column_name = 'pmRA'", url=settings.vizier_tap_url, timeout=60, retries=1)
    unit = (rows[0].get("unit") or "").strip().lower() if rows else ""
    return 1.0 if "mas" in unit else 1000.0


def _vizier(ra, dec, r) -> list[dict]:
    scale = _vizier_pm_scale()
    rows = tap_csv(f"""SELECT TOP {MAX_ROWS} Name, RA_ICRS, DE_ICRS, pmRA, pmDE, W1mproPM, W2mproPM
        FROM "II/365/catwise" WHERE {circle(ra, dec, r, 'RA_ICRS', 'DE_ICRS')} ORDER BY W2mproPM""",
                   url=settings.vizier_tap_url, timeout=120)
    out = []
    for x in rows:
        pmra, pmdec = num(x["pmRA"]), num(x["pmDE"])
        out.append(_row(x["Name"], x["RA_ICRS"], x["DE_ICRS"], pmra * scale if pmra is not None else None,
                        pmdec * scale if pmdec is not None else None, x["W1mproPM"], x["W2mproPM"]))
    return out


def _datalab(ra, dec, r) -> list[dict]:  # IRSA's column names and units (motions in arcsec/yr); box query
    rows = tap_csv(f"""SELECT TOP {MAX_ROWS} source_name, ra, dec, pmra, pmdec, w1mpro, w2mpro
        FROM catwise2020.main WHERE {box(ra, dec, r)} ORDER BY w2mpro""", url=settings.datalab_tap_url, timeout=120)
    return _irsa_rows([x for x in rows if in_circle(ra, dec, r, num(x["ra"]), num(x["dec"]))])


def query(ra: float, dec: float, r: float) -> list[dict]:
    source = {endpoints.CATWISE_IRSA: _irsa, endpoints.CATWISE_VIZIER: _vizier, endpoints.CATWISE_DATALAB: _datalab}
    rows, _ = upstreams.call("catwise", lambda ep: source[ep](ra, dec, r))
    return rows
