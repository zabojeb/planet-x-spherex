"""Gaia DR3 sources from the ESA Gaia archive (positions at epoch 2016.0)."""

from __future__ import annotations

from ..archive.tap import tap_csv
from ..config import settings
from .common import MAX_ROWS, circle, num

LABEL = "Gaia DR3 (ESA)"


def query(ra: float, dec: float, r: float) -> list[dict]:
    rows = tap_csv(f"""SELECT TOP {MAX_ROWS} source_id, ra, dec, pmra, pmdec, parallax, phot_g_mean_mag, bp_rp
        FROM gaiadr3.gaia_source WHERE {circle(ra, dec, r)} ORDER BY phot_g_mean_mag""",
                   url=settings.gaia_tap_url, timeout=120)
    return [{"id": f"Gaia DR3 {x['source_id']}", "ra": num(x["ra"]), "dec": num(x["dec"]), "epoch": 2016.0,
             "pmra": num(x["pmra"]), "pmdec": num(x["pmdec"]), "parallax": num(x["parallax"]),
             "mag": num(x["phot_g_mean_mag"]), "mag_band": "G", "color": num(x["bp_rp"])} for x in rows]
