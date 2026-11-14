"""Offline tests for analyses, exports and build filters on synthetic data."""

import io
import math

import numpy as np
import pytest
from astropy.io import fits
from astropy.io.votable import parse_single_table

from spherex_core.analysis.lightcurve import relative_fluxes
from spherex_core.analysis.motion import fit_motion
from spherex_core.analysis.periodogram import periodogram
from spherex_core.archive import Frame
from spherex_core.bands import BANDS
from spherex_core.exports import coadds_fits, download_script, photometry_csv, photometry_votable
from spherex_core.pipeline import results
from spherex_core.pipeline.params import SequenceParams
from spherex_core.processing import make_grid
from spherex_core.timeutil import year_to_mjd


def test_relative_fluxes_match_client_rules():
    ps = [{"wl": 1.0, "flux_mjy": 10.0, "err_mjy": 1.0, "flagged": False},
          {"wl": 1.0, "flux_mjy": 20.0, "err_mjy": 1.0, "flagged": False},
          {"wl": 1.0, "flux_mjy": 1000.0, "err_mjy": 1.0, "flagged": True},  # excluded from the reference
          {"wl": 4.0, "flux_mjy": 5.0, "err_mjy": 0.5, "flagged": False, "relative": True},
          {"wl": 4.0, "flux_mjy": None}]
    out = relative_fluxes(ps)
    assert len(out) == 4
    # upper median of [10, 20] is 20
    assert out[0]["rel"] == 0.5 and out[1]["rel"] == 1.0 and out[2]["rel"] == 50.0
    assert out[3]["rel"] == 1.0 and out[3]["rel_err"] == 0.1


def _sine_records(period=240.0, n=300, bands=("D1", "D3")):
    rng = np.random.default_rng(2)
    t = np.sort(np.concatenate([rng.uniform(c, c + 30, n // 3) for c in (61000, 61180, 61360)]))
    recs = []
    for i, ti in enumerate(t):
        b = bands[i % len(bands)]
        amp = 0.3 if b == "D1" else 0.1
        f = 100 * (1 + amp * math.sin(2 * math.pi * ti / period)) + rng.normal(0, 0.5)
        recs.append({"index": i, "mjd": float(ti), "wl": 1.0 if b == "D1" else 2.0, "band": b, "flux_mjy": f,
                     "err_mjy": 0.5, "flagged": False, "relative": False})
    return recs


@pytest.mark.parametrize("method", ["standard", "multiband"])
def test_periodogram_recovers_period(method):
    out = periodogram(_sine_records(), method=method, min_period=50, max_period=400)
    assert out["method"] == method
    assert abs(out["best"]["period_days"] - 240) < 12
    assert len(out["folded"]) == 300 and 0 <= min(p["phase"] for p in out["folded"])
    assert len(out["periodogram"]["period_days"]) == len(out["periodogram"]["power"])
    if method == "standard":
        assert out["best"]["false_alarm_probability"] < 1e-6 and len(out["model"]["rel"]) == 200
    else:
        assert set(out["model"]["rel_by_band"]) == {"D1", "D3"}


def test_periodogram_rejects_few_points():
    with pytest.raises(ValueError):
        periodogram(_sine_records()[:3])


def _moving_coadds(pmra=2000.0, pmdec=-1500.0, n=41):
    grid = make_grid(150.0, 20.0, n * 6.15 / 60)
    years = [2025.4, 2025.9, 2026.4, 2026.9]
    epochs, data = [], np.zeros((4, 6, grid.n, grid.n), np.float32)
    rng = np.random.default_rng(3)
    yy, xx = np.mgrid[0:grid.n, 0:grid.n]
    cosd = math.cos(math.radians(20.0))
    t_ref = float(np.mean(years))
    for e, yr in enumerate(years):
        ra = 150.0 + pmra / 1000 * (yr - t_ref) / 3600 / cosd
        dec = 20.0 + pmdec / 1000 * (yr - t_ref) / 3600
        x, y = grid.world_to_pixel(ra, dec)
        mjd = float(year_to_mjd(yr))
        for b in range(6):
            data[e, b] = 100 * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * 0.8 ** 2)) + rng.normal(0, 0.5, xx.shape)
        epochs.append({"index": e, "mjd_mean": mjd, "bands": {b: {"mjd_mean": mjd} for b in BANDS}})
    return {"epochs": epochs}, data, grid


def test_motion_fit_recovers_proper_motion():
    meta, data, grid = _moving_coadds()
    c = (grid.n - 1) / 2
    out = fit_motion(meta, data, grid, c, c)
    m = out["combined"]
    assert abs(m["pmra"] - 2000) < 60 and abs(m["pmdec"] + 1500) < 60
    assert m["n_epochs"] == 4 and set(out["per_band"]) == set(BANDS) and len(out["track"]) == 4
    static_meta, static, g2 = _moving_coadds(0.0, 0.0)
    s = fit_motion(static_meta, static, g2, c, c, bands=["D1"])["combined"]
    assert abs(s["pmra"]) < 60 and abs(s["pmdec"]) < 60


def test_motion_fit_needs_a_source():
    meta, data, grid = _moving_coadds()
    with pytest.raises(ValueError):
        fit_motion(meta, np.random.default_rng(0).normal(0, 1, data.shape).astype(np.float32), grid, 5, 5)


def _sequence(tmp_path):
    grid = make_grid(10.0, 20.0, 1.0)
    n = grid.n
    coadds = np.ones((1, 6, n, n), np.float32)
    meta = {"id": "seq1", "version": 3, "created": "2026-11-14T00:00:00+00:00",
            "params": {}, "target": {"name": "Test", "ra": 10.0, "dec": 20.0, "ref_epoch": 2000.0},
            "grid": grid.to_dict(), "sources": {"collections": ["spherex_dr1"], "data": "https://example/"},
            "epochs": [{"index": 0, "partial": False, "date_min": "2025-05-01 00:00", "date_max": "2025-05-02 00:00",
                        "bands": {"D1": {"n": 3, "wl_min": 0.8, "wl_max": 0.9, "mjd_mean": 60796.0, "noise": 0.1,
                                         "filled_pixels": 0}}}],
            "arrays": {k: {"shape": [1, 6, n, n]} for k in ("coadds", "coverage", "flags")},
            "exposures": [{"index": 0, "epoch": 0, "band": "D1", "mjd": 60796.0, "date": "2025-05-01 12:00",
                           "file": "level2_a_1D1_spx_x.fits", "obs_id": "a", "collection": "spherex_dr1"}],
            "photometry": [{"index": 0, "epoch": 0, "band": "D1", "mjd": 60796.0, "wl": 0.85, "dwl": 0.02,
                            "flux_mjy": 1.5, "err_mjy": 0.1, "ab_mag": 16.0, "method": "psf", "relative": False,
                            "flagged": False, "saturated": False, "npix": 30, "chi2": 1.1}]}
    arrays = {"coadds": coadds, "coverage": np.full((1, 6, n, n), 3, np.uint16), "flags": np.zeros((1, 6, n, n), np.uint32)}
    d = tmp_path / "seq1"
    results.write_result(d, meta, arrays)
    return d, results.read_meta(d)


def test_fits_exports(tmp_path):
    d, meta = _sequence(tmp_path)
    one = fits.open(io.BytesIO(coadds_fits(d, meta, 0, "D1", coverage=True)))
    assert one[0].header["DETECTOR"] == "D1" and one[0].header["BUNIT"] == "MJy/sr"
    assert one[0].header["CTYPE1"] == "RA---TAN" and one[0].header["SEQID"] == "seq1" and one[1].name == "COVERAGE"
    mef = fits.open(io.BytesIO(coadds_fits(d, meta)))
    assert [h.name for h in mef[1:]] == ["E0_D1"] and mef[0].header["OBJECT"] == "Test"
    from spherex_core.errors import NotFound
    with pytest.raises(NotFound):
        coadds_fits(d, meta, 0, "D6")


def test_table_exports(tmp_path):
    _, meta = _sequence(tmp_path)
    csv = photometry_csv(meta).decode().splitlines()
    assert csv[0].startswith("index,epoch,band,mjd,date,wl") and "level2_a_1D1_spx_x.fits" in csv[1]
    t = parse_single_table(io.BytesIO(photometry_votable(meta))).to_table()
    assert t["flux_mjy"].unit == "mJy" and t["file"][0] == "level2_a_1D1_spx_x.fits"


def test_download_script(tmp_path):
    _, meta = _sequence(tmp_path)
    f = Frame(uri="ibe/data/spherex/dr1/level2/w/v/1/level2_a_1D1_spx_x.fits", band="D1", mjd=1, mjd_start=1,
              mjd_end=1, collection="spherex_dr1", obs_id="a", wl_min_um=0.8, wl_max_um=0.9)
    full = download_script(meta, [f, None])
    assert full.startswith("#!/bin/sh") and "https://irsa.ipac.caltech.edu/ibe/data/spherex/dr1" in full
    assert "nasa-irsa-spherex.s3.us-east-1.amazonaws.com/dr1/level2" in download_script(meta, [f], source="s3")
    assert "?center=10.000000,20.000000d&size=" in download_script(meta, [f], kind="cutout")
    with pytest.raises(ValueError):
        download_script(meta, [f], kind="zip")


def test_filters_keep_key_and_select():
    base = SequenceParams(ra=1.0, dec=2.0)
    assert SequenceParams(ra=1.0, dec=2.0, date_min=None, bands=None).key() == base.key()
    filt = SequenceParams(ra=1.0, dec=2.0, date_min=60800.0, bands=("D6",))
    assert filt.key() != base.key()
    fr = [Frame("u", b, m, m, m, "c", "o", 1, 2) for b, m in [("D1", 60790.0), ("D6", 60801.0), ("D6", 60700.0)]]
    assert [(f.band, f.mjd) for f in filt.select(fr)] == [("D6", 60801.0)]
