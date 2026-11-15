"""Endpoint ordering and hedging, the file-catalogue mirror, catalogue and file fallbacks, popular fields."""

import asyncio
import json
import time

import pytest

from spherex_core import endpoints, netpolicy, popular, upstreams
from spherex_core.archive import mirror as mirror_mod
from spherex_core.archive import spherex as archive
from spherex_core.upstreams import Endpoint


@pytest.fixture(autouse=True)
def _fresh_stats():
    upstreams.reset()
    yield
    upstreams.reset()


def _eps(kind, *names):
    eps = [Endpoint(n, kind, f"{n}.test", priority=i) for i, n in enumerate(names)]
    upstreams.register(*eps)
    return eps


# ---------------------------------------------------------------- upstreams


def test_failures_and_latency_decide_the_order():
    a, b, c = _eps("t-order", "a", "b", "c")
    assert [e.name for e in upstreams.ordered("t-order")] == ["a", "b", "c"]  # static priority with no data
    for _ in range(5):
        upstreams.record(a, 2.0, True)
        upstreams.record(b, 0.1, True)
    upstreams.record(c, 0.05, False, "HTTP 502")
    assert [e.name for e in upstreams.ordered("t-order")] == ["b", "a", "c"]


def test_call_falls_through_to_the_next_source():
    a, b = _eps("t-fall", "dead", "alive")

    def ask(ep):
        if ep is a:
            raise RuntimeError("boom")
        return "ok"

    out, ep = upstreams.call("t-fall", ask, hedge=False)
    assert (out, ep.name) == ("ok", "alive")
    assert upstreams.ordered("t-fall")[0].name == "alive"  # the failure moved it down
    with pytest.raises(upstreams.AllFailed):
        upstreams.call("t-fall", lambda ep: (_ for _ in ()).throw(RuntimeError("x")), hedge=False)


def test_hedged_call_takes_the_faster_answer():
    slow, fast = _eps("t-hedge", "slow", "fast")
    for _ in range(5):
        upstreams.record(slow, 0.05, True)  # p90 ~0.05 s -> hedge after HEDGE_MIN_S

    def ask(ep):
        time.sleep(3.0 if ep is slow else 0.05)
        return ep.name

    t0 = time.perf_counter()
    out, ep = upstreams.call("t-hedge", ask, hedge=True)
    assert out == "fast" and time.perf_counter() - t0 < 2.0


def test_health_neutral_errors_are_not_failures():
    a, b = _eps("t-neutral", "mirror", "origin")

    class Missing(Exception):
        health_neutral = True

    def ask(ep):
        if ep is a:
            raise Missing("not in this mirror")
        return 1

    upstreams.call("t-neutral", ask, hedge=False)
    row = next(r for r in upstreams.status()["kinds"]["t-neutral"] if r["name"] == "mirror")
    assert row["samples"] == 0 and row["last_error"] is None  # "not here" is not a health failure


def test_hedged_async_call_cancels_the_loser():
    slow, fast = _eps("t-async", "slow", "fast")
    cancelled = []

    async def ask(ep):
        try:
            await asyncio.sleep(3.0 if ep is slow else 0.05)
        except asyncio.CancelledError:
            cancelled.append(ep.name)
            raise
        return ep.name

    async def go():
        return await upstreams.call_async("t-async", ask, hedge=True)

    out, ep = asyncio.run(go())
    assert out == "fast" and cancelled == ["slow"]


def test_fallback_and_last_resort_flags():
    a, b = _eps("t-resort", "a", "b")
    seen = []

    def fn(ep):
        seen.append((ep.name, upstreams.has_fallback(), upstreams.last_resort()))
        if ep is a:
            raise RuntimeError("down")
        return 1

    for hedge in (False, True):
        upstreams.reset()
        netpolicy.reset()  # a's failure opened its breaker
        seen.clear()
        upstreams.call("t-resort", fn, hedge=hedge)
        # a: quick single attempt (b can answer); b: the faster source failed, one longer attempt
        assert seen == [("a", True, False), ("b", False, True)]
    seen.clear()
    upstreams.call("t-resort", fn, hedge=False)
    assert seen == [("b", True, False)]  # a failed last time: b is asked first now


def test_mostly_failing():
    (a,) = _eps("t-mf", "cut")
    for _ in range(9):
        upstreams.record(a, 0.1, False, "busy", breaker=False)
    assert not upstreams.mostly_failing(a)  # not enough samples yet
    upstreams.record(a, 0.1, False, "busy", breaker=False)
    assert upstreams.mostly_failing(a)
    assert "cut.test" not in netpolicy.status()  # breaker=False left the breaker alone


# ---------------------------------------------------------------- mirror


def _quad(ra, dec, half=1.75):
    import math

    c = math.cos(math.radians(dec))
    return [ra - half / c, dec - half, ra + half / c, dec - half, ra + half / c, dec + half, ra - half / c, dec + half]


def _row(uri, release, band, mjd, corners, obs="obs1"):
    return {"uri": uri, "release": release, "collection": f"spherex_{release}", "obs_id": obs, "band": band,
            "mjd": mjd, "mjd_start": mjd - 0.001, "mjd_end": mjd + 0.001, "wl_min": 0.75, "wl_max": 1.1,
            "corners": corners, "source": "test"}


def test_mirror_point_queries(tmp_path):
    m = mirror_mod.Mirror(tmp_path / "m.sqlite")
    m.upsert([
        _row("ibe/data/spherex/dr1/level2/a.fits", "dr1", "D1", 60800, _quad(10.0, 20.0), "o1"),
        _row("ibe/data/spherex/dr1/level2/b.fits", "dr1", "D2", 60801, _quad(359.5, 0.0), "o2"),  # RA wrap
        _row("ibe/data/spherex/qr3/level2/c.fits", "qr3", "D1", 61200, _quad(10.5, 20.5), "o3"),
        _row("ibe/data/spherex/dr1/level2/p.fits", "dr1", "D3", 60802,
             [0.0, 88.0, 90.0, 88.0, 180.0, 88.0, 270.0, 88.0], "o4"),  # around the pole
    ])
    coll = ("spherex_dr1", "spherex_dr1_deep", "spherex_qr3", "spherex_qr3_deep")
    hits = m.query(10.2, 20.1, coll)
    assert [f.uri.rsplit("/", 1)[-1] for f in hits] == ["a.fits", "c.fits"]  # oldest first, both releases
    assert [f.uri.rsplit("/", 1)[-1] for f in m.query(0.4, 0.3, coll)] == ["b.fits"]
    assert [f.uri.rsplit("/", 1)[-1] for f in m.query(123.0, 89.5, coll)] == ["p.fits"]
    assert m.query(40.0, -30.0, coll) == []
    assert [f.uri.rsplit("/", 1)[-1] for f in m.query(10.2, 20.1, ("spherex_qr3",))] == ["c.fits"]


def test_mirror_readiness(tmp_path):
    m = mirror_mod.Mirror(tmp_path / "m.sqlite")
    coll = ("spherex_dr1", "spherex_qr3")
    assert not m.ready(coll) and not m.usable(coll)
    m.upsert([_row("ibe/data/spherex/dr1/x.fits", "dr1", "D1", 60800, _quad(1, 1)),
              _row("ibe/data/spherex/qr3/y.fits", "qr3", "D1", 61200, _quad(1, 1))])
    assert m.usable(coll) and not m.ready(coll)
    m.set_state(**{"complete:dr1": "1", "complete:qr3": "1", "synced_at": time.time()})
    assert m.ready(coll)
    m.set_state(synced_at=time.time() - 100 * 3600)
    assert not m.ready(coll) and m.usable(coll)


def test_query_frames_uses_the_mirror_and_survives_irsa_outage(tmp_path, monkeypatch):
    from spherex_core import cache

    cache.set_root(tmp_path)
    try:
        m = mirror_mod.Mirror(tmp_path / "m.sqlite")
        m.upsert([_row("ibe/data/spherex/dr1/level2/a.fits", "dr1", "D1", 60800, _quad(10.0, 20.0))])
        monkeypatch.setattr(mirror_mod, "get", lambda: m)
        calls = []

        def tap_down(*a, **k):
            calls.append(1)
            raise RuntimeError("HTTP 502 Proxy Error")

        monkeypatch.setattr(archive, "tap_csv", tap_down)
        from spherex_core.archive import sia

        monkeypatch.setattr(sia, "query_frames", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("SIA 502")))
        coll = ("spherex_dr1",)
        # incomplete mirror: IRSA is asked first, then the mirror answers as the last resort
        assert [f.uri for f in archive.query_frames(10.1, 20.1, coll, use_cache=False)] == \
            ["ibe/data/spherex/dr1/level2/a.fits"]
        assert calls
        # complete and fresh: the mirror answers alone, IRSA is not asked at all
        calls.clear()
        m.set_state(**{"complete:dr1": "1", "synced_at": time.time()})
        assert len(archive.query_frames(10.1, 20.1, coll, use_cache=False)) == 1 and not calls
        # no mirror and IRSA down: a clear error
        monkeypatch.setattr(mirror_mod, "get", lambda: None)
        with pytest.raises(RuntimeError, match="image search failed"):
            archive.query_frames(10.1, 20.1, coll, use_cache=False)
    finally:
        cache.set_root(None)


def test_row_from_header_gives_a_footprint():
    from astropy.io import fits
    from astropy.wcs import WCS

    w = WCS(naxis=2)
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    w.wcs.crval = [150.0, 2.0]
    w.wcs.crpix = [1020.5, 1020.5]
    w.wcs.cdelt = [-6.15 / 3600, 6.15 / 3600]
    img = w.to_header()
    img["XTENSION"] = "IMAGE"
    for k, v in {"NAXIS": 2, "NAXIS1": 2040, "NAXIS2": 2040, "DETECTOR": 4, "OBSID": "2026W01_1A_0001_1",
                 "MJD-BEG": 61000.0, "MJD-END": 61000.002}.items():
        img[k] = v
    buf = fits.PrimaryHDU().header.tostring().encode() + img.tostring().encode()
    key = "qr3/level2/2026W01_1A/v/4/level2_2026W01_1A_0001_1D4_spx_v.fits"
    row = mirror_mod.row_from_header(key, buf, "2026-01-01", 64_000_000, {"D4": (2.4, 3.8)})
    assert row["band"] == "D4" and row["release"] == "qr3" and row["obs_id"] == "2026W01_1A_0001_1"
    assert mirror_mod.contains(row["corners"], 150.0, 2.0) and not mirror_mod.contains(row["corners"], 155.0, 2.0)
    assert abs(row["mjd"] - 61000.001) < 1e-9


# ---------------------------------------------------------------- catalogue and file fallbacks


def test_gaia_falls_back_to_mast(monkeypatch):
    from spherex_core.catalogs import gaia
    from spherex_core.config import settings

    def tap(adql, url=None, **k):
        if url == settings.gaia_tap_url:
            raise RuntimeError("timed out")
        assert "dbo.gaia_source" in adql
        return [{"source_id": "1", "ra": "10", "dec": "20", "pmra": "1", "pmdec": "2", "parallax": "3",
                 "phot_g_mean_mag": "12", "bp_rp": "1"}]

    monkeypatch.setattr(gaia, "tap_csv", tap)
    rows = gaia.query(10, 20, 0.01)
    assert rows[0]["id"] == "Gaia DR3 1" and rows[0]["epoch"] == 2016.0
    assert upstreams.ordered("gaia")[0] is endpoints.GAIA_MAST  # ESA failed and moved down


def test_range_reads_fall_back_and_share_identical_requests(monkeypatch):
    from aiohttp import web
    from aiohttp.test_utils import TestServer

    from spherex_core.config import settings
    from spherex_core.io import http

    hits = {"s3": 0, "irsa": 0}
    payload = bytes(range(256)) * 16

    async def s3(request):
        hits["s3"] += 1
        return web.Response(status=503)

    async def irsa(request):
        hits["irsa"] += 1
        await asyncio.sleep(0.05)
        a, b = request.headers["Range"].removeprefix("bytes=").split("-")
        return web.Response(status=206, body=payload[int(a):int(b) + 1])

    async def go():
        app = web.Application()
        app.router.add_get("/s3/{name}", s3)
        app.router.add_get("/irsa/{name}", irsa)
        server = TestServer(app)
        await server.start_server()
        base = f"http://127.0.0.1:{server.port}"
        monkeypatch.setattr(endpoints, "file_url", lambda ep, url: {
            "aws-s3": f"{base}/s3/x.fits", "irsa-ibe": f"{base}/irsa/x.fits"}.get(ep.name))
        try:
            async with http.RangeClient(8, retries=2) as c:
                url = settings.s3_base + "dr1/level2/x.fits"
                a, b = await asyncio.gather(c.get(url, 10, 20), c.get(url, 10, 20))
                return a, b, c.hedges
        finally:
            await server.close()

    monkeypatch.setattr(http, "HEDGE_READS", False)
    a, b, hedges = asyncio.run(go())
    assert a == b == payload[10:30]
    assert hits["irsa"] == 1  # the two identical reads shared one request
    assert hedges == 1


# ---------------------------------------------------------------- popular fields


def test_request_counting_promotes_fields(tmp_path, monkeypatch):
    monkeypatch.setattr(popular, "_counts_path", lambda: tmp_path / "requests.json")
    monkeypatch.setattr(popular, "_redis", lambda: None)
    for _ in range(4):
        popular.count_request(10.0, 20.0, 6, "x", {"ra": 10.0, "dec": 20.0, "fov_arcmin": 6})
    assert popular.promoted(5) == []
    popular.count_request(10.001, 20.001, 6, "x", {"ra": 10.0, "dec": 20.0, "fov_arcmin": 6})  # same field
    assert [p["n"] for p in popular.promoted(5)] == [5]
    assert all("query" in e for e in popular.curated())


SKYBOT_ROWS = [  # trimmed from a real conesearch answer (-output=all, -loc=500)
    {"Num": 425642, "Name": "2010 VJ175", "RA (hms)": "09 59 58.9549", "DEC (dms)": "+09 59 47.765",
     "Class": "MB>Inner", "VMag (mag)": 20.5, "Err (arcsec)": 0.035, "dRA (arcsec/h)": 26.3277,
     "dDEC (arcsec/h)": -10.6682, "dg (ua)": 2.457},
    {"Num": "-", "Name": "C/2099 Z1", "RA (hms)": "10 00 01.0", "DEC (dms)": "-00 30 00.0",
     "Class": "Comet", "VMag (mag)": 18.0, "Err (arcsec)": 2.0, "dRA (arcsec/h)": 1.0, "dDEC (arcsec/h)": 2.0,
     "dg (ua)": 1.0},
    {"Num": 1, "Name": "faint", "RA (hms)": "10 00 00", "DEC (dms)": "+10 00 00", "VMag (mag)": 23.5},
]


def test_known_objects_fall_back_to_skybot(tmp_path, monkeypatch):
    from spherex_core import cache
    from spherex_core.field import known

    cache.set_root(tmp_path)
    try:
        calls = []

        def jpl_down(*a):
            calls.append("jpl")
            raise TimeoutError("timed out")

        monkeypatch.setattr(known, "_sb_ident", jpl_down)

        def sky(ra, dec, half, mjd, vmag_lim, timeout=60.0):
            calls.append("skybot")
            return known.parse_skybot(SKYBOT_ROWS, mjd, vmag_lim)

        monkeypatch.setattr(known, "skybot", sky)
        objs = known.query(150.0, 10.0, 0.1, 61000.0, vmag_lim=21.0)
        assert [o["name"] for o in objs] == ["425642 2010 VJ175", "C/2099 Z1"]  # V=23.5 dropped
        a, c = objs
        assert a["geocentric"] and a["kind"] == "asteroid" and c["kind"] == "comet"
        assert abs(a["ra"] - 149.99564) < 1e-4 and abs(a["dec"] - 9.99660) < 1e-4 and c["dec"] == -0.5
        assert abs(a["err_ra"] * 2 ** 0.5 - (0.035 + 9.7 / 2.457)) < 1e-6  # parallax added to the error
        assert known.query(150.0, 10.0, 0.1, 61000.0, vmag_lim=21.0) == objs  # SkyBoT answer cached ...
        assert calls == ["jpl", "skybot", "jpl"]  # ... apart: JPL is asked again first
        assert not list((tmp_path / "known_objects").glob("????????????????.json"))
        jpl = next(r for r in upstreams.status()["kinds"]["solar-system"] if r["name"] == "jpl")
        assert jpl["last_error"] == "timed out"  # JPL's failure is visible in /api/status/upstreams
    finally:
        cache.set_root(None)


def test_geocentric_positions_get_a_wider_motion_tolerance():
    from spherex_core.field import known

    # an object 2.4 AU away: measured from SPHEREx 18"/h at PA 100 deg, geocentric 30"/h at PA 90 deg (the
    # spacecraft's orbital motion adds up to ~15"/h at that distance)
    o = {"name": "x", "kind": "asteroid", "vmag": 18.0, "speed_arcsec_per_hr": 30.0, "pa_deg": 90.0,
         "ra": 10.0, "dec": 0.0, "mjd": 0.0, "ra_rate": 30.0, "dec_rate": 0.0, "err_ra": 3.0, "err_dec": 3.0}
    t = {"detections": [{"mjd": 0.0, "ra": 10.0, "dec": 0.0}, {"mjd": 0.01, "ra": 10.0 + 0.002, "dec": 0.0}],
         "rate_arcsec_per_hr": 18.0, "pa_deg": 100.0}
    assert known._rank(t, [o]) == []  # outside +-30 % for a SPHEREx-centric position
    m = known._rank(t, [{**o, "geocentric": True, "distance_au": 2.4}])
    assert m and m[0]["geocentric"]


def test_status_route_lists_sources():
    from fastapi.testclient import TestClient

    from spherex_api.app import create_app

    body = TestClient(create_app(web_dist=None)).get("/api/status/upstreams").json()
    kinds = body["kinds"]
    assert {"spherex-files", "spherex-meta", "gaia", "catwise", "2mass", "simbad", "solar-system"} <= set(kinds)
    assert any(r["note"] == "geocentric positions" for r in kinds["solar-system"])
    assert "breakers" in body and "mirror" in body
    json.dumps(body)


ASU_2MASS = """#
#   VizieR Astronomical Server vizier.cfa.harvard.edu
#Column	2MASS	(a17)	Source designation

2MASS	RAJ2000	DEJ2000	Jmag	Hmag	Kmag	JD
 	deg	deg	mag	mag	mag	d
-----------------	----------	----------	------	------	------	------------
13252770-4301071 	201.365452	-43.018658	11.463	10.297	 8.385	2450947.5814
13252741-4301089 	201.364224	-43.019146	12.112	 9.262	 8.527	2450947.5814

"""


def test_asu_tsv_and_box_helpers():
    from spherex_core.catalogs import common

    rows, units = common.parse_asu_tsv(ASU_2MASS)
    assert [r["2MASS"] for r in rows] == ["13252770-4301071", "13252741-4301089"] and rows[1]["Kmag"] == "8.527"
    assert units["RAJ2000"] == "deg"
    assert common.parse_asu_tsv("#only comments\n#Column\tx\n") == ([], {})  # an empty table has no header
    assert " OR " in common.box(359.99, 10.0, 0.05)  # RA wraps through 0
    pole = common.box(0.0, 89.97, 0.05)
    assert pole.startswith("dec BETWEEN") and "ra " not in pole
    assert common.in_circle(10, 20, 0.01, 10.005, 20.005) and not common.in_circle(10, 20, 0.01, 10.02, 20)


def test_2mass_falls_back_to_the_cfa_vizier_mirror(monkeypatch):
    from spherex_core.catalogs import common, twomass

    def tap(adql, url=None, **k):
        raise RuntimeError("HTTP Error 502: Proxy Error")

    monkeypatch.setattr(twomass, "tap_csv", tap)
    monkeypatch.setattr(twomass, "vizier_asu", lambda *a, **k: common.parse_asu_tsv(ASU_2MASS))
    rows = twomass.query(201.365, -43.019, 0.01)
    assert rows[0]["id"] == "2MASS J13252770-4301071" and rows[0]["mag"] == 8.385
    assert abs(rows[0]["epoch"] - 1998.36) < 0.01
    assert upstreams.ordered("2mass")[0] is endpoints.TWOMASS_CFA
