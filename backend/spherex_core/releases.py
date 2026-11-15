"""What data exist: SPHEREx collections at IRSA (size, time span) and whether a sequence has newer exposures.

Both answer from IRSA TAP. The collection summary scans the whole plane table (~14 s), so it is cached for an
hour; a freshness check runs the sequence's footprint search without the cache (and refreshes that cache,
so a forced rebuild picks the new files up).
"""

from __future__ import annotations

import json
import time
from collections import Counter

from . import cache
from .archive import query_frames
from .archive.tap import tap_csv
from .config import settings
from .errors import UpstreamError
from .timeutil import mjd_to_iso, utc_now_iso, year_to_mjd

RELEASES_MAX_AGE_S = 3600
FRESHNESS_MAX_AGE_S = 600


def _read_fresh(path, max_age_s: float) -> dict | None:
    if path.exists() and time.time() - path.stat().st_mtime < max_age_s:
        try:
            return json.loads(path.read_text())
        except ValueError:
            return None
    return None


def _write(path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    tmp.replace(path)


def releases() -> dict:
    """Every SPHEREx collection with its image count and observation time span."""
    path = cache.layout().root / "releases.json"
    hit = _read_fresh(path, RELEASES_MAX_AGE_S)
    if hit:
        return hit
    from .archive import mirror as mirror_mod

    m = mirror_mod.get()
    if m is not None and m.ready(settings.collections):  # the local catalogue answers in milliseconds
        data = _from_mirror(m)
        _write(path, data)
        return data
    try:
        rows = tap_csv("""SELECT o.collection, COUNT(*) AS n, MIN(p.time_bounds_lower) AS oldest,
                                 MAX(p.time_bounds_lower) AS newest
                          FROM spherex.plane p JOIN spherex.observation o ON p.obsid = o.obsid
                          GROUP BY o.collection""", timeout=300, retries=2)
    except RuntimeError as e:
        if m is not None and m.usable(settings.collections):
            return _from_mirror(m)
        raise UpstreamError(f"IRSA TAP is unavailable: {e}") from e
    out = []
    for r in sorted(rows, key=lambda r: r["collection"]):
        oldest = float(r["oldest"]) if r["oldest"] else None
        newest = float(r["newest"]) if r["newest"] else None
        out.append({"collection": r["collection"], "images": int(r["n"]), "oldest_mjd": oldest,
                    "newest_mjd": newest, "oldest_date": mjd_to_iso(oldest) if oldest else None,
                    "newest_date": mjd_to_iso(newest) if newest else None,
                    "searched": r["collection"] in settings.collections})
    data = {"checked": utc_now_iso(), "collections": out, "searched_collections": list(settings.collections)}
    _write(path, data)
    return data


def _from_mirror(m) -> dict:
    """The same summary from the local file catalogue (per release; wide and deep counted together unless the
    mirror was labelled from TAP)."""
    out = []
    for coll, n, oldest, newest in m._db().execute(
            "SELECT collection, COUNT(*), MIN(mjd_start), MAX(mjd_start) FROM files GROUP BY collection ORDER BY 1"):
        out.append({"collection": coll, "images": int(n), "oldest_mjd": oldest, "newest_mjd": newest,
                    "oldest_date": mjd_to_iso(oldest) if oldest else None,
                    "newest_date": mjd_to_iso(newest) if newest else None,
                    "searched": any(c.startswith(coll) for c in settings.collections)})
    return {"checked": utc_now_iso(), "collections": out, "searched_collections": list(settings.collections),
            "source": f"local mirror (synced {m.age_hours():.1f} h ago)" if m.age_hours() is not None else "local mirror"}


def freshness(ctx) -> dict:
    """How many exposures newer than the sequence's latest one are available now."""
    path = cache.layout().root / "freshness" / f"{ctx.seq_id}.json"
    hit = _read_fresh(path, FRESHNESS_MAX_AGE_S)
    if hit:
        return hit
    from .pipeline.sequence import SEARCH_EPOCH

    ra_q, dec_q = ctx.params.target.position_at(year_to_mjd(SEARCH_EPOCH))
    try:
        frames = query_frames(float(ra_q), float(dec_q), ctx.params.collections, use_cache=False)
    except RuntimeError as e:
        raise UpstreamError(f"IRSA TAP is unavailable: {e}") from e
    if hasattr(ctx.params, "select"):
        frames = ctx.params.select(frames)
    exps = ctx.meta["exposures"]
    have = {e["file"] for e in exps}
    newest = max(e["mjd"] for e in exps)
    new = [f for f in frames if f.file_name not in have and f.mjd > newest]
    data = {"sequence": ctx.seq_id, "built": ctx.meta.get("created"), "checked": utc_now_iso(),
            "exposures_in_sequence": len(exps), "exposures_available": len(frames), "new_exposures": len(new),
            "newest_in_sequence_mjd": newest, "newest_in_sequence_date": mjd_to_iso(newest),
            "newest_available_mjd": max((f.mjd for f in frames), default=None),
            "newest_available_date": mjd_to_iso(max(f.mjd for f in frames)) if frames else None,
            "new_by_band": dict(Counter(f.band for f in new)), "new_by_collection": dict(Counter(f.collection for f in new)),
            "rebuild_recommended": bool(new)}
    _write(path, data)
    return data
