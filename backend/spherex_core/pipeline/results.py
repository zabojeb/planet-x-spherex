"""The result format of a built sequence. This is the only module that writes or reads it.

    <cache>/sequences/<id>/meta.json       metadata, JSON without NaN (see assemble.py for the fields)
                           coadds.f32      float32 LE [epochs, 6, n, n], MJy/sr, NaN where no data
                           coverage.u16    uint16  LE [epochs, 6, n, n], frames per pixel
                           flags.u32       uint32  LE [epochs, 6, n, n], OR of FLAGS bits
                           exposures.f16   float16 LE [frames, n, n], aligned single exposures (optional)

Directories are written to "<id>.tmp" and renamed, so a complete meta.json means a complete result.
"""

from __future__ import annotations

import json
import math
import shutil
from pathlib import Path

import numpy as np

from ..bands import BANDS
from ..errors import NotFound

META = "meta.json"
ARRAY_FILES = {"coadds": "coadds.f32", "coverage": "coverage.u16", "flags": "flags.u32"}
EXPOSURES_FILE = "exposures.f16"
# array name -> (numpy dtype on disk, dtype name in meta.json)
DTYPES = {"coadds": ("<f4", "float32"), "coverage": ("<u2", "uint16"), "flags": ("<u4", "uint32"),
          "exposures": ("<f2", "float16")}


def clean_json(obj):
    """JSON-safe copy: numpy scalars to Python, NaN/inf to None (browsers reject NaN in JSON)."""
    if isinstance(obj, dict):
        return {k: clean_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean_json(v) for v in obj]
    if isinstance(obj, (np.floating, float)):
        return float(obj) if math.isfinite(obj) else None
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.bool_):
        return bool(obj)
    return obj


def is_complete(result_dir: Path) -> bool:
    return (result_dir / META).exists()


def write_result(result_dir: Path, meta: dict, arrays: dict[str, np.ndarray]) -> None:
    """Write arrays and meta.json atomically (temporary directory, then rename)."""
    tmp = result_dir.with_name(result_dir.name + ".tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    for name, arr in arrays.items():
        file = EXPOSURES_FILE if name == "exposures" else ARRAY_FILES[name]
        arr.astype(DTYPES[name][0]).tofile(tmp / file)
    (tmp / META).write_text(json.dumps(clean_json(meta)))
    shutil.rmtree(result_dir, ignore_errors=True)
    tmp.rename(result_dir)


def meta_bytes(result_dir: Path) -> bytes:
    return (result_dir / META).read_bytes()


def read_meta(result_dir: Path) -> dict:
    return json.loads(meta_bytes(result_dir))


def array_path(result_dir: Path, name: str) -> Path:
    if name not in ARRAY_FILES:
        raise NotFound("unknown array")
    return result_dir / ARRAY_FILES[name]


def read_array(result_dir: Path, meta: dict, name: str) -> np.ndarray:
    """A co-add cube (coadds, coverage or flags) with the shape recorded in meta.json."""
    spec = meta["arrays"][name]
    return np.fromfile(array_path(result_dir, name), dtype=DTYPES[name][0]).reshape(spec["shape"])


def read_exposures(result_dir: Path, meta: dict, epoch: int, band: str) -> tuple[np.ndarray, list[int]]:
    """Aligned single exposures of one epoch and detector as float32 [k, n, n], plus their indices in
    meta['exposures']. Exposures are stored sorted by (epoch, band, time), so the slice is contiguous."""
    spec = meta["arrays"].get("exposures")
    if spec is None:
        raise NotFound("single exposures are not stored for this field size")
    idx = [e["index"] for e in meta["exposures"] if e["epoch"] == epoch and e["band"] == band]
    if band not in BANDS or not idx:
        raise NotFound("no exposures for this epoch and detector")
    arr = np.memmap(result_dir / spec["file"], dtype=DTYPES["exposures"][0], mode="r", shape=tuple(spec["shape"]))
    return np.asarray(arr[idx[0]: idx[-1] + 1], dtype="<f4"), idx
