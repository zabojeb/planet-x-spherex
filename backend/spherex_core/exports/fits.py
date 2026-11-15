"""Co-adds as FITS: the grid's TAN WCS, units and provenance in the headers."""

from __future__ import annotations

import io

import numpy as np
from astropy.io import fits

from .. import __version__
from ..bands import BAND_INFO, BANDS
from ..errors import NotFound
from ..pipeline import results
from ..timeutil import mjd_to_iso

ACKNOWLEDGEMENT = ("This publication makes use of data products from SPHEREx, a joint project of JPL and "
                   "Caltech funded by NASA.")


def _provenance(h: fits.Header, meta: dict) -> None:
    t = meta["target"]
    h["TELESCOP"] = "SPHEREx"
    h["ORIGIN"] = ("Seek In Space", "built by spherex-backend " + __version__)
    h["SEQID"] = (meta["id"], "sequence id")
    h["ALGOVER"] = (meta["version"], "pipeline algorithm version")
    h["CREATED"] = meta.get("created", "")
    h["OBJECT"] = (t.get("name") or "")[:68]
    h["TGT_RA"] = (t["ra"], "[deg] target RA at TGT_EPOC")
    h["TGT_DEC"] = (t["dec"], "[deg] target Dec at TGT_EPOC")
    h["TGT_EPOC"] = (t.get("ref_epoch", 2000.0), "[yr] epoch of TGT_RA/TGT_DEC")
    h["PMRA"] = (t.get("pmra", 0.0), "[mas/yr] incl. cos(dec)")
    h["PMDEC"] = (t.get("pmdec", 0.0), "[mas/yr]")
    h["FOV"] = (meta["grid"]["fov_arcmin"], "[arcmin]")
    h["COLLECTS"] = ",".join(meta["sources"]["collections"])[:68]
    h["DATASRC"] = meta["sources"]["data"][:68]
    h["COMMENT"] = "Background-subtracted co-add of aligned SPHEREx Level 2 exposures."
    h["COMMENT"] = ACKNOWLEDGEMENT


def _image_header(meta: dict, epoch: dict, band: str) -> fits.Header:
    from ..processing import Grid

    g = meta["grid"]
    h = Grid(g["ra"], g["dec"], g["n"], g["pixscale_arcsec"]).wcs.to_header()
    info = epoch["bands"][band]
    h["BUNIT"] = "MJy/sr"
    h["EPOCH"] = (epoch["index"], "epoch index in the sequence")
    h["DETECTOR"] = (band, "SPHEREx detector")
    h["WAVEMIN"] = (info["wl_min"], "[um] shortest exposure wavelength")
    h["WAVEMAX"] = (info["wl_max"], "[um] longest exposure wavelength")
    h["DETWMIN"] = (BAND_INFO[band]["wl_min"], "[um] nominal detector range")
    h["DETWMAX"] = (BAND_INFO[band]["wl_max"], "[um]")
    h["MJD-AVG"] = (info["mjd_mean"], "mean exposure time")
    h["DATE-AVG"] = mjd_to_iso(info["mjd_mean"]).replace(" ", "T")
    h["DATE-BEG"] = epoch["date_min"].replace(" ", "T")
    h["DATE-END"] = epoch["date_max"].replace(" ", "T")
    h["NFRAMES"] = (info["n"], "exposures co-added")
    h["NOISE"] = (info.get("noise"), "[MJy/sr] median per-exposure rms")
    h["FILLED"] = (info.get("filled_pixels", 0), "pixels filled from masked data")
    h["PARTIAL"] = (bool(epoch.get("partial", False)), "fewer exposures than a full visit")
    return h


def coadds_fits(result_dir, meta: dict, epoch: int | None = None, band: str | None = None,
                coverage: bool = False) -> bytes:
    """FITS bytes: one image when epoch and band are given, else a multi-extension file with one image
    extension per epoch and detector (EXTNAME E<epoch>_<band>), optionally each followed by its coverage."""
    data = results.read_array(result_dir, meta, "coadds")
    cov = results.read_array(result_dir, meta, "coverage") if coverage else None
    epochs = {e["index"]: e for e in meta["epochs"]}
    if (epoch is None) != (band is None):
        raise NotFound("give both epoch and band, or neither")
    if epoch is not None:
        if epoch not in epochs or band not in BANDS or band not in epochs[epoch]["bands"]:
            raise NotFound("no co-add for this epoch and detector")
        hdu = fits.PrimaryHDU(data[epoch, BANDS.index(band)].astype(np.float32),
                              header=_image_header(meta, epochs[epoch], band))
        _provenance(hdu.header, meta)
        hdul = fits.HDUList([hdu])
        if cov is not None:
            hdul.append(fits.ImageHDU(cov[epoch, BANDS.index(band)].astype(np.int16), name="COVERAGE"))
    else:
        prim = fits.PrimaryHDU()
        _provenance(prim.header, meta)
        hdul = fits.HDUList([prim])
        for e in meta["epochs"]:
            for b in BANDS:
                if b not in e["bands"]:
                    continue
                hdul.append(fits.ImageHDU(data[e["index"], BANDS.index(b)].astype(np.float32),
                                          header=_image_header(meta, e, b), name=f"E{e['index']}_{b}"))
                if cov is not None:
                    hdul.append(fits.ImageHDU(cov[e["index"], BANDS.index(b)].astype(np.int16),
                                              name=f"E{e['index']}_{b}_COV"))
    buf = io.BytesIO()
    hdul.writeto(buf, output_verify="silentfix")
    return buf.getvalue()
