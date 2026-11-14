"""Load a built sequence for analysis: result directory, parameters, grid and the Level 2 file of each exposure."""

from __future__ import annotations

import math
from dataclasses import dataclass, fields
from pathlib import Path

from .. import cache
from ..archive import Frame, query_frames
from ..bands import NATIVE_PIXSCALE
from ..errors import NotFound
from ..pipeline import results
from ..pipeline.params import SequenceParams
from ..pipeline.sequence import SEARCH_EPOCH
from ..processing import Grid
from ..timeutil import year_to_mjd


@dataclass
class SequenceContext:
    seq_id: str
    dir: Path
    meta: dict
    params: SequenceParams
    grid: Grid


def cutout_half(grid: Grid) -> int:
    """Half-size in native pixels of the cutouts a sequence reads, so the grid is covered at any rotation.
    It is part of the cutout cache key, so it must match pipeline.sequence.build_sequence."""
    return int(math.ceil(grid.n * grid.pixscale / NATIVE_PIXSCALE * 0.5 * 1.42)) + 3


def _params_from_meta(p: dict) -> SequenceParams:
    known = {f.name for f in fields(SequenceParams)}
    kw = {k: v for k, v in p.items() if k in known}
    for k in ("collections", "bands"):
        if kw.get(k) is not None:
            kw[k] = tuple(kw[k])
    return SequenceParams(**kw)


def load_context(seq_id: str) -> SequenceContext:
    """A complete sequence by id. Raises NotFound when it is unknown or still building."""
    d = cache.layout().sequence(seq_id) if seq_id.isalnum() else None
    if d is None or not results.is_complete(d):
        raise NotFound("sequence not ready")
    meta = results.read_meta(d)
    g = meta["grid"]
    return SequenceContext(seq_id, d, meta, _params_from_meta(meta["params"]),
                           Grid(float(g["ra"]), float(g["dec"]), int(g["n"]), float(g["pixscale_arcsec"])))


def sequence_frames(ctx: SequenceContext, use_cache: bool = True) -> list[Frame | None]:
    """The Level 2 file of every entry in meta['exposures'], in the same order (None if it is no longer listed).

    meta.json keeps only file names, so the files are looked up with the same footprint search the build ran
    (the TAP cache when fresh, otherwise a new query; new releases only add files)."""
    ra_q, dec_q = ctx.params.target.position_at(year_to_mjd(SEARCH_EPOCH))
    frames = query_frames(float(ra_q), float(dec_q), ctx.params.collections, use_cache=use_cache)
    by_name = {f.file_name: f for f in frames}
    out = [by_name.get(e["file"]) for e in ctx.meta["exposures"]]
    if use_cache and any(f is None for f in out):
        return sequence_frames(ctx, use_cache=False)
    return out
