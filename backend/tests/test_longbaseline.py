"""Long-baseline light curves (ADR-0059), offline: frame photometry, synthetic bands, cross-calibration helpers,
the multiband period / event model, catalogue fallbacks, routes and MCP tools."""

import math

import numpy as np
import pytest
from fastapi.testclient import TestClient

from spherex_api.app import create_app
from spherex_core import services
from spherex_core.field import jobs as fj
from spherex_core.longbaseline import calibrate, model, refcat
from spherex_core.longbaseline.archival_lc import calibrate_frames, combine, robust_mean
from spherex_core.longbaseline.export import lightcurve_csv
from spherex_core.longbaseline.framephot import choose_ring, measure_position, ring_dn
from spherex_core.longbaseline.instruments import INSTRUMENTS
from spherex_core.longbaseline.lightcurve import LongBaselineRequest, anchor, assign_groups
from spherex_core.longbaseline.spherex_lc import band_flux


# ---------------------------------------------------------------- frame photometry
def _gauss_frame(n=101, x0=50.3, y0=49.7, total=1e5, sigma=1.1, bkg=10.0, noise=0.0, seed=1):
    yy, xx = np.mgrid[0:n, 0:n]
    img = total / (2 * np.pi * sigma ** 2) * np.exp(-0.5 * ((xx - x0) ** 2 + (yy - y0) ** 2) / sigma ** 2) + bkg
    if noise:
        img = img + np.random.default_rng(seed).normal(0, noise, img.shape)
    return img, x0, y0


def _gauss_ee(r, sigma):
    return 1 - math.exp(-0.5 * r * r / sigma ** 2)


def test_aperture_on_gaussian_star():
    img, x0, y0 = _gauss_frame()
    bad = np.zeros(img.shape, bool)
    m = measure_position(img, bad, bad, x0, y0, 3.0, 18.0, 25.0, 24, (24.0, 30.0), 1.0)
    assert m["core_r"] == 0 and not m["sat_in_aperture"]
    assert m["bkg"] == pytest.approx(10.0, abs=1e-6)
    # pixel values are point samples spread over their pixel: a Gaussian convolved with the pixel box
    assert m["flux_dn"] == pytest.approx(1e5 * _gauss_ee(3.0, math.sqrt(1.1 ** 2 + 1 / 12)), rel=3e-3)


def test_saturated_core_and_ring():
    sigma = 3.0
    img, x0, y0 = _gauss_frame(sigma=sigma, total=1e7)
    core = np.hypot(*np.mgrid[0:101, 0:101][::-1] - np.array([x0, y0])[:, None, None]) < 4.2
    img[core] = np.nan
    m = measure_position(img, core, core, x0, y0, 3.0, 18.0, 25.0, 24, (24.0, 30.0), 1.0)
    assert m["sat_in_aperture"] and 4.0 <= m["core_r"] <= 5.5
    r_in, r_out = choose_ring([m["core_r"]], 4, 24)
    assert r_in >= m["core_r"]
    flux, err = ring_dn(m["profile"], r_in, r_out)
    # profile bins are floor(r): exactly the pixels whose centres lie in r_in <= r < r_out
    yy, xx = np.mgrid[0:101, 0:101]
    rr = np.hypot(xx - x0, yy - y0)
    ring = (rr >= r_in) & (rr < r_out)
    assert flux == pytest.approx(float((img[ring] - 10.0).sum()), rel=1e-9)
    # ... which is the continuous ring to a few per cent (the EE tables are measured with the same binning)
    assert flux == pytest.approx(1e7 * (_gauss_ee(r_out, sigma) - _gauss_ee(r_in, sigma)), rel=0.08)
    assert err > 0


def test_method_choice_and_visit_combination():
    inst = INSTRUMENTS[("neowise", "W1")]
    prof = {"ntotal": np.full(24, 10), "nvalid": np.full(24, 10), "sums": np.ones(24), "sigma": 1.0, "n_far": 100}
    base = {"flux_dn": 1000.0, "err_dn": 10.0, "magzp": 20.5, "masked_frac": 0.0, "profile": prof, "file": "f"}
    unsat = [{**base, "mjd": 57000.0 + i * 0.1, "core_r": 0.0, "sat_in_aperture": False} for i in range(10)]
    pts, info = calibrate_frames(unsat, inst)
    assert info["method"] == "aperture" and len(pts) == 10
    expect = 1000.0 * inst.apcorr * inst.f0_jy * 10 ** (-0.4 * 20.5) * 1e3
    assert pts[0]["flux_mjy"] == pytest.approx(expect)
    v = combine(pts + [{**p, "mjd": p["mjd"] + 180} for p in pts], inst)
    assert len(v) == 2 and v[0]["n"] == 10 and v[0]["method"] == "aperture"
    assert v[0]["err_mjy"] >= 0.015 * v[0]["flux_mjy"]  # systematic floor
    sat = [{**r, "core_r": 3.0, "sat_in_aperture": True} for r in unsat]
    _, info = calibrate_frames(sat, inst)
    assert info["method"] == "ring" and info["r_in_px"] == 4


def test_robust_mean_rejects_outlier():
    f = np.array([10.0, 10.1, 9.9, 10.05, 9.95, 30.0])
    mean, err, n, rej = robust_mean(f, np.full(f.size, 0.1))
    assert rej == 1 and n == 5 and mean == pytest.approx(10.0, abs=0.05)


# ---------------------------------------------------------------- synthetic bands
def test_band_flux_flat_and_power_law():
    lw = np.linspace(3.0, 3.8, 81)
    resp = np.exp(-0.5 * ((lw - 3.4) / 0.15) ** 2)
    wl = np.linspace(2.6, 4.2, 40)
    flat = band_flux(wl, np.full(wl.size, 5.0), np.full(wl.size, 0.05), "W1", response=(lw, resp))
    assert flat["flux_mjy"] == pytest.approx(5.0, rel=1e-6) and flat["coverage"] == pytest.approx(1.0)
    # 1 % per bin, about seven bins across the band: the band error is well below 1 % but above the floor
    assert 0.02 * 5.0 <= flat["err_mjy"] <= 0.03 * 5.0
    pl = band_flux(wl, wl ** -2.0, 0.01 * wl ** -2.0, "W1", response=(lw, resp))
    w = resp / lw
    assert pl["flux_mjy"] == pytest.approx(float((w * lw ** -2.0).sum() / w.sum()), rel=2e-3)
    gap = band_flux(wl[wl < 3.1], np.ones((wl < 3.1).sum()), np.full((wl < 3.1).sum(), 0.01), "W1",
                    response=(lw, resp))
    assert gap is None or gap["flux_mjy"] is None  # band not covered


# ---------------------------------------------------------------- model
def _mira(seed=0, period=245.0, amp=0.6, n_visits=20, noise=0.03):
    rng = np.random.default_rng(seed)
    pts = []
    t_visits = 56700 + np.arange(n_visits) * 182.6 + rng.normal(0, 3, n_visits)
    for t in t_visits:
        for band, off, a in (("W1", 1.0, amp), ("W2", 0.5, amp * 0.9)):
            pts.append({"mjd": float(t), "band": band, "group": band, "survey": "NEOWISE",
                        "mag": off + a * math.sin(2 * math.pi * t / period) + rng.normal(0, noise),
                        "mag_err": noise})
    for k in range(30):  # SPHEREx relative points (own zero point) on a dense ~20 d cadence
        t = 60800 + 20 * k
        pts.append({"mjd": t, "band": "D6", "group": "D6:SPHEREx-relative", "survey": "SPHEREx",
                    "mag": 3.2 + 1.5 * amp * math.sin(2 * math.pi * t / period) + rng.normal(0, noise),
                    "mag_err": noise})
    return pts


def test_period_recovered_with_free_zero_points():
    a = model.analyze(_mira(), period_bands=("W1", "W2", "D6"))
    p = a["period"]
    assert p["significant"] and abs(p["period_days"] - 245.0) < 3 * max(p["period_err_days"], 0.5)
    assert p["amplitudes_mag"]["W1"] == pytest.approx(1.2, rel=0.15)
    assert a["classification"]["label"] == "long-period variable"
    assert a["events"] == []  # a clean periodic star has no events


def test_constant_stars_false_positive_rate():
    hits = 0
    for seed in range(20):
        pts = _mira(seed=seed, amp=0.0)
        a = model.analyze(pts, period_bands=("W1", "W2", "D6"))
        hits += bool(a["classification"]["periodic"] or a["classification"]["variable"])
    assert hits <= 1


def test_events_need_independent_confirmation():
    t = np.array([0.0, 1.0, 100.0, 101.0, 200.0, 300.0])
    resid = np.array([0.5, 0.5, 0.0, 0.0, 0.0, 0.0])
    err = np.full(6, 0.02)
    sig = np.full(6, 0.02)
    sx = np.array(["SPHEREx", "SPHEREx", "SPHEREx", "SPHEREx", "SPHEREx", "SPHEREx"])
    # one bin each in two SPHEREx bands (one spectrum): not an event
    assert model.events(t, resid, err, np.array(["D4", "W1", "D4", "W1", "D4", "D4"]), sig, sx) == []
    # the same in NEOWISE W1 and W2: confirmed
    neo = np.array(["NEOWISE"] * 6)
    ev = model.events(t, resid, err, np.array(["W1", "W2", "W1", "W2", "W1", "W1"]), sig, neo)
    assert len(ev) == 1 and ev[0]["kind"] == "dimming" and ev[0]["bands"] == ["W1", "W2"]
    # NaN residuals (bands left out of the model) are ignored
    r2 = resid.copy()
    r2[1] = np.nan
    assert model.events(t, r2, err, np.array(["W1", "W2", "W1", "W2", "W1", "W1"]), sig, neo) == []


def test_trends_need_three_years():
    pts = [{"mjd": 60000 + 30 * k, "band": "W1", "group": "W1", "survey": "SPHEREx", "mag": 8 + 0.002 * k,
            "mag_err": 0.01} for k in range(20)]  # 1.6 yr
    assert "W1" not in model.analyze(pts)["trends"]
    pts = [{"mjd": 57000 + 180 * k, "band": "W1", "group": "W1", "survey": "NEOWISE", "mag": 8 + 0.05 * k,
            "mag_err": 0.01} for k in range(16)]  # 7.4 yr, 0.1 mag/yr
    tr = model.analyze(pts, period_bands=("D1",))["trends"]["W1"]  # no period search to absorb the ramp
    assert tr["slope_mag_per_year"] == pytest.approx(0.1, rel=0.05) and tr["significance"] > 5


# ---------------------------------------------------------------- calibration helpers
def test_robust_offset_and_star_selection():
    r = calibrate.robust_offset([0.10, 0.12, 0.08, 0.11, 0.5], 4)
    assert r["applied"] and r["offset_mag"] == pytest.approx(0.11) and r["scatter_mag"] < 0.05
    assert not calibrate.robust_offset([0.1, 0.2], 4)["applied"]
    c = (10.0, 20.0)
    cat = [{"id": "a", "ra": 10.0, "dec": 20.015, "w1": 10.0, "mag": 10.0},  # fine
           {"id": "b", "ra": 10.0, "dec": 20.02, "w1": 7.0, "mag": 7.0},  # saturates NEOWISE
           {"id": "c", "ra": 10.0, "dec": 20.03, "w1": 11.0, "mag": 11.0},  # crowded by d
           {"id": "d", "ra": 10.0, "dec": 20.0302, "w1": 11.5, "mag": 11.5},
           {"id": "e", "ra": 10.0, "dec": 20.0005, "w1": 10.0, "mag": 10.0},  # next to the target
           {"id": "f", "ra": 10.0, "dec": 19.98, "w1": 10.5, "mag": 10.4, "ccf": "h000"}]  # AllWISE halo flag
    ids = [s["id"] for s in calibrate.select_stars(cat, c, 150.0, [c])]
    assert ids == ["a"]
    gaia = [{"id": "g1", "ra": 10.0, "dec": 20.01, "mag": 12.0}, {"id": "g2", "ra": 10.0, "dec": 20.02, "mag": 18.0}]
    sel = calibrate.select_gaia_stars(gaia, c, 150.0, [])
    assert [s["id"] for s in sel] == ["g1"] and sel[0]["w1"] is None and sel[0]["g"] == 12.0


def test_groups_and_anchoring():
    pts = [{"band": "W1", "survey": "NEOWISE", "method": "ring", "mag": 1.0, "mjd": 1},
           {"band": "W1", "survey": "NEOWISE", "method": "ring", "mag": 1.2, "mjd": 2},
           {"band": "W1", "survey": "AllWISE", "method": "ring", "mag": 1.1, "mjd": 0},
           {"band": "W1", "survey": "SPHEREx", "method": "synthetic", "relative": True, "rel": 1.0, "mag": 0.0,
            "mjd": 3},
           {"band": "J", "survey": "2MASS", "method": "psc", "mag": 3.0, "mjd": 0}]
    assign_groups(pts)
    assert [p["group"] for p in pts] == ["W1:NEOWISE-ring", "W1:NEOWISE-ring", "W1:AllWISE-ring",
                                         "W1:SPHEREx-relative", "J"]
    info = anchor(pts, {"W1:NEOWISE-ring": 1.1, "W1:AllWISE-ring": 1.0, "W1:SPHEREx-relative": -0.2, "J": 3.0})
    assert info["W1:SPHEREx-relative"]["reference"] == "W1:NEOWISE-ring"  # the ring group with most points
    assert pts[3]["mag_anchored"] == pytest.approx(1.3) and pts[3]["anchored"]
    assert pts[2]["mag_anchored"] == pytest.approx(1.2)
    assert pts[4]["mag_anchored"] == 3.0 and not pts[4]["anchored"]
    csv = lightcurve_csv({"target": {"name": "x"}, "points": pts})
    assert csv.splitlines()[3].startswith("band,survey,date") and len(csv.splitlines()) == 4 + len(pts)


def test_vizier_parser_and_catalogue_fallback(monkeypatch):
    text = ("#comment\n#more\n\nAllWISE\tRAJ2000\tDEJ2000\tW1mag\tW2mag\tpmRA\tpmDE\tccf\tex\n"
            " \tdeg\tdeg\tmag\tmag\tmas/yr\tmas/yr\t \t \n---\t---\t---\t---\t---\t---\t---\t---\t---\n"
            "J000000.00+000000.0\t10.0\t20.0\t10.5\t10.4\t5\t-3\t0000\t0\n")
    rows = refcat.parse_asu_tsv(text)
    assert rows == [{"AllWISE": "J000000.00+000000.0", "RAJ2000": "10.0", "DEJ2000": "20.0", "W1mag": "10.5",
                     "W2mag": "10.4", "pmRA": "5", "pmDE": "-3", "ccf": "0000", "ex": "0"}]

    def cone(name, *a, **k):
        if name == "gaia":
            return [{"id": "G", "ra": 1.0, "dec": 2.0, "mag": 12.0}]
        raise RuntimeError("HTTP Error 502")

    monkeypatch.setattr(refcat.catalogs, "cone", cone)
    monkeypatch.setattr(refcat, "allwise", lambda *a: [{"id": "AllWISE x", "w1": 10.0, "mag": 9.9}])
    notes = []
    rows, label = refcat.wise_reference(1.0, 2.0, 0.04, notes)
    assert label == "AllWISE" and rows[0]["id"] == "AllWISE x" and "CatWISE2020 unavailable" in notes[0]

    def down(*a):
        raise RuntimeError("mirror down")

    monkeypatch.setattr(refcat, "allwise", down)
    rows, label = refcat.wise_reference(1.0, 2.0, 0.04, notes)
    assert label == "Gaia DR3" and rows[0]["id"] == "G"
    monkeypatch.setattr(refcat, "twomass_vizier", down)
    rows, label = refcat.twomass_reference(1.0, 2.0, 0.04, notes)
    assert rows == [] and label == "" and "2MASS PSC unavailable" in notes[-1]


# ---------------------------------------------------------------- API and MCP
def test_request_key_is_stable():
    a = LongBaselineRequest(ra=10.1234567, dec=-5.0)
    assert a.key()["ra"] == 10.12346 and a.key() == LongBaselineRequest(ra=10.123459, dec=-5.0).key()
    assert fj.make_id("seq", "l", {"lb": a.key()}) != fj.make_id("seq", "l", {"lb": LongBaselineRequest().key()})
    assert fj.KINDS["l"] == "long-baseline light curve" and fj.KINDS["w"] == "long-period search"


def test_routes_validate(tmp_path):
    services.configure(cache_root=tmp_path)
    try:
        c = TestClient(create_app(web_dist=None))
        assert c.post("/api/sequences/nope/long-baseline", json={}).status_code == 404
        assert c.post("/api/sequences/nope/long-baseline", json={"ra": 10.0}).status_code == 422
        assert c.post("/api/sequences/nope/long-period-search", json={"max_sources": 5}).status_code == 404
        assert c.post("/api/sequences/nope/long-period-search", json={"max_sources": 0}).status_code == 422
        assert c.get("/api/long-baseline/nope-f123").status_code == 404  # a field job is not a long-baseline job
        assert c.get("/api/long-baseline/seq-l0123456789ab").status_code == 404
        paths = c.get("/openapi.json").json()["paths"]
        for p in ("/api/sequences/{seq_id}/long-baseline", "/api/sequences/{seq_id}/long-period-search",
                  "/api/long-baseline/{job_id}", "/api/long-baseline/{job_id}/lightcurve.csv"):
            assert p in paths
    finally:
        services.configure(None)


def test_mcp_tools_registered():
    import asyncio

    from spherex_mcp.server import build_server

    names = {t.name for t in asyncio.run(build_server().list_tools())}
    assert {"long_baseline_lightcurve", "long_period_search"} <= names


def test_mcp_summary_and_allwise_rows(monkeypatch, tmp_path):
    from spherex_mcp import tools

    res = {"kind": "long-baseline light curve", "target": {"name": "R Dra", "ra": 1.0, "dec": 2.0},
           "coverage": {"first_year": 1999.4, "last_year": 2026.2, "bands": {"W1": {"n": 30, "surveys": ["NEOWISE"]}}},
           "analysis": {"period": {"period_days": 247.0, "significant": True, "peaks": [{"period_days": 247.0},
                                                                                       {"period_days": 123.0}]},
                        "classification": {"label": "long-period variable"}},
           "methods": {"NEOWISE W1": {"method": "ring"}}, "calibration": {}, "notes": []}
    s = tools._lb_summary(res, 5, "seq-labc")
    assert s["csv"] == "/api/long-baseline/seq-labc/lightcurve.csv"
    assert s["period"]["period_days"] == 247.0 and s["other_peaks_days"] == [123.0]
    monkeypatch.setattr(refcat, "vizier_cone", lambda *a, **k: [
        {"AllWISE": "J1", "RAJ2000": "10.0", "DEJ2000": "20.0", "W1mag": "10.5", "W2mag": "10.4", "pmRA": "-65",
         "e_pmRA": "168", "pmDE": "900", "e_pmDE": "60", "ccf": "0000", "ex": "0"}])
    monkeypatch.setattr(refcat.cache, "is_fresh", lambda *a: False)
    layout = type("L", (), {"catalog": lambda self, *a: tmp_path / "aw.json"})()
    monkeypatch.setattr(refcat.cache, "layout", lambda: layout)
    rows = refcat.allwise(10.0, 20.0, 0.01)
    assert rows[0]["w1"] == 10.5 and rows[0]["mag"] == 10.4 and not rows[0]["extended"]
    assert rows[0]["pmra"] == 0.0 and rows[0]["pmdec"] == 900.0  # noise-level AllWISE motions are dropped



def test_bad_nan_pixels_are_filled_not_saturated():
    """NEOWISE L1b sets bad pixels to NaN: with a bad-pixel bit they are filled from their radius, not a core."""
    from spherex_core.longbaseline.archival_lc import _bad_and_core
    from spherex_core.longbaseline.frames import FrameWindow

    n, x0, y0 = 81, 40.3, 39.8
    yy, xx = np.mgrid[0:n, 0:n]
    img = 1000.0 * np.exp(-0.5 * ((xx - x0) ** 2 + (yy - y0) ** 2) / 1.2 ** 2) + 5.0
    ref = measure_position(img, np.zeros_like(img, bool), np.zeros_like(img, bool), x0, y0, 3.0, 18.0, 25.0, 24,
                           (24.0, 30.0), 1.0)
    img = img.copy()
    mask = np.zeros(img.shape, np.int32)
    img[40, 41] = np.nan
    mask[40, 41] = 0x2  # static "generally noisy" pixel next to the peak
    w = FrameWindow("neowise", "W1", "x-int-1b.fits", 57000.0, "", 0, 0, img.astype(np.float32), mask)
    bad, core, _ = _bad_and_core(w)
    assert bad[40, 41] and not core.any()
    m = measure_position(img, bad, core, x0, y0, 3.0, 18.0, 25.0, 24, (24.0, 30.0), 1.0)
    assert m["core_r"] == 0 and not m["sat_in_aperture"]
    assert abs(m["flux_dn"] / ref["flux_dn"] - 1) < 0.03
    mask[40, 41] = 1 << 12  # saturated in sample read 3: a core
    bad, core, _ = _bad_and_core(w)
    assert core[40, 41]


def test_allwise_ring_follows_neowise_core():
    """AllWISE tiles have no mask: with the NEOWISE ring's inner radius the ring starts outside the real core."""
    inst = INSTRUMENTS[("allwise", "W1")]
    prof = {"ntotal": np.full(48, 100), "nvalid": np.full(48, 100), "sums": np.full(48, 10.0), "sigma": 1.0,
            "n_far": 200}
    recs = [{"flux_dn": 1e4, "err_dn": 10.0, "magzp": 20.5, "core_r": 2.5, "sat_in_aperture": True,
             "masked_frac": 0.0, "mjd": 55300.0 + i, "file": "t", "profile": prof} for i in range(4)]
    _, own = calibrate_frames([dict(r) for r in recs], inst)
    _, info = calibrate_frames([dict(r) for r in recs], inst, core_arcsec=35.75)
    assert own["method"] == info["method"] == "ring" and own["r_in_px"] == 4
    assert info["r_in_px"] == 26 and info["r_out_px"] == 34 and info["core_from"] == "NEOWISE"
