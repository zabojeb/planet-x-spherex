"""Load a built sequence for analysis: result directory, parameters, grid and the Level 2 file of each exposure."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, fields
from pathlib import Path

from .. import cache
from ..archive import Frame
from ..bands import NATIVE_PIXSCALE
from ..config import settings
from ..errors import NotFound
from ..pipeline import results
from ..pipeline.params import SequenceParams
from ..pipeline.sequence import SEARCH_EPOCH
from ..processing import Grid
from ..timeutil import year_to_mjd

FRAMES_FILE = "frames.json"
_FRAMES_MEMO: dict[str, list[Frame | None]] = {}
_NAME = re.compile(r"^level2_(?P<week>\d{4}W\d{2})_(?P<grp>[0-9A-Z]+)_\d+_\d+D(?P<det>[1-6])_spx_(?P<ver>.+)\.fits$")


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
    """The Level 2 file of every entry in meta['exposures'], in the same order (None if it cannot be located).

    Never queries IRSA: an existing sequence already knows its files, and an analysis must not wait on (or
    fail with) a slow archive. In order of preference:
      1. frames.json written next to the result by the build (every Frame, keyed by file name);
      2. the build's footprint search in the TAP cache, whatever its age (past exposures do not move);
      3. the IRSA path rebuilt from the file name and collection — every DR1/QR3 Level 2 file lives at
         ibe/data/spherex/<dr1|qr3>/level2/<week>_<group>/<version>/<detector>/<file>.
    """
    if use_cache and ctx.seq_id in _FRAMES_MEMO:
        return _FRAMES_MEMO[ctx.seq_id]
    exposures = ctx.meta["exposures"]
    by_name: dict[str, Frame] = {}
    try:
        by_name = {k: Frame(**v) for k, v in json.loads((ctx.dir / FRAMES_FILE).read_text()).items()}
    except (OSError, ValueError, TypeError):
        pass
    if any(e["file"] not in by_name for e in exposures):
        ra_q, dec_q = ctx.params.target.position_at(year_to_mjd(SEARCH_EPOCH))
        tap = cache.layout().tap(float(ra_q), float(dec_q), tuple(ctx.params.collections or settings.collections))
        try:
            for d in json.loads(tap.read_text()):
                by_name.setdefault(d["uri"].rsplit("/", 1)[-1], Frame(**d))
        except (OSError, ValueError, TypeError, KeyError):
            pass
    out = [by_name.get(e["file"]) or frame_from_exposure(e) for e in exposures]
    _FRAMES_MEMO[ctx.seq_id] = out
    return out


def frame_from_exposure(e: dict) -> Frame | None:
    """A Frame for a meta.json exposure rebuilt from its file name and collection (None if it does not parse)."""
    m = _NAME.match(e.get("file") or "")
    coll = str(e.get("collection") or "")
    release = "dr1" if coll.startswith("spherex_dr1") else "qr3" if coll.startswith("spherex_qr3") else None
    if not m or release is None:
        return None
    uri = f"ibe/data/spherex/{release}/level2/{m['week']}_{m['grp']}/{m['ver']}/{m['det']}/{e['file']}"
    mjd = float(e["mjd"])
    wl = float(e.get("wl") or 0.0)
    return Frame(uri=uri, band=e["band"], mjd=mjd, mjd_start=mjd, mjd_end=mjd, collection=coll,
                 obs_id=str(e.get("obs_id") or ""), wl_min_um=wl, wl_max_um=wl)
