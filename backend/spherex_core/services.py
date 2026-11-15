"""Interface-agnostic facade over the library.

The HTTP API (spherex_api) and the MCP server call only these functions, so validation, error
messages and the job registry live in one place. Blocking functions (network lookups) should be
run in a worker thread by async callers; submit_sequence must run on the event loop.

Errors: NotFound (unknown or not-ready resource, message safe for users), LookupError from
resolve_target, UpstreamError (a RuntimeError, message safe for users) when a remote catalog service fails.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import numpy as np

from . import __version__, cache, catalogs
from .config import settings
from .errors import NotFound, UpstreamError
from .jobs import PRIORITY_INTERACTIVE, JobQueue, QueueFull, TooManyJobs, make_queue
from .pipeline import results
from .pipeline.params import SequenceParams
from .store import store
from .targets import PRESETS, objects, resolve

__all__ = ["NotFound", "QueueFull", "SequenceParams", "TooManyJobs", "UpstreamError", "array_file", "catalog_cone",
           "configure", "health", "jobs", "list_catalogs", "list_presets", "load_sequence", "object_index", "poll_sequence",
           "queue_stats", "read_array", "read_exposures", "readiness", "resolve_target", "result_dir",
           "sequence_meta_bytes", "sequence_status", "submit_sequence", "withdraw_sequence"]

_jobs: JobQueue | None = None


def configure(cache_root: Path | None = None) -> None:
    """Point the services at another cache root (tests, embedded use); None restores settings.cache_dir."""
    global _jobs
    cache.set_root(cache_root)
    _jobs = None


def jobs() -> JobQueue:
    """The job queue: in-process (default) or Redis (SPHEREX_QUEUE=redis / REDIS_URL set), see jobs/."""
    global _jobs
    if _jobs is None:
        _jobs = make_queue()
    return _jobs


# ---------------------------------------------------------------- service info and targets
def health() -> dict:
    return {"ok": True, "version": __version__, "collections": settings.collections}


def list_presets() -> list[dict]:
    return [t.to_dict() for t in PRESETS.values()]


def resolve_target(query: str) -> dict:
    """Coordinates, preset id/name, a name in the local object index or any name Sesame knows -> target
    dict. Raises LookupError."""
    return resolve(query).to_dict()


def object_index() -> tuple[bytes, str]:
    """The local object index as served to clients (JSON bytes) and its ETag."""
    idx = objects.index()
    return idx.raw, idx.etag


# ---------------------------------------------------------------- sequences
async def submit_sequence(params: SequenceParams, force: bool = False, *, ip: str | None = None,
                          client: str | None = None, priority: int = PRIORITY_INTERACTIVE,
                          pinned: bool = False) -> dict:
    """Start (or join) a build; returns the job dict. Raises TooManyJobs / QueueFull (HTTP 429)."""
    q = jobs()
    if not pinned and not await q.allow_submit(ip):
        raise TooManyJobs("Too many requests from your address; slow down.", retry_after=20)
    if not pinned:  # count what people ask for: frequent fields get prewarmed (popular.py)
        import asyncio

        from . import popular

        req = {k: getattr(params, k) for k in ("ra", "dec", "pmra", "pmdec", "ref_epoch", "fov_arcmin")}
        asyncio.get_running_loop().run_in_executor(None, popular.count_request, params.ra, params.dec,
                                                   params.fov_arcmin, params.name, req)
    return await q.submit(params, force=force, ip=ip, client=client, priority=priority, pinned=pinned)


def upstream_status() -> dict:
    """Remote sources per kind of data, in the order they are asked now, plus the file-catalogue mirror."""
    from . import endpoints, netpolicy, upstreams  # noqa: F401 - endpoints registers the sources
    from .archive import mirror

    m = mirror.get()
    try:
        mstats = m.stats() if m is not None else None
    except Exception as e:  # noqa: BLE001
        mstats = {"error": str(e)}
    return {**upstreams.status(), "breakers": netpolicy.status(), "mirror": mstats}


async def poll_sequence(seq_id: str, client: str | None = None) -> tuple[str, dict | None]:
    """Like sequence_status, and the poll counts as a heartbeat that keeps the build alive."""
    import asyncio

    if seq_id.isalnum() and await asyncio.to_thread(store().ensure_local, seq_id):
        return "done", None
    job = await jobs().status(seq_id, client=client) if seq_id.isalnum() else None
    if job is None:
        return "unknown", None
    if job["status"] == "done":  # built by a worker: fetch it from the bucket now, or report it as still publishing
        if await asyncio.to_thread(store().ensure_local, seq_id, True):
            return "done", None
        return "running", {**job, "status": "running", "stage": "publish"}
    return job["status"], job


async def withdraw_sequence(seq_id: str, client: str | None = None) -> dict | None:
    """The client no longer needs this build; it is cancelled once nobody else wants it."""
    return await jobs().withdraw(seq_id, client=client) if seq_id.isalnum() else None


async def queue_stats() -> dict:
    return await jobs().stats()


async def readiness() -> dict:
    """Dependencies reachable (queue backend, bucket). Raises on failure."""
    import asyncio

    await jobs().ping()
    await asyncio.to_thread(store().ping)
    return {"ready": True, **await queue_stats(), "bucket": store().enabled}


def result_dir(seq_id: str) -> Path:
    """Local directory of a finished result (downloaded from the bucket if needed). Raises NotFound."""
    return _ready_dir(seq_id)


def _result_dir(seq_id: str) -> Path | None:
    return cache.layout().sequence(seq_id) if seq_id.isalnum() else None


def _ready_dir(seq_id: str) -> Path:
    d = _result_dir(seq_id)
    if d is None or not store().ensure_local(seq_id):
        raise NotFound("sequence not ready")
    return d


def sequence_status(seq_id: str) -> tuple[str, dict | None]:
    """Synchronous status without heartbeat (in-process queue only; async callers use poll_sequence)."""
    d = _result_dir(seq_id)
    if d is not None and store().ensure_local(seq_id):
        return "done", None
    job = getattr(jobs(), "jobs", {}).get(seq_id)
    if job is None:
        return "unknown", None
    return job.status, job.to_dict()


def sequence_meta_bytes(seq_id: str) -> bytes:
    return results.meta_bytes(_ready_dir(seq_id))


def load_sequence(seq_id: str) -> dict:
    return results.read_meta(_ready_dir(seq_id))


def array_file(seq_id: str, array: str) -> Path:
    """Path of a raw co-add cube file ('coadds', 'coverage', 'flags')."""
    if array not in results.ARRAY_FILES:
        raise NotFound("unknown array")
    return results.array_path(_ready_dir(seq_id), array)


def read_array(seq_id: str, array: str) -> np.ndarray:
    if array not in results.ARRAY_FILES:
        raise NotFound("unknown array")
    d = _ready_dir(seq_id)
    return results.read_array(d, results.read_meta(d), array)


def read_exposures(seq_id: str, epoch: int, band: str) -> tuple[np.ndarray, list[int]]:
    """Aligned single exposures of one epoch and detector, float32 [k, n, n], and their exposure indices."""
    d = _ready_dir(seq_id)
    return results.read_exposures(d, results.read_meta(d), epoch, band)


# ---------------------------------------------------------------- catalogs
def list_catalogs() -> dict:
    return catalogs.CATALOGS


def catalog_cone(name: str, ra: float, dec: float, radius_arcmin: float) -> list[dict]:
    """Catalog sources around (ra, dec). Raises NotFound for an unknown catalog, UpstreamError if the
    remote service fails."""
    if name not in catalogs.CATALOGS:
        raise NotFound("unknown catalog")
    from . import netpolicy

    with netpolicy.fast():  # an overlay layer: short timeouts, and skip a service that just failed
        return catalogs.cone(name, ra, dec, radius_arcmin / 60.0)


# ---------------------------------------------------------------- analyses, exports, releases (agent B)
# Thin wrappers; the logic lives in analysis/, exports/ and releases.py. Imports are local so a failure
# there cannot break the core services.
async def position_photometry(seq_id: str, ra: float, dec: float, pmra: float = 0.0, pmdec: float = 0.0,
                              ref_epoch: float = 2000.0, name: str | None = None) -> dict:
    """Forced photometry of any position in every exposure of a built sequence (seconds; cached cutouts)."""
    from .analysis import full_context
    from .analysis.remeasure import measure_position
    from .targets import Target

    target = Target(ra=ra, dec=dec, name=name, pmra=pmra, pmdec=pmdec, ref_epoch=ref_epoch)
    return await measure_position(full_context(seq_id), target)


def motion_fit(seq_id: str, x: float, y: float, bands: list[str] | None = None, pmra: float | None = None,
               pmdec: float | None = None) -> dict:
    """Proper motion of the source near grid pixel (x, y) from per-epoch co-add centroids. ValueError if it
    is found in fewer than two epochs."""
    from .analysis import fit_motion, load_context

    ctx = load_context(seq_id)
    return fit_motion(ctx.meta, results.read_array(ctx.dir, ctx.meta, "coadds"), ctx.grid, x, y, bands, pmra, pmdec)


def sky_to_grid(seq_id: str, ra: float, dec: float) -> tuple[float, float]:
    from .analysis import load_context

    x, y = load_context(seq_id).grid.world_to_pixel(ra, dec)
    return float(x), float(y)


def sequence_periodogram(seq_id: str, **options) -> dict:
    """Lomb-Scargle of the sequence target's photometry (options: method, band, min_period, max_period,
    include_flagged). ValueError for too few points or a bad range."""
    from .analysis import load_context, periodogram

    return periodogram(load_context(seq_id).meta["photometry"], **options)


def points_periodogram(photometry: list[dict], **options) -> dict:
    """Lomb-Scargle of client-supplied photometry records (e.g. from position_photometry)."""
    from .analysis import periodogram

    return periodogram(photometry, **options)


def export_coadds_fits(seq_id: str, epoch: int | None = None, band: str | None = None,
                       coverage: bool = False) -> bytes:
    from .analysis import full_context
    from .exports import coadds_fits

    ctx = full_context(seq_id)
    return coadds_fits(ctx.dir, ctx.meta, epoch, band, coverage)


def export_photometry(seq_id: str, fmt: str) -> bytes:
    """'csv' or 'vot' (VOTable)."""
    from .analysis import full_context
    from .exports import photometry_csv, photometry_votable

    meta = full_context(seq_id).meta
    return photometry_csv(meta) if fmt == "csv" else photometry_votable(meta)


def export_download_script(seq_id: str, kind: str = "full", source: str = "irsa") -> str:
    from .analysis import full_context, sequence_frames
    from .exports import download_script

    ctx = full_context(seq_id)
    try:
        frames = sequence_frames(ctx)
    except RuntimeError as e:
        raise UpstreamError(f"IRSA TAP is unavailable: {e}") from e
    return download_script(ctx.meta, frames, kind, source)


def releases_summary() -> dict:
    from . import releases

    return releases.releases()


def sequence_freshness(seq_id: str) -> dict:
    from . import releases
    from .analysis import load_context

    return releases.freshness(load_context(seq_id))


# ---------------------------------------------------------------- archival epochs, moving objects (agent A)
# Archival builds and MOST searches run as in-process background tasks (tasks.py); callers poll.
_tasks = None


def _task_registry():
    global _tasks
    if _tasks is None:
        from .tasks import TaskRegistry

        _tasks = TaskRegistry(max_concurrent=2)
    return _tasks


async def archival_status(seq_id: str, fov_arcmin: float | None = None) -> tuple[str, bytes | dict]:
    """2MASS / AllWISE / NEOWISE epochs on the sequence grid: ('done', meta.json bytes) or
    ('running' | 'error', task dict). Starts the build on first call. Raises NotFound (sequence not ready,
    or a solar-system object). Must be awaited on the event loop."""
    from . import archival
    from .archival import store as archival_store

    meta = load_sequence(seq_id)
    if meta.get("preview"):  # its archival build would be stored with it and deleted with it
        from .analysis.common import PREVIEW_NOT_ANALYSED

        raise NotFound(PREVIEW_NOT_ANALYSED)
    if meta["target"].get("ephemeris"):
        raise NotFound("Archival epochs are for fixed or proper-motion targets, not solar-system objects.")
    d = archival.archival_dir(seq_id, meta, fov_arcmin)
    if archival_store.is_complete(d):
        return "done", archival_store.meta_bytes(d)

    async def build(progress):
        try:
            await archival.build_archival(meta, d, fov_arcmin, archival.SURVEYS, progress)
        finally:
            _release(lock)

    key, lock = f"archival:{seq_id}:{d.name}", d.with_name(d.name + ".lock")
    t = await _start_locked(key, lock, build)
    if archival_store.is_complete(d):
        return "done", archival_store.meta_bytes(d)
    return ("error" if t["status"] == "done" else t["status"]), t


def archival_frames(seq_id: str, fov_arcmin: float | None = None, index: int | None = None) -> np.ndarray:
    """Archival frames float32 [k, n, n] (or one [n, n]). Raises NotFound until built."""
    from . import archival
    from .archival import store as archival_store

    return archival_store.read_frames(archival.archival_dir(seq_id, load_sequence(seq_id), fov_arcmin), index)


async def moving_search(name: str | None = None, obj_type: str | None = None, begin: str | None = None,
                        end: str | None = None, naif_id: int | None = None) -> tuple[str, dict]:
    """SPHEREx exposures of a solar-system object via IRSA MOST: ('done', summary) or ('running' | 'error',
    task dict with kind/choices). Long windows take minutes on MOST's side, so the first call starts a
    background search. ValueError without a name or NAIF ID. Must be awaited on the event loop."""
    import asyncio
    import json

    from . import moving
    from .moving import most

    q = moving.build_query(name, obj_type, begin, end, naif_id)
    hit = most.cached(q)
    if hit is not None:
        return "done", moving.search_summary(hit)
    path = most.cache_path(q)
    lock = path.with_name(path.name + ".lock")

    async def search(progress):
        try:
            await asyncio.to_thread(moving.query_most, name, obj_type, begin, end, naif_id)
        finally:
            _release(lock)

    t = await _start_locked("most:" + json.dumps(q, sort_keys=True), lock, search)
    hit = most.cached(q)
    if hit is not None:
        return "done", moving.search_summary(hit)
    return t["status"], t


async def _start_locked(key: str, lock, make) -> dict:
    """Start a background task unless this process already runs it or another process holds the lock
    (several uvicorn workers share the disk). Returns the task dict."""
    from .tasks import claim

    reg = _task_registry()
    t = reg.get(key)
    if t is None:
        if not claim(lock):
            return {"id": key, "status": "running", "stage": "running in another worker", "done": 0, "total": 0,
                    "error": None, "elapsed": 0.0}
        t = reg.start(key, make)
    return t.to_dict()


def _release(lock) -> None:
    from .tasks import release

    release(lock)


# ---------------------------------------------------------------- spectral analysis (spectral agent)
# Thin wrappers over spectral/. Results of a built sequence depend only on its immutable meta.json and the
# analysis version, so they are memoised per (sequence, options) in this process.
_SPECTRAL_CACHE: "dict[tuple, dict]" = {}
_SPECTRAL_CACHE_MAX = 64


def _spectral_memo(key: tuple, make) -> dict:
    hit = _SPECTRAL_CACHE.get(key)
    if hit is None:
        hit = make()
        if len(_SPECTRAL_CACHE) >= _SPECTRAL_CACHE_MAX:
            _SPECTRAL_CACHE.pop(next(iter(_SPECTRAL_CACHE)))
        _SPECTRAL_CACHE[key] = hit
    return hit


def _sequence_target(meta: dict):
    from .targets import Target

    t = meta["target"]
    return Target(ra=t["ra"], dec=t["dec"], name=t.get("name"), pmra=t.get("pmra") or 0.0,
                  pmdec=t.get("pmdec") or 0.0, ref_epoch=t.get("ref_epoch") or 2000.0)


def sequence_spectrum(seq_id: str, include_flagged: bool = False) -> dict:
    """Binned spectrum (overall, per epoch, relative), continuum, curated features, detections, indices."""
    from . import spectral
    from .spectral.version import SPECTRAL_VERSION

    meta = load_sequence(seq_id)
    return _spectral_memo(("spectrum", seq_id, include_flagged, SPECTRAL_VERSION),
                          lambda: {"sequence": seq_id, "target": (meta.get("target") or {}).get("name"),
                                   "version": SPECTRAL_VERSION,
                                   **spectral.spectrum_report(meta["photometry"], include_flagged)})


def sequence_classification(seq_id: str, catalogs: bool = False) -> dict:
    """Template / blackbody / IR-index classification and synthetic photometry (optionally vs catalogues)."""
    from . import spectral
    from .spectral.version import SPECTRAL_VERSION

    meta = load_sequence(seq_id)
    return _spectral_memo(("classify", seq_id, catalogs, SPECTRAL_VERSION),
                          lambda: {"sequence": seq_id, "target": (meta.get("target") or {}).get("name"),
                                   "version": SPECTRAL_VERSION,
                                   **spectral.classify_report(meta["photometry"],
                                                              _sequence_target(meta) if catalogs else None,
                                                              catalogs=catalogs)})


def sequence_spectral_variability(seq_id: str, reference: int | None = None) -> dict:
    """Ratio spectra of every epoch to a reference epoch."""
    from . import spectral
    from .spectral.version import SPECTRAL_VERSION

    meta = load_sequence(seq_id)
    return _spectral_memo(("variability", seq_id, reference, SPECTRAL_VERSION),
                          lambda: {"sequence": seq_id, "version": SPECTRAL_VERSION,
                                   **spectral.variability_report(meta["photometry"], reference)})


def analyze_spectrum(photometry: list[dict], target: dict | None = None, catalogs: bool = False,
                     reference: int | None = None) -> dict:
    """Full spectral analysis of client-supplied photometry records (e.g. a clicked source)."""
    from . import spectral
    from .spectral.version import SPECTRAL_VERSION
    from .targets import Target

    tgt = Target(**target) if target else None
    return {"version": SPECTRAL_VERSION,
            **spectral.analyze(photometry, target=tgt, catalogs=catalogs, reference=reference)}


def spectral_reference() -> dict:
    """The bin grid, curated features, index definitions and template library listing."""
    from . import spectral

    return spectral.reference()


# ---------------------------------------------------------------- field analysis, moving objects, track & stack
def _field_data(seq_id: str):
    from .analysis.common import full_context
    from .field.common import load_field

    ctx = full_context(seq_id)
    return ctx, load_field(ctx)


async def field_submit(seq_id: str, options: dict | None = None, client: str | None = None,
                       force: bool = False) -> dict:
    """Start or join 'Analyze this field' (all searches); poll with field_status. Raises NotFound."""
    from .field import FieldOptions, field_jobs

    return await field_jobs().submit(seq_id, FieldOptions(**(options or {})), client, force)


async def known_objects_submit(seq_id: str, times: str = "epochs", epoch: int | None = None, vmag_lim: float = 21.0,
                               client: str | None = None) -> dict:
    """Known minor planets/comets seen from SPHEREx at every epoch mid-time (or every exposure of one epoch)."""
    from .field import jobs as fj
    from .field import known
    from .field.common import load_field

    if times not in ("epochs", "exposures"):
        raise ValueError("times must be 'epochs' or 'exposures'")
    jid = fj.make_id(seq_id, "k", {"times": times, "epoch": epoch, "vmag": vmag_lim})

    def work(ctx, progress, check):
        fd = load_field(ctx)
        if times == "epochs":
            mjds = [e["mjd_mean"] for e in ctx.meta["epochs"] if epoch is None or e["index"] == epoch]
        else:
            mjds = sorted({round(x["mjd"], 5) for x in ctx.meta["exposures"] if epoch is None or x["epoch"] == epoch})
        progress("known objects", 0, len(mjds))
        out = {"sequence": seq_id, "times_mode": times, "epoch": epoch, **known.overlay(fd, [], vmag_lim)}
        for i, m in enumerate(mjds):
            check()
            out["times"].append({"mjd": m, "objects": [o for o in known.field_objects(fd, m, vmag_lim) if o["inside"]]})
            progress("known objects", i + 1, len(mjds))
        return out

    return await fj.field_jobs().submit_work(seq_id, jid, work, client)


async def trackstack_search_submit(seq_id: str, epoch: int, rate_min: float = 0.3, rate_max: float = 10.0,
                                   window_hours: float = 48.0, bands: list[str] | None = None, snr_min: float = 6.0,
                                   client: str | None = None) -> dict:
    """Synthetic-tracking search of one visit over trial motions (a job)."""
    from .field import jobs as fj
    from .field import trackstack
    from .field.common import load_field

    params = {"epoch": epoch, "rmin": rate_min, "rmax": rate_max, "win": window_hours, "bands": bands, "snr": snr_min}
    jid = fj.make_id(seq_id, "s", params)

    def work(ctx, progress, check):
        progress("track & stack search", 0, 1)
        return {"sequence": seq_id, **trackstack.search(load_field(ctx), epoch, rate_min=rate_min, rate_max=rate_max,
                                                         window_hours=window_hours, bands=bands, snr_min=snr_min,
                                                         check=check)}

    return await fj.field_jobs().submit_work(seq_id, jid, work, client)


async def field_status(job_id: str, client: str | None = None) -> tuple[str, dict | None]:
    """('done', None) | (queued|running|error|cancelled, job) | ('unknown', None). ValueError for a bad id."""
    from .field import field_jobs
    from .field.jobs import split_id

    split_id(job_id)
    return await field_jobs().status(job_id, client)


async def field_withdraw(job_id: str, client: str | None = None) -> dict | None:
    from .field import field_jobs

    return await field_jobs().withdraw(job_id, client)


def field_result_path(job_id: str) -> Path:
    from .field.jobs import result_path

    p = result_path(job_id)
    if not p.exists():
        raise NotFound("field result not ready")
    return p


def field_ades(job_id: str, include_low: bool = False) -> str:
    """ADES PSV of a field / moving-object result's high-quality tracklets (all with include_low).
    Raises UpstreamError if Horizons fails."""
    import json

    from .field.ades import ades_psv

    res = json.loads(field_result_path(job_id).read_text())
    tracklets = res.get("candidates", {}).get("tracklets", [])
    try:
        return ades_psv(tracklets, res.get("sequence", ""), include_low)
    except RuntimeError as e:
        raise UpstreamError(f"JPL Horizons did not answer: {e}") from e


def trackstack_image(seq_id: str, epoch: int, rate: float, pa: float, bands: list[str] | None = None,
                     combine: str = "mean", subtract_static: bool = False, parts: int = 1) -> dict:
    """Track & Stack of one visit at a given motion: {'t_ref', 'frames', 'parts': [{'image': float32 [n,n]}]}."""
    from .field import trackstack

    _, fd = _field_data(seq_id)
    return trackstack.stack(fd, epoch, rate, pa, bands=bands, combine=combine, subtract_static=subtract_static,
                            parts=parts)


async def verify_candidate(seq_id: str, ra: float, dec: float, mjd_ref: float | None = None, rate: float = 0.0,
                           pa: float = 0.0, epoch: int | None = None, max_exposures: int = 40) -> dict:
    """Per-exposure ePSF fits (free position) and cutout strips for a candidate. Raises NotFound/ValueError."""
    import asyncio

    from .field.verify import verify

    ctx, fd = await asyncio.to_thread(_field_data, seq_id)
    return await verify(ctx, fd, ra, dec, mjd_ref, rate, pa, epoch, max_exposures)


# ---------------------------------------------------------------- point analysis ("click a point -> Analyze")
async def point_analysis_submit(seq_id: str, ra: float, dec: float, pmra: float | None = None,
                                pmdec: float | None = None, ref_epoch: float = 2000.0, name: str | None = None,
                                client: str | None = None, force: bool = False) -> dict:
    """Start or join every automatic analysis of one position (a job; poll with field_status)."""
    from .analysis.point import PointRequest, analyze_point
    from .field import jobs as fj

    req = PointRequest(ra=ra, dec=dec, pmra=pmra, pmdec=pmdec, ref_epoch=ref_epoch, name=name)
    jid = fj.make_id(seq_id, "p", req.key())

    def work(ctx, progress, check):
        return analyze_point(ctx, req, progress, check)

    return await fj.field_jobs().submit_work(seq_id, jid, work, client, force)


# ---------------------------------------------------------------- long-baseline light curves (ADR-0059)
async def long_baseline_submit(seq_id: str, ra: float | None = None, dec: float | None = None,
                               pmra: float | None = None, pmdec: float | None = None, ref_epoch: float = 2000.0,
                               name: str | None = None, calibrate: bool = True, min_period: float | None = None,
                               max_period: float | None = None, client: str | None = None,
                               force: bool = False, field_radius: float = 150.0) -> dict:
    """Start or join the 1999-2026 light curve of the sequence target (or any position inside its field): 2MASS,
    AllWISE and NEOWISE frames measured like the target, SPHEREx synthetic bands on the same scale, period / trend /
    event analysis (ADR-0059). A job; poll with field_status."""
    from .field import jobs as fj
    from .longbaseline import LB_VERSION, LongBaselineRequest, run

    req = LongBaselineRequest(ra=ra, dec=dec, pmra=pmra, pmdec=pmdec, ref_epoch=ref_epoch, name=name,
                              calibrate=calibrate, min_period=min_period, max_period=max_period,
                              field_radius=field_radius)
    jid = fj.make_id(seq_id, "l", {"lb": req.key(), "v": LB_VERSION})  # a new version does not reuse old results
    return await fj.field_jobs().submit_work(seq_id, jid, lambda ctx, progress, check: run(ctx, req, progress, check),
                                             client, force)


async def long_period_search_submit(seq_id: str, options: dict | None = None, client: str | None = None,
                                    force: bool = False) -> dict:
    """Start or join the field-wide ranking of sources by long-baseline variability (a job)."""
    from .field import jobs as fj
    from .longbaseline import SearchOptions, search_field
    from .longbaseline.search import SEARCH_VERSION

    opts = SearchOptions(**(options or {}))
    jid = fj.make_id(seq_id, "w", {"lp": opts.to_dict(), "v": SEARCH_VERSION})
    return await fj.field_jobs().submit_work(seq_id, jid,
                                             lambda ctx, progress, check: search_field(ctx, opts, progress, check),
                                             client, force)


def long_baseline_csv(job_id: str) -> str:
    """CSV of a finished long-baseline light curve job. Raises NotFound."""
    import json

    from .longbaseline.export import lightcurve_csv

    if job_id.partition("-")[2][:1] != "l":
        raise NotFound("not a long-baseline light-curve job")
    return lightcurve_csv(json.loads(field_result_path(job_id).read_text()))


# ---------------------------------------------------------------- solar system (ADR-0060): spherex_core.solar.service
from .solar.service import (asteroid_submit, clean_exposures, clean_summary, comet_search_submit,  # noqa: E402, F401
                            comet_submit, comet_watch, comet_watch_refresh, static_sky_submit, unknown_movers_submit)


# ---------------------------------------------------------------- sky map (ADR-0061)


async def hips_file(survey: str, path: str):
    """Local copy of a HiPS file (tile, Allsky, properties, Moc), fetched from the source once."""
    from . import hips

    return await hips.get(survey, path)


async def coverage_at(ra: float, dec: float) -> dict:
    """SPHEREx exposures and visits covering a position (map pick card)."""
    from . import coverage

    return await asyncio.to_thread(coverage.at, ra, dec)


async def footprints_near(ra: float, dec: float, radius_deg: float, limit: int = 600) -> dict:
    """Outlines of SPHEREx exposures near a position (map coverage layer)."""
    from . import coverage

    return await asyncio.to_thread(coverage.footprints, ra, dec, radius_deg, limit)
