"""Gaia DR3 sources (positions at epoch 2016.0) from whichever archive answers best: ESA, MAST, IRSA or VizieR.

All four serve the same DR3 catalogue; `upstreams` orders them by recent latency and failures, so an ESA outage
costs one failed (or hedged) request instead of the whole overlay.
"""

from __future__ import annotations

from .. import endpoints, upstreams
from ..archive.tap import tap_csv
from ..config import settings
from .common import MAX_ROWS, circle, num

LABEL = "Gaia DR3"
_COLS = "source_id, ra, dec, pmra, pmdec, parallax, phot_g_mean_mag, bp_rp"


def _standard(url: str, table: str, ra: float, dec: float, r: float) -> list[dict]:
    rows = tap_csv(f"SELECT TOP {MAX_ROWS} {_COLS} FROM {table} WHERE {circle(ra, dec, r)} ORDER BY phot_g_mean_mag",
                   url=url, timeout=120)
    return [{"id": f"Gaia DR3 {x['source_id']}", "ra": num(x["ra"]), "dec": num(x["dec"]), "epoch": 2016.0,
             "pmra": num(x["pmra"]), "pmdec": num(x["pmdec"]), "parallax": num(x["parallax"]),
             "mag": num(x["phot_g_mean_mag"]), "mag_band": "G", "color": num(x["bp_rp"])} for x in rows]


def _vizier(ra: float, dec: float, r: float) -> list[dict]:
    rows = tap_csv(f"""SELECT TOP {MAX_ROWS} Source, RA_ICRS, DE_ICRS, pmRA, pmDE, Plx, Gmag, "BP-RP"
        FROM "I/355/gaiadr3" WHERE {circle(ra, dec, r, 'RA_ICRS', 'DE_ICRS')} ORDER BY Gmag""",
                   url=settings.vizier_tap_url, timeout=120)
    return [{"id": f"Gaia DR3 {x['Source']}", "ra": num(x["RA_ICRS"]), "dec": num(x["DE_ICRS"]), "epoch": 2016.0,
             "pmra": num(x["pmRA"]), "pmdec": num(x["pmDE"]), "parallax": num(x["Plx"]), "mag": num(x["Gmag"]),
             "mag_band": "G", "color": num(x.get("BP-RP"))} for x in rows]


def query(ra: float, dec: float, r: float) -> list[dict]:
    def ask(ep):
        if ep is endpoints.GAIA_ESA:
            return _standard(settings.gaia_tap_url, "gaiadr3.gaia_source", ra, dec, r)
        if ep is endpoints.GAIA_MAST:
            return _standard(settings.mast_gaia_tap_url, "dbo.gaia_source", ra, dec, r)
        if ep is endpoints.GAIA_IRSA:
            return _standard(settings.tap_url, "gaia_dr3_source", ra, dec, r)
        return _vizier(ra, dec, r)

    rows, _ = upstreams.call("gaia", ask)
    return rows
