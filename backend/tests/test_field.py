"""Field analysis on a synthetic sequence: detection, movers, red excess, tracklets, Track & Stack, jobs, routes."""

import asyncio

import numpy as np
import pytest
from fastapi.testclient import TestClient

from spherex_api.app import create_app
from spherex_core import cache, services
from spherex_core.analysis.common import load_context
from spherex_core.bands import BANDS
from spherex_core.field import ades, known, movers, realbogus, sources, trackstack, transients
from spherex_core.field.analyze import FieldOptions, analyze_field
from spherex_core.field.colours import red_excess_map
from spherex_core.field.common import load_field
from spherex_core.field.jobs import FieldJobs, make_id
from spherex_core.pipeline import results

N = 41
YEARS = [2025.6, 2026.1, 2026.6]
MOVER_PX_PER_EPOCH = 1.0  # ~12.3"/yr
STATIC = [(10.0, 10.0, 60.0), (30.0, 12.0, 40.0), (12.0, 30.0, 50.0), (28.0, 31.0, 45.0)]
RED = (8.0, 22.0)  # bright only in D5/D6
AST = {"x0": 6.0, "y0": 34.0, "vx": 20.0, "vy": -5.0}  # px/day, epoch 0 only


def mjd(year):
    return 51544.5 + (year - 2000.0) * 365.25


def gauss(x0, y0, amp, sigma=0.9):
    yy, xx = np.mgrid[0:N, 0:N]
    return amp * np.exp(-((xx - x0) ** 2 + (yy - y0) ** 2) / (2 * sigma ** 2))


def make_field():
    rng = np.random.default_rng(3)
    coadds = np.zeros((3, 6, N, N), np.float32)
    exposures, planes = [], []
    epochs = []
    for e, yr in enumerate(YEARS):
        bands = {}
        for b, band in enumerate(BANDS):
            img = rng.normal(0, 0.05, (N, N))
            for x, y, a in STATIC:
                img += gauss(x, y, a / 10)
            img += gauss(20.0 + MOVER_PX_PER_EPOCH * (e - 1), 20.0, 4.0)  # mover moves +x (west)
            if band in ("D5", "D6"):
                img += gauss(*RED, 2.0)
            coadds[e, b] = img
            bands[band] = {"n": 5, "mjd_mean": mjd(yr), "wl_min": 1.0, "wl_max": 1.1}
            if e == 0 and band in ("D1", "D4"):
                for k in range(5):
                    t = mjd(yr) + 0.1 * k
                    ex = img + rng.normal(0, 0.05, (N, N))
                    ex += gauss(AST["x0"] + AST["vx"] * 0.1 * k, AST["y0"] + AST["vy"] * 0.1 * k, 2.0)
                    exposures.append({"index": len(exposures), "epoch": e, "band": band, "mjd": t, "wl": 1.0 + b,
                                      "rms": 0.07, "obs_id": f"obs{k}", "file": f"f{e}{band}{k}.fits"})
                    planes.append(ex)
        epochs.append({"index": e, "mjd_mean": mjd(yr), "mjd_min": mjd(yr), "mjd_max": mjd(yr) + 0.5,
                       "date_min": f"{int(yr)}-01-01", "date_max": f"{int(yr)}-01-02", "n": 30, "bands": bands})
    cube = [3, 6, N, N]
    meta = {"id": "fld1", "target": {"name": "synthetic"},
            "params": {"ra": 150.0, "dec": 10.0, "fov_arcmin": N * 6.15 / 60},
            "grid": {"ra": 150.0, "dec": 10.0, "n": N, "pixscale_arcsec": 6.15},
            "epochs": epochs, "exposures": exposures, "photometry": [],
            "arrays": {"coadds": {"file": "coadds.f32", "dtype": "float32", "shape": cube},
                       "coverage": {"file": "coverage.u16", "dtype": "uint16", "shape": cube},
                       "flags": {"file": "flags.u32", "dtype": "uint32", "shape": cube},
                       "exposures": {"file": "exposures.f16", "dtype": "float16", "shape": [len(planes), N, N]}}}
    arrays = {"coadds": coadds, "coverage": np.full(cube, 5, np.uint16), "flags": np.zeros(cube, np.uint32),
              "exposures": np.array(planes, np.float32)}
    return meta, arrays


@pytest.fixture()
def field(tmp_path):
    services.configure(cache_root=tmp_path)
    meta, arrays = make_field()
    results.write_result(cache.layout().sequence("fld1"), meta, arrays)
    yield load_field(load_context("fld1"))
    services.configure(None)


def test_detect_and_mover(field):
    srcs = sources.detect(field, snr_min=5)
    assert len(srcs) >= 5
    cands = movers.fit_all(field, srcs)
    assert cands and abs(cands[0]["x"] - 20) < 1.5 and abs(cands[0]["y"] - 20) < 1.5
    # +x is west: negative pmra, ~12.3"/yr
    assert cands[0]["motion"]["pmra"] < -8000 and cands[0]["category"] == "unknown_mover"
    static = [s for s in srcs if abs(s["x"] - 10) < 1 and abs(s["y"] - 10) < 1][0]
    assert abs(static["motion"]["pm_total"]) < 3000


def test_red_excess_map(field):
    srcs = sources.detect(field, snr_min=5)
    red = red_excess_map(field, sources.deep_images(field), srcs)
    assert any(abs(r["x"] - RED[0]) <= 1 and abs(r["y"] - RED[1]) <= 1 for r in red)


def test_tracklet_from_single_exposures(field):
    dets, stats = transients.detect(field)
    assert stats["images"] == 10
    tracklets, _ = transients.link(dets, field.grid.pixscale)
    assert tracklets
    t = tracklets[0]
    assert t["n"] >= 3 and abs(t["rate_arcsec_per_hr"] - np.hypot(20, 5) * 6.15 / 24) < 1.5
    assert t["partner_fraction"] == 1.0  # the D4 twin sees it too


def test_trackstack_recovers_asteroid(field):
    rate = float(np.hypot(AST["vx"], AST["vy"]) * 6.15 / 24)
    pa = float(np.degrees(np.arctan2(-AST["vx"], AST["vy"])) % 360)
    res = trackstack.stack(field, 0, rate, pa, bands=["D1"], subtract_static=True)
    img = res["parts"][0]["image"]
    dt_h = (res["t_ref"] - mjd(YEARS[0])) * 24
    x = AST["x0"] + AST["vx"] / 24 * dt_h
    y = AST["y0"] + AST["vy"] / 24 * dt_h
    assert img[int(round(y)), int(round(x))] > 1.0 and res["frames"] == 5
    s = trackstack.search(field, 0, rate_min=1.0, rate_max=8.0, window_hours=24, bands=["D1", "D4"], snr_min=5)
    assert s["candidates"] and abs(s["candidates"][0]["rate_arcsec_per_hr"] - rate) < 1.5


def test_full_analysis_runs(field, monkeypatch):
    monkeypatch.setattr(known, "field_objects", lambda fd, m, v=21.0: [])
    res = analyze_field(field.ctx, FieldOptions(catalogs=False))
    assert res["candidates"]["movers"] and res["candidates"]["tracklets"]
    assert set(res["stats"]["seconds"]) >= {"detect", "motion", "transients", "total"}


def test_identification_criteria():
    t = {"detections": [{"mjd": 60000.0, "ra": 150.0, "dec": 10.0}, {"mjd": 60000.1, "ra": 150.001, "dec": 10.0}],
         "rate_arcsec_per_hr": 1.5, "pa_deg": 90.0}
    good = {"name": "A", "kind": "asteroid", "vmag": 18, "mjd": 60000.0, "ra": 150.0, "dec": 10.0,
            "ra_rate": 1.5, "dec_rate": 0.0, "speed_arcsec_per_hr": 1.5, "pa_deg": 90.0}
    wrong_dir = dict(good, name="B", pa_deg=180.0, ra_rate=0.0, dec_rate=-1.5)
    ranked = known._rank(t, [good, wrong_dir])
    assert [m["name"] for m in ranked] == ["A"] and ranked[0]["o_minus_c_arcsec"]


def test_realbogus_stumps_learn():
    rng = np.random.default_rng(0)
    x = rng.normal(size=(400, len(realbogus.FEATURES)))
    y = (x[:, 0] + 0.5 * x[:, 1] > 0).astype(int)
    model = realbogus.fit(x, y, rounds=40)
    acc = ((realbogus.predict(model, x) >= 0.5) == y).mean()
    assert acc > 0.85 and set(realbogus.importance(model)) == set(realbogus.FEATURES)


def test_ades_space_station(monkeypatch):
    monkeypatch.setattr(ades.observer, "states", lambda mjds: {f"{m:.5f}": [1000.0, 2000.0, 6500.0, 0, 7, 1]
                                                                 for m in mjds})
    t = [{"id": "T001", "detections": [{"mjd": 60000.0, "ra": 150.0, "dec": -10.0, "band": "D1", "snr": 10.0}]}]
    text = ades.ades_psv(t, "fld1")
    row = text.strip().splitlines()[-1].split("|")
    assert "! mpcCode 288" in text and "NOT A SUBMISSION" in text
    assert row[2] == "288" and row[3] == "ICRF_KM" and row[5] == "1000.000" and row[13] == "Gaia3" and row[14] == "Z"


def test_jobs_dedupe_and_cancel(field):
    async def run():
        jobs = FieldJobs(interest_timeout=0.5)
        jid = make_id("fld1", "s", {"x": 1})

        def slow(ctx, progress, check):
            import time
            for _ in range(200):
                check()
                time.sleep(0.02)
            return {"ok": True}

        a = await jobs.submit_work("fld1", jid, slow, client="a")
        b = await jobs.submit_work("fld1", jid, slow, client="b")
        assert a["id"] == b["id"] == jid and len(jobs.jobs) == 1
        await jobs.withdraw(jid, "a")
        await jobs.withdraw(jid, "b")
        for _ in range(100):
            await asyncio.sleep(0.05)
            if jobs.jobs[jid].status == "cancelled":
                break
        assert jobs.jobs[jid].status == "cancelled"

        def quick(ctx, progress, check):
            return {"value": 42}

        jid2 = make_id("fld1", "s", {"x": 2})
        await jobs.submit_work("fld1", jid2, quick)
        for _ in range(100):
            await asyncio.sleep(0.02)
            if (await jobs.status(jid2))[0] == "done":
                break
        assert (await jobs.status(jid2))[0] == "done"

    asyncio.run(run())


def test_routes(field):
    c = TestClient(create_app(web_dist=None))
    assert c.get("/api/field/nope").status_code == 404
    assert c.get("/api/field/fld1-fabc").status_code == 404
    r = c.get("/api/sequences/fld1/trackstack.bin", params={"epoch": 0, "rate": 5, "pa": 270, "bands": "D1"})
    assert r.status_code == 200 and r.headers["x-array-shape"] == f"{N},{N}" and len(r.content) == N * N * 4
    assert c.get("/api/sequences/fld1/trackstack.bin", params={"epoch": 0, "rate": 5, "bands": "D9"}).status_code == 422
    assert c.post("/api/sequences/unknown/field", json={}).status_code == 404
