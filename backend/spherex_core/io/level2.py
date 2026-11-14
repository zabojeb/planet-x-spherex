"""Read cutouts, wavelength maps and effective PSFs from SPHEREx Level 2 MEF files with HTTP range requests.

File layout (checked on DR1 and QR3 files): PRIMARY (empty) · IMAGE (float32 2040x2040, MJy/sr) ·
FLAGS (int32 bitmask, RICE_1 tile-compressed with one tile per row) · VARIANCE (float32) ·
ZODI (float32) · EPSF (441 zone ePSFs, 33x33 oversampled 5x) · WCS-WAVE (pixel -> wavelength lookup
table). EPSF and WCS-WAVE are identical for all files of a detector and are read once per detector.

A cutout costs three round trips: IMAGE header -> FLAGS header + tile index -> pixel rows of
IMAGE, FLAGS and VARIANCE. Only the rows that cover the cutout are transferred. Cutouts and
wavelength maps are cached on disk (see cache.py).
"""

from __future__ import annotations

import asyncio
import warnings
import zlib
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from astropy.io import fits
from astropy.io.fits.hdu.compressed._codecs import Rice1
from astropy.wcs import WCS, FITSFixedWarning

from ..cache import CacheLayout
from ..flags import FlagDefs, flag_defs_from_text, flag_defs_to_text, read_flag_defs
from .fitsheader import data_nbytes, padded, parse_header, slim_header
from .http import RangeClient

HEAD_BYTES = 65536

warnings.filterwarnings("ignore", category=FITSFixedWarning)


@dataclass
class WaveMap:
    """Detector lookup table: pixel -> (central wavelength, bandwidth) in microns, bilinear as in FITS -TAB.

    The table is defined on raw pixel coordinates; the celestial SIP distortion does not apply to it.
    """

    x: np.ndarray  # control points along FITS axis 1, 1-based pixel coordinates
    y: np.ndarray  # control points along FITS axis 2
    values: np.ndarray  # [len(y), len(x), 2]

    def __post_init__(self) -> None:
        self.x, self.y, self.values = (np.asarray(a, dtype=float) for a in (self.x, self.y, self.values))
        if self.x[0] > self.x[-1]:
            self.x, self.values = self.x[::-1], self.values[:, ::-1]
        if self.y[0] > self.y[-1]:
            self.y, self.values = self.y[::-1], self.values[::-1]

    def __call__(self, xpix, ypix):
        """Wavelength and bandwidth (um) at 0-based numpy pixel coordinates."""
        xi = np.interp(np.asarray(xpix, dtype=float) + 1.0, self.x, np.arange(self.x.size))
        yi = np.interp(np.asarray(ypix, dtype=float) + 1.0, self.y, np.arange(self.y.size))
        i0 = np.clip(np.floor(xi).astype(int), 0, self.x.size - 2)
        j0 = np.clip(np.floor(yi).astype(int), 0, self.y.size - 2)
        tx, ty = (xi - i0)[..., None], (yi - j0)[..., None]
        v = self.values
        out = ((1 - tx) * (1 - ty) * v[j0, i0] + tx * (1 - ty) * v[j0, i0 + 1]
               + (1 - tx) * ty * v[j0 + 1, i0] + tx * ty * v[j0 + 1, i0 + 1])
        return out[..., 0], out[..., 1]


@dataclass
class EPSFTable:
    """Effective PSFs of one detector: an oversampled, pixel-convolved PSF per detector zone (21 x 21 zones of
    ~97 px, photutils ePSF convention, each array sums to 1). Identical in every DR1 and QR3 file of a
    detector (checked on three files), so it is read once per detector and cached like the WaveMap."""

    xcenter: np.ndarray  # zone centres, 0-based pixel coordinates
    ycenter: np.ndarray
    psf: np.ndarray  # [zones, ny_os, nx_os]
    oversampling: int
    cwave: np.ndarray  # central wavelength of each zone, um
    neff: np.ndarray  # effective PSF area, native pixels

    def nearest(self, x: float, y: float) -> tuple[np.ndarray, int]:
        """The ePSF of the zone whose centre is nearest to pixel (x, y), and its row index."""
        k = int(np.argmin((self.xcenter - x) ** 2 + (self.ycenter - y) ** 2))
        return self.psf[k], k


@dataclass
class Cutout:
    url: str
    header: fits.Header  # slim IMAGE header; its WCS describes the full detector frame
    x0: int  # position of the cutout's first column/row in the full frame (0-based)
    y0: int
    image: np.ndarray  # float32, MJy/sr, zodiacal light not subtracted
    variance: np.ndarray  # float32, (MJy/sr)^2
    flags: np.ndarray  # int32 bitmask
    _wcs: WCS | None = field(default=None, repr=False)

    @property
    def wcs(self) -> WCS:
        if self._wcs is None:
            self._wcs = WCS(self.header)
        return self._wcs


class Level2Reader:
    """Cutouts and wavelength maps from Level 2 files. One instance per batch of requests.

    cache_dir: root of the on-disk cache (see cache.CacheLayout), or None to disable caching.
    """

    def __init__(self, client: RangeClient, cache_dir: Path | None = None):
        self.c = client
        self.cache = CacheLayout(Path(cache_dir)) if cache_dir is not None else None
        self.var_hdr_len: dict[int, int] = {}
        self.wave_maps: dict[int, WaveMap] = {}
        self._wave_tasks: dict[int, asyncio.Future] = {}
        self.epsf_tables: dict[int, EPSFTable] = {}
        self._epsf_tasks: dict[int, asyncio.Future] = {}
        self.flag_defs: FlagDefs = {}
        self.cache_hits = 0

    # ---------------------------------------------------------------- cutouts
    async def cutout(self, url: str, ra: float, dec: float, half: int) -> Cutout | None:
        """Square cutout of (2*half+1) native pixels centred on (ra, dec); None if the point is off the frame."""
        path = self.cache.cutout(url, ra, dec, half) if self.cache else None
        if path is not None and path.exists():
            try:
                with np.load(path, allow_pickle=False) as z:
                    if int(z["x0"]) < 0:
                        return None
                    hdr = slim_header(fits.Header.fromstring(str(z["header"])))
                    if not self.flag_defs and "flag_defs" in z:
                        self.flag_defs.update(flag_defs_from_text(str(z["flag_defs"])))
                    self.cache_hits += 1
                    return Cutout(url, hdr, int(z["x0"]), int(z["y0"]), z["image"], z["variance"], z["flags"])
            except Exception:  # noqa: BLE001 - corrupted cache entry, fetch again
                pass
        cut = await self._fetch_cutout(url, ra, dec, half)
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp.npz")
            if cut is None:
                np.savez(tmp, x0=-1)
            else:
                np.savez(tmp, header=cut.header.tostring(), x0=cut.x0, y0=cut.y0, image=cut.image,
                         variance=cut.variance, flags=cut.flags, flag_defs=flag_defs_to_text(self.flag_defs))
            tmp.replace(path)
        return cut

    async def _fetch_cutout(self, url: str, ra: float, dec: float, half: int) -> Cutout | None:
        c = self.c
        head = await c.get(url, 0, HEAD_BYTES)
        prim = parse_header(head)
        if prim is None:
            raise RuntimeError("primary header not found")
        l0 = prim[1]
        img = parse_header(head, l0)
        if img is None:
            head = await c.get(url, 0, 4 * HEAD_BYTES)
            img = parse_header(head, l0)
            if img is None:
                raise RuntimeError("IMAGE header not found")
        ih, l1 = img
        ih = slim_header(ih)
        nx, ny = int(ih["NAXIS1"]), int(ih["NAXIS2"])
        wcs = WCS(ih)
        x, y = wcs.all_world2pix(ra, dec, 0, quiet=True)
        x, y = float(x), float(y)
        if not (np.isfinite(x) and np.isfinite(y)):
            return None
        xc, yc = int(np.floor(x + 0.5)), int(np.floor(y + 0.5))
        x0, x1 = max(xc - half, 0), min(xc + half + 1, nx)
        y0, y1 = max(yc - half, 0), min(yc + half + 1, ny)
        if x1 - x0 < 3 or y1 - y0 < 3:
            return None

        row = nx * 4
        img_off = l0 + l1
        flags_hdr = img_off + padded(nx * ny * 4)
        img_raw, fhead = await asyncio.gather(
            c.get(url, img_off + y0 * row, (y1 - y0) * row),
            c.get(url, flags_hdr, HEAD_BYTES),
        )
        fp = parse_header(fhead)
        if fp is None:
            fhead = await c.get(url, flags_hdr, 4 * HEAD_BYTES)
            fp = parse_header(fhead)
            if fp is None:
                raise RuntimeError("FLAGS header not found")
        fh, fl = fp
        if not self.flag_defs:
            self.flag_defs.update(read_flag_defs(fh))

        if fh.get("ZIMAGE"):
            flag_job, var_hdr, decode_flags = await self._compressed_flags(url, fh, fl, fhead, flags_hdr,
                                                                           x0, x1, y0, y1)
        else:  # plain image extension
            frow = int(fh["NAXIS1"]) * abs(int(fh["BITPIX"])) // 8
            flag_job = c.get(url, flags_hdr + fl + y0 * frow, (y1 - y0) * frow)
            var_hdr = flags_hdr + fl + padded(data_nbytes(fh))
            dt = ">i4" if int(fh["BITPIX"]) == 32 else ">i2"

            def decode_flags(raw: bytes) -> np.ndarray:
                return np.frombuffer(raw, dtype=dt).reshape(y1 - y0, -1)[:, x0:x1].astype(np.int32)

        det = int(ih.get("DETECTOR", 0))
        vlen = self.var_hdr_len.get(det, 11520)
        flag_raw, vhead, var_raw = await asyncio.gather(
            flag_job, c.get(url, var_hdr, vlen), c.get(url, var_hdr + vlen + y0 * row, (y1 - y0) * row))
        vp = parse_header(vhead)
        if vp is None or vp[1] != vlen:  # header length differs from the prediction: read it properly
            vhead = await c.get(url, var_hdr, HEAD_BYTES)
            vp = parse_header(vhead)
            if vp is None:
                raise RuntimeError("VARIANCE header not found")
            vlen = vp[1]
            var_raw = await c.get(url, var_hdr + vlen + y0 * row, (y1 - y0) * row)
        self.var_hdr_len[det] = vlen

        image = np.frombuffer(img_raw, dtype=">f4").reshape(y1 - y0, nx)[:, x0:x1].astype(np.float32)
        variance = np.frombuffer(var_raw, dtype=">f4").reshape(y1 - y0, nx)[:, x0:x1].astype(np.float32)
        return Cutout(url, ih, x0, y0, image, variance, decode_flags(flag_raw), _wcs=wcs)

    async def _compressed_flags(self, url, fh, fl, fhead, flags_hdr, x0, x1, y0, y1):
        """Plan the read of RICE/GZIP row tiles y0..y1 of a tile-compressed FLAGS extension."""
        ntiles, rowlen = int(fh["NAXIS2"]), int(fh["NAXIS1"])
        if fh.get("TTYPE1") != "COMPRESSED_DATA" or int(fh.get("ZTILE2", 1)) != 1 \
                or int(fh.get("ZTILE1", fh["ZNAXIS1"])) != int(fh["ZNAXIS1"]):
            raise NotImplementedError("only row-tiled COMPRESSED_DATA is supported")
        need = fl + rowlen * ntiles
        if len(fhead) < need:
            fhead = fhead + await self.c.get(url, flags_hdr + len(fhead), need - len(fhead))
        wide = fh["TFORM1"].lstrip("0123456789").startswith("Q")
        width, dt = (16, ">i8") if wide else (8, ">i4")
        table = np.frombuffer(fhead[fl:need], dtype=np.uint8).reshape(ntiles, rowlen)[:, :width]
        desc = np.frombuffer(table.tobytes(), dtype=dt).reshape(ntiles, 2).astype(np.int64)  # (nbytes, offset)
        heap = flags_hdr + fl + int(fh.get("THEAP", rowlen * ntiles))
        var_hdr = flags_hdr + fl + padded(rowlen * ntiles + int(fh["PCOUNT"]))
        sel = desc[y0:y1]
        lo, hi = int(sel[:, 1].min()), int((sel[:, 1] + sel[:, 0]).max())
        params = {fh[f"ZNAME{i}"]: fh[f"ZVAL{i}"] for i in range(1, 10) if f"ZNAME{i}" in fh}
        cmp, tilesize = fh["ZCMPTYPE"].strip(), int(fh["ZNAXIS1"])
        bytepix = int(params.get("BYTEPIX", abs(int(fh.get("ZBITPIX", 32))) // 8))

        def decode(raw: bytes) -> np.ndarray:
            rows = []
            for nbytes, off in sel:
                chunk = raw[off - lo: off - lo + nbytes]
                if nbytes == 0:
                    vals = np.zeros(tilesize, dtype=np.int32)
                elif cmp == "RICE_1":
                    vals = Rice1(blocksize=int(params.get("BLOCKSIZE", 32)), bytepix=bytepix,
                                 tilesize=tilesize).decode(np.frombuffer(chunk, dtype=np.uint8))
                elif cmp in ("GZIP_1", "GZIP_2"):
                    vals = np.frombuffer(zlib.decompress(chunk, 15 + 32), dtype=f">i{bytepix}")
                else:
                    raise NotImplementedError(f"compression {cmp}")
                rows.append(np.asarray(vals)[x0:x1])
            return np.stack(rows).astype(np.int32)

        return self.c.get(url, heap + lo, hi - lo), var_hdr, decode

    # ---------------------------------------------------------------- wavelength
    async def wave_map(self, url: str, detector: int) -> WaveMap:
        if detector in self.wave_maps:
            return self.wave_maps[detector]
        if detector not in self._wave_tasks:
            self._wave_tasks[detector] = asyncio.ensure_future(self._load_wave_map(url, detector))
        wm = await self._wave_tasks[detector]
        self.wave_maps[detector] = wm
        return wm

    async def _load_wave_map(self, url: str, detector: int) -> WaveMap:
        path = self.cache.wave(detector) if self.cache else None
        if path is not None and path.exists():
            with np.load(path) as z:
                return WaveMap(z["x"], z["y"], z["values"])
        with await self._read_table_hdu(url, "WCS-WAVE") as hdul:
            tab = hdul[1].data
            wm = WaveMap(np.asarray(tab["X"][0]), np.asarray(tab["Y"][0]), np.asarray(tab["VALUES"][0]))
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            np.savez(path, x=wm.x, y=wm.y, values=wm.values)
        return wm

    # ---------------------------------------------------------------- effective PSF
    async def epsf_table(self, url: str, detector: int) -> EPSFTable:
        if detector in self.epsf_tables:
            return self.epsf_tables[detector]
        if detector not in self._epsf_tasks:
            self._epsf_tasks[detector] = asyncio.ensure_future(self._load_epsf(url, detector))
        table = await self._epsf_tasks[detector]
        self.epsf_tables[detector] = table
        return table

    async def _load_epsf(self, url: str, detector: int) -> EPSFTable:
        path = self.cache.epsf(detector) if self.cache else None
        if path is not None and path.exists():
            with np.load(path) as z:
                return EPSFTable(z["xcenter"], z["ycenter"], z["psf"], int(z["oversampling"]), z["cwave"], z["neff"])
        with await self._read_table_hdu(url, "EPSF") as hdul:
            hdu = hdul[1]
            tab = hdu.data
            ovs_x, ovs_y = int(hdu.header.get("OVSMPX", 1)), int(hdu.header.get("OVSMPY", 1))
            if ovs_x != ovs_y:
                raise NotImplementedError("anisotropic ePSF oversampling")
            table = EPSFTable(np.asarray(tab["XCENTER"], float), np.asarray(tab["YCENTER"], float),
                              np.asarray(tab["EPSF"], np.float32), ovs_x, np.asarray(tab["CWAVE"], float),
                              np.asarray(tab["NEFF_MEAN"], float))
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp.npz")
            np.savez(tmp, xcenter=table.xcenter, ycenter=table.ycenter, psf=table.psf,
                     oversampling=table.oversampling, cwave=table.cwave, neff=table.neff, source=url)
            tmp.replace(path)
        return table

    async def _read_table_hdu(self, url: str, extname: str) -> fits.HDUList:
        """Walk the HDU headers until EXTNAME == extname and return that HDU (header + data) as an HDUList
        whose element 1 is the extension. Only headers and the one data unit are transferred."""
        off = 0
        for _ in range(16):
            buf = await self.c.get(url, off, HEAD_BYTES)
            p = parse_header(buf)
            if p is None:
                buf = await self.c.get(url, off, 4 * HEAD_BYTES)
                p = parse_header(buf)
                if p is None:
                    break
            h, hl = p
            nbytes = data_nbytes(h)
            if str(h.get("EXTNAME", "")).strip() == extname:
                data = buf[hl:hl + nbytes] if len(buf) >= hl + nbytes else await self.c.get(url, off + hl, nbytes)
                blob = (fits.PrimaryHDU().header.tostring().encode() + buf[:hl] + data
                        + b"\0" * (padded(nbytes) - nbytes))
                return fits.HDUList.fromstring(blob)
            off += hl + padded(nbytes)
        raise RuntimeError(f"{extname} extension not found")
