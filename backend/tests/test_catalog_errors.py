"""Catalog failures surface as 502 with a readable message; damaged cache files are refetched."""

import pytest
from fastapi.testclient import TestClient

from spherex_api.app import create_app
from spherex_core import cache, catalogs, services

ROWS = [{"id": "Gaia DR3 1", "ra": 10.0, "dec": 20.0, "epoch": 2016.0, "pmra": 1.0, "pmdec": 2.0, "mag": 12.0,
         "mag_band": "G"}]


@pytest.fixture()
def client(tmp_path):
    services.configure(cache_root=tmp_path)
    yield TestClient(create_app(web_dist=None))
    services.configure(None)


@pytest.mark.parametrize("name", ["gaia", "catwise", "2mass", "simbad"])
@pytest.mark.parametrize("exc", [RuntimeError("TAP query failed"), TimeoutError("timed out"),
                                 KeyError("source_name"), ValueError("bad CSV")])
def test_upstream_failure_is_502(client, monkeypatch, name, exc):
    def boom(*a, **k):
        raise exc

    monkeypatch.setattr(catalogs._SOURCES[name], "query", boom)
    r = client.get(f"/api/catalogs/{name}", params={"ra": 10, "dec": 20, "radius_arcmin": 2})
    assert r.status_code == 502
    assert catalogs.CATALOGS[name] in r.json()["detail"]


def test_damaged_cache_is_refetched(client, monkeypatch):
    calls = []
    monkeypatch.setattr(catalogs._SOURCES["gaia"], "query", lambda *a: calls.append(a) or ROWS)
    path = cache.layout().catalog("gaia", 10.0, 20.0, 2 / 60)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('[{"id": "trunc')  # a partially written file
    r = client.get("/api/catalogs/gaia", params={"ra": 10, "dec": 20, "radius_arcmin": 2})
    assert r.status_code == 200 and r.json() == ROWS and len(calls) == 1
    r = client.get("/api/catalogs/gaia", params={"ra": 10, "dec": 20, "radius_arcmin": 2})
    assert r.status_code == 200 and len(calls) == 1  # served from the repaired cache


def test_simbad_falls_back_to_a_mirror(monkeypatch):
    """The first SIMBAD service failing is not an error; names lose the NAME prefix; V is the preferred magnitude."""
    from spherex_core.catalogs import simbad

    tried = []

    def fake_tap(adql, url=None, **kw):
        tried.append(url)
        if len(tried) == 1:
            raise RuntimeError("TAP query failed after 1 attempts: HTTP Error 404")
        return [{"main_id": "NAME Barnard's  star", "ra": "269.452", "dec": "4.693", "pmra": "-801.5", "pmdec": "10362.4",
                 "plx_value": "547", "otype": "BY*", "description": "BY Dra Variable", "sp_type": "M4V",
                 "rvz_redshift": "", "refs": "924", "V": "9.51", "G": "8.19", "J": "", "K": ""},
                {"main_id": "X 1", "ra": "", "dec": "4.7", "pmra": "", "pmdec": "", "plx_value": "", "otype": "X",
                 "description": "", "sp_type": "", "rvz_redshift": "", "refs": "0", "V": "", "G": "", "J": "", "K": ""}]

    monkeypatch.setattr(simbad, "tap_csv", fake_tap)
    rows = simbad.query(269.45, 4.69, 0.05)
    assert len(tried) == 2 and tried[0] != tried[1]
    assert rows == [{"id": "Barnard's star", "ra": 269.452, "dec": 4.693, "epoch": 2000.0, "pmra": -801.5,
                     "pmdec": 10362.4, "parallax": 547.0, "mag": 9.51, "mag_band": "V", "otype": "BY*",
                     "type": "BY Dra Variable", "sp_type": "M4V", "z": None, "refs": 924}]


def test_simbad_all_mirrors_down(monkeypatch):
    from spherex_core.catalogs import simbad

    def down(*a, **k):
        raise RuntimeError("TAP query failed after 1 attempts: timed out")

    monkeypatch.setattr(simbad, "tap_csv", down)
    with pytest.raises(RuntimeError, match="timed out"):
        simbad.query(10, 20, 0.05)
