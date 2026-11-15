"""What SPHEREx has at a sky position, for the map's pick card and coverage layer (ADR-0061).

Answers from the local file-catalogue mirror (archive/mirror.py) whenever it has rows, stale or not: the card is
a preview, and a fresh image search happens anyway when a sequence is built. Without a mirror it falls back to
the normal image search in interactive mode (short timeouts), so a dead IRSA costs seconds, not minutes.
"""

from __future__ import annotations

import time

from . import netpolicy
from .archive import mirror as mirror_mod
from .archive.spherex import query_frames
from .bands import BANDS
from .config import settings
from .timeutil import mjd_to_iso

VISIT_GAP_DAYS = 20.0
FOOTPRINT_MAX_RADIUS = 12.0  # degrees: wider views would mean tens of thousands of outlines


def _visits(mjds: list[float]) -> list[dict]:
    out: list[dict] = []
    for t in sorted(mjds):
        if out and t - out[-1]["mjd_max"] <= VISIT_GAP_DAYS:
            out[-1]["mjd_max"] = t
            out[-1]["n"] += 1
        else:
            out.append({"mjd_min": t, "mjd_max": t, "n": 1})
    for v in out:
        v["from"], v["to"] = mjd_to_iso(v["mjd_min"])[:10], mjd_to_iso(v["mjd_max"])[:10]
    return out


def at(ra: float, dec: float) -> dict:
    """Exposures, visits, detectors and releases covering (ra, dec)."""
    t0 = time.perf_counter()
    m = mirror_mod.get()
    source = "mirror"
    if m is not None and m.usable(settings.collections):
        frames = m.query(ra, dec, settings.collections)
        if not m.ready(settings.collections):
            source = "mirror (stale)"
    else:
        source = "image search"
        with netpolicy.fast():
            frames = query_frames(ra, dec)
    visits = _visits([f.mjd for f in frames])
    per_band = {b: sum(1 for f in frames if f.band == b) for b in BANDS}
    per_coll: dict[str, int] = {}
    for f in frames:
        per_coll[f.collection] = per_coll.get(f.collection, 0) + 1
    return {
        "ra": ra, "dec": dec, "n_exposures": len(frames), "n_visits": len(visits),
        "per_detector": per_band, "per_collection": per_coll,
        "date_min": visits[0]["from"] if visits else None, "date_max": visits[-1]["to"] if visits else None,
        "visits": visits, "deep": any("deep" in c for c in per_coll), "source": source,
        "seconds": round(time.perf_counter() - t0, 3),
    }


def footprints(ra: float, dec: float, radius_deg: float, limit: int = 600) -> dict:
    """Outlines of SPHEREx exposures (one detector per dichroic pair) within radius_deg, newest first."""
    m = mirror_mod.get()
    if m is None or not m.usable(settings.collections):
        return {"available": False, "reason": "the local file catalogue is not built on this server", "footprints": []}
    radius = min(max(radius_deg, 0.05), FOOTPRINT_MAX_RADIUS)
    t0 = time.perf_counter()
    fps, total = m.footprints(ra, dec, radius, limit=limit)
    return {"available": True, "ra": ra, "dec": dec, "radius_deg": radius, "total": total, "shown": len(fps),
            "footprints": fps, "seconds": round(time.perf_counter() - t0, 3)}
