"""Slow and distant movers inside one visit: multi-day linking and shift-and-stack with SPHEREx's parallax.

A SPHEREx visit to a field lasts 10-50 days in ~20 groups of exposures (one group = the exposures within an hour,
one orbit's pass). An object slower than ~2"/h (Centaurs, TNOs, distant comets, anything near a stationary point)
moves less than a pixel inside a group but many pixels over the visit, so the field analysis' straight-line
tracklets (one group, > 2"/h) miss it. Here:

* every group becomes one S/N map: its exposures minus the static template of the other visits (frames.py),
  combined as sum(S/N)/sqrt(n), and the per-visit median map is subtracted (it holds the static residuals: stars
  whose colour leaves a wavelength-dependent residual, persistent artefacts);
* the motion model over days is x(t) = x0 + v (t - t_ref) + w B(t), with w = 1/D (au^-1) and B(t) the apparent
  displacement of an object at 1 au caused by the observer's heliocentric motion since t_ref (geometry.
  parallax_basis: Earth's orbit plus SPHEREx's own 98-min orbit around the Earth, from Horizons). Its linear part
  is degenerate with v; its curvature (Earth's orbit) and its 98-min wobble (9.7"/D) are not, so a fit can bound D.
  The model is linear in (x0, y0, vx, vy, w), with 0 <= w <= W_MAX;
* linking: pairs of detections in different groups vote in a Hough space (position at t_ref, velocity), the
  densest cells seed least-squares fits that grow by prediction, and a link is kept when it spans >= 4 groups with
  a small rms and a motion that is significant (a static residual has none);
* stacking: for faint movers, the group maps are shifted along trial (vx, vy, w) on a lattice whose end-to-end
  displacement step is 0.7 px, inside the densest window; trials are capped (the lattice is coarsened and the
  coarsening reported) and the search checks for cancellation between rows.
"""

from __future__ import annotations

import math
import warnings
from dataclasses import dataclass

import numpy as np

from ..field.common import Check, local_peaks, no_check, robust_noise
from .frames import FieldImages

GROUP_GAP_H = 1.0
SLOW_SNR = 4.5
RATE_MAX = 2.5  # "/h; faster movers are the field analysis' tracklets
MIN_DT_D = 0.25
MIN_GROUPS = 4
LINK_TOL = 1.5  # px
MAX_RMS = 0.9  # px
MIN_MOVE = 1.5  # px over the link
MOVE_SIGMA = 5.0
W_MAX = 2.0  # au^-1 (D >= 0.5 au)
CENTROID_SIGMA = 0.35  # px per group detection (for the motion significance)
STACK_SNR = 7.0
STACK_MAD = 7.0  # and this many robust sigmas above the median of the best-trial map
STACK_W = (0.0, 0.25, 0.5, 1.0)
STACK_STEP = 0.7  # px end to end
MAX_TRIALS = 8000  # per window
MAX_TOTAL_TRIALS = 60000  # per visit
MAX_DETS_PER_GROUP = 60
MAX_SEEDS = 200  # Hough cells tried per window


@dataclass
class Group:
    epoch: int
    index: int
    t: float
    exposures: list[int]
    snr: np.ndarray


def groups(fi: FieldImages, epoch: int, check: Check = no_check) -> list[Group]:
    idx = fi.exposures(epoch)
    if not idx:
        return []
    t = np.array([fi.meta["exposures"][i]["mjd"] for i in idx])
    order = np.argsort(t)
    idx, t = [idx[k] for k in order], t[order]
    cuts = np.nonzero(np.diff(t) * 24.0 > GROUP_GAP_H)[0] + 1
    out = []
    for g, part in enumerate(np.split(np.arange(len(idx)), cuts)):
        maps = np.stack([fi.snr(idx[k]) for k in part])
        good = np.isfinite(maps)
        cnt = good.sum(axis=0)
        with np.errstate(all="ignore"):
            s = np.where(cnt > 0, np.where(good, maps, 0.0).sum(axis=0) / np.sqrt(np.maximum(cnt, 1)), np.nan)
        out.append(Group(epoch, g, float(t[part].mean()), [idx[k] for k in part], s.astype(np.float32)))
        check()
    if len(out) >= 5:  # static residuals of this visit
        with np.errstate(all="ignore"), warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)  # pixels masked in every group
            med = np.nanmedian(np.stack([g.snr for g in out]), axis=0)
        med = np.nan_to_num(med)
        for g in out:
            d = g.snr - med
            _, sd = robust_noise(d)
            g.snr = (d / sd if sd > 0 else d).astype(np.float32)
    return out


def _peaks(s: np.ndarray, threshold: float) -> list[tuple[float, float, float]]:
    """(x, y, S/N) of local maxima with a 3x3 centroid."""
    out = []
    ys, xs = local_peaks(np.where(np.isfinite(s), s, np.nan), threshold, size=3, edge=3)
    for y, x in zip(ys, xs):
        w = np.clip(np.nan_to_num(s[y - 1:y + 2, x - 1:x + 2]), 0, None)
        tot = w.sum()
        dx = float((w * np.array([-1, 0, 1])[None, :]).sum() / tot) if tot > 0 else 0.0
        dy = float((w * np.array([-1, 0, 1])[:, None]).sum() / tot) if tot > 0 else 0.0
        out.append((x + dx, y + dy, float(s[y, x])))
    return out


# ---------------------------------------------------------------- fast movers inside one pass
PASS_SNR = 5.5
PASS_MIN_DT = 0.0014  # d (2 min): SPHEREx often sees a field for only ~7 min in a pass
PASS_RATE = (2.0, 300.0)  # "/h: faster, random alignments of residuals in a pass of minutes dominate
PASS_TOL = 1.2  # px
PASS_RMS = 0.6  # px
PASS_MIN_N = 4  # detections, at >= 3 distinct times
PASS_MAX = 12  # tracklets per pass (the brightest)


def pass_tracklets(fi: FieldImages, epoch: int, pixscale: float, check: Check = no_check) -> list[dict]:
    """Straight-line tracklets inside one group of exposures (a pass of minutes), for movers too fast to stay in
    the field until the next pass: a comet at 100"/h crosses 10' in 6 h, and a pass often lasts 7 min (2 px of
    motion). Detections in the static-sky subtracted S/N maps; >= 3 detections at >= 2 distinct times, the
    simultaneous partner detector counting as confirmation."""
    exps = fi.meta["exposures"]
    idx = sorted(fi.exposures(epoch), key=lambda i: exps[i]["mjd"])
    if len(idx) < 3:
        return []
    t = np.array([exps[i]["mjd"] for i in idx])
    cuts = np.nonzero(np.diff(t) * 24.0 > GROUP_GAP_H)[0] + 1
    lo, hi = (r * 24.0 / pixscale for r in PASS_RATE)  # px/day
    out = []
    for g, part in enumerate(np.split(np.arange(len(idx)), cuts)):
        check()
        if len(part) < 3 or np.ptp(t[part]) < PASS_MIN_DT:
            continue
        dets = []
        for k in part:
            for x, y, s in _peaks(fi.snr(idx[k]), PASS_SNR):
                dets.append((t[k], x, y, s, idx[k]))
        if len(dets) < 3:
            continue
        d = np.array([r[:4] for r in dets])
        used = np.zeros(len(d), bool)
        order = np.argsort(-d[:, 3])
        found = []
        for a in order:
            if used[a]:
                continue
            best = None
            for b in range(len(d)):
                dt = d[b, 0] - d[a, 0]
                if used[b] or abs(dt) < PASS_MIN_DT:
                    continue
                dist = math.hypot(d[b, 1] - d[a, 1], d[b, 2] - d[a, 2])
                if dist < 0.8 or not lo <= dist / abs(dt) <= hi:
                    continue
                vx, vy = (d[b, 1] - d[a, 1]) / dt, (d[b, 2] - d[a, 2]) / dt
                px, py = d[a, 1] + vx * (d[:, 0] - d[a, 0]), d[a, 2] + vy * (d[:, 0] - d[a, 0])
                sup = np.nonzero(~used & (np.hypot(d[:, 1] - px, d[:, 2] - py) <= PASS_TOL))[0]
                times = np.unique(np.round(d[sup, 0], 5))
                if len(sup) >= PASS_MIN_N and len(times) >= 3 and (best is None or len(sup) > len(best)):
                    best = sup
            if best is None:
                continue
            tt, xx, yy = d[best, 0], d[best, 1], d[best, 2]
            t_ref = float(tt.mean())
            A = np.column_stack([np.ones_like(tt), tt - t_ref])
            cx, *_ = np.linalg.lstsq(A, xx, rcond=None)
            cy, *_ = np.linalg.lstsq(A, yy, rcond=None)
            rms = float(np.sqrt(np.mean((A @ cx - xx) ** 2 + (A @ cy - yy) ** 2)))
            v = math.hypot(cx[1], cy[1])
            if rms > PASS_RMS or not lo <= v <= hi or v * np.ptp(tt) < 0.8:
                continue
            used[best] = True
            found.append({"epoch": epoch, "group": g, "t_ref": t_ref, "x0": float(cx[0]), "y0": float(cy[0]),
                        "vx": float(cx[1]), "vy": float(cy[1]), "w": 0.0, "rms": rms,
                        "rate_arcsec_per_hr": v * pixscale / 24.0,
                        "pa_deg": float(math.degrees(math.atan2(-cx[1], cy[1])) % 360.0),
                        "mean_snr": float(d[best, 3].mean()), "span_minutes": float(np.ptp(tt) * 1440.0),
                        "detections": [{"exposure": int(dets[j][4]), "mjd": float(d[j, 0]), "x": float(d[j, 1]),
                                        "y": float(d[j, 2]), "snr": float(d[j, 3]),
                                        "band": exps[dets[j][4]]["band"]} for j in best]})
        out.extend(sorted(found, key=lambda f: -f["mean_snr"])[:PASS_MAX])
    return out


def detections(gs: list[Group], threshold: float = SLOW_SNR) -> dict[str, np.ndarray]:
    """Peaks of every group map (the MAX_DETS_PER_GROUP brightest: crowded fields leave many residuals)."""
    rows = []
    for gi, g in enumerate(gs):
        peaks = sorted(_peaks(g.snr, threshold), key=lambda p: -p[2])[:MAX_DETS_PER_GROUP]
        for x, y, s in peaks:
            rows.append((gi, g.t, x, y, s))
    a = np.array(rows, float).reshape(-1, 5)
    return {"g": a[:, 0].astype(int), "t": a[:, 1], "x": a[:, 2], "y": a[:, 3], "snr": a[:, 4]}


def fit(t: np.ndarray, x: np.ndarray, y: np.ndarray, B: np.ndarray, t_ref: float, fit_w: bool = True
        ) -> dict:
    """Least squares of x(t) = x0 + vx dt + w Bx, y(t) = y0 + vy dt + w By (dt in days, B in px at 1 au)."""
    dt = t - t_ref
    k = len(t)
    cols = 5 if fit_w else 4
    A = np.zeros((2 * k, cols))
    A[:k, 0], A[k:, 1] = 1.0, 1.0
    A[:k, 2], A[k:, 3] = dt, dt
    if fit_w:
        A[:k, 4], A[k:, 4] = B[:, 0], B[:, 1]
    b = np.concatenate([x, y])
    p, *_ = np.linalg.lstsq(A, b, rcond=None)
    if fit_w and not (0.0 <= p[4] <= W_MAX):
        w = float(np.clip(p[4], 0.0, W_MAX))
        r = fit(t, x - w * B[:, 0], y - w * B[:, 1], B, t_ref, fit_w=False)
        r["w"], r["w_bound"] = w, True
        r["res"] = np.hypot(x - predict(r, t, B)[0], y - predict(r, t, B)[1])
        r["rms"] = float(np.sqrt(np.mean(r["res"] ** 2)))
        return r
    w = float(p[4]) if fit_w else 0.0
    out = {"x0": float(p[0]), "y0": float(p[1]), "vx": float(p[2]), "vy": float(p[3]), "w": w, "t_ref": t_ref,
           "w_bound": False}
    try:
        cov = np.linalg.inv(A.T @ A) * CENTROID_SIGMA ** 2
        out["sv"] = float(math.sqrt(max(cov[2, 2] + cov[3, 3], 0.0) / 2))
        out["sw"] = float(math.sqrt(max(cov[4, 4], 0.0))) if fit_w else None
    except np.linalg.LinAlgError:
        out["sv"], out["sw"] = float("inf"), None
    px, py = predict(out, t, B)
    out["res"] = np.hypot(x - px, y - py)
    out["rms"] = float(np.sqrt(np.mean(out["res"] ** 2)))
    return out


def predict(m: dict, t, B) -> tuple[np.ndarray, np.ndarray]:
    dt = np.asarray(t, float) - m["t_ref"]
    B = np.atleast_2d(B)
    return m["x0"] + m["vx"] * dt + m["w"] * B[:, 0], m["y0"] + m["vy"] * dt + m["w"] * B[:, 1]


def _windows(times: np.ndarray, days: float) -> list[tuple[float, float]]:
    t0, t1 = float(times.min()), float(times.max())
    if t1 - t0 <= days:
        return [(t0, t1)]
    out, s = [], t0
    while s < t1 - 0.25 * days:
        out.append((s, min(s + days, t1)))
        s += 0.5 * days
    return out


def link(dets: dict, gs: list[Group], basis, pixscale: float, window_days: float = 6.0,
         rate_max: float = RATE_MAX, check: Check = no_check) -> list[dict]:
    """Links of group detections (see the module docstring). basis(times) -> [k, 2] px at 1 au."""
    n = len(dets["t"])
    if n < MIN_GROUPS or len(gs) < MIN_GROUPS:
        return []
    gt = np.array([g.t for g in gs])
    B_all = basis(dets["t"])
    vmax = rate_max * 24.0 / pixscale  # px/day
    used = np.zeros(n, bool)
    links = []
    for w0, w1 in _windows(gt, window_days):
        check()
        sel = np.nonzero((dets["t"] >= w0) & (dets["t"] <= w1))[0]
        if len(set(dets["g"][sel])) < MIN_GROUPS:
            continue
        t_ref = 0.5 * (w0 + w1)
        i, j = np.triu_indices(len(sel), 1)
        a, b = sel[i], sel[j]
        dt = dets["t"][b] - dets["t"][a]
        ok = (dets["g"][a] != dets["g"][b]) & (np.abs(dt) >= MIN_DT_D)
        a, b, dt = a[ok], b[ok], dt[ok]
        dx, dy = dets["x"][b] - dets["x"][a], dets["y"][b] - dets["y"][a]
        dist = np.hypot(dx, dy)
        slack = 2.0  # px of parallax / centroid noise
        ok = (dist <= vmax * np.abs(dt) + slack) & (dist >= 0.7)
        a, b, dt, dx, dy = a[ok], b[ok], dt[ok], dx[ok], dy[ok]
        if not len(a):
            continue
        vx, vy = dx / dt, dy / dt
        x0 = dets["x"][a] + vx * (t_ref - dets["t"][a])
        y0 = dets["y"][a] + vy * (t_ref - dets["t"][a])
        span = max(w1 - w0, 1.0)
        dv = 2.0 / span
        keys = np.stack([np.floor(x0 / 2.0), np.floor(y0 / 2.0), np.floor(vx / dv), np.floor(vy / dv)], axis=1).astype(np.int64)
        uniq, inv, counts = np.unique(keys, axis=0, return_inverse=True, return_counts=True)
        inv = inv.ravel()
        by_cell = np.argsort(inv, kind="stable")
        starts = np.concatenate([[0], np.cumsum(counts)])
        for k, c in enumerate(np.argsort(-counts)):
            if counts[c] < 2 or k >= MAX_SEEDS:
                break
            pairs = by_cell[starts[c]:starts[c + 1]]
            members = np.unique(np.concatenate([a[pairs], b[pairs]]))
            members = members[~used[members]]
            if len(set(dets["g"][members])) < 3:
                continue
            m = _grow(dets, members, sel, B_all, t_ref, used)
            if m is None:
                continue
            m = _accept(m, dets, pixscale)
            if m is not None:
                used[m["members"]] = True
                links.append(m)
    return links


def _grow(dets, members, pool, B_all, t_ref, used) -> dict | None:
    cur = np.array(sorted(members))
    m = None
    for _ in range(4):
        t, x, y = dets["t"][cur], dets["x"][cur], dets["y"][cur]
        fit_w = len(set(dets["g"][cur])) >= 6
        m = fit(t, x, y, B_all[cur], t_ref, fit_w=fit_w)
        # drop the worst outlier, then add the nearest detection of every group within tolerance
        if m["rms"] > MAX_RMS and len(cur) > 3:
            cur = np.delete(cur, int(np.argmax(m["res"])))
            continue
        px, py = predict(m, dets["t"][pool], B_all[pool])
        d = np.hypot(dets["x"][pool] - px, dets["y"][pool] - py)
        new = []
        for g in set(dets["g"][pool]):
            sel = np.nonzero((dets["g"][pool] == g) & (d <= LINK_TOL) & ~used[pool])[0]
            if len(sel):
                new.append(pool[sel[np.argmin(d[sel])]])
        new = np.array(sorted(set(new)))
        if len(new) == len(cur) and np.array_equal(new, cur):
            break
        if len(new) < 3:
            break
        cur = new
    if m is None:
        return None
    m["members"] = cur
    return m


def _accept(m: dict, dets: dict, pixscale: float) -> dict | None:
    cur = m["members"]
    groups_hit = sorted(set(dets["g"][cur]))
    if len(groups_hit) < MIN_GROUPS or m["rms"] > MAX_RMS:
        return None
    t = dets["t"][cur]
    span = float(t.max() - t.min())
    v = math.hypot(m["vx"], m["vy"])
    move = v * span
    sv = m.get("sv") or CENTROID_SIGMA / max(span, 1e-3)
    if move < MIN_MOVE or v / max(sv, 1e-9) < MOVE_SIGMA:
        return None
    m["rate_arcsec_per_hr"] = v * pixscale / 24.0
    m["pa_deg"] = float(math.degrees(math.atan2(-m["vx"], m["vy"])) % 360.0)
    m["span_days"] = span
    m["groups"] = groups_hit
    m["motion_sigma"] = v / max(sv, 1e-9)
    m["mean_snr"] = float(np.mean(dets["snr"][cur]))
    m["distance_au"] = (1.0 / m["w"]) if m["w"] > 0 and not m["w_bound"] and m.get("sw") and m["w"] > 3 * m["sw"] else None
    return m


# ---------------------------------------------------------------- shift-and-stack
def stack_search(gs: list[Group], basis, pixscale: float, *, rate_max: float = RATE_MAX, window_days: float = 6.0,
                 w_grid=STACK_W, budget: int = MAX_TOTAL_TRIALS, check: Check = no_check) -> tuple[list[dict], dict]:
    """Shift-and-stack of the group maps over trial motions in windows of the visit. Returns hits and stats."""
    stats = {"windows": 0, "trials": 0, "coarsened": False, "budget": budget, "w_grid_au^-1": list(w_grid)}
    if len(gs) < 3:
        return [], stats
    n = gs[0].snr.shape[0]
    gt = np.array([g.t for g in gs])
    B = basis(gt)
    maps = np.stack([np.nan_to_num(g.snr) for g in gs]).astype(np.float32)
    valid = np.stack([np.isfinite(g.snr) for g in gs]).astype(np.float32)
    hits: list[dict] = []
    vmax = rate_max * 24.0 / pixscale  # px/day
    for w0, w1 in _windows(gt, window_days):
        sel = np.nonzero((gt >= w0) & (gt <= w1))[0]
        if len(sel) < 4 or stats["trials"] >= budget:
            continue
        stats["windows"] += 1
        t_ref = float(gt[sel].mean())
        dt = gt[sel] - t_ref
        span = float(dt.max() - dt.min()) or 1.0
        step = STACK_STEP / span
        n_axis = 2 * vmax / step + 1
        trials_est = math.pi / 4 * n_axis ** 2 * len(w_grid)
        cap = min(MAX_TRIALS, budget - stats["trials"])
        if trials_est > cap:
            step *= math.sqrt(trials_est / cap)
            stats["coarsened"] = True
        axis = np.arange(-vmax, vmax + step / 2, step)
        pad = int(math.ceil(vmax * np.abs(dt).max() + max(w_grid) * np.abs(B[sel]).max())) + 2
        zp = np.pad(maps[sel], ((0, 0), (pad, pad), (pad, pad)))
        vp = np.pad(valid[sel], ((0, 0), (pad, pad), (pad, pad)))
        best = np.full((n, n), -np.inf, np.float32)
        best_t = np.zeros((n, n, 3), np.float32)
        need = max(3, 0.6 * len(sel))
        cnt0 = vp[:, pad:pad + n, pad:pad + n].sum(axis=0)
        zero = np.where(cnt0 >= need, zp[:, pad:pad + n, pad:pad + n].sum(axis=0) / np.sqrt(np.maximum(cnt0, 1)),
                        -np.inf)  # the static stack, kept to reject residuals
        for w in w_grid:
            if stats["trials"] >= budget:
                break
            for vx in axis:
                check()
                for vy in axis:
                    v = math.hypot(vx, vy)
                    if v > vmax or v * span < MIN_MOVE:
                        continue
                    acc = np.zeros((n, n), np.float32)
                    cnt = np.zeros((n, n), np.float32)
                    for k in range(len(sel)):
                        ox = int(round(vx * dt[k] + w * B[sel[k], 0]))
                        oy = int(round(vy * dt[k] + w * B[sel[k], 1]))
                        acc += zp[k, pad + oy: pad + oy + n, pad + ox: pad + ox + n]
                        cnt += vp[k, pad + oy: pad + oy + n, pad + ox: pad + ox + n]
                    stats["trials"] += 1
                    snr = np.where(cnt >= need, acc / np.sqrt(np.maximum(cnt, 1)), -np.inf)
                    better = snr > best
                    best[better] = snr[better]
                    best_t[better] = (vx, vy, w)
        # the best of ~10^4 trials per pixel is far from Gaussian: threshold on its own spread as well
        fin = best[np.isfinite(best)]
        thr = STACK_SNR
        if fin.size > 100:
            med = float(np.median(fin))
            thr = max(STACK_SNR, med + STACK_MAD * 1.4826 * float(np.median(np.abs(fin - med))))
        stats.setdefault("thresholds", []).append(round(thr, 2))
        for y, x in zip(*local_peaks(np.where(np.isfinite(best), best, np.nan), thr, size=5, edge=3)):
            vx, vy, w = (float(v) for v in best_t[y, x])
            if np.isfinite(zero[y, x]) and zero[y, x] > 0.6 * best[y, x]:
                continue  # as bright without moving: a static residual
            halves = []
            for part in np.array_split(np.arange(len(sel)), 2):
                acc, cnt = 0.0, 0
                for k in part:
                    ox = int(round(vx * dt[k] + w * B[sel[k], 0]))
                    oy = int(round(vy * dt[k] + w * B[sel[k], 1]))
                    if vp[k, pad + oy + y, pad + ox + x]:
                        acc, cnt = acc + float(zp[k, pad + oy + y, pad + ox + x]), cnt + 1
                halves.append(acc / math.sqrt(cnt) if cnt else 0.0)
            if min(halves) < 2.5:
                continue
            hits.append({"x0": float(x), "y0": float(y), "vx": vx, "vy": vy, "w": w, "t_ref": t_ref,
                         "snr": float(best[y, x]), "halves_snr": [round(h, 2) for h in halves],
                         "rate_arcsec_per_hr": math.hypot(vx, vy) * pixscale / 24.0,
                         "pa_deg": float(math.degrees(math.atan2(-vx, vy)) % 360.0),
                         "window_mjd": [w0, w1], "groups": [int(gs[k].index) for k in sel],
                         "epoch": gs[0].epoch, "step_px_per_day": step})
    hits.sort(key=lambda h: -h["snr"])
    return _dedupe(hits)[:30], stats


def _dedupe(hits: list[dict]) -> list[dict]:
    out: list[dict] = []
    for h in hits:
        if any(math.hypot(h["x0"] - o["x0"], h["y0"] - o["y0"]) < 3.0
               and abs(h["t_ref"] - o["t_ref"]) < 3.0 for o in out):
            continue
        out.append(h)
    return out
