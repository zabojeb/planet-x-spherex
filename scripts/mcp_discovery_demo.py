"""Scripted MCP client: a mini discovery run through the SPHEREx MCP server, the way an agent would drive it.

    python scripts/mcp_discovery_demo.py http://127.0.0.1:8000/mcp            # over streamable HTTP
    python scripts/mcp_discovery_demo.py http://host/mcp --field "331.04 -56.78" --field "190.0 75.0"

It builds each field, runs analyze_field, and verifies the top candidates with analyze_point and catalog_cone.
Images (blink strips) are written next to --out so a human can check what the agent saw.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import time
from pathlib import Path

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


def _payload(result) -> tuple[dict | list | str, list[bytes]]:
    texts, pngs = [], []
    for c in result.content:
        if getattr(c, "type", "") == "image":
            pngs.append(base64.b64decode(c.data))
        elif getattr(c, "type", "") == "text":
            texts.append(c.text)
    if getattr(result, "isError", False):
        raise RuntimeError(" ".join(texts))
    data = getattr(result, "structuredContent", None)
    if isinstance(data, dict) and set(data) == {"result"}:
        data = data["result"]
    if data is None and texts:
        try:
            data = json.loads(texts[0])
        except json.JSONDecodeError:
            data = texts[0]
    return data, pngs


async def call(s: ClientSession, tool_name: str, **args):
    r = await s.call_tool(tool_name, args)
    return _payload(r)


async def wait(s: ClientSession, tool: str, key: str, ident: str, every: float = 10.0, limit: float = 900.0):
    t0 = time.time()
    while time.time() - t0 < limit:
        data, _ = await call(s, tool, **{key: ident})
        if data.get("status") in ("done", "error", "cancelled"):
            return data
        print(f"    {tool}: {data.get('status')} {data.get('stage')} {data.get('done')}/{data.get('total')}")
        await asyncio.sleep(every)
    raise TimeoutError(f"{tool} {ident}")


async def run(url: str, fields: list[str], out: Path, fov: float) -> None:
    out.mkdir(parents=True, exist_ok=True)
    report = []
    async with streamable_http_client(url) as (read, write, *_):
        async with ClientSession(read, write) as s:
            await s.initialize()
            tools = await s.list_tools()
            print("tools:", [t.name for t in tools.tools])
            for q in fields:
                tgt, _ = await call(s, "resolve_target", query=q)
                print(f"\n== field {q}: ra {tgt['ra']:.4f} dec {tgt['dec']:.4f}")
                obs, _ = await call(s, "search_observations", ra=tgt["ra"], dec=tgt["dec"])
                print("   observations:", obs.get("n_frames"), "frames,", len(obs.get("visits", [])), "visits")
                b, _ = await call(s, "build_sequence", ra=tgt["ra"], dec=tgt["dec"], pmra=tgt.get("pmra") or 0.0,
                                  pmdec=tgt.get("pmdec") or 0.0, ref_epoch=tgt.get("ref_epoch") or 2000.0,
                                  fov_arcmin=fov, name=q)
                sid = b["sequence_id"]
                if b["status"] != "done":
                    await wait(s, "sequence_status", "sequence_id", sid)
                desc, _ = await call(s, "describe_sequence", sequence_id=sid)
                print("   epochs:", [e["from"] for e in desc["epochs"]])
                _, pngs = await call(s, "get_blink_strip", sequence_id=sid, band="ALL")
                (out / f"{sid}_blink.png").write_bytes(pngs[0])
                job, _ = await call(s, "analyze_field", sequence_id=sid)
                res = await wait(s, "job_result", "job_id", job["job_id"], every=15.0)
                print("   candidate counts:", res.get("counts"))
                found = []
                for cat in ("movers", "faint_movers", "red", "variables", "tracklets"):
                    for c in (res.get(cat) or [])[:3]:
                        if not isinstance(c, dict) or c.get("ra") is None:
                            continue
                        pa, _ = await call(s, "analyze_point", sequence_id=sid, ra=c["ra"], dec=c["dec"])
                        g, _ = await call(s, "catalog_cone", catalog="gaia", ra=c["ra"], dec=c["dec"],
                                          radius_arcmin=0.1, limit=2)
                        found.append({"category": cat, "ra": c["ra"], "dec": c["dec"],
                                      "evidence": {k: c.get(k) for k in ("pm_total", "pm_sig", "snr", "score",
                                                                         "gaia_match", "rb_score", "rate")
                                                   if c.get(k) is not None},
                                      "motion": pa.get("motion"),
                                      "class": (pa.get("classification") or {}).get("summary"),
                                      "gaia_n": g.get("n"),
                                      "gaia_pm": [(x.get("pmra"), x.get("pmdec")) for x in g.get("brightest", [])]})
                        print(f"   {cat}: {c['ra']:.5f} {c['dec']:.5f} gaia_n={g.get('n')} "
                              f"class={found[-1]['class']} motion={pa.get('motion')}")
                report.append({"field": q, "sequence_id": sid, "counts": res.get("counts"), "candidates": found})
    (out / "report.json").write_text(json.dumps(report, indent=1, default=str))
    print("\nreport:", out / "report.json")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--field", action="append", default=None)
    ap.add_argument("--fov", type=float, default=6.0)
    ap.add_argument("--out", type=Path, default=Path("mcp_demo_out"))
    a = ap.parse_args()
    asyncio.run(run(a.url, a.field or ["eps Ind B", "190.0 75.0"], a.out, a.fov))


if __name__ == "__main__":
    main()
