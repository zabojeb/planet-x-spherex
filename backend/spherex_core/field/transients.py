"""Moving Object Detection inside a visit (asteroids, comets) from single exposures.

For every exposure the epoch co-add of its detector is subtracted; the co-add rejects outliers per pixel, so
an object present in one exposure is not in it and stands out in the difference. Peaks are described by
features (real/bogus), including the simultaneous-pair check: D1/D4, D2/D5 and D3/D6 image the same sky at
the same moment, so a real source bright at both wavelengths shows in the partner exposure at the same place,
while a cosmic ray or detector artefact does not. Detections are linked within each epoch into tracklets
(three or more on a straight line with constant rate) and identified against known objects seen from SPHEREx
(field.known). Tracklets are straight-line fits in sky coordinates: below ~1.5 AU SPHEREx's orbital parallax
adds a wobble of >= 1 px that this model does not include.
"""

from __future__ import annotations

import logging

import numpy as np
from scipy.ndimage import binary_dilation

from ..bands import BANDS
from ..errors import NotFound
from ..pipeline import results
from . import realbogus
from .common import Check, FieldData, local_peaks, no_check, region_mask, robust_noise, saturated_regions, snr_image

DETECT_SNR = 6.0
BRIGHT_SNR = 10.0  # co-add S/N above which a star's wavelength-dependent residuals dominate (masked)
BRIGHT_GROW = 2
MAX_SHARPNESS = 0.75  # a single hot pixel or cosmic ray, not a point source
PARTNER = {"D1": "D4", "D4": "D1", "D2": "D5", "D5": "D2", "D3": "D6", "D6": "D3"}
PARTNER_SNR = 3.0
PARTNER_RADIUS = 1  # px
MIN_RB = 0.3  # detections below this real/bogus probability are not linked
MIN_DT = 0.01  # d between linked detections (~15 min)
MAX_DT = 3.0  # d
MIN_MOVE = 0.8  # px between the two seed detections
MIN_RATE = 7.8  # px/day (~2"/h): slower "tracklets" are static residuals
MIN_SPAN = 2.0  # px covered by a tracklet
MAX_RATE = 3000.0  # px/day (~770"/h)
LINK_TOL = 1.5  # px around the predicted position
MAX_RMS = 1.0  # px rms about the fitted line
HIGH_RB = 0.6  # tracklet quality "high": mean real/bogus, partner agreement and length
HIGH_PARTNER = 0.5
HIGH_N = 4
log = logging.getLogger(__name__)


def difference_maps(fd: FieldData, check: Check = no_check) -> dict:
    """S/N of (exposure - epoch co-add) for every stored exposure, with the bright-star mask applied.
    Returns {exposure index: (s_map, epoch, band_index)} plus per-(epoch, band) context under key None."""
    meta = fd.meta
    maps: dict = {}
    ctx: dict = {}
    halos = region_mask(fd.n, saturated_regions(fd), scale=1.5, extra=2.0)
    with np.errstate(all="ignore"):
        static = np.nanmedian(fd.coadds, axis=0)  # [B, n, n]: stars, not this visit's asteroid trails
    for e in range(fd.coadds.shape[0]):
        for b, band in enumerate(BANDS):
            coadd = fd.coadds[e, b]
            if not np.isfinite(coadd).any():
                continue
            try:
                stack, idx = results.read_exposures(fd.ctx.dir, meta, e, band)
            except NotFound:
                continue
            csnr = np.nan_to_num(snr_image(static[b] if np.isfinite(static[b]).any() else coadd), nan=0.0)
            bright = binary_dilation(csnr > BRIGHT_SNR, iterations=BRIGHT_GROW) | halos
            by, bx = np.nonzero(csnr > BRIGHT_SNR)
            ctx[(e, b)] = {"bright_xy": np.column_stack([bx, by]) if bx.size else None, "bright": bright}
            for k, i in enumerate(idx):
                d = stack[k].astype(float) - coadd
                _, noise = robust_noise(d)
                if not (noise > 0):
                    continue
                s = d / noise
                s[bright] = np.nan
                maps[int(i)] = (s.astype(np.float32), e, b, float(noise))
            check()
    maps[None] = ctx
    return maps


def partner_index(meta: dict) -> dict[int, int]:
    """Exposure index -> index of the simultaneous exposure on the partner detector (same obs_id)."""
    by = {(x["obs_id"], x["band"]): x["index"] for x in meta["exposures"]}
    return {x["index"]: by[(x["obs_id"], PARTNER[x["band"]])] for x in meta["exposures"]
            if (x["obs_id"], PARTNER.get(x["band"])) in by}


def partner_seen(maps: dict, partners: dict[int, int], i: int, x: int, y: int) -> float:
    """1 if the partner exposure has S/N >= PARTNER_SNR within PARTNER_RADIUS, 0 if not, 0.5 if unknown."""
    j = partners.get(i)
    if j is None or j not in maps:
        return 0.5
    s = maps[j][0]
    win = s[max(y - PARTNER_RADIUS, 0):y + PARTNER_RADIUS + 1, max(x - PARTNER_RADIUS, 0):x + PARTNER_RADIUS + 1]
    if not np.isfinite(win).any():
        return 0.5
    return float(np.nanmax(win) >= PARTNER_SNR)


def detect(fd: FieldData, check: Check = no_check, maps: dict | None = None) -> tuple[list[dict], dict]:
    """Detections in the difference S/N maps, with features and real/bogus probabilities."""
    maps = maps if maps is not None else difference_maps(fd, check)
    ctx = maps[None]
    partners = partner_index(fd.meta)
    exposures = fd.meta["exposures"]
    dets: list[dict] = []
    feats: list[np.ndarray] = []
    for i, (s, e, b, noise) in ((k, v) for k, v in maps.items() if k is not None):
        c = ctx[(e, b)]
        for y, x in zip(*local_peaks(s, DETECT_SNR, size=5, edge=3)):
            fv = realbogus.features(s, fd.flags[e, b], int(x), int(y), c["bright_xy"],
                                    partner_seen(maps, partners, i, int(x), int(y)))
            if fv[1] > MAX_SHARPNESS:
                continue
            feats.append(fv)
            dets.append({"exposure": i, "epoch": e, "band": BANDS[b], "mjd": float(exposures[i]["mjd"]),
                         "x": float(x), "y": float(y), "snr": float(s[y, x]), "flux": float(s[y, x] * noise)})
    check()
    if dets:
        probs = realbogus.score(np.array(feats))
        for det, p, f in zip(dets, probs, feats):
            det["rb"] = float(p)
            det["features"] = {name: float(v) for name, v in zip(realbogus.FEATURES, f)}
        ra, dec = fd.sky([d["x"] for d in dets], [d["y"] for d in dets])
        for det, r, dd in zip(dets, ra, dec):
            det["ra"], det["dec"] = float(r), float(dd)
    n_images = sum(1 for k in maps if k is not None)
    return dets, {"images": n_images, "detections": len(dets),
                  "model": "trained" if realbogus.load_model() else "heuristic"}


def _fit_line(pts: list[dict]) -> tuple[np.ndarray, np.ndarray, float]:
    t = np.array([p["mjd"] for p in pts])
    a = np.column_stack([np.ones_like(t), t - t.mean()])
    cx, *_ = np.linalg.lstsq(a, np.array([p["x"] for p in pts]), rcond=None)
    cy, *_ = np.linalg.lstsq(a, np.array([p["y"] for p in pts]), rcond=None)
    res = np.hypot(a @ cx - [p["x"] for p in pts], a @ cy - [p["y"] for p in pts])
    return cx, cy, float(np.sqrt(np.mean(res ** 2)))


def link(dets: list[dict], pixscale: float) -> tuple[list[dict], list[dict]]:
    """Tracklets (>= 3 detections) and strong pairs, per epoch."""
    tracklets, pairs = [], []
    for e in sorted({d["epoch"] for d in dets}):
        pool = sorted([d for d in dets if d["epoch"] == e and d["rb"] >= MIN_RB], key=lambda d: d["mjd"])
        used: set[int] = set()
        seeds = []
        for i, a in enumerate(pool):
            for j in range(i + 1, len(pool)):
                b = pool[j]
                dt = b["mjd"] - a["mjd"]
                if dt < MIN_DT:
                    continue
                if dt > MAX_DT:
                    break
                dist = np.hypot(b["x"] - a["x"], b["y"] - a["y"])
                if dist < MIN_MOVE or dist / dt > MAX_RATE or dist / dt < MIN_RATE:
                    continue
                seeds.append((a["snr"] + b["snr"], i, j))
        seeds.sort(reverse=True)
        for _, i, j in seeds:
            if i in used or j in used:
                continue
            a, b = pool[i], pool[j]
            dt = b["mjd"] - a["mjd"]
            vx, vy = (b["x"] - a["x"]) / dt, (b["y"] - a["y"]) / dt
            members = [i, j]
            for k, c in enumerate(pool):
                if k in used or k in (i, j) or min(abs(c["mjd"] - a["mjd"]), abs(c["mjd"] - b["mjd"])) < MIN_DT / 2:
                    continue
                px, py = a["x"] + vx * (c["mjd"] - a["mjd"]), a["y"] + vy * (c["mjd"] - a["mjd"])
                if np.hypot(c["x"] - px, c["y"] - py) <= LINK_TOL:
                    members.append(k)
            uniq = {round(pool[k]["mjd"], 4): pool[k] for k in members}  # one detection per exposure time
            pts = sorted(uniq.values(), key=lambda d: d["mjd"])
            if len(pts) >= 3:
                cx, cy, rms = _fit_line(pts)
                span = float(np.hypot(pts[-1]["x"] - pts[0]["x"], pts[-1]["y"] - pts[0]["y"]))
                speed = float(np.hypot(vx, vy)) or 1.0
                along = [((p["x"] - pts[0]["x"]) * vx + (p["y"] - pts[0]["y"]) * vy) / speed for p in pts]
                if rms > MAX_RMS or span < MIN_SPAN or any(b2 < a2 - 0.5 for a2, b2 in zip(along, along[1:])):
                    continue
                used.update(members)
                rate_px_day = float(np.hypot(cx[1], cy[1]))
                pa = float(np.degrees(np.arctan2(-cx[1], cy[1])) % 360.0)  # east is -x
                tracklets.append(_tracklet(len(tracklets), e, pts, rate_px_day * pixscale / 24.0, pa,
                                           rms * pixscale))
            elif a["snr"] >= 10 and b["snr"] >= 10 and a["rb"] >= 0.7 and b["rb"] >= 0.7:
                rate = np.hypot(vx, vy) * pixscale / 24.0
                pa = float(np.degrees(np.arctan2(-vx, vy)) % 360.0)
                pairs.append(_tracklet(len(pairs), e, [a, b], float(rate), pa, None, prefix="P"))
    for t in tracklets:
        pf = t["partner_fraction"]
        t["quality"] = "high" if (t["mean_rb"] >= HIGH_RB and (pf is None or pf >= HIGH_PARTNER)
                                  and t["n"] >= HIGH_N) else "low"
    tracklets.sort(key=lambda t: (t["quality"] != "high", -t["score"]))
    for i, t in enumerate(tracklets):
        t["id"] = f"T{i + 1:03d}"
    return tracklets, pairs


def _tracklet(i: int, epoch: int, pts: list[dict], rate: float, pa: float, rms: float | None,
              prefix: str = "T") -> dict:
    keep = ("exposure", "mjd", "band", "x", "y", "ra", "dec", "snr", "rb")
    det = [{k: p[k] for k in keep} | {"partner_seen": p["features"]["partner"]} for p in pts]
    mid = float(np.mean([p["mjd"] for p in pts]))
    partner = [d["partner_seen"] for d in det if d["partner_seen"] != 0.5]
    return {"id": f"{prefix}{i + 1:03d}", "epoch": epoch, "n": len(pts), "mjd_mid": mid,
            "rate_arcsec_per_hr": rate, "pa_deg": pa, "rms_arcsec": rms, "detections": det,
            "partner_fraction": float(np.mean(partner)) if partner else None,
            "mean_snr": float(np.mean([p["snr"] for p in pts])), "mean_rb": float(np.mean([p["rb"] for p in pts])),
            "score": float(np.mean([p["rb"] for p in pts]) * np.sqrt(sum(p["snr"] ** 2 for p in pts))),
            "known": None, "identification": []}
