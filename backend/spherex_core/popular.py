"""Popular fields: a curated list plus the fields people actually ask for, kept warm in every cache.

- `CURATED` (targets/popular.json): the five challenge targets, famous nearby stars and brown dwarfs, long-period
  variables and young stars, bright galaxies/nebulae/clusters, and a few solar-system objects. Each entry is a
  name the resolver knows (and, for safety, coordinates and motion in case the name service is down).
- Request counting: every sequence request increments a counter for its field (position rounded to ~0.6 arcmin
  and its field of view). Fields asked for at least PROMOTE_AFTER times are prewarmed too.

`scripts/prewarm_popular.py` builds sequences, archival epochs and catalogue cones for both lists at low priority
(pinned jobs, so they are never cancelled for lack of interest); everything lands in the normal caches, so a
popular field opens instantly.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from .config import settings

CURATED_FILE = Path(__file__).with_name("targets") / "popular.json"
PROMOTE_AFTER = int(os.environ.get("SPHEREX_POPULAR_PROMOTE_AFTER", "5"))
_lock = threading.Lock()


def curated() -> list[dict]:
    return json.loads(CURATED_FILE.read_text())["objects"]


def _counts_path() -> Path:
    return settings.cache_dir / "popular" / "requests.json"


def field_key(ra: float, dec: float, fov: float) -> str:
    return f"{ra:.2f},{dec:+.2f},{fov:g}"


def _load() -> dict:
    try:
        return json.loads(_counts_path().read_text())
    except (OSError, ValueError):
        return {}


def count_request(ra: float, dec: float, fov: float, name: str | None = None, params: dict | None = None) -> int:
    """Count one request for a field (Redis when configured, else a JSON file). Returns the new count."""
    key = field_key(ra, dec, fov)
    r = _redis()
    if r is not None:
        try:
            n = int(r.hincrby("spherex:popular:counts", key, 1))
            if params:
                r.hset("spherex:popular:params", key, json.dumps({"name": name, **params}))
            return n
        except Exception:  # noqa: BLE001 - counting must never break a request
            pass
    with _lock:
        data = _load()
        row = data.get(key) or {"n": 0}
        row["n"] += 1
        row["last"] = time.time()
        if params:
            row["params"] = {"name": name, **params}
        data[key] = row
        path = _counts_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
            tmp.write_text(json.dumps(data))
            tmp.replace(path)
        except OSError:
            pass
        return row["n"]


def promoted(min_count: int = PROMOTE_AFTER) -> list[dict]:
    """Requested fields with at least min_count requests, most requested first: [{key, n, params}]."""
    r = _redis()
    rows: list[dict] = []
    if r is not None:
        try:
            counts = r.hgetall("spherex:popular:counts")
            params = r.hgetall("spherex:popular:params")
            rows = [{"key": k, "n": int(v), "params": json.loads(params[k]) if k in params else None}
                    for k, v in counts.items()]
        except Exception:  # noqa: BLE001
            rows = []
    if not rows:
        rows = [{"key": k, "n": v["n"], "params": v.get("params")} for k, v in _load().items()]
    return sorted((x for x in rows if x["n"] >= min_count and x["params"]), key=lambda x: -x["n"])


def _redis():
    url = os.environ.get("REDIS_URL") or os.environ.get("SPHEREX_REDIS_URL")
    if not url:
        return None
    try:
        import redis

        return redis.Redis.from_url(url, decode_responses=True, socket_timeout=0.5, socket_connect_timeout=0.5)
    except Exception:  # noqa: BLE001
        return None
