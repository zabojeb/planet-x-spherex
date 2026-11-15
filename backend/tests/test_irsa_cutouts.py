"""Cutouts from IRSA's cutout service (ADR-0038), offline: a fake service cuts the synthetic Level 2 file of
test_level2_cache the way IRSA does (every extension, CRPIX shifted, detector offset in WCS "A", the EPSF table
appended unmodified), so the parser, the reader's fallback to S3 and the streaming read are checked."""

import asyncio
import io

import numpy as np
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from astropy.io import fits

import spherex_core.io.irsa as irsa_mod
from spherex_core.io import IrsaCutouts, IrsaUnavailable, Level2Reader
from spherex_core.io.irsa import needed_bytes, parse_cutout
from test_level2_cache import DATA, NX, NY, RA0, DEC0, TRUTH, URL, WCS0, FakeS3, assert_matches, sky

IRSA_URL = "https://irsa.example.invalid/ibe/data/spherex/level2_test_2D2.fits"
EPSF_ROWS = 400  # a big table after the cut extensions, as in the real cutouts (~4 MB there)


@pytest.fixture(autouse=True)
def small_detector(monkeypatch):
    monkeypatch.setattr(irsa_mod, "DETECTOR_NPIX", NX)  # the synthetic file is 240 px, real detectors 2040


def irsa_cutout(ra: float, dec: float, size: int, shift: int = 0) -> bytes:
    """What the service returns: all extensions cut to size x size px around (ra, dec), clipped to the detector.
    shift moves the window by a pixel to mimic a service that rounds the centre differently."""
    with fits.open(io.BytesIO(DATA)) as f:
        full = f["IMAGE"].header
    x, y = WCS0.all_world2pix(ra, dec, 0)
    xc, yc = int(np.floor(x + 0.5)) + shift, int(np.floor(y + 0.5)) + shift
    h = size // 2
    x0, x1, y0, y1 = max(xc - h, 0), min(xc - h + size, NX), max(yc - h, 0), min(yc - h + size, NY)
    sl = np.s_[y0:y1, x0:x1]
    hdr = full.copy()
    hdr["CRPIX1"] -= x0
    hdr["CRPIX2"] -= y0
    hdr["WCSAXESA"] = 2
    hdr["CTYPE1A"], hdr["CTYPE2A"] = "LINEAR", "LINEAR"
    hdr["CRPIX1A"], hdr["CRPIX2A"] = 1.0 - x0, 1.0 - y0
    hdr["CRVAL1A"], hdr["CRVAL2A"] = 0.0, 0.0
    hdr["CDELT1A"], hdr["CDELT2A"] = 1.0, 1.0
    fh = fits.Header()
    fh["MSKN00"], fh["MSKD00"] = "TRANSIENT", "transient detected"
    fh["MSKN01"], fh["MSKD01"] = "OVERFLOW", "overflow"
    epsf = fits.BinTableHDU.from_columns([fits.Column("EPSF", "1089D", array=np.ones((EPSF_ROWS, 1089)))], name="EPSF")
    hdus = fits.HDUList([
        fits.PrimaryHDU(),
        fits.ImageHDU(TRUTH["image"][sl], header=hdr, name="IMAGE"),
        fits.ImageHDU(TRUTH["flags"][sl], header=fh, name="FLAGS"),
        fits.ImageHDU(TRUTH["variance"][sl], name="VARIANCE"),
        fits.ImageHDU(np.zeros((y1 - y0, x1 - x0), np.float32), name="ZODI"),
        epsf,
    ])
    buf = io.BytesIO()
    hdus.writeto(buf)
    return buf.getvalue()


class FakeIrsa:
    """The IrsaCutouts interface over irsa_cutout(); busy=True answers like an overloaded service."""

    def __init__(self, busy: bool = False, slots: int = 4, shift: int = 0):
        self.busy, self.slots, self.shift, self.in_flight = busy, slots, shift, 0
        self.calls: list[tuple[str, int]] = []
        self.failed = 0

    def try_slot(self) -> bool:
        if self.in_flight >= self.slots:
            return False
        self.in_flight += 1
        return True

    async def fetch(self, url, ra, dec, size, want_flag_defs=False):
        try:
            self.calls.append((url, size))
            if self.busy:
                raise IrsaUnavailable("HTTP 503")
            buf = irsa_cutout(ra, dec, size, self.shift)
            return parse_cutout(buf[:needed_bytes(buf)], want_flag_defs)
        finally:
            self.in_flight -= 1


def read(cache, s3, irsa, ra=RA0, dec=DEC0, half=20, **kw):
    reader = Level2Reader(s3, cache, irsa=irsa)
    return asyncio.run(reader.cutout(URL, ra, dec, half, irsa_url=IRSA_URL, **kw)), reader


def test_parse_restores_the_detector_frame_header_and_pixels():
    buf = irsa_cutout(RA0, DEC0, 45)
    n = needed_bytes(buf)
    assert n is not None and n < len(buf) // 4  # the EPSF table is never needed
    assert parse_cutout(buf[:n - 1]) is None  # incomplete VARIANCE data
    cut = parse_cutout(buf[:n], want_flag_defs=True)
    s3_cut = asyncio.run(Level2Reader(FakeS3(), None).cutout(URL, RA0, DEC0, 22))
    assert cut.header.tostring() == s3_cut.header.tostring()
    assert (cut.x0, cut.y0) == (s3_cut.x0, s3_cut.y0)
    sl = np.s_[cut.y0:cut.y0 + 45, cut.x0:cut.x0 + 45]
    np.testing.assert_array_equal(cut.image, TRUTH["image"][sl])
    np.testing.assert_array_equal(cut.flags, TRUTH["flags"][sl])
    np.testing.assert_array_equal(cut.variance, TRUTH["variance"][sl])
    assert cut.flag_defs[1][0] == "OVERFLOW"


def test_irsa_first_then_the_cache(tmp_path):
    s3, irsa = FakeS3(), FakeIrsa()
    cut, reader = read(tmp_path, s3, irsa, half=20)
    assert_matches(cut, half=20)
    assert s3.calls == [] and irsa.calls == [(IRSA_URL, 45)]  # 2 * (20 + margin 2) + 1 px, one request
    ref = asyncio.run(Level2Reader(FakeS3(), None).cutout(URL, RA0, DEC0, 20))
    assert (cut.x0, cut.y0) == (ref.x0, ref.y0) and cut.header.tostring() == ref.header.tostring()
    cut, reader = read(tmp_path, s3, irsa, *sky(1, -1), half=15)  # smaller window inside: no request at all
    assert_matches(cut, half=15)
    assert len(irsa.calls) == 1 and s3.calls == [] and reader.cache_hits == 1
    cut, _ = read(tmp_path, s3, irsa, half=35)  # wider than what IRSA sent: asked again, still no S3
    assert_matches(cut, half=35)
    assert irsa.calls[-1] == (IRSA_URL, 75) and s3.calls == []


def test_busy_service_falls_back_to_s3(tmp_path):
    s3, irsa = FakeS3(), FakeIrsa(busy=True)
    cut, _ = read(tmp_path, s3, irsa, half=20)
    assert_matches(cut, half=20)
    assert len(irsa.calls) == 1 and len(s3.calls) > 0 and irsa.in_flight == 0


def test_no_free_slot_reads_from_s3(tmp_path):
    s3, irsa = FakeS3(), FakeIrsa(slots=0)
    cut, _ = read(tmp_path, s3, irsa, half=20)
    assert_matches(cut, half=20)
    assert irsa.calls == [] and len(s3.calls) > 0


def test_window_outside_what_irsa_cut_falls_back_to_s3(tmp_path):
    s3, irsa = FakeS3(), FakeIrsa(shift=5)  # the service centred 5 px off: more than the margin
    cut, _ = read(tmp_path, s3, irsa, half=20)
    assert_matches(cut, half=20)
    assert len(irsa.calls) == 1 and len(s3.calls) > 0


def test_edge_of_the_detector(tmp_path):
    ra, dec = sky(NX / 2 - 8, -(NY / 2 - 10))  # 8 px from the right edge, 10 px from the bottom
    s3, irsa = FakeS3(), FakeIrsa()
    cut, _ = read(tmp_path, s3, irsa, ra, dec, half=20)
    ref = asyncio.run(Level2Reader(FakeS3(), None).cutout(URL, ra, dec, 20))
    assert s3.calls == [] and (cut.x0, cut.y0) == (ref.x0, ref.y0)
    np.testing.assert_array_equal(cut.image, ref.image)
    np.testing.assert_array_equal(cut.flags, ref.flags)


def test_stream_is_closed_after_the_variance():
    """The real client against a local HTTP server: it reads only the first part of the answer."""
    body = irsa_cutout(RA0, DEC0, 45)
    sent = {}

    async def handler(request):
        assert request.query["size"] == "45pix" and request.query["center"].endswith("deg")
        if request.match_info["name"] == "busy.fits":
            return web.Response(status=503, text="Server busy, retry later.")
        resp = web.StreamResponse(headers={"Content-Type": "application/fits"})
        await resp.prepare(request)
        try:
            for i in range(0, len(body), 8192):
                await resp.write(body[i:i + 8192])
                sent["n"] = i + 8192
                await asyncio.sleep(0)
        except (ConnectionResetError, ConnectionError):
            pass
        return resp

    async def go():
        app = web.Application()
        app.router.add_get("/{name}", handler)
        async with TestServer(app) as server, IrsaCutouts(max_in_flight=2, timeout=10) as client:
            assert client.try_slot()
            cut = await client.fetch(str(server.make_url("/f.fits")), RA0, DEC0, 45)
            assert client.in_flight == 0
            assert client.try_slot()
            with pytest.raises(IrsaUnavailable):
                await client.fetch(str(server.make_url("/busy.fits")), RA0, DEC0, 45)
            return cut, client.stats()

    cut, stats = asyncio.run(go())
    assert (cut.x0 + 22, cut.y0 + 22) == tuple(int(np.floor(v + 0.5)) for v in WCS0.all_world2pix(RA0, DEC0, 0))
    assert stats["served"] == 1 and stats["busy"] == 1
    assert stats["bytes"] < len(body) // 2
