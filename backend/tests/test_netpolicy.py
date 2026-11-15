"""Interactive network policy: short timeouts, circuit breakers, no TAP for existing sequences."""

import json
import threading
import time
import urllib.request

import pytest

from spherex_core import cache, catalogs, netpolicy, services
from spherex_core.analysis import common
from spherex_core.archive import tap
from spherex_core.pipeline import results

from test_field import make_field


def test_timeouts_clamped_only_in_fast_mode():
    assert netpolicy.timeouts(300, 3) == (300, 3)
    with netpolicy.fast():
        assert netpolicy.timeouts(300, 3) == (netpolicy.FAST_TIMEOUT_S, netpolicy.FAST_ATTEMPTS)
        assert netpolicy.pause(8.0) == netpolicy.FAST_PAUSE_S


def test_fast_mode_follows_run_in_context_into_threads():
    seen = {}
    with netpolicy.fast():
        fn = netpolicy.run_in_context(lambda: seen.setdefault("fast", netpolicy.is_fast()))
    t = threading.Thread(target=fn)
    t.start()
    t.join()
    assert seen["fast"] is True and netpolicy.is_fast() is False


def test_breaker_skips_failed_service_in_fast_mode_only(monkeypatch):
    calls = []

    def boom(url, timeout):
        calls.append(timeout)
        raise TimeoutError("read timed out")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    monkeypatch.setattr(tap.time, "sleep", lambda s: None)
    with netpolicy.fast(), pytest.raises(RuntimeError):
        tap.tap_csv("SELECT 1", url="https://tap.example.org/sync", timeout=300, retries=3)
    assert calls == [netpolicy.FAST_TIMEOUT_S] * netpolicy.FAST_ATTEMPTS
    assert "tap.example.org" in netpolicy.status()
    t0 = time.monotonic()
    with netpolicy.fast(), pytest.raises(netpolicy.ServiceUnavailable):
        tap.tap_csv("SELECT 1", url="https://tap.example.org/sync")
    assert time.monotonic() - t0 < 0.1 and len(calls) == netpolicy.FAST_ATTEMPTS  # skipped, no new request
    with pytest.raises(RuntimeError):  # background work still tries (and records)
        tap.tap_csv("SELECT 1", url="https://tap.example.org/sync", timeout=1, retries=1)
    assert len(calls) == netpolicy.FAST_ATTEMPTS + 1


def test_catalog_serves_stale_cache_when_service_is_down(tmp_path, monkeypatch):
    services.configure(cache_root=tmp_path)
    try:
        path = cache.layout().catalog("gaia", 10.0, 20.0, 0.05)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps([{"id": "old"}]))
        old = time.time() - 30 * 86400
        import os

        os.utime(path, (old, old))  # well past max_age_hours

        def down(*a, **k):
            raise RuntimeError("TAP query failed after 2 attempts: timed out")

        monkeypatch.setattr(catalogs.gaia, "query", down)
        assert catalogs.cone("gaia", 10.0, 20.0, 0.05) == [{"id": "old"}]
    finally:
        services.configure(None)


def test_sequence_frames_never_queries_tap(tmp_path, monkeypatch):
    services.configure(cache_root=tmp_path)
    try:
        meta, arrays = make_field()
        meta["exposures"] = [{"index": 0, "epoch": 0, "band": "D3", "mjd": 60914.2, "wl": 2.0,
                              "collection": "spherex_dr1_deep", "obs_id": "2025W35_1A_0432_4",
                              "file": "level2_2025W35_1A_0432_4D3_spx_l2b-v27-2026-230.fits"},
                             {"index": 1, "epoch": 0, "band": "D1", "mjd": 60914.3, "wl": 0.9,
                              "collection": "spherex_qr3", "obs_id": "x", "file": "not-a-level2-name.fits"}]
        results.write_result(cache.layout().sequence("fld9"), meta, arrays)
        ctx = common.load_context("fld9")
        monkeypatch.setattr(tap, "tap_csv", lambda *a, **k: pytest.fail("analysis queried IRSA TAP"))
        frames = common.sequence_frames(ctx)
        assert frames[0].uri == ("ibe/data/spherex/dr1/level2/2025W35_1A/l2b-v27-2026-230/3/"
                                 "level2_2025W35_1A_0432_4D3_spx_l2b-v27-2026-230.fits")
        assert frames[0].detector == 3 and frames[1] is None
    finally:
        services.configure(None)
        common._FRAMES_MEMO.clear()
