"""The result format: write/read round trip, JSON cleaning, exposure slicing."""

import json

import numpy as np
import pytest

from spherex_core.errors import NotFound
from spherex_core.pipeline import results


def make_result(n_ep=2, n=5, with_exposures=True):
    rng = np.random.default_rng(0)
    coadds = rng.normal(size=(n_ep, 6, n, n)).astype(np.float32)
    coadds[1, 3] = np.nan
    arrays = {"coadds": coadds,
              "coverage": np.full((n_ep, 6, n, n), 3, np.uint16),
              "flags": np.full((n_ep, 6, n, n), 1 << 21, np.uint32)}
    # exposures sorted by (epoch, band, time): epoch 0 D1 x2, epoch 0 D6 x1, epoch 1 D1 x1
    exposures = [{"index": 0, "epoch": 0, "band": "D1"}, {"index": 1, "epoch": 0, "band": "D1"},
                 {"index": 2, "epoch": 0, "band": "D6"}, {"index": 3, "epoch": 1, "band": "D1"}]
    if with_exposures:
        arrays["exposures"] = np.arange(4 * n * n, dtype=np.float32).reshape(4, n, n)
    cube = [n_ep, 6, n, n]
    meta = {
        "id": "abc123", "rms": float("nan"), "count": np.int64(4), "flag": np.bool_(True), "x": np.float32(0.5),
        "arrays": {"coadds": {"file": "coadds.f32", "dtype": "float32", "shape": cube},
                   "coverage": {"file": "coverage.u16", "dtype": "uint16", "shape": cube},
                   "flags": {"file": "flags.u32", "dtype": "uint32", "shape": cube},
                   "exposures": ({"file": "exposures.f16", "dtype": "float16", "shape": [4, n, n]}
                                 if with_exposures else None)},
        "exposures": exposures,
    }
    return meta, arrays


def test_clean_json():
    out = results.clean_json({"a": [np.float64("inf"), np.int32(2), (np.bool_(False), 1.5)], "b": float("nan")})
    assert out == {"a": [None, 2, [False, 1.5]], "b": None}
    json.dumps(out, allow_nan=False)


def test_round_trip(tmp_path):
    meta, arrays = make_result()
    d = tmp_path / "abc123"
    assert not results.is_complete(d)
    results.write_result(d, meta, arrays)
    assert results.is_complete(d) and not (tmp_path / "abc123.tmp").exists()
    back = results.read_meta(d)
    assert back["rms"] is None and back["count"] == 4 and back["flag"] is True and back["x"] == 0.5
    assert results.meta_bytes(d) == json.dumps(results.clean_json(meta)).encode()
    for name in ("coadds", "coverage", "flags"):
        np.testing.assert_array_equal(results.read_array(d, back, name), arrays[name])
    assert (d / "coadds.f32").stat().st_size == arrays["coadds"].size * 4
    with pytest.raises(NotFound, match="unknown array"):
        results.array_path(d, "exposures")


def test_rewrite_replaces_previous_result(tmp_path):
    meta, arrays = make_result()
    d = tmp_path / "abc123"
    results.write_result(d, meta, arrays)
    meta2, arrays2 = make_result(with_exposures=False)
    results.write_result(d, meta2, arrays2)
    assert not (d / "exposures.f16").exists()


def test_exposure_slicing(tmp_path):
    meta, arrays = make_result()
    d = tmp_path / "abc123"
    results.write_result(d, meta, arrays)
    meta = results.read_meta(d)
    data, idx = results.read_exposures(d, meta, 0, "D1")
    assert idx == [0, 1] and data.dtype == np.dtype("<f4") and data.shape == (2, 5, 5)
    np.testing.assert_array_equal(data, arrays["exposures"][0:2].astype(np.float16).astype(np.float32))
    data, idx = results.read_exposures(d, meta, 1, "D1")
    assert idx == [3]
    with pytest.raises(NotFound, match="no exposures"):
        results.read_exposures(d, meta, 1, "D6")
    with pytest.raises(NotFound, match="no exposures"):
        results.read_exposures(d, meta, 0, "D9")


def test_exposures_not_stored(tmp_path):
    meta, arrays = make_result(with_exposures=False)
    d = tmp_path / "abc123"
    results.write_result(d, meta, arrays)
    with pytest.raises(NotFound, match="not stored"):
        results.read_exposures(d, results.read_meta(d), 0, "D1")
