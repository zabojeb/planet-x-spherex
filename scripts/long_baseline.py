"""Long-baseline light curve (2MASS, AllWISE, NEOWISE + SPHEREx) of a built sequence's target or a position, or the
field-wide long-period search, run in this process (no API) with a summary on stdout.

    python scripts/long_baseline.py <sequence id> [--ra RA --dec DEC] [--out result.json] [--png curve.png]
    python scripts/long_baseline.py <sequence id> --search [--out search.json]
"""

from __future__ import annotations

import argparse
import json
import time

from spherex_core.analysis.common import load_context
from spherex_core.longbaseline import LongBaselineRequest, SearchOptions, run, search_field
from spherex_core.pipeline.results import clean_json


def progress(stage: str, done: int, total: int, steps=None) -> None:
    if total and done not in (total,) and done % max(total // 10, 1):
        return
    now = time.strftime("%H:%M:%S")
    print(f"  {now} {stage} {done}/{total}" if total else f"  {now} {stage}", flush=True)


def summary(res: dict) -> None:
    a = res.get("analysis") or {}
    print(f"\n{res['target'].get('name')}: {res['coverage']['first_year']:.1f}-{res['coverage']['last_year']:.1f}"
          f" ({res['coverage']['baseline_years']:.1f} yr)")
    for b, v in res["coverage"]["bands"].items():
        print(f"  {b:3s} {v['n']:4d} points  {', '.join(v['surveys'])}")
    print("methods:", {k: v.get("method") for k, v in res.get("methods", {}).items()})
    for k, v in (res["calibration"].get("frames") or {}).items():
        if v.get("n"):
            print(f"  frames {k}: {v['offset_mag']:+.3f} mag, scatter {v['scatter_mag']}, n={v['n']}, "
                  f"applied={v['applied']}")
    for k, v in (res["calibration"].get("spherex") or {}).items():
        print(f"  SPHEREx {k}: used {v.get('used_offset_mag', 0):+.3f} ({v.get('source')}), "
              f"field {v.get('offset_mag')}, scatter {v.get('scatter_mag')}, n={v.get('n')}")
    p = a.get("period") or {}
    if p:
        print(f"period {p['period_days']:.1f} +- {p.get('period_err_days') or 0:.1f} d, power {p['power']:.2f}, "
              f"FAP {p['false_alarm_probability']:.1e}, significant {p['significant']}, amplitudes "
              f"{ {k: round(v, 2) for k, v in p['amplitudes_mag'].items()} }")
        print("  peaks:", [round(x["period_days"], 1) for x in p["peaks"]])
    print("trends:", {b: (round(t["slope_mag_per_year"], 3), round(t["significance"] or 0, 1))
                      for b, t in (a.get("trends") or {}).items()})
    print("events:", [(e["kind"], round(e["start_mjd"]), round(e["end_mjd"]), round(e["peak_mag"], 2), e["bands"])
                      for e in a.get("events", [])])
    print("verdict:", (a.get("classification") or {}).get("label"), (a.get("classification") or {}).get("reasons"))
    print("seconds:", {k: round(v, 1) for k, v in res["stats"]["seconds"].items()}, "frames:",
          {k: v for k, v in res["stats"]["frames"].items() if k != "errors"})
    if res.get("notes"):
        print("notes:", res["notes"])


def plot(res: dict, png: str) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    pts = [p for p in res["points"] if p.get("mag_anchored") is not None]
    bands = [b for b in ("J", "H", "Ks", "W1", "W2", "D1", "D3", "D5", "D6") if any(p["band"] == b for p in pts)]
    fig, axes = plt.subplots(len(bands), 1, figsize=(9, 1.7 * len(bands)), sharex=True, squeeze=False)
    colours = {"2MASS": "tab:green", "AllWISE": "tab:purple", "NEOWISE": "tab:blue", "SPHEREx": "tab:red"}
    for ax, b in zip(axes[:, 0], bands):
        for s, c in colours.items():
            q = [p for p in pts if p["band"] == b and p["survey"] == s]
            if q:
                ax.errorbar([p["year"] for p in q], [p["mag_anchored"] for p in q], [p["mag_err"] or 0 for p in q],
                            fmt="o", ms=3, color=c, label=s, lw=0.8)
        ax.invert_yaxis()
        ax.set_ylabel(b)
    axes[0, 0].legend(fontsize=7, ncol=4)
    p = (res.get("analysis") or {}).get("period") or {}
    name = res["target"].get("name")
    axes[0, 0].set_title(f"{name}  P = {p.get('period_days', 0):.1f} d" if p else name)
    fig.tight_layout()
    fig.savefig(png, dpi=110)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sequence")
    ap.add_argument("--ra", type=float)
    ap.add_argument("--dec", type=float)
    ap.add_argument("--pmra", type=float)
    ap.add_argument("--pmdec", type=float)
    ap.add_argument("--ref-epoch", type=float, default=2000.0)
    ap.add_argument("--no-calibrate", action="store_true")
    ap.add_argument("--min-period", type=float)
    ap.add_argument("--max-period", type=float)
    ap.add_argument("--search", action="store_true", help="field-wide long-period search instead")
    ap.add_argument("--max-sources", type=int, default=150)
    ap.add_argument("--out")
    ap.add_argument("--png")
    a = ap.parse_args()
    ctx = load_context(a.sequence)
    t0 = time.perf_counter()
    if a.search:
        res = search_field(ctx, SearchOptions(max_sources=a.max_sources), progress, lambda: None)
        print(json.dumps(res["summary"], indent=1))
        for c in res["candidates"][:15]:
            print(f"  {c['id']:32s} W1 {c.get('w1') or 0:5.2f} {c.get('label'):24s} score {c.get('score') or 0:5.2f} "
                  f"P {c.get('period_days') or 0:7.1f} FAP {c.get('period_fap') or 1:.1e} range {c.get('range_mag')}")
    else:
        req = LongBaselineRequest(ra=a.ra, dec=a.dec, pmra=a.pmra, pmdec=a.pmdec, ref_epoch=a.ref_epoch,
                                  calibrate=not a.no_calibrate, min_period=a.min_period, max_period=a.max_period)
        res = run(ctx, req, progress, lambda: None)
        summary(res)
        if a.png:
            plot(res, a.png)
    print(f"total {time.perf_counter() - t0:.1f} s")
    if a.out:
        with open(a.out, "w") as f:
            json.dump(clean_json(res), f)


if __name__ == "__main__":
    main()
