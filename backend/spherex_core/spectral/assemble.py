"""Combine per-exposure photometry into binned spectra (overall and per epoch).

Each exposure measures the source at one wavelength (with its bandwidth). Measurements falling in the same
bin of GRID are combined with inverse-variance weights after a robust outlier cut; the bin error is the
larger of the formal error and the scatter-based error of the mean, because single-exposure errors are
only approximately right (ADR-0016 added a 3 % floor for that reason).

Absolute spectra use calibrated measurements only (PSF or aperture, not flagged). Ring measurements of
saturated stars are relative: their scale differs per detector, so they are binned separately and only
used where ratios between epochs are what matters (variability).
"""

from __future__ import annotations

import math

import numpy as np

from .grid import GRID, BinGrid

CLIP_SIGMA = 4.0


def usable(records: list[dict], *, relative: bool = False, include_flagged: bool = False) -> list[dict]:
    """Measurements of one kind: calibrated (relative=False) or ring (relative=True), with positive errors."""
    out = []
    for r in records:
        f, e, wl = r.get("flux_mjy"), r.get("err_mjy"), r.get("wl")
        if f is None or e is None or wl is None or not (math.isfinite(f) and math.isfinite(e)) or e <= 0:
            continue
        if bool(r.get("relative")) != relative:
            continue
        if r.get("flagged") and not include_flagged:
            continue
        out.append(r)
    return out


def bin_spectrum(records: list[dict], *, grid: BinGrid = GRID, relative: bool = False,
                 include_flagged: bool = False, clip: float = CLIP_SIGMA) -> list[dict]:
    """Binned spectrum, one dict per non-empty bin, sorted by wavelength.

    Keys: band, wl (weighted mean wavelength of the members), wl_lo, wl_hi, flux_mjy, err_mjy, n, n_rejected,
    chi2 (reduced, of the members about the mean), relative, epochs (sorted list of contributing epochs).
    """
    pts = usable(records, relative=relative, include_flagged=include_flagged)
    if not pts:
        return []
    groups: dict[int, list[dict]] = {}
    for r in pts:
        i = int(grid.index(str(r["band"]), np.array([float(r["wl"])]))[0])
        if i >= 0:
            groups.setdefault(i, []).append(r)
    out = []
    for i in sorted(groups):
        g = groups[i]
        f = np.array([r["flux_mjy"] for r in g], float)
        e = np.array([r["err_mjy"] for r in g], float)
        wl = np.array([r["wl"] for r in g], float)
        keep = np.ones(f.size, bool)
        if f.size >= 3:
            med = np.median(f)
            mad = 1.4826 * np.median(np.abs(f - med))
            scale = max(mad, float(np.median(e)))
            keep = np.abs(f - med) <= clip * scale
            if keep.sum() == 0:
                keep[:] = True
        w = 1.0 / e[keep] ** 2
        mean = float((w * f[keep]).sum() / w.sum())
        err = float(math.sqrt(1.0 / w.sum()))
        n = int(keep.sum())
        chi2 = None
        if n >= 2:
            chi2 = float((w * (f[keep] - mean) ** 2).sum() / (n - 1))
            err = max(err, float(np.std(f[keep], ddof=1) / math.sqrt(n)))
        out.append({
            "band": str(grid.band[i]), "bin": i,
            "wl": float((w * wl[keep]).sum() / w.sum()),
            "wl_lo": float(grid.lo[i]), "wl_hi": float(grid.hi[i]),
            "flux_mjy": mean, "err_mjy": err, "n": n, "n_rejected": int(f.size - n), "chi2": chi2,
            "relative": relative,
            "epochs": sorted({int(r["epoch"]) for r in g if r.get("epoch") is not None}),
        })
    out.sort(key=lambda b: b["wl"])
    return out


def epochs_of(records: list[dict]) -> list[int]:
    return sorted({int(r["epoch"]) for r in records if r.get("epoch") is not None})


def by_epoch(records: list[dict], **kw) -> dict[int, list[dict]]:
    """Binned spectrum of each epoch separately."""
    return {e: bin_spectrum([r for r in records if r.get("epoch") == e], **kw) for e in epochs_of(records)}


def arrays(bins: list[dict]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(wavelength um, flux mJy, error mJy) of a binned spectrum."""
    if not bins:
        z = np.zeros(0)
        return z, z, z
    return (np.array([b["wl"] for b in bins]), np.array([b["flux_mjy"] for b in bins]),
            np.array([b["err_mjy"] for b in bins]))
