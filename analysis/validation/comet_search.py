"""Validation of the comet search (ADR-0063), run in this process on sequences already in the cache.

Object checks (co-moving sequences, spherex_core.comets.hunt.object_check):
  positives  3I/ATLAS (C/2025 N1, CO2-rich interstellar comet), C/2025 A6 (Lemmon)
  negatives  433 Eros (S-type near-Earth asteroid), 16 Psyche (M-type main-belt asteroid)
Field hunts (fixed-position sequences): every candidate of a field without a known comet that scores >= 50 counts
as a false positive; known asteroids that the search finds must score low; runtimes per field.

Usage (server):
    SPHEREX_CACHE_DIR=/workspace/cache python analysis/validation/comet_search.py [--objects ID ...]
        [--fields ID ...] [--no-identify] [--out runs/comet_validation.json]
"""

from __future__ import annotations

import argparse
import json
import time

OBJECTS = {"3b76226d959c647a930e": ("3I/ATLAS", True), "f6575736eb428e9f937b": ("C/2025 A6 (Lemmon)", True),
           "f593e741d7e4bf9c628a": ("433 Eros", False), "edddd3238611033f7666": ("16 Psyche", False)}
FIELDS = {"5344660567070b76cde7": "Klotho field (97 Klotho crosses it)", "4261e28d6e0c3cb6df94": "Barnard's Star",
          "cb983e7485da07d622a4": "RW Aur", "d651cb1431ed20027191": "eps Indi B (16')"}


def object_row(seq: str, label: str, positive: bool, opts) -> dict:
    from spherex_core.analysis.common import load_context
    from spherex_core.comets.hunt import comet_search

    t = time.time()
    res = comet_search(load_context(seq), opts)
    visits = []
    for v in res["candidates"]["comets"]:
        cm, sp, orb = v["coma"], v["spectrum"], v["orbit"] or {}
        co2 = next((b for b in sp.get("bands", []) if b["id"] == "co2"), {})
        visits.append({"visit": v["epoch"] + 1, "n": v["n_exposures"], "r_au": v.get("r_au"), "delta_au": v.get("delta_au"),
                       "coma": cm.get("verdict"), "extent_sigma": cm.get("extent_sigma"), "snr": cm.get("snr"),
                       "fwhm": (cm.get("size") or {}).get("fwhm_arcsec"), "psf_fwhm": (cm.get("size") or {}).get("psf_fwhm_arcsec"),
                       "tail": (cm.get("tail") or {}), "co2_snr": co2.get("snr"), "gas": sp.get("verdict"),
                       "orbit": orb.get("most_likely"), "p_comet_orbit": orb.get("comet_like"),
                       "score": v["comet_likeness"]["score"], "terms": v["comet_likeness"]["terms"]})
    return {"sequence": seq, "object": label, "positive": positive, "seconds": round(time.time() - t, 1),
            "image_source": res.get("image_source"), "psf": res.get("psf"), "visits": visits}


def _progress(label: str):
    t0, last = time.time(), [None]

    def progress(stage, done=0, total=0, steps=None):
        if stage != last[0]:
            last[0] = stage
            print(f"  [{label}] {time.time() - t0:7.1f} s  {stage}", flush=True)

    return progress


def field_row(seq: str, label: str, opts) -> dict:
    from spherex_core.analysis.common import load_context
    from spherex_core.comets.hunt import comet_search

    t = time.time()
    res = comet_search(load_context(seq), opts, _progress(label))
    cands = res["candidates"]["comets"]
    known = [c for c in cands if c.get("known")]
    fp = [c for c in cands if c["comet_likeness"]["score"] >= 50 and not (c.get("known") or {}).get("kind") == "comet"]
    return {"sequence": seq, "field": label, "seconds": round(time.time() - t, 1), "counts": res["counts"],
            "stats_seconds": res["stats"]["seconds"], "slow": {k: res["stats"]["slow"][k] for k in ("links", "stack_hits")},
            "stack_trials": sum(s.get("trials", 0) for s in res["stats"]["slow"]["stack"]),
            "false_positives": len(fp), "observer": res["observer"],
            "top": [{"id": c["id"], "kind": c["kind"], "score": c["comet_likeness"]["score"], "coma": c["coma"].get("verdict"),
                     "extent_sigma": c["coma"].get("extent_sigma"), "rate": c["rate_arcsec_per_hr"],
                     "orbit": (c["orbit"] or {}).get("most_likely"), "known": (c.get("known") or {}).get("name"),
                     "status": c["status"]} for c in cands[:8]],
            "known": [{"id": c["id"], "name": c["known"]["name"], "kind": c["known"].get("kind"),
                       "score": c["comet_likeness"]["score"], "coma": c["coma"].get("verdict")} for c in known]}


def main() -> None:
    from spherex_core.comets.hunt import HuntOptions

    ap = argparse.ArgumentParser()
    ap.add_argument("--objects", nargs="*", default=None)
    ap.add_argument("--fields", nargs="*", default=None)
    ap.add_argument("--no-identify", action="store_true")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    opts = HuntOptions(identify=not a.no_identify)
    out = {"objects": [], "fields": []}
    for seq in (a.objects if a.objects is not None else OBJECTS):
        label, pos = OBJECTS.get(seq, (seq, None))
        row = object_row(seq, label, pos, opts)
        out["objects"].append(row)
        print(json.dumps(row, default=str)[:3000], flush=True)
    for seq in (a.fields if a.fields is not None else FIELDS):
        row = field_row(seq, FIELDS.get(seq, seq), opts)
        out["fields"].append(row)
        print(json.dumps(row, default=str)[:3000], flush=True)
    if a.out:
        with open(a.out, "w") as f:
            json.dump(out, f, indent=1, default=str)


if __name__ == "__main__":
    main()
