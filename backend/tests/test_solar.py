"""Solar-system science (ADR-0057), offline: static sky, comet, asteroid and unknown-mover helpers, a synthetic
co-moving sequence through the comet analysis and the HTTP routes."""

import math

import numpy as np
import pytest
from astropy.wcs import WCS
from fastapi.testclient import TestClient

from spherex_api.app import create_app
from spherex_core import cache, services
from spherex_core.analysis.common import load_context
from spherex_core.errors import NotFound
from spherex_core.field.jobs import KINDS, make_id, split_id
from spherex_core.photometry import mjy_per_unit
from spherex_core.pipeline import results
from spherex_core.pipeline.params import SequenceParams
from spherex_core.solar import asteroid, comet, movers, skyframes, static_sky, sun
from spherex_core.solar.common import aperture_flux, fill_rings
from spherex_core.solar.geometry import km_to_arcsec, parse_observer_csv
from spherex_core.targets.model import Target

ARCSEC2_SR = (math.pi / 648000.0) ** 2


@pytest.fixture(autouse=True)
def _blackbody_sun(monkeypatch):
    """No CALSPEC download in tests: the 5772 K blackbody."""
    monkeypatch.setattr(sun, "_SUN", sun._blackbody())


# ---------------------------------------------------------------- footprints and the MOST fixed point
def _corners(ra0, dec0, rot_deg=20.0, n=2040, pix=6.15):
    w = WCS(naxis=2)
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    w.wcs.crval = [ra0, dec0]
    w.wcs.crpix = [(n + 1) / 2, (n + 1) / 2]
    c, s = math.cos(math.radians(rot_deg)), math.sin(math.radians(rot_deg))
    w.wcs.cd = np.array([[-c, s], [s, c]]) * pix / 3600.0
    px = [(-0.5, -0.5), (n - 0.5, -0.5), (n - 0.5, n - 0.5), (-0.5, n - 0.5)]
    return w, [list(map(float, w.wcs_pix2world([p], 0)[0])) for p in px]


@pytest.mark.parametrize("ra0,dec0", [(120.0, 30.0), (0.3, -10.0), (250.0, 80.0)])
def test_footprint_pixel_inverts_the_corners(ra0, dec0):
    w, corners = _corners(ra0, dec0)
    for x, y in [(1019.5, 1019.5), (100.0, 1800.0), (2000.0, 40.0)]:
        ra, dec = w.wcs_pix2world([[x, y]], 0)[0]
        fx, fy, inside = skyframes.footprint_pixel(corners, ra, dec)
        assert inside and abs(fx - x) < 0.5 and abs(fy - y) < 0.5
    ra, dec = w.wcs_pix2world([[2200.0, 1000.0]], 0)[0]
    assert not skyframes.contains(corners, ra, dec)


def test_fixed_point_elements_point_at_the_position():
    ra, dec = 287.3, -12.6
    parts = skyframes.fixed_point_elements(ra, dec).split()
    mean_anomaly, peri, node, incl, ecc, a = (float(parts[k]) for k in (4, 5, 6, 7, 8, 10))
    assert incl == 90.0 and ecc == 0.0 and a == 1e5 and mean_anomaly == 0.0 and parts[3] == "K25AD"
    lam, bet = skyframes._ecliptic(ra, dec)
    u, om = math.radians(peri), math.radians(node)  # circular, i = 90: argument of latitude = latitude
    vec = np.array([math.cos(om) * math.cos(u), math.sin(om) * math.cos(u), math.sin(u)])
    want = np.array([math.cos(math.radians(bet)) * math.cos(math.radians(lam)),
                     math.cos(math.radians(bet)) * math.sin(math.radians(lam)), math.sin(math.radians(bet))])
    assert np.allclose(vec, want, atol=1e-7)


def test_parse_ipac_fixed():
    text = ("\\fixlen = T\n"
            "|  uri        | productid |  ra1   |\n"
            "|  char       |   char    | double |\n"
            "   ibe/a.fits   D1          10.5\n"
            "   ibe/b.fits   D6          11.0\n")
    rows = skyframes.parse_ipac_fixed(text)
    assert rows == [{"uri": "ibe/a.fits", "productid": "D1", "ra1": "10.5"},
                    {"uri": "ibe/b.fits", "productid": "D6", "ra1": "11.0"}]


# ---------------------------------------------------------------- static sky
def test_combine_templates_rules():
    a, b, c = (np.full((3, 3), v, np.float32) for v in (1.0, 2.0, 4.0))
    t, method, dls = static_sky.combine_templates([(0.1, a), (-0.2, b), (0.4, c)])
    assert method == "median" and np.allclose(t, 2.0) and len(dls) == 3
    t, method, dls = static_sky.combine_templates([(-1.0, a), (1.0, c)])
    assert method == "interp" and np.allclose(t, 2.5) and dls == [-1.0, 1.0]
    assert static_sky.combine_templates([(0.3, a)])[1] == "single"
    assert static_sky.combine_templates([(1.2, a), (1.4, b)])[1] == "nearest"
    assert static_sky.combine_templates([(2.0, a)]) == (None, "none", [])


def _star_field(n=61, seed=1, nstars=25, noise=1.0):
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:n, 0:n]
    stars = np.zeros((n, n))
    for _ in range(nstars):
        x, y = rng.uniform(3, n - 3, 2)
        stars += rng.uniform(50, 3000) * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * 0.8 ** 2))
    return stars, rng, noise


def test_clean_exposure_keeps_the_object_and_removes_stars():
    n = 61
    stars, rng, noise = _star_field(n)
    c = (n - 1) / 2
    yy, xx = np.mgrid[0:n, 0:n]
    obj = 200.0 * np.exp(-((xx - c) ** 2 + (yy - c) ** 2) / (2 * 1.0 ** 2))
    stars[np.hypot(xx - c, yy - c) < 6] = 0.0  # a star right on the object would be masked, by design
    aligned = (5.0 + stars * 1.05 + obj + rng.normal(0, noise, (n, n))).astype(np.float32)
    template = (2.0 + stars + rng.normal(0, noise, (n, n))).astype(np.float32)
    clean, st = static_sky.clean_exposure(aligned, template, noise)
    assert abs(st["offset"] - 3.0) < 0.3
    assert 0.02 < st["eps"] < 0.1 and st["eps_measured"]
    assert st["p99_before"] > 20 and st["p99_after"] < 5
    assert np.isfinite(clean[int(c), int(c)]) and abs(clean[int(c), int(c)] - 200.0) < 6
    assert 0 < st["masked_frac"] < 0.3


def test_clusters():
    cent, lab = static_sky.clusters(np.array([[10.0, 0.0], [10.2, 0.1], [50.0, 0.0], [10.1, -0.1]]))
    assert len(cent) == 2 and lab.tolist() == [0, 0, 1, 0]


def _target(eph):
    return Target(name="obj", ra=float(eph[0][1]), dec=float(eph[0][2]), ephemeris=[list(map(float, r)) for r in eph])


def test_object_near_interpolates_extrapolates_and_forgets():
    # a slow object (0.05 deg/day, 29P-like) seen over one day; a fast one (1 deg/day)
    slow = _target([[60000.0, 100.0, 0.0], [60000.5, 100.025, 0.0], [60001.0, 100.05, 0.0]])
    assert static_sky.object_near(slow, 60000.25, 100.0125, 0.0, 1.0)  # inside the visit
    assert not static_sky.object_near(slow, 60000.25, 100.5, 0.0, 5.0)
    # two days after the visit it is 0.1 deg further on: near that point, not the visit's last one
    assert static_sky.object_near(slow, 60003.0, 100.15, 0.0, 3.0)
    assert not static_sky.object_near(slow, 60003.0, 100.05, 0.0, 3.0)
    assert not static_sky.object_near(slow, 60100.0, 100.05, 0.0, 30.0)  # beyond EXTRAPOLATE_DAYS
    fast = _target([[60000.0, 100.0, 0.0], [60000.5, 100.5, 0.0]])
    assert not static_sky.object_near(fast, 60003.0, 100.0, 0.0, 6.0)  # it has left the field
    ra, dec, dt, speed = static_sky.local_position(np.array(fast.ephemeris), 60001.0)
    assert abs(ra - 101.0) < 1e-9 and abs(dt - 0.5) < 1e-9 and abs(speed - 1.0) < 1e-9


def test_results_clean_arrays_round_trip(tmp_path):
    from test_results import make_result

    meta, arrays = make_result()
    d = tmp_path / "abc123"
    results.write_result(d, meta, arrays)
    with pytest.raises(NotFound, match="no static-sky"):
        results.array_path(d, "clean")
    with pytest.raises(NotFound):
        results.read_exposures(d, meta, 0, "D1", name="clean_exposures")
    n = 5
    arrays["clean"] = arrays["coadds"] * 0.5
    arrays["clean_coverage"] = arrays["coverage"]
    arrays["clean_exposures"] = arrays["exposures"] + 1
    cube = meta["arrays"]["coadds"]["shape"]
    meta["arrays"].update(clean={"file": "clean.f32", "dtype": "float32", "shape": cube},
                          clean_coverage={"file": "clean_coverage.u16", "dtype": "uint16", "shape": cube},
                          clean_exposures={"file": "clean_exposures.f16", "dtype": "float16", "shape": [4, n, n]})
    results.write_result(d, meta, arrays)
    back = results.read_meta(d)
    np.testing.assert_array_equal(results.read_array(d, back, "clean"), arrays["clean"])
    stack, idx = results.read_exposures(d, back, 0, "D1", name="clean_exposures")
    assert idx == [0, 1] and np.allclose(stack, arrays["exposures"][:2] + 1)


def test_params_key_and_request_validation():
    base = dict(ra=10.0, dec=5.0, object_name="3I", object_type="Comet")
    assert SequenceParams(**base).key() != SequenceParams(**base, static_sky=True).key()
    assert SequenceParams(ra=10.0, dec=5.0).key() == SequenceParams(ra=10.0, dec=5.0, static_sky=False).key()
    from spherex_api.schemas import SequenceRequest

    with pytest.raises(ValueError, match="static_sky needs a solar-system object"):
        SequenceRequest(ra=10.0, dec=5.0, static_sky=True)
    assert SequenceRequest(**base, static_sky=True).params().static_sky


def test_job_kinds():
    for k in ("c", "a", "u"):
        assert k in KINDS
        jid = make_id("abc123", k, {"x": 1})
        assert split_id(jid) == ("abc123", jid.split("-")[1])


# ---------------------------------------------------------------- comet helpers
def test_afrho_and_phase():
    assert comet.phase_factor(0.0) == pytest.approx(1.0)
    assert comet.phase_factor(30.0) < comet.phase_factor(10.0) < 1.0
    # F/F_sun = 1e-12 at r = 1 au, Delta = 1 au, rho = 1e4 km -> (2 au)^2 / rho x 1e-12
    want = (2 * comet.AU_CM) ** 2 / 1e9 * 1e-12
    assert comet.afrho_cm(1e-12, 1.0, 1.0, 1.0, 1e4) == pytest.approx(want)
    assert km_to_arcsec(725.27, 1.0) == pytest.approx(1.0)


def _channels(lo=3.8, hi=5.0, n=120):
    wl = np.linspace(lo, hi, n)
    return wl, wl / 120.0  # R ~ 120 at 4-5 um


def test_band_fit_recovers_an_emission_band():
    co2 = next(f for f in comet.FEATURES if f["id"] == "co2")
    wl, dwl = _channels()
    rng = np.random.default_rng(3)
    cont = 2.0 + 0.3 * (wl - 4.26)
    resp = comet._line_response(wl, dwl, co2["center"], co2["width"])  # per Hz of line flux
    line_mjy_hz = 0.5 / resp.max()  # integrated line flux (mJy Hz) peaking at 0.5 mJy
    err = np.full(wl.size, 0.02)
    flux = cont + line_mjy_hz * resp + rng.normal(0, err)
    fit = comet.band_fit(wl, dwl, flux, err, co2, np.random.default_rng(0))
    assert fit["detected"] and fit["value"] == pytest.approx(line_mjy_hz, rel=0.1)
    flat = comet.band_fit(wl, dwl, cont + rng.normal(0, err), err, co2, np.random.default_rng(0))
    assert not flat["detected"] and abs(flat["value"]) < 3 * flat["err"]


def test_band_fit_absorption_depth_and_missing_channels():
    ice = next(f for f in comet.FEATURES if f["id"] == "h2o_ice_3.0")
    wl = np.linspace(2.3, 3.8, 90)
    dwl = wl / 40.0
    flux = np.where((wl >= 2.85) & (wl <= 3.25), 0.7, 1.0)
    fit = comet.band_fit(wl, dwl, flux, np.full(wl.size, 0.01), ice)
    assert fit["value"] == pytest.approx(0.3, abs=0.01) and fit["detected"]
    none = comet.band_fit(wl[:10], dwl[:10], flux[:10], np.full(10, 0.01), ice)
    assert none["value"] is None and "not enough" in none["note"]


def test_production_proxy_scaling():
    co2 = next(f for f in comet.FEATURES if f["id"] == "co2")
    q1 = comet.production_proxy(1e-17, co2, 2.0, 1.5, 2e4)
    assert q1 > 0
    assert comet.production_proxy(2e-17, co2, 2.0, 1.5, 2e4) == pytest.approx(2 * q1)
    assert comet.production_proxy(1e-17, co2, 2.0, 1.5, 4e4) == pytest.approx(q1 / 2)
    assert comet.production_proxy(1e-17, co2, 2.0, 3.0, 2e4) == pytest.approx(4 * q1)
    assert comet.production_proxy(-1e-17, co2, 2.0, 1.5, 2e4) is None


def test_log_slope_of_a_steady_coma():
    r = np.arange(1.0, 40.0)
    sl = comet.log_slope(r, 100.0 / r, np.full(r.size, 0.01), 3, 30)
    assert sl["slope"] == pytest.approx(-1.0, abs=1e-6)


def test_aperture_flux_and_ring_fill():
    n, pix = 31, 6.2
    img = np.full((n, n), 2.0, np.float32)
    m = aperture_flux(img, 15, 15, 5.0, pix, 0.1)
    assert m["area_px"] == pytest.approx(math.pi * 25, rel=0.02)
    assert m["flux_mjy"] == pytest.approx(2.0 * m["area_px"] * mjy_per_unit(pix ** 2))
    holed = img.copy()
    holed[15, 17:19] = np.nan
    filled, bad = fill_rings(holed, 15, 15)
    assert bad.sum() == 2 and np.allclose(filled, 2.0)
    assert aperture_flux(holed, 15, 15, 5.0, pix, 0.1)["masked_frac"] > 0
    assert aperture_flux(img, 15, 15, 20.0, pix, 0.1) is None  # leaves the grid


def test_sun_blackbody_fallback():
    wl, fnu, src = sun._blackbody()
    assert "blackbody" in src
    nu = 2.99792458e14 / wl
    total = -np.trapezoid(fnu * 1e-29, nu)  # W m-2 between 0.3 and 10 um: 97 % of the 1361 W m-2
    assert 0.95 * 1361 < total < 1361
    assert sun.sun_fnu(1.0, 0.01) == pytest.approx(sun.sun_fnu(1.0), rel=0.01)


def test_parse_observer_csv():
    text = ("header\n$$SOE\n 2025-Aug-10 00:00, , , 3.2210, -12.5, 2.7110, 20.1, 17.10, /T, 1.0,\n"
            " 2025-Aug-11 00:00, , , 3.2100, -12.4, 2.7000, 20.0, 17.20, /T, 1.0,\n$$EOE\n")
    assert parse_observer_csv(text) == [[3.221, 2.711, 17.1], [3.21, 2.7, 17.2]]


# ---------------------------------------------------------------- a synthetic comet through the analysis
AFRHO_2AU = 300.0  # cm at r = 2 au, scaled as r^-2


def _coma(n, pix, k):
    """Pixel-averaged surface brightness K / theta (theta in arcsec), MJy/sr."""
    c = (n - 1) / 2
    sub = (np.arange(8) + 0.5) / 8 - 0.5  # never exactly on the nucleus
    yy, xx = np.mgrid[0:n, 0:n]
    th = np.hypot(xx[..., None, None] + sub[None, None, None, :] - c,
                  yy[..., None, None] + sub[None, None, :, None] - c) * pix
    return (k / th).mean(axis=(2, 3)).astype(np.float32)


def _comet_sequence(tmp_path, monkeypatch):
    services.configure(cache_root=tmp_path)
    n, pix = 41, 6.2
    epochs_r = [2.0, 2.5]
    delta = 1.5
    exps, images = [], []
    wls = {"D1": (0.8, 1.05), "D2": (1.1, 1.6), "D3": (1.65, 2.4), "D4": (2.45, 3.8), "D5": (3.85, 4.4),
           "D6": (4.45, 5.0)}
    for e, r in enumerate(epochs_r):
        afrho = AFRHO_2AU * (r / 2.0) ** -2
        for b, (lo, hi) in wls.items():
            for wl in np.linspace(lo, hi, 4):
                dwl = wl / 40.0
                fsun = float(sun.sun_fnu(wl, dwl))
                # Afrho = 4 Delta_cm^2 r^2 / rho_cm x F / F_sun with F = 2 pi K theta (arcsec^2 -> sr, MJy -> mJy)
                k = afrho * 725.27e5 * delta * fsun / (4 * (delta * comet.AU_CM) ** 2 * r ** 2 * 2 * math.pi
                                                       * ARCSEC2_SR * 1e9)
                i = len(exps)
                exps.append({"index": i, "epoch": e, "band": b, "mjd": 60900.0 + 100 * e + i * 0.01, "wl": float(wl),
                             "dwl": float(dwl), "rms": 1e-4, "psf_fwhm": 9.0, "ss_n": 4, "ss_method": "median",
                             "file": f"f{i}.fits"})
                images.append(_coma(n, pix, k))
    images = np.stack(images)
    cube = np.full((2, 6, n, n), np.nan, np.float32)
    for e in range(2):
        for bi, b in enumerate(wls):
            sel = [x["index"] for x in exps if x["epoch"] == e and x["band"] == b]
            cube[e, bi] = images[sel].mean(axis=0)
    epochs = [{"index": e, "date_min": f"2025-0{8 + e}-01T00:00:00", "date_max": f"2025-0{8 + e}-02T00:00:00",
               "mjd_mean": 60900.0 + 100 * e, "n": 24, "bands": {}} for e in range(2)]
    p = SequenceParams(ra=10.0, dec=5.0, object_name="C/2099 T1", object_type="Comet", static_sky=True)
    shape = [2, 6, n, n]
    meta = {"id": p.key(), "params": {"ra": 10.0, "dec": 5.0, "object_name": "C/2099 T1", "object_type": "Comet",
                                      "static_sky": True},
            "grid": {"ra": 10.0, "dec": 5.0, "n": n, "pixscale_arcsec": pix, "fov_arcmin": n * pix / 60,
                     "frame": "comoving"},
            "moving": {"object": "C/2099 T1"}, "epochs": epochs, "exposures": exps,
            "static_sky": {"version": 1, "method": "test", "exposures": len(exps), "with_template": len(exps)},
            "arrays": {"coadds": {"file": "coadds.f32", "dtype": "float32", "shape": shape},
                       "clean": {"file": "clean.f32", "dtype": "float32", "shape": shape},
                       "exposures": {"file": "exposures.f16", "dtype": "float16", "shape": list(images.shape)},
                       "clean_exposures": {"file": "clean_exposures.f16", "dtype": "float16",
                                           "shape": list(images.shape)}}}
    arrays = {"coadds": cube + 0.01, "clean": cube, "exposures": images, "clean_exposures": images}
    results.write_result(cache.layout().sequence(p.key()), meta, arrays)
    geom = [{"r_au": epochs_r[x["epoch"]], "delta_au": delta, "phase_deg": 20.0, "source": "test"} for x in exps]
    monkeypatch.setattr(comet, "exposure_geometry", lambda meta: geom)
    return p.key(), meta


def test_comet_analysis_recovers_afrho_and_its_trend(tmp_path, monkeypatch):
    seq, _ = _comet_sequence(tmp_path, monkeypatch)
    try:
        res = comet.comet_analysis(load_context(seq), comet.CometOptions.from_dict({"rho_km": [2e4, 5e4, 1e5]}))
    finally:
        services.configure(None)
    assert res["kind"] == "comet" and res["static_sky"] is True
    for ep, r in zip(res["epochs"], (2.0, 2.5)):
        want = AFRHO_2AU * (r / 2.0) ** -2
        for rho in ("20000", "50000", "100000"):
            got = ep["afrho"][rho]["D1"]
            assert got["afrho_cm"] == pytest.approx(want, rel=0.08), (rho, got)
            assert got["a0frho_cm"] / got["afrho_cm"] == pytest.approx(1 / float(comet.phase_factor(20.0)))
        assert abs(ep["dust_colour"]["slope_pct_per_100nm"]) < 1.0  # grey dust
        co2 = next(b for b in ep["bands"]["20000"] if b["id"] == "co2")
        assert not co2["detected"] and co2["q_upper_3sigma"] > 0  # no gas injected: an upper limit
    assert res["activity_trend"]["k"] == pytest.approx(-2.0, abs=0.15)
    slopes = [p["slope"]["slope"] for p in res["profiles"] if p["slope"]]
    assert slopes and all(abs(s + 1.0) < 0.15 for s in slopes)


def test_solar_routes(tmp_path, monkeypatch):
    seq, meta = _comet_sequence(tmp_path, monkeypatch)
    from test_results import make_result

    plain_meta, plain_arrays = make_result()
    results.write_result(cache.layout().sequence("abc123"), plain_meta, plain_arrays)
    c = TestClient(create_app(web_dist=None))
    try:
        n = meta["grid"]["n"]
        r = c.get(f"/api/sequences/{seq}/clean.bin")
        assert r.status_code == 200 and len(r.content) == 2 * 6 * n * n * 4
        r = c.get(f"/api/sequences/{seq}/clean_exposures.bin", params={"epoch": 1, "band": "D5"})
        assert r.status_code == 200 and r.headers["x-exposure-indices"] == "40,41,42,43"
        assert len(r.content) == 4 * n * n * 4
        s = c.get(f"/api/sequences/{seq}/clean-summary", params={"epoch": 0}).json()
        assert s["object"] == "C/2099 T1" and len(s["epochs"]) == 6 and s["epochs"][0]["centre_snr"] > 0
        r = c.post(f"/api/sequences/{seq}/static-sky", json={})
        assert r.json() == {"sequence": seq, "source": seq, "status": "done", "job": None}
        assert c.get("/api/sequences/abc123/clean.bin").status_code == 404
        assert c.get("/api/sequences/abc123/clean-summary").status_code == 404
        r = c.post("/api/sequences/abc123/static-sky", json={})
        assert r.status_code == 404 and "co-moving" in r.json()["detail"]
        assert c.post(f"/api/sequences/{seq}/comet", json={"rho_km": [-1]}).status_code == 422
        assert c.post(f"/api/sequences/{seq}/asteroid", json={"min_period_h": 0.01}).status_code == 422
        r = c.post("/api/sequences", json={"ra": 1, "dec": 2, "static_sky": True})
        assert r.status_code == 422
    finally:
        services.configure(None)


# ---------------------------------------------------------------- asteroid helpers
def _lightcurve(period_h=5.27, n_per_epoch=90, seed=0, noise=0.01, h1=0.06, h3=0.0, h4=0.0):
    rng = np.random.default_rng(seed)
    f = 24.0 / period_h
    pts = []
    colours = {"D1": 0.0, "D2": -0.1, "D3": -0.2, "D4": -0.25, "D5": 0.1, "D6": 0.3}
    for e, t0 in enumerate((60900.0, 61080.0)):
        t = np.sort(t0 + rng.uniform(0, 2.0, n_per_epoch))
        for k, tt in enumerate(t):
            det = list(colours)[k % 6]
            wl = 0.8 + 0.7 * (k % 6) + rng.uniform(0, 0.3)
            ph = 2 * np.pi * f * tt
            mag = (0.4 * np.cos(2 * ph) + h1 * np.cos(ph + 0.4) + h3 * np.cos(3 * ph + 1.0) + h4 * np.cos(4 * ph)
                   + colours[det] + rng.normal(0, noise))
            pts.append({"t": tt, "mag": mag, "err": noise, "det": det, "wl": wl, "alpha": 20.0 + e, "epoch": e})
    return pts


def test_period_search_finds_a_double_peaked_rotation():
    ps = asteroid.period_search(_lightcurve(), asteroid.AsteroidOptions())
    assert ps["best_period_h"] == pytest.approx(5.27, abs=0.01)
    assert ps["rotation"]["double_peak_confirmed"]
    assert ps["period_err_h"] and ps["period_err_h"] < 0.05
    assert {e["epoch"] for e in ps["epochs"]} == {0, 1}
    assert ps["confidence"] == "high" and ps["agreeing_epochs"] == [0, 1]


def test_period_confidence_flags_disagreement_and_orbit_aliases():
    ep = [{"epoch": 0, "best_period_h": 1.62}, {"epoch": 1, "best_period_h": 3.46}]
    c = asteroid.period_confidence(ep, 3.46, 3.46)
    assert c["confidence"] == "low" and c["agreeing_epochs"] == [1]
    assert any("orbital period" in w for w in c["warnings"])
    c = asteroid.period_confidence([{"epoch": 0, "best_period_h": 1.64}, {"epoch": 1, "best_period_h": 1.645}],
                                   1.643, 3.286)
    assert c["confidence"] == "medium" and "alias" in c["warnings"][0]
    eros = [{"epoch": 0, "best_period_h": 1.604}, {"epoch": 2, "best_period_h": 2.6351},
            {"epoch": 3, "best_period_h": 2.6358}]  # one visit at the orbit alias, two agreeing: still high
    assert asteroid.period_confidence(eros, 2.6351, 5.2703)["confidence"] == "high"


@pytest.mark.parametrize("h4", [0.15, -0.15])
def test_period_search_doubles_a_second_harmonic_peak(h4):
    """A strong 4th harmonic (non-sinusoidal maxima or minima, as Eros) puts the periodogram peak at P/2: one
    maximum per cycle there, so the rotation is twice the peak, and the unequal maxima (odd harmonics) confirm it."""
    ps = asteroid.period_search(_lightcurve(h4=h4, h1=0.03, h3=0.02), asteroid.AsteroidOptions())
    assert ps["periodogram_peak_h"] == pytest.approx(5.27 / 2, abs=0.01)
    assert ps["best_period_h"] == pytest.approx(5.27, abs=0.02) and ps["nterms"] == 4
    assert ps["rotation"]["double_peak_confirmed"] and ps["rotation"]["odd_harmonics_p"] < 1e-3


def test_period_search_needs_points():
    ps = asteroid.period_search(_lightcurve(n_per_epoch=20), asteroid.AsteroidOptions())
    assert ps["epochs"] == [] and "fewer than" in ps["note"]
    with pytest.raises(ValueError):
        asteroid.AsteroidOptions.from_dict({"min_period_h": 5, "max_period_h": 4})


def test_maxima_uses_prominence():
    x = np.linspace(0, 2 * np.pi, 201)
    assert asteroid._maxima(np.cos(2 * x)) == 2  # a double-peaked curve
    assert asteroid._maxima(np.cos(x)) == 1
    assert asteroid._maxima(0.4 * np.cos(x) - 0.15 * np.cos(2 * x)) == 1  # broad maximum with a shallow dip
    assert asteroid._maxima(-(0.4 * np.cos(x) + 0.15 * np.cos(2 * x))) == 1


def test_band_depth_binning_and_classes():
    wl = np.linspace(0.75, 2.5, 200)
    r = (1 + 0.3 * (wl - 1.25)) * (1 - 0.2 * np.exp(-0.5 * ((wl - 0.95) / 0.08) ** 2))
    e = np.full(wl.size, 0.005)
    d = asteroid.band_depth(wl, r, e, (0.74, 0.80), (1.40, 1.60), (0.85, 1.15))
    assert d["depth"] == pytest.approx(0.19, abs=0.03) and abs(d["center_um"] - 0.95) < 0.03
    binned = asteroid.bin_spectrum(wl, np.where(wl < 1.1, "D1", np.where(wl < 1.6, "D2", "D3")), r, e)
    assert binned["wl"].size > 10 and np.all(np.diff(binned["wl"]) > 0)
    classes = {"S": (0.2, 0.05, 30), "V": (0.4, 0.2, 10), "C": (0.02, None, 2), "X": (0.02, None, 20),
               "D": (0.01, None, 50)}
    for want, (b1, b2, slope) in classes.items():
        feat = {"band_1um": {"depth": b1}, "band_2um": {"depth": b2} if b2 else None, "slope": {"pct_per_um": slope}}
        assert asteroid.classify(feat)["complex"] == want
    assert asteroid.classify({"slope": {"pct_per_um": 5}})["complex"] is None
    hyd = asteroid.classify({"band_1um": {"depth": 0.01}, "slope": {"pct_per_um": 2},
                             "band_3um": {"depth": 0.1, "snr": 5}})
    assert hyd["hydrated"] is True


def test_thermal_fit_finds_a_warm_tail():
    wl = np.linspace(1.4, 5.0, 120)
    sun_f = sun.sun_fnu(wl)
    r = 1.0 + 0.1 * (wl - 2.0) + 3e15 * asteroid._bb_nu(wl, 300.0) / sun_f
    th = asteroid.thermal_fit(wl, r, np.full(wl.size, 0.01), sun_f)
    assert th["temperature_k"] == pytest.approx(300, abs=15)


# ---------------------------------------------------------------- unknown movers
def test_ecliptic_geometry():
    assert movers.ecliptic_pa(0.0, 0.0) == pytest.approx(90 - 23.4392911, abs=1e-6)
    assert movers.ecliptic_latitude(90.0, 23.4392911) == pytest.approx(0.0, abs=1e-6)
    assert movers.ecliptic_latitude(270.0, 66.5607089) == pytest.approx(90.0, abs=1e-6)
    assert movers.population_hint(80) .startswith("fast") and movers.population_hint(2).startswith("very slow")


def _tracklet(tid, known=None, ident=None, n=5, rb=0.8, quality="high"):
    det = [{"ra": 10.0 + 0.001 * k, "dec": 0.0, "mjd": 60900.0 + 0.01 * k, "band": "D1" if k % 2 else "D4",
            "x": 1.0, "y": 2.0, "snr": 9.0} for k in range(n)]
    return {"id": tid, "epoch": 0, "n": n, "detections": det, "rate_arcsec_per_hr": 15.0, "pa_deg": 90.0,
            "mean_snr": 9.0, "mean_rb": rb, "partner_fraction": 1.0, "quality": quality, "rms_arcsec": 1.2,
            "known": known, "identification": ident}


class _Ctx:
    seq_id = "abc123"
    meta = {"grid": {"frame": "fixed"}}


def test_unknown_movers_from_a_field_result():
    field = {"sequence": "abc123", "stats": {"transients": {"identification": {"objects_in_field": 1}}},
             "candidates": {"tracklets": [_tracklet("T1", known={"name": "(1234) Test"},
                                                    ident=[{"name": "(1234) Test"}]),
                                          _tracklet("T2", ident=[]), _tracklet("T3", ident=[], n=3, rb=0.3)]}}
    res = movers.unknown_movers(_Ctx(), 21.0, field)
    assert res["counts"] == {"tracklets": 3, "known": 1, "unidentified": 2, "unverified": 0, "strong": 1}
    first = res["unidentified"][0]
    assert first["id"] == "T2" and first["tier"] == "strong" and first["trk_sub"] == "Uabc001"
    assert first["bands"] == ["D1", "D4"] and first["population_hint"] == "main-belt-like"
    assert [t["quality"] for t in res["candidates"]["tracklets"]] == ["high", "low"]
    assert res["candidates"]["tracklets"][0]["id"] == "Uabc001"
    # identification failed (JPL down): nothing is called unidentified
    field["stats"]["transients"]["identification"] = {"error": "HTTPError"}
    for t in field["candidates"]["tracklets"]:
        t["identification"] = None
        t["known"] = None
    res = movers.unknown_movers(_Ctx(), 21.0, field)
    assert res["counts"]["unidentified"] == 0 and res["counts"]["unverified"] == 3


def test_repeats_in_other_visits_lower_the_tier():
    rows = [{"id": "a", "epoch": 0, "ra": 10.0, "dec": 0.0, "tier": "strong"},
            {"id": "b", "epoch": 1, "ra": 10.0 + 3 / 3600, "dec": 0.0, "tier": "candidate"},
            {"id": "c", "epoch": 1, "ra": 10.1, "dec": 0.0, "tier": "strong"},
            {"id": "d", "epoch": 0, "ra": 10.1 + 1 / 3600, "dec": 0.0, "tier": "weak"}]
    movers.mark_repeats(rows)
    assert [r["repeats_in_other_visits"] for r in rows] == [1, 1, 1, 1]
    assert [r["tier"] for r in rows] == ["candidate", "weak", "candidate", "weak"]
    lone = [{"id": "a", "epoch": 0, "ra": 10.0, "dec": 0.0, "tier": "strong"},
            {"id": "b", "epoch": 0, "ra": 10.0 + 1 / 3600, "dec": 0.0, "tier": "strong"}]  # same visit: no repeat
    movers.mark_repeats(lone)
    assert [r["tier"] for r in lone] == ["strong", "strong"]


def test_unknown_movers_refuse_comoving():
    class C(_Ctx):
        meta = {"grid": {"frame": "comoving"}}

    with pytest.raises(NotFound, match="fixed-position"):
        movers.unknown_movers(C(), 21.0, {"candidates": {}})
