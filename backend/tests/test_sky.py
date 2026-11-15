"""The sky map's backend (ADR-0061): HiPS proxy cache, coverage at a point, footprints near one."""

import json

import pytest
from fastapi.testclient import TestClient

from spherex_api.app import create_app
from spherex_core import hips, services, upstreams
from spherex_core.archive import mirror


@pytest.fixture
def client(tmp_path):
    services.configure(cache_root=tmp_path)
    upstreams.reset()
    yield TestClient(create_app(web_dist=None)), tmp_path
    services.configure(None)


def test_paths_are_validated():
    for ok in ["properties", "Moc.fits", "Norder3/Allsky_8.png", "Norder5/Dir10000/Npix12345_93.png",
               "Norder0/Dir0/Npix0.png"]:
        assert hips.check_path(ok) == ok
    for bad in ["../etc/passwd", "Norder3/../../x.png", "Norder3/Dir0/Npix1.exe", "index.html", "HpxFinder/x"]:
        with pytest.raises(hips.NotHipsPath):
            hips.check_path(bad)


def test_plan_counts():
    assert hips.tiles(0) == 12 and hips.tiles(3) == 768 and hips.tiles(5) == 12288
    p = hips.plan((8,), 1)
    assert p[:3] == ["properties", "Moc.fits", "Norder3/Allsky_8.png"]
    assert len(p) == 3 + 12 + 48 and "Norder1/Dir0/Npix47_8.png" in p


def test_tile_fetched_once_then_served_from_disk(client, monkeypatch):
    c, root = client
    calls = []

    async def fake_download(s, path):
        calls.append(path)
        return b"\x89PNG fake tile"

    monkeypatch.setattr(hips, "_download", fake_download)
    r1 = c.get("/hips/spherex-dr1/Norder3/Dir0/Npix100_8.png")
    r2 = c.get("/hips/spherex-dr1/Norder3/Dir0/Npix100_8.png")
    assert r1.status_code == r2.status_code == 200 and r1.content == b"\x89PNG fake tile"
    assert r1.headers["content-type"] == "image/png"
    assert "immutable" in r1.headers["cache-control"]
    assert calls == ["Norder3/Dir0/Npix100_8.png"]  # the second request never left the server
    assert (root / "hips" / "spherex-dr1" / "Norder3" / "Dir0" / "Npix100_8.png").exists()
    r3 = c.get("/hips/spherex-dr1/Norder3/Dir0/Npix100_8.png", headers={"If-None-Match": r1.headers["etag"]})
    assert r3.status_code == 304


def test_missing_tile_is_remembered(client, monkeypatch):
    c, _ = client
    calls = []

    async def fake_download(s, path):
        calls.append(path)
        raise hips.Missing(path)

    monkeypatch.setattr(hips, "_download", fake_download)
    assert c.get("/hips/spherex-dr1/Norder0/Dir0/Npix0_0.png").status_code == 404
    assert c.get("/hips/spherex-dr1/Norder0/Dir0/Npix0_0.png").status_code == 404
    assert len(calls) == 1


def test_source_down_is_502_but_cached_properties_still_served(client, monkeypatch):
    c, root = client

    async def ok(s, path):
        return b"hips_order = 5\n"

    async def down(s, path):
        raise upstreams.AllFailed("hips", {"irsa-hips": "HTTP 502"})

    monkeypatch.setattr(hips, "_download", down)
    assert c.get("/hips/spherex-dr1/Norder4/Dir0/Npix1_8.png").status_code == 502
    monkeypatch.setattr(hips, "_download", ok)
    assert c.get("/hips/spherex-dr1/properties").status_code == 200
    p = root / "hips" / "spherex-dr1" / "properties"
    import os
    import time

    old = time.time() - 2 * hips.META_TTL_S
    os.utime(p, (old, old))  # stale: a refresh is attempted, fails, and the cached copy is served
    monkeypatch.setattr(hips, "_download", down)
    r = c.get("/hips/spherex-dr1/properties")
    assert r.status_code == 200 and "hips_order" in r.text and "max-age=3600" in r.headers["cache-control"]


def test_unknown_survey_and_bad_path(client):
    c, _ = client
    assert c.get("/hips/nope/properties").status_code == 404
    assert c.get("/hips/spherex-dr1/index.html").status_code == 404


def _mirror_with_two_files(tmp_path):
    m = mirror.Mirror(tmp_path / "m.sqlite")
    sq = lambda ra, dec, h: [ra - h, dec - h, ra + h, dec - h, ra + h, dec + h, ra - h, dec + h]  # noqa: E731
    rows = []
    for i, (mjd, band) in enumerate([(60900.0, "D1"), (61100.0, "D4"), (60905.0, "D2")]):
        corners = sq(10.0, 20.0, 1.7)
        rows.append({"uri": f"ibe/data/spherex/dr1/level2/x/{i}.fits", "release": "dr1", "collection": "spherex_dr1",
                     "obs_id": f"obs{i}", "band": band, "mjd": mjd, "mjd_start": mjd, "mjd_end": mjd,
                     "wl_min": 1.0, "wl_max": 2.0, "corners": corners, "source": "test"})
    # the mirror counts as usable only with rows of every release the collections need (qr3 too)
    rows.append({"uri": "ibe/data/spherex/qr3/level2/y/0.fits", "release": "qr3", "collection": "spherex_qr3",
                 "obs_id": "far", "band": "D1", "mjd": 61200.0, "mjd_start": 61200.0, "mjd_end": 61200.0,
                 "wl_min": 1.0, "wl_max": 2.0, "corners": sq(200.0, -40.0, 1.7), "source": "test"})
    m.upsert(rows)
    m.set_state(**{"complete:dr1": 1, "complete:qr3": 1, "synced_at": __import__("time").time()})
    return m


def test_coverage_and_footprints_from_mirror(client, monkeypatch, tmp_path):
    c, _ = client
    m = _mirror_with_two_files(tmp_path)
    monkeypatch.setattr(mirror, "get", lambda: m)
    cov = c.get("/api/coverage", params={"ra": 10.0, "dec": 20.0}).json()
    assert cov["n_exposures"] == 3 and cov["n_visits"] == 2 and cov["source"].startswith("mirror")
    assert cov["per_detector"]["D1"] == 1 and cov["date_min"] <= cov["date_max"]
    fp = c.get("/api/footprints", params={"ra": 10.0, "dec": 20.0, "radius": 2}).json()
    assert fp["available"] and fp["total"] == 2  # D4 shares the sky of D1: not drawn twice
    assert fp["footprints"][0]["mjd"] >= fp["footprints"][-1]["mjd"] and len(fp["footprints"][0]["corners"]) == 8
    far = c.get("/api/footprints", params={"ra": 300.0, "dec": 60.0, "radius": 2}).json()
    assert far["total"] == 0


def test_footprints_without_mirror(client):
    c, _ = client
    out = c.get("/api/footprints", params={"ra": 10.0, "dec": 20.0}).json()
    assert out["available"] is False and out["footprints"] == []
    assert json.dumps(out)
