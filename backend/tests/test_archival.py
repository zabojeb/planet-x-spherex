"""Archival epochs offline: calibration, file selection, visits, resampling, the store and the routes."""

import math

import numpy as np
import pytest
from astropy.io import fits
from astropy.wcs import WCS
from fastapi.testclient import TestClient

from spherex_api.app import create_app
from spherex_core import cache, services
from spherex_core.archival import dn_to_mjy_sr, to_grid, visits, wise_s3_url
from spherex_core.archival import store as astore
from spherex_core.archival.build import grid_size
from spherex_core.archival.surveys import band_for
from spherex_core.errors import NotFound
from spherex_core.pipeline import results
from spherex_core.processing import Grid


def test_calibration():
    area = (1.375 / 3600 * math.pi / 180) ** 2
    expected = 309.54 * 10 ** (-0.4 * 20.5) / area / 1e6
    assert abs(dn_to_mjy_sr(20.5, 309.54, area) - expected) < 1e-12 * expected
    # a W1 = 10 (Vega) star of 1 DN-equivalent total flux check: 10**(-0.4*(10-20.5)) DN sum
    total_dn = 10 ** (-0.4 * (10.0 - 20.5))
    flux_jy = total_dn * dn_to_mjy_sr(20.5, 309.54, area) * 1e6 * area
    assert abs(flux_jy - 309.54 * 10 ** (-4.0)) < 1e-9


def test_wise_s3_url_and_band_selection():
    base = "https://irsa.ipac.caltech.edu/ibe/data/"
    aw = base + "wise/allwise/p3am_cdd/26/2688/2688p045_ac51/2688p045_ac51-w1-int-3.fits"
    nw = base + "wise/neowiser/p1bm_frm/9r/42589r/159/42589r159-w2-int-1b.fits"
    assert wise_s3_url(aw) == ("https://nasa-irsa-wise.s3.us-west-2.amazonaws.com/"
                               "wise/allwise/images/p3am_cdd/26/2688/2688p045_ac51/2688p045_ac51-w1-int-3.fits")
    assert wise_s3_url(nw).endswith("wise/neowiser/images/p1bm_frm/9r/42589r/159/42589r159-w2-int-1b.fits")
    assert wise_s3_url(base + "twomass/allsky/allsky/000528n/s101/image/ji1010220.fits.gz") is None
    assert band_for("wise_allwise", "W1", aw).band == "W1"
    assert band_for("wise_allwise", "W1", aw.replace("int-3.fits", "cov-3.fits.gz")) is None
    assert band_for("twomass_allsky", "K", base + "twomass/allsky/allsky/000528n/s101/image/ki1010220.fits.gz").band == "Ks"
    assert band_for("twomass_allsky", "K", base + "twomass/allsky/allsky/000528n/s101/pers.tbl") is None
    assert band_for("neowiser", "W2", nw).survey == "neowise"


def test_visits():
    assert visits([0.0, 0.1, 180.0, 0.2, 180.5, 400.0]) == [0, 0, 1, 0, 1, 2]
    v = visits(list(np.arange(0, 100, 1.0)))  # continuous coverage near the ecliptic poles
    assert max(v) == 3 and v[30] == 0 and v[31] == 1


def _star_image(ra, dec, pix_arcsec=1.375, n=240, flux_dn=5.0e4, magzp=20.5):
    w = WCS(naxis=2)
    w.wcs.ctype = ["RA---SIN", "DEC--SIN"]
    w.wcs.crval = [ra, dec]
    w.wcs.crpix = [n / 2.0 + 0.5, n / 2.0 + 0.5]
    w.wcs.cdelt = [-pix_arcsec / 3600, pix_arcsec / 3600]
    h = w.to_header()
    h["NAXIS"], h["NAXIS1"], h["NAXIS2"], h["MAGZP"] = 2, n, n, magzp
    yy, xx = np.mgrid[0:n, 0:n]
    x0, y0 = w.all_world2pix(ra + 20 / 3600 / math.cos(math.radians(dec)), dec + 10 / 3600, 0)  # 20" E, 10" N
    sigma = 2.5  # px
    img = flux_dn / (2 * math.pi * sigma ** 2) * np.exp(-((xx - x0) ** 2 + (yy - y0) ** 2) / (2 * sigma ** 2))
    return img.astype(np.float32) + 3.0, h


def test_to_grid_places_star_and_keeps_flux():
    ra, dec = 150.0, 20.0
    img, h = _star_image(ra, dec)
    grid = Grid(ra, dec, 31, 6.15)
    out = to_grid(img, h.tostring(), 0, 0, grid, 1.375, 309.54, 20.5)
    assert out.shape == (31, 31)
    yy, xx = np.unravel_index(np.nanargmax(out), out.shape)
    ex, ey = grid.world_to_pixel(ra + 20 / 3600 / math.cos(math.radians(dec)), dec + 10 / 3600)
    assert abs(xx - ex) <= 1 and abs(yy - ey) <= 1  # east is left: x decreases eastward
    area_native = (1.375 / 3600 * math.pi / 180) ** 2
    native_total = (img - 3.0).sum() * dn_to_mjy_sr(20.5, 309.54, area_native) * area_native
    grid_total = np.nansum(out) * (6.15 / 3600 * math.pi / 180) ** 2
    assert abs(grid_total / native_total - 1) < 0.08


def test_store_round_trip(tmp_path):
    d = tmp_path / "seq" / "11"
    meta = {"frames": [{"index": 0}, {"index": 1}], "array": {"file": "frames.f32", "dtype": "float32",
                                                             "shape": [2, 11, 11]}, "x": float("nan")}
    stack = np.arange(2 * 121, dtype=np.float32).reshape(2, 11, 11)
    astore.write(d, meta, stack)
    assert astore.is_complete(d) and astore.read_meta(d)["x"] is None
    np.testing.assert_array_equal(astore.read_frames(d), stack)
    np.testing.assert_array_equal(astore.read_frames(d, 1), stack[1])
    with pytest.raises(NotFound):
        astore.read_frames(d, 5)


@pytest.fixture()
def client(tmp_path):
    services.configure(cache_root=tmp_path)
    services._tasks = None
    n = 9
    seq_meta = {"id": "seq42", "grid": {"ra": 150.0, "dec": 20.0, "n": n, "pixscale_arcsec": 6.15},
                "target": {"ra": 150.0, "dec": 20.0, "pmra": 0.0, "pmdec": 0.0, "ref_epoch": 2000.0},
                "arrays": {"coadds": {"file": "coadds.f32", "dtype": "float32", "shape": [1, 6, n, n]},
                           "coverage": {"file": "coverage.u16", "dtype": "uint16", "shape": [1, 6, n, n]},
                           "flags": {"file": "flags.u32", "dtype": "uint32", "shape": [1, 6, n, n]},
                           "exposures": None}, "exposures": []}
    arrays = {"coadds": np.zeros((1, 6, n, n), np.float32), "coverage": np.zeros((1, 6, n, n), np.uint16),
              "flags": np.zeros((1, 6, n, n), np.uint32)}
    results.write_result(cache.layout().sequence("seq42"), seq_meta, arrays)
    assert grid_size(seq_meta, None) == n and grid_size(seq_meta, 2.0) == 21
    ameta = {"sequence": "seq42", "frames": [{"index": 0, "survey": "2MASS", "band": "J"}],
             "array": {"file": "frames.f32", "dtype": "float32", "shape": [1, n, n]}}
    astore.write(astore.result_dir("seq42", n), ameta, np.full((1, n, n), 2.5, np.float32))
    yield TestClient(create_app(web_dist=None))
    services.configure(None)
    services._tasks = None


def test_archival_routes(client):
    r = client.get("/api/sequences/seq42/archival")
    assert r.status_code == 200 and r.json()["frames"][0]["survey"] == "2MASS"
    b = client.get("/api/sequences/seq42/archival.bin")
    assert b.status_code == 200 and len(b.content) == 9 * 9 * 4
    assert np.all(np.frombuffer(b.content, "<f4") == 2.5)
    assert client.get("/api/sequences/seq42/archival.bin", params={"index": 0}).status_code == 200
    assert client.get("/api/sequences/seq42/archival.bin", params={"index": 3}).status_code == 404
    assert client.get("/api/sequences/nope/archival").status_code == 404
    # the plain array route still works and is not shadowed
    assert client.get("/api/sequences/seq42/coadds.bin").status_code == 200


def test_moving_search_route_cached(client, monkeypatch):
    from spherex_core.moving import most
    from test_moving import _result

    monkeypatch.setattr(most, "cached", lambda q: _result())
    r = client.get("/api/moving/search", params={"name": "2025 AA"})
    assert r.status_code == 200 and r.json()["n_exposures"] == 5
    assert client.get("/api/moving/search").status_code == 422
    assert client.get("/api/moving/search", params={"name": "x", "begin": "2025/01/01"}).status_code == 422


def test_limit_per_visit_spreads_frames():
    from spherex_core.archival.build import limit_per_visit
    from spherex_core.archival.sources import ArchivalImage

    ims = [ArchivalImage("neowise", "W1", f"u{i}", 100.0 + i * 0.01, None) for i in range(100)]
    ims += [ArchivalImage("neowise", "W1", f"v{i}", 300.0 + i * 0.01, None) for i in range(5)]
    out = limit_per_visit(ims, max_frames=10)
    assert len(out) == 15 and out[0].url == "u0" and out[9].url == "u99" and out[-1].url == "v4"
