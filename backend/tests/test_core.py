"""Unit tests that run offline."""

import numpy as np

from spherex_core.io.fitsheader import padded, parse_header
from spherex_core.io.level2 import WaveMap
from spherex_core.processing import assign_epochs, coadd, make_grid
from spherex_core.targets import PRESETS, Target, parse_coordinates
from spherex_core.timeutil import year_to_mjd


def test_parse_coordinates():
    t = parse_coordinates("269.452 4.693")
    assert t is not None and abs(t.ra - 269.452) < 1e-9
    t = parse_coordinates("17:57:48.50 +04:41:36.2")
    assert t is not None and abs(t.ra - 269.452083) < 1e-5 and abs(t.dec - 4.693389) < 1e-5
    t = parse_coordinates("08h55m10.831s -7d14m42.53s")
    assert t is not None and abs(t.dec + 7.245147) < 1e-5
    assert parse_coordinates("Barnard's star") is None
    assert parse_coordinates("400 10") is None


def test_proper_motion():
    b = PRESETS["barnard"]
    ra, dec = b.position_at(year_to_mjd(2026.0))
    assert abs((dec - b.dec) * 3600 - 10.362394 * 26) < 0.01
    t = Target(ra=10.0, dec=0.0)
    assert t.position_at(year_to_mjd(2030.0)) == (10.0, 0.0)


def test_epochs():
    mjd = np.array([0, 1, 2, 40, 41, 200, 260, 261, 262])
    ep = assign_epochs(mjd, gap_days=20, max_days=45)
    assert ep.tolist() == [0, 0, 0, 1, 1, 2, 3, 3, 3]
    ep = assign_epochs(np.arange(0, 100, 5.0), gap_days=20, max_days=30)
    assert ep.tolist() == [0] * 7 + [1] * 7 + [2] * 6  # new epochs start at t=35 and t=70


def test_coadd_rejects_outlier():
    stack = np.random.default_rng(1).normal(0, 1, (10, 5, 5)).astype(np.float32)
    stack[3, 2, 2] = 500.0  # asteroid in one frame
    stack[4, 1, 1] = np.nan
    mean, cov = coadd(stack, np.ones(10), noise=1.0)
    assert abs(mean[2, 2]) < 2 and cov[2, 2] == 9 and cov[1, 1] == 9


def test_grid_is_odd_and_centred():
    g = make_grid(10.0, 20.0, 4.0)
    assert g.n % 2 == 1
    x, y = g.world_to_pixel(10.0, 20.0)
    assert abs(x - (g.n - 1) / 2) < 1e-9 and abs(y - (g.n - 1) / 2) < 1e-9


def test_wave_map_bilinear():
    x = np.array([1.0, 1021.0, 2041.0])
    y = np.array([1.0, 2041.0])
    vals = np.zeros((2, 3, 2))
    vals[..., 0] = [[1.0, 2.0, 3.0], [1.0, 2.0, 3.0]]  # wavelength grows along x
    vals[..., 1] = 0.1
    wm = WaveMap(x, y, vals)
    wl, bw = wm(np.array([0.0, 510.0, 2040.0]), np.array([0.0, 0.0, 5.0]))
    np.testing.assert_allclose(wl, [1.0, 1.5, 3.0])
    np.testing.assert_allclose(bw, 0.1)


def test_parse_header_roundtrip():
    from astropy.io import fits

    h = fits.Header({"EXTNAME": "IMAGE", "NAXIS": 0})
    raw = h.tostring().encode() + b"x" * 100
    parsed, length = parse_header(raw)
    assert parsed["EXTNAME"] == "IMAGE" and length == padded(len(h.tostring())) == 2880
