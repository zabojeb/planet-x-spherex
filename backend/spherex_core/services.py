"""Interface-agnostic facade over the library.

The HTTP API (spherex_api) and the MCP server call only these functions, so validation, error
messages and the job registry live in one place. Blocking functions (network lookups) should be
run in a worker thread by async callers; submit_sequence must run on the event loop.

Errors: NotFound (unknown or not-ready resource, message safe for users), LookupError from
resolve_target, RuntimeError when a remote catalog service fails.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from . import __version__, cache, catalogs
from .config import settings
from .errors import NotFound
from .pipeline import results
from .pipeline.jobs import JobManager
from .pipeline.params import SequenceParams
from .targets import PRESETS, resolve

__all__ = ["NotFound", "SequenceParams", "array_file", "catalog_cone", "configure", "health", "jobs",
           "list_catalogs", "list_presets", "load_sequence", "read_array", "read_exposures", "resolve_target",
           "sequence_meta_bytes", "sequence_status", "submit_sequence"]

_jobs: JobManager | None = None


def configure(cache_root: Path | None = None) -> None:
    """Point the services at another cache root (tests, embedded use); None restores settings.cache_dir."""
    global _jobs
    cache.set_root(cache_root)
    _jobs = None


def jobs() -> JobManager:
    global _jobs
    if _jobs is None:
        _jobs = JobManager(cache.layout().sequences)
    return _jobs


# ---------------------------------------------------------------- service info and targets
def health() -> dict:
    return {"ok": True, "version": __version__, "collections": settings.collections}


def list_presets() -> list[dict]:
    return [t.to_dict() for t in PRESETS.values()]


def resolve_target(query: str) -> dict:
    """Coordinates, preset id/name or object name -> target dict. Raises LookupError."""
    return resolve(query).to_dict()


# ---------------------------------------------------------------- sequences
async def submit_sequence(params: SequenceParams, force: bool = False) -> dict:
    """Start (or reuse) a build; returns the job dict. Must be awaited on the event loop."""
    return jobs().submit(params, force=force).to_dict()


def _result_dir(seq_id: str) -> Path | None:
    return cache.layout().sequence(seq_id) if seq_id.isalnum() else None


def _ready_dir(seq_id: str) -> Path:
    d = _result_dir(seq_id)
    if d is None or not results.is_complete(d):
        raise NotFound("sequence not ready")
    return d


def sequence_status(seq_id: str) -> tuple[str, dict | None]:
    """('done', None) when the result exists, (job status, job dict) while queued/running/failed,
    ('unknown', None) when nothing is known about the id."""
    d = _result_dir(seq_id)
    if d is not None and results.is_complete(d):
        return "done", None
    job = jobs().get(seq_id)
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
    """Catalog sources around (ra, dec). Raises NotFound for an unknown catalog, RuntimeError if the
    remote service fails."""
    if name not in catalogs.CATALOGS:
        raise NotFound("unknown catalog")
    return catalogs.cone(name, ra, dec, radius_arcmin / 60.0)
