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


@pytest.mark.parametrize("name", ["gaia", "catwise", "2mass"])
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
