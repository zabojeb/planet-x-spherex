"""MCP tool layer, offline: a synthetic sequence in a temporary cache, called through the real MCP server."""

import asyncio
import json
import struct
import zlib

import numpy as np
import pytest

pytest.importorskip("mcp")

from spherex_core import cache, services  # noqa: E402
from spherex_core.pipeline import results  # noqa: E402
from spherex_mcp import build_server, images, tools  # noqa: E402
from test_results import make_result  # noqa: E402


@pytest.fixture()
def seq(tmp_path):
    services.configure(cache_root=tmp_path)
    meta, arrays = make_result(n_ep=3)
    meta.update({
        "target": {"name": "test", "ra": 10.0, "dec": 20.0, "pmra": 0.0, "pmdec": 0.0, "ref_epoch": 2000.0,
                   "track": [{"epoch": e, "x": 2.0, "y": 2.0} for e in range(3)]},
        "grid": {"ra": 10.0, "dec": 20.0, "n": 5, "pixscale_arcsec": 6.15, "fov_arcmin": 0.5},
        "bands": [{"id": f"D{i}", "wl_min": 0.7 + i, "wl_max": 1.2 + i} for i in range(1, 7)],
        "epochs": [{"index": e, "n": 4, "date_min": f"2025-0{e + 1}-01 00:00", "date_max": f"2025-0{e + 1}-02 00:00",
                    "bands": {"D1": {"n": 2}}} for e in range(3)],
        "photometry": [{"band": "D1", "flux_mjy": 1.0, "flagged": False}],
        "stats": {"frames_used": 12},
    })
    results.write_result(cache.layout().sequence("abc123"), meta, arrays)
    yield "abc123", meta, arrays
    services.configure(None)


def run(coro):
    return asyncio.run(coro)


def _png_size(data: bytes) -> tuple[int, int]:
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    w, h = struct.unpack(">II", data[16:24])
    return w, h


def test_server_lists_all_tools_resource_and_prompt():
    s = build_server()
    names = {t.name for t in run(s.list_tools())}
    for n in ("resolve_target", "search_observations", "build_sequence", "sequence_status", "describe_sequence",
              "get_image", "get_blink_strip", "analyze_point", "analyze_field", "job_result", "known_objects",
              "track_and_stack_search", "track_and_stack_image", "catalog_cone", "spectrum_classify",
              "archival_epochs", "moving_object_search", "export_report", "cancel_job", "list_presets"):
        assert n in names
    assert any(p.name == "discovery_report" for p in run(s.list_prompts()))
    assert any(str(r.uri) == "spherex://overview" for r in run(s.list_resources()))


def test_png_encoder_roundtrip():
    img = (np.arange(12, dtype=np.uint8).reshape(3, 4) * 20)
    data = images.encode_png(img)
    assert _png_size(data) == (4, 3)
    # the IDAT payload decompresses to filter byte + row bytes
    idat = data[data.index(b"IDAT") + 4:data.index(b"IEND") - 8]
    raw = zlib.decompress(idat)
    assert len(raw) == 3 * (1 + 4) and raw[1:5] == img[0].tobytes()


def test_describe_and_images(seq):
    sid, meta, _ = seq
    d = tools.describe_sequence(sid)
    assert d["sequence_id"] == sid and len(d["epochs"]) == len(meta["epochs"])
    for mode in ("coadd", "difference", "rgb"):
        png, info = tools.get_image(sid, epoch=-1, band="ALL" if mode != "coadd" else "D1", mode=mode)
        w, h = _png_size(png)
        assert w == h >= 256 and info["mode"] == mode
    png, info = tools.get_blink_strip(sid, band="ALL")
    w, h = _png_size(png)
    assert w > h and len(info["epochs"]) == len(meta["epochs"])


def test_image_tool_returns_image_content(seq):
    sid, _, _ = seq
    r = run(build_server().call_tool("get_blink_strip", {"sequence_id": sid}))
    kinds = [c.type for c in r.content]
    assert "image" in kinds and "text" in kinds


def test_errors_are_readable(seq):
    with pytest.raises(tools.ToolError, match="not built"):
        tools.describe_sequence("nope")
    with pytest.raises(tools.ToolError, match="band must be"):
        tools.get_image("abc123", band="D9")
    # the SDK turns this exception into an isError result with the message for a remote client
    with pytest.raises(Exception, match="not built"):
        run(build_server().call_tool("describe_sequence", {"sequence_id": "nope"}))


def test_build_limits():
    lim = tools.Limits(max_active_builds=1, builds_per_hour=2)
    lim.check_build(0)
    with pytest.raises(tools.ToolError, match="already running"):
        lim.check_build(1)
    lim.check_build(0)
    with pytest.raises(tools.ToolError, match="Hourly"):
        lim.check_build(0)


def test_build_validates_input():
    with pytest.raises(tools.ToolError, match="fov_arcmin"):
        run(tools.build_sequence(10.0, 20.0, fov_arcmin=90))
    with pytest.raises(tools.ToolError, match="ra must be"):
        run(tools.build_sequence(400.0, 20.0))


def test_compact_shrinks_payloads():
    big = {"a": 1.23456789, "arr": list(range(100)), "nested": [{"x": float("nan")}] * 10}
    c = tools.compact(big)
    assert c["a"] == 1.235 and c["arr"].startswith("<100") and c["nested"][-1] == "... 4 more"
    assert c["nested"][0]["x"] is None
    json.dumps(c)
    pos = tools.compact({"ra": 331.0441466, "dec": -56.7828442, "mjd": 60914.2131964, "pm_total": 4684.165})
    assert pos == {"ra": 331.0441466, "dec": -56.7828442, "mjd": 60914.2131964, "pm_total": 4684.0}


def test_http_endpoint_mounted(seq):
    from fastapi.testclient import TestClient

    from spherex_api.app import create_app

    with TestClient(create_app(web_dist=None)) as c:
        r = c.post("/mcp", headers={"accept": "application/json, text/event-stream"},
                   json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                         "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                                    "clientInfo": {"name": "t", "version": "1"}}})
        assert r.status_code == 200 and "spherex" in r.text.lower()
