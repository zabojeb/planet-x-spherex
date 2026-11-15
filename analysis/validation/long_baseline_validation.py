"""Validation of the long-baseline light curves and the field-wide long-period search (ADR-0059).

Light curves (spherex_core.longbaseline.run, in this process):
  R Dra      Mira, VSX P = 246.12 d                      sequence target
  V1968 Cyg  carbon Mira, VSX P = 395.45 d               a position 40" from the centre of a Cygnus field
  RW Aur     CTTS with optical dimming episodes since 2010 (IR brightening reported during the 2014-2016 dip)
Field searches (search_field) on fields with cached archival image lists, every source cross-matched with VSX
(5"): a clean source (no AllWISE contamination flag) flagged variable or periodic that VSX does not list counts as
a false positive (an upper bound, since VSX is incomplete).

Usage (server):
    SPHEREX_CACHE_DIR=/workspace/cache python analysis/validation/long_baseline_validation.py [--only NAME ...]
        [--search-only | --no-search] [--field-radius 360] [--search-radius 360] [--out-dir runs/validation]
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import time
import urllib.parse
import urllib.request

from spherex_core.analysis.common import load_context
from spherex_core.longbaseline import LongBaselineRequest, SearchOptions, run, search_field
from spherex_core.longbaseline.calibrate import _sep_arcsec
from spherex_core.longbaseline.refcat import parse_asu_tsv
from spherex_core.pipeline.results import clean_json

CASES = [
    {"name": "R Dra", "seq": "c16e0057355ff8f04f32", "period": 246.12},
    {"name": "V1968 Cyg", "seq": "e1c3e2e143890b1de498", "ra": 300.28772, "dec": 40.92750, "period": 395.45},
    {"name": "RW Aur", "seq": "cb983e7485da07d622a4"},
]
SEARCH_FIELDS = {"R Dra": "c16e0057355ff8f04f32", "RW Aur": "cb983e7485da07d622a4",
                 "eps Indi B": "d651cb1431ed20027191",
                 "Barnard's Star": "4261e28d6e0c3cb6df94", "WISE 0855-0714": "e3fc10babf437ede1d93",
                 "Cygnus (V1968 Cyg)": "e1c3e2e143890b1de498"}
VSX_MATCH_ARCSEC = 5.0


def _plotter():
    path = os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "long_baseline.py")
    spec = importlib.util.spec_from_file_location("long_baseline_cli", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def vsx(ra: float, dec: float, radius_arcsec: float) -> list[dict]:
    q = urllib.parse.urlencode({"-source": "B/vsx/vsx", "-c": f"{ra:.5f} {dec:+.5f}", "-c.rs": f"{radius_arcsec:.0f}",
                                "-out": "Name,RAJ2000,DEJ2000,Type,Period", "-out.max": "5000"})
    text = urllib.request.urlopen("https://vizier.cfa.harvard.edu/viz-bin/asu-tsv?" + q, timeout=90).read().decode()
    out = []
    for r in parse_asu_tsv(text):
        try:
            out.append({"name": r["Name"], "ra": float(r["RAJ2000"]), "dec": float(r["DEJ2000"]), "type": r["Type"],
                        "period": float(r["Period"]) if r.get("Period") else None})
        except (KeyError, ValueError):
            continue
    return out


def light_curves(cases: list[dict], out_dir: str, field_radius: float) -> list[dict]:
    cli = _plotter()
    rows = []
    for c in cases:
        ctx = load_context(c["seq"])
        req = LongBaselineRequest(ra=c.get("ra"), dec=c.get("dec"), name=c["name"] if c.get("ra") else None,
                                  field_radius=field_radius)
        t0 = time.perf_counter()
        res = run(ctx, req, lambda *a, **k: None, lambda: None)
        wall = time.perf_counter() - t0
        slug = c["name"].replace(" ", "_").lower()
        with open(f"{out_dir}/{slug}.json", "w") as f:
            json.dump(clean_json(res), f)
        try:
            cli.plot(res, f"{out_dir}/{slug}.png")
        except Exception as e:  # noqa: BLE001 - the numbers matter more than the figure
            print("plot failed:", e)
        a = res.get("analysis") or {}
        p = a.get("period") or {}
        row = {"name": c["name"], "sequence": c["seq"], "seconds": wall, "stats": res["stats"],
               "coverage": res["coverage"], "methods": {k: v.get("method") for k, v in res["methods"].items()},
               "calibration": {"references": res["calibration"].get("references"),
                               "stars": len(res["calibration"].get("stars", [])),
                               "frames": {k: {kk: v.get(kk) for kk in ("offset_mag", "scatter_mag", "n", "applied")}
                                          for k, v in (res["calibration"].get("frames") or {}).items()},
                               "spherex": {k: {kk: v.get(kk) for kk in ("offset_mag", "scatter_mag", "n", "applied",
                                                                        "used_offset_mag", "source",
                                                                        "rejected_variable")}
                                           for k, v in (res["calibration"].get("spherex") or {}).items()}},
               "period_days": p.get("period_days"), "period_err_days": p.get("period_err_days"),
               "power": p.get("power"), "fap": p.get("false_alarm_probability"), "significant": p.get("significant"),
               "amplitudes_mag": p.get("amplitudes_mag"), "peaks": [x["period_days"] for x in p.get("peaks", [])],
               "literature_period": c.get("period"),
               "trends": a.get("trends"), "events": a.get("events"), "era_means": a.get("era_means"),
               "classification": a.get("classification"), "notes": res.get("notes")}
        if c.get("period") and p.get("period_days"):
            row["period_offset_days"] = p["period_days"] - c["period"]
            row["period_offset_sigma"] = (row["period_offset_days"] / p["period_err_days"]
                                          if p.get("period_err_days") else None)
        # SPHEREx-only period, for comparison with the long baseline
        if p:
            from spherex_core.longbaseline import model

            sx = [q for q in res["points"] if q["survey"] == "SPHEREx" and q.get("mag") is not None
                  and q.get("mag_err") is not None]
            try:
                a2 = model.analyze(sx, period_bands=("W1", "W2", "D1", "D2", "D3", "D4", "D5", "D6"))
                row["spherex_only_period_days"] = (a2.get("period") or {}).get("period_days")
            except ValueError:
                row["spherex_only_period_days"] = None
        rows.append(row)
        print(f"{c['name']}: P {row['period_days']} (lit {c.get('period')}) power {row['power']} FAP {row['fap']} "
              f"{wall:.0f} s, {row['classification']}", flush=True)
    return rows


def searches(fields: dict[str, str], out_dir: str, radius: float) -> list[dict]:
    rows = []
    for label, seq in fields.items():
        ctx = load_context(seq)
        t0 = time.perf_counter()
        res = search_field(ctx, SearchOptions(radius_arcsec=radius), lambda *a, **k: None, lambda: None)
        wall = time.perf_counter() - t0
        with open(f"{out_dir}/search_{seq}.json", "w") as f:
            json.dump(clean_json(res), f)
        try:
            known = vsx(ctx.grid.ra, ctx.grid.dec, radius + 20.0)
        except Exception as e:  # noqa: BLE001
            print("VSX unavailable:", e)
            known = None
        contaminated = [c for c in res["candidates"] if c.get("score") is not None and c.get("contamination")]
        measured = [c for c in res["candidates"] if c.get("score") is not None and not c.get("contamination")]
        flagged, fp, kv, kv_found = [], [], 0, 0
        for c in measured:
            match = None
            if known is not None:
                match = min(known, key=lambda k: _sep_arcsec(c["ra"], c["dec"], k["ra"], k["dec"]), default=None)
                if match and _sep_arcsec(c["ra"], c["dec"], match["ra"], match["dec"]) > VSX_MATCH_ARCSEC:
                    match = None
            hit = bool(c.get("variable") or c.get("periodic"))
            if match:
                kv += 1
                kv_found += hit
            if hit:
                flagged.append({"id": c["id"], "w1": c.get("w1"), "label": c["label"], "score": c["score"],
                                "period_days": c.get("period_days"), "saturated": c.get("saturated"),
                                "reasons": c.get("reasons"), "vsx": match})
                if not match:
                    fp.append(c["id"])
        n_const = len(measured) - kv
        row = {"field": label, "sequence": seq, "seconds": wall, "sources": res["summary"]["sources"],
               "measured": len(measured), "contaminated": len(contaminated),
               "contaminated_flagged": sum(bool(c.get("variable") or c.get("periodic")) for c in contaminated),
               "flagged": len(flagged), "vsx_known": kv, "vsx_recovered": kv_found,
               "false_positives": len(fp), "false_positive_rate": len(fp) / n_const if n_const else None,
               "flagged_sources": flagged, "ensemble": res.get("ensemble"), "notes": res.get("notes")}
        rows.append(row)
        print(f"{label}: {len(measured)} clean + {len(contaminated)} contaminated measured, {len(flagged)} flagged, "
              f"VSX {kv_found}/{kv}, FP {len(fp)} ({row['false_positive_rate']}), {wall:.0f} s", flush=True)
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", nargs="+")
    ap.add_argument("--search-only", action="store_true")
    ap.add_argument("--no-search", action="store_true")
    ap.add_argument("--out-dir", default="runs/validation")
    ap.add_argument("--field-radius", type=float, default=360.0, help="light curves: windows and calibration stars")
    ap.add_argument("--search-radius", type=float, default=360.0, help="arcsec (more clean sources than 150)")
    a = ap.parse_args()
    os.makedirs(a.out_dir, exist_ok=True)
    out: dict = {}
    if not a.search_only:
        cases = [c for c in CASES if not a.only or c["name"] in a.only]
        out["light_curves"] = light_curves(cases, a.out_dir, a.field_radius)
    if not a.no_search:
        fields = {k: v for k, v in SEARCH_FIELDS.items() if not a.only or k in a.only}
        out["searches"] = searches(fields, a.out_dir, a.search_radius)
        s = out["searches"]
        n_const = sum(r["measured"] - r["vsx_known"] for r in s)
        n_fp = sum(r["false_positives"] for r in s)
        out["false_positive_rate"] = {"sources": n_const, "false_positives": n_fp,
                                      "rate": n_fp / n_const if n_const else None,
                                      "upper_95": (n_fp + 1.96 * math.sqrt(max(n_fp, 1))) / n_const if n_const
                                      else None}
        print("false-positive rate:", out["false_positive_rate"])
    with open(f"{a.out_dir}/summary.json", "w") as f:
        json.dump(clean_json(out), f, indent=1)


if __name__ == "__main__":
    main()
