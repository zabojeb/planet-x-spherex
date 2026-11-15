"""Keep popular fields warm: sequences, archival epochs and catalogue cones for the curated list
(backend/spherex_core/targets/popular.json) plus fields people asked for often (popular.promoted()).

Runs builds at prewarm priority as pinned jobs (never cancelled for lack of interest), in this process, writing to
the shared cache (SPHEREX_CACHE_DIR), so the API serves the results instantly afterwards.

    python scripts/prewarm_popular.py                    # curated + promoted
    python scripts/prewarm_popular.py --only "Mira" barnard
    python scripts/prewarm_popular.py --no-archival --no-catalogs
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time

from spherex_core import catalogs, popular, services
from spherex_core.jobs import PRIORITY_PREWARM
from spherex_core.pipeline.params import SequenceParams
from spherex_core.targets import resolve

CATALOGS = ("gaia", "catwise", "2mass", "simbad")


async def wait_sequence(job: dict, timeout: float = 1800) -> tuple[str, str]:
    seq_id = job["id"]
    t0 = time.time()
    while time.time() - t0 < timeout:
        state, body = await services.poll_sequence(seq_id, client="prewarm")
        if state in ("done", "error", "cancelled"):
            return seq_id, state
        await asyncio.sleep(2)
    return seq_id, "timeout"


async def warm(entry: dict, archival: bool, cats: bool) -> dict:
    t0 = time.time()
    q, fov = entry["query"], float(entry.get("fov", 6))
    out = {"query": q, "kind": entry.get("kind")}
    try:
        if entry.get("moving"):
            state, body = "running", None
            while state == "running":
                state, body = await services.moving_search(q)
                if state == "running":
                    await asyncio.sleep(2)
            if state != "done":
                raise RuntimeError(f"moving-object search {state}: {body}")
            req = json.loads(body) if isinstance(body, (bytes, str)) else body
            params = SequenceParams(**{k: v for k, v in req["suggested_request"].items()
                                       if k in SequenceParams.__dataclass_fields__})
        else:
            t = await asyncio.to_thread(resolve, q)
            params = SequenceParams(ra=t.ra, dec=t.dec, pmra=t.pmra, pmdec=t.pmdec, ref_epoch=t.ref_epoch,
                                    name=t.name or q, fov_arcmin=fov)
        job = await services.submit_sequence(params, client="prewarm", priority=PRIORITY_PREWARM, pinned=True)
        seq_id, state = await wait_sequence(job)
        out.update(sequence=seq_id, state=state)
        if state == "done" and archival and not entry.get("moving"):
            a_state = "running"
            while a_state == "running":
                a_state, _ = await services.archival_status(seq_id)
                if a_state == "running":
                    await asyncio.sleep(3)
            out["archival"] = a_state
        if cats and not entry.get("moving"):
            r = fov / 60 * 0.75  # covers the field's corners
            got = {}
            for name in CATALOGS:
                try:
                    got[name] = len(await asyncio.to_thread(catalogs.cone, name, params.ra, params.dec, r))
                except Exception as e:  # noqa: BLE001 - one dead catalogue must not stop the warm-up
                    got[name] = f"failed: {str(e)[:80]}"
            out["catalogs"] = got
    except Exception as e:  # noqa: BLE001
        out["error"] = str(e)[:200]
    out["seconds"] = round(time.time() - t0, 1)
    print(json.dumps(out), flush=True)
    return out


async def main_async(a) -> None:
    entries = popular.curated()
    if a.only:
        wanted = {w.lower() for w in a.only}
        entries = [e for e in entries if e["query"].lower() in wanted]
    if not a.only and not a.no_promoted:
        for p in popular.promoted():
            prm = p["params"]
            entries.append({"query": f"{prm['ra']:.6f} {prm['dec']:+.6f}", "fov": prm.get("fov_arcmin", 6),
                            "kind": f"requested {p['n']}×"})
    print(f"prewarming {len(entries)} fields", flush=True)
    results = []
    for e in entries:  # one at a time: prewarm must not crowd out people's builds
        results.append(await warm(e, not a.no_archival, not a.no_catalogs))
    ok = sum(1 for r in results if r.get("state") == "done")
    print(json.dumps({"fields": len(results), "done": ok, "seconds": round(sum(r["seconds"] for r in results), 1)}))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--no-archival", action="store_true")
    ap.add_argument("--no-catalogs", action="store_true")
    ap.add_argument("--no-promoted", action="store_true")
    asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    main()
