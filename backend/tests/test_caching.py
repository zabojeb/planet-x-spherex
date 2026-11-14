"""HTTP caching of finished results, slices, lite/columnar meta, job deletion and the bucket store."""

import json

import numpy as np
import pytest
from fastapi.testclient import TestClient

from spherex_api.app import create_app
from spherex_core import cache, services, store as store_mod
from spherex_core.pipeline import results
from test_results import make_result

IMM = "public, max-age=31536000, immutable"


@pytest.fixture()
def client(tmp_path):
    services.configure(cache_root=tmp_path)
    meta, arrays = make_result()
    results.write_result(cache.layout().sequence("abc123"), meta, arrays)
    yield TestClient(create_app(web_dist=None)), meta, arrays
    services.configure(None)


def test_meta_precompressed_and_conditional(client):
    c, meta, _ = client
    r = c.get("/api/sequences/abc123", headers={"Accept-Encoding": "br"})
    assert r.status_code == 200 and r.headers["content-encoding"] == "br"
    assert r.headers["cache-control"] == IMM and r.headers["etag"] == '"abc123"'
    assert json.loads(r.content) == json.loads(json.dumps(results.clean_json(meta)))
    r = c.get("/api/sequences/abc123", headers={"If-None-Match": '"abc123"'})
    assert r.status_code == 304
    r = c.get("/api/sequences/abc123/coadds.bin", headers={"If-None-Match": '"abc123-coadds"'})
    assert r.status_code == 304


def test_slices_range_lite_columns(client):
    c, meta, arrays = client
    r = c.get("/api/sequences/abc123/coadds.bin", params={"epoch": 0, "band": "D2"})
    assert r.status_code == 200 and r.headers["cache-control"] == IMM and r.headers["x-array-shape"] == "5,5"
    np.testing.assert_array_equal(np.frombuffer(r.content, "<f4").reshape(5, 5), arrays["coadds"][0, 1])
    r = c.get("/api/sequences/abc123/flags.bin", params={"epoch": 0, "band": "D1"})
    assert r.headers["x-array-dtype"] == "uint32" and len(r.content) == 25 * 4
    assert c.get("/api/sequences/abc123/coadds.bin", params={"epoch": 0}).status_code == 422
    r = c.get("/api/sequences/abc123/coadds.bin", headers={"Range": "bytes=0-99"})
    assert r.status_code == 206 and len(r.content) == 100
    lite = c.get("/api/sequences/abc123/lite").json()
    assert "exposures" not in lite and lite["tables"]["exposures"]["rows"] == len(meta["exposures"])
    col = c.get("/api/sequences/abc123/columns/exposures").json()
    assert col["rows"] == len(meta["exposures"]) and col["data"]["band"] == [e["band"] for e in meta["exposures"]]
    assert c.get("/api/sequences/abc123/columns/nope").status_code == 404


def test_delete_unknown_is_204_and_ready(client):
    c, _, _ = client
    assert c.delete("/api/sequences/nope").status_code == 204
    r = c.get("/api/ready")
    assert r.status_code == 200 and r.json()["backend"] == "inprocess"


def test_bucket_store_roundtrip(tmp_path, monkeypatch):
    moto = pytest.importorskip("moto")
    import boto3

    with moto.mock_aws():
        monkeypatch.setenv("SPHEREX_BUCKET", "results")
        monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "x")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "x")
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="results")
        writer = tmp_path / "worker"
        reader = tmp_path / "api"
        cache.set_root(writer)
        meta, arrays = make_result()
        results.write_result(cache.layout().sequence("seq1"), meta, arrays)
        s = store_mod.ResultStore()
        s.publish("seq1")
        cache.set_root(reader)
        assert not results.is_complete(cache.layout().sequence("seq1"))
        assert s.ensure_local("seq1")  # downloaded
        assert results.read_meta(cache.layout().sequence("seq1"))["id"] == meta["id"]
        assert not s.ensure_local("missing")
        cache.set_root(None)
