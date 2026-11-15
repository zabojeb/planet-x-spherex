"""Track & Stack inside a visit, and the synthetic-tracking search over trial motions.

Asteroids move degrees between SPHEREx's 6-month visits, so stacking along a motion is done within one visit,
on the aligned single exposures (minutes to days apart). `stack` shifts every exposure of the visit by
v * (t - t_ref) (NaN-aware bilinear, as the build) and combines them by weighted mean (1/rms^2), median or
sum, optionally as independent sub-stacks (thirds by time, Astrometrica's re-stack presets). t_ref is the
weighted mean time of the frames used.

`search` runs synthetic tracking on the static-subtracted S/N maps (exposure - epoch co-add): a lattice of
trial velocities within a time window, integer half-pixel shifts on 2x-upsampled maps, the best S/N per pixel.
Following the MPC warning on stacked noise, a candidate must also reach SUB_SNR in at least two of three
independent sub-stacks (time thirds) at its best velocity.
"""

from __future__ import annotations

import numpy as np
from scipy.ndimage import shift as nd_shift

from ..bands import BANDS
from ..errors import NotFound
from ..pipeline import results
from .common import Check, FieldData, local_peaks, no_check

COMBINE = ("mean", "median", "sum")
MAX_AXIS_TRIALS = 41  # trial velocities per axis
SUB_SNR = 3.0
UPSAMPLE = 2


def _frames(fd: FieldData, epoch: int, bands: list[str] | None, window: tuple[float, float] | None):
    """Aligned exposures of one epoch: (stack [k,n,n], meta rows)."""
    if not 0 <= epoch < fd.coadds.shape[0]:
        raise NotFound("no such epoch")
    stacks, rows = [], []
    for band in (bands or BANDS):
        try:
            st, idx = results.read_exposures(fd.ctx.dir, fd.meta, epoch, band)
        except NotFound:
            continue
        for k, i in enumerate(idx):
            ex = fd.meta["exposures"][i]
            if window and not window[0] <= ex["mjd"] <= window[1]:
                continue
            stacks.append(st[k])
            rows.append(ex)
    if not rows:
        raise NotFound("no exposures for this epoch, detectors and time window")
    order = np.argsort([r["mjd"] for r in rows])
    return np.stack([stacks[i] for i in order]).astype(np.float64), [rows[i] for i in order]


def _velocity_px_per_hr(rate: float, pa: float, pixscale: float) -> tuple[float, float]:
    """Sky motion (\"/h, PA east of north) -> grid pixel velocity (east is -x)."""
    v = rate / pixscale
    return -v * np.sin(np.radians(pa)), v * np.cos(np.radians(pa))


def stack(fd: FieldData, epoch: int, rate: float, pa: float, *, bands: list[str] | None = None,
          combine: str = "mean", subtract_static: bool = False, parts: int = 1,
          window: tuple[float, float] | None = None, t_ref: float | None = None) -> dict:
    if combine not in COMBINE:
        raise ValueError(f"combine must be one of {COMBINE}")
    if parts not in (1, 2, 3):
        raise ValueError("parts must be 1, 2 or 3")
    imgs, rows = _frames(fd, epoch, bands, window)
    if subtract_static:
        for k, r in enumerate(rows):
            imgs[k] -= fd.coadds[epoch, BANDS.index(r["band"])]
    w = np.array([1.0 / r["rms"] ** 2 if r.get("rms") else 0.0 for r in rows])
    w = np.where(np.isfinite(w), w, 0.0)
    t = np.array([r["mjd"] for r in rows])
    t_ref = float(t_ref if t_ref is not None else (np.sum(w * t) / w.sum() if w.sum() > 0 else t.mean()))
    vx, vy = _velocity_px_per_hr(rate, pa, fd.grid.pixscale)
    shifted = np.empty_like(imgs)
    for k in range(len(rows)):
        dt = (t[k] - t_ref) * 24.0
        sh = (-vy * dt, -vx * dt)  # bring the object at x0 + v*dt back to x0
        valid = np.isfinite(imgs[k]).astype(float)
        data = nd_shift(np.nan_to_num(imgs[k]), sh, order=1, mode="constant", cval=0.0)
        cover = nd_shift(valid, sh, order=1, mode="constant", cval=0.0)
        with np.errstate(all="ignore"):
            shifted[k] = np.where(cover > 0.5, data / cover, np.nan)
    out = []
    for part in np.array_split(np.arange(len(rows)), parts):
        sub, ws = shifted[part], w[part]
        with np.errstate(all="ignore"):
            if combine == "median":
                img = np.nanmedian(sub, axis=0)
            else:
                good = np.isfinite(sub)
                wsum = (ws[:, None, None] * good).sum(axis=0)
                total = (np.nan_to_num(sub) * ws[:, None, None]).sum(axis=0)
                img = np.where(wsum > 0, total / wsum, np.nan)
                if combine == "sum":
                    img = img * good.sum(axis=0)
        out.append({"image": img.astype(np.float32), "frames": int(len(part)),
                    "mjd_min": float(t[part].min()), "mjd_max": float(t[part].max())})
    return {"t_ref": t_ref, "rate_arcsec_per_hr": rate, "pa_deg": pa, "combine": combine, "epoch": epoch,
            "subtract_static": subtract_static, "frames": len(rows),
            "bands": sorted({r["band"] for r in rows}, key=BANDS.index), "parts": out}


def densest_window(times: np.ndarray, hours: float) -> tuple[float, float]:
    t = np.sort(times)
    span = hours / 24.0
    j, best, best_i = 0, 0, 0
    for i in range(len(t)):
        while t[i] - t[j] > span:
            j += 1
        if i - j + 1 > best:
            best, best_i = i - j + 1, j
    return float(t[best_i]), float(t[best_i] + span)


def search(fd: FieldData, epoch: int, *, rate_min: float = 0.3, rate_max: float = 10.0, window_hours: float = 48.0,
           bands: list[str] | None = None, snr_min: float = 6.0, maps: dict | None = None,
           check: Check = no_check) -> dict:
    """Synthetic tracking over a lattice of trial velocities within the densest `window_hours` of the visit."""
    from .transients import difference_maps

    maps = maps if maps is not None else difference_maps(fd, check)
    rows = [(i, v) for i, v in maps.items() if i is not None and v[1] == epoch
            and (bands is None or BANDS[v[2]] in bands)]
    if len(rows) < 4:
        raise NotFound("fewer than four exposures in this epoch")
    exposures = fd.meta["exposures"]
    times = np.array([exposures[i]["mjd"] for i, _ in rows])
    w0, w1 = densest_window(times, window_hours)
    sel = [(i, v) for (i, v), tm in zip(rows, times) if w0 <= tm <= w1]
    t = np.array([exposures[i]["mjd"] for i, _ in sel])
    t_ref = float(t.mean())
    dt_h = (t - t_ref) * 24.0
    span = float(dt_h.max() - dt_h.min()) or 1.0
    n, up = fd.n, UPSAMPLE
    z = [np.repeat(np.repeat(v[0].astype(np.float64), up, axis=0), up, axis=1) for _, v in sel]
    vmax_px = rate_max / fd.grid.pixscale  # px/h
    # half-pixel lattice in end-to-end displacement, capped to MAX_AXIS_TRIALS per axis
    step = max(0.5 / span, 2 * vmax_px / (MAX_AXIS_TRIALS - 1))
    axis = np.arange(-vmax_px, vmax_px + step / 2, step)
    pad = int(np.ceil(vmax_px * np.abs(dt_h).max() * up)) + 2
    zp = [np.pad(zi, pad, constant_values=np.nan) for zi in z]
    best = np.full((n * up, n * up), -np.inf)
    best_v = np.zeros((n * up, n * up, 2))
    trials = 0
    for vx in axis:
        check()
        for vy in axis:
            rate = np.hypot(vx, vy) * fd.grid.pixscale
            if rate < rate_min or rate > rate_max:
                continue
            trials += 1
            acc = np.zeros((n * up, n * up))
            cnt = np.zeros((n * up, n * up))
            for k, zk in enumerate(zp):
                ox, oy = int(round(vx * dt_h[k] * up)), int(round(vy * dt_h[k] * up))
                sl = zk[pad + oy: pad + oy + n * up, pad + ox: pad + ox + n * up]
                ok = np.isfinite(sl)
                acc[ok] += sl[ok]
                cnt[ok] += 1
            snr = np.where(cnt >= max(3, len(zp) // 3), acc / np.sqrt(np.maximum(cnt, 1)), -np.inf)
            better = snr > best
            best[better] = snr[better]
            best_v[better] = (vx, vy)
    native = best.reshape(n, up, n, up).max(axis=(1, 3))
    vel = best_v[::up, ::up]
    cands = []
    for y, x in zip(*local_peaks(np.where(np.isfinite(native), native, np.nan), snr_min, size=5, edge=3)):
        uy, ux = np.unravel_index(np.argmax(best[y * up:(y + 1) * up, x * up:(x + 1) * up]), (up, up))
        vx, vy = best_v[y * up + uy, x * up + ux]
        subs = []
        for part in np.array_split(np.arange(len(zp)), 3):
            acc, cnt = 0.0, 0
            for k in part:
                ox, oy = int(round(vx * dt_h[k] * up)), int(round(vy * dt_h[k] * up))
                val = zp[k][pad + oy + y * up + uy, pad + ox + x * up + ux]
                if np.isfinite(val):
                    acc, cnt = acc + val, cnt + 1
            subs.append(float(acc / np.sqrt(cnt)) if cnt else None)
        n_sub = sum(1 for s in subs if s is not None and s >= SUB_SNR)
        if n_sub < 2:
            continue
        rate = float(np.hypot(vx, vy) * fd.grid.pixscale)
        pa = float(np.degrees(np.arctan2(-vx, vy)) % 360.0)
        ra, dec = fd.sky(x, y)
        cands.append({"id": "", "x": float(x), "y": float(y), "ra": float(ra), "dec": float(dec), "mjd_ref": t_ref,
                      "snr": float(native[y, x]), "rate_arcsec_per_hr": rate, "pa_deg": pa, "sub_snr": subs,
                      "sub_detections": n_sub, "score": float(native[y, x]) * n_sub / 3.0})
    del vel
    cands.sort(key=lambda c: -c["score"])
    for i, c in enumerate(cands):
        c["id"] = f"K{i + 1:03d}"
    return {"epoch": epoch, "window_mjd": [w0, w1], "t_ref": t_ref, "frames": len(sel), "trials": trials,
            "rate_range_arcsec_per_hr": [rate_min, rate_max], "velocity_step_arcsec_per_hr": step * fd.grid.pixscale,
            "candidates": cands[:50]}
