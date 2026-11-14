"""HTTP adapter wiring, offline: a synthetic result in a temporary cache served through the real routes."""

import json

import numpy as np
import pytest
from fastapi.testclient import TestClient

from spherex_api.app import create_app
from spherex_core import cache, services
from spherex_core.pipeline import results
from test_results import make_result


@pytest.fixture()
def client(tmp_path):
    services.configure(cache_root=tmp_path)
    meta, arrays = make_result()
    results.write_result(cache.layout().sequence("abc123"), meta, arrays)
    yield TestClient(create_app(web_dist=None)), meta, arrays
    services.configure(None)


def test_health_and_targets(client):
    c, _, _ = client
    h = c.get("/api/health").json()
    assert h["ok"] is True and "spherex_dr1" in h["collections"]
    targets = c.get("/api/targets").json()
    assert [t["id"] for t in targets] == ["barnard", "eps-indi-b", "wise-0855", "rw-aur", "r-dra"]
    hints = {t["id"]: (t["default_band"], t["default_mode"]) for t in targets}
    assert hints["wise-0855"] == ("D6", "rgb") and hints["barnard"] == ("all", "diff")


def test_resolve_coordinates(client):
    c, _, _ = client
    r = c.get("/api/resolve", params={"q": "269.452 4.693"})
    assert r.status_code == 200 and r.json()["ra"] == 269.452 and r.json()["source"] == "user"


def test_sequence_and_arrays(client):
    c, meta, arrays = client
    r = c.get("/api/sequences/abc123")
    assert r.status_code == 200 and r.content == json.dumps(results.clean_json(meta)).encode()
    r = c.get("/api/sequences/abc123/coadds.bin")
    assert r.status_code == 200 and r.headers["cache-control"] == "public, max-age=86400"
    np.testing.assert_array_equal(np.frombuffer(r.content, "<f4").reshape(arrays["coadds"].shape), arrays["coadds"])
    r = c.get("/api/sequences/abc123/exposures.bin", params={"epoch": 0, "band": "D1"})
    assert r.status_code == 200 and r.headers["x-exposure-indices"] == "0,1"
    assert r.headers["access-control-expose-headers"] == "X-Exposure-Indices"
    assert len(r.content) == 2 * 5 * 5 * 4


@pytest.mark.parametrize("path,status,detail", [
    ("/api/sequences/nope", 404, "unknown sequence"),
    ("/api/sequences/abc123/foo.bin", 404, "unknown array"),
    ("/api/sequences/nope/coadds.bin", 404, "sequence not ready"),
    ("/api/sequences/abc123/exposures.bin?epoch=9&band=D1", 404, "no exposures for this epoch and detector"),
    ("/api/sequences/abc123/exposures.bin?epoch=-1&band=D1", 422, None),
    ("/api/catalogs/nope?ra=1&dec=2", 404, "unknown catalog"),
])
def test_errors(client, path, status, detail):
    c, _, _ = client
    r = c.get(path)
    assert r.status_code == status
    if detail:
        assert r.json() == {"detail": detail}


def test_running_job_reports_202_and_failed_job_500(client):
    c, _, _ = client
    from spherex_core.pipeline.jobs import Job
    from spherex_core.pipeline.params import SequenceParams

    p = SequenceParams(ra=1.0, dec=2.0)
    services.jobs().jobs["run1"] = Job(id="run1", params=p, status="running", stage="fetch", done=3, total=9)
    services.jobs().jobs["bad1"] = Job(id="bad1", params=p, status="error", error="boom")
    r = c.get("/api/sequences/run1")
    assert r.status_code == 202 and r.json()["done"] == 3 and r.json()["stage"] == "fetch"
    r = c.get("/api/sequences/bad1")
    assert r.status_code == 500 and r.json()["error"] == "boom"


def test_list_catalogs(client):
    c, _, _ = client
    assert c.get("/api/catalogs").json() == {"gaia": "Gaia DR3 (ESA)", "catwise": "CatWISE2020 (IRSA)",
                                             "2mass": "2MASS point sources (IRSA)", "simbad": "SIMBAD (CDS)"}
