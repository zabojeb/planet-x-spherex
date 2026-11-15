"""Comet discovery (ADR-0063), offline: orbits and ranging, the coma test, the spectral signature, slow linking and
shift-and-stack with parallax, the comet-likeness, the watch's helpers, the search job on synthetic sequences, the
HTTP routes and the MCP tools. Network tests (JPL SBDB) are marked."""

import math
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient

from spherex_api.app import create_app
from spherex_core import cache, services
from spherex_core.analysis.common import load_context
from spherex_core.bands import BANDS
from spherex_core.comets import coma, geometry, hunt, orbits, score, signature, slow, watch
from spherex_core.field.jobs import KINDS, make_id, split_id
from spherex_core.pipeline import results
from spherex_core.solar import sun

PIX = 6.15


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    """No CALSPEC download and no Horizons: the blackbody Sun and a SPHEREx at the geocentre."""
    monkeypatch.setattr(sun, "_SUN", sun._blackbody())
    from spherex_core.field import observer

    monkeypatch.setattr(observer, "states", lambda mjds: {observer._key(m): [0.0] * 6 for m in np.atleast_1d(mjds)})


# ---------------------------------------------------------------- orbits
def test_propagate_conics_and_elements():
    for e in (0.0, 0.5, 0.97, 1.0, 1.5, 6.1):
        r, v = orbits.propagate(1.35, e, 30.0, 100.0, 40.0, 2460000.0, 2460000.0)
        assert np.linalg.norm(r) == pytest.approx(1.35)
        for dt in (-300.0, 50.0, 400.0):
            r, v = orbits.propagate(1.35, e, 30.0, 100.0, 40.0, 2460000.0, 2460000.0 + dt)
            el = orbits.elements(r, v)
            assert float(el["e"]) == pytest.approx(e, abs=1e-6)
            assert float(el["q"]) == pytest.approx(1.35, rel=1e-6)
            assert float(el["i"]) == pytest.approx(30.0, abs=1e-6)
    # a circular orbit keeps its radius and moves at the circular speed
    r, v = orbits.propagate(2.7, 0.0, 0.0, 0.0, 0.0, 2460000.0, 2460123.0)
    assert np.linalg.norm(r) == pytest.approx(2.7) and np.linalg.norm(v) == pytest.approx(math.sqrt(orbits.GM / 2.7))


def _apparent(el, mjd, dt=0.05):
    f = lambda m: orbits.ecl_to_eq(orbits.propagate(*el, m + 2400000.5)[0])  # noqa: E731
    p0, p1 = f(mjd), f(mjd + dt)
    e0, _ = geometry.earth_helio(mjd)
    e1, _ = geometry.earth_helio(mjd + dt)
    d0, d1 = p0 - e0[0], p1 - e1[0]
    ra, dec = (float(x) for x in geometry.radec(d0))
    east, north = geometry.tangent_basis(ra, dec)
    u = d1 / np.linalg.norm(d1)
    ve, vn = u @ east * 206264.8 / (dt * 24), u @ north * 206264.8 / (dt * 24)
    o, v = 0.5 * (e0[0] + e1[0]), (e1[0] - e0[0]) / dt
    return ra, dec, math.hypot(ve, vn), geometry.pa_of(ve, vn), o, v, float(np.linalg.norm(d0))


def test_orbit_classes_rank_main_belt_and_comets():
    mba = _apparent((2.43, 0.1, 5.0, 80.0, 30.0, 2460500.5), 60900.0)
    r = orbits.orbit_classes(*mba[:6])
    assert r["most_likely"] == "mba" and r["comet_like"] < 0.02
    # a high-inclination long-period comet at high latitude: no asteroid class explains it well
    lpc = _apparent((2.5, 0.9995, 80.0, 40.0, 120.0, 2460950.5), 60900.0)
    r = orbits.orbit_classes(*lpc[:6])
    assert r["comet_like"] > 10 * orbits.orbit_classes(*mba[:6])["comet_like"]
    # with the distance known, the interstellar comet is hyperbolic and the asteroid stays in the main belt
    i3 = _apparent((1.3564, 6.1395, 175.113, 322.15, 128.01, 2460977.98), 60900.0)
    r = orbits.orbit_classes(*i3[:6], distance_au=(0.97 * i3[6], 1.03 * i3[6]))
    assert r["most_likely"] == "hyperbolic" and r["comet_like"] > 0.99 and r["constrained"]
    r = orbits.orbit_classes(*mba[:6], distance_au=(0.97 * mba[6], 1.03 * mba[6]))
    assert r["most_likely"] == "mba"


def test_antisolar_direction_and_elongation():
    mjd = 60900.0
    sra, sdec = geometry.sun_radec(mjd)
    # 90 deg east of the Sun along the equator-ish: the anti-solar direction points away from the Sun
    ra, dec = (float(sra[0]) + 90.0) % 360.0, 0.0
    pa_sun = geometry.pa_toward(ra, dec, float(sra[0]), float(sdec[0]))
    assert geometry.angle_diff(geometry.antisolar_pa(ra, dec, mjd), pa_sun) == pytest.approx(180.0, abs=1e-6)
    assert 80.0 < geometry.elongation(ra, dec, mjd) < 100.0
    assert geometry.angle_diff(10.0, 350.0) == pytest.approx(20.0)


def test_parallax_basis_wobbles_with_the_spacecraft(monkeypatch):
    from spherex_core.field import observer

    period = 98.0 / 1440.0

    def states(mjds):
        return {observer._key(m): [7000.0 * math.cos(2 * math.pi * m / period), 7000.0 * math.sin(2 * math.pi * m / period),
                                   0.0, 0.0, 0.0, 0.0] for m in np.atleast_1d(mjds)}

    monkeypatch.setattr(observer, "states", states)
    t = 60900.0 + np.linspace(0, 2 * period, 40)
    b, who = geometry.parallax_basis(t, 0.0, 0.0, float(t[0]))
    assert who == "SPHEREx"
    # Earth's own motion over 3 h is smooth; the residual about a quadratic is the 9.7"-at-1-au wobble
    res = [b[:, k] - np.polyval(np.polyfit(t - t[0], b[:, k], 2), t - t[0]) for k in (0, 1)]
    amp = max(np.ptp(r) for r in res) / 2
    assert 7.0 < amp < 11.0


# ---------------------------------------------------------------- coma test
N = 61


def _psf(x, y, fwhm=1.7, n=N):
    s = fwhm / 2.3548
    yy, xx = np.mgrid[0:n, 0:n]
    sub = (np.arange(4) + 0.5) / 4 - 0.5
    g = np.zeros((n, n))
    for dy in sub:
        for dx in sub:
            g += np.exp(-((xx + dx - x) ** 2 + (yy + dy - y) ** 2) / (2 * s * s))
    return g / g.sum()


def _comet_image(x, y, n=N, k=1.0, tail_pa=None):
    """1/rho coma (plus an optional tail) convolved with the PSF, unit total inside r < 6 px."""
    from scipy.signal import fftconvolve

    yy, xx = np.mgrid[0:n, 0:n].astype(float)
    rr = np.hypot(xx - x, yy - y)
    img = k / np.maximum(rr, 0.35)
    img[rr > 12] = 0
    if tail_pa is not None:
        ang = np.radians(tail_pa)
        ux, uy = -math.sin(ang), math.cos(ang)  # grid x grows to the west
        along = (xx - x) * ux + (yy - y) * uy
        perp = np.abs((xx - x) * uy - (yy - y) * ux)
        img += 1.5 * np.where((along > 0) & (along < 8) & (perp < 1.2), 1.0 / np.maximum(along, 1.0), 0.0)
    img = fftconvolve(img, _psf((n - 1) / 2, (n - 1) / 2), mode="same")
    return img / img[rr < 6].sum()


def _star_field(rng, n=N, stars=14):
    """Up to `stars` positions, isolated by 11 px (a stamp's half-width plus the PSF) and away from the centre."""
    pos = []
    for _ in range(5000):
        if len(pos) >= stars:
            break
        x, y = rng.uniform(9, n - 10, 2)
        if math.hypot(x - n / 2, y - n / 2) > 12 and all(math.hypot(x - a, y - b) > 11 for a, b in pos):
            pos.append((x, y))
    return pos


def _exposures(rng, obj, kind="point", k=20, noise=0.002, tail_pa=None):
    stars = _star_field(rng)
    imgs, plain = [], []
    for _ in range(k):
        img = np.zeros((N, N))
        for x, y in stars:
            img += 0.5 * _psf(x, y)
        o = _psf(*obj) if kind == "point" else _comet_image(*obj, tail_pa=tail_pa)
        plain.append(img + 0.3 * o + rng.normal(0, noise, (N, N)))
        imgs.append(0.3 * o + rng.normal(0, noise, (N, N)))  # static sky subtracted
    return imgs, plain


@pytest.mark.parametrize("kind", ["point", "coma"])
def test_coma_test_tells_comets_from_stars(kind):
    rng = np.random.default_rng(1)
    obj = (30.3, 29.8)
    imgs, plain = _exposures(rng, obj, kind)
    psfs = coma.star_psfs({"D1": plain}, {"D1": [0.002] * len(plain)}, exclude=[obj])
    assert psfs["D1"]["source"].endswith("field stars")
    st, w = coma.object_stamps(imgs, [obj] * len(imgs), [0.002] * len(imgs))
    res = coma.coma_test(st, w, ["D1"] * len(imgs), psfs, pixscale=PIX, antisolar_pa=60.0, motion_pa=240.0,
                         jitter_px=0.05, interp=coma.interp_var([obj] * len(imgs)))
    if kind == "point":
        assert res["verdict"] == "point-like" and res["extent_sigma"] < 2 and res["coma_score"] < 0.2
        assert score.morphology_llr(res) == -2.0
    else:
        assert res["verdict"] == "extended" and res["extent_sigma"] > 5 and res["coma_score"] > 0.9
        assert res["size"]["intrinsic_fwhm_arcsec"] > 3
        assert score.morphology_llr(res) > 3


def test_coma_test_finds_an_aligned_tail():
    rng = np.random.default_rng(2)
    obj = (30.0, 30.0)
    imgs, plain = _exposures(rng, obj, "coma", tail_pa=60.0)
    psfs = coma.star_psfs({"D1": plain}, {"D1": [0.002] * len(plain)}, exclude=[obj])
    st, w = coma.object_stamps(imgs, [obj] * len(imgs), [0.002] * len(imgs))
    res = coma.coma_test(st, w, ["D1"] * len(imgs), psfs, pixscale=PIX, antisolar_pa=65.0, motion_pa=200.0,
                         jitter_px=0.05, interp=coma.interp_var([obj] * len(imgs)))
    t = res["tail"]
    assert t["detected"] and geometry.angle_diff(t["pa_deg"], 60.0) < 15 and t["aligned"]
    assert t["offset_from_antisolar_deg"] < 20


def test_fill_holes_and_masked_core():
    st = np.ones((15, 15))
    st[7, 11] = np.nan
    assert np.isfinite(coma.fill_holes(st)).all()
    st[7, 7] = np.nan
    assert coma.fill_holes(st) is None
    stamps = np.full((5, 15, 15), np.nan)
    res = coma.coma_test(stamps, np.zeros(5), ["D1"] * 5, {}, pixscale=PIX)
    assert res["verdict"].startswith("core masked")


# ---------------------------------------------------------------- spectral signature
def _spectrum(co2=0.0, n=240, seed=0):
    rng = np.random.default_rng(seed)
    wl = np.sort(rng.uniform(0.75, 5.0, n))
    dwl = wl / np.where(wl > 4.4, 130.0, 40.0)
    f = 0.05 * sun.sun_fnu(wl, dwl) / sun.sun_fnu(1.0, 0.025)  # grey reflectance, 0.05 mJy at 1 um
    if co2:
        from spherex_core.solar.comet import _line_response

        f = f + co2 * _line_response(wl, dwl, 4.26, 0.05)
    err = np.full(n, 0.002)
    return [{"wl": a, "dwl": b, "flux_mjy": c + rng.normal(0, 0.002), "err_mjy": e, "band": "D5"}
            for a, b, c, e in zip(wl, dwl, f, err)]


def test_signature_detects_co2_and_stays_quiet_without_it():
    s = signature.signature(_spectrum(co2=4e10))
    co2 = next(b for b in s["bands"] if b["id"] == "co2")
    assert co2["detected"] and co2["snr"] > 5 and s["verdict"] == "gas emission" and s["llr"] >= 2.5
    quiet = signature.signature(_spectrum(co2=0.0))
    assert quiet["llr"] == 0.0 and quiet["verdict"] == "no band detected"
    assert 0.8 < quiet["long_excess"]["ratio"] < 1.2  # grey reflectance: no thermal tail
    assert signature.signature([])["verdict"] == "no data"


def test_comet_likeness_combines_the_terms():
    mba_like = {"comet_like": 0.001, "most_likely": "mba"}
    point = {"coma_score": 0.02, "snr": 50, "verdict": "point-like", "extent_sigma": 0.3}
    ext = {"coma_score": 0.999, "snr": 50, "verdict": "extended", "extent_sigma": 9.0}
    gas = {"llr": 2.5, "verdict": "gas emission", "gas_sigma": 6.0}
    a = score.comet_likeness(mba_like, point, None)["score"]
    b = score.comet_likeness(mba_like, ext, None)["score"]
    c = score.comet_likeness(mba_like, ext, gas)["score"]
    d = score.comet_likeness({"comet_like": 0.6, "most_likely": "lpc"}, ext, gas)["score"]
    assert a < 1 < b < c < d and c > 80 and d > 99
    faint = {"coma_score": 0.99, "snr": 4, "verdict": "too faint"}
    assert score.comet_likeness(mba_like, faint, None)["terms"]["morphology"] == 0.0


# ---------------------------------------------------------------- slow movers
def _groups(rng, n=48, times=None, mover=None, static=None, snr=8.0, noise=1.0, basis=None):
    times = times if times is not None else 60900.0 + np.array([0.0, 1.3, 2.4, 3.9, 5.1, 6.6, 7.8, 9.2])
    gs = []
    for k, t in enumerate(times):
        img = rng.normal(0, noise, (n, n))
        for src, s in ((mover, snr), (static, snr)):
            if src is None:
                continue
            x0, y0, vx, vy, w = src
            b = basis([t])[0] if basis else (0.0, 0.0)
            x, y = x0 + vx * (t - times.mean()) + w * b[0], y0 + vy * (t - times.mean()) + w * b[1]
            img += s * 2 * math.pi * (1.7 / 2.3548) ** 2 * _psf(x, y, n=n)
        gs.append(slow.Group(0, k, float(t), [k], img.astype(np.float32)))
    return gs, times


def _basis(times):
    """Earth-like curvature plus a 98-min wobble, in px for an object at 1 au."""
    t = np.atleast_1d(np.asarray(times, float)) - 60900.0
    return np.column_stack([0.02 * t ** 2 + 1.5 * np.cos(2 * np.pi * t / 0.068), -0.01 * t ** 2 + 1.5 * np.sin(2 * np.pi * t / 0.068)])


def test_slow_link_recovers_motion_and_distance_and_rejects_static():
    rng = np.random.default_rng(4)
    mover = (20.0, 24.0, 1.6, -0.9, 0.5)
    gs, times = _groups(rng, mover=mover, static=(35.0, 12.0, 0.0, 0.0, 0.0), basis=_basis)
    dets = slow.detections(gs)
    links = slow.link(dets, gs, _basis, PIX, window_days=10.0)
    assert len(links) == 1
    L = links[0]
    assert L["vx"] == pytest.approx(1.6, abs=0.15) and L["vy"] == pytest.approx(-0.9, abs=0.15)
    assert L["w"] == pytest.approx(0.5, abs=0.15) and len(L["groups"]) >= 6 and L["rms"] < 0.6
    assert L["rate_arcsec_per_hr"] == pytest.approx(math.hypot(1.6, 0.9) * PIX / 24, rel=0.1)


def test_stack_search_finds_a_faint_slow_mover():
    rng = np.random.default_rng(5)
    gs, _ = _groups(rng, mover=(22.0, 25.0, 1.2, 0.7, 0.0), snr=3.2)
    assert not slow.link(slow.detections(gs), gs, lambda t: np.zeros((len(np.atleast_1d(t)), 2)), PIX,
                         window_days=10.0)  # too faint per group to link
    hits, st = slow.stack_search(gs, lambda t: np.zeros((len(np.atleast_1d(t)), 2)), PIX, rate_max=1.0,
                                 window_days=10.0, w_grid=(0.0,))
    assert st["trials"] > 100 and hits
    h = hits[0]
    assert abs(h["x0"] - 22.0) <= 1.5 and abs(h["y0"] - 25.0) <= 1.5 and h["snr"] > 6.5
    assert h["vx"] == pytest.approx(1.2, abs=0.3) and h["vy"] == pytest.approx(0.7, abs=0.3)


class _FakeImages:
    """The slice of FieldImages that pass_tracklets reads."""

    def __init__(self, maps, times, bands):
        self.meta = {"exposures": [{"index": i, "epoch": 0, "mjd": t, "band": b} for i, (t, b) in enumerate(zip(times, bands))]}
        self.maps = maps

    def exposures(self, epoch=None):
        return list(range(len(self.maps)))

    def snr(self, i):
        return self.maps[i]


def test_pass_tracklets_link_a_fast_comet_seen_for_minutes():
    rng = np.random.default_rng(9)
    n = 48
    times = 60900.0 + np.repeat(np.arange(4), 2) * 0.0016  # 4 pairs of simultaneous partner exposures, 7 min
    bands = ["D2", "D5"] * 4
    rate = 100.0  # "/h
    v = rate * 24 / PIX  # px/day, moving west (+x) and a little north
    maps = []
    for t in times:
        img = rng.normal(0, 1, (n, n))
        x, y = 20.0 + v * 0.9 * (t - times[0]), 24.0 + v * 0.44 * (t - times[0])
        img += 12 * 2 * math.pi * (1.7 / 2.3548) ** 2 * _psf(x, y, n=n)
        maps.append(img)
    tr = slow.pass_tracklets(_FakeImages(maps, times, bands), 0, PIX)
    assert len(tr) == 1
    t = tr[0]
    assert t["rate_arcsec_per_hr"] == pytest.approx(rate, rel=0.2) and len(t["detections"]) >= 6
    assert t["span_minutes"] == pytest.approx(6.9, abs=0.2)


def test_stack_search_respects_the_trial_budget():
    rng = np.random.default_rng(6)
    gs, _ = _groups(rng)
    _, st = slow.stack_search(gs, lambda t: np.zeros((len(np.atleast_1d(t)), 2)), PIX, rate_max=2.5,
                              window_days=10.0, budget=500)
    assert st["trials"] <= 500 * 1.05 and st["coarsened"]


# ---------------------------------------------------------------- watch helpers
def test_designations_and_outbursts():
    assert watch.designation({"pdes": "29P", "prefix": "P"}) == "29P"
    assert watch.designation({"pdes": "240P-B", "prefix": "P"}) == "240P-B"
    assert watch.designation({"pdes": "2025 A6", "prefix": "C"}) == "C/2025 A6"
    assert watch.designation({"pdes": "3I", "prefix": "I"}) == "3I"
    rows = [{"epoch": k, "r_au": r, "afrho_cm": 1000.0 * (r / 3.0) ** -2, "afrho_err_cm": 50.0}
            for k, r in enumerate((3.0, 3.5, 4.0, 4.5))]
    assert watch.outbursts(rows) == []
    rows[2]["afrho_cm"] *= 3.0
    ob = watch.outbursts(rows)
    assert [o["epoch"] for o in ob] == [2] and ob[0]["ratio"] > 2.5 and ob[0]["delta_mag"] < -1


def test_comets_now_from_cached_elements(tmp_path, monkeypatch):
    services.configure(cache_root=tmp_path)
    try:
        rows = [{"name": "C/2025 N1 (ATLAS)", "designation": "3I", "e": 6.1395, "q": 1.3564, "tp": 2460977.98,
                 "i": 175.113, "om": 322.15, "w": 128.01, "M1": 12.0, "K1": 10.0},
                {"name": "99P/Test", "designation": "99P", "e": 0.5, "q": 2.0, "tp": 2461300.0, "i": 10.0, "om": 0.0,
                 "w": 0.0, "M1": 30.0, "K1": 10.0}]
        monkeypatch.setattr(watch, "sbdb_comets", lambda refresh=False: rows)
        now = watch.comets_now(60900.0)
        assert now[0]["designation"] == "3I" and now[0]["pinned"]
        p = now[0]
        assert 1.0 < p["delta_au"] < 4.0 and p["mag"] is not None and -90 < p["dec"] < 90
        chosen = watch.choose(now)
        assert [c["designation"] for c in chosen][:1] == ["3I"] and "99P" not in [c["designation"] for c in chosen]
        state = watch.watch()
        assert state["watched"][0]["designation"] == "3I" and state["refresh"]["status"] == "idle"
    finally:
        services.configure(None)


def test_activity_summary_from_a_comet_result():
    res = {"epochs": [{"epoch": 0, "mjd_mean": 60900.0, "r_au": 3.0, "delta_au": 2.5, "phase_deg": 10.0,
                       "afrho": {"10000": {"D1": {"afrho_cm": 900.0, "err_cm": 30.0}}},
                       "bands": {"10000": [{"id": "co2", "snr": 12.0, "value_w_m2": 1e-17, "q_proxy": 1e26}]}}],
           "activity_trend": None}
    a = watch.activity(res)
    assert a["visits"][0]["afrho_cm"] == 900.0 and a["visits"][0]["co2_snr"] == 12.0 and a["outbursts"] == []


@pytest.mark.network
def test_sbdb_comet_list(tmp_path):
    services.configure(cache_root=tmp_path)
    try:
        rows = watch.sbdb_comets(refresh=True)
        assert len(rows) > 20 and all(r["q"] < 6 for r in rows)
        assert any(r["designation"].endswith("P") for r in rows)
    finally:
        services.configure(None)


# ---------------------------------------------------------------- the search job on synthetic sequences
def _write_comoving(tmp_path, kind="coma", seed=7):
    """A co-moving sequence: the object on the centre of every exposure, field stars elsewhere, two visits."""
    from spherex_core.pipeline.params import SequenceParams

    services.configure(cache_root=tmp_path)
    rng = np.random.default_rng(seed)
    exps, clean, plain = [], [], []
    c = (N - 1) / 2
    for e in range(2):
        for b, band in enumerate(BANDS):
            stars = _star_field(rng)
            for k, wl in enumerate(np.linspace(0.8 + 0.7 * b, 1.4 + 0.7 * b, 6)):
                i = len(exps)
                t = 60900.0 + 150 * e + 0.02 * i
                o = _comet_image(c, c, tail_pa=120.0) if kind == "coma" else _psf(c, c)
                obj = 0.4 * o
                star_img = sum(0.5 * _psf(x, y) for x, y in stars)
                noise = rng.normal(0, 0.002, (N, N))
                clean.append(obj + noise)
                plain.append(obj + star_img + noise)
                exps.append({"index": i, "epoch": e, "band": band, "mjd": t, "wl": float(wl), "dwl": float(wl) / 40,
                             "rms": 0.002, "psf_fwhm": 1.7 * PIX, "ss_n": 4, "ss_method": "median",
                             "file": f"f{i}.fits", "obs_id": f"o{i}",
                             "center": [10.0 + 0.003 * i, 5.0 + 0.001 * i]})
    clean, plain = np.stack(clean).astype(np.float32), np.stack(plain).astype(np.float32)
    cube = np.full((2, 6, N, N), np.nan, np.float32)
    for e in range(2):
        for b, band in enumerate(BANDS):
            sel = [x["index"] for x in exps if x["epoch"] == e and x["band"] == band]
            cube[e, b] = clean[sel].mean(axis=0)
    epochs = [{"index": e, "date_min": f"2025-0{8 + e}-01T00:00:00", "date_max": f"2025-0{8 + e}-02T00:00:00",
               "mjd_mean": 60900.0 + 150 * e, "n": 36, "bands": {}} for e in range(2)]
    name = "C/2099 T1" if kind == "coma" else "(99999) Testrock"
    p = SequenceParams(ra=10.0, dec=5.0, object_name=name, object_type="Comet" if kind == "coma" else "Asteroid",
                       static_sky=True)
    shape = [2, 6, N, N]
    meta = {"id": p.key(), "params": {"ra": 10.0, "dec": 5.0, "object_name": name, "static_sky": True,
                                      "object_type": "Comet" if kind == "coma" else "Asteroid"},
            "grid": {"ra": 10.0, "dec": 5.0, "n": N, "pixscale_arcsec": PIX, "fov_arcmin": N * PIX / 60,
                     "frame": "comoving"},
            "moving": {"object": name}, "epochs": epochs, "exposures": exps, "target": {"name": name, "track": []},
            "static_sky": {"version": 1, "method": "test", "exposures": len(exps), "with_template": len(exps)},
            "arrays": {"coadds": {"file": "coadds.f32", "dtype": "float32", "shape": shape},
                       "clean": {"file": "clean.f32", "dtype": "float32", "shape": shape},
                       "exposures": {"file": "exposures.f16", "dtype": "float16", "shape": list(plain.shape)},
                       "clean_exposures": {"file": "clean_exposures.f16", "dtype": "float16", "shape": list(clean.shape)}}}
    results.write_result(cache.layout().sequence(p.key()), meta, {"coadds": cube, "clean": cube, "exposures": plain,
                                                                  "clean_exposures": clean})
    return p.key()


@pytest.mark.parametrize("kind", ["coma", "point"])
def test_object_check_on_a_comoving_sequence(tmp_path, monkeypatch, kind):
    from spherex_core.solar import geometry as sgeo

    seq = _write_comoving(tmp_path, kind)
    monkeypatch.setattr(sgeo, "exposure_geometry", lambda meta, use_horizons=True: [
        {"r_au": 3.0, "delta_au": 2.5 + 0.001 * k, "phase_deg": 15.0, "source": "test"} for k in range(len(meta["exposures"]))])
    try:
        res = hunt.comet_search(load_context(seq), hunt.HuntOptions())
    finally:
        services.configure(None)
    assert res["mode"] == "object" and len(res["candidates"]["comets"]) == 2
    v = res["candidates"]["comets"][0]
    assert v["coma"]["psf_source"]["D1"].endswith("field stars")
    if kind == "coma":
        assert v["coma"]["verdict"] == "extended" and v["comet_likeness"]["score"] > 60
        assert len(v["strip"]) == hunt.STRIP_FRAMES and len(v["coma"]["stamp"]) == 2 * coma.HALF + 1
    else:
        assert v["coma"]["verdict"] == "point-like" and v["comet_likeness"]["terms"]["morphology"] == -2.0
    assert v["orbit"]["constrained"] and v["spectrum"]["n_points"] >= 30


def _write_field(tmp_path, seed=8):
    """A fixed field with three visits: visit 0 has eight groups over 9 days with a slow comet crossing it."""
    services.configure(cache_root=tmp_path)
    rng = np.random.default_rng(seed)
    n = N
    stars = _star_field(rng, stars=20)
    static = sum(0.5 * _psf(x, y) for x, y in stars)
    exps, planes = [], []
    times = 60900.0 + np.array([0.0, 1.3, 2.4, 3.9, 5.1, 6.6, 7.8, 9.2])
    tmid = times.mean()
    vx, vy = 1.4, -0.8  # px/day (~0.4"/h)
    coadds = np.zeros((3, 6, n, n), np.float32)
    for e in range(3):
        for b, band in enumerate(("D1", "D2", "D4", "D5")):
            bi = BANDS.index(band)
            coadds[e, bi] = static + rng.normal(0, 0.0007, (n, n))
            if e:
                continue
            for g, t in enumerate(times):
                for k in range(2):
                    tt = t + 0.01 * k + 0.002 * b
                    x, y = 30.0 + vx * (tt - tmid), 28.0 + vy * (tt - tmid)
                    img = static + 0.25 * _comet_image(x, y) + rng.normal(0, 0.002, (n, n))
                    i = len(exps)
                    exps.append({"index": i, "epoch": 0, "band": band, "mjd": float(tt), "wl": 1.0 + 0.7 * bi + 0.05 * g,
                                 "dwl": 0.03, "rms": 0.002, "psf_fwhm": 1.7 * PIX, "obs_id": f"o{g}{k}",
                                 "file": f"f{i}.fits"})
                    planes.append(img)
    # exposures stored in (epoch, band, time) order
    order = sorted(range(len(exps)), key=lambda i: (exps[i]["epoch"], BANDS.index(exps[i]["band"]), exps[i]["mjd"]))
    exps = [dict(exps[i], index=k) for k, i in enumerate(order)]
    planes = [planes[i] for i in order]
    epochs = [{"index": e, "mjd_mean": 60900.0 + 180 * e, "mjd_min": 60900.0 + 180 * e, "mjd_max": 60910.0 + 180 * e,
               "date_min": f"202{5 + e}-01-01", "date_max": f"202{5 + e}-01-10", "n": 64, "bands": {}} for e in range(3)]
    cube = [3, 6, n, n]
    for e in range(3):
        for bi in range(6):
            if not coadds[e, bi].any():
                coadds[e, bi] = np.nan
    meta = {"id": "cfld1", "target": {"name": "synthetic"}, "params": {"ra": 150.0, "dec": 40.0, "fov_arcmin": n * PIX / 60},
            "grid": {"ra": 150.0, "dec": 40.0, "n": n, "pixscale_arcsec": PIX}, "epochs": epochs, "exposures": exps,
            "photometry": [],
            "arrays": {"coadds": {"file": "coadds.f32", "dtype": "float32", "shape": cube},
                       "coverage": {"file": "coverage.u16", "dtype": "uint16", "shape": cube},
                       "flags": {"file": "flags.u32", "dtype": "uint32", "shape": cube},
                       "exposures": {"file": "exposures.f16", "dtype": "float16", "shape": [len(planes), n, n]}}}
    results.write_result(cache.layout().sequence("cfld1"), meta, {
        "coadds": coadds, "coverage": np.full(cube, 5, np.uint16), "flags": np.zeros(cube, np.uint32),
        "exposures": np.array(planes, np.float32)})
    return "cfld1", (30.0, 28.0, vx, vy, tmid)


def test_hunt_finds_a_slow_comet_in_a_fixed_field(tmp_path):
    seq, truth = _write_field(tmp_path)
    try:
        res = hunt.comet_search(load_context(seq), hunt.HuntOptions(identify=False, window_days=10.0))
    finally:
        services.configure(None)
    assert res["mode"] == "field" and res["observer"] == "SPHEREx"
    cands = res["candidates"]["comets"]
    assert cands, res["stats"]
    best = cands[0]
    x0, y0, vx, vy, tmid = truth
    px, py = x0 + vx * (best["mjd"] - tmid), y0 + vy * (best["mjd"] - tmid)
    assert math.hypot(best["x"] - px, best["y"] - py) < 2.0
    assert best["kind"] in ("slow", "stack") and best["rate_arcsec_per_hr"] == pytest.approx(
        math.hypot(vx, vy) * PIX / 24, rel=0.25)
    assert best["coma"]["verdict"] in ("extended", "possibly extended") and best["status"] == "unverified"
    assert best["comet_likeness"]["score"] >= 50 and best["id"] == "H001"
    assert res["stats"]["seconds"]["total"] < 120


# ---------------------------------------------------------------- routes, jobs and MCP
def test_job_kind_and_options():
    assert KINDS["h"] == "comet search"
    jid = make_id("abc123", "h", hunt.HuntOptions().to_dict())
    assert split_id(jid)[1][0] == "h"
    with pytest.raises(ValueError):
        hunt.HuntOptions.from_dict({"window_days": 0.1})
    with pytest.raises(ValueError):
        hunt.load_field_result("abc123", "other1-fdeadbeef00")


def test_comet_routes(tmp_path, monkeypatch):
    from spherex_core.solar import geometry as sgeo

    seq = _write_comoving(tmp_path, "coma")
    monkeypatch.setattr(sgeo, "exposure_geometry", lambda meta, use_horizons=True: [
        {"r_au": 3.0, "delta_au": 2.5, "phase_deg": 15.0, "source": "test"} for _ in meta["exposures"]])
    monkeypatch.setattr(watch, "sbdb_comets", lambda refresh=False: [])
    try:
        with TestClient(create_app(web_dist=None)) as c:
            assert c.post(f"/api/sequences/{seq}/comet-search", json={"window_days": 0.5}).status_code == 422
            assert c.post("/api/sequences/nope/comet-search", json={}).status_code == 404
            r = c.post(f"/api/sequences/{seq}/comet-search", json={}, headers={"X-Client-Id": "t"})
            assert r.status_code == 200
            jid = r.json()["id"]
            assert "-h" in jid
            for _ in range(200):
                g = c.get(f"/api/field/{jid}", headers={"X-Client-Id": "t"})
                if g.status_code != 202:
                    break
                time.sleep(0.05)
            assert g.status_code == 200, g.text
            body = g.json()
            assert body["kind"] == "comet_search" and body["mode"] == "object"
            assert body["candidates"]["comets"][0]["coma"]["verdict"] == "extended"
            w = c.get("/api/comets/watch").json()
            assert {"watched", "now", "refresh", "pinned"} <= set(w)
    finally:
        services.configure(None)


def test_mcp_lists_the_comet_tools():
    import asyncio

    from spherex_mcp import build_server

    names = {t.name for t in asyncio.run(build_server().list_tools())}
    assert {"comet_search", "comet_candidates", "comet_watch"} <= names


def test_mcp_summary_of_a_comet_search():
    from spherex_mcp import tools

    res = {"kind": "comet_search", "mode": "field", "sequence": "abc", "counts": {"candidates": 1},
           "observer": "SPHEREx", "stats": {"seconds": {"total": 1.0}},
           "candidates": {"comets": [{"id": "H001", "kind": "slow", "ra": 1.0, "dec": 2.0, "mjd": 60900.0,
                                      "rate_arcsec_per_hr": 0.4, "pa_deg": 120.0, "status": "unidentified",
                                      "comet_likeness": {"score": 88.0, "terms": {}, "why": ["x"]},
                                      "coma": {"verdict": "extended", "extent_sigma": 7.0, "tail": {}},
                                      "spectrum": {"verdict": "no band detected", "bands": []},
                                      "orbit": {"most_likely": "tno", "comet_like": 0.1}, "strip": [1, 2]}],
                          "tracklets": []}, "caveats": ["c"]}
    s = tools._summarise_comet_search(res, 5)
    assert s["candidates"][0]["id"] == "H001" and "strip" not in s["candidates"][0]
