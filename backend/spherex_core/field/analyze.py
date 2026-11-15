"""'Analyze this field': every automated search over one built sequence, in one result.

Steps (each optional): source detection -> catalog cross-match -> motion fits for every bright source ->
shift-and-stack for faint movers -> intra-visit moving objects (tracklets, known-object check) -> variability
-> colour searches (red excess, anomalies). The result is a JSON document of the whole field plus ranked
candidate lists; every candidate carries its position (grid pixels and RA/Dec), a score and the evidence.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Callable

from ..analysis.common import SequenceContext
from . import colours, crossmatch, known, movers, realbogus, sources, transients, variability
from .common import Check, load_field, no_check

FIELD_VERSION = 1
Progress = Callable[[str, int, int], None]
STEPS = ("detect", "crossmatch", "motion", "stack", "transients", "variability", "colours")


@dataclass(frozen=True)
class FieldOptions:
    snr_min: float = 5.0
    max_sources: int = 300
    catalogs: bool = True
    motion: bool = True
    shift_stack: bool = True
    transients: bool = True
    known_objects: bool = True
    variability: bool = True
    colours: bool = True
    vmag_lim: float = 21.0

    def to_dict(self) -> dict:
        return asdict(self)


def analyze_field(ctx: SequenceContext, opts: FieldOptions, progress: Progress | None = None,
                  check: Check = no_check) -> dict:
    """Blocking; call in a worker thread. `check` raises FieldCancelled when the job should stop."""
    report = progress or (lambda stage, done, total: None)
    t_all = time.perf_counter()
    secs: dict[str, float] = {}

    def step(i: int, name: str):
        report(name, i, len(STEPS))
        check()
        return time.perf_counter()

    t = step(0, "detect")
    fd = load_field(ctx)
    srcs = sources.detect(fd, opts.snr_min, opts.max_sources, check)
    secs["detect"] = time.perf_counter() - t

    cat_stats: dict = {}
    if opts.catalogs:
        t = step(1, "crossmatch")
        cat_stats = crossmatch.crossmatch(fd, srcs)
        secs["crossmatch"] = time.perf_counter() - t

    mover_c: list[dict] = []
    if opts.motion:
        t = step(2, "motion")
        mover_c = movers.fit_all(fd, srcs, check)
        secs["motion"] = time.perf_counter() - t

    faint: list[dict] = []
    if opts.shift_stack:
        t = step(3, "stack")
        faint = movers.shift_and_stack(fd, srcs, mover_c, check)
        secs["stack"] = time.perf_counter() - t

    tr: dict = {"tracklets": [], "pairs": [], "stats": {}}
    if opts.transients:
        t = step(4, "transients")
        dets, dstats = transients.detect(fd, check)
        tracklets, pairs = transients.link(dets, fd.grid.pixscale)
        t_id = time.perf_counter()
        ident = known.identify(fd, tracklets + pairs, opts.vmag_lim) if opts.known_objects else {}
        secs["known_objects"] = time.perf_counter() - t_id
        tr = {"tracklets": tracklets[:100], "pairs": pairs[:30],
              "stats": {**dstats, "tracklets": len(tracklets), "pairs": len(pairs), "identification": ident,
                        "detections_rb_over_0.5": sum(d["rb"] >= 0.5 for d in dets)}}
        secs["transients"] = time.perf_counter() - t

    var_c: list[dict] = []
    if opts.variability:
        t = step(5, "variability")
        var_c = variability.search(fd, srcs, check)
        secs["variability"] = time.perf_counter() - t

    red_c, anom_c, anom_info = [], [], {}
    if opts.colours:
        t = step(6, "colours")
        red_c = colours.merge_red(colours.red_excess(srcs),
                                  colours.red_excess_map(fd, sources.deep_images(fd), srcs))
        anom_c, anom_info = colours.anomalies(srcs)
        secs["colours"] = time.perf_counter() - t
    report("done", len(STEPS), len(STEPS))

    model = realbogus.load_model()
    secs["total"] = time.perf_counter() - t_all
    for s in srcs:
        s.pop("motion_track", None)
    return {
        "sequence": ctx.seq_id, "version": FIELD_VERSION,
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "options": opts.to_dict(), "target": ctx.meta.get("target", {}).get("name"),
        "grid": ctx.meta["grid"],
        "epochs": [{"index": e["index"], "year": float(y), "date_min": e["date_min"], "date_max": e["date_max"]}
                   for e, y in zip(ctx.meta["epochs"], fd.years)],
        "reference_year": fd.t_ref,
        "sources": srcs,
        "candidates": {"movers": mover_c, "faint_movers": faint, "tracklets": tr["tracklets"],
                       "pairs": tr["pairs"], "variables": var_c, "red": red_c, "anomalies": anom_c},
        "stats": {"sources": len(srcs), "catalogs": cat_stats, "transients": tr["stats"], "anomaly": anom_info,
                  "seconds": secs},
        "realbogus": {"model": "boosted stumps" if model else "heuristic (no trained model installed)",
                      "validation": (model or {}).get("validation"),
                      "importance": realbogus.importance(model) if model else None},
        "notes": fd.notes + [
            "Co-add fluxes are approximate (aperture r = 2 px with a nominal correction); use the per-exposure "
            "ePSF photometry for calibrated spectra.",
            "With 3-4 visits a motion error of ~300 mas/yr is typical; movers below ~1.2\"/yr are not significant.",
            "Ring photometry of saturated stars is relative and drifts up to ~14 % on constant stars.",
        ],
    }
