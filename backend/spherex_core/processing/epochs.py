"""Group exposures into epochs (SPHEREx revisits most of the sky every ~6 months)."""

from __future__ import annotations

import numpy as np

# Epochs smaller than this are merged into a neighbour or marked partial (see merge_small_epochs).
MIN_EPOCH_FRAMES = 20
# ...when the neighbour is less than this many days away.
MERGE_GAP_DAYS = 60.0


def assign_epochs(mjd: np.ndarray, gap_days: float = 20.0, max_days: float = 45.0) -> np.ndarray:
    """Epoch index per frame: a new epoch starts after a gap > gap_days or once an epoch spans max_days.

    max_days splits the near-continuous coverage around the ecliptic poles into usable time bins.
    """
    mjd = np.asarray(mjd, dtype=float)
    out = np.zeros(mjd.size, dtype=int)
    if mjd.size == 0:
        return out
    order = np.argsort(mjd)
    cur, start, prev = 0, mjd[order[0]], mjd[order[0]]
    for i in order:
        t = mjd[i]
        if t - prev > gap_days or t - start > max_days:
            cur, start = cur + 1, t
        out[i], prev = cur, t
    return out


def merge_small_epochs(mjd: np.ndarray, epochs: np.ndarray, min_frames: int = MIN_EPOCH_FRAMES,
                       max_gap_days: float = MERGE_GAP_DAYS) -> np.ndarray:
    """Fold epochs with fewer than min_frames frames into the nearest neighbouring epoch, if that neighbour is
    less than max_gap_days away (gap between their closest frames). Smallest epochs go first; on a tie in gap
    the larger neighbour wins. Returns epoch indices renumbered 0..k-1 in time order.

    A few stray exposures a couple of weeks after a visit otherwise become an epoch of their own with only one
    or two detectors and a noisy, misleading frame in the blink.
    """
    mjd = np.asarray(mjd, dtype=float)
    ep = np.asarray(epochs, dtype=int).copy()
    while True:
        ids = sorted(set(ep.tolist()), key=lambda e: mjd[ep == e].min())
        if len(ids) < 2:
            break
        count = {e: int((ep == e).sum()) for e in ids}
        span = {e: (mjd[ep == e].min(), mjd[ep == e].max()) for e in ids}
        merged = False
        for e in sorted(ids, key=lambda e: (count[e], span[e][0])):
            if count[e] >= min_frames:
                break
            i = ids.index(e)
            options = []
            if i > 0:
                prev = ids[i - 1]
                options.append((span[e][0] - span[prev][1], -count[prev], prev))
            if i < len(ids) - 1:
                nxt = ids[i + 1]
                options.append((span[nxt][0] - span[e][1], -count[nxt], nxt))
            options = [o for o in options if o[0] < max_gap_days]
            if options:
                ep[ep == e] = min(options)[2]
                merged = True
                break
        if not merged:
            break
    order = sorted(set(ep.tolist()), key=lambda e: mjd[ep == e].min())
    remap = {e: k for k, e in enumerate(order)}
    return np.array([remap[e] for e in ep.tolist()], dtype=int)


def group_epochs(mjd: np.ndarray, gap_days: float = 20.0, max_days: float = 45.0,
                 min_frames: int = MIN_EPOCH_FRAMES, merge_gap_days: float = MERGE_GAP_DAYS
                 ) -> tuple[np.ndarray, list[bool]]:
    """Epoch index per frame and, per epoch, whether it is partial (still below min_frames after merging)."""
    ep = merge_small_epochs(mjd, assign_epochs(mjd, gap_days, max_days), min_frames, merge_gap_days)
    n_ep = int(ep.max()) + 1 if ep.size else 0
    partial = [int((ep == e).sum()) < min_frames for e in range(n_ep)]
    return ep, partial
