"""A whole sequence build offline (ADR-0039): synthetic Level 2 files on a local HTTP server that honours Range
requests (standing in for S3) and cuts like IRSA's cutout service. Checks that batches in pool workers, batches
in the event loop and IRSA cutouts all give the same result, that the opening epoch is read first, and that
previews are announced and cleaned up."""

import asyncio
import dataclasses
import io
import json

import numpy as np
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from astropy.io import fits
from astropy.wcs import WCS

import spherex_core.archive.spherex as archive_mod
import spherex_core.config as config_mod
import spherex_core.io.irsa as irsa_mod
import spherex_core.pipeline.sequence as seq_mod
from spherex_core import cache
from spherex_core.archive import Frame
from spherex_core.pipeline import SequenceParams
from spherex_core.pipeline.results import is_complete

NPIX = 128
RA0, DEC0 = 210.0, 30.0
PIX = 6.15 / 3600
EPOCHS = (61000.0, 61180.0, 61360.0)  # three visits half a year apart
PER = 8  # exposures per detector and visit


def level2_file(i: int, det: int, mjd: float, newer: bool = False) -> tuple[bytes, dict]:
    """newer: the FLAGS header defines one more bit, as files of a later pipeline version do."""
    rng = np.random.default_rng(i)
    w = WCS(naxis=2)
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    w.wcs.crval = [RA0 + rng.normal(0, 1e-3), DEC0 + rng.normal(0, 1e-3)]
    w.wcs.crpix = [NPIX / 2 + 0.5, NPIX / 2 + 0.5]
    th = np.radians(rng.uniform(0, 360))
    w.wcs.cd = PIX * np.array([[-np.cos(th), np.sin(th)], [np.sin(th), np.cos(th)]])
    hdr = w.to_header()
    hdr["DETECTOR"] = det
    hdr["MJD-AVG"] = mjd
    hdr["PSF_FWHM"] = 2.0 * PIX
    yy, xx = np.mgrid[:NPIX, :NPIX]
    star = 50 * np.exp(-((xx - NPIX / 2 + 0.5) ** 2 + (yy - NPIX / 2 + 0.5) ** 2) / 4.0)
    arrays = {
        "image": (rng.normal(1.0, 0.05, (NPIX, NPIX)) + star).astype(np.float32),
        "flags": np.where(rng.random((NPIX, NPIX)) < 0.02, 1 << 5, 0).astype(np.int32),
        "variance": rng.uniform(0.002, 0.003, (NPIX, NPIX)).astype(np.float32),
    }
    fh = fits.Header()
    fh["MSKN05"], fh["MSKD05"] = "PERSIST", "persistence"
    if newer:
        fh["MSKN29"], fh["MSKD29"] = "SATELLITE_HALO", "satellite streak halo"
    arrays["flags_header"] = fh
    hdus = fits.HDUList([
        fits.PrimaryHDU(),
        fits.ImageHDU(arrays["image"], header=hdr, name="IMAGE"),
        fits.CompImageHDU(arrays["flags"], header=fh, name="FLAGS", compression_type="RICE_1", tile_shape=(1, NPIX)),
        fits.ImageHDU(arrays["variance"], name="VARIANCE"),
    ])
    buf = io.BytesIO()
    hdus.writeto(buf)
    arrays["header"] = hdr
    return buf.getvalue(), arrays


def make_frames():
    frames, files = [], {}
    i = 0
    for e, t in enumerate(EPOCHS):
        for det in (1, 2):
            for k in range(PER):
                mjd = t + k * 0.3 + det * 0.01
                name = f"level2_2026W{e:02d}_{k:04d}_1D{det}_spx_test.fits"
                uri = f"ibe/data/spherex/test/{name}"
                files[uri.removeprefix("ibe/data/spherex/")] = level2_file(i, det, mjd, newer=e == 2)
                frames.append(Frame(uri=uri, band=f"D{det}", mjd=mjd, mjd_start=mjd - 0.001, mjd_end=mjd + 0.001,
                                    collection="spherex_test", obs_id=f"obs{e}_{k}", wl_min_um=0.75 + det * 0.4,
                                    wl_max_um=1.1 + det * 0.4))
                i += 1
    return frames, files


FRAMES, FILES = make_frames()


def irsa_answer(tail: str, ra: float, dec: float, size: int) -> bytes:
    data, arr = FILES[tail]
    full = arr["header"]
    x, y = WCS(full).all_world2pix(ra, dec, 0)
    xc, yc = int(np.floor(x + 0.5)), int(np.floor(y + 0.5))
    h = size // 2
    x0, x1, y0, y1 = max(xc - h, 0), min(xc - h + size, NPIX), max(yc - h, 0), min(yc - h + size, NPIX)
    sl = np.s_[y0:y1, x0:x1]
    hdr = full.copy()
    hdr["CRPIX1"] -= x0
    hdr["CRPIX2"] -= y0
    for k, v in (("CTYPE1A", "LINEAR"), ("CTYPE2A", "LINEAR"), ("CRPIX1A", 1.0 - x0), ("CRPIX2A", 1.0 - y0),
                 ("CRVAL1A", 0.0), ("CRVAL2A", 0.0), ("CDELT1A", 1.0), ("CDELT2A", 1.0)):
        hdr[k] = v
    buf = io.BytesIO()
    fits.HDUList([fits.PrimaryHDU(), fits.ImageHDU(arr["image"][sl], header=hdr, name="IMAGE"),
                  fits.ImageHDU(arr["flags"][sl], header=arr["flags_header"].copy(), name="FLAGS"),
                  fits.ImageHDU(arr["variance"][sl], name="VARIANCE"),
                  fits.BinTableHDU.from_columns([fits.Column("EPSF", "100D", array=np.ones((300, 100)))],
                                                name="EPSF")]).writeto(buf)
    return buf.getvalue()


def seed_tables(root):
    """Wavelength maps and ePSF tables on disk, as after a first build (the synthetic files have none)."""
    os_ = 5
    yy, xx = np.mgrid[:33, :33]
    psf = np.exp(-((xx - 16) ** 2 + (yy - 16) ** 2) / (2 * (0.85 * os_) ** 2))
    psf = (psf / psf.sum()).astype(np.float32)[None].repeat(4, axis=0)
    for det in (1, 2):
        lay = cache.CacheLayout(root)
        lay.wave(det).parent.mkdir(parents=True, exist_ok=True)
        lay.epsf(det).parent.mkdir(parents=True, exist_ok=True)
        x = y = np.array([1.0, NPIX])
        vals = np.zeros((2, 2, 2))
        vals[..., 0], vals[..., 1] = 0.75 + det * 0.4, 0.02
        np.savez(lay.wave(det), x=x, y=y, values=vals)
        np.savez(lay.epsf(det), xcenter=np.array([32.0, 96, 32, 96]), ycenter=np.array([32.0, 32, 96, 96]), psf=psf,
                 oversampling=os_, cwave=np.full(4, 1.0), neff=np.full(4, 10.0))


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A cache seeded with tables, the archive answering FRAMES, and a local server for S3 and IRSA."""
    seed_tables(tmp_path)
    monkeypatch.setattr(seq_mod, "query_frames", lambda ra, dec, coll=None: list(FRAMES))
    monkeypatch.setattr(irsa_mod, "DETECTOR_NPIX", NPIX)
    cache.set_root(tmp_path)
    seen = {"s3": 0, "irsa": 0, "irsa_open": 0, "irsa_peak": 0}

    async def s3(request):
        data = FILES[request.match_info["tail"]][0]
        a, b = request.headers["Range"].removeprefix("bytes=").split("-")
        seen["s3"] += 1
        return web.Response(body=data[int(a):int(b) + 1], status=206)

    async def irsa(request):
        q = request.query
        ra, dec = (float(v) for v in q["center"].removesuffix("deg").split(","))
        seen["irsa"] += 1
        seen["irsa_open"] += 1
        seen["irsa_peak"] = max(seen["irsa_peak"], seen["irsa_open"])
        try:
            await asyncio.sleep(0.05)  # slower than S3, as the real service
            return web.Response(body=irsa_answer(request.match_info["tail"], ra, dec,
                                                 int(q["size"].removesuffix("pix"))))
        finally:
            seen["irsa_open"] -= 1

    def app():
        a = web.Application()
        a.router.add_get("/s3/{tail:.*}", s3)
        a.router.add_get("/ibe/data/spherex/{tail:.*}", irsa)
        return a

    yield {"app": app, "seen": seen, "root": tmp_path, "monkeypatch": monkeypatch}
    cache.set_root(None)
    seq_mod.reset_pool()


def build(world, name, **overrides):
    """Run one build with settings overridden (also in spawned workers, through the environment)."""
    mp = world["monkeypatch"]

    async def go():
        async with TestServer(world["app"]()) as server:
            base = str(server.make_url("/"))
            s = dataclasses.replace(config_mod.settings, s3_base=base + "s3/", irsa_base=base, **overrides)
            for mod in (config_mod, seq_mod, archive_mod):
                mp.setattr(mod, "settings", s)
            mp.setenv("SPHEREX_S3_BASE", base + "s3/")
            mp.setenv("SPHEREX_IRSA_BASE", base)
            mp.setenv("SPHEREX_CUTOUT_SOURCE", s.cutout_source)
            seq_mod.reset_pool()
            events = []
            p = SequenceParams(ra=RA0, dec=DEC0, fov_arcmin=2.0)
            out = world["root"] / "out" / name
            def progress(st, d, t):
                if st.startswith("preview:"):  # the preview is a complete result when announced
                    pm = json.loads((cache.layout().sequence(st[8:]) / "meta.json").read_text())
                    assert pm["id"] == st[8:] and pm["preview"]["frames"] < len(FRAMES)
                events.append((st, d, t))

            meta = await seq_mod.build_sequence(p, out, progress=progress)
            return meta, out, events

    return asyncio.run(go())


def same_result(a, b):
    ma, mb = (json.loads((d / "meta.json").read_text()) for d in (a, b))
    for m in (ma, mb):
        m.pop("stats", None)
        m.pop("created", None)
    assert ma == mb
    for f in sorted(p.name for p in a.iterdir() if not p.name.startswith("meta.json")):
        assert (a / f).read_bytes() == (b / f).read_bytes(), f


def test_batches_in_workers_match_the_event_loop_and_irsa(world):
    inline, out_a, ev = build(world, "inline", fetch_workers=False, cutout_source="s3", cpu_workers=2)
    assert inline["stats"]["frames_used"] == len(FRAMES)
    # bits defined by any file, whichever was read first (only the last visit's files define bit 29)
    assert {b["bit"]: b["name"] for b in inline["flag_bits"]}.items() >= {5: "PERSIST", 29: "SATELLITE_HALO"}.items()
    previews = [st.split(":", 1)[1] for st, _, _ in ev if st.startswith("preview:")]
    assert previews and all(p.startswith(inline["id"] + "p") for p in previews)
    assert not any(is_complete(cache.layout().sequence(p)) for p in previews)  # removed with the full result
    fetch = [d for st, d, _ in ev if st == "fetch"]
    assert fetch == sorted(fetch) and fetch[-1] == len(FRAMES)

    s3_requests = world["seen"]["s3"]
    for d in ("chunks",):  # a cold cache again for the next builds
        import shutil
        shutil.rmtree(world["root"] / d, ignore_errors=True)
    pooled, out_b, _ = build(world, "pool", fetch_workers=True, cutout_source="s3", cpu_workers=2)
    same_result(out_a, out_b)
    assert world["seen"]["s3"] > s3_requests and world["seen"]["irsa"] == 0

    import shutil
    shutil.rmtree(world["root"] / "chunks", ignore_errors=True)
    s3_before = world["seen"]["s3"]
    viairsa, out_c, _ = build(world, "irsa", fetch_workers=False, cutout_source="irsa", cpu_workers=2,
                              irsa_max_in_flight=64)
    same_result(out_a, out_c)
    assert world["seen"]["irsa"] == len(FRAMES) and world["seen"]["s3"] == s3_before
    assert viairsa["stats"]["irsa"]["served"] == len(FRAMES)


def test_opening_epoch_is_read_first_and_previewed(world):
    seen_order = []
    real = seq_mod.read_batch

    async def spy(spec, items, **kw):
        seen_order.extend(i for i, _ in items)
        return await real(spec, items, **kw)

    world["monkeypatch"].setattr(seq_mod, "read_batch", spy)
    meta, out, ev = build(world, "order", fetch_workers=False, cutout_source="s3", cpu_workers=2,
                          max_frames_in_flight=2)
    first = {FRAMES[i].obs_id.split("_")[0] for i in seen_order[:2 * PER]}
    assert first == {"obs0"}  # the first full visit before any other
    pid = next(st.split(":", 1)[1] for st, _, _ in ev if st.startswith("preview:"))
    assert pid == meta["id"] + "p1"


def test_pool_workers_share_the_irsa_slots(world):
    """Workers are separate processes; the pool's semaphore still keeps the build within irsa_max_in_flight
    requests, IRSA serves exposures throughout the build, S3 the rest, and the result is the same."""
    ref, out_a, _ = build(world, "ref", fetch_workers=False, cutout_source="s3", cpu_workers=2)
    import shutil
    shutil.rmtree(world["root"] / "chunks", ignore_errors=True)
    meta, out_b, _ = build(world, "pool_irsa", fetch_workers=True, cutout_source="irsa", cpu_workers=3,
                           irsa_max_in_flight=2, max_frames_in_flight=6)
    same_result(out_a, out_b)
    irsa = meta["stats"]["irsa"]
    assert 0 < irsa["served"] < len(FRAMES) and irsa["served"] == world["seen"]["irsa"]
    assert world["seen"]["irsa_peak"] <= 2
    assert irsa["served"] > 2  # slots freed by earlier exposures were taken again by later ones
