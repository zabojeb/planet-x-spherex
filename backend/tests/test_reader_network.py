"""Checks the range reader against astropy on a real Level 2 file (needs network)."""

import asyncio

import numpy as np
import pytest
from astropy.io import fits
from astropy.wcs import WCS

from spherex_core.io import Level2Reader, RangeClient

URL = ("https://nasa-irsa-spherex.s3.us-east-1.amazonaws.com/dr1/level2/2025W35_1A/l2b-v27-2026-230/1/"
       "level2_2025W35_1A_0432_4D1_spx_l2b-v27-2026-230.fits")
RA, DEC = 269.4463, 4.7682  # Barnard's Star in 2025


async def _read():
    async with RangeClient(8) as client:
        reader = Level2Reader(client, cache_dir=None)
        cut = await reader.cutout(URL, RA, DEC, 20)
        wave = await reader.wave_map(URL, 1)
        epsf = await reader.epsf_table(URL, 1)
        return cut, wave, reader.flag_defs, epsf


@pytest.mark.network
def test_epsf_table_matches_astropy():
    _, _, _, epsf = asyncio.run(_read())
    assert epsf.psf.shape == (441, 33, 33) and epsf.oversampling == 5
    psf, row = epsf.nearest(1019.5, 1019.5)
    with fits.open(URL, use_fsspec=True, fsspec_kwargs={"block_size": 1 << 20}) as hdul:
        tab = hdul["EPSF"].data
        assert (tab["BINX"][row], tab["BINY"][row]) == (10, 10)
        np.testing.assert_allclose(psf, tab["EPSF"][row], rtol=1e-6)
    assert abs(psf.sum() - 1.0) < 1e-5


@pytest.mark.network
def test_cutout_matches_astropy():
    cut, wave, flag_defs, _ = asyncio.run(_read())
    assert cut is not None and cut.image.shape == (41, 41)
    with fits.open(URL, use_fsspec=True, fsspec_kwargs={"block_size": 1 << 20}) as hdul:
        sl = np.s_[cut.y0:cut.y0 + 41, cut.x0:cut.x0 + 41]
        np.testing.assert_array_equal(cut.image, hdul["IMAGE"].section[sl])
        np.testing.assert_array_equal(cut.variance, hdul["VARIANCE"].section[sl])
        np.testing.assert_array_equal(cut.flags, hdul["FLAGS"].section[sl])
        # Wavelength lookup vs astropy's -TAB implementation. The table is defined on raw pixel
        # coordinates, so the celestial SIP distortion keywords must not be applied to it.
        hdr = hdul["IMAGE"].header.copy()
        for k in [k for k in hdr if k.split("_")[0] in ("A", "B", "AP", "BP")]:
            del hdr[k]
        w = WCS(hdr, fobj=hdul, key="W")
        xs, ys = np.array([0.0, 500.0, 1019.5, 2039.0]), np.array([0.0, 1500.0, 1019.5, 2039.0])
        ref = w.pixel_to_world_values(xs, ys)
        mine = wave(xs, ys)
        np.testing.assert_allclose(mine[0], ref[0], rtol=1e-6)
        np.testing.assert_allclose(mine[1], ref[1], rtol=1e-6)
    assert flag_defs[21][0] == "SOURCE"
