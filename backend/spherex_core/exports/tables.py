"""Photometry of a sequence as an astropy Table, CSV and VOTable."""

from __future__ import annotations

import io

from astropy.table import Table

# (column, unit, description)
COLUMNS = [
    ("index", None, "exposure index in meta.json"),
    ("epoch", None, "epoch index"),
    ("band", None, "SPHEREx detector"),
    ("mjd", "d", "mid-exposure MJD"),
    ("date", None, "mid-exposure UTC"),
    ("wl", "um", "central wavelength at the source"),
    ("dwl", "um", "bandwidth at the source"),
    ("flux_mjy", "mJy", "flux density (relative=True: ring flux, not calibrated)"),
    ("err_mjy", "mJy", "uncertainty incl. 3% systematic floor"),
    ("ab_mag", "mag", "AB magnitude (empty for relative fluxes)"),
    ("method", None, "psf | aperture | ring"),
    ("relative", None, "flux is relative only"),
    ("flagged", None, "measurement affected by flagged pixels"),
    ("saturated", None, "saturated core excluded"),
    ("npix", None, "pixels used"),
    ("chi2", None, "reduced chi^2 of the PSF fit"),
    ("obs_id", None, "SPHEREx observation id"),
    ("collection", None, "IRSA collection"),
    ("file", None, "Level 2 file"),
]


def photometry_table(meta: dict, photometry: list[dict] | None = None) -> Table:
    exp = {e["index"]: e for e in meta["exposures"]}
    rows = []
    for p in photometry if photometry is not None else meta["photometry"]:
        e = exp.get(p["index"], {})
        rows.append({**p, "date": e.get("date", ""), "obs_id": e.get("obs_id", ""),
                     "collection": e.get("collection", ""), "file": e.get("file", "")})
    t = Table()
    for name, unit, desc in COLUMNS:
        vals = [r.get(name) for r in rows]
        if name in ("ab_mag", "chi2", "flux_mjy", "err_mjy"):
            vals = [float("nan") if v is None else float(v) for v in vals]
        elif name == "npix":
            vals = [-1 if v is None else int(v) for v in vals]
        elif name in ("relative", "flagged", "saturated"):
            vals = [bool(v) for v in vals]
        elif name in ("band", "method", "date", "obs_id", "collection", "file"):
            vals = ["" if v is None else str(v) for v in vals]
        t[name] = vals
        if unit:
            t[name].unit = unit
        t[name].description = desc
    t.meta["sequence"] = meta["id"]
    t.meta["target"] = meta["target"].get("name") or ""
    return t


def photometry_csv(meta: dict, photometry: list[dict] | None = None) -> bytes:
    buf = io.StringIO()
    photometry_table(meta, photometry).write(buf, format="ascii.csv")
    return buf.getvalue().encode()


def photometry_votable(meta: dict, photometry: list[dict] | None = None) -> bytes:
    from astropy.io.votable import from_table

    t = photometry_table(meta, photometry)
    vot = from_table(t)
    vot.resources[0].tables[0].description = (f"SPHEREx forced photometry of {t.meta['target']} "
                                               f"(sequence {meta['id']})")
    buf = io.BytesIO()
    vot.to_xml(buf)
    return buf.getvalue()
