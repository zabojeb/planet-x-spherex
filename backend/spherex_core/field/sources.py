"""Source detection over the whole field and per-detector co-add photometry.

Peaks are found in the S/N map of every epoch's co-add and of the all-epoch mean, per detector, then merged
across detectors and epochs within MERGE_RADIUS (a fast mover drifts ~1.6 px in our baseline, so detections of
one star at different epochs merge). Each source keeps where it was seen and its flux per detector in the
all-epoch mean image.
"""

from __future__ import annotations

import numpy as np

from ..analysis.motion import centroid
from ..bands import BANDS
from ..flags import FILLED, SATURATION
from .common import Check, FieldData, aperture_fluxes, local_peaks, no_check, snr_image, to_mjy

MERGE_RADIUS = 2.0  # px
EPOCH_EXTRA_SNR = 1.0  # single-epoch detections need this much more S/N than the deep threshold
BRIGHT_MERGE_SNR = 50.0
ABSORB_RATIO = 5.0
BLEND_RATIO = 5.0
BLEND_RADIUS = 5.0  # px


def deep_images(fd: FieldData) -> dict[int, np.ndarray]:
    """Mean over epochs per detector (NaN where no epoch has data)."""
    out = {}
    for b in fd.bands_present():
        planes = [fd.coadds[e, b] for e in range(fd.coadds.shape[0]) if np.isfinite(fd.coadds[e, b]).any()]
        with np.errstate(all="ignore"):
            out[b] = np.nanmean(np.stack(planes), axis=0)
    return out


def detect(fd: FieldData, snr_min: float = 5.0, max_sources: int = 300, check: Check = no_check) -> list[dict]:
    deep = deep_images(fd)
    dets: list[tuple[float, float, float, int, int | None]] = []  # snr, x, y, band, epoch
    for b, img in deep.items():
        s = snr_image(img)
        for y, x in zip(*local_peaks(s, snr_min)):
            dets.append((float(s[y, x]), float(x), float(y), b, None))
        for e in range(fd.coadds.shape[0]):
            plane = fd.coadds[e, b]
            if not np.isfinite(plane).any():
                continue
            s = snr_image(plane)
            for y, x in zip(*local_peaks(s, snr_min + EPOCH_EXTRA_SNR)):
                dets.append((float(s[y, x]), float(x), float(y), b, e))
        check()
    dets.sort(key=lambda d: -d[0])
    masters: list[dict] = []
    mx = np.empty(len(dets))
    my = np.empty(len(dets))
    for snr, x, y, b, e in dets:
        best = None
        if masters:
            d = np.hypot(mx[:len(masters)] - x, my[:len(masters)] - y)
            k = int(np.argmin(d))
            if d[k] <= MERGE_RADIUS:
                best = masters[k]
        if best is None:
            best = {"_x0": x, "_y0": y, "_pts": [], "snr": snr, "bands": set(), "epochs": set()}
            mx[len(masters)], my[len(masters)] = x, y
            masters.append(best)
        best["_pts"].append((x, y, snr, e is None))
        best["bands"].add(BANDS[b])
        if e is not None:
            best["epochs"].add(e)
        best["snr"] = max(best["snr"], snr)
    masters.sort(key=lambda m: -m["snr"])
    masters = _absorb(masters)[:max_sources]

    best_band = max(deep, key=lambda b: np.isfinite(deep[b]).sum()) if deep else None
    sources = []
    for i, m in enumerate(masters):
        pts = [p for p in m["_pts"] if p[3]] or m["_pts"]
        w = np.array([p[2] for p in pts])
        x = float(np.average([p[0] for p in pts], weights=w))
        y = float(np.average([p[1] for p in pts], weights=w))
        if best_band is not None:
            c = centroid(np.nan_to_num(deep[best_band], nan=0.0), x, y, max_shift=1.5)
            if c is not None:
                x, y = c["x"], c["y"]
        if any(np.hypot(o["x"] - x, o["y"] - y) <= MERGE_RADIUS for o in sources):
            continue  # centroided onto a brighter source already listed
        sources.append({"id": f"S{len(sources) + 1:03d}", "x": x, "y": y, "snr": float(m["snr"]),
                        "bands_detected": sorted(m["bands"], key=BANDS.index),
                        "epochs_detected": sorted(m["epochs"])})
    if sources:
        ra, dec = fd.sky([s["x"] for s in sources], [s["y"] for s in sources])
        for s, r, d in zip(sources, ra, dec):
            s["ra"], s["dec"] = float(r), float(d)
        _deep_photometry(fd, sources, deep)
        mark_blends(sources)
    return sources


def _bright_radius(snr: float) -> float:
    """Pixels around a bright star within which fainter peaks are its own wings, rings or saturation
    residuals rather than separate sources."""
    if snr <= BRIGHT_MERGE_SNR:
        return MERGE_RADIUS
    return float(min(MERGE_RADIUS + 2.0 * np.log10(snr / BRIGHT_MERGE_SNR), 6.0))


def _absorb(masters: list[dict]) -> list[dict]:
    """Fold peaks that sit in the halo of a much brighter source into it (masters sorted by S/N)."""
    keep: list[dict] = []
    for m in masters:
        host = None
        for k in keep:
            if k["snr"] >= ABSORB_RATIO * m["snr"] and np.hypot(k["_x0"] - m["_x0"], k["_y0"] - m["_y0"]) <= \
                    _bright_radius(k["snr"]):
                host = k
                break
        if host is None:
            keep.append(m)
        else:
            host["bands"] |= m["bands"]
            host["epochs"] |= m["epochs"]
    return keep


def mark_blends(sources: list[dict]) -> None:
    """`blended_with`: a source at least BLEND_RATIO times brighter within BLEND_RADIUS (its centroids and
    fluxes are pulled by the neighbour)."""
    for s in sources:
        s["blended_with"] = None
        for o in sources:
            if o is not s and o["snr"] >= BLEND_RATIO * s["snr"] and \
                    np.hypot(o["x"] - s["x"], o["y"] - s["y"]) <= BLEND_RADIUS:
                s["blended_with"] = o["id"]
                break


def _deep_photometry(fd: FieldData, sources: list[dict], deep: dict[int, np.ndarray]) -> None:
    """Flux (mJy, approximate) and S/N per detector in the all-epoch mean, and whether the core is saturated."""
    xs = np.array([s["x"] for s in sources])
    ys = np.array([s["y"] for s in sources])
    for s in sources:
        s["flux_mjy"], s["flux_err_mjy"], s["snr_band"] = {}, {}, {}
    for b, img in deep.items():
        flux, err, masked = aperture_fluxes(img, xs, ys)
        f_mjy, e_mjy = to_mjy(flux), to_mjy(err)
        for s, f, e, m in zip(sources, f_mjy, e_mjy, masked):
            if np.isfinite(f) and m < 0.5:
                s["flux_mjy"][BANDS[b]] = float(f)
                s["flux_err_mjy"][BANDS[b]] = float(e) if np.isfinite(e) else None
                s["snr_band"][BANDS[b]] = float(f / e) if e and np.isfinite(e) and e > 0 else None
    sat = 0
    for b in range(fd.flags.shape[1]):
        sat |= np.bitwise_or.reduce(fd.flags[:, b], axis=0).astype(np.int64)
    for s in sources:
        xi, yi = int(round(s["x"])), int(round(s["y"]))
        y0, y1 = max(yi - 1, 0), min(yi + 2, fd.n)
        x0, x1 = max(xi - 1, 0), min(xi + 2, fd.n)
        core = int(np.bitwise_or.reduce(sat[y0:y1, x0:x1].ravel())) if y1 > y0 and x1 > x0 else 0
        s["saturated"] = bool(core & (SATURATION | FILLED))
