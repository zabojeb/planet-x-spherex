"""Use cases of the solar-system analyses, shared by the HTTP API and the MCP server (re-exported by services.py).

* static_sky_submit: the build of a co-moving sequence with the static sky subtracted, on the build queue
  (spherex_core.jobs): deduplicated by the sequence id, cancellable, run by the workers.
* comet_submit / asteroid_submit / unknown_movers_submit: jobs on the field-analysis runner (field.jobs, ids
  '<sequence>-c|a|u<hash>'), polled and fetched with the field job endpoints (GET /api/field/{job}).
* clean_exposures / clean_preview: static-sky subtracted single exposures and an epoch summary for clients.
"""

from __future__ import annotations

import json
from dataclasses import fields

import numpy as np

from ..bands import BANDS
from ..errors import NotFound
from ..pipeline import results


def _meta(seq_id: str) -> dict:
    from .. import services

    return services.load_sequence(seq_id)


def clean_params(meta: dict):
    """SequenceParams of the static-sky build for a co-moving sequence's metadata."""
    from ..pipeline.params import SequenceParams

    if (meta.get("grid") or {}).get("frame") != "comoving":
        raise NotFound("static-sky subtraction needs a co-moving sequence of a solar-system object")
    names = {f.name for f in fields(SequenceParams)}
    kw = {k: (tuple(v) if isinstance(v, list) else v) for k, v in (meta.get("params") or {}).items() if k in names}
    kw["static_sky"] = True
    return SequenceParams(**kw)


async def static_sky_submit(seq_id: str, *, force: bool = False, ip: str | None = None,
                            client: str | None = None) -> dict:
    """Start (or join) the static-sky build of a co-moving sequence. Returns {'sequence': id of the clean build,
    'source': seq_id, 'status', 'job'}; poll GET /api/sequences/{sequence}. A sequence that already has clean
    arrays answers 'done' with its own id."""
    from .. import services

    meta = _meta(seq_id)
    if (meta.get("arrays") or {}).get("clean"):
        return {"sequence": seq_id, "source": seq_id, "status": "done", "job": None}
    p = clean_params(meta)
    status, _ = await services.poll_sequence(p.key(), client)
    if status == "done" and not force:
        return {"sequence": p.key(), "source": seq_id, "status": "done", "job": None}
    job = await services.submit_sequence(p, force=force, ip=ip, client=client)
    return {"sequence": p.key(), "source": seq_id, "status": job.get("status"), "job": job}


def clean_exposures(seq_id: str, epoch: int, band: str) -> tuple[np.ndarray, list[int]]:
    """Static-sky subtracted single exposures of one epoch and detector, float32 [k, n, n], and their indices."""
    from .. import services

    d = services.result_dir(seq_id)
    return results.read_exposures(d, results.read_meta(d), epoch, band, name="clean_exposures")


def clean_summary(seq_id: str, epoch: int | None = None) -> dict:
    """What a client needs to show the clean mode: per epoch and detector the clean/plain noise and object S/N,
    the static-sky statistics, and the array names to fetch (GET /api/sequences/{id}/clean.bin?epoch=&band=)."""
    from .. import services

    d = services.result_dir(seq_id)
    meta = results.read_meta(d)
    info = meta.get("static_sky")
    if not info:
        raise NotFound("this sequence has no static-sky subtraction; POST /api/sequences/{id}/static-sky")
    clean = results.read_array(d, meta, "clean")
    plain = results.read_array(d, meta, "coadds")
    n = clean.shape[-1]
    c = (n - 1) / 2.0
    yy, xx = np.mgrid[0:n, 0:n]
    r = np.hypot(xx - c, yy - c)
    outer, core = r > 0.6 * n / 2, r <= 1.5
    rows = []
    for e in meta["epochs"]:
        if epoch is not None and e["index"] != epoch:
            continue
        for bi, b in enumerate(BANDS):
            k, p = clean[e["index"], bi], plain[e["index"], bi]
            if not np.isfinite(k).any():
                continue

            def noise(img):
                v = img[outer & np.isfinite(img)]
                return float(1.4826 * np.median(np.abs(v - np.median(v)))) if v.size > 20 else None

            nk, npl = noise(k), noise(p)
            peak = float(np.nanmean(k[core])) if np.isfinite(k[core]).any() else None
            rows.append({"epoch": e["index"], "band": b, "noise_clean": nk, "noise_plain": npl,
                         "noise_ratio": (nk / npl) if nk and npl else None, "centre_clean": peak,
                         "centre_snr": (peak / nk) if peak is not None and nk else None})
    return {"sequence": seq_id, "object": (meta.get("moving") or {}).get("object"), "static_sky": info,
            "arrays": {"clean": meta["arrays"].get("clean"), "clean_coverage": meta["arrays"].get("clean_coverage"),
                       "clean_exposures": meta["arrays"].get("clean_exposures")},
            "epochs": rows}


def _field_job(seq_id: str, kind: str, params: dict):
    from ..field import jobs as fj

    return fj, fj.make_id(seq_id, kind, params)


async def comet_submit(seq_id: str, options: dict | None = None, client: str | None = None,
                       force: bool = False) -> dict:
    """Comet analysis of a co-moving sequence (a job; poll GET /api/field/{id})."""
    from .comet import CometOptions, comet_analysis

    opts = CometOptions.from_dict(options)
    fj, jid = _field_job(seq_id, "c", opts.to_dict())

    def work(ctx, progress, check):
        return comet_analysis(ctx, opts, progress, check)

    return await fj.field_jobs().submit_work(seq_id, jid, work, client, force)


async def asteroid_submit(seq_id: str, options: dict | None = None, client: str | None = None,
                          force: bool = False) -> dict:
    """Asteroid light curve, period, colours and rough taxonomy of a co-moving sequence (a job)."""
    from .asteroid import AsteroidOptions, asteroid_analysis

    opts = AsteroidOptions.from_dict(options)
    fj, jid = _field_job(seq_id, "a", opts.to_dict())

    def work(ctx, progress, check):
        return asteroid_analysis(ctx, opts, progress, check)

    return await fj.field_jobs().submit_work(seq_id, jid, work, client, force)


async def unknown_movers_submit(seq_id: str, vmag_lim: float = 21.0, field_job: str | None = None,
                                client: str | None = None, force: bool = False) -> dict:
    """Unidentified-mover candidates of a fixed-position sequence (a job). field_job: a finished field analysis
    (or moving-objects) job of the same sequence to reuse instead of detecting again."""
    from .movers import MOVERS_VERSION, unknown_movers

    reuse = None
    if field_job:
        from ..field.jobs import result_path, split_id

        seq, key = split_id(field_job)
        if seq != seq_id or key[0] != "f":
            raise ValueError("field_job must be a field analysis job of the same sequence")
        p = result_path(field_job)
        if not p.exists():
            raise NotFound("field analysis result not ready")
        reuse = json.loads(p.read_text())
    fj, jid = _field_job(seq_id, "u", {"vmag": vmag_lim, "reuse": field_job, "v": MOVERS_VERSION})

    def work(ctx, progress, check):
        return unknown_movers(ctx, vmag_lim, reuse, progress, check)

    return await fj.field_jobs().submit_work(seq_id, jid, work, client, force)
