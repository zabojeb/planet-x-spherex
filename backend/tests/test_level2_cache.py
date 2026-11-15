"""The Level 2 reader on a synthetic file served from memory: values, the per-file chunk cache, row deltas and
the VARIANCE window (offline)."""

import asyncio
import io

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS

from spherex_core.cache import CacheLayout
from spherex_core.io import Level2Reader

URL = "https://example.invalid/level2/level2_test_2D2.fits"
NX = NY = 240
ROW = NX * 4
RA0, DEC0 = 120.0, -10.0
PIX = 6.15 / 3600


def make_file(seed: int = 0):
    """A small Level 2 look-alike: PRIMARY, IMAGE (rotated TAN WCS), FLAGS (RICE_1, one tile per row), VARIANCE."""
    rng = np.random.default_rng(seed)
    w = WCS(naxis=2)
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    w.wcs.crval = [RA0, DEC0]
    w.wcs.crpix = [NX / 2 + 0.5, NY / 2 + 0.5]
    th = np.radians(30.0)
    w.wcs.cd = PIX * np.array([[-np.cos(th), np.sin(th)], [np.sin(th), np.cos(th)]])
    hdr = w.to_header()
    hdr["DETECTOR"] = 2
    hdr["MJD-AVG"] = 60900.0
    truth = {
        "image": rng.normal(1.0, 0.1, (NY, NX)).astype(np.float32),
        "flags": np.where(rng.random((NY, NX)) < 0.05, rng.integers(1, 1 << 20, (NY, NX)), 0).astype(np.int32),
        "variance": rng.uniform(0.01, 0.02, (NY, NX)).astype(np.float32),
    }
    hdus = fits.HDUList([
        fits.PrimaryHDU(),
        fits.ImageHDU(truth["image"], header=hdr, name="IMAGE"),
        fits.CompImageHDU(truth["flags"], name="FLAGS", compression_type="RICE_1", tile_shape=(1, NX)),
        fits.ImageHDU(truth["variance"], name="VARIANCE"),
    ])
    buf = io.BytesIO()
    hdus.writeto(buf)
    return buf.getvalue(), truth, w


DATA, TRUTH, WCS0 = make_file()


class FakeS3:
    """Serves byte ranges of one file and records every request."""

    def __init__(self, data: bytes = DATA):
        self.data, self.calls = data, []

    async def get(self, url: str, start: int, length: int) -> bytes:
        if length <= 0:
            return b""
        self.calls.append((start, length))
        return self.data[start:start + length]


def sky(dx: float, dy: float) -> tuple[float, float]:
    """Sky position dx, dy native pixels from the detector centre."""
    ra, dec = WCS0.all_pix2world(NX / 2 - 0.5 + dx, NY / 2 - 0.5 + dy, 0)
    return float(ra), float(dec)


def cutout(cache, s3, ra=RA0, dec=DEC0, half=20, **kw):
    reader = Level2Reader(s3, cache)
    return asyncio.run(reader.cutout(URL, ra, dec, half, **kw)), reader


def assert_matches(cut, half=None, variance=True):
    h, w = cut.image.shape
    if half is not None:
        assert (h, w) == (2 * half + 1, 2 * half + 1)
    sl = np.s_[cut.y0:cut.y0 + h, cut.x0:cut.x0 + w]
    np.testing.assert_array_equal(cut.image, TRUTH["image"][sl])
    np.testing.assert_array_equal(cut.flags, TRUTH["flags"][sl])
    if variance:
        np.testing.assert_array_equal(cut.variance, TRUTH["variance"][sl])


def test_first_read_matches_the_file(tmp_path):
    s3 = FakeS3()
    cut, _ = cutout(tmp_path, s3, half=20, keep_half=60)
    assert_matches(cut, half=20)
    x, y = WCS0.all_world2pix(RA0, DEC0, 0)
    assert (cut.x0, cut.y0) == (int(np.floor(x + 0.5)) - 20, int(np.floor(y + 0.5)) - 20)
    assert s3.calls[0] == (0, 65536)  # the IMAGE header comes first


def test_smaller_or_shifted_window_needs_no_request(tmp_path):
    s3 = FakeS3()
    cutout(tmp_path, s3, half=20, keep_half=60)
    n = len(s3.calls)
    cut, reader = cutout(tmp_path, s3, half=12, keep_half=60)
    assert_matches(cut, half=12)
    cut, _ = cutout(tmp_path, s3, *sky(6, -5), half=12, keep_half=60)
    assert_matches(cut, half=12)
    assert len(s3.calls) == n and reader.cache_hits == 1


def test_larger_window_reads_only_the_new_rows(tmp_path):
    s3 = FakeS3()
    cutout(tmp_path, s3, half=20, keep_half=60)
    n = len(s3.calls)
    cut, _ = cutout(tmp_path, s3, half=35, keep_half=60)
    assert_matches(cut, half=35)
    new = s3.calls[n:]
    assert all(start > 65536 for start, _ in new)  # no header, the layout is cached
    assert len(new) == 6  # IMAGE, FLAGS and VARIANCE rows above and below, all in one round trip
    assert [length for _, length in new].count(15 * ROW) == 4  # 15 new IMAGE and VARIANCE rows on each side
    cutout(tmp_path, s3, half=30, keep_half=60)
    assert len(s3.calls) == n + 6  # the merged rows serve any window inside them


def test_window_outside_the_column_band_starts_over(tmp_path):
    s3 = FakeS3()
    cutout(tmp_path, s3, half=20, keep_half=20)
    n = len(s3.calls)
    cut, _ = cutout(tmp_path, s3, *sky(30, 0), half=20, keep_half=20)
    assert_matches(cut, half=20)
    assert len(s3.calls) == n + 3 and all(start > 65536 for start, _ in s3.calls[n:])


def test_variance_only_near_the_target(tmp_path):
    s3 = FakeS3()
    target = sky(3.2, 8.6)
    at = lambda h: target  # noqa: E731
    cut, _ = cutout(tmp_path, s3, half=20, keep_half=20, variance_at=at, variance_radius=5)
    assert_matches(cut, half=20, variance=False)
    yt = float(WCS0.all_world2pix(*target, 0)[1])
    k = int(np.floor(yt)) - cut.y0
    finite = np.flatnonzero(np.isfinite(cut.variance).all(axis=1))
    assert finite.tolist() == list(range(k - 5, k + 6))
    assert np.isnan(np.delete(cut.variance, finite, axis=0)).all()
    sl = np.s_[cut.y0 + k - 5:cut.y0 + k + 6, cut.x0:cut.x0 + 41]
    np.testing.assert_array_equal(cut.variance[finite], TRUTH["variance"][sl])
    lengths = [length for _, length in s3.calls]
    assert lengths.count(41 * ROW) == 1 and 11 * ROW in lengths  # 41 IMAGE rows but only 11 VARIANCE rows
    # Asking for all of it later reads just the rows that are missing.
    n = len(s3.calls)
    cut, _ = cutout(tmp_path, s3, half=20, keep_half=20)
    assert_matches(cut, half=20)
    assert len(s3.calls) == n + 2


def test_off_frame_is_remembered(tmp_path):
    s3 = FakeS3()
    assert cutout(tmp_path, s3, RA0 + 5.0, DEC0)[0] is None
    assert len(s3.calls) == 1
    assert cutout(tmp_path, s3, RA0 + 5.0, DEC0)[0] is None
    assert len(s3.calls) == 1


def test_old_per_request_entry_is_reused(tmp_path):
    first, _ = cutout(None, FakeS3(), half=20)
    path = CacheLayout(tmp_path).cutout(URL, RA0, DEC0, 20)
    path.parent.mkdir(parents=True)
    np.savez(path, header=first.header.tostring(), x0=first.x0, y0=first.y0, image=first.image,
             variance=first.variance, flags=first.flags, flag_defs="")
    s3 = FakeS3()
    cut, _ = cutout(tmp_path, s3, half=20)
    assert_matches(cut, half=20)
    assert s3.calls == []
    cut, _ = cutout(tmp_path, s3, half=30)  # wider than the old entry: learns the layout, then reads all rows
    assert_matches(cut, half=30)
