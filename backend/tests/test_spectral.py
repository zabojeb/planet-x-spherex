"""Spectral analysis on synthetic photometry (offline: the template library is replaced by a small one)."""

import math

import numpy as np
import pytest

from spherex_core import spectral
from spherex_core.spectral import assemble, classify as cls, features, grid, templates, variability
from spherex_core.spectral.classify import planck_nu


def _records(fn, n_per_bin=4, epochs=(0, 1), noise=0.01, seed=0, relative=False, scale_by_epoch=None):
    """Photometry records sampling flux fn(wl) [mJy] across the grid."""
    rng = np.random.default_rng(seed)
    g = grid.GRID
    out = []
    for e in epochs:
        k = (scale_by_epoch or {}).get(e, 1.0)
        for i in range(g.size):
            for wl in np.linspace(g.lo[i], g.hi[i], n_per_bin + 2)[1:-1]:
                f = float(np.asarray(fn(wl)).ravel()[0]) * (k(wl) if callable(k) else k)
                out.append({"wl": float(wl), "band": str(g.band[i]), "epoch": e, "mjd": 60000.0 + 180 * e,
                            "flux_mjy": f * (1 + noise * rng.normal()), "err_mjy": abs(f) * noise + 1e-4,
                            "flagged": False, "relative": relative})
    return out


def star(wl, t=5800.0):
    return 10.0 * planck_nu(np.atleast_1d(wl), np.array([t]))[0] / planck_nu(np.array([2.2]), np.array([t]))[0][0]


def test_grid_has_17_bins_per_detector_and_covers_range():
    g = grid.GRID
    assert g.size == 102
    assert g.lo.min() < 0.75 and g.hi.max() > 5.0
    assert (g.index("D3", np.array([2.0]))[0]) >= 0 and g.index("D3", np.array([4.0]))[0] == -1


def test_binning_rejects_outlier_and_keeps_errors_honest():
    recs = _records(star, n_per_bin=6, epochs=(0,))
    recs[10]["flux_mjy"] *= 5  # an asteroid
    bins = assemble.bin_spectrum(recs)
    assert len(bins) == 102
    b = next(b for b in bins if b["n_rejected"])
    assert b["n_rejected"] == 1
    wl, f, e = assemble.arrays(bins)
    assert np.all(np.abs(f / star(wl) - 1) < 0.05)


def test_relative_and_flagged_points_stay_out_of_absolute_spectrum():
    recs = _records(star, epochs=(0,))
    for r in recs[::2]:
        r["relative"] = True
    for r in recs[1::4]:
        r["flagged"] = True
    absolute = assemble.bin_spectrum(recs)
    relative = assemble.bin_spectrum(recs, relative=True)
    assert all(not b["relative"] for b in absolute) and all(b["relative"] for b in relative)
    assert sum(b["n"] for b in absolute) == len([r for r in recs if not r["relative"] and not r["flagged"]])


def _with_band(fn, lo, hi, depth):
    return lambda wl: fn(wl) * (1 - depth if lo <= wl <= hi else 1.0)


def test_curated_feature_depth_and_no_false_detection_on_a_smooth_star():
    smooth = assemble.bin_spectrum(_records(star, epochs=(0,)))
    feats = {f["id"]: f for f in features.measure_features(smooth)}
    assert not any(f["detected"] for f in feats.values())  # power-law sidebands follow the SED
    absorbed = assemble.bin_spectrum(_records(_with_band(star, 2.92, 3.18, 0.4), epochs=(0,)))
    ice = {f["id"]: f for f in features.measure_features(absorbed)}["h2o_ice_3.05"]
    assert ice["detected"] and ice["depth"] == pytest.approx(0.4, abs=0.06)


def test_methane_index_and_auto_detection():
    recs = _records(_with_band(star, 3.15, 3.45, 0.8), epochs=(0,))
    bins = assemble.bin_spectrum(recs)
    idx = features.indices(bins)
    assert idx["ch4_3.3"]["value"] < 0.3
    det = features.detect_features(bins, features.continuum(bins))
    assert any(d["kind"] == "absorption" and "ch4_3.3" in d["identified_as"] for d in det)


def test_blackbody_fit_recovers_temperature_and_extinction():
    t, ak = 1500.0, 1.0
    fn = lambda wl: star(wl, t) * 10 ** (-0.4 * ak * cls.ext_ratio(np.atleast_1d(wl)))[0]  # noqa: E731
    wl, f, e = assemble.arrays(assemble.bin_spectrum(_records(fn, epochs=(0,), noise=0.005)))
    bb = cls.fit_blackbody(wl, f, e)
    assert bb["teff_k"] == pytest.approx(t, rel=0.1) and bb["a_k_mag"] == pytest.approx(ak, abs=0.3)


@pytest.fixture
def fake_library(monkeypatch):
    wl = np.linspace(0.6, 2.6, 400)
    temps = {"G2V": 5800, "M5V": 3000, "T5V": 1000}
    entries = []
    for sp, t in temps.items():
        flam = planck_nu(wl, np.array([t]))[0] / wl ** 2
        if sp.startswith("T"):
            flam = flam * np.where((wl > 1.6) & (wl < 1.75), 0.3, 1.0)
        entries.append((sp, templates.integrate(wl, flam)))
    lib = templates.TemplateLibrary([e[0] for e in entries], [e[0] for e in entries], ["star"] * 3,
                                    ["test"] * 3, np.array([templates.type_code(e[0]) for e in entries]),
                                    ["V"] * 3, np.stack([e[1] for e in entries]))
    monkeypatch.setattr(templates, "_LIBRARY", lib)
    return lib


def test_template_ranking_picks_the_right_type(fake_library):
    recs = _records(lambda wl: star(wl, 3000.0), epochs=(0,))
    out = cls.classify(assemble.bin_spectrum(recs))
    assert out["templates"]["best"]["sptype"] == "M5V"
    assert out["templates"]["best"]["probability"] > 0.9
    assert out["summary"]["family"] == "star"


def test_composite_cold_source_is_flagged(fake_library):
    """A K-like star at short wavelengths plus a methane-rich 4-5 um source (the WISE 0855 situation)."""
    cold = lambda wl: 8.0 * math.exp(-0.5 * ((wl - 4.6) / 0.25) ** 2) if wl > 3.6 else 0.0  # noqa: E731
    fn = lambda wl: star(wl, 5800.0) * 0.05 + cold(wl)  # noqa: E731
    out = cls.classify(assemble.bin_spectrum(_records(fn, epochs=(0,))))
    assert out["ir_excess"]["3.8_5um"]["ratio"] > 1.5
    assert out["summary"]["family"] == "composite"


def test_spectral_type_codes():
    assert templates.type_code("wG5V") == 45 and templates.type_code("T6") == 86
    assert templates.clean_type("rK3III") == "K3III"


def test_variability_ratio_flags_the_changing_band():
    fade = {1: lambda wl: 0.6 if wl > 4.4 else 1.0}
    recs = _records(star, epochs=(0, 1), scale_by_epoch=fade, relative=True)
    v = variability.variability(recs, reference=0)
    ep = v["epochs"][0]
    assert ep["per_band"]["D6"]["ratio"] == pytest.approx(0.6, abs=0.03) and "D6" in ep["significant_bands"]
    assert abs(ep["per_band"]["D2"]["ratio"] - 1) < 0.03 and "D2" not in ep["significant_bands"]


def test_synthetic_photometry_of_vega_like_flat_spectrum(monkeypatch):
    # top-hat filters so the test needs no download; F_nu = zero point -> magnitude 0
    from spherex_core.spectral import synphot

    for band, spec in synphot.FILTERS.items():
        lo, hi = {"J": (1.1, 1.4), "H": (1.5, 1.8), "Ks": (2.0, 2.3), "W1": (2.8, 3.8), "W2": (4.0, 5.2)}[band]
        lw = np.linspace(lo, hi, 50)
        monkeypatch.setitem(synphot._CURVES, band, (lw, np.ones_like(lw)))
    for band, spec in synphot.FILTERS.items():
        recs = _records(lambda wl, z=spec["zp_jy"]: z * 1e3, epochs=(0,), noise=0.0)
        out = synphot.synthetic(assemble.bin_spectrum(recs))[band]
        assert out["mag"] == pytest.approx(0.0, abs=0.02)


def test_reports_end_to_end(fake_library):
    recs = _records(star, epochs=(0, 1))
    s = spectral.spectrum_report(recs)
    assert len(s["bins"]) == 102 and len(s["epochs"]) == 2 and s["continuum"]["reliable"]
    a = spectral.analyze(recs)
    assert a["classification"]["available"] and a["variability"]["available"]


def test_routes(tmp_path, fake_library):
    from fastapi.testclient import TestClient

    from spherex_api.app import create_app
    from spherex_core import cache, services
    from spherex_core.pipeline import results
    from test_results import make_result

    services.configure(cache_root=tmp_path)
    try:
        meta, arrays = make_result()
        meta["photometry"] = _records(star, epochs=(0, 1))
        results.write_result(cache.layout().sequence("spec1"), meta, arrays)
        c = TestClient(create_app(web_dist=None))
        r = c.get("/api/sequences/spec1/spectrum")
        assert r.status_code == 200 and len(r.json()["bins"]) == 102 and "max-age" in r.headers["cache-control"]
        r = c.get("/api/sequences/spec1/classify")
        assert r.status_code == 200 and r.json()["templates"]["best"]["sptype"] == "G2V"
        assert c.get("/api/sequences/spec1/spectral-variability").json()["available"]
        r = c.post("/api/spectrum/analyze", json={"photometry": meta["photometry"]})
        assert r.status_code == 200 and r.json()["spectrum"]["bins"]
        assert c.post("/api/spectrum/analyze", json={"photometry": []}).status_code == 422
        assert c.get("/api/sequences/nope/spectrum").status_code == 404
    finally:
        services.configure(None)
