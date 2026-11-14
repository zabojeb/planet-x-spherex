"""Template spectra integrated onto the SPHEREx bin grid.

Libraries (downloaded once on first use, then cached as one .npz in the cache directory):

* Pickles (1998) UVKLIB — 131 stars O5-M10, dwarfs to supergiants, 0.115-2.5 um, from the STScI CDBS
  reference atlases (public domain, NASA/STScI);
* SpeX Prism Library spectral standards M0-T9 (Burgasser 2014 and references therein), 0.65-2.55 um, from
  the SPLAT repository (MIT licence; spectra published by their authors).

Neither library covers 2.5-5 um, so templates constrain the fit only below ~2.45 um; beyond that the
classifier relies on blackbody/power-law fits and the spectral indices (CH4 3.3 um, 4 um peak).

Each template is converted to F_nu and averaged over a Gaussian response per bin whose variance is the
SPHEREx channel width (FWHM = lambda/R) plus the bin width (uniform over the bin), i.e. what a binned
SPHEREx spectrum of that object would look like.
"""

from __future__ import annotations

import gzip
import io
import logging
import re
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import numpy as np

from .. import cache
from ..logs import event, timed
from .grid import GRID, BinGrid

log = logging.getLogger(__name__)

PICKLES_INDEX = "https://archive.stsci.edu/hlsps/reference-atlases/cdbs/grid/pickles/dat_uvk/pickles_uk.fits"
PICKLES_FILE = "https://archive.stsci.edu/hlsps/reference-atlases/cdbs/grid/pickles/dat_uvk/{name}.fits"
SPLAT_BASE = "https://raw.githubusercontent.com/aburgasser/splat/main/src/splat/resources/"
SPLAT_STANDARDS = SPLAT_BASE + "Libraries/STANDARDS_DWARF_SPEX-PRISM.csv"
SPLAT_SPECTRUM = SPLAT_BASE + "Spectra/Public/SPEX-PRISM/{file}.gz"
LIBRARY_VERSION = 1


@dataclass
class TemplateLibrary:
    names: list[str]
    sptype: list[str]
    family: list[str]  # "star" | "dwarf_standard"
    source: list[str]
    code: np.ndarray  # numeric type: O=0 ... M=60, L=70, T=80, Y=90 (+ subclass)
    lumclass: list[str]
    model: np.ndarray  # [n_templates, n_bins] F_nu, arbitrary scale, NaN where not covered

    def subset(self, mask: np.ndarray) -> "TemplateLibrary":
        idx = np.nonzero(mask)[0]
        pick = lambda xs: [xs[i] for i in idx]  # noqa: E731
        return TemplateLibrary(pick(self.names), pick(self.sptype), pick(self.family), pick(self.source),
                               self.code[idx], pick(self.lumclass), self.model[idx])


_LETTERS = {"O": 0, "B": 10, "A": 20, "F": 30, "G": 40, "K": 50, "M": 60, "L": 70, "T": 80, "Y": 90}


def clean_type(sptype: str) -> str:
    """Pickles marks metal-weak/-rich spectra with a leading 'w'/'r' (e.g. 'wG5V'); drop it."""
    s = sptype.strip()
    return s[1:] if s[:1] in ("w", "r") and len(s) > 1 and s[1].isupper() else s


def type_code(sptype: str) -> float:
    m = re.match(r"\s*([OBAFGKMLTY])\s*(\d+(?:\.\d+)?)?", clean_type(sptype).upper())
    if not m:
        return float("nan")
    return _LETTERS[m.group(1)] + (float(m.group(2)) if m.group(2) else 0.0)


def lum_class(sptype: str) -> str:
    m = re.search(r"(I{1,3}|IV|V)\s*$", sptype.strip().upper())
    return m.group(1) if m else "V"


def integrate(wl_um: np.ndarray, flam: np.ndarray, grid: BinGrid = GRID) -> np.ndarray:
    """Template (wavelength um, F_lambda any unit) -> F_nu per bin (arbitrary scale), NaN if not covered."""
    order = np.argsort(wl_um)
    wl, fl = wl_um[order], flam[order]
    good = np.isfinite(fl)
    wl, fl = wl[good], fl[good]
    fnu = fl * wl ** 2
    out = np.full(grid.size, np.nan)
    fwhm_channel = grid.center / grid.resolving_power
    sigma = np.sqrt((fwhm_channel / 2.3548) ** 2 + (grid.hi - grid.lo) ** 2 / 12.0)
    for i in range(grid.size):
        c, s = grid.center[i], sigma[i]
        if c - 2.5 * s < wl[0] or c + 2.5 * s > wl[-1]:
            continue
        sel = (wl > c - 4 * s) & (wl < c + 4 * s)
        if sel.sum() < 3:
            out[i] = np.interp(c, wl, fnu)
            continue
        w = np.exp(-0.5 * ((wl[sel] - c) / s) ** 2) * np.gradient(wl[sel])
        out[i] = float((w * fnu[sel]).sum() / w.sum())
    return out


def _get(url: str, timeout: float = 60.0) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read()


def _download_pickles() -> list[tuple[str, str, str, np.ndarray, np.ndarray]]:
    from astropy.table import Table

    index = Table.read(io.BytesIO(_get(PICKLES_INDEX)))
    rows = [(str(r["FILENAME"]).strip(), str(r["SPTYPE"]).strip()) for r in index]

    def one(row):
        name, sp = row
        t = Table.read(io.BytesIO(_get(PICKLES_FILE.format(name=name))))
        return name, sp, "Pickles 1998 (STScI CDBS)", np.asarray(t["WAVELENGTH"], float) / 1e4, \
            np.asarray(t["FLUX"], float)

    with ThreadPoolExecutor(8) as ex:
        return list(ex.map(one, rows))


def _download_spex_standards() -> list[tuple[str, str, str, np.ndarray, np.ndarray]]:
    from astropy.io import fits

    text = _get(SPLAT_STANDARDS).decode().replace("\r", "\n")
    pairs = re.findall(r"(\d+_\d+\.fits)\s*,\s*([MLTY]\d(?:\.\d)?)", text)

    def one(pair):
        file, sp = pair
        raw = gzip.decompress(_get(SPLAT_SPECTRUM.format(file=file)))
        data = fits.open(io.BytesIO(raw))[0].data
        return f"spex_{sp}", f"{sp}V", "SpeX Prism standard (SPLAT)", np.asarray(data[0], float), \
            np.asarray(data[1], float)

    with ThreadPoolExecutor(8) as ex:
        return list(ex.map(one, pairs))


_LIBRARY: TemplateLibrary | None = None


def library(refresh: bool = False) -> TemplateLibrary:
    """The template library on GRID, downloading and integrating it on first use (~15 s, ~8 MB)."""
    global _LIBRARY
    if _LIBRARY is not None and not refresh:
        return _LIBRARY
    path = cache.layout().root / "spectral" / f"templates_v{LIBRARY_VERSION}.npz"
    if path.exists() and not refresh:
        z = np.load(path, allow_pickle=False)
        _LIBRARY = TemplateLibrary(list(z["names"]), list(z["sptype"]), list(z["family"]), list(z["source"]),
                                   z["code"], list(z["lumclass"]), z["model"])
        return _LIBRARY
    entries = []
    for family, fetch in (("star", _download_pickles), ("dwarf_standard", _download_spex_standards)):
        try:
            with timed(log, "spectral templates download", library=family) as info:
                got = fetch()
                info["n"] = len(got)
            entries += [(family, *e) for e in got]
        except Exception as exc:  # noqa: BLE001 - one library missing still leaves a usable classifier
            event(log, logging.WARNING, "spectral templates unavailable", library=family, error=repr(exc)[:200])
    if not entries:
        raise RuntimeError("no template library could be downloaded")
    model = np.stack([integrate(wl, fl) for _, _, _, _, wl, fl in entries])
    lib = TemplateLibrary([e[1] for e in entries], [e[2] for e in entries], [e[0] for e in entries],
                          [e[3] for e in entries], np.array([type_code(e[2]) for e in entries]),
                          [lum_class(e[2]) for e in entries], model)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp.npz")
    np.savez(tmp, names=lib.names, sptype=lib.sptype, family=lib.family, source=lib.source, code=lib.code,
             lumclass=lib.lumclass, model=lib.model)
    tmp.replace(path)
    _LIBRARY = lib
    return lib
