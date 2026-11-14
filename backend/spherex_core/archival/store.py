"""Result format of archival epochs. The only module that reads or writes it.

    <cache>/archival/<sequence id>/<n>/meta.json   frames list (survey, band, dates, sources, target pixel)
                                       frames.f32  float32 LE [frames, n, n], MJy/sr, NaN where no data

n is the grid size: the sequence's own grid by default, a larger concentric grid when a wider field was
requested. Written to "<n>.tmp" and renamed, so a complete meta.json means a complete result.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np

from .. import cache
from ..errors import NotFound
from ..pipeline.results import clean_json

META = "meta.json"
FRAMES = "frames.f32"


def result_dir(seq_id: str, n: int) -> Path:
    return cache.layout().root / "archival" / seq_id / str(int(n))


def is_complete(d: Path) -> bool:
    return (d / META).exists()


def write(d: Path, meta: dict, stack: np.ndarray) -> None:
    tmp = d.with_name(d.name + ".tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    stack.astype("<f4").tofile(tmp / FRAMES)
    (tmp / META).write_text(json.dumps(clean_json(meta)))
    shutil.rmtree(d, ignore_errors=True)
    tmp.rename(d)


def meta_bytes(d: Path) -> bytes:
    if not is_complete(d):
        raise NotFound("archival epochs not ready")
    return (d / META).read_bytes()


def read_meta(d: Path) -> dict:
    return json.loads(meta_bytes(d))


def read_frames(d: Path, index: int | None = None) -> np.ndarray:
    """All frames [k, n, n], or one [n, n] when index is given."""
    meta = read_meta(d)
    shape = tuple(meta["array"]["shape"])
    if shape[0] == 0:
        raise NotFound("no archival images cover this field")
    arr = np.memmap(d / FRAMES, dtype="<f4", mode="r", shape=shape)
    if index is None:
        return np.asarray(arr)
    if not 0 <= index < shape[0]:
        raise NotFound("no archival frame with this index")
    return np.asarray(arr[index])
