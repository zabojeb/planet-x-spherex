"""'Click a point -> Analyze' on the synthetic field of test_field (offline; catalogues and JPL stubbed)."""

import copy
import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from spherex_api.app import create_app
from spherex_core import cache, catalogs, services
from spherex_core.analysis import point
from spherex_core.analysis.common import load_context
from spherex_core.field import jobs as fj
from spherex_core.field import known
from spherex_core.pipeline import results

from test_field import make_field  # pytest puts tests/ on sys.path (rootdir-relative, no package)


@pytest.fixture()
def seq(tmp_path, monkeypatch):
    services.configure(cache_root=tmp_path)
    fj.reset()
    meta, arrays = make_field()
    results.write_result(cache.layout().sequence("fld1"), meta, arrays)
    ctx = load_context("fld1")

    def fake_cone(name, ra, dec, radius_deg, max_age_hours=0):
        if name == "gaia":  # one static star far from the mover, nothing at the mover
            return [{"id": "Gaia DR3 1", "ra": ctx.grid.ra + 0.01, "dec": ctx.grid.dec, "epoch": 2016.0,
                     "pmra": 0.0, "pmdec": 0.0, "mag": 15.0, "mag_band": "G"}]
        if name == "catwise":
            raise RuntimeError("IRSA down")  # one catalogue failing must not fail the job
        return []

    def fake_photometry(ctx_, target):
        recs = []
        for i, e in enumerate(ctx_.meta["epochs"]):
            for b, wl in (("D1", 0.9), ("D2", 1.3), ("D3", 2.0), ("D5", 4.1), ("D6", 4.7)):
                f = 2.0 if wl > 3.8 else 0.5  # red source
                recs.append({"index": len(recs), "epoch": i, "band": b, "mjd": e["mjd_mean"], "wl": wl,
                             "dwl": 0.05, "flux_mjy": f, "err_mjy": 0.05, "flagged": False, "relative": False,
                             "method": "psf"})
        return {"sequence": ctx_.seq_id, "photometry": recs, "rings": {}, "stats": {},
                "summary": {"n": len(recs), "n_flagged": 0, "n_relative": 0, "n_saturated": 0}}

    monkeypatch.setattr(catalogs, "cone", fake_cone)
    monkeypatch.setattr(known, "query", lambda *a, **k: [])
    from spherex_core.spectral import synphot

    def no_filters(band):  # offline: no SVO filter curves (synthetic photometry reports them unavailable)
        raise RuntimeError("offline test")

    monkeypatch.setattr(synphot, "curve", no_filters)
    monkeypatch.setattr(point, "photometry", fake_photometry)
    yield ctx
    services.configure(None)
    fj.reset()


def test_analyze_point_on_mover(seq):
    ra, dec = (float(v) for v in seq.grid.wcs.all_pix2world(20.0, 20.0, 0))
    req = point.PointRequest(ra=ra, dec=dec)
    stages = []
    r = point.analyze_point(seq, req, lambda s, d, t, steps=None: stages.append(s), lambda: None, deadline_s=600)
    assert "identification" in stages[0] and stages[-1] == "verdict"
    assert set(r["timings"]["steps_s"]) == set(point.STEPS) and not r["timings"]["timed_out"]
    for slot in point.STEPS:
        assert slot in r
    assert "error" in r["identification"]["catwise"] and r["identification"]["gaia"]["counterpart"] is False
    assert r["motion"]["combined"]["pm_total"] > 5000  # ~1 px per half year
    assert r["colours"]["red_excess"] is True
    assert r["tracklets"]["available"] is False
    v = r["verdict"]
    kinds = {f["kind"] for f in v["findings"]}
    assert {"motion", "colour", "identity"} <= kinds
    assert v["novelty"] >= 0.7 and "Gaia" in v["headline"]


def test_step_failure_is_isolated(seq, monkeypatch):
    monkeypatch.setattr(point, "colours", lambda recs: 1 / 0)
    ra, dec = (float(v) for v in seq.grid.wcs.all_pix2world(10.0, 10.0, 0))
    r = point.analyze_point(seq, point.PointRequest(ra=ra, dec=dec), lambda *a, **k: None, lambda: None,
                            deadline_s=600)
    assert r["colours"]["error"].startswith("ZeroDivisionError") and "verdict" in r


def test_slow_step_times_out_without_blocking(seq, monkeypatch):
    """A hanging service (here: sb_ident) becomes a timed-out slot; the rest of the analysis still returns."""
    monkeypatch.setattr(known, "query", lambda *a, **k: time.sleep(5) or [])
    monkeypatch.setattr(point, "KNOWN_BUDGET_S", 30.0)
    ra, dec = (float(v) for v in seq.grid.wcs.all_pix2world(20.0, 20.0, 0))
    t0 = time.monotonic()
    r = point.analyze_point(seq, point.PointRequest(ra=ra, dec=dec), lambda *a, **k: None, lambda: None,
                            deadline_s=2.0)
    assert time.monotonic() - t0 < 4.0
    assert "known_objects" in r["timings"]["timed_out"] and "TimeoutError" in r["known_objects"]["error"]
    assert "photometry" in r and "error" not in r["photometry"] and "verdict" in r


def test_late_steps_do_not_touch_the_returned_result(seq, monkeypatch):
    """A step still running at the deadline keeps running in the background; when it ends it must not rewrite the
    result the job is serialising, start the steps that follow it, or report progress for a finished job."""
    release, ran = threading.Event(), []
    slow = point.photometry

    def held_photometry(ctx_, target):
        release.wait(10)
        return slow(ctx_, target)

    def counted(fn, name):
        return lambda *a, **k: ran.append(name) or fn(*a, **k)

    monkeypatch.setattr(point, "photometry", held_photometry)
    monkeypatch.setattr(point, "colours", counted(point.colours, "colours"))
    calls, returned = [], threading.Event()
    progress = lambda *a, **k: calls.append(returned.is_set())  # noqa: E731
    ra, dec = (float(v) for v in seq.grid.wcs.all_pix2world(20.0, 20.0, 0))
    r = point.analyze_point(seq, point.PointRequest(ra=ra, dec=dec), progress, lambda: None, deadline_s=1.0)
    returned.set()
    before = copy.deepcopy(r)
    assert {"photometry", "spectrum", "periodogram", "colours"} <= set(r["timings"]["timed_out"])

    release.set()  # photometry now finishes in the background
    for t in threading.enumerate():
        if t.name.startswith("point"):
            t.join(5)
    assert r == before and r["timings"]["steps_s"] == before["timings"]["steps_s"]
    assert "TimeoutError" in r["spectrum"]["error"] and "TimeoutError" in r["colours"]["error"]
    assert not ran and not any(calls)  # nothing after the deadline: no late steps, no late progress
    json.dumps(results.clean_json(r))


def test_verdict_known_asteroid_and_saturation():
    r = {"identification": {"gaia": {"matches": [], "counterpart": False}},
         "known_objects": {"matches": [{"name": "(2) Pallas", "kind": "asteroid", "sep_arcsec": 3.0, "epoch": 1}]},
         "photometry": {"summary": {"n": 10, "n_relative": 8}}}
    v = point.verdict(r)
    texts = " ".join(f["text"] for f in v["findings"])
    assert "Pallas" in texts and "Saturated" in texts


def test_verdict_mover_and_weak_motion():
    base = {"identification": {"gaia": {"matches": [], "counterpart": False}},
            "motion": {"combined": {"pm_total": 4400.0, "pm_total_err": 1270.0, "pa_deg": 215.0}}}
    v = point.verdict({**base, "tracklets": {"candidates": [
        {"kind": "tracklet", "id": "T001", "sep_arcsec": 0.0, "rate_arcsec_per_hr": 6.9, "known": "97 Klotho"}]}})
    texts = " ".join(f["text"] for f in v["findings"])
    assert "not a proper motion" in texts and "Klotho" in texts and v["novelty"] < 0.3
    v2 = point.verdict(base)  # 3.5 sigma without a mover: hedged, not a candidate claim
    assert "needs more epochs" in " ".join(f["text"] for f in v2["findings"])


def test_ring_drift_is_not_variability():
    per_band = {"D2": {"ratio": 1.31, "z": 9.0}, "D3": {"ratio": 1.1, "z": 6.0}}
    r = {"photometry": {"summary": {"n": 10, "n_relative": 10}},
         "spectrum": {"variability": {"epochs": [{"epoch": 1, "per_band": per_band}]}}}
    assert not [f for f in point.verdict(r)["findings"] if f["kind"] == "variability"]
    per_band["D3"]["ratio"] = 0.6
    assert [f for f in point.verdict(r)["findings"] if f["kind"] == "variability"]


def test_cache_key_rounds_position():
    a = point.PointRequest(ra=10.00001, dec=-5.00002).key()
    b = point.PointRequest(ra=10.00003, dec=-5.00001).key()
    assert a == b


def test_routes_lifecycle(seq):
    ra, dec = (float(v) for v in seq.grid.wcs.all_pix2world(20.0, 20.0, 0))
    with TestClient(create_app(web_dist=None)) as c:
        assert c.post("/api/sequences/nope/analyze", json={"ra": ra, "dec": dec}).status_code == 404
        r = c.post("/api/sequences/fld1/analyze", json={"ra": ra, "dec": dec}, headers={"X-Client-Id": "a"})
        assert r.status_code == 200
        jid = r.json()["id"]
        assert "-p" in jid
        again = c.post("/api/sequences/fld1/analyze", json={"ra": ra + 1e-6, "dec": dec})
        assert again.json()["id"] == jid  # deduplicated
        for _ in range(100):
            g = c.get(f"/api/analyses/{jid}", headers={"X-Client-Id": "a"})
            if g.status_code != 202:
                break
            time.sleep(0.1)
        assert g.status_code == 200 and g.headers["etag"] == f'"{jid}"'
        assert g.json()["verdict"]["findings"]
        assert c.get("/api/analyses/fld1-fabc").status_code == 404
        assert c.delete("/api/analyses/fld1-pdeadbeef00").status_code == 204
