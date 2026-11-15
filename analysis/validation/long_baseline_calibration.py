"""Enclosed-energy curves, aperture corrections and the ring scale of the archival frames (ADR-0059).

For every field (a built sequence) the script reads the same frame windows as a long-baseline job (cached) and
measures catalogue stars on them with spherex_core.longbaseline:

1. Growth curves: isolated, unsaturated stars (NEOWISE / AllWISE W1 8.5-12, W2 8-11.5 from AllWISE; 2MASS
   J/H/Ks 9.5-13 from the PSC) within --radius of the field centre (default 360"). The expected flux of faint
   field sources (far-annulus mean minus median per pixel) is subtracted, since the median background leaves
   it in. Each frame's background-subtracted radial profile, summed outwards, is divided by
   the star's catalogue flux in that frame's DN (catalogue magnitude and the frame's MAGZP), so EE(r) is the
   fraction of the catalogue (total) flux within r. The aperture value uses the exact standard-aperture sum.
   Medians over each star's frames, then over stars (the outer curve from strictly isolated stars, no brighter
   neighbour within the profile); the star-to-star scatter of -2.5 log10 EE(r_ap) is the
   zero-point scatter of the frame photometry.
2. Ring scale: saturated stars (AllWISE W1 2-8, W2 1.5-7; 2MASS brighter than the image saturation) measured
   with the ring method and the new curves, against the catalogue magnitude.

Usage (on the server, where the sequences and the frame cache live):
    SPHEREX_CACHE_DIR=/workspace/cache python analysis/validation/long_baseline_calibration.py <seq> [<seq> ...] \
        [--out calibration.json]
It prints the tables to paste into backend/spherex_core/longbaseline/instruments.py.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import math
import time
from collections import defaultdict

import numpy as np

from spherex_core.analysis.common import load_context
from spherex_core.longbaseline import refcat
from spherex_core.longbaseline.archival_lc import Position, calibrate_frames, measure_all
from spherex_core.longbaseline.calibrate import _sep_arcsec
from spherex_core.longbaseline.frames import read_windows
from spherex_core.longbaseline.instruments import INSTRUMENTS
from spherex_core.longbaseline.lightcurve import MAX_WINDOW_RADIUS_ARCSEC, archival_images
from spherex_core.targets import Target
from spherex_core.timeutil import year_to_mjd

UNSAT = {"W1": (8.5, 12.0), "W2": (8.0, 11.5), "J": (9.5, 13.0), "H": (9.0, 12.5), "Ks": (9.0, 12.5)}
SAT = {"W1": (2.0, 7.5), "W2": (1.5, 6.5), "J": (4.0, 8.5), "H": (3.5, 8.0), "Ks": (3.0, 7.5)}
WISE_COL = {"W1": "w1", "W2": "mag"}
TM_COL = {"J": "j", "H": "h", "Ks": "mag"}


def _isolated(s, rows, mag_key, near_arcsec, near_dm, far_arcsec, far_dm):
    m = s[mag_key]
    for o in rows:
        if o is s or o.get(mag_key) is None:
            continue
        d = _sep_arcsec(s["ra"], s["dec"], o["ra"], o["dec"])
        if d < near_arcsec and o[mag_key] < m + near_dm:
            return False
        if d < far_arcsec and o[mag_key] < m - far_dm:
            return False
    return True


def _growth(rec, cat_mag, inst):
    """EE at radii 1..profile_px (flux within r < k + 1) and at the standard aperture, or None."""
    pr = rec["profile"]
    ntot, nval, sums = pr["ntotal"].astype(float), pr["nvalid"].astype(float), pr["sums"]
    k = np.arange(ntot.size)
    full = np.pi * ((k + 1) ** 2 - k ** 2)
    if (np.any(ntot[2:] < 0.85 * full[2:]) or np.any(nval[2:] < 0.7 * ntot[2:]) or rec["core_r"] > 0
            or rec["masked_frac"] > 0.05):
        return None
    f_cat = 10 ** (-0.4 * (cat_mag - rec["magzp"]))
    # minus the expected flux of faint field sources (mean - median of the far annulus, per pixel), which a
    # median background leaves in and which would make EE grow ~ r^2 for faint calibration stars
    cum = np.cumsum(sums * ntot / np.maximum(nval, 1) - pr.get("far_excess", 0.0) * ntot)
    return cum / f_cat, rec["flux_dn"] / f_cat


def field(seq: str, out: dict, radius: float) -> None:
    t0 = time.perf_counter()
    ctx = load_context(seq)
    g = ctx.grid
    notes: list[str] = []
    r_deg = radius / 3600.0
    wise = refcat.allwise(g.ra, g.dec, r_deg)
    psc, _ = refcat.twomass_reference(g.ra, g.dec, r_deg, notes)
    tgt = ctx.meta["target"]
    target = Target(ra=float(tgt["ra"]), dec=float(tgt["dec"]), pmra=float(tgt.get("pmra") or 0),
                    pmdec=float(tgt.get("pmdec") or 0), ref_epoch=float(tgt.get("ref_epoch") or 2000))
    track = [target.position_at(year_to_mjd(y)) for y in np.linspace(1998, 2026, 15)]

    def away(s):
        return all(_sep_arcsec(s["ra"], s["dec"], a, b) > 60.0 for a, b in track) and \
            _sep_arcsec(s["ra"], s["dec"], g.ra, g.dec) < radius - 12.0

    stars: dict[str, dict] = {}
    wise = [s for s in wise if s.get("w1") is not None and s.get("mag") is not None and away(s)]
    for s in wise:
        unsat = any(UNSAT[b][0] <= s[WISE_COL[b]] <= UNSAT[b][1] for b in ("W1", "W2"))
        sat = any(SAT[b][0] <= s[WISE_COL[b]] <= SAT[b][1] for b in ("W1", "W2"))
        if unsat and s.get("ccf") in (None, "", "0000") and not s.get("extended") and \
                _isolated(s, wise, "w1", 16.0, 2.5, 90.0, 2.0):
            stars[s["id"]] = {"kind": "wise", "row": s, "sat": False,
                              "strict": _isolated(s, wise, "w1", 70.0, 3.0, 150.0, 3.0)}
        elif sat and _isolated(s, wise, "w1", 40.0, 0.0, 150.0, 1.0):
            stars[s["id"]] = {"kind": "wise", "row": s, "sat": True}
    psc = [s for s in psc if s.get("j") is not None and s.get("h") is not None and s.get("mag") is not None and away(s)]
    for s in psc:
        unsat = UNSAT["J"][0] <= s["j"] <= UNSAT["J"][1] and UNSAT["Ks"][0] <= s["mag"] <= UNSAT["Ks"][1]
        sat = SAT["Ks"][0] <= s["mag"] <= SAT["Ks"][1]
        if unsat and _isolated(s, psc, "mag", 8.0, 2.5, 30.0, 2.0):
            stars[s["id"]] = {"kind": "2mass", "row": s, "sat": False,
                              "strict": _isolated(s, psc, "mag", 25.0, 3.0, 60.0, 2.0)}
        elif sat and _isolated(s, psc, "mag", 25.0, 0.0, 60.0, 1.0):
            stars[s["id"]] = {"kind": "2mass", "row": s, "sat": True}
    positions = [Position(k, Target(ra=v["row"]["ra"], dec=v["row"]["dec"], ref_epoch=v["row"].get("epoch") or 2000))
                 for k, v in stars.items()]
    surveys = ("2mass", "allwise", "neowise")
    images = archival_images(g, surveys)
    windows, stats = asyncio.run(read_windows(images, g.ra, g.dec, radius, None, lambda: None))
    per_frame = measure_all(windows, positions)
    t_meas = time.perf_counter() - t0

    growth: dict[tuple, list] = defaultdict(list)  # (survey, band) -> [(star, ee[], ap)]
    ring: dict[tuple, list] = defaultdict(list)  # (survey, band) -> [(star, cat, recs)]
    for key, recs in per_frame.items():
        st = stars[key]
        by = defaultdict(list)
        for r in recs:
            by[(r["survey"], r["band"])].append(r)
        for (survey, band), rr in by.items():
            if (st["kind"] == "wise") != (band in ("W1", "W2")):
                continue
            cat = st["row"][WISE_COL[band]] if band in WISE_COL else st["row"][TM_COL[band]]
            if cat is None:
                continue
            if st["sat"] or not (UNSAT[band][0] <= cat <= UNSAT[band][1]):
                if SAT[band][0] <= cat <= SAT[band][1]:
                    ring[(survey, band)].append((key, cat, rr))
                continue
            gs = [x for x in (_growth(r, cat, INSTRUMENTS[(survey, band)]) for r in rr) if x is not None]
            if len(gs) >= 1:
                growth[(survey, band)].append((key, np.median([x[0] for x in gs], axis=0),
                                               float(np.median([x[1] for x in gs])), len(gs), st["strict"], cat))
    out[seq] = {"name": tgt.get("name"), "stars": len(stars), "frames": len(windows), "seconds": t_meas,
                "growth": {f"{s}:{b}": [(k, ee.tolist(), ap, n, strict, cat) for k, ee, ap, n, strict, cat in v]
                           for (s, b), v in growth.items()},
                "ring": {f"{s}:{b}": [(k, cat, [{kk: r[kk] for kk in ("flux_dn", "err_dn", "core_r", "magzp",
                                                                       "sat_in_aperture", "masked_frac", "mjd")}
                                                 | {"profile": {pk: (pv.tolist() if hasattr(pv, "tolist") else pv)
                                                                for pk, pv in r["profile"].items()}}
                                                 for r in rr]) for k, cat, rr in v]
                         for (s, b), v in ring.items()},
                "notes": notes}
    n_growth = {f"{s}:{b}": len(v) for (s, b), v in growth.items()}
    n_ring = {f"{s}:{b}": len(v) for (s, b), v in ring.items()}
    print(f"{seq} {tgt.get('name')}: {len(stars)} stars, {len(windows)} windows, {t_meas:.0f} s; growth {n_growth} "
          f"ring {n_ring}", flush=True)


def _theil_sen_intercepts(u: np.ndarray, x: np.ndarray) -> np.ndarray:
    """Per radius (columns of x), the intercept at u = 0 of the Theil-Sen line through (u, x[:, k])."""
    i, j = np.triu_indices(u.size, 1)
    du = u[j] - u[i]
    keep = np.abs(du) > 1e-6
    out = np.empty(x.shape[1])
    for k in range(x.shape[1]):
        slope = float(np.median((x[j, k] - x[i, k])[keep] / du[keep])) if keep.any() else 0.0
        out[k] = float(np.median(x[:, k] - slope * u))
    return out


def summarise(fields: dict) -> dict:
    res: dict = {}
    keys = sorted({k for f in fields.values() for k in f["growth"]} | {k for f in fields.values() for k in f["ring"]})
    for key in keys:
        survey, band = key.split(":")
        inst = INSTRUMENTS[(survey, band)]
        rows = [r for f in fields.values() for r in f["growth"].get(key, [])]
        entry: dict = {"stars": len(rows)}
        if len(rows) >= 3:
            strict = [r for r in rows if r[4]]
            ee = np.array([r[1] for r in (strict if len(strict) >= 5 else rows)])  # outer curve: strictly isolated
            ap = np.array([r[2] for r in rows])
            # diagnostic: if neighbours added flux independent of the star, EE_s(r) = EE(r) + B(r) / F_s per field and
            # the intercept at 1 / F = 0 would be the uncontaminated curve. On the validation fields it rises even
            # faster than the median (few stars, noisy extrapolation), so the tables use the median curve.
            fits = []
            for f in fields.values():
                fr = f["growth"].get(key, [])
                if len(fr) < 5:
                    continue
                u = np.array([10 ** (0.4 * (r[5] - 10.0)) for r in fr])
                x = np.array([r[1] for r in fr])
                fits.append((len(fr), _theil_sen_intercepts(u, x)))
            if fits:
                w = np.array([n for n, _ in fits], float)
                icpt = np.array([c for _, c in fits])
                entry["ee_intercept"] = (w[:, None] * icpt).sum(0) / w.sum()
                entry["intercept_fields"] = len(fits)
                entry["intercept_spread"] = (icpt.max(0) - icpt.min(0)).tolist() if len(fits) > 1 else None
            apm = -2.5 * np.log10(np.clip(ap, 1e-6, None))
            curve = np.median(ee, axis=0)
            if "ee_intercept" in entry:
                entry["ee_intercept"] = entry["ee_intercept"].tolist()
            entry.update(
                ee_profile=curve.tolist(),
                ee_ap=float(np.median(ap)),
                apcorr_mag=float(np.median(apm)),
                apcorr_scatter_mag=float(1.4826 * np.median(np.abs(apm - np.median(apm)))),
                apcorr_err_mag=float(1.2533 * 1.4826 * np.median(np.abs(apm - np.median(apm))) / math.sqrt(len(apm))),
                ee_last=float(curve[-1]),
                frames=int(sum(r[3] for r in rows)), strict_stars=len(strict),
                outer_from="median of the strictly isolated stars" if len(strict) >= 5 else "median of all stars",
            )
            # table: exact aperture value at r_ap, profile sums elsewhere, monotonic
            radii = [r for r in TABLE_R[survey] if r != inst.r_ap_px]
            pairs = sorted([(inst.r_ap_px, entry["ee_ap"])] + [(r, float(curve[int(r) - 1])) for r in radii])
            ee_t = np.maximum.accumulate([p[1] for p in pairs])
            entry["table_r"] = [p[0] for p in pairs]
            entry["table_ee"] = [round(float(x), 4) for x in ee_t]
        res[key] = entry
    # ring scale with the new curves
    for key in keys:
        survey, band = key.split(":")
        e = res.setdefault(key, {})
        if "table_ee" not in e:
            continue
        inst = dataclasses.replace(INSTRUMENTS[(survey, band)], ee_r=tuple(e["table_r"]), ee=tuple(e["table_ee"]))
        deltas = []
        for f in fields.values():
            for star, cat, rr in f["ring"].get(key, []):
                recs = []
                for r in rr:
                    pr = {k: np.asarray(v) if isinstance(v, list) else v for k, v in r["profile"].items()}
                    recs.append({**r, "profile": pr, "file": ""})
                frames, info = calibrate_frames(recs, inst)
                fl = [x["flux_mjy"] for x in frames if x["flux_mjy"] > 0 and not x["flagged"]]
                if info.get("method") != "ring" or len(fl) < 3:
                    continue
                mag = -2.5 * math.log10(float(np.median(fl)) * 1e-3 / inst.f0_jy)
                deltas.append((star, cat, cat - mag, info.get("r_in_px"), len(fl)))
        if deltas:
            d = np.array([x[2] for x in deltas])
            e["ring"] = {"stars": len(d), "offset_mag": float(np.median(d)),
                         "scatter_mag": float(1.4826 * np.median(np.abs(d - np.median(d)))) if d.size >= 3 else None,
                         "rows": deltas}
    return res


TABLE_R = {"neowise": (1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 8.0, 10.0, 12.0, 14.0, 16.0, 18.0, 20.0, 22.0, 24.0),
           "allwise": (2.0, 4.0, 6.0, 8.0, 10.0, 12.0, 16.0, 20.0, 24.0, 28.0, 32.0, 36.0, 40.0, 44.0, 48.0),
           "2mass": (1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 8.0, 10.0, 12.0, 14.0, 18.0, 22.0, 26.0, 30.0)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sequences", nargs="*")
    ap.add_argument("--out", default="long_baseline_calibration.json")
    ap.add_argument("--radius", type=float, default=MAX_WINDOW_RADIUS_ARCSEC, help="arcsec around the field centre")
    ap.add_argument("--fields", nargs="+", help="reuse saved per-field files (merged) instead of measuring")
    a = ap.parse_args()
    if a.fields:
        fields = {}
        for path in a.fields:
            fields.update(json.load(open(path)))
    else:
        fields = {}
        for seq in a.sequences:
            try:
                field(seq, fields, a.radius)
            except Exception as e:  # noqa: BLE001 - next field
                print(f"{seq}: {e}")
        json.dump(fields, open(a.out.replace(".json", "_fields.json"), "w"))
    res = summarise(fields)
    for key, e in res.items():
        line = f"{key:12s} stars {e.get('stars', 0):3d}"
        if "apcorr_mag" in e:
            line += (f"  EE(r_ap) {e['ee_ap']:.3f} = {e['apcorr_mag']:.3f} mag +- {e['apcorr_err_mag']:.3f} "
                     f"(scatter {e['apcorr_scatter_mag']:.3f}), EE(last) {e['ee_last']:.3f}, frames {e['frames']}")
        if "ring" in e:
            line += (f"\n{'':12s} ring: {e['ring']['stars']} stars, offset {e['ring']['offset_mag']:+.3f} "
                     f"scatter {e['ring']['scatter_mag']}")
        if "outer_from" in e:
            line += f"\n{'':12s} outer curve: {e['outer_from']} ({e['strict_stars']} strictly isolated)"
        print(line)
        if "table_ee" in e:
            print(f"{'':12s} r  = {tuple(e['table_r'])}\n{'':12s} EE = {tuple(e['table_ee'])}")
    json.dump({"fields": {k: {kk: v[kk] for kk in ("name", "stars", "frames", "seconds", "notes")}
                          for k, v in fields.items()}, "summary": res}, open(a.out, "w"), default=float)


if __name__ == "__main__":
    main()
