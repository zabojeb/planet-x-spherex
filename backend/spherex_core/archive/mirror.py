"""Local mirror of the SPHEREx Level 2 file catalogue, so finding the images of a position takes milliseconds and
keeps working while IRSA TAP is down.

One row per file: IRSA path, release, detector, exposure times, wavelength range and the four footprint corners,
in SQLite with an R-tree over the footprint's bounding box in unit-vector space (no RA wrap-around or pole
special cases). A point query takes the R-tree candidates and keeps the footprints that contain the point
(convex spherical quadrilateral test), then deduplicates exposures across releases exactly like the TAP path.

Two ways to fill it:
- `sync_s3()` (primary): list the public bucket (~1.6 M current files in ~20 s), then read the first 32 KiB of
  every file not yet mirrored and take the footprint from its own WCS (SIP included), the times from MJD-BEG /
  MJD-END and the detector from DETECTOR. Needs nothing from IRSA, is incremental (only new keys are read) and
  notices files that disappeared.
- `sync_tap()` (when IRSA TAP is up): the same rows from spherex.plane/artifact/observation in weekly windows;
  adds the exact collection names (wide vs deep survey) and IRSA's wavelength bounds.

The file path comes from settings (SPHEREX_MIRROR_DB, default <cache>/mirror/spherex.sqlite).
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import sqlite3
import threading
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from ..bands import BAND_INFO
from ..config import settings

log = logging.getLogger(__name__)
RELEASE_OF = {"spherex_dr1": "dr1", "spherex_dr1_deep": "dr1", "spherex_qr3": "qr3", "spherex_qr3_deep": "qr3",
              "spherex_qr2": "qr2", "spherex_qr2_deep": "qr2", "spherex_qr": "qr"}
IRSA_PREFIX = "ibe/data/spherex/"
HEADER_BYTES = 32768  # primary (2880) + IMAGE header (23-26 KiB); retried with 128 KiB when END is missing
PAD = 2e-3  # bounding-box margin in unit-vector space (~0.11 deg): great-circle edges bulge past the chords
NX = NY = 2040
SCHEMA = """
CREATE TABLE IF NOT EXISTS files(
  id INTEGER PRIMARY KEY, uri TEXT UNIQUE NOT NULL, release TEXT NOT NULL, collection TEXT NOT NULL,
  obs_id TEXT, band TEXT NOT NULL, mjd REAL, mjd_start REAL, mjd_end REAL, wl_min REAL, wl_max REAL,
  corners TEXT NOT NULL, size INTEGER, modified TEXT, source TEXT);
CREATE INDEX IF NOT EXISTS files_mjd ON files(mjd);
CREATE INDEX IF NOT EXISTS files_release ON files(release);
CREATE VIRTUAL TABLE IF NOT EXISTS files_rt USING rtree(id, x0, x1, y0, y1, z0, z1);
CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY, value TEXT);
"""


def unit(ra, dec) -> np.ndarray:
    ra, dec = np.radians(np.asarray(ra, float)), np.radians(np.asarray(dec, float))
    return np.stack([np.cos(dec) * np.cos(ra), np.cos(dec) * np.sin(ra), np.sin(dec)], axis=-1)


def contains(corners: list[float], ra: float, dec: float) -> bool:
    """True when (ra, dec) is inside the convex spherical quadrilateral with these corners (ra0 dec0 ... ra3 dec3)."""
    v = unit(corners[0::2], corners[1::2])
    p = unit(ra, dec)
    s = [float(np.dot(np.cross(v[i], v[(i + 1) % len(v)]), p)) for i in range(len(v))]
    return all(x >= -1e-12 for x in s) or all(x <= 1e-12 for x in s)


def bbox(corners: list[float]) -> tuple[float, ...]:
    v = unit(corners[0::2], corners[1::2])
    lo, hi = v.min(axis=0) - PAD, v.max(axis=0) + PAD
    return float(lo[0]), float(hi[0]), float(lo[1]), float(hi[1]), float(lo[2]), float(hi[2])


def mirror_path() -> Path:
    return Path(settings.mirror_db) if settings.mirror_db else settings.cache_dir / "mirror" / "spherex.sqlite"


class Mirror:
    """Thread-safe handle on the mirror database (one connection per thread, WAL mode)."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else mirror_path()
        self._local = threading.local()

    def _db(self) -> sqlite3.Connection:
        c = getattr(self._local, "c", None)
        if c is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            c = sqlite3.connect(self.path, timeout=30)
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=NORMAL")
            c.executescript(SCHEMA)
            self._local.c = c
        return c

    # ---------------------------------------------------------------- state
    def get_state(self, key: str, default: str | None = None) -> str | None:
        row = self._db().execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set_state(self, **kv) -> None:
        db = self._db()
        db.executemany("INSERT OR REPLACE INTO state(key, value) VALUES (?, ?)", [(k, str(v)) for k, v in kv.items()])
        db.commit()

    def releases_needed(self, collections: Iterable[str]) -> set[str]:
        return {RELEASE_OF.get(c, c.removeprefix("spherex_").split("_")[0]) for c in collections}

    def complete(self, release: str) -> bool:
        return self.get_state(f"complete:{release}") == "1"

    def age_hours(self) -> float | None:
        t = self.get_state("synced_at")
        return (time.time() - float(t)) / 3600 if t else None

    def ready(self, collections: Iterable[str], max_age_hours: float | None = None) -> bool:
        """The mirror has every needed release complete and was refreshed recently enough to answer alone."""
        max_age = settings.mirror_max_age_hours if max_age_hours is None else max_age_hours
        age = self.age_hours()
        return (age is not None and age <= max_age
                and all(self.complete(r) for r in self.releases_needed(collections)))

    def usable(self, collections: Iterable[str]) -> bool:
        """At least some rows for every needed release (good enough as a last resort when IRSA is down)."""
        db = self._db()
        return all(db.execute("SELECT 1 FROM files WHERE release=? LIMIT 1", (r,)).fetchone()
                   for r in self.releases_needed(collections))

    # ---------------------------------------------------------------- reading
    def query(self, ra: float, dec: float, collections: tuple[str, ...]) -> list:
        """Frames whose footprint contains (ra, dec), one per exposure and detector, oldest first."""
        from .spherex import Frame

        p = unit(ra, dec)
        rows = self._db().execute(
            """SELECT f.uri, f.release, f.collection, f.obs_id, f.band, f.mjd, f.mjd_start, f.mjd_end, f.wl_min,
                      f.wl_max, f.corners
               FROM files_rt r JOIN files f ON f.id = r.id
               WHERE r.x0 <= ? AND r.x1 >= ? AND r.y0 <= ? AND r.y1 >= ? AND r.z0 <= ? AND r.z1 >= ?""",
            (p[0], p[0], p[1], p[1], p[2], p[2])).fetchall()
        rank = {c: i for i, c in enumerate(collections)}
        release_rank: dict[str, int] = {}
        for c in collections:
            release_rank.setdefault(RELEASE_OF.get(c, c), rank[c])
        best: dict[tuple[str, str], tuple[int, Frame]] = {}
        for uri, release, collection, obs, band, mjd, t0, t1, wl0, wl1, corners in rows:
            if release not in release_rank:
                continue
            if collection in rank:
                r = rank[collection]
            elif collection == f"spherex_{release}" or collection.startswith(f"spherex_{release}"):
                r = release_rank[release]  # mirrored from S3: wide/deep unknown, ranked with its release
            else:
                continue
            if not contains(json.loads(corners), ra, dec):
                continue
            f = Frame(uri=uri, band=band, mjd=mjd, mjd_start=t0, mjd_end=t1, collection=collection,
                      obs_id=obs or uri, wl_min_um=wl0, wl_max_um=wl1)
            k = (f.obs_id, band)
            if k not in best or r < best[k][0]:
                best[k] = (r, f)
        return sorted((f for _, f in best.values()), key=lambda f: f.mjd)

    def known_uris(self, release: str | None = None) -> set[str]:
        q = "SELECT uri FROM files" + (" WHERE release=?" if release else "")
        return {u for (u,) in self._db().execute(q, (release,) if release else ())}

    def stats(self) -> dict:
        """Size, per-release counts and sync state. The counts scan the table (~0.5 s for 1.6 M rows), so they
        are reused for a minute (the status endpoint may be polled)."""
        synced = self.get_state("synced_at")
        cached = getattr(self, "_stats_cache", None)
        if cached is None or cached[0] != synced or time.time() - cached[1] > 60:
            per = {r: {"files": n, "oldest_mjd": a, "newest_mjd": b, "complete": self.complete(r)}
                   for r, n, a, b in self._db().execute(
                       "SELECT release, COUNT(*), MIN(mjd_start), MAX(mjd_start) FROM files GROUP BY release")}
            self._stats_cache = cached = (synced, time.time(), per)
        per = cached[2]
        size = self.path.stat().st_size if self.path.exists() else 0
        wal = self.path.with_name(self.path.name + "-wal")
        return {"path": str(self.path), "bytes": size + (wal.stat().st_size if wal.exists() else 0),
                "files": sum(v["files"] for v in per.values()), "releases": per,
                "synced_at": datetime.fromtimestamp(float(synced), timezone.utc).isoformat(timespec="seconds")
                if synced else None, "age_hours": self.age_hours(),
                "last_sync": json.loads(self.get_state("last_sync", "null") or "null")}

    # ---------------------------------------------------------------- writing
    def upsert(self, rows: list[dict]) -> int:
        """Insert or replace rows (dicts with the files columns plus 'corners' as a list)."""
        db = self._db()
        n = 0
        with db:
            for r in rows:
                cur = db.execute(
                    """INSERT INTO files(uri, release, collection, obs_id, band, mjd, mjd_start, mjd_end, wl_min,
                           wl_max, corners, size, modified, source)
                       VALUES (:uri, :release, :collection, :obs_id, :band, :mjd, :mjd_start, :mjd_end, :wl_min,
                           :wl_max, :corners, :size, :modified, :source)
                       ON CONFLICT(uri) DO UPDATE SET collection=excluded.collection, obs_id=excluded.obs_id,
                           band=excluded.band, mjd=excluded.mjd, mjd_start=excluded.mjd_start,
                           mjd_end=excluded.mjd_end, wl_min=excluded.wl_min, wl_max=excluded.wl_max,
                           corners=excluded.corners, size=COALESCE(excluded.size, files.size),
                           modified=COALESCE(excluded.modified, files.modified), source=excluded.source
                       RETURNING id""",
                    {**r, "corners": json.dumps([round(x, 7) for x in r["corners"]]),
                     "size": r.get("size"), "modified": r.get("modified")})
                fid = cur.fetchone()[0]
                db.execute("INSERT OR REPLACE INTO files_rt(id, x0, x1, y0, y1, z0, z1) VALUES (?,?,?,?,?,?,?)",
                           (fid, *bbox(r["corners"])))
                n += 1
        return n

    def delete_uris(self, uris: Iterable[str]) -> int:
        db = self._db()
        n = 0
        with db:
            for u in uris:
                row = db.execute("SELECT id FROM files WHERE uri=?", (u,)).fetchone()
                if row:
                    db.execute("DELETE FROM files_rt WHERE id=?", row)
                    db.execute("DELETE FROM files WHERE id=?", row)
                    n += 1
        return n


_mirror: Mirror | None = None
_mirror_lock = threading.Lock()


def get() -> Mirror | None:
    """The process-wide mirror, or None when disabled (SPHEREX_MIRROR=0) or not created yet."""
    global _mirror
    if not settings.mirror_enabled:
        return None
    with _mirror_lock:
        if _mirror is None or _mirror.path != mirror_path():
            if not mirror_path().exists():
                return None
            _mirror = Mirror()
        return _mirror


def reset() -> None:
    global _mirror
    with _mirror_lock:
        _mirror = None


# ---------------------------------------------------------------- S3 harvest


_KEY = re.compile(r"<Key>([^<]*)</Key>.*?<LastModified>([^<]*)</LastModified>.*?<Size>(\d+)</Size>", re.S)


async def _list(session, base: str, prefix: str, delimiter: str | None = None):
    keys, prefixes, token = [], [], None
    while True:
        params = {"list-type": "2", "prefix": prefix}
        if delimiter:
            params["delimiter"] = delimiter
        if token:
            params["continuation-token"] = token
        async with session.get(base, params=params) as r:
            r.raise_for_status()
            text = await r.text()
        keys += [(k, m, int(s)) for k, m, s in _KEY.findall(text)]
        if delimiter:
            prefixes += re.findall(r"<Prefix>([^<]*)</Prefix>", text)[1:]
        m = re.search(r"<NextContinuationToken>([^<]*)</NextContinuationToken>", text)
        if not m:
            return keys, prefixes
        token = m.group(1)


async def list_level2(release: str, base: str | None = None) -> list[tuple[str, str, int]]:
    """(key, last-modified, size) of every Level 2 FITS file of a release in the public bucket."""
    import aiohttp

    base = base or settings.s3_base
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=600)) as s:
        _, weeks = await _list(s, base, f"{release}/level2/", "/")
        sem = asyncio.Semaphore(32)

        async def one(w):
            async with sem:
                return (await _list(s, base, w))[0]

        parts = await asyncio.gather(*(one(w) for w in weeks))
    return [k for part in parts for k in part if k[0].endswith(".fits")]


def _wave_bounds() -> dict[str, tuple[float, float]]:
    """Wavelength range per detector: from the cached wavelength tables when present, else nominal."""
    out = {b: (v["wl_min"], v["wl_max"]) for b, v in BAND_INFO.items()}
    for b in BAND_INFO:
        p = settings.cache_dir / "wave" / f"D{b[1]}.npz"
        try:
            with np.load(p) as z:
                wl = z["values"][..., 0]
                out[b] = (float(np.nanmin(wl)), float(np.nanmax(wl)))
        except Exception:  # noqa: BLE001
            pass
    return out


def row_from_header(key: str, buf: bytes, modified: str | None, size: int | None,
                    wave: dict[str, tuple[float, float]]) -> dict:
    """A mirror row from the first bytes of a Level 2 file (its primary and IMAGE headers)."""
    import warnings

    from astropy.io import fits
    from astropy.wcs import WCS

    from ..io.fitsheader import parse_header, slim_header

    prim = parse_header(buf)
    if prim is None:
        raise ValueError("primary header incomplete")
    img = parse_header(buf, prim[1])
    if img is None:
        raise ValueError("IMAGE header incomplete")
    h = slim_header(img[0])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        w = WCS(h)
        nx, ny = int(h.get("NAXIS1", NX)), int(h.get("NAXIS2", NY))
        ra, dec = w.all_pix2world([-0.5, nx - 0.5, nx - 0.5, -0.5], [-0.5, -0.5, ny - 0.5, ny - 0.5], 0)
    corners = [float(x) for pair in zip(ra, dec) for x in pair]
    if not all(math.isfinite(x) for x in corners):
        raise ValueError("WCS gave non-finite corners")
    det = int(h.get("DETECTOR", 0)) or int(re.search(r"D(\d)_spx", key).group(1))
    band = f"D{det}"
    t0, t1 = float(h.get("MJD-BEG", h.get("MJD-AVG", 0))), float(h.get("MJD-END", h.get("MJD-AVG", 0)))
    release = key.split("/", 1)[0]
    wl0, wl1 = wave.get(band, (None, None))
    return {"uri": IRSA_PREFIX + key, "release": release, "collection": f"spherex_{release}",
            "obs_id": str(h.get("OBSID", "")).strip() or None, "band": band, "mjd": (t0 + t1) / 2, "mjd_start": t0,
            "mjd_end": t1, "wl_min": wl0, "wl_max": wl1, "corners": corners, "size": size, "modified": modified,
            "source": "s3-header"}


def _harvest_chunk(items: list[tuple[str, str, int]], base: str, concurrency: int,
                   wave: dict[str, tuple[float, float]]) -> tuple[list[dict], list[tuple[str, str]]]:
    """Worker process: read the headers of these keys and turn them into rows."""
    import warnings

    warnings.filterwarnings("ignore")

    async def run():
        import aiohttp

        rows, failures = [], []
        sem = asyncio.Semaphore(concurrency)
        conn = aiohttp.TCPConnector(limit=concurrency, ttl_dns_cache=600)
        async with aiohttp.ClientSession(connector=conn, timeout=aiohttp.ClientTimeout(total=60)) as s:
            async def one(key, modified, size):
                async with sem:
                    for nbytes in (HEADER_BYTES, 131072):
                        for attempt in range(3):
                            try:
                                async with s.get(base + key, headers={"Range": f"bytes=0-{nbytes - 1}"}) as r:
                                    if r.status not in (200, 206):
                                        raise RuntimeError(f"HTTP {r.status}")
                                    buf = await r.read()
                                break
                            except Exception as e:  # noqa: BLE001
                                if attempt == 2:
                                    failures.append((key, f"{e.__class__.__name__}: {e}"[:120]))
                                    return
                                await asyncio.sleep(0.5 * (attempt + 1))
                        try:
                            rows.append(row_from_header(key, buf, modified, size, wave))
                            return
                        except ValueError as e:
                            if nbytes != HEADER_BYTES:
                                failures.append((key, str(e)))
                                return
                        except Exception as e:  # noqa: BLE001
                            failures.append((key, f"{e.__class__.__name__}: {e}"[:120]))
                            return

            await asyncio.gather(*(one(*it) for it in items))
        return rows, failures

    return asyncio.run(run())


def sync_s3(releases: Iterable[str] | None = None, *, max_new: int | None = None, workers: int | None = None,
            chunk: int = 1500, concurrency: int = 24, base: str | None = None, prune: bool = True,
            mirror: Mirror | None = None, progress: Callable[[int, int], None] | None = None) -> dict:
    """List the bucket and mirror every file not mirrored yet. Returns a summary (counts, seconds)."""
    import multiprocessing as mp

    m = mirror or Mirror()
    base = base or settings.s3_base
    releases = list(releases or sorted({RELEASE_OF.get(c, c) for c in settings.collections}))
    wave = _wave_bounds()
    t0 = time.perf_counter()
    summary: dict = {"releases": {}, "started": time.time()}
    for rel in releases:
        t_list = time.perf_counter()
        listing = asyncio.run(list_level2(rel, base))
        listed = {IRSA_PREFIX + k for k, _, _ in listing}
        known = m.known_uris(rel)
        new = [it for it in listing if IRSA_PREFIX + it[0] not in known]
        truncated = max_new is not None and len(new) > max_new
        if truncated:
            new = new[:max_new]
        gone = known - listed if prune else set()
        if gone:
            m.delete_uris(gone)
        log.info("mirror %s: listed %d files in %.1f s, %d new, %d gone", rel, len(listing),
                 time.perf_counter() - t_list, len(new), len(gone))
        added, failures = 0, []
        chunks = [new[i:i + chunk] for i in range(0, len(new), chunk)]
        if chunks:
            nproc = workers or max(1, min(settings.cpu_workers, len(chunks)))
            with ProcessPoolExecutor(max_workers=nproc, mp_context=mp.get_context("spawn")) as pool:
                futs = [pool.submit(_harvest_chunk, c, base, concurrency, wave) for c in chunks]
                for fut in futs:
                    rows, fails = fut.result()
                    added += m.upsert(rows)
                    failures += fails
                    if progress:
                        progress(added + len(failures), len(new))
        # a handful of unreadable files (left for the next sync) does not make the release incomplete
        complete = not truncated and len(failures) <= max(5, 0.001 * len(listing))
        m.set_state(**{f"complete:{rel}": "1" if complete else "0", f"listed:{rel}": len(listing)})
        summary["releases"][rel] = {"listed": len(listing), "new": len(new), "added": added, "gone": len(gone),
                                    "failed": len(failures), "failures": failures[:5], "truncated": truncated}
    summary["seconds"] = round(time.perf_counter() - t0, 1)
    m.set_state(synced_at=time.time(), last_sync=json.dumps(summary))
    log.info("mirror sync done in %.1f s: %s", summary["seconds"],
             {r: {k: v for k, v in s.items() if k != "failures"} for r, s in summary["releases"].items()})
    return summary


# ---------------------------------------------------------------- TAP sync


def _floats(text: str) -> list[float]:
    return [float(x) for x in re.findall(r"[-+]?\d+(?:\.\d*)?(?:[eE][-+]?\d+)?", text or "")]


def sync_tap(collections: Iterable[str] | None = None, start_mjd: float = 60780.0, end_mjd: float | None = None,
             window_days: float = 7.0, mirror: Mirror | None = None) -> dict:
    """Fill or relabel the mirror from IRSA TAP in time windows (exact collections and wavelength bounds)."""
    from .tap import tap_csv

    m = mirror or Mirror()
    collections = tuple(collections or settings.collections)
    coll = ",".join(f"'{c}'" for c in collections)
    end_mjd = end_mjd or (time.time() / 86400 + 40587.0 + 1)
    t0, n, windows = time.perf_counter(), 0, 0
    a = start_mjd
    while a < end_mjd:
        b = a + window_days
        rows = tap_csv(f"""
            SELECT a.uri, p.energy_bandpassname AS band, p.time_bounds_lower AS t0, p.time_bounds_upper AS t1,
                   p.energy_bounds_lower AS wl0, p.energy_bounds_upper AS wl1, o.collection, o.observationid AS obs,
                   p.poly
            FROM spherex.artifact a JOIN spherex.plane p ON a.planeid = p.planeid
            JOIN spherex.observation o ON p.obsid = o.obsid
            WHERE p.time_bounds_lower >= {a} AND p.time_bounds_lower < {b}
              AND o.collection IN ({coll}) AND a.producttype = 'science'""", timeout=600, retries=3)
        out = []
        for r in rows:
            corners = _floats(r.get("poly", ""))
            if len(corners) < 6 or len(corners) % 2:
                continue
            uri = r["uri"]
            release = uri.removeprefix(IRSA_PREFIX).split("/", 1)[0]
            t_lo, t_hi = float(r["t0"]), float(r["t1"])
            out.append({"uri": uri, "release": release, "collection": r["collection"], "obs_id": r["obs"],
                        "band": r["band"].replace("SPHEREx-", ""), "mjd": (t_lo + t_hi) / 2, "mjd_start": t_lo,
                        "mjd_end": t_hi, "wl_min": float(r["wl0"]) * 1e6, "wl_max": float(r["wl1"]) * 1e6,
                        "corners": corners[:8] if len(corners) >= 8 else corners, "source": "irsa-tap"})
        n += m.upsert(out)
        windows += 1
        a = b
    summary = {"rows": n, "windows": windows, "seconds": round(time.perf_counter() - t0, 1)}
    m.set_state(tap_synced_at=time.time(), last_tap_sync=json.dumps(summary))
    return summary


# ---------------------------------------------------------------- background refresh


_bg_started = False


def start_background_sync(interval_s: float | None = None) -> None:
    """Keep the mirror fresh from a daemon thread: bootstrap when empty (if allowed), then incremental syncs.

    A lock file makes sure only one process of the machine syncs at a time (uvicorn workers, API + worker).
    """
    global _bg_started
    if _bg_started or not settings.mirror_enabled or not settings.mirror_autosync:
        return
    _bg_started = True
    interval = interval_s or settings.mirror_sync_interval_s

    def loop():
        import fcntl

        while True:
            lock_path = mirror_path().with_suffix(".lock")
            try:
                lock_path.parent.mkdir(parents=True, exist_ok=True)
                with open(lock_path, "w") as fh:
                    try:
                        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except OSError:
                        pass  # another process is syncing
                    else:
                        m = Mirror()
                        age = m.age_hours()
                        if (age is not None or settings.mirror_bootstrap) and (age is None or age * 3600 >= interval * 0.9):
                            sync_s3(mirror=m)  # first run: full bootstrap; later: only new files
                            reset()
            except Exception as e:  # noqa: BLE001 - try again next round
                log.warning("mirror background sync failed: %s", e)
            time.sleep(interval)

    threading.Thread(target=loop, name="mirror-sync", daemon=True).start()
