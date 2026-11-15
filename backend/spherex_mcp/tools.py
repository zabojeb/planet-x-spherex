"""The MCP tool layer as plain functions over spherex_core.services (no HTTP round trips).

Every function returns JSON-serialisable data kept small enough for an LLM context, or PNG bytes for images.
Long work (sequence builds, field analysis, known objects, track & stack searches, archival epochs, MOST
searches) is start/poll: a start tool returns a job id at once and the agent polls a status tool. While a job
runs, a keep-alive heartbeats it on the agent's behalf, so a slow-thinking agent does not lose its build to the
server's "nobody is waiting" cancellation; the agent can cancel explicitly, and the keep-alive gives up after
KEEPALIVE_MAX seconds. Caps in `Limits` stop a careless agent from flooding the queue.
"""

from __future__ import annotations

import asyncio
import json
import math
import time
from collections import deque
from dataclasses import dataclass, field

import numpy as np

from spherex_core import services
from spherex_core.archive.spherex import query_frames
from spherex_core.bands import BANDS
from spherex_core.errors import NotFound
from spherex_core.pipeline.params import SequenceParams

from . import images

CLIENT = "mcp-agent"
KEEPALIVE_EVERY = 10.0
KEEPALIVE_MAX = 30 * 60.0
LIST_LIMIT = 12  # candidates returned per category by default


class ToolError(Exception):
    """A user-facing error: the message is returned to the agent as the tool's error text."""


# ---------------------------------------------------------------- limits and keep-alive
@dataclass
class Limits:
    max_active_builds: int = 3
    builds_per_hour: int = 30
    max_active_analyses: int = 2
    max_fov_arcmin: float = 20.0
    _builds: deque = field(default_factory=deque)

    def check_build(self, active: int) -> None:
        now = time.time()
        while self._builds and now - self._builds[0] > 3600:
            self._builds.popleft()
        if active >= self.max_active_builds:
            raise ToolError(f"{active} builds already running for agents (limit {self.max_active_builds}). "
                            "Poll sequence_status until one finishes, or cancel_job one you no longer need.")
        if len(self._builds) >= self.builds_per_hour:
            raise ToolError(f"Hourly build limit reached ({self.builds_per_hour}). Analyse the sequences you have.")
        self._builds.append(now)


LIMITS = Limits()


@dataclass
class _Watched:
    kind: str  # "sequence" | "field"
    started: float = field(default_factory=time.monotonic)
    task: asyncio.Task | None = None


_WATCHED: dict[str, _Watched] = {}


async def _status(kind: str, job_id: str) -> tuple[str, dict | None]:
    if kind == "sequence":
        return await services.poll_sequence(job_id, client=CLIENT)
    return await services.field_status(job_id, client=CLIENT)


def _keepalive(kind: str, job_id: str) -> None:
    """Heartbeat a job until it ends, is cancelled, or KEEPALIVE_MAX passes."""
    w = _WATCHED.get(job_id)
    if w and w.task and not w.task.done():
        return
    w = _Watched(kind)

    async def loop() -> None:
        try:
            while time.monotonic() - w.started < KEEPALIVE_MAX:
                st, _ = await _status(kind, job_id)
                if st in ("done", "error", "cancelled", "unknown"):
                    return
                await asyncio.sleep(KEEPALIVE_EVERY)
        except Exception:  # noqa: BLE001 - a dead keep-alive just lets the server-side timeout apply
            return
        finally:
            _WATCHED.pop(job_id, None)

    w.task = asyncio.get_running_loop().create_task(loop())
    _WATCHED[job_id] = w


def _active(kind: str) -> int:
    return sum(1 for w in _WATCHED.values() if w.kind == kind and w.task and not w.task.done())


# ---------------------------------------------------------------- helpers
def _r(x, nd: int = 4):
    """Round floats to `nd` significant digits; drop NaN."""
    if isinstance(x, float):
        if not math.isfinite(x):
            return None
        return float(f"{x:.{nd}g}")
    return x


_PRECISE = ("ra", "dec", "mjd", "t_ref", "x", "y", "lon", "lat")


def _precise_key(k) -> bool:
    """Coordinates and times must not lose precision (4 significant digits of an RA is 0.1 deg)."""
    k = str(k).lower()
    return any(k == p or k.startswith(p + "_") or k.endswith("_" + p) for p in _PRECISE)


def compact(obj, depth: int = 0, max_list: int = 6):
    """Shrink nested candidate records for an LLM: round floats (not coordinates or times), truncate long lists,
    drop big arrays."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if isinstance(v, (list, tuple)) and len(v) > 40:
                out[k] = f"<{len(v)} values omitted>"
                continue
            if _precise_key(k) and isinstance(v, (float, np.floating)):
                out[k] = round(float(v), 7) if math.isfinite(float(v)) else None
                continue
            out[k] = compact(v, depth + 1, max_list)
        return out
    if isinstance(obj, (list, tuple)):
        items = [compact(v, depth + 1, max_list) for v in obj[:max_list]]
        if len(obj) > max_list:
            items.append(f"... {len(obj) - max_list} more")
        return items
    if isinstance(obj, (np.floating, float)):
        return _r(float(obj))
    if isinstance(obj, np.integer):
        return int(obj)
    return obj


def _band_index(band: str) -> int:
    b = band.upper()
    if b not in BANDS:
        raise ToolError(f"band must be one of {', '.join(BANDS)}")
    return BANDS.index(b)


def _meta(seq_id: str) -> dict:
    try:
        return services.load_sequence(seq_id)
    except NotFound as e:
        raise ToolError(f"Sequence {seq_id} is not built (yet). Use build_sequence and sequence_status.") from e


# ---------------------------------------------------------------- targets and discovery
def list_presets() -> list[dict]:
    return [{k: t.get(k) for k in ("id", "name", "ra", "dec", "pmra", "pmdec", "ref_epoch", "fov_arcmin",
                                    "description")} for t in services.list_presets()]


def resolve_target(query: str) -> dict:
    try:
        return services.resolve_target(query)
    except LookupError as e:
        raise ToolError(str(e)) from e


async def search_observations(ra: float, dec: float) -> dict:
    frames = await asyncio.to_thread(query_frames, float(ra), float(dec))
    if not frames:
        return {"ra": ra, "dec": dec, "n_frames": 0, "note": "No SPHEREx images cover this position yet."}
    mjd = np.array([f.mjd for f in frames])
    order = np.argsort(mjd)
    visits, start, prev = [], mjd[order[0]], mjd[order[0]]
    count = 0
    for i in order:
        if mjd[i] - prev > 20:
            visits.append((start, prev, count))
            start, count = mjd[i], 0
        prev, count = mjd[i], count + 1
    visits.append((start, prev, count))
    from spherex_core.timeutil import mjd_to_iso

    per_band = {b: sum(1 for f in frames if f.band == b) for b in BANDS}
    per_coll: dict[str, int] = {}
    for f in frames:
        per_coll[f.collection] = per_coll.get(f.collection, 0) + 1
    return {"ra": ra, "dec": dec, "n_frames": len(frames), "per_detector": per_band, "per_collection": per_coll,
            "visits": [{"from": mjd_to_iso(a)[:10], "to": mjd_to_iso(b)[:10], "frames": n} for a, b, n in visits]}


# ---------------------------------------------------------------- sequences
async def build_sequence(ra: float, dec: float, pmra: float = 0.0, pmdec: float = 0.0, ref_epoch: float = 2000.0,
                         fov_arcmin: float = 6.0, name: str | None = None) -> dict:
    if not (0 <= ra < 360 and -90 <= dec <= 90):
        raise ToolError("ra must be in [0, 360) and dec in [-90, 90] degrees")
    fov = float(fov_arcmin)
    if not (1.0 <= fov <= LIMITS.max_fov_arcmin):
        raise ToolError(f"fov_arcmin must be between 1 and {LIMITS.max_fov_arcmin:g}")
    params = SequenceParams(ra=float(ra), dec=float(dec), pmra=float(pmra), pmdec=float(pmdec),
                            ref_epoch=float(ref_epoch), name=name, fov_arcmin=fov)
    st, _ = await services.poll_sequence(params.key(), client=CLIENT)
    if st == "done":
        return {"sequence_id": params.key(), "status": "done", "note": "Already built; call describe_sequence."}
    LIMITS.check_build(_active("sequence"))
    try:
        job = await services.submit_sequence(params, ip="mcp", client=CLIENT)
    except Exception as e:  # TooManyJobs / QueueFull
        raise ToolError(f"The server is busy: {e}. Try again in a minute.") from e
    if job.get("status") not in ("done", "error", "cancelled"):
        _keepalive("sequence", job["id"])
    return {"sequence_id": job["id"], "status": job.get("status"), "stage": job.get("stage"),
            "hint": "Poll sequence_status every 10-30 s; a new field takes ~20-60 s, cached ones are instant."}


async def sequence_status(sequence_id: str) -> dict:
    st, job = await services.poll_sequence(sequence_id, client=CLIENT)
    if st == "done":
        return {"sequence_id": sequence_id, "status": "done"}
    if st == "unknown":
        raise ToolError("Unknown sequence id; start it with build_sequence.")
    out = {"sequence_id": sequence_id, **{k: (job or {}).get(k) for k in ("status", "stage", "done", "total",
                                                                          "position", "error")}}
    if st in ("queued", "running"):
        _keepalive("sequence", sequence_id)
    return out


async def cancel_job(job_id: str) -> dict:
    """Cancel a sequence build or an analysis job this agent started."""
    w = _WATCHED.pop(job_id, None)
    if w and w.task:
        w.task.cancel()
    if "-" in job_id:
        res = await services.field_withdraw(job_id, client=CLIENT)
    else:
        res = await services.withdraw_sequence(job_id, client=CLIENT)
    return {"job_id": job_id, "withdrawn": True, "status": (res or {}).get("status")}


def describe_sequence(sequence_id: str) -> dict:
    m = _meta(sequence_id)
    ph = m.get("photometry", [])
    by_band = {}
    for b in BANDS:
        pts = [p for p in ph if p["band"] == b]
        good = [p["flux_mjy"] for p in pts if not p.get("flagged") and p.get("flux_mjy") is not None]
        by_band[b] = {"n": len(pts), "unflagged": len(good),
                      "median_flux_mjy": _r(float(np.median(good))) if good else None,
                      "relative": any(p.get("relative") for p in pts)}
    t = m["target"]
    return {
        "sequence_id": sequence_id,
        "target": {k: _r(t.get(k)) for k in ("name", "ra", "dec", "pmra", "pmdec", "ref_epoch")},
        "grid": {k: _r(m["grid"].get(k)) for k in ("ra", "dec", "n", "pixscale_arcsec", "fov_arcmin", "frame")},
        "epochs": [{"index": e["index"], "from": e["date_min"][:10], "to": e["date_max"][:10], "frames": e["n"],
                    "partial": e.get("partial", False), "detectors": {b: v["n"] for b, v in e["bands"].items()}}
                   for e in m["epochs"]],
        "frames_used": m["stats"].get("frames_used"),
        "target_photometry_by_detector": by_band,
        "bands": [{"id": b["id"], "wl_um": [b["wl_min"], b["wl_max"]]} for b in m["bands"]],
        "notes": ("Grid pixel (0,0) is the south-east corner; images are north up, east left. "
                  "Use get_image / get_blink_strip to look, analyze_field to search, analyze_point to verify."),
    }


# ---------------------------------------------------------------- images
def _cube(sequence_id: str) -> tuple[dict, np.ndarray]:
    m = _meta(sequence_id)
    return m, services.read_array(sequence_id, "coadds")


def _all_bands(c: np.ndarray, e: int) -> np.ndarray:
    planes = []
    for b in range(c.shape[1]):
        p = c[e, b]
        if np.isfinite(p).any():
            s = np.nanstd(p)
            planes.append(p / s if s > 0 else p)
    return np.nanmean(np.stack(planes), axis=0) if planes else np.full(c.shape[2:], np.nan, np.float32)


def _plane(c: np.ndarray, e: int, band: str) -> np.ndarray:
    return _all_bands(c, e) if band.upper() == "ALL" else c[e, _band_index(band)]


def _track_px(m: dict, e: int) -> tuple[float, float]:
    tr = {t["epoch"]: t for t in m["target"].get("track", [])}.get(e)
    return (tr["x"], tr["y"]) if tr else (math.nan, math.nan)


def get_image(sequence_id: str, epoch: int = -1, band: str = "ALL", mode: str = "coadd", reference: int = 0,
              stretch: str = "asinh", mark_target: bool = True) -> tuple[bytes, dict]:
    m, c = _cube(sequence_id)
    n_ep, n = c.shape[0], c.shape[2]
    e = epoch % n_ep
    mode = mode.lower()
    if mode == "rgb":
        def grp(e_, idx):
            planes = [c[e_, i] for i in idx if np.isfinite(c[e_, i]).any()]
            return np.nanmean(np.stack(planes), axis=0) if planes else np.full((n, n), np.nan)
        chans = [grp(e, (4, 5)), grp(e, (2, 3)), grp(e, (0, 1))]
        rgb = []
        for ch in chans:
            lo, hi = images.limits(ch, 1, 99.5)
            rgb.append(np.nan_to_num(images.stretch(ch, lo, hi, stretch)) * 255)
        img = np.flipud(np.stack(rgb, axis=-1)).astype(np.uint8)
        caption = "false colour: red = D5+D6 (3.8-5 um), green = D3+D4, blue = D1+D2; cold brown dwarfs look red"
    elif mode == "difference":
        r = reference % n_ep
        d = _plane(c, e, band) - _plane(c, r, band)
        scale = float(np.nanpercentile(np.abs(d[np.isfinite(d)]), 99.5)) if np.isfinite(d).any() else 1.0
        img = images.diverging(d, scale)
        caption = (f"epoch {e} minus epoch {r}: red = brighter / arrived, blue = fainter / left. "
                   "A moving source shows a red-blue pair along its motion.")
    else:
        stack = np.stack([_plane(c, k, band) for k in range(n_ep)])
        lo, hi = images.limits(stack)
        img = images.to_grey(images.stretch(_plane(c, e, band), lo, hi, stretch))
        caption = f"co-add of epoch {e}, detector {band.upper()}"
    img = images.upscale(img)
    if mark_target:
        img = images.mark(img, *_track_px(m, e), n)
    ep = m["epochs"][e]
    info = {"sequence_id": sequence_id, "epoch": e, "dates": f"{ep['date_min'][:10]}..{ep['date_max'][:10]}",
            "band": band.upper(), "mode": mode, "grid_n": n, "pixscale_arcsec": m["grid"]["pixscale_arcsec"],
            "caption": caption + (". Amber crosshair: predicted target position." if mark_target else ""),
            "orientation": "north up, east left"}
    return images.encode_png(img), info


def get_blink_strip(sequence_id: str, band: str = "ALL", stretch: str = "asinh") -> tuple[bytes, dict]:
    m, c = _cube(sequence_id)
    n_ep, n = c.shape[0], c.shape[2]
    planes = [_plane(c, k, band) for k in range(n_ep)]
    lo, hi = images.limits(np.stack(planes))
    side = max(96, min(images.MAX_SIDE, 1400 // max(n_ep, 1)))
    panels = [images.mark(images.upscale(images.to_grey(images.stretch(p, lo, hi, stretch)), side),
                          *_track_px(m, k), n) for k, p in enumerate(planes)]
    info = {"sequence_id": sequence_id, "band": band.upper(),
            "epochs": [f"{e['index']}: {e['date_min'][:10]}" for e in m["epochs"]],
            "caption": "Epochs left to right, same brightness scale; amber crosshair = predicted target. "
                       "Look for a dot that shifts between panels (mover) or changes brightness (variable)."}
    return images.encode_png(images.hstack(panels)), info


# ---------------------------------------------------------------- point analysis
async def analyze_point(sequence_id: str, ra: float, dec: float, pmra: float = 0.0, pmdec: float = 0.0,
                        ref_epoch: float = 2016.0) -> dict:
    """Everything about one position: photometry -> spectrum + class + variability, motion fit, period, Gaia."""
    m = _meta(sequence_id)
    native = getattr(services, "analyze_point", None)  # point-analysis job from the parallel PR, when merged
    if native is not None:
        return compact(await native(sequence_id, ra, dec))
    out: dict = {"sequence_id": sequence_id, "ra": ra, "dec": dec}
    phot = await services.position_photometry(sequence_id, ra, dec, pmra, pmdec, ref_epoch)
    records = phot.get("photometry", [])
    good = [p for p in records if not p.get("flagged")]
    out["photometry"] = {"n": len(records), "unflagged": len(good)}
    if records:
        try:
            sp = await asyncio.to_thread(services.analyze_spectrum, records, None, False, None)
            cls = sp.get("classification", {})
            out["classification"] = compact({k: cls.get(k) for k in ("available", "summary", "blackbody", "ir_excess")})
            tm = (cls.get("templates") or {})
            out["best_templates"] = compact([{k: t.get(k) for k in ("name", "sptype", "family", "probability")}
                                             for t in (tm.get("ranked") or [])[:3]])
            spec = sp.get("spectrum", {})
            out["features_detected"] = compact([{k: f.get(k) for k in ("name", "depth", "significance")}
                                                for f in spec.get("features", []) if f.get("detected")], max_list=8)
            out["indices"] = compact({k: (v or {}).get("value") for k, v in (spec.get("indices") or {}).items()})
            var = sp.get("variability", {})
            out["spectral_variability"] = compact([{k: e.get(k) for k in ("epoch", "variable", "significant_bands",
                                                                           "chi2_red_constant")}
                                                   for e in var.get("epochs", [])])
        except Exception as e:  # noqa: BLE001 - each part reports its own failure
            out["spectrum_error"] = str(e)[:200]
        if len(good) >= 6:
            try:
                pg = await asyncio.to_thread(services.points_periodogram, records)
                out["periodogram"] = compact({"best": pg.get("best"), "peaks": (pg.get("peaks") or [])[:3],
                                              "baseline_days": pg.get("baseline_days"), "n_points": pg.get("n_points")})
            except Exception as e:  # noqa: BLE001
                out["periodogram"] = f"not available: {str(e)[:120]}"
    try:
        x, y = services.sky_to_grid(sequence_id, ra, dec)
        mo = await asyncio.to_thread(services.motion_fit, sequence_id, x, y)
        out["motion"] = compact({k: mo.get("combined", {}).get(k) for k in
                                 ("pmra", "pmra_err", "pmdec", "pmdec_err", "pm_total", "pa_deg", "n_epochs")})
    except Exception as e:  # noqa: BLE001
        out["motion"] = f"not measurable: {str(e)[:160]}"
    try:
        gaia = await asyncio.to_thread(services.catalog_cone, "gaia", ra, dec, 0.25)
        out["gaia_within_15arcsec"] = compact([{k: s.get(k) for k in ("id", "mag", "pmra", "pmdec", "parallax")}
                                               for s in gaia[:3]])
    except Exception as e:  # noqa: BLE001
        out["gaia_within_15arcsec"] = f"unavailable: {str(e)[:120]}"
    out["epoch_dates"] = [e["date_min"][:10] for e in m["epochs"]]
    return out


async def spectrum_classify(sequence_id: str) -> dict:
    cls = await asyncio.to_thread(services.sequence_classification, sequence_id, True)
    tm = cls.get("templates") or {}
    return compact({"summary": cls.get("summary"), "available": cls.get("available"),
                    "best_templates": [{k: t.get(k) for k in ("name", "sptype", "family", "probability")}
                                       for t in (tm.get("ranked") or [])[:5]],
                    "class_probabilities": tm.get("class_probabilities"), "blackbody": cls.get("blackbody"),
                    "catalog_comparison": (cls.get("catalog_comparison") or {}).get("bands"),
                    "notes": cls.get("notes")})


# ---------------------------------------------------------------- field analysis and solar-system objects
async def analyze_field(sequence_id: str, snr_min: float = 5.0) -> dict:
    _meta(sequence_id)
    if _active("field") >= LIMITS.max_active_analyses:
        raise ToolError("Two analyses are already running; poll job_result first.")
    job = await services.field_submit(sequence_id, {"snr_min": float(snr_min)}, client=CLIENT)
    if job.get("status") not in ("done", "error", "cancelled"):
        _keepalive("field", job["id"])
    return {"job_id": job["id"], "status": job.get("status"),
            "hint": "Poll job_result every 15-30 s; a full field analysis takes 1-5 minutes."}


async def known_objects(sequence_id: str, vmag_lim: float = 21.0) -> dict:
    job = await services.known_objects_submit(sequence_id, vmag_lim=float(vmag_lim), client=CLIENT)
    if job.get("status") not in ("done", "error", "cancelled"):
        _keepalive("field", job["id"])
    return {"job_id": job["id"], "status": job.get("status"), "hint": "Poll job_result."}


async def track_and_stack_search(sequence_id: str, epoch: int, rate_min: float = 0.3, rate_max: float = 10.0) -> dict:
    job = await services.trackstack_search_submit(sequence_id, int(epoch), float(rate_min), float(rate_max),
                                                  client=CLIENT)
    if job.get("status") not in ("done", "error", "cancelled"):
        _keepalive("field", job["id"])
    return {"job_id": job["id"], "status": job.get("status"), "hint": "Poll job_result."}


def _summarise_field(res: dict, limit: int) -> dict:
    cands = res.get("candidates", {})
    out = {"sequence": res.get("sequence"), "target": res.get("target"),
           "counts": {k: len(v) for k, v in cands.items() if isinstance(v, list)},
           "stats": compact({k: res.get("stats", {}).get(k) for k in ("sources", "catalogs", "transients")}),
           "realbogus": compact({k: (res.get("realbogus") or {}).get(k) for k in ("model", "validation")}),
           "notes": res.get("notes", [])[:4]}
    for k, v in cands.items():
        if isinstance(v, list) and v:
            out[k] = compact(v[:limit], max_list=4)
    for k in ("times", "objects", "candidates_list", "hits"):
        if k in res and k not in out:
            out[k] = compact(res[k], max_list=limit)
    return out


async def job_result(job_id: str, limit: int = LIST_LIMIT) -> dict:
    """Status of an analysis job; when done, a compact summary of its result."""
    try:
        st, job = await services.field_status(job_id, client=CLIENT)
    except ValueError as e:
        raise ToolError(f"Not an analysis job id: {e}") from e
    if st == "done":
        res = json.loads(services.field_result_path(job_id).read_text())
        if res.get("kind") in _LB_SUMMARIES:
            return {"job_id": job_id, "status": "done", **_LB_SUMMARIES[res["kind"]](res, int(limit), job_id)}
        return {"job_id": job_id, "status": "done", **_summarise_field(res, int(limit))}
    if st == "unknown":
        raise ToolError("Unknown or expired job id.")
    if st in ("queued", "running"):
        _keepalive("field", job_id)
    return {"job_id": job_id, **{k: (job or {}).get(k) for k in ("status", "stage", "done", "total", "error")}}


def track_and_stack_image(sequence_id: str, epoch: int, rate_arcsec_per_hour: float, pa_deg: float,
                          subtract_static: bool = True) -> tuple[bytes, dict]:
    res = services.trackstack_image(sequence_id, int(epoch), float(rate_arcsec_per_hour), float(pa_deg),
                                    subtract_static=subtract_static)
    img = res["parts"][0]["image"]
    lo, hi = images.limits(img, 1, 99.8)
    png = images.encode_png(images.upscale(images.to_grey(images.stretch(img, lo, hi))))
    return png, {"epoch": epoch, "rate_arcsec_per_hour": rate_arcsec_per_hour, "pa_deg": pa_deg,
                 "frames": res.get("frames"), "t_ref_mjd": res.get("t_ref"),
                 "caption": "Exposures shifted along the trial motion and stacked: an object moving at this rate "
                            "becomes a sharp dot at its t_ref position, stars smear (or vanish if subtracted)."}


async def moving_object_search(name: str) -> dict:
    st, res = await services.moving_search(name)
    if st != "done":
        return compact({"status": st, **{k: res.get(k) for k in ("kind", "error", "choices")}})
    return compact({"status": "done", **{k: res.get(k) for k in ("object", "n_exposures", "date_min", "date_max",
                                                                 "suggested_request")}})


async def archival_epochs(sequence_id: str) -> dict:
    st, res = await services.archival_status(sequence_id)
    if st != "done":
        return compact({"status": st, **({k: res.get(k) for k in ("stage", "done", "total", "error")}
                                           if isinstance(res, dict) else {})})
    meta = json.loads(res)
    return compact({"status": "done", "frames": [{k: f.get(k) for k in ("index", "survey", "band", "date",
                                                                         "target_px", "target_in_field")}
                                                  for f in meta.get("frames", [])], "grid": meta.get("grid")},
                   max_list=60)


# ---------------------------------------------------------------- long-baseline light curves (ADR-0059)
def _lb_summary(res: dict, limit: int = LIST_LIMIT, job_id: str = "") -> dict:
    """A long-baseline light curve without its point list: coverage, calibration, period, trends, events."""
    a = res.get("analysis") or {}
    per = a.get("period") or {}
    cal = res.get("calibration") or {}
    bands = (res.get("coverage") or {}).get("bands") or {}
    return compact({
        "target": {k: (res.get("target") or {}).get(k) for k in ("name", "ra", "dec", "pmra", "pmdec")},
        "verdict": a.get("classification"),
        "coverage": {"first_year": (res.get("coverage") or {}).get("first_year"),
                     "last_year": (res.get("coverage") or {}).get("last_year"),
                     "bands": {b: {"n": v.get("n"), "surveys": v.get("surveys")} for b, v in bands.items()}},
        "period": {k: per.get(k) for k in ("period_days", "period_err_days", "power", "false_alarm_probability",
                                           "significant", "amplitudes_mag", "bands", "baseline_days")} if per else None,
        "other_peaks_days": [p_.get("period_days") for p_ in (per.get("peaks") or [])[1:4]],
        "trends": a.get("trends"), "events": (a.get("events") or [])[:limit],
        "era_means": {b: [{k: e.get(k) for k in ("survey", "mean_mag", "err_mag", "n")} for e in v]
                      for b, v in (a.get("era_means") or {}).items() if b in ("W1", "W2", "J", "H", "Ks")},
        "methods": {k: v.get("method") for k, v in (res.get("methods") or {}).items()},
        "calibration": {
            "frames_vs_catalogue": {k: {kk: v.get(kk) for kk in ("offset_mag", "scatter_mag", "n", "applied")}
                                    for k, v in (cal.get("frames") or {}).items()},
            "spherex_vs_archive": {k: {kk: v.get(kk) for kk in ("used_offset_mag", "scatter_mag", "n", "source")}
                                   for k, v in (cal.get("spherex") or {}).items()}},
        "csv": f"/api/long-baseline/{job_id}/lightcurve.csv", "notes": res.get("notes", [])[:4]},
        max_list=max(limit, 8))


def _lp_summary(res: dict, limit: int = LIST_LIMIT, job_id: str = "") -> dict:
    keep = ("id", "ra", "dec", "w1", "w2", "label", "score", "contamination", "period_days", "period_fap",
            "cadence_alias", "range_mag", "chi2_red", "trends", "events", "baseline_years")
    return compact({"summary": res.get("summary"),
                    "candidates": [{k: c.get(k) for k in keep} for c in (res.get("candidates") or [])[:limit]],
                    "ensemble": res.get("ensemble"), "notes": res.get("notes")}, max_list=max(limit, 6))


_LB_SUMMARIES = {"long-baseline light curve": _lb_summary, "long-period search": _lp_summary}


async def _lb_job(job: dict, limit: int, hint: str) -> dict:
    if job.get("status") == "done":
        res = json.loads(services.field_result_path(job["id"]).read_text())
        return {"job_id": job["id"], "status": "done", **_LB_SUMMARIES[res["kind"]](res, limit, job["id"])}
    if job.get("status") in ("queued", "running"):
        _keepalive("field", job["id"])
    return {"job_id": job["id"], **{k: job.get(k) for k in ("status", "stage", "done", "total", "error")}, "hint": hint}


async def long_baseline_lightcurve(sequence_id: str, ra: float | None = None, dec: float | None = None,
                                   pmra: float | None = None, pmdec: float | None = None,
                                   ref_epoch: float = 2000.0) -> dict:
    """1999-2026 light curve of the target (or a position): 2MASS, AllWISE, NEOWISE frames + SPHEREx synthetic bands."""
    _meta(sequence_id)
    if (ra is None) != (dec is None):
        raise ToolError("Give both ra and dec, or neither for the sequence target.")
    job = await services.long_baseline_submit(sequence_id, ra, dec, pmra, pmdec, ref_epoch, client=CLIENT)
    return await _lb_job(job, LIST_LIMIT, "Call again with the same arguments (or job_result) every 15-30 s; "
                                          "a first run reads ~0.5 GB of WISE frames and takes 1-3 minutes.")


async def long_period_search(sequence_id: str, limit: int = LIST_LIMIT) -> dict:
    """Rank the field's sources by long-baseline (NEOWISE + SPHEREx) variability."""
    _meta(sequence_id)
    job = await services.long_period_search_submit(sequence_id, client=CLIENT)
    return await _lb_job(job, int(limit), "Call again (or job_result) every 30 s; a field takes 1-5 minutes.")


# ---------------------------------------------------------------- catalogs and exports
def catalog_cone(catalog: str, ra: float, dec: float, radius_arcmin: float = 2.0, limit: int = 25) -> dict:
    try:
        rows = services.catalog_cone(catalog, ra, dec, min(float(radius_arcmin), 10.0))
    except KeyError as e:
        raise ToolError(f"catalog must be one of {', '.join(services.list_catalogs())}") from e
    fast = [s for s in rows if s.get("pmra") is not None and math.hypot(s["pmra"], s.get("pmdec") or 0) > 100]
    keep = ("id", "ra", "dec", "epoch", "pmra", "pmdec", "parallax", "mag", "mag_band")
    return {"catalog": catalog, "n": len(rows),
            "brightest": [compact({k: s.get(k) for k in keep}) for s in rows[:int(limit)]],
            "high_proper_motion": [compact({k: s.get(k) for k in keep}) for s in fast[:10]]}


def export_report(sequence_id: str, fmt: str, job_id: str | None = None) -> dict:
    fmt = fmt.lower()
    if fmt == "ades":
        if not job_id:
            raise ToolError("ADES needs the job_id of a finished analyze_field result.")
        text = services.field_ades(job_id)
        return {"format": "ades", "text": text[:20000]}
    if fmt in ("csv", "votable"):
        data = services.export_photometry(sequence_id, "csv" if fmt == "csv" else "vot")
        return {"format": fmt, "text": data.decode()[:20000], "truncated": len(data) > 20000}
    if fmt == "fits":
        return {"format": "fits", "url": f"/api/sequences/{sequence_id}/export/coadds.fits",
                "note": "Binary; download it from the site (prefix with the server URL)."}
    raise ToolError("fmt must be ades, csv, votable or fits")
